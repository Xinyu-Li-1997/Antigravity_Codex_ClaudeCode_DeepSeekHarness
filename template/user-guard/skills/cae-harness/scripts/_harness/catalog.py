# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_harness.catalog - 模型目录、语义线索、挡位策略、执行画像与额度冷却门禁。"""

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


