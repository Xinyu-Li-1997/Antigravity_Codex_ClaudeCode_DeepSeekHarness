# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""
shell_lex.py - Shared shell lexing, quoting, ANSI-C $'...' decoding, and substitution extraction.
Pure Python standard library only.
"""

import os
import re
import shlex
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


WRAPPER_VALUE_FLAGS: Dict[str, Tuple[str, ...]] = {
    "wsl": ("-d", "--distribution", "-u", "--user", "--cd"),
    "sudo": (
        "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
        "-r", "--role", "-t", "--type", "-C", "--close-from",
        "-T", "--command-timeout", "-D", "--chdir", "-R", "--chroot",
    ),
    "timeout": ("-k", "--kill-after", "-s", "--signal"),
    "nice": ("-n", "--adjustment"),
    "nohup": (),
    "env": ("-u", "--unset", "-C", "--chdir"),
    "uv_global": ("--directory", "--project", "--config-file"),
    "uv_run": (
        "--python", "-p", "--with", "--with-editable", "--with-requirements",
        "--directory", "--project", "--env-file", "--package",
    ),
    "exec": ("-a",),
    "systemd-run": (
        "-p", "--property", "--unit", "-u", "--slice", "--working-directory",
        "-E", "--setenv", "--uid", "--gid", "--service-type", "--description",
    ),
}
SUBSYSTEM_WRAPPER_NAMES = frozenset({"wsl", "wsl.exe", "ssh", "ssh.exe", "docker", "docker.exe", "podman"})


def unquote_split_string_payload(inner: str) -> str:
    """剥离 Windows shlex(posix=False) 拆解 --split-string="..." 后残留的开头或成对引号。"""
    s = inner.strip()
    if not s or s[0] not in ("'", '"'):
        return s
    q = s[0]
    if len(s) >= 2 and s[-1] == q:
        body = s[1:-1]
        return body.replace('\\"', '"') if q == '"' else body.replace("''", "'")
    end = s.find(q, 1)
    if end != -1:
        head = s[1:end]
        head = head.replace('\\"', '"') if q == '"' else head.replace("''", "'")
        return head + s[end + 1 :]
    return s[1:]


def consume_leading_wrapper(
    tokens: List[str],
    idx: int,
    *,
    unquote: Optional[Callable[[str], str]] = None,
    allow_subsystem: bool = True,
    allow_wsl_only: bool = False,
    allow_exec: bool = False,
    allow_systemd_run: bool = False,
) -> Optional[Dict[str, Any]]:
    """统一剥离单个前导命令包装器（command/nohup/nice/uv run/sudo/timeout/wsl/exec/systemd-run）。

    返回 dict:
      - next_idx: int
      - wrapper: str
      - lookup_only: bool (command -v / -V)
      - chdir: Optional[str] (由 sudo -D / wsl --cd 捕获的未展开目录参数)
      - used_subsystem: bool
      - saw_no_pager_env: bool (systemd-run -E GIT_PAGER=...)
    若 tokens[idx] 不是支持的包装器则返回 None。
    """
    n = len(tokens)
    if idx >= n:
        return None
    norm = unquote if unquote is not None else (lambda s: s)
    raw = norm(tokens[idx])
    head = Path(raw.replace("\\", "/")).name.lower()
    head_stem = head[:-4] if head.endswith(".exe") else head

    if head_stem == "command":
        j = idx + 1
        lookup_only = False
        while j < n and norm(tokens[j]).startswith("-"):
            fl = norm(tokens[j])
            j += 1
            if fl == "--":
                break
            if fl in ("-v", "-V") or (not fl.startswith("--") and any(c in fl[1:] for c in ("v", "V"))):
                lookup_only = True
                break
        return {
            "next_idx": j, "wrapper": "command", "lookup_only": lookup_only,
            "chdir": None, "used_subsystem": False, "saw_no_pager_env": False,
        }

    if head_stem == "nohup":
        j = idx + 1
        if j < n and norm(tokens[j]) == "--":
            j += 1
        return {
            "next_idx": j, "wrapper": "nohup", "lookup_only": False,
            "chdir": None, "used_subsystem": False, "saw_no_pager_env": False,
        }

    if head_stem == "nice":
        j = idx + 1
        while j < n and norm(tokens[j]).startswith("-"):
            fl = norm(tokens[j])
            j += 1
            if fl == "--":
                break
            if fl in WRAPPER_VALUE_FLAGS["nice"] and j < n:
                j += 1
        return {
            "next_idx": j, "wrapper": "nice", "lookup_only": False,
            "chdir": None, "used_subsystem": False, "saw_no_pager_env": False,
        }

    if head_stem == "uv" and idx + 1 < n:
        j = idx + 1
        chdir_val: Optional[str] = None
        while j < n and norm(tokens[j]).startswith("-"):
            fl = norm(tokens[j])
            j += 1
            if fl.startswith("--directory="):
                chdir_val = fl.split("=", 1)[1]
            elif fl in WRAPPER_VALUE_FLAGS["uv_global"] and j < n:
                if fl == "--directory":
                    chdir_val = tokens[j]
                j += 1
        if j < n and norm(tokens[j]).lower() == "run":
            j += 1
            while j < n and norm(tokens[j]).startswith("-"):
                fl = norm(tokens[j])
                j += 1
                if fl == "--":
                    break
                if fl.startswith("--directory="):
                    chdir_val = fl.split("=", 1)[1]
                elif fl in WRAPPER_VALUE_FLAGS["uv_run"] and j < n:
                    if fl == "--directory":
                        chdir_val = tokens[j]
                    j += 1
            return {
                "next_idx": j, "wrapper": "uv", "lookup_only": False,
                "chdir": chdir_val, "used_subsystem": False, "saw_no_pager_env": False,
            }
        return None

    if head_stem == "sudo":
        j = idx + 1
        chdir_val = None
        while j < n and norm(tokens[j]).startswith("-"):
            fl = norm(tokens[j])
            j += 1
            if fl == "--":
                break
            if fl.startswith("--chdir="):
                chdir_val = fl.split("=", 1)[1]
            elif fl in WRAPPER_VALUE_FLAGS["sudo"] and j < n:
                if fl in ("-D", "--chdir"):
                    chdir_val = tokens[j]
                j += 1
        return {
            "next_idx": j, "wrapper": "sudo", "lookup_only": False,
            "chdir": chdir_val, "used_subsystem": False, "saw_no_pager_env": False,
        }

    if head_stem == "timeout":
        j = idx + 1
        while j < n and norm(tokens[j]).startswith("-"):
            fl = norm(tokens[j]).lower()
            j += 1
            if fl == "--":
                break
            if fl in WRAPPER_VALUE_FLAGS["timeout"] and j < n:
                j += 1
        if j < n:
            j += 1  # 跳过时长参数
        return {
            "next_idx": j, "wrapper": "timeout", "lookup_only": False,
            "chdir": None, "used_subsystem": False, "saw_no_pager_env": False,
        }

    is_sub = (head in ("wsl", "wsl.exe")) if allow_wsl_only else (allow_subsystem and head in SUBSYSTEM_WRAPPER_NAMES)
    if is_sub:
        j = idx + 1
        chdir_val = None
        while j < n and norm(tokens[j]).startswith("-"):
            fl = norm(tokens[j])
            fl_low = fl.lower()
            j += 1
            if fl_low in ("--", "-e", "--exec"):
                break
            if fl_low.startswith("--cd="):
                chdir_val = fl.split("=", 1)[1]
            elif fl_low in WRAPPER_VALUE_FLAGS["wsl"] and j < n:
                if fl_low == "--cd":
                    chdir_val = tokens[j]
                j += 1
        return {
            "next_idx": j, "wrapper": head_stem, "lookup_only": False,
            "chdir": chdir_val, "used_subsystem": True, "saw_no_pager_env": False,
        }

    if allow_exec and head_stem == "exec":
        j = idx + 1
        while j < n and norm(tokens[j]).startswith("-"):
            fl = norm(tokens[j])
            j += 1
            if fl == "--":
                break
            if fl in WRAPPER_VALUE_FLAGS["exec"] and j < n:
                j += 1
        return {
            "next_idx": j, "wrapper": "exec", "lookup_only": False,
            "chdir": None, "used_subsystem": False, "saw_no_pager_env": False,
        }

    if allow_systemd_run and head_stem == "systemd-run":
        j = idx + 1
        saw_no_pager = False
        while j < n and norm(tokens[j]).startswith("-"):
            fl = norm(tokens[j])
            j += 1
            if fl == "--":
                break
            if re.search(r"(?:^|=)(?:GIT_PAGER|PAGER)=", fl, re.I):
                saw_no_pager = True
            if fl in WRAPPER_VALUE_FLAGS["systemd-run"] and j < n:
                if fl in ("-E", "--setenv") and re.match(r"^(?:GIT_PAGER|PAGER)=", norm(tokens[j]), re.I):
                    saw_no_pager = True
                j += 1
        return {
            "next_idx": j, "wrapper": "systemd-run", "lookup_only": False,
            "chdir": None, "used_subsystem": False, "saw_no_pager_env": saw_no_pager,
        }

    return None


def safe_split_tokens(segment: str) -> List[str]:
    try:
        tokens = shlex.split(segment, posix=(os.name != "nt"))
    except ValueError:
        tokens = segment.split()
    return [t.strip("\"'") for t in tokens if t.strip("\"'")]


def decode_ansi_c_quotes(text: str, start_idx: int) -> Optional[Tuple[str, int]]:
    """解码 Bash/Zsh ANSI-C $'...' 引号字面量，遇 \0 / \x00 按 C-string argv 语义截断有效载荷。"""
    n = len(text)
    if start_idx + 1 >= n or text[start_idx : start_idx + 2] != "$'":
        return None
    idx = start_idx + 2
    out: List[str] = []
    byte_buf = bytearray()
    nul_seen = False
    simple_escapes = {
        "\\": "\\", "'": "'", '"': '"', "?": "?", "a": "\a", "b": "\b",
        "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
    }

    def _flush_bytes() -> None:
        if byte_buf:
            try:
                out.append(byte_buf.decode("utf-8"))
            except UnicodeDecodeError:
                out.append(byte_buf.decode("latin-1"))
            byte_buf.clear()

    while idx < n:
        ch = text[idx]
        if ch == "'":
            if not nul_seen:
                _flush_bytes()
            return "".join(out), idx + 1
        if ch == "\\" and idx + 1 < n:
            nxt = text[idx + 1]
            if nxt == "x" and idx + 2 < n:
                m_hex = re.match(r"[0-9a-fA-F]{1,2}", text[idx + 2 : idx + 4])
                if m_hex:
                    val = int(m_hex.group(0), 16)
                    if not nul_seen:
                        if val == 0:
                            _flush_bytes()
                            nul_seen = True
                        else:
                            byte_buf.append(val)
                    idx += 2 + len(m_hex.group(0))
                    continue
            if nxt in "01234567":
                m_oct = re.match(r"[0-7]{1,3}", text[idx + 1 : idx + 4])
                if m_oct:
                    val = int(m_oct.group(0), 8) & 0xFF
                    if not nul_seen:
                        if val == 0:
                            _flush_bytes()
                            nul_seen = True
                        else:
                            byte_buf.append(val)
                    idx += 1 + len(m_oct.group(0))
                    continue
            if not nul_seen:
                _flush_bytes()
            if nxt in simple_escapes:
                if not nul_seen:
                    out.append(simple_escapes[nxt])
                idx += 2
                continue
            if nxt in ("u", "U"):
                width = 4 if nxt == "u" else 8
                m_u = re.match(rf"[0-9a-fA-F]{{1,{width}}}", text[idx + 2 : idx + 2 + width])
                if m_u:
                    cp = int(m_u.group(0), 16)
                    if not nul_seen:
                        if cp == 0:
                            nul_seen = True
                        else:
                            out.append("\ufffd" if (cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF) else chr(cp))
                    idx += 2 + len(m_u.group(0))
                    continue
            if nxt == "c" and idx + 2 < n:
                ctrl = ord(text[idx + 2]) & 0x1F
                if not nul_seen:
                    if ctrl == 0:
                        nul_seen = True
                    else:
                        out.append(chr(ctrl))
                idx += 3
                continue
            if not nul_seen:
                if nxt == "\x00":
                    nul_seen = True
                else:
                    out.append("\\" + nxt)
            idx += 2
            continue
        if not nul_seen:
            _flush_bytes()
            if ch == "\x00":
                nul_seen = True
            else:
                out.append(ch)
        idx += 1
    return None


def unquote_shell_word(raw_tok: str) -> str:
    """展开复合拼接的引号/ANSI-C 引号词元（如 '"."/../../outside/a'），保留 Windows 盘符路径反斜杠。"""
    s = raw_tok.strip()
    if not s:
        return ""
    n = len(s)
    idx = 0
    parts: List[str] = []
    while idx < n:
        if s[idx : idx + 2] == "$'":
            ansi = decode_ansi_c_quotes(s, idx)
            if ansi is not None:
                parts.append(ansi[0])
                idx = ansi[1]
                continue
        if s[idx] in ("'", '"'):
            q = s[idx]
            idx += 1
            while idx < n:
                c = s[idx]
                if q == '"' and c == "\\" and idx + 1 < n:
                    nxt = s[idx + 1]
                    if nxt in ('"', "\\", "$", "`"):
                        parts.append(nxt)
                    else:
                        parts.append("\\" + nxt)
                    idx += 2
                    continue
                if q == "'" and c == "'" and idx + 1 < n and s[idx + 1] == "'":
                    parts.append("'")
                    idx += 2
                    continue
                if c == q:
                    idx += 1
                    break
                parts.append(c)
                idx += 1
            continue
        if s[idx] == "\\" and idx + 1 < n and s[idx + 1] in " \t;|&<>()\"'":
            parts.append(s[idx + 1])
            idx += 2
            continue
        parts.append(s[idx])
        idx += 1
    return "".join(parts)


def read_quoted_arg(text: str, start_idx: int) -> Optional[Tuple[str, int, str]]:
    """从 start_idx 读取单引号、双引号或 ANSI-C $'...' 包裹的参数内容，返回 (inner_content, end_idx, quote_char)"""
    n = len(text)
    if start_idx + 1 < n and text[start_idx : start_idx + 2] == "$'":
        ansi = decode_ansi_c_quotes(text, start_idx)
        return (ansi[0], ansi[1], "$'") if ansi is not None else None
    if start_idx >= n or text[start_idx] not in ("'", '"'):
        return None
    q = text[start_idx]
    idx = start_idx + 1
    buf: List[str] = []
    while idx < n:
        ch = text[idx]
        if q == '"':
            if ch == "\\" and idx + 1 < n:
                nxt = text[idx + 1]
                if nxt == "\n":
                    idx += 2
                    continue
                if nxt in ('"', "\\", "$", "`"):
                    buf.append(nxt)
                else:
                    buf.append("\\" + nxt)
                idx += 2
                continue
            if ch == '"':
                return "".join(buf), idx + 1, q
        else:
            # POSIX bash '\''、shlex.quote 生成的 '"'"' 与 PowerShell '' 单引号转义兼容
            if ch == "'" and idx + 4 < n and text[idx : idx + 5] == "'\"'\"'":
                buf.append("'")
                idx += 5
                continue
            if ch == "'" and idx + 3 < n and text[idx : idx + 4] == "'\\''":
                buf.append("'")
                idx += 4
                continue
            if ch == "'" and idx + 1 < n and text[idx + 1] == "'":
                buf.append("'")
                idx += 2
                continue
            if ch == "'":
                return "".join(buf), idx + 1, q
        buf.append(ch)
        idx += 1
    return None


def pos_inside_quotes(text: str, target_pos: int, *, backslash_escape: bool = True) -> bool:
    in_single = False
    in_ansi_single = False
    in_double = False
    idx = 0
    limit = min(target_pos, len(text))
    while idx < limit:
        ch = text[idx]
        if ch == "\\" and (in_ansi_single or (in_double and backslash_escape)) and idx + 1 < limit:
            idx += 2
            continue
        if not backslash_escape and ch == "`" and in_double and idx + 1 < limit:
            idx += 2
            continue
        if (
            backslash_escape
            and ch == "\\"
            and not in_single
            and not in_ansi_single
            and not in_double
            and idx + 1 < len(text)
            and text[idx + 1] in "\"'$\\` \t\r\n;|&<>()#"
        ):
            idx += 2
            continue
        if (
            not in_single
            and not in_ansi_single
            and not in_double
            and ch == "#"
            and (idx == 0 or text[idx - 1] in " \t\r\n;|&({")
        ):
            nl = text.find("\n", idx)
            if nl == -1 or nl > target_pos:
                return True
            idx = nl + 1
            continue
        if not in_single and not in_ansi_single and not in_double and ch == "$" and idx + 1 < limit and text[idx + 1] == "'":
            in_ansi_single = True
            idx += 2
            continue
        if in_single and ch == "'" and idx + 1 < len(text) and text[idx + 1] == "'":
            idx += 2
            continue
        if ch == "'" and in_ansi_single:
            in_ansi_single = False
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single and not in_ansi_single:
            in_double = not in_double
        idx += 1
    return in_single or in_ansi_single or in_double


def split_shell_clauses(cmd_str: str, *, backslash_escape: bool = True) -> List[str]:
    """切分引号外的命令分组，保留命令替换和函数调用中的内嵌括号。"""
    clauses: List[str] = []
    buf: List[str] = []
    quote_ch: Optional[str] = None
    embedded_parens = 0
    embedded_braces = 0
    i = 0
    n = len(cmd_str)
    while i < n:
        ch = cmd_str[i]
        if quote_ch:
            if quote_ch == "'" and ch == "'" and i + 1 < n and cmd_str[i + 1] == "'":
                buf.extend(("'", "'"))
                i += 2
                continue
            buf.append(ch)
            if ch == "\\" and (quote_ch == "$'" or (quote_ch == '"' and backslash_escape)) and i + 1 < n:
                buf.append(cmd_str[i + 1])
                i += 2
                continue
            if not backslash_escape and ch == "`" and quote_ch == '"' and i + 1 < n:
                buf.append(cmd_str[i + 1])
                i += 2
                continue
            if ch == ("'" if quote_ch == "$'" else quote_ch):
                quote_ch = None
            i += 1
            continue

        if ch == "$" and i + 1 < n and cmd_str[i + 1] == "'":
            quote_ch = "$'"
            buf.extend(("$", "'"))
            i += 2
            continue

        if ch == "$" and i + 1 < n and cmd_str[i + 1] == "{":
            embedded_braces += 1
            buf.extend(("$", "{"))
            i += 2
            continue

        if ch in ("'", '"'):
            quote_ch = ch
            buf.append(ch)
            i += 1
            continue

        if backslash_escape and ch == "\\" and i + 1 < n:
            if cmd_str[i + 1] == "\r" and i + 2 < n and cmd_str[i + 2] == "\n":
                buf.append(" ")
                i += 3
                continue
            if cmd_str[i + 1] == "\n":
                buf.append(" ")
                i += 2
                continue
            if cmd_str[i + 1] in ";&|{}()\"'#":
                buf.extend((ch, cmd_str[i + 1]))
                i += 2
                continue

        if not embedded_braces and ch == "#" and (i == 0 or cmd_str[i - 1] in " \t\r\n;|&({"):
            nl = cmd_str.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue

        if embedded_braces:
            if ch == "{":
                embedded_braces += 1
            elif ch == "}":
                embedded_braces -= 1
            buf.append(ch)
            i += 1
            continue

        if ch == "(":
            previous = buf[-1] if buf else ""
            # %(refname) 等 Git 格式占位符也属于当前参数。
            if embedded_parens or (previous and (previous.isalnum() or previous in "$<>_:%")):
                embedded_parens += 1
                buf.append(ch)
            else:
                seg = "".join(buf).strip()
                if seg:
                    clauses.append(seg)
                buf = []
            i += 1
            continue

        if ch == ")" and embedded_parens:
            embedded_parens -= 1
            buf.append(ch)
            i += 1
            continue

        if embedded_parens:
            buf.append(ch)
            i += 1
            continue

        if ch in ("\n", ";", "{", "}", ")"):
            seg = "".join(buf).strip()
            if seg:
                clauses.append(seg)
            buf = []
            i += 1
            continue

        if ch in ("&", "|"):
            if i + 1 < n and cmd_str[i + 1] == ch:
                seg = "".join(buf).strip()
                if seg:
                    clauses.append(seg)
                buf = []
                i += 2
                continue
            if ch == "|" or (ch == "&" and (i == 0 or cmd_str[i - 1] not in "><")):
                seg = "".join(buf).strip()
                if seg:
                    clauses.append(seg)
                buf = []
                i += 1
                continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        clauses.append(tail)
    return clauses


def join_powershell_line_continuations(cmd_str: str) -> str:
    """安全合并 PowerShell 引号外行尾反引号续行（` + 可选空白 + 换行）。

    仅当引号外所有反引号均严格处于同行非空内容之后的行尾续行位置时才合并，
    避免吞掉 POSIX 多行反引号命令替换（例如 `echo `\nrm -rf /outside\n``）。
    """
    if "`" not in cmd_str or ("\n" not in cmd_str and "\r" not in cmd_str):
        return cmd_str
    spans: List[Tuple[int, int]] = []
    quote_ch: Optional[str] = None
    line_has_non_ws = False
    i = 0
    n = len(cmd_str)
    while i < n:
        ch = cmd_str[i]
        if quote_ch:
            if quote_ch == "'" and ch == "'" and i + 1 < n and cmd_str[i + 1] == "'":
                i += 2
                continue
            if ch == "\\" and quote_ch == "$'" and i + 1 < n:
                i += 2
                continue
            if ch == "`" and quote_ch == '"' and i + 1 < n:
                i += 2
                continue
            if ch == ("'" if quote_ch == "$'" else quote_ch):
                quote_ch = None
            if ch in ("\n", "\r"):
                line_has_non_ws = False
            else:
                line_has_non_ws = True
            i += 1
            continue
        if ch == "$" and i + 1 < n and cmd_str[i + 1] == "'":
            quote_ch = "$'"
            line_has_non_ws = True
            i += 2
            continue
        if ch in ("'", '"'):
            quote_ch = ch
            line_has_non_ws = True
            i += 1
            continue
        if ch == "#" and (i == 0 or cmd_str[i - 1] in " \t\r\n;|&({"):
            nl = cmd_str.find("\n", i)
            if nl == -1:
                break
            line_has_non_ws = False
            i = nl + 1
            continue
        if ch == "`":
            j = i + 1
            while j < n and cmd_str[j] in " \t":
                j += 1
            if line_has_non_ws and j < n and cmd_str[j] in ("\r", "\n"):
                end_j = j + 2 if (cmd_str[j] == "\r" and j + 1 < n and cmd_str[j + 1] == "\n") else j + 1
                spans.append((i, end_j))
                line_has_non_ws = False
                i = end_j
                continue
            # 存在非行尾续行的反引号（如 POSIX 命令替换或 PowerShell 字符转义），保守不合并。
            return cmd_str
        if ch in ("\n", "\r"):
            line_has_non_ws = False
        elif ch not in " \t":
            line_has_non_ws = True
        i += 1
    if not spans:
        return cmd_str
    parts: List[str] = []
    pos = 0
    for start, end in spans:
        parts.append(cmd_str[pos:start])
        parts.append(" ")
        pos = end
    parts.append(cmd_str[pos:])
    return "".join(parts)


