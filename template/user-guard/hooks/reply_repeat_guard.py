"""
reply_repeat_guard.py - Stop 阶段长会话交接核查与重复等待回复拦截钩子 (user-guard)

在模型准备结束当前回合时执行两项检查（互不干扰后台正常任务）：
1. 长会话收尾阶段（drain / freeze）交接核查（配合 handoff_gate.py）：
   - 检查是否已写入非空交接文档，或在回复中给出可直接复制的【新对话提示词】；开启 /goal 时同步检查末尾是否包含 `<!-- GOAL_CANCELLED -->` 标记（每个用户回合最多提醒补齐一次）。
2. 连续重复空等回复拦截：
   - 读取会话轨迹末尾窗口（文件超过 256 KB 时仅扫描尾部窗口，单回合超长时向前分块定位回合起点）；
   - 判断最新回复是否属于无实质进展的短等待提示（不超过 300 字符、含后台运行等待词、不含向用户提问或请用户决定的词）；
   - 相邻回复按保留数字的归一化文本比对（保留迭代步数与残差数值），仅当同一回合连续三次输出内容相近的无增量短等待回复时触发一次拦截提醒。
"""

# ① 常量、阈值与宿主依赖

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS_DIR))
import handoff_gate  # noqa: E402
import user_words  # noqa: E402

user_words.ensure_utf8_stdio()

MAX_SHORT_REPLY_CHARS = 300
TAIL_SCAN_BYTES = 256 * 1024  # 256 KB
SIMILARITY_THRESHOLD = 0.80
CORE_SIMILARITY_THRESHOLD = 0.75
CONSECUTIVE_WAIT_LIMIT = 3

_REPEAT_BUILTINS: Dict[str, Any] = {
    "MAX_SHORT_REPLY_CHARS": MAX_SHORT_REPLY_CHARS,
    "TAIL_SCAN_BYTES": TAIL_SCAN_BYTES,
    "SIMILARITY_THRESHOLD": SIMILARITY_THRESHOLD,
    "CORE_SIMILARITY_THRESHOLD": CORE_SIMILARITY_THRESHOLD,
    "CONSECUTIVE_WAIT_LIMIT": CONSECUTIVE_WAIT_LIMIT,
}
_ACTIVE_REPEAT_BASELINE: Dict[str, Any] = dict(_REPEAT_BUILTINS)
_LAST_REPEAT_PROFILE_TOKEN: Optional[Tuple[Any, ...]] = None


def _sync_repeat_tunables() -> None:
    """从 fleet_profile.json 同步重复等待门禁参数，同时兼容单测属性覆盖。"""
    global _LAST_REPEAT_PROFILE_TOKEN
    cache_token = user_words.get_fleet_profile_cache_token(HOOKS_DIR)
    if _LAST_REPEAT_PROFILE_TOKEN == cache_token:
        return
    _LAST_REPEAT_PROFILE_TOKEN = cache_token
    lg = user_words.get_runtime_tunables("loop_guards", HOOKS_DIR)
    cfg = lg.get("reply_repeat") if isinstance(lg.get("reply_repeat"), dict) else {}
    resolved: Dict[str, Any] = {
        "MAX_SHORT_REPLY_CHARS": user_words._coerce_pos_int(cfg.get("max_short_reply_chars"), 300, minimum=50),
        "TAIL_SCAN_BYTES": user_words._coerce_pos_int(cfg.get("tail_scan_kib"), 256, minimum=16) * 1024,
        "SIMILARITY_THRESHOLD": user_words._coerce_ratio(cfg.get("similarity_threshold"), 0.80, low=0.50, high=0.98),
        "CORE_SIMILARITY_THRESHOLD": user_words._coerce_ratio(cfg.get("core_similarity_threshold"), 0.75, low=0.45, high=0.98),
        "CONSECUTIVE_WAIT_LIMIT": user_words._coerce_pos_int(cfg.get("consecutive_wait_limit"), 3, minimum=2),
    }
    mod_globals = globals()
    for name, new_val in resolved.items():
        was_unpatched = (mod_globals[name] == _ACTIVE_REPEAT_BASELINE[name])
        _ACTIVE_REPEAT_BASELINE[name] = new_val
        if was_unpatched:
            mod_globals[name] = new_val


_sync_repeat_tunables()

WAIT_KEYWORDS = (
    "仍在运行",
    "还在运行",
    "正在运行",
    "继续运行",
    "后台运行",
    "仍在计算",
    "正在计算",
    "继续等待",
    "等待完成",
    "等待结果",
    "等待后台",
    "耐心等待",
    "请稍候",
    "稍后汇报",
    "尚未完成",
    "还未完成",
    "still running",
    "waiting for",
)

WAIT_BOILERPLATE_TOKENS = sorted(
    set(WAIT_KEYWORDS)
    | {
        "请耐心",
        "in the background",
        "currently",
        "please",
        "wait",
        "still",
        "目前",
        "当前",
        "现在",
        "后台",
        "仍在",
        "还在",
        "正在",
        "继续",
        "依然",
        "仍然",
        "稍候",
        "等待",
        "运行中",
        "计算中",
        "请",
        "中",
        "的",
        "了",
        "呢",
        "吧",
    },
    key=len,
    reverse=True,
)

# 向用户提问或等待用户拍板裁决的标记（命中则豁免，绝不拦截正常的等待用户回复）
USER_DECISION_OR_QUESTION_MARKERS = (
    "?",
    "？",
    "请问",
    "是否需要",
    "是否继续",
    "请确认",
    "请告知",
    "由您决定",
    "由你决定",
    "请拍板",
    "等你",
    "等待你",
    "等待您",
    "用户裁决",
    "你的指示",
    "您的指示",
)

