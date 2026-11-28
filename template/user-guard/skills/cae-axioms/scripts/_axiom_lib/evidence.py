# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_axiom_lib.evidence - 严格客观见证校验、双哈希锚定与磁盘新鲜度重算。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .contract import (
    PROBE_ROLES,
    GateError,
    _NONINTERACTIVE_ENV,
    _reject_links,
    compute_bytes_hashes,
    compute_file_hashes,
    hashes_match_disk,
    loads_strict_json,
    reject_unknown_fields,
    strict_bool,
    strict_int,
    strict_str,
    strict_str_list,
)

_SOURCE_ANCHOR_KEYS = frozenset({"type", "file", "line_start", "line_end", "excerpt", "note"})
_LEAN_THEOREM_KEYS = frozenset({"type", "module", "theorem", "artifact_files", "artifact_binding", "note"})
_PROBE_EXEC_KEYS = frozenset({
    "type",
    "command",
    "execute",
    "expected_exit_code",
    "recorded_exit_code",
    "receipt_file",
    "expected_output_substring",
    "cwd",
    "probe_role",
    "target_files",
    "domain_spec_file",
    "exhaustive_domain_size",
    "note",
})

_EXIT_CODE_TEXT_RE = re.compile(r"""(?i)(?:["'])?\b(?:exit[_ ]?code|return[_ ]?code|returncode|rc)\b(?:["'])?\s*[:=]\s*(-?\d+)\b""")


def _extract_receipt_exit_codes(raw_text: str) -> List[int]:
    """从离线探针回执提取显式退出码列表（JSON 回执复用严格解码拒绝重复键；无标记返回空列表）。"""
    codes: List[int] = []
    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        # 严格 JSON 解码：重复键（如 {"exit_code": 1, "exit_code": 0}）或非有限浮点立即报错拒收
        try:
            obj = loads_strict_json(stripped, label="离线探针 JSON 回执")
        except GateError as exc:
            if exc.code in ("SCHEMA_DUPLICATE_KEY", "SCHEMA_NONSTANDARD_NUMBER"):
                raise
            obj = None
        if isinstance(obj, dict):
            for k in ("exit_code", "returncode", "return_code", "rc"):
                val = obj.get(k)
                if type(val) is int:
                    codes.append(val)
    for m_rc in _EXIT_CODE_TEXT_RE.finditer(raw_text):
        codes.append(int(m_rc.group(1)))
    return codes


def _anchor_project_file(
    raw_file: str,
    project_root: Path,
    *,
    label: str,
    force_raw: bool = False,
) -> Tuple[Path, str, Dict[str, Any], bytes]:
    """单次读取文件原始字节并计算双哈希，彻底消除建证阶段摘要与内容解析的双读 TOCTOU。"""
    cand = Path(raw_file)
    if not cand.is_absolute():
        cand = project_root / cand
    resolved = _reject_links(cand, label=label).resolve()
    root_resolved = _reject_links(project_root, label="课题根目录").resolve()
    if not resolved.is_relative_to(root_resolved):
        raise GateError("WITNESS_OUT_OF_SCOPE", f"{label}越出项目根目录: {raw_file}")
    if not resolved.is_file():
        raise GateError("WITNESS_FILE_MISSING", f"{label}不存在: {raw_file}")
    rel = resolved.relative_to(root_resolved).as_posix()
    raw_bytes = resolved.read_bytes()
    return resolved, rel, compute_bytes_hashes(raw_bytes, resolved, force_raw=force_raw), raw_bytes