def strip_quoted_segments(clause: str, *, backslash_escape: bool = True) -> str:
    """屏蔽引号（含 ANSI-C $'...'）内字符串文本并保持等长偏移，防止引号内 `>` 触发重定向误判"""
    out: List[str] = []
    quote_ch: Optional[str] = None
    i = 0
    n = len(clause)
    while i < n:
        ch = clause[i]
        if quote_ch:
            if quote_ch == "'" and ch == "'" and i + 1 < n and clause[i + 1] == "'":
                out.append("  ")
                i += 2
                continue
            if ch == "\\" and (quote_ch == "$'" or (quote_ch == '"' and backslash_escape)) and i + 1 < n:
                out.append("  ")
                i += 2
                continue
            if not backslash_escape and ch == "`" and quote_ch == '"' and i + 1 < n:
                out.append("  ")
                i += 2
                continue
            if ch == ("'" if quote_ch == "$'" else quote_ch):
                quote_ch = None
            out.append(" ")
            i += 1
            continue
        if ch == "$" and i + 1 < n and clause[i + 1] == "'":
            quote_ch = "$'"
            out.append("  ")
            i += 2
            continue
        if backslash_escape and ch == "\\" and i + 1 < n and clause[i + 1] in ("'", '"', "$", "\\", ">", "<", "#"):
            out.append("  ")
            i += 2
            continue
        if ch == "#" and (i == 0 or clause[i - 1] in " \t\r\n;|&({"):
            nl = clause.find("\n", i)
            end = n if nl == -1 else nl
            out.append(" " * (end - i))
            i = end
            continue
        if ch in ("'", '"'):
            quote_ch = ch
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def extract_cmd_substitutions(clause: str, *, backslash_escape: bool = True) -> List[str]:
    """提取非单引号区间的 $(...)、成对反引号及非引号区间的 <(...) / >(...) 进程替换表达式"""
    subs: List[str] = []
    in_single = False
    in_ansi_single = False
    in_double = False
    i = 0
    n = len(clause)
    while i < n:
        ch = clause[i]
        if backslash_escape and ch == "\\" and not in_single and i + 1 < n:
            i += 2
            continue
        if not backslash_escape and ch == "`" and in_double and i + 1 < n:
            i += 2
            continue
        if not in_single and not in_ansi_single and not in_double and ch == "#" and (i == 0 or clause[i - 1] in " \t\r\n;|&({"):
            nl = clause.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            continue
        if not in_single and not in_ansi_single and not in_double and ch == "$" and i + 1 < n and clause[i + 1] == "'":
            in_ansi_single = True
            i += 2
            continue
        if in_single and ch == "'" and i + 1 < n and clause[i + 1] == "'":
            i += 2
            continue
        if ch == "'" and in_ansi_single:
            in_ansi_single = False
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single and not in_ansi_single:
            in_double = not in_double
            i += 1
            continue
        if not in_single and not in_ansi_single:
            is_cmd_sub = ch == "$" and i + 1 < n and clause[i + 1] == "("
            is_proc_sub = not in_double and ch in ("<", ">") and i + 1 < n and clause[i + 1] == "("
            if is_cmd_sub or is_proc_sub:
                depth = 1
                j = i + 2
                s_q = False
                a_q = False
                d_q = False
                while j < n and depth > 0:
                    c2 = clause[j]
                    if backslash_escape and c2 == "\\" and not s_q and j + 1 < n:
                        j += 2
                        continue
                    if not backslash_escape and c2 == "`" and d_q and j + 1 < n:
                        j += 2
                        continue
                    if s_q and c2 == "'" and j + 1 < n and clause[j + 1] == "'":
                        j += 2
                        continue
                    if not s_q and not a_q and not d_q and c2 == "#" and (j == i + 2 or clause[j - 1] in " \t\r\n;|&({"):
                        nl = clause.find("\n", j)
                        if nl == -1:
                            j = n
                            break
                        j = nl + 1
                        continue
                    if not s_q and not a_q and not d_q and c2 == "$" and j + 1 < n and clause[j + 1] == "'":
                        a_q = True
                        j += 2
                        continue
                    if c2 == "'" and a_q:
                        a_q = False
                    elif c2 == "'" and not d_q:
                        s_q = not s_q
                    elif c2 == '"' and not s_q and not a_q:
                        d_q = not d_q
                    elif not s_q and not a_q and not d_q:
                        if c2 == "(":
                            depth += 1
                        elif c2 == ")":
                            depth -= 1
                    j += 1
                if depth == 0:
                    inner = clause[i + 2 : j - 1].strip()
                    if inner:
                        subs.append(inner)
                    i = j
                    continue
            if ch == "`" and i + 1 < n:
                j = i + 1
                bt_chars: List[str] = []
                closed_bt = False
                while j < n:
                    c2 = clause[j]
                    if c2 == "\\" and j + 1 < n:
                        nxt = clause[j + 1]
                        if nxt in ("`", "\\", "$"):
                            bt_chars.append(nxt)
                        elif in_double and nxt == '"':
                            bt_chars.append('"')
                        elif nxt == "\n":
                            pass
                        else:
                            bt_chars.append("\\" + nxt)
                        j += 2
                        continue
                    if c2 == "`":
                        closed_bt = True
                        j += 1
                        break
                    bt_chars.append(c2)
                    j += 1
                if closed_bt:
                    inner = "".join(bt_chars).strip()
                    if inner and inner not in ("r", "n", "t", "0"):
                        subs.append(inner)
                    i = j
                    continue
        i += 1
    return subs


