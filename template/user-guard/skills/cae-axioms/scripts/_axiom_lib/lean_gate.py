# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_axiom_lib.lean_gate - 零依赖纯核 Lean 4 静态预检（失败即短路）+ 真内核 KernelAudit 驱动。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .contract import (
    ALLOWED_LEAN_AXIOMS,
    FORBIDDEN_LEAN_IMPORTS,
    FORBIDDEN_LEAN_TOKENS,
    GOLDEN_LEAN_VERSION_PREFIX,
    KERNEL_AUDIT_DRIVER_SHA256,
    LOGIC_CORE_GOLDEN_TYPE_HASHES,
    OBJECTIVE_ROLES,
    PROFILE_LEAN_MODULES,
    REQUIRED_THEOREMS,
    RESERVED_LOGIC_NAMES,
    GateError,
    _NONINTERACTIVE_ENV,
    _camel_to_snake,
    _default_lean_dir,
    _is_link_or_reparse,
    _kernel_audit_driver_path,
    _reject_links,
    compute_bytes_hashes,
)


def _strip_lean_comments_and_strings(source: str) -> str:
    """剥离 Lean 4 行注释 --、可嵌套块注释 /- ... -/、字符串字面量与字符字面量 '...'，保留换行符以便行号对齐。"""
    out: List[str] = []
    idx = 0
    n = len(source)
    block_depth = 0
    in_string = False
    while idx < n:
        ch = source[idx]
        nxt = source[idx + 1] if idx + 1 < n else ""
        if block_depth > 0:
            if ch == "/" and nxt == "-":
                block_depth += 1
                out.append("  ")
                idx += 2
            elif ch == "-" and nxt == "/":
                block_depth -= 1
                out.append("  ")
                idx += 2
            elif ch == "\n":
                out.append("\n")
                idx += 1
            else:
                out.append(" ")
                idx += 1
            continue
        if in_string:
            if ch == "\\" and idx + 1 < n:
                out.append("  ")
                idx += 2
            elif ch == '"':
                in_string = False
                out.append(" ")
                idx += 1
            elif ch == "\n":
                out.append("\n")
                idx += 1
            else:
                out.append(" ")
                idx += 1
            continue
        if ch == "/" and nxt == "-":
            block_depth = 1
            out.append("  ")
            idx += 2
            continue
        if ch == "-" and nxt == "-":
            while idx < n and source[idx] != "\n":
                out.append(" ")
                idx += 1
            continue
        if ch == '"':
            in_string = True
            out.append(" ")
            idx += 1
            continue
        # Lean 4 字符字面量：区分标识符撇号后缀（前驱为字母/数字/_/'）与真实字符字面量 'c' / '\...'
        if ch == "'":
            prev_ch = source[idx - 1] if idx > 0 else ""
            if not (prev_ch.isalnum() or prev_ch in ("_", "'")):
                j = idx + 1
                if j < n and source[j] == "\\":
                    j += 1
                    if j < n and source[j] in ("x", "u"):
                        j += 1
                        while j < n and source[j] in "0123456789abcdefABCDEF":
                            j += 1
                    elif j < n:
                        j += 1
                elif j < n and source[j] not in ("\n", "'"):
                    j += 1
                if j < n and source[j] == "'":
                    out.append(" " * (j - idx + 1))
                    idx = j + 1
                    continue
        out.append(ch)
        idx += 1
    return "".join(out)


_NONEMPTY_FORBIDDEN_TOP_OPS = (
    "→",
    "->",
    "↔",
    "<->",
    "∨",
    "\\/",
    "⊕",
    "¬",
    "∀",
    "∃",
    "=>",
    "|",
    ",",
)

_NONEMPTY_FORBIDDEN_TOP_WORDS = frozenset({"fun", "forall", "Sum", "Option", "List", "Or", "Not"})


def _match_strict_nonempty_type(type_expr: str, target_name: str) -> bool:
    """严格匹配无外层联结词且括号顶层无函数箭头/析取包装的 Nonempty S 或 Nonempty (S args...)。"""
    norm = re.sub(r"\s+", " ", type_expr).strip()
    if norm == f"Nonempty {target_name}":
        return True
    prefix = f"Nonempty ({target_name}"
    if not norm.startswith(prefix):
        return False
    after_name_idx = len(prefix)
    if after_name_idx >= len(norm) or norm[after_name_idx] not in (" ", ")"):
        return False
    open_idx = norm.index("(")
    depth = 0
    top_level_chars: List[str] = []
    for idx in range(open_idx, len(norm)):
        ch = norm[idx]
        if ch == "(":
            depth += 1
            if depth == 2:
                top_level_chars.append(" ")
        elif ch == ")":
            depth -= 1
            if depth == 0:
                if idx != len(norm) - 1:
                    return False
                break
            if depth < 0:
                return False
        elif depth == 1:
            top_level_chars.append(ch)
    else:
        return False
    top_segment = "".join(top_level_chars)
    for op in _NONEMPTY_FORBIDDEN_TOP_OPS:
        if op in top_segment:
            return False
    top_words = re.findall(r"[A-Za-z_][A-Za-z0-9_']*", top_segment)
    if any(w in _NONEMPTY_FORBIDDEN_TOP_WORDS for w in top_words):
        return False
    return True


def _find_strict_nonempty_witness(decl_blocks: List[str], target_name: str) -> Optional[str]:
    """要求存在零前提绑定器的 theorem/example，其整个返回类型严格为 Nonempty target_name / Nonempty (target_name ...)。"""
    header_re = re.compile(
        r"^\s*(?:private\s+)?(?:theorem\s+([A-Za-z_][A-Za-z0-9_']*)|example)\s*:(.*)$",
        re.S,
    )
    for block in decl_blocks:
        if ":=" not in block:
            continue
        header = block.split(":=", 1)[0]
        m = header_re.match(header)
        if not m:
            continue
        thm_name = m.group(1) or "example"
        type_expr = m.group(2)
        if _match_strict_nonempty_type(type_expr, target_name):
            return thm_name
    return None


