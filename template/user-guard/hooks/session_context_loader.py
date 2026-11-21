"""
session_context_loader.py - Antigravity、Codex 与 Claude Code 压缩后原话及最近子代理返回恢复钩子 (user-guard)

挂载事件：
- Antigravity（--client antigravity，默认）：
  * SessionStart：会话启动或重载时同步检查点游标，若存在尚未注入的压缩检查点则补发一次恢复提醒；
  * PreInvocation：每轮调用模型前按文件字节偏移增量检查新增 CHECKPOINT；未发生压缩时不额外读全盘、不注入上下文；发生压缩后提取全部用户原话与最近子代理返回摘要写入归档文件，并向本轮有界注入归档路径、原话摘要及最近子代理返回摘要，同时配合 handoff_gate.py 发送长会话提醒。
- Codex 与 Claude Code（--client codex / claude）：
  * SessionStart（触发源 compact 或 resume）：在上下文压缩或会话恢复时刷新 hooks/state/user-words/<session_id>.md，并通过 hookSpecificOutput.additionalContext 注入原话归档路径、历史指令摘要与最近子代理返回摘要。
"""

# ① 常量、阈值与宿主依赖

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS_DIR))
import user_words  # noqa: E402
from _lib import adaptive_policy as _adaptive_policy  # noqa: E402
from _lib.process_guard import install_hook_self_guard  # noqa: E402

user_words.ensure_utf8_stdio()

_DEFAULT_MAX_INLINE_TOTAL_CHARS = 8000
_DEFAULT_MAX_INLINE_ITEM_CHARS = 1200
_DEFAULT_MAX_INLINE_SUBAGENT_TOTAL_CHARS = 1400
_DEFAULT_MAX_INLINE_SUBAGENT_ITEM_CHARS = 280
_DEFAULT_MAX_TRANSCRIPT_TRACKED_IMAGES = 2000
_DEFAULT_ARCHIVE_RETRY_INTERVAL_SEC = 30

MAX_INLINE_TOTAL_CHARS = _DEFAULT_MAX_INLINE_TOTAL_CHARS
MAX_INLINE_ITEM_CHARS = _DEFAULT_MAX_INLINE_ITEM_CHARS
MAX_INLINE_SUBAGENT_TOTAL_CHARS = _DEFAULT_MAX_INLINE_SUBAGENT_TOTAL_CHARS
MAX_INLINE_SUBAGENT_ITEM_CHARS = _DEFAULT_MAX_INLINE_SUBAGENT_ITEM_CHARS
MAX_TRANSCRIPT_TRACKED_IMAGES = _DEFAULT_MAX_TRANSCRIPT_TRACKED_IMAGES
ARCHIVE_RETRY_INTERVAL_SEC = _DEFAULT_ARCHIVE_RETRY_INTERVAL_SEC


_extract_runtime_model_and_effort = _adaptive_policy.extract_runtime_model_and_effort


def _resolve_loader_policy(client: str = "", model_name: str = "", effort: str = "") -> Dict[str, Any]:
    """合并 fleet_profile.json 中按主会话客户端与模型校准的恢复参数，并兼容单测模块级常量覆盖。"""
    policy = user_words.get_compaction_tunables(client, HOOKS_DIR, model_name=model_name, effort=effort)
    if MAX_INLINE_TOTAL_CHARS != _DEFAULT_MAX_INLINE_TOTAL_CHARS:
        policy["inline_total_chars"] = max(400, int(MAX_INLINE_TOTAL_CHARS))
    if MAX_INLINE_ITEM_CHARS != _DEFAULT_MAX_INLINE_ITEM_CHARS:
        policy["inline_user_item_chars"] = max(200, int(MAX_INLINE_ITEM_CHARS))
    if MAX_INLINE_SUBAGENT_TOTAL_CHARS != _DEFAULT_MAX_INLINE_SUBAGENT_TOTAL_CHARS:
        policy["inline_subagent_total_chars"] = max(120, int(MAX_INLINE_SUBAGENT_TOTAL_CHARS))
    if MAX_INLINE_SUBAGENT_ITEM_CHARS != _DEFAULT_MAX_INLINE_SUBAGENT_ITEM_CHARS:
        policy["inline_subagent_item_chars"] = max(120, int(MAX_INLINE_SUBAGENT_ITEM_CHARS))
    if MAX_TRANSCRIPT_TRACKED_IMAGES != _DEFAULT_MAX_TRANSCRIPT_TRACKED_IMAGES:
        policy["max_tracked_images"] = max(10, int(MAX_TRANSCRIPT_TRACKED_IMAGES))
    if ARCHIVE_RETRY_INTERVAL_SEC != _DEFAULT_ARCHIVE_RETRY_INTERVAL_SEC:
        policy["archive_retry_interval_sec"] = max(1, int(ARCHIVE_RETRY_INTERVAL_SEC))
    return policy

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
UI_BANNER_PHRASE = "this conversation grew too large, so the history before this point may be missing or incomplete"

# ② 目录、日志与压缩状态读写

def get_data_dirs() -> Tuple[Path, Path]:
    return user_words.resolve_data_dirs(HOOKS_DIR)


def get_current_iso_time() -> str:
    return user_words.current_iso_time()


def log_event(
    event_name: str,
    conversation_id: str,
    message: str,
    payload_summary: Optional[Dict[str, Any]] = None,
) -> None:
    """追加结构化日志到 logs/lifecycle.log，附带 2MB 自动滚动裁剪防无限膨胀"""
    user_words.append_lifecycle_log(HOOKS_DIR, event_name, conversation_id, message, payload_summary)


def _default_compaction_state(conversation_id: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "telemetryVersion": 6,
        "conversationId": conversation_id,
        "scannedByteOffset": 0,
        "scannedTranscriptName": "",
        "scannedTranscriptIdentity": [],
        "scannedTranscriptAnchor": "",
        "scannedTranscriptMtimeNs": -1,
        "latestCheckpointStep": -1,
        "lastInjectedCheckpointStep": -1,
        "explicitResumeInjected": False,
        "archiveComplete": False,
        "lastExplicitCompact": [],
        "hasExplicitUserInput": False,
        "checkpointSteps": [],
        "mediaStepCount": 0,
        "transcriptImageSizes": {},
        "transcriptImageCallCount": 0,
        "transcriptImageResultCount": 0,
        "transcriptImageAttemptSizes": {},
        "hostBannerStep": -1,
        "latestStepIndex": -1,
        "latestUserStepIndex": -1,
        "latestUserInputHash": "",
        "latestUserInputPreview": "",
        "latestUserHandoffIntent": "",
        "latestHandoffStepIndex": -1,
        "latestHandoffInputHash": "",
        "latestUserBannerReport": False,
        "isGoalActive": False,
        "latestGoalStepIndex": -1,
        "pendingControlSteps": {},
        "controlFullSignature": [],
        "controlFullCheckedAt": 0.0,
        "updatedAt": get_current_iso_time(),
    }


