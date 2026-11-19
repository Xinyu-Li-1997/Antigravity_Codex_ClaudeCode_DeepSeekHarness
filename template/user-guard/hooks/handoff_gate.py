"""
handoff_gate.py - 长会话交接策略与 Antigravity / Codex 宿主适配 (user-guard)

Antigravity 适配保留原有遥测与门禁；Codex 独立入口默认仅观测和提醒，
仅在显式 enforce 模式及显式压缩次数策略或用户交接指令下进入排空。
以下检查点、图片和 /goal 规则只用于 Antigravity：
1. 结合增量会话遥测（压缩检查点数、近期检查点间隔坍塌、transcript 体积、多模态读图数/字节数）、
   UI 截断告警与显式 R8 用户暂缓指令，维护四态状态机：
   normal -> advisory -> drain -> freeze（或 R8 暂缓 overridden）。
2. Stage-1 (advisory)：纯提醒不阻断，在 PreInvocation 提醒主控注意上下文预算与大体积读图消耗。
3. Stage-2 (drain -> freeze)：
   - 严禁新开委派（invoke_subagent / define_subagent / run_workflow / swarm / schedule / browser_* / cae_harness 派工 / git push）；
   - drain 排空窗口（默认 4 次模型调用 + 8 次写/运行动作）内允许完成正在进行中的原子收口与本地校验；
   - freeze 冻结期仅放行：read_file / view_file 非图片只读核对、后台任务/子代理查询管理（manage_task / manage_subagents）、已登记跨表面文件工具的交接文档编写与本地 git add/commit；
   - Stop 门禁验收交接完整性（并在开启 /goal 时强制校验 <!-- GOAL_CANCELLED --> 标记），每回合至多阻断 1 次。
"""

# ① 常量、阈值与宿主依赖

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, ContextManager, Dict, List, Optional, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
import user_words  # noqa: E402
from _lib import adaptive_policy as _adaptive_policy  # noqa: E402
from _lib import shell_lex as _shell_lex  # noqa: E402

user_words.ensure_utf8_stdio()

# 阶段预算默认值（支持由 fleet_profile.json runtime_tunables.handoff_thresholds 动态同步，并兼容单测属性覆盖）
DEFAULT_DRAIN_INVOCATIONS = 4
DEFAULT_DRAIN_ACTIONS = 8

# 遥测阈值是项目收口策略的启发式参数，不能据此推断模型的上下文容量。
ADVISORY_CP_THRESHOLD = 3
ADVISORY_TRANSCRIPT_BYTES = 3 * 1024 * 1024
ADVISORY_THRASH_MIN_CP = 3
ADVISORY_THRASH_AVG_INTERVAL = 90.0
ADVISORY_MEDIA_STEPS = 60
ADVISORY_IMAGE_CALLS = 35
ADVISORY_IMAGE_BYTES = 50 * 1024 * 1024

DRAIN_CP_THRESHOLD = 5
DRAIN_THRASH_MIN_CP = 4
DRAIN_THRASH_AVG_INTERVAL = 75.0
DRAIN_TRANSCRIPT_BYTES = 5 * 1024 * 1024
DRAIN_MEDIA_STEPS = 160
DRAIN_MEDIA_MIN_CP = 3
DRAIN_IMAGE_CALLS = 65
DRAIN_IMAGE_BYTES = 100 * 1024 * 1024

# 用户直接报告 UI 告警时，另检查现有轨迹是否达到最低遥测门槛。
UI_BANNER_PHRASE = "this conversation grew too large, so the history before this point may be missing or incomplete"
BANNER_PLAUSIBLE_CP = 2
BANNER_PLAUSIBLE_STEP = 120
BANNER_PLAUSIBLE_TRANSCRIPT_BYTES = 256 * 1024
BANNER_PLAUSIBLE_MEDIA_STEPS = 20
BANNER_PLAUSIBLE_IMAGE_CALLS = 20
BANNER_PLAUSIBLE_IMAGE_BYTES = 30 * 1024 * 1024

OVERRIDE_SNOOZE_CPS = 3
OVERRIDE_SNOOZE_STEPS = 120
MAX_TRACKED_IMAGES = 2000
_CLAUDE_DRAIN_ACTIONS = 4

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

OVERRIDE_COMMAND = (
    r"(?:暂不交接|先不交接|先别交接|不用交接|不要交接|取消交接|解除交接|暂缓交接|跳过交接|"
    r"先继续(?:当前|完成|执行)|继续当前对话|不用开新对话|先别停|继续往下做)"
)
AGY_OVERRIDE_REQUEST_RE = re.compile(
    r"(?:请(?:你)?)?(?:" + OVERRIDE_COMMAND + r"|继续当前对话(?:做完|完成)这一批)"
    r"(?:[，,、]\s*(?:请(?:你)?)?(?:" + OVERRIDE_COMMAND + r"|继续当前对话(?:做完|完成)这一批))*[。.!！]*",
)
AGY_HANDOFF_REQUEST_RE = re.compile(
    r"(?:/handoff|(?:请(?:你)?)?(?:立即)?(?:处理好交接|(?:整理|开始|准备|完成|做好|立即|执行)交接)"
    r"(?:并|后)?(?:停机|停止|结束当前对话)?)[。.!！]*",
)
AGY_BANNER_REPORT_RE = re.compile(
    r"(?:我(?:刚才|刚刚)?(?:看到|收到)|(?:界面|宿主|客户端|对话)(?:刚才|刚刚)?(?:显示|出现|提示))"
    r"(?:了)?(?:界面|系统|截断|上下文|会话|UI|\s)*(?:告警|提示|报错)?\s*[:：]\s*[“\"‘']?"
    + re.escape(UI_BANNER_PHRASE) + r"[.。]?[”\"’']?[.。!！]*",
    re.IGNORECASE,
)

# 交接产物的识别正则
HANDOFF_FILENAME_RE = re.compile(
    r"(?:handoff|handover|resume|next[_-]?prompt|new[_-]?conversation|交接|接续|提示词|停机|^state\.md$)",
    re.IGNORECASE,
)

# Stop 回复校验中提示词标记
PROMPT_MARKER_RE = re.compile(
    r"(?:新对话提示词|下一对话提示词|接续提示词|交接提示词|新会话提示词|next\s*prompt|new\s*conversation\s*prompt|"
    r"新开对话.*?提示词|复现与接续提示词)",
    re.IGNORECASE,
)

DELEGATION_TOOLS = {"invoke_subagent", "define_subagent", "swarm", "schedule", "run_workflow"}
AGY_TOOL_ALIASES = {
    "view_file": "read_file",
    "write_to_file": "write_file",
    "replace_file_content": "edit_file",
    "multi_replace_file_content": "edit_file",
    "sed_file": "edit_file",
    "notebook_edit": "edit_file",
}
AGY_INSPECTION_TOOLS = {
    "list_dir",
    "list_directory",
    "view_file_outline",
    "view_code_item",
    "grep_search",
    "find_by_name",
    "search_files",
    "read_url_content",
    "read_board",
    "list_resources",
    "read_resource",
    "manage_task",
    "manage_subagents",
    "ask_question",
    "finish",
}
MEDIA_TOOLS = {"generate_image", "imagegen", "view_image", "open_image"}

READONLY_BINARIES = {
    "ls",
    "dir",
    "pwd",
    "echo",
    "printf",
    "cat",
    "head",
    "tail",
    "wc",
    "find",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "fd",
    "which",
    "where",
    "type",
    "stat",
    "get-content",
    "gc",
    "get-childitem",
    "gci",
    "select-string",
    "sls",
    "get-location",
    "test-path",
    "git",
}

GIT_READONLY_SUBCMDS = {"status", "diff", "log", "show", "rev-parse", "ls-files", "describe", "shortlog"}
GIT_LOCAL_COMMIT_SUBCMDS = {"add", "commit"}
SHELL_WRAPPERS = {"bash", "sh", "zsh", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "env"}
SUBSYSTEM_WRAPPERS = {"wsl", "wsl.exe", "ssh", "ssh.exe", "docker", "docker.exe", "podman"}

MAX_HANDOFF_DOCUMENT_BYTES = 1024 * 1024

# Codex 使用官方事件字段；不读取 Antigravity 的检查点、图片体积或目标终止标记。
CODEX_EVENTS = {"SessionStart", "PostCompact", "UserPromptSubmit", "PreToolUse", "Stop"}
CODEX_DRAIN_ACTIONS = 4  # 显式严格模式的收尾动作预算，不表示模型容量。
CODEX_HANDOFF_REQUEST_RE = re.compile(
    r"^(?:/handoff|(?:请)?(?:立即)?(?:整理|准备|完成|做好|开始)交接(?:并|后)(?:停机|停止|结束当前对话))[。！!\s]*$",
    re.IGNORECASE,
)
CODEX_OVERRIDE_REQUEST_RE = re.compile(
    r"^(?:请)?" + OVERRIDE_COMMAND
    + r"(?:[，,；;、\s]+(?:请)?" + OVERRIDE_COMMAND + r")*[。.!！\s]*$",
    re.IGNORECASE,
)
CODEX_READ_TOOLS = {
    "read_file", "list_dir", "list_directory", "search_files", "get_goal",
    "list_agents", "wait_agent", "interrupt_agent", "wait", "request_user_input",
    "request_user_input_async", "list_mcp_resources", "read_mcp_resource",
}
CODEX_DELEGATION_TOOLS = {"spawn_agent", "agent", "followup_task", "send_message", "create_thread"}

_HANDOFF_BUILTINS: Dict[str, Any] = {
    "DEFAULT_DRAIN_INVOCATIONS": DEFAULT_DRAIN_INVOCATIONS,
    "DEFAULT_DRAIN_ACTIONS": DEFAULT_DRAIN_ACTIONS,
    "ADVISORY_CP_THRESHOLD": ADVISORY_CP_THRESHOLD,
    "ADVISORY_TRANSCRIPT_BYTES": ADVISORY_TRANSCRIPT_BYTES,
    "ADVISORY_THRASH_MIN_CP": ADVISORY_THRASH_MIN_CP,
    "ADVISORY_THRASH_AVG_INTERVAL": ADVISORY_THRASH_AVG_INTERVAL,
    "ADVISORY_MEDIA_STEPS": ADVISORY_MEDIA_STEPS,
    "ADVISORY_IMAGE_CALLS": ADVISORY_IMAGE_CALLS,
    "ADVISORY_IMAGE_BYTES": ADVISORY_IMAGE_BYTES,
    "DRAIN_CP_THRESHOLD": DRAIN_CP_THRESHOLD,
    "DRAIN_THRASH_MIN_CP": DRAIN_THRASH_MIN_CP,
    "DRAIN_THRASH_AVG_INTERVAL": DRAIN_THRASH_AVG_INTERVAL,
    "DRAIN_TRANSCRIPT_BYTES": DRAIN_TRANSCRIPT_BYTES,
    "DRAIN_MEDIA_STEPS": DRAIN_MEDIA_STEPS,
    "DRAIN_MEDIA_MIN_CP": DRAIN_MEDIA_MIN_CP,
    "DRAIN_IMAGE_CALLS": DRAIN_IMAGE_CALLS,
    "DRAIN_IMAGE_BYTES": DRAIN_IMAGE_BYTES,
    "OVERRIDE_SNOOZE_CPS": OVERRIDE_SNOOZE_CPS,
    "OVERRIDE_SNOOZE_STEPS": OVERRIDE_SNOOZE_STEPS,
    "MAX_TRACKED_IMAGES": MAX_TRACKED_IMAGES,
    "CODEX_DRAIN_ACTIONS": CODEX_DRAIN_ACTIONS,
    "_CLAUDE_DRAIN_ACTIONS": _CLAUDE_DRAIN_ACTIONS,
}
_ACTIVE_HANDOFF_BASELINE: Dict[str, Any] = dict(_HANDOFF_BUILTINS)
_LAST_HANDOFF_PROFILE_TOKEN: Optional[Tuple[Any, ...]] = None


def _pos_float(raw: Any, fallback: float) -> float:
    if isinstance(raw, bool):
        return fallback
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return fallback
    return val if val > 0 else fallback


_extract_runtime_model_and_effort = _adaptive_policy.extract_runtime_model_and_effort
_LAST_ACTIVE_HANDOFF_CTX: Tuple[str, str, str] = ("antigravity", "", "")


def _sync_handoff_tunables(
    client: Optional[str] = None,
    model_name: Optional[str] = None,
    effort: Optional[str] = None,
) -> Dict[str, Any]:
    """从 fleet_profile.json 同步交接门禁参数；若单测通过 monkeypatch 覆盖了模块属性则保留覆盖值。"""
    global _LAST_HANDOFF_PROFILE_TOKEN, _LAST_ACTIVE_HANDOFF_CTX
    if client is None and model_name is None and effort is None:
        eff_client, eff_model, eff_effort = _LAST_ACTIVE_HANDOFF_CTX
    else:
        eff_client = (client or "antigravity").strip().lower() or "antigravity"
        eff_model = (model_name or "").strip()
        eff_effort = (effort or "").strip()
        _LAST_ACTIVE_HANDOFF_CTX = (eff_client, eff_model, eff_effort)
    cache_token = (user_words.get_fleet_profile_cache_token(HOOKS_DIR), eff_client, eff_model, eff_effort)
    profile = user_words.load_fleet_profile(HOOKS_DIR)
    ht = user_words.get_runtime_tunables("handoff_thresholds", HOOKS_DIR)
    if _LAST_HANDOFF_PROFILE_TOKEN == cache_token:
        return ht
    _LAST_HANDOFF_PROFILE_TOKEN = cache_token
    agy = _adaptive_policy.resolve_adaptive_handoff_policy(
        profile, "antigravity", eff_model if eff_client == "antigravity" else "", eff_effort if eff_client == "antigravity" else ""
    )
    codex = _adaptive_policy.resolve_adaptive_handoff_policy(
        profile, "codex", eff_model if eff_client == "codex" else "", eff_effort if eff_client == "codex" else ""
    )
    claude = _adaptive_policy.resolve_adaptive_handoff_policy(
        profile, "claude", eff_model if eff_client == "claude" else "", eff_effort if eff_client == "claude" else ""
    )
    comp_agy = user_words.get_compaction_tunables(
        "antigravity",
        HOOKS_DIR,
        model_name=eff_model if eff_client == "antigravity" else "",
        effort=eff_effort if eff_client == "antigravity" else "",
    )
    resolved: Dict[str, Any] = {
        "DEFAULT_DRAIN_INVOCATIONS": user_words._coerce_pos_int(agy.get("default_drain_invocations"), _HANDOFF_BUILTINS["DEFAULT_DRAIN_INVOCATIONS"]),
        "DEFAULT_DRAIN_ACTIONS": user_words._coerce_pos_int(agy.get("default_drain_actions"), _HANDOFF_BUILTINS["DEFAULT_DRAIN_ACTIONS"]),
        "ADVISORY_CP_THRESHOLD": user_words._coerce_pos_int(agy.get("advisory_cp_threshold"), _HANDOFF_BUILTINS["ADVISORY_CP_THRESHOLD"]),
        "ADVISORY_TRANSCRIPT_BYTES": user_words._coerce_pos_int(agy.get("advisory_transcript_mib"), 3) * 1024 * 1024,
        "ADVISORY_THRASH_MIN_CP": user_words._coerce_pos_int(agy.get("advisory_thrash_min_cp"), _HANDOFF_BUILTINS["ADVISORY_THRASH_MIN_CP"]),
        # 注意：近期压缩频率统计单位为“步数（steps）”；优先读取语义准确的 *_steps，并兼容历史 *_sec 键名。
        "ADVISORY_THRASH_AVG_INTERVAL": _pos_float(
            agy.get("advisory_thrash_avg_interval_steps", agy.get("advisory_thrash_avg_interval_sec")),
            _HANDOFF_BUILTINS["ADVISORY_THRASH_AVG_INTERVAL"],
        ),
        "ADVISORY_MEDIA_STEPS": user_words._coerce_pos_int(agy.get("advisory_media_steps"), _HANDOFF_BUILTINS["ADVISORY_MEDIA_STEPS"]),
        "ADVISORY_IMAGE_CALLS": user_words._coerce_pos_int(agy.get("advisory_image_calls"), _HANDOFF_BUILTINS["ADVISORY_IMAGE_CALLS"]),
        "ADVISORY_IMAGE_BYTES": user_words._coerce_pos_int(agy.get("advisory_image_mib"), 50) * 1024 * 1024,
        "DRAIN_CP_THRESHOLD": user_words._coerce_pos_int(agy.get("drain_cp_threshold"), _HANDOFF_BUILTINS["DRAIN_CP_THRESHOLD"]),
        "DRAIN_THRASH_MIN_CP": user_words._coerce_pos_int(agy.get("drain_thrash_min_cp"), _HANDOFF_BUILTINS["DRAIN_THRASH_MIN_CP"]),
        "DRAIN_THRASH_AVG_INTERVAL": _pos_float(
            agy.get("drain_thrash_avg_interval_steps", agy.get("drain_thrash_avg_interval_sec")),
            _HANDOFF_BUILTINS["DRAIN_THRASH_AVG_INTERVAL"],
        ),
        "DRAIN_TRANSCRIPT_BYTES": user_words._coerce_pos_int(agy.get("drain_transcript_mib"), 5) * 1024 * 1024,
        "DRAIN_MEDIA_STEPS": user_words._coerce_pos_int(agy.get("drain_media_steps"), _HANDOFF_BUILTINS["DRAIN_MEDIA_STEPS"]),
        "DRAIN_MEDIA_MIN_CP": user_words._coerce_pos_int(agy.get("drain_media_min_cp"), _HANDOFF_BUILTINS["DRAIN_MEDIA_MIN_CP"]),
        "DRAIN_IMAGE_CALLS": user_words._coerce_pos_int(agy.get("drain_image_calls"), _HANDOFF_BUILTINS["DRAIN_IMAGE_CALLS"]),
        "DRAIN_IMAGE_BYTES": user_words._coerce_pos_int(agy.get("drain_image_mib"), 100) * 1024 * 1024,
        "OVERRIDE_SNOOZE_CPS": user_words._coerce_pos_int(agy.get("override_snooze_cps"), _HANDOFF_BUILTINS["OVERRIDE_SNOOZE_CPS"]),
        "OVERRIDE_SNOOZE_STEPS": user_words._coerce_pos_int(agy.get("override_snooze_steps"), _HANDOFF_BUILTINS["OVERRIDE_SNOOZE_STEPS"]),
        "MAX_TRACKED_IMAGES": int(comp_agy.get("max_tracked_images", _HANDOFF_BUILTINS["MAX_TRACKED_IMAGES"])),
        "CODEX_DRAIN_ACTIONS": user_words._coerce_pos_int(codex.get("enforce_drain_actions"), _HANDOFF_BUILTINS["CODEX_DRAIN_ACTIONS"]),
        "_CLAUDE_DRAIN_ACTIONS": user_words._coerce_pos_int(claude.get("enforce_drain_actions"), _HANDOFF_BUILTINS["_CLAUDE_DRAIN_ACTIONS"]),
    }
    mod_globals = globals()
    for name, new_val in resolved.items():
        was_unpatched = (mod_globals[name] == _ACTIVE_HANDOFF_BASELINE[name])
        _ACTIVE_HANDOFF_BASELINE[name] = new_val
        if was_unpatched:
            mod_globals[name] = new_val
    return ht


