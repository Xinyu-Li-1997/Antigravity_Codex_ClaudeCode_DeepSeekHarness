-- Copyright 2026 Xinyu Li (Xinyu-Li-1997)
-- SPDX-License-Identifier: Apache-2.0
/-
  KernelAudit.lean
  零 Mathlib / 零 Lake 受控单文件真内核 Lean 4 审计驱动：
  1. `header <lean_file>`：调用 Lean.Parser.parseHeader + Elab.headerToImports 提取 AST 级真实导入与解析诊断。
  2. `audit <module_name> <lean_file>`：
     - 解析真实文件头部导入；
     - 在不调用 enableInitializersExecution 的前提下加载洁净现编 .olean（彻底免疫 initialize 进程劫持）；
     - 对目标模块全部常量执行 Lean.Kernel.Environment.replay 内核重放（防御篡改 .olean / skipKernelTC）；
     - 遍历 moduleData.constNames 全量拦截 axiomInfo / opaqueInfo / isUnsafe / implemented_by / extern / csimp / initFn；
     - 对全部常量运行 Lean.collectAxioms 并输出精确 JSON；
     - 在内核 Expr 层精确校验 _root_.Nonempty 零前提见证、inductInfo 构造子与 thmInfo 类型签名。
-/

import Lean

open Lean

private def jsonEscape (s : String) : String :=
  s.foldl (init := "") fun acc c =>
    match c with
    | '"'  => acc ++ "\\\""
    | '\\' => acc ++ "\\\\"
    | '\n' => acc ++ "\\n"
    | '\r' => acc ++ "\\r"
    | '\t' => acc ++ "\\t"
    | _    =>
      if c.toNat < 16 then
        acc ++ "\\u000" ++ String.ofList (Nat.toDigits 16 c.toNat)
      else if c.toNat < 32 then
        acc ++ "\\u00" ++ String.ofList (Nat.toDigits 16 c.toNat)
      else
        acc.push c

private def jsonStr (s : String) : String :=
  "\"" ++ jsonEscape s ++ "\""

private def jsonBool (b : Bool) : String :=
  if b then "true" else "false"

private def jsonStrArray (xs : Array String) : String :=
  "[" ++ String.intercalate "," (xs.toList.map jsonStr) ++ "]"