def _load_compaction_state(state_file: Path, conversation_id: str) -> Dict[str, Any]:
    default = _default_compaction_state(conversation_id)
    if not state_file.exists():
        return default
    try:
        data = json.loads(state_file.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and data.get("version") == 1 and data.get("conversationId") == conversation_id:
            merged = dict(default)
            merged.update(data)
            if int(data.get("telemetryVersion", 1)) < default["telemetryVersion"]:
                for key in default.keys() - {"lastInjectedCheckpointStep", "explicitResumeInjected"}:
                    merged[key] = default[key]
            for key in ("scannedTranscriptIdentity", "scannedTranscriptAnchor", "scannedTranscriptMtimeNs",
                        "archiveComplete", "lastExplicitCompact"):
                if merged[key] is None:
                    merged[key] = default[key]
            for key in default:
                if type(merged[key]) is not type(default[key]):
                    raise ValueError(f"字段 {key} 格式错误")
            if (merged["scannedByteOffset"] < 0
                    or any(type(value) is not int or value < 0 for value in merged["checkpointSteps"])
                    or any(type(value) is not int or value < 0
                           for key in ("transcriptImageSizes", "transcriptImageAttemptSizes")
                           for value in merged[key].values())
                    or any(not key.isdecimal() or value not in ("user", "reply")
                           for key, value in merged["pendingControlSteps"].items())):
                raise ValueError("上下文状态含无效游标或遥测记录")
            return merged
        raise ValueError("会话状态格式或身份错误")
    except (ValueError, OSError, TypeError, RecursionError) as exc:
        log_event("Context:state_error", conversation_id, f"无法读取上下文状态（{exc}），从轨迹重建")
    return default


def _agy_source_signatures(transcript_path: Path) -> List[List[int]]:
    signatures: List[List[int]] = []
    for path in (transcript_path, transcript_path.parent / "transcript_full.jsonl"):
        try:
            stat = path.stat()
            signatures.append([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns])
        except OSError:
            signatures.append([])
    return signatures


def _append_codex_log(log_path: Path, entry: Dict[str, Any]) -> None:
    """向 Codex 钩子日志文件写入仅含元数据的 JSONL 记录（附带 2MB 自动滚动裁剪）"""
    user_words.append_jsonl_log(log_path, entry)


# ③ 增量轨迹解析、遥测与迟到控制指令对账

def _decode_proto_str(raw: Any) -> str:
    """还原 protojson 参数里带外层引号与转义反斜杠的字符串值。"""
    return user_words._unquote_proto_scalar(raw).strip("\"'")


def _reconcile_agy_controls(transcript_path: Path, state: Dict[str, Any], steps: List[Dict[str, Any]]) -> bool:
    """仅用完整控制事件更新权限。迟到全文按步骤顺序补入，较新已核实命令优先。"""
    pending = state["pendingControlSteps"]
    events: Dict[int, Tuple[str, Any]] = {}
    new_pending = False
    changed = False
    for step in steps:
        step_index = step.get("step_index")
        if not isinstance(step_index, int):
            continue
        if step.get("type") == "USER_INPUT" or step.get("source") == "USER_EXPLICIT":
            event_kind = "user"
        elif step.get("type") == "PLANNER_RESPONSE":
            event_kind = "reply"
        else:
            continue
        truncated_fields = step.get("truncated_fields")
        if isinstance(truncated_fields, list) and "content" in truncated_fields:
            pending[str(step_index)] = event_kind
            new_pending = True
            if event_kind == "user" and step_index >= state["latestUserStepIndex"]:
                state.update(latestUserStepIndex=step_index, latestUserInputHash="", latestUserInputPreview="",
                             latestUserBannerReport=False)
        else:
            events[step_index] = (event_kind, step.get("content"))
            pending.pop(str(step_index), None)
    if pending:
        signature = _agy_source_signatures(transcript_path)[1]
        now = time.time()
        retry_sec = int(_resolve_loader_policy("antigravity")["archive_retry_interval_sec"])
        if (new_pending or signature != state["controlFullSignature"]
                or now - state["controlFullCheckedAt"] >= retry_sec):
            full = user_words._load_truncated_contents_map(
                transcript_path.parent / "transcript_full.jsonl", {int(index) for index in pending},
            )
            for index, content in full.items():
                events[index] = (pending.pop(str(index)), content)
            state.update(controlFullSignature=signature, controlFullCheckedAt=now)
            changed = True
    if not events:
        return changed

    import handoff_gate

    for index, (kind, content) in sorted(events.items()):
        if kind == "user":
            cleaned = user_words.clean_user_content(content)
            text_hash = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]
            if index >= state["latestUserStepIndex"]:
                preview = cleaned if len(cleaned) <= 4000 else cleaned[:2500] + "\n...\n" + cleaned[-1500:]
                state.update(latestUserStepIndex=index, latestUserInputPreview=preview,
                             latestUserInputHash=text_hash,
                             latestUserBannerReport=handoff_gate.agy_banner_report(cleaned))
            intent = handoff_gate.agy_handoff_intent(cleaned)
            if intent and index >= state["latestHandoffStepIndex"]:
                state.update(latestUserHandoffIntent=intent, latestHandoffStepIndex=index,
                             latestHandoffInputHash=text_hash)
            command = user_words.agy_goal_command(cleaned)
        else:
            command = False if isinstance(content, str) and user_words.has_goal_cancel_marker(content) else None
        if command is not None and index >= state["latestGoalStepIndex"]:
            state.update(latestGoalStepIndex=index, isGoalActive=command)
    return True


