# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_harness.config - 全局常量、回执 Schema、动态配置与跨平台路径/WSL 互操作工具。"""

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


def _deep_merge_local_overlay(base, overlay):
    merged = dict(base)
    for k, v in overlay.items():
        if k == 'schema_version':
            continue
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            sub = _deep_merge_local_overlay(merged[k], v)
            if k == 'hook_safety':
                base_hs = base.get('hook_safety') if isinstance(base.get('hook_safety'), dict) else {}
                for bound_k in ('max_rss_mib', 'wall_timeout_sec'):
                    if bound_k in base_hs and bound_k in sub:
                        try:
                            if float(sub[bound_k]) > float(base_hs[bound_k]):
                                sub[bound_k] = base_hs[bound_k]
                        except (TypeError, ValueError):
                            sub[bound_k] = base_hs[bound_k]
            merged[k] = sub
        else:
            merged[k] = v
    return merged


def _fleet_profile_local_path(profile_path):
    env_local = os.environ.get('CAE_FLEET_PROFILE_LOCAL', '').strip()
    if env_local:
        cand = Path(env_local).expanduser().resolve()
        return cand if (cand.is_file() and not _is_link(cand)) else None
    if os.environ.get('CAE_FLEET_PROFILE', '').strip():
        return None
    sibling = profile_path.with_name('fleet_profile.local.json')
    return sibling if (sibling.is_file() and not _is_link(sibling)) else None


def _fleet_profile():
    path = _fleet_profile_path()
    try:
        st = path.stat()
        local_path = _fleet_profile_local_path(path)
        local_key = None
        if local_path is not None:
            lst = local_path.stat()
            local_key = (str(local_path), lst.st_mtime_ns, lst.st_size)
        key = (str(path), st.st_mtime_ns, st.st_size, local_key)
        if _FLEET_PROFILE_CACHE['key'] == key and _FLEET_PROFILE_CACHE['data'] is not None:
            return _FLEET_PROFILE_CACHE['data']
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(raw, dict) or raw.get('schema_version') != 1:
            raise ValueError('invalid fleet_profile schema_version')
        if local_path is not None:
            try:
                ov = json.loads(local_path.read_text(encoding='utf-8-sig'))
                if isinstance(ov, dict):
                    raw = _deep_merge_local_overlay(raw, ov)
            except (OSError, ValueError, RecursionError):
                pass
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
    is_posix = _is_posix()

    def _codex_standalone(user_home, *, windows):
        exe = 'codex.exe' if windows else 'codex'
        return (
            user_home / '.local' / 'share' / 'codex-package' / 'bin' / exe,
            user_home / '.local' / 'bin' / exe,
            user_home / '.cargo' / 'bin' / exe,
        )

    if name == 'codex':
        for standalone in _codex_standalone(home, windows=not is_posix):
            if standalone.is_file():
                return str(standalone)

    found = shutil.which(name)
    if found and is_posix and re.match(r'^/mnt/[a-z]/', Path(found).as_posix().lower()):
        found = None
    if found and Path(found).suffix.lower() not in ('.cmd', '.bat'):
        if name == 'codex' and not is_posix:
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
    candidates = [home / '.gemini' / 'bin' / ('agy' if is_posix else 'agy.exe')] if name == 'agy' else []
    if name == 'codex' and not is_posix:
        local_app = Path(os.environ.get('LOCALAPPDATA') or (home / 'AppData' / 'Local'))
        pkg_cli = _find_windows_codex_package_cli() if not os.environ.get('CAE_CODEX_HOME') else None
        candidates.extend(_rank_codex_candidates([*_sorted_codex_binaries(local_app / 'OpenAI' / 'Codex' / 'bin'), pkg_cli]))
    candidates.append(home / '.local' / 'bin' / (name if is_posix else name + '.exe'))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    if name == 'codex' and is_posix and shutil.which('wslpath'):
        win_desktop_bins = []
        if shutil.which('cmd.exe'):
            try:
                win_profile = _windows_env('USERPROFILE')
                if win_profile:
                    win_home = Path(_wsl_path(win_profile, windows=False))
                    for standalone in _codex_standalone(win_home, windows=True):
                        if standalone.is_file():
                            return str(standalone)
            except (HarnessError, OSError):
                pass
            try:
                local_app = Path(_wsl_path(_windows_env('LOCALAPPDATA'), windows=False))
                win_desktop_bins = _sorted_codex_binaries(local_app / 'OpenAI' / 'Codex' / 'bin')
            except (HarnessError, OSError):
                pass
        found_exe = shutil.which('codex.exe')
        found_norm = Path(found_exe).as_posix().lower() if found_exe else ''
        is_alias_stub = '/microsoft/windowsapps/' in found_norm
        in_managed = ('/windowsapps/' in found_norm) or ('/openai/codex/bin/' in found_norm)
        if found_exe and not in_managed:
            return found_exe
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


