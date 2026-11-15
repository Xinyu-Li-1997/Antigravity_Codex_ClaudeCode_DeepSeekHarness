#!/usr/bin/env python3


"""Antigravity、Codex、Claude Code 与 DeepSeek Harness 跨端任务派发与验收工具。"""


import argparse
import base64
import collections
import concurrent.futures
import datetime
import fnmatch
import hashlib
import ipaddress
import json
import math
import ntpath
import os
import posixpath
import queue
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


# ════════════════════════════════════════════════════════════════════════════
# ① 全局常量、异常与回执 JSON Schema
# ════════════════════════════════════════════════════════════════════════════


SOURCE_EXTENSIONS = {'.f90', '.f95', '.f03', '.f08', '.f', '.for', '.c', '.cpp', '.cc', '.cxx', '.cu', '.cuh',
                     '.h', '.hpp', '.hh', '.py', '.jl', '.m', '.rs', '.go', '.ts', '.js', '.cmake', '.toml',
                     '.yaml', '.yml', '.json', '.md', '.svg', '.tex', '.bib', '.rst', '.txt', '.lean', '.inc',
                     '.sh', '.bash', '.ps1', '.cfg', '.ini'}

NAMED_TEXT_FILES = {'CMakeLists.txt', 'Makefile', 'makefile', 'GNUmakefile', 'Dockerfile', 'NOTICE', 'LICENSE', 'COPYING'}


IGNORED_DIRS = {'.git', '.hg', '.svn', '__pycache__', '.pytest_cache', 'node_modules', '.venv', 'venv',
                'env', 'build', 'dist', 'target', '.idea', '.vscode', '.gemini', '.antigravity-ide'}


EXIT_CODES = {'ok': 0, 'failed': 1, 'scope_violation': 1, 'verification_failed': 2,
              'partial': 3, 'invalid_output': 4, 'quota_exhausted': 5, 'timeout': 124, 'cancelled': 130}


QUOTA_ERROR_RE = re.compile(
    r"(?:hit your (?:usage|session|weekly) limit|usage limit|(?:session|weekly)[ _-]limit(?:[ _-](?:reached|exceeded))?|"
    r"5-hour (?:usage |session )?limit|insufficient_quota|exceeded your current quota|"
    r"purchase more credits|billing_hard_limit_reached|quota[ _-]?exceeded|quota[ _-]?exhausted|"
    r"out of quota|out of credits|resource_exhausted|daily limit exceeded|"
    r"credit balance is too low|insufficient[ _-]balance|余额不足|账户余额不足)",
    re.IGNORECASE,
)


EFFORT_LADDER = ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')


TIER_CHOICES = ('auto', 'ultra', 'max', 'xhigh', 'high', 'medium', 'low')


_DEFAULT_CATALOG_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
CATALOG_MAX_AGE_SECONDS = _DEFAULT_CATALOG_MAX_AGE_SECONDS


HIGH_COMPLEXITY_CUES = (
    '守恒', 'conservation', '通量', '通量重构', '黎曼', 'riemann', '雅可比', 'jacobian',
    '熵稳定', 'entropy stability', 'dgsem', 'weno', '高阶格式', '间断伽辽金', '有限元',
    '收敛阶', 'order of convergence', '制造解', '数值稳定性', '边界条件', '特征分解', '特征值',
    '数学公式', '公式推导', '控制方程', '偏微分方程', '本构', '线性化', '牛顿法', 'newton',
    '证明', 'prove', '定理', '引理', '推导', 'derive', '变分',
    '根因', 'root cause', '死锁', 'deadlock', '竞态', 'race condition', '并发', 'concurrency',
    '数值耗散', '激波',
)


TRIVIAL_SURFACE_CUES = ('错别字', 'typo', '拼写', '缩进', '空行', '注释微调', '文案微调', '格式微调')


ECONOMY_DOWNGRADE_CUES = (
    '降挡', '降档', '降一挡', '降一档', '适当降挡', '适当降档', '降低挡位', '降低档位',
    '省额度', '省点额度', '省些额度', '节省额度', '节约额度', '省着点额度',
    '省token', '省 token', '省点token', '省点 token', '省些token', '省些 token',
    '节省token', '节省 token', '节省点token', '节省点 token', '节省一点token', '节省一点 token',
    '节约token', '节约 token', '省算力', '节省算力', '省开销', '节省开销', '轻量快速',
    'save tokens', 'save token', 'save quota', 'lower tier', 'lower effort',
)


QUOTA_LOCK = threading.RLock()


AUDIT_JSON_SCHEMA = {
    'type': 'object', 'properties': {
        'summary': {'type': 'string'},
        'read_files': {'type': 'array', 'items': {'type': 'string'}},
        'unread_files': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'file': {'type': 'string'}, 'reason': {'type': 'string'}},
            'required': ['file', 'reason'], 'additionalProperties': False}},
        'findings': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'file': {'type': 'string'}, 'line_start': {'type': 'integer', 'minimum': 1},
            'line_end': {'type': 'integer', 'minimum': 1},
            'severity': {'type': 'string', 'enum': ['critical', 'high', 'medium', 'low']},
            'category': {'type': 'string'}, 'description': {'type': 'string'}, 'evidence': {'type': 'string'},
        }, 'required': ['file', 'line_start', 'line_end', 'severity', 'category', 'description', 'evidence'],
        'additionalProperties': False}},
    }, 'required': ['summary', 'findings', 'read_files', 'unread_files'], 'additionalProperties': False,
}


TASK_JSON_SCHEMA = {
    'type': 'object', 'properties': {'summary': {'type': 'string'}, 'content': {'type': 'string'},
        'evidence': {'type': 'array', 'items': {'type': 'string'}},
        'unverified': {'type': 'array', 'items': {'type': 'string'}}},
    'required': ['summary', 'content', 'evidence', 'unverified'], 'additionalProperties': False,
}


_CANCELLED = threading.Event()


_EVENT_LOCK = threading.Lock()


_SESSION_QUOTA_BLOCKED = {}


_CODEX_TOML_KEYS = {'model', 'model_reasoning_effort', 'model_context_window', 'model_auto_compact_token_limit'}


_CUE_BOUNDARIES = re.compile(r'[，。；、,;.!?！？\n]+|\b(?:but|however|instead)\b|但是|而是|而只', re.I)


_NEGATION = re.compile(
    r"(?:不(?:要|用|必|需|须|能|可|得|会|想|愿|再|应|打算|准备|允许|降|省|节|考虑|讨论|涉及|检查|核查|分析|推导|证明|处理|复核|验证)|"
    r"无需|无须|禁止|排除|避免|跳过|别|勿|莫)[^，。；、,;.!?！？\n]{0,12}$|"
    r"(?:^|(?<![要能好行成可会]))不\s*$|"
    r"\b(?:no|not|never|without|exclude|skip|avoid|don't|cannot|can't)\b(?:\s+[\w-]+){0,5}\s*$", re.I)


ROUTER_MODEL_TAGS = {'orchestrator', 'router', 'auto'}

_FLEET_PROFILE_CACHE = {'key': None, 'data': None}


def _fleet_profile_path():
    override = os.environ.get('CAE_FLEET_PROFILE', '').strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / 'fleet_profile.json'


def _fleet_profile():
    path = _fleet_profile_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if _FLEET_PROFILE_CACHE['key'] == key and _FLEET_PROFILE_CACHE['data'] is not None:
            return _FLEET_PROFILE_CACHE['data']
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or raw.get('schema_version') != 1:
            raise ValueError('invalid fleet_profile schema_version')
        _FLEET_PROFILE_CACHE['key'] = key
        _FLEET_PROFILE_CACHE['data'] = raw
        return raw
    except (OSError, ValueError) as exc:
        if os.environ.get('CAE_FLEET_PROFILE', '').strip():
            raise HarnessError(f'无法读取指定的模型评估快照 {path}: {exc}') from exc
        return {}


def _catalog_rules(prefix):
    rules = _fleet_profile().get('client_catalog_rules')
    entry = rules.get(prefix) if isinstance(rules, dict) else None
    return entry if isinstance(entry, dict) else {}


def _headless_tunables():
    tunables = _fleet_profile().get('runtime_tunables')
    hd = tunables.get('headless_dispatch') if isinstance(tunables, dict) else None
    return hd if isinstance(hd, dict) else {}


def _catalog_max_age_seconds():
    if CATALOG_MAX_AGE_SECONDS != _DEFAULT_CATALOG_MAX_AGE_SECONDS:
        return CATALOG_MAX_AGE_SECONDS
    days = _headless_tunables().get('catalog_max_age_days')
    if isinstance(days, int) and not isinstance(days, bool) and days > 0:
        return days * 24 * 60 * 60
    return CATALOG_MAX_AGE_SECONDS


GENERAL_PROJECT_CAPABILITIES = (
    'global_rules_R0_R11',
    'lifecycle_hooks',
    'readonly_subagents',
    'cae_harness_probe_assess',
    'cae_harness_task_gate_audit',
    'cae_harness_check_grounding',
    'ccswitch_admin',
)

UPSTREAM_ONLY_CAPABILITIES = (
    'fleet_evolve_full_hygiene_audit',
    'fleet_state_lock',
    'host_global_sync_install_py',
)


def detect_workspace_mode(target_dir=None):
    """识别当前工作区属于通用课题项目（部分功能）还是本配置上游维护仓库（全功能）。"""
    base = Path(target_dir).expanduser().resolve() if target_dir is not None else Path.cwd().resolve()
    if base.is_file():
        base = base.parent
    matched_root = None
    for candidate in (base, *base.parents):
        if (candidate / 'install.py').is_file() and (candidate / 'template' / 'user-guard' / 'plugin.json').is_file():
            matched_root = candidate
            break
    is_upstream = matched_root is not None
    return {
        'mode': 'upstream_config_repo' if is_upstream else 'general_project',
        'is_upstream_config_repo': is_upstream,
        'inspected_path': str(base),
        'upstream_repo_root': str(matched_root) if matched_root else None,
        'available_capabilities': list(
            GENERAL_PROJECT_CAPABILITIES + UPSTREAM_ONLY_CAPABILITIES
            if is_upstream else GENERAL_PROJECT_CAPABILITIES
        ),
        'upstream_only_capabilities': [] if is_upstream else list(UPSTREAM_ONLY_CAPABILITIES),
    }


def _fast_model_tags(prefix=None):
    if prefix:
        tags = _catalog_rules(prefix).get('fast_tags')
        if isinstance(tags, list) and tags:
            return {str(item).lower() for item in tags if item}
    merged = set()
    for client in ('agy', 'codex'):
        for item in _catalog_rules(client).get('fast_tags') or ():
            if item:
                merged.add(str(item).lower())
    return merged or {'lite', 'mini', 'fast', 'flash'}


def _deep_model_tags(prefix='codex'):
    tags = _catalog_rules(prefix).get('deep_tags')
    return {str(item).lower() for item in tags if item} if isinstance(tags, list) else set()


NON_AI_MODEL_STOPWORDS = {
    'turbulence', 'rans', 'les', 'dns', 'sst', 'kepsilon', 'komega', 'spalart', 'allmaras',
    'epsilon', 'navier', 'stokes', 'euler', 'boltzmann', 'bgk', 'muscl', 'tvd', 'godunov',
    'smagorinsky', 'vreman', 'courant', 'knudsen', 'reynolds', 'prandtl', 'mach',
    'weno', 'cweno', 'eno', 'dg', 'dgsem', 'fvm', 'fem', 'fea', 'fdm', 'cfd', 'cae', 'pde', 'hpc', 'mms', 'eoc', 'gcl', 'eos',
    'riemann', 'roe', 'hll', 'hllc', 'hlle', 'ausm', 'rusanov', 'rk', 'bdf', 'cfl',
    'model', 'models', 'default', 'auto', 'cli', 'lite', 'lightweight',
    'codex', 'antigravity', 'agy', 'openai', 'google', 'gemini', 'gpt',
    'claude', 'cc', 'anthropic', 'dsh', 'harness',
}


PROMPT_MODEL_HINT_PATTERNS = (
    re.compile(
        r'(?:请)?(?:使用|调用|采用|切到|切换至|指定|选用|改用|用)\s*'
        r'((?:混动\s*)?[A-Za-z][A-Za-z0-9._()（） -]{1,40}?|混动(?:路由(?:器)?)?)\s*'
        r'(?:AI\s*)?(?:模型|路由(?:器)?|引擎)',
        re.IGNORECASE,
    ),
    re.compile(r'(?:指定模型|执行模型|目标模型)\s*[:=：]\s*([A-Za-z][A-Za-z0-9._()（）-]{1,40})', re.IGNORECASE),
    re.compile(
        r'(?:请)?(?:使用|调用|采用|切到|切换至|指定|选用|改用|用)\s*'
        r'(?:混动\s*)?(orchestrator(?:\s*[-()（）A-Za-z0-9]+)?)',
        re.IGNORECASE,
    ),
)


PHYSICAL_SUBSTANCE_TAGS = {
    'hydrogen', 'carbon', 'argon', 'oxygen', 'nitrogen', 'helium', 'methane', 'air', 'water', 'soot',
}


PHYSICS_MODEL_CONTEXT_RE = re.compile(
    r'(?:计算|模拟|仿真|求解|离散|预测|分析|爆轰|燃烧|反应|组分|流场|传热|热流|'
    r'detonation|combustion|reaction|species|flow|chemistry|flame)',
    re.IGNORECASE,
)


_AGY_AUTH_REQUIRED = re.compile(
    r'\b(?:please (?:sign in|log in|login)|not (?:signed|logged) in|authentication required|'
    r'login required|sign-in required|unauthenticated)\b', re.I)


_QUOTA_EVENT_NAME = re.compile(r'^[0-9a-f]{32}\.json$')


_DEFAULT_MAX_INTEROP_STAGE_BYTES = 64 * 1024 * 1024
MAX_INTEROP_STAGE_BYTES = _DEFAULT_MAX_INTEROP_STAGE_BYTES


def _max_interop_stage_bytes():
    if MAX_INTEROP_STAGE_BYTES != _DEFAULT_MAX_INTEROP_STAGE_BYTES:
        return MAX_INTEROP_STAGE_BYTES
    mib = _positive_int(_headless_tunables().get('interop_stage_max_mib'))
    return mib * 1024 * 1024 if mib else MAX_INTEROP_STAGE_BYTES


STAGED_PROMPT_SUFFIX = '\n[跨端只读工作区说明] 当前目录已按原相对路径只读镜像目标文件，请直接在当前目录下按相对路径读取。'


class HarnessError(RuntimeError):
    def __init__(self, message, status='failed', **details):
        super().__init__(message)
        self.status = status
        self.details = details


class OutputParseError(HarnessError):
    def __init__(self, message):
        super().__init__(message, 'invalid_output')


# ════════════════════════════════════════════════════════════════════════════
# ② 基础配置、跨平台路径与 WSL 互操作工具
# ════════════════════════════════════════════════════════════════════════════


def emit_event(event, **fields):
    with _EVENT_LOCK:
        print(json.dumps({'event': event, **fields}, ensure_ascii=False), file=sys.stderr, flush=True)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def _top_level_toml(path):
    try:
        text = path.read_text(encoding='utf-8-sig')
        config = tomllib.loads(text)
        return {key: config[key] for key in _CODEX_TOML_KEYS if key in config}
    except (OSError, ValueError):
        return {}


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _local_command(args, cwd=None):
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError(f'本地查询无法完成: {args[0]}', stderr=str(exc)) from exc
    if proc.returncode:
        raise HarnessError(f'本地查询失败: {args[0]}')
    return proc.stdout.strip()


def _wsl_path(path, windows=True):
    return _local_command(['wslpath', '-w' if windows else '-u', str(path)])


def _windows_env(var_name):
    var_name = str(var_name)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', var_name):
        raise HarnessError(f'Windows 环境变量名不合法: {var_name}')
    powershell_exe = shutil.which('powershell.exe')
    if powershell_exe:
        command = [powershell_exe, '-NoProfile', '-NonInteractive', '-Command',
                   f"[Environment]::GetEnvironmentVariable('{var_name}')"]
    else:
        cmd_exe = shutil.which('cmd.exe')
        if not cmd_exe:
            raise HarnessError('未发现 powershell.exe 或 cmd.exe，无法解析 Windows 环境变量')
        command = [cmd_exe, '/d', '/c', 'echo', f'%{var_name}%']
    parent = Path(command[0]).parent
    value = _local_command(command, cwd=str(parent) if parent.is_dir() else None)
    if not value or (not powershell_exe and value == f'%{var_name}%'):
        raise HarnessError(f'无法解析 Windows 环境变量 {var_name}')
    return value


def _is_posix():
    return os.name != 'nt'


def _find_windows_agy_in_wsl():
    if not (_is_posix() and shutil.which('wslpath')):
        return None
    found_exe = shutil.which('agy.exe')
    if found_exe:
        return found_exe
    if shutil.which('cmd.exe'):
        try:
            win_home = Path(_wsl_path(_windows_env('USERPROFILE'), windows=False))
            for candidate in (win_home / '.gemini' / 'bin' / 'agy.exe', win_home / '.local' / 'bin' / 'agy.exe'):
                if candidate.is_file():
                    return str(candidate)
        except (HarnessError, OSError):
            pass
    return None


def _rank_codex_candidates(paths):
    scored = []
    seen = set()
    for item in paths:
        if item is None:
            continue
        p = Path(item)
        key = str(p).lower()
        if key in seen:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            seen.add(key)
            scored.append((st.st_mtime_ns, str(p), p))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in scored]


def _sorted_codex_binaries(root):
    if not root.is_dir():
        return []
    try:
        entries = list(root.rglob('codex.exe'))
    except OSError:
        return []
    return _rank_codex_candidates(entries)


def _parse_codex_package_version(sub_name):
    parts = str(sub_name).split('_')
    return tuple(int(part) for part in parts[1].split('.') if part.isdigit()) if len(parts) > 1 else ()