def _scan_transcript_incrementally(
    transcript_path: Path,
    state: Dict[str, Any],
) -> bool:
    """
    从上次 scannedByteOffset 起以二进制模式仅扫描新增尾部字节，
    识别新增的 CHECKPOINT 步骤与 USER_INPUT 标记。
    返回 state 是否发生变更。
    """
    try:
        file_stat = transcript_path.stat()
    except OSError:
        return False

    file_size = file_stat.st_size
    offset = int(state.get("scannedByteOffset", 0))
    prev_name = str(state.get("scannedTranscriptName") or "")
    prev_id = state.get("scannedTranscriptIdentity")
    identity = [file_stat.st_dev, file_stat.st_ino]
    changed = False

    needs_reset = (
        (prev_name and prev_name != transcript_path.name)
        or file_size < offset
        or (prev_id and prev_id != identity)
    )

    parsed_steps: List[Dict[str, Any]] = []
    latest_scanned_step = -1
    new_offset = 0 if needs_reset else offset
    try:
        with open(transcript_path, "rb") as stream:
            # 用打开后的文件身份确定快照，避免轨迹替换时把旧游标或空白重置写回磁盘。
            opened = os.fstat(stream.fileno())
            if [opened.st_dev, opened.st_ino] != identity:
                return False
            if needs_reset:
                previous_checkpoint = state["lastInjectedCheckpointStep"] if prev_name != transcript_path.name else -1
                state.update(_default_compaction_state(state["conversationId"]))
                state["lastInjectedCheckpointStep"] = previous_checkpoint
                state.pop("pendingArchive", None)
                offset = 0
                changed = True
            file_size = opened.st_size
            stream.seek(max(0, offset - 128))
            scanned_tail = stream.read(min(128, offset))
            anchor = hashlib.sha256(scanned_tail).hexdigest()
            rewritten = file_size < offset or bool(
                offset and state.get("scannedTranscriptAnchor") not in (None, "", anchor)
            )
            if rewritten:
                state.update(_default_compaction_state(state["conversationId"]))
                state.pop("pendingArchive", None)
                offset = new_offset = 0
                scanned_tail = b""
                changed = True
            if file_size == offset:
                tail_anchor = hashlib.sha256(scanned_tail).hexdigest()
                core_same = (
                    state.get("scannedTranscriptName") == transcript_path.name
                    and state.get("scannedTranscriptIdentity") == identity
                    and state.get("scannedTranscriptAnchor") == tail_anchor
                    and state.get("scannedTranscriptMtimeNs") not in (None, -1)
                )
                if not core_same:
                    state.update(
                        {
                            "scannedTranscriptName": transcript_path.name,
                            "scannedTranscriptIdentity": identity,
                            "scannedTranscriptAnchor": tail_anchor,
                            "scannedTranscriptMtimeNs": opened.st_mtime_ns,
                        }
                    )
                    changed = True
                return _reconcile_agy_controls(transcript_path, state, []) or changed
            stream.seek(offset)
            # 逐行读取固定大小的文件快照。保留未完成尾行的起点，下轮从原位置重读；
            # 不能仅靠末尾的右花括号判断 JSON 完整，也不能按 Unicode 行分隔符拆正文。
            while stream.tell() < file_size:
                row_offset = stream.tell()
                raw_line = stream.readline(file_size - row_offset)
                try:
                    step = json.loads(user_words.decode_jsonl_line(raw_line)) if raw_line.strip() else None
                except (ValueError, RecursionError):
                    if not raw_line.endswith(b"\n"):
                        break
                    step = None
                new_offset = stream.tell()
                scanned_tail = (scanned_tail + raw_line)[-128:]
                if not isinstance(step, dict):
                    continue
                step_index = step.get("step_index")
                if type(step_index) is int:
                    latest_scanned_step = max(latest_scanned_step, step_index)
                if (step.get("type") in ("USER_INPUT", "CHECKPOINT", "PLANNER_RESPONSE", "SYSTEM_MESSAGE")
                        or step.get("source") == "USER_EXPLICIT" or step.get("media") or step.get("tool_calls")):
                    parsed_steps.append(step)
    except OSError:
        return changed
    if new_offset == offset:
        return changed

    latest_cp = int(state.get("latestCheckpointStep", -1))
    has_user = bool(state.get("hasExplicitUserInput", False))
    cp_steps: List[int] = [int(x) for x in (state.get("checkpointSteps") or []) if isinstance(x, int)]
    cp_seen = set(cp_steps)
    media_step_count = int(state.get("mediaStepCount", 0))
    img_sizes: Dict[str, int] = dict(state.get("transcriptImageSizes") or {})
    attempt_sizes: Dict[str, int] = dict(state.get("transcriptImageAttemptSizes") or {})
    img_call_count = int(state.get("transcriptImageCallCount", 0))
    img_result_count = int(state.get("transcriptImageResultCount", 0))
    host_banner_step = int(state.get("hostBannerStep", -1))
    latest_step = max(int(state.get("latestStepIndex", -1)), latest_scanned_step)

    max_tracked_images = int(_resolve_loader_policy("antigravity")["max_tracked_images"])

    def _stat_image_uri(raw_uri: str, image_map: Dict[str, int], is_image_media: bool = False) -> None:
        if not raw_uri or len(image_map) >= max_tracked_images:
            return
        cleaned = _decode_proto_str(raw_uri)
        if cleaned.startswith("file://"):
            cleaned = cleaned[7:]
            if os.name == "nt" and cleaned.startswith("/") and len(cleaned) > 2 and cleaned[2] == ":":
                cleaned = cleaned[1:]
        suffix = Path(cleaned.replace("\\", "/")).suffix.lower()
        if suffix not in IMAGE_EXTS and not is_image_media:
            return
        try:
            p = Path(cleaned)
            key_str = str(p.resolve()) if p.exists() else cleaned
        except OSError:
            key_str = cleaned
        key = key_str.casefold() if os.name == "nt" else key_str
        if key not in image_map:
            sz = 0
            try:
                p_obj = Path(cleaned)
                if p_obj.exists() and p_obj.is_file():
                    sz = int(p_obj.stat().st_size)
            except OSError:
                sz = 0
            image_map[key] = sz
        elif image_map[key] == 0:
            try:
                p_obj = Path(cleaned)
                if p_obj.exists() and p_obj.is_file():
                    image_map[key] = int(p_obj.stat().st_size)
            except OSError:
                pass

    for step in parsed_steps:
        s_type = step.get("type", "")
        s_source = step.get("source", "")
        step_idx = step.get("step_index")
        if isinstance(step_idx, int) and step_idx > latest_step:
            latest_step = step_idx

        if s_type == "CHECKPOINT":
            cp_num = int(step_idx) if isinstance(step_idx, int) else max(latest_cp + 1, 0)
            if cp_num > latest_cp:
                latest_cp = cp_num
            if cp_num not in cp_seen:
                cp_seen.add(cp_num)
                cp_steps.append(cp_num)

        if s_type == "USER_INPUT" or s_source == "USER_EXPLICIT":
            has_user = True

        if s_type == "SYSTEM_MESSAGE" and s_source in ("SYSTEM", "SYSTEM_SDK"):
            text = str(step.get("content") or "").strip()
            for tag in ("SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE"):
                if text.startswith(f"<{tag}>") and text.endswith(f"</{tag}>"):
                    text = text[len(tag) + 2:-(len(tag) + 3)].strip()
            if text.casefold().rstrip(".") == UI_BANNER_PHRASE and isinstance(step_idx, int):
                host_banner_step = max(host_banner_step, step_idx)

        media_list = step.get("media")
        if isinstance(media_list, list) and media_list and not step.get("error"):
            if s_type != "CHECKPOINT":
                media_step_count += 1
            has_image_result = False
            for m_item in media_list:
                if isinstance(m_item, dict):
                    uri_val = str(m_item.get("uri") or m_item.get("path") or "")
                    is_image = str(m_item.get("mime_type") or "").startswith("image/")
                    if s_type != "CHECKPOINT":
                        _stat_image_uri(uri_val, img_sizes, is_image)
                    has_image_result |= is_image or Path(uri_val).suffix.lower() in IMAGE_EXTS
            if has_image_result and s_type in ("GENERIC", "TOOL_RESULT", "TOOL_RESPONSE") and step.get("status") in (
                "DONE", "SUCCESS", "COMPLETED",
            ):
                img_result_count += 1

        tool_calls = step.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_name = str(tc.get("name") or tc.get("tool_name") or "").strip()
                if tc_name not in ("read_file", "view_file"):
                    continue
                tc_args = tc.get("args") or tc.get("arguments")
                if isinstance(tc_args, str):
                    try:
                        tc_args = json.loads(tc_args)
                    except (ValueError, RecursionError):
                        tc_args = {}
                if isinstance(tc_args, dict):
                    f_path = _decode_proto_str(tc_args.get("AbsolutePath") or tc_args.get("Path") or tc_args.get("path"))
                    if Path(f_path.replace("\\", "/")).suffix.lower() in IMAGE_EXTS:
                        img_call_count += 1
                        _stat_image_uri(f_path, attempt_sizes)

    state["scannedByteOffset"] = new_offset
    state["scannedTranscriptName"] = transcript_path.name
    state["scannedTranscriptIdentity"] = identity
    state["scannedTranscriptAnchor"] = hashlib.sha256(scanned_tail).hexdigest()
    state["scannedTranscriptMtimeNs"] = opened.st_mtime_ns
    state["latestCheckpointStep"] = latest_cp
    state["hasExplicitUserInput"] = has_user
    state["checkpointSteps"] = sorted(cp_seen)
    state["mediaStepCount"] = media_step_count
    state["transcriptImageSizes"] = img_sizes
    state["transcriptImageCallCount"] = img_call_count
    state["transcriptImageResultCount"] = img_result_count
    state["transcriptImageAttemptSizes"] = attempt_sizes
    state["hostBannerStep"] = host_banner_step
    state["latestStepIndex"] = latest_step
    _reconcile_agy_controls(transcript_path, state, parsed_steps)
    state["updatedAt"] = get_current_iso_time()
    return True


