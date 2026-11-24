"""
test_lifecycle_offline.py - 上下文压缩原话回溯引擎离线单元测试套件

覆盖验证：
1. 新会话与常规未压缩多轮对话：SessionStart 与 PreInvocation 零注入、零生成归档；
2. 会话内自动压缩或手动 /compact 写入 CHECKPOINT 后：下一轮 PreInvocation 精确触发 1 次提取落盘与注入；
3. 压缩后第 2、3 轮日常对话：检查点步号幂等去重，零重复注入；
4. 二次压缩（新 CHECKPOINT 步号递增）：再次精确触发 1 次；
5. 冷重载 SessionStart 与 PreInvocation 协同幂等（绝不双倍注入）；
6. user_words 边界：嵌套 </USER_REQUEST> 不截断、O(N+M) 截断补全、ask_question 选项框回答提取；
7. 超长历史原话字符预算保护（保留首轮总纲与最近定论，中间省略并指向归档文件）；
8. 子代理会话自动豁免主会话注入，且 PLUGIN_DATA 环境变量隔离生效。
"""

import concurrent.futures
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
def _resolve_hooks_dir(raw_env_val=None) -> Path:
    default_base = REPO_ROOT / "template" / "user-guard"
    raw_installed = (
        os.environ.get("USER_GUARD_INSTALLED_DIR", "")
        if raw_env_val is None
        else str(raw_env_val)
    ).strip()
    if raw_installed:
        cand = Path(raw_installed).expanduser()
        if (cand / "hooks").is_dir():
            return (cand / "hooks").resolve()
        if cand.is_dir() and (cand / "user_words.py").is_file():
            return cand.resolve()
    return (default_base / "hooks").resolve()


_ISOLATED_STRIP_EXACT = frozenset({
    "PLUGIN_DATA",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "PYTHONPATH",
})
_ISOLATED_STRIP_PREFIXES = (
    "AGY_",
    "ANTIGRAVITY_",
    "CODEX_",
    "CLAUDE",
    "ANTHROPIC_",
    "DSH_",
    "CAE_",
    "GIT_",
    "XDG_",
)


def _scrub_host_env(**overrides: str) -> dict:
    cleaned = {
        k: v
        for k, v in os.environ.items()
        if k not in _ISOLATED_STRIP_EXACT
        and not k.startswith(_ISOLATED_STRIP_PREFIXES)
        and not (k.startswith("USER_GUARD_") and k != "USER_GUARD_INSTALLED_DIR")
    }
    cleaned.update(overrides)
    return cleaned


HOOKS_DIR = _resolve_hooks_dir()
sys.path.insert(0, str(HOOKS_DIR))

import handoff_gate  # noqa: E402
import reply_repeat_guard  # noqa: E402
import session_context_loader  # noqa: E402
import status_guard  # noqa: E402
import user_words  # noqa: E402


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _new_temp_dir(prefix: str = "lifecycle-test-") -> tempfile.TemporaryDirectory:
    try:
        tmp = tempfile.TemporaryDirectory(prefix=prefix)
        probe = Path(tmp.name) / ".probe.tmp"
        target = Path(tmp.name) / ".probe.ok"
        probe.write_bytes(b"ok")
        os.replace(probe, target)
        target.unlink()
        return tmp
    except OSError:
        fallback = REPO_ROOT / ".pytest_cache" / "lifecycle_sandbox"
        fallback.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix=prefix, dir=fallback)


class TestLifecycleCompactionEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = _new_temp_dir()
        sandbox_root = Path(self.tmp_obj.name).resolve()
        self.tmp_root = sandbox_root / "workspace"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.fake_home = sandbox_root / "home"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.plugin_data = self.tmp_root / "plugin_data"
        clean_env = _scrub_host_env(
            HOME=str(self.fake_home),
            USERPROFILE=str(self.fake_home),
            PLUGIN_DATA=str(self.plugin_data),
        )
        self._env_patch = mock.patch.dict(os.environ, clean_env, clear=True)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self._home_patch = mock.patch.object(Path, "home", return_value=self.fake_home)
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def tearDown(self) -> None:
        self.tmp_obj.cleanup()

    def test_01_normal_session_zero_injection_across_turns(self) -> None:
        """新会话启动及多轮日常未压缩对话，绝不触发原话回读注入"""
        conv_id = "conv-normal-001"
        tp = self.tmp_root / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"

        # 1. 空会话启动 SessionStart
        r0 = session_context_loader.handle_session_start({"conversationId": conv_id, "transcriptPath": str(tp)})
        self.assertEqual(r0.get("injectSteps"), [])

        # 2. 连续 3 轮普通对话 PreInvocation（无 CHECKPOINT）
        for turn in range(3):
            _append_jsonl(
                tp,
                {
                    "step_index": turn * 2,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": f"<USER_REQUEST>\n日常开发指令第 {turn + 1} 轮\n</USER_REQUEST>",
                },
            )
            res = session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(tp)})
            self.assertEqual(res.get("injectSteps"), [])
            _append_jsonl(
                tp,
                {"step_index": turn * 2 + 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "收到并已完成。"},
            )

        archive_md = self.plugin_data / "state" / f"{conv_id}_user_words.md"
        self.assertFalse(archive_md.exists(), "未压缩日常会话不应执行全盘提取与归档写入")

    def test_02_compaction_triggers_exactly_once_and_second_compaction_retriggers(self) -> None:
        """发生自动压缩或 /compact 后首轮精确注入 1 次，后续日常轮 0 注入，二次压缩再触发 1 次"""
        conv_id = "conv-compact-002"
        tp = self.tmp_root / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"

        _append_jsonl(
            tp,
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": "2026-11-12T01:00:00Z",
                "content": "<USER_REQUEST>\n实现高阶守恒律数值求解器（如 FVM / DG / DGSEM / WENO），严禁改动网格接口。\n</USER_REQUEST>",
            },
        )
        _append_jsonl(tp, {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "好的。"})

        # 第一轮未压缩，0 注入
        self.assertEqual(
            session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(tp)})["injectSteps"],
            [],
        )

        # 触发第一次上下文压缩 CHECKPOINT (step 10) + 新一轮用户提问 (step 11)
        _append_jsonl(tp, {"step_index": 10, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Resuming from a compaction"})
        _append_jsonl(
            tp,
            {
                "step_index": 11,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": "2026-11-12T02:00:00Z",
                "content": "<USER_REQUEST>\n压缩后继续核对通量边界条件\n</USER_REQUEST>",
            },
        )

        # 压缩后首次 PreInvocation -> 必须精确触发注入！
        r_after_cp1 = session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(tp)})
        steps1 = r_after_cp1.get("injectSteps", [])
        self.assertEqual(len(steps1), 1)
        msg1 = steps1[0]["systemMessage"]["systemMessage"]
        self.assertIn("CHECKPOINT step #10", msg1)
        self.assertIn("严禁改动网格接口", msg1)
        self.assertIn("压缩后继续核对通量边界条件", msg1)

        archive_md = self.plugin_data / "state" / f"{conv_id}_user_words.md"
        self.assertTrue(archive_md.exists())
        self.assertIn(str(archive_md), msg1)

        # 压缩后的第 2 轮、第 3 轮日常对话 -> 必须严格 0 重复注入！
        _append_jsonl(
            tp,
            {"step_index": 13, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "<USER_REQUEST>\n再跑一下收敛阶\n</USER_REQUEST>"},
        )
        r_turn2 = session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(tp)})
        self.assertEqual(r_turn2.get("injectSteps"), [])

        # 即使此时窗口重启触发 SessionStart，因 CHECKPOINT #10 已注入过，依然幂等 0 注入！
        r_reload = session_context_loader.handle_session_start({"conversationId": conv_id, "transcriptPath": str(tp)})
        self.assertEqual(r_reload.get("injectSteps"), [])

        # 发生第二次上下文压缩 CHECKPOINT (step 30 > 10) -> 必须再次精确触发 1 次！
        _append_jsonl(tp, {"step_index": 30, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Second compaction"})
        _append_jsonl(
            tp,
            {"step_index": 31, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "<USER_REQUEST>\n出最终验收报告\n</USER_REQUEST>"},
        )
        r_after_cp2 = session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(tp)})
        steps2 = r_after_cp2.get("injectSteps", [])
        self.assertEqual(len(steps2), 1)
        msg2 = steps2[0]["systemMessage"]["systemMessage"]
        self.assertIn("CHECKPOINT step #30", msg2)
        self.assertIn("出最终验收报告", msg2)
        self.assertIn("再跑一下收敛阶", msg2)

    def test_03_nested_tags_truncated_full_lookup_and_ask_question(self) -> None:
        """验证嵌套 </USER_REQUEST> 不截断、O(N+M) 截断补全及 ask_question 选项回答提取"""
        log_dir = self.tmp_root / "logs_parse"
        tp = log_dir / "transcript.jsonl"
        full_tp = log_dir / "transcript_full.jsonl"

        # 1. 含内部 </USER_REQUEST> 代码片段的用户原话
        nested_prompt = (
            "<USER_REQUEST>\n"
            "请检查下面这段正则示例：\n"
            "`<USER_REQUEST>示例文本</USER_REQUEST>`\n"
            "真正的结尾要求在这里：保留高精度浮点输出！\n"
            "</USER_REQUEST>\n"
            "<ADDITIONAL_METADATA>\nThe current local time is: 2026-11-12.\n</ADDITIONAL_METADATA>"
        )
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": nested_prompt})

        # 2. 被截断的超长用户指令（需从 transcript_full.jsonl 补全）
        full_long_body = "【核心数值边界约束】" + ("高阶通量重构细节_" * 400) + "【末尾锚点_不可丢失】"
        _append_jsonl(
            tp,
            {
                "step_index": 2,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>\n【核心数值...<truncated>",
                "truncated_fields": ["content"],
            },
        )
        _append_jsonl(
            full_tp,
            {
                "step_index": 2,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": f"<USER_REQUEST>\n{full_long_body}\n</USER_REQUEST>",
            },
        )

        # 3. 使用宿主调用标识关联选项结果，不能把中间的其它工具输出当成回答
        _append_jsonl(
            tp,
            {
                "step_index": 5,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "ask_question",
                        "id": "question-5",
                        "args": {"questions": [{"question": "选择时间推进格式", "options": ["SSP-RK3", "ADER"]}]},
                    }
                ],
            },
        )
        _append_jsonl(
            tp,
            {
                "step_index": 6,
                "source": "MODEL",
                "type": "GENERIC",
                "content": "不相关输出",
                "tool_call_id": "other-6",
            },
        )
        _append_jsonl(
            tp,
            {
                "step_index": 7,
                "source": "MODEL",
                "type": "GENERIC",
                "tool_call_id": "question-5",
                "content": "Created At: 2026-11-12T06:00:00+08:00\nCompleted At: 2026-11-12T06:00:05+08:00\nUser selected: SSP-RK3",
            },
        )

        words = user_words.extract_user_words(str(tp))
        self.assertEqual(len(words), 3)
        self.assertIn("真正的结尾要求在这里：保留高精度浮点输出！", words[0]["content"])
        self.assertIn("`<USER_REQUEST>示例文本</USER_REQUEST>`", words[0]["content"])
        self.assertIn("【末尾锚点_不可丢失】", words[1]["content"])
        self.assertEqual(words[2]["kind"], "ask_question_answer")
        self.assertIn("选择时间推进格式", words[2]["content"])
        self.assertIn("User selected: SSP-RK3", words[2]["content"])

    def test_04_budget_cap_preserves_first_and_latest_turns(self) -> None:
        """超长多轮历史原话触发注入时，严格遵守字符上限并保留首轮总纲与最新定论"""
        words = []
        for i in range(1, 21):
            words.append(
                {
                    "turn": i,
                    "step_index": i * 2,
                    "created_at": "2026-11-12T00:00:00Z",
                    "kind": "user_input",
                    "content": f"第 {i} 轮指令核心头_" + ("长篇技术参数说明_" * 200) + f"_第 {i} 轮尾部定论",
                }
            )
        archive_path = self.plugin_data / "state" / "budget_test_user_words.md"
        msg = session_context_loader.build_compaction_system_message(words, archive_path, "CHECKPOINT step #99")

        self.assertLessEqual(len(msg), session_context_loader.MAX_INLINE_TOTAL_CHARS + 300)
        self.assertIn("第 1 轮指令核心头_", msg)
        self.assertIn("第 20 轮尾部定论", msg)
        self.assertIn("因上下文预算省略内联", msg)
        self.assertIn(str(archive_path), msg)

    def test_05_subagent_session_skips_main_injection(self) -> None:
        """无 USER_INPUT 的纯子代理会话在自动压缩时不注入主会话回溯消息"""
        conv_id = "subagent-readonly-005"
        tp = self.tmp_root / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"

        _append_jsonl(
            tp,
            {
                "step_index": 0,
                "source": "SYSTEM_SDK",
                "type": "SYSTEM_MESSAGE",
                "content": "<SYSTEM_MESSAGE>\n[Message] sender=parent-conv content=请只读审查 status_guard.py 第 1~100 行\n</SYSTEM_MESSAGE>",
            },
        )
        _append_jsonl(tp, {"step_index": 20, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Compacted"})

        res = session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(tp)})
        self.assertEqual(res.get("injectSteps"), [])

        # 但直接调用 extract_user_words(include_subagent_msgs=True) 可正确提取父代理任务书
        sub_words = user_words.extract_user_words(str(tp), include_subagent_msgs=True)
        self.assertEqual(len(sub_words), 1)
        self.assertEqual(sub_words[0]["kind"], "subagent_message")
        self.assertIn("请只读审查 status_guard.py", sub_words[0]["content"])

    def test_06_codex_multisegment_rollout_extraction_and_session_start_hook(self) -> None:
        """验证 Codex 模式跨归档与活动分段合并、provenance 过滤、选项框识别及缺口保底"""
        sid = "01a1c238-test-codex-session"
        codex_root = self.tmp_root / ".codex"
        seg1 = codex_root / "archived_sessions" / f"rollout-2026-11-09T01-00-00-{sid}.jsonl"
        seg2 = codex_root / "sessions" / "2026" / "11" / "10" / f"rollout-2026-11-10T05-00-00-{sid}_cont.jsonl"
        seg_sub = codex_root / "sessions" / "2026" / "11" / "10" / f"rollout-2026-11-10T06-00-00-{sid}_sub.jsonl"

        _append_jsonl(seg1, {"type": "session_meta", "payload": {"id": sid, "cwd": "/tmp/proj", "source": "cli"}})
        # 系统自动注入的 AGENTS.md 与环境块应被过滤
        _append_jsonl(
            seg1,
            {
                "type": "response_item",
                "ordinal": 1,
                "timestamp": "2026-11-09T01:00:01Z",
                "payload": {
                    "id": "sys-msg-1",
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /tmp/proj"}],
                    "internal_chat_message_metadata_passthrough": {
                        "content_item_metadata": [{"provenance": {"type": "agents_md"}}]
                    },
                },
            },
        )
        # 人类真实输入（即使正文引用了 # AGENTS.md 字面量，因 provenance=user 必须完整保留）
        shared_first_msg = {
            "type": "response_item",
            "ordinal": 2,
            "timestamp": "2026-11-09T01:01:00Z",
            "payload": {
                "id": "usr-msg-1",
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "首轮立项：检查 # AGENTS.md instructions 与 DGSEM 通量守恒"}],
                "internal_chat_message_metadata_passthrough": {
                    "content_item_metadata": [{"provenance": {"type": "user"}}]
                },
            },
        }
        _append_jsonl(seg1, shared_first_msg)

        # 第二段续跑包含第一段镜像记录（应按 id 去重）+ 两次人类打字“继续”（不同 id 均须保留）+ 选项框回答
        _append_jsonl(seg2, {"type": "session_meta", "payload": {"id": sid, "cwd": "/tmp/proj", "source": "desktop"}})
        _append_jsonl(seg2, shared_first_msg)
        _append_jsonl(
            seg2,
            {
                "type": "response_item",
                "ordinal": 3,
                "timestamp": "2026-11-10T05:01:00Z",
                "payload": {
                    "id": "usr-msg-2", "type": "message", "role": "user", "content": "<environment_context>\nauto\n</environment_context>",
                    "internal_chat_message_metadata_passthrough": {
                        "content_item_metadata": [{"provenance": {"type": "harness"}}],
                    },
                },
            },
        )
        _append_jsonl(
            seg2,
            {
                "type": "response_item",
                "ordinal": 4,
                "timestamp": "2026-11-10T05:02:00Z",
                "payload": {
                    "id": "usr-msg-3", "type": "message", "role": "user", "content": "继续",
                    "internal_chat_message_metadata_passthrough": {
                        "content_item_metadata": [{"provenance": {"type": "user"}}],
                    },
                },
            },
        )
        _append_jsonl(
            seg2,
            {
                "type": "response_item",
                "ordinal": 5,
                "timestamp": "2026-11-10T05:03:00Z",
                "payload": {
                    "id": "usr-msg-4", "type": "message", "role": "user", "content": "继续",
                    "internal_chat_message_metadata_passthrough": {
                        "content_item_metadata": [{"provenance": {"type": "user"}}],
                    },
                },
            },
        )
        _append_jsonl(
            seg2,
            {
                "type": "response_item",
                "ordinal": 6,
                "timestamp": "2026-11-10T05:04:00Z",
                "payload": {
                    "id": "usr-msg-5",
                    "type": "message",
                    "role": "user",
                    "content": "<send_user_message_question_reply>采用熵稳定通量</send_user_message_question_reply>",
                    "internal_chat_message_metadata_passthrough": {
                        "content_item_metadata": [{"provenance": {"type": "user"}}],
                    },
                },
            },
        )

        # 子代理分段不应混入主会话
        _append_jsonl(seg_sub, {"type": "session_meta", "payload": {"id": sid, "cwd": "/tmp/proj", "source": "subagent"}})
        _append_jsonl(
            seg_sub,
            {
                "type": "response_item",
                "ordinal": 1,
                "timestamp": "2026-11-10T06:00:01Z",
                "payload": {"id": "sub-1", "type": "message", "role": "user", "content": "子代理内部提示词"},
            },
        )

        state_dir = codex_root / "hooks" / "state" / "session-context"
        log_file = codex_root / "hooks" / "logs" / "session-context.jsonl"
        out = session_context_loader.handle_codex_session_start(
            {
                "session_id": sid,
                "transcript_path": str(seg2),
                "cwd": "/tmp/proj",
                "hook_event_name": "SessionStart",
                "source": "compact",
            },
            state_dir_str=str(state_dir),
            log_path_str=str(log_file),
        )
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "SessionStart")
        ctx = spec["additionalContext"]
        self.assertIn("提取状态：成功", ctx)
        self.assertIn("第 1～4 条", ctx)
        self.assertIn("DGSEM 通量守恒", ctx)
        self.assertIn("采用熵稳定通量", ctx)
        self.assertNotIn("子代理内部提示词", ctx)

        md_file = codex_root / "hooks" / "state" / "user-words" / f"{sid}.md"
        self.assertTrue(md_file.is_file())
        md_text = md_file.read_text(encoding="utf-8")
        self.assertEqual(md_text.count("\n继续\n"), 2, "两次真实打字『继续』均须保留")
        self.assertTrue(log_file.is_file())
        self.assertNotIn("DGSEM 通量守恒", log_file.read_text(encoding="utf-8"), "运行日志仅记元数据，不落盘对话正文")

        # 当 transcript_path 缺失触发缺口时，必须保留上次已验证的归档文档
        gap_out = session_context_loader.handle_codex_session_start(
            {"session_id": sid, "transcript_path": "", "source": "resume"},
            state_dir_str=str(state_dir),
            log_path_str=str(log_file),
        )
        gap_ctx = gap_out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("提取状态：存在缺口", gap_ctx)
        self.assertIn("保留上次已验证文档", gap_ctx)
        self.assertEqual(md_file.read_text(encoding="utf-8"), md_text)

    def test_codex_long_gap_preserves_provenance_and_reports_unverified_manifest(self) -> None:
        sid = "codex-long-gap"
        transcript = self.tmp_root / "rollout-long.jsonl"
        state_dir = self.tmp_root / "session-context"
        log = self.tmp_root / "hook-log.jsonl"
        _append_jsonl(transcript, {"type": "session_meta", "payload": {"id": sid, "source": "vscode"}})

        def add_user(index, body, origin="user"):
            metadata = {"content_item_metadata": [{"provenance": {"type": origin}}]} if origin else {}
            _append_jsonl(transcript, {"type": "response_item", "ordinal": index, "payload": {
                "type": "message", "role": "user", "id": f"user-{index}", "content": body,
                "internal_chat_message_metadata_passthrough": metadata,
            }})

        add_user(1, "# AGENTS.md instructions\n真实首轮立项")
        for index in range(2, 23):
            add_user(index, "中间工程约束。" * 250)
        add_user(23, "最新裁定保留。")
        payload = {"session_id": sid, "transcript_path": str(transcript), "source": "compact"}

        def render():
            return session_context_loader.handle_codex_session_start(
                payload, str(state_dir), str(log),
            )["hookSpecificOutput"]["additionalContext"]

        self.assertIn("提取状态：成功", render())
        md = self.tmp_root / "user-words" / f"{sid}.md"
        md.write_text("可读，但和清单错配\n", encoding="utf-8")
        add_user(24, "<environment_context>\n字面量输入候选\n</environment_context>", origin=None)
        with transcript.open("ab") as stream:
            stream.write(b'{"type":"response_item","payload":')
        context = render()
        self.assertLessEqual(len(context), session_context_loader.MAX_INLINE_TOTAL_CHARS)
        self.assertIn("提取状态：存在缺口", context)
        self.assertIn("正文可读，完整性未验证", context)
        self.assertIn("原始轨迹：" + str(transcript), context)
        self.assertIn("缺口信息", context)
        self.assertIn("真实首轮立项", context)
        self.assertIn("最新裁定保留", context)
        self.assertIn("字面量输入候选", context)
        self.assertIn("用户角色，来源未核实", context)
        self.assertNotIn("保留上次已验证文档", context)
        self.assertNotIn("完整归档", context)

    def test_codex_archive_publication_failure_is_detectable_and_recoverable(self) -> None:
        sid = "codex-publication"
        transcript = self.tmp_root / "rollout.jsonl"
        user_dir = self.tmp_root / "user-words"
        _append_jsonl(transcript, {"type": "session_meta", "payload": {"id": sid, "source": "cli"}})
        first = user_words.refresh_codex_session_archive(str(transcript), user_dir, sid)
        self.assertTrue(first["saved"]["verified"])
        _append_jsonl(transcript, {"type": "response_item", "payload": {"type": "message", "role": "user",
                      "content": "并发归档记录", "internal_chat_message_metadata_passthrough": {
                          "content_item_metadata": [{"provenance": {"type": "user"}}],
                      }}})
        with mock.patch.object(user_words, "atomic_write_json", side_effect=OSError("写清单被中断")):
            interrupted = user_words.refresh_codex_session_archive(str(transcript), user_dir, sid)
        self.assertFalse(interrupted["complete"])
        self.assertTrue(interrupted["saved"]["available"])
        self.assertFalse(interrupted["saved"]["verified"])
        self.assertFalse(interrupted["saved"]["preserved"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: user_words.refresh_codex_session_archive(str(transcript), user_dir, sid), range(8)))
        self.assertTrue(all(item["complete"] and item["saved"]["verified"] for item in results))
        md = user_dir / f"{sid}.md"
        manifest = user_dir / f"{sid}.json"
        published = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(published["sha256"], hashlib.sha256(md.read_bytes()).hexdigest())
        manifest.write_text('{"publication":{"sha256":"' + published["sha256"] + '"}}', encoding="utf-8")
        gap = user_words.refresh_codex_session_archive("", user_dir, sid)
        self.assertTrue(gap["saved"]["available"])
        self.assertFalse(gap["saved"]["verified"])
        self.assertFalse(gap["saved"]["preserved"])

    def test_codex_legacy_manifest_migration_requires_matching_body_and_source_coverage(self) -> None:
        for scenario in ("complete", "missing_segment", "bad_body_hash"):
            with self.subTest(scenario=scenario):
                sid = f"codex-v1-{scenario}"
                older = self.tmp_root / ".codex" / "archived_sessions" / f"rollout-2026-01-01-{sid}.jsonl"
                active = self.tmp_root / ".codex" / "sessions/2026/01/02" / f"rollout-2026-01-02-{sid}.jsonl"
                user_dir = self.tmp_root / "user-words"
                user_dir.mkdir(exist_ok=True)
                md = user_dir / f"{sid}.md"
                manifest = user_dir / f"{sid}.json"
                for segment in (older, active):
                    _append_jsonl(segment, {"type": "session_meta", "payload": {"id": sid, "source": "cli"}})

                def user_message(segment, key, body):
                    _append_jsonl(segment, {"type": "response_item", "payload": {
                        "type": "message", "role": "user", "id": key, "content": body,
                        "internal_chat_message_metadata_passthrough": {
                            "content_item_metadata": [{"provenance": {"type": "user"}}],
                        },
                    }})

                user_message(older, "old", "旧档案中的第一条原话")
                parsed = user_words.extract_codex_session_words(str(active), sid)
                v1_body = user_words.format_user_words_markdown(sid, parsed["words"]).replace(
                    "# 会话用户原话归档", "# 会话用户原话全量留痕档案", 1,
                )
                user_words.atomic_write_text(md, v1_body)
                user_words.atomic_write_json(manifest, {
                    "format": "user-guard-codex-v1", "id": sid, "file": str(md), "count": 1,
                    "last_time": "", "sources": parsed["sources"],
                    "sha256": "0" * 64 if scenario == "bad_body_hash" else hashlib.sha256(md.read_bytes()).hexdigest(),
                })
                old_md, old_manifest = md.read_bytes(), manifest.read_bytes()
                unavailable = user_words.refresh_codex_session_archive("", user_dir, sid)
                self.assertFalse(unavailable["saved"]["verified"])
                self.assertFalse(unavailable["saved"]["preserved"])
                if scenario != "bad_body_hash":
                    self.assertTrue(unavailable["saved"]["manifest_verified"])
                    self.assertTrue(unavailable["saved"]["legacy_manifest"])
                    self.assertEqual(unavailable["saved"]["sources"], parsed["sources"])
                user_message(active, "new", "升级期间新增的用户原话")
                if scenario == "missing_segment":
                    older.rename(older.with_suffix(".unavailable"))
                result = user_words.refresh_codex_session_archive(str(active), user_dir, sid)
                if scenario == "complete":
                    self.assertTrue(result["complete"])
                    self.assertTrue(result["saved"]["verified"])
                    self.assertEqual(result["saved"]["count"], 2)
                    self.assertIn("升级期间新增的用户原话", md.read_text(encoding="utf-8"))
                    self.assertEqual(json.loads(manifest.read_bytes())["format"], "user-guard-codex-v2")
                else:
                    self.assertFalse(result["complete"])
                    self.assertFalse(result["saved"]["verified"])
                    self.assertFalse(result["saved"]["preserved"])
                    self.assertEqual(md.read_bytes(), old_md)
                    self.assertEqual(manifest.read_bytes(), old_manifest)
                    if scenario == "missing_segment":
                        self.assertTrue(any(str(older) in issue for issue in result["issues"]))
                    else:
                        self.assertTrue(any("正文哈希与校验清单不匹配" in issue for issue in result["issues"]))

    def test_codex_interrupted_publication_cannot_hide_a_missing_segment(self) -> None:
        sid = "codex-interrupted-missing-segment"
        older = self.tmp_root / ".codex" / "archived_sessions" / f"rollout-2026-01-01-{sid}.jsonl"
        active = self.tmp_root / ".codex" / "sessions/2026/01/02" / f"rollout-2026-01-02-{sid}.jsonl"
        user_dir = self.tmp_root / "user-words"
        md = user_dir / f"{sid}.md"
        manifest = user_dir / f"{sid}.json"
        for segment in (older, active):
            _append_jsonl(segment, {"type": "session_meta", "payload": {"id": sid, "source": "cli"}})

        def user_message(segment, key, body):
            _append_jsonl(segment, {"type": "response_item", "payload": {
                "type": "message", "role": "user", "id": key, "content": body,
                "internal_chat_message_metadata_passthrough": {
                    "content_item_metadata": [{"provenance": {"type": "user"}}],
                },
            }})

        user_message(older, "older", "早期分段独有原话")
        self.assertTrue(user_words.refresh_codex_session_archive(str(active), user_dir, sid)["complete"])
        old_manifest = manifest.read_bytes()
        user_message(active, "new", "后续分段新增原话")
        with mock.patch.object(user_words, "atomic_write_json", side_effect=OSError("写清单被中断")):
            self.assertFalse(user_words.refresh_codex_session_archive(str(active), user_dir, sid)["complete"])
        pending_body = md.read_bytes()
        self.assertIn("早期分段独有原话", pending_body.decode())
        self.assertIn("后续分段新增原话", pending_body.decode())
        unavailable = older.with_suffix(".unavailable")
        older.rename(unavailable)
        failed = user_words.refresh_codex_session_archive(str(active), user_dir, sid)
        self.assertFalse(failed["complete"])
        self.assertFalse(failed["saved"]["verified"])
        self.assertFalse(failed["saved"]["preserved"])
        self.assertTrue(any(str(older) in issue for issue in failed["issues"]))
        self.assertEqual(md.read_bytes(), pending_body)
        self.assertEqual(manifest.read_bytes(), old_manifest)

        unavailable.rename(older)
        with mock.patch.object(user_words, "atomic_write_text", wraps=user_words.atomic_write_text) as writes:
            restored = user_words.refresh_codex_session_archive(str(active), user_dir, sid)
        self.assertTrue(restored["saved"]["verified"])
        self.assertEqual(md.read_bytes(), pending_body)
        self.assertEqual([call.args[0] for call in writes.call_args_list], [manifest])

    def test_codex_later_system_provenance_corrects_and_reports_conflicts(self) -> None:
        for first_origin, second_origin in ((None, "harness"), ("user", "agents_md"), ("harness", "user")):
            with self.subTest(first=first_origin, later=second_origin):
                sid = f"codex-source-{first_origin}-{second_origin}"
                older = self.tmp_root / ".codex" / "archived_sessions" / f"rollout-2026-01-01-{sid}.jsonl"
                active = self.tmp_root / ".codex" / "sessions/2026/01/02" / f"rollout-2026-01-02-{sid}.jsonl"
                user_dir = self.tmp_root / "user-words"
                for segment in (older, active):
                    _append_jsonl(segment, {"type": "session_meta", "payload": {"id": sid, "source": "cli"}})

                def message(segment, origin):
                    metadata = {"content_item_metadata": [{"provenance": {"type": origin}}]} if origin else {}
                    _append_jsonl(segment, {"type": "response_item", "payload": {
                        "type": "message", "role": "user", "id": "shared-input", "content": "来源待判定的内容",
                        "internal_chat_message_metadata_passthrough": metadata,
                    }})

                message(older, first_origin)
                user_words.refresh_codex_session_archive(str(active), user_dir, sid)
                message(active, second_origin)
                result = user_words.refresh_codex_session_archive(str(active), user_dir, sid)
                self.assertEqual(result["words"], [])
                self.assertEqual(result["complete"], first_origin is None)
                self.assertEqual(any("用户与系统来源冲突" in issue for issue in result["issues"]), first_origin is not None)
                self.assertTrue(result["saved"]["manifest_verified"])
                self.assertNotIn("来源待判定的内容", (user_dir / f"{sid}.md").read_text(encoding="utf-8"))

    def test_codex_attachment_and_cross_segment_provenance_upgrade(self) -> None:
        sid = "codex-attachment-history"
        first = self.tmp_root / ".codex" / "archived_sessions" / f"rollout-2026-01-01-{sid}.jsonl"
        second = self.tmp_root / ".codex" / "sessions/2026/01/02" / f"rollout-2026-01-02-{sid}.jsonl"
        for transcript in (first, second):
            _append_jsonl(transcript, {"type": "session_meta", "payload": {"id": sid, "source": "cli"}})
        attachment = {"type": "input_image", "file_id": "fixture-image"}
        _append_jsonl(first, {"type": "response_item", "payload": {
            "type": "message", "role": "user", "id": "image-1", "content": [attachment],
        }})
        _append_jsonl(second, {"type": "response_item", "payload": {
            "type": "message", "role": "user", "id": "image-1", "content": [attachment],
            "internal_chat_message_metadata_passthrough": {"content_item_metadata": [
                {"provenance": {"type": "user"}},
            ]},
        }})
        user_dir = self.tmp_root / "user-words"
        result = user_words.refresh_codex_session_archive(str(second), user_dir, sid)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["words"]), 1)
        self.assertTrue(result["words"][0]["provenance_verified"])
        self.assertEqual(result["words"][0]["source_path"], str(second))
        body = (user_dir / f"{sid}.md").read_text(encoding="utf-8")
        self.assertIn("图片附件", body)
        self.assertIn(str(second), body)
        self.assertNotIn("fixture-image", body)

        _append_jsonl(second, {"type": "response_item", "payload": {
            "type": "message", "role": "user", "id": "image-1",
            "content": [{"type": "input_image", "file_id": "another-image"}],
            "internal_chat_message_metadata_passthrough": {"content_item_metadata": [
                {"provenance": {"type": "user"}},
            ]},
        }})
        conflict = user_words.extract_codex_session_words(str(second), sid)
        self.assertFalse(conflict["complete"])
        self.assertEqual(len(conflict["words"]), 2)
        self.assertTrue(any("内容冲突" in issue for issue in conflict["issues"]))

    def test_codex_archive_updates_partial_history_and_preserves_missing_segment(self) -> None:
        sid = "codex-archive-coverage"
        older = self.tmp_root / ".codex" / "archived_sessions" / f"rollout-2026-01-01-{sid}.jsonl"
        active = self.tmp_root / ".codex" / "sessions/2026/01/02" / f"rollout-2026-01-02-{sid}.jsonl"
        user_dir = self.tmp_root / "user-words"
        archive = user_dir / f"{sid}.md"
        for segment in (older, active):
            _append_jsonl(segment, {"type": "session_meta", "payload": {"id": sid, "source": "cli"}})

        def user_message(segment, key, body, origin=None):
            metadata = {"content_item_metadata": [{"provenance": {"type": origin}}]} if origin else {}
            _append_jsonl(segment, {"type": "response_item", "payload": {
                "type": "message", "role": "user", "id": key, "content": body,
                "internal_chat_message_metadata_passthrough": metadata,
            }})

        user_message(older, "initial", "早期原话，初次缺少来源标记")
        self.assertFalse(user_words.refresh_codex_session_archive(str(active), user_dir, sid)["complete"])
        user_message(active, "later", "本轮新增且来源已核实", "user")
        partial = user_words.refresh_codex_session_archive(str(active), user_dir, sid)
        self.assertFalse(partial["complete"])
        self.assertTrue(partial["saved"]["manifest_verified"])
        self.assertIn("本轮新增且来源已核实", archive.read_text(encoding="utf-8"))
        user_message(active, "initial", "早期原话，初次缺少来源标记", "user")
        complete = user_words.refresh_codex_session_archive(str(active), user_dir, sid)
        self.assertTrue(complete["complete"])
        before = archive.read_bytes()
        older.rename(older.with_suffix(".unavailable"))
        gap = user_words.refresh_codex_session_archive(str(active), user_dir, sid)
        self.assertFalse(gap["complete"])
        self.assertTrue(gap["saved"]["preserved"])
        self.assertTrue(any(str(older) in issue for issue in gap["issues"]))
        self.assertEqual(archive.read_bytes(), before)

    def test_codex_gate_and_archive_use_independent_lock_namespaces(self) -> None:
        import handoff_gate

        sid = "codex-independent-locks"
        transcript = self.tmp_root / "rollout.jsonl"
        _append_jsonl(transcript, {"type": "session_meta", "payload": {"id": sid, "source": "cli"}})
        state = self.tmp_root / "state"
        with user_words.session_state_lock(state / "user-words", sid):
            response = handoff_gate.handle_codex_handoff(
                {"session_id": sid, "hook_event_name": "SessionStart", "source": "compact",
                 "transcript_path": str(transcript)}, state / "handoff",
            )
            self.assertIn("hookSpecificOutput", response)
        with user_words.session_state_lock(state / "handoff", sid):
            self.assertTrue(user_words.refresh_codex_session_archive(str(transcript), state / "user-words", sid)["complete"])

    def test_implicit_question_result_remains_unverified_and_full_log_repairs_pending_checkpoint(self) -> None:
        cid = "agy-archive-gap"
        transcript = self.tmp_root / "transcript.jsonl"
        full = self.tmp_root / "transcript_full.jsonl"
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": "<USER_REQUEST>\n正文待补",
                                  "truncated_fields": ["content"]})
        _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "step_index": 1, "tool_calls": [{"name": "ask_question"}]})
        _append_jsonl(transcript, {"type": "GENERIC", "step_index": 2, "content": "User selected: 未核对候选"})
        _append_jsonl(transcript, {"type": "CHECKPOINT", "step_index": 3})
        payload = {"conversationId": cid, "transcriptPath": str(transcript)}
        first = session_context_loader.handle_pre_invocation(payload)
        context = first["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("截断正文待补", context)
        self.assertIn("选项回答来源未核实", context)
        self.assertNotIn("未核对候选", context)
        for _ in range(4):
            self.assertEqual(session_context_loader.handle_pre_invocation(payload)["injectSteps"], [])
        _append_jsonl(full, {"type": "USER_INPUT", "step_index": 0, "content": "<USER_REQUEST>\n已补齐正文\n</USER_REQUEST>"})
        updated = session_context_loader.handle_pre_invocation(payload)["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("已补齐正文", updated)
        self.assertNotIn("截断正文待补", updated)
        self.assertIn("选项回答来源未核实", updated)
        archive = self.plugin_data / "state" / f"{cid}_user_words.md"
        self.assertIn("已补齐正文", archive.read_text(encoding="utf-8"))
        self.assertNotIn("未核对候选", archive.read_text(encoding="utf-8"))

    def test_agy_late_full_control_updates_latest_turn_and_goal(self) -> None:
        import handoff_gate

        cid = "agy-late-control"
        state_dir = self.plugin_data / "state"
        transcript = self.tmp_root / "transcript.jsonl"
        full = self.tmp_root / "transcript_full.jsonl"
        payload = {"conversationId": cid, "transcriptPath": str(transcript)}
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": "请整理交接并停机"})
        session_context_loader.handle_pre_invocation(payload)
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 1, "content": "先别交接",
                                  "truncated_fields": ["content"]})
        session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "drain")
        _append_jsonl(full, {"type": "USER_INPUT", "step_index": 1, "content": "/goal\n先别交接"})
        session_context_loader.handle_pre_invocation(payload)
        gate = handoff_gate.load_gate_state(state_dir, cid)
        self.assertEqual(gate["phase"], "overridden")
        self.assertTrue(gate["isGoalActive"])

        _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "step_index": 2,
                                  "content": "<!-- GOAL_CANCELLED -->", "truncated_fields": ["content"]})
        session_context_loader.handle_pre_invocation(payload)
        self.assertTrue(handoff_gate.load_gate_state(state_dir, cid)["isGoalActive"])
        _append_jsonl(full, {"type": "PLANNER_RESPONSE", "step_index": 2,
                             "content": "已收尾。\n<!-- GOAL_CANCELLED -->"})
        session_context_loader.handle_pre_invocation(payload)
        self.assertFalse(handoff_gate.load_gate_state(state_dir, cid)["isGoalActive"])

        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 3, "content": "/goal",
                                  "truncated_fields": ["content"]})
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 4, "content": "取消 /goal\n请立即交接"})
        session_context_loader.handle_pre_invocation(payload)
        _append_jsonl(full, {"type": "USER_INPUT", "step_index": 3, "content": "/goal\n先别交接"})
        session_context_loader.handle_pre_invocation(payload)
        gate = handoff_gate.load_gate_state(state_dir, cid)
        self.assertEqual(gate["phase"], "drain")
        self.assertFalse(gate["isGoalActive"])

    def test_agy_late_override_survives_newer_ordinary_user_message(self) -> None:
        import handoff_gate

        cid = "agy-late-override"
        state_dir = self.plugin_data / "state"
        transcript = self.tmp_root / "transcript.jsonl"
        full = self.tmp_root / "transcript_full.jsonl"
        payload = {"conversationId": cid, "transcriptPath": str(transcript)}
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": "请整理交接并停机"})
        session_context_loader.handle_pre_invocation(payload)
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 1, "content": "暂缓",
                                  "truncated_fields": ["content"]})
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 2, "content": "查看进度"})
        session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "drain")
        _append_jsonl(full, {"type": "USER_INPUT", "step_index": 1, "content": "暂缓交接"})
        session_context_loader.handle_pre_invocation(payload)
        gate = handoff_gate.load_gate_state(state_dir, cid)
        self.assertEqual(gate["phase"], "overridden")
        self.assertTrue(gate["overriddenTurnHash"].startswith("1:"))
        compaction = json.loads((state_dir / f"{cid}_compaction.json").read_text(encoding="utf-8"))
        self.assertEqual(compaction["latestUserStepIndex"], 2)
        self.assertEqual(compaction["latestHandoffStepIndex"], 1)

        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 3, "content": "暂缓",
                                  "truncated_fields": ["content"]})
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 4, "content": "请整理交接并停机"})
        session_context_loader.handle_pre_invocation(payload)
        _append_jsonl(full, {"type": "USER_INPUT", "step_index": 3, "content": "暂缓交接"})
        session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "drain")

    def test_mature_conversation_ignores_quoted_banner_and_yields_to_explicit_handoff(self) -> None:
        import handoff_gate

        cid = "agy-quoted-banner"
        transcript = self.tmp_root / "transcript.jsonl"
        state_dir = self.plugin_data / "state"
        banner = handoff_gate.UI_BANNER_PHRASE
        payload = {"conversationId": cid, "transcriptPath": str(transcript)}

        def add(step_index, step_type, content="", source="USER_EXPLICIT"):
            _append_jsonl(transcript, {"step_index": step_index, "type": step_type, "source": source, "content": content})

        add(0, "USER_INPUT", "核对轨迹")
        add(160, "CHECKPOINT", source="SYSTEM")
        add(320, "CHECKPOINT", source="SYSTEM")
        add(321, "USER_INPUT", f"历史引用如下。\n> {banner}\n> 先别交接\n请分析历史问题。")
        add(322, "SYSTEM_MESSAGE", f"<SYSTEM_MESSAGE>\nThe following system message was injected by a hook:\n{banner}\n</SYSTEM_MESSAGE>", "SYSTEM_SDK")
        session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "normal")
        add(323, "USER_INPUT", "先别交接，继续当前对话")
        session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "overridden")
        add(324, "USER_INPUT", "~~~text\n请你开始交接\n~~~\n请逐句分析。")
        session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "overridden")
        add(325, "USER_INPUT", "请你开始交接")
        session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "drain")
        self.assertIn("user_request", handoff_gate.load_gate_state(state_dir, cid)["triggers"])

    def test_07_handoff_gate_telemetry_advisory_drain_and_override(self) -> None:
        """验证长会话遥测建档、Turn-1 防误伤、Stage-1 预警、检查点坍塌 drain 及 R8 显式暂缓"""
        import handoff_gate

        # 1) Turn-1 提问引用英文 UI Banner 严禁误触发 Stage-2
        conv_turn1 = "ag-handoff-turn1"
        tp_t1 = self.tmp_root / "t1.jsonl"
        banner = "This conversation grew too large, so the history before this point may be missing or incomplete."
        _append_jsonl(tp_t1, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": f"<USER_REQUEST>\n{banner}\n请分析原因\n</USER_REQUEST>"})
        out_t1 = session_context_loader.handle_pre_invocation({"conversationId": conv_turn1, "transcriptPath": str(tp_t1)})
        self.assertEqual(out_t1["injectSteps"], [], "新会话首轮仅引用 Banner 不得误触发")

        # 2) 旧版 v1 state 平滑回填遥测，不重复注入已注入检查点，且健康会话保持仅 Stage-1 advisory
        conv_healthy = "ag-handoff-healthy"
        tp_ok = self.tmp_root / "healthy.jsonl"
        _append_jsonl(tp_ok, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "开始长期研究"})
        step_cursor = 140
        for _ in range(4):
            _append_jsonl(tp_ok, {"step_index": step_cursor, "source": "SYSTEM", "type": "CHECKPOINT"})
            step_cursor += 140
        out_ok = session_context_loader.handle_pre_invocation({"conversationId": conv_healthy, "transcriptPath": str(tp_ok)})
        self.assertEqual(len(out_ok["injectSteps"]), 1)
        msg_ok = out_ok["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("Stage-1 advisory", msg_ok)
        self.assertNotIn("Stage-2 drain", msg_ok)
        gate_ok = handoff_gate.load_gate_state(self.plugin_data / "state", conv_healthy)
        self.assertEqual(gate_ok["phase"], "advisory")

        # 3) 检查点间隔坍塌（cp >= 4 且尾 3 段间隔 <= 75）：触发 Stage-2 drain
        conv_thrash = "ag-handoff-thrash"
        tp_th = self.tmp_root / "thrash.jsonl"
        _append_jsonl(tp_th, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/goal\n完成批量校准，不要擅自停止"})
        s = 100
        for i in range(4):
            _append_jsonl(tp_th, {"step_index": s, "source": "SYSTEM", "type": "CHECKPOINT"})
            s += 50
        out_th = session_context_loader.handle_pre_invocation({"conversationId": conv_thrash, "transcriptPath": str(tp_th)})
        msg_th = out_th["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("Stage-2 drain", msg_th)
        self.assertIn("GOAL_CANCELLED", msg_th)

        # 4) 用户发送 "请你处理好交接" 不得误触发 R8 暂缓；发送 "先别交接，继续当前对话" 则触发 overridden 宽限
        _append_jsonl(tp_th, {"step_index": s + 5, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "请你处理好交接"})
        session_context_loader.handle_pre_invocation({"conversationId": conv_thrash, "transcriptPath": str(tp_th)})
        self.assertEqual(handoff_gate.load_gate_state(self.plugin_data / "state", conv_thrash)["phase"], "drain")

        _append_jsonl(tp_th, {"step_index": s + 10, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "先别交接，继续当前对话做完这一批"})
        out_ovr = session_context_loader.handle_pre_invocation({"conversationId": conv_thrash, "transcriptPath": str(tp_th)})
        self.assertEqual(handoff_gate.load_gate_state(self.plugin_data / "state", conv_thrash)["phase"], "overridden")
        self.assertEqual(out_ovr["injectSteps"], [])

        # 5) 受信系统记录、图片请求与轨迹结果，以及超 4000 字符末尾的独立暂缓指令
        conv_proto = "ag-handoff-proto"
        tp_pr = self.tmp_root / "proto.jsonl"
        img_file = self.tmp_root / "plot_01.png"
        img_file.write_bytes(b"PNG" + b"\x00" * 2048)
        _append_jsonl(
            tp_pr,
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>\n检查图片轨迹\n</USER_REQUEST>",
            },
        )
        _append_jsonl(tp_pr, {"step_index": 120, "source": "SYSTEM", "type": "CHECKPOINT"})
        _append_jsonl(tp_pr, {"step_index": 121, "source": "SYSTEM_SDK", "type": "SYSTEM_MESSAGE",
                             "content": f"<SYSTEM_MESSAGE>\n<EPHEMERAL_MESSAGE>\n{banner}\n</EPHEMERAL_MESSAGE>\n</SYSTEM_MESSAGE>"})
        _append_jsonl(
            tp_pr,
            {
                "step_index": 125,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "read_file", "args": {"Path": json.dumps(str(img_file))}}],
            },
        )
        _append_jsonl(tp_pr, {"step_index": 126, "source": "MODEL", "type": "GENERIC", "status": "DONE",
                             "media": [{"uri": str(img_file), "mime_type": "image/png"}]})
        out_pr = session_context_loader.handle_pre_invocation({"conversationId": conv_proto, "transcriptPath": str(tp_pr)})
        self.assertEqual(handoff_gate.load_gate_state(self.plugin_data / "state", conv_proto)["phase"], "drain")
        self.assertEqual(len(out_pr["injectSteps"]), 1)
        comp_pr = json.loads((self.plugin_data / "state" / f"{conv_proto}_compaction.json").read_text(encoding="utf-8"))
        self.assertEqual(comp_pr["transcriptImageCallCount"], 1)
        self.assertEqual(comp_pr["transcriptImageResultCount"], 1)
        image_key = str(img_file.resolve()).casefold() if os.name == "nt" else str(img_file.resolve())
        self.assertEqual(comp_pr["transcriptImageAttemptSizes"][image_key], 2051)
        self.assertEqual(comp_pr["transcriptImageSizes"][image_key], 2051)

        long_tail_r8 = ("补充长指令背景说明。\n" * 400) + "暂不交接，继续当前对话"
        _append_jsonl(tp_pr, {"step_index": 130, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": long_tail_r8})
        session_context_loader.handle_pre_invocation({"conversationId": conv_proto, "transcriptPath": str(tp_pr)})
        self.assertEqual(handoff_gate.load_gate_state(self.plugin_data / "state", conv_proto)["phase"], "overridden")

    def test_11_compact_transcript_resolution_and_legacy_gate_recalibration(self) -> None:
        """传入 transcript_full.jsonl 时优先度量紧凑版 transcript.jsonl，兼顾截断原文还原与旧误报自愈"""
        import handoff_gate

        conv_id = "ag-compact-resolve"
        log_dir = self.tmp_root / "brain" / conv_id / ".system_generated" / "logs"
        compact_tp = log_dir / "transcript.jsonl"
        full_tp = log_dir / "transcript_full.jsonl"

        full_user_text = "<USER_REQUEST>\n重要原始需求：保持数值守恒\n</USER_REQUEST>"
        _append_jsonl(
            compact_tp,
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>\n重要原始<truncated 12 bytes>",
                "truncated_fields": ["content"],
            },
        )
        _append_jsonl(compact_tp, {"step_index": 10, "source": "SYSTEM", "type": "CHECKPOINT"})

        _append_jsonl(
            full_tp,
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": full_user_text,
            },
        )
        pad_block = "X" * (650 * 1024)
        for idx in range(1, 11):
            _append_jsonl(full_tp, {"step_index": idx, "source": "SYSTEM", "type": "GENERIC", "content": pad_block})

        self.assertGreater(full_tp.stat().st_size, 6 * 1024 * 1024)
        self.assertLess(compact_tp.stat().st_size, 1024 * 1024)

        out = session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(full_tp)})
        self.assertEqual(handoff_gate.load_gate_state(self.plugin_data / "state", conv_id)["phase"], "normal")
        self.assertEqual(len(out["injectSteps"]), 1)
        self.assertIn("重要原始需求：保持数值守恒", out["injectSteps"][0]["systemMessage"]["systemMessage"])
        comp_saved = json.loads((self.plugin_data / "state" / f"{conv_id}_compaction.json").read_text(encoding="utf-8"))
        self.assertEqual(comp_saved.get("scannedTranscriptName"), "transcript.jsonl")

        # 模拟从旧版 telemetryVersion=2 + gateSchemaVersion=1（因 transcript_full 体积误进入 drain）平滑升级
        conv_legacy = "ag-compact-legacy"
        state_dir = self.plugin_data / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / f"{conv_legacy}_compaction.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "telemetryVersion": 2,
                    "conversationId": conv_legacy,
                    "scannedByteOffset": full_tp.stat().st_size,
                    "latestCheckpointStep": 10,
                    "lastInjectedCheckpointStep": 10,
                    "hasExplicitUserInput": True,
                }
            ),
            encoding="utf-8",
        )
        (state_dir / f"{conv_legacy}_handoff_gate.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "conversationId": conv_legacy,
                    "phase": "drain",
                    "triggers": ["transcript_bytes"],
                }
            ),
            encoding="utf-8",
        )
        out_leg = session_context_loader.handle_pre_invocation({"conversationId": conv_legacy, "transcriptPath": str(full_tp)})
        self.assertEqual(out_leg["injectSteps"], [], "升级重标定时不得重复注入已注入过的 CHECKPOINT")
        gate_leg = handoff_gate.load_gate_state(state_dir, conv_legacy)
        self.assertEqual(gate_leg["phase"], "normal")
        self.assertEqual(gate_leg["gateSchemaVersion"], 3)

        # 取消 /goal 与宽限期届满后重复发送相同文本 R8 暂缓指令必须生效
        conv_r8 = "ag-cancel-repeat-r8"
        tp_r8 = self.tmp_root / f"{conv_r8}.jsonl"
        _append_jsonl(tp_r8, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/goal\n开始执行"})
        s = 100
        for _ in range(16):
            _append_jsonl(tp_r8, {"step_index": s, "source": "SYSTEM", "type": "CHECKPOINT"})
            s += 100
        session_context_loader.handle_pre_invocation({"conversationId": conv_r8, "transcriptPath": str(tp_r8)})
        self.assertTrue(handoff_gate.load_gate_state(state_dir, conv_r8)["isGoalActive"])

        _append_jsonl(tp_r8, {"step_index": s + 1, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "取消 /goal\n先别交接，继续当前对话"})
        session_context_loader.handle_pre_invocation({"conversationId": conv_r8, "transcriptPath": str(tp_r8)})
        g_r8 = handoff_gate.load_gate_state(state_dir, conv_r8)
        self.assertFalse(g_r8["isGoalActive"], "显式取消 /goal 必须关闭 isGoalActive")
        self.assertEqual(g_r8["phase"], "overridden")

        for k in range(3):
            s += 150
            _append_jsonl(tp_r8, {"step_index": s, "source": "SYSTEM", "type": "CHECKPOINT"})
        session_context_loader.handle_pre_invocation({"conversationId": conv_r8, "transcriptPath": str(tp_r8)})
        self.assertEqual(handoff_gate.load_gate_state(state_dir, conv_r8)["phase"], "drain")

        _append_jsonl(tp_r8, {"step_index": s + 5, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "取消 /goal\n先别交接，继续当前对话"})
        session_context_loader.handle_pre_invocation({"conversationId": conv_r8, "transcriptPath": str(tp_r8)})
        self.assertEqual(handoff_gate.load_gate_state(state_dir, conv_r8)["phase"], "overridden")

    def test_12_agy_handoff_gate_v232_workflow_swarm_and_edit_aliases(self) -> None:
        """验证 v2.32.0 / CLI v1.5.8 新增工具在 drain 与 freeze 阶段的收口门禁行为"""
        import handoff_gate
        import status_guard

        cid = "ag-v232-tools"
        project = self.tmp_root / "proj_v232"
        project.mkdir()
        state_dir = self.plugin_data / "state"
        tp = self.tmp_root / "v232.jsonl"
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "请整理交接并停机"})
        base = {"conversationId": cid, "transcriptPath": str(tp), "cwd": str(project), "workspacePaths": [str(project)]}
        session_context_loader.handle_pre_invocation(base)
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "drain")

        def pre_tool(name: str, args: dict) -> dict:
            return status_guard.handle_pre_tool_use({**base, "toolCall": {"name": name, "args": args}})

        # drain 阶段：run_workflow 作为多代理编排委派工具立即拦截（不消耗 drain 预算）
        self.assertEqual(pre_tool("run_workflow", {"Script": "x = 1\n"})["decision"], "deny")
        # drain 阶段（总预算 8 次）：post_message / swarm_wait / sed_file / notebook_edit 各消耗 1 次动作预算
        self.assertEqual(pre_tool("post_message", {"Message": "请立即汇总收口"})["decision"], "allow")
        self.assertEqual(pre_tool("swarm_wait", {})["decision"], "allow")
        for idx in range(3):
            self.assertEqual(pre_tool("sed_file", {"Path": str(project / f"a_{idx}.py")})["decision"], "allow")
            self.assertEqual(pre_tool("notebook_edit", {"NotebookPath": str(project / f"b_{idx}.ipynb")})["decision"], "allow")
        # 满 8 次动作后已自动转入 freeze，后续局部编辑立即拒绝
        self.assertEqual(handoff_gate.load_gate_state(state_dir, cid)["phase"], "freeze")
        self.assertEqual(pre_tool("sed_file", {"Path": str(project / "c.py")})["decision"], "deny")

        # freeze 阶段：只读盘点（含 read_board / read_url_content / list_resources / read_resource / 文本 view_file）放行
        for ro_tool, ro_args in (
            ("read_board", {}),
            ("read_url_content", {"Url": "https://antigravity.google/docs/changelog"}),
            ("list_resources", {"ServerName": "zotero"}),
            ("read_resource", {"ServerName": "zotero", "Uri": "zotero://item/1"}),
            ("view_file", {"TargetFile": str(project / "notes.md")}),
        ):
            self.assertEqual(pre_tool(ro_tool, ro_args)["decision"], "allow", ro_tool)

        # freeze 阶段：图片读取、Swarm 通信、编排委派与普通编辑一律拒绝
        for blocked_tool, blocked_args in (
            ("view_file", {"TargetFile": str(project / "mesh.png")}),
            ("post_message", {"Message": "继续派活"}),
            ("swarm_wait", {}),
            ("run_workflow", {"Script": "x = 1\n"}),
            ("notebook_edit", {"NotebookPath": str(project / "b.ipynb")}),
        ):
            self.assertEqual(pre_tool(blocked_tool, blocked_args)["decision"], "deny", blocked_tool)