BLOCK_REASON_TEXT = (
    "【user-guard 重复空等拦截】当前已连续三次输出内容相近、无实质进展的短状态等待回复。"
    "请停止重复汇报；后台任务尚未完成时，请直接结束本轮并等待系统 Reactive Wakeup 通知。"
)


def _build_block_reason_text() -> str:
    if CONSECUTIVE_WAIT_LIMIT == 3:
        return BLOCK_REASON_TEXT
    return BLOCK_REASON_TEXT.replace("连续三次", f"连续 {CONSECUTIVE_WAIT_LIMIT} 次")


# ② 目录、日志与防重复状态读写

def get_data_dirs() -> Tuple[Path, Path]:
    return user_words.resolve_data_dirs(HOOKS_DIR)


def get_current_iso_time() -> str:
    return user_words.current_iso_time()


def log_event(event_name: str, conversation_id: str, message: str) -> None:
    user_words.append_lifecycle_log(HOOKS_DIR, event_name, conversation_id, message)


def _load_repeat_state(state_file: Path, conversation_id: str, logs_dir: Optional[Path] = None) -> Dict[str, Any]:
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8-sig"))
            if (isinstance(data, dict) and data.get("version") == 2
                    and data.get("conversationId") == conversation_id and isinstance(data.get("turns"), dict)):
                for record in data["turns"].values():
                    if (not isinstance(record, dict)
                            or type(record.get("consecutiveShortWaitCount")) is not int
                            or record["consecutiveShortWaitCount"] < 0
                            or not isinstance(record.get("lastNormalizedReply"), str)
                            or not isinstance(record.get("blockedSignatures"), list)
                            or (record.get("lastStepIndex") is not None and type(record["lastStepIndex"]) is not int)):
                        raise ValueError("回合复读记录格式错误")
                return data
            raise ValueError("状态格式或会话身份不符")
        except (OSError, ValueError, RecursionError) as exc:
            if logs_dir is None:
                log_event("Stop:repeat_state_error", conversation_id, f"防重复状态读取失败（{exc}），保留原文件")
            else:
                user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
                    "event": "Stop:repeat_state_error", "client": "claude", "session_id": conversation_id,
                    "message": f"防重复状态读取失败（{exc}），保留原文件",
                })
            raise
    return {"version": 2, "conversationId": conversation_id, "turns": {}}


def _save_repeat_state(state_file: Path, state: Dict[str, Any], conversation_id: str) -> None:
    state["version"] = 2
    state["conversationId"] = conversation_id
    state["updatedAt"] = get_current_iso_time()
    user_words.atomic_write_json(state_file, state)


# ③ 尾部窗口、逆序回合定位与截断回复回查