# ④ 原话内联摘要与字符预算

def _unclosed_markdown_fence(text: str) -> str:
    fence = ""
    for line in text.splitlines():
        match = user_words.MARKDOWN_FENCE_RE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ""
        elif match:
            fence = match[1]
    return fence


def _close_unbalanced_fence(text: str, max_len: Optional[int] = None) -> str:
    if max_len is not None and len(text) > max_len:
        text = text[:max_len]
    fence = _unclosed_markdown_fence(text)
    if not fence:
        return text
    closing = ("\n" if text and not text.endswith("\n") else "") + fence
    if max_len is not None and len(text) + len(closing) > max_len:
        keep = max(0, max_len - len(closing))
        text = text[:keep]
        fence = _unclosed_markdown_fence(text)
        if not fence:
            return text
        closing = ("\n" if text and not text.endswith("\n") else "") + fence
        if len(text) + len(closing) > max_len:
            return text[:max_len]
    return text + closing


def _truncate_single_item(content: str, turn: int, max_chars: int = MAX_INLINE_ITEM_CHARS) -> str:
    if len(content) <= max_chars:
        return _close_unbalanced_fence(content)
    marker = f"\n...[第 {turn} 条记录的中间部分省略]...\n"
    usable = max(max_chars - len(marker), 80)
    tail_len = min(120, max(usable // 3, 25))
    head_len = max(usable - tail_len, 40)
    head_part = _close_unbalanced_fence(content[:head_len].rstrip())
    tail_part = _close_unbalanced_fence(content[-tail_len:].lstrip())
    return f"{head_part}{marker}{tail_part}"


def _format_inline_subagent_section(
    subagent_returns: List[Dict[str, Any]],
    policy: Optional[Dict[str, Any]] = None,
) -> str:
    if not subagent_returns:
        return ""
    cfg = policy if isinstance(policy, dict) else _resolve_loader_policy("")
    sub_total_cap = int(cfg["inline_subagent_total_chars"])
    sub_item_cap = int(cfg["inline_subagent_item_chars"])
    sub_head_ratio = float(cfg.get("subagent_head_ratio", 0.58))
    title = "【最近子代理返回摘要（采纳前须按 R6 核对原文件或子代理日志）】"
    chosen_blocks: List[str] = []
    used = len(title) + 2
    for item in reversed(subagent_returns):
        label = str(item.get("label") or item.get("agent_id") or "subagent").strip()
        trunc_tag = " | 轨迹截断" if item.get("truncated") else ""
        log_hint = f"\n原始日志：{item['log_path']}" if item.get("log_path") else ""
        raw_sub_text = item.get("raw_body") if isinstance(item.get("raw_body"), str) and item.get("raw_body") else item.get("content", "")
        collapsed_sub_text = user_words.collapse_historical_cards(raw_sub_text)
        body = _close_unbalanced_fence(user_words.summarize_subagent_return(collapsed_sub_text, sub_item_cap, sub_head_ratio))
        blk = f"· [{label} | Step {item['step_index']}{trunc_tag}]{log_hint}\n{body}"
        cost = len(blk) + (2 if chosen_blocks else 1)
        if used + cost > sub_total_cap:
            break
        chosen_blocks.append(blk)
        used += cost
    if not chosen_blocks:
        return ""
    chosen_blocks.reverse()
    return "\n\n" + title + "\n" + "\n\n".join(chosen_blocks)


def build_compaction_system_message(
    words: List[Dict[str, Any]],
    saved_md_path: Optional[Path],
    trigger_reason: str,
    *,
    archive_status: str = "已保存可提取记录",
    issues: Optional[List[str]] = None,
    source_paths: Optional[List[str]] = None,
    header: str = "",
    subagent_returns: Optional[List[Dict[str, Any]]] = None,
    client: str = "",
    policy: Optional[Dict[str, Any]] = None,
) -> str:
    """构造带动态预算上限保护与归档文件指针的原话及最近子代理摘要回溯消息"""
    cfg = policy if isinstance(policy, dict) else _resolve_loader_policy(client)
    total_cap = int(cfg["inline_total_chars"])
    user_item_cap = int(cfg["inline_user_item_chars"])
    head_turns = max(1, int(cfg.get("inline_guaranteed_head_turns", 1)))
    archive_str = str(saved_md_path) if saved_md_path else "未保存归档文件"
    total_turns = len(words)
    recent_returns = list(subagent_returns or [])

    if header:
        header_lines = [header]
        if recent_returns:
            header_lines.append(f"最近子代理返回摘要：{len(recent_returns)} 条（已附于归档末尾）")
    else:
        sub_note = f"，附最近 {len(recent_returns)} 条子代理返回摘要" if recent_returns else ""
        header_lines = [
            f"【系统原话回溯】会话经过压缩或恢复（触发源：{trigger_reason}）。",
            f"归档位置：{archive_str}",
            f"归档状态：{archive_status}。本次提取 {total_turns} 条记录{sub_note}。",
        ]
    if source_paths:
        header_lines.append("原始轨迹：" + "、".join(source_paths[:2]))
        if len(source_paths) > 2:
            header_lines.append(f"另有 {len(source_paths) - 2} 个分段，参见校验清单。")
    if issues:
        detail = "、".join(issues)
        header_lines.append(f"缺口信息：{detail[:1100]}" + ("…（其余缺口见日志或归档）" if len(detail) > 1100 else ""))
    header_lines.append("请按全局规则 R9 核对原始轨迹、归档状态及用户最新裁定，汇报现状后继续。")
    header_text = "\n".join(header_lines) + "\n\n"

    subagent_section = _format_inline_subagent_section(recent_returns, cfg)
    words_total_budget = total_cap - len(subagent_section)

    formatted_blocks: List[str] = []
    for w in words:
        time_tag = f", {w['created_at']}" if w.get("created_at") else ""
        kind_label = " [选项框回答]" if w.get("kind") == "ask_question_answer" else ""
        if w.get("truncated"):
            kind_label += " [截断正文待补]"
        if w.get("provenance_verified") is False:
            kind_label += " [用户角色，来源未核实]"
        body = _truncate_single_item(user_words.collapse_historical_cards(w["content"]), w["turn"], user_item_cap)
        if w.get("attachments") and w.get("source_path"):
            body += f"\n附件来源：{w['source_path']}"
        block = f"【第 {w['turn']} 轮指令】{kind_label} (Step {w['step_index']}{time_tag})\n{body}"
        formatted_blocks.append(block)

    def _merge_preserving_subagents(words_part: str) -> str:
        if len(words_part) + len(subagent_section) <= total_cap:
            return words_part + subagent_section
        if subagent_section and words_total_budget >= 80:
            compressed_words = _close_unbalanced_fence(words_part, words_total_budget)
            return compressed_words + subagent_section
        return _close_unbalanced_fence(words_part + subagent_section, total_cap)

    joined_all = "\n\n".join(formatted_blocks)
    if len(header_text) + len(joined_all) <= words_total_budget or not formatted_blocks:
        return _merge_preserving_subagents(header_text + joined_all)

    # 超出总预算时：按策略保留开篇立项轮次 + 逆序尽可能多地保留最近定论轮次
    kept_head = formatted_blocks[:1]
    for cand_blk in formatted_blocks[1:head_turns]:
        cand_head_text = "\n\n".join(kept_head + [cand_blk])
        if len(header_text) + len(cand_head_text) + 260 <= words_total_budget:
            kept_head.append(cand_blk)
        else:
            break
    head_count = len(kept_head)
    head_joined = "\n\n".join(kept_head)
    budget_left = words_total_budget - len(header_text) - len(head_joined) - 180
    tail_blocks: List[str] = []
    used = 0
    for blk in reversed(formatted_blocks[head_count:]):
        if used + len(blk) + 2 > budget_left:
            break
        tail_blocks.append(blk)
        used += len(blk) + 2
    tail_blocks.reverse()

    omitted_count = len(formatted_blocks) - head_count - len(tail_blocks)
    if omitted_count > 0:
        start_omitted_turn = head_count + 1
        end_omitted_turn = head_count + omitted_count
        omit_notice = (
            f"...（中间第 {start_omitted_turn}～{end_omitted_turn} 轮共 {omitted_count} 条历史指令因上下文预算省略内联；"
            "请按 R9 查阅轨迹和已保存的归档）..."
        )
        words_rendered = header_text + "\n\n".join(kept_head + [omit_notice] + tail_blocks)
        return _merge_preserving_subagents(words_rendered)

    return _merge_preserving_subagents(header_text + joined_all)


# ⑤ Antigravity 恢复流程与兼容入口

def _workflow_recovery_blocks(session_id: str, state_dir: Path, ticket=None) -> List[str]:
    """专项计划与未报账单独立于有界原话摘要，不随摘要截断。"""
    blocks = []
    if isinstance(ticket, list):
        pending_tickets = [item for item in ticket if isinstance(item, dict)]
    elif isinstance(ticket, dict):
        pending_tickets = [ticket]
    else:
        pending_tickets = user_words.find_session_pending_tickets(session_id, state_dir)
    for pending_ticket in pending_tickets:
        blocks.append(user_words.render_pending_workflow_block(pending_ticket))
    completions = user_words.load_unreported_heavy_completions(session_id, state_dir)
    if completions:
        lines = ["【待汇报重负载完工账单】"]
        for entry in completions:
            lines.append("运行：" + str(entry.get("run_id")) + "；全部成功：" + str(entry.get("all_succeeded"))
                         + "；回执：" + str(entry.get("receipt_file") or "未记录"))
            for stream in entry.get("streams", []):
                lines.append("流：" + str(stream.get("stream_id")) + "；引擎：" + str(stream.get("engine"))
                             + "；模型：" + str(stream.get("model")) + "；成功：" + str(stream.get("succeeded"))
                             + "；状态：" + str(stream.get("status")) + "；Token："
                             + json.dumps(stream.get("token_usage"), ensure_ascii=False, sort_keys=True))
            lines.append("Token 汇总：" + json.dumps(entry.get("token_usage_summary"), ensure_ascii=False, sort_keys=True))
        blocks.append("\n".join(lines))
    return blocks


def _handle_context_locked(conversation_id: str, event_name: str, explicit_source: str,
                           transcript_path: Path, state_dir: Path,
                           payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state_file = state_dir / f"{conversation_id}_compaction.json"
    state = _load_compaction_state(state_file, conversation_id)

    state_changed = _scan_transcript_incrementally(transcript_path, state)

    latest_cp = int(state.get("latestCheckpointStep", -1))
    last_injected_cp = int(state.get("lastInjectedCheckpointStep", -1))
    has_user_input = bool(state.get("hasExplicitUserInput", False))

    # 子代理会话（无显式人类 USER_INPUT）不执行主会话原话注入与长会话交接门禁
    if not has_user_input:
        if state_changed:
            user_words.atomic_write_json(state_file, state)
        return {"injectSteps": []}

    pending_tickets = user_words.find_session_pending_tickets(conversation_id, state_dir)
    # Antigravity 专属长会话“原子收口 + 交接停机”硬门禁评估（懒加载以保持 --client codex 零依赖隔离）
    _agy_m, _agy_e = _extract_runtime_model_and_effort("antigravity", payload)
    import handoff_gate  # noqa: E402

    try:
        gate_res = handoff_gate.evaluate_pre_invocation_locked(
            conversation_id=conversation_id,
            event_name=event_name,
            state_dir=state_dir,
            compaction_state=state,
            transcript_path=transcript_path,
            payload=payload,
            model_name=_agy_m,
            effort=_agy_e,
        )
    except (ValueError, TypeError, OSError) as exc:
        log_event(f"{event_name}:handoff_gate_error", conversation_id, f"交接门禁状态异常，继续原话恢复（{exc}）")
        gate_res = {"phase": "normal", "advisoryNotice": None, "mandateMessage": None}
    agy_policy = _resolve_loader_policy("antigravity", _agy_m, _agy_e)
    trigger_reason: Optional[str] = None
    pending = state.get("pendingArchive")
    pending = pending if isinstance(pending, dict) else {}
    signatures = _agy_source_signatures(transcript_path)
    if latest_cp > last_injected_cp:
        trigger_reason = f"CHECKPOINT step #{latest_cp}"
    elif explicit_source in ("compact", "compaction") and state.get("lastExplicitCompact") != signatures[0]:
        trigger_reason = f"explicit {explicit_source}"
    elif explicit_source == "resume" and not state.get("explicitResumeInjected", False):
        trigger_reason = "explicit resume"
    elif pending:
        last_scan = pending.get("checkedAt", 0)
        last_scan = last_scan if isinstance(last_scan, (float, int)) else 0
        old_signatures = pending.get("sources")
        full_changed = isinstance(old_signatures, list) and len(old_signatures) == 2 and old_signatures[1] != signatures[1]
        if full_changed or time.time() - last_scan >= int(agy_policy["archive_retry_interval_sec"]):
            trigger_reason = "原话归档缺口复核"

    injected_blocks: List[str] = []

    if trigger_reason:
        # 确证发生新的上下文压缩或首次显式恢复：提取纯净用户原话与最近子代理返回摘要（严格与人类原话分区）
        archive = user_words.extract_agy_archive(str(transcript_path), include_subagent_msgs=False, policy=agy_policy)
        words = archive["words"]
        subagent_returns = archive.get("subagent_returns") or []
        unresolved_steps = archive["unresolved_steps"]
        unverified_answers = archive["unverified_answers"]
        issues = list(archive["issues"])
        if unresolved_steps:
            issues.append(f"以下步骤的截断正文待补：{unresolved_steps}")
        if unverified_answers:
            issues.append(f"以下步骤的选项回答来源未核实：{unverified_answers}")
        if _agy_source_signatures(transcript_path) != signatures:
            issues.append("提取时轨迹发生变化，待再次核对")
        if not words:
            issues.append("未提取到可归档的用户输入")

        existing = state_dir / f"{conversation_id}_user_words.md"
        previous_issues = pending.get("issues")
        update_notice = trigger_reason != "原话归档缺口复核" or issues != previous_issues
        previous_was_complete = state.get("archiveComplete") is True
        if issues and existing.is_file() and previous_was_complete:
            saved_md_path = existing
            archive_status = "保留已有文件，本次提取存在缺口"
        else:
            saved_md_path = (
                user_words.save_user_words_markdown(
                    state_dir, conversation_id, words, issues, subagent_returns=subagent_returns, policy=agy_policy
                )
                if update_notice or not existing.is_file()
                else existing
            )
            archive_status = "已保存部分记录" if issues else "本次可见轨迹中的原话已归档"
            state["archiveComplete"] = bool(saved_md_path) and not issues
        if issues:
            state["pendingArchive"] = {"sources": signatures, "checkedAt": time.time(), "issues": issues}
        else:
            state.pop("pendingArchive", None)
        if latest_cp > last_injected_cp:
            state["lastInjectedCheckpointStep"] = latest_cp
        if explicit_source == "resume":
            state["explicitResumeInjected"] = True
        if explicit_source in ("compact", "compaction"):
            state["lastExplicitCompact"] = signatures[0]
        state_changed = True
        if update_notice:
            injected_blocks.append(build_compaction_system_message(
                words, saved_md_path, trigger_reason, archive_status=archive_status, issues=issues,
                source_paths=[str(transcript_path), str(transcript_path.parent / "transcript_full.jsonl")],
                subagent_returns=subagent_returns, client="antigravity", policy=agy_policy,
            ))
            # 同一步骤若 handoff_gate 已随 mandateMessage 输出完整未决工作流交接块，
            # 跳过重复的 pending_tickets 恢复块（仍保留未汇报的 heavy_completions 完工账单）。
            recovery_pending = [] if gate_res.get("workflowNoticeFull") else pending_tickets
            injected_blocks.extend(_workflow_recovery_blocks(conversation_id, state_dir, recovery_pending))
            state["lastInjectedAt"] = get_current_iso_time()
            state["lastInjectedWordCount"] = len(words)
        log_event(
            event_name, conversation_id, f"原话归档检查：{trigger_reason}（{archive_status}）",
            {"wordCount": len(words), "subagentReturnCount": len(subagent_returns),
             "latestCheckpointStep": latest_cp, "unresolvedSteps": unresolved_steps,
             "unverifiedAnswers": unverified_answers, "issues": issues},
        )

    if gate_res.get("advisoryNotice") and not gate_res.get("mandateMessage"):
        injected_blocks.append(str(gate_res["advisoryNotice"]))
    if gate_res.get("mandateMessage"):
        injected_blocks.append(str(gate_res["mandateMessage"]))

    if state_changed:
        user_words.atomic_write_json(state_file, state)

    if not injected_blocks:
        return {"injectSteps": []}

    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": "\n\n---\n\n".join(injected_blocks),
                }
            }
        ]
    }


