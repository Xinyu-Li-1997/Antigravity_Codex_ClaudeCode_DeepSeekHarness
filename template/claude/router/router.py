#!/usr/bin/env python3
"""Claude Code 本地模型分流代理（配合 CC Switch 供应商卡片使用，仅依赖 Python 标准库）。

Claude Code 每次请求固定发往单一 ANTHROPIC_BASE_URL。将激活卡片指向本服务后，代理按请求体中的
"model" 字段透明转发：
  * 模型名匹配某条路由前缀（默认 "deepseek"）时，转发至该路由对应的上游接口，并自动从同名
    CC Switch 卡片读取 API Key 替换请求头，同时剥离 Claude Code 附加的 "[1m]"/"[1M]" 后缀；
  * 其余请求转发至默认上游（如 Kimi），并从 default_key_from_card 指定的卡片读取真实密钥，
    因此指向本地端口的分流卡片本身无需保存真实密钥。
上游响应（含 SSE 流式块）按字节原样回传。日志仅记录单行耗时、状态码与字节数，绝不写入请求正文、
响应正文或任何密钥。

用法：python3 router.py [--config router.json]
健康检查：GET http://127.0.0.1:<port>/__router/health
"""
import argparse
import base64
import copy
import fnmatch
import http.client
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.realpath(__file__))
_BASE_DEFAULTS = types.MappingProxyType({
    "listen": "127.0.0.1",
    "port": 8240 if os.name == "nt" else 8241,
    "default_upstream": "https://api.kimi.com/coding",
    "routes": (
        types.MappingProxyType({
            "prefix": "deepseek",
            "upstream": "https://api.deepseek.com/anthropic",
            "key_from_card": "DeepSeek",
            "force_effort": "max",
        }),
    ),
    "default_key_from_card": "Kimi",
    "kimi_effort_map": types.MappingProxyType({"xhigh": "max"}),
    "ccswitch_db": "~/.cc-switch/cc-switch.db",
    "log": "~/.claude/router/logs/router.log",
    "timeout_s": 900,
    "max_body_bytes": 64 * 1024 * 1024,
    "max_inflight": 64,
    "key_cache_ttl_s": 60,
    "windows_proxy_ttl_s": 5,
    "follow_windows_proxy": False,
})


def _clone_base_defaults():
    cfg = dict(_BASE_DEFAULTS)
    cfg["port"] = 8240 if os.name == "nt" else 8241
    cfg["routes"] = [dict(item) for item in _BASE_DEFAULTS["routes"]]
    cfg["kimi_effort_map"] = dict(_BASE_DEFAULTS["kimi_effort_map"])
    return cfg


DEFAULTS = _clone_base_defaults()
HOP = {"host", "content-length", "connection", "keep-alive", "proxy-connection",
       "proxy-authorization", "proxy-authenticate", "transfer-encoding", "te", "trailer", "upgrade"}
