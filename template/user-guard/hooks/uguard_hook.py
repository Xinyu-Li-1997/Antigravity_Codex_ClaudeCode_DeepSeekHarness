#!/usr/bin/env python3
# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""
uguard_hook.py - 四端共享单入口生命周期钩子调度器（纯 Python 3.11+ 标准库）。

职责：
1. 将 Antigravity、Codex、Claude Code 的各生命周期事件收口为单一、命令串字节恒定的入口进程，
   将 Codex 与 Claude Code 单事件双进程冷启动降为单进程进程内串联执行；
2. 交接模式与压缩阈值改由运行时读取 fleet_profile.json（含 fleet_profile.local.json）或状态目录
   handoff/policy.json，切换策略时无需改动 hooks.json 命令串，从而保护 Codex [hooks.state] 信任哈希；
3. 保留原有四个独立钩子脚本供离线单元测试或直接调用兼容，入口统一受 hook_safety 墙钟与条件内存上限保护。
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from _lib.process_guard import install_hook_self_guard  # noqa: E402
import user_words  # noqa: E402


def _default_native_dirs(client: str, state_dir_arg: str, log_dir_arg: str) -> Tuple[Path, Path]:
    if client == "claude":
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    else:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    state_dir = Path(state_dir_arg).expanduser() if state_dir_arg else (home / "hooks" / "state")
    log_dir = Path(log_dir_arg).expanduser() if log_dir_arg else (home / "hooks" / "logs")
    return state_dir, log_dir


def _load_handoff_policy_override(handoff_state_dir: Path) -> Tuple[Optional[str], Optional[int]]:
    """读取用户经 install.py 显式设置的交接策略文件 handoff/policy.json（忽略符号链接）。"""
    policy_path = handoff_state_dir / "policy.json"
    try:
        if policy_path.is_symlink() or not policy_path.is_file():
            return None, None
        doc = json.loads(policy_path.read_text(encoding="utf-8-sig"))
        if not isinstance(doc, dict):
            return None, None
        raw_mode = str(doc.get("mode") or "").strip().lower()
        mode = raw_mode if raw_mode in ("advisory", "enforce", "off") else None
        raw_lim = doc.get("compact_limit")
        limit = int(raw_lim) if isinstance(raw_lim, int) and not isinstance(raw_lim, bool) and raw_lim >= 0 else None
        return mode, limit
    except (OSError, ValueError, TypeError):
        return None, None


def _run_native_handoff(
    payload: Dict[str, Any],
    event: str,
    client: str,
    handoff_dir: Path,
    cli_mode: Optional[str],
    cli_limit: Optional[int],
) -> Dict[str, Any]:
    file_mode, file_limit = _load_handoff_policy_override(handoff_dir)
    eff_mode = cli_mode if cli_mode is not None else file_mode
    eff_limit = cli_limit if cli_limit is not None else file_limit
    if eff_mode == "off":
        return {}
    import handoff_gate  # noqa: E402

    ev_payload = dict(payload)
    if event and not ev_payload.get("hook_event_name"):
        ev_payload["hook_event_name"] = event
    try:
        return handoff_gate._handle_native_handoff(
            ev_payload,
            handoff_dir,
            eff_mode if eff_mode is not None else "advisory",
            eff_limit if eff_limit is not None else 0,
            client,
            _mode_explicit=(eff_mode is not None),
            _limit_explicit=(eff_limit is not None),
        )
    except Exception as exc:
        label = "Claude" if client == "claude" else "Codex"
        sys.stderr.write(f"{label} handoff hook: {exc}\n")
        return {"systemMessage": f"{label} 长会话门禁本次处理失败，未更新保护状态；请检查钩子错误。"}


def _merge_session_start_responses(r1: Dict[str, Any], r2: Dict[str, Any]) -> Dict[str, Any]:
    hso1 = r1.get("hookSpecificOutput") if isinstance(r1.get("hookSpecificOutput"), dict) else {}
    hso2 = r2.get("hookSpecificOutput") if isinstance(r2.get("hookSpecificOutput"), dict) else {}
    ctx1 = str(hso1.get("additionalContext") or "")
    ctx2 = str(hso2.get("additionalContext") or "")
    if ctx1 and ctx2:
        merged = dict(r1)
        merged_hso = dict(hso1)
        merged_hso["hookEventName"] = merged_hso.get("hookEventName") or "SessionStart"
        merged_hso["additionalContext"] = ctx1 + "\n\n---\n\n" + ctx2
        merged["hookSpecificOutput"] = merged_hso
        return merged
    if ctx1:
        return r1
    if ctx2:
        return r2
    return r1 or r2 or {}


def _merge_pre_tool_responses(r1: Dict[str, Any], r2: Dict[str, Any]) -> Dict[str, Any]:
    hso1 = r1.get("hookSpecificOutput") if isinstance(r1.get("hookSpecificOutput"), dict) else {}
    hso2 = r2.get("hookSpecificOutput") if isinstance(r2.get("hookSpecificOutput"), dict) else {}
    d1 = str(hso1.get("permissionDecision") or "")
    d2 = str(hso2.get("permissionDecision") or "")
    if d1 == "deny":
        return r1
    if d2 == "deny":
        return r2
    if d1 == "ask":
        ctx2 = str(hso2.get("additionalContext") or "")
        if ctx2:
            merged = dict(r1)
            merged_hso = dict(hso1)
            existing_ctx = str(merged_hso.get("additionalContext") or "")
            merged_hso["additionalContext"] = (existing_ctx + "\n\n---\n\n" + ctx2) if existing_ctx else ctx2
            merged["hookSpecificOutput"] = merged_hso
            return merged
        return r1
    return r2 or r1 or {}


