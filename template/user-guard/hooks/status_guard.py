"""
status_guard.py - PreToolUse 项目目录写边界、长会话收尾限制与重复读取日志拦截钩子 (user-guard)

主要职责：
1. 项目目录外文件改动拦截（对应全局规则 R4）：
   - 检查 write_file / edit_file 及跨端文件编辑工具对当前项目根目录以外路径的写入；
   - 解析内联 Python AST 语法树与 Shell 命令子句，拦截向项目目录外创建 git worktree（如 ../.codex-worktrees）、增删改外部文件或运行全局配置同步（区分只读打开、容器 .copy()、文档字符串与外部工具调用，避免误报）；
   - 自动放行操作系统标准临时目录、当前客户端会话记录与制品目录（brain / plugin_data / memories / sessions 等）、标准缓存目录（~/.cache）以及 Windows 与 WSL 指向同一项目的挂载路径；
   - 交互式主会话遇到项目外写操作时返回 force_ask / ask，列出任务意图、写删动作、外部目标路径与触发语句请用户确认；后台无头子进程（CAE_HARNESS_DEPTH >= 1）直接返回 deny。
2. 长会话收尾阶段限制（配合 handoff_gate.py）：
   - 在 drain / freeze 阶段拦截新开子代理、后台任务编排、图片读取与 git push，按剩余动作次数限制修改与执行，同时放行只读核对、交接文档编写和本地 git add / git commit。
3. 重复读取未变化日志拦截（配合客户端 Reactive Wakeup 唤醒机制）：
   - 对日志文件或任务输出的读取按文件大小、修改时间及行号范围记录指纹，阻止在同一文件未发生变化时原地重复读取相同区间；文件追加新内容或翻页读取其他区间时正常放行。
"""

# ① 常量、阈值与宿主依赖

import argparse
import ast
import base64
import binascii
import hashlib
import itertools
import importlib.util
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS_DIR))
import handoff_gate  # noqa: E402
import user_words  # noqa: E402
from _lib import shell_lex as _shell_lex  # noqa: E402

user_words.ensure_utf8_stdio()

if os.name == "nt":
    import ctypes

    _WIN_GET_LONG_PATH = ctypes.windll.kernel32.GetLongPathNameW
    _WIN_GET_LONG_PATH.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    _WIN_GET_LONG_PATH.restype = ctypes.c_uint
else:
    _WIN_GET_LONG_PATH = None

# 证据类扩展名与严格锚定文件名正则（防止误伤业务源码如 status.json / test_result.json）
EVIDENCE_EXTENSIONS = {".log", ".out", ".err", ".output"}
EVIDENCE_FILENAME_RE = re.compile(
    r"^(?:"
    r"journal[^.]*\.jsonl|"
    r"output\.[^.]+|"
    r"task[-_]?output[^.]*(?:\.[^.]+)?|"
    r"results?\.(?:txt|json|jsonl|dat|csv)|"
    r"task\.(?:json|txt|status)|"
    r"wf_[^.]+\.json"
    r")$",
    re.IGNORECASE,
)

READONLY_COMMANDS = {
    "cat",
    "tail",
    "head",
    "get-content",
    "gc",
    "type",
    "grep",
    "rg",
    "select-string",
    "sls",
}

REMOTE_OR_SUBSYSTEM_WRAPPERS = {"wsl", "wsl.exe", "ssh", "ssh.exe", "docker", "docker.exe", "podman"}
SHELL_WRAPPERS = {"bash", "sh", "zsh", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "env"}
READ_FILE_TOOLS = ("read_file", "view_file")
WRITE_FILE_TOOLS = ("write_file", "write_to_file")
EDIT_FILE_TOOLS = ("edit_file", "replace_file_content", "multi_replace_file_content", "sed_file", "notebook_edit")
FILE_MUTATION_TOOLS = WRITE_FILE_TOOLS + EDIT_FILE_TOOLS
FILE_PATH_KEYS = ("Path", "path", "AbsolutePath", "absolute_path", "FilePath", "TargetFile",
                  "target_file", "file_path", "NotebookPath", "notebook_path")

LARGE_FILE_THRESHOLD = 4 * 1024 * 1024  # 4 MB
CHUNK_SAMPLE_BYTES = 1 * 1024 * 1024    # 1 MB
MAX_WORKFLOW_SCRIPT_BYTES = 1 * 1024 * 1024  # 1 MB
TASK_NO_LOG_COOLDOWN_SEC = 30.0
MAX_WRAPPER_DEPTH = 4

_STATUS_BUILTINS: Dict[str, Any] = {
    "LARGE_FILE_THRESHOLD": LARGE_FILE_THRESHOLD,
    "CHUNK_SAMPLE_BYTES": CHUNK_SAMPLE_BYTES,
    "MAX_WORKFLOW_SCRIPT_BYTES": MAX_WORKFLOW_SCRIPT_BYTES,
    "TASK_NO_LOG_COOLDOWN_SEC": TASK_NO_LOG_COOLDOWN_SEC,
    "MAX_WRAPPER_DEPTH": MAX_WRAPPER_DEPTH,
}
_ACTIVE_STATUS_BASELINE: Dict[str, Any] = dict(_STATUS_BUILTINS)
_LAST_STATUS_PROFILE_TOKEN: Optional[Tuple[Any, ...]] = None


def _sync_status_tunables() -> None:
    """从 fleet_profile.json 同步文件指纹采样与无日志冷却参数，同时兼容单测属性覆盖。"""
    global _LAST_STATUS_PROFILE_TOKEN
    cache_token = user_words.get_fleet_profile_cache_token(HOOKS_DIR)
    if _LAST_STATUS_PROFILE_TOKEN == cache_token:
        return
    _LAST_STATUS_PROFILE_TOKEN = cache_token
    lg = user_words.get_runtime_tunables("loop_guards", HOOKS_DIR)
    cfg = lg.get("status_guard") if isinstance(lg.get("status_guard"), dict) else {}
    raw_cd = cfg.get("task_no_log_cooldown_sec")
    try:
        cd_val = float(raw_cd) if not isinstance(raw_cd, bool) else 30.0
    except (TypeError, ValueError):
        cd_val = 30.0
    if cd_val <= 0:
        cd_val = 30.0
    resolved: Dict[str, Any] = {
        "LARGE_FILE_THRESHOLD": user_words._coerce_pos_int(cfg.get("large_file_threshold_mib"), 4, minimum=1) * 1024 * 1024,
        "CHUNK_SAMPLE_BYTES": user_words._coerce_pos_int(cfg.get("chunk_sample_mib"), 1, minimum=1) * 1024 * 1024,
        "MAX_WORKFLOW_SCRIPT_BYTES": user_words._coerce_pos_int(cfg.get("max_workflow_script_mib"), 1, minimum=1) * 1024 * 1024,
        "TASK_NO_LOG_COOLDOWN_SEC": cd_val,
        "MAX_WRAPPER_DEPTH": user_words._coerce_pos_int(cfg.get("max_wrapper_depth"), 4, minimum=1),
    }
    mod_globals = globals()
    for name, new_val in resolved.items():
        was_unpatched = (mod_globals[name] == _ACTIVE_STATUS_BASELINE[name])
        _ACTIVE_STATUS_BASELINE[name] = new_val
        if was_unpatched:
            mod_globals[name] = new_val


_sync_status_tunables()

DENY_REASON_TEXT = (
    "【user-guard 防空转门禁阻断】目标证据文件内容与查询参数自上次读取后未发生任何变化。"
    "反重力具备后台任务就绪通知机制（Reactive Wakeup），严禁在此原地空转轮询！"
    "请等待后台任务完成通知或先推进其他工作；文件产生新输出、或改变读取区间/查询参数后将自动放行。"
)

# 项目目录外文件改动硬边界门禁 (Workspace Boundary Hard Guard - R4)

_NULL_SINKS = {"/dev/null", "nul", "$null"}
# 引号内的转义与普通字符分支互斥；命令头限定在子句起点，避免长串失败匹配反复回扫。
_COMMIT_MSG_RE = re.compile(
    r"""((?:^|[\r\n;|&])\s*\bgit\b[^\r\n;|&]*?\bcommit\b[^\r\n;|&]*?(?:\s-(?:m|am)|--message(?:=|\s+)))(?:"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|\S+)""",
    re.IGNORECASE,
)
_GIT_WORKTREE_ADD_RE = re.compile(r"\bgit\b[^\r\n;|&]*?\bworktree\s+add\b([^\r\n;|&]+)", re.IGNORECASE)
_INSTALLER_INVOKE_RE = re.compile(
    r"(?:^|[;&|\r\n]\s*)(?:[A-Za-z_]\w*=\S+\s+)*"
    r"(?:(?:wsl(?:\.exe)?|bash|sh|pwsh(?:\.exe)?|powershell(?:\.exe)?|cmd(?:\.exe)?|"
    r"python[0-9.]*(?:\.exe)?|py(?:\.exe)?\s+-3)\b[^\r\n;|&]*?[\s\"'\\/]+|(?:\.[\\/]))"
    r"(?<![A-Za-z0-9_.-])install\.(?:py|ps1|sh)\b\"?'?([^\r\n;|&]*)",
    re.IGNORECASE,
)

_PY_OPT_VAL_RE = r"""(?:"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|\$'(?:\\.|[^'\\])*'|[^\s"';|&()])+"""
_PY_INLINE_HEAD_RE = re.compile(
    r"""(?<![^\s;|&()])(?:"[^"\r\n]*[\\/]|'[^'\r\n]*[\\/]|[^\s"';|&()]+[\\/]|["'])?\b(?i:(?:python[0-9.]*|py)(?:\.exe)?)\b["']?"""
    rf"""(?:\s+(?:["']?-[0-9][0-9.]*["']?|["']?-[BbIdEisSuqvVxOP]+["']?|["']?(?i:--safe-path)["']?|-[WXQ](?:\s+{_PY_OPT_VAL_RE}|{_PY_OPT_VAL_RE})|(?i:--check-hash-based-pycs)(?:={_PY_OPT_VAL_RE}|\s+{_PY_OPT_VAL_RE})))*"""
    r"""(?:\s+(?:"-(?:[BbIdEisSuqvVxOP]+)?c"|'-(?:[BbIdEisSuqvVxOP]+)?c'|-(?:[BbIdEisSuqvVxOP]+)?c)(?:\s+|(?=['"]|\$'|[A-Za-z_0-9\\]))|\s+(?=(?:"|'|\$')-(?:[BbIdEisSuqvVxOP]+)?c\S))"""
)
_PS_HERESTRING_PY_RE = re.compile(
    r"@(['\"])\r?\n([\s\S]*?)\r?\n\1@\s*\|\s*[^\r\n|]*\bpython[0-9.]*(?:\.exe)?\b",
    re.IGNORECASE,
)
_SH_HEREDOC_PY_RE = re.compile(
    r"\bpython[0-9.]*(?:\.exe)?\b[^\r\n]*<<-?\s*['\"]?([A-Za-z0-9_]+)['\"]?\r?\n([\s\S]*?)\r?\n\1(?=\r?\n|$)",
    re.IGNORECASE,
)
_DOTNET_IO_MUTATE_RE = re.compile(
    r"\[(?:System\.)?IO\.(File|Directory)\]::"
    r"(Write\w*|Append\w*|Create\w*|Delete\w*|Move\w*|Copy\w*)\s*\(([^)\r\n]+)\)",
    re.IGNORECASE,
)
_SHELL_REDIRECT_RE = re.compile(
    r"""(?:&>{1,2}|(?<![->=&])(?:\d+)?(?:>{1,2}\|?|<>))(?![>&=(])\s*((?:\$\{[^}\r\n]*\}|\$'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|\\[\s;|&<>()"']|[^\s;|&<>()"'])+)""",
)
_APPLY_PATCH_HEADER_RE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$", re.MULTILINE)
_PY_FALLBACK_PAIRS = (
    ("Python 写入文件 (open)", re.compile(
        r"""\bopen\s*\(\s*(['"][^'"]+['"])\s*,\s*['"][^'"]*[wax+][^'"]*['"]""")),
    ("Python 文件写删 (Path)", re.compile(
        r"""\bPath\s*\(\s*(['"][^'"]+['"])\s*\)\s*\.\s*(?:write_text|write_bytes|unlink|rmdir|mkdir|touch|chmod|lchmod)\b""")),
    ("Python 系统删除/建目录", re.compile(
        r"""\b(?:shutil\.rmtree|os\.(?:remove|unlink|rmdir|makedirs|mkdir|chmod|lchmod|chown|lchown|utime))\s*\(\s*(['"][^'"]+['"])""")),
    ("Python 复制写入外部路径", re.compile(
        r"""\bshutil\.copy\w*\s*\([^,]+,\s*(['"][^'"]+['"])""")),
)

_SHELL_DELETE_VERBS = {"rm", "rmdir", "rd", "del", "erase", "remove-item", "ri"}
_SHELL_CREATE_WRITE_VERBS = {
    "mkdir",
    "md",
    "touch",
    "truncate",
    "tee",
    "new-item",
    "ni",
    "set-content",
    "add-content",
    "ac",
    "clear-content",
    "clc",
    "out-file",
}
_SHELL_MOVE_VERBS = {"mv", "move", "ren", "rename", "move-item", "mi", "rename-item", "rni"}
_SHELL_COPY_VERBS = {"cp", "copy", "xcopy", "robocopy", "copy-item", "cpi"}
_SHELL_LINK_VERBS = {"ln", "mklink"}
_PS_NON_PATH_VALUE_FLAGS = {
    "-value",
    "-itemtype",
    "-type",
    "-encoding",
    "-filter",
    "-include",
    "-exclude",
    "-delimiter",
    "-stream",
}

WSL_TEMP_CHECK = """
import json, os, sys, tempfile
from pathlib import Path

targets = json.load(sys.stdin)
runtime_root = tempfile.gettempdir()
allowed = []
if runtime_root == "/tmp" and Path(runtime_root).resolve() == Path(runtime_root):
    for raw in targets:
        normalized = os.path.normpath(raw)
        resolved = Path(raw).resolve()
        if (normalized.startswith("/tmp/") and resolved != Path(runtime_root)
                and resolved.is_relative_to(runtime_root)):
            allowed.append(raw)
print(json.dumps(allowed))
"""


# ② 路径定位、日志与轮询状态读写

def get_data_dirs() -> Tuple[Path, Path]:
    return user_words.resolve_data_dirs(HOOKS_DIR)


def get_current_iso_time() -> str:
    return user_words.current_iso_time()


def log_event(
    event_name: str,
    conversation_id: str,
    message: str,
    logs_dir: Optional[Path] = None,
    client: str = "claude",
) -> None:
    if logs_dir is None:
        user_words.append_lifecycle_log(HOOKS_DIR, event_name, conversation_id, message)
    else:
        user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
            "timestamp": get_current_iso_time(), "event": event_name, "client": client,
            "conversationId": conversation_id, "message": message,
        })


def _is_inside_dir(target: Path, parent: Path) -> bool:
    try:
        target.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def resolve_target_path(
    raw_path_str: str,
    cwd: Optional[str],
    workspace_paths: Optional[List[str]],
) -> Path:
    """严格按 payload.cwd -> workspacePaths 优先级解析相对路径，绝不落入插件自身工作目录"""
    return handoff_gate._resolve_candidate_path(raw_path_str, cwd, workspace_paths)


def _canonical_path_key(p: Path) -> str:
    return user_words._canonical_path_key(p)


def _load_guard_state(
    state_file: Path,
    conversation_id: str,
    logs_dir: Optional[Path] = None,
    client: str = "claude",
) -> Dict[str, Any]:
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8-sig"))
            if (isinstance(data, dict) and data.get("version") == 2
                    and data.get("conversationId") == conversation_id and isinstance(data.get("records"), dict)):
                return data
            raise ValueError("轮询状态格式或会话身份错误")
        except (ValueError, OSError, RecursionError) as exc:
            log_event("PreToolUse:poll_state_error", conversation_id, f"轮询状态读取失败（{exc}），保留原文件", logs_dir, client=client)
            raise
    return {"version": 2, "conversationId": conversation_id, "records": {}}


def _save_guard_state(state_file: Path, state: Dict[str, Any], conversation_id: str) -> None:
    state["version"] = 2
    state["conversationId"] = conversation_id
    state["updatedAt"] = get_current_iso_time()
    user_words.atomic_write_json(state_file, state)


# ③ 证据查询解析、文件指纹与防空转检测

def is_evidence_file(file_path_str: str) -> bool:
    """严格判定路径是否属于算例/后台任务输出证据文件"""
    if not file_path_str:
        return False

    cleaned = file_path_str.strip().strip("\"'")
    if not cleaned or cleaned.startswith("-"):
        return False

    norm_slashes = cleaned.replace("\\", "/").lower()
    if "/.system_generated/tasks/" in norm_slashes or "/.system_generated/steps/" in norm_slashes:
        return True

    p = Path(cleaned.replace("\\", "/"))
    name = p.name
    ext = p.suffix.lower()

    if ext in EVIDENCE_EXTENSIONS:
        return True
    if EVIDENCE_FILENAME_RE.match(name):
        return True
    return False


_safe_split_tokens = _shell_lex.safe_split_tokens
_split_shell_clauses = _shell_lex.split_shell_clauses
_extract_cmd_substitutions = _shell_lex.extract_cmd_substitutions


def _unquote_split_string_payload(inner: str) -> str:
    """剥离 Windows shlex(posix=False) 拆解 --split-string="..." 后残留的开头或成对引号。"""
    s = inner.strip()
    if not s or s[0] not in ("'", '"'):
        return s
    q = s[0]
    if len(s) >= 2 and s[-1] == q:
        return s[1:-1]
    end = s.find(q, 1)
    if end != -1:
        return s[1:end] + s[end + 1 :]
    return s[1:]


def _unwrap_inner_command(tokens: List[str]) -> Tuple[List[str], bool]:
    """
    剥离前导 env / timeout / bash -c / powershell -Command / wsl 等包装器，
    返回 (核心命令 token 列表, 是否经过 wsl/ssh/docker 跨子系统包装)。
    """
    idx = 0
    used_subsystem = False
    n = len(tokens)

    while idx < n:
        head_raw = tokens[idx]
        head_name = Path(head_raw.replace("\\", "/")).name.lower()

        if head_raw == "&":
            idx += 1
            continue

        if head_name == "command":
            idx += 1
            while idx < n and tokens[idx].startswith("-"):
                fl = tokens[idx]
                idx += 1
                if fl == "--":
                    break
                if fl in ("-v", "-V") or (not fl.startswith("--") and any(c in fl[1:] for c in ("v", "V"))):
                    return [], used_subsystem
            continue

        if head_name == "env":
            idx += 1
            while idx < n and tokens[idx].startswith("-"):
                fl = tokens[idx]
                idx += 1
                if fl == "--":
                    break
                if fl.startswith("--split-string="):
                    split_sub = _unquote_split_string_payload(
                        fl.split("=", 1)[1] + (" " + " ".join(tokens[idx:]) if idx < n else "")
                    )
                    tokens = _safe_split_tokens(split_sub)
                    idx = 0
                    n = len(tokens)
                    break
                if fl in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string") and idx < n:
                    if fl in ("-S", "--split-string"):
                        tokens = _safe_split_tokens(" ".join(tokens[idx:]))
                        idx = 0
                        n = len(tokens)
                        break
                    idx += 1
            continue

        if head_name == "nohup":
            idx += 1
            continue

        if head_name == "nice":
            idx += 1
            while idx < n and tokens[idx].startswith("-"):
                fl = tokens[idx]
                idx += 1
                if fl == "--":
                    break
                if fl in ("-n", "--adjustment") and idx < n:
                    idx += 1
            continue

        if head_name == "uv" and idx + 1 < n:
            j = idx + 1
            while j < n and tokens[j].startswith("-"):
                j += 2 if tokens[j] in ("--directory", "--project", "--config-file") and j + 1 < n else 1
            if j < n and tokens[j].lower() == "run":
                j += 1
                uv_vals = ("--python", "-p", "--with", "--with-editable", "--with-requirements",
                           "--directory", "--project", "--env-file", "--package")
                while j < n and tokens[j].startswith("-"):
                    fl = tokens[j]
                    j += 1
                    if fl == "--":
                        break
                    if fl in uv_vals and j < n:
                        j += 1
                idx = j
                continue

        if head_name == "sudo":
            idx += 1
            while idx < n and tokens[idx].startswith("-"):
                fl = tokens[idx]
                idx += 1
                if fl == "--":
                    break
                if fl in (
                    "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
                    "-r", "--role", "-t", "--type", "-C", "--close-from",
                    "-T", "--command-timeout", "-D", "--chdir", "-R", "--chroot",
                ) and idx < n:
                    idx += 1
            continue

        # 跳过形如 KEY=VALUE 的环境变量前缀
        if "=" in head_raw and not head_raw.startswith("-") and "/" not in head_raw and "\\" not in head_raw:
            idx += 1
            continue

        if head_name == "timeout":
            idx += 1
            while idx < n and tokens[idx].startswith("-"):
                flag_low = tokens[idx].lower()
                idx += 1
                if flag_low == "--":
                    break
                if flag_low in ("-k", "--kill-after", "-s", "--signal") and idx < n:
                    idx += 1
            if idx < n:
                idx += 1  # 跳过秒数参数
            continue

        if head_name in REMOTE_OR_SUBSYSTEM_WRAPPERS:
            used_subsystem = True
            idx += 1
            while idx < n and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 1
                if flag in ("--", "-e", "--exec"):
                    break
                if flag in ("-d", "--distribution", "-u", "--user", "--cd") and idx < n:
                    idx += 1
            continue

        if head_name in SHELL_WRAPPERS:
            idx += 1
            while idx < n:
                flag_low = tokens[idx].lower()
                if flag_low in ("-executionpolicy", "-ep", "-windowstyle", "-w"):
                    idx += 2
                    continue
                is_command = flag_low in ("/c", "-command") or bool(re.fullmatch(r"-[a-z]*c", flag_low))
                if not is_command and flag_low not in ("-noprofile", "-noni", "-noninteractive"):
                    break
                idx += 1
                if is_command and idx < n:
                    tokens = _safe_split_tokens(" ".join(tokens[idx:]))
                    idx = 0
                    n = len(tokens)
                    break
            continue

        break

    return tokens[idx:], used_subsystem


def parse_run_command_evidence(command_line: str) -> Tuple[Optional[str], bool, str]:
    """
    解析命令行是否属于对证据文件的只读查看。
    返回 (证据文件路径或 None, 是否跨子系统命令, 归一化命令签名)。
    """
    if not command_line or not command_line.strip():
        return None, False, ""

    # 查询参数中的大小写与引号内空白有实际含义，不能随 Windows 路径一起折叠。
    norm_cmd_sig = command_line.strip()

    # 若含重定向写入或修改型操作符，非纯只读查询，直接放行
    if re.search(r"(?:>>|>|\btee\b|\brm\b|\bmv\b|\bcp\b|\bsed\s+-i\b)", command_line):
        return None, False, norm_cmd_sig

    segments = [(seg, 0) for seg in reversed(_split_shell_clauses(command_line))]
    any_subsystem = False
    evidence = None

    while segments:
        seg, depth = segments.pop()
        if not seg:
            continue
        raw_tokens = _safe_split_tokens(seg)
        if not raw_tokens:
            continue
        tokens, used_sub = _unwrap_inner_command(raw_tokens)
        any_subsystem = any_subsystem or used_sub
        inner = _unwrap_shell_script_layer(seg)
        if inner is not None:
            if depth >= 12:
                return None, False, norm_cmd_sig
            segments.extend((part, depth + 1) for part in reversed(_split_shell_clauses(inner)))
            continue
        if not tokens:
            continue

        cmd_head = Path(tokens[0].replace("\\", "/")).name.lower()
        if cmd_head.endswith(".exe"):
            cmd_head = cmd_head[:-4]

        if cmd_head not in READONLY_COMMANDS:
            return None, False, norm_cmd_sig

        search_command = cmd_head in ("grep", "rg", "select-string", "sls")
        pattern_flags = {"-e", "--regexp", "-f", "--file", "-pattern"}
        value_flags = pattern_flags | {
            "-g", "--glob", "--iglob", "-t", "--type", "--type-not", "-m", "--max-count",
            "--after-context", "--before-context", "--context", "-encoding", "-context", "-include", "-exclude",
        }
        explicit_pattern = search_command and any(
            tok.lower().split("=", 1)[0].split(":", 1)[0] in pattern_flags
            or (cmd_head in ("grep", "rg") and tok.startswith(("-e", "-f")) and not tok.startswith("--") and len(tok) > 2)
            for tok in tokens[1:]
        )
        skip_pattern = search_command and not explicit_pattern
        idx = 1
        options = True
        while idx < len(tokens):
            tok = tokens[idx]
            idx += 1
            if options and tok == "--":
                options = False
                continue
            if options and tok.startswith("-"):
                if search_command and (tok.lower() in value_flags or tok in ("-A", "-B", "-C")):
                    idx += 1
                continue
            if skip_pattern:
                skip_pattern = False
                continue
            if evidence is None and is_evidence_file(tok):
                evidence = tok

    return evidence, any_subsystem, norm_cmd_sig


