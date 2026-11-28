# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_axiom_lib.case - 审查单读写、前驱链校验、双哈希精准失活、双正交轴分账与重构幂等事务。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .contract import (
    ALL_STAGES,
    FEEDBACK_LAYERS,
    ISSUE_KIND_PRIORITY,
    LOCKED_HISTORY_STATES,
    PROFILE_HOLD_STAGE,
    PROFILE_HOLD_STATE,
    PROFILE_ROOT_HALT_STAGES,
    PROFILE_STAGES,
    PROFILES,
    SCHEMA_VERSION,
    TERMINAL_ADVANCE_BLOCK_STATES,
    VERIFIED_STATES,
    GateError,
    _atomic_write_text,
    _normalize_claim_text,
    _now_utc_iso,
    _read_json,
    _reject_links,
    canonical_json_sha256,
    case_file_lock,
    compute_gate_code_sha256,
)
from .evidence import _verify_witness_list, recompute_witness_drift, rerun_live_probe
from .lean_gate import audit_lean_suite
from .rules import _STAGE_EVALUATORS, _validate_non_formalized_zones


def _capture_git_baseline(project_root: Path) -> Dict[str, Any]:
    """记录 init 时的基线 Git HEAD 与树哈希（非 Git 目录优雅留空）。"""
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        return {"is_git_repo": True, "head_commit": head, "tree_sha": tree}
    except (OSError, subprocess.SubprocessError):
        return {"is_git_repo": False, "head_commit": None, "tree_sha": None}


def _append_conflict_history(case_doc: Dict[str, Any], stage: str, structured_issues: List[Dict[str, str]]) -> None:
    history = case_doc.setdefault("immutable_conflict_history", [])
    prev_hash = str(history[-1].get("entry_hash", "")) if history else "GENESIS"
    entry: Dict[str, Any] = {
        "recorded_at": _now_utc_iso(),
        "stage": stage,
        "original_claim": case_doc.get("root_claim") or case_doc.get("original_claim"),
        "effective_claim": case_doc.get("effective_claim") or case_doc.get("original_claim"),
        "issues": [it["message"] for it in structured_issues],
        "structured_issues": list(structured_issues),
        "prev_hash": prev_hash,
    }
    digest_payload = json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8")
    entry["entry_hash"] = hashlib.sha256(digest_payload).hexdigest()[:24]
    history.append(entry)
    case_doc["conflict_history_count"] = len(history)


def _verify_conflict_history_chain(case_doc: Dict[str, Any]) -> None:
    history = case_doc.get("immutable_conflict_history")
    if history is None:
        if case_doc.get("root_conflict_stage") or case_doc.get("first_conflict_stage") or int(case_doc.get("conflict_history_count") or 0) > 0:
            raise GateError("CONFLICT_HISTORY_TAMPERED", "审查单记录过冲突但 immutable_conflict_history 被删除")
        return
    if not isinstance(history, list):
        raise GateError("CONFLICT_HISTORY_TAMPERED", "immutable_conflict_history 必须为列表")
    recorded_count = case_doc.get("conflict_history_count")
    if recorded_count is not None and (type(recorded_count) is not int or len(history) < recorded_count):
        raise GateError(
            "CONFLICT_HISTORY_TAMPERED",
            f"immutable_conflict_history 长度（{len(history)}）少于登记计数（{recorded_count}），疑似截断末条",
        )
    records = case_doc.get("stage_records") or {}
    conflict_rec_count = sum(1 for r in records.values() if isinstance(r, dict) and r.get("verdict") == "conflict")
    if (case_doc.get("root_conflict_stage") or case_doc.get("first_conflict_stage") or conflict_rec_count > 0) and len(history) < max(1, conflict_rec_count):
        raise GateError("CONFLICT_HISTORY_TAMPERED", "审查单存在冲突标记或冲突关口但冲突历史条数不足，疑似被删改")
    expected_prev = "GENESIS"
    for idx, entry in enumerate(history, 1):
        if not isinstance(entry, dict):
            raise GateError("CONFLICT_HISTORY_TAMPERED", f"冲突历史第 {idx} 条非对象")
        if entry.get("prev_hash") != expected_prev:
            raise GateError("CONFLICT_HISTORY_TAMPERED", f"冲突历史第 {idx} 条 prev_hash 断裂")
        recorded_hash = str(entry.get("entry_hash") or "")
        body = {k: v for k, v in entry.items() if k != "entry_hash"}
        recomputed = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
        if recorded_hash != recomputed:
            raise GateError("CONFLICT_HISTORY_TAMPERED", f"冲突历史第 {idx} 条 entry_hash 校验失败，疑似被篡改")
        expected_prev = recorded_hash


def _verify_case_schema_version(case_doc: Dict[str, Any]) -> None:
    ver = case_doc.get("schema_version")
    if type(ver) is not int or ver != SCHEMA_VERSION:
        raise GateError(
            "UNSUPPORTED_SCHEMA_VERSION",
            f"审查单 schema_version={ver!r} 与当前状态机要求（v{SCHEMA_VERSION}）不匹配，拒绝静默解析",
        )


def _assert_target_not_locked_case(path: Path, *, action_label: str) -> None:
    if path.is_dir():
        raise GateError("INVALID_TARGET_PATH", f"{action_label} 目标路径是目录而非文件: {path}")
    if not path.exists():
        return
    try:
        existing = _read_json(path, label=f"{action_label} 既有目标")
    except GateError as exc:
        raise GateError(
            "IMMUTABLE_HISTORY_LOCKED",
            f"{action_label} 拒绝覆写无法解析为合法 JSON 的既有文件（防破坏或绕过历史回锁）: {path} ({exc.code})",
        ) from exc
    existing_state = str(existing.get("current_state") or "")
    if bool(existing.get("immutable_conflict_history")) or existing_state in LOCKED_HISTORY_STATES:
        raise GateError(
            "IMMUTABLE_HISTORY_LOCKED",
            f"{action_label} 禁止覆写或清空已记录冲突历史/已锁定的审查单（state={existing_state}）: {path}",
        )


def _case_profile(case_doc: Dict[str, Any]) -> str:
    prof = str(case_doc.get("profile") or "").strip()
    if prof not in PROFILE_STAGES:
        raise GateError("INVALID_PROFILE", f"审查单包含未知 profile: {prof!r}（允许值: {', '.join(PROFILES)}）")
    return prof


def _resolve_case_project_root(case_doc: Dict[str, Any], override_root: str = "") -> Path:
    raw = str(override_root or case_doc.get("project_root") or "").strip()
    if not raw:
        raise GateError("PROJECT_ROOT_MISSING", "审查单缺少有效的 project_root")
    resolved = _reject_links(Path(raw), label="课题根目录").resolve()
    if not resolved.is_dir():
        raise GateError("PROJECT_ROOT_MISSING", f"课题根目录不存在: {resolved}")
    return resolved


def _verify_effective_claim_integrity(case_doc: Dict[str, Any]) -> Tuple[bool, str]:
    root_claim = str(case_doc.get("root_claim") or "")
    root_sha = str(case_doc.get("root_claim_sha256") or "")
    if hashlib.sha256(root_claim.encode("utf-8")).hexdigest() != root_sha:
        return False, "root_claim_sha256_mismatch"
    if str(case_doc.get("original_claim") or "") != root_claim:
        return False, "original_claim_diverged_from_root_claim"
    chain = case_doc.get("effective_claim_chain")
    if not isinstance(chain, list) or not chain:
        return False, "effective_claim_chain_missing"
    recomputed_hash = canonical_json_sha256(chain)
    if recomputed_hash != case_doc.get("effective_claim_hash"):
        return False, "effective_claim_hash_mismatch"
    if str(chain[-1].get("claim") or "") != str(case_doc.get("effective_claim") or ""):
        return False, "effective_claim_head_mismatch"
    return True, "ok"


