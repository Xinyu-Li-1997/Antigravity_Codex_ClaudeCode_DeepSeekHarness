# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
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
from _lib import adaptive_policy as _adaptive_policy  # noqa: E402
from _lib.process_guard import install_hook_self_guard  # noqa: E402

user_words.ensure_utf8_stdio()

MAX_SHORT_REPLY_CHARS = 300
TAIL_SCAN_BYTES = 256 * 1024  # 256 KB
SIMILARITY_THRESHOLD = 0.80
CORE_SIMILARITY_THRESHOLD = 0.75
CONSECUTIVE_WAIT_LIMIT = 3
EVIDENCE_GATE_MODE = "advisory"
NATIVE_BILL_GATE_MODE = "advisory"

_REPEAT_BUILTINS: Dict[str, Any] = {
    "MAX_SHORT_REPLY_CHARS": MAX_SHORT_REPLY_CHARS,
    "TAIL_SCAN_BYTES": TAIL_SCAN_BYTES,
    "SIMILARITY_THRESHOLD": SIMILARITY_THRESHOLD,
    "CORE_SIMILARITY_THRESHOLD": CORE_SIMILARITY_THRESHOLD,
    "CONSECUTIVE_WAIT_LIMIT": CONSECUTIVE_WAIT_LIMIT,
    "EVIDENCE_GATE_MODE": EVIDENCE_GATE_MODE,
    "NATIVE_BILL_GATE_MODE": NATIVE_BILL_GATE_MODE,
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
    raw_ev_mode = str(cfg.get("evidence_gate_mode") or _REPEAT_BUILTINS["EVIDENCE_GATE_MODE"]).strip().lower()
    if raw_ev_mode not in ("advisory", "enforce", "off"):
        raw_ev_mode = _REPEAT_BUILTINS["EVIDENCE_GATE_MODE"]
    raw_native_mode = str(cfg.get("native_bill_gate_mode") or _REPEAT_BUILTINS["NATIVE_BILL_GATE_MODE"]).strip().lower()
    if raw_native_mode not in ("advisory", "enforce", "off"):
        raw_native_mode = _REPEAT_BUILTINS["NATIVE_BILL_GATE_MODE"]
    resolved: Dict[str, Any] = {
        "MAX_SHORT_REPLY_CHARS": user_words._coerce_pos_int(cfg.get("max_short_reply_chars"), 300, minimum=50),
        "TAIL_SCAN_BYTES": user_words._coerce_pos_int(cfg.get("tail_scan_kib"), 256, minimum=16) * 1024,
        "SIMILARITY_THRESHOLD": user_words._coerce_ratio(cfg.get("similarity_threshold"), 0.80, low=0.50, high=0.98),
        "CORE_SIMILARITY_THRESHOLD": user_words._coerce_ratio(cfg.get("core_similarity_threshold"), 0.75, low=0.45, high=0.98),
        "CONSECUTIVE_WAIT_LIMIT": user_words._coerce_pos_int(cfg.get("consecutive_wait_limit"), 3, minimum=2),
        "EVIDENCE_GATE_MODE": raw_ev_mode,
        "NATIVE_BILL_GATE_MODE": raw_native_mode,
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

def _build_block_reason_text(client: str = "antigravity") -> str:
    _ = client
    base = BLOCK_REASON_TEXT
    if CONSECUTIVE_WAIT_LIMIT == 3:
        return base
    return base.replace("连续三次", f"连续 {CONSECUTIVE_WAIT_LIMIT} 次")


# ② 目录、日志与防重复状态读写

def get_data_dirs() -> Tuple[Path, Path]:
    return user_words.resolve_data_dirs(HOOKS_DIR)


def get_current_iso_time() -> str:
    return user_words.current_iso_time()


def log_event(event_name: str, conversation_id: str, message: str) -> None:
    user_words.append_lifecycle_log(HOOKS_DIR, event_name, conversation_id, message)


def _load_repeat_state(
    state_file: Path,
    conversation_id: str,
    logs_dir: Optional[Path] = None,
    client: str = "claude",
) -> Dict[str, Any]:
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
                            or (record.get("lastStepIndex") is not None and type(record["lastStepIndex"]) is not int)
                            or ("shownPendingTickets" in record and not isinstance(record["shownPendingTickets"], dict))):
                        raise ValueError("回合复读记录格式错误")
                return data
            raise ValueError("状态格式或会话身份不符")
        except (OSError, ValueError, RecursionError) as exc:
            if logs_dir is None:
                log_event("Stop:repeat_state_error", conversation_id, f"防重复状态读取失败（{exc}），保留原文件")
            else:
                user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
                    "event": "Stop:repeat_state_error", "client": client, "session_id": conversation_id,
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
    # JSON 字符串允许含 U+2028 等字符，只能按真实换行字节分行，并记录精确行首字节偏移 row_offset。
    parts = raw.split(b"\n")
    cur_offset = start_offset
    for idx_part, line in enumerate(parts):
        row_offset = cur_offset
        cur_offset += len(line) + (1 if idx_part < len(parts) - 1 else 0)
        if not line.strip():
            continue
        try:
            obj = json.loads(user_words.decode_jsonl_line(line))
            if isinstance(obj, dict):
                obj["row_offset"] = row_offset
                steps.append(obj)
        except (ValueError, RecursionError):
            continue
    return steps, start_offset


def _is_explicit_user_step(step: Dict[str, Any]) -> bool:
    return step.get("type") == "USER_INPUT" or step.get("source") == "USER_EXPLICIT"


def _is_turn_boundary_step(step: Dict[str, Any], allow_system_message: bool = True) -> bool:
    """识别回合起点：人类显式 USER_INPUT；仅当无显式人类输入（纯子代理会话）时允许 SYSTEM_MESSAGE"""
    if _is_explicit_user_step(step):
        return True
    if allow_system_message and step.get("type") == "SYSTEM_MESSAGE":
        return bool(user_words.clean_user_content(step.get("content"), "SYSTEM_MESSAGE"))
    return False


def _step_cursor_value(step: Dict[str, Any], fallback_offset: int) -> int:
    raw_idx = step.get("step_index")
    return raw_idx if type(raw_idx) is int else fallback_offset


def _iter_located_lines_before_offset(
    transcript_path: Path,
    end_offset: int,
    lower_bound: int = 0,
):
    pos = end_offset
    carry = b""
    with open(transcript_path, "rb") as f:
        while pos > lower_bound:
            chunk_start = max(lower_bound, pos - TAIL_SCAN_BYTES)
            f.seek(chunk_start)
            chunk = f.read(pos - chunk_start)
            buf = chunk + carry
            lines = buf.split(b"\n")
            if chunk_start > lower_bound:
                carry = lines[0]
                candidate_lines = lines[1:]
                offset = chunk_start + len(carry) + 1
            elif chunk_start > 0:
                # 触及非文件头下界时，首段可能是半行，跳过半行并精确对齐其后行首偏移。
                dropped = lines[0]
                carry = b""
                candidate_lines = lines[1:]
                offset = chunk_start + len(dropped) + 1
            else:
                carry = b""
                candidate_lines = lines
                offset = 0
            located: List[Tuple[int, bytes]] = []
            for idx_l, raw_line in enumerate(candidate_lines):
                located.append((offset, raw_line))
                offset += len(raw_line) + (1 if idx_l < len(candidate_lines) - 1 else 0)
            for row_offset, raw_line in reversed(located):
                yield row_offset, raw_line
            pos = chunk_start


def _find_turn_key_before_offset(
    transcript_path: Path,
    end_offset: int,
    allow_system_message: bool = True,
) -> str:
    """当单回合轨迹超过尾部 256KB 窗口时，向前分块逆序定位最近一次回合起点并返回 turn_<cursor>_<hash>"""
    if end_offset <= 0:
        return "turn_0_initial"
    fallback_sys_key: Optional[str] = None
    try:
        for row_offset, raw_line in _iter_located_lines_before_offset(transcript_path, end_offset, 0):
            stripped = raw_line.strip()
            if not stripped:
                continue
            if (
                b'"USER_INPUT"' not in stripped
                and b'"USER_EXPLICIT"' not in stripped
                and (not allow_system_message or b"[Message]" not in stripped)
            ):
                continue
            try:
                st = json.loads(user_words.decode_jsonl_line(stripped))
            except (ValueError, RecursionError):
                continue
            if not isinstance(st, dict):
                continue
            if _is_explicit_user_step(st):
                s_idx = _step_cursor_value(st, row_offset)
                c_hash = hashlib.sha256(str(st.get("content", "")).encode("utf-8")).hexdigest()[:12]
                return f"turn_{s_idx}_{c_hash}"
            if allow_system_message and fallback_sys_key is None and _is_turn_boundary_step(st, allow_system_message=True):
                s_idx = _step_cursor_value(st, row_offset)
                c_hash = hashlib.sha256(str(st.get("content", "")).encode("utf-8")).hexdigest()[:12]
                fallback_sys_key = f"turn_{s_idx}_{c_hash}"
    except OSError:
        pass
    return fallback_sys_key or "turn_0_initial"


def _has_explicit_user_before_offset(transcript_path: Path, end_offset: int) -> bool:
    if end_offset <= 0:
        return False
    try:
        for _, raw_line in _iter_located_lines_before_offset(transcript_path, end_offset, 0):
            stripped = raw_line.strip()
            if not stripped or (b'"USER_INPUT"' not in stripped and b'"USER_EXPLICIT"' not in stripped):
                continue
            try:
                st = json.loads(user_words.decode_jsonl_line(stripped))
            except (ValueError, RecursionError):
                continue
            if isinstance(st, dict) and _is_explicit_user_step(st):
                return True
    except OSError:
        pass
    return False


def extract_current_turn_latest_reply(transcript_path_str: Optional[str]) -> Tuple[str, Optional[int], str]:
    """
    提取当前回合键 turn_key、最新一条模型文本回复的游标（step_index 或 row_offset）及其内容。
    """
    if not transcript_path_str:
        return "turn_0_initial", None, ""

    tp = user_words.resolve_compact_transcript_path(Path(transcript_path_str).expanduser())
    if not tp.exists() or not tp.is_file():
        return "turn_0_initial", None, ""

    steps, window_start_offset = _read_tail_steps_with_offset(tp)
    if not steps and window_start_offset <= 0:
        return "turn_0_initial", None, ""

    has_explicit_user = any(_is_explicit_user_step(st) for st in steps)
    if not has_explicit_user and window_start_offset > 0:
        has_explicit_user = _has_explicit_user_before_offset(tp, window_start_offset)
    allow_sys_boundary = not has_explicit_user

    turn_key = "turn_0_initial"
    turn_start_pos = 0

    for i in range(len(steps) - 1, -1, -1):
        st = steps[i]
        if _is_turn_boundary_step(st, allow_system_message=allow_sys_boundary):
            s_idx = _step_cursor_value(st, int(st.get("row_offset", i)))
            c_hash = hashlib.sha256(str(st.get("content", "")).encode("utf-8")).hexdigest()[:12]
            turn_key = f"turn_{s_idx}_{c_hash}"
            turn_start_pos = i + 1
            break

    if turn_key == "turn_0_initial" and window_start_offset > 0:
        turn_key = _find_turn_key_before_offset(tp, window_start_offset, allow_system_message=allow_sys_boundary)

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
                latest_step_idx = _step_cursor_value(st, int(st.get("row_offset", pos)))
                latest_text = content
                trunc_fields = st.get("truncated_fields") or []
                latest_is_truncated = isinstance(trunc_fields, list) and "content" in trunc_fields

    # 当尾部窗口首部截断丢弃了超长 PLANNER_RESPONSE（其后又有短步骤追加）时，有界向前读取找回本回合回复。
    if latest_step_idx is None and turn_start_pos == 0 and window_start_offset > 0:
        lower_bound = max(0, window_start_offset - user_words.MAX_HOOK_INPUT_CHARS)
        try:
            for row_offset, raw_line in _iter_located_lines_before_offset(tp, window_start_offset, lower_bound):
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    st = json.loads(user_words.decode_jsonl_line(stripped))
                except (ValueError, RecursionError):
                    continue
                if not isinstance(st, dict):
                    continue
                if _is_turn_boundary_step(st, allow_system_message=allow_sys_boundary):
                    break
                if (st.get("type") == "PLANNER_RESPONSE" or st.get("source") == "MODEL") and st.get("type") != "GENERIC":
                    content = st.get("content")
                    if isinstance(content, str) and content.strip():
                        latest_step_idx = _step_cursor_value(st, row_offset)
                        latest_text = content
                        trunc_fields = st.get("truncated_fields") or []
                        latest_is_truncated = isinstance(trunc_fields, list) and "content" in trunc_fields
                        break
        except OSError:
            pass

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
    native_turn, all_calls = status_guard._native_turn_snapshot(payload, all_turns=True)
    calls = [c for c in all_calls if not c.get("denied")]
    if path is None or not calls:
        return None
    pending = {str(c["call_id"]): c for c in calls if c["call_id"]}
    unidentified_by_index: Dict[int, List[Dict[str, Any]]] = {}
    for c in all_calls:
        if not c["call_id"]:
            unidentified_by_index.setdefault(int(c["index"]), []).append(c)
    agy_batch_slots: List[Optional[Dict[str, Any]]] = []
    agents, tasks, wait_calls, completed = {}, {}, {}, {}
    first_call = min(c["index"] for c in all_calls)

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
                explicit_turn = typ == "USER_INPUT" or entry.get("source") == "USER_EXPLICIT"
                if typ == "user":
                    u_text, u_kind = user_words.claude_user_content(entry)
                    explicit_turn = bool(u_text and u_kind in ("user_input", "queued_command"))
                elif typ == "response_item":
                    rec_u = entry.get("payload")
                    if isinstance(rec_u, dict) and rec_u.get("type") == "message" and rec_u.get("role") == "user":
                        _, word = user_words._extract_codex_record(entry, index)
                        if word:
                            word["source_path"] = str(path)
                        explicit_turn = bool(word and user_words._codex_word_from_record(word))
                if explicit_turn:
                    completed.clear()
                    agy_batch_slots.clear()
                if typ == "PLANNER_RESPONSE":
                    raw_tcs = entry.get("tool_calls")
                    if isinstance(raw_tcs, list) and raw_tcs:
                        agy_batch_slots.clear()
                        step_native_queue = list(unidentified_by_index.get(index, []))
                        for tc in raw_tcs:
                            if not isinstance(tc, dict):
                                continue
                            if tc.get("call_id") or tc.get("id"):
                                continue
                            tc_name = status_guard._native_tool_name(str(tc.get("name") or ""))
                            tc_args = tc.get("args", tc.get("arguments", tc.get("input", {})))
                            if isinstance(tc_args, str):
                                try:
                                    tc_args = json.loads(tc_args)
                                except (ValueError, RecursionError):
                                    tc_args = {}
                            if isinstance(tc_args, dict) and status_guard._native_dispatch(tc_name, tc_args):
                                matched_c = next((item for item in step_native_queue if item["name"] == tc_name), None)
                                if matched_c is not None:
                                    step_native_queue.remove(matched_c)
                                    agy_batch_slots.append(matched_c)
                                else:
                                    agy_batch_slots.append(None)
                            else:
                                agy_batch_slots.append(None)
                    continue
                if typ in ("GENERIC", "TOOL_RESULT", "TOOL_RESPONSE"):
                    result = entry.get("tool_result") or entry
                    if not isinstance(result, dict):
                        continue
                    key = user_words._tool_call_id(result) or user_words._tool_call_id(entry)
                    raw = entry.get("content") or result.get("output") or ""
                    call = pending.get(key)
                    if call is None and not key and agy_batch_slots:
                        raw_str = str(raw)
                        if "tool call denied by pre-tool hook" in raw_str.lower():
                            matched_pos = next(
                                (i for i, s in enumerate(agy_batch_slots) if s is not None and s.get("denied")),
                                next((i for i, s in enumerate(agy_batch_slots) if s is not None), 0),
                            )
                            agy_batch_slots.pop(matched_pos)
                            continue
                        if user_words.parse_agy_created_subagent(raw_str) is not None:
                            matched_pos = next(
                                (i for i, s in enumerate(agy_batch_slots)
                                 if s is not None and not s.get("denied") and s["name"] == "invoke_subagent"),
                                next((i for i, s in enumerate(agy_batch_slots) if s is not None and not s.get("denied")), 0),
                            )
                        elif (
                            re.search(r"running (?:in (?:the )?background|as a background task)", raw_str, re.I)
                            and not re.search(r"Task Description:[^\n]*(?:cae_harness\.py|python|git|\$)", raw_str, re.I)
                            and any(s is not None and not s.get("denied") and s["name"] == "run_workflow" for s in agy_batch_slots)
                        ):
                            matched_pos = next(
                                (i for i, s in enumerate(agy_batch_slots)
                                 if s is not None and not s.get("denied") and s["name"] == "run_workflow"),
                                0,
                            )
                        elif None in agy_batch_slots and re.search(
                            r"(?:Showing lines|Partial read|Command exited|Created file|Made \d+ replacement|Task Description:[^\n]*(?:cae_harness\.py|python|git|\$))",
                            raw_str,
                        ):
                            matched_pos = agy_batch_slots.index(None)
                        else:
                            matched_pos = 0
                        call = agy_batch_slots.pop(matched_pos)
                        if call is not None and not call.get("denied"):
                            key = f"agy-step-{call['index']}-{call['name']}-{index}"
                    if call is None or call.get("denied"):
                        continue
                    if call["name"] == "run_workflow":
                        task = re.search(r"running (?:in (?:the )?background|as a background task).*?(task-[A-Za-z0-9_-]+)", str(raw), re.I | re.S)
                        if task:
                            tasks[task.group(1)] = call
                        elif not re.search(r"running (?:in (?:the )?background|as a background task)|Created the following|in_progress", str(raw), re.I):
                            finish(key, call, raw, bool(entry.get("error") or result.get("error")))
                    else:
                        meta = user_words.parse_agy_created_subagent(str(raw))
                        if meta is not None:
                            if meta.get("conversationId"):
                                agents[str(meta["conversationId"])] = (call, str(meta.get("logAbsoluteUri") or ""))
                        elif not re.search(r"running|pending|spawned", str(raw), re.I):
                            finish(key, call, raw, bool(entry.get("error") or result.get("error")))
                elif typ == "SYSTEM_MESSAGE":
                    content = str(entry.get("content") or "")
                    sender, body = user_words._extract_agy_subagent_return_body(content)
                    if sender in agents and body:
                        # 跳过多轮交互子代理（如 /teamwork）的访谈准备/中间询问/待批子票据消息，不误判为最终完工
                        is_intermediate = bool(re.search(
                            r"Your Teamwork run is being prepared|putting together a short task interview|"
                            r"运行正在准备中|【待确认派工|状态[：:]\s*`*PENDING`*",
                            body,
                            re.I,
                        ))
                        if not is_intermediate:
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
    if not rows or not (len(completed) >= status_guard._heavy_min_streams() or any(
            r["channel"] in ("native_workflow", "native_swarm") or not r["readonly"] for r in rows)):
        return None
    token_keys = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    summary = {key: sum(row["token_usage"][key] for row in rows
                        if type(row["token_usage"].get(key)) is int)
               for key in token_keys if any(type(row["token_usage"].get(key)) is int for row in rows)}
    return {"streams": rows, "receipt_file": str(path.resolve()), "token_usage_summary": summary,
            "native": True, "turn_key": native_turn, "all_succeeded": all(row["status"] != "失败" for row in rows)}


def _render_heavy_bill(entries):
    lines = ["| 流号 | 通道与模型 | 执行结果 | Token 明细与回执路径 |",
             "| --- | --- | --- | --- |"]
    def usage_text(usage):
        if not isinstance(usage, dict):
            usage = {}
        numbers = {k: v for k, v in usage.items() if k.endswith("tokens") and type(v) is int}
        if not numbers:
            return "该通道未单独暴露 Token 整数，无法统计"
        source = "（超时前部分用量，cli_partial_on_timeout）" if usage.get("usage_source") == "cli_partial_on_timeout" else ""
        return json.dumps(numbers, ensure_ascii=False, sort_keys=True) + source
    for entry in entries:
        for row in entry.get("streams", []):
            outcome = row.get("status") or ("成功" if row.get("succeeded") else "失败")
            if "succeeded" in row:
                outcome = ("成功" if row["succeeded"] else "失败") + " / " + str(outcome)
            lines.append("| " + " | ".join(str(v).replace("\r\n", "\n").replace("\r", " ").replace("\n", " ").replace("|", "\\|") for v in (
                row.get("stream_id", "未提供流号"), str(row.get("channel") or "cae_harness_headless") + " " +
                str(row.get("engine") or "未提供 agent") + " / " + str(row.get("model") or "未提供模型"),
                outcome, usage_text(row.get("token_usage")) + "；" + str(row.get("receipt_file") or entry.get("receipt_file") or "未提供回执路径"))) + " |")
        lines.append("Token 汇总：" + usage_text(entry.get("token_usage_summary")) + "；回执：" + str(entry.get("receipt_file") or "未提供").replace("\r\n", "\n").replace("\r", " ").replace("\n", " "))
        rec_file = str(entry.get("reconcile_file") or "").strip()
        if rec_file:
            rec_sum = str(entry.get("reconcile_summary") or "").strip()
            suffix = f"（{rec_sum}）" if rec_sum else ""
            lines.append("机械对账表：" + rec_file.replace("\r\n", "\n").replace("\r", " ").replace("\n", " ") + suffix)
    return "\n".join(lines)


def _heavy_report_blocked(state_dir, sid, turn_key, payload):
    state = user_words._read_state_json(state_dir / (sid + "_heavy_report_guard.json")) or {}
    if state.get("native_turn"):
        import status_guard
        native_turn, calls = status_guard._native_turn_snapshot(payload)
        if calls and state["native_turn"] == native_turn:
            return state.get("blocked") is True
    return state.get("turn") == turn_key and state.get("blocked") is True


def _has_token_report(reply: str) -> bool:
    if re.search(
        r"tokens?[^\n。；]*\d|\d[\d, ]*[`*_]*\s*[`*_]*tokens?"
        r"|tokens?[^\n。；]*(?:未.*(?:暴露|提供|统计)|无法|不.*(?:暴露|提供))"
        r"|未[^\n。；]*(?:暴露|提供)[^\n。；]*tokens?",
        reply,
        re.I,
    ):
        return True
    # 兼容 Markdown 表格账单：表头含 Token 列、紧随分隔线且对应 Token 列单元格含数字或未上报说明。
    lines = reply.splitlines()
    for idx in range(len(lines) - 2):
        hdr = lines[idx]
        sep = lines[idx + 1]
        if (
            "|" in hdr
            and re.search(r"tokens?", hdr, re.I)
            and re.match(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$", sep)
        ):
            hdr_cells = [c.strip() for c in hdr.strip().strip("|").split("|")]
            token_cols = [c_idx for c_idx, cell in enumerate(hdr_cells) if re.search(r"tokens?", cell, re.I)]
            if not token_cols:
                continue
            for row in lines[idx + 2:]:
                if "|" not in row:
                    break
                row_cells = [c.strip() for c in row.strip().strip("|").split("|")]
                for c_idx in token_cols:
                    if c_idx < len(row_cells) and re.search(r"\d|无法统计|未暴露|未提供|未上报|不暴露|不提供", row_cells[c_idx]):
                        return True
    return False


def _check_heavy_report_locked(payload, state_dir, sid, turn_key, reply, decision, *, reconcile_only: bool = False):
    if (
        os.environ.get("CAE_HARNESS_DEPTH", "").strip() not in ("", "0")
        or os.environ.get("USER_GUARD_DISABLE_TICKET_GATE") == "1"
    ):
        return None
    entries = user_words.load_unreported_heavy_completions(sid, state_dir)
    state_file = state_dir / (sid + "_heavy_report_guard.json")
    state = user_words._read_state_json(state_file) or {}
    native = _native_completion_bill(payload, state) if NATIVE_BILL_GATE_MODE == "enforce" else None
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
    facts = re.sub(r"(?:无|零|没有)(?:任何)?(?:失败|超时)|\b(?:no|0) (?:fail(?:ed|ures?)|timeouts?)\b", "", reply, flags=re.I)
    failure = re.search(r"失败|超时|部分(?:完成|成功|通过)|\b(?:fail(?:ed|ures?)|time(?:d)?[ _-]?out|partial(?:ly)?|"
                        r"invalid_output|verification_failed|scope_violation)\b", facts, re.I)
    status = failure or re.search(r"成功|完成|通过|\b(?:succeeded|completed|passed|ok)\b", reply, re.I)
    tokens = _has_token_report(reply)
    receipt = bool(re.search(r"receipt|回执|轨迹|日志|[\w-]+\.(?:jsonl|json|log)\b", reply, re.I))
    def has_token(token):
        if not token:
            return False
        # 允许词项后紧跟英文句末句点（只要句点后非标识符字符），同时大小写不敏感。
        return bool(re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(token)}(?!(?:[A-Za-z0-9_-]|\.[A-Za-z0-9_-]))", reply, re.I))

    ext_receipt_counts: Dict[str, int] = {}
    for entry in entries:
        if not entry.get("native") and entry.get("receipt_file"):
            fname_key = str(entry["receipt_file"]).replace(chr(92), "/").rsplit("/", 1)[-1].casefold()
            ext_receipt_counts[fname_key] = ext_receipt_counts.get(fname_key, 0) + 1

    bound = True
    missing_reconcile = False
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
                count_pattern += rf"|(?<![十百千万零一二三四五六七八九两第下另这每])(?:{chinese})\s*(?:路|个|名|位)"
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
                    has_concrete_model = bool(model and model != "inherit")
                    model_bound = has_token(model) if has_concrete_model else False
                    status_bound = (not has_concrete_model) and has_token(task_status)
                    bound &= single_task and (model_bound or status_bound)
            if entry.get("receipt_file"):
                norm_receipt = str(entry["receipt_file"]).replace(chr(92), "/")
                filename = norm_receipt.rsplit("/", 1)[-1]
                file_ok = has_token(filename)
                if ext_receipt_counts.get(filename.casefold(), 0) > 1:
                    # 多个未汇报运行共用同一裸文件名时，须以 run_id（前 8 位或完整）、唯一对账表文件名或完整路径区分。
                    rid = str(entry.get("run_id") or "")
                    rec_name = str(entry.get("reconcile_file") or "").replace(chr(92), "/").rsplit("/", 1)[-1]
                    parent_pair = "/".join(norm_receipt.rsplit("/", 2)[-2:]) if "/" in norm_receipt else ""
                    file_ok &= bool(
                        (rid and (has_token(rid) or has_token(rid[:8])))
                        or (rec_name and has_token(rec_name))
                        or (parent_pair and parent_pair.casefold() in reply.replace(chr(92), "/").casefold())
                    )
                bound &= file_ok
            if entry.get("reconcile_file"):
                rec_name = str(entry["reconcile_file"]).replace(chr(92), "/").rsplit("/", 1)[-1]
                if rec_name and not has_token(rec_name):
                    bound = False
                    missing_reconcile = True
    unquoted_reply = "\n".join(user_words.markdown_unquoted_lines(reply))
    non_neg_claims = re.sub(
        r"(?:未|没有|并未|并非|非|不(?:能|可|应|要|得|宣称)?|勿|切勿)\s*(?:全部|全数)(?:成功|完成|通过)"
        r"|(?:全部|全数)(?:成功|完成|通过)\s*[:：]\s*(?:false|否|0)\b"
        r"|\bnot\s+all\s+(?:succeeded|completed|passed)\b"
        r"|\ball_succeeded\s*[:=]\s*false\b",
        "",
        unquoted_reply,
        flags=re.I,
    )
    has_incomplete_entry = any(entry.get("all_succeeded") is False for entry in entries)
    has_failed_stream = any(
        (not entry.get("native")) and (
            any(
                row.get("succeeded") is False or str(row.get("status") or "").lower() not in ("", "ok")
                for row in (entry.get("streams") or [])
            )
            or (not entry.get("streams") and entry.get("all_succeeded") is False)
        )
        for entry in entries
    )
    claims_all_ok = bool(re.search(r"(?:全部|全数)(?:成功|完成|通过)|\ball\s+(?:succeeded|completed|passed)\b", non_neg_claims, re.I))
    contradicted = (has_incomplete_entry and claims_all_ok) or (has_failed_stream and not failure)
    if status and tokens and receipt and bound and not contradicted:
        verified_ids = [str(entry["run_id"]) for entry in entries if not entry.get("native") and entry.get("run_id")]
        user_words.mark_heavy_completions_reported(sid, state_dir, run_ids=verified_ids or None)
        state["native_reported"] = native_key
        if native:
            state["reported_native_ids"] = list(dict.fromkeys(state.get("reported_native_ids", []) + [
                row["native_id"] for row in native["streams"]]))
        user_words.atomic_write_json(state_file, state)
        return None
    if reconcile_only or state.get("blocked"):
        return None
    repeat = user_words._read_state_json(state_dir / (sid + "_repeat_guard.json")) or {}
    if repeat.get("turns", {}).get(turn_key, {}).get("blockedSignatures"):
        return None
    for hg_path in (
        state_dir / f"{sid}_handoff_gate.json",
        state_dir / "handoff" / f"{sid}_handoff_gate.json",
    ):
        handoff = user_words._read_state_json(hg_path)
        if not isinstance(handoff, dict):
            continue
        if (
            turn_key in (handoff.get("stopGateBlockedTurnKeys") or [])
            or handoff.get("stopCorrectionRequested") is True
            or (
                handoff.get("mode") == "enforce"
                and handoff.get("phase") in ("drain", "freeze")
                and handoff.get("handoffRecorded") is not True
            )
        ):
            return None
    state["blocked"] = True
    user_words.atomic_write_json(state_file, state)
    correction = "磁盘账单含未成功的任务，请如实说明失败、超时或部分完成情况，勿写成全部成功。" if contradicted else ""
    reconcile_hint = "、机械对账表查看链接" if missing_reconcile else ""
    return {"decision": decision, "reason": "【user-guard 重负载完工核验】" + correction + f"请在本轮回复补齐各路成败、Token 明细与汇总、回执或子代理日志位置{reconcile_hint}。磁盘账单如下：\n" + _render_heavy_bill(entries)}


_SUITE_RESULTS = re.compile(
    r"^[ \t]*Ran[ \t]+(?P<unittest>\d+)[ \t]+tests?\b[^\n]*\n(?:[ \t\r]*\n)*[ \t]*(?P<outcome>OK|FAILED)\b"
    r"|^[ \t=]*(?P<pytest>\d+ (?:failed|passed|errors?)(?:, \d+ (?:failed|passed|skipped|deselected|errors?|xfailed|xpassed|warnings?))*)"
    r"(?: in [^\n]+)?[ \t=\r]*$"
    r"|^[ \t]*(?:Test Summary:[ \t]*\|[ \t]*)?(?P<julia_columns>Pass(?:[ \t]+Fail)?|Fail)[ \t]+Total[^\n]*\n"
    r"(?:[^\n|]*\|)?[ \t]*(?P<julia_pass>\d+)[ \t]+(?:\d+[ \t]+)?(?P<julia_total>\d+)(?=[ \t\r]|$)", re.M)


def _turn_suite_results(path):
    steps, _ = _read_tail_steps_with_offset(path)
    start = next((i + 1 for i in range(len(steps) - 1, -1, -1)
                  if _is_explicit_user_step(steps[i]) or user_words.transcript_user_text(steps[i], path)), 0)
    for entry in steps[start:]:
        if entry.get("isSidechain") is True or entry.get("isMeta") is True:
            continue
        if entry.get("type") in ("GENERIC", "TOOL_RESULT", "TOOL_RESPONSE"):
            result = entry.get("tool_result")
            outputs = [entry.get("content") or (result.get("output") if isinstance(result, dict) else entry.get("output"))]
        else:
            outputs = [obj.get("output", obj.get("content"))
                       for obj in user_words._HarnessArchiveCollector._objects(entry, parse_text=False)
                       if obj.get("type") in ("function_call_output", "custom_tool_call_output", "local_shell_call_output", "tool_result")]
        for raw in outputs:
            if isinstance(raw, dict):
                raw = raw.get("stdout") or raw.get("output")
            output = re.sub(r"\x1b\[[0-9;]*m", "", user_words._codex_output_to_text(raw))
            for result in _SUITE_RESULTS.finditer(output):
                if result["unittest"]:
                    yield int(result["unittest"]), result["outcome"] == "OK"
                elif result["pytest"]:
                    counts = {status: int(n) for n, status in re.findall(r"(\d+) (\w+)", result["pytest"])}
                    failed = sum(counts.get(key, 0) for key in ("failed", "error", "errors"))
                    yield counts.get("passed", 0) + failed, not failed
                else:
                    yield int(result["julia_total"]), "Pass" in result["julia_columns"] and result["julia_pass"] == result["julia_total"]


def _has_terminal_block_sig(blocked_sigs: List[str]) -> bool:
    """判断本回合是否已触发过终态质量/实证阻断（忽略早期短等待 wait: 阻断签名）。"""
    return any(not str(sig).startswith("wait:") for sig in (blocked_sigs or ()))


def _check_test_suite_locked(payload, reply, state_file, state, sid, turn_key, decision):
    record = state["turns"][turn_key]
    green = r"(?:全绿|全部通过|全过|\bpassed\b)"
    reply = "\n".join(user_words.markdown_unquoted_lines(reply))
    if _has_terminal_block_sig(record["blockedSignatures"]) or not re.search(green, reply, re.I):
        return None
    raw_path = payload.get("transcriptPath") or payload.get("transcript_path")
    if not raw_path:
        return None
    passes_since_failure = {}
    for size, passed in _turn_suite_results(user_words.resolve_compact_transcript_path(Path(raw_path).expanduser())):
        if passed:
            passes_since_failure = {total: max(largest, size) for total, largest in passes_since_failure.items() if size < total}
        elif size >= 20:
            passes_since_failure[size] = 0
    for total, largest in passes_since_failure.items():
        subject = rf"(?:全套测试|全量(?:单测|测试|回归测试)|(?<![\d.]){total}(?!\d)\s*(?:项|个|条)?\s*(?:测试(?:用例)?|用例|tests?|cases?)?)"
        neg_prefix = r"(?<!未)(?<!没)(?<!非)(?<!勿)(?<!不宣称)(?<!不构成)"
        claim_re = rf"{neg_prefix}{subject}\s*[:：]?\s*(?:均已|已|均|都)?\s*{green}|\bpassed\s+{total}\s+tests?\b"
        if 2 * largest < total and re.search(claim_re, reply, re.I):
            if _heavy_report_blocked(state_file.parent, sid, turn_key, payload):
                return None
            record["blockedSignatures"].append("test-suite")
            _save_repeat_state(state_file, state, sid)
            reason = (
                f"【user-guard 测试结果核验】{total} 项测试的运行有失败。请按 R3.6 分别报告已通过的局部补测和待复跑的全量回归。"
                if largest > 0
                else f"【user-guard 测试结果核验】{total} 项测试的运行有失败，且之后尚未有全量复跑通过记录。请按 R3.6 如实报告失败项，勿宣称全套测试通过。"
            )
            return {"decision": decision, "reason": reason}
    return None


_UNVERIFIED_NEG_META_RE = re.compile(
    r"(?:不得宣称|严禁宣称|禁止宣称|不应宣称|未实查即下否定|守卫零执行|否定宣称拦截|局部排查防全称|长文交付自审|user-guard)"
)
_UNVERIFIED_NEG_HEDGE_RE = re.compile(
    r"(?:尚未核实|尚未确认|暂未确认|未核实|待核实|待确认|尚不确定|仅核查了|只核查了|仅排查了|只查了|探测失败|命令报错|命令失败)"
)
_EMPIRICAL_CLAIM_RE = re.compile(
    r"(?<!未)(?<!没)(?<!尚)(?<!暂)(?:已核实|已实查|经核查|经核实|已亲自核查|实查结果|本机实际查到|已确认(?:本机|本地|当前|环境)?|\bverified\s+locally\b|\bconfirmed\s+locally\b)",
    re.I,
)
_STRONG_NEGATIVE_CLAIM_RE = re.compile(
    r"(?:未连接|没有连接|并未连接|并没有连接|没有连到|并没有连到|未挂载|没有映射|无映射网络盘|查不到|无法访问|不存在|未安装|没有安装|未配置|不可用|没有现成的?远端连接|\bnot\s+connected\b|\bnot\s+mounted\b|\bnot\s+installed\b|\bdoes\s+not\s+exist\b|\bnot\s+available\b)",
    re.I,
)
_SWEEPING_GAP_VERDICT_RE = re.compile(
    r"(?:(?:现有|当前|仓库|底层|整体|全套)?(?:数据|算例|实验|图件|图表|证据|材料|代码|实现)(?:本身)?(?:确实|明显|严重|根本|整体)?(?:不够|不足|缺失(?:很多|大量|严重)|不支撑|撑不起|无法支撑|达不到)"
    r"|(?:必须|只能|需要)(?:重做|重跑整套|重写整套|推倒重来|补齐核心算法|新增算法创新)"
    r"|\b(?:underlying|existing|current)\s+(?:data|results|evidence|benchmarks)\s+(?:is|are)\s+(?:insufficient|lacking|missing)\b"
    r"|\binsufficient\s+(?:data|results|evidence|benchmarks)\b)",
    re.I,
)
_SCOPE_DISCLOSURE_RE = re.compile(
    r"(?:仅核查了|只核查了|仅查看了|只看了|仅检查了|只检查了|仅扫描了|单从(?:该|此|上述)|尚未核查|尚未排查|尚未盘点|未全量盘点|基于已读|仅基于|\bchecked\s+only\b|\bonly\s+inspected\b|\bhaven'?t\s+checked\b)",
    re.I,
)
_UNREVIEWED_DISCLOSURE_RE = re.compile(
    r"(?:初稿|草稿|未经(?:跨端|独立)(?:审查|审校|核验|复核)|尚未(?:跨端|独立|运行)[^\n。；]{0,12}(?:审查|审校|核验|复核|check-grounding)|待(?:跨端|独立)?(?:审查|审校|复核|验证)|check-grounding[^\n。；]{0,24}(?:检出|报告|发现|遗留|未通过|非零)|\bunreviewed\s+draft\b|\bnot\s+yet\s+reviewed\b)",
    re.I,
)
_NONZERO_EXIT_RE = re.compile(
    r"(?:Command exited with code|Process exited with code|Exit code)\s*[:：]?\s*[1-9]\d*",
    re.I,
)
_TOOL_DENIED_OR_ERR_RE = re.compile(
    r"(?:Encountered error in step execution|tool call denied|denied by pre-tool hook)",
    re.I,
)
_BACKGROUND_PENDING_RE = re.compile(
    r"(?:running\s+as\s+a\s+background\s+task|running\s+in\s+(?:the\s+)?background|background\s+task.*started|Task\s+id\s+\S+\s+started|Created\s+the\s+following\s+subagent)",
    re.I,
)
_INDEX_PATH_HINTS = (
    "map", "index", "manifest", "catalog", "scope", "spec", "rulings", "studies",
    "inventory", "readme", "agents", "claude", "outline", "toc", "overview",
    "总览", "索引", "目录", "清单", "规范", "总纲",
)
_BROAD_SEARCH_CMD_RE = re.compile(
    r"(?:\b(?:find|tree|rg|ripgrep|fd)\b|\bgrep\s+-[A-Za-z]*[rR]|\bgit\s+ls-files\b|\bGet-ChildItem\b[^\n]*-Recurse)",
    re.I,
)
_SINGLE_FILE_EXT_RE = re.compile(
    r"(?:^|[\s\"'])([^\s\"']+\.(?:md|tex|rst|txt|json|toml|yaml|yml|py|f90|f95|f03|c|cpp|h|hpp|jl|rs|go|sh|ps1|csv|log))(?:[\s\"']|$)",
    re.I,
)
_HARNESS_REVIEW_CMD_RE = re.compile(
    r"cae_harness\.py[\"']?\s+(?:audit|batch|task|gate|followup|check-grounding|cross-check|reconcile)\b",
    re.I,
)
_FORMAL_DOC_EXTS = frozenset({".tex", ".md", ".rst"})
_FORMAL_DOC_SKIP_DIRS = frozenset({
    "tests", "test", "scratch", "tmp", "temp", ".git", "plugin_data",
    "state", "logs", "handoff", "archive", "archived", ".system_generated",
    ".agents", ".agent", "_agents", "_agent", "plugins", "skills", "sidecars",
})
_FORMAL_DOC_SKIP_NAMES = frozenset({
    "changelog.md", "license.md", "notice.md", "todo.md", "notes.md",
    "plan.md", "implementation_plan.md", "walkthrough.md", "skill.md",
})
_FORMAL_DOC_SKIP_SUFFIXES = (
    "_card.md", "_reconcile.md", "_read_plan.md", "_cross_check.md", "_user_words.md", "_assistant_replies.md",
)
_LARGE_DOC_REWRITE_MIN_CHARS = 4000


def _strip_inline_code_spans(text: str) -> str:
    return re.sub(r"`[^`\r\n]*`", " ", text)


def _has_empirical_negative_claim(text: str, *, preprocessed: bool = False) -> bool:
    plain = text if preprocessed else _strip_inline_code_spans("\n".join(user_words.markdown_unquoted_lines(text)))
    for clause in re.split(r"[。！？；\n]+", plain):
        seg = clause.strip()
        if not seg or _UNVERIFIED_NEG_META_RE.search(seg) or _UNVERIFIED_NEG_HEDGE_RE.search(seg):
            continue
        if _EMPIRICAL_CLAIM_RE.search(seg) and _STRONG_NEGATIVE_CLAIM_RE.search(seg):
            return True
    return False


def _has_sweeping_gap_verdict(plain: str) -> bool:
    for sentence in re.split(r"[。！？\n]+", plain):
        seg = sentence.strip()
        if (not seg or _UNVERIFIED_NEG_META_RE.search(seg)
                or _UNVERIFIED_NEG_HEDGE_RE.search(seg) or _SCOPE_DISCLOSURE_RE.search(seg)):
            continue
        if _SWEEPING_GAP_VERDICT_RE.search(seg):
            return True
    return False


def _is_trivial_ui_ok_output(text: str) -> bool:
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith(("Created At:", "Completed At:"))
    ]
    return len(lines) == 1 and lines[0] == "OK"


def _is_formal_deliverable_doc(raw_path: str) -> Tuple[bool, str]:
    norm = user_words._unquote_proto_scalar(raw_path).replace("\\", "/").strip()
    if not norm:
        return False, ""
    p = Path(norm)
    ext = p.suffix.lower()
    if ext not in _FORMAL_DOC_EXTS:
        return False, norm
    low_name = p.name.lower()
    if (low_name in _FORMAL_DOC_SKIP_NAMES
            or low_name.endswith(_FORMAL_DOC_SKIP_SUFFIXES)
            or any(tag in low_name for tag in ("handoff", "prompt", "task_"))):
        return False, norm
    parent_parts = [part.lower() for part in p.parts[:-1]]
    if any(part in _FORMAL_DOC_SKIP_DIRS for part in parent_parts):
        return False, norm
    if any(
        parent_parts[i] == "brain" and i > 0 and parent_parts[i - 1] == "antigravity"
        for i in range(len(parent_parts))
    ):
        return False, norm
    return True, norm


def _norm_doc_cmp_path(raw: str) -> str:
    s = str(raw or "").replace("\\", "/").strip().lower()
    while s.startswith("./"):
        s = s[2:]
    return s


def _doc_paths_match(doc_path: str, cand_path: str) -> bool:
    a = _norm_doc_cmp_path(doc_path)
    b = _norm_doc_cmp_path(cand_path)
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _grounding_result_verifies_docs(text: str, is_err: bool, written_docs: List[Dict[str, Any]]) -> bool:
    """核验 check-grounding 输出是否完整覆盖本轮写入的全部正式文档，且未启用 --hygiene-only 逃逸事实锚定。"""
    if _TOOL_DENIED_OR_ERR_RE.search(text):
        return False
    parsed_rep: Optional[Dict[str, Any]] = None
    if '"checked_files"' in text or '"checked_file_count"' in text or '"findings"' in text:
        j_start = text.find("{")
        j_end = text.rfind("}")
        if j_start != -1 and j_end > j_start:
            try:
                obj = json.loads(text[j_start : j_end + 1])
                if isinstance(obj, dict):
                    parsed_rep = obj
            except ValueError:
                parsed_rep = None
    if parsed_rep is not None:
        if parsed_rep.get("hygiene_only") is True:
            return False
        checked_files = parsed_rep.get("checked_files")
        if isinstance(checked_files, list) and written_docs:
            checked_strs = [str(x) for x in checked_files if isinstance(x, str)]
            if not all(any(_doc_paths_match(d["path"], cf) for cf in checked_strs) for d in written_docs):
                return False
        findings = parsed_rep.get("findings")
        if isinstance(findings, list):
            finding_files = [
                str(f.get("file") or "")
                for f in findings
                if isinstance(f, dict) and f.get("file")
            ]
            if written_docs and any(
                _doc_paths_match(d["path"], ff) for d in written_docs for ff in finding_files
            ):
                return False
            if not written_docs and is_err and finding_files:
                return False
            return True
    return not is_err


def _is_index_path(path_str: str) -> bool:
    low = path_str.replace("\\", "/").lower()
    return any(hint in low for hint in _INDEX_PATH_HINTS)


def _is_broad_shell_search(cmd_str: str) -> bool:
    if not _BROAD_SEARCH_CMD_RE.search(cmd_str):
        return False
    # 针对单文件的 rg/grep 命令不视为全仓检索
    file_args = _SINGLE_FILE_EXT_RE.findall(cmd_str)
    if file_args and not re.search(r"\b(?:git\s+ls-files|tree|find)\b|-[A-Za-z]*[rR]|-Recurse|\*", cmd_str, re.I):
        return False
    return True


def _extract_entry_model(entry: Dict[str, Any]) -> str:
    if isinstance(entry.get("model"), str) and entry["model"].strip():
        return entry["model"].strip()
    payload = entry.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("model"), str) and payload["model"].strip():
        return payload["model"].strip()
    msg = entry.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("model"), str) and msg["model"].strip():
        return msg["model"].strip()
    return ""