def compute_file_fingerprint(target_path: Path) -> Optional[Dict[str, Any]]:
    """
    计算证据文件的快速复合指纹：
    - <= 4MB: 全量 SHA256 + size + mtime_ns
    - >  4MB: 头部 1MB + 尾部 1MB 分块 SHA256 + size + mtime_ns（防 GB 级算例日志阻塞超时）
    """
    path_key = _canonical_path_key(target_path)
    try:
        resolved_str = str(target_path.resolve())
    except (OSError, ValueError, RuntimeError):
        resolved_str = str(target_path)

    if not target_path.exists():
        return {
            "pathKey": path_key,
            "path": resolved_str,
            "exists": False,
            "mtimeNs": 0,
            "size": 0,
            "sha256": "non_existent",
        }

    if not target_path.is_file():
        return None

    try:
        st = target_path.stat()
        fsize = st.st_size
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))

        hasher = hashlib.sha256()
        with open(target_path, "rb") as f:
            if fsize <= LARGE_FILE_THRESHOLD:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            else:
                hasher.update(f.read(CHUNK_SAMPLE_BYTES))
                f.seek(max(0, fsize - CHUNK_SAMPLE_BYTES))
                hasher.update(f.read(CHUNK_SAMPLE_BYTES))

        return {
            "pathKey": path_key,
            "path": resolved_str,
            "exists": True,
            "mtimeNs": mtime_ns,
            "size": fsize,
            "sha256": hasher.hexdigest(),
        }
    except (OSError, ValueError):
        return None


def _is_fingerprint_equal(rec: Dict[str, Any], fp: Dict[str, Any]) -> bool:
    return (
        rec.get("exists") == fp["exists"]
        and rec.get("mtimeNs") == fp["mtimeNs"]
        and rec.get("size") == fp["size"]
        and rec.get("sha256") == fp["sha256"]
    )


def _locate_task_log_file(transcript_path_str: Optional[str], task_id: str) -> Optional[Path]:
    """根据会话 transcriptPath 定位 Antigravity 后台任务日志 .system_generated/tasks/<TaskId>.log"""
    if not transcript_path_str or not user_words.valid_session_id(task_id):
        return None
    tp = Path(transcript_path_str).expanduser()
    # 标准布局: <brain>/<convId>/.system_generated/logs/transcript.jsonl
    sg_dir = tp.parent.parent
    candidates = [
        sg_dir / "tasks" / f"{task_id}.log",
        sg_dir / "tasks" / task_id,
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return candidates[0]


def _check_polling_locked(tool_name: str, args: Dict[str, Any], conversation_id: str, cwd: Optional[str],
                          workspace_paths: List[str], transcript_path_str: Optional[str], state_dir: Path,
                          logs_dir: Path) -> Dict[str, Any]:
    state_file = state_dir / f"{conversation_id}_status_guard.json"

    # 分支 1: read_file / view_file 或 run_command 访问证据文件
    if tool_name in (*READ_FILE_TOOLS, "run_command"):
        raw_target: Optional[str] = None
        param_sig = ""
        is_subsystem_cmd = False

        if tool_name in READ_FILE_TOOLS:
            file_arg = str(
                args.get("AbsolutePath")
                or args.get("Path")
                or args.get("path")
                or args.get("TargetFile")
                or args.get("target_file")
                or args.get("file_path")
                or ""
            ).strip()
            if not file_arg or not is_evidence_file(file_arg):
                return {"decision": "allow"}
            raw_target = file_arg
            start_line = args.get("StartLine", args.get("startLine"))
            end_line = args.get("EndLine", args.get("endLine"))
            byte_offset = args.get("ByteOffset", args.get("byteOffset"))
            param_sig = f"s={start_line}:e={end_line}:b={byte_offset}"
        else:
            cmd_line = str(args.get("CommandLine") or args.get("command_line") or args.get("command") or "")
            raw_target, is_subsystem_cmd, param_sig = parse_run_command_evidence(cmd_line)
            if not raw_target:
                return {"decision": "allow"}

        # Windows 宿主下通过 wsl/ssh/docker 访问子系统内部 POSIX 路径（宿主不可见）时豁免防死锁
        cleaned_raw = raw_target.strip().strip("\"'")
        is_posix_style_on_win = os.name == "nt" and cleaned_raw.startswith("/") and not cleaned_raw.startswith("//")
        target_path = resolve_target_path(raw_target, cwd, workspace_paths)

        if (is_subsystem_cmd or is_posix_style_on_win) and not target_path.exists():
            return {"decision": "allow"}

        # 豁免插件自身的状态与日志目录，绝不自我拦截
        if _is_inside_dir(target_path, state_dir) or _is_inside_dir(target_path, logs_dir):
            return {"decision": "allow"}

        fp = compute_file_fingerprint(target_path)
        if not fp:
            return {"decision": "allow"}

        canon_tool = "read_file" if tool_name in READ_FILE_TOOLS else tool_name
        query_key = f"{canon_tool}:{fp['pathKey']}|{param_sig}"
        state = _load_guard_state(state_file, conversation_id)
        records = state.setdefault("records", {})
        last_rec = records.get(query_key)

        if isinstance(last_rec, dict) and _is_fingerprint_equal(last_rec, fp):
            query_count = int(last_rec.get("queryCount", 1)) + 1
            last_rec["queryCount"] = query_count
            last_rec["lastBlockedAt"] = get_current_iso_time()
            _save_guard_state(state_file, state, conversation_id)
            log_event(
                "PreToolUse:status_guard",
                conversation_id,
                f"DENIED unchanged {tool_name} on {fp['path']} [{param_sig}] (query #{query_count})",
            )
            return {"decision": "deny", "reason": DENY_REASON_TEXT}

        records[query_key] = {
            "type": "evidence_query",
            "tool": tool_name,
            "path": fp["path"],
            "params": param_sig,
            "exists": fp["exists"],
            "mtimeNs": fp["mtimeNs"],
            "size": fp["size"],
            "sha256": fp["sha256"],
            "queryCount": 1,
            "lastAllowedAt": get_current_iso_time(),
        }
        _save_guard_state(state_file, state, conversation_id)
        log_event(
            "PreToolUse:status_guard",
            conversation_id,
            f"ALLOWED {tool_name} on {fp['path']} [{param_sig}] (size={fp['size']})",
        )
        return {"decision": "allow"}

    # 分支 2: manage_task(Action="status") 联动后台任务日志物理指纹
    if tool_name == "manage_task":
        action = str(args.get("Action") or args.get("action") or "").strip().lower()
        if action != "status":
            return {"decision": "allow"}

        task_id = str(args.get("TaskId") or args.get("taskId") or args.get("task_id") or "default_task").strip()
        task_key = f"task:{task_id}"
        task_log_path = _locate_task_log_file(transcript_path_str, task_id)
        task_fp = compute_file_fingerprint(task_log_path) if task_log_path else None

        state = _load_guard_state(state_file, conversation_id)
        records = state.setdefault("records", {})
        last_rec = records.get(task_key)
        now_epoch = time.time()

        if isinstance(last_rec, dict):
            if task_fp and task_fp["exists"]:
                # 任务日志存在：严格依据任务日志是否产生新输出判定放行或阻断
                if _is_fingerprint_equal(last_rec, task_fp):
                    query_count = int(last_rec.get("queryCount", 1)) + 1
                    last_rec["queryCount"] = query_count
                    last_rec["lastBlockedAt"] = get_current_iso_time()
                    _save_guard_state(state_file, state, conversation_id)
                    log_event(
                        "PreToolUse:status_guard",
                        conversation_id,
                        f"DENIED unchanged manage_task(status) for {task_id} (query #{query_count})",
                    )
                    return {"decision": "deny", "reason": DENY_REASON_TEXT}
            else:
                # 尚无任务日志文件时：阻断 30 秒内的高频盲目轮询，超时后允许再次探查防永久锁死
                last_epoch = float(last_rec.get("lastAllowedEpoch", 0.0))
                if (now_epoch - last_epoch) < TASK_NO_LOG_COOLDOWN_SEC:
                    query_count = int(last_rec.get("queryCount", 1)) + 1
                    last_rec["queryCount"] = query_count
                    last_rec["lastBlockedAt"] = get_current_iso_time()
                    _save_guard_state(state_file, state, conversation_id)
                    log_event(
                        "PreToolUse:status_guard",
                        conversation_id,
                        f"DENIED rapid manage_task(status) polling for {task_id} (query #{query_count})",
                    )
                    return {"decision": "deny", "reason": DENY_REASON_TEXT}

        new_rec: Dict[str, Any] = {
            "type": "manage_task_status",
            "taskId": task_id,
            "queryCount": 1,
            "lastAllowedEpoch": now_epoch,
            "lastAllowedAt": get_current_iso_time(),
        }
        if task_fp:
            new_rec.update(
                {
                    "exists": task_fp["exists"],
                    "mtimeNs": task_fp["mtimeNs"],
                    "size": task_fp["size"],
                    "sha256": task_fp["sha256"],
                }
            )
        records[task_key] = new_rec
        _save_guard_state(state_file, state, conversation_id)
        log_event("PreToolUse:status_guard", conversation_id, f"ALLOWED manage_task(status) for {task_id}")
        return {"decision": "allow"}

    return {"decision": "allow"}


# ④ 跨系统路径归一化与目录边界

def _expand_host_vars(
    raw_str: str, base_cwd: Optional[str] = None, *, from_shell: bool = True
) -> str:
    s = raw_str.strip()
    if from_shell and len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1]
    if not s:
        return ""
    home_str = str(Path.home())
    tmp_dir = tempfile.gettempdir()
    tmpdir_val = os.environ.get("TMPDIR") or tmp_dir
    env_map = {
        "$env:userprofile": os.environ.get("USERPROFILE") or home_str,
        "${env:userprofile}": os.environ.get("USERPROFILE") or home_str,
        "%userprofile%": os.environ.get("USERPROFILE") or home_str,
        "$env:home": home_str,
        "${env:home}": home_str,
        "$home": home_str,
        "${home}": home_str,
        "$env:appdata": os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming"),
        "%appdata%": os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming"),
        "$env:localappdata": os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"),
        "%localappdata%": os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"),
        "$env:temp": tmp_dir,
        "${env:temp}": tmp_dir,
        "%temp%": tmp_dir,
        "$temp": tmp_dir,
        "${temp}": tmp_dir,
        "$env:tmp": tmp_dir,
        "${env:tmp}": tmp_dir,
        "%tmp%": tmp_dir,
        "$tmp": tmp_dir,
        "${tmp}": tmp_dir,
        "$env:tmpdir": tmpdir_val,
        "${env:tmpdir}": tmpdir_val,
        "$tmpdir": tmpdir_val,
        "${tmpdir}": tmpdir_val,
        "$xdg_cache_home": os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache"),
        "${xdg_cache_home}": os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache"),
    }
    if isinstance(base_cwd, str) and base_cwd.strip():
        env_map["$pwd"] = base_cwd
        env_map["${pwd}"] = base_cwd
        env_map["$env:pwd"] = base_cwd
        env_map["${env:pwd}"] = base_cwd
    low = s.lower()
    for prefix, val in env_map.items():
        if low == prefix:
            return val
        if low.startswith(prefix + "/") or low.startswith(prefix + "\\"):
            return val.rstrip("/\\") + "/" + s[len(prefix) + 1 :]
    return os.path.expanduser(s)


_SHELL_DYN_VAR_RE = re.compile(r"\$(?:\{|\(|[0-9@#?!*$]|[A-Za-z_][A-Za-z_0-9]*)")
_WIN_PCT_VAR_RE = re.compile(r"%[A-Za-z_][A-Za-z_0-9]*%")


def _has_unexpanded_dynamic_var(s: str, *, is_cmd_or_win: bool = False) -> bool:
    if not s or s.lower() in _NULL_SINKS:
        return False
    if _SHELL_DYN_VAR_RE.search(s):
        return True
    pct_target = s if (os.name == "nt" or is_cmd_or_win) else re.sub(r"%[0-9A-Fa-f]{2}", "", s)
    return bool(_WIN_PCT_VAR_RE.search(pct_target))


def _win_expand_short_path(win_path: str) -> str:
    """在 Windows 下将已存在前缀中的 NTFS 8.3 短文件名还原为长文件名，不跟随软链接或目录联接。"""
    if os.name != "nt" or _WIN_GET_LONG_PATH is None or "~" not in win_path:
        return win_path
    try:
        cur = Path(win_path)
        tail: List[str] = []
        while True:
            cur_str = str(cur)
            buf = ctypes.create_unicode_buffer(max(len(cur_str) + 256, 512))
            length = _WIN_GET_LONG_PATH(cur_str, buf, len(buf))
            if length >= len(buf):
                buf = ctypes.create_unicode_buffer(length)
                length = _WIN_GET_LONG_PATH(cur_str, buf, length)
            if length:
                res = Path(buf.value)
                for part in reversed(tail):
                    res = res / part
                return str(res)
            if cur.parent == cur:
                return win_path
            tail.append(cur.name)
            cur = cur.parent
    except (OSError, ValueError, RuntimeError):
        return win_path


def _normalize_win_mount_key(drv: str, rest: str) -> str:
    rel_norm = posixpath.normpath("/" + rest.lstrip("/")).lstrip("/")
    norm = f"/mnt/{drv}" + (f"/{rel_norm}" if rel_norm else "")
    prefix = f"/mnt/{drv}"
    if "~" in norm and os.name == "nt" and (norm == prefix or norm.startswith(prefix + "/")):
        sub = norm[len(prefix):].lstrip("/")
        expanded = _win_expand_short_path(f"{drv.upper()}:\\" + sub.replace("/", "\\"))
        m_exp = re.match(r"^([A-Za-z]):(?:[\\/](.*))?$", expanded)
        if m_exp:
            exp_rel = posixpath.normpath("/" + (m_exp.group(2) or "").replace(chr(92), "/").lstrip("/")).lstrip("/")
            norm = f"/mnt/{m_exp.group(1).lower()}" + (f"/{exp_rel}" if exp_rel else "")
    return norm.casefold()


def _normalize_cross_env_lexical(
    raw_str: str, base_dir: Optional[str] = None, *, from_shell: bool = True
) -> str:
    r"""盘符与 /mnt/<drive> 共用规范键；兼容 Windows 盘符相对路径（如 D:out.txt、C:..\out.txt）。"""
    expanded = user_words.strip_win_extended_prefix(
        _expand_host_vars(raw_str, base_cwd=base_dir, from_shell=from_shell)
    )
    if not expanded:
        return ""
    slashes = expanded.replace("\\", "/")

    m_rel_drive = re.match(r"^([A-Za-z]):([^/].*)$", slashes)
    if m_rel_drive:
        drv = m_rel_drive.group(1).lower()
        rel_part = m_rel_drive.group(2)
        if base_dir:
            base_norm = _normalize_cross_env_lexical(base_dir, None)
            drv_prefix = f"/mnt/{drv}"
            if base_norm == drv_prefix or base_norm.startswith(drv_prefix + "/"):
                base_sub = base_norm[len(drv_prefix) :].lstrip("/")
                combined = f"{base_sub}/{rel_part}" if base_sub else rel_part
                return _normalize_win_mount_key(drv, combined)
        return _normalize_win_mount_key(drv, rel_part)

    is_win_drive = bool(re.match(r"^[A-Za-z]:(?:/|$)", slashes))
    is_unc = slashes.startswith("//")
    is_posix_abs = slashes.startswith("/") and not is_unc

    if not (is_win_drive or is_unc or is_posix_abs) and base_dir:
        base_norm = _normalize_cross_env_lexical(base_dir, None)
        if base_norm:
            slashes = base_norm.rstrip("/") + "/" + slashes
            is_posix_abs = slashes.startswith("/") and not slashes.startswith("//")
            is_unc = slashes.startswith("//")

    m_drive = re.match(r"^([A-Za-z]):(?:/(.*))?$", slashes)
    if m_drive:
        return _normalize_win_mount_key(m_drive.group(1).lower(), m_drive.group(2) or "")

    m_mnt = re.match(r"^/mnt/([A-Za-z])(?:/(.*))?$", slashes)
    if m_mnt:
        return _normalize_win_mount_key(m_mnt.group(1).lower(), m_mnt.group(2) or "")

    if is_unc:
        norm = _normalize_unc_key(slashes)
        if "~" in norm and os.name == "nt":
            win_unc = _win_expand_short_path(norm.replace("/", "\\"))
            if win_unc.startswith("\\\\"):
                norm = _normalize_unc_key(win_unc.replace("\\", "/"))
        return norm.casefold()

    norm = posixpath.normpath(slashes)
    return norm.casefold() if os.name == "nt" else norm


def _normalize_unc_key(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    root, tail = parts[:2], []
    for part in parts[2:]:
        if part == "..":
            if tail:
                tail.pop()
        elif part != ".":
            tail.append(part)
    return "//" + "/".join(root + tail)


def _is_under_norm_key(target_key: str, root_key: str) -> bool:
    if not target_key or not root_key:
        return False
    if root_key == "/":
        return target_key.startswith("/")
    return target_key == root_key or target_key.startswith(root_key.rstrip("/") + "/")


def _is_standard_os_temp_key(norm_key: str) -> bool:
    raw = tempfile.gettempdir()
    roots = [_normalize_cross_env_lexical(raw)]
    rp = _resolve_nearest_existing(raw, None)
    if rp is not None:
        roots.append(_normalize_cross_env_lexical(str(rp)))
    return any(_is_under_norm_key(norm_key, r) for r in roots if r)


def _is_wsl_standard_temp_path(target: str, distribution: str) -> bool:
    """在选定的本机 WSL 发行版核对标准 /tmp 的物理位置。"""
    if (os.name != "nt" or not target.startswith("/tmp/")
            or not re.fullmatch(r"/tmp/[A-Za-z0-9_./ +@=-]+", target)):
        return False
    # 固定入口与代码，待检查路径仅以 stdin 数据传递。Windows 宿主命令不会经此路径执行。
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
    selector = ["-d", distribution] if distribution else []
    try:
        result = subprocess.run(
            [str(executable), *selector, "--exec", "/usr/bin/python3", "-B", "-I", "-c", WSL_TEMP_CHECK],
            input=json.dumps([target]).encode("utf-8"), capture_output=True, timeout=2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0 and json.loads(result.stdout) == [target]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def _wsl_command_prefix(command: str) -> Optional[Tuple[str, str]]:
    """只识别明确的本机 WSL 前缀，保留发行版与内层原文。"""
    prefix = re.match(
        r"""^\s*(?:wsl(?:\.exe)?)\s+"""
        r"""(?:(?:-d|--distribution)\s+(?:"([A-Za-z0-9][A-Za-z0-9_.-]{0,63})"|"""
        r"""([A-Za-z0-9][A-Za-z0-9_.-]{0,63}))\s+)?"""
        r"""(?:(?:--exec|-e|--)\s+)?(?=\S)(?!-)""",
        command, re.IGNORECASE,
    )
    if not prefix:
        return None
    return prefix[1] or prefix[2] or "", command[prefix.end():]


def _outer_shell_operators(command: str) -> bool:
    """检测由 Windows 外层 shell 消费的控制或重定向符号。"""
    quote = ""
    escaped = False
    for char in command:
        if escaped:
            escaped = False
        elif char == chr(96) or (quote and char == "\\"):
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in ";|&><\r\n()":
            return True
    return bool(quote or escaped)


def _host_path_string(
    raw_str: str, base_dir: Optional[str] = None, *, from_shell: bool = True
) -> Optional[str]:
    """将盘符或 WSL 挂载路径还原为当前宿主可解析的路径字符串。"""
    expanded = user_words.strip_win_extended_prefix(
        _expand_host_vars(raw_str, base_cwd=base_dir, from_shell=from_shell)
    )
    if not expanded:
        return None
    if os.name == "nt" and expanded.startswith("/") and not expanded.startswith("//"):
        m_mnt = re.match(r"^/mnt/([A-Za-z])(?:/(.*))?$", expanded)
        if m_mnt:
            expanded = f"{m_mnt.group(1).upper()}:/{m_mnt.group(2) or ''}"
        else:
            return None
    elif re.match(r"^([A-Za-z]):(?![\\/])(.+)$", expanded):
        drv = expanded[0].lower()
        rel_part = expanded[2:].replace("\\", "/")
        same_drive_base = None
        if base_dir:
            b_norm = _normalize_cross_env_lexical(base_dir, None)
            drv_prefix = f"/mnt/{drv}"
            if b_norm == drv_prefix or b_norm.startswith(drv_prefix + "/"):
                b_sub = b_norm[len(drv_prefix) :].lstrip("/")
                same_drive_base = f"{b_sub}/{rel_part}" if b_sub else rel_part
        target_rel = same_drive_base if same_drive_base is not None else rel_part
        if os.name == "nt":
            expanded = f"{drv.upper()}:/{target_rel}"
        else:
            expanded = f"/mnt/{drv}/{target_rel}"
    elif os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", expanded):
        drv = expanded[0].lower()
        rest = expanded[2:].replace("\\", "/").lstrip("/")
        expanded = f"/mnt/{drv}/{rest}"

    return expanded


def _resolve_nearest_existing(
    raw_str: str, base_dir: Optional[str], *, from_shell: bool = True
) -> Optional[Path]:
    """解析真实物理位置；相对路径与基准目录必须使用同一种宿主路径格式。"""
    expanded = _host_path_string(raw_str, base_dir=base_dir, from_shell=from_shell)
    if expanded is None:
        return None
    p = Path(expanded)
    if not p.is_absolute():
        if not base_dir:
            return None
        base_expanded = _host_path_string(base_dir)
        if base_expanded is None:
            return None
        p = Path(base_expanded) / p

    try:
        return p.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _collect_boundary_context(
    payload: Dict[str, Any],
    state_dir: Path,
    logs_dir: Path,
) -> Tuple[List[str], List[str], List[str], str, Optional[str]]:
    """
    构建当前会话的项目目录边界上下文：
    返回 (project_norm_keys, allowed_norm_keys, allowed_resolved_keys, project_display, base_cwd)
    """
    workspace_paths = payload.get("workspacePaths") if isinstance(payload.get("workspacePaths"), list) else []
    cwd_raw = str(payload.get("cwd") or "").strip()

    raw_projects = [str(wp).strip() for wp in workspace_paths if wp and str(wp).strip()]
    if not raw_projects and cwd_raw:
        raw_projects = [cwd_raw]

    if not raw_projects:
        return [], [], [], "", cwd_raw or None

    tool_call = payload.get("toolCall")
    args = tool_call.get("args") if isinstance(tool_call, dict) else None
    raw_name = (tool_call.get("name") or tool_call.get("tool_name")) if isinstance(tool_call, dict) else ""
    context_tool = handoff_gate.AGY_TOOL_ALIASES.get(raw_name, raw_name)
    command_cwd = (
        (args.get("Cwd") or args.get("cwd"))
        if isinstance(args, dict) and context_tool in ("run_command", "run_workflow")
        else None
    )
    base_cwd = str(command_cwd or cwd_raw or raw_projects[0])
    if command_cwd:
        base_cwd = _ast_resolve_cwd(str(command_cwd), cwd_raw or raw_projects[0])
    project_norm_keys = [k for k in (_normalize_cross_env_lexical(p) for p in raw_projects) if k]
    workspace_in_os_temp = any(_is_standard_os_temp_key(pk) for pk in project_norm_keys)

    home_p = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or home_p / ".codex").expanduser()
    gemini_home = home_p / ".gemini"
    internal_raw: List[str] = [
        str(state_dir),
        str(logs_dir),
        str(home_p / ".cache"),
    ]
    internal_raw.extend(str(codex_home / name) for name in (
        "memories", "sessions", "cache", "tmp", ".tmp", "sqlite", "attachments", "visualizations",
    ))
    if payload.get("_client") == "claude":
        claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home_p / ".claude").expanduser()
        internal_raw.extend(str(claude_home / name) for name in (
            "projects", "todos", "session-env", "statsig", "telemetry", "hooks/state", "hooks/logs",
        ))
    for host in ("antigravity", "antigravity-cli", "antigravity-ide"):
        internal_raw.extend(str(gemini_home / host / name) for name in (
            "brain", "plugin_data", "conversations", "annotations", "knowledge", "scratch",
            "sidecar_data", "cache", "log", "logs",
        ))
    internal_raw.extend(str(gemini_home / name) for name in ("logs", "sandbox"))
    if not workspace_in_os_temp:
        internal_raw.append(tempfile.gettempdir())

    art_dir = str(payload.get("artifactDirectoryPath") or "").strip()
    if art_dir:
        internal_raw.append(art_dir)

    tp_raw = str(payload.get("transcriptPath") or "").strip()
    if tp_raw:
        tp_norm = tp_raw.replace("\\", "/")
        marker = "/.system_generated/"
        idx = tp_norm.lower().find(marker)
        if idx > 0:
            internal_raw.append(tp_raw[:idx])

    allowed_norm_keys: List[str] = []
    allowed_resolved_keys: List[str] = []

    for item in raw_projects + internal_raw:
        nk = _normalize_cross_env_lexical(item)
        rp = _resolve_nearest_existing(item, None)
        rk = _normalize_cross_env_lexical(str(rp)) if rp is not None else ""
        if item in raw_projects:
            if rk:
                allowed_resolved_keys.append(rk)
        elif rk and (rk == nk or item in (str(state_dir), str(logs_dir), tempfile.gettempdir())):
            allowed_resolved_keys.append(rk)
        else:
            # 缓存、轨迹等目录只有物理位置与登记的标准目录一致时才适用例外。
            continue
        if nk and nk not in allowed_norm_keys:
            allowed_norm_keys.append(nk)
        if (item in raw_projects or item in (str(state_dir), str(logs_dir), tempfile.gettempdir())) and rk:
            allowed_norm_keys.append(rk)

    project_display = ", ".join(raw_projects)
    return project_norm_keys, allowed_norm_keys, allowed_resolved_keys, project_display, base_cwd


