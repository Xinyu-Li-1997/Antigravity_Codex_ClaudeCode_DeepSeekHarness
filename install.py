#!/usr/bin/env python3
"""
Antigravity、Codex、Claude Code 与 DeepSeek Harness 全局配置安装脚本（仅依赖 Python 3.11+ 标准库）。

主要功能：
1. 自动探测已安装客户端并按需同步配置（--app auto|all|both|antigravity|codex|claude|dsh）：
   - Antigravity：同步 ~/.gemini/config/plugins/user-guard 插件（含压缩后原话恢复、项目外写操作拦截、重复等待拦截与长会话交接钩子）、CLI 默认模型补缺与可选 Zotero MCP；
   - Codex：同步 ~/.codex/AGENTS.md（保留自定义尾段）、只读子代理、会话钩子、config.toml 工作区写沙箱与默认模型补缺及可选 Zotero MCP；
   - Claude Code：同步 ~/.claude/CLAUDE.md（保留自定义尾段）、只读子代理、生命周期钩子与托管技能，可选同步本地分流代理并停用 CC Switch 旧提示词；
   - DeepSeek Harness（DSH）：同步 ~/.dsh/AGENTS.md（保留自定义尾段）与托管技能，不改动登录凭据或 cordis.patch.yml。
2. 仅检测到单个客户端时提示是否先安装其他客户端；选择继续或传入 --yes 时直接完成当前客户端配置。
3. --probe-apps 只读探测客户端安装情况；--audit-upgrade 只读核查配置差异、CC Switch 卡片状态与本地模型列表。
4. 安装后默认以 -B 无字节码模式运行 tests/ 下回归测试（可通过 --skip-test 跳过）。
"""

import argparse
import copy
import datetime
import hashlib
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import signal
import stat
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time

if sys.version_info < (3, 11):
    sys.stderr.write("[!] 错误：需要 Python 3.11 或更高版本 (Python 3.11+ is required).\n")
    raise SystemExit(2)

import tomllib
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple
from types import ModuleType
from urllib.parse import urlsplit

# 全平台强制标准流为 UTF-8
for _stream_name in ("stdin", "stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

# 一、常量、路径与平台识别

IGNORED_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db", ".gitkeep"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".pyd"}
EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
ZOTERO_ENV = {"ZOTERO_LOCAL": "true", "PYTHON_DOTENV_DISABLED": "1", "NO_PROXY": "localhost,127.0.0.1"}
UTF8_BOM = b"\xef\xbb\xbf"
REGRESSION_TIMEOUT_SECONDS = 600


TOML_STRING = r'''(?:"(?:[^"\\\r\n]|\\.)*"|'[^'\r\n]*')'''
TOML_KEY = rf"(?:[A-Za-z0-9_-]+|{TOML_STRING})"
TOML_KEY_PATH = rf"{TOML_KEY}(?:[ \t]*\.[ \t]*{TOML_KEY})*"
TOML_TABLE = re.compile(rf"^[ \t]*(\[{{1,2}})[ \t]*({TOML_KEY_PATH})[ \t]*(\]{{1,2}})[ \t]*(?:#.*)?$")
TOML_ASSIGNMENT = re.compile(rf"^([ \t]*({TOML_KEY_PATH})[ \t]*=[ \t]*)(.*)$")
HOOK_PYTHON_FLAGS = ["-X", "utf8", "-I", "-B"]
PY_LAUNCHER_ARGS: Tuple[List[str], ...] = (["-3"], ["-3.14"], ["-3.13"], ["-3.12"], ["-3.11"])
POSIX_HOOK_PYTHON_NAMES: Tuple[str, ...] = (
    "python3", "python", "python3.14", "python3.13", "python3.12", "python3.11",
)
AGY_SINGLE_EFFORT_RE = re.compile(r"^(.+)-(minimal|low|medium|high|xhigh|max)$", re.IGNORECASE)
LEGACY_PYTHON_FLAGS = ([], ["-B"], ["-I", "-B"], HOOK_PYTHON_FLAGS)
HANDOFF_MARKER = re.compile(r"\[user-guard codex-handoff v1 mode=(advisory|enforce|off) compact_limit=([0-9]+)\]")
CLAUDE_HOOK_SCRIPTS = ("user_words.py", "session_context_loader.py", "status_guard.py", "reply_repeat_guard.py", "handoff_gate.py")
CODEX_HOOK_ENTRIES = ("session_context_loader.py", "status_guard.py", "reply_repeat_guard.py", "handoff_gate.py")
HOOK_LIB_FILES = ("__init__.py", "fs_safety.py", "shell_lex.py", "adaptive_policy.py", "process_guard.py")
CLAUDE_RETIRED_SCRIPTS = ("background_status.py", "read_delegation_guard.py", "receipts_stop.py")


def _load_fleet_profile(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """读取模型评估配置文件 fleet_profile.json（支持环境变量覆盖以供离线单测）。"""
    env_override = os.environ.get("CAE_FLEET_PROFILE", "").strip()
    candidates: List[Path] = []
    if env_override:
        candidates.append(Path(env_override).expanduser())
    base = repo_root if repo_root is not None else Path(__file__).resolve().parent
    candidates.append(base / "template/user-guard/skills/cae-harness/fleet_profile.json")
    for candidate in candidates:
        try:
            if candidate.is_file() and not _is_link_or_junction(candidate):
                parsed = json.loads(candidate.read_text(encoding="utf-8-sig"))
                if isinstance(parsed, dict):
                    return parsed
        except (OSError, UnicodeError, ValueError):
            continue
    return {}


def _default_claude_effort(repo_root: Optional[Path] = None) -> str:
    rules = _load_fleet_profile(repo_root).get("client_catalog_rules")
    cl_rules = rules.get("claude") if isinstance(rules, dict) else None
    val = str((cl_rules or {}).get("default_interactive_effort") or "xhigh").strip().lower() if isinstance(cl_rules, dict) else "xhigh"
    return val if val in {"low", "medium", "high", "xhigh", "max"} else "xhigh"


def _load_adaptive_policy_readonly(repo_root: Optional[Path] = None) -> Optional[ModuleType]:
    base_root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parent
    policy_path = base_root / "template/user-guard/hooks/_lib/adaptive_policy.py"
    if not policy_path.is_file():
        return None
    try:
        mod = ModuleType("_install_adaptive_policy")
        mod.__file__ = str(policy_path)
        exec(compile(policy_path.read_text(encoding="utf-8"), str(policy_path), "exec"), mod.__dict__)
        return mod
    except (OSError, SyntaxError, ValueError, RuntimeError):
        return None


def _default_handoff_policy(
    client: str,
    repo_root: Optional[Path] = None,
    model_name: str = "",
    effort: str = "",
    explicit_window: Optional[int] = None,
) -> Tuple[str, int]:
    profile = _load_fleet_profile(repo_root)
    adaptive_policy = _load_adaptive_policy_readonly(repo_root)
    if adaptive_policy is not None:
        resolved = adaptive_policy.resolve_adaptive_handoff_policy(
            profile, client, model_name=model_name, effort=effort, explicit_window=explicit_window,
        )
        return str(resolved["default_mode"]), int(resolved["default_compact_limit"])
    tunables = profile.get("runtime_tunables")
    ht = tunables.get("handoff_thresholds") if isinstance(tunables, dict) else None
    cfg = ht.get(client) if isinstance(ht, dict) else None
    if not isinstance(cfg, dict):
        return "advisory", 0
    raw_mode = str(cfg.get("default_mode") or "advisory").strip().lower()
    mode = raw_mode if raw_mode in {"advisory", "enforce", "off"} else "advisory"
    raw_lim = cfg.get("default_compact_limit")
    limit = int(raw_lim) if isinstance(raw_lim, int) and not isinstance(raw_lim, bool) and raw_lim >= 0 else 0
    return mode, limit


def _is_mnt_windows_path(path_str: Optional[str]) -> bool:
    """在 POSIX/WSL 下判断可执行文件路径是否位于 /mnt/<盘符>/ 挂载的 Windows 侧"""
    if os.name == "nt" or not path_str:
        return False
    return bool(re.match(r"^/mnt/[a-zA-Z]/", path_str.replace("\\", "/")))


def _is_wsl() -> bool:
    if os.name == "nt":
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower()
    except (OSError, UnicodeError):
        return False


# 二、路径安全、原子读写与 TOML / JSON 配置处理


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    tag = getattr(metadata, "st_reparse_tag", 0)
    if os.name == "nt" and tag:
        return tag in (
            getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
            getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
        )
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _same_nonlink_path(left: Path, right: Path) -> bool:
    if not left.is_absolute() or not right.is_absolute():
        return False
    if left == right:
        return True
    try:
        for path in (left, right):
            if any(_is_link_or_junction(item) for item in (*path.parents, path)):
                return False
        return left.resolve() == right.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _validate_path(path: Path, *, directory: bool, allow_hardlinks: bool = False) -> Path:
    """逐级检查真实目录，拒绝路径跳转和目标文件硬链接。"""
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise ValueError(f"安装路径不能包含未解析的上级目录: {path}")
    for ancestor in (*reversed(path.parents), path):
        if os.name == "nt":
            if ancestor.name.endswith((".", " ")):
                raise ValueError(f"Windows 安装路径不能包含以空格或点结尾的名称: {ancestor}")
            if re.search(r'[<>:"|?*\x00-\x1f]', ancestor.name) or re.fullmatch(
                r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", ancestor.name, re.IGNORECASE,
            ):
                raise ValueError(f"Windows 安装路径包含保留名称或非法字符: {ancestor}")
        if _is_link_or_junction(ancestor):
            if ancestor == path:
                raise ValueError(f"安装目标是链接或目录联接，请指定真实路径: {ancestor}")
            raise ValueError(f"安装路径含祖先链接或目录联接，请指定真实路径: {ancestor}")
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            continue
        want_directory = ancestor != path or directory
        if want_directory and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"安装目标或其祖先必须为目录: {ancestor}")
        if not want_directory and not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"安装目标必须为普通文件: {ancestor}")
        if not want_directory and not allow_hardlinks and metadata.st_nlink > 1:
            raise ValueError(f"安装目标存在硬链接，请指定独立文件: {ancestor}")
    return path


def _validate_tree_links(root: Path) -> None:
    """在镜像同步和缓存清理前检查整棵目录树中的链接。"""
    _validate_path(root, directory=True)
    if not root.is_dir():
        return

    def fail(error: OSError) -> None:
        raise error

    for parent, dirs, files in os.walk(root, onerror=fail, followlinks=False):
        for name in dirs + files:
            entry = Path(parent) / name
            if _is_link_or_junction(entry):
                raise ValueError(f"安装目标包含链接或目录联接，请先核对: {entry}")


def validate_install_target(target: Path, repo_root: Path) -> Path:
    """在镜像同步前核实绝对目标，拒绝源树、用户根目录和危险链接。"""
    resolved = _validate_path(target, directory=True).resolve()
    repo_root = repo_root.resolve()
    user_home = Path.home()
    user_root = user_home.resolve()
    protected = {
        user_root,
        user_root / ".gemini",
        user_root / ".gemini" / "config",
        user_root / ".gemini" / "config" / "plugins",
        user_root / ".codex",
        user_root / ".codex" / "skills",
        user_root / ".claude",
        user_root / ".claude" / "skills",
        user_root / ".dsh",
        user_root / ".dsh" / "skills",
    }
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    if configured_home:
        codex_home = Path(configured_home).expanduser()
        codex_root = codex_home.resolve()
        protected.update((codex_root, codex_root / "skills"))
    if resolved in protected or resolved in user_root.parents:
        raise ValueError(f"安装目标不能是用户根目录或共享配置目录: {resolved}")
    if resolved == repo_root or resolved in repo_root.parents or repo_root in resolved.parents:
        raise ValueError(f"安装目标与源码仓库重叠: {resolved}")
    _validate_tree_links(resolved)
    return resolved


def validate_codex_home_target(codex_home: Path, repo_root: Path, label: str = "Codex") -> Path:
    """校验客户端配置主目录（支持默认目录或独立沙箱目录，拒绝源仓库重叠、用户主目录及链接重定向）"""
    resolved = _validate_path(codex_home, directory=True).resolve()
    repo_resolved = repo_root.resolve()
    user_root = Path.home().resolve()

    if resolved == user_root or resolved in user_root.parents:
        raise ValueError(f"{label} 目标目录不能是用户主目录或上级系统目录: {resolved}")
    gemini_root = user_root / ".gemini"
    if resolved == gemini_root or gemini_root in resolved.parents:
        raise ValueError(f"{label} 目标目录不能与反重力配置目录混用: {resolved}")
    if resolved == repo_resolved or resolved in repo_resolved.parents or repo_resolved in resolved.parents:
        raise ValueError(f"{label} 目标目录与源码仓库重叠: {resolved}")
    return resolved


class FileChange(NamedTuple):
    path: Path
    original: Optional[bytes]
    content: bytes


def _read_optional_bytes(path: Path, *, allow_hardlinks: bool = False) -> Optional[bytes]:
    path = _validate_path(path, directory=False, allow_hardlinks=allow_hardlinks)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _decode_utf8(path: Path, raw: bytes) -> str:
    """接受 UTF-8 BOM，遇到损坏编码时在错误中标明原文件。"""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError(f"{path} 不是有效的 UTF-8，原文件已保留。") from exc


def _atomic_write(change: FileChange) -> None:
    """先核对旧内容，再原子替换，避免中断留下半截文件或覆盖同期修改。"""
    path = _validate_path(change.path, directory=False)
    original, content = change.original, change.content
    if _read_optional_bytes(path) != original:
        raise ValueError(f"{path} 在安装期间发生变更，请重新运行安装器。")
    if content == original:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_path(path, directory=False)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
        _validate_path(path, directory=False)
        if _read_optional_bytes(path) != original:
            raise ValueError(f"{path} 在安装期间发生变更，请重新运行安装器。")
        _validate_path(temp_path, directory=False)
        if original is not None:
            temp_path.chmod(stat.S_IMODE(path.stat().st_mode))
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            # Windows 无法删除带只读属性的临时文件。
            _validate_path(temp_path, directory=False)
            temp_path.chmod(stat.S_IMODE(temp_path.stat().st_mode) | stat.S_IWUSR)
            temp_path.unlink()


def _apply_file_change(change: Optional[FileChange], dry_run: bool) -> None:
    if change is None:
        return
    if dry_run:
        print(f"[Dry-Run] 将更新 {change.path}")
    else:
        _atomic_write(change)
        print(f"[+] 已更新 {change.path}")


def _json_bytes(doc: Dict[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _preserve_text_format(content: bytes, original: Optional[bytes]) -> bytes:
    """重新生成 JSON 等文本时沿用原文件的 BOM 和换行格式。"""
    previous = original or b""
    if b"\r\n" in previous:
        content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return (UTF8_BOM if previous.startswith(UTF8_BOM) else b"") + content


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 中包含非法数值 {value}")


def _reject_duplicate_json_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 键重复: {key}")
        result[key] = value
    return result


def _read_json_object(path: Path, raw: Optional[bytes]) -> Dict[str, Any]:
    if raw is None:
        return {}
    try:
        result = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(result, dict):
            raise ValueError("顶层必须是对象")
        # JSON 数字溢出及未配对的 Unicode 代理项也须在写入前检出。
        _json_bytes(result)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"无法安全合并 {path}，原文件已保留: {exc}") from exc
    return result


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", r"\u007f")


def _toml_key_parts(source: str) -> Tuple[str, ...]:
    table = tomllib.loads(f"[{source}]\n")
    parts = []
    while table:
        key, table = next(iter(table.items()))
        parts.append(key)
    return tuple(parts)


def _parse_toml(source: str, path: Path) -> Dict[str, Any]:
    try:
        return tomllib.loads(source)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"无法安全合并 {path}，原文件已保留: {exc}") from exc


def _toml_inline_table_end(line: str, start: int) -> Optional[int]:
    depth = 0
    cursor = start
    while cursor < len(line):
        char = line[cursor]
        if char in "\"'":
            string = re.match(TOML_STRING, line[cursor:])
            if string is None:
                return None
            cursor += string.end()
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return None


def _toml_inline_table_location(
    line: str, start: int, path: Tuple[str, ...],
) -> Optional[Tuple[int, int, Tuple[str, ...]]]:
    """沿嵌套内联表找到插入位置，缺失的子表可用点分键补齐。"""
    end = _toml_inline_table_end(line, start)
    if end is None:
        return None
    if not path:
        return start, end, ()
    cursor = start + 1
    while cursor < end:
        assignment = TOML_ASSIGNMENT.match(line[cursor:end])
        if assignment is None:
            break
        keys = _toml_key_parts(assignment[2])
        value_start = cursor + assignment.end(1)
        if path[:len(keys)] == keys and line[value_start] == "{":
            return _toml_inline_table_location(line, value_start, path[len(keys):])
        cursor = value_start
        depth = 0
        while cursor < end:
            char = line[cursor]
            if char in "\"'":
                string = re.match(TOML_STRING, line[cursor:])
                if string is None:
                    return None
                cursor += string.end()
                continue
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "," and depth == 0:
                cursor += 1
                break
            cursor += 1
    return start, end, path


def _toml_statement_end(lines: List[str], start: int, offset: int) -> int:
    """跳过多行值，避免把值中的文本当成表头或键。"""
    quote = ""
    depth = 0
    for index in range(start, len(lines)):
        line = lines[index][offset if index == start else 0:]
        pos = 0
        while pos < len(line):
            char = line[pos]
            if quote:
                if quote[0] == '"' and char == "\\":
                    pos += 2
                    continue
                if line.startswith(quote, pos):
                    pos += len(quote)
                    if len(quote) == 3:
                        while pos < len(line) and line[pos] == quote[0]:
                            pos += 1
                    quote = ""
                    continue
            elif char in "\"'":
                quote = char * (3 if line.startswith(char * 3, pos) else 1)
                pos += len(quote)
                continue
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "#":
                break
            pos += 1
        if not quote and not depth:
            return index
    return len(lines) - 1


class CodexConfig:
    """保留注释、BOM 与换行，在原 TOML 文本中补缺或替换指定字符串。"""

    def __init__(self, path: Path):
        self.path = path
        _validate_path(path, directory=False)
        self.original = _read_optional_bytes(path)
        self.source = _decode_utf8(path, self.original or b"")
        self.newline = "\r\n" if "\r\n" in self.source else "\n"
        self.data = _parse_toml(self.source, self.path)

    def _locations(self) -> Tuple[List[str], Dict[Tuple[str, ...], int], Dict[Tuple[str, ...], Tuple[int, re.Match]]]:
        parts = self.source.split("\n")
        lines = [part + "\n" for part in parts[:-1]]
        if parts[-1]:
            lines.append(parts[-1])
        tables: Dict[Tuple[str, ...], int] = {}
        assignments: Dict[Tuple[str, ...], Tuple[int, re.Match]] = {}
        section: Tuple[str, ...] = ()
        index = 0
        while index < len(lines):
            line = lines[index].rstrip("\r\n")
            header = TOML_TABLE.fullmatch(line)
            if header:
                section = _toml_key_parts(header[2])
                if header[1] == "[":
                    tables[section] = index
            assignment = TOML_ASSIGNMENT.fullmatch(line)
            if assignment:
                assignments[section + _toml_key_parts(assignment[2])] = (index, assignment)
                index = _toml_statement_end(lines, index, assignment.end(1))
            index += 1
        return lines, tables, assignments

    def _update(self, lines: List[str]) -> None:
        source = "".join(lines)
        self.data = _parse_toml(source, self.path)
        self.source = source

    def add_defaults(self, path: Tuple[str, ...], defaults: Dict[str, str]) -> None:
        section = self.data
        for key in path:
            section = section.get(key, {})
            if not isinstance(section, dict):
                raise ValueError(f"{self.path} 的 {'.'.join(path)} 配置必须为表")
        missing = [f"{key} = {value}" for key, value in defaults.items() if key not in section]
        if not missing:
            return
        lines, tables, assignments = self._locations()
        missing_lines = [line + self.newline for line in missing]
        inline_parent = next((
            path[:depth] for depth in range(len(path), 0, -1)
            if path[:depth] in assignments and assignments[path[:depth]][1][3].startswith("{")
        ), None)
        if not path:
            lines[0:0] = missing_lines
        elif path in tables:
            insert = tables[path] + 1
            if not lines[insert - 1].endswith("\n"):
                lines[insert - 1] += self.newline
            lines[insert:insert] = missing_lines
        elif inline_parent is not None:
            index, assignment = assignments[inline_parent]
            line = lines[index]
            location = _toml_inline_table_location(line, assignment.end(1), path[len(inline_parent):])
            if location is None:
                raise ValueError(f"{self.path} 的内联表 {'.'.join(path)} 需要手工补齐缺失配置")
            start, end, remaining = location
            if remaining:
                missing = [".".join(remaining) + "." + entry for entry in missing]
            insert = len(line[:end].rstrip(" \t"))
            separator = ", " if line[start + 1:insert].strip() else " "
            padding = " " if insert == end else ""
            lines[index] = line[:insert] + separator + ", ".join(missing) + padding + line[insert:]
        else:
            # 如果已有点分键，沿其所在的显式父表补缺，避免重定义点分键建立的表。
            existing = any(key[:len(path)] == path for key in assignments)
            if existing:
                parent = next((path[:depth] for depth in range(len(path) - 1, 0, -1) if path[:depth] in tables), ())
                insert = tables[parent] + 1 if parent else 0
                if insert and not lines[insert - 1].endswith("\n"):
                    lines[insert - 1] += self.newline
                prefix = ".".join(path[len(parent):]) + "."
                lines[insert:insert] = [prefix + line for line in missing_lines]
            else:
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += self.newline
                if lines and lines[-1].strip():
                    lines.append(self.newline)
                lines += [f"[{'.'.join(path)}]{self.newline}", *missing_lines]
        self._update(lines)

    def set_string(self, key: str, value: str) -> None:
        if key not in self.data:
            self.add_defaults((), {key: _toml_string(value)})
            return
        if self.data[key] == value:
            return
        lines, _, assignments = self._locations()
        if (key,) in assignments:
            index, assignment = assignments[(key,)]
            literal = re.fullmatch(rf"{TOML_STRING}([ \t]*(?:#.*)?)", assignment[3])
            if literal:
                ending = lines[index][len(lines[index].rstrip("\r\n")):]
                lines[index] = assignment[1] + _toml_string(value) + literal[1] + ending
                self._update(lines)
                return
        raise ValueError(f"{self.path} 的 {key} 不是可安全替换的单行字符串，原文件已保留")

    def change(self) -> Optional[FileChange]:
        updated = self.source.encode("utf-8")
        if (self.original or b"").startswith(UTF8_BOM):
            updated = UTF8_BOM + updated
        return FileChange(self.path, self.original, updated) if updated != (self.original or b"") else None

    def set_integer(self, key: str, value: int) -> None:
        if key not in self.data:
            self.add_defaults((), {key: str(value)})
        elif self.data[key] != value:
            lines, _, assignments = self._locations()
            location = assignments.get((key,))
            if location is None or not re.fullmatch(r"[0-9_]+[ \t]*(?:#.*)?", location[1][3]):
                raise ValueError(f"{self.path} 的 {key} 不是可安全替换的非负整数")
            index, assignment = location
            suffix = re.sub(r"^[0-9_]+", "", assignment[3])
            ending = lines[index][len(lines[index].rstrip("\r\n")):]
            lines[index] = assignment[1] + str(value) + suffix + ending
            self._update(lines)


# 三、客户端、WSL 互操作与 Python / Zotero 探测


def _first_executable(candidates: List[Path], *, windows: bool = False) -> Optional[str]:
    for candidate in candidates:
        try:
            if candidate.is_file() and (windows or os.access(candidate, os.X_OK)):
                return str(candidate)
        except OSError:
            continue
    return None


def _windows_app_binaries(home: Path, local_app: Path, name: str) -> Tuple[Optional[str], Optional[str]]:
    if name == "agy":
        cli = [home / ".gemini" / "bin" / "agy.exe", home / ".local" / "bin" / "agy.exe"]
        desktop = [
            local_app / "Programs" / "antigravity" / "Antigravity.exe",
            local_app / "Programs" / "Antigravity IDE" / "Antigravity IDE.exe",
        ]
    elif name == "codex":
        codex_dir = local_app / "OpenAI" / "Codex"
        cli = [home / ".local" / "bin" / "codex.exe", home / ".cargo" / "bin" / "codex.exe"]
        desktop = [codex_dir / "Codex.exe"]
        try:
            if codex_dir.is_dir():
                scored_cx: List[Tuple[int, str, Path]] = []
                for entry in codex_dir.glob("bin/**/codex.exe"):
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        scored_cx.append((st.st_mtime_ns, str(entry), entry))
                scored_cx.sort(key=lambda row: (row[0], row[1]), reverse=True)
                cli += [row[2] for row in scored_cx]
        except OSError:
            pass
    elif name == "claude":
        cli = [home / ".local" / "bin" / f"claude{suffix}" for suffix in (".exe", "")]
        cli += [home / ".npm-global" / "bin" / f"claude{suffix}" for suffix in (".cmd", ".exe")]
        cli += [home / "AppData" / "Roaming" / "npm" / "claude.cmd"]
        desktop = []
    elif name == "cc-switch":
        program_files = Path("/mnt/c/Program Files") if os.name != "nt" else Path(os.environ.get("ProgramFiles") or "C:/Program Files")
        cli = []
        desktop = [
            local_app / "Programs" / "CC Switch" / "cc-switch.exe",
            local_app / "Programs" / "cc-switch" / "cc-switch.exe",
            program_files / "CC Switch" / "cc-switch.exe",
        ]
    elif name == "dsh":
        program_files = Path("/mnt/c/Program Files") if os.name != "nt" else Path(os.environ.get("ProgramFiles") or "C:/Program Files")
        cli = [local_app / "Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd",
               program_files / "DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd"]
        desktop = [
            local_app / "Programs" / "DeepSeek Harness" / "DeepSeek Harness.exe",
            program_files / "DeepSeek Harness" / "DeepSeek Harness.exe",
        ]
    else:
        return None, None
    return _first_executable(cli, windows=True), _first_executable(desktop, windows=True)


def _is_posix_native_cli_path(path_str: Optional[str]) -> bool:
    if not path_str:
        return False
    if _is_mnt_windows_path(path_str) or path_str.lower().endswith((".exe", ".cmd", ".bat")):
        return False
    try:
        resolved = str(Path(path_str).resolve())
    except OSError:
        return False
    if _is_mnt_windows_path(resolved) or resolved.lower().endswith((".exe", ".cmd", ".bat")):
        return False
    return True