class TestCodexHandoffAdapter(unittest.TestCase):
    """Codex 独立协议、保守默认值和严格模式的可观察状态转换。"""

    def setUp(self):
        import handoff_gate
        self.gate = handoff_gate
        self.tmp = _new_temp_dir(prefix="codex-handoff-test-")
        sandbox_root = Path(self.tmp.name).resolve()
        self.root = sandbox_root / "workspace"
        self.root.mkdir(parents=True, exist_ok=True)
        self.fake_home = sandbox_root / "home"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.state_dir = self.root / "state"
        clean_env = _scrub_host_env(
            HOME=str(self.fake_home),
            USERPROFILE=str(self.fake_home),
            PLUGIN_DATA=str(self.root),
            USER_GUARD_LOGS_DIR=str(self.root / "logs"),
        )
        environment = mock.patch.dict(os.environ, clean_env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        home_patch = mock.patch.object(Path, "home", return_value=self.fake_home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.transcript = self.root / "rollout.jsonl"
        self.sid = "codex-handoff-main"
        _append_jsonl(self.transcript, {"type": "session_meta", "payload": {
            "id": self.sid, "cwd": str(self.root), "source": "cli"}})

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, event, mode="advisory", limit=0, **extra):
        payload = {"session_id": self.sid, "transcript_path": str(self.transcript),
                   "cwd": str(self.root), "hook_event_name": event, "turn_id": "turn-1", **extra}
        return self.gate.handle_codex_handoff(payload, self.state_dir, mode, limit)

    def state(self):
        return json.loads((self.state_dir / (self.sid + "_handoff_gate.json")).read_text(encoding="utf-8"))

    def test_advisory_never_blocks_and_compaction_events_are_not_double_counted(self):
        first = self.call("PostCompact", trigger="auto")
        self.assertIn("Codex 长会话", first["systemMessage"])
        self.call("PostCompact", trigger="auto")
        start = self.call("SessionStart", source="compact")
        self.assertIn("additionalContext", start["hookSpecificOutput"])
        self.assertEqual(self.state()["compactionCount"], 1)
        for index in range(2, 21):
            self.call("PostCompact", turn_id=f"turn-{index}", trigger="auto")
        self.assertEqual(self.call("PreToolUse", tool_name="spawn_agent", tool_input={}), {})
        self.assertEqual(self.call("Stop", last_assistant_message="尚未交接"), {})
        self.assertEqual(self.state()["phase"], "advisory")

    def test_strict_without_explicit_limit_does_not_invent_capacity_threshold(self):
        for index in range(5):
            self.call("PostCompact", mode="enforce", turn_id=f"turn-{index}")
        self.assertEqual(self.call("PreToolUse", mode="enforce", tool_name="spawn_agent"), {})
        self.assertEqual(self.state()["phase"], "advisory")

    def test_record_identity_counts_two_compactions_in_one_turn(self):
        for stamp in ("2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z"):
            _append_jsonl(self.transcript, {"type": "compacted", "timestamp": stamp, "payload": {"message": "summary"}})
            self.call("PostCompact", mode="enforce", limit=2)
            self.call("PostCompact", mode="enforce", limit=2)
        self.assertEqual(self.state()["compactionCount"], 2)
        self.assertEqual(self.state()["compactionIdentity"], "rollout_record")
        self.assertEqual(self.state()["phase"], "drain")

    def test_strict_budget_idempotence_and_tool_protocol(self):
        self.call("PostCompact", mode="enforce", limit=1)
        denied = self.call("PreToolUse", mode="enforce", limit=1, tool_name="spawn_agent")
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        mcp_denied = self.call("PreToolUse", mode="enforce", limit=1, tool_name="mcp__codex_app__create_thread")
        self.assertEqual(mcp_denied["hookSpecificOutput"]["permissionDecision"], "deny")
        push = self.call("PreToolUse", mode="enforce", limit=1, tool_name="Bash",
                         tool_input={"command": 'echo "$(git push origin main)"'})
        self.assertEqual(push["hookSpecificOutput"]["permissionDecision"], "deny")
        for index in range(self.gate.CODEX_DRAIN_ACTIONS):
            args = {"tool_name": "Bash", "tool_input": {"command": "python -B verify.py"}, "tool_use_id": str(index)}
            first = self.call("PreToolUse", mode="enforce", limit=1, **args)
            self.assertEqual(first, self.call("PreToolUse", mode="enforce", limit=1, **args))
        self.assertEqual(self.state()["phase"], "freeze")
        self.assertEqual(self.state()["drainActionsRemaining"], 0)
        blocked = self.call("PreToolUse", mode="enforce", limit=1, tool_name="Bash", tool_input={"command": "python build.py"})
        self.assertEqual(blocked["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.call("PreToolUse", mode="enforce", limit=1, tool_name="Bash", tool_input={"command": "git status --short"}), {})
        self.assertEqual(self.call("PreToolUse", mode="enforce", limit=1, tool_name="get_goal"), {})

    def test_git_branch_mutations_block_in_both_frozen_hosts(self):
        self.call("UserPromptSubmit", mode="enforce", prompt="请整理交接并停机")
        state = self.state()
        state["phase"] = "freeze"
        self.gate.save_gate_state(self.state_dir, self.sid, state)
        agy_sid = "agy-frozen-branch"
        self.gate.save_gate_state(self.state_dir, agy_sid, {**state, "conversationId": agy_sid})
        safe = ("", "-a -vv", "--list feature/*", "--contains HEAD", "--merged HEAD --format=%(refname)",
                "--points-at HEAD --sort=-committerdate", "--abbrev=8 --color=never", "--no-column")
        unsafe = ("--unset-upstream", "--set-upstream-to=origin/main", "-u origin/main", "--track", "--no-track",
                  "--edit-description", "-f", "--force", "-d old", "-D old", "-m old new", "-M old new",
                  "-c old new", "-C old new", "--delete old", "--move old new", "--copy old new",
                  "--list --unset-upstream", "new-branch", "--contains HEAD new-branch", "--unknown-option")
        for options, denied in [(args, False) for args in safe] + [(args, True) for args in unsafe]:
            command = "git branch " + options
            with self.subTest(command=command):
                result = self.call("PreToolUse", mode="enforce", tool_name="Bash", tool_input={"command": command})
                if denied:
                    self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
                else:
                    self.assertEqual(result, {})
                agy = self.gate.check_pre_tool_use_locked({
                    "conversationId": agy_sid, "cwd": str(self.root), "workspacePaths": [str(self.root)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
                }, self.state_dir)
                self.assertEqual(agy["decision"] if agy else "allow", "deny" if denied else "allow")

    def test_frozen_patch_exemption_is_only_for_in_project_handoff_documents(self):
        self.call("UserPromptSubmit", mode="enforce", prompt="请整理交接并停机")
        state = self.state()
        state["phase"] = "freeze"
        self.gate.save_gate_state(self.state_dir, self.sid, state)
        good = "*** Begin Patch\n*** Add File: docs/handoff.md\n+handoff\n*** End Patch"
        self.assertEqual(self.call("PreToolUse", mode="enforce", tool_name="apply_patch", tool_input={"command": good}), {})
        for path in ("src/handoff.py", "../handoff.md", "docs/ordinary.md"):
            patch = good.replace("docs/handoff.md", path)
            result = self.call("PreToolUse", mode="enforce", tool_name="apply_patch", tool_input={"command": patch})
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        mixed = good.replace("*** End Patch", "*** Update File: solver.py\n@@\n-old\n+new\n*** End Patch")
        result = self.call("PreToolUse", mode="enforce", tool_name="apply_patch", tool_input={"command": mixed})
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_stop_correction_is_bounded_and_never_claims_goal_cancelled(self):
        self.call("UserPromptSubmit", mode="enforce", prompt="请整理交接并停机")
        first = self.call("Stop", mode="enforce", last_assistant_message="尚未整理")
        self.assertEqual(first["decision"], "block")
        second = self.call("Stop", mode="enforce", turn_id="continuation-turn", last_assistant_message="仍未整理")
        self.assertFalse(second["continue"])
        self.assertFalse(self.state()["handoffRecorded"])
        reply = ("已验证：现有离线测试通过。Git 工作区保留尚未提交的改动。未完成：真实宿主的事件验收。\n"
                 "新对话提示词\n```text\n请读用户原话和当前源码，先核对工作区，继续未完成的真实宿主验收；后台任务尚待核对。\n```")
        final = self.call("Stop", mode="enforce", last_assistant_message=reply)
        self.assertFalse(final["continue"])
        self.assertTrue(self.state()["handoffRecorded"])
        self.assertNotIn("GOAL_CANCELLED", json.dumps(final))

    def test_codex_stop_accepts_only_changed_current_handoff_document(self):
        self.call("UserPromptSubmit", mode="enforce", prompt="请整理交接并停机")
        path = self.root / "HANDOFF.md"
        document = (
            "已验证：已完成第一批核对。\nGit 工作区：保留当前修改。\n未完成：第二批验收。\n"
            "## 新对话提示词\n请读取用户最新裁定，从第二批的完整验收开始。\n"
        )
        path.write_text(document, encoding="utf-8")
        patch = "*** Begin Patch\n*** Update File: HANDOFF.md\n@@\n+本轮增加证据。\n*** End Patch"
        self.call("PreToolUse", mode="enforce", tool_name="apply_patch", tool_input=patch, tool_use_id="proposal")
        self.assertEqual(self.call("Stop", mode="enforce", last_assistant_message="文件已写")["decision"], "block")
        path.write_text(document + "本轮增加证据。\n", encoding="utf-8")
        result = self.call("Stop", mode="enforce", last_assistant_message="文件已写")
        self.assertFalse(result["continue"])
        self.assertTrue(self.state()["handoffRecorded"])

        self.call("UserPromptSubmit", mode="enforce", turn_id="next", prompt="暂不交接")
        self.call("UserPromptSubmit", mode="enforce", turn_id="new-handoff", prompt="请整理交接并停机")
        self.assertEqual(self.call("Stop", mode="enforce", last_assistant_message="仍用前一次文档")["decision"], "block")

    def test_codex_stop_rejects_candidate_redirected_outside_project(self):
        import subprocess

        project = self.root / "project"
        project.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        outside_file = outside / "handoff.md"
        outside_file.write_text(
            "已验证：第一批通过。\nGit 工作区：当前干净。\n未完成：第二批。\n"
            "## 新对话提示词\n请先核对工作区，再完成第二批验收。\n", encoding="utf-8",
        )
        original = project / "docs"
        original.mkdir()
        self.call("UserPromptSubmit", mode="enforce", prompt="请整理交接并停机", cwd=str(project))
        patch = "*** Begin Patch\n*** Add File: docs/handoff.md\n+内容\n*** End Patch"
        self.call("PreToolUse", mode="enforce", tool_name="apply_patch", tool_input=patch, cwd=str(project))
        original.rmdir()
        try:
            original.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "New-Item -ItemType Junction -Path $env:UG_LINK -Target $env:UG_TARGET | Out-Null"],
                env={**os.environ, "UG_LINK": str(original), "UG_TARGET": str(outside)},
                capture_output=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
        rejected = self.call("Stop", mode="enforce", cwd=str(project), last_assistant_message="文件已写")
        self.assertEqual(rejected["decision"], "block")
        self.assertFalse(self.state()["handoffRecorded"])

    def test_user_override_is_per_turn_and_does_not_trigger_from_quoted_history(self):
        self.call("PostCompact", mode="enforce", limit=1)
        self.call("UserPromptSubmit", mode="enforce", limit=1,
                  prompt='历史材料：“先别交接，继续当前对话”。请分析这句话。')
        self.assertEqual(self.state()["phase"], "drain")
        self.call("UserPromptSubmit", mode="enforce", limit=1, prompt="先别交接，继续当前对话")
        self.assertEqual(self.state()["phase"], "overridden")
        for index in range(2, 5):
            self.call("PostCompact", mode="enforce", limit=1, turn_id=f"turn-{index}")
        self.assertEqual(self.state()["phase"], "drain")
        self.call("UserPromptSubmit", mode="enforce", limit=1, turn_id="turn-5", prompt="先别交接，继续当前对话")
        self.assertEqual(self.state()["phase"], "overridden")
        self.call("UserPromptSubmit", mode="enforce", limit=1, turn_id="turn-6", prompt="历史材料：请整理交接并停机。这是以前的原话，请分析。")
        self.assertEqual(self.state()["phase"], "overridden")

    def test_subagent_missing_identity_and_malformed_state_do_not_contaminate_parent(self):
        self.assertEqual(self.call("PostCompact", agent_id="child-agent"), {})
        self.assertFalse(self.state_dir.exists())
        self.assertIn("systemMessage", self.call("PostCompact", session_id="../escape"))
        self.assertFalse(self.state_dir.exists())
        self.assertIn("systemMessage", self.call("PostCompact", transcript_path=""))
        self.assertFalse(self.state_dir.exists())
        self.call("PostCompact")
        state_path = self.state_dir / (self.sid + "_handoff_gate.json")
        state_path.write_text("{bad", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.call("PostCompact")
        self.assertEqual(state_path.read_text(encoding="utf-8"), "{bad")

    def test_concurrent_compactions_and_strict_lock_timeout(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: self.call("PostCompact", turn_id=f"parallel-{n}"), range(32)))
        self.assertEqual(self.state()["compactionCount"], 32)
        anchor = self.state_dir / f".{self.sid}.lock"
        inode = anchor.stat().st_ino
        holder = (
            "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import user_words\n"
            "with user_words.session_state_lock(Path(sys.argv[2]), sys.argv[3]):\n"
            "    print('locked', flush=True)\n"
            "    sys.stdin.readline()\n"
        )
        with subprocess.Popen(
            [sys.executable, "-B", "-X", "utf8", "-c", holder, str(HOOKS_DIR), str(self.state_dir), self.sid],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as proc:
            try:
                self.assertEqual(proc.stdout.readline().strip(), "locked")
                os.utime(anchor, (1, 1))
                for strict in (True, False):
                    with self.assertRaises(TimeoutError):
                        with self.gate._gate_state_lock(self.state_dir, self.sid, timeout_sec=0.03, strict=strict):
                            self.fail("未取得锁时不得进入状态更新")
                self.assertEqual(anchor.stat().st_ino, inode)
                proc.kill()
            finally:
                proc.communicate(timeout=5)
        with self.gate._gate_state_lock(self.state_dir, self.sid, strict=True):
            # 替换 token 模拟迟到清理，离开旧上下文时不得清除不属于它的记录。
            with anchor.open("r+b") as stream:
                stream.seek(1)
                stream.write(b"x" * 32)
        self.assertEqual(anchor.read_bytes()[1:], b"x" * 32)
        with self.gate._gate_state_lock(self.state_dir, self.sid, strict=True):
            self.assertEqual(anchor.stat().st_ino, inode)
        self.assertEqual(anchor.read_bytes()[1:], b"")

    def test_compaction_reconciles_delayed_large_and_multiple_records(self):
        self.call("PostCompact", turn_id="delayed", trigger="auto")
        self.assertEqual(self.state()["compactionFallbackCount"], 1)
        _append_jsonl(self.transcript, {"type": "event_msg", "payload": {
            "type": "task_started", "turn_id": "delayed",
        }})
        _append_jsonl(self.transcript, {"type": "compacted", "ordinal": 3, "payload": {
            "window_id": "window-one", "replacement_history": ["x" * (300 * 1024)],
        }})
        _append_jsonl(self.transcript, {"type": "turn_context", "payload": {"turn_id": "delayed"}})
        self.call("UserPromptSubmit", turn_id="next")
        repaired = self.state()
        self.assertEqual(repaired["compactionCount"], 1)
        self.assertEqual(repaired["compactionRecordCount"], 1)
        self.assertEqual(repaired["compactionFallbackCount"], 0)
        self.assertEqual(repaired["compactionLedger"]["records"]["window:window-one"]["turnSource"], "following_turn_context")
        for index in (2, 3):
            _append_jsonl(self.transcript, {"type": "compacted", "payload": {"window_id": f"window-{index}"}})
        _append_jsonl(self.transcript, {"type": "response_item", "payload": {"output": "x" * (300 * 1024)}})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.call("PostCompact", turn_id="delayed", trigger="auto"), range(8)))
        self.assertEqual(self.state()["compactionCount"], 3)
        self.assertEqual(self.state()["compactionRecordCount"], 3)
        self.assertEqual(self.state()["compactionFallbackCount"], 0)

    def test_delayed_turn_context_cannot_prematurely_trigger_drain(self):
        _append_jsonl(self.transcript, {"type": "turn_context", "payload": {"turn_id": "previous-turn"}})
        _append_jsonl(self.transcript, {"type": "compacted", "payload": {"window_id": "window-one"}})
        self.call("PostCompact", mode="enforce", limit=2, turn_id="current-turn", trigger="auto")
        early = self.state()
        self.assertEqual(early["compactionCount"], 1)
        self.assertEqual(early["phase"], "advisory")
        self.assertTrue(early["compactionIssues"])
        _append_jsonl(self.transcript, {"type": "turn_context", "payload": {"turn_id": "current-turn"}})
        self.call("SessionStart", mode="enforce", limit=2, source="compact")
        repaired = self.state()
        self.assertEqual(repaired["compactionCount"], 1)
        self.assertEqual(repaired["phase"], "advisory")
        self.assertEqual(repaired["compactionLedger"]["records"]["window:window-one"]["turn"], "current-turn")
        _append_jsonl(self.transcript, {"type": "compacted", "payload": {"window_id": "window-two"}})
        self.call("PostCompact", mode="enforce", limit=2, turn_id="current-turn", trigger="auto")
        self.assertEqual(self.state()["compactionCount"], 2)
        self.assertEqual(self.state()["phase"], "drain")

    def test_unassigned_compaction_does_not_duplicate_fallback(self):
        self.call("PostCompact", turn_id="undetermined", trigger="auto")
        row = b'{"type":"compacted","payload":{"window_id":"known-record"}}'
        with self.transcript.open("ab") as stream:
            stream.write(row[:-1])
        self.call("SessionStart", source="compact")
        self.assertEqual(self.state()["compactionRecordCount"], 0)
        self.assertTrue(self.state()["compactionIssues"])
        with self.transcript.open("ab") as stream:
            stream.write(row[-1:] + b"\n")
        self.call("SessionStart", source="compact")
        self.assertEqual(self.state()["compactionCount"], 1)
        self.assertEqual(self.state()["compactionRecordCount"], 1)
        self.assertEqual(self.state()["compactionFallbackCount"], 0)
        self.assertTrue(self.state()["compactionIssues"])

    def test_canonical_identity_is_shared_by_archive_and_gate(self):
        state_dir = self.root / "session-context"
        log = self.root / "archive.log"
        payload = {"session_id": self.sid, "transcript_path": str(self.transcript), "source": "resume"}
        self.assertEqual(session_context_loader.handle_codex_session_start(
            {**payload, "agent_id": "another-agent"}, str(state_dir), str(log),
        ), {})
        self.assertFalse(log.exists())
        self.transcript.write_text("", encoding="utf-8")
        _append_jsonl(self.transcript, {"type": "session_meta", "payload": {"id": self.sid, "source": "unknown"}})
        self.assertIn("来源未知", self.call("PostCompact")["systemMessage"])
        context = session_context_loader.handle_codex_session_start(payload, str(state_dir), str(log))
        self.assertIn("来源未知", context["hookSpecificOutput"]["additionalContext"])
        self.assertFalse((self.root / "user-words").exists())

    def test_corrupted_agy_handoff_gate_json_does_not_abort_compaction_recovery(self):
        plugin_data = self.root / "agy-plugin-data"
        state_dir = plugin_data / "state"
        state_dir.mkdir(parents=True)
        cid = "agy-corrupt-gate"
        (state_dir / f"{cid}_handoff_gate.json").write_text("{corrupted", encoding="utf-8")
        tp = self.root / "agy-brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "<USER_REQUEST>\n保留数值通量守恒\n</USER_REQUEST>"})
        _append_jsonl(tp, {"step_index": 12, "source": "SYSTEM", "type": "CHECKPOINT"})
        with unittest.mock.patch.dict(os.environ, {"PLUGIN_DATA": str(plugin_data)}):
            out = session_context_loader.handle_pre_invocation({"conversationId": cid, "transcriptPath": str(tp)})
        self.assertEqual(len(out["injectSteps"]), 1)
        self.assertIn("保留数值通量守恒", out["injectSteps"][0]["systemMessage"]["systemMessage"])
        self.assertTrue((state_dir / f"{cid}_user_words.md").is_file())

    def test_batch2_parenthesized_commands_are_blocked_in_both_hosts_and_phases(self):
        self.call("UserPromptSubmit", mode="enforce", prompt="请整理交接并停机")
        commands = (
            "(git push origin main)",
            "((git push origin main))",
            "((ls && git push origin main))",
            "(git status; git push origin main)",
            "(python cae_harness.py task --kind code)",
            "(ls && (git push origin main))",
            'echo $(git status; git push origin main)',
        )
        for phase in ("drain", "freeze"):
            state = self.state()
            state["phase"] = phase
            self.gate.save_gate_state(self.state_dir, self.sid, state)
            agy_sid = "agy-grouped-" + phase
            agy_state = self.gate.default_gate_state(agy_sid)
            agy_state["phase"] = phase
            self.gate.save_gate_state(self.state_dir, agy_sid, agy_state)
            for command in commands:
                with self.subTest(phase=phase, command=command):
                    self.assertEqual(self.gate._classify_run_command(command), "blocked_always")
                    codex = self.call("PreToolUse", mode="enforce", tool_name="Bash", tool_input={"command": command})
                    self.assertEqual(codex["hookSpecificOutput"]["permissionDecision"], "deny")
                    agy = self.gate.check_pre_tool_use_locked({
                        "conversationId": agy_sid, "cwd": str(self.root),
                        "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
                    }, self.state_dir)
                    self.assertEqual(agy["decision"], "deny")
            for command in ('echo "$(git status)"', "echo [System.Text.UTF8Encoding]::new($false)"):
                with self.subTest(phase=phase, safe=command):
                    self.assertEqual(self.gate._split_shell_clauses(command), [command])
                    self.assertEqual(self.gate._classify_run_command(command), "readonly_inspect")
                    self.assertEqual(self.call("PreToolUse", mode="enforce", tool_name="Bash",
                                               tool_input={"command": command}), {})
                    self.assertIsNone(self.gate.check_pre_tool_use_locked({
                        "conversationId": agy_sid, "cwd": str(self.root),
                        "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
                    }, self.state_dir))

    def test_batch2_embedded_parentheses_preserve_calls_and_substitutions(self):
        commands = (
            "[System.Text.UTF8Encoding]::new($false)",
            "foo()", "foo(bar(baz))", "echo $(git status)",
            "cat <(git status)", "cat >(git status)", 'echo "(git push origin main)"',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.gate._split_shell_clauses(command), [command])
                self.assertNotEqual(self.gate._classify_run_command(command), "blocked_always")
        self.assertEqual(self.gate._split_shell_clauses("((git status; git push origin main))"),
                         ["git status", "git push origin main"])

    def test_batch2_compaction_pending_survives_unrelated_and_unparsed_rows(self):
        self.call("PostCompact", trigger="auto")
        _append_jsonl(self.transcript, {"type": "compacted", "payload": {"window_id": "batch2-window"}})
        self.call("PreToolUse", tool_name="read_file")
        _append_jsonl(self.transcript, {"type": "response_item", "payload": {"output": "ordinary output"}})
        _append_jsonl(self.transcript, {"type": "session_meta", "payload": {"id": self.sid}})
        _append_jsonl(self.transcript, {"type": "response_item", "payload": {"text": "turn_context"}})
        _append_jsonl(self.transcript, ["turn_context"])
        _append_jsonl(self.transcript, {"type": "turn_context", "payload": None})
        with self.transcript.open("ab") as stream:
            stream.write(b'{"type":"turn_context",bad}\n')
        self.call("PreToolUse", tool_name="read_file")
        ledger = self.state()["compactionLedger"]
        self.assertEqual(ledger["files"][self.gate._path_key(self.transcript)]["pendingRecord"], "window:batch2-window")
        _append_jsonl(self.transcript, {"type": "turn_context", "payload": {"turn_id": "turn-1"}})
        self.call("PostCompact", trigger="auto")
        state = self.state()
        record = state["compactionLedger"]["records"]["window:batch2-window"]
        self.assertEqual((record["turn"], record["turnSource"]), ("turn-1", "following_turn_context"))
        self.assertEqual(state["compactionCount"], 1)
        self.assertEqual(state["compactionRecordCount"], 1)
        self.assertEqual(state["compactionFallbackCount"], 0)
        self.assertEqual(state["compactionLedger"]["files"][self.gate._path_key(self.transcript)]["pendingRecord"], "")

    def test_batch2_compaction_pending_ends_at_turn_completion_or_next_record(self):
        for boundary in ("task_complete", "turn_complete", "turn_aborted", "compacted"):
            with self.subTest(boundary=boundary):
                transcript = self.root / (boundary + ".jsonl")
                ledger = {"files": {}, "records": {}, "observations": {}}
                _append_jsonl(transcript, {"type": "compacted", "payload": {"window_id": "first"}})
                if boundary == "compacted":
                    event = {"type": "compacted", "payload": {"window_id": "second"}}
                else:
                    event = {"type": "event_msg", "payload": {"type": boundary, "turn_id": "old"}}
                _append_jsonl(transcript, event)
                _append_jsonl(transcript, {"type": "turn_context", "payload": {"turn_id": "next"}})
                self.assertIsNone(self.gate._scan_codex_compactions(transcript, ledger))
                self.assertEqual(ledger["records"]["window:first"]["turn"], "")
                if boundary == "compacted":
                    self.assertEqual(ledger["records"]["window:second"]["turnSource"], "following_turn_context")
                self.assertEqual(ledger["files"][self.gate._path_key(transcript)]["pendingRecord"], "")

    def test_batch2_mirrored_compactions_keep_confirmed_turn_ownership(self):
        for source in ("record", "following_turn_context"):
            with self.subTest(source=source):
                ledger = {"files": {}, "records": {}, "observations": {}}
                first = self.root / (source + "-first.jsonl")
                second = self.root / (source + "-mirror.jsonl")
                fields = {"window_id": "shared"}
                if source == "record":
                    fields["turn_id"] = "confirmed"
                _append_jsonl(first, {"type": "compacted", "payload": fields})
                _append_jsonl(first, {"type": "turn_context", "payload": {"turn_id": "confirmed"}})
                self.assertIsNone(self.gate._scan_codex_compactions(first, ledger))
                _append_jsonl(second, {"type": "compacted", "payload": {"window_id": "shared"}})
                _append_jsonl(second, {"type": "turn_context", "payload": {"turn_id": "unrelated"}})
                self.assertIsNone(self.gate._scan_codex_compactions(second, ledger))
                self.assertEqual(len(ledger["records"]), 1)
                self.assertEqual(ledger["records"]["window:shared"]["turn"], "confirmed")
                self.assertEqual(ledger["records"]["window:shared"]["turnSource"], source)
                self.assertEqual(ledger["files"][self.gate._path_key(second)]["pendingRecord"], "")

    def test_batch2_rewritten_codex_files_remove_only_their_offset_records(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                transcript = self.root / ("rewrite-" + str(replacement) + ".jsonl")
                other = self.root / ("other-" + str(replacement) + ".jsonl")
                ledger = {"files": {}, "records": {}, "observations": {}}
                for message in ("old-one", "old-two"):
                    _append_jsonl(transcript, {"type": "compacted", "payload": {"message": message, "turn_id": "old"}})
                _append_jsonl(transcript, {"type": "compacted", "payload": {"window_id": "retained", "turn_id": "old"}})
                _append_jsonl(other, {"type": "compacted", "payload": {"message": "other", "turn_id": "other"}})
                self.assertIsNone(self.gate._scan_codex_compactions(transcript, ledger))
                self.assertIsNone(self.gate._scan_codex_compactions(other, ledger))
                old_keys = {key for key, rec in ledger["records"].items()
                            if key.startswith("record:") and rec["path"] == str(transcript)}
                other_keys = set(ledger["records"]) - old_keys
                if replacement:
                    rewritten = transcript.with_name("replacement.jsonl")
                    rewritten.write_text("", encoding="utf-8")
                    _append_jsonl(rewritten, {"type": "compacted", "payload": {"message": "new", "turn_id": "new"}})
                    old_identity = [transcript.stat().st_dev, transcript.stat().st_ino]
                    os.replace(rewritten, transcript)
                    self.assertNotEqual([transcript.stat().st_dev, transcript.stat().st_ino], old_identity)
                else:
                    transcript.write_text("", encoding="utf-8")
                    self.assertIsNone(self.gate._scan_codex_compactions(transcript, ledger))
                    self.assertTrue(old_keys.isdisjoint(ledger["records"]))
                    _append_jsonl(transcript, {"type": "compacted", "payload": {"message": "new", "turn_id": "new"}})
                self.assertIsNone(self.gate._scan_codex_compactions(transcript, ledger))
                self.assertTrue(old_keys.isdisjoint(ledger["records"]))
                self.assertTrue(other_keys.issubset(ledger["records"]))
                self.assertEqual(len(ledger["records"]), len(other_keys) + 1)

    def test_batch2_new_user_turn_resets_stop_correction_once(self):
        self.call("UserPromptSubmit", mode="enforce", prompt="请整理交接并停机")
        self.assertEqual(self.call("Stop", mode="enforce", last_assistant_message="尚未交接")["decision"], "block")
        self.assertFalse(self.call("Stop", mode="enforce", last_assistant_message="仍未交接")["continue"])
        self.assertTrue(self.state()["stopCorrectionRequested"])
        self.call("UserPromptSubmit", mode="enforce", turn_id="next-user", prompt="核对本轮未完成事项")
        self.assertFalse(self.state()["stopCorrectionRequested"])
        self.assertEqual(self.call("Stop", mode="enforce", last_assistant_message="缺少提示词")["decision"], "block")
        self.call("UserPromptSubmit", mode="enforce", turn_id="next-user", prompt="核对本轮未完成事项")
        self.assertTrue(self.state()["stopCorrectionRequested"])
        self.assertFalse(self.call("Stop", mode="enforce", last_assistant_message="仍缺少提示词")["continue"])

    def test_batch2_advisory_downgrade_clears_drain_and_freeze(self):
        self.call("UserPromptSubmit", mode="enforce", prompt="请整理交接并停机")
        for phase in ("drain", "freeze"):
            with self.subTest(phase=phase):
                state = self.state()
                state["phase"] = phase
                self.gate.save_gate_state(self.state_dir, self.sid, state)
                self.assertEqual(self.call("PreToolUse", tool_name="spawn_agent"), {})
                self.assertEqual(self.state()["phase"], "advisory")
                self.call("UserPromptSubmit", turn_id=phase, prompt="请整理交接并停机")
                self.assertEqual(self.state()["phase"], "advisory")
                self.assertEqual(self.call("Stop", last_assistant_message="尚未交接"), {})

    def test_batch2_unchanged_readonly_agy_tools_do_not_save_gate_state(self):
        for phase in ("drain", "freeze"):
            cid = "agy-readonly-" + phase
            state = self.gate.default_gate_state(cid)
            state["phase"] = phase
            self.gate.save_gate_state(self.state_dir, cid, state)
            with mock.patch.object(self.gate, "save_gate_state", wraps=self.gate.save_gate_state) as save:
                calls = [(name, {}) for name in sorted(self.gate.AGY_INSPECTION_TOOLS)]
                calls += [(name, {"Path": str(self.root / "source.py")}) for name in ("read_file", "view_file")]
                for name, args in calls:
                    with self.subTest(phase=phase, tool=name):
                        self.assertIsNone(self.gate.check_pre_tool_use_locked({
                            "conversationId": cid, "toolCall": {"name": name, "args": args},
                        }, self.state_dir))
                save.assert_not_called()
                image = self.gate.check_pre_tool_use_locked({
                    "conversationId": cid, "toolCall": {"name": "read_file", "args": {"Path": "image.png"}},
                }, self.state_dir)
                self.assertEqual(image["decision"], "deny")
                save.assert_called_once()
            self.assertEqual(self.gate.load_gate_state(self.state_dir, cid)["imageReadCallCount"], 1)

    def test_batch2_compaction_metadata_schema_normalizes_legacy_none(self):
        cid = "agy-schema-none"
        expected = {"scannedTranscriptIdentity": [], "scannedTranscriptAnchor": "",
                    "scannedTranscriptMtimeNs": -1, "archiveComplete": False, "lastExplicitCompact": []}
        state = session_context_loader._default_compaction_state(cid)
        for key, value in expected.items():
            self.assertEqual(state[key], value)
            state[key] = None
        state.update(scannedByteOffset=123, lastInjectedCheckpointStep=17, hasExplicitUserInput=True)
        path = self.root / "legacy-compaction.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        with mock.patch.object(session_context_loader, "log_event") as log:
            loaded = session_context_loader._load_compaction_state(path, cid)
        log.assert_not_called()
        for key, value in expected.items():
            self.assertEqual(loaded[key], value)
        self.assertEqual(loaded["scannedByteOffset"], 123)
        self.assertEqual(loaded["lastInjectedCheckpointStep"], 17)
        self.assertTrue(loaded["hasExplicitUserInput"])

    def test_batch2_empty_transcript_cursor_stabilizes_after_truncate_and_replace(self):
        for kind in ("initial", "truncate", "replace"):
            with self.subTest(kind=kind):
                transcript = self.root / kind / "transcript.jsonl"
                transcript.parent.mkdir()
                transcript.write_text("", encoding="utf-8")
                state = session_context_loader._default_compaction_state("agy-empty-" + kind)
                if kind != "initial":
                    _append_jsonl(transcript, {"step_index": 99, "type": "CHECKPOINT"})
                    self.assertTrue(session_context_loader._scan_transcript_incrementally(transcript, state))
                    state["lastInjectedCheckpointStep"] = 99
                    if kind == "replace":
                        replacement = transcript.with_name("empty.jsonl")
                        replacement.write_text("", encoding="utf-8")
                        os.replace(replacement, transcript)
                    else:
                        transcript.write_text("", encoding="utf-8")
                self.assertTrue(session_context_loader._scan_transcript_incrementally(transcript, state))
                opened = transcript.stat()
                self.assertEqual(state["scannedByteOffset"], 0)
                self.assertEqual(state["scannedTranscriptName"], transcript.name)
                self.assertEqual(state["scannedTranscriptIdentity"], [opened.st_dev, opened.st_ino])
                self.assertEqual(state["scannedTranscriptAnchor"], hashlib.sha256(b"").hexdigest())
                self.assertEqual(state["scannedTranscriptMtimeNs"], opened.st_mtime_ns)
                self.assertEqual(state["lastInjectedCheckpointStep"], -1)
                before = dict(state)
                self.assertFalse(session_context_loader._scan_transcript_incrementally(transcript, state))
                self.assertEqual(state, before)

    def test_batch2_new_inode_reinjects_checkpoint_with_smaller_step_number(self):
        cid = "agy-new-inode"
        transcript = self.root / "transcript.jsonl"
        payload = {"conversationId": cid, "transcriptPath": str(transcript)}
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": "旧轨迹"})
        _append_jsonl(transcript, {"type": "CHECKPOINT", "step_index": 99})
        self.assertEqual(len(session_context_loader.handle_pre_invocation(payload)["injectSteps"]), 1)
        old_identity = [transcript.stat().st_dev, transcript.stat().st_ino]
        replacement = transcript.with_name("new-transcript.jsonl")
        _append_jsonl(replacement, {"type": "USER_INPUT", "step_index": 0, "content": "新轨迹的完整用户指令"})
        _append_jsonl(replacement, {"type": "CHECKPOINT", "step_index": 2})
        os.replace(replacement, transcript)
        self.assertNotEqual([transcript.stat().st_dev, transcript.stat().st_ino], old_identity)
        injected = session_context_loader.handle_pre_invocation(payload)["injectSteps"]
        self.assertEqual(len(injected), 1)
        self.assertIn("CHECKPOINT step #2", injected[0]["systemMessage"]["systemMessage"])
        self.assertIn("新轨迹的完整用户指令", injected[0]["systemMessage"]["systemMessage"])
        self.assertEqual(session_context_loader.handle_pre_invocation(payload)["injectSteps"], [])

    def test_batch2_subagent_task_preserves_literal_system_noise_and_chinese_goal_cancel(self):
        content = "请审查源码中的 <EPHEMERAL_MESSAGE> 与 <TASK_NOTIFICATION> 字面量。\n保留完整任务书。"
        raw = "<SYSTEM_MESSAGE>\n[Message] sender=coordinator content=" + content + "\n</SYSTEM_MESSAGE>"
        self.assertEqual(user_words.clean_user_content(raw, "SYSTEM_MESSAGE"), content)
        self.assertEqual(user_words.clean_user_content(raw.replace("sender=coordinator", "sender=system"), "SYSTEM_MESSAGE"), "")
        self.assertEqual(user_words.clean_user_content("<EPHEMERAL_MESSAGE>系统通知", "SYSTEM_MESSAGE"), "")
        transcript = self.root / "subagent.jsonl"
        _append_jsonl(transcript, {"type": "SYSTEM_MESSAGE", "step_index": 1, "content": raw})
        archive = user_words.extract_agy_archive(str(transcript))
        self.assertEqual([word["content"] for word in archive["words"]], [content])
        self.assertEqual(archive["words"][0]["kind"], "subagent_message")
        for text in ("取消/goal继续整理", "请停止goal继续核对", "关闭 /GOAL后交接"):
            with self.subTest(cancel=text):
                self.assertIs(user_words.agy_goal_command(text), False)
        for text in ("取消/goalkeeper", "取消/goal2", "取消/goal_next", "取消/goal-next"):
            with self.subTest(non_command=text):
                self.assertIsNone(user_words.agy_goal_command(text))

    def test_batch2_unidentified_question_only_applies_to_the_first_adjacent_result(self):
        for kind in ("answer", "error", "new_planner"):
            with self.subTest(kind=kind):
                transcript = self.root / ("question-" + kind + ".jsonl")
                _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "step_index": 1,
                                          "tool_calls": [{"name": "ask_question"}]})
                if kind == "new_planner":
                    _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "step_index": 2, "content": "开始读取日志"})
                else:
                    result = {"type": "GENERIC", "step_index": 2, "content": "User selected: 待核实"}
                    if kind == "error":
                        result["error"] = "tool failed"
                    _append_jsonl(transcript, result)
                _append_jsonl(transcript, {"type": "GENERIC", "step_index": 3, "content": "普通日志输出"})
                _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "step_index": 4, "content": "继续核对"})
                _append_jsonl(transcript, {"type": "GENERIC", "step_index": 5, "content": "另一条普通输出"})
                archive = user_words.extract_agy_archive(str(transcript))
                self.assertEqual(archive["unverified_answers"], [2] if kind == "answer" else [])
                self.assertEqual(archive["words"], [])

    @unittest.skipUnless(os.name == "nt", "仅 Windows 使用 msvcrt 字节范围锁")
    def test_batch2_windows_session_lock_explicitly_unlocks_on_normal_and_exceptional_exit(self):
        import msvcrt

        for raises in (False, True):
            with self.subTest(raises=raises), mock.patch.object(msvcrt, "locking", wraps=msvcrt.locking) as locking:
                if raises:
                    with self.assertRaisesRegex(RuntimeError, "transaction failed"):
                        with user_words.session_state_lock(self.state_dir, "explicit-unlock"):
                            raise RuntimeError("transaction failed")
                else:
                    with user_words.session_state_lock(self.state_dir, "explicit-unlock"):
                        pass
                self.assertEqual([call.args[1:] for call in locking.call_args_list],
                                 [(msvcrt.LK_NBLCK, 1), (msvcrt.LK_UNLCK, 1)])

    def test_batch2_chinese_adjacent_timestamps_normalize_without_losing_numeric_progress(self):
        import reply_repeat_guard

        base = reply_repeat_guard.normalize_reply_text("当前时间后台仍在运行，请稍候")
        for stamp in ("06:12:34", "06:12:35", "2026-11-14T06:12:34Z", "2026-11-14T06:12:35.123+08:00"):
            with self.subTest(stamp=stamp):
                self.assertEqual(reply_repeat_guard.normalize_reply_text("当前时间" + stamp + "后台仍在运行，请稍候"), base)
        first = reply_repeat_guard.normalize_reply_text("当前时间06:12:34后台仍在运行，迭代100步，残差1e-5")
        second = reply_repeat_guard.normalize_reply_text("当前时间06:12:35后台仍在运行，迭代101步，残差1e-6")
        self.assertEqual(reply_repeat_guard._extract_number_tokens(first), ["100", "1e-5"])
        self.assertEqual(reply_repeat_guard._extract_number_tokens(second), ["101", "1e-6"])
        self.assertFalse(reply_repeat_guard.are_replies_homogeneous(first, second))

    def test_batch2_stop_uses_reconciled_goal_cancellation_instead_of_old_prompt(self):
        import reply_repeat_guard

        reply = ("已验证：现有离线测试通过。\nGit 工作区：保留当前修改。\n未完成：真实宿主验收。\n"
                 "新对话提示词\n```text\n请读取用户原话和当前源码，核对工作区后继续真实宿主验收。\n```")
        for cancel_type in ("PLANNER_RESPONSE", "USER_INPUT"):
            with self.subTest(cancel_type=cancel_type):
                cid = "agy-stop-goal-" + cancel_type
                transcript = self.root / (cid + ".jsonl")
                first_input = "/goal 整理当前任务"
                _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": first_input})
                cancel = "已收尾。\n<!-- GOAL_CANCELLED -->" if cancel_type == "PLANNER_RESPONSE" else "取消/goal继续整理"
                _append_jsonl(transcript, {"type": cancel_type, "step_index": 1, "content": cancel})
                _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "step_index": 2, "content": reply})
                payload = {"conversationId": cid, "transcriptPath": str(transcript), "lastUserInput": first_input,
                           "terminationReason": "model_stop"}
                session_context_loader.handle_pre_invocation(payload)
                state = self.gate.load_gate_state(self.state_dir, cid)
                self.assertFalse(state["isGoalActive"])
                state["phase"] = "freeze"
                self.gate.save_gate_state(self.state_dir, cid, state)
                self.assertEqual(reply_repeat_guard.handle_stop_repeat_guard(payload), {"decision": "allow"})
                self.assertFalse(self.gate.load_gate_state(self.state_dir, cid)["isGoalActive"])

    def test_batch2_stop_retains_goal_prompt_fallback_without_transcript_state(self):
        import reply_repeat_guard

        cid = "agy-stop-no-transcript"
        state = self.gate.default_gate_state(cid)
        state["phase"] = "freeze"
        self.gate.save_gate_state(self.state_dir, cid, state)
        result = reply_repeat_guard.handle_stop_repeat_guard({
            "conversationId": cid, "lastUserInput": "/goal 继续整理", "terminationReason": "model_stop",
        })
        self.assertEqual(result["decision"], "continue")
        self.assertIn("GOAL_CANCELLED", result["reason"])

    def test_batch2_latest_handoff_reply_over_tail_window_is_read_completely(self):
        import reply_repeat_guard

        reply = ("已验证：当前回归测试通过。\nGit 工作区：保留修改。\n未完成：真实宿主核验。\n"
                 + "x" * (2 * reply_repeat_guard.TAIL_SCAN_BYTES + 31)
                 + "\n新对话提示词\n```text\n请先核对用户原话和工作区，继续完成真实宿主核验。\n```")
        for has_user in (False, True):
            for ending in (b"", b"\n", b"\n\n"):
                with self.subTest(has_user=has_user, ending=ending):
                    transcript = self.root / ("long-" + str(has_user) + "-" + str(len(ending)) + ".jsonl")
                    expected_turn = "turn_0_initial"
                    if has_user:
                        content = "整理交接"
                        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 3, "content": content})
                        expected_turn = "turn_3_" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
                    _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "step_index": 7, "content": reply})
                    transcript.write_bytes(transcript.read_bytes().rstrip(b"\n") + ending)
                    self.assertGreater(transcript.stat().st_size, reply_repeat_guard.TAIL_SCAN_BYTES)
                    self.assertEqual(reply_repeat_guard.extract_current_turn_latest_reply(str(transcript)),
                                     (expected_turn, 7, reply))

    def test_agy_compaction_recovers_truncated_recent_subagent_returns_and_skips_task_noise(self):
        cid = "agy-subagent-digest"
        log_dir = self.root / "brain" / cid / ".system_generated" / "logs"
        tp = log_dir / "transcript.jsonl"
        tp_full = log_dir / "transcript_full.jsonl"

        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
                           "created_at": "2026-11-15T01:00:00Z", "content": "<USER_REQUEST>\n核查通量守恒与边界条件\n</USER_REQUEST>"})
        _append_jsonl(tp_full, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
                                "created_at": "2026-11-15T01:00:00Z", "content": "<USER_REQUEST>\n核查通量守恒与边界条件\n</USER_REQUEST>"})

        # 噪声 1：后台命令完成通知 (/task-99) 与 BROWSER_HOST_FACTS 必须排除
        _append_jsonl(tp, {
            "step_index": 1, "source": "SYSTEM", "type": "SYSTEM_MESSAGE",
            "content": f"<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-15T01:01:00Z sender={cid}/task-99 content=Task id \"{cid}/task-99\" finished\n</SYSTEM_MESSAGE>",
        })
        _append_jsonl(tp, {
            "step_index": 2, "source": "SYSTEM", "type": "SYSTEM_MESSAGE",
            "content": "<SYSTEM_MESSAGE>\nBROWSER_HOST_FACTS sender=sub-browser content=origin ok\n</SYSTEM_MESSAGE>",
        })

        # 连续 5 个子代理完成返回：仅保留最近 4 个，且第 5 个在紧凑轨迹中被截断、需从 transcript_full.jsonl 补全首尾
        for idx in range(1, 6):
            sub_id = f"sub-auditor-000{idx}"
            call_step = idx * 10
            res_step = call_step + 1
            msg_step = call_step + 2
            _append_jsonl(tp, {
                "step_index": call_step, "source": "MODEL", "type": "PLANNER_RESPONSE",
                "tool_calls": [{
                    "id": f"call-{idx}", "name": "invoke_subagent",
                    "args": {"TypeName": '"readonly-auditor"', "Role": f'"Auditor #{idx}"'},
                }],
            })
            _append_jsonl(tp, {
                "step_index": res_step, "source": "MODEL", "type": "GENERIC", "tool_call_id": f"call-{idx}",
                "content": "Created the following subagent: " + json.dumps({
                    "conversationId": sub_id,
                    "logAbsoluteUri": f"file:///tmp/{sub_id}/transcript.jsonl",
                }),
            })
            full_report = (
                f"【核查范围 #{idx}】src/flux/hllc.py 第 10-88 行。\n"
                + ("中间推导细节_" * (180 if idx == 5 else 5))
                + f"\n【最终结论 #{idx}】第 {40 + idx} 行法向投影无误。"
            )
            full_wrapped = f"<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-15T01:0{idx}:00Z sender={sub_id} priority=HIGH content={full_report}\n</SYSTEM_MESSAGE>"
            if idx == 5:
                trunc_wrapped = f"<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-15T01:05:00Z sender={sub_id} priority=HIGH content=【核查范围 #5】截断预览..."
                _append_jsonl(tp, {
                    "step_index": msg_step, "source": "SYSTEM", "type": "SYSTEM_MESSAGE",
                    "created_at": "2026-11-15T01:05:00Z", "content": trunc_wrapped, "truncated_fields": ["content"],
                })
                _append_jsonl(tp_full, {
                    "step_index": msg_step, "source": "SYSTEM", "type": "SYSTEM_MESSAGE",
                    "created_at": "2026-11-15T01:05:00Z", "content": full_wrapped,
                })
            else:
                _append_jsonl(tp, {
                    "step_index": msg_step, "source": "SYSTEM", "type": "SYSTEM_MESSAGE",
                    "created_at": f"2026-11-15T01:0{idx}:00Z", "content": full_wrapped,
                })

        _append_jsonl(tp, {"step_index": 60, "source": "SYSTEM", "type": "CHECKPOINT", "content": "compacted"})
        payload = {"conversationId": cid, "transcriptPath": str(tp)}
        first_res = session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(len(first_res["injectSteps"]), 1)
        injected = first_res["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertLessEqual(len(injected), session_context_loader.MAX_INLINE_TOTAL_CHARS)
        self.assertIn("最近子代理返回摘要（采纳前须按 R6 核对原文件或子代理日志）", injected)
        self.assertNotIn("Auditor #1", injected)
        self.assertIn("readonly-auditor (Auditor #5)", injected)
        self.assertIn("【最终结论 #5】第 45 行法向投影无误。", injected)
        self.assertNotIn("task-99", injected)
        self.assertNotIn("BROWSER_HOST_FACTS", injected)

        # 未新增 CHECKPOINT 的后续回合必须严格零字节注入，保护前缀缓存命中率
        _append_jsonl(tp, {"step_index": 61, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "继续下一步"})
        second_res = session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(second_res, {"injectSteps": []})

        md_file = self.state_dir / f"{cid}_user_words.md"
        md_text = md_file.read_text(encoding="utf-8")
        self.assertIn("- **累计指令轮数**: 1", md_text)
        self.assertIn("- **最近子代理返回摘要数**: 4", md_text)
        self.assertIn("file:///tmp/sub-auditor-0005/transcript.jsonl", md_text)
        self.assertIn("【最终结论 #5】第 45 行法向投影无误。", md_text)

        # 多轮超长原话叠加子代理摘要时，总内联长度仍严格不超过 MAX_INLINE_TOTAL_CHARS，且首轮指令保留
        many_words = [
            {"turn": i, "step_index": i, "created_at": "2026-11-15T00:00:00Z", "kind": "user_input",
             "content": f"第{i}轮首锚点_" + ("超长约束条件_" * 180) + f"_第{i}轮尾锚点"}
            for i in range(1, 18)
        ]
        archive_data = user_words.extract_agy_archive(str(tp), include_subagent_msgs=False)
        capped = session_context_loader.build_compaction_system_message(
            many_words, md_file, "CHECKPOINT step #60", subagent_returns=archive_data["subagent_returns"],
        )
        self.assertLessEqual(len(capped), session_context_loader.MAX_INLINE_TOTAL_CHARS)
        self.assertIn("第1轮首锚点_", capped)
        self.assertIn("Auditor #5", capped)

    def test_codex_session_start_recovers_wait_agent_returns_and_deduplicates_segments(self):
        sid = "019codex-subagent-main"
        codex_root = self.root / ".codex"
        older = codex_root / "archived_sessions" / f"rollout-2026-11-15T01-00-00-{sid}.jsonl"
        active = codex_root / "sessions" / "2026" / "11" / "15" / f"rollout-2026-11-15T02-00-00-{sid}.jsonl"
        sub_rollout = codex_root / "sessions" / "2026" / "11" / "15" / "rollout-2026-11-15T01-05-00-019subagent001.jsonl"
        _append_jsonl(sub_rollout, {"type": "session_meta", "payload": {"id": "019subagent001", "source": "subagent"}})

        for seg in (older, active):
            _append_jsonl(seg, {"type": "session_meta", "payload": {"id": sid, "source": "cli", "cwd": str(self.root)}})
            _append_jsonl(seg, {
                "type": "response_item", "ordinal": 1, "timestamp": "2026-11-15T01:01:00Z",
                "payload": {
                    "type": "message", "role": "user", "id": "u-1", "content": "推导旋转网格度量恒等式",
                    "internal_chat_message_metadata_passthrough": {
                        "content_item_metadata": [{"provenance": {"type": "user"}}],
                    },
                },
            })
            # spawn_agent 仅返回句柄，不得误归档为子代理完成报告
            _append_jsonl(seg, {
                "type": "response_item", "ordinal": 2, "timestamp": "2026-11-15T01:02:00Z",
                "payload": {
                    "type": "function_call", "name": "functions.spawn_agent", "call_id": "call-spawn-1",
                    "arguments": json.dumps({"agent_type": "readonly-collaborator", "description": "GCL derivation"}),
                },
            })
            _append_jsonl(seg, {
                "type": "response_item", "ordinal": 3, "timestamp": "2026-11-15T01:02:01Z",
                "payload": {
                    "type": "function_call_output", "call_id": "call-spawn-1",
                    "output": json.dumps({"agent_id": "019subagent001", "nickname": "Gauss"}),
                },
            })
            _append_jsonl(seg, {
                "type": "response_item", "ordinal": 4, "timestamp": "2026-11-15T01:05:00Z",
                "payload": {
                    "type": "function_call", "name": "functions.wait_agent", "call_id": "call-wait-1",
                    "arguments": json.dumps({"ids": ["019subagent001"]}),
                },
            })
            _append_jsonl(seg, {
                "type": "response_item", "ordinal": 5, "timestamp": "2026-11-15T01:05:30Z",
                "payload": {
                    "type": "function_call_output", "call_id": "call-wait-1",
                    "output": json.dumps({
                        "status": {"019subagent001": {"completed": "离散几何守恒律（GCL）在三阶曲网格下残差为机器零。"}},
                        "timed_out": False,
                    }, ensure_ascii=False),
                },
            })

        ctx_state = codex_root / "hooks" / "state" / "session-context"
        res = session_context_loader.handle_codex_session_start(
            {"session_id": sid, "transcript_path": str(active), "source": "compact", "cwd": str(self.root)},
            state_dir_str=str(ctx_state),
            log_path_str=str(codex_root / "hooks" / "logs" / "session-context.jsonl"),
        )
        add_ctx = res["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(add_ctx), session_context_loader.MAX_INLINE_TOTAL_CHARS)
        self.assertIn("最近子代理返回摘要：1 条", add_ctx)
        self.assertIn("readonly-collaborator (Gauss)", add_ctx)
        self.assertIn("离散几何守恒律（GCL）", add_ctx)
        self.assertIn(str(sub_rollout), add_ctx)

        md_path = codex_root / "hooks" / "state" / "user-words" / f"{sid}.md"
        meta_path = codex_root / "hooks" / "state" / "user-words" / f"{sid}.json"
        manifest = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["sha256"], hashlib.sha256(md_path.read_bytes()).hexdigest())
        self.assertEqual(manifest["count"], 1)

    def test_dynamic_runtime_tunables_and_substantive_subagent_selection(self) -> None:
        # 1. 验证窗口内实质子代理筛选：最新 1 条无条件保留，前序高价值审查结论不被简短状态回执挤掉
        items = [
            {
                "step_index": 10,
                "agent_label": "readonly-auditor (Core Auditor #1)",
                "content": "完成 src/flux/hllc.py 第 12-88 行逐行审查：质量与总能离散守恒残差均为 1.1e-15，未见容差放宽。",
            },
            {
                "step_index": 20,
                "agent_label": "readonly-collaborator (Math Check #2)",
                "content": "完成三维曲网格离散几何守恒律（GCL）体积度量对称性推导，边界求积阶数需满足 p >= 2m。",
            },
            {"step_index": 30, "agent_label": "helper-ping-1", "content": "已收到。"},
            {"step_index": 40, "agent_label": "helper-ping-2", "content": "无异常。"},
            {"step_index": 50, "agent_label": "helper-ping-3", "content": "继续等待。"},
            {"step_index": 60, "agent_label": "helper-ping-4", "content": "状态正常。"},
        ]
        selected = user_words.select_recent_subagent_returns(
            items,
            {"recent_subagent_returns": 3, "recent_subagent_window": 10, "min_substantive_subagent_chars": 40},
        )
        self.assertEqual([it["step_index"] for it in selected], [10, 20, 60])

        # 2. 验证 CAE_FLEET_PROFILE 动态覆盖各端压缩恢复参数与钩子阈值，同时保留 mock.patch.object 优先级
        custom_profile = self.root / "custom_fleet_profile.json"
        custom_profile.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "runtime_tunables": {
                        "compaction_recovery": {
                            "defaults": {
                                "inline_total_chars": 5200,
                                "recent_subagent_returns": 2,
                                "recent_subagent_window": 8,
                                "min_substantive_subagent_chars": 48,
                                "inline_subagent_total_chars": 900,
                                "inline_subagent_item_chars": 220,
                            },
                            "per_client": {
                                "antigravity": {"inline_total_chars": 4800, "recent_subagent_returns": 2},
                                "codex": {"inline_total_chars": 6400, "recent_subagent_returns": 4},
                            },
                        },
                        "handoff_thresholds": {"antigravity": {"advisory_cp_threshold": 9, "drain_cp_threshold": 14}},
                        "loop_guards": {
                            "reply_repeat": {"consecutive_wait_limit": 4, "max_short_reply_chars": 260},
                            "status_guard": {"task_no_log_cooldown_sec": 42.0},
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"CAE_FLEET_PROFILE": str(custom_profile)}):
            ag_pol = user_words.get_compaction_tunables("antigravity")
            cx_pol = user_words.get_compaction_tunables("codex")
            self.assertEqual(ag_pol["inline_total_chars"], 4800)
            self.assertEqual(ag_pol["recent_subagent_returns"], 2)
            self.assertEqual(cx_pol["inline_total_chars"], 6400)
            self.assertEqual(cx_pol["recent_subagent_returns"], 4)

            handoff_gate._sync_handoff_tunables()
            self.assertEqual(handoff_gate.ADVISORY_CP_THRESHOLD, 9)
            self.assertEqual(handoff_gate.DRAIN_CP_THRESHOLD, 14)
            with mock.patch.object(handoff_gate, "ADVISORY_CP_THRESHOLD", 2):
                handoff_gate._sync_handoff_tunables()
                self.assertEqual(handoff_gate.ADVISORY_CP_THRESHOLD, 2)

            reply_repeat_guard._sync_repeat_tunables()
            self.assertEqual(reply_repeat_guard.CONSECUTIVE_WAIT_LIMIT, 4)
            self.assertEqual(reply_repeat_guard.MAX_SHORT_REPLY_CHARS, 260)

            status_guard._sync_status_tunables()
            self.assertEqual(status_guard.TASK_NO_LOG_COOLDOWN_SEC, 42.0)
        # 退出自定义环境变量后自动恢复默认快照校准值
        handoff_gate._sync_handoff_tunables()
        reply_repeat_guard._sync_repeat_tunables()
        status_guard._sync_status_tunables()
        self.assertEqual(handoff_gate.ADVISORY_CP_THRESHOLD, 3)
        self.assertEqual(reply_repeat_guard.CONSECUTIVE_WAIT_LIMIT, 3)

    def test_truncated_subagent_full_restore_fence_closure_and_stale_lock_sweep(self) -> None:
        import time

        # 1) Antigravity 子代理返回带 truncated_fields: ["content"] 时，先回查 transcript_full.jsonl 再做实质筛选，且无双重省略
        cid = "ag-trunc-subagent-01"
        log_dir = self.root / "brain" / cid / ".system_generated" / "logs"
        compact_tp = log_dir / "transcript.jsonl"
        full_tp = log_dir / "transcript_full.jsonl"
        sub_cid = "sub-auditor-trunc-01"
        sub_log_uri = f"file:///{(self.root / 'sub_log.jsonl').as_posix()}"

        _append_jsonl(compact_tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "请全面核对离散守恒"})
        _append_jsonl(
            compact_tp,
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"id": "call-sub-1", "name": "invoke_subagent", "args": {"TypeName": "readonly-auditor", "Role": "Flux Auditor"}}],
            },
        )
        created_json = json.dumps({"conversationId": sub_cid, "logAbsoluteUri": sub_log_uri})
        _append_jsonl(
            compact_tp,
            {"step_index": 2, "source": "SYSTEM", "type": "TOOL_RESULT", "id": "call-sub-1", "content": f"Created the following subagent:\n{created_json}"},
        )
        full_audit_report = (
            f"<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-15T01:02:00Z sender={sub_cid} content="
            + ("逐行核对 src/flux/hllc.py 第 10-120 行：质量、动量与总能通量守恒残差均小于 1e-15，边界反射对称无误。\n" * 8)
            + "</SYSTEM_MESSAGE>"
        )
        _append_jsonl(
            compact_tp,
            {
                "step_index": 3,
                "source": "SYSTEM_SDK",
                "type": "SYSTEM_MESSAGE",
                "content": f"<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-15T01:02:00Z sender={sub_cid} content=逐行核对<truncated>",
                "truncated_fields": ["content"],
            },
        )
        _append_jsonl(full_tp, {"step_index": 3, "source": "SYSTEM_SDK", "type": "SYSTEM_MESSAGE", "content": full_audit_report})
        # 追加 3 条简短回执，验证 step 3 还原全文后作为高价值审查报告仍被保留
        for idx in (4, 5, 6):
            _append_jsonl(
                compact_tp,
                {
                    "step_index": idx,
                    "source": "SYSTEM_SDK",
                    "type": "SYSTEM_MESSAGE",
                    "content": f"<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-15T01:03:0{idx}Z sender={sub_cid} content=状态正常 #{idx}。\n</SYSTEM_MESSAGE>",
                },
            )
        _append_jsonl(compact_tp, {"step_index": 7, "source": "SYSTEM", "type": "CHECKPOINT"})

        ag_state = self.root / "ag_plugin_data"
        with mock.patch.dict(os.environ, {"PLUGIN_DATA": str(ag_state)}):
            res = session_context_loader.handle_pre_invocation({"conversationId": cid, "transcriptPath": str(compact_tp)})
        injected = res["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("质量、动量与总能通量守恒残差均小于 1e-15", injected)
        self.assertLessEqual(injected.count("...[中间部分已省略"), 1)

        # 2) _close_unbalanced_fence 自动闭合奇数代码围栏且严格遵守 max_len
        unbalanced = "前缀说明\n```python\ndef solve():\n    return 42"
        closed = session_context_loader._close_unbalanced_fence(unbalanced, max_len=len(unbalanced) + 2)
        self.assertLessEqual(len(closed), len(unbalanced) + 2)
        self.assertEqual(session_context_loader._unclosed_markdown_fence(closed), "")

        # 3) sweep_stale_lock_files 清理过期空闲锁并保留新鲜锁与业务状态文件
        sweep_dir = self.root / "lock_sweep_sandbox"
        (sweep_dir / "handoff").mkdir(parents=True)
        stale_dot = sweep_dir / ".expired-session.lock"
        stale_legacy = sweep_dir / "handoff" / "session-legacy.json.lock"
        fresh_dot = sweep_dir / ".fresh-session.lock"
        keep_json = sweep_dir / "active_state.json"
        for p in (stale_dot, stale_legacy, fresh_dot, keep_json):
            p.write_text("x", encoding="utf-8")
        old_ts = time.time() - 10 * 86400
        os.utime(stale_dot, (old_ts, old_ts))
        os.utime(stale_legacy, (old_ts, old_ts))
        removed = user_words.sweep_stale_lock_files(sweep_dir)
        self.assertEqual(removed, 2)
        self.assertFalse(stale_dot.exists())
        self.assertFalse(stale_legacy.exists())
        self.assertTrue(fresh_dot.exists())
        self.assertTrue(keep_json.exists())


class TestHeavyWorkflowLifecycle(unittest.TestCase):
    """磁盘票据、命令行回执与三端生命周期协议的离线集成验收。"""

    def setUp(self):
        self.tmp = _new_temp_dir(prefix="workflow-lifecycle-")
        self.addCleanup(self.tmp.cleanup)
        sandbox_root = Path(self.tmp.name).resolve()
        self.root = sandbox_root / "workspace"
        self.root.mkdir(parents=True, exist_ok=True)
        self.fake_home = sandbox_root / "home"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.state = self.root / "state"
        self.sid = "fixture-session"
        clean_env = _scrub_host_env(
            HOME=str(self.fake_home),
            USERPROFILE=str(self.fake_home),
            CAE_PLAN_STATE_DIR="",
            USER_GUARD_STATE_DIR=str(self.state),
            PLUGIN_DATA=str(self.root / "plugin"),
            USER_GUARD_LOGS_DIR=str(self.root / "logs"),
            CODEX_HOME=str(self.root / "codex"),
            CLAUDE_CONFIG_DIR=str(self.root / "claude"),
            CAE_HARNESS_DEPTH="",
        )
        environment = mock.patch.dict(os.environ, clean_env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        home_patch = mock.patch.object(Path, "home", return_value=self.fake_home)
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def ticket(self, status="PENDING", tid="fixture-ticket", directory=None):
        import time
        streams = []
        for number, channel in enumerate(("native_subagent", "cae_harness_headless"), 1):
            streams.append({"stream_id": f"S{number}", "subtask": f"核对接口第{number}路", "target": ".",
                            "channel": channel, "client": "codex" if number == 1 else "claude",
                            "agent_type": "readonly-auditor", "model": f"fixture-model-{number}",
                            "model_display": f"fixture-model-{number}", "tier": "high", "context_window": 64000,
                            "headless": number == 2, "clean_room": number == 2, "access_mode": "只读",
                            "write_scope": [], "verify_cmd": "", "inline_readonly": number == 2,
                            "timeout": 120, "currently_available": True, "unavailable_reason": "",
                            "quota_scheme": "fixture", "quota_summary": "凭据就绪，额度未触发冷却",
                            "receipt_location": f"receipts/fixture-{number}.json"})
        ticket = {"ticket_id": tid, "session_id": self.sid, "status": status, "expires_at": time.time() + 3600,
                  "workflow_bg": "压缩前需要交叉核对当前接口。", "workflow_goal": "逐路只读核对并汇总依据。",
                  "workflow_impact": "只产生核查回执，工作区源码保持原状。", "streams": streams,
                  "capability_ledger": {"last_online_refresh_at": "2026-11-15T01:00:00Z",
                                        "local_bench_verified_at": "2026-11-15T02:00:00Z"}}
        path = (directory or self.state / "dispatch_plans") / (tid + ".json")
        user_words.atomic_write_json(path, ticket)
        user_words.atomic_write_json(path.parent / f"session_{self.sid}_pending.json",
                                     {"ticket_id": tid, "status": status, "ticket_file": str(path)})
        return path, ticket

    def native_payload(self, client):
        transcript = self.root / client / "fixture.jsonl"
        if client == "codex":
            _append_jsonl(transcript, {"type": "session_meta", "payload": {"id": self.sid, "source": "cli"}})
        else:
            _append_jsonl(transcript, {"type": "user", "sessionId": self.sid,
                                       "message": {"role": "user", "content": "继续核对接口。"}})
        return {"session_id": self.sid, "transcript_path": str(transcript), "cwd": str(self.root)}

    def test_ticket_discovery_priority_isolation_expiry_and_approved(self):
        override = self.root / "override"
        _, plugin = self.ticket(tid="plugin-ticket", directory=self.root / "plugin/state/dispatch_plans")
        _, local = self.ticket(tid="local-ticket")
        path, first = self.ticket(tid="override-ticket", directory=override)
        with mock.patch.dict(os.environ, {"CAE_PLAN_STATE_DIR": str(override)}):
            dirs = user_words.candidate_dispatch_plan_dirs(self.state)
            self.assertEqual(dirs[:2], [override.resolve(), (self.state / "dispatch_plans").resolve()])
            self.assertEqual(user_words.find_session_pending_ticket(self.sid, self.state), first)
            first["expires_at"] = 0
            user_words.atomic_write_json(path, first)
            self.assertEqual(user_words.find_session_pending_ticket(self.sid, self.state), local)
            self.assertIsNone(user_words.find_session_pending_ticket("../escape", self.state))
        _, approved = self.ticket(status="APPROVED", tid="approved-ticket")
        self.assertEqual(user_words.find_session_approved_tickets(self.sid, self.state), [approved])
        # 移走本地票据后，插件目录仍受 USER_GUARD_STATE_DIR 隔离，不回读其他宿主。
        (self.state / "dispatch_plans/local-ticket.json").unlink()
        self.assertIsNone(user_words.find_session_pending_ticket(self.sid, self.state))
        with mock.patch.dict(os.environ, {"USER_GUARD_STATE_DIR": ""}):
            self.assertEqual(user_words.find_session_pending_ticket(self.sid, self.root / "unused"), plugin)

    def test_completion_bills_use_exact_harness_schema_and_explicit_acknowledgement(self):
        entries = [{"run_id": "fixture-run", "reported_to_user": False, "all_succeeded": False,
                    "subcommand": "batch", "receipt_file": "receipts/fixture.json",
                    "streams": [{"stream_id": "S1", "engine": "claude:fixture-model", "model": "fixture-model",
                                 "succeeded": False, "status": "failed", "token_usage": {"total_tokens": 42}}],
                    "token_usage_summary": {"total_tokens": 42}},
                   {"run_id": "already-reported", "reported_to_user": True}]
        path = self.state / "heavy_completions" / (self.sid + ".json")
        user_words.atomic_write_json(path, entries)
        self.assertEqual(user_words.load_unreported_heavy_completions(self.sid, self.state), entries[:1])
        block = "\n".join(session_context_loader._workflow_recovery_blocks(self.sid, self.state))
        for value in ("【待汇报重负载完工账单】", "S1", "failed", "42", "receipts/fixture.json"):
            self.assertIn(value, block)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), entries, "恢复注入不能代替向用户报账")
        user_words.mark_heavy_completions_reported(self.sid, self.state)
        self.assertEqual(user_words.load_unreported_heavy_completions(self.sid, self.state), [])
        self.assertTrue(all(row["reported_to_user"] for row in json.loads(path.read_text(encoding="utf-8"))))

    def test_three_clients_archive_completed_harness_commands(self):
        for client in ("antigravity", "codex", "claude"):
            for command in ("task", "gate", "audit", "batch"):
                with self.subTest(client=client, command=command):
                    receipt = self.root / f"{client}-{command}-receipt.json"
                    result = {"status": "ok", "model": "fixture-model", "receipt_file": str(receipt),
                              "result": {"summary": "已完成接口核对，回执中保留逐项依据。"}}
                    user_words.atomic_write_json(receipt, result)
                    transcript = self.root / f"{client}-{command}.jsonl"
                    shell = f'python -B -X utf8 tools/cae_harness.py {command} --output "{receipt}"'
                    if client == "antigravity":
                        rows = [{"type": "PLANNER_RESPONSE", "tool_calls": [
                            {"id": "call-fixture", "name": "run_command", "args": {"CommandLine": shell}}]},
                            {"type": "GENERIC", "tool_call_id": "call-fixture", "content": json.dumps(result)}]
                    elif client == "codex":
                        rows = [{"type": "session_meta", "payload": {"id": self.sid, "source": "cli"}},
                                {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                 "call_id": "call-fixture", "arguments": json.dumps({"cmd": shell})}},
                                {"type": "response_item", "payload": {"type": "function_call_output",
                                 "call_id": "call-fixture", "output": json.dumps(result)}}]
                    else:
                        rows = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "call-fixture",
                                 "name": "Bash", "input": {"command": shell}}]}},
                                {"type": "user", "message": {"content": [{"type": "tool_result",
                                 "tool_use_id": "call-fixture", "content": json.dumps(result)}]}}]
                    for row in rows:
                        _append_jsonl(transcript, row)
                    archive = (user_words.extract_agy_archive(str(transcript)) if client == "antigravity" else
                               user_words.extract_codex_session_words(str(transcript), self.sid) if client == "codex" else
                               user_words.extract_claude_user_words(transcript))
                    returns = archive["subagent_returns"]
                    self.assertEqual(len(returns), 1)
                    self.assertIn("已完成接口核对", returns[0]["content"])
                    self.assertIn("fixture-model", returns[0]["label"])
                    self.assertEqual(returns[0]["log_path"], str(receipt))
                    self.assertEqual(archive["words"], [])

    def test_agy_background_notification_reads_task_log_without_new_user_boundary(self):
        transcript = self.root / ".system_generated/logs/transcript.jsonl"
        log = self.root / ".system_generated/tasks/task-fixture.log"
        user_words.atomic_write_text(log, json.dumps({"model": "fixture-model", "status": "ok",
                                                     "result": {"summary": "后台只读审查已结束，依据在回执。"}}))
        rows = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "继续检查。"},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {
                    "CommandLine": "python -B cae_harness.py audit"}}]},
                {"type": "GENERIC", "content": "Command is running in background with ID: task-fixture"},
                {"type": "SYSTEM_MESSAGE", "content": "<SYSTEM_MESSAGE>\n[Message] sender=fixture/task-fixture content=命令已完成\n</SYSTEM_MESSAGE>"}]
        for index, row in enumerate(rows):
            _append_jsonl(transcript, dict(row, step_index=index))
        archive = user_words.extract_agy_archive(str(transcript))
        self.assertEqual([item["content"] for item in archive["words"]], ["继续检查。"])
        self.assertEqual(len(archive["subagent_returns"]), 1)
        self.assertEqual(archive["subagent_returns"][0]["log_path"], str(log))
        self.assertIn("后台只读审查已结束", archive["subagent_returns"][0]["content"])
        self.assertEqual(user_words.clean_user_content(rows[-1]["content"], "SYSTEM_MESSAGE"), "")

    def test_harness_archive_excludes_launch_noise_and_keeps_timeout_salvage(self):
        transcript = self.root / ".system_generated/logs/transcript.jsonl"
        log = self.root / ".system_generated/tasks/task-salvage.log"
        user_words.atomic_write_text(log, '{"event":"preflight"}\n{"event":"process_started","pid":12}\n')
        _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "tool_calls": [
            {"id": "launch", "name": "run_command", "args": {"CommandLine": "python cae_harness.py followup"}},
        ]})
        _append_jsonl(transcript, {"type": "GENERIC", "tool_call_id": "launch",
                                   "content": "Tool is running as a background task. Task: task-salvage"})
        _append_jsonl(transcript, {"type": "SYSTEM_MESSAGE", "content": "[swarm] task-salvage 已启动"})
        self.assertEqual(user_words.extract_agy_archive(str(transcript))["subagent_returns"], [])

        receipt = self.root / "timeout-receipt.json"
        user_words.atomic_write_json(receipt, {
            "status": "partial", "session_id": "fixture-session",
            "timeout_salvage": {"provider_session_id": "timeout-parent", "last_messages": ["写入修正说明"],
                                "changed_files_on_disk": ["notes.md"]},
            "streams": [{"stream_id": "S1", "status": "timeout", "timeout_salvage": {
                "provider_session_id": "timeout-stream", "last_messages": ["完成接口边界核对"],
                "changed_files_on_disk": ["solver.py"],
            }}],
        })
        for content in (json.dumps({"receipt_file": str(receipt), "status": "partial"}),
                        {"error": "tool call denied by pre-tool hook"},
                        "Created At: now\nEncountered error in tool execution", "[swarm] 看板更新\n子任务已入队",
                        [{"type": "text", "text": "Tool is running as a background task"}]):
            _append_jsonl(transcript, {"type": "GENERIC", "tool_call_id": "launch", "content": content})
        archive = user_words.extract_agy_archive(str(transcript))
        self.assertEqual(len(archive["subagent_returns"]), 1)
        returned = archive["subagent_returns"][0]
        self.assertEqual(returned["receipt_file"], str(receipt))
        markdown = user_words.format_user_words_markdown(self.sid, archive["words"], subagent_returns=[returned])
        for value in ("timeout-parent", "写入修正说明", "notes.md", "timeout-stream", "完成接口边界核对", "solver.py"):
            self.assertIn(value, markdown)
        self.assertNotIn("看板更新", markdown)

    def test_native_returns_filter_async_deny_and_board_notifications(self):
        noises = ("Tool is running as a background task", '{"event":"process_started","pid":12}',
                  "tool call denied by pre-tool hook", "Encountered error in tool execution",
                  "[swarm] 子任务已入队\n[swarm] 看板已更新")
        completion = "process_started\nprocess_finished\n子代理核对已完成，结果保存在当前工作区。"
        for body in (*noises, completion):
            with self.subTest(body=body):
                useful = body == completion
                agy = user_words._extract_agy_subagent_return_body(f"[Message] sender=fixture-agent content={body}")
                self.assertEqual(agy[1], completion if useful else "")
                codex = user_words._extract_codex_completed_subagent_items(
                    {"tool": "wait_agent"}, {"status": {"fixture-agent": {"completed": body}}}, {}, [], set())
                self.assertEqual([entry[-1] for entry in codex], [completion] if useful else [])
                claude = user_words._extract_claude_subagent_returns_from_user({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "call-fixture", "content": body},
                ]}}, 1, {"call-fixture": {"sub_type": "fixture-agent"}}, self.root / "claude-trace.jsonl")
                self.assertEqual([entry["raw_body"] for entry in claude], [completion] if useful else [])

    def test_codex_async_poll_completion_is_correlated(self):
        payload = self.native_payload("codex")
        transcript = Path(payload["transcript_path"])
        rows = [{"type": "function_call", "name": "exec_command", "call_id": "start",
                 "arguments": json.dumps({"cmd": "python -B cae_harness.py task"})},
                {"type": "function_call_output", "call_id": "start", "output": json.dumps({
                    "session_id": 42, "output": "still running"})},
                {"type": "function_call", "name": "write_stdin", "call_id": "poll",
                 "arguments": json.dumps({"session_id": 42})},
                {"type": "function_call_output", "call_id": "poll", "output": json.dumps({
                    "exit_code": 0, "output": json.dumps({"status": "ok", "model": "fixture-model",
                                                           "result": {"summary": "异步完成的接口审查。"}})})}]
        for row in rows:
            _append_jsonl(transcript, {"type": "response_item", "payload": row})
        returns = user_words.extract_codex_session_words(str(transcript), self.sid)["subagent_returns"]
        self.assertEqual(len(returns), 1)
        self.assertIn("异步完成的接口审查", returns[0]["content"])

    def test_three_clients_restore_workflow_outside_regular_summary_budget(self):
        path, ticket = self.ticket()
        ticket["workflow_bg"] += "保留票据记录中的完整接口核对依据。" * 800
        user_words.atomic_write_json(path, ticket)
        expected = user_words.render_pending_workflow_block(ticket)
        self.assertGreater(len(expected), user_words.get_compaction_tunables("codex")["inline_total_chars"])
        user_words.atomic_write_json(self.state / "heavy_completions" / (self.sid + ".json"), [{
            "run_id": "unreported-fixture", "all_succeeded": True, "reported_to_user": False,
            "receipt_file": "receipts/finished.json", "streams": [{"stream_id": "S1", "succeeded": True,
                "status": "ok", "token_usage": {"total_tokens": 42}}], "token_usage_summary": {"total_tokens": 42}}])
        table_rows = [line for line in expected.splitlines() if line.startswith("|")]
        self.assertTrue(all(len(re.split(r"(?<!\\)\|", line)[1:-1]) == 3 for line in table_rows))
        self.assertEqual(expected.count("| 序号 | 参数项 | 当前值 |"), len(ticket["streams"]))
        for client in ("antigravity", "codex", "claude"):
            with self.subTest(client=client):
                if client == "antigravity":
                    transcript = self.root / "agy/transcript.jsonl"
                    _append_jsonl(transcript, {"type": "USER_INPUT", "source": "USER_EXPLICIT", "step_index": 0,
                                               "content": "继续逐项核对。"})
                    _append_jsonl(transcript, {"type": "CHECKPOINT", "step_index": 1, "content": "summary"})
                    response = session_context_loader.handle_session_start({"conversationId": self.sid,
                                                                           "transcriptPath": str(transcript)})
                    context = response["injectSteps"][0]["systemMessage"]["systemMessage"]
                else:
                    payload = dict(self.native_payload(client), source="compact")
                    handler = (session_context_loader.handle_codex_session_start if client == "codex" else
                               session_context_loader.handle_claude_session_start)
                    context = handler(payload, str(self.state), str(self.root / f"{client}.log"))["hookSpecificOutput"]["additionalContext"]
                self.assertIn(expected, context)
                self.assertIn("【待汇报重负载完工账单】", context)
                self.assertIn("receipts/finished.json", context)
                self.assertIn('"total_tokens": 42', context)
                self.assertLess(context.index("【系统原话回溯】"), context.index(expected))
                self.assertIn("\n\n---\n\n" + expected, context)
                for stream in ticket["streams"]:
                    self.assertIn(stream["channel"], context)
                    self.assertIn(stream["receipt_location"], context)
                self.assertNotIn("gpt-", expected)

    def test_renderer_preserves_disk_popup_and_fills_missing_fields(self):
        _, ticket = self.ticket()
        ticket["human_popup_markdown"] = "原票据弹框的专门说明。"
        ticket["handoff_prompt_block"] = "原票据接力要求。"
        popup = user_words.render_pending_workflow_block(ticket)
        handoff = user_words.render_pending_workflow_block(ticket, for_handoff=True)
        self.assertIn(ticket["human_popup_markdown"], popup)
        self.assertIn(ticket["handoff_prompt_block"], handoff)
        for block in (popup, handoff):
            for value in (ticket["workflow_bg"], ticket["workflow_goal"], ticket["workflow_impact"], "③ 暂缓并交接", "64000"):
                self.assertIn(value, block)
        missing = user_words.render_pending_workflow_block({"ticket_id": "missing", "workflow_background": "只有背景。"})
        self.assertIn("只有背景。", missing)
        self.assertIn("要做什么与后续计划：未记录", missing)

    def test_readonly_harness_commands_are_allowed_in_both_phases_for_all_clients(self):
        for phase in ("drain", "freeze"):
            for command in ("check-grounding --target . --strict", "assess --target .", "probe", "plan --target ."):
                shell = "python -B -X utf8 tools/cae_harness.py " + command
                self.assertEqual(handoff_gate._classify_run_command(shell), "readonly_inspect")
                self.assertEqual(handoff_gate._classify_run_command("tools/cae_harness.py " + command), "readonly_inspect")
                agy_state = handoff_gate.default_gate_state(self.sid)
                agy_state["phase"] = phase
                handoff_gate.save_gate_state(self.state, self.sid, agy_state)
                result = handoff_gate.check_pre_tool_use_locked({"conversationId": self.sid,
                    "toolCall": {"name": "run_command", "args": {"CommandLine": shell}}}, self.state)
                self.assertIsNone(result)
                for client in ("codex", "claude"):
                    payload = self.native_payload(client)
                    directory = self.state / client
                    handler = handoff_gate.handle_codex_handoff if client == "codex" else handoff_gate.handle_claude_handoff
                    handler(dict(payload, hook_event_name="UserPromptSubmit", prompt="请整理交接并停机"), directory, "enforce")
                    state = json.loads((directory / (self.sid + "_handoff_gate.json")).read_text(encoding="utf-8"))
                    state["phase"] = phase
                    handoff_gate.save_gate_state(directory, self.sid, state)
                    result = handler(dict(payload, hook_event_name="PreToolUse", tool_name="Bash",
                                          tool_input={"command": shell}), directory, "enforce")
                    self.assertEqual(result, {})
                    after = json.loads((directory / (self.sid + "_handoff_gate.json")).read_text(encoding="utf-8"))
                    self.assertEqual(after["drainActionsRemaining"], state["drainActionsRemaining"])
        self.assertEqual(handoff_gate._classify_run_command("python cae_harness.py task --prompt probe"), "blocked_always")
        self.assertEqual(handoff_gate._classify_run_command("python cae_harness.py batch"), "blocked_always")

    def test_agy_stop_requires_complete_pending_workflow_once_in_both_phases(self):
        for phase in ("drain", "freeze"):
            with self.subTest(phase=phase):
                path, ticket = self.ticket()
                state = handoff_gate.default_gate_state(self.sid)
                state["phase"] = phase
                handoff_gate.save_gate_state(self.state, self.sid, state)
                reply = "已验证：接口已核对。Git 工作区待提交。未完成：独立审查。\n【新对话提示词】\n请核对当前工作区并继续完成独立审查和回执核验。"
                result = handoff_gate.check_stop_handoff_locked({"conversationId": self.sid}, self.state, "fixture-turn", reply)
                self.assertEqual(result["decision"], "continue")
                handed = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(handed, dict(ticket, status="HANDED_OFF"))
                block = user_words.render_pending_workflow_block(handed, for_handoff=True)
                self.assertIn(block, result["reason"])
                self.assertEqual(handoff_gate.check_stop_handoff_locked({"conversationId": self.sid}, self.state,
                                  "fixture-turn", reply)["decision"], "allow", "同一回合只补正一次")
                self.assertEqual(handoff_gate.check_stop_handoff_locked({"conversationId": self.sid}, self.state,
                                  "fixture-corrected", reply + "\n" + block)["decision"], "allow")

    def test_native_stop_requires_pending_workflow_once_and_accepts_corrected_reply(self):
        for client in ("codex", "claude"):
            for phase in ("drain", "freeze"):
                with self.subTest(client=client, phase=phase):
                    path, ticket = self.ticket()
                    payload = self.native_payload(client)
                    directory = self.state / f"{client}-{phase}"
                    handler = handoff_gate.handle_codex_handoff if client == "codex" else handoff_gate.handle_claude_handoff
                    notice = handler(dict(payload, hook_event_name="UserPromptSubmit", prompt="请整理交接并停机"), directory, "enforce")
                    handed = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(handed, dict(ticket, status="HANDED_OFF"))
                    block = user_words.render_pending_workflow_block(handed, for_handoff=True)
                    self.assertIn(block, json.dumps(notice, ensure_ascii=False).replace("\\n", "\n"))
                    state_path = directory / (self.sid + "_handoff_gate.json")
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    state["phase"] = phase
                    handoff_gate.save_gate_state(directory, self.sid, state)
                    reply = "已验证：接口已核对。Git 工作区待提交。未完成：独立审查。\n【新对话提示词】\n请核对当前工作区并继续完成独立审查和回执核验。"
                    first = handler(dict(payload, hook_event_name="Stop", last_assistant_message=reply), directory, "enforce")
                    self.assertEqual(first["decision"], "block")
                    self.assertIn(block, first["reason"])
                    second = handler(dict(payload, hook_event_name="Stop", last_assistant_message=reply), directory, "enforce")
                    self.assertNotEqual(second.get("decision"), "block")
                    self.assertFalse(json.loads(state_path.read_text(encoding="utf-8"))["handoffRecorded"])
                    corrected = handler(dict(payload, hook_event_name="Stop", last_assistant_message=reply + "\n" + block), directory, "enforce")
                    self.assertNotEqual(corrected.get("decision"), "block")
                    self.assertTrue(json.loads(state_path.read_text(encoding="utf-8"))["handoffRecorded"])

    def test_stop_preserves_decision_placeholders_from_baseline_and_current_document(self):
        prefix = ("已验证：接口已核对。Git 工作区待提交。未完成：独立审查。\n"
                  "## 新对话提示词\n请读取会话记录，并根据已验证的接口证据完成交接。\n")
        confirmed = "采用方案甲。"
        for client, section, placeholder in (("antigravity", "已拍板", "[请在此填入]"),
                                              ("codex", "作者决定", "<待拍板>"),
                                              ("claude", "用户已确认", "[待确认]")):
            with self.subTest(client=client):
                project = self.root / f"project-{client}"
                project.mkdir()
                doc = project / "HANDOFF.md"
                doc.write_text(prefix + f"### {section}\n- {placeholder}\n", encoding="utf-8")
                state_dir = self.state / client
                promoted = prefix + f"### {section}\n- {confirmed}\n"
                retained = prefix + f"### 待用户拍板\n- {placeholder}\n"
                if client == "antigravity":
                    transcript = project / "transcript.jsonl"
                    _append_jsonl(transcript, {"type": "USER_INPUT", "content": "我确认采用方案甲。"})
                    state = handoff_gate.default_gate_state(self.sid)
                    state["phase"] = "freeze"
                    handoff_gate._record_handoff_baseline(state, doc, transcript)
                    handoff_gate.save_gate_state(state_dir, self.sid, state)
                    doc.write_text(promoted, encoding="utf-8")
                    payload = {"conversationId": self.sid, "cwd": str(project), "transcriptPath": str(transcript)}

                    def stop(reply):
                        return handoff_gate.check_stop_handoff_locked(payload, state_dir, "turn-1", reply)

                else:
                    payload = dict(self.native_payload(client), cwd=str(project))
                    handler = handoff_gate.handle_codex_handoff if client == "codex" else handoff_gate.handle_claude_handoff
                    handler(dict(payload, hook_event_name="UserPromptSubmit", prompt="请整理交接并停机"), state_dir, "enforce")

                    def stop(reply):
                        return handler(dict(payload, hook_event_name="Stop", last_assistant_message=reply), state_dir, "enforce")

                self.assertNotIn("reason", stop(retained), "保留决策占位符时允许交接")
                blocked = stop(promoted)
                self.assertEqual(blocked["decision"], "continue" if client == "antigravity" else "block")
                self.assertIn("保留待用户拍板状态", blocked["reason"])
                self.assertNotIn("reason", stop(promoted), "同一回合只补正一次")

    def test_only_later_human_decision_supports_filling_handoff_placeholder(self):
        payload = self.native_payload("codex")
        transcript = Path(payload["transcript_path"])
        state_dir = self.state / "handoff-decisions"
        handoff_gate.handle_codex_handoff(dict(payload, hook_event_name="UserPromptSubmit", prompt="请整理交接并停机"),
                                         state_dir, "enforce")
        prefix = ("已验证：接口已核对。Git 工作区待提交。未完成：独立审查。\n【新对话提示词】\n"
                  "```markdown\n请先对照原文核查本会话的裁定和接口证据。\n## 用户已确认\n- ")
        reply = prefix + "采用方案甲。\n```"
        doc = self.root / "HANDOFF.md"
        doc.write_text(prefix + "[待确认]\n```", encoding="utf-8")
        handoff_gate.handle_codex_handoff(dict(payload, hook_event_name="PreToolUse", tool_name="apply_patch",
                                              tool_input="*** Begin Patch\n*** Update File: HANDOFF.md\n@@\n+更新\n*** End Patch"),
                                         state_dir, "enforce")
        self.assertTrue(handoff_gate._pending_decision_promoted({}, reply, transcript, str(self.root)))
        doc.write_text(reply, encoding="utf-8")
        file_only = handoff_gate.handle_codex_handoff(
            dict(payload, hook_event_name="Stop", last_assistant_message="交接已写入 HANDOFF.md。"), state_dir, "enforce"
        )
        self.assertEqual(file_only["decision"], "block")
        self.assertIn("保留待用户拍板状态", file_only["reason"])
        state = json.loads((state_dir / f"{self.sid}_handoff_gate.json").read_text(encoding="utf-8"))
        state["stopCorrectionRequested"] = False
        user_words.atomic_write_json(state_dir / f"{self.sid}_handoff_gate.json", state)
        multi_reply = prefix + "采用方案甲；采用方案丙。\n```"
        for role, text in (("assistant", "我确认采用方案甲。"), ("user", "我确认采用方案乙。"),
                           ("user", "> 我确认采用方案甲。"), ("user", "我确认采用方案甲。")):
            with self.subTest(role=role, text=text):
                _append_jsonl(transcript, {"type": "response_item", "payload": {"type": "message", "role": role,
                                           "content": [{"type": "input_text", "text": text}]}})
                if role == "user" and text == "我确认采用方案甲。":
                    self.assertTrue(handoff_gate._pending_decision_promoted(state, multi_reply, transcript, str(self.root)))
                stop = handoff_gate.handle_codex_handoff(dict(payload, hook_event_name="Stop", last_assistant_message=reply),
                                                        state_dir, "enforce")
                state = json.loads((state_dir / f"{self.sid}_handoff_gate.json").read_text(encoding="utf-8"))
                grounded = role == "user" and text == "我确认采用方案甲。"
                self.assertEqual(state["handoffRecorded"], grounded)
                if grounded:
                    self.assertEqual(stop["continue"], False)

    def test_workflow_must_be_inside_prompt_or_a_changed_handoff_document(self):
        _, ticket = self.ticket()
        block = user_words.render_pending_workflow_block(ticket, for_handoff=True)
        reply = "已验证：接口已核对。Git 工作区待提交。未完成：独立审查。\n【新对话提示词】\n请核对当前工作区并继续完成独立审查和回执核验。"
        self.assertFalse(handoff_gate._workflow_handoff_present(block + "\n" + reply, [ticket]))
        self.assertFalse(handoff_gate._workflow_handoff_present(reply + "\n" + ticket["ticket_id"], [ticket]))
        document = self.root / "handoff.md"
        state = {}
        handoff_gate._record_handoff_baseline(state, document)
        document.write_text(reply, encoding="utf-8", newline="\n")
        self.assertFalse(handoff_gate._has_valid_handoff_file(state, [ticket]))
        document.write_text(reply + "\n" + block, encoding="utf-8", newline="\n")
        self.assertTrue(handoff_gate._has_valid_handoff_file(state, [ticket]))
        unchanged = {}
        handoff_gate._record_handoff_baseline(unchanged, document)
        self.assertFalse(handoff_gate._has_valid_handoff_file(unchanged, [ticket]))

    def test_vertical_workflow_requires_every_parameter_and_accepts_legacy_rows(self):
        _, ticket = self.ticket()
        for number, stream in enumerate(ticket["streams"], 1):
            stream.update(target=f"target-{number}", access_mode=f"access-{number}", write_scope=[f"scope-{number}.py"],
                          verify_cmd=f"verify-{number}", unavailable_reason=f"reason-{number}", currently_available=False)
        block = "【新对话提示词】\n" + user_words.render_pending_workflow_block(ticket, for_handoff=True)
        self.assertTrue(handoff_gate._workflow_handoff_present(block, [ticket]))
        lines = block.splitlines()
        rows = [line for line in lines if re.match(r"^\| (?:[1-9]|1[0-4]) \|", line)]
        self.assertEqual(len(rows), 28)
        columns = ["流号", "子任务与范围", "通道", "客户端及子代理类型", "模型名字", "挡位", "上下文窗口",
                   "是否无头", "是否净室", "访问模式、写入范围与验收命令", "内联与超时", "当前是否可用",
                   "本机配置额度类型与当前状态", "回执或子代理日志位置"]
        self.assertTrue(all(len(re.split(r"(?<!\\)\|", line)[1:-1]) == 3 for line in rows))
        for number in range(2):
            cells = [[part.strip() for part in re.split(r"(?<!\\)\|", row)[1:-1]] for row in rows[number * 14:(number + 1) * 14]]
            self.assertEqual([cell[0] for cell in cells], [str(i) for i in range(1, 15)])
            self.assertEqual([cell[1] for cell in cells], columns)
        for row in rows:
            with self.subTest(missing=row):
                self.assertFalse(handoff_gate._workflow_handoff_present(block.replace(row, "", 1), [ticket]))
        two_columns = re.sub(r"^(\| )\d+ \| ", r"\1", block, flags=re.MULTILINE)
        self.assertTrue(handoff_gate._workflow_handoff_present(two_columns, [ticket]))
        for stream in ticket["streams"]:
            for key in ("stream_id", "subtask", "target", "channel", "client", "agent_type", "model_display", "tier",
                        "context_window", "access_mode", "verify_cmd", "timeout", "unavailable_reason", "quota_scheme",
                        "quota_summary", "receipt_location"):
                with self.subTest(stream=stream["stream_id"], missing_value=key):
                    # Remove the value only from this stream's rows, leaving headings and all other streams intact.
                    start = block.index("| 1 | 流号 | " + stream["stream_id"])
                    end = block.index("| 14 | 回执或子代理日志位置 |", start)
                    end = block.index("\n", end)
                    detail = block[start:end].replace(str(stream[key]), "")
                    self.assertFalse(handoff_gate._workflow_handoff_present(block[:start] + detail + block[end:], [ticket]))
            for key, label in (("headless", "是否无头"), ("clean_room", "是否净室"),
                               ("inline_readonly", "内联与超时"), ("currently_available", "当前是否可用")):
                index = columns.index(label)
                row = rows[(0 if stream["stream_id"] == "S1" else 14) + index]
                boolean = "是" if stream[key] else "否"
                changed = row.replace("| " + boolean, "| 缺失") if key != "inline_readonly" else row.replace("内联：" + boolean, "内联：缺失")
                self.assertFalse(handoff_gate._workflow_handoff_present(block.replace(row, changed, 1), [ticket]))
            self.assertFalse(handoff_gate._workflow_handoff_present(block.replace(stream["write_scope"][0], ""), [ticket]))
        legacy = ["| " + " | ".join(columns) + " |", "|" + "---|" * 14]
        for number in range(2):
            values = [re.split(r"(?<!\\)\|", row)[-2].strip() for row in rows[number * 14:(number + 1) * 14]]
            legacy.append("| " + " | ".join(values) + " |")
        table_start = next(i for i, line in enumerate(lines) if line.startswith("#### 流"))
        ledger_start = next(i for i, line in enumerate(lines) if line.startswith("离线性能记录更新时间："))
        old_block = "\n".join(lines[:table_start] + legacy + lines[ledger_start:])
        self.assertTrue(handoff_gate._workflow_handoff_present(old_block, [ticket]))
        for stored_key, for_handoff in (("human_popup_markdown", False), ("handoff_prompt_block", True)):
            ticket[stored_key] = old_block
            rendered = user_words.render_pending_workflow_block(ticket, for_handoff=for_handoff)
            self.assertIn(old_block, rendered)
            self.assertEqual(rendered.count("| 序号 | 参数项 | 当前值 |"), 2)

    def test_complete_stored_popup_is_rendered_once(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("lifecycle_plan_renderer", HOOKS_DIR.parent / "skills/cae-harness/scripts/cae_harness.py")
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        _, ticket = self.ticket()
        ticket["streams"][0]["subtask"] = "核对 | 通量\n耦合接口"
        ticket["streams"][0]["receipt_location"] = "receipts/a|b.json"
        harness._render_plan(ticket)
        for for_handoff in (False, True):
            block = user_words.render_pending_workflow_block(ticket, for_handoff=for_handoff)
            stored = ticket["handoff_prompt_block" if for_handoff else "human_popup_markdown"]
            self.assertIn(stored, block)
            self.assertEqual(block.count("| 序号 | 参数项 | 当前值 |"), 2)
            self.assertIn(r"核对 \| 通量 耦合接口", block)
            self.assertIn(r"receipts/a\|b.json", block)
            self.assertEqual(block.count("工作流背景："), 1)
            self.assertEqual(block.count("恢复命令：plan --resume-ticket"), 1)
            self.assertTrue(all(len(re.split(r"(?<!\\)\|", row)[1:-1]) <= 4
                                for row in block.splitlines() if row.startswith("|")))
            self.assertTrue(handoff_gate._workflow_handoff_present("【新对话提示词】\n" + block, [ticket]))

    def test_original_harness_popup_layout_is_accepted_without_canonical_labels(self):
        _, ticket = self.ticket()
        block = user_words.render_pending_workflow_block(ticket, for_handoff=True)
        # 保留纵向 14 项明细，采用 cae_harness._render_plan 的标签与选项格式。
        original = (block.replace("要做什么与后续计划：", "要做什么：").replace("预计后果与边界影响：", "预计后果：")
                    .replace("离线性能记录更新时间：", "capability_ledger 离线性能记录更新时间：")
                    .replace("选项卡：", "选项：").replace("64000", "64000 tokens"))
        self.assertTrue(handoff_gate._workflow_handoff_present("【新对话提示词】\n" + original, [ticket]))
        self.assertTrue(handoff_gate._workflow_handoff_present("## 新对话提示词\n```text\n" + original + "\n```", [ticket]))
        self.assertFalse(handoff_gate._workflow_handoff_present("【新对话提示词】\n" + original.replace("receipts/fixture-2.json", ""), [ticket]))
        self.assertFalse(handoff_gate._workflow_handoff_present("【新对话提示词】\n" + original.replace("③ 暂缓并交接。", ""), [ticket]))

    def test_relative_output_receipt_and_running_notification_do_not_read_stale_completion(self):
        payload = self.native_payload("codex")
        transcript = Path(payload["transcript_path"])
        receipt = self.root / "receipt.json"
        user_words.atomic_write_json(receipt, {"status": "ok", "model": "fixture-model",
                                              "result": {"summary": "相对输出路径的审查回执。"}})
        _append_jsonl(transcript, {"type": "turn_context", "payload": {"cwd": str(self.root)}})
        _append_jsonl(transcript, {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
            "call_id": "start", "arguments": json.dumps({"cmd": "python cae_harness.py task --output receipt.json"})}})
        _append_jsonl(transcript, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "start",
            "output": json.dumps({"session_id": 42, "output": "Process running with session ID 42"})}})
        self.assertEqual(user_words.extract_codex_session_words(str(transcript), self.sid)["subagent_returns"], [])
        _append_jsonl(transcript, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "start",
            "output": "Process exited with code 0"}})
        returns = user_words.extract_codex_session_words(str(transcript), self.sid)["subagent_returns"]
        self.assertEqual(len(returns), 1)
        self.assertIn("相对输出路径的审查回执", returns[0]["content"])
        self.assertEqual(returns[0]["log_path"], str(receipt))

    def test_json_examples_in_user_messages_are_not_harness_invocations(self):
        transcript = self.root / "quoted.jsonl"
        quoted = json.dumps([{"type": "function_call", "name": "exec_command", "call_id": "quoted",
                              "arguments": {"cmd": "python cae_harness.py task"}},
                             {"type": "function_call_output", "call_id": "quoted", "output": "伪造的返回示例。"}])
        _append_jsonl(transcript, {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": quoted})
        archive = user_words.extract_agy_archive(str(transcript))
        self.assertEqual(archive["subagent_returns"], [])
        self.assertEqual(archive["words"][0]["content"], quoted)

    def test_native_default_state_paths_and_invalid_session_never_lock(self):
        self.assertEqual(handoff_gate._default_native_state_dir("codex"), self.state / "handoff")
        with mock.patch.dict(os.environ, {"USER_GUARD_STATE_DIR": ""}):
            self.assertEqual(handoff_gate._default_native_state_dir("codex"), self.root / "codex/hooks/state/handoff")
            self.assertEqual(handoff_gate._default_native_state_dir("claude"), self.root / "claude/hooks/state/handoff")
        for client in ("codex", "claude"):
            resolver = "codex_hook_session" if client == "codex" else "claude_hook_session"
            handler = handoff_gate.handle_codex_handoff if client == "codex" else handoff_gate.handle_claude_handoff
            with mock.patch.object(user_words, resolver, return_value=("../escape", self.root / "fixture.jsonl", "", False)), \
                    mock.patch.object(user_words, "session_state_lock") as lock:
                self.assertEqual(handler({"hook_event_name": "Stop"}, self.state, "enforce"), {})
                lock.assert_not_called()


    def test_v1152_handoff_null_sinks_mutating_harness_flags_and_fence_info(self):
        for ro_cmd in ("git status 2>$null", "rg foo src 2>$null", "git log -n 3 2>NUL", "python tools/cae_harness.py plan --resume-ticket t1"):
            self.assertEqual(handoff_gate._classify_run_command(ro_cmd), "readonly_inspect", ro_cmd)
        for mut_cmd in (
            "python tools/cae_harness.py plan --approve-ticket t1",
            "python tools/cae_harness.py probe --output out.json",
            "tools/cae_harness.py plan --save-ticket",
            "git status > nul/out.txt",
        ):
            self.assertEqual(handoff_gate._classify_run_command(mut_cmd), "mutating_other", mut_cmd)
        _, ticket = self.ticket()
        block = user_words.render_pending_workflow_block(ticket, for_handoff=True)
        wrapped = "## 新对话提示词\n````markdown\n````json\n{\"x\": 1}\n```\n### 补充上下文标题\n" + block + "\n````"
        self.assertTrue(handoff_gate._workflow_handoff_present(wrapped, [ticket]))

    def test_v1152_user_words_plan_dir_links_dict_scrub_and_lock_sweep_subdirs(self):
        import time
        # 1) _scrub_lifecycle_entry 同时脱敏结构化字典敏感键名与内联键值且保持合法 JSON
        secret_tok = "custom_router_" + "z" * 16
        scrubbed = user_words._scrub_lifecycle_entry({
            "summary": {"ANTHROPIC_AUTH_TOKEN": secret_tok, "CLAUDE_CODE_SKIP_BEDROCK_AUTH": "1", "input_tokens": 42},
            "raw": json.dumps({"api_key": secret_tok, "ok": True}),
        })
        self.assertEqual(scrubbed["summary"]["ANTHROPIC_AUTH_TOKEN"], "<redacted>")
        self.assertEqual(scrubbed["summary"]["CLAUDE_CODE_SKIP_BEDROCK_AUTH"], "1")
        self.assertEqual(scrubbed["summary"]["input_tokens"], 42)
        self.assertEqual(json.loads(scrubbed["raw"]), {"api_key": "<redacted>", "ok": True})
        # 2) sweep_stale_lock_files 覆盖 dispatch_plans 与 heavy_completions 子目录
        sweep_root = self.root / "sweep-v1152"
        for sub in ("dispatch_plans", "heavy_completions"):
            (sweep_root / sub).mkdir(parents=True, exist_ok=True)
            lk = sweep_root / sub / ".expired-item.lock"
            lk.write_text("x", encoding="utf-8")
            old_ts = time.time() - 10 * 86400
            os.utime(lk, (old_ts, old_ts))
        self.assertEqual(user_words.sweep_stale_lock_files(sweep_root), 2)
        # 3) candidate_dispatch_plan_dirs 拒绝末级软链同时兼容祖先软链目录
        real_base = self.root / "real-base"
        (real_base / "sub" / "plans").mkdir(parents=True)
        evil_state = self.root / "evil-state"
        evil_state.mkdir()
        anc_link = self.root / "anc-link"
        try:
            anc_link.symlink_to(real_base, target_is_directory=True)
            (evil_state / "dispatch_plans").symlink_to(real_base / "sub" / "plans", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"当前环境无符号链接创建权限: {exc}")
        cands = user_words.candidate_dispatch_plan_dirs(evil_state, (anc_link / "sub" / "plans",))
        self.assertEqual(cands[0], (real_base / "sub" / "plans").resolve())
        self.assertNotIn((real_base / "sub" / "plans").resolve(), user_words.candidate_dispatch_plan_dirs(evil_state))

    def test_v1153_h3_h9_handoff_proc_subst_control_flow_symlink_cwd_image_read_and_stop_fallback(self):
        # 1) H3: 进程替换 <(...) / >(...) 与前导控制流关键字分类
        self.assertEqual(
            handoff_gate._extract_cmd_substitutions('diff <(git show HEAD:a) >(cat) \'<(ignored)\' "<(ignored2)"'),
            ["git show HEAD:a", "cat"],
        )
        for blk_cmd in (
            "cat <(git push origin main)",
            "echo ok > >(git push origin main)",
            "if git push origin main; then echo ok; fi",
            "while true; do python cae_harness.py task --kind code; done",
            "if false; then echo a; elif cat plot.png; then echo b; fi",
        ):
            self.assertEqual(handoff_gate._classify_run_command(blk_cmd), "blocked_always", blk_cmd)
        for ro_cmd in (
            "cat <(git status)",
            "if git status; then git diff; fi",
        ):
            self.assertEqual(handoff_gate._classify_run_command(ro_cmd), "readonly_inspect", ro_cmd)
        self.assertEqual(handoff_gate._classify_run_command("cat <(rm -f tmp_out.txt)"), "mutating_other")

        # 2) H9-2: _claude_tool_kind 覆盖 dispatch_agent 与 Read 图片阻断
        self.assertEqual(handoff_gate._claude_tool_kind({"tool_name": "dispatch_agent", "tool_input": {}}), ("blocked_always", []))
        self.assertEqual(handoff_gate._claude_tool_kind({"tool_name": "Read", "tool_input": {"file_path": "fig/mesh.png"}}), ("blocked_always", []))
        self.assertEqual(handoff_gate._claude_tool_kind({"tool_name": "Read", "tool_input": {"file_path": "docs/notes.md"}}), ("readonly", []))

        # 3) H9-1: 软链 cwd 下 _claude_handoff_targets 与 _codex_handoff_patch_paths 兼容且防软链逃逸
        real_proj = self.root / "real_proj_h9"
        outside_dir = self.root / "outside_h9"
        real_proj.mkdir()
        outside_dir.mkdir()
        sym_proj = self.root / "sym_proj_h9"
        escape_link = real_proj / "escape_sub"
        symlink_supported = True
        try:
            sym_proj.symlink_to(real_proj, target_is_directory=True)
            escape_link.symlink_to(outside_dir, target_is_directory=True)
        except OSError:
            symlink_supported = False
        if symlink_supported:
            for raw_cand in ("handoff.md", str(sym_proj / "handoff.md"), str(real_proj / "handoff.md")):
                self.assertEqual(len(handoff_gate._claude_handoff_targets([raw_cand], str(sym_proj))), 1, raw_cand)
                patch_txt = f"*** Begin Patch\n*** Add File: {raw_cand}\n+ok\n*** End Patch\n"
                self.assertEqual(len(handoff_gate._codex_handoff_patch_paths(patch_txt, str(sym_proj))), 1, raw_cand)
            for bad_cand in ("../outside_h9/handoff.md", "escape_sub/handoff.md", str(sym_proj / "escape_sub" / "handoff.md")):
                self.assertEqual(handoff_gate._claude_handoff_targets([bad_cand], str(sym_proj)), [], bad_cand)
                bad_patch = f"*** Begin Patch\n*** Add File: {bad_cand}\n+bad\n*** End Patch\n"
                self.assertEqual(handoff_gate._codex_handoff_patch_paths(bad_patch, str(sym_proj)), [], bad_cand)

        # 4) H9-3: Stop 事件 last_assistant_message 为空时回退从 transcript 提取本回合最新助手交接回复
        valid_handoff_reply = (
            "已验证：全部离线单测通过。\nGit 工作区：干净。\n未完成：待下一会话继续基准测试。\n"
            "## 新对话提示词\n```markdown\n请读取当前仓库状态并继续执行后续基准测试。\n```"
        )
        cl_tp = self.root / "claude_stop_fallback.jsonl"
        _append_jsonl(cl_tp, {"type": "user", "uuid": "u-h9", "message": {"role": "user", "content": "/handoff"}})
        cl_state = self.root / "cl_handoff_state"
        handoff_gate.handle_claude_handoff(
            {"hook_event_name": "UserPromptSubmit", "session_id": "cl-h9-sid", "transcript_path": str(cl_tp), "prompt": "/handoff"},
            cl_state, mode="enforce", compact_limit=1,
        )
        _append_jsonl(cl_tp, {"type": "assistant", "uuid": "a-h9", "message": {"content": [{"type": "text", "text": valid_handoff_reply}]}})
        cl_stop = handoff_gate.handle_claude_handoff(
            {"hook_event_name": "Stop", "session_id": "cl-h9-sid", "transcript_path": str(cl_tp), "last_assistant_message": ""},
            cl_state, mode="enforce", compact_limit=1,
        )
        self.assertEqual(cl_stop, {})
        self.assertTrue(json.loads((cl_state / "cl-h9-sid_handoff_gate.json").read_text(encoding="utf-8"))["handoffRecorded"])

        cx_tp = self.root / "codex_stop_fallback.jsonl"
        _append_jsonl(cx_tp, {"type": "session_meta", "payload": {"id": "cx-h9-sid", "source": "cli", "cwd": str(real_proj)}})
        _append_jsonl(cx_tp, {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "/handoff"}]}})
        cx_state = self.root / "cx_handoff_state"
        handoff_gate.handle_codex_handoff(
            {"hook_event_name": "UserPromptSubmit", "session_id": "cx-h9-sid", "transcript_path": str(cx_tp), "prompt": "/handoff"},
            cx_state, mode="enforce", compact_limit=1,
        )
        _append_jsonl(cx_tp, {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": valid_handoff_reply}]}})
        cx_stop = handoff_gate.handle_codex_handoff(
            {"hook_event_name": "Stop", "session_id": "cx-h9-sid", "transcript_path": str(cx_tp)},
            cx_state, mode="enforce", compact_limit=1,
        )
        self.assertIs(cx_stop.get("continue"), False)
        self.assertTrue(json.loads((cx_state / "cx-h9-sid_handoff_gate.json").read_text(encoding="utf-8"))["handoffRecorded"])

    def test_v1153_h5_h8_h10_lock_reverify_log_symlink_bom_state_and_scrub_secrets(self):
        # 1) H5-1: session_state_lock 在获得 flock / locking 之后复核 (st_dev, st_ino) 与 st_nlink==1，
        # 若并发 _try_unlink_unheld_lock 在 open 与加锁之间删除并替换了锁文件，自动解锁重开新锁
        lock_dir = self.root / "lock_race_dir"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / ".race-sid.lock"
        lock_file.write_text("x", encoding="utf-8")
        first_ino = lock_file.lstat().st_ino
        unlinked_at_lock = {"done": False}

        if os.name != "nt":
            import fcntl
            orig_flock = fcntl.flock
            posix_race = {"lock_calls": 0}

            def racing_flock(fd, op):
                if op & fcntl.LOCK_EX:
                    posix_race["lock_calls"] += 1
                if not unlinked_at_lock["done"] and (op & fcntl.LOCK_EX):
                    unlinked_at_lock["done"] = True
                    lock_file.unlink(missing_ok=True)
                return orig_flock(fd, op)

            lock_ctx = mock.patch("fcntl.flock", side_effect=racing_flock)
        else:
            import msvcrt
            orig_locking = msvcrt.locking
            orig_lstat = Path.lstat
            win_race = {"locked_once": False, "dropped_once": False, "unlocked": False}

            def racing_locking(fd, mode, nbytes):
                res = orig_locking(fd, mode, nbytes)
                if mode == msvcrt.LK_NBLCK:
                    win_race["locked_once"] = True
                elif mode == msvcrt.LK_UNLCK and win_race["dropped_once"]:
                    win_race["unlocked"] = True
                return res

            def racing_lstat(path_self):
                if path_self == lock_file and win_race["locked_once"] and not win_race["dropped_once"]:
                    win_race["dropped_once"] = True
                    raise FileNotFoundError(str(path_self))
                return orig_lstat(path_self)

            lock_ctx = contextlib.ExitStack()
            lock_ctx.enter_context(mock.patch("msvcrt.locking", side_effect=racing_locking))
            lock_ctx.enter_context(mock.patch.object(Path, "lstat", autospec=True, side_effect=racing_lstat))

        with lock_ctx:
            with user_words.session_state_lock(lock_dir, "race-sid"):
                self.assertTrue(lock_file.is_file(), "加锁后复核发现被 unlink 必须重开并锚定新锁文件")
                self.assertEqual(lock_file.lstat().st_nlink, 1)
                if os.name != "nt":
                    self.assertGreaterEqual(posix_race["lock_calls"], 2)
                    self.assertNotEqual(lock_file.read_bytes(), b"x")
                else:
                    self.assertTrue(win_race["dropped_once"] and win_race["unlocked"])

        # 2) H5-2: append_jsonl_log 持锁后拒绝写入软链日志文件
        logs_dir = self.root / "logs_symlink_guard"
        logs_dir.mkdir(parents=True, exist_ok=True)
        victim = self.root / "victim_outside.txt"
        victim.write_text("KEEP_INTACT\n", encoding="utf-8")
        sym_log = logs_dir / "lifecycle.log"
        symlink_ok = True
        try:
            sym_log.symlink_to(victim)
        except OSError:
            symlink_ok = False
        if symlink_ok:
            user_words.append_jsonl_log(sym_log, {"event": "test", "message": "should_not_write"})
            self.assertEqual(victim.read_text(encoding="utf-8"), "KEEP_INTACT\n")

        # 3) H8: extract_agy_archive 兼容嵌套 JSON 与尾随花括号的子代理创建回执
        agy_tp = self.root / "agy_nested_sub.jsonl"
        _append_jsonl(agy_tp, {"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "开始审查"})
        _append_jsonl(agy_tp, {"step_index": 1, "type": "PLANNER_RESPONSE", "tool_calls": [
            {"id": "call-n1", "name": "invoke_subagent", "args": {"TypeName": "readonly-auditor", "Role": "Mesh Auditor"}},
        ]})
        _append_jsonl(agy_tp, {"step_index": 2, "type": "GENERIC", "tool_result": {"call_id": "call-n1"},
                               "content": 'Created the following subagent: {"conversationId": "sub-nested-99", "extra": {"a": {"b": 1}}, "logAbsoluteUri": "/tmp/s99.jsonl"} status={spawned}'})
        _append_jsonl(agy_tp, {"step_index": 3, "type": "SYSTEM_MESSAGE",
                               "content": "<SYSTEM_MESSAGE>\n[Message] sender=sub-nested-99 content=已完成全部网格法向核对并确认零穿透误差。\n</SYSTEM_MESSAGE>"})
        arch = user_words.extract_agy_archive(str(agy_tp), include_subagent_msgs=False)
        self.assertEqual(len(arch["subagent_returns"]), 1)
        self.assertIn("readonly-auditor (Mesh Auditor)", arch["subagent_returns"][0]["label"])

        # 4) H10: _read_state_json 支持 utf-8-sig BOM；_scrub_lifecycle_entry 支持转义引号、*_TOKEN、*_SECRET_ACCESS_KEY、PASSWORD/PASSWD、Basic 头与 tuple 递归
        bom_json = self.root / "bom_state.json"
        bom_json.write_bytes("\ufeff".encode("utf-8") + json.dumps({"ok": True, "v": 7}).encode("utf-8"))
        self.assertEqual(user_words._read_state_json(bom_json), {"ok": True, "v": 7})

        sec = "secretVal_" + ("k" * 12)
        scrubbed = user_words._scrub_lifecycle_entry({
            "escaped_json": f'{{\\"api_key\\": \\"{sec}\\", \\"ok\\": true}}',
            "env_line": f"GITHUB_TOKEN={sec} AWS_SECRET_ACCESS_KEY={sec} PASSWORD={sec} PASSWD={sec} PWD=/tmp/work",
            "auth_header": "Authorization: Basic dXNlcjpzdXBlcnNlY3JldA==",
            "dict_pw": {"db_passwd": sec, "input_tokens": 128},
            "tuple_payload": (f"HF_TOKEN={sec}", {"secret_access_key": sec}),
        })
        self.assertNotIn(sec, json.dumps(scrubbed))
        self.assertIn('\\"api_key\\": \\"<redacted>\\"', scrubbed["escaped_json"])
        self.assertIn("PWD=/tmp/work", scrubbed["env_line"])
        self.assertNotIn("dXNlcjpzdXBlcnNlY3JldA==", scrubbed["auth_header"])
        self.assertEqual(scrubbed["dict_pw"], {"db_passwd": "<redacted>", "input_tokens": 128})
        self.assertIsInstance(scrubbed["tuple_payload"], tuple)
        self.assertEqual(scrubbed["tuple_payload"], ("HF_TOKEN=<redacted>", {"secret_access_key": "<redacted>"}))

    def test_r2_s2_01_codex_huge_bom_rollout_meta_and_agent_role_subagent(self):
        # 1) >64KB 首行 session_meta + UTF-8 BOM 流式读取识别，以及 >8MB 无换行超长安全拒绝
        big_tp = self.root / "rollout-huge-bom.jsonl"
        huge_instructions = "A" * (160 * 1024)
        meta_line = json.dumps({
            "type": "session_meta",
            "payload": {
                "id": "codex-huge-ro",
                "cwd": str(self.root),
                "source": "cli",
                "agent_role": "readonly-auditor",
                "base_instructions": huge_instructions,
            },
        }) + "\n"
        big_tp.write_bytes("\ufeff".encode("utf-8") + meta_line.encode("utf-8"))

        meta, err = user_words.read_codex_rollout_meta(big_tp)
        self.assertIsNone(err)
        self.assertEqual(meta["id"], "codex-huge-ro")
        self.assertEqual(meta["source_kind"], "subagent")
        self.assertEqual(
            status_guard._codex_readonly_subagent_role({"_client": "codex", "transcript_path": str(big_tp)}),
            "readonly-auditor",
        )

        nick_tp = self.root / "rollout-nick.jsonl"
        _append_jsonl(nick_tp, {
            "type": "session_meta",
            "payload": {"id": "codex-nick-sub", "cwd": str(self.root), "source": "cli", "agent_nickname": "Gauss"},
        })
        nick_meta, nick_err = user_words.read_codex_rollout_meta(nick_tp)
        self.assertIsNone(nick_err)
        self.assertEqual(nick_meta["source_kind"], "subagent")

        _, limit_exceeded = user_words.read_bounded_first_line(big_tp, max_bytes=1024)
        self.assertTrue(limit_exceeded)

    def test_r2_s2_02_loader_mtime_stability_subagent_budget_bad_json_and_startup_sweep(self):
        import time

        # 1) [st_dev, st_ino]、file_size 与末尾 anchor 均未变时忽略单纯 mtime_ns 变化
        cid = "agy-mtime-stable"
        tp = self.root / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "保持守恒性"})
        _append_jsonl(tp, {"step_index": 5, "source": "SYSTEM", "type": "CHECKPOINT"})
        payload = {"conversationId": cid, "transcriptPath": str(tp)}
        first = session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(len(first["injectSteps"]), 1)
        st = tp.stat()
        os.utime(tp, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        second = session_context_loader.handle_pre_invocation(payload)
        self.assertEqual(second["injectSteps"], [], "单纯 mtime_ns 变化不得重置 lastInjectedCheckpointStep 重复注入")

        # 2) 超预算时先压缩 words_rendered 确保保留 subagent_section
        huge_first_word = [{"turn": 1, "step_index": 0, "created_at": "2026-11-17T00:00:00Z", "kind": "user_input", "content": "首轮超长指令_" * 900}]
        sub_returns = [{"step_index": 12, "label": "readonly-auditor (KeyCheck)", "log_path": "/tmp/sub.jsonl", "content": "关键审查结论：离散通量零泄漏。"}]
        rendered = session_context_loader.build_compaction_system_message(
            huge_first_word, self.root / "archive.md", "CHECKPOINT step #5",
            subagent_returns=sub_returns,
            policy={
                "inline_total_chars": 1600,
                "inline_user_item_chars": 1500,
                "inline_subagent_total_chars": 500,
                "inline_subagent_item_chars": 240,
                "inline_guaranteed_head_turns": 1,
            },
        )
        self.assertLessEqual(len(rendered), 1600)
        self.assertIn("readonly-auditor (KeyCheck)", rendered)
        self.assertIn("关键审查结论：离散通量零泄漏。", rendered)

        # 3) claude/codex 遇损坏 stdin JSON 输出 {} 并以 0 退出；codex 异常同样截断至 inline_total_chars
        for client, expected_label in (("claude", "Claude"), ("codex", "Codex")):
            proc = subprocess.run(
                [sys.executable, "-B", str(HOOKS_DIR / "session_context_loader.py"), "--client", client],
                input="{corrupted-stdin-json",
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn(expected_label, proc.stderr)

        # 4) SessionStart(startup) 早退前执行 sweep_stale_lock_files
        plugin_state, _ = session_context_loader.get_data_dirs()
        plugin_state.mkdir(parents=True, exist_ok=True)
        stale_lock = plugin_state / ".expired-startup.lock"
        stale_lock.write_text("x", encoding="utf-8")
        old_ts = time.time() - 10 * 86400
        os.utime(stale_lock, (old_ts, old_ts))
        (plugin_state / ".lock_sweep.stamp").unlink(missing_ok=True)
        res_start = session_context_loader.handle_session_start({"conversationId": "startup-sid", "source": "startup"})
        self.assertEqual(res_start, {"injectSteps": []})
        self.assertFalse(stale_lock.exists(), "SessionStart(startup) 早退前应清理陈旧锁文件")

        small_profile = self.root / "tiny_codex_cap.json"
        small_profile.write_text(json.dumps({
            "schema_version": 1,
            "runtime_tunables": {"compaction_recovery": {"per_client": {"codex": {"inline_total_chars": 600}}}},
        }), encoding="utf-8")
        sym_bad_state = self.root / "sym_bad_state"
        outside_target = self.root / "outside_target"
        outside_target.mkdir(exist_ok=True)
        try:
            sym_bad_state.symlink_to(outside_target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"当前环境无符号链接创建权限: {exc}")
        codex_tp = self.root / "codex_err_cap.jsonl"
        _append_jsonl(codex_tp, {"type": "session_meta", "payload": {"id": "cx-cap-sid", "source": "cli", "cwd": str(self.root)}})
        proc_err = subprocess.run(
            [sys.executable, "-B", str(HOOKS_DIR / "session_context_loader.py"), "--client", "codex",
             "--state-dir", str(sym_bad_state / "session-context")],
            input=json.dumps({"session_id": "cx-cap-sid", "transcript_path": str(codex_tp), "source": "compact_" + ("S" * 900)}),
            env={**os.environ, "CAE_FLEET_PROFILE": str(small_profile)},
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc_err.returncode, 0, proc_err.stderr)
        err_ctx = json.loads(proc_err.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(len(err_ctx), 600)

    def test_r2_s2_03_claude_task_notification_filtered_from_words_and_turn_key(self):
        cl_tp = self.root / "claude_task_notif.jsonl"
        _append_jsonl(cl_tp, {"type": "user", "uuid": "u-real-1", "message": {"role": "user", "content": "启动后台收敛测试"}})
        _append_jsonl(cl_tp, {"type": "assistant", "uuid": "a-wait-1", "message": {"content": [{"type": "text", "text": "后台正在运行，请稍候。"}]}})
        t1, _, _, _ = reply_repeat_guard._claude_turn_reply({"session_id": "cl-tn-1", "transcript_path": str(cl_tp)})
        _append_jsonl(cl_tp, {
            "type": "user",
            "uuid": "u-notif-1",
            "message": {"role": "user", "content": "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>"},
        })
        _append_jsonl(cl_tp, {"type": "assistant", "uuid": "a-wait-2", "message": {"content": [{"type": "text", "text": "后台正在运行，请稍候。"}]}})
        t2, _, _, _ = reply_repeat_guard._claude_turn_reply({"session_id": "cl-tn-1", "transcript_path": str(cl_tp)})
        self.assertEqual(t1, t2, "<task-notification> 不得重置人类回合 turn_key")
        extracted = user_words.extract_claude_user_words(cl_tp)
        self.assertEqual([w["content"] for w in extracted["words"]], ["启动后台收敛测试"])

    def test_r2_s2_04_handoff_gate_redirs_quoted_gt_subdir_reply_stop_coord_and_parent_symlink_lock(self):
        # 1) >&2, 1>&2, 2>>/dev/null 标准流/空设备重定向放行
        for ro_cmd in ("echo check >&2", "git status 1>&2", "git diff 2>>/dev/null"):
            self.assertEqual(handoff_gate._classify_run_command(ro_cmd), "readonly_inspect", ro_cmd)

        # 2) _claude_tool_kind 先屏蔽引号再扫 >，且 Claude/Codex 均支持 handoff/*.md 子目录
        proj = self.root / "proj_r2_hg"
        proj.mkdir()
        kind_ok, targets_ok = handoff_gate._claude_tool_kind({
            "tool_name": "Bash",
            "cwd": str(proj),
            "tool_input": {"command": 'echo "step 1 -> step 2 > /outside/ignored" > handoff/summary.md'},
        })
        self.assertEqual(kind_ok, "handoff_document")
        self.assertEqual(len(targets_ok), 1)
        kind_bad, _ = handoff_gate._claude_tool_kind({
            "tool_name": "Bash",
            "cwd": str(proj),
            "tool_input": {"command": 'echo "> handoff/summary.md" && rm -rf build'},
        })
        self.assertEqual(kind_bad, "mutating_other")
        patch_sub = "*** Begin Patch\n*** Add File: handoff/notes.md\n+handoff body\n*** End Patch\n"
        self.assertEqual(
            handoff_gate._codex_tool_kind({"tool_name": "apply_patch", "cwd": str(proj), "tool_input": {"patch": patch_sub}})[0],
            "handoff_document",
        )
        # _codex_tool_kind 纳入 shell / shell_command 列表态
        self.assertEqual(
            handoff_gate._codex_tool_kind({"tool_name": "functions.shell", "tool_input": {"command": ["git", "push", "origin", "main"]}})[0],
            "blocked_always",
        )
        self.assertEqual(
            handoff_gate._codex_tool_kind({"tool_name": "shell_command", "tool_input": {"command": ["git", "status"]}})[0],
            "readonly_inspect",
        )

        # 3) _extract_native_turn_latest_reply：连续纯文本拼接、tool_use 中断态判空、Codex local_shell_call/web_search_call 重置
        cl_stream = self.root / "cl_stream_reply.jsonl"
        _append_jsonl(cl_stream, {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "整理交接"}})
        _append_jsonl(cl_stream, {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "已验证：单测全绿。\nGit 工作区：干净。"}]}})
        _append_jsonl(cl_stream, {"type": "assistant", "uuid": "a2", "message": {"content": [{"type": "text", "text": "未完成：待上线。\n## 新对话提示词\n请继续完成上线验收与监控核对。"}]}})
        merged_cl = handoff_gate._extract_native_turn_latest_reply(cl_stream, "claude")
        self.assertTrue(handoff_gate._handoff_structure_present(merged_cl))
        _append_jsonl(cl_stream, {"type": "assistant", "uuid": "a3", "message": {"content": [
            {"type": "text", "text": "正在调用工具检查..."},
            {"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {"command": "git status"}},
        ]}})
        self.assertEqual(handoff_gate._extract_native_turn_latest_reply(cl_stream, "claude"), "")

        cx_stream = self.root / "cx_stream_reply.jsonl"
        _append_jsonl(cx_stream, {"type": "session_meta", "payload": {"id": "cx-s1", "source": "cli", "cwd": str(proj)}})
        _append_jsonl(cx_stream, {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "第一段"}]}})
        _append_jsonl(cx_stream, {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "第二段"}]}})
        self.assertEqual(handoff_gate._extract_native_turn_latest_reply(cx_stream, "codex"), "第一段\n第二段")
        for reset_type in ("local_shell_call", "web_search_call"):
            tp_r = self.root / f"cx_{reset_type}.jsonl"
            tp_r.write_bytes(cx_stream.read_bytes())
            _append_jsonl(tp_r, {"type": "response_item", "payload": {"type": reset_type}})
            self.assertEqual(handoff_gate._extract_native_turn_latest_reply(tp_r, "codex"), "")

        # 4) reply_repeat_guard._check_heavy_report_locked 兼容 state_dir/handoff/<sid>_handoff_gate.json (enforce drain/freeze & stopCorrectionRequested)
        user_words.atomic_write_json(self.state / "heavy_completions" / "coord-sid.json", [{
            "run_id": "r1", "all_succeeded": True, "reported_to_user": False, "subcommand": "batch",
            "receipt_file": "rec.json", "streams": [{"stream_id": "S1", "model": "m1", "succeeded": True, "status": "ok", "token_usage": {"total_tokens": 10}}],
            "token_usage_summary": {"total_tokens": 10},
        }])
        user_words.atomic_write_json(self.state / "handoff" / "coord-sid_handoff_gate.json", {
            "client": "claude", "version": 1, "conversationId": "coord-sid", "mode": "enforce",
            "phase": "drain", "stopCorrectionRequested": False, "handoffRecorded": False,
        })
        self.assertIsNone(
            reply_repeat_guard._check_heavy_report_locked({}, self.state, "coord-sid", "turn_1", "准备交接", "block")
        )

        # 5) CAE_HARNESS_DEPTH="2" 跳过缺失轨迹，"0" 不跳过；全新首轮无状态会话缺失轨迹静默返回 {}
        with mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": "2"}):
            self.assertTrue(user_words.codex_hook_session({"session_id": "d2-sid", "transcript_path": ""})[3])
            self.assertTrue(user_words.claude_hook_session({"session_id": "d2-sid", "transcript_path": str(self.root / "missing.jsonl")})[3])
        with mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": "0"}):
            self.assertFalse(user_words.codex_hook_session({"session_id": "d0-sid", "transcript_path": ""})[3])

        fresh_state = self.root / "fresh_native_state"
        for ev in ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"):
            self.assertEqual(
                handoff_gate.handle_claude_handoff(
                    {"hook_event_name": ev, "session_id": "brand-new-cl", "transcript_path": str(self.root / "not_created_yet.jsonl")},
                    fresh_state, mode="enforce", compact_limit=2,
                ),
                {},
            )
            self.assertEqual(
                handoff_gate.handle_codex_handoff(
                    {"hook_event_name": ev, "session_id": "brand-new-cx", "transcript_path": str(self.root / "not_created_yet.jsonl")},
                    fresh_state, mode="enforce", compact_limit=2,
                ),
                {},
            )
        self.assertIn(
            "systemMessage",
            handoff_gate.handle_claude_handoff(
                {"hook_event_name": "PostCompact", "session_id": "brand-new-cl", "transcript_path": str(self.root / "not_created_yet.jsonl")},
                fresh_state, mode="enforce", compact_limit=2,
            ),
        )

        # 6) 读图阻断原因明确说明多模态上下文消耗，且 Stop 补正提示列出 4 个必需交接章节要素
        ux_payload = self.native_payload("claude")
        ux_dir = self.root / "ux_handoff_state"
        handoff_gate.handle_claude_handoff(dict(ux_payload, hook_event_name="UserPromptSubmit", prompt="/handoff"), ux_dir, "enforce")
        img_deny = handoff_gate.handle_claude_handoff(
            dict(ux_payload, hook_event_name="PreToolUse", tool_name="Read", tool_input={"file_path": "plot.png"}),
            ux_dir, "enforce",
        )
        img_reason = img_deny["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("多模态", img_reason)
        stop_block = handoff_gate.handle_claude_handoff(
            dict(ux_payload, hook_event_name="Stop", last_assistant_message="不完整的收尾"),
            ux_dir, "enforce",
        )
        self.assertEqual(stop_block["decision"], "block")
        for elem in ("① 已验证进展", "② Git/工作区状态", "③ 未完成事项", "④ 独立【新对话提示词】"):
            self.assertIn(elem, stop_block["reason"])

        # 7) session_state_lock 逐级校验已存在父目录软链
        outside_lock_target = self.root / "outside_lock_dir"
        outside_lock_target.mkdir(exist_ok=True)
        sym_parent = self.root / "sym_lock_parent"
        try:
            sym_parent.symlink_to(outside_lock_target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"当前环境无符号链接创建权限: {exc}")
        for nested in (sym_parent / "handoff", sym_parent / "deep" / "handoff"):
            with self.assertRaises(OSError):
                with user_words.session_state_lock(nested, "valid-sid"):
                    pass
        self.assertFalse((outside_lock_target / "handoff").exists())
        self.assertFalse((outside_lock_target / "deep").exists())


    def test_persisted_ticket_uses_compact_card_link_in_recovery_and_handoff(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("lifecycle_compact_card_harness", HOOKS_DIR.parent / "skills/cae-harness/scripts/cae_harness.py")
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)

        plan_dir = self.state / "dispatch_plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        _, ticket1 = self.ticket(tid="ticket-compact-1")
        ticket1["streams"][0]["client"] = "dsh"
        ticket1["streams"][1]["client"] = "agy"
        harness._save_plan(ticket1, plan_dir)

        _, ticket2 = self.ticket(tid="ticket-compact-2")
        ticket2["streams"] = [ticket2["streams"][0]]
        ticket2["streams"][0]["stream_id"] = "W1"
        ticket2["streams"][0]["channel"] = "native_workflow"
        ticket2["streams"][0]["client"] = "agy"
        harness._save_plan(ticket2, plan_dir)

        # 1) render_pending_workflow_block 输出精简 4 列总览卡与可点击链接，不展开 14 项长表
        compact_popup = user_words.render_pending_workflow_block(ticket1, for_handoff=False)
        compact_handoff = user_words.render_pending_workflow_block(ticket1, for_handoff=True)
        for rendered in (compact_popup, compact_handoff):
            self.assertIn("| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |", rendered)
            self.assertIn("[ticket-compact-1_card.md](file://", rendered)
            self.assertIn("恢复命令：plan --resume-ticket ticket-compact-1", rendered)
            self.assertNotIn("14 项完整参数明细", rendered)
            self.assertNotIn("| 序号 | 参数项 | 当前值 |", rendered)

        # 2) handoff_gate._workflow_handoff_present 认可精简总览 + 明细链接交接，并拦截缺漏字段
        prompt_text = "【新对话提示词】\n" + compact_handoff
        self.assertTrue(handoff_gate._workflow_handoff_present(prompt_text, [ticket1]))
        self.assertTrue(handoff_gate._workflow_handoff_present(ticket1["compact_handoff_prompt_block"], [ticket1]))
        self.assertFalse(handoff_gate._workflow_handoff_present(prompt_text.replace("ticket-compact-1_card.md", "other.md"), [ticket1]))
        self.assertFalse(handoff_gate._workflow_handoff_present(prompt_text.replace("plan --resume-ticket ticket-compact-1", "resume"), [ticket1]))
        self.assertFalse(handoff_gate._workflow_handoff_present(prompt_text.replace("| S2 |", "| S9 |"), [ticket1]))

        # 3) _workflow_handoff_notice 与 session_context_loader._workflow_recovery_blocks 覆盖多张未决票据且均保持精简
        notice = handoff_gate._workflow_handoff_notice([ticket1, ticket2])
        self.assertIn("精简 4 列总览表", notice)
        self.assertIn("[ticket-compact-1_card.md](file://", notice)
        self.assertIn("[ticket-compact-2_card.md](file://", notice)
        self.assertNotIn("14 项完整参数明细", notice)

        blocks = session_context_loader._workflow_recovery_blocks(self.sid, self.state)
        self.assertEqual(len(blocks), 2)
        joined = "\n".join(blocks)
        self.assertIn("[ticket-compact-1_card.md](file://", joined)
        self.assertIn("[ticket-compact-2_card.md](file://", joined)
        self.assertNotIn("14 项完整参数明细", joined)

        # 4) 兼容旧版 v1.16.0（有 card_detail_file 但 compact_summary_card 缺少 4 列总览与背景字段）
        legacy_v116 = dict(ticket1, compact_summary_card="工作流目标：旧版单行\n- S1: dsh | fixture | 64000 tokens | 只读")
        legacy_v116["status"] = "HANDED_OFF"
        synthesized = user_words.render_pending_workflow_block(legacy_v116, for_handoff=True)
        self.assertIn("| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |", synthesized)
        self.assertIn("[ticket-compact-1_card.md](file://", synthesized)
        self.assertIn("HANDED_OFF", synthesized)
        self.assertNotIn("14 项完整参数明细", synthesized)
        self.assertTrue(handoff_gate._workflow_handoff_present("【新对话提示词】\n" + synthesized, [legacy_v116]))

    def test_v1171_collapse_table_blank_line_missing_card_fallback_and_multi_ticket_s1(self):
        # 1) collapse_historical_cards stops folding at blank line, preserving scientific tables
        text = (
            "#### 流 S1（14 项完整参数明细）\n"
            "| 序号 | 参数项 | 当前值 |\n| --- | --- | --- |\n| 1 | 流号 | S1 |\n\n"
            "| 网格 | L2 误差 | 收敛阶 |\n| --- | --- | --- |\n| 64 | 1.2e-4 | 2.01 |\n"
        )
        folded = user_words.collapse_historical_cards(text)
        self.assertIn("[已折叠历史确认单/账单明细]", folded)
        self.assertIn("| 网格 | L2 误差 | 收敛阶 |", folded)
        self.assertIn("| 64 | 1.2e-4 | 2.01 |", folded)

        # 2) render_pending_workflow_block falls back to 14-item table if card_detail_file is deleted
        _, tk = self.ticket(tid="ticket-missing-card")
        tk["card_detail_file"] = str(self.state / "dispatch_plans" / "deleted_card.md")
        rendered_fb = user_words.render_pending_workflow_block(tk, for_handoff=False)
        self.assertIn("| 序号 | 参数项 | 当前值 |", rendered_fb)

        # 3) _matches_compact_card_handoff consumes rows per ticket (no multi-ticket S1 aliasing)
        import importlib.util
        spec = importlib.util.spec_from_file_location("lifecycle_v1171_harness", HOOKS_DIR.parent / "skills/cae-harness/scripts/cae_harness.py")
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        plan_dir = self.state / "dispatch_plans"
        _, t1 = self.ticket(tid="t-multi-a")
        t1["streams"] = [t1["streams"][0]]
        harness._save_plan(t1, plan_dir)
        _, t2 = self.ticket(tid="t-multi-b")
        t2["streams"] = [dict(t2["streams"][0], model_display="other-model-b")]
        harness._save_plan(t2, plan_dir)
        single_row_spoof = (
            "【新对话提示词】\n"
            + user_words.render_pending_workflow_block(t1, for_handoff=True)
            + "\n票据：t-multi-b，明细文件 t-multi-b_card.md，恢复命令：plan --resume-ticket t-multi-b\n"
        )
        self.assertFalse(handoff_gate._workflow_handoff_present(single_row_spoof, [t1, t2]))

    def test_v118_drain_dedup_collapse_blank_and_fenced_and_cross_os_card(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("lifecycle_v118_harness", HOOKS_DIR.parent / "skills/cae-harness/scripts/cae_harness.py")
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)

        # 1) collapse_historical_cards: 标题与表头间空行不产生双占位符，且文本/markdown 围栏内 14 项表可折叠，代码围栏与科学表保持原样
        raw_card_history = (
            "#### 流 S1（14 项完整参数明细）\n\n"
            "| 序号 | 参数项 | 当前值 |\n| --- | --- | --- |\n| 1 | 流号 | S1 |\n\n"
            "| 网格 | L2 误差 | 收敛阶 |\n| --- | --- | --- |\n| 64 | 1.2e-4 | 2.01 |\n\n"
            "```text\n"
            "【新对话提示词】\n"
            "#### 流 S2（14 项完整参数明细）\n\n"
            "| 序号 | 参数项 | 当前值 |\n| --- | --- | --- |\n| 1 | 流号 | S2 |\n"
            "```\n\n"
            "```python\n"
            "sample = '| 序号 | 参数项 | 当前值 |'\n"
            "```\n"
        )
        folded = user_words.collapse_historical_cards(raw_card_history)
        self.assertEqual(folded.count("[已折叠历史确认单/账单明细]"), 2)
        self.assertIn("| 网格 | L2 误差 | 收敛阶 |", folded)
        self.assertIn("| 64 | 1.2e-4 | 2.01 |", folded)
        self.assertIn("【新对话提示词】", folded)
        self.assertNotIn("| 1 | 流号 | S2 |", folded)
        self.assertIn("sample = '| 序号 | 参数项 | 当前值 |'", folded)

        # 2) session_context_loader + handoff_gate: 同一步 CHECKPOINT+drain 仅注入 1 次完整卡，后续同阶段 PreInvocation 仅 1 行提醒
        plan_dir = self.state / "dispatch_plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        cid = self.sid
        _, tk = self.ticket(tid="t-v118-dedup")
        harness._save_plan(tk, plan_dir)
        transcript = self.root / "v118_drain_dedup.jsonl"
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": "请立即整理交接并停机"})
        _append_jsonl(transcript, {"type": "CHECKPOINT", "step_index": 1})
        payload = {"conversationId": cid, "transcriptPath": str(transcript)}
        first_res = session_context_loader.handle_pre_invocation(payload)
        first_msg = first_res["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertEqual(first_msg.count("| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |"), 1)
        self.assertIn("t-v118-dedup_card.md", first_msg)

        # 同阶段无新压缩、票据未变：第二次 PreInvocation 不再灌入完整表格，仅 1 行提醒
        second_res = session_context_loader.handle_pre_invocation(payload)
        second_msg = second_res["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertNotIn("| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |", second_msg)
        self.assertIn("[未决工作流交接提醒]", second_msg)
        self.assertIn("t-v118-dedup", second_msg)

        # 再次发生 CHECKPOINT 压缩后，重新给出 1 次完整精简卡
        _append_jsonl(transcript, {"type": "CHECKPOINT", "step_index": 2})
        third_res = session_context_loader.handle_pre_invocation(payload)
        third_msg = third_res["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertEqual(third_msg.count("| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |"), 1)

        # 3) render_pending_workflow_block 跨 OS 路径翻译解析
        real_card = plan_dir / "t-v118-dedup_card.md"
        self.assertTrue(real_card.is_file())
        tk_cross = dict(tk)
        if os.name == "nt":
            drive = real_card.drive.rstrip(":").lower()
            rest = str(real_card)[3:].replace("\\", "/")
            tk_cross["card_detail_file"] = f"/mnt/{drive}/{rest}"
        else:
            tk_cross["card_detail_file"] = r"C:\nonexistent\cross_os\t-v118-dedup_card.md"
        with unittest.mock.patch.object(user_words, "candidate_dispatch_plan_dirs", return_value=[plan_dir]):
            rendered_cross = user_words.render_pending_workflow_block(tk_cross, for_handoff=False)
        self.assertIn("| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |", rendered_cross)
        self.assertNotIn("| 序号 | 参数项 | 当前值 |", rendered_cross)

        # 4) handoff_gate P3-1 *_steps 别名优先于旧 *_sec 键
        fake_profile = {
            "runtime_tunables": {
                "handoff_thresholds": {
                    "antigravity": {
                        "advisory_thrash_avg_interval_steps": 42,
                        "advisory_thrash_avg_interval_sec": 99,
                        "drain_thrash_avg_interval_steps": 17,
                        "drain_thrash_avg_interval_sec": 88,
                    }
                }
            }
        }
        with unittest.mock.patch.object(user_words, "load_fleet_profile", return_value=fake_profile), \
             unittest.mock.patch.object(user_words, "get_fleet_profile_cache_token", return_value=("v118-steps-test",)):
            handoff_gate._LAST_HANDOFF_PROFILE_TOKEN = None
            handoff_gate._sync_handoff_tunables("antigravity", "", "")
            self.assertEqual(handoff_gate.ADVISORY_THRASH_AVG_INTERVAL, 42.0)
            self.assertEqual(handoff_gate.DRAIN_THRASH_AVG_INTERVAL, 17.0)
        handoff_gate._LAST_HANDOFF_PROFILE_TOKEN = None
        handoff_gate._sync_handoff_tunables("antigravity", "", "")

    def test_v1181_single_turn_runaway_and_full_transcript_size_trigger_drain(self):
        # 1) 单用户回合内连续跨 4 次 CHECKPOINT 且步数跨度 >= 650 触发 turn_runaway 进入 Stage-2 drain
        tp_runaway = self.root / "runaway_compact" / "transcript.jsonl"
        tp_runaway.parent.mkdir(parents=True, exist_ok=True)
        _append_jsonl(tp_runaway, {"step_index": 167, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "批准"})
        for cp_step in (215, 434, 680, 877):
            _append_jsonl(tp_runaway, {"step_index": cp_step, "source": "SYSTEM", "type": "CHECKPOINT"})
        cid_run = "ag-v1181-runaway"
        out_run = session_context_loader.handle_pre_invocation({"conversationId": cid_run, "transcriptPath": str(tp_runaway)})
        self.assertIn("Stage-2 drain", out_run["injectSteps"][0]["systemMessage"]["systemMessage"])
        gate_run = handoff_gate.load_gate_state(self.state, cid_run)
        self.assertEqual(gate_run["phase"], "drain")
        self.assertIn("turn_runaway", gate_run["triggers"])

        # 2) 紧凑版 < 3 MiB 但 transcript_full.jsonl >= 6 MiB 且 cp >= 3 触发 full_transcript_bytes
        tp_full_dir = self.root / "full_heavy"
        tp_full_dir.mkdir(parents=True, exist_ok=True)
        tp_compact = tp_full_dir / "transcript.jsonl"
        tp_full = tp_full_dir / "transcript_full.jsonl"
        _append_jsonl(tp_compact, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "推进"})
        for cp_step in (120, 260, 410):
            _append_jsonl(tp_compact, {"step_index": cp_step, "source": "SYSTEM", "type": "CHECKPOINT"})
        with tp_full.open("wb") as fh:
            fh.seek(6 * 1024 * 1024 + 128)
            fh.write(b"\n")
        cid_full = "ag-v1181-full-bytes"
        session_context_loader.handle_pre_invocation({"conversationId": cid_full, "transcriptPath": str(tp_compact)})
        gate_full = handoff_gate.load_gate_state(self.state, cid_full)
        self.assertEqual(gate_full["phase"], "drain")
        self.assertIn("full_transcript_bytes", gate_full["triggers"])

    def test_v1183_backtick_tokens_and_repeated_pending_ticket_guard(self):
        import reply_repeat_guard

        cid = "ag-v1183-ticket-guard"
        tp = self.root / "v1183_ticket" / "transcript.jsonl"
        tp.parent.mkdir(parents=True, exist_ok=True)
        _append_jsonl(tp, {"step_index": 1, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "批准"})

        # 1) Backtick-wrapped token numbers (e.g. `201,795` tokens) satisfy heavy completion report check
        receipt_path = str(self.root / "codex_readers_batch.json")
        user_words.atomic_write_json(
            self.state / "heavy_completions" / f"{cid}.json",
            [{
                "run_id": "run-v1183-bt",
                "subcommand": "batch",
                "all_succeeded": True,
                "receipt_file": receipt_path,
                "streams": [
                    {"stream_id": "S1", "engine": "codex:gpt-6.2-sol", "model": "gpt-6.2-sol", "succeeded": True, "status": "ok",
                     "token_usage": {"input_tokens": 199574, "output_tokens": 2221, "total_tokens": 201795}},
                    {"stream_id": "S2", "engine": "codex:gpt-6.2-astra", "model": "gpt-6.2-astra", "succeeded": True, "status": "ok",
                     "token_usage": {"input_tokens": 282718, "output_tokens": 3569, "total_tokens": 286287}},
                ],
                "token_usage_summary": {"total_tokens": 488082},
            }],
        )
        _append_jsonl(tp, {
            "step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "content": (
                "现汇报外部无头批次 codex_readers_batch.json 执行结果：两路进程均完成。\n"
                "- S1：小计 `201,795` tokens；\n- S2：小计 `286,287` tokens；总计 `488,082` tokens。"
            ),
        })
        res_bt = reply_repeat_guard.handle_stop_repeat_guard({
            "conversationId": cid, "terminationReason": "model_stop", "transcriptPath": str(tp),
        })
        self.assertEqual(res_bt["decision"], "allow")
        self.assertEqual(user_words.load_unreported_heavy_completions(cid, self.state), [])

        # 2) Pending ticket multi-line card allowed once per (turn, checkpoint), blocked on background wakeup repeat, 1-line reminder allowed
        card_text = (
            "**【待确认派工 · 严格只读】** 并发 3 路复核文档（共 3 路：S1 codex:gpt-6.2-sol (max)；额度均可用）。\n"
            "- 完整总览与 14 项明细：[680ed899dfc844c8baccfcd107d7dc21_card.md](file:///tmp/680ed899dfc844c8baccfcd107d7dc21_card.md)\n"
            "- **请确认**：回复 **「批准」** 启动（票据 `680ed899dfc844c8baccfcd107d7dc21`，状态：`PENDING`）。"
        )
        _append_jsonl(tp, {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "第 1 阶段完成。\n\n" + card_text})
        res_first = reply_repeat_guard.handle_stop_repeat_guard({
            "conversationId": cid, "terminationReason": "model_stop", "transcriptPath": str(tp),
        })
        self.assertEqual(res_first["decision"], "allow")

        # Background task finishes via SYSTEM_MESSAGE (same user turn, no compaction) -> repeating full PENDING card is blocked once
        _append_jsonl(tp, {"step_index": 4, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "content": "[Message] sender=task-1 finished"})
        _append_jsonl(tp, {"step_index": 5, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "第 2A 阶段完成。\n\n" + card_text})
        res_repeat = reply_repeat_guard.handle_stop_repeat_guard({
            "conversationId": cid, "terminationReason": "model_stop", "transcriptPath": str(tp),
        })
        self.assertEqual(res_repeat["decision"], "continue")
        self.assertIn("待批票据防刷屏", res_repeat["reason"])

        # Rewritten 1-line brief reminder passes cleanly
        _append_jsonl(tp, {
            "step_index": 6, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "content": "第 2A 阶段完成（票据 `680ed899` 仍候回复「批准」启动，不影响当前后台任务）。",
        })
        res_oneline = reply_repeat_guard.handle_stop_repeat_guard({
            "conversationId": cid, "terminationReason": "model_stop", "transcriptPath": str(tp),
        })
        self.assertEqual(res_oneline["decision"], "allow")

        # After a CHECKPOINT compaction, showing chat_brief once again is allowed
        _append_jsonl(tp, {"step_index": 7, "source": "SYSTEM", "type": "CHECKPOINT"})
        session_context_loader.handle_pre_invocation({"conversationId": cid, "transcriptPath": str(tp)})
        _append_jsonl(tp, {"step_index": 8, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "压缩恢复汇报。\n\n" + card_text})
        res_after_cp = reply_repeat_guard.handle_stop_repeat_guard({
            "conversationId": cid, "terminationReason": "model_stop", "transcriptPath": str(tp),
        })
        self.assertEqual(res_after_cp["decision"], "allow")

    def test_v11811_headless_depth_hook_isolation_and_markdown_table_token_bill(self):
        import reply_repeat_guard
        import status_guard

        # 1) CAE_HARNESS_DEPTH=1 must unconditionally skip context/handoff/stop hooks even when valid transcripts exist,
        #    while status_guard keeps enforcing external-write boundaries.
        codex_tp = self.root / "v11811_codex_rollout.jsonl"
        _append_jsonl(codex_tp, {"type": "session_meta", "payload": {"id": "cx-depth-1", "source": "cli", "cwd": str(self.root)}})
        _append_jsonl(codex_tp, {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "执行核对"}]}})
        claude_tp = self.root / "v11811_claude.jsonl"
        _append_jsonl(claude_tp, {"type": "user", "uuid": "u-1", "message": {"role": "user", "content": "执行核对"}})
        agy_tp = self.root / "v11811_agy.jsonl"
        _append_jsonl(agy_tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "执行核对"})
        _append_jsonl(agy_tp, {"step_index": 1, "source": "SYSTEM", "type": "CHECKPOINT"})
        _append_jsonl(agy_tp, {"step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "后台任务仍在运行，请稍候。"})

        with unittest.mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": "1"}):
            _, _, _, cx_skip = user_words.codex_hook_session({"session_id": "cx-depth-1", "transcript_path": str(codex_tp)})
            _, _, _, cl_skip = user_words.claude_hook_session({"session_id": "cl-depth-1", "transcript_path": str(claude_tp)})
            self.assertTrue(cx_skip)
            self.assertTrue(cl_skip)
            self.assertEqual(
                session_context_loader.handle_context_check({"conversationId": "agy-depth-1", "transcriptPath": str(agy_tp)}),
                {"injectSteps": []},
            )
            self.assertEqual(
                session_context_loader.handle_codex_session_start({"session_id": "cx-depth-1", "transcript_path": str(codex_tp)}),
                {},
            )
            self.assertEqual(
                session_context_loader.handle_claude_session_start({"session_id": "cl-depth-1", "transcript_path": str(claude_tp)}),
                {},
            )
            self.assertEqual(
                handoff_gate.handle_codex_handoff(
                    {"hook_event_name": "SessionStart", "session_id": "cx-depth-1", "transcript_path": str(codex_tp)},
                    self.state, mode="enforce", compact_limit=1,
                ),
                {},
            )
            self.assertEqual(
                reply_repeat_guard.handle_stop_repeat_guard({
                    "conversationId": "agy-depth-1", "terminationReason": "model_stop", "transcriptPath": str(agy_tp),
                }),
                {"decision": "allow"},
            )
            # status_guard still hard-denies external writes inside headless sub-runs
            proj = self.root / "depth_proj"
            outside = self.root / "depth_outside"
            proj.mkdir()
            outside.mkdir()
            sg_res = status_guard.handle_pre_tool_use({
                "conversationId": "agy-depth-1",
                "cwd": str(proj),
                "workspacePaths": [str(proj)],
                "toolCall": {"name": "write_file", "args": {"Path": str(outside / "escape.txt"), "Content": "x"}},
            })
            self.assertEqual(sg_res["decision"], "deny")

        # 2) reply_repeat_guard recognizes Markdown table Token columns while rejecting empty Token cells with digits in other columns
        self.assertTrue(reply_repeat_guard._has_token_report(
            "| 流号 | 模型 | 状态 | 输入 / 输出 / 总 Token |\n"
            "| --- | --- | --- | --- |\n"
            "| S1 | codex:gpt-6.2-astra | 成功 | 120,000 / 3,400 / 123,400 |\n"
        ))
        self.assertTrue(reply_repeat_guard._has_token_report(
            "| 流号 | 模型 | Token 用量 |\n"
            "| --- | --- | --- |\n"
            "| S1 | agy:default | 无法统计 |\n"
        ))
        self.assertFalse(reply_repeat_guard._has_token_report(
            "| 流号 | 模型 | 状态 | Token 用量 |\n"
            "| --- | --- | --- | --- |\n"
            "| S1 | gpt-6.2-astra | 成功 | — |\n"
        ))

        cid_tbl = "ag-v11811-tbl-bill"
        tp_tbl = self.root / "v11811_tbl" / "transcript.jsonl"
        tp_tbl.parent.mkdir(parents=True, exist_ok=True)
        _append_jsonl(tp_tbl, {"step_index": 1, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "继续"})
        rec_tbl = str(self.root / "batch_v11811.json")
        user_words.atomic_write_json(
            self.state / "heavy_completions" / f"{cid_tbl}.json",
            [{
                "run_id": "run-v11811-tbl",
                "subcommand": "batch",
                "all_succeeded": True,
                "receipt_file": rec_tbl,
                "streams": [
                    {"stream_id": "S1", "engine": "codex:gpt-6.2-astra", "model": "gpt-6.2-astra", "succeeded": True, "status": "ok",
                     "token_usage": {"input_tokens": 120000, "output_tokens": 3400, "total_tokens": 123400}},
                ],
                "token_usage_summary": {"total_tokens": 123400},
            }],
        )
        _append_jsonl(tp_tbl, {
            "step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "content": (
                "批处理回执 batch_v11811.json 已执行完成：\n\n"
                "| 流号 | 模型 | 状态 | Token（输入/输出/总计） |\n"
                "| --- | --- | --- | --- |\n"
                "| S1 | gpt-6.2-astra | 成功 | 120,000 / 3,400 / 123,400 |\n"
            ),
        })
        res_tbl = reply_repeat_guard.handle_stop_repeat_guard({
            "conversationId": cid_tbl, "terminationReason": "model_stop", "transcriptPath": str(tp_tbl),
        })
        self.assertEqual(res_tbl["decision"], "allow")
        self.assertEqual(user_words.load_unreported_heavy_completions(cid_tbl, self.state), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

