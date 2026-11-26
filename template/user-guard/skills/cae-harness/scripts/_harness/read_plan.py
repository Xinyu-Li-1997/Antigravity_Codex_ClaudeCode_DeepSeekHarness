"""_harness.read_plan - 读前尺寸评估、保守层级分片规划与跨端盲读汇总核对。"""

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