def _native_cli(name: str, home: Path) -> Optional[str]:
    if name == "dsh":
        override = os.environ.get("CAE_DSH_BINARY")
        found = (shutil.which(override) or _first_executable([Path(override).expanduser()], windows=os.name == "nt")) if override else shutil.which("dsh")
        if found and (os.name == "nt" or _is_posix_native_cli_path(found)):
            return found
    if os.name == "nt":
        local_app = Path(os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local"))
        local_cli, _ = _windows_app_binaries(home, local_app, name)
        found = local_cli or shutil.which(f"{name}.exe") or shutil.which(name)
        if name == "codex" and found and "/microsoft/windowsapps/" in Path(found).as_posix().lower():
            return None
        return found
    candidates = [home / ".local" / "bin" / name]
    if name in {"agy", "codex"}:
        candidates.insert(0, home / (".gemini" if name == "agy" else ".cargo") / "bin" / name)
    if name == "claude":
        candidates.append(home / ".npm-global" / "bin" / name)
    if name == "dsh":
        candidates.append(Path("/opt/DeepSeek Harness/resources/runtime/cli/bin/dsh"))
    candidates += [Path("/usr/bin") / name, Path("/usr/local/bin") / name]
    found = _first_executable(candidates) or shutil.which(name)
    return found if _is_posix_native_cli_path(found) else None


def _is_windows() -> bool:
    return os.name == "nt"


def _newer_executable(candidate: str, current: str) -> bool:
    try:
        cand_st = Path(candidate).stat()
        curr_st = Path(current).stat()
    except OSError:
        return False
    return (cand_st.st_mtime_ns, candidate) > (curr_st.st_mtime_ns, current)


def _find_windows_codex_package() -> Tuple[Optional[str], Optional[str]]:
    """通过注册表只读读取当前用户的 Codex 应用包位置，不启动外部进程且零磁盘写入。"""
    if not _is_windows():
        return None, None
    try:
        import winreg

        key_path = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
        locations: List[Tuple[Tuple[int, ...], str]] = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as root_key:
            count = winreg.QueryInfoKey(root_key)[0]
            for idx in range(count):
                try:
                    sub_name = winreg.EnumKey(root_key, idx)
                except OSError:
                    continue
                if not sub_name.lower().startswith("openai.codex_"):
                    continue
                try:
                    with winreg.OpenKey(root_key, sub_name) as sub_key:
                        loc, _ = winreg.QueryValueEx(sub_key, "PackageRootFolder")
                except OSError:
                    continue
                if isinstance(loc, str) and loc.strip():
                    parts = sub_name.split("_")
                    ver = tuple(int(part) for part in parts[1].split(".") if part.isdigit()) if len(parts) > 1 else ()
                    locations.append((ver, loc.strip()))
        for _, location in sorted(locations, reverse=True):
            app = Path(location) / "app"
            cli = _first_executable([app / "resources" / "codex.exe"], windows=True)
            desktop = _first_executable([app / "Codex.exe"], windows=True)
            if cli or desktop:
                return cli, desktop
    except (ImportError, OSError, ValueError):
        pass
    return None, None


def _decode_wsl_reg_output(raw: bytes, reg_exe: str, reg_cwd: Optional[str]) -> Optional[str]:
    if raw.isascii():
        return raw.decode("ascii")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for value_name in ("OEMCP", "ACP"):
        try:
            cp_proc = subprocess.run(
                [reg_exe, "query", r"HKLM\SYSTEM\CurrentControlSet\Control\Nls\CodePage", "/v", value_name],
                cwd=reg_cwd,
                capture_output=True,
                timeout=5,
            )
            if cp_proc.returncode == 0:
                cp_match = re.search(rf"{value_name}\s+REG_SZ\s+([0-9]+)".encode("ascii"), cp_proc.stdout)
                if cp_match:
                    cp_num = cp_match.group(1).decode("ascii")
                    return raw.decode("utf-8" if cp_num == "65001" else f"cp{cp_num}")
        except (LookupError, OSError, subprocess.SubprocessError, UnicodeDecodeError):
            continue
    return None


def _find_wsl_windows_codex_package() -> Tuple[Optional[str], Optional[str]]:
    """在 WSL2 中通过原生 reg.exe 只读读取 Windows Codex MSIX 应用包位置，零 PowerShell 缓存写入。"""
    if not _is_wsl():
        return None, None
    reg_exe = shutil.which("reg.exe")
    wslpath = shutil.which("wslpath")
    if not (reg_exe and wslpath):
        return None, None
    key_path = r"HKCU\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
    try:
        reg_parent = Path(reg_exe).parent
        reg_cwd = str(reg_parent) if reg_parent.is_dir() else None
        proc = subprocess.run(
            [reg_exe, "query", key_path, "/s", "/v", "PackageRootFolder"],
            cwd=reg_cwd,
            capture_output=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return None, None
        text = _decode_wsl_reg_output(proc.stdout, reg_exe, reg_cwd)
        if not text:
            return None, None
        locations: List[Tuple[Tuple[int, ...], str]] = []
        current_sub = ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.upper().startswith("HKEY_CURRENT_USER\\"):
                current_sub = line.rsplit("\\", 1)[-1].strip()
            elif current_sub.lower().startswith("openai.codex_") and "REG_SZ" in line and "PackageRootFolder" in line:
                loc = line.split("REG_SZ", 1)[1].strip()
                if loc:
                    parts = current_sub.split("_")
                    ver = tuple(int(part) for part in parts[1].split(".") if part.isdigit()) if len(parts) > 1 else ()
                    locations.append((ver, loc))
        for _, win_loc in sorted(locations, reverse=True):
            converted = subprocess.run(
                [wslpath, "-u", win_loc],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
            )
            wsl_loc = converted.stdout.strip()
            if converted.returncode != 0 or not wsl_loc:
                continue
            app = Path(wsl_loc) / "app"
            cli = _first_executable([app / "resources" / "codex.exe"], windows=True)
            desktop = _first_executable([app / "Codex.exe"], windows=True)
            if cli or desktop:
                return cli, desktop
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None, None


def _probe_wsl_windows_profile_dir() -> Optional[Path]:
    """以 UTF-16 读取 Windows 用户主目录，再用 wslpath 转为本侧路径。"""
    if not _is_wsl():
        return None
    cmd_exe = shutil.which("cmd.exe")
    wslpath = shutil.which("wslpath")
    if not cmd_exe or not wslpath:
        return None
    try:
        profile = subprocess.run(
            [cmd_exe, "/d", "/u", "/c", "set", "USERPROFILE"],
            cwd=str(Path(cmd_exe).parent),
            capture_output=True,
            text=True,
            encoding="utf-16-le",
            timeout=5,
        )
        raw_out = profile.stdout.strip().lstrip("\ufeff")
        win_profile = raw_out
        for line in raw_out.splitlines():
            clean_line = line.strip()
            if clean_line.upper().startswith("USERPROFILE="):
                win_profile = clean_line.split("=", 1)[1].strip()
                break
        if profile.returncode != 0 or not PureWindowsPath(win_profile).is_absolute():
            return None
        converted = subprocess.run(
            [wslpath, "-u", win_profile],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        wsl_home = converted.stdout.strip()
        if converted.returncode != 0 or not wsl_home:
            return None
        profile_path = Path(wsl_home)
        return profile_path if profile_path.is_absolute() and profile_path.is_dir() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _wsl_distro_list(wsl: str, *options: str) -> Optional[List[str]]:
    try:
        result = subprocess.run([wsl, "--list", *options, "--quiet"], capture_output=True, timeout=5)
        if result.returncode != 0:
            return None
        # 不同版本的 wsl.exe 重定向输出可能是 UTF-16 或 UTF-8。
        if result.stdout.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        else:
            encoding = "utf-16-le" if b"\0" in result.stdout else "utf-8-sig"
        names = [line.strip().lstrip("\ufeff").strip() for line in result.stdout.decode(encoding).splitlines()]
        return [name for name in names if name]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _probe_windows_wsl() -> Dict[str, Any]:
    """只检查已运行发行版，避免只读探测启动已停止的 WSL 环境。"""
    result: Dict[str, Any] = {"status": "unknown", "installed_distros": [], "running_distros": [], "apps": {}}
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return result
    installed = _wsl_distro_list(wsl)
    running = _wsl_distro_list(wsl, "--running")
    result["installed_distros"] = installed or []
    result["running_distros"] = running or []
    if installed is None or running is None:
        return result

    # --exec 不加载交互配置，不运行被探测的 CLI。
    script = (
        'is_native() { '
        '  local p="$1" r; [ -n "$p" ] && [ -f "$p" ] && [ -x "$p" ] || return 1; '
        '  r=$(readlink -f "$p" 2>/dev/null || printf "%s" "$p"); '
        '  case "$r" in /*) case "$r" in /mnt/[a-zA-Z]/*|*.[eE][xX][eE]|*.[cC][mM][dD]|*.[bB][aA][tT]) return 1;; esac;; *) return 1;; esac; '
        '  return 0; '
        '}; '
        'for name in agy codex claude dsh cc-switch; do '
        '  bin=; '
        '  for candidate in "$HOME/.gemini/bin/$name" "$HOME/.local/bin/$name" "$HOME/.cargo/bin/$name" '
        '    "$HOME/.npm-global/bin/$name" "/usr/local/bin/$name" "/usr/bin/$name" '
        '    "/opt/DeepSeek Harness/resources/runtime/cli/bin/$name" "$(command -v "$name" 2>/dev/null)"; do '
        '    if is_native "$candidate"; then bin=$candidate; break; fi; '
        '  done; '
        '  desk=; '
        '  if [ "$name" = agy ]; then '
        '    for dcand in "$HOME/.local/bin/antigravity" "/usr/local/bin/antigravity" "/usr/bin/antigravity" '
        '      "/usr/share/antigravity/antigravity" "/opt/antigravity/antigravity" "/opt/Antigravity/antigravity"; do '
        '      if is_native "$dcand"; then desk=$dcand; break; fi; '
        '    done; '
        '  fi; '
        '  trace=false; '
        '  if [ "$name" = agy ]; then '
        '    [ -d "$HOME/.gemini/config" ] || [ -d "$HOME/.gemini/antigravity-cli" ] '
        '      || [ -d "$HOME/.gemini/antigravity" ] || [ -d "$HOME/.config/Antigravity" ]; '
        '  elif [ "$name" = codex ]; then [ -d "$HOME/.codex" ]; '
        '  elif [ "$name" = claude ]; then [ -d "$HOME/.claude" ]; '
        '  elif [ "$name" = dsh ]; then [ -d "$HOME/.dsh" ]; '
        '  else [ -f "$HOME/.cc-switch/cc-switch.db" ]; fi && trace=true; '
        '  printf "%s\\t%s\\t%s\\t%s\\n" "$name" "$bin" "$trace" "$desk"; '
        'done'
    )
    complete = set(installed) == set(running)
    for distro in running:
        try:
            probe = subprocess.run(
                [wsl, "--distribution", distro, "--exec", "/bin/sh", "-c", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
            )
            if probe.returncode != 0:
                complete = False
                continue
            for line in probe.stdout.splitlines():
                fields = line.split("\t")
                if len(fields) in (3, 4) and fields[0] in {"agy", "codex", "claude", "dsh", "cc-switch"}:
                    name, binary, trace = fields[0], fields[1], fields[2]
                    desktop_bin = fields[3] if len(fields) == 4 else ""
                    entry: Dict[str, Any] = {
                        "distro": distro,
                        "binary": binary or None,
                        "config_dir_exists": trace == "true",
                    }
                    if desktop_bin:
                        entry["desktop_binary"] = desktop_bin
                    result["apps"].setdefault(name, []).append(entry)
            if any(not any(item["distro"] == distro for item in result["apps"].get(name, [])) for name in ("agy", "codex", "claude", "dsh", "cc-switch")):
                complete = False
        except (OSError, ValueError, subprocess.SubprocessError):
            complete = False
    result["status"] = "checked" if complete else "unknown"
    return result


def _probe_dsh_hook_capability(dsh_bin: Optional[str]) -> Dict[str, Any]:
    """探测 DSH CLI 是否已原生支持生命周期硬钩子，用于触发后续元更新。"""
    info: Dict[str, Any] = {
        "hook_protocol": "soft_rules_only",
        "hard_hooks_supported": False,
        "meta_update_trigger": "awaiting_upstream_dsh_hooks",
    }
    if not dsh_bin:
        return info
    try:
        cmd = (
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", dsh_bin, "--help"]
            if os.name == "nt" and Path(dsh_bin).suffix.lower() in {".cmd", ".bat"}
            else [dsh_bin, "--help"]
        )
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3,
        )
        help_txt = proc.stdout or ""
        rc = getattr(proc, "returncode", 0)
        rc_ok = not (isinstance(rc, int) and not isinstance(rc, bool) and rc != 0)
        if rc_ok and any(tok in help_txt for tok in ("PreToolUse", "SessionStart", "hooks.json", "--hooks")):
            info["hook_protocol"] = "lifecycle_hooks"
            info["hard_hooks_supported"] = True
            info["meta_update_trigger"] = "ready_for_fleet_evolve_dsh_hooks"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return info


def detect_installed_apps() -> Dict[str, Any]:
    """
    只读探测四端客户端与 CC Switch（区分本侧原生与跨子系统互操作）。
    """
    home = Path.home()
    gemini_root = home / ".gemini"
    configured_codex = os.environ.get("CODEX_HOME", "").strip()
    codex_root = Path(configured_codex).expanduser() if configured_codex else (home / ".codex")
    claude_root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
    dsh_root = Path(os.environ.get("DSH_HOME") or home / ".dsh").expanduser()
    cc_db = home / ".cc-switch" / "cc-switch.db"

    wsl = _is_wsl()
    windows_home = _probe_wsl_windows_profile_dir() if wsl else None
    wsl_probe = _probe_windows_wsl() if os.name == "nt" else None

    def _prefer_codex_package_binaries(
        cur_cli: Optional[str],
        cur_desktop: Optional[str],
        pkg_cli: Optional[str],
        pkg_desktop: Optional[str],
        user_home: Optional[Path],
    ) -> Tuple[Optional[str], Optional[str]]:
        standalone_bins = set()
        if user_home is not None:
            for user_bin in (user_home / ".local" / "bin" / "codex.exe", user_home / ".cargo" / "bin" / "codex.exe"):
                try:
                    standalone_bins.add(str(user_bin.resolve()).lower())
                except OSError:
                    standalone_bins.add(str(user_bin).lower())
        cli_norm = ""
        if cur_cli:
            try:
                cli_norm = str(Path(cur_cli).resolve()).lower()
            except OSError:
                cli_norm = cur_cli.lower()
        if pkg_cli and (not cur_cli or (cli_norm not in standalone_bins and _newer_executable(pkg_cli, cur_cli))):
            cur_cli = pkg_cli
        if pkg_desktop and (not cur_desktop or _newer_executable(pkg_desktop, cur_desktop)):
            cur_desktop = pkg_desktop
        return cur_cli, cur_desktop

    def app_info(name: str, config_dir_exists: bool) -> Dict[str, Any]:
        cli = _native_cli(name, home)
        desktop = None
        if os.name == "nt":
            local_app = Path(os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local"))
            _, desktop = _windows_app_binaries(home, local_app, name)
            if name == "codex":
                pkg_cli, pkg_desktop = _find_windows_codex_package()
                cli, desktop = _prefer_codex_package_binaries(cli, desktop, pkg_cli, pkg_desktop, home)
        elif name == "agy":
            cand_desk = _first_executable([
                home / ".local/bin/antigravity",
                Path("/usr/local/bin/antigravity"),
                Path("/usr/bin/antigravity"),
                Path("/usr/share/antigravity/antigravity"),
                Path("/opt/antigravity/antigravity"),
                Path("/opt/Antigravity/antigravity"),
            ])
            desktop = cand_desk if _is_posix_native_cli_path(cand_desk) else None

        evidence = []
        interop_complete = not wsl
        if wsl:
            found = shutil.which(name)
            win_cli = shutil.which(f"{name}.exe") or (found if _is_mnt_windows_path(found) else None)
            if name == "codex" and win_cli and "/microsoft/windowsapps/" in Path(win_cli).as_posix().lower():
                win_cli = None
            win_desktop = None
            trace = False
            if windows_home is not None:
                interop_complete = True
                known_cli, win_desktop = _windows_app_binaries(windows_home, windows_home / "AppData" / "Local", name)
                win_cli = win_cli or known_cli
                config_name = {"agy": ".gemini", "codex": ".codex", "claude": ".claude", "dsh": ".dsh", "cc-switch": ".cc-switch"}[name]
                trace = (windows_home / config_name).is_dir()
            if name == "codex":
                pkg_cli, pkg_desktop = _find_wsl_windows_codex_package()
                win_cli, win_desktop = _prefer_codex_package_binaries(win_cli, win_desktop, pkg_cli, pkg_desktop, windows_home)
            evidence.append({"binary": win_cli, "desktop_binary": win_desktop, "config_dir_exists": trace})
        elif wsl_probe is not None:
            evidence = wsl_probe["apps"].get(name, [])
            interop_complete = wsl_probe["status"] == "checked"
        executable = bool(cli or desktop)
        interop_available = any(item.get("binary") or item.get("desktop_binary") for item in evidence)
        return {
            "native_installed": executable,
            "native_binary": cli,
            "native_desktop_binary": desktop,
            "native_config_dir_exists": config_dir_exists,
            "native_status": "executable_detected" if executable else ("config_only" if config_dir_exists else "not_detected"),
            "interop_available": interop_available,
            "interop_status": "executable_detected" if interop_available else ("not_detected" if interop_complete else "unknown"),
            "interop_evidence": evidence,
            "authentication_status": "not_checked",
        }

    ag = app_info(
        "agy",
        any((gemini_root / sub).is_dir() for sub in ("antigravity", "antigravity-cli", "config"))
        or (os.name != "nt" and (home / ".config" / "Antigravity").is_dir()),
    )
    cx = app_info("codex", codex_root.is_dir())
    cl = app_info("claude", claude_root.is_dir())
    dsh = app_info("dsh", dsh_root.is_dir())
    cc = app_info("cc-switch", cc_db.is_file())
    ag["target_plugin_dir"] = str(gemini_root / "config" / "plugins" / "user-guard")
    cx["target_codex_home"] = str(codex_root)
    cl["target_claude_home"] = str(claude_root)
    dsh["target_dsh_home"] = str(dsh_root)
    dsh.update(_probe_dsh_hook_capability(dsh.get("native_binary")))
    cc.update(db_path=str(cc_db), db_exists=cc_db.is_file())
    ag_native, cx_native = ag["native_installed"], cx["native_installed"]
    cl_native = cl["native_installed"]

    if dsh["native_installed"] and (ag_native or cx_native or cl_native):
        recommended = "all"
    elif cl_native and (ag_native or cx_native):
        recommended = "all"
    elif ag_native and cx_native:
        recommended = "both"
    elif ag_native:
        recommended = "antigravity"
    elif cx_native:
        recommended = "codex"
    elif cl_native:
        recommended = "claude"
    elif dsh["native_installed"]:
        recommended = "dsh"
    else:
        recommended = "none"

    zotero_exe = detect_zotero_mcp_executable()

    return {
        "platform": sys.platform,
        "os_name": os.name,
        "is_wsl": wsl,
        "wsl_probe": wsl_probe,
        "antigravity": ag,
        "codex": cx,
        "claude": cl,
        "dsh": dsh,
        "cc_switch": cc,
        "zotero_mcp_detected": zotero_exe,
        "recommended_app_mode": recommended,
        "single_app_only_on_host": (
            sum(bool(info["native_installed"]) for info in (ag, cx, cl, dsh)) == 1
            and all(info["native_installed"] or info["interop_status"] == "not_detected" for info in (ag, cx, cl, dsh))
        ),
    }


def _hook_python_candidates() -> List[Tuple[str, str, List[str]]]:
    """从安装器自己的 PATH 和当前解释器发现候选，不读取钩子注册表。"""
    candidates: List[Tuple[str, str, List[str]]] = []
    path_dirs = []
    for entry in os.get_exec_path():
        directory = Path(entry.strip().strip('"') if os.name == "nt" else entry)
        if directory.is_absolute():
            path_dirs.append(directory)
    if os.name == "nt":
        seen_py: Set[str] = set()
        for directory in path_dirs:
            path = directory / "python.exe"
            if not any(part.lower() == "windowsapps" for part in path.parts) and path.is_file():
                norm_path = str(path).lower()
                if norm_path not in seen_py:
                    seen_py.add(norm_path)
                    candidates.append(("python.exe", str(path), []))
        seen_launcher: Set[str] = set()
        for directory in path_dirs:
            path = directory / "py.exe"
            if not any(part.lower() == "windowsapps" for part in path.parts) and path.is_file():
                norm_path = str(path).lower()
                if norm_path not in seen_launcher:
                    seen_launcher.add(norm_path)
                    for launcher_args in PY_LAUNCHER_ARGS:
                        candidates.append(("py.exe", str(path), list(launcher_args)))
    else:
        posix_dirs = [directory for directory in path_dirs if not _is_mnt_windows_path(str(directory))]
        seen_posix: Set[Tuple[str, str]] = set()
        for name in POSIX_HOOK_PYTHON_NAMES:
            for directory in posix_dirs:
                hit = shutil.which(name, path=str(directory))
                if hit and (name, hit) not in seen_posix and _is_posix_native_cli_path(hit):
                    seen_posix.add((name, hit))
                    candidates.append((name, hit, []))
    if sys.executable and (os.name == "nt" or _is_posix_native_cli_path(sys.executable)):
        candidates.append(("", sys.executable, []))
    return candidates


def _is_working_python_cmd(executable: str, launcher_args: List[str]) -> bool:
    """只执行安装器发现的解释器，并只传入固定版本探测参数。"""
    if not Path(executable).is_absolute() or launcher_args not in ([], *PY_LAUNCHER_ARGS):
        return False
    if os.name != "nt" and not _is_posix_native_cli_path(executable):
        return False
    try:
        proc = subprocess.run(
            [executable, *launcher_args, *HOOK_PYTHON_FLAGS,
             "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def detect_hook_python_command(*, antigravity: bool = False) -> str:
    """生成宿主钩子可调用的 Python 命令。Windows 反重力要求 PATH 前缀。"""
    flags = " ".join(HOOK_PYTHON_FLAGS)
    for name, executable, launcher_args in _hook_python_candidates():
        if not name:
            continue
        shell_path = shutil.which(name)
        if (
            shell_path
            and (os.name == "nt" or _is_posix_native_cli_path(shell_path))
            and _same_nonlink_path(Path(shell_path), Path(executable))
            and _is_working_python_cmd(executable, launcher_args)
        ):
            return " ".join([name, *launcher_args, flags])
    if antigravity and os.name == "nt":
        raise ValueError("Windows 反重力钩子需要 PATH 中可执行的 Python 3.11+（python.exe 或 py.exe -3 / -3.14..-3.11）。")
    executable = f'"{sys.executable}"' if os.name == "nt" else shlex.quote(sys.executable)
    return f"{executable} {flags}"


def _is_usable_zotero_binary(path_str: Optional[str]) -> bool:
    return bool(path_str) and (os.name == "nt" or _is_posix_native_cli_path(path_str))


def detect_zotero_mcp_executable(explicit_cmd: str = "") -> Optional[str]:
    """动态探测本机是否存在可用的 zotero-mcp 可执行文件（零个人路径硬编码）。"""
    def _which_usable(cmd_name: str) -> Optional[str]:
        hit = shutil.which(cmd_name)
        if _is_usable_zotero_binary(hit):
            return hit
        if hit and os.name != "nt":
            for part in os.environ.get("PATH", "").split(os.pathsep):
                if not part or _is_mnt_windows_path(part):
                    continue
                cand = Path(part) / cmd_name
                if cand.is_file() and _is_usable_zotero_binary(str(cand)) and os.access(cand, os.X_OK):
                    return str(cand)
        return None

    if explicit_cmd:
        explicit_path = Path(explicit_cmd).expanduser()
        if explicit_path.is_file() and _is_usable_zotero_binary(str(explicit_path)):
            return str(explicit_path.resolve())
        return _which_usable(explicit_cmd)

    in_path = _which_usable("zotero-mcp")
    if in_path:
        return in_path

    home = Path.home()
    exe_names = ["zotero-mcp.exe", "zotero-mcp"] if os.name == "nt" else ["zotero-mcp"]
    venv_subdirs = ("Scripts", "bin") if os.name == "nt" else ("bin",)

    # 1. 扫描 ~/.local/share/codex-tools/zotero-mcp-* 虚拟环境
    codex_tools_dir = home / ".local" / "share" / "codex-tools"
    candidates: List[Path] = []
    try:
        for venv_dir in sorted(codex_tools_dir.glob("zotero-mcp*"), reverse=True):
            candidates.extend(venv_dir / ".venv" / subdir / name
                              for subdir in venv_subdirs for name in exe_names)
    except OSError:
        pass

    # 2. 扫描常见 pipx / uv / user local bin 路径
    common_bins = [
        home / ".local" / "bin",
        home / "pipx" / "venvs" / "zotero-mcp" / ("Scripts" if os.name == "nt" else "bin"),
        home / ".local" / "pipx" / "venvs" / "zotero-mcp" / ("Scripts" if os.name == "nt" else "bin"),
    ]
    candidates.extend(directory / name for directory in common_bins for name in exe_names)
    usable_candidates = [item for item in candidates if _is_usable_zotero_binary(str(item))]
    found = _first_executable(usable_candidates, windows=os.name == "nt")
    if _is_usable_zotero_binary(found):
        return str(Path(found).resolve())

    # 3. 兼容从本机 ~/.codex/config.toml 中读取已验证存在的 zotero 路径
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    codex_root = Path(configured_home).expanduser() if configured_home else home / ".codex"
    codex_cfg = codex_root / "config.toml"
    if codex_cfg.is_file():
        try:
            raw_codex_cfg = _read_optional_bytes(codex_cfg, allow_hardlinks=True)
            if raw_codex_cfg is None:
                return None
            cfg = _parse_toml(raw_codex_cfg.decode("utf-8-sig"), codex_cfg)
            command = cfg.get("mcp_servers", {}).get("zotero", {}).get("command")
            if isinstance(command, str):
                candidate = Path(command).expanduser()
                if (
                    candidate.is_absolute()
                    and _is_usable_zotero_binary(str(candidate))
                    and _first_executable([candidate], windows=os.name == "nt")
                ):
                    return str(candidate.resolve())
        except (AttributeError, OSError, ValueError, RuntimeError):
            pass

    return None


# 四、反重力插件镜像、MCP 与默认模型同步


def should_ignore_template_item(path: Path) -> bool:
    if len(path.parts) >= 2 and path.parts[0] == "hooks" and path.parts[1] in ("state", "logs"):
        return True
    return any(part in IGNORED_NAMES for part in path.parts) or path.suffix.lower() in IGNORED_SUFFIXES


def _template_files(template_dir: Path) -> List[Path]:
    return [
        item for item in sorted(template_dir.rglob("*"))
        if not should_ignore_template_item(item.relative_to(template_dir)) and item.is_file()
    ]


def _is_valid_python_exe_name(exe_name: str) -> bool:
    low = exe_name.lower()
    if low in {"py", "py.exe", "python", "python.exe", "python3", "python3.exe", "python3t", "python3t.exe"}:
        return True
    if re.fullmatch(r"python[4-9]\d*t?(?:\.exe)?", low):
        return True
    match = re.fullmatch(r"python(\d+)\.(\d+)(?:\.\d+)*t?(?:\.exe)?", low)
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return major > 3 or (major == 3 and minor >= 11)


def _is_valid_hook_python_prefix(prefix: str) -> bool:
    try:
        raw_tokens = shlex.split(prefix, posix=(os.name != "nt"))
    except ValueError:
        return False
    tokens = [tok.strip('"') for tok in raw_tokens] if os.name == "nt" else raw_tokens
    if not tokens or not tokens[0] or tokens[0].endswith(("/", "\\")):
        return False
    exe_token = tokens[0]
    has_sep = "/" in exe_token or "\\" in exe_token or PureWindowsPath(exe_token).is_absolute()
    if os.name == "nt":
        if has_sep:
            return False
    elif has_sep:
        if (
            "\\" in exe_token
            or not Path(exe_token).is_absolute()
            or not _is_posix_native_cli_path(exe_token)
            or not Path(exe_token).is_file()
        ):
            return False
    else:
        resolved_cmd = shutil.which(exe_token)
        if resolved_cmd and not _is_posix_native_cli_path(resolved_cmd):
            return False
    exe_name = PureWindowsPath(exe_token).name.lower()
    if not _is_valid_python_exe_name(exe_name):
        return False
    if exe_name in {"py", "py.exe"}:
        return any(tokens[1:] == [*launcher_args, *HOOK_PYTHON_FLAGS] for launcher_args in PY_LAUNCHER_ARGS)
    return tokens[1:] == HOOK_PYTHON_FLAGS


_PRESERVED_HOOK_RUNTIME_DIRS = frozenset({
    Path("hooks/state"),
    Path("hooks/logs"),
    Path("hooks/state/handoff"),
    Path("hooks/state/session-context"),
    Path("hooks/state/user-words"),
})


def _is_preserved_mirror_dir(template_dir: Path, rel: Path) -> bool:
    if (template_dir / rel).is_dir() and not should_ignore_template_item(rel):
        return True
    return (template_dir / "hooks").is_dir() and rel in _PRESERVED_HOOK_RUNTIME_DIRS


def _stale_mirror_files(
    template_dir: Path,
    target_dir: Path,
    expected_rel_files: Optional[Set[Path]] = None,
) -> List[Path]:
    """按 mirror_sync_plugin 同源排除规则扫描 target_dir 中多出的陈旧文件、软链与空目录（排除运行态日志/状态/__pycache__）。"""
    if not target_dir.exists() or _is_link_or_junction(target_dir) or not target_dir.is_dir():
        return []
    if expected_rel_files is None:
        expected_rel_files = {item.relative_to(template_dir) for item in _template_files(template_dir)}
    stale_files: List[Path] = []
    for parent, dirs, files in os.walk(target_dir, followlinks=False):
        parent_path = Path(parent)
        kept_dirs: List[str] = []
        for dname in sorted(dirs):
            dpath = parent_path / dname
            if _is_link_or_junction(dpath):
                stale_files.append(dpath)
                continue
            if dname == "__pycache__":
                continue
            kept_dirs.append(dname)
        dirs[:] = kept_dirs
        for fname in sorted(files):
            existing = parent_path / fname
            if _is_link_or_junction(existing):
                stale_files.append(existing)
                continue
            rel = existing.relative_to(target_dir)
            if "__pycache__" in rel.parts:
                continue
            if len(rel.parts) >= 2 and rel.parts[0] == "hooks" and rel.parts[1] in ("state", "logs"):
                if (
                    fname == ".gitkeep"
                    or fname.endswith("_stop_gate.json")
                    or (fname.startswith("session-") and fname.endswith(".json.lock"))
                ):
                    stale_files.append(existing)
                continue
            if rel not in expected_rel_files:
                stale_files.append(existing)
    stale_empty_dirs: Set[Path] = set()
    for parent, _, _ in os.walk(target_dir, topdown=False, followlinks=False):
        directory = Path(parent)
        if directory == target_dir or _is_link_or_junction(directory):
            continue
        rel = directory.relative_to(target_dir)
        if "__pycache__" in rel.parts or _is_preserved_mirror_dir(template_dir, rel):
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        if all(entry in stale_empty_dirs for entry in entries):
            stale_empty_dirs.add(directory)
            stale_files.append(directory)
    return sorted(set(stale_files))


def _validate_mirror(template_dir: Path, target_dir: Path) -> None:
    """写入和清理共用一次完整校验，拒绝目录重叠及源、目标树中的链接。"""
    _validate_path(template_dir, directory=True)
    if not template_dir.is_dir():
        raise ValueError(f"找不到安装模板目录 {template_dir}")
    _validate_path(target_dir, directory=True)
    source, target = template_dir.resolve(), target_dir.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError(f"安装目标与模板目录重叠: {target_dir}")
    _validate_tree_links(template_dir)
    _validate_tree_links(target_dir)
    for item in _template_files(template_dir):
        _validate_path(target_dir / item.relative_to(template_dir), directory=False)


def mirror_sync_plugin(
    template_dir: Path, target_plugin_dir: Path, dry_run: bool = False, *, hooks_bytes: Optional[bytes] = None,
) -> None:
    """
    将 template_dir 镜像同步至 target_plugin_dir，并精准清理陈旧废弃文件与 __pycache__。
    保留运行态历史归档（如 hooks/state/*_user_words.md 与 hooks/logs/lifecycle.log），但清除废弃 *_stop_gate.json。
    """
    _validate_mirror(template_dir, target_plugin_dir)
    files = _template_files(template_dir)
    expected_rel_files = {item.relative_to(template_dir) for item in files}
    pending_updates: List[Tuple[Path, FileChange]] = []
    for src in files:
        rel = src.relative_to(template_dir)
        dst = target_plugin_dir / rel
        original = _read_optional_bytes(dst)
        content = hooks_bytes if hooks_bytes is not None and rel == Path("hooks.json") else src.read_bytes()
        if original != content:
            pending_updates.append((src, FileChange(dst, original, content)))

    stale_files: List[Path] = []
    stale_dirs: List[Path] = []
    if target_plugin_dir.exists():
        stale_files = _stale_mirror_files(template_dir, target_plugin_dir, expected_rel_files)
        for existing in sorted(target_plugin_dir.rglob("*")):
            if existing.is_dir():
                rel = existing.relative_to(target_plugin_dir)
                if existing.name == "__pycache__" and not any(parent.name == "__pycache__" for parent in rel.parents):
                    stale_dirs.append(existing)
        for stale_file in stale_files:
            if stale_file.is_dir() and not _is_link_or_junction(stale_file):
                _validate_path(stale_file, directory=True)
            else:
                _validate_path(stale_file, directory=False)
        for stale_dir in stale_dirs:
            _validate_path(stale_dir, directory=True)
            _validate_tree_links(stale_dir)

    if dry_run:
        if pending_updates:
            print(f"[Dry-Run] 将同步 {len(pending_updates)} 个变更文件至: {target_plugin_dir}")
            for source, change in pending_updates:
                print(f"  [SYNC] {change.path.relative_to(target_plugin_dir)}")
        else:
            print(f"[*] 目录已是最新状态，无需写入: {target_plugin_dir}")
    else:
        target_plugin_dir.mkdir(parents=True, exist_ok=True)
        for source, change in pending_updates:
            _atomic_write(change)
            if change.original is None:
                change.path.chmod(stat.S_IMODE(source.stat().st_mode))

    if not target_plugin_dir.exists():
        return

    if not dry_run:
        stale_files = _stale_mirror_files(template_dir, target_plugin_dir, expected_rel_files)
        stale_dirs = []
        for existing in sorted(target_plugin_dir.rglob("*")):
            if existing.is_dir():
                rel = existing.relative_to(target_plugin_dir)
                if existing.name == "__pycache__" and not any(parent.name == "__pycache__" for parent in rel.parents):
                    stale_dirs.append(existing)

    for stale_file in stale_files:
        rel = stale_file.relative_to(target_plugin_dir)
        if stale_file.is_dir() and not _is_link_or_junction(stale_file):
            if dry_run:
                print(f"  [Dry-Run CLEAN] 将清理过时空目录: {rel}")
            continue
        if dry_run:
            print(f"  [Dry-Run CLEAN] 将清理过时文件: {rel}")
        else:
            _validate_path(stale_file, directory=False)
            stale_file.unlink(missing_ok=True)
            print(f"  [-] 已清理过时残留文件: {rel}")

    for stale_dir in stale_dirs:
        rel = stale_dir.relative_to(target_plugin_dir)
        if dry_run:
            print(f"  [Dry-Run CLEAN] 将清理缓存目录: {rel}")
        else:
            _validate_path(stale_dir, directory=True)
            _validate_tree_links(stale_dir)
            shutil.rmtree(stale_dir)
            print(f"  [-] 已清理字节码缓存目录: {rel}")

    if not dry_run:
        for parent, _, _ in os.walk(target_plugin_dir, topdown=False, followlinks=False):
            directory = Path(parent)
            rel = directory.relative_to(target_plugin_dir)
            if directory == target_plugin_dir or _is_preserved_mirror_dir(template_dir, rel):
                continue
            _validate_path(directory, directory=True)
            if not any(directory.iterdir()):
                directory.rmdir()
                print(f"  [-] 已清理过时空目录: {rel}")


def _clean_legacy_session_lock_files(state_root: Path, dry_run: bool = False) -> None:
    """清理状态目录下早期版本遗留的 session-*.json.lock 废弃锁文件，保留真实归档数据。"""
    if not state_root.exists():
        return
    _validate_tree_links(state_root)
    for stale_lock in sorted(state_root.rglob("session-*.json.lock")):
        if not stale_lock.is_file():
            continue
        if dry_run:
            print(f"  [Dry-Run CLEAN] 将清理过时锁文件: {stale_lock}")
        else:
            _validate_path(stale_lock, directory=False)
            stale_lock.unlink(missing_ok=True)
            print(f"  [-] 已清理过时锁文件: {stale_lock}")


def _plan_user_zotero(config_root: Path, zotero_exe: Optional[str]) -> Optional[FileChange]:
    """仅补齐缺失的 Zotero 条目，已有远程或本地连接沿用用户配置。"""
    path = config_root / "mcp_config.json"
    _validate_path(path, directory=False)
    if not zotero_exe:
        return None
    raw = _read_optional_bytes(path)
    data = _read_json_object(path, raw)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict) or any(not isinstance(server, dict) for server in servers.values()):
        raise ValueError(f"{path} 的 mcpServers 及各服务器条目必须为对象，原文件已保留。")
    if servers.get("zotero"):
        print("[*] 保留反重力已配置的 Zotero MCP。")
        return None
    servers["zotero"] = {"command": zotero_exe, "args": [], "env": ZOTERO_ENV}
    return FileChange(path, raw, _preserve_text_format(_json_bytes(data), raw))


def _parse_agy_model_lines(output: str) -> List[Dict[str, str]]:
    parsed: List[Dict[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or "\t" not in line:
            continue
        slug, display_name = line.split("\t", 1)
        slug, display_name = slug.strip(), display_name.strip()
        if slug and display_name:
            parsed.append({"slug": slug, "display_name": display_name})
    return parsed


def _agy_slug_tokens(slug: str) -> Set[str]:
    return set(re.split(r"[^a-z0-9]+", slug.lower()))


def _filter_agy_catalog_models(
    models: List[Dict[str, Any]], agy_rules: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    blocked_tokens = {
        str(tok).lower()
        for tok in (agy_rules.get("blocked_auto_tokens") or ("orchestrator", "router", "claude", "sonnet", "opus", "haiku", "gpt", "oss"))
    }
    light_tokens = {
        str(tok).lower()
        for tok in (agy_rules.get("fast_tags") or ("lite", "mini", "fast", "flash"))
    }
    eligible = [
        item for item in models
        if not item["slug"].lower().endswith("-ultra") and not (_agy_slug_tokens(item["slug"]) & blocked_tokens)
    ]
    return eligible, light_tokens


def _select_agy_frontier_max_label(
    models: List[Dict[str, str]], repo_root: Optional[Path] = None,
) -> Optional[str]:
    agy_rules = _load_fleet_profile(repo_root).get("client_catalog_rules", {}).get("agy", {})
    family_priority = [str(tok).lower() for tok in (agy_rules.get("family_priority") or ())]
    _tokens = _agy_slug_tokens
    eligible, light_tokens = _filter_agy_catalog_models(models, agy_rules)
    if not eligible:
        return None
    tiered: List[Tuple[str, str, str]] = []
    for item in eligible:
        match = re.match(r"^(.+)-(minimal|low|medium|high|xhigh|max)$", item["slug"].lower())
        if match:
            tiered.append((match.group(1), match.group(2), item["display_name"]))
    if not tiered:
        for pref in family_priority:
            matched_items = [
                item for item in eligible
                if pref in _tokens(item["slug"]) and not (_tokens(item["slug"]) & light_tokens)
            ]
            if matched_items:
                return max(matched_items, key=lambda item: _model_generation(item["slug"]))["display_name"]
        non_light = [item for item in eligible if not (_tokens(item["slug"]) & light_tokens)]
        pool = non_light or eligible
        return max(pool, key=lambda item: _model_generation(item["slug"]))["display_name"]
    primary_family = None
    for pref in family_priority:
        matched_fams = list(dict.fromkeys(
            fam for fam, _, _ in tiered if pref in _tokens(fam) and not (_tokens(fam) & light_tokens)
        ))
        if matched_fams:
            primary_family = max(matched_fams, key=_model_generation)
            break
    if not primary_family:
        non_light_fams = list(dict.fromkeys(fam for fam, _, _ in tiered if not (_tokens(fam) & light_tokens)))
        fam_pool = non_light_fams or list(dict.fromkeys(fam for fam, _, _ in tiered))
        primary_family = max(fam_pool, key=_model_generation)
    variants = {tier: label for fam, tier, label in tiered if fam == primary_family}
    best_tier = max(variants, key=EFFORT_ORDER.index)
    return variants[best_tier]


def _plan_agy_model(
    gemini_root: Path, *, dry_run: bool, overwrite: bool, repo_root: Optional[Path] = None,
) -> List[FileChange]:
    """保留现有模型。预演仅读取本侧缓存，正式安装才查询原生 CLI。"""
    cli_dir = gemini_root / "antigravity-cli"
    settings_path = cli_dir / "settings.json"
    desktop_path = gemini_root / "config" / "config.json"
    cache_path = cli_dir / "cache" / "cae_models_cache.json"
    _validate_path(settings_path, directory=False, allow_hardlinks=True)
    settings_raw = _read_optional_bytes(settings_path, allow_hardlinks=True)
    settings = _read_json_object(settings_path, settings_raw)
    current = settings.get("model")
    if not overwrite and not (isinstance(current, str) and current.strip()):
        _validate_path(desktop_path, directory=False, allow_hardlinks=True)
        desktop = _read_json_object(desktop_path, _read_optional_bytes(desktop_path, allow_hardlinks=True))
        user_settings = desktop.get("userSettings", {})
        if not isinstance(user_settings, dict):
            raise ValueError(f"{desktop_path} 的 userSettings 必须为对象")
        current = user_settings.get("model")
    if isinstance(current, str) and current.strip() and not overwrite:
        print("[*] 保留 Antigravity 已配置的默认模型。")
        return []

    _validate_path(cache_path, directory=False, allow_hardlinks=True)
    cache_raw = _read_optional_bytes(cache_path, allow_hardlinks=True)
    models: List[Dict[str, str]] = []
    changes = []
    agy_bin = None if dry_run else _native_cli("agy", gemini_root.parent)
    if agy_bin:
        try:
            proc = subprocess.run(
                [agy_bin, "models"], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=6,
            )
            if proc.returncode == 0:
                models = _parse_agy_model_lines(proc.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if models:
        _validate_path(cache_path, directory=False)
        snapshot = {
            "source": "agy_models_cli",
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "models": models,
        }
        changes.append(FileChange(cache_path, cache_raw, _preserve_text_format(_json_bytes(snapshot), cache_raw)))
    elif cache_raw is not None:
        try:
            cache = _read_json_object(cache_path, cache_raw)
            if cache.get("source") in (None, "agy_models_cli"):
                models = [item for item in cache.get("models", [])
                          if isinstance(item, dict) and isinstance(item.get("slug"), str)
                          and isinstance(item.get("display_name"), str) and item["slug"] and item["display_name"]]
        except (TypeError, ValueError):
            print(f"[*] 无法读取模型目录 {cache_path}，保留原文件。")
    label = _select_agy_frontier_max_label(models, repo_root=repo_root)
    if not label:
        suffix = "。预演不会查询 CLI，正式安装可尝试读取本侧模型目录。" if dry_run else "，保留原配置。"
        print("[*] 未取得 Antigravity 的可用模型目录" + suffix)
    elif settings.get("model") != label:
        _validate_path(settings_path, directory=False)
        settings["model"] = label
        changes.append(FileChange(settings_path, settings_raw, _preserve_text_format(_json_bytes(settings), settings_raw)))
    return changes


def configure_agy_frontier_max_default(
    gemini_root: Path, dry_run: bool = False, *, overwrite: bool = False, repo_root: Optional[Path] = None,
) -> None:
    """把已校验的模型变更逐文件原子写入磁盘，预演只输出计划。"""
    for change in _plan_agy_model(gemini_root, dry_run=dry_run, overwrite=overwrite, repo_root=repo_root):
        _apply_file_change(change, dry_run)


def _agy_hooks_bytes(source: bytes, py_cmd: str) -> bytes:
    """以模板为输入生成目标平台命令，使重装比较最终内容。"""
    hooks_data = _read_json_object(Path("hooks.json"), source)
    lifecycle = hooks_data["user-guard-lifecycle"]
    for event, groups in lifecycle.items():
        for group in groups:
            for hook in group.get("hooks", [group]):
                command = hook["command"]
                prefix, separator, script = command.partition(" ./hooks/")
                if not separator or prefix != "python3 -X utf8 -I -B":
                    raise ValueError(f"反重力钩子模板的 {event} 命令格式有误")
                hook["command"] = py_cmd + separator + script
    return _json_bytes(hooks_data)


# 五、Codex 规则、只读子代理、钩子与沙箱配置同步


def _managed_skill_names(template_dir: Path) -> List[str]:
    skills_root = template_dir / "skills"
    if not skills_root.is_dir():
        return ["cae-harness"]
    return sorted(
        entry.name for entry in skills_root.iterdir()
        if entry.is_dir() and not entry.name.startswith(".") and entry.name != "__pycache__"
    )


def codex_skill_target(
    skills_dir: str, plugin_target_explicit: bool, disabled: bool, skill_name: str = "cae-harness"
) -> Optional[Path]:
    """显式插件目标仅配合显式技能目标，避免隔离安装触及真实用户目录。"""
    if disabled:
        return None
    if skills_dir:
        return Path(skills_dir).expanduser().absolute() / skill_name
    if plugin_target_explicit:
        return None
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    codex_root = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    if not codex_root.is_dir():
        return None
    return codex_root.absolute() / "skills" / skill_name


def _apply_codex_frontier_max(
    editor: "CodexConfig", cache_path: Path, *, overwrite: bool, repo_root: Optional[Path] = None,
) -> None:
    """按本机模型目录补齐缺项，显式覆盖时才更新已有模型及挡位。"""
    keys = ("model", "model_reasoning_effort")
    values = {key: editor.data[key] for key in keys if key in editor.data}
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError(f"{editor.path} 的默认模型和推理挡位必须为字符串")

    cur_model = values.get("model", "").strip()
    cur_effort = values.get("model_reasoning_effort", "").strip()
    if cur_model and cur_effort and "model_context_window" in editor.data and not overwrite:
        print("[*] 保留 Codex 已配置的默认模型和推理挡位。")
        return

    try:
        raw_cache = _read_optional_bytes(cache_path, allow_hardlinks=True)
        if raw_cache is None:
            raise ValueError("缺少 Codex 模型目录")
        cache = _read_json_object(cache_path, raw_cache)
        if not isinstance(cache.get("models"), list):
            raise ValueError("模型目录的 models 必须是列表")
        models = [model for model in cache["models"]
                  if isinstance(model, dict) and isinstance(model.get("slug"), str) and model["slug"]]
    except (OSError, ValueError):
        print(f"[*] 未读取到有效的 Codex 模型目录 {cache_path}，默认模型设置保持原样。")
        return

    if cur_model and not overwrite:
        selected = next((model for model in models if model["slug"] == cur_model), None)
    else:
        visible = [model for model in models if model.get("visibility") != "hide"] or models
        if cur_effort and not overwrite:
            visible = [model for model in visible if cur_effort in _model_efforts(model)]
        cx_rules = _load_fleet_profile(repo_root).get("client_catalog_rules", {}).get("codex", {})
        workhorse_families = [str(fam).lower() for fam in (cx_rules.get("workhorse_families") or ())]
        selected = next(
            (candidate for fam in workhorse_families for candidate in [_latest_codex_model(visible, fam)] if candidate is not None),
            None,
        ) or min(
            visible,
            key=lambda model: model["priority"] if type(model.get("priority")) is int else 9999,
            default=None,
        )
    if selected is None:
        print("[*] Codex 模型目录未找到与现有模型设置匹配的条目，已保留原配置。")
        return

    slug = selected["slug"]
    efforts = _model_efforts(selected)
    effort = cur_effort if cur_effort and not overwrite else max(efforts, key=EFFORT_ORDER.index, default="")
    if not effort:
        print(f"[*] Codex 模型 {slug} 缺少可用的非委派推理挡位元数据，未补齐挡位。")
        if overwrite and cur_effort:
            return

    for key, value in zip(keys, (slug, effort)):
        if value and value != values.get(key, "").strip():
            editor.set_string(key, value)
    maximum = _model_window(selected)
    if maximum is not None and (overwrite or "model_context_window" not in editor.data):
        editor.set_integer("model_context_window", maximum)


def _model_generation(slug: str) -> Tuple[int, ...]:
    match = re.search(r"(?:gpt|gemini)[- ](\d+(?:\.\d+)*)", slug, re.I)
    return tuple(map(int, match[1].split("."))) if match else ()


def _latest_codex_model(models: List[Dict[str, Any]], family: str) -> Optional[Dict[str, Any]]:
    candidates = [item for item in models if re.fullmatch(rf"gpt-\d+(?:\.\d+)*-{family}", item["slug"], re.I)
                  and item.get("visibility") != "hide"]
    return max(candidates, key=lambda item: _model_generation(item["slug"]), default=None)


def _model_window(model: Dict[str, Any]) -> Optional[int]:
    for key in ("max_context_window", "context_window"):
        value = model.get(key)
        if type(value) is int and value > 0:
            return value
    return None


def _model_efforts(model: Dict[str, Any]) -> List[str]:
    levels = model.get("supported_reasoning_levels", model.get("supported_efforts", []))
    if not isinstance(levels, list):
        return []
    return [effort for level in levels for effort in [level.get("effort") if isinstance(level, dict) else level]
            if effort in EFFORT_ORDER and effort != "ultra"
            and not (isinstance(level, dict) and "delegation" in str(level.get("description", "")).lower())]


def _apply_codex_workspace_boundary(editor: "CodexConfig") -> None:
    sandbox_mode = editor.data.get("sandbox_mode")
    if sandbox_mode is None or sandbox_mode == "danger-full-access":
        sandbox_mode = "workspace-write"
        editor.set_string("sandbox_mode", sandbox_mode)
    editor.add_defaults((), {"approval_policy": '"on-request"'})
    if sandbox_mode == "workspace-write":
        editor.add_defaults(("sandbox_workspace_write",), {"network_access": "true"})


def _apply_codex_baseline(editor: "CodexConfig", include_sandbox: bool) -> None:
    """补齐工程配置基线，沿用用户已明确设置的开关。"""
    editor.add_defaults((), {"model_reasoning_summary": '"auto"'})
    editor.add_defaults(("features",), {"memories": "false", "chronicle": "false"})
    editor.add_defaults(("memories",), {
        "generate_memories": "false", "use_memories": "false", "disable_on_external_context": "true",
    })
    editor.add_defaults(("desktop", "external-agent-import-sync-item-types"), {"HOOKS": "false"})
    if include_sandbox:
        _apply_codex_workspace_boundary(editor)


def _apply_codex_zotero(editor: "CodexConfig", zotero_exe: str) -> None:
    servers = editor.data.get("mcp_servers", {})
    if not isinstance(servers, dict) or ("zotero" in servers and not isinstance(servers["zotero"], dict)):
        raise ValueError(f"{editor.path} 的 mcp_servers 和 zotero 配置必须为表")
    if servers.get("zotero"):
        print("[*] 保留 Codex 已配置的 Zotero MCP。")
        return
    editor.add_defaults(("mcp_servers", "zotero"), {"command": _toml_string(zotero_exe)})
    editor.add_defaults(("mcp_servers", "zotero", "env"), {
        key: _toml_string(value) for key, value in ZOTERO_ENV.items()
    })


def _plan_codex_config(
    codex_root: Path, *, include_sandbox: bool, include_model: bool,
    overwrite_model: bool, zotero_exe: Optional[str], repo_root: Optional[Path] = None,
) -> Optional["FileChange"]:
    """先在内存中合并 TOML 并校验，双端安装可在写入前统一报错。"""
    editor = CodexConfig(codex_root / "config.toml")
    _apply_codex_baseline(editor, include_sandbox)
    if zotero_exe:
        _apply_codex_zotero(editor, zotero_exe)
    if include_model:
        _apply_codex_frontier_max(editor, codex_root / "models_cache.json", overwrite=overwrite_model, repo_root=repo_root)
    return editor.change()


def _sync_codex_frontier_max(
    codex_root: Path, dry_run: bool = False, *, overwrite: bool = False, repo_root: Optional[Path] = None,
) -> None:
    editor = CodexConfig(codex_root / "config.toml")
    _apply_codex_frontier_max(editor, codex_root / "models_cache.json", overwrite=overwrite, repo_root=repo_root)
    _apply_file_change(editor.change(), dry_run)


def _sync_codex_workspace_boundary(codex_root: Path, dry_run: bool = False) -> None:
    editor = CodexConfig(codex_root / "config.toml")
    if editor.original is not None:
        _apply_codex_workspace_boundary(editor)
        _apply_file_change(editor.change(), dry_run)


def _sync_codex_global_rules(template_rules_path: Path, codex_root: Path, dry_run: bool = False) -> None:
    _sync_global_rules(template_rules_path, codex_root / "AGENTS.md", "Codex", dry_run)


_LOCAL_ENV_COLLISIONS = re.compile(r"^([ \t]*(?:(?:[-*+]|\d+\.)[ \t]+)?(?:#{1,6}[ \t]+)?(?:\*{1,2})?)(R10|R11)\.", re.MULTILINE)


def _find_custom_rules_tail_start(text: str) -> Optional[int]:
    fence_re = re.compile(r"^[ \t]*(`{3,}|~{3,})")
    env_sec_re = re.compile(r"^##[ \t]+工具与([^\r\n]*)[^\S\n]*(?:\r?\n|$)")
    h2_re = re.compile(r"^##[ \t]+\S[^\r\n]*")
    in_fence_char = ""
    in_fence_len = 0
    custom_env_start: Optional[int] = None
    standard_env_seen = False
    following_start: Optional[int] = None
    pos = 0
    for line in text.splitlines(keepends=True):
        f_match = fence_re.match(line)
        if f_match:
            marker = f_match.group(1)
            if not in_fence_char:
                in_fence_char, in_fence_len = marker[0], len(marker)
            elif marker[0] == in_fence_char and len(marker) >= in_fence_len and not line[f_match.end():].strip():
                in_fence_char, in_fence_len = "", 0
            pos += len(line)
            continue
        if not in_fence_char:
            env_m = env_sec_re.match(line)
            if env_m:
                if env_m.group(1).strip() == "运行环境":
                    if not standard_env_seen:
                        standard_env_seen = True
                elif custom_env_start is None:
                    custom_env_start = pos
            elif standard_env_seen and following_start is None and h2_re.match(line):
                following_start = pos
        pos += len(line)
    if custom_env_start is not None and following_start is not None:
        return min(custom_env_start, following_start)
    return custom_env_start if custom_env_start is not None else following_start


def _sync_global_rules(template_rules_path: Path, rules_path: Path, label: str, dry_run: bool) -> None:
    """以完整模板更新规则，保留自定义尾段并迁移与标准规则冲突的编号。"""
    if not template_rules_path.is_file():
        return
    template_text = _decode_utf8(template_rules_path, template_rules_path.read_bytes()).replace("\r\n", "\n")

    codex_agents = rules_path
    original = _read_optional_bytes(codex_agents)
    existing = _decode_utf8(codex_agents, original or b"")

    tail_start = _find_custom_rules_tail_start(existing)
    newline = "\r\n" if "\r\n" in existing else "\n"
    if tail_start is not None:
        custom_tail = _LOCAL_ENV_COLLISIONS.sub(
            lambda match: match[1] + ("L1" if match[2] == "R10" else "L2") + ".",
            existing[tail_start:],
        )
        # 旧版安装器为尾段补一个末尾换行，继续兼容这一输出约定。
        if not custom_tail.endswith("\n"):
            custom_tail += newline
        prefix = template_text.rstrip()
        merged = (prefix.replace("\n", newline) + newline * 2 if prefix else "") + custom_tail
    else:
        merged = (template_text.rstrip() + "\n").replace("\n", newline)

    if existing == merged:
        print(f"[*] {label} 全局规则 {rules_path.name} 已是最新状态。")
        return

    if dry_run:
        print(f"[Dry-Run] 将同步更新 {codex_agents} 全局规则")
        return

    content = (UTF8_BOM if (original or b"").startswith(UTF8_BOM) else b"") + merged.encode("utf-8")
    _atomic_write(FileChange(codex_agents, original, content))
    print(f"[+] 已同步更新 {label} 全局规则 {codex_agents}")


def _sync_codex_agents(codex_template_dir: Path, codex_root: Path, dry_run: bool = False) -> None:
    """同步 Codex 只读子代理定义 (*.toml) 至 <codex_root>/agents/，保留用户其它自定义子代理。"""
    src_agents_dir = codex_template_dir / "agents"
    if not src_agents_dir.is_dir():
        return
    dst_agents_dir = codex_root / "agents"
    updated_names: List[str] = []
    for src_toml in sorted(src_agents_dir.glob("*.toml")):
        dst_toml = dst_agents_dir / src_toml.name
        src_bytes = src_toml.read_bytes()
        original = _read_optional_bytes(dst_toml)
        if original == src_bytes:
            continue
        updated_names.append(src_toml.name)
        if not dry_run:
            _atomic_write(FileChange(dst_toml, original, src_bytes))

    if not updated_names:
        print("[*] Codex 只读子代理 (agents/*.toml) 已是最新状态。")
    elif dry_run:
        print(f"[Dry-Run] 将同步 Codex 只读子代理至 {dst_agents_dir}: {', '.join(updated_names)}")
    else:
        print(f"[+] 已同步 Codex 只读子代理至 {dst_agents_dir}: {', '.join(updated_names)}")


class ManagedCodexHook(NamedTuple):
    kind: str
    interpreter: Optional[Tuple[str, List[str]]]
    policy: Optional[Tuple[str, int]] = None


class CodexHooksPlan(NamedTuple):
    change: Optional[FileChange]
    mode: str
    compact_limit: int


def _codex_hook_tokens(handler: Dict[str, Any]) -> List[str]:
    if handler.get("type") != "command" or not isinstance(handler.get("command"), str):
        return []
    try:
        tokens = shlex.split(handler["command"], posix=(os.name != "nt"))
        return [token.strip('"') for token in tokens] if os.name == "nt" else tokens
    except ValueError:
        return []


def _codex_managed_hook(
    handler: Dict[str, Any], codex_root: Path, candidates: List[Tuple[str, str, List[str]]],
) -> Optional[ManagedCodexHook]:
    tokens = _codex_hook_tokens(handler)
    hook_root = codex_root / "hooks"

    def matches(token: str, path: Path) -> bool:
        try:
            return _same_nonlink_path(Path(token).expanduser(), path)
        except (RuntimeError, ValueError):
            return False

    for idx, token in enumerate(tokens[1:], start=1):
        script_name = Path(token).name
        if script_name not in CODEX_HOOK_ENTRIES or not matches(token, hook_root / script_name):
            continue
        args = tokens[idx + 1:]
        executable = tokens[0]
        exe_name = PureWindowsPath(executable).name.lower()
        path_prefix = (
            Path(executable).is_absolute()
            or (os.name == "nt" and (PureWindowsPath(executable).is_absolute() or executable.startswith("/")))
        )
        if not path_prefix and ("/" in executable or "\\" in executable):
            return ManagedCodexHook("custom", None)
        command = executable.lower() if os.name == "nt" else executable
        interpreter = next(
            ((path, flags) for name, path, flags in candidates
             if (path_prefix and _same_nonlink_path(Path(executable), Path(path)))
             or (not path_prefix and name and command == name)
             or (not path_prefix and os.name == "nt" and name and command + ".exe" == name)),
            None,
        )
        if not path_prefix and interpreter is not None:
            resolved = shutil.which(executable)
            if not resolved or not _same_nonlink_path(Path(resolved), Path(interpreter[0])):
                interpreter = None
        if interpreter is not None:
            launcher = interpreter[1]
        else:
            if (path_prefix and Path(executable).exists()) or not _is_valid_python_exe_name(exe_name):
                return ManagedCodexHook("custom", None)
            launcher = tokens[1:2] if exe_name in {"py", "py.exe"} and tokens[1:2] in PY_LAUNCHER_ARGS else []
        actual_flags = tokens[1:idx]
        if not any(actual_flags == launcher + flags for flags in LEGACY_PYTHON_FLAGS):
            return ManagedCodexHook("custom", None)
        if script_name == "session_context_loader.py" and args[:2] == ["--client", "codex"]:
            rest = args[2:]
            if rest and (
                len(rest) not in (2, 4) or rest[0] != "--state-dir"
                or not matches(rest[1], hook_root / "state" / "session-context")
                or (len(rest) == 4 and (rest[2] != "--log" or not matches(rest[3], hook_root / "logs" / "session-context.jsonl")))
            ):
                return ManagedCodexHook("custom", None)
            return ManagedCodexHook("loader", interpreter)
        if script_name in {"status_guard.py", "reply_repeat_guard.py"}:
            if (len(args) == 6 and args[:3] == ["--client", "codex", "--state-dir"]
                    and matches(args[3], hook_root / "state") and args[4] == "--log-dir"
                    and matches(args[5], hook_root / "logs")):
                return ManagedCodexHook(script_name, interpreter)
            return ManagedCodexHook("custom", None)
        if (
            script_name == "handoff_gate.py" and len(args) == 8
            and actual_flags in (launcher + HOOK_PYTHON_FLAGS, launcher + ["-I", "-B"])
            and args[:3] == ["--client", "codex", "--mode"] and args[3] in {"advisory", "enforce"}
            and args[4] == "--compact-limit" and re.fullmatch(r"[0-9]+", args[5])
            and args[6] == "--state-dir" and matches(args[7], hook_root / "state" / "handoff")
        ):
            return ManagedCodexHook("gate", interpreter, (args[3], int(args[5])))
        return ManagedCodexHook("custom", None)
    return None


def _codex_handoff_policy(
    doc: Dict[str, Any], policies: Set[Tuple[str, int]], requested_mode: Optional[str],
    requested_limit: Optional[int], *, repo_root: Optional[Path] = None,
    model_name: str = "", effort: str = "", explicit_window: Optional[int] = None,
) -> Tuple[str, int]:
    description = doc.get("description", "")
    if not isinstance(description, str):
        raise ValueError("Codex 钩子 description 字段必须是字符串")
    markers = [(index, line) for index, line in enumerate(description.split("\n"))
               if line.startswith("[user-guard codex-handoff")]
    if markers:
        match = HANDOFF_MARKER.fullmatch(markers[0][1].removesuffix("\r"))
        if len(markers) != 1 or match is None:
            raise ValueError("Codex 钩子 description 中的交接配置标记无效或重复")
        policies.add((match[1], int(match[2])))

    modes = {mode for mode, _ in policies}
    limits = {limit for _, limit in policies}
    if (requested_mode is None and len(modes) > 1) or (requested_limit is None and len(limits) > 1):
        raise ValueError("Codex 钩子交接策略不一致，请用 --codex-handoff-mode 和 --codex-handoff-compactions 指定需要保留的值")
    def_mode, def_limit = _default_handoff_policy("codex", repo_root, model_name=model_name, effort=effort, explicit_window=explicit_window)
    mode = requested_mode if requested_mode is not None else next(iter(modes), def_mode)
    limit = requested_limit if requested_limit is not None else next(iter(limits), def_limit)
    marker = f"[user-guard codex-handoff v1 mode={mode} compact_limit={limit}]"
    if markers:
        lines = description.split("\n")
        ending = "\r" if markers[0][1].endswith("\r") else ""
        lines[markers[0][0]] = marker + ending
        doc["description"] = "\n".join(lines)
    else:
        newline = "\r\n" if "\r\n" in description else "\n"
        separator = newline if description and not description.endswith("\n") else ""
        doc["description"] = description + separator + marker
    return mode, limit


def _read_codex_hooks(path: Path, raw: Optional[bytes]) -> Dict[str, Any]:
    if raw is None:
        return {"hooks": {}}
    doc = _read_json_object(path, raw)
    if "hooks" in doc and not isinstance(doc["hooks"], dict):
        raise ValueError(f"无法安全合并 {path}，hooks 字段必须是对象。")
    return doc


def _plan_codex_hooks(
    hooks_path: Path, template_path: Path, py_cmd: str,
    mode: Optional[str] = None, compact_limit: Optional[int] = None,
    *, verify_interpreters: bool = True, repo_root: Optional[Path] = None,
    config_bytes: Optional[bytes] = None,
) -> CodexHooksPlan:
    """逐条识别本项目钩子并合并，保留用户 handler、信任信息及交接策略。"""
    _validate_path(hooks_path, directory=False)
    codex_root = hooks_path.parent.resolve()
    original = _read_optional_bytes(hooks_path)
    doc = _read_codex_hooks(hooks_path, original)
    initial = copy.deepcopy(doc)
    defaults = _read_codex_hooks(template_path, template_path.read_bytes())["hooks"]
    events = doc.setdefault("hooks", {})
    codex_path = codex_root.as_posix()
    quote_path = (lambda path: '"' + path + '"') if os.name == "nt" else shlex.quote
    quote_exe = (
        (lambda exe: f'"{exe}"' if Path(exe).is_absolute() or any(ch.isspace() for ch in exe) else exe)
        if os.name == "nt" else shlex.quote
    )
    py_prefix_size = len(shlex.split(py_cmd, posix=(os.name != "nt")))

    candidates = _hook_python_candidates()
    classifications = {}
    policies = set()
    for event, existing in events.items():
        if not isinstance(existing, list) or any(
            not isinstance(group, dict) or not isinstance(group.get("hooks"), list)
            or any(not isinstance(handler, dict) for handler in group["hooks"])
            for group in existing
        ):
            raise ValueError(f"无法安全合并 {hooks_path} 的 {event}，事件须包含钩子组列表及各组的 handler 对象列表。")
        if event not in defaults:
            continue
        for group in existing:
            for handler in group["hooks"]:
                managed = _codex_managed_hook(handler, codex_root, candidates)
                classifications[id(handler)] = managed
                if managed and managed.kind == "custom":
                    print(f"[!] {hooks_path} 的 {event} 包含自定义脚本命令，保留原 handler。", file=sys.stderr)
                if managed and managed.policy is not None:
                    policies.add(managed.policy)
    cx_model, cx_effort, cx_window = "", "", None
    cfg_toml = codex_root / "config.toml"
    cfg_raw = config_bytes if config_bytes is not None else _read_optional_bytes(cfg_toml)
    if cfg_raw is not None:
        try:
            top = tomllib.loads(cfg_raw.decode("utf-8-sig"))
            cx_model = str(top.get("model") or "").strip()
            cx_effort = str(top.get("model_reasoning_effort") or "").strip()
            raw_cw = top.get("model_context_window")
            if isinstance(raw_cw, int) and not isinstance(raw_cw, bool):
                cx_window = raw_cw
        except (UnicodeError, tomllib.TOMLDecodeError, ValueError, RecursionError):
            pass
    mode, compact_limit = _codex_handoff_policy(
        doc, policies, mode, compact_limit, repo_root=repo_root,
        model_name=cx_model, effort=cx_effort, explicit_window=cx_window,
    )

    for event, groups in defaults.items():
        matcher = groups[0].get("matcher")
        desired = {}
        for handler in groups[0]["hooks"]:
            name = next(name for name in CODEX_HOOK_ENTRIES if name in handler["command"])
            kind = {"handoff_gate.py": "gate", "session_context_loader.py": "loader"}.get(name, name)
            if kind == "gate" and mode == "off":
                continue
            command = handler["command"].replace("--mode advisory --compact-limit 0", f"--mode {mode} --compact-limit {compact_limit}")
            script_start = command.index('"~/.codex/')
            script_command = re.sub(
                r'"~/.codex/([^"\n]+)"',
                lambda match: quote_path(codex_path + '/' + match.group(1)),
                command[script_start:],
            )
            command = f'{py_cmd} {script_command}'
            desired[kind] = {**handler, "command": command}

        if not desired and event not in events:
            continue
        existing = events.get(event, [])
        retained = set()
        first_matching_group = None
        merged = []
        for group in existing:
            matching = group.get("matcher") == matcher
            handlers = []
            custom_slots = set()
            for handler in group["hooks"]:
                managed = classifications[id(handler)]
                if managed and managed.kind == "custom" and matching and event == "SessionStart":
                    if any(Path(token).name == "session_context_loader.py" for token in _codex_hook_tokens(handler)):
                        custom_slots.add("loader")
                if managed is None or managed.kind == "custom" or (managed.kind == "loader" and event != "SessionStart"):
                    handlers.append(handler)
                    continue
                kind = managed.kind
                if matching and kind in desired and kind not in retained:
                    updated_handler = handler
                    if handler["command"] != desired[kind]["command"]:
                        if verify_interpreters and (managed.interpreter is None or not _is_working_python_cmd(*managed.interpreter)):
                            updated_handler = {**handler, "command": desired[kind]["command"]}
                        else:
                            tokens = _codex_hook_tokens(handler)
                            launcher = managed.interpreter[1] if managed.interpreter else (
                                tokens[1:2] if PureWindowsPath(tokens[0]).name.lower() in {"py", "py.exe"} and tokens[1:2] in PY_LAUNCHER_ARGS else []
                            )
                            latest_args = _codex_hook_tokens(desired[kind])[py_prefix_size:]
                            if tokens[1:] != launcher + HOOK_PYTHON_FLAGS + latest_args:
                                prefix = " ".join([quote_exe(tokens[0]), *launcher, *HOOK_PYTHON_FLAGS])
                                updated_handler = {**handler, "command": prefix + desired[kind]["command"][len(py_cmd):]}
                    handlers.append(updated_handler)
                    retained.add(kind)
            retained.update(custom_slots)
            if handlers or not group["hooks"]:
                updated_group = {**group, "hooks": handlers}
                if matching and first_matching_group is None:
                    first_matching_group = updated_group
                merged.append(updated_group)

        missing = [handler for kind, handler in desired.items() if kind not in retained]
        if missing:
            if first_matching_group is None:
                first_matching_group = {} if matcher is None else {"matcher": matcher}
                first_matching_group["hooks"] = []
                merged.append(first_matching_group)
            first_matching_group["hooks"].extend(missing)
        if merged != existing:
            if merged:
                events[event] = merged
            else:
                events.pop(event, None)
    try:
        content = _preserve_text_format(_json_bytes(doc), original)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"无法安全合并 {hooks_path}，钩子注册表不能写成有效的 UTF-8 JSON: {exc}") from exc
    change = FileChange(hooks_path, original, content) if doc != initial else None
    return CodexHooksPlan(change, mode, compact_limit)


def _codex_hook_script_names() -> List[str]:
    return [*CODEX_HOOK_ENTRIES, "user_words.py"]


def _sync_codex_hooks(
    user_guard_hooks_dir: Path,
    codex_root: Path,
    plan: CodexHooksPlan,
    dry_run: bool = False,
) -> None:
    """同步 Codex 生命周期钩子脚本，逐条合并已登记的 handler。"""
    hooks_path = codex_root / "hooks.json"
    dst_hooks_dir = codex_root / "hooks"
    mode, compact_limit = plan.mode, plan.compact_limit
    hook_scripts = _codex_hook_script_names()
    for target in (dst_hooks_dir, *(dst_hooks_dir / name for name in hook_scripts)):
        if _is_link_or_junction(target):
            raise ValueError(f"Codex 钩子脚本或目录是链接，请先核对: {target}")
    scripts_updated: List[str] = []

    for name in hook_scripts:
        src_bytes = (user_guard_hooks_dir / name).read_bytes()
        dst_file = dst_hooks_dir / name
        original = _read_optional_bytes(dst_file)
        if original == src_bytes:
            continue
        scripts_updated.append(name)
        if not dry_run:
            _atomic_write(FileChange(dst_file, original, src_bytes))
    src_lib_dir = user_guard_hooks_dir / "_lib"
    if src_lib_dir.is_dir():
        dst_lib_dir = dst_hooks_dir / "_lib"
        if _is_link_or_junction(dst_lib_dir):
            raise ValueError(f"Codex 钩子共享库目录是链接，请先核对: {dst_lib_dir}")
        for lib_name in HOOK_LIB_FILES:
            src_lib = src_lib_dir / lib_name
            if not src_lib.is_file():
                continue
            dst_lib = dst_lib_dir / lib_name
            if _is_link_or_junction(dst_lib):
                raise ValueError(f"Codex 钩子共享库文件是链接，请先核对: {dst_lib}")
            src_bytes = src_lib.read_bytes()
            original = _read_optional_bytes(dst_lib)
            if original == src_bytes:
                continue
            scripts_updated.append(f"_lib/{lib_name}")
            if not dry_run:
                _atomic_write(FileChange(dst_lib, original, src_bytes))

    for pycache_dir in (dst_hooks_dir / "__pycache__", dst_hooks_dir / "_lib" / "__pycache__"):
        _validate_path(pycache_dir, directory=True)
        _validate_tree_links(pycache_dir)
        if pycache_dir.is_dir():
            if dry_run:
                print(f"  [Dry-Run CLEAN] 将清理 Codex 钩子字节码缓存: {pycache_dir}")
            else:
                shutil.rmtree(pycache_dir)
    _clean_legacy_session_lock_files(dst_hooks_dir / "state", dry_run=dry_run)

    if scripts_updated:
        action = "将同步" if dry_run else "已同步"
        prefix = "[Dry-Run]" if dry_run else "[+]"
        print(f"{prefix} {action} Codex 钩子脚本至 {dst_hooks_dir}: {', '.join(scripts_updated)}")

    if plan.change is None:
        print("[*] Codex 钩子注册表已是最新状态，保留现有文件字节。")
    elif dry_run:
        print(f"[Dry-Run] 将合并 Codex 钩子注册表 {hooks_path}（交接模式 {mode}，压缩次数阈值 {compact_limit}）")
    else:
        _atomic_write(plan.change)
        print(f"[+] 已合并 Codex 钩子注册表 {hooks_path}（交接模式 {mode}，压缩次数阈值 {compact_limit}）")
        print("[!] 若本机 Codex 已启用钩子信任哈希（[hooks.state]），请在 Codex 交互终端执行 /hooks 复核并重新信任变更后的钩子。")


def _validate_codex_assets(
    codex_root: Path, codex_template_dir: Path, args: argparse.Namespace, hooks_plan: Optional[CodexHooksPlan],
) -> None:
    """在双端任一目录写入前，检查目标路径及本次需要的全部 Codex 模板。"""
    _validate_path(codex_root / "config.toml", directory=False)
    if not args.no_codex_rules:
        source_rules = codex_template_dir.parent / "user-guard" / "rules" / "AGENTS.md"
        _decode_utf8(source_rules, _validate_path(source_rules, directory=False, allow_hardlinks=True).read_bytes())
        rules = _validate_path(codex_root / "AGENTS.md", directory=False)
        _decode_utf8(rules, _read_optional_bytes(rules) or b"")
    if not args.no_codex_agents:
        source_agents = _validate_path(codex_template_dir / "agents", directory=True)
        if not source_agents.is_dir():
            raise ValueError(f"找不到 Codex 只读子代理模板目录 {source_agents}")
        _validate_path(codex_root / "agents", directory=True)
        for source in sorted(source_agents.glob("*.toml")):
            _decode_utf8(source, _validate_path(source, directory=False, allow_hardlinks=True).read_bytes())
            _validate_path(codex_root / "agents" / source.name, directory=False)
    if hooks_plan is not None:
        hooks_dir = codex_root / "hooks"
        _validate_path(hooks_dir, directory=True)
        if hooks_plan.mode == "off":
            _validate_path(hooks_dir / "handoff_gate.py", directory=False)
        for name in _codex_hook_script_names():
            source = codex_template_dir.parent / "user-guard" / "hooks" / name
            _decode_utf8(source, _validate_path(source, directory=False, allow_hardlinks=True).read_bytes())
            _validate_path(hooks_dir / name, directory=False)
        _validate_path(hooks_dir / "_lib", directory=True)
        src_lib_dir = codex_template_dir.parent / "user-guard" / "hooks" / "_lib"
        if os.path.lexists(src_lib_dir):
            _validate_path(src_lib_dir, directory=True)
        for lib_name in HOOK_LIB_FILES:
            src_lib = src_lib_dir / lib_name
            if os.path.lexists(src_lib):
                _decode_utf8(src_lib, _validate_path(src_lib, directory=False, allow_hardlinks=True).read_bytes())
            _validate_path(hooks_dir / "_lib" / lib_name, directory=False)
        for rel in ("state", "state/handoff", "state/session-context", "state/user-words", "logs"):
            _validate_path(hooks_dir / rel, directory=True)
        _validate_path(hooks_dir / "logs" / "session-context.jsonl", directory=False)
        for pycache in (hooks_dir / "__pycache__", hooks_dir / "_lib" / "__pycache__"):
            _validate_tree_links(_validate_path(pycache, directory=True))
        _validate_tree_links(hooks_dir / "state")


# 六、Claude Code、路由器与 CC Switch 同步


def _sync_claude_global_rules(template_rules_path: Path, claude_root: Path, dry_run: bool = False) -> None:
    _sync_global_rules(template_rules_path, claude_root / "CLAUDE.md", "Claude Code", dry_run)


def _sync_claude_agents(claude_template_dir: Path, claude_root: Path, dry_run: bool = False) -> None:
    for source in sorted((claude_template_dir / "agents").glob("readonly-*.md")):
        target = claude_root / "agents" / source.name
        original = _read_optional_bytes(target)
        content = source.read_bytes()
        if original != content:
            _apply_file_change(FileChange(target, original, content), dry_run)


def _claude_hook_tokens(handler: Dict[str, Any]) -> List[str]:
    if handler.get("type") != "command" or not isinstance(handler.get("command"), str):
        return []
    args = handler.get("args")
    if isinstance(args, list) and all(isinstance(arg, str) for arg in args):
        return [handler["command"].strip('"'), *args]
    return _codex_hook_tokens(handler)


def _claude_managed_script(handler: Dict[str, Any], claude_root: Path) -> Optional[str]:
    tokens = _claude_hook_tokens(handler)
    if len(tokens) < 2 or not tokens[0] or tokens[0].endswith(("/", "\\")):
        return None
    exe_token = tokens[0]
    if os.name != "nt" and ("/" in exe_token or "\\" in exe_token) and ("\\" in exe_token or not Path(exe_token).is_absolute()):
        return None
    if os.name == "nt" and ("/" in exe_token or "\\" in exe_token) and not PureWindowsPath(exe_token).is_absolute():
        return None
    exe_name = PureWindowsPath(exe_token).name.lower()
    if not _is_valid_python_exe_name(exe_name):
        return None
    idx = 2 if exe_name in {"py", "py.exe"} and tokens[1:2] in PY_LAUNCHER_ARGS else 1
    while idx < len(tokens):
        if tokens[idx] == "-X" and idx + 1 < len(tokens):
            idx += 2
        elif tokens[idx] in {"-I", "-B", "-u", "-S", "-E", "-s"}:
            idx += 1
        else:
            break
    if idx >= len(tokens):
        return None
    target = tokens[idx]
    name = Path(target).name
    if name in (*CLAUDE_HOOK_SCRIPTS, *CLAUDE_RETIRED_SCRIPTS):
        try:
            if _same_nonlink_path(Path(target).expanduser(), claude_root / "hooks" / name):
                return name
        except (OSError, RuntimeError, ValueError):
            return None
    return None


def _claude_handoff_policy(
    handlers: List[Dict[str, Any]], mode: Optional[str], compact_limit: Optional[int],
    *, repo_root: Optional[Path] = None,
    model_name: str = "", effort: str = "", explicit_window: Optional[int] = None,
) -> Tuple[str, int]:
    policies = set()
    for handler in handlers:
        tokens = _claude_hook_tokens(handler)
        try:
            old_mode = tokens[tokens.index("--mode") + 1]
            old_limit = tokens[tokens.index("--compact-limit") + 1]
            if old_mode in {"advisory", "enforce", "off"} and re.fullmatch(r"[0-9]+", old_limit):
                policies.add((old_mode, int(old_limit)))
        except (ValueError, IndexError):
            continue
    modes, limits = {item[0] for item in policies}, {item[1] for item in policies}
    if (mode is None and len(modes) > 1) or (compact_limit is None and len(limits) > 1):
        raise ValueError("Claude 交接策略不一致，请显式指定 --claude-handoff-mode 和 --claude-handoff-compactions")
    def_mode, def_limit = _default_handoff_policy("claude", repo_root, model_name=model_name, effort=effort, explicit_window=explicit_window)
    return mode or next(iter(modes), def_mode), compact_limit if compact_limit is not None else next(iter(limits), def_limit)


def _plan_claude_settings(
    settings_path: Path, template_path: Path, py_cmd: str,
    mode: Optional[str] = None, compact_limit: Optional[int] = None, *,
    include_hooks: bool = True, include_model: bool = True, overwrite_model: bool = False,
    verify_interpreters: bool = True, repo_root: Optional[Path] = None,
) -> CodexHooksPlan:
    """按目标脚本路径迁移托管钩子，其余设置及第三方 handler 保持原值。"""
    original = _read_optional_bytes(settings_path)
    doc = _read_json_object(settings_path, original)
    initial = copy.deepcopy(doc)
    claude_root = settings_path.parent.resolve()
    env = doc.get("env", {})
    if not isinstance(env, dict):
        raise ValueError(f"{settings_path} 的 env 必须是对象")
    if include_model:
        if overwrite_model or "effortLevel" not in doc:
            doc["effortLevel"] = _default_claude_effort(repo_root)
        if overwrite_model:
            env.pop("CLAUDE_CODE_EFFORT_LEVEL", None)
    if include_hooks:
        events = doc.setdefault("hooks", {})
        if not isinstance(events, dict):
            raise ValueError(f"{settings_path} 的 hooks 必须是对象")
        defaults = _read_json_object(template_path, template_path.read_bytes())["hooks"]
        managed = {}
        prefixes = {}
        verified = {}
        gates = []
        for event, groups in events.items():
            if not isinstance(groups, list) or any(
                not isinstance(group, dict) or not isinstance(group.get("hooks"), list)
                or any(not isinstance(handler, dict) for handler in group["hooks"]) for group in groups
            ):
                raise ValueError(f"{settings_path} 的 {event} 必须包含钩子组与 handler 对象列表")
            for group in groups:
                for handler in group["hooks"]:
                    name = _claude_managed_script(handler, claude_root)
                    if name is None:
                        continue
                    managed[id(handler)] = name
                    if name == "handoff_gate.py":
                        gates.append(handler)
                    tokens = _claude_hook_tokens(handler)
                    executable = tokens[0]
                    launcher = tokens[1:2] if PureWindowsPath(executable).name.lower() in {"py", "py.exe"} and tokens[1:2] in PY_LAUNCHER_ARGS else []
                    resolved = executable if Path(executable).is_absolute() else shutil.which(executable)
                    if resolved and verify_interpreters:
                        key = (resolved, tuple(launcher))
                        if key not in verified:
                            verified[key] = _is_working_python_cmd(resolved, launcher)
                        if verified[key]:
                            quote = ((lambda value: '"' + value + '"' if Path(value).is_absolute() or any(c.isspace() for c in value) else value)
                                     if os.name == "nt" else shlex.quote)
                            prefixes.setdefault(name, " ".join([quote(executable), *launcher, *HOOK_PYTHON_FLAGS]))
        # 关闭模式通过现有托管钩子中不含 gate 的状态沿用，无需添加非标准 settings 字段。
        modern = any("args" not in handler and "--client" in tokens
                     and tokens[2 if tokens[1:2] in PY_LAUNCHER_ARGS else 1:][:4] == HOOK_PYTHON_FLAGS
                     for groups in events.values() for group in groups for handler in group["hooks"]
                     if id(handler) in managed for tokens in [_claude_hook_tokens(handler)])
        if mode is None and managed and modern and not gates:
            mode = "off"
        cl_model = str(env.get("ANTHROPIC_MODEL") or "").strip()
        cl_effort = str(doc.get("effortLevel") or "").strip()
        raw_cl_win = str(env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") or "").strip()
        cl_window = int(raw_cl_win) if raw_cl_win.isdigit() else None
        mode, compact_limit = _claude_handoff_policy(
            gates, mode, compact_limit, repo_root=repo_root,
            model_name=cl_model, effort=cl_effort, explicit_window=cl_window,
        )
        for event, groups in list(events.items()):
            retained = []
            for group in groups:
                handlers = [handler for handler in group["hooks"] if id(handler) not in managed]
                if handlers or not group["hooks"]:
                    retained.append({**group, "hooks": handlers})
            if retained:
                events[event] = retained
            else:
                events.pop(event, None)
        quote_path = (lambda value: '"' + value + '"') if os.name == "nt" else shlex.quote
        for event, groups in defaults.items():
            default_group = groups[0]
            desired = []
            for handler in default_group["hooks"]:
                command = handler["command"]
                name = next(name for name in CLAUDE_HOOK_SCRIPTS if name in command)
                if name == "handoff_gate.py" and mode == "off":
                    continue
                command = command[command.index('"~/.claude/'):]
                command = re.sub(r'"~/.claude/([^"\n]+)"',
                                 lambda match: quote_path(claude_root.as_posix() + "/" + match[1]), command)
                command = command.replace("--mode advisory --compact-limit 0", f"--mode {mode} --compact-limit {compact_limit}")
                desired.append({**handler, "command": prefixes.get(name, next(iter(prefixes.values()), py_cmd)) + " " + command})
            if not desired:
                continue
            existing = events.setdefault(event, [])
            target = next((group for group in existing if group.get("matcher") == default_group.get("matcher")), None)
            if target is None:
                target = {key: value for key, value in default_group.items() if key != "hooks"}
                target["hooks"] = []
                existing.append(target)
            target["hooks"].extend(desired)
    else:
        cl_model = str(env.get("ANTHROPIC_MODEL") or "").strip()
        cl_effort = str(doc.get("effortLevel") or "").strip()
        raw_cl_win = str(env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") or "").strip()
        cl_window = int(raw_cl_win) if raw_cl_win.isdigit() else None
        def_mode, def_limit = _default_handoff_policy(
            "claude", repo_root, model_name=cl_model, effort=cl_effort, explicit_window=cl_window,
        )
        mode, compact_limit = mode or def_mode, compact_limit if compact_limit is not None else def_limit
    content = _preserve_text_format(_json_bytes(doc), original)
    return CodexHooksPlan(FileChange(settings_path, original, content) if doc != initial else None, mode, compact_limit)


def _claude_hook_script_names() -> List[str]:
    return list(CLAUDE_HOOK_SCRIPTS)


def _sync_claude_hooks(
    source_hooks: Path, claude_root: Path, plan: CodexHooksPlan, dry_run: bool = False,
) -> None:
    hooks_dir = claude_root / "hooks"
    for name in _claude_hook_script_names():
        target = hooks_dir / name
        original, content = _read_optional_bytes(target), (source_hooks / name).read_bytes()
        if original != content:
            _apply_file_change(FileChange(target, original, content), dry_run)
    src_lib_dir = source_hooks / "_lib"
    if src_lib_dir.is_dir():
        dst_lib_dir = hooks_dir / "_lib"
        _validate_path(dst_lib_dir, directory=True)
        for lib_name in HOOK_LIB_FILES:
            src_lib = src_lib_dir / lib_name
            if not src_lib.is_file():
                continue
            target_lib = _validate_path(dst_lib_dir / lib_name, directory=False)
            original, content = _read_optional_bytes(target_lib), src_lib.read_bytes()
            if original != content:
                _apply_file_change(FileChange(target_lib, original, content), dry_run)
    retired = list(CLAUDE_RETIRED_SCRIPTS)
    for name in retired:
        target = _validate_path(hooks_dir / name, directory=False)
        if target.exists():
            if dry_run:
                print(f"[Dry-Run CLEAN] 将清理 Claude 废弃钩子 {target}")
            else:
                target.unlink()
    for cache in (hooks_dir / "__pycache__", hooks_dir / "_lib" / "__pycache__"):
        _validate_tree_links(cache)
        if cache.is_dir():
            if dry_run:
                print(f"[Dry-Run CLEAN] 将清理 {cache}")
            else:
                shutil.rmtree(cache)
    _clean_legacy_session_lock_files(hooks_dir / "state", dry_run=dry_run)
    _apply_file_change(plan.change, dry_run)


def _cc_router_files(template_dir: Path, claude_root: Path) -> List[Tuple[Path, Path]]:
    pairs = [(source, claude_root / "router" / source.name) for source in _template_files(template_dir)]
    primary = claude_root / "router" / "router.json"
    if not primary.exists():
        pairs.append((template_dir / ("router.windows.json" if os.name == "nt" else "router.wsl.json"), primary))
    return pairs


def _sync_cc_router(template_dir: Path, claude_root: Path, dry_run: bool = False) -> None:
    for source, target in _cc_router_files(template_dir, claude_root):
        original, content = _read_optional_bytes(target), source.read_bytes()
        if target.name == "router.json" and original is None:
            config = json.loads(content.decode("utf-8-sig"))
            repo_root = template_dir.parent.parent.parent
            router = _load_fleet_profile(repo_root).get("runtime_tunables", {}).get("headless_dispatch", {}).get("router", {})
            port = router.get("port" if os.name == "nt" else "wsl_port", config.get("port"))
            if type(port) is int and 1 <= port <= 65535 and config.get("port") != port:
                config["port"] = port
                content = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if content != original:
            _apply_file_change(FileChange(target, original, content), dry_run)
        if not dry_run and target.suffix in {".sh", ".py"}:
            try:
                if stat.S_IMODE(target.stat().st_mode) != 0o755:
                    target.chmod(0o755)
            except OSError:
                pass


def _load_ccswitch_admin(repo_root: Path) -> ModuleType:
    source = repo_root / "template" / "claude" / "router" / "ccswitch_admin.py"
    module = ModuleType("installer_ccswitch_admin")
    module.__file__ = str(source)
    # 不通过导入机制生成字节码缓存，即使调用方没有启用 -B，体检也保持零写入。
    exec(compile(source.read_bytes(), str(source), "exec"), module.__dict__)
    return module


def _validate_claude_assets(repo_root: Path, claude_root: Path, args: argparse.Namespace) -> None:
    _validate_path(claude_root, directory=True)
    _validate_path(claude_root / "settings.json", directory=False)
    sources = []
    if not args.no_claude_rules:
        sources.append((repo_root / "template/user-guard/rules/AGENTS.md", claude_root / "CLAUDE.md"))
    if not args.no_claude_agents:
        source_agents = _validate_path(repo_root / "template/claude/agents", directory=True)
        if not source_agents.is_dir():
            raise ValueError(f"找不到 Claude 只读子代理模板目录 {source_agents}")
        _validate_path(claude_root / "agents", directory=True)
        sources.extend((source, claude_root / "agents" / source.name)
                       for source in sorted(source_agents.glob("readonly-*.md")))
    if not args.no_claude_hooks:
        hooks_dir = _validate_path(claude_root / "hooks", directory=True)
        _validate_path(hooks_dir / "_lib", directory=True)
        for lib_name in HOOK_LIB_FILES:
            _validate_path(hooks_dir / "_lib" / lib_name, directory=False)
        for name in CLAUDE_RETIRED_SCRIPTS:
            _validate_path(hooks_dir / name, directory=False)
        for rel in ("state", "state/handoff", "state/session-context", "state/user-words", "logs"):
            _validate_path(hooks_dir / rel, directory=True)
        _validate_path(hooks_dir / "logs" / "session-context.jsonl", directory=False)
        _validate_tree_links(hooks_dir / "__pycache__")
        _validate_tree_links(hooks_dir / "_lib" / "__pycache__")
        _validate_tree_links(hooks_dir / "state")
        sources.append((repo_root / "template/claude/router/ccswitch_admin.py", hooks_dir / "ccswitch_admin.py"))
        sources.extend((repo_root / "template/user-guard/hooks" / name, hooks_dir / name)
                       for name in _claude_hook_script_names())
        src_lib_dir = repo_root / "template/user-guard/hooks/_lib"
        if os.path.lexists(src_lib_dir):
            _validate_path(src_lib_dir, directory=True)
            sources.extend(
                (src_lib_dir / lib_name, hooks_dir / "_lib" / lib_name)
                for lib_name in HOOK_LIB_FILES
                if os.path.lexists(src_lib_dir / lib_name)
            )
    if args.with_cc_router:
        _validate_tree_links(claude_root / "router")
        sources.extend(_cc_router_files(repo_root / "template/claude/router", claude_root))
    for source, target in sources:
        _decode_utf8(source, _validate_path(source, directory=False, allow_hardlinks=True).read_bytes())
        original = _read_optional_bytes(target)
        if target.name == "CLAUDE.md":
            _decode_utf8(target, original or b"")


# 七、零写入升级体检


def _read_pe_file_version(binary_path: Path) -> Optional[str]:
    """Windows 纯文件只读提取原生 PE 可执行文件版本（零进程、不启动 GUI）。"""
    if os.name != "nt":
        return None
    try:
        if not binary_path.is_file() or _is_link_or_junction(binary_path):
            return None
        import ctypes
        import ctypes.wintypes as wt

        ver = ctypes.windll.version
        ver.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wt.DWORD
        ver.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID]
        ver.GetFileVersionInfoW.restype = wt.BOOL
        ver.VerQueryValueW.argtypes = [wt.LPCVOID, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.UINT)]
        ver.VerQueryValueW.restype = wt.BOOL

        path_str = str(binary_path)
        size = int(ver.GetFileVersionInfoSizeW(path_str, None))
        if size <= 0 or size > 4 * 1024 * 1024:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path_str, 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = wt.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        if not ptr.value or length.value < 52:
            return None
        vals = (ctypes.c_uint32 * 6).from_address(ptr.value)
        if vals[0] != 0xFEEF04BD:
            return None
        ms, ls = (vals[4] or vals[2]), (vals[5] or vals[3])
        if not (ms or ls):
            return None
        parts = [(ms >> 16) & 0xFFFF, ms & 0xFFFF, (ls >> 16) & 0xFFFF, ls & 0xFFFF]
        while len(parts) > 3 and parts[-1] == 0:
            parts.pop()
        return ".".join(str(part) for part in parts)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _read_dpkg_package_version(
    package_name: str, status_path: Path = Path("/var/lib/dpkg/status"),
) -> Optional[str]:
    """Debian/Ubuntu 纯文件只读解析 dpkg 已安装包版本（不启动 GUI 或子进程）。"""
    if os.name == "nt" or not package_name:
        return None
    try:
        if not status_path.is_file() or _is_link_or_junction(status_path) or status_path.stat().st_size > 64 * 1024 * 1024:
            return None
        target = package_name.strip().lower()
        cur_pkg = ""
        cur_installed = False
        cur_ver = ""
        with status_path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    if cur_pkg == target and cur_installed and cur_ver:
                        return cur_ver
                    cur_pkg, cur_installed, cur_ver = "", False, ""
                    continue
                if line.startswith("Package:"):
                    cur_pkg = line.split(":", 1)[1].strip().lower()
                elif line.startswith("Status:"):
                    cur_installed = "installed" in line.split(":", 1)[1].lower().split()
                elif line.startswith("Version:"):
                    cur_ver = line.split(":", 1)[1].strip()
        if cur_pkg == target and cur_installed and cur_ver:
            return cur_ver
    except OSError:
        return None
    return None


def _read_desktop_app_version(desktop_binary: Optional[str]) -> Optional[str]:
    """纯文件只读提取桌面应用包版本（不启动 GUI 进程）。"""
    if not desktop_binary:
        return None
    try:
        real_bin = Path(desktop_binary).resolve()
    except OSError:
        real_bin = Path(desktop_binary)
    def _append_bin_roots(bin_path: Path, out_roots: List[Path]) -> None:
        for r in ([bin_path.parent] + ([bin_path.parent.parent] if bin_path.parent.name.lower() == "bin" and bin_path.parent.parent != bin_path.parent else [])):
            if r not in out_roots:
                out_roots.append(r)

    candidate_roots: List[Path] = []
    _append_bin_roots(real_bin, candidate_roots)
    try:
        if real_bin.is_file() and not _is_link_or_junction(real_bin) and 0 < real_bin.stat().st_size <= 65536:
            head_bytes = real_bin.read_bytes()
            if head_bytes.startswith(b"#!"):
                script_txt = head_bytes.decode("utf-8", errors="ignore")
                bin_stem = re.escape(real_bin.stem)
                for m_exec in re.finditer(rf"((?:[A-Za-z]:)?(?:/[A-Za-z0-9_./-]+)+/{bin_stem})\b", script_txt, re.I):
                    exec_cand = Path(m_exec.group(1))
                    if os.name == "nt" or _is_posix_native_cli_path(str(exec_cand)):
                        try:
                            _append_bin_roots(exec_cand.resolve(), candidate_roots)
                        except OSError:
                            _append_bin_roots(exec_cand, candidate_roots)
    except OSError:
        pass
    for app_root in candidate_roots:
        res_dir = app_root / "resources"
        if _is_link_or_junction(res_dir):
            continue
        for rel, prefer_ide_only in (("resources/app/product.json", True), ("resources/app/package.json", False)):
            cand = app_root / rel
            try:
                if (
                    cand.is_file()
                    and not _is_link_or_junction(cand)
                    and not _is_link_or_junction(cand.parent)
                    and cand.stat().st_size <= 2 * 1024 * 1024
                ):
                    doc = json.loads(cand.read_text(encoding="utf-8-sig"))
                    if isinstance(doc, dict):
                        ver = doc.get("ideVersion") if prefer_ide_only else (doc.get("ideVersion") or doc.get("version"))
                        if isinstance(ver, str) and ver.strip():
                            return ver.strip()
            except (OSError, ValueError, TypeError, RecursionError):
                continue
        asar = res_dir / "app.asar"
        try:
            if asar.is_file() and not _is_link_or_junction(asar):
                with asar.open("rb") as handle:
                    header = handle.read(16)
                    if len(header) != 16:
                        continue
                    pickle0, header_size, _, json_len = struct.unpack("<IIII", header)
                    if pickle0 != 4 or not (0 < json_len <= 16 * 1024 * 1024 and json_len + 8 <= header_size <= 32 * 1024 * 1024):
                        continue
                    tree = json.loads(handle.read(json_len).decode("utf-8-sig"))
                    files = tree.get("files") if isinstance(tree, dict) else None
                    if not isinstance(files, dict):
                        continue
                    for entry_name, prefer_ide_only in (("product.json", True), ("package.json", False)):
                        pkg_entry = files.get(entry_name)
                        if not isinstance(pkg_entry, dict):
                            continue
                        offset = int(pkg_entry.get("offset", -1))
                        size = int(pkg_entry.get("size", -1))
                        if offset < 0 or not (0 < size <= 512 * 1024):
                            continue
                        handle.seek(8 + header_size + offset)
                        pkg = json.loads(handle.read(size).decode("utf-8-sig"))
                        if isinstance(pkg, dict):
                            ver = pkg.get("ideVersion") if prefer_ide_only else (pkg.get("ideVersion") or pkg.get("version"))
                            if isinstance(ver, str) and ver.strip():
                                return ver.strip()
        except (OSError, ValueError, TypeError, RecursionError, struct.error):
            continue
    if os.name == "nt" and real_bin.suffix.lower() == ".exe":
        pe_ver = _read_pe_file_version(real_bin)
        if pe_ver:
            return pe_ver
    if (
        os.name != "nt"
        and real_bin.name.lower() == "cc-switch"
        and real_bin.as_posix().startswith(("/usr/bin/", "/usr/sbin/", "/opt/"))
        and real_bin.is_file()
        and not _is_link_or_junction(real_bin)
    ):
        dpkg_ver = _read_dpkg_package_version("cc-switch")
        if dpkg_ver:
            return dpkg_ver
    return None


def _audit_cli(info: Dict[str, Any]) -> Dict[str, Any]:
    report = {key: info.get(key) for key in (
        "native_installed", "native_binary", "native_desktop_binary", "native_status", "interop_status",
    )}
    report["version"] = None
    report["desktop_version"] = _read_desktop_app_version(info.get("native_desktop_binary"))
    binary = info.get("native_binary")
    if binary:
        if "cc-switch" in str(binary).lower():
            offline_ver = _read_desktop_app_version(str(binary))
            if offline_ver:
                report["version"] = offline_ver
                if not report["desktop_version"]:
                    report["desktop_version"] = offline_ver
                return report
        try:
            cmd = (
                [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", binary, "--version"]
                if os.name == "nt" and str(binary).lower().endswith((".cmd", ".bat"))
                else [binary, "--version"]
            )
            options = dict(capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3)
            if "cc-switch" in str(binary).lower():
                isolated_home = tempfile.mkdtemp(prefix="cc-switch-audit-")
                isolated_env = dict(os.environ)
                for key in ("HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA"):
                    isolated_env[key] = isolated_home
                proc_handle = None
                try:
                    popen_kwargs: Dict[str, Any] = dict(
                        env=isolated_env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if os.name != "nt":
                        popen_kwargs["start_new_session"] = True
                    proc_handle = subprocess.Popen(cmd, **popen_kwargs)
                    try:
                        out, err = proc_handle.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        if os.name != "nt":
                            try:
                                os.killpg(proc_handle.pid, signal.SIGKILL)
                            except OSError:
                                proc_handle.kill()
                        else:
                            proc_handle.kill()
                        try:
                            proc_handle.communicate(timeout=2)
                        except (OSError, subprocess.SubprocessError):
                            pass
                        raise
                    proc = subprocess.CompletedProcess(cmd, proc_handle.returncode, out, err)
                finally:
                    for _attempt in range(3):
                        shutil.rmtree(isolated_home, ignore_errors=True)
                        if not os.path.exists(isolated_home):
                            break
                        time.sleep(0.05)
            else:
                proc = subprocess.run(cmd, **options)
            match = re.search(r"\b\d+(?:\.\d+){1,3}(?:-[A-Za-z0-9.]+)?\b", proc.stdout or "")
            if proc.returncode == 0 and match:
                report["version"] = match[0]
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return report


def _rules_core(raw: bytes) -> str:
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    return re.split(r"^##[ \t]+工具与[^\n]*", text, maxsplit=1, flags=re.MULTILINE)[0].rstrip()


def _rules_equal(actual_raw: bytes, expected_raw: bytes) -> bool:
    actual_text = actual_raw.decode("utf-8-sig").replace("\r\n", "\n")
    expected_text = expected_raw.decode("utf-8-sig").replace("\r\n", "\n")
    tail_start = _find_custom_rules_tail_start(actual_text)
    if tail_start is not None:
        return (actual_text[:tail_start].rstrip() == expected_text.rstrip()
                and _LOCAL_ENV_COLLISIONS.search(actual_text[tail_start:]) is None)
    return actual_text.rstrip() == expected_text.rstrip()


_SCIENTIFIC_ABI_PACKAGES = (
    "jax", "jaxlib", "jaxopt", "scipy", "matplotlib", "torch", "cupy", "mpi4py",
    "petsc4py", "h5py", "numba", "vtk", "gmsh", "fenics-dolfinx",
)


def _audit_host_toolchain(*, is_sandbox_run: bool, detection: Dict[str, Any]) -> Dict[str, Any]:
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    py_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    info: Dict[str, Any] = {
        "python_minor": py_minor,
        "python_isolated_env": in_venv,
        "uv_detected": bool(shutil.which("uv")),
        "git_version": None,
        "compiled_scientific_packages": [],
        "coupling_warnings": [],
    }
    if is_sandbox_run:
        return info
    git_bin = shutil.which("git")
    if git_bin:
        try:
            proc = subprocess.run(
                [git_bin, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2,
            )
            match = re.search(r"\b\d+(?:\.\d+){1,3}(?:\.[A-Za-z0-9_-]+)*\b", proc.stdout or "")
            if proc.returncode == 0 and match:
                info["git_version"] = match[0]
        except (OSError, subprocess.SubprocessError):
            pass
    found_pkgs: List[str] = []
    wanted = {name.lower(): name for name in _SCIENTIFIC_ABI_PACKAGES}
    try:
        seen: Set[str] = set()
        for dist in importlib.metadata.distributions():
            raw_name = (dist.metadata.get("Name") or "").strip().lower()
            if raw_name in wanted and raw_name not in seen:
                seen.add(raw_name)
                ver = (dist.version or "").strip()
                found_pkgs.append(f"{wanted[raw_name]}=={ver}" if ver else wanted[raw_name])
    except OSError:
        pass
    found_pkgs.sort()
    info["compiled_scientific_packages"] = found_pkgs
    if not in_venv and found_pkgs:
        pkg_preview = ", ".join(found_pkgs[:6]) + (" ..." if len(found_pkgs) > 6 else "")
        info["coupling_warnings"].append(
            f"全局 Python {py_minor} 环境已安装编译型科学计算包 ({pkg_preview}) 且被宿主全局钩子引用："
            f"同次版本补丁升级 ({py_minor}.x) 保持 ABI 兼容（升级前须先退出占用 python 的进程）；"
            f"严禁原地跨次版本覆盖至 Python {sys.version_info.major}.{sys.version_info.minor + 1}，"
            f"请用 uv / venv 隔离新版本以免破坏编译扩展与全局钩子。"
        )
    wsl_probe = detection.get("wsl_probe") if isinstance(detection.get("wsl_probe"), dict) else {}
    if os.name == "nt" and (wsl_probe.get("installed_distros") or wsl_probe.get("distros")):
        info["coupling_warnings"].append(
            "WSL2 内核更新 (wsl --update) 会重启正在运行的发行版实例，执行前须确认无后台 MPI / 求解器算例运行；"
            "严禁自动执行跨 LTS 发行版大版本升级 (do-release-upgrade)，以免破坏 gcc/gfortran/MPI/glibc 二进制兼容性。"
        )
    return info


def _audit_redact(value: Any, roots: List[Tuple[Path, str]], secrets: Set[str]) -> Any:
    if isinstance(value, dict):
        return {_audit_redact(key, roots, secrets): _audit_redact(item, roots, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_audit_redact(item, roots, secrets) for item in value]
    if not isinstance(value, str):
        return value
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            value = value.replace(secret, "<redacted>")
    for path, label in sorted(roots, key=lambda item: len(str(item[0])), reverse=True):
        for variant in {str(path), path.as_posix()}:
            value = re.sub(re.escape(variant), lambda match: label, value, flags=re.IGNORECASE if os.name == "nt" else 0)
    # URL 仅保留服务端点；查询串、片段与 userinfo 均不进入报告。
    def endpoint(match: re.Match) -> str:
        raw_u = match[0]
        trail_chars: List[str] = []
        while raw_u:
            ch = raw_u[-1]
            if ch in ".,;:!?}":
                trail_chars.append(ch)
                raw_u = raw_u[:-1]
            elif ch == ")" and raw_u.count(")") > raw_u.count("("):
                trail_chars.append(ch)
                raw_u = raw_u[:-1]
            elif ch == "]" and raw_u.count("]") > raw_u.count("["):
                trail_chars.append(ch)
                raw_u = raw_u[:-1]
            else:
                break
        trail = "".join(reversed(trail_chars))
        if not raw_u:
            return match[0]
        try:
            parsed = urlsplit(raw_u)
            host = parsed.hostname or ""
            if not host:
                return "<redacted-url>" + trail
            if ":" in host:
                host = "[" + host + "]"
            return f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port else "") + parsed.path + trail
        except ValueError:
            return "<redacted-url>" + trail

    value = re.sub(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----|[^\r\n]*)",
        "<redacted-private-key>",
        value,
    )
    value = re.sub(r"(?:https?|wss?|socks5h?|socks4a?)://[^\s\"'\x60<>]+", endpoint, value, flags=re.IGNORECASE)
    value = re.sub(r"(?i)(\b[a-z][a-z0-9+.-]*://)([^/\s\"'\x60<>@]+@)", r"\1<redacted>@", value)
    octet = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    value = re.sub(
        rf"(?<![0-9A-Za-z_.-])(?:10\.{octet}\.{octet}\.{octet}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.{octet}\.{octet}|172\.(?:1[6-9]|2\d|3[01])\.{octet}\.{octet}|192\.168\.{octet}\.{octet}|169\.254\.{octet}\.{octet})(?![0-9A-Za-z_.-])",
        "<private-ip>",
        value,
    )
    value = re.sub(
        r"(?i)(?<![0-9A-Fa-f:])(?:fe[89ab][0-9a-f]|f[cd][0-9a-f]{2}):[0-9a-f:]+(?![0-9A-Fa-f:])",
        "<private-ip>",
        value,
    )
    value = re.sub(
        r"(?i)(?:[A-Z]:[/\\]Users[/\\]|/mnt/[a-z]/Users[/]|(?:\\\\|//)wsl(?:\.localhost|\$)[/\\][^/\\\s\"'\x60|()（）\[\]{},;:]+[/\\]home[/\\]|/(?:home|Users)/)[^/\\\s\"'\x60|()（）\[\]{},;:]+",
        "<home>",
        value,
    )
    value = re.sub(
        r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9._-]{20,}|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9._-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[0-9A-Z]{16}|hf_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|(?:Bearer|Basic|Token|ApiKey)\s+[A-Za-z0-9._~+/=-]{6,})",
        "<redacted>",
        value,
    )
    return value


def _normalize_agy_configured_model(
    raw_configured: Any,
    ag_models: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str, Optional[int]]:
    raw = str(raw_configured or "").strip()
    if not raw:
        return "", "", None
    raw_low = raw.lower()
    matched_slug = ""
    explicit_win: Optional[int] = None
    for item in ag_models or ():
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        disp = str(item.get("display_name") or "").strip()
        if (slug and slug.lower() == raw_low) or (disp and disp.lower() == raw_low):
            matched_slug = slug
            explicit_win = _model_window(item)
            break
    source = matched_slug or raw
    effort = ""
    paren_match = re.match(r"^(.*?)\s*\(\s*(minimal|low|medium|high|xhigh|max)\s*\)\s*$", source, re.IGNORECASE)
    if paren_match:
        source = paren_match.group(1).strip()
        effort = paren_match.group(2).lower()
    normalized = re.sub(r"[\s_]+", "-", source.strip().lower()).strip("-")
    tier_match = AGY_SINGLE_EFFORT_RE.match(normalized)
    if tier_match:
        normalized = tier_match.group(1)
        if not effort:
            effort = tier_match.group(2).lower()
    if not effort and matched_slug:
        raw_paren = re.match(r"^(.*?)\s*\(\s*(minimal|low|medium|high|xhigh|max)\s*\)\s*$", raw, re.IGNORECASE)
        if raw_paren:
            effort = raw_paren.group(2).lower()
    return normalized, effort, explicit_win


def _agy_catalog_matches_model(slug: str, target_model: str) -> bool:
    slug_low = slug.strip().lower()
    target_low = target_model.strip().lower()
    if not slug_low or not target_low:
        return False
    if slug_low == target_low:
        return True
    tier_match = AGY_SINGLE_EFFORT_RE.match(slug_low)
    return bool(tier_match and tier_match.group(1) == target_low)


def _build_adaptive_meta_summary(
    fleet_profile: Dict[str, Any],
    ag: Dict[str, Any],
    cx: Dict[str, Any],
    cl: Dict[str, Any],
    repo_root: Optional[Path] = None,
    ag_models: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    adaptive_policy = _load_adaptive_policy_readonly(repo_root)
    if adaptive_policy is None:
        return {}
    ag_model_val, ag_effort_val, ag_win_val = _normalize_agy_configured_model(
        ag.get("configured_model"), ag_models,
    )
    cx_model_val = str(cx.get("model") or "").strip()
    cx_raw_win = cx.get("model_context_window")
    cx_win_val = cx_raw_win if isinstance(cx_raw_win, int) and not isinstance(cx_raw_win, bool) and cx_raw_win > 0 else None
    cx_effort_val = "" if (not cx_model_val and cx_win_val is None) else str(cx.get("model_reasoning_effort") or "").strip()
    cl_mapping = cl.get("model_mapping") or {}
    raw_cl_win = str(cl_mapping.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") or "").strip()
    cl_window = int(raw_cl_win) if raw_cl_win.isdigit() and int(raw_cl_win) > 0 else None
    cl_model_val = str(cl_mapping.get("ANTHROPIC_MODEL") or "").strip()
    cl_effort_val = "" if (not cl_model_val and cl_window is None) else str(cl.get("effortLevel") or "").strip()
    out: Dict[str, Any] = {}
    for cname, model_val, effort_val, win_val in (
        ("antigravity", ag_model_val, ag_effort_val, ag_win_val),
        ("codex", cx_model_val, cx_effort_val, cx_win_val),
        ("claude", cl_model_val, cl_effort_val, cl_window),
    ):
        if not model_val and win_val is None:
            effort_val = ""
        cr = adaptive_policy.resolve_adaptive_compaction_policy(
            fleet_profile, client=cname, model_name=model_val, effort=effort_val, explicit_window=win_val,
        )
        hg = adaptive_policy.resolve_adaptive_handoff_policy(
            fleet_profile, client=cname, model_name=model_val, effort=effort_val, explicit_window=win_val,
        )
        out[cname] = {
            "active_model": model_val or None,
            "effective_context_window": hg.get("effective_context_window"),
            "adaptive_inline_total_chars": cr.get("inline_total_chars"),
            "adaptive_handoff_mode": hg.get("default_mode"),
            "adaptive_compact_limit": hg.get("default_compact_limit"),
        }
    return out


def _audit_codex_hook_trust(
    codex_root: Path,
    codex_hooks_path: Path,
    codex_doc: Dict[str, Any],
    config: Dict[str, Any],
    *,
    codex_version: Any,
    is_sandbox_run: bool,
    hooks_aligned: bool = True,
) -> Dict[str, Any]:
    """只读核对 Codex config.toml 的 [hooks.state.*] 受管槽位覆盖情况（不复算 Codex 内部哈希）。"""
    def _norm_state_path(raw: str) -> str:
        cleaned = raw[4:] if raw.startswith("\\\\?\\") else raw
        if os.name == "nt":
            cleaned = cleaned.replace("/", "\\")
            return os.path.normcase(os.path.normpath(cleaned))
        return os.path.normpath(cleaned)

    managed_slots: List[Tuple[str, int, int]] = []
    seen_slots: Set[Tuple[str, int, int]] = set()
    hooks_map = codex_doc.get("hooks") if isinstance(codex_doc, dict) else None
    if codex_hooks_path.is_file() and isinstance(hooks_map, dict):
        for event, groups in hooks_map.items():
            if not isinstance(event, str) or not isinstance(groups, list):
                continue
            event_snake = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
            for g_idx, group in enumerate(groups):
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    continue
                for h_idx, handler in enumerate(group["hooks"]):
                    if not isinstance(handler, dict):
                        continue
                    try:
                        tokens = _codex_hook_tokens(handler)
                    except (ValueError, TypeError):
                        continue
                    for tok in tokens[1:]:
                        sname = Path(tok).name
                        if sname in CODEX_HOOK_ENTRIES and _same_nonlink_path(
                            Path(tok).expanduser(), codex_root / "hooks" / sname
                        ):
                            slot = (event_snake, g_idx, h_idx)
                            if slot not in seen_slots:
                                seen_slots.add(slot)
                                managed_slots.append(slot)
                            break

    hooks_cfg = config.get("hooks") if isinstance(config, dict) else None
    hooks_state = hooks_cfg.get("state") if isinstance(hooks_cfg, dict) else None
    state_table_present = isinstance(hooks_state, dict) and bool(hooks_state)
    expected_paths = {_norm_state_path(str(codex_hooks_path))}
    try:
        expected_paths.add(_norm_state_path(str(codex_hooks_path.resolve())))
    except OSError:
        pass
    trusted_slots: Set[Tuple[str, int, int]] = set()
    if isinstance(hooks_state, dict):
        for raw_key, entry in hooks_state.items():
            if not isinstance(raw_key, str) or not isinstance(entry, dict):
                continue
            trusted_hash = entry.get("trusted_hash")
            if not isinstance(trusted_hash, str) or not trusted_hash.strip():
                continue
            parts = raw_key.rsplit(":", 3)
            if len(parts) != 4:
                continue
            raw_path, ev_name, g_str, h_str = parts
            if not (g_str.isdigit() and h_str.isdigit()):
                continue
            if _norm_state_path(raw_path) in expected_paths:
                trusted_slots.add((ev_name.lower(), int(g_str), int(h_str)))

    missing_entries = [
        f"<codex>/hooks.json:{ev}:{g_idx}:{h_idx}"
        for ev, g_idx, h_idx in managed_slots
        if (ev, g_idx, h_idx) not in trusted_slots
    ]
    managed_count = len(managed_slots)
    trusted_count = managed_count - len(missing_entries)
    ver_tuple = tuple(int(part) for part in re.findall(r"\d+", str(codex_version or "")))
    trust_gate_active = state_table_present or (not is_sandbox_run and ver_tuple >= (0, 171, 0))
    if managed_count == 0 or not codex_hooks_path.is_file():
        status = "not_applicable"
    elif not missing_entries and hooks_aligned:
        status = "slots_recorded"
    elif trust_gate_active:
        status = "untrusted_or_partial"
    else:
        status = "unrecorded"
    return {
        "status": status,
        "hash_verified": False,
        "managed_count": managed_count,
        "trusted_count": trusted_count,
        "missing_entries": missing_entries,
        "state_table_present": state_table_present,
    }


def audit_upgrade_status(
    repo_root: Optional[Path] = None, target_plugin_dir: Optional[Path] = None,
    codex_root: Optional[Path] = None, claude_root: Optional[Path] = None,
    ccswitch_db: Optional[Path] = None, *, detection: Optional[Dict[str, Any]] = None,
    is_sandbox_run: bool = False, app_scope: str = "auto", expect_cc_router: bool = False,
    dsh_home: Optional[Path] = None,
) -> Dict[str, Any]:
    """只读本地资产、模型目录和卡片状态；不刷新目录、不修改配置。"""
    explicit_ag = target_plugin_dir is not None
    explicit_cx = codex_root is not None
    explicit_cl = claude_root is not None
    explicit_cc = ccswitch_db is not None
    explicit_dsh = dsh_home is not None
    repo_root = Path(repo_root or Path(__file__).resolve().parent)
    home = Path.home()
    target_plugin_dir = Path(target_plugin_dir or home / ".gemini/config/plugins/user-guard")
    codex_root = Path(codex_root or os.environ.get("CODEX_HOME") or home / ".codex")
    claude_root = Path(claude_root or os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    dsh_root = Path(dsh_home or os.environ.get("DSH_HOME") or home / ".dsh").expanduser().absolute()
    ccswitch_db = Path(ccswitch_db or home / ".cc-switch/cc-switch.db")
    target_plugin_dir, codex_root, claude_root, ccswitch_db = (
        path.expanduser().absolute() for path in (target_plugin_dir, codex_root, claude_root, ccswitch_db)
    )
    detection = detect_installed_apps() if detection is None else detection
    errors: Dict[str, str] = {}
    secrets: Set[str] = set()
    template = repo_root / "template/user-guard"

    def collect_secrets(doc: Any) -> None:
        if isinstance(doc, dict):
            for key, value in doc.items():
                if re.search(
                    r"(?i)(?:auth|api.?key|secret|password|passwd|passphrase|credential|bearer|(?:set[_-]?)?cookie|(?:^|[_-])token(?:$|[_-])|[a-z0-9]Token$|^token[A-Z])",
                    key,
                ):
                    is_pwd = bool(re.search(r"(?i)pass(?:w(?:or)?d|phrase)", key))
                    if (
                        isinstance(value, str)
                        and len(value.strip()) >= 6
                        and (not value.strip().isdigit() or is_pwd)
                        and value.strip().lower() not in {"via-router", "present", "missing", "placeholder", "n/a", "true", "false", "none", "null"}
                    ):
                        secrets.add(value)
                collect_secrets(value)
        elif isinstance(doc, list):
            for item in doc:
                collect_secrets(item)

    def read_json(path: Path, *, allow_hardlinks: bool = False) -> Dict[str, Any]:
        try:
            result = _read_json_object(path, _read_optional_bytes(path, allow_hardlinks=allow_hardlinks))
            collect_secrets(result)
            return result
        except (OSError, ValueError, RuntimeError) as exc:
            errors[str(path)] = type(exc).__name__
            return {}

    def read_raw(path: Path) -> Optional[bytes]:
        try:
            return _read_optional_bytes(path)
        except (OSError, ValueError, RuntimeError) as exc:
            errors[str(path)] = type(exc).__name__
            return None

    def drift(root: Path, sources: List[Tuple[Path, str]], *, core_rules: bool = False) -> List[str]:
        result = []
        for source, relative in sources:
            actual = read_raw(root / relative)
            expected = source.read_bytes()
            try:
                equal = actual is not None and (_rules_equal(actual, expected) if core_rules and relative.endswith(".md") and "/" not in relative else actual == expected)
            except UnicodeError:
                equal = False
            if not equal:
                result.append(relative)
        return result

    repo_version = read_json(template / "plugin.json", allow_hardlinks=True).get("version")
    ag = _audit_cli(detection.get("antigravity", {}))
    ag.update(plugin_exists=target_plugin_dir.is_dir(), installed_version=read_json(target_plugin_dir / "plugin.json").get("version"))
    version_tuple = lambda value: tuple(int(part) for part in re.findall(r"\d+", str(value or "")))
    ag["upgrade_recommended"] = version_tuple(ag["installed_version"]) < version_tuple(repo_version)
    ag_sources = [(source, source.relative_to(template).as_posix()) for source in _template_files(template)
                  if source.relative_to(template).as_posix() != "hooks.json"]
    ag["drift_files"] = drift(target_plugin_dir, ag_sources)
    def extend_stale_mirror_drift(drift_list: List[str], tpl_dir: Path, dst_dir: Path, rel_root: Path) -> None:
        try:
            for stale in _stale_mirror_files(tpl_dir, dst_dir):
                rel_posix = stale.relative_to(rel_root).as_posix()
                if rel_posix not in drift_list:
                    drift_list.append(rel_posix)
        except OSError as exc:
            errors[str(dst_dir)] = type(exc).__name__

    extend_stale_mirror_drift(ag["drift_files"], template, target_plugin_dir, target_plugin_dir)
    installed_ag_hooks = read_json(target_plugin_dir / "hooks.json")
    try:
        lifecycle = installed_ag_hooks["user-guard-lifecycle"]
        first = next(iter(lifecycle.values()))[0]
        prefix, sep, _ = first.get("hooks", [first])[0]["command"].partition(" ./hooks/")
        if (
            not sep
            or not _is_valid_hook_python_prefix(prefix)
            or installed_ag_hooks != json.loads(_agy_hooks_bytes((template / "hooks.json").read_bytes(), prefix))
        ):
            ag["drift_files"].append("hooks.json")
    except (KeyError, IndexError, TypeError, ValueError, StopIteration, AttributeError):
        ag["drift_files"].append("hooks.json")
    if target_plugin_dir.parent.name == "plugins" and target_plugin_dir.parent.parent.name == "config":
        gemini_root = target_plugin_dir.parent.parent.parent
    elif target_plugin_dir.parent.name == "plugins":
        gemini_root = target_plugin_dir.parent.parent
    else:
        gemini_root = target_plugin_dir.parent
    ag_settings = read_json(gemini_root / "antigravity-cli/settings.json", allow_hardlinks=True)
    ag_desktop = read_json(gemini_root / "config/config.json", allow_hardlinks=True).get("userSettings", {})
    desktop_model = ag_desktop.get("model") if isinstance(ag_desktop, dict) else None
    ag["configured_model"] = ag_settings.get("model") or desktop_model or read_json(gemini_root / "antigravity/settings.json", allow_hardlinks=True).get("model")
    ag_models = read_json(gemini_root / "antigravity-cli/cache/cae_models_cache.json", allow_hardlinks=True).get("models", [])
    if not ag_models:
        ag_models = read_json(gemini_root / "antigravity/cache/cae_models_cache.json", allow_hardlinks=True).get("models", [])
    if not isinstance(ag_models, list):
        ag_models = []
    ag_models = [item for item in ag_models if isinstance(item, dict) and isinstance(item.get("slug"), str)]
    fleet_profile = _load_fleet_profile(repo_root)
    cat_rules = fleet_profile.get("client_catalog_rules", {}) if isinstance(fleet_profile.get("client_catalog_rules"), dict) else {}
    agy_rules = cat_rules.get("agy", {}) if isinstance(cat_rules.get("agy"), dict) else {}
    cx_rules = cat_rules.get("codex", {}) if isinstance(cat_rules.get("codex"), dict) else {}
    cl_rules = cat_rules.get("claude", {}) if isinstance(cat_rules.get("claude"), dict) else {}
    retiring_patterns = [
        str(pat) for pat in ((fleet_profile.get("excluded_or_retiring") or {}).get("agy_retiring_patterns") or ()) if pat
    ]
    retiring_re = re.compile("|".join(f"(?:{pat})" for pat in retiring_patterns), re.I) if retiring_patterns else None

    ag["model_catalog"] = [{"slug": item["slug"], "generation": list(_model_generation(item["slug"])),
                             "declared_max_context_window": _model_window(item), "supported_efforts": _model_efforts(item)} for item in ag_models]
    agy_priorities = [str(tok).lower() for tok in (agy_rules.get("family_priority") or ("pro",))]
    eligible_agy, agy_light_tokens = _filter_agy_catalog_models(ag_models, agy_rules)
    non_light_agy = [item for item in eligible_agy if not (_agy_slug_tokens(item["slug"]) & agy_light_tokens)] or eligible_agy
    primary_agy_models: List[Dict[str, Any]] = []
    for fam_tok in agy_priorities:
        matched = [item for item in non_light_agy if fam_tok in _agy_slug_tokens(item["slug"])]
        if matched:
            primary_agy_models = matched
            break
    latest_primary_gen = max((_model_generation(item["slug"]) for item in primary_agy_models), default=())
    primary_agy_models = [item for item in primary_agy_models if _model_generation(item["slug"]) == latest_primary_gen]
    ag["recommended_primary_variants"] = [item["slug"] for item in primary_agy_models]
    ag["primary_supported_efforts"] = sorted(
        {item["slug"].rsplit("-", 1)[-1] for item in primary_agy_models if item["slug"].rsplit("-", 1)[-1] in EFFORT_ORDER},
        key=EFFORT_ORDER.index,
    )
    ag["recommended_carbon_variants"] = ag["recommended_primary_variants"]
    ag["carbon_supported_efforts"] = ag["primary_supported_efforts"]
    ag["retiring_models"] = [item["slug"] for item in ag_models if retiring_re and retiring_re.search(item["slug"])]
    ag["configured_model_retiring"] = bool(
        isinstance(ag["configured_model"], str)
        and retiring_re is not None
        and retiring_re.search(ag["configured_model"])
    )
    if ag["configured_model_retiring"] and ag["configured_model"] not in ag["retiring_models"]:
        ag["retiring_models"].append(ag["configured_model"])
    ag["catalog_source"] = "local_cache"

    cx = _audit_cli(detection.get("codex", {}))
    config_path = codex_root / "config.toml"
    try:
        config = _parse_toml(_decode_utf8(config_path, read_raw(config_path) or b""), config_path)
        collect_secrets(config)
    except ValueError as exc:
        errors[str(config_path)] = type(exc).__name__
        config = {}
    cx.update({key: config.get(key) for key in ("model", "model_reasoning_effort", "model_context_window")})
    cx_models = read_json(codex_root / "models_cache.json", allow_hardlinks=True).get("models", [])
    cx_models = [item for item in cx_models if isinstance(item, dict) and isinstance(item.get("slug"), str)] if isinstance(cx_models, list) else []
    workhorse_fams = [str(fam).lower() for fam in (cx_rules.get("workhorse_families") or ())]
    deep_fams = [str(fam).lower() for fam in (cx_rules.get("deep_tags") or ())]
    tracked_fams = list(dict.fromkeys([*workhorse_fams, *deep_fams]))
    by_fam = {fam: _latest_codex_model(cx_models, fam) for fam in tracked_fams}
    workhorse_latest = next((by_fam[fam] for fam in workhorse_fams if by_fam.get(fam) is not None), None)
    deep_latest = next((by_fam[fam] for fam in deep_fams if by_fam.get(fam) is not None), None)
    selected = workhorse_latest or deep_latest
    current = next((item for item in cx_models if item["slug"] == cx["model"]), None)
    fam_pattern = "|".join(re.escape(fam) for fam in tracked_fams) if tracked_fams else ""
    fam_match = re.fullmatch(rf"gpt-\d+(?:\.\d+)*-({fam_pattern})", str(cx["model"]), re.I) if fam_pattern else None
    family_latest = by_fam.get(fam_match.group(1).lower()) if fam_match else workhorse_latest
    latest = family_latest if (fam_match and cx_models) else (family_latest or selected)
    cx["latest_workhorse_slug"] = (workhorse_latest or {}).get("slug")
    cx["latest_deep_slug"] = (deep_latest or {}).get("slug")
    cx["latest_sol_slug"], cx["latest_astra_slug"] = cx["latest_workhorse_slug"], cx["latest_deep_slug"]
    cx["declared_max_context_window"] = _model_window(latest or {})
    cx["supported_efforts"] = _model_efforts(latest or {})
    reasons = []
    codex_interop_only = bool(
        not cx.get("native_installed")
        and (detection.get("codex", {}).get("interop_available") or cx.get("interop_status") == "executable_detected")
        and not cx_models
        and cx.get("model") is None
    )
    if latest is None:
        if not codex_interop_only:
            reasons.append("本地模型目录缺少评估清单中的主力模型系列，需刷新客户端目录后复查")
    else:
        if current is None or current["slug"] != latest["slug"]:
            reasons.append("当前默认模型与本地目录的最新同系列模型不一致")
        if cx["declared_max_context_window"] is not None and cx["model_context_window"] != cx["declared_max_context_window"]:
            reasons.append("声明上下文窗口未对齐")
        best_effort = max(cx["supported_efforts"], key=EFFORT_ORDER.index, default=None)
        if best_effort is None or cx["model_reasoning_effort"] != best_effort:
            reasons.append("思考强度未对齐目录中的最高单模型挡位")
    cx.update(model_aligned=not reasons, upgrade_recommended=bool(reasons), alignment_reasons=reasons,
              catalog_source="interop_delegated" if codex_interop_only else "local_cache")
    codex_doc = read_json(codex_root / "hooks.json")
    cx_sources = [(template / "rules/AGENTS.md", "AGENTS.md")]
    cx_sources += [(source, "agents/" + source.name) for source in sorted((repo_root / "template/codex/agents").glob("*.toml"))]
    cx_sources += [(template / "hooks" / name, "hooks/" + name) for name in _codex_hook_script_names()]
    cx_sources += [(template / "hooks" / "_lib" / lib_name, "hooks/_lib/" + lib_name) for lib_name in HOOK_LIB_FILES if (template / "hooks" / "_lib" / lib_name).is_file()]
    skill_sources = [(source, source.relative_to(template).as_posix()) for source in _template_files(template / "skills")]
    cx_sources += skill_sources
    managed_skill_names = _managed_skill_names(template)
    cx["drift_files"] = drift(codex_root, cx_sources, core_rules=True)
    for sname in managed_skill_names:
        extend_stale_mirror_drift(cx["drift_files"], template / "skills" / sname, codex_root / "skills" / sname, codex_root)
    codex_hooks_path = codex_root / "hooks.json"
    try:
        cx_plan = _plan_codex_hooks(
            codex_hooks_path, repo_root / "template/codex/hooks.json", "python3 -X utf8 -I -B",
            verify_interpreters=False, repo_root=repo_root,
        )
        expected_cx_doc = json.loads(cx_plan.change.content.decode("utf-8-sig")) if cx_plan.change else codex_doc

        def _hook_exe_exists_if_abs(exe_tok: str) -> bool:
            if os.name == "nt":
                return not PureWindowsPath(exe_tok).is_absolute() or Path(exe_tok).is_file()
            return not Path(exe_tok).is_absolute() or (_is_posix_native_cli_path(exe_tok) and Path(exe_tok).is_file())

        def codex_hook_layout(doc: Dict[str, Any], *, check_exe: bool = False) -> List[Any]:
            result = []
            for event, groups in doc.get("hooks", {}).items():
                for group in groups:
                    for handler in group["hooks"]:
                        tokens = _codex_hook_tokens(handler)
                        for idx, tok in enumerate(tokens[1:], start=1):
                            sname = Path(tok).name
                            if sname in CODEX_HOOK_ENTRIES and _same_nonlink_path(
                                Path(tok).expanduser(), codex_root / "hooks" / sname
                            ):
                                prefix_end = 2 if tokens[1:2] in PY_LAUNCHER_ARGS else 1
                                exe_ok = (not check_exe) or (
                                    _is_valid_python_exe_name(PureWindowsPath(tokens[0]).name)
                                    and _hook_exe_exists_if_abs(tokens[0])
                                )
                                result.append((event, group.get("matcher"), sname, tokens[prefix_end:idx], tokens[idx + 1:], exe_ok))
                                break
            return sorted(result, key=repr)

        cx["hooks_aligned"] = codex_hooks_path.is_file() and codex_hook_layout(codex_doc, check_exe=True) == codex_hook_layout(expected_cx_doc, check_exe=False)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        cx["hooks_aligned"] = False
    if not cx["hooks_aligned"]:
        cx["drift_files"].append("hooks.json")
    cx["hooks_trust"] = _audit_codex_hook_trust(
        codex_root, codex_hooks_path, codex_doc, config,
        codex_version=cx.get("version"), is_sandbox_run=is_sandbox_run,
        hooks_aligned=cx["hooks_aligned"],
    )

    cl = _audit_cli(detection.get("claude", {}))
    settings_path = claude_root / "settings.json"
    settings = read_json(settings_path)
    env = settings.get("env", {})
    env = env if isinstance(env, dict) else {}
    cl.update(effortLevel=settings.get("effortLevel"), locked_effort_env="CLAUDE_CODE_EFFORT_LEVEL" in env,
              model_mapping={key: env.get(key) for key in ("ANTHROPIC_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_MAX_CONTEXT_TOKENS")})
    try:
        plan = _plan_claude_settings(settings_path, repo_root / "template/claude/hooks.json", "python3 -X utf8 -I -B",
                                    include_model=False, verify_interpreters=False, repo_root=repo_root)
        expected_doc = json.loads(plan.change.content.decode("utf-8-sig")) if plan.change else settings
        def hook_layout(doc: Dict[str, Any], *, check_exe: bool = False) -> List[Any]:
            result = []
            for event, groups in doc.get("hooks", {}).items():
                for group in groups:
                    for handler in group["hooks"]:
                        name = _claude_managed_script(handler, claude_root)
                        if name:
                            tokens = _claude_hook_tokens(handler)
                            prefix_end = 2 if tokens[1:2] in PY_LAUNCHER_ARGS else 1
                            exe_ok = (not check_exe) or (
                                (not (os.name != "nt" and Path(tokens[0]).is_absolute()) or (_is_posix_native_cli_path(tokens[0]) and Path(tokens[0]).is_file()))
                                and (not (os.name == "nt" and PureWindowsPath(tokens[0]).is_absolute()) or Path(tokens[0]).is_file())
                            )
                            result.append((event, group.get("matcher"), name, tokens[prefix_end:], "args" in handler, exe_ok))
            return sorted(result, key=repr)
        cl["hooks_aligned"] = settings_path.is_file() and hook_layout(settings, check_exe=True) == hook_layout(expected_doc, check_exe=False)
    except (OSError, ValueError, TypeError, KeyError):
        cl["hooks_aligned"] = False
    cl_sources = [(template / "rules/AGENTS.md", "CLAUDE.md")]
    cl_sources += [(source, "agents/" + source.name) for source in sorted((repo_root / "template/claude/agents").glob("readonly-*.md"))]
    cl_sources += [(template / "hooks" / name, "hooks/" + name) for name in _claude_hook_script_names()]
    cl_sources += [(template / "hooks" / "_lib" / lib_name, "hooks/_lib/" + lib_name) for lib_name in HOOK_LIB_FILES if (template / "hooks" / "_lib" / lib_name).is_file()]
    cl_sources += [(repo_root / "template/claude/router/ccswitch_admin.py", "hooks/ccswitch_admin.py")]
    cl_sources += skill_sources
    cl["drift_files"] = drift(claude_root, cl_sources, core_rules=True)
    cl["drift_files"].extend("hooks/" + name for name in CLAUDE_RETIRED_SCRIPTS if read_raw(claude_root / "hooks" / name) is not None)
    for sname in managed_skill_names:
        extend_stale_mirror_drift(cl["drift_files"], template / "skills" / sname, claude_root / "skills" / sname, claude_root)
    if not cl["hooks_aligned"]:
        cl["drift_files"].append("settings.json")
    router_sources = [(source, source.name) for source in _template_files(repo_root / "template/claude/router")]
    router_drift = drift(claude_root / "router", router_sources)
    cl["router"] = {"installed": (claude_root / "router/router.py").is_file(), "drift_files": router_drift,
                    "config_exists": (claude_root / "router/router.json").is_file(),
                    "static_timeout_override": None,
                    "template_hashes": {relative: hashlib.sha256(source.read_bytes()).hexdigest() for source, relative in router_sources}}

    dsh_det = detection.get("dsh", {})
    dsh = _audit_cli(dsh_det)
    dsh["hook_protocol"] = dsh_det.get("hook_protocol", "soft_rules_only")
    dsh["hard_hooks_supported"] = bool(dsh_det.get("hard_hooks_supported", False))
    dsh["meta_update_trigger"] = dsh_det.get("meta_update_trigger", "awaiting_upstream_dsh_hooks")
    dsh["drift_files"] = drift(dsh_root, [(template / "rules/AGENTS.md", "AGENTS.md"), *skill_sources], core_rules=True)
    for sname in managed_skill_names:
        extend_stale_mirror_drift(dsh["drift_files"], template / "skills" / sname, dsh_root / "skills" / sname, dsh_root)
    dsh["credentials_present"] = any((dsh_root / name).is_file() for name in (".credentials.yaml", ".env"))
    if app_scope == "all":
        ag_active, cx_active, cl_active = True, True, True
        dsh_active = bool(explicit_dsh or (not is_sandbox_run and (dsh.get("native_installed") or dsh_root.is_dir())))
    elif app_scope == "both":
        ag_active, cx_active, cl_active, dsh_active = True, True, False, False
    elif app_scope == "antigravity":
        ag_active, cx_active, cl_active, dsh_active = True, False, False, False
    elif app_scope == "codex":
        ag_active, cx_active, cl_active, dsh_active = False, True, False, False
    elif app_scope == "claude":
        ag_active, cx_active, cl_active, dsh_active = False, False, True, False
    elif app_scope == "dsh":
        ag_active, cx_active, cl_active, dsh_active = False, False, False, True
    elif explicit_ag or explicit_cx or explicit_cl or explicit_dsh:
        ag_active, cx_active, cl_active, dsh_active = explicit_ag, explicit_cx, explicit_cl, explicit_dsh
    else:
        ag_active = bool(ag.get("native_installed") or target_plugin_dir.is_dir())
        cx_active = bool(cx.get("native_installed") or codex_root.is_dir())
        cl_active = bool(cl.get("native_installed") or claude_root.is_dir())
        dsh_active = bool(not is_sandbox_run and (dsh.get("native_installed") or dsh_root.is_dir()))
        if not (ag_active or cx_active or cl_active or dsh_active):
            ag_active = cx_active = cl_active = True

    for peer_report, is_active, peer_dir in (
        (ag, ag_active, target_plugin_dir),
        (cx, cx_active, codex_root),
        (cl, cl_active, claude_root),
        (dsh, dsh_active, dsh_root),
    ):
        peer_report["active"] = is_active
        if not is_active and not peer_dir.is_dir():
            peer_report["drift_files"] = []

    cc = _audit_cli(detection.get("cc_switch", {}))
    tunables = fleet_profile.get("runtime_tunables") if isinstance(fleet_profile.get("runtime_tunables"), dict) else {}
    hd_tunables = tunables.get("headless_dispatch") if isinstance(tunables.get("headless_dispatch"), dict) else {}
    router_tunables = hd_tunables.get("router") if isinstance(hd_tunables.get("router"), dict) else {}
    default_port_key = "port" if os.name == "nt" else "wsl_port"
    fallback_port = 8240 if os.name == "nt" else 8241
    raw_default_port = router_tunables.get(default_port_key, fallback_port)
    port = (
        raw_default_port
        if isinstance(raw_default_port, int) and not isinstance(raw_default_port, bool) and 1 <= raw_default_port <= 65535
        else fallback_port
    )
    router_cfg_path = claude_root / "router/router.json"
    if router_cfg_path.is_file():
        custom_router_cfg = read_json(router_cfg_path)
        if isinstance(custom_router_cfg, dict):
            custom_port = custom_router_cfg.get("port")
            if isinstance(custom_port, int) and not isinstance(custom_port, bool) and 1 <= custom_port <= 65535:
                port = custom_port
            for timeout_key in ("timeout_s", "timeout_sec"):
                raw_timeout = custom_router_cfg.get(timeout_key)
                if isinstance(raw_timeout, int) and not isinstance(raw_timeout, bool) and raw_timeout > 0:
                    cl["router"]["static_timeout_override"] = raw_timeout
                    break

    def _url_points_to_local_router(raw_url: Any, expected_port: int) -> bool:
        if not isinstance(raw_url, str) or not raw_url.strip():
            return False
        try:
            parsed = urlsplit(raw_url.strip())
        except ValueError:
            return False
        return (
            parsed.scheme.lower() in {"http", "https"}
            and (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
            and parsed.port == expected_port
        )

    settings_uses_router = _url_points_to_local_router(env.get("ANTHROPIC_BASE_URL"), port)
    card_uses_router = False
    inspect_cc_db = not is_sandbox_run or explicit_cc
    if inspect_cc_db:
        try:
            admin = _load_ccswitch_admin(repo_root)
            probe_port = port if (cl_active and settings_uses_router) else None
            cc.update(admin.inspect_status(ccswitch_db, settings_path, router_port=probe_port))
            current_card = cc.get("current_claude_card")
            card_uses_router = _url_points_to_local_router((current_card or {}).get("base_url"), port) if isinstance(current_card, dict) else False
            uses_local_router = card_uses_router if isinstance(current_card, dict) else settings_uses_router
            if cl_active and uses_local_router and probe_port is None:
                cc.update(admin.inspect_status(ccswitch_db, settings_path, router_port=port))
            elif not uses_local_router:
                cc.pop("router_health", None)
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            cc.update(db_exists=ccswitch_db.is_file(), schema_version=None, enabled_prompts=[], enabled_skills=[], inspection_error=type(exc).__name__)
            uses_local_router = settings_uses_router
    else:
        cc.update(
            db_exists=False, schema_version=None, cards=[], current_claude_card=None,
            enabled_prompts=[], stored_prompt_count=0, enabled_skills=[], stored_skill_count=0,
            skills_dir_entries=[], settings_aligned=None,
        )
        uses_local_router = settings_uses_router
    matrix = fleet_profile.get("subscription_matrix", {})
    matrix = matrix if isinstance(matrix, dict) else {}
    subscription_candidates: Dict[str, Any] = {"agents": {}, "models": {}}
    peer_reports = {"agy": ag, "codex": cx, "claude": cl, "dsh": dsh}
    for client, entry in (matrix.get("agents") or {}).items():
        if not isinstance(entry, dict):
            continue
        detected = detection.get("antigravity" if client == "agy" else client, {})
        subscription_candidates["agents"][client] = {
            "enabled_in_profile": bool(entry.get("enabled", entry.get("enabled_in_profile", False))),
            "currently_available": bool(peer_reports.get(client, {}).get("native_installed")
                                        or detected.get("interop_available")),
            "quota_scheme": entry.get("quota_scheme"),
        }
    catalogs = {"agy": ag_models, "codex": cx_models}
    for model, entry in (matrix.get("models") or {}).items():
        if not isinstance(entry, dict):
            continue
        client = entry.get("agent")
        available = subscription_candidates["agents"].get(client, {}).get("currently_available", False)
        scheme = entry.get("quota_scheme")
        detected_client = detection.get("antigravity" if client == "agy" else str(client), {})
        interop_delegated = bool(
            not peer_reports.get(str(client), {}).get("native_installed") and detected_client.get("interop_available")
        )
        if entry.get("card"):
            cards = [card for card in (cc.get("cards") or []) if isinstance(card, dict)
                     and card.get("app_type") == client and card.get("name") == entry["card"]]
            ready_cards = [card for card in cards if not card.get("invalid_json")
                           and card.get("token") in {"present", "via-router"}]
            available = available and bool(ready_cards)
            scheme = next((card["quota_scheme"] for card in ready_cards if card.get("quota_scheme")), scheme)
        elif client in catalogs:
            available = available and (
                (interop_delegated and not catalogs[client])
                or any(
                    item.get("visibility") != "hide"
                    and (
                        _agy_catalog_matches_model(item["slug"], str(model))
                        if client == "agy"
                        else item["slug"] == model
                    )
                    for item in catalogs[client]
                )
            )
        elif client == "dsh":
            available = available and (
                dsh["credentials_present"] or bool(os.environ.get("DEEPSEEK_API_KEY")) or interop_delegated
            )
        subscription_candidates["models"][model] = {
            "agent": client, "enabled_in_profile": bool(entry.get("enabled", entry.get("enabled_in_profile", False))),
            "currently_available": bool(available), "quota_scheme": scheme,
        }
    managed_card_names = {str(name) for name in (cl_rules.get("managed_cards") or ("Kimi", "DeepSeek", "K3.1主+DS子代理")) if name}
    custom_card_refs: List[Tuple[str, str]] = []
    _card_uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    for card in cc.get("cards") or []:
        if not isinstance(card, dict):
            continue
        if card.get("app_type") != "claude" or card.get("name") not in managed_card_names:
            if isinstance(card.get("app_type"), str) and isinstance(card.get("name"), str) and card["name"]:
                custom_card_refs.append((card["app_type"], card["name"]))
            card["id"] = "<custom-card-id>"
            card["name"] = "<custom-card>"
        elif isinstance(card.get("id"), str) and _card_uuid_re.fullmatch(card["id"]):
            card["id"] = "<managed-card-id>"
    if isinstance(cc.get("current_claude_card"), dict):
        if cc["current_claude_card"].get("name") not in managed_card_names:
            cc["current_claude_card"]["id"] = "<custom-card-id>"
            cc["current_claude_card"]["name"] = "<custom-card>"
        elif isinstance(cc["current_claude_card"].get("id"), str) and _card_uuid_re.fullmatch(cc["current_claude_card"]["id"]):
            cc["current_claude_card"]["id"] = "<managed-card-id>"
    if custom_card_refs and isinstance(cc.get("warnings"), list):
        ordered_refs = sorted(set(custom_card_refs), key=lambda item: len(item[1]), reverse=True)
        ref_re = re.compile("|".join(re.escape(f"{app_t}:{raw_name}") for app_t, raw_name in ordered_refs))
        cc["warnings"] = [
            ref_re.sub(lambda m: m.group(0).split(":", 1)[0] + ":<custom-card>", str(warn))
            for warn in cc["warnings"]
        ]
    for prompt_row in cc.get("enabled_prompts") or []:
        if isinstance(prompt_row, dict):
            prompt_row["id"] = "<custom-prompt-id>"
            prompt_row["name"] = "<custom-prompt>"
    for skill_row in cc.get("enabled_skills") or []:
        if isinstance(skill_row, dict):
            skill_row["id"] = "<custom-skill-id>"
            skill_row["name"] = "<custom-skill>"
    if isinstance(cc.get("skills_dir_entries"), list):
        cc["skills_dir_entries"] = ["<custom-skill-dir>" for _ in cc["skills_dir_entries"]]
    cc["default_router_port"] = port
    cc["legacy_residue_present"] = bool(
        cc.get("stored_prompt_count") or cc.get("stored_skill_count") or cc.get("skills_dir_entries")
    )
    router_active = cl_active and bool(
        expect_cc_router or cl["router"]["installed"] or cl["router"]["config_exists"] or uses_local_router
    )
    cc_active = cl_active and inspect_cc_db and bool(not is_sandbox_run or ccswitch_db.is_file())
    actions = []
    if ag_active:
        if ag["drift_files"] or ag["upgrade_recommended"]:
            actions.append("sync_antigravity_plugin")
        if ag["configured_model_retiring"]:
            actions.append("review_antigravity_models")
    if cx_active:
        if cx["drift_files"]:
            actions.append("sync_codex")
        if cx["upgrade_recommended"]:
            actions.append("sync_codex_model_defaults" if latest else "refresh_codex_model_catalog")
        if (cx.get("hooks_trust") or {}).get("status") == "untrusted_or_partial":
            actions.append("review_codex_hook_trust")
    if cl_active:
        if cl["drift_files"]:
            actions.append("sync_claude")
        if cl["effortLevel"] != _default_claude_effort(repo_root) or cl["locked_effort_env"]:
            actions.append("sync_claude_model_defaults")
    if dsh_active:
        if dsh["drift_files"]:
            actions.append("sync_dsh")
        if dsh.get("hard_hooks_supported"):
            actions.append("upgrade_dsh_hook_protocol")
    if router_active and (router_drift or not cl["router"]["config_exists"]):
        actions.append("sync_cc_router")
    if cc_active:
        if cc.get("enabled_prompts") or cc.get("enabled_skills"):
            actions.append("disable_ccswitch_prompts")
        if cc.get("settings_aligned") is False:
            actions.append("align_ccswitch_card")
        if uses_local_router and cc.get("router_health") and not cc["router_health"].get("ok"):
            actions.append("start_cc_router")
        if cc.get("inspection_error"):
            actions.append("inspect_ccswitch_schema")
    if errors:
        actions.append("repair_unreadable_configs")
    toolchain = _audit_host_toolchain(is_sandbox_run=is_sandbox_run, detection=detection)
    eval_policy = fleet_profile.get("evaluation_policy") if isinstance(fleet_profile.get("evaluation_policy"), dict) else {}
    native_profiles = fleet_profile.get("native_agent_profiles") if isinstance(fleet_profile.get("native_agent_profiles"), dict) else {}
    wsl_probe_data = detection.get("wsl_probe") if isinstance(detection.get("wsl_probe"), dict) else {}
    peer_apps_detected: Dict[str, bool] = {}
    peer_probe_status = "not_applicable"
    stopped_wsl_distros: List[str] = []
    if os.name == "nt":
        peer_probe_status = str(wsl_probe_data.get("status") or "unknown")
        installed_distros = [d for d in (wsl_probe_data.get("installed_distros") or []) if isinstance(d, str)]
        running_set = {d for d in (wsl_probe_data.get("running_distros") or []) if isinstance(d, str)}
        stopped_wsl_distros = [d for d in installed_distros if d not in running_set]
        if isinstance(wsl_probe_data.get("apps"), dict):
            wsl_apps = wsl_probe_data["apps"]
            wsl_key_map = {
                "antigravity": ("agy", "antigravity"),
                "codex": ("codex",),
                "claude": ("claude",),
                "dsh": ("dsh",),
                "cc_switch": ("cc-switch", "cc_switch"),
            }
            for app_key, aliases in wsl_key_map.items():
                entries = next((wsl_apps[k] for k in aliases if k in wsl_apps), [])
                peer_apps_detected[app_key] = bool(
                    isinstance(entries, list)
                    and any(
                        isinstance(item, dict)
                        and (item.get("binary") or item.get("desktop_binary") or item.get("config_dir_exists"))
                        for item in entries
                    )
                )
    elif detection.get("is_wsl"):
        peer_statuses = []
        for app_key in ("antigravity", "codex", "claude", "dsh", "cc_switch"):
            app_det = detection.get(app_key) or {}
            peer_apps_detected[app_key] = bool(app_det.get("interop_available"))
            peer_statuses.append(app_det.get("interop_status"))
        peer_probe_status = "checked" if any(st and st != "unknown" for st in peer_statuses) else "unknown"
    peer_side_present = bool(any(peer_apps_detected.values()))
    peer_audit_hint = None
    if not is_sandbox_run:
        if peer_side_present:
            peer_audit_hint = (
                "检测到对侧环境存在客户端或配置，全面更新须在 Windows 与 WSL2 两侧分别执行 install.py --audit-upgrade 与同步（不依赖单侧覆盖）。"
            )
        elif peer_probe_status == "unknown" and stopped_wsl_distros:
            peer_audit_hint = (
                "存在未启动的 WSL2 发行版（只读探测不自动拉起），请启动目标 WSL2 发行版后在对侧单独执行 install.py --audit-upgrade 核对双端配置。"
            )
    cross_side_status = {
        "current_side": "windows" if os.name == "nt" else ("wsl" if detection.get("is_wsl") else "posix"),
        "peer_side": "wsl" if os.name == "nt" else ("windows" if detection.get("is_wsl") else None),
        "peer_probe_status": peer_probe_status,
        "peer_side_present": peer_side_present,
        "peer_apps_detected": peer_apps_detected,
        "dual_side_sync_required": bool(not is_sandbox_run and peer_side_present),
        "peer_audit_hint": peer_audit_hint,
    }
    report = {"plugin_version": repo_version,
              "fleet_profile": {
                  "profile_id": fleet_profile.get("profile_id"),
                  "evaluated_at": fleet_profile.get("evaluated_at"),
                  "all_roles_dynamic": bool(eval_policy.get("all_roles_dynamic", True)),
                  "dynamic_tunables_enabled": bool(eval_policy.get("dynamic_tunables_enabled", isinstance(fleet_profile.get("runtime_tunables"), dict))),
                  "native_agent_profiles_configured": sorted(k for k in native_profiles if isinstance(k, str)),
              },
              "host": {"platform": sys.platform, "python_version": sys.version.split()[0], "is_wsl": detection.get("is_wsl"),
                       "wsl_probe": detection.get("wsl_probe"), "toolchain": toolchain},
              "cross_side_status": cross_side_status,
              "antigravity": ag, "codex": cx, "claude": cl, "dsh": dsh, "cc_switch": cc,
              "subscription_candidates": subscription_candidates,
              "summary": {"all_aligned": not actions, "recommended_actions": actions},
              "adaptive_meta": _build_adaptive_meta_summary(
                  fleet_profile, ag, cx, cl, repo_root=repo_root, ag_models=ag_models,
              ),
              "read_errors": errors}
    roots = [(home, "~"), (target_plugin_dir, "<antigravity>"), (codex_root, "<codex>"), (claude_root, "<claude>"),
             (dsh_root, "<dsh>"), (ccswitch_db.parent, "<ccswitch>"), (repo_root, "<repo>")]
    if detection.get("is_wsl") and not is_sandbox_run:
        win_home = _probe_wsl_windows_profile_dir()
        if win_home is not None:
            roots.append((win_home, "<home>"))
    return _audit_redact(report, roots, secrets)


# 八、CLI 参数、预演与主安装流程


def _confirm_single_app_install(
    installed_label: str,
    missing_label: str,
    interactive: bool,
    auto_yes: bool,
    dry_run: bool,
) -> bool:
    """当本机仅检测到单客户端时提示用户：是否先去安装另一端，或不安装直接单独配置当前客户端"""
    print("-" * 80)
    print(f"[!] 单客户端提示：检测到本机已安装 {installed_label}，未检测到 {missing_label}。")
    print(f"    - 若希望启用多端协同（反重力 / Codex / Claude Code / DSH），可先退出安装 {missing_label} 后再运行本脚本；")
    print(f"    - 若暂不安装 {missing_label}，可直接单独配置 {installed_label} 的完整独立工作流。")
    if dry_run or auto_yes or not interactive:
        print(f"[*] 当前为非交互或自动确认模式，将仅针对已安装的 {installed_label} 生成/执行独立配置。")
        return True
    try:
        answer = input(f"是否不安装 {missing_label}，直接单独配置本机 {installed_label} 独立工作流？[Y/n]: ").strip().lower()
    except EOFError:
        return True
    if answer in ("n", "no", "0", "false"):
        print(f"[*] 已按选择取消本次配置。待安装 {missing_label} 后重新运行即可一键完成双端配置。")
        return False
    return True


def resolve_app_plan(
    app_arg: str,
    has_explicit_ag_target: bool,
    has_explicit_cx_target: bool,
    detection: Dict[str, Any],
    interactive: bool,
    auto_yes: bool,
    dry_run: bool,
) -> Tuple[bool, bool]:
    """返回 (install_antigravity, install_codex)"""
    if app_arg == "both":
        return True, True
    if app_arg == "antigravity":
        return True, False
    if app_arg == "codex":
        return False, True

    # app_arg == "auto"
    if has_explicit_ag_target or has_explicit_cx_target:
        return bool(has_explicit_ag_target), bool(has_explicit_cx_target)

    ag_info = detection["antigravity"]
    cx_info = detection["codex"]
    ag_native = bool(ag_info["native_installed"])
    cx_native = bool(cx_info["native_installed"])

    if ag_native and cx_native:
        return True, True

    if ag_native and not cx_native:
        if cx_info["interop_available"]:
            print(
                "[*] 检测到本侧反重力与另一子系统的 Codex。"
                "本次同步本侧反重力，请在另一子系统的终端配置 Codex。"
            )
            return True, False
        if cx_info.get("interop_status") == "unknown":
            print("[*] 另一子系统的 Codex 安装状态未确认，本次只配置本侧反重力。")
            return True, False
        if not _confirm_single_app_install("反重力 (Antigravity)", "Codex", interactive, auto_yes, dry_run):
            sys.exit(0)
        return True, False

    if cx_native and not ag_native:
        if ag_info["interop_available"]:
            print(
                "[*] 检测到本侧已安装 Codex，且跨子系统一侧已安装反重力；"
                "本次将同步本侧 Codex 配置（另一侧反重力端配置可在对应终端运行安装脚本更新）。"
            )
            return False, True
        if ag_info.get("interop_status") == "unknown":
            print("[*] 另一子系统的反重力安装状态未确认，本次只配置本侧 Codex。")
            return False, True
        if not _confirm_single_app_install("Codex", "反重力 (Antigravity)", interactive, auto_yes, dry_run):
            sys.exit(0)
        return False, True

    print(
        "[!] 未在本侧检测到反重力（Antigravity）或 Codex 可执行文件。\n"
        "    请先安装至少一个客户端，或显式指定 --app codex / --app antigravity / --app both 预置配置目录。",
        file=sys.stderr,
    )
    sys.exit(2)


def resolve_multi_app_plan(
    app_arg: str, has_explicit_ag: bool, has_explicit_cx: bool, has_explicit_cl: bool,
    detection: Dict[str, Any], interactive: bool, auto_yes: bool, dry_run: bool,
) -> Tuple[bool, bool, bool]:
    explicit = {"all": (True, True, True), "both": (True, True, False),
                "antigravity": (True, False, False), "codex": (False, True, False), "claude": (False, False, True)}
    if app_arg in explicit:
        return explicit[app_arg]
    if has_explicit_ag or has_explicit_cx or has_explicit_cl:
        return bool(has_explicit_ag), bool(has_explicit_cx), bool(has_explicit_cl)
    names = ("antigravity", "codex", "claude")
    installed = tuple(bool(detection.get(name, {}).get("native_installed")) for name in names)
    if sum(installed) >= 2:
        return installed
    if not any(installed):
        print("[!] 未检测到本侧客户端，请先安装客户端或显式指定 --app。", file=sys.stderr)
        sys.exit(2)
    missing = [name for name, native in zip(names, installed) if not native]
    if any(detection.get(name, {}).get("interop_available") or detection.get(name, {}).get("interop_status") == "unknown" for name in missing):
        print("[*] 对侧客户端可用或状态未确认，本次只配置已检测到的本侧客户端。")
        return installed
    app_labels = {"antigravity": "反重力 (Antigravity)", "codex": "Codex", "claude": "Claude Code"}
    label = app_labels.get(names[installed.index(True)], names[installed.index(True)])
    missing_labels = [app_labels.get(item, item) for item in missing]
    dsh_info = detection.get("dsh", {}) if isinstance(detection.get("dsh"), dict) else {}
    if not dsh_info.get("native_installed") and not dsh_info.get("interop_available") and dsh_info.get("interop_status") != "unknown":
        missing_labels.append("DeepSeek Harness")
    if not _confirm_single_app_install(label, " / ".join(missing_labels), interactive, auto_yes, dry_run):
        sys.exit(0)
    return installed


def main() -> None:
    """先解析参数、完成各端预检，再按预演或正式安装路径执行。"""
    parser = argparse.ArgumentParser(description="Antigravity、Codex、Claude Code 与 DeepSeek Harness 统一配置安装脚本")
    parser.add_argument(
        "--app",
        choices=("auto", "all", "both", "antigravity", "codex", "claude", "dsh"),
        default="auto",
        help="auto 按已安装集合配置；both 仅反重力加 Codex；all 配置四端（按需接入 DSH）；也可单独指定客户端",
    )
    parser.add_argument("--probe-apps", action="store_true", help="只读探测四端客户端、CC Switch 与 Zotero，输出 JSON")
    parser.add_argument("--audit-upgrade", action="store_true", help="只读检查各端配置、CC Switch 卡片与本地模型列表同步状态，输出 JSON")
    parser.add_argument("-y", "--yes", action="store_true", help="非交互自动确认（仅检测到单客户端时直接单独配置已安装的一端）")
    parser.add_argument("--target-dir", type=str, default="",
                        help="自定义反重力插件目录。配置 Zotero 时须采用 <配置目录>/plugins/user-guard 布局")
    parser.add_argument("--codex-home", type=str, default="", help="自定义 Codex 主配置目录（默认 $CODEX_HOME 或 ~/.codex，用于沙箱全量验证）")
    parser.add_argument("--codex-skills-dir", default="", help="仅自定义 Codex 技能根目录（其下安装托管技能 cae-harness 与 fleet-evolve，兼容隔离验证）")
    parser.add_argument("--claude-home", default="", help="Claude Code 主目录（默认 $CLAUDE_CONFIG_DIR 或 ~/.claude）")
    parser.add_argument("--dsh-home", default="", help="DeepSeek Harness 主目录（默认 $DSH_HOME 或 ~/.dsh）")
    parser.add_argument("--no-dsh-rules", action="store_true", help="跳过 DSH 全局 AGENTS.md")
    parser.add_argument("--no-dsh-skill", action="store_true", help="跳过 DSH 托管技能（cae-harness、fleet-evolve）")
    parser.add_argument("--no-claude-skill", action="store_true", help="跳过 Claude 托管技能（cae-harness、fleet-evolve）")
    parser.add_argument("--no-claude-rules", action="store_true", help="跳过 Claude 全局 CLAUDE.md")
    parser.add_argument("--no-claude-agents", action="store_true", help="跳过 Claude 只读子代理")
    parser.add_argument("--no-claude-hooks", action="store_true", help="跳过 Claude 钩子同步")
    parser.add_argument("--claude-handoff-mode", choices=("advisory", "enforce", "off"), help="首次默认 advisory，升级沿用已有策略")
    parser.add_argument("--claude-handoff-compactions", type=int, help="首次默认 0，升级沿用已有阈值")
    parser.add_argument("--with-cc-router", action="store_true", help="同步 Claude 本地路由器并保留已有 router.json")
    parser.add_argument("--ccswitch-db", default="", help="自定义 CC Switch 数据库路径")
    parser.add_argument("--disable-ccswitch-prompts", action="store_true", help="在线备份并禁用 CC Switch 陈旧提示词与技能开关，支持预演")
    parser.add_argument("--purge-ccswitch-legacy", action="store_true", help="在线备份并归档清空 CC Switch 已退役的提示词、技能表及 ~/.cc-switch/skills 目录")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，仅打印同步与配置计划不实际写入")
    parser.add_argument("--skip-test", action="store_true", help="跳过安装后的全套自动化回归测试")
    parser.add_argument("--no-zotero", action="store_true", help="跳过本机可选 Zotero MCP 自动探测与配置")
    parser.add_argument("--zotero-cmd", type=str, default="", help="显式指定本地 zotero-mcp 可执行文件路径")
    parser.add_argument("--no-codex-skill", action="store_true", help="跳过 Codex 托管技能（cae-harness、fleet-evolve）安装")
    parser.add_argument("--no-codex-rules", action="store_true", help="跳过 Codex 全局 AGENTS.md 核心总纲同步")
    parser.add_argument("--no-codex-agents", action="store_true", help="跳过 Codex 只读子代理 (agents/*.toml) 同步")
    parser.add_argument("--no-codex-hooks", action="store_true", help="跳过 Codex 钩子 (hooks.json + hooks/) 同步")
    parser.add_argument("--codex-handoff-mode", choices=("advisory", "enforce", "off"),
                        help="Codex 长会话交接模式：首次默认 advisory，升级默认沿用已有值")
    parser.add_argument("--codex-handoff-compactions", type=int,
                        help="Codex 交接的压缩次数阈值：首次默认 0，升级默认沿用已有值")
    model_options = parser.add_mutually_exclusive_group()
    model_options.add_argument("--no-model-config", action="store_true", help="跳过默认模型设置")
    model_options.add_argument("--sync-model-defaults", action="store_true", help="用本机模型目录覆盖已有默认模型与挡位")
    parser.add_argument("--no-sandbox-config", action="store_true", help="跳过 Codex 项目目录边界硬约束配置（workspace-write + on-request）")
    args = parser.parse_args()

    if args.codex_handoff_compactions is not None and args.codex_handoff_compactions < 0:
        parser.error("--codex-handoff-compactions 必须为非负整数")
    if args.claude_handoff_compactions is not None and args.claude_handoff_compactions < 0:
        parser.error("--claude-handoff-compactions 必须为非负整数")

    repo_root = Path(__file__).resolve().parent
    is_sandbox_run = bool(args.target_dir or args.codex_home or args.codex_skills_dir or args.claude_home or args.dsh_home)
    if args.app == "dsh" and (args.target_dir or args.codex_home or args.codex_skills_dir or args.claude_home):
        parser.error("--app dsh 不能指定其他客户端目标目录")
    if args.dsh_home and args.app in {"codex", "antigravity", "claude", "both"}:
        parser.error(f"--app {args.app} 不能指定 --dsh-home")
    if args.audit_upgrade:
        report = audit_upgrade_status(
            repo_root, Path(args.target_dir) if args.target_dir else None,
            Path(args.codex_home) if args.codex_home else None,
            Path(args.claude_home) if args.claude_home else None,
            Path(args.ccswitch_db) if args.ccswitch_db else None, is_sandbox_run=is_sandbox_run,
            app_scope=args.app, expect_cc_router=args.with_cc_router,
            dsh_home=Path(args.dsh_home) if args.dsh_home else None,
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    if args.probe_apps:
        try:
            detection = detect_installed_apps()
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(detection, indent=2, ensure_ascii=False))
        return

    if args.no_codex_skill and args.codex_skills_dir:
        parser.error("--no-codex-skill 与 --codex-skills-dir 不能同时指定")
    if args.codex_home and args.codex_skills_dir:
        parser.error("--codex-home 已包含托管技能管理，请勿同时指定 --codex-skills-dir")
    if args.app == "codex" and args.target_dir:
        parser.error("--app codex 模式下不应指定反重力 --target-dir")
    if args.app == "antigravity" and (args.codex_home or args.codex_skills_dir):
        parser.error("--app antigravity 模式下不应指定 --codex-home 或 --codex-skills-dir")
    if args.app == "both" and bool(args.target_dir) != bool(args.codex_home or args.codex_skills_dir):
        parser.error("--app both 指定自定义目标目录时，须同时提供反重力 --target-dir 与 Codex 目录参数，避免误改另一端真实主目录")
    if args.app == "claude" and (args.target_dir or args.codex_home or args.codex_skills_dir):
        parser.error("--app claude 不能指定反重力或 Codex 目标目录")
    if args.app in {"codex", "antigravity", "both"} and args.claude_home:
        parser.error(f"--app {args.app} 不能指定 --claude-home")
    if args.app == "all" and is_sandbox_run and not (args.target_dir and (args.codex_home or args.codex_skills_dir) and args.claude_home):
        parser.error("--app all 自定义安装必须同时提供 --target-dir、Codex 目录与 --claude-home")

    template_dir = repo_root / "template" / "user-guard"
    codex_template_dir = repo_root / "template" / "codex"
    claude_template_dir = repo_root / "template" / "claude"
    tests_dir = repo_root / "tests"

    if not template_dir.is_dir():
        print(f"[!] 错误：找不到反重力插件模板目录 {template_dir}", file=sys.stderr)
        sys.exit(1)

    if not args.skip_test and not args.dry_run and not tests_dir.is_dir():
        print(f"[!] 错误：找不到测试套件目录 {tests_dir}", file=sys.stderr)
        sys.exit(1)

    has_explicit_ag = bool(args.target_dir)
    has_explicit_cx = bool(args.codex_home or args.codex_skills_dir)
    has_explicit_cl = bool(args.claude_home)
    has_explicit_dsh = bool(args.dsh_home)
    has_legacy_dirs = has_explicit_ag or has_explicit_cx or has_explicit_cl
    try:
        detection = detect_installed_apps() if (args.app == "auto" and not (has_legacy_dirs or has_explicit_dsh)
                                               or args.app == "all" and not is_sandbox_run) else {}
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    is_interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())

    default_dsh_root = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh").expanduser()
    if args.app == "all":
        install_dsh = has_explicit_dsh if is_sandbox_run else bool(
            has_explicit_dsh or detection.get("dsh", {}).get("native_installed") or default_dsh_root.is_dir())
    else:
        install_dsh = args.app == "dsh" or args.app == "auto" and bool(
            has_explicit_dsh or detection.get("dsh", {}).get("native_installed"))
    if args.app == "dsh":
        install_ag, install_cx, install_cl = False, False, False
    elif args.app == "auto" and install_dsh and not has_legacy_dirs:
        peer_names = ("antigravity", "codex", "claude")
        install_ag, install_cx, install_cl = (False, False, False) if has_explicit_dsh else tuple(
            bool(detection.get(name, {}).get("native_installed")) for name in peer_names)
        if not (install_ag or install_cx or install_cl) and not has_explicit_dsh:
            if any(
                detection.get(name, {}).get("interop_available")
                or detection.get(name, {}).get("interop_status") == "unknown"
                for name in peer_names
            ):
                print("[*] 对侧客户端可用或状态未确认，本次只配置已检测到的本侧客户端。")
            elif not _confirm_single_app_install(
                "DeepSeek Harness", "Antigravity / Codex / Claude Code", is_interactive, args.yes, args.dry_run
            ):
                return
    else:
        install_ag, install_cx, install_cl = resolve_multi_app_plan(
            args.app, has_explicit_ag=has_explicit_ag, has_explicit_cx=has_explicit_cx, has_explicit_cl=has_explicit_cl,
            detection=detection, interactive=is_interactive, auto_yes=args.yes, dry_run=args.dry_run,
        )

    default_config_root = Path.home() / ".gemini" / "config"
    configured_codex_env = os.environ.get("CODEX_HOME", "").strip()
    default_codex_root = Path(configured_codex_env).expanduser() if configured_codex_env else (Path.home() / ".codex")
    default_claude_root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    claude_root = Path(args.claude_home).expanduser().absolute() if args.claude_home else default_claude_root.absolute()
    claude_root = claude_root if install_cl else None
    dsh_root = (Path(args.dsh_home).expanduser().absolute() if args.dsh_home else default_dsh_root.absolute()) if install_dsh else None
    ccswitch_db = Path(args.ccswitch_db).expanduser().absolute() if args.ccswitch_db else Path.home() / ".cc-switch/cc-switch.db"
    retire_ccswitch = (args.disable_ccswitch_prompts or args.purge_ccswitch_legacy) and (not is_sandbox_run or bool(args.ccswitch_db))
    if args.with_cc_router and not install_cl:
        parser.error("--with-cc-router 需要选择 Claude 安装")

    target_plugin_dir: Optional[Path] = None
    if install_ag:
        if args.target_dir:
            target_plugin_dir = Path(args.target_dir).expanduser().absolute()
        else:
            target_plugin_dir = default_config_root / "plugins" / "user-guard"

    skill_names = _managed_skill_names(template_dir)
    codex_root: Optional[Path] = None
    full_codex_sync = False
    codex_skill_dirs: List[Path] = []

    if install_cx:
        if args.codex_skills_dir and not args.codex_home:
            # 兼容仅指定 --codex-skills-dir 的轻量技能隔离同步模式
            full_codex_sync = False
            codex_skill_dirs = [
                path for name in skill_names
                if (path := codex_skill_target(args.codex_skills_dir, False, args.no_codex_skill, name)) is not None
            ]
        else:
            full_codex_sync = True
            codex_root = Path(args.codex_home).expanduser().absolute() if args.codex_home else default_codex_root.absolute()
            if not args.no_codex_skill:
                codex_skill_dirs = [codex_root / "skills" / name for name in skill_names]

    zotero_exe: Optional[str] = None
    if not args.no_zotero and (not is_sandbox_run or args.zotero_cmd):
        try:
            zotero_exe = detect_zotero_mcp_executable(args.zotero_cmd)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        if args.zotero_cmd and not zotero_exe:
            parser.error(f"指定的 Zotero MCP 可执行文件不存在: {args.zotero_cmd}")
        if not zotero_exe:
            print("[*] 未在本机检测到 zotero-mcp 可执行文件。")

    codex_hooks_plan = None
    codex_config_change = None
    agy_hooks = None
    agy_zotero_change = None
    agy_model_changes: List[FileChange] = []
    py_cmd = None
    claude_settings_plan = None
    ccswitch_admin = None
    try:
        agy_config_root = None
        if target_plugin_dir is not None:
            target_plugin_dir = validate_install_target(target_plugin_dir, repo_root)
            _validate_mirror(template_dir, target_plugin_dir)
            standard_layout = target_plugin_dir == target_plugin_dir.parent.parent / "plugins" / "user-guard"
            if zotero_exe and not standard_layout:
                raise ValueError("为反重力配置 Zotero MCP 时，--target-dir 必须为 <配置目录>/plugins/user-guard。")
            if standard_layout and not args.no_zotero:
                config_root = target_plugin_dir.parent.parent
                if zotero_exe:
                    user_root = Path.home().resolve()
                    unsafe_roots = {user_root, *user_root.parents, repo_root, *repo_root.parents}
                    if config_root in unsafe_roots or repo_root in config_root.parents:
                        raise ValueError(f"反重力 Zotero MCP 配置目录需独立于源码仓库和系统根目录: {config_root}")
                agy_config_root = config_root
        def _paths_overlap(left_path: Path, right_path: Path) -> bool:
            lp, rp = left_path.resolve(), right_path.resolve()
            return lp == rp or lp in rp.parents or rp in lp.parents

        def _validate_skill_targets(skill_paths: List[Path]) -> List[Path]:
            validated: List[Path] = []
            for dst in skill_paths:
                checked = validate_install_target(dst, repo_root)
                _validate_mirror(template_dir / "skills" / checked.name, checked)
                validated.append(checked)
            return validated

        default_plugin_root = default_config_root / "plugins" / "user-guard"
        if target_plugin_dir is not None and any(
            _paths_overlap(target_plugin_dir, other)
            for other in (default_codex_root, Path.home() / ".codex", default_claude_root, default_dsh_root)
        ):
            raise ValueError("反重力插件目录不能与其他客户端配置目录相互包含")
        if codex_root is not None:
            codex_root = validate_codex_home_target(codex_root, repo_root)
            if any(_paths_overlap(codex_root, other) for other in (default_plugin_root, default_claude_root, default_dsh_root)):
                raise ValueError("Codex 目录不能与其他客户端配置目录相互包含")
            if not args.no_codex_skill:
                codex_skill_dirs = [codex_root / "skills" / name for name in skill_names]
        if codex_skill_dirs:
            codex_skill_dirs = _validate_skill_targets(codex_skill_dirs)
        if claude_root is not None:
            claude_root = validate_codex_home_target(claude_root, repo_root, label="Claude")
            if any(_paths_overlap(claude_root, other) for other in (default_plugin_root, default_codex_root, default_dsh_root)):
                raise ValueError("Claude 目录不能与其他客户端配置目录相互包含")
            if not args.no_claude_skill:
                _validate_skill_targets([claude_root / "skills" / name for name in skill_names])
        if dsh_root is not None:
            dsh_root = validate_codex_home_target(dsh_root, repo_root, label="DSH")
            if any(_paths_overlap(dsh_root, other) for other in (default_plugin_root, default_codex_root, default_claude_root)):
                raise ValueError("DSH 目录不能与其他客户端配置目录相互包含")
            if not args.no_dsh_rules:
                _decode_utf8(dsh_root / "AGENTS.md", _read_optional_bytes(dsh_root / "AGENTS.md") or b"")
            if not args.no_dsh_skill:
                _validate_skill_targets([dsh_root / "skills" / name for name in skill_names])
        targets = [*(path for path in (target_plugin_dir, codex_root, claude_root, dsh_root) if path is not None), *codex_skill_dirs]
        for index, left in enumerate(targets):
            for right in targets[index + 1:]:
                if left == codex_root and right in codex_skill_dirs:
                    continue  # Codex 自带的托管技能目录是明确约定的从属资产。
                if left == right or left in right.parents or right in left.parents:
                    raise ValueError("不同客户端的安装目标不能相互包含")
        if agy_config_root is not None:
            mcp_path = agy_config_root / "mcp_config.json"
            if mcp_path in {codex_root, *codex_skill_dirs}:
                raise ValueError(f"反重力 Zotero 配置文件与 Codex 目标目录冲突: {mcp_path}")
            agy_zotero_change = _plan_user_zotero(agy_config_root, zotero_exe)
        if target_plugin_dir is not None or (codex_root is not None and not args.no_codex_hooks) or (claude_root is not None and not args.no_claude_hooks):
            py_cmd = detect_hook_python_command(antigravity=target_plugin_dir is not None)
        if target_plugin_dir is not None:
            agy_hooks = _agy_hooks_bytes((template_dir / "hooks.json").read_bytes(), py_cmd)
        if codex_root is not None:
            _validate_path(codex_root / "config.toml", directory=False)
            codex_config_change = _plan_codex_config(
                codex_root, include_sandbox=not args.no_sandbox_config, include_model=not args.no_model_config,
                overwrite_model=args.sync_model_defaults, zotero_exe=zotero_exe, repo_root=repo_root,
            )
            if not args.no_codex_hooks:
                codex_hooks_plan = _plan_codex_hooks(
                    codex_root / "hooks.json", codex_template_dir / "hooks.json", py_cmd,
                    args.codex_handoff_mode, args.codex_handoff_compactions, repo_root=repo_root,
                    config_bytes=codex_config_change.content if codex_config_change is not None else None,
                )
            _validate_codex_assets(codex_root, codex_template_dir, args, codex_hooks_plan)
        if claude_root is not None:
            claude_settings_plan = _plan_claude_settings(
                claude_root / "settings.json", claude_template_dir / "hooks.json", py_cmd or "", args.claude_handoff_mode,
                args.claude_handoff_compactions, include_hooks=not args.no_claude_hooks,
                include_model=not args.no_model_config, overwrite_model=args.sync_model_defaults,
                repo_root=repo_root,
            )
            _validate_claude_assets(repo_root, claude_root, args)
        if retire_ccswitch:
            _validate_path(ccswitch_db, directory=False)
            ccswitch_admin = _load_ccswitch_admin(repo_root)
            # 先只读验证 schema，再写入客户端资产；预演不备份、不改库。
            ccswitch_admin.retire_prompts_and_skills(ccswitch_db, purge=args.purge_ccswitch_legacy, dry_run=True)
        if target_plugin_dir == (default_config_root / "plugins" / "user-guard").resolve() and not args.no_model_config:
            agy_model_changes = _plan_agy_model(
                default_config_root.parent, dry_run=args.dry_run, overwrite=args.sync_model_defaults,
                repo_root=repo_root,
            )
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    mode_desc = " + ".join(name for name, enabled in (("反重力", install_ag), ("Codex", install_cx), ("Claude Code", install_cl), ("DeepSeek Harness", install_dsh)) if enabled)

    print("=" * 80)
    print("  Antigravity、Codex、Claude Code 与 DeepSeek Harness 统一工作流安装程序")
    print("=" * 80)
    print(f"[*] 仓库根目录   : {repo_root}")
    print(f"[*] 部署目标模式 : {mode_desc}")
    if py_cmd is not None:
        print(f"[*] 钩子运行命令 : {py_cmd}")
    if target_plugin_dir is not None:
        print(f"[*] 反重力插件路径: {target_plugin_dir}")
    if codex_root is not None:
        print(f"[*] Codex 主目录 : {codex_root}")
    if claude_root is not None:
        print(f"[*] Claude 主目录: {claude_root}")
    if dsh_root is not None:
        print(f"[*] DSH 主目录   : {dsh_root}")
    if codex_skill_dirs:
        print(f"[*] Codex 技能路径: {', '.join(str(path) for path in codex_skill_dirs)}")
    print("-" * 80)

    try:
        # 部署反重力侧。
        if target_plugin_dir is not None:
            mirror_sync_plugin(template_dir, target_plugin_dir, dry_run=args.dry_run, hooks_bytes=agy_hooks)
            _apply_file_change(agy_zotero_change, args.dry_run)
            for change in agy_model_changes:
                _apply_file_change(change, args.dry_run)

        # 部署 Codex 侧。
        if install_cx:
            for skill_dst in codex_skill_dirs:
                mirror_sync_plugin(template_dir / "skills" / skill_dst.name, skill_dst, dry_run=args.dry_run)
            if full_codex_sync and codex_root is not None:
                if not args.dry_run:
                    codex_root.mkdir(parents=True, exist_ok=True)
                if not args.no_codex_rules:
                    _sync_codex_global_rules(template_dir / "rules" / "AGENTS.md", codex_root, dry_run=args.dry_run)
                if not args.no_codex_agents:
                    _sync_codex_agents(codex_template_dir, codex_root, dry_run=args.dry_run)
                if codex_hooks_plan is not None:
                    _sync_codex_hooks(template_dir / "hooks", codex_root, codex_hooks_plan, dry_run=args.dry_run)
                _apply_file_change(codex_config_change, args.dry_run)
        if claude_root is not None:
            if not args.no_claude_skill:
                for name in skill_names:
                    mirror_sync_plugin(template_dir / "skills" / name, claude_root / "skills" / name, dry_run=args.dry_run)
            if not args.no_claude_rules:
                _sync_claude_global_rules(template_dir / "rules/AGENTS.md", claude_root, args.dry_run)
            if not args.no_claude_agents:
                _sync_claude_agents(claude_template_dir, claude_root, args.dry_run)
            if not args.no_claude_hooks:
                _sync_claude_hooks(template_dir / "hooks", claude_root, claude_settings_plan, args.dry_run)
                admin_target = claude_root / "hooks/ccswitch_admin.py"
                admin_original = _read_optional_bytes(admin_target)
                admin_content = (claude_template_dir / "router/ccswitch_admin.py").read_bytes()
                if admin_original != admin_content:
                    _apply_file_change(FileChange(admin_target, admin_original, admin_content), args.dry_run)
            else:
                _apply_file_change(claude_settings_plan.change, args.dry_run)
            if args.with_cc_router:
                _sync_cc_router(claude_template_dir / "router", claude_root, args.dry_run)
        if dsh_root is not None:
            if not args.no_dsh_rules:
                _sync_global_rules(template_dir / "rules/AGENTS.md", dsh_root / "AGENTS.md", "DeepSeek Harness", args.dry_run)
            if not args.no_dsh_skill:
                for name in skill_names:
                    mirror_sync_plugin(template_dir / "skills" / name, dsh_root / "skills" / name, dry_run=args.dry_run)
        if retire_ccswitch:
            result = ccswitch_admin.retire_prompts_and_skills(
                ccswitch_db, purge=args.purge_ccswitch_legacy, dry_run=args.dry_run
            )
            if args.purge_ccswitch_legacy:
                print(
                    f"[*] CC Switch 退役资产清理: 提示词 {result['purged_prompts']} 条, "
                    f"技能 {result['purged_skills']} 条, 归档目录 {result['archived_skills_dir']}; 备份: {result['backup']}"
                )
            else:
                print(
                    f"[*] CC Switch 提示词/技能禁用: 提示词 {len(result['disabled_prompts'])} 条, "
                    f"技能 {len(result['disabled_skills'])} 条; 备份: {result['backup']}"
                )
        elif args.disable_ccswitch_prompts or args.purge_ccswitch_legacy:
            print("[*] 沙箱未显式指定 --ccswitch-db，跳过宿主提示词与技能数据库。")
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"[!] 配置同步失败: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("\n[+] Dry-Run 演练检查完成，未修改磁盘文件。")
        return

    if not args.skip_test:
        print("\n" + "=" * 80)
        print("  正在执行全量无字节码 (-B) 自动化测试回归...")
        print("=" * 80)

        _scrub_exact = {
            "PLUGIN_DATA", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "PYTHONPATH",
        }
        _scrub_prefixes = (
            "CAE_", "USER_GUARD_", "AGY_", "ANTIGRAVITY_", "CODEX_",
            "CLAUDE", "ANTHROPIC_", "DSH_", "GIT_", "XDG_",
        )
        env = {
            k: v for k, v in os.environ.items()
            if k.upper() not in _scrub_exact and not k.upper().startswith(_scrub_prefixes)
        }
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["USER_GUARD_INSTALLED_DIR"] = str(target_plugin_dir if (install_ag and target_plugin_dir is not None) else template_dir)
        if install_cx and codex_root is not None:
            env["USER_GUARD_INSTALLED_CODEX_DIR"] = str(codex_root)
        if install_cl and claude_root is not None:
            env["USER_GUARD_INSTALLED_CLAUDE_DIR"] = str(claude_root)
        if install_dsh and dsh_root is not None:
            env["USER_GUARD_INSTALLED_DSH_DIR"] = str(dsh_root)

        test_cmd = [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(tests_dir),
            "-p",
            "test_*.py",
            "-v",
        ]
        try:
            result = subprocess.run(test_cmd, env=env, cwd=str(repo_root), timeout=REGRESSION_TIMEOUT_SECONDS)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"\n[!] 自动化回归测试执行失败: {exc}", file=sys.stderr)
            sys.exit(1)

        if result.returncode != 0:
            print("\n[!] 自动化回归测试未全部通过，请检查上方输出日志。", file=sys.stderr)
            sys.exit(result.returncode)

    print("\n" + "=" * 80)
    if args.skip_test:
        print("  指定目标配置同步完成，本次已按参数跳过回归测试。")
    else:
        print("  指定目标配置同步完成，全量离线回归测试通过。")
    print("=" * 80)


if __name__ == "__main__":
    main()