def _verify_single_witness(
    witness: Any,
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool = False,
    context_label: str = "witnesses",
) -> Dict[str, Any]:
    if not isinstance(witness, dict):
        raise GateError("INVALID_WITNESS", f"{context_label} 的每条客观见证必须为 JSON 对象，禁止传裸布尔值或字符串")
    wtype = strict_str(witness.get("type"), f"{context_label}.type")

    if wtype == "source_anchor":
        reject_unknown_fields(witness, _SOURCE_ANCHOR_KEYS, f"{context_label}(source_anchor)")
        raw_file = strict_str(witness.get("file"), f"{context_label}.file")
        excerpt = strict_str(witness.get("excerpt"), f"{context_label}.excerpt")
        line_start = strict_int(witness.get("line_start"), f"{context_label}.line_start", min_value=1)
        line_end = strict_int(witness.get("line_end"), f"{context_label}.line_end", min_value=line_start)
        _, rel, fhash, raw_bytes = _anchor_project_file(raw_file, project_root, label=f"{context_label} source_anchor 文件")
        raw_text = raw_bytes.decode("utf-8-sig", errors="replace")
        lines = raw_text.splitlines()
        if line_end > len(lines):
            raise GateError("WITNESS_LINE_OUT_OF_RANGE", f"{context_label}: {raw_file} 共 {len(lines)} 行，超出声明的 L{line_end}")
        segment = "\n".join(lines[line_start - 1 : line_end])
        norm_excerpt = excerpt.replace("\r\n", "\n")
        if norm_excerpt not in segment:
            raise GateError("WITNESS_EXCERPT_MISMATCH", f"{context_label}: 在 {raw_file}#L{line_start}-L{line_end} 中未找到声明的原文摘录")
        return {
            "type": "source_anchor",
            "file": rel,
            "line_start": line_start,
            "line_end": line_end,
            "excerpt_sha256": hashlib.sha256(norm_excerpt.encode("utf-8")).hexdigest()[:16],
            "segment_sha256": hashlib.sha256(segment.encode("utf-8")).hexdigest(),
            **fhash,
            "verification_level": "file_anchored",
            "verified": True,
        }

    if wtype == "lean_theorem":
        reject_unknown_fields(witness, _LEAN_THEOREM_KEYS, f"{context_label}(lean_theorem)")
        module = strict_str(witness.get("module"), f"{context_label}.module")
        theorem = strict_str(witness.get("theorem"), f"{context_label}.theorem")
        if not lean_audit.get("ast_gate_ok"):
            raise GateError("LEAN_AST_GATE_FAILED", f"{context_label}: Lean 反空真门禁未通过，拒绝接受 lean_theorem 见证")
        comp_info = lean_audit.get("lean_compiler") or {}
        comp_status = str(comp_info.get("status") or "not_requested")
        if comp_info.get("requested") and comp_status != "passed":
            raise GateError(
                "LEAN_COMPILER_NOT_PASSED",
                f"{context_label}: 已启用 --run-lean 但 Lean 编译器状态为 {comp_status}，拒绝接受 lean_theorem 见证",
            )
        kernel_ok = comp_status == "passed"
        kernel_thm_info = None
        if kernel_ok:
            ambiguous = set((lean_audit.get("ambiguous_short_names") or {}).get(module) or [])
            if theorem in ambiguous:
                raise GateError(
                    "AMBIGUOUS_THEOREM_NAME",
                    f"{context_label}: 模块 {module} 中存在多个同短名定理 {theorem!r}，请使用完整 Lean 限定名",
                )
            k_mod_thms = (lean_audit.get("kernel_theorems") or {}).get(module) or {}
            k_mod_defns = (lean_audit.get("kernel_defns") or {}).get(module) or []
            kernel_thm_info = k_mod_thms.get(theorem)
            if not kernel_thm_info:
                if any(d == theorem or d.endswith(f".{theorem}") for d in k_mod_defns):
                    raise GateError(
                        "NOT_A_KERNEL_THEOREM",
                        f"{context_label}: {module}.{theorem} 在 Lean 内核中为 defnInfo 而非 thmInfo 定理，拒绝冒充定理见证",
                    )
                raise GateError("LEAN_THEOREM_NOT_FOUND", f"{context_label}: Lean 内核模块 {module} 中未找到公开定理 {theorem}")
        else:
            known = lean_audit.get("public_theorems", {}).get(module, [])
            if theorem not in known:
                raise GateError("LEAN_THEOREM_NOT_FOUND", f"{context_label}: Lean 模块 {module} 中未声明公开定理 {theorem}")

        # 可选被审工件文件绑定（artifact_bound_lean 充分证书用）
        bound_artifacts: List[Dict[str, Any]] = []
        raw_art_files = witness.get("artifact_files")
        if raw_art_files is not None:
            for af in strict_str_list(raw_art_files, f"{context_label}.artifact_files", non_empty=True):
                _, rel_af, af_hash, _ = _anchor_project_file(af, project_root, label=f"{context_label} artifact_files")
                bound_artifacts.append({"file": rel_af, **af_hash})
        artifact_binding = ""
        if witness.get("artifact_binding") is not None:
            artifact_binding = strict_str(witness.get("artifact_binding"), f"{context_label}.artifact_binding")

        is_builtin_meta = bool(lean_audit.get("is_default_dir")) and module == "LogicCore"
        src_path_str = (lean_audit.get("source_paths") or {}).get(module, "")
        src_sha = (lean_audit.get("source_hashes") or {}).get(module, "")
        src_hash_rec = (lean_audit.get("source_hash_records") or {}).get(module) or {}
        return {
            "type": "lean_theorem",
            "module": module,
            "theorem": theorem,
            "kernel_full_name": kernel_thm_info["full_name"] if kernel_thm_info else None,
            "lean_source_path": src_path_str,
            "lean_source_sha256": src_sha,
            "lean_source_hash_record": src_hash_rec,
            "is_builtin_meta_module": is_builtin_meta,
            "artifact_binding": artifact_binding,
            "artifact_files": bound_artifacts,
            "kernel_type_sha256": kernel_thm_info["type_sha256"] if kernel_thm_info else None,
            "lean_ast_verified": True,
            "lean_compiler_status": comp_status,
            "verification_level": "kernel_compiled" if kernel_ok else "ast_only",
            "verified": kernel_ok,
        }

    if wtype == "probe_exec":
        reject_unknown_fields(witness, _PROBE_EXEC_KEYS, f"{context_label}(probe_exec)")
        cmd = strict_str(witness.get("command"), f"{context_label}.command")
        expected_code = strict_int(witness.get("expected_exit_code", 0), f"{context_label}.expected_exit_code")
        execute_now = strict_bool(witness.get("execute"), f"{context_label}.execute", default=False)
        probe_role = strict_str(witness.get("probe_role"), f"{context_label}.probe_role", allowed=PROBE_ROLES, default="standard")

        # 绑定探针目标脚本文件（按 O-B5 强制走 raw_sha256 失活监测）
        bound_targets: List[Dict[str, Any]] = []
        if witness.get("target_files") is not None:
            for tf in strict_str_list(witness.get("target_files"), f"{context_label}.target_files", non_empty=True):
                _, rel_tf, tf_hash, _ = _anchor_project_file(tf, project_root, label=f"{context_label} target_files", force_raw=True)
                bound_targets.append({"file": rel_tf, **tf_hash})

        # exhaustive_domain 充分证书必须绑定有限可达域枚举文件与基数（O-B1）
        domain_spec_entry: Optional[Dict[str, Any]] = None
        exhaustive_size: Optional[int] = None
        if probe_role == "exhaustive_domain":
            raw_dom = strict_str(witness.get("domain_spec_file"), f"{context_label}.domain_spec_file")
            exhaustive_size = strict_int(witness.get("exhaustive_domain_size"), f"{context_label}.exhaustive_domain_size", min_value=1)
            _, rel_dom, dom_hash, _ = _anchor_project_file(raw_dom, project_root, label=f"{context_label} domain_spec_file")
            domain_spec_entry = {"file": rel_dom, **dom_hash}
        elif witness.get("domain_spec_file") is not None or witness.get("exhaustive_domain_size") is not None:
            raise GateError(
                "SCHEMA_VALUE_ERROR",
                f"{context_label}: domain_spec_file / exhaustive_domain_size 仅允许在 probe_role=exhaustive_domain 时声明",
            )

        if execute_now:
            if not allow_probe_exec:
                raise GateError("PROBE_EXEC_NOT_ALLOWED", f"{context_label}: 探针实时执行需显式传入 --allow-probe-exec")
            sub_cwd = ""
            if witness.get("cwd") is not None:
                sub_cwd = strict_str(witness.get("cwd"), f"{context_label}.cwd", non_empty=False)
            run_dir = project_root.resolve()
            if sub_cwd:
                run_dir = _reject_links(project_root / sub_cwd, label="探针工作目录").resolve()
                if not run_dir.is_relative_to(project_root.resolve()) or not run_dir.is_dir():
                    raise GateError("WITNESS_OUT_OF_SCOPE", f"{context_label}: 探针工作目录非法或越界: {sub_cwd}")
            env = {**os.environ, **_NONINTERACTIVE_ENV}
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(run_dir),
                    env=env,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise GateError("PROBE_EXEC_FAILED", f"{context_label}: 探针执行失败: {exc}") from exc
            if proc.returncode != expected_code:
                raise GateError(
                    "PROBE_EXIT_MISMATCH",
                    f"{context_label}: 探针退出码为 {proc.returncode}，与预期 {expected_code} 不符",
                    {"stdout_tail": proc.stdout[-400:], "stderr_tail": proc.stderr[-400:]},
                )
            exp_sub = ""
            if witness.get("expected_output_substring") is not None:
                exp_sub = strict_str(witness.get("expected_output_substring"), f"{context_label}.expected_output_substring", non_empty=False)
            if exp_sub and exp_sub not in proc.stdout:
                raise GateError("PROBE_OUTPUT_MISMATCH", f"{context_label}: 探针实跑 stdout 未包含预期片段: {exp_sub}")
            return {
                "type": "probe_exec",
                "command": cmd,
                "cwd": sub_cwd,
                "execute": True,
                "executed": True,
                "probe_role": probe_role,
                "expected_exit_code": expected_code,
                "expected_output_substring": exp_sub,
                "exit_code": proc.returncode,
                "stdout_sha256": hashlib.sha256(proc.stdout.replace("\r\n", "\n").encode("utf-8")).hexdigest(),
                "target_files": bound_targets,
                "domain_spec": domain_spec_entry,
                "exhaustive_domain_size": exhaustive_size,
                "verification_level": "live_executed",
                "live_verified": True,
                "verified": True,
            }

        # 离线回执模式（B4 清零）：单次读取字节并严校验全部退出码标记
        receipt_raw = strict_str(witness.get("receipt_file"), f"{context_label}.receipt_file")
        exp_sub = strict_str(witness.get("expected_output_substring"), f"{context_label}.expected_output_substring")
        recorded_code = strict_int(witness.get("recorded_exit_code"), f"{context_label}.recorded_exit_code")
        if recorded_code != expected_code:
            raise GateError("PROBE_EXIT_MISMATCH", f"{context_label}: 离线 probe_exec 的 recorded_exit_code({recorded_code}) 必须等于 {expected_code}")
        _, rec_rel, rec_hash, rec_bytes = _anchor_project_file(receipt_raw, project_root, label=f"{context_label} 探针回执文件")
        raw_text = rec_bytes.decode("utf-8-sig", errors="replace")
        if not raw_text.strip():
            raise GateError("WITNESS_EMPTY_RECEIPT", f"{context_label}: 探针回执文件为空: {receipt_raw}")
        found_codes = _extract_receipt_exit_codes(raw_text)
        if not found_codes:
            raise GateError(
                "PROBE_RECEIPT_MISSING_EXIT_CODE",
                f"{context_label}: 离线探针回执 {receipt_raw} 缺少显式退出码标记（exit_code/return_code/rc），拒绝以普通文本充当回执",
            )
        for rc_found in found_codes:
            if rc_found != expected_code:
                raise GateError(
                    "PROBE_EXIT_MISMATCH",
                    f"{context_label}: 探针回执文件 {receipt_raw} 内记录退出码 {rc_found} 与预期 {expected_code} 冲突",
                )
        if exp_sub not in raw_text:
            raise GateError("PROBE_OUTPUT_MISMATCH", f"{context_label}: 探针回执文件 {receipt_raw} 未包含预期输出片段: {exp_sub}")
        return {
            "type": "probe_exec",
            "command": cmd,
            "execute": False,
            "executed": False,
            "probe_role": probe_role,
            "receipt_file": rec_rel,
            "expected_exit_code": expected_code,
            "expected_output_substring": exp_sub,
            "exit_code": recorded_code,
            "receipt_sha256": rec_hash["artifact_sha256"],
            **rec_hash,
            "target_files": bound_targets,
            "domain_spec": domain_spec_entry,
            "exhaustive_domain_size": exhaustive_size,
            "verification_level": "offline_receipt_anchored",
            "live_verified": False,
            "verified": True,
        }

    raise GateError("INVALID_WITNESS", f"{context_label}: 不支持的见证类型: {wtype!r}（仅允许 source_anchor / lean_theorem / probe_exec）")


