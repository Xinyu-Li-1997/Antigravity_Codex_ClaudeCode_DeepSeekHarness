"""
user_words.py - Antigravity、Codex 与 Claude Code 会话原话及最近子代理返回归档模块 (user-guard)

职责：
1. 提供跨平台 UTF-8 标准流初始化、原子文件写入、会话文件锁与数据目录定位函数；
2. 解析 Antigravity 会话 transcript.jsonl，遇到 truncated_fields 截断标记时按步骤号单次索引补齐 transcript_full.jsonl；
3. 解析 Codex 会话 rollout-*.jsonl（合并 sessions 与 archived_sessions 下的同会话分段并校验 SHA-256 摘要）及 Claude Code 会话 jsonl；
4. 过滤系统自动注入噪声，提取用户原始指令、交互式选项回答以及最近 N 次子代理返回摘要（附原始日志路径指针），按时间顺序原子写入归档 Markdown 文件。
"""

# ① 常量、阈值与宿主依赖

import contextlib
import errno
import hashlib
import json
import os
import re
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HOOKS_DIR_SELF = Path(__file__).resolve().parent
if str(_HOOKS_DIR_SELF) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR_SELF))
from _lib import fs_safety as _fs_safety
from _lib import adaptive_policy as _adaptive_policy
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

MAX_HOOK_INPUT_CHARS = 8 * 1024 * 1024

# 系统自动注入的伪消息特征（子代理消息筛选时排除）
SYSTEM_NOISE_PATTERNS = (
    "<EPHEMERAL_MESSAGE>",
    "<CONVERSATION_SUMMARIES>",
    "<TASK_NOTIFICATION>",
    "The following system message was injected by a",
    "[Notice] All your subagents and background tasks have been stopped",
)

TOOL_TIMING_HEADER_RE = re.compile(
    r"^(?:(?:Created|Completed) At:\s*[^\r\n]+\r?\n)+",
    re.MULTILINE,
)

MARKDOWN_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
GOAL_START_RE = re.compile(r"^/goal(?:\s|$)", re.IGNORECASE)
GOAL_CANCEL_RE = re.compile(r"^(?:请)?(?:取消|停止|结束|关闭)\s*/?goal(?![A-Za-z0-9_-])", re.IGNORECASE)

# Codex Rollout 会话记录解析与归档 (供 --client codex 在 compact/resume 时调用)

CODEX_MAIN_SOURCES = {"cli", "vscode", "exec", "mcp", "desktop", "app_server"}
CODEX_SYSTEM_PROVENANCE = {"harness", "agents_md"}
CODEX_ATTACHMENTS = {"input_image": "图片", "input_audio": "音频", "input_file": "文件"}
CODEX_ARCHIVE_FORMAT = "user-guard-codex-v2"
CODEX_LEGACY_ARCHIVE_FORMAT = "user-guard-codex-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}")

_DEFAULT_MAX_LOG_BYTES = 2 * 1024 * 1024
_DEFAULT_TRIM_LOG_BYTES = 1 * 1024 * 1024
MAX_LOG_BYTES = _DEFAULT_MAX_LOG_BYTES
TRIM_LOG_BYTES = _DEFAULT_TRIM_LOG_BYTES

_DEFAULT_MAX_RECENT_SUBAGENT_RETURNS = 4
_DEFAULT_MAX_ARCHIVE_SUBAGENT_ITEM_CHARS = 1200
MAX_RECENT_SUBAGENT_RETURNS = _DEFAULT_MAX_RECENT_SUBAGENT_RETURNS
MAX_ARCHIVE_SUBAGENT_ITEM_CHARS = _DEFAULT_MAX_ARCHIVE_SUBAGENT_ITEM_CHARS

_FLEET_PROFILE_CACHE: Dict[str, Any] = {"key": None, "version": 0, "data": {}}

DEFAULT_COMPACTION_TUNABLES: Dict[str, Any] = {
    "inline_total_chars": 8000,
    "inline_user_item_chars": 1200,
    "inline_guaranteed_head_turns": 1,
    "recent_subagent_returns": 4,
    "recent_subagent_window": 12,
    "min_substantive_subagent_chars": 64,
    "priority_subagent_keywords": ["auditor", "collaborator", "reader", "review", "math", "audit"],
    "subagent_head_ratio": 0.58,
    "inline_subagent_total_chars": 1400,
    "inline_subagent_item_chars": 280,
    "archive_subagent_item_chars": 1200,
    "archive_retry_interval_sec": 30,
    "max_tracked_images": 2000,
}


_is_reparse_or_link = _fs_safety.is_reparse_or_link


def global_cae_harness_skill_dirs() -> List[Path]:
    """返回四端宿主机全局安装下的 skills/cae-harness 候选根目录列表。"""
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex")).expanduser()
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (home / ".claude")).expanduser()
    dsh_home = Path(os.environ.get("DSH_HOME") or (home / ".dsh")).expanduser()
    return [
        home / ".gemini" / "config" / "plugins" / "user-guard" / "skills" / "cae-harness",
        home / ".gemini" / "antigravity" / "plugins" / "user-guard" / "skills" / "cae-harness",
        codex_home / "skills" / "cae-harness",
        claude_home / "skills" / "cae-harness",
        dsh_home / "skills" / "cae-harness",
    ]


def _valid_profile_schema_version(parsed: Any, *, allow_omitted: bool = True) -> bool:
    if not isinstance(parsed, dict):
        return False
    if "schema_version" not in parsed or parsed.get("schema_version") is None:
        return allow_omitted
    sv = parsed.get("schema_version")
    return type(sv) is int and sv == 1


def load_fleet_profile(anchor_dir: Optional[Path] = None) -> Dict[str, Any]:
    """按 mtime 缓存读取 fleet_profile.json（支持 CAE_FLEET_PROFILE 强隔离覆盖与缺失负缓存，异常时安全降级为空字典）。"""
    env_override = os.environ.get("CAE_FLEET_PROFILE", "").strip()
    if env_override:
        cand = Path(env_override).expanduser()
        try:
            if not _is_reparse_or_link(cand) and cand.is_file():
                st = cand.stat()
                cache_key: Any = ("override_file", str(cand), st.st_mtime_ns, st.st_size)
                if _FLEET_PROFILE_CACHE.get("key") == cache_key:
                    return _FLEET_PROFILE_CACHE["data"]
                parsed = json.loads(cand.read_text(encoding="utf-8-sig"))
                if _valid_profile_schema_version(parsed, allow_omitted=True):
                    _FLEET_PROFILE_CACHE["key"] = cache_key
                    _FLEET_PROFILE_CACHE["version"] = int(_FLEET_PROFILE_CACHE.get("version", 0)) + 1
                    _FLEET_PROFILE_CACHE["data"] = parsed
                    return parsed
        except (OSError, UnicodeError, ValueError, RecursionError):
            pass
        miss_override_key = ("missing_override", env_override)
        if _FLEET_PROFILE_CACHE.get("key") != miss_override_key:
            _FLEET_PROFILE_CACHE["key"] = miss_override_key
            _FLEET_PROFILE_CACHE["version"] = int(_FLEET_PROFILE_CACHE.get("version", 0)) + 1
            _FLEET_PROFILE_CACHE["data"] = {}
        return _FLEET_PROFILE_CACHE["data"]

    anchor = Path(anchor_dir).resolve() if anchor_dir is not None else Path(__file__).resolve().parent
    candidates: List[Path] = [
        anchor.parent / "skills" / "cae-harness" / "fleet_profile.json",
        anchor / "fleet_profile.json",
        *(skill_dir / "fleet_profile.json" for skill_dir in global_cae_harness_skill_dirs()),
    ]
    for cand in candidates:
        try:
            if _is_reparse_or_link(cand) or not cand.is_file():
                continue
            st = cand.stat()
            cache_key = ("file", str(cand), st.st_mtime_ns, st.st_size)
            if _FLEET_PROFILE_CACHE.get("key") == cache_key:
                return _FLEET_PROFILE_CACHE["data"]
            parsed = json.loads(cand.read_text(encoding="utf-8-sig"))
            if _valid_profile_schema_version(parsed, allow_omitted=False):
                _FLEET_PROFILE_CACHE["key"] = cache_key
                _FLEET_PROFILE_CACHE["version"] = int(_FLEET_PROFILE_CACHE.get("version", 0)) + 1
                _FLEET_PROFILE_CACHE["data"] = parsed
                return parsed
        except (OSError, UnicodeError, ValueError, RecursionError):
            continue
    miss_key = (
        "missing",
        str(anchor),
        str(Path.home()),
        os.environ.get("CODEX_HOME", ""),
        os.environ.get("CLAUDE_CONFIG_DIR", ""),
        os.environ.get("DSH_HOME", ""),
    )
    if _FLEET_PROFILE_CACHE.get("key") != miss_key:
        _FLEET_PROFILE_CACHE["key"] = miss_key
        _FLEET_PROFILE_CACHE["version"] = int(_FLEET_PROFILE_CACHE.get("version", 0)) + 1
        _FLEET_PROFILE_CACHE["data"] = {}
    return _FLEET_PROFILE_CACHE["data"]


def get_fleet_profile_cache_token(anchor_dir: Optional[Path] = None) -> Tuple[Any, int]:
    """返回稳定的配置文件缓存令牌，避免临时空字典引发 id() 地址复用假命中。"""
    load_fleet_profile(anchor_dir)
    return _FLEET_PROFILE_CACHE.get("key"), int(_FLEET_PROFILE_CACHE.get("version", 0))


def get_runtime_tunables(section: str = "", anchor_dir: Optional[Path] = None) -> Dict[str, Any]:
    """读取 fleet_profile.json 中的 runtime_tunables（或指定子节）。"""
    tunables = load_fleet_profile(anchor_dir).get("runtime_tunables")
    if not isinstance(tunables, dict):
        return {}
    if not section:
        return tunables
    sub = tunables.get(section)
    return sub if isinstance(sub, dict) else {}


def _coerce_pos_int(raw: Any, fallback: int, minimum: int = 1) -> int:
    if isinstance(raw, bool):
        return fallback
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return fallback
    return val if val >= minimum else fallback


def _coerce_ratio(raw: Any, fallback: float, low: float = 0.20, high: float = 0.85) -> float:
    if isinstance(raw, bool):
        return fallback
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return fallback
    return min(max(val, low), high)


_BLOCKQUOTE_PREFIX_RE = re.compile(r"^(\s*(?:>\s*)+)(.*)$")


_TEXT_FENCE_LANGS = frozenset({"", "text", "txt", "plain", "markdown", "md"})


def collapse_historical_cards(text: str) -> str:
    """折叠历史对话中的多行 14 项确认卡与完工账单表格，避免压缩回读重复污染上下文。"""
    raw = str(text or "")
    if not raw or not any(
        marker in raw
        for marker in ("14 项完整参数明细", "【重负载完工账单】", "【工作流确认单】", "通道与模型", "参数项", "流号")
    ):
        return raw
    out_lines: List[str] = []
    in_table = False
    awaiting_detail_table = False
    collapsed_once = False
    fence = ""
    fence_is_code = False
    for line in raw.splitlines():
        q_match = _BLOCKQUOTE_PREFIX_RE.match(line)
        quote_prefix = q_match.group(1) if q_match else ""
        unquoted = q_match.group(2) if q_match else line
        match = MARKDOWN_FENCE_RE.match(unquoted)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ""
                fence_is_code = False
                in_table = False
                awaiting_detail_table = False
                collapsed_once = False
                out_lines.append(line)
                continue
            if fence_is_code:
                out_lines.append(line)
                continue
        elif match:
            in_table = False
            awaiting_detail_table = False
            collapsed_once = False
            fence = match[1]
            info_tokens = (match[2] or "").strip().split()
            fence_lang = info_tokens[0].lower() if info_tokens else ""
            fence_is_code = fence_lang not in _TEXT_FENCE_LANGS
            out_lines.append(line)
            continue
        stripped = unquoted.strip()
        if stripped.startswith("#### 流 ") and "14 项完整参数明细" in stripped:
            awaiting_detail_table = True
            in_table = False
            if not collapsed_once:
                out_lines.append(f"{quote_prefix}[已折叠历史确认单/账单明细]")
                collapsed_once = True
            continue
        if awaiting_detail_table:
            if not stripped:
                continue
            if stripped.startswith("|"):
                awaiting_detail_table = False
                in_table = True
                continue
            awaiting_detail_table = False
            collapsed_once = False
        if in_table:
            if stripped.startswith("|"):
                continue
            in_table = False
            collapsed_once = False
            if not stripped:
                out_lines.append(line)
                continue
        is_card_table_header = stripped.startswith("|") and (
            ("参数项" in stripped and ("序号" in stripped or "当前值" in stripped))
            or ("流号" in stripped and ("通道与执行端" in stripped or "通道与模型" in stripped or "模型与挡位" in stripped))
        )
        if is_card_table_header:
            in_table = True
            if not collapsed_once:
                out_lines.append(f"{quote_prefix}[已折叠历史确认单/账单明细]")
                collapsed_once = True
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def get_compaction_tunables(
    client: str = "antigravity",
    anchor_dir: Optional[Path] = None,
    model_name: str = "",
    effort: str = "",
    explicit_window: Optional[int] = None,
) -> Dict[str, Any]:
    """合并内置兜底值、fleet_profile 默认值与当前主会话客户端/活跃模型的动态校准参数。"""
    profile = load_fleet_profile(anchor_dir)
    client_key = str(client or "").strip().lower()
    merged: Dict[str, Any] = _adaptive_policy.resolve_adaptive_compaction_policy(
        profile,
        client=client_key,
        model_name=model_name,
        effort=effort,
        explicit_window=explicit_window,
        defaults=DEFAULT_COMPACTION_TUNABLES,
    )

    recent_limit = _coerce_pos_int(
        merged.get("recent_subagent_returns"), DEFAULT_COMPACTION_TUNABLES["recent_subagent_returns"], minimum=0
    )
    if MAX_RECENT_SUBAGENT_RETURNS != _DEFAULT_MAX_RECENT_SUBAGENT_RETURNS:
        recent_limit = max(0, int(MAX_RECENT_SUBAGENT_RETURNS))
    archive_item_chars = _coerce_pos_int(
        merged.get("archive_subagent_item_chars"), DEFAULT_COMPACTION_TUNABLES["archive_subagent_item_chars"], minimum=120
    )
    if MAX_ARCHIVE_SUBAGENT_ITEM_CHARS != _DEFAULT_MAX_ARCHIVE_SUBAGENT_ITEM_CHARS:
        archive_item_chars = max(120, int(MAX_ARCHIVE_SUBAGENT_ITEM_CHARS))
    raw_kws = merged.get("priority_subagent_keywords")
    kws = [str(k).strip().lower() for k in raw_kws if str(k).strip()] if isinstance(raw_kws, list) else list(
        DEFAULT_COMPACTION_TUNABLES["priority_subagent_keywords"]
    )
    return {
        "inline_total_chars": _coerce_pos_int(merged.get("inline_total_chars"), 8000, minimum=600),
        "inline_user_item_chars": _coerce_pos_int(merged.get("inline_user_item_chars"), 1200, minimum=200),
        "inline_guaranteed_head_turns": _coerce_pos_int(merged.get("inline_guaranteed_head_turns"), 1, minimum=1),
        "recent_subagent_returns": recent_limit,
        "recent_subagent_window": max(recent_limit, _coerce_pos_int(merged.get("recent_subagent_window"), 12, minimum=0)),
        "min_substantive_subagent_chars": _coerce_pos_int(merged.get("min_substantive_subagent_chars"), 64, minimum=1),
        "priority_subagent_keywords": kws,
        "subagent_head_ratio": _coerce_ratio(merged.get("subagent_head_ratio"), 0.58),
        "inline_subagent_total_chars": _coerce_pos_int(merged.get("inline_subagent_total_chars"), 1400, minimum=160),
        "inline_subagent_item_chars": _coerce_pos_int(merged.get("inline_subagent_item_chars"), 280, minimum=120),
        "archive_subagent_item_chars": archive_item_chars,
        "archive_retry_interval_sec": _coerce_pos_int(merged.get("archive_retry_interval_sec"), 30, minimum=1),
        "max_tracked_images": _coerce_pos_int(merged.get("max_tracked_images"), 2000, minimum=10),
    }