_sync_handoff_tunables()


# ② 会话状态、路径定位与图片请求登记

def default_gate_state(conversation_id: str) -> Dict[str, Any]:
    """生成初始长会话交接门禁状态"""
    _sync_handoff_tunables()
    return {
        "version": 1,
        "gateSchemaVersion": 3,
        "conversationId": conversation_id,
        "phase": "normal",
        "triggers": [],
        "bannerDetectedAtStep": None,
        "bannerTriggerTurnHash": "",
        "bannerTriggerHostStep": -1,
        "enteredDrainAtStep": None,
        "enteredFreezeAtStep": None,
        "drainInvocationsRemaining": DEFAULT_DRAIN_INVOCATIONS,
        "drainActionsRemaining": DEFAULT_DRAIN_ACTIONS,
        "handoffFileBaselines": {},
        "imageReads": {},
        "imageReadCallCount": 0,
        "isGoalActive": False,
        "overriddenTurnHash": "",
        "handoffRequestTurnHash": "",
        "overrideUntilCheckpoint": -1,
        "overrideUntilStep": -1,
        "stopGateBlockedTurnKeys": [],
        "lastAdvisoryCp": -1,
        "lastAdvisoryImageMilestone": 0,
        "lastAdvisoryTriggers": [],
        "updatedAt": user_words.current_iso_time(),
    }


def _gate_state_path(state_dir: Path, conversation_id: str) -> Path:
    return state_dir / f"{conversation_id}_handoff_gate.json"


def load_gate_state(state_dir: Path, conversation_id: str) -> Dict[str, Any]:
    """安全加载或初始化会话级交接门禁状态"""
    state_file = _gate_state_path(state_dir, conversation_id)
    default = default_gate_state(conversation_id)
    if not state_file.exists():
        return default
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8-sig"))
        if (not isinstance(raw, dict) or raw.get("conversationId") != conversation_id
                or raw.get("version") != 1):
            raise ValueError("门禁状态格式或身份错误")
        merged = dict(default)
        merged.update(raw)
        merged["gateSchemaVersion"] = int(raw.get("gateSchemaVersion", 1))
        for key in ("triggers", "handoffFileBaselines", "imageReads", "stopGateBlockedTurnKeys",
                    "lastAdvisoryTriggers", "drainInvocationsRemaining", "drainActionsRemaining",
                    "imageReadCallCount", "overrideUntilCheckpoint", "overrideUntilStep", "bannerTriggerHostStep"):
            if type(merged[key]) is not type(default[key]):
                raise ValueError(f"门禁状态字段 {key} 格式错误")
        if merged["phase"] not in ("normal", "advisory", "drain", "freeze", "overridden"):
            raise ValueError("未知的门禁阶段")
        if (any(not isinstance(item, str) for key in ("triggers", "stopGateBlockedTurnKeys", "lastAdvisoryTriggers")
                for item in merged[key])
                or any(type(value) is not int or value < 0 for value in merged["imageReads"].values())
                or merged["drainInvocationsRemaining"] < 0 or merged["drainActionsRemaining"] < 0
                or type(merged["isGoalActive"]) is not bool):
            raise ValueError("门禁状态含无效预算或遥测记录")
        return merged
    except (TypeError, ValueError, OSError, RecursionError) as exc:
        user_words.append_lifecycle_log(HOOKS_DIR, "HandoffGate:state_error", conversation_id,
                                        f"门禁状态读取失败（{exc}），保留原文件")
        raise


def save_gate_state(state_dir: Path, conversation_id: str, gate_state: Dict[str, Any]) -> None:
    gate_state["updatedAt"] = user_words.current_iso_time()
    user_words.atomic_write_json(_gate_state_path(state_dir, conversation_id), gate_state)


def _gate_state_lock(state_dir: Path, conversation_id: str, timeout_sec: float = 1.5,
                     strict: bool = False) -> ContextManager[None]:
    """保留既有测试和调用入口，两种模式均须持锁才可进入事务。"""
    _ = strict
    return user_words.session_state_lock(state_dir, conversation_id, timeout_sec)


def consume_drain_action(gate_state: Dict[str, Any]) -> bool:
    """共用阶段策略：完成一次获准的收尾动作后扣预算，用尽即冻结。"""
    remaining = max(0, int(gate_state.get("drainActionsRemaining", 0)) - 1)
    gate_state["drainActionsRemaining"] = remaining
    if remaining == 0:
        gate_state["phase"] = "freeze"
    return remaining == 0


def is_image_path(path_str: str) -> bool:
    if not path_str:
        return False
    cleaned = path_str.strip().strip("\"'")
    return Path(cleaned.replace("\\", "/")).suffix.lower() in IMAGE_EXTENSIONS


def _resolve_candidate_path(
    raw_path: str,
    cwd: Optional[str] = None,
    workspace_paths: Optional[List[str]] = None,
) -> Path:
    cleaned = os.path.expanduser(raw_path.strip().strip("\"'"))
    p = Path(cleaned)
    if p.is_absolute():
        return p
    bases: List[Path] = []
    if cwd and str(cwd).strip():
        bases.append(Path(str(cwd).strip()))
    if workspace_paths:
        for wp in workspace_paths:
            if wp and str(wp).strip():
                bases.append(Path(str(wp).strip()))
    for base in bases:
        cand = base / p
        if cand.exists():
            return cand
    if bases:
        return bases[0] / p
    return p


def _path_key(p: Path) -> str:
    return user_words._canonical_path_key(p)


def record_image_read(
    gate_state: Dict[str, Any],
    raw_path: str,
    cwd: Optional[str] = None,
    workspace_paths: Optional[List[str]] = None,
    increment_calls: bool = True,
) -> Tuple[int, int]:
    """登记 PreToolUse 图片请求，文件大小用于估算可能增加的图片负载。"""
    _sync_handoff_tunables()
    if increment_calls:
        gate_state["imageReadCallCount"] = int(gate_state.get("imageReadCallCount", 0)) + 1

    img_map: Dict[str, int] = gate_state.setdefault("imageReads", {})
    cleaned = raw_path.strip().strip("\"'")
    if cleaned.startswith("file://"):
        cleaned = cleaned[7:]
        if os.name == "nt" and cleaned.startswith("/") and len(cleaned) > 2 and cleaned[2] == ":":
            cleaned = cleaned[1:]
    target = _resolve_candidate_path(cleaned, cwd=cwd, workspace_paths=workspace_paths)
    key = _path_key(target)

    if key in img_map or len(img_map) < MAX_TRACKED_IMAGES:
        fsize = 0
        try:
            if target.exists() and target.is_file():
                fsize = int(target.stat().st_size)
        except OSError:
            fsize = 0
        img_map[key] = fsize

    return len(img_map), sum(int(v) for v in img_map.values() if isinstance(v, (int, float)))


# ③ Shell 分句、包装器与命令副作用分类

_split_shell_clauses = _shell_lex.split_shell_clauses


def _safe_split_clause_tokens(clause: str) -> List[str]:
    try:
        return shlex.split(clause, posix=(os.name != "nt"))
    except ValueError:
        return clause.split()


def _strip_token_quotes(tok: str) -> str:
    s = tok.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        inner = s[1:-1]
        return inner.replace('\\"', '"') if s[0] == '"' else inner.replace("''", "'")
    return s


def _unquote_split_string_payload(inner: str) -> str:
    """剥离 Windows shlex(posix=False) 拆解 --split-string="..." 后残留的外层成对引号。"""
    s = inner.strip()
    if len(s) >= 2 and s[0] in ("'", '"'):
        q = s[0]
        if s[-1] == q:
            return _strip_token_quotes(s)
        end = s.find(q, 1)
        if end != -1:
            return _strip_token_quotes(s[: end + 1]) + s[end + 1 :]
    return s


_ENCODED_CMD_BLOCKED_SENTINEL = "\x00__BLOCKED_ENCODED_COMMAND__"


def _unwrap_clause_or_shell(tokens: List[str]) -> Tuple[List[str], Optional[str]]:
    """剥离前导 env/sudo/command/timeout/wsl/& 等包装；若遇到 bash -lc / powershell -Command 等壳包装则返回内层脚本供递归分类。"""
    idx = 0
    n = len(tokens)
    while idx < n:
        head_raw = _strip_token_quotes(tokens[idx])
        head_name = Path(head_raw.replace("\\", "/")).name.lower()
        if head_raw == "&":
            idx += 1
            continue
        if head_name == "command":
            idx += 1
            while idx < n and _strip_token_quotes(tokens[idx]).startswith("-"):
                fl = _strip_token_quotes(tokens[idx])
                idx += 1
                if fl == "--":
                    break
                if fl in ("-v", "-V") or (not fl.startswith("--") and any(c in fl[1:] for c in ("v", "V"))):
                    return [], None
            continue
        if head_name == "env":
            idx += 1
            while idx < n and _strip_token_quotes(tokens[idx]).startswith("-"):
                fl = _strip_token_quotes(tokens[idx])
                idx += 1
                if fl == "--":
                    break
                if fl.startswith("--split-string="):
                    inner = fl.split("=", 1)[1]
                    if idx < n:
                        inner += " " + " ".join(tokens[idx:])
                    return [], _unquote_split_string_payload(inner)
                if fl in ("-S", "--split-string"):
                    if idx >= n:
                        return [], ""
                    rest = [_strip_token_quotes(tokens[idx])] + list(tokens[idx + 1 :])
                    return [], " ".join(rest)
                if fl in ("-u", "--unset", "-C", "--chdir") and idx < n:
                    idx += 1
            continue
        if head_name == "nohup":
            idx += 1
            continue
        if head_name == "nice":
            idx += 1
            while idx < n and _strip_token_quotes(tokens[idx]).startswith("-"):
                fl = _strip_token_quotes(tokens[idx])
                idx += 1
                if fl == "--":
                    break
                if fl in ("-n", "--adjustment") and idx < n:
                    idx += 1
            continue
        if head_name == "uv" and idx + 1 < n:
            j = idx + 1
            while j < n and _strip_token_quotes(tokens[j]).startswith("-"):
                j += 2 if _strip_token_quotes(tokens[j]) in ("--directory", "--project", "--config-file") and j + 1 < n else 1
            if j < n and _strip_token_quotes(tokens[j]).lower() == "run":
                j += 1
                uv_vals = ("--python", "-p", "--with", "--with-editable", "--with-requirements",
                           "--directory", "--project", "--env-file", "--package")
                while j < n and _strip_token_quotes(tokens[j]).startswith("-"):
                    fl = _strip_token_quotes(tokens[j])
                    j += 1
                    if fl == "--":
                        break
                    if fl in uv_vals and j < n:
                        j += 1
                idx = j
                continue
        if head_name == "sudo":
            idx += 1
            while idx < n and _strip_token_quotes(tokens[idx]).startswith("-"):
                fl = _strip_token_quotes(tokens[idx])
                idx += 1
                if fl == "--":
                    break
                if fl in ("-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-r", "--role", "-t", "--type", "-C", "--close-from", "-T", "--command-timeout", "-D", "--chdir", "-R", "--chroot") and idx < n:
                    idx += 1
            continue
        if "=" in head_raw and not head_raw.startswith("-") and "/" not in head_raw and "\\" not in head_raw:
            idx += 1
            continue
        if head_name in ("timeout", "timeout.exe"):
            idx += 1
            while idx < n and _strip_token_quotes(tokens[idx]).startswith("-"):
                flag_t = _strip_token_quotes(tokens[idx])
                idx += 1
                if flag_t in ("-k", "--kill-after", "-s", "--signal") and idx < n:
                    idx += 1
            if idx < n:
                idx += 1
            continue
        if head_name in SUBSYSTEM_WRAPPERS:
            idx += 1
            while idx < n and _strip_token_quotes(tokens[idx]).startswith("-"):
                flag = _strip_token_quotes(tokens[idx])
                idx += 1
                if flag in ("--", "-e", "--exec"):
                    break
                if flag in ("-d", "--distribution", "-u", "--user", "--cd") and idx < n:
                    idx += 1
            continue
        if head_name in SHELL_WRAPPERS:
            shell_idx = idx
            idx += 1
            is_cmd_shell = head_name in ("cmd", "cmd.exe")
            is_ps_shell = head_name in ("pwsh", "pwsh.exe", "powershell", "powershell.exe")
            is_posix_shell = head_name in ("bash", "sh", "zsh")
            while idx < n:
                raw_flag = _strip_token_quotes(tokens[idx])
                fl = raw_flag.lower()
                if (
                    is_ps_shell
                    and fl in (
                        "-executionpolicy", "-ep", "-windowstyle", "-w", "-inputformat",
                        "-outputformat", "-configurationname", "-workingdirectory", "-version", "-v",
                    )
                    and idx + 1 < n
                ):
                    idx += 2
                    continue
                if is_posix_shell and (
                    raw_flag in ("-o", "+o", "-O", "+O")
                    or (len(raw_flag) > 2 and raw_flag[0] == "-" and raw_flag[1] != "-" and raw_flag[-1] in ("o", "O") and "c" not in raw_flag[1:])
                ) and idx + 1 < n:
                    idx += 2
                    continue
                if is_ps_shell and (fl == "-ec" or (fl.startswith("-e") and "-encodedcommand".startswith(fl))):
                    idx += 1
                    if idx >= n:
                        return [], _ENCODED_CMD_BLOCKED_SENTINEL
                    raw_b64_tok = tokens[idx]
                    b64_str = _strip_token_quotes(raw_b64_tok).strip()
                    extra_tail = [_strip_token_quotes(t).strip() for t in tokens[idx + 1 :] if _strip_token_quotes(t).strip()]
                    if any(
                        not re.fullmatch(r"(?:\d+)?(?:>&[12]|>&-)|(?:&>{1,2}|\d*>{1,2})(?:/dev/null|\$null|nul)?", t, re.I)
                        for t in extra_tail
                    ):
                        return [], _ENCODED_CMD_BLOCKED_SENTINEL
                    if (
                        not b64_str
                        or "$" in raw_b64_tok
                        or "$" in b64_str
                        or re.search(r"%[A-Za-z_][A-Za-z_0-9]*%", raw_b64_tok)
                    ):
                        return [], _ENCODED_CMD_BLOCKED_SENTINEL
                    try:
                        raw_bytes = base64.b64decode(b64_str, validate=True)
                        if not raw_bytes or len(raw_bytes) % 2 != 0:
                            return [], _ENCODED_CMD_BLOCKED_SENTINEL
                        decoded_script = raw_bytes.decode("utf-16-le")
                    except (binascii.Error, ValueError, UnicodeDecodeError):
                        return [], _ENCODED_CMD_BLOCKED_SENTINEL
                    if not decoded_script.strip() or "\x00" in decoded_script:
                        return [], _ENCODED_CMD_BLOCKED_SENTINEL
                    return [], decoded_script
                if (
                    (is_cmd_shell and fl in ("/c", "/k"))
                    or (is_ps_shell and fl.startswith("-c") and "-command".startswith(fl))
                    or (is_posix_shell and bool(re.fullmatch(r"-[a-z]*c", fl)))
                ):
                    idx += 1
                    if idx >= n:
                        return [], ""
                    if n - idx == 1:
                        return [], _strip_token_quotes(tokens[idx])
                    rest = [_strip_token_quotes(tokens[idx])] + list(tokens[idx + 1 :])
                    return [], " ".join(rest)
                if fl.startswith("-") or (is_posix_shell and raw_flag.startswith("+") and len(raw_flag) > 1) or (is_cmd_shell and fl.startswith("/")):
                    idx += 1
                    continue
                break
            idx = shell_idx
            break
        break
    return tokens[idx:], None


