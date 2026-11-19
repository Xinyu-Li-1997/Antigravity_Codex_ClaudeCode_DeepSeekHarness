#!/usr/bin/env python3
"""fleet_state.py - fleet-evolve 维护流程的本机审批与重启验证状态锁（仅依赖 Python 标准库）。"""

from __future__ import annotations

import argparse
import contextlib
from contextlib import contextmanager
import hashlib
import json
import os
import stat
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


_GIT_ENV_SCRUB = frozenset({
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
    "GIT_PREFIX",
})


_WIN_LINK_REPARSE_TAGS = frozenset({
    getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
})


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if bool(getattr(st, "st_file_attributes", 0) & reparse_flag):
        tag = getattr(st, "st_reparse_tag", 0)
        return tag == 0 or tag in _WIN_LINK_REPARSE_TAGS
    return False


def _reject_symlink_chain(path: Path) -> Path:
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise ValueError(f"Refusing parent traversal in fleet-evolve state path: {path}")
    target = expanded.absolute()
    if ".." in target.parts:
        raise ValueError(f"Refusing parent traversal in fleet-evolve state path: {path}")
    for candidate in (*reversed(target.parents), target):
        if _is_reparse_or_symlink(candidate):
            raise ValueError(f"Refusing symbolic link or reparse point in fleet-evolve state path: {candidate}")
    return target