_decode_ansi_c_quotes = _shell_lex.decode_ansi_c_quotes


_unquote_shell_word = _shell_lex.unquote_shell_word


def _is_write_target_allowed(
    raw_target: str,
    base_cwd: Optional[str],
    allowed_norm_keys: List[str],
    allowed_resolved_keys: List[str],
    allow_os_temp: bool = True,
    wsl_distribution: Optional[str] = None,
    from_shell: bool = True,
) -> bool:
    cleaned = (
        _unquote_shell_word(raw_target.strip()).strip("`;,)")
        if from_shell
        else raw_target.strip()
    )
    if not cleaned or cleaned.lower() in _NULL_SINKS:
        return True
    expanded_check = _expand_host_vars(cleaned, base_cwd=base_cwd, from_shell=from_shell)
    if _has_unexpanded_dynamic_var(
        expanded_check, is_cmd_or_win=(wsl_distribution is not None)
    ):
        return False

    lex_key = _normalize_cross_env_lexical(cleaned, base_cwd, from_shell=from_shell)
    if not lex_key:
        return True
    if not any(_is_under_norm_key(lex_key, rk) for rk in allowed_norm_keys):
        return (wsl_distribution is not None and allow_os_temp
                and _is_wsl_standard_temp_path(cleaned, wsl_distribution))

    # 若本地文件系统可解析真实路径，校验是否存在软链接/目录联接逃逸到允许目录外
    resolved_p = _resolve_nearest_existing(cleaned, base_cwd, from_shell=from_shell)
    if resolved_p is None:
        # 仅保留当前进程无法物理访问的原生 POSIX 项目路径的跨端词法比较。
        expanded = _expand_host_vars(cleaned, base_cwd=base_cwd, from_shell=from_shell)
        if os.name != "nt" or not expanded.startswith("/") or re.match(r"^/mnt/[A-Za-z](?:/|$)", expanded):
            return False
    if resolved_p is not None and allowed_resolved_keys:
        res_key = _normalize_cross_env_lexical(str(resolved_p))
        if not any(_is_under_norm_key(res_key, ark) for ark in allowed_resolved_keys):
            return False
    return True


# ⑤ 内联 Python / 动态工作流提取与 AST 写入目标分析

_read_quoted_arg = _shell_lex.read_quoted_arg


_UNCLOSED_INLINE_PY_SENTINEL = "\x00__UNCLOSED_INLINE_PYTHON__"


def _extract_and_mask_inline_python(command_line: str) -> Tuple[List[str], str]:
    """提取命令中的 python -c / PowerShell here-string / heredoc 代码块，并在原命令中按序号掩码以便对应子句工作目录"""
    blocks: List[str] = []

    def _alloc_marker(code: str) -> str:
        idx = len(blocks)
        blocks.append(code)
        return f"__INLINE_PY_{idx}__"

    masked = _PS_HERESTRING_PY_RE.sub(lambda m_ps: _alloc_marker(m_ps.group(2)), command_line)
    masked = _SH_HEREDOC_PY_RE.sub(lambda m_hd: _alloc_marker(m_hd.group(2)), masked)

    def _read_inline_py_word(text: str, start_idx: int, allow_concat: bool) -> Optional[Tuple[str, int]]:
        n = len(text)
        idx = start_idx
        while idx < n and text[idx] in (" ", "\t"):
            idx += 1
        if idx >= n or text[idx] in "\r\n;|&<>()":
            return None
        if not allow_concat:
            q_single = _read_quoted_arg(text, idx)
            return (q_single[0], q_single[1]) if q_single is not None else None
        parts: List[str] = []
        start_word = idx
        while idx < n and text[idx] not in " \t\r\n;|&<>()":
            if text[idx : idx + 2] == "$'":
                ansi = _decode_ansi_c_quotes(text, idx)
                if ansi is None:
                    return _UNCLOSED_INLINE_PY_SENTINEL, n
                parts.append(ansi[0])
                idx = ansi[1]
                continue
            if text[idx] == '"':
                dq = _read_quoted_arg(text, idx)
                if dq is None:
                    return _UNCLOSED_INLINE_PY_SENTINEL, n
                parts.append(dq[0])
                idx = dq[1]
                continue
            if text[idx] == "'":
                end_sq = text.find("'", idx + 1)
                if end_sq == -1:
                    return _UNCLOSED_INLINE_PY_SENTINEL, n
                parts.append(text[idx + 1 : end_sq])
                idx = end_sq + 1
                continue
            if text[idx] == "\\" and idx + 1 < n:
                parts.append(text[idx + 1])
                idx += 2
                continue
            parts.append(text[idx])
            idx += 1
        return ("".join(parts), idx) if idx > start_word else None

    out_parts: List[str] = []
    pos = 0
    while True:
        m = _PY_INLINE_HEAD_RE.search(masked, pos)
        if not m:
            out_parts.append(masked[pos:])
            break
        in_outer_quotes = _pos_inside_quotes(masked, m.start())
        q_res = _read_inline_py_word(masked, m.end(), allow_concat=not in_outer_quotes)
        if not q_res:
            out_parts.append(masked[pos : m.end()])
            pos = m.end()
            continue
        code_str, end_pos = q_res
        head_stripped = m.group(0).rstrip()
        if code_str != _UNCLOSED_INLINE_PY_SENTINEL and not head_stripped.endswith(("c", 'c"', "c'")):
            code_str = re.sub(r"^-[BbIdEisSuqvVxOP]*c", "", code_str, count=1)
        out_parts.append(masked[pos : m.start()])
        out_parts.append(_alloc_marker(code_str))
        pos = end_pos

    return blocks, "".join(out_parts)


def _is_anchored_path_str(path_str: str) -> bool:
    s = path_str.strip()
    return s.startswith(("/", "\\", "~")) or bool(re.match(r"^[A-Za-z]:", s))


def _join_ast_path_parts(parts: List[str]) -> Optional[str]:
    valid = [s for s in parts if s]
    if not valid:
        return None
    acc = valid[0].rstrip("/\\") or valid[0]
    for seg in valid[1:]:
        if _is_anchored_path_str(seg):
            acc = seg.rstrip("/\\") or seg
        else:
            acc = (acc.rstrip("/\\") + "/" + seg.lstrip("/\\")) if acc else seg
    return acc


# 使用对象身份标记未知值，脚本中的同名字符串不能伪造安全状态。
_AST_DYNAMIC = object()
_AST_CWD = object()
_AST_DEFINITION = object()
_AST_MAX_CANDIDATES = 64
_AST_OS_EXEC_SPAWN = {
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "posix_spawn", "posix_spawnp",
}
_AST_MODULE_CALLS = {
    "os": {"chdir", "getcwd", "open", "remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs", "rename",
           "replace", "renames", "truncate", "symlink", "link", "chmod", "lchmod", "chown", "lchown", "utime",
           "system", "popen", *_AST_OS_EXEC_SPAWN},
    "sys": {"executable"},
    "shutil": {"rmtree", "move", "copy", "copy2", "copyfile", "copytree", "make_archive", "unpack_archive"},
    "tempfile": {"gettempdir", "mkdtemp", "TemporaryDirectory", "mkstemp", "NamedTemporaryFile"},
    "subprocess": {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
    "numpy": {"save", "savez", "savez_compressed", "savetxt"},
}
_AST_TEMP_DIR_INDEX = {"mkdtemp": 2, "TemporaryDirectory": 2, "mkstemp": 2, "NamedTemporaryFile": 6}
_AST_WRITE_CALL_ARGS = {
    **{"os." + name: ((0, ("path",)),) for name in
       ("remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs", "truncate",
        "chmod", "lchmod", "chown", "lchown", "utime")},
    **{"os." + name: ((0, ("src",)), (1, ("dst",))) for name in ("rename", "replace", "renames")},
    **{"os." + name: ((1, ("dst",)),) for name in ("symlink", "link")},
    "shutil.rmtree": ((0, ("path",)),),
    "shutil.move": ((0, ("src",)), (1, ("dst",))),
    **{"shutil." + name: ((1, ("dst",)),) for name in ("copy", "copy2", "copyfile", "copytree")},
    "shutil.make_archive": ((0, ("base_name",)),),
    "shutil.unpack_archive": ((1, ("extract_dir",)),),
}


class _AstModule:
    def __init__(self, name):
        self.name = name


class _AstCallable:
    def __init__(self, qualname):
        self.qualname = qualname


class _AstDict:
    def __init__(self, fields=None, uncertain=False):
        self.fields = fields or {}
        self.uncertain = uncertain


class _AstTempPath(str):
    """仅由已识别的 tempfile 工厂产生，普通字符串无法伪造此类型。"""


def _copy_ast_bindings(bindings):
    memo = {}

    def copy_value(value):
        if isinstance(value, _AstDict):
            if value not in memo:
                memo[value] = _AstDict(uncertain=value.uncertain)
                memo[value].fields = {key: frozenset(copy_value(v) for v in values) for key, values in value.fields.items()}
            return memo[value]
        return value

    return {key: frozenset(copy_value(value) for value in _ast_values(values)) for key, values in bindings.items()}


def _ast_resolve_cwd(value, base):
    normalized = _normalize_cross_env_lexical(value, base)
    resolved = _resolve_nearest_existing(normalized, None)
    return str(resolved) if resolved is not None else normalized


def _ast_values(value):
    return value if isinstance(value, frozenset) else frozenset((value,))


def _ast_product(parts, combine):
    if not parts or any(_AST_DYNAMIC in part for part in parts):
        return frozenset((_AST_DYNAMIC,))
    result = set()
    for values in itertools.product(*parts):
        if not all(isinstance(value, str) for value in values):
            return frozenset((_AST_DYNAMIC,))
        combined = combine(values)
        if any(isinstance(value, _AstTempPath) for value in values):
            root = _normalize_cross_env_lexical(tempfile.gettempdir())
            if _is_under_norm_key(_normalize_cross_env_lexical(combined), root):
                combined = _AstTempPath(combined)
        result.add(combined)
        if len(result) >= _AST_MAX_CANDIDATES:
            return frozenset((_AST_DYNAMIC,))
    return frozenset(result)


def _ast_call_qualnames(fn, bindings):
    names = {value.qualname for value in _eval_ast_path_candidates(fn, bindings) if isinstance(value, _AstCallable)}
    # 保留原有对直接模块调用的识别；显式绑定后只按该绑定判断。
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id not in bindings:
        module = "numpy" if fn.value.id == "np" else fn.value.id
        if module in _AST_MODULE_CALLS:
            names.add(module + "." + fn.attr)
    return names


def _ast_call_arg_candidates(node, index, keywords, bindings, default=_AST_DYNAMIC):
    """合并位置与关键字实参；影响该参数位置的解包及未知关键字均保留动态候选。"""
    values = set()
    if len(node.args) > index and not isinstance(node.args[index], ast.Starred):
        values.update(_eval_ast_path_candidates(node.args[index], bindings))
    for kw in node.keywords:
        if kw.arg in keywords:
            values.update(_eval_ast_path_candidates(kw.value, bindings))
    if not values:
        values.update(_ast_values(default))
    if any(isinstance(arg, ast.Starred) for arg in node.args[:index + 1]) or any(kw.arg is None for kw in node.keywords):
        values.add(_AST_DYNAMIC)
    return frozenset(values)


def _ast_has_unpacking(node):
    return any(isinstance(arg, ast.Starred) for arg in node.args) or any(kw.arg is None for kw in node.keywords)


def _ast_temp_path_candidates(node, name, bindings):
    if _ast_has_unpacking(node):
        return frozenset((_AST_DYNAMIC,))
    if name == "gettempdir":
        return frozenset((_AST_DYNAMIC,)) if node.args or node.keywords else frozenset((_AstTempPath(tempfile.gettempdir()),))
    parents = _ast_call_arg_candidates(node, _AST_TEMP_DIR_INDEX[name], ("dir",), bindings, None)
    return frozenset(_AstTempPath(tempfile.gettempdir()) if parent is None else parent for parent in parents)


def _eval_ast_path_candidates(node, bindings):
    """保留全部候选绑定；表达式存在但无法求值时显式返回未知值。"""
    dynamic = frozenset((_AST_DYNAMIC,))
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, type(None))):
        return frozenset((node.value,))
    if isinstance(node, ast.Lambda):
        return frozenset((node,))
    if isinstance(node, ast.Name):
        return _ast_values(bindings.get(node.id, _AST_DYNAMIC))
    if isinstance(node, ast.Attribute):
        if node.attr == "executable":
            if isinstance(node.value, ast.Name) and node.value.id == "sys" and "sys" not in bindings:
                return frozenset((sys.executable,))
            owners = _eval_ast_path_candidates(node.value, bindings)
            if any(isinstance(owner, _AstModule) and owner.name == "sys" for owner in owners):
                return frozenset((sys.executable,))
        values = frozenset(_AstCallable(owner.name + "." + node.attr)
                           for owner in _eval_ast_path_candidates(node.value, bindings) if isinstance(owner, _AstModule))
        return values or dynamic
    if isinstance(node, (ast.List, ast.Tuple)):
        parts = [_eval_ast_path_candidates(elt, bindings) for elt in node.elts]
        if any(_AST_DYNAMIC in part for part in parts):
            return dynamic
        values = set()
        for items in itertools.product(*parts):
            values.add(items)
            if len(values) >= _AST_MAX_CANDIDATES:
                return dynamic
        return frozenset(values)
    if isinstance(node, ast.NamedExpr):
        return _eval_ast_path_candidates(node.value, bindings)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                if part.conversion not in (-1, 115):
                    parts.append(dynamic)
                    continue
                fmt_spec: Optional[str] = ""
                if part.format_spec is not None:
                    if isinstance(part.format_spec, ast.JoinedStr) and all(
                        isinstance(seg, ast.Constant) and isinstance(seg.value, str) for seg in part.format_spec.values
                    ):
                        fmt_spec = "".join(seg.value for seg in part.format_spec.values)
                    else:
                        fmt_spec = None
                if fmt_spec is None:
                    parts.append(dynamic)
                    continue
                formatted = set()
                for cand_val in _eval_ast_path_candidates(part.value, bindings):
                    if isinstance(cand_val, (str, int)) and not isinstance(cand_val, bool):
                        try:
                            formatted.add(format(cand_val, fmt_spec) if fmt_spec else str(cand_val))
                        except (ValueError, TypeError):
                            formatted.add(_AST_DYNAMIC)
                    else:
                        formatted.add(_AST_DYNAMIC)
                parts.append(frozenset(formatted) or dynamic)
            else:
                parts.append(_eval_ast_path_candidates(part, bindings))
        return _ast_product(parts, lambda values: "".join(values))
    if isinstance(node, ast.IfExp):
        return _eval_ast_path_candidates(node.body, bindings) | _eval_ast_path_candidates(node.orelse, bindings)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left, right = (_eval_ast_path_candidates(expr, bindings) for expr in (node.left, node.right))
        if isinstance(node.op, ast.Add):
            return _ast_product([left, right], lambda values: "".join(values))
        # 绝对右操作数覆盖左操作数；未知值始终传播。
        return _ast_product([left, right], lambda values: _join_ast_path_parts(list(values)))
    if isinstance(node, ast.Dict):
        states = [_AstDict()]
        for key, value in zip(node.keys, node.values):
            values = _eval_ast_path_candidates(value, bindings)
            if key is None:
                expanded = []
                for state in states:
                    for other in values:
                        if isinstance(other, _AstDict):
                            expanded.append(_AstDict({**state.fields, **other.fields}, state.uncertain or other.uncertain))
                        else:
                            expanded.append(_AstDict(dict(state.fields), True))
                states = expanded
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                for state in states:
                    state.fields[key.value] = values  # Python 重复键由后值覆盖。
            else:
                for state in states:
                    state.uncertain = True
            if len(states) > _AST_MAX_CANDIDATES:
                return dynamic
        return frozenset(states)
    if isinstance(node, ast.Subscript):
        keys = _eval_ast_path_candidates(node.slice, bindings)
        result = set()
        for value in _eval_ast_path_candidates(node.value, bindings):
            for key in keys:
                result.update(value.fields.get(key, dynamic) if isinstance(value, _AstDict) and not value.uncertain else dynamic)
        return frozenset(result) or dynamic
    if isinstance(node, ast.Call):
        qualnames = _ast_call_qualnames(node.func, bindings)
        temp_names = qualnames & {"tempfile." + name for name in _AST_MODULE_CALLS["tempfile"]}
        if temp_names:
            return frozenset().union(*(_ast_temp_path_candidates(node, name.split(".")[1], bindings) for name in temp_names))
        if "os.getcwd" in qualnames and not node.args and not node.keywords:
            return frozenset((c or ".") if isinstance(c, str) else _AST_DYNAMIC for c in _ast_values(bindings.get(_AST_CWD, ".")))
        name = node.func.id if isinstance(node.func, ast.Name) else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
        if name == "home" and isinstance(node.func, ast.Attribute) and not node.args and not node.keywords:
            return frozenset(("~",))
        if name == "cwd" and isinstance(node.func, ast.Attribute) and not node.args and not node.keywords:
            return frozenset((c or ".") if isinstance(c, str) else _AST_DYNAMIC for c in _ast_values(bindings.get(_AST_CWD, ".")))
        if name in ("Path", "PurePath", "WindowsPath", "PosixPath", "expanduser", "abspath", "realpath", "resolve", "join", "joinpath", "str"):
            parts = [_eval_ast_path_candidates(arg, bindings) for arg in node.args]
            is_path_ctor = name in ("Path", "PurePath", "WindowsPath", "PosixPath")
            if name in ("joinpath", "expanduser", "resolve") or (not node.args and isinstance(node.func, ast.Attribute) and not is_path_ctor):
                parts.insert(0, _eval_ast_path_candidates(node.func.value, bindings))
            if not parts and is_path_ctor:
                return frozenset((".",))
            return _ast_product(parts, lambda values: _join_ast_path_parts(list(values)))
    return dynamic


def _eval_ast_path_expr(node: ast.AST, bindings: Dict[str, Any]) -> Optional[str]:
    """保留原单路径查询接口；安全检查使用完整候选集合。"""
    values = _eval_ast_path_candidates(node, bindings)
    return next(iter(values)) if len(values) == 1 and all(isinstance(v, str) for v in values) else None


def _format_ast_candidates(values):
    return ", ".join(sorted(v if isinstance(v, str) else "<DYNAMIC_UNRESOLVED>" for v in _ast_values(values)))


def _open_mode_is_mutating(call_node: ast.Call, mode_pos_idx: int, bindings=None) -> Tuple[bool, str]:
    values = _ast_call_arg_candidates(call_node, mode_pos_idx, ("mode",), bindings or {}, "r")
    if any(isinstance(arg, ast.Starred) for arg in call_node.args):
        values |= frozenset((_AST_DYNAMIC,))
    readonly = all(isinstance(v, str) and v and "r" in v and set(v) <= set("rbtU") for v in values)
    return not readonly, _format_ast_candidates(values)


def _bind_ast_target(target, value, bindings):
    if isinstance(target, ast.Name):
        bindings[target.id] = value
    elif isinstance(target, (ast.Tuple, ast.List)):
        # value 为解包后的逐项候选集合，长度不匹配时清除全部旧绑定。
        items = value if isinstance(value, list) and len(value) == len(target.elts) else [frozenset((_AST_DYNAMIC,))] * len(target.elts)
        for child, item in zip(target.elts, items):
            _bind_ast_target(child, item, bindings)
    elif isinstance(target, ast.Subscript):
        keys = _eval_ast_path_candidates(target.slice, bindings)
        for container in _eval_ast_path_candidates(target.value, bindings):
            if isinstance(container, _AstDict):
                if len(keys) == 1 and all(isinstance(key, str) for key in keys):
                    container.fields[next(iter(keys))] = value
                else:
                    container.uncertain = True


def _merge_ast_bindings(bindings, alternatives):
    for key in set().union(*(set(state) for state in alternatives)):
        bindings[key] = frozenset().union(*(_ast_values(state.get(key, _AST_DYNAMIC)) for state in alternatives))