def _iter_entry_tool_calls(entry: Dict[str, Any]):
    """统一抽取 Antigravity / Codex / Claude 单条轨迹记录中的工具调用 (call_id, tool_name, parsed_args, raw_text)。"""
    for obj in user_words._HarnessArchiveCollector._objects(entry, parse_text=False):
        kind = obj.get("type")
        is_call = kind in ("function_call", "custom_tool_call", "local_shell_call", "tool_use") or (
            obj.get("name") and any(key in obj for key in ("args", "arguments", "input"))
        )
        if not is_call:
            continue
        call_id = user_words._tool_call_id(obj, is_call=True)
        name = str(obj.get("name") or ("shell" if kind == "local_shell_call" else "")).strip()
        raw_args = obj.get("arguments", obj.get("args", obj.get("input", {})))
        if kind == "local_shell_call" and isinstance(obj.get("action"), dict):
            raw_args = obj["action"]
        parsed = user_words._parse_codex_json_maybe(raw_args)
        parsed_dict = parsed if isinstance(parsed, dict) else {}
        raw_text = raw_args if isinstance(raw_args, str) else json.dumps(raw_args, ensure_ascii=False)
        yield call_id, name, parsed_dict, raw_text


def _inspect_turn_evidence(path: Path) -> Optional[Dict[str, Any]]:
    """单次扫描跨三端轨迹窗口，绑定工具调用与终态结果，汇总本回合探测、范围、长文整写与审查证据。"""
    if not path.is_file():
        return None
    steps, start_offset = _read_tail_steps_with_offset(path)
    user_boundary = next(
        (
            i
            for i in range(len(steps) - 1, -1, -1)
            if _is_explicit_user_step(steps[i]) or user_words.transcript_user_text(steps[i], path)
        ),
        None,
    )
    start = (user_boundary + 1) if user_boundary is not None else 0
    turn_truncated = bool(start_offset > 0 and user_boundary is None)

    active_model = ""
    total = 0
    succeeded = 0
    failed = 0
    checked_paths: Set[str] = set()
    consulted_index = False
    large_doc_writes: List[Dict[str, Any]] = []
    seen_writes: Set[str] = set()
    result_seq = 0
    latest_write_seq = -1
    verified_review_seq = -1

    pending_by_id: Dict[str, Dict[str, Any]] = {}
    pending_unid: List[Dict[str, Any]] = []
    pending_review_agents: Set[str] = set()
    pending_review_tasks: Dict[str, bool] = {}

    def _register_pending_review(text: str, is_grounding: bool) -> None:
        parsed_sub = user_words.parse_agy_created_subagent(text)
        sub_cid = ""
        if isinstance(parsed_sub, dict):
            sub_cid = str(
                parsed_sub.get("conversationId")
                or parsed_sub.get("conversation_id")
                or parsed_sub.get("id")
                or ""
            ).strip()
        elif isinstance(parsed_sub, str):
            sub_cid = parsed_sub.strip()
        if sub_cid:
            pending_review_agents.add(sub_cid)
        for m_tid in re.finditer(
            r"\b(?:Task\s+id|task_id|Command\s+id)\s*[:：]?\s*([A-Za-z0-9_/-]+)|\b(task-\d+)\b",
            text,
            re.I,
        ):
            tid = (m_tid.group(1) or m_tid.group(2) or "").strip()
            if tid:
                pending_review_tasks[tid] = is_grounding
                if "/" in tid:
                    pending_review_tasks[tid.rsplit("/", 1)[-1]] = is_grounding

    def _settle_result(call_id: str, text: str, is_err: bool) -> None:
        nonlocal total, succeeded, failed, consulted_index, result_seq, latest_write_seq, verified_review_seq
        result_seq += 1
        meta = pending_by_id.pop(call_id, None) if call_id else None
        if meta is None and not call_id and pending_unid:
            meta = pending_unid.pop(0)

        if meta is not None:
            kind = meta["kind"]
            if kind == "ui":
                return
            if is_err:
                if kind == "probe":
                    total += 1
                    failed += 1
                elif kind == "review" and meta.get("is_grounding"):
                    if _grounding_result_verifies_docs(text, True, large_doc_writes):
                        verified_review_seq = result_seq
                return
            if kind == "write_doc":
                for doc_info in meta.get("doc_infos") or ():
                    if doc_info and doc_info["path"] not in seen_writes:
                        seen_writes.add(doc_info["path"])
                        large_doc_writes.append(doc_info)
                latest_write_seq = result_seq
                return
            if kind == "review":
                if _BACKGROUND_PENDING_RE.search(text):
                    _register_pending_review(text, bool(meta.get("is_grounding")))
                elif meta.get("is_grounding"):
                    if _grounding_result_verifies_docs(text, False, large_doc_writes):
                        verified_review_seq = result_seq
                else:
                    verified_review_seq = result_seq
                return
            if kind == "probe":
                total += 1
                succeeded += 1
                for p in meta["paths"]:
                    checked_paths.add(p)
                    if _is_index_path(p):
                        consulted_index = True
                if meta["broad_search"]:
                    consulted_index = True
                return
            # kind == "other"（非探测性成功写入/编辑等）不充当 Gate A 只读探测成功证据
            return

        # 无对应调用记录的纯输出条目（兼容精简轨迹或单测夹具）
        total += 1
        if is_err:
            failed += 1
        else:
            succeeded += 1

    for idx, entry in enumerate(steps):
        if entry.get("isSidechain") is True or entry.get("isMeta") is True:
            continue
        m_name = _extract_entry_model(entry)
        if m_name:
            active_model = m_name
        if idx < start:
            continue

        for call_id, tool_name, parsed, raw_text in _iter_entry_tool_calls(entry):
            tlow = tool_name.lower()
            raw_cmd = parsed.get("CommandLine") or parsed.get("command") or parsed.get("cmd") or ""
            cmd_str = (
                " ".join(str(x) for x in raw_cmd)
                if isinstance(raw_cmd, list)
                else user_words._unquote_proto_scalar(raw_cmd)
            )
            meta: Dict[str, Any] = {"kind": "other", "paths": [], "broad_search": False, "doc_infos": [], "is_grounding": False}
            if tlow in ("update_focus", "update_steps"):
                meta["kind"] = "ui"
            elif _HARNESS_REVIEW_CMD_RE.search(cmd_str or raw_text):
                meta["kind"] = "review"
                meta["is_grounding"] = bool(re.search(r"\bcheck-grounding\b", cmd_str or raw_text, re.I))
            elif tlow in ("invoke_subagent", "spawn_agent", "agent", "task", "subagent", "dispatch_agent", "run_workflow", "swarm"):
                if re.search(r"(?:audit|review|collaborator|verifier|readonly-auditor|readonly-collaborator)", raw_text, re.I):
                    meta["kind"] = "review"
            elif tlow in ("read_file", "read", "view_file", "list_dir", "ls"):
                meta["kind"] = "probe"
                p_val = user_words._unquote_proto_scalar(
                    parsed.get("Path") or parsed.get("file_path") or parsed.get("path") or parsed.get("DirectoryPath") or ""
                )
                if p_val:
                    meta["paths"].append(p_val.replace("\\", "/").lower())
            elif tlow in ("grep_search", "grep", "find_by_name", "glob"):
                meta["kind"] = "probe"
                p_val = user_words._unquote_proto_scalar(
                    parsed.get("SearchPath") or parsed.get("SearchDirectory") or parsed.get("path") or ""
                )
                if p_val:
                    meta["paths"].append(p_val.replace("\\", "/").lower())
                # 仅当目标不是单个明确扩展名文件时才视为目录/全仓级检索
                meta["broad_search"] = not (p_val and Path(p_val).suffix and "*" not in p_val)
            elif tlow in ("run_command", "bash", "exec_command", "shell", "local_shell_call"):
                meta["kind"] = "probe"
                meta["broad_search"] = _is_broad_shell_search(cmd_str)
                for tok in re.findall(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+(?:\.[A-Za-z0-9]+)?|\b[A-Za-z0-9_.-]+\.(?:md|tex|rst|json|toml|yaml|yml|py|f90|cpp|c|h)\b", cmd_str):
                    meta["paths"].append(tok.replace("\\", "/").lower())
            elif tlow in ("write_file", "write_to_file", "write", "create_file"):
                raw_target = (
                    parsed.get("Path") or parsed.get("file_path") or parsed.get("TargetFile") or parsed.get("path") or ""
                )
                is_append = str(parsed.get("Append") or parsed.get("append") or "").strip().lower() == "true"
                ok_doc, norm_doc = _is_formal_deliverable_doc(str(raw_target))
                if ok_doc and not is_append:
                    content = user_words._unquote_proto_scalar(
                        parsed.get("Content") or parsed.get("content") or parsed.get("CodeContent") or ""
                    )
                    size_chars = len(content)
                    if size_chars < _LARGE_DOC_REWRITE_MIN_CHARS:
                        try:
                            disk_p = Path(norm_doc)
                            if disk_p.is_file():
                                size_chars = max(size_chars, disk_p.stat().st_size)
                        except OSError:
                            pass
                    if size_chars >= _LARGE_DOC_REWRITE_MIN_CHARS:
                        meta["kind"] = "write_doc"
                        meta["doc_infos"] = [{"path": norm_doc, "chars": size_chars}]
            elif tlow == "apply_patch":
                patch_text = str(parsed.get("input") or parsed.get("patch") or raw_text or "")
                cur_doc = ""
                cur_added = 0
                doc_infos: List[Dict[str, Any]] = []
                for pline in patch_text.splitlines():
                    pm = re.match(r"^\*\*\*\s+(?:Add|Update)\s+File:\s+(.+)$", pline.strip())
                    if pm:
                        if cur_doc and cur_added >= _LARGE_DOC_REWRITE_MIN_CHARS:
                            doc_infos.append({"path": cur_doc, "chars": cur_added})
                        ok_doc, norm_doc = _is_formal_deliverable_doc(pm.group(1))
                        cur_doc = norm_doc if ok_doc else ""
                        cur_added = 0
                    elif cur_doc and pline.startswith("+") and not pline.startswith("+++"):
                        cur_added += len(pline)
                if cur_doc and cur_added >= _LARGE_DOC_REWRITE_MIN_CHARS:
                    doc_infos.append({"path": cur_doc, "chars": cur_added})
                if doc_infos:
                    meta["kind"] = "write_doc"
                    meta["doc_infos"] = doc_infos

            if call_id:
                pending_by_id[call_id] = meta
            else:
                pending_unid.append(meta)

        if entry.get("type") == "SYSTEM_MESSAGE":
            sys_txt = str(entry.get("content") or "")
            sender = str(entry.get("sender") or entry.get("from") or "").strip()
            matched_agent = next(
                (aid for aid in pending_review_agents if aid == sender or aid in sys_txt),
                None,
            )
            if matched_agent is not None:
                if (
                    not _NONZERO_EXIT_RE.search(sys_txt)
                    and not _TOOL_DENIED_OR_ERR_RE.search(sys_txt)
                    and (
                        bool(user_words._extract_agy_subagent_return_body(sys_txt)[1])
                        or bool(re.search(r"finished|completed|完成", sys_txt, re.I))
                    )
                ):
                    result_seq += 1
                    verified_review_seq = result_seq
                pending_review_agents.discard(matched_agent)
                continue
            matched_task = next(
                (
                    tid
                    for tid in pending_review_tasks
                    if tid == sender
                    or re.search(rf"(?<![A-Za-z0-9_-]){re.escape(tid)}(?![A-Za-z0-9_-])", sys_txt)
                ),
                None,
            )
            if matched_task is not None:
                is_g_task = pending_review_tasks.pop(matched_task)
                if is_g_task:
                    if _grounding_result_verifies_docs(sys_txt, bool(_NONZERO_EXIT_RE.search(sys_txt)), large_doc_writes):
                        result_seq += 1
                        verified_review_seq = result_seq
                elif (
                    re.search(r"finished|completed|exited\s+with\s+code\s+0|完成", sys_txt, re.I)
                    and not _NONZERO_EXIT_RE.search(sys_txt)
                    and not _TOOL_DENIED_OR_ERR_RE.search(sys_txt)
                    and not re.search(r"\b(?:failed|timed\s*out|killed)\b", sys_txt, re.I)
                ):
                    result_seq += 1
                    verified_review_seq = result_seq
            continue

        if entry.get("type") in ("GENERIC", "TOOL_RESULT", "TOOL_RESPONSE"):
            result = entry.get("tool_result")
            raw = entry.get("content")
            if raw is None:
                raw = result.get("output") if isinstance(result, dict) else entry.get("output")
            text = re.sub(r"\x1b\[[0-9;]*m", "", user_words._codex_output_to_text(raw))
            status_err = str(entry.get("status") or "").strip().upper() == "ERROR"
            res_cid = user_words._tool_call_id(entry) or (
                user_words._tool_call_id(result) if isinstance(result, dict) else ""
            )
            if not status_err and (not text.strip() or _is_trivial_ui_ok_output(text)):
                # 同步弹出对应的 ui/空回执调用，避免队列错位
                if res_cid:
                    pending_by_id.pop(res_cid, None)
                elif pending_unid:
                    pending_unid.pop(0)
                continue
            is_err = bool(status_err or _NONZERO_EXIT_RE.search(text) or _TOOL_DENIED_OR_ERR_RE.search(text))
            _settle_result(res_cid, text, is_err)
        else:
            for obj in user_words._HarnessArchiveCollector._objects(entry, parse_text=False):
                if obj.get("type") not in ("function_call_output", "custom_tool_call_output", "local_shell_call_output", "tool_result"):
                    continue
                res_cid = user_words._tool_call_id(obj) or str(obj.get("tool_use_id") or "")
                raw = obj.get("output", obj.get("content"))
                is_err = obj.get("is_error") is True
                if isinstance(raw, dict):
                    meta_obj = raw.get("metadata")
                    ec_raw = meta_obj.get("exit_code") if isinstance(meta_obj, dict) else raw.get("exit_code")
                    if isinstance(ec_raw, int) and ec_raw != 0:
                        is_err = True
                    raw_text = raw.get("stdout") or raw.get("output") or json.dumps(raw, ensure_ascii=False)
                else:
                    raw_text = user_words._codex_output_to_text(raw)
                text = re.sub(r"\x1b\[[0-9;]*m", "", raw_text)
                if not is_err and text.strip().startswith("{"):
                    try:
                        parsed_out = json.loads(text)
                        if isinstance(parsed_out, dict):
                            meta_p = parsed_out.get("metadata")
                            ec = meta_p.get("exit_code") if isinstance(meta_p, dict) else parsed_out.get("exit_code")
                            if isinstance(ec, int) and ec != 0:
                                is_err = True
                    except ValueError:
                        pass
                if not is_err and (not text.strip() or _is_trivial_ui_ok_output(text)):
                    if res_cid:
                        pending_by_id.pop(res_cid, None)
                    elif pending_unid:
                        pending_unid.pop(0)
                    continue
                is_err = bool(is_err or _NONZERO_EXIT_RE.search(text) or _TOOL_DENIED_OR_ERR_RE.search(text))
                _settle_result(res_cid, text, is_err)

    return {
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "turn_truncated": turn_truncated,
        "checked_paths": checked_paths,
        "consulted_index": consulted_index,
        "large_doc_writes": large_doc_writes,
        "has_independent_review": bool(latest_write_seq >= 0 and verified_review_seq > latest_write_seq),
        "active_model": active_model,
    }


def _code_family_mismatch_hint(client: str, active_model: str, payload: Dict[str, Any]) -> str:
    """按 fleet_profile.json 的 code_families / deep_families 判断当前主会话是否以编码高吞吐系列承担长文定稿。"""
    model = active_model or _adaptive_policy.extract_runtime_model_and_effort(client, payload)[0]
    if not model:
        return ""
    profile = user_words.load_fleet_profile(HOOKS_DIR)
    rules = profile.get("client_catalog_rules") if isinstance(profile.get("client_catalog_rules"), dict) else {}
    agent_key = "agy" if client == "antigravity" else client
    c_rule = rules.get(agent_key) if isinstance(rules.get(agent_key), dict) else {}
    code_fams = [str(x).strip().lower() for x in (c_rule.get("code_families") or []) if str(x).strip()]
    deep_fams = [str(x).strip().lower() for x in (c_rule.get("deep_families") or []) if str(x).strip()]
    if not code_fams or not deep_fams:
        return ""
    m_low = model.lower()
    in_code = any(re.search(rf"(?<![a-z0-9]){re.escape(fam)}(?![a-z0-9])", m_low) for fam in code_fams)
    in_deep = any(re.search(rf"(?<![a-z0-9]){re.escape(fam)}(?![a-z0-9])", m_low) for fam in deep_fams)
    if in_code and not in_deep:
        return (
            f"另检测到当前主会话运行在编码高吞吐系列（`{model}`），"
            f"正式长文、证明与技术规格定稿建议切用审慎推理系列（如 {'/'.join(deep_fams)}）或交由跨端独立审查把关。"
        )
    return ""


def _check_turn_evidence_locked(
    payload: Dict[str, Any],
    reply: str,
    state_file: Path,
    state: Dict[str, Any],
    sid: str,
    turn_key: str,
    decision: str,
    *,
    client: str = "antigravity",
    defer_deliverable_review: bool = False,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """统一执行三端回合实证核验（单次扫描，每回合至多拦截一次）：零探测否定、局部排查全称缺口、长文单端免审。"""
    record = state["turns"][turn_key]
    if _has_terminal_block_sig(record["blockedSignatures"]):
        return None
    raw_path = payload.get("transcriptPath") or payload.get("transcript_path")
    if not raw_path:
        return None
    unquoted = "\n".join(user_words.markdown_unquoted_lines(reply))
    # 仅豁免【新对话提示词】正文段落，保留其之前的事实汇报继续受检
    body_for_check = unquoted.split("【新对话提示词】", 1)[0].strip() if "【新对话提示词】" in unquoted else unquoted
    if not body_for_check:
        return None
    plain = _strip_inline_code_spans(body_for_check)
    has_emp_neg = _has_empirical_negative_claim(plain, preprocessed=True)
    has_gap_verdict = _has_sweeping_gap_verdict(plain)
    ev = _inspect_turn_evidence(user_words.resolve_compact_transcript_path(Path(raw_path).expanduser()))
    if ev is None:
        return None

    # Gate A/B/C 属于基于回复措辞的启发式实证检查（D3 决策：默认 advisory 记录日志不硬阻断，enforce 模式才阻断）
    ev_mode = str(EVIDENCE_GATE_MODE or "advisory").strip().lower()
    if ev_mode == "off":
        return None

    def _emit_evidence_hit(ev_kind: str, sig: str, reason: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        if ev_mode != "enforce":
            advisories = record.setdefault("advisories", [])
            if sig not in advisories:
                advisories.append(sig)
                _save_repeat_state(state_file, state, sid)
            log_event(f"Stop:advisory:{ev_kind}", sid, reason)
            return None
        if _heavy_report_blocked(state_file.parent, sid, turn_key, payload):
            return None
        record["blockedSignatures"].append(sig)
        _save_repeat_state(state_file, state, sid)
        return ev_kind, {"decision": decision, "reason": reason}

    # Gate A: 零成功探测或全报错探测却宣称“已核实未连接/不存在/不可用”（尾窗截断时不误拦）
    if has_emp_neg and ev["succeeded"] == 0 and not ev["turn_truncated"]:
        if ev["failed"] > 0:
            reason = (
                f"【user-guard 未实查即下否定结论拦截】本轮探测命令均报错或被拦截（成功 0 条、失败 {ev['failed']} 条），"
                "尚无有效只读成功输出，不得宣称“已核实未连接/不存在/不可用”。"
                "请按 R3.6 先修复命令完成只读查证，或如实说明探测失败与尚未核实的范围。"
            )
        else:
            reason = (
                "【user-guard 未实查即下否定结论拦截】本轮尚未执行任何实质只读或命令核查（成功 0 条），"
                "不得直接宣称“已核实未连接/不存在/不可用”。"
                "请按 R3.6/R4 先对本机已连渠道执行只读核实，或如实标明尚未核查。"
            )
        return _emit_evidence_hit("unverified_negative_claim", "unverified-negative-claim", reason)

    # Gate B: 仅核查少量局部路径（未读索引/清单且未做全仓检索）却下“现有数据/材料不足或需整体重做”的全称缺口定论
    if (has_gap_verdict and ev["succeeded"] > 0 and not ev["turn_truncated"]
            and not ev["consulted_index"] and len(ev["checked_paths"]) <= 3):
        reason = (
            "【user-guard 局部排查防全称缺口结论】本轮仅核查了少量局部路径（未读取项目总览/资产索引，也未执行全仓检索），"
            "却直接给出了“现有数据/材料/实现不足或需整体重做”的全局定论。"
            "请按 R3.6 先补查项目索引、资产目录或全仓检索核实存量，或如实标明“仅核查了哪些路径、尚未核查其余目录”。"
        )
        return _emit_evidence_hit("partial_scope_verdict", "partial-scope-verdict", reason)

    # Gate C: 单端整篇写入正式长文（.tex / .md / .rst）却无覆盖当前交付的独立复核记录且未标明初稿待审
    if (not defer_deliverable_review and ev["large_doc_writes"] and not ev["has_independent_review"]
            and not _UNREVIEWED_DISCLOSURE_RE.search(plain)):
        doc_names = "、".join(Path(item["path"]).name for item in ev["large_doc_writes"][:3])
        model_hint = _code_family_mismatch_hint(client, ev["active_model"], payload)
        reason = (
            f"【user-guard 长文交付自审提醒】本轮单端整篇写入了正式文档（{doc_names}），"
            "尚未见覆盖当前版本的独立复核或 `check-grounding --doc-hygiene` 核验记录，且回复未标明“未经独立审校的初稿”。"
            "请先运行 `check-grounding --strict --doc-hygiene` 或按 R3.1 发起跨端独立审查；"
            "若确为阶段初稿，请如实标明“初稿待独立审校”及未核验范围。"
            + (f" {model_hint}" if model_hint else "")
        )
        return _emit_evidence_hit("solo_deliverable_review", "solo-deliverable-review", reason)

    return None


_PENDING_TICKET_STATUS_RE = re.compile(
    r"票据(?:[：:]|\s+)\s*`*([0-9a-fA-F_-]{8,64})`*[^\n]*?状态[：:]\s*`*PENDING`*\b",
    re.I,
)


def _extract_full_pending_ticket_id(reply: str) -> Optional[str]:
    """仅当回复包含完整多行待批确认块或总览表（且非交接提示词）时提取票据标识；单行简短待办提醒不命中。"""
    unquoted = "\n".join(user_words.markdown_unquoted_lines(reply))
    if not unquoted or "【新对话提示词】" in unquoted:
        return None
    m = _PENDING_TICKET_STATUS_RE.search(unquoted)
    if not m:
        return None
    has_full_card = bool(
        re.search(r"^\s*\|\s*流号\s*\|", unquoted, re.M)
        or "【待确认派工" in unquoted
        or ("工作流背景" in unquoted and "选项" in unquoted)
    )
    return m.group(1).lower() if has_full_card else None


def _check_repeated_pending_ticket_locked(
    reply: str,
    state_file: Path,
    state: Dict[str, Any],
    record: Dict[str, Any],
    checkpoint_step: int,
    state_dir: Path,
    sid: str,
    turn_key: str,
    payload: Dict[str, Any],
    step_idx: Optional[int],
    decision: str,
    message_id: str = "",
) -> Optional[Dict[str, Any]]:
    tid = _extract_full_pending_ticket_id(reply)
    if not tid:
        return None
    shown = record.setdefault("shownPendingTickets", {})
    if shown.get(tid) != checkpoint_step:
        shown[tid] = checkpoint_step
        return None
    blocked_sigs = record.setdefault("blockedSignatures", [])
    if blocked_sigs or _heavy_report_blocked(state_dir, sid, turn_key, payload):
        return None
    blocked_sigs.append(f"pending_ticket:{tid}:{checkpoint_step}")
    record["lastStepIndex"] = step_idx
    if message_id:
        record["lastMessageId"] = message_id
    _save_repeat_state(state_file, state, sid)
    short_tid = tid[:8]
    return {
        "decision": decision,
        "reason": (
            f"【user-guard 待批票据防刷屏】票据 `{tid}` 的确认信息在本回合已展示过，且期间未发生新的上下文压缩。"
            f"后台任务被动唤醒汇报进展时，请删去重复的多行确认块或总览表（若需附带提醒，至多保留单行说明“票据 `{short_tid}` 仍候回复『批准』启动，不影响当前后台任务”）。"
        ),
    }


def _handle_stop_locked(payload: Dict[str, Any], state_dir: Path, conversation_id: str) -> Dict[str, Any]:
    turn_key, step_idx, latest_reply = extract_current_turn_latest_reply(payload.get("transcriptPath"))

    # 若当前会话处于长会话 Stage-2 (drain/freeze)，优先验收交接完整性与 /goal 停止标记（每回合至多阻断 1 次）
    last_input = user_words.clean_user_content(payload.get("lastUserInput"))
    goal_active = user_words.agy_goal_command(last_input)
    compaction_cp = -1
    compaction_file = state_dir / f"{conversation_id}_compaction.json"
    try:
        compaction_state = json.loads(compaction_file.read_text(encoding="utf-8-sig"))
        if isinstance(compaction_state, dict) and compaction_state.get("conversationId") == conversation_id:
            if isinstance(compaction_state.get("latestCheckpointStep"), int):
                compaction_cp = compaction_state["latestCheckpointStep"]
            if compaction_state.get("hasExplicitUserInput") is True:
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

    # 后台任务或子代理尚未全部空闲（fullyIdle=False）时，仅推迟 Gate C 长文交付自审提醒，
    # 虚假测试“全过”宣称、零探测否定结论与局部全称缺口定论依然即时拦截。
    if correction := _check_test_suite_locked(payload, latest_reply, state_file, state, conversation_id, turn_key, "continue"):
        return correction
    if ev_hit := _check_turn_evidence_locked(
        payload, latest_reply, state_file, state, conversation_id, turn_key, "continue",
        client="antigravity", defer_deliverable_review=(payload.get("fullyIdle") is False),
    ):
        ev_kind, ev_resp = ev_hit
        log_event("Stop:reply_repeat_guard", conversation_id, f"BLOCKED {ev_kind} in {turn_key}")
        return ev_resp

    # 同一 step_index 幂等保护（防止重复投递误累加）
    last_step = turn_rec.get("lastStepIndex")
    if step_idx is not None and isinstance(last_step, int) and step_idx <= last_step:
        return {"decision": "allow"}

    if ticket_block := _check_repeated_pending_ticket_locked(
        latest_reply, state_file, state, turn_rec, compaction_cp,
        state_dir, conversation_id, turn_key, payload, step_idx, "continue",
    ):
        log_event("Stop:reply_repeat_guard", conversation_id, f"BLOCKED repeated pending ticket card in {turn_key}")
        return ticket_block

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
        blocked_sigs.append(f"wait:{sig}")
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
    if os.environ.get("CAE_HARNESS_DEPTH", "").strip() not in ("", "0"):
        return {"decision": "allow"}
    _sync_repeat_tunables()
    conversation_id = str(payload.get("conversationId") or "unknown_session").strip()
    termination_reason = str(payload.get("terminationReason") or "").strip().casefold()

    # 1. 非正常无工具结束时直接放行
    if (termination_reason not in ("model_stop", "no_tool_call") or payload.get("error")
            or not user_words.valid_session_id(conversation_id)):
        return {"decision": "allow"}

    # 2. 官方防死循环协议规范：续跑态绝不二次阻断，但若续跑已补齐完工账单则先销账再放行，避免跨回合残留死循环
    if payload.get("continuationActive") is True or payload.get("continuation_active") is True or payload.get("stop_hook_active") is True:
        state_dir, _ = get_data_dirs()
        try:
            with user_words.session_state_lock(state_dir, conversation_id):
                turn_key, _, latest_reply = extract_current_turn_latest_reply(payload.get("transcriptPath"))
                _check_heavy_report_locked(payload, state_dir, conversation_id, turn_key, latest_reply, "continue", reconcile_only=True)
        except (OSError, ValueError):
            pass
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
    supplied_text = supplied.strip() if isinstance(supplied, str) else ""
    latest_reply = supplied_text
    _, transcript, _, skip = user_words.claude_hook_session(payload)
    if skip or transcript is None:
        return "turn_0_initial", None, latest_reply, ""
    step: Optional[int] = None
    message_id = ""
    turn_key = "turn_0_initial"
    pending_question: Optional[Tuple[int, Dict[str, Any], Set[str]]] = None
    assistant_checked = False
    saw_newer_tool_activity = False
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
            if not assistant_checked and entry.get("type") == "user":
                if user_words._claude_tool_result_ids(entry) or isinstance(entry.get("toolUseResult"), dict):
                    saw_newer_tool_activity = True
                continue
            if entry.get("type") != "assistant" or assistant_checked:
                continue
            msg = entry.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            has_tool_use = False
            if isinstance(content, list):
                has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
                text = "\n".join(block["text"] for block in content if isinstance(block, dict)
                                 and block.get("type") == "text" and isinstance(block.get("text"), str))
            else:
                text = content if isinstance(content, str) else ""
            flushed_text = text.strip()
            if not flushed_text:
                if has_tool_use:
                    saw_newer_tool_activity = True
                continue
            assistant_checked = True
            if saw_newer_tool_activity or has_tool_use or (supplied_text and supplied_text != flushed_text):
                # 落盘助手文本之后已有更新的工具活动，或显式传入的新回复尚未刷盘：不复用旧落盘游标/message_id。
                step = None
                message_id = ""
            else:
                step = offset
                message_id = str(entry.get("uuid") or entry.get("id") or "")
                if not latest_reply:
                    latest_reply = flushed_text
        if step is None and latest_reply:
            # 宿主可能在助手记录刷盘前调用 Stop；以负偏移区分未刷盘 EOF 快照与已落盘记录偏移，防游标碰撞。
            step = -(transcript.stat().st_size + 1)
            message_id = ""
    except OSError:
        return turn_key, None, latest_reply, ""
    return turn_key, step, latest_reply, message_id


def _codex_turn_reply(payload):
    import status_guard

    turn, _ = status_guard._native_turn_snapshot(payload)
    raw_supplied = payload.get("last_assistant_message")
    supplied_raw = str(raw_supplied) if isinstance(raw_supplied, str) else ""
    supplied = supplied_raw.strip()
    reply = supplied_raw if supplied else ""
    path = status_guard._dispatch_transcript(payload)
    step = None
    if path:
        try:
            offset = 0
            flushed_step = None
            flushed_reply = ""
            saw_tool_after_assistant = False
            with path.open("rb") as source:
                for line in source:
                    row_offset = offset
                    offset += len(line)
                    try:
                        entry = json.loads(user_words.decode_jsonl_line(line))
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(entry, dict):
                        continue
                    typ = entry.get("type")
                    rec = entry.get("payload") if typ == "response_item" else None
                    if _is_explicit_user_step(entry) or user_words.transcript_user_text(entry, path):
                        flushed_step = None
                        flushed_reply = ""
                        saw_tool_after_assistant = False
                        continue
                    if isinstance(rec, dict) and rec.get("type") == "message" and rec.get("role") == "assistant":
                        text = "\n".join(b.get("text", "") for b in rec.get("content", []) if isinstance(b, dict) and isinstance(b.get("text"), str))
                        if text.strip():
                            flushed_step = row_offset
                            flushed_reply = text
                            saw_tool_after_assistant = False
                        continue
                    if flushed_step is not None and (
                        (isinstance(rec, dict) and rec.get("type") in (
                            "function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output",
                            "local_shell_call", "local_shell_call_output", "web_search_call", "web_search_call_output",
                        ))
                        or typ in ("TOOL_CALL", "TOOL_RESULT", "TOOL_RESPONSE", "GENERIC")
                    ):
                        saw_tool_after_assistant = True
            if flushed_step is not None:
                if saw_tool_after_assistant:
                    if supplied:
                        step = -(offset + 1)
                    else:
                        step = None
                        reply = ""
                elif supplied and supplied != flushed_reply.strip():
                    step = -(offset + 1)
                else:
                    step = flushed_step
                    if not supplied:
                        reply = flushed_reply
            elif reply:
                step = -(offset + 1)
        except OSError:
            pass
    return turn, step, reply, ""


def handle_claude_stop_repeat_guard(payload: Dict[str, Any], state_dir: Optional[Path] = None,
                                   logs_dir: Optional[Path] = None) -> Dict[str, Any]:
    _sync_repeat_tunables()
    continuation = (
        payload.get("stop_hook_active") is True
        or payload.get("continuationActive") is True
        or payload.get("continuation_active") is True
    )
    client = payload.get("_client", "claude")
    home = Path(os.environ.get("CODEX_HOME" if client == "codex" else "CLAUDE_CONFIG_DIR") or Path.home() / (".codex" if client == "codex" else ".claude")).expanduser()
    resolved_state_dir = Path(state_dir).expanduser() if state_dir is not None else home / "hooks" / "state"
    if continuation:
        if not resolved_state_dir.is_dir():
            return {}
        raw_sid = str(payload.get("session_id") or payload.get("sessionId") or "").strip()
        if not user_words.valid_session_id(raw_sid):
            return {}
        has_heavy_state = (resolved_state_dir / f"{raw_sid}_heavy_report_guard.json").is_file() or bool(
            user_words.load_unreported_heavy_completions(raw_sid, resolved_state_dir)
        )
        if not has_heavy_state:
            return {}
        turn_key, _, reply, _ = (_codex_turn_reply(payload) if client == "codex" else _claude_turn_reply(payload))
        try:
            with user_words.session_state_lock(resolved_state_dir, raw_sid):
                _check_heavy_report_locked(payload, resolved_state_dir, raw_sid, turn_key, reply, "block", reconcile_only=True)
        except (OSError, ValueError):
            pass
        return {}
    sid, _, _, skip = (
        user_words.codex_hook_session(payload)
        if client == "codex"
        else user_words.claude_hook_session(payload)
    )
    if skip or not user_words.valid_session_id(sid):
        return {}
    state_dir = resolved_state_dir
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
            state = _load_repeat_state(state_file, sid, logs_dir, client=client)
            record = state["turns"].setdefault(turn_key, {
                "consecutiveShortWaitCount": 0, "lastNormalizedReply": "", "lastStepIndex": None,
                "blockedSignatures": [],
            })
            if correction := _check_test_suite_locked(payload, reply, state_file, state, sid, turn_key, "block"):
                return correction
            if ev_hit := _check_turn_evidence_locked(
                payload, reply, state_file, state, sid, turn_key, "block", client=client,
            ):
                ev_kind, ev_resp = ev_hit
                user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
                    "event": f"Stop:{ev_kind}", "client": client, "session_id": sid, "turn_key": turn_key,
                })
                return ev_resp
            if ((step is not None and step == record["lastStepIndex"])
                    or (message_id and message_id == record.get("lastMessageId"))):
                return {}
            if ticket_block := _check_repeated_pending_ticket_locked(
                reply, state_file, state, record, -1,
                state_dir, sid, turn_key, payload, step, "block", message_id=message_id,
            ):
                user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
                    "event": "Stop:repeated_pending_ticket", "client": client, "session_id": sid, "turn_key": turn_key,
                })
                return ticket_block
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
                record["blockedSignatures"].append("wait:" + hashlib.sha256(record["lastNormalizedReply"].encode("utf-8")).hexdigest()[:16])
            _save_repeat_state(state_file, state, sid)
            if block:
                user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
                    "event": "Stop:reply_repeat_guard", "client": client, "session_id": sid,
                    "turn_key": turn_key, "count": record["consecutiveShortWaitCount"],
                })
                return {"decision": "block", "reason": _build_block_reason_text(client=client)}
    except (OSError, ValueError) as exc:
        user_words.append_jsonl_log(logs_dir / "lifecycle.log", {
            "event": "Stop:state_error", "client": client, "session_id": sid, "message": str(exc),
        })
    return {}


# ⑥ 命令行主入口

def main() -> None:
    with install_hook_self_guard(HOOKS_DIR):
        _main_inner()


def _main_inner() -> None:
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
            client_label = "Codex" if args.client == "codex" else "Claude"
            sys.stderr.write(f"{client_label} Stop JSON parse error: {e}\n")
        else:
            log_event("Stop:reply_repeat_guard", "unknown", f"Failed to parse stdin JSON: {e}")

    try:
        response = (handle_claude_stop_repeat_guard({**payload, "_client": args.client}, Path(args.state_dir) if args.state_dir else None,
                                                  Path(args.log_dir) if args.log_dir else None)
                    if args.client in ("claude", "codex") else handle_stop_repeat_guard(payload))
    except Exception as e:
        stack_str = traceback.format_exc()
        if args.client in ("claude", "codex"):
            client_label = "Codex" if args.client == "codex" else "Claude"
            sys.stderr.write(f"{client_label} Stop: {e}\n")
            response = {}
        else:
            log_event("Stop:reply_repeat_guard", str(payload.get("conversationId", "unknown")), f"Exception: {e}\n{stack_str}")
            response = {"decision": "allow"}

    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