def _git_subcommand(tokens: List[str]) -> Tuple[Optional[str], List[str]]:
    """跳过 git 全局选项（如 -C dir, --no-pager）提取真实子命令名"""
    i = 1
    n = len(tokens)
    value_opts = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
    while i < n:
        tok = _strip_token_quotes(tokens[i])
        if tok in value_opts:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok.lower(), [_strip_token_quotes(t) for t in tokens[i + 1 :]]
    return None, []


_strip_quoted_segments = _shell_lex.strip_quoted_segments


_extract_cmd_substitutions = _shell_lex.extract_cmd_substitutions


def _git_branch_is_readonly(subargs: List[str]) -> bool:
    """仅放行明确的列举选项，未知选项和创建/修改分支均按写操作处理。"""
    flags = {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "-l",
             "--show-current", "--no-abbrev", "--abbrev", "--color", "--no-color", "--column",
             "--no-column", "-i", "--ignore-case"}
    selectors = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at"}
    valued = {"--sort", "--format"}
    equals = selectors | valued | {"--abbrev", "--color", "--column"}
    mutations = {"--unset-upstream", "--set-upstream-to", "-u", "--track", "--no-track",
                 "--edit-description", "-f", "--force", "-d", "-D", "-m", "-M", "-c", "-C",
                 "--delete", "--move", "--copy"}
    if any(arg.split("=", 1)[0] in mutations or (arg.startswith("-u") and not arg.startswith("--")) for arg in subargs):
        return False
    explicit_list = "--list" in subargs or "-l" in subargs
    idx = 0
    while idx < len(subargs):
        arg = subargs[idx]
        if arg in selectors | valued:
            has_value = idx + 1 < len(subargs) and not subargs[idx + 1].startswith("-")
            if arg in valued and not has_value:
                return False
            idx += 2 if has_value else 1
            continue
        if arg in flags or ("=" in arg and arg.split("=", 1)[0] in equals):
            idx += 1
            continue
        if explicit_list and not arg.startswith("-"):
            idx += 1
            continue
        return False
    return True


def _is_readonly_harness_subcommand(sub_tokens: List[str]) -> bool:
    if not sub_tokens or sub_tokens[0] not in ("check-grounding", "assess", "probe", "plan", "--help", "-h", "status"):
        return False
    mutating_flags = ("--output", "--approve-ticket", "--save-ticket", "--mark-handed-off")
    return not any(tok in mutating_flags or any(tok.startswith(f + "=") for f in mutating_flags) for tok in sub_tokens[1:])


def _classify_run_command(cmd_str: str, is_daemon: bool = False, _depth: int = 0) -> str:
    """
    将 run_command 分类为：
    - blocked_always: cae_harness 新派工、git push、IsDaemon 常驻进程
    - readonly_inspect: 全部子句为纯只读核对（含 git status/diff/log/show 等）
    - git_local_commit: 含 git add/commit 且无外部写操作与 push
    - mutating_other: 其他会改动/运行测试/脚本的命令
    """
    if is_daemon or cmd_str == _ENCODED_CMD_BLOCKED_SENTINEL:
        return "blocked_always"
    if not cmd_str or not cmd_str.strip():
        return "mutating_other"
    if _depth > 3:
        return "mutating_other"

    has_git_commit_or_add = False
    has_mutating = False

    clauses = _split_shell_clauses(cmd_str)
    if r'\"' in cmd_str:
        for extra_cl in _shell_lex.split_shell_clauses(cmd_str, backslash_escape=False):
            if extra_cl not in clauses:
                clauses.append(extra_cl)
    for clause in clauses:
        sub_exprs = list(_extract_cmd_substitutions(clause))
        if r'\"' in clause or r'\$' in clause:
            for ps_sub in _shell_lex.extract_cmd_substitutions(clause, backslash_escape=False):
                if ps_sub not in sub_exprs:
                    sub_exprs.append(ps_sub)
        for sub_expr in sub_exprs:
            sub_expr_kind = _classify_run_command(sub_expr, is_daemon=False, _depth=_depth + 1)
            if sub_expr_kind == "blocked_always":
                return "blocked_always"
            if sub_expr_kind == "mutating_other":
                has_mutating = True
            elif sub_expr_kind == "git_local_commit":
                has_git_commit_or_add = True

        clause = re.sub(
            r"^(?:(?:if|then|else|elif|while|until|do)\b(?:\s+|$)|!\s+)+",
            "",
            clause.strip(),
            flags=re.IGNORECASE,
        ).strip()
        if not clause or clause.lower() in ("fi", "done", "esac"):
            continue

        unquoted = _strip_quoted_segments(clause)
        stripped_redirs = re.sub(
            r"(?:\d+)?(?:>&[12]|>&-)|(?:&>{1,2}|\d*>{1,2})\s*(?:/dev/null|\$null|nul)(?![A-Za-z0-9_./\\])",
            "",
            unquoted,
            flags=re.IGNORECASE,
        )
        has_redir = bool(re.search(r"(?:>>|>|\bout-file\b|\bset-content\b|\badd-content\b|\btee\b)", stripped_redirs, re.IGNORECASE))

        tokens, inner_script = _unwrap_clause_or_shell(_safe_split_clause_tokens(clause))
        if inner_script is not None:
            sub_kind = _classify_run_command(inner_script, is_daemon=False, _depth=_depth + 1)
            if sub_kind == "blocked_always":
                return "blocked_always"
            if sub_kind == "mutating_other" or has_redir:
                has_mutating = True
                continue
            if sub_kind == "git_local_commit":
                has_git_commit_or_add = True
                continue
            continue

        if not tokens:
            continue

        raw_head = _strip_token_quotes(tokens[0])
        head_file = Path(raw_head.replace("\\", "/")).name.lower()
        head = head_file[:-4] if head_file.endswith(".exe") else head_file
        if any(is_image_path(_strip_token_quotes(argument)) for argument in tokens[1:]) and head in (
            "cat", "type", "get-content", "gc", "head", "tail", "xxd", "display", "open", "start",
        ):
            return "blocked_always"

        if head_file == "cae_harness.py":
            sub_tokens = [_strip_token_quotes(st) for st in tokens[1:]]
            if not has_redir and _is_readonly_harness_subcommand(sub_tokens):
                continue
            if sub_tokens and sub_tokens[0] in ("check-grounding", "assess", "probe", "plan", "--help", "-h", "status"):
                has_mutating = True
                continue
            return "blocked_always"

        if head == "git":
            subcmd, subargs = _git_subcommand(tokens)
            if subcmd == "push":
                return "blocked_always"
            writes_output = any(arg == "--output" or arg.startswith("--output=") for arg in subargs)
            if subcmd in GIT_READONLY_SUBCMDS and not has_redir and not writes_output:
                continue
            if subcmd == "branch" and _git_branch_is_readonly(subargs) and not has_redir:
                continue
            if subcmd in GIT_LOCAL_COMMIT_SUBCMDS and not has_redir:
                has_git_commit_or_add = True
                continue
            has_mutating = True
            continue

        if head in READONLY_BINARIES and not has_redir:
            find_unsafe = ("-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint", "-fprintf", "-fls")
            if head == "find" and any(_strip_token_quotes(a) in find_unsafe for a in tokens[1:]):
                has_mutating = True
            else:
                continue

        if head == "py" or head.startswith("python"):
            py_args = tokens[1:]
            idx_arg = 0
            saw_inline_or_mod = False
            while idx_arg < len(py_args):
                tok_arg = _strip_token_quotes(py_args[idx_arg])
                if tok_arg == "--":
                    idx_arg += 1
                    break
                if (
                    tok_arg == "-m"
                    or (tok_arg.startswith("-m") and not tok_arg.startswith("--"))
                    or bool(re.match(r"^-[BbIdEisSuqvVxOP]*c", tok_arg))
                ):
                    saw_inline_or_mod = True
                    break
                if tok_arg in ("-X", "-W", "-Q", "--check-hash-based-pycs"):
                    idx_arg += 2
                    continue
                if tok_arg == "--safe-path" or tok_arg.startswith(("-X", "-W", "-Q", "--check-hash-based-pycs=")):
                    idx_arg += 1
                    continue
                if tok_arg.startswith("-") and tok_arg != "-":
                    idx_arg += 1
                    continue
                break
            if saw_inline_or_mod:
                if re.search(r"cae_harness", clause, re.IGNORECASE):
                    return "blocked_always"
                has_mutating = True
                continue
            if idx_arg < len(py_args):
                script_name = Path(_strip_token_quotes(py_args[idx_arg]).replace("\\", "/")).name.lower()
                if script_name == "cae_harness.py":
                    sub_tokens = [_strip_token_quotes(st) for st in py_args[idx_arg + 1 :]]
                    if not has_redir and _is_readonly_harness_subcommand(sub_tokens):
                        continue
                    if sub_tokens and sub_tokens[0] in ("check-grounding", "assess", "probe", "plan", "--help", "-h", "status"):
                        has_mutating = True
                        continue
                    return "blocked_always"

        has_mutating = True

    if has_mutating:
        return "mutating_other"
    if has_git_commit_or_add:
        return "git_local_commit"
    return "readonly_inspect"


# ④ 交接文档快照、基线与结构校验

def _is_handoff_target(
    raw_path: str,
    conversation_id: str,
    cwd: Optional[str],
    workspace_paths: Optional[List[str]],
) -> bool:
    if not raw_path or not raw_path.strip():
        return False
    target = _resolve_candidate_path(raw_path, cwd=cwd, workspace_paths=workspace_paths)
    norm = str(target).replace("\\", "/")
    norm_low = norm.lower()
    name = target.name

    # 允许 Antigravity 当前对话 brain Artifact 目录写交接文档（排除系统文件与媒体上传目录）
    if conversation_id and f"/brain/{conversation_id.lower()}/" in norm_low:
        if "/.system_generated/" in norm_low or "/.user_uploaded/" in norm_low:
            return False
        if name.endswith("_user_words.md"):
            return False
        if target.suffix.lower() in {".md", ".txt", ".json"}:
            return True

    # 项目目录内交接文件仅放行纯文本文档后缀（严禁将 src/resume_solver.py 或 handoff/solver.cpp 等源码误判为交接文档）
    if target.suffix.lower() not in {".md", ".markdown", ".txt", ".rst"}:
        return False

    if "/handoff/" in norm_low or norm_low.endswith("/handoff"):
        return True
    if HANDOFF_FILENAME_RE.search(name):
        return True
    return False


def _handoff_document_snapshot(path: Path, *, include_text: bool = False) -> Optional[Dict[str, Any]]:
    try:
        resolved = str(path.resolve())
        if not path.exists():
            return {"path": resolved, "sha256": ""}
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(MAX_HANDOFF_DOCUMENT_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(data) > MAX_HANDOFF_DOCUMENT_BYTES:
            return None
        current = path.stat()
        if (str(path.resolve()) != resolved
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino)):
            return None
        snapshot = {"path": resolved, "sha256": hashlib.sha256(data).hexdigest()}
        if include_text:
            snapshot["text"] = data.decode("utf-8-sig")
        return snapshot
    except (OSError, ValueError, RuntimeError):
        return None


def _record_handoff_baseline(state: Dict[str, Any], path: Path) -> None:
    baselines = state.setdefault("handoffFileBaselines", {})
    key = str(path.absolute())
    if key not in baselines:
        snapshot = _handoff_document_snapshot(path)
        if snapshot is not None:
            baselines[key] = snapshot


def _has_valid_handoff_file(gate_state: Dict[str, Any], tickets=None) -> bool:
    for raw_path, baseline in gate_state.get("handoffFileBaselines", {}).items():
        current = _handoff_document_snapshot(Path(raw_path), include_text=True)
        if (isinstance(baseline, dict) and current and current.get("path") == baseline.get("path")
                and current.get("sha256") != baseline.get("sha256")
                and _handoff_structure_present(current.get("text") or "")
                and _workflow_handoff_present(current.get("text") or "", tickets or [])):
            return True
    return False