def _iter_ast_calls_in_order(node, bindings, active=()):
    """按求值顺序遍历；分支保守合并，函数按每次调用时的绑定重新审查。"""
    walk = lambda child, scope=bindings: _iter_ast_calls_in_order(child, scope, active)
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
        if node.value is not None:
            yield from walk(node.value)
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, (ast.List, ast.Tuple)) and isinstance(node.value, (ast.List, ast.Tuple)):
                value = [_eval_ast_path_candidates(expr, bindings) for expr in node.value.elts]
            else:
                value = _eval_ast_path_candidates(node.value, bindings)
            _bind_ast_target(target, value, bindings)
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        for decorator in getattr(node, "decorator_list", ()):
            yield from walk(decorator)
        params = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
        for value in defaults:
            yield from walk(value)
        node._guard_defaults = {arg.arg: _eval_ast_path_candidates(value, bindings)
                                for arg, value in zip((list(node.args.posonlyargs) + list(node.args.args))[-len(node.args.defaults):], node.args.defaults)}
        node._guard_defaults.update({arg.arg: _eval_ast_path_candidates(value, bindings)
                                    for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults) if value is not None})
        if not isinstance(node, ast.Lambda):
            bindings[node.name] = frozenset((node,))
        # 定义时检查已知危险路径；未知形参留待调用点叠加实参后核验。
        local = _copy_ast_bindings(bindings)
        local[_AST_DEFINITION] = frozenset((True,))
        for arg in params:
            local[arg.arg] = node._guard_defaults.get(arg.arg, frozenset((_AST_DYNAMIC,)))
        for stmt in node.body if isinstance(node.body, list) else [node.body]:
            yield from _iter_ast_calls_in_order(stmt, local, active + (node,))
        return
    if isinstance(node, ast.ClassDef):
        for expr in list(node.decorator_list) + list(node.bases) + list(node.keywords):
            yield from walk(expr)
        local = _copy_ast_bindings(bindings)
        for stmt in node.body:
            yield from walk(stmt, local)
        bindings[node.name] = frozenset((_AST_DYNAMIC,))
        return
    if isinstance(node, ast.ExceptHandler):
        if node.type is not None:
            yield from walk(node.type)
        if node.name:
            bindings[node.name] = frozenset((_AST_DYNAMIC,))
        for stmt in node.body:
            yield from walk(stmt)
        return
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        local = _copy_ast_bindings(bindings)
        for gen in node.generators:
            yield from walk(gen.iter, local)
            _bind_ast_target(gen.target, frozenset((_AST_DYNAMIC,)), local)
            for condition in gen.ifs:
                yield from walk(condition, local)
        for expr in (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,):
            yield from walk(expr, local)
        return
    if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.BoolOp, ast.Match, ast.Try, ast.TryStar)):
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            yield from walk(node.test)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            yield from walk(node.iter)
        branches = []
        if isinstance(node, ast.Match):
            yield from walk(node.subject)
            branches = [[case] for case in node.cases]
        elif isinstance(node, ast.BoolOp):
            branches = [[value] for value in node.values]
        else:
            for field in ("body", "orelse", "handlers"):
                branch = getattr(node, field, [])
                branches.append(branch if isinstance(branch, list) else [branch])
        alternatives = [_copy_ast_bindings(bindings)]
        for branch in branches:
            local = _copy_ast_bindings(bindings)
            if isinstance(node, (ast.For, ast.AsyncFor)):
                _bind_ast_target(node.target, frozenset((_AST_DYNAMIC,)), local)
            if isinstance(node, ast.Match):
                case = branch[0]
                for pattern in ast.walk(case.pattern):
                    # MatchAs / MatchStar 使用 name，MatchMapping 使用 rest。
                    for field in ("name", "rest"):
                        name = getattr(pattern, field, None)
                        if name:
                            local[name] = frozenset((_AST_DYNAMIC,))
                if case.guard is not None:
                    yield from walk(case.guard, local)
                branch = case.body
            for stmt in branch:
                yield from walk(stmt, local)
            alternatives.append(local)
        _merge_ast_bindings(bindings, alternatives)
        for stmt in getattr(node, "finalbody", []):
            yield from walk(stmt)
        return
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            yield from walk(item.context_expr)
            if item.optional_vars is not None:
                _bind_ast_target(item.optional_vars, _eval_ast_path_candidates(item.context_expr, bindings), bindings)
        for stmt in node.body:
            yield from walk(stmt)
        return
    if isinstance(node, ast.ImportFrom) and node.module in _AST_MODULE_CALLS:
        for alias in node.names:
            if alias.name in _AST_MODULE_CALLS[node.module]:
                if node.module == "sys" and alias.name == "executable":
                    bindings[alias.asname or alias.name] = frozenset((sys.executable,))
                else:
                    bindings[alias.asname or alias.name] = frozenset((_AstCallable(node.module + "." + alias.name),))
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in _AST_MODULE_CALLS:
                bindings[alias.asname or alias.name] = frozenset((_AstModule(alias.name),))
    if isinstance(node, (ast.AugAssign, ast.Delete)):
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                bindings[child.id] = frozenset((_AST_DYNAMIC,))
    if isinstance(node, ast.Call):
        # 参数中的海象赋值先于外层调用生效。
        yield from walk(node.func)
        for arg in node.args:
            yield from walk(arg)
        for kw in node.keywords:
            yield from walk(kw.value)
        yield node, bindings
        functions = _eval_ast_path_candidates(node.func, bindings)
        for function in functions:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if function in active or len(active) >= 12:
                # 递归无法继续静态展开时，将后续绑定置脏，避免沿用安全值。
                for key in list(bindings):
                    if isinstance(key, str):
                        bindings[key] = _ast_values(bindings[key]) | frozenset((_AST_DYNAMIC,))
                continue
            local = _copy_ast_bindings(bindings)
            local.pop(_AST_DEFINITION, None)
            params = list(function.args.posonlyargs) + list(function.args.args)
            for arg in params + list(function.args.kwonlyargs):
                local[arg.arg] = function._guard_defaults.get(arg.arg, frozenset((_AST_DYNAMIC,)))
            for param, value in zip(params, node.args):
                local[param.arg] = _eval_ast_path_candidates(value, bindings)
            for kw in node.keywords:
                if kw.arg:
                    local[kw.arg] = _eval_ast_path_candidates(kw.value, bindings)
            if any(isinstance(arg, ast.Starred) for arg in node.args) or any(kw.arg is None for kw in node.keywords):
                for arg in params + list(function.args.kwonlyargs):
                    local[arg.arg] = _ast_values(local[arg.arg]) | frozenset((_AST_DYNAMIC,))
            for stmt in function.body if isinstance(function.body, list) else [function.body]:
                yield from _iter_ast_calls_in_order(stmt, local, active + (function,))
            bindings[_AST_CWD] = local.get(_AST_CWD, bindings.get(_AST_CWD, frozenset(("",))))
            for stmt in ast.walk(function):
                if isinstance(stmt, (ast.Global, ast.Nonlocal)):
                    for name in stmt.names:
                        bindings[name] = local.get(name, frozenset((_AST_DYNAMIC,)))
        return
    for child in ast.iter_child_nodes(node):
        yield from walk(child)


def _ast_bad_target(values, bindings, base_cwd, allowed_norm_keys, allowed_resolved_keys,
                    allow_os_temp, wsl_distribution=None):
    cwds = _ast_values(bindings.get(_AST_CWD, base_cwd))
    for value in _ast_values(values):
        if not isinstance(value, str):
            if _AST_DEFINITION not in bindings:
                return True
            continue
        if isinstance(value, _AstTempPath):
            root = tempfile.gettempdir()
            normalized = [_normalize_cross_env_lexical(root)]
            rp = _resolve_nearest_existing(root, None)
            resolved = [_normalize_cross_env_lexical(str(rp))] if rp is not None else normalized
            if _is_write_target_allowed(value, base_cwd, normalized, resolved):
                continue
        if any(not isinstance(cwd, (str, type(None))) or not _is_write_target_allowed(
            value, cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, wsl_distribution
        ) for cwd in cwds):
            return True
    return False


def _track_ast_chdir(node, bindings, base_cwd, allowed_norm_keys, allowed_resolved_keys,
                     allow_os_temp, snippet, wsl_distribution=None):
    if "os.chdir" not in _ast_call_qualnames(node.func, bindings):
        return None
    values = _ast_call_arg_candidates(node, 0, ("path",), bindings)
    if _ast_bad_target(values, bindings, base_cwd, allowed_norm_keys, allowed_resolved_keys,
                       allow_os_temp, wsl_distribution):
        return "Python 越界或动态切换工作目录 (os.chdir)", _format_ast_candidates(values), snippet
    next_cwds = set()
    for cur_c in _ast_values(bindings.get(_AST_CWD, base_cwd)):
        for target_c in values:
            if not isinstance(target_c, str):
                next_cwds.add(_AST_DYNAMIC)
            elif isinstance(cur_c, str):
                next_cwds.add(_ast_resolve_cwd(target_c, cur_c))
            elif cur_c is None and _is_anchored_path_str(target_c):
                next_cwds.add(_ast_resolve_cwd(target_c, None))
            else:
                next_cwds.add(_AST_DYNAMIC)
    bindings[_AST_CWD] = frozenset(next_cwds) or frozenset((_AST_DYNAMIC,))
    return None