private def dedupStrings (xs : Array String) : Array String :=
  xs.foldl (init := #[]) fun acc s =>
    if acc.contains s then acc else acc.push s

private def parseHeaderReport (filePath : String) : IO (Array String × Array String) := do
  let src ← IO.FS.readFile filePath
  let ictx := Parser.mkInputContext src filePath
  let (header, _pstate, msgs) ← Parser.parseHeader ictx
  let imports := dedupStrings ((Elab.headerToImports header).map (fun imp => imp.module.toString))
  let mut errs : Array String := #[]
  for msg in msgs.toList do
    if msg.severity == MessageSeverity.error then
      let s ← msg.toString
      errs := errs.push s.trimAscii.toString
  return (imports, errs)

private def isAllowedAuxUnsafe (env : Environment) (cname : Name) : Bool :=
  match cname with
  | .str parent "_unsafe_rec" =>
    match env.find? parent with
    | some (.inductInfo ind) => !ind.isUnsafe
    | _ => false
  | .str (.str parent "ctorIdx") "_impl" =>
    match env.find? parent with
    | some (.inductInfo ind) =>
      !ind.isUnsafe && Compiler.getImplementedBy? env (.str parent "ctorIdx") == some cname
    | _ => false
  | _ => false

private def isAllowedAuxImplementedBy (env : Environment) (cname : Name) : Bool :=
  match cname with
  | .str parent "ctorIdx" =>
    match env.find? parent with
    | some (.inductInfo ind) =>
      !ind.isUnsafe && Compiler.getImplementedBy? env cname == some (.str cname "_impl")
    | _ => false
  | _ => false

private def isForbiddenNonemptyWrapper (n : Name) : Bool :=
  n == ``Or || n == ``Sum || n == ``Option || n == ``List || n == ``Not || n == ``PUnit || n == ``Unit

private def extractStrictNonemptyTarget (e : Expr) : Option String :=
  if e.isForall then
    none
  else
    let fn := e.getAppFn
    let args := e.getAppArgs
    if fn.constName? == some ``Nonempty && args.size == 1 then
      let inner := args[0]!
      if inner.isForall then
        none
      else
        match inner.getAppFn.constName? with
        | some targetName =>
          if isForbiddenNonemptyWrapper targetName then none
          else some targetName.toString
        | none => none
    else
      none

private def hasFalseHypothesis (e : Expr) : Bool :=
  match e with
  | .forallE _ dom body _ =>
    if dom.getAppFn.constName? == some ``False then true
    else hasFalseHypothesis body
  | _ => false

private def isTrivialTrueConclusion (e : Expr) : Bool :=
  match e with
  | .forallE _ _ body _ => isTrivialTrueConclusion body
  | _ => e.getAppFn.constName? == some ``True

private def runHeaderMode (filePath : String) : IO UInt32 := do
  let (imports, errs) ← parseHeaderReport filePath
  let ok := errs.isEmpty
  IO.println ("{"
    ++ "\"mode\":\"header\","
    ++ "\"ok\":" ++ jsonBool ok ++ ","
    ++ "\"imports\":" ++ jsonStrArray imports ++ ","
    ++ "\"parse_errors\":" ++ jsonStrArray errs
    ++ "}")
  return if ok then 0 else 2

private def collectAxiomsBatch (env : Environment) (names : Array Name) : IO (Std.HashMap Name (Array String)) := do
  let ctx : Core.Context := { fileName := "<kernel-audit>", fileMap := default }
  let st : Core.State := { env := env }
  let action : CoreM (Std.HashMap Name (Array String)) := do
    let mut out : Std.HashMap Name (Array String) := {}
    for cname in names do
      let axs ← Lean.collectAxioms cname
      out := out.insert cname (axs.map (fun a => a.toString))
    return out
  let (res, _) ← action.toIO ctx st
  return res

private def auditSingleModuleJson (modStr : String) (filePath : String) : IO (Bool × String) := do
  let (headerImports, headerErrs) ← parseHeaderReport filePath
  let modName := modStr.toName
  let env ← importModules #[{ module := modName }] {} (trustLevel := 0) (loadExts := false) (leakEnv := false)
  let mut targetIdx : Option Nat := none
  for i in [:env.header.moduleNames.size] do
    if env.header.moduleNames[i]! == modName then
      targetIdx := some i
  match targetIdx with
  | none =>
    return (false, "{"
      ++ "\"mode\":\"audit\",\"ok\":false,"
      ++ "\"error\":\"MODULE_NOT_IN_HEADER\","
      ++ "\"module\":" ++ jsonStr modStr
      ++ "}")
  | some idx =>
    let modData := env.header.moduleData[idx]!
    let constNames := modData.constNames
    let modImports := modData.imports.map (fun imp => imp.module.toString)

    -- 1. 内核重放：在仅导入前置模块的洁净 Kernel.Environment 上重放本模块全部常量
    let mut replayOk := true
    let mut replayErr := ""
    try
      let baseEnv ← importModules modData.imports {} (trustLevel := 0) (loadExts := false) (leakEnv := false)
      let mut newConsts : Std.HashMap Name ConstantInfo := {}
      for cname in constNames do
        match env.find? cname with
        | some cinfo =>
          newConsts := newConsts.insert cname cinfo
        | none =>
          replayOk := false
          replayErr := "MISSING_CONSTANT:" ++ cname.toString
      if replayOk then
        let _ ← Kernel.Environment.replay newConsts baseEnv.toKernelEnv
    catch e =>
      replayOk := false
      replayErr := e.toString

    -- 2. 全常量公理闭包提取 + 模块序列化扩展条目与安全属性审计（彻底免疫 initialize 进程劫持）
    let axMap ← collectAxiomsBatch env constNames
    let csimpEntries := Compiler.CSimp.ext.ext.getModuleEntries env idx
    let regInitEntries := regularInitAttr.ext.getModuleEntries env idx
    let builtinInitEntries := builtinInitAttr.ext.getModuleEntries env idx
    let mut axiomDecls : Array String := #[]
    let mut opaqueDecls : Array String := #[]
    let mut unsafeDecls : Array String := #[]
    let mut implByDecls : Array String := #[]
    let mut externDecls : Array String := #[]
    let mut csimpDecls : Array String := #[]
    let mut initDecls : Array String := #[]
    let mut allAxioms : Array String := #[]
    let mut theoremEntries : Array String := #[]
    let mut defnNames : Array String := #[]
    let mut inductEntries : Array String := #[]
    let mut nonemptyTargets : Array String := #[]

    for entryIdx in [:csimpEntries.size] do
      csimpDecls := csimpDecls.push (modStr ++ "#csimp_" ++ toString entryIdx)
    for entryIdx in [:regInitEntries.size] do
      initDecls := initDecls.push (modStr ++ "#init_" ++ toString entryIdx)
    for entryIdx in [:builtinInitEntries.size] do
      initDecls := initDecls.push (modStr ++ "#builtin_init_" ++ toString entryIdx)

    for cname in constNames do
      let cnameStr := cname.toString
      if (Compiler.getImplementedBy? env cname).isSome && !isAllowedAuxImplementedBy env cname then
        implByDecls := implByDecls.push cnameStr
      if isExtern env cname || (getExternAttrData? env cname).isSome then
        externDecls := externDecls.push cnameStr
      if (regularInitAttr.getParam? env cname).isSome || (builtinInitAttr.getParam? env cname).isSome then
        if !initDecls.contains cnameStr then
          initDecls := initDecls.push cnameStr

      let axStrs := axMap.getD cname #[]
      for a in axStrs do
        if !allAxioms.contains a then
          allAxioms := allAxioms.push a

      if let some cinfo := env.find? cname then
        if cinfo.isUnsafe && !isAllowedAuxUnsafe env cname then
          unsafeDecls := unsafeDecls.push cnameStr
        match cinfo with
        | .axiomInfo _ =>
          axiomDecls := axiomDecls.push cnameStr
        | .opaqueInfo _ =>
          opaqueDecls := opaqueDecls.push cnameStr
        | .inductInfo ind =>
          if !isPrivateName cname && !cname.isInternal then
            let ctors := (ind.ctors.map (fun c => c.toString)).toArray
            inductEntries := inductEntries.push (
              "{\"name\":" ++ jsonStr cnameStr
              ++ ",\"ctors\":" ++ jsonStrArray ctors ++ "}"
            )
        | .defnInfo _ =>
          if !isPrivateName cname && !cname.isInternal then
            defnNames := defnNames.push cnameStr
        | .thmInfo _ =>
          if let some tgt := extractStrictNonemptyTarget cinfo.type then
            nonemptyTargets := nonemptyTargets.push (tgt ++ ":" ++ cnameStr)
          if !isPrivateName cname && !cname.isInternal then
            let typeStr := toString cinfo.type
            theoremEntries := theoremEntries.push (
              "{\"name\":" ++ jsonStr cnameStr
              ++ ",\"axioms\":" ++ jsonStrArray axStrs
              ++ ",\"has_false_hyp\":" ++ jsonBool (hasFalseHypothesis cinfo.type)
              ++ ",\"trivial_true\":" ++ jsonBool (isTrivialTrueConclusion cinfo.type)
              ++ ",\"type_repr\":" ++ jsonStr typeStr
              ++ "}"
            )
        | _ => pure ()

    let ok := headerErrs.isEmpty && replayOk
    let jsonOut := "{"
      ++ "\"mode\":\"audit\","
      ++ "\"ok\":" ++ jsonBool ok ++ ","
      ++ "\"module\":" ++ jsonStr modStr ++ ","
      ++ "\"header_imports\":" ++ jsonStrArray headerImports ++ ","
      ++ "\"header_errors\":" ++ jsonStrArray headerErrs ++ ","
      ++ "\"module_imports\":" ++ jsonStrArray modImports ++ ","
      ++ "\"constant_count\":" ++ toString constNames.size ++ ","
      ++ "\"replay_ok\":" ++ jsonBool replayOk ++ ","
      ++ "\"replay_error\":" ++ jsonStr replayErr ++ ","
      ++ "\"axiom_decls\":" ++ jsonStrArray axiomDecls ++ ","
      ++ "\"opaque_decls\":" ++ jsonStrArray opaqueDecls ++ ","
      ++ "\"unsafe_decls\":" ++ jsonStrArray unsafeDecls ++ ","
      ++ "\"implemented_by_decls\":" ++ jsonStrArray implByDecls ++ ","
      ++ "\"extern_decls\":" ++ jsonStrArray externDecls ++ ","
      ++ "\"csimp_decls\":" ++ jsonStrArray csimpDecls ++ ","
      ++ "\"init_decls\":" ++ jsonStrArray initDecls ++ ","
      ++ "\"all_axioms\":" ++ jsonStrArray allAxioms ++ ","
      ++ "\"nonempty_targets\":" ++ jsonStrArray nonemptyTargets ++ ","
      ++ "\"defn_names\":" ++ jsonStrArray defnNames ++ ","
      ++ "\"inductives\":[" ++ String.intercalate "," inductEntries.toList ++ "],"
      ++ "\"theorems\":[" ++ String.intercalate "," theoremEntries.toList ++ "]"
      ++ "}"
    return (ok, jsonOut)

private def runAuditMode (modStr : String) (filePath : String) : IO UInt32 := do
  initSearchPath (← findSysroot)
  let (ok, jsonOut) ← auditSingleModuleJson modStr filePath
  IO.println jsonOut
  return if ok then 0 else 2

private def parsePairs : List String → Option (List (String × String))
  | [] => some []
  | m :: f :: rest => do
    let tail ← parsePairs rest
    return (m, f) :: tail
  | _ => none

private def runHeaderBatchMode (files : List String) : IO UInt32 := do
  let mut allOk := true
  let mut items : Array String := #[]
  for fp in files do
    let (imports, errs) ← parseHeaderReport fp
    let ok := errs.isEmpty
    if !ok then allOk := false
    items := items.push (
      "{\"file\":" ++ jsonStr fp
      ++ ",\"ok\":" ++ jsonBool ok
      ++ ",\"imports\":" ++ jsonStrArray imports
      ++ ",\"parse_errors\":" ++ jsonStrArray errs
      ++ "}"
    )
  IO.println ("{\"mode\":\"header-batch\",\"ok\":" ++ jsonBool allOk
    ++ ",\"files\":[" ++ String.intercalate "," items.toList ++ "]}")
  return if allOk then 0 else 2

private def runAuditBatchMode (args : List String) : IO UInt32 := do
  match parsePairs args with
  | none =>
    IO.eprintln "Usage: KernelAudit.lean audit-batch <Mod1> <File1> [<Mod2> <File2> ...]"
    return 2
  | some pairs =>
    initSearchPath (← findSysroot)
    let mut allOk := true
    let mut items : Array String := #[]
    for (m, f) in pairs do
      let (ok, j) ← auditSingleModuleJson m f
      if !ok then allOk := false
      items := items.push j
    IO.println ("{\"mode\":\"audit-batch\",\"ok\":" ++ jsonBool allOk
      ++ ",\"modules\":[" ++ String.intercalate "," items.toList ++ "]}")
    return if allOk then 0 else 2

def main (args : List String) : IO UInt32 := do
  match args with
  | ["header", filePath] => runHeaderMode filePath
  | "header-batch" :: rest => runHeaderBatchMode rest
  | ["audit", modStr, filePath] => runAuditMode modStr filePath
  | "audit-batch" :: rest => runAuditBatchMode rest
  | _ =>
    IO.eprintln "Usage: KernelAudit.lean header <file> | header-batch <f...> | audit <Mod> <file> | audit-batch <Mod> <file>..."
    return 2
