# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_harness.engines - 进程树监督、JSON 流解析、轨迹与引用审计、无头执行与工作区快照。"""

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
    r'''(?:^|[\s'";&|=(])(?:Get-Content|gc|Select-String|sls|type)\b[^;|&()\r\n]*?\s-(?:LiteralPath|Path)\s+(?:"([^"]+)"|'([^']+)'|([^\s"'|;&)$]+))''',
    re.I,
)
_SHELL_READ_DOTNET_OR_PY_RE = re.compile(
    r'''(?:\[(?:System\.)?IO\.File\]::Read(?:AllText|AllLines|AllBytes)|Path\(|open\()\s*(?:"([^"\r\n]+)"|'([^'\r\n]+)')''',
    re.I,
)
_JS_EXEC_CMD_RE = re.compile(
    r'''\b(?:cmd|command)\s*:\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')''',
    re.I,
)
_JS_READ_FILE_RE = re.compile(
    r'''\b(?:read_file|view_file|read_text_file)\s*\(\s*\{[^}]*?\b(?:path|file_path|filepath)\s*:\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')''',
    re.I | re.S,
)


_JS_SIMPLE_ESCAPES = {'\\': '\\', '"': '"', "'": "'", 'n': '\n', 'r': '\r', 't': '\t'}


def _unescape_js_literal(raw):
    if not isinstance(raw, str) or '\\' not in raw:
        return raw or ''
    return re.sub(r'\\(.)', lambda m: _JS_SIMPLE_ESCAPES.get(m.group(1), m.group(0)), raw)


def _extract_shell_read_paths(command):
    if not isinstance(command, str) or not command:
        return []
    # 兼容 Windows Codex 经 powershell.exe -Command 单引号嵌套转义生成的 '"'path' 形式。
    normalized = command.replace("'\"'", "'")
    paths = []
    for pattern in (_SHELL_READ_POS_RE, _SHELL_READ_PS_NAMED_RE, _SHELL_READ_DOTNET_OR_PY_RE):
        for match in pattern.finditer(normalized):
            cand = next((g for g in match.groups() if g), '')
            if cand and not cand.startswith(('-', '$')) and '$' not in cand:
                paths.append(cand)
    return paths


def _extract_js_exec_read_paths(script):
    """提取 Codex 0.171+ custom_tool_call('exec') JS 编排脚本中通过 tools.exec_command / tools.read_file 读取的路径。"""
    if not isinstance(script, str) or not script:
        return []
    paths = []
    for m in _JS_READ_FILE_RE.finditer(script):
        cand = _unescape_js_literal(m.group(1) or m.group(2) or '')
        if cand:
            paths.append(cand)
    for m in _JS_EXEC_CMD_RE.finditer(script):
        cmd_text = _unescape_js_literal(m.group(1) or m.group(2) or '')
        if cmd_text:
            paths.extend(_extract_shell_read_paths(cmd_text))
    return paths