def _scan_inline_python_code(
    py_code: str,
    base_cwd: Optional[str],
    allowed_norm_keys: List[str],
    allowed_resolved_keys: List[str],
    allow_os_temp: bool,
    depth: int = 0,
    wsl_distribution: Optional[str] = None,
    tree: Optional[ast.AST] = None,
) -> Optional[Tuple[str, str, str]]:
    """使用 AST 精确识别内联 Python 脚本中真正发生写/删/改的外部路径（忽略一切只读 open 与数组 .copy()）"""
    if py_code == _UNCLOSED_INLINE_PY_SENTINEL:
        return "内联 Python 参数引号未闭合，无法静态确证安全", "<UNCLOSED_INLINE_PYTHON>", "python -c <unclosed>"
    has_raw_nul = "\x00" in py_code
    clean_code = py_code.split("\x00", 1)[0] if has_raw_nul else py_code
    candidates = [py_code]
    if has_raw_nul and clean_code not in candidates:
        candidates.append(clean_code)
    if '\\"' in clean_code or "\\'" in clean_code or "\\n" in clean_code:
        candidates.append(clean_code.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n"))

    used_src = clean_code
    saw_recursion_error = False
    if tree is None:
        for cand_src in candidates:
            try:
                tree = ast.parse(cand_src)
                used_src = cand_src
                break
            except RecursionError:
                saw_recursion_error = True
                break
            except (SyntaxError, ValueError):
                continue
    if saw_recursion_error:
        return "内联 Python 语法树嵌套深度超出静态审查上限", "<AST_RECURSION_LIMIT>", _compact_snippet(used_src)

    bindings = {_AST_CWD: frozenset((base_cwd,))}

    def _bad(target) -> bool:
        return _ast_bad_target(target, bindings, base_cwd, allowed_norm_keys, allowed_resolved_keys,
                               allow_os_temp, wsl_distribution)

    if tree is not None:
        try:
            ast_calls = list(_iter_ast_calls_in_order(tree, bindings))
        except RecursionError:
            return "内联 Python 语法树嵌套深度超出静态审查上限", "<AST_RECURSION_LIMIT>", _compact_snippet(used_src)
        for node, bindings in ast_calls:
            snippet = _compact_snippet(ast.get_source_segment(used_src, node) or "python call")
            fn = node.func
            chdir_hit = _track_ast_chdir(node, bindings, base_cwd, allowed_norm_keys, allowed_resolved_keys,
                                        allow_os_temp, snippet, wsl_distribution)
            if chdir_hit:
                return chdir_hit

            # 模块函数先于同名 Path 方法检查，模块与可调用标识不能作为路径字符串使用。
            qualnames = _ast_call_qualnames(fn, bindings)
            for qualname in qualnames:
                if qualname == "os.open":
                    flags = _ast_call_arg_candidates(node, 1, ("flags",), bindings)
                    if not all(type(value) is int and value == 0 for value in flags):
                        tgt = _ast_call_arg_candidates(node, 0, ("path",), bindings)
                        if _bad(tgt):
                            return "Python 系统文件写入 (os.open)", _format_ast_candidates(tgt), snippet
                elif qualname in _AST_WRITE_CALL_ARGS:
                    for index, keywords in _AST_WRITE_CALL_ARGS[qualname]:
                        default = "." if qualname == "shutil.unpack_archive" else _AST_DYNAMIC
                        tgt = _ast_call_arg_candidates(node, index, keywords, bindings, default)
                        if _bad(tgt):
                            return f"Python 系统文件写删操作 ({qualname})", _format_ast_candidates(tgt), snippet
                elif qualname.startswith("tempfile.") and qualname.split(".")[1] in _AST_TEMP_DIR_INDEX:
                    tgt = _ast_temp_path_candidates(node, qualname.split(".")[1], bindings)
                    if _bad(tgt):
                        return f"Python 创建临时目录或文件 ({qualname})", _format_ast_candidates(tgt), snippet
                elif qualname in {"numpy." + name for name in _AST_MODULE_CALLS["numpy"]}:
                    tgt = _ast_call_arg_candidates(node, 0, ("file", "fname"), bindings)
                    if _bad(tgt):
                        return f"NumPy 数组落盘 ({qualname})", _format_ast_candidates(tgt), snippet
                elif qualname in {"os." + name for name in _AST_OS_EXEC_SPAWN}:
                    if _AST_DEFINITION not in bindings:
                        return f"Python 进程替换或派生执行 ({qualname})", "<EXEC_OR_SPAWN>", snippet
                elif qualname in ("os.system", "os.popen") or qualname in {
                    "subprocess." + name for name in _AST_MODULE_CALLS["subprocess"]
                }:
                    if depth >= MAX_WRAPPER_DEPTH:
                        if _AST_DEFINITION in bindings:
                            continue
                        return "深层嵌套命令超出静态目录边界审查上限", "<NESTED_DEPTH_EXCEEDED>", snippet
                    cwds = _ast_values(bindings.get(_AST_CWD, base_cwd))
                    if qualname.startswith("subprocess.") and any(kw.arg == "cwd" for kw in node.keywords):
                        raw_cwds = frozenset().union(*(
                            _eval_ast_path_candidates(kw.value, bindings) for kw in node.keywords if kw.arg == "cwd"
                        ))
                        specified = raw_cwds - frozenset((None,))
                        if _bad(specified):
                            return "Python 子进程指定项目外或动态 Cwd", _format_ast_candidates(specified), snippet
                        resolved = _ast_product([cwds, specified], lambda parts: _ast_resolve_cwd(parts[1], parts[0])) if specified else frozenset()
                        cwds = resolved | (cwds if None in raw_cwds else frozenset())
                    commands = _ast_call_arg_candidates(node, 0, ("args", "command", "cmd"), bindings)
                    for command in commands:
                        direct_py_code: Optional[str] = None
                        if isinstance(command, tuple) and all(isinstance(part, str) for part in command):
                            if command:
                                exe_stem = Path(command[0].replace("\\", "/")).name.lower()
                                if exe_stem.endswith(".exe"):
                                    exe_stem = exe_stem[:-4]
                                if exe_stem == "py" or exe_stem.startswith("python"):
                                    idx_arg = 1
                                    while idx_arg < len(command):
                                        tok = command[idx_arg]
                                        if tok == "--":
                                            break
                                        if tok in ("-W", "-X", "-Q", "--check-hash-based-pycs"):
                                            idx_arg += 2
                                            continue
                                        if tok == "--safe-path" or tok.startswith(("-W", "-X", "-Q", "--check-hash-based-pycs=")):
                                            idx_arg += 1
                                            continue
                                        m_inline_c = re.fullmatch(r"-[BbIdEisSuqvVxOP]*c(.*)", tok, re.DOTALL)
                                        if m_inline_c:
                                            if m_inline_c.group(1):
                                                direct_py_code = m_inline_c.group(1)
                                            elif idx_arg + 1 < len(command):
                                                direct_py_code = command[idx_arg + 1]
                                            break
                                        if not tok.startswith("-") or tok == "-":
                                            break
                                        idx_arg += 1
                            command = " ".join(shlex.quote(part) for part in command)
                        if not isinstance(command, str):
                            if _AST_DEFINITION in bindings:
                                continue
                            return "Python 子进程命令无法静态确定", "<DYNAMIC_UNRESOLVED>", snippet
                        for cwd in cwds:
                            if not isinstance(cwd, (str, type(None))):
                                if _AST_DEFINITION in bindings:
                                    continue
                                return "Python 子进程工作目录无法静态确定", "<DYNAMIC_UNRESOLVED>", snippet
                            if direct_py_code is not None:
                                py_hit = _scan_inline_python_code(
                                    direct_py_code, cwd, allowed_norm_keys, allowed_resolved_keys,
                                    allow_os_temp, depth + 1, wsl_distribution,
                                )
                                if py_hit:
                                    return py_hit
                            sub_hit = _detect_command_external_mutation(
                                command, cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp,
                                depth + 1, wsl_distribution,
                            )
                            if sub_hit:
                                return sub_hit
            if qualnames:
                continue

            # 内置 open 与 Path 方法分别检查路径、模式和解包参数。
            if isinstance(fn, ast.Name) and fn.id == "open":
                is_mut, mode_s = _open_mode_is_mutating(node, 1, bindings)
                if is_mut:
                    tgt = _ast_call_arg_candidates(node, 0, ("file",), bindings)
                    if _bad(tgt):
                        return f"Python 写入/修改文件 (open mode='{mode_s}')", _format_ast_candidates(tgt), snippet

            elif isinstance(fn, ast.Attribute):
                attr = fn.attr

                # 2) Path(p).open(mode)
                if attr == "open":
                    is_mut, mode_s = _open_mode_is_mutating(node, 0, bindings)
                    if is_mut:
                        tgt = _eval_ast_path_candidates(fn.value, bindings)
                        if _bad(tgt):
                            return f"Python 写入/修改文件 (.open mode='{mode_s}')", _format_ast_candidates(tgt), snippet

                # 3) Path(p).write_text / write_bytes / unlink / rmdir / mkdir / touch / chmod / lchmod
                elif attr in ("write_text", "write_bytes", "unlink", "rmdir", "mkdir", "touch", "symlink_to", "hardlink_to", "chmod", "lchmod"):
                    tgt = _eval_ast_path_candidates(fn.value, bindings)
                    if _bad(tgt):
                        return f"Python 文件写删操作 (.{attr})", _format_ast_candidates(tgt), snippet

                # 4) Path(p).rename(dst) / replace(dst)
                elif attr in ("rename", "replace"):
                    if attr == "replace" and (
                        (not any(isinstance(arg, ast.Starred) for arg in node.args) and len(node.args) >= 2)
                        or any(kw.arg in ("old", "new", "count") for kw in node.keywords)
                    ):
                        continue
                    target = _ast_call_arg_candidates(node, 0, ("target",), bindings)
                    if _ast_has_unpacking(node):
                        target |= frozenset((_AST_DYNAMIC,))
                    for tgt in (_eval_ast_path_candidates(fn.value, bindings), target):
                        if _bad(tgt):
                            return f"Python 移动/替换文件 (.{attr})", _format_ast_candidates(tgt), snippet

                elif attr in ("savefig", "to_csv", "to_parquet", "to_excel", "to_feather", "to_pickle"):
                    keywords = ("fname",) if attr == "savefig" else (("excel_writer",) if attr == "to_excel" else ("path_or_buf", "path"))
                    default = None if attr in ("to_csv", "to_parquet") else _AST_DYNAMIC
                    tgt = _ast_call_arg_candidates(node, 0, keywords, bindings, default)
                    if attr in ("to_csv", "to_parquet"):
                        tgt -= frozenset((None,))
                    if _bad(tgt):
                        return f"Python 数据/图表导出 (.{attr})", _format_ast_candidates(tgt), snippet
        if has_raw_nul:
            return "内联 Python 含空字节无法静态确证安全", "<NUL_BYTE>", _compact_snippet(py_code.replace("\x00", "\\x00"))
        return None

    # AST 无法解析时（如混入未展开的 Shell 语法），仅使用严格“写函数-目标参数”配对正则兜底
    for label, pat in _PY_FALLBACK_PAIRS:
        for m in pat.finditer(used_src):
            cand = m.group(1).strip("\"'")
            if _bad(cand):
                return label, cand, _compact_snippet(m.group(0))
    if has_raw_nul:
        return "内联 Python 含空字节无法静态确证安全", "<NUL_BYTE>", _compact_snippet(py_code.replace("\x00", "\\x00"))
    return None


def _ast_dict_field(fields, keys, default=_AST_DYNAMIC):
    values = [fields[key] for key in keys if key in fields]
    return frozenset().union(*values) if values else _ast_values(default)


def _ast_workflow_tool_arguments(node, bindings):
    """仅展开静态确定的序列与字典；未知解包不能覆盖已知安全实参而获准。"""
    positions = [()]
    for arg in node.args:
        if isinstance(arg, ast.Starred):
            expanded = _eval_ast_path_candidates(arg.value, bindings)
            if any(not isinstance(value, tuple) for value in expanded):
                return None
            positions = [prev + tuple(_ast_values(item) for item in value) for prev in positions for value in expanded]
        else:
            values = _eval_ast_path_candidates(arg, bindings)
            positions = [prev + (values,) for prev in positions]
        if len(positions) > _AST_MAX_CANDIDATES:
            return None
    fields = _eval_ast_path_candidates(ast.Dict(
        keys=[ast.Constant(value=kw.arg) if kw.arg is not None else None for kw in node.keywords],
        values=[kw.value for kw in node.keywords],
    ), bindings)
    if any(not isinstance(value, _AstDict) or value.uncertain for value in fields):
        return None
    if len(positions) * len(fields) > _AST_MAX_CANDIDATES:
        return None
    return [
        (args[0] if args else _ast_dict_field(kw.fields, ("name", "tool_name", "ToolName")),
         args[1] if len(args) > 1 else _ast_dict_field(kw.fields, ("args", "Arguments", "arguments")))
        for args in positions for kw in fields
    ]


def _scan_workflow_python_code(
    py_code: str,
    base_cwd: Optional[str],
    allowed_norm_keys: List[str],
    allowed_resolved_keys: List[str],
    allow_os_temp: bool,
    depth: int = 0,
) -> Optional[Tuple[str, str, str]]:
    """审查 run_workflow 动态工作流脚本中的直接 Python 越界写入及 call_tool / agent 越界调用"""
    try:
        tree = ast.parse(py_code)
    except (SyntaxError, ValueError, RecursionError):
        return "动态工作流脚本语法错误，无法静态核验", "<INVALID_WORKFLOW>", _compact_snippet(py_code)
    direct_hit = _scan_inline_python_code(
        py_code, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, depth, tree=tree,
    )
    if direct_hit:
        return direct_hit

    bindings = {_AST_CWD: frozenset((base_cwd or "",))}

    def _bad(target) -> bool:
        return _ast_bad_target(target, bindings, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp)

    for node, bindings in _iter_ast_calls_in_order(tree, bindings):
        fn = node.func
        fn_name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
        snippet = _compact_snippet(ast.get_source_segment(py_code, node) or "workflow call")
        chdir_hit = _track_ast_chdir(node, bindings, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, snippet)
        if chdir_hit:
            return chdir_hit

        if fn_name == "call_tool":
            tool_arguments = _ast_workflow_tool_arguments(node, bindings)
            if tool_arguments is None:
                if _AST_DEFINITION in bindings:
                    continue
                return "动态工作流 call_tool 解包参数无法静态核验", "<DYNAMIC_UNRESOLVED>", snippet
            for raw_name, param_values in ((name, params) for names, params in tool_arguments for name in names):
                if not isinstance(raw_name, str):
                    if _AST_DEFINITION in bindings:
                        continue
                    return "动态工作流 call_tool 工具名无法静态确定", "<DYNAMIC_UNRESOLVED>", snippet
                tname = handoff_gate.AGY_TOOL_ALIASES.get(raw_name, raw_name)
                if tname not in (*FILE_MUTATION_TOOLS, "run_command", "run_workflow"):
                    continue
                for params in param_values:
                    if not isinstance(params, _AstDict) or params.uncertain:
                        if _AST_DEFINITION in bindings:
                            continue
                        return f"动态工作流 call_tool({raw_name}) 参数无法静态核验", "<DYNAMIC_UNRESOLVED>", snippet
                    fields = params.fields
                    if tname in FILE_MUTATION_TOOLS:
                        targets = _ast_dict_field(fields, FILE_PATH_KEYS)
                        if _bad(targets):
                            return f"动态工作流 call_tool({raw_name}) 修改项目外或动态路径", _format_ast_candidates(targets), snippet
                        continue
                    cwds = _ast_dict_field(fields, ("Cwd", "cwd"), bindings.get(_AST_CWD, base_cwd or ""))
                    if _bad(cwds):
                        return f"动态工作流 call_tool({raw_name}) 指定项目外或动态 Cwd", _format_ast_candidates(cwds), snippet
                    resolved_cwds = _ast_product(
                        [_ast_values(bindings.get(_AST_CWD, base_cwd or "")), cwds],
                        lambda parts: _ast_resolve_cwd(parts[1], parts[0]),
                    )
                    for cwd in resolved_cwds:
                        if not isinstance(cwd, str):
                            if _AST_DEFINITION in bindings:
                                continue
                            return "动态工作流工作目录无法静态确定", "<DYNAMIC_UNRESOLVED>", snippet
                        if tname == "run_command":
                            commands = _ast_dict_field(fields, ("CommandLine", "command_line", "command"))
                            for command in commands:
                                if not isinstance(command, str):
                                    return "动态工作流 run_command 命令无法静态确定", "<DYNAMIC_UNRESOLVED>", snippet
                                hit = _detect_command_external_mutation(command, cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp)
                                if hit:
                                    return f"动态工作流 call_tool(run_command): {hit[0]}", hit[1], hit[2]
                        else:
                            if depth >= 3:
                                return "动态工作流嵌套超出静态审查上限", "<DYNAMIC_UNRESOLVED>", snippet
                            for keys, is_path in ((("Script", "script"), False), (("ScriptPath", "scriptPath", "script_path"), True)):
                                scripts = _ast_dict_field(fields, keys, "")
                                for script in scripts:
                                    if not isinstance(script, str):
                                        return "嵌套动态工作流脚本无法静态确定", "<DYNAMIC_UNRESOLVED>", snippet
                                    hit = _detect_workflow_external_mutation(
                                        {"Cwd": cwd, "ScriptPath" if is_path else "Script": script}, cwd, cwd, [],
                                        allowed_norm_keys, allowed_resolved_keys, allow_os_temp, depth + 1,
                                    ) if script else None
                                    if hit:
                                        return hit
                            if not any(key in fields for key in ("Script", "script", "ScriptPath", "scriptPath", "script_path")):
                                return "嵌套动态工作流缺少可核验脚本", "<DYNAMIC_UNRESOLVED>", snippet

        elif fn_name == "agent":
            for kw in node.keywords:
                if kw.arg in ("cwd", "Cwd"):
                    ag_cwd = _eval_ast_path_candidates(kw.value, bindings)
                    if _bad(ag_cwd):
                        return "动态工作流 agent() 指定项目外工作目录", _format_ast_candidates(ag_cwd), snippet
                elif kw.arg is None:
                    for params in _eval_ast_path_candidates(kw.value, bindings):
                        if not isinstance(params, _AstDict) or params.uncertain:
                            if _AST_DEFINITION in bindings:
                                continue
                            return "动态工作流 agent() 解包参数无法静态核验", "<DYNAMIC_UNRESOLVED>", snippet
                        ag_cwd = _ast_dict_field(params.fields, ("cwd", "Cwd"), frozenset())
                        if _bad(ag_cwd):
                            return "动态工作流 agent() 指定项目外工作目录", _format_ast_candidates(ag_cwd), snippet
    return None


def _is_trusted_readonly_skill_root(target_path: Path) -> bool:
    gemini_home = Path.home() / ".gemini"
    skill_roots = (
        gemini_home / "antigravity" / "builtin" / "skills",
        gemini_home / "antigravity" / "skills",
        gemini_home / "antigravity" / "plugins",
        gemini_home / "config" / "skills",
        gemini_home / "config" / "plugins",
    )
    return any(_is_inside_dir(target_path, root) for root in skill_roots if root.exists())


def _detect_workflow_external_mutation(
    args: Dict[str, Any],
    payload_cwd: Optional[str],
    base_cwd: Optional[str],
    workspace_paths: List[str],
    allowed_norm_keys: List[str],
    allowed_resolved_keys: List[str],
    allow_os_temp: bool,
    depth: int = 0,
) -> Optional[Tuple[str, str, str]]:
    """在 run_workflow 启动前完成外层一次性 R4 路径与内联脚本预审"""
    raw_wf_cwd = str(args.get("Cwd") or args.get("cwd") or "").strip()
    if raw_wf_cwd and not _is_write_target_allowed(
        raw_wf_cwd, payload_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp
    ):
        return "在项目目录外指定动态工作流运行目录 (run_workflow Cwd)", raw_wf_cwd, f"Cwd={raw_wf_cwd}"

    script_src = str(args.get("Script") or args.get("script") or "")
    if script_src.strip():
        hit = _scan_workflow_python_code(script_src, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, depth)
        if hit:
            return hit

    raw_script_path = str(args.get("ScriptPath") or args.get("scriptPath") or args.get("script_path") or "").strip()
    if raw_script_path:
        try:
            resolved_sp = resolve_target_path(raw_script_path, base_cwd, workspace_paths)
            is_file = resolved_sp.is_file()
        except (OSError, ValueError, RuntimeError):
            return "无法定位动态工作流脚本 (run_workflow ScriptPath)", raw_script_path, f"ScriptPath={raw_script_path}"
        in_allowed = _is_write_target_allowed(
            raw_script_path, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp
        )
        in_skill_root = _is_trusted_readonly_skill_root(resolved_sp)
        if not in_allowed and not in_skill_root:
            return "加载并执行项目目录外的动态工作流脚本 (run_workflow ScriptPath)", raw_script_path, f"ScriptPath={raw_script_path}"
        if is_file:
            try:
                if resolved_sp.stat().st_size <= MAX_WORKFLOW_SCRIPT_BYTES:
                    file_src = resolved_sp.read_text(encoding="utf-8-sig")
                    sp_hit = _scan_workflow_python_code(
                        file_src, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, depth
                    )
                    if sp_hit:
                        return sp_hit
                else:
                    return "动态工作流脚本体积超出静态审查上限 (run_workflow ScriptPath)", raw_script_path, f"ScriptPath={raw_script_path}"
            except (OSError, UnicodeError):
                return "无法核实动态工作流脚本安全性 (run_workflow ScriptPath)", raw_script_path, f"ScriptPath={raw_script_path}"
        else:
            return "无法定位动态工作流普通脚本文件 (run_workflow ScriptPath)", raw_script_path, f"ScriptPath={raw_script_path}"
    return None


# ⑥ Shell 子句、写入路径与审批响应

def _compact_snippet(text: str, max_len: int = 140) -> str:
    flat = re.sub(r"\s+", " ", text.strip())
    return flat if len(flat) <= max_len else flat[: max_len - 3] + "..."


_INVALID_ENCODED_SENTINEL = "\x00__INVALID_ENCODED_COMMAND__"
_INLINE_PY_MARKER_RE = re.compile(r"__INLINE_PY_(\d+)__")


_SHELL_BIN_PREFIX_RE = r"""(?:"[^"\r\n]*[\\/]|'[^'\r\n]*[\\/]|[^\s"';|&()]+[\\/]|["'])?"""


def _read_unquoted_shell_word(text: str, start_idx: int) -> Optional[Tuple[str, int]]:
    """读取首个非空白 Shell 词元（兼容拼接引号与反斜杠转义），返回 (unquoted_word, end_idx)。"""
    n = len(text)
    idx = start_idx
    while idx < n and text[idx] in " \t":
        idx += 1
    if idx >= n or text[idx] in "\r\n;|&<>()":
        return None
    word_start = idx
    while idx < n and text[idx] not in " \t\r\n;|&<>()":
        if text[idx : idx + 2] == "$'":
            ansi = _decode_ansi_c_quotes(text, idx)
            if ansi is None:
                return None
            idx = ansi[1]
            continue
        if text[idx] in ("'", '"'):
            q = _read_quoted_arg(text, idx)
            if q is None:
                return None
            idx = q[1]
            continue
        if text[idx] == "\\" and idx + 1 < n:
            idx += 2
            continue
        idx += 1
    if idx <= word_start:
        return None
    return _unquote_shell_word(text[word_start:idx]), idx


def _unwrap_shell_script_with_tail(cmd: str) -> Optional[Tuple[str, str]]:
    """提取壳包装的内层脚本及外层同子句尾随部分（重定向或位置参数），返回 (inner_script, outer_tail)。"""
    lead_prefix = (
        r"^\s*(?:&\s+)?(?:[A-Za-z_]\w*=\S+\s+)*"
        r"(?:(?:eval|exec)\b(?:\s+(?:-[cl]|-a\s+\S+|--))*\s+)*"
        rf"""(?:{_SHELL_BIN_PREFIX_RE}wsl(?:\.exe)?["']?\b(?:\s+(?:-(?:d|u)|--(?:distribution|user|cd))\s+(?:"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|\S+)|\s+(?:--|-e|--exec|-[A-Za-z]+))*\s+)?"""
    )
    m_enc = re.match(
        lead_prefix
        + rf"""{_SHELL_BIN_PREFIX_RE}(?:pwsh(?:\.exe)?|powershell(?:\.exe)?)\b["']?"""
        + r"(?:\s+(?:-(?:ExecutionPolicy|ep|WindowStyle|w|InputFormat|OutputFormat|ConfigurationName|WorkingDirectory|Version|v)\s+\S+|-(?!(?:EncodedCommand|ec)\b)[A-Za-z]+))*"
        + r"""\s+["']?-(?:EncodedCommand|ec)["']?(?:\s+|$)""",
        cmd,
        re.IGNORECASE,
    )
    if m_enc:
        rest = cmd[m_enc.end() :].strip()
        if not rest:
            return _INVALID_ENCODED_SENTINEL, ""
        if rest[0] in ("'", '"') or rest.startswith("$'"):
            q_enc = _read_quoted_arg(rest, 0)
            if not q_enc:
                return _INVALID_ENCODED_SENTINEL, ""
            raw_b64_seg = rest[: q_enc[1]]
            b64_raw = q_enc[0].strip()
            enc_tail = rest[q_enc[1] :].strip()
        else:
            m_b64 = re.match(r"^([^\s;|&<>()]+)(.*)$", rest, re.DOTALL)
            if not m_b64:
                return _INVALID_ENCODED_SENTINEL, ""
            raw_b64_seg = m_b64.group(1)
            b64_raw = raw_b64_seg.strip()
            enc_tail = m_b64.group(2).strip()
        if enc_tail:
            if enc_tail.startswith((";", "|")) or (enc_tail.startswith("&") and not enc_tail.startswith("&>")):
                return None
            if len(_split_shell_clauses("true " + enc_tail)) > 1:
                return None
        if (
            not b64_raw
            or "$" in b64_raw
            or (not raw_b64_seg.startswith("'") and "$" in raw_b64_seg)
            or _has_unexpanded_dynamic_var(b64_raw, is_cmd_or_win=True)
        ):
            return _INVALID_ENCODED_SENTINEL, ""
        try:
            raw_bytes = base64.b64decode(b64_raw, validate=True)
            if not raw_bytes or len(raw_bytes) % 2 != 0:
                return _INVALID_ENCODED_SENTINEL, ""
            decoded = raw_bytes.decode("utf-16-le")
            if not decoded.strip() or "\x00" in decoded:
                return _INVALID_ENCODED_SENTINEL, ""
            return decoded, enc_tail
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return _INVALID_ENCODED_SENTINEL, ""

    m = re.match(
        lead_prefix
        + rf"""{_SHELL_BIN_PREFIX_RE}(?P<sh>bash|sh|zsh|pwsh(?:\.exe)?|powershell(?:\.exe)?|cmd(?:\.exe)?)\b["']?"""
        + r"(?:\s+(?:-(?:ExecutionPolicy|ep|WindowStyle|w|InputFormat|OutputFormat|ConfigurationName|WorkingDirectory|Version|v)\s+\S+|-(?!(?:[A-Za-z]*c|Command)\b)[A-Za-z]+|/(?![ck]\b)[A-Za-z](?::[A-Za-z]+)?))*"
        + r"""\s+(?P<sw>["']?-(?:[A-Za-z]*c|Command)["']?|["']?/[ck]["']?)\s+""",
        cmd,
        re.IGNORECASE,
    )
    if not m:
        return None
    sh_low = m.group("sh").lower().removesuffix(".exe")
    sw_low = m.group("sw").strip("\"'").lower()
    if sh_low in ("bash", "sh", "zsh") and not re.fullmatch(r"-[a-z]*c", sw_low):
        return None
    if sh_low in ("pwsh", "powershell") and sw_low not in ("-c", "-command"):
        return None
    if sh_low == "cmd" and sw_low not in ("/c", "/k"):
        return None

    arg_idx = m.end()
    while arg_idx < len(cmd) and cmd[arg_idx] in " \t":
        arg_idx += 1
    if arg_idx >= len(cmd):
        return None

    q_res = _read_quoted_arg(cmd, arg_idx)
    if q_res is None:
        if sh_low in ("pwsh", "powershell", "cmd"):
            rest = cmd[arg_idx:].strip()
            return (rest, "") if rest else None
        word_res = _read_unquoted_shell_word(cmd, arg_idx)
        if word_res is None:
            return None
        first_word, end_w = word_res
        tail = cmd[end_w:].strip()
        if tail:
            if tail.startswith((";", "|")) or (tail.startswith("&") and not tail.startswith("&>")):
                return None
            if len(_split_shell_clauses("true " + tail)) > 1:
                return None
        return (first_word, tail) if first_word else None

    inner_parts = [q_res[0]]
    end_q = q_res[1]
    while end_q < len(cmd) and cmd[end_q] not in " \t\r\n;|&<>()":
        nxt_q = _read_quoted_arg(cmd, end_q)
        if nxt_q is not None:
            inner_parts.append(nxt_q[0])
            end_q = nxt_q[1]
            continue
        if cmd[end_q] == "\\" and end_q + 1 < len(cmd):
            inner_parts.append(cmd[end_q + 1])
            end_q += 2
            continue
        inner_parts.append(cmd[end_q])
        end_q += 1
    tail = cmd[end_q:].strip()
    if tail:
        if tail.startswith((";", "|")) or (tail.startswith("&") and not tail.startswith("&>")):
            return None
        if len(_split_shell_clauses("true " + tail)) > 1:
            return None
    return "".join(inner_parts), tail


def _unwrap_shell_script_layer(cmd: str) -> Optional[str]:
    """若整条命令是 wsl/bash/powershell -c '<inner>' 或 -EncodedCommand 包装，提取内层脚本递归审查"""
    unwrapped = _unwrap_shell_script_with_tail(cmd)
    if unwrapped is None:
        return None
    return unwrapped[0]


def _extract_clause_path_targets(tokens: List[str], copy_dest_only: bool = False, head_low: str = "") -> List[str]:
    """从单条写/删/复制/移动 Shell 子句参数中提取实际受影响的目标路径列表"""
    if copy_dest_only:
        for idx, tok in enumerate(tokens[1:], start=1):
            low_tok = tok.lower()
            if low_tok in ("-destination", "-dest") and idx + 1 < len(tokens):
                return [tokens[idx + 1]]
            if low_tok.startswith("-destination:"):
                return [tok.split(":", 1)[1]]
            if head_low in ("cp", "ln"):
                if (tok == "-t" or low_tok == "--target-directory") and idx + 1 < len(tokens):
                    return [tokens[idx + 1]]
                if low_tok.startswith("--target-directory="):
                    return [tok.split("=", 1)[1]]

    positional: List[str] = []
    explicit_paths: List[str] = []
    has_explicit_script = False
    after_ddash = False
    idx = 1
    n = len(tokens)
    while idx < n:
        tok = tokens[idx]
        low = tok.lower()
        if not after_ddash and tok == "--":
            after_ddash = True
            idx += 1
            continue
        if not after_ddash:
            if head_low in ("sed", "perl"):
                if low in ("-e", "--expression", "-f", "--file"):
                    has_explicit_script = True
                    idx += 2
                    continue
                if tok.startswith(("-e", "-f")) and not tok.startswith("--") and len(tok) > 2:
                    has_explicit_script = True
                    idx += 1
                    continue
            if head_low in ("cp", "mv", "ln"):
                if (tok == "-t" or low == "--target-directory") and idx + 1 < n:
                    explicit_paths.append(tokens[idx + 1])
                    idx += 2
                    continue
                if low.startswith("--target-directory="):
                    explicit_paths.append(tok.split("=", 1)[1])
                    idx += 1
                    continue
                if head_low == "ln" and (tok == "-S" or low == "--suffix") and idx + 1 < n:
                    idx += 2
                    continue
            if low in _PS_NON_PATH_VALUE_FLAGS:
                idx += 2
                continue
            if low in ("-path", "-literalpath", "-filepath", "-destination", "-newname") and idx + 1 < n:
                explicit_paths.append(tokens[idx + 1])
                idx += 2
                continue
            if head_low == "robocopy" and re.match(r"^/(?:MIR|E|S|PURGE|MOVE|MOV|R|W|COPY|DCOPY|MT|LOG|TEE|NFL|NDL)(?::|$)", tok, re.I):
                idx += 1
                continue
            if head_low == "mklink" and re.fullmatch(r"/[DdHhJj?]", tok):
                idx += 1
                continue
            if tok.startswith("-"):
                idx += 1
                continue
            if (
                os.name == "nt"
                or head_low in ("rd", "del", "erase", "md", "move", "ren", "rename", "copy", "xcopy", "mklink")
            ) and re.fullmatch(r"/[A-Za-z0-9?]", tok):
                idx += 1
                continue
        positional.append(tok)
        idx += 1

    if head_low in ("sed", "perl") and not has_explicit_script and positional:
        positional = positional[1:]

    if head_low == "mklink":
        return [positional[0]] if positional else []
    if head_low == "ln":
        if explicit_paths:
            return explicit_paths
        if len(positional) >= 2:
            return [positional[-1]]
        if len(positional) == 1:
            return ["."]
        return []

    all_paths = explicit_paths + positional
    if not copy_dest_only:
        return all_paths
    if not all_paths:
        return []
    if head_low == "robocopy" and len(positional) >= 2:
        return [positional[1]]
    return [all_paths[-1]]


def _looks_like_explicit_or_escaped_path(tok: str) -> bool:
    s = tok.strip().strip("\"'")
    if not s or s == "__INLINE_PY__" or s.startswith("__INLINE_PY_"):
        return False
    if re.match(r"^[A-Za-z]:", s) or s.startswith(("\\\\", "//", "~", "..", "$HOME", "${HOME}", "$env:", "%")):
        return True
    if s.startswith("/") and not re.fullmatch(r"/[A-Za-z0-9?]", s):
        return True
    return False


_pos_inside_quotes = _shell_lex.pos_inside_quotes


def _extract_git_worktree_add_target(cmd_tokens: List[str], effective_cwd: Optional[str]) -> Optional[Tuple[str, Optional[str], bool]]:
    """解析 git [-C dir] [--work-tree dir] worktree add <path>，返回 (wt_path, resolved_git_cwd, cwd_is_dynamic)。"""
    git_cwd = effective_cwd
    cwd_dynamic = effective_cwd is None
    j = 1
    n = len(cmd_tokens)

    def _apply_git_dir(raw_dir: str) -> None:
        nonlocal git_cwd, cwd_dynamic
        c_dir = _expand_host_vars(_unquote_shell_word(raw_dir), base_cwd=git_cwd)
        if _has_unexpanded_dynamic_var(c_dir) or (git_cwd is None and not _is_anchored_path_str(c_dir)):
            cwd_dynamic = True
            git_cwd = None
        else:
            git_cwd = _ast_resolve_cwd(c_dir, git_cwd)
            cwd_dynamic = False

    while j < n:
        gtok = cmd_tokens[j]
        if gtok == "-C" and j + 1 < n:
            _apply_git_dir(cmd_tokens[j + 1])
            j += 2
            continue
        if gtok.startswith("-C") and len(gtok) > 2:
            _apply_git_dir(gtok[2:])
            j += 1
            continue
        if gtok == "--work-tree" and j + 1 < n:
            _apply_git_dir(cmd_tokens[j + 1])
            j += 2
            continue
        if gtok.startswith("--work-tree="):
            _apply_git_dir(gtok.split("=", 1)[1])
            j += 1
            continue
        if gtok in ("-c", "--git-dir", "--namespace", "--exec-path", "--super-prefix", "--config-env") and j + 1 < n:
            j += 2
            continue
        if gtok == "--":
            j += 1
            break
        if gtok.startswith("-"):
            j += 1
            continue
        break
    if j + 1 >= n or cmd_tokens[j].lower() != "worktree" or cmd_tokens[j + 1].lower() != "add":
        return None
    wt_args = cmd_tokens[j + 2 :]
    k = 0
    wt_path: Optional[str] = None
    while k < len(wt_args):
        tok = wt_args[k]
        if tok in ("-b", "-B", "--reason"):
            k += 2
            continue
        if tok == "--":
            if k + 1 < len(wt_args):
                wt_path = wt_args[k + 1]
            break
        if tok.startswith("-"):
            k += 1
            continue
        wt_path = tok
        break
    if not wt_path:
        return None
    return wt_path, git_cwd, cwd_dynamic


def _detect_command_external_mutation(
    command_line: str,
    base_cwd: Optional[str],
    allowed_norm_keys: List[str],
    allowed_resolved_keys: List[str],
    allow_os_temp: bool = True,
    depth: int = 0,
    wsl_distribution: Optional[str] = None,
) -> Optional[Tuple[str, str, str]]:
    """
    精准检测命令行是否改动项目目录外的业务路径或全局配置。
    返回 None（允许），或 (具体动作说明, 项目外目标路径, 触发语句片段)。
    """
    if not command_line or not command_line.strip():
        return None
    if depth > MAX_WRAPPER_DEPTH:
        return "深层嵌套命令超出静态目录边界审查上限", "<NESTED_DEPTH_EXCEEDED>", _compact_snippet(command_line)

    wsl_prefix = _wsl_command_prefix(command_line) if os.name == "nt" else None
    if wsl_prefix and not _outer_shell_operators(wsl_prefix[1]):
        distribution, inner = wsl_prefix
        return _detect_command_external_mutation(
            inner, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, depth + 1, distribution,
        )

    sanitized = (_COMMIT_MSG_RE.sub(r'\1"__COMMIT_MSG__"', command_line)
                 if "commit" in command_line.casefold() else command_line)

    # 先拆壳并隔离内联 Python，再扫描 Shell；若外层存在尾随重定向或位置参数，同时扫描内层与外层剩余部分。
    unwrapped_pair = _unwrap_shell_script_with_tail(sanitized)
    if unwrapped_pair is not None:
        unwrapped, outer_tail = unwrapped_pair
        if unwrapped == _INVALID_ENCODED_SENTINEL:
            return "PowerShell 编码命令无法静态解码审查 (-EncodedCommand)", "<ENCODED_COMMAND>", _compact_snippet(command_line)
        inner_hit = _detect_command_external_mutation(
            unwrapped, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp,
            depth + 1, wsl_distribution,
        )
        if inner_hit:
            return inner_hit
        if outer_tail:
            tail_hit = _detect_command_external_mutation(
                "true " + outer_tail, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp,
                depth + 1, wsl_distribution,
            )
            if tail_hit:
                return tail_hit
        return None
    py_blocks, shell_masked = _extract_and_mask_inline_python(sanitized)

    for inner in _extract_cmd_substitutions(shell_masked):
        hit = _detect_command_external_mutation(
            inner, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp,
            depth + 1, wsl_distribution,
        )
        if hit:
            return hit

    # 1. 专项拦截: 单子句顶层 git worktree add 指向项目目录外 (多子句 cd 与 git -C/--work-tree 同时由第 4 步精确复核)
    if len(_split_shell_clauses(shell_masked)) <= 1:
        for m_wt in _GIT_WORKTREE_ADD_RE.finditer(shell_masked):
            if _pos_inside_quotes(shell_masked, m_wt.start()):
                continue
            parsed_wt = _extract_git_worktree_add_target(_safe_split_tokens(m_wt.group(0)), base_cwd)
            if parsed_wt is not None:
                wt_path, wt_cwd, wt_cwd_dyn = parsed_wt
                exp_wt = _expand_host_vars(_unquote_shell_word(wt_path), base_cwd=wt_cwd)
                if (
                    (wt_cwd_dyn and not _is_anchored_path_str(exp_wt))
                    or _has_unexpanded_dynamic_var(exp_wt, is_cmd_or_win=(wsl_distribution is not None))
                    or not _is_write_target_allowed(
                        exp_wt, wt_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, wsl_distribution
                    )
                ):
                    return "创建项目外 Git 工作树 (git worktree add)", wt_path, _compact_snippet(m_wt.group(0))

    # 2. 专项拦截: 执行生产级全局安装脚本 (默认改写四端全局配置或 ~/.cc-switch)
    for m_inst in _INSTALLER_INVOKE_RE.finditer(shell_masked):
        if _pos_inside_quotes(shell_masked, m_inst.start()):
            continue
        prefix_seg = shell_masked[m_inst.start():m_inst.start(1)]
        if re.search(r"(?:^|\s)-m\s+py_compile\b", prefix_seg, re.IGNORECASE):
            continue
        tail_args = m_inst.group(1) or ""
        arg_tokens = _safe_split_tokens(tail_args)
        value_flags = {
            "--app", "--target-dir", "--codex-home", "--codex-skills-dir", "--claude-home", "--dsh-home",
            "--ccswitch-db", "--zotero-cmd", "--codex-handoff-mode", "--codex-handoff-compactions",
            "--claude-handoff-mode", "--claude-handoff-compactions",
        }
        options = {flag: [] for flag in value_flags}
        switches = set()
        idx = 0
        while idx < len(arg_tokens):
            token = arg_tokens[idx]
            flag, equal, value = token.partition("=")
            idx += 1
            if flag in value_flags:
                if not equal:
                    value = arg_tokens[idx] if idx < len(arg_tokens) else ""
                    idx += 1
                options[flag].append(value)
            else:
                switches.add(token.lower())
        if switches & {"--dry-run", "-dryrun", "--probe-apps", "--audit-upgrade"}:
            continue
        target_dirs = options["--target-dir"]
        skills_dirs = options["--codex-skills-dir"]
        codex_homes = options["--codex-home"]
        claude_homes = options["--claude-home"]
        dsh_homes = options["--dsh-home"]
        ccswitch_dbs = options["--ccswitch-db"]
        app = options["--app"][-1] if options["--app"] else "auto"
        complete = {
            "all": bool(target_dirs and (codex_homes or skills_dirs) and claude_homes),
            "both": bool(target_dirs and (codex_homes or skills_dirs) and not (claude_homes or dsh_homes)),
            "antigravity": bool(target_dirs and not (codex_homes or skills_dirs or claude_homes or dsh_homes)),
            "codex": bool((codex_homes or skills_dirs) and not (target_dirs or claude_homes or dsh_homes)),
            "claude": bool(claude_homes and not (target_dirs or codex_homes or skills_dirs or dsh_homes)),
            "dsh": bool(dsh_homes and not (target_dirs or codex_homes or skills_dirs or claude_homes)),
            "auto": bool(target_dirs or codex_homes or skills_dirs or claude_homes or dsh_homes),
        }.get(app, False)
        if complete and "--no-zotero" in switches:
            sandbox_targets = target_dirs + skills_dirs + codex_homes + claude_homes + dsh_homes + ccswitch_dbs
            if all(value and _is_write_target_allowed(
                value, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, wsl_distribution
            ) for value in sandbox_targets):
                continue
        return (
            "执行全局配置同步安装 (将更新本机 ~/.gemini/config、~/.codex、~/.claude 或 ~/.dsh)",
            "~/.gemini/config/plugins/user-guard, ~/.codex, ~/.claude, ~/.dsh",
            _compact_snippet(m_inst.group(0)),
        )

    # 3. 检查 PowerShell .NET [IO.File] / [IO.Directory] 写删方法
    for m_net in _DOTNET_IO_MUTATE_RE.finditer(shell_masked):
        cls_name, method_name, raw_args = m_net.group(1), m_net.group(2), m_net.group(3)
        parts = [p.strip().strip("\"'") for p in raw_args.split(",") if p.strip()]
        if not parts:
            continue
        check_list = [parts[1]] if method_name.lower().startswith("copy") and len(parts) >= 2 else parts[:1]
        if method_name.lower().startswith("move") and len(parts) >= 2:
            check_list = parts[:2]
        for cand in check_list:
            if _looks_like_explicit_or_escaped_path(cand) and not _is_write_target_allowed(
                cand, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, wsl_distribution
            ):
                return f".NET 文件写删操作 ([IO.{cls_name}]::{method_name})", cand, _compact_snippet(m_net.group(0))

    # 4. 按子句跟踪多子句目录切换，逐子句检查输出重定向、内联 Python、嵌套拆壳与写删移动复制命令
    clause_cwd = base_cwd
    scanned_py_indices: set[int] = set()
    checked_any_redirect = False
    clauses = _split_shell_clauses(shell_masked)
    for raw_clause in clauses:
        clause = raw_clause.strip()
        if not clause:
            continue
        # 剥离前导控制流关键字（if/elif/while/until 条件子句中的命令同样会真实执行）
        clause = re.sub(r"^(?:(?:else|then|do|if|elif|while|until)\b\s*)+", "", clause, flags=re.IGNORECASE)
        tokens = _safe_split_tokens(clause)
        effective_cwd = clause_cwd
        idx = 0
        n_tok = len(tokens)
        lookup_only = False
        eval_sub: Optional[str] = None
        while idx < n_tok:
            t = tokens[idx]
            tl = Path(t.replace("\\", "/")).name.lower()
            if tl == "&":
                idx += 1
                continue
            if tl == "command":
                idx += 1
                while idx < n_tok and tokens[idx].startswith("-"):
                    fl = tokens[idx]
                    idx += 1
                    if fl == "--":
                        break
                    if fl in ("-v", "-V") or (not fl.startswith("--") and any(c in fl[1:] for c in ("v", "V"))):
                        lookup_only = True
                        break
                if lookup_only:
                    break
                continue
            if tl == "env":
                idx += 1
                split_sub = None
                while idx < n_tok and tokens[idx].startswith("-"):
                    fl = tokens[idx]
                    idx += 1
                    if fl == "--":
                        break
                    if fl in ("-u", "--unset") and idx < n_tok:
                        idx += 1
                    elif fl in ("-C", "--chdir") and idx < n_tok:
                        c_arg = _expand_host_vars(_unquote_shell_word(tokens[idx]), base_cwd=effective_cwd)
                        effective_cwd = (
                            None
                            if _has_unexpanded_dynamic_var(c_arg) or (effective_cwd is None and not _is_anchored_path_str(c_arg))
                            else _ast_resolve_cwd(c_arg, effective_cwd)
                        )
                        idx += 1
                    elif fl.startswith("--chdir="):
                        c_arg = _expand_host_vars(_unquote_shell_word(fl.split("=", 1)[1]), base_cwd=effective_cwd)
                        effective_cwd = (
                            None
                            if _has_unexpanded_dynamic_var(c_arg) or (effective_cwd is None and not _is_anchored_path_str(c_arg))
                            else _ast_resolve_cwd(c_arg, effective_cwd)
                        )
                    elif fl in ("-S", "--split-string") and idx < n_tok:
                        split_sub = " ".join(tokens[idx:])
                        break
                    elif fl.startswith("--split-string="):
                        split_sub = _unquote_split_string_payload(
                            fl.split("=", 1)[1] + (" " + " ".join(tokens[idx:]) if idx < n_tok else "")
                        )
                        break
                if split_sub is not None:
                    sub_hit = _detect_command_external_mutation(
                        split_sub, effective_cwd, allowed_norm_keys, allowed_resolved_keys,
                        allow_os_temp, depth + 1, wsl_distribution,
                    )
                    if sub_hit:
                        return sub_hit
                    idx = n_tok
                    break
                continue
            if tl == "nohup":
                idx += 1
                continue
            if tl == "nice":
                idx += 1
                while idx < n_tok and tokens[idx].startswith("-"):
                    fl = tokens[idx]
                    idx += 1
                    if fl == "--":
                        break
                    if fl in ("-n", "--adjustment") and idx < n_tok:
                        idx += 1
                continue
            if tl == "uv" and idx + 1 < n_tok:
                j = idx + 1
                while j < n_tok and tokens[j].startswith("-"):
                    j += 2 if tokens[j] in ("--directory", "--project", "--config-file") and j + 1 < n_tok else 1
                if j < n_tok and tokens[j].lower() == "run":
                    j += 1
                    uv_vals = ("--python", "-p", "--with", "--with-editable", "--with-requirements",
                               "--directory", "--project", "--env-file", "--package")
                    while j < n_tok and tokens[j].startswith("-"):
                        fl = tokens[j]
                        j += 1
                        if fl == "--":
                            break
                        if fl in uv_vals and j < n_tok:
                            j += 1
                    idx = j
                    continue
            if tl == "sudo":
                idx += 1
                while idx < n_tok and tokens[idx].startswith("-"):
                    fl = tokens[idx]
                    idx += 1
                    if fl == "--":
                        break
                    if fl in (
                        "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
                        "-r", "--role", "-t", "--type", "-C", "--close-from",
                        "-T", "--command-timeout", "-D", "--chdir", "-R", "--chroot",
                    ) and idx < n_tok:
                        if fl in ("-D", "--chdir"):
                            c_arg = _expand_host_vars(_unquote_shell_word(tokens[idx]), base_cwd=effective_cwd)
                            effective_cwd = (
                                None
                                if _has_unexpanded_dynamic_var(c_arg) or (effective_cwd is None and not _is_anchored_path_str(c_arg))
                                else _ast_resolve_cwd(c_arg, effective_cwd)
                            )
                        idx += 1
                continue
            if tl == "timeout":
                idx += 1
                while idx < n_tok and tokens[idx].startswith("-"):
                    fl = tokens[idx].lower()
                    idx += 1
                    if fl == "--":
                        break
                    if fl in ("-k", "--kill-after", "-s", "--signal") and idx < n_tok:
                        idx += 1
                if idx < n_tok:
                    idx += 1
                continue
            if tl in ("wsl", "wsl.exe"):
                idx += 1
                while idx < n_tok and tokens[idx].startswith("-"):
                    fl = tokens[idx].lower()
                    idx += 1
                    if fl in ("--", "-e", "--exec"):
                        break
                    if fl in ("-d", "--distribution", "-u", "--user", "--cd") and idx < n_tok:
                        if fl == "--cd":
                            c_arg = _expand_host_vars(_unquote_shell_word(tokens[idx]), base_cwd=effective_cwd)
                            effective_cwd = (
                                None
                                if _has_unexpanded_dynamic_var(c_arg) or (effective_cwd is None and not _is_anchored_path_str(c_arg))
                                else _ast_resolve_cwd(c_arg, effective_cwd)
                            )
                        idx += 1
                continue
            if tl == "exec":
                idx += 1
                while idx < n_tok and tokens[idx].startswith("-"):
                    fl = tokens[idx]
                    idx += 1
                    if fl == "--":
                        break
                    if fl == "-a" and idx < n_tok:
                        idx += 1
                continue
            if tl == "eval":
                idx += 1
                if idx < n_tok and tokens[idx] == "--":
                    idx += 1
                eval_sub = " ".join(tokens[idx:]) if idx < n_tok else ""
                break
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
                idx += 1
                continue
            break

        for m_py in _INLINE_PY_MARKER_RE.finditer(raw_clause):
            py_idx = int(m_py.group(1))
            if 0 <= py_idx < len(py_blocks):
                scanned_py_indices.add(py_idx)
                py_hit = _scan_inline_python_code(
                    py_blocks[py_idx], effective_cwd, allowed_norm_keys, allowed_resolved_keys,
                    allow_os_temp, depth, wsl_distribution,
                )
                if py_hit:
                    return py_hit

        if effective_cwd != base_cwd:
            for sub_expr in _extract_cmd_substitutions(raw_clause):
                sub_hit = _detect_command_external_mutation(
                    sub_expr, effective_cwd, allowed_norm_keys, allowed_resolved_keys,
                    allow_os_temp, depth + 1, wsl_distribution,
                )
                if sub_hit:
                    return sub_hit

        for m_red in _SHELL_REDIRECT_RE.finditer(raw_clause):
            if _pos_inside_quotes(raw_clause, m_red.start()):
                continue
            checked_any_redirect = True
            red_tok = _unquote_shell_word(m_red.group(1).strip())
            if not red_tok or red_tok.lower() in _NULL_SINKS:
                continue
            expanded_red = _expand_host_vars(red_tok, base_cwd=effective_cwd)
            if _has_unexpanded_dynamic_var(expanded_red, is_cmd_or_win=(wsl_distribution is not None)):
                return "Shell 输出重定向写入 [未解析动态变量路径]", red_tok, _compact_snippet(m_red.group(0))
            if not _is_write_target_allowed(
                expanded_red, effective_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, wsl_distribution
            ):
                return "Shell 输出重定向写入", red_tok, _compact_snippet(m_red.group(0))

        if eval_sub is not None:
            if eval_sub.strip():
                eval_hit = _detect_command_external_mutation(
                    eval_sub, effective_cwd, allowed_norm_keys, allowed_resolved_keys,
                    allow_os_temp, depth + 1, wsl_distribution,
                )
                if eval_hit:
                    return eval_hit
            continue

        sub_pair = _unwrap_shell_script_with_tail(clause)
        if sub_pair is None and idx > 0 and idx < n_tok:
            sub_pair = _unwrap_shell_script_with_tail(" ".join(shlex.quote(tok) for tok in tokens[idx:]))
        if sub_pair is not None:
            sub_unwrapped, sub_tail = sub_pair
            if sub_unwrapped == _INVALID_ENCODED_SENTINEL:
                return "PowerShell 编码命令无法静态解码审查 (-EncodedCommand)", "<ENCODED_COMMAND>", _compact_snippet(clause)
            sub_hit = _detect_command_external_mutation(
                sub_unwrapped, effective_cwd, allowed_norm_keys, allowed_resolved_keys,
                allow_os_temp, depth + 1, wsl_distribution,
            )
            if sub_hit:
                return sub_hit
            if sub_tail:
                tail_hit = _detect_command_external_mutation(
                    "true " + sub_tail, effective_cwd, allowed_norm_keys, allowed_resolved_keys,
                    allow_os_temp, depth + 1, wsl_distribution,
                )
                if tail_hit:
                    return tail_hit
            continue

        if lookup_only or idx >= n_tok:
            continue

        cmd_tokens = tokens[idx:]
        raw_head = cmd_tokens[0]
        head_low = Path(raw_head.replace("\\", "/")).name.lower()
        if head_low.endswith(".exe"):
            head_low = head_low[:-4]

        if head_low in ("cd", "chdir", "set-location", "pushd"):
            cd_raw_arg: Optional[str] = None
            cd_i = 1
            after_ddash = False
            while cd_i < len(cmd_tokens):
                ct = cmd_tokens[cd_i]
                ct_low = ct.lower()
                if not after_ddash:
                    if ct == "--":
                        after_ddash = True
                        cd_i += 1
                        continue
                    if ct_low in ("-path", "-literalpath") and cd_i + 1 < len(cmd_tokens):
                        cd_raw_arg = cmd_tokens[cd_i + 1]
                        break
                    if ct_low in ("/d", "-passthru") or re.fullmatch(r"-[LPpe@]+", ct):
                        cd_i += 1
                        continue
                cd_raw_arg = ct
                break
            if cd_raw_arg is None:
                if head_low in ("cd", "pushd", "set-location"):
                    clause_cwd = str(Path.home())
            else:
                cd_unquoted = _unquote_shell_word(cd_raw_arg)
                if cd_unquoted in ("-", "~-") or (
                    head_low == "pushd" and bool(re.fullmatch(r"[+-]\d+", cd_unquoted))
                ):
                    clause_cwd = None
                else:
                    cd_target = _expand_host_vars(cd_unquoted, base_cwd=effective_cwd)
                    if _has_unexpanded_dynamic_var(
                        cd_target, is_cmd_or_win=(wsl_distribution is not None)
                    ) or (effective_cwd is None and not _is_anchored_path_str(cd_target)):
                        clause_cwd = None
                    else:
                        clause_cwd = _ast_resolve_cwd(cd_target, effective_cwd)
            continue

        if head_low == "git":
            parsed_wt = _extract_git_worktree_add_target(cmd_tokens, effective_cwd)
            if parsed_wt is not None:
                wt_path, wt_cwd, wt_cwd_dyn = parsed_wt
                exp_wt = _expand_host_vars(_unquote_shell_word(wt_path), base_cwd=wt_cwd)
                if (
                    (wt_cwd_dyn and not _is_anchored_path_str(exp_wt))
                    or _has_unexpanded_dynamic_var(exp_wt, is_cmd_or_win=(wsl_distribution is not None))
                    or not _is_write_target_allowed(
                        exp_wt, wt_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, wsl_distribution
                    )
                ):
                    return "创建项目外 Git 工作树 (git worktree add)", wt_path, _compact_snippet(clause)
            continue

        action_label: Optional[str] = None
        targets: List[str] = []

        if head_low in _SHELL_DELETE_VERBS:
            action_label = f"删除文件或目录 ({raw_head})"
            targets = _extract_clause_path_targets(cmd_tokens, copy_dest_only=False, head_low=head_low)
        elif head_low in _SHELL_CREATE_WRITE_VERBS:
            action_label = f"创建或写入文件/目录 ({raw_head})"
            targets = _extract_clause_path_targets(cmd_tokens, copy_dest_only=False, head_low=head_low)
        elif head_low in _SHELL_MOVE_VERBS:
            action_label = f"移动或重命名路径 ({raw_head})"
            targets = _extract_clause_path_targets(cmd_tokens, copy_dest_only=False, head_low=head_low)
        elif head_low in _SHELL_COPY_VERBS:
            action_label = f"复制写入外部路径 ({raw_head})"
            targets = _extract_clause_path_targets(cmd_tokens, copy_dest_only=True, head_low=head_low)
        elif head_low in _SHELL_LINK_VERBS:
            action_label = f"创建链接写入外部路径 ({raw_head})"
            targets = _extract_clause_path_targets(cmd_tokens, copy_dest_only=True, head_low=head_low)
        elif head_low in ("sed", "perl"):
            has_inplace = any(
                t == "--in-place" or t.startswith("--in-place=") or t.startswith("-i")
                or (t.startswith("-") and not t.startswith("--") and "i" in t[1:])
                for t in cmd_tokens[1:]
            )
            if has_inplace:
                action_label = f"原地修改文件 ({raw_head} -i)"
                targets = _extract_clause_path_targets(cmd_tokens, copy_dest_only=False, head_low=head_low)

        if action_label and targets:
            for cand in targets:
                if not cand:
                    continue
                unq_cand = _unquote_shell_word(cand)
                expanded_cand = _expand_host_vars(unq_cand, base_cwd=effective_cwd)
                is_win_verb = head_low in ("rd", "del", "erase", "md", "move", "ren", "rename", "copy", "xcopy", "robocopy", "mklink")
                if _has_unexpanded_dynamic_var(
                    expanded_cand, is_cmd_or_win=(is_win_verb or wsl_distribution is not None)
                ):
                    return f"{action_label} [未解析动态变量路径]", cand, _compact_snippet(clause)
                command_distribution = wsl_distribution
                if wsl_distribution is not None and any(
                    Path(tok.replace("\\", "/")).name.casefold() in REMOTE_OR_SUBSYSTEM_WRAPPERS | {"chroot", "nsenter"}
                    for tok in tokens[:idx + 1]
                ):
                    command_distribution = None
                if not _is_write_target_allowed(
                    expanded_cand, effective_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, command_distribution
                ):
                    return action_label, cand, _compact_snippet(clause)

    for py_idx, block in enumerate(py_blocks):
        if py_idx not in scanned_py_indices:
            py_hit = _scan_inline_python_code(
                block, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, depth, wsl_distribution
            )
            if py_hit:
                return py_hit
    if not checked_any_redirect:
        for m_red in _SHELL_REDIRECT_RE.finditer(shell_masked):
            if _pos_inside_quotes(shell_masked, m_red.start()):
                continue
            red_tok = _unquote_shell_word(m_red.group(1).strip())
            if not red_tok or red_tok.lower() in _NULL_SINKS:
                continue
            expanded_red = _expand_host_vars(red_tok, base_cwd=base_cwd)
            if _has_unexpanded_dynamic_var(expanded_red, is_cmd_or_win=(wsl_distribution is not None)):
                return "Shell 输出重定向写入 [未解析动态变量路径]", red_tok, _compact_snippet(m_red.group(0))
            if not _is_write_target_allowed(
                expanded_red, base_cwd, allowed_norm_keys, allowed_resolved_keys, allow_os_temp, wsl_distribution
            ):
                return "Shell 输出重定向写入", red_tok, _compact_snippet(m_red.group(0))

    return None


def _heavy_min_streams() -> int:
    profile = user_words.load_fleet_profile(HOOKS_DIR)
    config = profile.get("dispatch_confirmation", {})
    if not config:
        config = profile.get("runtime_tunables", {}).get("dispatch_confirmation", {})
    try:
        return max(1, int(config.get("heavy_min_streams", 2)))
    except (TypeError, ValueError, OverflowError):
        return 2


def _native_tool_name(name: str) -> str:
    return name.rsplit(".", 1)[-1].rsplit("__", 1)[-1]


def _native_dispatch(name: str, args: Dict[str, Any]) -> bool:
    return name in ("invoke_subagent", "spawn_agent", "Agent", "Task", "run_workflow") or (
        name == "swarm" and str(args.get("action") or args.get("Action")).lower() == "add")


def _native_call_stream_weight(name: str, args: Any) -> int:
    if name == "swarm" and isinstance(args, dict):
        tasks = args.get("tasks") if "tasks" in args else args.get("Tasks")
        if isinstance(tasks, list) and tasks:
            return len(tasks)
        raw_n = args.get("n") if "n" in args else args.get("N")
        if isinstance(raw_n, int) and not isinstance(raw_n, bool) and raw_n > 0:
            return raw_n
    return 1


def _dispatch_transcript(payload: Dict[str, Any]) -> Optional[Path]:
    raw = payload.get("transcriptPath") or payload.get("transcript_path")
    return user_words.resolve_compact_transcript_path(Path(raw).expanduser()) if raw else None


def _native_turn_snapshot(payload: Dict[str, Any], *, all_turns=False) -> Tuple[str, List[Dict[str, Any]]]:
    """读取原生调用；派工按当前回合计数，完工核验可保留全部可见回合。"""
    path = _dispatch_transcript(payload)
    turn, calls = "initial", []
    if path is None:
        return turn, calls
    try:
        with path.open("rb") as source:
            for index, line in enumerate(source):
                try:
                    entry = json.loads(user_words.decode_jsonl_line(line))
                except (ValueError, RecursionError):
                    continue
                if not isinstance(entry, dict):
                    continue
                typ = entry.get("type")
                explicit = typ == "USER_INPUT" or entry.get("source") == "USER_EXPLICIT"
                if typ == "user":
                    text, kind = user_words.claude_user_content(entry)
                    explicit = bool(text and kind in ("user_input", "queued_command"))
                rec = entry.get("payload") if typ == "response_item" else None
                if isinstance(rec, dict) and rec.get("type") == "message" and rec.get("role") == "user":
                    _, word = user_words._extract_codex_record(entry, index)
                    if word:
                        word["source_path"] = str(path)
                    explicit = bool(word and user_words._codex_word_from_record(word))
                if explicit:
                    turn = "turn_" + hashlib.sha256(str(index).encode("ascii") + b":" + line).hexdigest()[:24]
                    if not all_turns:
                        calls = []
                candidates = list(entry.get("tool_calls") or [])
                msg = entry.get("message")
                if typ == "assistant" and isinstance(msg, dict) and isinstance(msg.get("content"), list):
                    candidates += [b for b in msg["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
                if isinstance(rec, dict) and rec.get("type") == "function_call":
                    candidates.append(rec)
                for call in candidates:
                    if not isinstance(call, dict):
                        continue
                    name = _native_tool_name(str(call.get("name") or ""))
                    args = call.get("args", call.get("arguments", call.get("input", {})))
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (ValueError, RecursionError):
                            args = {}
                    if isinstance(args, dict) and _native_dispatch(name, args):
                        calls.append({"name": name, "args": args, "index": index,
                                      "call_id": call.get("call_id") or call.get("id") or ""})
    except OSError:
        pass
    return turn, calls


def _dispatch_commands(command: str, depth: int = 0):
    if depth > MAX_WRAPPER_DEPTH:
        return
    for raw_clause in _split_shell_clauses(command):
        clause = raw_clause.strip()
        if not clause:
            continue
        clause = re.sub(
            r"^(?:(?:if|then|else|elif|while|until|do)\b(?:\s+|$)|!\s+)+",
            "",
            clause,
            flags=re.IGNORECASE,
        ).strip()
        if not clause or clause.lower() in ("fi", "done", "esac"):
            continue
        inner_pair = _unwrap_shell_script_with_tail(clause)
        if inner_pair is not None and inner_pair[0] and inner_pair[0] != _INVALID_ENCODED_SENTINEL:
            yield from _dispatch_commands(inner_pair[0], depth + 1)
            if inner_pair[1]:
                for tail_sub in _extract_cmd_substitutions(inner_pair[1]):
                    yield from _dispatch_commands(tail_sub, depth + 1)
            continue
        for sub_expr in _extract_cmd_substitutions(clause):
            yield from _dispatch_commands(sub_expr, depth + 1)
        tokens = _safe_split_tokens(clause)
        while tokens:
            head = Path(tokens[0].replace(chr(92), "/")).name.lower().removesuffix(".exe")
            if head in ("&", "call", "if", "then", "else", "elif", "while", "until", "do", "!") or re.match(
                r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]
            ):
                tokens.pop(0)
                continue
            if head == "command":
                i = 1
                is_query = False
                while i < len(tokens) and tokens[i].startswith("-"):
                    fl = tokens[i]
                    i += 1
                    if fl == "--":
                        break
                    if fl in ("-v", "-V") or (not fl.startswith("--") and any(c in fl[1:] for c in ("v", "V"))):
                        is_query = True
                        break
                if is_query:
                    tokens = []
                    break
                tokens = tokens[i:]
                continue
            if head == "env":
                i = 1
                split_sub = None
                while i < len(tokens):
                    fl = tokens[i]
                    if fl == "--":
                        i += 1
                        break
                    if fl in ("-u", "--unset", "-C", "--chdir") and i + 1 < len(tokens):
                        i += 2
                        continue
                    if fl.startswith(("--unset=", "--chdir=")):
                        i += 1
                        continue
                    if fl in ("-S", "--split-string") and i + 1 < len(tokens):
                        split_sub = " ".join(tokens[i + 1:])
                        break
                    if fl.startswith("--split-string="):
                        split_sub = _unquote_split_string_payload(
                            fl.split("=", 1)[1] + (" " + " ".join(tokens[i + 1:]) if i + 1 < len(tokens) else "")
                        )
                        break
                    if fl.startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", fl):
                        i += 1
                        continue
                    break
                if split_sub is not None:
                    yield from _dispatch_commands(split_sub, depth + 1)
                    tokens = []
                    break
                tokens = tokens[i:]
                continue
            if head == "uv" and len(tokens) >= 2:
                i = 1
                while i < len(tokens) and tokens[i].startswith("-"):
                    i += 2 if tokens[i] in ("--directory", "--project", "--config-file") and i + 1 < len(tokens) else 1
                if i < len(tokens) and tokens[i].lower() == "run":
                    i += 1
                    uv_vals = ("--python", "-p", "--with", "--with-editable", "--with-requirements",
                               "--directory", "--project", "--env-file", "--package")
                    while i < len(tokens) and tokens[i].startswith("-"):
                        fl = tokens[i]
                        i += 1
                        if fl == "--":
                            break
                        if fl in uv_vals and i < len(tokens):
                            i += 1
                    tokens = tokens[i:]
                    continue
            if head == "cmd":
                marker = next((i for i, token in enumerate(tokens[1:], 1) if token.lower() in ("/c", "/k")), None)
                if marker is None:
                    break
                tokens = tokens[marker + 1:]
            elif head == "start":
                # start 的首个引号参数是窗口标题；空标题会在分词时被移除。
                title = re.search(r'(?:^|\s)start(?:\.exe)?\s+"([^"\n]*)"', clause.strip(), re.I)
                if title and title[1] and tokens[1:]:
                    tokens.pop(1)
                i = 1
                while i < len(tokens) and tokens[i].startswith("/"):
                    flag = tokens[i].lower()
                    i += 2 if flag in ("/d", "/node", "/affinity", "/machine") else 1
                tokens = tokens[i:]
            elif head == "eval":
                i = 2 if len(tokens) >= 2 and tokens[1] == "--" else 1
                if i < len(tokens):
                    yield from _dispatch_commands(" ".join(tokens[i:]), depth + 1)
                tokens = []
                break
            elif head == "exec":
                i = 1
                while i < len(tokens) and tokens[i].startswith("-"):
                    fl = tokens[i]
                    i += 1
                    if fl == "--":
                        break
                    if fl == "-a" and i < len(tokens):
                        i += 1
                tokens = tokens[i:]
                continue
            elif head in ("bash", "sh", "zsh", "pwsh", "powershell"):
                unw_sub = _unwrap_shell_script_with_tail(" ".join(shlex.quote(t) for t in tokens))
                if unw_sub is not None:
                    if unw_sub[0] and unw_sub[0] != _INVALID_ENCODED_SENTINEL:
                        yield from _dispatch_commands(unw_sub[0], depth + 1)
                        if unw_sub[1]:
                            for tail_sub in _extract_cmd_substitutions(unw_sub[1]):
                                yield from _dispatch_commands(tail_sub, depth + 1)
                    tokens = []
                    break
                marker = next((i for i, token in enumerate(tokens[1:], 1) if token.lower() == "-command"
                               or re.fullmatch(r"-[a-z]*c", token.lower())), None)
                if marker is None:
                    break
                yield from _dispatch_commands(" ".join(tokens[marker + 1:]), depth + 1)
                tokens = []
            elif head in ("wsl", "sudo", "timeout", "nohup", "nice"):
                takes_value = {
                    "wsl": ("-d", "--distribution", "-u", "--user", "--cd"),
                    "sudo": ("-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "--close-from", "-T", "--command-timeout", "-r", "--role", "-t", "--type", "-D", "--chdir", "-R", "--chroot"),
                    "timeout": ("-k", "--kill-after", "-s", "--signal"),
                    "nice": ("-n", "--adjustment"), "nohup": (),
                }[head]
                i = 1
                while i < len(tokens) and tokens[i].startswith("-"):
                    flag = tokens[i]
                    i += 1
                    if flag == "--" or (head == "wsl" and flag in ("-e", "--exec")):
                        break
                    if flag in takes_value and i < len(tokens):
                        i += 1
                if head == "timeout" and i < len(tokens):
                    i += 1
                tokens = tokens[i:]
            else:
                break
            if len(tokens) == 1:
                yield from _dispatch_commands(tokens[0], depth + 1)
                tokens = []
            elif tokens:
                inner_pair = _unwrap_shell_script_with_tail(" ".join(tokens))
                if inner_pair is not None and inner_pair[0] and inner_pair[0] != _INVALID_ENCODED_SENTINEL:
                    yield from _dispatch_commands(inner_pair[0], depth + 1)
                    if inner_pair[1]:
                        for tail_sub in _extract_cmd_substitutions(inner_pair[1]):
                            yield from _dispatch_commands(tail_sub, depth + 1)
        if tokens:
            yield tokens


def _native_readonly(args):
    kind = str(args.get("TypeName") or args.get("typeName") or args.get("agent_type") or args.get("subagent_type") or "self")
    return kind.startswith("readonly-") or kind in ("research", "browser", "image-generator", "Explore", "Plan", "explorer")


def _dispatch_option(tokens, name):
    for i, token in enumerate(tokens):
        if token.startswith(name + "="):
            return token.split("=", 1)[1].strip("\"'")
        if token == name and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


_HARNESS_METRICS_CACHE: Dict[str, Tuple[int, Any]] = {}


def _resolve_cae_harness_script(anchor_dir: Optional[Path] = None) -> Path:
    base = Path(anchor_dir).resolve() if anchor_dir is not None else HOOKS_DIR
    primary = base.parent / "skills" / "cae-harness" / "scripts" / "cae_harness.py"
    candidates = [
        primary,
        base / "cae_harness.py",
        *(skill_dir / "scripts" / "cae_harness.py" for skill_dir in user_words.global_cae_harness_skill_dirs()),
    ]
    for cand in candidates:
        try:
            if not user_words._is_reparse_or_link(cand) and cand.is_file():
                return cand
        except OSError:
            continue
    return primary


def _load_harness_metrics_module(script: Path) -> Any:
    key = str(script.resolve())
    mtime_ns = script.stat().st_mtime_ns
    cached = _HARNESS_METRICS_CACHE.get(key)
    if cached and cached[0] == mtime_ns:
        return cached[1]
    spec = importlib.util.spec_from_file_location("_guard_harness_metrics", script)
    metrics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics)
    _HARNESS_METRICS_CACHE[key] = (mtime_ns, metrics)
    return metrics


def _check_dispatch_locked(payload, tool_name, args, sid, state_dir):
    if _is_headless_subtask() or os.environ.get("USER_GUARD_DISABLE_TICKET_GATE") == "1":
        return None
    tool_name = _native_tool_name(tool_name)
    command = _extract_native_shell_command(args)
    shell = tool_name in ("run_command", "Bash", "PowerShell", "exec_command", "bash", "shell", "shell_command")
    heavy_tickets = []
    if shell:
        for tokens in _dispatch_commands(command):
            names = [Path(t.replace(chr(92), "/")).name.lower().removesuffix(".exe").removesuffix(".cmd") for t in tokens]
            if "cae_harness.py" in names and (names[0] == "cae_harness.py" or re.fullmatch(r"(?:python(?:[0-9.]+)?|py)", names[0])):
                tail = tokens[names.index("cae_harness.py") + 1:]
                code_task = tail[:1] == ["task"] and ("--kind=code" in tail or any(
                    tail[i:i+2] == ["--kind", "code"] for i in range(len(tail))))
                profile = user_words.load_fleet_profile(HOOKS_DIR)
                config = profile.get("dispatch_confirmation") or profile.get("runtime_tunables", {}).get("dispatch_confirmation", {})
                try:
                    limit = float(config.get("heavy_max_light_timeout_sec", 600))
                    default_timeout = profile.get("runtime_tunables", {}).get("headless_dispatch", {}).get("default_timeout_sec", 600)
                    long_task = tail[:1] == ["task"] and float(_dispatch_option(tail, "--timeout") or default_timeout) > limit
                except (TypeError, ValueError, OverflowError):
                    long_task = False
                large_target = False
                base = Path(payload.get("cwd") or Path.cwd())
                command_cwd = args.get("Cwd") or args.get("cwd") or args.get("workdir")
                if command_cwd:
                    base = base / Path(command_cwd).expanduser()
                plan_dir_arg = _dispatch_option(tail, "--plan-dir")
                custom_plan_dir = base / Path(plan_dir_arg).expanduser() if plan_dir_arg else None
                extra_plan_dirs = (custom_plan_dir,) if custom_plan_dir is not None else ()
                target_arg = _dispatch_option(tail, "--target") or "."
                if tail[:1] == ["task"] and "--dry-run" not in tail:
                    target = (base / Path(target_arg).expanduser()).resolve()
                    if target.exists():
                        # 复用派工端的过滤与字节估算，目录链接、空文件等判据只维护一处；脚本缺失时拒绝因 FileNotFoundError 漏放。
                        script = _resolve_cae_harness_script(HOOKS_DIR)
                        metrics = None
                        try:
                            if script.is_file():
                                metrics = _load_harness_metrics_module(script)
                        except (OSError, ImportError, AttributeError):
                            metrics = None
                        if metrics is not None:
                            count, tokens_estimate = metrics._target_metrics(
                                target, _dispatch_option(tail, "--user-prompt") or _dispatch_option(tail, "--task") or ""
                            )
                            large_target = (
                                count > config.get("heavy_max_light_files", 2)
                                or tokens_estimate > config.get("heavy_max_light_tokens", 24000)
                            )
                        else:
                            large_target = True
                if tail and (tail[0] in ("batch", "audit", "gate") or code_task or long_task or large_target) and "--dry-run" not in tail:
                    heavy_tickets.append((_dispatch_option(tail, "--ticket"), extra_plan_dirs))
                continue
            head, tail = names[0], tokens[1:]
            low = [t.lower() for t in tail]
            if any(t in low for t in ("--help", "-h", "--version")) or low[:2] in (["auth", "status"], ["plugin", "validate"]) or low[:1] == ["models"]:
                continue
            bare = (head == "codex" and "exec" in low or head == "claude" and any(t in low for t in ("-p", "--print"))
                    or head == "dsh" and ("--profile=headless" in low or any(low[i:i+2] == ["--profile", "headless"] for i in range(len(low))))
                    or head == "agy" and "task" in low)
            if bare:
                return "【user-guard】禁止直接调用第三方子代理 CLI。请改用 cae_harness.py，保留声明窗口、非委派挡位、快照核验与额度检查。"
    native = _native_dispatch(tool_name, args)
    if not (native or heavy_tickets):
        return None
    if not user_words.valid_session_id(sid):
        return _ticket_required_reason(sid, state_dir)
    # 外部重任务只对无轨迹且未显式启用门禁的离线夹具豁免。
    offline = not (_dispatch_transcript(payload) or os.environ.get("USER_GUARD_ENFORCE_TICKET_GATE") == "1")
    if offline and not native:
        return None
    if native:
        tickets = user_words.find_session_approved_tickets(sid, state_dir)
        turn, calls = _native_turn_snapshot(payload)
        file = state_dir / (sid + "_dispatch_guard.json")
        state = user_words._read_state_json(file) or {}
        if not isinstance(state, dict):
            return _ticket_required_reason(sid, state_dir)
        if state.get("turn") != turn:
            state = {"turn": turn, "count": 0, "calls": [], "channel_counts": {}}
        if type(state.get("count")) is not int or state["count"] < 0 or not isinstance(state.get("calls"), list):
            return _ticket_required_reason(sid, state_dir)
        identity = str(payload.get("tool_use_id") or payload.get("call_id") or "")
        if identity:
            calls = [call for call in calls if call["call_id"] != identity]
        weight = _native_call_stream_weight(tool_name, args)
        prior_transcript_count = sum(_native_call_stream_weight(call["name"], call.get("args")) for call in calls)
        prior = max(state["count"], prior_transcript_count)
        duplicate = identity and identity in state.get("calls", [])
        count = prior if duplicate else prior + weight
        channel = {"run_workflow": "native_workflow", "swarm": "native_swarm"}.get(tool_name, "native_subagent")
        channel_counts = state.get("channel_counts", {})
        channel_transcript_prior = sum(
            _native_call_stream_weight(call["name"], call.get("args"))
            for call in calls
            if {"run_workflow": "native_workflow", "swarm": "native_swarm"}.get(call["name"], "native_subagent") == channel
        )
        channel_prior = max(channel_counts.get(channel, 0), channel_transcript_prior)
        channel_count = channel_prior if duplicate else channel_prior + weight
        capacity = max((sum(isinstance(row, dict) and row.get("channel") == channel
                            for row in ticket.get("streams", [])) for ticket in tickets), default=0)
        heavy = tool_name in ("run_workflow", "swarm") or not _native_readonly(args) or count >= _heavy_min_streams()
        if heavy and channel_count > capacity:
            return _ticket_required_reason(sid, state_dir)
        if not duplicate:
            state["count"] = count
            state.setdefault("channel_counts", {})[channel] = channel_count
            if identity:
                state.setdefault("calls", []).append(identity)
            user_words.atomic_write_json(file, state)
    for ticket_id, extra_plan_dirs in heavy_tickets:
        if not ticket_id or not user_words.find_session_approved_tickets(
                sid, state_dir, ticket_id=ticket_id, channel="cae_harness_headless", extra_plan_dirs=extra_plan_dirs):
            return _ticket_required_reason(sid, state_dir, extra_plan_dirs=extra_plan_dirs)
    return None


def _ticket_required_reason(sid, state_dir=None, extra_plan_dirs=()):
    harness_script = _resolve_cae_harness_script(HOOKS_DIR)
    pending_ticket = next(
        (t for _, t in user_words._session_tickets(str(sid or ""), state_dir, statuses=("PENDING",), extra_plan_dirs=extra_plan_dirs)),
        None,
    )
    if pending_ticket:
        tid = str(pending_ticket.get("ticket_id") or "<TICKET_ID>")
        detail_file = str(pending_ticket.get("card_detail_file") or "").strip()
        detail_hint = f"（完整明细见：{detail_file}）" if detail_file else ""
        return (
            f"【user-guard 重负载派工门禁】检测到当前会话已有未过期的待确认（PENDING）批次票据 `{tid}`，尚未获得 APPROVED 授权{detail_hint}。"
            f'请先向用户展示包含内部与外部子代理完整 14 项参数的中文确认单，待用户明确确认后再执行 `python "{harness_script}" plan --session-id {sid} --approve-ticket {tid}` 解锁启动（严禁未经用户确认自行审批或重复建单）；原生调用须在票据中列出原生通道。'
        )
    return (
        "【user-guard 重负载派工门禁】当前会话尚无有效且未过期的批次票据（未找到 APPROVED 或待确认 PENDING 票据，或票据通道容量不匹配）。"
        f'请先调用 `python "{harness_script}" plan --session-id {sid} ...` 生成中文确认单，包含内部与外部子代理完整 14 项参数明细；'
        "获用户确认并执行 `--approve-ticket` 后再启动；原生调用须在票据中列出原生通道。"
    )


def _is_headless_subtask() -> bool:
    return os.environ.get("CAE_HARNESS_DEPTH", "").strip() not in ("", "0")


def _build_boundary_violation_response(
    tool_name: str,
    action_desc: str,
    external_target: str,
    project_display: str,
    conversation_id: str,
    tool_summary: str = "",
    snippet: str = "",
) -> Dict[str, Any]:
    headless_subtask = _is_headless_subtask()
    decision = "deny" if headless_subtask else "force_ask"
    intent_line = f" | 任务说明：{tool_summary}" if tool_summary else ""
    snippet_line = f"\n• 触发语句：{snippet}" if snippet else ""
    if headless_subtask:
        reason = (
            f"【user-guard 项目目录硬边界阻断】跨端无头子任务严禁在目标项目目录（{project_display}）之外改动业务文件或全局配置！"
            f"已拦截越界操作：{action_desc} -> {external_target}{intent_line}。"
        )
    else:
        summary_bullet = f"\n• 当前任务意图：{tool_summary}" if tool_summary else ""
        reason = (
            f"【⚠️ 项目目录外文件改动确认】"
            f"{summary_bullet}"
            f"\n• 具体写删动作：{action_desc}"
            f"\n• 项目外目标路径：{external_target}（当前项目根目录：{project_display}）"
            f"{snippet_line}"
            f"\n（注：标准临时目录与对话记忆缓存已自动放行；按 R4 硬约束，改动项目外业务目录或全局配置须经您明确批准。）"
        )
    log_event(
        "PreToolUse:workspace_boundary",
        conversation_id,
        f"{decision.upper()} {action_desc} via {tool_name} -> {external_target} (workspace={project_display})",
    )
    return {"decision": decision, "reason": reason}


# ⑦ PreToolUse 调度与命令行主入口

def handle_pre_tool_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    """处理 PreToolUse 门禁请求"""
    _sync_status_tunables()
    conversation_id = str(payload.get("conversationId") or "unknown_session").strip()
    workspace_paths = payload.get("workspacePaths") if isinstance(payload.get("workspacePaths"), list) else []
    transcript_path_str = payload.get("transcriptPath")

    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    raw_tool_name = str(tool_call.get("name") or tool_call.get("tool_name") or "").strip()
    tool_name = handoff_gate.AGY_TOOL_ALIASES.get(raw_tool_name, raw_tool_name)
    display_tool = raw_tool_name or tool_name
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}

    payload_cwd = payload.get("cwd") or (workspace_paths[0] if workspace_paths else None)
    command_cwd = (args.get("Cwd") or args.get("cwd")) if tool_name in ("run_command", "run_workflow") else None
    cwd = _ast_resolve_cwd(str(command_cwd), payload_cwd) if command_cwd else payload_cwd
    state_dir, logs_dir = get_data_dirs()

    base_cwd: Optional[str] = None
    boundary: Optional[Dict[str, Any]] = None
    # 分支 0: 项目目录外文件改动硬边界门禁 (R4: 跨表面文件修改工具 / run_command / run_workflow)
    if tool_name in (*FILE_MUTATION_TOOLS, "run_command", "run_workflow"):
        proj_keys, allowed_keys, allowed_res_keys, proj_display, base_cwd = _collect_boundary_context(
            payload, state_dir, logs_dir
        )
        if proj_keys:
            allow_os_temp = not any(_is_standard_os_temp_key(pk) for pk in proj_keys)
            if tool_name in FILE_MUTATION_TOOLS:
                write_arg = str(
                    args.get("Path")
                    or args.get("path")
                    or args.get("AbsolutePath")
                    or args.get("absolute_path")
                    or args.get("FilePath")
                    or args.get("TargetFile")
                    or args.get("target_file")
                    or args.get("file_path")
                    or args.get("NotebookPath")
                    or args.get("notebook_path")
                    or ""
                ).strip()
                if write_arg and not _is_write_target_allowed(
                    write_arg, base_cwd, allowed_keys, allowed_res_keys, allow_os_temp, from_shell=False
                ):
                    if tool_name in EDIT_FILE_TOOLS:
                        t_lines = len(str(args.get("Target") or args.get("TargetContent") or "").splitlines()) or 1
                        r_lines = len(str(args.get("Replacement") or args.get("ReplacementContent") or "").splitlines())
                        act = f"修改项目外已有文件 ({display_tool}: 替换 {t_lines} 行 -> {r_lines} 行)"
                    elif args.get("Append"):
                        content_txt = str(args.get("Content") or args.get("CodeContent") or "")
                        act = f"向项目外文件追加内容 ({display_tool} Append=true, {len(content_txt)} 字符)"
                    else:
                        content_txt = str(args.get("Content") or args.get("CodeContent") or "")
                        act = f"创建或覆写项目外文件 ({display_tool}, {len(content_txt)} 字符)"
                    meta = args.get("ArtifactMetadata") if isinstance(args.get("ArtifactMetadata"), dict) else {}
                    summary_txt = str(meta.get("Summary") or "").splitlines()[0].strip() if meta.get("Summary") else ""
                    boundary = _build_boundary_violation_response(
                        display_tool, act, write_arg, proj_display, conversation_id, tool_summary=summary_txt
                    )
            elif tool_name == "run_workflow":
                wf_desc = str(args.get("Description") or args.get("description") or "").strip()
                wf_hit = _detect_workflow_external_mutation(
                    args,
                    str(payload.get("cwd") or proj_display.split(",")[0].strip()),
                    base_cwd,
                    workspace_paths,
                    allowed_keys,
                    allowed_res_keys,
                    allow_os_temp,
                )
                if wf_hit:
                    act_desc, ext_target, clause_snip = wf_hit
                    boundary = _build_boundary_violation_response(
                        display_tool,
                        act_desc,
                        ext_target,
                        proj_display,
                        conversation_id,
                        tool_summary=wf_desc,
                        snippet=clause_snip,
                    )
            else:
                cmd_str = str(args.get("CommandLine") or args.get("command_line") or args.get("command") or "")
                cmd_summary = str(args.get("toolSummary") or args.get("description") or "").strip()
                ext_hit = _detect_command_external_mutation(
                    cmd_str, cwd or base_cwd, allowed_keys, allowed_res_keys, allow_os_temp
                )
                if ext_hit:
                    act_desc, ext_target, clause_snip = ext_hit
                    boundary = _build_boundary_violation_response(
                        display_tool,
                        act_desc,
                        ext_target,
                        proj_display,
                        conversation_id,
                        tool_summary=cmd_summary,
                        snippet=clause_snip,
                    )

    if not user_words.valid_session_id(conversation_id):
        reason = _check_dispatch_locked(payload, tool_name, args, conversation_id, state_dir)
        return {"decision": "deny", "reason": reason} if reason else boundary or {"decision": "allow"}
    try:
        with user_words.session_state_lock(state_dir, conversation_id):
            gate_response = handoff_gate.check_pre_tool_use_locked(payload, state_dir, boundary_blocked=boundary is not None)
            if gate_response is not None:
                return gate_response
            if boundary is not None:
                return boundary
            ticket_reason = _check_dispatch_locked(payload, tool_name, args, conversation_id, state_dir)
            if ticket_reason:
                return {"decision": "deny", "reason": ticket_reason}
            try:
                return _check_polling_locked(tool_name, args, conversation_id, base_cwd or cwd, workspace_paths, transcript_path_str,
                                             state_dir, logs_dir)
            except (OSError, ValueError) as exc:
                log_event("PreToolUse:poll_state_error", conversation_id, f"轮询状态未更新（{exc}）")
                return {"decision": "allow"}
    except (OSError, ValueError) as exc:
        log_event("PreToolUse:state_error", conversation_id, f"工具门禁状态未更新（{exc}）")
        if (state_dir / f"{conversation_id}_handoff_gate.json").exists():
            return {"decision": "deny", "reason": "【user-guard】无法核实会话交接阶段。请重试该工具或直接收尾。"}
        return boundary or {"decision": "allow"}


def _claude_permission(decision: str, reason: str) -> Dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def _expand_rg_braces(pattern: str) -> List[str]:
    match = re.search(r"\{([^{}]+)\}", pattern)
    if not match:
        return [pattern]
    prefix, suffix = pattern[:match.start()], pattern[match.end():]
    expanded: List[str] = []
    for part in match.group(1).split(","):
        expanded.extend(_expand_rg_braces(prefix + part.strip() + suffix))
    return expanded


def _rg_glob_to_regex(pattern: str) -> Tuple[bool, re.Pattern[str]]:
    pat = re.sub(r"/+", "/", pattern.replace("\\", "/"))
    while pat.startswith("./"):
        pat = pat[2:]
    pat = pat.lstrip("/")
    pat = re.sub(r"(?:^|/)\*\*(?:/\*\*)+(?=/|$)", lambda m: "/**" if m.group(0).startswith("/") else "**", pat)
    match_path = "/" in pat or "**" in pat
    if not pat or pat in ("*", "**", "**/*"):
        return True, re.compile(r"^.*$")
    parts: List[str] = ["^"]
    idx, length = 0, len(pat)
    while idx < length:
        if pat.startswith("/**/", idx):
            parts.append(r"(?:/.+/|/)")
            idx += 4
        elif idx == 0 and pat.startswith("**/", idx):
            parts.append(r"(?:.+/)?")
            idx += 3
        elif pat.startswith("/**", idx) and idx + 3 == length:
            parts.append(r"/.*")
            idx += 3
        elif pat.startswith("**", idx):
            parts.append(r".*")
            idx += 2
        elif pat[idx] == "*":
            parts.append(r"[^/]*")
            idx += 1
        elif pat[idx] == "?":
            parts.append(r"[^/]")
            idx += 1
        elif pat[idx] == "[":
            search_start = idx + 2 if pat[idx + 1 : idx + 2] in ("!", "^") else idx + 1
            if pat[search_start : search_start + 1] == "]":
                search_start += 1
            end = pat.find("]", search_start)
            if end != -1:
                neg = "^" if pat[idx + 1 : idx + 2] in ("!", "^") else ""
                body = pat[idx + (2 if neg else 1) : end]
                if body.startswith("]"):
                    body = r"\]" + body[1:]
                parts.append(f"(?!/)[{neg}{body}]")
                idx = end + 1
            else:
                parts.append(r"\[")
                idx += 1
        else:
            parts.append(re.escape(pat[idx]))
            idx += 1
    parts.append("$")
    try:
        return match_path, re.compile("".join(parts))
    except re.error:
        return match_path, re.compile("^" + re.escape(pat) + "$")


def _rg_glob_matches(rel_posix: str, name: str, pattern: str) -> bool:
    match_path, regex = _rg_glob_to_regex(pattern)
    return bool(regex.match(rel_posix if match_path else name))


def _claude_grep_evidence_targets(target: Path, raw_glob: Any) -> List[Path]:
    raw_pat = str(raw_glob or "").strip()
    clauses = [item.strip() for item in (_expand_rg_braces(raw_pat) if raw_pat else []) if item.strip()]
    positives = [item for item in clauses if not item.startswith("!")]
    negatives = [item[1:].strip() for item in clauses if item.startswith("!") and item[1:].strip()]
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}
    matched: List[Path] = []
    try:
        for root_str, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            root_path = Path(root_str)
            for fname in files:
                fpath = root_path / fname
                if not is_evidence_file(str(fpath)):
                    continue
                rel_posix = fpath.relative_to(target).as_posix()
                if positives and not any(_rg_glob_matches(rel_posix, fname, pat) for pat in positives):
                    continue
                if any(_rg_glob_matches(rel_posix, fname, pat) for pat in negatives):
                    continue
                matched.append(fpath)
    except OSError:
        return []
    return sorted(matched)