def _verify_witness_list(
    raw_list: Any,
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool = False,
    context_label: str = "witnesses",
) -> List[Dict[str, Any]]:
    if not isinstance(raw_list, list) or not raw_list:
        raise GateError(
            "MISSING_OBJECTIVE_WITNESS",
            f"防填表迎合门禁：{context_label} 必须提供非空见证列表（禁止仅传裸布尔值）",
        )
    return [
        _verify_single_witness(
            item,
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label=f"{context_label}[{idx}]",
        )
        for idx, item in enumerate(raw_list, 1)
    ]


def recompute_witness_drift(witnesses: List[Dict[str, Any]], project_root: Path) -> List[Dict[str, Any]]:
    """现场重算单关全部见证引用文件的双哈希与祖先路径安全边界，返回所有漂移条目。"""
    drifts: List[Dict[str, Any]] = []
    root_res = _reject_links(project_root, label="课题根目录").resolve()
    for w in witnesses or []:
        wtype = str(w.get("type") or "")
        if wtype == "source_anchor":
            rel = str(w.get("file") or "")
            ok, reason = hashes_match_disk(w, root_res / rel, project_root=root_res)
            if not ok:
                drifts.append({"type": wtype, "file": rel, "reason": reason})
        elif wtype == "probe_exec":
            if w.get("receipt_file"):
                rel = str(w["receipt_file"])
                ok, reason = hashes_match_disk(w, root_res / rel, project_root=root_res)
                if not ok:
                    drifts.append({"type": wtype, "file": rel, "reason": reason})
            for tf in w.get("target_files") or []:
                rel_tf = str(tf.get("file") or "")
                ok, reason = hashes_match_disk(tf, root_res / rel_tf, force_raw=True, project_root=root_res)
                if not ok:
                    drifts.append({"type": "probe_target", "file": rel_tf, "reason": reason})
            dom = w.get("domain_spec")
            if isinstance(dom, dict) and dom.get("file"):
                rel_dom = str(dom["file"])
                ok, reason = hashes_match_disk(dom, root_res / rel_dom, project_root=root_res)
                if not ok:
                    drifts.append({"type": "probe_domain_spec", "file": rel_dom, "reason": reason})
        elif wtype == "lean_theorem":
            lpath = str(w.get("lean_source_path") or "")
            lsha = str(w.get("lean_source_sha256") or "")
            lrec = w.get("lean_source_hash_record")
            if lpath and (lsha or isinstance(lrec, dict)):
                lp = Path(lpath)
                if isinstance(lrec, dict) and lrec.get("artifact_sha256"):
                    ok, reason = hashes_match_disk(lrec, lp)
                    if not ok:
                        drifts.append({"type": wtype, "file": lpath, "reason": f"lean_source_{reason}"})
                else:
                    try:
                        checked_lp = _reject_links(lp, label="Lean 源码路径")
                        if not checked_lp.is_file() or compute_file_hashes(checked_lp)["normalized_sha256"] != lsha:
                            drifts.append({"type": wtype, "file": lpath, "reason": "lean_source_sha256_mismatch"})
                    except GateError:
                        drifts.append({"type": wtype, "file": lpath, "reason": "lean_source_unsafe_path"})
            for af in w.get("artifact_files") or []:
                rel_af = str(af.get("file") or "")
                ok, reason = hashes_match_disk(af, root_res / rel_af, project_root=root_res)
                if not ok:
                    drifts.append({"type": "lean_artifact_file", "file": rel_af, "reason": reason})
    return drifts


