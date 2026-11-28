# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_harness.grounding - 零黑名单确定性事实锚定与通用文档卫生校验引擎。"""

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
    'axiom_gate.py', '$axiom_gate', '${axiom_gate}', '%axiom_gate%', '_axiom_lib',
    'fleet_state.py', 'ccswitch_admin.py', 'router.py',
    'handoff_gate.py', 'status_guard.py', 'reply_repeat_guard.py',
    'user_words.py', 'session_context_loader.py',
    'update-cc-switch-wsl.sh', 'launch-cc-switch-wsl.sh',
)

_GROUNDING_ANCHOR_EXTS = frozenset({
    '.py', '.f90', '.f95', '.f03', '.f08', '.f', '.for', '.c', '.cpp', '.cc', '.cxx', '.cu', '.cuh',
    '.h', '.hpp', '.hh', '.jl', '.rs', '.go', '.ts', '.js', '.cmake', '.toml', '.yaml', '.yml', '.json',
    '.sh', '.bash', '.ps1', '.cfg', '.ini', '.lean', '.bib',
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


_GROUNDING_EXTRA_EXCLUDED_DIRS = frozenset({
    '.runtime', '.superpowers', '归档', '历史探索',
})
_GROUNDING_EXCLUDED_PREFIXES = ('archive_', 'outdated_', '归档_')


def _grounding_excluded_dirs():
    hd = _headless_tunables()
    base = {
        str(d).strip().lower()
        for d in hd.get('grounding_excluded_dirs', ('.micromamba', '.toolchain', 'conda-meta', 'archive', 'archived', 'outdated'))
        if str(d).strip()
    }
    return base | {d.lower() for d in _GROUNDING_EXTRA_EXCLUDED_DIRS}


def _is_grounding_excluded_dir(name, dpath, target):
    if name in IGNORED_DIRS or _is_link(dpath / name):
        return True
    if target is not None and target.is_relative_to(dpath / name):
        return False
    low = name.lower()
    return low in _grounding_excluded_dirs() or low.startswith(_GROUNDING_EXCLUDED_PREFIXES)


def _build_grounding_anchor(root_dir, exclude_rels, allow_files=None, extra_allowed_text='', *, target=None):
    root_dir = Path(root_dir).resolve()
    hd = _headless_tunables()
    cap = (_positive_int(hd.get('grounding_max_anchor_file_mib')) or 2) * 1024 ** 2
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
        dirs[:] = sorted(d for d in dirs if not _is_grounding_excluded_dir(d, dpath, target))
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


_TEX_SKIP_ENVS = frozenset({
    'equation', 'equation*', 'align', 'align*', 'gather', 'gather*',
    'multline', 'multline*', 'eqnarray', 'eqnarray*', 'flalign', 'flalign*',
    'verbatim', 'verbatim*', 'lstlisting', 'minted', 'tikzpicture', 'thebibliography', 'comment',
})
_TEX_DROP_ARG_CMD_RE = re.compile(
    r'\\(?:label|ref|eqref|pageref|autoref|[Cc]ref|cite[pt]?|documentclass|usepackage|'
    r'includegraphics|graphicspath|bibliographystyle|bibliography|hypersetup|definecolor|'
    r'begin|end|input|include)\*?(?:\[[^\]]*\])*\{[^}]*\}'
)


def _strip_tex_comment(line):
    """剥离行内未转义的 LaTeX `%` 注释，保留 `\\%`。"""
    out = []
    idx = 0
    while idx < len(line):
        ch = line[idx]
        if ch == '\\' and idx + 1 < len(line):
            out.append(line[idx : idx + 2])
            idx += 2
            continue
        if ch == '%':
            break
        out.append(ch)
        idx += 1
    return ''.join(out)


def _iter_visible_doc_lines(rel_path, raw_text):
    """提取文档（Markdown/LaTeX/reST）或 SVG 中需要做事实锚定核验的可见文本行。"""
    low_rel = rel_path.lower()
    if low_rel.endswith('.svg'):
        for m in re.finditer(r'<text\b[^>]*>([\s\S]*?)</text>', raw_text, re.I):
            line_no = raw_text.count('\n', 0, m.start()) + 1
            plain = re.sub(r'<[^>]+>', ' ', m.group(1))
            plain = (plain.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
                     .replace('&quot;', '"').replace('&#39;', "'")).strip()
            if plain:
                yield line_no, plain
        return
    if low_rel.endswith('.tex'):
        stripped_lines = [(ln_no, _strip_tex_comment(raw_line)) for ln_no, raw_line in enumerate(raw_text.splitlines(), 1)]
        has_doc_env = any(r'\begin{document}' in ln for _, ln in stripped_lines)
        in_body = not has_doc_env
        skip_env = ''
        in_display_math = False
        for line_no, line in stripped_lines:
            if not in_body:
                if r'\begin{document}' in line:
                    in_body = True
                    line = line.split(r'\begin{document}', 1)[1]
                else:
                    continue
            if r'\end{document}' in line:
                line = line.split(r'\end{document}', 1)[0]
                in_body = False
            # 逐段剥离数学与字面环境，保留同行环境外可见正文
            visible_parts = []
            cur = line
            while cur:
                if skip_env:
                    m_end = re.search(rf'\\end\{{{re.escape(skip_env)}\}}', cur)
                    if not m_end:
                        cur = ''
                        break
                    skip_env = ''
                    cur = cur[m_end.end():]
                    continue
                if in_display_math:
                    m_dm_end = re.search(r'\\\]|\$\$', cur)
                    if not m_dm_end:
                        cur = ''
                        break
                    in_display_math = False
                    cur = cur[m_dm_end.end():]
                    continue
                m_env = re.search(r'\\begin\{([A-Za-z*]+)\}', cur)
                if m_env and m_env.group(1) not in _TEX_SKIP_ENVS:
                    m_env = None
                m_dm = re.search(r'\\\[|\$\$', cur)
                candidates = [m for m in (m_env, m_dm) if m is not None]
                if not candidates:
                    visible_parts.append(cur)
                    break
                first_m = min(candidates, key=lambda m: m.start())
                visible_parts.append(cur[:first_m.start()])
                rest = cur[first_m.end():]
                if first_m is m_env:
                    env_name = first_m.group(1)
                    m_end = re.search(rf'\\end\{{{re.escape(env_name)}\}}', rest)
                    if m_end:
                        cur = rest[m_end.end():]
                    else:
                        skip_env = env_name
                        cur = ''
                else:
                    close_pat = r'\\\]' if first_m.group(0) == r'\[' else r'\$\$'
                    m_close = re.search(close_pat, rest)
                    if m_close:
                        cur = rest[m_close.end():]
                    else:
                        in_display_math = True
                        cur = ''
            joined = ' '.join(visible_parts)
            cleaned = _TEX_DROP_ARG_CMD_RE.sub(' ', joined)
            cleaned = re.sub(r'\\[A-Za-z@]+\*?', ' ', cleaned)
            cleaned = cleaned.replace('{', ' ').replace('}', ' ').replace('~', ' ')
            if cleaned.strip():
                yield line_no, cleaned
            if not in_body:
                break
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


def _audit_single_doc_text(rel_path, doc_text, anchor, baseline_text='', *, is_upstream_repo=True):
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
            num_reason = (
                '定量数值未在 fleet_profile.json 或源码常量中找到对应依据'
                if is_upstream_repo
                else '定量数值未在项目源码、数据或配置常量中找到对应依据'
            )
            record(line_no, 'ungrounded_number', f'{raw_num} {unit}', num_reason)

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


_HYGIENE_MD_IMG_RE = re.compile(r'!\[[^\]]*\]\(((?:[^()\r\n]|\([^()\r\n]*\))+)\)')
_HYGIENE_TEX_IMG_RE = re.compile(r'\\includegraphics(?:\[[^\]]*\])?\{([^}\r\n]+)\}')
_HYGIENE_RST_IMG_RE = re.compile(r'^\s*\.\.\s+(?:image|figure)::\s+(\S+)')
_HYGIENE_TEX_GRAPHICSPATH_RE = re.compile(r'\\graphicspath\{((?:\s*\{[^}]+\}\s*)+)\}')
_HYGIENE_TEX_NEWTHEOREM_RE = re.compile(
    r'\\newtheorem\*?\{([A-Za-z0-9_*-]+)\}(?:\[[^\]]*\])?\{([^}]*)\}'
)
_HYGIENE_CLAIM_TITLE_RE = re.compile(
    r'(?:\b(?:theorem|proposition|lemma|corollary|claim)\b|定理|命题|引理|推论)',
    re.I,
)
_HYGIENE_TEX_PROOF_RE = re.compile(
    r'\\begin\{proof\*?\}|\\(?:input|include)\{[^}]*proof[^}]*\}|\\(?:sub)*section\*?\{[^}]*(?:proof|证明)[^}]*\}',
    re.I,
)
_HYGIENE_MD_CLAIM_RE = re.compile(
    r'^(?:#{1,6}\s+|\*{2}|__)(?:Theorem|Proposition|Lemma|Corollary|定理|命题|引理|推论)\s*(?:\d+(?:\.\d+)*|[IVX]+)\b',
    re.I,
)
_HYGIENE_MD_PROOF_RE = re.compile(
    r'^(?:(?:#{1,6}\s+|\*{2}|__|_|\*)\s*(?:Proof|证明|证明要点|Proof\s+Sketch)\b|\\begin\{proof\*?\})',
    re.I,
)
_HYGIENE_RST_CLAIM_HEADING_RE = re.compile(
    r'^(?:Theorem|Proposition|Lemma|Corollary|定理|命题|引理|推论)\s*(?:\d+(?:\.\d+)*|[IVX]+)\b',
    re.I,
)
_HYGIENE_RST_CLAIM_DIRECTIVE_RE = re.compile(
    r'^\s*\.\.\s+(?:theorem|proposition|lemma|corollary)::',
    re.I,
)
_HYGIENE_RST_PROOF_RE = re.compile(
    r'^(?:\s*\.\.\s+proof::|\s*(?:\*{2}|__)?(?:Proof|证明|证明要点|Proof\s+Sketch)(?:\*{2}|__)?\s*[:：.]?\s*$)',
    re.I,
)
_HYGIENE_RST_UNDERLINE_RE = re.compile(r'^([=\-~^"\'`:#*+])\1{2,}\s*$')
_HYGIENE_PROOF_DEFERRED_RE = re.compile(
    r'(?:proofs?\s+(?:are|appear|deferred|omitted|provided|located)\s+in\s+(?:the\s+)?(?:supplementary|online\s+supplement|companion)'
    r'|证明(?:详见|载于|附于)(?:外部|补充材料|配套附录|独立附录))',
    re.I,
)
_HYGIENE_OUTLINE_TITLE_RE = re.compile(
    r'(?:\b(?:outline|index|toc|table\s+of\s+contents|roadmap|summary|map)\b|目录|大纲|提纲|总纲|索引|路线图)',
    re.I,
)
_TEX_IMAGE_AUTO_EXTS = ('', '.pdf', '.png', '.jpg', '.jpeg', '.eps', '.svg')


def _iter_tex_active_lines(doc_text):
    """剥离 LaTeX 注释并跳过 verbatim/lstlisting/minted/comment 环境，产出 (line_no, active_line)。"""
    skip_env = ''
    for line_no, raw_line in enumerate(doc_text.splitlines(), 1):
        line = _strip_tex_comment(raw_line)
        if skip_env:
            m_end = re.search(rf'\\end\{{{re.escape(skip_env)}\}}', line)
            if not m_end:
                continue
            skip_env = ''
            line = line[m_end.end():]
        m_begin = re.search(r'\\begin\{(verbatim\*?|lstlisting|minted|comment)\}', line)
        if m_begin:
            env_name = m_begin.group(1)
            m_end = re.search(rf'\\end\{{{re.escape(env_name)}\}}', line[m_begin.end():])
            if m_end:
                line = line[:m_begin.start()] + ' ' + line[m_begin.end() + m_end.end():]
            else:
                line = line[:m_begin.start()]
                skip_env = env_name
        if line.strip():
            yield line_no, line


def _hygiene_clean_local_media_target(raw_target):
    target = str(raw_target or '').strip()
    if not target:
        return ''
    if target.startswith('<') and '>' in target:
        target = target[1:target.index('>')].strip()
    else:
        target = re.split(r'\s+(?=["\'])', target, maxsplit=1)[0].strip()
    target = target.split('#', 1)[0].split('?', 1)[0].strip()
    if not target:
        return ''
    low = target.lower()
    if low.startswith(('http://', 'https://', 'data:', 'mailto:', 'ftp://', 'file://', '//')):
        return ''
    if any(ch in target for ch in ('<', '>', '{', '}', '*', '$', '\\', '#')):
        return ''
    if target.startswith('/') or re.match(r'^[A-Za-z]:[/\\]', target):
        return ''
    return target


def _hygiene_audit_local_media_refs(rel_path, doc_text, fpath, root_dir, record):
    """跨格式校验 Markdown / LaTeX / reST 的本地相对图表或媒体引用是否真实存在。"""
    if fpath is None:
        return
    base_dir = Path(fpath).parent
    ext = Path(rel_path).suffix.lower()

    def exists_local(rel_target, *, allow_tex_auto_ext=False, search_dirs=(base_dir,)):
        exts = _TEX_IMAGE_AUTO_EXTS if (allow_tex_auto_ext and not Path(rel_target).suffix) else ('',)
        for parent in search_dirs:
            for suffix in exts:
                cand = parent / f'{rel_target}{suffix}'
                if cand.is_file():
                    return True
        return False

    if ext == '.tex':
        active_lines = list(_iter_tex_active_lines(doc_text))
        gpaths = []
        for _, line in active_lines:
            for gm in _HYGIENE_TEX_GRAPHICSPATH_RE.finditer(line):
                for sub in re.findall(r'\{([^}]+)\}', gm.group(1)):
                    sub_clean = sub.strip()
                    if sub_clean and not sub_clean.startswith('/'):
                        gpaths.append(base_dir / sub_clean)
        tex_dirs = [base_dir, *gpaths]
        if root_dir is not None and Path(root_dir).resolve() != base_dir.resolve():
            tex_dirs.append(Path(root_dir).resolve())
        for line_no, line in active_lines:
            for m in _HYGIENE_TEX_IMG_RE.finditer(line):
                target = _hygiene_clean_local_media_target(m.group(1))
                if target and not exists_local(target, allow_tex_auto_ext=True, search_dirs=tex_dirs):
                    record(
                        rel_path, line_no, 'broken_local_media_ref', target,
                        '本地图表或媒体相对引用指向的文件不存在，请核对路径或补齐对应图件',
                    )
        return

    if ext == '.rst':
        for line_no, raw_line in enumerate(doc_text.splitlines(), 1):
            m = _HYGIENE_RST_IMG_RE.match(raw_line)
            if not m:
                continue
            target = _hygiene_clean_local_media_target(m.group(1))
            if target and not exists_local(target, search_dirs=(base_dir,)):
                record(
                    rel_path, line_no, 'broken_local_media_ref', target,
                    '本地图表或媒体相对引用指向的文件不存在，请核对路径或补齐对应图件',
                )
        return

    for line_no, raw_line, in_fence, in_math in _hygiene_iter_unmasked_lines(doc_text):
        if in_fence or in_math:
            continue
        cleaned = re.sub(r'<!--.*?-->', ' ', raw_line)
        cleaned = re.sub(r'`[^`\r\n]*`', ' ', cleaned)
        for m in _HYGIENE_MD_IMG_RE.finditer(cleaned):
            target = _hygiene_clean_local_media_target(m.group(1))
            if target and not exists_local(target, search_dirs=(base_dir,)):
                record(
                    rel_path, line_no, 'broken_local_media_ref', target,
                    '本地图表或媒体相对引用指向的文件不存在，请核对路径或补齐对应图件',
                )


def _hygiene_audit_theorem_shells(rel_path, doc_text, record):
    """跨格式检测正式文稿中声明多条定理/命题但全文缺失证明段落的空壳问题。"""
    ext = Path(rel_path).suffix.lower()
    if ext not in ('.tex', '.md', '.rst'):
        return
    if _HYGIENE_OUTLINE_TITLE_RE.search(Path(rel_path).stem):
        return
    if ext == '.tex':
        active_lines = list(_iter_tex_active_lines(doc_text))
        title_m = re.search(r'\\title(?:\[[^\]]*\])?\{([^}]*)\}', '\n'.join(ln for _, ln in active_lines))
        if title_m and _HYGIENE_OUTLINE_TITLE_RE.search(title_m.group(1)):
            return
        claim_envs = {'theorem', 'proposition', 'lemma', 'corollary'}
        for _, line in active_lines:
            for nm in _HYGIENE_TEX_NEWTHEOREM_RE.finditer(line):
                if _HYGIENE_CLAIM_TITLE_RE.search(nm.group(2)):
                    claim_envs.add(nm.group(1).rstrip('*'))
        claim_pat = re.compile(
            r'\\begin\{(?:' + '|'.join(re.escape(e) for e in sorted(claim_envs)) + r')\*?\}'
        )
        claims = 0
        proofs = 0
        first_claim_line = 0
        deferred = False
        for line_no, line in active_lines:
            c_hits = claim_pat.findall(line)
            if c_hits:
                if not first_claim_line:
                    first_claim_line = line_no
                claims += len(c_hits)
            if _HYGIENE_TEX_PROOF_RE.search(line):
                proofs += 1
            if _HYGIENE_PROOF_DEFERRED_RE.search(line):
                deferred = True
        if claims >= 2 and proofs == 0 and not deferred:
            record(
                rel_path, first_claim_line or 1, 'unproved_theorem_shell',
                f'{claims} formal claims, 0 proofs',
                '正式文稿声明了多条定理或命题但全文缺失证明段落，请补齐完整证明或显式指向外部证明附录',
            )
        return

    if ext == '.rst':
        raw_lines = doc_text.splitlines()
        for idx, raw_line in enumerate(raw_lines[:15]):
            s = raw_line.strip()
            if s and idx + 1 < len(raw_lines) and _HYGIENE_RST_UNDERLINE_RE.match(raw_lines[idx + 1].strip()):
                if _HYGIENE_OUTLINE_TITLE_RE.search(s):
                    return
                break
        claims = 0
        proofs = 0
        first_claim_line = 0
        deferred = False
        for idx, raw_line in enumerate(raw_lines):
            line_no = idx + 1
            s = raw_line.strip()
            if not s:
                continue
            has_underline = (idx + 1 < len(raw_lines) and bool(_HYGIENE_RST_UNDERLINE_RE.match(raw_lines[idx + 1].strip())))
            if _HYGIENE_RST_CLAIM_DIRECTIVE_RE.match(raw_line) or (has_underline and _HYGIENE_RST_CLAIM_HEADING_RE.match(s)) or _HYGIENE_MD_CLAIM_RE.match(s):
                if not first_claim_line:
                    first_claim_line = line_no
                claims += 1
            if _HYGIENE_RST_PROOF_RE.match(raw_line) or _HYGIENE_MD_PROOF_RE.match(s):
                proofs += 1
            if _HYGIENE_PROOF_DEFERRED_RE.search(s):
                deferred = True
        if claims >= 2 and proofs == 0 and not deferred:
            record(
                rel_path, first_claim_line or 1, 'unproved_theorem_shell',
                f'{claims} formal claims, 0 proofs',
                '正式文稿声明了多条定理或命题但全文缺失证明段落，请补齐完整证明或显式指向外部证明附录',
            )
        return

    lines_meta = list(_hygiene_iter_unmasked_lines(doc_text))
    for _, raw_line, in_f, in_m in lines_meta[:15]:
        if in_f or in_m:
            continue
        s = raw_line.strip()
        if s.startswith('# ') and _HYGIENE_OUTLINE_TITLE_RE.search(s):
            return
    claims = 0
    proofs = 0
    first_claim_line = 0
    deferred = False
    for line_no, raw_line, in_f, in_m in lines_meta:
        if in_f or in_m:
            continue
        s = raw_line.strip()
        if not s:
            continue
        s_box = re.sub(r'^(?:>\s*)+', '', s).strip()
        if _HYGIENE_MD_CLAIM_RE.match(s_box):
            if not first_claim_line:
                first_claim_line = line_no
            claims += 1
        if _HYGIENE_MD_PROOF_RE.match(s_box):
            proofs += 1
        if _HYGIENE_PROOF_DEFERRED_RE.search(s_box):
            deferred = True
    if claims >= 2 and proofs == 0 and not deferred:
        record(
            rel_path, first_claim_line or 1, 'unproved_theorem_shell',
            f'{claims} formal claims, 0 proofs',
            '正式文稿声明了多条定理或命题但全文缺失证明段落，请补齐完整证明或显式指向外部证明附录',
        )


def _audit_docs_hygiene(
    doc_entries,
    *,
    root_dir=None,
    file_paths=None,
    landing_max_bytes=18000,
    max_cjk_sentence_chars=220,
    max_ascii_sentence_chars=480,
    max_sentence_semicolons=4,
    dup_min_chars=36,
    dup_min_files=3,
    landing_max_inline_codes=8,
):
    """通用零黑名单文档卫生校验：跨格式图件引用与证明完整度、计数一致、补丁长句、跨文档复读与入口清晰度。"""
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
    path_lookup = dict(file_paths or {})

    for rel_path, doc_text in doc_entries:
        ext = Path(rel_path).suffix.lower()
        if ext not in ('.md', '.tex', '.rst'):
            continue
        is_changelog = Path(rel_path).name.upper() == 'CHANGELOG.MD'
        is_agent_prompt = '/agents/' in f'/{rel_path}'
        is_landing = rel_path == 'README.md'

        # 0. 跨格式（Markdown / LaTeX / reST）通用交付卫生：本地图件链接与定理空壳检测
        if not is_changelog and not is_agent_prompt:
            _hygiene_audit_local_media_refs(rel_path, doc_text, path_lookup.get(rel_path), root_dir, record)
            if not is_landing:
                _hygiene_audit_theorem_shells(rel_path, doc_text, record)

        if ext != '.md':
            continue

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
    hygiene_only=False,
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
    if hygiene_only and (base_ref or allow_files):
        raise HarnessError('--hygiene-only 仅执行文档结构与图件卫生检查，不可与 --base-ref 或 --allow-from-file 同时使用')
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
    effective_hygiene = bool(doc_hygiene or hygiene_only)
    doc_files = []
    if target.is_file():
        rel = target.relative_to(root).as_posix() if target.is_relative_to(root) else target.name
        doc_files.append((target, rel))
    else:
        skip_rels = {'LICENSE', 'NOTICE'} if is_upstream_repo else {'CHANGELOG.md', 'LICENSE', 'NOTICE'}
        for dirpath, dirs, files in os.walk(target, followlinks=False):
            dpath = Path(dirpath)
            dirs[:] = sorted(d for d in dirs if not _is_grounding_excluded_dir(d, dpath, target))
            for fname in sorted(files):
                fpath = dpath / fname
                if _is_link(fpath):
                    continue
                resolved_f = fpath.resolve()
                rel = resolved_f.relative_to(root).as_posix() if resolved_f.is_relative_to(root) else resolved_f.relative_to(target).as_posix()
                is_codex_toml = _is_codex_agent_toml_rel(rel)
                if fpath.suffix.lower() not in ('.md', '.tex', '.svg', '.rst') and not is_codex_toml:
                    continue
                if rel in skip_rels or rel.startswith('tests/') or rel == 'template/user-guard/assets/logo.svg':
                    continue
                doc_files.append((fpath, rel))
    exclude_rels = {rel for _, rel in doc_files}
    anchor = None if hygiene_only else _build_grounding_anchor(
        root, exclude_rels, allow_files=resolved_allow_files, extra_allowed_text=extra_allowed_text, target=target,
    )
    all_findings = []
    checked_files = []
    hygiene_entries = []
    hygiene_paths = {}
    for fpath, rel in doc_files:
        is_hygiene_doc = fpath.suffix.lower() in ('.md', '.tex', '.rst')
        if hygiene_only and not is_hygiene_doc:
            continue
        checked_files.append(rel)
        raw_doc = _optional_text(fpath)
        if effective_hygiene and is_hygiene_doc:
            hygiene_entries.append((rel, raw_doc))
            hygiene_paths[rel] = fpath
        if hygiene_only:
            continue
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
        all_findings.extend(_audit_single_doc_text(rel, raw_doc, anchor, baseline_txt, is_upstream_repo=is_upstream_repo))
    if hygiene_only and not checked_files:
        rel_target = target.relative_to(root).as_posix() if target.is_relative_to(root) else target.name
        all_findings.append({
            'file': rel_target,
            'line': 1,
            'category': 'no_applicable_hygiene_files',
            'token': target.suffix.lower() or target.name,
            'message': '--hygiene-only 仅检查 .md/.tex/.rst 文档，当前目标中未发现适用文档文件',
        })
    if effective_hygiene and hygiene_entries:
        all_findings.extend(_audit_docs_hygiene(hygiene_entries, root_dir=root, file_paths=hygiene_paths))
    by_category = collections.Counter(item['category'] for item in all_findings)
    return {
        'ok': not all_findings,
        'root': str(root),
        'target': str(target),
        'base_ref': base_ref or None,
        'doc_hygiene': bool(effective_hygiene),
        'hygiene_only': bool(hygiene_only),
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
        hygiene_only=bool(getattr(args, 'hygiene_only', False)),
    )
    status = 'verification_failed' if (args.strict and not report['ok']) else 'ok'
    payload = {'status': status, 'workspace_mode': detect_workspace_mode(args.root or args.target), **report}
    out_path = str(getattr(args, 'output', '') or '').strip()
    if out_path:
        dest = Path(out_path).expanduser()
        for cand in (dest, dest.parent, *dest.parent.parents):
            if os.path.lexists(cand) and _is_link(cand):
                raise HarnessError(f'--output 拒绝写入符号链接路径: {cand}')
        dest_resolved = dest.resolve()
        if dest_resolved.is_dir():
            raise HarnessError(f'--output 路径不能为目录: {dest}')
        target_resolved = Path(report['target']).resolve()
        root_resolved = Path(report['root']).resolve()
        protected = {target_resolved} if target_resolved.is_file() else set()
        for rel_cf in report.get('checked_files') or ():
            protected.add((root_resolved / rel_cf).resolve())
        if dest_resolved in protected:
            raise HarnessError(f'--output 路径不可覆盖待审计的目标文档: {dest}')
        payload['output_file'] = str(dest_resolved)
        _atomic_json(dest, payload)
    return _print(payload)