def select_recent_subagent_returns(
    candidates: List[Dict[str, Any]],
    policy: Optional[Dict[str, Any]] = None,
    *,
    body_getter: Any = None,
    label_getter: Any = None,
) -> List[Dict[str, Any]]:
    """按动态窗口筛选最有恢复价值的子代理返回：必保最新一条，其余优先保留实质长度与审查/推导角色并恢复时间序。"""
    cfg = policy if isinstance(policy, dict) else get_compaction_tunables()
    limit = int(cfg.get("recent_subagent_returns", DEFAULT_COMPACTION_TUNABLES["recent_subagent_returns"]))
    if limit <= 0 or not candidates:
        return []
    window_size = max(limit, int(cfg.get("recent_subagent_window", limit)))
    window_items = list(candidates[-window_size:]) if window_size > 0 else list(candidates)
    if len(window_items) <= limit:
        return window_items
    min_chars = int(cfg.get("min_substantive_subagent_chars", 64))
    priority_kws = tuple(str(k).lower() for k in (cfg.get("priority_subagent_keywords") or ()) if k)
    scored: List[Tuple[int, int, int]] = []
    for idx, item in enumerate(window_items[:-1]):
        body = str(body_getter(item) if body_getter is not None else item.get("content") or "").strip()
        label = str(label_getter(item) if label_getter is not None else item.get("label") or "").strip().lower()
        substantive = 1 if (item.get("needs_full") or len(body) >= min_chars) else 0
        priority = 1 if (priority_kws and any(kw in label for kw in priority_kws)) else 0
        scored.append((substantive, priority, idx))
    scored.sort(reverse=True)
    chosen_indices = sorted([entry[2] for entry in scored[: limit - 1]] + [len(window_items) - 1])
    return [window_items[i] for i in chosen_indices]

_AGY_SUBAGENT_CREATED_HEAD_RE = re.compile(
    r"Created the following subagent:\s*\{",
)


def parse_agy_created_subagent(raw_text: Any) -> Optional[Dict[str, Any]]:
    """使用 JSONDecoder.raw_decode 提取 Antigravity 子代理创建回执，兼容嵌套对象与尾随花括号。"""
    if not isinstance(raw_text, str) or "Created the following subagent:" not in raw_text:
        return None
    m = _AGY_SUBAGENT_CREATED_HEAD_RE.search(raw_text)
    if not m:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(raw_text, m.end() - 1)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None
AGY_SYSTEM_MSG_RE = re.compile(
    r"\[Message\][^\n]*?\bsender=([^\s]+)[^\n]*?\bcontent=",
)
CODEX_SUBAGENT_SPAWN_TOOLS = {"spawn_agent", "agent"}
CODEX_SUBAGENT_WAIT_TOOLS = {"wait_agent", "wait"}
CLAUDE_SUBAGENT_TOOLS = {"Agent", "Task", "Subagent", "dispatch_agent"}


# ② 标准流协议、数据目录与原子状态事务

def ensure_utf8_stdio() -> None:
    """全平台强制标准流为 UTF-8 编码，消除 Windows GBK 管道及 POSIX C locale 编解码崩溃"""
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


ensure_utf8_stdio()


def read_hook_payload() -> Dict[str, Any]:
    """宿主以 EOF 结束一次 JSON 请求；交互终端不等待，管道输入限制体积。"""
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read(MAX_HOOK_INPUT_CHARS + 1)
    if len(raw) > MAX_HOOK_INPUT_CHARS:
        raise ValueError("钩子输入超过 8 Mi 字符上限")
    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except RecursionError as exc:
        raise ValueError("钩子输入嵌套过深") from exc
    if not isinstance(payload, dict):
        raise ValueError("钩子输入必须为 JSON 对象")
    return payload


decode_jsonl_line = _fs_safety.decode_jsonl_line


def resolve_data_dirs(hooks_dir: Path) -> Tuple[Path, Path]:
    """
    解析运行时状态目录与日志目录优先级：
    1. 显式环境变量 USER_GUARD_STATE_DIR / USER_GUARD_LOGS_DIR（测试沙箱隔离）；
    2. Antigravity 官方注入的持久化插件数据目录 PLUGIN_DATA（保持插件代码目录零污染）；
    3. 本地回退目录 hooks_dir/state 与 hooks_dir/logs。
    """
    plugin_data = os.environ.get("PLUGIN_DATA", "").strip()
    default_base = Path(plugin_data).expanduser() if plugin_data else hooks_dir

    state_env = os.environ.get("USER_GUARD_STATE_DIR", "").strip()
    logs_env = os.environ.get("USER_GUARD_LOGS_DIR", "").strip()

    state_dir = Path(state_env).expanduser() if state_env else (default_base / "state")
    logs_dir = Path(logs_env).expanduser() if logs_env else (default_base / "logs")
    return state_dir, logs_dir


def resolve_compact_transcript_path(raw_path: Path) -> Path:
    """
    Antigravity 宿主传入 transcriptPath 时可能指向 transcript_full.jsonl；
    若同目录存在紧凑版 transcript.jsonl，优先定位至 transcript.jsonl，
    既严格对齐 4MB/6MB 遥测标定基准，又将增量扫描与原话索引体积降低约 3 倍（截断正文按需回查 transcript_full.jsonl）。
    """
    if raw_path.name == "transcript_full.jsonl":
        compact = raw_path.with_name("transcript.jsonl")
        if compact.exists() and compact.is_file():
            return compact
    return raw_path


valid_session_id = _fs_safety.valid_session_id


def get_plugin_data_state_dir() -> Path:
    """定位插件持久状态；隔离环境不读取真实宿主状态。"""
    if os.environ.get("USER_GUARD_STATE_DIR"):
        return Path(os.environ["USER_GUARD_STATE_DIR"]).expanduser()
    if os.environ.get("PLUGIN_DATA"):
        return Path(os.environ["PLUGIN_DATA"]).expanduser() / "state"
    return Path.home() / ".gemini/antigravity/plugin_data/user-guard/state"


def candidate_dispatch_plan_dirs(state_dir=None, extra_plan_dirs=()) -> List[Path]:
    raw_candidates: List[Path] = []
    for directory in extra_plan_dirs:
        raw_candidates.append(Path(directory).expanduser().absolute())
    if os.environ.get("CAE_PLAN_STATE_DIR"):
        raw_candidates.append(Path(os.environ["CAE_PLAN_STATE_DIR"]).expanduser().absolute())
    if state_dir is not None:
        root = Path(state_dir).expanduser().absolute()
        raw_candidates.append(root / "dispatch_plans")
        if root.name in ("handoff", "session-context"):
            raw_candidates.append(root.parent / "dispatch_plans")
    raw_candidates.append(get_plugin_data_state_dir().expanduser().absolute() / "dispatch_plans")
    resolved_candidates: List[Path] = []
    for cand in raw_candidates:
        if _is_reparse_or_link(cand) or _is_reparse_or_link(cand.parent):
            continue
        resolved_candidates.append(cand.resolve())
    return list(dict.fromkeys(resolved_candidates))


def _read_state_json(path: Path) -> Any:
    try:
        if _is_reparse_or_link(path) or _is_reparse_or_link(path.parent):
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return None


def _session_tickets(session_id, state_dir=None, statuses=("PENDING",), extra_plan_dirs=()):
    if not valid_session_id(session_id):
        return
    seen = set()
    for directory in candidate_dispatch_plan_dirs(state_dir, extra_plan_dirs):
        pointer = _read_state_json(directory / f"session_{session_id}_pending.json")
        preferred = pointer.get("ticket_id") if isinstance(pointer, dict) else None
        paths = ([directory / (preferred + ".json")] if valid_session_id(preferred) else [])
        paths += sorted(directory.glob("*.json"))
        for path in paths:
            key = os.path.normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            ticket = _read_state_json(path)
            if not isinstance(ticket, dict):
                continue
            tid = ticket.get("ticket_id")
            if (not valid_session_id(tid) or path.name != tid + ".json"
                    or ticket.get("session_id") != session_id or ticket.get("status") not in statuses):
                continue
            try:
                if not float(ticket["expires_at"]) > time.time():
                    continue
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
            yield path, ticket


def find_session_pending_ticket(session_id, state_dir=None):
    return next((ticket for _, ticket in _session_tickets(session_id, state_dir)), None)


def find_session_pending_tickets(session_id, state_dir=None):
    return [ticket for _, ticket in _session_tickets(session_id, state_dir)]


def find_session_approved_tickets(session_id, state_dir=None, *, ticket_id=None, channel=None, extra_plan_dirs=()):
    return [ticket for _, ticket in _session_tickets(session_id, state_dir, ("APPROVED",), extra_plan_dirs)
            if (ticket_id is None or ticket.get("ticket_id") == ticket_id)
            and (channel is None or any(isinstance(row, dict) and row.get("channel") == channel
                                        for row in ticket.get("streams", [])))]


def basename_any_os(raw_path: str) -> str:
    clean = str(raw_path or "").strip().rstrip("/\\")
    if not clean:
        return ""
    return re.split(r"[/\\]", clean)[-1]


def format_card_detail_link(detail_path: str) -> str:
    clean = str(detail_path or "").replace("\r\n", "\n").replace("\r", " ").replace("\n", " ").strip()
    if not clean:
        return ""
    name = basename_any_os(clean) or clean
    if clean.startswith("<"):
        return clean
    posix = clean.replace("\\", "/")
    encoded = (
        posix.replace("%", "%25")
        .replace(" ", "%20")
        .replace("(", "%28")
        .replace(")", "%29")
        .replace("[", "%5B")
        .replace("]", "%5D")
        .replace("#", "%23")
        .replace("?", "%3F")
    )
    if re.match(r"^[A-Za-z]:/", posix):
        uri = "file:///" + encoded
    elif posix.startswith("//"):
        uri = "file:" + encoded
    elif posix.startswith("/"):
        uri = "file://" + encoded
    else:
        return clean
    return f"[{name}]({uri})"


def _sync_handed_off_text(raw: str) -> str:
    updated = str(raw or "").replace(
        "\n\n选项：① 批准当前计划（推荐）；② 微调引擎、挡位或超时后批准；③ 暂缓并交接。",
        "\n\n当前状态：已登记交接（HANDED_OFF）；新会话复核后可选：① 批准当前计划；② 微调引擎、挡位或超时后批准；③ 暂缓并交接。",
    )
    updated = re.sub(r"(状态\s*[:：]\s*)PENDING\b", r"\1HANDED_OFF", updated)
    updated = re.sub(r"(?im)^(\s*status\s*:\s*)PENDING\b", r"\1HANDED_OFF", updated)
    return updated


def _sync_handed_off_card_markdown(card_path: Path) -> None:
    if _is_reparse_or_link(card_path) or not card_path.is_file():
        return
    try:
        raw_card = card_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return
    updated = _sync_handed_off_text(raw_card)
    if updated != raw_card:
        with contextlib.suppress(OSError):
            atomic_write_text(card_path, updated if updated.endswith("\n") else updated + "\n")


def handoff_session_pending_tickets(session_id, state_dir=None):
    """保留票据全部绑定字段，只转换状态；回执仍供新会话恢复。"""
    tickets = []
    for path, _ in _session_tickets(session_id, state_dir, ("PENDING", "HANDED_OFF")):
        with session_state_lock(path.parent, "plan_dir"), session_state_lock(path.parent, path.stem):
            ticket = _read_state_json(path)
            if (not isinstance(ticket, dict) or ticket.get("status") not in ("PENDING", "HANDED_OFF")
                    or ticket.get("session_id") != session_id or ticket.get("ticket_id") != path.stem):
                continue
            try:
                if not float(ticket["expires_at"]) > time.time():
                    continue
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
            if ticket["status"] == "PENDING":
                ticket["status"] = "HANDED_OFF"
                atomic_write_json(path, ticket)
            if ticket["status"] == "HANDED_OFF":
                _sync_handed_off_card_markdown(path.parent / f"{ticket['ticket_id']}_card.md")
                pointer_path = path.parent / f"session_{session_id}_pending.json"
                pointer = _read_state_json(pointer_path)
                if isinstance(pointer, dict) and pointer.get("ticket_id") == ticket["ticket_id"] and pointer.get("status") != "HANDED_OFF":
                    pointer["status"] = "HANDED_OFF"
                    atomic_write_json(pointer_path, pointer)
            tickets.append(ticket)
    return tickets


def _heavy_completion_paths(session_id, state_dir=None):
    if valid_session_id(session_id):
        for directory in dict.fromkeys(path.parent for path in candidate_dispatch_plan_dirs(state_dir)):
            yield directory / "heavy_completions" / (session_id + ".json")


def load_unreported_heavy_completions(session_id, state_dir=None):
    entries, seen = [], set()
    for path in _heavy_completion_paths(session_id, state_dir):
        rows = _read_state_json(path)
        for entry in rows if isinstance(rows, list) else []:
            if not isinstance(entry, dict) or entry.get("reported_to_user") is not False:
                continue
            key = entry.get("run_id") or json.dumps(entry, sort_keys=True)
            if key not in seen:
                seen.add(key)
                entries.append(entry)
    return entries


def mark_heavy_completions_reported(session_id, state_dir=None):
    for path in _heavy_completion_paths(session_id, state_dir):
        if not path.is_file():
            continue
        with session_state_lock(path.parent, session_id):
            rows = _read_state_json(path)
            if not isinstance(rows, list):
                continue
            changed = False
            for entry in rows:
                if isinstance(entry, dict) and entry.get("reported_to_user") is False:
                    entry["reported_to_user"] = True
                    changed = True
            if changed:
                atomic_write_json(path, rows)


def _resolve_cross_os_detail_file(detail_file: str, ticket_id: str = "") -> Tuple[str, bool]:
    """解析明细卡文件路径；仅当路径属跨 OS 格式且原路径不可达时，尝试 Windows<->WSL 互转与候选目录核对。"""
    clean = str(detail_file or "").strip()
    if not clean:
        return "", False
    try:
        direct = Path(clean)
        if (not _is_reparse_or_link(direct)) and direct.is_file():
            return clean, True
    except OSError:
        pass
    is_win_abs = bool(re.match(r"^[A-Za-z]:[/\\]", clean))
    m_wsl = re.match(r"^/mnt/([A-Za-z])/(.*)$", clean)
    is_cross_os = (is_win_abs and os.name != "nt") or (bool(m_wsl) and os.name == "nt")
    if not is_cross_os:
        return clean, False
    candidates: List[Path] = []
    if is_win_abs and os.name != "nt":
        drive = clean[0].lower()
        rest = clean[3:].replace("\\", "/")
        candidates.append(Path(f"/mnt/{drive}/{rest}"))
    elif m_wsl and os.name == "nt":
        drive = m_wsl.group(1).upper()
        rest = m_wsl.group(2).replace("/", "\\")
        candidates.append(Path(f"{drive}:\\{rest}"))
    base_name = basename_any_os(clean)
    if base_name and re.fullmatch(r"[A-Za-z0-9._-]{1,160}", base_name):
        for plan_dir in candidate_dispatch_plan_dirs():
            candidates.append(plan_dir / base_name)
    for cand in candidates:
        try:
            if (not _is_reparse_or_link(cand)) and cand.is_file():
                return str(cand), True
        except OSError:
            continue
    return clean, False


