#!/usr/bin/env python3
"""fleet_state.py - fleet-evolve 维护流程的本机审批与重启验证状态锁（仅依赖 Python 标准库）。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _side() -> str:
    return "windows" if os.name == "nt" else "posix"


def _default_state_dir() -> Path:
    override = os.environ.get("CAE_FLEET_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return (base / "cae-harness" / "fleet-evolve").expanduser().resolve()


def _overlaps(left: Path, right: Path) -> bool:
    lp, rp = left.resolve(), right.resolve()
    return lp == rp or lp in rp.parents or rp in lp.parents


def _validated_state_file(repo_root: Path) -> Path:
    state_dir = _default_state_dir()
    home = Path.home().expanduser().resolve()
    raw_forbidden = (
        repo_root,
        home / ".gemini" / "config" / "plugins" / "user-guard",
        Path(os.environ.get("CODEX_HOME") or (home / ".codex")),
        Path(os.environ.get("CLAUDE_CONFIG_DIR") or (home / ".claude")),
        Path(os.environ.get("DSH_HOME") or (home / ".dsh")),
        home / ".cc-switch",
    )
    forbidden = tuple(item.expanduser().resolve() for item in raw_forbidden)
    if any(_overlaps(state_dir, root) for root in forbidden):
        raise ValueError(f"fleet-evolve state dir overlaps repo or client config root: {state_dir}")
    return state_dir / f"state.{_side()}.json"


class NotUpstreamRepoError(ValueError):
    """当前目录并非上游配置维护仓库，且未显式传入有效的 --repo。"""


def _is_upstream_repo(candidate: Path) -> bool:
    return (candidate / "install.py").is_file() and (candidate / "template" / "user-guard" / "plugin.json").is_file()


def _resolve_repo_root(raw_repo: str | None) -> Path:
    if raw_repo:
        root = Path(raw_repo).expanduser().resolve()
        if not _is_upstream_repo(root):
            raise NotUpstreamRepoError(
                f"--repo 指向的路径不是上游配置维护仓库（缺少 install.py 或 template/user-guard/plugin.json）: {root}"
            )
        return root
    for seed in (Path.cwd().resolve(), Path(__file__).resolve().parent):
        for candidate in (seed, *seed.parents):
            if _is_upstream_repo(candidate):
                return candidate
    raise NotUpstreamRepoError(
        "当前工作区属于通用课题项目（general_project），fleet-evolve 与 fleet_state.py 仅用于维护上游配置仓库；如需跨目录查看请显式传入 --repo <path>"
    )


def _run_repo_grounding_gate(repo_root: Path) -> Dict[str, Any]:
    try:
        harness_py = repo_root / "template" / "user-guard" / "skills" / "cae-harness" / "scripts" / "cae_harness.py"
        if not harness_py.is_file():
            raise FileNotFoundError("cae_harness.py is missing")
        import importlib.util
        spec = importlib.util.spec_from_file_location("_cae_harness_grounding", harness_py)
        if spec is None or spec.loader is None:
            raise ImportError("cae_harness.py has no import spec or loader")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.audit_doc_grounding(repo_root, repo_root)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ValueError("grounding gate returned an invalid result")
        return result
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _default_ttl_seconds(repo_root: Path | None = None) -> int:
    candidates: list[Path] = []
    env_profile = os.environ.get("CAE_FLEET_PROFILE", "").strip()
    if env_profile:
        candidates.append(Path(env_profile).expanduser())
    if repo_root is not None:
        candidates.append(repo_root / "template" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json")
    candidates.append(Path(__file__).resolve().parents[2] / "cae-harness" / "fleet_profile.json")
    for candidate in candidates:
        try:
            if candidate.is_file():
                raw = json.loads(candidate.read_text(encoding="utf-8-sig"))
                hd = ((raw.get("runtime_tunables") or {}).get("headless_dispatch") or {}) if isinstance(raw, dict) else {}
                val = hd.get("fleet_state_ttl_sec") if isinstance(hd, dict) else None
                if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                    return val
        except (OSError, ValueError):
            continue
    return 86400


def _repo_meta(repo_root: Path) -> Dict[str, str]:
    plugin_json = repo_root / "template" / "user-guard" / "plugin.json"
    skill_md = repo_root / "template" / "user-guard" / "skills" / "fleet-evolve" / "SKILL.md"
    profile_json = repo_root / "template" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json"
    plugin_version = json.loads(plugin_json.read_text(encoding="utf-8-sig")).get("version", "") if plugin_json.is_file() else ""
    skill_sha256 = hashlib.sha256(skill_md.read_bytes()).hexdigest() if skill_md.is_file() else ""
    profile_sha256 = hashlib.sha256(profile_json.read_bytes()).hexdigest() if profile_json.is_file() else ""
    core_digest = hashlib.sha256()
    for rel in (
        "install.py",
        "template/user-guard/plugin.json",
        "template/user-guard/skills/fleet-evolve/SKILL.md",
        "template/user-guard/skills/cae-harness/fleet_profile.json",
        "template/user-guard/skills/cae-harness/scripts/cae_harness.py",
    ):
        target = repo_root / rel
        core_digest.update(rel.encode("utf-8") + b"\0")
        if target.is_file():
            core_digest.update(hashlib.sha256(target.read_bytes()).digest())
    commit = ""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, encoding="utf-8", timeout=5
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "repo_root": str(repo_root),
        "repo_commit": commit,
        "plugin_version": str(plugin_version),
        "skill_sha256": skill_sha256,
        "profile_sha256": profile_sha256,
        "core_sha256": core_digest.hexdigest(),
    }


def _is_aligned_with_repo(saved: Dict[str, Any], meta: Dict[str, str]) -> bool:
    return bool(
        saved
        and saved.get("execution_side") == _side()
        and saved.get("plugin_version") == meta["plugin_version"]
        and saved.get("skill_sha256") == meta["skill_sha256"]
        and (not saved.get("profile_sha256") or saved.get("profile_sha256") == meta["profile_sha256"])
        and (not saved.get("repo_root") or saved.get("repo_root") == meta["repo_root"])
        and (not saved.get("core_sha256") or saved.get("core_sha256") == meta["core_sha256"])
        and (not meta["repo_commit"] or saved.get("repo_commit") == meta["repo_commit"])
    )


@contextmanager
def _state_lock(state_file: Path) -> Iterator[None]:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        state_file.parent.chmod(0o700)
    lock_path = state_file.with_suffix(state_file.suffix + ".lock")
    with open(lock_path, "a+b") as handle:
        if os.name == "nt":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.parent.chmod(0o700)
    fd, tmp = tempfile.mkstemp(prefix=".fleet-state-", suffix=".json", dir=str(path.parent))
    try:
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _int_field(data: Dict[str, Any], key: str, default: int, *, positive: bool = False) -> int:
    val = data.get(key)
    if val is None:
        return default
    if isinstance(val, bool) or not isinstance(val, (int, str)):
        raise ValueError(f"invalid integer field: {key}")
    try:
        parsed = int(val)
    except ValueError as exc:
        raise ValueError(f"invalid integer field: {key}") from exc
    if positive and parsed <= 0:
        raise ValueError(f"non-positive integer field: {key}")
    return parsed


def _load_state(path: Path, default_ttl: int = 86400) -> Dict[str, Any]:
    raw = ""
    for attempt in range(3):
        try:
            raw = path.read_text(encoding="utf-8-sig")
            break
        except FileNotFoundError:
            return {}
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.02)
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("state file root must be a JSON object")
    if loaded:
        _int_field(loaded, "ttl_seconds", default_ttl, positive=True)
        _int_field(loaded, "updated_at_epoch", 0)
        _int_field(loaded, "last_verified_epoch", 0)
    return loaded


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Manage fleet-evolve two-pass bootstrap state lock")
    parser.add_argument("--repo", default=None, help="Upstream repository root (auto-detected from cwd/script if omitted)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Read current bootstrap or approval phase without modifying disk")

    p_prop = sub.add_parser("propose", help="Record candidate client-model table and pause for user sign-off")
    p_prop.add_argument("--session-id", required=True)
    p_prop.add_argument("--summary", required=True, help="Brief summary of proposed role/model changes requiring user approval")
    p_prop.add_argument("--ttl", type=int, default=None)

    p_app = sub.add_parser("approve", help="Record explicit user sign-off on the proposed client-model table")
    p_app.add_argument("--session-id", required=True)

    p_rec = sub.add_parser("record-pass1", help="Record Pass 1 completion awaiting Master reload")
    p_rec.add_argument("--session-id", required=True)
    p_rec.add_argument("--target-master", required=True)
    p_rec.add_argument("--ttl", type=int, default=None)
    p_rec.add_argument("--force", action="store_true", help="Overwrite even if an active Pass 2 session has claimed the lock")

    p_ver = sub.add_parser("verify-pass2", help="Validate Pass 2 re-entry from a reloaded Master session")
    p_ver.add_argument("--session-id", required=True)

    p_clr = sub.add_parser("clear", help="Reset bootstrap state lock to IDLE (optionally recording last_verified metadata)")
    p_clr.add_argument("--verified", action="store_true")
    p_clr.add_argument("--force", action="store_true", help="Allow clearing an active user approval proposal")
    p_clr.add_argument("--readback-done", action="store_true", help="Confirm cold full-file readback pass completed")

    args = parser.parse_args(argv)
    try:
        repo_root = _resolve_repo_root(args.repo)
    except NotUpstreamRepoError as exc:
        print(json.dumps({
            "ok": False,
            "workspace_mode": "general_project",
            "reason": "not_upstream_config_repo",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    try:
        default_ttl = _default_ttl_seconds(repo_root)
        if args.cmd in ("propose", "record-pass1") and args.ttl is None:
            args.ttl = default_ttl
        state_file = _validated_state_file(repo_root)
        meta = _repo_meta(repo_root)
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "reason": "invalid_state_or_repo", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    now = int(time.time())

    if args.cmd == "status":
        corrupt = False
        try:
            saved = _load_state(state_file, default_ttl)
        except (ValueError, OSError):
            saved = {}
            corrupt = True
        raw_phase = "CORRUPT" if corrupt else str(saved.get("phase") or "IDLE")
        ttl = _int_field(saved, "ttl_seconds", default_ttl, positive=True) if not corrupt else default_ttl
        updated = _int_field(saved, "updated_at_epoch", 0) if not corrupt else 0
        side_mismatch = bool(saved and saved.get("execution_side") != _side())
        active_phases = ("AWAITING_MASTER_RELOAD", "AWAITING_USER_APPROVAL")
        expired = bool(raw_phase in active_phases and (now - updated > ttl))
        aligned = _is_aligned_with_repo(saved, meta)
        stale_reason = "corrupt_state" if corrupt else None
        if raw_phase in active_phases:
            if side_mismatch:
                stale_reason = "side_mismatch"
            elif expired:
                stale_reason = "expired_ttl"
            elif not aligned:
                stale_reason = "repo_drift"
        effective_phase = raw_phase if (raw_phase in active_phases and stale_reason is None) else "IDLE"
        print(json.dumps({
            "workspace_mode": "upstream_config_repo",
            "phase": effective_phase,
            "raw_phase": raw_phase,
            "execution_side": _side(),
            "expired": expired,
            "aligned_with_repo": aligned,
            "stale_reason": stale_reason,
            "state_file": str(state_file),
            "current_repo": meta,
            "saved": saved or None,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "propose":
        sid = args.session_id.strip()
        summary = args.summary.strip()
        if not sid or not summary or args.ttl <= 0:
            print(json.dumps({"ok": False, "reason": "invalid_arguments", "error": "session-id and summary must be non-empty and ttl must be positive"}, ensure_ascii=False, indent=2))
            return 2
        payload = {
            "schema_version": 1,
            "phase": "AWAITING_USER_APPROVAL",
            "execution_side": _side(),
            "proposing_session_id": sid,
            "proposal_summary": summary,
            "updated_at_epoch": now,
            "ttl_seconds": args.ttl,
            **meta,
        }
        with _state_lock(state_file):
            _atomic_write_json(state_file, payload)
        print(json.dumps({"ok": True, "user_approval_required": True, "state_file": str(state_file), "state": payload}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "approve":
        sid = args.session_id.strip()
        if not sid:
            print(json.dumps({"ok": False, "reason": "invalid_arguments", "error": "session-id must be non-empty"}, ensure_ascii=False, indent=2))
            return 2
        with _state_lock(state_file):
            try:
                saved = _load_state(state_file)
            except ValueError:
                print(json.dumps({"ok": False, "reason": "corrupt_state"}, ensure_ascii=False, indent=2))
                return 2
            if saved.get("phase") != "AWAITING_USER_APPROVAL":
                print(json.dumps({"ok": False, "reason": "no_pending_proposal", "saved": saved or None}, ensure_ascii=False, indent=2))
                return 2
            if saved.get("execution_side") != _side():
                print(json.dumps({"ok": False, "reason": "side_mismatch", "saved": saved}, ensure_ascii=False, indent=2))
                return 2
            if not _is_aligned_with_repo(saved, meta):
                print(json.dumps({"ok": False, "reason": "repo_or_profile_changed_since_propose", "saved": saved, "current": meta}, ensure_ascii=False, indent=2))
                return 2
            if now - _int_field(saved, "updated_at_epoch", 0) > _int_field(saved, "ttl_seconds", 86400, positive=True):
                print(json.dumps({"ok": False, "reason": "expired_ttl", "saved": saved}, ensure_ascii=False, indent=2))
                return 2
            payload = {
                "schema_version": 1,
                "phase": "IDLE",
                "user_approved": True,
                "approved_by_session_id": sid,
                "approved_at_epoch": now,
                "approved_proposal_summary": saved.get("proposal_summary"),
                "updated_at_epoch": now,
                "ttl_seconds": _int_field(saved, "ttl_seconds", 86400, positive=True),
                "execution_side": _side(),
                **meta,
            }
            _atomic_write_json(state_file, payload)
        print(json.dumps({"ok": True, "phase": "IDLE", "user_approved": True, "state": payload}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "record-pass1":
        sid = args.session_id.strip()
        if not sid or args.ttl <= 0:
            print(json.dumps({"ok": False, "reason": "invalid_arguments", "error": "session-id must be non-empty and ttl must be positive"}, ensure_ascii=False, indent=2))
            return 2
        payload = {
            "schema_version": 1,
            "phase": "AWAITING_MASTER_RELOAD",
            "execution_side": _side(),
            "pass1_session_id": sid,
            "target_master": args.target_master.strip(),
            "updated_at_epoch": now,
            "ttl_seconds": args.ttl,
            **meta,
        }
        with _state_lock(state_file):
            if not args.force:
                try:
                    existing = _load_state(state_file)
                except ValueError:
                    existing = {}
                claimed_by = existing.get("claimed_by_session_id")
                if (existing.get("phase") == "AWAITING_USER_APPROVAL"
                        and now - _int_field(existing, "updated_at_epoch", 0) <= _int_field(existing, "ttl_seconds", 86400, positive=True)):
                    print(json.dumps({"ok": False, "reason": "active_user_approval", "saved": existing}, ensure_ascii=False, indent=2))
                    return 2
                if (
                    existing.get("phase") == "AWAITING_MASTER_RELOAD"
                    and claimed_by
                    and claimed_by != sid
                    and now - _int_field(existing, "updated_at_epoch", 0) <= _int_field(existing, "ttl_seconds", 86400, positive=True)
                    and _is_aligned_with_repo(existing, meta)
                ):
                    print(json.dumps({"ok": False, "reason": "active_pass2_claimed", "saved": existing}, ensure_ascii=False, indent=2))
                    return 2
            _atomic_write_json(state_file, payload)
        print(json.dumps({"ok": True, "state_file": str(state_file), "state": payload}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "verify-pass2":
        sid = args.session_id.strip()
        with _state_lock(state_file):
            try:
                saved = _load_state(state_file)
            except ValueError:
                print(json.dumps({"ok": False, "reason": "corrupt_state"}, ensure_ascii=False, indent=2))
                return 2
            reason = None
            if saved.get("phase") != "AWAITING_MASTER_RELOAD":
                reason = "idle_state"
            elif saved.get("execution_side") != _side():
                reason = "side_mismatch"
            elif now - _int_field(saved, "updated_at_epoch", 0) > _int_field(saved, "ttl_seconds", 86400, positive=True):
                reason = "expired_ttl"
            elif saved.get("pass1_session_id") == sid or not sid:
                reason = "same_session_loop"
            elif saved.get("claimed_by_session_id") and saved.get("claimed_by_session_id") != sid:
                reason = "already_claimed"
            elif not _is_aligned_with_repo(saved, meta):
                reason = "repo_drift"
            ok = reason is None
            if ok and saved.get("claimed_by_session_id") != sid:
                saved = {**saved, "claimed_by_session_id": sid}
                _atomic_write_json(state_file, saved)
        print(json.dumps({"ok": ok, "reason": reason or "ready_for_pass2", "saved": saved}, ensure_ascii=False, indent=2))
        return 0 if ok else 2

    with _state_lock(state_file):
        if args.verified and not args.force:
            try:
                saved = _load_state(state_file, default_ttl)
            except (ValueError, OSError) as exc:
                print(json.dumps({"ok": False, "reason": "invalid_state", "error": str(exc)}, ensure_ascii=False, indent=2))
                return 1
            if (
                saved.get("phase") == "AWAITING_USER_APPROVAL"
                and int(time.time()) - _int_field(saved, "updated_at_epoch", 0)
                <= _int_field(saved, "ttl_seconds", default_ttl, positive=True)
            ):
                print(json.dumps({"ok": False, "reason": "active_user_approval", "error": "An active proposal requires user approval; use --force to explicitly override"}, ensure_ascii=False, indent=2))
                return 1
        if args.verified and args.readback_done:
            grounding = _run_repo_grounding_gate(repo_root)
            if grounding.get("ok") is not True:
                print(json.dumps({
                    "ok": False,
                    "readback_verified": False,
                    "reason": "grounding_check_failed",
                    "error": grounding.get("error"),
                    "finding_count": grounding.get("finding_count", 0),
                    "findings": grounding.get("findings", []),
                }, ensure_ascii=False, indent=2))
                return 2
        if args.verified:
            payload = {
                "schema_version": 1,
                "phase": "IDLE",
                "last_verified_epoch": now,
                "readback_verified": bool(args.readback_done),
                "execution_side": _side(),
                **meta,
            }
            _atomic_write_json(state_file, payload)
        else:
            state_file.unlink(missing_ok=True)
    out: Dict[str, Any] = {
        "ok": True,
        "phase": "IDLE",
        "verified": bool(args.verified),
        "readback_verified": bool(args.verified and args.readback_done),
    }
    if args.verified and not args.readback_done:
        out["notice"] = "pending_cold_readback"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
