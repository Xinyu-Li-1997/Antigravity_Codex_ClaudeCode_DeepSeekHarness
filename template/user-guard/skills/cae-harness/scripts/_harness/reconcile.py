"""_harness.reconcile - 统一基线冻结、义务结算与确定性机械对账表引擎。"""

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
    return _grounding_excluded_dirs()


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