def _default_state_dir() -> Path:
    override = os.environ.get("CAE_FLEET_STATE_DIR", "").strip()
    if override:
        return _reject_symlink_chain(Path(override)).resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return _reject_symlink_chain(base / "cae-harness" / "fleet-evolve").resolve()


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
    state_file = state_dir / f"state.{_side()}.json"
    lock_path = state_file.with_suffix(state_file.suffix + ".lock")
    for candidate in (state_file, lock_path):
        if _is_reparse_or_symlink(candidate):
            raise ValueError(f"Refusing symbolic link or reparse point in fleet-evolve state path: {candidate}")
    return state_file


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
        if _is_reparse_or_symlink(harness_py):
            raise ValueError(f"Refusing symbolic link or reparse point in cae_harness.py: {harness_py}")
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
    except (OSError, ImportError, SyntaxError, ValueError, TypeError, AttributeError, RuntimeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _check_json_nesting_depth(raw: str, max_depth: int = 64, *, label: str = "corrupt state JSON") -> None:
    depth = 0
    in_string = False
    escaped = False
    for ch in raw:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            if depth > max_depth:
                raise ValueError(label)
        elif ch in "}]":
            depth = max(0, depth - 1)


def _default_ttl_seconds(repo_root: Path | None = None) -> int:
    env_profile = os.environ.get("CAE_FLEET_PROFILE", "").strip()
    if env_profile:
        candidates: list[Path] = [Path(env_profile).expanduser()]
    else:
        candidates = []
        if repo_root is not None:
            candidates.append(repo_root / "template" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json")
        candidates.append(Path(__file__).resolve().parents[2] / "cae-harness" / "fleet_profile.json")
    for candidate in candidates:
        try:
            if candidate.is_file() and not _is_reparse_or_symlink(candidate):
                text = candidate.read_text(encoding="utf-8-sig")
                _check_json_nesting_depth(text, label="corrupt fleet_profile.json")
                raw = json.loads(text)
                if not isinstance(raw, dict):
                    continue
                if "schema_version" in raw and (isinstance(raw["schema_version"], bool) or raw["schema_version"] != 1):
                    continue
                hd = (raw.get("runtime_tunables") or {}).get("headless_dispatch") or {}
                val = hd.get("fleet_state_ttl_sec") if isinstance(hd, dict) else None
                if isinstance(val, int) and not isinstance(val, bool) and val > 0:
                    return val
        except (OSError, UnicodeError, ValueError, RecursionError):
            continue
    return 86400


def _repo_meta(repo_root: Path) -> Dict[str, str]:
    plugin_json = repo_root / "template" / "user-guard" / "plugin.json"
    skill_md = repo_root / "template" / "user-guard" / "skills" / "fleet-evolve" / "SKILL.md"
    profile_json = repo_root / "template" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json"
    plugin_version = ""
    if plugin_json.is_file():
        try:
            plugin_text = plugin_json.read_text(encoding="utf-8-sig")
            _check_json_nesting_depth(plugin_text, label="corrupt plugin.json")
            parsed_plugin = json.loads(plugin_text)
        except RecursionError as exc:
            raise ValueError("corrupt plugin.json") from exc
        if not isinstance(parsed_plugin, dict):
            raise ValueError("plugin.json root must be a JSON object")
        plugin_version = parsed_plugin.get("version", "")
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
    git_env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_SCRUB}
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            env=git_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
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
    if not saved:
        return False
    strict_v1 = saved.get("schema_version") == 1
    return bool(
        saved.get("execution_side") == _side()
        and saved.get("plugin_version") == meta["plugin_version"]
        and saved.get("skill_sha256") == meta["skill_sha256"]
        and (
            saved.get("profile_sha256") == meta["profile_sha256"]
            if strict_v1 or saved.get("profile_sha256")
            else True
        )
        and (
            saved.get("repo_root") == meta["repo_root"]
            if strict_v1 or saved.get("repo_root")
            else True
        )
        and (
            saved.get("core_sha256") == meta["core_sha256"]
            if strict_v1 or saved.get("core_sha256")
            else True
        )
        and (not meta["repo_commit"] or saved.get("repo_commit") == meta["repo_commit"])
    )


@contextmanager
def _state_lock(state_file: Path) -> Iterator[None]:
    _reject_symlink_chain(state_file)
    lock_path = state_file.with_suffix(state_file.suffix + ".lock")
    if _is_reparse_or_symlink(lock_path):
        raise ValueError(f"Refusing symbolic link or reparse point in fleet-evolve state path: {lock_path}")
    leaf_created = not state_file.parent.exists()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_chain(state_file)
    if leaf_created and os.name == "posix":
        state_file.parent.chmod(0o700)
    if _is_reparse_or_symlink(state_file) or _is_reparse_or_symlink(lock_path):
        raise ValueError(f"Refusing symbolic link or reparse point in fleet-evolve state path: {lock_path}")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(8):
        if _is_reparse_or_symlink(lock_path):
            raise ValueError(f"Refusing symbolic link or reparse point in fleet-evolve state path: {lock_path}")
        fd = os.open(lock_path, flags, 0o600)
        locked = False
        try:
            opened_st = os.fstat(fd)
            if not stat.S_ISREG(opened_st.st_mode) or opened_st.st_nlink > 1:
                raise ValueError(f"Refusing non-regular or hardlinked fleet-evolve lock file: {lock_path}")
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                locked = True
            else:
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            opened_after = os.fstat(fd)
            try:
                path_st = lock_path.lstat()
            except FileNotFoundError:
                path_st = None
            if path_st is not None and _is_reparse_or_symlink(lock_path):
                raise ValueError(f"Refusing symbolic link or reparse point in fleet-evolve state path: {lock_path}")
            if (
                not stat.S_ISREG(opened_after.st_mode)
                or opened_after.st_nlink > 1
                or (path_st is not None and not stat.S_ISREG(path_st.st_mode))
            ):
                raise ValueError(f"Refusing non-regular or hardlinked fleet-evolve lock file: {lock_path}")
            if (
                path_st is None
                or ((os.name != "nt" and opened_after.st_nlink == 0) or opened_after.st_nlink > 1)
                or (
                    (opened_after.st_dev or path_st.st_dev or opened_after.st_ino or path_st.st_ino)
                    and (opened_after.st_dev, opened_after.st_ino) != (path_st.st_dev, path_st.st_ino)
                )
            ):
                continue
            yield
            return
        finally:
            if locked:
                with contextlib.suppress(OSError):
                    if os.name == "nt":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
    raise ValueError(f"Lock file replaced concurrently while acquiring fleet-evolve lock: {lock_path}")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    _reject_symlink_chain(path)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    leaf_created = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_chain(path)
    if leaf_created and os.name == "posix":
        path.parent.chmod(0o700)
    fd, tmp = tempfile.mkstemp(prefix=".fleet-state-", suffix=".json", dir=str(path.parent))
    fd_open = True
    try:
        handle = os.fdopen(fd, "wb")
        fd_open = False
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        if _is_reparse_or_symlink(path) or _is_reparse_or_symlink(path.parent):
            raise ValueError(f"Refusing symbolic link or reparse point in fleet-evolve state path: {path}")
        try:
            existing_st = path.lstat()
        except FileNotFoundError:
            existing_st = None
        if existing_st is not None and (not stat.S_ISREG(existing_st.st_mode) or existing_st.st_nlink > 1):
            raise ValueError(f"Refusing non-regular or hardlinked fleet-evolve state file: {path}")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt >= 4:
                    raise
                time.sleep(0.02)
    finally:
        if fd_open:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
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


def _reject_json_constant(_const: str) -> Any:
    raise ValueError("corrupt state JSON")


def _load_state(path: Path, default_ttl: int = 86400) -> Dict[str, Any]:
    _reject_symlink_chain(path)
    raw = ""
    open_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    for attempt in range(3):
        try:
            fd = os.open(path, open_flags)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
                    raise ValueError(f"Refusing non-regular or hardlinked fleet-evolve state file: {path}")
                with os.fdopen(fd, "rb") as fh:
                    fd = -1
                    raw = fh.read().decode("utf-8-sig")
            finally:
                if fd >= 0:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            break
        except FileNotFoundError:
            return {}
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.02)
    try:
        _check_json_nesting_depth(raw)
        loaded = json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError("corrupt state JSON") from exc
    if not isinstance(loaded, dict):
        raise ValueError("state file root must be a JSON object")
    if loaded:
        if "schema_version" in loaded and (isinstance(loaded["schema_version"], bool) or loaded["schema_version"] != 1):
            raise ValueError("unsupported state schema_version")
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
    p_prop.add_argument("--force", action="store_true", help="Overwrite even if an active Pass 1/Pass 2 reload lock or corrupt state exists")

    p_app = sub.add_parser("approve", help="Record explicit user sign-off on the proposed client-model table")
    p_app.add_argument("--session-id", required=True)

    p_rec = sub.add_parser("record-pass1", help="Record Pass 1 completion awaiting Master reload")
    p_rec.add_argument("--session-id", required=True)
    p_rec.add_argument("--target-master", required=True)
    p_rec.add_argument("--ttl", type=int, default=None)
    p_rec.add_argument("--force", action="store_true", help="Overwrite even if an active proposal, Pass 2 session lock, or corrupt state exists")

    p_ver = sub.add_parser("verify-pass2", help="Validate Pass 2 re-entry from a reloaded Master session")
    p_ver.add_argument("--session-id", required=True)

    p_clr = sub.add_parser("clear", help="Reset bootstrap state lock to IDLE (optionally recording last_verified metadata)")
    p_clr.add_argument("--verified", action="store_true")
    p_clr.add_argument("--session-id", default="", help="Pass 2 session id that claimed the active reload lock")
    p_clr.add_argument("--force", action="store_true", help="Allow clearing an active user approval proposal, active Pass 1/Pass 2 reload lock, or corrupt state")
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
    if args.cmd == "status":
        now = int(time.time())
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

    try:
        if args.cmd == "propose":
            sid = args.session_id.strip()
            summary = args.summary.strip()
            if not sid or not summary or args.ttl <= 0:
                print(json.dumps({"ok": False, "reason": "invalid_arguments", "error": "session-id and summary must be non-empty and ttl must be positive"}, ensure_ascii=False, indent=2))
                return 2
            with _state_lock(state_file):
                now = int(time.time())
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
                if not args.force:
                    try:
                        existing = _load_state(state_file, default_ttl)
                    except (ValueError, OSError) as exc:
                        print(json.dumps({"ok": False, "reason": "corrupt_state", "error": str(exc)}, ensure_ascii=False, indent=2))
                        return 2
                    if (
                        existing.get("phase") == "AWAITING_MASTER_RELOAD"
                        and now - _int_field(existing, "updated_at_epoch", 0) <= _int_field(existing, "ttl_seconds", default_ttl, positive=True)
                        and _is_aligned_with_repo(existing, meta)
                    ):
                        reason = "active_pass2_claimed" if existing.get("claimed_by_session_id") else "active_master_reload"
                        print(json.dumps({"ok": False, "reason": reason, "saved": existing}, ensure_ascii=False, indent=2))
                        return 2
                    if (
                        existing.get("phase") == "AWAITING_USER_APPROVAL"
                        and existing.get("proposing_session_id")
                        and existing.get("proposing_session_id") != sid
                        and now - _int_field(existing, "updated_at_epoch", 0) <= _int_field(existing, "ttl_seconds", default_ttl, positive=True)
                    ):
                        print(json.dumps({"ok": False, "reason": "active_other_session_proposal", "saved": existing}, ensure_ascii=False, indent=2))
                        return 2
                _atomic_write_json(state_file, payload)
            print(json.dumps({"ok": True, "user_approval_required": True, "state_file": str(state_file), "state": payload}, ensure_ascii=False, indent=2))
            return 0

        if args.cmd == "approve":
            sid = args.session_id.strip()
            if not sid:
                print(json.dumps({"ok": False, "reason": "invalid_arguments", "error": "session-id must be non-empty"}, ensure_ascii=False, indent=2))
                return 2
            with _state_lock(state_file):
                now = int(time.time())
                try:
                    saved = _load_state(state_file, default_ttl)
                except (ValueError, OSError):
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
                if now - _int_field(saved, "updated_at_epoch", 0) > _int_field(saved, "ttl_seconds", default_ttl, positive=True):
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
                    "ttl_seconds": _int_field(saved, "ttl_seconds", default_ttl, positive=True),
                    "execution_side": _side(),
                    **meta,
                }
                _atomic_write_json(state_file, payload)
            print(json.dumps({"ok": True, "phase": "IDLE", "user_approved": True, "state": payload}, ensure_ascii=False, indent=2))
            return 0

        if args.cmd == "record-pass1":
            sid = args.session_id.strip()
            target_master = args.target_master.strip()
            if not sid or not target_master or args.ttl <= 0:
                print(json.dumps({"ok": False, "reason": "invalid_arguments", "error": "session-id and target-master must be non-empty and ttl must be positive"}, ensure_ascii=False, indent=2))
                return 2
            with _state_lock(state_file):
                now = int(time.time())
                payload = {
                    "schema_version": 1,
                    "phase": "AWAITING_MASTER_RELOAD",
                    "execution_side": _side(),
                    "pass1_session_id": sid,
                    "target_master": target_master,
                    "updated_at_epoch": now,
                    "ttl_seconds": args.ttl,
                    **meta,
                }
                if not args.force:
                    try:
                        existing = _load_state(state_file, default_ttl)
                    except (ValueError, OSError) as exc:
                        print(json.dumps({"ok": False, "reason": "corrupt_state", "error": str(exc)}, ensure_ascii=False, indent=2))
                        return 2
                    claimed_by = existing.get("claimed_by_session_id")
                    if (
                        existing.get("phase") == "AWAITING_USER_APPROVAL"
                        and now - _int_field(existing, "updated_at_epoch", 0) <= _int_field(existing, "ttl_seconds", default_ttl, positive=True)
                        and _is_aligned_with_repo(existing, meta)
                    ):
                        print(json.dumps({"ok": False, "reason": "active_user_approval", "saved": existing}, ensure_ascii=False, indent=2))
                        return 2
                    if (
                        existing.get("phase") == "AWAITING_MASTER_RELOAD"
                        and claimed_by
                        and claimed_by != sid
                        and now - _int_field(existing, "updated_at_epoch", 0) <= _int_field(existing, "ttl_seconds", default_ttl, positive=True)
                        and _is_aligned_with_repo(existing, meta)
                    ):
                        print(json.dumps({"ok": False, "reason": "active_pass2_claimed", "saved": existing}, ensure_ascii=False, indent=2))
                        return 2
                _atomic_write_json(state_file, payload)
            print(json.dumps({"ok": True, "state_file": str(state_file), "state": payload}, ensure_ascii=False, indent=2))
            return 0

        if args.cmd == "verify-pass2":
            sid = args.session_id.strip()
            if not sid:
                print(json.dumps({"ok": False, "reason": "invalid_arguments", "error": "session-id must be non-empty"}, ensure_ascii=False, indent=2))
                return 2
            with _state_lock(state_file):
                now = int(time.time())
                try:
                    saved = _load_state(state_file, default_ttl)
                except (ValueError, OSError):
                    print(json.dumps({"ok": False, "reason": "corrupt_state"}, ensure_ascii=False, indent=2))
                    return 2
                reason = None
                if saved.get("phase") != "AWAITING_MASTER_RELOAD":
                    reason = "idle_state"
                elif saved.get("execution_side") != _side():
                    reason = "side_mismatch"
                elif now - _int_field(saved, "updated_at_epoch", 0) > _int_field(saved, "ttl_seconds", default_ttl, positive=True):
                    reason = "expired_ttl"
                elif saved.get("pass1_session_id") == sid:
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
            now = int(time.time())
            if not args.force:
                try:
                    saved = _load_state(state_file, default_ttl)
                except (ValueError, OSError) as exc:
                    print(json.dumps({"ok": False, "reason": "corrupt_state", "error": str(exc)}, ensure_ascii=False, indent=2))
                    return 1
                within_ttl = (
                    bool(saved)
                    and now - _int_field(saved, "updated_at_epoch", 0)
                    <= _int_field(saved, "ttl_seconds", default_ttl, positive=True)
                )
                if saved.get("phase") == "AWAITING_USER_APPROVAL" and within_ttl and _is_aligned_with_repo(saved, meta):
                    print(json.dumps({"ok": False, "reason": "active_user_approval", "error": "An active proposal requires user approval; use --force to explicitly override"}, ensure_ascii=False, indent=2))
                    return 1
                if (
                    saved.get("phase") == "AWAITING_MASTER_RELOAD"
                    and within_ttl
                    and _is_aligned_with_repo(saved, meta)
                ):
                    claimed_by = saved.get("claimed_by_session_id")
                    caller_sid = args.session_id.strip()
                    if claimed_by and caller_sid and caller_sid == claimed_by:
                        pass
                    else:
                        if claimed_by:
                            reason = "active_pass2_claimed"
                            err_msg = "An active Pass 2 session has claimed the lock; pass --session-id <claiming_id> or use --force to explicitly override"
                        else:
                            reason = "active_master_reload"
                            err_msg = "An active Pass 1 reload lock is awaiting Master reload; use --force to explicitly override"
                        print(json.dumps({"ok": False, "reason": reason, "error": err_msg}, ensure_ascii=False, indent=2))
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
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "reason": "invalid_state_or_repo", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