def _read_tail_steps_with_offset(transcript_path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """读取 transcript.jsonl 末尾窗口并解析步骤列表，同时返回窗口起始字节偏移"""
    try:
        start_offset = 0
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            fsize = f.tell()
            if fsize > TAIL_SCAN_BYTES:
                seek_pos = fsize - TAIL_SCAN_BYTES
                f.seek(seek_pos - 1)
                at_line_start = f.read(1) == b"\n"
                raw = f.read(TAIL_SCAN_BYTES)
                if at_line_start:
                    start_offset = seek_pos
                else:
                    first_nl = raw.find(b"\n")
                    if first_nl != -1 and raw[first_nl + 1 :].strip():
                        start_offset = seek_pos + first_nl + 1
                        raw = raw[first_nl + 1 :]
                    else:
                        # 尾行可能独占整个窗口（包括仅有末尾换行的 JSONL）。
                        # 有界逆向寻找行首，避免丢弃超长的最终交接回复。
                        search_pos = seek_pos
                        lower_bound = max(0, fsize - user_words.MAX_HOOK_INPUT_CHARS)
                        while search_pos > lower_bound:
                            chunk_start = max(lower_bound, search_pos - TAIL_SCAN_BYTES)
                            f.seek(chunk_start)
                            prefix = f.read(search_pos - chunk_start)
                            last_nl = prefix.rfind(b"\n")
                            if last_nl != -1:
                                search_pos = chunk_start + last_nl + 1
                                break
                            search_pos = chunk_start
                        else:
                            if search_pos:
                                return [], fsize
                        start_offset = search_pos
                        f.seek(start_offset)
                        raw = f.read(fsize - start_offset)
            else:
                f.seek(0)
                raw = f.read(fsize)
    except OSError:
        return [], 0

    steps: List[Dict[str, Any]] = []
    # JSON 字符串允许含 U+2028 等字符，只能按真实换行字节分行。
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(user_words.decode_jsonl_line(line))
            if isinstance(obj, dict):
                steps.append(obj)
        except (ValueError, RecursionError):
            continue
    return steps, start_offset


def _is_turn_boundary_step(step: Dict[str, Any]) -> bool:
    """识别回合起点：人类显式 USER_INPUT 或跨代理派发的非 system 消息"""
    s_type = step.get("type", "")
    if s_type == "USER_INPUT" or step.get("source") == "USER_EXPLICIT":
        return True
    if s_type == "SYSTEM_MESSAGE":
        return bool(user_words.clean_user_content(step.get("content"), "SYSTEM_MESSAGE"))
    return False


def _find_turn_key_before_offset(transcript_path: Path, end_offset: int) -> str:
    """当单回合轨迹超过尾部 256KB 窗口时，向前分块逆序定位最近一次回合起点并返回 turn_<step>_<hash>"""
    if end_offset <= 0:
        return "turn_0_initial"
    try:
        pos = end_offset
        carry = b""
        with open(transcript_path, "rb") as f:
            while pos > 0:
                chunk_start = max(0, pos - TAIL_SCAN_BYTES)
                f.seek(chunk_start)
                chunk = f.read(pos - chunk_start)
                buf = chunk + carry
                lines = buf.split(b"\n")
                if chunk_start > 0:
                    carry = lines[0]
                    candidate_lines = lines[1:]
                else:
                    carry = b""
                    candidate_lines = lines
                for raw_line in reversed(candidate_lines):
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    if (
                        b'"USER_INPUT"' not in stripped
                        and b'"USER_EXPLICIT"' not in stripped
                        and b"[Message]" not in stripped
                    ):
                        continue
                    try:
                        st = json.loads(user_words.decode_jsonl_line(stripped))
                    except (ValueError, RecursionError):
                        continue
                    if isinstance(st, dict) and _is_turn_boundary_step(st):
                        s_idx = st.get("step_index", 0)
                        c_hash = hashlib.sha256(str(st.get("content", "")).encode("utf-8")).hexdigest()[:12]
                        return f"turn_{s_idx}_{c_hash}"
                pos = chunk_start
    except OSError:
        pass
    return "turn_0_initial"


def extract_current_turn_latest_reply(transcript_path_str: Optional[str]) -> Tuple[str, Optional[int], str]:
    """
    提取当前回合键 turn_key、最新一条模型文本回复的 step_index 及其内容。
    """
    if not transcript_path_str:
        return "turn_0_initial", None, ""

    tp = user_words.resolve_compact_transcript_path(Path(transcript_path_str).expanduser())
    if not tp.exists() or not tp.is_file():
        return "turn_0_initial", None, ""

    steps, window_start_offset = _read_tail_steps_with_offset(tp)
    if not steps and window_start_offset <= 0:
        return "turn_0_initial", None, ""

    turn_key = "turn_0_initial"
    turn_start_pos = 0

    for i in range(len(steps) - 1, -1, -1):
        st = steps[i]
        if _is_turn_boundary_step(st):
            s_idx = st.get("step_index", i)
            c_hash = hashlib.sha256(str(st.get("content", "")).encode("utf-8")).hexdigest()[:12]
            turn_key = f"turn_{s_idx}_{c_hash}"
            turn_start_pos = i + 1
            break

    if turn_key == "turn_0_initial" and window_start_offset > 0:
        turn_key = _find_turn_key_before_offset(tp, window_start_offset)

    latest_step_idx: Optional[int] = None
    latest_text = ""
    latest_is_truncated = False

    for pos, st in enumerate(steps[turn_start_pos:], start=turn_start_pos):
        if st.get("type") == "PLANNER_RESPONSE" or st.get("source") == "MODEL":
            # 排除工具输出 GENERIC 步骤
            if st.get("type") == "GENERIC":
                continue
            content = st.get("content")
            if isinstance(content, str) and content.strip():
                raw_idx = st.get("step_index")
                latest_step_idx = int(raw_idx) if isinstance(raw_idx, int) else pos
                latest_text = content
                trunc_fields = st.get("truncated_fields") or []
                latest_is_truncated = isinstance(trunc_fields, list) and "content" in trunc_fields

    if latest_is_truncated and latest_step_idx is not None:
        full_map = user_words._load_truncated_contents_map(
            tp.parent / "transcript_full.jsonl",
            {latest_step_idx},
        )
        latest_text = full_map.get(latest_step_idx) or latest_text

    return turn_key, latest_step_idx, latest_text


# ④ 短等待识别、数值标记与相邻回复相似度

def is_short_status_reply(text: str) -> bool:
    """判定回复是否属于无提问的短状态等待回复"""
    if not text:
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_SHORT_REPLY_CHARS:
        return False

    low = stripped.lower()
    for qm in USER_DECISION_OR_QUESTION_MARKERS:
        if qm in low:
            return False

    return any(kw in low for kw in WAIT_KEYWORDS)


def normalize_reply_text(text: str) -> str:
    """归一化回复文本（去除标点与多余空白，但保留字母、汉字与数字以敏感识别迭代步数/残差变化）"""
    low = text.strip().lower()
    # 移除常见 ISO 时间戳如 2026-11-12T06:00:00 或 06:12:34，保留业务数值（步数、残差、百分比）
    low = re.sub(r"(?<!\d)\d{4}-\d{2}-\d{2}[t\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:z|[+-]\d{2}:\d{2})?(?![0-9a-z])", "", low)
    low = re.sub(r"(?<!\d)\d{2}:\d{2}:\d{2}(?!\d)", "", low)
    # 仅保留中英文字符、数字与科学计数法符号
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.+-]+", " ", low)
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_number_tokens(norm_text: str) -> List[str]:
    """提取归一化文本中的数值标记（如迭代步数 100、残差 1e-5、进度 45）"""
    return re.findall(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?", norm_text)


def _strip_wait_boilerplate(norm_text: str) -> str:
    """剥离通用等待套话词汇，提取短状态回复的核心任务主语"""
    s = norm_text
    for tok in WAIT_BOILERPLATE_TOKENS:
        s = s.replace(tok, " ")
    return re.sub(r"\s+", "", s).strip()


def are_replies_homogeneous(prev_norm: str, curr_norm: str) -> bool:
    """
    严格比对相邻两条短等待回复是否同质：
    1. 归一化文本完全一致 -> 同质；
    2. 若包含数值指标（如迭代步数、残差、完成度）且数值发生变化 -> 属于真实进展汇报，绝不判为复读；
    3. 数值序列相同（或均无数值）时：
       - 整句序列相似度 >= SIMILARITY_THRESHOLD，或
        - 剥离通用等待套话后的核心主语一致 / 高度相似 -> 判为同质等待复读。
    这里比较相邻短句的字符序列，不对长篇技术报告执行 N-gram 或段落去重。
    """
    if not prev_norm or not curr_norm:
        return False
    if prev_norm == curr_norm:
        return True

    prev_nums = _extract_number_tokens(prev_norm)
    curr_nums = _extract_number_tokens(curr_norm)
    if prev_nums != curr_nums:
        return False

    if difflib.SequenceMatcher(None, prev_norm, curr_norm).ratio() >= SIMILARITY_THRESHOLD:
        return True

    core_prev = _strip_wait_boilerplate(prev_norm)
    core_curr = _strip_wait_boilerplate(curr_norm)
    if core_prev == core_curr:
        return True
    if core_prev and core_curr:
        return difflib.SequenceMatcher(None, core_prev, core_curr).ratio() >= CORE_SIMILARITY_THRESHOLD
    return False


# ⑤ Stop 交接终审与每回合一次的防重复提醒

def _native_completion_bill(payload, state=None):
    import status_guard

    path = status_guard._dispatch_transcript(payload)
    native_turn, calls = status_guard._native_turn_snapshot(payload, all_turns=True)
    if path is None or not calls:
        return None
    pending = {str(c["call_id"]): c for c in calls if c["call_id"]}
    agents, tasks, wait_calls, completed = {}, {}, {}, {}
    first_call = min(c["index"] for c in calls)

    def finish(key, call, raw, failed=False, log=""):
        parsed = user_words._parse_codex_json_maybe(raw) if isinstance(raw, str) else raw
        parsed = parsed if isinstance(parsed, dict) else {}
        status = str(parsed.get("status") or "").lower()
        if status in ("running", "pending", "in_progress", "async_launched", "queued"):
            return
        args = call["args"]
        usage = parsed.get("token_usage") or parsed.get("usage") or {}
        channel = {"run_workflow": "native_workflow", "swarm": "native_swarm"}.get(call["name"], "native_subagent")
        completed[key] = {"stream_id": key, "channel": channel,
                          "native_id": str(call["call_id"] or key), "readonly": status_guard._native_readonly(args),
                          "engine": args.get("TypeName") or args.get("typeName") or args.get("agent_type") or args.get("subagent_type") or call["name"],
                          "model": args.get("model") or "继承主会话",
                          "status": "失败" if failed or parsed.get("error") or status in ("failed", "errored", "timeout", "timed_out") else "完成返回",
                          "token_usage": usage if isinstance(usage, dict) else {},
                          "receipt_file": log or str(path.resolve()) + "#" + key}
    try:
        with path.open("rb") as source:
            for index, line in enumerate(source):
                if index < first_call:
                    continue
                try:
                    entry = json.loads(user_words.decode_jsonl_line(line))
                except (ValueError, RecursionError):
                    continue
                if not isinstance(entry, dict):
                    continue
                typ = entry.get("type")
                if typ in ("GENERIC", "TOOL_RESULT", "TOOL_RESPONSE"):
                    result = entry.get("tool_result") or entry
                    if not isinstance(result, dict):
                        continue
                    key = user_words._tool_call_id(result) or user_words._tool_call_id(entry)
                    raw = entry.get("content") or result.get("output") or ""
                    call = pending.get(key)
                    if call is None:
                        continue
                    if call["name"] == "run_workflow":
                        task = re.search(r"running in background.*?(task-[A-Za-z0-9_-]+)", str(raw), re.I | re.S)
                        if task:
                            tasks[task.group(1)] = call
                        elif not re.search(r"running in background|Created the following|in_progress", str(raw), re.I):
                            finish(key, call, raw, bool(entry.get("error") or result.get("error")))
                    else:
                        match = re.search(r"Created the following subagent:\s*(\{.*\})", str(raw), re.S)
                        if match:
                            meta = user_words._parse_codex_json_maybe(match.group(1))
                            if isinstance(meta, dict) and meta.get("conversationId"):
                                agents[meta["conversationId"]] = (call, meta.get("logAbsoluteUri") or "")
                        elif not re.search(r"running|pending|spawned", str(raw), re.I):
                            finish(key, call, raw, bool(entry.get("error") or result.get("error")))
                elif typ == "SYSTEM_MESSAGE":
                    content = str(entry.get("content") or "")
                    sender, body = user_words._extract_agy_subagent_return_body(content)
                    if sender in agents and body:
                        call, log = agents[sender]
                        finish(sender, call, body, log=log)
                    notice = re.search(r"\[Message\][^\n]*\bsender=([^\s]+)[^\n]*\bcontent=(.*)", content, re.S)
                    if notice:
                        task_id = notice.group(1).rsplit("/", 1)[-1]
                        if task_id in tasks and re.search(r"finished|completed|failed|timed out|完成|失败|超时", notice.group(2), re.I):
                            finish(task_id, tasks[task_id], notice.group(2), bool(re.search(
                                r"failed|timed out|失败|超时", notice.group(2), re.I)))
                elif typ == "user":
                    msg = entry.get("message") or {}
                    result = entry.get("toolUseResult") or {}
                    if not isinstance(result, dict):
                        result = {}
                    if result.get("status") in ("async_launched", "running", "pending", "in_progress"):
                        aid = str(result.get("agentId") or result.get("agent_id") or result.get("task_id") or "")
                        for block in msg.get("content", []) if isinstance(msg.get("content"), list) else []:
                            if not isinstance(block, dict):
                                continue
                            key = str(block.get("tool_use_id") or "")
                            if aid and key in pending:
                                agents[aid] = (pending[key], result.get("agent_transcript_path") or result.get("transcript_path") or "")
                        continue
                    for block in msg.get("content", []) if isinstance(msg.get("content"), list) else []:
                        if not isinstance(block, dict):
                            continue
                        key = str(block.get("tool_use_id") or "")
                        if block.get("type") == "tool_result" and key in pending:
                            finish(key, pending[key], result or block.get("content"), bool(block.get("is_error")),
                                   result.get("agent_transcript_path") or result.get("transcript_path") or "")
                        elif block.get("type") == "tool_result" and wait_calls.get(key) in agents:
                            aid = wait_calls[key]
                            call, log = agents[aid]
                            finish(aid, call, result or block.get("content"), bool(block.get("is_error")), log)
                elif typ == "assistant":
                    msg = entry.get("message") or {}
                    for block in msg.get("content", []) if isinstance(msg.get("content"), list) else []:
                        if not isinstance(block, dict) or block.get("name") != "TaskOutput":
                            continue
                        args = block.get("input") or {}
                        if isinstance(args, dict):
                            wait_calls[str(block.get("id") or "")] = str(args.get("task_id") or "")
                elif typ == "response_item":
                    rec = entry.get("payload") or {}
                    if rec.get("type") != "function_call_output":
                        continue
                    key = str(rec.get("call_id") or "")
                    raw = user_words._parse_codex_json_maybe(rec.get("output"))
                    if not isinstance(raw, dict):
                        continue
                    if key in pending and raw.get("agent_id"):
                        agents[str(raw["agent_id"])] = (pending[key], raw.get("log_path") or "")
                    states = raw.get("status")
                    if isinstance(states, dict):
                        for aid, result in states.items():
                            if aid not in agents:
                                continue
                            terminal = isinstance(result, dict) and any(k in result for k in ("completed", "failed", "errored"))
                            if terminal:
                                call, log = agents[aid]
                                finish(aid, call, result, "failed" in result or "errored" in result, log)
                    elif key in pending and (raw.get("completed") or raw.get("status") in ("completed", "failed", "errored")):
                        finish(key, pending[key], raw)
    except OSError:
        return None
    reported = set((state or {}).get("reported_native_ids", []))
    rows = [row for row in completed.values() if row["native_id"] not in reported]
    if not rows or not (len(rows) >= status_guard._heavy_min_streams() or any(
            r["channel"] in ("native_workflow", "native_swarm") or not r["readonly"] for r in rows)):
        return None
    token_keys = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    summary = {key: sum(row["token_usage"][key] for row in rows
                        if type(row["token_usage"].get(key)) is int)
               for key in token_keys if any(type(row["token_usage"].get(key)) is int for row in rows)}
    return {"streams": rows, "receipt_file": str(path.resolve()), "token_usage_summary": summary,
            "native": True, "turn_key": native_turn}


def _render_heavy_bill(entries):
    lines = ["| 流号 | 通道与模型 | 执行结果 | Token 明细与回执路径 |",
             "| --- | --- | --- | --- |"]
    def usage_text(usage):
        if not isinstance(usage, dict):
            usage = {}
        numbers = {k: v for k, v in usage.items() if k.endswith("tokens") and type(v) is int}
        return json.dumps(numbers, ensure_ascii=False, sort_keys=True) if numbers else "该通道未单独暴露 Token 整数，无法统计"
    for entry in entries:
        for row in entry.get("streams", []):
            outcome = row.get("status") or ("成功" if row.get("succeeded") else "失败")
            if "succeeded" in row:
                outcome = ("成功" if row["succeeded"] else "失败") + " / " + str(outcome)
            lines.append("| " + " | ".join(str(v).replace("\n", " ").replace("|", "\\|") for v in (
                row.get("stream_id", "未提供流号"), str(row.get("channel") or "cae_harness_headless") + " " +
                str(row.get("engine") or "未提供 agent") + " / " + str(row.get("model") or "未提供模型"),
                outcome, usage_text(row.get("token_usage")) + "；" + str(row.get("receipt_file") or entry.get("receipt_file") or "未提供回执路径"))) + " |")
        lines.append("Token 汇总：" + usage_text(entry.get("token_usage_summary")) + "；回执：" + str(entry.get("receipt_file") or "未提供"))
    return "\n".join(lines)


def _heavy_report_blocked(state_dir, sid, turn_key, payload):
    state = user_words._read_state_json(state_dir / (sid + "_heavy_report_guard.json")) or {}
    if state.get("native_turn"):
        import status_guard
        native_turn, calls = status_guard._native_turn_snapshot(payload)
        if calls and state["native_turn"] == native_turn:
            return state.get("blocked") is True
    return state.get("turn") == turn_key and state.get("blocked") is True


def _check_heavy_report_locked(payload, state_dir, sid, turn_key, reply, decision):
    if os.environ.get("CAE_HARNESS_DEPTH") or os.environ.get("USER_GUARD_DISABLE_TICKET_GATE") == "1":
        return None
    entries = user_words.load_unreported_heavy_completions(sid, state_dir)
    state_file = state_dir / (sid + "_heavy_report_guard.json")
    state = user_words._read_state_json(state_file) or {}
    native = _native_completion_bill(payload, state)
    same_native_turn = native and state.get("native_turn") == native["turn_key"]
    if state.get("turn") != turn_key and not same_native_turn:
        state = {"turn": turn_key, "reported_native_ids": state.get("reported_native_ids", [])}
    if native:
        state["native_turn"] = native["turn_key"]
    native_key = hashlib.sha256(json.dumps(native, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() if native else ""
    if native and state.get("native_reported") != native_key:
        entries.append(native)
    if not entries:
        return None
    status = bool(re.search(r"成功|完成|失败|超时|succeeded|completed|failed|timeout|\bok\b", reply, re.I))
    tokens = bool(re.search(r"tokens?[^\n。；]*\d|\d[\d, ]*\s*tokens?|tokens?[^\n。；]*(?:未.*(?:暴露|提供|统计)|无法|不.*(?:暴露|提供))|未[^\n。；]*(?:暴露|提供)[^\n。；]*tokens?", reply, re.I))
    receipt = bool(re.search(r"receipt|回执|轨迹|日志|[\w-]+\.(?:jsonl|json|log)\b", reply, re.I))
    def has_token(token):
        return bool(token and re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(token)}(?![A-Za-z0-9_.-])", reply))

    bound = True
    for entry in entries:
        rows = entry.get("streams", [])
        if entry.get("native"):
            ids_present = all(has_token(str(row["stream_id"])) for row in rows)
            roles = [str(row.get("engine") or "") for row in rows]
            roles_present = len(set(roles)) == len(rows) and all(role and role in reply for role in roles)
            count = len(rows)
            chinese = {1: "一", 2: "两|二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}.get(count)
            count_pattern = rf"(?<!\d){count}\s*(?:路|个|名|位|agents?\b)"
            if chinese:
                count_pattern += rf"|(?:{chinese})\s*(?:路|个|名|位)"
            covered = ids_present or roles_present or bool(re.search(count_pattern, reply, re.I))
            logs = bool(re.search(r"日志|轨迹|[\w-]+\.(?:jsonl|json|log)\b", reply, re.I))
            bound &= covered and logs
        else:
            for row in rows:
                stream_id = str(row.get("stream_id") or "")
                if stream_id and not has_token(stream_id):
                    single_task = len(rows) == 1 and stream_id == "S1" and entry.get("subcommand") in ("task", "gate")
                    model = str(row.get("model") or "")
                    task_status = str(row.get("status") or "")
                    bound &= single_task and bool((model and model in reply) or (task_status and task_status in reply))
            if entry.get("receipt_file"):
                filename = str(entry["receipt_file"]).replace(chr(92), "/").rsplit("/", 1)[-1]
                bound &= has_token(filename)
    if status and tokens and receipt and bound:
        user_words.mark_heavy_completions_reported(sid, state_dir)
        state["native_reported"] = native_key
        if native:
            state["reported_native_ids"] = list(dict.fromkeys(state.get("reported_native_ids", []) + [
                row["native_id"] for row in native["streams"]]))
        user_words.atomic_write_json(state_file, state)
        return None
    if state.get("blocked"):
        return None
    repeat = user_words._read_state_json(state_dir / (sid + "_repeat_guard.json")) or {}
    handoff = user_words._read_state_json(state_dir / (sid + "_handoff_gate.json")) or {}
    if (repeat.get("turns", {}).get(turn_key, {}).get("blockedSignatures")
            or turn_key in handoff.get("stopGateBlockedTurnKeys", [])):
        return None
    state["blocked"] = True
    user_words.atomic_write_json(state_file, state)
    return {"decision": decision, "reason": "【user-guard 重负载完工报账】请在本轮回复补齐各路成败、Token 明细与汇总、回执或子代理日志位置。磁盘账单如下：\n" + _render_heavy_bill(entries)}


def _handle_stop_locked(payload: Dict[str, Any], state_dir: Path, conversation_id: str) -> Dict[str, Any]:
    turn_key, step_idx, latest_reply = extract_current_turn_latest_reply(payload.get("transcriptPath"))

    # 若当前会话处于长会话 Stage-2 (drain/freeze)，优先验收交接完整性与 /goal 停止标记（每回合至多阻断 1 次）
    last_input = user_words.clean_user_content(payload.get("lastUserInput"))
    goal_active = user_words.agy_goal_command(last_input)
    compaction_file = state_dir / f"{conversation_id}_compaction.json"
    try:
        compaction_state = json.loads(compaction_file.read_text(encoding="utf-8-sig"))
        if (isinstance(compaction_state, dict) and compaction_state.get("conversationId") == conversation_id
                and compaction_state.get("hasExplicitUserInput") is True):
            # 已按步骤对账的用户输入及后续取消标记优先于 Stop 中的旧首轮输入。
            goal_active = None
    except (OSError, ValueError, RecursionError):
        pass
    handoff_resp = handoff_gate.check_stop_handoff_locked(
        payload, state_dir, turn_key, latest_reply, goal_active=goal_active,
    )
    if handoff_resp is not None and handoff_resp.get("decision") != "allow":
        return handoff_resp

    heavy = _check_heavy_report_locked(payload, state_dir, conversation_id, turn_key, latest_reply, "continue")
    if heavy:
        return heavy
    if not latest_reply:
        return {"decision": "allow"}

    state_file = state_dir / f"{conversation_id}_repeat_guard.json"
    state = _load_repeat_state(state_file, conversation_id)
    turns = state.setdefault("turns", {})
    turn_rec = turns.setdefault(
        turn_key,
        {
            "consecutiveShortWaitCount": 0,
            "lastNormalizedReply": "",
            "lastStepIndex": None,
            "blockedSignatures": [],
        },
    )

    # 同一 step_index 幂等保护（防止重复投递误累加）
    last_step = turn_rec.get("lastStepIndex")
    if step_idx is not None and isinstance(last_step, int) and step_idx <= last_step:
        return {"decision": "allow"}

    if not is_short_status_reply(latest_reply):
        turn_rec["consecutiveShortWaitCount"] = 0
        turn_rec["lastNormalizedReply"] = ""
        turn_rec["lastStepIndex"] = step_idx
        _save_repeat_state(state_file, state, conversation_id)
        return {"decision": "allow"}

    curr_norm = normalize_reply_text(latest_reply)
    prev_norm = str(turn_rec.get("lastNormalizedReply") or "")
    prev_count = int(turn_rec.get("consecutiveShortWaitCount", 0))

    if prev_count > 0 and are_replies_homogeneous(prev_norm, curr_norm):
        count = prev_count + 1
    else:
        # 首次出现短等待回复，或本条包含新的迭代步数/实质进展信息 -> 从 1 重新计数
        count = 1

    turn_rec["consecutiveShortWaitCount"] = count
    turn_rec["lastNormalizedReply"] = curr_norm
    turn_rec["lastStepIndex"] = step_idx

    blocked_sigs: List[str] = turn_rec.setdefault("blockedSignatures", [])
    sig = hashlib.sha256(curr_norm.encode("utf-8")).hexdigest()[:16]

    # fullyIdle=false 仍可能需要交接，也允许单次防重复提醒。它只表示存在后台任务。
    if count >= CONSECUTIVE_WAIT_LIMIT and not blocked_sigs and not _heavy_report_blocked(state_dir, conversation_id, turn_key, payload):
        blocked_sigs.append(sig)
        _save_repeat_state(state_file, state, conversation_id)
        log_event(
            "Stop:reply_repeat_guard",
            conversation_id,
            f"BLOCKED homogeneous short wait reply (count={count}) in {turn_key}",
        )
        return {
            "decision": "continue",
            "reason": _build_block_reason_text(),
        }

    _save_repeat_state(state_file, state, conversation_id)
    log_event(
        "Stop:reply_repeat_guard",
        conversation_id,
        f"ALLOWED short wait reply (count={count}/{CONSECUTIVE_WAIT_LIMIT}) in {turn_key}",
    )
    return {"decision": "allow"}


def handle_stop_repeat_guard(payload: Dict[str, Any]) -> Dict[str, Any]:
    """处理 Stop 事件防复读刷屏检测"""
    _sync_repeat_tunables()
    conversation_id = str(payload.get("conversationId") or "unknown_session").strip()
    termination_reason = str(payload.get("terminationReason") or "").strip().casefold()

    # 1. 非正常无工具结束时直接放行
    if (termination_reason not in ("model_stop", "no_tool_call") or payload.get("error")
            or not user_words.valid_session_id(conversation_id)):
        return {"decision": "allow"}

    # 2. 官方防死循环协议规范：若当前回合已处于 Stop 钩子触发的续跑状态（兼容 App camelCase 与 CLI v1.5.6+ snake_case），立即放行
    if payload.get("continuationActive") is True or payload.get("continuation_active") is True or payload.get("stop_hook_active") is True:
        return {"decision": "allow"}

    state_dir, _ = get_data_dirs()
    try:
        with user_words.session_state_lock(state_dir, conversation_id):
            return _handle_stop_locked(payload, state_dir, conversation_id)
    except (OSError, ValueError) as exc:
        log_event("Stop:state_error", conversation_id, f"Stop 状态未更新（{exc}）")
        return {"decision": "allow"}


def _claude_reverse_entries(path: Path):
    """从末尾分块读取，保留行首字节偏移，回合超出尾部窗口时继续向前找。"""
    with path.open("rb") as stream:
        position = stream.seek(0, 2)
        carry = b""
        while position:
            start = max(0, position - TAIL_SCAN_BYTES)
            stream.seek(start)
            data = stream.read(position - start) + carry
            lines = data.split(b"\n")
            carry = lines.pop(0) if start else b""
            offset = start + len(carry) + 1 if start else 0
            located = []
            for line in lines:
                located.append((offset, line))
                offset += len(line) + 1
            for row_offset, raw in reversed(located):
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(user_words.decode_jsonl_line(raw))
                except (ValueError, RecursionError):
                    continue
                if isinstance(entry, dict):
                    yield row_offset, entry
            position = start


def _claude_turn_reply(payload: Dict[str, Any]) -> Tuple[str, Optional[int], str, str]:
    supplied = payload.get("last_assistant_message")
    latest_reply = supplied.strip() if isinstance(supplied, str) else ""
    _, transcript, _, skip = user_words.claude_hook_session(payload)
    if skip or transcript is None:
        return "turn_0_initial", None, latest_reply, ""
    step: Optional[int] = None
    message_id = ""
    turn_key = "turn_0_initial"
    pending_question: Optional[Tuple[int, Dict[str, Any], Set[str]]] = None
    try:
        for offset, entry in _claude_reverse_entries(transcript):
            if entry.get("isSidechain") is True or entry.get("isMeta") is True:
                continue
            if pending_question is not None and entry.get("type") == "assistant":
                cand_offset, cand_entry, cand_ids = pending_question
                q_ids = user_words._claude_question_ids_from_assistant(entry)
                if q_ids & cand_ids:
                    q_text, _ = user_words.claude_user_content(cand_entry, question_ids=q_ids)
                    if q_text:
                        identity = str(cand_entry.get("uuid") or cand_entry.get("id") or cand_offset)
                        turn_key = "turn_" + hashlib.sha256((identity + ":" + q_text).encode("utf-8")).hexdigest()[:24]
                        break
                msg = entry.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                tool_ids = {
                    str(block["id"]) for block in (content if isinstance(content, list) else [])
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
                }
                if cand_ids & tool_ids:
                    pending_question = None
            text, kind = user_words.claude_user_content(entry)
            if text and kind in ("user_input", "queued_command"):
                identity = str(entry.get("uuid") or entry.get("id") or offset)
                turn_key = "turn_" + hashlib.sha256((identity + ":" + text).encode("utf-8")).hexdigest()[:24]
                break
            if text and kind == "ask_question_answer":
                r_ids = user_words._claude_tool_result_ids(entry)
                if r_ids and pending_question is None:
                    pending_question = (offset, entry, r_ids)
                continue
            if entry.get("type") != "assistant" or step is not None:
                continue
            msg = entry.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                text = "\n".join(block["text"] for block in content if isinstance(block, dict)
                                 and block.get("type") == "text" and isinstance(block.get("text"), str))
            else:
                text = content if isinstance(content, str) else ""
            if text.strip():
                step = offset
                message_id = str(entry.get("uuid") or entry.get("id") or "")
                if not latest_reply:
                    latest_reply = text.strip()
        if step is None and latest_reply:
            # 宿主可能在助手记录刷盘前调用 Stop；同一快照的重复投递仍须幂等。
            step = transcript.stat().st_size
    except OSError:
        return turn_key, None, latest_reply, ""
    return turn_key, step, latest_reply, message_id


def _codex_turn_reply(payload):
    import status_guard

    turn, _ = status_guard._native_turn_snapshot(payload)
    reply = str(payload.get("last_assistant_message") or "")
    path = status_guard._dispatch_transcript(payload)
    step = None
    if path:
        try:
            with path.open("rb") as source:
                for index, line in enumerate(source):
                    try:
                        entry = json.loads(user_words.decode_jsonl_line(line))
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(entry, dict):
                        continue
                    rec = entry.get("payload") or {}
                    if entry.get("type") == "response_item" and rec.get("type") == "message" and rec.get("role") == "assistant":
                        text = "\n".join(b.get("text", "") for b in rec.get("content", []) if isinstance(b, dict))
                        if text:
                            step = index
                            if not payload.get("last_assistant_message"):
                                reply = text
            if step is None:
                step = path.stat().st_size
        except OSError:
            pass
    return turn, step, reply, ""


def handle_claude_stop_repeat_guard(payload: Dict[str, Any], state_dir: Optional[Path] = None,
                                   logs_dir: Optional[Path] = None) -> Dict[str, Any]:
    _sync_repeat_tunables()
    if payload.get("stop_hook_active") is True or payload.get("continuationActive") is True or payload.get("continuation_active") is True:
        return {}
    sid, _, _, skip = user_words.claude_hook_session(payload)
    if skip or not user_words.valid_session_id(sid):
        return {}
    client = payload.get("_client", "claude")
    home = Path(os.environ.get("CODEX_HOME" if client == "codex" else "CLAUDE_CONFIG_DIR") or Path.home() / (".codex" if client == "codex" else ".claude")).expanduser()
    state_dir = Path(state_dir).expanduser() if state_dir is not None else home / "hooks" / "state"
    logs_dir = Path(logs_dir).expanduser() if logs_dir is not None else home / "hooks" / "logs"
    turn_key, step, reply, message_id = (_codex_turn_reply(payload) if client == "codex" else _claude_turn_reply(payload))
    try:
        with user_words.session_state_lock(state_dir, sid):
            heavy = _check_heavy_report_locked(payload, state_dir, sid, turn_key, reply, "block")
            if heavy:
                return heavy
            if not reply:
                return {}
            state_file = state_dir / f"{sid}_repeat_guard.json"
            state = _load_repeat_state(state_file, sid, logs_dir)
            record = state["turns"].setdefault(turn_key, {
                "consecutiveShortWaitCount": 0, "lastNormalizedReply": "", "lastStepIndex": None,
                "blockedSignatures": [],
            })
            if ((step is not None and step == record["lastStepIndex"])
                    or (message_id and message_id == record.get("lastMessageId"))):
                return {}
            record.update(lastStepIndex=step, lastMessageId=message_id)
            if is_short_status_reply(reply):
                normalized = normalize_reply_text(reply)
                previous = record["lastNormalizedReply"]
                record["consecutiveShortWaitCount"] = (record["consecutiveShortWaitCount"] + 1
                    if are_replies_homogeneous(previous, normalized) else 1)
                record["lastNormalizedReply"] = normalized
            else:
                record.update(consecutiveShortWaitCount=0, lastNormalizedReply="")
            block = (record["consecutiveShortWaitCount"] >= CONSECUTIVE_WAIT_LIMIT and not record["blockedSignatures"]
                     and not _heavy_report_blocked(state_dir, sid, turn_key, payload))
            if block:
                record["blockedSignatures"].append(hashlib.sha256(record["lastNormalizedReply"].encode("utf-8")).hexdigest()[:16])
            _save_repeat_state(state_file, state, sid)
            if block:
                user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
                    "event": "Stop:reply_repeat_guard", "client": "claude", "session_id": sid,
                    "turn_key": turn_key, "count": record["consecutiveShortWaitCount"],
                })
                return {"decision": "block", "reason": _build_block_reason_text()}
    except (OSError, ValueError) as exc:
        user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
            "event": "Stop:state_error", "client": "claude", "session_id": sid, "message": str(exc),
        })
    return {}