def _decode_reg_output(raw, reg_exe, reg_cwd):
    if raw.isascii():
        return raw.decode('ascii')
    try:
        cp_proc = subprocess.run(
            [reg_exe, 'query', r'HKLM\SYSTEM\CurrentControlSet\Control\Nls\CodePage', '/v', 'ACP'],
            cwd=reg_cwd, capture_output=True, timeout=5,
        )
        if cp_proc.returncode == 0:
            cp_match = re.search(rb'ACP\s+REG_SZ\s+([0-9]+)', cp_proc.stdout)
            if cp_match:
                acp = cp_match.group(1).decode('ascii')
                return raw.decode('utf-8' if acp == '65001' else f'cp{acp}')
    except (LookupError, OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _find_windows_codex_package_cli():
    key_path = r'Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages'
    if _is_posix():
        reg_exe = shutil.which('reg.exe')
        if not (reg_exe and shutil.which('wslpath')):
            return None
        try:
            reg_parent = Path(reg_exe).parent
            reg_cwd = str(reg_parent) if reg_parent.is_dir() else None
            proc = subprocess.run(
                [reg_exe, 'query', r'HKCU' + '\\' + key_path, '/s', '/v', 'PackageRootFolder'],
                cwd=reg_cwd,
                capture_output=True, timeout=5,
            )
            if proc.returncode != 0:
                return None
            text = _decode_reg_output(proc.stdout, reg_exe, reg_cwd)
            if not text:
                return None
            locations = []
            current_sub = ''
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if line.upper().startswith('HKEY_CURRENT_USER\\'):
                    current_sub = line.rsplit('\\', 1)[-1].strip()
                elif current_sub.lower().startswith('openai.codex_') and 'REG_SZ' in line and 'PackageRootFolder' in line:
                    loc = line.split('REG_SZ', 1)[1].strip()
                    if loc:
                        locations.append((_parse_codex_package_version(current_sub), loc))
            for _, win_loc in sorted(locations, reverse=True):
                try:
                    wsl_loc = Path(_wsl_path(win_loc, windows=False))
                except HarnessError:
                    continue
                cli = wsl_loc / 'app' / 'resources' / 'codex.exe'
                if cli.is_file():
                    return cli
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return None
    try:
        import winreg
        locations = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as root_key:
            count = winreg.QueryInfoKey(root_key)[0]
            for idx in range(count):
                try:
                    sub_name = winreg.EnumKey(root_key, idx)
                except OSError:
                    continue
                if not sub_name.lower().startswith('openai.codex_'):
                    continue
                try:
                    with winreg.OpenKey(root_key, sub_name) as sub_key:
                        loc, _ = winreg.QueryValueEx(sub_key, 'PackageRootFolder')
                except OSError:
                    continue
                if isinstance(loc, str) and loc.strip():
                    locations.append((_parse_codex_package_version(sub_name), loc.strip()))
        for _, location in sorted(locations, reverse=True):
            cli = Path(location) / 'app' / 'resources' / 'codex.exe'
            if cli.is_file():
                return cli
    except (ImportError, OSError, ValueError):
        pass
    return None


def _find_binary(name):
    if name == 'claude':
        return _find_claude()
    if name == 'dsh':
        return _find_dsh()
    override = os.environ.get('CAE_' + name.upper() + '_BINARY')
    if override:
        candidate = Path(override).expanduser()
        found = shutil.which(override) or (str(candidate) if candidate.is_file() else None)
        if not found:
            raise HarnessError(f'指定的 {name} 程序不存在')
        return found
    home = Path.home()
    found = shutil.which(name)
    if found and Path(found).suffix.lower() not in ('.cmd', '.bat'):
        if name == 'codex' and not _is_posix():
            local_app = Path(os.environ.get('LOCALAPPDATA') or (home / 'AppData' / 'Local'))
            codex_bin_root = local_app / 'OpenAI' / 'Codex' / 'bin'
            found_path = Path(found)
            found_norm = found_path.as_posix().lower()
            is_alias_stub = '/microsoft/windowsapps/' in found_norm
            try:
                in_desktop_bin = found_path.resolve().is_relative_to(codex_bin_root.resolve())
            except OSError:
                in_desktop_bin = False
            in_winapps = '/windowsapps/' in found_norm
            if in_desktop_bin or in_winapps:
                pkg_cli = _find_windows_codex_package_cli() if not os.environ.get('CAE_CODEX_HOME') else None
                real_found = found_path if not is_alias_stub else None
                ranked = _rank_codex_candidates([*_sorted_codex_binaries(codex_bin_root), pkg_cli, real_found])
                if ranked:
                    return str(ranked[0])
                if is_alias_stub:
                    found = None
        if found:
            return found
    candidates = [home / '.gemini' / 'bin' / ('agy' if _is_posix() else 'agy.exe')] if name == 'agy' else []
    if name == 'codex' and not _is_posix():
        for standalone in (home / '.local' / 'bin' / 'codex.exe', home / '.cargo' / 'bin' / 'codex.exe'):
            if standalone.is_file():
                return str(standalone)
        local_app = Path(os.environ.get('LOCALAPPDATA') or (home / 'AppData' / 'Local'))
        pkg_cli = _find_windows_codex_package_cli() if not os.environ.get('CAE_CODEX_HOME') else None
        candidates.extend(_rank_codex_candidates([*_sorted_codex_binaries(local_app / 'OpenAI' / 'Codex' / 'bin'), pkg_cli]))
    candidates.append(home / '.local' / 'bin' / (name if _is_posix() else name + '.exe'))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    if name == 'codex' and _is_posix() and shutil.which('wslpath'):
        found_exe = shutil.which('codex.exe')
        found_norm = Path(found_exe).as_posix().lower() if found_exe else ''
        is_alias_stub = '/microsoft/windowsapps/' in found_norm
        in_managed = ('/windowsapps/' in found_norm) or ('/openai/codex/bin/' in found_norm)
        if found_exe and not in_managed:
            return found_exe
        win_desktop_bins = []
        if shutil.which('cmd.exe'):
            try:
                local_app = Path(_wsl_path(_windows_env('LOCALAPPDATA'), windows=False))
                win_desktop_bins = _sorted_codex_binaries(local_app / 'OpenAI' / 'Codex' / 'bin')
            except (HarnessError, OSError):
                pass
        pkg_cli = _find_windows_codex_package_cli() if not os.environ.get('CAE_CODEX_HOME') else None
        real_found = found_exe if (found_exe and not is_alias_stub) else None
        ranked = _rank_codex_candidates([*win_desktop_bins, pkg_cli, real_found])
        if ranked:
            return str(ranked[0])
        if real_found:
            return real_found
    if name == 'agy':
        win_agy = _find_windows_agy_in_wsl()
        if win_agy:
            return win_agy
    return None


def _find_windows_claude_in_wsl():
    if not (_is_posix() and shutil.which('wslpath')):
        return None
    found = shutil.which('claude.exe')
    if found:
        return found
    try:
        win_home = Path(_wsl_path(_windows_env('USERPROFILE'), windows=False))
        candidate = win_home / '.local/bin/claude.exe'
        return str(candidate) if candidate.is_file() else None
    except (HarnessError, OSError):
        return None


def _find_claude():
    override = os.environ.get('CAE_CLAUDE_BINARY')
    if override:
        candidate = Path(override).expanduser()
        found = shutil.which(override) or (str(candidate) if candidate.is_file() else None)
        if not found:
            raise HarnessError('指定的 claude 程序不存在')
        return found
    found = shutil.which('claude')
    candidate = Path.home() / '.local/bin' / ('claude' if _is_posix() else 'claude.exe')
    if found and not (not _is_posix() and Path(found).suffix.lower() in ('.cmd', '.bat') and candidate.is_file()):
        return found
    if candidate.is_file():
        return str(candidate)
    return _find_windows_claude_in_wsl()


def _find_windows_dsh_in_wsl():
    if not (_is_posix() and shutil.which('wslpath')):
        return None
    candidates = [Path('/mnt/c/Program Files/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd')]
    try:
        win_home = Path(_wsl_path(_windows_env('USERPROFILE'), windows=False))
        candidates.insert(0, win_home / 'AppData/Local/Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd')
    except (HarnessError, OSError):
        pass
    return next((str(path) for path in candidates if path.is_file()), None)


def _find_dsh():
    override = os.environ.get('CAE_DSH_BINARY')
    if override:
        candidate = Path(override).expanduser()
        found = shutil.which(override) or (str(candidate) if candidate.is_file() else None)
        if not found:
            raise HarnessError('指定的 dsh 程序不存在')
        return found
    found = shutil.which('dsh')
    if found:
        return found
    if _is_posix():
        candidates = (Path.home() / '.local/bin/dsh', Path('/usr/local/bin/dsh'),
                      Path('/opt/DeepSeek Harness/resources/runtime/cli/bin/dsh'))
    else:
        candidates = (Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') /
                      'Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd',
                      Path(os.environ.get('ProgramFiles') or 'C:/Program Files') /
                      'DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd')
    return next((str(path) for path in candidates if path.is_file()), None) or _find_windows_dsh_in_wsl()


def _claude_home(binary):
    interop = bool(binary and _is_posix() and binary.lower().endswith('.exe'))
    if os.environ.get('CAE_CLAUDE_HOME'):
        return Path(os.environ['CAE_CLAUDE_HOME']).expanduser(), interop
    if interop:
        win_home = Path(_wsl_path(_windows_env('USERPROFILE'), windows=False))
        return win_home / '.claude', True
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').expanduser(), False


def _dsh_home(binary):
    interop = bool(binary and _is_posix() and Path(binary).suffix.lower() in ('.exe', '.cmd', '.bat'))
    if os.environ.get('CAE_DSH_HOME'):
        return Path(os.environ['CAE_DSH_HOME']).expanduser(), interop
    if interop:
        win_home = Path(_wsl_path(_windows_env('USERPROFILE'), windows=False))
        return win_home / '.dsh', True
    return Path(os.environ.get('DSH_HOME') or Path.home() / '.dsh').expanduser(), False


def _codex_home(binary):
    interop = bool(binary and _is_posix() and binary.lower().endswith('.exe'))
    if os.environ.get('CAE_CODEX_HOME'):
        return Path(os.environ['CAE_CODEX_HOME']).expanduser(), interop
    if interop:
        for parent in Path(binary).parents:
            if parent.name.lower() == 'appdata':
                return parent.parent / '.codex', True
        windows_home = _windows_env('USERPROFILE')
        return Path(_wsl_path(Path(windows_home), windows=False)) / '.codex', True
    return Path(os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')).expanduser(), False


def _agy_home(binary):
    interop = bool(binary and _is_posix() and binary.lower().endswith('.exe'))
    if os.environ.get('CAE_AGY_HOME'):
        return Path(os.environ['CAE_AGY_HOME']).expanduser(), interop
    if interop:
        for parent in Path(binary).parents:
            if parent.name.lower() == '.gemini':
                return parent, True
        windows_home = _windows_env('USERPROFILE')
        return Path(_wsl_path(Path(windows_home), windows=False)) / '.gemini', True
    return Path.home() / '.gemini', False


def resolve_caller(value):
    raw = str(value or '').strip().lower()
    if raw == 'auto':
        explicit_env = os.environ.get('CAE_HARNESS_CALLER', '').strip().lower()
        if explicit_env:
            raw = explicit_env
        elif any(os.environ.get(k, '').strip() for k in ('ANTIGRAVITY_AGENT', 'ANTIGRAVITY_CONVERSATION_ID', 'ANTIGRAVITY_APP_DATA_DIR')):
            raw = 'antigravity'
        elif any(os.environ.get(k, '').strip() for k in ('CODEX_THREAD_ID', 'CODEX_CI')):
            raw = 'codex'
        elif any(os.environ.get(k, '').strip() for k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT')):
            raw = 'claude'
        elif any(os.environ.get(k, '').strip() for k in ('DSH_SESSION_ID', 'DSH_PROFILE')):
            raw = 'dsh'
        else:
            raw = ''
    raw = {'cc': 'claude', 'agy': 'antigravity'}.get(raw, raw)
    return raw if raw in ('codex', 'antigravity', 'claude', 'dsh') else 'caller'


def _engine_parts(spec):
    prefix, _, model = spec.partition(':')
    prefix = 'claude' if prefix == 'cc' else prefix
    if prefix not in ('codex', 'agy', 'claude', 'dsh'):
        raise HarnessError('本协作机制仅支持 agy、codex、claude (cc) 和 dsh 后端')
    if model == '1m':
        emit_event('deprecated_model_alias', engine=prefix, alias='1m', replacement='default')
        model = 'default'
    return prefix, model if model and model != 'default' else ''


def _peer_auto_ready(backends, name):
    info = backends.get(name, {})
    return bool(info.get('available') and (name not in ('claude', 'dsh') or info.get('llm_configured')))


def _resolve_role_engine(backends, caller, role_spec, prompt_text=''):
    role_name = role_spec.split(':', 1)[1].strip().lower()
    roles = _fleet_profile().get('roles')
    role_cfg = roles.get(role_name) if isinstance(roles, dict) else None
    candidates = role_cfg.get('candidates') if isinstance(role_cfg, dict) else None
    if not isinstance(candidates, list) or not candidates:
        raise HarnessError(f'未知或未配置的角色槽位: {role_name!r}（支持 role:math、role:code、role:review、role:fast、role:orchestrator）')
    negated = _extract_negated_backends(prompt_text)
    caller_backend = {'antigravity': 'agy', 'codex': 'codex', 'claude': 'claude', 'dsh': 'dsh'}.get(resolve_caller(caller))
    parsed = []
    for raw_cand in candidates:
        if not isinstance(raw_cand, str) or ':' not in raw_cand:
            continue
        cand_prefix, _ = _engine_parts(raw_cand)
        if cand_prefix not in negated:
            parsed.append((cand_prefix, raw_cand))
    if not parsed:
        raise HarnessError(f'提示词已排除角色 {role_name} 的全部候选后端，请显式指定 --engine/--worker')
    ready_peers = [spec for prefix, spec in parsed if prefix != caller_backend and _peer_auto_ready(backends, prefix)
                   and not _active_quota_record(prefix, backends[prefix])]
    if ready_peers:
        return ready_peers[0]
    ready_any = [spec for prefix, spec in parsed if _peer_auto_ready(backends, prefix)
                 and not _active_quota_record(prefix, backends[prefix])]
    if ready_any:
        return ready_any[0]
    avail_any = [spec for prefix, spec in parsed if backends.get(prefix, {}).get('available')]
    if avail_any:
        return avail_any[0]
    raise HarnessError(f'未发现角色 {role_name} 可用的已安装后端，请先配置对应客户端')


def choose_engine(backends, caller, explicit='', prompt_text=''):
    if explicit and explicit.strip().lower().startswith('role:'):
        explicit = _resolve_role_engine(backends, caller, explicit.strip(), prompt_text)
        name, _ = _engine_parts(explicit)
    elif explicit:
        name, _ = _engine_parts(explicit)
        explicit = name + ':' + (explicit.partition(':')[2] or 'default')
    elif hinted := _extract_prompt_backend_hint(prompt_text):
        name, explicit = hinted, hinted + ':default'
    else:
        fallback_map = _fleet_profile().get('peer_fallback_order')
        default_orders = fallback_map if isinstance(fallback_map, dict) else {
            'antigravity': ['codex', 'claude', 'dsh'],
            'codex': ['agy', 'claude', 'dsh'],
            'claude': ['codex', 'agy', 'dsh'],
            'dsh': ['codex', 'agy', 'claude', 'dsh'],
        }
        order = default_orders.get(resolve_caller(caller))
        if not order:
            raise HarnessError('请指定 --caller，或指定 --engine/--worker/--left（或 role:<math|code|review|fast>）')
        negated = _extract_negated_backends(prompt_text)
        allowed = [peer for peer in order if peer not in negated]
        if not allowed:
            raise HarnessError('提示词已排除全部候选后端，请显式指定 --engine/--worker')
        name = next((peer for peer in allowed if _peer_auto_ready(backends, peer)), allowed[0])
        explicit = name + ':default'
    if not backends.get(name, {}).get('available'):
        raise HarnessError(f'未发现所选后端 {name}，请配置对应运行环境')
    return explicit


def _is_link(path, *, st=None):
    st = path.lstat() if st is None else st
    attributes = getattr(st, 'st_file_attributes', 0) or 0
    return stat.S_ISLNK(st.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _ps_literal(text):
    return "'" + text.replace("'", "''") + "'"


def _stage_wsl_readonly_workspace(target_dir, stage_root, rel_files=None, *, exclude_agent_instructions=False):
    """将只读目标文件镜像到临时目录；清单不能逃出原目标树。"""
    target_dir = target_dir.resolve()
    stage_dir = stage_root / 'workspace'
    stage_dir.mkdir(parents=True, exist_ok=True)
    if rel_files is None:
        records = collect_target_files(target_dir)
        if exclude_agent_instructions:
            records = [
                item for item in records
                if Path(item['rel_path']).name.upper() not in ('AGENTS.MD', 'CLAUDE.MD')
            ]
    else:
        records = []
        for rel in rel_files:
            rel = _scope_patterns([rel])[0]
            src = target_dir / rel
            try:
                src.resolve().relative_to(target_dir)
                # 叶文件正常也可能经过目录链接，逐级检查阻止镜像树外材料。
                ancestors = [src] + list(src.parents)[:len(src.relative_to(target_dir).parts) - 1]
                if any(_is_link(path) for path in ancestors) or not src.is_file():
                    raise HarnessError(f'只读镜像不接受文件或目录链接: {rel}')
                records.append({'path': src, 'rel_path': rel, 'bytes': src.stat().st_size})
            except (OSError, ValueError) as exc:
                raise HarnessError(f'无法在目标目录内镜像文件: {rel}') from exc
    stage_limit = _max_interop_stage_bytes()
    if sum(item['bytes'] for item in records) > stage_limit:
        limit_mib = max(1, stage_limit // (1024 * 1024))
        raise HarnessError(f'WSL 纯 Linux 路径只读镜像超出 {limit_mib}MB 上限，请缩小 --target 范围或在 WSL 安装原生 codex')
    windows_names = {}
    for item in records:
        parts = Path(item['rel_path']).parts
        for index, part in enumerate(parts, 1):
            if (re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((' ', '.'))
                    or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)):
                raise HarnessError(f'源文件名无法在 Windows 镜像中无损表示: {item["rel_path"]}')
            spelling = '/'.join(parts[:index])
            key = spelling.casefold()
            if key in windows_names and windows_names[key] != spelling:
                raise HarnessError(f'源路径在 Windows 镜像中发生大小写冲突: {windows_names[key]} / {spelling}')
            windows_names[key] = spelling
    for item in records:
        try:
            item['path'].resolve().relative_to(target_dir)
        except (OSError, ValueError) as exc:
            raise HarnessError(f'镜像源已离开目标目录: {item["rel_path"]}') from exc
        dst = stage_dir / item['rel_path']
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item['path'], dst)
    return stage_dir


def _rewrite_staged_paths(parsed, stage_dir):
    # Windows 可改变回执中的大小写；使用镜像里保留的原拼写还原 ext4 路径。
    original_paths = {}
    for directory, _dirs, files in os.walk(stage_dir):
        for name in files:
            relative = (Path(directory) / name).relative_to(stage_dir).as_posix()
            original_paths[relative.casefold()] = relative

    prefixes = [(str(stage_dir).replace('\\', '/').rstrip('/') + '/', os.name == 'nt'),
                (str(stage_dir.resolve()).replace('\\', '/').rstrip('/') + '/', os.name == 'nt')]
    if _is_posix() and shutil.which('wslpath'):
        try:
            prefixes.append((_wsl_path(stage_dir, windows=True).replace('\\', '/').rstrip('/') + '/', True))
        except HarnessError:
            pass

    def _clean(raw_path):
        norm = raw_path.strip().replace('\\', '/')
        for prefix, case_insensitive in prefixes:
            value = norm.casefold() if case_insensitive else norm
            base = prefix.casefold() if case_insensitive else prefix
            if value.startswith(base):
                relative = posixpath.normpath(norm[len(prefix):])
                return original_paths.get(relative.casefold(), relative)
        return original_paths.get(posixpath.normpath(norm).casefold(), raw_path)

    if 'read_files' in parsed:
        parsed['read_files'] = [_clean(item) for item in parsed['read_files']]
    if 'unread_files' in parsed:
        parsed['unread_files'] = [{**item, 'file': _clean(item['file'])} for item in parsed['unread_files']]
    if 'findings' in parsed:
        parsed['findings'] = [{**item, 'file': _clean(item['file'])} for item in parsed['findings']]
    return parsed


def _interop_command(command, work_dir, temp_dir, input_text, codex_home=None, extra_env: dict[str, str] | None = None):
    """监督进程写入本次真实 Windows PID，供 WSL 定向清理进程树。"""
    pid_file = temp_dir / 'native.pid'
    if input_text is None:
        pipes = [
            '$output = $p.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())',
            '$errors = $p.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())',
            'while ($null -ne ($line = [Console]::ReadLine())) {',
            '$p.StandardInput.WriteLine($line)', '$p.StandardInput.Flush()', '}',
            '$p.StandardInput.Close()', '$p.WaitForExit()', '$output.Wait()', '$errors.Wait()',
        ]
    else:
        prompt_file = temp_dir / 'input.txt'
        prompt_file.write_text(input_text, encoding='utf-8')
        pipes = [
            '$output = $p.StandardOutput.ReadToEndAsync()', '$errors = $p.StandardError.ReadToEndAsync()',
            '$inputBytes = [IO.File]::ReadAllBytes(' + _ps_literal(_wsl_path(prompt_file)) + ')',
            '$p.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)', '$p.StandardInput.BaseStream.Flush()',
            '$p.StandardInput.Close()', '$p.WaitForExit()',
            '[Console]::Out.Write($output.Result)', '[Console]::Error.Write($errors.Result)',
        ]
    args = subprocess.list2cmdline(command[1:])
    executable = _wsl_path(Path(command[0]))
    if Path(command[0]).suffix.lower() in ('.cmd', '.bat'):
        batch_args = [str(arg).replace('%', '%%') for arg in command[1:]]
        args = '/d /s /c "' + subprocess.list2cmdline([executable, *batch_args]) + '"'
        executable = 'cmd.exe'
    env_lines = ["$info.EnvironmentVariables['CAE_HARNESS_DEPTH'] = '1'"]
    if codex_home:
        env_lines.insert(0, "$info.EnvironmentVariables['CODEX_HOME'] = " + _ps_literal(_wsl_path(Path(codex_home))))
    for key, value in (extra_env or {}).items():
        env_lines.append('$info.EnvironmentVariables[' + _ps_literal(key) + '] = ' + _ps_literal(value))
    script = '\n'.join([
        "$ErrorActionPreference='Stop'",
        "$ProgressPreference='SilentlyContinue'",
        '[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)',
        '[IO.File]::WriteAllText(' + _ps_literal(_wsl_path(pid_file.with_suffix('.pending'))) + ', [string]$PID)',
        '[IO.File]::Move(' + _ps_literal(_wsl_path(pid_file.with_suffix('.pending'))) + ', ' + _ps_literal(_wsl_path(pid_file)) + ')',
        '$info = New-Object System.Diagnostics.ProcessStartInfo',
        '$info.FileName = ' + _ps_literal(executable),
        '$info.Arguments = ' + _ps_literal(args),
        '$info.WorkingDirectory = ' + _ps_literal(_wsl_path(work_dir)),
        '$info.UseShellExecute = $false', '$info.CreateNoWindow = $true',
        '$info.RedirectStandardInput = $true', '$info.RedirectStandardOutput = $true', '$info.RedirectStandardError = $true',
        '$info.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)',
        '$info.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)',
        *env_lines,
        '$p = New-Object System.Diagnostics.Process', '$p.StartInfo = $info',
        '$started = $false', '$exitCode = 1', 'try {',
        '[void]$p.Start()', '$started = $true',
        *pipes,
        '$exitCode = $p.ExitCode', '} finally {',
        'if ($started) {',
        '  try {',
        "    $tk = New-Object System.Diagnostics.ProcessStartInfo('taskkill.exe', ('/T /F /PID ' + $p.Id))",
        '    $tk.UseShellExecute = $false; $tk.CreateNoWindow = $true',
        '    $tk.RedirectStandardOutput = $true; $tk.RedirectStandardError = $true',
        '    $kp = [System.Diagnostics.Process]::Start($tk)',
        '    if ($null -ne $kp) {',
        '      $tkOut = $kp.StandardOutput.ReadToEndAsync(); $tkErr = $kp.StandardError.ReadToEndAsync()',
        "      if (-not $kp.WaitForExit(5000)) { try { $kp.Kill() } catch {}; [Console]::Error.WriteLine('[cae-harness] taskkill cleanup timed out for PID ' + $p.Id) }",
        '      [void]$tkOut.Wait(1000); [void]$tkErr.Wait(1000); $kp.Dispose()',
        '    }',
        "  } catch { [Console]::Error.WriteLine('[cae-harness] taskkill cleanup failed: ' + $_.Exception.Message) }",
        '}',
        '$p.Dispose()', '}', 'exit $exitCode',
    ])
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    return ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], pid_file


# ════════════════════════════════════════════════════════════════════════════
# ③ 模型目录、语义线索、挡位策略与执行画像
# ════════════════════════════════════════════════════════════════════════════


def _tokens_lower(text):
    return set(re.split(r'[^a-z0-9]+', str(text or '').lower())) - {''}


def _alias_tokens(text):
    return set(re.findall(r'[a-z0-9]+(?:\.[a-z0-9]+)*', str(text or '').lower()))


def _model_records(cache):
    items = cache.get('models', []) if isinstance(cache, dict) else cache
    if not isinstance(items, list):
        return []
    fields = ('slug', 'display_name', 'description', 'priority', 'visibility', 'default_reasoning_level',
              'context_window', 'max_context_window', 'effective_context_window_percent',
              'provider', 'model_provider', 'category', 'routing_type', 'model_specialty', 'modelSpecialty', 'supported_in_api')
    records = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('slug'), str) or not item['slug'].strip():
            continue
        rec = {key: item[key] for key in fields if key in item}
        levels = item.get('supported_reasoning_levels')
        if isinstance(levels, list):
            efforts = [lvl.get('effort') for lvl in levels if isinstance(lvl, dict) and lvl.get('effort') in EFFORT_LADDER]
            if efforts:
                rec['supported_efforts'] = efforts
            delegating = [
                lvl.get('effort')
                for lvl in levels
                if isinstance(lvl, dict)
                and lvl.get('effort') in EFFORT_LADDER
                and ('delegation' in str(lvl.get('description') or '').lower() or lvl.get('effort') == 'ultra')
            ]
            if delegating:
                rec['delegating_efforts'] = delegating
        elif isinstance(item.get('supported_efforts'), list):
            efforts = [e for e in item['supported_efforts'] if e in EFFORT_LADDER]
            rec['supported_efforts'] = efforts
            delegating = [e for e in efforts if e == 'ultra' or e in (item.get('delegating_efforts') or [])]
            if delegating:
                rec['delegating_efforts'] = delegating
        records.append(rec)
    return records


def _parse_agy_models(output):
    models = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or '\t' not in line:
            continue
        slug, display_name = line.split('\t', 1)
        slug, display_name = slug.strip(), display_name.strip()
        if slug:
            models.append({'slug': slug, 'display_name': display_name})
    return models


def _agy_slug_tier(slug):
    match = re.match(r'^(.+)-(minimal|low|medium|high|xhigh|max|ultra)$', str(slug or '').strip().lower())
    return (match.group(1), match.group(2)) if match else ('', '')


def _catalog_order(item):
    priority = item.get('priority')
    if type(priority) is not int or priority < 0:
        priority = 9999
    description = str(item.get('description') or '').strip().lower()
    return (bool(re.match(r'(?:older|previous|legacy)\b', description)), priority,
            str(item.get('slug') or '').lower())


def _is_agy_orchestrator(record):
    # Published Antigravity fixed composite: planning/review and execution
    # alternate within the same agent. It does not accept a reasoning tier.
    return 'orchestrator' in _tokens_lower(f"{record.get('slug', '')} {record.get('display_name', '')}")


def _model_category(prefix, record):
    slug = str(record.get('slug') or '').lower()
    label = str(record.get('display_name') or '')
    category = str(record.get('category') or record.get('routing_type') or '').lower()
    provider = str(record.get('provider') or record.get('model_provider') or '').lower()
    if prefix == 'agy' and _is_agy_orchestrator(record):
        return 'fixed_composite'
    tags = _tokens_lower(f'{slug} {label}')
    if tags & ROUTER_MODEL_TAGS or category in ('router', 'composite', 'hybrid', 'auto', 'fixed_composite'):
        return 'router_or_composite'
    if 'auto-review' in slug or record.get('visibility') == 'hide':
        return 'hidden_or_internal'
    if category in ('hosted', 'third_party', 'hosted_or_third_party', 'third-party'):
        return 'hosted_or_third_party'
    if prefix == 'agy' and slug.startswith('gemini-') and provider in ('', 'google', 'gemini'):
        return 'native'
    if prefix == 'codex' and provider in ('', 'openai') and (
            provider == 'openai' or re.match(r'^(?:gpt-[0-9]+(?:\.[0-9]+)?(?:-|$)|o[1-9]\d*(?:-|$))', slug)):
        return 'native'
    if prefix == 'claude' and (provider in ('', 'anthropic', 'claude_configured') or slug in ('sonnet', 'opus', 'haiku')):
        return 'native'
    if prefix == 'dsh' and (provider in ('', 'deepseek', 'deepseek-official', 'deepseek-account') or slug.startswith('deepseek-')):
        return 'native'
    if provider or re.match(r'^(?:claude-|gpt-oss-|deepseek-|qwen-|mistral-|llama-)', slug):
        return 'hosted_or_third_party'
    return 'unknown'


def _model_specialty(record):
    specialty = str(record.get('model_specialty') or record.get('modelSpecialty') or '').lower()
    description = str(record.get('description') or '').lower()
    slug_tokens = _tokens_lower(record.get('slug'))
    if specialty in ('fast', 'lightweight', 'speed') or slug_tokens & _fast_model_tags() or re.search(
            r'\b(?:fast and affordable|easier tasks|lightweight)\b', description):
        return 'fast'
    if specialty in ('deep', 'reasoning', 'deep_reasoning') or slug_tokens & _deep_model_tags('codex') or re.search(
            r'\b(?:most demanding|frontier intelligence|deepest reasoning|hardest problems)\b', description):
        return 'deep'
    return 'engineering'


def _codex_versioned_line(slug):
    match = re.match(r'^gpt-(\d+(?:\.\d+)*)-([a-z][a-z0-9-]*)$', str(slug or '').strip().lower())
    if not match:
        return None, None
    return tuple(int(part) for part in match.group(1).split('.')), match.group(2)


def _unique_model_matches(query, models):
    by_slug = {item['slug']: item for item in models}
    if len(by_slug) > 1:
        choices = sorted(by_slug)
        raise HarnessError(f'模型别名 {query!r} 不唯一，请指定精确 slug', model_alias=query, matching_models=choices)
    return next(iter(by_slug.values()), {})


def _exact_model_record(models, query):
    value = query.lower()
    return _unique_model_matches(query, [item for item in models if item['slug'].lower() == value]) or _unique_model_matches(
        query, [item for item in models if str(item.get('display_name') or '').lower() == value])


def _resolve_explicit_codex_record(models, explicit_model):
    query = str(explicit_model or '').strip()
    if not query:
        return {}
    exact = _exact_model_record(models, query)
    if exact:
        return exact
    terms = _alias_tokens(query)
    matches = [
        item for item in models if terms and terms <= _alias_tokens(f"{item['slug']} {item.get('display_name', '')}")
        and _model_category('codex', item) != 'hidden_or_internal'
    ]
    if len({item['slug'] for item in matches}) > 1 and not any(re.search(r'\d', tok) for tok in terms):
        parsed = [(item, *_codex_versioned_line(item['slug'])) for item in matches]
        lines = {line for _item, ver, line in parsed if ver is not None and line is not None}
        if len(lines) == 1 and all(ver is not None for _item, ver, _line in parsed):
            return max(parsed, key=lambda row: (row[1], -_catalog_order(row[0])[1]))[0]
    return _unique_model_matches(query, matches)


def _agy_families(models):
    families = {}
    for item in models:
        if _model_category('agy', item) in ('fixed_composite', 'router_or_composite'):
            continue
        name, effort = _agy_slug_tier(item['slug'])
        if name and effort:
            family = families.setdefault(name, {})
            family[effort] = _unique_model_matches(name, [x for x in (family.get(effort), item) if x])
    return families


def _agy_family_order(name):
    generation = re.search(r'^gemini-(\d+(?:\.\d+)*)-', name)
    version = tuple(int(v) for v in generation.group(1).split('.')) if generation else ()
    tags = _tokens_lower(name)
    configured_priority = _catalog_rules('agy').get('family_priority') or ['pro']
    priority = next((n for n, tag in enumerate(configured_priority) if str(tag).lower() in tags), 9)
    return tuple(-v for v in (version + (0, 0, 0))[:3]), priority, name


def _match_effort_level(supported, target_tier, delegating_efforts=None):
    """max 表示目录声明的最高非委派挡；仅显式 ultra 可以放行委派挡。"""
    valid = [item for item in supported if item in EFFORT_LADDER]
    if target_tier != 'ultra':
        blocked = set(delegating_efforts or ()) | {'ultra'}
        valid = [item for item in valid if item not in blocked]
    if not valid:
        return None
    if target_tier in ('max', 'ultra'):
        return max(valid, key=EFFORT_LADDER.index)
    target_rank = EFFORT_LADDER.index(target_tier)
    at_or_below = [item for item in valid if EFFORT_LADDER.index(item) <= target_rank]
    return max(at_or_below, key=EFFORT_LADDER.index) if at_or_below else min(valid, key=EFFORT_LADDER.index)


def _only_delegating_efforts(record, tier):
    efforts = record.get('supported_efforts') or []
    return bool(efforts and tier != 'ultra' and not _match_effort_level(efforts, tier, record.get('delegating_efforts')))


def _match_agy_family(family, tier):
    return family.get(_match_effort_level(family, tier), {})


def _resolve_explicit_agy_record(models, query, tier):
    exact = _exact_model_record(models, query)
    if exact:
        return exact, 'explicit_engine_model'
    terms = _alias_tokens(query)
    variant_terms = terms & set(EFFORT_LADDER)
    family_terms = terms - variant_terms
    wants_composite = bool(terms & ROUTER_MODEL_TAGS or '混动' in query)
    candidates = []
    if family_terms and not wants_composite:
        for family, variants in _agy_families(models).items():
            if family_terms <= _alias_tokens(family):
                if variant_terms:
                    candidates.extend(record for level, record in variants.items() if level in variant_terms)
                else:
                    record = _match_agy_family(variants, tier)
                    if record:
                        candidates.append(record)
                    elif variants:
                        # Expose a named ultra-only family as blocked, not unknown.
                        candidates.extend(variants.values())
    for item in models:
        composite = _model_category('agy', item) in ('fixed_composite', 'router_or_composite')
        if composite != wants_composite:
            continue
        if not composite and _agy_slug_tier(item['slug'])[1]:
            continue
        if (terms and terms <= _alias_tokens(f"{item['slug']} {item.get('display_name', '')}")) or (
                wants_composite and not terms and composite):
            candidates.append(item)
    return _unique_model_matches(query, candidates), 'explicit_fuzzy_model'


def _semantic_text(text):
    return re.sub(r'(?m)^\s*>[^\n]*|\x60{3}[\s\S]*?\x60{3}|\x60[^\x60\n]*\x60|'
                  r'“[^”]*”|‘[^’]*’|"[^"\n]*"|(?<![A-Za-z0-9_])\x27[^\x27\n]*\x27(?![A-Za-z0-9_])',
                  ' ', str(text or ''))


def _cue_is_negated(prefix):
    # “能不能降档”“要不省点”是在提议；不能因其中一个“不”反转意图。
    prefix = re.sub(r'(?<!不)(?:能不能|可不可以|要不|不妨|何不)', ' ', prefix)
    return bool(_NEGATION.search(prefix))


def _cue_pattern(cue):
    escaped = re.escape(cue)
    # 中英文混写中的空白不影响意图，例如“省  token”和“save   tokens”。
    escaped = escaped.replace(r'\ ', r'\s+')
    if cue.isascii():
        return r'(?<![A-Za-z0-9_])' + escaped + r'(?![A-Za-z0-9_])'
    return escaped


def _has_cue(text, cues, *, negated=False):
    if not cues:
        return False
    pattern = re.compile('|'.join(_cue_pattern(cue) for cue in cues), re.I)
    for clause in _CUE_BOUNDARIES.split(_semantic_text(text)):
        for match in pattern.finditer(clause):
            if _cue_is_negated(clause[:match.start()]) == negated:
                return True
    return False


def _has_negated_cue(text, cues):
    return _has_cue(text, cues, negated=True)


def _has_positive_cue(text, cues):
    return _has_cue(text, cues)


_BACKEND_HINT_NAMES = {
    'claude': r'(?:claude\s*(?:code|cli)|cc(?:\s+cli)?)',
    'dsh': r'(?:deepseek\s*harness|dsh(?:\s+cli)?)',
    'codex': r'codex',
    'agy': r'(?:反重力|antigravity|agy)',
}


def _extract_negated_backends(prompt_text):
    text = _semantic_text(prompt_text)
    if not text:
        return set()
    negated = set()
    for clause in _CUE_BOUNDARIES.split(text):
        for name, pattern in _BACKEND_HINT_NAMES.items():
            for match in re.finditer(r'(?<![A-Za-z0-9_.=-])' + pattern + r'(?![A-Za-z0-9_.=-])', clause, re.I):
                if _cue_is_negated(clause[:match.start()]):
                    negated.add(name)
    return negated


def _extract_prompt_backend_hint(prompt_text):
    text = _semantic_text(prompt_text)
    action = r'(?:请)?(?:使用|调用|采用|切到|切换至|指定|选用|改用|交给|让|用)\s*'
    english = r'\b(?:use|invoke|via|switch\s+to)\s+'
    candidates = []
    for name, pattern in _BACKEND_HINT_NAMES.items():
        for match in re.finditer('(?:' + action + '|' + english + ')' + pattern + r'(?![A-Za-z0-9_.=-])', text, re.I):
            if not _cue_is_negated(_CUE_BOUNDARIES.split(text[:match.start()])[-1]):
                candidates.append((match.start(), name))
    return min(candidates)[1] if candidates else ''


def _extract_prompt_model_hint(prompt_text):
    text = _semantic_text(prompt_text).strip()
    if not text:
        return ''
    stop_words = NON_AI_MODEL_STOPWORDS - ROUTER_MODEL_TAGS
    for pattern in PROMPT_MODEL_HINT_PATTERNS:
        for match in pattern.finditer(text):
            prefix = _CUE_BOUNDARIES.split(text[:match.start()])[-1]
            if _cue_is_negated(prefix):
                continue
            cand = re.sub(r'\s+', ' ', match.group(1)).strip()
            if not cand:
                continue
            if re.fullmatch(r'(?:claude\s*(?:code|cli)|deepseek\s*harness|cc(?:\s+cli)?|dsh(?:\s+cli)?)', cand, re.I):
                continue
            tokens = [tok for tok in re.findall(r'[a-z0-9]+', cand.lower()) if tok]
            collapsed = re.sub(r'[^a-z0-9]+', '', cand.lower())
            # 通用名称与 CFD 格式名称应排除，完整 gpt/gemini 型号则保留。
            provider_qualifiers = {'gpt', 'gemini', 'codex', 'agy', 'openai', 'google',
                                   'claude', 'cc', 'anthropic', 'dsh', 'harness'}
            if collapsed in stop_words or any(tok in stop_words - provider_qualifiers for tok in tokens):
                continue
            if collapsed in PHYSICAL_SUBSTANCE_TAGS:
                has_ai_qualifier = bool(re.search(r'(?:\bAI\b|\bagy\b|\bcodex\b|\bgemini\b|\borchestrator\b|[()（）])', match.group(0), re.IGNORECASE))
                tail_ctx = text[match.end():min(len(text), match.end() + 24)]
                head_ctx = text[max(0, match.start() - 16):match.start()]
                if not has_ai_qualifier and (
                    PHYSICS_MODEL_CONTEXT_RE.search(tail_ctx) or PHYSICS_MODEL_CONTEXT_RE.search(head_ctx)
                ):
                    continue
            if tokens or '混动' in cand:
                return cand
    return ''


def _is_fast_engine_spec(engine):
    raw = str(engine or '').strip().lower()
    if raw == 'role:fast':
        return True
    if ':' in raw:
        _, _, suffix = raw.partition(':')
        if suffix in ('fast', '@fast'):
            return True
    return False


def _role_recommended_tier(engine):
    raw = str(engine or '').strip().lower()
    role_name = ''
    if raw.startswith('role:'):
        role_name = raw.split(':', 1)[1].strip()
    elif ':' in raw:
        prefix, _, suffix = raw.partition(':')
        role_name = _per_backend_role_target(prefix, suffix)
    if not role_name:
        return '', ''
    roles = _fleet_profile().get('roles')
    role_cfg = roles.get(role_name) if isinstance(roles, dict) else None
    rec = str((role_cfg or {}).get('recommended_tier') or '').strip().lower() if isinstance(role_cfg, dict) else ''
    return role_name, (rec if rec in TIER_CHOICES and rec not in ('auto', 'ultra') else '')


def select_task_tier(requested_tier='auto', kind='review', text='', file_count=1, estimated_tokens=0, *, engine=''):
    """默认选用单模型非委派最高挡位；显式挡位、角色槽位推荐挡位、节省额度意图与环境默认值可覆盖自动策略。"""
    if requested_tier and requested_tier != 'auto':
        return requested_tier, 'explicit_tier_flag'
    sample = (text or '').strip()
    prose_requests = {
        'max': ('用最高挡', '用最高档', '选最高挡', '选最高档', '用最大挡', '用最大档', '使用 max 挡', 'use max effort'),
        'xhigh': ('用超高挡', '用超高档', '选超高挡', '选超高档', '使用 xhigh 挡', 'use xhigh effort'),
        'low': ('用低挡', '用低档', '选低挡', '选低档', '低挡快速', '低档快速', '降到低挡', '降到低档',
                '最低挡', '最低档', '使用 low 挡', 'use low effort'),
        'medium': ('用中挡', '用中档', '选中挡', '选中档', '降到中挡', '降到中档', '使用 medium 挡', 'use medium effort'),
        'high': ('用高挡', '用高档', '选高挡', '选高档', '降到高挡', '降到高档', '使用 high 挡', 'use high effort'),
    }
    for level, cues in prose_requests.items():
        if _has_positive_cue(sample, cues):
            return level, 'user_description_' + level
    role_name, role_tier = _role_recommended_tier(engine)
    if _is_fast_engine_spec(engine):
        fast_tier = role_tier or 'low'
        return fast_tier, ('role_fast_low' if fast_tier == 'low' else f'role_profile_fast_{fast_tier}')
    env_tier = os.environ.get('CAE_DEFAULT_TIER', '').strip().lower()
    if env_tier in TIER_CHOICES and env_tier not in ('auto', 'ultra'):
        return env_tier, 'env_default_tier'
    hd = _headless_tunables()
    short_max_files = _positive_int(hd.get('polish_short_max_files')) or 3
    short_max_tokens = _positive_int(hd.get('polish_short_max_tokens')) or 4_000
    short_max_chars = _positive_int(hd.get('polish_short_max_chars')) or 2_500
    if kind == 'polish' and not _has_positive_cue(sample, ('证明', '推导', '根因', 'prove', 'derive')):
        if file_count <= short_max_files and estimated_tokens <= short_max_tokens and len(sample) <= short_max_chars:
            return 'medium', 'complexity_lightweight_polish'
        return 'high', 'complexity_long_polish'
    if _has_positive_cue(sample, ECONOMY_DOWNGRADE_CUES):
        if (kind in ('review', 'code', 'audit')
                and (_has_positive_cue(sample, TRIVIAL_SURFACE_CUES)
                     or _has_positive_cue(sample, ('轻量快速', '快速扫', '简单扫', '快速看')))
                and not _has_positive_cue(sample, HIGH_COMPLEXITY_CUES)
                and not _has_positive_cue(sample, ('兼容', '回归', '正确性', '安全', '功能', '全量审计', 'verify', 'regression'))):
            return 'low', 'economy_trivial_surface_low'
        if (kind in ('math', 'direction') or _has_positive_cue(sample, HIGH_COMPLEXITY_CUES)) and not _has_positive_cue(
                sample, ('降挡', '降档', '降一挡', '降一档', '适当降挡', '适当降档', '降低挡位', '降低档位', 'lower tier', 'lower effort')):
            return 'max', f'task_specialty_{kind}_max' if kind in ('math', 'direction') else 'complexity_high_domain_max'
        return 'high', 'economy_adaptive_high'
    if role_tier and role_tier != 'max':
        return role_tier, f'role_profile_{role_name}_{role_tier}'
    if kind in ('math', 'direction'):
        return 'max', f'task_specialty_{kind}_max'
    if _has_positive_cue(sample, HIGH_COMPLEXITY_CUES):
        return 'max', 'complexity_high_domain_max'
    return 'max', 'default_flagship_max'


def _execution_side(info):
    return info.get('execution_side') or (
        'windows' if info.get('windows_interop') or not _is_posix() else 'posix')


def _execution_environment(info):
    return {'side': _execution_side(info), 'windows_interop': bool(info.get('windows_interop')),
            'binary': info.get('binary'), 'config_home': info.get('config_home'),
            'authentication_status': info.get('authentication_status', 'not_checked'),
            'fallback_from': info.get('fallback_from')}


def _execution_budget(prefix, info, record):
    hd = _headless_tunables()
    default = _positive_int(record.get('context_window'))
    maximum = _positive_int(record.get('max_context_window'))
    configured = _positive_int(info.get('configured_window_tokens'))
    compact = _positive_int(info.get('effective_compaction_tokens'))
    window = maximum or default
    percent = _positive_int(record.get('effective_context_window_percent'))
    if percent is not None and percent > 100:
        percent = None
    effective_window = window * percent // 100 if window is not None and percent is not None else None
    model_limit = effective_window if effective_window is not None else window
    requested = _positive_int(info.get('requested_input_budget_tokens'))
    policy_cap = _positive_int(hd.get('unlisted_window_fallback_tokens')) or 120_000
    raw_ratio = hd.get('input_budget_safety_ratio')
    ratio_permille = (
        int(round(float(raw_ratio) * 1000))
        if isinstance(raw_ratio, (int, float)) and not isinstance(raw_ratio, bool) and 0.50 <= float(raw_ratio) <= 1.0
        else 900
    )
    cap = min((v for v in (model_limit, compact) if v is not None), default=None)
    if cap is None:
        budget = requested or policy_cap
        source = 'explicit_unverified_planning_budget' if requested else 'conservative_estimate_without_model_window'
    elif requested:
        budget, source = min(requested, cap), 'explicit_planning_budget_capped'
    else:
        budget = min(cap, max(1, cap * ratio_permille // 1000))
        if not window:
            budget = min(budget, policy_cap)
        source = ('policy_estimate_90pct_of_planning_cap' if window and ratio_permille == 900 else
                  f'policy_estimate_{ratio_permille}permille_of_planning_cap' if window else
                  'conservative_estimate_capped_by_configured_compaction')
    return {
        'model_default_window_tokens': default, 'model_declared_max_window_tokens': maximum,
        'frontend_configured_window_tokens': configured, 'configured_compaction_tokens': compact,
        'selected_window_tokens': window, 'cli_context_window_tokens': window if prefix == 'codex' else None,
        'window_source': ('model_declared_max' if maximum else 'model_declared_default' if default else 'unknown'),
        'declared_effective_context_percent': percent,
        'catalog_effective_window_tokens': effective_window,
        'effective_window_source': 'catalog_percent_of_selected_window' if effective_window is not None else None,
        'planning_cap_tokens': cap, 'requested_input_budget_tokens': requested,
        'input_budget_tokens': budget, 'policy_reserve_tokens': cap - budget if cap is not None else None,
        'budget_source': source,
        'capacity_verified': False,
    }


def _profile(prefix, info, spec, slug, record, tier, source, effort=None, cli_effort=None,
             effort_source='unknown', error=None):
    catalog = info.get('model_catalog') or {}
    capacity = _execution_budget(prefix, info, record)
    if prefix == 'codex' and not capacity['selected_window_tokens'] and capacity['frontend_configured_window_tokens']:
        error = error or ('该子模型的默认及最大窗口均无目录声明，无法排除宿主全局窗口覆盖；'
                          '请刷新目录或选择有窗口声明的模型。')
    declared = effort if effort_source in ('catalog_declared', 'catalog_variant') else None
    if effort_source == 'fixed_configuration':
        adjustment = 'fixed_configuration_ignores_requested_tier'
    elif declared is None:
        adjustment = 'unsupported_tier_not_verified' if cli_effort == tier else 'requested_tier_not_verified'
    elif effort != tier:
        ladder = ('off', 'none', *EFFORT_LADDER)
        direction = 'lower' if ladder.index(effort) < ladder.index(tier) else 'higher'
        adjustment = f'{direction}_catalog_effort_or_model_variant'
    else:
        adjustment = None
    return {
        'spec': f'{prefix}:{slug}' if slug else f'{prefix}:default',
        'requested_engine': spec, 'requested_model': _engine_parts(spec)[1] or None,
        'model': slug or None, 'model_category': _model_category(prefix, record) if record else 'unknown',
        'reasoning_effort': effort, 'cli_effort': cli_effort, 'declared_effort': declared,
        'effective_effort': None, 'effort_verified': False,
        'tier': tier, 'selected_tier': tier, 'profile_source': source, 'effort_source': effort_source,
        'tier_adjustment': adjustment, 'execution_blocked_reason': error,
        'capacity': capacity, 'model_catalog': catalog,
        'model_listing': info.get('model_listing'),
        'catalog_usage': ('listing_declared' if catalog.get('source') == 'agy_models_cli' else
                          'builtin_contract' if catalog.get('source') == 'dsh_builtin_models' else
                          'local_configuration' if catalog.get('source') == 'claude_settings' else
                          'unverified_client_cache' if catalog.get('model_count') else 'unverified_or_missing'),
        'execution_environment': _execution_environment(info),
    }


def _require_execution_profile(profile):
    error = profile.get('execution_blocked_reason')
    if error:
        raise HarnessError(error, execution_profile=profile)


def _codex_profile(info, spec, record, slug, tier, source):
    effort = _match_effort_level(record.get('supported_efforts', []), tier, record.get('delegating_efforts'))
    if _only_delegating_efforts(record, tier):
        return _profile('codex', info, spec, slug, record, tier, source, effort_source='no_non_delegating_effort',
                        error='目录未声明可用的非委派挡位，请指定其他模型或显式选择 --tier ultra。')
    if effort:
        return _profile('codex', info, spec, slug, record, tier, source, effort, effort, 'catalog_declared')
    # Never inherit an interactive delegated setting. An unlisted named model
    # may receive a requested tier but the receipt cannot claim it is supported.
    effort = tier if tier != 'max' else 'high'
    return _profile('codex', info, spec, slug, record, tier, source, effort, effort, 'unverified_request')


def _agy_profile(info, spec, record, slug, tier, source):
    if _is_agy_orchestrator(record or {'slug': slug}):
        profile = _profile('agy', info, spec, slug, record, tier, source, effort_source='fixed_configuration')
        profile.update(model_category='fixed_composite',
                       fixed_configuration='same_agent_planning_review_and_execution',
                       effort_option_supported=False)
        return profile
    _, variant = _agy_slug_tier(slug)
    error = '模型变体会启用自动委派；请显式选择 --tier ultra 或其他模型。' if variant == 'ultra' and tier != 'ultra' else None
    return _profile('agy', info, spec, slug, record, tier, source, variant or None, None,
                    'catalog_variant' if record and variant else 'unverified_request' if variant else 'unknown', error)


def _select_codex_frontier_record(models):
    native = [record for record in models if _model_category('codex', record) == 'native']
    return min(native, key=_catalog_order) if native else {}


def _select_codex_specialty_record(info, kind, tier, prompt_text, estimated_tokens, *, prefer_fast=False, allow_tier_fast=True):
    models = [item for item in info.get('models', []) if _model_category('codex', item) == 'native']
    eligible = sorted((item for item in models if not _only_delegating_efforts(item, tier)), key=_catalog_order)
    # 同一产品线只自动采用最新一代；目录优先级仍用于不同产品线之间的选择。
    latest = {}
    for item in eligible:
        version, line = _codex_versioned_line(item['slug'])
        if version is not None:
            latest[line] = max(latest.get(line, ()), version)
    current = []
    for item in eligible:
        version, line = _codex_versioned_line(item['slug'])
        if version is None or version == latest[line]:
            current.append(item)
    eligible = current
    if not eligible:
        return {}, 'no_non_delegating_effort' if models else 'no_native_catalog_candidate'

    def budget(record):
        return _execution_budget('codex', info, record)['input_budget_tokens']

    if prefer_fast or (allow_tier_fast and tier == 'low'
                       and not _has_negated_cue(prompt_text, tuple({'轻量', '极速'} | _fast_model_tags('codex')))):
        fast = [item for item in eligible if _model_specialty(item) == 'fast' and estimated_tokens <= budget(item)]
        if fast:
            return fast[0], 'specialty_fast_lightweight'
    if (kind in ('math', 'direction') or (kind == 'review' and tier != 'low'
                                         and _has_positive_cue(prompt_text, HIGH_COMPLEXITY_CUES))):
        deep = [item for item in eligible if _model_specialty(item) == 'deep' and estimated_tokens <= budget(item)]
        if deep:
            return deep[0], 'specialty_deep_reasoning'
    # 默认选最高挡时不能因主力工程模型缺失而静默切到轻量模型。
    non_lightweight = [item for item in eligible if _model_specialty(item) != 'fast']
    engineering = [item for item in non_lightweight if _model_specialty(item) == 'engineering']
    workhorse_tags = {str(t).lower() for t in (_catalog_rules('codex').get('workhorse_families') or ())}
    preferred_workhorse = [item for item in engineering if workhorse_tags and (_tokens_lower(item['slug']) & workhorse_tags)]
    workhorse = preferred_workhorse or engineering or non_lightweight
    if not workhorse:
        return {}, 'no_native_catalog_candidate'
    fitting = [item for item in workhorse if estimated_tokens <= budget(item)]
    if not fitting and kind in ('audit', 'code', 'gate'):
        return max(workhorse, key=budget), 'largest_engineering_budget'
    return (fitting or workhorse)[0], 'frontier_catalog'


def _per_backend_role_target(prefix, raw_model):
    token = str(raw_model or '').strip().lower()
    if token.startswith('@'):
        role = token[1:]
        return role if role in ('orchestrator', 'math', 'code', 'review', 'fast') else ''
    if prefix in ('codex', 'dsh', 'agy', 'claude') and token in ('math', 'code', 'review'):
        return token
    if prefix in ('dsh', 'claude') and token == 'fast':
        return token
    return ''


def resolve_execution_profile(backends, spec, tier='max', prompt_text='', kind='review', estimated_tokens=0):
    prefix, explicit_model = _engine_parts(spec)
    info = backends.get(prefix, {})
    models = info.get('models', [])
    role_target = _per_backend_role_target(prefix, explicit_model)
    if role_target:
        explicit_model = ''
        if role_target == 'fast':
            if tier in ('max', 'auto', ''):
                tier = 'low'
            kind = 'review'
            prompt_text = ''
        elif role_target == 'orchestrator':
            kind = 'review'
        else:
            kind = role_target
    hint = _extract_prompt_model_hint(prompt_text) if not explicit_model and not role_target and models else ''
    if prefix in ('claude', 'dsh'):
        claude_rules = _catalog_rules('claude')
        dsh_rules = _catalog_rules('dsh')
        claude_aliases = {str(k).lower(): str(v) for k, v in (claude_rules.get('alias_map') or {}).items()}
        dsh_aliases = {str(k).lower(): str(v) for k, v in (dsh_rules.get('alias_map') or {}).items()}
        dsh_default = str(dsh_rules.get('default_model') or (models[0]['slug'] if models else 'default'))
        dsh_fast = str(dsh_rules.get('fast_model') or (models[-1]['slug'] if models else dsh_default))
        query = explicit_model or hint
        q_low = query.lower()
        record = _exact_model_record(models, query) if query else {}
        if prefix == 'dsh' and q_low in dsh_aliases:
            record = _exact_model_record(models, dsh_aliases[q_low])
        elif prefix == 'claude' and q_low in claude_aliases:
            target_prefix = claude_aliases[q_low].split('[', 1)[0].split('-', 1)[0].lower()
            record = _exact_model_record(models, claude_aliases[q_low]) or next(
                (m for m in models if m['slug'].lower().startswith((target_prefix, q_low))), {}
            )
        if query and not record:
            terms = _alias_tokens(query) - NON_AI_MODEL_STOPWORDS
            matches = [item for item in models if terms and terms <= _alias_tokens(
                f"{item['slug']} {item.get('display_name', '')}")]
            record = _unique_model_matches(query, matches)
        source = ('role_specialty_' + role_target if role_target else
                  'prompt_fuzzy_model' if hint and record else
                  'explicit_engine_model' if explicit_model else 'configured_model')
        slug = record.get('slug') or explicit_model
        if prefix == 'claude' and not record and info.get('llm_source') == 'local_router' and q_low in claude_aliases:
            slug = claude_aliases[q_low]
        if not slug:
            light_cues = tuple({'轻量', '极速'} | _fast_model_tags())
            forbid_lightweight = _has_negated_cue(prompt_text, light_cues)
            use_fast_slot = (role_target == 'fast') or (not role_target and tier in ('low', 'minimal') and not forbid_lightweight)
            fallback_claude = str(claude_rules.get('default_fallback_alias') or 'default')
            if prefix == 'dsh':
                slug = dsh_fast if use_fast_slot else dsh_default
            elif role_target == 'fast':
                router_sub = str(claude_rules.get('router_subagent_model') or '')
                fast_configured = next((m['slug'] for m in models if _tokens_lower(m['slug']) & _fast_model_tags()), '')
                slug = (router_sub if info.get('llm_source') == 'local_router' and router_sub
                        else fast_configured or info.get('model') or fallback_claude)
            else:
                slug = info.get('model') or fallback_claude
            record = _exact_model_record(models, slug)
            if prefix == 'dsh' and not role_target:
                source = 'specialty_fast_lightweight' if use_fast_slot else 'builtin_flagship'
        if prefix == 'dsh' and slug.lower() in dsh_aliases:
            slug = dsh_aliases[slug.lower()]
            record = _exact_model_record(models, slug)
        effort = ({'ultra': 'max', 'max': 'max', 'xhigh': 'max', 'high': 'high',
                   'medium': 'low', 'low': 'low', 'minimal': 'low', 'off': 'off', 'none': 'off'}[tier]
                  if prefix == 'dsh' else 'max' if tier == 'ultra' else 'low' if tier == 'minimal' else tier)
        return _profile(prefix, info, spec, slug, record, tier, source, effort, effort,
                        'catalog_declared' if record.get('supported_efforts') else 'unverified_request')
    if prefix == 'codex':
        if explicit_model or hint:
            try:
                record = _resolve_explicit_codex_record(models, explicit_model or hint)
            except HarnessError:
                if explicit_model:
                    raise
                record, hint = {}, ''
            if record or explicit_model:
                slug = record.get('slug') or explicit_model
                source = ('prompt_fuzzy_model' if hint else 'explicit_engine_model' if slug.lower() == explicit_model.lower()
                          else 'explicit_fuzzy_model')
                return _codex_profile(info, spec, record, slug, tier, source)
        record, source = _select_codex_specialty_record(
            info, kind, tier, prompt_text, estimated_tokens,
            prefer_fast=(role_target == 'fast'), allow_tier_fast=(not role_target),
        )
        if record:
            return _codex_profile(info, spec, record, record['slug'], tier,
                                  'role_specialty_' + role_target if role_target else source)
    else:
        if explicit_model or hint:
            try:
                record, source = _resolve_explicit_agy_record(models, explicit_model or hint, tier)
            except HarnessError:
                if explicit_model:
                    raise
                record, source, hint = {}, '', ''
            if record or explicit_model:
                return _agy_profile(info, spec, record, record.get('slug') or explicit_model, tier,
                                    'prompt_fuzzy_model' if hint else source if record else 'explicit_engine_model')
        agy_blocked = {str(t).lower() for t in (_catalog_rules('agy').get('blocked_auto_tokens') or ())}
        retiring_cfg = (_fleet_profile().get('excluded_or_retiring') or {})
        retiring_res = []
        if isinstance(retiring_cfg, dict):
            for pat in retiring_cfg.get('agy_retiring_patterns') or ():
                if isinstance(pat, str) and pat.strip():
                    try:
                        retiring_res.append(re.compile(pat, re.I))
                    except re.error:
                        continue
        native = [
            item for item in models
            if _model_category('agy', item) == 'native'
            and not (_tokens_lower(f"{item['slug']} {item.get('display_name', '')}") & agy_blocked)
            and not any(rx.search(f"{item['slug']} {item.get('display_name', '')}") for rx in retiring_res)
        ]
        families = _agy_families(native)
        eligible = sorted((name for name in families if _match_agy_family(families[name], tier)), key=_agy_family_order)
        if eligible:
            agy_fast_tags = _fast_model_tags('agy')
            is_fast = lambda name: bool(_tokens_lower(name) & agy_fast_tags)
            normal = [name for name in eligible if not is_fast(name)]
            light_cues = tuple({'轻量', '极速'} | _fast_model_tags())
            forbid_lightweight = _has_negated_cue(prompt_text, light_cues)
            if not normal and role_target != 'fast' and (bool(role_target) or tier != 'low' or forbid_lightweight):
                return _profile(prefix, info, spec, None, {}, tier, 'no_native_catalog_candidate',
                                error='未找到原生旗舰模型，请刷新目录或显式指定模型。')
            normal = normal or eligible
            chosen, source = normal[0], 'frontier_catalog'
            if role_target == 'fast' or (not role_target and tier == 'low' and not forbid_lightweight):
                fast = [name for name in eligible if is_fast(name)]
                if fast:
                    chosen, source = fast[0], 'specialty_fast_lightweight'
            elif kind in ('math', 'direction') or (kind == 'review' and tier != 'low'
                                                  and _has_positive_cue(prompt_text, HIGH_COMPLEXITY_CUES)):
                top_gen = _agy_family_order(normal[0])[0]
                top_normal = [name for name in normal if _agy_family_order(name)[0] == top_gen]
                chosen = max(top_normal, key=lambda name: EFFORT_LADDER.index(
                    _agy_slug_tier(_match_agy_family(families[name], tier)['slug'])[1]))
                source = 'specialty_deep_reasoning'
            record = _match_agy_family(families[chosen], tier)
            return _agy_profile(info, spec, record, record['slug'], tier,
                                'role_specialty_' + role_target if role_target else source)
        source = 'no_non_delegating_effort' if families else 'no_native_catalog_candidate'
    return _profile(prefix, info, spec, None, {}, tier, source, effort_source=source,
                    error='未找到符合自动选路规则的原生目录模型，请刷新目录或显式指定后端模型。')


def _catalog_metadata(path, data, models, source, now):
    max_age = _catalog_max_age_seconds()
    exists = path.is_file() if path else False
    fetched_at = data.get('fetched_at') if isinstance(data, dict) else None
    stamp = None
    if isinstance(fetched_at, str):
        try:
            stamp = datetime.datetime.fromisoformat(fetched_at)
            stamp = stamp.astimezone(datetime.timezone.utc) if stamp.tzinfo is not None else None
        except (ValueError, OverflowError):
            pass
    age = (now - stamp).total_seconds() if stamp else None
    if not models:
        freshness = 'empty_or_unreadable' if exists or path is None else 'missing'
    elif age is None:
        freshness = 'unknown'
    elif age < 0:
        freshness = 'future_timestamp'
    else:
        freshness = 'stale' if age >= max_age else 'within_assessment_window'
    return {'source': source, 'path': str(path) if path else None, 'cache_file_exists': exists,
            'fetched_at': stamp.isoformat() if stamp else None,
            'timestamp_source': ('cli_query_time_utc' if path is None else 'cache_fetched_at_field') if stamp else None,
            'age_seconds': int(age) if age is not None and age >= 0 else None,
            'freshness': freshness, 'assessment_window_seconds': max_age,
            'model_count': len(models)}


def _read_codex_catalog(home, now):
    path = Path(home) / 'models_cache.json'
    cache = _read_json(path)
    models = _model_records(cache)
    catalog = _catalog_metadata(path, cache, models, 'codex_client_cache', now)
    cache_version = cache.get('client_version') if isinstance(cache, dict) else None
    if isinstance(cache_version, str):
        catalog['client_version_at_collection'] = cache_version
    return models, catalog


def _current_catalog(info):
    catalog = info.get('model_catalog') or {}
    return bool(info.get('models') and catalog.get('model_count') and catalog.get('fetched_at')
                and catalog.get('freshness') == 'within_assessment_window')


def _refresh_codex_listing(info, batch):
    _enforce_preflight_quota({'codex': info}, ('codex:default',), batch=batch)
    try:
        listing = _query_codex_listing(info)
    except HarnessError as exc:
        detail = str(exc.details.get('stderr') or exc)
        if _is_quota_exhausted(detail):
            cooldown = _record_quota_exhausted('codex', info, detail, batch=batch)
            raise HarnessError('Codex 模型目录查询遇到额度限制，停止派工', 'quota_exhausted',
                               preflight_blocked=False, execution_environment=_execution_environment(info),
                               **_quota_user_choices('codex', cooldown)) from exc
        raise
    fetched = datetime.datetime.now(datetime.timezone.utc)
    models, catalog = _read_codex_catalog(info['config_home'], fetched)
    if catalog['freshness'] != 'within_assessment_window':
        raise HarnessError('Codex 模型列表已返回，但同侧客户端缓存缺少有效的采集时间或模型记录，停止派工。',
                           model_catalog=catalog, execution_environment=_execution_environment(info))
    cached_by_slug = {record['slug']: record for record in models}
    listed_models = []
    for slug, row in listing.items():
        cached = cached_by_slug.get(slug, {})
        # The app-server list does not expose context windows. Join these
        # fields only from the same-side cache and on the exact model slug.
        available = {'slug': slug, 'visibility': 'list'}
        for upstream, field in (('displayName', 'display_name'), ('description', 'description'),
                                ('defaultReasoningEffort', 'default_reasoning_level'),
                                ('supportedReasoningEfforts', 'supported_reasoning_levels'),
                                ('modelSpecialty', 'model_specialty')):
            if row.get(upstream) is not None:
                available[field] = row[upstream]
        normalized = _model_records({'models': [available]})[0]
        listed_models.append({**cached, **normalized, 'context_cache_slug_matched': bool(cached)})
    info.update(models=listed_models, model_catalog=catalog,
                model_listing={'source': 'codex_app_server_model_list', 'fetched_at': fetched.isoformat(),
                               'model_count': len(listed_models),
                               'context_fields': 'codex_client_cache_exact_slug_only'},
                verified_status='model_listing_only', capability_source='model_list_and_same_side_cache')


def _agy_config(root):
    cli_path = root / 'antigravity-cli' / 'settings.json'
    app_path = root / 'config' / 'config.json'
    settings, config = _read_json(cli_path), _read_json(app_path)
    user_settings = config.get('userSettings') if isinstance(config, dict) else None
    cli_model = settings.get('model') if isinstance(settings, dict) else None
    app_model = user_settings.get('model') if isinstance(user_settings, dict) else None
    return {'model': cli_model or app_model, 'configured_model_source': str(cli_path if cli_model else app_path) if cli_model or app_model else None,
            'config_files': {'cli': {'path': str(cli_path), 'exists': cli_path.is_file()},
                             'desktop': {'path': str(app_path), 'exists': app_path.is_file()}},
            'config_home': str(root)}


def _agy_cached_records(data):
    models = data.get('models') if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    return [item for item in models if isinstance(item, dict) and isinstance(item.get('slug'), str)
            and item['slug'].strip()]


def _agy_info(binary, requested, now=None):
    root, interop = _agy_home(binary)
    cache_file = root / 'antigravity-cli' / 'cache' / 'cae_models_cache.json'
    data = _read_json(cache_file)
    models = _agy_cached_records(data)
    fallback_win = _positive_int(_headless_tunables().get('unlisted_window_fallback_tokens')) or 120_000
    return {'available': bool(binary), 'detected': bool(binary), 'binary': binary, 'family': 'antigravity',
            **_agy_config(root), 'models': models, 'windows_interop': interop,
            'verified_status': 'unverified', 'authentication_status': 'not_checked',
            'model_catalog': _catalog_metadata(cache_file, data, models, 'agy_harness_cache',
                                               now or datetime.datetime.now(datetime.timezone.utc)),
            'execution_config_supported': not os.environ.get('CAE_AGY_HOME'),
            'configured_window_tokens': None, 'effective_compaction_tokens': None,
            'requested_input_budget_tokens': requested, 'safe_input_budget_tokens': requested or fallback_win,
            'budget_source': 'explicit_planning_budget' if requested else f'policy_estimate_{fallback_win}',
            'capability_source': 'local_settings_and_cache' if models else 'local_settings',
            'capacity_notice': '宿主压缩阈值未核实，规划预算不表示无压缩容量。'}


def _can_fallback_agy(info):
    return _is_posix() and not info.get('windows_interop') and not os.environ.get('CAE_AGY_BINARY')


def _fallback_agy_info(info):
    if not _can_fallback_agy(info):
        return None
    binary = _find_windows_agy_in_wsl()
    if not binary or binary == info.get('binary'):
        return None
    fallback = _agy_info(binary, info.get('requested_input_budget_tokens'))
    fallback['fallback_from'] = _execution_environment(info)
    fallback['fallback_reason'] = 'native_authentication_required'
    return fallback


def _run_agy_listing(info):
    binary = info['binary']
    command = [binary, 'models']
    query_timeout = _positive_int(_headless_tunables().get('catalog_query_timeout_sec')) or 30
    if not info.get('windows_interop'):
        return run_process(command, Path.cwd(), timeout_sec=query_timeout)
    if not shutil.which('powershell.exe') or not shutil.which('taskkill.exe'):
        raise HarnessError('WSL 跨端查询需要 powershell.exe 与 taskkill.exe 进行进程监督')
    windows_temp = _wsl_path(Path(_windows_env('TEMP')), windows=False)
    with tempfile.TemporaryDirectory(prefix='cae-harness-', dir=windows_temp) as temp:
        path = Path(temp)
        command, pid_file = _interop_command(command, path, path, '', None)
        return run_process(command, path, timeout_sec=query_timeout, native_pid_file=pid_file)


def _refresh_agy_listing(info, batch, *, refresh_model_cache, record_quota=True):
    if not info.get('execution_config_supported', True):
        raise HarnessError('CAE_AGY_HOME 仅用于离线配置探测，执行查询请使用当前 CLI 的真实用户配置')
    if not info.get('binary'):
        raise HarnessError('未发现 agy，无法查询模型')
    first = info
    while True:
        _enforce_preflight_quota({'agy': info}, ('agy:default',), batch=batch,
                                 cleanup_expired=refresh_model_cache)
        result = _run_agy_listing(info)
        detail = ((result.stderr or '') + '\n' + (result.stdout or '')).strip()
        models = _parse_agy_models(result.stdout) if result.returncode == 0 else []
        if not models and _is_quota_exhausted(detail):
            if record_quota:
                cooldown = _record_quota_exhausted('agy', info, detail, batch=batch)
            else:
                cooldown = {'backend': 'agy', 'execution_side': _execution_side(info), 'exhausted': True,
                            **_parse_quota_reset(detail)}
            raise HarnessError('agy models 查询遇到额度限制，停止派工', 'quota_exhausted',
                               preflight_blocked=False, execution_environment=_execution_environment(info),
                               **_quota_user_choices('agy', cooldown))
        if result.returncode == 0:
            if not models:
                raise HarnessError('agy models 未返回可解析的目录，停止派工',
                                   execution_environment=_execution_environment(info))
            break
        if _AGY_AUTH_REQUIRED.search(detail):
            info['authentication_status'] = 'unauthenticated'
            alternate = _fallback_agy_info(info) if info is first else None
            if alternate:
                info = alternate
                continue
        raise HarnessError('agy models 查询失败，停止派工', returncode=result.returncode,
                           stderr=_redact_runtime_secret(detail)[-1000:],
                           execution_environment=_execution_environment(info))
    fetched_at = datetime.datetime.now(datetime.timezone.utc)
    snapshot = {'source': 'agy_models_cli', 'fetched_at': fetched_at.isoformat(), 'models': models}
    if refresh_model_cache:
        cache_file = Path(info['config_home']) / 'antigravity-cli' / 'cache' / 'cae_models_cache.json'
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        except OSError as exc:
            info['model_cache_write_error'] = str(exc)
    info.update(models=models, authentication_status='authenticated', model_discovery_source='agy_models_cli',
                model_list_parsed=True, verified_status='model_listing_only', cached_model_catalog=info.get('model_catalog'),
                capability_source='agy_models_cli',
                model_catalog=_catalog_metadata(None, snapshot, models, 'agy_models_cli', fetched_at))
    return info


def _sanitize_endpoint_url(raw_url):
    text = str(raw_url or '').strip()
    if not text:
        return ''
    if not text.lower().startswith(('http://', 'https://')):
        return '<redacted-url>' if ('@' in text or '?' in text) else text
    try:
        parsed = urlsplit(text)
        host = parsed.hostname or ''
        if not host:
            return '<redacted-url>'
        if ':' in host:
            host = '[' + host + ']'
        port_part = f':{parsed.port}' if parsed.port else ''
        return f'{parsed.scheme}://{host}{port_part}{parsed.path}'
    except ValueError:
        return '<redacted-url>'


def _redact_runtime_secret(text):
    raw = str(text or '')
    if not raw:
        return ''
    raw = re.sub(r'https?://[^\s"<>]+', lambda m: _sanitize_endpoint_url(m.group(0)), raw)
    raw = re.sub(
        r'(?i)\b(DEEPSEEK_API_KEY|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY)\s*([=:])\s*(["\']?)[^\s"\']+\3',
        r'\1\2<redacted>',
        raw,
    )
    raw = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}', 'Bearer <redacted>', raw)
    raw = re.sub(
        r'\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|'
        r'github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,})',
        '<redacted>',
        raw,
    )
    return raw


def _usable_credential(value):
    return (isinstance(value, str) and bool(value.strip()) and not value.strip().upper().startswith('PASTE_')
            and value.strip().lower() not in ('via-router', 'null', '~', '{}', '[]')
            and not value.strip().startswith('<') and '\r' not in value and '\n' not in value)


def _optional_text(path):
    try:
        return path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        return ''


def _credential_scalar(value):
    value = value.strip()
    if value.startswith('#'):
        return ''
    if value.startswith(('"', "'")):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end > 0 else ''
    return re.split(r'\s+#', value, maxsplit=1)[0].strip()


def _claude_info(binary, requested=None):
    root, interop = _claude_home(binary)
    settings_file = root / 'settings.json'
    settings = _read_json(settings_file)
    settings = settings if isinstance(settings, dict) else {}
    router_cfg = _read_json(root / 'router/router.json')
    env = settings.get('env', {})
    env = env if isinstance(env, dict) else {}
    env = {**os.environ, **env}
    tokens = [env[key] for key in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY') if key in env]
    base_url = str(env.get('ANTHROPIC_BASE_URL') or '')
    try:
        local_router = urlsplit(base_url).hostname in ('127.0.0.1', 'localhost', '::1')
    except ValueError:
        local_router = False
    configured = settings.get('model') or env.get('ANTHROPIC_MODEL')
    configured_window = _positive_int(env.get('CLAUDE_CODE_MAX_CONTEXT_TOKENS'))
    compact_window = _positive_int(env.get('CLAUDE_CODE_AUTO_COMPACT_WINDOW'))
    helper = settings.get('apiKeyHelper')
    source = ('local_router' if local_router else 'settings_env' if _usable_credential(env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY'))
              or isinstance(helper, str) and helper.strip() else 'placeholder_token' if tokens
              else 'credentials_file' if (root / '.credentials.json').is_file() else 'unconfigured')
    model_names = [configured, *(env.get(key) for key in ('ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
                    'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL'))]
    tag_1m_window = _positive_int(_catalog_rules('claude').get('auto_compact_window')) or 1_000_000
    models = [{'slug': name, 'display_name': name, 'provider': 'claude_configured', 'priority': i,
               'max_context_window': tag_1m_window if '[1m]' in name.lower() else configured_window,
               'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max']}
              for i, name in enumerate(dict.fromkeys(name for name in model_names if isinstance(name, str) and name.strip()))]
    if not models and source not in ('local_router', 'settings_env', 'placeholder_token'):
        models = [{'slug': alias, 'display_name': alias, 'provider': 'claude_builtin_alias', 'priority': i,
                   'max_context_window': configured_window, 'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max']}
                  for i, alias in enumerate(('opus', 'sonnet', 'haiku'))]
    return {'available': bool(binary), 'detected': bool(binary), 'binary': binary, 'family': 'claude',
            'config_home': str(root), 'windows_interop': interop, 'model': configured, 'models': models,
            'base_url': _sanitize_endpoint_url(base_url), 'router_configured': bool(router_cfg),
            'llm_source': source, 'llm_configured': source in ('local_router', 'settings_env', 'credentials_file'),
            'configured_window_tokens': configured_window, 'effective_compaction_tokens': compact_window,
            'verified_status': 'unverified', 'authentication_status': 'not_checked',
            'requested_input_budget_tokens': requested, 'capability_source': 'local_settings',
            'model_catalog': _catalog_metadata(settings_file, settings, models, 'claude_settings',
                                              datetime.datetime.now(datetime.timezone.utc))}


def _dsh_info(binary, requested=None):
    root, interop = _dsh_home(binary)
    api_key = _usable_credential(os.environ.get('DEEPSEEK_API_KEY'))
    for line in _optional_text(root / '.env').splitlines():
        if line.strip().startswith('#'):
            continue
        key, equal, value = line.partition('=')
        clean_key = re.sub(r'^export\s+', '', key.strip())
        if equal and clean_key == 'DEEPSEEK_API_KEY':
            api_key = api_key or _usable_credential(_credential_scalar(value))
    account = False
    # 仅识别凭据文件中两种官方提供方及其密钥字段，不实现通用 YAML 解析器。
    scopes = []
    for line in _optional_text(root / '.credentials.yaml').splitlines():
        match = re.match(r'''^(\s*)(?:['"]([^'"]+)['"]|([^:#\s][^:]*)):\s*(.*?)\s*$''', line)
        if not match:
            continue
        indent, quoted, plain, value = match.groups()
        key = (quoted or plain).strip()
        while scopes and len(indent) <= scopes[-1][0]:
            scopes.pop()
        path = [name for _indent, name in scopes] + [key]
        value = _credential_scalar(value)
        official = any(name in ('deepseek-official', 'deepseek-official/DEEPSEEK_API_KEY') for name in path) or key == 'DEEPSEEK_API_KEY'
        platform = any(name.startswith('deepseek-account-platform/') for name in path)
        credential_key = key in ('DEEPSEEK_API_KEY', 'deepseek-official/DEEPSEEK_API_KEY', 'token', 'secret', 'access_token', 'api_key', 'value')
        if _usable_credential(value) and (credential_key or key == 'deepseek-official' or key.startswith('deepseek-account-platform/')):
            api_key = api_key or official
            account = account or platform
        if not value:
            scopes.append((len(indent), key))
    source = 'api_key' if api_key else 'deepseek_account' if account else 'unconfigured'
    dsh_rules = _catalog_rules('dsh')
    raw_builtin = dsh_rules.get('builtin_models') or []
    models = [{'slug': item['slug'], 'display_name': item.get('display_name', item['slug']),
               'provider': 'deepseek', 'category': 'native', 'priority': i,
               'context_window': item.get('context_window', 1_048_576),
               'max_context_window': item.get('max_context_window', 1_048_576),
               'supported_efforts': list(item.get('supported_efforts') or ['off', 'low', 'high', 'max'])}
              for i, item in enumerate(raw_builtin) if isinstance(item, dict) and item.get('slug')]
    default_dsh_model = str(dsh_rules.get('default_model') or (models[0]['slug'] if models else 'default'))
    return {'available': bool(binary), 'detected': bool(binary), 'binary': binary, 'family': 'dsh',
            'config_home': str(root), 'windows_interop': interop, 'model': default_dsh_model, 'models': models,
            'llm_source': source, 'dsh_provider': 'deepseek-official' if api_key else 'deepseek-account' if account else None,
            'llm_configured': source != 'unconfigured', 'verified_status': 'unverified', 'authentication_status': 'not_checked',
            'requested_input_budget_tokens': requested, 'capability_source': 'dsh_builtin_models',
            'model_catalog': {'source': 'dsh_builtin_models', 'model_count': len(models), 'freshness': 'builtin_contract'}}


def _run_claude_auth(info):
    command = [info['binary'], 'auth', 'status']
    if not info.get('windows_interop'):
        env = os.environ.copy()
        env['CLAUDE_CONFIG_DIR'] = info['config_home']
        return run_process(command, Path.cwd(), timeout_sec=15, env=env)
    if not shutil.which('powershell.exe') or not shutil.which('taskkill.exe'):
        raise HarnessError('WSL 跨端认证查询需要 powershell.exe 与 taskkill.exe')
    windows_temp = _wsl_path(Path(_windows_env('TEMP')), windows=False)
    with tempfile.TemporaryDirectory(prefix='cae-harness-', dir=windows_temp) as temp:
        path = Path(temp)
        command, pid_file = _interop_command(command, path, path, '', extra_env={
            'CLAUDE_CONFIG_DIR': _wsl_path(Path(info['config_home']))})
        return run_process(command, path, timeout_sec=15, native_pid_file=pid_file)


def _probe_router_health(info):
    health_url = info['base_url'].rstrip('/') + '/__router/health'
    if info.get('windows_interop'):
        if not shutil.which('powershell.exe'):
            return False
        script = (f"try {{ $r = Invoke-RestMethod -Uri {_ps_literal(health_url)} -TimeoutSec 2; "
                  "if ($r.ok -eq $true) { exit 0 } else { exit 1 } } catch { exit 1 }")
        try:
            return run_process(['powershell.exe', '-NoProfile', '-Command', script], Path.cwd(), timeout_sec=5).returncode == 0
        except HarnessError:
            return False
    try:
        with urlopen(health_url, timeout=2.0) as response:
            return response.status == 200 and json.load(response).get('ok') is True
    except (OSError, URLError, ValueError, AttributeError):
        return False


def _verify_claude_router_card_key(info, model_name):
    cfg = _read_json(Path(info['config_home']) / 'router/router.json')
    if not isinstance(cfg, dict) or not isinstance(cfg.get('routes', []), list):
        raise HarnessError('Claude Code 路由配置格式无效，请检查 router/router.json')
    model = (model_name or info.get('model') or '').strip()
    claude_aliases = {str(k).lower(): str(v) for k, v in (_catalog_rules('claude').get('alias_map') or {}).items()}
    if model.lower() in claude_aliases:
        model = claude_aliases[model.lower()]
    card = cfg.get('default_key_from_card')
    for route in cfg.get('routes', []):
        if isinstance(route, dict) and route.get('prefix') and model.lower().startswith(str(route['prefix']).lower()):
            card = route.get('key_from_card')
            break
    if not card:
        return
    db_value = str(cfg.get('ccswitch_db') or '~/.cc-switch/cc-switch.db')
    if info.get('windows_interop') and db_value.startswith('~/'):
        db = Path(info['config_home']).parent / db_value[2:]
    elif info.get('windows_interop') and ntpath.isabs(db_value) and not posixpath.isabs(db_value):
        db = Path(_wsl_path(Path(db_value), windows=False))
    else:
        db = Path(db_value).expanduser()
    try:
        con = sqlite3.connect(db.absolute().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            rows = con.execute("SELECT settings_config FROM providers WHERE app_type='claude' AND name=? LIMIT 2", (card,)).fetchall()
        finally:
            con.close()
        env = json.loads(rows[0][0]).get('env', {}) if len(rows) == 1 else {}
        usable = _usable_credential(env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY'))
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        usable = False
    if not usable:
        raise HarnessError(f'Claude Code 路由卡片 {card} 缺少有效 API 凭据，请先在 CC Switch 配置密钥')


_WSL_FAKE_IP_NETWORKS = (ipaddress.ip_network('198.18.0.0/15'), ipaddress.ip_network('2001:2::/48'))


def _wsl_upstream_hits_unproxied_fake_ip(base_url):
    if not (_is_posix() and os.environ.get('WSL_DISTRO_NAME')):
        return False
    if any(os.environ.get(k) for k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy')):
        return False
    host = (urlsplit(base_url or '').hostname or '').strip()
    if not host or host == 'localhost':
        return False
    try:
        addrs = [ans[4][0] for ans in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
    except OSError:
        return False
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if any(ip in net for net in _WSL_FAKE_IP_NETWORKS):
            return True
    return False


def _verify_claude_llm_ready(info, requested_model='', batch=None):
    source = info.get('llm_source', 'unconfigured')
    if source == 'placeholder_token':
        raise HarnessError('Claude Code 配置中的 API 凭据为空或仍为占位符，请先在 CC Switch 填入有效 API Key 并切换卡片',
                           execution_environment=_execution_environment(info))
    if source == 'local_router':
        if not _probe_router_health(info):
            raise HarnessError('Claude Code 本地路由器未就绪，请先启动路由器并检查 /__router/health')
        _verify_claude_router_card_key(info, requested_model)
    elif source == 'settings_env':
        if not info.get('windows_interop') and _wsl_upstream_hits_unproxied_fake_ip(info.get('base_url', '')):
            router_card = str(_catalog_rules('claude').get('router_card_name') or '本地混合路由卡片')
            wsl_router_port = _positive_int((_headless_tunables().get('router') or {}).get('wsl_port')) or 8241
            raise HarnessError(
                'WSL 检测到 Claude Code 直连上游域名解析为宿主 Fake-IP 且未配置代理环境变量；'
                f'请切换至本地路由卡片（{router_card} / 127.0.0.1:{wsl_router_port}）或配置 HTTPS_PROXY',
                execution_environment=_execution_environment(info),
            )
    else:
        if not info.get('binary'):
            raise HarnessError('未发现 Claude Code CLI，请先安装或指定 CAE_CLAUDE_BINARY')
        first = info
        while True:
            _enforce_preflight_quota({'claude': info}, ('claude:default',), batch=batch)
            result = _run_claude_auth(info)
            try:
                logged_in = result.returncode == 0 and json.loads(result.stdout).get('loggedIn') is True
            except (ValueError, AttributeError):
                logged_in = False
            if logged_in:
                break
            if info is first and _is_posix() and not info.get('windows_interop') and not os.environ.get('CAE_CLAUDE_BINARY'):
                binary = _find_windows_claude_in_wsl()
                if binary and binary != info['binary']:
                    info = _claude_info(binary, info.get('requested_input_budget_tokens'))
                    continue
            raise HarnessError('Claude Code 未登录且未配置有效 API 凭据，请先运行 claude auth login 或在 CC Switch 切换有效卡片',
                               execution_environment=_execution_environment(info))
        if info is not first:
            info.update(fallback_from=_execution_environment(first), fallback_reason='native_authentication_required')
            first.update(info)
        first['authentication_status'] = 'authenticated'
        first['llm_configured'] = True
    info['llm_configured'] = True


def _verify_dsh_llm_ready(info):
    if info.get('llm_configured') is not True or info.get('llm_source') not in ('api_key', 'deepseek_account'):
        raise HarnessError('DeepSeek Harness (dsh) 未检测到已登录的 DeepSeek 账号或有效的 DEEPSEEK_API_KEY，请先在 DSH 登录或在 ~/.dsh/.env 配置密钥',
                           execution_environment=_execution_environment(info))


def probe_backends(codex_input_budget=None, agy_input_budget=None, discover_models=False, *, refresh_model_cache=True,
                   claude_input_budget=None, dsh_input_budget=None):
    """普通探测只读文件，程序发现和缓存均不代表认证或线上容量验证。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    binary = _find_binary('codex')
    home, interop = _codex_home(binary)
    config_file = home / 'config.toml'
    config = _top_level_toml(config_file)
    models, catalog = _read_codex_catalog(home, now)
    interactive_model = config.get('model')
    if interactive_model:
        selected = next((item for item in models if item['slug'] == interactive_model), {})
    else:
        selected = _select_codex_frontier_record(models)
    configured = _positive_int(config.get('model_context_window'))
    cached_default = _positive_int(selected.get('context_window'))
    cached_max = _positive_int(selected.get('max_context_window'))
    inferred = configured or cached_default
    if inferred and cached_max:
        inferred = min(inferred, cached_max)
    compact = _positive_int(config.get('model_auto_compact_token_limit'))
    requested = codex_input_budget or _positive_int(os.environ.get('CAE_CODEX_INPUT_BUDGET'))
    codex = {'verified_status': 'unverified', 'authentication_status': 'not_checked',
        'available': bool(binary), 'detected': bool(binary), 'binary': binary,
        'family': 'codex', 'model': interactive_model, 'reasoning_effort': config.get('model_reasoning_effort'),
        'models': models, 'config_home': str(home),
        'config_files': {'cli': {'path': str(config_file), 'exists': config_file.is_file()}},
        'configured_model_source': str(config_file) if interactive_model else None, 'model_catalog': catalog,
        'windows_interop': interop, 'configured_window_tokens': configured, 'cached_default_window_tokens': cached_default,
        'cached_max_window_tokens': cached_max, 'inferred_window_tokens': inferred, 'effective_compaction_tokens': compact,
        'context_metadata_model': selected.get('slug'),
        'cached_effective_context_percent': selected.get('effective_context_window_percent'),
        'requested_input_budget_tokens': requested, 'capability_source': 'local_config_and_cache',
        'capacity_notice': '配置和缓存供预算估算，实际模型权限、压缩阈值及完整读取仍需执行验证。'}
    requested = agy_input_budget or _positive_int(os.environ.get('CAE_AGY_INPUT_BUDGET'))
    agy = _agy_info(_find_binary('agy'), requested, now)
    if discover_models and agy.get('binary'):
        try:
            agy = _refresh_agy_listing(agy, _QuotaBatch(), refresh_model_cache=refresh_model_cache,
                                       record_quota=refresh_model_cache)
        except HarnessError as exc:
            agy['discovery_error'] = str(exc)
    elif discover_models:
        agy['discovery_error'] = 'agy_not_detected'
    claude = _claude_info(_find_binary('claude'), claude_input_budget or _positive_int(os.environ.get('CAE_CLAUDE_INPUT_BUDGET')))
    dsh = _dsh_info(_find_binary('dsh'), dsh_input_budget or _positive_int(os.environ.get('CAE_DSH_INPUT_BUDGET')))
    result_map = {'codex': codex, 'agy': agy, 'claude': claude, 'dsh': dsh}
    for name, info in result_map.items():
        info['execution_side'] = _execution_side(info)
        prof = resolve_execution_profile(result_map, name + ':default', 'max', kind='code')
        deep_prof = resolve_execution_profile(result_map, name + ':default', 'max', kind='math')
        fast_prof = resolve_execution_profile(result_map, name + ':default', 'low', kind='review')
        info['frontier_model'] = prof['model']
        info['max_reasoning_effort'] = prof['reasoning_effort']
        info['auto_selectable'] = bool(_peer_auto_ready(result_map, name) and not prof['execution_blocked_reason'])
        info['specialty_routing'] = {
            'engineering_workhorse': {'model': prof['model'], 'reasoning_effort': prof['reasoning_effort']},
            'deep_reasoning': {'model': deep_prof['model'], 'reasoning_effort': deep_prof['reasoning_effort']},
            'fast_lightweight': {'model': fast_prof['model'], 'reasoning_effort': fast_prof['reasoning_effort']},
        }
        info['budget_model'] = prof['model']
        info['safe_input_budget_tokens'] = prof['capacity']['input_budget_tokens']
        info['budget_source'] = prof['capacity']['budget_source']
        cooldown = _load_active_quota_cooldown(name, info, cleanup_expired=refresh_model_cache)
        info['quota_status'] = 'exhausted_cached' if cooldown else 'unknown_until_called'
        info['quota_cooldown'] = cooldown
    return result_map


def _capability_matrix(prefix, info):
    models = info['models']
    families = _agy_families(models) if prefix == 'agy' else {}
    rows = []
    for record in sorted(models, key=_catalog_order):
        slug = record['slug']
        category = _model_category(prefix, record)
        if prefix == 'agy':
            family, variant = _agy_slug_tier(slug)
            efforts = sorted(families.get(family, {}), key=EFFORT_LADDER.index) if family else []
        else:
            family, variant = None, None
            efforts = list(record.get('supported_efforts') or [])
        rows.append({
            'model': slug, 'display_name': record.get('display_name'), 'category': category,
            'auto_candidate': category == 'native',
            'family': family or None, 'variant_effort': variant or None,
            'declared_efforts': efforts, 'declared_default_effort': record.get('default_reasoning_level'),
            'effort_source': ('fixed_configuration' if category == 'fixed_composite' else
                              'agy_catalog_slug_suffix' if variant else
                              'dsh_builtin_model_contract' if prefix == 'dsh' and efforts else
                              'codex_client_cache_reasoning_levels' if prefix == 'codex' and efforts else 'unknown'),
            'fixed_configuration': 'same_agent_planning_review_and_execution' if category == 'fixed_composite' else None,
            'capacity': _execution_budget(prefix, info, record),
            'catalog_source': info['model_catalog'].get('source'),
            'catalog_fetched_at': info['model_catalog'].get('fetched_at'),
            'catalog_freshness': info['model_catalog'].get('freshness'),
            'runtime_verified': False,
        })
    return rows


# ════════════════════════════════════════════════════════════════════════════
# ④ 额度冷却持久化、熔断门禁与回退资格
# ════════════════════════════════════════════════════════════════════════════


class _QuotaBatch:
    def __init__(self, retry=False):
        self.retry = retry
        self.initial_cooldown = {}
        self.blocked = {}


def _is_quota_exhausted(text):
    return bool(text and QUOTA_ERROR_RE.search(text))


def _parse_quota_reset(text, now=None):
    now_ts = time.time() if now is None else float(now)
    match = re.search(
        r'(?:try again (?:at|after|in)\s+|resets?(?:\s+(?:at|in))?\s+)([^\r\n]+?)(?:\.(?:\s|$)|$)',
        text or '',
        re.IGNORECASE,
    )
    hint = match.group(1).strip() if match else ''
    cleaned = re.sub(r'\b(\d{1,2})(?:st|nd|rd|th)\b', r'\1', hint, flags=re.IGNORECASE).strip()
    epoch = None
    if cleaned:
        for fmt in ('%b %d, %Y %I:%M %p', '%B %d, %Y %I:%M %p', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                epoch = datetime.datetime.strptime(cleaned, fmt).timestamp()
                break
            except (ValueError, OverflowError, OSError):
                continue
        if epoch is None:
            try:
                epoch = datetime.datetime.fromisoformat(cleaned.replace('Z', '+00:00')).timestamp()
            except (ValueError, OverflowError, OSError):
                rel = re.fullmatch(r'(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s(?:ec)?)?', cleaned, re.IGNORECASE)
                if rel and any(rel.groups()):
                    try:
                        hrs, mins, secs = (int(val or 0) for val in rel.groups())
                        epoch = now_ts + hrs * 3600 + mins * 60 + secs
                    except (ValueError, OverflowError):
                        epoch = None
    hd = _headless_tunables()
    horizon_days = _positive_int(hd.get('quota_max_horizon_days')) or 45
    default_cd = _positive_int(hd.get('quota_default_cooldown_sec')) or 3600
    if epoch is not None and now_ts < epoch <= now_ts + horizon_days * 86400:
        until, source = epoch, 'parsed_reset_time'
    else:
        until, source = now_ts + default_cd, f'default_ttl_{default_cd}s'
    return {'reset_hint': hint or None, 'cooldown_until_epoch': round(until, 3), 'cooldown_source': source}


def _quota_scope(prefix, info):
    home = info.get('config_home')
    side = _execution_side(info)
    scope = str(Path(home).resolve()) if home else str(info.get('binary') or '')
    if side == 'windows':
        scope = ntpath.normpath(scope).replace('\\', '/').casefold()
    return prefix, side, scope


def _quota_state_path(prefix, info, *, legacy=False):
    cfg_home = info.get('config_home')
    if not cfg_home:
        return None
    base = Path(cfg_home)
    if prefix == 'agy':
        base /= 'antigravity-cli'
    filename = 'cae_quota_state.json' if legacy else f'cae_quota_state_{_execution_side(info)}.json'
    return base / 'cache' / filename


def _quota_events_dir(state_path):
    return state_path.with_suffix('.events')


def _quota_ack_name(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest() + '.json'


def _quota_identity(record):
    if not record:
        return None
    return _quota_ack_name(record)


def _publish_quota_record(path, record):
    # Each failure owns a unique path. Publish it atomically so a reader
    # cannot mistake partially written JSON for an absent cooldown.
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name('.' + uuid.uuid4().hex + '.pending')
    try:
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def _read_quota_cooldowns(prefix, info, now=None, *, cleanup_expired=False):
    now_ts = time.time() if now is None else float(now)
    state_path = _quota_state_path(prefix, info)
    if state_path is None:
        return []
    found = []
    events = _quota_events_dir(state_path)
    try:
        entries = list(events.iterdir())
    except OSError:
        entries = []
    for path in entries + [state_path, _quota_state_path(prefix, info, legacy=True)]:
        immutable = path.parent == events
        if immutable and not _QUOTA_EVENT_NAME.fullmatch(path.name):
            continue
        data = _read_json(path)
        if not isinstance(data, dict) or not data.get('exhausted') or data.get('backend', prefix) != prefix:
            continue
        if data.get('execution_side', _execution_side(info)) != _execution_side(info):
            continue
        until = data.get('cooldown_until_epoch')
        if (type(until) not in (int, float) or (type(until) is float and not math.isfinite(until))
                or not now_ts < until):
            if cleanup_expired and immutable:
                try:
                    path.unlink()
                except OSError:
                    pass
            continue
        # 旧版写固定路径。按记录全文的摘要确认成功，只确认本次见过的值，
        # 不删除可能已被另一进程替换为新失败记录的固定路径。
        if not immutable and (events / 'acknowledged' / _quota_ack_name(data)).is_file():
            continue
        found.append((path, data))
    if cleanup_expired:
        try:
            for ack in (events / 'acknowledged').iterdir():
                acknowledged = _read_json(ack)
                until = acknowledged.get('cooldown_until_epoch') if isinstance(acknowledged, dict) else None
                if type(until) in (int, float) and now_ts >= until:
                    ack.unlink(missing_ok=True)
        except OSError:
            pass
    return found


def _load_active_quota_cooldown(prefix, info, now=None, *, cleanup_expired=True):
    with QUOTA_LOCK:
        found = _read_quota_cooldowns(prefix, info, now, cleanup_expired=cleanup_expired)
        return max((data for _path, data in found), key=lambda data: data['cooldown_until_epoch'], default=None)


def _active_quota_record(prefix, info, files=None):
    with QUOTA_LOCK:
        key = _quota_scope(prefix, info)
        in_session = _SESSION_QUOTA_BLOCKED.get(key)
        if in_session and time.time() >= in_session['cooldown_until_epoch']:
            _SESSION_QUOTA_BLOCKED.pop(key, None)
            in_session = None
        if files is None:
            files = _read_quota_cooldowns(prefix, info)
        records = [record for _path, record in files]
        if in_session:
            records.append(in_session)
        return max(records, key=lambda record: record['cooldown_until_epoch'], default=None)


def _record_quota_exhausted(prefix, info, detail, now=None, *, batch=None):
    now_ts = time.time() if now is None else float(now)
    clean_detail = _redact_runtime_secret(re.sub(r'<Objs\b[\s\S]*', '', detail or '').strip())
    key = _quota_scope(prefix, info)
    record = {'backend': prefix, 'execution_side': key[1], 'exhausted': True,
              'event_id': uuid.uuid4().hex, 'detected_at_epoch': round(now_ts, 3),
              **_parse_quota_reset(clean_detail, now_ts), 'detail': clean_detail[-600:]}
    state_path = _quota_state_path(prefix, info)
    with QUOTA_LOCK:
        _SESSION_QUOTA_BLOCKED[key] = record
        if batch is not None:
            batch.blocked[key] = record
        info.update(quota_status='exhausted_cached', quota_cooldown=record)
        if state_path:
            path = _quota_events_dir(state_path) / (record['event_id'] + '.json')
            try:
                _publish_quota_record(path, record)
            except OSError:
                info['quota_cache_write_failed'] = True
    return record


def _mark_quota_success(prefix, info, batch):
    # A successful request already in flight cannot undo a failure that
    # happened in this batch, or a newer persisted failure from another batch.
    if not batch.retry:
        return
    key = _quota_scope(prefix, info)
    with QUOTA_LOCK:
        initial = batch.initial_cooldown.get(key, set())
        in_session = _SESSION_QUOTA_BLOCKED.get(key)
        if key in batch.blocked or (in_session and _quota_identity(in_session) not in initial):
            return
        files = _read_quota_cooldowns(prefix, info, cleanup_expired=True)
        if any(_quota_identity(record) not in initial for _path, record in files):
            return
        _SESSION_QUOTA_BLOCKED.pop(key, None)
        state_path = _quota_state_path(prefix, info)
        for path, record in files:
            events = _quota_events_dir(state_path)
            try:
                if path.parent == events:
                    path.unlink(missing_ok=True)
                else:
                    _publish_quota_record(events / 'acknowledged' / _quota_ack_name(record),
                                          {'cooldown_until_epoch': record['cooldown_until_epoch']})
            except OSError:
                pass
        remaining = _active_quota_record(prefix, info)
        info.update(quota_status='exhausted_cached' if remaining else 'unknown_until_called', quota_cooldown=remaining)


def _quota_user_choices(prefix, cooldown):
    hint = cooldown.get('reset_hint') or '待冷却结束或补充额度后'
    return {
        'quota_backend': prefix, 'quota_execution_side': cooldown.get('execution_side'),
        'quota_cooldown': cooldown, 'quota_fuse_triggered': True, 'user_confirmation_required': True,
        'user_choices': [
            f'1. 先不发起跨端调用：改由当前总控在本侧直接完成（{prefix} 预计恢复参考: {hint}）',
            f'2. 已充值或额度已恢复：经用户确认后附带 --retry-quota 重新发起跨端调用',
        ],
    }


def _enforce_preflight_quota(backends, specs, retry_quota=False, *, batch=None, cleanup_expired=True):
    batch = batch or _QuotaBatch(retry_quota)
    for spec in specs:
        if not spec:
            continue
        prefix, _ = _engine_parts(spec)
        info = backends.get(prefix, {})
        key = _quota_scope(prefix, info)
        with QUOTA_LOCK:
            files = _read_quota_cooldowns(prefix, info, cleanup_expired=cleanup_expired)
            active = _active_quota_record(prefix, info, files)
            if batch.retry and key not in batch.initial_cooldown:
                persisted = {_quota_identity(record) for _path, record in files}
                batch.initial_cooldown[key] = persisted | ({_quota_identity(active)} if active else set())
            allowed = batch.initial_cooldown.get(key, set())
            unacknowledged = [record for _path, record in files if _quota_identity(record) not in allowed]
            if active and _quota_identity(active) not in allowed:
                unacknowledged.append(active)
            cooldown = batch.blocked.get(key) or max(unacknowledged, key=lambda record: record['cooldown_until_epoch'], default=None)
            if cooldown:
                info.update(quota_status='exhausted_cached', quota_cooldown=cooldown)
        if cooldown:
            hint = cooldown.get('reset_hint') or '待额度恢复'
            raise HarnessError(f'{prefix} 处于已知额度冷却期（预计恢复: {hint}），已在发起前拦截',
                               'quota_exhausted', preflight_blocked=True,
                               execution_environment=_execution_environment(info),
                               **_quota_user_choices(prefix, cooldown))


def _local_fallback_engine(caller, engine, backends, prompt_text='', *, clean_room=False, exclude_prefixes=()):
    cur_prefix, _ = _engine_parts(engine)
    excluded = set(_extract_negated_backends(prompt_text)) | {str(p).strip().lower() for p in exclude_prefixes if p}
    if clean_room:
        excluded.add('agy')
    caller_id = resolve_caller(caller)
    local = {'antigravity': 'agy', 'codex': 'codex', 'claude': 'claude', 'dsh': 'dsh'}.get(caller_id)
    fb_map = _fleet_profile().get('peer_fallback_order')
    configured_peers = fb_map.get(caller_id, []) if isinstance(fb_map, dict) else []
    for candidate in dict.fromkeys((local, *configured_peers, 'agy', 'codex', 'claude', 'dsh')):
        if (candidate and candidate != cur_prefix and candidate not in excluded
                and _peer_auto_ready(backends, candidate) and not _active_quota_record(candidate, backends[candidate])):
            return candidate + ':default'
    return None


def _quota_fallback_record(from_engine, to_engine, details):
    details = details or {}
    return {
        'from_engine': from_engine,
        'to_engine': to_engine,
        'reason': 'quota_exhausted',
        'preflight_blocked': bool(details.get('preflight_blocked')),
        'quota_cooldown': details.get('quota_cooldown'),
    }


def _quota_can_replay(details, invocation):
    return (bool(details.get('preflight_blocked')) or invocation.get('startup_only') is True
            or not invocation.get('invocation_started', invocation.get('launch_attempted', False)))


# ════════════════════════════════════════════════════════════════════════════
# ⑤ 进程树监督、JSON 流解析、App-Server RPC 与无头执行
# ════════════════════════════════════════════════════════════════════════════


class _WindowsJob:
    """为本次子进程单独创建 Windows Job 对象；以挂起状态创建子进程，先绑定至 Job 再恢复执行，防止派生子进程逃逸。"""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                        ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                        ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ('ReadOperationCount', ctypes.c_ulonglong),
                ('WriteOperationCount', ctypes.c_ulonglong),
                ('OtherOperationCount', ctypes.c_ulonglong),
                ('ReadTransferCount', ctypes.c_ulonglong),
                ('WriteTransferCount', ctypes.c_ulonglong),
                ('OtherTransferCount', ctypes.c_ulonglong),
            ]

        class BasicLimitInfo(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class ExtendedLimitInfo(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', BasicLimitInfo),
                ('IoInfo', IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        self.ctypes = ctypes
        self.entry_type = ThreadEntry
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = (
            ('CreateJobObjectW', wintypes.HANDLE, (ctypes.c_void_p, wintypes.LPCWSTR)),
            ('SetInformationJobObject', wintypes.BOOL, (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)),
            ('AssignProcessToJobObject', wintypes.BOOL, (wintypes.HANDLE, wintypes.HANDLE)),
            ('TerminateJobObject', wintypes.BOOL, (wintypes.HANDLE, wintypes.UINT)),
            ('CloseHandle', wintypes.BOOL, (wintypes.HANDLE,)),
            ('CreateToolhelp32Snapshot', wintypes.HANDLE, (wintypes.DWORD, wintypes.DWORD)),
            ('Thread32First', wintypes.BOOL, (wintypes.HANDLE, ctypes.POINTER(ThreadEntry))),
            ('Thread32Next', wintypes.BOOL, (wintypes.HANDLE, ctypes.POINTER(ThreadEntry))),
            ('OpenThread', wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)),
            ('ResumeThread', wintypes.DWORD, (wintypes.HANDLE,)),
        )
        for name, result, arguments in signatures:
            function = getattr(self.api, name)
            function.restype, function.argtypes = result, arguments
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        ext_info = ExtendedLimitInfo()
        ext_info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(ext_info), ctypes.sizeof(ext_info))

    def attach(self, proc):
        # Popen 保留的句柄具有创建时的权限，受限令牌重新 OpenProcess 可能被拒绝。
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        snapshot = self.api.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            entry = self.entry_type()
            entry.dwSize = self.ctypes.sizeof(entry)
            available = self.api.Thread32First(snapshot, self.ctypes.byref(entry))
            while available:
                if entry.th32OwnerProcessID == proc.pid:
                    thread = self.api.OpenThread(0x0002, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise self.ctypes.WinError(self.ctypes.get_last_error())
                    try:
                        if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise self.ctypes.WinError(self.ctypes.get_last_error())
                    finally:
                        self.api.CloseHandle(thread)
                    return
                available = self.api.Thread32Next(snapshot, self.ctypes.byref(entry))
            raise OSError('未找到挂起子进程的主线程')
        finally:
            self.api.CloseHandle(snapshot)

    def terminate(self):
        if self.handle and not self.api.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _process_options():
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x00000004}
    return {'start_new_session': True}


def _note_cleanup_errors(failure, errors):
    if isinstance(failure, HarnessError):
        failure.details.setdefault('cleanup_errors', []).extend(errors)
    elif hasattr(failure, 'add_note'):
        failure.add_note('资源回收未验证: ' + '; '.join(errors))
    else:
        failure.cleanup_errors = list(errors)


def _close_process_streams(proc):
    errors = []
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except (OSError, ValueError) as exc:
                errors.append('Pipe close failed: ' + str(exc))
    if os.name == 'nt' and proc.returncode is not None:
        proc._handle.Close()
    return errors


def _start_process(command, cwd=None, env=None, shell=False):
    job = _WindowsJob() if os.name == 'nt' else None
    proc = None
    try:
        proc = subprocess.Popen(command, cwd=str(cwd) if cwd is not None else None, env=env, shell=shell,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', **_process_options())
        if job is not None:
            proc._cae_job = job
            job.attach(proc)
        return proc
    except BaseException as exc:
        # 即使事件输出、入 Job 或恢复线程失败，也不能遗留一个挂起进程。
        cleanup_errors = []
        try:
            if proc is not None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired) as cleanup_error:
                    cleanup_errors.append(str(cleanup_error))
                finally:
                    cleanup_errors.extend(_close_process_streams(proc))
        finally:
            if job is not None:
                job.close()
        if cleanup_errors:
            if isinstance(exc, Exception):
                raise HarnessError('子进程启动失败且资源回收未完成', stderr=str(exc), cleanup_errors=cleanup_errors) from exc
            _note_cleanup_errors(exc, cleanup_errors)
        raise


def _terminate_tree(proc, native_pid_file=None):
    errors = []
    if native_pid_file and proc.poll() is None and not getattr(proc, '_cae_native_tree_terminated', False):
        deadline = time.monotonic() + 2
        while not native_pid_file.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            native_pid = int(native_pid_file.read_text(encoding='utf-8-sig').strip())
            if native_pid <= 0:
                raise ValueError('native PID invalid')
            cleanup = subprocess.run(['taskkill.exe', '/T', '/F', '/PID', str(native_pid)], capture_output=True, timeout=10)
            if cleanup.returncode:
                errors.append('Windows process tree termination returned nonzero')
            else:
                proc._cae_native_tree_terminated = True
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            errors.append('Windows process tree cleanup unverified: ' + str(exc))
    try:
        if getattr(proc, '_cae_job', None) is not None:
            proc._cae_job.terminate()
        elif os.name == 'nt':
            cleanup = subprocess.run(['taskkill.exe', '/T', '/F', '/PID', str(proc.pid)], capture_output=True,
                                     timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if cleanup.returncode and proc.poll() is None:
                errors.append('Local process tree termination returned nonzero')
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append('Local process tree cleanup unverified: ' + str(exc))
    return errors


def _reap_process(proc, native_pid_file=None):
    """先清本次进程树，再 wait 回收直接子进程；最后一次 kill 后也必须 wait。"""
    errors = _terminate_tree(proc, native_pid_file)
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append('Child process reaping unverified: ' + str(exc))
    finally:
        job = getattr(proc, '_cae_job', None)
        if job is not None:
            job.close()
    return errors


def run_process(command, cwd, input_text=None, timeout_sec=600, env=None, shell=False, native_pid_file=None):
    if _CANCELLED.is_set():
        raise HarnessError('任务已取消', 'cancelled')
    deadline = time.monotonic() + timeout_sec
    proc = _start_process(command, cwd, env, shell)
    completed = queue.Queue(maxsize=1)
    failure = None

    def communicate():
        # Windows 的 communicate(timeout=...) 仍会同步写 stdin；由独立线程承受管道背压。
        try:
            completed.put((proc.communicate(input=input_text), None))
        except (OSError, ValueError) as exc:
            completed.put((None, exc))

    communicator = threading.Thread(target=communicate, name='cae-process-io', daemon=True)
    try:
        emit_event('process_started', pid=proc.pid)
        communicator.start()
        while True:
            if _CANCELLED.is_set():
                raise HarnessError('任务已取消', 'cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessError('子进程超时', 'timeout', timeout_sec=timeout_sec)
            try:
                output, communication_error = completed.get(timeout=min(remaining, 0.05))
                if communication_error is not None:
                    raise communication_error
                out, err = output
                break
            except queue.Empty:
                if proc.poll() is not None:
                    # 父进程退出不等于管道 EOF，后代可能仍持有写端。
                    cleanup_errors = _terminate_tree(proc, native_pid_file)
                    if cleanup_errors:
                        raise HarnessError('父进程退出后无法核实后代清理', cleanup_errors=cleanup_errors)
        if _CANCELLED.is_set():
            raise HarnessError('任务已取消', 'cancelled')
        emit_event('process_finished', pid=proc.pid, returncode=proc.returncode)
        return subprocess.CompletedProcess(command, proc.returncode, out, err)
    except BaseException as exc:
        failure = exc
        if isinstance(exc, HarnessError):
            exc.details['process_started'] = True
        raise
    finally:
        cleanup_errors = _reap_process(proc, native_pid_file)
        if communicator.ident is not None:
            communicator.join(timeout=5)
        if communicator.is_alive():
            cleanup_errors.append('Child pipe reader/writer did not stop')
        else:
            cleanup_errors.extend(_close_process_streams(proc))
        if cleanup_errors:
            if failure is not None:
                if isinstance(failure, Exception) and not isinstance(failure, HarnessError):
                    raise HarnessError(str(failure), cleanup_errors=cleanup_errors) from failure
                _note_cleanup_errors(failure, cleanup_errors)
            elif failure is None:
                raise HarnessError('子进程资源回收未完成', cleanup_errors=cleanup_errors)


def _cancel_signal(_signum, _frame):
    _CANCELLED.set()


def _validate_schema(value, schema, location='result'):
    expected = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list), 'string': isinstance(value, str),
             'integer': isinstance(value, int) and not isinstance(value, bool)}
    if expected and not valid.get(expected, False):
        raise OutputParseError(f'{location} 类型不符合 {expected}')
    if 'enum' in schema and value not in schema['enum']:
        raise OutputParseError(f'{location} 取值不合法')
    if expected == 'integer' and value < schema.get('minimum', value):
        raise OutputParseError(f'{location} 超出下界')
    if expected == 'object':
        properties = schema.get('properties', {})
        if any(key not in value for key in schema.get('required', [])):
            raise OutputParseError(f'{location} 缺少必需字段')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise OutputParseError(f'{location} 含未定义字段')
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key], location + '.' + key)
    if expected == 'array':
        for index, item in enumerate(value):
            _validate_schema(item, schema['items'], f'{location}[{index}]')


def _is_agy_print_timeout_text(text):
    sample = str(text or '')
    low = sample.lower()
    if 'print timeout after' in low:
        return True
    if re.search(r'"(?:finished_reason|reason)"\s*:\s*"print_timeout"', sample):
        return True
    return False


def _parse_agy_error_line(line):
    stripped = line.strip()
    if not stripped.startswith('AGY_ERROR:'):
        return None
    body = stripped[len('AGY_ERROR:'):].strip()
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, RecursionError):
        return None


def _check_output_status(parsed):
    """只解释协议错误，不把工具读出的额度说明或中间工具调用异常当作后端终止错误。"""
    if parsed.get('event', parsed.get('type')) in ('tool_call', 'tool_result'):
        return
    if parsed.get('type') == 'final' and not (isinstance(parsed.get('reason'), dict) and parsed['reason'].get('kind') == 'completed'):
        raise HarnessError('后端返回未完成状态', 'failed', reason=parsed.get('reason'))
    finished_reason = str(parsed.get('finished_reason', '')).lower()
    if finished_reason == 'print_timeout':
        raise HarnessError('后端输出因超时截断', 'timeout', finished_reason=finished_reason)
    if finished_reason in ('interrupted', 'cancelled', 'canceled'):
        raise HarnessError('后端执行被中断', 'cancelled', finished_reason=finished_reason)
    status = str(parsed.get('status', '')).lower()
    if status == 'timeout':
        raise HarnessError('后端返回超时回执', 'timeout', backend_status=status)
    if status in ('cancelled', 'canceled', 'interrupted'):
        raise HarnessError('后端返回取消回执', 'cancelled', backend_status=status)
    event_error = parsed.get('event', parsed.get('type')) == 'error'
    if (event_error or finished_reason == 'error' or parsed.get('is_error') or parsed.get('error')
            or status in ('error', 'failed', 'partial', 'incomplete', 'invalid', 'waiting', 'running')):
        result_err = parsed.get('result') if isinstance(parsed.get('result'), str) else ''
        error = str(parsed.get('error') or parsed.get('message') or result_err or '')[-2000:]
        error_status = 'quota_exhausted' if _is_quota_exhausted(error) else 'failed'
        raise HarnessError('后端返回错误回执', error_status, backend_status=status or 'error',
                           finished_reason=finished_reason, error=error)


DSML_TAIL_RE = re.compile(r'(?:\s*</?[｜|]+DSML[｜|]+[^>]*>)+\s*$')


def _strip_dsml_tail(text):
    return DSML_TAIL_RE.sub('', text).strip()


_HEX4_CHARS = frozenset('0123456789abcdefABCDEF')
_VALID_JSON_ESCAPES = frozenset('"\\/bfnrt')


def _repair_json_string_escapes(text):
    """在标准 json.loads 失败后，仅修复双引号字符串字面量内部的非法单反斜杠转义（如 Windows 路径与 LaTeX 命令）。"""
    if '\\' not in text:
        return text
    out = []
    in_string = False
    idx = 0
    length = len(text)
    while idx < length:
        ch = text[idx]
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            idx += 1
            continue
        if ch == '"':
            in_string = False
            out.append(ch)
            idx += 1
            continue
        if ch != '\\':
            out.append(ch)
            idx += 1
            continue
        if idx + 1 >= length:
            out.append('\\\\')
            idx += 1
            continue
        nxt = text[idx + 1]
        if nxt in _VALID_JSON_ESCAPES:
            out.append('\\')
            out.append(nxt)
            idx += 2
            continue
        if nxt == 'u' and idx + 6 <= length and all(c in _HEX4_CHARS for c in text[idx + 2:idx + 6]):
            out.append(text[idx:idx + 6])
            idx += 6
            continue
        out.append('\\\\')
        idx += 1
    return ''.join(out)


def _loads_relaxed_dict(candidate):
    for attempt in (candidate, _repair_json_string_escapes(candidate)):
        try:
            parsed = json.loads(attempt, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, RecursionError):
            continue
    return None


def _decode_output_json(raw_text):
    raw = _strip_dsml_tail(raw_text.strip().lstrip('\ufeff'))
    if not raw:
        raise OutputParseError('子任务输出为空')
    try:
        return json.loads(raw)
    except RecursionError as exc:
        raise OutputParseError('JSON 嵌套过深') from exc
    except ValueError as exc:
        relaxed_top = _loads_relaxed_dict(raw)
        if relaxed_top is not None:
            return relaxed_top

        def _raise_if_stream_error_lines(text):
            err_items = []
            saw_timeout = False
            for raw_line in text.splitlines():
                stripped_line = _strip_dsml_tail(raw_line.strip())
                if not stripped_line:
                    continue
                parsed_err = _parse_agy_error_line(stripped_line)
                if parsed_err is not None:
                    err_items.append(parsed_err)
                    continue
                if re.match(r'^(?:\[agy\]\s+print timeout after\b|error:\s+print timeout after\b)', stripped_line, re.I):
                    saw_timeout = True
            for item in err_items:
                reason = str(item.get('reason') or '').lower()
                short_err = str(item.get('short_error') or item.get('message') or '')
                if reason == 'print_timeout' or 'print timeout after' in short_err.lower():
                    raise HarnessError('后端输出因超时截断', 'timeout', finished_reason='print_timeout')
                if _is_quota_exhausted(short_err):
                    raise HarnessError('后端返回错误回执', 'quota_exhausted', error=short_err[-2000:])
            if saw_timeout:
                raise HarnessError('后端输出因超时截断', 'timeout', finished_reason='print_timeout')
            if err_items:
                short_err = str(err_items[-1].get('short_error') or err_items[-1])[-2000:]
                raise HarnessError('后端返回错误回执', 'failed', error=short_err)

        _raise_if_stream_error_lines(raw)
        fence = re.fullmatch(r'```(?:json)?\s*(\{[\s\S]*\})\s*```', raw)
        if fence:
            fenced_dict = _loads_relaxed_dict(_strip_dsml_tail(fence.group(1)))
            if fenced_dict is not None:
                return fenced_dict
        try:
            events = []
            for line in raw.splitlines():
                stripped = _strip_dsml_tail(line.strip())
                if not stripped or stripped.startswith('[llm-'):
                    continue
                events.append(json.loads(stripped))
        except (ValueError, RecursionError) as decode_error:
            # 兼容 CLI wrapper（如 Windows .CMD / 插件提示行）混入少量非 JSON 诊断行的 JSONL 事件流
            tolerant_events = []
            for line in raw.splitlines():
                stripped = _strip_dsml_tail(line.strip())
                if not stripped or not (stripped.startswith('{') and stripped.endswith('}')):
                    continue
                try:
                    ev_obj = json.loads(stripped)
                except (ValueError, RecursionError):
                    continue
                if isinstance(ev_obj, dict):
                    _check_output_status(ev_obj)
                    tolerant_events.append(ev_obj)
            tolerant_results = [
                ev for ev in tolerant_events
                if ev.get('event') == 'result' or ev.get('type') == 'final'
            ]
            if len(tolerant_results) == 1:
                return tolerant_results[0]

            for pattern in (r'```(?:json)?\s*(\{[\s\S]*\})\s*```', r'```(?:json)?\s*(\{[\s\S]*?\})\s*```'):
                fence = re.search(pattern, raw)
                if fence:
                    fenced_dict = _loads_relaxed_dict(_strip_dsml_tail(fence.group(1)))
                    if fenced_dict is not None:
                        return fenced_dict
            decoder = json.JSONDecoder(strict=False)
            for scan_text in (raw, _repair_json_string_escapes(raw)):
                schema_candidates = []
                search_pos = 0
                for _ in range(64):
                    brace_idx = scan_text.find('{', search_pos)
                    if brace_idx < 0:
                        break
                    search_pos = brace_idx + 1
                    try:
                        candidate_obj, end_idx = decoder.raw_decode(scan_text, brace_idx)
                    except (ValueError, RecursionError):
                        continue
                    if isinstance(candidate_obj, dict) and candidate_obj:
                        tail_rest = _strip_dsml_tail(scan_text[end_idx:]).strip()
                        if not tail_rest or re.fullmatch(r'```+', tail_rest):
                            return candidate_obj
                        if 'summary' in candidate_obj and ('content' in candidate_obj or 'findings' in candidate_obj):
                            schema_candidates.append(candidate_obj)
                            search_pos = max(search_pos, end_idx)
                if len(schema_candidates) == 1:
                    return schema_candidates[0]
            raise OutputParseError('子任务输出不是合法 JSON') from decode_error
        results = []
        for event in events:
            if not isinstance(event, dict):
                raise OutputParseError('输出流事件必须为对象') from exc
            _check_output_status(event)
            if event.get('event') == 'result' or event.get('type') == 'final':
                results.append(event)
        if len(results) != 1:
            raise OutputParseError('输出流必须包含一个最终 result 回执') from exc
        return results[0]


def _extract_json_object(raw_text, schema=None):
    schema = schema or AUDIT_JSON_SCHEMA
    parsed = _decode_output_json(raw_text)
    # 字典和字符串包装共用深度上限，字符串解包不能递归重置计数。
    for _ in range(8):
        if not isinstance(parsed, dict):
            raise OutputParseError('最终回执必须为对象')
        _check_output_status(parsed)
        if all(key in parsed for key in schema['required']):
            _validate_schema(parsed, schema)
            if not parsed['summary'].strip():
                raise OutputParseError('summary 为空')
            if 'content' in schema['properties'] and not parsed['content'].strip():
                raise OutputParseError('任务正文 content 为空')
            for finding in parsed.get('findings', []):
                if finding['line_end'] < finding['line_start']:
                    raise OutputParseError('发现项的行号范围倒置')
            return parsed
        next_value = None
        for key in ('structured_output', 'result', 'response', 'output', 'text', 'content'):
            if key in parsed and isinstance(parsed[key], (dict, str)):
                next_value = parsed[key]
                break
        if isinstance(next_value, str):
            next_value = _decode_output_json(next_value)
        if next_value is None:
            break
        parsed = next_value
    raise OutputParseError('回执缺少符合指定结构的结果')


def _read_rpc_lines(stream, inbox, stopped=None):
    def publish(line):
        while stopped is None or not stopped.is_set():
            try:
                inbox.put(line, timeout=0.05)
                return
            except queue.Full:
                continue

    try:
        for line in stream:
            if stopped is not None and stopped.is_set():
                break
            publish(line)
    except (OSError, ValueError):
        pass
    finally:
        publish(None)


def _query_codex_stdio(command, cwd, env, native_pid_file, timeout_sec):
    if _CANCELLED.is_set():
        raise HarnessError('任务已取消', 'cancelled')
    deadline = time.monotonic() + timeout_sec
    try:
        proc = _start_process(command, cwd, env)
    except OSError as exc:
        raise HarnessError('无法启动 Codex 模型目录查询', stderr=str(exc)) from exc
    inbox = queue.Queue(maxsize=128)
    outbox = queue.Queue()
    writer_errors = queue.Queue(maxsize=1)
    stopped = threading.Event()
    errors = collections.deque(maxlen=8)
    stdout_reader = threading.Thread(target=_read_rpc_lines, args=(proc.stdout, inbox, stopped),
                                     name='cae-rpc-stdout', daemon=True)

    def drain_stderr():
        try:
            for line in proc.stderr:
                errors.append(line[-2000:])
        except (OSError, ValueError):
            pass

    stderr_reader = threading.Thread(target=drain_stderr, name='cae-rpc-stderr', daemon=True)

    def write_requests():
        try:
            while True:
                message = outbox.get()
                if message is None:
                    break
                proc.stdin.write(message)
                proc.stdin.flush()
        except (OSError, ValueError) as exc:
            writer_errors.put(exc)
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    stdin_writer = threading.Thread(target=write_requests, name='cae-rpc-stdin', daemon=True)
    failure = None

    def send(message):
        outbox.put(json.dumps(message) + '\n')

    def rpc(number, method, params):
        send({'id': number, 'method': method, 'params': params})
        while True:
            if _CANCELLED.is_set():
                raise HarnessError('任务已取消', 'cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessError('Codex 模型目录查询超时', 'timeout')
            try:
                write_error = writer_errors.get_nowait()
            except queue.Empty:
                pass
            else:
                raise HarnessError('Codex 模型目录通信失败', stderr=str(write_error)) from write_error
            try:
                line = inbox.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue
            if line is None:
                raise HarnessError('Codex app-server 在返回模型目录前退出')
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get('id') != number or 'method' in event:
                continue
            if event.get('error'):
                raise HarnessError(f'Codex {method} 返回错误', stderr=str(event['error'])[-2000:])
            if not isinstance(event.get('result'), dict):
                raise HarnessError(f'Codex {method} 回执结构无效')
            return event['result']

    try:
        stdout_reader.start()
        stderr_reader.start()
        stdin_writer.start()
        rpc(1, 'initialize', {'clientInfo': {'name': 'cae_harness', 'version': '1.0'},
                              'capabilities': {'experimentalApi': True}})
        send({'method': 'initialized'})
        listing = {}
        params = {'includeHidden': False, 'limit': 100}
        cursors = set()
        for number in range(2, 12):
            response = rpc(number, 'model/list', params)
            data = response.get('data')
            if not isinstance(data, list):
                raise HarnessError('Codex 模型列表结构无效')
            for model in data:
                slug = model.get('model') if isinstance(model, dict) else None
                if isinstance(slug, str) and slug and not model.get('hidden'):
                    listing[slug] = model
            cursor = response.get('nextCursor')
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise HarnessError('Codex 模型列表分页游标无效')
            cursors.add(cursor)
            params['cursor'] = cursor
        else:
            raise HarnessError('Codex 模型目录超出查询页数上限')
        if not listing:
            raise HarnessError('Codex 模型目录为空，停止派工')
        return listing
    except HarnessError as exc:
        failure = exc
        raise
    except OSError as exc:
        failure = HarnessError('Codex 模型目录通信失败', stderr=str(exc))
        raise failure from exc
    except BaseException as exc:
        failure = exc
        raise
    finally:
        # 写线程独占 stdin；控制线程不能 close 一个正被阻塞写入的文本管道。
        outbox.put(None)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # 正常退出也回收后代，避免后代继承管道让读取线程永久等 EOF。
        stopped.set()
        cleanup_errors = _reap_process(proc, native_pid_file)
        for reader in (stdout_reader, stderr_reader, stdin_writer):
            if reader.ident is not None:
                reader.join(timeout=5)
            if reader.is_alive():
                cleanup_errors.append(reader.name + ' did not stop')
        if not any(reader.is_alive() for reader in (stdout_reader, stderr_reader, stdin_writer)):
            cleanup_errors.extend(_close_process_streams(proc))
        if isinstance(failure, HarnessError) and errors:
            failure.details['stderr'] = (str(failure.details.get('stderr') or '') + '\n' + ''.join(errors))[-3000:]
        if cleanup_errors:
            if failure is not None:
                if isinstance(failure, Exception) and not isinstance(failure, HarnessError):
                    raise HarnessError(str(failure), cleanup_errors=cleanup_errors) from failure
                _note_cleanup_errors(failure, cleanup_errors)
            elif sys.exc_info()[0] is None:
                raise HarnessError('Codex 模型查询资源回收未完成', cleanup_errors=cleanup_errors)


def _query_codex_listing(info, timeout_sec=30):
    if _CANCELLED.is_set():
        raise HarnessError('任务已取消', 'cancelled')
    home = info.get('config_home')
    binary = info.get('binary')
    if not home or not binary:
        raise HarnessError('未发现 Codex 或其配置目录，无法刷新模型列表')
    command = [binary, 'app-server', '--stdio']
    if info.get('windows_interop'):
        if not shutil.which('powershell.exe') or not shutil.which('taskkill.exe'):
            raise HarnessError('WSL 跨端查询需要 powershell.exe 与 taskkill.exe 进行进程监督')
        windows_temp = _wsl_path(Path(_windows_env('TEMP')), windows=False)
        with tempfile.TemporaryDirectory(prefix='cae-harness-', dir=windows_temp) as temp:
            cwd = Path(temp)
            command, pid_file = _interop_command(command, cwd, cwd, None, home)
            return _query_codex_stdio(command, cwd, None, pid_file, timeout_sec)
    env = os.environ.copy()
    env.update(CODEX_HOME=home, CAE_HARNESS_DEPTH='1')
    return _query_codex_stdio(command, None, env, None, timeout_sec)


def _quota_startup_only(stdout):
    """只有空输出或明确启动/错误事件才证明尚未执行任务；未知事件按已执行处理。"""
    for line in (stdout or '').splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return False
        if not isinstance(event, dict):
            return False
        kind = event.get('event', event.get('type'))
        if kind in ('init', 'session', 'thread.started', 'turn.started', 'turn.failed', 'error'):
            continue
        if kind == 'status' and event.get('phase') in ('turn_start', 'step_start'):
            continue
        if kind == 'final' and not event.get('text'):
            continue
        if kind == 'system' and event.get('subtype') == 'init':
            continue
        if kind == 'result':
            result = event.get('result', event)
            if (isinstance(result, dict) and type(result.get('num_turns')) is int and result['num_turns'] == 0
                    and not any(result.get(key) for key in ('structured_output', 'response', 'output', 'content', 'text'))):
                continue
        return False
    return True


def _backend_diagnostics(result, *, structured_only=False):
    parts = [] if structured_only else [result.stderr or '']

    def error_details(event, depth=0):
        details = {key: event[key] for key in (
            'event', 'type', 'status', 'finished_reason', 'is_error', 'error', 'message',
            'code', 'reason', 'short_error', 'detail', 'details') if key in event}
        is_error = (event.get('is_error') or event.get('error')
                    or str(event.get('status', '')).lower() in (
                        'error', 'failed', 'partial', 'incomplete', 'invalid', 'waiting', 'running',
                        'timeout', 'cancelled', 'canceled', 'interrupted')
                    or str(event.get('finished_reason', '')).lower() in (
                        'error', 'print_timeout', 'interrupted', 'cancelled', 'canceled'))
        nested = event.get('result')
        if isinstance(nested, dict) and depth < 8:
            nested_details, nested_error = error_details(nested, depth + 1)
            if nested_details:
                details['result'] = nested_details
            is_error = is_error or nested_error
        elif isinstance(nested, str) and is_error and nested.strip():
            details['result'] = nested[-2000:]
        return details, is_error

    for line in (result.stdout or '').splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            if not structured_only:
                parts.append(line)
            continue
        if isinstance(event, dict):
            kind = event.get('event', event.get('type'))
            if kind in ('error', 'result', 'turn.failed'):
                details, is_error = error_details(event)
                if kind != 'result' or is_error:
                    parts.append(json.dumps(details, ensure_ascii=False))
    return '\n'.join(part for part in parts if part).strip()


def invoke_engine(spec, target_dir, prompt, backends, schema, readonly=True, timeout_sec=600,
                  agent='readonly-collaborator', stage_files=None, effort=None, retry_quota=False,
                  *, profile=None, batch=None, receipt=None, clean_room=False):
    batch = batch or _QuotaBatch(retry_quota)
    prefix, _ = _engine_parts(spec)
    if clean_room:
        if not readonly:
            raise HarnessError('--clean-room 仅支持只读任务，写入任务必须保留全局安全钩子')
        if prefix == 'agy':
            raise HarnessError('agy 后端不支持剥离全局插件目录，--clean-room 请选用 codex、claude 或 dsh')
    info = backends.get(prefix, {})
    if not info.get('available'):
        raise HarnessError(f'未发现所选后端 {prefix}')
    if profile is None:
        _prepare_execution(backends, (spec,), batch)
        info = backends[prefix]
        profile = resolve_execution_profile(backends, spec, effort or 'max')
    _require_execution_profile(profile)
    _, model = _engine_parts(profile['spec'])
    effort = profile['cli_effort']
    if receipt is None:
        receipt = {}
    receipt.update(requested_engine=profile['requested_engine'], execution_environment=_execution_environment(info),
                   model_requested_to_cli=model or None, actual_model=None, effective_effort=None,
                   declared_effort=profile['declared_effort'], launch_attempted=False, invocation_started=False)
    _enforce_preflight_quota(backends, (spec,), batch=batch)
    binary = info['binary']
    if prefix == 'agy' and not info.get('execution_config_supported', True):
        raise HarnessError('CAE_AGY_HOME 仅用于离线配置探测，执行请使用当前 CLI 的真实用户配置')
    if prefix in ('codex', 'agy', 'claude') and Path(binary).suffix.lower() in ('.cmd', '.bat'):
        raise HarnessError('请通过 CAE_CODEX_BINARY / CAE_AGY_BINARY / CAE_CLAUDE_BINARY 指定原生可执行文件')
    if prefix == 'claude' and info.get('llm_source') == 'local_router':
        _verify_claude_router_card_key(info, model)
    interop = info.get('windows_interop', False)
    temp_parent = None
    if interop:
        if not shutil.which('powershell.exe') or not shutil.which('taskkill.exe'):
            raise HarnessError('WSL 跨端调用需要 powershell.exe 与 taskkill.exe 进行进程监督')
        windows_temp = _windows_env('TEMP')
        temp_parent = _wsl_path(Path(windows_temp), windows=False)
    with tempfile.TemporaryDirectory(prefix='cae-harness-', dir=temp_parent) as temp:
        temp_path = Path(temp)
        schema_file = temp_path / 'schema.json'
        schema_file.write_text(json.dumps(schema, ensure_ascii=False), encoding='utf-8')
        output_file = temp_path / 'result.json'
        env = os.environ.copy()
        env['CAE_HARNESS_DEPTH'] = '1'
        effective_codex_home = info.get('config_home')
        effective_dsh_home = info.get('config_home')
        clean_claude_settings = None
        if clean_room:
            src_home = Path(info['config_home']) if info.get('config_home') else None
            if prefix == 'codex':
                clean_codex = temp_path / 'clean-codex-home'
                clean_codex.mkdir(parents=True, exist_ok=True)
                if src_home and src_home.is_dir():
                    for fname in ('auth.json', 'models_cache.json'):
                        fsrc = src_home / fname
                        if fsrc.is_file() and not _is_link(fsrc):
                            shutil.copy2(fsrc, clean_codex / fname)
                effective_codex_home = str(clean_codex)
            elif prefix == 'claude':
                env_only = {}
                if src_home and (src_home / 'settings.json').is_file() and not _is_link(src_home / 'settings.json'):
                    try:
                        raw_cfg = json.loads((src_home / 'settings.json').read_text(encoding='utf-8-sig'))
                    except (OSError, ValueError):
                        raw_cfg = {}
                    if isinstance(raw_cfg, dict) and isinstance(raw_cfg.get('env'), dict):
                        env_only = {'env': raw_cfg['env']}
                clean_claude_settings = temp_path / 'clean-claude-settings.json'
                clean_claude_settings.write_text(json.dumps(env_only, ensure_ascii=False), encoding='utf-8')
            elif prefix == 'dsh':
                clean_dsh = temp_path / 'clean-dsh-home'
                clean_dsh.mkdir(parents=True, exist_ok=True)
                if src_home and (src_home / '.credentials.yaml').is_file() and not _is_link(src_home / '.credentials.yaml'):
                    shutil.copy2(src_home / '.credentials.yaml', clean_dsh / '.credentials.yaml')
                if src_home and not env.get('DEEPSEEK_API_KEY') and (src_home / '.env').is_file() and not _is_link(src_home / '.env'):
                    for line in _optional_text(src_home / '.env').splitlines():
                        if line.strip().startswith('#'):
                            continue
                        k_env, eq_env, v_env = line.partition('=')
                        if eq_env and re.sub(r'^export\s+', '', k_env.strip()) == 'DEEPSEEK_API_KEY':
                            extracted_key = _credential_scalar(v_env)
                            if _usable_credential(extracted_key):
                                env['DEEPSEEK_API_KEY'] = extracted_key
                            break
                effective_dsh_home = str(clean_dsh)
        if prefix == 'codex' and effective_codex_home and not interop:
            env['CODEX_HOME'] = effective_codex_home
        if prefix == 'claude' and not interop and (os.environ.get('CAE_CLAUDE_HOME') or os.environ.get('CLAUDE_CONFIG_DIR')):
            env['CLAUDE_CONFIG_DIR'] = info['config_home']
        if prefix == 'dsh':
            env['DSH_TELEMETRY_DISABLED'] = '1'
            if effective_dsh_home and not interop:
                env['DSH_HOME'] = effective_dsh_home
        native_pid_file = None
        exec_dir = target_dir
        stage_dir = None
        stage_before = None
        exec_prompt = prompt
        translate = _wsl_path if interop else str
        win_target = translate(target_dir)
        try:
            if (interop and win_target.startswith('\\\\')) or clean_room:
                if not readonly:
                    cli_label = Path(binary).name
                    native_hint = f'安装并登录原生 {prefix}'
                    raise HarnessError(f'WSL 纯 Linux 文件系统路径无法在 Windows {cli_label} 沙箱内直接写入；请在 WSL {native_hint}，或在 /mnt/<盘符> 工作区执行写入任务')
                stage_dir = _stage_wsl_readonly_workspace(
                    target_dir, temp_path, stage_files, exclude_agent_instructions=bool(clean_room)
                )
                stage_before = snapshot_files(stage_dir)
                receipt['staged_readonly_workspace'] = True
                exec_dir = stage_dir
                win_target = translate(stage_dir)
                exec_prompt = prompt + STAGED_PROMPT_SUFFIX
                if clean_room and prefix == 'codex':
                    staged_records = collect_target_files(stage_dir)
                    total_bytes = sum(item['bytes'] for item in staged_records)
                    inline_cap = (_positive_int(_headless_tunables().get('clean_room_inline_snapshot_kib')) or 256) * 1024
                    if staged_records and total_bytes <= inline_cap:
                        blocks = []
                        for item in staged_records:
                            try:
                                lines = item['path'].read_text(encoding='utf-8').splitlines()
                            except (OSError, UnicodeDecodeError):
                                continue
                            numbered = '\n'.join(f'{idx}: {line}' for idx, line in enumerate(lines, 1))
                            blocks.append(f"=== FILE: {item['rel_path']} ===\n{numbered}")
                        if blocks:
                            exec_prompt += '\n\n【只读目标文件带行号原文快照（可直接阅读，无需调用外部命令）】\n' + '\n\n'.join(blocks)
            if prefix == 'codex':
                command = [binary, 'exec', '-s', 'read-only' if readonly else 'workspace-write',
                           '-c', 'approval_policy="never"', '--ephemeral',
                           '--skip-git-repo-check', '-C', win_target, '--output-schema', translate(schema_file), '-o', translate(output_file)]
                if model:
                    command.extend(['-m', model])
                if effort:
                    command.extend(['-c', f'model_reasoning_effort="{effort}"'])
                context_window = profile['capacity']['cli_context_window_tokens']
                if context_window:
                    command.extend(['-c', f'model_context_window={context_window}'])
                command.append('-')
                input_text = exec_prompt
            elif prefix == 'agy':
                command = [binary, '--input-format', 'stream-json', '--output-format', 'stream-json',
                           '--print-timeout', f'{timeout_sec}s', '--json-schema', translate(schema_file),
                           '--disable-slash-commands', '--sandbox']
                if model:
                    command.extend(['--model', model])
                if effort and profile.get('effort_option_supported') is not False and not _agy_slug_tier(model)[1]:
                    command.extend(['--effort', effort])
                if readonly:
                    command.extend(['--agent', agent])
                else:
                    command.extend(['--mode', 'accept-edits', '--dangerously-skip-permissions'])
                input_text = json.dumps({'event': 'user', 'message': {'content': exec_prompt}}, ensure_ascii=False) + '\n'
            elif prefix == 'claude':
                command = [binary, '-p', '--output-format', 'json', '--json-schema', json.dumps(schema, ensure_ascii=False),
                           '--no-session-persistence']
                if clean_room:
                    command.append('--bare')
                    if clean_claude_settings is not None:
                        command.extend(['--settings', translate(clean_claude_settings)])
                if model:
                    command.extend(['--model', model])
                if effort in ('low', 'medium', 'high', 'xhigh', 'max'):
                    command.extend(['--effort', effort])
                if readonly:
                    command.extend(['--permission-mode', 'dontAsk', '--tools', 'Read,Grep,Glob', '--permission-prompts', 'none'])
                else:
                    command.extend(['--permission-mode', 'bypassPermissions', '--tools', 'Read,Grep,Glob,Edit,Write,Bash'])
                input_text = exec_prompt
            else:
                patch_file = temp_path / 'dsh-headless.patch.yml'
                provider = 'deepseek-official' if info.get('dsh_provider') == 'deepseek-official' else 'deepseek-account'
                dsh_default_slug = str(_catalog_rules('dsh').get('default_model') or info.get('model') or 'default')
                patch_file.write_text(
                    '- id: credentials\n  disabled: false\n- id: authorization\n  disabled: false\n'
                    '- id: deepseek-account\n  disabled: false\n- id: agent-default-model\n'
                    "  name: '@deepseek-ai/dsh-agent-default-model'\n  config:\n"
                    f'    provider: {provider}\n    model: {json.dumps(model or dsh_default_slug)}\n'
                    f'    reasoningEffort: {effort or "high"}\n', encoding='utf-8')
                command = [binary, '--profile', 'headless', '--patch', translate(patch_file), '--json', '--permission-mode',
                           'read-only' if readonly else 'workspace-write', '--approval-policy', 'never', '-']
                req_keys = ', '.join(schema.get('required', ()))
                exec_prompt = (f'{exec_prompt}\n\n请仅输出一个以 {{ 开头、以 }} 结尾且严格符合以下 JSON Schema 的单行或多行 JSON 对象'
                               f'（必含顶层键：{req_keys}；JSON 字符串内的反斜杠如 Windows 路径与 LaTeX 公式必须转义为双反斜杠 \\\\），'
                               f'严禁输出外层 Markdown 代码块围栏或任何额外解释文字：\n'
                               f'{json.dumps(schema, ensure_ascii=False)}')
                input_text = exec_prompt
            receipt.update(argv=list(command), work_dir=str(exec_dir), staged_readonly_workspace=stage_dir is not None,
                           clean_room=bool(clean_room),
                           command_transport='windows_interop' if interop else 'native')
            if interop:
                extra_env = {}
                if prefix == 'claude':
                    extra_env['CLAUDE_CONFIG_DIR'] = _wsl_path(Path(info['config_home']))
                elif prefix == 'dsh' and effective_dsh_home:
                    extra_env = {'DSH_HOME': _wsl_path(Path(effective_dsh_home)), 'DSH_TELEMETRY_DISABLED': '1'}
                command, native_pid_file = _interop_command(command, exec_dir, temp_path, input_text,
                                                            effective_codex_home if prefix == 'codex' else None,
                                                            extra_env=extra_env)
                input_text = None
            # Queued units must recheck after staging. A success already in flight
            # cannot clear a failure recorded by another unit in this same batch.
            _enforce_preflight_quota(backends, (spec,), batch=batch)
            receipt['launch_attempted'] = True
            agy_grace = _positive_int(_headless_tunables().get('agy_timeout_grace_sec')) or 15
            proc_timeout = timeout_sec + agy_grace if prefix == 'agy' else timeout_sec
            try:
                result = run_process(command, exec_dir, input_text, proc_timeout, env=env, native_pid_file=native_pid_file)
            except HarnessError as exc:
                receipt['invocation_started'] = bool(exc.details.get('process_started'))
                raise
            receipt['invocation_started'] = True
            receipt['returncode'] = result.returncode
            startup_stderr = all(not line.strip() or _is_quota_exhausted(line)
                                 for line in (result.stderr or '').splitlines())
            receipt['startup_only'] = (_quota_startup_only(result.stdout) and startup_stderr
                                       and not (output_file.exists() and output_file.stat().st_size))
            if prefix == 'agy' and result.returncode == 0:
                # 仅解释 stderr 中明确的终止诊断；普通警告不参与失败分类。
                diagnostics = [line.strip() for line in (result.stderr or '').splitlines()
                               if _parse_agy_error_line(line) is not None or re.match(
                                   r'^(?:\[agy\]\s+print timeout after\b|error:\s+print timeout after\b)', line.strip(), re.I)]
                if diagnostics:
                    detail = '\n'.join(diagnostics)
                    redacted_detail = _redact_runtime_secret(detail)
                    if _is_agy_print_timeout_text(detail):
                        raise HarnessError('agy 执行触发 --print-timeout 超时', 'timeout',
                                           returncode=0, finished_reason='print_timeout', stderr=redacted_detail[-2000:])
                    if _is_quota_exhausted(detail):
                        cooldown = _record_quota_exhausted(prefix, info, detail, batch=batch)
                        raise HarnessError('agy 额度不足或已达用量上限，已停止并记录冷却状态',
                                           'quota_exhausted', preflight_blocked=False, returncode=0,
                                           stderr=redacted_detail[-2000:], **_quota_user_choices(prefix, cooldown))
                    raise HarnessError('agy 返回终止错误诊断', 'failed', returncode=0, stderr=redacted_detail[-2000:])
            if result.returncode:
                detail = _backend_diagnostics(result)
                structured = _backend_diagnostics(result, structured_only=True)
                redacted_stderr = _redact_runtime_secret(result.stderr or '')[-2000:]
                if _is_quota_exhausted(structured or detail):
                    cooldown = _record_quota_exhausted(prefix, info, detail, batch=batch)
                    raise HarnessError(f'{prefix} 额度不足或已达用量上限，已停止并记录冷却状态',
                                       'quota_exhausted', preflight_blocked=False, returncode=result.returncode,
                                       stderr=redacted_stderr, **_quota_user_choices(prefix, cooldown))
                if prefix == 'agy' and _is_agy_print_timeout_text(detail):
                    raise HarnessError('agy 执行触发 --print-timeout 超时', 'timeout',
                                       returncode=result.returncode, finished_reason='print_timeout',
                                       stderr=redacted_stderr)
                # A worker may already have written files. Authentication fallback
                # is allowed only in model listing before planning and execution.
                raise HarnessError(f'{prefix} 执行失败，退出码 {result.returncode}', returncode=result.returncode,
                                   stderr=redacted_stderr)
            raw = output_file.read_text(encoding='utf-8-sig') if prefix == 'codex' and output_file.exists() else result.stdout
            try:
                parsed = _extract_json_object(raw, schema)
            except HarnessError as exc:
                if exc.status == 'quota_exhausted':
                    detail = str(exc.details.get('error') or raw)[-1000:]
                    cooldown = _record_quota_exhausted(prefix, info, detail, batch=batch)
                    raise HarnessError(f'{prefix} 额度不足或已达用量上限，已停止并记录冷却状态',
                                       'quota_exhausted', preflight_blocked=False,
                                       **_quota_user_choices(prefix, cooldown)) from exc
                raise
            _mark_quota_success(prefix, info, batch)
            if prefix == 'claude':
                envelope = _decode_output_json(raw)
                model_usage = envelope.get('modelUsage') if isinstance(envelope, dict) else None
                if isinstance(model_usage, dict) and model_usage:
                    receipt['actual_model'] = next(iter(model_usage))
                    receipt['actual_model_source'] = 'claude_result_model_usage'
            if prefix == 'agy':
                for line in (result.stdout or '').splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    init = event.get('init') if isinstance(event, dict) and event.get('event') == 'init' else None
                    if isinstance(init, dict) and isinstance(init.get('model'), str):
                        receipt['actual_model'] = init['model']
                        receipt['actual_model_source'] = 'agy_init_event'
                        break
            return _rewrite_staged_paths(parsed, stage_dir) if stage_dir is not None else parsed
        finally:
            if stage_dir is not None and stage_before is not None:
                receipt['staged_scope'] = _safe_changes_report(stage_before, stage_dir, [], readonly)


def invoke_readonly_engine(spec, target_dir, prompt, backends, timeout_sec=600, stage_files=None, effort=None, retry_quota=False,
                           *, profile=None, batch=None, receipt=None):
    return invoke_engine(spec, target_dir, prompt, backends, AUDIT_JSON_SCHEMA, True, timeout_sec,
                         'readonly-auditor', stage_files, effort, retry_quota, profile=profile, batch=batch, receipt=receipt)


def invoke_edit_worker(spec, target_dir, prompt, backends, timeout_sec=600, effort=None, retry_quota=False,
                       *, profile=None, batch=None, receipt=None):
    result = invoke_engine(spec, target_dir, prompt, backends, TASK_JSON_SCHEMA, False, timeout_sec,
                           'readonly-collaborator', None, effort, retry_quota, profile=profile, batch=batch, receipt=receipt)
    return json.dumps(result, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# ⑥ 工作区快照、分片审计规划与双盲归并
# ════════════════════════════════════════════════════════════════════════════


def snapshot_files(root):
    """对独立任务目录作事后核对，排除 Git 元数据，不撤销文件。"""
    root = Path(root)

    def traversal_failed(exc):
        directory = Path(exc.filename) if exc.filename else root
        try:
            location = directory.relative_to(root).as_posix()
        except ValueError:
            location = str(directory)
        raise HarnessError(f'无法遍历目录 {location}: {exc}', snapshot_path=location) from exc

    try:
        result = {'.': 'directory:' + str(stat.S_IMODE(root.stat().st_mode))}
    except OSError as exc:
        raise HarnessError(f'无法核对目标目录: {exc}', snapshot_path='.') from exc
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=traversal_failed):
        dirs[:] = sorted(d for d in dirs if d != '.git')
        for name in list(dirs):
            link = Path(directory) / name
            rel = link.relative_to(root).as_posix()
            try:
                if _is_link(link):
                    result[rel] = 'symlink:' + os.readlink(link)
                    dirs.remove(name)
                else:
                    result[rel + '/'] = 'directory:' + str(stat.S_IMODE(link.stat().st_mode))
            except OSError as exc:
                raise HarnessError(f'无法核对目录 {rel}: {exc}', snapshot_path=rel) from exc
        for name in files:
            path = Path(directory) / name
            if path == root / '.git':
                continue
            rel = path.relative_to(root).as_posix()
            try:
                st = path.lstat()
                if _is_link(path, st=st):
                    result[rel] = 'symlink:' + os.readlink(path)
                elif not stat.S_ISREG(st.st_mode):
                    raise HarnessError(f'工作区包含非常规特殊文件，无法安全快照: {rel}', snapshot_path=rel)
                else:
                    digest = hashlib.sha256()
                    with path.open('rb') as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(block)
                    result[rel] = digest.hexdigest() + ':' + str(stat.S_IMODE(st.st_mode))
            except OSError as exc:
                raise HarnessError(f'无法核对文件 {rel}: {exc}', snapshot_path=rel) from exc
    return result


def _scope_patterns(patterns):
    result = []
    for pattern in patterns:
        normalized = pattern.replace('\\', '/')
        if not normalized or normalized.startswith('/') or ':' in normalized or '..' in normalized.split('/'):
            raise HarnessError('--write-scope 必须使用目标目录内的相对路径或通配符')
        normalized = posixpath.normpath(normalized)
        if normalized == '.':
            raise HarnessError('--write-scope 不可为空路径')
        result.append(normalized)
    return result


def _scope_matches(path, scopes):
    def key(value):
        return value.casefold() if os.name == 'nt' else value

    return any(fnmatch.fnmatchcase(key(path), key(pattern)) for pattern in scopes)


def changed_files(before, after):
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    # 文件变化已能代表其新建/删除的父目录；单独保留空目录或目录权限变化。
    return sorted(path for path in changed if not path.endswith('/')
                  or (path in before and path in after)
                  or not any(other != path and other.startswith(path) for other in changed))


def _changes_report(before, root, scopes, readonly):
    changes = changed_files(before, snapshot_files(root))
    violations = changes if readonly else [path for path in changes if scopes and not _scope_matches(path, scopes)]
    return {'changed_files': changes, 'scope_violations': violations, 'scope_check': 'post_execution_snapshot',
            'scope_notice': '需使用独立任务目录，快照无法区分并发写入来源，检查不自动回滚。'}


def _safe_changes_report(before, root, scopes, readonly):
    try:
        report = _changes_report(before, root, scopes, readonly)
        report['scope_verified'] = True
        return report
    except (HarnessError, OSError) as exc:
        return {
            'changed_files': None,
            'scope_violations': [],
            'scope_check': 'unverified',
            'scope_verified': False,
            'scope_error': str(exc),
            'scope_error_path': getattr(exc, 'details', {}).get('snapshot_path'),
        }


def _workspace_unchanged(before, root):
    if before is None:
        return False
    scope = _safe_changes_report(before, root, [], True)
    return scope['scope_verified'] and not scope['changed_files']


def estimate_tokens_from_bytes(num_bytes):
    return max(1, (num_bytes + 2) // 3) if num_bytes else 0


def collect_target_files(target, skipped_files=None):
    target = target.resolve()
    skipped = skipped_files if skipped_files is not None else []
    candidates = [target] if target.is_file() else []
    if target.is_dir():
        for root, dirs, files in os.walk(target):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and (d == '.github' or not d.startswith('.')))
            for name in list(dirs):
                directory = Path(root) / name
                if _is_link(directory):
                    skipped.append({'file': directory.relative_to(target).as_posix(), 'reason': 'directory_link'})
                    dirs.remove(name)
            candidates.extend(Path(root) / name for name in sorted(files)
                              if Path(name).suffix.lower() in SOURCE_EXTENSIONS or name in NAMED_TEXT_FILES)
    records = []
    for path in candidates:
        rel = path.name if target.is_file() else path.relative_to(target).as_posix()
        if _is_link(path):
            skipped.append({'file': rel, 'reason': 'symbolic_link'})
            continue
        try:
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode):
                skipped.append({'file': rel, 'reason': 'special_file'})
                continue
            size = st.st_size
            with path.open('rb') as stream:
                stream.read(1)
        except OSError as exc:
            skipped.append({'file': rel, 'reason': 'unreadable', 'detail': str(exc)})
            continue
        if not size:
            skipped.append({'file': rel, 'reason': 'empty_file'})
            continue
        records.append({'path': path, 'rel_path': rel, 'bytes': size, 'tokens': estimate_tokens_from_bytes(size)})
    return records


def _normalize_rel_file(file_str, target_dir):
    cleaned = file_str.strip().replace('\\', '/')
    if _is_posix() and re.match(r'^(?:[A-Za-z]:/|//)', cleaned) and shutil.which('wslpath'):
        try:
            cleaned = _wsl_path(cleaned, windows=False)
        except HarnessError:
            pass
    candidate = Path(cleaned)
    # 无法映射的 Windows 绝对路径必须原样交给范围核对，不能伪装成相对路径。
    if not candidate.is_absolute() and ntpath.isabs(cleaned):
        return cleaned
    try:
        root = target_dir.resolve()
        absolute = candidate if candidate.is_absolute() else root / candidate
        return absolute.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return posixpath.normpath(cleaned)


def _build_audit_prompt(unit, focus):
    files = '\n'.join('- ' + path for path in unit['files'])
    return ('在当前目录按指定范围只读审查，给出文件和真实行号。不要修改文件，严禁在目标工作目录外创建或改动任何文件或目录（含严禁 git worktree add），且不要另行调用本编排器。\n重点: '
            + (focus or '物理守恒、边界条件、数值稳定性及代码一致性') + '\n计划文件清单:\n' + files
            + '\n按 JSON Schema 返回发现，read_files 列出已读取文件，unread_files 逐文件说明未完成的原因。')


def _audit_estimate(files, file_tokens, focus, interop=False):
    prompt = _build_audit_prompt({'files': files}, focus)
    schema_bytes = len(json.dumps(AUDIT_JSON_SCHEMA, ensure_ascii=False).encode('utf-8'))
    interop_bytes = len(STAGED_PROMPT_SUFFIX.encode('utf-8')) if interop else 0
    overhead = estimate_tokens_from_bytes(len(prompt.encode('utf-8')) + schema_bytes + interop_bytes)
    return {'estimated_file_tokens': file_tokens, 'estimated_prompt_tokens': overhead,
            'estimated_tokens': file_tokens + overhead}


def build_shards(file_records, max_shard_tokens, *, input_budget=None, focus='', interop=False):
    if max_shard_tokens <= 0:
        raise HarnessError('分片预算必须为正数')
    budget = input_budget if input_budget is not None else max_shard_tokens
    soft = min(max_shard_tokens, budget)
    shards, files, file_tokens = [], [], 0
    base_bytes = len(_build_audit_prompt({'files': []}, focus).encode('utf-8'))
    base_bytes += len(json.dumps(AUDIT_JSON_SCHEMA, ensure_ascii=False).encode('utf-8'))
    if interop:
        base_bytes += len(STAGED_PROMPT_SUFFIX.encode('utf-8'))
    path_bytes = 0
    for record in file_records:
        name = record['rel_path']
        size = len(('- ' + name).encode('utf-8'))
        if record['tokens'] + estimate_tokens_from_bytes(base_bytes + size) > budget:
            raise HarnessError(f'单文件与审查提示词超出输入预算: {name}')
        combined = file_tokens + record['tokens'] + estimate_tokens_from_bytes(base_bytes + path_bytes + size + bool(files))
        if files and combined > soft:
            shards.append({'shard_id': len(shards) + 1, 'files': files, **_audit_estimate(files, file_tokens, focus, interop)})
            files, file_tokens, path_bytes = [], 0, 0
        path_bytes += size + bool(files)
        files.append(name)
        file_tokens += record['tokens']
    if files:
        shards.append({'shard_id': len(shards) + 1, 'files': files, **_audit_estimate(files, file_tokens, focus, interop)})
    return shards


def _budget_for_spec(backends, spec):
    return resolve_execution_profile(backends, spec, kind='audit')['capacity']['input_budget_tokens']


def _audit_engines(backends, dual_blind, left_override, right_override, allow_unconfigured, caller, prompt_text=''):
    negated = _extract_negated_backends(prompt_text)
    if not left_override and caller == 'caller' and not _extract_prompt_backend_hint(prompt_text):
        available = [name for name in ('codex', 'agy', 'claude', 'dsh') if name not in negated and _peer_auto_ready(backends, name)]
        if not available:
            if allow_unconfigured:
                return None, None
            raise HarnessError('未发现已就绪的 agy、codex、claude 或 dsh 后端')
        left_override = available[0] + ':default'
    left = choose_engine(backends, caller, left_override, prompt_text)
    right = None
    if dual_blind:
        name, _ = _engine_parts(left)
        candidates = [peer for peer in ('agy', 'codex', 'claude', 'dsh') if peer != name and peer not in negated]
        if not right_override and not candidates:
            raise HarnessError('提示词已排除其余候选后端，无法组建双路审查')
        other = next((peer for peer in candidates if _peer_auto_ready(backends, peer)),
                     candidates[0] if candidates else ('agy' if name == 'codex' else 'codex'))
        right = choose_engine(backends, caller, right_override or other + ':default')
        if left == right:
            raise HarnessError('双路审查需要两个不同引擎或显式模型')
    return left, right


def select_audit_plan(total_tokens, file_records, backends, strategy='auto', dual_blind=True,
                      left_override='', right_override='', shard_tokens=None, allow_unconfigured=False, caller='caller',
                      *, focus='', profiles=None, engines=None):
    if shard_tokens is None:
        shard_tokens = _positive_int(_headless_tunables().get('default_shard_tokens')) or 120_000
    requested_strategy = strategy
    if strategy == 'whole-1m':
        strategy = 'single-pass'
    left, right = engines if engines is not None else _audit_engines(
        backends, dual_blind, left_override, right_override, allow_unconfigured, caller, focus)
    if not left:
        return {'mode': 'unconfigured', 'caller': caller, 'handoff_to_caller': True,
                'total_files': len(file_records), 'total_estimated_tokens': total_tokens,
                'left_engine': None, 'right_engine': None, 'left_units': [], 'right_units': [],
                'skipped_files': [], 'execution_profiles': {}, 'notice': '未发现已就绪的四端后端，当前只提供未就绪计划。'}
    if profiles is None:
        tier, _ = select_task_tier(kind='audit', text=focus)
        paths = [record['rel_path'] for record in file_records]
        estimated = _audit_estimate(paths, total_tokens, focus)['estimated_tokens']
        profiles = {}
        for side, spec in (('left', left), ('right', right)):
            if spec:
                profiles[side] = resolve_execution_profile(backends, spec, tier, focus, 'audit', estimated)
    if right and profiles['left']['model'] and profiles['left']['spec'] == profiles['right']['spec']:
        raise HarnessError('双路选路最终指向同一个模型；请为其中一路指定其他模型。')
    plan = {'mode': strategy, 'requested_strategy': requested_strategy, 'caller': caller, 'handoff_to_caller': True,
            'total_files': len(file_records), 'total_estimated_tokens': total_tokens,
            'dual_blind': bool(right), 'left_engine': left, 'right_engine': right, 'skipped_files': [],
            'execution_profiles': profiles, 'soft_shard_target_tokens': shard_tokens,
            'coverage_notice': '清单是计划覆盖范围，实际读取完整性仍由回执与证据核实。',
            'budget_notice': '按每 3 字节约 1 token 估算文件、审查提示词、清单与 JSON Schema，另保留策略余量；不表示分词器实测容量。',
            'independence_notice': '不同进程独立审查，模型家族及错误独立性尚未验证。'}
    all_files = [record['rel_path'] for record in file_records]
    whole = strategy != 'sharded'
    for side, spec in (('left', left), ('right', right)):
        units_key = side + '_units'
        plan[units_key] = []
        if not spec:
            continue
        profile = profiles[side]
        cap = profile['capacity']['input_budget_tokens']
        interop = profile['execution_environment']['windows_interop']
        all_estimate = _audit_estimate(all_files, total_tokens, focus, interop)
        if strategy != 'sharded' and all_estimate['estimated_tokens'] <= cap:
            plan[units_key] = [{'shard_id': 1, 'files': all_files, **all_estimate, 'single_pass_planned': True}]
            continue
        whole = False
        if strategy == 'single-pass':
            raise HarnessError(f'{spec} 文件与提示词超出单次预算，请选择 sharded 或调整有依据的预算。',
                               execution_profile=profile, **all_estimate)
        usable = []
        for record in file_records:
            estimate = _audit_estimate([record['rel_path']], record['tokens'], focus, interop)
            if estimate['estimated_tokens'] > cap:
                plan['skipped_files'].append({'file': record['rel_path'], 'engine': spec, 'side': side,
                                             'reason': 'over_input_budget', **estimate, 'budget_tokens': cap})
            else:
                usable.append(record)
        plan[units_key] = [{**unit, 'single_pass_planned': False,
                            'exceeds_soft_shard_target': unit['estimated_tokens'] > shard_tokens}
                           for unit in build_shards(usable, shard_tokens, input_budget=cap, focus=focus, interop=interop)]
    plan['mode'] = 'single-pass' if whole else 'sharded'
    if not whole:
        plan['coverage_notice'] += ' 跨分片依赖未在同一个上下文内联合核查。'
    if requested_strategy == 'whole-1m':
        plan['deprecation_notice'] = 'whole-1m 兼容映射至 single-pass，不表示百万 token 或无压缩能力。'
    return plan


def merge_blind_findings(left_findings, right_findings, left_engine, right_engine, target_dir, line_tolerance=None):
    """按相近行号组织候选发现，同一位置的意见仍需当前总控核实。"""
    if line_tolerance is None:
        line_tolerance = _positive_int(_headless_tunables().get('consensus_line_tolerance')) or 8
    ranks = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
    left = [{**item, 'file': _normalize_rel_file(item['file'], target_dir)} for item in left_findings]
    right = [{**item, 'file': _normalize_rel_file(item['file'], target_dir)} for item in right_findings]
    matched = set()
    consensus, singles = [], []
    for item in left:
        hit = None
        if right_engine:
            for index, other in enumerate(right):
                if (index not in matched and item['file'] == other['file']
                        and item['line_start'] - line_tolerance <= other['line_end']
                        and other['line_start'] - line_tolerance <= item['line_end']):
                    hit = index
                    break
        if hit is None:
            singles.append({**item, 'reported_by': [left_engine], 'independently_verified': False})
            continue
        matched.add(hit)
        other = right[hit]
        consensus.append({**item, 'line_start': min(item['line_start'], other['line_start']),
                          'line_end': max(item['line_end'], other['line_end']),
                          'severity': max((item['severity'], other['severity']), key=lambda level: ranks[level]),
                          'reported_by': [left_engine, right_engine], 'left_description': item['description'],
                          'right_description': other['description'], 'left_finding': item, 'right_finding': other,
                          'match_basis': 'location_overlap_only', 'independently_verified': False})
    singles.extend({**item, 'reported_by': [right_engine], 'independently_verified': False}
                   for i, item in enumerate(right) if i not in matched)
    key = lambda item: (-ranks[item['severity']], item['file'], item['line_start'])
    return {'consensus_findings': sorted(consensus, key=key), 'single_source_findings': sorted(singles, key=key)}


# ════════════════════════════════════════════════════════════════════════════
# ⑦ 物理门禁循环与五大 CLI 子命令入口
# ════════════════════════════════════════════════════════════════════════════


def run_physics_gate_loop(target_dir, task_desc, verify_cmd, worker_fn, verify_fn=None, max_rounds=3,
                          caller='caller', timeout_sec=600, write_scope=None):
    if not str(verify_cmd).strip():
        raise HarnessError('--verify-cmd 不能为空白命令')

    def default_verify(command, directory):
        result = run_process(command, directory, timeout_sec=timeout_sec, shell=True)
        return result.returncode, (result.stdout + '\n' + result.stderr).strip()

    verifier = verify_fn or default_verify
    scopes = _scope_patterns(write_scope or [])
    worker_changes = set()
    verification_changes = set()
    violations = set()
    history = []
    status = 'verification_failed'
    last_error = ''
    scope_failure = {}

    def checked_snapshot(phase, round_number=None):
        try:
            return snapshot_files(target_dir)
        except (HarnessError, OSError) as exc:
            scope_failure.update(scope_error_phase=phase, scope_error=str(exc))
            if round_number is not None:
                scope_failure['scope_error_round'] = round_number
            path = getattr(exc, 'details', {}).get('snapshot_path') or getattr(exc, 'filename', None)
            if path is not None:
                scope_failure['scope_error_path'] = str(path)
            return None

    initial = checked_snapshot('preflight')
    rounds = range(1, max_rounds + 1) if initial is not None else ()
    for number in rounds:
        before_worker = initial if number == 1 else checked_snapshot('pre_worker', number)
        if before_worker is None:
            break
        prompt = (f"按授权任务修改当前目录，允许写入范围为 {scopes or '调用方授权的目标工作目录'}。"
                  f'严禁在目标工作目录外创建、修改或删除任何文件或目录（含严禁向目录外执行 git worktree add）。\n'
                  f'{task_desc}\n验收命令为 {verify_cmd}。不得通过放宽验收要求取得通过。'
                  f'Git 提交与推送由当前总控负责。不要调用本编排器或改变其他会话。\n上一轮反馈:\n{last_error[-3500:]}')
        entry = {'round': number, 'passed': False, 'verify_exit_code': None, 'verification_executed': False}
        emit_event('gate_round_started', round=number, caller=caller)
        worker_ok = False
        try:
            entry['worker_summary'] = worker_fn(prompt, number)
            worker_ok = True
        except HarnessError as exc:
            status = exc.status
            entry.update(error=str(exc), status=exc.status, **exc.details)
            last_error = str(exc)
        except Exception as exc:
            status = 'failed'
            entry.update(error=str(exc), status='failed')
            last_error = str(exc)
        after_worker = checked_snapshot('post_worker', number)
        if after_worker is None:
            entry.update(scope_verified=False, scope_status='failed', **scope_failure)
            history.append(entry)
            break
        round_changes = changed_files(before_worker, after_worker)
        worker_changes.update(round_changes)
        round_violations = [path for path in round_changes if scopes and not _scope_matches(path, scopes)]
        violations.update(round_violations)
        entry['worker_changed_files'] = round_changes
        if round_violations:
            status = 'scope_violation'
            entry.update(status=status, scope_violations=round_violations)
            worker_ok = False
        if worker_ok:
            try:
                entry['verification_executed'] = True
                code, output = verifier(verify_cmd, target_dir)
                entry.update(verify_exit_code=code, verify_output_tail=output[-2000:], passed=code == 0)
                status = 'ok' if code == 0 else 'verification_failed'
                last_error = output
            except HarnessError as exc:
                status = exc.status
                entry.update(error=str(exc), status=exc.status, **exc.details)
                last_error = str(exc)
            except Exception as exc:
                status = 'failed'
                entry.update(error=str(exc), status='failed')
                last_error = str(exc)
            after_verify = checked_snapshot('post_verify', number)
            if after_verify is None:
                entry.update(passed=False, scope_verified=False, scope_status='failed', **scope_failure)
                history.append(entry)
                break
            verify_changes = changed_files(after_worker, after_verify)
            verification_changes.update(verify_changes)
            entry['verification_changed_files'] = verify_changes
        history.append(entry)
        if status in ('ok', 'cancelled', 'timeout', 'scope_violation', 'invalid_output', 'quota_exhausted'):
            break
    final = checked_snapshot('final') if not scope_failure else None
    changes = changed_files(initial, final) if final is not None else None
    extra_quota = {k: history[-1][k] for k in ('preflight_blocked', 'quota_backend', 'quota_execution_side', 'quota_cooldown',
                                             'quota_fuse_triggered', 'user_confirmation_required', 'user_choices')
                   if history and k in history[-1]} if status == 'quota_exhausted' else {}
    scope_error = {'error': scope_failure['scope_error'], **scope_failure} if scope_failure else {}
    if scope_failure and status not in ('quota_exhausted', 'cancelled', 'timeout', 'scope_violation'):
        status = 'failed'
    return {'status': status, 'gate_passed': status == 'ok', 'circuit_breaker_triggered': status != 'ok',
            'rounds_used': len(history), 'verify_cmd': verify_cmd, 'history': history,
            'verification_executed': any(item.get('verification_executed') for item in history),
            'caller': caller, 'handoff_to_caller': True, 'changed_files': changes,
            'worker_changed_files': sorted(worker_changes), 'verification_changed_files': sorted(verification_changes),
            'scope_violations': sorted(violations), 'scope_check': 'unverified' if scope_failure else 'post_worker_snapshot',
            'scope_verified': not scope_failure,
            'scope_notice': '需使用独立任务目录，快照无法区分并发写入来源。验收命令产物单独记录，检查不自动回滚。',
            **extra_quota, **scope_error}


def _args_backends(args, *, refresh_model_cache=None):
    if refresh_model_cache is None:
        refresh_model_cache = not getattr(args, 'dry_run', False)
    return probe_backends(args.codex_input_budget, args.agy_input_budget, getattr(args, 'discover_models', False),
                          refresh_model_cache=refresh_model_cache,
                          claude_input_budget=args.claude_input_budget, dsh_input_budget=args.dsh_input_budget)


def _target(args):
    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        raise HarnessError(f'目标不存在: {target}')
    return target, target if target.is_dir() else target.parent


def _task_text(args):
    text = Path(args.task_file).expanduser().resolve().read_text(encoding='utf-8-sig') if getattr(args, 'task_file', None) else args.task
    if not text or not text.strip():
        raise HarnessError('任务内容为空')
    return text


def _git_head(root):
    try:
        result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _target_metrics(requested_target, text):
    prompt_tokens = estimate_tokens_from_bytes(len((text or '').encode('utf-8')))
    if requested_target.is_file():
        try:
            size = requested_target.stat().st_size
        except OSError:
            size = 0
        return 1, estimate_tokens_from_bytes(size) + prompt_tokens
    records = collect_target_files(requested_target)
    return max(1, len(records)), sum(item['tokens'] for item in records) + prompt_tokens


def _prepare_execution(backends, specs, batch, *, dry_run=False):
    if dry_run:
        return
    # Check all selected sides before sending even a model-listing request.
    _enforce_preflight_quota(backends, specs, batch=batch)
    for spec in specs:
        if not spec:
            continue
        prefix, model = _engine_parts(spec)
        try:
            if prefix == 'claude':
                _verify_claude_llm_ready(backends[prefix], model, batch)
            elif prefix == 'dsh':
                _verify_dsh_llm_ready(backends[prefix])
        except HarnessError as exc:
            exc.details.setdefault('requested_engine', spec)
            exc.details.setdefault('execution_environment', _execution_environment(backends[prefix]))
            raise
    codex_spec = next((spec for spec in specs if spec and _engine_parts(spec)[0] == 'codex'), None)
    if codex_spec and not _current_catalog(backends['codex']):
        try:
            _refresh_codex_listing(backends['codex'], batch)
        except HarnessError as exc:
            exc.details.setdefault('requested_engine', codex_spec)
            exc.details.setdefault('execution_environment', _execution_environment(backends['codex']))
            raise
    agy_spec = next((spec for spec in specs if spec and _engine_parts(spec)[0] == 'agy'), None)
    if not agy_spec:
        return
    info = backends.get('agy', {})
    if not info.get('execution_config_supported', True):
        raise HarnessError('CAE_AGY_HOME 仅用于离线配置探测，执行请使用当前 CLI 的真实用户配置')
    authenticated = info.get('authentication_status') == 'authenticated'
    needs_side_check = _can_fallback_agy(info) and not authenticated and bool(_find_windows_agy_in_wsl())
    if needs_side_check or not _current_catalog(info):
        try:
            backends['agy'] = _refresh_agy_listing(info, batch, refresh_model_cache=True)
        except HarnessError as exc:
            exc.details.setdefault('requested_engine', agy_spec)
            raise
    _enforce_preflight_quota(backends, specs, batch=batch)


def _planned_profile(args, backends, engine, tier, reason, text, kind, estimated_tokens):
    profile = resolve_execution_profile(backends, engine, tier, text, kind, estimated_tokens)
    prefix, _ = _engine_parts(engine)
    profile.update(requested_tier=args.tier, tier_reason=reason,
                   quota_status=backends.get(prefix, {}).get('quota_status', 'unknown_until_called'))
    if (args.tier != 'auto' and prefix == 'agy' and profile['effort_source'] != 'fixed_configuration'
            and profile['declared_effort'] is None and profile['cli_effort'] is None):
        profile['tier_adjustment'] = 'requested_tier_not_applied_no_catalog_declaration'
    return profile


def _profile_payload(profile):
    return {key: profile[key] for key in ('requested_engine', 'requested_tier', 'model', 'reasoning_effort',
                                          'declared_effort', 'effective_effort', 'selected_tier', 'tier_reason',
                                          'profile_source', 'effort_source', 'quota_status')} | {'execution_profile': profile}


def _run_cli_gate(args, root, requested_target, text, caller, engine, backends, scopes, profile, kind, estimated_tokens, batch):
    receipts = []
    current = profile
    prefix, _ = _engine_parts(engine)
    retry_on_failure = (args.tier == 'auto' and profile['tier'] not in ('max', 'ultra')
                        and not profile['tier_reason'].startswith('user_description_'))

    def variant_key(selected):
        return (selected['spec'], selected['cli_effort'], selected['capacity']['cli_context_window_tokens'])

    def worker(prompt, number):
        nonlocal current
        previous = current
        if number == 2 and retry_on_failure:
            candidate = _planned_profile(args, backends, engine, 'max', 'gate_retry_escalated_max', text, kind, estimated_tokens)
            old = previous.get('declared_effort')
            new = candidate.get('declared_effort')
            if (not candidate.get('execution_blocked_reason') and variant_key(candidate) != variant_key(previous)
                    and new in EFFORT_LADDER and (old is None or EFFORT_LADDER.index(new) > EFFORT_LADDER.index(old))):
                current = candidate
        invocation = {}
        entry = {'execution_profile': current, 'invocation': invocation, 'variant_changed': variant_key(current) != variant_key(previous)}
        receipts.append(entry)
        return invoke_edit_worker(current['spec'], root, prompt, backends, args.timeout, current['cli_effort'], args.retry_quota,
                                  profile=current, batch=batch, receipt=invocation)

    result = run_physics_gate_loop(root, f'任务目标 {requested_target}。\n' + text, args.verify_cmd, worker,
                                   max_rounds=args.max_rounds, caller=caller, timeout_sec=args.timeout, write_scope=scopes)
    for entry, receipt in zip(result['history'], receipts):
        entry.update(receipt)
        entry['escalated'] = receipt['variant_changed'] and bool(receipt['invocation'].get('invocation_started'))
    last = next((entry['execution_profile'] for entry in reversed(result['history'])
                 if entry.get('invocation', {}).get('invocation_started')), profile)
    result.update(_profile_payload(last))
    result['quota_status'] = backends.get(prefix, {}).get('quota_status', last['quota_status'])
    return result


def _gate_can_replay(result, before, root):
    """第二轮、验收已执行、出现过改动或快照无法核对时，均不可自动重放。"""
    if (before is None or result.get('scope_verified') is not True
            or result.get('changed_files') != [] or result.get('worker_changed_files')
            or result.get('verification_changed_files') or result.get('rounds_used') != 1):
        return False
    entry = result['history'][0]
    if (entry.get('verification_executed') or entry.get('verify_exit_code') is not None
            or not _quota_can_replay(entry, entry.get('invocation', {}))):
        return False
    scope = _safe_changes_report(before, root, [], True)
    return scope['scope_verified'] and not scope['changed_files']


def _run_gate_with_fallback(args, root, requested_target, text, caller, engine, backends,
                            scopes, profile, kind, estimated_tokens, batch, meta, before):
    result = _run_cli_gate(args, root, requested_target, text, caller, engine, backends,
                           scopes, profile, kind, estimated_tokens, batch)
    if (result.get('status') != 'quota_exhausted' or not getattr(args, 'fallback_on_quota', False)
            or 'quota_fallback' in meta or not _gate_can_replay(result, before, root)):
        return result
    fallback = _local_fallback_engine(caller, engine, backends, text)
    if not fallback:
        return result
    meta['quota_fallback'] = _quota_fallback_record(engine, fallback, result)
    try:
        _prepare_execution(backends, (fallback,), batch)
        if not _workspace_unchanged(before, root):
            meta.pop('quota_fallback')
            return result
        selected = _planned_profile(args, backends, fallback, profile['tier'], profile['tier_reason'],
                                     text, kind, estimated_tokens)
        _require_execution_profile(selected)
    except HarnessError as exc:
        # 本侧准备失败时保留原额度回执与门禁历史，便于总控复核两次尝试。
        return {**result, 'status': exc.status, 'error': str(exc), **exc.details}
    meta.update(engine=fallback)
    if 'worker_engine' in meta:
        meta['worker_engine'] = fallback
    meta.update(_profile_payload(selected))
    return _run_cli_gate(args, root, requested_target, text, caller, fallback, backends,
                         scopes, selected, kind, estimated_tokens, batch)


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return EXIT_CODES.get(payload.get('status', 'ok'), 1)


def _fleet_profile_summary():
    prof = _fleet_profile()
    if not prof:
        return None
    return {
        'profile_id': prof.get('profile_id'),
        'evaluated_at': prof.get('evaluated_at'),
        'evaluation_policy': prof.get('evaluation_policy'),
        'roles': prof.get('roles'),
        'excluded_or_retiring': prof.get('excluded_or_retiring'),
        'runtime_tunables': prof.get('runtime_tunables'),
    }


_GROUNDING_GENERIC_ACRONYMS = frozenset({
    'API', 'CLI', 'CPU', 'GPU', 'RAM', 'OOM', 'ROM', 'SSD', 'SSH', 'SSL', 'TLS', 'TCP', 'UDP', 'DNS', 'URL', 'URI',
    'HTTP', 'HTTPS', 'JSON', 'JSONL', 'YAML', 'TOML', 'XML', 'HTML', 'CSS', 'SVG', 'PNG', 'JPG', 'PDF', 'UTF', 'BOM',
    'CRLF', 'EOF', 'EOL', 'AST', 'PID', 'TID', 'UID', 'GID', 'WSL', 'POSIX', 'GNU', 'MIT', 'BSD', 'SHA', 'RSA',
    'LLM', 'IDE', 'SDK', 'MCP', 'SSE', 'RPC', 'IPC', 'ABI', 'ENV', 'CWD', 'TMP', 'DIR', 'WIN', 'MAC', 'APP',
    'PRO', 'MAX', 'MIN', 'AVG', 'TTL', 'LRU', 'RPM', 'TPM', 'QPS', 'TUN', 'NAT', 'LAN', 'WAN', 'VPN', 'RFC', 'ISO',
    'UTC', 'GMT', 'CSC', 'ASCII', 'OAUTH',
})

_GROUNDING_EXTERNAL_CLIS = frozenset({
    'git', 'agy', 'codex', 'claude', 'cc', 'dsh', 'python', 'python3', 'py', 'wsl', 'powershell', 'pwsh',
    'bash', 'sh', 'cmd', 'curl', 'uv', 'pip', 'npm', 'npx', 'cargo', 'cmake', 'ctest', 'pytest', 'mpirun', 'mpiexec',
})

_GROUNDING_STOP_CJK2 = frozenset({
    '计算', '科学', '工程', '系统', '软件', '分析', '数值', '规划', '方法', '模型', '算法', '项目', '求解', '问题',
    '检查', '判据', '连续', '容差', '有限', '二维', '三维', '复杂', '高可', '可靠', '靠系', '统软', '件研', '研发',
    '程研', '靠工', '发中', '算流', '体力', '元分', '性有', '线性', '非线', '解器', '器或', '库中', '领域', '标准',
    '术语', '日常', '课题', '常见', '场景', '模块', '功能',
})

_GROUNDING_ANCHOR_EXTS = frozenset({
    '.py', '.f90', '.f95', '.f03', '.f08', '.f', '.for', '.c', '.cpp', '.cc', '.cxx', '.cu', '.cuh',
    '.h', '.hpp', '.hh', '.jl', '.rs', '.go', '.ts', '.js', '.cmake', '.toml', '.yaml', '.yml', '.json',
    '.sh', '.bash', '.ps1', '.cfg', '.ini',
})


def _extract_cjk_2grams(text):
    return {
        run[idx : idx + 2]
        for run in re.findall(r'[\u4e00-\u9fff]{2,}', text or '')
        for idx in range(len(run) - 1)
    }


def _extract_anchor_numbers(text):
    nums = {'0', '1', '2', '3', '4', '5', '7', '8', '10', '16', '32', '64', '100', '128', '256', '512', '1024'}
    for raw in re.findall(r'(?<![A-Za-z_])\d[\d_,]*(?:\.\d+)?(?![A-Za-z_])', text or ''):
        cleaned = raw.replace(',', '').replace('_', '')
        nums.add(cleaned)
        if '.' in cleaned:
            try:
                fval = float(cleaned)
                nums.add(f'{fval:g}')
                if 0.0 < fval <= 1.0:
                    pct = int(round(fval * 100))
                    nums.add(str(pct))
                    if 0 < pct < 100:
                        nums.add(str(100 - pct))
            except ValueError:
                pass
        else:
            try:
                ival = int(cleaned)
                if ival >= 1024 and ival % 1024 == 0:
                    nums.add(str(ival // 1024))
                    if ival >= 1024 * 1024 and ival % (1024 * 1024) == 0:
                        nums.add(str(ival // (1024 * 1024)))
                if ival >= 10_000:
                    nums.add(str(ival * 9 // 10))
                    nums.add(str((ival * 95 // 100) * 9 // 10))
                    if ival % 1000 == 0:
                        nums.add(str(ival // 1000))
            except ValueError:
                pass
    return nums


def _build_grounding_anchor(root_dir, exclude_rels, allow_files=None, extra_allowed_text=''):
    root_dir = Path(root_dir).resolve()
    anchor_chunks = []
    repo_names = set()
    dynamic_file_globs = set()
    for dirpath, dirs, files in os.walk(root_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        dpath = Path(dirpath)
        for fname in sorted(files):
            fpath = dpath / fname
            if _is_link(fpath) or not fpath.resolve().is_relative_to(root_dir):
                continue
            rel = fpath.resolve().relative_to(root_dir).as_posix()
            repo_names.add(fname.lower())
            repo_names.add(fpath.stem.lower())
            repo_names.add(rel.lower())
            if rel in exclude_rels or rel.startswith('tests/'):
                continue
            is_agent_contract = (
                (rel.startswith('template/user-guard/agents/') or rel.startswith('template/claude/agents/')
                 or rel.startswith('.claude/agents/') or rel.startswith('.codex/agents/'))
                and fpath.suffix.lower() in ('.md', '.toml')
            )
            if fpath.suffix.lower() in _GROUNDING_ANCHOR_EXTS or is_agent_contract or rel in ('NOTICE', 'CMakeLists.txt'):
                txt = _optional_text(fpath)
                if txt:
                    anchor_chunks.append(txt)
                    for pat in re.findall(r'([A-Za-z0-9_.-]*\{[^}\r\n]{1,32}\}[A-Za-z0-9_.-]*\.[A-Za-z0-9]+)', txt):
                        dynamic_file_globs.add(re.sub(r'\{[^}]+\}', '*', pat).lower())
    for extra_file in (allow_files or ()):
        ep = Path(extra_file).expanduser()
        if not ep.is_absolute():
            ep = root_dir / ep
        if ep.is_file() and not _is_link(ep):
            anchor_chunks.append(_optional_text(ep))
    if extra_allowed_text:
        anchor_chunks.append(str(extra_allowed_text))
    combined = '\n'.join(anchor_chunks)
    low = combined.lower()
    words = set(re.findall(r'[a-z0-9]+(?:[._-][a-z0-9]+)*', low)) | set(re.findall(r'[a-z0-9]+', low))
    flags = set(re.findall(r'(?<![a-z0-9])(--[a-z0-9][a-z0-9_-]*)', low))
    cjk2 = _extract_cjk_2grams(combined)
    numbers = _extract_anchor_numbers(combined)
    return {
        'words': words,
        'flags': flags,
        'cjk2': cjk2,
        'numbers': numbers,
        'repo_names': repo_names,
        'dynamic_file_globs': dynamic_file_globs,
    }


def _iter_visible_doc_lines(rel_path, raw_text):
    """提取文档或 SVG 中需要做事实锚定核验的可见文本行（跳过代码块围栏，SVG 仅提取 <text> 节点）。"""
    if rel_path.lower().endswith('.svg'):
        for m in re.finditer(r'<text\b[^>]*>([\s\S]*?)</text>', raw_text, re.I):
            line_no = raw_text.count('\n', 0, m.start()) + 1
            plain = re.sub(r'<[^>]+>', ' ', m.group(1))
            plain = (plain.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
                     .replace('&quot;', '"').replace('&#39;', "'")).strip()
            if plain:
                yield line_no, plain
        return
    fence_marker = ''
    fence_len = 0
    for line_no, line in enumerate(raw_text.splitlines(), 1):
        stripped = line.lstrip()
        m_fence = re.match(r'^(`{3,}|~{3,})', stripped)
        if m_fence:
            tok = m_fence.group(1)
            if not fence_marker:
                rest = stripped[len(tok):]
                if tok not in rest:
                    fence_marker, fence_len = tok[0], len(tok)
                    continue
            elif tok[0] == fence_marker and len(tok) >= fence_len and not stripped[len(tok):].strip():
                fence_marker, fence_len = '', 0
                continue
        if not fence_marker:
            yield line_no, line


def _audit_single_doc_text(rel_path, doc_text, anchor, baseline_text=''):
    findings = []
    seen = set()
    baseline_words = set(re.findall(r'[a-z0-9]+', baseline_text.lower())) if baseline_text else set()
    baseline_cjk2 = _extract_cjk_2grams(baseline_text) if baseline_text else set()
    baseline_nums = _extract_anchor_numbers(baseline_text) if baseline_text else set()

    def record(line_no, category, token, reason):
        key = (rel_path, line_no, category, token)
        if key not in seen:
            seen.add(key)
            findings.append({
                'file': rel_path,
                'line': line_no,
                'category': category,
                'token': token,
                'reason': reason,
            })

    for line_no, raw_line in _iter_visible_doc_lines(rel_path, doc_text):
        # 保护行内代码跨度中的 $VAR，避免两个代码跨度之间的文本被误识别为行内公式 $...$，同时保留代码标识符供后续枚举锚定
        code_spans = re.findall(r'`([^`\n]+)`', raw_line)
        line_safe_code = re.sub(r'`[^`\n]+`', lambda m: m.group(0).replace('$', '_'), raw_line)
        no_math_prose = re.sub(r'\$\$?[^$\n]+\$\$?', ' ', line_safe_code)
        no_anti_quote = re.sub(r'“[^”\n]{1,60}”', ' ', no_math_prose)

        # 1. 行内代码标识符（CLI 选项与源码/配置文件名）
        for span in code_spans:
            span_clean = span.strip()
            if not span_clean:
                continue
            has_placeholder = '<' in span_clean or '>' in span_clean
            flag_scan_text = re.sub(r'<[^>\n]*>', ' ', span_clean) if has_placeholder else span_clean
            head_source = flag_scan_text.strip()
            if not head_source:
                continue
            head_tok = re.split(r'[\s/\\]+', head_source, maxsplit=1)[0].strip('"\'').lower()
            head_stem = Path(head_tok).stem.lower()
            is_external_cmd = head_tok in _GROUNDING_EXTERNAL_CLIS or head_stem in _GROUNDING_EXTERNAL_CLIS
            if not is_external_cmd:
                for flag in re.findall(r'(?<![A-Za-z0-9])(--[a-z0-9][a-z0-9_-]*)', flag_scan_text.lower()):
                    if flag not in anchor['flags']:
                        record(line_no, 'ungrounded_identifier', flag, '行内代码引用的 CLI 选项未在仓库脚本或配置中声明')
            else:
                # 对 python install.py / cae_harness.py / fleet_state.py / ccswitch_admin.py 调用仍严格校验参数
                if any(s in flag_scan_text.lower() for s in ('install.py', 'cae_harness.py', 'fleet_state.py', 'ccswitch_admin.py', 'handoff_gate.py', 'router.py')):
                    for flag in re.findall(r'(?<![A-Za-z0-9])(--[a-z0-9][a-z0-9_-]*)', flag_scan_text.lower()):
                        if flag not in anchor['flags']:
                            record(line_no, 'ungrounded_identifier', flag, '仓库脚本调用参数未在源码 argparse 中声明')
            if not is_external_cmd and not has_placeholder:
                for fn in re.findall(r'\b([A-Za-z0-9_.*-]+(?:/[A-Za-z0-9_.*-]+)*\.(?:py|json|toml|yaml|yml|svg|sh|ps1))\b', span_clean):
                    fn_low = fn.lower().lstrip('./')
                    base_low = Path(fn_low).name
                    if '*' in base_low:
                        continue
                    if (fn_low in anchor['repo_names'] or base_low in anchor['repo_names']
                            or base_low in anchor['words'] or base_low.lstrip('.') in anchor['words']
                            or any(fnmatch.fnmatchcase(base_low, pat) for pat in anchor['dynamic_file_globs'])):
                        continue
                    record(line_no, 'ungrounded_identifier', fn, '行内代码引用的脚本或配置文件名在仓库与锚点语料中均不存在')

        # 2. 全大写领域缩写词（零领域黑名单：必须存在于锚点语料、仓库文件名或基础计算机协议集合中）
        prose_no_urls = re.sub(r'https?://\S+', ' ', no_anti_quote)
        for acro in re.findall(r'(?<![A-Za-z0-9_])([A-Z][A-Z0-9]{2,})(?![A-Za-z0-9_])', prose_no_urls):
            if (acro in _GROUNDING_GENERIC_ACRONYMS
                    or re.fullmatch(r'(?:R\d{1,2}|L\d+|V\d+|\d+M|\d+K|\d+G)', acro)
                    or acro.lower() in anchor['repo_names']
                    or acro.lower() in anchor['words']
                    or acro.lower() in baseline_words):
                continue
            record(line_no, 'ungrounded_acronym', acro, '大写技术缩写在仓库源码、配置与允许锚点中无对应依据')

        # 3. 带容量/比例/阈值单位的定量数字（防止重写时编造死数字或与 fleet_profile.json 失步）
        for num_m in re.finditer(r'(?<![A-Za-z0-9_.-])(\d{1,3}(?:,\d{3})+|\d{2,}(?:\.\d+)?)\s*(token|tokens|字符|KB|KiB|MB|MiB|%)', prose_no_urls, re.I):
            raw_num, unit = num_m.group(1), num_m.group(2)
            # 跳过区间近似表达（如 200k–260k）以外的精确数字前先归一化
            norm_num = raw_num.replace(',', '')
            if norm_num in anchor['numbers'] or norm_num in baseline_nums:
                continue
            record(line_no, 'ungrounded_number', f'{raw_num} {unit}', '定量数值未在 fleet_profile.json 或源码常量中找到对应依据')

        # 4. 举例与括注并列枚举短语（例如/比如/涵盖/面向... 及全角括号枚举）：零黑名单检测无源码锚点的虚构领域词
        clean_prose = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', no_anti_quote)
        enum_clauses = [
            ex_m.group(1)
            for ex_m in re.finditer(r'(?:例如|比如|譬如|涵盖|面向)[:：]?\s*([^。；\n|]{4,160})', clean_prose)
        ] + [
            paren_m.group(1)
            for paren_m in re.finditer(r'（([^（）\n]{6,160})）', clean_prose)
        ]
        for clause in enum_clauses:
            if '、' not in clause:
                continue
            items = [
                seg.strip()
                for seg in re.split(
                    r'[、，,]|(?:\s+(?:与|和|及|或|以及)\s+)|(?:(?<=[\u4e00-\u9fff）\)])(?:与|和|及|或|以及)(?=[\u4e00-\u9fffA-Za-z]))',
                    clause,
                )
                if seg.strip()
            ]
            if len(items) < 2:
                continue
            for item in items:
                ascii_toks = [tok.lower() for tok in re.findall(r'[A-Za-z][A-Za-z0-9_.-]{1,}', item)]
                if any(tok in anchor['words'] or tok in anchor['repo_names'] or tok in baseline_words for tok in ascii_toks):
                    continue
                stripped_item = re.sub(r'（[^）]*）|\([^)]*\)|`[^`]*`', ' ', item)
                grams = _extract_cjk_2grams(stripped_item) - _GROUNDING_STOP_CJK2
                if len(grams) >= 2 and not (grams & anchor['cjk2']) and not (grams & baseline_cjk2):
                    record(line_no, 'ungrounded_exemplar', item, '举例或适用范围枚举项在仓库源码、配置与允许锚点中无词干依据')

        # 5. 若提供了改写前基线（如 polish 原稿或 --base-ref），额外拦截改写过程中凭空新增的无依据并列技术名词
        if baseline_text and '、' in clean_prose:
            for part in re.split(r'[、，,。；;|：:]|(?:\s*(?:与|和|及|或|以及)\s*)', clean_prose):
                part = part.strip()
                if len(part) < 4:
                    continue
                ascii_toks = [tok.lower() for tok in re.findall(r'[A-Za-z][A-Za-z0-9_.-]{1,}', part)]
                if any(tok in anchor['words'] or tok in baseline_words for tok in ascii_toks):
                    continue
                stripped_part = re.sub(r'（[^）]*）|\([^)]*\)|`[^`]*`', ' ', part)
                grams = _extract_cjk_2grams(stripped_part) - _GROUNDING_STOP_CJK2
                if len(grams) >= 3 and not (grams & anchor['cjk2']) and not (grams & baseline_cjk2):
                    record(line_no, 'ungrounded_rewrite_addition', part, '润色或重写阶段新增了原稿与仓库锚点均未出现的陌生领域短语')

    return findings


def audit_doc_grounding(target_path, root_dir=None, *, base_ref=None, allow_files=None, extra_allowed_text=''):
    """对目标文档或目录执行零黑名单确定性事实锚定核验（标识符、缩写、容量数字与举例词干）。"""
    target = Path(target_path).expanduser().resolve()
    if not target.exists():
        raise HarnessError(f'目标路径不存在: {target}')
    if root_dir is not None:
        root = Path(root_dir).expanduser().resolve()
    else:
        mode_info = detect_workspace_mode(target)
        root = Path(mode_info['upstream_repo_root']) if mode_info['upstream_repo_root'] else (
            target if target.is_dir() else target.parent
        )
    doc_files = []
    if target.is_file():
        rel = target.relative_to(root).as_posix() if target.is_relative_to(root) else target.name
        doc_files.append((target, rel))
    else:
        skip_rels = {'CHANGELOG.md', 'LICENSE', 'NOTICE'}
        for dirpath, dirs, files in os.walk(target, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
            dpath = Path(dirpath)
            for fname in sorted(files):
                fpath = dpath / fname
                if _is_link(fpath) or fpath.suffix.lower() not in ('.md', '.svg', '.rst'):
                    continue
                resolved_f = fpath.resolve()
                rel = resolved_f.relative_to(root).as_posix() if resolved_f.is_relative_to(root) else resolved_f.relative_to(target).as_posix()
                if (rel in skip_rels or rel.startswith('tests/')
                        or rel.startswith('template/user-guard/agents/')
                        or rel.startswith('template/claude/agents/')
                        or rel == 'template/user-guard/assets/logo.svg'):
                    continue
                doc_files.append((fpath, rel))
    exclude_rels = {rel for _, rel in doc_files}
    anchor = _build_grounding_anchor(root, exclude_rels, allow_files=allow_files, extra_allowed_text=extra_allowed_text)
    all_findings = []
    checked_files = []
    for fpath, rel in doc_files:
        checked_files.append(rel)
        baseline_txt = ''
        if base_ref:
            try:
                proc = subprocess.run(
                    ['git', '-C', str(root), 'show', f'{base_ref}:{rel}'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
                )
                if proc.returncode == 0:
                    baseline_txt = proc.stdout
            except (OSError, subprocess.SubprocessError):
                pass
        all_findings.extend(_audit_single_doc_text(rel, _optional_text(fpath), anchor, baseline_txt))
    by_category = collections.Counter(item['category'] for item in all_findings)
    return {
        'ok': not all_findings,
        'root': str(root),
        'target': str(target),
        'base_ref': base_ref or None,
        'checked_file_count': len(checked_files),
        'checked_files': checked_files,
        'finding_count': len(all_findings),
        'counts_by_category': dict(sorted(by_category.items())),
        'findings': all_findings,
    }


def cmd_check_grounding(args):
    report = audit_doc_grounding(
        args.target,
        args.root or None,
        base_ref=args.base_ref or None,
        allow_files=args.allow_from_file or (),
    )
    status = 'verification_failed' if (args.strict and not report['ok']) else 'ok'
    return _print({'status': status, 'workspace_mode': detect_workspace_mode(args.root or args.target), **report})


def cmd_probe(args):
    refresh = bool(getattr(args, 'discover_models', False))
    return _print({'status': 'ok', 'caller': resolve_caller(args.caller),
                   'workspace_mode': detect_workspace_mode(),
                   'fleet_profile': _fleet_profile_summary(),
                   'backends': _args_backends(args, refresh_model_cache=refresh)})


def cmd_assess(args):
    backends = _args_backends(args, refresh_model_cache=False)
    caller = resolve_caller(args.caller)
    local_backend = {'codex': 'codex', 'antigravity': 'agy', 'claude': 'claude', 'dsh': 'dsh'}.get(caller)
    scenarios = {kind: (kind, '') for kind in ('review', 'direction', 'polish', 'math', 'code', 'audit', 'gate')}
    scenarios['surface_review'] = ('review', '检查注释拼写')
    scenarios['economy_review'] = ('review', '省额度做常规代码审查')
    scenarios['economy_surface_review'] = ('review', '省额度检查注释拼写')
    recommendations = {}
    for name, info in backends.items():
        detected = bool(info.get('detected', info.get('available')))
        info['caller_relation'] = ('local' if name == local_backend else 'counterpart') if local_backend else 'unspecified'
        info['program_origin'] = ('windows_interop' if info.get('windows_interop') else 'native') if detected else None
        info['execution_environment'] = _execution_environment(info)
        info['capability_matrix'] = _capability_matrix(name, info)
        info['model_metadata_sources'] = {
            'context': 'codex_client_cache_context_fields' if name == 'codex' and any(
                model.get('context_window') or model.get('max_context_window') for model in info.get('models', [])) else
                'dsh_builtin_model_contract' if name == 'dsh' else None,
            'effort': ('codex_client_cache_reasoning_levels' if name == 'codex' and any(
                model.get('supported_efforts') for model in info.get('models', [])) else
                'agy_catalog_slug_suffix' if name == 'agy' and any(
                    _agy_slug_tier(model['slug'])[1] for model in info.get('models', [])) else
                'dsh_builtin_model_contract' if name == 'dsh' else None),
            'runtime_verified': False,
        }
        recommendations[name] = {}
        for scenario, (kind, prompt) in scenarios.items():
            tier, reason = select_task_tier(kind=kind, text=prompt)
            profile = resolve_execution_profile(backends, name + ':default', tier, prompt, kind=kind)
            recommendations[name][scenario] = {**profile, 'requested_tier': 'auto', 'tier_reason': reason,
                                                'backend_detected': detected}
    resolved_roles = {}
    roles_cfg = (_fleet_profile().get('roles') or {}) if isinstance(_fleet_profile().get('roles'), dict) else {}
    for role_name in ('orchestrator', 'math', 'code', 'review', 'fast'):
        try:
            role_spec = _resolve_role_engine(backends, caller, f'role:{role_name}')
            cfg_entry = roles_cfg.get(role_name) if isinstance(roles_cfg.get(role_name), dict) else {}
            rec_tier = str(cfg_entry.get('recommended_tier') or '').strip().lower()
            role_tier = (rec_tier if rec_tier in TIER_CHOICES and rec_tier not in ('auto', 'ultra')
                         else ('low' if role_name == 'fast' else 'max'))
            role_kind = 'review' if role_name in ('fast', 'orchestrator') else role_name
            role_prof = resolve_execution_profile(backends, role_spec, role_tier, kind=role_kind)
            resolved_roles[role_name] = {
                'selected_engine': role_spec,
                'model': role_prof['model'],
                'reasoning_effort': role_prof['reasoning_effort'],
                'recommended_tier': role_tier,
                'dynamic_slot': True,
            }
        except HarnessError as exc:
            resolved_roles[role_name] = {'selected_engine': None, 'blocked_reason': str(exc), 'dynamic_slot': True}
    return _print({'status': 'ok', 'caller': caller, 'workspace_mode': detect_workspace_mode(),
                   'fleet_profile': _fleet_profile_summary(),
                   'resolved_roles': resolved_roles,
                   'backends': backends,
                   'recommendations': recommendations,
                   'catalog_discovery_requested': args.discover_models,
                   'runtime_verified': False, 'controls_native_spawn_agent': False,
                   'notice': '能力矩阵和选路基于目录及策略。实际可用性、性能、有效上下文、压缩和委派行为仍需执行验证。'})


def cmd_audit(args):
    target, root = _target(args)
    caller = resolve_caller(args.caller)
    backends = _args_backends(args)
    skipped = []
    records = collect_target_files(target, skipped)
    if not records:
        raise HarnessError('目标范围没有可审查文件', skipped_files=skipped)
    total_tokens = sum(item['tokens'] for item in records)
    engines = _audit_engines(backends, not args.single, args.left, args.right, args.dry_run, caller, args.focus)
    audit_engine_hint = (args.left or (engines[0] or '')) if args.single else ''
    tier, tier_reason = select_task_tier(args.tier, 'audit', args.focus, len(records), total_tokens, engine=audit_engine_hint)
    meta = {'caller': caller, 'handoff_to_caller': True, 'requested_tier': args.tier,
            'selected_tier': tier, 'tier_reason': tier_reason, 'target': str(root)}
    batch = _QuotaBatch(args.retry_quota)
    fallback_before = snapshot_files(root) if getattr(args, 'fallback_on_quota', False) and not args.dry_run else None
    try:
        _prepare_execution(backends, engines, batch, dry_run=args.dry_run)
    except HarnessError as exc:
        q_backend = exc.details.get('quota_backend') or ''
        fb_engine = _local_fallback_engine(caller, f'{q_backend}:default', backends, args.focus) if (
            exc.status == 'quota_exhausted' and q_backend and getattr(args, 'fallback_on_quota', False)
            and _workspace_unchanged(fallback_before, root)
        ) else None
        if not fb_engine:
            return _print({'status': exc.status, **meta, 'error': str(exc), 'requested_engines': list(engines), **exc.details})
        tripped_spec = next((e for e in engines if e and _engine_parts(e)[0] == q_backend), f'{q_backend}:default')
        kept = [e for e in engines if e and _engine_parts(e)[0] != q_backend]
        if kept and not args.single:
            kept_prefix = _engine_parts(kept[0])[0]
            alt_fb = _local_fallback_engine(
                caller, f'{q_backend}:default', backends, args.focus, exclude_prefixes=(q_backend, kept_prefix)
            )
            if alt_fb:
                engines = (alt_fb, engines[1]) if (engines[0] and _engine_parts(engines[0])[0] == q_backend) else (engines[0], alt_fb)
                meta['quota_fallback'] = _quota_fallback_record(tripped_spec, alt_fb, exc.details)
            else:
                engines = (kept[0], None)
                meta['quota_fallback'] = _quota_fallback_record(tripped_spec, kept[0], exc.details)
        else:
            target_engine = kept[0] if kept else fb_engine
            meta['quota_fallback'] = _quota_fallback_record(tripped_spec, target_engine, exc.details)
            engines = (target_engine, None)
        try:
            _prepare_execution(backends, engines, batch, dry_run=args.dry_run)
            if not _workspace_unchanged(fallback_before, root):
                raise HarnessError('回退预检后工作区发生变化或无法核对，已停止执行')
        except HarnessError as fb_exc:
            return _print({'status': fb_exc.status, **meta, 'error': str(fb_exc), 'requested_engines': list(engines), **fb_exc.details})
    profiles = {}
    for side, engine in zip(('left', 'right'), engines):
        if engine:
            prefix, _ = _engine_parts(engine)
            estimate = _audit_estimate([record['rel_path'] for record in records], total_tokens, args.focus,
                                       backends[prefix].get('windows_interop', False))
            profiles[side] = _planned_profile(args, backends, engine, tier, tier_reason, args.focus, 'audit', estimate['estimated_tokens'])
    plan = select_audit_plan(total_tokens, records, backends, args.strategy, not args.single, args.left, args.right,
                             args.shard_tokens, args.dry_run, caller, focus=args.focus, profiles=profiles, engines=engines)
    plan['skipped_files'].extend(skipped)
    plan.update(requested_tier=args.tier, selected_tier=tier, tier_reason=tier_reason)
    if args.dry_run:
        return _print({'status': 'ok', 'dry_run': True, 'plan': plan})
    for profile in profiles.values():
        _require_execution_profile(profile)
    base = _git_head(root)
    before = snapshot_files(root)
    tasks = [(side, unit) for side in ('left', 'right') for unit in plan[side + '_units']]
    findings = {'left': [], 'right': []}
    errors, completed = [], []
    max_workers_cfg = _positive_int(_headless_tunables().get('audit_max_workers')) or 4
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers_cfg, max(1, len(tasks)))) as pool:
        futures = {}
        for side, unit in tasks:
            profile = profiles[side]
            receipt = {}
            future = pool.submit(invoke_readonly_engine, profile['spec'], root, _build_audit_prompt(unit, args.focus),
                                 backends, args.timeout, unit['files'], profile['cli_effort'], args.retry_quota,
                                 profile=profile, batch=batch, receipt=receipt)
            futures[future] = side, unit, receipt
        for future in concurrent.futures.as_completed(futures):
            side, unit, receipt = futures[future]
            profile = profiles[side]
            engine = plan[side + '_engine']
            common = {'side': side, 'engine': engine, 'model': profile['model'],
                      'reasoning_effort': profile['reasoning_effort'], 'shard_id': unit['shard_id'],
                      'planned_files': unit['files'], 'invocation': receipt}
            try:
                result = future.result()
                _validate_schema(result, AUDIT_JSON_SCHEMA)
                planned = set(unit['files'])
                read = [_normalize_rel_file(path, root) for path in result['read_files']]
                read_paths = set(read)
                unread = [{**item, 'file': _normalize_rel_file(item['file'], root)} for item in result['unread_files']]
                unread_paths = {item['file'] for item in unread}
                if (read_paths | unread_paths) - planned or read_paths & unread_paths:
                    raise OutputParseError('读取覆盖回执与本分片文件范围不一致')
                if len(read_paths) != len(read) or len(unread_paths) != len(unread):
                    raise OutputParseError('读取覆盖回执包含重复路径')
                normalized = [{**item, 'file': _normalize_rel_file(item['file'], root)} for item in result['findings']]
                if {item['file'] for item in normalized} - planned:
                    raise OutputParseError('发现项引用了分片清单之外的文件')
                if any(item['file'] not in read_paths for item in normalized):
                    raise OutputParseError('发现项没有相应的自报已读文件')
                for missing in sorted(planned - read_paths - unread_paths):
                    unread.append({'file': missing, 'reason': 'backend_did_not_report_reading'})
                findings[side].extend(normalized)
                completed.append({**common, 'read_files': read, 'unread_files': unread,
                                  'status': 'partial' if unread else 'ok',
                                  'reading_coverage_verified': False, 'summary': result['summary']})
                emit_event('audit_unit_completed', engine=engine, shard_id=unit['shard_id'])
            except Exception as exc:
                errors.append({**common, 'status': getattr(exc, 'status', 'failed'),
                               'error': str(exc), **getattr(exc, 'details', {})})
    scope = _safe_changes_report(before, root, [], True)
    quota_err = next((err for err in errors if err.get('status') == 'quota_exhausted'), None)
    if scope['scope_violations']:
        status = 'scope_violation'
    elif _CANCELLED.is_set():
        status = 'cancelled'
    elif not tasks:
        status = 'partial'
    elif not completed:
        statuses = {error['status'] for error in errors}
        status = 'quota_exhausted' if 'quota_exhausted' in statuses else (next(iter(statuses)) if len(statuses) == 1 else 'failed')
    else:
        status = 'partial' if errors or plan['skipped_files'] or any(unit['unread_files'] for unit in completed) else 'ok'
    scope_error = {}
    if not scope['scope_verified']:
        if status not in ('quota_exhausted', 'cancelled', 'scope_violation'):
            status = 'failed'
        scope_error['error'] = scope['scope_error']
    quota_meta = {k: quota_err[k] for k in ('quota_backend', 'quota_execution_side', 'quota_cooldown',
                                          'quota_fuse_triggered', 'user_confirmation_required', 'user_choices')
                  if quota_err and k in quota_err}
    completed.sort(key=lambda unit: (unit['side'], unit['shard_id']))
    errors.sort(key=lambda unit: (unit['side'], unit['shard_id']))
    merged = merge_blind_findings(findings['left'], findings['right'], plan['left_engine'], plan['right_engine'], root)
    return _print({'status': status, **meta, 'plan': plan, 'base_commit': base,
                   'completed_units': completed, 'completed_count': sum(unit['status'] == 'ok' for unit in completed),
                   'received_count': len(completed), 'total_units': len(tasks), 'errors': errors,
                   'reading_coverage_verified': False,
                   'consensus_count': len(merged['consensus_findings']), 'single_source_count': len(merged['single_source_findings']),
                   **merged, **scope, **scope_error, **quota_meta,
                   'verification_notice': '当前总控需核实原始文件与证据后采纳发现。'})


def cmd_gate(args):
    if not str(args.verify_cmd).strip():
        raise HarnessError('--verify-cmd 不能为空白命令')
    requested_target, root = _target(args)
    text = _task_text(args)
    backends = _args_backends(args)
    caller = resolve_caller(args.caller)
    engine = choose_engine(backends, caller, args.worker, text)
    scopes = _scope_patterns(args.write_scope)
    if requested_target.is_file() and not scopes:
        scopes = [requested_target.relative_to(root).as_posix()]
    file_count, est_tokens = _target_metrics(requested_target, text)
    tier, tier_reason = select_task_tier(args.tier, 'gate', text, file_count, est_tokens, engine=args.worker or engine)
    batch = _QuotaBatch(args.retry_quota)
    meta = {'caller': caller, 'handoff_to_caller': True, 'engine': engine, 'worker_engine': engine,
            'requested_tier': args.tier, 'selected_tier': tier, 'tier_reason': tier_reason,
            'target': str(root), 'requested_target': str(requested_target), 'verification_executed': False}
    fallback_before = snapshot_files(root) if getattr(args, 'fallback_on_quota', False) and not args.dry_run else None
    try:
        _prepare_execution(backends, (engine,), batch, dry_run=args.dry_run)
    except HarnessError as exc:
        fb_engine = _local_fallback_engine(caller, engine, backends, text, clean_room=False) if (
            exc.status == 'quota_exhausted' and getattr(args, 'fallback_on_quota', False)
            and _workspace_unchanged(fallback_before, root)
        ) else None
        if not fb_engine:
            return _print({'status': exc.status, **meta, 'error': str(exc), **exc.details})
        meta['quota_fallback'] = _quota_fallback_record(engine, fb_engine, exc.details)
        engine = fb_engine
        meta.update(engine=engine, worker_engine=engine)
        try:
            _prepare_execution(backends, (engine,), batch, dry_run=args.dry_run)
            if not _workspace_unchanged(fallback_before, root):
                raise HarnessError('回退预检后工作区发生变化或无法核对，已停止执行')
        except HarnessError as fb_exc:
            return _print({'status': fb_exc.status, **meta, 'error': str(fb_exc), **fb_exc.details})
    profile = _planned_profile(args, backends, engine, tier, tier_reason, text, 'gate', est_tokens)
    if args.dry_run:
        prefix, _ = _engine_parts(engine)
        return _print({'status': 'ok', 'dry_run': True, **meta, **_profile_payload(profile),
                       'quota_cooldown': backends.get(prefix, {}).get('quota_cooldown'),
                       'work_dir': str(root), 'verify_cmd': args.verify_cmd, 'write_scope': scopes, 'max_rounds': args.max_rounds})
    _require_execution_profile(profile)
    base = _git_head(root)
    try:
        result = _run_gate_with_fallback(args, root, requested_target, text, caller, engine, backends,
                                         scopes, profile, 'gate', est_tokens, batch, meta, fallback_before)
    except HarnessError as exc:
        return _print({'status': exc.status, **meta, 'base_commit': base, 'error': str(exc), **exc.details})
    return _print({**meta, **result, 'base_commit': base})


def cmd_task(args):
    if args.kind == 'code' and args.verify_cmd is not None and not str(args.verify_cmd).strip():
        raise HarnessError('--verify-cmd 不能为空白命令')
    requested_target, root = _target(args)
    text = _task_text(args)
    caller = resolve_caller(args.caller)
    backends = _args_backends(args)
    engine = choose_engine(backends, caller, args.engine, text)
    scopes = _scope_patterns(args.write_scope)
    readonly = args.kind != 'code'
    if readonly and (scopes or args.verify_cmd):
        raise HarnessError('只读任务不可声明写入范围或验收命令')
    clean_room = bool(getattr(args, 'clean_room', False))
    if clean_room and not readonly:
        raise HarnessError('--clean-room 仅支持只读任务（review/direction/polish/math），写入任务不可剥离安全钩子')
    if clean_room and engine.startswith('agy'):
        raise HarnessError('agy 后端不支持剥离全局插件目录，--clean-room 请显式指定 codex、cc 或 dsh')
    rel_single_file = requested_target.relative_to(root).as_posix() if requested_target.is_file() else None
    if not readonly and rel_single_file and not scopes:
        scopes = [rel_single_file]
    base = _git_head(root)
    if args.base_commit:
        expected = _local_command(['git', '-C', str(root), 'rev-parse', args.base_commit + '^{commit}'])
        if base != expected:
            raise HarnessError('当前 HEAD 与 --base-commit 不一致')
    file_count, est_tokens = _target_metrics(requested_target, text)
    tier, tier_reason = select_task_tier(args.tier, args.kind, text, file_count, est_tokens, engine=args.engine or engine)
    batch = _QuotaBatch(args.retry_quota)
    payload = {'kind': args.kind, 'caller': caller, 'handoff_to_caller': True, 'engine': engine,
               'requested_tier': args.tier, 'selected_tier': tier, 'tier_reason': tier_reason,
               'target': str(root), 'requested_target': str(requested_target), 'base_commit': base,
               'readonly': readonly, 'write_scope': scopes}
    if not readonly:
        payload['verification_executed'] = False
    fallback_before = snapshot_files(root) if getattr(args, 'fallback_on_quota', False) and not args.dry_run else None
    try:
        _prepare_execution(backends, (engine,), batch, dry_run=args.dry_run)
    except HarnessError as exc:
        fb_engine = _local_fallback_engine(caller, engine, backends, text, clean_room=clean_room) if (
            exc.status == 'quota_exhausted' and getattr(args, 'fallback_on_quota', False)
            and _workspace_unchanged(fallback_before, root)
        ) else None
        if not fb_engine:
            return _print({'status': exc.status, **payload, 'result': {}, 'changed_files': [], 'scope_violations': [],
                           'error': str(exc), **exc.details})
        payload['quota_fallback'] = _quota_fallback_record(engine, fb_engine, exc.details)
        engine = fb_engine
        payload['engine'] = engine
        try:
            _prepare_execution(backends, (engine,), batch, dry_run=args.dry_run)
            if not _workspace_unchanged(fallback_before, root):
                raise HarnessError('回退预检后工作区发生变化或无法核对，已停止执行')
        except HarnessError as fb_exc:
            return _print({'status': fb_exc.status, **payload, 'result': {}, 'changed_files': [], 'scope_violations': [],
                           'error': str(fb_exc), **fb_exc.details})
    profile = _planned_profile(args, backends, engine, tier, tier_reason, text, args.kind, est_tokens)
    prefix, _ = _engine_parts(engine)
    payload.update(_profile_payload(profile))
    if args.dry_run:
        return _print({'status': 'ok', 'dry_run': True, 'quota_cooldown': backends.get(prefix, {}).get('quota_cooldown'), **payload})
    _require_execution_profile(profile)
    if args.kind == 'code' and args.verify_cmd:
        try:
            result = _run_gate_with_fallback(args, root, requested_target, text, caller, engine, backends,
                                             scopes, profile, args.kind, est_tokens, batch, payload, fallback_before)
        except HarnessError as exc:
            return _print({'status': exc.status, **payload, 'result': {}, 'changed_files': [],
                           'scope_violations': [], 'error': str(exc), **exc.details})
        return _print({**payload, **result})
    if not readonly:
        payload['verification_notice'] = '未提供 --verify-cmd，本次代码修改未经宿主机命令实测验证，请总控运行验证后再验收。'
    before = snapshot_files(root)
    rel_hint = f'（相对路径: {rel_single_file}）' if rel_single_file else ''
    stage_files = [rel_single_file] if (readonly and rel_single_file) else None
    polish_guard = (
        '【润色防幻觉硬约束】严禁新增原稿与任务说明中不存在的陌生学科术语、虚构算例名称（如未在代码中出现的领域词）、'
        '未声明的命令行参数、文件名或无依据的定量数字；仅优化表达清晰度与逻辑结构。\n'
        if args.kind == 'polish' else ''
    )
    prompt = (f'任务类型 {args.kind}。当前总控 {caller}。工作目录为当前目录。任务目标 {requested_target}{rel_hint}。\n'
              + ('只读完成任务，正文通过回执返回。' if readonly else f"允许写入范围 {scopes or '调用方授权的目标工作目录'}。")
              + '严禁在目标工作目录外创建、修改或删除任何文件或目录（含严禁向目录外执行 git worktree add）。'
              + '不要调用本编排器，不要修改其他会话或全局配置。\n' + polish_guard + text
              + '\n按给定 JSON Schema 返回正文、证据及未验证事项。')
    result, error, status, invocation = {}, {}, 'ok', {}
    try:
        try:
            result = invoke_engine(profile['spec'], root, prompt, backends, TASK_JSON_SCHEMA,
                                   readonly, args.timeout, 'readonly-collaborator', stage_files, profile['cli_effort'], args.retry_quota,
                                   profile=profile, batch=batch, receipt=invocation, clean_room=clean_room)
        except HarnessError as exc:
            fb_engine = _local_fallback_engine(caller, engine, backends, text, clean_room=clean_room) if (
                getattr(exc, 'status', None) == 'quota_exhausted'
                and getattr(args, 'fallback_on_quota', False)
                and 'quota_fallback' not in payload
                and _quota_can_replay(exc.details, invocation)
                and _workspace_unchanged(before, root)
                and _workspace_unchanged(fallback_before, root)
            ) else None
            if not fb_engine or (clean_room and fb_engine.startswith('agy')):
                raise
            payload['quota_fallback'] = _quota_fallback_record(engine, fb_engine, exc.details)
            _prepare_execution(backends, (fb_engine,), batch, dry_run=False)
            if not _workspace_unchanged(before, root):
                payload.pop('quota_fallback')
                raise
            fb_profile = _planned_profile(args, backends, fb_engine, tier, tier_reason, text, args.kind, est_tokens)
            _require_execution_profile(fb_profile)
            engine = fb_engine
            prefix, _ = _engine_parts(engine)
            payload['engine'] = engine
            payload.update(_profile_payload(fb_profile))
            invocation = {}
            result = invoke_engine(fb_profile['spec'], root, prompt, backends, TASK_JSON_SCHEMA,
                                   readonly, args.timeout, 'readonly-collaborator', stage_files, fb_profile['cli_effort'], args.retry_quota,
                                   profile=fb_profile, batch=batch, receipt=invocation, clean_room=clean_room)
    except Exception as exc:
        status = getattr(exc, 'status', 'failed')
        error = {'error': str(exc), **getattr(exc, 'details', {})}
    finally:
        staged_scope = invocation.get('staged_scope') if (readonly and invocation.get('staged_readonly_workspace')) else None
        scope = dict(staged_scope) if isinstance(staged_scope, dict) else _safe_changes_report(before, root, scopes, readonly)
        invocation.pop('staged_scope', None)
    payload['quota_status'] = backends.get(prefix, {}).get('quota_status', payload['quota_status'])
    if not scope.get('scope_verified', True) and status == 'ok':
        status = 'failed'
        error.setdefault('error', scope.get('scope_error') or '工作区快照核对失败')
    elif scope['scope_violations']:
        status = 'scope_violation'
    if args.kind == 'polish' and status == 'ok' and isinstance(result, dict) and isinstance(result.get('content'), str):
        orig_doc = _optional_text(requested_target) if requested_target.is_file() else ''
        rel_label = rel_single_file or requested_target.name
        anchor = _build_grounding_anchor(root, {rel_label} if rel_single_file else set(), extra_allowed_text=text)
        drift_hits = _audit_single_doc_text(rel_label, result['content'], anchor, baseline_text=orig_doc)
        payload['grounding_audit'] = {
            'ok': not drift_hits,
            'finding_count': len(drift_hits),
            'findings': drift_hits,
        }
    return _print({'status': status, **payload, 'invocation': invocation, 'result': result, **error, **scope})


def _positive_arg(raw):
    value = _positive_int(raw)
    if value is None:
        raise argparse.ArgumentTypeError('必须为正整数')
    return value


def build_arg_parser():
    hd = _headless_tunables()
    default_shard = int(hd.get('default_shard_tokens') or 120_000)
    default_rounds = int(hd.get('default_gate_max_rounds') or 3)
    default_timeout = int(hd.get('default_timeout_sec') or 600)
    parser = argparse.ArgumentParser(description='Antigravity / Codex / Claude Code / DeepSeek Harness 对等任务与审计编排')
    sub = parser.add_subparsers(dest='subcommand', required=True)
    probe = sub.add_parser('probe', help='只读探测程序与配置，认证和执行能力分别验证')
    probe.add_argument('--discover-models', action='store_true', help='显式执行 agy models 查询并刷新本地模型目录缓存')
    assess = sub.add_parser('assess', help='只读评估程序、目录元数据和任务选路')
    assess.add_argument('--discover-models', action='store_true', help='显式执行 agy models 查询，结果仅用于本次评估')
    grounding = sub.add_parser('check-grounding', help='文档与重写润色阶段的零黑名单确定性事实锚定核验')
    grounding.add_argument('--target', default='.', help='待核验的文档文件或目录路径')
    grounding.add_argument('--root', default='', help='锚点扫描根目录（省略时按工作区类型自动推断）')
    grounding.add_argument('--base-ref', default='', help='可选 Git 基线引用（如 HEAD），额外拦截改写期间新增的无依据领域短语')
    grounding.add_argument('--allow-from-file', action='append', default=[], help='额外纳入允许锚点语料的说明或需求文件路径')
    grounding.add_argument('--strict', action='store_true', help='检出任何无依据标识符、缩写、定量数字或举例词干时返回非零退出码')
    audit = sub.add_parser('audit', help='单路或双路文件审计')
    audit.add_argument('--target', required=True)
    audit.add_argument('--focus', default='')
    audit.add_argument('--strategy', choices=['auto', 'single-pass', 'whole-1m', 'sharded'], default='auto')
    audit.add_argument('--single', action='store_true')
    audit.add_argument('--left', default='', help='左路引擎与模型规格（agy、codex、claude/cc、dsh；支持 role:*、<client>:<role>、精确 slug 或模糊别名）')
    audit.add_argument('--right', default='', help='右路引擎与模型规格（agy、codex、claude/cc、dsh；支持 role:*、<client>:<role>、精确 slug 或模糊别名）')
    audit.add_argument('--shard-tokens', type=_positive_arg, default=default_shard)
    gate = sub.add_parser('gate', help='修改后运行明确验收命令')
    gate.add_argument('--target', required=True)
    gate.add_argument('--worker', default='', help='执行引擎与模型规格（agy、codex、claude/cc、dsh；支持 role:*、<client>:<role>、精确 slug 或模糊别名）')
    gate.add_argument('--verify-cmd', required=True)
    task = sub.add_parser('task', help='通用审查、方向、润色、数学和代码任务')
    task.add_argument('--target', required=True)
    task.add_argument('--kind', required=True, choices=['review', 'direction', 'polish', 'math', 'code'])
    task.add_argument('--engine', default='', help='后端与模型规格（agy、codex、claude/cc、dsh；支持 role:*、<client>:<role>、精确 slug 或模糊别名）')
    task.add_argument('--base-commit')
    task.add_argument('--verify-cmd')
    task.add_argument('--clean-room', action='store_true',
                      help='纯净盲读模式（仅限 codex/cc/dsh 只读任务）：仅保留底层登录/API 凭据，剥离宿主机全局规则、钩子、全局技能及工作区自动加载的 AGENTS.md/CLAUDE.md')
    for command in (gate, task):
        group = command.add_mutually_exclusive_group(required=True)
        group.add_argument('--task')
        group.add_argument('--task-file')
        command.add_argument('--write-scope', action='append', default=[])
        command.add_argument('--max-rounds', type=_positive_arg, default=default_rounds)
    for command in (probe, assess, grounding, audit, gate, task):
        command.add_argument('--caller', choices=['auto', 'codex', 'antigravity', 'claude', 'cc', 'dsh'], default='auto')
        if command is not grounding:
            command.add_argument('--codex-input-budget', type=_positive_arg)
            command.add_argument('--agy-input-budget', type=_positive_arg)
            command.add_argument('--claude-input-budget', type=_positive_arg)
            command.add_argument('--dsh-input-budget', type=_positive_arg)
    for command in (audit, gate, task):
        command.add_argument('--tier', choices=list(TIER_CHOICES), default='auto',
                             help='挡位策略：auto 默认按角色槽位或单模型非委派最高挡选路（短润色用 medium、长润色用 high）；仅当用户要求降挡／省额度时切换分级自适应')
        command.add_argument('--retry-quota', action='store_true',
                             help='用户确认已充值或额度恢复后，忽略本地额度冷却记录重试发起')
        command.add_argument('--fallback-on-quota', action='store_true',
                              help='目标触发额度熔断且未修改工作区时，优先回退发起方，再选择其他已就绪且未冷却的端')
        command.add_argument('--timeout', type=_positive_arg, default=default_timeout)
        command.add_argument('--dry-run', action='store_true')
    return parser


def main():
    # 作为库被测试导入时不改宿主标准流；编码与取消状态只在 CLI 入口初始化。
    for name in ('stdin', 'stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    _CANCELLED.clear()
    with QUOTA_LOCK:
        _SESSION_QUOTA_BLOCKED.clear()
    args = build_arg_parser().parse_args()
    if os.environ.get('CAE_HARNESS_DEPTH') and args.subcommand not in ('probe', 'assess', 'check-grounding'):
        sys.exit(_print({'status': 'failed', 'error': '子任务再次调用本编排器会形成递归派工，已拒绝'}))
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _cancel_signal)
    try:
        handler = {
            'probe': cmd_probe,
            'assess': cmd_assess,
            'check-grounding': cmd_check_grounding,
            'audit': cmd_audit,
            'gate': cmd_gate,
            'task': cmd_task,
        }[args.subcommand]
        code = handler(args)
    except HarnessError as exc:
        code = _print({'status': exc.status, 'error': str(exc), 'caller': resolve_caller(args.caller), **exc.details})
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        code = _print({'status': 'failed', 'error': str(exc), 'caller': resolve_caller(args.caller)})
    sys.exit(code)


if __name__ == '__main__':
    main()