def _workflow_handoff_present(text: str, tickets) -> bool:
    if not tickets:
        return True
    # 只核验提示词正文，正文之前提到票据或在其他章节列矩阵不能代替接力。
    lines = text.splitlines()
    start = None
    inline = ""
    for i, line in enumerate(user_words.markdown_unquoted_lines(text)):
        heading, *tail = re.split(r"[:：]", line, maxsplit=1)
        heading = heading.strip("#*【】[] \t")
        heading = re.sub(r"^(?:[-*+]\s+|\(?[0-9一二三四五六七八九十]+[.)、．]\s*)", "", heading).strip("#*【】[] \t")
        if PROMPT_MARKER_RE.fullmatch(heading):
            start, inline = i, tail[0] if tail else ""
            break
    if start is None:
        return False
    body_lines = [inline]
    fence = ""
    for line in lines[start + 1:]:
        match = user_words.MARKDOWN_FENCE_RE.match(line)
        if match:
            if not fence:
                fence = match[1]
            elif match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ""
        if (not fence and re.match(r"^\s*#{1,6}\s+", line)
                and not re.match(r"^\s*#### 流 .+（14 项完整参数明细）\s*$", line)):
            break
        body_lines.append(line)
    def normalize(value):
        return re.sub(r"\s+", "", str(value).replace(r"\|", "|"))

    body = "\n".join(body_lines)
    normalized = normalize(body)
    columns = ["流号", "子任务与范围", "通道", "客户端及子代理类型", "模型名字", "挡位", "上下文窗口",
               "是否无头", "是否净室", "访问模式、写入范围与验收命令", "内联与超时", "当前是否可用",
               "本机配置额度类型与当前状态", "回执或子代理日志位置"]
    rows, overview_rows, vertical = [], [], {}

    def finish_vertical():
        if all(column in vertical for column in columns):
            rows.append([vertical[column] for column in columns])

    for line in body_lines:
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = re.split(r"(?<!\\)\|", line.strip())[1:-1]
            if len(cells) == 14:
                rows.append([normalize(cell) for cell in cells])
            elif len(cells) == 4:
                norm_cells = [normalize(cell) for cell in cells]
                if norm_cells[0] not in ("流号", "---") and not all(re.fullmatch(r":?-{2,}:?", c or "") for c in norm_cells):
                    overview_rows.append(norm_cells)
            elif len(cells) == 2 or (len(cells) == 3 and re.fullmatch(r"\s*(?:[1-9]|1[0-4])\s*", cells[0])):
                key, value = cells[-2].strip(), normalize(cells[-1])
                if key == "流号":
                    finish_vertical()
                    vertical = {}
                if key in columns:
                    vertical[key] = value
    finish_vertical()

    def contains(cell, value):
        return value in (None, "", []) or normalize(value) in cell

    def boolean(cell, value):
        if not isinstance(value, bool):
            return True
        return ("是" if value else "否") in cell

    def _matches_full_streams(ticket_streams) -> bool:
        for stream in ticket_streams:
            matched = False
            for row in rows:
                if row[0] != normalize(stream.get("stream_id")):
                    continue
                checks = [(1, "subtask"), (1, "target"), (2, "channel"), (3, "client"), (3, "agent_type"),
                          (5, "tier"), (6, "context_window"), (9, "access_mode"), (9, "verify_cmd"),
                          (10, "timeout"), (11, "unavailable_reason"), (12, "quota_scheme"),
                          (12, "quota_summary"), (13, "receipt_location")]
                if not all(contains(row[index], stream.get(key)) for index, key in checks):
                    continue
                if not contains(row[4], stream.get("model_display") or stream.get("model")):
                    continue
                if not all(boolean(row[index], stream.get(key)) for index, key in
                           ((7, "headless"), (8, "clean_room"), (10, "inline_readonly"), (11, "currently_available"))):
                    continue
                if not all(contains(row[9], scope) for scope in stream.get("write_scope", [])):
                    continue
                matched = True
                break
            if not matched:
                return False
        return True

    used_overview_indices: set[int] = set()

    def _matches_compact_card_handoff(ticket_obj) -> bool:
        detail_file = str(ticket_obj.get("card_detail_file") or "").strip()
        if not detail_file:
            return False
        detail_name = user_words.basename_any_os(detail_file)
        tid = str(ticket_obj.get("ticket_id") or "").strip()
        if not detail_name or not contains(normalized, detail_name):
            return False
        if not tid or not contains(normalized, f"plan --resume-ticket {tid}"):
            return False
        claimed_here: List[int] = []
        for stream in ticket_obj.get("streams", []):
            matched_idx = None
            for idx_row, row in enumerate(overview_rows):
                if idx_row in used_overview_indices or idx_row in claimed_here:
                    continue
                if row[0] != normalize(stream.get("stream_id")):
                    continue
                if not all(contains(row[1], stream.get(key)) for key in ("channel", "client", "agent_type")):
                    continue
                if not contains(row[2], stream.get("model_display") or stream.get("model")):
                    continue
                if not contains(row[2], stream.get("tier")):
                    continue
                if not boolean(row[3], stream.get("currently_available")):
                    continue
                if not all(contains(row[3], stream.get(key)) for key in ("unavailable_reason", "quota_scheme", "quota_summary")):
                    continue
                matched_idx = idx_row
                break
            if matched_idx is None:
                return False
            claimed_here.append(matched_idx)
        used_overview_indices.update(claimed_here)
        return True

    for ticket in tickets:
        for value in (ticket.get("ticket_id"), ticket.get("workflow_bg") or ticket.get("workflow_background"),
                      ticket.get("workflow_goal"), ticket.get("workflow_impact")):
            if not contains(normalized, value):
                return False
        if not all(option in normalized for option in ("①", "②", "③", "批准当前计划", "微调", "暂缓", "交接")):
            return False
        ledger = ticket.get("capability_ledger") or {}
        if any(not contains(normalized, ledger.get(key)) for key in ("last_online_refresh_at", "local_bench_verified_at")):
            return False
        if not (_matches_full_streams(ticket.get("streams", [])) or _matches_compact_card_handoff(ticket)):
            return False
    return True


def _workflow_handoff_notice(tickets) -> str:
    if not tickets:
        return ""
    all_persisted = all(isinstance(t, dict) and str(t.get("card_detail_file") or "").strip() for t in tickets)
    if all_persisted:
        lead = (
            "\n必须将以下未决重负载工作流的背景、要做什么、预计后果、精简 4 列总览表、"
            "完整 14 项明细链接、离线性能更新时间、恢复命令及选项卡写入【新对话提示词】，"
            "由新对话读取磁盘票据再请用户确认（勿在对话中展开 14 项长表）。\n"
        )
    else:
        lead = (
            "\n必须将以下未决重负载工作流的背景、要做什么、预计后果、14项全字段参数表、"
            "离线性能更新时间及选项卡完整写入【新对话提示词】，由新对话读取磁盘票据再请用户确认。\n"
        )
    return lead + "\n\n".join(user_words.render_pending_workflow_block(ticket, for_handoff=True) for ticket in tickets)


def _reply_has_handoff_prompt(reply_text: str) -> bool:
    lines = reply_text.rstrip().splitlines()
    plain_lines = list(user_words.markdown_unquoted_lines(reply_text.rstrip()))
    for idx, line in enumerate(plain_lines):
        heading, *body = re.split(r"[:：]", line, maxsplit=1)
        cleaned_heading = heading.strip("#*【】[] \t")
        cleaned_heading = re.sub(
            r"^(?:[-*+]\s+|\(?[0-9一二三四五六七八九十]+[.)、．]\s*)",
            "",
            cleaned_heading,
        ).strip("#*【】[] \t")
        if not PROMPT_MARKER_RE.fullmatch(cleaned_heading):
            continue
        if body and len(body[0].strip()) >= 15:
            return True
        non_empty_seen = 0
        for pos in range(idx + 1, len(lines)):
            if not lines[pos].strip():
                continue
            if lines[pos].lstrip().startswith("#"):
                break
            fence = user_words.MARKDOWN_FENCE_RE.match(lines[pos])
            if fence:
                content = []
                for body_line in lines[pos + 1:]:
                    ending = user_words.MARKDOWN_FENCE_RE.match(body_line)
                    if ending and ending[1][0] == fence[1][0] and len(ending[1]) >= len(fence[1]) and not ending[2].strip():
                        return len("\n".join(content).strip()) >= 15
                    content.append(body_line)
                return False
            non_empty_seen += 1
            candidate = plain_lines[pos].strip()
            if len(candidate) >= 15 and not lines[pos].lstrip().startswith(">") and not candidate.endswith((":", "：")):
                return True
            if non_empty_seen <= 2 and candidate.endswith((":", "：")):
                continue
            break
    return False


def _handoff_structure_present(text: str) -> bool:
    """核对交接文档结构，内容是否属实仍需对照源证据。"""
    prose = "\n".join(user_words.markdown_unquoted_lines(text))
    return (_reply_has_handoff_prompt(text)
            and bool(re.search(r"已验证|已完成|进展|verified|completed", prose, re.IGNORECASE))
            and bool(re.search(r"未完成|待办|下一步|剩余|remaining|next steps", prose, re.IGNORECASE))
            and bool(re.search(r"git|提交|工作区|workspace", prose, re.IGNORECASE)))


# ⑤ Antigravity 遥测、阶段迁移与工具门禁

def agy_handoff_intent(user_text: str) -> str:
    """识别独立指令句，按最后一条直接指令处理，忽略引用与代码。"""
    intent = ""
    for line in user_words.markdown_unquoted_lines(user_text):
        if AGY_OVERRIDE_REQUEST_RE.fullmatch(line):
            intent = "override"
        elif AGY_HANDOFF_REQUEST_RE.fullmatch(line):
            intent = "handoff"
    return intent


def agy_banner_report(user_text: str) -> bool:
    return any(AGY_BANNER_REPORT_RE.fullmatch(line) for line in user_words.markdown_unquoted_lines(user_text))