def _codex_readonly_subagent_role(payload: Dict[str, Any]) -> str:
    """当 client=='codex' 且 rollout 首条 session_meta 明确标识当前会话自身为 readonly-* 子代理时返回其名称。"""
    if payload.get("_client") != "codex":
        return ""
    raw_tp = user_words.strip_win_extended_prefix(
        str(payload.get("transcript_path") or payload.get("transcriptPath") or "")
    )
    if not raw_tp:
        return ""
    tp = Path(raw_tp).expanduser()
    if not tp.is_file():
        return ""
    try:
        first_line, too_long = user_words.read_bounded_first_line(tp)
        if too_long or not first_line:
            return ""
        entry = json.loads(user_words.decode_jsonl_line(first_line).lstrip("\ufeff"))
    except (OSError, ValueError, RecursionError):
        return ""
    if not isinstance(entry, dict) or entry.get("type") != "session_meta":
        return ""
    meta = entry.get("payload")
    if not isinstance(meta, dict):
        return ""

    def _pick_readonly(obj: Any) -> str:
        if isinstance(obj, str):
            val = obj.strip()
            return val if val.lower().startswith("readonly-") else ""
        if isinstance(obj, dict):
            for k in (
                "agent_role", "agent_nickname", "subagent", "agent_type", "subagent_type",
                "agent_name", "role", "name", "custom", "other", "thread_spawn",
            ):
                if k in obj:
                    hit = _pick_readonly(obj[k])
                    if hit:
                        return hit
        return ""

    for top_key in ("agent_role", "agent_nickname", "subagent", "agent_type", "subagent_type", "agent_name"):
        if top_key in meta:
            hit = _pick_readonly(meta[top_key])
            if hit:
                return hit
    src = meta.get("source")
    if isinstance(src, dict) and "subagent" in src:
        hit = _pick_readonly(src["subagent"])
        if hit:
            return hit
    return ""