def handle_context_check(payload: Dict[str, Any], event_name: str = "PreInvocation") -> Dict[str, Any]:
    """
    统一处理 SessionStart 与 PreInvocation 事件：
    1. 日常无新增 CHECKPOINT：按增量字节游标直接返回 {"injectSteps": []}，避免全文件重复扫描与重复注入；
    2. 捕获新增 CHECKPOINT（或显式 compact/resume 信号）：提取全量用户原话落盘并精确注入 1 次。
    """
    conversation_id = str(payload.get("conversationId") or "unknown_session").strip()
    transcript_path_str = payload.get("transcriptPath")
    explicit_source = str(payload.get("source") or payload.get("eventSource") or "").strip().lower()

    if explicit_source in ("startup", "fresh", "new"):
        if event_name == "SessionStart":
            state_dir, _ = get_data_dirs()
            user_words.sweep_stale_lock_files(state_dir, HOOKS_DIR)
        log_event(event_name, conversation_id, f"Explicit {explicit_source} start. Skipping injection.")
        return {"injectSteps": []}

    if not transcript_path_str or not user_words.valid_session_id(conversation_id):
        return {"injectSteps": []}

    transcript_path = user_words.resolve_compact_transcript_path(Path(transcript_path_str).expanduser())
    if not transcript_path.exists() or not transcript_path.is_file():
        return {"injectSteps": []}

    state_dir, _ = get_data_dirs()
    if event_name == "SessionStart":
        user_words.sweep_stale_lock_files(state_dir, HOOKS_DIR)
    try:
        with user_words.session_state_lock(state_dir, conversation_id):
            return _handle_context_locked(
                conversation_id, event_name, explicit_source, transcript_path, state_dir, payload=payload
            )
    except (TimeoutError, OSError) as exc:
        log_event(event_name, conversation_id, f"上下文状态未更新（{exc}）")
        return {"injectSteps": []}


