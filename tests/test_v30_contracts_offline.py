# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""tests/test_v30_contracts_offline.py - v3.0.0 四端原生共存、单入口钩子与元更新契约离线回归。"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / "template" / "user-guard" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

import handoff_gate  # noqa: E402
import reply_repeat_guard  # noqa: E402
import session_context_loader  # noqa: E402
import status_guard  # noqa: E402
import uguard_hook  # noqa: E402
import user_words  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


INSTALLER = _load_module("installer_v30_test", REPO_ROOT / "install.py")
FLEET_STATE = _load_module(
    "fleet_state_v30_test",
    REPO_ROOT / "template" / "user-guard" / "skills" / "fleet-evolve" / "scripts" / "fleet_state.py",
)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestV30ContractsOffline(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v30-contracts-")
        self.root = Path(self._tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.state = self.root / "state"
        self.logs = self.root / "logs"
        self.state.mkdir()
        self.logs.mkdir()
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "PLUGIN_DATA": str(self.root / "plugin_data"),
                "USER_GUARD_ENFORCE_TICKET_GATE": "1",
            },
            clear=False,
        )
        self._env_patch.start()
        os.environ.pop("CAE_HARNESS_DEPTH", None)
        os.environ.pop("USER_GUARD_DISABLE_TICKET_GATE", None)
        self._dir_patches = [
            mock.patch.object(status_guard, "get_data_dirs", return_value=(self.state, self.logs)),
            mock.patch.object(reply_repeat_guard, "get_data_dirs", return_value=(self.state, self.logs)),
            mock.patch.object(session_context_loader, "get_data_dirs", return_value=(self.state, self.logs)),
        ]
        for p in self._dir_patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._dir_patches):
            p.stop()
        self._env_patch.stop()
        self._tmp.cleanup()

    def _pre(self, tool: str, args: dict, tp: Path, sid: str = "v30-sess") -> dict:
        return status_guard.handle_pre_tool_use(
            {
                "conversationId": sid,
                "cwd": str(self.project),
                "workspacePaths": [str(self.project)],
                "transcriptPath": str(tp),
                "toolCall": {"name": tool, "args": args},
            }
        )

    def test_d1_native_orchestration_decoupling_and_headless_hard_deny(self) -> None:
        """D1: 显式原生斜杠、只读多路与子代理会话零级联放行；未授权可写走 ask；外部无头/裸 CLI 与 R4 写边界维持硬拦截。"""
        # 1) 用户显式调用原生斜杠命令 -> 同端可写编排直接放行
        for idx, slash_prompt in enumerate(
            ("/teamwork 分析架构", "/boost 深度重构", "/swarm 并行验证", "/plan 制定方案", "/run-workflow 执行编排")
        ):
            tp = self.root / f"slash_{idx}.jsonl"
            _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": slash_prompt})
            for tool, args in (
                ("invoke_subagent", {"TypeName": "self", "Role": "Worker"}),
                ("swarm", {"action": "add", "name": "w1", "brief": "do work"}),
                ("run_workflow", {"Script": "x = 1\n"}),
            ):
                res = self._pre(tool, args, tp, sid=f"slash-{idx}")
                self.assertEqual(res["decision"], "allow", (slash_prompt, tool, res))

        # 2) AGY 子代理会话（无 USER_INPUT / USER_EXPLICIT，仅有 SYSTEM_MESSAGE）-> 零级联阻断，但 R4 外部写仍硬阻断
        tp_child = self.root / "child_subagent.jsonl"
        _append_jsonl(
            tp_child,
            {"step_index": 0, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "content": "You are teamwork_interviewer."},
        )
        self.assertEqual(
            self._pre("invoke_subagent", {"TypeName": "self", "Role": "Helper"}, tp_child, sid="child-1")["decision"],
            "allow",
        )
        self.assertEqual(
            self._pre("swarm", {"action": "add", "name": "helper"}, tp_child, sid="child-1")["decision"],
            "allow",
        )
        outside_file = self.root / "outside_escape.txt"
        self.assertIn(
            self._pre("write_file", {"Path": str(outside_file), "Content": "leak"}, tp_child, sid="child-1")["decision"],
            ("force_ask", "deny"),
        )

        # 3) 普通会话：只读多路全部直接放行；未授权同端可写返回 ask；外部无头与现代 agy 裸调维持 hard deny
        tp_norm = self.root / "normal_turn.jsonl"
        _append_jsonl(tp_norm, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "核对代码"})
        for ro_type in ("readonly-reader", "readonly-auditor", "readonly-collaborator", "research", "Explore", "Plan"):
            self.assertEqual(
                self._pre("invoke_subagent", {"TypeName": ro_type}, tp_norm, sid="norm-1")["decision"],
                "allow",
                ro_type,
            )
        for wr_tool, wr_args in (
            ("invoke_subagent", {"TypeName": "self"}),
            ("swarm", {"action": "add", "name": "w1"}),
            ("run_workflow", {"Script": "x = 1\n"}),
            ("spawn_agent", {"agent_type": "self"}),
            ("Agent", {"subagent_type": "general-purpose"}),
        ):
            self.assertEqual(self._pre(wr_tool, wr_args, tp_norm, sid="norm-1")["decision"], "ask", (wr_tool, wr_args))

        # 即使在 /teamwork 会话下，外部无头重负载与现代裸调 CLI（含 agy -p/--print/--prompt/--input-format stream-json）仍严格 deny
        tp_tw = self.root / "tw_external.jsonl"
        _append_jsonl(tp_tw, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/teamwork 推进"})
        for bare_cmd in (
            "codex exec 'audit repo'",
            "claude -p 'audit repo'",
            "dsh --profile headless 'audit repo'",
            "agy task 'audit repo'",
            "agy -p 'audit repo'",
            "agy --print 'audit repo'",
            "agy --prompt 'audit repo'",
            "agy --input-format stream-json",
            "python -B cae_harness.py batch --spec-file s.json",
        ):
            res = self._pre("run_command", {"CommandLine": bare_cmd}, tp_tw, sid="tw-ext")
            self.assertEqual(res["decision"], "deny", (bare_cmd, res))

        # R4: run_workflow 动态未解析路径写仍必须触发 force_ask（零安全倒退）
        res_dyn_wf = self._pre("run_workflow", {"Script": "p = get_ext()\nopen(p, 'w').write('x')\n"}, tp_tw, sid="tw-r4")
        self.assertEqual(res_dyn_wf["decision"], "force_ask")

    def test_d2_advisory_default_manual_compact_goal_protection_and_drain_exceptions(self) -> None:
        """D2: 四端统一 advisory；手动 /compact 不计入震荡；活跃 /goal 不被启发式强杀；英文取消与 drain 例外准确。"""
        # 1) 手动 /compact 触发原话回溯归档，但不计入自动压缩震荡次数 cpCount
        tp_mc = self.root / "manual_compact.jsonl"
        _append_jsonl(tp_mc, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "开始任务"})
        for step_idx in (30, 60, 90, 120):
            _append_jsonl(tp_mc, {"step_index": step_idx - 1, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/compact"})
            _append_jsonl(tp_mc, {"step_index": step_idx, "source": "SYSTEM", "type": "CHECKPOINT"})
        with mock.patch.object(handoff_gate, "DEFAULT_AGY_HANDOFF_MODE", "enforce"):
            out_mc = session_context_loader.handle_pre_invocation({"conversationId": "mc-sess", "transcriptPath": str(tp_mc)})
            self.assertEqual(len(out_mc["injectSteps"]), 1)
            self.assertNotIn("Stage-2 drain", out_mc["injectSteps"][0]["systemMessage"]["systemMessage"])
            gate_mc = handoff_gate.load_gate_state(self.state, "mc-sess")
            self.assertEqual(gate_mc["phase"], "normal")
            comp_mc = json.loads((self.state / "mc-sess_compaction.json").read_text(encoding="utf-8"))
            self.assertEqual(comp_mc["manualCompactSteps"], [30, 60, 90, 120])

        # 2) 英文取消 /goal 指令清除 isGoalActive
        tp_en = self.root / "goal_en.jsonl"
        _append_jsonl(tp_en, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/goal\nrun overnight"})
        session_context_loader.handle_pre_invocation({"conversationId": "goal-en", "transcriptPath": str(tp_en)})
        self.assertTrue(handoff_gate.load_gate_state(self.state, "goal-en")["isGoalActive"])
        _append_jsonl(tp_en, {"step_index": 10, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "please cancel /goal now"})
        session_context_loader.handle_pre_invocation({"conversationId": "goal-en", "transcriptPath": str(tp_en)})
        self.assertFalse(handoff_gate.load_gate_state(self.state, "goal-en")["isGoalActive"])

        # 3) drain 阶段放行 browser_close 与 /.system_generated/swarm/plan.json，同时仍阻断 browser_click 与新建 schedule
        cid_dr = "drain-exc-01"
        brain_plan = self.root / "brain" / cid_dr / ".system_generated" / "swarm" / "plan.json"
        brain_plan.parent.mkdir(parents=True, exist_ok=True)
        st = handoff_gate.load_gate_state(self.state, cid_dr)
        st["phase"] = "drain"
        handoff_gate.save_gate_state(self.state, cid_dr, st)
        self.assertIsNone(
            handoff_gate.check_pre_tool_use_locked(
                {"conversationId": cid_dr, "cwd": str(self.project), "toolCall": {"name": "browser_close", "args": {}}},
                self.state,
            )
        )
        self.assertEqual(
            handoff_gate.check_pre_tool_use_locked(
                {"conversationId": cid_dr, "cwd": str(self.project), "toolCall": {"name": "browser_click", "args": {}}},
                self.state,
            )["decision"],
            "deny",
        )
        self.assertEqual(
            handoff_gate.check_pre_tool_use_locked(
                {
                    "conversationId": cid_dr,
                    "cwd": str(self.project),
                    "toolCall": {"name": "schedule", "args": {"DurationSeconds": 60, "Prompt": "ping"}},
                },
                self.state,
            )["decision"],
            "deny",
        )
        self.assertTrue(handoff_gate._is_handoff_target(str(brain_plan), cid_dr, str(self.project), [str(self.project)]))

    def test_d3_single_entry_uguard_hook_and_stop_filters(self) -> None:
        """D3: 单入口 uguard_hook.py 合并响应与 policy.json 热切换；过滤 /teamwork 过渡消息；Gate C 豁免 skill/plugin 目录；外部账单保持硬拦。"""
        # 1) uguard_hook 响应合并优先级（SessionStart 串联、PreToolUse deny > ask > context）
        m_ss = uguard_hook._merge_session_start_responses(
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "PART1"}},
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "PART2"}},
        )
        self.assertIn("PART1", m_ss["hookSpecificOutput"]["additionalContext"])
        self.assertIn("PART2", m_ss["hookSpecificOutput"]["additionalContext"])
        m_pre_deny = uguard_hook._merge_pre_tool_responses(
            {"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "ask1"}},
            {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "deny2"}},
        )
        self.assertEqual(m_pre_deny["hookSpecificOutput"]["permissionDecision"], "deny")
        m_pre_ask = uguard_hook._merge_pre_tool_responses(
            {"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "ask1"}},
            {"hookSpecificOutput": {"additionalContext": "handoff note"}},
        )
        self.assertEqual(m_pre_ask["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("handoff note", m_pre_ask["hookSpecificOutput"]["additionalContext"])

        # 2) policy.json == off 时 PostCompact 返回空字典；切换为 advisory 时返回提示且无需改动任何命令串
        cx_state = self.root / "cx_state"
        cx_logs = self.root / "cx_logs"
        handoff_dir = cx_state / "handoff"
        handoff_dir.mkdir(parents=True)
        cx_logs.mkdir(parents=True)
        rollout = self.root / "rollout-cx-u.jsonl"
        _append_jsonl(rollout, {"type": "session_meta", "payload": {"id": "cx-u", "source": "cli"}})
        payload = {"session_id": "cx-u", "cwd": str(self.project), "transcript_path": str(rollout), "trigger": "auto"}
        user_words.atomic_write_json(handoff_dir / "policy.json", {"mode": "off", "compact_limit": 0})
        self.assertEqual(
            uguard_hook.dispatch_hook_event(
                "codex", "PostCompact", payload, state_dir_str=str(cx_state), log_dir_str=str(cx_logs)
            ),
            {},
        )
        user_words.atomic_write_json(handoff_dir / "policy.json", {"mode": "advisory", "compact_limit": 0})
        res_adv = uguard_hook.dispatch_hook_event(
            "codex", "PostCompact", payload, state_dir_str=str(cx_state), log_dir_str=str(cx_logs)
        )
        self.assertIn("systemMessage", res_adv)

        # 3) /teamwork 过渡准备消息不误触发 _native_completion_bill
        tp_tw = self.root / "tw_prep.jsonl"
        _append_jsonl(tp_tw, {"type": "USER_INPUT", "step_index": 0, "content": "/teamwork 调研"})
        _append_jsonl(
            tp_tw,
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"id": "tw-1", "name": "invoke_subagent", "args": {"TypeName": "teamwork"}}]},
        )
        _append_jsonl(
            tp_tw,
            {
                "type": "GENERIC",
                "tool_result": {"call_id": "tw-1"},
                "content": 'Created the following subagent: {"conversationId":"tw-agent-1"}',
            },
        )
        _append_jsonl(
            tp_tw,
            {
                "type": "SYSTEM_MESSAGE",
                "content": "<SYSTEM_MESSAGE>\n[Message] sender=tw-agent-1 content=Your Teamwork run is being prepared. Before execution begins, we are putting together a short task interview.\n</SYSTEM_MESSAGE>",
            },
        )
        self.assertIsNone(reply_repeat_guard._native_completion_bill({"transcriptPath": str(tp_tw)}))

        # 4) Gate C 在 enforce 模式下豁免 .agents/skills/SKILL.md，但对正式交付 docs/design_spec.md 仍严格拦截
        big_body = "# Skill Doc\n\n" + ("Detailed workflow instructions for engineering automation.\n" * 120)
        with mock.patch.object(reply_repeat_guard, "EVIDENCE_GATE_MODE", "enforce"):
            for exempt_path in (".agents/skills/demo/SKILL.md", "plugins/demo/skills/x/SKILL.md", "skills/custom/SKILL.md"):
                tp_ex = self.root / f"gatec_{abs(hash(exempt_path))}.jsonl"
                _append_jsonl(tp_ex, {"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "写技能"})
                _append_jsonl(
                    tp_ex,
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps(exempt_path), "Content": json.dumps(big_body)}}],
                    },
                )
                _append_jsonl(tp_ex, {"step_index": 2, "type": "GENERIC", "status": "DONE", "content": "Wrote file"})
                _append_jsonl(tp_ex, {"step_index": 3, "type": "PLANNER_RESPONSE", "content": "已完成全文定稿交付。"})
                res_ex = reply_repeat_guard.handle_stop_repeat_guard(
                    {"conversationId": f"ex-{abs(hash(exempt_path))}", "terminationReason": "model_stop", "transcriptPath": str(tp_ex)}
                )
                self.assertEqual(res_ex["decision"], "allow", exempt_path)

        # 5) 默认 advisory 模式下，外部无头 cae_harness 完工账单仍必须硬阻断缺项汇报
        sid_ext = "ext-bill-hard"
        tp_ext = self.root / "ext_bill.jsonl"
        _append_jsonl(tp_ext, {"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "执行外部批处理"})
        _append_jsonl(tp_ext, {"step_index": 1, "type": "PLANNER_RESPONSE", "source": "MODEL", "content": "已全部完成。"})
        user_words.atomic_write_json(
            self.state / "heavy_completions" / f"{sid_ext}.json",
            [
                {
                    "run_id": "run-ext-1",
                    "subcommand": "batch",
                    "all_succeeded": False,
                    "reported_to_user": False,
                    "receipt_file": str(self.root / "batch_receipt.json"),
                    "streams": [
                        {"stream_id": "S1", "engine": "codex:default", "model": "m1", "succeeded": False, "status": "timeout", "token_usage": {"total_tokens": 500}},
                    ],
                    "token_usage_summary": {"total_tokens": 500},
                }
            ],
        )
        res_ext = reply_repeat_guard.handle_stop_repeat_guard(
            {"conversationId": sid_ext, "terminationReason": "model_stop", "transcriptPath": str(tp_ext)}
        )
        self.assertEqual(res_ext["decision"], "continue")

    def test_d4_d5_agent_spec_parity_client_rules_overlay_clamp_and_codex_permissions(self) -> None:
        """D4/D5: agents_spec.json 三端一致、@client 规则零泄漏过滤、local overlay 只紧不松与 Codex default_permissions 兼容。"""
        # 1) agents_spec.json 与三端只读子代理模板语义一致
        spec = json.loads((REPO_ROOT / "template" / "_shared" / "agents_spec.json").read_text(encoding="utf-8"))
        for agent_name, meta in spec["agents"].items():
            agy_text = (REPO_ROOT / "template" / "user-guard" / "agents" / f"{agent_name}.md").read_text(encoding="utf-8")
            codex_text = (REPO_ROOT / "template" / "codex" / "agents" / f"{agent_name}.toml").read_text(encoding="utf-8")
            claude_text = (REPO_ROOT / "template" / "claude" / "agents" / f"{agent_name}.md").read_text(encoding="utf-8")
            self.assertIn(meta["role_intro"], agy_text, agent_name)
            self.assertIn(meta["role_intro"], codex_text, agent_name)
            self.assertIn(meta["role_intro"], claude_text, agent_name)
            for t in meta["antigravity"]["tools"]:
                self.assertIn(t, agy_text)
            self.assertIn("disallowedTools: Write, Edit, MultiEdit, NotebookEdit, Bash", claude_text)
            self.assertIn("permissionMode: dontAsk", claude_text)
            self.assertIn("maxTurns: 20", claude_text)

        # 2) 按客户端条件渲染规则零泄漏
        raw_rules = (REPO_ROOT / "template" / "user-guard" / "rules" / "AGENTS.md").read_text(encoding="utf-8")
        for client in ("antigravity", "codex", "claude", "dsh"):
            rendered = INSTALLER.render_rules_for_client(raw_rules, client)
            self.assertNotIn("<!-- @client:", rendered, client)
            self.assertNotIn("<!-- @/client -->", rendered, client)
        dsh_rules = INSTALLER.render_rules_for_client(raw_rules, "dsh")
        for forbidden in ("GOAL_CANCELLED", "CC Switch", ".codex-worktrees", "status_guard.py", "reply_repeat_guard.py", "uguard_hook.py"):
            self.assertNotIn(forbidden, dsh_rules, f"DSH 规则不应包含 {forbidden}")
        cx_rules = INSTALLER.render_rules_for_client(raw_rules, "codex")
        self.assertNotIn("GOAL_CANCELLED", cx_rules)
        self.assertNotIn("CC Switch", cx_rules)
        self.assertIn(".codex-worktrees", cx_rules)
        self.assertIn("uguard_hook.py", cx_rules)
        cl_rules = INSTALLER.render_rules_for_client(raw_rules, "claude")
        self.assertNotIn("GOAL_CANCELLED", cl_rules)
        self.assertNotIn(".codex-worktrees", cl_rules)
        self.assertIn("CC Switch", cl_rules)
        ag_rules = INSTALLER.render_rules_for_client(raw_rules, "antigravity")
        self.assertIn("GOAL_CANCELLED", ag_rules)
        self.assertNotIn("CC Switch", ag_rules)
        self.assertNotIn(".codex-worktrees", ag_rules)

        # 3) fleet_profile.local.json 覆盖层：合法覆盖生效，但 hook_safety 上限只紧不松且不可覆写 schema_version
        ov_dir = self.root / "overlay_test"
        ov_dir.mkdir()
        base_pf = {
            "schema_version": 1,
            "runtime_tunables": {
                "hook_safety": {"wall_timeout_sec": 12, "max_rss_mib": 512},
                "dispatch_confirmation": {"heavy_min_streams": 2},
            },
        }
        local_loosen = {
            "schema_version": 99,
            "runtime_tunables": {
                "hook_safety": {"wall_timeout_sec": 999, "max_rss_mib": 99999},
                "dispatch_confirmation": {"heavy_min_streams": 4},
            },
        }
        pf_path = ov_dir / "fleet_profile.json"
        local_path = ov_dir / "fleet_profile.local.json"
        user_words.atomic_write_json(pf_path, base_pf)
        user_words.atomic_write_json(local_path, local_loosen)
        with mock.patch.dict(os.environ, {"CAE_FLEET_PROFILE": str(pf_path), "CAE_FLEET_PROFILE_LOCAL": str(local_path)}):
            merged_uw = user_words.load_fleet_profile()
            self.assertEqual(merged_uw["schema_version"], 1)
            self.assertEqual(merged_uw["runtime_tunables"]["hook_safety"]["wall_timeout_sec"], 12)
            self.assertEqual(merged_uw["runtime_tunables"]["hook_safety"]["max_rss_mib"], 512)
            self.assertEqual(merged_uw["runtime_tunables"]["dispatch_confirmation"]["heavy_min_streams"], 4)
        merged_inst = INSTALLER._deep_merge_profile_overlay(base_pf, local_loosen)
        self.assertEqual(merged_inst["schema_version"], 1)
        self.assertEqual(merged_inst["runtime_tunables"]["hook_safety"]["wall_timeout_sec"], 12)
        self.assertEqual(merged_inst["runtime_tunables"]["hook_safety"]["max_rss_mib"], 512)
        # 收紧 hook_safety 允许生效
        local_tighten = {"runtime_tunables": {"hook_safety": {"wall_timeout_sec": 8, "max_rss_mib": 256}}}
        tightened = INSTALLER._deep_merge_profile_overlay(base_pf, local_tighten)
        self.assertEqual(tightened["runtime_tunables"]["hook_safety"]["wall_timeout_sec"], 8)
        self.assertEqual(tightened["runtime_tunables"]["hook_safety"]["max_rss_mib"], 256)

        # 4) Codex config.toml 存在现代 default_permissions 时不注入旧 sandbox_mode；缺失时补齐旧 sandbox_mode
        modern_cfg = self.root / "codex_modern.toml"
        modern_cfg.write_text('default_permissions = "workspace"\n', encoding="utf-8")
        ed_modern = INSTALLER.CodexConfig(modern_cfg)
        INSTALLER._apply_codex_workspace_boundary(ed_modern)
        self.assertNotIn("sandbox_mode", ed_modern.source)
        self.assertIn('approval_policy = "on-request"', ed_modern.source)
        legacy_cfg = self.root / "codex_legacy.toml"
        legacy_cfg.write_text('model = "gpt-6.2-sol"\n', encoding="utf-8")
        ed_legacy = INSTALLER.CodexConfig(legacy_cfg)
        INSTALLER._apply_codex_workspace_boundary(ed_legacy)
        self.assertIn('sandbox_mode = "workspace-write"', ed_legacy.source)

        # 5) 仓库真实 fleet_profile.json 默认回退别名与结构化基线完整
        live_pf = json.loads(
            (REPO_ROOT / "template" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json").read_text(encoding="utf-8")
        )
        self.assertEqual(live_pf["client_catalog_rules"]["claude"]["default_fallback_alias"], "deepseek")
        self.assertEqual(live_pf["native_agent_profiles"]["dsh"]["hook_protocol"], "no_lifecycle_hooks")
        self.assertIn("client_capability_baselines", live_pf)

        # 6) _clean_retired_upstream_skills 能安全清理带只读属性的旧版 fleet-evolve 目录
        ro_skills_root = self.root / "ro_skills"
        ro_fe = ro_skills_root / "fleet-evolve" / "scripts"
        ro_fe.mkdir(parents=True)
        ro_file = ro_fe / "fleet_state.py"
        ro_file.write_text("# legacy\n", encoding="utf-8")
        os.chmod(ro_file, 0o444)
        os.chmod(ro_fe, 0o555)
        os.chmod(ro_skills_root / "fleet-evolve", 0o555)
        INSTALLER._clean_retired_upstream_skills(ro_skills_root, dry_run=False)
        self.assertFalse((ro_skills_root / "fleet-evolve").exists())

    def test_d6_four_client_capability_probe_matrix_dual_track_and_core_sha256(self) -> None:
        """D6: 四端四值探针正/负夹具、双轨 recommended_actions vs evolution_signals、以及 fleet_state core_sha256 扩面防篡改。"""
        live_pf = json.loads(
            (REPO_ROOT / "template" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json").read_text(encoding="utf-8")
        )
        active_all = {"antigravity": True, "codex": True, "claude": True, "dsh": True}

        # 1) 沙箱模式不执行 CLI 探针，所有信号返回 unknown_not_probed
        det_sbx = {c: {"native_binary": f"/fake/{c}"} for c in active_all}
        mat_sbx, sigs_sbx, mf_sbx = INSTALLER._probe_client_capabilities(
            det_sbx, {"antigravity": "1.5.19", "codex": "0.172.0", "claude": "2.1.332", "dsh": "0.2.2-rc.5"},
            live_pf, REPO_ROOT, is_sandbox_run=True, active_clients=active_all,
        )
        self.assertFalse(mf_sbx)
        self.assertEqual(sigs_sbx, [])
        for c in active_all:
            self.assertEqual(mat_sbx[c]["probe_status"], "unknown_not_probed")
            self.assertEqual(mat_sbx[c]["missing_mode_guide_entries"], [])

        # 2) 正/负夹具：DSH --patch 属 deferred（进 tolerated_signals 不报演进）；DSH PreToolUse 与 Codex 主版本跃迁进 evolution_signals；
        #    AGY plugin validate 失败触发 manifest_failed
        def fake_cmd(binary: str, args: list, timeout: int = 3):
            if "agy" in binary and args[:2] == ["plugin", "validate"]:
                return 1, "", "invalid hook schema"
            if "dsh" in binary:
                return 0, "Usage: dsh [OPTIONS] --patch --dump-config PreToolUse", ""
            if "codex" in binary:
                return 0, "Usage: codex [OPTIONS] exec", ""
            return 0, "Usage: cli [OPTIONS]", ""

        det_live = {
            "antigravity": {"native_binary": "/bin/agy"},
            "codex": {"native_binary": "/bin/codex"},
            "claude": {"native_binary": "/bin/claude"},
            "dsh": {"native_binary": "/bin/dsh", "hard_hooks_supported": True, "hook_protocol": "hard_hooks_detected", "evidence_state": "detected_heuristic"},
        }
        with mock.patch.object(INSTALLER, "_run_readonly_cli_cmd", side_effect=fake_cmd):
            mat_live, sigs_live, mf_live = INSTALLER._probe_client_capabilities(
                det_live,
                {"antigravity": "1.5.19", "codex": "1.0.0", "claude": "2.1.332", "dsh": "0.2.2-rc.5"},
                live_pf,
                REPO_ROOT,
                is_sandbox_run=False,
                active_clients=active_all,
            )
        self.assertTrue(mf_live)
        self.assertEqual(mat_live["antigravity"]["manifest_validation"]["state"], "unsupported")
        self.assertIn("--patch", mat_live["dsh"]["tolerated_signals"])
        self.assertNotIn("--patch", mat_live["dsh"]["actionable_signals"])
        self.assertTrue(mat_live["codex"]["major_version_jump"])
        codes = {s["code"] for s in sigs_live}
        self.assertIn("evolve_dsh_hard_hooks", codes)
        self.assertIn("evolve_codex_capabilities", codes)

        # 3) 文档漂移负夹具：临时缺少原生模式章节时触发 update_mode_workflow_guide（evidence_state == supported_verified）
        fake_repo = self.root / "fake_repo_doc"
        (fake_repo / "docs").mkdir(parents=True)
        (fake_repo / "docs" / "workflow-guide.md").write_text("# 空指南\n不包含任何模式名。\n", encoding="utf-8")
        _, sigs_doc, _ = INSTALLER._probe_client_capabilities(
            det_sbx, {}, live_pf, fake_repo, is_sandbox_run=True, active_clients=active_all,
        )
        doc_codes = [s for s in sigs_doc if s["code"] == "update_mode_workflow_guide"]
        self.assertEqual(len(doc_codes), 4)
        self.assertTrue(all(s["evidence_state"] == "supported_verified" for s in doc_codes))

        # 4) fleet_state core_sha256 覆盖 hooks / rules / agents / _shared 任一核心文件改动
        mini_repo = self.root / "mini_upstream"
        for rel in (
            "install.py",
            "template/user-guard/plugin.json",
            "template/user-guard/hooks.json",
            "template/user-guard/hooks/uguard_hook.py",
            "template/user-guard/rules/AGENTS.md",
            "template/user-guard/skills/fleet-evolve/SKILL.md",
            "template/user-guard/skills/cae-harness/fleet_profile.json",
            "template/claude/agents/readonly-auditor.md",
            "template/_shared/agents_spec.json",
        ):
            dst = mini_repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / rel, dst)
        base_sha = FLEET_STATE._repo_meta(mini_repo)["core_sha256"]
        for mutate_rel in (
            "template/user-guard/hooks/uguard_hook.py",
            "template/user-guard/rules/AGENTS.md",
            "template/claude/agents/readonly-auditor.md",
            "template/_shared/agents_spec.json",
        ):
            target = mini_repo / mutate_rel
            orig_bytes = target.read_bytes()
            target.write_bytes(orig_bytes + b"\n# tamper\n")
            mutated_sha = FLEET_STATE._repo_meta(mini_repo)["core_sha256"]
            self.assertNotEqual(mutated_sha, base_sha, f"core_sha256 未覆盖 {mutate_rel}")
            target.write_bytes(orig_bytes)


if __name__ == "__main__":
    unittest.main()