def rerun_live_probe(witness: Dict[str, Any], project_root: Path) -> Dict[str, Any]:
    """仅对本地案卷中已锁定的 execute:true 探针在终态重跑验签（严格字段解码，O-B2 / A13）。"""
    if not isinstance(witness, dict) or witness.get("type") != "probe_exec" or witness.get("execute") is not True:
        raise GateError("INVALID_WITNESS", "仅支持重跑本地已锁定的 execute=true 探针")
    cmd = strict_str(witness.get("command"), "rerun_live_probe.command")
    expected_code = strict_int(witness.get("expected_exit_code", 0), "rerun_live_probe.expected_exit_code")
    exp_sub = strict_str(witness.get("expected_output_substring"), "rerun_live_probe.expected_output_substring", non_empty=False, default="")
    sub_cwd = strict_str(witness.get("cwd"), "rerun_live_probe.cwd", non_empty=False, default="")
    run_dir = _reject_links(project_root, label="课题根目录").resolve()
    if sub_cwd:
        run_dir = _reject_links(run_dir / sub_cwd, label="探针工作目录").resolve()
        if not run_dir.is_relative_to(project_root.resolve()) or not run_dir.is_dir():
            raise GateError("WITNESS_OUT_OF_SCOPE", f"探针工作目录非法或越界: {sub_cwd}")
    env = {**os.environ, **_NONINTERACTIVE_ENV}
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(run_dir),
            env=env,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError("PROBE_EXEC_FAILED", f"终态重跑探针失败: {exc}") from exc
    if proc.returncode != expected_code:
        raise GateError("PROBE_EXIT_MISMATCH", f"终态重跑探针退出码 {proc.returncode} 与预期 {expected_code} 不符")
    if exp_sub and exp_sub not in proc.stdout:
        raise GateError("PROBE_OUTPUT_MISMATCH", f"终态重跑探针未输出预期片段: {exp_sub}")
    return {
        "command": cmd,
        "exit_code": proc.returncode,
        "stdout_sha256": hashlib.sha256(proc.stdout.replace("\r\n", "\n").encode("utf-8")).hexdigest(),
    }