def _output_block_has_error(block):
    if block.get('is_error') or block.get('isError') or block.get('error') or block.get('status') == 'error':
        return True
    out = block.get('output')
    if isinstance(out, dict):
        meta = out.get('metadata') if isinstance(out.get('metadata'), dict) else out
        ec = meta.get('exit_code')
        if isinstance(ec, int) and ec != 0:
            return True
    elif isinstance(out, list):
        texts = [str(item.get('text') or '') for item in out if isinstance(item, dict)]
        joined = '\n'.join(texts)
        if re.search(r'(?:Script failed|Uncaught|ReferenceError|SyntaxError|TypeError:)', joined):
            return True
        codes = [int(c) for c in re.findall(r'"exit_code"\s*:\s*(-?\d+)', joined)]
        if codes and any(c != 0 for c in codes):
            return True
    elif isinstance(out, str):
        if out.strip().startswith('{'):
            parsed = next(_json_events([out]), None)
            if isinstance(parsed, dict):
                meta = parsed.get('metadata') if isinstance(parsed.get('metadata'), dict) else parsed
                ec = meta.get('exit_code')
                if isinstance(ec, int) and ec != 0:
                    return True
    return False


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
            btype = block.get('type') or ''
            name = str(tool.get('name') or tool.get('tool') or block.get('tool_name') or '').lower()
            raw_args = tool.get('input') if tool.get('input') is not None else (
                tool.get('parameters') if tool.get('parameters') is not None else tool.get('arguments')
            )
            args = raw_args if isinstance(raw_args, dict) else {}
            if isinstance(raw_args, str):
                parsed_args = next(_json_events([raw_args]), None)
                if isinstance(parsed_args, dict):
                    args = parsed_args
            call = next((block[k] for k in ('tool_use_id', 'callId', 'call_id', 'toolCallId', 'id')
                         if isinstance(block.get(k), str)), None)
            paths = []
            if name in ('read', 'read_file', 'view_file', 'read_text_file') and isinstance(args, dict):
                paths = [v for k, v in args.items() if k.lower() in ('path', 'file_path', 'filepath', 'absolutepath')]
                if call and kind != 'tool' and block.get('status') != 'completed':
                    pending[call], paths = paths, []
                elif (block.get('state') != 'DONE' and block.get('status') != 'completed') or tool.get('error'):
                    paths = []
            elif (
                btype in ('custom_tool_call', 'function_call', 'local_shell_call', 'tool_use')
                and name in ('exec', 'exec_command', 'shell', 'shell_command', 'bash', 'run_command', 'js_repl')
            ):
                cmd_paths = []
                if isinstance(args, dict):
                    cmd_val = args.get('cmd') or args.get('command') or args.get('CommandLine')
                    if isinstance(cmd_val, list):
                        cmd_val = ' '.join(str(x) for x in cmd_val)
                    if isinstance(cmd_val, str) and cmd_val:
                        cmd_paths.extend(_extract_shell_read_paths(cmd_val))
                if not cmd_paths and isinstance(raw_args, str) and raw_args:
                    cmd_paths.extend(_extract_js_exec_read_paths(raw_args))
                    if not cmd_paths:
                        cmd_paths.extend(_extract_shell_read_paths(raw_args))
                if cmd_paths and call:
                    pending[call] = cmd_paths
            if (
                btype in ('tool_result', 'function_call_output', 'custom_tool_call_output', 'local_shell_call_output')
                or kind in ('tool_result', 'tool/result', 'custom_tool_call_output', 'local_shell_call_output')
            ):
                paths = pending.pop(call, []) if not _output_block_has_error(block) else []
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

    def _resolve_opened_alias(rel_cand):
        if rel_cand in opened:
            return rel_cand
        if ntpath.isabs(rel_cand) or rel_cand.startswith('/'):
            return rel_cand
        norm = rel_cand
        while norm.startswith('./'):
            norm = norm[2:]
        if not norm or '..' in norm.split('/'):
            return rel_cand
        # 模型常以文件名或子路径简写引用已内联/已读文件（如 manuscript.md 对应 src/paper/manuscript.md）
        suffix = '/' + norm
        matches = [o for o in opened if o == norm or o.endswith(suffix)]
        if len(matches) == 1:
            return matches[0]
        if os.name == 'nt':
            low_cand = norm.casefold()
            low_suf = suffix.casefold()
            ci_matches = [o for o in opened if o.casefold() == low_cand or o.casefold().endswith(low_suf)]
            if len(ci_matches) == 1:
                return ci_matches[0]
        return rel_cand

    for path, start, end in sorted(refs):
        rel = _normalize_rel_file(path, root)
        direct_exists = (
            not ntpath.isabs(rel) and not rel.startswith('/')
            and (
                (stage_dir is not None and _is_resolved_within(stage_dir / rel, stage_dir) and (stage_dir / rel).is_file())
                or (_is_resolved_within(root / rel, root) and (root / rel).is_file())
            )
        )
        if not direct_exists:
            aliased = _resolve_opened_alias(rel)
            if aliased != rel:
                rel = aliased
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
            raise HarnessError('dsh 当前无原生生命周期硬钩子（no_lifecycle_hooks，仅原生文件沙箱），不支持 hooks_only 隔离，请选用 no_skills、clean_room 或改用 codex/claude')


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
    prefix, _ = _engine_parts(spec)
    persist = bool(resume) or (not ephemeral and hd.get('persist_sessions_by_default', True))
    # agy --print-timeout 超时时后台回合仍在执行（with turn in progress），同会话 wrapup 必然跳过或阻塞；
    # 因此不对 agy 提前按 soft_timeout_ratio 倒扣主回合预算，把完整 timeout_sec 全额交给主回合。
    soft = persist and timeout_sec >= 120 and prefix != 'agy'
    primary_budget = (min(timeout_sec - hd.get('wrapup_min_sec', 30),
                          max(timeout_sec - hd.get('wrapup_max_sec', 150), int(timeout_sec * hd.get('soft_timeout_ratio', .85))))
                      if soft else timeout_sec)
    batch = batch or _QuotaBatch(retry_quota)
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
        if prefix == 'codex':
            env['CAE_HARNESS_OS_SANDBOX'] = 'read-only' if readonly else 'workspace-write'
        else:
            env.pop('CAE_HARNESS_OS_SANDBOX', None)
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
                               os_readonly_enforced=bool(readonly and prefix == 'codex'),
                               inline_readonly=bool(readonly and (inline_readonly is True or (inline_readonly is None and clean_room))),
                               command_transport='windows_interop' if interop else 'native')
            if interop:
                extra_env = {}
                if env.get('CAE_HARNESS_OS_SANDBOX'):
                    extra_env['CAE_HARNESS_OS_SANDBOX'] = env['CAE_HARNESS_OS_SANDBOX']
                if prefix == 'claude' and effective_claude_home:
                    extra_env['CLAUDE_CONFIG_DIR'] = _wsl_path(Path(effective_claude_home))
                elif prefix == 'dsh' and effective_dsh_home:
                    extra_env.update({'DSH_HOME': _wsl_path(Path(effective_dsh_home)), 'DSH_TELEMETRY_DISABLED': '1'})
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
                if (
                    prefix == 'agy'
                    and not wrapup_stalled
                    and (
                        getattr(failure, 'details', {}).get('finished_reason') == 'print_timeout'
                        or _is_agy_print_timeout_text(failure.details.get('partial_stderr') or '')
                    )
                    and 'with turn in progress' in (
                        (failure.details.get('stderr') or '') + '\n' + (failure.details.get('partial_stderr') or '')
                    ).lower()
                ):
                    wrapup_stalled = True
                    wrapup_blocked_reason = 'agy_background_turn_in_progress'
                    receipt['wrapup_stalled'] = True
                    failure.details['wrapup_stalled'] = True
                    failure.details['wrapup_status'] = 'skipped_background_turn_active'
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
                           None, stage_files, effort, retry_quota, profile=profile, batch=batch, receipt=receipt,
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
    blobs_dir = target / 'blobs'
    manifest_file = target / 'manifest.json'
    is_replay_packet = (
        target.is_dir()
        and manifest_file.is_file()
        and not _is_link(manifest_file)
        and blobs_dir.is_dir()
        and not _is_link(blobs_dir)
    )
    resolved_blobs_dir = blobs_dir.resolve() if is_replay_packet else None
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
            in_packet_blobs = resolved_blobs_dir is not None and Path(root).resolve() == resolved_blobs_dir
            for name in sorted(files):
                is_packet_blob = in_packet_blobs and bool(re.fullmatch(r'[0-9a-f]{64}', name))
                if not is_packet_blob and Path(name).suffix.lower() not in SOURCE_EXTENSIONS and name not in NAMED_TEXT_FILES:
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