def _merge_telemetry(
    compaction_state: Dict[str, Any],
    gate_state: Dict[str, Any],
    transcript_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """整合 transcript 增量遥测与实时 PreToolUse 图片读数"""
    cp_steps_raw = compaction_state.get("checkpointSteps") or []
    cp_steps = sorted({int(x) for x in cp_steps_raw if isinstance(x, int) and x >= 0})
    cp_count = len(cp_steps)

    intervals = [cp_steps[i] - cp_steps[i - 1] for i in range(1, len(cp_steps)) if cp_steps[i] > cp_steps[i - 1]]
    recent_avg_interval: Optional[float] = None
    if len(intervals) >= 3:
        tail3 = intervals[-3:]
        recent_avg_interval = sum(tail3) / len(tail3)

    transcript_bytes = 0
    if transcript_path is not None:
        transcript_path = user_words.resolve_compact_transcript_path(transcript_path)
        if transcript_path.exists():
            try:
                transcript_bytes = int(transcript_path.stat().st_size)
            except OSError:
                transcript_bytes = 0

    recorded_imgs = compaction_state.get("transcriptImageSizes") or {}
    img_map: Dict[str, int] = {}
    for source in (recorded_imgs, compaction_state.get("transcriptImageAttemptSizes") or {}, gate_state["imageReads"]):
        for key, size in source.items():
            if isinstance(size, int) and size >= 0 and (key in img_map or len(img_map) < MAX_TRACKED_IMAGES):
                img_map[key] = max(size, img_map.get(key, 0))

    comp_img_calls = int(compaction_state.get("transcriptImageCallCount", 0))
    gate_img_calls = int(gate_state.get("imageReadCallCount", 0))
    result_count = int(compaction_state.get("transcriptImageResultCount", 0))
    image_attempt_count = max(comp_img_calls, gate_img_calls)
    image_call_count = max(image_attempt_count, result_count, len(img_map))
    total_image_bytes = sum(img_map.values())
    media_step_count = int(compaction_state.get("mediaStepCount", 0))

    latest_step = max(
        int(compaction_state.get("latestStepIndex", -1)),
        cp_steps[-1] if cp_steps else -1,
    )

    return {
        "cpCount": cp_count,
        "latestCp": cp_steps[-1] if cp_steps else -1,
        "recentAvgInterval": recent_avg_interval,
        "transcriptBytes": transcript_bytes,
        "mediaStepCount": media_step_count,
        "uniqueImageCount": len(img_map),
        "imageCallCount": image_call_count,
        "imageAttemptCount": image_attempt_count,
        "imageResultCount": result_count,
        "recordedImageBytes": sum(v for v in recorded_imgs.values() if isinstance(v, int) and v >= 0),
        "totalImageBytes": total_image_bytes,
        "latestStep": latest_step,
        "latestUserInputHash": str(compaction_state.get("latestUserInputHash") or ""),
        "latestUserInputPreview": str(compaction_state.get("latestUserInputPreview") or ""),
        "latestUserHandoffIntent": compaction_state.get("latestUserHandoffIntent"),
        "latestHandoffStepIndex": compaction_state.get("latestHandoffStepIndex"),
        "latestHandoffInputHash": compaction_state.get("latestHandoffInputHash"),
        "latestUserBannerReport": compaction_state.get("latestUserBannerReport"),
        "latestUserStepIndex": int(compaction_state.get("latestUserStepIndex", -1)),
        "hostBannerStep": int(compaction_state.get("hostBannerStep", -1)),
        "isGoalActive": bool(compaction_state.get("isGoalActive", False)),
    }


def _evaluate_triggers(tel: Dict[str, Any], user_text: str) -> Tuple[List[str], List[str]]:
    """计算当前满足的 Stage-1 (advisory) 与 Stage-2 (drain) 触发条件列表"""
    _sync_handoff_tunables()
    advisory_hits: List[str] = []
    stage2_hits: List[str] = []

    cp_count = tel["cpCount"]
    avg_int = tel["recentAvgInterval"]
    tp_bytes = tel["transcriptBytes"]
    media_steps = tel["mediaStepCount"]
    img_calls = tel["imageCallCount"]
    img_bytes = tel["totalImageBytes"]
    latest_step = tel["latestStep"]

    # Stage 1 (零阻断温控预警)
    if cp_count >= ADVISORY_CP_THRESHOLD:
        advisory_hits.append("cp_warning")
    if tp_bytes >= ADVISORY_TRANSCRIPT_BYTES:
        advisory_hits.append("transcript_warning")
    if cp_count >= ADVISORY_THRASH_MIN_CP and avg_int is not None and avg_int <= ADVISORY_THRASH_AVG_INTERVAL:
        advisory_hits.append("interval_warning")
    if (cp_count >= ADVISORY_THRASH_MIN_CP and media_steps >= ADVISORY_MEDIA_STEPS) or img_calls >= ADVISORY_IMAGE_CALLS:
        advisory_hits.append("media_warning")
    if img_bytes >= ADVISORY_IMAGE_BYTES:
        advisory_hits.append("image_bytes_warning")

    # Stage 2 (强收口硬门禁)
    user_report = tel.get("latestUserBannerReport")
    if user_report is None:
        user_report = agy_banner_report(user_text)
    if user_report:
        banner_plausible = (
            cp_count >= BANNER_PLAUSIBLE_CP
            or latest_step >= BANNER_PLAUSIBLE_STEP
            or tp_bytes >= BANNER_PLAUSIBLE_TRANSCRIPT_BYTES
            or media_steps >= BANNER_PLAUSIBLE_MEDIA_STEPS
            or img_calls >= BANNER_PLAUSIBLE_IMAGE_CALLS
            or img_bytes >= BANNER_PLAUSIBLE_IMAGE_BYTES
        )
        if banner_plausible:
            stage2_hits.append("user_banner_report")

    if cp_count >= DRAIN_CP_THRESHOLD:
        stage2_hits.append("cp_count")
    if cp_count >= DRAIN_THRASH_MIN_CP and avg_int is not None and avg_int <= DRAIN_THRASH_AVG_INTERVAL:
        stage2_hits.append("cp_thrashing")
    if tp_bytes >= DRAIN_TRANSCRIPT_BYTES:
        stage2_hits.append("transcript_bytes")
    if img_bytes >= DRAIN_IMAGE_BYTES:
        stage2_hits.append("media_bytes")
    if (media_steps >= DRAIN_MEDIA_STEPS and cp_count >= DRAIN_MEDIA_MIN_CP) or img_calls >= DRAIN_IMAGE_CALLS:
        stage2_hits.append("media_flood")

    return advisory_hits, stage2_hits


def _format_telemetry_brief(tel: Dict[str, Any]) -> str:
    mb_tp = tel["transcriptBytes"] / (1024 * 1024)
    mb_img = tel["totalImageBytes"] / (1024 * 1024)
    avg_txt = f"{tel['recentAvgInterval']:.0f}" if tel["recentAvgInterval"] is not None else "N/A"
    return (
        f"压缩={tel['cpCount']}次, 近期均值间隔={avg_txt}步, "
        f"transcript={mb_tp:.2f}MB，读图尝试={tel['imageAttemptCount']}次，"
        f"轨迹图片结果={tel['imageResultCount']}步，图片文件估算={mb_img:.1f}MB"
    )


def _build_advisory_message(tel: Dict[str, Any], triggers: List[str]) -> str:
    extra = ""
    if "media_warning" in triggers or "image_bytes_warning" in triggers:
        extra = "；检测到批量读图偏高，请改用已提取文本/脚本核对，避免无谓大图耗尽上下文。"
    return (
        f"【长会话提醒 · Stage-1 advisory】当前会话达到启发式收口预警阈值（{_format_telemetry_brief(tel)}）。"
        f"建议在完成本原子单元并提交本地 Git 后适时整理交接；若非必要请勿新开长耗时探索{extra}"
    )


def _build_drain_message(gate_state: Dict[str, Any], tel: Dict[str, Any]) -> str:
    goal_line = (
        "\n- 注意：检测到当前会话开启了 `/goal`，最终交接输出末尾必须附带 `<!-- GOAL_CANCELLED -->` 以终止循环！"
        if gate_state.get("isGoalActive")
        else ""
    )
    return (
        f"【长会话强收口门禁 · Stage-2 drain】触发源：{', '.join(gate_state.get('triggers') or ['pressure'])} "
        f"（{_format_telemetry_brief(tel)}）。\n"
        f"- 当前进入原子收口排空期（剩余模型调用 {gate_state['drainInvocationsRemaining']} 次 / 写或运行动作 {gate_state['drainActionsRemaining']} 次）：\n"
        "  1. 禁止新开 subagent/run_workflow/swarm/schedule/browser/cae_harness 派工、禁止读图及 `git push`；\n"
        "  2. 仅完成手头未闭环的局部收尾与本地验收，`git add`/`git commit`；\n"
        "  3. 全量整理当前已验证进展、未尽事项与可直接粘贴的【新对话提示词】写入交接文档或在终局回复给出，随后立即停机。"
        + goal_line
    )


def _build_freeze_message(gate_state: Dict[str, Any], tel: Dict[str, Any]) -> str:
    goal_line = " 并在末尾附带 `<!-- GOAL_CANCELLED -->`" if gate_state.get("isGoalActive") else ""
    return (
        f"【长会话强收口冻结 · Stage-2 freeze】触发源：{', '.join(gate_state.get('triggers') or ['pressure'])} "
        f"（{_format_telemetry_brief(tel)}）。\n"
        "- 排空预算已耗尽！当前仅允许：① 非图片文件只读核对 / `manage_task` / `manage_subagents`；② 编写交接档案；③ 本地 `git add`/`git commit`。\n"
        f"- 请立即整理已实测状态、未决问题与【新对话提示词】{goal_line}，直接收尾停机交由新对话接续。"
    )


def _refresh_phase_from_telemetry(
    gate_state: Dict[str, Any],
    tel: Dict[str, Any],
    user_text: str,
    conversation_id: str,
    decrement_invocation: bool = False,
) -> bool:
    """
    根据最新遥测与用户指令刷新门禁状态机，返回 gate_state 是否发生变化。
    """
    changed = False
    prev_phase = str(gate_state.get("phase") or "normal")
    turn_hash = tel.get("latestUserInputHash") or ""
    u_step_idx = int(tel.get("latestUserStepIndex", -1))
    turn_token = f"{u_step_idx}:{turn_hash}" if (turn_hash and u_step_idx >= 0) else turn_hash
    intent = tel.get("latestUserHandoffIntent")
    if intent is None:
        intent = agy_handoff_intent(user_text)
    control_hash = tel.get("latestHandoffInputHash")
    control_step = tel.get("latestHandoffStepIndex")
    if control_step is None:
        control_token, control_hash = turn_token, turn_hash
    else:
        control_token = f"{control_step}:{control_hash}" if control_hash else ""
    new_request = intent == "handoff" and control_token and gate_state.get("handoffRequestTurnHash") != control_token
    host_banner_step = int(tel.get("hostBannerStep", -1))
    host_banner_is_new = host_banner_step > int(gate_state.get("bannerTriggerHostStep", -1))
    advisory_hits, stage2_hits = _evaluate_triggers(tel, user_text)

    # 重新核对旧版单一体积或匿名告警触发，保留其他已登记的触发依据。
    if int(gate_state.get("gateSchemaVersion", 1)) < 3:
        gate_state["gateSchemaVersion"] = 3
        changed = True
        prev_triggers = set(gate_state.get("triggers") or [])
        if (prev_phase in ("advisory", "drain", "freeze") and prev_triggers
                and prev_triggers <= {"transcript_warning", "transcript_bytes", "banner"}):
            if not stage2_hits and not host_banner_is_new:
                prev_phase = "advisory" if advisory_hits else "normal"
                gate_state["phase"] = prev_phase
                gate_state["triggers"] = list(advisory_hits)
                gate_state["drainInvocationsRemaining"] = DEFAULT_DRAIN_INVOCATIONS
                gate_state["drainActionsRemaining"] = DEFAULT_DRAIN_ACTIONS
                gate_state["enteredDrainAtStep"] = None
                gate_state["enteredFreezeAtStep"] = None
                user_words.append_lifecycle_log(
                    HOOKS_DIR,
                    "HandoffGate:recalibrate",
                    conversation_id,
                    f"Recalibrated compact transcript size -> phase={prev_phase} ({_format_telemetry_brief(tel)})",
                )

    if bool(tel.get("isGoalActive")) != bool(gate_state.get("isGoalActive")):
        gate_state["isGoalActive"] = bool(tel.get("isGoalActive"))
        changed = True

    # 1. 检查显式 R8 暂缓指令（绑定步骤号+内容哈希，防止同轮重复消费且允许后续轮次再次输入相同暂缓词）
    if control_token and gate_state.get("overriddenTurnHash") not in (control_token, control_hash) and intent == "override":
        gate_state["phase"] = "overridden"
        gate_state["overriddenTurnHash"] = control_token
        gate_state["overrideUntilCheckpoint"] = int(tel["cpCount"]) + OVERRIDE_SNOOZE_CPS
        gate_state["overrideUntilStep"] = int(tel["latestStep"]) + OVERRIDE_SNOOZE_STEPS
        gate_state["drainInvocationsRemaining"] = DEFAULT_DRAIN_INVOCATIONS
        gate_state["drainActionsRemaining"] = DEFAULT_DRAIN_ACTIONS
        gate_state["bannerTriggerHostStep"] = max(gate_state["bannerTriggerHostStep"], host_banner_step)
        user_words.append_lifecycle_log(
            HOOKS_DIR,
            "HandoffGate:override",
            conversation_id,
            f"R8 explicit user override accepted until cp>={gate_state['overrideUntilCheckpoint']} step>={gate_state['overrideUntilStep']}",
        )
        return True

    # 2. 暂缓期内，新交接指令或新宿主告警优先。
    if prev_phase == "overridden":
        snooze_expired = (
            int(tel["cpCount"]) >= int(gate_state.get("overrideUntilCheckpoint", -1))
            or int(tel["latestStep"]) >= int(gate_state.get("overrideUntilStep", -1))
        )
        new_banner_in_fresh_turn = (
            turn_token
            and gate_state.get("overriddenTurnHash") not in (turn_token, turn_hash)
            and gate_state.get("bannerTriggerTurnHash") not in (turn_token, turn_hash)
            and "user_banner_report" in stage2_hits
        )
        if not (snooze_expired or new_banner_in_fresh_turn or new_request or host_banner_is_new):
            return changed
        # 宽限到期或新报错出现，允许重新进入阈值判定
        gate_state["phase"] = "normal"
        prev_phase = "normal"
        changed = True

    if new_request:
        gate_state["handoffRequestTurnHash"] = control_token
        stage2_hits.append("user_request")
        changed = True
    if host_banner_is_new:
        gate_state["bannerTriggerHostStep"] = host_banner_step
        stage2_hits.append("host_banner")
        changed = True

    # 3. 阶段跃迁与单调冻结演进
    if prev_phase == "freeze":
        for tr in stage2_hits:
            if tr not in gate_state["triggers"]:
                gate_state["triggers"].append(tr)
                changed = True
        return changed

    if prev_phase == "drain":
        for tr in stage2_hits:
            if tr not in gate_state["triggers"]:
                gate_state["triggers"].append(tr)
                changed = True
        if decrement_invocation:
            rem = max(0, int(gate_state.get("drainInvocationsRemaining", 0)) - 1)
            gate_state["drainInvocationsRemaining"] = rem
            changed = True
            if rem <= 0 or int(gate_state.get("drainActionsRemaining", 0)) <= 0:
                gate_state["phase"] = "freeze"
                gate_state["enteredFreezeAtStep"] = tel["latestStep"]
                user_words.append_lifecycle_log(
                    HOOKS_DIR,
                    "HandoffGate:freeze",
                    conversation_id,
                    f"Transitioned drain -> freeze at step={tel['latestStep']} (invocation budget exhausted)",
                )
        return changed

    # 当前为 normal 或 advisory：判定是否升级到 drain 或 advisory
    if stage2_hits:
        gate_state["phase"] = "drain"
        gate_state["triggers"] = list(dict.fromkeys(stage2_hits))
        gate_state["enteredDrainAtStep"] = tel["latestStep"]
        gate_state["handoffFileBaselines"] = {}
        gate_state["drainInvocationsRemaining"] = DEFAULT_DRAIN_INVOCATIONS
        gate_state["drainActionsRemaining"] = DEFAULT_DRAIN_ACTIONS
        if "user_banner_report" in stage2_hits or "host_banner" in stage2_hits:
            gate_state["bannerDetectedAtStep"] = tel["latestStep"]
            gate_state["bannerTriggerTurnHash"] = turn_token
        user_words.append_lifecycle_log(
            HOOKS_DIR,
            "HandoffGate:drain",
            conversation_id,
            f"Entered Stage-2 drain (triggers={gate_state['triggers']}, {_format_telemetry_brief(tel)})",
        )
        return True

    if advisory_hits and prev_phase == "normal":
        gate_state["phase"] = "advisory"
        gate_state["triggers"] = list(dict.fromkeys(advisory_hits))
        user_words.append_lifecycle_log(
            HOOKS_DIR,
            "HandoffGate:advisory",
            conversation_id,
            f"Entered Stage-1 advisory (triggers={gate_state['triggers']}, {_format_telemetry_brief(tel)})",
        )
        return True

    if advisory_hits and prev_phase == "advisory":
        for tr in advisory_hits:
            if tr not in gate_state["triggers"]:
                gate_state["triggers"].append(tr)
                changed = True

    return changed


def evaluate_pre_invocation_locked(
    conversation_id: str,
    event_name: str,
    state_dir: Path,
    compaction_state: Dict[str, Any],
    transcript_path: Optional[Path] = None,
    payload: Optional[Dict[str, Any]] = None,
    model_name: str = "",
    effort: str = "",
) -> Dict[str, Any]:
    """评估遥测并更新门禁。调用方在同一会话锁内提交压缩状态。"""
    if not model_name and not effort:
        model_name, effort = _extract_runtime_model_and_effort("antigravity", payload)
    _sync_handoff_tunables("antigravity", model_name, effort)
    gate_state = load_gate_state(state_dir, conversation_id)
    tel = _merge_telemetry(compaction_state, gate_state, transcript_path=transcript_path)
    user_text = tel["latestUserInputPreview"]

    is_pre_inv = event_name == "PreInvocation"
    changed = _refresh_phase_from_telemetry(
        gate_state,
        tel,
        user_text,
        conversation_id,
        decrement_invocation=is_pre_inv,
    )

    phase = str(gate_state.get("phase") or "normal")
    advisory_notice: Optional[str] = None
    mandate_message: Optional[str] = None

    if phase == "advisory":
        cp_now = int(tel["cpCount"])
        img_milestone = int(tel["imageCallCount"]) // 25
        curr_triggers = list(gate_state.get("triggers") or [])
        prev_adv_triggers = set(gate_state.get("lastAdvisoryTriggers") or [])
        if (
            cp_now > int(gate_state.get("lastAdvisoryCp", -1))
            or img_milestone > int(gate_state.get("lastAdvisoryImageMilestone", 0))
            or bool(set(curr_triggers) - prev_adv_triggers)
        ):
            gate_state["lastAdvisoryCp"] = cp_now
            gate_state["lastAdvisoryImageMilestone"] = img_milestone
            gate_state["lastAdvisoryTriggers"] = curr_triggers
            advisory_notice = _build_advisory_message(tel, curr_triggers)
            changed = True
    elif phase == "drain":
        mandate_message = _build_drain_message(gate_state, tel)
    elif phase == "freeze":
        mandate_message = _build_freeze_message(gate_state, tel)

    workflow_notice_full = False
    if phase in ("drain", "freeze"):
        tickets = user_words.handoff_session_pending_tickets(conversation_id, state_dir)
        if tickets:
            ticket_ids = [str(t.get("ticket_id") or "") for t in tickets]
            cp_now = int(tel["cpCount"])
            need_full_workflow_notice = (
                gate_state.get("lastWorkflowNoticePhase") != phase
                or gate_state.get("lastWorkflowNoticeTicketIds") != ticket_ids
                or cp_now > int(gate_state.get("lastWorkflowNoticeCp", -1))
            )
            if need_full_workflow_notice:
                mandate_message = (mandate_message or "") + _workflow_handoff_notice(tickets)
                gate_state["lastWorkflowNoticePhase"] = phase
                gate_state["lastWorkflowNoticeTicketIds"] = ticket_ids
                gate_state["lastWorkflowNoticeCp"] = cp_now
                workflow_notice_full = True
                changed = True
            else:
                mandate_message = (
                    (mandate_message or "")
                    + "\n[未决工作流交接提醒] 本会话仍有未决票据 "
                    + ", ".join(tid for tid in ticket_ids if tid)
                    + "（精简总览卡与明细链接已在本阶段首次门禁中给出，请完整写入【新对话提示词】并附 `plan --resume-ticket <票据>`）。"
                )

    if changed:
        save_gate_state(state_dir, conversation_id, gate_state)

    return {
        "phase": phase,
        "advisoryNotice": advisory_notice,
        "mandateMessage": mandate_message,
        "workflowNoticeFull": workflow_notice_full,
        "telemetry": tel,
    }


def check_pre_tool_use_locked(payload: Dict[str, Any], state_dir: Path,
                              *, boundary_blocked: bool = False) -> Optional[Dict[str, Any]]:
    """在同一会话锁中检查阶段并更新状态。"""
    conversation_id = str(payload.get("conversationId") or "").strip()
    if not conversation_id:
        return None
    model_name, effort = _extract_runtime_model_and_effort("antigravity", payload)
    _sync_handoff_tunables("antigravity", model_name, effort)

    compaction_state_file = state_dir / f"{conversation_id}_compaction.json"
    compaction_state = {}
    if compaction_state_file.is_file():
        try:
            compaction_state = json.loads(compaction_state_file.read_text(encoding="utf-8-sig"))
            if not isinstance(compaction_state, dict) or compaction_state.get("conversationId") != conversation_id:
                raise ValueError("压缩状态格式或身份错误")
        except (ValueError, OSError, RecursionError) as exc:
            if not _gate_state_path(state_dir, conversation_id).exists():
                raise
            user_words.append_lifecycle_log(HOOKS_DIR, "PreToolUse:state_error", conversation_id,
                                            f"读取压缩状态失败（{exc}），沿用现有门禁阶段")
            compaction_state = {}
    if not compaction_state.get("hasExplicitUserInput", False) and not _gate_state_path(state_dir, conversation_id).exists():
        return None

    gate_state = load_gate_state(state_dir, conversation_id)
    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    raw_tool_name = str(tool_call.get("name") or tool_call.get("tool_name") or "").strip()
    tool_name = AGY_TOOL_ALIASES.get(raw_tool_name, raw_tool_name)
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
    cwd = args.get("Cwd") if tool_name == "run_command" else None
    cwd = cwd or payload.get("cwd")
    workspace_paths = payload.get("workspacePaths") if isinstance(payload.get("workspacePaths"), list) else []
    transcript_str = payload.get("transcriptPath")
    transcript_path = user_words.resolve_compact_transcript_path(Path(transcript_str).expanduser()) if transcript_str else None

    # 对 read_file / view_file 图片读取实时累计计数并检查是否当场突破 Stage-2
    is_image_read = False
    if tool_name == "read_file":
        file_arg = str(
            args.get("AbsolutePath")
            or args.get("absolute_path")
            or args.get("FilePath")
            or args.get("Path")
            or args.get("path")
            or args.get("TargetFile")
            or args.get("target_file")
            or args.get("file_path")
            or ""
        ).strip()
        if is_image_path(file_arg):
            is_image_read = True
            record_image_read(gate_state, file_arg, cwd=cwd, workspace_paths=workspace_paths, increment_calls=True)

    tel = _merge_telemetry(compaction_state, gate_state, transcript_path=transcript_path)
    if not compaction_state:
        tel["isGoalActive"] = gate_state["isGoalActive"]
    changed = _refresh_phase_from_telemetry(
        gate_state,
        tel,
        tel["latestUserInputPreview"],
        conversation_id,
        decrement_invocation=False,
    )

    phase = str(gate_state.get("phase") or "normal")
    if phase not in ("drain", "freeze"):
        if changed or is_image_read:
            save_gate_state(state_dir, conversation_id, gate_state)
        return None

    # 以下为 Stage-2 (drain / freeze) 精确工具门禁
    user_words.handoff_session_pending_tickets(conversation_id, state_dir)
    # 1. 绝不允许的派工与常驻/外推工具（drain 与 freeze 均阻断）
    if tool_name in DELEGATION_TOOLS or tool_name in MEDIA_TOOLS or tool_name.startswith("browser_"):
        save_gate_state(state_dir, conversation_id, gate_state)
        media_note = (
            "收口期禁止看图或生成多模态图片（图片会急剧消耗剩余上下文窗口并加剧压缩丢历史风险），请改用已有文本/脚本输出核对；"
            if tool_name in MEDIA_TOOLS
            else ""
        )
        return {
            "decision": "deny",
            "reason": (
                f"【user-guard 长会话交接门禁 ({phase})】当前会话已进入交接停机收口期（{_format_telemetry_brief(tel)}），"
                f"收口期禁用 `{tool_name}`。{media_note}"
                "请仅完成本地原子收口并整理交接文档与新对话提示词后立即停机。"
            ),
        }

    # 2. manage_task / manage_subagents：允许查看或收尾后台任务（不扣减动作预算）
    if tool_name in AGY_INSPECTION_TOOLS:
        if changed or is_image_read:
            save_gate_state(state_dir, conversation_id, gate_state)
        return None

    # 3. read_file：禁止读图（防耗尽剩余配额），放行文本核对
    if tool_name == "read_file":
        if changed or is_image_read:
            save_gate_state(state_dir, conversation_id, gate_state)
        if is_image_read:
            return {
                "decision": "deny",
                "reason": (
                    f"【user-guard 长会话交接门禁 ({phase})】当前图片遥测（{_format_telemetry_brief(tel)}）。"
                    "收口期内禁止继续调用 `read_file` 看图（多模态图片会急剧占用剩余上下文配额并触发二次压缩），"
                    "请改用文本摘要或脚本输出核对后完成交接。"
                ),
            }
        return None

    # 4. write_file / edit_file：交接文档始终放行；非交接修改在 drain 扣减预算，在 freeze 阻断
    if tool_name in ("write_file", "edit_file"):
        write_arg = str(
            args.get("Path")
            or args.get("path")
            or args.get("AbsolutePath")
            or args.get("absolute_path")
            or args.get("FilePath")
            or args.get("TargetFile")
            or args.get("target_file")
            or args.get("file_path")
            or args.get("NotebookPath")
            or args.get("notebook_path")
            or ""
        ).strip()
        if _is_handoff_target(write_arg, conversation_id, cwd, workspace_paths):
            if boundary_blocked:
                if changed:
                    save_gate_state(state_dir, conversation_id, gate_state)
                return None
            resolved_target = str(_resolve_candidate_path(write_arg, cwd=cwd, workspace_paths=workspace_paths))
            _record_handoff_baseline(gate_state, Path(resolved_target))
            save_gate_state(state_dir, conversation_id, gate_state)
            return None

        if phase == "freeze":
            save_gate_state(state_dir, conversation_id, gate_state)
            return {
                "decision": "deny",
                "reason": (
                    "【user-guard 长会话交接门禁 (freeze)】排空预算已用尽，当前已进入冻结期，禁止修改业务源码或非交接文件！"
                    "请仅编写交接文件、执行本地 `git add`/`git commit` 或直接输出交接总结与新对话提示词后停机。"
                ),
            }

        if boundary_blocked:
            if changed:
                save_gate_state(state_dir, conversation_id, gate_state)
            return None
        # drain 阶段扣减动作预算
        rem_actions = int(gate_state.get("drainActionsRemaining", 0))
        if rem_actions <= 0:
            gate_state["phase"] = "freeze"
            gate_state["enteredFreezeAtStep"] = tel["latestStep"]
            save_gate_state(state_dir, conversation_id, gate_state)
            return {
                "decision": "deny",
                "reason": (
                    f"【user-guard 长会话交接门禁 (drain -> freeze)】收尾动作预算（{DEFAULT_DRAIN_ACTIONS} 次）已用完，已转入冻结阶段。"
                    "禁止继续修改业务文件，请整理交接提示词后结束本轮。"
                ),
            }
        if consume_drain_action(gate_state):
            gate_state["enteredFreezeAtStep"] = tel["latestStep"]
        save_gate_state(state_dir, conversation_id, gate_state)
        return None

    # 5. send_message / post_message / swarm_wait：仅允许在 drain 消耗 1 次动作通知收尾，freeze 冻结阻断
    if tool_name in ("send_message", "post_message", "swarm_wait"):
        if phase == "freeze" or int(gate_state.get("drainActionsRemaining", 0)) <= 0:
            if phase != "freeze":
                gate_state["enteredFreezeAtStep"] = tel["latestStep"]
            gate_state["phase"] = "freeze"
            save_gate_state(state_dir, conversation_id, gate_state)
            return {
                "decision": "deny",
                "reason": f"【user-guard 长会话交接门禁 (freeze)】已冻结，禁止再通过 `{tool_name}` 唤醒或等待后台子代理。请直接整理交接并停机。",
            }
        if consume_drain_action(gate_state):
            gate_state["enteredFreezeAtStep"] = tel["latestStep"]
        save_gate_state(state_dir, conversation_id, gate_state)
        return None

    # 6. run_command：分类管控（始终禁派工/push，始终放行只读与本地 git add/commit）
    if tool_name == "run_command":
        cmd_str = str(args.get("CommandLine") or args.get("command_line") or args.get("command") or "")
        is_daemon = bool(args.get("IsDaemon", False))
        cmd_kind = _classify_run_command(cmd_str, is_daemon=is_daemon)

        if cmd_kind == "blocked_always":
            save_gate_state(state_dir, conversation_id, gate_state)
            return {
                "decision": "deny",
                "reason": (
                    f"【user-guard 长会话交接门禁 ({phase})】收口期禁用派工、推送、读图（防止多模态图片耗尽剩余上下文窗口）和常驻进程。"
                    "仅允许本地非图片只读核对、运行本地验收与本地 `git add`/`git commit`。"
                ),
            }

        if cmd_kind in ("readonly_inspect", "git_local_commit"):
            save_gate_state(state_dir, conversation_id, gate_state)
            return None

        if phase == "freeze":
            save_gate_state(state_dir, conversation_id, gate_state)
            return {
                "decision": "deny",
                "reason": (
                    "【user-guard 长会话交接门禁 (freeze)】已进入冻结期，禁止运行新的构建/批量脚本或修改命令！"
                    "当前仅允许只读检查命令（`git status/diff/log`）与本地 `git add`/`git commit`。"
                ),
            }

        if boundary_blocked:
            if changed:
                save_gate_state(state_dir, conversation_id, gate_state)
            return None
        rem_actions = int(gate_state.get("drainActionsRemaining", 0))
        if rem_actions <= 0:
            gate_state["phase"] = "freeze"
            gate_state["enteredFreezeAtStep"] = tel["latestStep"]
            save_gate_state(state_dir, conversation_id, gate_state)
            return {
                "decision": "deny",
                "reason": "【user-guard 长会话交接门禁 (drain -> freeze)】排空运行预算已耗尽，禁止再启动新命令，请完成交接停机。",
            }
        if consume_drain_action(gate_state):
            gate_state["enteredFreezeAtStep"] = tel["latestStep"]
        save_gate_state(state_dir, conversation_id, gate_state)
        return None

    if phase == "freeze" or int(gate_state.get("drainActionsRemaining", 0)) <= 0:
        if phase != "freeze":
            gate_state["enteredFreezeAtStep"] = tel["latestStep"]
        gate_state["phase"] = "freeze"
        save_gate_state(state_dir, conversation_id, gate_state)
        return {"decision": "deny", "reason": "【user-guard 冻结】当前工具缺少可核实的只读或交接用途。"}
    if consume_drain_action(gate_state):
        gate_state["enteredFreezeAtStep"] = tel["latestStep"]
    save_gate_state(state_dir, conversation_id, gate_state)
    return None


def check_stop_handoff_locked(
    payload: Dict[str, Any],
    state_dir: Path,
    turn_key: str,
    latest_reply: str,
    *,
    goal_active: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """检查当前交接结构，每个用户回合最多要求补正一次。"""
    conversation_id = str(payload.get("conversationId") or "").strip()
    if not conversation_id:
        return None

    gate_state = load_gate_state(state_dir, conversation_id)
    phase = str(gate_state.get("phase") or "normal")
    if phase not in ("drain", "freeze"):
        return None

    tickets = user_words.handoff_session_pending_tickets(conversation_id, state_dir)

    if turn_key == "turn_0_initial":
        comp_file = state_dir / f"{conversation_id}_compaction.json"
        if comp_file.exists():
            try:
                comp = json.loads(comp_file.read_text(encoding="utf-8-sig"))
                if isinstance(comp, dict):
                    u_step = int(comp.get("latestUserStepIndex", -1))
                    u_hash = str(comp.get("latestUserInputHash") or "")
                    if u_step >= 0 and u_hash:
                        turn_key = f"turn_{u_step}_{u_hash[:12]}"
            except (OSError, ValueError, TypeError, RecursionError):
                pass

    blocked_keys: List[str] = gate_state.setdefault("stopGateBlockedTurnKeys", [])
    if turn_key in blocked_keys:
        return {"decision": "allow"}

    missing_parts: List[str] = []
    has_file = _has_valid_handoff_file(gate_state, tickets)
    has_prompt_inline = (_handoff_structure_present(latest_reply)
                         and _workflow_handoff_present(latest_reply, tickets))
    if not has_file and not has_prompt_inline:
        missing_parts.append(
            "尚未落盘非空交接文档，且最终回复未同时满足 4 项必需交接章节要素："
            "① 已验证进展（含“已验证/已完成”）；"
            "② Git/工作区状态（含“Git/提交/工作区”）；"
            "③ 未完成事项与下一步待办（含“未完成/待办/下一步”）；"
            "④ 独立【新对话提示词】章节（如 `## 新对话提示词`，正文不少于 15 字且可直接复制粘贴）"
        )
        if tickets:
            missing_parts.append("【新对话提示词】遗漏未决票据的完整工作流详述、矩阵或选项卡")

    requires_cancel = bool(gate_state.get("isGoalActive")) if goal_active is None else goal_active
    if requires_cancel and not user_words.has_goal_cancel_marker(latest_reply):
        missing_parts.append(
            "检测到当前会话运行了 `/goal` 自主循环，回复末尾必须附带 `<!-- GOAL_CANCELLED -->` 才能安全停机"
        )

    if not missing_parts:
        user_words.append_lifecycle_log(
            HOOKS_DIR,
            "Stop:handoff_gate",
            conversation_id,
            f"ALLOWED clean handoff stop in phase={phase} ({turn_key})",
        )
        return {"decision": "allow"}

    blocked_keys.append(turn_key)
    save_gate_state(state_dir, conversation_id, gate_state)
    user_words.append_lifecycle_log(
        HOOKS_DIR,
        "Stop:handoff_gate",
        conversation_id,
        f"BLOCKED incomplete handoff in phase={phase} ({turn_key}): {'; '.join(missing_parts)}",
    )
    return {
        "decision": "continue",
        "reason": (
            "【user-guard 交接停机门禁验收未通过】"
            + "；".join(missing_parts)
            + "。请补全上述交接要素后直接输出停机回复。"
            + _workflow_handoff_notice(tickets)
        ),
    }


# ⑥ Codex 压缩事件对账与独立协议适配

def _codex_notice(phase: str, count: int, mode: str, client_label: str = "Codex") -> str:
    prefix = f"【{client_label} 长会话交接：{phase}】已登记的压缩次数下界为 {count}。模式 {mode}。"
    if phase == "overridden":
        return prefix + f"已按用户明确指令继续；宽限到其后第 {OVERRIDE_SNOOZE_CPS} 个新压缩事件，再按已配置策略评估。"
    if phase in ("drain", "freeze"):
        return (prefix + "请收尾当前小步，核对用户原话与最新裁定，整理已验证进展、Git/工作区状态、"
                "未完成事项及可复制的新对话提示词。结束当前回合不等于取消目标循环或终止后台任务；"
                "须如实登记其状态，不得把未完成目标标成完成。"
                + ("当前仅提醒，不阻断工具。" if mode == "advisory" else "暂停新派工、读图和推送；冻结后仅允许非图片只读核对与交接。"))
    return prefix + "先核对压缩恢复的原话与当前状态，再继续工作；本提醒不表示上下文已失效。"


def _codex_context(event: str, message: str) -> Dict[str, Any]:
    if event in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}
    return {"systemMessage": message}


def _scan_codex_compactions(transcript: Path, ledger: Dict[str, Any]) -> Optional[str]:
    """按轨迹文件身份与游标对账，保留可见记录及可确认的回合关联。"""
    path_key = _path_key(transcript)
    records = ledger["records"]
    try:
        with transcript.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = [stat.st_dev, stat.st_ino]
            cursor = ledger["files"].get(path_key, {})
            if not isinstance(cursor, dict):
                raise ValueError("压缩轨迹游标必须为对象")
            offset = cursor.get("offset", 0)
            if type(offset) is not int or offset < 0:
                raise ValueError("压缩轨迹游标格式错误")
            stream.seek(max(0, offset - 128))
            anchor = hashlib.sha256(stream.read(min(128, offset))).hexdigest()
            if (cursor.get("identity") != identity or offset > stat.st_size or anchor != cursor.get("anchor")):
                for key in list(records):
                    rec_path = records[key].get("path")
                    if key.startswith("record:") and (rec_path == str(transcript) or (isinstance(rec_path, str) and _path_key(Path(rec_path)) == path_key)):
                        del records[key]
                cursor = {"identity": identity, "activeTurn": "", "pendingRecord": "", "occurrences": {}}
                offset = 0
            if (not isinstance(cursor.get("occurrences"), dict)
                    or any(type(count) is not int or count < 0 for count in cursor["occurrences"].values())
                    or not isinstance(cursor.get("activeTurn"), str)
                    or not isinstance(cursor.get("pendingRecord"), str)
                    or (cursor["pendingRecord"] and cursor["pendingRecord"] not in records)):
                raise ValueError("压缩轨迹关联状态格式错误")
            stream.seek(offset)
            while stream.tell() < stat.st_size:
                row_offset = stream.tell()
                raw = stream.readline(stat.st_size - row_offset)
                if not raw.endswith(b"\n"):
                    stream.seek(row_offset)
                    break
                if not any(token in raw for token in (b'"compacted"', b'"turn_context"', b'"task_started"',
                                                      b'"turn_started"', b'"task_complete"', b'"turn_complete"',
                                                      b'"turn_aborted"')):
                    continue
                try:
                    entry = json.loads(user_words.decode_jsonl_line(raw))
                except (ValueError, RecursionError):
                    cursor["unreadable"] = True
                    continue
                if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
                    continue
                pending = cursor["pendingRecord"]
                kind = entry.get("type")
                fields = entry["payload"]
                turn = fields.get("turn_id")
                turn = turn if isinstance(turn, str) else ""
                if kind == "event_msg":
                    if fields.get("type") in ("task_started", "turn_started"):
                        cursor["activeTurn"] = turn
                    elif fields.get("type") in ("task_complete", "turn_complete", "turn_aborted"):
                        cursor["activeTurn"] = ""
                        cursor["pendingRecord"] = ""
                elif kind == "turn_context" and turn:
                    if pending:
                        records[pending].update(turn=turn, turnSource="following_turn_context")
                        cursor["pendingRecord"] = ""
                    if not cursor["activeTurn"]:
                        cursor["activeTurn"] = turn
                elif kind == "compacted":
                    cursor["pendingRecord"] = ""
                    window_id = fields.get("window_id")
                    if isinstance(window_id, str) and window_id:
                        key = f"window:{window_id}"
                    else:
                        # 没有宿主记录 ID 时，区分同一轨迹内相同内容的不同位置，镜像分段按出现顺序去重。
                        digest = hashlib.sha256(raw.strip()).hexdigest()
                        occurrence = cursor["occurrences"].get(digest, 0) + 1
                        cursor["occurrences"][digest] = occurrence
                        key = f"record:{digest}:{occurrence}"
                    active_turn = cursor["activeTurn"]
                    rec = records.setdefault(key, {"path": str(transcript), "offset": row_offset, "turn": "",
                                                   "turnSource": "unknown"})
                    if turn:
                        rec.update(turn=turn, turnSource="record")
                    else:
                        if active_turn and not rec["turn"]:
                            rec.update(turn=active_turn, turnSource="active_turn")
                        if not (rec["turn"] and rec["turnSource"] in ("record", "following_turn_context")):
                            cursor["pendingRecord"] = key

            offset = stream.tell()
            stream.seek(max(0, offset - 128))
            anchor = hashlib.sha256(stream.read(min(128, offset))).hexdigest()
            cursor.update(offset=offset, anchor=anchor)
            ledger["files"][path_key] = cursor
            if cursor.get("unreadable"):
                return "轨迹中存在不可解析的相关记录"
            if offset < stat.st_size:
                return "轨迹末尾记录待写完"
    except OSError as exc:
        return f"压缩轨迹读取失败（{exc}）"
    return None


def _reconcile_codex_compactions(payload: Dict[str, Any], transcript: Path, state: Dict[str, Any]) -> None:
    ledger = state.get("compactionLedger")
    if ledger is None:
        ledger = {"files": {}, "records": {}, "observations": {}}
        for key in state.get("compactionKeys", []):
            if isinstance(key, str) and key.startswith("turn:"):
                turn, _, trigger = key[5:].rpartition(":")
                if turn:
                    ledger["observations"][key] = {"turn": turn, "trigger": trigger, "source": "legacy_hook"}
        state["compactionLedger"] = ledger
        state.pop("compactionKeys", None)
    if not isinstance(ledger, dict) or any(not isinstance(ledger.get(key), dict)
                                          for key in ("files", "records", "observations")):
        raise ValueError("压缩对账状态格式错误，保留原文件")
    if (any(not isinstance(row, dict) or not isinstance(row.get("turn"), str)
            or not isinstance(row.get("turnSource"), str) for row in ledger["records"].values())
            or any(not isinstance(row, dict) or not isinstance(row.get("turn"), str)
                   for row in ledger["observations"].values())):
        raise ValueError("压缩对账记录格式错误，保留原文件")

    paths = [transcript]
    issues: List[str] = []
    if payload["hook_event_name"] == "SessionStart":
        segments, _, issues = user_words.discover_codex_session_segments(str(transcript), state["conversationId"])
        paths = segments or paths
    for path in paths:
        issue = _scan_codex_compactions(path, ledger)
        if issue:
            issues.append(issue)
    if payload["hook_event_name"] == "PostCompact":
        turn = payload.get("turn_id")
        if isinstance(turn, str) and turn:
            trigger = payload.get("trigger")
            trigger = trigger if trigger in ("manual", "auto") else "unknown"
            ledger["observations"].setdefault(f"turn:{turn}:{trigger}", {"turn": turn, "trigger": trigger,
                                                                          "source": "post_compact"})
        else:
            issues.append("PostCompact 未提供回合标识，无法按回合登记")

    verified_turns = set()
    unassigned = 0
    for record in ledger["records"].values():
        if record.get("turn") and record.get("turnSource") == "record":
            verified_turns.add(record["turn"])
        else:
            unassigned += 1
    observed_turns = {observation["turn"] for observation in ledger["observations"].values()}
    unmatched = observed_turns.difference(verified_turns)
    # 邻接记录只提供归属线索。它可能来自上一回合或恢复快照，不能证明与 hook 事件相互独立。
    extra = max(0, len(unmatched) - unassigned)
    record_count = len(ledger["records"])
    if unmatched and unassigned:
        issues.append("部分压缩记录未提供自身回合标识，与 PostCompact 观察可能重合")
    state["compactionCount"] = record_count + extra
    state["compactionRecordCount"] = record_count
    state["compactionFallbackCount"] = extra
    state["compactionIssues"] = issues
    state["compactionIdentity"] = ("mixed" if record_count and extra else "rollout_record" if record_count
                                  else "turn_fallback" if extra else "unknown")


def _codex_handoff_patch_paths(command: str, cwd: str) -> List[Path]:
    """获取项目内的交接补丁目标，兼容软链 cwd 并防止软链逃逸，移动源和目标均须通过路径核验。"""
    edits = re.findall(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$", command, re.MULTILINE)
    if not edits or not cwd or not cwd.strip():
        return []
    try:
        cwd_expanded = Path(os.path.expanduser(cwd.strip()))
        lex_base = Path(os.path.abspath(str(cwd_expanded)))
        base = cwd_expanded.resolve()
        targets: List[Path] = []
        for kind, raw in edits:
            cleaned = os.path.expanduser(raw.strip().strip("\"'"))
            if not cleaned:
                return []
            cand = Path(cleaned)
            path = Path(os.path.abspath(str(cand if cand.is_absolute() else lex_base / cand)))
            resolved = path.resolve()
            rel_dirs = [part.lower() for part in resolved.relative_to(base).parts[:-1]] if resolved.is_relative_to(base) else []
            if (not (path.is_relative_to(lex_base) or path.is_relative_to(base))
                    or not resolved.is_relative_to(base)
                    or resolved.suffix.lower() not in (".md", ".markdown", ".txt", ".rst")
                    or not (HANDOFF_FILENAME_RE.search(resolved.name) or "handoff" in rel_dirs)):
                return []
            if kind != "Delete File":
                targets.append(path)
        return targets
    except (OSError, RuntimeError, ValueError):
        return []


def _codex_tool_kind(payload: Dict[str, Any]) -> Tuple[str, List[Path]]:
    raw_name = str(payload.get("tool_name") or "")
    name = re.split(r"[.:]|__", raw_name)[-1].lower()
    raw_input = payload.get("tool_input")
    args = raw_input if isinstance(raw_input, dict) else {}
    if name in CODEX_DELEGATION_TOOLS or name in ("view_image", "imagegen"):
        return "blocked_always", []
    if name in CODEX_READ_TOOLS:
        if name == "read_file":
            read_target = str(args.get("path") or args.get("Path") or args.get("file_path") or args.get("AbsolutePath") or "").strip()
            if read_target and is_image_path(read_target):
                return "blocked_always", []
        return "readonly_inspect", []
    if name in ("bash", "exec_command", "run_command", "shell", "shell_command"):
        raw_cmd = args.get("command")
        if raw_cmd is None:
            raw_cmd = args.get("cmd") or args.get("CommandLine") or args.get("command_line") or ""
        cmd_str = (
            " ".join(shlex.quote(str(part)) for part in raw_cmd)
            if isinstance(raw_cmd, list)
            else str(raw_cmd or "")
        )
        return _classify_run_command(cmd_str), []
    if name in ("apply_patch", "edit", "write"):
        command = str(args.get("command") or args.get("patch") or args.get("patch_text") or
                      (raw_input if isinstance(raw_input, str) else ""))
        targets = _codex_handoff_patch_paths(command, str(payload.get("cwd") or ""))
        if targets:
            return "handoff_document", targets
    # 未识别工具不赋予只读豁免；严格模式 drain 中计入预算，freeze 中拒绝。
    return "mutating_other", []


def _reconcile_claude_compactions(payload: Dict[str, Any], transcript: Path, state: Dict[str, Any]) -> None:
    """增量登记边界；同一边界的 PostCompact / SessionStart 观察只计一次。"""
    ledger = state.setdefault("claudeCompactionLedger", {"files": {}, "records": {}, "observations": {}})
    if not isinstance(ledger, dict) or any(not isinstance(ledger.get(key), dict)
                                          for key in ("files", "records", "observations")):
        raise ValueError("Claude 压缩对账状态格式错误，保留原文件")
    issues = []
    path_key = _path_key(transcript)
    cursor = ledger["files"].get(path_key, {})
    records = ledger["records"]
    observations = ledger["observations"]
    new_boundaries = []
    with transcript.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        identity = [stat.st_dev, stat.st_ino]
        if not isinstance(cursor, dict) or type(cursor.get("offset", 0)) is not int or cursor.get("offset", 0) < 0:
            raise ValueError("Claude 压缩轨迹游标格式错误")
        offset = cursor.get("offset", 0)
        stream.seek(max(0, offset - 128))
        anchor = hashlib.sha256(stream.read(min(128, offset))).hexdigest()
        if cursor.get("identity") != identity or offset > stat.st_size or cursor.get("anchor") != anchor:
            offset = 0
            cursor = {"identity": identity, "occurrences": {}}
        if not isinstance(cursor.get("occurrences"), dict):
            raise ValueError("Claude 压缩轨迹出现次数格式错误")
        stream.seek(offset)
        while stream.tell() < stat.st_size:
            row_offset = stream.tell()
            raw = stream.readline(stat.st_size - row_offset)
            if not raw.endswith(b"\n"):
                stream.seek(row_offset)
                issues.append("Claude 轨迹末尾记录待写完")
                break
            if not raw.strip():
                continue
            try:
                entry = json.loads(user_words.decode_jsonl_line(raw))
            except (ValueError, RecursionError):
                cursor["unreadable"] = True
                continue
            if (not isinstance(entry, dict) or entry.get("isSidechain") is True
                    or entry.get("type") != "system"
                    or entry.get("subtype") not in ("compact_boundary", "microcompact_boundary")):
                continue
            digest = hashlib.sha256(raw.strip()).hexdigest()
            occurrence = cursor["occurrences"].get(digest, 0) + 1
            cursor["occurrences"][digest] = occurrence
            key = str(entry.get("uuid") or entry.get("id") or f"{digest}:{occurrence}")
            if key not in records:
                records[key] = {"path": str(transcript), "offset": row_offset}
                new_boundaries.append(key)
            cursor["latestBoundary"] = key
        offset = stream.tell()
        stream.seek(max(0, offset - 128))
        anchor = hashlib.sha256(stream.read(min(128, offset))).hexdigest()
        cursor.update(offset=offset, anchor=anchor)
        ledger["files"][path_key] = cursor
    if cursor.get("unreadable"):
        issues.append("Claude 轨迹存在不可解析记录")
    # 先到达的 hook 观察在边界刷盘后并入对应记录，避免两套来源相加。
    pending = [key for key, value in observations.items() if not value]
    for pending_key, boundary in zip(pending, new_boundaries):
        observations[pending_key] = boundary
    event = payload["hook_event_name"]
    if event == "PostCompact" or (event == "SessionStart" and payload.get("source") == "compact"):
        latest = cursor.get("latestBoundary", "")
        observation_key = latest or str(payload.get("compact_id") or payload.get("event_id") or "pending_snapshot")
        observations.setdefault(observation_key, latest)
    fallback = sum(not value for value in observations.values())
    state["compactionCount"] = max(state.get("compactionCount", 0), len(records) + fallback)
    state.update(compactionRecordCount=len(records), compactionFallbackCount=fallback,
                 compactionIssues=issues, compactionIdentity="claude_boundary" if records else "hook_snapshot")


def _claude_handoff_targets(raw_paths: List[str], cwd: str) -> List[Path]:
    if not cwd or not cwd.strip() or not raw_paths:
        return []
    try:
        cwd_expanded = Path(os.path.expanduser(cwd.strip()))
        lex_base = Path(os.path.abspath(str(cwd_expanded)))
        base = cwd_expanded.resolve()
        targets = []
        for raw in raw_paths:
            if not raw or not str(raw).strip():
                return []
            raw_s = str(raw).strip()
            if "$" in raw_s or "`" in raw_s or re.search(r"%[A-Za-z_][A-Za-z_0-9]*%", raw_s):
                return []
            raw_path = _resolve_candidate_path(raw, cwd)
            path = Path(os.path.abspath(str(raw_path)))
            resolved = path.resolve()
            rel_dirs = [part.lower() for part in resolved.relative_to(base).parts[:-1]] if resolved.is_relative_to(base) else []
            if (not (path.is_relative_to(lex_base) or path.is_relative_to(base))
                    or not resolved.is_relative_to(base)
                    or resolved.suffix.lower() not in (".md", ".markdown", ".txt", ".rst")
                    or not (HANDOFF_FILENAME_RE.search(resolved.name) or "handoff" in rel_dirs)):
                return []
            targets.append(path)
        return targets
    except (OSError, RuntimeError, ValueError):
        return []


def _claude_tool_kind(payload: Dict[str, Any]) -> Tuple[str, List[Path]]:
    raw_name = str(payload.get("tool_name") or "").strip()
    name = raw_name.rsplit(".", 1)[-1].rsplit("__", 1)[-1]
    args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    cwd = str(payload.get("cwd") or "")
    if name in ("Agent", "Task", "Subagent", "dispatch_agent"):
        return "blocked_always", []
    if name == "Read":
        if is_image_path(str(args.get("file_path") or args.get("path") or "")):
            return "blocked_always", []
        return "readonly", []
    if name in ("Grep", "Glob", "LS", "ToolSearch", "TodoRead", "TodoWrite", "AskUserQuestion"):
        return "readonly", []
    if name in ("Write", "Edit", "MultiEdit"):
        targets = _claude_handoff_targets([str(args.get("file_path") or args.get("path") or "")], cwd)
        if targets:
            return "handoff_document", targets
    if name in ("Bash", "PowerShell"):
        command = str(args.get("command") or "")
        kind = _classify_run_command(command)
        if kind != "mutating_other":
            return kind, []
        unquoted = _strip_quoted_segments(command)
        redir_ops = re.compile(r"(?:&>{1,2}|(?<![>&0-9])(?:\d*)>{1,2})(?![>&])")
        arg_pat = re.compile(r"""\s*((?:\$'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|\\.|[^\s;|&<>])+)""")
        raw_paths: List[str] = []
        spans: List[Tuple[int, int]] = []
        valid_redirs = True
        for op_m in redir_ops.finditer(unquoted):
            arg_m = arg_pat.match(command, op_m.end())
            if not arg_m:
                valid_redirs = False
                break
            tok = _strip_token_quotes(arg_m.group(1).strip())
            spans.append((op_m.start(), arg_m.end()))
            if tok.lower() not in ("/dev/null", "nul", "$null"):
                raw_paths.append(tok)
        if valid_redirs and raw_paths:
            kept_parts: List[str] = []
            cursor_idx = 0
            for sp_start, sp_end in spans:
                kept_parts.append(command[cursor_idx:sp_start])
                cursor_idx = sp_end
            kept_parts.append(command[cursor_idx:])
            if _classify_run_command("".join(kept_parts)) == "readonly_inspect":
                targets = _claude_handoff_targets(raw_paths, cwd)
                if targets:
                    return "handoff_document", targets
    return "mutating_other", []


def _extract_native_turn_latest_reply(transcript: Path, client: str) -> str:
    """当 Stop 事件未携带 last_assistant_message 时，从轨迹提取当前回合最新助手文本回复。"""
    max_tail_bytes = 2 * 1024 * 1024
    try:
        if not transcript or not transcript.is_file():
            return ""
        with open(transcript, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            start = max(0, size - max_tail_bytes)
            stream.seek(start)
            buf = stream.read(max_tail_bytes)
    except OSError:
        return ""
    if start > 0:
        first_nl = buf.find(b"\n")
        buf = buf[first_nl + 1 :] if first_nl != -1 else b""
    raw_lines = [line.rstrip(b"\r") for line in buf.split(b"\n")]
    if client == "claude":
        chunks: List[str] = []
        for raw in reversed(raw_lines):
            if not raw.strip():
                continue
            try:
                entry = json.loads(user_words.decode_jsonl_line(raw))
            except (ValueError, RecursionError):
                continue
            if not isinstance(entry, dict) or entry.get("isSidechain") is True or entry.get("isMeta") is True:
                continue
            u_text, u_kind = user_words.claude_user_content(entry)
            if u_text and u_kind in ("user_input", "queued_command", "ask_question_answer"):
                break
            if entry.get("type") == "user" and (
                user_words._claude_tool_result_ids(entry) or isinstance(entry.get("toolUseResult"), dict)
            ):
                break
            if entry.get("type") == "assistant":
                msg = entry.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, list):
                    has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
                    text = "\n".join(
                        b["text"] for b in content
                        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
                    )
                    if has_tool_use:
                        if not chunks:
                            return ""
                        break
                    if text.strip():
                        chunks.append(text.strip())
                elif isinstance(content, str) and content.strip():
                    chunks.append(content.strip())
        return "\n".join(reversed(chunks)).strip()

    codex_chunks: List[str] = []
    for index, raw in enumerate(raw_lines):
        if not raw.strip():
            continue
        try:
            entry = json.loads(user_words.decode_jsonl_line(raw))
        except (ValueError, RecursionError):
            continue
        if not isinstance(entry, dict):
            continue
        typ = entry.get("type")
        rec = entry.get("payload") if typ == "response_item" else None
        is_user = typ == "USER_INPUT" or entry.get("source") == "USER_EXPLICIT"
        if isinstance(rec, dict) and rec.get("type") == "message" and rec.get("role") == "user":
            _, word = user_words._extract_codex_record(entry, index)
            if word:
                word["source_path"] = str(transcript)
            is_user = bool(word and user_words._codex_word_from_record(word))
        if is_user:
            codex_chunks.clear()
            continue
        if isinstance(rec, dict) and rec.get("type") == "message" and rec.get("role") == "assistant":
            text = "\n".join(
                b.get("text", "") for b in rec.get("content", [])
                if isinstance(b, dict) and isinstance(b.get("text"), str)
            )
            if text.strip():
                codex_chunks.append(text.strip())
        elif (
            isinstance(rec, dict)
            and rec.get("type")
            in (
                "function_call",
                "function_call_output",
                "custom_tool_call",
                "custom_tool_call_output",
                "local_shell_call",
                "local_shell_call_output",
                "web_search_call",
                "web_search_call_output",
            )
        ) or typ in ("TOOL_CALL", "TOOL_RESULT", "TOOL_RESPONSE", "GENERIC"):
            codex_chunks.clear()
    return "\n".join(codex_chunks).strip()


def handle_codex_handoff(payload: Dict[str, Any], state_dir: Path, mode: str = "advisory",
                         compact_limit: int = 0) -> Dict[str, Any]:
    """Codex 原生协议入口。advisory 永不阻断；支持运行期自适应策略升级。"""
    return _handle_native_handoff(payload, state_dir, mode, compact_limit, "codex")


def handle_claude_handoff(payload: Dict[str, Any], state_dir: Path, mode: str = "advisory",
                          compact_limit: int = 0) -> Dict[str, Any]:
    """Claude 原生生命周期入口；Stop 只补正一次，不使用 Codex 停机字段。"""
    return _handle_native_handoff(payload, state_dir, mode, compact_limit, "claude")


def _handle_native_handoff(
    payload: Dict[str, Any],
    state_dir: Path,
    mode: str,
    compact_limit: int,
    client: str,
    *,
    _mode_explicit: Optional[bool] = None,
    _limit_explicit: Optional[bool] = None,
) -> Dict[str, Any]:
    label = "Claude" if client == "claude" else "Codex"
    event = str(payload.get("hook_event_name") or "")
    if event not in CODEX_EVENTS or mode == "off":
        return {}
    if mode not in ("advisory", "enforce") or type(compact_limit) is not int or compact_limit < 0:
        raise ValueError(f"无效的 {label} 交接策略")
    resolver = user_words.claude_hook_session if client == "claude" else user_words.codex_hook_session
    sid, transcript, identity_error, skip = resolver(payload)
    if skip:
        return {}
    state_dir = Path(state_dir)
    if transcript is None:
        raw_tp = user_words.strip_win_extended_prefix(str(payload.get("transcript_path") or "")).strip()
        missing_tp = not raw_tp or not Path(raw_tp).expanduser().exists()
        is_compact_or_resume = (
            event == "PostCompact"
            or str(payload.get("source") or "").strip().lower() in ("compact", "resume")
        )
        if (
            user_words.valid_session_id(sid)
            and missing_tp
            and not is_compact_or_resume
            and not _gate_state_path(state_dir, sid).exists()
        ):
            return {}
        return {"systemMessage": f"{label} 长会话门禁未更新。{identity_error}。"}
    if not user_words.valid_session_id(sid):
        return {}
    with user_words.session_state_lock(state_dir, sid):
        state_path = _gate_state_path(state_dir, sid)
        state_existed = state_path.exists()
        if state_existed:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            if (not isinstance(state, dict) or state.get("client") != client or state.get("version") != 1
                    or state.get("conversationId") != sid or not isinstance(state.get("observedEvents"), list)
                    or state.get("phase") not in ("normal", "advisory", "drain", "freeze", "overridden")):
                raise ValueError(f"{label} 门禁状态格式不兼容，保留原文件")
            for key, expected in (("drainActionsRemaining", int), ("overrideUntilCount", int),
                                  ("lastUserEvent", str), ("stopCorrectionRequested", bool), ("handoffRecorded", bool)):
                if type(state.get(key)) is not expected:
                    raise ValueError(f"{label} 门禁状态字段 {key} 格式错误")
            if state["drainActionsRemaining"] < 0:
                raise ValueError(f"{label} 排空动作预算不能为负数")
            for key in ("toolDecisions", "handoffFileBaselines"):
                if key in state and not isinstance(state[key], dict):
                    raise ValueError(f"{label} 门禁状态字段 {key} 必须为对象")
        else:
            state = {"client": client, "version": 1, "conversationId": sid, "phase": "normal",
                     "compactionKeys": [], "compactionCount": 0, "observedEvents": [],
                     "drainActionsRemaining": 0, "overrideUntilCount": -1,
                     "lastUserEvent": "", "stopCorrectionRequested": False, "handoffRecorded": False}

        raw_m, _e = _extract_runtime_model_and_effort(client, payload)
        clean_m = "" if raw_m.lower() == "unknown" else raw_m
        stored_m = str(state.get("lastModel") or "").strip()
        if stored_m.lower() == "unknown":
            stored_m = ""
        eff_model = clean_m or stored_m

        _sync_handoff_tunables(client, eff_model, _e)
        client_drain_actions = (
            CODEX_DRAIN_ACTIONS
            if CODEX_DRAIN_ACTIONS != _ACTIVE_HANDOFF_BASELINE["CODEX_DRAIN_ACTIONS"]
            else (_CLAUDE_DRAIN_ACTIONS if client == "claude" else CODEX_DRAIN_ACTIONS)
        )
        if not state_existed:
            state["drainActionsRemaining"] = client_drain_actions

        client_cfg = _adaptive_policy.resolve_adaptive_handoff_policy(
            user_words.load_fleet_profile(HOOKS_DIR), client, eff_model, _e
        )
        cfg_mode = str(client_cfg.get("default_mode") or "advisory").strip().lower()
        resolved_mode = cfg_mode if cfg_mode in ("advisory", "enforce", "off") else "advisory"
        resolved_limit = user_words._coerce_pos_int(client_cfg.get("default_compact_limit"), 0, minimum=0)

        is_default_pair = (mode == "advisory" and compact_limit == 0)
        mode_explicit = (not is_default_pair) if _mode_explicit is None else bool(_mode_explicit)
        limit_explicit = (not is_default_pair) if _limit_explicit is None else bool(_limit_explicit)

        if (not mode_explicit or is_default_pair) and mode == "advisory" and resolved_mode != "advisory":
            mode = resolved_mode
        if mode == "off":
            return {}
        if (not limit_explicit or is_default_pair) and compact_limit == 0 and resolved_limit > 0:
            compact_limit = resolved_limit

        if event not in state["observedEvents"]:
            state["observedEvents"].append(event)
        state.update(mode=mode, compactLimit=compact_limit, lastModel=eff_model or "unknown")
        if mode == "advisory" and state["phase"] in ("drain", "freeze"):
            state["phase"] = "advisory"
        if client == "claude":
            _reconcile_claude_compactions(payload, transcript, state)
        else:
            _reconcile_codex_compactions(payload, transcript, state)
        if state["phase"] == "normal" and (event == "PostCompact" or state["compactionCount"]):
            state["phase"] = "advisory"
        count = state["compactionCount"]
        if state["phase"] == "overridden" and count >= state["overrideUntilCount"]:
            state["phase"] = "advisory"
        enter_drain = (mode == "enforce" and compact_limit > 0 and count >= compact_limit
                       and state["phase"] in ("normal", "advisory"))
        user_event_is_new = False
        prompt = str(payload.get("prompt") or "").strip() if event == "UserPromptSubmit" else ""
        if prompt and not payload.get("stop_hook_active"):
            turn = str(payload.get("turn_id") or payload.get("uuid") or payload.get("event_id") or transcript.stat().st_size)
            user_key = turn + ":" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            user_event_is_new = user_key != state["lastUserEvent"]
            if user_event_is_new:
                state["lastUserEvent"] = user_key
                state["stopCorrectionRequested"] = False
                # 只处理短而明确的用户指令，引用长文中的相同短语不改变门禁。
                if len(prompt) <= 160 and CODEX_OVERRIDE_REQUEST_RE.fullmatch(prompt):
                    state.update(phase="overridden", overrideUntilCount=count + OVERRIDE_SNOOZE_CPS,
                                 stopCorrectionRequested=False, handoffRecorded=False, toolDecisions={},
                                 handoffFileBaselines={})
                    enter_drain = False
                elif mode == "enforce" and CODEX_HANDOFF_REQUEST_RE.fullmatch(prompt):
                    enter_drain = state["phase"] not in ("drain", "freeze")
        if enter_drain:
            state.update(phase="drain", drainActionsRemaining=client_drain_actions,
                         stopCorrectionRequested=False, handoffRecorded=False, toolDecisions={},
                         handoffFileBaselines={})
        phase = state["phase"]
        client_label = "Claude" if client == "claude" else "Codex"
        notice = _codex_notice(phase, count, mode, client_label=client_label)
        tickets = (user_words.handoff_session_pending_tickets(sid, state_dir)
                   if phase in ("drain", "freeze") else [])
        notice += _workflow_handoff_notice(tickets)
        if state["compactionIssues"]:
            notice += " 压缩轨迹对账存在缺口，次数按当前可见证据保守计算。"
        response: Dict[str, Any] = {}
        if event in ("PostCompact", "SessionStart"):
            if event == "PostCompact" or payload.get("source") == "compact" or phase in ("drain", "freeze", "overridden"):
                response = _codex_context(event, notice)
        elif event == "UserPromptSubmit" and user_event_is_new and (phase in ("drain", "freeze", "overridden")):
            response = _codex_context(event, notice)
        elif event == "PreToolUse" and mode == "enforce" and phase in ("drain", "freeze"):
            tool_id = str(payload.get("tool_use_id") or "")
            previous = state.setdefault("toolDecisions", {})
            if tool_id and tool_id in previous:
                save_gate_state(state_dir, sid, state)
                return previous[tool_id]
            kind, targets = _claude_tool_kind(payload) if client == "claude" else _codex_tool_kind(payload)
            if kind == "blocked_always" or (phase == "freeze" and kind == "mutating_other"):
                raw_tname = str(payload.get("tool_name") or "").strip()
                short_tname = re.split(r"[.:]|__", raw_tname)[-1]
                t_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
                raw_sh = t_args.get("command") or t_args.get("cmd") or ""
                sh_str = " ".join(str(x) for x in raw_sh) if isinstance(raw_sh, list) else str(raw_sh)
                is_img_block = (
                    short_tname.lower() in ("view_image", "open_image", "imagegen", "generate_image")
                    or (
                        short_tname.lower() in ("read", "read_file")
                        and is_image_path(str(t_args.get("file_path") or t_args.get("path") or t_args.get("Path") or ""))
                    )
                    or (
                        kind == "blocked_always"
                        and short_tname.lower() in ("bash", "powershell", "exec_command", "run_command", "shell", "shell_command")
                        and any(is_image_path(tok.strip("\"'")) for tok in sh_str.split()[1:])
                    )
                )
                deny_reason = notice
                if is_img_block:
                    deny_reason += " 收口/冻结期禁止读图或看图（多模态图片会急剧消耗剩余上下文窗口并加剧二次压缩风险），请改用已有文本摘要或脚本日志核对。"
                response = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                            "permissionDecisionReason": deny_reason}}
            elif kind == "mutating_other":
                if consume_drain_action(state):
                    notice = _codex_notice(state["phase"], count, mode, client_label=client_label)
                    notice += _workflow_handoff_notice(tickets)
                    if state["compactionIssues"]:
                        notice += " 压缩轨迹对账存在缺口，次数按当前可见证据保守计算。"
                response = _codex_context(event, notice)
            elif kind == "handoff_document":
                for target in targets:
                    _record_handoff_baseline(state, target)
            if tool_id:
                previous[tool_id] = response
                state["toolDecisions"] = dict(list(previous.items())[-512:])
        elif event == "Stop" and mode == "enforce" and phase in ("drain", "freeze"):
            reply = str(payload.get("last_assistant_message") or "")
            if not reply.strip():
                reply = _extract_native_turn_latest_reply(transcript, client)
            if ((_handoff_structure_present(reply) and _workflow_handoff_present(reply, tickets))
                    or _has_valid_handoff_file(state, tickets)):
                state.update(phase="freeze", handoffRecorded=True)
                response = {} if client == "claude" else {"continue": False, "stopReason": "已记录交接结构，结束当前回合；目标与后台任务状态须由用户/总控另行核对。"}
            elif payload.get("stop_hook_active") or state["stopCorrectionRequested"]:
                state["phase"] = "freeze"
                response = ({"systemMessage": "交接未通过结构检查，不能报告为已完成。"} if client == "claude" else
                            {"continue": False, "stopReason": "交接要素仍不完整，已停止本轮自动补正以防循环；请人工核对未完成事项。",
                             "systemMessage": "交接未通过结构检查，不能报告为已完成。"})
            else:
                state["stopCorrectionRequested"] = True
                four_elements = (
                    "请补齐以下 4 个必需交接章节要素后结束（本次仅补正一次）："
                    "① 已验证进展（含“已验证/已完成”）；"
                    "② Git/工作区状态（含“Git/提交/工作区”）；"
                    "③ 未完成事项与下一步待办（含“未完成/待办/下一步”）；"
                    "④ 独立【新对话提示词】章节（如 `## 新对话提示词`，正文不少于 15 字且可直接复制粘贴）。"
                )
                response = {"decision": "block", "reason": notice + four_elements}
        save_gate_state(state_dir, sid, state)
        return response


