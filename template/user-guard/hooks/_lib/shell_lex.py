"""
shell_lex.py - Shared shell lexing, quoting, ANSI-C $'...' decoding, and substitution extraction.
Pure Python standard library only.
"""

import os
import re
import shlex
from typing import List, Optional, Tuple


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


def pos_inside_quotes(text: str, target_pos: int) -> bool:
    in_single = False
    in_ansi_single = False
    in_double = False
    idx = 0
    limit = min(target_pos, len(text))
    while idx < limit:
        ch = text[idx]
        if ch == "\\" and (in_double or in_ansi_single) and idx + 1 < limit:
            idx += 2
            continue
        if (
            ch == "\\"
            and not in_single
            and not in_ansi_single
            and not in_double
            and idx + 1 < len(text)
            and text[idx + 1] in "\"'$\\` \t\r\n;|&<>()"
        ):
            idx += 2
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


def split_shell_clauses(cmd_str: str) -> List[str]:
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
            if ch == "\\" and quote_ch in ('"', "$'") and i + 1 < n:
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

        if ch == "\\" and i + 1 < n and cmd_str[i + 1] in ";&|{}()\n\"'":
            buf.extend((ch, cmd_str[i + 1]))
            i += 2
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


def strip_quoted_segments(clause: str) -> str:
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
            if ch == "\\" and quote_ch in ('"', "$'") and i + 1 < n:
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
        if ch == "\\" and i + 1 < n and clause[i + 1] in ("'", '"', "$", "\\", ">", "<"):
            out.append("  ")
            i += 2
            continue
        if ch in ("'", '"'):
            quote_ch = ch
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def extract_cmd_substitutions(clause: str) -> List[str]:
    """提取非单引号区间的 $(...)、成对反引号及非引号区间的 <(...) / >(...) 进程替换表达式"""
    subs: List[str] = []
    in_single = False
    in_ansi_single = False
    in_double = False
    i = 0
    n = len(clause)
    while i < n:
        ch = clause[i]
        if ch == "\\" and not in_single and i + 1 < n:
            i += 2
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
                    if c2 == "\\" and not s_q and j + 1 < n:
                        j += 2
                        continue
                    if s_q and c2 == "'" and j + 1 < n and clause[j + 1] == "'":
                        j += 2
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