def dispatch_hook_event(
    client: str,
    event: str,
    payload: Dict[str, Any],
    *,
    state_dir_str: str = "",
    log_dir_str: str = "",
    handoff_mode: Optional[str] = None,
    compact_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """统一分派四阶段钩子事件（进程内懒加载执行，零额外子进程开销）。"""
    ev = event or str(payload.get("hookEventName") or payload.get("hook_event_name") or payload.get("eventName") or "")

    if client == "antigravity":
        if ev in ("SessionStart", "PreInvocation"):
            import session_context_loader  # noqa: E402
            return session_context_loader.handle_context_check(payload, event_name=ev)
        if ev == "PreToolUse":
            import status_guard  # noqa: E402
            return status_guard.handle_pre_tool_use(payload)
        if ev == "Stop":
            import reply_repeat_guard  # noqa: E402
            return reply_repeat_guard.handle_stop_repeat_guard(payload)
        return {}

    state_dir, log_dir = _default_native_dirs(client, state_dir_str, log_dir_str)
    session_ctx_dir = state_dir / "session-context"
    handoff_dir = state_dir / "handoff"
    session_ctx_log = log_dir / "session-context.jsonl"

    if ev == "SessionStart":
        import session_context_loader  # noqa: E402
        loader = (
            session_context_loader.handle_claude_session_start
            if client == "claude"
            else session_context_loader.handle_codex_session_start
        )
        try:
            r1 = loader(payload, state_dir_str=str(session_ctx_dir), log_path_str=str(session_ctx_log))
        except Exception as exc:
            err_cap = int(session_context_loader._resolve_loader_policy(client)["inline_total_chars"])
            err_text = f"恢复来源：{'Claude ' if client == 'claude' else 'Codex '}{payload.get('source', 'unknown')}\n提取状态：异常（{exc}）"
            r1 = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": err_text[:err_cap]}}
        r2 = _run_native_handoff(payload, ev, client, handoff_dir, handoff_mode, compact_limit)
        return _merge_session_start_responses(r1, r2)

    if ev in ("PostCompact", "UserPromptSubmit"):
        return _run_native_handoff(payload, ev, client, handoff_dir, handoff_mode, compact_limit)

    if ev == "PreToolUse":
        import status_guard  # noqa: E402
        r1 = status_guard.handle_claude_pre_tool_use({**payload, "_client": client}, state_dir, log_dir)
        hso1 = r1.get("hookSpecificOutput") if isinstance(r1.get("hookSpecificOutput"), dict) else {}
        if hso1.get("permissionDecision") == "deny":
            return r1
        r2 = _run_native_handoff(payload, ev, client, handoff_dir, handoff_mode, compact_limit)
        return _merge_pre_tool_responses(r1, r2)

    if ev == "Stop":
        import reply_repeat_guard  # noqa: E402
        r1 = reply_repeat_guard.handle_claude_stop_repeat_guard({**payload, "_client": client}, state_dir, log_dir)
        if isinstance(r1, dict) and r1.get("decision") == "block":
            return r1
        r2 = _run_native_handoff(payload, ev, client, handoff_dir, handoff_mode, compact_limit)
        return r2 or r1 or {}

    return {}


def main() -> None:
    with install_hook_self_guard(HOOKS_DIR):
        _main_inner()


def _main_inner() -> None:
    parser = argparse.ArgumentParser(description="user-guard 四端统一单入口生命周期钩子调度器")
    parser.add_argument("--client", choices=("antigravity", "codex", "claude"), default="antigravity")
    parser.add_argument(
        "--event",
        choices=("SessionStart", "PreInvocation", "PostCompact", "UserPromptSubmit", "PreToolUse", "Stop", ""),
        default="",
    )
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--handoff-mode", choices=("advisory", "enforce", "off"), default=None)
    parser.add_argument("--compact-limit", type=int, default=None)
    args, _ = parser.parse_known_args()

    payload: Dict[str, Any] = {}
    try:
        payload = user_words.read_hook_payload()
    except (OSError, ValueError) as exc:
        if args.client in ("codex", "claude") and args.event == "SessionStart":
            label = "Codex" if args.client == "codex" else "Claude"
            sys.stderr.write(f"{label} uguard_hook SessionStart JSON parse error: {exc}\n")
            sys.exit(2)
        if args.client in ("codex", "claude"):
            label = "Codex" if args.client == "codex" else "Claude"
            sys.stderr.write(f"{label} uguard_hook {args.event or 'Unknown'} JSON parse error: {exc}\n")

    ev = args.event or str(payload.get("hookEventName") or payload.get("hook_event_name") or payload.get("eventName") or "")
    try:
        response = dispatch_hook_event(
            args.client,
            ev,
            payload,
            state_dir_str=args.state_dir,
            log_dir_str=args.log_dir,
            handoff_mode=args.handoff_mode,
            compact_limit=args.compact_limit,
        )
    except Exception as exc:
        stack_str = traceback.format_exc()
        if args.client in ("codex", "claude"):
            label = "Codex" if args.client == "codex" else "Claude"
            sys.stderr.write(f"{label} uguard_hook {ev}: {exc}\n")
            response = {}
        else:
            if ev in ("SessionStart", "PreInvocation"):
                response = {"injectSteps": []}
            elif ev in ("PreToolUse", "Stop"):
                response = {"decision": "allow"}
            else:
                response = {}
            sys.stderr.write(f"Antigravity uguard_hook {ev}: {exc}\n{stack_str}\n")

    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