# ⑦ 命令行主入口

def _default_native_state_dir(client: str) -> Path:
    state = os.environ.get("USER_GUARD_STATE_DIR")
    if state:
        return Path(state).expanduser() / "handoff"
    variable, dirname = (("CLAUDE_CONFIG_DIR", ".claude") if client == "claude" else ("CODEX_HOME", ".codex"))
    return Path(os.environ.get(variable) or Path.home() / dirname).expanduser() / "hooks" / "state" / "handoff"


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex & Claude Code 长会话交接事件适配；反重力使用既有模块接口")
    parser.add_argument("--client", choices=("codex", "claude"), required=True)
    parser.add_argument("--mode", choices=("advisory", "enforce", "off"), default=None)
    parser.add_argument("--compact-limit", type=int, default=None)
    parser.add_argument("--state-dir", default=None)
    args = parser.parse_args()
    try:
        payload = user_words.read_hook_payload()
        mode_explicit = args.mode is not None
        limit_explicit = args.compact_limit is not None
        raw_mode = args.mode if mode_explicit else "advisory"
        raw_limit = args.compact_limit if limit_explicit else 0
        state_dir = Path(args.state_dir).expanduser() if args.state_dir else _default_native_state_dir(args.client)
        response = _handle_native_handoff(
            payload,
            state_dir,
            raw_mode,
            raw_limit,
            args.client,
            _mode_explicit=mode_explicit,
            _limit_explicit=limit_explicit,
        )
    except Exception as exc:
        # 状态/协议异常时不伪装已受保护，不在失败的钩子中无限继续。
        label = "Claude" if args.client == "claude" else "Codex"
        sys.stderr.write(f"{label} handoff hook: {exc}\n")
        response = {"systemMessage": f"{label} 长会话门禁本次处理失败，未更新保护状态；请检查钩子错误。"}
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