def render_pending_workflow_block(ticket, for_handoff=False) -> str:
    """只渲染磁盘字段；有落盘明细文件时优先输出精简总览卡与可点击链接，缺失信息明确标注。"""
    def value(raw):
        if raw is None or raw == "":
            return "未记录"
        if isinstance(raw, bool):
            return "是" if raw else "否"
        if isinstance(raw, (dict, list)):
            return json.dumps(raw, ensure_ascii=False, sort_keys=True)
        return str(raw)

    def cell(raw):
        return value(raw).replace("|", r"\|").replace("\r", " ").replace("\n", " ")

    raw_detail_file = str(ticket.get("card_detail_file") or "").strip()
    detail_file, detail_exists = _resolve_cross_os_detail_file(raw_detail_file, str(ticket.get("ticket_id") or ""))
    tid_text = value(ticket.get("ticket_id"))
    status_text = value(ticket.get("status"))
    ledger = ticket.get("capability_ledger") or {}

    if detail_file and detail_exists:
        title = (
            "【新对话待选重负载工作流精简总览与明细链接块】"
            if for_handoff
            else "【未决重负载工作流专项计划（压缩前待确认方案，非回读摘要——请向用户展示精简总览卡与完整 14 项明细链接，勿在对话中展开 14 项长表）】"
        )
        lines = [title, "票据：" + tid_text, "状态：" + status_text]
        detail_name = basename_any_os(detail_file)
        compact_stored = str(ticket.get("compact_summary_card") or "").strip()
        if compact_stored and status_text == "HANDED_OFF":
            compact_stored = _sync_handed_off_text(compact_stored)
        overview_rows_present = (
            bool(compact_stored)
            and "| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |" in compact_stored
        )
        compact_required = [
            ticket.get("workflow_bg") or ticket.get("workflow_background"),
            ticket.get("workflow_goal"),
            ticket.get("workflow_impact"),
            ledger.get("last_online_refresh_at"),
            ledger.get("local_bench_verified_at"),
            detail_name,
        ]
        if status_text in ("PENDING", "HANDED_OFF", "未记录"):
            compact_required += ["①", "②", "③", "批准当前计划", "微调", "暂缓", "交接"]
        for stream in ticket.get("streams", []):
            if isinstance(stream, dict):
                compact_required.append(stream.get("stream_id"))
        if overview_rows_present and all(
            raw is not None and raw != "" and (str(raw) in compact_stored or cell(raw) in compact_stored)
            for raw in compact_required
        ):
            lines.append(compact_stored)
            if "恢复命令：plan --resume-ticket" not in compact_stored:
                lines.append("恢复命令：plan --resume-ticket " + tid_text)
            return "\n".join(lines)

        lines += [
            "工作流背景：" + value(ticket.get("workflow_bg") or ticket.get("workflow_background")),
            "要做什么与后续计划：" + value(ticket.get("workflow_goal")),
            "预计后果与边界影响：" + value(ticket.get("workflow_impact")),
            "",
            "| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |",
            "| --- | --- | --- | --- |",
        ]
        for stream in ticket.get("streams", []):
            if not isinstance(stream, dict):
                continue
            avail = value(stream.get("currently_available"))
            if stream.get("unavailable_reason"):
                avail += "（" + str(stream.get("unavailable_reason")) + "）"
            lines.append(
                "| "
                + " | ".join(
                    cell(part)
                    for part in (
                        stream.get("stream_id"),
                        value(stream.get("channel")) + "；" + value(stream.get("client")) + "；" + value(stream.get("agent_type")),
                        value(stream.get("model_display") or stream.get("model")) + "；" + value(stream.get("tier")),
                        avail + "；" + value(stream.get("quota_scheme")) + "；" + value(stream.get("quota_summary")),
                    )
                )
                + " |"
            )
        lines += [
            "",
            "离线性能记录更新时间：在线刷新 " + value(ledger.get("last_online_refresh_at"))
            + "；本机实测 " + value(ledger.get("local_bench_verified_at")),
            "完整 14 项参数明细：" + format_card_detail_link(detail_file),
            (
                "当前状态：已登记交接（HANDED_OFF）；新会话复核后可选：① 批准当前计划；② 微调引擎、挡位或超时后批准；③ 暂缓并交接。"
                if status_text == "HANDED_OFF"
                else "选项卡：① 批准当前计划（推荐）；② 微调引擎、挡位或超时后批准；③ 暂缓并交接。"
            ),
            "恢复命令：plan --resume-ticket " + tid_text,
        ]
        return "\n".join(lines)

    title = ("【新对话待选重负载工作流详述与选项卡块】" if for_handoff else
             "【未决重负载工作流专项计划（压缩前待确认方案，非回读摘要——请向用户完整详述工作流背景、要做什么、预计后果、14项全字段参数表及选项卡）】")
    lines = [title, "票据：" + tid_text, "状态：" + status_text]
    stored = ticket.get("handoff_prompt_block" if for_handoff else "human_popup_markdown")
    if stored:
        lines += [str(stored)]
        required = [ticket.get("workflow_bg") or ticket.get("workflow_background"),
                    ticket.get("workflow_goal"), ticket.get("workflow_impact"),
                    ledger.get("last_online_refresh_at"), ledger.get("local_bench_verified_at"), "①", "②", "③"]
        for stream in ticket.get("streams", []):
            if isinstance(stream, dict):
                required += [stream.get("stream_id"), stream.get("receipt_location")]
        old_table = any(len(re.split(r"(?<!\\)\|", line.strip())[1:-1]) == 14
                        for line in str(stored).splitlines() if line.strip().startswith("|"))
        if not old_table and all(raw is not None and raw != "" and (str(raw) in str(stored) or cell(raw) in str(stored))
                                 for raw in required):
            if "恢复命令：plan --resume-ticket" not in str(stored):
                lines.append("恢复命令：plan --resume-ticket " + tid_text)
            return "\n".join(lines)
    lines += ["工作流背景：" + value(ticket.get("workflow_bg") or ticket.get("workflow_background")),
              "要做什么与后续计划：" + value(ticket.get("workflow_goal")),
              "预计后果与边界影响：" + value(ticket.get("workflow_impact"))]
    columns = ["流号", "子任务与范围", "通道", "客户端及子代理类型", "模型名字", "挡位", "上下文窗口",
               "是否无头", "是否净室", "访问模式、写入范围与验收命令", "内联与超时", "当前是否可用",
               "本机配置额度类型与当前状态", "回执或子代理日志位置"]
    for stream in ticket.get("streams", []):
        if not isinstance(stream, dict):
            continue
        clean_room_val = value(stream.get("clean_room"))
        if not stream.get("clean_room") and stream.get("isolation") in ("full", "no_skills", "hooks_only"):
            clean_room_val += f"（隔离：{stream['isolation']}）"
        fields = [stream.get("stream_id"), value(stream.get("subtask")) + "；范围：" + value(stream.get("target")),
                  stream.get("channel"), value(stream.get("client")) + "；" + value(stream.get("agent_type")),
                  stream.get("model_display") or stream.get("model"), stream.get("tier"), stream.get("context_window"),
                  stream.get("headless"), clean_room_val,
                  value(stream.get("access_mode")) + "；写入范围：" + value(stream.get("write_scope"))
                  + "；验收命令：" + value(stream.get("verify_cmd")),
                  "内联：" + value(stream.get("inline_readonly")) + "；超时：" + value(stream.get("timeout")),
                  value(stream.get("currently_available")) + "；" + value(stream.get("unavailable_reason")),
                  value(stream.get("quota_scheme")) + "；" + value(stream.get("quota_summary")), stream.get("receipt_location")]
        lines += ["", "#### 流 " + value(stream.get("stream_id")) + "（14 项完整参数明细）", "",
                  "| 序号 | 参数项 | 当前值 |", "| --- | --- | --- |"]
        lines.extend("| " + str(index) + " | " + name + " | " + cell(field) + " |"
                     for index, (name, field) in enumerate(zip(columns, fields), 1))
    lines += ["离线性能记录更新时间：在线刷新 " + value(ledger.get("last_online_refresh_at"))
              + "；本机实测 " + value(ledger.get("local_bench_verified_at")),
              "选项卡：① 批准当前计划（推荐）；② 微调引擎、挡位或超时后批准；③ 暂缓并交接。",
              "恢复命令：plan --resume-ticket " + tid_text]
    return "\n".join(lines)


atomic_write_text = _fs_safety.atomic_write_text


def atomic_write_json(target_path: Path, data: Dict[str, Any]) -> None:
    """原子写入 JSON 状态文件"""
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(target_path, text)


session_state_lock = _fs_safety.session_state_lock


_try_unlink_unheld_lock = _fs_safety.try_unlink_unheld_lock


def sweep_stale_lock_files(state_root: Path, anchor_dir: Optional[Path] = None) -> int:
    """按受控周期低频清理超过保留天数的空闲 .lock 文件，不触碰任何原话归档与状态 JSON。"""
    try:
        if not state_root.is_dir() or _is_reparse_or_link(state_root):
            return 0
        guard_cfg = get_runtime_tunables("loop_guards", anchor_dir)
        log_cfg = guard_cfg.get("lifecycle_log") if isinstance(guard_cfg.get("lifecycle_log"), dict) else {}
        raw_days = (
            log_cfg.get("stale_lock_cleanup_days")
            if "stale_lock_cleanup_days" in log_cfg
            else guard_cfg.get("stale_lock_cleanup_days")
        )
        raw_interval = (
            log_cfg.get("stale_lock_sweep_interval_sec")
            if "stale_lock_sweep_interval_sec" in log_cfg
            else guard_cfg.get("stale_lock_sweep_interval_sec")
        )
        cleanup_days = _coerce_pos_int(raw_days, 7, minimum=1)
        sweep_interval = _coerce_pos_int(raw_interval, 86400, minimum=60)
        now = time.time()
        stamp = state_root / ".lock_sweep.stamp"
        try:
            if stamp.is_file() and not _is_reparse_or_link(stamp) and (now - stamp.stat().st_mtime) < sweep_interval:
                return 0
        except OSError:
            pass
        with contextlib.suppress(OSError):
            atomic_write_text(stamp, f"{int(now)}\n")
        cutoff = now - cleanup_days * 86400
        scan_dirs = [state_root]
        for sub_name in ("handoff", "session-context", "user-words", "dispatch_plans", "heavy_completions"):
            sub_dir = state_root / sub_name
            if sub_dir.is_dir() and not _is_reparse_or_link(sub_dir):
                scan_dirs.append(sub_dir)
        removed = 0
        for folder in scan_dirs:
            with contextlib.suppress(OSError):
                for entry in os.scandir(folder):
                    name = entry.name
                    is_dot_lock = name.startswith(".") and name.endswith(".lock") and len(name) > 6
                    is_legacy_lock = name.startswith("session-") and name.endswith(".json.lock")
                    if not (is_dot_lock or is_legacy_lock):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    if not is_legacy_lock and st.st_mtime > cutoff:
                        continue
                    if _try_unlink_unheld_lock(Path(entry.path)):
                        removed += 1
        return removed
    except OSError:
        return 0


def current_iso_time() -> str:
    """返回带本机时区偏移的 ISO-8601 时间戳"""
    return datetime.now(timezone.utc).astimezone().isoformat()


def _effective_log_limits(anchor_dir: Optional[Path] = None) -> Tuple[int, int]:
    log_cfg = get_runtime_tunables("loop_guards", anchor_dir).get("lifecycle_log")
    max_bytes = MAX_LOG_BYTES
    trim_bytes = TRIM_LOG_BYTES
    if isinstance(log_cfg, dict):
        if MAX_LOG_BYTES == _DEFAULT_MAX_LOG_BYTES and "max_log_mib" in log_cfg:
            max_bytes = _coerce_pos_int(log_cfg.get("max_log_mib"), 2, minimum=1) * 1024 * 1024
        if TRIM_LOG_BYTES == _DEFAULT_TRIM_LOG_BYTES and "trim_to_mib" in log_cfg:
            trim_bytes = _coerce_pos_int(log_cfg.get("trim_to_mib"), 1, minimum=1) * 1024 * 1024
    trim_bytes = min(trim_bytes, max_bytes)
    return max_bytes, trim_bytes


_LIFECYCLE_SECRET_RE = re.compile(
    r"(?i)(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|Basic\s+[A-Za-z0-9+/=_-]{6,}|"
    r"sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,})"
)
_LIFECYCLE_KV_SECRET_RE = re.compile(
    r"(?i)(\b(?:[A-Za-z0-9_]+[_-](?:api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"secret[_-]?access[_-]?key|secret[_-]?key|secret|token|password|passwd)|"
    r"api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|secret[_-]?access[_-]?key|"
    r"secret[_-]?key|client[_-]?secret|password|passwd|token)"
    r"(?:\\*[\"\'])?\s*[=:]\s*(?:\\*[\"\'])?)([^\s\"\'\\,;})]+)"
)
_LIFECYCLE_SECRET_KEY_RE = re.compile(
    r"(?i)(?:auth|api.?key|secret|password|passwd|credential|bearer|(?:^|[_-])token(?:$|[_-])|[a-z0-9]Token$|^token[A-Z])"
)
_LIFECYCLE_SAFE_LITERALS = frozenset({"via-router", "present", "missing", "placeholder", "n/a", "true", "false", "none", "null"})


def _scrub_kv_secret_match(match: re.Match[str]) -> str:
    prefix, raw_val = match.group(1), match.group(2)
    stripped = raw_val.strip()
    is_pwd_key = bool(re.search(r"(?i)passw(?:or)?d", prefix))
    if len(stripped) < 6 or (stripped.isdigit() and not is_pwd_key) or stripped.lower() in _LIFECYCLE_SAFE_LITERALS:
        return match.group(0)
    return f"{prefix}<redacted>"


def _scrub_lifecycle_entry(val: Any) -> Any:
    if isinstance(val, str):
        val = re.sub(r"(?i)(https?://)([^/\s@]+@)", r"\1<redacted>@", val)
        val = re.sub(
            r"(?i)([?&](?:[A-Za-z0-9_]+[_-])?(?:key|api_key|token|access_token|secret|secret_access_key|password|passwd)=)[^&\s\"'\\]+",
            r"\1<redacted>",
            val,
        )
        val = _LIFECYCLE_KV_SECRET_RE.sub(_scrub_kv_secret_match, val)
        return _LIFECYCLE_SECRET_RE.sub("<redacted>", val)
    if isinstance(val, dict):
        scrubbed: Dict[Any, Any] = {}
        for k, v in val.items():
            is_pwd = isinstance(k, str) and bool(re.search(r"(?i)passw(?:or)?d", k))
            if (
                isinstance(k, str) and _LIFECYCLE_SECRET_KEY_RE.search(k)
                and isinstance(v, str) and len(v.strip()) >= 6
                and (not v.strip().isdigit() or is_pwd)
                and v.strip().lower() not in _LIFECYCLE_SAFE_LITERALS
            ):
                scrubbed[k] = "<redacted>"
            else:
                scrubbed[k] = _scrub_lifecycle_entry(v)
        return scrubbed
    if isinstance(val, list):
        return [_scrub_lifecycle_entry(v) for v in val]
    if isinstance(val, tuple):
        return tuple(_scrub_lifecycle_entry(v) for v in val)
    return val


def append_jsonl_log(log_path: Path, entry: Dict[str, Any]) -> None:
    """日志共用独立锁；裁剪仅读尾部，避免大会话日志全量入内存或并发丢行。"""
    try:
        max_bytes, trim_bytes = _effective_log_limits()
        row = json.dumps(_scrub_lifecycle_entry(entry), ensure_ascii=False) + "\n"
        lock_id = "log-" + hashlib.sha256(_canonical_path_key(log_path).encode("utf-8")).hexdigest()[:24]
        with session_state_lock(log_path.parent, lock_id):
            if _is_reparse_or_link(log_path):
                raise OSError(errno.EINVAL, "拒绝通过符号链接或重解析点写入日志文件", str(log_path))
            if log_path.exists() and log_path.stat().st_size > max_bytes:
                with log_path.open("rb") as stream:
                    stream.seek(-trim_bytes, os.SEEK_END)
                    stream.readline()  # 丢掉裁剪边界上的半行。
                    tail = stream.read(trim_bytes)
                atomic_write_text(log_path, tail.decode("utf-8", errors="replace"))
            with log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(row)
    except (OSError, ValueError, TypeError, RecursionError):
        # 日志不应影响主协议；状态事务的异常由调用方另行处理。
        pass


def append_lifecycle_log(
    hooks_dir: Path,
    event_name: str,
    conversation_id: str,
    message: str,
    payload_summary: Optional[Dict[str, Any]] = None,
) -> None:
    """向 logs/lifecycle.log 追加结构化 JSONL 日志，按配置上限自动滚动裁剪防无限膨胀"""
    _, logs_dir = resolve_data_dirs(hooks_dir)
    append_jsonl_log(logs_dir / "lifecycle.log", {
        "timestamp": current_iso_time(), "event": event_name,
        "conversationId": conversation_id, "message": message, "summary": payload_summary or {},
    })


# ③ 用户正文、控制指令与 Markdown 归档格式

def _extract_outer_user_request(text: str) -> Optional[str]:
    """
    安全提取最外层 <USER_REQUEST>...</USER_REQUEST> 内容。
    只识别块首宿主封装，保留用户正文中间出现的同名标签。
    """
    open_tag = "<USER_REQUEST>"
    close_tag = "</USER_REQUEST>"
    start_idx = 0
    # 宿主可能在用户请求之前附加一个独立的 ephemeral 封装。
    ephemeral = "<EPHEMERAL_MESSAGE>"
    if text.startswith(ephemeral):
        end_ephemeral = text.find("</EPHEMERAL_MESSAGE>")
        if end_ephemeral >= 0:
            start_idx = end_ephemeral + len("</EPHEMERAL_MESSAGE>")
            start_idx += len(text[start_idx:]) - len(text[start_idx:].lstrip())
    if not text.startswith(open_tag, start_idx):
        return None

    body_start = start_idx + len(open_tag)
    end_idx = text.rfind(close_tag, body_start)
    while end_idx >= body_start:
        # 只移动索引，不反复复制越来越长的后缀，避免多闭标签输入退化为平方耗时。
        trailer_idx = end_idx + len(close_tag)
        while trailer_idx < len(text) and text[trailer_idx].isspace():
            trailer_idx += 1
        if trailer_idx == len(text) or text.startswith(("<ADDITIONAL_METADATA>", "<USER_SETTINGS_CHANGE>"), trailer_idx):
            return text[body_start:end_idx].strip()
        end_idx = text.rfind(close_tag, body_start, end_idx)
    return text[body_start:].strip()