def handle_session_start(payload: Dict[str, Any]) -> Dict[str, Any]:
    return handle_context_check(payload, event_name="SessionStart")


def handle_pre_invocation(payload: Dict[str, Any]) -> Dict[str, Any]:
    return handle_context_check(payload, event_name="PreInvocation")


# ⑥ Codex 与 Claude Code 恢复协议适配

def handle_codex_session_start(
    payload: Dict[str, Any],
    state_dir_str: str = "",
    log_path_str: str = "",
) -> Dict[str, Any]:
    """
    处理 Codex SessionStart (compact / resume) 事件：
    提取同 ID 主会话 rollout 原话并落盘至 user-words/<session_id>.md，返回 hookSpecificOutput.additionalContext。
    """
    codex_home_env = os.environ.get("CODEX_HOME", "").strip()
    default_codex_home = Path(codex_home_env).expanduser() if codex_home_env else (Path.home() / ".codex")

    state_dir = Path(os.path.expanduser(state_dir_str)) if state_dir_str else (default_codex_home / "hooks" / "state" / "session-context")
    user_words_dir = (state_dir.parent / "user-words") if state_dir.name == "session-context" else (state_dir / "user-words")
    log_path = Path(os.path.expanduser(log_path_str)) if log_path_str else (default_codex_home / "hooks" / "logs" / "session-context.jsonl")
    user_words.sweep_stale_lock_files(state_dir.parent if state_dir.name == "session-context" else state_dir, HOOKS_DIR)

    source = str(payload.get("source") or "unknown").strip()
    session_id, transcript, identity_error, skip = user_words.codex_hook_session(payload)
    if skip:
        return {}
    cwd_str = str(payload.get("cwd") or os.getcwd())

    _cx_m, _cx_e = _extract_runtime_model_and_effort("codex", payload)
    codex_policy = _resolve_loader_policy("codex", _cx_m, _cx_e)
    result = user_words.refresh_codex_session_archive(
        str(transcript) if transcript else "", user_words_dir, session_id, identity_error=identity_error, policy=codex_policy,
    )

    saved = result.get("saved") or {}
    complete = bool(result.get("complete"))
    count = saved.get("count")
    words = result.get("words") or []

    lines: List[str] = ["【系统原话回溯】", f"恢复来源：{source}",
                        "提取状态：成功（已合并可见的同 ID 主会话记录）" if complete else "提取状态：存在缺口"]
    if saved.get("available"):
        lines.append(f"本会话用户原话文件：{saved['file']}")
        if isinstance(count, int) and count > 0:
            scope = f"第 1～{count} 条"
        elif count == 0:
            scope = "0 条"
        else:
            scope = "尚未核实"
        lines.append(f"已保存记录范围：{scope}。最后一条时间：{saved.get('last_time') or '未记录'}。")
        if saved.get("preserved"):
            lines.append("文件状态：保留上次已验证文档，本次因读取缺口未覆盖")
        elif saved.get("verified"):
            lines.append("文件状态：归档正文哈希与清单一致，本次可见记录已归档")
        elif saved.get("legacy_manifest"):
            lines.append("文件状态：旧版归档正文哈希与清单一致，完整性未验证")
        elif saved.get("manifest_verified"):
            lines.append("文件状态：归档正文哈希与清单一致，记录范围存在缺口")
        else:
            lines.append("文件状态：正文可读，完整性未验证")
        sources = saved.get("sources") or []
        if sources:
            lines.append(f"已保存记录来源：{len(sources)} 段。校验清单：{result['meta_path']}")
    else:
        lines.append("本会话用户原话文件：尚无已确认可读的归档文档")
        lines.append("已保存记录范围：尚未核实")

    codex_cap = int(codex_policy["inline_total_chars"])
    source_paths = result["segments"] or [item["path"] for item in saved.get("sources") or []]
    full_context = build_compaction_system_message(
        words, Path(saved["file"]) if saved.get("available") else None, f"Codex {source}",
        header="\n".join(lines), issues=result.get("issues"), source_paths=source_paths,
        subagent_returns=result.get("subagent_returns"), client="codex", policy=codex_policy,
    )
    if len(full_context) > codex_cap:
        suffix = "\n（内联内容已截断，请查阅原话档案。）"
        full_context = _close_unbalanced_fence(full_context, codex_cap - len(suffix)) + suffix
    blocks = _workflow_recovery_blocks(session_id, state_dir)
    if blocks:
        full_context += "\n\n---\n\n" + "\n\n---\n\n".join(blocks)

    _append_codex_log(
        log_path,
        {
            "ts": get_current_iso_time(),
            "event": "SessionStart",
            "session_id": session_id or "unknown",
            "cwd": cwd_str,
            "source": source,
            "pid": os.getpid(),
            "client": "codex",
            "outcome": "saved" if complete else "gap",
            "saved": saved,
            "issues": result.get("issues") or [],
        },
    )

    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": full_context,
        }
    }