READONLY_GIT_SUBCOMMANDS = frozenset({
    "status", "log", "show", "diff", "rev-parse", "ls-files",
    "cat-file", "grep", "blame", "describe", "shortlog", "reflog",
})


def parse_readonly_git_prefix(tokens: List[str]) -> Tuple[bool, Optional[str], List[str]]:
    """
    检查参数序列是否为纯只读 git 子命令前缀：
    返回 (is_readonly_git, explicit_git_cwd, remaining_subcommand_args)。
    """
    if not tokens:
        return False, None, []
    head = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if head.endswith(".exe"):
        head = head[:-4]
    if head != "git":
        return False, None, []
    idx = 1
    git_cwd: Optional[str] = None
    subcmd = ""
    while idx < len(tokens):
        tok = tokens[idx]
        low = tok.lower()
        if tok in ("-P", "--no-pager", "--no-optional-locks", "--bare"):
            idx += 1
            continue
        if tok == "-C":
            if idx + 1 >= len(tokens):
                return False, None, []
            git_cwd = tokens[idx + 1]
            idx += 2
            continue
        if tok.startswith("-C") and len(tok) > 2:
            git_cwd = tok[2:]
            idx += 1
            continue
        if tok in ("-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"):
            if idx + 1 >= len(tokens):
                return False, None, []
            if tok == "-c":
                cfg_k = tokens[idx + 1].split("=", 1)[0].strip().lower()
                if cfg_k.startswith(("alias.", "core.fsmonitor", "core.sshcommand", "core.pager", "pager.")) and not cfg_k.startswith(("core.pager", "pager.")):
                    return False, None, []
                if any(cfg_k.startswith(p) for p in ("alias.", "core.fsmonitor", "core.sshcommand", "core.hooks", "diff.external")):
                    return False, None, []
            else:
                return False, None, []
            idx += 2
            continue
        if low.startswith(("--git-dir=", "--work-tree=", "-c")):
            if low.startswith("-c") and len(tok) > 2:
                cfg_k = tok[2:].split("=", 1)[0].strip().lower()
                if any(cfg_k.startswith(p) for p in ("alias.", "core.fsmonitor", "core.sshcommand", "core.hooks", "diff.external")):
                    return False, None, []
                idx += 1
                continue
            return False, None, []
        if tok.startswith("-"):
            return False, None, []
        subcmd = low
        idx += 1
        break
    if subcmd not in READONLY_GIT_SUBCOMMANDS:
        return False, git_cwd, []
    rest = tokens[idx:]
    for arg in rest:
        al = arg.lower()
        if al in ("-o", "--output") or al.startswith(("--output=", "-o/")):
            return False, git_cwd, rest
        if subcmd == "diff" and al.startswith("--ext-diff"):
            return False, git_cwd, rest
        if subcmd == "grep" and (
            (arg.startswith("-") and not arg.startswith("--") and "O" in arg[1:])
            or al == "--open-files-in-pager"
            or al.startswith("--open-files-in-pager=")
        ):
            return False, git_cwd, rest
    return True, git_cwd, rest

