#!/usr/bin/env python3
"""Host-local two-pass bootstrap state lock for fleet-evolve (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict


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


def _is_upstream_repo(candidate: Path) -> bool:
    return (candidate / "install.py").is_file() and (candidate / "template" / "user-guard" / "plugin.json").is_file()


def _resolve_repo_root(raw_repo: str | None) -> Path:
    if raw_repo:
        root = Path(raw_repo).expanduser().resolve()
        if not _is_upstream_repo(root):
            raise ValueError(f"--repo is not an upstream repository root (missing install.py or plugin.json): {root}")
        return root
    for seed in (Path.cwd().resolve(), Path(__file__).resolve().parent):
        for candidate in (seed, *seed.parents):
            if _is_upstream_repo(candidate):
                return candidate
    raise ValueError("Cannot auto-detect upstream repository root; pass --repo <path> explicitly")


def _repo_meta(repo_root: Path) -> Dict[str, str]:
    plugin_json = repo_root / "template" / "user-guard" / "plugin.json"
    skill_md = repo_root / "template" / "user-guard" / "skills" / "fleet-evolve" / "SKILL.md"
    plugin_version = json.loads(plugin_json.read_text(encoding="utf-8")).get("version", "") if plugin_json.is_file() else ""
    skill_sha256 = hashlib.sha256(skill_md.read_bytes()).hexdigest() if skill_md.is_file() else ""
    commit = ""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, encoding="utf-8", timeout=5
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {"repo_commit": commit, "plugin_version": str(plugin_version), "skill_sha256": skill_sha256}


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


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage fleet-evolve two-pass bootstrap state lock")
    parser.add_argument("--repo", default=None, help="Upstream repository root (auto-detected from cwd/script if omitted)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Read current bootstrap phase without modifying disk")

    p_rec = sub.add_parser("record-pass1", help="Record Pass 1 completion awaiting Master reload")
    p_rec.add_argument("--session-id", required=True)
    p_rec.add_argument("--target-master", required=True)
    p_rec.add_argument("--ttl", type=int, default=86400)

    p_ver = sub.add_parser("verify-pass2", help="Validate Pass 2 re-entry from a reloaded Master session")
    p_ver.add_argument("--session-id", required=True)

    p_clr = sub.add_parser("clear", help="Reset bootstrap state lock to IDLE (optionally recording last_verified metadata)")
    p_clr.add_argument("--verified", action="store_true")

    args = parser.parse_args(argv)
    repo_root = _resolve_repo_root(args.repo)
    state_file = _validated_state_file(repo_root)
    meta = _repo_meta(repo_root)
    now = int(time.time())

    if args.cmd == "status":
        saved = _load_state(state_file) if state_file.is_file() else {}
        raw_phase = str(saved.get("phase") or "IDLE")
        ttl = int(saved.get("ttl_seconds") or 86400)
        updated = int(saved.get("updated_at_epoch") or 0)
        side_mismatch = bool(saved and saved.get("execution_side") != _side())
        expired = bool(raw_phase == "AWAITING_MASTER_RELOAD" and (now - updated > ttl))
        aligned = bool(
            saved
            and not side_mismatch
            and saved.get("plugin_version") == meta["plugin_version"]
            and saved.get("skill_sha256") == meta["skill_sha256"]
            and (not meta["repo_commit"] or saved.get("repo_commit") == meta["repo_commit"])
        )
        stale_reason = None
        if raw_phase == "AWAITING_MASTER_RELOAD":
            if side_mismatch:
                stale_reason = "side_mismatch"
            elif expired:
                stale_reason = "expired_ttl"
            elif not aligned:
                stale_reason = "repo_drift"
        effective_phase = "AWAITING_MASTER_RELOAD" if (raw_phase == "AWAITING_MASTER_RELOAD" and stale_reason is None) else "IDLE"
        print(json.dumps({
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

    if args.cmd == "record-pass1":
        sid = args.session_id.strip()
        if not sid or args.ttl <= 0:
            raise ValueError("session-id must be non-empty and ttl must be positive")
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
        _atomic_write_json(state_file, payload)
        print(json.dumps({"ok": True, "state_file": str(state_file), "state": payload}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "verify-pass2":
        sid = args.session_id.strip()
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
        elif now - int(saved.get("updated_at_epoch") or 0) > int(saved.get("ttl_seconds") or 86400):
            reason = "expired_ttl"
        elif saved.get("pass1_session_id") == sid or not sid:
            reason = "same_session_loop"
        elif (
            saved.get("plugin_version") != meta["plugin_version"]
            or saved.get("skill_sha256") != meta["skill_sha256"]
            or (meta["repo_commit"] and saved.get("repo_commit") != meta["repo_commit"])
        ):
            reason = "repo_drift"
        ok = reason is None
        print(json.dumps({"ok": ok, "reason": reason or "ready_for_pass2", "saved": saved}, ensure_ascii=False, indent=2))
        return 0 if ok else 2

    if args.verified:
        payload = {
            "schema_version": 1,
            "phase": "IDLE",
            "last_verified_epoch": now,
            "execution_side": _side(),
            **meta,
        }
        _atomic_write_json(state_file, payload)
    elif state_file.is_file():
        state_file.unlink()
    print(json.dumps({"ok": True, "phase": "IDLE", "verified": bool(args.verified)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