def handle_claude_session_start(
    payload: Dict[str, Any], state_dir_str: str = "", log_path_str: str = "",
) -> Dict[str, Any]:
    """Claude SessionStart：恢复原话并按宿主协议注入有界上下文。"""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
    state_dir = Path(state_dir_str).expanduser() if state_dir_str else home / "hooks" / "state" / "session-context"
    user_words.sweep_stale_lock_files(state_dir.parent if state_dir.name == "session-context" else state_dir, HOOKS_DIR)
    _cl_m, _cl_e = _extract_runtime_model_and_effort("claude", payload)
    claude_policy = _resolve_loader_policy("claude", _cl_m, _cl_e)
    result = user_words.archive_claude_session_words(payload, state_dir, policy=claude_policy)
    if result.get("skipped"):
        return {}
    saved = result["saved"]
    source = str(payload.get("source") or "unknown").strip()
    count = saved.get("count")
    scope = f"第 1～{count} 条" if isinstance(count, int) and count > 0 else "0 条" if count == 0 else "尚未核实"
    lines = ["【系统原话回溯】", f"恢复来源：Claude {source}",
             "提取状态：成功（本次可见主轨迹中的原话已归档）" if result["complete"] else "提取状态：存在缺口",
             f"本会话用户原话文件：{saved['file']}" if saved.get("available") else "本会话用户原话文件：尚无已确认可读的归档文档",
             f"已保存记录范围：{scope}。最后一条时间：{saved.get('last_time') or '未记录'}。"]
    if saved.get("preserved"):
        lines.append("文件状态：保留上次已验证文档，本次因读取缺口未覆盖")
    elif saved.get("manifest_verified"):
        lines.append("文件状态：归档正文 SHA-256 与校验清单一致" + ("，本次可见记录已归档" if saved.get("verified") else "，记录范围存在缺口"))
    else:
        lines.append("文件状态：归档完整性未验证")
    if saved.get("available"):
        lines.append(f"校验清单：{result['meta_path']}")
    claude_cap = int(claude_policy["inline_total_chars"])
    full_context = build_compaction_system_message(
        result["words"], Path(saved["file"]) if saved.get("available") else None, f"Claude {source}",
        header="\n".join(lines), issues=result["issues"], source_paths=result["segments"],
        subagent_returns=result.get("subagent_returns"), client="claude", policy=claude_policy,
    )
    # 路径、来源和时间戳也可能很长，最终输出仍须遵守 SessionStart 字符预算。
    if len(full_context) > claude_cap:
        suffix = "\n（内联内容已截断，请查阅原话档案。）"
        full_context = _close_unbalanced_fence(full_context, claude_cap - len(suffix)) + suffix
    blocks = _workflow_recovery_blocks(str(payload.get("session_id") or ""), state_dir)
    if blocks:
        full_context += "\n\n---\n\n" + "\n\n---\n\n".join(blocks)
    if log_path_str:
        user_words.append_jsonl_log(Path(log_path_str).expanduser(), {
            "ts": get_current_iso_time(), "event": "SessionStart", "client": "claude",
            "session_id": str(payload.get("session_id") or "unknown"), "source": source,
            "cwd": str(payload.get("cwd") or ""), "outcome": "saved" if result["complete"] else "gap",
            "saved": saved, "issues": result["issues"],
        })
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": full_context}}