def clean_user_content(raw_content: str, step_type: str = "USER_INPUT") -> str:
    """清洗并提取纯净的用户原始输入或父代理任务书"""
    if not isinstance(raw_content, str) or not raw_content:
        return ""

    text = raw_content.strip()

    if step_type == "USER_INPUT":
        extracted = _extract_outer_user_request(text)
        if extracted is not None:
            return extracted

        return text

    if step_type == "SYSTEM_MESSAGE":
        # 仅提取非 system 发送的跨代理任务消息（如父代理派发给子代理的指令）
        msg_match = re.search(r"\[Message\][^\n]*?\bsender=([^\s]+)[^\n]*?\bcontent=", text)
        if msg_match:
            sender = msg_match.group(1).strip().lower()
            if sender == "system" or re.search(r"(?:^|/)task-[A-Za-z0-9_-]+", sender):
                return ""
            content_start = msg_match.end()
            close_idx = text.rfind("</SYSTEM_MESSAGE>", content_start)
            body = text[content_start:close_idx] if close_idx != -1 else text[content_start:]
            return body.strip()
        for noise in SYSTEM_NOISE_PATTERNS:
            if noise in text:
                return ""
        return ""

    return ""


def agy_goal_command(text: str) -> Optional[bool]:
    if GOAL_CANCEL_RE.match(text):
        return False
    if GOAL_START_RE.match(text):
        return True
    return None


def markdown_unquoted_lines(text: str) -> Iterator[str]:
    """返回 Markdown 正文行，排除引用、围栏代码块和缩进代码块。"""
    fence = ""
    for line in text.splitlines():
        match = MARKDOWN_FENCE_RE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ""
        elif match:
            fence = match[1]
        elif not line.startswith(("    ", "\t")) and not line.lstrip().startswith(">"):
            yield line.strip()
            continue
        yield ""


def has_goal_cancel_marker(text: str) -> bool:
    marker = "<!-- GOAL_CANCELLED -->"
    lines = text.rstrip().splitlines()
    return bool(lines and lines[-1] == marker and list(markdown_unquoted_lines(text.rstrip()))[-1] == marker)


def summarize_subagent_return(
    content: str,
    max_chars: Optional[int] = None,
    head_ratio: Optional[float] = None,
) -> str:
    """对子代理返回正文做首尾双端摘要裁剪，兼顾开篇核查范围与末尾缺陷表／结论。"""
    cleaned = str(content or "").strip()
    cap = MAX_ARCHIVE_SUBAGENT_ITEM_CHARS if max_chars is None else max(120, int(max_chars))
    if len(cleaned) <= cap:
        return cleaned
    ratio = _coerce_ratio(head_ratio, 0.58) if head_ratio is not None else 0.58
    marker = "\n...[中间部分已省略，详见子代理日志或源码]...\n"
    usable = max(cap - len(marker), 120)
    head_len = max(int(usable * ratio), 60)
    tail_len = max(usable - head_len, 50)
    return f"{cleaned[:head_len].rstrip()}{marker}{cleaned[-tail_len:].lstrip()}"


