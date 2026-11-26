"""_harness.dispatch - 物理门禁循环、全通道确认票据、批处理编排与 CLI 主入口。"""

# ════════════════════════════════════════════════════════════════════════════
# ⑦ 物理门禁循环与五大 CLI 子命令入口
# ════════════════════════════════════════════════════════════════════════════


_AGY_BATCH_LAUNCH_STAGGER_SEC = 1.5


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
    is_upstream_repo = detect_workspace_mode(anchor_root)['is_upstream_config_repo']
    drift_hits = _audit_single_doc_text(
        rel_label, polished_content, anchor, baseline_text=orig_doc, is_upstream_repo=is_upstream_repo,
    )
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
                    staged_violations = list(staged_scope.get('scope_violations') or ()) if isinstance(staged_scope, dict) else []
                    if isinstance(staged_scope, dict):
                        scope = _merge_staged_scope_report(scope, staged_scope)
                    fold = os.name == 'nt' or root.as_posix().startswith('/mnt/')
                    peer_masked = [p for p in (scope.get('scope_violations') or []) if peer_scopes and _scope_matches(p, peer_scopes, fold)]
                    for field in ('changed_files', 'scope_violations'):
                        if peer_scopes and scope[field] is not None:
                            scope[field] = [p for p in scope[field] if not _scope_matches(p, peer_scopes, fold)]
                    if peer_masked:
                        result['_peer_masked'] = (str(root), fold, peer_masked)
                    # OS 级强制只读沙箱且暂存目录零越界时，主目录快照捕获的变动源于外部并发进程，记录而不误判 scope_violation。
                    if (
                        readonly
                        and invocation.get('os_readonly_enforced') is True
                        and not staged_violations
                        and scope.get('scope_violations')
                    ):
                        result['external_concurrent_changes'] = list(scope['scope_violations'])
                        scope['scope_violations'] = []
                        scope['changed_files'] = []
                        scope['scope_check'] = 'external_concurrent_drift'
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
        stagger = _AGY_BATCH_LAUNCH_STAGGER_SEC if (prefix == 'agy' and not args.dry_run and len(group) > 1) else 0.0
        stagger_lock = threading.Lock()
        next_launch_at = [0.0]

        def run_paced(stream):
            if stagger > 0.0:
                delay = 0.0
                with stagger_lock:
                    now = time.monotonic()
                    if next_launch_at[0] > now:
                        delay = next_launch_at[0] - now
                        next_launch_at[0] += stagger
                    else:
                        next_launch_at[0] = now + stagger
                if delay > 0.0:
                    time.sleep(delay)
            return run(stream)

        with concurrent.futures.ThreadPoolExecutor(max_workers=_positive_int(limits.get(prefix)) or 2) as pool:
            return list(zip((index for index, _ in group), pool.map(run_paced, (stream for _, stream in group))))
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
    grounding.add_argument('--hygiene-only', action='store_true', help='仅执行通用文档卫生检查（图件链接、定理证明完整度、计数一致、补丁长句与跨文档复读），跳过源码词法锚定')
    grounding.add_argument('--output', default='', help='可选将核验结果 JSON 写入指定文件路径')
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