# ⑦ 命令行主入口

def main() -> None:
    with install_hook_self_guard(HOOKS_DIR):
        _main_inner()


def _main_inner() -> None:
    parser = argparse.ArgumentParser(description="Antigravity, Codex & Claude Code Compaction Context Loader Hook")
    parser.add_argument("--client", type=str, default="antigravity", choices=("antigravity", "codex", "claude"), help="Host client type")
    parser.add_argument("--event", type=str, default="", help="Hook event name: SessionStart or PreInvocation")
    parser.add_argument("--state-dir", type=str, default="", help="Optional state directory override (used by Codex and Claude hooks)")
    parser.add_argument("--log", type=str, default="", help="Optional JSONL log file path override (used by Codex and Claude hooks)")
    args, _ = parser.parse_known_args()

    payload: Dict[str, Any] = {}
    try:
        payload = user_words.read_hook_payload()
    except (OSError, ValueError) as e:
        if args.client in ("codex", "claude"):
            client_label = "Codex" if args.client == "codex" else "Claude"
            sys.stderr.write(f"{client_label} session_context_loader JSON parse error: {e}\n")
            sys.exit(2)
        log_event(args.event or "Unknown", "unknown", f"Failed to parse stdin JSON: {e}")

    if args.client in ("codex", "claude"):
        try:
            handler = handle_claude_session_start if args.client == "claude" else handle_codex_session_start
            response = handler(payload, state_dir_str=args.state_dir, log_path_str=args.log)
        except Exception as e:
            err_cap = int(_resolve_loader_policy(args.client)["inline_total_chars"])
            err_text = f"恢复来源：{'Claude ' if args.client == 'claude' else 'Codex '}{payload.get('source', 'unknown')}\n提取状态：异常（{e}）"
            response = {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": err_text[:err_cap],
                }
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return

    event_name = args.event or str(payload.get("hookEventName") or payload.get("eventName") or "PreInvocation")

    try:
        if event_name in ("SessionStart", "PreInvocation"):
            response = handle_context_check(payload, event_name=event_name)
        else:
            response = {}
    except Exception as e:
        stack_str = traceback.format_exc()
        log_event(event_name, str(payload.get("conversationId", "unknown")), f"Unhandled exception: {e}\n{stack_str}")
        response = {"injectSteps": []} if event_name in ("SessionStart", "PreInvocation") else {}

    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
