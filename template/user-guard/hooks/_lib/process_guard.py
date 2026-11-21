"""CLI hook resource limits and noninteractive subprocess defaults."""

import math
import os
import signal
import threading
from contextlib import suppress
from pathlib import Path

import user_words


def _limit_address_space(cap: int):
    with suppress(ImportError, AttributeError, OSError, ValueError, OverflowError, IndexError):
        import resource

        # RLIMIT_AS limits virtual address space, including mappings outside RSS.
        with open("/proc/self/statm", encoding="ascii") as stats:
            used = int(stats.readline(128).split()[0]) * os.sysconf("SC_PAGE_SIZE")
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if (0 < used < cap and (soft == resource.RLIM_INFINITY or cap < soft)
                and (hard == resource.RLIM_INFINITY or cap <= hard)):
            resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
            return resource, (soft, hard)
    return None, None


class _SelfGuardCleanup:
    def __init__(self, close):
        self.close = close

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def install_hook_self_guard(hooks_dir: Path) -> _SelfGuardCleanup:
    config = user_words.get_runtime_tunables("loop_guards", hooks_dir).get("hook_safety")
    config = config if isinstance(config, dict) else {}
    seconds = 12
    with suppress(TypeError, ValueError, OverflowError):
        seconds = float(config.get("wall_timeout_sec", seconds))
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = 12

    def timeout(*_signal_args):
        try:
            os.write(2, b"user-guard: hook wall-clock timeout\n")
        finally:
            if os.name == "nt":
                os._exit(1)
            raise SystemExit(1)

    if os.name == "nt":
        timer = threading.Timer(seconds, timeout)
        timer.daemon = True
        timer.start()
        return _SelfGuardCleanup(timer.cancel)
    if os.name == "posix":
        res_mod, prev_rlimit = None, None
        with suppress(OverflowError):
            lim_res = _limit_address_space(user_words._coerce_pos_int(config.get("max_rss_mib"), 1024) * 1024**2)
            if isinstance(lim_res, tuple) and len(lim_res) == 2:
                res_mod, prev_rlimit = lim_res
        if threading.current_thread() is threading.main_thread():
            with suppress(AttributeError, OSError, ValueError, OverflowError):
                prev = signal.signal(signal.SIGALRM, timeout)
                old_delay, old_interval = signal.setitimer(signal.ITIMER_REAL, seconds)

                def _reset_posix() -> None:
                    with suppress(AttributeError, OSError, ValueError):
                        signal.setitimer(signal.ITIMER_REAL, old_delay, old_interval)
                        signal.signal(signal.SIGALRM, prev)
                    if res_mod is not None and prev_rlimit is not None:
                        with suppress(OSError, ValueError):
                            res_mod.setrlimit(res_mod.RLIMIT_AS, prev_rlimit)

                return _SelfGuardCleanup(_reset_posix)
        if res_mod is not None and prev_rlimit is not None:
            return _SelfGuardCleanup(lambda: res_mod.setrlimit(res_mod.RLIMIT_AS, prev_rlimit))
    return _SelfGuardCleanup(lambda: None)


def apply_noninteractive_env(env: dict) -> dict:
    return {"GIT_PAGER": "cat", "PAGER": "cat", "LESS": "-FRX",
            "GCM_INTERACTIVE": "never", "HWLOC_COMPONENTS": "-gl", **env}