# ⑥ 命令行主入口

def main() -> None:
    parser = argparse.ArgumentParser(description="Stop 短状态等待回复门禁")
    parser.add_argument("--client", choices=("antigravity", "claude", "codex"), default="antigravity")
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--log-dir", default="")
    args, _ = parser.parse_known_args()
    payload: Dict[str, Any] = {}
    try:
        payload = user_words.read_hook_payload()
    except (OSError, ValueError) as e:
        if args.client in ("claude", "codex"):
            sys.stderr.write(f"Claude Stop JSON parse error: {e}\n")
        else:
            log_event("Stop:reply_repeat_guard", "unknown", f"Failed to parse stdin JSON: {e}")

    try:
        response = (handle_claude_stop_repeat_guard({**payload, "_client": args.client}, Path(args.state_dir) if args.state_dir else None,
                                                  Path(args.log_dir) if args.log_dir else None)
                    if args.client in ("claude", "codex") else handle_stop_repeat_guard(payload))
    except Exception as e:
        stack_str = traceback.format_exc()
        if args.client in ("claude", "codex"):
            sys.stderr.write(f"Claude Stop: {e}\n")
            response = {}
        else:
            log_event("Stop:reply_repeat_guard", str(payload.get("conversationId", "unknown")), f"Exception: {e}\n{stack_str}")
            response = {"decision": "allow"}

    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