def _parse_inductive_ctors(decl_blocks: List[str], ind_name: str) -> List[str]:
    ind_re = re.compile(rf"^\s*(?:private\s+)?inductive\s+{re.escape(ind_name)}\b")
    for block in decl_blocks:
        if ind_re.match(block):
            ctors: List[str] = []
            for line in block.splitlines()[1:]:
                m = re.match(r"^\s*\|\s*(.+)$", line)
                if not m:
                    continue
                for seg in m.group(1).split("|"):
                    m_name = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_']*)\b", seg)
                    if m_name:
                        ctors.append(m_name.group(1))
            return ctors
    return []


def _verify_kernel_driver() -> Path:
    drv = _reject_links(_kernel_audit_driver_path(), label="Lean 真内核审计驱动")
    if not drv.is_file():
        raise GateError("LEAN_DRIVER_MISSING", f"缺少受控 Lean 真内核审计驱动: {drv}")
    actual_sha = hashlib.sha256(drv.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if actual_sha != KERNEL_AUDIT_DRIVER_SHA256:
        raise GateError(
            "LEAN_DRIVER_TAMPERED",
            f"受控 Lean 真内核审计驱动 SHA-256 校验不匹配（预期 {KERNEL_AUDIT_DRIVER_SHA256[:16]}...，实际 {actual_sha[:16]}...）",
            {"expected_sha256": KERNEL_AUDIT_DRIVER_SHA256, "actual_sha256": actual_sha},
        )
    return drv


def _find_lean_executable(explicit_cmd: str = "") -> Optional[str]:
    if explicit_cmd:
        cand = Path(explicit_cmd).expanduser()
        if cand.is_file():
            return str(cand.resolve())
        found = shutil.which(explicit_cmd)
        if found:
            return found
        return None
    found = shutil.which("lean")
    if found:
        return found
    elan_bin = Path.home() / ".elan" / "bin" / ("lean.exe" if os.name == "nt" else "lean")
    if elan_bin.is_file():
        return str(elan_bin)
    return None


def _to_wsl_path(win_path: Path) -> str:
    resolved = win_path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if drive and len(drive) == 1:
        rest = resolved.as_posix().split(":", 1)[1]
        return f"/mnt/{drive}{rest}"
    return resolved.as_posix()


def _probe_lean_version_and_libdir(*, lean_exe: str, use_wsl: bool) -> Tuple[Optional[str], Optional[str]]:
    env = {**os.environ, **_NONINTERACTIVE_ENV}
    try:
        if use_wsl:
            py_probe = (
                "import pathlib, subprocess; "
                f"exe={lean_exe!r}; "
                "v=subprocess.check_output([exe,'--version'],text=True).splitlines()[0].strip(); "
                "l=subprocess.check_output([exe,'--print-libdir'],text=True).strip(); "
                "print(v); print(l)"
            )
            proc = subprocess.run(
                ["wsl", "--", "python3", "-B", "-X", "utf8", "-c", py_probe],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            if proc.returncode != 0:
                return None, None
            lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            if len(lines) >= 2:
                return lines[0], lines[1]
            return (lines[0] if lines else None), None
        p_ver = subprocess.run(
            [lean_exe, "--version"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        p_lib = subprocess.run(
            [lean_exe, "--print-libdir"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    ver = (p_ver.stdout.strip() or p_ver.stderr.strip()).splitlines()[0].strip() if p_ver.returncode == 0 and (p_ver.stdout.strip() or p_ver.stderr.strip()) else None
    libdir = p_lib.stdout.strip() if p_lib.returncode == 0 and p_lib.stdout.strip() else None
    return ver, libdir


def _check_set_option_lexical(cleaned: str, fname: str) -> List[Dict[str, Any]]:
    """词法级检查整份源码全部 set_option（含跨行、分号拼接与 `set_option ... in` 局部作用域形式）。"""
    violations: List[Dict[str, Any]] = []
    lines = cleaned.splitlines()
    for m in re.finditer(r"(?<![A-Za-z0-9_'])\bset_option\b(?![A-Za-z0-9_'])", cleaned):
        start = m.start()
        lineno = cleaned.count("\n", 0, start) + 1
        line_text = lines[lineno - 1] if lineno - 1 < len(lines) else ""
        # 必须整行独占且精确为 `set_option autoImplicit false`，且下一非空词元不得是 `in`
        if not re.match(r"^\s*set_option\s+autoImplicit\s+false\s*$", line_text):
            violations.append({
                "file": fname,
                "line": lineno,
                "code": "FORBIDDEN_SET_OPTION",
                "detail": f"仅允许独占行白名单 `set_option autoImplicit false`，拒绝: {line_text.strip()}",
            })
            continue
        after_line = cleaned[m.end() :]
        after_nl = after_line.split("\n", 1)[1] if "\n" in after_line else ""
        if re.match(r"^\s*in\b", after_nl):
            violations.append({
                "file": fname,
                "line": lineno,
                "code": "FORBIDDEN_SET_OPTION",
                "detail": "禁止使用跨行 `set_option ... in` 局部作用域形式",
            })
    return violations


def _run_kernel_audit_native(
    lean_files: List[Path],
    raw_sources: Dict[str, str],
    driver_path: Path,
    *,
    lean_exe: str,
    libdir: str,
    allowed_import_modules: Set[str],
) -> Dict[str, Any]:
    """在洁净空临时目录 + 隔离 LEAN_PATH 中执行 AST 头部检查、现编 .olean 与真内核 KernelAudit 审计。"""
    env = {**os.environ, **_NONINTERACTIVE_ENV}
    with tempfile.TemporaryDirectory(prefix="axiom-kernel-audit-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        env["LEAN_PATH"] = f"{tmp_dir}{os.pathsep}{libdir}"
        staged: List[Tuple[str, Path, str]] = []
        for fpath in lean_files:
            raw = raw_sources[fpath.name]
            dst = tmp_path / fpath.name
            dst.write_text(raw, encoding="utf-8", newline="\n")
            src_sha = hashlib.sha256(raw.replace("\r\n", "\n").encode("utf-8")).hexdigest()
            staged.append((fpath.stem, dst, src_sha))

        # 第一步：编译前调用 Lean.Parser.parseHeader 校验全部显式与隐式导入（越权即硬短路，绝不编译！）
        header_proc = subprocess.run(
            [lean_exe, "--run", str(driver_path), "header-batch", *(str(dst) for _, dst, _ in staged)],
            cwd=tmp_dir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        header_out_lines = [ln for ln in header_proc.stdout.splitlines() if ln.strip()]
        if header_proc.returncode != 0 or not header_out_lines or not header_out_lines[-1].startswith("{"):
            return {
                "ok": False,
                "stage": "parse_header",
                "error": (header_proc.stderr.strip() or header_proc.stdout.strip())[-600:],
                "modules": [],
            }
        header_rep = json.loads(header_out_lines[-1])
        header_violations: List[Dict[str, Any]] = []
        for hitem in header_rep.get("files", []):
            hfile = Path(str(hitem.get("file") or "")).name
            for err in hitem.get("parse_errors", []):
                header_violations.append({
                    "file": hfile,
                    "line": 1,
                    "code": "HEADER_PARSE_ERROR",
                    "detail": f"Parser.parseHeader 诊断错误: {err}",
                })
            for imp in hitem.get("imports", []):
                if imp not in allowed_import_modules:
                    header_violations.append({
                        "file": hfile,
                        "line": 1,
                        "code": "FORBIDDEN_HEADER_IMPORT",
                        "detail": f"Parser.parseHeader 检出未授权导入: {imp}（白名单: {sorted(allowed_import_modules)}）",
                    })
        if header_violations:
            return {
                "ok": False,
                "stage": "parse_header",
                "violations": header_violations,
                "modules": [],
            }

        # 第二步：在洁净临时目录逐模块现编 .olean 并双哈希绑定
        compile_results: Dict[str, Dict[str, Any]] = {}
        compiled_pairs: List[str] = []
        all_compiled = True
        for mod_name, dst, src_sha in staged:
            olean_path = tmp_path / f"{mod_name}.olean"
            cproc = subprocess.run(
                [lean_exe, "-o", str(olean_path), str(dst)],
                cwd=tmp_dir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
            )
            olean_sha = hashlib.sha256(olean_path.read_bytes()).hexdigest() if (cproc.returncode == 0 and olean_path.is_file()) else None
            compile_results[mod_name] = {
                "file": dst.name,
                "module": mod_name,
                "returncode": cproc.returncode,
                "source_sha256": src_sha,
                "olean_sha256": olean_sha,
                "stderr_tail": (cproc.stderr.strip() or cproc.stdout.strip())[-600:],
            }
            if cproc.returncode == 0 and olean_sha:
                compiled_pairs.extend([mod_name, str(dst)])
            else:
                all_compiled = False

        if not all_compiled:
            return {
                "ok": False,
                "stage": "compile_olean",
                "compile_results": compile_results,
                "modules": [],
            }

        # 第三步：单次启动 KernelAudit.lean audit-batch 完成内核重放、安全/扩展属性与全量 collectAxioms 校验
        audit_proc = subprocess.run(
            [lean_exe, "--run", str(driver_path), "audit-batch", *compiled_pairs],
            cwd=tmp_dir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        audit_lines = [ln for ln in audit_proc.stdout.splitlines() if ln.strip()]
        if not audit_lines or not audit_lines[-1].startswith("{"):
            return {
                "ok": False,
                "stage": "kernel_audit",
                "compile_results": compile_results,
                "error": (audit_proc.stderr.strip() or audit_proc.stdout.strip())[-600:],
                "modules": [],
            }
        audit_rep = json.loads(audit_lines[-1])
        return {
            "ok": bool(audit_rep.get("ok")) and audit_proc.returncode == 0,
            "audit_returncode": audit_proc.returncode,
            "stage": "kernel_audit",
            "compile_results": compile_results,
            "modules": audit_rep.get("modules", []),
        }


def _run_kernel_audit_wsl(
    lean_files: List[Path],
    raw_sources: Dict[str, str],
    driver_path: Path,
    *,
    lean_exe: str,
    libdir: str,
    allowed_import_modules: Set[str],
) -> Dict[str, Any]:
    """通过 WSL2 原生临时目录执行隔离编译与 KernelAudit.lean 审计，彻底规避 /mnt/c 性能与空格路径坑。"""
    env = {**os.environ, **_NONINTERACTIVE_ENV}
    wsl_driver = _to_wsl_path(driver_path)
    payload_in = {
        "lean_exe": lean_exe,
        "libdir": libdir,
        "driver": wsl_driver,
        "allowed_imports": sorted(allowed_import_modules),
        "files": [{"module": p.stem, "name": p.name, "source": raw_sources[p.name]} for p in lean_files],
    }
    wsl_runner = r"""
import hashlib, json, os, pathlib, subprocess, sys, tempfile

cfg = json.loads(sys.stdin.read())
lean_exe = cfg["lean_exe"]
libdir = cfg["libdir"]
driver = cfg["driver"]
allowed = set(cfg["allowed_imports"])

with tempfile.TemporaryDirectory(prefix="axiom-ka-wsl-") as td:
    tdp = pathlib.Path(td)
    env = {**os.environ, "LEAN_PATH": f"{td}:{libdir}"}
    staged = []
    for item in cfg["files"]:
        dst = tdp / item["name"]
        dst.write_text(item["source"], encoding="utf-8", newline="\n")
        src_sha = hashlib.sha256(item["source"].replace("\r\n", "\n").encode("utf-8")).hexdigest()
        staged.append((item["module"], dst, src_sha))

    hp = subprocess.run([lean_exe, "--run", driver, "header-batch", *(str(d) for _, d, _ in staged)],
                        cwd=td, env=env, capture_output=True, text=True, timeout=90)
    hlines = [ln for ln in hp.stdout.splitlines() if ln.strip()]
    if hp.returncode != 0 or not hlines or not hlines[-1].startswith("{"):
        print(json.dumps({"ok": False, "stage": "parse_header", "error": (hp.stderr or hp.stdout)[-600:], "modules": []}))
        sys.exit(0)
    hrep = json.loads(hlines[-1])
    hviol = []
    for hitem in hrep.get("files", []):
        hf = pathlib.Path(str(hitem.get("file") or "")).name
        for err in hitem.get("parse_errors", []):
            hviol.append({"file": hf, "line": 1, "code": "HEADER_PARSE_ERROR", "detail": f"Parser.parseHeader 诊断错误: {err}"})
        for imp in hitem.get("imports", []):
            if imp not in allowed:
                hviol.append({"file": hf, "line": 1, "code": "FORBIDDEN_HEADER_IMPORT", "detail": f"Parser.parseHeader 检出未授权导入: {imp}"})
    if hviol:
        print(json.dumps({"ok": False, "stage": "parse_header", "violations": hviol, "modules": []}))
        sys.exit(0)

    cresults = {}
    pairs = []
    all_ok = True
    for mod, dst, src_sha in staged:
        olean = tdp / f"{mod}.olean"
        cp = subprocess.run([lean_exe, "-o", str(olean), str(dst)], cwd=td, env=env, capture_output=True, text=True, timeout=90)
        osha = hashlib.sha256(olean.read_bytes()).hexdigest() if (cp.returncode == 0 and olean.is_file()) else None
        cresults[mod] = {
            "file": dst.name, "module": mod, "returncode": cp.returncode,
            "source_sha256": src_sha, "olean_sha256": osha,
            "stderr_tail": (cp.stderr.strip() or cp.stdout.strip())[-600:],
        }
        if cp.returncode == 0 and osha:
            pairs.extend([mod, str(dst)])
        else:
            all_ok = False
    if not all_ok:
        print(json.dumps({"ok": False, "stage": "compile_olean", "compile_results": cresults, "modules": []}))
        sys.exit(0)

    ap = subprocess.run([lean_exe, "--run", driver, "audit-batch", *pairs],
                        cwd=td, env=env, capture_output=True, text=True, timeout=120)
    alines = [ln for ln in ap.stdout.splitlines() if ln.strip()]
    if not alines or not alines[-1].startswith("{"):
        print(json.dumps({"ok": False, "stage": "kernel_audit", "compile_results": cresults, "error": (ap.stderr or ap.stdout)[-600:], "modules": []}))
        sys.exit(0)
    arep = json.loads(alines[-1])
    print(json.dumps({"ok": bool(arep.get("ok")) and ap.returncode == 0, "audit_returncode": ap.returncode, "stage": "kernel_audit", "compile_results": cresults, "modules": arep.get("modules", [])}))
"""
    try:
        proc = subprocess.run(
            ["wsl", "--", "python3", "-B", "-X", "utf8", "-c", wsl_runner],
            input=json.dumps(payload_in, ensure_ascii=False),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "stage": "wsl_transport", "error": str(exc), "modules": []}
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if proc.returncode != 0 or not lines or not lines[-1].startswith("{"):
        return {
            "ok": False,
            "stage": "wsl_transport",
            "error": (proc.stderr.strip() or proc.stdout.strip())[-600:],
            "modules": [],
        }
    return json.loads(lines[-1])


def _evaluate_kernel_audit_modules(
    kernel_rep: Dict[str, Any],
    *,
    expected_modules: List[str],
    allowed_import_modules: Set[str],
    lean_version: Optional[str],
    is_default_dir: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, List[str]], Dict[str, List[str]], Dict[str, Any]]:
    """将 KernelAudit.lean 的原始内核结构化输出转化为严格违例列表、逐文件结果、无歧义定理索引与黄金基线核验摘要。"""
    violations: List[Dict[str, Any]] = []
    file_results: List[Dict[str, Any]] = []
    kernel_theorems: Dict[str, Dict[str, Any]] = {}
    kernel_defns: Dict[str, List[str]] = {}
    ambiguous_short_names: Dict[str, List[str]] = {}
    golden_summary: Dict[str, Any] = {
        "expected_version_prefix": GOLDEN_LEAN_VERSION_PREFIX,
        "detected_version": lean_version,
        "checked": False,
        "checked_count": 0,
        "expected_count": len(LOGIC_CORE_GOLDEN_TYPE_HASHES),
        "status": "not_applicable",
    }
    compile_results = kernel_rep.get("compile_results") or {}

    if kernel_rep.get("violations"):
        violations.extend(kernel_rep["violations"])

    if kernel_rep.get("stage") == "compile_olean":
        for mod, cres in compile_results.items():
            ok_c = cres.get("returncode") == 0 and bool(cres.get("olean_sha256"))
            file_results.append({
                "file": cres.get("file", f"{mod}.lean"),
                "returncode": cres.get("returncode", -1),
                "ok": False,
                "axioms_ok": False,
                "axioms_used": [],
                "checked_axioms_count": 0,
                "expected_axioms_count": 0,
                "missing_axiom_theorems": [],
                "disallowed_axioms": [],
                "source_sha256": cres.get("source_sha256"),
                "olean_sha256": cres.get("olean_sha256"),
                "replay_ok": False,
                "stderr_tail": cres.get("stderr_tail", ""),
            })
            if not ok_c:
                violations.append({
                    "file": cres.get("file", f"{mod}.lean"),
                    "line": 1,
                    "code": "LEAN_COMPILE_FAILED",
                    "detail": f"Lean 编译失败: {cres.get('stderr_tail', '')[:240]}",
                })
        return violations, file_results, kernel_theorems, kernel_defns, ambiguous_short_names, golden_summary

    if not kernel_rep.get("modules") and not kernel_rep.get("ok"):
        violations.append({
            "file": "KernelAudit.lean",
            "line": 1,
            "code": "KERNEL_AUDIT_EXEC_FAILED",
            "detail": str(kernel_rep.get("error") or f"真内核审计阶段 {kernel_rep.get('stage')} 失败"),
        })
        return violations, file_results, kernel_theorems, kernel_defns, ambiguous_short_names, golden_summary

    audit_rc = kernel_rep.get("audit_returncode")
    if audit_rc is not None and audit_rc != 0 and kernel_rep.get("ok"):
        violations.append({
            "file": "KernelAudit.lean",
            "line": 1,
            "code": "KERNEL_AUDIT_NONZERO_EXIT",
            "detail": f"KernelAudit 审计进程返回非零退出码 {audit_rc}",
        })

    returned_mods = [str(m.get("module") or "") for m in (kernel_rep.get("modules") or [])]
    if len(returned_mods) != len(set(returned_mods)) or set(returned_mods) != set(expected_modules):
        violations.append({
            "file": "KernelAudit.lean",
            "line": 1,
            "code": "KERNEL_MODULE_SET_MISMATCH",
            "detail": f"真内核返回模块集合 {returned_mods} 与待审集合 {expected_modules} 不一致（禁止遗漏或重复模块）",
        })

    version_matches_golden = bool(lean_version and f"version {GOLDEN_LEAN_VERSION_PREFIX}" in lean_version)
    if "LogicCore" in expected_modules and is_default_dir:
        if not lean_version:
            golden_summary["status"] = "version_unavailable"
            violations.append({
                "file": "LogicCore.lean",
                "line": 1,
                "code": "LEAN_VERSION_UNAVAILABLE",
                "detail": "无法获取 Lean 编译器版本信息，拒绝跳过 LogicCore 40 条黄金类型签名校验",
            })
        elif not version_matches_golden:
            golden_summary["status"] = "version_mismatch_skipped"
            violations.append({
                "file": "LogicCore.lean",
                "line": 1,
                "code": "GOLDEN_BASELINE_VERSION_MISMATCH",
                "detail": f"当前 Lean 版本 {lean_version!r} 与黄金基线版本前缀 {GOLDEN_LEAN_VERSION_PREFIX!r} 不匹配，拒绝静默跳过 40 条黄金签名校验",
            })
        else:
            golden_summary["checked"] = True
            golden_summary["status"] = "verified"

    for m in kernel_rep.get("modules", []):
        mod_name = str(m.get("module") or "")
        fname = f"{mod_name}.lean"
        cres = compile_results.get(mod_name, {})
        mod_ok = True

        if not m.get("ok") or not m.get("replay_ok"):
            mod_ok = False
            violations.append({
                "file": fname,
                "line": 1,
                "code": "KERNEL_REPLAY_FAILED",
                "detail": f"Lean Kernel.Environment.replay 重放失败: {m.get('replay_error') or m.get('error') or 'unknown'}",
            })

        if int(m.get("constant_count") or 0) <= 0:
            mod_ok = False
            violations.append({
                "file": fname,
                "line": 1,
                "code": "EMPTY_MODULE_CONSTANTS",
                "detail": f"模块 {mod_name} 内核常量集合为空",
            })

        for imp in m.get("module_imports", []):
            if imp not in allowed_import_modules:
                mod_ok = False
                violations.append({
                    "file": fname,
                    "line": 1,
                    "code": "FORBIDDEN_HEADER_IMPORT",
                    "detail": f"模块内核导入表含未授权模块: {imp}",
                })

        if m.get("axiom_decls"):
            mod_ok = False
            violations.append({
                "file": fname,
                "line": 1,
                "code": "KERNEL_FORBIDDEN_AXIOM_DECL",
                "detail": f"内核检出自定义公理常量: {m['axiom_decls']}",
            })

        if m.get("extern_decls") or m.get("opaque_decls"):
            mod_ok = False
            violations.append({
                "file": fname,
                "line": 1,
                "code": "KERNEL_FORBIDDEN_EXTERN_OR_OPAQUE",
                "detail": f"内核检出 extern/opaque/partial 常量: extern={m.get('extern_decls')}, opaque={m.get('opaque_decls')}",
            })

        if m.get("unsafe_decls") or m.get("implemented_by_decls"):
            mod_ok = False
            violations.append({
                "file": fname,
                "line": 1,
                "code": "KERNEL_UNSAFE_OR_PARTIAL",
                "detail": f"内核检出 unsafe 或 @[implemented_by] 常量: unsafe={m.get('unsafe_decls')}, implemented_by={m.get('implemented_by_decls')}",
            })

        if m.get("csimp_decls") or m.get("init_decls"):
            mod_ok = False
            violations.append({
                "file": fname,
                "line": 1,
                "code": "KERNEL_FORBIDDEN_INIT_OR_CSIMP",
                "detail": f"内核检出 initialize 或 @[csimp] 扩展常量: init={m.get('init_decls')}, csimp={m.get('csimp_decls')}",
            })

        all_axioms = list(m.get("all_axioms") or [])
        disallowed = sorted({a for a in all_axioms if a not in ALLOWED_LEAN_AXIOMS})
        if disallowed:
            mod_ok = False
            violations.append({
                "file": fname,
                "line": 1,
                "code": "DISALLOWED_KERNEL_AXIOM",
                "detail": f"Lean.collectAxioms 传递闭包检出非白名单公理: {disallowed}",
            })

        # 内核级 _root_.Nonempty 见证与归纳类型构造子核验
        nonempty_by_target: Dict[str, str] = {}
        for item in m.get("nonempty_targets", []):
            if ":" in item:
                tgt, thm = item.split(":", 1)
                nonempty_by_target[tgt] = thm
        for ind in m.get("inductives", []):
            ind_full = str(ind.get("name") or "")
            ctors_full = list(ind.get("ctors") or [])
            if not ctors_full:
                mod_ok = False
                violations.append({
                    "file": fname,
                    "line": 1,
                    "code": "EMPTY_INDUCTIVE_TYPE",
                    "detail": f"内核检出无构造子的空归纳类型: {ind_full}",
                })
            if ind_full not in nonempty_by_target:
                mod_ok = False
                violations.append({
                    "file": fname,
                    "line": 1,
                    "code": "KERNEL_MISSING_NONEMPTY_WITNESS",
                    "detail": f"内核未找到针对 {ind_full} 的零前提 _root_.Nonempty 构造性定理（已防御命名空间 Nonempty 阴影）",
                })
            if mod_name == "LogicCore" and ind_full.endswith(".ObjectiveRole"):
                short_ctors = {_camel_to_snake(c.split(".")[-1]) for c in ctors_full}
                if short_ctors != set(OBJECTIVE_ROLES):
                    mod_ok = False
                    violations.append({
                        "file": fname,
                        "line": 1,
                        "code": "ROLE_DRIFT",
                        "detail": f"内核 LogicCore.ObjectiveRole 构造子 {sorted(short_ctors)} 与状态机 {sorted(OBJECTIVE_ROLES)} 不一致",
                    })

        # Astra #5 清零：以 full_name 为主键建索引，短名冲突严禁静默覆盖并登记为歧义短名
        mod_thms: Dict[str, Any] = {}
        short_to_fulls: Dict[str, List[str]] = {}
        for t in m.get("theorems", []):
            full_name = str(t.get("name") or "")
            short_name = full_name.split(".")[-1]
            type_repr = str(t.get("type_repr") or "")
            type_sha = hashlib.sha256(type_repr.encode("utf-8")).hexdigest()[:16]
            t_entry = {
                "full_name": full_name,
                "axioms": list(t.get("axioms") or []),
                "has_false_hyp": bool(t.get("has_false_hyp")),
                "trivial_true": bool(t.get("trivial_true")),
                "type_sha256": type_sha,
            }
            mod_thms[full_name] = t_entry
            short_to_fulls.setdefault(short_name, []).append(full_name)

        ambig_list: List[str] = []
        for sname, fulls in short_to_fulls.items():
            if len(fulls) == 1:
                mod_thms.setdefault(sname, mod_thms[fulls[0]])
            else:
                ambig_list.append(sname)
        ambiguous_short_names[mod_name] = sorted(ambig_list)
        kernel_theorems[mod_name] = mod_thms
        kernel_defns[mod_name] = [str(d) for d in m.get("defn_names", [])]

        if mod_name in REQUIRED_THEOREMS and (is_default_dir or mod_name == "LogicCore"):
            for req in REQUIRED_THEOREMS[mod_name]:
                if req in ambig_list:
                    mod_ok = False
                    violations.append({
                        "file": fname,
                        "line": 1,
                        "code": "AMBIGUOUS_THEOREM_NAME",
                        "detail": f"内核模块 {mod_name} 存在多个同短名必含定理 {req!r}: {short_to_fulls.get(req)}",
                    })
                    continue
                tinfo = mod_thms.get(req)
                if not tinfo:
                    mod_ok = False
                    violations.append({
                        "file": fname,
                        "line": 1,
                        "code": "MISSING_REQUIRED_THEOREM",
                        "detail": f"内核模块 {mod_name} 缺少必含 thmInfo 定理: {req}",
                    })
                    continue
                if tinfo["has_false_hyp"] or tinfo["trivial_true"]:
                    mod_ok = False
                    violations.append({
                        "file": fname,
                        "line": 1,
                        "code": "VACUOUS_THEOREM_SIGNATURE",
                        "detail": f"内核定理 {mod_name}.{req} 含 False 前件或退化为 True 结论",
                    })
                if mod_name == "LogicCore" and version_matches_golden and req in LOGIC_CORE_GOLDEN_TYPE_HASHES:
                    golden_summary["checked_count"] = int(golden_summary["checked_count"]) + 1
                    expected_sig = LOGIC_CORE_GOLDEN_TYPE_HASHES[req]
                    if tinfo["type_sha256"] != expected_sig:
                        mod_ok = False
                        golden_summary["status"] = "mismatch"
                        violations.append({
                            "file": fname,
                            "line": 1,
                            "code": "GOLDEN_SIGNATURE_MISMATCH",
                            "detail": f"内核定理 LogicCore.{req} ({tinfo['full_name']}) 类型签名哈希 {tinfo['type_sha256']} 与黄金基线 {expected_sig} 不符",
                        })

        pub_count = len(m.get("theorems", []))
        file_results.append({
            "file": fname,
            "returncode": 0 if mod_ok else 2,
            "ok": mod_ok,
            "axioms_ok": len(disallowed) == 0,
            "axioms_used": sorted(set(all_axioms)),
            "checked_axioms_count": pub_count,
            "expected_axioms_count": pub_count,
            "missing_axiom_theorems": [],
            "disallowed_axioms": disallowed,
            "constant_count": int(m.get("constant_count") or 0),
            "replay_ok": bool(m.get("replay_ok")),
            "source_sha256": cres.get("source_sha256"),
            "olean_sha256": cres.get("olean_sha256"),
            "stderr_tail": "",
        })

    return violations, file_results, kernel_theorems, kernel_defns, ambiguous_short_names, golden_summary


def audit_lean_suite(
    lean_dir: Optional[Path] = None,
    *,
    run_lean: bool = False,
    lean_cmd: str = "",
    wsl_lean: bool = False,
    profile: str = "cae",
    allow_static_bypass_for_kernel_test: bool = False,
) -> Dict[str, Any]:
    """核验纯核 Lean 4 骨架：词法/AST 静态预检（失败即硬短路）+ 受控真内核 KernelAudit.lean 校验。"""
    prof = profile if profile in PROFILE_LEAN_MODULES else "cae"
    root = _reject_links(lean_dir or _default_lean_dir(), label="Lean 目录")
    if not root.is_dir():
        raise GateError("LEAN_DIR_MISSING", f"Lean 目录不存在: {root}")
    is_default_dir = (lean_dir is None) or (root.resolve() == _default_lean_dir().resolve())
    all_lean_files = sorted(p for p in root.glob("*.lean") if p.is_file())
    if is_default_dir and prof == "core":
        allowed_mods = set(PROFILE_LEAN_MODULES["core"])
        lean_files = [p for p in all_lean_files if p.stem in allowed_mods]
    else:
        lean_files = all_lean_files
    if not lean_files:
        raise GateError("LEAN_FILES_MISSING", f"Lean 目录下未找到 .lean 文件: {root}")

    violations: List[Dict[str, Any]] = []
    theorem_index: Dict[str, List[str]] = {}
    public_theorem_index: Dict[str, List[str]] = {}
    structure_index: Dict[str, List[str]] = {}
    inductive_index: Dict[str, List[str]] = {}
    nonempty_witnesses: Dict[str, List[str]] = {}
    raw_sources: Dict[str, str] = {}
    source_paths: Dict[str, str] = {}
    source_hashes: Dict[str, str] = {}
    source_hash_records: Dict[str, Dict[str, Any]] = {}

    for fpath in lean_files:
        if _is_link_or_reparse(fpath):
            violations.append({"file": fpath.name, "line": 1, "code": "SYMLINK_REJECTED", "detail": "禁止符号链接"})
            continue
        raw_bytes = fpath.read_bytes()
        hrec = compute_bytes_hashes(raw_bytes, fpath)
        raw = raw_bytes.decode("utf-8-sig", errors="replace")
        raw_sources[fpath.name] = raw
        source_paths[fpath.stem] = str(fpath.resolve())
        source_hashes[fpath.stem] = hrec["artifact_sha256"]
        source_hash_records[fpath.stem] = hrec
        cleaned = _strip_lean_comments_and_strings(raw)
        module = fpath.stem

        if not allow_static_bypass_for_kernel_test:
            violations.extend(_check_set_option_lexical(cleaned, fpath.name))
            for lineno, line in enumerate(cleaned.splitlines(), 1):
                m_imp = re.match(r"^\s*import\s+([A-Za-z0-9_.]+)", line)
                if m_imp:
                    target_mod = m_imp.group(1)
                    top = target_mod.split(".", 1)[0]
                    if top in FORBIDDEN_LEAN_IMPORTS or (top != "Init"):
                        violations.append({
                            "file": fpath.name,
                            "line": lineno,
                            "code": "FORBIDDEN_IMPORT",
                            "detail": f"零依赖纯核 Lean 骨架禁止外部导入: import {target_mod}",
                        })
                if "«" in line or "»" in line:
                    violations.append({
                        "file": fpath.name,
                        "line": lineno,
                        "code": "FORBIDDEN_QUOTED_IDENT",
                        "detail": "纯核 Lean 骨架禁止使用法式引号标识符 «...» 以防绕过定理索引与公理核验",
                    })
                if re.search(r"(?:^|\s)#(?:exit|eval)\b", line):
                    violations.append({
                        "file": fpath.name,
                        "line": lineno,
                        "code": "FORBIDDEN_LEAN_DIRECTIVE",
                        "detail": "禁止使用 #exit 提前截断内核检查或 #eval 在编译期执行求值",
                    })
                for tok in FORBIDDEN_LEAN_TOKENS:
                    if re.search(rf"(?<![A-Za-z0-9_'])\b{re.escape(tok)}\b(?![A-Za-z0-9_'])", line):
                        violations.append({
                            "file": fpath.name,
                            "line": lineno,
                            "code": "FORBIDDEN_LEAN_TOKEN",
                            "detail": f"检测到禁止词元 '{tok}'",
                        })
                m_shadow = re.match(
                    r"^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+|protected\s+)?(?:def|abbrev|structure|inductive|class)\s+([A-Za-z_][A-Za-z0-9_']*)\b",
                    line,
                )
                if m_shadow and m_shadow.group(1) in RESERVED_LOGIC_NAMES:
                    violations.append({
                        "file": fpath.name,
                        "line": lineno,
                        "code": "FORBIDDEN_SHADOW_RESERVED_NAME",
                        "detail": f"禁止在模块内重定义阴影核心逻辑保留名 '{m_shadow.group(1)}'",
                    })

        structs = re.findall(r"(?m)^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+)?structure\s+([A-Za-z_][A-Za-z0-9_']*)\b", cleaned)
        inductives = re.findall(r"(?m)^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+)?inductive\s+([A-Za-z_][A-Za-z0-9_']*)\b", cleaned)
        all_theorems = re.findall(r"(?m)^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+)?theorem\s+([A-Za-z_][A-Za-z0-9_']*)\b", cleaned)
        pub_theorems = re.findall(r"(?m)^\s*(?:@\[[^\]]*\]\s*)*theorem\s+([A-Za-z_][A-Za-z0-9_']*)\b", cleaned)
        structure_index[module] = structs
        inductive_index[module] = inductives
        theorem_index[module] = all_theorems
        public_theorem_index[module] = pub_theorems

        if not allow_static_bypass_for_kernel_test:
            seen_type_names: Set[str] = set()
            for tname in (*structs, *inductives):
                if tname in seen_type_names:
                    violations.append({
                        "file": fpath.name,
                        "line": 1,
                        "code": "DUPLICATE_TYPE_NAME",
                        "detail": f"同一文件内禁止重复声明同名类型 '{tname}'（防跨命名空间错绑 Nonempty 见证）",
                    })
                seen_type_names.add(tname)

            decl_blocks = re.split(
                r"(?m)(?=^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+)?(?:theorem|example|def|abbrev|structure|inductive|namespace|end)\b)",
                cleaned,
            )

            witness_hits: List[str] = []
            for sname in structs:
                hit = _find_strict_nonempty_witness(decl_blocks, sname)
                if hit:
                    witness_hits.append(f"{sname}:{hit}")
                else:
                    violations.append({
                        "file": fpath.name,
                        "line": 1,
                        "code": "MISSING_NONEMPTY_WITNESS",
                        "detail": f"正面结构体 {sname} 缺少零前提绑定器、类型严格为 Nonempty {sname} 的构造性实例定理",
                    })
            for iname in inductives:
                ctors = _parse_inductive_ctors(decl_blocks, iname)
                if not ctors:
                    violations.append({
                        "file": fpath.name,
                        "line": 1,
                        "code": "EMPTY_INDUCTIVE_TYPE",
                        "detail": f"归纳类型 {iname} 不含任何构造子，存在空类型退化风险",
                    })
                hit = _find_strict_nonempty_witness(decl_blocks, iname)
                if hit:
                    witness_hits.append(f"{iname}:{hit}")
                else:
                    violations.append({
                        "file": fpath.name,
                        "line": 1,
                        "code": "MISSING_NONEMPTY_WITNESS",
                        "detail": f"归纳类型 {iname} 缺少零前提绑定器的构造性 Nonempty 实例定理",
                    })
            nonempty_witnesses[module] = witness_hits

            if module == "LogicCore":
                role_ctors = _parse_inductive_ctors(decl_blocks, "ObjectiveRole")
                lean_role_set = {_camel_to_snake(c) for c in role_ctors}
                if lean_role_set != set(OBJECTIVE_ROLES):
                    violations.append({
                        "file": fpath.name,
                        "line": 1,
                        "code": "ROLE_DRIFT",
                        "detail": (
                            f"LogicCore.ObjectiveRole 构造子集合 {sorted(lean_role_set)} "
                            f"与状态机 OBJECTIVE_ROLES {sorted(OBJECTIVE_ROLES)} 不一致"
                        ),
                    })
            if module in REQUIRED_THEOREMS:
                pub_set = set(pub_theorems)
                for req in REQUIRED_THEOREMS[module]:
                    if req not in pub_set:
                        violations.append({
                            "file": fpath.name,
                            "line": 1,
                            "code": "MISSING_REQUIRED_THEOREM",
                            "detail": f"模块 {module} 缺少契约必含公开定理: {req}",
                        })
        else:
            nonempty_witnesses[module] = []

    if is_default_dir:
        present_modules = {p.stem for p in lean_files}
        for core_mod in PROFILE_LEAN_MODULES[prof]:
            if core_mod not in present_modules:
                violations.append({
                    "file": f"{core_mod}.lean",
                    "line": 1,
                    "code": "MISSING_CORE_MODULE",
                    "detail": f"默认骨架目录（profile={prof}）缺少核心 Lean 模块: {core_mod}.lean",
                })

    static_ok = len(violations) == 0
    compiler_info: Dict[str, Any] = {
        "requested": bool(run_lean),
        "executed": False,
        "status": "not_requested",
        "executable": None,
        "version": None,
        "driver_sha256": KERNEL_AUDIT_DRIVER_SHA256,
        "transport": "wsl" if wsl_lean else "native",
        "files": [],
    }
    kernel_theorems: Dict[str, Dict[str, Any]] = {}
    kernel_defns: Dict[str, List[str]] = {}
    ambiguous_short_names: Dict[str, List[str]] = {}
    golden_summary: Dict[str, Any] = {
        "expected_version_prefix": GOLDEN_LEAN_VERSION_PREFIX,
        "detected_version": None,
        "checked": False,
        "checked_count": 0,
        "expected_count": len(LOGIC_CORE_GOLDEN_TYPE_HASHES),
        "status": "not_requested",
    }

    # B1 硬短路：静态预检一旦失败，严禁启动真实 Lean 编译器执行不可信源码
    if run_lean and not static_ok:
        compiler_info["status"] = "skipped_due_to_static_violations"
    elif run_lean:
        driver_path = _verify_kernel_driver()
        if wsl_lean:
            try:
                probe = subprocess.run(
                    [
                        "wsl", "--", "python3", "-B", "-X", "utf8", "-c",
                        "import pathlib, shutil; p=shutil.which('lean') or str(pathlib.Path.home()/'.elan'/'bin'/'lean'); print(p if pathlib.Path(p).is_file() else '')",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                )
                lean_exe = probe.stdout.strip() if probe.returncode == 0 and probe.stdout.strip() else None
            except (OSError, subprocess.SubprocessError):
                lean_exe = None
        else:
            lean_exe = _find_lean_executable(lean_cmd)

        if not lean_exe:
            compiler_info["status"] = "not_installed"
        else:
            ver, libdir = _probe_lean_version_and_libdir(lean_exe=lean_exe, use_wsl=wsl_lean)
            compiler_info["executable"] = f"wsl:{lean_exe}" if wsl_lean else lean_exe
            compiler_info["version"] = ver
            if not libdir:
                compiler_info["status"] = "failed"
                violations.append({
                    "file": "KernelAudit.lean",
                    "line": 1,
                    "code": "LEAN_LIBDIR_UNAVAILABLE",
                    "detail": "无法获取 Lean 标准库路径（lean --print-libdir）",
                })
            else:
                compiler_info["executed"] = True
                allowed_imports = {"Init"} | {p.stem for p in lean_files}
                if wsl_lean:
                    krep = _run_kernel_audit_wsl(
                        lean_files,
                        raw_sources,
                        driver_path,
                        lean_exe=lean_exe,
                        libdir=libdir,
                        allowed_import_modules=allowed_imports,
                    )
                else:
                    krep = _run_kernel_audit_native(
                        lean_files,
                        raw_sources,
                        driver_path,
                        lean_exe=lean_exe,
                        libdir=libdir,
                        allowed_import_modules=allowed_imports,
                    )
                k_viols, file_results, kernel_theorems, kernel_defns, ambiguous_short_names, golden_summary = _evaluate_kernel_audit_modules(
                    krep,
                    expected_modules=[p.stem for p in lean_files],
                    allowed_import_modules=allowed_imports,
                    lean_version=ver,
                    is_default_dir=is_default_dir,
                )
                violations.extend(k_viols)
                compiler_info["files"] = file_results
                compiler_info["golden_signature"] = golden_summary
                all_files_ok = bool(file_results) and all(f["ok"] for f in file_results) and not k_viols
                compiler_info["status"] = "passed" if all_files_ok else "failed"

    ast_ok = len(violations) == 0
    overall_ok = ast_ok and (not run_lean or compiler_info["status"] == "passed")

    return {
        "ok": overall_ok,
        "ast_gate_ok": ast_ok,
        "is_default_dir": is_default_dir,
        "lean_dir": str(root),
        "checked_files": [p.name for p in lean_files],
        "source_paths": source_paths,
        "source_hashes": source_hashes,
        "source_hash_records": source_hash_records,
        "structures": structure_index,
        "inductives": inductive_index,
        "theorems": theorem_index,
        "public_theorems": public_theorem_index,
        "kernel_theorems": kernel_theorems,
        "kernel_defns": kernel_defns,
        "ambiguous_short_names": ambiguous_short_names,
        "nonempty_witnesses": nonempty_witnesses,
        "violation_count": len(violations),
        "violations": violations,
        "lean_compiler": compiler_info,
    }
