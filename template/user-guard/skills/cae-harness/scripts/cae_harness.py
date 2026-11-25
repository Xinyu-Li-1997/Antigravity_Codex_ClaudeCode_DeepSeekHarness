#!/usr/bin/env python3


"""Antigravity、Codex、Claude Code 与 DeepSeek Harness 跨端任务派发与验收工具。"""


import argparse
import ast
import base64
import collections
import concurrent.futures
import contextlib
import copy
import datetime
import fnmatch
import hashlib
import ipaddress
import itertools
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
from urllib.request import ProxyHandler, build_opener, urlopen

_STDLIB_URLOPEN = urlopen


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


ISOLATION_CHOICES = ('full', 'no_skills', 'hooks_only', 'clean_room')


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
    '越权', '越界', '安全钩子', '权限', '数据丢失',
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

_NONINTERACTIVE_ENV = {'GIT_PAGER': 'cat', 'PAGER': 'cat', 'LESS': '-FRX', 'GCM_INTERACTIVE': 'never',
                       'HWLOC_COMPONENTS': '-gl'}

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
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(raw, dict) or raw.get('schema_version') != 1:
            raise ValueError('invalid fleet_profile schema_version')
        _FLEET_PROFILE_CACHE['key'] = key
        _FLEET_PROFILE_CACHE['data'] = raw
        return raw
    except (OSError, ValueError, RecursionError) as exc:
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
    stop_roots = {Path.home().resolve(), Path(tempfile.gettempdir()).resolve()}
    for candidate in (base, *base.parents):
        if candidate in stop_roots:
            break
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
    'hydrogen', 'carbon', 'argon', 'neon', 'oxygen', 'nitrogen', 'helium', 'methane', 'air', 'water', 'soot',
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
_AGENT_INSTRUCTION_FILENAMES = frozenset({
    'AGENTS.MD', 'AGENTS.OVERRIDE.MD', 'CLAUDE.MD', 'CLAUDE.LOCAL.MD',
})
_STAGED_NEUTRAL_PREFIX = '_cae_readonly_target_'
_STAGED_NEUTRAL_SUFFIX = '.txt'


class HarnessError(RuntimeError):
    def __init__(self, message, status='failed', **details):
        super().__init__(message)
        self.status = status
        self.details = details


class OutputParseError(HarnessError):
    def __init__(self, message, **details):
        super().__init__(message, 'invalid_output', **details)


# ════════════════════════════════════════════════════════════════════════════
# ② 基础配置、跨平台路径与 WSL 互操作工具
# ════════════════════════════════════════════════════════════════════════════


def emit_event(event, **fields):
    with _EVENT_LOCK:
        print(json.dumps({'event': event, **fields}, ensure_ascii=False), file=sys.stderr, flush=True)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, RecursionError):
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


_GIT_REPO_ENV_VARS = (
    'GIT_DIR',
    'GIT_WORK_TREE',
    'GIT_INDEX_FILE',
    'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES',
    'GIT_COMMON_DIR',
    'GIT_CEILING_DIRECTORIES',
    'GIT_PREFIX',
)


def _clean_git_env(base_env=None):
    src_env = os.environ if base_env is None else base_env
    return {k: v for k, v in src_env.items() if k not in _GIT_REPO_ENV_VARS}


def _local_command(args, cwd=None):
    run_env = _clean_git_env() if (args and Path(str(args[0])).stem.lower() == 'git') else None
    try:
        proc = subprocess.run(args, cwd=cwd, env=run_env, capture_output=True, text=True,
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


_SESSION_ID_ENV_VARS = (
    'AGY_CONVERSATION_ID',
    'ANTIGRAVITY_CONVERSATION_ID',
    'CLAUDE_SESSION_ID',
    'CODEX_THREAD_ID',
    'DSH_SESSION_ID',
    'CAE_SESSION_ID',
)
_CHILD_SCRUB_SESSION_ENV_VARS = (
    *_SESSION_ID_ENV_VARS,
    'ANTIGRAVITY_AGENT',
    'ANTIGRAVITY_APP_DATA_DIR',
    'CODEX_CI',
    'CLAUDECODE',
    'CLAUDE_CODE_ENTRYPOINT',
    'DSH_PROFILE',
    'CAE_HARNESS_CALLER',
)


def resolve_caller(value):
    raw = str(value or '').strip().lower()
    if raw == 'auto':
        explicit_env = os.environ.get('CAE_HARNESS_CALLER', '').strip().lower()
        if explicit_env:
            raw = explicit_env
        elif any(os.environ.get(k, '').strip() for k in ('ANTIGRAVITY_AGENT', 'ANTIGRAVITY_CONVERSATION_ID', 'AGY_CONVERSATION_ID', 'ANTIGRAVITY_APP_DATA_DIR')):
            raw = 'antigravity'
        elif any(os.environ.get(k, '').strip() for k in ('CODEX_THREAD_ID', 'CODEX_CI')):
            raw = 'codex'
        elif any(os.environ.get(k, '').strip() for k in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_SESSION_ID')):
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


def _confirmation_tunables():
    return _fleet_profile().get('runtime_tunables', {}).get('dispatch_confirmation', {})


def _subscription_model(model_slug, prefix=None):
    key = re.sub(r'\[1m\]$', '', str(model_slug or '').strip(), flags=re.I).lower()
    if not key:
        return {}
    norm_prefix = ({'cc': 'claude', 'antigravity': 'agy'}.get(str(prefix).strip().lower(), str(prefix).strip().lower())
                   if prefix else None)
    models = _fleet_profile().get('subscription_matrix', {}).get('models', {})
    if not isinstance(models, dict):
        return {}

    def _lookup_key(target_key):
        fallback = {}
        for slug, record in models.items():
            if not isinstance(record, dict):
                continue
            if re.sub(r'\[1m\]$', '', str(slug), flags=re.I).lower() != target_key:
                continue
            if not norm_prefix:
                return record
            rec_agent_raw = str(record.get('agent') or '').strip().lower()
            rec_agent = {'cc': 'claude', 'antigravity': 'agy'}.get(rec_agent_raw, rec_agent_raw)
            if rec_agent == norm_prefix:
                return record
            if not rec_agent and not fallback:
                fallback = record
        return fallback

    exact = _lookup_key(key)
    if exact:
        return exact
    if norm_prefix == 'agy' or norm_prefix is None:
        family_base = _agy_slug_tier(key)[0]
        if family_base and family_base != key:
            fam_rec = _lookup_key(family_base)
            if fam_rec and (
                norm_prefix == 'agy'
                or {'cc': 'claude', 'antigravity': 'agy'}.get(
                    str(fam_rec.get('agent') or '').strip().lower(),
                    str(fam_rec.get('agent') or '').strip().lower(),
                ) == 'agy'
            ):
                return fam_rec
    return {}


def _is_subscription_enabled(prefix, model_slug=None):
    matrix = _fleet_profile().get('subscription_matrix', {})
    return (matrix.get('agents', {}).get(prefix, {}).get('enabled') is not False
            and _subscription_model(model_slug, prefix).get('enabled') is not False)


def _require_subscription(prefix, model_slug=None):
    if not _is_subscription_enabled(prefix, model_slug):
        raise HarnessError(f'{prefix}:{model_slug or "default"} 已在 fleet_profile.json 中停用',
                           'subscription_disabled')


def _is_lightweight(prefix, slug):
    return bool(_subscription_model(slug, prefix).get('lightweight')
                or _tokens_lower(slug) & _fast_model_tags(prefix))


def _recent_rollout_events(info):
    home = info.get('config_home')
    if not home:
        return
    count = _positive_int(_confirmation_tunables().get('codex_rollout_scan_files')) or 5
    try:
        paths = sorted((Path(home) / 'sessions').rglob('rollout-*.jsonl'),
                       key=lambda path: path.stat().st_mtime, reverse=True)[:count]
        events = []
        for path in paths:
            with path.open('rb') as stream:
                stream.seek(0, 2)
                start = max(0, stream.tell() - 256 * 1024)
                stream.seek(start)
                if start:
                    stream.readline()
                lines = stream.read().decode('utf-8', errors='replace').splitlines()
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        try:
                            stamp = datetime.datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
                            if stamp.tzinfo is None:
                                stamp = stamp.replace(tzinfo=datetime.timezone.utc)
                            stamp = stamp.timestamp()
                        except (KeyError, AttributeError, TypeError, ValueError):
                            stamp = path.stat().st_mtime
                        events.append((stamp, event))
                except ValueError:
                    continue
        for _, event in sorted(events, key=lambda item: item[0], reverse=True):
            yield event
    except OSError:
        return


def _loopback_router_alive(base_url):
    try:
        url = urlsplit(base_url)
        if url.hostname not in ('127.0.0.1', 'localhost', '::1') or not url.port:
            return False
        # The health request must never follow an external redirect.
        import http.client
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=1.5)
        try:
            connection.request('GET', '/__router/health')
            response = connection.getresponse()
            return response.status == 200 and json.loads(response.read()).get('ok') is True
        finally:
            connection.close()
    except (OSError, ValueError, AttributeError):
        return False


_DEFAULT_CLAUDE_ROUTER_ROUTES = [{'prefix': 'deepseek', 'key_from_card': 'DeepSeek'}]


def _load_claude_router_config(config_home, *, require_valid_file=False, interop=False):
    router_dir = Path(config_home or Path.home() / '.claude') / 'router'
    primary = router_dir / 'router.json'
    platform_cfg = router_dir / ('router.windows.json' if (os.name == 'nt' or interop) else 'router.wsl.json')
    cfg_path = primary if primary.exists() else (platform_cfg if platform_cfg.exists() else primary)
    if cfg_path.exists():
        try:
            if _is_link(cfg_path) or not cfg_path.is_file():
                raise ValueError('invalid router config file')
            raw = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            if require_valid_file:
                raise HarnessError('Claude Code 路由配置格式无效，请检查 router/router.json') from exc
            raw = None
    else:
        if require_valid_file:
            raise HarnessError('Claude Code 路由配置格式无效，请检查 router/router.json')
        raw = {}
    if raw is None:
        if require_valid_file:
            raise HarnessError('Claude Code 路由配置格式无效，请检查 router/router.json')
        raw = {}
    if not isinstance(raw, dict) or not isinstance(raw.get('routes', []), list):
        if require_valid_file:
            raise HarnessError('Claude Code 路由配置格式无效，请检查 router/router.json')
        return {'default_key_from_card': 'Kimi', 'routes': list(_DEFAULT_CLAUDE_ROUTER_ROUTES)}
    merged = {'default_key_from_card': 'Kimi', 'routes': list(_DEFAULT_CLAUDE_ROUTER_ROUTES)}
    merged.update(raw)
    return merged


def _resolve_claude_router_card(cfg, raw_model):
    model = str(raw_model or '').strip()
    claude_aliases = {str(k).lower(): str(v) for k, v in (_catalog_rules('claude').get('alias_map') or {}).items()}
    if model.lower() in claude_aliases:
        model = claude_aliases[model.lower()]
    card = cfg.get('default_key_from_card')
    for route in cfg.get('routes', []):
        if isinstance(route, dict) and route.get('prefix') and model.lower().startswith(str(route['prefix']).lower()):
            card = route.get('key_from_card') or card
            break
    return card


def _claude_card_quota(info, model_slug):
    interop = bool(info.get('windows_interop'))
    cfg = _load_claude_router_config(info.get('config_home'), interop=interop)
    raw_db = os.environ.get('CAE_CCSWITCH_DB') or cfg.get('ccswitch_db') or '~/.cc-switch/cc-switch.db'
    db_str = str(raw_db)
    try:
        if interop and not os.environ.get('CAE_CCSWITCH_DB') and info.get('config_home'):
            if db_str.startswith('~/'):
                db = Path(info['config_home']).parent / db_str[2:]
            elif ntpath.isabs(db_str) and not posixpath.isabs(db_str):
                db = Path(_wsl_path(Path(db_str), windows=False))
            else:
                db = Path(db_str).expanduser()
        else:
            db = Path(db_str).expanduser()
    except (OSError, RuntimeError, ValueError):
        return {}
    if not db.is_file():
        return {}
    card = None
    if info.get('uses_local_router') or info.get('llm_source') == 'local_router':
        card = _resolve_claude_router_card(cfg, model_slug or info.get('model') or '')
    try:
        with contextlib.closing(sqlite3.connect(db.absolute().as_uri() + '?mode=ro', uri=True, timeout=2)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM providers WHERE app_type='claude'").fetchall()
            row = next((dict(row) for row in rows if (row['name'] == card if card else row['is_current'] == 1)), None)
            if not row:
                return {}
            meta = json.loads(row.get('meta') or '{}')
            if not isinstance(meta, dict):
                meta = {}
            usage = meta.get('usage_script') or meta.get('usageScript')
            if not isinstance(usage, dict):
                usage = {}
            result = {'quota_scheme': usage.get('templateType') or 'ccswitch_card_dependent', 'quota_exceeded': False}
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'usage_daily_rollups' in tables:
                # Select only known cost/date columns; never evaluate metadata as SQL.
                columns = {r[1] for r in con.execute('PRAGMA table_info(usage_daily_rollups)')}
                date_col = next((c for c in ('date', 'day', 'usage_date') if c in columns), None)
                cost_col = next((c for c in ('total_cost_usd', 'cost_usd', 'total_cost') if c in columns), None)
                if date_col and cost_col and 'provider_id' in columns:
                    today = datetime.date.today().isoformat()
                    for period, prefix in (('daily', today), ('monthly', today[:7])):
                        limit = meta.get(f'limit_{period}_usd', row.get(f'limit_{period}_usd'))
                        try:
                            limit = float(limit or 0)
                        except (TypeError, ValueError):
                            continue
                        if limit > 0:
                            amount = con.execute(f'SELECT SUM("{cost_col}") FROM usage_daily_rollups '
                                                 f'WHERE provider_id=? AND "{date_col}" LIKE ?',
                                                 (row['id'], prefix + '%')).fetchone()[0] or 0
                            result['quota_exceeded'] |= float(amount) >= limit
                            result['quantitative_quota_known'] = True
            return result
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return {}


def inspect_backend_quota_and_availability(prefix, info, model_slug=None, redact_usage=False, *, ignore_cooldown=False):
    matrix = _fleet_profile().get('subscription_matrix', {})
    scheme = (_subscription_model(model_slug, prefix).get('quota_scheme')
              or matrix.get('agents', {}).get(prefix, {}).get('quota_scheme') or 'unknown')
    enabled = _is_subscription_enabled(prefix, model_slug)
    reason = None if enabled else 'subscription_disabled'
    exceeded, reset, percentages = False, None, []
    plan_type = None
    quantitative_known = False
    if prefix == 'codex':
        for event in _recent_rollout_events(info):
            payload = event.get('payload') or event
            rate = payload.get('rate_limits') or event.get('rate_limits')
            if not isinstance(rate, dict):
                continue
            plan_type = rate.get('plan_type')
            block = float(_confirmation_tunables().get('quota_block_percent', 100))
            any_expired_window = False
            any_active_window = False
            for name in ('primary', 'secondary'):
                window = rate.get(name)
                if not isinstance(window, dict):
                    continue
                try:
                    resets = float(window['resets_at']) if window.get('resets_at') is not None else None
                    if resets is not None and not math.isfinite(resets):
                        resets = None
                except (ValueError, TypeError):
                    resets = None
                expired = resets is not None and time.time() >= resets
                any_expired_window |= expired
                any_active_window |= not expired
                if not expired and resets is not None:
                    reset = min(reset or resets, resets)
                exceeded |= not expired and bool(window.get('rate_limit_reached_type')
                             or window.get('spend_control_reached'))
                try:
                    used = float(window['used_percent'])
                    if not math.isfinite(used) or used < 0:
                        continue
                    if expired:
                        used = 0.0
                except (KeyError, ValueError, TypeError):
                    continue
                label = {300: '5h_window', 10080: 'weekly_window'}.get(window.get('window_minutes'), name)
                percentages.append(f'{label}:{used:g}%')
                blocked = not expired and used >= block
                exceeded |= blocked
            top_flag = bool(rate.get('rate_limit_reached_type') or rate.get('spend_control_reached'))
            if top_flag and (not any_expired_window and (any_active_window or not percentages)):
                exceeded = True
            break
        quantitative_known = bool(percentages)
    elif prefix == 'claude':
        card = _claude_card_quota(info, model_slug)
        scheme = card.get('quota_scheme') or scheme
        exceeded = card.get('quota_exceeded', False)
        quantitative_known = card.get('quantitative_quota_known', False)
        if info.get('uses_local_router') or info.get('llm_source') == 'local_router':
            router_ok = _probe_router_health(info) if info.get('windows_interop') else _loopback_router_alive(info.get('base_url', ''))
            if not router_ok:
                reason = reason or 'local_router_unavailable'
    cooldown = None if ignore_cooldown else _active_quota_record(prefix, info)
    if cooldown:
        exceeded = True
        reset = cooldown.get('cooldown_until') or cooldown.get('cooldown_until_epoch') or cooldown.get('resets_at') or reset
    if exceeded:
        reason = reason or 'quota_exhausted'
    if not info.get('available'):
        reason = reason or 'backend_unavailable'
    if prefix in ('claude', 'dsh') and not info.get('llm_configured'):
        reason = reason or 'credentials_unconfigured'
    label = ('quota_exceeded' if exceeded else 'within_quota' if quantitative_known
             else 'no_local_cooldown (remaining_unknown)')
    summary = f'{scheme}:{label}'
    if percentages and not redact_usage:
        summary += ' ' + ', '.join(percentages)
    if reset is not None:
        summary += f' resets_at={reset}'
    result = {'currently_available': reason is None, 'unavailable_reason': reason,
              'subscription_enabled': enabled, 'quota_scheme': scheme, 'quota_exceeded': bool(exceeded),
              'quota_summary': summary, 'resets_at': reset}
    if plan_type is not None and not redact_usage:
        result['plan_type'] = plan_type
    if cooldown:
        result['cooldown_until'] = reset
    return result


def _peer_auto_ready(backends, name, *, ignore_cooldown=False, allow_lightweight=False):
    info = backends.get(name, {})
    if name == 'claude' and info.get('uses_local_router') is True and info.get('router_alive') is False:
        return False
    if (not allow_lightweight and name == 'claude' and info.get('llm_source') != 'local_router'
            and _is_lightweight('claude', info.get('model'))):
        return False
    return bool(_is_subscription_enabled(name, info.get('model'))
                and (ignore_cooldown or not _active_quota_record(name, info))
                and info.get('available') and (name not in ('claude', 'dsh') or info.get('llm_configured')))


def _resolve_role_engine(backends, caller, role_spec, prompt_text='', *, ignore_cooldown=False):
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
        if cand_prefix not in negated and _is_subscription_enabled(cand_prefix):
            try:
                candidate_profile = resolve_execution_profile(backends, raw_cand)
                if (cand_prefix == 'claude' and _per_backend_role_target(cand_prefix, _engine_parts(raw_cand)[1]) != 'fast'
                        and (_is_lightweight('claude', candidate_profile.get('model'))
                             or not _is_subscription_enabled(cand_prefix, candidate_profile.get('model')))):
                    continue
            except HarnessError:
                continue
            parsed.append((cand_prefix, raw_cand))
    if not parsed:
        raise HarnessError(f'提示词已排除角色 {role_name} 的全部候选后端，请显式指定 --engine/--worker')
    ready_peers = [spec for prefix, spec in parsed if prefix != caller_backend
                   and _peer_auto_ready(backends, prefix, ignore_cooldown=ignore_cooldown, allow_lightweight=(role_name == 'fast'))]
    if ready_peers:
        return ready_peers[0]
    ready_any = [spec for prefix, spec in parsed if _peer_auto_ready(backends, prefix, ignore_cooldown=ignore_cooldown, allow_lightweight=(role_name == 'fast'))]
    if ready_any:
        return ready_any[0]
    avail_any = [spec for prefix, spec in parsed if backends.get(prefix, {}).get('available')
                 and (ignore_cooldown or not _active_quota_record(prefix, backends[prefix]))]
    if avail_any:
        return avail_any[0]
    if not ignore_cooldown:
        cooled_pair = next(
            ((p, _active_quota_record(p, backends.get(p, {}))) for p, _ in parsed
             if backends.get(p, {}).get('available') and _active_quota_record(p, backends.get(p, {}))),
            None,
        ) or next(
            ((p, _active_quota_record(p, backends.get(p, {}))) for p, _ in parsed
             if _active_quota_record(p, backends.get(p, {}))),
            None,
        )
        if cooled_pair:
            cooled_p, cooled_rec = cooled_pair
            raise HarnessError('角色候选均不可用或处于配额冷却', 'quota_exhausted', preflight_blocked=True,
                               **_quota_user_choices(cooled_p, cooled_rec))
    raise HarnessError(f'未发现角色 {role_name} 可用的已安装后端，请先配置对应客户端')


def choose_engine(backends, caller, explicit='', prompt_text='', user_prompt='', *, ignore_cooldown=False):
    if user_prompt:
        hinted = _prompt_engine_spec(backends, user_prompt)
        if hinted:
            original = (explicit or '').strip().lower()
            slot = (original.split(':', 1)[1] if original.startswith('role:')
                    else _per_backend_role_target(*_engine_parts(original)) if original else '')
            backend = _extract_prompt_backend_hint(user_prompt)
            if backend and not _extract_prompt_model_hint(user_prompt):
                if slot:
                    hinted = f'{backend}:@{slot}'
                elif explicit and not original.startswith('role:'):
                    orig_prefix, orig_model = _engine_parts(explicit)
                    if orig_prefix == backend and orig_model and orig_model.lower() != 'default' and not orig_model.startswith('@'):
                        hinted = f'{orig_prefix}:{orig_model}'
            explicit = hinted
        prompt_text = user_prompt
    if explicit and explicit.strip().lower().startswith('role:'):
        explicit = _resolve_role_engine(backends, caller, explicit.strip(), prompt_text, ignore_cooldown=ignore_cooldown)
        name, _ = _engine_parts(explicit)
    elif explicit:
        name, model = _engine_parts(explicit)
        explicit = name + ':' + (model or 'default')
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
        allowed = [peer for peer in order if peer not in negated and _is_subscription_enabled(peer, backends.get(peer, {}).get('model'))
                   and (ignore_cooldown or not _active_quota_record(peer, backends.get(peer, {})))]
        if not allowed:
            cooled = next((peer for peer in order if peer not in negated and _is_subscription_enabled(peer)
                           and _active_quota_record(peer, backends.get(peer, {}))), None)
            if cooled:
                raise HarnessError('候选客户端处于配额冷却', 'quota_exhausted', preflight_blocked=True,
                                   **_quota_user_choices(cooled, _active_quota_record(cooled, backends[cooled])))
            raise HarnessError('提示词已排除或停用全部候选后端，请显式指定 --engine/--worker')
        ready = next((peer for peer in allowed if _peer_auto_ready(backends, peer, ignore_cooldown=ignore_cooldown)), None)
        if not ready and not ignore_cooldown:
            cooled = next((peer for peer in order if peer not in negated and _is_subscription_enabled(peer)
                           and backends.get(peer, {}).get('available') and _active_quota_record(peer, backends[peer])), None)
            if cooled:
                cooldown = _active_quota_record(cooled, backends[cooled])
                raise HarnessError('候选客户端处于配额冷却', 'quota_exhausted', preflight_blocked=True,
                                   **_quota_user_choices(cooled, cooldown))
        if not ready:
            caller_backend = {'antigravity': 'agy', 'codex': 'codex', 'claude': 'claude', 'dsh': 'dsh'}.get(resolve_caller(caller))
            if (
                caller_backend
                and caller_backend not in negated
                and _is_subscription_enabled(caller_backend, backends.get(caller_backend, {}).get('model'))
                and (ignore_cooldown or not _active_quota_record(caller_backend, backends.get(caller_backend, {})))
                and _peer_auto_ready(backends, caller_backend, ignore_cooldown=ignore_cooldown)
            ):
                ready = caller_backend
        name = ready or allowed[0]
        explicit = name + ':default'
    _require_subscription(name, _engine_parts(explicit)[1])
    if not backends.get(name, {}).get('available'):
        raise HarnessError(f'未发现所选后端 {name}，请配置对应运行环境')
    return explicit


_WIN_LINK_REPARSE_TAGS = frozenset({
    getattr(stat, 'IO_REPARSE_TAG_SYMLINK', 0xA000000C),
    getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', 0xA0000003),
})


def _is_link(path, *, st=None):
    st = path.lstat() if st is None else st
    if stat.S_ISLNK(st.st_mode):
        return True
    attributes = getattr(st, 'st_file_attributes', 0) or 0
    if bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
        tag = getattr(st, 'st_reparse_tag', None)
        if not isinstance(tag, int):
            return True
        return tag == 0 or tag in _WIN_LINK_REPARSE_TAGS
    return False


def _ps_literal(text):
    return "'" + text.replace("'", "''") + "'"


_CLEAN_ROOM_ONLY_INSTR_ERROR = (
    '净室目录模式下目标范围仅含代理指令文件（AGENTS.md/CLAUDE.md 等）且已被自动剥离；'
    '若需审查该指令文件本身，请将 --target 直接指向具体单文件'
)


def _stage_wsl_readonly_workspace(target_dir, stage_root, rel_files=None, *, exclude_agent_instructions=False):
    """将只读目标文件镜像到临时目录；清单不能逃出原目标树。"""
    target_dir = target_dir.resolve()
    stage_dir = stage_root / 'workspace'
    stage_dir.mkdir(parents=True, exist_ok=True)
    if rel_files is None:
        raw_records = collect_target_files(target_dir)
        if exclude_agent_instructions:
            records = [
                item for item in raw_records
                if Path(item['rel_path']).name.upper() not in _AGENT_INSTRUCTION_FILENAMES
            ]
            if raw_records and not records:
                raise HarnessError(_CLEAN_ROOM_ONLY_INSTR_ERROR)
        else:
            records = raw_records
    else:
        norm_rels = [_literal_input_path(rel) for rel in rel_files]
        records = []
        explicit_single = len(norm_rels) == 1
        for rel_norm in norm_rels:
            is_instr = posixpath.basename(rel_norm).upper() in _AGENT_INSTRUCTION_FILENAMES
            if exclude_agent_instructions and is_instr and not explicit_single:
                continue
            src = target_dir / rel_norm
            staged_rel = (
                posixpath.join(
                    posixpath.dirname(rel_norm),
                    f'{_STAGED_NEUTRAL_PREFIX}{posixpath.basename(rel_norm)}{_STAGED_NEUTRAL_SUFFIX}',
                )
                if (exclude_agent_instructions and is_instr and explicit_single) else rel_norm
            )
            try:
                src.resolve().relative_to(target_dir)
                # 叶文件正常也可能经过目录链接，逐级检查阻止镜像树外材料。
                ancestors = [src] + list(src.parents)[:len(src.relative_to(target_dir).parts) - 1]
                if any(_is_link(path) for path in ancestors) or not src.is_file():
                    raise HarnessError(f'只读镜像不接受文件或目录链接: {rel_norm}')
                records.append({'path': src, 'rel_path': rel_norm, 'staged_rel_path': staged_rel, 'bytes': src.stat().st_size})
            except (OSError, ValueError) as exc:
                raise HarnessError(f'无法在目标目录内镜像文件: {rel_norm}') from exc
        if exclude_agent_instructions and norm_rels and not records:
            raise HarnessError(_CLEAN_ROOM_ONLY_INSTR_ERROR)
    stage_limit = _max_interop_stage_bytes()
    if sum(item['bytes'] for item in records) > stage_limit:
        limit_mib = max(1, stage_limit // (1024 * 1024))
        raise HarnessError(f'WSL 纯 Linux 路径只读镜像超出 {limit_mib}MB 上限，请缩小 --target 范围或在 WSL 安装原生 codex')
    windows_names = {}
    for item in records:
        effective_rel = item.get('staged_rel_path', item['rel_path'])
        parts = Path(effective_rel).parts
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
        dst = stage_dir / item.get('staged_rel_path', item['rel_path'])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item['path'], dst)
    return stage_dir


def _rewrite_staged_paths(parsed, stage_dir):
    # Windows 可改变回执中的大小写；使用镜像里保留的原拼写还原 ext4 路径。
    original_paths = {}
    neutral_map = {}
    neutral_basename_targets = collections.defaultdict(list)
    for directory, _dirs, files in os.walk(stage_dir):
        rel_dir = Path(directory).relative_to(stage_dir)
        for name in files:
            relative = (rel_dir / name).as_posix()
            if name.startswith(_STAGED_NEUTRAL_PREFIX) and name.endswith(_STAGED_NEUTRAL_SUFFIX):
                orig_name = name[len(_STAGED_NEUTRAL_PREFIX):-len(_STAGED_NEUTRAL_SUFFIX)]
                orig_rel = (rel_dir / orig_name).as_posix()
                original_paths[relative.casefold()] = orig_rel
                original_paths[orig_rel.casefold()] = orig_rel
                neutral_map[relative] = orig_rel
                neutral_basename_targets[name.casefold()].append((name, orig_rel))
            else:
                original_paths[relative.casefold()] = relative

    unique_neutral_basenames = {}
    for base_key, entries in neutral_basename_targets.items():
        if len(entries) == 1:
            base_name, orig_rel = entries[0]
            original_paths.setdefault(base_key, orig_rel)
            unique_neutral_basenames[base_name] = orig_rel

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

    text_replacements = []
    if neutral_map:
        for staged_rel, orig_rel in sorted(neutral_map.items(), key=lambda kv: -len(kv[0])):
            staged_bs = staged_rel.replace('/', '\\')
            for prefix, _ in prefixes:
                prefix_bs = prefix.replace('/', '\\')
                text_replacements.append((prefix + staged_rel, orig_rel))
                if prefix_bs != prefix or staged_bs != staged_rel:
                    text_replacements.append((prefix_bs + staged_bs, orig_rel))
            text_replacements.append((staged_rel, orig_rel))
            if staged_bs != staged_rel:
                text_replacements.append((staged_bs, orig_rel))
        for base_name, orig_rel in sorted(unique_neutral_basenames.items(), key=lambda kv: -len(kv[0])):
            if base_name not in neutral_map:
                text_replacements.append((base_name, orig_rel))

    def _rewrite_text(val):
        if not isinstance(val, str) or not text_replacements:
            return val
        for pat_str, repl_str in text_replacements:
            val = re.sub(re.escape(pat_str), lambda _, r=repl_str: r, val, flags=re.I)
        return val

    if 'read_files' in parsed:
        parsed['read_files'] = [_clean(item) for item in parsed['read_files']]
    if 'unread_files' in parsed:
        parsed['unread_files'] = [
            {**item, 'file': _clean(item['file']),
             **({'reason': _rewrite_text(item['reason'])} if isinstance(item.get('reason'), str) else {})}
            for item in parsed['unread_files']
        ]
    if isinstance(parsed.get('summary'), str):
        parsed['summary'] = _rewrite_text(parsed['summary'])
    if isinstance(parsed.get('content'), str):
        parsed['content'] = _rewrite_text(parsed['content'])
    if isinstance(parsed.get('evidence'), list):
        parsed['evidence'] = [_rewrite_text(item) for item in parsed['evidence']]
    if isinstance(parsed.get('unverified'), list):
        parsed['unverified'] = [_rewrite_text(item) for item in parsed['unverified']]
    if 'findings' in parsed:
        parsed['findings'] = [
            {
                k: (_clean(v) if k == 'file' else _rewrite_text(v) if isinstance(v, str) else v)
                for k, v in item.items()
            }
            for item in parsed['findings']
        ]
    return parsed


def _interop_command(command, work_dir, temp_dir, input_text, codex_home=None, extra_env: dict[str, str] | None = None):
    """监督进程写入本次真实 Windows PID，供 WSL 定向清理进程树。"""
    pid_file = temp_dir / 'native.pid'
    pid_file.unlink(missing_ok=True)
    if input_text is None:
        writer = [
            '  while ($null -ne ($line = [Console]::ReadLine())) {',
            '    $p.StandardInput.WriteLine($line)',
            '    $p.StandardInput.Flush()', '  }',
        ]
    else:
        prompt_file = temp_dir / 'input.txt'
        prompt_file.write_text(input_text, encoding='utf-8')
        writer = [
            '$inputBytes = [IO.File]::ReadAllBytes(' + _ps_literal(_wsl_path(prompt_file)) + ')',
            '  $p.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)',
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
    for key, value in {**(extra_env or {}), **_NONINTERACTIVE_ENV}.items():
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
        '$output = $p.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())',
        '$errors = $p.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())',
        'try {', *writer, '  $p.StandardInput.Close()', '} catch [System.IO.IOException] {}',
        '$p.WaitForExit()', '$output.Wait()', '$errors.Wait()',
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


def _pick_agy_tagged_family(candidates, configured_tags):
    tags = [str(tag).strip().lower() for tag in (configured_tags or ()) if str(tag).strip()]
    for tag in tags:
        for name in candidates:
            if tag in _tokens_lower(name):
                return name
    return None


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
    text = re.sub(r'(?m)^\s*>[^\n]*|\x60{3}[\s\S]*?\x60{3}', ' ', str(text or ''))
    quoted = re.compile(r'`([^`\n]*)`|“([^”\n]*)”|‘([^’\n]*)’|"([^"\n]*)"|(?<![A-Za-z0-9_])\x27([^\x27\n]*)\x27(?![A-Za-z0-9_])')
    def replace(match):
        value = next((v for v in match.groups() if v is not None), '')
        action = re.search(r'(?:使用|调用|采用|切到|指定|选用|改用|交给|让|用|\buse|\binvoke|\bvia)\s*$', text[:match.start()], re.I)
        return value if action and re.fullmatch(r'[\w.:[\]@+/-]{1,48}', value) else ' '
    return quoted.sub(replace, text)


def _prompt_engine_spec(backends, text):
    hint = _extract_prompt_model_hint(text)
    backend = _extract_prompt_backend_hint(text)
    if hint:
        for prefix, info in ([(backend, backends.get(backend, {}))] if backend else backends.items()):
            aliases = {str(k).lower(): str(v) for k, v in (_catalog_rules(prefix).get('alias_map') or {}).items()}
            slug = aliases.get(hint.lower(), hint)
            if (_exact_model_record(info.get('models', []), slug)
                    or hint.lower() in aliases
                    or _subscription_model(slug, prefix).get('agent') == prefix):
                return prefix + ':' + slug
        if backend:
            return backend + ':' + hint
    return backend + ':default' if backend else ''


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
    major_split = re.compile(r'[，。；,;.!?！？\n]+|\b(?:but|however|instead)\b|但是|而是|而只', re.I)
    any_backend = '|'.join(f'(?P<b_{k}>{v})' for k, v in _BACKEND_HINT_NAMES.items())
    chain_step = re.compile(r'^\s*(?:、|/|和|与|及|或|\band\b|\bor\b)\s*(?:' + any_backend + r')(?![A-Za-z0-9_.=-])', re.I)
    only_head = re.compile(
        r'(?:只|仅)(?:使用|调用|采用|选用|用|限(?:使用|调用|用)?)\s*(?:' + any_backend + r')(?![A-Za-z0-9_.=-])',
        re.I,
    )

    def _matched_backend(m):
        return next((k[2:] for k, v in m.groupdict().items() if k.startswith('b_') and v is not None), '')

    for sentence in major_split.split(text):
        for name, pattern in _BACKEND_HINT_NAMES.items():
            for match in re.finditer(r'(?<![A-Za-z0-9_.=-])' + pattern + r'(?![A-Za-z0-9_.=-])', sentence, re.I):
                sub_prefix = _CUE_BOUNDARIES.split(sentence[:match.start()])[-1]
                if _cue_is_negated(sub_prefix):
                    negated.add(name)
                    pos = match.end()
                    while True:
                        nxt = chain_step.match(sentence[pos:])
                        if not nxt:
                            break
                        b_name = _matched_backend(nxt)
                        if b_name:
                            negated.add(b_name)
                        pos += nxt.end()
        for match in only_head.finditer(sentence):
            sub_prefix = _CUE_BOUNDARIES.split(sentence[:match.start()])[-1]
            if _cue_is_negated(sub_prefix):
                continue
            allowed = {_matched_backend(match)} - {''}
            pos = match.end()
            while True:
                nxt = chain_step.match(sentence[pos:])
                if not nxt:
                    break
                b_name = _matched_backend(nxt)
                if b_name:
                    allowed.add(b_name)
                pos += nxt.end()
            if allowed:
                negated.update(set(_BACKEND_HINT_NAMES) - allowed)
    return negated


def _extract_prompt_backend_hint(prompt_text):
    text = _semantic_text(prompt_text)
    action = r'(?:请)?(?:使用|调用|采用|切到|切换至|指定|选用|改用|交给|让|用)\s*'
    english = r'\b(?:use|invoke|via|switch\s+to)\s+'
    candidates = []
    backend_names = '|'.join(_BACKEND_HINT_NAMES.values())
    parallel_tail = r'\s*(?:等|(?:和|与|及|、|/|或|\band\b|\bor\b)\s*(?:' + backend_names + r')(?![A-Za-z0-9_.=-]))'
    for name, pattern in _BACKEND_HINT_NAMES.items():
        for match in re.finditer('(?:' + action + '|' + english + ')' + pattern + r'(?![A-Za-z0-9_.=-])', text, re.I):
            if not _cue_is_negated(_CUE_BOUNDARIES.split(text[:match.start()])[-1]):
                if re.match(parallel_tail, text[match.end():], re.I):
                    return ''
                candidates.append((match.start(), name))
    return min(candidates)[1] if candidates else ''


def _extract_prompt_model_hint(prompt_text):
    text = _semantic_text(prompt_text).strip()
    if not text:
        return ''
    action = r'(?:使用|调用|采用|切到|指定|选用|改用|交给|让|用|\buse|\binvoke|\bvia)\s*'
    aliases = {str(key).lower() for prefix in ('codex', 'agy', 'claude', 'dsh')
               for key in (_catalog_rules(prefix).get('alias_map') or {})}
    known = {re.sub(r'\[1m\]$', '', str(slug), flags=re.I).lower()
             for slug in _fleet_profile().get('subscription_matrix', {}).get('models', {})}

    def is_client_name(candidate, tail):
        return bool(re.fullmatch(r'(?:claude\s*(?:code|cli)|deepseek\s*harness|cc(?:\s+cli)?|dsh(?:\s+cli)?)', candidate, re.I)
                    or (candidate.lower() == 'deepseek' and re.match(r'\s*harness\b', tail, re.I))
                    or (candidate.lower() == 'claude' and re.match(r'\s*(?:code|cli)\b', tail, re.I)))

    for match in re.finditer(action + r'([A-Za-z][A-Za-z0-9._\[\]@+-]{0,47})(?![A-Za-z0-9_.=-])', text, re.I):
        name = match.group(1)
        if is_client_name(name, text[match.end(1):]):
            continue
        if _cue_is_negated(_CUE_BOUNDARIES.split(text[:match.start()])[-1]):
            continue
        name_base = re.sub(r'\[1m\]$', '', name, flags=re.I).lower()
        if (name.lower() in aliases or name.lower() in known or name_base in known
                or re.fullmatch(r'(?:gpt|gemini|deepseek|kimi|k\d)[-\w.\[\]]*', name, re.I)):
            return name
    stop_words = NON_AI_MODEL_STOPWORDS - ROUTER_MODEL_TAGS
    for pattern in PROMPT_MODEL_HINT_PATTERNS:
        for match in pattern.finditer(text):
            prefix = _CUE_BOUNDARIES.split(text[:match.start()])[-1]
            if _cue_is_negated(prefix):
                continue
            cand = re.sub(r'\s+', ' ', match.group(1)).strip()
            if not cand:
                continue
            if is_client_name(cand, text[match.end(1):]):
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
        prefix, _, suffix = raw.partition(':')
        return suffix == '@fast' or (prefix in ('dsh', 'claude') and suffix == 'fast')
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
    if requested_tier is not None and not isinstance(requested_tier, str):
        raise HarnessError(f'无效的挡位参数: {requested_tier!r}')
    if requested_tier:
        if requested_tier not in TIER_CHOICES:
            raise HarnessError(f'无效的挡位参数: {requested_tier!r}')
        if requested_tier != 'auto':
            return requested_tier, 'explicit_tier_flag'
    sample = (text or '').strip()
    prose_requests = {
        'max': ('用最高挡', '用最高档', '选最高挡', '选最高档', '用最大挡', '用最大档', '使用 max 挡', 'use max effort'),
        'xhigh': ('用超高挡', '用超高档', '选超高挡', '选超高档', '使用 xhigh 挡', 'use xhigh effort'),
        'low': ('用低挡', '用低档', '选低挡', '选低档', '低挡快速', '低档快速', '降到低挡', '降到低档',
                '最低挡', '最低档', '使用 low 挡', 'use low effort'),
        'medium': ('用中挡', '用中档', '选中挡', '选中档', '降到中挡', '降到中档', '使用 medium 挡', 'use medium effort'),
        'high': ('高挡位', '高档位', '挂 high 挡', '用高挡', '用高档', '选高挡', '选高档', '降到高挡', '降到高档', '使用 high 挡', 'use high effort'),
    }
    for level in ('max', 'xhigh', 'low', 'medium', 'high'):
        if _has_positive_cue(sample, (level + ' 挡位', level + ' 档位', level + ' 挡', level + ' 档')):
            return level, 'user_description_' + level
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
            if kind == 'direction' and role_tier and role_tier != 'max':
                return role_tier, f'role_profile_{role_name}_{role_tier}'
            return 'max', f'task_specialty_{kind}_max' if kind in ('math', 'direction') else 'complexity_high_domain_max'
        return 'high', 'economy_adaptive_high'
    if role_tier and role_tier != 'max':
        if role_name == 'review' and _has_positive_cue(sample, HIGH_COMPLEXITY_CUES):
            return 'max', 'complexity_high_domain_max'
        return role_tier, f'role_profile_{role_name}_{role_tier}'
    if kind in ('math', 'direction'):
        return 'max', f'task_specialty_{kind}_max'
    if _has_positive_cue(sample, HIGH_COMPLEXITY_CUES):
        return 'max', 'complexity_high_domain_max'
    return 'max', 'default_flagship_max'


def _resolve_effective_tier(explicit_tier, kind, user_prompt, fallback_text, engine='', *, file_count=1, estimated_tokens=0):
    if user_prompt:
        tier, reason = select_task_tier('auto', kind, user_prompt, file_count, estimated_tokens, engine=engine)
        if reason.startswith(('user_description_', 'economy_')):
            return tier, reason
    return select_task_tier(explicit_tier, kind, user_prompt or fallback_text, file_count, estimated_tokens, engine=engine)


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
    if not (maximum or default) and isinstance(record, dict) and record.get('slug'):
        default = _positive_int(_subscription_model(record.get('slug'), prefix).get('context_window'))
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


def _select_codex_specialty_record(info, kind, tier, prompt_text, estimated_tokens, *, prefer_fast=False, allow_tier_fast=True,
                                  enforce_subscription=True):
    models = [item for item in info.get('models', []) if _model_category('codex', item) == 'native'
              and (not enforce_subscription or _is_subscription_enabled('codex', item['slug']))]
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


def resolve_execution_profile(backends, spec, tier='max', prompt_text='', kind='review', estimated_tokens=0, *,
                              enforce_subscription=True):
    prefix, explicit_model = _engine_parts(spec)
    info = backends.get(prefix, {})
    if enforce_subscription:
        _require_subscription(prefix, explicit_model)
    models = [m for m in info.get('models', []) if _is_subscription_enabled(prefix, m['slug'])]
    if not models and not enforce_subscription:
        models = info.get('models', [])
    info = {**info, 'models': models}
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
    hint = _extract_prompt_model_hint(prompt_text) if not explicit_model else ''
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
        if enforce_subscription:
            _require_subscription(prefix, slug)
        conflict = None
        if (
            prefix == 'claude'
            and not query
            and (role_target in ('math', 'code', 'review', 'orchestrator') or (not role_target and tier not in ('low', 'minimal')))
            and _is_lightweight(prefix, slug)
        ):
            conflict = '当前 Claude 配置卡片为轻量模型，不满足非 fast 专长角色要求；请切换主力卡片、启用本地路由或显式指定轻量模型/role:fast'
        return _profile(prefix, info, spec, slug, record, tier, source, effort, effort,
                        'catalog_declared' if record.get('supported_efforts') else 'unverified_request', error=conflict)
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
            enforce_subscription=enforce_subscription,
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
            if not record and explicit_model and enforce_subscription:
                try:
                    unfiltered_rec, _ = _resolve_explicit_agy_record(backends.get(prefix, {}).get('models', []), explicit_model, tier)
                except HarnessError:
                    unfiltered_rec = {}
                if unfiltered_rec and unfiltered_rec.get('slug'):
                    _require_subscription(prefix, unfiltered_rec['slug'])
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
            agy_rules = _catalog_rules('agy')
            if role_target == 'fast' or (not role_target and tier == 'low' and not forbid_lightweight):
                fast = [name for name in eligible if is_fast(name)]
                if fast:
                    chosen, source = fast[0], 'specialty_fast_lightweight'
            elif kind in ('code', 'gate'):
                top_gen = _agy_family_order(normal[0])[0]
                top_normal = [name for name in normal if _agy_family_order(name)[0] == top_gen]
                code_pick = _pick_agy_tagged_family(top_normal, agy_rules.get('code_families'))
                if code_pick:
                    chosen = code_pick
            elif kind in ('math', 'direction') or (kind == 'review' and tier != 'low'
                                                  and _has_positive_cue(prompt_text, HIGH_COMPLEXITY_CUES)):
                top_gen = _agy_family_order(normal[0])[0]
                top_normal = [name for name in normal if _agy_family_order(name)[0] == top_gen]
                deep_pick = _pick_agy_tagged_family(top_normal, agy_rules.get('deep_families'))
                chosen = deep_pick or max(top_normal, key=lambda name: EFFORT_LADDER.index(
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
    return {'model': cli_model or app_model, 'cli_model': cli_model, 'desktop_model': app_model,
            'configured_model_source': str(cli_path if cli_model else app_path) if cli_model or app_model else None,
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
        return run_process(command, Path.cwd(), timeout_sec=query_timeout, check=False)
    if not shutil.which('powershell.exe') or not shutil.which('taskkill.exe'):
        raise HarnessError('WSL 跨端查询需要 powershell.exe 与 taskkill.exe 进行进程监督')
    windows_temp = _wsl_path(Path(_windows_env('TEMP')), windows=False)
    with tempfile.TemporaryDirectory(prefix='cae-harness-', dir=windows_temp) as temp:
        path = Path(temp)
        command, pid_file = _interop_command(command, path, path, '', None)
        return run_process(command, path, timeout_sec=query_timeout, native_pid_file=pid_file, check=False)


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


def _split_url_trailing_punct(raw_url):
    trail = []
    s = raw_url
    while s:
        ch = s[-1]
        if ch in '.,;:!?}':
            trail.append(ch)
            s = s[:-1]
        elif ch == ')' and s.count(')') > s.count('('):
            trail.append(ch)
            s = s[:-1]
        elif ch == ']' and s.count(']') > s.count('['):
            trail.append(ch)
            s = s[:-1]
        else:
            break
    return s, ''.join(reversed(trail))


def _sanitize_endpoint_url(raw_url):
    text = str(raw_url or '').strip()
    if not text:
        return ''
    core, trail = _split_url_trailing_punct(text)
    if not core:
        return text
    if not core.lower().startswith(('http://', 'https://')):
        sanitized = '<redacted-url>' if ('@' in core or '?' in core or '#' in core) else core
        return sanitized + trail
    try:
        parsed = urlsplit(core)
        host = parsed.hostname or ''
        if not host:
            return '<redacted-url>' + trail
        if ':' in host:
            host = '[' + host + ']'
        port_part = f':{parsed.port}' if parsed.port else ''
        return f'{parsed.scheme}://{host}{port_part}{parsed.path}{trail}'
    except ValueError:
        return '<redacted-url>' + trail


def _redact_runtime_secret(text):
    raw = str(text or '')
    if not raw:
        return ''
    raw = re.sub(
        r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----|[^\r\n]*)',
        '<redacted-private-key>',
        raw,
    )
    raw = re.sub(
        r'(?i)(?:https?|socks5h?|socks4a?|wss?)://[^\s"\'\x60<>]+',
        lambda m: _sanitize_endpoint_url(m.group(0)),
        raw,
    )
    raw = re.sub(r'(?i)(\b[a-z][a-z0-9+.-]*://)([^/\s"\'\x60<>@]+@)', r'\1<redacted>@', raw)
    raw = re.sub(r'(?i)\b(Bearer|Basic|Token|ApiKey)\s+[A-Za-z0-9._~+/=-]{8,}', r'\1 <redacted>', raw)
    raw = re.sub(
        r'(?i)(?:^|(?<=[\s"\'{,;]))([A-Za-z0-9_-]*(?:DEEPSEEK_API_KEY|ANTHROPIC_AUTH_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY|'
        r'api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|'
        r'secret[_-]?key|password|passwd|passphrase|authorization))(["\']?\s*[=:]\s*)'
        r'(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|(?:(?:Bearer|Basic|Token|ApiKey)\s+)?[^\s"\',}]+)',
        r'\1\2<redacted>',
        raw,
    )
    raw = re.sub(
        r'\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9._-]{20,}|'
        r'eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9._-]{8,}|'
        r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[0-9A-Z]{16}|'
        r'hf_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,})',
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
    router_dir = root / 'router'
    router_primary = router_dir / 'router.json'
    router_platform = router_dir / ('router.windows.json' if (os.name == 'nt' or interop) else 'router.wsl.json')
    router_cfg = _read_json(router_primary if router_primary.exists() else router_platform)
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
    claude_max_ctx = _positive_int(_catalog_rules('claude').get('max_context_tokens'))
    models = []
    for i, name in enumerate(dict.fromkeys(name for name in model_names if isinstance(name, str) and name.strip())):
        tag_1m_window = (
            _positive_int(_subscription_model(name, 'claude').get('context_window'))
            or configured_window
            or claude_max_ctx
            or 1_000_000
        )
        models.append({
            'slug': name,
            'display_name': name,
            'provider': 'claude_configured',
            'priority': i,
            'max_context_window': tag_1m_window if '[1m]' in name.lower() else configured_window,
            'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max'],
        })
    if not models and source not in ('local_router', 'settings_env', 'placeholder_token'):
        models = [{'slug': alias, 'display_name': alias, 'provider': 'claude_builtin_alias', 'priority': i,
                   'max_context_window': configured_window, 'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max']}
                  for i, alias in enumerate(('opus', 'sonnet', 'haiku'))]
    return {'available': bool(binary), 'detected': bool(binary), 'binary': binary, 'family': 'claude',
            'config_home': str(root), 'windows_interop': interop, 'model': configured, 'models': models,
            'base_url': _sanitize_endpoint_url(base_url), 'router_configured': bool(router_cfg) and local_router,
            'uses_local_router': local_router, 'router_alive': _loopback_router_alive(base_url) if local_router else False,
            'llm_source': source, 'llm_configured': source in ('local_router', 'settings_env', 'credentials_file'),
            'configured_window_tokens': configured_window, 'effective_compaction_tokens': compact_window,
            'verified_status': 'unverified', 'authentication_status': 'not_checked',
            'requested_input_budget_tokens': requested, 'capability_source': 'local_settings',
            'model_catalog': _catalog_metadata(settings_file, settings, models, 'claude_settings',
                                              datetime.datetime.now(datetime.timezone.utc))}


def _dsh_info(binary, requested=None):
    root, interop = _dsh_home(binary)
    env_api_key = _usable_credential(os.environ.get('DEEPSEEK_API_KEY'))
    dotenv_api_key = bool(_dsh_dotenv_key(root))
    yaml_api_key = False
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
            yaml_api_key = yaml_api_key or official
            account = account or platform
        if not value:
            scopes.append((len(indent), key))
    api_key = yaml_api_key or dotenv_api_key or (env_api_key and not account)
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
            'credential_source': ('yaml_api_key' if yaml_api_key else 'dotenv_api_key' if dotenv_api_key
                                  else 'account' if account else 'env_api_key' if env_api_key else 'unconfigured'),
            'llm_source': source, 'dsh_provider': 'deepseek-official' if api_key else 'deepseek-account' if account else None,
            'llm_configured': source != 'unconfigured', 'verified_status': 'unverified', 'authentication_status': 'not_checked',
            'requested_input_budget_tokens': requested, 'capability_source': 'dsh_builtin_models',
            'model_catalog': {'source': 'dsh_builtin_models', 'model_count': len(models), 'freshness': 'builtin_contract'}}


def _run_claude_auth(info):
    command = [info['binary'], 'auth', 'status']
    if not info.get('windows_interop'):
        env = os.environ.copy()
        env['CLAUDE_CONFIG_DIR'] = info['config_home']
        return run_process(command, Path.cwd(), timeout_sec=15, env=env, check=False)
    if not shutil.which('powershell.exe') or not shutil.which('taskkill.exe'):
        raise HarnessError('WSL 跨端认证查询需要 powershell.exe 与 taskkill.exe')
    windows_temp = _wsl_path(Path(_windows_env('TEMP')), windows=False)
    with tempfile.TemporaryDirectory(prefix='cae-harness-', dir=windows_temp) as temp:
        path = Path(temp)
        command, pid_file = _interop_command(command, path, path, '', extra_env={
            'CLAUDE_CONFIG_DIR': _wsl_path(Path(info['config_home']))})
        return run_process(command, path, timeout_sec=15, native_pid_file=pid_file, check=False)


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
        is_loopback = (urlsplit(health_url).hostname or '').lower() in ('127.0.0.1', 'localhost', '::1')
        open_fn = (
            build_opener(ProxyHandler({})).open
            if is_loopback and urlopen is _STDLIB_URLOPEN
            else urlopen
        )
        with open_fn(health_url, timeout=2.0) as response:
            return response.status == 200 and json.load(response).get('ok') is True
    except (OSError, URLError, ValueError, AttributeError):
        return False


def _verify_claude_router_card_key(info, model_name):
    interop = bool(info.get('windows_interop'))
    cfg = _load_claude_router_config(info['config_home'], interop=interop, require_valid_file=True)
    card = _resolve_claude_router_card(cfg, model_name or info.get('model') or '')
    if not card:
        return
    db_value = str(os.environ.get('CAE_CCSWITCH_DB') or cfg.get('ccswitch_db') or '~/.cc-switch/cc-switch.db')
    if interop and not os.environ.get('CAE_CCSWITCH_DB') and db_value.startswith('~/'):
        db = Path(info['config_home']).parent / db_value[2:]
    elif interop and not os.environ.get('CAE_CCSWITCH_DB') and ntpath.isabs(db_value) and not posixpath.isabs(db_value):
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
        info['subscription_enabled'] = _is_subscription_enabled(name)
        prof = resolve_execution_profile(result_map, name + ':default', 'max', kind='code', enforce_subscription=False)
        deep_prof = resolve_execution_profile(result_map, name + ':default', 'max', kind='math', enforce_subscription=False)
        fast_prof = resolve_execution_profile(result_map, name + ':default', 'low', kind='review', enforce_subscription=False)
        info['frontier_model'] = prof['model']
        info['max_reasoning_effort'] = prof['reasoning_effort']
        info['auto_selectable'] = bool(_peer_auto_ready(result_map, name)
                                       and _is_subscription_enabled(name, prof['model'])
                                       and not prof['execution_blocked_reason'])
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
        prefix, requested_model = _engine_parts(spec)
        _require_subscription(prefix, requested_model)
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


def _local_fallback_engine(caller, engine, backends, prompt_text='', *, clean_room=False, isolation='', exclude_prefixes=()):
    cur_prefix, _ = _engine_parts(engine)
    if _extract_prompt_backend_hint(prompt_text) or _extract_prompt_model_hint(prompt_text):
        return None
    excluded = set(_extract_negated_backends(prompt_text)) | {str(p).strip().lower() for p in exclude_prefixes if p}
    norm_iso = _normalize_isolation(isolation, clean_room=clean_room)
    if norm_iso == 'clean_room':
        excluded.add('agy')
    elif norm_iso == 'hooks_only':
        excluded.update({'agy', 'dsh'})
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
    """单独创建带 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 的 Windows Job 对象，
    在 Popen 启动后立即绑定进程句柄，异常或不可用时回退 taskkill /T /F。
    """

    def __init__(self):
        import ctypes
        from ctypes import wintypes

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
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = (
            ('CreateJobObjectW', wintypes.HANDLE, (ctypes.c_void_p, wintypes.LPCWSTR)),
            ('SetInformationJobObject', wintypes.BOOL, (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)),
            ('AssignProcessToJobObject', wintypes.BOOL, (wintypes.HANDLE, wintypes.HANDLE)),
            ('TerminateJobObject', wintypes.BOOL, (wintypes.HANDLE, wintypes.UINT)),
            ('CloseHandle', wintypes.BOOL, (wintypes.HANDLE,)),
        )
        for name, result, arguments in signatures:
            function = getattr(self.api, name)
            function.restype, function.argtypes = result, arguments
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        ext_info = ExtendedLimitInfo()
        ext_info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(ext_info), ctypes.sizeof(ext_info)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def attach(self, proc):
        # Popen 保留的句柄具有创建时的权限，受限令牌重新 OpenProcess 可能被拒绝。
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def terminate(self):
        if self.handle and not self.api.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _process_options():
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
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
    job = None
    if os.name == 'nt':
        try:
            job = _WindowsJob()
        except OSError as exc:
            emit_event('windows_job_unavailable', error=str(exc), cleanup='taskkill /T /F')
    proc = None
    try:
        proc = subprocess.Popen(command, cwd=str(cwd) if cwd is not None else None, env=env, shell=shell,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', **_process_options())
        if job is not None:
            try:
                job.attach(proc)
                proc._cae_job = job
            except OSError as exc:
                job.close()
                job = None
                emit_event('windows_job_attach_failed', error=str(exc), cleanup='taskkill /T /F')
        return proc
    except BaseException as exc:
        # 即使事件输出或绑定 Job 失败，也必须清理已启动的进程。
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
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                grace_deadline = time.monotonic() + 1.5
                while proc.poll() is None and time.monotonic() < grace_deadline:
                    time.sleep(0.05)
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


def run_process(command, cwd, input_text=None, timeout_sec=600, env=None, shell=False, native_pid_file=None, *, check=True):
    if _CANCELLED.is_set():
        raise HarnessError('任务已取消', 'cancelled', partial_stdout='', partial_stderr='')
    deadline = time.monotonic() + timeout_sec
    proc = _start_process(command, cwd, {**(os.environ if env is None else env), **_NONINTERACTIVE_ENV}, shell)
    stdout, stderr, io_errors = [], [], []
    failure = None

    def read_lines(pipe, chunks):
        try:
            for line in pipe:
                chunks.append(line)
        except (OSError, ValueError) as exc:
            io_errors.append(str(exc))

    def write_input():
        try:
            with proc.stdin:
                if input_text:
                    proc.stdin.write(input_text)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (OSError, ValueError) as exc:
            io_errors.append(str(exc))

    threads = [threading.Thread(target=read_lines, args=(pipe, chunks), daemon=True)
               for pipe, chunks in ((proc.stdout, stdout), (proc.stderr, stderr))]
    threads.append(threading.Thread(target=write_input, daemon=True))
    try:
        emit_event('process_started', pid=proc.pid)
        for thread in threads:
            thread.start()
        reaping = False
        while proc.poll() is None or any(t.is_alive() for t in threads):
            if _CANCELLED.is_set():
                raise HarnessError('任务已取消', 'cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessError('子进程超时', 'timeout', timeout_sec=timeout_sec)
            if proc.returncode is not None and not reaping:
                # 父进程退出后，后代仍可能持有管道写端。
                reaping = True
                cleanup_errors = _terminate_tree(proc, native_pid_file)
                if cleanup_errors:
                    raise HarnessError('父进程退出后无法核实后代清理', cleanup_errors=cleanup_errors)
            _CANCELLED.wait(min(remaining, 0.05))
        if _CANCELLED.is_set():
            raise HarnessError('任务已取消', 'cancelled')
        emit_event('process_finished', pid=proc.pid, returncode=proc.returncode)
        if check and proc.returncode:
            raise HarnessError(f'子进程退出码 {proc.returncode}', returncode=proc.returncode)
        if io_errors and not proc.returncode:
            raise HarnessError('子进程管道读写失败', pipe_errors=io_errors)
        return subprocess.CompletedProcess(command, proc.returncode, ''.join(stdout), ''.join(stderr))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_errors = _reap_process(proc, native_pid_file)
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=1)
        if any(t.is_alive() for t in threads):
            cleanup_errors.append('Child pipe reader/writer did not stop')
        else:
            cleanup_errors.extend(_close_process_streams(proc))
        if isinstance(failure, HarnessError):
            failure.details.update(process_started=True, partial_stdout=''.join(stdout), partial_stderr=''.join(stderr))
        if cleanup_errors:
            if failure is None:
                raise HarnessError('子进程资源回收未完成', cleanup_errors=cleanup_errors,
                                   partial_stdout=''.join(stdout), partial_stderr=''.join(stderr))
            else:
                _note_cleanup_errors(failure, cleanup_errors)


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


def _extract_errors_text(value):
    if not isinstance(value, list) or not value:
        return ''
    parts = []
    for item in value:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
        elif isinstance(item, dict):
            msg = item.get('message') or item.get('error') or item.get('detail') or json.dumps(item, ensure_ascii=False)
            if str(msg).strip():
                parts.append(str(msg).strip())
        elif item is not None and str(item).strip():
            parts.append(str(item).strip())
    return '; '.join(parts)


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
    errors_text = _extract_errors_text(parsed.get('errors'))
    event_error = parsed.get('event', parsed.get('type')) == 'error'
    subtype_error = str(parsed.get('subtype', '')).lower().startswith('error')
    if (event_error or subtype_error or finished_reason == 'error' or parsed.get('is_error')
            or parsed.get('error') or errors_text
            or status in ('error', 'failed', 'partial', 'incomplete', 'invalid', 'waiting', 'running')):
        result_err = parsed.get('result') if isinstance(parsed.get('result'), str) else ''
        err_parts = [
            str(v).strip() for v in (parsed.get('error'), errors_text, parsed.get('message'), result_err)
            if v is not None and str(v).strip()
        ]
        error = '\n'.join(dict.fromkeys(err_parts))[-2000:]
        error_status = 'quota_exhausted' if _is_quota_exhausted(error) else 'failed'
        raise HarnessError('后端返回错误回执', error_status, backend_status=status or 'error',
                           finished_reason=finished_reason, error=error)


DSML_TAIL_RE = re.compile(r'(?:\s*</?[｜|]+DSML[｜|]+[^>]*>)+\s*$')


def _strip_dsml_tail(text):
    return DSML_TAIL_RE.sub('', text).strip()


_HEX4_CHARS = frozenset('0123456789abcdefABCDEF')
_VALID_JSON_ESCAPES = frozenset('"\\/bfnrt')
_LATEX_CONTROL_ESCAPE_RE = re.compile(
    r'^(?:[bf][A-Za-z]'
    r'|(?:nabla|notin|nmid|noindent|nonumber|norm|ni|nleq|ngeq'
    r'|rho|rightarrow|Rightarrow|rightharpoonup|rbrace|rangle|rceil|rfloor|rvert|rVert|rm'
    r'|tau|theta|Theta|vartheta|times|tilde|widetilde|triangle|triangleq|tfrac'
    r'|tanh|tan|top|to|therefore|textstyle)(?![A-Za-z])'
    r'|(?:neq|ne)(?![A-Za-z]|\s*=)'
    r'|nu(?=_(?=\{)|[\^\\{]|(?:\s*[,.;:)\]}\$]))'
    r'|(?:text(?:bf|it|tt|rm|sf|sc|normal)?|tag)(?=\s*\{)'
    r'|right(?=\s*[)\]\\|.]))'
)
_LINE_NUM_PREFIX_RE = re.compile(r':\d+$')
_WIN_SPACE_CONT_RE = re.compile(r'^ [A-Za-z0-9_.-]+\\')


def _starts_unescaped_win_drive(text, str_start, idx):
    return (
        idx - str_start >= 2
        and text[idx - 1] == ':'
        and text[idx - 2].isalpha()
        and (idx - str_start == 2 or not (text[idx - 3].isalnum() or text[idx - 3] in ('_', '/', '\\')))
        and idx + 1 < len(text)
        and text[idx + 1] not in ('\\', '"', '/')
    )


def _win_path_continues_at_char(text, idx, ch):
    if ch in ',;()[]{}<>|?*:\r\n\t':
        return False
    if ch.isspace():
        return ch == ' ' and bool(_WIN_SPACE_CONT_RE.match(text[idx:idx + 64]))
    return True


def _is_unescaped_control_collision(text, str_start, idx, win_path_active):
    nxt = text[idx + 1]
    if nxt not in 'bfnrt':
        return False
    if win_path_active:
        return True
    if nxt in 'nt' and _LINE_NUM_PREFIX_RE.search(text[max(str_start, idx - 16):idx]):
        return False
    return bool(_LATEX_CONTROL_ESCAPE_RE.match(text[idx + 1:idx + 32]))


def _repair_json_string_escapes(text):
    """在标准 json.loads 失败后，仅修复双引号字符串字面量内部的非法单反斜杠转义（如 Windows 路径与 LaTeX 命令）。"""
    if '\\' not in text:
        return text
    out = []
    in_string = False
    str_start = 0
    win_path_active = False
    idx = 0
    length = len(text)
    while idx < length:
        ch = text[idx]
        if not in_string:
            if ch == '"':
                in_string = True
                str_start = idx + 1
                win_path_active = False
            out.append(ch)
            idx += 1
            continue
        if ch == '"':
            in_string = False
            win_path_active = False
            out.append(ch)
            idx += 1
            continue
        if ch != '\\':
            if win_path_active and not _win_path_continues_at_char(text, idx, ch):
                win_path_active = False
            out.append(ch)
            idx += 1
            continue
        if idx + 1 >= length:
            out.append('\\\\')
            idx += 1
            continue
        if not win_path_active and _starts_unescaped_win_drive(text, str_start, idx):
            win_path_active = True
        nxt = text[idx + 1]
        if nxt == '"':
            win_path_active = False
            out.append('\\"')
            idx += 2
            continue
        if nxt == '\\':
            out.append('\\\\')
            idx += 2
            continue
        if nxt in _VALID_JSON_ESCAPES and not _is_unescaped_control_collision(text, str_start, idx, win_path_active):
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


def _has_unrepaired_latex_escape(text):
    if '\\' not in text:
        return False
    in_string = False
    str_start = 0
    win_path_active = False
    idx = 0
    length = len(text)
    while idx < length:
        ch = text[idx]
        if not in_string:
            if ch == '"':
                in_string = True
                str_start = idx + 1
                win_path_active = False
            idx += 1
            continue
        if ch == '"':
            in_string = False
            win_path_active = False
            idx += 1
            continue
        if ch != '\\':
            if win_path_active and not _win_path_continues_at_char(text, idx, ch):
                win_path_active = False
            idx += 1
            continue
        if idx + 1 >= length:
            return False
        if not win_path_active and _starts_unescaped_win_drive(text, str_start, idx):
            win_path_active = True
        nxt = text[idx + 1]
        if nxt == '"':
            win_path_active = False
            idx += 2
            continue
        if nxt in ('\\', '/'):
            idx += 2
            continue
        if _is_unescaped_control_collision(text, str_start, idx, win_path_active):
            return True
        if nxt == 'u' and idx + 6 <= length and all(c in _HEX4_CHARS for c in text[idx + 2:idx + 6]):
            idx += 6
            continue
        idx += (1 if win_path_active else 2)
    return False


def _repair_unclosed_json_tail(text):
    """保守修补模型在长数组尾部漏写 1~2 个闭合括号（如 `["..."}` 缺 `]`）的情况。"""
    s = str(text or '').strip()
    if not s.startswith('{'):
        return None
    out = []
    stack = []
    in_string = False
    escaped = False
    repairs = 0
    for ch in s:
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch in ('{', '['):
            stack.append(ch)
            out.append(ch)
            continue
        if ch == '}':
            if not stack:
                return None
            if stack[-1] == '[':
                if '{' not in stack:
                    return None
                while stack and stack[-1] == '[':
                    stack.pop()
                    out.append(']')
                    repairs += 1
                    if repairs > 2:
                        return None
            if not stack or stack[-1] != '{':
                return None
            stack.pop()
            out.append(ch)
            continue
        if ch == ']':
            if not stack:
                return None
            if stack[-1] == '{':
                if '[' not in stack:
                    return None
                while stack and stack[-1] == '{':
                    stack.pop()
                    out.append('}')
                    repairs += 1
                    if repairs > 2:
                        return None
            if not stack or stack[-1] != '[':
                return None
            stack.pop()
            out.append(ch)
            continue
        out.append(ch)
    if in_string:
        return None
    if stack:
        if len(stack) + repairs > 2:
            return None
        while stack:
            opener = stack.pop()
            out.append('}' if opener == '{' else ']')
            repairs += 1
    if repairs == 0:
        return None
    return ''.join(out)


def _loads_relaxed_dict(candidate):
    attempts = (
        (_repair_json_string_escapes(candidate), candidate)
        if _has_unrepaired_latex_escape(candidate)
        else (candidate, _repair_json_string_escapes(candidate))
    )
    for attempt in attempts:
        try:
            parsed = json.loads(attempt, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, RecursionError):
            continue
    for attempt in attempts:
        repaired_tail = _repair_unclosed_json_tail(attempt)
        if not repaired_tail:
            continue
        try:
            parsed = json.loads(repaired_tail, strict=False)
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
        first_raw = (
            _repair_json_string_escapes(raw)
            if raw.startswith(('{', '[')) and _has_unrepaired_latex_escape(raw)
            else raw
        )
        return json.loads(first_raw)
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
                events.append(json.loads(_repair_json_string_escapes(stripped) if _has_unrepaired_latex_escape(stripped) else stripped))
        except (ValueError, RecursionError) as decode_error:
            # 兼容 CLI wrapper（如 Windows .CMD / 插件提示行）混入少量非 JSON 诊断行的 JSONL 事件流
            tolerant_events = []
            for line in raw.splitlines():
                stripped = _strip_dsml_tail(line.strip())
                if not stripped or not (stripped.startswith('{') and stripped.endswith('}')):
                    continue
                try:
                    ev_obj = json.loads(_repair_json_string_escapes(stripped) if _has_unrepaired_latex_escape(stripped) else stripped)
                except (ValueError, RecursionError):
                    continue
                if isinstance(ev_obj, dict):
                    _check_output_status(ev_obj)
                    tolerant_events.append(ev_obj)
            tolerant_results = [
                ev for ev in tolerant_events
                if ev.get('event') == 'result' or ev.get('type') in ('final', 'result')
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
            schema_candidates = []
            search_pos = 0
            for _ in range(64):
                brace_idx = raw.find('{', search_pos)
                if brace_idx < 0:
                    break
                search_pos = brace_idx + 1
                slice_text = raw[brace_idx:]
                repaired_slice = _repair_json_string_escapes(slice_text)
                attempts = (
                    ((repaired_slice, True), (slice_text, False))
                    if _has_unrepaired_latex_escape(slice_text)
                    else ((slice_text, False), (repaired_slice, True))
                )
                decoded = None
                for slice_used, was_repaired in attempts:
                    try:
                        candidate_obj, rel_end = decoder.raw_decode(slice_used, 0)
                        decoded = (candidate_obj, slice_used, rel_end, was_repaired)
                        break
                    except (ValueError, RecursionError):
                        continue
                if decoded is None:
                    continue
                candidate_obj, slice_used, rel_end, was_repaired = decoded
                if isinstance(candidate_obj, dict) and candidate_obj:
                    tail_rest = _strip_dsml_tail(slice_used[rel_end:]).strip()
                    if not tail_rest or re.fullmatch(r'```+', tail_rest):
                        return candidate_obj
                    if 'summary' in candidate_obj and ('content' in candidate_obj or 'findings' in candidate_obj):
                        schema_candidates.append(candidate_obj)
                        adv_end = brace_idx + (1 if was_repaired else rel_end)
                        search_pos = max(search_pos, adv_end)
            if len(schema_candidates) == 1:
                return schema_candidates[0]
            first_brace = raw.find('{')
            if first_brace >= 0:
                tail_repaired = _loads_relaxed_dict(raw[first_brace:])
                if tail_repaired is not None:
                    return tail_repaired
            raise OutputParseError('子任务输出不是合法 JSON') from decode_error
        results = []
        for event in events:
            if not isinstance(event, dict):
                raise OutputParseError('输出流事件必须为对象') from exc
            _check_output_status(event)
            if event.get('event') == 'result' or event.get('type') in ('final', 'result'):
                results.append(event)
        if len(results) != 1:
            raise OutputParseError('输出流必须包含一个最终 result 回执') from exc
        return results[0]


def _extract_json_object(raw_text, schema=None, *, strict=False):
    schema = schema or AUDIT_JSON_SCHEMA
    decode = json.loads if strict else _decode_output_json
    parsed = decode(raw_text)
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
            next_value = decode(next_value)
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
            'event', 'type', 'subtype', 'status', 'finished_reason', 'is_error', 'error', 'message',
            'code', 'reason', 'short_error', 'detail', 'details') if key in event}
        errors_text = _extract_errors_text(event.get('errors'))
        if errors_text:
            details['errors'] = event['errors']
            details['errors_joined'] = errors_text
        is_error = bool(
            event.get('is_error') or event.get('error') or errors_text
            or str(event.get('subtype', '')).lower().startswith('error')
            or str(event.get('status', '')).lower() in (
                'error', 'failed', 'partial', 'incomplete', 'invalid', 'waiting', 'running',
                'timeout', 'cancelled', 'canceled', 'interrupted')
            or str(event.get('finished_reason', '')).lower() in (
                'error', 'print_timeout', 'interrupted', 'cancelled', 'canceled')
        )
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


def _dsh_dotenv_key(home):
    for line in _optional_text(Path(home) / '.env').splitlines():
        if line.lstrip().startswith('#'):
            continue
        key, eq, value = line.partition('=')
        if eq and re.sub(r'^export\s+', '', key.strip()) == 'DEEPSEEK_API_KEY':
            value = _credential_scalar(value)
            return value if _usable_credential(value) else None
    return None


_DSH_PERMISSION_CACHE = {}
_DSH_PERMISSION_LOCK = threading.Lock()


def _dsh_supports_cli_permission_flags(binary, env=None, *, interop=False):
    try:
        stamp = Path(binary).stat().st_mtime_ns
    except OSError:
        return False
    key = (str(binary), stamp, bool(interop))
    with _DSH_PERMISSION_LOCK:
        if key in _DSH_PERMISSION_CACHE:
            return _DSH_PERMISSION_CACHE[key]
        try:
            probe_cmd = [binary, '--profile', 'headless', '--help']
            if interop and _is_posix() and str(binary).lower().endswith(('.cmd', '.bat')):
                probe_cmd = ['cmd.exe', '/c', _wsl_path(Path(binary)), '--profile', 'headless', '--help']
            result = run_process(probe_cmd, Path.cwd(),
                                 timeout_sec=8.0 if interop else 5.0, env=env)
            output = (result.stdout or '') + (result.stderr or '')
            is_help = bool(re.search(r'(?im)(?:usage:|options:|--help|--profile)', output))
            if result.returncode != 0 or not is_help:
                return False
            supported = '--permission-mode' in output
        except (OSError, HarnessError, subprocess.TimeoutExpired):
            return False
        _DSH_PERMISSION_CACHE[key] = supported
        return supported


_INLINE_OMITTED_LIST_MAX_ITEMS = 80
_INLINE_OMITTED_LIST_MAX_BYTES = 8 * 1024
_INLINE_OMITTED_RECEIPT_MAX_ITEMS = 200
_CODEX_MAX_PROMPT_CHARS = 950_000


def _inline_readonly_cap_bytes(prefix=''):
    hd = _headless_tunables()
    default_kib = _positive_int(hd.get('inline_readonly_max_kib')) or 512
    if str(prefix or '').strip().lower() == 'agy':
        agy_kib = _positive_int(hd.get('agy_inline_readonly_max_kib'))
        if agy_kib is None:
            agy_kib = min(default_kib, 128)
        return agy_kib * 1024
    return default_kib * 1024


def _format_omitted_list_suffix(omitted):
    if not omitted:
        return ''
    prefix_text = '\n未内联清单：'
    shown = []
    used = len(prefix_text.encode('utf-8'))
    for rel in omitted:
        if len(shown) >= _INLINE_OMITTED_LIST_MAX_ITEMS:
            break
        sep = ', ' if shown else ''
        piece = sep + rel
        piece_bytes = len(piece.encode('utf-8'))
        if shown and used + piece_bytes > _INLINE_OMITTED_LIST_MAX_BYTES:
            break
        shown.append(rel)
        used += piece_bytes
    rest = len(omitted) - len(shown)
    tail = f' ...（其余 {rest} 个文件已省略，请按需定向读取）' if rest > 0 else ''
    return prefix_text + ', '.join(shown) + tail


def _inline_readonly_prompt(prompt, root, rel_files=None, *, exclude_agent_instructions=False, included=None, summary=None, prefix=''):
    root = Path(root).resolve()
    norm_rels = [_literal_input_path(rel) for rel in rel_files] if rel_files is not None else None
    explicit_single = norm_rels is not None and len(norm_rels) == 1
    if norm_rels is None:
        raw_records = collect_target_files(root)
        if exclude_agent_instructions:
            records = [
                item for item in raw_records
                if Path(item['rel_path']).name.upper() not in _AGENT_INSTRUCTION_FILENAMES
            ]
            if raw_records and not records:
                raise HarnessError(_CLEAN_ROOM_ONLY_INSTR_ERROR)
        else:
            records = raw_records
    else:
        records = [{'rel_path': rel, 'path': root / rel} for rel in norm_rels]
        if exclude_agent_instructions and not explicit_single:
            filtered = [
                item for item in records
                if posixpath.basename(item['rel_path']).upper() not in _AGENT_INSTRUCTION_FILENAMES
            ]
            if records and not filtered:
                raise HarnessError(_CLEAN_ROOM_ONLY_INSTR_ERROR)
            records = filtered
    cap = _inline_readonly_cap_bytes(prefix)
    header_full = '\n\n所需目标文件原文已附带精确行号内联在下方，请勿再重复调用文件读取工具遍历工作区。\n'
    header_partial = '\n\n以下已内联文件附带精确行号，对已内联文件请勿再重复调用文件读取工具；未内联清单中的非敏感文本文件可按需单次读取。\n'
    blocks, block_rels, omitted, cap_omitted_count = [], [], [], 0
    size = max(len(header_full.encode('utf-8')), len(header_partial.encode('utf-8')))
    for item in records:
        path, rel = Path(item['path']), item['rel_path']
        if (path.name.startswith('.env') or re.search(r'(?i)(credential|secret|auth\.json|private.?key|\.pem$|\.key$)', path.name)):
            omitted.append(rel)
            continue
        if explicit_single and exclude_agent_instructions and not path.exists():
            neutral_candidate = path.with_name(f'{_STAGED_NEUTRAL_PREFIX}{path.name}{_STAGED_NEUTRAL_SUFFIX}')
            if neutral_candidate.exists():
                path = neutral_candidate
        try:
            path.resolve().relative_to(root)
            if _is_link(path) or any(_is_link(parent) for parent in path.parents if parent != root and root in parent.parents):
                raise ValueError('linked source')
            if path.stat().st_size > cap:
                omitted.append(rel)
                cap_omitted_count += 1
                continue
            text = path.read_text(encoding='utf-8')
            if '\0' in text:
                raise ValueError('binary source')
            numbered = '\n'.join(f'{index}: {line}' for index, line in enumerate(text.splitlines(), 1))
            block = f'=== FILE: {rel} ===\n{numbered}\n\n'
            block_size = len(block.encode('utf-8'))
            if size + block_size > cap:
                omitted.append(rel)
                cap_omitted_count += 1
                continue
            size += block_size
            blocks.append(block)
            block_rels.append(rel)
        except (OSError, ValueError):
            omitted.append(rel)
    if str(prefix or '').strip().lower() == 'codex':
        # Codex CLI 单条输入协议硬上限约 1,048,576 字符；预留安全余量防止 input_too_large。
        while blocks:
            candidate_suffix = _format_omitted_list_suffix(omitted)
            candidate_header = header_partial if omitted else header_full
            total_chars = len(prompt) + len(candidate_header) + sum(len(b) for b in blocks) + len(candidate_suffix)
            if total_chars <= _CODEX_MAX_PROMPT_CHARS:
                break
            dropped_block = blocks.pop()
            dropped_rel = block_rels.pop()
            size -= len(dropped_block.encode('utf-8'))
            omitted.insert(0, dropped_rel)
            cap_omitted_count += 1
    if included is not None:
        included.extend(block_rels)
    if isinstance(summary, dict):
        summary.update(
            inlined_count=len(blocks),
            omitted_count=len(omitted),
            cap_omitted_count=cap_omitted_count,
            inlined_bytes=size if blocks else 0,
            cap_bytes=cap,
            omitted_files=list(omitted[:_INLINE_OMITTED_RECEIPT_MAX_ITEMS]),
            omitted_files_truncated=len(omitted) > _INLINE_OMITTED_RECEIPT_MAX_ITEMS,
        )
    header = header_partial if omitted else header_full
    suffix = _format_omitted_list_suffix(omitted)
    return prompt + (header + ''.join(blocks) if blocks else '') + suffix


def extract_cli_token_usage(prefix, raw_stdout, raw_stderr=''):
    unknown = {'input_tokens': None, 'cached_input_tokens': None, 'output_tokens': None,
               'total_tokens': None, 'usage_source': 'unreported_by_cli'}
    reports = []
    def visit(value, terminal=False):
        if not isinstance(value, dict):
            return
        terminal = terminal or value.get('type') in ('final', 'result', 'turn.completed') or value.get('event') in ('final', 'result')
        for key in ('usage', 'tokenUsage', 'tokens'):
            usage = value.get(key)
            if isinstance(usage, dict):
                reports.append((usage, terminal))
        if prefix == 'claude' and isinstance(value.get('modelUsage'), dict) and not any(
                isinstance(value.get(key), dict) and any(v is not None for v in normalize(value[key]).values())
                for key in ('usage', 'tokenUsage', 'tokens')):
            aggregate = {}
            for source, target in (('inputTokens', 'input_tokens'), ('outputTokens', 'output_tokens'),
                                   ('cacheReadInputTokens', 'cache_read_input_tokens'),
                                   ('cacheCreationInputTokens', 'cache_creation_input_tokens')):
                counts = [integer(usage.get(source)) for usage in value['modelUsage'].values() if isinstance(usage, dict)]
                if any(count is not None for count in counts):
                    aggregate[target] = sum(count for count in counts if count is not None)
            reports.append((aggregate, terminal))
        # Only structured envelopes are examined; generated result text is never treated as telemetry.
        for key in ('payload', 'data', 'message', 'info', 'result', 'step_update'):
            if isinstance(value.get(key), dict):
                visit(value[key], terminal)
    def integer(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    def normalize(usage):
        def first(*names):
            return next((integer(usage[name]) for name in names if integer(usage.get(name)) is not None), None)
        inp = first('input_tokens', 'prompt_tokens', 'inputTokens', 'promptTokens', 'uncachedInputTokens')
        out = first('output_tokens', 'completion_tokens', 'outputTokens', 'completionTokens')
        cached = first(
            'cached_input_tokens', 'cache_hit_tokens', 'cached_tokens',
            'cache_read_input_tokens', 'cachedInputTokens',
            'cacheReadTokens', 'cache_read_tokens', 'prompt_cache_hit_tokens',
        )
        details = usage.get('prompt_tokens_details') or usage.get('input_tokens_details') or {}
        if cached is None and isinstance(details, dict):
            cached = integer(details.get('cached_tokens'))
        total = first('total_tokens', 'totalTokens')
        if prefix == 'claude':
            creation = first('cache_creation_input_tokens', 'cacheCreationInputTokens')
            # Claude's input_tokens excludes both cache categories.
            if inp is not None:
                inp += (cached or 0) + (creation or 0)
        elif prefix == 'dsh':
            creation = first('cacheWriteTokens', 'cache_write_tokens', 'cache_creation_input_tokens')
            extra_cache = (cached or 0) + (creation or 0)
            if inp is not None and extra_cache > 0:
                # DSH 的 inputTokens 仅计未命中缓存输入；当 totalTokens 与之吻合（或缺失）时再计入总输入，避免未来重复计数。
                if total is None or (out is not None and inp + out + extra_cache == total):
                    inp += extra_cache
                elif total is not None and out is None and inp + extra_cache <= total:
                    inp += extra_cache
        elif prefix == 'agy':
            if inp is not None and cached:
                # agy CLI 的 input_tokens 与 total_tokens 均不含 cache_read_tokens；仅在口径未含缓存时补入总输入防重复计数。
                if total is None or (out is not None and inp + out == total):
                    orig_inp = inp
                    inp += cached
                    if total is not None and out is not None and total == orig_inp + out:
                        total = inp + out
        if total is None and inp is not None and out is not None:
            total = inp + out
        return {'input_tokens': inp, 'cached_input_tokens': cached, 'output_tokens': out, 'total_tokens': total}
    for raw in (raw_stdout, raw_stderr):
        try:
            visit(json.loads(raw))
        except (TypeError, ValueError):
            for line in (raw or '').splitlines():
                try:
                    visit(json.loads(line))
                except ValueError:
                    continue
    usable = [(normalize(usage), terminal) for usage, terminal in reports]
    usable = [(usage, terminal) for usage, terminal in usable if any(v is not None for v in usage.values())]
    if not usable:
        return unknown
    finals = [usage for usage, terminal in usable if terminal]
    if finals:
        result = finals[-1]
    elif prefix in ('codex', 'claude'):
        result = usable[-1][0]
    else:
        result = {key: (sum(usage[key] for usage, _ in usable if usage[key] is not None)
                        if any(usage[key] is not None for usage, _ in usable) else None) for key in unknown if key != 'usage_source'}
    return {**result, 'usage_source': 'cli_reported'}


def _json_events(lines):
    for line in lines:
        with contextlib.suppress(ValueError, RecursionError):
            event = json.loads(line.lstrip('\ufeff'))
            if isinstance(event, dict):
                yield event


def _provider_id(event):
    keys = ('thread_id', 'session_id', 'sessionId', 'conversation_id')
    if event.get('type') in ('session_meta', 'session'):
        keys += ('id',)
    for node in (event, event.get('init'), event.get('result'), event.get('payload')):
        if isinstance(node, dict):
            for key in keys:
                value = node.get(key)
                if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
                    return value
    return None


def _session_logs(prefix, home, sid, isolated):
    if not (home and (sid or isolated)):
        return []
    if prefix == 'dsh':
        pat = f'**/{sid or "*"}/session*.jsonl*'
        for sub in ('headless-sessions', 'sessions'):
            found = sorted(p for p in (home / sub).glob(pat) if p.is_file() and not _is_link(p))
            if found:
                return found
        return []
    subdir = {'codex': 'sessions', 'claude': 'projects', 'agy': 'antigravity-cli/brain'}[prefix]
    patterns = {'codex': f'**/rollout-*{sid or ""}.jsonl', 'claude': f'**/{sid}.jsonl',
                'agy': f'{sid}/.system_generated/logs/transcript*.jsonl'}
    return sorted(p for p in (home / subdir).glob(patterns[prefix])
                  if p.is_file() and not _is_link(p))


def _is_resolved_within(candidate, base_dir):
    try:
        return Path(candidate).resolve().is_relative_to(Path(base_dir).resolve())
    except (OSError, RuntimeError, ValueError):
        return False


_SHELL_READ_POS_RE = re.compile(
    r'''(?:^|[\s'";&|=(])(?:cat|nl\s+-ba|sed\s+-n\s+'[^']+'|Get-Content)\s+(?:-(?:LiteralPath|Path)\s+)?(?:"([^"]+)"|'([^']+)'|([^\s"'|;&)$]+))''',
    re.I,
)
_SHELL_READ_PS_NAMED_RE = re.compile(
    r'''(?:^|[\s'";&|=(])(?:Get-Content|Select-String)\b[^;|&()\r\n]*?\s-(?:LiteralPath|Path)\s+(?:"([^"]+)"|'([^']+)'|([^\s"'|;&)$]+))''',
    re.I,
)


def _extract_shell_read_paths(command):
    if not isinstance(command, str) or not command:
        return []
    # 兼容 Windows Codex 经 powershell.exe -Command 单引号嵌套转义生成的 '"'path' 形式。
    normalized = command.replace("'\"'", "'")
    paths = []
    for pattern in (_SHELL_READ_POS_RE, _SHELL_READ_PS_NAMED_RE):
        for match in pattern.finditer(normalized):
            cand = next((g for g in match.groups() if g), '')
            if cand and not cand.startswith(('-', '$')) and '$' not in cand:
                paths.append(cand)
    return paths


def _session_trace(events, root, stage_dir):
    reads, pending, totals = set(), {}, {}
    assistant_msgs, reasoning_msgs = collections.deque(maxlen=4), collections.deque(maxlen=3)
    for event in events:
        node = next((event[k] for k in ('item', 'step_update', 'payload', 'data', 'result')
                     if isinstance(event.get(k), dict)), event)
        kind = node.get('type') or node.get('step_type') or event.get('type') or event.get('event')
        if isinstance(node.get('message'), dict):
            node = node['message']
        blocks = node.get('content')
        blocks = blocks if isinstance(blocks, list) else []
        for block in (node, *blocks):
            if not isinstance(block, dict):
                continue
            tool = block.get('tool_info')
            tool = tool if isinstance(tool, dict) else block
            name = str(tool.get('name') or tool.get('tool') or block.get('tool_name') or '').lower()
            args = tool.get('input') or tool.get('parameters') or tool.get('arguments') or {}
            if isinstance(args, str):
                args = next(_json_events([args]), {})
            call = next((block[k] for k in ('tool_use_id', 'callId', 'call_id', 'toolCallId', 'id')
                         if isinstance(block.get(k), str)), None)
            paths = []
            if name in ('read', 'read_file', 'view_file', 'read_text_file') and isinstance(args, dict):
                paths = [v for k, v in args.items() if k.lower() in ('path', 'file_path', 'filepath', 'absolutepath')]
                if call and kind != 'tool' and block.get('status') != 'completed':
                    pending[call], paths = paths, []
                elif (block.get('state') != 'DONE' and block.get('status') != 'completed') or tool.get('error'):
                    paths = []
            if block.get('type') in ('tool_result', 'function_call_output') or kind in ('tool_result', 'tool/result'):
                paths = pending.pop(call, []) if not (
                    block.get('is_error') or block.get('isError') or block.get('error') or block.get('status') == 'error'
                ) else []
            if kind in ('command_execution', 'exec_command_end') and node.get('exit_code') == 0:
                paths = [cmd.get('path') for cmd in (node.get('parsed_cmd') or ()) if isinstance(cmd, dict) and cmd.get('type') == 'read']
                if not paths and isinstance(node.get('command'), str):
                    paths = _extract_shell_read_paths(node['command'])
            for path in paths:
                if isinstance(path, str):
                    current = stage_dir or root
                    rel = _normalize_rel_file(path, current)
                    if not ntpath.isabs(rel) and '\0' not in rel and _is_resolved_within(current / rel, current):
                        reads.add(rel)
            is_reasoning = kind in ('reasoning', 'agent_reasoning', 'thinking')
            is_assistant = (
                kind in ('agent_message', 'agent_response', 'assistant', 'assistant/message', 'text', 'final', 'result')
                or (node.get('role') == 'assistant' and not is_reasoning)
                or ('summary' in node and not is_reasoning)
            )
            if is_reasoning or is_assistant:
                msg = next((block[k] for k in ('text', 'thinking', 'text_delta', 'message', 'response', 'result', 'content')
                            if isinstance(block.get(k), str) and block[k].strip()), None)
                if isinstance(msg, str) and msg.strip():
                    clipped = msg[-2500:]
                    target_q = reasoning_msgs if is_reasoning else assistant_msgs
                    if clipped not in target_q:
                        target_q.append(clipped)
        if kind == 'token_count' and isinstance(node.get('info'), dict):
            totals = node['info'].get('total_token_usage') or totals
    # 优先保留正式助手回复，避免被短推理小标题挤出队列；无正式回复时回退到推理片段。
    selected = list(assistant_msgs)[-3:] if assistant_msgs else list(reasoning_msgs)
    # 从最新片段向前分配字符预算。
    remaining, tail = 2500, []
    for msg in reversed(selected):
        if remaining:
            tail.append(msg[-remaining:])
            remaining -= len(tail[-1])
    if stage_dir and reads:
        reads = _rewrite_staged_paths({'read_files': sorted(reads)}, stage_dir)['read_files']
    return sorted(reads), tail[::-1], totals


_CITATION_SPAN_RE = re.compile(
    r'\x60([^\x60\r\n]+?)(?:#L|:)(\d+)(?:(?:-L?)(\d+))?\x60'
    r'|\x60([^\x60\r\n]+?)\x60(?:#L|:)(\d+)(?:(?:-L?)(\d+))?'
    r'|([^\s\x60<>()\[\]"\'：,，;；。、！？【】「」『』（）｜–—]+?)(?:#L|:)(\d+)(?:(?:-L?)(\d+))?'
)

_CITATION_NOISE_CHARS_RE = re.compile(r'[\s｜、，；：！？【】「」『』（）{}\*\?<>"]')
_CITATION_PURE_EXT_RE = re.compile(r'^(?:\.[A-Za-z0-9_-]+)+$')


def _is_plausible_citation_candidate(candidate, root):
    if not candidate or candidate.startswith('#') or re.match(r'^\w+://', candidate):
        return False
    if candidate.isdigit() or _CITATION_NOISE_CHARS_RE.search(candidate):
        return False
    if candidate.strip('.,;:/\\-_~') == '':
        return False
    with contextlib.suppress(OSError):
        if (root / candidate).is_file():
            return True
    if _CITATION_PURE_EXT_RE.match(candidate) or re.fullmatch(r'[\d/\\._-]+', candidate):
        return False
    if '/' in candidate or '\\' in candidate:
        return True
    if '.' in candidate and not candidate.startswith('.') and not candidate.endswith('.'):
        return True
    return False


def _citation_audit(result, root, reads, inlined, stage_dir=None):
    findings = result.get('findings', [])
    refs = {(f['file'], f['line_start'], f['line_end']) for f in findings}
    for body in (result.get('content', ''), *result.get('evidence', []), *(f['evidence'] for f in findings)):
        for match in _CITATION_SPAN_RE.finditer(body):
            g = match.groups()
            quoted = g[0] or g[3]
            bare = g[6]
            start = g[1] or g[4] or g[7]
            end = g[2] or g[5] or g[8]
            candidate = (quoted or bare).strip().lstrip('[').rstrip('.,;:')
            if _is_plausible_citation_candidate(candidate, root) and (
                quoted or re.search(r'[./\\]|#L', match[0]) or (root / candidate).is_file()
            ):
                refs.add((candidate, int(start), int(end or start)))
    audit = dict(actual_files_read=reads, inlined_files=inlined, missing_files=[], out_of_range_lines=[], unopened_cited_files=[])
    lengths, opened = {}, set(reads) | set(inlined)
    for path, start, end in sorted(refs):
        rel = _normalize_rel_file(path, root)
        base_dir = stage_dir if stage_dir and (stage_dir / rel).is_file() else root
        file = base_dir / rel
        if rel not in lengths:
            lengths[rel] = None
            if not ntpath.isabs(rel) and not rel.startswith('/') and _is_resolved_within(file, base_dir) and file.is_file():
                with contextlib.suppress(OSError):
                    with file.open(encoding='utf-8-sig', errors='replace') as stream:
                        lengths[rel] = sum(1 for _ in stream)
        count = lengths[rel]
        if count is None:
            audit['missing_files'].append(rel)
        elif not 1 <= start <= end <= count:
            audit['out_of_range_lines'].append({'file': rel, 'line_start': start, 'line_end': end, 'line_count': count})
        if rel not in opened:
            audit['unopened_cited_files'].append(rel)
    for name in ('missing_files', 'unopened_cited_files'):
        audit[name] = sorted(set(audit[name]))
    return audit


def _normalize_isolation(raw_isolation='', *, clean_room=False):
    raw = str(raw_isolation or '').strip().lower()
    if raw and raw not in ISOLATION_CHOICES:
        raise HarnessError(f'不支持的隔离模式: {raw!r}（可选：{", ".join(ISOLATION_CHOICES)}）')
    if clean_room:
        if raw and raw != 'clean_room':
            raise HarnessError(f'--clean-room 与 --isolation {raw} 冲突，请仅指定其一')
        return 'clean_room'
    return raw


def _effective_isolation(prefix, raw_isolation='', *, clean_room=False):
    norm = _normalize_isolation(raw_isolation, clean_room=clean_room)
    if norm:
        return norm, True
    return ('no_skills' if prefix == 'agy' else 'full'), False


def _validate_isolation(prefix, mode, readonly):
    if mode == 'clean_room':
        if not readonly:
            raise HarnessError('--clean-room 仅支持只读任务，写入任务必须保留全局安全钩子')
        if prefix == 'agy':
            raise HarnessError('agy 后端不支持剥离全局插件目录，--clean-room 请选用 codex、claude 或 dsh')
    elif mode == 'hooks_only':
        if prefix == 'agy':
            raise HarnessError('agy 后端不支持仅保留钩子并剥离全局规则/技能的 hooks_only 模式，请选用 codex 或 claude')
        if prefix == 'dsh':
            raise HarnessError('dsh 当前为软规则模式（soft_rules_only），不支持 hooks_only 隔离，请选用 no_skills、clean_room 或改用 codex/claude')


def _copy_tree_no_links(src_dir, dst_dir):
    src_dir = Path(src_dir)
    dst_dir = Path(dst_dir)
    if not src_dir.is_dir() or _is_link(src_dir):
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(src_dir)
        kept = []
        for dirname in sorted(dirs):
            if dirname == '__pycache__':
                continue
            cand = root_path / dirname
            if not _is_link(cand):
                kept.append(dirname)
                (dst_dir / rel_root / dirname).mkdir(parents=True, exist_ok=True)
        dirs[:] = kept
        for fname in sorted(files):
            fsrc = root_path / fname
            if not _is_link(fsrc) and fsrc.is_file():
                shutil.copy2(fsrc, dst_dir / rel_root / fname)


def _is_appcontainer_or_interop_codex(info):
    if not isinstance(info, dict):
        return False
    if info.get('windows_interop'):
        return True
    binary_norm = str(info.get('binary') or '').replace('\\', '/').lower()
    return '/windowsapps/' in binary_norm or '/openai/codex/bin/' in binary_norm


def _check_writable_transport(target, info, readonly):
    if not readonly and info.get('windows_interop') and _wsl_path(target).startswith('\\\\'):
        raise HarnessError(f'WSL 纯 Linux 文件系统路径无法在 Windows {Path(info["binary"]).name} 沙箱内直接写入',
                           'wsl_interop_write_unsupported')


def _check_readonly_inline_transport(prefix, info, readonly, inline_readonly, *, is_resume=False):
    if prefix == 'codex' and readonly and not is_resume and inline_readonly is False and _is_appcontainer_or_interop_codex(info):
        raise HarnessError(
            'Windows AppContainer 或 WSL 跨子系统 codex.exe 在 -s read-only 沙箱下会拦截子进程读盘（blocked by policy），'
            '禁止传入 --no-inline-readonly；请开启默认内联只读，超长输入请分片或改用支持直接读盘的本地引擎',
            'inline_readonly_required',
        )


_AGY_NON_PROGRESS_EVENTS = frozenset({
    'init', 'user_input', 'system_message', 'system', 'notice', 'heartbeat', 'ping',
})


def _rmtree_retry(path, *, ignore_errors=False, retries=5, delay=0.1):
    target = Path(path)
    if not os.path.lexists(target):
        return
    last_exc = None
    for attempt in range(max(1, retries)):
        try:
            shutil.rmtree(target)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay * (attempt + 1))
    if not ignore_errors and last_exc is not None:
        raise last_exc


def _turn_made_model_progress(prefix, raw_out, events, raw_file_text=''):
    """判断本回合是否实际进入模型推理或工具阶段（对 agy 解包 step_update 与 result，避免被累计 usage 误导）。"""
    if prefix != 'agy':
        return True
    if (raw_file_text or '').strip():
        return True
    saw_structured_agy = False
    for ev in events or ():
        if not isinstance(ev, dict):
            continue
        ev_name = str(ev.get('event') or ev.get('type') or '').strip().lower()
        if not ev_name:
            continue
        if ev_name == 'step_update':
            saw_structured_agy = True
            su = ev.get('step_update')
            if isinstance(su, dict):
                step_type = str(su.get('step_type') or '').strip().lower()
                if step_type and step_type not in _AGY_NON_PROGRESS_EVENTS:
                    return True
            continue
        if ev_name == 'result':
            saw_structured_agy = True
            res = ev.get('result')
            if isinstance(res, dict):
                if str(res.get('response') or '').strip() or res.get('structured_output') is not None:
                    return True
                finished_reason = str(res.get('finished_reason') or '').strip().lower()
                if finished_reason and finished_reason not in ('print_timeout', 'timeout', 'error', 'cancelled', 'canceled'):
                    return True
            continue
        if ev_name not in _AGY_NON_PROGRESS_EVENTS:
            return True
    if not saw_structured_agy:
        usage = extract_cli_token_usage(prefix, raw_out, '')
        if usage.get('usage_source') != 'unreported_by_cli' and (usage.get('total_tokens') or 0) > 0:
            return True
    return False


def invoke_engine(spec, target_dir, prompt, backends, schema, readonly=True, timeout_sec=600,
                  agent='readonly-collaborator', stage_files=None, effort=None, retry_quota=False,
                  *, profile=None, batch=None, receipt=None, clean_room=False, isolation='', inline_readonly=None,
                  ephemeral=False, plan_dir=None, resume=None, write_scope=()):
    wall_start = time.monotonic()
    wall_deadline = wall_start + timeout_sec
    hd = _headless_tunables()
    persist = bool(resume) or (not ephemeral and hd.get('persist_sessions_by_default', True))
    soft = persist and timeout_sec >= 120
    primary_budget = (min(timeout_sec - hd.get('wrapup_min_sec', 30),
                          max(timeout_sec - hd.get('wrapup_max_sec', 150), int(timeout_sec * hd.get('soft_timeout_ratio', .85))))
                      if soft else timeout_sec)
    batch = batch or _QuotaBatch(retry_quota)
    prefix, _ = _engine_parts(spec)
    iso_mode, _ = _effective_isolation(prefix, isolation, clean_room=clean_room)
    clean_room = iso_mode == 'clean_room'
    _validate_isolation(prefix, iso_mode, readonly)
    info = backends.get(prefix, {})
    if not info.get('available'):
        raise HarnessError(f'未发现所选后端 {prefix}')
    if profile is None:
        _prepare_execution(backends, (spec,), batch)
        info = backends[prefix]
        profile = resolve_execution_profile(backends, spec, effort or 'max')
    _require_execution_profile(profile)
    _require_subscription(prefix, profile.get('model'))
    _, model = _engine_parts(profile['spec'])
    effort = profile['cli_effort']
    receipt = {} if receipt is None else receipt
    receipt.update(requested_engine=profile['requested_engine'], execution_environment=_execution_environment(info),
                   model_requested_to_cli=model or None, actual_model=None, effective_effort=None,
                   declared_effort=profile['declared_effort'], launch_attempted=False, invocation_started=False,
                   provider_session_id=None, target_dir=str(target_dir), engine=profile['spec'], readonly=readonly,
                   write_scope=list(write_scope), files=stage_files, agent_type=agent, tier=profile['tier'],
                   persist_session=persist)
    _enforce_preflight_quota(backends, (spec,), batch=batch)
    binary = info['binary']
    if prefix == 'agy' and not info.get('execution_config_supported', True):
        raise HarnessError('CAE_AGY_HOME 仅用于离线配置探测，执行请使用当前 CLI 的真实用户配置')
    if prefix in ('codex', 'agy', 'claude') and Path(binary).suffix.lower() in ('.cmd', '.bat'):
        raise HarnessError('请通过 CAE_CODEX_BINARY / CAE_AGY_BINARY / CAE_CLAUDE_BINARY 指定原生可执行文件')
    if prefix == 'claude' and info.get('llm_source') == 'local_router':
        _verify_claude_router_card_key(info, model)
    interop = info.get('windows_interop', False)
    _check_writable_transport(target_dir, info, readonly)
    _check_readonly_inline_transport(prefix, info, readonly, inline_readonly, is_resume=bool(resume))
    translate = _wsl_path if interop else str
    needs_stage = clean_room or (interop and translate(target_dir).startswith('\\\\'))
    temp_parent = None
    if interop:
        if not shutil.which('powershell.exe') or not shutil.which('taskkill.exe'):
            raise HarnessError('WSL 跨端调用需要 powershell.exe 与 taskkill.exe 进行进程监督')
        windows_temp = _windows_env('TEMP')
        temp_parent = _wsl_path(Path(windows_temp), windows=False)
    with contextlib.ExitStack() as cleanup:
        resume_id = _safe_state_id(resume['provider_session_id']) if resume else None
        store_id = _safe_state_id(resume['session_store_id']) if resume and resume.get('session_store_id') else uuid.uuid4().hex
        if needs_stage and persist:
            # 续接时先持会话锁再重建同一路径，满足会话对工作目录的校验并防并发冲突。
            resolved_plan = resolve_dispatch_plan_dir(plan_dir)
            cleanup.enter_context(_directory_file_lock(resolved_plan / 'headless_sessions', f'stage_{store_id}', '无头会话暂存'))
            temp_path = Path(temp_parent or tempfile.gettempdir()) / f'cae-harness-{store_id}'
            if temp_path.exists() and not _is_link(temp_path):
                _rmtree_retry(temp_path)
            temp_path.mkdir(mode=0o700)
            cleanup.callback(_rmtree_retry, temp_path, ignore_errors=True)
            receipt.update(session_store_id=store_id, session_plan_dir=str(resolved_plan))
        else:
            temp_path = Path(tempfile.mkdtemp(prefix='cae-harness-', dir=temp_parent))
            cleanup.callback(_rmtree_retry, temp_path, ignore_errors=True)
        schema_file = temp_path / 'schema.json'
        schema_file.write_text(json.dumps(schema, ensure_ascii=False), encoding='utf-8')
        output_file = temp_path / 'result.json'
        env = os.environ.copy()
        for _scrub_k in _CHILD_SCRUB_SESSION_ENV_VARS:
            env.pop(_scrub_k, None)
        env['CAE_HARNESS_DEPTH'] = '1'
        effective_codex_home = info.get('config_home')
        effective_claude_home = info.get('config_home')
        effective_dsh_home = info.get('config_home')
        clean_claude_settings = None
        if iso_mode in ('no_skills', 'hooks_only', 'clean_room'):
            src_home = Path(info['config_home']) if info.get('config_home') else None
            if prefix == 'codex':
                iso_codex = temp_path / ('clean-codex-home' if clean_room else f'iso-{iso_mode}-codex-home')
                iso_codex.mkdir(parents=True, exist_ok=True)
                if src_home and src_home.is_dir():
                    copy_files = ('auth.json', 'models_cache.json')
                    if iso_mode == 'hooks_only':
                        copy_files = ('auth.json', 'models_cache.json', 'config.toml', 'hooks.json')
                    elif iso_mode == 'no_skills':
                        copy_files = ('auth.json', 'models_cache.json', 'config.toml', 'AGENTS.md', 'hooks.json')
                    for fname in copy_files:
                        fsrc = src_home / fname
                        if fsrc.is_file() and not _is_link(fsrc):
                            shutil.copy2(fsrc, iso_codex / fname)
                    if iso_mode in ('hooks_only', 'no_skills'):
                        _copy_tree_no_links(src_home / 'hooks', iso_codex / 'hooks')
                    if iso_mode == 'no_skills':
                        _copy_tree_no_links(src_home / 'agents', iso_codex / 'agents')
                effective_codex_home = str(iso_codex)
            elif prefix == 'claude':
                if iso_mode == 'hooks_only' or (clean_room and persist):
                    iso_claude = temp_path / f'iso-{iso_mode}-claude-home'
                    iso_claude.mkdir()
                    effective_claude_home = str(iso_claude)
                    creds_src = src_home / '.credentials.json' if src_home else None
                    if creds_src and creds_src.is_file() and not _is_link(creds_src):
                        shutil.copy2(creds_src, iso_claude)
                settings_path = src_home / 'settings.json' if src_home else None
                settings = _read_json(settings_path) if settings_path and settings_path.is_file() and not _is_link(settings_path) else {}
                settings = settings if isinstance(settings, dict) else {}
                if clean_room:
                    env_only = {k: settings[k] for k, typ in (('env', dict), ('apiKeyHelper', str))
                                if isinstance(settings.get(k), typ) and settings[k]}
                    clean_claude_settings = temp_path / 'clean-claude-settings.json'
                    clean_claude_settings.write_text(json.dumps(env_only, ensure_ascii=False), encoding='utf-8')
                elif iso_mode == 'hooks_only':
                    hooks_cfg = {k: settings[k] for k in ('env', 'hooks', 'effortLevel', 'permissions', 'apiKeyHelper', 'model')
                                 if k in settings}
                    (iso_claude / 'settings.json').write_text(json.dumps(hooks_cfg, ensure_ascii=False), encoding='utf-8')
                    if src_home:
                        _copy_tree_no_links(src_home / 'hooks', iso_claude / 'hooks')
            elif prefix == 'dsh' and iso_mode in ('no_skills', 'clean_room'):
                iso_dsh = temp_path / ('clean-dsh-home' if clean_room else 'iso-no-skills-dsh-home')
                iso_dsh.mkdir(parents=True, exist_ok=True)
                if src_home and (src_home / '.credentials.yaml').is_file() and not _is_link(src_home / '.credentials.yaml'):
                    shutil.copy2(src_home / '.credentials.yaml', iso_dsh / '.credentials.yaml')
                if iso_mode == 'no_skills' and src_home and (src_home / 'AGENTS.md').is_file() and not _is_link(src_home / 'AGENTS.md'):
                    shutil.copy2(src_home / 'AGENTS.md', iso_dsh / 'AGENTS.md')
                effective_dsh_home = str(iso_dsh)
        if prefix == 'codex' and effective_codex_home and not interop:
            env['CODEX_HOME'] = effective_codex_home
        if prefix == 'claude' and effective_claude_home and not interop:
            env['CLAUDE_CONFIG_DIR'] = effective_claude_home
        if prefix == 'dsh':
            src_home = Path(info.get('config_home') or Path.home() / '.dsh')
            if not _usable_credential(env.get('DEEPSEEK_API_KEY')):
                key_value = _dsh_dotenv_key(src_home)
                if key_value:
                    env['DEEPSEEK_API_KEY'] = key_value
            if info.get('credential_source') == 'account':
                env.pop('DEEPSEEK_API_KEY', None)
            env['DSH_TELEMETRY_DISABLED'] = '1'
            if effective_dsh_home and not interop:
                env['DSH_HOME'] = effective_dsh_home
        exec_dir = target_dir
        stage_dir = stage_before = failure = parsed = None
        inlined, outputs = [], []
        home = {'codex': effective_codex_home, 'claude': effective_claude_home,
                'agy': info.get('config_home'), 'dsh': effective_dsh_home}[prefix]
        if prefix == 'dsh' and not persist:
            home = str(temp_path)
        home = Path(home) if home else None
        isolated = home is not None and home.is_relative_to(temp_path)
        archive = None
        if isolated and persist:
            archive = resolve_dispatch_plan_dir(plan_dir) / 'headless_sessions' / prefix / store_id
            if any(os.path.lexists(p) and _is_link(p) for p in (archive, *archive.parents)):
                raise HarnessError('会话归档路径不能经过链接')
            cleanup.enter_context(_directory_file_lock(archive.parent, store_id, '无头会话归档'))
            if resume and resume.get('session_store_id'):
                if not archive.is_dir():
                    raise HarnessError('找不到原会话归档，请核对 --plan-dir')
                _copy_tree_no_links(archive, home)
            receipt.update(session_store_id=store_id, session_plan_dir=str(archive.parents[2]))
        claude_id = str(uuid.uuid4()) if prefix == 'claude' and persist else None

        def session_logs():
            sid = resume_id or next((sid for _, _, events, _ in outputs for e in events if (sid := _provider_id(e))), None)
            paths = _session_logs(prefix, home, sid or claude_id, isolated)
            if not sid and len(paths) == 1 and paths[0].suffix == '.jsonl':
                with paths[0].open(encoding='utf-8-sig', errors='replace') as stream:
                    sid = _provider_id(next(_json_events([stream.readline()]), {})) or claude_id
            if persist:
                receipt['provider_session_id'] = sid
            return paths

        def _wrapup_model_and_effort():
            if prefix == 'codex':
                rec = _exact_model_record(info.get('models', []), model) if model else {}
                supported = [str(lv).lower() for lv in (rec.get('supported_efforts') or ()) if lv]
                delegating = {str(lv).lower() for lv in (rec.get('delegating_efforts') or ()) if lv}
                non_del = [lv for lv in supported if lv not in delegating]
                for cand in ('low', 'minimal', 'medium'):
                    if not non_del or cand in non_del:
                        return model, cand, cand
                return model, non_del[0], non_del[0]
            if prefix in ('claude', 'dsh'):
                return model, 'low', 'low'
            if prefix == 'agy':
                family, slug_tier = _agy_slug_tier(model)
                if slug_tier:
                    if slug_tier in ('low', 'minimal'):
                        return model, None, slug_tier
                    cands = ('low', 'minimal') if slug_tier == 'medium' else ('low', 'minimal', 'medium')
                    catalog_models = info.get('models') or ()
                    for cand in cands:
                        target_slug = f'{family}-{cand}'
                        rec = next(
                            (m for m in catalog_models
                             if isinstance(m, dict)
                             and str(m.get('slug') or '').strip().lower() == target_slug
                             and _model_category('agy', m) != 'hidden_or_internal'),
                            None,
                        )
                        if rec is not None:
                            return rec['slug'], None, cand
                    return model, effort, slug_tier
                if profile.get('effort_option_supported') is not False:
                    return model, 'low', 'low'
                return model, effort, effort
            return model, effort, effort

        def turn(readonly, exec_prompt, budget, session_id, wrapup=False):
            native_pid_file = None
            win_target = translate(exec_dir)
            turn_output_file = (temp_path / 'result_wrapup.json') if wrapup else output_file
            turn_model, turn_effort, wrapup_effort_label = (
                _wrapup_model_and_effort() if wrapup else (model, effort, None)
            )
            if wrapup:
                receipt['wrapup_effort'] = wrapup_effort_label
                if turn_model and turn_model != model:
                    receipt['wrapup_model'] = turn_model
            agy_print_sec = None
            agy_grace_used = 0
            if prefix == 'codex':
                command = [binary, 'exec', '--json', '-s', 'read-only' if readonly else 'workspace-write',
                           '-c', 'approval_policy="never"',
                           '--skip-git-repo-check', '-C', win_target, '--output-schema', translate(schema_file), '-o', translate(turn_output_file)]
                if turn_model:
                    command.extend(['-m', turn_model])
                if turn_effort:
                    command.extend(['-c', f'model_reasoning_effort="{turn_effort}"'])
                context_window = profile['capacity']['cli_context_window_tokens']
                if context_window:
                    command.extend(['-c', f'model_context_window={context_window}'])
                if not persist:
                    command.append('--ephemeral')
                if session_id:
                    command.extend(['resume', session_id])
                command.append('-')
                input_text = exec_prompt
            elif prefix == 'agy':
                grace = max(1, int(hd.get('agy_timeout_grace_sec', 15)))
                elapsed_pre = time.monotonic() - wall_start
                if not soft:
                    agy_print_sec = max(1, int(timeout_sec - int(elapsed_pre)))
                    agy_grace_used = grace
                elif not wrapup:
                    agy_print_sec = max(1, int(budget - int(elapsed_pre)))
                    headroom = max(1, int(timeout_sec - elapsed_pre - agy_print_sec - 20))
                    agy_grace_used = min(grace, headroom)
                else:
                    agy_print_sec = max(1, int(timeout_sec - int(elapsed_pre)))
                    agy_grace_used = min(grace, 10)
                command = [binary, '--input-format', 'stream-json', '--output-format', 'stream-json',
                           '--print-timeout', f'{agy_print_sec}s', '--json-schema', translate(schema_file)]
                if session_id:
                    command.extend(['--conversation', session_id])
                if iso_mode != 'full':
                    command.append('--disable-slash-commands')
                command.append('--sandbox')
                if turn_model:
                    command.extend(['--model', turn_model])
                if turn_effort and profile.get('effort_option_supported') is not False and not _agy_slug_tier(turn_model)[1]:
                    command.extend(['--effort', turn_effort])
                if readonly:
                    command.extend(['--agent', 'readonly-collaborator' if wrapup else agent])
                    if not clean_room and stage_dir is None:
                        repo_anchor = _enclosing_git_root(exec_dir)
                        if repo_anchor is not None:
                            command.extend(['--add-dir', translate(repo_anchor)])
                else:
                    command.extend(['--mode', 'accept-edits', '--dangerously-skip-permissions'])
                input_text = json.dumps({'event': 'user', 'message': {'content': exec_prompt}}, ensure_ascii=False) + '\n'
            elif prefix == 'claude':
                command = [binary, '-p', '--output-format', 'stream-json', '--verbose',
                           '--json-schema', json.dumps(schema, ensure_ascii=False)]
                command.extend(['--resume', session_id] if session_id else
                               ['--session-id', claude_id] if persist else ['--no-session-persistence'])
                if clean_room:
                    command.extend(['--bare', '--disable-slash-commands'])
                    if persist:
                        command.extend(['--system-prompt-snapshot', 'on'])
                    if clean_claude_settings is not None:
                        command.extend(['--settings', translate(clean_claude_settings)])
                elif iso_mode in ('no_skills', 'hooks_only'):
                    command.append('--disable-slash-commands')
                if model:
                    command.extend(['--model', model])
                if turn_effort in ('low', 'medium', 'high', 'xhigh', 'max'):
                    command.extend(['--effort', turn_effort])
                if readonly:
                    command.extend(['--permission-mode', 'dontAsk', '--tools', 'Read,Grep,Glob', '--permission-prompts', 'none'])
                else:
                    command.extend(['--permission-mode', 'bypassPermissions', '--tools', 'Read,Grep,Glob,Edit,Write,Bash'])
                input_text = exec_prompt
            else:
                supports_perm_flags = _dsh_supports_cli_permission_flags(binary, env, interop=interop)
                mode_str = 'read-only' if readonly else 'workspace-write'
                patch_file = temp_path / 'dsh-headless.patch.yml'
                provider = 'deepseek-official' if info.get('dsh_provider') == 'deepseek-official' else 'deepseek-account'
                dsh_default_slug = str(_catalog_rules('dsh').get('default_model') or info.get('model') or 'default')
                legacy_permissions = (
                    '- id: sandbox-policy\n  config:\n    mode: ' + mode_str + '\n'
                    '- id: approval\n  config:\n    policy: never\n- id: permission\n  config:\n    mode: ' + mode_str + '\n'
                ) if not supports_perm_flags else ''
                storage = ('- id: session-persistence-jsonl\n  config:\n    compression: none\n    root: '
                           + json.dumps(translate(home / 'headless-sessions')) + '\n') if home else ''
                patch_file.write_text(
                    legacy_permissions + storage + '- id: credentials\n  disabled: false\n- id: authorization\n  disabled: false\n'
                    '- id: deepseek-account\n  disabled: false\n- id: agent-default-model\n'
                    "  name: '@deepseek-ai/dsh-agent-default-model'\n  config:\n"
                    f'    provider: {provider}\n    model: {json.dumps(model or dsh_default_slug)}\n'
                    f'    reasoningEffort: {turn_effort or "high"}\n', encoding='utf-8')
                command = [binary, '--profile', 'headless', '--patch', translate(patch_file), '--json']
                if session_id:
                    command.extend(['--session-id', session_id])
                if supports_perm_flags:
                    command.extend(['--permission-mode', mode_str, '--approval-policy', 'never'])
                else:
                    env['DSH_PERMISSION_MODE'] = mode_str
                command.append('-')
                req_keys = ', '.join(schema.get('required', ()))
                exec_prompt = (f'{exec_prompt}\n\n单模型直接完成，严禁在工具内部派生子代理；'
                               f'请仅输出一个以 {{ 开头、以 }} 结尾且严格符合以下 JSON Schema 的单行或多行 JSON 对象'
                               f'（必含顶层键：{req_keys}；JSON 字符串内的反斜杠如 Windows 路径与 LaTeX 公式必须转义为双反斜杠 \\\\），'
                               f'严禁输出外层 Markdown 代码块围栏或任何额外解释文字：\n'
                               f'{json.dumps(schema, ensure_ascii=False)}')
                input_text = exec_prompt
            if not receipt['launch_attempted']:
                receipt.update(argv=list(command), work_dir=str(exec_dir), staged_readonly_workspace=stage_dir is not None,
                               clean_room=clean_room, isolation=iso_mode,
                               inline_readonly=bool(readonly and (inline_readonly is True or (inline_readonly is None and clean_room))),
                               command_transport='windows_interop' if interop else 'native')
            if interop:
                extra_env = {}
                if prefix == 'claude' and effective_claude_home:
                    extra_env['CLAUDE_CONFIG_DIR'] = _wsl_path(Path(effective_claude_home))
                elif prefix == 'dsh' and effective_dsh_home:
                    extra_env = {'DSH_HOME': _wsl_path(Path(effective_dsh_home)), 'DSH_TELEMETRY_DISABLED': '1'}
                    if env.get('DEEPSEEK_API_KEY'):
                        extra_env['DEEPSEEK_API_KEY'] = env['DEEPSEEK_API_KEY']
                    if env.get('DSH_PERMISSION_MODE'):
                        extra_env['DSH_PERMISSION_MODE'] = env['DSH_PERMISSION_MODE']
                command, native_pid_file = _interop_command(command, exec_dir, temp_path, input_text,
                                                            effective_codex_home if prefix == 'codex' else None,
                                                            extra_env=extra_env)
                input_text = None
            # Queued units must recheck after staging. A success already in flight
            # cannot clear a failure recorded by another unit in this same batch.
            _enforce_preflight_quota(backends, (spec,), batch=batch)
            elapsed = time.monotonic() - wall_start
            if prefix == 'agy':
                if elapsed >= timeout_sec:
                    raise HarnessError('前置暂存或处理已耗尽墙钟超时预算', 'timeout')
                proc_timeout = agy_print_sec + agy_grace_used
            else:
                proc_timeout = (timeout_sec if wrapup else budget) - (elapsed if soft else int(elapsed))
                if elapsed >= timeout_sec or proc_timeout <= 0:
                    raise HarnessError('前置暂存或处理已耗尽墙钟超时预算', 'timeout')
            process_failure = None
            receipt['launch_attempted'] = True
            try:
                result = run_process(command, exec_dir, input_text, proc_timeout, env=env, native_pid_file=native_pid_file)
            except HarnessError as exc:
                process_failure = exc
                result = subprocess.CompletedProcess(command, exc.details.get('returncode'),
                                                     exc.details.get('partial_stdout', ''), exc.details.get('partial_stderr', ''))
            receipt['invocation_started'] |= process_failure is None or bool(process_failure.details.get('process_started'))
            raw = _optional_text(turn_output_file)
            events = list(_json_events(result.stdout.splitlines()))
            outputs.append((result.stdout, result.stderr, events, raw))
            receipt['returncode'] = result.returncode
            if process_failure is not None and (result.returncode is None or process_failure.details.get('cleanup_errors')):
                raise process_failure
            startup_stderr = all(not line.strip() or _is_quota_exhausted(line)
                                 for line in (result.stderr or '').splitlines())
            receipt['startup_only'] = (_quota_startup_only(result.stdout) and startup_stderr
                                       and not (turn_output_file.exists() and turn_output_file.stat().st_size))
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
                                           timeout_sec=agy_print_sec, returncode=0,
                                           finished_reason='print_timeout', stderr=redacted_detail[-2000:])
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
                                       timeout_sec=agy_print_sec, returncode=result.returncode,
                                       finished_reason='print_timeout', stderr=redacted_stderr)
                # A worker may already have written files. Authentication fallback
                # is allowed only in model listing before planning and execution.
                raise HarnessError(f'{prefix} 执行失败，退出码 {result.returncode}', returncode=result.returncode,
                                   stderr=redacted_stderr)
            raw = raw or result.stdout
            try:
                parsed = _extract_json_object(raw, schema)
            except HarnessError as exc:
                if exc.status == 'quota_exhausted':
                    detail = str(exc.details.get('error') or raw)[-1000:]
                    cooldown = _record_quota_exhausted(prefix, info, detail, batch=batch)
                    raise HarnessError(f'{prefix} 额度不足或已达用量上限，已停止并记录冷却状态',
                                       'quota_exhausted', preflight_blocked=False,
                                       **_quota_user_choices(prefix, cooldown)) from exc
                if exc.status in ('invalid_output', 'timeout'):
                    redacted_stderr = _redact_runtime_secret(result.stderr or '')[-2000:]
                    redacted_raw = _redact_runtime_secret(raw or '')[-1500:]
                    if redacted_stderr and 'stderr' not in exc.details:
                        exc.details['stderr'] = redacted_stderr
                    if exc.status == 'invalid_output' and redacted_raw and 'raw_output_excerpt' not in exc.details:
                        exc.details['raw_output_excerpt'] = redacted_raw
                    if exc.status == 'invalid_output' and prefix == 'agy' and 'soft-denying' in (result.stderr or '').lower():
                        exc.details.setdefault(
                            'diagnostic_hint',
                            'agy 无头只读模式下触发了工具权限软拒绝（soft-denying），通常因尝试读取工作目录外文件所致，请检查目标目录或 --add-dir 范围。',
                        )
                raise
            _mark_quota_success(prefix, info, batch)
            if prefix == 'claude':
                envelope = next((ev for ev in reversed(events) if ev.get('type') == 'result'), {})
                model_usage = envelope.get('modelUsage') if isinstance(envelope, dict) else None
                if isinstance(envelope, dict):
                    if isinstance(envelope.get('total_cost_usd'), (int, float)):
                        receipt['total_cost_usd'] = envelope['total_cost_usd']
                    if isinstance(model_usage, dict):
                        receipt['modelUsage'] = model_usage
                if isinstance(model_usage, dict) and model_usage:
                    receipt['actual_model'] = next(iter(model_usage))
                    receipt['actual_model_source'] = 'claude_result_model_usage'
            if prefix == 'agy':
                for event in events:
                    init = event.get('init') if event.get('event') == 'init' else None
                    if isinstance(init, dict) and isinstance(init.get('model'), str):
                        receipt['actual_model'] = init['model']
                        receipt['actual_model_source'] = 'agy_init_event'
                        break
            return parsed

        def completed_output():
            out, _, events, raw = outputs[-1]
            candidates = (json.dumps(e.get('item') or e) for e in reversed(events)
                          if e.get('type') in ('item.completed', 'final', 'result') or e.get('event') == 'result')
            for candidate in itertools.chain((raw, out), candidates):
                with contextlib.suppress(ValueError, HarnessError, RecursionError):
                    return _extract_json_object(candidate, schema, strict=True)
            return None

        wrapup_stalled = False
        wrapup_blocked_reason = ''
        try:
            if needs_stage:
                stage_dir = _stage_wsl_readonly_workspace(target_dir, temp_path, stage_files, exclude_agent_instructions=clean_room)
                stage_ignore_patterns = read_agentignore_patterns(stage_dir)
                stage_before = snapshot_files(stage_dir, ignore_patterns=stage_ignore_patterns)
                exec_dir, prompt = stage_dir, prompt + STAGED_PROMPT_SUFFIX
                if clean_room and stage_files and len(stage_files) == 1 and Path(stage_files[0]).name.upper() in _AGENT_INSTRUCTION_FILENAMES:
                    rel = Path(stage_files[0])
                    neutral = rel.with_name(f'{_STAGED_NEUTRAL_PREFIX}{rel.name}{_STAGED_NEUTRAL_SUFFIX}').as_posix()
                    prompt += f'\n[净室单文件映射] 目标文件 {rel.as_posix()} 在当前镜像目录中暂存为 {neutral}。'
            if readonly and (inline_readonly is True or (inline_readonly is None and clean_room)):
                inline_summary = {}
                prompt = _inline_readonly_prompt(prompt, exec_dir, stage_files, exclude_agent_instructions=clean_room,
                                                 included=inlined, summary=inline_summary, prefix=prefix)
                receipt['inline_readonly_summary'] = inline_summary
            if prefix == 'codex' and _is_appcontainer_or_interop_codex(info) and not clean_room and not resume:
                if readonly:
                    prompt += (
                        '\n[Windows Codex 只读沙箱边界] 本沙箱禁止调用 wsl.exe（将返回 Wsl/E_ACCESSDENIED），'
                        '且 PowerShell 处于受限语言模式（禁止 .NET 构造器与非常规静态方法，且可能无 rg）；'
                        '请优先直接基于已内联原文或简单 Get-Content/Select-String 读取。'
                    )
                else:
                    prompt += (
                        '\n[Windows Codex 沙箱边界] 本沙箱禁止调用 wsl.exe（将返回 Wsl/E_ACCESSDENIED）；'
                        '若项目门禁涉及 WSL，请在回执 unverified 中如实披露并按授权范围处理，严禁反复重试 wsl.exe；'
                        '验证或编译产生的临时目录请置于系统临时区或退出前彻底清理，严禁在工作区根目录遗留未授权临时目录。'
                    )
            try:
                parsed = turn(readonly, prompt, primary_budget, resume_id)
            except HarnessError as exc:
                if exc.status != 'timeout' or not soft or exc.details.get('cleanup_errors'):
                    raise
                if 'timeout_sec' in exc.details:
                    exc.details.setdefault('primary_timeout_sec', exc.details['timeout_sec'])
                    exc.details['timeout_sec'] = timeout_sec
                parsed = completed_output() if outputs else None
                if parsed is None:
                    with contextlib.suppress(OSError):
                        session_logs()
                    primary_stderr = str(exc.details.get('stderr') or '')
                    if outputs:
                        primary_stderr = primary_stderr + '\n' + str(outputs[-1][1] or '')
                    if (
                        prefix == 'agy'
                        and exc.details.get('finished_reason') == 'print_timeout'
                        and 'with turn in progress' in primary_stderr.lower()
                    ):
                        wrapup_stalled = True
                        wrapup_blocked_reason = 'agy_background_turn_in_progress'
                        receipt['wrapup_stalled'] = True
                        exc.details['wrapup_stalled'] = True
                        exc.details['wrapup_status'] = 'skipped_background_turn_active'
                        raise
                    sid = receipt['provider_session_id']
                    remaining = wall_deadline - time.monotonic()
                    if not (sid and receipt['invocation_started'] and remaining >= 20 and not _CANCELLED.is_set()):
                        raise
                    try:
                        parsed = turn(
                            True,
                            '立即停止工具调用。仅根据已读上下文和已完成工作，按原 JSON Schema 紧凑收口：'
                            '将总输出控制在约 1500 tokens 以内，只保留核心结论、关键阻断项或改动项的精确文件路径与行号及未完事项，严禁长段复述或全文重写。',
                            int(remaining), sid, wrapup=True,
                        )
                    except HarnessError as wrapup:
                        if wrapup.status in ('cancelled', 'quota_exhausted'):
                            wrapup.details['primary_status'] = exc.status
                            raise
                        parsed = (completed_output() if wrapup.status == 'timeout'
                                  and not wrapup.details.get('cleanup_errors') and len(outputs) > 1 else None)
                        if parsed is None:
                            exc.details['wrapup_status'] = wrapup.status
                            if wrapup.status == 'timeout' and 'timeout_sec' in wrapup.details:
                                exc.details['wrapup_timeout_sec'] = wrapup.details['timeout_sec']
                            if wrapup.details.get('cleanup_errors'):
                                exc.details['cleanup_errors'] = wrapup.details['cleanup_errors']
                            if prefix == 'agy' and wrapup.status == 'timeout' and len(outputs) >= 2:
                                w_out, _, w_events, w_raw = outputs[-1]
                                if not _turn_made_model_progress('agy', w_out, w_events, w_raw):
                                    wrapup_stalled = True
                                    wrapup_blocked_reason = 'agy_wrapup_stalled_without_model_turn'
                                    receipt['wrapup_stalled'] = True
                                    exc.details['wrapup_stalled'] = True
                            raise exc from wrapup
                    receipt['soft_timeout_wrapup'] = True
            if stage_dir is not None:
                parsed = _rewrite_staged_paths(parsed, stage_dir)
            return parsed
        except HarnessError as exc:
            failure = exc
            raise
        finally:
            if stage_dir is not None and stage_before is not None:
                receipt['staged_scope'] = _safe_changes_report(stage_before, stage_dir, [], readonly, ignore_patterns=stage_ignore_patterns)
            paths, reads, messages, totals = [], [], [], None
            try:
                paths = session_logs()
                with contextlib.ExitStack() as logs:
                    events = (e for path in paths if path.suffix == '.jsonl' and not _is_link(path)
                              for e in _json_events(logs.enter_context(path.open(encoding='utf-8-sig', errors='replace'))))
                    events = itertools.chain(events, (e for _, _, stream, _ in outputs for e in stream),
                                             ({'type': 'agent_message', 'text': raw} for _, _, _, raw in outputs if raw))
                    reads, messages, totals = _session_trace(events, target_dir, stage_dir)
            except OSError as exc:
                receipt['session_log_error'] = str(exc)
                reads, messages, totals = _session_trace((e for _, _, stream, _ in outputs for e in stream), target_dir, stage_dir)
            if archive and paths and not receipt.get('session_log_error'):
                try:
                    for path in paths:
                        if not _is_link(path) and path.is_file():
                            _atomic_text(archive / path.relative_to(home), path.read_text(encoding='utf-8-sig', errors='replace'))
                except (OSError, HarnessError) as exc:
                    receipt['session_log_error'] = str(exc)
            timed_out = failure is not None and failure.status == 'timeout'
            usages = [extract_cli_token_usage(prefix, out, err) for out, err, _, _ in outputs]
            if prefix == 'agy' and len(usages) > 1:
                # agy --conversation 续接返回的 result.usage 为全会话累计值，取各字段最大值避免主回合与收口重复叠加。
                reported_items = [u for u in usages if u.get('usage_source') != 'unreported_by_cli']
                reported = len(reported_items)
                usage = {}
                for k in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens'):
                    vals = [u[k] for u in reported_items if type(u.get(k)) is int]
                    usage[k] = max(vals) if vals else None
            else:
                usage = _usage_summary([{'token_usage': u} for u in usages])
                reported = usage.pop('reported_streams')
                usage.pop('unreported_streams')
            if totals and not resume_id and (reported < len(outputs) or timed_out):
                rollup = extract_cli_token_usage(prefix, json.dumps({'usage': totals}))
                if not reported or (rollup['total_tokens'] or 0) >= (usage['total_tokens'] or 0):
                    usage, reported = rollup, int(rollup['usage_source'] == 'cli_reported') * max(1, len(outputs))
            partial = timed_out or (0 < reported < len(outputs))
            usage['usage_source'] = ('cli_partial_on_timeout' if partial else 'cli_reported') if reported else 'unreported_by_cli'
            receipt['token_usage'] = usage
            if failure is not None:
                failure.details.update(partial_stdout='\n'.join(out for out, _, _, _ in outputs),
                                       partial_stderr='\n'.join(err for _, err, _, _ in outputs))
            if timed_out:
                sid = receipt['provider_session_id']
                can_resume = bool(sid and (not isolated or (paths and not receipt.get('session_log_error'))))
                if wrapup_stalled:
                    can_resume = False
                salvage = dict(provider_session_id=sid, resumable=can_resume,
                               actual_files_read=reads, changed_files_on_disk=None, last_messages=messages)
                if wrapup_stalled:
                    reason_code = wrapup_blocked_reason or 'agy_wrapup_stalled_without_model_turn'
                    salvage['resume_blocked_reason'] = reason_code
                    if reason_code == 'agy_background_turn_in_progress':
                        salvage['resume_warning'] = (
                            'agy 主回合触发 --print-timeout 时后台回合仍在执行且 CLI 未取消该回合，'
                            '立即同会话续接将被阻塞卡死；已跳过收口并禁止继续 followup，请按分片缩减输入后新开会话。'
                        )
                    else:
                        salvage['resume_warning'] = (
                            'agy 收口续接超时且未进入模型推理（0 token 且无模型响应事件），'
                            '原会话状态疑似异常或已锁死；禁止继续 followup，请按分片缩减输入后新开会话。'
                        )
                receipt['timeout_salvage'] = failure.details['timeout_salvage'] = salvage
            if readonly and parsed is not None:
                receipt['citation_audit'] = _citation_audit(parsed, target_dir, reads, inlined, stage_dir=stage_dir)
            receipt['elapsed_sec'] = round(time.monotonic() - wall_start, 6)


def invoke_readonly_engine(spec, target_dir, prompt, backends, timeout_sec=600, stage_files=None, effort=None, retry_quota=False,
                           *, profile=None, batch=None, receipt=None, inline_readonly=True, isolation='', clean_room=False, **session):
    return invoke_engine(spec, target_dir, prompt, backends, AUDIT_JSON_SCHEMA, True, timeout_sec,
                         'readonly-auditor', stage_files, effort, retry_quota, profile=profile, batch=batch, receipt=receipt,
                         clean_room=clean_room, isolation=isolation, inline_readonly=inline_readonly, **session)


def invoke_edit_worker(spec, target_dir, prompt, backends, timeout_sec=600, effort=None, retry_quota=False,
                       *, profile=None, batch=None, receipt=None, isolation='', stage_files=None, **session):
    result = invoke_engine(spec, target_dir, prompt, backends, TASK_JSON_SCHEMA, False, timeout_sec,
                           'readonly-collaborator', stage_files, effort, retry_quota, profile=profile, batch=batch, receipt=receipt,
                           isolation=isolation, **session)
    return json.dumps(result, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# ⑥ 工作区快照、分片审计规划与双盲归并
# ════════════════════════════════════════════════════════════════════════════


def _snapshot_ignored_dirs():
    return set(_headless_tunables().get('snapshot_ignored_dirs',
               ['.git', '.venv', 'venv', 'build', 'dist', 'runs', '__pycache__', 'node_modules', '.cache', '.julia']))


def read_agentignore_patterns(root):
    root = Path(root)
    ignore_file = root / '.agentignore'
    try:
        ignore_text = _optional_text(ignore_file) if ignore_file.is_file() and not _is_link(ignore_file) else ''
    except OSError:
        ignore_text = ''
    patterns = []
    for raw_line in ignore_text.lstrip('\ufeff').splitlines():
        line = raw_line.lstrip('\ufeff').strip()
        if not line or line.startswith('#'):
            continue
        line = line.replace('\\', '/')
        while line.startswith('./'):
            line = line[2:]
        line = line.rstrip('/')
        if line and line != '.':
            patterns.append(line)
    return patterns


_FAST_SNAPSHOT_FILE_THRESHOLD = 500
_FAST_SNAPSHOT_BYTES_THRESHOLD = 32 * 1024 * 1024


def snapshot_files(root, ignore_dirs=None, ignore_patterns=None, *, fast_stat=None):
    """对独立任务目录作事后核对，排除 Git 元数据，不撤销文件。大目录自动切换元数据快快照避免全盘哈希耗时。"""
    root = Path(root)
    ignored = set(ignore_dirs) if ignore_dirs is not None else _snapshot_ignored_dirs()
    patterns = list(ignore_patterns) if ignore_patterns is not None else read_agentignore_patterns(root)
    def excluded(path):
        rel = path.relative_to(root).as_posix()
        if rel == '.agentignore':
            return False
        return any(fnmatch.fnmatchcase(rel, pattern) or rel.split('/')[0] == pattern for pattern in patterns)

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
    regular_files = []
    total_regular_bytes = 0
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=traversal_failed):
        dirs[:] = sorted(d for d in dirs if d not in ignored and not excluded(Path(directory) / d))
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
            if path == root / '.git' or excluded(path):
                continue
            rel = path.relative_to(root).as_posix()
            try:
                st = path.lstat()
                if _is_link(path, st=st):
                    result[rel] = 'symlink:' + os.readlink(path)
                elif not stat.S_ISREG(st.st_mode):
                    raise HarnessError(f'工作区包含非常规特殊文件，无法安全快照: {rel}', snapshot_path=rel)
                else:
                    regular_files.append((rel, path, st))
                    raw_size = getattr(st, 'st_size', 0)
                    if isinstance(raw_size, int) and not isinstance(raw_size, bool):
                        total_regular_bytes += raw_size
            except OSError as exc:
                raise HarnessError(f'无法核对文件 {rel}: {exc}', snapshot_path=rel) from exc
    use_fast_stat = (
        bool(fast_stat)
        if fast_stat is not None
        else (len(regular_files) > _FAST_SNAPSHOT_FILE_THRESHOLD or total_regular_bytes > _FAST_SNAPSHOT_BYTES_THRESHOLD)
    )
    for rel, path, st in regular_files:
        if use_fast_stat:
            result[rel] = f'stat:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}:{stat.S_IMODE(st.st_mode)}'
        else:
            try:
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                result[rel] = digest.hexdigest() + ':' + str(stat.S_IMODE(st.st_mode))
            except OSError as exc:
                raise HarnessError(f'无法核对文件 {rel}: {exc}', snapshot_path=rel) from exc
    return result


def _scope_patterns(patterns):
    if patterns is None:
        return []
    if isinstance(patterns, str):
        patterns = [patterns]
    elif not isinstance(patterns, (list, tuple, set, frozenset)):
        raise HarnessError('--write-scope 必须为字符串或字符串列表')
    result = []
    for raw in patterns:
        if not isinstance(raw, str):
            raise HarnessError('--write-scope 元素必须为字符串')
        items = [part.strip() for part in raw.split(',')] if ',' in raw else [raw]
        for pattern in items:
            raw_norm = pattern.strip().replace('\\', '/')
            had_trailing_slash = raw_norm.endswith('/') and raw_norm != '/'
            if not raw_norm or raw_norm.startswith('/') or ':' in raw_norm or '..' in raw_norm.split('/'):
                raise HarnessError('--write-scope 必须使用目标目录内的相对路径或通配符')
            normalized = posixpath.normpath(raw_norm)
            if normalized == '.':
                raise HarnessError('--write-scope 不可为空路径')
            result.append(normalized + '/*' if had_trailing_slash else normalized)
    return result


def _expand_globstar_variants(pat):
    variants = {pat}
    pending = [pat]
    while pending:
        cur = pending.pop()
        nexts = set()
        idx = 0
        while True:
            pos = cur.find('/**/', idx)
            if pos < 0:
                break
            nexts.add(cur[:pos] + '/' + cur[pos + 4:])
            idx = pos + 1
        if cur.startswith('**/'):
            nexts.add(cur[3:])
        if cur.endswith('/**'):
            nexts.add(cur[:-3])
            nexts.add(cur[:-2] + '*')
        for cand in nexts - variants:
            variants.add(cand)
            pending.append(cand)
    return variants


def _scope_matches(path, scopes, casefold=False):
    def key(value):
        return value.casefold() if (casefold or os.name == 'nt') else value

    target = key(path.rstrip('/'))
    for pattern in scopes:
        pat = key(pattern)
        variants = _expand_globstar_variants(pat)
        if any(fnmatch.fnmatchcase(target, cand) for cand in variants):
            return True
        if pat.endswith('/*') and target == pat[:-2]:
            return True
        if not any(ch in pattern for ch in '*?[]') and target.startswith(pat + '/'):
            return True
    return False


def _scope_subset_of(req_scopes, allowed_scopes, target_root=None):
    reqs = _scope_patterns(req_scopes)
    allows = _scope_patterns(allowed_scopes)
    if not reqs and not allows:
        return True
    if not reqs or not allows:
        return False
    root_path = Path(target_root).expanduser().resolve() if target_root is not None else None

    def key(value):
        return value.casefold() if os.name == 'nt' else value

    def single_covered(req_pat, allow_pat):
        rk, ak = key(req_pat), key(allow_pat)
        if rk == ak or ak in ('*', '**'):
            return True
        # Subtree wildcard or directory prefix on allow_pat
        subtree_prefix = None
        if not any(ch in allow_pat for ch in '*?[]'):
            allow_is_existing_file = bool(root_path is not None and (root_path / allow_pat).is_file())
            if not allow_is_existing_file:
                subtree_prefix = ak
        elif ak.endswith('/*') and not any(ch in allow_pat[:-2] for ch in '*?[]'):
            subtree_prefix = ak[:-2]
        elif ak.endswith('/**') and not any(ch in allow_pat[:-3] for ch in '*?[]'):
            subtree_prefix = ak[:-3]
        if subtree_prefix is not None:
            return rk == subtree_prefix or rk.startswith(subtree_prefix + '/')
        # Concrete file path on req_pat against glob allow_pat
        if not any(ch in req_pat for ch in '*?[]'):
            if root_path is not None and (root_path / req_pat).is_dir():
                return False
            return _scope_matches(req_pat, [allow_pat])
        if '[' in req_pat or '[' in allow_pat:
            return False
        s1 = 'zzprobe0'
        s2 = 'qqprobe9'
        while s1 in ak or s1 in rk:
            s1 += 'x'
        while s2 in ak or s2 in rk:
            s2 += 'y'
        for star_sub, q_sub in ((s1, '0'), (s2 + '/nested', '9'), (s1 + '_' + s2, 'z')):
            probe = re.sub(r'\*{2,}', star_sub, req_pat)
            probe = probe.replace('*', star_sub).replace('?', q_sub)
            if not _scope_matches(probe, [allow_pat]):
                return False
        return True

    return all(any(single_covered(r, a) for a in allows) for r in reqs)


def changed_files(before, after):
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    # 文件变化已能代表其新建/删除的父目录；单独保留空目录或目录权限变化。
    return sorted(path for path in changed if not path.endswith('/')
                  or (path in before and path in after)
                  or not any(other != path and other.startswith(path) for other in changed))


def _snapshot_ignore_dirs_for(scopes=(), *, include_build=False):
    heads = [scope.split('/')[0] for scope in (scopes or ()) if scope]
    ignored = {
        d for d in _snapshot_ignored_dirs()
        if not any(
            d == h or (d != '.git' and (
                fnmatch.fnmatchcase(d.casefold(), h.casefold()) if os.name == 'nt' else fnmatch.fnmatchcase(d, h)
            ))
            for h in heads
        )
    }
    if include_build:
        ignored.discard('build')
    return ignored


def _changes_report(before, root, scopes, readonly, *, ignore_dirs=None, ignore_patterns=None):
    before_uses_stat = any(
        isinstance(val, str) and val.startswith('stat:')
        for key, val in (before or {}).items()
        if key != '.' and not key.endswith('/')
    )
    after = snapshot_files(root, ignore_dirs=ignore_dirs, ignore_patterns=ignore_patterns, fast_stat=before_uses_stat)
    changes = changed_files(before, after)
    violations = changes if readonly else [path for path in changes if scopes and not _scope_matches(path, scopes)]
    return {'changed_files': changes, 'scope_violations': violations, 'scope_check': 'post_execution_snapshot',
            'scope_notice': '需使用独立任务目录，快照无法区分并发写入来源，检查不自动回滚。'}


def _safe_changes_report(before, root, scopes, readonly, *, ignore_dirs=None, ignore_patterns=None):
    try:
        report = _changes_report(
            before, root, scopes, readonly, ignore_dirs=ignore_dirs, ignore_patterns=ignore_patterns
        )
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


def _workspace_unchanged(before, root, ignore_dirs=None, ignore_patterns=None):
    if before is None:
        return False
    scope = _safe_changes_report(before, root, [], True, ignore_dirs=ignore_dirs, ignore_patterns=ignore_patterns)
    return scope['scope_verified'] and not scope['changed_files']


def _enclosing_git_root(path):
    """若 path 位于某 Git 仓库子目录内，返回严格上级仓库根目录；否则返回 None。"""
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return None
    cur = resolved.parent
    while cur and cur != cur.parent:
        if _is_link(cur):
            return None
        git_marker = cur / '.git'
        try:
            if git_marker.exists() and not _is_link(git_marker):
                return cur
        except OSError:
            return None
        cur = cur.parent
    return None


def estimate_tokens_from_bytes(num_bytes):
    return max(1, (num_bytes + 2) // 3) if num_bytes else 0


_TARGET_METRICS_MAX_FILES = 20000


def collect_target_files(target, skipped_files=None, *, verify_readable=True, respect_agentignore=False, max_files=None):
    target = target.resolve()
    skipped = skipped_files if skipped_files is not None else []
    patterns = read_agentignore_patterns(target) if (respect_agentignore and target.is_dir()) else []

    def _excluded_by_agentignore(path):
        if not patterns:
            return False
        rel = path.relative_to(target).as_posix()
        if rel == '.agentignore':
            return False
        return any(fnmatch.fnmatchcase(rel, pat) or rel.split('/')[0] == pat for pat in patterns)

    candidates = [target] if target.is_file() else []
    if target.is_dir():
        for root, dirs, files in os.walk(target):
            dirs[:] = sorted(
                d for d in dirs
                if d not in IGNORED_DIRS
                and (d == '.github' or not d.startswith('.'))
                and not _excluded_by_agentignore(Path(root) / d)
            )
            for name in list(dirs):
                directory = Path(root) / name
                if _is_link(directory):
                    skipped.append({'file': directory.relative_to(target).as_posix(), 'reason': 'directory_link'})
                    dirs.remove(name)
            for name in sorted(files):
                if Path(name).suffix.lower() not in SOURCE_EXTENSIONS and name not in NAMED_TEXT_FILES:
                    continue
                cand = Path(root) / name
                if _excluded_by_agentignore(cand):
                    continue
                candidates.append(cand)
                if max_files is not None and len(candidates) >= max_files:
                    break
            if max_files is not None and len(candidates) >= max_files:
                break
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
            if verify_readable:
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
    except (OSError, RuntimeError, ValueError):
        return posixpath.normpath(cleaned)


def _build_audit_prompt(unit, focus):
    files = '\n'.join('- ' + path for path in unit['files'])
    return ('在当前目录按指定范围只读审查，给出文件和真实行号。不要修改文件，严禁在目标工作目录外创建或改动任何文件或目录（含严禁 git worktree add），且不要另行调用本编排器。\n重点: '
            + (focus or '物理守恒、边界条件、数值稳定性及代码一致性')
            + '\n分级标准: 仅将具有明确触发或复现链路的实质缺陷标为 critical/high（阻断级），其余细节与风格建议标为 medium/low（参考级），不借极端假想反复打回。\n计划文件清单:\n'
            + files
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


def _same_backend_heterogeneous_right(prefix, left_spec, backends, prompt_text='', *, ignore_cooldown=False):
    if prefix != 'agy' or _extract_prompt_model_hint(prompt_text):
        return None
    if not _peer_auto_ready(backends, 'agy', ignore_cooldown=ignore_cooldown):
        return None
    agy_rules = _catalog_rules('agy')
    pair_tags = list(dict.fromkeys([
        *(str(t).strip().lower() for t in (agy_rules.get('deep_families') or ()) if str(t).strip()),
        *(str(t).strip().lower() for t in (agy_rules.get('code_families') or ()) if str(t).strip()),
    ]))
    if len(pair_tags) < 2:
        return None
    try:
        left_prof = resolve_execution_profile(backends, left_spec, 'max', prompt_text, 'audit')
    except HarnessError:
        return None
    left_slug = str(left_prof.get('model') or '')
    if not left_slug or left_prof.get('execution_blocked_reason'):
        return None
    left_tokens = _tokens_lower(left_slug)
    for tag in pair_tags:
        if tag in left_tokens:
            continue
        alt_spec = f'agy:{tag}'
        try:
            alt_prof = resolve_execution_profile(backends, alt_spec, 'max', '', 'audit')
        except HarnessError:
            continue
        if (alt_prof.get('model') and not alt_prof.get('execution_blocked_reason')
                and alt_prof.get('spec') != left_prof.get('spec')):
            return alt_spec
    return None


def _audit_engines(backends, dual_blind, left_override, right_override, allow_unconfigured, caller, prompt_text='', *,
                   ignore_cooldown=False):
    negated = set(_extract_negated_backends(prompt_text))
    pos_backend = _extract_prompt_backend_hint(prompt_text)
    if pos_backend and pos_backend not in negated:
        negated.update(peer for peer in ('agy', 'codex', 'claude', 'dsh') if peer != pos_backend)
    if not left_override and caller == 'caller' and not pos_backend:
        available = [name for name in ('codex', 'agy', 'claude', 'dsh')
                     if name not in negated and _peer_auto_ready(backends, name, ignore_cooldown=ignore_cooldown)]
        if not available:
            if allow_unconfigured:
                return None, None
            raise HarnessError('未发现已就绪的 agy、codex、claude 或 dsh 后端')
        left_override = available[0] + ':default'
    left = choose_engine(backends, caller, left_override, prompt_text, ignore_cooldown=ignore_cooldown)
    right = None
    if dual_blind:
        name, _ = _engine_parts(left)
        candidates = [peer for peer in ('agy', 'codex', 'claude', 'dsh') if peer != name and peer not in negated]
        ready_other = next((peer for peer in candidates if _peer_auto_ready(backends, peer, ignore_cooldown=ignore_cooldown)), None)
        if not right_override and ready_other is None:
            same_het = _same_backend_heterogeneous_right(
                name, left, backends, prompt_text, ignore_cooldown=ignore_cooldown,
            )
            if same_het:
                return left, same_het
        if not right_override and not candidates:
            raise HarnessError('提示词已限定单一候选后端且该端无异构双模型，请加 --single 或允许跨端双路审查')
        other = ready_other or (candidates[0] if candidates else ('agy' if name == 'codex' else 'codex'))
        right_prompt = prompt_text if (right_override or '').strip().lower().startswith('role:') else ''
        right = choose_engine(backends, caller, right_override or other + ':default', right_prompt,
                              ignore_cooldown=ignore_cooldown)
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
                          'severity_divergent': item['severity'] != other['severity'],
                          'r6_verification_required': True,
                          'reported_by': [left_engine, right_engine], 'left_description': item['description'],
                          'right_description': other['description'], 'left_finding': item, 'right_finding': other,
                          'match_basis': 'location_overlap_only', 'independently_verified': False})
    singles.extend({**item, 'reported_by': [right_engine], 'independently_verified': False,
                    'r6_verification_required': True}
                   for i, item in enumerate(right) if i not in matched)
    for item in singles:
        item.setdefault('r6_verification_required', True)
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
        verify_env = {
            k: v for k, v in os.environ.items()
            if k not in _GIT_REPO_ENV_VARS and k not in _CHILD_SCRUB_SESSION_ENV_VARS
        }
        verify_env['CAE_HARNESS_DEPTH'] = '1'
        result = run_process(command, directory, timeout_sec=timeout_sec, shell=True, env=verify_env, check=False)
        return result.returncode, (result.stdout + '\n' + result.stderr).strip()

    verifier = verify_fn or default_verify
    scopes = _scope_patterns(write_scope or [])
    # Gate 必须记录验收生成的 build 产物，并检查工作代理是否越界修改它们。
    # 其余缓存目录沿用默认排除配置；显式授权的顶层目录重新纳入快照。
    ignored = _snapshot_ignore_dirs_for(scopes, include_build=True)
    ignore_patterns = read_agentignore_patterns(target_dir)
    worker_changes = set()
    verification_changes = set()
    violations = set()
    history = []
    status = 'verification_failed'
    last_error = ''
    scope_failure = {}

    def checked_snapshot(phase, round_number=None):
        try:
            return snapshot_files(target_dir, ignore_dirs=ignored, ignore_patterns=ignore_patterns)
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
                status = 'ok' if code == 0 else 'verification_failed'
                entry.update(verify_exit_code=code, verify_output_tail=output[-2000:], passed=code == 0, status=status)
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
        if status in ('ok', 'failed', 'cancelled', 'timeout', 'scope_violation', 'invalid_output', 'quota_exhausted'):
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


def _input_envelope(args, target, stream=None):
    stream = stream or {}
    root = target if target.is_dir() else target.parent
    files = stream.get('files', getattr(args, 'files', []))
    negative = stream.get('negative_scope_files', getattr(args, 'negative_scope_file', []))
    contract = stream.get('env_contract', '')
    if not isinstance(files, list) or not isinstance(negative, list) or not isinstance(contract, str):
        raise HarnessError('files 与 negative_scope_files 必须为路径列表，env_contract 必须为字符串')
    negative = _scope_patterns(negative)
    selected = [target.name] if target.is_file() else []
    resolved_root = root.resolve()
    for value in files:
        rel = _literal_input_path(value)
        path = root / rel
        if (not path.is_file() or not path.resolve().is_relative_to(resolved_root)
                or any(_is_link(p) for p in (path, *path.parents) if root in p.parents)):
            raise HarnessError(f'files 仅接受目标目录内无链接的具体文件: {value}')
        selected.append(rel)
    return list(dict.fromkeys(selected)), negative, contract


def _literal_input_path(value):
    path = value.replace('\\', '/') if isinstance(value, str) else ''
    if not path or path.startswith('/') or ntpath.isabs(path) or ntpath.splitdrive(path)[0] or '..' in path.split('/') or '\0' in path:
        raise HarnessError(f'files 必须使用目标目录内的相对路径: {value!r}')
    return posixpath.normpath(path)


def _input_contract(files, negative, contract):
    return (('\n指定输入文件：' + ', '.join(files)) if files else '') + (
        '\n禁止修改：' + ', '.join(negative) if negative else '') + ('\n环境约定：' + contract if contract else '')


def _task_prompt(kind, caller, target, readonly, scopes, text, boundary):
    location = str(target) + (f'（相对路径: {target.name}）' if target.is_file() else '')
    access = '只读完成任务，正文通过回执返回。' if readonly else f"允许写入范围 {scopes or '调用方授权的目标工作目录'}。"
    polish = ('【润色防幻觉硬约束】严禁新增原稿与任务说明中不存在的陌生学科术语、虚构算例名称（如未在代码中出现的领域词）、'
              '未声明的命令行参数、文件名或无依据的定量数字；仅优化表达清晰度与逻辑结构。\n' if kind == 'polish' else '')
    review = ('【审查分级】仅将具有明确失效或复现链路的实质缺陷标为阻断级，其余细节建议仅作参考记录，不借极端假想反复打回。\n'
              if kind == 'review' else '')
    return (f'任务类型 {kind}。当前总控 {caller}。工作目录为当前目录。任务目标 {location}。\n' + access
            + '严禁在目标工作目录外创建、修改或删除任何文件或目录（含严禁向目录外执行 git worktree add）。'
            + '不要调用本编排器，不要修改其他会话或全局配置。\n' + boundary + '\n' + polish + review + text
            + '\n按给定 JSON Schema 返回正文、证据及未验证事项。')


def _raw_task_body(args):
    if getattr(args, 'task_file', None):
        raw = Path(args.task_file).expanduser().resolve().read_text(encoding='utf-8-sig')
    else:
        raw = getattr(args, 'task', None) or ''
    return raw.strip()


def _task_text(args):
    text = _raw_task_body(args)
    if not text:
        raise HarnessError('任务内容为空')
    return (getattr(args, 'user_prompt', '') + '\n' + text).strip()


def _git_head(root):
    try:
        result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                env=_clean_git_env(), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _target_metrics(requested_target, text, *, write_scope=None, cache=None):
    prompt_tokens = estimate_tokens_from_bytes(len((text or '').encode('utf-8')))
    try:
        target = Path(requested_target).expanduser().resolve()
        if target.is_file():
            try:
                size = target.stat().st_size
            except OSError:
                size = 0
            return 1, estimate_tokens_from_bytes(size) + prompt_tokens
        scopes = []
        if write_scope:
            raw_scopes = [write_scope] if isinstance(write_scope, str) else list(write_scope)
            scopes = [s.strip().replace('\\', '/') for s in raw_scopes if isinstance(s, str) and s.strip()]
        cache_key = (str(target), tuple(scopes))
        if isinstance(cache, dict) and cache_key in cache:
            base_count, base_tokens = cache[cache_key]
            return base_count, base_tokens + prompt_tokens
        # 当可写任务已显式声明具体 write_scope 文件（无通配符）时，按白名单估算，避免对大型算例目录做无关全盘扫描。
        if scopes and all(not any(ch in pat for ch in '*?[') for pat in scopes):
            scoped_count, scoped_tokens, any_resolved = 0, 0, False
            for rel in scopes:
                cand = (target / rel)
                try:
                    resolved_cand = cand.resolve()
                    resolved_cand.relative_to(target)
                except (OSError, ValueError):
                    continue
                if _is_link(cand) or not cand.exists():
                    continue
                any_resolved = True
                if cand.is_file():
                    try:
                        scoped_tokens += estimate_tokens_from_bytes(cand.lstat().st_size)
                        scoped_count += 1
                    except OSError:
                        continue
                elif cand.is_dir():
                    sub_recs = collect_target_files(
                        cand, verify_readable=False, respect_agentignore=True, max_files=_TARGET_METRICS_MAX_FILES
                    )
                    scoped_count += len(sub_recs)
                    scoped_tokens += sum(item['tokens'] for item in sub_recs)
            if any_resolved:
                base_count = max(1, scoped_count)
                if isinstance(cache, dict):
                    cache[cache_key] = (base_count, scoped_tokens)
                return base_count, scoped_tokens + prompt_tokens
        # 目录体量估算仅读元数据（lstat），不逐个 open() 文件，并遵循 .agentignore 避免大结果目录拖垮磁盘 I/O。
        records = collect_target_files(
            target, verify_readable=False, respect_agentignore=True, max_files=_TARGET_METRICS_MAX_FILES
        )
        base_count = max(1, len(records))
        base_tokens = sum(item['tokens'] for item in records)
        if isinstance(cache, dict):
            cache[cache_key] = (base_count, base_tokens)
        return base_count, base_tokens + prompt_tokens
    except (HarnessError, OSError):
        return 1, prompt_tokens


def _prepare_execution(backends, specs, batch, *, dry_run=False):
    if dry_run:
        return
    # Check all selected sides before sending even a model-listing request.
    _enforce_preflight_quota(backends, specs, batch=batch)
    for spec in specs:
        if not spec:
            continue
        prefix, model = _engine_parts(spec)
        _require_subscription(prefix, model)
        local_quota = inspect_backend_quota_and_availability(prefix, backends[prefix], model)
        if local_quota['quota_exceeded'] and not batch.retry:
            raise HarnessError('本地额度检查拒绝执行', 'quota_exhausted', quota_backend=prefix, preflight_blocked=True)
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
    user_prompt = getattr(args, 'user_prompt', '')
    ticket = getattr(args, '_approved_ticket', None)
    explicit_eng = getattr(args, 'engine', '') or getattr(args, 'worker', '') or getattr(args, 'left', '')
    skip_prompt_tier = bool(ticket) and getattr(args, 'tier', 'auto') != 'auto'
    skip_prompt_engine = bool(ticket) and bool(explicit_eng) and not explicit_eng.strip().lower().startswith('role:')
    if user_prompt and not skip_prompt_tier:
        selected, selected_reason = _resolve_effective_tier(args.tier, kind, user_prompt, text, engine)
        if selected_reason.startswith(('user_description_', 'economy_')):
            tier, reason = selected, selected_reason
    if skip_prompt_engine:
        raw_body = _raw_task_body(args)
        ticket_streams = ticket.get('streams') if isinstance(ticket, dict) else ()
        allow_fuzzy = any(
            (s.get('execution_profile') or {}).get('profile_source') == 'prompt_fuzzy_model'
            for s in (ticket_streams or ()) if isinstance(s, dict)
        )
        prof_text = '' if (not allow_fuzzy and _extract_prompt_model_hint(raw_body)) else raw_body
        profile = resolve_execution_profile(backends, engine, tier, prof_text, kind, estimated_tokens)
        if (
            ticket_streams
            and not any((s.get('execution_profile') or {}).get('spec') == profile['spec']
                        for s in ticket_streams if isinstance(s, dict))
            and user_prompt
            and not _extract_prompt_model_hint(user_prompt)
        ):
            alt_prof = resolve_execution_profile(backends, engine, tier, user_prompt or text, kind, estimated_tokens)
            if any((s.get('execution_profile') or {}).get('spec') == alt_prof['spec']
                   for s in ticket_streams if isinstance(s, dict)):
                profile = alt_prof
    else:
        profile = resolve_execution_profile(backends, engine, tier, user_prompt or text, kind, estimated_tokens)
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
                                  profile=current, batch=batch, receipt=invocation,
                                  isolation=getattr(args, 'isolation', ''), stage_files=getattr(args, '_stage_files', None),
                                  ephemeral=getattr(args, 'ephemeral', False), plan_dir=getattr(args, 'plan_dir', ''),
                                  write_scope=scopes)

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


def _gate_can_replay(result, before, root, ignore_dirs=None, ignore_patterns=None):
    """第二轮、验收已执行、出现过改动或快照无法核对时，均不可自动重放。"""
    if (before is None or result.get('scope_verified') is not True
            or result.get('changed_files') != [] or result.get('worker_changed_files')
            or result.get('verification_changed_files') or result.get('rounds_used') != 1):
        return False
    entry = result['history'][0]
    if (entry.get('verification_executed') or entry.get('verify_exit_code') is not None
            or not _quota_can_replay(entry, entry.get('invocation', {}))):
        return False
    scope = _safe_changes_report(before, root, [], True, ignore_dirs=ignore_dirs, ignore_patterns=ignore_patterns)
    return scope['scope_verified'] and not scope['changed_files']


def _run_gate_with_fallback(args, root, requested_target, text, caller, engine, backends,
                            scopes, profile, kind, estimated_tokens, batch, meta, before,
                            *, ignore_dirs=None, ignore_patterns=None):
    result = _run_cli_gate(args, root, requested_target, text, caller, engine, backends,
                           scopes, profile, kind, estimated_tokens, batch)
    if (result.get('status') != 'quota_exhausted' or not getattr(args, 'fallback_on_quota', False)
            or getattr(args, '_approved_ticket', None) or 'quota_fallback' in meta
            or not _gate_can_replay(result, before, root, ignore_dirs=ignore_dirs, ignore_patterns=ignore_patterns)):
        return result
    fallback = _local_fallback_engine(caller, engine, backends, text, isolation=getattr(args, 'isolation', ''))
    if not fallback:
        return result
    meta['quota_fallback'] = _quota_fallback_record(engine, fallback, result)
    try:
        _prepare_execution(backends, (fallback,), batch)
        if not _workspace_unchanged(before, root, ignore_dirs=ignore_dirs, ignore_patterns=ignore_patterns):
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
    summary = {
        'profile_id': prof.get('profile_id'),
        'evaluated_at': prof.get('evaluated_at'),
        'evaluation_policy': prof.get('evaluation_policy'),
        'roles': prof.get('roles'),
        'excluded_or_retiring': prof.get('excluded_or_retiring'),
        'runtime_tunables': prof.get('runtime_tunables'),
    }
    if isinstance(prof.get('native_agent_profiles'), dict):
        summary['native_agent_profiles'] = prof['native_agent_profiles']
    return summary


_GROUNDING_GENERIC_ACRONYMS = frozenset({
    'API', 'CLI', 'CPU', 'GPU', 'RAM', 'OOM', 'ROM', 'SSD', 'SSH', 'SSL', 'TLS', 'TCP', 'UDP', 'DNS', 'URL', 'URI',
    'HTTP', 'HTTPS', 'JSON', 'JSONL', 'YAML', 'TOML', 'XML', 'HTML', 'CSS', 'SVG', 'PNG', 'JPG', 'PDF', 'DER', 'UTF',
    'BOM', 'CRLF', 'EOF', 'EOL', 'AST', 'PID', 'TID', 'UID', 'GID', 'WSL', 'POSIX', 'GNU', 'MIT', 'BSD', 'SHA', 'RSA',
    'LLM', 'IDE', 'SDK', 'MCP', 'SSE', 'RPC', 'IPC', 'ABI', 'ENV', 'CWD', 'TMP', 'DIR', 'WIN', 'MAC', 'APP',
    'PRO', 'MAX', 'MIN', 'AVG', 'TTL', 'LRU', 'RPM', 'TPM', 'QPS', 'TUN', 'NAT', 'LAN', 'WAN', 'VPN', 'RFC', 'ISO',
    'DOI', 'UTC', 'GMT', 'CSC', 'ASCII', 'OAUTH', 'JWT', 'PEM',
})

_GROUNDING_EXTERNAL_CLIS = frozenset({
    'git', 'agy', 'codex', 'claude', 'cc', 'dsh', 'python', 'python3', 'py', 'wsl', 'powershell', 'pwsh',
    'bash', 'sh', 'cmd', 'curl', 'uv', 'pip', 'npm', 'npx', 'cargo', 'cmake', 'ctest', 'pytest', 'mpirun', 'mpiexec',
})

_GROUNDING_STOP_CJK2 = frozenset({
    '计算', '科学', '工程', '系统', '软件', '分析', '数值', '规划', '方法', '模型', '算法', '项目', '求解', '问题',
    '检查', '判据', '连续', '容差', '有限', '二维', '三维', '复杂', '高可', '可靠', '靠系', '统软', '件研', '研发',
    '程研', '靠工', '发中', '算流', '体力', '元分', '性有', '线性', '非线', '解器', '器或', '库中', '领域', '标准',
    '术语', '日常', '课题', '常见', '场景', '模块', '功能', '不夸', '夸大', '大量', '量词',
})

_GROUNDING_CLIENT_READONLY_TOOLS = (
    'view_file', 'grep_search', 'find_by_name',
    'Get-Content', 'Select-String', 'Get-ChildItem',
    'Read', 'Grep', 'Glob',
    'read_file', 'search_web', 'send_message', 'finish',
)

_GROUNDING_REPO_SCRIPT_TOKENS = (
    'install.py', 'install.ps1', 'install.sh',
    'cae_harness.py', '$harness', '${harness}', '%harness%', '_harness',
    'fleet_state.py', 'ccswitch_admin.py', 'router.py',
    'handoff_gate.py', 'status_guard.py', 'reply_repeat_guard.py',
    'user_words.py', 'session_context_loader.py',
    'update-cc-switch-wsl.sh', 'launch-cc-switch-wsl.sh',
)

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


def _collect_toml_keys(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            _collect_toml_keys(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_toml_keys(item, out)


def _is_codex_agent_toml_rel(rel):
    return (rel.startswith('template/codex/agents/') or rel.startswith('.codex/agents/')) and rel.lower().endswith('.toml')


def _is_md_agent_contract_rel(rel):
    return (
        rel.startswith('template/user-guard/agents/')
        or rel.startswith('template/claude/agents/')
        or rel.startswith('.claude/agents/')
    ) and rel.lower().endswith('.md')


def _extract_codex_agent_toml_doc(raw_text):
    if not raw_text:
        return ''
    try:
        parsed = tomllib.loads(raw_text.lstrip('\ufeff'))
    except (tomllib.TOMLDecodeError, ValueError):
        return raw_text
    if not isinstance(parsed, dict):
        return ''
    out_lines = []
    for field in ('description', 'developer_instructions'):
        val = parsed.get(field)
        if not isinstance(val, str) or not val.strip():
            continue
        m = re.search(rf'(?m)^\s*{re.escape(field)}\s*=', raw_text)
        start_line = raw_text.count('\n', 0, m.start()) + 1 if m else (len(out_lines) + 1)
        while len(out_lines) < start_line - 1:
            out_lines.append('')
        out_lines.extend(val.splitlines())
    return '\n'.join(out_lines)


def _extract_latest_changelog_section(raw_text):
    if not raw_text:
        return ''
    out_lines = []
    started = False
    for line in raw_text.splitlines():
        if re.match(r'^##\s*\[', line.strip()):
            if not started:
                started = True
                out_lines.append(line)
                continue
            break
        out_lines.append(line if started else '')
    return '\n'.join(out_lines) if started else raw_text


def _build_grounding_anchor(root_dir, exclude_rels, allow_files=None, extra_allowed_text='', *, target=None):
    root_dir = Path(root_dir).resolve()
    hd = _headless_tunables()
    cap = (_positive_int(hd.get('grounding_max_anchor_file_mib')) or 2) * 1024 ** 2
    excluded = set(hd.get('grounding_excluded_dirs', ('.micromamba', '.toolchain', 'conda-meta', 'archive', 'archived', 'outdated')))
    target = Path(target).resolve() if target is not None else root_dir

    def anchor_text(path):
        with contextlib.suppress(OSError):
            with path.open('rb') as stream:
                if stream.seek(0, 2) <= cap:
                    stream.seek(0)
                    return stream.read(cap).decode('utf-8-sig', errors='replace')
                stream.seek(0)
                head = stream.read(65536)
                stream.seek(-65536, 2)
                return (head + b'\n' + stream.read(65536)).decode('utf-8-sig', errors='replace')
        return ''

    words, flags, cjk2, numbers, repo_names, dynamic_file_globs = (set() for _ in range(6))

    def add_text(text):
        low = text.lower()
        words.update(re.findall(r'[a-z0-9]+(?:[._-][a-z0-9]+)*', low))
        words.update(re.findall(r'[a-z0-9]+', low))
        flags.update(re.findall(r'(?<![a-z0-9])(--[a-z0-9][a-z0-9_-]*)', low))
        cjk2.update(_extract_cjk_2grams(text))
        numbers.update(_extract_anchor_numbers(text))

    add_text(' '.join(_GROUNDING_CLIENT_READONLY_TOOLS))
    for dirpath, dirs, files in os.walk(root_dir, followlinks=False):
        dpath = Path(dirpath)
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not _is_link(dpath / d) and
                         (target.is_relative_to(dpath / d) or (d.lower() not in excluded and not d.lower().startswith(('archive_', 'outdated_')))))
        for fname in sorted(files):
            fpath = dpath / fname
            if _is_link(fpath) or not fpath.resolve().is_relative_to(root_dir):
                continue
            rel = fpath.resolve().relative_to(root_dir).as_posix()
            repo_names.add(fname.lower())
            repo_names.add(fpath.stem.lower())
            repo_names.add(rel.lower())
            if rel.startswith('tests/') or _is_md_agent_contract_rel(rel):
                continue
            if _is_codex_agent_toml_rel(rel):
                txt = anchor_text(fpath)
                if txt:
                    try:
                        toml_keys = set()
                        _collect_toml_keys(tomllib.loads(txt.lstrip('\ufeff')), toml_keys)
                        if toml_keys:
                            add_text(' '.join(sorted(toml_keys)))
                    except (tomllib.TOMLDecodeError, ValueError):
                        pass
                continue
            if rel in exclude_rels:
                continue
            if fpath.suffix.lower() in _GROUNDING_ANCHOR_EXTS or rel in ('NOTICE', 'CMakeLists.txt'):
                txt = anchor_text(fpath)
                if txt:
                    add_text(txt)
                    for pat in re.findall(r'([A-Za-z0-9_.-]*\{[^}\r\n]{1,32}\}[A-Za-z0-9_.-]*\.[A-Za-z0-9]+)', txt):
                        dynamic_file_globs.add(re.sub(r'\{[^}]+\}', '*', pat).lower())
    for extra_file in (allow_files or ()):
        ep = Path(extra_file).expanduser()
        if not ep.is_absolute():
            ep = root_dir / ep
        if ep.is_file() and not _is_link(ep):
            add_text(anchor_text(ep))
    if extra_allowed_text:
        add_text(str(extra_allowed_text))
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


def _infer_project_anchor_root(target):
    resolved = Path(target).expanduser().resolve()
    mode_info = detect_workspace_mode(resolved)
    if mode_info['upstream_repo_root']:
        return Path(mode_info['upstream_repo_root'])
    base = resolved if resolved.is_dir() else resolved.parent
    stop_roots = {Path.home().resolve(), Path(tempfile.gettempdir()).resolve()}
    for candidate in (base, *base.parents):
        if candidate in stop_roots:
            break
        if (candidate / '.git').exists():
            return candidate
    return base


_FENCED_SHELL_LANGS = frozenset({'bash', 'sh', 'shell', 'zsh', 'powershell', 'pwsh', 'ps1'})


def _strip_shell_comment_line(line):
    out = []
    quote = ''
    idx = 0
    while idx < len(line):
        ch = line[idx]
        if quote:
            if ch == '\\' and quote == '"' and idx + 1 < len(line):
                out.append(line[idx:idx + 2])
                idx += 2
                continue
            if ch == quote:
                quote = ''
            out.append(ch)
            idx += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            idx += 1
            continue
        if ch == '#' and (idx == 0 or line[idx - 1].isspace()):
            break
        out.append(ch)
        idx += 1
    return ''.join(out).rstrip()


def _split_shell_clauses(pairs):
    clauses = []
    cur = []
    for line_no, seg in pairs:
        quote = ''
        buf = []
        idx = 0
        while idx < len(seg):
            ch = seg[idx]
            if quote:
                if ch == '\\' and quote == '"' and idx + 1 < len(seg):
                    buf.append(seg[idx:idx + 2])
                    idx += 2
                    continue
                if ch == quote:
                    quote = ''
                buf.append(ch)
                idx += 1
                continue
            if ch in ('"', "'"):
                quote = ch
                buf.append(ch)
                idx += 1
                continue
            if seg[idx:idx + 2] in ('&&', '||'):
                if ''.join(buf).strip():
                    cur.append((line_no, ''.join(buf)))
                if cur:
                    clauses.append(cur)
                    cur = []
                buf = []
                idx += 2
                continue
            if ch in (';', '|'):
                if ''.join(buf).strip():
                    cur.append((line_no, ''.join(buf)))
                if cur:
                    clauses.append(cur)
                    cur = []
                buf = []
                idx += 1
                continue
            buf.append(ch)
            idx += 1
        if ''.join(buf).strip():
            cur.append((line_no, ''.join(buf)))
    if cur:
        clauses.append(cur)
    return clauses


def _iter_fenced_shell_clauses(rel_path, raw_text):
    if rel_path.lower().endswith('.svg'):
        return
    fence_marker = ''
    fence_len = 0
    fence_lang = ''
    block_lines = []
    for line_no, line in enumerate(raw_text.splitlines(), 1):
        stripped = line.lstrip()
        m_fence = re.match(r'^(`{3,}|~{3,})', stripped)
        if m_fence:
            tok = m_fence.group(1)
            if not fence_marker:
                rest = stripped[len(tok):]
                if tok not in rest:
                    fence_marker, fence_len = tok[0], len(tok)
                    fence_lang = rest.strip().split()[0].lower() if rest.strip() else ''
                    block_lines = []
                    continue
            elif tok[0] == fence_marker and len(tok) >= fence_len and not stripped[len(tok):].strip():
                if fence_lang in _FENCED_SHELL_LANGS and block_lines:
                    current = []
                    for ln, raw_ln in block_lines:
                        cleaned = _strip_shell_comment_line(raw_ln)
                        if not cleaned.strip():
                            if current:
                                yield from _split_shell_clauses(current)
                                current = []
                            continue
                        cont = cleaned.endswith('\\') or (fence_lang in ('powershell', 'pwsh', 'ps1') and cleaned.endswith('`'))
                        seg = cleaned[:-1].rstrip() if cont else cleaned
                        current.append((ln, seg))
                        if not cont:
                            yield from _split_shell_clauses(current)
                            current = []
                    if current:
                        yield from _split_shell_clauses(current)
                fence_marker, fence_len, fence_lang, block_lines = '', 0, '', []
                continue
        if fence_marker:
            block_lines.append((line_no, line))


def _audit_single_doc_text(rel_path, doc_text, anchor, baseline_text=''):
    findings = []
    seen = set()
    baseline_low = baseline_text.lower() if baseline_text else ''
    baseline_words = (
        set(re.findall(r'[a-z0-9]+(?:[._-][a-z0-9]+)*', baseline_low)) | set(re.findall(r'[a-z0-9]+', baseline_low))
    ) if baseline_low else set()
    baseline_flags = set(re.findall(r'(?<![a-z0-9])(--[a-z0-9][a-z0-9_-]*)', baseline_low)) if baseline_low else set()
    baseline_files = {
        m.lower().lstrip('./')
        for m in re.findall(r'\b([A-Za-z0-9_.*-]+(?:/[A-Za-z0-9_.*-]+)*\.(?:py|json|toml|yaml|yml|svg|sh|ps1))\b', baseline_text)
    } if baseline_text else set()
    baseline_files |= {Path(p).name for p in baseline_files}
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
                    if flag not in anchor['flags'] and flag not in baseline_flags:
                        record(line_no, 'ungrounded_identifier', flag, '行内代码引用的 CLI 选项未在仓库脚本或配置中声明')
            else:
                # 对仓库脚本及 $HARNESS 变量调用仍严格校验参数
                if any(s in flag_scan_text.lower() for s in _GROUNDING_REPO_SCRIPT_TOKENS):
                    span_unquoted = re.sub(r'"(?:\\.|[^"\\])*"|\x27[^\x27]*\x27', ' ', flag_scan_text)
                    for flag in re.findall(r'(?<![A-Za-z0-9])(--[a-z0-9][a-z0-9_-]*)', span_unquoted.lower()):
                        if flag not in anchor['flags'] and flag not in baseline_flags:
                            record(line_no, 'ungrounded_identifier', flag, '仓库脚本调用参数未在源码 argparse 中声明')
            if not is_external_cmd and not has_placeholder:
                for fn in re.findall(r'\b([A-Za-z0-9_.*-]+(?:/[A-Za-z0-9_.*-]+)*\.(?:py|json|toml|yaml|yml|svg|sh|ps1))\b', span_clean):
                    fn_low = fn.lower().lstrip('./')
                    base_low = Path(fn_low).name
                    if '*' in base_low:
                        continue
                    if (fn_low in anchor['repo_names'] or base_low in anchor['repo_names']
                            or fn_low in baseline_files or base_low in baseline_files
                            or base_low in anchor['words'] or base_low.lstrip('.') in anchor['words']
                            or base_low in baseline_words or base_low.lstrip('.') in baseline_words
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

    for clause in _iter_fenced_shell_clauses(rel_path, doc_text):
        joined_clause = ' '.join(re.sub(r'<[^>\n]*>', ' ', seg) for _, seg in clause)
        if not any(s in joined_clause.lower() for s in _GROUNDING_REPO_SCRIPT_TOKENS):
            continue
        for ln, seg in clause:
            seg_no_ph = re.sub(r'<[^>\n]*>', ' ', seg)
            seg_no_quotes = re.sub(r'"(?:\\.|[^"\\])*"|\x27[^\x27]*\x27', ' ', seg_no_ph)
            for flag in re.findall(r'(?<![A-Za-z0-9])(--[a-z0-9][a-z0-9_-]*)', seg_no_quotes.lower()):
                if flag not in anchor['flags'] and flag not in baseline_flags:
                    record(ln, 'ungrounded_identifier', flag, '代码块内仓库脚本调用参数未在源码或脚本中声明')

    return findings


_HYGIENE_CN_NUMS = {'两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
_HYGIENE_COUNT_CLAIM_RE = re.compile(
    r'(?<!前)(?<!至少)(?<!最多)(?<!至多)(?<!约)(?<!每)(?<!含)(?<!其中)(?<!第)(?<!共)'
    r'([两二三四五六七八九十]|\b(?:[2-9]|1\d|20)\b)\s*(?:个|类|项|步|条|层|道|种|大)'
    r'(?:[^\n。；，,：:]{0,18})[：:]\s*$'
)
_HYGIENE_TABLE_SEP_RE = re.compile(r'^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$')
_HYGIENE_LIST_ITEM_RE = re.compile(r'^([ \t]*)(?:[-*+]|\d+\.)\s+\S')
_HYGIENE_SLASH_CHAIN_RE = re.compile(
    r'(?<![A-Za-z0-9_./:\\-])([\u4e00-\u9fffA-Za-z0-9_-]{1,20}(?:/[\u4e00-\u9fffA-Za-z0-9_-]{1,20}){3,})(?![A-Za-z0-9_./:-])'
)
_HYGIENE_PRIVATE_SYM_RE = re.compile(r'^_([a-z][a-z0-9_]{2,})$')
_HYGIENE_DUP_EXEMPT_RE = re.compile(
    r'(?:Apache\s*License|Copyright\s+\d{4}|国家留学基金|China\s+Scholarship\s+Council|留金选|python3?\s+-B\s+-X\s+utf8)',
    re.I,
)


def _hygiene_parse_count(tok):
    if tok in _HYGIENE_CN_NUMS:
        return _HYGIENE_CN_NUMS[tok]
    try:
        return int(tok)
    except ValueError:
        return None


def _hygiene_iter_unmasked_lines(doc_text):
    """产出 (line_no, raw_line, in_fence, in_math) 元组，跳过围栏代码块与独立数学公式块。"""
    in_fence = False
    fence_char = ''
    fence_len = 0
    in_math = False
    for idx, raw in enumerate(doc_text.splitlines(), 1):
        s = raw.lstrip()
        m = re.match(r'^(`{3,}|~{3,})(.*)$', s)
        if m:
            ticks, rest = m.group(1), m.group(2)
            if not in_fence:
                if ticks[0] not in rest:
                    in_fence = True
                    fence_char = ticks[0]
                    fence_len = len(ticks)
                yield idx, raw, True, in_math
                continue
            if ticks[0] == fence_char and len(ticks) >= fence_len and not rest.strip():
                in_fence = False
                fence_char = ''
                fence_len = 0
                yield idx, raw, True, in_math
                continue
        if not in_fence and s.strip() == '$$':
            in_math = not in_math
            yield idx, raw, False, True
            continue
        yield idx, raw, in_fence, in_math


def _hygiene_following_block_count(lines_meta, start_pos):
    """检查紧随声明行之后的结构块，若是 Markdown 表格或顶层列表则返回 (kind, count)。"""
    n = len(lines_meta)
    pos = start_pos
    while pos < n:
        _, raw, in_f, in_m = lines_meta[pos]
        if in_f or in_m:
            return None, 0
        if not raw.strip():
            pos += 1
            continue
        break
    if pos >= n:
        return None, 0

    # 1. Markdown 表格：header + separator + data rows
    _, first_raw, in_f, in_m = lines_meta[pos]
    if not in_f and not in_m and '|' in first_raw and pos + 1 < n and _HYGIENE_TABLE_SEP_RE.match(lines_meta[pos + 1][1]):
        rows = 0
        cur = pos + 2
        while cur < n:
            _, rline, rf, rm = lines_meta[cur]
            if rf or rm or not rline.strip() or '|' not in rline:
                break
            rows += 1
            cur += 1
        return ('table', rows) if rows >= 2 else (None, 0)

    # 2. 顶层有序/无序列表：统计相同起始缩进的连续条目
    m0 = _HYGIENE_LIST_ITEM_RE.match(first_raw)
    if m0:
        base_indent = len(m0.group(1).expandtabs(4))
        items = 0
        cur = pos
        while cur < n:
            _, rline, rf, rm = lines_meta[cur]
            if rf or rm:
                # 缩进围栏代码块可属于当前列表项
                cur += 1
                continue
            if not rline.strip():
                # 仅当后续非空行仍是同缩进或更深缩进的列表/续行时继续
                nxt = cur + 1
                while nxt < n and not lines_meta[nxt][1].strip() and not lines_meta[nxt][2]:
                    nxt += 1
                if nxt >= n:
                    break
                nm = _HYGIENE_LIST_ITEM_RE.match(lines_meta[nxt][1])
                nxt_indent = len(lines_meta[nxt][1]) - len(lines_meta[nxt][1].lstrip(' \t'))
                if (nm and len(nm.group(1).expandtabs(4)) == base_indent) or nxt_indent > base_indent:
                    cur += 1
                    continue
                break
            if rline.lstrip().startswith('#') or rline.lstrip().startswith('---'):
                break
            lm = _HYGIENE_LIST_ITEM_RE.match(rline)
            if lm:
                cur_indent = len(lm.group(1).expandtabs(4))
                if cur_indent == base_indent:
                    items += 1
                elif cur_indent < base_indent:
                    break
            else:
                cur_indent = len(rline) - len(rline.lstrip(' \t'))
                if cur_indent <= base_indent:
                    break
            cur += 1
        return ('list', items) if items >= 2 else (None, 0)
    return None, 0


def _audit_docs_hygiene(
    doc_entries,
    *,
    landing_max_bytes=18000,
    max_cjk_sentence_chars=220,
    max_ascii_sentence_chars=480,
    max_sentence_semicolons=4,
    dup_min_chars=36,
    dup_min_files=3,
    landing_max_inline_codes=8,
):
    """通用零黑名单文档卫生校验：计数一致、补丁长句/斜杠串、跨文档复读与 T0 入口清晰度。"""
    findings = []
    seen = set()

    def record(rel_path, line_no, category, token, reason):
        key = (rel_path, line_no, category, str(token)[:80])
        if key in seen:
            return
        seen.add(key)
        findings.append({
            'file': rel_path,
            'line': line_no,
            'category': category,
            'token': str(token)[:120],
            'reason': reason,
        })

    dup_index = {}  # norm_clause -> list[(rel_path, line_no, raw_clause)]

    for rel_path, doc_text in doc_entries:
        if not rel_path.lower().endswith('.md'):
            continue
        is_changelog = Path(rel_path).name.upper() == 'CHANGELOG.MD'
        is_agent_prompt = '/agents/' in f'/{rel_path}'
        is_landing = rel_path == 'README.md'

        # 1. T0 入口文档体积预算
        if is_landing:
            utf8_size = len(doc_text.encode('utf-8'))
            if utf8_size > landing_max_bytes:
                record(
                    rel_path, 1, 'landing_size_budget',
                    f'{utf8_size} B > {landing_max_bytes} B',
                    '根目录 README.md 超过入口页精简预算，建议将底层架构或维护细节下沉到 docs/ 子文档',
                )

        if is_changelog:
            continue

        lines_meta = list(_hygiene_iter_unmasked_lines(doc_text))
        for idx, (line_no, raw_line, in_fence, in_math) in enumerate(lines_meta):
            if in_fence or in_math:
                continue
            stripped = raw_line.strip()
            if not stripped or stripped.startswith('<!--') or stripped.startswith('>'):
                continue

            is_heading = stripped.startswith('#')
            is_table = stripped.startswith('|') or bool(_HYGIENE_TABLE_SEP_RE.match(stripped))

            # 2. 计数声明与紧邻表格/列表行数一致性
            if not is_heading and not is_table:
                no_code = re.sub(r'`[^`\r\n]*`', ' ', stripped)
                no_quotes = re.sub(r'“[^”\n]*”|"[^"\n]*"', ' ', no_code)
                cm = _HYGIENE_COUNT_CLAIM_RE.search(no_quotes)
                if cm:
                    declared = _hygiene_parse_count(cm.group(1))
                    kind, actual = _hygiene_following_block_count(lines_meta, idx + 1)
                    if declared is not None and kind and actual >= 2 and actual != declared:
                        label = '表格数据行' if kind == 'table' else '顶层列表项'
                        record(
                            rel_path, line_no, 'count_structure_mismatch',
                            f'{cm.group(0).strip()} ({declared} != {actual})',
                            f'引导句声明 {declared} 项，但紧随其后的{label}为 {actual} 项，存在补丁残留计数失步',
                        )

            # 3. 散文补丁超长句与连续斜杠术语串
            if not is_heading and not is_table:
                prose = re.sub(r'!\[[^\]]*\]\([^)]+\)', ' ', stripped)
                prose = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', prose)
                prose = re.sub(r'https?://\S+', ' ', prose)
                prose_no_code = re.sub(r'`[^`\r\n]*`', ' ', prose)
                # 连续 4 段以上含中文斜杠术语串
                for sm in _HYGIENE_SLASH_CHAIN_RE.finditer(prose_no_code):
                    tok = sm.group(1)
                    if re.search(r'[\u4e00-\u9fff]', tok):
                        record(
                            rel_path, line_no, 'slash_chain', tok,
                            '散文存在四段及以上连续斜杠术语串，建议改写为自然工程表述',
                        )
                # 补丁沉积超长单句 / 多重分号串烧
                prose_clean = re.sub(r'\*\*|__|`', '', prose)
                for sent in re.split(r'[。！？]+|(?<=\.)\s+(?=[A-Z])', prose_clean):
                    seg = sent.strip()
                    if not seg:
                        continue
                    has_cjk = bool(re.search(r'[\u4e00-\u9fff]', seg))
                    limit = max_cjk_sentence_chars if has_cjk else max_ascii_sentence_chars
                    semi_count = seg.count('；') + seg.count(';')
                    if len(seg) > limit or semi_count >= max_sentence_semicolons:
                        record(
                            rel_path, line_no, 'runon_patch_sentence',
                            f'{seg[:48]}... ({len(seg)} chars, {semi_count} semicolons)',
                            '单句过长或堆叠过多分号补丁，建议拆分为清晰的短句或独立条目',
                        )

            # 4. T0 入口页防内部私有符号与高密度反引号行话
            if is_landing:
                code_spans = [sp.strip() for sp in re.findall(r'`([^`\r\n]+)`', stripped) if sp.strip()]
                for sp in code_spans:
                    if not (sp.startswith('__') and sp.endswith('__')) and _HYGIENE_PRIVATE_SYM_RE.match(sp):
                        record(
                            rel_path, line_no, 'landing_private_symbol', sp,
                            '根目录 README.md 不应直接暴露内部下划线私有符号，建议下沉至架构参考文档',
                        )
                if not is_heading and not is_table:
                    uniq_codes = set(code_spans)
                    if len(uniq_codes) >= landing_max_inline_codes:
                        record(
                            rel_path, line_no, 'landing_jargon_density',
                            f'{len(uniq_codes)} inline code tokens',
                            '根目录 README.md 单段散文堆砌过多行内代码标识符，建议改用自然工程语言并把细节链接到子文档',
                        )

            # 5. 收集跨文档逐字复读候选子句（跳过镜像代理模板与纯标题/表头）
            if not is_agent_prompt and not is_heading and not _HYGIENE_TABLE_SEP_RE.match(stripped):
                clean_dup = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', stripped)
                clean_dup = re.sub(r'https?://\S+', ' ', clean_dup)
                for clause in re.split(r'[。；！？\n|（）]+', clean_dup):
                    raw_c = clause.strip()
                    if not raw_c or _HYGIENE_DUP_EXEMPT_RE.search(raw_c):
                        continue
                    norm_c = re.sub(r'[\s`*_~“”"\'()【】\[\]、，,：:·\-—]+', '', raw_c)
                    if len(norm_c) < dup_min_chars:
                        continue
                    if len(re.findall(r'[\u4e00-\u9fff]', norm_c)) < 16:
                        continue
                    # 按固定窗口取归一化切片，既能抓整句复读也能抓换个前缀的细则粘贴
                    win = dup_min_chars
                    stride = max(6, win // 4)
                    offsets = list(range(0, max(1, len(norm_c) - win + 1), stride))
                    tail_off = len(norm_c) - win
                    if tail_off > 0 and tail_off not in offsets:
                        offsets.append(tail_off)
                    for off in offsets:
                        shingle = norm_c[off : off + win]
                        if len(re.findall(r'[\u4e00-\u9fff]', shingle)) >= 16:
                            dup_index.setdefault(shingle, []).append((rel_path, line_no, raw_c))

    # 每个文档至多针对同一行报告一次跨文档复读（避免相邻切片重复刷屏）
    reported_dup_lines = set()
    for shingle, occs in dup_index.items():
        distinct_files = sorted({r for r, _, _ in occs})
        if len(distinct_files) >= dup_min_files:
            file_list = ', '.join(distinct_files[:4]) + ('...' if len(distinct_files) > 4 else '')
            for rel_path, line_no, raw_c in occs:
                if (rel_path, line_no) in reported_dup_lines:
                    continue
                reported_dup_lines.add((rel_path, line_no))
                record(
                    rel_path, line_no, 'cross_doc_duplication',
                    f'{shingle[:44]} ({len(distinct_files)} files)',
                    f'相同长技术细则在 {len(distinct_files)} 份文档中重复粘贴（{file_list}），建议单点维护并改用交叉链接',
                )

    return findings


def audit_doc_grounding(
    target_path,
    root_dir=None,
    *,
    base_ref=None,
    allow_files=None,
    extra_allowed_text='',
    doc_hygiene=False,
):
    """对目标文档或目录执行零黑名单确定性事实锚定核验（标识符、缩写、容量数字与举例词干）。"""
    target = Path(target_path).expanduser().resolve()
    if not target.exists():
        raise HarnessError(f'目标路径不存在: {target}')
    if root_dir is not None:
        root = Path(root_dir).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise HarnessError(f'锚点根目录不存在或不是目录: {root}')
    else:
        root = _infer_project_anchor_root(target)
    resolved_allow_files = []
    for extra_file in (allow_files or ()):
        ep = Path(extra_file).expanduser()
        if not ep.is_absolute():
            ep = (root / ep) if (root / ep).exists() else (Path.cwd() / ep)
        if not ep.exists() or _is_link(ep) or not ep.is_file():
            raise HarnessError(f'允许锚点文件不存在或不可读: {extra_file}')
        resolved_allow_files.append(ep.resolve())
    git_toplevel = None
    verified_commit = None
    git_env = _clean_git_env()
    if base_ref:
        ref_str = str(base_ref).strip()
        if not ref_str or ref_str.startswith('-'):
            raise HarnessError(f'无效的 Git 基线引用: {base_ref!r}')
        try:
            top_proc = subprocess.run(
                ['git', '-C', str(root), 'rev-parse', '--show-toplevel'],
                env=git_env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            )
            ver_proc = subprocess.run(
                ['git', '-C', str(root), 'rev-parse', '--verify', f'{ref_str}^{{commit}}'],
                env=git_env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HarnessError(f'无法核验 Git 基线引用 {ref_str!r}: {exc}') from exc
        if top_proc.returncode != 0 or not top_proc.stdout.strip():
            raise HarnessError(f'目标目录不在有效的 Git 仓库内，无法使用 --base-ref: {root}')
        if ver_proc.returncode != 0 or not ver_proc.stdout.strip():
            raise HarnessError(f'无效的 Git 基线引用: {ref_str}')
        git_toplevel = Path(top_proc.stdout.strip()).resolve()
        verified_commit = ver_proc.stdout.strip()
    is_upstream_repo = detect_workspace_mode(root)['is_upstream_config_repo']
    doc_files = []
    if target.is_file():
        rel = target.relative_to(root).as_posix() if target.is_relative_to(root) else target.name
        doc_files.append((target, rel))
    else:
        skip_rels = {'LICENSE', 'NOTICE'} if is_upstream_repo else {'CHANGELOG.md', 'LICENSE', 'NOTICE'}
        for dirpath, dirs, files in os.walk(target, followlinks=False):
            dpath = Path(dirpath)
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not _is_link(dpath / d))
            for fname in sorted(files):
                fpath = dpath / fname
                if _is_link(fpath):
                    continue
                resolved_f = fpath.resolve()
                rel = resolved_f.relative_to(root).as_posix() if resolved_f.is_relative_to(root) else resolved_f.relative_to(target).as_posix()
                is_codex_toml = _is_codex_agent_toml_rel(rel)
                if fpath.suffix.lower() not in ('.md', '.svg', '.rst') and not is_codex_toml:
                    continue
                if rel in skip_rels or rel.startswith('tests/') or rel == 'template/user-guard/assets/logo.svg':
                    continue
                doc_files.append((fpath, rel))
    exclude_rels = {rel for _, rel in doc_files}
    anchor = _build_grounding_anchor(root, exclude_rels, allow_files=resolved_allow_files, extra_allowed_text=extra_allowed_text,
                                     target=target)
    all_findings = []
    checked_files = []
    hygiene_entries = []
    for fpath, rel in doc_files:
        checked_files.append(rel)
        raw_doc = _optional_text(fpath)
        if doc_hygiene and fpath.suffix.lower() == '.md':
            hygiene_entries.append((rel, raw_doc))
        baseline_txt = ''
        if verified_commit and git_toplevel:
            resolved_doc = fpath.resolve()
            if resolved_doc.is_relative_to(git_toplevel):
                git_rel = resolved_doc.relative_to(git_toplevel).as_posix()
                try:
                    proc = subprocess.run(
                        ['git', '-C', str(git_toplevel), 'show', f'{verified_commit}:{git_rel}'],
                        env=git_env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
                    )
                    if proc.returncode == 0:
                        baseline_txt = proc.stdout
                except (OSError, subprocess.SubprocessError):
                    pass
        if _is_codex_agent_toml_rel(rel):
            raw_doc = _extract_codex_agent_toml_doc(raw_doc)
            if baseline_txt:
                baseline_txt = _extract_codex_agent_toml_doc(baseline_txt)
        elif is_upstream_repo and rel == 'CHANGELOG.md':
            raw_doc = _extract_latest_changelog_section(raw_doc)
            if baseline_txt:
                baseline_txt = _extract_latest_changelog_section(baseline_txt)
        all_findings.extend(_audit_single_doc_text(rel, raw_doc, anchor, baseline_txt))
    if doc_hygiene:
        all_findings.extend(_audit_docs_hygiene(hygiene_entries))
    by_category = collections.Counter(item['category'] for item in all_findings)
    return {
        'ok': not all_findings,
        'root': str(root),
        'target': str(target),
        'base_ref': base_ref or None,
        'doc_hygiene': bool(doc_hygiene),
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
        doc_hygiene=bool(getattr(args, 'doc_hygiene', False)),
    )
    status = 'verification_failed' if (args.strict and not report['ok']) else 'ok'
    return _print({'status': status, 'workspace_mode': detect_workspace_mode(args.root or args.target), **report})


# ════════════════════════════════════════════════════════════════════════════
# ⑦B 统一基线冻结、义务结算与确定性机械对账表引擎
# ════════════════════════════════════════════════════════════════════════════

_RECONCILE_CODE_EXTS = {
    '.py', '.f90', '.f95', '.f03', '.f08', '.f', '.for', '.c', '.cpp', '.cc', '.cxx',
    '.cu', '.cuh', '.h', '.hpp', '.hh', '.jl', '.m', '.rs', '.go', '.ts', '.js',
    '.sh', '.bash', '.ps1', '.cmake', '.lean', '.inc',
}
_RECONCILE_DOC_EXTS = {'.md', '.rst', '.txt', '.tex'}
_RECONCILE_CONFIG_EXTS = {'.json', '.toml', '.yaml', '.yml', '.ini', '.cfg'}
_RECONCILE_GENERIC_SYMBOLS = frozenset({
    'main', 'setup', 'teardown', 'helper', 'config', 'status', 'result', 'render',
    'format', 'parse', 'load', 'save', 'read', 'write', 'check', 'build', 'update',
    'create', 'delete', 'remove', 'handle', 'process', 'execute', 'validate',
    'summary', 'report', 'record', 'state', 'client', 'server', 'worker', 'stream',
})
_RECONCILE_MD_LINK_RE = re.compile(r'(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
_RECONCILE_BACKTICK_RE = re.compile(r'`([^`\r\n]{2,160})`')
_RECONCILE_MAX_ACTIVE_FILES = 4000
_RECONCILE_TOP_SCREEN_ITEMS = 10


def _reconcile_git_porcelain(root):
    """只读读取 Git 工作树脏文件状态（含未跟踪文件）；非 Git 仓库或失败时返回 None。"""
    try:
        proc = subprocess.run(
            ['git', '-C', str(root), 'status', '--porcelain=v1', '-uall'],
            env=_clean_git_env(), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=15,
        )
        if proc.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    dirty = {}
    for raw in (proc.stdout or '').splitlines():
        if len(raw) < 4:
            continue
        code = raw[:2]
        path_part = raw[3:].strip()
        if ' -> ' in path_part:
            path_part = path_part.split(' -> ', 1)[1].strip()
        if path_part.startswith('"') and path_part.endswith('"'):
            path_part = path_part[1:-1]
        norm = path_part.replace('\\', '/').lstrip('./')
        if norm:
            dirty[norm] = code.strip() or code
    return dirty


def _reconcile_git_diff_names(root, base_ref):
    """对比指定 Git 基线引用与当前工作树（覆盖已提交、暂存、未暂存与未跟踪文件）。"""
    try:
        resolved = subprocess.run(
            ['git', '-C', str(root), 'rev-parse', '--verify', str(base_ref) + '^{commit}'],
            env=_clean_git_env(), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=15,
        )
        if resolved.returncode != 0:
            return None
        diff_proc = subprocess.run(
            ['git', '-C', str(root), 'diff', '--name-status', '--no-renames', str(base_ref)],
            env=_clean_git_env(), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=20,
        )
        if diff_proc.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    added, modified, deleted = set(), set(), set()
    for raw in (diff_proc.stdout or '').splitlines():
        parts = raw.split('\t', 1)
        if len(parts) != 2:
            continue
        st_code = parts[0].strip()[:1].upper()
        rel = parts[1].strip().replace('\\', '/').lstrip('./')
        if not rel:
            continue
        if st_code == 'A':
            added.add(rel)
        elif st_code == 'D':
            deleted.add(rel)
        else:
            modified.add(rel)
    porcelain = _reconcile_git_porcelain(root) or {}
    for rel, code in porcelain.items():
        if code == '??' and rel not in modified and rel not in deleted:
            added.add(rel)
    return {
        'base_commit': (resolved.stdout or '').strip(),
        'added': sorted(added),
        'modified': sorted(modified - added - deleted),
        'deleted': sorted(deleted),
    }


def capture_reconcile_baseline(root, *, ignore_dirs=None, ignore_patterns=None, pre_snapshot=None, capture_snapshot=True):
    """冻结任务开工时刻的授权目录范围基线（覆盖 Git HEAD、开工前已脏文件与内容/元数据快照）。"""
    root_path = Path(root).expanduser().resolve()
    if root_path.is_file():
        root_path = root_path.parent
    snap = pre_snapshot
    if snap is None and capture_snapshot and root_path.is_dir():
        try:
            snap = snapshot_files(root_path, ignore_dirs=ignore_dirs, ignore_patterns=ignore_patterns)
        except (HarnessError, OSError):
            snap = None
    file_snap = {}
    uses_stat = False
    if isinstance(snap, dict):
        for key, val in snap.items():
            if key == '.' or key.endswith('/'):
                continue
            file_snap[key] = val
            if isinstance(val, str) and val.startswith('stat:'):
                uses_stat = True
    dirty_map = _reconcile_git_porcelain(root_path) if root_path.is_dir() else None
    return {
        'schema_version': 1,
        'root': str(root_path),
        'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'git_head': _git_head(root_path) if root_path.is_dir() else None,
        'git_dirty_files': sorted(dirty_map) if isinstance(dirty_map, dict) else [],
        'snapshot_mode': ('stat_metadata' if uses_stat else 'content_sha256') if snap is not None else 'none',
        'ignore_dirs': sorted(ignore_dirs) if ignore_dirs else None,
        'snapshot': file_snap if snap is not None else None,
    }


def _classify_reconcile_file(rel_path):
    norm = str(rel_path or '').replace('\\', '/').lstrip('./')
    low = norm.lower()
    name = low.rsplit('/', 1)[-1]
    ext = Path(name).suffix.lower()
    if (low.startswith('tests/') or '/tests/' in low
            or name.startswith('test_') or name.endswith('_test.py')
            or name.endswith('_test.rs') or name.endswith('_test.go')):
        return 'test'
    if ext in _RECONCILE_DOC_EXTS or name in ('readme', 'license', 'notice', 'changelog'):
        return 'doc'
    if ext in _RECONCILE_CONFIG_EXTS:
        return 'config'
    if ext in _RECONCILE_CODE_EXTS or name in ('makefile', 'cmakelists.txt', 'dockerfile'):
        return 'code'
    return 'other'


def _reconcile_excluded_dirs():
    hd = _headless_tunables()
    return set(hd.get('grounding_excluded_dirs', ('.micromamba', '.toolchain', 'conda-meta', 'archive', 'archived', 'outdated')))


def _collect_active_repo_files(root_dir):
    """枚举项目内现役活动文件（跳过依赖/构建/归档与符号链接，带安全文件上限防卡死）。"""
    root_path = Path(root_dir).expanduser().resolve()
    if not root_path.is_dir():
        return [], False, 0
    excluded = _reconcile_excluded_dirs() | set(_snapshot_ignored_dirs()) | IGNORED_DIRS
    active = []
    truncated = False
    skipped_archive_or_ignored = 0
    for dirpath, dirs, files in os.walk(root_path, followlinks=False):
        dpath = Path(dirpath)
        kept = []
        for dirname in sorted(dirs):
            cand = dpath / dirname
            low = dirname.lower()
            if (dirname in excluded or low in excluded
                    or low.startswith(('archive_', 'outdated_', '.')) and dirname != '.github'
                    or _is_link(cand)):
                skipped_archive_or_ignored += 1
                continue
            kept.append(dirname)
        dirs[:] = kept
        for fname in sorted(files):
            fpath = dpath / fname
            if _is_link(fpath):
                continue
            try:
                resolved = fpath.resolve()
                if not resolved.is_relative_to(root_path):
                    continue
                rel = resolved.relative_to(root_path).as_posix()
            except (OSError, ValueError):
                continue
            if Path(fname).suffix.lower() not in SOURCE_EXTENSIONS and fname not in NAMED_TEXT_FILES:
                continue
            active.append(rel)
            if len(active) >= _RECONCILE_MAX_ACTIVE_FILES:
                truncated = True
                return active, truncated, skipped_archive_or_ignored
    return active, truncated, skipped_archive_or_ignored


def _bounded_read_text(path, cap_bytes):
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap_bytes:
            return ''
        return path.read_text(encoding='utf-8-sig', errors='replace')
    except OSError:
        return ''


def _extract_py_ast_metadata(root_path, rel_path, cap_bytes):
    """用 Python AST 静态提取模块导入目标与顶层类/函数精确符号名。"""
    text = _bounded_read_text(root_path / rel_path, cap_bytes)
    if not text:
        return {'imports': set(), 'symbols': set()}
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return {'imports': set(), 'symbols': set()}
    imports = set()
    symbols = set()
    rel_parts = Path(rel_path).with_suffix('').parts
    pkg_parts = rel_parts[:-1]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            if len(name) >= 6 and not name.startswith('__') and name.lower() not in _RECONCILE_GENERIC_SYMBOLS:
                symbols.add(name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    imports.add(alias.name)
                    imports.add(alias.name.rsplit('.', 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            if node.level and node.level > 0:
                up = node.level - 1
                base = pkg_parts[:-up] if up > 0 and up <= len(pkg_parts) else (pkg_parts if up == 0 else ())
                if mod:
                    full_mod = '.'.join((*base, *mod.split('.')))
                    imports.add(full_mod)
                    imports.add(mod.rsplit('.', 1)[-1])
                else:
                    for alias in node.names:
                        if alias.name and alias.name != '*':
                            imports.add('.'.join((*base, alias.name)))
                            imports.add(alias.name)
            elif mod:
                imports.add(mod)
                imports.add(mod.rsplit('.', 1)[-1])
                for alias in node.names:
                    if alias.name and alias.name != '*':
                        imports.add(f'{mod}.{alias.name}')
                        imports.add(alias.name)
    return {'imports': imports, 'symbols': symbols}


def _extract_md_links_and_citations(root_path, rel_path, cap_bytes):
    """跳过代码块围栏，提取 Markdown 显式相对链接、反引号代码引用与普通正文文本。"""
    text = _bounded_read_text(root_path / rel_path, cap_bytes)
    if not text:
        return {'links': [], 'backticks': set(), 'visible_text': ''}
    doc_dir = (root_path / rel_path).parent
    links = []
    backticks = set()
    visible_lines = []
    for line_no, line in _iter_visible_doc_lines(rel_path, text):
        visible_lines.append(line)
        for m in _RECONCILE_MD_LINK_RE.finditer(line):
            raw_target = m.group(1).strip().strip('<>')
            if (not raw_target or raw_target.startswith(('#', 'http://', 'https://', 'mailto:', 'file:', 'conversation:'))
                    or '://' in raw_target):
                continue
            clean = raw_target.split('#', 1)[0].split('?', 1)[0].strip()
            if not clean:
                continue
            try:
                cand = (doc_dir / clean).resolve()
                if cand.is_relative_to(root_path):
                    norm = cand.relative_to(root_path).as_posix()
                    links.append((norm, line_no))
            except (OSError, ValueError):
                continue
        for m in _RECONCILE_BACKTICK_RE.finditer(line):
            tok = m.group(1).strip()
            if tok:
                backticks.add(tok)
    return {'links': links, 'backticks': backticks, 'visible_text': '\n'.join(visible_lines)}


def _normalize_obligations(raw_obligations):
    items = []
    if not isinstance(raw_obligations, list):
        return items
    for idx, item in enumerate(raw_obligations, 1):
        if not isinstance(item, dict):
            continue
        oid = str(item.get('id') or f'OBL-{idx}').strip()
        kind = str(item.get('kind') or 'review').strip().lower()
        target = str(item.get('target') or '').strip().replace('\\', '/').lstrip('./')
        items.append({
            'id': oid,
            'kind': kind,
            'target': target,
            'line_range': item.get('line_range'),
            'required_clients': list(item.get('required_clients') or []),
            'source': str(item.get('source') or 'plan').strip(),
            'description': str(item.get('description') or '').strip(),
        })
    return items


def _normalize_reconcile_paths(raw_list, root_path):
    out = []
    for item in (raw_list or ()):
        if isinstance(item, str) and item.strip():
            norm = _normalize_rel_file(item, root_path) if root_path else item.strip().replace('\\', '/').lstrip('./')
            out.append(norm)
    return sorted(set(out))


def build_reconciliation_report(
    target_root,
    *,
    run_id='',
    subcommand='reconcile',
    payload=None,
    baseline=None,
    base_ref='',
    obligations=None,
    ticket=None,
    cross_check=None,
):
    """构建确定性机械对账结构化报告（纯静态代码事实，绝不冒充深层语义证明）。"""
    root_path = Path(target_root).expanduser().resolve() if target_root else Path.cwd().resolve()
    if root_path.is_file():
        root_path = root_path.parent
    run_id = run_id or uuid.uuid4().hex
    hd = _headless_tunables()
    cap_bytes = (_positive_int(hd.get('grounding_max_anchor_file_mib')) or 2) * 1024 ** 2

    # 1. 确定性变更集计算（优先使用冻结基线快照，避免漏掉阶段内提交或误归属开工前已脏文件）
    baseline_source = 'unverified_no_baseline'
    snapshot_mode = 'none'
    changes_status = 'unknown'
    added_files, modified_files, deleted_files = [], [], []
    preexisting_dirty_unchanged = []
    base_git_head = None
    current_git_head = _git_head(root_path) if root_path.is_dir() else None

    payload_scope_unverified = (
        isinstance(payload, dict) and payload.get('scope_verified') is False
    )
    payload_has_verified_changes = (
        isinstance(payload, dict)
        and isinstance(payload.get('changed_files'), list)
        and payload.get('scope_verified', True) is True
    )
    payload_stream_changes = None
    if isinstance(payload, dict) and isinstance(payload.get('streams'), list):
        _st_changed = set()
        _st_any_verified = False
        _st_any_unverified = False
        for _st in payload['streams']:
            if not isinstance(_st, dict):
                continue
            if _st.get('scope_verified') is False:
                _st_any_unverified = True
            elif isinstance(_st.get('changed_files'), list) and _st.get('scope_verified', True):
                _st_any_verified = True
                _st_changed.update(_normalize_reconcile_paths(_st.get('changed_files'), root_path))
        if _st_any_unverified and not _st_any_verified:
            payload_scope_unverified = True
        elif _st_any_verified:
            payload_stream_changes = sorted(_st_changed)

    base_head_hint = baseline.get('git_head') if isinstance(baseline, dict) else None
    has_frozen_snapshot = isinstance(baseline, dict) and isinstance(baseline.get('snapshot'), dict)
    committed_during_run = bool(base_head_hint and current_git_head and base_head_hint != current_git_head)

    if payload_scope_unverified and not base_ref:
        baseline_source = 'payload_scope_unverified'
        snapshot_mode = 'none'
        changes_status = 'unknown'
        base_git_head = base_head_hint or (payload.get('base_commit') if isinstance(payload, dict) else None)
    elif payload_has_verified_changes and not base_ref and not committed_during_run:
        raw_changed = _normalize_reconcile_paths(payload.get('changed_files'), root_path)
        before_snap = baseline.get('snapshot') if has_frozen_snapshot else None
        for rel in raw_changed:
            if root_path.is_dir() and not (root_path / rel).exists():
                deleted_files.append(rel)
            elif before_snap is not None and rel not in before_snap:
                added_files.append(rel)
            else:
                modified_files.append(rel)
        changed_union = set(added_files) | set(modified_files) | set(deleted_files)
        if isinstance(baseline, dict):
            preexisting_dirty_unchanged = sorted(
                p for p in (baseline.get('git_dirty_files') or ()) if p not in changed_union
            )
        baseline_source = 'frozen_receipt_snapshot' if isinstance(baseline, dict) else 'receipt_snapshot_diff'
        snapshot_mode = str(baseline.get('snapshot_mode') or 'receipt_snapshot') if isinstance(baseline, dict) else 'receipt_snapshot'
        changes_status = 'verified'
        base_git_head = base_head_hint or payload.get('base_commit')
    elif payload_stream_changes is not None and not base_ref and not committed_during_run:
        before_snap = baseline.get('snapshot') if has_frozen_snapshot else None
        for rel in payload_stream_changes:
            if root_path.is_dir() and not (root_path / rel).exists():
                deleted_files.append(rel)
            elif before_snap is not None and rel not in before_snap:
                added_files.append(rel)
            else:
                modified_files.append(rel)
        changed_union = set(added_files) | set(modified_files) | set(deleted_files)
        if isinstance(baseline, dict):
            preexisting_dirty_unchanged = sorted(
                p for p in (baseline.get('git_dirty_files') or ()) if p not in changed_union
            )
        baseline_source = 'batch_stream_snapshots'
        snapshot_mode = 'receipt_snapshot'
        changes_status = 'verified'
        base_git_head = base_head_hint
    elif has_frozen_snapshot and root_path.is_dir():
        # 有冻结内容/元数据快照时优先按起止快照比对：既可捕获任务内 git commit，又能精准隔离任务前已脏但未再改动的文件
        before_snap = baseline['snapshot']
        snapshot_mode = str(baseline.get('snapshot_mode') or 'content_sha256')
        uses_stat = snapshot_mode == 'stat_metadata'
        ignore_dirs = set(baseline['ignore_dirs']) if baseline.get('ignore_dirs') else None
        snap_patterns = read_agentignore_patterns(root_path)
        try:
            raw_after = snapshot_files(root_path, ignore_dirs=ignore_dirs, ignore_patterns=snap_patterns, fast_stat=uses_stat)
            after_snap = {k: v for k, v in raw_after.items() if k != '.' and not k.endswith('/')}
            before_keys = set(before_snap)
            after_keys = set(after_snap)
            added_files = sorted(after_keys - before_keys)
            deleted_files = sorted(before_keys - after_keys)
            modified_files = sorted(k for k in (before_keys & after_keys) if before_snap[k] != after_snap[k])
            changed_union = set(added_files) | set(modified_files) | set(deleted_files)
            preexisting_dirty_unchanged = sorted(
                p for p in (baseline.get('git_dirty_files') or ()) if p not in changed_union
            )
            baseline_source = 'frozen_snapshot_baseline'
            changes_status = 'verified'
            base_git_head = baseline.get('git_head')
        except (HarnessError, OSError):
            changes_status = 'unknown'
    elif committed_during_run and root_path.is_dir():
        git_diff = _reconcile_git_diff_names(root_path, base_head_hint)
        if git_diff is not None:
            added_files = git_diff['added']
            modified_files = git_diff['modified']
            deleted_files = git_diff['deleted']
            base_git_head = git_diff['base_commit']
            changed_union = set(added_files) | set(modified_files) | set(deleted_files)
            preexisting_dirty_unchanged = sorted(
                p for p in (baseline.get('git_dirty_files') or ()) if p not in changed_union
            )
            baseline_source = 'frozen_git_head_diff'
            snapshot_mode = 'git_diff_working_tree'
            changes_status = 'verified'
    elif base_ref and root_path.is_dir():
        git_diff = _reconcile_git_diff_names(root_path, base_ref)
        if git_diff is not None:
            added_files = git_diff['added']
            modified_files = git_diff['modified']
            deleted_files = git_diff['deleted']
            base_git_head = git_diff['base_commit']
            baseline_source = f'git_ref:{base_ref}'
            snapshot_mode = 'git_diff_working_tree'
            changes_status = 'verified'
    elif root_path.is_dir() and current_git_head:
        git_diff = _reconcile_git_diff_names(root_path, 'HEAD')
        if git_diff is not None:
            added_files = git_diff['added']
            modified_files = git_diff['modified']
            deleted_files = git_diff['deleted']
            base_git_head = git_diff['base_commit']
            baseline_source = 'git_head_working_tree'
            snapshot_mode = 'git_diff_working_tree'
            changes_status = 'verified'

    changed_all = sorted(set(added_files) | set(modified_files) | set(deleted_files))
    changed_set = set(changed_all)
    change_kind_map = {}
    for rel in added_files:
        change_kind_map[rel] = '新增'
    for rel in modified_files:
        change_kind_map[rel] = '已更新'
    for rel in deleted_files:
        change_kind_map[rel] = '已删除'

    # 2. 枚举现役活动文件并按类型分组
    active_files, scan_truncated, skipped_dirs_count = _collect_active_repo_files(root_path)
    by_category = {'code': [], 'test': [], 'doc': [], 'config': [], 'other': []}
    changed_by_category = {'code': [], 'test': [], 'doc': [], 'config': [], 'other': []}
    unchanged_docs = []
    for rel in active_files:
        cat = _classify_reconcile_file(rel)
        by_category[cat].append(rel)
        if rel in changed_set:
            changed_by_category[cat].append(rel)
        elif cat == 'doc':
            unchanged_docs.append(rel)
    for rel in deleted_files:
        cat = _classify_reconcile_file(rel)
        if rel not in changed_by_category[cat]:
            changed_by_category[cat].append(rel)

    # 3. 派单流与只读覆盖对账（区分计划文件、可证实交付、引用缺口与测试验收）
    stream_rows = []
    all_planned_files = set()
    all_delivered_files = set()
    all_unverified_planned = set()
    all_citation_gaps = []
    if isinstance(payload, dict):
        raw_streams = _result_streams(payload)
        for idx, st in enumerate(raw_streams, 1):
            if not isinstance(st, dict):
                continue
            inv = st.get('invocation') if isinstance(st.get('invocation'), dict) else {}
            sid = str(
                st.get('stream_id')
                or (f"{st['side']}-S{st['shard_id']}" if st.get('side') and st.get('shard_id') is not None
                    else f"S{st['shard_id']}" if st.get('shard_id') is not None else f'S{idx}')
            )
            planned = _normalize_reconcile_paths(
                st.get('planned_files') or st.get('files') or inv.get('files') or [], root_path,
            )
            ca = st.get('citation_audit') or inv.get('citation_audit') or {}
            inlined = _normalize_reconcile_paths(ca.get('inlined_files') or [], root_path)
            actual_read = _normalize_reconcile_paths(ca.get('actual_files_read') or [], root_path)
            delivered = sorted(set(inlined) | set(actual_read))
            unverified_planned = sorted(set(planned) - set(delivered))
            missing_cited = _normalize_reconcile_paths(ca.get('missing_files') or [], root_path)
            unopened_cited = _normalize_reconcile_paths(ca.get('unopened_cited_files') or [], root_path)
            out_of_range = list(ca.get('out_of_range_lines') or [])
            all_planned_files.update(planned)
            all_delivered_files.update(delivered)
            all_unverified_planned.update(unverified_planned)
            for mf in missing_cited:
                all_citation_gaps.append({'stream_id': sid, 'kind': 'missing_cited_file', 'file': mf})
            for uf in unopened_cited:
                all_citation_gaps.append({'stream_id': sid, 'kind': 'unopened_cited_file', 'file': uf})
            for oor in out_of_range:
                all_citation_gaps.append({'stream_id': sid, 'kind': 'out_of_range_line', 'detail': oor})
            stream_rows.append({
                'stream_id': sid,
                'engine': str(st.get('engine') or payload.get('engine') or ''),
                'model': str(st.get('model') or payload.get('model') or ''),
                'status': str(st.get('status') or ('ok' if st.get('succeeded', payload.get('status') == 'ok') else 'failed')),
                'succeeded': bool(st.get('succeeded', st.get('status', payload.get('status')) == 'ok')),
                'planned_count': len(planned),
                'delivered_count': len(delivered),
                'unverified_planned': unverified_planned,
                'citation_gap_count': len(missing_cited) + len(unopened_cited) + len(out_of_range),
                'verification_executed': bool(st.get('verification_executed', payload.get('verification_executed', False))),
                'verify_exit_code': st.get('verify_exit_code', payload.get('verify_exit_code')),
            })

    # 4. 确定性结构逻辑关系图（AST 直接导入 + Markdown 显式相对链接 + 强反引号引用 vs 普通词法提及计数）
    relations = []
    pending_review_targets = {}  # target_file -> {'target', 'category', 'reasons': [...], 'weight': int}
    weak_mention_docs = set()

    if changed_set and root_path.is_dir():
        changed_py = [rel for rel in changed_all if rel.endswith('.py') and (root_path / rel).is_file()]
        changed_py_mod_keys = {}  # module_key -> changed_rel
        changed_symbols = {}  # symbol_name -> changed_rel
        changed_basename_map = {}  # exact filename (e.g. reply_repeat_guard.py) -> set of rels
        changed_stem_map = {}  # stem (e.g. reply_repeat_guard) -> set of rels
        for rel in changed_all:
            fname = rel.rsplit('/', 1)[-1]
            stem = Path(fname).stem
            changed_basename_map.setdefault(fname, set()).add(rel)
            if len(stem) >= 5:
                changed_stem_map.setdefault(stem.lower(), set()).add(rel)
        for rel in changed_py:
            no_ext = rel[:-3]
            dotted = no_ext.replace('/', '.')
            mod_name = no_ext.rsplit('/', 1)[-1]
            changed_py_mod_keys[dotted] = rel
            if mod_name != '__init__':
                changed_py_mod_keys[mod_name] = rel
            # 同时登记从最末两段起的后缀模块路径（兼容 template/user-guard/hooks/_lib/shell_lex.py）
            parts = dotted.split('.')
            for tail_len in (2, 3):
                if len(parts) >= tail_len:
                    changed_py_mod_keys['.'.join(parts[-tail_len:])] = rel
            meta = _extract_py_ast_metadata(root_path, rel, cap_bytes)
            for sym in meta['symbols']:
                changed_symbols[sym] = rel

        # 4a. Python AST 直接导入边（下游源码与测试 -> 变更 .py）
        if changed_py_mod_keys:
            for rel in active_files:
                if not rel.endswith('.py') or rel in changed_set:
                    continue
                meta = _extract_py_ast_metadata(root_path, rel, cap_bytes)
                hit_sources = sorted({changed_py_mod_keys[m] for m in meta['imports'] if m in changed_py_mod_keys})
                for src_rel in hit_sources:
                    cat = _classify_reconcile_file(rel)
                    rel_type = 'test_imports_changed_code' if cat == 'test' else 'code_imports_changed_code'
                    relations.append({
                        'source': src_rel,
                        'target': rel,
                        'relation': rel_type,
                        'strength': 'strong',
                        'target_status': '未更新',
                    })
                    entry = pending_review_targets.setdefault(rel, {'target': rel, 'category': cat, 'reasons': [], 'weight': 0})
                    entry['reasons'].append(f'AST 直接导入已变模块 `{src_rel}`')
                    entry['weight'] += 3 if cat == 'test' else 2

        # 4b. Markdown 显式相对链接 & 强反引号引用 vs 普通词法提及
        for doc_rel in by_category['doc']:
            if doc_rel in changed_set:
                continue
            md_info = _extract_md_links_and_citations(root_path, doc_rel, cap_bytes)
            strong_reasons = []
            linked_sources = set()
            for link_target, line_no in md_info['links']:
                if link_target in changed_set:
                    linked_sources.add(link_target)
                    relations.append({
                        'source': link_target,
                        'target': doc_rel,
                        'relation': 'doc_links_changed_file',
                        'strength': 'strong',
                        'line': line_no,
                        'target_status': '未更新',
                    })
                    strong_reasons.append(f'L{line_no} 显式链接已变文件 `{link_target}`')
            # 反引号强路径/强文件名/强符号引用
            cited_sources = set()
            for bt in md_info['backticks']:
                bt_norm = bt.replace('\\', '/').lstrip('./')
                if bt_norm in changed_set and bt_norm not in linked_sources:
                    cited_sources.add(bt_norm)
                    relations.append({
                        'source': bt_norm,
                        'target': doc_rel,
                        'relation': 'doc_cites_changed_path',
                        'strength': 'strong',
                        'target_status': '未更新',
                    })
                    strong_reasons.append(f'反引号精确引用已变路径 `{bt_norm}`')
                elif bt_norm in changed_basename_map:
                    for src_rel in sorted(changed_basename_map[bt_norm]):
                        if src_rel not in linked_sources and src_rel not in cited_sources:
                            cited_sources.add(src_rel)
                            relations.append({
                                'source': src_rel,
                                'target': doc_rel,
                                'relation': 'doc_cites_changed_filename',
                                'strength': 'strong',
                                'target_status': '未更新',
                            })
                            strong_reasons.append(f'反引号精确引用已变文件名 `{bt_norm}`')
                elif bt in changed_symbols:
                    src_rel = changed_symbols[bt]
                    if src_rel not in linked_sources and src_rel not in cited_sources:
                        cited_sources.add(src_rel)
                        relations.append({
                            'source': src_rel,
                            'target': doc_rel,
                            'relation': 'doc_cites_changed_symbol',
                            'strength': 'strong',
                            'symbol': bt,
                            'target_status': '未更新',
                        })
                        strong_reasons.append(f'反引号精确引用已变符号 `{bt}`（来自 `{src_rel}`）')
            if strong_reasons:
                dedup_reasons = list(dict.fromkeys(strong_reasons))
                entry = pending_review_targets.setdefault(doc_rel, {'target': doc_rel, 'category': 'doc', 'reasons': [], 'weight': 0})
                entry['reasons'].extend(dedup_reasons)
                entry['weight'] += 4 * len(linked_sources) + 2 * len(cited_sources)
            else:
                low_vis = md_info['visible_text'].lower()
                if any(stem in low_vis for stem in changed_stem_map):
                    weak_mention_docs.add(doc_rel)

    pending_review_list = sorted(
        pending_review_targets.values(),
        key=lambda item: (-item['weight'], item['category'] != 'doc', item['target']),
    )

    # 5. 义务结算（显式义务 + 派单派生义务）
    combined_obligations = _normalize_obligations(obligations)
    if isinstance(ticket, dict) and isinstance(ticket.get('obligations'), list):
        seen_ids = {item['id'] for item in combined_obligations}
        for item in _normalize_obligations(ticket.get('obligations')):
            if item['id'] not in seen_ids:
                combined_obligations.append(item)
                seen_ids.add(item['id'])
    obligation_results = []
    for ob in combined_obligations:
        kind = ob['kind']
        tgt = ob['target']
        if kind == 'read':
            if not tgt:
                st_label, detail = '未知', '未指定目标路径'
            elif tgt in all_delivered_files:
                st_label, detail = '已满足', '已证实交付（内联或回执读取记录命中）'
            else:
                st_label, detail = '缺凭据', '未在已证实交付集合中观测到该文件'
        elif kind == 'modify':
            if not tgt:
                st_label, detail = '未知', '未指定目标路径'
            elif tgt in changed_set:
                st_label, detail = '已满足', f'本轮{change_kind_map.get(tgt, "已变更")}'
            elif changes_status != 'verified':
                st_label, detail = '未知', '缺少基线，无法证实是否修改'
            else:
                st_label, detail = '未改·待复核', '要求修改但起止内容未变'
        elif kind == 'review':
            if tgt and tgt in all_delivered_files:
                st_label, detail = '已满足', '目标已证实交付审查'
            elif tgt and tgt in changed_set:
                st_label, detail = '已满足', '本轮已修改覆盖'
            else:
                st_label, detail = '未改·待复核', '缺少本版复核交付凭据'
        elif kind == 'test':
            executed = any(row['verification_executed'] for row in stream_rows)
            passed = executed and all(
                (row['verify_exit_code'] == 0) for row in stream_rows if row['verification_executed']
            )
            if passed:
                st_label, detail = '已满足', '受管验收命令已运行且退出码为 0'
            elif executed:
                st_label, detail = '已失败', '受管验收命令退出码非零'
            else:
                st_label, detail = '缺凭据', '本回执中未观测到受管测试执行记录'
        elif kind == 'final_audit':
            if stream_rows and all(row['succeeded'] for row in stream_rows):
                st_label, detail = '已满足', '全部派单流成功终态'
            elif stream_rows:
                st_label, detail = '已失败', '存在未成功的派单流'
            else:
                st_label, detail = '缺凭据', '缺少跨端终校回执'
        else:
            st_label, detail = '未知', f'未识别的义务类型: {kind}'
        obligation_results.append({**ob, 'status': st_label, 'detail': detail})

    # 派单自动派生核验项（越界写、测试失败、流失败）
    derived_alerts = []
    if isinstance(payload, dict):
        violations = list(payload.get('scope_violations') or [])
        for st in _result_streams(payload):
            if isinstance(st, dict) and st.get('scope_violations'):
                violations.extend(st['scope_violations'])
        if violations:
            derived_alerts.append({
                'severity': 'red',
                'kind': 'scope_violation',
                'target': ', '.join(sorted(set(str(v) for v in violations))[:5]),
                'detail': f'检测到 {len(set(violations))} 处写入越界',
            })
        failed_streams = [row['stream_id'] for row in stream_rows if not row['succeeded']]
        if failed_streams:
            derived_alerts.append({
                'severity': 'red',
                'kind': 'stream_not_ok',
                'target': ', '.join(failed_streams),
                'detail': f'存在 {len(failed_streams)} 路未成功流（不可宣称全部成功）',
            })
    for ob in obligation_results:
        if ob['status'] in ('已失败', '缺凭据', '未改·待复核'):
            derived_alerts.append({
                'severity': 'red' if ob['status'] in ('已失败', '缺凭据') else 'yellow',
                'kind': f"obligation:{ob['kind']}",
                'target': ob['target'] or ob['id'],
                'detail': f"{ob['id']} [{ob['status']}]：{ob['detail']}",
            })
    if all_unverified_planned:
        derived_alerts.append({
            'severity': 'yellow',
            'kind': 'readonly_delivery_gap',
            'target': ', '.join(sorted(all_unverified_planned)[:5]),
            'detail': f'{len(all_unverified_planned)} 份计划必读文件未在可证实交付集合中出现',
        })
    if all_citation_gaps:
        derived_alerts.append({
            'severity': 'red',
            'kind': 'citation_audit_gap',
            'target': f'{len(all_citation_gaps)} 处引用核验缺口',
            'detail': '回执引用含缺失文件、未打开文件或越界行号',
        })

    # 并轨阶段 3 跨端汇总核对结论（来自显式 --cross-check 或 audit 载荷中的 cross_client_coverage）
    cross_summary = None
    if isinstance(cross_check, dict):
        cc_counts = cross_check.get('counts') if isinstance(cross_check.get('counts'), dict) else {}
        cross_summary = {
            'summary_line': str(cross_check.get('summary_line') or ''),
            'shared_blind_spots': list(cross_check.get('shared_blind_spots') or [])[:20],
            'failed_or_timeout_cells_count': int(cc_counts.get('failed_or_timeout_cells', len(cross_check.get('failed_or_timeout_cells') or []))),
            'divergent_findings_count': int(cc_counts.get('divergent_findings', len(cross_check.get('divergent_findings') or []))),
            'cross_shard_unverified_count': int(cc_counts.get('cross_shard_unverified', 0)),
            'multi_client_delivered_count': int(cc_counts.get('multi_client_delivered', 0)),
        }
        for cc_alert in (cross_check.get('alerts') or []):
            if isinstance(cc_alert, dict):
                derived_alerts.append(cc_alert)
    elif isinstance(payload, dict) and isinstance(payload.get('cross_client_coverage'), dict):
        cov = payload['cross_client_coverage']
        blind = list(cov.get('shared_blind_spots') or [])
        cross_summary = {
            'summary_line': str(cov.get('summary_line') or ''),
            'shared_blind_spots': blind[:20],
            'failed_or_timeout_cells_count': int(cov.get('failed_units_count', 0)),
            'divergent_findings_count': int(cov.get('divergent_findings_count', 0)),
            'cross_shard_unverified_count': 0,
            'multi_client_delivered_count': int(cov.get('dual_delivered_count', 0)),
        }
        if blind:
            derived_alerts.append({
                'severity': 'red',
                'kind': 'shared_blind_spot',
                'target': ', '.join(blind[:5]),
                'detail': f'{len(blind)} 份审计文件在参与端均缺少可证实交付凭据',
            })
        if int(cov.get('divergent_findings_count', 0)) > 0:
            derived_alerts.append({
                'severity': 'yellow',
                'kind': 'cross_client_finding_divergence',
                'target': f"{cov['divergent_findings_count']} 处双端严重度分歧",
                'detail': '双端同位置发现严重度不一致，须主会话按 R6 回源核对原文',
            })

    pending_doc_count = sum(1 for item in pending_review_list if item['category'] == 'doc')
    pending_test_count = sum(1 for item in pending_review_list if item['category'] == 'test')
    obligation_gap_count = sum(1 for ob in obligation_results if ob['status'] not in ('已满足',))
    readonly_gap_count = len(all_unverified_planned) + len(all_citation_gaps)
    cross_gap_count = (
        len(cross_summary['shared_blind_spots']) + cross_summary['divergent_findings_count'] + cross_summary['cross_shard_unverified_count']
        if isinstance(cross_summary, dict) else 0
    )
    summary_parts = [
        f"实改 {len(changed_all)} 文件（源码 {len(changed_by_category['code'])}、测试 {len(changed_by_category['test'])}、文档 {len(changed_by_category['doc'])}、配置 {len(changed_by_category['config'])}）"
        if changes_status == 'verified' else '改动状态未知（缺基线）',
        f'强关联未更新文档 {pending_doc_count} 份',
    ]
    if pending_test_count:
        summary_parts.append(f'强关联未更新测试 {pending_test_count} 份')
    if all_planned_files or all_delivered_files:
        summary_parts.append(f'只读交付 {len(all_delivered_files)}/{max(len(all_planned_files), len(all_delivered_files))}')
    if obligation_gap_count or readonly_gap_count or cross_gap_count:
        summary_parts.append(f'缺口 {obligation_gap_count + readonly_gap_count + cross_gap_count} 项')
    summary_line = '；'.join(summary_parts)

    return {
        'schema_version': 1,
        'run_id': run_id,
        'subcommand': subcommand,
        'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'target_root': str(root_path),
        'baseline_source': baseline_source,
        'snapshot_mode': snapshot_mode,
        'changes_status': changes_status,
        'base_git_head': base_git_head,
        'current_git_head': current_git_head,
        'preexisting_dirty_unchanged': preexisting_dirty_unchanged,
        'scan_truncated': scan_truncated,
        'skipped_dirs_count': skipped_dirs_count,
        'summary_line': summary_line,
        'counts': {
            'active_total': len(active_files),
            'active_by_category': {k: len(v) for k, v in by_category.items()},
            'changed_total': len(changed_all),
            'added': len(added_files),
            'modified': len(modified_files),
            'deleted': len(deleted_files),
            'changed_by_category': {k: len(v) for k, v in changed_by_category.items()},
            'unchanged_active_docs': len(unchanged_docs),
            'pending_review_total': len(pending_review_list),
            'pending_review_docs': pending_doc_count,
            'pending_review_tests': pending_test_count,
            'weak_mention_docs': len(weak_mention_docs),
            'planned_files': len(all_planned_files),
            'delivered_files': len(all_delivered_files),
            'unverified_planned_files': len(all_unverified_planned),
            'citation_gaps': len(all_citation_gaps),
            'obligations_total': len(obligation_results),
            'obligation_gaps': obligation_gap_count,
        },
        'changes': {
            'added': added_files,
            'modified': modified_files,
            'deleted': deleted_files,
            'by_category': changed_by_category,
        },
        'unchanged_active_docs_sample': unchanged_docs[:30],
        'pending_review': pending_review_list,
        'relations': relations,
        'streams': stream_rows,
        'readonly_coverage': {
            'planned_files': sorted(all_planned_files),
            'verified_delivered_files': sorted(all_delivered_files),
            'unverified_planned_files': sorted(all_unverified_planned),
            'citation_gaps': all_citation_gaps,
        },
        'obligations': obligation_results,
        'cross_check_summary': cross_summary,
        'alerts': derived_alerts,
        'honesty_boundary': (
            '本表由纯 Python 静态代码基于文件版本快照、Git 差异、Python AST 导入、Markdown 显式相对链接、反引号精确符号引用及派单回执确定性生成；'
            '仅证明文件版本状态、静态引用关系与回执交付事实，不证明动态调用、深层数理公式或语义逻辑等价性。'
        ),
    }


def render_reconciliation_markdown(report):
    """将机械对账结构化报告渲染为首屏降噪、零幻觉的可查阅 Markdown 表。"""
    c = report.get('counts', {})
    lines = [
        f"# 机械对账表（`{report.get('run_id', '')[:12]}` · `{report.get('subcommand', 'reconcile')}`）",
        '',
        f"- **生成时间**：`{report.get('generated_at', '')}`",
        f"- **目标根目录**：`{report.get('target_root', '')}`",
        f"- **基线口径**：`{report.get('baseline_source', '')}`（快照模式：`{report.get('snapshot_mode', '')}`；改动核验：`{report.get('changes_status', '')}`）",
        f"- **Git 版本**：基线 `{report.get('base_git_head') or '无'}` → 当前 `{report.get('current_git_head') or '无'}`",
        f"- **一句话摘要**：{report.get('summary_line', '')}",
        f"- **确定性边界**：{report.get('honesty_boundary', '')}",
        '',
        '---',
        '',
        '## 一、首屏重点关注（缺口与强显式依赖未更新 Top 10）',
        '',
    ]
    alerts = report.get('alerts') or []
    pending = report.get('pending_review') or []
    if not alerts and not pending:
        lines.append('> 当前未检出硬缺口或一跳强显式依赖未更新项。')
    else:
        lines.append('| 级别 | 类型 | 目标对象 | 确定性依据 |')
        lines.append('| --- | --- | --- | --- |')
        shown = 0
        for item in alerts:
            if shown >= _RECONCILE_TOP_SCREEN_ITEMS:
                break
            icon = '🔴 阻断/缺口' if item.get('severity') == 'red' else '🟡 待核对'
            lines.append(f"| {icon} | `{item.get('kind', '')}` | `{item.get('target', '')}` | {item.get('detail', '')} |")
            shown += 1
        for item in pending:
            if shown >= _RECONCILE_TOP_SCREEN_ITEMS:
                break
            reason = '；'.join(item.get('reasons', [])[:3])
            lines.append(f"| 🟡 未改·待核对 | `{item.get('category', '')}` | `{item.get('target', '')}` | {reason} |")
            shown += 1
        total_focus = len(alerts) + len(pending)
        if total_focus > shown:
            lines.append(f'\n> 首屏已展示前 {shown} 项，其余 {total_focus - shown} 项见下方完整分表。')

    lines.extend([
        '',
        '---',
        '',
        '## 二、代码与文档更新 vs 未更新对账',
        '',
        '| 分类 | 现役总数 | 本轮实改（增/改/删汇总） | 本轮未更新数 |',
        '| --- | ---: | ---: | ---: |',
    ])
    cat_labels = {'code': '源码 (`code`)', 'test': '测试 (`test`)', 'doc': '活动文档 (`doc`)', 'config': '配置 (`config`)', 'other': '其他 (`other`)'}
    for key in ('code', 'test', 'doc', 'config', 'other'):
        tot = c.get('active_by_category', {}).get(key, 0)
        chg = c.get('changed_by_category', {}).get(key, 0)
        lines.append(f'| {cat_labels[key]} | {tot} | {chg} | {max(0, tot - chg)} |')
    if c.get('weak_mention_docs', 0):
        lines.append(f"\n> 另检出 `{c['weak_mention_docs']}` 份活动文档仅含普通词法提及（无显式链接或反引号精确符号引用），为防扇出噪声未标红。")
    if report.get('preexisting_dirty_unchanged'):
        lines.append(f"\n> 开工前原本已脏但本轮内容未变的文件（已隔离，未归属本轮改动，共 `{len(report['preexisting_dirty_unchanged'])}` 份）：`{'`, `'.join(report['preexisting_dirty_unchanged'][:10])}`")

    lines.extend(['', '### 本轮已变更文件清单', ''])
    changes = report.get('changes', {})
    changed_rows = (
        [(p, '新增') for p in changes.get('added', [])]
        + [(p, '已更新') for p in changes.get('modified', [])]
        + [(p, '已删除') for p in changes.get('deleted', [])]
    )
    if not changed_rows:
        lines.append('（本轮零文件变更，或缺少基线未能计算变更。）')
    else:
        lines.append('| 变更状态 | 分类 | 文件路径 |')
        lines.append('| --- | --- | --- |')
        for rel, st_lbl in changed_rows[:200]:
            lines.append(f'| {st_lbl} | `{_classify_reconcile_file(rel)}` | `{rel}` |')
        if len(changed_rows) > 200:
            lines.append(f'\n> 共 {len(changed_rows)} 份变更文件，表内展示前 200 项。')

    lines.extend(['', '---', '', '## 三、确定性结构逻辑关系（变更源 → 未更新受影响对象）', ''])
    if not pending:
        lines.append('（无一跳强显式依赖的未更新对象。）')
    else:
        lines.append('| 受影响未更新对象 | 分类 | 状态 | 确定性结构依据 |')
        lines.append('| --- | --- | --- | --- |')
        for item in pending[:80]:
            lines.append(f"| `{item['target']}` | `{item['category']}` | 未改·待核对 | {'；'.join(item['reasons'][:4])} |")
        if len(pending) > 80:
            lines.append(f'\n> 共 {len(pending)} 份受影响未更新对象，表内展示前 80 项。')

    lines.extend(['', '---', '', '## 四、派单流状态、只读交付覆盖与义务结算', ''])
    streams = report.get('streams') or []
    if streams:
        lines.append('| 流号 | 引擎 / 模型 | 终态 | 计划文件 | 可证实交付 | 引用缺口 | 验收命令 |')
        lines.append('| --- | --- | --- | ---: | ---: | ---: | --- |')
        for row in streams:
            v_str = (
                f"exit={row['verify_exit_code']}" if row.get('verification_executed')
                else '未执行'
            )
            lines.append(
                f"| `{row['stream_id']}` | `{row['engine']}` / `{row['model'] or '未声明'}` | `{row['status']}` | "
                f"{row['planned_count']} | {row['delivered_count']} | {row['citation_gap_count']} | {v_str} |"
            )
        rc = report.get('readonly_coverage', {})
        if rc.get('unverified_planned_files'):
            lines.append(f"\n- **未证实交付的计划文件**：`{'`, `'.join(rc['unverified_planned_files'][:20])}`")
        if rc.get('citation_gaps'):
            lines.append(f"- **引用核验缺口数**：`{len(rc['citation_gaps'])}` 处")
    else:
        lines.append('（本次为独立点名对账，未绑定派单回执流。）')

    obs = report.get('obligations') or []
    if obs:
        lines.extend(['', '### 显式义务结算', '', '| 义务 ID | 类型 | 目标对象 | 结算状态 | 依据 |', '| --- | --- | --- | --- | --- |'])
        for ob in obs:
            lines.append(f"| `{ob['id']}` | `{ob['kind']}` | `{ob['target'] or '-'}` | **{ob['status']}** | {ob['detail']} |")

    ccs = report.get('cross_check_summary')
    if isinstance(ccs, dict):
        lines.extend([
            '',
            '### 跨端汇总核对并轨',
            '',
            f"- **跨端核对摘要**：{ccs.get('summary_line') or '已并轨跨端覆盖核验'}",
            f"- **跨客户端实证文件数**：`{ccs.get('multi_client_delivered_count', 0)}`；**失败/超时格数**：`{ccs.get('failed_or_timeout_cells_count', 0)}`；**跨端分歧发现数**：`{ccs.get('divergent_findings_count', 0)}`；**跨片缺联审边数**：`{ccs.get('cross_shard_unverified_count', 0)}`",
        ])
        if ccs.get('shared_blind_spots'):
            lines.append(f"- **全端共同盲区**：`{'`, `'.join(ccs['shared_blind_spots'][:20])}`")

    return '\n'.join(lines).rstrip() + '\n'


def _write_reconciliation_artifact(report, md_path, *, json_path=None, redact=False):
    """安全校验非符号链接后原子写出机械对账 Markdown（及可选 JSON）。"""
    md_target = Path(md_path).expanduser()
    for anc in (md_target.parent, md_target):
        if os.path.lexists(anc) and _is_link(anc):
            raise HarnessError(f'拒绝通过符号链接写入机械对账表: {anc}')
    md_text = render_reconciliation_markdown(report)
    if redact:
        md_text = _redact_public(md_text)
    _atomic_text(md_target, md_text)
    if json_path is not None:
        jt = Path(json_path).expanduser()
        for anc in (jt.parent, jt):
            if os.path.lexists(anc) and _is_link(anc):
                raise HarnessError(f'拒绝通过符号链接写入机械对账 JSON: {anc}')
        _atomic_json(jt, _redact_public(report) if redact else report)
    return str(md_target.resolve())


def cmd_reconcile(args):
    """用户点名离线硬触发：生成纯确定性机械对账表并返回单行摘要与查看链接。"""
    target, root = _target(args)
    if getattr(args, 'save_baseline', ''):
        snap_patterns = read_agentignore_patterns(root) if root.is_dir() else None
        base_obj = capture_reconcile_baseline(root, ignore_patterns=snap_patterns)
        out_base = Path(args.save_baseline).expanduser()
        if os.path.lexists(out_base) and _is_link(out_base):
            raise HarnessError(f'拒绝通过符号链接写入基线文件: {out_base}')
        _atomic_json(out_base, base_obj)
        res = {
            'status': 'ok',
            'baseline_file': str(out_base.resolve()),
            'root': base_obj['root'],
            'git_head': base_obj['git_head'],
            'snapshot_mode': base_obj['snapshot_mode'],
            'tracked_file_count': len(base_obj.get('snapshot') or {}),
            'preexisting_dirty_count': len(base_obj.get('git_dirty_files') or []),
        }
        return _inspection_print(args, res)

    baseline = None
    if getattr(args, 'baseline_file', ''):
        baseline = _read_json(Path(args.baseline_file).expanduser())
    receipt_payload = None
    if getattr(args, 'receipt', ''):
        receipt_payload = _read_json(Path(args.receipt).expanduser())
    obligations = None
    if getattr(args, 'obligations_file', ''):
        raw_ob = _read_json(Path(args.obligations_file).expanduser())
        obligations = raw_ob.get('obligations') if isinstance(raw_ob, dict) else raw_ob
    cross_check_obj = None
    if getattr(args, 'cross_check', ''):
        cross_check_obj = _read_json(Path(args.cross_check).expanduser())

    run_id = uuid.uuid4().hex
    report = build_reconciliation_report(
        root,
        run_id=run_id,
        subcommand='reconcile',
        payload=receipt_payload,
        baseline=baseline,
        base_ref=getattr(args, 'base_ref', '') or '',
        obligations=obligations,
        cross_check=cross_check_obj,
    )
    if getattr(args, 'output_md', ''):
        md_path = Path(args.output_md).expanduser()
    else:
        plan_dir = resolve_dispatch_plan_dir(getattr(args, 'plan_dir', None))
        md_path = plan_dir / 'reconcile' / f'{run_id}_reconcile.md'
    saved_md = _write_reconciliation_artifact(report, md_path, redact=bool(getattr(args, 'redact', False)))
    md_uri = Path(saved_md).as_uri()
    chat_line = f"机械对账（{report['summary_line']}）：[{Path(saved_md).name}]({md_uri})"
    out = {
        'status': 'ok',
        'run_id': run_id,
        'report_file': saved_md,
        'chat_line': chat_line,
        'summary_line': report['summary_line'],
        'counts': report['counts'],
        'baseline_source': report['baseline_source'],
        'changes_status': report['changes_status'],
        'alerts': report['alerts'][:_RECONCILE_TOP_SCREEN_ITEMS],
        'pending_review_top': report['pending_review'][:_RECONCILE_TOP_SCREEN_ITEMS],
        'honesty_boundary': report['honesty_boundary'],
    }
    return _inspection_print(args, out)


# ════════════════════════════════════════════════════════════════════════════
# ⑦C 阶段 2：读前尺寸评估、超大单文件行段切分与保守层级分片规划（read-plan）
# ════════════════════════════════════════════════════════════════════════════

_READ_PLAN_LINE_OVERHEAD_BYTES = 10
_READ_PLAN_FILE_HEADER_BYTES = 128
_READ_PLAN_SECTION_OVERLAP_LINES = 20
_READ_PLAN_DEFAULT_CONSERVATIVE_RATIO = 0.25
_READ_PLAN_DEFAULT_CONCURRENCY = 2
_READ_PLAN_MD_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*$')
_READ_PLAN_PY_BOUNDARY_RE = re.compile(r'^(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)')
_READ_PLAN_CORE_FILENAME_HINTS = frozenset({
    'agents.md', 'claude.md', 'readme.md', 'constitution.md', 'spec.md', 'design.md',
    'fleet_profile.json', 'pyproject.toml', 'cargo.toml', 'cmakelists.txt',
})


def _estimate_inlined_file_bytes(raw_bytes, line_count):
    """估算带精确行号内联传输时的真实 UTF-8 字节数（正文 + 行号前缀 + 文件头）。"""
    return int(raw_bytes) + max(1, int(line_count)) * _READ_PLAN_LINE_OVERHEAD_BYTES + _READ_PLAN_FILE_HEADER_BYTES


def _read_plan_collect_files(root_path, *, explicit_files=None, include_dirs=None, max_files=None):
    """独立枚举授权范围内的可读文本文件，并如实记录被默认排除的归档目录与不可读跳过项。"""
    cap = _positive_int(max_files) or _RECONCILE_MAX_ACTIVE_FILES
    excluded_cfg = _reconcile_excluded_dirs() | set(_snapshot_ignored_dirs()) | IGNORED_DIRS
    extra_inc = {
        str(d).strip().replace('\\', '/').strip('/')
        for d in (include_dirs or ())
        if isinstance(d, str) and str(d).strip()
    }
    extra_inc_low = {d.lower() for d in extra_inc}
    records, skipped, excluded_archive_dirs = [], [], []
    truncated = False

    def _inspect_rel(rel_posix):
        full = (root_path / rel_posix)
        if not os.path.lexists(full) or _is_link(full):
            skipped.append({'file': rel_posix, 'reason': 'missing_or_symlink'})
            return
        try:
            resolved = full.resolve()
            if not resolved.is_relative_to(root_path) or not resolved.is_file():
                skipped.append({'file': rel_posix, 'reason': 'outside_root_or_not_file'})
                return
            st = resolved.stat()
            raw_bytes = int(st.st_size)
            with open(resolved, 'rb') as fh:
                sample = fh.read(min(raw_bytes, 8192))
            if b'\x00' in sample:
                skipped.append({'file': rel_posix, 'reason': 'binary_file'})
                return
            line_count = 0
            with open(resolved, 'rb') as fh:
                for chunk in iter(lambda: fh.read(65536), b''):
                    line_count += chunk.count(b'\n')
            if raw_bytes > 0:
                line_count = max(1, line_count + (0 if sample.endswith(b'\n') and raw_bytes <= 8192 else 1))
            records.append({
                'rel_path': rel_posix,
                'bytes': raw_bytes,
                'line_count': line_count,
                'tokens': estimate_tokens_from_bytes(raw_bytes),
                'inlined_bytes': _estimate_inlined_file_bytes(raw_bytes, line_count),
                'category': _classify_reconcile_file(rel_posix),
            })
        except OSError as exc:
            skipped.append({'file': rel_posix, 'reason': f'read_error:{exc.__class__.__name__}'})

    if explicit_files:
        seen = set()
        for raw in explicit_files:
            rel = _normalize_rel_file(raw, root_path)
            if rel in seen:
                continue
            seen.add(rel)
            _inspect_rel(rel)
        return records, skipped, excluded_archive_dirs, False

    for dirpath, dirs, files in os.walk(root_path, followlinks=False):
        dpath = Path(dirpath)
        rel_dir = '' if dpath == root_path else dpath.relative_to(root_path).as_posix()
        kept = []
        for dirname in sorted(dirs):
            cand = dpath / dirname
            sub_rel = f'{rel_dir}/{dirname}' if rel_dir else dirname
            low = dirname.lower()
            explicitly_included = (
                sub_rel in extra_inc or sub_rel.lower() in extra_inc_low
                or dirname in extra_inc or low in extra_inc_low
            )
            if _is_link(cand):
                skipped.append({'file': sub_rel + '/', 'reason': 'symlink_dir'})
                continue
            is_archive_like = (
                low in ('archive', 'archived', 'outdated')
                or low.startswith(('archive_', 'outdated_'))
                or '历史' in dirname or '归档' in dirname
            )
            is_excluded = (
                dirname in excluded_cfg or low in excluded_cfg
                or (low.startswith('.') and dirname != '.github')
                or is_archive_like
            )
            if is_excluded and not explicitly_included:
                if is_archive_like:
                    try:
                        approx_cnt = sum(1 for _ in cand.rglob('*') if _.is_file())
                    except OSError:
                        approx_cnt = 0
                    excluded_archive_dirs.append({'dir': sub_rel, 'file_count': approx_cnt})
                continue
            kept.append(dirname)
        dirs[:] = kept
        for fname in sorted(files):
            if len(records) >= cap:
                truncated = True
                break
            rel_posix = f'{rel_dir}/{fname}' if rel_dir else fname
            _inspect_rel(rel_posix)
        if truncated:
            break
    return records, skipped, excluded_archive_dirs, truncated


def _split_oversized_file_ranges(root_path, rec, max_inlined_bytes, max_tokens, *, overlap_lines=_READ_PLAN_SECTION_OVERLAP_LINES):
    """将超出单分片内联字节或 Token 预算的大文件按章节/顶层定义边界切分为连续带重叠的行段。"""
    rel = rec['rel_path']
    full = root_path / rel
    text = full.read_text(encoding='utf-8', errors='replace')
    raw_lines = text.splitlines(keepends=True)
    total_lines = len(raw_lines)
    if total_lines == 0:
        return [{
            'kind': 'file_range', 'file': rel, 'line_start': 1, 'line_end': 1, 'total_lines': 0,
            'heading': '', 'bytes': 0, 'inlined_bytes': _READ_PLAN_FILE_HEADER_BYTES,
            'tokens': 0, 'single_line_over_cap': False, 'category': rec['category'],
        }]
    line_byte_sizes = [len(ln.encode('utf-8')) for ln in raw_lines]
    is_md = Path(rel).suffix.lower() in ('.md', '.rst', '.txt')
    is_py = Path(rel).suffix.lower() == '.py'
    boundaries = {}
    for idx, ln in enumerate(raw_lines):
        stripped = ln.strip()
        if is_md:
            m = _READ_PLAN_MD_HEADING_RE.match(stripped)
            if m:
                boundaries[idx] = f'{m.group(1)} {m.group(2)[:64]}'
        elif is_py:
            m = _READ_PLAN_PY_BOUNDARY_RE.match(ln)
            if m:
                boundaries[idx] = m.group(0)[:64]

    eff_max_bytes = max(4096, int(max_inlined_bytes))
    eff_max_tokens = max(1000, int(max_tokens))
    ranges = []
    start_idx = 0
    last_heading = ''
    while start_idx < total_lines:
        cur_raw = 0
        cur_inlined = _READ_PLAN_FILE_HEADER_BYTES
        end_idx = start_idx
        single_over = False
        while end_idx < total_lines:
            add_raw = line_byte_sizes[end_idx]
            next_raw = cur_raw + add_raw
            next_inlined = cur_inlined + add_raw + _READ_PLAN_LINE_OVERHEAD_BYTES
            next_tokens = estimate_tokens_from_bytes(next_raw)
            if end_idx > start_idx and (next_inlined > eff_max_bytes or next_tokens > eff_max_tokens):
                break
            cur_raw = next_raw
            cur_inlined = next_inlined
            if end_idx == start_idx and (cur_inlined > eff_max_bytes or next_tokens > eff_max_tokens):
                single_over = True
                end_idx += 1
                break
            end_idx += 1
        end_inclusive = max(start_idx, end_idx - 1)
        # 非末段时优先在后 28% 区间内的结构边界（标题或顶层 def/class）整齐切分
        if end_inclusive < total_lines - 1 and end_inclusive - start_idx >= 12:
            search_lo = start_idx + max(1, int((end_inclusive - start_idx + 1) * 0.72))
            chosen_cut = None
            for cand in range(end_inclusive, search_lo - 1, -1):
                if cand in boundaries and cand > start_idx:
                    chosen_cut = cand - 1
                    break
            if chosen_cut is not None and chosen_cut >= start_idx:
                end_inclusive = chosen_cut
        seg_raw = sum(line_byte_sizes[start_idx:end_inclusive + 1])
        seg_lines = end_inclusive - start_idx + 1
        seg_inlined = _estimate_inlined_file_bytes(seg_raw, seg_lines)
        seg_heading = ''
        for b_idx in range(start_idx, end_inclusive + 1):
            if b_idx in boundaries:
                seg_heading = boundaries[b_idx]
                break
        if not seg_heading:
            for b_idx in range(start_idx - 1, -1, -1):
                if b_idx in boundaries:
                    seg_heading = boundaries[b_idx]
                    break
        if seg_heading:
            last_heading = seg_heading
        ranges.append({
            'kind': 'file_range',
            'file': rel,
            'line_start': start_idx + 1,
            'line_end': end_inclusive + 1,
            'total_lines': total_lines,
            'heading': seg_heading or last_heading,
            'bytes': seg_raw,
            'inlined_bytes': seg_inlined,
            'tokens': estimate_tokens_from_bytes(seg_raw),
            'single_line_over_cap': single_over,
            'category': rec['category'],
        })
        if end_inclusive >= total_lines - 1:
            break
        eff_overlap = min(max(0, int(overlap_lines)), max(0, (end_inclusive - start_idx) // 3))
        start_idx = max(start_idx + 1, end_inclusive + 1 - eff_overlap)
    return ranges


def _build_read_plan_dependency_order(root_path, records):
    """基于 Python AST 直接导入与 Markdown 显式相对链接对文件聚组排序，并返回有向显式依赖边。"""
    rel_set = {rec['rel_path'] for rec in records}
    by_rel = {rec['rel_path']: rec for rec in records}
    py_mod_to_rel = {}
    for rel in rel_set:
        if rel.endswith('.py'):
            mod = rel[:-3].replace('/', '.')
            py_mod_to_rel[mod] = rel
            if mod.endswith('.__init__'):
                py_mod_to_rel[mod[:-9]] = rel
            py_mod_to_rel.setdefault(Path(rel).stem, rel)

    hd = _headless_tunables()
    cap_bytes = (_positive_int(hd.get('grounding_max_anchor_file_mib')) or 2) * 1024 ** 2
    edges = []
    adj = {rel: set() for rel in rel_set}
    for rec in records:
        rel = rec['rel_path']
        full = root_path / rel
        if rel.endswith('.py'):
            meta = _extract_py_ast_metadata(root_path, rel, cap_bytes)
            for mod in sorted(meta['imports']):
                target_rel = py_mod_to_rel.get(mod)
                if not target_rel and '.' in mod:
                    target_rel = py_mod_to_rel.get(mod.rsplit('.', 1)[0])
                if not target_rel:
                    target_rel = py_mod_to_rel.get(mod.rsplit('.', 1)[-1])
                if target_rel and target_rel != rel and target_rel in rel_set:
                    edges.append({'source': rel, 'target': target_rel, 'relation': 'py_import'})
                    adj[rel].add(target_rel)
                    adj[target_rel].add(rel)
        elif Path(rel).suffix.lower() in ('.md', '.rst'):
            md_info = _extract_md_links_and_citations(root_path, rel, cap_bytes)
            seen_targets = set()
            for target_rel, _lineno in md_info['links']:
                if target_rel in rel_set and target_rel != rel and target_rel not in seen_targets:
                    seen_targets.add(target_rel)
                    edges.append({'source': rel, 'target': target_rel, 'relation': 'md_link'})
                    adj[rel].add(target_rel)
                    adj[target_rel].add(rel)

    # 连通强依赖组件优先按目录聚合，组件内部按 BFS 邻接排序，确保源码与配套文档/测试尽量同片
    visited = set()
    components = []
    for rel in sorted(rel_set, key=lambda r: (r.split('/')[0] if '/' in r else '', r)):
        if rel in visited:
            continue
        comp, queue = [], [rel]
        visited.add(rel)
        while queue:
            cur = queue.pop(0)
            comp.append(cur)
            for nxt in sorted(adj.get(cur, ())):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        components.append(comp)
    components.sort(key=lambda c: (-len(c), c[0]))
    ordered_records = [by_rel[rel] for comp in components for rel in comp]
    return ordered_records, edges


def _resolve_read_plan_engines(backends, caller, raw_engines, cross_client_mode):
    """离线解析读前评估的目标执行引擎与各自安全窗口、内联上限和单端并发上限。"""
    hd = _headless_tunables()
    fallback_budget = _positive_int(hd.get('unlisted_window_fallback_tokens')) or 120_000
    worker_caps = hd.get('batch_max_workers_per_backend') or {}
    specs = []
    if raw_engines:
        for item in raw_engines:
            for part in str(item or '').split(','):
                s = part.strip()
                if s and s not in specs:
                    specs.append(s)
    else:
        ready = [p for p in ('codex', 'dsh', 'agy', 'claude') if _peer_auto_ready(backends, p)]
        if cross_client_mode in ('risk-tiered', 'all-engines') and len(ready) >= 2:
            specs = [f'{ready[0]}:@review', f'{ready[1]}:@review']
        elif ready:
            specs = [f'{ready[0]}:@review']
        else:
            specs = ['codex:default']

    resolved = []
    seen_specs = set()
    for raw_spec in specs:
        try:
            chosen = choose_engine(backends, caller, raw_spec) if raw_spec.startswith('role:') else raw_spec
        except HarnessError:
            chosen = raw_spec
        prefix, _ = _engine_parts(chosen)
        if prefix not in backends:
            raise HarnessError(f'read-plan 遇到未知后端引擎规格: {raw_spec}')
        try:
            prof = resolve_execution_profile(backends, chosen, 'high', kind='review')
            cap = prof.get('capacity') or {}
            win = cap.get('selected_window_tokens')
            budget = cap.get('input_budget_tokens') or fallback_budget
            model_name = prof.get('model') or chosen
            spec_str = prof.get('spec') or chosen
            ready_flag = bool(_peer_auto_ready(backends, prefix)) and not prof.get('execution_blocked_reason')
        except HarnessError:
            win = None
            budget = fallback_budget
            model_name = chosen
            spec_str = chosen
            ready_flag = False
        if spec_str in seen_specs:
            continue
        seen_specs.add(spec_str)
        resolved.append({
            'engine': spec_str,
            'requested': raw_spec,
            'prefix': prefix,
            'model': model_name,
            'ready': ready_flag,
            'context_window_tokens': win,
            'input_budget_tokens': int(budget),
            'inline_cap_bytes': _inline_readonly_cap_bytes(prefix),
            'max_workers': _positive_int(worker_caps.get(prefix)) or 2,
            'clean_room_supported': prefix in ('codex', 'claude', 'dsh'),
        })
    return resolved


def build_read_plan(
    target_root,
    *,
    plan_id='',
    files=None,
    shared_files=None,
    core_files=None,
    engines=None,
    backends=None,
    caller='caller',
    shard_tokens=None,
    max_concurrency=None,
    context_used_tokens=0,
    cross_client_mode='risk-tiered',
    include_dirs=None,
    focus='',
    clean_room=False,
):
    """纯离线读前尺寸评估与保守层级分片规划（双口径硬约束 + 超大单文件行段切分 + 跨端波次编排）。"""
    root_path = Path(target_root).expanduser().resolve()
    if root_path.is_file():
        explicit_from_target = [root_path.name]
        root_path = root_path.parent
    else:
        explicit_from_target = None
    if not root_path.is_dir():
        raise HarnessError(f'read-plan 目标目录不存在: {target_root}')

    plan_id = plan_id or uuid.uuid4().hex
    hd = _headless_tunables()
    default_shard = _positive_int(hd.get('default_shard_tokens')) or 120_000
    audit_max_workers = _positive_int(hd.get('audit_max_workers')) or 4
    raw_ratio = hd.get('read_plan_shard_budget_ratio', _READ_PLAN_DEFAULT_CONSERVATIVE_RATIO)
    try:
        conservative_ratio = min(0.5, max(0.1, float(raw_ratio)))
    except (TypeError, ValueError):
        conservative_ratio = _READ_PLAN_DEFAULT_CONSERVATIVE_RATIO

    resolved_backends = backends if backends is not None else discover_backends(refresh_model_cache=False)
    engine_profiles = _resolve_read_plan_engines(resolved_backends, caller, engines, cross_client_mode)

    min_input_budget = min(ep['input_budget_tokens'] for ep in engine_profiles)
    min_inline_cap = min(ep['inline_cap_bytes'] for ep in engine_profiles)
    reserve_tokens = max(8000, int(min_input_budget * 0.10))
    used_tokens = max(0, int(context_used_tokens or 0))
    remaining_tokens = max(4000, min_input_budget - used_tokens - reserve_tokens)
    conservative_target = min(default_shard, max(8000, int(min_input_budget * conservative_ratio)), remaining_tokens)
    if shard_tokens is not None and int(shard_tokens) > 0:
        conservative_target = min(conservative_target, int(shard_tokens))

    # 1. 收集共享核心材料与目标读取范围
    norm_shared = _normalize_reconcile_paths(shared_files or [], root_path)
    norm_core = set(_normalize_reconcile_paths(core_files or [], root_path)) | set(norm_shared)
    explicit_list = list(files or []) if files else explicit_from_target
    if explicit_list is not None:
        all_explicit = list(dict.fromkeys(list(norm_shared) + list(explicit_list)))
        all_records, skipped, excluded_archives, scan_truncated = _read_plan_collect_files(
            root_path, explicit_files=all_explicit, include_dirs=include_dirs,
        )
    else:
        all_records, skipped, excluded_archives, scan_truncated = _read_plan_collect_files(
            root_path, include_dirs=include_dirs,
        )
        present_rels = {r['rel_path'] for r in all_records}
        missing_shared = [s for s in norm_shared if s not in present_rels]
        if missing_shared:
            extra_recs, extra_skip, _, _ = _read_plan_collect_files(root_path, explicit_files=missing_shared)
            all_records.extend(extra_recs)
            skipped.extend(extra_skip)

    shared_set = set(norm_shared)
    shared_records = [r for r in all_records if r['rel_path'] in shared_set]
    target_records = [r for r in all_records if r['rel_path'] not in shared_set]
    shared_bytes = sum(r['bytes'] for r in shared_records)
    shared_inlined_bytes = sum(r['inlined_bytes'] for r in shared_records)
    shared_tokens = sum(r['tokens'] for r in shared_records)

    capacity_alerts = []
    shared_mode = 'inlined_per_shard' if shared_records else 'none'
    avail_inlined_bytes = min_inline_cap - shared_inlined_bytes - 4096
    avail_tokens = conservative_target - shared_tokens - 2000
    if shared_records and (avail_inlined_bytes < 16384 or avail_tokens < 4000):
        shared_mode = 'anchor_reference_only'
        capacity_alerts.append({
            'severity': 'red',
            'kind': 'shared_context_exceeds_budget',
            'detail': (
                f'共享核心材料（{len(shared_records)} 份，内联 {shared_inlined_bytes / 1024:.1f} KiB / {shared_tokens} tokens）'
                f'超出最严格单片内联上限（{min_inline_cap // 1024} KiB）或保守预算（{conservative_target} tokens），'
                '已自动降级为锚点引用模式，防止每片超窗。'
            ),
        })
        avail_inlined_bytes = max(16384, min_inline_cap - 4096)
        avail_tokens = max(4000, conservative_target - 2000)
    elif shared_records and (shared_inlined_bytes > int(min_inline_cap * 0.5) or shared_tokens > int(conservative_target * 0.45)):
        capacity_alerts.append({
            'severity': 'yellow',
            'kind': 'shared_context_heavy',
            'detail': (
                f'共享核心材料占用了内联上限的 {shared_inlined_bytes / max(1, min_inline_cap) * 100:.0f}%，'
                '建议仅保留关键裁定章节或拆分子锚点。'
            ),
        })
    if used_tokens > 0 and remaining_tokens <= 12000:
        capacity_alerts.append({
            'severity': 'red',
            'kind': 'context_remaining_low',
            'detail': f'当前上下文已占约 {used_tokens} tokens，剩余安全预算仅约 {remaining_tokens} tokens，建议新开会话或交后台无头分片执行。',
        })

    # 2. 依赖感知聚类排序 + 超大单文件行段切分
    ordered_records, dep_edges = _build_read_plan_dependency_order(root_path, target_records)
    units = []
    oversized_splits = []
    for rec in ordered_records:
        if rec['inlined_bytes'] > avail_inlined_bytes or rec['tokens'] > avail_tokens:
            ranges = _split_oversized_file_ranges(root_path, rec, avail_inlined_bytes, avail_tokens)
            oversized_splits.append({
                'file': rec['rel_path'],
                'total_lines': rec['line_count'],
                'bytes': rec['bytes'],
                'inlined_bytes': rec['inlined_bytes'],
                'range_count': len(ranges),
            })
            units.extend(ranges)
        else:
            units.append({
                'kind': 'file',
                'file': rec['rel_path'],
                'line_start': 1,
                'line_end': rec['line_count'],
                'total_lines': rec['line_count'],
                'heading': '',
                'bytes': rec['bytes'],
                'inlined_bytes': rec['inlined_bytes'],
                'tokens': rec['tokens'],
                'single_line_over_cap': False,
                'category': rec['category'],
            })

    # 3. 双口径（内联 UTF-8 字节 + 保守 Token 预算）严格装箱
    shards = []
    cur_units, cur_bytes, cur_inlined, cur_tokens = [], 0, 0, 0
    for unit in units:
        exceeds_bytes = bool(cur_units) and (cur_inlined + unit['inlined_bytes'] > avail_inlined_bytes)
        exceeds_tokens = bool(cur_units) and (cur_tokens + unit['tokens'] > avail_tokens)
        if exceeds_bytes or exceeds_tokens:
            shards.append({
                'shard_id': len(shards) + 1,
                'units': cur_units,
                'files': list(dict.fromkeys(u['file'] for u in cur_units)),
                'line_ranges': [
                    {'file': u['file'], 'line_start': u['line_start'], 'line_end': u['line_end'], 'heading': u['heading']}
                    for u in cur_units if u['kind'] == 'file_range'
                ],
                'raw_bytes': cur_bytes,
                'inlined_bytes': cur_inlined + (shared_inlined_bytes if shared_mode == 'inlined_per_shard' else 0),
                'estimated_tokens': cur_tokens + (shared_tokens if shared_mode == 'inlined_per_shard' else 0),
                'over_inline_cap': (cur_inlined + (shared_inlined_bytes if shared_mode == 'inlined_per_shard' else 0)) > min_inline_cap,
            })
            cur_units, cur_bytes, cur_inlined, cur_tokens = [], 0, 0, 0
        cur_units.append(unit)
        cur_bytes += unit['bytes']
        cur_inlined += unit['inlined_bytes']
        cur_tokens += unit['tokens']
    if cur_units:
        shards.append({
            'shard_id': len(shards) + 1,
            'units': cur_units,
            'files': list(dict.fromkeys(u['file'] for u in cur_units)),
            'line_ranges': [
                {'file': u['file'], 'line_start': u['line_start'], 'line_end': u['line_end'], 'heading': u['heading']}
                for u in cur_units if u['kind'] == 'file_range'
            ],
            'raw_bytes': cur_bytes,
            'inlined_bytes': cur_inlined + (shared_inlined_bytes if shared_mode == 'inlined_per_shard' else 0),
            'estimated_tokens': cur_tokens + (shared_tokens if shared_mode == 'inlined_per_shard' else 0),
            'over_inline_cap': (cur_inlined + (shared_inlined_bytes if shared_mode == 'inlined_per_shard' else 0)) > min_inline_cap,
        })

    # 4. 计算跨片显式依赖边并标注高风险/核心分片
    file_to_shards = {}
    for sh in shards:
        for rel in sh['files']:
            file_to_shards.setdefault(rel, []).append(sh['shard_id'])
    cross_shard_edges = []
    seen_cross = set()
    for edge in dep_edges:
        s_ids = file_to_shards.get(edge['source']) or []
        t_ids = file_to_shards.get(edge['target']) or []
        if not s_ids or not t_ids or (set(s_ids) & set(t_ids)):
            continue
        key = (edge['source'], edge['target'], edge['relation'])
        if key in seen_cross:
            continue
        seen_cross.add(key)
        cross_shard_edges.append({
            'from_shard': s_ids[0],
            'to_shard': t_ids[0],
            'source': edge['source'],
            'target': edge['target'],
            'relation': edge['relation'],
        })

    cross_shard_ids = {e['from_shard'] for e in cross_shard_edges} | {e['to_shard'] for e in cross_shard_edges}
    for sh in shards:
        is_core = bool(
            any(f in norm_core for f in sh['files'])
            or any(Path(f).name.lower() in _READ_PLAN_CORE_FILENAME_HINTS for f in sh['files'])
            or bool(sh['line_ranges'])
            or sh['shard_id'] in cross_shard_ids
        )
        sh['is_core_shard'] = is_core

    # 5. 保守并发分层波次与可直接送入 plan --spec-file 的多端工作流清单
    sum_caps = sum(ep['max_workers'] for ep in engine_profiles)
    req_conc = _positive_int(max_concurrency) or _READ_PLAN_DEFAULT_CONCURRENCY
    eff_concurrency = max(1, min(req_conc, audit_max_workers, max(1, sum_caps)))

    mode_norm = str(cross_client_mode or 'risk-tiered').strip().lower()
    if mode_norm not in ('single', 'risk-tiered', 'all-engines'):
        mode_norm = 'risk-tiered'
    if mode_norm in ('risk-tiered', 'all-engines') and len(engine_profiles) < 2:
        capacity_alerts.append({
            'severity': 'yellow',
            'kind': 'single_engine_available',
            'detail': '当前仅解析到 1 个可用执行端，无法形成跨客户端双盲覆盖；可显式传入多个 --engines 或补齐对等端后重试。',
        })

    planned_assignments = []
    rr_idx = 0
    for sh in shards:
        if mode_norm == 'all-engines':
            assigned = list(engine_profiles)
        elif mode_norm == 'risk-tiered' and sh['is_core_shard'] and len(engine_profiles) >= 2:
            assigned = list(engine_profiles)
        elif mode_norm == 'single':
            assigned = [engine_profiles[0]]
        else:
            assigned = [engine_profiles[rr_idx % len(engine_profiles)]]
            rr_idx += 1
        sh['assigned_engines'] = [ep['engine'] for ep in assigned]
        for ep in assigned:
            planned_assignments.append((sh, ep))

    # 贪心按波次装箱：每波次同时严守总并发 <= eff_concurrency 与每端并发 <= ep['max_workers']
    spec_streams = []
    remaining_items = list(planned_assignments)
    wave_num = 1
    stream_seq = 1
    while remaining_items:
        next_remaining = []
        wave_total = 0
        per_prefix = {}
        placed_any = False
        for sh, ep in remaining_items:
            pfx = ep['prefix']
            if wave_total < eff_concurrency and per_prefix.get(pfx, 0) < ep['max_workers']:
                wave_total += 1
                per_prefix[pfx] = per_prefix.get(pfx, 0) + 1
                placed_any = True
                stream_files = list(dict.fromkeys(
                    (list(norm_shared) if shared_mode == 'inlined_per_shard' else []) + list(sh['files'])
                ))
                range_note = ''
                if sh['line_ranges']:
                    parts = [
                        f"{lr['file']}:L{lr['line_start']}-L{lr['line_end']}"
                        + (f"({lr['heading']})" if lr.get('heading') else '')
                        for lr in sh['line_ranges']
                    ]
                    range_note = '；超大文件必读行段：' + '、'.join(parts)
                anchor_note = ''
                if shared_mode == 'anchor_reference_only' and norm_shared:
                    anchor_note = '；共享核心锚点（按需定向查行号）：' + ', '.join(norm_shared)
                subtask_text = (
                    f"分片 P{sh['shard_id']} 只读核对（共 {len(sh['files'])} 份目标文件）"
                    + (f"；关注焦点：{focus}" if focus else '')
                    + range_note
                    + anchor_note
                    + '。严禁改文件，每条结论必须附带精确文件路径与行号。'
                )
                stream_entry = {
                    'stream_id': f'R{wave_num}_S{stream_seq}',
                    'wave': wave_num,
                    'channel': 'cae_harness_headless',
                    'kind': 'review',
                    'engine': ep['engine'],
                    'target': str(root_path),
                    'files': stream_files,
                    'subtask': subtask_text,
                    '_read_plan_shard_id': sh['shard_id'],
                    '_read_plan_line_ranges': sh['line_ranges'],
                }
                if clean_room and ep['clean_room_supported']:
                    stream_entry['isolation'] = 'clean_room'
                spec_streams.append(stream_entry)
                stream_seq += 1
            else:
                next_remaining.append((sh, ep))
        if not placed_any:
            # 极端保底：强行放置一条防死循环
            sh, ep = next_remaining.pop(0)
            spec_streams.append({
                'stream_id': f'R{wave_num}_S{stream_seq}',
                'wave': wave_num,
                'channel': 'cae_harness_headless',
                'kind': 'review',
                'engine': ep['engine'],
                'target': str(root_path),
                'files': list(sh['files']),
                'subtask': f"分片 P{sh['shard_id']} 只读核对。",
                '_read_plan_shard_id': sh['shard_id'],
                '_read_plan_line_ranges': sh['line_ranges'],
            })
            stream_seq += 1
        remaining_items = next_remaining
        wave_num += 1

    total_waves = max((s['wave'] for s in spec_streams), default=0)
    total_raw_bytes = sum(r['bytes'] for r in all_records)
    total_inlined_bytes = sum(r['inlined_bytes'] for r in all_records)
    total_tokens = sum(r['tokens'] for r in all_records)

    # 配套显式义务列表（每个计划文件一条 read 义务 + 存在跨端或跨片边时补一条 final_audit 义务）
    planned_obligations = [
        {'id': f'READ-{idx}', 'kind': 'read', 'target': r['rel_path']}
        for idx, r in enumerate(all_records, 1)
    ]
    if len(engine_profiles) >= 2 or cross_shard_edges:
        planned_obligations.append({'id': 'AUDIT-CROSS', 'kind': 'final_audit', 'target': ''})

    single_session_safe = bool(
        len(shards) <= 1
        and not oversized_splits
        and total_tokens <= conservative_target
        and total_inlined_bytes <= min_inline_cap
        and not capacity_alerts
    )
    recommended_layers = 1 if single_session_safe else (3 if (cross_shard_edges or len(engine_profiles) >= 2) else 2)
    summary_line = (
        f"共 {len(all_records)} 文件（{total_raw_bytes / 1024:.1f} KiB / 约 {total_tokens} tokens）；"
        f"按最严内联 {min_inline_cap // 1024} KiB 与保守单片 {conservative_target} tokens 拆为 {len(shards)} 片、"
        f"{total_waves} 波次（总并发 ≤ {eff_concurrency}，模式 {mode_norm}，{len(spec_streams)} 流）"
        + (f"；超大单文件行段切分 {len(oversized_splits)} 份" if oversized_splits else '')
        + (f"；跨片显式依赖 {len(cross_shard_edges)} 条" if cross_shard_edges else '')
    )

    return {
        'schema_version': 1,
        'plan_id': plan_id,
        'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'target_root': str(root_path),
        'cross_client_mode': mode_norm,
        'focus': focus,
        'recommended_layers': recommended_layers,
        'single_session_safe': single_session_safe,
        'summary_line': summary_line,
        'budget': {
            'min_input_budget_tokens': min_input_budget,
            'reserve_tokens': reserve_tokens,
            'context_used_tokens': used_tokens,
            'remaining_tokens': remaining_tokens,
            'conservative_shard_tokens': conservative_target,
            'conservative_ratio': conservative_ratio,
            'min_inline_cap_bytes': min_inline_cap,
            'effective_concurrency': eff_concurrency,
            'shared_mode': shared_mode,
            'shared_file_count': len(shared_records),
            'shared_inlined_bytes': shared_inlined_bytes,
            'shared_tokens': shared_tokens,
        },
        'engines': engine_profiles,
        'counts': {
            'total_files': len(all_records),
            'shared_files': len(shared_records),
            'target_files': len(target_records),
            'total_raw_bytes': total_raw_bytes,
            'total_inlined_bytes': total_inlined_bytes,
            'total_estimated_tokens': total_tokens,
            'shard_count': len(shards),
            'core_shard_count': sum(1 for sh in shards if sh['is_core_shard']),
            'oversized_split_count': len(oversized_splits),
            'cross_shard_edge_count': len(cross_shard_edges),
            'stream_count': len(spec_streams),
            'wave_count': total_waves,
            'skipped_count': len(skipped),
            'excluded_archive_dir_count': len(excluded_archives),
            'scan_truncated': scan_truncated,
        },
        'shared_files': [r['rel_path'] for r in shared_records],
        'planned_files': [r['rel_path'] for r in all_records],
        'oversized_splits': oversized_splits,
        'shards': [
            {k: v for k, v in sh.items() if k != 'units'}
            for sh in shards
        ],
        'cross_shard_edges': cross_shard_edges,
        'spec_streams': spec_streams,
        'obligations': planned_obligations,
        'excluded_archive_dirs': excluded_archives,
        'skipped_files': skipped[:100],
        'capacity_alerts': capacity_alerts,
        'honesty_boundary': (
            '本阅读计划按每 3 字节约 1 token 与真实 UTF-8 行号内联字节双口径离线估算，'
            '不代表底层分词器精确计数；被默认排除的归档/历史目录已单列，如需排查历史否决依据请显式传 --include-dirs。'
        ),
    }


def render_read_plan_markdown(plan_report):
    """将读前预算与保守层级分片计划渲染为结构化 Markdown 清单。"""
    b = plan_report['budget']
    c = plan_report['counts']
    lines = [
        f"# 读前尺寸评估与保守层级分片计划（`{plan_report['plan_id'][:12]}`）",
        '',
        f"- **生成时间**：`{plan_report['generated_at']}`",
        f"- **目标根目录**：`{plan_report['target_root']}`",
        f"- **跨端阅读模式**：`{plan_report['cross_client_mode']}`（推荐层级：`{plan_report['recommended_layers']}` 层；单会话直读安全：`{'是' if plan_report['single_session_safe'] else '否'}`）",
        f"- **一句话摘要**：{plan_report['summary_line']}",
        f"- **估算边界声明**：{plan_report['honesty_boundary']}",
        '',
        '---',
        '',
        '## 一、双口径容量预算与参与执行端',
        '',
        '| 引擎规格 | 模型 | 状态 | 声明窗口 (tokens) | 安全预算 (tokens) | 内联上限 (KiB) | 单端并发上限 |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: |',
    ]
    for ep in plan_report.get('engines') or []:
        lines.append(
            f"| `{ep['engine']}` | `{ep['model']}` | `{'ready' if ep['ready'] else 'unready'}` | "
            f"{ep['context_window_tokens'] or '未知'} | {ep['input_budget_tokens']} | "
            f"{ep['inline_cap_bytes'] // 1024} | {ep['max_workers']} |"
        )
    lines.extend([
        '',
        f"- **生效双口径上限**：单片保守 Token ≤ `{b['conservative_shard_tokens']}`（占比 `{b['conservative_ratio']:.0%}`，已扣减预留 `{b['reserve_tokens']}` 与当前占用 `{b['context_used_tokens']}`）；最严内联载荷 ≤ `{b['min_inline_cap_bytes'] // 1024} KiB`；总并发上限 ≤ `{b['effective_concurrency']}`。",
        f"- **共享核心材料（L0）**：共 `{b['shared_file_count']}` 份（模式 `{b['shared_mode']}`，内联 `{b['shared_inlined_bytes'] / 1024:.1f} KiB` / 约 `{b['shared_tokens']}` tokens）"
        + (f"：`{'`, `'.join(plan_report['shared_files'])}`" if plan_report.get('shared_files') else '。'),
    ])

    alerts = plan_report.get('capacity_alerts') or []
    if alerts:
        lines.extend(['', '### 容量预警', ''])
        for al in alerts:
            icon = '🔴' if al.get('severity') == 'red' else '🟡'
            lines.append(f"- {icon} **`{al.get('kind')}`**：{al.get('detail')}")

    archives = plan_report.get('excluded_archive_dirs') or []
    if archives:
        desc = '、'.join(f"`{a['dir']}/`（约 {a['file_count']} 份）" for a in archives[:12])
        lines.extend([
            '',
            f"> **历史与归档目录分母披露**：默认跳过 {len(archives)} 个归档/历史目录（{desc}）。"
            '若需核查历史否决依据，请显式传入 `--include-dirs` 或将裁定文档加入 `--shared-files`。',
        ])

    lines.extend([
        '',
        '---',
        '',
        '## 二、分层分片清单（L1）',
        '',
        '| 分片 | 核心片 | 文件数 | 原始体积 (KiB) | 内联载荷 (KiB) | 估算 Tokens | 分配执行端 | 包含文件 / 行段 |',
        '| --- | --- | ---: | ---: | ---: | ---: | --- | --- |',
    ])
    for sh in plan_report.get('shards') or []:
        range_map = {}
        for lr in sh.get('line_ranges') or []:
            range_map.setdefault(lr['file'], []).append(f"L{lr['line_start']}-L{lr['line_end']}")
        file_desc_parts = []
        for f in sh['files'][:8]:
            if f in range_map:
                file_desc_parts.append(f"`{f}`({', '.join(range_map[f])})")
            else:
                file_desc_parts.append(f"`{f}`")
        if len(sh['files']) > 8:
            file_desc_parts.append(f"…共 {len(sh['files'])} 份")
        lines.append(
            f"| `P{sh['shard_id']}` | {'是' if sh['is_core_shard'] else '否'} | {len(sh['files'])} | "
            f"{sh['raw_bytes'] / 1024:.1f} | {sh['inlined_bytes'] / 1024:.1f} | {sh['estimated_tokens']} | "
            f"`{', '.join(sh.get('assigned_engines') or [])}` | {'；'.join(file_desc_parts)} |"
        )

    if plan_report.get('oversized_splits'):
        lines.extend(['', '### 超大单文件连续行段切分明细', '', '| 文件 | 总行数 | 内联字节 (KiB) | 切分段数 |', '| --- | ---: | ---: | ---: |'])
        for sp in plan_report['oversized_splits']:
            lines.append(f"| `{sp['file']}` | {sp['total_lines']} | {sp['inlined_bytes'] / 1024:.1f} | {sp['range_count']} |")

    cross_edges = plan_report.get('cross_shard_edges') or []
    lines.extend(['', '---', '', '## 三、跨片显式依赖边（供 L2 / `cross-check` 重点回源核对）', ''])
    if not cross_edges:
        lines.append('（无跨分片显式静态导入或 Markdown 链接边。）')
    else:
        lines.append('| 源分片 → 目标分片 | 关系类型 | 源文件 | 目标文件 |')
        lines.append('| --- | --- | --- | --- |')
        for e in cross_edges[:80]:
            lines.append(f"| `P{e['from_shard']}` → `P{e['to_shard']}` | `{e['relation']}` | `{e['source']}` | `{e['target']}` |")

    lines.extend(['', '---', '', '## 四、保守并发波次流排程（可直接送入 `plan --spec-file` 审批）', ''])
    streams = plan_report.get('spec_streams') or []
    if not streams:
        lines.append('（目标目录为空，无待执行流。）')
    else:
        lines.append('| 波次 | 流号 | 执行端 | 隔离 | 目标文件数 | 任务范围 |')
        lines.append('| ---: | --- | --- | --- | ---: | --- |')
        for st in streams[:120]:
            lines.append(
                f"| Wave {st['wave']} | `{st['stream_id']}` | `{st['engine']}` | `{st.get('isolation') or 'default'}` | "
                f"{len(st.get('files') or [])} | {st.get('subtask', '')[:120]} |"
            )
    return '\n'.join(lines).rstrip() + '\n'


def cmd_read_plan(args):
    """纯离线读前尺寸评估与保守层级分片规划入口：输出 3 句话简报并落盘完整清单与可选 spec.json。"""
    target, _root = _target(args)
    caller = resolve_caller(args.caller)
    backends = _args_backends(args, refresh_model_cache=False)
    plan_id = uuid.uuid4().hex
    report = build_read_plan(
        target,
        plan_id=plan_id,
        files=getattr(args, 'files', None) or None,
        shared_files=getattr(args, 'shared_files', None) or None,
        core_files=getattr(args, 'core_files', None) or None,
        engines=getattr(args, 'engines', None) or None,
        backends=backends,
        caller=caller,
        shard_tokens=getattr(args, 'shard_tokens', None),
        max_concurrency=getattr(args, 'max_concurrency', None),
        context_used_tokens=getattr(args, 'context_used_tokens', 0) or 0,
        cross_client_mode=getattr(args, 'cross_client_mode', 'risk-tiered') or 'risk-tiered',
        include_dirs=getattr(args, 'include_dirs', None) or None,
        focus=getattr(args, 'focus', '') or '',
        clean_room=bool(getattr(args, 'clean_room', False)),
    )
    plan_dir = resolve_dispatch_plan_dir(getattr(args, 'plan_dir', None))
    rp_dir = plan_dir / 'read_plans'
    if os.path.lexists(rp_dir) and _is_link(rp_dir):
        raise HarnessError(f'拒绝通过符号链接目录写入阅读计划: {rp_dir}')
    md_path = Path(args.output_md).expanduser() if getattr(args, 'output_md', '') else (rp_dir / f'{plan_id}_read_plan.md')
    json_path = rp_dir / f'{plan_id}_read_plan.json'
    redact = bool(getattr(args, 'redact', False))
    for anc in (md_path.parent, md_path, json_path.parent, json_path):
        if os.path.lexists(anc) and _is_link(anc):
            raise HarnessError(f'拒绝通过符号链接写入阅读计划文件: {anc}')
    md_text = render_read_plan_markdown(report)
    if redact:
        md_text = _redact_public(md_text)
    _atomic_text(md_path, md_text)
    _atomic_json(json_path, _redact_public(report) if redact else report)
    saved_md = str(md_path.resolve())
    saved_json = str(json_path.resolve())

    emitted_spec = ''
    if getattr(args, 'emit_spec', ''):
        spec_out = Path(args.emit_spec).expanduser()
        for anc in (spec_out.parent, spec_out):
            if os.path.lexists(anc) and _is_link(anc):
                raise HarnessError(f'拒绝通过符号链接写入工作流配置: {spec_out}')
        clean_streams = [
            {k: v for k, v in st.items() if not k.startswith('_')}
            for st in report['spec_streams']
        ]
        _atomic_json(spec_out, _redact_public(clean_streams) if redact else clean_streams)
        emitted_spec = str(spec_out.resolve())

    sid = _session_id(args)
    if sid:
        ptr_path = rp_dir / f'session_{_safe_state_id(sid)}_latest.json'
        if not (os.path.lexists(ptr_path) and _is_link(ptr_path)):
            _atomic_json(ptr_path, {
                'plan_id': plan_id,
                'session_id': sid,
                'report_file': saved_md,
                'plan_json_file': saved_json,
                'summary_line': report['summary_line'],
                'shard_count': report['counts']['shard_count'],
                'wave_count': report['counts']['wave_count'],
                'created_at': report['generated_at'],
            })

    md_uri = Path(saved_md).as_uri()
    chat_brief = (
        f"**【读前尺寸评估 · {report['recommended_layers']} 层保守分片】** {report['summary_line']}。\n"
        f"- 完整分片、行段切分与波次清单：[{Path(saved_md).name}]({md_uri})\n"
        f"- 计划 JSON：`{Path(saved_json).name}`"
        + (f"；已导出可执行工作流配置：`{Path(emitted_spec).name}`" if emitted_spec else '')
        + '。'
    )
    out = {
        'status': 'ok',
        'plan_id': plan_id,
        'report_file': saved_md,
        'plan_json_file': saved_json,
        'emitted_spec_file': emitted_spec or None,
        'chat_brief': chat_brief,
        'summary_line': report['summary_line'],
        'recommended_layers': report['recommended_layers'],
        'single_session_safe': report['single_session_safe'],
        'budget': report['budget'],
        'counts': report['counts'],
        'capacity_alerts': report['capacity_alerts'],
        'excluded_archive_dirs': report['excluded_archive_dirs'],
        'honesty_boundary': report['honesty_boundary'],
    }
    return _inspection_print(args, out)


# ════════════════════════════════════════════════════════════════════════════
# ⑦D 阶段 3：跨端分片盲读覆盖矩阵与确定性回源汇总核对（cross-check）
# ════════════════════════════════════════════════════════════════════════════

_CROSS_CHECK_OK_POLARITY_RE = re.compile(
    r'无需修改|无问题|已满足|一致通过|未发现缺陷|\b(?:no\s+issue|no\s+defect|consistent|looks\s+good)\b',
    re.I,
)
_CROSS_CHECK_BAD_POLARITY_RE = re.compile(
    r'错误|缺失|遗漏|越界|不守恒|违规|失效|崩溃|死锁|相反|冲突|阻断|漏改|'
    r'\b(?:bug|error|missing|violation|incorrect|contradict|fail(?:ed|ure)?|mismatch|deadlock)\b',
    re.I,
)


def _extract_cross_check_streams(receipt_obj, root_path):
    """将 batch / audit / task 回执统一标准化为跨端核对流列表，保留原始终态、交付凭据与发现项。"""
    if not isinstance(receipt_obj, dict):
        return []
    out = []
    # audit 顶层按引擎提取原始发现（避免仅按行合并丢掉各端原话）
    audit_findings_by_engine = {}
    if 'completed_units' in receipt_obj or 'consensus_findings' in receipt_obj:
        for cf in receipt_obj.get('consensus_findings') or []:
            if not isinstance(cf, dict):
                continue
            rby = cf.get('reported_by') or []
            if isinstance(cf.get('left_finding'), dict) and len(rby) >= 1:
                audit_findings_by_engine.setdefault(str(rby[0]), []).append(cf['left_finding'])
            if isinstance(cf.get('right_finding'), dict) and len(rby) >= 2:
                audit_findings_by_engine.setdefault(str(rby[1]), []).append(cf['right_finding'])
        for sf in receipt_obj.get('single_source_findings') or []:
            if not isinstance(sf, dict):
                continue
            rby = sf.get('reported_by') or []
            eng = str(rby[0]) if rby else str(receipt_obj.get('left_engine') or '')
            if eng:
                audit_findings_by_engine.setdefault(eng, []).append(sf)

    raw_streams = _result_streams(receipt_obj)
    attached_audit_engines = set()
    for idx, st in enumerate(raw_streams, 1):
        if not isinstance(st, dict):
            continue
        inv = st.get('invocation') if isinstance(st.get('invocation'), dict) else {}
        eng = str(st.get('engine') or receipt_obj.get('engine') or '').strip()
        if not eng:
            eng = f'unknown:{idx}'
        prefix, _ = _engine_parts(eng) if ':' in eng else (eng.lower(), '')
        sid = str(
            st.get('stream_id')
            or (f"{st['side']}-S{st['shard_id']}" if st.get('side') and st.get('shard_id') is not None
                else f"S{st['shard_id']}" if st.get('shard_id') is not None else f'S{idx}')
        )
        st_status = str(st.get('status') or ('ok' if st.get('succeeded', receipt_obj.get('status') == 'ok') else 'failed'))
        succeeded = bool(st.get('succeeded', st_status == 'ok'))
        planned = _normalize_reconcile_paths(
            st.get('planned_files') or st.get('files') or inv.get('files') or [], root_path,
        )
        ca = st.get('citation_audit') or inv.get('citation_audit') or {}
        inlined = _normalize_reconcile_paths(ca.get('inlined_files') or [], root_path)
        actual_read = _normalize_reconcile_paths(ca.get('actual_files_read') or [], root_path)
        delivered = sorted(set(inlined) | set(actual_read))
        self_read = _normalize_reconcile_paths(st.get('read_files') or [], root_path)
        missing_cited = _normalize_reconcile_paths(ca.get('missing_files') or [], root_path)
        unopened_cited = _normalize_reconcile_paths(ca.get('unopened_cited_files') or [], root_path)
        out_of_range = list(ca.get('out_of_range_lines') or [])

        raw_findings = []
        res_obj = st.get('result')
        if isinstance(res_obj, dict) and isinstance(res_obj.get('findings'), list):
            raw_findings.extend(res_obj['findings'])
        if isinstance(st.get('findings'), list):
            raw_findings.extend(st['findings'])
        if eng in audit_findings_by_engine and eng not in attached_audit_engines:
            attached_audit_engines.add(eng)
            raw_findings.extend(audit_findings_by_engine[eng])

        norm_findings = []
        for fitem in raw_findings:
            if not isinstance(fitem, dict):
                continue
            fpath = str(fitem.get('file') or '').strip()
            if not fpath:
                continue
            try:
                frel = _normalize_rel_file(fpath, root_path)
            except HarnessError:
                frel = fpath.replace('\\', '/').lstrip('./')
            ls = fitem.get('line_start')
            le = fitem.get('line_end')
            line_start = int(ls) if isinstance(ls, int) and not isinstance(ls, bool) and ls > 0 else 1
            line_end = int(le) if isinstance(le, int) and not isinstance(le, bool) and le >= line_start else line_start
            sev = str(fitem.get('severity') or 'medium').strip().lower()
            if sev not in ('critical', 'high', 'medium', 'low'):
                sev = 'medium'
            norm_findings.append({
                'file': frel,
                'line_start': line_start,
                'line_end': line_end,
                'severity': sev,
                'description': str(fitem.get('description') or fitem.get('summary') or '').strip(),
                'evidence': str(fitem.get('evidence') or '').strip(),
                'engine': eng,
                'client_prefix': prefix,
                'stream_id': sid,
                'stream_status': st_status,
            })

        out.append({
            'stream_id': sid,
            'engine': eng,
            'client_prefix': prefix,
            'model': str(st.get('model') or receipt_obj.get('model') or ''),
            'status': st_status,
            'succeeded': succeeded,
            'planned_files': planned,
            'delivered_files': delivered,
            'self_reported_read_files': self_read,
            'citation_gap_count': len(missing_cited) + len(unopened_cited) + len(out_of_range),
            'findings': norm_findings,
        })
    return out


def build_cross_check_report(
    target_root,
    *,
    run_id='',
    receipts=None,
    read_plan=None,
    obligations=None,
    line_tolerance=None,
):
    """纯确定性跨端汇总核对：覆盖矩阵、共同盲区、多端同位并列/分歧、跨片接口核对与 R6 回源清单。"""
    root_path = Path(target_root).expanduser().resolve() if target_root else Path.cwd().resolve()
    if root_path.is_file():
        root_path = root_path.parent
    run_id = run_id or uuid.uuid4().hex
    if line_tolerance is None:
        line_tolerance = _positive_int(_headless_tunables().get('consensus_line_tolerance')) or 8
    ranks = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}

    all_streams = []
    for rec in (receipts or []):
        all_streams.extend(_extract_cross_check_streams(rec, root_path))

    # 1. 构建分母（计划文件 × 期望执行端）
    expected_engines = []
    engine_prefix_map = {}
    planned_file_engines = {}  # file -> set(expected_engines) from read_plan
    denominator_files = []

    def _add_eng(eng_str):
        if eng_str and eng_str not in expected_engines:
            expected_engines.append(eng_str)
            engine_prefix_map[eng_str] = _engine_parts(eng_str)[0] if ':' in eng_str else eng_str.lower()

    def _add_file(rel):
        if rel and rel not in denominator_files:
            denominator_files.append(rel)

    if isinstance(read_plan, dict):
        for ep in read_plan.get('engines') or []:
            if isinstance(ep, dict) and ep.get('engine'):
                _add_eng(str(ep['engine']))
        for rel in _normalize_reconcile_paths(read_plan.get('planned_files') or [], root_path):
            _add_file(rel)
        for sh in read_plan.get('shards') or []:
            if not isinstance(sh, dict):
                continue
            sh_engs = [str(e) for e in (sh.get('assigned_engines') or []) if e]
            for e in sh_engs:
                _add_eng(e)
            for rel in _normalize_reconcile_paths(sh.get('files') or [], root_path):
                _add_file(rel)
                planned_file_engines.setdefault(rel, set()).update(sh_engs)
        shared_rels = _normalize_reconcile_paths(read_plan.get('shared_files') or [], root_path)
        if shared_rels and (read_plan.get('budget') or {}).get('shared_mode') == 'inlined_per_shard':
            for rel in shared_rels:
                _add_file(rel)
                planned_file_engines.setdefault(rel, set()).update(expected_engines)

    for st in all_streams:
        _add_eng(st['engine'])
        for rel in st['planned_files']:
            _add_file(rel)
        for rel in st['delivered_files']:
            _add_file(rel)
        for fitem in st['findings']:
            _add_file(fitem['file'])

    norm_obs = _normalize_obligations(obligations)
    if isinstance(read_plan, dict) and isinstance(read_plan.get('obligations'), list):
        seen_ob_ids = {item['id'] for item in norm_obs}
        for item in _normalize_obligations(read_plan.get('obligations')):
            if item['id'] not in seen_ob_ids:
                norm_obs.append(item)
                seen_ob_ids.add(item['id'])
    for ob in norm_obs:
        if ob['kind'] in ('read', 'review') and ob['target']:
            _add_file(ob['target'])

    denominator_files = sorted(denominator_files)

    # 2. 按 (file, engine) 逐格计算覆盖矩阵
    coverage_matrix = {}  # file -> {engine: status}
    multi_client_delivered = []
    multi_engine_delivered = []
    single_engine_only_delivered = []
    shared_blind_spots = []
    failed_or_timeout_cells = []
    missing_envelope_cells = []
    reported_only_cells = []

    for rel in denominator_files:
        row_map = {}
        delivered_engs = []
        delivered_clients = set()
        for eng in expected_engines:
            eng_streams = [st for st in all_streams if st['engine'] == eng]
            assigned_streams = [st for st in eng_streams if rel in st['planned_files'] or rel in st['delivered_files'] or rel in st['self_reported_read_files']]
            ok_delivered = any(st['succeeded'] and rel in st['delivered_files'] for st in eng_streams)
            ok_reported = any(st['succeeded'] and rel in st['self_reported_read_files'] for st in eng_streams)
            any_failed = any((not st['succeeded']) and (rel in st['planned_files']) for st in eng_streams)
            if ok_delivered:
                cell = 'delivered'
                delivered_engs.append(eng)
                delivered_clients.add(engine_prefix_map.get(eng, eng))
            elif any_failed:
                cell = 'failed_or_timeout'
                failed_or_timeout_cells.append({'file': rel, 'engine': eng})
            elif ok_reported:
                cell = 'reported_only'
                reported_only_cells.append({'file': rel, 'engine': eng})
            elif assigned_streams:
                cell = 'unverified_delivery'
            elif eng in planned_file_engines.get(rel, ()):
                cell = 'missing_from_envelope'
                missing_envelope_cells.append({'file': rel, 'engine': eng})
            else:
                cell = 'not_assigned'
            row_map[eng] = cell
        coverage_matrix[rel] = row_map
        if len(delivered_clients) >= 2:
            multi_client_delivered.append(rel)
        if len(delivered_engs) >= 2:
            multi_engine_delivered.append(rel)
        elif len(delivered_engs) == 1:
            single_engine_only_delivered.append(rel)
        else:
            shared_blind_spots.append(rel)

    # 3. 发现项跨端对齐与分歧保全（绝不以同位置覆盖相反观点，绝不免除 R6 回源核对）
    all_findings = [fitem for st in all_streams for fitem in st['findings']]
    clusters = []
    for fitem in sorted(all_findings, key=lambda x: (x['file'], x['line_start'], -ranks[x['severity']], x['engine'])):
        placed = False
        for cl in clusters:
            if (
                cl['file'] == fitem['file']
                and fitem['line_start'] - line_tolerance <= cl['line_end']
                and cl['line_start'] - line_tolerance <= fitem['line_end']
            ):
                cl['items'].append(fitem)
                cl['line_start'] = min(cl['line_start'], fitem['line_start'])
                cl['line_end'] = max(cl['line_end'], fitem['line_end'])
                placed = True
                break
        if not placed:
            clusters.append({
                'file': fitem['file'],
                'line_start': fitem['line_start'],
                'line_end': fitem['line_end'],
                'items': [fitem],
            })

    co_located_multi_engine = []
    single_engine_findings = []
    divergent_findings = []
    for cl in clusters:
        engines_in_cl = list(dict.fromkeys(it['engine'] for it in cl['items']))
        clients_in_cl = list(dict.fromkeys(it['client_prefix'] for it in cl['items']))
        max_sev = max((it['severity'] for it in cl['items']), key=lambda s: ranks[s])
        min_sev = min((it['severity'] for it in cl['items']), key=lambda s: ranks[s])
        if len(engines_in_cl) >= 2:
            sev_div = (ranks[max_sev] - ranks[min_sev]) >= 1
            descs = [it['description'] for it in cl['items']]
            has_ok = any(_CROSS_CHECK_OK_POLARITY_RE.search(d) for d in descs)
            has_bad = any(_CROSS_CHECK_BAD_POLARITY_RE.search(d) for d in descs)
            pol_div = bool(has_ok and has_bad)
            entry = {
                'file': cl['file'],
                'line_start': cl['line_start'],
                'line_end': cl['line_end'],
                'max_severity': max_sev,
                'engines': engines_in_cl,
                'cross_client': len(clients_in_cl) >= 2,
                'severity_divergent': sev_div,
                'polarity_divergence_hint': pol_div,
                'match_basis': 'location_overlap_only',
                'r6_host_verification_required': True,
                'items': cl['items'],
            }
            co_located_multi_engine.append(entry)
            if sev_div or pol_div:
                divergent_findings.append(entry)
        else:
            only_eng = engines_in_cl[0]
            other_status = {
                eng: ('delivered_no_finding' if st_val == 'delivered' else st_val)
                for eng, st_val in (coverage_matrix.get(cl['file']) or {}).items()
                if eng != only_eng
            }
            single_engine_findings.append({
                'file': cl['file'],
                'line_start': cl['line_start'],
                'line_end': cl['line_end'],
                'max_severity': max_sev,
                'engine': only_eng,
                'client_prefix': clients_in_cl[0],
                'other_engines_coverage': other_status,
                'other_engine_failed_or_missing': any(
                    v in ('failed_or_timeout', 'missing_from_envelope', 'unverified_delivery')
                    for v in other_status.values()
                ),
                'r6_host_verification_required': True,
                'items': cl['items'],
            })

    co_located_multi_engine.sort(key=lambda x: (-ranks[x['max_severity']], not (x['severity_divergent'] or x['polarity_divergence_hint']), x['file'], x['line_start']))
    single_engine_findings.sort(key=lambda x: (-ranks[x['max_severity']], x['file'], x['line_start']))

    # 4. 跨片接口核对清单（检测是否存在无公共复核端的跨片显式依赖边）
    cross_shard_verification = []
    if isinstance(read_plan, dict):
        for edge in read_plan.get('cross_shard_edges') or []:
            src = edge.get('source', '')
            tgt = edge.get('target', '')
            src_map = coverage_matrix.get(src) or {}
            tgt_map = coverage_matrix.get(tgt) or {}
            joint = [e for e in expected_engines if src_map.get(e) == 'delivered' and tgt_map.get(e) == 'delivered']
            src_deliv = [e for e in expected_engines if src_map.get(e) == 'delivered']
            tgt_deliv = [e for e in expected_engines if tgt_map.get(e) == 'delivered']
            needs_w2 = not joint
            cross_shard_verification.append({
                'from_shard': edge.get('from_shard'),
                'to_shard': edge.get('to_shard'),
                'source': src,
                'target': tgt,
                'relation': edge.get('relation', ''),
                'source_delivered_by': src_deliv,
                'target_delivered_by': tgt_deliv,
                'joint_delivered_by': joint,
                'needs_wave2_cross_check': needs_w2,
            })

    # 5. R6 主会话回源原文件精确行号核对清单与顶层告警
    r6_source_check_list = []
    for item in co_located_multi_engine:
        r6_source_check_list.append({
            'file': item['file'],
            'line_start': item['line_start'],
            'line_end': item['line_end'],
            'severity': item['max_severity'],
            'kind': 'divergent_multi_engine' if (item['severity_divergent'] or item['polarity_divergence_hint']) else 'co_located_multi_engine',
            'engines': item['engines'],
        })
    for item in single_engine_findings:
        r6_source_check_list.append({
            'file': item['file'],
            'line_start': item['line_start'],
            'line_end': item['line_end'],
            'severity': item['max_severity'],
            'kind': 'single_engine_with_peer_gap' if item['other_engine_failed_or_missing'] else 'single_engine_finding',
            'engines': [item['engine']],
        })
    r6_source_check_list.sort(key=lambda x: (-ranks[x['severity']], x['file'], x['line_start']))

    alerts = []
    if shared_blind_spots:
        alerts.append({
            'severity': 'red',
            'kind': 'shared_blind_spot',
            'target': ', '.join(shared_blind_spots[:5]),
            'detail': f'{len(shared_blind_spots)} 份分母文件在全部参与端均无可证实交付凭据（可能存在共同信封缺口或全端失败）',
        })
    if failed_or_timeout_cells:
        alerts.append({
            'severity': 'red',
            'kind': 'cross_client_stream_failure',
            'target': ', '.join(sorted({f"{c['engine']}:{c['file']}" for c in failed_or_timeout_cells})[:5]),
            'detail': f'{len(failed_or_timeout_cells)} 个（文件 × 引擎）格子因流失败或超时缺证，仍保留在验收分母中',
        })
    if missing_envelope_cells:
        alerts.append({
            'severity': 'red',
            'kind': 'missing_from_envelope',
            'target': ', '.join(sorted({f"{c['engine']}:{c['file']}" for c in missing_envelope_cells})[:5]),
            'detail': f'{len(missing_envelope_cells)} 个计划分配项未出现在任何派单流输入信封中',
        })
    if divergent_findings:
        alerts.append({
            'severity': 'yellow',
            'kind': 'cross_client_finding_divergence',
            'target': ', '.join(f"{d['file']}:L{d['line_start']}" for d in divergent_findings[:5]),
            'detail': f'{len(divergent_findings)} 处多端同位置发现存在严重度或极性分歧，须主会话按 R6 亲自回源核对',
        })
    cross_gaps = [c for c in cross_shard_verification if c['needs_wave2_cross_check']]
    if cross_gaps:
        alerts.append({
            'severity': 'yellow',
            'kind': 'cross_shard_unverified_interface',
            'target': ', '.join(f"{c['source']}->{c['target']}" for c in cross_gaps[:5]),
            'detail': f'{len(cross_gaps)} 条跨片显式依赖边缺乏同时阅读两端的公共执行端，建议安排 L2 联合回源复核',
        })

    summary_line = (
        f"分母 {len(denominator_files)} 文件 × {len(expected_engines)} 引擎（多端实证 {len(multi_engine_delivered)}、"
        f"跨客户端实证 {len(multi_client_delivered)}、共同盲区 {len(shared_blind_spots)}、失败/超时格 {len(failed_or_timeout_cells)}）；"
        f"多端同位发现 {len(co_located_multi_engine)} 处（含分歧 {len(divergent_findings)} 处）、单端发现 {len(single_engine_findings)} 处"
        + (f"；跨片缺联审边 {len(cross_gaps)} 条" if cross_gaps else '')
    )

    return {
        'schema_version': 1,
        'run_id': run_id,
        'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'target_root': str(root_path),
        'summary_line': summary_line,
        'engines': expected_engines,
        'counts': {
            'denominator_files': len(denominator_files),
            'engines_count': len(expected_engines),
            'streams_count': len(all_streams),
            'failed_streams_count': sum(1 for st in all_streams if not st['succeeded']),
            'multi_client_delivered': len(multi_client_delivered),
            'multi_engine_delivered': len(multi_engine_delivered),
            'single_engine_only_delivered': len(single_engine_only_delivered),
            'shared_blind_spots': len(shared_blind_spots),
            'failed_or_timeout_cells': len(failed_or_timeout_cells),
            'missing_envelope_cells': len(missing_envelope_cells),
            'reported_only_cells': len(reported_only_cells),
            'co_located_multi_engine': len(co_located_multi_engine),
            'divergent_findings': len(divergent_findings),
            'single_engine_findings': len(single_engine_findings),
            'cross_shard_edges_total': len(cross_shard_verification),
            'cross_shard_unverified': len(cross_gaps),
            'r6_items_total': len(r6_source_check_list),
        },
        'alerts': alerts,
        'streams': [{k: v for k, v in st.items() if k != 'findings'} for st in all_streams],
        'coverage_matrix': coverage_matrix,
        'shared_blind_spots': shared_blind_spots,
        'failed_or_timeout_cells': failed_or_timeout_cells,
        'missing_envelope_cells': missing_envelope_cells,
        'co_located_multi_engine': co_located_multi_engine,
        'divergent_findings': divergent_findings,
        'single_engine_findings': single_engine_findings,
        'cross_shard_verification': cross_shard_verification,
        'r6_source_check_list': r6_source_check_list,
        'honesty_boundary': (
            '本跨端汇总核对表由纯确定性代码基于回执终态、citation_audit 可证实交付记录与原始发现并列生成；'
            '多端同位置检出绝不等同于共识已证实，且超时/失败端仍计入验收分母；所有拟采纳发现须由主会话按 R6 亲自打开原文件核对行号与上下文。'
        ),
    }


def render_cross_check_markdown(report):
    """将跨端覆盖矩阵、共同盲区、多端并列/分歧发现与 R6 待回源清单渲染为 Markdown。"""
    c = report['counts']
    engines = report.get('engines') or []
    lines = [
        f"# 跨端分片读取与独立汇总核对表（`{report['run_id'][:12]}`）",
        '',
        f"- **生成时间**：`{report['generated_at']}`",
        f"- **目标根目录**：`{report['target_root']}`",
        f"- **一句话摘要**：{report['summary_line']}",
        f"- **核对边界声明**：{report['honesty_boundary']}",
        '',
        '---',
        '',
        '## 一、首屏重点告警（共同盲区、超时缺口、跨端分歧与跨片边）',
        '',
    ]
    alerts = report.get('alerts') or []
    if not alerts:
        lines.append('（无共同盲区、失败缺口或显式分歧告警；拟采纳发现仍须按 R6 回源核对原文。）')
    else:
        lines.append('| 级别 | 类型 | 涉及对象 | 确定性说明 |')
        lines.append('| --- | --- | --- | --- |')
        for al in alerts[:_RECONCILE_TOP_SCREEN_ITEMS]:
            icon = '🔴 阻断/缺口' if al.get('severity') == 'red' else '🟡 待回源核对'
            lines.append(f"| {icon} | `{al.get('kind', '')}` | `{al.get('target', '')}` | {al.get('detail', '')} |")

    lines.extend(['', '---', '', '## 二、计划分母 × 客户端覆盖矩阵', ''])
    if not report.get('coverage_matrix'):
        lines.append('（无计划或已交付文件记录。）')
    else:
        cell_labels = {
            'delivered': '已实证',
            'reported_only': '仅自报',
            'failed_or_timeout': '失败/超时',
            'missing_from_envelope': '漏入信封',
            'unverified_delivery': '未证实交付',
            'not_assigned': '未分配',
        }
        header = '| 目标文件 | ' + ' | '.join(f'`{e}`' for e in engines) + ' |'
        sep = '| --- | ' + ' | '.join('---' for _ in engines) + ' |'
        lines.extend([header, sep])
        for rel, row_map in list(report['coverage_matrix'].items())[:150]:
            vals = ' | '.join(cell_labels.get(row_map.get(e, 'not_assigned'), row_map.get(e, '')) for e in engines)
            lines.append(f'| `{rel}` | {vals} |')
        if len(report['coverage_matrix']) > 150:
            lines.append(f"\n> 分母共 {len(report['coverage_matrix'])} 份文件，表内展示前 150 项。")

    lines.extend(['', '---', '', '## 三、多端同位置发现并列核对（含分歧标出，均需 R6 回源）', ''])
    co_list = report.get('co_located_multi_engine') or []
    if not co_list:
        lines.append('（无多端同位置发现。）')
    else:
        lines.append('| 文件与行段 | 最高级别 | 跨客户端 | 分歧标记 | 各端原始意见并列 |')
        lines.append('| --- | --- | --- | --- | --- |')
        for item in co_list[:80]:
            div_tags = []
            if item['severity_divergent']:
                div_tags.append('严重度分歧')
            if item['polarity_divergence_hint']:
                div_tags.append('极性冲突线索')
            div_str = '、'.join(div_tags) if div_tags else '同位待 R6 核实'
            opinions = '；'.join(
                f"`{sub['engine']}`[{sub['severity']}]: {sub['description'][:90]}"
                for sub in item['items']
            )
            lines.append(
                f"| `{item['file']}:L{item['line_start']}-L{item['line_end']}` | `{item['max_severity']}` | "
                f"{'是' if item['cross_client'] else '否'} | {div_str} | {opinions} |"
            )

    lines.extend(['', '---', '', '## 四、单端发现与对端覆盖状态', ''])
    single_list = report.get('single_engine_findings') or []
    if not single_list:
        lines.append('（无单端独有发现。）')
    else:
        lines.append('| 文件与行段 | 级别 | 报告端 | 对端覆盖状态 | 原始描述 |')
        lines.append('| --- | --- | --- | --- | --- |')
        for item in single_list[:80]:
            peer_str = '；'.join(f"`{e}`={st}" for e, st in item['other_engines_coverage'].items()) or '无对端'
            desc = item['items'][0]['description'][:110] if item['items'] else ''
            lines.append(
                f"| `{item['file']}:L{item['line_start']}-L{item['line_end']}` | `{item['max_severity']}` | "
                f"`{item['engine']}` | {peer_str} | {desc} |"
            )

    lines.extend(['', '---', '', '## 五、跨片显式接口边联审核对', ''])
    if report.get('cross_shard_verification'):
        lines.append('| 源分片 → 目标分片 | 关系 | 源文件 | 目标文件 | 共同实证端 | 需 L2 联合回源 |')
        lines.append('| --- | --- | --- | --- | --- | --- |')
        for edge in report['cross_shard_verification'][:80]:
            joint = ', '.join(edge['joint_delivered_by']) or '无'
            lines.append(
                f"| `P{edge['from_shard']}` → `P{edge['to_shard']}` | `{edge['relation']}` | "
                f"`{edge['source']}` | `{edge['target']}` | `{joint}` | {'**是**' if edge['needs_wave2_cross_check'] else '否'} |"
            )
    else:
        lines.append('（本次核对未绑定含跨片显式依赖边的阅读计划。）')

    lines.extend(['', '---', '', '## 六、R6 主会话待回源原文件精确行号清单', ''])
    r6_list = report.get('r6_source_check_list') or []
    if not r6_list:
        lines.append('（本次无待回源发现项。）')
    else:
        for idx, item in enumerate(r6_list[:80], 1):
            lines.append(
                f"{idx}. `{item['file']}:L{item['line_start']}-L{item['line_end']}` "
                f"(`{item['severity']}` · `{item['kind']}` · 来源 `{', '.join(item['engines'])}`)"
            )
    return '\n'.join(lines).rstrip() + '\n'


def cmd_cross_check(args):
    """纯离线跨端分片读取汇总核对入口：对比多份回执与阅读计划分母并落盘 Markdown/JSON。"""
    target, root = _target(args)
    raw_receipts = getattr(args, 'receipt', None) or []
    if not raw_receipts:
        raise HarnessError('cross-check 至少需要一份 --receipt 回执 JSON 路径')
    receipts = []
    for rp in raw_receipts:
        obj = _read_json(Path(rp).expanduser())
        if not isinstance(obj, dict):
            raise HarnessError(f'无效的回执 JSON: {rp}')
        receipts.append(obj)
    read_plan = None
    if getattr(args, 'read_plan', ''):
        read_plan = _read_json(Path(args.read_plan).expanduser())
    obligations = None
    if getattr(args, 'obligations_file', ''):
        raw_ob = _read_json(Path(args.obligations_file).expanduser())
        obligations = raw_ob.get('obligations') if isinstance(raw_ob, dict) else raw_ob

    run_id = uuid.uuid4().hex
    report = build_cross_check_report(
        root,
        run_id=run_id,
        receipts=receipts,
        read_plan=read_plan,
        obligations=obligations,
    )
    plan_dir = resolve_dispatch_plan_dir(getattr(args, 'plan_dir', None))
    rec_dir = plan_dir / 'reconcile'
    if os.path.lexists(rec_dir) and _is_link(rec_dir):
        raise HarnessError(f'拒绝通过符号链接目录写入跨端核对表: {rec_dir}')
    md_path = Path(args.output_md).expanduser() if getattr(args, 'output_md', '') else (rec_dir / f'{run_id}_cross_check.md')
    json_path = rec_dir / f'{run_id}_cross_check.json'
    redact = bool(getattr(args, 'redact', False))
    for anc in (md_path.parent, md_path, json_path.parent, json_path):
        if os.path.lexists(anc) and _is_link(anc):
            raise HarnessError(f'拒绝通过符号链接写入跨端核对文件: {anc}')
    md_text = render_cross_check_markdown(report)
    if redact:
        md_text = _redact_public(md_text)
    _atomic_text(md_path, md_text)
    _atomic_json(json_path, _redact_public(report) if redact else report)
    saved_md = str(md_path.resolve())
    saved_json = str(json_path.resolve())

    md_uri = Path(saved_md).as_uri()
    chat_line = f"跨端汇总核对（{report['summary_line']}）：[{Path(saved_md).name}]({md_uri})"
    out = {
        'status': 'ok',
        'run_id': run_id,
        'report_file': saved_md,
        'cross_check_json_file': saved_json,
        'chat_line': chat_line,
        'summary_line': report['summary_line'],
        'counts': report['counts'],
        'alerts': report['alerts'][:_RECONCILE_TOP_SCREEN_ITEMS],
        'shared_blind_spots': report['shared_blind_spots'][:20],
        'r6_source_check_top': report['r6_source_check_list'][:_RECONCILE_TOP_SCREEN_ITEMS],
        'honesty_boundary': report['honesty_boundary'],
    }
    return _inspection_print(args, out)


def _inspection_print(args, payload):
    if getattr(args, 'output', ''):
        _atomic_json(args.output, payload if getattr(args, 'no_redact_output', False) else _redact_public(payload))
    return _print(_redact_public(payload) if getattr(args, 'redact', False) else payload)


def cmd_probe(args):
    refresh = bool(getattr(args, 'discover_models', False))
    backends = _args_backends(args, refresh_model_cache=refresh)
    for prefix, info in backends.items():
        info['subscription_status'] = inspect_backend_quota_and_availability(prefix, info, info.get('model'), redact_usage=True)
    return _inspection_print(args, {'status': 'ok', 'caller': resolve_caller(args.caller),
                   'workspace_mode': detect_workspace_mode(),
                   'fleet_profile': _fleet_profile_summary(),
                   'capability_ledger': _fleet_profile().get('capability_ledger', {}),
                   'subscription_status': _fleet_profile().get('subscription_matrix', {}),
                   'backends': backends})


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
        info['subscription_status'] = inspect_backend_quota_and_availability(name, info, info.get('model'), redact_usage=True)
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
            try:
                profile = resolve_execution_profile(backends, name + ':default', tier, prompt, kind=kind)
            except HarnessError as exc:
                profile = {'execution_blocked_reason': str(exc), 'status': exc.status}
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
    return _inspection_print(args, {'status': 'ok', 'caller': caller, 'workspace_mode': detect_workspace_mode(),
                   'fleet_profile': _fleet_profile_summary(),
                   'capability_ledger': _fleet_profile().get('capability_ledger', {}),
                   'subscription_status': _fleet_profile().get('subscription_matrix', {}),
                   'resolved_roles': resolved_roles,
                   'backends': backends,
                   'recommendations': recommendations,
                   'catalog_discovery_requested': args.discover_models,
                   'runtime_verified': False, 'controls_native_spawn_agent': False,
                   'notice': '能力矩阵和选路基于目录及策略。实际可用性、性能、有效上下文、压缩和委派行为仍需执行验证。'})


def cmd_audit(args):
    _begin_dispatch(args)
    target, root = _target(args)
    caller = resolve_caller(args.caller)
    backends = _args_backends(args)
    if getattr(args, 'user_prompt', ''):
        args.focus = args.user_prompt + '\n' + args.focus
        if not (getattr(args, '_approved_ticket', None) and args.left and not args.left.strip().lower().startswith('role:')):
            user_engine = _prompt_engine_spec(backends, args.user_prompt)
            if user_engine:
                original = (args.left or 'role:review').strip().lower()
                slot = (original.split(':', 1)[1] if original.startswith('role:')
                        else _per_backend_role_target(*_engine_parts(original)))
                backend = _extract_prompt_backend_hint(args.user_prompt)
                args.left = (f'{backend}:@{slot}' if slot and backend and not _extract_prompt_model_hint(args.user_prompt)
                             else user_engine)
    raw_audit_iso = _normalize_isolation(getattr(args, 'isolation', ''), clean_room=bool(getattr(args, 'clean_room', False)))
    skipped = []
    records = collect_target_files(target, skipped)
    if raw_audit_iso == 'clean_room' and target.is_dir():
        filtered_records = []
        for rec in records:
            if Path(rec['rel_path']).name.upper() in _AGENT_INSTRUCTION_FILENAMES:
                skipped.append({'file': rec['rel_path'], 'reason': 'clean_room_excluded_agent_instruction'})
            else:
                filtered_records.append(rec)
        if records and not filtered_records:
            raise HarnessError(_CLEAN_ROOM_ONLY_INSTR_ERROR, skipped_files=skipped)
        records = filtered_records
    if not records:
        raise HarnessError('目标范围没有可审查文件', skipped_files=skipped)
    total_tokens = sum(item['tokens'] for item in records)
    try:
        engines = _audit_engines(backends, not args.single, args.left, args.right, args.dry_run, caller, args.focus,
                                 ignore_cooldown=getattr(args, 'retry_quota', False))
    except HarnessError as exc:
        if (exc.status != 'quota_exhausted' or not getattr(args, 'fallback_on_quota', False)
                or getattr(args, '_approved_ticket', None)):
            return _dispatch_print(args, {'status': exc.status, 'error': str(exc), **exc.details})
        prefix = exc.details.get('quota_backend') or 'codex'
        fallback = _local_fallback_engine(caller, prefix + ':default', backends, args.focus,
                                          clean_room=(raw_audit_iso == 'clean_room'), isolation=raw_audit_iso)
        if not fallback:
            return _dispatch_print(args, {'status': exc.status, 'error': str(exc), **exc.details})
        engines = (fallback, None)
        args._selection_fallback = _quota_fallback_record(prefix + ':default', fallback, exc.details)

    audit_engine_hint = (args.left or (engines[0] or '')) if args.single else ''
    eff_audit_user_prompt = '' if (getattr(args, '_approved_ticket', None) and args.tier != 'auto') else getattr(args, 'user_prompt', '')
    tier, tier_reason = _resolve_effective_tier(args.tier, 'audit', eff_audit_user_prompt, args.focus, audit_engine_hint,
                                                file_count=len(records), estimated_tokens=total_tokens)
    meta = {'caller': caller, 'handoff_to_caller': True, 'requested_tier': args.tier,
            'selected_tier': tier, 'tier_reason': tier_reason, 'target': str(root)}
    if getattr(args, '_selection_fallback', None):
        meta['quota_fallback'] = args._selection_fallback
    batch = _QuotaBatch(args.retry_quota)
    snap_patterns = read_agentignore_patterns(root) if not args.dry_run else None
    fallback_before = snapshot_files(root, ignore_patterns=snap_patterns) if getattr(args, 'fallback_on_quota', False) and not args.dry_run else None
    try:
        _prepare_execution(backends, engines, batch, dry_run=args.dry_run)
    except HarnessError as exc:
        q_backend = exc.details.get('quota_backend') or ''
        fb_engine = _local_fallback_engine(caller, f'{q_backend}:default', backends, args.focus,
                                           clean_room=(raw_audit_iso == 'clean_room'), isolation=raw_audit_iso) if (
            exc.status == 'quota_exhausted' and q_backend and getattr(args, 'fallback_on_quota', False)
            and not getattr(args, '_approved_ticket', None)
            and _workspace_unchanged(fallback_before, root, ignore_patterns=snap_patterns)
        ) else None
        if not fb_engine:
            return _dispatch_print(args, {'status': exc.status, **meta, 'error': str(exc), 'requested_engines': list(engines), **exc.details})
        tripped_spec = next((e for e in engines if e and _engine_parts(e)[0] == q_backend), f'{q_backend}:default')
        kept = [e for e in engines if e and _engine_parts(e)[0] != q_backend]
        if kept and not args.single:
            kept_prefix = _engine_parts(kept[0])[0]
            alt_fb = _local_fallback_engine(
                caller, f'{q_backend}:default', backends, args.focus,
                clean_room=(raw_audit_iso == 'clean_room'), isolation=raw_audit_iso,
                exclude_prefixes=(q_backend, kept_prefix),
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
            if not _workspace_unchanged(fallback_before, root, ignore_patterns=snap_patterns):
                raise HarnessError('回退预检后工作区发生变化或无法核对，已停止执行')
        except HarnessError as fb_exc:
            return _dispatch_print(args, {'status': fb_exc.status, **meta, 'error': str(fb_exc), 'requested_engines': list(engines), **fb_exc.details})
    profiles = {}
    for side, engine in zip(('left', 'right'), engines):
        if engine:
            prefix, _ = _engine_parts(engine)
            side_iso, _ = _effective_isolation(prefix, raw_audit_iso)
            _validate_isolation(prefix, side_iso, readonly=True)
            estimate = _audit_estimate([record['rel_path'] for record in records], total_tokens, args.focus,
                                       backends[prefix].get('windows_interop', False))
            profiles[side] = _planned_profile(args, backends, engine, tier, tier_reason, args.focus, 'audit', estimate['estimated_tokens'])
    if raw_audit_iso:
        meta['isolation'] = raw_audit_iso
    for profile in profiles.values():
        _check_ticket_execution(args, profile, target, 'review')
    plan = select_audit_plan(total_tokens, records, backends, args.strategy, not args.single, args.left, args.right,
                             args.shard_tokens, args.dry_run, caller, focus=args.focus, profiles=profiles, engines=engines)
    plan['skipped_files'].extend(skipped)
    plan.update(requested_tier=args.tier, selected_tier=tier, tier_reason=tier_reason)
    if args.dry_run:
        return _dispatch_print(args, {'status': 'ok', 'dry_run': True, 'plan': plan})
    for profile in profiles.values():
        _require_execution_profile(profile)
    base = _git_head(root)
    before = snapshot_files(root, ignore_patterns=snap_patterns)
    args._reconcile_target_root = root
    args._reconcile_baseline = capture_reconcile_baseline(root, ignore_patterns=snap_patterns, pre_snapshot=before)
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
                                 profile=profile, batch=batch, receipt=receipt,
                                 inline_readonly=getattr(args, 'inline_readonly', True), ephemeral=getattr(args, 'ephemeral', False),
                                 plan_dir=getattr(args, 'plan_dir', ''),
                                 isolation=raw_audit_iso)
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
    scope = _safe_changes_report(before, root, [], True, ignore_patterns=snap_patterns)
    for item in (*completed, *errors):
        inv = item.get('invocation')
        if isinstance(inv, dict):
            staged_scope = inv.pop('staged_scope', None)
            if isinstance(staged_scope, dict):
                scope = _merge_staged_scope_report(scope, staged_scope)
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
    elif not scope.get('scope_verified', True):
        status = 'failed'
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
    side_delivered = {'left': set(), 'right': set()}
    has_delivery_telemetry = False
    for unit in completed:
        ca = (unit.get('invocation') or {}).get('citation_audit')
        if isinstance(ca, dict) and (ca.get('inlined_files') or ca.get('actual_files_read')):
            has_delivery_telemetry = True
            side_delivered[unit['side']].update(
                _normalize_reconcile_paths(list(ca.get('inlined_files') or []) + list(ca.get('actual_files_read') or []), root)
            )
    all_audit_files = sorted({f for u in (*completed, *errors) for f in (u.get('planned_files') or [])})
    dual_delivered = sorted(side_delivered['left'] & side_delivered['right'])
    single_side = sorted(side_delivered['left'] ^ side_delivered['right'])
    shared_blind = sorted(set(all_audit_files) - (side_delivered['left'] | side_delivered['right'])) if has_delivery_telemetry else []
    divergent_cnt = sum(1 for item in merged['consensus_findings'] if item.get('severity_divergent'))
    cross_coverage = {
        'delivery_telemetry_present': has_delivery_telemetry,
        'planned_files_count': len(all_audit_files),
        'dual_delivered_count': len(dual_delivered),
        'single_side_delivered_count': len(single_side),
        'shared_blind_spots': shared_blind,
        'failed_units_count': len(errors),
        'divergent_findings_count': divergent_cnt,
        'summary_line': (
            f"双端实证 {len(dual_delivered)}/{len(all_audit_files)} 份，单端 {len(single_side)} 份，"
            f"失败单元 {len(errors)} 个，双端严重度分歧 {divergent_cnt} 处"
            if has_delivery_telemetry else
            f"计划 {len(all_audit_files)} 份（无底层交付遥测，失败单元 {len(errors)} 个，双端严重度分歧 {divergent_cnt} 处）"
        ),
    }
    return _dispatch_print(args, {'status': status, **meta, 'plan': plan, 'base_commit': base,
                   'completed_units': completed, 'completed_count': sum(unit['status'] == 'ok' for unit in completed),
                   'received_count': len(completed), 'total_units': len(tasks), 'errors': errors,
                   'reading_coverage_verified': False, 'cross_client_coverage': cross_coverage,
                   'consensus_count': len(merged['consensus_findings']), 'single_source_count': len(merged['single_source_findings']),
                   **merged, **scope, **scope_error, **quota_meta,
                   'verification_notice': '当前总控需核实原始文件与证据后采纳发现。'})


def cmd_gate(args):
    _begin_dispatch(args)
    if not str(args.verify_cmd).strip():
        raise HarnessError('--verify-cmd 不能为空白命令')
    requested_target, root = _target(args)
    text = _task_text(args)
    backends = _args_backends(args)
    caller = resolve_caller(args.caller)
    try:
        engine = _choose_dispatch_engine(args, backends, caller, args.worker, text)
    except HarnessError as exc:
        return _dispatch_print(args, {'status': exc.status, 'error': str(exc), **exc.details})
    gate_prefix, _ = _engine_parts(engine)
    gate_iso, _ = _effective_isolation(gate_prefix, getattr(args, 'isolation', ''))
    _validate_isolation(gate_prefix, gate_iso, readonly=False)
    scopes = _scope_patterns(args.write_scope)
    if requested_target.is_file() and not scopes:
        scopes = [requested_target.relative_to(root).as_posix()]
    file_count, est_tokens = _target_metrics(requested_target, text)
    tier, tier_reason = select_task_tier(args.tier, 'gate', text, file_count, est_tokens, engine=args.worker or engine)
    batch = _QuotaBatch(args.retry_quota)
    meta = {'caller': caller, 'handoff_to_caller': True, 'engine': engine, 'worker_engine': engine,
            'requested_tier': args.tier, 'selected_tier': tier, 'tier_reason': tier_reason,
            'target': str(root), 'requested_target': str(requested_target), 'verification_executed': False,
            'isolation': gate_iso}
    snap_ignored = _snapshot_ignore_dirs_for(scopes, include_build=True)
    snap_patterns = read_agentignore_patterns(root) if not args.dry_run else None
    fallback_before = (
        snapshot_files(root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns)
        if getattr(args, 'fallback_on_quota', False) and not args.dry_run else None
    )
    try:
        _prepare_execution(backends, (engine,), batch, dry_run=args.dry_run)
    except HarnessError as exc:
        fb_engine = _local_fallback_engine(caller, engine, backends, text, clean_room=False, isolation=getattr(args, 'isolation', '')) if (
            exc.status == 'quota_exhausted' and getattr(args, 'fallback_on_quota', False)
            and not getattr(args, '_approved_ticket', None)
            and _workspace_unchanged(fallback_before, root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns)
        ) else None
        if not fb_engine:
            return _dispatch_print(args, {'status': exc.status, **meta, 'error': str(exc), **exc.details})
        meta['quota_fallback'] = _quota_fallback_record(engine, fb_engine, exc.details)
        engine = fb_engine
        fb_prefix, _ = _engine_parts(engine)
        fb_iso, _ = _effective_isolation(fb_prefix, getattr(args, 'isolation', ''))
        meta.update(engine=engine, worker_engine=engine, isolation=fb_iso)
        try:
            _prepare_execution(backends, (engine,), batch, dry_run=args.dry_run)
            if not _workspace_unchanged(fallback_before, root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns):
                raise HarnessError('回退预检后工作区发生变化或无法核对，已停止执行')
        except HarnessError as fb_exc:
            return _dispatch_print(args, {'status': fb_exc.status, **meta, 'error': str(fb_exc), **fb_exc.details})
    profile = _planned_profile(args, backends, engine, tier, tier_reason, text, 'gate', est_tokens)
    _check_ticket_execution(args, profile, requested_target, 'gate', text, write_scope=scopes, verify_cmd=args.verify_cmd)
    if args.dry_run:
        prefix, _ = _engine_parts(engine)
        return _dispatch_print(args, {'status': 'ok', 'dry_run': True, **meta, **_profile_payload(profile),
                       'quota_cooldown': backends.get(prefix, {}).get('quota_cooldown'),
                       'work_dir': str(root), 'verify_cmd': args.verify_cmd, 'write_scope': scopes, 'max_rounds': args.max_rounds})
    _require_execution_profile(profile)
    base = _git_head(root)
    args._reconcile_target_root = root
    args._reconcile_baseline = capture_reconcile_baseline(
        root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns,
        pre_snapshot=fallback_before, capture_snapshot=False,
    )
    try:
        result = _run_gate_with_fallback(
            args, root, requested_target, text, caller, engine, backends,
            scopes, profile, 'gate', est_tokens, batch, meta, fallback_before,
            ignore_dirs=snap_ignored, ignore_patterns=snap_patterns,
        )
    except HarnessError as exc:
        return _dispatch_print(args, {'status': exc.status, **meta, 'base_commit': base, 'error': str(exc), **exc.details})
    return _dispatch_print(args, {**meta, **result, 'base_commit': base})


def cmd_task(args):
    _begin_dispatch(args)
    if args.kind == 'code' and args.verify_cmd is not None and not str(args.verify_cmd).strip():
        raise HarnessError('--verify-cmd 不能为空白命令')
    requested_target, root = _target(args)
    text = _task_text(args)
    files, negative, contract = _input_envelope(args, requested_target)
    args._stage_files = files or None
    boundary = _input_contract(files, negative, contract)
    caller = resolve_caller(args.caller)
    backends = _args_backends(args)
    try:
        engine = _choose_dispatch_engine(args, backends, caller, args.engine, text)
    except HarnessError as exc:
        return _dispatch_print(args, {'status': exc.status, 'error': str(exc), **exc.details})
    scopes = _scope_patterns(args.write_scope)
    readonly = args.kind != 'code'
    if readonly and (scopes or args.verify_cmd):
        raise HarnessError('只读任务不可声明写入范围或验收命令')
    task_prefix, _ = _engine_parts(engine)
    raw_iso_arg = getattr(args, 'isolation', '')
    task_iso, task_iso_explicit = _effective_isolation(task_prefix, raw_iso_arg, clean_room=bool(getattr(args, 'clean_room', False)))
    clean_room = task_iso == 'clean_room'
    if clean_room and not readonly:
        raise HarnessError('--clean-room 仅支持只读任务（review/direction/polish/math），写入任务不可剥离安全钩子')
    if clean_room and engine.startswith('agy'):
        raise HarnessError('agy 后端不支持剥离全局插件目录，--clean-room 请显式指定 codex、cc 或 dsh')
    _validate_isolation(task_prefix, task_iso, readonly)
    if clean_room and requested_target.is_dir() and not files:
        raw_dir_records = collect_target_files(requested_target)
        if raw_dir_records and all(Path(r['rel_path']).name.upper() in _AGENT_INSTRUCTION_FILENAMES for r in raw_dir_records):
            raise HarnessError(_CLEAN_ROOM_ONLY_INSTR_ERROR)
    rel_single_file = requested_target.relative_to(root).as_posix() if requested_target.is_file() else None
    if not readonly and rel_single_file and not scopes:
        scopes = [rel_single_file]
    base = _git_head(root)
    if args.base_commit:
        expected = _local_command(['git', '-C', str(root), 'rev-parse', args.base_commit + '^{commit}'])
        if base != expected:
            raise HarnessError('当前 HEAD 与 --base-commit 不一致')
    file_count, est_tokens = _target_metrics(root if files else requested_target, text + boundary, write_scope=files)
    tier, tier_reason = select_task_tier(args.tier, args.kind, text, file_count, est_tokens, engine=args.engine or engine)
    batch = _QuotaBatch(args.retry_quota)
    payload = {'kind': args.kind, 'caller': caller, 'handoff_to_caller': True, 'engine': engine,
               'requested_tier': args.tier, 'selected_tier': tier, 'tier_reason': tier_reason,
               'target': str(root), 'requested_target': str(requested_target), 'base_commit': base,
               'readonly': readonly, 'write_scope': scopes, 'isolation': task_iso}
    if not readonly:
        payload['verification_executed'] = False
    snap_ignored = None if readonly else _snapshot_ignore_dirs_for(scopes, include_build=bool(args.verify_cmd))
    snap_patterns = read_agentignore_patterns(root) if not args.dry_run else None
    fallback_before = (
        snapshot_files(root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns)
        if getattr(args, 'fallback_on_quota', False) and not args.dry_run else None
    )
    try:
        _prepare_execution(backends, (engine,), batch, dry_run=args.dry_run)
    except HarnessError as exc:
        fb_engine = _local_fallback_engine(caller, engine, backends, text, clean_room=clean_room,
                                           isolation=task_iso if task_iso_explicit else '') if (
            exc.status == 'quota_exhausted' and getattr(args, 'fallback_on_quota', False)
            and not getattr(args, '_approved_ticket', None)
            and _workspace_unchanged(fallback_before, root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns)
        ) else None
        if not fb_engine:
            return _dispatch_print(args, {'status': exc.status, **payload, 'result': {}, 'changed_files': [], 'scope_violations': [],
                           'error': str(exc), **exc.details})
        payload['quota_fallback'] = _quota_fallback_record(engine, fb_engine, exc.details)
        engine = fb_engine
        fb_prefix, _ = _engine_parts(engine)
        fb_iso, _ = _effective_isolation(fb_prefix, raw_iso_arg, clean_room=clean_room)
        payload.update(engine=engine, isolation=fb_iso)
        try:
            _prepare_execution(backends, (engine,), batch, dry_run=args.dry_run)
            if not _workspace_unchanged(fallback_before, root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns):
                raise HarnessError('回退预检后工作区发生变化或无法核对，已停止执行')
        except HarnessError as fb_exc:
            return _dispatch_print(args, {'status': fb_exc.status, **payload, 'result': {}, 'changed_files': [], 'scope_violations': [],
                           'error': str(fb_exc), **fb_exc.details})
    profile = _planned_profile(args, backends, engine, tier, tier_reason, text, args.kind, est_tokens)
    _check_ticket_execution(args, profile, requested_target, args.kind, text, write_scope=scopes, verify_cmd=args.verify_cmd)
    prefix, _ = _engine_parts(engine)
    payload.update(_profile_payload(profile))
    if args.dry_run:
        return _dispatch_print(args, {'status': 'ok', 'dry_run': True, 'quota_cooldown': backends.get(prefix, {}).get('quota_cooldown'), **payload})
    _require_execution_profile(profile)
    if files:
        payload['planned_files'] = list(files)
    args._reconcile_target_root = root
    if args.kind == 'code' and args.verify_cmd:
        args._reconcile_baseline = capture_reconcile_baseline(
            root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns,
            pre_snapshot=fallback_before, capture_snapshot=False,
        )
        try:
            result = _run_gate_with_fallback(
                args, root, requested_target, text + boundary, caller, engine, backends,
                scopes, profile, args.kind, est_tokens, batch, payload, fallback_before,
                ignore_dirs=snap_ignored, ignore_patterns=snap_patterns,
            )
        except HarnessError as exc:
            return _dispatch_print(args, {'status': exc.status, **payload, 'result': {}, 'changed_files': [],
                           'scope_violations': [], 'error': str(exc), **exc.details})
        return _dispatch_print(args, {**payload, **result})
    if not readonly:
        payload['verification_notice'] = '未提供 --verify-cmd，本次代码修改未经宿主机命令实测验证，请总控运行验证后再验收。'
    before = snapshot_files(root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns)
    args._reconcile_baseline = capture_reconcile_baseline(
        root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns, pre_snapshot=before,
    )
    prompt = _task_prompt(args.kind, caller, requested_target, readonly, scopes, text, boundary)
    result, error, status, invocation = {}, {}, 'ok', {}
    try:
        try:
            result = invoke_engine(profile['spec'], root, prompt, backends, TASK_JSON_SCHEMA,
                                   readonly, args.timeout, 'readonly-collaborator', args._stage_files, profile['cli_effort'], args.retry_quota,
                                   profile=profile, batch=batch, receipt=invocation, clean_room=clean_room,
                                   isolation=task_iso if task_iso_explicit else '',
                                   inline_readonly=getattr(args, 'inline_readonly', True), ephemeral=getattr(args, 'ephemeral', False),
                                   plan_dir=getattr(args, 'plan_dir', ''), write_scope=scopes)
        except HarnessError as exc:
            fb_engine = _local_fallback_engine(caller, engine, backends, text, clean_room=clean_room,
                                               isolation=task_iso if task_iso_explicit else '') if (
                getattr(exc, 'status', None) == 'quota_exhausted'
                and getattr(args, 'fallback_on_quota', False)
                and not getattr(args, '_approved_ticket', None)
                and 'quota_fallback' not in payload
                and _quota_can_replay(exc.details, invocation)
                and _workspace_unchanged(before, root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns)
                and _workspace_unchanged(fallback_before, root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns)
            ) else None
            if not fb_engine or (clean_room and fb_engine.startswith('agy')):
                raise
            payload['quota_fallback'] = _quota_fallback_record(engine, fb_engine, exc.details)
            _prepare_execution(backends, (fb_engine,), batch, dry_run=False)
            if not _workspace_unchanged(before, root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns):
                payload.pop('quota_fallback')
                raise
            fb_profile = _planned_profile(args, backends, fb_engine, tier, tier_reason, text, args.kind, est_tokens)
            _require_execution_profile(fb_profile)
            engine = fb_engine
            prefix, _ = _engine_parts(engine)
            fb_iso, _ = _effective_isolation(prefix, raw_iso_arg, clean_room=clean_room)
            payload['engine'] = engine
            payload['isolation'] = fb_iso
            payload.update(_profile_payload(fb_profile))
            invocation = {}
            result = invoke_engine(fb_profile['spec'], root, prompt, backends, TASK_JSON_SCHEMA,
                                   readonly, args.timeout, 'readonly-collaborator', args._stage_files, fb_profile['cli_effort'], args.retry_quota,
                                   profile=fb_profile, batch=batch, receipt=invocation, clean_room=clean_room,
                                   isolation=task_iso if task_iso_explicit else '',
                                   inline_readonly=getattr(args, 'inline_readonly', True), ephemeral=getattr(args, 'ephemeral', False),
                                   plan_dir=getattr(args, 'plan_dir', ''), write_scope=scopes)
    except Exception as exc:
        status = getattr(exc, 'status', 'failed')
        error = {'error': str(exc), **getattr(exc, 'details', {})}
    finally:
        staged_scope = invocation.pop('staged_scope', None) if (readonly and invocation.get('staged_readonly_workspace')) else None
        scope = dict(staged_scope) if isinstance(staged_scope, dict) else _safe_changes_report(
            before, root, scopes, readonly, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns
        )
        if staged_scope and error.get('timeout_salvage'):
            error['timeout_salvage']['changed_files_on_disk'] = _safe_changes_report(before, root, scopes, readonly,
                                                                                  ignore_patterns=snap_patterns)['changed_files']
    payload['quota_status'] = backends.get(prefix, {}).get('quota_status', payload['quota_status'])
    if not scope.get('scope_verified', True) and status == 'ok':
        status = 'failed'
        error.setdefault('error', scope.get('scope_error') or '工作区快照核对失败')
    elif scope['scope_violations']:
        status = 'scope_violation'
    if args.kind == 'polish' and status == 'ok' and isinstance(result, dict) and isinstance(result.get('content'), str):
        payload['grounding_audit'] = _compute_polish_grounding_audit(requested_target, result['content'], text)
    if args.kind == 'code' and status == 'ok' and scope.get('scope_verified') and not scope.get('changed_files'):
        payload['no_files_changed'] = True
        payload['no_files_changed_notice'] = '可写编码任务退出码为 0 但磁盘零改动，请核查是否因门禁或前置条件停手。'
    return _dispatch_print(args, {'status': status, **payload, 'invocation': invocation, 'result': result, **error, **scope})


def _compute_polish_grounding_audit(requested_target, polished_content, extra_text=''):
    orig_doc = _optional_text(requested_target) if requested_target.is_file() else ''
    anchor_root = _infer_project_anchor_root(requested_target)
    if requested_target.is_file():
        rel_label = (
            requested_target.relative_to(anchor_root).as_posix()
            if requested_target.is_relative_to(anchor_root) else requested_target.name
        )
        exclude_set = {rel_label} if requested_target.is_relative_to(anchor_root) else set()
    else:
        rel_label = requested_target.name
        exclude_set = set()
    anchor = _build_grounding_anchor(anchor_root, exclude_set, extra_allowed_text=extra_text)
    drift_hits = _audit_single_doc_text(rel_label, polished_content, anchor, baseline_text=orig_doc)
    return {
        'ok': not drift_hits,
        'finding_count': len(drift_hits),
        'findings': drift_hits,
    }


def _atomic_text(path, content):
    raw_path = Path(path).expanduser()
    for candidate in (raw_path, raw_path.parent, *raw_path.parent.parents):
        if os.path.lexists(candidate) and _is_link(candidate):
            raise HarnessError(f'拒绝通过符号链接写入文件: {candidate}')
    path = raw_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    for candidate in (raw_path, raw_path.parent, *raw_path.parent.parents, path, path.parent, *path.parent.parents):
        if os.path.lexists(candidate) and _is_link(candidate):
            raise HarnessError(f'拒绝通过符号链接写入文件: {candidate}')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(5):
            if os.path.lexists(path) and _is_link(path):
                raise HarnessError(f'拒绝通过符号链接写入文件: {path}')
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt >= 4:
                    raise
                time.sleep(0.01 * (2 ** attempt))
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path, value):
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def resolve_dispatch_plan_dir(state_dir=None):
    extra_checks = ()
    if state_dir:
        raw = Path(state_dir).expanduser()
    elif os.environ.get('CAE_PLAN_STATE_DIR'):
        raw = Path(os.environ['CAE_PLAN_STATE_DIR']).expanduser()
    elif os.environ.get('USER_GUARD_STATE_DIR'):
        raw = Path(os.environ['USER_GUARD_STATE_DIR']).expanduser() / 'dispatch_plans'
    elif os.environ.get('PLUGIN_DATA'):
        plugin_root = Path(os.environ['PLUGIN_DATA']).expanduser()
        extra_checks = (plugin_root,)
        raw = plugin_root / 'state' / 'dispatch_plans'
    else:
        raw = (Path.home() / '.gemini' / 'antigravity' / 'plugin_data' / 'user-guard' / 'state' / 'dispatch_plans').expanduser()
    for candidate in (*extra_checks, raw.parent, raw):
        if os.path.lexists(candidate) and _is_link(candidate):
            raise HarnessError(f'拒绝使用符号链接作为票据状态目录: {candidate}', 'ticket_invalid')
    return raw.resolve()


def _safe_state_id(value):
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise HarnessError('票据或会话标识符只能含字母、数字、下划线与连字符', 'ticket_invalid')
    return value


def _caller_session_id(args):
    explicit = str(getattr(args, 'session_id', '') or '').strip()
    if explicit:
        return explicit
    for key in _SESSION_ID_ENV_VARS:
        val = os.environ.get(key, '').strip()
        if val:
            return val
    return ''


def _verify_ticket_session(ticket, args):
    ticket_sid = str((ticket or {}).get('session_id') or '').strip()
    caller_sid = _caller_session_id(args)
    if (
        ticket_sid
        and ticket_sid != 'unknown_session'
        and caller_sid
        and caller_sid != 'unknown_session'
        and ticket_sid != caller_sid
    ):
        raise HarnessError('计划票据所属会话与当前会话不一致', 'ticket_invalid')


def _session_id(args):
    explicit = str(getattr(args, 'session_id', '') or '').strip()
    if explicit:
        return explicit
    approved = str((getattr(args, '_approved_ticket', None) or {}).get('session_id', '') or '').strip()
    if approved:
        return approved
    for key in _SESSION_ID_ENV_VARS:
        val = os.environ.get(key, '').strip()
        if val:
            return val
    return ''


def _clean_plan_streams(streams):
    if not isinstance(streams, list):
        return streams
    return [{k: v for k, v in s.items() if not str(k).startswith('_')} if isinstance(s, dict) else s for s in streams]


def _plan_hash(ticket):
    binding = {key: ticket.get(key) for key in ('streams', 'workflow_bg', 'workflow_goal', 'workflow_impact')}
    binding['streams'] = _clean_plan_streams(binding.get('streams'))
    return hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _load_ticket(ticket_id, plan_dir=None, approved=False):
    raw = resolve_dispatch_plan_dir(plan_dir) / (_safe_state_id(ticket_id) + '.json')
    for candidate in (raw, raw.parent):
        if os.path.lexists(candidate) and _is_link(candidate):
            raise HarnessError(f'拒绝读取符号链接计划票据: {candidate}', 'ticket_invalid')
    path = raw.resolve()
    ticket = _read_json(path)
    if not isinstance(ticket, dict) or ticket.get('ticket_id') != ticket_id:
        raise HarnessError('计划票据不存在或格式无效', 'ticket_invalid')
    required_ticket_keys = ('ticket_id', 'status', 'expires_at', 'streams', 'workflow_bg', 'workflow_goal', 'workflow_impact')
    raw_expires = ticket.get('expires_at')
    if (
        any(key not in ticket for key in required_ticket_keys)
        or not isinstance(ticket.get('status'), str)
        or not ticket['status'].strip()
        or isinstance(raw_expires, bool)
        or not isinstance(raw_expires, (int, float))
        or not math.isfinite(raw_expires)
        or not all(isinstance(ticket.get(k), str) for k in ('workflow_bg', 'workflow_goal', 'workflow_impact'))
    ):
        raise HarnessError('计划票据缺少必要字段或格式无效', 'ticket_invalid')
    streams = ticket.get('streams')
    if not isinstance(streams, list) or not streams:
        raise HarnessError('计划票据缺少有效流定义', 'ticket_invalid')
    for stream in streams:
        if not isinstance(stream, dict):
            raise HarnessError('计划票据流条目格式无效', 'ticket_invalid')
        timeout_val = stream.get('timeout')
        if (
            not isinstance(stream.get('stream_id'), str) or not stream['stream_id'].strip()
            or not isinstance(stream.get('target'), str) or not stream['target'].strip()
            or not isinstance(stream.get('kind'), str) or not stream['kind'].strip()
            or not isinstance(stream.get('tier'), str) or not stream['tier'].strip()
            or not isinstance(stream.get('engine'), str) or not stream['engine'].strip()
            or isinstance(timeout_val, bool)
            or not isinstance(timeout_val, (int, float))
            or not math.isfinite(timeout_val)
            or timeout_val <= 0
        ):
            raise HarnessError('计划票据流条目缺少必要字段或格式无效', 'ticket_invalid')
        if 'execution_profile' in stream or (
            approved and stream.get('channel', 'cae_harness_headless') == 'cae_harness_headless'
        ):
            ep = stream.get('execution_profile')
            if not isinstance(ep, dict) or not isinstance(ep.get('spec'), str) or not ep['spec'].strip():
                raise HarnessError('计划票据流条目缺少必要字段或格式无效', 'ticket_invalid')
    expired = time.time() >= float(raw_expires)
    if expired or ticket.get('plan_hash') != _plan_hash(ticket):
        raise HarnessError('计划票据已过期或内容校验失败', 'ticket_invalid')
    if approved and ticket.get('status') != 'APPROVED':
        raise HarnessError('计划票据尚未批准', 'ticket_invalid')
    return ticket


def _merge_staged_scope_report(scope, staged_scope):
    """暂存副本与原工作目录双重核验；任一快照越界或核对失败均不得放行。"""
    both_verified = bool(scope.get('scope_verified') and staged_scope.get('scope_verified'))
    c1, c2 = scope.get('changed_files'), staged_scope.get('changed_files')
    merged_changed = (
        None if (c1 is None or c2 is None) and not (c1 or c2)
        else sorted(set(c1 or []) | set(c2 or []))
    )
    return {
        **staged_scope,
        **scope,
        'scope_verified': both_verified,
        'scope_check': scope.get('scope_check') if not scope.get('scope_verified') else staged_scope.get('scope_check', 'post_execution_snapshot'),
        'changed_files': merged_changed,
        'scope_violations': sorted(set(scope.get('scope_violations') or [])
                                   | set(staged_scope.get('scope_violations') or [])),
    }


def _choose_dispatch_engine(args, backends, caller, explicit, text):
    skip_user_prompt = (
        bool(getattr(args, '_approved_ticket', None))
        and bool(explicit)
        and not explicit.strip().lower().startswith('role:')
    )
    eff_user_prompt = '' if skip_user_prompt else getattr(args, 'user_prompt', '')
    try:
        return choose_engine(backends, caller, explicit, text, eff_user_prompt,
                             ignore_cooldown=getattr(args, 'retry_quota', False))
    except HarnessError as exc:
        if exc.status != 'quota_exhausted':
            raise
        prefix = exc.details.get('quota_backend')
        if prefix and (getattr(args, 'dry_run', False) or getattr(args, 'retry_quota', False)):
            return prefix + ':default'
        if prefix and getattr(args, 'fallback_on_quota', False) and not getattr(args, '_approved_ticket', None):
            fallback = _local_fallback_engine(
                caller, prefix + ':default', backends, text,
                clean_room=bool(getattr(args, 'clean_room', False)),
                isolation=getattr(args, 'isolation', ''),
            )
            if fallback:
                args._selection_fallback = _quota_fallback_record(prefix + ':default', fallback, exc.details)
                return fallback
        raise


def _check_ticket_execution(args, profile, target, kind, text=None, *, write_scope=None, verify_cmd=None):
    ticket = getattr(args, '_approved_ticket', None)
    if not ticket:
        return
    resolved_target = Path(target).expanduser().resolve()
    target_root = resolved_target if resolved_target.is_dir() else resolved_target.parent
    matches = [stream for stream in ticket['streams']
               if stream.get('channel', 'cae_harness_headless') == 'cae_harness_headless'
               and Path(stream['target']).resolve() == resolved_target
               and stream['kind'] == kind and stream['execution_profile']['spec'] == profile['spec']
               and stream['tier'] == profile['tier'] and args.timeout <= stream['timeout']]
    if not matches:
        raise HarnessError('执行目标、模型、挡位或超时与获批票据不一致', 'ticket_invalid')
    req_prefix, _ = _engine_parts(profile['spec'])
    req_iso, _ = _effective_isolation(req_prefix, getattr(args, 'isolation', ''),
                                      clean_room=bool(getattr(args, 'clean_room', False)))
    matches = [
        stream for stream in matches
        if (stream.get('isolation') or ('clean_room' if stream.get('clean_room') else ('no_skills' if req_prefix == 'agy' else 'full'))) == req_iso
    ]
    if not matches:
        raise HarnessError(f'隔离模式（{req_iso}）与获批票据不一致', 'ticket_invalid')
    requested_input = _input_envelope(args, resolved_target)
    matches = [s for s in matches if s.get('ephemeral', False) == getattr(args, 'ephemeral', False)
               and _input_envelope(None, resolved_target, s) == requested_input]
    if not matches:
        raise HarnessError('输入文件、负向范围或会话持久化与获批票据不一致', 'ticket_invalid')
    if text is not None:
        accepted_texts = {text.strip()}
        raw_body = _raw_task_body(args)
        if raw_body:
            accepted_texts.add(raw_body)
        matches = [stream for stream in matches if stream.get('subtask', '').strip() in accepted_texts]
        if not matches:
            raise HarnessError('任务内容与获批票据不一致', 'ticket_invalid')
    if write_scope is not None:
        req_scopes = list(write_scope)
        allowed_scopes = [stream.get('write_scope') or [] for stream in matches]
        matches = [
            stream for stream in matches
            if _scope_subset_of(req_scopes, stream.get('write_scope') or [], target_root=target_root)
        ]
        if not matches:
            raise HarnessError(
                f'写入范围与获批票据不一致（当前请求：{req_scopes}；票据允许：{allowed_scopes}）',
                'ticket_invalid', requested_write_scope=req_scopes, approved_write_scopes=allowed_scopes,
            )
    if kind in ('code', 'gate') or verify_cmd is not None:
        req_verify = str(verify_cmd or '').strip()
        allowed_verify = [str(stream.get('verify_cmd') or '').strip() for stream in matches]
        if not any(cmd == req_verify for cmd in allowed_verify):
            raise HarnessError(
                f'验收命令与获批票据不一致（当前请求：{req_verify!r}；票据要求：{allowed_verify}）',
                'ticket_invalid', requested_verify_cmd=req_verify, approved_verify_cmds=allowed_verify,
            )


def _begin_dispatch(args):
    args._dispatch_started = time.monotonic()
    ticket_id = getattr(args, 'ticket', '')
    if ticket_id:
        ticket = _load_ticket(ticket_id, getattr(args, 'plan_dir', None), approved=True)
        _verify_ticket_session(ticket, args)
        args._approved_ticket = ticket


_SECRET_FIELD_KEY_RE = re.compile(
    r'(?i)(?:^|[_-])(?:api[_-]?(?:key|token)|auth[_-]?token|access[_-]?token|refresh[_-]?token|'
    r'id[_-]?token|session[_-]?token|oauth[_-]?token|bearer[_-]?token|private[_-]?key|'
    r'client[_-]?secret|secret(?:[_-]?key)?|password|passwd|passphrase|credential|'
    r'authorization|(?:set[_-]?)?cookie)s?$'
    r'|(?:api|auth|access|refresh|id|session|oauth|bearer|private|secret|client)(?:Key|Token|Secret)s?$'
)
_CGNAT_NETWORK = ipaddress.IPv4Network((0x64400000, 10))


def _redact_secret_leaves(value):
    if isinstance(value, str):
        return '<redacted>' if value else value
    if isinstance(value, list):
        return [_redact_secret_leaves(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_secret_leaves(item) for item in value)
    if isinstance(value, dict):
        return {
            (_redact_public(k) if isinstance(k, str) else k): _redact_secret_leaves(v)
            for k, v in value.items()
            if k not in ('total_cost_usd', 'cost_usd', 'modelUsage', 'plan_type')
        }
    return value


_QUOTA_FIELD_KEY_RE = re.compile(r'(?i)(?:quota|usage|rate.?limit|window|billing|budget|used_pct|remaining_pct)')
_QUOTA_CONTEXT_RE = re.compile(
    r'(?i)(?:quota|usage|5h[_ -]?window|7d[_ -]?window|rolling|rate[_ -]?limit|\bused\b|\bremaining\b|'
    r'reset|额度|配额|已用|剩余|余额|窗口|套餐|限额)'
)
_SCIENTIFIC_CONTENT_KEYS = frozenset({
    'content', 'summary', 'evidence', 'unverified', 'findings', 'description',
    'subtask', 'workflow_bg', 'workflow_goal', 'workflow_impact', 'verify_output_tail',
})


def _mask_quota_percentages(text, *, force=False):
    if '%' not in text:
        return text
    if force:
        return re.sub(r'\b\d+(?:\.\d+)?%', '<USAGE>', text)
    if '\n' not in text:
        return re.sub(r'\b\d+(?:\.\d+)?%', '<USAGE>', text) if _QUOTA_CONTEXT_RE.search(text) else text
    return '\n'.join(
        re.sub(r'\b\d+(?:\.\d+)?%', '<USAGE>', line) if ('%' in line and _QUOTA_CONTEXT_RE.search(line)) else line
        for line in text.split('\n')
    )


def _redact_public(value, *, _key='', _in_result_payload=False):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ('total_cost_usd', 'cost_usd', 'modelUsage', 'plan_type'):
                continue
            rk = _redact_public(k) if isinstance(k, str) else k
            if isinstance(k, str) and _SECRET_FIELD_KEY_RE.search(k):
                out[rk] = _redact_secret_leaves(v)
            else:
                out[rk] = _redact_public(
                    v,
                    _key=k if isinstance(k, str) else '',
                    _in_result_payload=_in_result_payload or (k == 'result'),
                )
        return out
    if isinstance(value, list):
        return [_redact_public(v, _key=_key, _in_result_payload=_in_result_payload) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_public(v, _key=_key, _in_result_payload=_in_result_payload) for v in value)
    if not isinstance(value, str):
        return value
    text = _redact_runtime_secret(value)
    temp_raw = tempfile.gettempdir()
    try:
        temp_resolved = str(Path(temp_raw).resolve())
    except OSError:
        temp_resolved = temp_raw
    for path, replacement in ((str(Path.home()), '<HOME>'), (temp_raw, '<TEMP>'), (temp_resolved, '<TEMP>')):
        if len(path) > 3:
            for variant in dict.fromkeys((path, path.replace('\\', '/'), path.replace('\\', '\\\\'))):
                text = re.sub(re.escape(variant), lambda _: replacement, text, flags=re.I)
    text = re.sub(
        r'(?i)(?:'
        r'(?:\\{2,}|//)wsl(?:\.localhost|\$)(?:\\+|/)[^\\/\s"\x27\x60\n|()（）\[\]{},;:]+(?:\\+|/)home(?:\\+|/)[^\\/\s"\x27\x60\n|()（）\[\]{},;:]+'
        r'|[A-Z]:(?:\\+|/)Users(?:\\+|/)[^\\/\s"\x27\x60\n|()（）\[\]{},;:]+'
        r'|/mnt/[a-z]/Users/[^\\/\s"\x27\x60\n|()（）\[\]{},;:]+'
        r'|(?<![A-Za-z0-9_.-])/Users/[^\\/\s"\x27\x60\n|()（）\[\]{},;:]+'
        r'|(?<![A-Za-z0-9_.-])/home/[^\\/\s"\x27\x60\n|()（）\[\]{},;:]+'
        r')',
        '<HOME>',
        text,
    )
    def mask_ip(match):
        try:
            address = ipaddress.ip_address(match.group())
            is_priv = address.is_private or (address.version == 4 and address in _CGNAT_NETWORK)
            return '<PRIVATE_IP>' if is_priv else match.group()
        except ValueError:
            return match.group()
    text = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', mask_ip, text)
    text = re.sub(r'(?i)(?<![\w:])(?:f[cd][0-9a-f]{2}:[0-9a-f:]{2,}|fe[89ab][0-9a-f]:[0-9a-f:]{2,})(?![\w:])', mask_ip, text)
    text = re.sub(r'cae-harness-[A-Za-z0-9_-]+', '<STAGING>', text)
    is_quota_key = bool(_key and _QUOTA_FIELD_KEY_RE.search(_key))
    is_scientific_field = _in_result_payload or (_key in _SCIENTIFIC_CONTENT_KEYS)
    if is_quota_key:
        text = _mask_quota_percentages(text, force=True)
    elif not is_scientific_field:
        text = _mask_quota_percentages(text, force=False)
    return text


def _result_streams(payload):
    if isinstance(payload.get('streams'), list):
        return payload['streams']
    if 'completed_units' in payload or 'errors' in payload:
        return list(payload.get('completed_units', [])) + list(payload.get('errors', []))
    if isinstance(payload.get('history'), list):
        return payload['history']
    return [payload]


def _usage_summary(streams):
    keys = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')
    usages = [item.get('token_usage') or item.get('invocation', {}).get('token_usage')
              or extract_cli_token_usage('', '') for item in streams]
    reported = sum(usage.get('usage_source') in ('cli_reported', 'cli_partial_on_timeout') for usage in usages)
    return {**{key: (sum(u[key] for u in usages if isinstance(u.get(key), int))
                      if any(isinstance(u.get(key), int) for u in usages) else None) for key in keys},
            'reported_streams': reported, 'unreported_streams': len(usages) - reported}


def _light_file_limit(cfg, has_explicit_files=False):
    base_limit = int(cfg.get('heavy_max_light_files', 2))
    if not has_explicit_files:
        return base_limit
    raw_explicit = cfg.get('heavy_max_light_explicit_files')
    return int(raw_explicit) if isinstance(raw_explicit, int) and not isinstance(raw_explicit, bool) and raw_explicit > 0 else max(base_limit, 4)


def _is_heavy_streams(streams, switched=False):
    cfg = _confirmation_tunables()
    return bool(switched or len(streams) >= cfg.get('heavy_min_streams', 2) or any(
        stream.get('kind') in ('code', 'gate')
        or stream.get('file_count', 0) > _light_file_limit(cfg, bool(stream.get('files')))
        or stream.get('estimated_tokens', 0) > cfg.get('heavy_max_light_tokens', 24000)
        or stream.get('timeout', 0) > cfg.get('heavy_max_light_timeout_sec', 600) for stream in streams))


def _resolve_reconcile_target_root(args, payload):
    """为派单完工自动机械对账安全定位目标根目录；纯 mock 无目标 batch 不盲扫宿主 CWD。"""
    candidates = []
    explicit_root = getattr(args, '_reconcile_target_root', None)
    if explicit_root:
        candidates.append(explicit_root)
    base_obj = getattr(args, '_reconcile_baseline', None)
    if isinstance(base_obj, dict) and base_obj.get('root'):
        candidates.append(base_obj['root'])
    if isinstance(payload, dict) and payload.get('target'):
        candidates.append(payload['target'])
    if isinstance(payload, dict):
        for st in _result_streams(payload):
            if not isinstance(st, dict):
                continue
            if st.get('target'):
                candidates.append(st['target'])
            inv = st.get('invocation')
            if isinstance(inv, dict) and inv.get('target_dir'):
                candidates.append(inv['target_dir'])
    raw_arg_target = getattr(args, 'target', None)
    cmd = getattr(args, 'subcommand', '')
    if raw_arg_target and not (cmd == 'batch' and str(raw_arg_target).strip() == '.' and not candidates):
        candidates.append(raw_arg_target)
    for raw in candidates:
        try:
            p = Path(str(raw)).expanduser().resolve()
            if p.is_file():
                p = p.parent
            if p.is_dir():
                return p
        except OSError:
            continue
    return None


def record_heavy_completion_for_stop_hook(args, payload):
    if (getattr(args, 'dry_run', False) or os.environ.get('CAE_HARNESS_DEPTH')
            or os.environ.get('CAE_DISABLE_HEAVY_COMPLETIONS') == '1'):
        return
    command = getattr(args, 'subcommand', '')
    heavy = (command in ('audit', 'gate', 'batch') or getattr(args, 'ticket', '')
             or getattr(args, 'kind', '') == 'code' or (command == 'followup' and payload.get('readonly') is False))
    if not heavy and getattr(args, 'target', None):
        explicit_files = list(getattr(args, '_stage_files', None) or getattr(args, 'files', None) or [])
        try:
            prompt_sample = _task_text(args)
        except (HarnessError, OSError, AttributeError, TypeError):
            prompt_sample = getattr(args, 'user_prompt', '') or ''
        try:
            resolved_target = Path(args.target).expanduser().resolve()
            metric_root = resolved_target.parent if explicit_files and resolved_target.is_file() else resolved_target
            count, tokens = _target_metrics(metric_root, prompt_sample, write_scope=explicit_files or None)
        except (HarnessError, OSError):
            count, tokens = 1, estimate_tokens_from_bytes(len((prompt_sample or '').encode('utf-8')))
        heavy = _is_heavy_streams([{'file_count': count, 'estimated_tokens': tokens, 'timeout': args.timeout, 'files': explicit_files}])
    if not heavy:
        return
    run_id = uuid.uuid4().hex
    raw_plan_arg = getattr(args, 'plan_dir', None)
    if raw_plan_arg:
        raw_plan_p = Path(raw_plan_arg).expanduser()
        if os.path.lexists(raw_plan_p) and _is_link(raw_plan_p):
            raise HarnessError(f'拒绝使用符号链接作为票据状态目录: {raw_plan_p}')
    plan_dir = resolve_dispatch_plan_dir(raw_plan_arg)
    raw_out = getattr(args, 'output', '')
    if raw_out:
        raw_receipt = Path(raw_out).expanduser()
    else:
        receipts_dir = plan_dir / 'receipts'
        if os.path.lexists(receipts_dir) and _is_link(receipts_dir):
            raise HarnessError(f'拒绝通过符号链接目录写入回执: {receipts_dir}')
        raw_receipt = receipts_dir / f'{run_id}_receipt.json'
    if os.path.lexists(raw_receipt) and _is_link(raw_receipt):
        raise HarnessError(f'拒绝通过符号链接写入回执文件: {raw_receipt}')
    receipt_path = raw_receipt.resolve()
    payload['receipt_file'] = str(receipt_path)
    # 派单完工硬触发：生成纯确定性机械对账表落盘至 plan_dir/reconcile/，不污染目标业务仓库
    reconcile_saved_md = ''
    reconcile_summary = ''
    rec_root = _resolve_reconcile_target_root(args, payload)
    if rec_root is not None:
        rec_dir = plan_dir / 'reconcile'
        if os.path.lexists(rec_dir) and _is_link(rec_dir):
            raise HarnessError(f'拒绝通过符号链接目录写入机械对账表: {rec_dir}')
        rec_ticket = getattr(args, '_approved_ticket', None)
        rec_report = build_reconciliation_report(
            rec_root,
            run_id=run_id,
            subcommand=command or 'dispatch',
            payload=payload,
            baseline=getattr(args, '_reconcile_baseline', None),
            obligations=getattr(args, '_reconcile_obligations', None),
            ticket=rec_ticket if isinstance(rec_ticket, dict) else None,
        )
        rec_md_path = rec_dir / f'{run_id}_reconcile.md'
        reconcile_saved_md = _write_reconciliation_artifact(
            rec_report, rec_md_path, redact=bool(getattr(args, 'redact', False)),
        )
        reconcile_summary = rec_report['summary_line']
        payload['reconciliation'] = {
            'run_id': run_id,
            'report_file': reconcile_saved_md,
            'chat_line': f"机械对账（{reconcile_summary}）：[{Path(reconcile_saved_md).name}]({Path(reconcile_saved_md).as_uri()})",
            'summary_line': reconcile_summary,
            'counts': rec_report['counts'],
            'baseline_source': rec_report['baseline_source'],
            'changes_status': rec_report['changes_status'],
            'alert_count': len(rec_report['alerts']),
            'pending_review_count': len(rec_report['pending_review']),
        }
    # Explicit --output is written by _dispatch_print after all metadata is attached.
    if not raw_out:
        _atomic_json(raw_receipt, _redact_public(payload))
    session_id = _session_id(args)
    if session_id:
        state_dir = plan_dir.parent
        hc_dir = state_dir / 'heavy_completions'
        if os.path.lexists(hc_dir) and _is_link(hc_dir):
            raise HarnessError(f'拒绝通过符号链接目录写入重负载完工记录: {hc_dir}')
        path = hc_dir / (_safe_state_id(session_id) + '.json')
        if os.path.lexists(path) and _is_link(path):
            raise HarnessError(f'拒绝通过符号链接写入重负载完工记录: {path}')
        entry = {'run_id': run_id, 'ticket_id': getattr(args, 'ticket', '') or None,
                 'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'subcommand': command,
                 'receipt_file': str(receipt_path), 'all_succeeded': payload.get('status') == 'ok',
                 'streams': [{'stream_id': (
                                  stream.get('stream_id')
                                  or (f"{stream['side']}-S{stream['shard_id']}" if stream.get('side') and stream.get('shard_id') is not None
                                      else f"S{stream['shard_id']}" if stream.get('shard_id') is not None
                                      else f'S{i}')
                              ),
                              'engine': stream.get('engine') or payload.get('engine'),
                              'model': stream.get('model') or payload.get('model'),
                              'succeeded': stream.get('succeeded', stream.get('status') == 'ok'),
                              'status': stream.get('status') or ('ok' if stream.get('succeeded', payload.get('status') == 'ok') else 'failed'), 'elapsed_sec': stream.get('elapsed_sec'),
                              'token_usage': stream.get('token_usage') or stream.get('invocation', {}).get('token_usage')
                                             or extract_cli_token_usage('', '')}
                             for i, stream in enumerate(_result_streams(payload), 1)],
                 'token_usage_summary': payload['token_usage_summary'], 'reported_to_user': False}
        if reconcile_saved_md:
            entry['reconcile_file'] = reconcile_saved_md
            entry['reconcile_summary'] = reconcile_summary
        with _COMPLETION_LOCK, _heavy_completion_file_lock(hc_dir, session_id):
            if os.path.lexists(path) and _is_link(path):
                raise HarnessError(f'拒绝通过符号链接写入重负载完工记录: {path}')
            if not path.exists():
                entries = []
            else:
                raw_hc = None
                for attempt in range(4):
                    try:
                        raw_hc = path.read_text(encoding='utf-8-sig')
                        break
                    except PermissionError:
                        if attempt >= 3:
                            raise
                        time.sleep(0.01 * (2 ** attempt))
                try:
                    entries = json.loads(raw_hc) if raw_hc and raw_hc.strip() else []
                except (ValueError, RecursionError) as exc:
                    raise HarnessError(f'重负载完工记录 JSON 已损坏: {path}') from exc
            if not isinstance(entries, list):
                raise HarnessError('重负载完工记录状态格式无效')
            entries.append(entry)
            _atomic_json(path, entries)


_COMPLETION_LOCK = threading.Lock()


@contextlib.contextmanager
def _directory_file_lock(directory, lock_id, label='重负载完工记录'):
    directory = Path(directory).expanduser()
    for candidate in (directory, directory.parent):
        if os.path.lexists(candidate) and _is_link(candidate):
            raise HarnessError(f'{label}目录不可为符号链接')
    directory.mkdir(parents=True, exist_ok=True)
    for candidate in (directory, directory.parent):
        if os.path.lexists(candidate) and _is_link(candidate):
            raise HarnessError(f'{label}目录不可为符号链接')
    lock_path = directory / ('.' + _safe_state_id(lock_id) + '.lock')
    for _attempt in range(8):
        if os.path.lexists(lock_path) and _is_link(lock_path):
            raise HarnessError(f'{label}锁路径不可为符号链接')
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(lock_path, flags, 0o600)
        locked = False
        try:
            opened_st = os.fstat(fd)
            if not stat.S_ISREG(opened_st.st_mode) or opened_st.st_nlink > 1:
                raise HarnessError(f'{label}锁必须为独立普通文件')
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                locked = True
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = True
            opened_after = os.fstat(fd)
            try:
                path_st = lock_path.lstat()
            except FileNotFoundError:
                path_st = None
            if path_st is not None and _is_link(lock_path, st=path_st):
                raise HarnessError(f'{label}锁路径不可为符号链接')
            if (
                not stat.S_ISREG(opened_after.st_mode)
                or opened_after.st_nlink > 1
                or (path_st is not None and not stat.S_ISREG(path_st.st_mode))
            ):
                raise HarnessError(f'{label}锁必须为独立普通文件')
            if (
                path_st is None
                or ((os.name != 'nt' and opened_after.st_nlink == 0) or opened_after.st_nlink > 1)
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
                    if os.name == 'nt':
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
    raise HarnessError(f'{label}锁文件在加锁期间发生并发替换，无法稳定获取文件锁')


def _heavy_completion_file_lock(directory, session_id):
    return _directory_file_lock(directory, session_id, label='重负载完工记录')


def _plan_dir_lock(plan_dir):
    return _directory_file_lock(plan_dir, 'plan_dir', label='计划票据')


def _dispatch_print(args, payload):
    streams = _result_streams(payload)
    for stream in streams:
        stream.setdefault('succeeded', stream.get('status', payload.get('status')) == 'ok')
        invocation = stream.get('invocation', {})
        stream.setdefault('elapsed_sec', invocation.get('elapsed_sec'))
        for key in ('provider_session_id', 'token_usage', 'citation_audit', 'timeout_salvage'):
            if key in invocation:
                stream.setdefault(key, invocation[key])
        salvage = stream.get('timeout_salvage')
        if salvage and salvage.get('changed_files_on_disk') is None:
            salvage['changed_files_on_disk'] = stream.get('worker_changed_files', stream.get('changed_files', payload.get('changed_files')))
    payload.setdefault('elapsed_sec', round(time.monotonic() - getattr(args, '_dispatch_started', time.monotonic()), 6))
    payload['succeeded'] = payload.get('status') == 'ok'
    payload['token_usage_summary'] = _usage_summary(streams)
    record_heavy_completion_for_stop_hook(args, payload)
    if getattr(args, 'output', ''):
        public = dict(payload)
        public['sanitized_invocation'] = _redact_public([stream.get('invocation', {}) for stream in streams])
        _atomic_json(args.output, public if getattr(args, 'no_redact_output', False) else _redact_public(public))
    return _print(_redact_public(payload) if getattr(args, 'redact', False) else payload)


def _read_stream_spec(path):
    value = _read_json(Path(path))
    if not isinstance(value, list) or not value or any(not isinstance(item, dict) for item in value):
        raise HarnessError('--spec-file 必须包含非空工作流对象列表')
    return value


_PLAN_CHANNEL_LABELS = {
    'cae_harness_headless': 'cae-harness 跨端无头调用',
    'native_subagent': '同端内部子代理',
    'native_workflow': '同端内部工作流',
    'native_swarm': '同端内部并行代理组',
}


def _native_agent_profile(prefix):
    profiles = _fleet_profile().get('native_agent_profiles')
    if not isinstance(profiles, dict):
        return {}
    key = 'antigravity' if prefix == 'agy' else prefix
    entry = profiles.get(key) or profiles.get(prefix)
    return dict(entry) if isinstance(entry, dict) else {}


def _read_agent_frontmatter_model(path):
    if not path.is_file() or _is_link(path):
        return ''
    text = _optional_text(path)
    match = re.match(r'^---\r?\n(.*?)\r?\n---', text, re.S)
    if not match:
        return ''
    for line in match.group(1).splitlines():
        m = re.match(r'^\s*model\s*:\s*([^\n#]+)', line)
        if m:
            val = m.group(1).strip().strip('"\'')
            return '' if not val or val.lower() == 'inherit' else val
    return ''


def _native_plan_model(prefix, info, requested_model, agent_type=''):
    """只读解析前端继承模型，不经无头后端重新选模。"""
    home = info.get('config_home')
    if not home:
        home_fn = {'codex': _codex_home, 'agy': _agy_home, 'claude': _claude_home, 'dsh': _dsh_home}[prefix]
        home, _ = home_fn(info.get('binary'))
    home = Path(home).expanduser()
    config = _top_level_toml(home / 'config.toml') if prefix == 'codex' else {}
    if requested_model and requested_model != 'inherit':
        return requested_model, requested_model, home, config
    safe_agent = agent_type if isinstance(agent_type, str) and re.fullmatch(r'[A-Za-z0-9._-]+', agent_type) else ''
    agent_model = ''
    if safe_agent and safe_agent != 'self':
        if prefix == 'codex':
            agent_toml = home / 'agents' / f'{safe_agent}.toml'
            if agent_toml.is_file() and not _is_link(agent_toml):
                raw_am = str(_top_level_toml(agent_toml).get('model') or '').strip()
                if raw_am and raw_am.lower() != 'inherit':
                    agent_model = raw_am
        elif prefix == 'claude':
            agent_model = _read_agent_frontmatter_model(home / 'agents' / f'{safe_agent}.md')
        elif prefix == 'agy':
            agent_model = _read_agent_frontmatter_model(home / 'config' / 'plugins' / 'user-guard' / 'agents' / f'{safe_agent}.md')
    if agent_model:
        return agent_model, f'inherit -> {agent_model} (agent:{safe_agent})', home, config
    sub_model = os.environ.get('CLAUDE_CODE_SUBAGENT_MODEL', '').strip() if prefix == 'claude' else ''
    if prefix == 'claude' and not sub_model:
        raw_settings = _read_json(home / 'settings.json')
        settings = raw_settings if isinstance(raw_settings, dict) else {}
        env_map = settings.get('env') if isinstance(settings.get('env'), dict) else {}
        sub_model = str(env_map.get('CLAUDE_CODE_SUBAGENT_MODEL') or '').strip()
    if sub_model.lower() == 'inherit':
        sub_model = ''
    agy_cli_fallback = ''
    if prefix == 'claude':
        model = sub_model or os.environ.get('ANTHROPIC_MODEL', '').strip() or info.get('model')
    elif prefix == 'codex':
        model = config.get('model') or info.get('model')
    elif prefix == 'agy':
        agy_cfg = _agy_config(home)
        model = agy_cfg.get('desktop_model')
        if not model:
            agy_cli_fallback = str(agy_cfg.get('cli_model') or info.get('model') or '').strip()
    elif prefix == 'dsh':
        dsh_native_cfg = _native_agent_profile('dsh')
        model = str(dsh_native_cfg.get('main_model') or '').strip() or info.get('model')
    else:
        model = info.get('model')
    if not model or model == 'inherit':
        model = None
    if model:
        display = 'inherit -> ' + model if sub_model else 'inherit (' + model + ')'
    elif agy_cli_fallback:
        display = f'inherit (跟随 Antigravity IDE 当前会话模型；CLI 默认：{agy_cli_fallback})'
    else:
        display = 'inherit (跟随当前前端所选模型)'
    return model, display, home, config


def _native_swarm_worker_model(prefix, info, kind, raw_tier='', parent_model=''):
    """当 native_swarm 配置了编码专长或异构验收模型且当前流未显式锁定模型时，按目录解析对应模型。"""
    native_cfg = _native_agent_profile(prefix)
    worker_family = str(native_cfg.get('swarm_worker_model_family') or '').strip().lower()
    worker_kinds = {str(k).strip().lower() for k in (native_cfg.get('swarm_worker_kinds') or ()) if str(k).strip()}
    verifier_family = str(native_cfg.get('swarm_verifier_model_family') or '').strip().lower()
    verifier_kinds = {str(k).strip().lower() for k in (native_cfg.get('swarm_verifier_kinds') or ()) if str(k).strip()}
    if worker_family and kind in worker_kinds:
        target_family = worker_family
        default_tier = str(native_cfg.get('swarm_worker_tier') or 'high').strip().lower() or 'high'
        role_note = 'native_swarm 编码专长推荐'
        require_heterogeneous_parent = False
    elif verifier_family and kind in verifier_kinds:
        target_family = verifier_family
        default_tier = str(native_cfg.get('swarm_verifier_tier') or 'high').strip().lower() or 'high'
        role_note = 'native_swarm 异构验收推荐'
        require_heterogeneous_parent = True
    else:
        return None, None, None
    target_tier = raw_tier if raw_tier and raw_tier != 'auto' else default_tier
    if prefix == 'agy':
        agy_blocked = {str(t).lower() for t in (_catalog_rules('agy').get('blocked_auto_tokens') or ())}
        native_models = [
            item for item in info.get('models', [])
            if _model_category('agy', item) == 'native'
            and _is_subscription_enabled('agy', item.get('slug', ''))
            and not (_tokens_lower(f"{item.get('slug', '')} {item.get('display_name', '')}") & agy_blocked)
        ]
        if require_heterogeneous_parent:
            parent_text = str(parent_model or '').strip()
            if not parent_text or target_family in _tokens_lower(parent_text):
                return None, None, None
            try:
                parent_rec, _ = _resolve_explicit_agy_record(native_models, parent_text, 'high')
            except HarnessError:
                parent_rec = {}
            if not parent_rec or target_family in _tokens_lower(parent_rec.get('slug', '')):
                return None, None, None
        families = _agy_families(native_models)
        eligible = sorted((name for name in families if _match_agy_family(families[name], target_tier)), key=_agy_family_order)
        chosen = _pick_agy_tagged_family(eligible, [target_family])
        if chosen:
            record = _match_agy_family(families[chosen], target_tier)
            slug = record.get('slug')
            if slug:
                label = record.get('display_name') or slug
                _, resolved_tier = _agy_slug_tier(slug)
                display = f'{slug} ({role_note}；swarm add 传入 model="{label}")'
                return slug, display, resolved_tier or target_tier
    return None, None, None


def _plan_explicit_file_records(target, files):
    if not files:
        return []
    root = target.parent if target.is_file() else target
    records = []
    for rel in files:
        path = root / rel
        try:
            size = path.lstat().st_size if path.is_file() else 0
        except OSError:
            size = 0
        records.append({'rel_path': rel, 'bytes': size, 'tokens': estimate_tokens_from_bytes(size)})
    return records


def _suggest_plan_shards(file_records, target_tokens, *, input_budget=None, inline_cap_bytes=0):
    if len(file_records) < 2:
        return []
    soft_tokens = max(1, min(target_tokens, input_budget or target_tokens))
    shards, bucket, b_tokens, b_bytes = [], [], 0, 0
    for rec in file_records:
        would_exceed_tokens = bool(bucket) and (b_tokens + rec['tokens'] > soft_tokens)
        would_exceed_inline = bool(bucket and inline_cap_bytes and (b_bytes + rec['bytes'] > inline_cap_bytes))
        if would_exceed_tokens or would_exceed_inline:
            shards.append({
                'shard_id': len(shards) + 1,
                'file_count': len(bucket),
                'files': list(bucket),
                'estimated_bytes': b_bytes,
                'estimated_tokens': b_tokens,
                'exceeds_single_file_budget': bool(input_budget and b_tokens > input_budget),
            })
            bucket, b_tokens, b_bytes = [], 0, 0
        bucket.append(rec['rel_path'])
        b_tokens += rec['tokens']
        b_bytes += rec['bytes']
    if bucket:
        shards.append({
            'shard_id': len(shards) + 1,
            'file_count': len(bucket),
            'files': list(bucket),
            'estimated_bytes': b_bytes,
            'estimated_tokens': b_tokens,
            'exceeds_single_file_budget': bool(input_budget and b_tokens > input_budget),
        })
    return shards


def _plan_capacity_assessment(target, files, readonly, channel, inline_readonly, profile, estimated_tokens, *, prefix=''):
    hd = _headless_tunables()
    capacity = profile.get('capacity', {})
    selected_window = capacity.get('selected_window_tokens')
    input_budget = capacity.get('input_budget_tokens')
    inline_cap_bytes = _inline_readonly_cap_bytes(prefix)
    shard_target = _positive_int(hd.get('default_shard_tokens')) or 120_000
    records = _plan_explicit_file_records(target, files)
    explicit_scope = bool(records)
    explicit_bytes = sum(rec['bytes'] for rec in records)
    ratio = round(estimated_tokens / selected_window, 4) if selected_window else None
    over_window = bool(explicit_scope and readonly and selected_window and estimated_tokens > selected_window)
    over_budget = bool(explicit_scope and readonly and input_budget and estimated_tokens > input_budget)
    exceeds_inline = bool(explicit_scope and readonly and channel == 'cae_harness_headless'
                          and inline_readonly and explicit_bytes > inline_cap_bytes)
    shards = (
        _suggest_plan_shards(
            records,
            shard_target,
            input_budget=input_budget,
            inline_cap_bytes=inline_cap_bytes if (channel == 'cae_harness_headless' and inline_readonly) else 0,
        )
        if (over_window or over_budget or exceeds_inline) and len(records) >= 2
        else []
    )
    warnings = []
    if over_window:
        warnings.append(
            f'显式只读输入估算 {estimated_tokens} tokens 超出声明上下文窗口 {selected_window} tokens'
            f'（占比 {ratio * 100:.1f}%），建议分片或换更大窗口模型'
        )
    elif over_budget:
        warnings.append(
            f'显式只读输入估算 {estimated_tokens} tokens 超出安全输入预算 {input_budget} tokens'
            f'（声明窗口 {selected_window or "未声明"}），建议分片以防中途触发压缩或超时'
        )
    if exceeds_inline:
        warnings.append(
            f'显式只读文件共 {explicit_bytes / 1024:.1f} KiB，超出内联上限 {inline_cap_bytes // 1024} KiB，'
            '超限文件将退回工具分页读取并显著拉长耗时'
        )
    return {
        'context_window': selected_window,
        'input_budget_tokens': input_budget,
        'budget_utilization_ratio': ratio,
        'over_input_budget': over_budget,
        'over_context_window': over_window,
        'exceeds_inline_cap': exceeds_inline,
        'explicit_bytes': explicit_bytes,
        'inline_cap_bytes': inline_cap_bytes,
        'capacity_warnings': warnings,
        'suggested_shards': shards,
    }


def _plan_stream_details(args, stream, profile, prefix, channel):
    native = channel != 'cae_harness_headless'
    readonly = stream['kind'] not in ('code', 'gate')
    scopes = _scope_patterns(stream.get('write_scope', getattr(args, 'write_scope', [])))
    if not readonly and not scopes:
        try:
            tgt = Path(stream['target']).expanduser().resolve()
            if tgt.is_file():
                scopes = [tgt.name]
        except OSError:
            pass
    verify_cmd = stream.get('verify_cmd', getattr(args, 'verify_cmd', '')) or ''
    access = '只读' if readonly else '写入范围：' + (', '.join(scopes) or '未声明') + '；验收命令：' + (verify_cmd or '未声明')
    if native:
        clean_room = False
        explicit_iso = ''
    else:
        raw_iso = stream.get('isolation', getattr(args, 'isolation', ''))
        raw_cr = bool(stream.get('clean_room', getattr(args, 'clean_room', False)))
        iso_mode, iso_explicit = _effective_isolation(prefix, raw_iso, clean_room=raw_cr)
        _validate_isolation(prefix, iso_mode, readonly)
        clean_room = iso_mode == 'clean_room'
        explicit_iso = iso_mode if iso_explicit else ''
    capacity = profile.get('capacity', {})
    return {**stream, 'channel': channel, 'client': prefix,
            'subtask': stream.get('subtask') or getattr(args, 'workflow_goal', '')
                       or getattr(args, 'user_prompt', '') or '检查目标',
            'agent_type': stream.get('agent_type') or getattr(args, 'agent_type', '') or ('self' if native else 'readonly-collaborator'),
            'model': profile.get('model'), 'model_display': stream.get('model_display') if native else str(profile.get('model') or '未声明'),
            'context_window': capacity.get('selected_window_tokens'),
            'headless': not native, 'clean_room': clean_room, 'isolation': explicit_iso, 'access_mode': access,
            'write_scope': scopes, 'verify_cmd': verify_cmd,
            'inline_readonly': False if native or not readonly else bool(stream.get('inline_readonly', getattr(args, 'inline_readonly', True))),
            'receipt_location': stream.get('receipt_location') or getattr(args, 'receipt_location', '')
                                or f'执行回执 streams[{stream["stream_id"]}].invocation（执行后生成）'}


def _resolve_plan_streams(args, streams, backends):
    resolved, switched = [], False
    ids = set()
    metrics_cache = {}
    for index, original in enumerate(streams, 1):
        if not isinstance(original, dict):
            raise HarnessError('工作流条目必须为对象')
        stream = dict(original)
        override_engine = bool(stream.pop('_override_engine', False))
        override_tier = bool(stream.pop('_override_tier', False))
        orig_tier_before_override = stream.pop('_orig_tier', '')
        is_persisted_stream = 'execution_profile' in stream
        for str_field in ('target', 'engine', 'model', 'subtask', 'agent_type'):
            if str_field in stream:
                val = stream[str_field]
                if val is None and str_field == 'model' and is_persisted_stream:
                    continue
                if not isinstance(val, str):
                    raise HarnessError(f'工作流字段 {str_field} 必须为字符串')
        for opt_str_field in ('requested_engine', 'requested_model'):
            if opt_str_field in stream and stream[opt_str_field] is not None and not isinstance(stream[opt_str_field], str):
                raise HarnessError(f'工作流字段 {opt_str_field} 必须为字符串')
        if 'tier' in stream:
            if not isinstance(stream['tier'], str) or (stream['tier'] and stream['tier'] not in TIER_CHOICES):
                raise HarnessError(f'工作流 tier 无效: {stream["tier"]!r}')
        if 'isolation' in stream and stream['isolation'] is not None:
            if not isinstance(stream['isolation'], str) or (stream['isolation'] and stream['isolation'].strip().lower() not in ISOLATION_CHOICES):
                raise HarnessError(f'工作流 isolation 无效: {stream["isolation"]!r}')
        sid = str(stream.get('stream_id') or f'S{index}')
        if sid in ids:
            raise HarnessError('工作流 stream_id 不可重复')
        ids.add(sid)
        target = Path(stream.get('target') or args.target).expanduser().resolve()
        if not target.exists():
            raise HarnessError(f'工作流目标不存在: {target}')
        kind = stream.get('kind') or args.kind
        if kind not in ('review', 'math', 'code', 'gate', 'polish', 'direction'):
            raise HarnessError('工作流 kind 无效')
        if _positive_int(stream.get('timeout', args.timeout)) is None:
            raise HarnessError('工作流 timeout 必须为正整数')
        wave = stream.get('wave', 1)
        ephemeral = stream.get('ephemeral', getattr(args, 'ephemeral', False))
        run_on_upstream_failure = stream.get('run_on_upstream_failure', False)
        allow_over_budget = stream.get('allow_over_budget', getattr(args, 'allow_over_budget', False))
        if (type(wave) is not int or type(ephemeral) is not bool
                or type(run_on_upstream_failure) is not bool or type(allow_over_budget) is not bool):
            raise HarnessError('工作流 wave 必须为整数，ephemeral、run_on_upstream_failure 与 allow_over_budget 必须为布尔值')
        files, negative, contract = _input_envelope(args, target, stream)
        stream.update(files=files, negative_scope_files=negative, env_contract=contract,
                      wave=wave, ephemeral=ephemeral, run_on_upstream_failure=run_on_upstream_failure,
                      allow_over_budget=allow_over_budget)
        channel = stream.get('channel') or getattr(args, 'channel', 'cae_harness_headless')
        if channel not in _PLAN_CHANNEL_LABELS:
            raise HarnessError('工作流 channel 无效')
        user_text = getattr(args, 'user_prompt', '')
        text = user_text or stream.get('subtask', '')
        requested = stream.get('requested_engine', stream.get('engine')) or args.engine
        requested_model = stream.get('requested_model', stream.get('model', getattr(args, 'model', ''))) or ''
        stream_write_scope = stream.get('write_scope', getattr(args, 'write_scope', None))
        count, tokens = _target_metrics(
            target.parent if files and target.is_file() else target,
            stream.get('subtask', '') + _input_contract(files, negative, contract),
            write_scope=files or (stream_write_scope if kind in ('code', 'gate') else None),
            cache=metrics_cache,
        )
        if channel != 'cae_harness_headless':
            caller = resolve_caller(stream.get('caller') or args.caller)
            prefix = 'agy' if caller == 'antigravity' else caller
            if prefix not in ('codex', 'agy', 'claude', 'dsh'):
                raise HarnessError('原生通道必须指定或识别 caller 客户端')
            info = backends.get(prefix, {})
            raw_requested_model = requested_model
            native_agent_type = stream.get('agent_type') or getattr(args, 'agent_type', '') or 'self'
            raw_tier_arg = stream.get('tier') or args.tier
            if override_tier:
                sub_sample = stream.get('subtask', '')
                tier, tier_reason = select_task_tier(
                    raw_tier_arg, kind,
                    '' if sub_sample == user_text else sub_sample, count, tokens,
                )
            else:
                tier, tier_reason = _resolve_effective_tier(raw_tier_arg, kind, user_text, text,
                                                            file_count=count, estimated_tokens=tokens)
            swarm_model, swarm_display, swarm_tier = (None, None, None)
            inh_model, _, home, config = _native_plan_model(prefix, info, 'inherit', agent_type=native_agent_type)
            if (channel == 'native_swarm' and not raw_requested_model
                    and not _extract_prompt_model_hint(user_text)):
                explicit_swarm_tier = (
                    tier if (override_tier or raw_tier_arg not in ('', 'auto')
                             or tier_reason.startswith(('user_description_', 'economy_')))
                    else ''
                )
                if override_tier and raw_tier_arg == 'auto' and not tier_reason.startswith(('user_description_', 'economy_')):
                    explicit_swarm_tier = ''
                swarm_model, swarm_display, swarm_tier = _native_swarm_worker_model(
                    prefix, info, kind, explicit_swarm_tier, parent_model=inh_model or '',
                )
            if swarm_model:
                model, display = swarm_model, swarm_display
                tier = swarm_tier or tier
                requested_model = ''
                profile_source = 'native_swarm_worker_profile'
            else:
                requested_model = raw_requested_model or 'inherit'
                model, display, home, config = _native_plan_model(prefix, info, requested_model, agent_type=native_agent_type)
                profile_source = 'native_frontend_config'
            record = _exact_model_record(info.get('models', []), model) if model else {}
            capacity = _execution_budget(prefix, info, record or {})
            if prefix == 'codex' and model and model == config.get('model'):
                capacity['selected_window_tokens'] = (_positive_int(config.get('model_context_window'))
                                                     or capacity['selected_window_tokens'])
            engine = prefix + ':' + (model or 'default')
            profile = {'spec': engine, 'model': model, 'tier': tier, 'capacity': capacity,
                       'profile_source': profile_source}
            native_cfg = _native_agent_profile(prefix)
            if native_cfg:
                profile['native_agent_profile'] = native_cfg
            quota = inspect_backend_quota_and_availability(prefix, info, model,
                                                            redact_usage=getattr(args, 'redact', False),
                                                            ignore_cooldown=getattr(args, 'retry_quota', False))
            cap_assess = _plan_capacity_assessment(
                target, files, kind not in ('code', 'gate'), channel, False, profile, tokens, prefix=prefix,
            )
            log_dir = home / ('sessions' if prefix == 'codex' else 'projects' if prefix == 'claude' else 'antigravity' if prefix == 'agy' else 'sessions')
            chosen = {**stream, 'stream_id': sid, 'target': str(target), 'kind': kind, 'caller': caller,
                      'requested_engine': requested, 'engine': engine, 'tier': tier, 'requested_model': requested_model,
                      'model_display': display, 'timeout': int(stream.get('timeout', args.timeout)),
                      'execution_profile': profile, 'file_count': count, 'estimated_tokens': tokens,
                      'input_budget_tokens': cap_assess['input_budget_tokens'],
                      'budget_utilization_ratio': cap_assess['budget_utilization_ratio'],
                      'over_input_budget': cap_assess['over_input_budget'],
                      'over_context_window': cap_assess['over_context_window'],
                      'exceeds_inline_cap': cap_assess['exceeds_inline_cap'],
                      'explicit_bytes': cap_assess['explicit_bytes'],
                      'inline_cap_bytes': cap_assess['inline_cap_bytes'],
                      'capacity_warnings': cap_assess['capacity_warnings'],
                      **({'suggested_shards': cap_assess['suggested_shards']} if cap_assess['suggested_shards'] else {}),
                      **quota}
            chosen['receipt_location'] = (stream.get('receipt_location') or getattr(args, 'receipt_location', '')
                                          or str(log_dir) + '（具体子代理日志由原生客户端分配）')
            resolved.append(_plan_stream_details(args, chosen, profile, prefix, channel))
            continue
        locked_tier_model = False
        if override_tier and not override_engine and stream.get('engine'):
            orig_prefix, _ = _engine_parts(stream['engine'])
            orig_model = stream.get('model') or ''
            orig_tier = orig_tier_before_override or (stream.get('execution_profile') or {}).get('tier') or 'max'
            orig_prof_src = (stream.get('execution_profile') or {}).get('profile_source')
            orig_check_text = text
            if orig_prof_src and orig_prof_src != 'prompt_fuzzy_model' and _extract_prompt_model_hint(orig_check_text):
                orig_check_text = stream.get('subtask', '') if not _extract_prompt_model_hint(stream.get('subtask', '')) else ''
            orig_model_still_current = False
            if orig_model:
                try:
                    orig_prof_check = resolve_execution_profile(
                        backends, stream['engine'], orig_tier, orig_check_text, kind, tokens,
                    )
                    orig_model_still_current = (
                        not orig_prof_check.get('execution_blocked_reason')
                        and orig_prof_check.get('model') == orig_model
                    )
                except HarnessError:
                    orig_model_still_current = False
            if orig_model_still_current:
                if orig_prefix == 'agy':
                    cand_family = _agy_slug_tier(orig_model)[0]
                    if cand_family and any(
                        _agy_slug_tier(m.get('slug'))[0] == cand_family
                        for m in backends.get('agy', {}).get('models', [])
                    ):
                        requested = f'agy:{cand_family}'
                        requested_model = ''
                        locked_tier_model = True
                    else:
                        requested_model = orig_model
                        locked_tier_model = True
                else:
                    requested_model = orig_model
                    locked_tier_model = True
        eff_user_engine = '' if (override_engine or locked_tier_model) else user_text
        eff_text_engine = (
            (stream.get('subtask', '') if not _extract_prompt_model_hint(stream.get('subtask', '')) else '')
            if (override_engine or locked_tier_model) else text
        )
        excluded = set()
        last_error = None
        last_over_window_exc = None
        chosen = None
        stream_switched = False
        auto_route = (not _extract_prompt_backend_hint(eff_user_engine) and not _extract_prompt_model_hint(eff_user_engine)
                      and (not requested or requested.startswith('role:')) and not requested_model)
        while len(excluded) < len(backends):
            available = {key: info for key, info in backends.items() if key not in excluded}
            engine = ''
            try:
                engine = choose_engine(available, args.caller, requested, eff_text_engine, eff_user_engine,
                                       ignore_cooldown=getattr(args, 'retry_quota', False))
                model = requested_model
                if model and model != 'inherit' and not _extract_prompt_model_hint(eff_user_engine) and not excluded:
                    engine = _engine_parts(engine)[0] + ':' + model
                if override_tier:
                    sub_sample = stream.get('subtask', '')
                    tier, _ = select_task_tier(
                        stream.get('tier') or args.tier, kind,
                        '' if sub_sample == user_text else sub_sample, count, tokens, engine=engine,
                    )
                else:
                    eff_user_tier = '' if override_engine else user_text
                    eff_text_tier = eff_text_engine if override_engine else text
                    tier, _ = _resolve_effective_tier(stream.get('tier') or args.tier, kind, eff_user_tier, eff_text_tier, engine,
                                                      file_count=count, estimated_tokens=tokens)
                profile = resolve_execution_profile(available, engine, tier, eff_text_engine, kind, tokens)
                _require_execution_profile(profile)
                eff_profile_tier = profile.get('tier') or tier
                prefix, _ = _engine_parts(engine)
                eff_readonly = kind not in ('code', 'gate')
                eff_inline_ro = False if (channel != 'cae_harness_headless' or not eff_readonly) else bool(
                    stream.get('inline_readonly', getattr(args, 'inline_readonly', True))
                )
                _check_writable_transport(target if target.is_dir() else target.parent, backends[prefix], eff_readonly)
                _check_readonly_inline_transport(prefix, backends[prefix], eff_readonly, eff_inline_ro)
                raw_iso_stream = stream.get('isolation', getattr(args, 'isolation', ''))
                raw_cr_stream = bool(stream.get('clean_room', getattr(args, 'clean_room', False)))
                stream_iso_mode, _ = _effective_isolation(prefix, raw_iso_stream, clean_room=raw_cr_stream)
                _validate_isolation(prefix, stream_iso_mode, readonly=eff_readonly)
                quota = inspect_backend_quota_and_availability(prefix, backends[prefix], profile['model'],
                                                                redact_usage=getattr(args, 'redact', False),
                                                                ignore_cooldown=getattr(args, 'retry_quota', False))
                if not quota['currently_available']:
                    last_error = quota['unavailable_reason']
                    if not auto_route:
                        raise HarnessError('指定通道不可用：' + str(last_error), last_error, stream_id=sid)
                    excluded.add(prefix)
                    stream_switched = True
                    continue
                cap_assess = _plan_capacity_assessment(
                    target, files, eff_readonly, channel, eff_inline_ro, profile, tokens, prefix=prefix,
                )
                if cap_assess['over_context_window'] and not allow_over_budget:
                    shard_note = (f'（已生成 {len(cap_assess["suggested_shards"])} 组建议分片）'
                                  if cap_assess['suggested_shards'] else '')
                    raise HarnessError(
                        f'流 {sid}（{profile.get("spec") or engine}）显式只读文件估算约 {tokens} tokens，'
                        f'超出模型声明上下文窗口 {cap_assess["context_window"]} tokens{shard_note}；'
                        '请按 suggested_shards 拆分为多批/多流执行，改用更大窗口引擎，'
                        '或仅确认抽查时显式传 --allow-over-budget / allow_over_budget: true',
                        'over_context_window',
                        stream_id=sid,
                        engine=profile.get('spec') or engine,
                        model=profile.get('model'),
                        estimated_tokens=tokens,
                        context_window=cap_assess['context_window'],
                        input_budget_tokens=cap_assess['input_budget_tokens'],
                        explicit_bytes=cap_assess['explicit_bytes'],
                        suggested_shards=cap_assess['suggested_shards'],
                        allow_override_flag='--allow-over-budget',
                    )
                chosen = {**stream, 'stream_id': sid, 'target': str(target), 'kind': kind,
                          'requested_engine': requested, 'requested_model': requested_model, 'engine': engine, 'tier': eff_profile_tier,
                          'timeout': int(stream.get('timeout') or args.timeout), 'execution_profile': profile,
                          'file_count': count, 'estimated_tokens': tokens,
                          'input_budget_tokens': cap_assess['input_budget_tokens'],
                          'budget_utilization_ratio': cap_assess['budget_utilization_ratio'],
                          'over_input_budget': cap_assess['over_input_budget'],
                          'over_context_window': cap_assess['over_context_window'],
                          'exceeds_inline_cap': cap_assess['exceeds_inline_cap'],
                          'explicit_bytes': cap_assess['explicit_bytes'],
                          'inline_cap_bytes': cap_assess['inline_cap_bytes'],
                          'capacity_warnings': cap_assess['capacity_warnings'],
                          **({'suggested_shards': cap_assess['suggested_shards']} if cap_assess['suggested_shards'] else {}),
                          **quota}
                break
            except HarnessError as exc:
                last_error = exc.status
                if exc.status == 'over_context_window':
                    last_over_window_exc = exc
                if not auto_route:
                    raise
                if engine:
                    prefix, _ = _engine_parts(engine)
                    if prefix not in excluded:
                        excluded.add(prefix)
                        stream_switched = True
                        continue
                break
        if chosen is None:
            if last_over_window_exc is not None:
                raise last_over_window_exc
            raise HarnessError('二次检查门未找到可用通道', last_error or 'failed', stream_id=sid)
        switched |= stream_switched and (not original.get('execution_profile')
                                        or engine != original.get('engine')
                                        or profile['model'] != original.get('model'))
        resolved.append(_plan_stream_details(args, chosen, profile, prefix, channel))
    return resolved, switched


def _format_card_detail_link(detail_path):
    clean = str(detail_path or '').replace('\r\n', '\n').replace('\r', ' ').replace('\n', ' ').strip()
    if not clean:
        return ''
    name = re.split(r'[/\\]', clean.rstrip('/\\'))[-1] or clean
    if clean.startswith('<'):
        return clean
    posix = clean.replace('\\', '/')
    encoded = (
        posix.replace('%', '%25')
        .replace(' ', '%20')
        .replace('(', '%28')
        .replace(')', '%29')
        .replace('[', '%5B')
        .replace(']', '%5D')
        .replace('#', '%23')
        .replace('?', '%3F')
    )
    if re.match(r'^[A-Za-z]:/', posix):
        uri = 'file:///' + encoded
    elif posix.startswith('//'):
        uri = 'file:' + encoded
    elif posix.startswith('/'):
        uri = 'file://' + encoded
    else:
        return clean
    return f'[{name}]({uri})'


def _render_plan(ticket):
    columns = ['流号', '子任务与范围', '通道', '客户端及子代理类型', '模型名字', '挡位', '上下文窗口',
               '是否无头', '是否净室', '访问模式、写入范围与验收命令', '内联与超时', '当前是否可用',
               '本机配置额度类型与当前状态', '回执或子代理日志位置']
    rows = ['| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |',
            '| --- | --- | --- | --- |']
    details = []

    def _clean_text(value):
        return str(value).replace('\r\n', '\n').replace('\r', ' ')

    def _cell(value):
        return _clean_text(value).replace('|', r'\|').replace('\n', ' ')

    def row(values):
        return '| ' + ' | '.join(_cell(value) for value in values) + ' |'

    yes_no = lambda value: '是' if value else '否'
    for stream in ticket['streams']:
        available = yes_no(stream['currently_available'])
        if stream.get('unavailable_reason'):
            available += '（' + stream['unavailable_reason'] + '）'
        clean_room_cell = yes_no(stream['clean_room'])
        if not stream['clean_room'] and stream.get('isolation') in ('full', 'no_skills', 'hooks_only'):
            clean_room_cell += f'（隔离：{stream["isolation"]}）'
        window_cell = str(stream['context_window']) + ' tokens' if stream['context_window'] else '未声明'
        if stream.get('over_context_window') or stream.get('over_input_budget'):
            est_tok = stream.get('estimated_tokens')
            budget_tok = stream.get('input_budget_tokens')
            shard_cnt = len(stream.get('suggested_shards') or ())
            tag = '超声明窗口' if stream.get('over_context_window') else '超安全预算'
            window_cell += f'（估算 {est_tok} / 安全预算 {budget_tok} tokens；⚠ {tag}'
            if shard_cnt:
                window_cell += f'，建议拆 {shard_cnt} 片'
            window_cell += '）'
        inline_cell = '内联：' + yes_no(stream['inline_readonly']) + '；超时：' + str(stream['timeout']) + ' 秒'
        if stream.get('exceeds_inline_cap') and stream.get('explicit_bytes'):
            cap_kib = (stream.get('inline_cap_bytes') or _inline_readonly_cap_bytes(stream.get('client'))) // 1024
            inline_cell += f'（⚠ 显式文件 {stream["explicit_bytes"] / 1024:.1f} KiB > {cap_kib} KiB 内联上限，超限文件将分页读取）'
        values = [stream['stream_id'], stream.get('subtask', '') + '；范围：' + stream['target'],
                  _PLAN_CHANNEL_LABELS[stream['channel']] + ' (' + stream['channel'] + ')',
                  stream['client'] + '；' + stream['agent_type'], stream['model_display'], stream['tier'],
                  window_cell,
                  yes_no(stream['headless']), clean_room_cell, stream['access_mode'],
                  inline_cell,
                  available, stream['quota_scheme'] + '；' + stream['quota_summary'], stream['receipt_location']]
        rows.append(row([values[0], values[2] + '；' + values[3], values[4] + '；' + values[5],
                         values[11] + '；' + values[12]]))
        details += ['', '#### 流 ' + _clean_text(stream['stream_id']).replace('\n', ' ') + '（14 项完整参数明细）', '',
                    '| 序号 | 参数项 | 当前值 |', '| --- | --- | --- |']
        details.extend(row([index, name, value]) for index, (name, value) in enumerate(zip(columns, values), 1))
        if stream.get('capacity_warnings'):
            details += ['', '**容量与内联预警**：']
            details.extend(f'- {_clean_text(warn)}' for warn in stream['capacity_warnings'])
        if stream.get('suggested_shards'):
            details += ['', '**建议分片（按单分片预算与内联上限切分）**：']
            for sh in stream['suggested_shards']:
                file_list = ', '.join(_clean_text(f) for f in sh.get('files', []))
                single_flag = '（单文件超安全预算）' if sh.get('exceeds_single_file_budget') else ''
                details.append(
                    f'- 分片 {sh["shard_id"]}（{sh["file_count"]} 文件，约 {sh["estimated_bytes"] / 1024:.1f} KiB / '
                    f'{sh["estimated_tokens"]} tokens{single_flag}）：{file_list}'
                )
    overview_markdown = '\n'.join(rows)
    rows.extend(details)
    ledger = ticket.get('capability_ledger', {})
    status = _clean_text(ticket.get('status') or 'PENDING').strip() or 'PENDING'
    tid_clean = _clean_text(ticket['ticket_id']).replace('\n', ' ')
    if status == 'PENDING':
        popup_footer = '\n\n选项：① 批准当前计划（推荐）；② 微调引擎、挡位或超时后批准；③ 暂缓并交接。'
    elif status == 'APPROVED':
        popup_footer = f'\n\n当前状态：已批准（APPROVED），可按票据 {tid_clean} 执行（--ticket {tid_clean}）。'
    elif status == 'HANDED_OFF':
        popup_footer = '\n\n当前状态：已登记交接（HANDED_OFF）；新会话复核后可选：① 批准当前计划；② 微调引擎、挡位或超时后批准；③ 暂缓并交接。'
    else:
        popup_footer = f'\n\n当前状态：{status}。'
    ledger_line = (
        'capability_ledger 离线性能记录更新时间：在线刷新 ' + _clean_text(ledger.get('last_online_refresh_at'))
        + '；本机实测 ' + _clean_text(ledger.get('local_bench_verified_at'))
    )
    markdown = ('工作流背景：' + _clean_text(ticket['workflow_bg']) + '\n\n要做什么：' + _clean_text(ticket['workflow_goal'])
                + '\n\n预计后果：' + _clean_text(ticket['workflow_impact']) + '\n\n' + '\n'.join(rows)
                + '\n\n' + ledger_line
                + popup_footer
                + '\n\n票据：' + tid_clean + '；状态：' + status)
    ticket['human_popup_markdown'] = markdown
    persisted = bool(ticket.get('card_detail_file'))
    detail_link = _format_card_detail_link(ticket.get('card_detail_file') or '') if persisted else ''
    detail_ref = (
        f'\n完整 14 项参数明细：{detail_link}'
        if detail_link
        else '\n完整 14 项参数明细：尚未落盘（如需持久票据与明细链接请加 --save-ticket）'
    )
    ticket['compact_summary_card'] = (
        '工作流背景：' + _clean_text(ticket['workflow_bg'])
        + '\n工作流目标：' + _clean_text(ticket['workflow_goal'])
        + '\n预计后果：' + _clean_text(ticket['workflow_impact'])
        + '\n\n' + overview_markdown
        + '\n\n' + ledger_line
        + detail_ref
        + popup_footer
        + '\n\n票据：' + tid_clean + '；状态：' + status
    )
    all_readonly = all(s.get('access_mode') == '只读' for s in ticket['streams'])
    mode_badge = '严格只读' if all_readonly else '含写入范围'
    stream_tags = '、'.join(
        f"{_clean_text(s['stream_id']).strip()} {_clean_text(s['client']).strip()}:{_clean_text(s['model_display']).strip()} ({_clean_text(s['tier']).strip()})"
        for s in ticket['streams']
    )
    unavailable_notes = [
        f"{_clean_text(s['stream_id']).strip()}:{_clean_text(s.get('unavailable_reason') or '不可用').strip()}"
        for s in ticket['streams']
        if not s.get('currently_available')
    ]
    quota_brief = ('不可用：' + '，'.join(unavailable_notes)) if unavailable_notes else '额度均可用'
    warn_streams = [
        _clean_text(s['stream_id']).strip()
        for s in ticket['streams']
        if s.get('capacity_warnings')
    ]
    if warn_streams:
        quota_brief += '；⚠ ' + '、'.join(warn_streams) + ' 超安全预算或内联上限，见明细'
    goal_one_line = re.sub(r'\s+', ' ', _clean_text(ticket['workflow_goal'])).strip()
    if len(goal_one_line) > 140:
        goal_one_line = goal_one_line[:137] + '...'
    if status == 'PENDING':
        action_line = f'**请确认**：回复 **「批准」** 启动；如需调整模型或挡位请直接说明，暂缓请回复「交接」（票据 `{tid_clean}`，状态：`{status}`）。'
    elif status == 'HANDED_OFF':
        action_line = f'**待接续确认**：已交接票据 `{tid_clean}`（状态：`{status}`），回复 **「批准」** 启动，或说明微调要求。'
    elif status == 'APPROVED':
        action_line = f'当前状态：已批准（`{status}`），绑定票据 `{tid_clean}` 执行。'
    else:
        action_line = f'票据：`{tid_clean}`；状态：`{status}`。'
    ticket['chat_brief'] = (
        f'**【待确认派工 · {mode_badge}】** {goal_one_line}（共 {len(ticket["streams"])} 路：{stream_tags}；{quota_brief}）。\n'
        f'- 完整总览与 14 项明细：{detail_link}\n'
        f'- {action_line}'
    ) if (persisted and detail_link) else ticket['compact_summary_card']
    resume_ref = ('\n恢复命令：plan --resume-ticket ' + tid_clean) if persisted else ''
    ticket['handoff_prompt_block'] = ('【新对话提示词】\n请接力以下完整工作流，先读取并复核票据，不要推测缺失内容。\n'
                                      + markdown + resume_ref)
    ticket['compact_handoff_prompt_block'] = (
        '【新对话提示词】\n请接力以下未决重负载工作流，先读取并复核磁盘票据与完整 14 项明细文件，不要推测缺失内容。\n'
        + ticket['compact_summary_card']
        + resume_ref
    ) if persisted else ticket['handoff_prompt_block']


def _compact_plan_stdout_view(ticket):
    """生成精简 stdout 视图：置顶 chat_brief 与 compact_summary_card，省略已落盘的 14 项长表与 execution_profile。"""
    detail_file = str(ticket.get('card_detail_file') or '')
    full_ticket_file = str(Path(detail_file).with_name(f"{ticket['ticket_id']}.json")) if detail_file else ''
    compact_streams = [
        {k: v for k, v in s.items() if k != 'execution_profile'} if isinstance(s, dict) else s
        for s in ticket.get('streams', [])
    ]
    view = {
        'ticket_id': ticket.get('ticket_id'),
        'status': ticket.get('status'),
        'is_heavy': ticket.get('is_heavy'),
        'stdout_view': 'compact',
        'card_detail_file': detail_file,
        'full_ticket_file': full_ticket_file,
        'chat_brief': ticket.get('chat_brief', ''),
        'compact_summary_card': ticket.get('compact_summary_card', ''),
    }
    if ticket.get('status') not in ('APPROVED', 'CANCELLED', 'SUPERSEDED'):
        view['compact_handoff_prompt_block'] = ticket.get('compact_handoff_prompt_block', '')
    for key in (
        'session_id', 'plan_hash', 'created_at', 'expires_at', 'user_prompt',
        'workflow_bg', 'workflow_goal', 'workflow_impact', 'capability_ledger',
        'superseded_ticket_ids',
    ):
        if key in ticket:
            view[key] = ticket[key]
    view['streams'] = compact_streams
    return view


def _supersede_overlapping_pending_tickets(approved_ticket, plan_dir):
    """批准票据后，将同会话、更早创建且通道存在交集的旧 PENDING/HANDED_OFF 票据标记为 SUPERSEDED。"""
    sid = str(approved_ticket.get('session_id') or '').strip()
    if not sid or sid == 'unknown_session':
        return []
    approved_tid = str(approved_ticket.get('ticket_id') or '').strip()
    try:
        approved_created = float(approved_ticket.get('created_at') or 0.0)
    except (TypeError, ValueError):
        approved_created = 0.0
    approved_channels = {
        s.get('channel', 'cae_harness_headless')
        for s in approved_ticket.get('streams', [])
        if isinstance(s, dict)
    }
    if not approved_channels:
        return []
    plan_dir = resolve_dispatch_plan_dir(plan_dir)
    superseded = []
    for path in sorted(plan_dir.glob('*.json')):
        if _is_link(path) or path.name.startswith('session_'):
            continue
        other = _read_json(path)
        if not isinstance(other, dict):
            continue
        other_tid = str(other.get('ticket_id') or '').strip()
        if (
            not other_tid
            or other_tid == approved_tid
            or path.name != other_tid + '.json'
            or other.get('session_id') != sid
            or other.get('status') not in ('PENDING', 'HANDED_OFF')
        ):
            continue
        try:
            other_created = float(other.get('created_at') or 0.0)
        except (TypeError, ValueError):
            continue
        if approved_created > 0 and other_created > approved_created:
            continue
        other_channels = {
            s.get('channel', 'cae_harness_headless')
            for s in other.get('streams', [])
            if isinstance(s, dict)
        }
        if not (other_channels & approved_channels):
            continue
        try:
            with _directory_file_lock(plan_dir, f'ticket_{other_tid}', label='计划票据'):
                latest = _read_json(path)
                if not isinstance(latest, dict) or latest.get('status') not in ('PENDING', 'HANDED_OFF'):
                    continue
                latest['status'] = 'SUPERSEDED'
                latest['superseded_by'] = approved_tid
                _save_plan(latest, plan_dir)
                superseded.append(other_tid)
        except (HarnessError, OSError):
            continue
    return superseded


def _save_plan(ticket, plan_dir):
    tid = _safe_state_id(ticket.get('ticket_id', ''))
    plan_dir = resolve_dispatch_plan_dir(plan_dir)
    if isinstance(ticket.get('streams'), list):
        ticket['streams'] = _clean_plan_streams(ticket['streams'])
    ticket['plan_hash'] = _plan_hash(ticket)
    detail_path = plan_dir / (tid + '_card.md')
    ticket_path = plan_dir / (tid + '.json')
    ticket['card_detail_file'] = str(detail_path)
    _render_plan(ticket)
    _atomic_json(ticket_path, ticket)
    _atomic_text(detail_path, ticket['human_popup_markdown'] + '\n')
    session_id = str(ticket.get('session_id') or '').strip()
    if session_id:
        pointer = plan_dir / ('session_' + _safe_state_id(session_id) + '_pending.json')
        if os.path.lexists(pointer) and _is_link(pointer):
            raise HarnessError(f'拒绝操作符号链接会话指针文件: {pointer}', 'ticket_invalid')
        if ticket['status'] in ('APPROVED', 'CANCELLED', 'SUPERSEDED'):
            existing_ptr = _read_json(pointer)
            if isinstance(existing_ptr, dict) and existing_ptr.get('ticket_id') == tid:
                pointer.unlink(missing_ok=True)
        else:
            _atomic_json(pointer, {'ticket_id': tid, 'status': ticket['status'],
                                   'ticket_file': str(ticket_path)})


def cmd_plan(args):
    if getattr(args, 'ticket', ''):
        raise HarnessError(
            'plan 子命令不支持 --ticket；批准票据请用 --approve-ticket <ID>，'
            '恢复查看请用 --resume-ticket <ID>，登记交接请用 --mark-handed-off <ID>，'
            '取消待决票据请用 --cancel-ticket <ID>，按已批准票据执行请改用 batch/gate/task/audit --ticket <ID>',
            'ticket_invalid',
        )
    if getattr(args, 'override_json', '') and not getattr(args, 'approve_ticket', ''):
        raise HarnessError(
            '--override-json 仅可配合 --approve-ticket <ID> 在批准票据时微调 engine/tier/timeout',
            'ticket_invalid',
        )
    plan_dir = resolve_dispatch_plan_dir(args.plan_dir)
    cancel_tid = getattr(args, 'cancel_ticket', '')
    ticket_id = args.approve_ticket or args.resume_ticket or args.mark_handed_off or cancel_tid
    if ticket_id:
        with _plan_dir_lock(plan_dir), _directory_file_lock(plan_dir, f'ticket_{ticket_id}', label='计划票据'):
            ticket = _load_ticket(ticket_id, plan_dir)
            if args.approve_ticket or args.mark_handed_off or cancel_tid:
                _verify_ticket_session(ticket, args)
            forced = bool(getattr(args, 'force', False))
            if args.resume_ticket and not forced:
                args.user_prompt = ticket.get('user_prompt', '') or args.user_prompt
            else:
                args.user_prompt = args.user_prompt or ticket.get('user_prompt', '')
                ticket['user_prompt'] = args.user_prompt
            if cancel_tid:
                if ticket.get('status') == 'APPROVED' and not forced:
                    raise HarnessError('已批准票据不可直接取消，如需强制取消请传 --force', 'ticket_invalid')
                ticket['status'] = 'CANCELLED'
                _save_plan(ticket, plan_dir)
            elif args.mark_handed_off:
                if ticket.get('status') in ('APPROVED', 'CANCELLED', 'SUPERSEDED') and not forced:
                    raise HarnessError(f'票据当前状态为 {ticket.get("status")}，不可直接转为已交接状态；如需强制交接请传 --force', 'ticket_invalid')
                ticket['status'] = 'HANDED_OFF'
            else:
                if args.approve_ticket and ticket.get('status') in ('CANCELLED', 'SUPERSEDED') and not forced:
                    raise HarnessError(f'终态票据（{ticket.get("status")}）不可直接批准，请重新生成计划或传 --force', 'ticket_invalid')
                orig_streams = copy.deepcopy(ticket['streams'])
                candidate_streams = copy.deepcopy(orig_streams)
                overrides = {}
                if args.approve_ticket and args.override_json:
                    try:
                        overrides = json.loads(args.override_json)
                    except ValueError as exc:
                        raise HarnessError('--override-json 不是合法 JSON', 'ticket_invalid') from exc
                    if not isinstance(overrides, dict):
                        raise HarnessError('--override-json 须按 stream_id 提供对象', 'ticket_invalid')
                    ordered_ids = [stream['stream_id'] for stream in orig_streams]
                    known_ids = set(ordered_ids)
                    unknown_ids = sorted(set(overrides) - known_ids)
                    if unknown_ids:
                        raise HarnessError(
                            f'--override-json 包含未知 stream_id: {", ".join(unknown_ids)}（当前票据可选流号：{", ".join(ordered_ids)}）',
                            'ticket_invalid', unknown_stream_ids=unknown_ids, available_stream_ids=ordered_ids,
                        )
                    valid_tiers = ', '.join(TIER_CHOICES)
                    for sid, raw_ov in overrides.items():
                        if not isinstance(raw_ov, dict):
                            raise HarnessError(f'--override-json[{sid}] 必须为 JSON 对象', 'ticket_invalid')
                        if set(raw_ov) - {'engine', 'tier', 'timeout'}:
                            raise HarnessError('只可微调 engine、tier、timeout', 'ticket_invalid')
                        if 'tier' in raw_ov and (not isinstance(raw_ov['tier'], str) or raw_ov['tier'] not in TIER_CHOICES):
                            raise HarnessError(
                                f'--override-json[{sid}].tier 无效：{raw_ov["tier"]!r}（可选值：{valid_tiers}）',
                                'ticket_invalid',
                            )
                        if 'timeout' in raw_ov and _positive_int(raw_ov['timeout']) is None:
                            raise HarnessError(f'--override-json[{sid}].timeout 必须为正整数', 'ticket_invalid')
                        if 'engine' in raw_ov and (not isinstance(raw_ov['engine'], str) or not raw_ov['engine'].strip()):
                            raise HarnessError(f'--override-json[{sid}].engine 必须为非空字符串', 'ticket_invalid')
                    for orig_stream, stream in zip(orig_streams, candidate_streams):
                        override = dict(overrides.get(stream['stream_id'], {}))
                        if 'engine' in override and stream.get('channel', 'cae_harness_headless') != 'cae_harness_headless':
                            raise HarnessError(
                                f'原生通道流 {stream["stream_id"]} 不支持通过 --override-json 微调 engine',
                                'ticket_invalid',
                            )
                        if 'timeout' in override:
                            override['timeout'] = int(override['timeout'])
                        if 'tier' in override:
                            stream['_orig_tier'] = orig_stream.get('tier') or ''
                        stream.update(override)
                        if 'engine' in override:
                            stream['requested_engine'] = override['engine']
                            stream['requested_model'] = ''
                            stream['_override_engine'] = True
                            if 'tier' not in override:
                                stream['tier'] = 'auto'
                        if 'tier' in override:
                            stream['_override_tier'] = True
                backends = _args_backends(args, refresh_model_cache=False)
                core_fields = ('engine', 'model', 'tier', 'timeout', 'channel', 'context_window',
                               'currently_available', 'unavailable_reason', 'quota_exceeded')
                expected = [{key: stream.get(key) for key in core_fields} for stream in candidate_streams]
                profile_fields = ('spec', 'model', 'reasoning_effort', 'cli_effort')
                expected_profiles = [{key: stream.get('execution_profile', {}).get(key) for key in profile_fields}
                                     for stream in candidate_streams]
                # 微调的预期值先按用户指定引擎和挡位计算；第二道门仍负责检查可用性和漂移。
                if args.approve_ticket and args.override_json:
                    for orig_stream, stream, core, profile_core in zip(orig_streams, candidate_streams, expected, expected_profiles):
                        override = overrides.get(stream['stream_id'], {})
                        if not (set(override) & {'engine', 'tier'}):
                            continue
                        if stream.get('channel', 'cae_harness_headless') != 'cae_harness_headless':
                            if 'tier' in override:
                                sub_sample = stream.get('subtask', '')
                                tier, tier_reason = select_task_tier(
                                    stream['tier'], stream['kind'],
                                    '' if sub_sample == args.user_prompt else sub_sample,
                                    stream.get('file_count', 1), stream.get('estimated_tokens', 0),
                                )
                                core['tier'] = tier
                                if (stream.get('channel') == 'native_swarm'
                                        and not stream.get('requested_model')
                                        and not _extract_prompt_model_hint(args.user_prompt)):
                                    caller_p = resolve_caller(stream.get('caller') or args.caller)
                                    p_prefix = 'agy' if caller_p == 'antigravity' else caller_p
                                    eff_swarm_tier = (
                                        '' if stream['tier'] == 'auto'
                                        and not tier_reason.startswith(('user_description_', 'economy_'))
                                        else tier
                                    )
                                    p_info = backends.get(p_prefix, {})
                                    p_inh, _, _, _ = _native_plan_model(
                                        p_prefix, p_info, 'inherit',
                                        agent_type=stream.get('agent_type') or getattr(args, 'agent_type', '') or 'self',
                                    )
                                    sm, _, st = _native_swarm_worker_model(
                                        p_prefix, p_info, stream['kind'], eff_swarm_tier,
                                        parent_model=p_inh or '',
                                    )
                                    if sm:
                                        core['model'] = sm
                                        core['engine'] = p_prefix + ':' + sm
                                        core['tier'] = st or tier
                                        profile_core['model'] = sm
                                        profile_core['spec'] = p_prefix + ':' + sm
                            continue
                        locked_model_target = False
                        if 'engine' in override:
                            sub_clean = stream.get('subtask', '') if not _extract_prompt_model_hint(stream.get('subtask', '')) else ''
                            engine = choose_engine(backends, args.caller, stream['requested_engine'], sub_clean, '',
                                                   ignore_cooldown=getattr(args, 'retry_quota', False))
                            locked_model_target = True
                        else:
                            orig_prefix = _engine_parts(orig_stream['engine'])[0]
                            orig_model = orig_stream.get('model') or ''
                            orig_tier = orig_stream.get('tier') or 'max'
                            orig_prof_src = (orig_stream.get('execution_profile') or {}).get('profile_source')
                            orig_check_text = args.user_prompt or orig_stream.get('subtask', '')
                            if orig_prof_src and orig_prof_src != 'prompt_fuzzy_model' and _extract_prompt_model_hint(orig_check_text):
                                orig_check_text = orig_stream.get('subtask', '') if not _extract_prompt_model_hint(orig_stream.get('subtask', '')) else ''
                            still_current = False
                            if orig_model:
                                try:
                                    orig_check = resolve_execution_profile(
                                        backends, orig_stream['engine'], orig_tier, orig_check_text,
                                        orig_stream['kind'], estimated_tokens=orig_stream.get('estimated_tokens', 0),
                                    )
                                    still_current = (
                                        not orig_check.get('execution_blocked_reason')
                                        and orig_check.get('model') == orig_model
                                    )
                                except HarnessError:
                                    still_current = False
                            if not still_current:
                                sub_sample = stream.get('subtask', '')
                                tier, _ = select_task_tier(
                                    stream['tier'], stream['kind'],
                                    '' if sub_sample == args.user_prompt else sub_sample,
                                    stream.get('file_count', 1), stream.get('estimated_tokens', 0), engine=orig_stream['engine'],
                                )
                                core['tier'] = tier
                                continue
                            cand_family = _agy_slug_tier(orig_model)[0] if orig_prefix == 'agy' else ''
                            if cand_family and any(
                                _agy_slug_tier(m.get('slug'))[0] == cand_family
                                for m in backends.get('agy', {}).get('models', [])
                            ):
                                engine = f'agy:{cand_family}'
                            else:
                                # 只改挡位且未发生目录漂移时锁定原具体模型并同步更新 core。
                                engine = orig_prefix + ':' + (orig_model or 'default')
                            locked_model_target = True
                        if 'tier' in override:
                            sub_sample = stream.get('subtask', '')
                            tier, _ = select_task_tier(
                                stream['tier'], stream['kind'],
                                '' if sub_sample == args.user_prompt else sub_sample,
                                stream.get('file_count', 1), stream.get('estimated_tokens', 0), engine=engine,
                            )
                        else:
                            eff_user_tier = '' if 'engine' in override else args.user_prompt
                            eff_sub_tier = (
                                (stream.get('subtask', '') if not _extract_prompt_model_hint(stream.get('subtask', '')) else '')
                                if 'engine' in override else stream.get('subtask', '')
                            )
                            tier, _ = _resolve_effective_tier(stream['tier'], stream['kind'], eff_user_tier, eff_sub_tier, engine)
                        prof_text = (
                            (stream.get('subtask', '') if not _extract_prompt_model_hint(stream.get('subtask', '')) else '')
                            if locked_model_target
                            else (args.user_prompt or stream.get('subtask', ''))
                        )
                        profile = resolve_execution_profile(
                            backends, engine, tier, prof_text, stream['kind'],
                            estimated_tokens=stream.get('estimated_tokens', 0),
                        )
                        _require_execution_profile(profile)
                        ov_prefix, _ = _engine_parts(engine)
                        ov_quota = inspect_backend_quota_and_availability(
                            ov_prefix, backends[ov_prefix], profile['model'],
                            redact_usage=getattr(args, 'redact', False),
                            ignore_cooldown=getattr(args, 'retry_quota', False),
                        )
                        core.update(engine=engine, model=profile['model'], tier=profile['tier'],
                                    context_window=profile['capacity']['selected_window_tokens'],
                                    currently_available=ov_quota['currently_available'],
                                    unavailable_reason=ov_quota['unavailable_reason'],
                                    quota_exceeded=ov_quota['quota_exceeded'])
                        profile_core.update({key: profile.get(key) for key in profile_fields})
                has_engine_override = any(isinstance(ov, dict) and 'engine' in ov for ov in overrides.values())
                try:
                    streams, switched = _resolve_plan_streams(args, candidate_streams, backends)
                except HarnessError:
                    ticket['streams'] = _clean_plan_streams(orig_streams)
                    if not args.approve_ticket or has_engine_override:
                        raise
                    streams = [dict(stream) for stream in ticket['streams']]
                    for stream in streams:
                        prefix, _ = _engine_parts(stream['engine'])
                        stream.update(inspect_backend_quota_and_availability(
                            prefix, backends[prefix], stream.get('model'),
                            redact_usage=getattr(args, 'redact', False),
                            ignore_cooldown=getattr(args, 'retry_quota', False),
                        ))
                    orig_expected = [{key: s.get(key) for key in core_fields} for s in orig_streams]
                    if all(all(stream.get(key) == value for key, value in core.items())
                           for stream, core in zip(streams, orig_expected)):
                        raise
                    switched = True
                drift = switched or any(any(stream.get(key) != value for key, value in core.items())
                                        for stream, core in zip(streams, expected)) or any(
                    any(stream.get('execution_profile', {}).get(key) != value for key, value in core.items())
                    for stream, core in zip(streams, expected_profiles))
                if args.approve_ticket and drift:
                    if ticket.get('status') == 'APPROVED' and not forced:
                        unavail_streams = [stream for stream in streams if not stream.get('currently_available')]
                        if unavail_streams:
                            unavail_desc = '；'.join(
                                f"{s.get('stream_id', '?')}[{s.get('channel', 'cae_harness_headless')}]"
                                f"({s.get('unavailable_reason') or 'unavailable'})"
                                for s in unavail_streams
                            )
                            raise HarnessError(
                                f'二次检查门拒绝批准不可用的工作流：{unavail_desc}',
                                'quota_exhausted' if any(s.get('quota_exceeded') for s in unavail_streams) else 'failed',
                                unavailable_streams=[
                                    {
                                        'stream_id': s.get('stream_id'),
                                        'channel': s.get('channel', 'cae_harness_headless'),
                                        'client': s.get('client'),
                                        'unavailable_reason': s.get('unavailable_reason'),
                                    }
                                    for s in unavail_streams
                                ],
                            )
                        raise HarnessError('已批准票据在通道漂移时不可静默降级为待决状态，如需重置请传 --force', 'ticket_invalid')
                    ticket.update(streams=streams, status='PENDING', is_heavy=_is_heavy_streams(streams, switched))
                    _save_plan(ticket, plan_dir)
                    raise HarnessError('确认期间通道可用性或选路发生变化，已更新确认单，请重新向用户展示确认', 'ticket_changed')
                if args.approve_ticket:
                    unavail_streams = [stream for stream in streams if not stream.get('currently_available')]
                    if unavail_streams:
                        unavail_desc = '；'.join(
                            f"{s.get('stream_id', '?')}[{s.get('channel', 'cae_harness_headless')}]"
                            f"({s.get('unavailable_reason') or 'unavailable'})"
                            for s in unavail_streams
                        )
                        raise HarnessError(
                            f'二次检查门拒绝批准不可用的工作流：{unavail_desc}',
                            'quota_exhausted' if any(s.get('quota_exceeded') for s in unavail_streams) else 'failed',
                            unavailable_streams=[
                                {
                                    'stream_id': s.get('stream_id'),
                                    'channel': s.get('channel', 'cae_harness_headless'),
                                    'client': s.get('client'),
                                    'unavailable_reason': s.get('unavailable_reason'),
                                }
                                for s in unavail_streams
                            ],
                        )
                changed = orig_streams != streams
                if ticket.get('status') == 'APPROVED' and not args.approve_ticket and not forced:
                    spec_fields = ('engine', 'model', 'tier', 'timeout', 'channel', 'context_window')
                    spec_drifted = switched or any(
                        any(stream.get(key) != core.get(key) for key in spec_fields)
                        for stream, core in zip(streams, expected)
                    ) or any(
                        any(stream.get('execution_profile', {}).get(key) != pcore.get(key) for key in profile_fields)
                        for stream, pcore in zip(streams, expected_profiles)
                    )
                    if spec_drifted:
                        raise HarnessError(
                            '已批准票据在恢复复核期间发生引擎、模型或挡位漂移，拒绝静默改写；如需重置为待确认请传 --force',
                            'ticket_changed',
                        )
                    # 仅刷新实时可用性/额度视图供展示，绝不改写磁盘已批票据与 plan_hash。
                    ticket = copy.deepcopy(ticket)
                    ticket['streams'] = streams
                    _render_plan(ticket)
                elif ticket.get('status') in ('CANCELLED', 'SUPERSEDED') and not forced:
                    # 终态票据在只读复核时仅刷新内存展示视图，不静默复活为 PENDING 或覆盖会话指针。
                    ticket = copy.deepcopy(ticket)
                    ticket['streams'] = streams
                    _render_plan(ticket)
                else:
                    ticket['streams'] = streams
                    ticket['is_heavy'] = _is_heavy_streams(streams, switched)
                    if args.approve_ticket:
                        for stream in streams:
                            ep_tier = (stream.get('execution_profile') or {}).get('tier')
                            if ep_tier:
                                stream['tier'] = ep_tier
                        ticket['status'] = 'APPROVED'
                    elif ticket.get('status') == 'APPROVED':
                        if forced and changed:
                            ticket['status'] = 'PENDING'
                    elif ticket.get('status') != 'HANDED_OFF' and changed:
                        ticket['status'] = 'PENDING'
                    _save_plan(ticket, plan_dir)
                    if args.approve_ticket:
                        superseded_ids = _supersede_overlapping_pending_tickets(ticket, plan_dir)
                        if superseded_ids:
                            ticket['superseded_ticket_ids'] = superseded_ids
            if args.mark_handed_off:
                _save_plan(ticket, plan_dir)
    else:
        streams = _read_stream_spec(args.spec_file) if args.spec_file else [{
            'stream_id': 'S1', 'subtask': args.workflow_goal or args.user_prompt or '检查目标',
            'target': args.target, 'kind': args.kind, 'engine': args.engine, 'tier': args.tier, 'timeout': args.timeout,
            'channel': args.channel, 'agent_type': args.agent_type, 'model': args.model}]
        streams, switched = _resolve_plan_streams(args, streams, _args_backends(args, refresh_model_cache=False))
        ticket = {'ticket_id': uuid.uuid4().hex, 'status': 'PENDING', 'created_at': time.time(),
                  'expires_at': time.time() + _confirmation_tunables().get('ticket_ttl_sec', 86400),
                  'session_id': _session_id(args), 'workflow_bg': args.workflow_bg,
                  'user_prompt': args.user_prompt,
                  'workflow_goal': args.workflow_goal, 'workflow_impact': args.workflow_impact,
                  'streams': streams, 'is_heavy': _is_heavy_streams(streams, switched),
                  'capability_ledger': _fleet_profile().get('capability_ledger', {})}
        if getattr(args, 'obligations_file', ''):
            raw_ob = _read_json(Path(args.obligations_file).expanduser())
            raw_ob_list = raw_ob.get('obligations') if isinstance(raw_ob, dict) else raw_ob
            ticket['obligations'] = _normalize_obligations(raw_ob_list)
        baseline_heads = {}
        for _st in streams:
            try:
                _tp = Path(str(_st.get('target') or '')).expanduser().resolve()
                _rt = _tp if _tp.is_dir() else _tp.parent
                if _rt.is_dir() and str(_rt) not in baseline_heads:
                    _hd = _git_head(_rt)
                    if _hd:
                        baseline_heads[str(_rt)] = _hd
            except OSError:
                continue
        if baseline_heads:
            ticket['baseline_heads'] = baseline_heads
        ticket['plan_hash'] = _plan_hash(ticket)
        _render_plan(ticket)
        if ticket['is_heavy'] or args.save_ticket:
            with _plan_dir_lock(plan_dir), _directory_file_lock(plan_dir, f'ticket_{ticket["ticket_id"]}', label='计划票据'):
                _save_plan(ticket, plan_dir)
    if args.output:
        _atomic_json(args.output, ticket if args.no_redact_output else _redact_public(ticket))
    stdout_payload = (
        _compact_plan_stdout_view(ticket)
        if ticket.get('card_detail_file') and not getattr(args, 'full_json', False)
        else ticket
    )
    _print(_redact_public(stdout_payload) if args.redact else stdout_payload)
    return 0


def cmd_batch(args):
    _begin_dispatch(args)
    backends = _args_backends(args, refresh_model_cache=False)
    if getattr(args, '_approved_ticket', None):
        all_streams = args._approved_ticket['streams']
        args.user_prompt = args.user_prompt or args._approved_ticket.get('user_prompt', '')
    elif args.spec_file:
        all_streams, _ = _resolve_plan_streams(args, _read_stream_spec(args.spec_file), backends)
    else:
        raise HarnessError('batch 需要 --spec-file 或已批准的 --ticket')
    streams = [s for s in all_streams if s.get('channel', 'cae_harness_headless') == 'cae_harness_headless']
    native_streams = [s for s in all_streams if s.get('channel', 'cae_harness_headless') != 'cae_harness_headless']
    if not streams:
        raise HarnessError('原生通道请在 caller 客户端内部执行，batch 仅执行 cae_harness_headless 通道')
    caller_label = resolve_caller(args.caller)
    limits = _headless_tunables().get('batch_max_workers_per_backend', {})
    blocked, lock = set(), threading.Lock()
    ws_cond, active_ws = threading.Condition(), []

    def _ws_conflicts(r1, s1, r2, s2):
        if r1 == r2 and s1 and s2:
            fold = os.name == 'nt' or r1.as_posix().startswith('/mnt/')
            def stem(pattern):
                return re.split(r'[*?\[]', pattern, 1)[0].rpartition('/')[0] if re.search(r'[*?\[]', pattern) else pattern
            return any(not a or not b or _scope_matches(a, [b], fold) or _scope_matches(b, [a], fold)
                       for a, b in itertools.product(map(stem, s1), map(stem, s2)))
        return (s1 is not None or s2 is not None) and (r1 == r2 or r1 in r2.parents or r2 in r1.parents)

    @contextlib.contextmanager
    def _workspace_guard(root, scopes):
        peer_scopes = []
        with ws_cond:
            while any(_ws_conflicts(root, scopes, ar, scope) for ar, scope, _ in active_ws):
                ws_cond.wait()
            for peer_root, scope, peers in active_ws:
                if root == peer_root and scopes and scope:
                    peers.extend(scopes)
                    peer_scopes.extend(scope)
            token = (root, scopes, peer_scopes)
            active_ws.append(token)
        try:
            yield peer_scopes
        finally:
            with ws_cond:
                active_ws.remove(token)
                ws_cond.notify_all()

    quota_batch, failed_roots = _QuotaBatch(args.retry_quota), set()
    def run(stream):
        started = time.monotonic()
        engine = stream['engine']
        prefix, _ = _engine_parts(engine)
        invocation = {}
        result = {'stream_id': stream['stream_id'], 'engine': engine, 'wave': stream.get('wave', 1),
                  'invocation': invocation, 'status': 'failed'}
        try:
            target = Path(stream['target']).resolve()
            root = target if target.is_dir() else target.parent
            readonly = stream['kind'] not in ('code', 'gate')
            allow_on_upstream_fail = bool(readonly and stream.get('run_on_upstream_failure', False))
            if not allow_on_upstream_fail and any(
                root == fr or root in fr.parents or fr in root.parents for fr in failed_roots
            ):
                raise HarnessError(
                    '同一工作区前序波次的可写任务失败（若只读流确需独立执行请显式声明 run_on_upstream_failure=true）',
                    'upstream_wave_failed',
                )
            _check_writable_transport(root, backends[prefix], readonly)
            if stream['kind'] == 'gate' and not (stream.get('verify_cmd') or '').strip():
                raise HarnessError('批处理 gate 任务必须声明非空 verify_cmd')
            with lock:
                if prefix in blocked:
                    raise HarnessError('同后端前序任务已触发额度熔断，停止排队流', 'quota_exhausted', queue_aborted=True)
            quota = inspect_backend_quota_and_availability(prefix, backends[prefix], (stream.get('execution_profile') or {}).get('model'),
                                                           ignore_cooldown=args.retry_quota)
            if not quota['currently_available']:
                if quota['quota_exceeded']:
                    with lock:
                        blocked.add(prefix)
                raise HarnessError('批处理二次检查门拒绝执行', quota['unavailable_reason'], preflight_blocked=True)
            text = args.user_prompt or stream.get('subtask', '')
            if getattr(args, '_approved_ticket', None):
                if getattr(args, 'ephemeral', False) and not stream.get('ephemeral'):
                    raise HarnessError('会话持久化选项与获批票据不一致', 'ticket_invalid')
                cli_iso = _normalize_isolation(getattr(args, 'isolation', ''), clean_room=bool(getattr(args, 'clean_room', False)))
                if cli_iso:
                    approved_iso, _ = _effective_isolation(prefix, stream.get('isolation', ''), clean_room=bool(stream.get('clean_room', False)))
                    if cli_iso != approved_iso:
                        raise HarnessError(f'隔离模式（{cli_iso}）与获批票据（{approved_iso}）不一致', 'ticket_invalid')
                tier = stream['tier']
                if stream.get('execution_profile', {}).get('profile_source') != 'prompt_fuzzy_model' and _extract_prompt_model_hint(text):
                    text = stream.get('subtask', '') if not _extract_prompt_model_hint(stream.get('subtask', '')) else ''
            else:
                tier, _ = _resolve_effective_tier(
                    stream['tier'], stream['kind'], args.user_prompt, text, engine,
                    file_count=stream.get('file_count', 1), estimated_tokens=stream.get('estimated_tokens', 0),
                )
            profile = resolve_execution_profile(
                backends, engine, tier, text, stream['kind'],
                estimated_tokens=stream.get('estimated_tokens', 0),
            )
            approved_prof = stream.get('execution_profile') or {}
            if (
                getattr(args, '_approved_ticket', None)
                and profile['spec'] != approved_prof.get('spec')
                and approved_prof.get('profile_source') != 'prompt_fuzzy_model'
            ):
                sub_only = stream.get('subtask', '')
                if _extract_prompt_model_hint(sub_only):
                    sub_only = ''
                if sub_only != text:
                    alt_profile = resolve_execution_profile(
                        backends, engine, tier, sub_only, stream['kind'],
                        estimated_tokens=stream.get('estimated_tokens', 0),
                    )
                    if not alt_profile.get('execution_blocked_reason'):
                        profile = alt_profile
            _require_execution_profile(profile)
            if getattr(args, '_approved_ticket', None) and (
                profile['spec'] != approved_prof.get('spec')
                or profile.get('cli_effort') != approved_prof.get('cli_effort')
            ):
                raise HarnessError('实际模型与获批计划不一致，请重新确认票据', 'ticket_invalid')
            result['model'] = profile['model']
            if not target.exists():
                raise HarnessError(f'工作流目标不存在: {target}')
            if args.dry_run:
                result.update(status='ok', dry_run=True)
                return result
            scopes = _scope_patterns(stream.get('write_scope', []))
            if not readonly and not scopes:
                raise HarnessError('批处理编码任务必须声明 write_scope')
            files, negative, contract = _input_envelope(args, target, stream)
            if files:
                result['planned_files'] = list(files)
            with _workspace_guard(root, None if readonly else scopes) as peer_scopes:
                snap_ignored = None if readonly else _snapshot_ignore_dirs_for(
                    scopes, include_build=bool((stream.get('verify_cmd') or '').strip())
                )
                snap_patterns = read_agentignore_patterns(root)
                before = snapshot_files(root, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns)
                try:
                    batch_prompt = _task_prompt(stream['kind'], caller_label, target, readonly, scopes,
                                                stream.get('subtask', ''), _input_contract(files, negative, contract))
                    result['result'] = invoke_engine(profile['spec'], root, batch_prompt, backends,
                                                     TASK_JSON_SCHEMA, readonly, stream['timeout'],
                                                     agent=stream.get('agent_type') or 'readonly-collaborator',
                                                     stage_files=files or None, ephemeral=stream.get('ephemeral', False),
                                                     plan_dir=getattr(args, 'plan_dir', ''), write_scope=scopes,
                                                     effort=profile.get('cli_effort'),
                                                     retry_quota=args.retry_quota,
                                                     profile=profile, receipt=invocation, batch=quota_batch,
                                                     clean_room=stream.get('clean_room', False),
                                                     isolation=stream.get('isolation', ''),
                                                     inline_readonly=stream.get('inline_readonly', args.inline_readonly))
                    result['status'] = 'ok'
                finally:
                    scope = _safe_changes_report(
                        before, root, scopes, readonly, ignore_dirs=snap_ignored, ignore_patterns=snap_patterns
                    )
                    staged_scope = invocation.pop('staged_scope', None)
                    if isinstance(staged_scope, dict):
                        scope = _merge_staged_scope_report(scope, staged_scope)
                    fold = os.name == 'nt' or root.as_posix().startswith('/mnt/')
                    peer_masked = [p for p in (scope.get('scope_violations') or []) if peer_scopes and _scope_matches(p, peer_scopes, fold)]
                    for field in ('changed_files', 'scope_violations'):
                        if peer_scopes and scope[field] is not None:
                            scope[field] = [p for p in scope[field] if not _scope_matches(p, peer_scopes, fold)]
                    if peer_masked:
                        result['_peer_masked'] = (str(root), fold, peer_masked)
                    result.update(scope)
                    if scope.get('scope_violations'):
                        result['status'] = 'scope_violation'
                    elif not scope.get('scope_verified'):
                        result['status'] = 'failed'
                    elif result['status'] == 'ok' and stream['kind'] == 'code' and not scope.get('changed_files'):
                        result['no_files_changed'] = True
                        result['no_files_changed_notice'] = '可写编码流退出码为 0 但磁盘零改动，请核查是否因门禁或前置条件停手。'
                if (
                    result['status'] == 'ok'
                    and stream['kind'] == 'polish'
                    and isinstance(result.get('result'), dict)
                    and isinstance(result['result'].get('content'), str)
                ):
                    result['grounding_audit'] = _compute_polish_grounding_audit(
                        target, result['result']['content'], stream.get('subtask', '')
                    )
                if result['status'] == 'ok' and not readonly and stream.get('verify_cmd'):
                    verify_env = {
                        k: v for k, v in os.environ.items()
                        if k not in _GIT_REPO_ENV_VARS and k not in _CHILD_SCRUB_SESSION_ENV_VARS
                    }
                    verify_env['CAE_HARNESS_DEPTH'] = '1'
                    verification = run_process(
                        stream['verify_cmd'], root, timeout_sec=stream['timeout'], shell=True, env=verify_env, check=False,
                    )
                    result['verification_executed'] = True
                    result['verify_exit_code'] = verification.returncode
                    result['verify_output_tail'] = ((verification.stdout or '') + '\n' + (verification.stderr or '')).strip()[-2000:]
                    if verification.returncode:
                        result['status'] = 'verification_failed'
        except Exception as exc:
            status = getattr(exc, 'status', 'failed')
            details = getattr(exc, 'details', {})
            if result['status'] != 'scope_violation':
                result['status'] = status
            result.update(error=str(exc), **details)
            if status == 'quota_exhausted':
                with lock:
                    blocked.add(prefix)
                if (not args.dry_run and not details.get('quota_cooldown')
                        and not details.get('preflight_blocked') and not details.get('queue_aborted')):
                    _record_quota_exhausted(prefix, backends[prefix], str(exc), batch=quota_batch)
        finally:
            result['succeeded'] = result['status'] == 'ok'
            result['elapsed_sec'] = round(time.monotonic() - started, 6)
            result['token_usage'] = invocation.get('token_usage') or extract_cli_token_usage(prefix, '')
        return result
    if not args.dry_run:
        primary_root = None
        for _st in streams:
            try:
                _tp = Path(str(_st.get('target') or '')).expanduser().resolve()
                if _tp.exists():
                    primary_root = _tp if _tp.is_dir() else _tp.parent
                    break
            except OSError:
                continue
        if primary_root is not None:
            args._reconcile_target_root = primary_root
            args._reconcile_baseline = capture_reconcile_baseline(
                primary_root, ignore_patterns=read_agentignore_patterns(primary_root),
                capture_snapshot=False,
            )
    waves = {}
    for index, stream in enumerate(streams):
        groups = waves.setdefault(stream.get('wave', 1), {})
        groups.setdefault(_engine_parts(stream['engine'])[0], []).append((index, stream))
    def run_backend(item):
        prefix, group = item
        with concurrent.futures.ThreadPoolExecutor(max_workers=_positive_int(limits.get(prefix)) or 2) as pool:
            return list(zip((index for index, _ in group), pool.map(run, (stream for _, stream in group))))
    results = [None] * len(streams)
    for _, groups in sorted(waves.items()):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(groups)) as pool:
            for group in pool.map(run_backend, groups.items()):
                for index, result in group:
                    results[index] = result
        wave_items = list(itertools.chain.from_iterable(groups.values()))
        claimed = {}
        for index, stream in wave_items:
            if stream['kind'] in ('code', 'gate') and results[index].get('changed_files'):
                tgt = Path(stream['target']).resolve()
                claimed.setdefault(str(tgt if tgt.is_dir() else tgt.parent), set()).update(results[index]['changed_files'])
        for index, stream in wave_items:
            masked = results[index].pop('_peer_masked', None)
            if masked:
                rkey, fold, paths = masked
                unclaimed = [p for p in paths if not _scope_matches(p, claimed.get(rkey, ()), fold)]
                if unclaimed:
                    results[index]['changed_files'] = sorted(set(results[index].get('changed_files') or ()) | set(unclaimed))
                    results[index]['scope_violations'] = sorted(set(results[index].get('scope_violations') or ()) | set(unclaimed))
                    results[index].pop('no_files_changed', None)
                    results[index].pop('no_files_changed_notice', None)
                    results[index].update(status='scope_violation', succeeded=False)
            if not results[index]['succeeded'] and stream['kind'] in ('code', 'gate'):
                target = Path(stream['target']).resolve()
                failed_roots.add(target if target.is_dir() else target.parent)
    out_payload = {'status': 'ok' if all(s['succeeded'] for s in results) else 'partial', 'streams': results}
    if native_streams:
        out_payload['skipped_native_streams'] = [
            {
                'stream_id': s.get('stream_id'),
                'channel': s.get('channel'),
                'client': s.get('client'),
                'agent_type': s.get('agent_type'),
                'note': '原生通道须由 caller 客户端内部按获批票据执行，batch 已跳过',
            }
            for s in native_streams
        ]
    return _dispatch_print(args, out_payload)


def _positive_arg(raw):
    value = _positive_int(raw)
    if value is None:
        raise argparse.ArgumentTypeError('必须为正整数')
    return value


def _unredact_local_path(raw):
    text = str(raw or '').replace('<HOME>', str(Path.home())).replace('<TEMP>', tempfile.gettempdir())
    return Path(text) if text else Path()


def cmd_followup(args):
    _begin_dispatch(args)
    source = _read_json(_unredact_local_path(args.receipt).expanduser())
    if not isinstance(source, dict):
        raise HarnessError('无法读取原会话回执')
    candidates = []
    for stream in _result_streams(source):
        default_id = f'{stream["side"]}:{stream.get("shard_id")}' if 'side' in stream else f'S{stream.get("round", 1)}'
        alt_id = f'{stream["side"]}-S{stream.get("shard_id")}' if 'side' in stream else ''
        sid = stream.get('stream_id') or default_id
        previous = {**stream, **stream.get('invocation', {})}
        if previous.get('provider_session_id') and (not args.stream or args.stream in (str(sid), alt_id)):
            candidates.append(previous)
    if len(candidates) != 1:
        raise HarnessError('回执中未找到唯一可续接会话，请核对 --stream 和 provider_session_id')
    previous = candidates[0]
    salvage = previous.get('timeout_salvage') if isinstance(previous.get('timeout_salvage'), dict) else None
    if salvage is None and len(_result_streams(source)) == 1 and isinstance(source.get('timeout_salvage'), dict):
        salvage = source.get('timeout_salvage')
    if isinstance(salvage, dict) and salvage.get('resumable') is False:
        raise HarnessError(
            salvage.get('resume_warning') or '原会话回执已标记为不可续接（timeout_salvage.resumable=false），请按分片新开会话',
            'session_not_resumable',
            provider_session_id=previous.get('provider_session_id'),
            resume_blocked_reason=salvage.get('resume_blocked_reason') or 'marked_not_resumable',
        )
    root = _unredact_local_path(previous.get('target_dir') or source.get('target') or '')
    readonly = previous.get('readonly', source.get('readonly'))
    engine = previous.get('engine') or source.get('engine')
    if not root.is_absolute() or not root.is_dir() or type(readonly) is not bool or not isinstance(engine, str):
        raise HarnessError('回执缺少有效的原工作目录、引擎或读写属性')
    root = root.resolve()
    scopes = _scope_patterns(previous.get('write_scope', source.get('write_scope', [])))
    ticket = getattr(args, '_approved_ticket', None)
    if ticket:
        matched = [
            s for s in ticket['streams']
            if s.get('channel', 'cae_harness_headless') == 'cae_harness_headless'
            and (Path(s['target']).resolve() == root or Path(s['target']).resolve().parent == root)
            and (s['kind'] not in ('code', 'gate')) == readonly
            and (readonly or _scope_subset_of(scopes, s.get('write_scope') or [], target_root=root))
            and args.timeout <= s['timeout']
        ]
        if not matched:
            raise HarnessError('续接目标、读写范围或超时与获批票据不一致', 'ticket_invalid')
    prompt = ('续接原会话，保持其工作目录和输入范围。' +
              ('仅作只读核对。' if readonly else f'允许写入范围 {scopes}。') +
              '\n' + _task_text(args) + '\n按给定 JSON Schema 返回结果。')
    prev_model = (
        previous.get('model')
        or previous.get('actual_model')
        or previous.get('model_requested_to_cli')
        or source.get('model')
    )
    prev_status = str(previous.get('status') or source.get('status') or '').strip().lower()
    was_timeout = prev_status == 'timeout' or bool(salvage)
    explicit_effort = str(getattr(args, 'effort', '') or getattr(args, 'tier', '') or '').strip().lower()
    prev_tier = str(previous.get('tier') or '').strip().lower()
    if explicit_effort and explicit_effort != 'auto':
        followup_tier = explicit_effort
    elif was_timeout and prev_tier in ('auto', 'max', 'xhigh', 'high', ''):
        followup_tier = str(_headless_tunables().get('followup_default_effort') or 'medium').strip().lower()
    else:
        followup_tier = previous.get('tier')
    invocation, payload = {}, {'target': str(root), 'engine': engine, 'model': prev_model, 'readonly': readonly}
    if args.dry_run:
        return _dispatch_print(args, {**payload, 'status': 'ok', 'dry_run': True, 'followup_tier': followup_tier,
                                      'changed_files': [], 'scope_violations': [], 'scope_verified': True, 'invocation': invocation})
    backends = _args_backends(args, refresh_model_cache=False)
    ignored = None if readonly else _snapshot_ignore_dirs_for(scopes)
    patterns = read_agentignore_patterns(root)
    before = snapshot_files(root, ignore_dirs=ignored, ignore_patterns=patterns)
    args._reconcile_target_root = root
    args._reconcile_baseline = capture_reconcile_baseline(
        root, ignore_dirs=ignored, ignore_patterns=patterns, pre_snapshot=before,
    )
    raw_session_plan_dir = str(previous.get('session_plan_dir') or '').strip()
    state = args.plan_dir or (str(_unredact_local_path(raw_session_plan_dir)) if raw_session_plan_dir else '')
    try:
        payload['result'] = invoke_engine(engine, root, prompt, backends, TASK_JSON_SCHEMA, readonly, args.timeout,
                                          previous.get('agent_type', 'readonly-collaborator'), previous.get('files'),
                                          followup_tier, retry_quota=args.retry_quota, receipt=invocation,
                                          isolation=previous.get('isolation', ''),
                                          resume=previous, plan_dir=state, write_scope=scopes, inline_readonly=False)
        payload['status'] = 'ok'
    except HarnessError as exc:
        payload.update(status=exc.status, error=str(exc), **exc.details)
    if invocation.get('actual_model') or invocation.get('model_requested_to_cli'):
        payload['model'] = invocation.get('actual_model') or invocation.get('model_requested_to_cli')
    scope = _safe_changes_report(before, root, scopes, readonly, ignore_dirs=ignored, ignore_patterns=patterns)
    if invocation.get('staged_scope'):
        scope = _merge_staged_scope_report(scope, invocation.pop('staged_scope'))
    if scope['scope_violations'] or not scope['scope_verified']:
        payload['status'] = 'scope_violation' if scope['scope_violations'] else 'failed'
    return _dispatch_print(args, {**payload, **scope, 'invocation': invocation})


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
    grounding.add_argument('--doc-hygiene', action='store_true', help='额外执行通用文档卫生检查：计数与结构一致、补丁长句与斜杠串、跨文档逐字复读及入口页清晰度')
    reconcile = sub.add_parser('reconcile', help='纯确定性离线机械对账表：核对实改与未更新代码/文档、强显式依赖与义务结算')
    reconcile.add_argument('--target', default='.', help='待对账的仓库或目录路径')
    reconcile.add_argument('--base-ref', default='', help='可选 Git 基线引用（如 HEAD 或提交哈希），用于独立点名核对工作区改动')
    reconcile.add_argument('--baseline-file', default='', help='由 --save-baseline 冻结的开工基线 JSON 路径')
    reconcile.add_argument('--save-baseline', default='', help='仅冻结当前工作区基线并写出至指定 JSON 路径，随后退出')
    reconcile.add_argument('--receipt', default='', help='可选派单回执 JSON 路径，将流终态、只读交付覆盖与引用核验并入对账表')
    reconcile.add_argument('--cross-check', default='', help='可选 cross-check 跨端汇总核对 JSON 路径，将共同盲区与分歧并入机械对账表')
    reconcile.add_argument('--obligations-file', default='', help='可选显式义务清单 JSON 文件路径（逐项核验 read/modify/review/test/final_audit）')
    reconcile.add_argument('--output-md', default='', help='显式指定机械对账 Markdown 输出路径（默认落盘至 plan-dir/reconcile/）')
    reconcile.add_argument('--plan-dir', default='', help='未传 --output-md 时用于存放机械对账表的状态目录路径')
    read_plan = sub.add_parser('read-plan', help='读前尺寸评估与保守层级分片规划：双口径防爆窗、超大单文件行段切分与多端波次编排')
    read_plan.add_argument('--target', default='.', help='待评估读取的目录或文件路径')
    read_plan.add_argument('--files', action='append', default=[], help='可选显式目标读取文件相对路径（可重复传入）')
    read_plan.add_argument('--shared-files', action='append', default=[], help='每片共享的核心裁定或接口规范文件相对路径（单独核扣内联与 Token 预算）')
    read_plan.add_argument('--core-files', action='append', default=[], help='高风险核心文件相对路径（在 risk-tiered 模式下优先安排跨端独立盲读）')
    read_plan.add_argument('--engines', action='append', default=[], help='参与评估与分片的引擎规格（可逗号分隔或重复传入，如 codex:@review,dsh:@review）')
    read_plan.add_argument('--cross-client-mode', choices=['single', 'risk-tiered', 'all-engines'], default='risk-tiered',
                           help='跨端读取策略：risk-tiered 核心片多端盲读其余分担（默认）；all-engines 全量每端独立读；single 单端分片')
    read_plan.add_argument('--shard-tokens', type=_positive_arg, help='可选覆写单片保守 Token 上限')
    read_plan.add_argument('--max-concurrency', type=_positive_arg, help='可选覆写单波次总并发上限（默认保守为 2 且受每端并发上限约束）')
    read_plan.add_argument('--context-used-tokens', type=int, default=0, help='当前主会话已占用的估算 Token 数，用于核算剩余安全预算')
    read_plan.add_argument('--include-dirs', action='append', default=[], help='显式纳入默认排除的归档或历史目录（如 历史探索）')
    read_plan.add_argument('--focus', default='', help='本轮只读评估关注焦点说明')
    read_plan.add_argument('--clean-room', action='store_true', help='为支持净室的后端在导出工作流中启用 clean_room 隔离')
    read_plan.add_argument('--emit-spec', default='', help='可选导出可直接交 plan --spec-file 审批的纯流数组 JSON 路径')
    read_plan.add_argument('--output-md', default='', help='显式指定阅读计划 Markdown 输出路径（默认落盘至 plan-dir/read_plans/）')
    read_plan.add_argument('--plan-dir', default='', help='阅读计划落盘状态目录路径')
    read_plan.add_argument('--session-id', default='', help='可选绑定当前会话标识以在压缩恢复时保留轻量阅读计划指针')
    cross_check = sub.add_parser('cross-check', help='跨端分片读取与独立汇总核对：覆盖矩阵、共同盲区、分歧保全与 R6 回源行号清单')
    cross_check.add_argument('--target', default='.', help='目标仓库根目录路径')
    cross_check.add_argument('--receipt', action='append', default=[], required=True, help='待汇总核对的派单回执 JSON 路径（支持重复传入多份）')
    cross_check.add_argument('--read-plan', default='', help='可选由 read-plan 生成的计划 JSON 路径，提供分母与跨片依赖边')
    cross_check.add_argument('--obligations-file', default='', help='可选显式义务清单 JSON 路径')
    cross_check.add_argument('--output-md', default='', help='显式指定跨端核对 Markdown 输出路径（默认落盘至 plan-dir/reconcile/）')
    cross_check.add_argument('--plan-dir', default='', help='跨端核对表落盘状态目录路径')
    plan = sub.add_parser('plan', help='纯离线生成工作流确认票据与交接提示')
    plan.add_argument('--target', default='.')
    plan.add_argument('--kind', default='review', choices=['review', 'math', 'code', 'gate', 'direction', 'polish'])
    plan.add_argument('--engine', default='')
    plan.add_argument('--channel', choices=list(_PLAN_CHANNEL_LABELS), default='cae_harness_headless')
    plan.add_argument('--agent-type', default='')
    plan.add_argument('--model', default='')
    plan.add_argument('--clean-room', action='store_true')
    plan.add_argument('--write-scope', action='append', default=[])
    plan.add_argument('--verify-cmd', default='')
    plan.add_argument('--receipt-location', default='')
    plan.add_argument('--spec-file', default='', help='多流工作流配置 JSON 文件路径')
    plan.add_argument('--obligations-file', default='', help='可选显式义务清单 JSON 文件路径（随票据落盘并在完工机械对账中逐项结算）')
    plan.add_argument('--workflow-bg', default='', help='工作流背景说明')
    plan.add_argument('--workflow-goal', default='', help='工作流目标说明')
    plan.add_argument('--workflow-impact', default='', help='预计后果与影响范围说明')
    plan.add_argument('--save-ticket', action='store_true', help='即使未达重负载阈值也强制保存票据文件')
    plan.add_argument('--allow-over-budget', action='store_true', help='允许显式只读文件估算超过目标模型声明上下文窗口')
    plan.add_argument('--full-json', action='store_true', help='标准输出保留完整票据 JSON（含 14 项长表与 execution_profile）；默认仅输出精简视图')
    actions = plan.add_mutually_exclusive_group()
    actions.add_argument('--approve-ticket', default='', help='按票据 ID 经二次可用性检查后登记为已批准状态')
    actions.add_argument('--resume-ticket', default='', help='按票据 ID 读取并刷新待决或已交接工作流状态')
    actions.add_argument('--mark-handed-off', default='', help='按票据 ID 将工作流标记为已交接状态')
    actions.add_argument('--cancel-ticket', default='', help='按票据 ID 取消待决或已交接工作流（已批准需加 --force）')
    plan.add_argument('--override-json', default='', help='配合 --approve-ticket 按 stream_id 微调 engine、tier 或 timeout 的 JSON 对象')
    plan.add_argument('--force', action='store_true', help='强制允许对已批准票据执行交接或状态重置')
    batch = sub.add_parser('batch', help='按后端限流执行独立工作流')
    batch.add_argument('--spec-file', default='', help='待执行的无头工作流列表 JSON 文件路径')
    batch.add_argument('--target', default='.', help='默认目标目录或文件路径')
    batch.add_argument('--kind', default='review', choices=['review', 'math', 'code', 'gate', 'direction', 'polish'],
                       help='默认子任务类型（当工作流条目未单独声明 kind 时生效）')
    batch.add_argument('--engine', default='', help='默认后端与模型规格')
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
    followup = sub.add_parser('followup', help='按既有回执续接指定会话')
    followup.add_argument('--receipt', required=True)
    followup.add_argument('--stream', default='')
    followup.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh', 'max'], default='',
                          help='显式覆盖续接挡位（超时回执未指定时默认按配置降挡防再次超时）')
    followup.add_argument('--tier', choices=list(TIER_CHOICES), default='',
                          help='续接挡位策略别名（显式指定时优先生效）')
    for command in (task, plan, batch):
        command.add_argument('--files', action='append', default=[])
        command.add_argument('--negative-scope-file', action='append', default=[])
    for command in (gate, task, followup):
        group = command.add_mutually_exclusive_group(required=True)
        group.add_argument('--task')
        group.add_argument('--task-file')
    for command in (gate, task):
        command.add_argument('--write-scope', action='append', default=[])
        command.add_argument('--max-rounds', type=_positive_arg, default=default_rounds)
    for command in (probe, assess, grounding, reconcile, read_plan, cross_check, audit, gate, task, plan, batch, followup):
        command.add_argument('--caller', choices=['auto', 'codex', 'antigravity', 'claude', 'cc', 'dsh'], default='auto')
        if command not in (grounding, reconcile, cross_check):
            command.add_argument('--codex-input-budget', type=_positive_arg)
            command.add_argument('--agy-input-budget', type=_positive_arg)
            command.add_argument('--claude-input-budget', type=_positive_arg)
            command.add_argument('--dsh-input-budget', type=_positive_arg)
    for command in (audit, gate, task, plan, batch):
        command.add_argument('--tier', choices=list(TIER_CHOICES), default='auto',
                             help='挡位策略：auto 默认按角色槽位或单模型非委派最高挡选路（短润色用 medium、长润色用 high）；仅当用户要求降挡／省额度时切换分级自适应')
        command.add_argument('--fallback-on-quota', action='store_true',
                              help='目标触发额度熔断且未修改工作区时，优先回退发起方，再选择其他已就绪且未冷却的端')
        command.add_argument('--ephemeral', action='store_true', help='关闭本次无头会话的续接')
    for command in (audit, gate, task, plan, batch, followup):
        command.add_argument('--retry-quota', action='store_true',
                             help='用户确认已充值或额度恢复后，忽略本地额度冷却记录重试发起')
        command.add_argument('--timeout', type=_positive_arg, default=default_timeout)
        command.add_argument('--dry-run', action='store_true')
    for command in (probe, assess, reconcile, read_plan, cross_check, audit, gate, task, plan, batch, followup):
        command.add_argument('--output', default='', help='将结构化结果写入指定 JSON 文件路径（默认自动脱敏）')
        command.add_argument('--no-redact-output', action='store_true', help='写入 --output 文件时保留未脱敏原始路径与统计字段')
        command.add_argument('--redact', action='store_true', help='对标准输出 JSON 启用路径、私网地址、用量比例与敏感键值脱敏')
    for command in (audit, gate, task, plan, batch, followup):
        command.add_argument('--user-prompt', default='', help='传入用户原话以锁定显式点名的后端、模型简称或挡位意图')
        command.add_argument('--session-id', default='', help='绑定当前会话标识符以关联待批票据与完工记录')
        command.add_argument('--plan-dir', default='', help='显式指定确认票据与运行回执状态目录路径')
        ticket_help = (
            '（误用拦截）plan 请改用 --approve-ticket / --resume-ticket / --mark-handed-off'
            if command is plan else '绑定已获批准的确认票据 ID 执行一致性核验'
        )
        command.add_argument('--ticket', default='', help=ticket_help)
    for command in (audit, gate, task, plan, batch):
        command.add_argument('--inline-readonly', action=argparse.BooleanOptionalAction, default=True,
                             help='为只读无头任务自动内联小文件带行号原文')
        command.add_argument('--isolation', choices=list(ISOLATION_CHOICES), default='',
                             help='隔离级别：full 保留规则/钩子/技能；no_skills 仅剥离全局技能与斜杠命令；hooks_only 保留安全钩子并剥离全局规则/技能（仅 codex/claude）；clean_room 等同 --clean-room（仅只读 codex/claude/dsh）')
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
    if os.environ.get('CAE_HARNESS_DEPTH') and args.subcommand not in ('probe', 'assess', 'check-grounding', 'reconcile', 'read-plan', 'cross-check'):
        sys.exit(_print({'status': 'failed', 'error': '子任务再次调用本编排器会形成递归派工，已拒绝'}))
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _cancel_signal)
    try:
        handler = {
            'probe': cmd_probe,
            'assess': cmd_assess,
            'check-grounding': cmd_check_grounding,
            'reconcile': cmd_reconcile,
            'read-plan': cmd_read_plan,
            'cross-check': cmd_cross_check,
            'audit': cmd_audit,
            'gate': cmd_gate,
            'task': cmd_task,
            'plan': cmd_plan,
            'batch': cmd_batch,
            'followup': cmd_followup,
        }[args.subcommand]
        code = handler(args)
    except HarnessError as exc:
        err_payload = {'status': exc.status, 'error': str(exc), 'caller': resolve_caller(args.caller), **exc.details}
        code = _print(_redact_public(err_payload) if getattr(args, 'redact', False) else err_payload)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        err_payload = {'status': 'failed', 'error': str(exc), 'caller': resolve_caller(args.caller)}
        code = _print(_redact_public(err_payload) if getattr(args, 'redact', False) else err_payload)
    sys.exit(code)


if __name__ == '__main__':
    main()