def _extract_native_shell_command(args: Dict[str, Any]) -> str:
    for key in ("command", "cmd", "CommandLine", "command_line"):
        raw_cmd = args.get(key)
        if raw_cmd is not None and raw_cmd != "" and raw_cmd != []:
            if isinstance(raw_cmd, list):
                return " ".join(shlex.quote(str(part)) for part in raw_cmd)
            return str(raw_cmd)
    return ""


def _sed_has_mutating_flags_or_cmds(args: List[str]) -> bool:
    scripts: List[str] = []
    explicit_e = False
    i = 0
    n = len(args)
    while i < n:
        a = args[i]
        low = a.lower()
        if a == "--":
            i += 1
            break
        if low == "--in-place" or low.startswith("--in-place=") or a.startswith("-i") or (
            a.startswith("-") and not a.startswith("--") and "i" in a[1:]
        ):
            return True
        if low in ("-f", "--file") or low.startswith("--file=") or (a.startswith("-f") and not a.startswith("--")):
            return True
        if a in ("-e", "--expression") and i + 1 < n:
            explicit_e = True
            scripts.append(args[i + 1])
            i += 2
            continue
        if low.startswith("--expression="):
            explicit_e = True
            scripts.append(a.split("=", 1)[1])
            i += 1
            continue
        if a.startswith("-e") and not a.startswith("--") and len(a) > 2:
            explicit_e = True
            scripts.append(a[2:])
            i += 1
            continue
        if a.startswith("-"):
            i += 1
            continue
        if not explicit_e and not scripts:
            scripts.append(a)
        i += 1
    for expr in scripts:
        # 拦截 sed 写文件指令 w/W 与 GNU sed 执行命令指令 e
        if re.search(r"(?:^|[;\n}])\s*(?:\d+|\$|/[^/]*/)?(?:\s*,\s*(?:\d+|\$|/[^/]*/))?\s*[wWe]\b", expr):
            return True
        if re.search(r"s(?P<d>[^\\\s])(?:\\.|(?!(?P=d)).)*(?P=d)(?:\\.|(?!(?P=d)).)*(?P=d)[0-9gipmM]*[wWe]", expr):
            return True
    return False