WINDOWS_POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
WINDOWS_PROXY_QUERY = base64.b64encode(r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$settings = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
[Console]::WriteLine((ConvertTo-Json -InputObject @{
    enabled = ($settings.ProxyEnable -eq 1)
    server = [string]$settings.ProxyServer
    pac = [string]$settings.AutoConfigURL
    override = [string]$settings.ProxyOverride
} -Compress))
""".encode("utf-16-le")).decode("ascii")
_key_cache = {}
_key_lock = threading.Lock()
WINDOWS_PROXY_TTL = 5
KEY_CACHE_TTL = 60
_FAKE_IP_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("2001:2::/48"))
_proxy_cache = None
_proxy_lock = threading.Lock()
_log_lock = threading.Lock()
_INFLIGHT_SEM = threading.BoundedSemaphore(64)


def _is_reparse_or_symlink(path):
    if path.is_symlink():
        return True
    try:
        st = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & reparse_flag)


def _read_router_tunables_from_file(cand):
    try:
        if _is_reparse_or_symlink(cand) or not cand.is_file():
            return None
        data = json.loads(cand.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or isinstance(data.get("schema_version"), bool) or data.get("schema_version") != 1:
            return None
        rt = (((data.get("runtime_tunables") or {}).get("headless_dispatch") or {}).get("router") or {})
        return rt if isinstance(rt, dict) else {}
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None


def _load_fleet_router_tunables():
    """读取 fleet_profile.json 中的 router 运行期默认参数（router.json 显式配置优先）。"""
    env_p = os.environ.get("CAE_FLEET_PROFILE", "").strip()
    if env_p:
        rt = _read_router_tunables_from_file(Path(env_p).expanduser())
        return rt if rt is not None else {}
    here_path = Path(HERE)
    home = Path.home()
    codex_env = os.environ.get("CODEX_HOME", "").strip()
    dsh_env = os.environ.get("DSH_HOME", "").strip()
    codex_root = Path(codex_env).expanduser() if codex_env else (home / ".codex")
    dsh_root = Path(dsh_env).expanduser() if dsh_env else (home / ".dsh")
    candidates = [
        here_path.parent / "skills" / "cae-harness" / "fleet_profile.json",
        here_path.parent.parent / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json",
        home / ".gemini" / "config" / "plugins" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json",
        home / ".gemini" / "antigravity" / "plugins" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json",
        codex_root / "skills" / "cae-harness" / "fleet_profile.json",
        dsh_root / "skills" / "cae-harness" / "fleet_profile.json",
    ]
    for cand in candidates:
        rt = _read_router_tunables_from_file(cand)
        if rt is not None:
            return rt
    return {}


def _apply_fleet_router_defaults():
    global WINDOWS_PROXY_TTL, KEY_CACHE_TTL, _INFLIGHT_SEM
    rt = _load_fleet_router_tunables()
    if not rt:
        return
    port_key = "port" if os.name == "nt" else "wsl_port"
    if isinstance(rt.get(port_key), int) and not isinstance(rt[port_key], bool) and 1 <= rt[port_key] <= 65535:
        DEFAULTS["port"] = rt[port_key]
    if isinstance(rt.get("timeout_sec"), int) and not isinstance(rt["timeout_sec"], bool) and rt["timeout_sec"] > 0:
        DEFAULTS["timeout_s"] = rt["timeout_sec"]
    if isinstance(rt.get("max_body_mib"), int) and not isinstance(rt["max_body_mib"], bool) and rt["max_body_mib"] > 0:
        DEFAULTS["max_body_bytes"] = rt["max_body_mib"] * 1024 * 1024
    if isinstance(rt.get("max_inflight"), int) and not isinstance(rt["max_inflight"], bool) and rt["max_inflight"] > 0:
        DEFAULTS["max_inflight"] = rt["max_inflight"]
        _INFLIGHT_SEM = threading.BoundedSemaphore(rt["max_inflight"])
    if isinstance(rt.get("windows_proxy_ttl_sec"), int) and not isinstance(rt["windows_proxy_ttl_sec"], bool) and rt["windows_proxy_ttl_sec"] > 0:
        WINDOWS_PROXY_TTL = rt["windows_proxy_ttl_sec"]
        DEFAULTS["windows_proxy_ttl_s"] = WINDOWS_PROXY_TTL
    if isinstance(rt.get("key_cache_ttl_sec"), int) and not isinstance(rt["key_cache_ttl_sec"], bool) and rt["key_cache_ttl_sec"] > 0:
        KEY_CACHE_TTL = rt["key_cache_ttl_sec"]
        DEFAULTS["key_cache_ttl_s"] = KEY_CACHE_TTL


_apply_fleet_router_defaults()


def default_config_path():
    primary = os.path.join(HERE, "router.json")
    if os.path.exists(primary):
        return primary
    platform_cfg = os.path.join(HERE, "router.windows.json" if os.name == "nt" else "router.wsl.json")
    return platform_cfg if os.path.exists(platform_cfg) else primary


def _require_pos_int(val, name):
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return val


def _require_pos_number(val, name):
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val <= 0:
        raise ValueError(f"{name} must be a positive number")
    return val


def _validate_upstream_url(val, name):
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"{name} must be a non-empty http/https URL")
    raw = val.strip()
    if any(ch.isspace() or ord(ch) < 0x20 for ch in raw):
        raise ValueError(f"{name} contains invalid control or whitespace characters")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"{name} must use http or https with a non-empty hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{name} must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain query or fragment")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} has an invalid port") from exc
    return raw


def load_config(path):
    cfg = _clone_base_defaults()
    rt = _load_fleet_router_tunables()
    if rt:
        port_key = "port" if os.name == "nt" else "wsl_port"
        if isinstance(rt.get(port_key), int) and not isinstance(rt[port_key], bool) and 1 <= rt[port_key] <= 65535:
            cfg["port"] = rt[port_key]
        if isinstance(rt.get("timeout_sec"), int) and not isinstance(rt["timeout_sec"], bool) and rt["timeout_sec"] > 0:
            cfg["timeout_s"] = rt["timeout_sec"]
        if isinstance(rt.get("max_body_mib"), int) and not isinstance(rt["max_body_mib"], bool) and rt["max_body_mib"] > 0:
            cfg["max_body_bytes"] = rt["max_body_mib"] * 1024 * 1024
        if isinstance(rt.get("max_inflight"), int) and not isinstance(rt["max_inflight"], bool) and rt["max_inflight"] > 0:
            cfg["max_inflight"] = rt["max_inflight"]
        if isinstance(rt.get("windows_proxy_ttl_sec"), int) and not isinstance(rt["windows_proxy_ttl_sec"], bool) and rt["windows_proxy_ttl_sec"] > 0:
            cfg["windows_proxy_ttl_s"] = rt["windows_proxy_ttl_sec"]
        if isinstance(rt.get("key_cache_ttl_sec"), int) and not isinstance(rt["key_cache_ttl_sec"], bool) and rt["key_cache_ttl_sec"] > 0:
            cfg["key_cache_ttl_s"] = rt["key_cache_ttl_sec"]
    if path and os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("router config root must be a JSON object")
        for sec_key, s_key in (
            ("timeout_sec", "timeout_s"),
            ("key_cache_ttl_sec", "key_cache_ttl_s"),
            ("windows_proxy_ttl_sec", "windows_proxy_ttl_s"),
        ):
            if sec_key in loaded:
                _require_pos_number(loaded[sec_key], sec_key)
                if s_key not in loaded:
                    cfg[s_key] = loaded[sec_key]
        if "max_body_mib" in loaded:
            mib = _require_pos_int(loaded["max_body_mib"], "max_body_mib")
            if "max_body_bytes" not in loaded:
                cfg["max_body_bytes"] = mib * 1024 * 1024
        cfg.update(loaded)
    if not isinstance(cfg["follow_windows_proxy"], bool):
        raise ValueError("follow_windows_proxy must be a boolean")
    if not isinstance(cfg.get("listen"), str) or not cfg["listen"].strip():
        raise ValueError("listen must be a non-empty string")
    listen_host = cfg["listen"].strip().strip("[]")
    if not _is_loopback_host(listen_host):
        raise ValueError("listen must be a loopback address (127.0.0.1, ::1, or localhost)")
    cfg["listen"] = listen_host
    port = cfg.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError("port must be an integer between 1 and 65535")
    _require_pos_number(cfg.get("timeout_s"), "timeout_s")
    _require_pos_int(cfg.get("max_body_bytes"), "max_body_bytes")
    _require_pos_int(cfg.get("max_inflight"), "max_inflight")
    _require_pos_number(cfg.get("key_cache_ttl_s"), "key_cache_ttl_s")
    _require_pos_number(cfg.get("windows_proxy_ttl_s"), "windows_proxy_ttl_s")
    if not isinstance(cfg.get("routes"), list) or any(not isinstance(r, dict) for r in cfg["routes"]):
        raise ValueError("routes must be a list of route objects")
    _validate_upstream_url(cfg.get("default_upstream"), "default_upstream")
    for idx, r in enumerate(cfg["routes"]):
        _validate_upstream_url(r.get("upstream"), f"routes[{idx}].upstream")
    return cfg


def _safe_powershell_cwd(powershell):
    ps_dir = os.path.dirname(powershell) if powershell and os.path.isabs(powershell) else ""
    for candidate in (
        ps_dir,
        "/mnt/c/Windows/System32" if os.name != "nt" else os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
        tempfile.gettempdir(),
        os.path.abspath(os.sep),
    ):
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


def _read_windows_proxy_settings():
    """Read manual registry settings without exposing subprocess output on failure."""
    powershell = shutil.which("powershell.exe") or (WINDOWS_POWERSHELL if os.path.exists(WINDOWS_POWERSHELL) else None)
    if not powershell:
        return {"enabled": False, "server": "", "pac": "", "override": ""}
    try:
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", WINDOWS_PROXY_QUERY],
            capture_output=True, check=True, timeout=8, cwd=_safe_powershell_cwd(powershell),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Windows proxy lookup timed out") from None
    except (OSError, subprocess.CalledProcessError):
        raise RuntimeError("Windows proxy lookup failed") from None
    try:
        value = json.loads(result.stdout.decode("utf-8-sig"))
        if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
            raise ValueError
        if not isinstance(value.get("override", ""), str):
            raise ValueError
        return value
    except ValueError:
        raise RuntimeError("Windows proxy returned an invalid endpoint") from None


def _windows_proxy_settings(ttl=None):
    """Cache registry snapshots and lookup failures; one thread refreshes each TTL."""
    global _proxy_cache
    effective_ttl = WINDOWS_PROXY_TTL if ttl is None else float(ttl)
    with _proxy_lock:
        if _proxy_cache is None or time.monotonic() - _proxy_cache[2] >= effective_ttl:
            try:
                value, error = _read_windows_proxy_settings(), None
            except RuntimeError as exc:
                value, error = None, str(exc)
            _proxy_cache = (value, error, time.monotonic())
        value, error, _ = _proxy_cache
        if error:
            raise RuntimeError(error)
        return value


def proxy_bypassed(hostname, overrides):
    """Match semicolon-separated host patterns, including IPv6 literals and <local>."""
    host = (hostname or "").strip("[]").lower()
    if not host:
        return False
    for pattern in overrides.split(";"):
        pattern = pattern.strip().strip("[]").lower()
        if pattern == "<local>":
            if "." not in host and ":" not in host:
                return True
        elif pattern and fnmatch.fnmatchcase(host, pattern):
            return True
    return False


def _is_loopback_host(hostname):
    """Recognize loopback literals and localhost without resolving arbitrary hosts."""
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _resolves_to_fake_ip(hostname):
    """Check benchmark ranges in IP literals or any TCP DNS answer."""
    if not hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            addresses = [answer[4][0] for answer in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)]
        except OSError:
            return False
    else:
        addresses = [hostname]
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if any(ip in network for network in _FAKE_IP_NETWORKS):
            return True
    return False


def _probe_proxy_port(hostname, port):
    """Open and immediately close a short TCP probe of the selected loopback port."""
    try:
        socket.create_connection((hostname, port), timeout=0.5).close()
        return True
    except OSError:
        return False


def windows_proxy_for(url, ttl=None):
    """Select a manual or live loopback Fake-IP proxy; never evaluate PAC or WPAD."""
    value = _windows_proxy_settings() if ttl is None else _windows_proxy_settings(ttl)
    if value.get("pac"):
        return None
    enabled = value["enabled"]
    if not enabled and (not isinstance(value.get("server"), str) or not value["server"].strip()):
        return None
    try:
        target = urlsplit(url)
        if proxy_bypassed(target.hostname, value.get("override", "")):
            return None
        endpoint = value.get("server")
        if not isinstance(endpoint, str):
            raise ValueError
        endpoint = endpoint.strip()
        if re.match(r"^[a-z][a-z0-9+.-]*\s*=", endpoint, re.I):
            per_scheme = {scheme.strip().lower(): address.strip() for part in endpoint.split(";")
                          if "=" in part for scheme, address in [part.split("=", 1)]}
            endpoint = per_scheme.get(target.scheme.lower(), "")
        if not endpoint:
            return None
        proxy = urlsplit(endpoint if "://" in endpoint else "http://" + endpoint)
        if not proxy.hostname or proxy.port == 0 or proxy.path not in ("", "/") or proxy.query or proxy.fragment:
            raise ValueError
    except ValueError:
        if not enabled:
            return None
        raise RuntimeError("Windows proxy returned an invalid endpoint") from None
    if proxy.scheme.lower() != "http":
        if not enabled:
            return None
        raise RuntimeError("Windows proxy must use the HTTP scheme")
    if proxy.username is not None or proxy.password is not None:
        if not enabled:
            return None
        raise RuntimeError("Windows proxy credentials in the endpoint are unsupported")
    if not enabled and not (
        _is_loopback_host(proxy.hostname)
        and _resolves_to_fake_ip(target.hostname)
        and _probe_proxy_port(proxy.hostname, proxy.port or http.client.HTTP_PORT)
    ):
        return None
    return proxy


_SINGLETON_HEADERS = frozenset({
    "authorization", "x-api-key", "content-type", "content-length",
    "user-agent", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
})


def end_to_end_headers(headers):
    """Remove both standard hop headers and fields nominated by Connection (RFC 9110 7.6.1)."""
    pairs = list(headers)
    hop = HOP | {token.strip().lower() for key, value in pairs if key.lower() == "connection"
                 for token in value.split(",")}
    return [(key, value) for key, value in pairs if key.lower() not in hop]


def _merge_request_headers(pairs):
    """Combine repeated list-based request headers with ', ' (RFC 9110 §5.3); keep last value for singletons."""
    merged = {}
    canonical_name = {}
    for key, value in pairs:
        low = key.lower()
        if low in _SINGLETON_HEADERS or low not in merged:
            canonical_name.setdefault(low, key)
            merged[low] = value
        else:
            merged[low] = f"{merged[low]}, {value}"
    return {canonical_name[low]: val for low, val in merged.items()}


def card_key(cfg, card_name):
    """API key of a cc-switch Claude provider card, read-only, cached for KEY_CACHE_TTL s."""
    db = Path(cfg["ccswitch_db"]).expanduser().resolve()
    cache_id = (os.path.normcase(str(db)), card_name)
    ttl = float(cfg.get("key_cache_ttl_s", cfg.get("key_cache_ttl_sec", KEY_CACHE_TTL)))
    with _key_lock:
        hit = _key_cache.get(cache_id)
        if hit and time.monotonic() - hit[1] < ttl:
            return hit[0]
        try:
            con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5)
            try:
                rows = con.execute("select settings_config from providers where app_type='claude' and name=? limit 2",
                                   (card_name,)).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            raise RuntimeError("cc-switch database could not be read") from None
        if not rows:
            raise RuntimeError("cc-switch card %r not found" % card_name)
        if len(rows) > 1:
            raise RuntimeError("multiple cc-switch cards named %r" % card_name)
        row = rows[0]
        try:
            env = json.loads(row[0])["env"]
            raw_auth = env.get("ANTHROPIC_AUTH_TOKEN")
            raw_api = env.get("ANTHROPIC_API_KEY")
            if isinstance(raw_auth, str) and raw_auth.strip():
                key = raw_auth
            elif raw_auth is None or raw_auth == "" or (isinstance(raw_auth, str) and not raw_auth.strip()):
                key = raw_api if raw_api is not None else raw_auth
            else:
                key = raw_auth
        except (ValueError, TypeError, KeyError, AttributeError):
            raise RuntimeError("cc-switch card has invalid settings") from None
        if (not isinstance(key, str) or not key.strip() or key.strip().upper().startswith("PASTE_")
                or key.strip().lower() == "via-router" or "\r" in key or "\n" in key):
            raise RuntimeError("cc-switch card has no usable API key")
        key = key.strip()
        _key_cache[cache_id] = (key, time.monotonic())
        return key


def invalidate_card_key_cache(cfg, card_name):
    if not card_name:
        return
    try:
        db = Path(cfg["ccswitch_db"]).expanduser().resolve()
        cache_id = (os.path.normcase(str(db)), card_name)
    except OSError:
        return
    with _key_lock:
        _key_cache.pop(cache_id, None)


def _ascii_reason(msg):
    return str(msg or "").encode("ascii", "backslashreplace").decode("ascii")[:200]


class _BodyTooLargeError(ValueError):
    pass


def _read_chunked_body(rfile, max_bytes):
    chunks = []
    total = 0
    while True:
        size_line = rfile.readline(65537)
        if not size_line or len(size_line) > 65536 or not size_line.endswith(b"\n"):
            raise ValueError("invalid chunked header")
        semi = size_line.find(b";")
        hex_part = (size_line[:semi] if semi >= 0 else size_line).strip()
        if not re.fullmatch(rb"[0-9A-Fa-f]+", hex_part):
            raise ValueError("invalid chunk size")
        chunk_len = int(hex_part, 16)
        if chunk_len == 0:
            trailer_lines = 0
            while True:
                trailer = rfile.readline(65537)
                if not trailer or len(trailer) > 65536 or not trailer.endswith(b"\n"):
                    raise ValueError("invalid or truncated chunked trailer")
                if trailer in (b"\r\n", b"\n"):
                    break
                trailer_lines += 1
                if trailer_lines > 64:
                    raise ValueError("too many chunked trailer lines")
            break
        if total + chunk_len > max_bytes:
            raise _BodyTooLargeError("chunked body exceeds max_body_bytes")
        data = rfile.read(chunk_len)
        if len(data) != chunk_len:
            raise ValueError("truncated chunk body")
        crlf = rfile.readline(4)
        if crlf not in (b"\r\n", b"\n"):
            raise ValueError("missing chunk CRLF")
        chunks.append(data)
        total += chunk_len
    return b"".join(chunks)


_LOG_SECRET_RE = re.compile(
    r"(?i)(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,})"
)
_LOG_KV_SECRET_RE = re.compile(
    r"(?i)((?:DEEPSEEK_API_KEY|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY|"
    r"api[_-]?key|auth[_-]?token|access[_-]?token|secret[_-]?key)[\"']?\s*[=:]\s*[\"']?)[^\s\"',}]+"
)
_LOG_SECRET_KEY_RE = re.compile(
    r"(?i)(?:auth|api.?key|secret|password|credential|bearer|(?:^|[_-])token(?:$|[_-])|[a-z0-9]Token$|^token[A-Z])"
)
_LOG_SAFE_LITERALS = frozenset({"via-router", "present", "missing", "placeholder", "n/a", "true", "false", "none", "null"})


def _scrub_log_value(val):
    if isinstance(val, str):
        val = re.sub(r"(?i)(https?://)([^/\s@]+@)", r"\1<redacted>@", val)
        val = re.sub(r"(?i)([?&](?:key|api_key|token|access_token|secret)=)[^&\s\"']+", r"\1<redacted>", val)
        val = _LOG_KV_SECRET_RE.sub(r"\1<redacted>", val)
        return _LOG_SECRET_RE.sub("<redacted>", val)
    if isinstance(val, dict):
        out = {}
        for k, v in val.items():
            if (
                isinstance(k, str) and _LOG_SECRET_KEY_RE.search(k)
                and isinstance(v, str) and len(v.strip()) >= 6
                and not v.strip().isdigit() and v.strip().lower() not in _LOG_SAFE_LITERALS
            ):
                out[k] = "<redacted>"
            else:
                out[k] = _scrub_log_value(v)
        return out
    if isinstance(val, list):
        return [_scrub_log_value(v) for v in val]
    return val


def log_line(cfg, rec):
    expanded = os.path.expanduser(cfg["log"])
    path = os.path.abspath(expanded)
    line = json.dumps(_scrub_log_value(rec), ensure_ascii=False) + "\n"
    with _log_lock:
        try:
            if ".." in Path(expanded).parts:
                return
            log_path = Path(path)
            log_dir = log_path.parent
            for anc in (*reversed(log_dir.parents), log_dir):
                if (anc.exists() or anc.is_symlink()) and _is_reparse_or_symlink(anc):
                    return
            os.makedirs(log_dir, mode=0o700, exist_ok=True)
            rotated_path = Path(path + ".1")
            if _is_reparse_or_symlink(log_dir) or _is_reparse_or_symlink(log_path) or _is_reparse_or_symlink(rotated_path):
                return
            if log_path.exists() and log_path.stat().st_size > 5_000_000:
                try:
                    if not _is_reparse_or_symlink(log_path) and not _is_reparse_or_symlink(rotated_path):
                        os.replace(path, path + ".1")
                except OSError:
                    pass
            if _is_reparse_or_symlink(log_path):
                return
            existed_before = log_path.exists()
            open_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, open_flags, 0o600)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    return
                if os.name == "posix" and not existed_before:
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as f:
                    fd = -1
                    f.write(line)
            finally:
                if fd >= 0:
                    os.close(fd)
        except OSError:
            pass


_CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


def _request_target_path(raw_path):
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("request target must be non-empty")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_path):
        raise ValueError("request target contains control or whitespace characters")
    if raw_path.lower().startswith(("http://", "https://")):
        parts = urlsplit(raw_path)
        if not parts.hostname:
            raise ValueError("absolute request target missing hostname")
        rel_path = parts.path if parts.path.startswith("/") else ("/" + parts.path if parts.path else "/")
        return f"{rel_path}?{parts.query}" if parts.query else rel_path
    if not raw_path.startswith("/") or raw_path.startswith("//"):
        raise ValueError("request target must be an origin-form path starting with '/'")
    return raw_path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"     # stream until close; no chunk re-encoding needed
    server_version = "cc-local-router/1"
    timeout = 60

    def log_message(self, *args):     # silence default stderr access log
        pass

    def _safe_send_error(self, code, message=None):
        try:
            self.send_error(code, message)
        except _CLIENT_DISCONNECT_ERRORS:
            pass

    def do_GET(self):
        try:
            target_path = _request_target_path(self.path)
        except ValueError:
            self._safe_send_error(400, "invalid request target")
            return
        if target_path.split("?", 1)[0] == "/__router/health":
            body = json.dumps({"ok": True, "port": self.server.cfg["port"]}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except _CLIENT_DISCONNECT_ERRORS:
                pass
            return
        self.forward(b"")

    def do_POST(self):
        sem = getattr(self.server, "inflight_sem", _INFLIGHT_SEM)
        if not sem.acquire(blocking=False):
            self._safe_send_error(503, "router max_inflight reached")
            return
        try:
            self._read_and_forward_locked()
        finally:
            sem.release()

    def _read_and_forward_locked(self):
        max_bytes = int(self.server.cfg.get("max_body_bytes") or (64 * 1024 * 1024))
        hdr_items = list(self.headers.items())
        raw_tes = [v for k, v in hdr_items if k.lower() == "transfer-encoding"]
        raw_cls = [v for k, v in hdr_items if k.lower() == "content-length"]
        if raw_tes:
            if raw_cls:
                self._safe_send_error(400, "conflicting Transfer-Encoding and Content-Length")
                return
            te_codings = [tok.strip().lower() for raw in raw_tes for tok in str(raw).split(",")]
            if te_codings != ["chunked"]:
                self._safe_send_error(400, "unsupported Transfer-Encoding")
                return
            try:
                body = _read_chunked_body(self.rfile, max_bytes)
            except _BodyTooLargeError:
                self._safe_send_error(413, "request body exceeds router max_body_bytes")
                return
            except ValueError:
                self._safe_send_error(400, "invalid chunked request body")
                return
            except OSError:
                return
            self._forward_locked(body)
            return
        if raw_cls:
            cl_tokens = [tok.strip() for raw in raw_cls for tok in str(raw).split(",")]
            if not cl_tokens or any(not re.fullmatch(r"[0-9]+", tok) for tok in cl_tokens):
                self._safe_send_error(400, "invalid Content-Length")
                return
            parsed_cls = {int(tok) for tok in cl_tokens}
            if len(parsed_cls) != 1:
                self._safe_send_error(400, "invalid Content-Length")
                return
            n = next(iter(parsed_cls))
        else:
            n = 0
        if n > max_bytes:
            self._safe_send_error(413, "request body exceeds router max_body_bytes")
            return
        try:
            body = self.rfile.read(n) if n else b""
        except OSError:
            return
        if len(body) != n:
            self._safe_send_error(400, "truncated request body")
            return
        self._forward_locked(body)

    do_PUT = do_POST

    def do_DELETE(self):
        self.forward(b"")

    def forward(self, body):
        sem = getattr(self.server, "inflight_sem", _INFLIGHT_SEM)
        if not sem.acquire(blocking=False):
            self._safe_send_error(503, "router max_inflight reached")
            return
        try:
            self._forward_locked(body)
        finally:
            sem.release()

    def _forward_locked(self, body):
        cfg = self.server.cfg
        t0 = time.time()
        try:
            req_target = _request_target_path(self.path)
        except ValueError:
            self._safe_send_error(400, "invalid request target")
            return
        rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "m": self.command,
               "path": req_target.split("?")[0], "transport": "direct"}
        upstream, route, data = cfg["default_upstream"], None, None
        ctype = self.headers.get("Content-Type", "")
        if body and "json" in ctype.lower():
            try:
                data = json.loads(body)
            except (ValueError, RecursionError):
                data = None
        model = data.get("model", "") if isinstance(data, dict) else ""
        if not isinstance(model, str):
            self._safe_send_error(400, "model must be a string")
            return
        rec["model"] = model
        for r in cfg["routes"]:
            prefix = str(r.get("prefix") or "")
            if prefix and model.lower().startswith(prefix.lower()):
                upstream, route = r["upstream"], r
                break
        rec["route"] = route["prefix"] if route else "default"
        headers = _merge_request_headers(end_to_end_headers(self.headers.items()))
        if isinstance(data, dict):
            oc = data.get("output_config") if isinstance(data.get("output_config"), dict) else {}
            rec["effort_in"] = oc.get("effort")
            rec["thinking"] = (data.get("thinking") or {}).get("type") if isinstance(data.get("thinking"), dict) else None
            if route:
                data["model"] = model.replace("[1m]", "").replace("[1M]", "")
                effort = oc.get("effort")
                if route.get("force_effort") and (
                    "effort" not in oc
                    or (isinstance(effort, str) and effort.lower() in ("high", "xhigh", "max"))
                ):
                    oc = dict(oc)
                    oc["effort"] = route["force_effort"]
                    data["output_config"] = oc
            elif isinstance(cfg.get("kimi_effort_map"), dict) and isinstance(oc.get("effort"), str):
                effort_map = {str(k).lower(): v for k, v in cfg["kimi_effort_map"].items()}
                mapped_effort = effort_map.get(oc["effort"].lower())
                if mapped_effort is not None:
                    oc = dict(oc)
                    oc["effort"] = mapped_effort
                    data["output_config"] = oc
            rec["effort_out"] = oc.get("effort")
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        key_card = route.get("key_from_card") if route else cfg.get("default_key_from_card")
        if key_card:
            try:
                key = card_key(cfg, key_card)
            except Exception as e:
                rec.update(status=502, err=str(e))
                log_line(cfg, rec)
                self._safe_send_error(502, _ascii_reason("router: %s" % e))
                return
            for k in list(headers):
                if k.lower() in ("x-api-key", "authorization"):
                    del headers[k]
            headers["x-api-key"] = key
            headers["Authorization"] = "Bearer " + key
        u = urlsplit(upstream)
        origin_path = u.path.rstrip("/") + req_target
        headers["Host"] = u.netloc
        if body or self.command in ("POST", "PUT", "PATCH"):
            headers["Content-Length"] = str(len(body))
        else:
            for k in list(headers):
                if k.lower() == "content-length":
                    del headers[k]
        conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = None
        sent, side, total = False, "upstream", 0
        try:
            proxy = None
            absolute_url = f"{u.scheme}://{u.netloc}{origin_path}"
            if cfg["follow_windows_proxy"]:
                try:
                    cfg_proxy_ttl = cfg.get("windows_proxy_ttl_s", cfg.get("windows_proxy_ttl_sec"))
                    proxy = (
                        windows_proxy_for(absolute_url, ttl=cfg_proxy_ttl)
                        if cfg_proxy_ttl is not None
                        else windows_proxy_for(absolute_url)
                    )
                except RuntimeError:
                    rec["proxy_fallback"] = "lookup_failed_or_unsupported"
                rec["transport"] = "windows_proxy" if proxy else "direct"
            if proxy:
                req_path = origin_path if u.scheme == "https" else absolute_url
                conn = conn_cls(proxy.hostname, proxy.port or http.client.HTTP_PORT, timeout=cfg["timeout_s"])
                if u.scheme == "https":
                    conn.set_tunnel(u.hostname, u.port or http.client.HTTPS_PORT)
                try:
                    conn.request(self.command, req_path, body=body if body else None, headers=headers)
                except ConnectionRefusedError:
                    if not (_is_loopback_host(proxy.hostname) and not _resolves_to_fake_ip(u.hostname)):
                        raise
                    conn.close()
                    conn = None
                    rec["proxy_fallback"] = "loopback_proxy_unreachable"
                    rec["transport"] = "direct"
                    conn = conn_cls(u.netloc, timeout=cfg["timeout_s"])
                    conn.request(self.command, origin_path, body=body if body else None, headers=headers)
            else:
                conn = conn_cls(u.netloc, timeout=cfg["timeout_s"])
                conn.request(self.command, origin_path, body=body if body else None, headers=headers)
            resp = conn.getresponse()
            rec["status"] = resp.status
            if resp.status in (401, 403) and key_card:
                invalidate_card_key_cache(cfg, key_card)
            side = "client"
            self.send_response(resp.status, resp.reason)
            resp_headers = resp.getheaders()
            for k, v in end_to_end_headers(resp_headers):
                self.send_header(k, v)
            if not getattr(resp, "chunked", False):
                resp_cl = next((v for k, v in resp_headers if k.lower() == "content-length"), None)
                if resp_cl is not None and re.fullmatch(r"[0-9]+", str(resp_cl).strip()):
                    self.send_header("Content-Length", str(resp_cl).strip())
            self.send_header("Connection", "close")
            self.end_headers()
            sent = True
            while True:
                side = "upstream"
                chunk = resp.read1(65536)
                if not chunk:
                    remaining = getattr(resp, "length", None)
                    if isinstance(remaining, int) and not isinstance(remaining, bool) and remaining > 0:
                        raise http.client.IncompleteRead(b"", remaining)
                    break
                side = "client"
                self.wfile.write(chunk)
                self.wfile.flush()
                total += len(chunk)
        except Exception as e:
            rec.update(status=rec.get("status") or 502, err="%s: %s" % (type(e).__name__, e), err_side=side)
            if not sent:  # 响应头已发出时不能再补写错误页，直接关闭连接避免破坏 SSE 流
                try:
                    self._safe_send_error(502, "router upstream error")
                except Exception:
                    pass
        finally:
            if conn is not None:
                conn.close()
            rec["bytes"] = total
            rec["ms"] = int((time.time() - t0) * 1000)
            log_line(cfg, rec)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Claude Code 本地模型分流代理服务")
    ap.add_argument("--config", default=None, help="路由配置文件路径（默认优先读取同目录 router.json）")
    a = ap.parse_args(argv)
    if a.config is not None:
        cfg_path = os.path.expanduser(a.config)
        if not os.path.isfile(cfg_path):
            ap.error(f"router config file not found: {a.config}")
    else:
        cfg_path = default_config_path()
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        ap.error(f"invalid router config ({cfg_path}): {exc}")
    class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    try:
        is_ipv6 = False
        try:
            is_ipv6 = ipaddress.ip_address(cfg["listen"]).version == 6
        except ValueError:
            is_ipv6 = False
        server_cls = _IPv6ThreadingHTTPServer if is_ipv6 else ThreadingHTTPServer
        srv = server_cls((cfg["listen"], cfg["port"]), Handler)
    except OSError as exc:
        host = f"[{cfg['listen']}]" if ":" in str(cfg["listen"]) and not str(cfg["listen"]).startswith("[") else cfg["listen"]
        health_url = f"http://{host}:{cfg['port']}/__router/health"
        msg = (
            f"router failed to bind {cfg['listen']}:{cfg['port']} ({exc}); "
            f"check existing instance via {health_url} or choose another port in --config"
        ).replace("\r", " ").replace("\n", " ")
        sys.stderr.write(msg + "\n")
        raise SystemExit(1) from None
    srv.daemon_threads = True
    srv.cfg = cfg
    srv.inflight_sem = threading.BoundedSemaphore(max(1, int(cfg.get("max_inflight") or 64)))
    log_line(cfg, {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "start", "port": cfg["port"], "pid": os.getpid()})
    srv.serve_forever()


if __name__ == "__main__":
    main()
