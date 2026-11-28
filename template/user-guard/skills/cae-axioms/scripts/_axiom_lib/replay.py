# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_axiom_lib.replay - 纯数据净室隔离复演协议（export-packet / verify-replay），100% 复用 cae_harness --clean-room。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .case import (
    _case_profile,
    _collect_all_stage_artifact_hashes,
    _compute_stage_digest,
    _reconstruct_expected_next_stage,
    _resolve_case_project_root,
    _verify_case_schema_version,
    _verify_conflict_history_chain,
    reconcile_case_freshness,
)
from .contract import (
    ALL_STAGES,
    EXIT_KINDS,
    OBJECTIVE_ROLES,
    PROFILE_STAGES,
    REPLAY_STAGE_VERDICTS,
    REVIEW_EXIT_FIELD_VISIBILITY,
    SCHEMA_VERSION,
    STAGE_FIELD_SPECS,
    SUFFICIENT_CERTIFICATE_KINDS,
    VERIFIED_STATES,
    GateError,
    _atomic_write_text,
    _is_link_or_reparse,
    _now_utc_iso,
    _read_json,
    _reject_links,
    canonical_json_sha256,
    case_file_lock,
    compute_file_hashes,
    compute_gate_code_sha256,
    loads_strict_json,
    strict_bool,
    strict_str,
)
from .evidence import rerun_live_probe
from .lean_gate import audit_lean_suite
from .rules import _normalize_objective_role

REPLAY_PROTOCOL_VERSION = 1

_SAFE_LEDGER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")

_TREE_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv",
    "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})

_ARTIFACT_KIND_PRIORITY = {
    "probe_target": 50,
    "domain_spec": 40,
    "lean_artifact": 30,
    "probe_receipt": 25,
    "source_file": 20,
    "extra_requested_context": 10,
}

# B09 防泄漏特征：过滤受跟踪公理案卷或关口载荷 JSON 的 diff 块，防止 Git diff 把已剥离的自评字段送回复演端
_SENSITIVE_DIFF_MARKERS = (
    '"stage_records"',
    '"immutable_conflict_history"',
    '"actual_state_accepted"',
    '"objective_role"',
    '"root_premises_satisfiable"',
    '"decision_basis_explicit"',
    '"closure_assumptions_explicit"',
    '"sufficient_certificate_kind"',
)


def _strip_blind_item(obj: Any, drop_keys: Set[str]) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_blind_item(v, drop_keys) for k, v in obj.items() if k not in drop_keys}
    if isinstance(obj, list):
        return [_strip_blind_item(x, drop_keys) for x in obj]
    return obj