def _python_tokens_is_safe_inline_readonly(tokens: List[str]) -> bool:
    args = tokens[1:]
    if not args:
        return False
    if len(args) == 1 and args[0] in ("--version", "-V", "--help", "-h"):
        return True
    i = 0
    n = len(args)
    code_str: Optional[str] = None
    while i < n:
        tok = args[i]
        if tok == "--":
            return False
        if tok in ("-W", "-X", "-Q", "--check-hash-based-pycs"):
            i += 2
            continue
        if tok == "--safe-path" or tok.startswith(("-W", "-X", "-Q", "--check-hash-based-pycs=")) or re.fullmatch(r"-[0-9][0-9.]*", tok):
            i += 1
            continue
        m_c = re.fullmatch(r"-[BbIdEisSuqvVxOP]*c(.*)", tok, re.DOTALL)
        if m_c:
            if m_c.group(1):
                code_str = m_c.group(1)
            elif i + 1 < n:
                code_str = args[i + 1]
            break
        if tok.startswith("-") and tok != "-" and not tok.startswith("-m") and tok != "--":
            if re.fullmatch(r"-[BbIdEisSuqvVxOP]+", tok):
                i += 1
                continue
        return False
    if code_str is None:
        return False
    try:
        tree = ast.parse(code_str)
    except (SyntaxError, ValueError, RecursionError):
        return False
    blocked_imports = {"shutil", "tempfile", "subprocess", "ctypes", "pty", "socket", "urllib", "http"}
    blocked_builtins = {"exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr", "input"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in blocked_imports for alias in node.names):
                return False
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in blocked_imports:
                return False
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in blocked_builtins:
            return False
    return True


_READONLY_SUBAGENT_EXPLICIT_MUTATION_VERBS = (
    _SHELL_DELETE_VERBS
    | _SHELL_CREATE_WRITE_VERBS
    | _SHELL_MOVE_VERBS
    | _SHELL_COPY_VERBS
    | _SHELL_LINK_VERBS
    | {"sed", "perl", "python", "python3", "py"}
    | {"apply_patch", "patch", "chmod", "chown", "chgrp", "truncate", "unlink", "shred", "dd", "install"}
)


def _detect_readonly_subagent_command_mutation(cmd_str: str, base_cwd: Optional[str]) -> Optional[Tuple[str, str, str]]:
    hit = _detect_command_external_mutation(cmd_str, base_cwd, [], [], allow_os_temp=False)
    if hit:
        return hit
    for tokens in _dispatch_commands(cmd_str):
        if not tokens:
            continue
        head = Path(tokens[0].replace("\\", "/")).name.lower().removesuffix(".exe")
        is_py_bin = head in ("python", "python3", "py") or bool(re.fullmatch(r"python[0-9.]+", head))
        if head in _READONLY_SUBAGENT_EXPLICIT_MUTATION_VERBS or is_py_bin:
            if head == "sed":
                if _sed_has_mutating_flags_or_cmds(tokens[1:]):
                    return "只读子代理禁止修改型 sed 命令 (sed)", tokens[0], _compact_snippet(" ".join(tokens))
                continue
            if head == "perl":
                if not (len(tokens) == 2 and tokens[1] in ("-v", "-V", "-h", "--help", "--version")):
                    return "只读子代理禁止执行 perl 脚本或内联修改 (perl)", tokens[0], _compact_snippet(" ".join(tokens))
                continue
            if is_py_bin:
                if not _python_tokens_is_safe_inline_readonly(tokens):
                    return f"只读子代理禁止执行脚本、模块或动态 Python 命令 ({head})", tokens[0], _compact_snippet(" ".join(tokens))
                continue
            return f"只读子代理禁止写删或修改命令 ({head})", tokens[0], _compact_snippet(" ".join(tokens))
        if head == "find" and any(
            a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls")
            for a in tokens[1:]
        ):
            return "只读子代理禁止 find 删除或执行副作用", "find", _compact_snippet(" ".join(tokens))
        if head == "git":
            subcmd, subargs = handoff_gate._git_subcommand(tokens)
            writes_output = any(a == "--output" or a.startswith("--output=") for a in subargs)
            is_ro = (
                (subcmd in handoff_gate.GIT_READONLY_SUBCMDS and not writes_output)
                or (subcmd == "branch" and handoff_gate._git_branch_is_readonly(subargs) and not writes_output)
            )
            if not is_ro:
                label = f"git {subcmd}".strip() if subcmd else "git"
                return f"只读子代理禁止修改型 Git 命令 ({label})", label, _compact_snippet(" ".join(tokens))
    return None


def _claude_check_polling(
    tool_name: str,
    args: Dict[str, Any],
    sid: str,
    cwd: Optional[str],
    state_dir: Path,
    logs_dir: Path,
    client: str = "claude",
) -> Dict[str, Any]:
    tool_name = _native_tool_name(tool_name)
    raw_target = ""
    if tool_name == "Read":
        raw_target = str(args.get("file_path") or "")
        signature = json.dumps({key: args.get(key) for key in ("offset", "limit")}, sort_keys=True)
    elif tool_name == "Grep":
        raw_target = str(args.get("path") or cwd or "")
        signature = json.dumps({key: args.get(key) for key in
                                ("pattern", "glob", "output_mode", "head_limit", "offset", "type", "-i", "-n", "-A", "-B", "-C", "multiline")},
                               sort_keys=True, ensure_ascii=False)
    elif tool_name in ("Bash", "PowerShell", "exec_command", "bash", "shell", "shell_command"):
        raw_target, subsystem, signature = parse_run_command_evidence(_extract_native_shell_command(args))
        if subsystem:
            return {}
    else:
        return {}
    if not raw_target:
        return {}
    target = resolve_target_path(raw_target, cwd, [])
    if tool_name == "Grep" and target.is_dir():
        targets = _claude_grep_evidence_targets(target, args.get("glob"))
    else:
        targets = [target] if is_evidence_file(str(target)) else []
    fingerprints = []
    for path in targets:
        if _is_inside_dir(path, state_dir) or _is_inside_dir(path, logs_dir):
            continue
        fp = compute_file_fingerprint(path)
        if fp:
            fingerprints.append(fp)
    if not fingerprints:
        return {}
    state_file = state_dir / f"{sid}_status_guard.json"
    state = _load_guard_state(state_file, sid, logs_dir, client=client)
    query_key = f"claude:{tool_name}:{_canonical_path_key(target)}|{signature}"
    previous = state["records"].get(query_key)
    unchanged = isinstance(previous, dict) and previous.get("files") == fingerprints
    state["records"][query_key] = {"files": fingerprints, "params": signature}
    _save_guard_state(state_file, state, sid)
    if unchanged:
        reason = ("【user-guard 防空转门禁阻断】目标证据文件内容与查询参数自上次读取后未发生任何变化。"
                  "请等待后台任务产生新输出或先推进其他工作；改变读取区间或查询参数后将自动放行。")
        log_event("PreToolUse:status_guard", sid, f"DENIED unchanged {client} {tool_name}", logs_dir, client=client)
        return _claude_permission("deny", reason)
    return {}


def handle_claude_pre_tool_use(payload: Dict[str, Any], state_dir: Optional[Path] = None,
                               logs_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Claude / Codex PreToolUse：项目边界、只读子代理防护与证据查询门禁，放行使用空对象。"""
    _sync_status_tunables()
    sid = str(payload.get("session_id") or "unknown_session").strip()
    client = str(payload.get("_client") or "claude")
    home = Path(os.environ.get("CODEX_HOME" if client == "codex" else "CLAUDE_CONFIG_DIR") or Path.home() / (".codex" if client == "codex" else ".claude")).expanduser()
    state_dir = Path(state_dir).expanduser() if state_dir is not None else home / "hooks" / "state"
    logs_dir = Path(logs_dir).expanduser() if logs_dir is not None else home / "hooks" / "logs"
    raw_tool_name = str(payload.get("tool_name") or "").strip()
    tool_name = _native_tool_name(raw_tool_name)
    raw_input = payload.get("tool_input")
    args = raw_input if isinstance(raw_input, dict) else {}
    cwd = str(payload.get("cwd") or "").strip() or None
    boundary_payload = {"cwd": cwd, "transcriptPath": payload.get("transcript_path"), "_client": client, "toolCall": {"name": "run_command", "args": {"Cwd": args.get("workdir") or args.get("Cwd") or args.get("cwd")}}}
    projects, allowed, resolved, display, base_cwd = _collect_boundary_context(boundary_payload, state_dir, logs_dir)

    ro_subagent = _codex_readonly_subagent_role(payload)
    if ro_subagent:
        ro_hit: Optional[Tuple[str, str, str]] = None
        if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit", "write", "edit", "multiedit", "notebookedit", "write_file", "write_to_file", "edit_file"):
            target = str(args.get("file_path") or args.get("notebook_path") or args.get("path") or args.get("Path") or "<FILE>").strip()
            ro_hit = (f"{tool_name} 修改文件", target, "")
        elif tool_name == "apply_patch":
            ro_hit = ("apply_patch 修改文件", "<PATCH>", "")
        elif tool_name.lower() == "write_stdin":
            ro_hit = ("write_stdin 向会话写入标准输入", f"session_id={args.get('session_id', '<SESSION>')}", "")
        elif tool_name in ("Bash", "PowerShell", "exec_command", "bash", "run_command", "shell", "shell_command"):
            ro_hit = _detect_readonly_subagent_command_mutation(_extract_native_shell_command(args), base_cwd or cwd)
        if ro_hit:
            ro_act, ro_tgt, ro_snip = ro_hit
            ro_reason = (
                f"【user-guard 只读子代理硬边界】当前 Codex 会话为只读子代理（{ro_subagent}），"
                f"严禁执行写文件、apply_patch 或写删命令：{ro_act} -> {ro_tgt}。"
            )
            if ro_snip:
                ro_reason += f"触发语句：{ro_snip}。"
            log_event("PreToolUse:readonly_subagent", sid, ro_reason, logs_dir, client=client)
            return _claude_permission("deny", ro_reason)

    hit = None
    if projects:
        allow_temp = not any(_is_standard_os_temp_key(key) for key in projects)
        if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit", "write", "edit", "multiedit", "notebookedit"):
            target = str(args.get("file_path") or args.get("notebook_path") or args.get("path") or args.get("Path") or "").strip()
            if target and not _is_write_target_allowed(target, base_cwd, allowed, resolved, allow_temp, from_shell=False):
                hit = (f"{tool_name} 创建或修改文件", target, "")
            elif not target and (args.get("patch") or args.get("patch_text") or isinstance(raw_input, str)):
                patch_text = str(args.get("patch") or args.get("patch_text") or (raw_input if isinstance(raw_input, str) else ""))
                edits = _APPLY_PATCH_HEADER_RE.findall(patch_text)
                if not edits:
                    hit = (f"{tool_name} 未解析标准补丁头", "<UNPARSED_PATCH>", _compact_snippet(patch_text) if patch_text.strip() else "<EMPTY_PATCH>")
                else:
                    for op, raw_p in edits:
                        cand_p = raw_p.strip()
                        if not cand_p or not _is_write_target_allowed(cand_p, base_cwd, allowed, resolved, allow_temp, from_shell=False):
                            hit = (f"{tool_name} ({op})", cand_p or "<EMPTY_PATCH_PATH>", f"*** {op}: {cand_p}")
                            break
        elif tool_name == "apply_patch":
            patch_text = str(
                args.get("patch")
                or args.get("patch_text")
                or args.get("input")
                or args.get("command")
                or args.get("cmd")
                or (raw_input if isinstance(raw_input, str) else "")
            )
            edits = _APPLY_PATCH_HEADER_RE.findall(patch_text)
            if not edits:
                hit = ("apply_patch 未解析标准补丁头", "<UNPARSED_PATCH>", _compact_snippet(patch_text) if patch_text.strip() else "<EMPTY_PATCH>")
            else:
                for op, raw_p in edits:
                    cand_p = raw_p.strip()
                    if not cand_p or not _is_write_target_allowed(cand_p, base_cwd, allowed, resolved, allow_temp, from_shell=False):
                        hit = (f"apply_patch ({op})", cand_p or "<EMPTY_PATCH_PATH>", f"*** {op}: {cand_p}")
                        break
        elif tool_name in ("Bash", "PowerShell", "exec_command", "bash", "shell", "shell_command"):
            hit = _detect_command_external_mutation(_extract_native_shell_command(args), base_cwd, allowed, resolved, allow_temp)
        elif tool_name.lower() == "write_stdin":
            stdin_text = str(args.get("chars") if args.get("chars") is not None else (args.get("input") or ""))
            if stdin_text.strip():
                hit = _detect_command_external_mutation(stdin_text, base_cwd, allowed, resolved, allow_temp)
    if hit:
        action, target, snippet = hit
        decision = "deny" if _is_headless_subtask() else "ask"
        reason = (f"【user-guard 项目目录硬边界】当前项目根目录：{display}。"
                  f"具体写删动作：{action}；项目外目标路径：{target}。")
        description = str(args.get("description") or "").strip()
        if description:
            reason += f"任务说明：{description}。"
        if snippet:
            reason += f"触发语句：{snippet}。"
        reason += "无头子任务禁止越界写入。" if decision == "deny" else "按 R4，改动项目外业务文件或全局配置须先获得用户明确批准。"
        log_event("PreToolUse:workspace_boundary", sid, reason, logs_dir, client=client)
        return _claude_permission(decision, reason)
    if not user_words.valid_session_id(sid):
        reason = _check_dispatch_locked(payload, tool_name, args, sid, state_dir)
        return _claude_permission("deny", reason) if reason else {}
    try:
        with user_words.session_state_lock(state_dir, sid):
            reason = _check_dispatch_locked(payload, tool_name, args, sid, state_dir)
            if reason:
                return _claude_permission("deny", reason)
            return _claude_check_polling(tool_name, args, sid, base_cwd or cwd, state_dir, logs_dir, client=client)
    except (OSError, ValueError) as exc:
        label = "Codex" if client == "codex" else "Claude"
        log_event("PreToolUse:state_error", sid, f"{label} 轮询状态未更新（{exc}）", logs_dir, client=client)
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="项目目录边界与防空转门禁")
    parser.add_argument("--client", choices=("antigravity", "claude", "codex"), default="antigravity")
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--log-dir", default="")
    args, _ = parser.parse_known_args()
    payload: Dict[str, Any] = {}
    try:
        payload = user_words.read_hook_payload()
    except (OSError, ValueError) as e:
        if args.client in ("claude", "codex"):
            client_label = "Codex" if args.client == "codex" else "Claude"
            sys.stderr.write(f"{client_label} PreToolUse JSON parse error: {e}\n")
        else:
            log_event("PreToolUse:status_guard", "unknown", f"Failed to parse stdin JSON: {e}")

    try:
        response = (handle_claude_pre_tool_use({**payload, "_client": args.client}, Path(args.state_dir) if args.state_dir else None,
                                              Path(args.log_dir) if args.log_dir else None)
                    if args.client in ("claude", "codex") else handle_pre_tool_use(payload))
    except Exception as e:
        stack_str = traceback.format_exc()
        if args.client in ("claude", "codex"):
            client_label = "Codex" if args.client == "codex" else "Claude"
            sys.stderr.write(f"{client_label} PreToolUse: {e}\n")
            response = {}
        else:
            log_event("PreToolUse:status_guard", str(payload.get("conversationId", "unknown")), f"Exception: {e}\n{stack_str}")
            response = {"decision": "allow"}

    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