class _HarnessArchiveCollector:
    """把关联命令的完成输出与异步回执转换成原生归档条目。"""

    def __init__(self, transcript_path: Path, policy: Dict[str, Any]):
        self.path = transcript_path
        self.policy = policy
        self.cwd = ""
        self.calls: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.unidentified: List[Dict[str, Any]] = []
        self.items: List[Dict[str, Any]] = []
        self.seen: Set[str] = set()

    @staticmethod
    def _objects(raw, parse_text=True):
        if isinstance(raw, dict):
            yield raw
            for val in raw.values():
                yield from _HarnessArchiveCollector._objects(val, parse_text)
        elif isinstance(raw, list):
            for val in raw:
                yield from _HarnessArchiveCollector._objects(val, parse_text)
        elif parse_text and isinstance(raw, str):
            decoder = json.JSONDecoder()
            pos = 0
            while pos < len(raw):
                start = raw.find("{", pos)
                if start < 0:
                    break
                try:
                    obj, used = decoder.raw_decode(raw[start:])
                except (ValueError, RecursionError):
                    pos = start + 1
                    continue
                yield from _HarnessArchiveCollector._objects(obj)
                pos = start + used

    def _file(self, raw_path, cwd=""):
        if not isinstance(raw_path, str) or not raw_path:
            return None
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (Path(cwd) if cwd else self.path.parent) / path
        try:
            if path.is_file() and path.stat().st_size <= MAX_HOOK_INPUT_CHARS:
                return path, path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            pass
        return None

    def _complete(self, meta, raw, index, timestamp):
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        objects = list(self._objects(raw))
        if not text.strip() or re.search(
                r"Script running|Process running|running with (?:cell|session|id)|running in (?:the )?background|background task.*started", text, re.I):
            return
        if any(obj.get("session_id") and obj.get("exit_code") is None for obj in objects):
            return
        receipt = next((obj.get("receipt_file") for obj in objects if obj.get("receipt_file")), "")
        file_data = self._file(receipt or meta.get("output"), meta.get("cwd", ""))
        if file_data:
            receipt, text = str(file_data[0]), file_data[1]
            objects = list(self._objects(text))
        models = list(dict.fromkeys(str(obj["model"]) for obj in objects if obj.get("model")))
        summaries = []
        for obj in objects:
            for key in ("summary", "content", "evidence", "unverified", "error"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    summaries.append(val)
                elif isinstance(val, list) and all(isinstance(item, str) for item in val):
                    summaries.extend(val)
        if not summaries:
            outputs = [obj.get("output") or obj.get("stdout") for obj in objects]
            summaries = [val for val in outputs if isinstance(val, str) and val.strip()] or [text]
        body = "\n".join(dict.fromkeys(summaries))
        if models:
            body = "模型：" + "、".join(models) + "\n" + body
        if receipt:
            body += "\n回执：" + str(receipt)
        fingerprint = hashlib.sha256((meta["command"] + str(receipt) + body).encode("utf-8")).hexdigest()
        if fingerprint in self.seen:
            return
        self.seen.add(fingerprint)
        self.items.append({"step_index": index, "created_at": timestamp,
                           "agent_id": "cae-harness-" + fingerprint[:12],
                           "label": "cae-harness " + meta["kind"] + (" " + ", ".join(models) if models else ""),
                           "model": ", ".join(models), "receipt_file": str(receipt),
                           "log_path": str(receipt or meta.get("log_path") or self.path), "raw_body": body,
                           "content": summarize_subagent_return(body, self.policy["archive_subagent_item_chars"],
                                                                 self.policy["subagent_head_ratio"]), "truncated": False})

    def feed(self, entry, index):
        timestamp = str(entry.get("timestamp") or entry.get("created_at") or "")
        context = entry.get("payload") if entry.get("type") in ("session_meta", "turn_context") else entry
        if isinstance(context, dict) and isinstance(context.get("cwd"), str):
            self.cwd = context["cwd"]
        # 协议结构只遍历对象，不将人类消息或工具正文中的 JSON 示例解释成调用。
        objects = list(self._objects(entry, parse_text=False))
        for obj in objects:
            kind = obj.get("type")
            is_call = kind in ("function_call", "custom_tool_call", "tool_use") or (
                obj.get("name") and any(key in obj for key in ("args", "arguments", "input")))
            if not is_call:
                continue
            args = obj.get("arguments", obj.get("args", obj.get("input", {})))
            text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            parsed = _parse_codex_json_maybe(args)
            parsed = parsed if isinstance(parsed, dict) else {}
            call_id = _tool_call_id(obj, is_call=True)
            task_id = str(parsed.get("session_id") or parsed.get("cell_id") or parsed.get("CommandId")
                          or parsed.get("command_id") or parsed.get("task_id") or "")
            if call_id and task_id in self.tasks:
                self.calls[call_id] = self.tasks[task_id]
                continue
            match = re.search(r"cae_harness\.py[\"']?\s+(task|gate|audit|batch)\b", text)
            if not match:
                continue
            command = str(parsed.get("command") or parsed.get("cmd") or parsed.get("CommandLine") or text)
            output = re.search(r"--output(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))", command)
            meta = {"command": command, "kind": match[1],
                    "tool": obj.get("name"),
                    "cwd": str(parsed.get("cwd") or parsed.get("workdir") or parsed.get("Cwd") or self.cwd),
                    "output": next((v for v in output.groups() if v), "") if output else ""}
            if call_id:
                self.calls[call_id] = meta
            else:
                self.unidentified.append(meta)
        for obj in objects:
            call_id = _tool_call_id(obj) or str(obj.get("tool_use_id") or "")
            meta = self.calls.get(call_id)
            if not meta and obj is entry and entry.get("type") in ("GENERIC", "TOOL_RESULT", "TOOL_RESPONSE"):
                tool_result = entry.get("tool_result")
                name = entry.get("tool_name") or (tool_result.get("name") if isinstance(tool_result, dict) else None)
                matching = [candidate for candidate in self.unidentified if not name or candidate["tool"] == name]
                if len(matching) == 1:
                    meta = matching[0]
                    self.unidentified.remove(meta)
            if not meta:
                continue
            raw = obj.get("output", obj.get("content", obj.get("stdout")))
            if raw is None and obj is entry.get("tool_result"):
                raw = entry.get("content")
            if raw is None:
                continue
            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            for task in re.findall(r"\btask-[A-Za-z0-9_-]+", text):
                self.tasks[task] = meta
            for task in re.findall(r'(?:session_id|cell ID)[\s":]+([A-Za-z0-9_-]+)', text):
                self.tasks[task] = meta
            self._complete(meta, raw, index, timestamp)
        if entry.get("type") == "SYSTEM_MESSAGE":
            text = str(entry.get("content") or "")
            for task in re.findall(r"\btask-[A-Za-z0-9_-]+", text):
                meta = self.tasks.get(task)
                if not meta:
                    continue
                roots = [self.path.parent, self.path.parent.parent]
                log = next((data for root in roots
                            if (data := self._file(str(root / "tasks" / (task + ".log"))))), None)
                if log:
                    meta = dict(meta, log_path=str(log[0]))
                self._complete(meta, log[1] if log else text, index, timestamp)


def _merge_harness_returns(native, collector, policy):
    return select_recent_subagent_returns(
        sorted(native + collector.items, key=lambda item: item["step_index"]), policy,
        body_getter=lambda item: item.get("raw_body") or item.get("content") or "")


def _unquote_proto_scalar(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            decoded = json.loads(text)
            if isinstance(decoded, str):
                text = decoded
        except (ValueError, RecursionError):
            text = text[1:-1]
    return text.strip()


def format_user_words_markdown(
    conversation_id: str,
    words: List[Dict[str, Any]],
    issues: Optional[List[str]] = None,
    subagent_returns: Optional[List[Dict[str, Any]]] = None,
    policy: Optional[Dict[str, Any]] = None,
) -> str:
    """将提取出的用户原话及最近子代理返回摘要格式化为持久留痕 Markdown 文档"""
    cfg = policy if isinstance(policy, dict) else get_compaction_tunables()
    item_cap = int(cfg.get("archive_subagent_item_chars", MAX_ARCHIVE_SUBAGENT_ITEM_CHARS))
    head_ratio = float(cfg.get("subagent_head_ratio", 0.58))
    recent_returns = list(subagent_returns or [])
    lines = [
        "# 会话用户原话归档",
        "",
        f"- **Conversation ID**: `{conversation_id}`",
        f"- **累计指令轮数**: {len(words)}",
    ]
    if recent_returns:
        lines.append(f"- **最近子代理返回摘要数**: {len(recent_returns)}")
    if issues:
        lines.extend(["- 归档状态：存在缺口，未核实完整性", *[f"  - {issue}" for issue in issues]])
    lines.extend(["", "---", ""])

    for w in words:
        time_str = f" ({w['created_at']})" if w.get("created_at") else ""
        kind_tag = ""
        if w.get("kind") == "ask_question_answer":
            kind_tag = " [选项框回答]"
        elif w.get("kind") == "subagent_message":
            kind_tag = " [父代理任务]"
        if w.get("truncated"):
            kind_tag += " [截断正文待补]"
        if w.get("provenance_verified") is False:
            kind_tag += " [用户角色，来源未核实]"
        lines.append(f"## 第 {w['turn']} 轮指令 [Step {w['step_index']}]{kind_tag}{time_str}")
        if w.get("attachments") and w.get("source_path"):
            lines.append(f"来源分段：`{w['source_path']}`")
        lines.append("")
        lines.append(w["content"])
        lines.append("")
        lines.append("---")
        lines.append("")

    if recent_returns:
        lines.append(
            f"## 附：最近子代理返回摘要（最近 {len(recent_returns)} 条，采纳前须按 R6 核对原文件或子代理日志）"
        )
        lines.append("")
        for idx, item in enumerate(recent_returns, start=1):
            time_str = f" ({item['created_at']})" if item.get("created_at") else ""
            trunc_tag = " [轨迹截断摘要]" if item.get("truncated") else ""
            label = str(item.get("label") or item.get("agent_id") or f"subagent-{idx}").strip()
            lines.append(f"### 子代理返回 #{idx} · {label} [Step {item['step_index']}]{trunc_tag}{time_str}")
            if item.get("agent_id"):
                lines.append(f"- **子代理标识**: `{item['agent_id']}`")
            if item.get("log_path"):
                lines.append(f"- **原始日志**: `{item['log_path']}`")
            lines.append("")
            raw_sub_text = item.get("raw_body") if isinstance(item.get("raw_body"), str) and item.get("raw_body") else item.get("content", "")
            lines.append(summarize_subagent_return(raw_sub_text, item_cap, head_ratio))
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def save_user_words_markdown(
    state_dir: Path,
    conversation_id: str,
    words: List[Dict[str, Any]],
    issues: Optional[List[str]] = None,
    subagent_returns: Optional[List[Dict[str, Any]]] = None,
    policy: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """将用户原话列表与最近子代理返回摘要原子保存至 state/<conversationId>_user_words.md"""
    if not words:
        return None
    if not valid_session_id(conversation_id):
        raise ValueError("会话标识缺失或不合法")
    out_file = state_dir / f"{conversation_id}_user_words.md"
    md_text = format_user_words_markdown(
        conversation_id, words, issues, subagent_returns=subagent_returns, policy=policy
    )
    atomic_write_text(out_file, md_text)
    return out_file


# ④ Antigravity JSONL 解析与截断正文回查

def _load_truncated_contents_map(full_transcript_path: Path, needed_indices: Set[int]) -> Dict[int, str]:
    """
    单次线性扫描 transcript_full.jsonl，按需补全所有被截断步骤的完整 content。
    时间复杂度严格 O(M)，找齐所需步骤后立即提前退出，杜绝 O(N*M) 重复扫描。
    """
    resolved: Dict[int, str] = {}
    if not needed_indices or not full_transcript_path.exists():
        return resolved

    remaining = set(needed_indices)
    try:
        with open(full_transcript_path, "rb") as f:
            for line in f:
                if not remaining:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(decode_jsonl_line(line))
                except (ValueError, RecursionError):
                    continue
                if not isinstance(step, dict):
                    continue
                truncated = step.get("truncated_fields")
                if isinstance(truncated, list) and "content" in truncated:
                    continue
                idx = step.get("step_index")
                if isinstance(idx, int) and idx in remaining:
                    content = step.get("content")
                    if isinstance(content, str):
                        resolved[idx] = content
                        remaining.discard(idx)
    except OSError:
        pass

    return resolved


def _ask_question_title(call: Dict[str, Any]) -> str:
    questions_titles: List[str] = []
    args = call.get("args") or call.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, RecursionError):
            args = None
    qs = args.get("questions") if isinstance(args, dict) else None
    if isinstance(qs, str):
        try:
            qs = json.loads(qs)
        except (ValueError, RecursionError):
            qs = None
    if isinstance(qs, list):
        for q in qs:
            if isinstance(q, dict) and isinstance(q.get("question"), str):
                questions_titles.append(q["question"].strip())
    return " / ".join(questions_titles) or "交互式选项提问"


def _tool_call_id(record: Dict[str, Any], *, is_call: bool = False) -> str:
    keys = ("tool_call_id", "call_id", "toolCallId", "callId")
    if is_call:
        keys += ("id",)
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _clean_generic_tool_answer(raw_output: str) -> str:
    """清洗 ask_question 对应的 GENERIC 步骤输出"""
    if not raw_output:
        return ""
    cleaned = TOOL_TIMING_HEADER_RE.sub("", raw_output).strip()
    if not cleaned or cleaned.startswith("Encountered error in tool execution"):
        return ""
    return cleaned


def _parse_agy_subagent_call(call: Dict[str, Any]) -> Tuple[str, str]:
    args = call.get("args") or call.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, RecursionError):
            args = {}
    if not isinstance(args, dict):
        return "", ""
    type_name = _unquote_proto_scalar(args.get("TypeName") or args.get("typeName") or "")
    role = _unquote_proto_scalar(args.get("Role") or args.get("role") or "")
    return type_name, role


def _extract_agy_subagent_return_body(raw_content: str) -> Tuple[str, str]:
    """解析主会话 SYSTEM_MESSAGE 中的子代理返回标识与正文；后台命令任务与系统通知返回空。"""
    if not isinstance(raw_content, str) or not raw_content:
        return "", ""
    text = raw_content.strip()
    if "BROWSER_HOST_FACTS" in text or any(noise in text for noise in SYSTEM_NOISE_PATTERNS):
        return "", ""
    msg_match = AGY_SYSTEM_MSG_RE.search(text)
    if not msg_match:
        return "", ""
    sender = msg_match.group(1).strip()
    sender_lower = sender.lower()
    if not sender or sender_lower == "system" or "/task-" in sender_lower or sender_lower.startswith("task-"):
        return "", ""
    content_start = msg_match.end()
    close_idx = text.rfind("</SYSTEM_MESSAGE>", content_start)
    body = (text[content_start:close_idx] if close_idx != -1 else text[content_start:]).strip()
    if not body or body.startswith("Task id "):
        return "", ""
    return sender, body


def extract_agy_archive(
    transcript_path_str: Optional[str],
    include_subagent_msgs: bool = True,
    *,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """提取原话、最近子代理返回摘要和来源缺口，仅归档可关联的选项回答。"""
    result: Dict[str, Any] = {
        "words": [],
        "subagent_returns": [],
        "unresolved_steps": [],
        "unverified_answers": [],
        "issues": [],
    }
    if not transcript_path_str:
        result["issues"].append("未提供 transcriptPath")
        return result

    transcript_path = resolve_compact_transcript_path(Path(transcript_path_str).expanduser())
    full_transcript_path = transcript_path.parent / "transcript_full.jsonl"

    raw_candidates: List[Dict[str, Any]] = []
    raw_subagent_returns: List[Dict[str, Any]] = []
    pending_subagent_calls: List[Tuple[str, str, str]] = []
    subagent_registry: Dict[str, Dict[str, str]] = {}
    truncated_needed: Set[int] = set()
    pending_questions: Dict[str, Tuple[int, str]] = {}
    unidentified_questions: Set[int] = set()
    ambiguous_outputs: Dict[int, Set[int]] = {}
    agy_policy = policy if isinstance(policy, dict) else get_compaction_tunables("antigravity")
    harness = _HarnessArchiveCollector(transcript_path, agy_policy)

    try:
        with open(transcript_path, "rb") as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(decode_jsonl_line(line))
                except (ValueError, RecursionError):
                    result["issues"].append(f"{transcript_path.name} 第 {line_num + 1} 行未能解析")
                    continue
                if not isinstance(step, dict):
                    result["issues"].append(f"{transcript_path.name} 第 {line_num + 1} 行缺少步骤对象")
                    continue

                step_type = step.get("type", "")
                source = step.get("source", "")
                step_index = step.get("step_index")
                if not isinstance(step_index, int):
                    step_index = line_num
                created_at = step.get("created_at", "")
                harness.feed(step, step_index)
                truncated_fields = step.get("truncated_fields") or []
                is_content_truncated = isinstance(truncated_fields, list) and "content" in truncated_fields

                if step_type == "PLANNER_RESPONSE":
                    unidentified_questions.clear()
                    calls = step.get("tool_calls")
                    if isinstance(calls, list):
                        for call in calls:
                            if not isinstance(call, dict):
                                continue
                            call_name = call.get("name")
                            if call_name == "invoke_subagent":
                                t_name, role_name = _parse_agy_subagent_call(call)
                                pending_subagent_calls.append(
                                    (_tool_call_id(call, is_call=True), t_name, role_name)
                                )
                                continue
                            if call_name != "ask_question":
                                continue
                            call_id = _tool_call_id(call, is_call=True)
                            if call_id and call_id not in pending_questions:
                                pending_questions[call_id] = (step_index, _ask_question_title(call))
                            else:
                                unidentified_questions.add(step_index)
                    continue

                if step_type in ("GENERIC", "TOOL_RESULT", "TOOL_RESPONSE"):
                    tool_result = step.get("tool_result")
                    tool_result = tool_result if isinstance(tool_result, dict) else step
                    call_id = _tool_call_id(tool_result) or _tool_call_id(step)
                    raw_tool_out = step.get("content") or tool_result.get("output") or ""
                    created_meta = parse_agy_created_subagent(raw_tool_out)
                    if created_meta:
                        sub_cid = str(created_meta.get("conversationId") or "").strip()
                        if sub_cid:
                            matched_idx = next(
                                (idx for idx, item in enumerate(pending_subagent_calls) if call_id and item[0] == call_id),
                                0 if pending_subagent_calls else None,
                            )
                            t_name, role_name = ("", "")
                            if matched_idx is not None:
                                _, t_name, role_name = pending_subagent_calls.pop(matched_idx)
                            subagent_registry[sub_cid] = {
                                "type_name": t_name,
                                "role": role_name,
                                "log_path": str(created_meta.get("logAbsoluteUri") or "").strip(),
                            }
                    named_ask = (tool_result.get("tool_name") or tool_result.get("name")) == "ask_question"
                    question = pending_questions.pop(call_id, None)
                    waiting = {pos for pos in unidentified_questions if pos < step_index} | {
                        pos for pos, _ in pending_questions.values()
                    }
                    if not call_id:
                        unidentified_questions.clear()
                    if step.get("error") or tool_result.get("error"):
                        continue
                    if question is None:
                        if (not call_id and waiting) or named_ask:
                            ambiguous_outputs[step_index] = waiting if not named_ask else waiting | {step_index}
                        continue
                    q_index, q_title = question
                    for positions in ambiguous_outputs.values():
                        positions.discard(q_index)
                    if (tool_result.get("tool_name") or tool_result.get("name")) not in (None, "", "ask_question"):
                        ambiguous_outputs[step_index] = {q_index}
                        continue
                    raw_ans = step.get("content") or tool_result.get("output") or ""
                    if not isinstance(raw_ans, str):
                        raw_ans = ""
                    if is_content_truncated:
                        truncated_needed.add(step_index)
                    raw_candidates.append(
                        {
                            "step_index": step_index,
                            "created_at": created_at,
                            "step_type": "ASK_QUESTION_ANSWER",
                            "question_title": q_title,
                            "raw_content": raw_ans,
                            "needs_full": is_content_truncated,
                        }
                    )
                    continue

                if step_type == "SYSTEM_MESSAGE" and not include_subagent_msgs:
                    raw_sys = step.get("content") if isinstance(step.get("content"), str) else ""
                    is_sys_noise = "BROWSER_HOST_FACTS" in raw_sys or any(
                        noise in raw_sys for noise in SYSTEM_NOISE_PATTERNS
                    )
                    if not is_sys_noise:
                        sender_id, sub_body = _extract_agy_subagent_return_body(raw_sys)
                        valid_sender = bool(
                            sender_id and sub_body and (sender_id in subagent_registry or valid_session_id(sender_id))
                        )
                        if valid_sender or (is_content_truncated and subagent_registry):
                            raw_subagent_returns.append(
                                {
                                    "step_index": step_index,
                                    "created_at": created_at,
                                    "sender": sender_id if valid_sender else "",
                                    "raw_content": raw_sys,
                                    "fallback_body": sub_body if valid_sender else "",
                                    "needs_full": is_content_truncated,
                                }
                            )
                    continue

                is_explicit_user = step_type == "USER_INPUT" or source == "USER_EXPLICIT"
                is_subagent_msg = include_subagent_msgs and step_type == "SYSTEM_MESSAGE"

                if not (is_explicit_user or is_subagent_msg):
                    continue

                raw_content = step.get("content") or ""
                if not isinstance(raw_content, str):
                    result["issues"].append(f"步骤 {step_index} 缺少文本正文")
                    raw_content = ""
                norm_type = "USER_INPUT" if is_explicit_user else "SYSTEM_MESSAGE"
                if is_content_truncated:
                    truncated_needed.add(step_index)

                raw_candidates.append(
                    {
                        "step_index": step_index,
                        "created_at": created_at,
                        "step_type": norm_type,
                        "raw_content": raw_content,
                        "needs_full": is_content_truncated,
                    }
                )
    except OSError as exc:
        result["issues"].append(f"读取 {transcript_path.name} 失败（{exc}）")

    subagent_truncated_needed = {
        item["step_index"] for item in raw_subagent_returns if item["needs_full"]
    }

    # 单次按需补全所有被截断的用户记录与子代理返回（仅用户记录计入 unresolved_steps 完整性门禁）
    full_map = _load_truncated_contents_map(full_transcript_path, truncated_needed | subagent_truncated_needed)
    unresolved_steps = sorted(truncated_needed.difference(full_map))

    resolved_sub_candidates: List[Dict[str, Any]] = []
    for sub_item in raw_subagent_returns:
        s_idx = sub_item["step_index"]
        sender_id = sub_item["sender"]
        body_text = sub_item["fallback_body"]
        still_truncated = bool(sub_item["needs_full"])
        if sub_item["needs_full"] and s_idx in full_map:
            full_sender, full_body = _extract_agy_subagent_return_body(full_map[s_idx])
            if full_sender and full_body and (full_sender in subagent_registry or valid_session_id(full_sender)):
                sender_id = full_sender
                body_text = full_body
                still_truncated = False
            elif sender_id and full_body:
                body_text = full_body
                still_truncated = False
        if not (sender_id and body_text and (sender_id in subagent_registry or valid_session_id(sender_id))):
            continue
        resolved_sub_candidates.append(
            {
                "step_index": s_idx,
                "created_at": sub_item["created_at"],
                "sender": sender_id,
                "body_text": body_text,
                "needs_full": still_truncated,
            }
        )

    def _agy_cand_label(item: Dict[str, Any]) -> str:
        meta = subagent_registry.get(item["sender"], {})
        return f"{meta.get('type_name') or ''} {meta.get('role') or ''} {item['sender']}".strip()

    recent_sub_candidates = select_recent_subagent_returns(
        resolved_sub_candidates,
        agy_policy,
        body_getter=lambda item: item.get("body_text") or "",
        label_getter=_agy_cand_label,
    )

    results: List[Dict[str, Any]] = []
    turn_counter = 0

    for item in raw_candidates:
        s_idx = item["step_index"]
        raw_text = full_map.get(s_idx, item["raw_content"]) if item["needs_full"] else item["raw_content"]
        s_type = item["step_type"]

        if s_type == "ASK_QUESTION_ANSWER":
            ans_text = _clean_generic_tool_answer(raw_text)
            if not ans_text:
                continue
            q_title = item.get("question_title", "交互式选项提问")
            cleaned = f"【选项框回答 · {q_title}】\n{ans_text}"
            kind = "ask_question_answer"
        else:
            cleaned = clean_user_content(raw_text, s_type)
            if not cleaned:
                continue
            kind = "user_input" if s_type == "USER_INPUT" else "subagent_message"

        turn_counter += 1
        results.append(
            {
                "turn": turn_counter,
                "step_index": s_idx,
                "created_at": item["created_at"],
                "kind": kind,
                "content": cleaned,
                "truncated": s_idx in unresolved_steps,
            }
        )

    subagent_returns: List[Dict[str, Any]] = []
    agy_archive_cap = int(agy_policy["archive_subagent_item_chars"])
    agy_head_ratio = float(agy_policy["subagent_head_ratio"])
    for sub_item in recent_sub_candidates:
        s_idx = sub_item["step_index"]
        sender_id = sub_item["sender"]
        body_text = sub_item["body_text"]
        still_truncated = bool(sub_item["needs_full"])
        meta = subagent_registry.get(sender_id, {})
        type_name = meta.get("type_name") or ""
        role_name = meta.get("role") or ""
        if type_name and role_name:
            label = f"{type_name} ({role_name})"
        else:
            label = role_name or type_name or sender_id
        subagent_returns.append(
            {
                "step_index": s_idx,
                "created_at": sub_item["created_at"],
                "agent_id": sender_id,
                "label": label,
                "log_path": meta.get("log_path") or "",
                "raw_body": body_text,
                "content": summarize_subagent_return(body_text, agy_archive_cap, agy_head_ratio),
                "truncated": still_truncated,
            }
        )

    result.update(
        words=results,
        subagent_returns=_merge_harness_returns(subagent_returns, harness, agy_policy),
        unresolved_steps=unresolved_steps,
        unverified_answers=sorted(index for index, questions in ambiguous_outputs.items() if questions),
    )
    return result


def extract_user_words_with_status(
    transcript_path_str: Optional[str],
    include_subagent_msgs: bool = True,
) -> Tuple[List[Dict[str, Any]], List[int]]:
    result = extract_agy_archive(transcript_path_str, include_subagent_msgs)
    return result["words"], result["unresolved_steps"]


def extract_user_words(
    transcript_path_str: Optional[str],
    include_subagent_msgs: bool = True,
) -> List[Dict[str, Any]]:
    """
    从 transcript.jsonl 提取会话全部历史用户原话（含被截断长指令还原与 ask_question 选项回答）。
    """
    words, _ = extract_user_words_with_status(transcript_path_str, include_subagent_msgs=include_subagent_msgs)
    return words


# ⑤ Codex 路径、主会话身份与分段发现

def strip_win_extended_prefix(path_str: str) -> str:
    """剥离 Windows \\\\?\\ 扩展长度路径前缀，统一跨端路径比较与读取"""
    raw = str(path_str or "").strip()
    if raw[:8].casefold() == "\\\\?\\unc\\":
        return "\\\\" + raw[8:]
    if raw.startswith("\\\\?\\"):
        return raw[4:]
    return raw


def _canonical_path_key(p: Path) -> str:
    try:
        s = str(p.resolve())
    except (OSError, RuntimeError, ValueError):
        s = os.path.abspath(str(p))
    return s.casefold() if os.name == "nt" else s


read_bounded_first_line = _fs_safety.read_bounded_first_line


def read_codex_rollout_meta(rollout_path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """仅读取 rollout 首行元数据，区分主会话与子代理会话"""
    try:
        first_line, too_long = read_bounded_first_line(rollout_path)
        if too_long:
            return None, f"首行元数据未写完或超长: {rollout_path}"
        if not first_line:
            return None, f"空会话文件: {rollout_path}"
        line_text = decode_jsonl_line(first_line).lstrip("\ufeff")
        if not first_line.endswith(b"\n") and not line_text.endswith("}"):
            return None, f"首行元数据未写完或超长: {rollout_path}"
        entry = json.loads(line_text)
        payload = entry.get("payload") if isinstance(entry, dict) and entry.get("type") == "session_meta" else None
        if not isinstance(payload, dict):
            return None, f"首条记录非 session_meta: {rollout_path}"
        sid = payload.get("id")
        cwd = payload.get("cwd") or ""
        source = payload.get("source")
        if not isinstance(sid, str) or not sid.strip():
            return None, f"session_meta 缺少有效 id: {rollout_path}"
        root_id = payload.get("session_id")
        thread_source = payload.get("thread_source")
        agent_role = payload.get("agent_role")
        agent_nickname = payload.get("agent_nickname")
        if (
            source in ("subagent", "internal")
            or (isinstance(source, dict) and ("subagent" in source or "internal" in source))
            or (isinstance(root_id, str) and root_id and root_id != sid)
            or payload.get("parent_thread_id")
            or thread_source in ("subagent", "guardian_review", "memory_consolidation")
            or (isinstance(agent_role, str) and bool(agent_role.strip()))
            or (isinstance(agent_nickname, str) and bool(agent_nickname.strip()))
        ):
            source_kind = "subagent"
        elif isinstance(source, str) and source.casefold() in CODEX_MAIN_SOURCES:
            source_kind = "main"
        else:
            source_kind = "unknown"
        return {"id": sid.strip(), "cwd": str(cwd), "source_kind": source_kind}, None
    except (OSError, ValueError, RecursionError) as exc:
        return None, f"读取 Codex 会话元数据失败 ({rollout_path}): {exc}"


def codex_hook_session(
    payload: Dict[str, Any],
) -> Tuple[str, Optional[Path], Optional[str], bool]:
    """供两个钩子共用，返回会话标识、轨迹、未核实原因与子会话跳过标记。"""
    sid = str(payload.get("session_id") or "").strip()
    agent_id = payload.get("agent_id")
    if agent_id is not None and str(agent_id) != sid:
        return sid, None, None, True
    if not valid_session_id(sid):
        return sid, None, "hook input 缺少有效 session_id", False
    raw_path = strip_win_extended_prefix(str(payload.get("transcript_path") or ""))
    in_harness = os.environ.get("CAE_HARNESS_DEPTH", "").strip() not in ("", "0")
    if not raw_path:
        if in_harness:
            return sid, None, None, True
        return sid, None, "未提供 transcript_path，无法核实主会话身份", False
    transcript = Path(raw_path).expanduser()
    if not transcript.is_file() and in_harness:
        return sid, None, None, True
    meta, error = read_codex_rollout_meta(transcript)
    if meta and meta["source_kind"] == "subagent":
        return sid, None, None, True
    if not meta:
        return sid, None, error or "主会话身份未核实", False
    if meta["id"] != sid:
        return sid, None, f"轨迹 session_id 不匹配（期望 {sid}，实际 {meta['id']}）", False
    if meta["source_kind"] != "main":
        return sid, None, "轨迹来源未知，主会话身份未核实", False
    return sid, transcript, None, False


def _scan_codex_home_rollouts(codex_root: Path) -> List[Path]:
    """扫描 <codex_root>/sessions/YYYY/MM/DD 与 archived_sessions 下的 rollout-*.jsonl"""
    candidates: List[Path] = []
    sessions_dir = codex_root / "sessions"
    if sessions_dir.is_dir():
        try:
            for y_dir in sessions_dir.iterdir():
                if not (y_dir.name.isdigit() and len(y_dir.name) == 4 and y_dir.is_dir()):
                    continue
                for m_dir in y_dir.iterdir():
                    if not (m_dir.name.isdigit() and len(m_dir.name) == 2 and m_dir.is_dir()):
                        continue
                    for d_dir in m_dir.iterdir():
                        if not (d_dir.name.isdigit() and len(d_dir.name) == 2 and d_dir.is_dir()):
                            continue
                        for f in d_dir.iterdir():
                            if f.name.startswith("rollout-") and f.name.endswith(".jsonl") and f.is_file():
                                candidates.append(f)
        except OSError:
            pass

    archived_dir = codex_root / "archived_sessions"
    if archived_dir.is_dir():
        try:
            for f in archived_dir.iterdir():
                if f.name.startswith("rollout-") and f.name.endswith(".jsonl") and f.is_file():
                    candidates.append(f)
        except OSError:
            pass

    return candidates


def discover_codex_session_segments(
    transcript_path_str: str,
    session_id: Optional[str] = None,
) -> Tuple[List[Path], Optional[str], List[str]]:
    """发现同一 session_id 的全部主会话 rollout 分段并按时间序排列"""
    issues: List[str] = []
    cleaned_str = strip_win_extended_prefix(os.path.expanduser(str(transcript_path_str or "").strip()))
    if not cleaned_str:
        return [], session_id, ["未提供 transcript_path"]

    primary = Path(cleaned_str)
    if not primary.is_file():
        return [], session_id, [f"transcript_path 不存在: {cleaned_str}"]

    meta, err = read_codex_rollout_meta(primary)
    if err or not meta:
        return [], session_id, [err or f"无法识别元数据: {cleaned_str}"]
    if meta["source_kind"] == "subagent":
        return [], meta["id"], ["子代理 rollout 会话不执行主会话原话提取"]
    if meta["source_kind"] != "main":
        return [], session_id, [f"轨迹来源未知，无法核实主会话身份: {primary}"]
    if session_id and session_id.strip() and meta["id"] != session_id.strip():
        return [], session_id.strip(), [f"session_id 不匹配: 期望 {session_id.strip()}，实际 {meta['id']}"]

    sid = meta["id"]
    seen_keys = {_canonical_path_key(primary)}
    segments: List[Path] = [primary]

    # 向上寻找所属的 sessions 或 archived_sessions 父目录以定位 codex_root
    cur = primary.parent
    codex_root: Optional[Path] = None
    while cur != cur.parent:
        if cur.name in ("sessions", "archived_sessions"):
            codex_root = cur.parent
            break
        cur = cur.parent

    if codex_root is not None:
        for cand in _scan_codex_home_rollouts(codex_root):
            if sid not in cand.name:
                continue
            ckey = _canonical_path_key(cand)
            if ckey in seen_keys:
                continue
            c_meta, c_err = read_codex_rollout_meta(cand)
            if c_err:
                issues.append(c_err)
                continue
            if c_meta and c_meta["id"] == sid:
                if c_meta["source_kind"] == "main":
                    seen_keys.add(ckey)
                    segments.append(cand)
                elif c_meta["source_kind"] == "unknown":
                    issues.append(f"来源未核实的同 ID 分段: {cand}")

    segments.sort(key=lambda p: (p.name, str(p)))
    return segments, sid, issues


# ⑥ Codex 来源合并、完整性校验与归档发布

def _extract_codex_record(
    entry: Dict[str, Any],
    fallback_step: int,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """保留记录标识及内容项来源，供跨分段核对。"""
    if not isinstance(entry, dict) or entry.get("type") != "response_item":
        return None, None
    payload = entry.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message" or payload.get("role") != "user":
        return None, None

    content = payload.get("content")
    meta_pass = payload.get("internal_chat_message_metadata_passthrough")
    raw_meta_items = meta_pass.get("content_item_metadata") if isinstance(meta_pass, dict) else None
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
    origins = ["unknown"] * len(blocks)
    if isinstance(raw_meta_items, list) and len(raw_meta_items) == len(blocks):
        for index, item in enumerate(raw_meta_items):
            prov = item.get("provenance") if isinstance(item, dict) else None
            origin = prov.get("type") if isinstance(prov, dict) else None
            if origin == "user":
                origins[index] = "user"
            elif isinstance(origin, str) and origin in CODEX_SYSTEM_PROVENANCE:
                origins[index] = "system"

    rec_id: Optional[str] = None
    for cand_id in (payload.get("id"), entry.get("uuid")):
        if isinstance(cand_id, str) and cand_id.strip():
            rec_id = f"id:{cand_id.strip()}"
            break
    if rec_id is None and isinstance(entry.get("ordinal"), int) and isinstance(entry.get("timestamp"), str):
        identity_fields = [entry["ordinal"], entry["timestamp"], blocks]
        canonical_json = json.dumps(identity_fields, sort_keys=True, ensure_ascii=True)
        rec_id = "record:" + hashlib.sha256(canonical_json.encode("ascii")).hexdigest()
    if rec_id is None and origins and all(origin == "system" for origin in origins):
        return None, None

    return rec_id, {
        "step_index": entry.get("ordinal") if isinstance(entry.get("ordinal"), int) else fallback_step,
        "created_at": str(entry.get("timestamp") or ""),
        "blocks": blocks,
        "origins": origins,
        "content_signature": hashlib.sha256(
            json.dumps(blocks, ensure_ascii=True, sort_keys=True).encode("ascii")
        ).hexdigest(),
    }


def _codex_word_from_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    blocks = record["blocks"]
    kept_blocks: List[str] = []
    attachments: List[str] = []
    missing_content = not blocks
    provenance_verified = bool(blocks)
    for block, origin in zip(blocks, record["origins"]):
        if origin in ("system", "conflict"):
            continue
        if origin != "user":
            provenance_verified = False
        block_type = block.get("type") if isinstance(block, dict) else None
        if block_type in ("input_text", "text") and isinstance(block.get("text"), str):
            kept_blocks.append(block["text"])
        elif isinstance(block_type, str) and block_type in CODEX_ATTACHMENTS:
            label = CODEX_ATTACHMENTS[block_type]
            attachments.append(label)
            kept_blocks.append(f"【{label}附件，内容见原始轨迹】")
            if not any(block.get(key) for key in (
                "image_url", "file_id", "audio_url", "audio", "data", "file_data", "file_url",
            )):
                missing_content = True
        else:
            missing_content = True
    if missing_content:
        kept_blocks.append("【存在无法归档的输入内容，待核对原始轨迹】")
    if not kept_blocks:
        return None

    merged_text = "\n\n".join(kept_blocks)
    kind = "user_input"
    if provenance_verified and not missing_content and not attachments and re.fullmatch(
        r"<send_user_message_question_reply>[\s\S]+</send_user_message_question_reply>",
        merged_text.strip(),
    ):
        kind = "ask_question_answer"

    return {
        "step_index": record["step_index"],
        "created_at": record["created_at"],
        "source_path": record["source_path"],
        "kind": kind,
        "content": merged_text,
        "provenance_verified": provenance_verified,
        "attachments": attachments,
        "content_verified": not missing_content,
    }


def _merge_codex_record_origins(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Tuple[bool, bool]:
    """返回是否纠正了归属、是否遇到新的明确来源冲突。"""
    previous = existing["origins"]
    updated = list(previous)
    corrected = False
    conflict = False
    for index, (old, new) in enumerate(zip(previous, incoming["origins"])):
        if old == "unknown":
            updated[index] = new
            corrected |= new == "system"
        elif old != "conflict" and new not in ("unknown", old):
            updated[index] = "conflict"
            conflict = True
    if updated != previous:
        existing["origins"] = updated
        existing["source_path"] = incoming["source_path"]
        existing["step_index"] = incoming["step_index"]
        existing["created_at"] = incoming["created_at"]
    return corrected, conflict


def _parse_codex_json_maybe(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except (ValueError, RecursionError):
                return None
    return None


def _codex_output_to_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts: List[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                for key in ("text", "output_text", "content"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        parts.append(val.strip())
                        break
        return "\n".join(parts).strip()
    return ""


def _resolve_codex_subagent_log(agent_id: str, rollouts: List[Path], main_keys: Set[str]) -> str:
    aid = agent_id.strip()
    if len(aid) < 6:
        return ""
    for cand in rollouts:
        if aid in cand.name and _canonical_path_key(cand) not in main_keys:
            return str(cand)
    return ""


def _format_codex_subagent_label(meta: Dict[str, str], fallback_id: str) -> str:
    agent_type = meta.get("agent_type") or ""
    nickname = meta.get("nickname") or ""
    task_hint = meta.get("task_hint") or ""
    if agent_type and nickname and nickname != agent_type:
        return f"{agent_type} ({nickname})"
    if agent_type and task_hint:
        return f"{agent_type} ({task_hint})"
    return agent_type or nickname or task_hint or fallback_id or "codex-subagent"


def _extract_codex_completed_subagent_items(
    call_meta: Dict[str, Any],
    raw_output: Any,
    agent_registry: Dict[str, Dict[str, str]],
    rollouts: List[Path],
    main_keys: Set[str],
) -> List[Tuple[str, str, str, str]]:
    """解析 Codex spawn_agent / wait_agent 工具输出，跳过纯启动句柄回执，仅返回已完成的子代理正文。"""
    out_text = _codex_output_to_text(raw_output)
    parsed = raw_output if isinstance(raw_output, dict) else _parse_codex_json_maybe(out_text)
    pending_states = {"pending_init", "pending", "running", "in_progress", "shutdown", "not_found", "timed_out"}
    emitted: List[Tuple[str, str, str, str]] = []

    def _register_agent(aid: str, nickname: str = "", agent_type: str = "") -> Dict[str, str]:
        clean_id = aid.strip()
        if not clean_id:
            return {
                "agent_type": str(call_meta.get("agent_type") or ""),
                "nickname": nickname.strip(),
                "task_hint": str(call_meta.get("task_hint") or ""),
                "log_path": "",
            }
        entry = agent_registry.setdefault(clean_id, {})
        if agent_type.strip() and not entry.get("agent_type"):
            entry["agent_type"] = agent_type.strip()
        if call_meta.get("agent_type") and not entry.get("agent_type"):
            entry["agent_type"] = str(call_meta["agent_type"])
        if nickname.strip() and not entry.get("nickname"):
            entry["nickname"] = nickname.strip()
        if call_meta.get("task_hint") and not entry.get("task_hint"):
            entry["task_hint"] = str(call_meta["task_hint"])
        if not entry.get("log_path"):
            entry["log_path"] = _resolve_codex_subagent_log(clean_id, rollouts, main_keys)
        return entry

    if isinstance(parsed, dict):
        top_aid = str(parsed.get("agent_id") or parsed.get("id") or "").strip()
        top_nick = str(parsed.get("nickname") or parsed.get("role") or "").strip()
        top_type = str(parsed.get("agent_type") or parsed.get("subagent_type") or "").strip()
        if top_aid:
            _register_agent(top_aid, nickname=top_nick, agent_type=top_type)

        status_val = parsed.get("status")
        if isinstance(status_val, dict):
            for sub_aid_raw, state_item in status_val.items():
                sub_aid = str(sub_aid_raw or "").strip()
                body = ""
                if isinstance(state_item, dict):
                    for key in ("completed", "final_output", "output", "result", "message", "content"):
                        cand = _codex_output_to_text(state_item.get(key))
                        if cand:
                            body = cand
                            break
                elif isinstance(state_item, str):
                    cand = state_item.strip()
                    if cand and cand.lower() not in pending_states:
                        body = cand
                if body:
                    meta = _register_agent(sub_aid)
                    emitted.append((sub_aid, _format_codex_subagent_label(meta, sub_aid), meta.get("log_path") or "", body))
            return emitted

        for list_key in ("results", "outputs"):
            items_list = parsed.get(list_key)
            if isinstance(items_list, list):
                for elem in items_list:
                    if not isinstance(elem, dict):
                        continue
                    elem_status = str(elem.get("status") or "").strip().lower()
                    if elem_status in pending_states or elem.get("error"):
                        continue
                    sub_aid = str(elem.get("agent_id") or elem.get("id") or top_aid).strip()
                    for key in ("completed", "final_output", "output", "result", "content", "message"):
                        cand = _codex_output_to_text(elem.get(key))
                        if cand:
                            meta = _register_agent(
                                sub_aid,
                                nickname=str(elem.get("nickname") or ""),
                                agent_type=str(elem.get("agent_type") or ""),
                            )
                            emitted.append((sub_aid, _format_codex_subagent_label(meta, sub_aid), meta.get("log_path") or "", cand))
                            break
                return emitted

        status_str = str(status_val or "").strip().lower()
        if status_str in pending_states or status_str in ("errored", "failed") or parsed.get("error"):
            return []

        for key in ("completed", "final_output", "output", "result", "response", "content", "message"):
            cand = _codex_output_to_text(parsed.get(key))
            if cand:
                target_ids = call_meta.get("target_ids") or []
                aid = top_aid or (target_ids[0] if len(target_ids) == 1 else "")
                meta = _register_agent(aid, nickname=top_nick, agent_type=top_type)
                emitted.append((aid, _format_codex_subagent_label(meta, aid), meta.get("log_path") or "", cand))
                return emitted
        return []

    if out_text:
        lowered = out_text.lower()
        if lowered.startswith(("error:", "timed out", "agent spawned", "spawned agent")):
            return []
        if call_meta.get("tool") in CODEX_SUBAGENT_WAIT_TOOLS or call_meta.get("tool") == "agent":
            target_ids = call_meta.get("target_ids") or []
            aid = target_ids[0] if len(target_ids) == 1 else ""
            meta = _register_agent(aid)
            emitted.append((aid, _format_codex_subagent_label(meta, aid), meta.get("log_path") or "", out_text))
    return emitted


def extract_codex_session_words(
    transcript_path_str: str,
    session_id: Optional[str] = None,
    *,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """合并解析同一 Codex 主会话所有 rollout 分段的用户原话与最近子代理返回摘要"""
    segments, resolved_sid, issues = discover_codex_session_segments(transcript_path_str, session_id=session_id)
    if not segments:
        return {
            "session_id": resolved_sid or session_id or "unknown",
            "words": [],
            "subagent_returns": [],
            "segments": [],
            "sources": [],
            "issues": issues or ["未找到可读的 Codex 会话分段"],
            "complete": False,
            "provenance_corrected": False,
        }

    codex_root: Optional[Path] = None
    cur_dir = segments[0].parent
    while cur_dir != cur_dir.parent:
        if cur_dir.name in ("sessions", "archived_sessions"):
            codex_root = cur_dir.parent
            break
        cur_dir = cur_dir.parent
    all_rollouts = _scan_codex_home_rollouts(codex_root) if codex_root is not None else []
    main_segment_keys = {_canonical_path_key(p) for p in segments}

    records: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    records_by_id: Dict[str, List[int]] = {}
    provenance_corrected = False
    pending_subagent_calls: Dict[str, Dict[str, Any]] = {}
    codex_agent_registry: Dict[str, Dict[str, str]] = {}
    seen_subagent_keys: Set[Tuple[str, str, str]] = set()
    raw_subagent_returns: List[Dict[str, Any]] = []

    codex_policy = policy if isinstance(policy, dict) else get_compaction_tunables("codex")
    codex_archive_cap = int(codex_policy["archive_subagent_item_chars"])
    codex_head_ratio = float(codex_policy["subagent_head_ratio"])
    harness = _HarnessArchiveCollector(segments[0], codex_policy)
    global_step_counter = 0

    for seg_path in segments:
        harness.path = seg_path
        digest = hashlib.sha256()
        offset = 0
        bad_lines = 0

        try:
            with open(seg_path, "rb") as f:
                before = os.fstat(f.fileno())
                initial_size = before.st_size
                for raw_line in f:
                    global_step_counter += 1
                    if not raw_line.strip():
                        offset += len(raw_line)
                        digest.update(raw_line)
                        continue
                    try:
                        entry = json.loads(decode_jsonl_line(raw_line))
                    except (ValueError, RecursionError):
                        if not raw_line.endswith(b"\n"):
                            issues.append(f"末尾存在未写完记录: {seg_path.name}")
                            break
                        bad_lines += 1
                        offset += len(raw_line)
                        digest.update(raw_line)
                        continue
                    offset += len(raw_line)
                    digest.update(raw_line)
                    if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
                        bad_lines += 1
                        continue
                    harness.feed(entry, global_step_counter)
                    item_obj = (
                        entry.get("payload")
                        if entry.get("type") == "response_item"
                        else (entry.get("event") if entry.get("type") == "event" else None)
                    )
                    if isinstance(item_obj, dict):
                        item_type = item_obj.get("type")
                        step_no = entry.get("ordinal") if isinstance(entry.get("ordinal"), int) else global_step_counter
                        ts_str = str(entry.get("timestamp") or "")
                        if item_type in ("function_call", "custom_tool_call"):
                            raw_tool = str(item_obj.get("name") or item_obj.get("tool_name") or "").strip()
                            base_tool = raw_tool.rsplit(".", 1)[-1]
                            call_id = str(item_obj.get("call_id") or item_obj.get("id") or "").strip()
                            if call_id and base_tool in (CODEX_SUBAGENT_SPAWN_TOOLS | CODEX_SUBAGENT_WAIT_TOOLS):
                                raw_args = item_obj.get("arguments") if "arguments" in item_obj else item_obj.get("input")
                                parsed_args = _parse_codex_json_maybe(raw_args)
                                args_dict = parsed_args if isinstance(parsed_args, dict) else {}
                                agent_type = str(
                                    args_dict.get("agent_type")
                                    or args_dict.get("subagent_type")
                                    or args_dict.get("role")
                                    or args_dict.get("name")
                                    or ""
                                ).strip()
                                task_hint = str(args_dict.get("description") or args_dict.get("title") or "").strip()
                                if len(task_hint) > 48:
                                    task_hint = task_hint[:45].rstrip() + "..."
                                target_ids: List[str] = []
                                for id_key in ("ids", "agent_ids"):
                                    id_list = args_dict.get(id_key)
                                    if isinstance(id_list, list):
                                        target_ids.extend(str(x).strip() for x in id_list if str(x).strip())
                                single_id = args_dict.get("agent_id")
                                if isinstance(single_id, str) and single_id.strip():
                                    target_ids.append(single_id.strip())
                                pending_subagent_calls[call_id] = {
                                    "tool": base_tool,
                                    "agent_type": agent_type,
                                    "task_hint": task_hint,
                                    "target_ids": target_ids,
                                }
                        elif item_type in ("function_call_output", "custom_tool_call_output"):
                            call_id = str(item_obj.get("call_id") or item_obj.get("id") or "").strip()
                            call_meta = pending_subagent_calls.get(call_id)
                            if call_meta is not None:
                                completed_items = _extract_codex_completed_subagent_items(
                                    call_meta,
                                    item_obj.get("output"),
                                    codex_agent_registry,
                                    all_rollouts,
                                    main_segment_keys,
                                )
                                for sub_aid, sub_label, sub_log, sub_body in completed_items:
                                    body_hash = hashlib.sha256(sub_body.encode("utf-8")).hexdigest()[:24]
                                    dedup_key = (call_id, sub_aid, body_hash)
                                    if dedup_key in seen_subagent_keys:
                                        continue
                                    seen_subagent_keys.add(dedup_key)
                                    raw_subagent_returns.append(
                                        {
                                            "step_index": step_no,
                                            "created_at": ts_str,
                                            "agent_id": sub_aid,
                                            "label": sub_label,
                                            "log_path": sub_log,
                                            "raw_body": sub_body,
                                            "content": summarize_subagent_return(
                                                sub_body, codex_archive_cap, codex_head_ratio
                                            ),
                                            "truncated": False,
                                        }
                                    )
                    rec_id, record = _extract_codex_record(entry, fallback_step=global_step_counter)
                    if record is None:
                        continue
                    record["source_path"] = str(seg_path)
                    if rec_id is not None:
                        indices = records_by_id.setdefault(rec_id, [])
                        matching = next((i for i in indices if records[i]["content_signature"] ==
                                         record["content_signature"]), None)
                        if matching is not None:
                            corrected, conflict = _merge_codex_record_origins(records[matching], record)
                            provenance_corrected |= corrected or conflict
                            if conflict:
                                issues.append(f"同一记录标识的内容项存在用户与系统来源冲突（{rec_id}），冲突项未归档")
                            continue
                        indices.append(len(records))
                    records.append(record)
                after = os.fstat(f.fileno())
            if bad_lines:
                issues.append(f"存在损坏 JSON 行 ({bad_lines} 行): {seg_path.name}")
            current = seg_path.stat()
            if (offset != initial_size
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                issues.append(f"读取时分段未稳定或不完整: {seg_path.name}")
            sources.append(
                {
                    "path": str(seg_path),
                    "complete_offset": offset,
                    "observed_size": initial_size,
                    "sha256": digest.hexdigest(),
                }
            )
        except OSError as exc:
            issues.append(f"读取分段失败 ({seg_path.name}): {exc}")

    rendered = [_codex_word_from_record(record) for record in records]
    words = [word for word in rendered if word is not None]
    for rec_id, indices in records_by_id.items():
        if len(indices) > 1:
            issues.append(f"同一记录标识的内容冲突（{rec_id}），冲突版本的来源待核对")
    unknown_source_rows = sum(not item["provenance_verified"] for item in words)
    incomplete_rows = sum(not item["content_verified"] for item in words)
    if unknown_source_rows:
        issues.append(f"{unknown_source_rows} 条用户角色记录的来源未核实")
    if incomplete_rows:
        issues.append(f"{incomplete_rows} 条记录含无法核实的输入内容")
    for index, item in enumerate(words, start=1):
        item["turn"] = index

    return {
        "session_id": resolved_sid or session_id or "unknown",
        "words": words,
        "subagent_returns": select_recent_subagent_returns(
            sorted(raw_subagent_returns + harness.items, key=lambda item: item["step_index"]),
            codex_policy, body_getter=lambda item: item.get("raw_body") or item.get("content") or ""
        ),
        "segments": [str(p) for p in segments],
        "sources": sources,
        "issues": issues,
        "complete": len(issues) == 0,
        "provenance_corrected": provenance_corrected,
    }


def _read_codex_publication(md_path: Path, meta_path: Path, sid: str,
                            archive_format: str = CODEX_ARCHIVE_FORMAT) -> Dict[str, Any]:
    saved: Dict[str, Any] = {
        "file": str(md_path), "available": False, "manifest_verified": False, "verified": False,
        "preserved": False, "count": None, "last_time": None, "sources": [],
    }
    try:
        body = md_path.read_bytes()
        saved["body_sha256"] = hashlib.sha256(body).hexdigest()
        body.decode("utf-8")
    except FileNotFoundError:
        if meta_path.is_file():
            saved["issue"] = "校验清单存在，归档正文缺失"
        return saved
    except (OSError, UnicodeDecodeError):
        saved["issue"] = "归档正文无法读取"
        return saved

    saved["available"] = True
    digest = saved["body_sha256"]
    try:
        manifest = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError
        sources = manifest.get("sources")
        count = manifest.get("count")
        last_time = manifest.get("last_time")
        manifest_format = manifest.get("format")
        legacy = archive_format == CODEX_ARCHIVE_FORMAT and manifest_format == CODEX_LEGACY_ARCHIVE_FORMAT
        complete = manifest.get("complete")
        if (
            (manifest_format != archive_format and not legacy) or manifest.get("id") != sid
            or manifest.get("file") != str(md_path) or not isinstance(manifest.get("sha256"), str)
            or not SHA256_RE.fullmatch(manifest["sha256"])
            or type(count) is not int or count < 0
            or (last_time is not None and not isinstance(last_time, str))
            or (not legacy and type(complete) is not bool)
            or not isinstance(sources, list) or not sources
        ):
            raise ValueError
        for source in sources:
            if (
                not isinstance(source, dict) or not isinstance(source.get("path"), str) or not source["path"]
                or not isinstance(source.get("sha256"), str) or not SHA256_RE.fullmatch(source["sha256"])
                or type(source.get("complete_offset")) is not int or type(source.get("observed_size")) is not int
                or source["complete_offset"] < 0 or source["observed_size"] < 0
                or source["complete_offset"] > source["observed_size"]
                or (not legacy and complete and source["complete_offset"] != source["observed_size"])
                or (legacy and source["complete_offset"] == 0)
            ):
                raise ValueError
    except (ValueError, OSError, RecursionError):
        saved["issue"] = "校验清单格式无效或无法读取，归档完整性未验证"
        return saved
    if manifest["sha256"] != digest:
        saved.update(issue="归档正文哈希与校验清单不匹配", previous_manifest_sources=sources)
        if legacy:
            saved["legacy_hash_mismatch"] = True
        return saved
    saved.update(manifest_verified=True, verified=not legacy and complete is True, count=count, last_time=last_time,
                 sources=sources, sha256=digest)
    if legacy:
        saved.update(legacy_manifest=True, issue="旧版归档正文哈希与清单一致，记录完整性未验证")
    elif not complete:
        saved["issue"] = "此归档保存的是上次可恢复的部分记录"
    return saved


def _missing_codex_source_coverage(previous: List[Dict[str, Any]], current: List[Dict[str, Any]]) -> List[str]:
    """先按原路径匹配，再核对已迁移或并入其它分段的原始字节前缀。"""
    indexed = {_canonical_path_key(Path(item["path"])): item for item in current}
    prefix_digests: Dict[Tuple[str, int], Optional[str]] = {}
    missing: List[str] = []
    for old in previous:
        key = _canonical_path_key(Path(old["path"]))
        same_path = indexed.get(key)
        candidates = ([same_path] + [item for item in current if item is not same_path]
                      if same_path is not None else current)
        length = old["complete_offset"]
        for candidate in candidates:
            if candidate["complete_offset"] < length:
                continue
            if candidate["complete_offset"] == length:
                digest = candidate["sha256"]
            else:
                cache_key = candidate["path"], length
                if cache_key not in prefix_digests:
                    digest_prefix = hashlib.sha256()
                    remaining = length
                    try:
                        with Path(candidate["path"]).open("rb") as stream:
                            while remaining:
                                data = stream.read(min(remaining, 1024 * 1024))
                                if not data:
                                    break
                                remaining -= len(data)
                                digest_prefix.update(data)
                    except OSError:
                        remaining = -1
                    prefix_digests[cache_key] = digest_prefix.hexdigest() if remaining == 0 else None
                digest = prefix_digests[cache_key]
            if digest == old["sha256"]:
                break
        else:
            missing.append(old["path"])
    return missing


def refresh_codex_session_archive(
    transcript_path_str: str,
    user_words_dir: Path,
    session_id: str,
    *,
    identity_error: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """串行发布原话与校验清单。发布中断时，按磁盘上实际可核验的一对文件判定。"""
    sid = session_id.strip()
    if not valid_session_id(sid):
        return {"complete": False, "words": [], "subagent_returns": [], "issues": [identity_error or "无效的 session_id"],
                "saved": {}, "meta_path": "", "segments": []}
    md_path = user_words_dir / f"{sid}.md"
    meta_path = user_words_dir / f"{sid}.json"
    if identity_error and not md_path.exists() and not meta_path.exists():
        return {"complete": False, "words": [], "subagent_returns": [], "issues": [identity_error], "saved": {},
                "meta_path": str(meta_path), "segments": []}
    codex_policy = policy if isinstance(policy, dict) else get_compaction_tunables("codex")
    with session_state_lock(user_words_dir, sid):
        previous = _read_codex_publication(md_path, meta_path, sid)
        parsed = (
            {"complete": False, "issues": [identity_error], "words": [], "subagent_returns": [],
             "sources": [], "segments": [], "provenance_corrected": False}
            if identity_error else extract_codex_session_words(transcript_path_str, session_id=sid, policy=codex_policy)
        )
        words = parsed["words"]
        subagent_returns = parsed.get("subagent_returns") or []
        issues = list(parsed["issues"])
        complete = bool(parsed["complete"])
        saved = previous
        recorded_sources = previous.get("previous_manifest_sources") or previous["sources"]
        missing = _missing_codex_source_coverage(recorded_sources, parsed["sources"])
        if missing:
            complete = False
            issues.append("本次无法核对已有归档分段的来源覆盖：" + "、".join(missing))
        can_update_partial = parsed["provenance_corrected"] or (bool(words) and (not previous["available"] or (
            previous["manifest_verified"] and not previous["verified"] and len(words) >= previous["count"]
        )))
        md_text = format_user_words_markdown(
            sid, words, issues, subagent_returns=subagent_returns, policy=codex_policy
        )
        md_sha = hashlib.sha256(md_text.encode("utf-8")).hexdigest()
        body_sha = previous.get("body_sha256")
        can_restore_manifest = bool(body_sha and body_sha == md_sha and parsed["sources"])
        if previous.get("legacy_hash_mismatch"):
            can_restore_manifest = False
        unresolved_body = bool(body_sha and not previous["manifest_verified"] and not can_restore_manifest)
        if unresolved_body:
            complete = False
            issues.append("未能证明本次重建覆盖现有归档正文，已保留原正文")
        if previous.get("legacy_manifest"):
            should_publish = complete
        else:
            should_publish = complete or can_update_partial or can_restore_manifest
        if not missing and not unresolved_body and should_publish:
            try:
                manifest = {
                    "format": CODEX_ARCHIVE_FORMAT, "id": sid, "file": str(md_path), "count": len(words),
                    "last_time": words[-1]["created_at"] if words else None, "sources": parsed["sources"],
                    "sha256": md_sha, "complete": complete,
                }
                if body_sha != md_sha:
                    atomic_write_text(md_path, md_text)
                atomic_write_json(meta_path, manifest)
            except OSError as exc:
                complete = False
                issues.append(f"归档发布中断（{exc}）")
            saved = _read_codex_publication(md_path, meta_path, sid)
            if not saved["manifest_verified"]:
                complete = False
        if not complete and saved["verified"] and saved.get("sha256") == previous.get("sha256"):
            saved["preserved"] = True
        if saved.get("issue"):
            issues.append(saved["issue"])

    saved.pop("body_sha256", None)
    saved.pop("previous_manifest_sources", None)
    saved.pop("legacy_hash_mismatch", None)
    return {
        "complete": complete,
        "words": words,
        "subagent_returns": subagent_returns,
        "issues": issues,
        "saved": saved,
        "meta_path": str(meta_path),
        "segments": parsed["segments"],
    }


# ⑦ Claude Code 主轨迹与原话归档

CLAUDE_ARCHIVE_FORMAT = "user-guard-claude-v1"
CLAUDE_INJECTED_TAG_RE = re.compile(
    r"<(ide_opened_file|ide_selection|ide_diagnostics|system-reminder|task-notification)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)


def claude_hook_session(payload: Dict[str, Any]) -> Tuple[str, Optional[Path], str, bool]:
    """定位显式主轨迹；子代理支线返回 skip=True，未传路径时仅查 projects 下同 ID 主会话。"""
    sid = str(payload.get("session_id") or "").strip()
    if payload.get("agent_id"):
        return sid, None, "", True
    if not valid_session_id(sid):
        return "", None, "缺少合法 session_id", False
    raw = strip_win_extended_prefix(str(payload.get("transcript_path") or ""))
    if raw:
        path = Path(raw).expanduser()
        if any(part.lower() == "subagents" for part in path.parts):
            return sid, None, "", True
        if path.is_file():
            return sid, path, "", False
    else:
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
        candidates = list((home / "projects").glob(f"*/{sid}.jsonl"))
        candidates = [path for path in candidates if path.is_file()]
        if candidates:
            return sid, max(candidates, key=lambda path: path.stat().st_mtime_ns), "", False
    if os.environ.get("CAE_HARNESS_DEPTH", "").strip() not in ("", "0"):
        return sid, None, "", True
    return sid, None, "未找到 Claude 会话轨迹文件", False


def _claude_clean_text(text: str) -> str:
    stripped = text.strip()
    while stripped:
        lead = CLAUDE_INJECTED_TAG_RE.match(stripped)
        if lead:
            stripped = stripped[lead.end():].strip()
            continue
        # 尾部宿主自动追加的注入块同样剥离，但正文内部讨论同名标签时原样保留
        tail_removed = re.sub(
            r"(?:\s*<(ide_opened_file|ide_selection|ide_diagnostics|system-reminder|task-notification)\b[^>]*>.*?</\1\s*>\s*)+\Z",
            "",
            stripped,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        if tail_removed != stripped:
            stripped = tail_removed
            continue
        break
    if not stripped:
        return ""
    if re.fullmatch(
        r"(?:\s*<local-command-(?:stdout|stderr|caveat)\b[^>]*>.*?</local-command-(?:stdout|stderr|caveat)\s*>\s*)+",
        stripped,
        re.DOTALL | re.IGNORECASE,
    ):
        return ""
    if re.fullmatch(
        r"(?:\s*<command-(?:message|name|args)\b[^>]*>.*?</command-(?:message|name|args)\s*>\s*)+",
        stripped,
        re.DOTALL | re.IGNORECASE,
    ):
        command = re.search(r"<command-name>(.*?)</command-name>", stripped, re.DOTALL | re.IGNORECASE)
        if command:
            args = re.search(r"<command-args>(.*?)</command-args>", stripped, re.DOTALL | re.IGNORECASE)
            return (command[1].strip() + (" " + args[1].strip() if args and args[1].strip() else "")).strip()
    return stripped


def _claude_answer_text(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("answers"), dict):
        lines = []
        for question, answer in value["answers"].items():
            if isinstance(answer, list):
                answer = "、".join(str(item) for item in answer)
            if isinstance(answer, str) and answer.strip():
                lines.append(f"{question}：{answer.strip()}")
        return "\n".join(lines)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            prefix = "User has answered your questions:"
            if value.strip().startswith(prefix):
                return value.strip()[len(prefix):].split("You can now continue with the user's answers", 1)[0].strip()
        else:
            if isinstance(decoded, dict):
                return _claude_answer_text(decoded)
    return ""


def _claude_question_ids_from_assistant(entry: Dict[str, Any]) -> Set[str]:
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return set()
    return {
        str(block["id"]) for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
        and block.get("name") == "AskUserQuestion" and block.get("id")
    }


def _claude_tool_result_ids(entry: Dict[str, Any]) -> Set[str]:
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return set()
    return {
        str(block["tool_use_id"]) for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")
    }


def claude_user_content(entry: Dict[str, Any], question_ids: Optional[Set[str]] = None) -> Tuple[str, str]:
    """仅识别人类正文、排队输入与经 tool_use_id 核实的问答选择，供归档和回合定位共用。"""
    if entry.get("isSidechain") is True or entry.get("isMeta") is True:
        return "", ""
    if entry.get("type") == "attachment":
        att = entry.get("attachment")
        if not isinstance(att, dict) or att.get("type") != "queued_command":
            return "", ""
        origin = att.get("origin") or {}
        if not isinstance(origin, dict) or origin.get("kind") not in ("human", None):
            return "", ""
        prompt = att.get("prompt")
        return (prompt.strip(), "queued_command") if isinstance(prompt, str) else ("", "")
    msg = entry.get("message")
    if entry.get("type") != "user" or not isinstance(msg, dict):
        return "", ""
    content = msg.get("content")
    if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result"
                                         for block in content):
        matched_blocks = [
            block for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result"
            and (question_ids is None or str(block.get("tool_use_id") or "") in question_ids)
        ]
        if not matched_blocks:
            return "", ""
        answer = _claude_answer_text(entry.get("toolUseResult"))
        if not answer:
            answers = []
            for block in matched_blocks:
                text = _claude_answer_text(block) or _claude_answer_text(block.get("content"))
                if not text and isinstance(block.get("content"), list):
                    text = "\n".join(_claude_answer_text(item.get("text")) for item in block["content"]
                                     if isinstance(item, dict))
                if text:
                    answers.append(text)
            answer = "\n".join(answers)
        return answer, "ask_question_answer" if answer else ""
    if isinstance(content, list):
        content = "\n".join(block["text"] for block in content if isinstance(block, dict)
                            and block.get("type") == "text" and isinstance(block.get("text"), str))
    return (_claude_clean_text(content), "user_input") if isinstance(content, str) else ("", "")


def _claude_subagent_calls_from_assistant(entry: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return {}
    calls: Dict[str, Dict[str, str]] = {}
    for block in content:
        if not (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") in CLAUDE_SUBAGENT_TOOLS
            and block.get("id")
        ):
            continue
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        sub_type = str(
            inp.get("subagent_type") or inp.get("agent_type") or inp.get("name") or block.get("name") or ""
        ).strip()
        desc = str(inp.get("description") or "").strip()
        if len(desc) > 48:
            desc = desc[:45].rstrip() + "..."
        calls[str(block["id"])] = {"sub_type": sub_type, "description": desc}
    return calls


def _extract_claude_text_blocks(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts = [
            item["text"].strip()
            for item in raw
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str) and item["text"].strip()
        ]
        return "\n".join(parts).strip()
    return ""


def _resolve_claude_subagent_log(transcript_path: Path, agent_id: str, explicit_path: str = "") -> str:
    if explicit_path:
        cand_exp = Path(strip_win_extended_prefix(explicit_path)).expanduser()
        if cand_exp.is_file():
            return str(cand_exp)
    aid = agent_id.strip()
    if not aid:
        return ""
    for cand in (
        transcript_path.with_suffix("") / "subagents" / f"agent-{aid}.jsonl",
        transcript_path.parent / "subagents" / f"agent-{aid}.jsonl",
    ):
        if cand.is_file():
            return str(cand)
    return ""


def _extract_claude_subagent_returns_from_user(
    entry: Dict[str, Any],
    line_index: int,
    pending_subagents: Dict[str, Dict[str, str]],
    transcript_path: Path,
    claude_policy: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if entry.get("isSidechain") is True or entry.get("isMeta") is True or entry.get("type") != "user":
        return []
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return []
    matched = [
        (block, pending_subagents[str(block.get("tool_use_id") or "")])
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_result"
        and not block.get("is_error")
        and str(block.get("tool_use_id") or "") in pending_subagents
    ]
    if not matched:
        return []
    tool_res = entry.get("toolUseResult")
    if isinstance(tool_res, dict) and str(tool_res.get("status") or "").strip().lower() in (
        "async_launched", "running", "pending", "in_progress",
    ):
        return []

    policy = claude_policy if isinstance(claude_policy, dict) else get_compaction_tunables("claude")
    archive_cap = int(policy["archive_subagent_item_chars"])
    head_ratio = float(policy["subagent_head_ratio"])
    returns: List[Dict[str, Any]] = []
    ts_str = str(entry.get("timestamp") or "")
    for block, call_meta in matched:
        body_text = _extract_claude_text_blocks(block.get("content"))
        if not body_text and isinstance(tool_res, dict) and len(matched) == 1:
            for key in ("content", "result", "output", "text"):
                body_text = _extract_claude_text_blocks(tool_res.get(key))
                if body_text:
                    break
        if not body_text:
            continue
        agent_id = ""
        explicit_log = ""
        if isinstance(tool_res, dict) and len(matched) == 1:
            agent_id = str(tool_res.get("agentId") or tool_res.get("agent_id") or "").strip()
            explicit_log = str(tool_res.get("agent_transcript_path") or tool_res.get("transcript_path") or "").strip()
        if not agent_id:
            id_match = re.search(r"\bagentId:\s*([A-Za-z0-9_-]{4,120})", body_text)
            if id_match:
                agent_id = id_match.group(1).strip()
        sub_type = call_meta.get("sub_type") or ""
        desc = call_meta.get("description") or ""
        if sub_type and desc:
            label = f"{sub_type} ({desc})"
        else:
            label = sub_type or desc or agent_id or "Agent"
        returns.append(
            {
                "step_index": line_index,
                "created_at": ts_str,
                "agent_id": agent_id,
                "label": label,
                "log_path": _resolve_claude_subagent_log(transcript_path, agent_id, explicit_log),
                "raw_body": body_text,
                "content": summarize_subagent_return(body_text, archive_cap, head_ratio),
                "truncated": False,
            }
        )
    return returns


def extract_claude_user_words(
    transcript_path: Path,
    *,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """逐行严格解码并记录读取缺口；合并排队输入并提取最近子代理返回摘要。"""
    words: List[Dict[str, Any]] = []
    subagent_returns: List[Dict[str, Any]] = []
    issues: List[str] = []
    sources: List[Dict[str, Any]] = []
    digest = hashlib.sha256()
    offset = 0
    queued_index: Optional[int] = None
    question_ids: Set[str] = set()
    pending_subagents: Dict[str, Dict[str, str]] = {}
    claude_policy = policy if isinstance(policy, dict) else get_compaction_tunables("claude")
    harness = _HarnessArchiveCollector(transcript_path, claude_policy)
    try:
        with transcript_path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            for line_index, raw in enumerate(stream, 1):
                digest.update(raw)
                offset += len(raw)
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(decode_jsonl_line(raw))
                    if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
                        raise ValueError("记录缺少 type")
                except (ValueError, RecursionError) as exc:
                    issues.append(f"Claude 轨迹第 {line_index} 行无法解析（{exc}）")
                    queued_index = None
                    continue
                harness.feed(entry, line_index)
                if entry.get("type") == "assistant" and not entry.get("isSidechain"):
                    question_ids.update(_claude_question_ids_from_assistant(entry))
                    pending_subagents.update(_claude_subagent_calls_from_assistant(entry))
                if pending_subagents:
                    subagent_returns.extend(
                        _extract_claude_subagent_returns_from_user(
                            entry, line_index, pending_subagents, transcript_path, claude_policy=claude_policy
                        )
                    )
                text, kind = claude_user_content(entry, question_ids=question_ids)
                if not text:
                    if entry.get("type") == "assistant" and not entry.get("isSidechain"):
                        queued_index = None
                    continue
                word = {"turn": len(words) + 1, "step_index": line_index,
                        "created_at": str(entry.get("timestamp") or ""), "content": text, "kind": kind}
                if (kind == "user_input" and queued_index is not None
                        and words[queued_index]["content"] == text):
                    word["turn"] = words[queued_index]["turn"]
                    words[queued_index] = word
                else:
                    words.append(word)
                queued_index = len(words) - 1 if kind == "queued_command" else None
            after = os.fstat(stream.fileno())
        current = transcript_path.stat()
        if (offset != before.st_size
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            issues.append("读取时 Claude 轨迹发生变化，待再次核对")
        sources.append({"path": str(transcript_path), "complete_offset": min(offset, before.st_size),
                        "observed_size": before.st_size, "sha256": digest.hexdigest()})
    except OSError as exc:
        issues.append(f"读取 Claude 轨迹失败（{exc}）")
    return {
        "words": words,
        "subagent_returns": select_recent_subagent_returns(
            sorted(subagent_returns + harness.items, key=lambda item: item["step_index"]),
            claude_policy, body_getter=lambda item: item.get("raw_body") or item.get("content") or ""
        ),
        "issues": issues,
        "sources": sources,
        "complete": not issues,
    }


def archive_claude_session_words(
    payload: Dict[str, Any],
    state_dir: Path,
    *,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按 Codex SessionStart 的目录与恢复安全规则发布 Claude 原话、最近子代理摘要及 SHA-256 清单。"""
    sid, transcript, error, skip = claude_hook_session(payload)
    result: Dict[str, Any] = {"complete": False, "words": [], "subagent_returns": [],
                              "issues": [error] if error else [],
                              "saved": {}, "meta_path": "", "segments": [], "skipped": skip}
    if skip or not sid:
        return result
    user_words_dir = state_dir.parent / "user-words" if state_dir.name == "session-context" else state_dir / "user-words"
    md_path = user_words_dir / f"{sid}.md"
    meta_path = user_words_dir / f"{sid}.meta.json"
    result["meta_path"] = str(meta_path)
    claude_policy = policy if isinstance(policy, dict) else get_compaction_tunables("claude")
    with session_state_lock(user_words_dir, sid):
        previous = _read_codex_publication(md_path, meta_path, sid, CLAUDE_ARCHIVE_FORMAT)
        parsed = extract_claude_user_words(transcript, policy=claude_policy) if transcript else result
        words = parsed["words"]
        subagent_returns = parsed.get("subagent_returns") or []
        issues = list(parsed["issues"])
        sources = parsed.get("sources", [])
        complete = bool(transcript is not None and not issues)
        recorded_sources = previous.get("previous_manifest_sources") or previous.get("sources", [])
        missing = _missing_codex_source_coverage(recorded_sources, sources)
        if missing:
            complete = False
            issues.append("本次轨迹未覆盖已有归档来源：" + "、".join(missing))
        can_update_partial = bool(words) and (not previous["available"] or (
            previous["manifest_verified"] and not previous["verified"] and len(words) >= previous["count"]
        ))
        md_text = format_user_words_markdown(
            sid, words, issues, subagent_returns=subagent_returns, policy=claude_policy
        )
        md_sha = hashlib.sha256(md_text.encode("utf-8")).hexdigest()
        body_sha = previous.get("body_sha256")
        can_restore_manifest = bool(body_sha and body_sha == md_sha and sources and not previous.get("legacy_hash_mismatch"))
        unresolved_body = bool(body_sha and not previous["manifest_verified"] and not can_restore_manifest)
        if unresolved_body:
            complete = False
            issues.append("未能证明本次重建覆盖现有归档正文，已保留原正文")
        saved = previous
        should_publish = (transcript is not None and not missing and not unresolved_body
                          and (complete or can_update_partial or can_restore_manifest))
        if should_publish:
            try:
                manifest = {"format": CLAUDE_ARCHIVE_FORMAT, "id": sid, "file": str(md_path),
                            "count": len(words), "last_time": words[-1]["created_at"] if words else None,
                            "sources": sources, "sha256": md_sha, "complete": complete}
                if body_sha != md_sha:
                    atomic_write_text(md_path, md_text)
                atomic_write_json(meta_path, manifest)
            except OSError as exc:
                complete = False
                issues.append(f"归档发布中断（{exc}）")
            saved = _read_codex_publication(md_path, meta_path, sid, CLAUDE_ARCHIVE_FORMAT)
            if not saved["manifest_verified"]:
                complete = False
        if not complete and saved.get("verified") and saved.get("sha256") == previous.get("sha256"):
            saved["preserved"] = True
        if saved.get("issue"):
            issues.append(saved["issue"])
        saved.pop("body_sha256", None)
        saved.pop("previous_manifest_sources", None)
        saved.pop("legacy_hash_mismatch", None)
        result.update(complete=complete and saved.get("verified", False), words=words,
                      subagent_returns=subagent_returns, issues=issues,
                      saved=saved, segments=[str(transcript)] if transcript else [])
    return result