def _build_blind_stage_view(stage: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    """按单源 STAGE_FIELD_SPECS 与 REVIEW_EXIT_FIELD_VISIBILITY 机械剥离全部 strip 字段与原始见证注记（O-B6 / S1 / S2）。"""
    spec = STAGE_FIELD_SPECS.get(stage) or {}
    raw_payload = rec.get("payload") or {}
    carried: Dict[str, Any] = {}
    for k, v in raw_payload.items():
        if spec.get(k) != "carry":
            continue
        # S1 清零：原始 payload.witnesses 不直透盲包，所有见证统一走严格清洗后的 witness_refs 白名单
        if k == "witnesses":
            continue
        if k == "review_exit" and isinstance(v, dict):
            cleaned_exit: Dict[str, Any] = {}
            for rk, rv in v.items():
                if REVIEW_EXIT_FIELD_VISIBILITY.get(rk) != "carry":
                    continue
                if rk.endswith("_witnesses"):
                    continue
                cleaned_exit[rk] = _strip_blind_item(rv, {"note", "verdict", "status"})
            carried[k] = cleaned_exit
        elif k in ("wellformed_guards", "domain_guards") and isinstance(v, list):
            carried[k] = _strip_blind_item(v, {"status", "note", "witnesses"})
        elif k == "modules" and isinstance(v, list):
            carried[k] = _strip_blind_item(v, {"status", "detail", "note", "witnesses"})
        elif k == "declared_constraints" and isinstance(v, list):
            carried[k] = _strip_blind_item(
                v,
                {"objective_role", "standard_claims", "claimed_as_axiom_standard", "note", "witnesses", "norm_equiv_witnesses"},
            )
        elif k == "definitions" and isinstance(v, list):
            carried[k] = _strip_blind_item(v, {"novel_and_noncircular", "existence_uniqueness_verified", "note", "witnesses"})
        elif k == "comparisons" and isinstance(v, list):
            carried[k] = _strip_blind_item(v, {"objects_fixed", "relation_fixed", "constraints_fixed", "note"})
        elif k == "constraint_pairs" and isinstance(v, list):
            carried[k] = _strip_blind_item(v, {"claimed_hierarchy", "explicit_preference_declared", "co_asserted", "note"})
        elif k in ("internal_verification", "external_validation", "verification", "validation"):
            # S2 清零：仅当原值确为非空字典时标记 declared_present=True
            carried[k] = {"declared_present": isinstance(v, dict) and bool(v)}
        elif k.endswith("_witnesses"):
            continue
        else:
            carried[k] = _strip_blind_item(v, {"note", "verdict", "status", "compatible", "checked"})

    witness_refs: List[Dict[str, Any]] = []
    for w in rec.get("witnesses") or []:
        wtype = w.get("type")
        if wtype == "source_anchor":
            witness_refs.append({
                "type": "source_anchor",
                "file": w.get("file"),
                "line_start": w.get("line_start"),
                "line_end": w.get("line_end"),
                "artifact_sha256": w.get("artifact_sha256"),
                "segment_sha256": w.get("segment_sha256"),
            })
        elif wtype == "probe_exec":
            witness_refs.append({
                "type": "probe_exec",
                "command": w.get("command"),
                "probe_role": w.get("probe_role"),
                "expected_exit_code": w.get("expected_exit_code"),
                "exit_code": w.get("exit_code"),
                "live_verified": w.get("live_verified"),
                "receipt_file": w.get("receipt_file"),
                "stdout_sha256": w.get("stdout_sha256"),
                "target_files": [tf.get("file") for tf in (w.get("target_files") or [])],
                "domain_spec": (w.get("domain_spec") or {}).get("file") if isinstance(w.get("domain_spec"), dict) else None,
            })
        elif wtype == "lean_theorem":
            witness_refs.append({
                "type": "lean_theorem",
                "module": w.get("module"),
                "theorem": w.get("theorem"),
                "kernel_full_name": w.get("kernel_full_name"),
                "verification_level": w.get("verification_level"),
                "lean_source_sha256": w.get("lean_source_sha256"),
                "kernel_type_sha256": w.get("kernel_type_sha256"),
                "artifact_files": [af.get("file") for af in (w.get("artifact_files") or [])],
            })

    return {
        "stage": stage,
        "stage_digest": rec.get("stage_digest"),
        "carried_facts": carried,
        "witness_refs": witness_refs,
    }


def _expected_blind_assertions_template(stage: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    """生成盲推比对契约模板（Opus B1 清零）：要求复演方独立盲推关键被剥离字段，验签时与案卷原值机械比对。"""
    meta = rec.get("meta") or {}
    if stage in ("L2_CONSTRAINT_ROLE", "S3_CLOSURE_CONSTRAINT"):
        roles = meta.get("constraint_roles") or {}
        if isinstance(roles, dict) and roles:
            return {
                "objective_roles": {cname: "<独立判定的七分角色之一>" for cname in sorted(roles.keys())},
            }
    if stage in ("L3_EMPIRICAL_EXIT", "S4_LOCAL_SPECTRAL_VV"):
        tpl: Dict[str, Any] = {
            "target_exogenous": "<true | false>",
            "exit_kind": "<nonperceivable_constraint | sufficient_certificate | explicit_limit>",
        }
        if meta.get("exit_kind") == "sufficient_certificate":
            tpl["sufficient_certificate_kind"] = "<exhaustive_domain | artifact_bound_lean | finite_probe_only>"
        return tpl
    return {}


def _verify_blind_assertions(
    covered_stages: List[str],
    records: Dict[str, Any],
    parsed_sv: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """机械比对复演方独立给出的 blind_assertions 与案卷原始自评结论（Opus B1 清零）。"""
    mismatches: List[Dict[str, Any]] = []
    for st in covered_stages:
        rec = records.get(st) or {}
        meta = rec.get("meta") or {}
        ba = parsed_sv[st].get("blind_assertions")
        if st in ("L2_CONSTRAINT_ROLE", "S3_CLOSURE_CONSTRAINT"):
            orig_roles = meta.get("constraint_roles") or {}
            if isinstance(orig_roles, dict) and orig_roles:
                if not isinstance(ba, dict) or not isinstance(ba.get("objective_roles"), dict):
                    mismatches.append({"stage": st, "field": "objective_roles", "reason": "missing_blind_assertions"})
                    continue
                rep_roles = ba["objective_roles"]
                for cname, orig_role in sorted(orig_roles.items()):
                    raw_rep = rep_roles.get(cname)
                    norm_rep = _normalize_objective_role(str(raw_rep or "").strip()) if isinstance(raw_rep, str) else None
                    if norm_rep != orig_role:
                        mismatches.append({
                            "stage": st,
                            "field": f"objective_roles.{cname}",
                            "author_value": orig_role,
                            "replayer_value": norm_rep or raw_rep,
                        })
        elif st in ("L3_EMPIRICAL_EXIT", "S4_LOCAL_SPECTRAL_VV"):
            if not isinstance(ba, dict):
                mismatches.append({"stage": st, "field": "review_exit", "reason": "missing_blind_assertions"})
                continue
            rep_exog = ba.get("target_exogenous")
            orig_exog = meta.get("target_exogenous")
            if type(rep_exog) is not bool or rep_exog != orig_exog:
                mismatches.append({
                    "stage": st,
                    "field": "target_exogenous",
                    "author_value": orig_exog,
                    "replayer_value": rep_exog,
                })
            rep_exit = ba.get("exit_kind")
            orig_exit = meta.get("exit_kind")
            if not isinstance(rep_exit, str) or rep_exit not in EXIT_KINDS or rep_exit != orig_exit:
                mismatches.append({
                    "stage": st,
                    "field": "exit_kind",
                    "author_value": orig_exit,
                    "replayer_value": rep_exit,
                })
            if orig_exit == "sufficient_certificate":
                rep_kind = ba.get("sufficient_certificate_kind")
                orig_kind = meta.get("sufficient_certificate_kind")
                if not isinstance(rep_kind, str) or rep_kind not in SUFFICIENT_CERTIFICATE_KINDS or rep_kind != orig_kind:
                    mismatches.append({
                        "stage": st,
                        "field": "sufficient_certificate_kind",
                        "author_value": orig_kind,
                        "replayer_value": rep_kind,
                    })
    return mismatches


def _collect_project_file_tree_hashes(project_root: Path) -> List[Dict[str, Any]]:
    """机械派生受跟踪文件树路径->哈希清单（使用 git ls-files -z 防非 ASCII 路径跳过，O-B6）。"""
    root_res = _reject_links(project_root, label="课题根目录").resolve()
    rel_paths: List[str] = []
    used_git = False
    try:
        out_bytes = subprocess.check_output(
            ["git", "ls-files", "-z"],
            cwd=str(root_res),
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        rel_paths = [
            chunk.decode("utf-8", errors="surrogateescape")
            for chunk in out_bytes.split(b"\x00")
            if chunk
        ]
        used_git = True
    except (OSError, subprocess.SubprocessError):
        pass

    if not used_git:
        for dirpath, dirnames, filenames in os.walk(root_res):
            dirnames[:] = sorted(d for d in dirnames if d not in _TREE_SKIP_DIRS)
            dp = Path(dirpath)
            for fn in sorted(filenames):
                fp = dp / fn
                if _is_link_or_reparse(fp):
                    continue
                rel_paths.append(fp.relative_to(root_res).as_posix())

    entries: List[Dict[str, Any]] = []
    for rel in sorted(set(rel_paths)):
        fp = root_res / rel
        if not fp.is_file() or _is_link_or_reparse(fp):
            continue
        h = compute_file_hashes(fp)
        entries.append({
            "rel_path": rel,
            "raw_sha256": h["raw_sha256"],
            "normalized_sha256": h["normalized_sha256"],
            "hash_mode": h["hash_mode"],
            "size_bytes": h["size_bytes"],
        })
    return entries


def _capture_mechanical_git_diff(
    project_root: Path,
    base_head: Optional[str],
    *,
    excluded_rel_paths: Optional[Set[str]] = None,
) -> Optional[str]:
    """机械捕获基线 diff，自动剥离案卷 JSON 与含公理自评载荷字段的 JSON diff 块（B09 防泄漏）。"""
    if not base_head:
        return None
    excl = excluded_rel_paths or set()
    try:
        raw_diff = subprocess.check_output(
            ["git", "diff", base_head],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        status_txt = subprocess.check_output(
            ["git", "status", "--short"],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    # 逐文件块过滤掉案卷本身与含自评载荷特征的 JSON 文件块
    filtered_blocks: List[str] = []
    skipped_files: List[str] = []
    if raw_diff.strip():
        blocks = re.split(r"(?m)(?=^diff --git )", raw_diff)
        for blk in blocks:
            if not blk.strip():
                continue
            first_line = blk.splitlines()[0]
            m_hdr = re.match(r"^diff --git a/(.+?) b/(.+)$", first_line)
            b_path = m_hdr.group(2).strip() if m_hdr else ""
            is_excluded_path = b_path in excl
            is_sensitive_json = b_path.lower().endswith(".json") and any(mk in blk for mk in _SENSITIVE_DIFF_MARKERS)
            if is_excluded_path or is_sensitive_json:
                skipped_files.append(b_path or "<unknown>")
                continue
            filtered_blocks.append(blk)

    header = f"=== GIT STATUS ===\n{status_txt}"
    if skipped_files:
        header += f"=== REDACTED_SELF_EVAL_DIFF_PATHS ===\n" + "\n".join(sorted(set(skipped_files))) + "\n"
    return f"{header}=== GIT DIFF FROM {base_head} ===\n{''.join(filtered_blocks)}"


def _write_blob(blobs_dir: Path, raw_bytes: bytes) -> str:
    sha = hashlib.sha256(raw_bytes).hexdigest()
    target = blobs_dir / sha
    if not target.exists():
        target.write_bytes(raw_bytes)
    return sha


def _extract_replay_verdict_object(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """B01 清零：严格仅从 cae_harness 回执 result.content 解析逐关三值判决对象，禁止顶层 replay_verdict 偷换覆盖。"""
    if "replay_verdict" in receipt:
        raise GateError("REPLAY_VERDICT_FORGED_TOPLEVEL", "拒绝顶层 replay_verdict 旁路字段，复演判决必须源自净室回执 result.content")
    res = receipt.get("result")
    if not isinstance(res, dict):
        raise GateError("REPLAY_VERDICT_UNPARSEABLE", "净室回执缺少 result 对象，无法解析复演判决")
    if "replay_verdict" in res:
        raise GateError("REPLAY_VERDICT_FORGED_TOPLEVEL", "拒绝 result.replay_verdict 旁路字段，复演判决必须位于 result.content")
    content = res.get("content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        try:
            parsed = loads_strict_json(text, label="净室复演判决 JSON")
            if isinstance(parsed, dict):
                return parsed
        except GateError as exc:
            if exc.code in ("SCHEMA_DUPLICATE_KEY", "SCHEMA_NONSTANDARD_NUMBER"):
                raise
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = loads_strict_json(text[start : end + 1], label="净室复演判决 JSON")
                if isinstance(parsed, dict):
                    return parsed
            except GateError as exc:
                if exc.code in ("SCHEMA_DUPLICATE_KEY", "SCHEMA_NONSTANDARD_NUMBER"):
                    raise
    raise GateError("REPLAY_VERDICT_UNPARSEABLE", "无法从净室回执 result.content 中解析结构化逐关三值判决对象（stage_verdicts）")


def _validate_safe_ledger_id(val: str, label: str) -> str:
    if not _SAFE_LEDGER_ID_RE.fullmatch(val):
        raise GateError("REPLAY_HARNESS_LEDGER_MISMATCH", f"非法 {label} 标识（防路径穿越）: {val!r}")
    return val


def _ticket_matches_packet(tdoc: Dict[str, Any], packet_dir: Path, packet_sha: str) -> bool:
    """校验 APPROVED 票据内至少有一条流绑定当前 packet_dir、clean_room 隔离与本次包摘要或 manifest.json。"""
    if tdoc.get("status") != "APPROVED":
        return False
    pkt_res = packet_dir.resolve()
    for stream in tdoc.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        iso = str(stream.get("isolation") or "").strip()
        is_cr = (iso == "clean_room") or (stream.get("clean_room") is True)
        if not is_cr:
            continue
        raw_tgt = str(stream.get("target") or "").strip()
        if not raw_tgt:
            continue
        try:
            if Path(raw_tgt).resolve() != pkt_res:
                continue
        except OSError:
            continue
        subtask = str(stream.get("subtask") or "")
        files_list = [str(f) for f in (stream.get("files") or [])]
        if packet_sha in subtask or "manifest.json" in files_list:
            return True
    return False


def _verify_harness_ledger_crosscheck(
    receipt: Dict[str, Any],
    harness_plan_dir: Path,
    *,
    packet_dir: Path,
    packet_sha: str,
    explicit_ticket_id: str = "",
) -> Dict[str, Any]:
    """按只读文件契约强制交叉核验 cae_harness 票据（绑定 packet_dir + clean_room + packet_sha）与对账账本（B01 / Opus B2 / S16 清零）。"""
    plan_dir = _reject_links(harness_plan_dir, label="harness 状态目录").resolve()
    if not plan_dir.is_dir():
        raise GateError("REPLAY_HARNESS_LEDGER_MISMATCH", f"harness 状态目录不存在: {plan_dir}")
    checked: Dict[str, Any] = {"plan_dir": str(plan_dir), "ticket_verified": False, "reconcile_verified": False}

    # 1. 强绑定核验 APPROVED 票据（显式 --ticket-id / 回执 ticket_id / 在 plan_dir 内精确反查绑定该包与净室的 APPROVED 票据）
    ticket_id = str(explicit_ticket_id or receipt.get("ticket_id") or (receipt.get("invocation") or {}).get("ticket_id") or "").strip()
    if ticket_id:
        _validate_safe_ledger_id(ticket_id, "ticket_id")
        tpath = _reject_links(plan_dir / f"{ticket_id}.json", label="harness 票据")
        if not tpath.is_file():
            raise GateError("REPLAY_HARNESS_LEDGER_MISMATCH", f"harness 账本缺少对应票据文件: {tpath.name}")
        tdoc = _read_json(tpath, label="harness 票据")
        if not _ticket_matches_packet(tdoc, packet_dir, packet_sha):
            raise GateError(
                "REPLAY_HARNESS_LEDGER_MISMATCH",
                f"harness 票据 {ticket_id} 非 APPROVED 或未绑定当前复演包目录、clean_room 隔离与 packet_sha256",
            )
        checked["ticket_id"] = ticket_id
        checked["ticket_verified"] = True
    else:
        matched_tid: Optional[str] = None
        for cand in sorted(plan_dir.glob("*.json")):
            if _is_link_or_reparse(cand):
                continue
            try:
                tdoc = _read_json(cand, label="候选票据")
            except GateError:
                continue
            if _ticket_matches_packet(tdoc, packet_dir, packet_sha):
                matched_tid = str(tdoc.get("ticket_id") or cand.stem)
                break
        if not matched_tid:
            raise GateError(
                "REPLAY_HARNESS_LEDGER_MISMATCH",
                "未在 harness 状态目录找到同时满足 APPROVED、clean_room 且绑定当前 packet_dir 与 packet_sha256 的派工票据",
            )
        checked["ticket_id"] = matched_tid
        checked["ticket_verified"] = True

    # 2. 强核验机械对账文件（禁止仅凭单边存在过关，且必须检验 run_id 格式与内容匹配）
    rec_info = receipt.get("reconciliation")
    if not isinstance(rec_info, dict) or not str(rec_info.get("run_id") or "").strip():
        raise GateError("REPLAY_HARNESS_LEDGER_MISMATCH", "净室回执缺少 reconciliation.run_id，无法完成机械对账交叉验签")
    run_id = _validate_safe_ledger_id(str(rec_info["run_id"]).strip(), "run_id")
    rec_md = _reject_links(plan_dir / "reconcile" / f"{run_id}_reconcile.md", label="harness 机械对账表")
    if not rec_md.is_file():
        raise GateError("REPLAY_HARNESS_LEDGER_MISMATCH", f"harness 对账账本缺少 {rec_md.name}")
    rec_text = rec_md.read_text(encoding="utf-8", errors="replace")
    if run_id not in rec_text and "机械对账" not in rec_text and "reconcile" not in rec_text.lower():
        raise GateError("REPLAY_HARNESS_LEDGER_MISMATCH", f"对账文件 {rec_md.name} 内容缺少有效对账标记，疑似空壳文件")
    # 若回执带 target，校验其与当前复演包目录一致
    rec_target = str(receipt.get("target") or (receipt.get("invocation") or {}).get("target_dir") or "").strip()
    if rec_target:
        try:
            if Path(rec_target).resolve() != packet_dir.resolve():
                raise GateError("REPLAY_HARNESS_LEDGER_MISMATCH", f"净室回执 target({rec_target}) 与当前复演包目录({packet_dir})不匹配")
        except OSError as exc:
            raise GateError("REPLAY_HARNESS_LEDGER_MISMATCH", f"无法解析净室回执 target: {exc}") from exc
    checked["run_id"] = run_id
    checked["reconcile_verified"] = True
    return checked


def _verify_replay_attempts_chain(case_doc: Dict[str, Any]) -> None:
    """校验只追加 replay_attempts 账本哈希链防删除或篡改（S3 清零）。"""
    attempts = case_doc.get("replay_attempts")
    if attempts is None:
        return
    if not isinstance(attempts, list):
        raise GateError("REPLAY_ATTEMPTS_TAMPERED", "replay_attempts 必须为列表")
    expected_prev = "GENESIS"
    for idx, entry in enumerate(attempts, 1):
        if not isinstance(entry, dict):
            raise GateError("REPLAY_ATTEMPTS_TAMPERED", f"replay_attempts 第 {idx} 条非对象")
        if entry.get("attempt_seq") != idx:
            raise GateError("REPLAY_ATTEMPTS_TAMPERED", f"replay_attempts 序号断裂：期望 {idx}，实际 {entry.get('attempt_seq')!r}")
        if "entry_hash" in entry:
            if entry.get("prev_hash") != expected_prev:
                raise GateError("REPLAY_ATTEMPTS_TAMPERED", f"replay_attempts 第 {idx} 条 prev_hash 断裂")
            body = {k: v for k, v in entry.items() if k != "entry_hash"}
            if canonical_json_sha256(body)[:24] != entry["entry_hash"]:
                raise GateError("REPLAY_ATTEMPTS_TAMPERED", f"replay_attempts 第 {idx} 条哈希校验失败")
            expected_prev = str(entry["entry_hash"])


def _safe_clear_existing_packet_dir(out_dir: Path, *, project_root: Path, case_path: Path) -> None:
    """S4 清零：--force 清理旧复演包前多重校验目录纯净性，杜绝误删自带 manifest.json 的真实项目。"""
    out_res = out_dir.resolve()
    proj_res = project_root.resolve()
    case_res = case_path.resolve()
    if out_res == proj_res or proj_res.is_relative_to(out_res) or case_res.is_relative_to(out_res):
        raise GateError("PACKET_DIR_PROTECTED", f"拒绝 --force 清空包含项目根目录或审查单的目录: {out_dir}")
    children = {p.name for p in out_dir.iterdir()}
    if not children.issubset({"manifest.json", "blobs"}) or "manifest.json" not in children:
        raise GateError("PACKET_DIR_PROTECTED", f"拒绝 --force 清空非纯净复演包目录（发现额外文件 {sorted(children - {'manifest.json', 'blobs'})}）: {out_dir}")
    old_manifest = _read_json(out_dir / "manifest.json", label="既有复演包 manifest.json")
    if old_manifest.get("packet_version") != REPLAY_PROTOCOL_VERSION or not old_manifest.get("packet_sha256"):
        raise GateError("PACKET_DIR_PROTECTED", f"既有 manifest.json 非 cae-axioms 复演包，拒绝清空: {out_dir}")
    body_no_sha = {k: v for k, v in old_manifest.items() if k != "packet_sha256"}
    if canonical_json_sha256(body_no_sha) != old_manifest.get("packet_sha256"):
        raise GateError("PACKET_DIR_PROTECTED", f"既有 manifest.json 校验和不匹配，拒绝 --force 清空: {out_dir}")
    shutil.rmtree(out_dir)


def cmd_export_packet(args: argparse.Namespace) -> int:
    """导出纯数据盲审复演包（manifest.json + blobs/<sha256>），机械派生全文件 blob 与文件树清单。"""
    case_path = _reject_links(Path(args.case), label="审查单路径")
    out_dir = _reject_links(Path(args.output_dir), label="复演包输出目录")
    with case_file_lock(case_path):
        case_doc = _read_json(case_path, label="审查单")
        _verify_case_schema_version(case_doc)
        _verify_conflict_history_chain(case_doc)
        _verify_replay_attempts_chain(case_doc)
        _reconstruct_expected_next_stage(case_doc)
        profile = _case_profile(case_doc)
        stages = PROFILE_STAGES[profile]
        project_root = _resolve_case_project_root(case_doc, getattr(args, "project_root", ""))
        fresh_rep = reconcile_case_freshness(case_doc, project_root, case_path=case_path, persist_on_invalidation=True)
        if not fresh_rep["fresh"]:
            raise GateError(
                str(fresh_rep["error_code"]),
                f"导包前现场重算发现漂移（起始关口: {fresh_rep['invalidated_from_stage']}），请先修复并重新推进",
                {"invalidated_from_stage": fresh_rep["invalidated_from_stage"], "drifts": fresh_rep["drifts"]},
            )

        records = case_doc.get("stage_records") or {}
        target_stage = str(getattr(args, "stage", "") or "").strip()
        if target_stage:
            if target_stage not in stages:
                raise GateError("STAGE_PROFILE_MISMATCH", f"--stage {target_stage} 不属于当前档位 {profile}")
            rec_t = records.get(target_stage)
            if not isinstance(rec_t, dict) or rec_t.get("verdict") != "pass" or rec_t.get("freshness_status") == "STALE_INVALIDATED":
                raise GateError("STAGE_NOT_PASSED", f"关口 {target_stage} 尚未有效通过（非 pass 或已失活），无法单独导包")
            covered_stages = [target_stage]
            mode = "single_stage"
        else:
            covered_stages = [
                st for st in stages
                if isinstance(records.get(st), dict)
                and records[st].get("verdict") == "pass"
                and records[st].get("freshness_status") != "STALE_INVALIDATED"
            ]
            if not covered_stages:
                raise GateError("NO_PASSED_STAGES", "审查单尚无有效已通过关口，无法导出整单复演包")
            mode = "full_case"

        if out_dir.exists():
            if not out_dir.is_dir():
                raise GateError("INVALID_TARGET_PATH", f"--output-dir 不是目录: {out_dir}")
            if any(out_dir.iterdir()):
                if not getattr(args, "force", False):
                    raise GateError("PACKET_DIR_NOT_EMPTY", f"复演包输出目录非空（如需覆写旧复演包请加 --force）: {out_dir}")
                _safe_clear_existing_packet_dir(out_dir, project_root=project_root, case_path=case_path)

        blobs_dir = out_dir / "blobs"
        blobs_dir.mkdir(parents=True, exist_ok=True)

        # S12 清零：按优先级合并同一文件的 kind，确保 probe_target 始终保留 raw 哈希模式
        rel_files_to_pack: Dict[str, str] = {}

        def _add_pack_file(rpath: str, kind: str) -> None:
            prev = rel_files_to_pack.get(rpath)
            if prev is None or _ARTIFACT_KIND_PRIORITY.get(kind, 0) > _ARTIFACT_KIND_PRIORITY.get(prev, 0):
                rel_files_to_pack[rpath] = kind

        for st in covered_stages:
            rec = records[st]
            for w in rec.get("witnesses") or []:
                wtype = w.get("type")
                if wtype == "source_anchor" and w.get("file"):
                    _add_pack_file(str(w["file"]), "source_file")
                elif wtype == "probe_exec":
                    if w.get("receipt_file"):
                        _add_pack_file(str(w["receipt_file"]), "probe_receipt")
                    for tf in w.get("target_files") or []:
                        if tf.get("file"):
                            _add_pack_file(str(tf["file"]), "probe_target")
                    dom = w.get("domain_spec")
                    if isinstance(dom, dict) and dom.get("file"):
                        _add_pack_file(str(dom["file"]), "domain_spec")
                elif wtype == "lean_theorem":
                    for af in w.get("artifact_files") or []:
                        if af.get("file"):
                            _add_pack_file(str(af["file"]), "lean_artifact")

        root_res = project_root.resolve()
        extra_included_paths: List[str] = []
        for extra_rel in (getattr(args, "include_path", None) or []):
            cand = _reject_links(root_res / extra_rel, label="include-path").resolve()
            if not cand.is_relative_to(root_res) or not cand.is_file():
                raise GateError("WITNESS_FILE_MISSING", f"--include-path 文件不存在或越出项目根目录: {extra_rel}")
            rel_posix = cand.relative_to(root_res).as_posix()
            _add_pack_file(rel_posix, "extra_requested_context")
            extra_included_paths.append(rel_posix)

        artifacts: List[Dict[str, Any]] = []
        for idx, (rel_path, kind) in enumerate(sorted(rel_files_to_pack.items()), 1):
            fp = _reject_links(root_res / rel_path, label="包内制品").resolve()
            if not fp.is_relative_to(root_res) or not fp.is_file():
                raise GateError("WITNESS_FILE_MISSING", f"导包时找不到锚定文件: {rel_path}")
            raw_bytes = fp.read_bytes()
            blob_sha = _write_blob(blobs_dir, raw_bytes)
            h = compute_file_hashes(fp, force_raw=(kind == "probe_target"))
            artifacts.append({
                "id": f"art_{idx:03d}",
                "rel_path": rel_path,
                "kind": kind,
                "blob_sha256": blob_sha,
                **h,
            })

        tree_entries = _collect_project_file_tree_hashes(project_root)
        tree_bytes = (json.dumps(tree_entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        tree_blob_sha = _write_blob(blobs_dir, tree_bytes)
        artifacts.append({
            "id": "meta_file_tree",
            "rel_path": "__meta__/file_tree_hashes.json",
            "kind": "file_tree_manifest",
            "blob_sha256": tree_blob_sha,
            "raw_sha256": tree_blob_sha,
            "normalized_sha256": tree_blob_sha,
            "hash_mode": "raw",
            "artifact_sha256": tree_blob_sha,
            "size_bytes": len(tree_bytes),
        })

        excl_diff: Set[str] = set()
        if case_path.resolve().is_relative_to(root_res):
            excl_diff.add(case_path.resolve().relative_to(root_res).as_posix())
        base_head = (case_doc.get("init_baseline") or {}).get("head_commit")
        diff_text = _capture_mechanical_git_diff(project_root, base_head, excluded_rel_paths=excl_diff)
        diff_blob_sha: Optional[str] = None
        if diff_text is not None:
            diff_bytes = diff_text.encode("utf-8")
            diff_blob_sha = _write_blob(blobs_dir, diff_bytes)
            artifacts.append({
                "id": "meta_git_diff",
                "rel_path": "__meta__/baseline_git_diff.txt",
                "kind": "baseline_git_diff",
                "blob_sha256": diff_blob_sha,
                "raw_sha256": diff_blob_sha,
                "normalized_sha256": diff_blob_sha,
                "hash_mode": "raw",
                "artifact_sha256": diff_blob_sha,
                "size_bytes": len(diff_bytes),
            })

        blind_stages = [_build_blind_stage_view(st, records[st]) for st in covered_stages]
        last_st = covered_stages[-1]
        case_head_digest = str(records[last_st].get("stage_digest") or "")
        artifacts_union_sha = canonical_json_sha256(_collect_all_stage_artifact_hashes(case_doc))
        code_shas = compute_gate_code_sha256()

        manifest_body: Dict[str, Any] = {
            "packet_version": REPLAY_PROTOCOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "profile": profile,
            "subject": case_doc.get("subject"),
            "mode": mode,
            "covered_stages": covered_stages,
            "extra_included_paths": sorted(set(extra_included_paths)),
            "root_claim": case_doc.get("root_claim"),
            "root_claim_sha256": case_doc.get("root_claim_sha256"),
            "effective_claim": case_doc.get("effective_claim"),
            "effective_claim_chain": case_doc.get("effective_claim_chain"),
            "effective_claim_hash": case_doc.get("effective_claim_hash"),
            "init_baseline": case_doc.get("init_baseline") or {},
            "gate_sha256": code_shas["gate_sha256"],
            "lean_driver_sha256": code_shas["lean_driver_sha256"],
            "case_head_digest": case_head_digest,
            "artifacts_union_sha256": artifacts_union_sha,
            "file_tree_blob_sha256": tree_blob_sha,
            "git_diff_blob_sha256": diff_blob_sha,
            "artifacts": artifacts,
            "blind_stages": blind_stages,
            "replay_instructions": (
                "请仅基于本包内 manifest.json 与 blobs/<sha256> 独立逐关复演有效命题 effective_claim"
                "（若发生过降格，原命题 root_claim 已知不可满足，严禁将有效命题冒充原命题）。"
                "对 covered_stages 中每一关输出三值判决 consistent / inconsistent / insufficient_context"
                "（缺上下文时在 requested_paths 中列出缺失文件路径，严禁脑补），并在 blind_assertions 中独立给出关键被剥离判断供机械比对。"
            ),
            "expected_replay_schema": {
                "packet_sha256": "<manifest.packet_sha256>",
                "stage_verdicts": {
                    st: {
                        "verdict": "consistent | inconsistent | insufficient_context",
                        "requested_paths": [],
                        "rationale": "<逐关客观依据（非空）>",
                        **(
                            {"blind_assertions": _expected_blind_assertions_template(st, records[st])}
                            if _expected_blind_assertions_template(st, records[st])
                            else {}
                        ),
                    }
                    for st in covered_stages
                },
            },
        }
        packet_sha = canonical_json_sha256(manifest_body)
        manifest = {**manifest_body, "packet_sha256": packet_sha}
        manifest_path = _atomic_write_text(out_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

        case_doc["last_exported_packet"] = {
            "exported_at": _now_utc_iso(),
            "packet_sha256": packet_sha,
            "mode": mode,
            "covered_stages": covered_stages,
            "extra_included_paths": sorted(set(extra_included_paths)),
            "artifacts_union_sha256": artifacts_union_sha,
            "file_tree_blob_sha256": tree_blob_sha,
            "case_head_digest": case_head_digest,
            "gate_sha256": code_shas["gate_sha256"],
            "lean_driver_sha256": code_shas["lean_driver_sha256"],
        }
        case_doc["updated_at"] = _now_utc_iso()
        _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps({
        "ok": True,
        "packet_version": REPLAY_PROTOCOL_VERSION,
        "mode": mode,
        "packet_dir": str(out_dir),
        "manifest_file": str(manifest_path),
        "packet_sha256": packet_sha,
        "covered_stages": covered_stages,
        "artifact_count": len(artifacts),
        "file_tree_count": len(tree_entries),
        "clean_room_note": "请经 cae_harness.py plan 审批后，对独立包目录执行 cae_harness.py task --clean-room --target <packet_dir> --kind review，再将回执交 verify-replay 验签。",
    }, ensure_ascii=False, indent=2))
    return 0


def _append_replay_attempt(case_doc: Dict[str, Any], entry_body: Dict[str, Any]) -> Dict[str, Any]:
    attempts = case_doc.setdefault("replay_attempts", [])
    prev_hash = str(attempts[-1].get("entry_hash") or "GENESIS") if attempts else "GENESIS"
    entry = {
        "attempt_seq": len(attempts) + 1,
        "prev_hash": prev_hash,
        **entry_body,
    }
    entry["entry_hash"] = canonical_json_sha256(entry)[:24]
    attempts.append(entry)
    return entry


def _recompute_case_stage_chain(case_doc: Dict[str, Any], stages: Tuple[str, ...]) -> None:
    """在逐条核验升级 Lean 见证字段后重算全链 stage_digest，保证摘要链 100% 一致（B03 清零）。"""
    profile = _case_profile(case_doc)
    eff_claim_hash = str(case_doc.get("effective_claim_hash") or "")
    records = case_doc.get("stage_records") or {}
    prev_digest = "GENESIS"
    for st in stages:
        rec = records.get(st)
        if not isinstance(rec, dict) or rec.get("freshness_status") == "STALE_INVALIDATED":
            break
        rec["prev_stage_digest"] = prev_digest
        rec["stage_digest"] = _compute_stage_digest(
            profile=profile,
            stage=st,
            effective_claim_hash=eff_claim_hash,
            prev_stage_digest=prev_digest,
            payload=rec.get("payload") if isinstance(rec.get("payload"), dict) else {},
            witnesses=rec.get("witnesses") if isinstance(rec.get("witnesses"), list) else [],
            verdict=str(rec.get("verdict") or "pass"),
            meta=rec.get("meta") if isinstance(rec.get("meta"), dict) else {},
        )
        prev_digest = rec["stage_digest"]


def cmd_verify_replay(args: argparse.Namespace) -> int:
    """验签净室复演回执（强制账本强绑定、盲推比对、防刷榜、逐条内核/实跑门槛与双轴终态升格）。"""
    case_path = _reject_links(Path(args.case), label="审查单路径")
    packet_dir = _reject_links(Path(args.packet), label="复演包目录").resolve()
    receipt_path = _reject_links(Path(args.receipt), label="净室复演回执").resolve()

    with case_file_lock(case_path):
        case_doc = _read_json(case_path, label="审查单")
        _verify_case_schema_version(case_doc)
        _verify_conflict_history_chain(case_doc)
        _verify_replay_attempts_chain(case_doc)
        _reconstruct_expected_next_stage(case_doc)
        profile = _case_profile(case_doc)
        stages = PROFILE_STAGES[profile]
        project_root = _resolve_case_project_root(case_doc, getattr(args, "project_root", ""))

        fresh_rep = reconcile_case_freshness(case_doc, project_root, case_path=case_path, persist_on_invalidation=True)
        if not fresh_rep["fresh"]:
            raise GateError(
                str(fresh_rep["error_code"]),
                f"验签前现场重算发现锚定文件或前提漂移（起始关口: {fresh_rep['invalidated_from_stage']}）",
                {"invalidated_from_stage": fresh_rep["invalidated_from_stage"], "drifts": fresh_rep["drifts"]},
            )

        # 1. 核验包完整性（manifest.json 与全部 blobs/<sha256> 逐字节验签）
        manifest = _read_json(packet_dir / "manifest.json", label="复演包 manifest.json")
        if manifest.get("packet_version") != REPLAY_PROTOCOL_VERSION:
            raise GateError("REPLAY_PACKET_INVALID", f"不支持的 packet_version: {manifest.get('packet_version')!r}")
        claimed_pkt_sha = str(manifest.get("packet_sha256") or "")
        body_no_sha = {k: v for k, v in manifest.items() if k != "packet_sha256"}
        recomputed_pkt_sha = canonical_json_sha256(body_no_sha)
        if not claimed_pkt_sha or claimed_pkt_sha != recomputed_pkt_sha:
            raise GateError("REPLAY_PACKET_HASH_MISMATCH", "复演包 manifest.json 自身 packet_sha256 校验失败，包疑似被篡改")

        blobs_dir = packet_dir / "blobs"
        for art in manifest.get("artifacts") or []:
            bsha = str(art.get("blob_sha256") or "")
            bpath = _reject_links(blobs_dir / bsha, label="包内 blob")
            if not bpath.is_file():
                raise GateError("REPLAY_PACKET_INVALID", f"复演包缺少声明的 blob: {bsha}")
            actual_bsha = hashlib.sha256(bpath.read_bytes()).hexdigest()
            if actual_bsha != bsha:
                raise GateError("REPLAY_PACKET_HASH_MISMATCH", f"复演包 blob 字节摘要不匹配: {art.get('rel_path')}")

        # 2. B02 清零：核验包与当前审查单、last_exported_packet、当前代码版本及受跟踪文件树完全一致
        cur_artifacts_union_sha = canonical_json_sha256(_collect_all_stage_artifact_hashes(case_doc))
        cur_code_shas = compute_gate_code_sha256()
        covered_stages = list(manifest.get("covered_stages") or [])
        if not covered_stages or any(st not in stages for st in covered_stages):
            raise GateError("REPLAY_PACKET_INVALID", f"复演包 covered_stages 非法: {covered_stages}")
        records = case_doc.get("stage_records") or {}
        last_st = covered_stages[-1]
        cur_head_digest = str((records.get(last_st) or {}).get("stage_digest") or "")
        last_exported = case_doc.get("last_exported_packet")
        if not isinstance(last_exported, dict) or last_exported.get("packet_sha256") != claimed_pkt_sha:
            raise GateError(
                "REPLAY_PACKET_HASH_MISMATCH",
                "复演包 packet_sha256 与审查单 last_exported_packet 不一致（拦截跨单重放、旧包或未登记包）",
            )
        if (
            manifest.get("profile") != profile
            or manifest.get("effective_claim_hash") != case_doc.get("effective_claim_hash")
            or manifest.get("artifacts_union_sha256") != cur_artifacts_union_sha
            or manifest.get("case_head_digest") != cur_head_digest
            or manifest.get("gate_sha256") != cur_code_shas["gate_sha256"]
            or manifest.get("lean_driver_sha256") != cur_code_shas["lean_driver_sha256"]
        ):
            raise GateError(
                "REPLAY_PACKET_HASH_MISMATCH",
                "复演包与当前审查单的有效命题、锚定哈希、关口摘要或状态机/驱动代码版本不匹配",
            )
        # 校验受跟踪文件树自导包后未发生未锚定依赖篡改（B02）
        cur_tree_entries = _collect_project_file_tree_hashes(project_root)
        cur_tree_sha = hashlib.sha256(
            (json.dumps(cur_tree_entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        ).hexdigest()
        if manifest.get("file_tree_blob_sha256") != cur_tree_sha:
            raise GateError(
                "REPLAY_FILE_TREE_DRIFT",
                "检测到导包后项目受跟踪文件树发生变动，旧复演包已失效，请重新导包复演",
            )

        is_full_case_upgrade = (
            manifest.get("mode") == "full_case"
            and covered_stages == list(stages)
            and case_doc.get("current_state") in ("STRUCTURE_PASSED", *VERIFIED_STATES)
        )

        # 3. 核验外部净室回执与强制账本绑定（B01 / Opus B2）
        receipt = _read_json(receipt_path, label="净室复演回执")
        inv = receipt.get("invocation") if isinstance(receipt.get("invocation"), dict) else {}
        if receipt.get("timeout_salvage") or inv.get("timeout_salvage"):
            raise GateError("REPLAY_TIMEOUT_SALVAGE_REJECTED", "净室回执包含 timeout_salvage（超时半截产物），严禁用于验收升格")
        if receipt.get("status") != "ok" or receipt.get("succeeded") is not True:
            raise GateError("REPLAY_RECEIPT_FAILED", f"净室回执状态未成功（status={receipt.get('status')!r}, succeeded={receipt.get('succeeded')!r}）")
        if receipt.get("readonly") is not True or receipt.get("scope_verified") is not True or bool(receipt.get("changed_files")):
            raise GateError("REPLAY_SCOPE_VIOLATION", "净室回执未通过只读零改动核验（readonly/scope_verified/changed_files）")
        iso = str(receipt.get("isolation") or inv.get("isolation") or "").strip()
        is_clean_room = (iso == "clean_room") or (receipt.get("clean_room") is True) or (inv.get("clean_room") is True)
        if not is_clean_room:
            raise GateError("REPLAY_NOT_CLEAN_ROOM", f"复演回执隔离级别为 {iso or 'unknown'}，必须使用 --clean-room 净室回执")

        replayer_sid = str(receipt.get("provider_session_id") or inv.get("provider_session_id") or "").strip()
        if not replayer_sid:
            raise GateError("SAME_CONTEXT_REPLAY_FORBIDDEN", "净室回执缺少独立 provider_session_id，拒绝自签")
        # Opus B2 清零：严禁从回执自取 generator_session_id；整单升格强制传入 --generator-session-id
        gen_sid = str(getattr(args, "generator_session_id", "") or "").strip()
        if is_full_case_upgrade and not gen_sid:
            raise GateError("SAME_CONTEXT_REPLAY_FORBIDDEN", "整单升格 VERIFIED 必须显式传入 --generator-session-id 以核验异源会话")
        if gen_sid and replayer_sid == gen_sid:
            raise GateError("SAME_CONTEXT_REPLAY_FORBIDDEN", f"复演会话 ID（{replayer_sid}）与主会话 generator_session_id 相同，禁止同上下文自签")

        plan_dir_raw = str(getattr(args, "harness_plan_dir", "") or "").strip()
        if is_full_case_upgrade and not plan_dir_raw:
            raise GateError(
                "REPLAY_HARNESS_LEDGER_REQUIRED",
                "整单升格 VERIFIED 必须传入 --harness-plan-dir 完成 APPROVED 票据与机械对账账本交叉验签（禁止孤立本地手写回执）",
            )
        ledger_check: Optional[Dict[str, Any]] = None
        if plan_dir_raw:
            ledger_check = _verify_harness_ledger_crosscheck(
                receipt,
                Path(plan_dir_raw),
                packet_dir=packet_dir,
                packet_sha=claimed_pkt_sha,
                explicit_ticket_id=str(getattr(args, "ticket_id", "") or "").strip(),
            )

        replayer_engine = str(receipt.get("engine") or inv.get("engine") or "").strip()
        replayer_model = str(receipt.get("model") or inv.get("actual_model") or "").strip()
        gen_family = str(getattr(args, "generator_model_family", "") or "").strip().lower()
        same_family_warn = bool(gen_family and gen_family in f"{replayer_engine} {replayer_model}".lower())

        # 4. 解析逐关三值判决 + 强制校验 packet_sha256、非空 rationale 与盲推比对（Opus B1 / B01 / S11）
        verdict_obj = _extract_replay_verdict_object(receipt)
        v_pkt_sha = str(verdict_obj.get("packet_sha256") or "").strip()
        if not v_pkt_sha or v_pkt_sha != claimed_pkt_sha:
            raise GateError("REPLAY_PACKET_HASH_MISMATCH", f"复演判决必须显式声明并精确匹配 packet_sha256（收到 {v_pkt_sha[:16]!r}，期望 {claimed_pkt_sha[:16]!r}）")

        raw_sv = verdict_obj.get("stage_verdicts")
        if not isinstance(raw_sv, dict):
            raise GateError("REPLAY_INCOMPLETE_STAGES", "复演判决缺少逐关字典 stage_verdicts")

        missing_stages = [st for st in covered_stages if st not in raw_sv]
        if missing_stages:
            raise GateError("REPLAY_INCOMPLETE_STAGES", f"复演判决缺失以下关口判决: {missing_stages}")
        extra_stages = [st for st in raw_sv.keys() if st not in covered_stages]
        if extra_stages:
            raise GateError("REPLAY_UNEXPECTED_STAGES", f"复演判决包含未覆盖的额外关口键: {extra_stages}")

        parsed_sv: Dict[str, Dict[str, Any]] = {}
        inconsistent_stages: List[str] = []
        insufficient_stages: List[str] = []
        requested_paths: List[str] = []
        for st in covered_stages:
            item = raw_sv[st]
            if not isinstance(item, dict):
                raise GateError("REPLAY_VERDICT_UNPARSEABLE", f"stage_verdicts[{st}] 必须为对象")
            sv = strict_str(item.get("verdict"), f"stage_verdicts.{st}.verdict", allowed=REPLAY_STAGE_VERDICTS)
            raw_req_p = item.get("requested_paths")
            if raw_req_p is None:
                req_p: List[str] = []
            elif isinstance(raw_req_p, list) and all(isinstance(x, str) and x.strip() for x in raw_req_p):
                req_p = [x.strip() for x in raw_req_p]
            else:
                raise GateError("SCHEMA_TYPE_ERROR", f"stage_verdicts.{st}.requested_paths 必须为非空字符串数组")
            rat = strict_str(item.get("rationale"), f"stage_verdicts.{st}.rationale")
            ba = item.get("blind_assertions")
            parsed_sv[st] = {
                "verdict": sv,
                "requested_paths": req_p,
                "rationale": rat,
                **({"blind_assertions": ba} if ba is not None else {}),
            }
            if sv == "inconsistent":
                inconsistent_stages.append(st)
            elif sv == "insufficient_context":
                if not req_p:
                    raise GateError("REPLAY_INSUFFICIENT_MISSING_PATHS", f"stage_verdicts[{st}] 判为 insufficient_context 时必须提供非空 requested_paths")
                insufficient_stages.append(st)
                for p in req_p:
                    if p not in requested_paths:
                        requested_paths.append(p)

        blind_mismatches: List[Dict[str, Any]] = []
        if not inconsistent_stages and not insufficient_stages:
            blind_mismatches = _verify_blind_assertions(covered_stages, records, parsed_sv)

        # S3 / B10 清零：区分正当补包（前次 insufficient_context 且本次新增了 extra_included_paths）与反复刷榜
        prior_attempts = case_doc.get("replay_attempts") or []
        cur_extra_paths = set(manifest.get("extra_included_paths") or [])
        fitting_prior_hits = 0
        for prev in prior_attempts:
            if prev.get("artifacts_union_sha256") != cur_artifacts_union_sha:
                continue
            prev_out = prev.get("outcome")
            if prev_out in ("inconsistent", "blind_assertion_mismatch", "probe_rerun_failed"):
                fitting_prior_hits += 1
            elif prev_out == "insufficient_context":
                prev_extras = set(prev.get("extra_included_paths") or [])
                if not (cur_extra_paths - prev_extras):
                    # 未补入任何新上下文却用同资产重复重演，记为刷榜嫌疑
                    fitting_prior_hits += 1
        packet_fitting_suspected = fitting_prior_hits > 0

        outcome = (
            "inconsistent" if inconsistent_stages
            else ("insufficient_context" if insufficient_stages else ("blind_assertion_mismatch" if blind_mismatches else "consistent"))
        )
        attempt_entry = _append_replay_attempt(case_doc, {
            "recorded_at": _now_utc_iso(),
            "mode": manifest.get("mode"),
            "packet_sha256": claimed_pkt_sha,
            "artifacts_union_sha256": cur_artifacts_union_sha,
            "extra_included_paths": sorted(cur_extra_paths),
            "replayer_session_id": replayer_sid,
            "replayer_engine": replayer_engine,
            "replayer_model": replayer_model,
            "same_model_family_warning": same_family_warn,
            "outcome": outcome,
            "packet_fitting_suspected": packet_fitting_suspected,
            "stage_verdicts": parsed_sv,
            "blind_assertion_mismatches": blind_mismatches,
            "harness_run_id": (receipt.get("reconciliation") or {}).get("run_id"),
            "ledger_check": ledger_check,
        })
        case_doc["updated_at"] = _now_utc_iso()

        if inconsistent_stages or blind_mismatches:
            # B10 清零：若该案卷此前已升格 VERIFIED_*，一旦被合法净室复演否决，立即撤销旧证书并降回 STRUCTURE_PASSED！
            old_cert = case_doc.get("replay_certificate")
            if isinstance(old_cert, dict) and old_cert.get("status") == "REPLAY_VERIFIED":
                old_cert["status"] = "REVOKED_BY_REPLAY_CONFLICT"
                old_cert["revoked_at"] = _now_utc_iso()
                old_cert["revoked_by_attempt_seq"] = attempt_entry["attempt_seq"]
                case_doc["verification_level"] = "STRUCTURE_ONLY"
                case_doc["t_assertion_level"] = "NO_T_ASSERTION"
                case_doc["current_state"] = "STRUCTURE_PASSED"
                if len(case_doc.get("effective_claim_chain") or []) > 1:
                    case_doc["original_claim_status"] = "ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED"
                    case_doc["effective_claim_status"] = "STRUCTURE_ONLY_UNVERIFIED"
                else:
                    case_doc["original_claim_status"] = "STRUCTURE_ONLY_UNVERIFIED"
                    case_doc["effective_claim_status"] = "STRUCTURE_ONLY_UNVERIFIED"
            _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
            if inconsistent_stages:
                raise GateError(
                    "REPLAY_VERDICT_CONFLICT",
                    f"净室复演在关口 {inconsistent_stages} 给出 inconsistent 冲突裁定，拒绝升格并撤销任何既有 VERIFIED 证书",
                    {"inconsistent_stages": inconsistent_stages, "attempt_seq": attempt_entry["attempt_seq"]},
                )
            raise GateError(
                "REPLAY_ASSERTION_MISMATCH",
                "净室复演独立盲推的 blind_assertions 与案卷原始结论不一致（一.8/四.4 盲推比对失败），拒绝升格 VERIFIED",
                {"mismatches": blind_mismatches, "attempt_seq": attempt_entry["attempt_seq"]},
            )
        if insufficient_stages:
            _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
            raise GateError(
                "REPLAY_INSUFFICIENT_CONTEXT",
                f"净室复演在关口 {insufficient_stages} 报告上下文不足，请按 requested_paths 经 --include-path 补包重演（严禁脑补放行）",
                {"insufficient_stages": insufficient_stages, "requested_paths": requested_paths, "attempt_seq": attempt_entry["attempt_seq"]},
            )

        # 5. 全覆盖关口均为 consistent 且盲推比对通过：处理整单终态升格或单关复演（O-B1 / O-B2 / B03 / B4 / B5）
        rerun_probes: List[Dict[str, Any]] = []
        if is_full_case_upgrade:
            # 5a) B03 / Opus B3 清零：逐条精准核验每条 ast_only Lean 见证，严禁批量标记未审计外部模块，并重算摘要链
            lean_witnesses = [
                (st, w) for st in stages
                for w in (records[st].get("witnesses") or [])
                if w.get("type") == "lean_theorem"
            ]
            ast_only_leaks = [(st, w) for st, w in lean_witnesses if w.get("verification_level") != "kernel_compiled"]
            if ast_only_leaks:
                if not getattr(args, "run_lean", False):
                    _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
                    raise GateError(
                        "UNVERIFIED_AST_ONLY_LEAN",
                        "审查单包含未经 --run-lean 真内核编译的 ast_only lean_theorem 见证，禁止升格 VERIFIED（请在 advance 或 verify-replay 启用 --run-lean）",
                    )
                lean_audit = audit_lean_suite(
                    Path(args.lean_dir) if getattr(args, "lean_dir", "") else None,
                    run_lean=True,
                    lean_cmd=getattr(args, "lean_cmd", "") or "",
                    wsl_lean=bool(getattr(args, "wsl_lean", False)),
                    profile=profile,
                )
                if not lean_audit["ok"]:
                    _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
                    raise GateError("LEAN_GATE_FAILED", "升格 VERIFIED 时现场 --run-lean 真内核校验未通过")
                k_thms_by_mod = lean_audit.get("kernel_theorems") or {}
                ambig_by_mod = lean_audit.get("ambiguous_short_names") or {}
                src_hashes_by_mod = lean_audit.get("source_hashes") or {}
                for st, w in ast_only_leaks:
                    mod = str(w.get("module") or "")
                    thm = str(w.get("theorem") or "")
                    if thm in (ambig_by_mod.get(mod) or []):
                        _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
                        raise GateError("AMBIGUOUS_THEOREM_NAME", f"{st} 见证 {mod}.{thm} 存在短名歧义，禁止升格")
                    k_info = (k_thms_by_mod.get(mod) or {}).get(thm)
                    if not k_info or (w.get("lean_source_sha256") and src_hashes_by_mod.get(mod) != w.get("lean_source_sha256")):
                        _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
                        raise GateError(
                            "UNVERIFIED_AST_ONLY_LEAN",
                            f"{st} 的 Lean 见证 {mod}.{thm} 未在当前 --run-lean 审计套件中逐条命中或源哈希不匹配，禁止批量升格为 kernel_compiled",
                        )
                    w["verification_level"] = "kernel_compiled"
                    w["verified"] = True
                    w["lean_compiler_status"] = "passed"
                    w["kernel_full_name"] = k_info["full_name"]
                    w["kernel_type_sha256"] = k_info["type_sha256"]
                _recompute_case_stage_chain(case_doc, stages)

            # 5b) Opus B4 / B10 清零：禁止任何离线自报探针混入升格；含实跑探针强制当轮 --allow-probe-exec 重跑且先落盘尝试账本
            all_probes = [
                w for st in stages
                for w in (records[st].get("witnesses") or [])
                if w.get("type") == "probe_exec"
            ]
            offline_probes = [w for w in all_probes if not (w.get("live_verified") is True and w.get("execute") is True)]
            live_probes = [w for w in all_probes if w.get("live_verified") is True and w.get("execute") is True]
            if offline_probes:
                _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
                raise GateError(
                    "OFFLINE_PROBE_CANNOT_GRANT_VERIFIED",
                    f"审查单包含 {len(offline_probes)} 条离线自报 probe_exec（execute=false），严禁靠混入实跑探针洗白升格 VERIFIED（离线回执最高封顶 STRUCTURE_PASSED）",
                )
            kernel_leaks_ok = [w for _, w in lean_witnesses if w.get("verification_level") == "kernel_compiled" and w.get("verified") is True]
            if not live_probes and not kernel_leaks_ok:
                _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
                raise GateError(
                    "MISSING_LIVE_OR_KERNEL_WITNESS",
                    "审查单既无实跑 probe_exec 探针又无经真内核编译的 lean_theorem 见证，缺少外部确定性执行验签，最高封顶 STRUCTURE_PASSED",
                )
            if live_probes:
                if not getattr(args, "allow_probe_exec", False):
                    _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
                    raise GateError(
                        "PROBE_RERUN_REQUIRED_FOR_VERIFIED",
                        "升格 VERIFIED 时必须显式传入 --allow-probe-exec 以在终态哈希下现场重跑本地已锁定实跑探针",
                    )
                # B10 清零：重跑探针失败也要保留失败账本记录，绝不丢账
                try:
                    for pw in live_probes:
                        rerun_probes.append(rerun_live_probe(pw, project_root))
                except GateError:
                    attempt_entry["outcome"] = "probe_rerun_failed"
                    body_no_h = {k: v for k, v in attempt_entry.items() if k != "entry_hash"}
                    attempt_entry["entry_hash"] = canonical_json_sha256(body_no_h)[:24]
                    _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
                    raise

            cert = {
                "status": "REPLAY_VERIFIED",
                "verified_at": _now_utc_iso(),
                "packet_sha256": claimed_pkt_sha,
                "artifacts_union_sha256": cur_artifacts_union_sha,
                "file_tree_blob_sha256": cur_tree_sha,
                "effective_claim_hash": case_doc.get("effective_claim_hash"),
                "gate_sha256": cur_code_shas["gate_sha256"],
                "lean_driver_sha256": cur_code_shas["lean_driver_sha256"],
                "replayer_session_id": replayer_sid,
                "replayer_engine": replayer_engine,
                "replayer_model": replayer_model,
                "same_model_family_warning": same_family_warn,
                "packet_fitting_suspected": packet_fitting_suspected,
                "harness_run_id": (receipt.get("reconciliation") or {}).get("run_id"),
                "ledger_check": ledger_check,
                "rerun_live_probes_count": len(rerun_probes),
                "stage_verdicts": parsed_sv,
            }
            case_doc["replay_certificate"] = cert
            case_doc["verification_level"] = "VERIFIED"
            last_meta = (records[stages[-1]].get("meta") or {})
            can_assert_t = (last_meta.get("allow_t_assertion") is True) and (not packet_fitting_suspected)
            is_child_claim = len(case_doc.get("effective_claim_chain") or []) > 1
            if can_assert_t:
                case_doc["t_assertion_level"] = "T_ASSERTED"
                case_doc["current_state"] = "VERIFIED_T_ASSERTED"
                eff_status = "PASSED_ALL_GATES"
            else:
                case_doc["t_assertion_level"] = "NO_T_ASSERTION"
                case_doc["current_state"] = "VERIFIED_NO_T"
                eff_status = "VERIFIED_NO_T_ASSERTION"
            # Opus B5 清零：降格/重构子单升格绝不改写 original_claim_status，原命题永久标为不可满足
            case_doc["effective_claim_status"] = eff_status
            case_doc["original_claim_status"] = "ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED" if is_child_claim else eff_status
        else:
            stage_certs = case_doc.setdefault("stage_replay_certificates", {})
            for st in covered_stages:
                stage_certs[st] = {
                    "status": "REPLAY_VERIFIED",
                    "verified_at": _now_utc_iso(),
                    "packet_sha256": claimed_pkt_sha,
                    "replayer_session_id": replayer_sid,
                    "verdict": parsed_sv[st],
                }

        _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps({
        "ok": True,
        "replay_status": "REPLAY_VERIFIED",
        "upgraded_case_terminal": is_full_case_upgrade,
        "current_state": case_doc["current_state"],
        "verification_level": case_doc["verification_level"],
        "t_assertion_level": case_doc["t_assertion_level"],
        "original_claim_status": case_doc["original_claim_status"],
        "effective_claim_status": case_doc.get("effective_claim_status"),
        "packet_sha256": claimed_pkt_sha,
        "attempt_seq": attempt_entry["attempt_seq"],
        "packet_fitting_suspected": packet_fitting_suspected,
        "same_model_family_warning": same_family_warn,
        "rerun_live_probes_count": len(rerun_probes),
    }, ensure_ascii=False, indent=2))
    return 0


__all__ = ["REPLAY_PROTOCOL_VERSION", "cmd_export_packet", "cmd_verify_replay"]