def _compute_stage_digest(
    *,
    profile: str,
    stage: str,
    effective_claim_hash: str,
    prev_stage_digest: str,
    payload: Dict[str, Any],
    witnesses: List[Dict[str, Any]],
    verdict: str,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    return canonical_json_sha256({
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "stage": stage,
        "effective_claim_hash": effective_claim_hash,
        "prev_stage_digest": prev_stage_digest,
        "payload": payload,
        "witnesses": witnesses,
        "verdict": verdict,
        "meta": meta or {},
    })


def _collect_all_stage_artifact_hashes(case_doc: Dict[str, Any]) -> Dict[str, str]:
    """汇总全部已通过关口锚定文件与依赖的生效哈希集合（供第 N+1 关复演证书绑定）。"""
    out: Dict[str, str] = {}
    profile = _case_profile(case_doc)
    records = case_doc.get("stage_records") or {}
    for stage in PROFILE_STAGES[profile]:
        rec = records.get(stage)
        if not isinstance(rec, dict) or rec.get("verdict") != "pass" or rec.get("freshness_status") == "STALE_INVALIDATED":
            continue
        for w in rec.get("witnesses") or []:
            wtype = w.get("type")
            if wtype == "source_anchor" and w.get("file"):
                out[f"src:{w['file']}"] = str(w.get("artifact_sha256") or "")
            elif wtype == "probe_exec":
                if w.get("receipt_file"):
                    out[f"receipt:{w['receipt_file']}"] = str(w.get("artifact_sha256") or "")
                for tf in w.get("target_files") or []:
                    if tf.get("file"):
                        out[f"probe_target:{tf['file']}"] = str(tf.get("raw_sha256") or "")
                dom = w.get("domain_spec")
                if isinstance(dom, dict) and dom.get("file"):
                    out[f"domain_spec:{dom['file']}"] = str(dom.get("artifact_sha256") or "")
            elif wtype == "lean_theorem":
                if w.get("module") and w.get("lean_source_sha256"):
                    out[f"lean:{w['module']}"] = str(w["lean_source_sha256"])
                for af in w.get("artifact_files") or []:
                    if af.get("file"):
                        out[f"lean_art:{af['file']}"] = str(af.get("artifact_sha256") or "")
    return dict(sorted(out.items()))


def _invalidate_from_stage(
    case_doc: Dict[str, Any],
    stages: Tuple[str, ...],
    start_idx: int,
    reason_code: str,
    details: Dict[str, Any],
) -> None:
    """精准失活第 start_idx 关及全部下游关口（含第 N+1 关复演证书与单关复演证书），完整保留 0..start_idx-1 有效前缀。"""
    records = case_doc.get("stage_records") or {}
    stage_certs = case_doc.get("stage_replay_certificates")
    for idx in range(start_idx, len(stages)):
        st = stages[idx]
        rec = records.get(st)
        if isinstance(rec, dict):
            rec["freshness_status"] = "STALE_INVALIDATED"
            rec["stale_reason"] = reason_code
            rec["stale_details"] = details
            rec["invalidated_at"] = _now_utc_iso()
        if isinstance(stage_certs, dict) and isinstance(stage_certs.get(st), dict):
            stage_certs[st]["status"] = "STALE_INVALIDATED"
            stage_certs[st]["stale_reason"] = reason_code
    cert = case_doc.get("replay_certificate")
    if isinstance(cert, dict):
        cert["status"] = "STALE_INVALIDATED"
        cert["stale_reason"] = reason_code
        cert["invalidated_at"] = _now_utc_iso()
    case_doc["verification_level"] = "STRUCTURE_ONLY"
    case_doc["t_assertion_level"] = "NO_T_ASSERTION"
    case_doc["current_state"] = "STALE_INVALIDATED"
    if len(case_doc.get("effective_claim_chain") or []) > 1:
        case_doc["original_claim_status"] = "ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED"
        case_doc["effective_claim_status"] = "STALE_INVALIDATED"
    else:
        case_doc["original_claim_status"] = "STALE_INVALIDATED"
        case_doc["effective_claim_status"] = "STALE_INVALIDATED"
    case_doc["next_stage"] = stages[start_idx]
    case_doc["updated_at"] = _now_utc_iso()


def reconcile_case_freshness(
    case_doc: Dict[str, Any],
    project_root: Path,
    *,
    case_path: Optional[Path] = None,
    persist_on_invalidation: bool = False,
    stop_before_stage: Optional[str] = None,
) -> Dict[str, Any]:
    """现场重算前提哈希、各关双哈希与第 N+1 关复演证书绑定；检测到漂移即执行精准失活（绝不解除已锁定冲突吸收态）。"""
    profile = _case_profile(case_doc)
    stages = PROFILE_STAGES[profile]
    cur_state = str(case_doc.get("current_state") or "")
    is_locked_history = cur_state in LOCKED_HISTORY_STATES

    claim_ok, claim_reason = _verify_effective_claim_integrity(case_doc)
    if not claim_ok:
        if not is_locked_history:
            _invalidate_from_stage(case_doc, stages, 0, "PREMISE_DRIFT_INVALIDATED", {"reason": claim_reason})
            if persist_on_invalidation and case_path is not None:
                _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
        return {
            "fresh": False,
            "error_code": "PREMISE_DRIFT_INVALIDATED",
            "invalidated_from_stage": stages[0],
            "reason": claim_reason,
            "drifts": [{"type": "premise", "reason": claim_reason}],
        }

    # B05 清零：已锁入 HALT_ROOT_CONFLICT / HALT_CLOSURE_CONFLICT / REBASED / DOWNGRADED_NEW_CLAIM 的历史冲突案卷，
    # 磁盘源码变更仅提示快照已漂移，绝不解除吸收态锁定或重设 next_stage！
    if is_locked_history:
        return {"fresh": True, "error_code": None, "invalidated_from_stage": None, "reason": "locked_history_preserved", "drifts": []}

    stop_idx = stages.index(stop_before_stage) if (stop_before_stage in stages) else len(stages)
    records = case_doc.get("stage_records") or {}
    for idx, st in enumerate(stages):
        if idx >= stop_idx:
            break
        rec = records.get(st)
        if not isinstance(rec, dict):
            continue
        if rec.get("freshness_status") == "STALE_INVALIDATED":
            return {
                "fresh": False,
                "error_code": str(rec.get("stale_reason") or "STALE_WITNESS_HASH"),
                "invalidated_from_stage": st,
                "reason": "stage_already_stale_invalidated",
                "drifts": [{"type": "stage", "stage": st, "reason": "already_stale_invalidated"}],
            }
        drifts = recompute_witness_drift(rec.get("witnesses") or [], project_root)
        if drifts:
            _invalidate_from_stage(case_doc, stages, idx, "STALE_WITNESS_HASH", {"stage": st, "drifts": drifts})
            if persist_on_invalidation and case_path is not None:
                _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
            return {
                "fresh": False,
                "error_code": "STALE_WITNESS_HASH",
                "invalidated_from_stage": st,
                "reason": "witness_artifact_drift",
                "drifts": drifts,
            }

    if stop_before_stage is not None:
        return {"fresh": True, "error_code": None, "invalidated_from_stage": None, "reason": "ok", "drifts": []}

    # 第 N+1 关：检查已签发复演证书是否因 artifact 并集、有效命题或状态机/驱动代码变化而失活（O-B3 / B05）
    cert = case_doc.get("replay_certificate")
    if isinstance(cert, dict) and cert.get("status") == "REPLAY_VERIFIED":
        cur_code_shas = compute_gate_code_sha256()
        cur_artifacts_hash = canonical_json_sha256(_collect_all_stage_artifact_hashes(case_doc))
        cert_drifts: List[str] = []
        if cert.get("effective_claim_hash") != case_doc.get("effective_claim_hash"):
            cert_drifts.append("effective_claim_hash_mismatch")
        if cert.get("artifacts_union_sha256") != cur_artifacts_hash:
            cert_drifts.append("artifacts_union_sha256_mismatch")
        if cert.get("gate_sha256") != cur_code_shas["gate_sha256"]:
            cert_drifts.append("gate_sha256_mismatch")
        if cert.get("lean_driver_sha256") != cur_code_shas["lean_driver_sha256"]:
            cert_drifts.append("lean_driver_sha256_mismatch")
        if cert_drifts:
            cert["status"] = "STALE_INVALIDATED"
            cert["stale_reason"] = "REPLAY_CERT_STALE"
            cert["stale_details"] = cert_drifts
            cert["invalidated_at"] = _now_utc_iso()
            case_doc["verification_level"] = "STRUCTURE_ONLY"
            case_doc["t_assertion_level"] = "NO_T_ASSERTION"
            # B05 清零：仅第 N+1 关证书失活而前 N 关均 CURRENT+pass 时，精准回落 STRUCTURE_PASSED 以便重导包复演
            all_n_pass = all(
                isinstance(records.get(s), dict)
                and records[s].get("verdict") == "pass"
                and records[s].get("freshness_status") != "STALE_INVALIDATED"
                for s in stages
            )
            case_doc["current_state"] = "STRUCTURE_PASSED" if all_n_pass else "STALE_INVALIDATED"
            if len(case_doc.get("effective_claim_chain") or []) > 1:
                case_doc["original_claim_status"] = "ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED"
                case_doc["effective_claim_status"] = "STRUCTURE_ONLY_UNVERIFIED" if all_n_pass else "STALE_INVALIDATED"
            else:
                case_doc["original_claim_status"] = "STRUCTURE_ONLY_UNVERIFIED" if all_n_pass else "STALE_INVALIDATED"
                case_doc["effective_claim_status"] = case_doc["original_claim_status"]
            case_doc["updated_at"] = _now_utc_iso()
            if persist_on_invalidation and case_path is not None:
                _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
            return {
                "fresh": False,
                "error_code": "STALE_WITNESS_HASH",
                "invalidated_from_stage": "REPLAY_CERTIFICATE",
                "reason": ",".join(cert_drifts),
                "drifts": [{"type": "replay_certificate", "reason": r} for r in cert_drifts],
            }

    return {"fresh": True, "error_code": None, "invalidated_from_stage": None, "reason": "ok", "drifts": []}


def _reconstruct_expected_next_stage(case_doc: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """不盲信磁盘字段，从 stage_records 逐关现场重算 stage_digest 重建合法前驱链并返回 (expected_next, prev_digest)（B04 清零）。"""
    profile = _case_profile(case_doc)
    stages = PROFILE_STAGES[profile]
    hold_stage = PROFILE_HOLD_STAGE[profile]
    records = case_doc.get("stage_records") or {}
    eff_claim_hash = str(case_doc.get("effective_claim_hash") or "")
    prev_digest = "GENESIS"
    for idx, st in enumerate(stages):
        rec = records.get(st)
        if not isinstance(rec, dict):
            for later in stages[idx + 1 :]:
                later_rec = records.get(later)
                if isinstance(later_rec, dict) and later_rec.get("freshness_status") != "STALE_INVALIDATED":
                    raise GateError("BROKEN_STAGE_CHAIN", f"前驱关口 {st} 缺失但下游关口 {later} 已有记录，拒绝跳步状态")
            return st, prev_digest
        if rec.get("freshness_status") == "STALE_INVALIDATED":
            return st, prev_digest
        verdict = str(rec.get("verdict") or "")
        if verdict not in ("pass", "conflict"):
            raise GateError("BROKEN_STAGE_CHAIN", f"关口 {st} 含非法判定值: {verdict!r}")
        if rec.get("prev_stage_digest") != prev_digest:
            raise GateError("BROKEN_STAGE_CHAIN", f"关口 {st} 前驱摘要断裂，疑似篡改或跳步写入")
        recorded_digest = str(rec.get("stage_digest") or "")
        if not recorded_digest:
            raise GateError("BROKEN_STAGE_CHAIN", f"关口 {st} 缺少 stage_digest")
        recomputed = _compute_stage_digest(
            profile=profile,
            stage=st,
            effective_claim_hash=eff_claim_hash,
            prev_stage_digest=prev_digest,
            payload=rec.get("payload") if isinstance(rec.get("payload"), dict) else {},
            witnesses=rec.get("witnesses") if isinstance(rec.get("witnesses"), list) else [],
            verdict=verdict,
            meta=rec.get("meta") if isinstance(rec.get("meta"), dict) else {},
        )
        if recorded_digest != recomputed:
            raise GateError("BROKEN_STAGE_CHAIN", f"关口 {st} 内容摘要校验失败（疑似磁盘篡改 payload/witnesses/meta）")
        if verdict == "conflict":
            if st == hold_stage and case_doc.get("current_state") == PROFILE_HOLD_STATE[profile]:
                return st, prev_digest
            return None, recorded_digest
        prev_digest = recorded_digest
    return None, prev_digest


def cmd_check_lean(args: argparse.Namespace) -> int:
    lean_dir = Path(args.lean_dir) if args.lean_dir else None
    profile = str(getattr(args, "profile", "cae") or "cae").strip()
    result = audit_lean_suite(
        lean_dir,
        run_lean=bool(args.run_lean),
        lean_cmd=args.lean_cmd or "",
        wsl_lean=bool(args.wsl_lean),
        profile=profile,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


def cmd_init(args: argparse.Namespace) -> int:
    case_path = _reject_links(Path(args.case), label="审查单路径")
    with case_file_lock(case_path):
        if case_path.exists():
            if not args.force:
                raise GateError("CASE_EXISTS", f"审查单已存在（如需覆盖请显式传 --force）: {case_path}")
            _assert_target_not_locked_case(case_path, action_label="init --force")
        project_root = _reject_links(Path(args.project_root), label="课题根目录").resolve()
        if not project_root.is_dir():
            raise GateError("PROJECT_ROOT_MISSING", f"课题根目录不存在: {project_root}")
        subject = str(args.subject or "").strip()
        if not subject:
            raise GateError("INVALID_ARG", "--subject 不能为空")
        profile = str(getattr(args, "profile", "cae") or "cae").strip()
        if profile not in PROFILE_STAGES:
            raise GateError("INVALID_PROFILE", f"--profile 须属于 {list(PROFILES)}，收到: {profile!r}")
        stages = PROFILE_STAGES[profile]
        root_claim = str(args.claim or subject).strip()
        now = _now_utc_iso()
        root_claim_sha = hashlib.sha256(root_claim.encode("utf-8")).hexdigest()
        claim_chain = [{"seq": 0, "kind": "root", "claim": root_claim, "claim_sha256": root_claim_sha, "recorded_at": now}]
        eff_claim_hash = canonical_json_sha256(claim_chain)
        code_shas = compute_gate_code_sha256()

        lean_audit = audit_lean_suite(Path(args.lean_dir) if args.lean_dir else None, run_lean=False, profile=profile)
        case_doc: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "profile": profile,
            "created_at": now,
            "updated_at": now,
            "project_root": str(project_root),
            "init_baseline": _capture_git_baseline(project_root),
            "gate_sha256": code_shas["gate_sha256"],
            "lean_driver_sha256": code_shas["lean_driver_sha256"],
            "subject": subject,
            "root_claim": root_claim,
            "root_claim_sha256": root_claim_sha,
            "original_claim": root_claim,
            "effective_claim": root_claim,
            "effective_claim_chain": claim_chain,
            "effective_claim_hash": eff_claim_hash,
            "original_claim_status": "PENDING",
            "verification_level": "STRUCTURE_ONLY",
            "t_assertion_level": "NO_T_ASSERTION",
            "current_state": "INIT",
            "next_stage": stages[0],
            "root_conflict_stage": None,
            "first_conflict_stage": None,
            "lean_ast_gate_at_init": {
                "ast_gate_ok": lean_audit["ast_gate_ok"],
                "checked_files": lean_audit["checked_files"],
                "violation_count": lean_audit["violation_count"],
            },
            "stage_records": {},
            "replay_certificate": None,
            "replay_attempts": [],
            "immutable_conflict_history": [],
            "resolutions": [],
            "pending_child_tx": None,
            "evolution_feedback": [],
        }
        _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "case_file": str(case_path),
        "current_state": "INIT",
        "verification_level": "STRUCTURE_ONLY",
        "t_assertion_level": "NO_T_ASSERTION",
        "next_stage": stages[0],
        "root_claim_sha256": root_claim_sha,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    case_path = _reject_links(Path(args.case), label="审查单路径")
    with case_file_lock(case_path):
        case_doc = _read_json(case_path, label="审查单")
        _verify_case_schema_version(case_doc)
        _verify_conflict_history_chain(case_doc)
        profile = _case_profile(case_doc)
        stages = PROFILE_STAGES[profile]
        root_halt_stages = PROFILE_ROOT_HALT_STAGES[profile]
        hold_stage = PROFILE_HOLD_STAGE[profile]
        hold_state = PROFILE_HOLD_STATE[profile]

        stage = str(args.stage or "").strip()
        if stage not in ALL_STAGES:
            raise GateError("UNKNOWN_STAGE", f"未知状态关口: {stage}（允许值: {', '.join(stages)}）")
        if stage not in stages:
            raise GateError(
                "STAGE_PROFILE_MISMATCH",
                f"当前审查单 profile={profile} 仅允许关口 {', '.join(stages)}，拒绝跨档提交 {stage}",
            )

        cur_state = str(case_doc.get("current_state") or "")
        if cur_state == "HALT_ROOT_CONFLICT":
            raise GateError(
                "ROOT_CONFLICT_LOCKED",
                f"状态机已于根层 {case_doc.get('root_conflict_stage')} 锁入吸收态 HALT_ROOT_CONFLICT，"
                f"硬拒绝推进至 {stage}；仅允许通过 resolve 执行 rebase_ontology 或 downgrade_claim",
            )
        if cur_state in TERMINAL_ADVANCE_BLOCK_STATES:
            raise GateError("CASE_TERMINATED", f"当前审查单已处于终态 {cur_state}，不能继续 advance")

        project_root = _resolve_case_project_root(case_doc, getattr(args, "project_root", ""))
        # 现场双哈希重算与精准级联失活（O-B3 / O-B5 / B5）：仅核验严格前驱 0..stage-1，允许重提交当前已失活关口以恢复推进
        fresh_rep = reconcile_case_freshness(
            case_doc,
            project_root,
            case_path=case_path,
            persist_on_invalidation=True,
            stop_before_stage=stage,
        )
        if not fresh_rep["fresh"]:
            raise GateError(
                str(fresh_rep["error_code"]),
                f"检测到上游前提或锚定见证漂移，已精准失活自 {fresh_rep['invalidated_from_stage']} 起的关口（原因: {fresh_rep['reason']}）",
                {"invalidated_from_stage": fresh_rep["invalidated_from_stage"], "drifts": fresh_rep["drifts"]},
            )

        # 不盲信磁盘 next_stage，从前驱记录严格重建合法下一关（B5 清零）
        reconstructed_next, prev_digest = _reconstruct_expected_next_stage(case_doc)
        disk_next = case_doc.get("next_stage")
        if reconstructed_next is None:
            raise GateError("CASE_TERMINATED", f"前驱链已终结（当前态 {cur_state}），无法继续 advance")
        if disk_next != reconstructed_next:
            raise GateError(
                "BROKEN_STAGE_CHAIN",
                f"审查单磁盘 next_stage={disk_next!r} 与前驱记录重建结果 {reconstructed_next!r} 冲突，拒绝跳步推进",
            )
        if stage != reconstructed_next:
            raise GateError(
                "OUT_OF_ORDER_STAGE",
                f"禁止跳步或逆序审查：当前唯一允许推进的关口为 {reconstructed_next}，拒绝 {stage}",
            )

        payload = _read_json(Path(args.payload), label="关口见证文件")
        lean_audit = audit_lean_suite(
            Path(args.lean_dir) if args.lean_dir else None,
            run_lean=bool(args.run_lean),
            lean_cmd=args.lean_cmd or "",
            wsl_lean=bool(args.wsl_lean),
            profile=profile,
        )
        if args.run_lean and not lean_audit["ok"]:
            raise GateError(
                "LEAN_GATE_FAILED",
                f"已显式启用 --run-lean，但 Lean 反空真或内核编译门禁未通过（status={lean_audit['lean_compiler']['status']}, violations={lean_audit['violation_count']}）",
                {"lean_compiler": lean_audit["lean_compiler"], "violations": lean_audit["violations"]},
            )
        stage_witnesses = _verify_witness_list(
            payload.get("witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=bool(args.allow_probe_exec),
            context_label="witnesses",
        )
        zones = _validate_non_formalized_zones(payload)
        verdict, structured_issues, item_witnesses, eval_meta = _STAGE_EVALUATORS[stage](
            payload,
            project_root,
            lean_audit,
            allow_probe_exec=bool(args.allow_probe_exec),
        )
        all_witnesses = stage_witnesses + item_witnesses
        true_verified_count = sum(1 for w in all_witnesses if w.get("verified") is True)
        issues_text = [it["message"] for it in structured_issues]
        primary_kind = (
            max((it["kind"] for it in structured_issues), key=lambda k: ISSUE_KIND_PRIORITY.get(k, 0))
            if structured_issues
            else None
        )
        stage_digest = _compute_stage_digest(
            profile=profile,
            stage=stage,
            effective_claim_hash=str(case_doc.get("effective_claim_hash") or ""),
            prev_stage_digest=prev_digest,
            payload=payload,
            witnesses=all_witnesses,
            verdict=verdict,
            meta=eval_meta,
        )

        record = {
            "stage": stage,
            "evaluated_at": _now_utc_iso(),
            "verdict": verdict,
            "freshness_status": "CURRENT",
            "prev_stage_digest": prev_digest,
            "stage_digest": stage_digest,
            "primary_issue_kind": primary_kind,
            "issues": issues_text,
            "structured_issues": structured_issues,
            "payload": payload,
            "witnesses": all_witnesses,
            "non_formalized_zones": zones,
            "meta": eval_meta,
            "notes": str(payload.get("notes") or "").strip(),
        }
        case_doc["stage_records"][stage] = record
        case_doc["updated_at"] = _now_utc_iso()
        # 推进任何关口均清除未消费旧失活下游、单关复演证书与整单复演证书
        stage_idx = stages.index(stage)
        stage_certs = case_doc.get("stage_replay_certificates")
        if isinstance(stage_certs, dict):
            for later_st in stages[stage_idx:]:
                stage_certs.pop(later_st, None)
        for later_st in stages[stage_idx + 1 :]:
            case_doc["stage_records"].pop(later_st, None)
        case_doc["replay_certificate"] = None

        if verdict == "conflict":
            _append_conflict_history(case_doc, stage, structured_issues)
            if not case_doc.get("first_conflict_stage"):
                case_doc["first_conflict_stage"] = stage
            case_doc["verification_level"] = "STRUCTURE_ONLY"
            case_doc["t_assertion_level"] = "NO_T_ASSERTION"
            if stage in root_halt_stages:
                case_doc["current_state"] = "HALT_ROOT_CONFLICT"
                case_doc["root_conflict_stage"] = stage
                case_doc["next_stage"] = None
                case_doc["original_claim_status"] = "ILL_FORMED_ROOT" if primary_kind == "ILL_FORMED" else "UNSAT_ROOT_CONFLICT"
            elif stage == hold_stage:
                if primary_kind in ("ILL_FORMED", "UNSAT"):
                    case_doc["current_state"] = "HALT_CLOSURE_CONFLICT"
                    case_doc["next_stage"] = None
                    case_doc["original_claim_status"] = "UNSAT_CLOSURE_CONFLICT"
                else:
                    case_doc["current_state"] = hold_state
                    case_doc["next_stage"] = hold_stage
                    case_doc["original_claim_status"] = "UNDERDETERMINED_OR_OVERREACH_HOLD"
            else:
                case_doc["current_state"] = "HALT_VV_CONFLICT"
                case_doc["next_stage"] = None
                case_doc["original_claim_status"] = "VV_FAILED"
        else:
            if stage_idx + 1 < len(stages):
                case_doc["current_state"] = f"PASSED_{stage}"
                case_doc["next_stage"] = stages[stage_idx + 1]
                case_doc["original_claim_status"] = "PENDING"
            else:
                # 双正交轴：末关 advance 未经净室 packet_replay 封顶在 STRUCTURE_PASSED（O-B1 / B4）
                case_doc["verification_level"] = "STRUCTURE_ONLY"
                case_doc["t_assertion_level"] = "NO_T_ASSERTION"
                case_doc["current_state"] = "STRUCTURE_PASSED"
                case_doc["original_claim_status"] = "STRUCTURE_ONLY_UNVERIFIED"
                case_doc["next_stage"] = None

        _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")

    out = {
        "ok": verdict == "pass",
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "stage": stage,
        "verdict": verdict,
        "primary_issue_kind": primary_kind,
        "current_state": case_doc["current_state"],
        "verification_level": case_doc["verification_level"],
        "t_assertion_level": case_doc["t_assertion_level"],
        "original_claim_status": case_doc["original_claim_status"],
        "next_stage": case_doc["next_stage"],
        "first_conflict_stage": case_doc.get("first_conflict_stage"),
        "stage_digest": stage_digest,
        "issues": issues_text,
        "structured_issues": structured_issues,
        "verified_witness_count": true_verified_count,
        "total_witness_count": len(all_witnesses),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if verdict == "pass" else 2


def _build_child_case_doc(
    parent_doc: Dict[str, Any],
    parent_path: Path,
    *,
    action: str,
    new_claim_text: str,
    lean_dir: Optional[Path],
) -> Dict[str, Any]:
    profile = _case_profile(parent_doc)
    stages = PROFILE_STAGES[profile]
    now = _now_utc_iso()
    lean_audit = audit_lean_suite(lean_dir, run_lean=False, profile=profile)
    code_shas = compute_gate_code_sha256()

    parent_chain = list(parent_doc.get("effective_claim_chain") or [])
    new_sha = hashlib.sha256(new_claim_text.encode("utf-8")).hexdigest()
    child_chain = parent_chain + [{
        "seq": len(parent_chain),
        "kind": action,
        "claim": new_claim_text,
        "claim_sha256": new_sha,
        "recorded_at": now,
        "parent_case": str(parent_path.resolve()),
    }]
    eff_hash = canonical_json_sha256(child_chain)
    tag = "rebased" if action == "rebase_ontology" else "downgraded"
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "created_at": now,
        "updated_at": now,
        "project_root": parent_doc["project_root"],
        "init_baseline": parent_doc.get("init_baseline") or {},
        "gate_sha256": code_shas["gate_sha256"],
        "lean_driver_sha256": code_shas["lean_driver_sha256"],
        "subject": f"{parent_doc['subject']} [{tag}: {new_claim_text}]",
        "root_claim": parent_doc["root_claim"],
        "root_claim_sha256": parent_doc["root_claim_sha256"],
        "original_claim": parent_doc["root_claim"],
        "effective_claim": new_claim_text,
        "effective_claim_chain": child_chain,
        "effective_claim_hash": eff_hash,
        "original_claim_status": "ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED",
        "effective_claim_status": "PENDING",
        "verification_level": "STRUCTURE_ONLY",
        "t_assertion_level": "NO_T_ASSERTION",
        "rebased_from_case": str(parent_path.resolve()),
        "current_state": "INIT",
        "next_stage": stages[0],
        "root_conflict_stage": None,
        "first_conflict_stage": None,
        "lean_ast_gate_at_init": {
            "ast_gate_ok": lean_audit["ast_gate_ok"],
            "checked_files": lean_audit["checked_files"],
            "violation_count": lean_audit["violation_count"],
        },
        "stage_records": {},
        "replay_certificate": None,
        "replay_attempts": [],
        "immutable_conflict_history": list(parent_doc.get("immutable_conflict_history", [])),
        "conflict_history_count": len(parent_doc.get("immutable_conflict_history", [])),
        "resolutions": [],
        "pending_child_tx": None,
        "evolution_feedback": [],
    }


def _resume_pending_child_tx_if_matching(
    case_doc: Dict[str, Any],
    case_path: Path,
    *,
    action: str,
    target_path_raw: str,
    requested_new_claim: str,
    force: bool,
) -> Optional[Dict[str, Any]]:
    """重构/降格双写中断后的完整幂等续交（B06 清零）：支持前半段中断与后半段（子单已写但母单未清 tx）续交，并核对请求命题一致。"""
    tx = case_doc.get("pending_child_tx")
    if not isinstance(tx, dict) or tx.get("action") != action or not target_path_raw:
        return None
    child_path = _reject_links(Path(target_path_raw), label="子审查单")
    if str(child_path.resolve()) != str(tx.get("target_path") or ""):
        return None
    tx_child_doc = tx.get("child_doc")
    if not isinstance(tx_child_doc, dict):
        return None
    expected_claim = str(tx_child_doc.get("effective_claim") or "")
    if requested_new_claim and _normalize_claim_text(requested_new_claim) != _normalize_claim_text(expected_claim):
        raise GateError(
            "PENDING_TX_CLAIM_MISMATCH",
            f"存在未完成事务 pending_child_tx，但本次请求命题 {requested_new_claim!r} 与事务记录 {expected_claim!r} 不一致，拒绝错写子单",
        )

    tx_child_sha = canonical_json_sha256(tx_child_doc)
    already_materialized = False
    if child_path.exists():
        try:
            existing_doc = _read_json(child_path, label="已落盘子审查单")
            if canonical_json_sha256(existing_doc) == tx_child_sha:
                already_materialized = True
        except GateError:
            already_materialized = False
        if not already_materialized:
            if not force:
                raise GateError("REBASED_CASE_EXISTS", f"子审查单路径已存在且内容与未完成事务不一致（如需覆盖请显式传 --force）: {child_path}")
            _assert_target_not_locked_case(child_path, action_label=f"{action} 幂等续交")

    if not already_materialized:
        _atomic_write_text(child_path, json.dumps(tx_child_doc, ensure_ascii=False, indent=2) + "\n")
    case_doc["pending_child_tx"] = None
    case_doc["updated_at"] = _now_utc_iso()
    _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
    return {
        "ok": True,
        "action": action,
        "resumed_transaction": True,
        "child_already_materialized": already_materialized,
        "tx_id": tx.get("tx_id"),
        "current_state": case_doc["current_state"],
        "original_claim_status": case_doc["original_claim_status"],
        "rebased_case_file": str(child_path) if action == "rebase_ontology" else None,
        "downgraded_case_file": str(child_path) if action == "downgrade_claim" else None,
    }


def cmd_resolve(args: argparse.Namespace) -> int:
    case_path = _reject_links(Path(args.case), label="审查单路径")
    with case_file_lock(case_path):
        case_doc = _read_json(case_path, label="审查单")
        _verify_case_schema_version(case_doc)
        _verify_conflict_history_chain(case_doc)
        profile = _case_profile(case_doc)
        hold_stage = PROFILE_HOLD_STAGE[profile]
        hold_state = PROFILE_HOLD_STATE[profile]
        cur_state = str(case_doc.get("current_state") or "")
        action = str(args.action or "").strip()
        rationale = str(args.rationale or "").strip()
        if not rationale:
            raise GateError("INVALID_ARG", "resolve 必须提供非空 --rationale")

        # 若上次双写子单前半段或后半段中断导致母单仍留 pending_child_tx，允许同事务幂等续交（B06）
        if cur_state in {"REBASED", "DOWNGRADED_NEW_CLAIM"} and action in {"rebase_ontology", "downgrade_claim"}:
            target_arg = str(args.emit_rebased_case or "") if action == "rebase_ontology" else str(getattr(args, "emit_downgraded_case", "") or "")
            req_claim = str(args.new_ontology or "").strip() if action == "rebase_ontology" else str(args.new_claim or "").strip()
            resumed = _resume_pending_child_tx_if_matching(
                case_doc,
                case_path,
                action=action,
                target_path_raw=target_arg,
                requested_new_claim=req_claim,
                force=bool(args.force),
            )
            if resumed is not None:
                print(json.dumps(resumed, ensure_ascii=False, indent=2))
                return 0

        if action == "declare_undetermined":
            if cur_state != hold_state:
                raise GateError("INVALID_RESOLVE_STATE", f"declare_undetermined 仅允许在非吸收态 {hold_state} 调用，当前为 {cur_state}")
            hold_rec = (case_doc.get("stage_records") or {}).get(hold_stage) or {}
            hold_issues = hold_rec.get("structured_issues") or []
            hold_codes = {str(it.get("code") or "") for it in hold_issues}
            if hold_codes != {"MISSING_CLOSURE_BASIS"} or any(it.get("kind") != "UNDETERMINED" for it in hold_issues):
                raise GateError(
                    "INVALID_UNDETERMINED_EXIT",
                    f"declare_undetermined 仅允许在 {hold_stage} 仅含纯欠封闭未定（MISSING_CLOSURE_BASIS）时调用；当前问题码 {sorted(hold_codes)} 含越权或未判定认知态 unclassified，严禁伪装为一.4 语义未定",
                )
            entry = {
                "resolved_at": _now_utc_iso(),
                "action": "declare_undetermined",
                "rationale": rationale,
                "note": "依第一层一.4 诚实声明在当前前提集下语义未定，不冒充通过亦不误判为不可满足",
            }
            case_doc["resolutions"].append(entry)
            case_doc["current_state"] = "DECLARED_UNDETERMINED"
            case_doc["original_claim_status"] = "UNDETERMINED_EXPLICIT"
            case_doc["next_stage"] = None
            case_doc["updated_at"] = _now_utc_iso()
            _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({
                "ok": True,
                "action": action,
                "current_state": case_doc["current_state"],
                "original_claim_status": case_doc["original_claim_status"],
            }, ensure_ascii=False, indent=2))
            return 0

        if cur_state not in {"HALT_ROOT_CONFLICT", "HALT_CLOSURE_CONFLICT"}:
            raise GateError("INVALID_RESOLVE_STATE", f"rebase_ontology/downgrade_claim 仅允许在 HALT_ROOT_CONFLICT 或 HALT_CLOSURE_CONFLICT 调用，当前为 {cur_state}")

        if action == "rebase_ontology":
            new_ontology = str(args.new_ontology or "").strip()
            if not new_ontology:
                raise GateError("INVALID_ARG", "rebase_ontology 必须提供非空 --new-ontology")
            if _normalize_claim_text(new_ontology) in {
                _normalize_claim_text(str(case_doc.get("root_claim") or "")),
                _normalize_claim_text(str(case_doc.get("effective_claim") or "")),
            }:
                raise GateError("IDENTICAL_CLAIM_REJECTED", "rebase_ontology 的 --new-ontology 规范化后与原/现行宣称完全相同，拒绝原地复述")

            rebased_path: Optional[Path] = None
            rebased_doc: Optional[Dict[str, Any]] = None
            if args.emit_rebased_case:
                rebased_path = _reject_links(Path(args.emit_rebased_case), label="新建重构审查单")
                if rebased_path.resolve() == case_path.resolve():
                    raise GateError("SAME_CASE_PATH", "--emit-rebased-case 严禁与当前母审查单 --case 指向同一路径")
                if rebased_path.is_dir():
                    raise GateError("INVALID_TARGET_PATH", f"--emit-rebased-case 目标路径是目录而非文件: {rebased_path}")
                if rebased_path.parent.exists() and not rebased_path.parent.is_dir():
                    raise GateError("INVALID_TARGET_PATH", f"--emit-rebased-case 父路径不是目录: {rebased_path.parent}")
                if rebased_path.exists():
                    if not args.force:
                        raise GateError("REBASED_CASE_EXISTS", f"新建重构审查单路径已存在（如需覆盖请显式传 --force）: {rebased_path}")
                    _assert_target_not_locked_case(rebased_path, action_label="rebase_ontology --emit-rebased-case")
                rebased_doc = _build_child_case_doc(
                    case_doc,
                    case_path,
                    action="rebase_ontology",
                    new_claim_text=new_ontology,
                    lean_dir=Path(args.lean_dir) if args.lean_dir else None,
                )

            prior_status = str(case_doc.get("original_claim_status") or "")
            perm_status = "ILL_FORMED_PERMANENT_REBASED" if prior_status.startswith("ILL_FORMED") else "UNSAT_PERMANENT_REBASED"
            tx_id = uuid.uuid4().hex if rebased_path is not None else None
            entry = {
                "resolved_at": _now_utc_iso(),
                "action": "rebase_ontology",
                "new_ontology": new_ontology,
                "rationale": rationale,
                "original_claim_preserved_invalid": True,
                "tx_id": tx_id,
            }
            case_doc["resolutions"].append(entry)
            case_doc["current_state"] = "REBASED"
            case_doc["original_claim_status"] = perm_status
            case_doc["updated_at"] = _now_utc_iso()
            if rebased_path is not None and rebased_doc is not None:
                case_doc["pending_child_tx"] = {
                    "tx_id": tx_id,
                    "action": "rebase_ontology",
                    "target_path": str(rebased_path.resolve()),
                    "child_doc": rebased_doc,
                }
            _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")

            rebased_path_str: Optional[str] = None
            if rebased_path is not None and rebased_doc is not None:
                _atomic_write_text(rebased_path, json.dumps(rebased_doc, ensure_ascii=False, indent=2) + "\n")
                rebased_path_str = str(rebased_path)
                case_doc["pending_child_tx"] = None
                _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")

            print(json.dumps({
                "ok": True,
                "action": action,
                "current_state": case_doc["current_state"],
                "original_claim_status": case_doc["original_claim_status"],
                "rebased_case_file": rebased_path_str,
            }, ensure_ascii=False, indent=2))
            return 0

        if action == "downgrade_claim":
            new_claim = str(args.new_claim or "").strip()
            if not new_claim:
                raise GateError("INVALID_ARG", "downgrade_claim 必须提供非空 --new-claim（新概念与新命题声明）")
            if _normalize_claim_text(new_claim) in {
                _normalize_claim_text(str(case_doc.get("root_claim") or "")),
                _normalize_claim_text(str(case_doc.get("effective_claim") or "")),
            }:
                raise GateError("IDENTICAL_CLAIM_REJECTED", "downgrade_claim 的 --new-claim 规范化后与原/现行宣称完全相同，违背一.4 同一律回锁")
            stripped = [s.strip() for s in (args.stripped_claims or []) if s.strip()]
            nonconv = str(args.nonconvergence_tag or "").strip()
            if not stripped and not nonconv:
                raise GateError("MISSING_DOWNGRADE_SCOPE", "downgrade_claim 至少须提供 --stripped-claims 或 --nonconvergence-tag 之一以明示剥离范围")

            downgraded_path: Optional[Path] = None
            downgraded_doc: Optional[Dict[str, Any]] = None
            emit_dg = str(getattr(args, "emit_downgraded_case", "") or "").strip()
            if emit_dg:
                downgraded_path = _reject_links(Path(emit_dg), label="新建降格审查单")
                if downgraded_path.resolve() == case_path.resolve():
                    raise GateError("SAME_CASE_PATH", "--emit-downgraded-case 严禁与当前母审查单 --case 指向同一路径")
                if downgraded_path.is_dir():
                    raise GateError("INVALID_TARGET_PATH", f"--emit-downgraded-case 目标路径是目录而非文件: {downgraded_path}")
                if downgraded_path.exists():
                    if not args.force:
                        raise GateError("REBASED_CASE_EXISTS", f"新建降格审查单路径已存在（如需覆盖请显式传 --force）: {downgraded_path}")
                    _assert_target_not_locked_case(downgraded_path, action_label="downgrade_claim --emit-downgraded-case")
                downgraded_doc = _build_child_case_doc(
                    case_doc,
                    case_path,
                    action="downgrade_claim",
                    new_claim_text=new_claim,
                    lean_dir=Path(args.lean_dir) if args.lean_dir else None,
                )

            prior_status = str(case_doc.get("original_claim_status") or "")
            perm_status = "ILL_FORMED_PERMANENT_DOWNGRADED" if prior_status.startswith("ILL_FORMED") else "UNSAT_PERMANENT_DOWNGRADED"
            tx_id = uuid.uuid4().hex if downgraded_path is not None else None
            entry = {
                "resolved_at": _now_utc_iso(),
                "action": "downgrade_claim",
                "new_claim": new_claim,
                "stripped_claims": stripped,
                "nonconvergence_tag": nonconv,
                "rationale": rationale,
                "tx_id": tx_id,
                "identity_relock_note": "依第一层一.4 同一律回锁，新概念与新命题不改变原语义下原命题不成立的既定事实",
            }
            case_doc["resolutions"].append(entry)
            case_doc["current_state"] = "DOWNGRADED_NEW_CLAIM"
            case_doc["original_claim_status"] = perm_status
            case_doc["updated_at"] = _now_utc_iso()
            if downgraded_path is not None and downgraded_doc is not None:
                case_doc["pending_child_tx"] = {
                    "tx_id": tx_id,
                    "action": "downgrade_claim",
                    "target_path": str(downgraded_path.resolve()),
                    "child_doc": downgraded_doc,
                }
            _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")

            dg_path_str: Optional[str] = None
            if downgraded_path is not None and downgraded_doc is not None:
                _atomic_write_text(downgraded_path, json.dumps(downgraded_doc, ensure_ascii=False, indent=2) + "\n")
                dg_path_str = str(downgraded_path)
                case_doc["pending_child_tx"] = None
                _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")

            print(json.dumps({
                "ok": True,
                "action": action,
                "current_state": case_doc["current_state"],
                "original_claim_status": case_doc["original_claim_status"],
                "new_claim": new_claim,
                "downgraded_case_file": dg_path_str,
            }, ensure_ascii=False, indent=2))
            return 0

        raise GateError("UNKNOWN_ACTION", f"仅允许 rebase_ontology、downgrade_claim 或 declare_undetermined，收到: {action!r}")


_FEEDBACK_SENSITIVE_PATTERNS = (
    ("宿主机用户路径", re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+")),
    ("POSIX用户路径", re.compile(r"/(?:Users|home)/[^/\s]+")),
    ("私网或回环IP", re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|127\.0\.0\.1)\b")),
)

_LAYER1_PHYSICS_POLLUTION_RE = re.compile(
    r"(?i)\b(?:Rankine-Hugoniot|Onsager|Navier-Stokes|Galilean|Reynolds|Knudsen|Mach|Nusselt|Prandtl|Peclet|Riemann|CFD|Boltzmann)\b"
    r"|激波管|连续介质力学|雷诺数|马赫数|努塞尔数|普朗特数|克努森数|纳维-斯托克斯|欧拉方程|昂萨格|伽利略|兰金-雨果尼奥|耗散不等式|本构关系"
)

_PLACEHOLDER_COUNTEREXAMPLES = frozenset({"待补", "待填", "待定", "无", "占位", "todo", "tbd", "none", "n/a", "placeholder"})


def cmd_feedback(args: argparse.Namespace) -> int:
    """登记反例/演进反哺条目（防泄漏且报错脱敏、防层 1 物理词倒灌、防评估集泄漏与层 1 逻辑反例准入守卫，B11 清零）。"""
    case_path = _reject_links(Path(args.case), label="审查单路径")
    with case_file_lock(case_path):
        case_doc = _read_json(case_path, label="审查单")
        _verify_case_schema_version(case_doc)
        _verify_conflict_history_chain(case_doc)
        layer = str(args.target_layer or "").strip()
        summary = str(args.summary or "").strip()
        rationale = str(args.rationale or "").strip()
        violated_axiom = str(getattr(args, "violated_axiom", "") or "").strip()
        proposed_artifact = str(args.proposed_artifact or "").strip()
        logic_ce = str(getattr(args, "logic_counterexample", "") or "").strip()
        preconditions = [s.strip() for s in (getattr(args, "preconditions", None) or []) if s.strip()]
        contraindications = [s.strip() for s in (getattr(args, "contraindications", None) or []) if s.strip()]

        if layer not in FEEDBACK_LAYERS:
            raise GateError("INVALID_FEEDBACK_SCHEMA", f"--target-layer 须属于 {sorted(FEEDBACK_LAYERS)}")
        if not summary or not rationale or not violated_axiom:
            raise GateError("INVALID_FEEDBACK_SCHEMA", "feedback 必须提供非空 --violated-axiom、--summary 与 --rationale")
        if layer == "1":
            compact_ce = re.sub(r"\s+", "", logic_ce).lower()
            if not logic_ce or compact_ce in _PLACEHOLDER_COUNTEREXAMPLES or len(compact_ce) < 8:
                raise GateError(
                    "INVALID_FEEDBACK_SCHEMA",
                    "第一层处于封板冻结态（layer1-logic-epistemology.md#L3），--target-layer 1 必须提供实质非占位的 --logic-counterexample（有限反模型或 Lean 逻辑反例依据，长度不少于 8 字符）",
                )

        all_texts = [summary, rationale, violated_axiom, proposed_artifact, logic_ce, *preconditions, *contraindications]
        joined = "\n".join(all_texts)

        for kind_label, pat in _FEEDBACK_SENSITIVE_PATTERNS:
            if pat.search(joined):
                # B11 清零：错误消息仅报违规类别，绝不回显捕获的真实宿主机路径或私网 IP
                raise GateError("FEEDBACK_SENSITIVE_PATH_LEAK", f"feedback 禁止写入敏感信息（已拦截类型：[已脱敏:{kind_label}]）")

        if layer == "1":
            m_phy = _LAYER1_PHYSICS_POLLUTION_RE.search(joined)
            if m_phy:
                raise GateError(
                    "LAYER_UPWARD_POLLUTION",
                    f"禁止向第一层纯逻辑底座倒灌下层物理/流体专有术语: {m_phy.group(0)!r}",
                )

        eval_names: set[str] = set()
        for rec in (case_doc.get("stage_records") or {}).values():
            if not isinstance(rec, dict):
                continue
            meta = rec.get("meta") or {}
            for k in ("validation_datasets", "evaluation_inputs"):
                for ds in meta.get(k) or []:
                    if isinstance(ds, str) and ds.strip():
                        eval_names.add(ds.strip())
        for ds in sorted(eval_names):
            if ds in joined:
                # B11 清零：不回显真实评估集标识
                raise GateError("FEEDBACK_EVAL_DATASET_LEAK", "feedback 禁止直接泄漏当前审查单的评估集标识（[已脱敏:评估集标识]）")

        item = {
            "recorded_at": _now_utc_iso(),
            "target_layer": layer,
            "violated_axiom_ref": violated_axiom,
            "summary": summary,
            "rationale": rationale,
            "preconditions": preconditions,
            "contraindications": contraindications,
            "logic_counterexample": logic_ce,
            "proposed_artifact": proposed_artifact,
            "reference_only": True,
            "evidence_ceiling": "veto_only",
        }
        case_doc.setdefault("evolution_feedback", []).append(item)
        case_doc["updated_at"] = _now_utc_iso()
        _atomic_write_text(case_path, json.dumps(case_doc, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "feedback_count": len(case_doc["evolution_feedback"]), "latest": item}, ensure_ascii=False, indent=2))
    return 0


def _render_report_markdown(case_doc: Dict[str, Any], lean_audit: Dict[str, Any]) -> str:
    profile = _case_profile(case_doc)
    stages = PROFILE_STAGES[profile]
    cur_state = str(case_doc.get("current_state") or "")
    v_level = str(case_doc.get("verification_level") or "STRUCTURE_ONLY")
    t_level = str(case_doc.get("t_assertion_level") or "NO_T_ASSERTION")
    claim_chain = case_doc.get("effective_claim_chain") or []
    is_downgraded_or_rebased = len(claim_chain) > 1

    lines = [
        f"# 硬公理状态机审查报告：{case_doc.get('subject', '')}",
        "",
    ]
    if is_downgraded_or_rebased:
        lines += [
            f"> **一.4 同一律回锁提示**：原命题「{case_doc.get('root_claim', '')}」在原语义下不可满足；本报告当前仅就有效命题「{case_doc.get('effective_claim', '')}」评定，绝不冒充原宣称成立。",
            "",
        ]
    if cur_state == "STRUCTURE_PASSED":
        lines += [
            "> **核验边界声明（仅结构门禁通过）**：本审查单当前处于 `STRUCTURE_PASSED`（`STRUCTURE_ONLY_UNVERIFIED`），尚未完成异源净室 `packet_replay` 复演与终态实证闭环，严禁当作终审通过。",
            "",
        ]
    elif cur_state == "VERIFIED_NO_T":
        lines += [
            "> **核验边界声明（独立复演通过 · 无全称 $T$ 断言）**：本审查单已通过独立净室复演与实测验签（`VERIFIED_NO_T_ASSERTION`），但因仅具有限样本否决存活证据、非充分退出支或内生目标，诚实不对目标 $T$ 下全称肯定断言。",
            "",
        ]
    elif cur_state == "VERIFIED_T_ASSERTED":
        lines += [
            "> **终审通过声明（`PASSED_ALL_GATES`）**：本审查单已完成全链实证、独立净室复演及充分证书核验（以下显式列出全部桥接前提 $B$）。",
            "",
        ]

    lines += [
        f"- **公理档位（profile）**：`{profile}`（Schema v{case_doc.get('schema_version')}）",
        f"- **原始不可变命题（root_claim）**：{case_doc.get('root_claim', case_doc.get('original_claim', ''))}",
        f"- **现行有效命题（effective_claim）**：{case_doc.get('effective_claim', case_doc.get('original_claim', ''))}",
        f"- **核验完备度轴**：`{v_level}`｜**目标断言轴**：`{t_level}`",
        f"- **原命题状态**：`{case_doc.get('original_claim_status', '')}`｜**现行有效命题状态**：`{case_doc.get('effective_claim_status', case_doc.get('original_claim_status', ''))}`",
        f"- **状态机当前态**：`{cur_state}`",
        f"- **根冲突锁定关口**：`{case_doc.get('root_conflict_stage') or '无'}`",
        f"- **首次冲突关口**：`{case_doc.get('first_conflict_stage') or '无'}`",
        f"- **Lean 4 AST 反空真门禁**：`{'通过' if lean_audit.get('ast_gate_ok') else '未通过'}`（内核编译状态：`{lean_audit.get('lean_compiler', {}).get('status', 'not_requested')}`，版本：`{lean_audit.get('lean_compiler', {}).get('version') or '未运行'}`）",
        "",
        f"## 单向短路轨迹（profile=`{profile}`，目的 G 下根层优先调度）",
        "",
        "| 关口 | 判定 | 新鲜度 | 主问题类型 | 已核验/总见证 | 问题摘要 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    records = case_doc.get("stage_records") or {}
    bridge_premises: List[str] = []
    for stage in stages:
        rec = records.get(stage)
        if not rec:
            lines.append(f"| `{stage}` | `NOT_REACHED` | — | — | 0/0 | 未到达 |")
        else:
            wits = rec.get("witnesses") or []
            v_cnt = sum(1 for w in wits if w.get("verified") is True)
            issue_str = "；".join(rec.get("issues") or []) or "无冲突"
            pkind = rec.get("primary_issue_kind") or "—"
            fresh_tag = rec.get("freshness_status") or "CURRENT"
            lines.append(f"| `{stage}` | `{rec.get('verdict')}` | `{fresh_tag}` | `{pkind}` | {v_cnt}/{len(wits)} | {issue_str} |")
            meta = rec.get("meta") or {}
            for bp in meta.get("bridge_premises") or []:
                if bp not in bridge_premises:
                    bridge_premises.append(bp)

    cert = case_doc.get("replay_certificate")
    lines += ["", "## 独立净室复演证书（第 N+1 关）", ""]
    if isinstance(cert, dict):
        lines.append(f"- 状态：`{cert.get('status')}`｜包摘要：`{cert.get('packet_sha256')}`｜复演端：`{cert.get('replayer_engine')}`（会话 `{cert.get('replayer_session_id')}`）")
    else:
        lines.append("- 尚未登记独立净室 `packet_replay` 复演证书。")

    if bridge_premises or cur_state in VERIFIED_STATES:
        lines += ["", "## 显式桥接前提集 B（诚实边界披露）", ""]
        for bp in bridge_premises:
            lines.append(f"- 业务桥接前提：{bp}")
        lines.append("- 基础设施留痕前提：假设外部无头调度账本（`cae_harness` 票据与对账记录）及宿主机工具链未被特权进程事后篡改（防篡改留痕 / tamper-evident）。")

    if case_doc.get("resolutions"):
        lines += ["", "## 一.4 同一律回锁与未定处置记录", ""]
        for res in case_doc["resolutions"]:
            lines.append(f"- `{res.get('action')}`（{res.get('resolved_at')}）：{res.get('rationale')}")
    if case_doc.get("evolution_feedback"):
        lines += ["", "## 实战反哺状态机建议", ""]
        for fb in case_doc["evolution_feedback"]:
            lines.append(f"- 目标层 `{fb.get('target_layer')}`：{fb.get('summary')}（依据：{fb.get('rationale')}）")
    lines.append("")
    return "\n".join(lines)


def cmd_report(args: argparse.Namespace) -> int:
    case_path = _reject_links(Path(args.case), label="审查单路径")
    with case_file_lock(case_path):
        case_doc = _read_json(case_path, label="审查单")
        _verify_case_schema_version(case_doc)
        _verify_conflict_history_chain(case_doc)
        profile = _case_profile(case_doc)
        project_root = _resolve_case_project_root(case_doc, getattr(args, "project_root", ""))
        fresh_rep = reconcile_case_freshness(case_doc, project_root, case_path=case_path, persist_on_invalidation=True)

    if not fresh_rep["fresh"]:
        raise GateError(
            str(fresh_rep["error_code"]),
            f"生成报告前现场重算发现锚定文件或前提漂移（起始关口: {fresh_rep['invalidated_from_stage']}，原因: {fresh_rep['reason']}）",
            {"invalidated_from_stage": fresh_rep["invalidated_from_stage"], "drifts": fresh_rep["drifts"]},
        )

    lean_audit = audit_lean_suite(
        Path(args.lean_dir) if args.lean_dir else None,
        run_lean=bool(args.run_lean),
        lean_cmd=args.lean_cmd or "",
        wsl_lean=bool(args.wsl_lean),
        profile=profile,
    )
    if args.run_lean and not lean_audit["ok"]:
        raise GateError(
            "LEAN_GATE_FAILED",
            f"已显式启用 --run-lean，但 Lean 反空真或内核编译门禁未通过（status={lean_audit['lean_compiler']['status']}, violations={lean_audit['violation_count']}）",
            {"lean_compiler": lean_audit["lean_compiler"], "violations": lean_audit["violations"]},
        )

    cur_state = str(case_doc.get("current_state") or "")
    require_verified = bool(getattr(args, "require_verified", False))
    require_t_asserted = bool(getattr(args, "require_t_asserted", False))
    allow_probe_exec = bool(getattr(args, "allow_probe_exec", False))

    if require_verified or require_t_asserted:
        # B04 清零：绝不轻信磁盘 current_state，强制重算整条前驱摘要链并核验双轴与复演证书完整性
        stages = PROFILE_STAGES[profile]
        reconstructed_next, _ = _reconstruct_expected_next_stage(case_doc)
        records = case_doc.get("stage_records") or {}
        all_stages_passed = (
            reconstructed_next is None
            and all(
                isinstance(records.get(s), dict)
                and records[s].get("verdict") == "pass"
                and records[s].get("freshness_status") == "CURRENT"
                for s in stages
            )
        )
        if not all_stages_passed:
            raise GateError("BROKEN_STAGE_CHAIN", "审查单前驱关口链未完整通过或存在篡改/失活，拒绝通过严格验收门禁")
        if cur_state == "STRUCTURE_PASSED":
            raise GateError(
                "UNVERIFIED_STRUCTURE_ONLY",
                "当前审查单仅通过结构门禁（STRUCTURE_PASSED / STRUCTURE_ONLY_UNVERIFIED），未通过独立净室复演与终态实证，拒绝通过 --require-verified 门禁",
            )
        if cur_state not in VERIFIED_STATES or case_doc.get("verification_level") != "VERIFIED":
            raise GateError(
                "CASE_NOT_VERIFIED",
                f"当前审查单处于 {cur_state}（verification_level={case_doc.get('verification_level')!r}），未达 VERIFIED 终态，拒绝通过严格验收门禁",
            )
        cert = case_doc.get("replay_certificate")
        cur_code_shas = compute_gate_code_sha256()
        cur_artifacts_hash = canonical_json_sha256(_collect_all_stage_artifact_hashes(case_doc))
        if (
            not isinstance(cert, dict)
            or cert.get("status") != "REPLAY_VERIFIED"
            or cert.get("effective_claim_hash") != case_doc.get("effective_claim_hash")
            or cert.get("artifacts_union_sha256") != cur_artifacts_hash
            or cert.get("gate_sha256") != cur_code_shas["gate_sha256"]
            or cert.get("lean_driver_sha256") != cur_code_shas["lean_driver_sha256"]
        ):
            raise GateError(
                "CASE_NOT_VERIFIED",
                "缺少有效且摘要匹配的独立净室复演证书（replay_certificate），禁止伪造 current_state 通过严格验收门禁",
            )
        if require_t_asserted:
            last_meta = (records[stages[-1]].get("meta") or {})
            if (
                cur_state != "VERIFIED_T_ASSERTED"
                or case_doc.get("t_assertion_level") != "T_ASSERTED"
                or last_meta.get("allow_t_assertion") is not True
                or cert.get("packet_fitting_suspected") is True
            ):
                raise GateError(
                    "T_ASSERTION_NOT_GRANTED",
                    f"当前审查单未满足 VERIFIED_T_ASSERTED 全部条件（state={cur_state}, t_level={case_doc.get('t_assertion_level')!r}）",
                )
        # 若显式传 --allow-probe-exec，在终态哈希下现场重跑本地已锁定的 execute:true 探针（O-B2 / A13）
        if allow_probe_exec:
            for st in stages:
                rec = records.get(st) or {}
                for w in rec.get("witnesses") or []:
                    if w.get("type") == "probe_exec" and w.get("execute") is True:
                        rerun_live_probe(w, project_root)

    md_text = _render_report_markdown(case_doc, lean_audit)
    md_path_str: Optional[str] = None
    if args.output_md:
        md_target = _reject_links(Path(args.output_md), label="报告输出路径")
        if md_target.resolve() == case_path.resolve():
            raise GateError("SAME_CASE_PATH", "report --output-md 严禁覆盖母审查单自身")
        if md_target.is_dir():
            raise GateError("INVALID_TARGET_PATH", f"report --output-md 目标路径是目录而非文件: {md_target}")
        if md_target.exists():
            try:
                maybe_case = _read_json(md_target, label="报告目标检测")
            except GateError:
                maybe_case = None
            if isinstance(maybe_case, dict) and ("current_state" in maybe_case and "immutable_conflict_history" in maybe_case):
                raise GateError("IMMUTABLE_HISTORY_LOCKED", f"report --output-md 严禁覆写既有审查单 JSON 文件: {md_target}")
        md_dest = _atomic_write_text(md_target, md_text)
        md_path_str = str(md_dest)

    print(json.dumps({
        "ok": lean_audit["ok"],
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "case_file": str(case_path),
        "current_state": cur_state,
        "verification_level": case_doc.get("verification_level"),
        "t_assertion_level": case_doc.get("t_assertion_level"),
        "original_claim_status": case_doc.get("original_claim_status"),
        "root_claim": case_doc.get("root_claim"),
        "effective_claim": case_doc.get("effective_claim"),
        "root_conflict_stage": case_doc.get("root_conflict_stage"),
        "first_conflict_stage": case_doc.get("first_conflict_stage"),
        "freshness": "CURRENT",
        "lean_ast_gate_ok": lean_audit["ast_gate_ok"],
        "lean_compiler_status": lean_audit["lean_compiler"]["status"],
        "lean_compiler_version": lean_audit["lean_compiler"].get("version"),
        "output_md": md_path_str,
        "feedback_count": len(case_doc.get("evolution_feedback") or []),
    }, ensure_ascii=False, indent=2))
    return 0 if lean_audit["ok"] else 2
