# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""Claude 生命周期、模型路由与 CC Switch 管理的纯离线回归。

临时文件优先使用系统临时目录（受限或与仓库重叠时回退至 .pytest_cache/claude_offline）；HTTP、DNS、代理和上游响应均使用本地夹具。
"""

import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from contextlib import redirect_stdout, redirect_stderr
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent

_INSTALLED_USER_GUARD_ENV_KEYS = frozenset({
    "USER_GUARD_INSTALLED_DIR",
    "USER_GUARD_INSTALLED_CODEX_DIR",
    "USER_GUARD_INSTALLED_CLAUDE_DIR",
    "USER_GUARD_INSTALLED_DSH_DIR",
})
_SCRUBBED_EXACT_ENV_KEYS = frozenset({
    "PLUGIN_DATA",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "PYTHONPATH",
})
_SCRUBBED_ENV_PREFIXES = (
    "CAE_",
    "AGY_",
    "ANTIGRAVITY_",
    "CODEX_",
    "CLAUDE",
    "ANTHROPIC_",
    "DSH_",
    "GIT_",
    "XDG_",
)
_REQUIRED_HOOK_FILES = (
    "handoff_gate.py",
    "reply_repeat_guard.py",
    "session_context_loader.py",
    "status_guard.py",
    "user_words.py",
)


def _should_scrub_host_env(key: str, *, keep_installed_dirs: bool = False) -> bool:
    ku = key.upper()
    if ku in _SCRUBBED_EXACT_ENV_KEYS or ku.startswith(_SCRUBBED_ENV_PREFIXES):
        return True
    if ku.startswith("USER_GUARD_"):
        if keep_installed_dirs and ku in _INSTALLED_USER_GUARD_ENV_KEYS:
            return False
        return True
    return False


def _resolve_hooks_dir(repo_root: Path = REPO_ROOT) -> Path:
    fallback = (repo_root / "template/user-guard/hooks").resolve()
    raw = os.environ.get("USER_GUARD_INSTALLED_DIR", "").strip()
    if not raw:
        return fallback
    try:
        candidate = (Path(raw).expanduser() / "hooks").resolve()
        if candidate.is_dir() and all((candidate / name).is_file() for name in _REQUIRED_HOOK_FILES):
            return candidate
    except (OSError, RuntimeError, ValueError):
        pass
    return fallback


HOOKS_DIR = _resolve_hooks_dir(REPO_ROOT)
sys.path.insert(0, str(HOOKS_DIR))


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with mock.patch.dict(
    os.environ,
    {k: v for k, v in os.environ.items() if not _should_scrub_host_env(k, keep_installed_dirs=True)},
    clear=True,
):
    import handoff_gate
    import reply_repeat_guard
    import session_context_loader
    import status_guard
    import user_words

    router = _load_module("claude_router_offline", REPO_ROOT / "template/claude/router/router.py")
    admin = _load_module("ccswitch_admin_offline", REPO_ROOT / "template/claude/router/ccswitch_admin.py")
    installer = _load_module("claude_installer_offline", REPO_ROOT / "install.py")
    fleet_state = _load_module("fleet_state_offline", REPO_ROOT / "template/user-guard/skills/fleet-evolve/scripts/fleet_state.py")


def _append_jsonl(path, entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _user(text, **fields):
    return {"type": "user", "message": {"role": "user", "content": text},
            "timestamp": "2026-11-14T01:00:00Z", **fields}


HANDOFF_TEXT = (
    "已验证：离线回归通过。\nGit 工作区：仍有待提交改动。\n未完成：真实宿主接入验收。\n"
    "【新对话提示词】\n请先核对当前工作区差异与用户最新裁定，再继续完成真实宿主接入验收。\n"
)


class OfflineSandbox(unittest.TestCase):
    def setUp(self):
        import time

        temporary = None
        cand = None
        try:
            cand = tempfile.TemporaryDirectory(prefix="claude-offline-")
            cand_path = Path(cand.name)
            installer._validate_path(cand_path, directory=True)
            resolved_cand = cand_path.resolve()
            if (
                resolved_cand == REPO_ROOT
                or REPO_ROOT in resolved_cand.parents
                or resolved_cand in REPO_ROOT.parents
            ):
                raise ValueError("system temp overlaps REPO_ROOT")
            probe = cand_path / ".probe"
            probe.write_bytes(b"ok")
            probe.unlink()
            temporary = cand
        except (OSError, ValueError):
            if cand is not None:
                cand.cleanup()
            cache_dir = (REPO_ROOT / ".pytest_cache" / "claude_offline").resolve()
            cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(prefix="claude-offline-", dir=cache_dir)

        def _cleanup_tempdir() -> None:
            try:
                for attempt in range(5):
                    try:
                        if Path(temporary.name).exists():
                            shutil.rmtree(temporary.name)
                        break
                    except OSError:
                        if attempt == 4:
                            raise
                        time.sleep(0.05)
            finally:
                temporary.cleanup()

        self.addCleanup(_cleanup_tempdir)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "home"
        self.claude_home = self.home / ".claude"
        self.state = self.claude_home / "hooks/state"
        self.logs = self.claude_home / "hooks/logs"
        self.sid = "claude_sess_alpha"
        self.transcript = self.claude_home / "projects/work" / f"{self.sid}.jsonl"
        self.payload = {"session_id": self.sid, "transcript_path": str(self.transcript), "cwd": str(self.project)}
        clean_env = {
            k: v for k, v in os.environ.items()
            if not _should_scrub_host_env(k, keep_installed_dirs=True)
        }
        for _inst_k in list(_INSTALLED_USER_GUARD_ENV_KEYS):
            if _inst_k in clean_env and not clean_env[_inst_k].strip():
                clean_env.pop(_inst_k, None)
        clean_env.update({
            "CLAUDE_CONFIG_DIR": str(self.claude_home), "HOME": str(self.home), "USERPROFILE": str(self.home),
            "PLUGIN_DATA": str(self.root / "plugin-data"), "USER_GUARD_STATE_DIR": str(self.state),
            "USER_GUARD_LOGS_DIR": str(self.logs), "CAE_HARNESS_DEPTH": "0", "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(self.root), "TMP": str(self.root), "TMPDIR": str(self.root),
        })
        patch = mock.patch.dict(os.environ, clean_env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(tempfile, "tempdir", str(self.root))
        patch.start()
        self.addCleanup(patch.stop)
        router._key_cache.clear()
        router._proxy_cache = None

    def cli(self, script, payload, *args):
        raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(HOOKS_DIR / script), "--client", "claude", *map(str, args)],
            input=raw, capture_output=True, text=True, encoding="utf-8", cwd=self.project, timeout=20,
        )

    def tool(self, name, **args):
        return {**self.payload, "hook_event_name": "PreToolUse", "tool_name": name, "tool_input": args}

    def guard(self, name, **args):
        return status_guard.handle_claude_pre_tool_use(self.tool(name, **args), self.state, self.logs)

    def archive(self):
        return user_words.archive_claude_session_words(self.payload, self.state / "session-context")

    def gate(self, event, mode="enforce", limit=2, **fields):
        return handoff_gate.handle_claude_handoff(
            {**self.payload, "hook_event_name": event, **fields}, self.state / "handoff", mode, limit,
        )

    def gate_state(self):
        return json.loads((self.state / "handoff" / f"{self.sid}_handoff_gate.json").read_text(encoding="utf-8"))

    def boundaries(self, count=2):
        for index in range(count):
            _append_jsonl(self.transcript, {"type": "system", "subtype": "compact_boundary",
                                           "uuid": f"boundary_{index}"})

    def database(self, version=26):
        path = self.root / "cc-switch.db"
        with closing(sqlite3.connect(path)) as con:
            con.executescript("""
                CREATE TABLE providers (
                    id TEXT PRIMARY KEY, app_type TEXT, name TEXT, settings_config TEXT,
                    is_current INTEGER DEFAULT 0, sort_index INTEGER, created_at INTEGER,
                    notes TEXT, meta TEXT, category TEXT, in_failover_queue INTEGER DEFAULT 0,
                    cost_multiplier TEXT DEFAULT '1.0', website_url TEXT, icon TEXT, icon_color TEXT,
                    limit_daily_usd REAL, limit_monthly_usd REAL, provider_type TEXT
                );
                CREATE TABLE prompts (id TEXT PRIMARY KEY, app_type TEXT, name TEXT, enabled INTEGER, content TEXT);
            """)
            con.execute(f"PRAGMA user_version={int(version)}")
            official = {"env": {"ANTHROPIC_AUTH_TOKEN": "tok_official_test", "ANTHROPIC_MODEL": "official-model",
                                "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1", "ENABLE_TOOL_SEARCH": "false"}}
            con.execute("INSERT INTO providers (id,app_type,name,settings_config,is_current,sort_index,created_at) "
                        "VALUES (?,?,?,?,?,?,?)", ("claude-official", "claude", "Official", json.dumps(official), 1, 1, 1))
            con.execute("INSERT INTO providers (id,app_type,name,settings_config,is_current) VALUES (?,?,?,?,?)",
                        ("codex_official", "codex", "Other", "{}", 1))
            con.commit()
        return path

    def settings(self):
        path = self.claude_home / "settings.json"
        user_words.atomic_write_json(path, {"hooks": {"Stop": [{"hooks": []}]}, "effortLevel": "xhigh",
                                           "env": {"PERSONAL_SETTING": "keep"}})
        return path

    def cards(self, db):
        with closing(sqlite3.connect(db)) as con:
            return {name: json.loads(raw) for name, raw in con.execute(
                "SELECT name, settings_config FROM providers WHERE app_type='claude'")}

    def replace_card(self, db, name, config):
        with closing(sqlite3.connect(db)) as con:
            con.execute("UPDATE providers SET settings_config=? WHERE name=? AND app_type='claude'",
                        (json.dumps(config), name))
            con.commit()


class TestClaudeArchive(OfflineSandbox):
    def test_identity_validation_and_missing_transcript(self):
        self.assertEqual(user_words.claude_hook_session({"session_id": "../escape"}),
                         ("", None, "缺少合法 session_id", False))
        self.assertEqual(user_words.claude_hook_session(self.payload),
                         (self.sid, None, "未找到 Claude 会话轨迹文件", False))
        self.assertFalse(self.archive()["complete"])

    def test_discovery_uses_latest_main_transcript_only(self):
        _append_jsonl(self.transcript, _user("较早记录"))
        later = self.claude_home / "projects/other" / f"{self.sid}.jsonl"
        _append_jsonl(later, _user("较新记录"))
        os.utime(self.transcript, ns=(1_000_000_000, 1_000_000_000))
        os.utime(later, ns=(2_000_000_000, 2_000_000_000))
        _append_jsonl(later.parent / "subagents" / f"{self.sid}.jsonl", _user("子代理记录"))
        self.assertEqual(user_words.claude_hook_session({"session_id": self.sid})[1], later)
        self.assertIsNone(user_words.claude_hook_session({**self.payload, "transcript_path": str(self.root / "missing.jsonl")})[1])

    def test_plain_text_blocks_timestamp_and_injected_noise(self):
        _append_jsonl(self.transcript, _user("<ide_opened_file>source.py</ide_opened_file>请检查守恒残差。"))
        _append_jsonl(self.transcript, _user([
            {"type": "text", "text": "<ide_selection>无关选区</ide_selection>请保留容差。"},
            {"type": "text", "text": "<system-reminder>自动提示</system-reminder><ide_diagnostics>诊断</ide_diagnostics>"},
        ]))
        result = user_words.extract_claude_user_words(self.transcript)
        self.assertTrue(result["complete"])
        self.assertEqual([word["content"] for word in result["words"]], ["请检查守恒残差。", "请保留容差。"])
        self.assertEqual(result["words"][0]["created_at"], "2026-11-14T01:00:00Z")

    def test_sidechain_meta_tool_output_and_local_echo_are_filtered(self):
        for entry in [
            _user("子代理消息", isSidechain=True), _user("系统注入", isMeta=True),
            _user([{"type": "tool_result", "content": "工具输出"}]),
            _user("<local-command-stdout>回显</local-command-stdout>"),
            _user("<local-command-caveat>本地警告</local-command-caveat>"),
            _user("<ide_opened_file>只有注入内容</ide_opened_file>"),
            _user("真实指令"),
        ]:
            _append_jsonl(self.transcript, entry)
        self.assertEqual([word["content"] for word in self.archive()["words"]], ["真实指令"])

    def test_slash_commands_are_preserved_with_arguments(self):
        _append_jsonl(self.transcript, _user("<command-name>/review</command-name><command-args>solver.py</command-args>"))
        self.assertEqual(self.archive()["words"][0]["content"], "/review solver.py")

    def test_question_answers_from_entry_and_result_blocks(self):
        for qid in ("toolu_q1", "toolu_q2", "toolu_q3"):
            _append_jsonl(self.transcript, {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": qid, "name": "AskUserQuestion", "input": {"questions": []}}]}})
        _append_jsonl(self.transcript, {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_read_1", "name": "Read", "input": {"file_path": "fixture.json"}}]}})
        _append_jsonl(self.transcript, _user([{"type": "tool_result", "tool_use_id": "toolu_q1", "content": "选择已提交"}],
                                           toolUseResult={"answers": {"格式": "DGSEM", "检查": ["守恒", "收敛阶"]}}))
        _append_jsonl(self.transcript, _user([{"type": "tool_result", "tool_use_id": "toolu_q2",
                                             "content": json.dumps({"answers": {"下一步": "运行回归"}})}]))
        _append_jsonl(self.transcript, _user([{"type": "tool_result", "tool_use_id": "toolu_q3", "content": [
            {"type": "text", "text": 'User has answered your questions: "方案"="保留现状". You can now continue with the user\'s answers in mind.'}]}]))
        _append_jsonl(self.transcript, _user([{"type": "tool_result", "tool_use_id": "toolu_read_1",
                                             "content": json.dumps({"answers": {"伪造": "不应归档"}})}],
                                           toolUseResult={"answers": {"伪造": "不应归档"}}))
        result = self.archive()
        self.assertEqual(len(result["words"]), 3)
        self.assertTrue(all(word["kind"] == "ask_question_answer" for word in result["words"]))
        self.assertIn("检查：守恒、收敛阶", result["words"][0]["content"])
        self.assertIn("下一步：运行回归", result["words"][1]["content"])
        self.assertIn("保留现状", result["words"][2]["content"])

    def test_queued_human_input_deduplicates_only_consumed_copy(self):
        _append_jsonl(self.transcript, {"type": "attachment", "attachment": {
            "type": "queued_command", "origin": {"kind": "human"}, "prompt": "检查接口"}})
        _append_jsonl(self.transcript, _user("检查接口"))
        _append_jsonl(self.transcript, _user("检查接口"))
        _append_jsonl(self.transcript, {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "未消费指令"}})
        _append_jsonl(self.transcript, {"type": "attachment", "attachment": {
            "type": "queued_command", "origin": {"kind": "agent"}, "prompt": "自动任务"}})
        self.assertEqual([word["content"] for word in self.archive()["words"]], ["检查接口", "检查接口", "未消费指令"])

    def test_archive_directory_matches_codex_rule_and_manifest_digest(self):
        _append_jsonl(self.transcript, _user("原话正文"))
        for directory in (self.state / "session-context", self.state, self.root / "custom-state"):
            with self.subTest(directory=directory.name):
                result = user_words.archive_claude_session_words(self.payload, directory)
                expected_base = directory.parent if directory.name == "session-context" else directory
                path = expected_base / "user-words" / f"{self.sid}.md"
                self.assertEqual(Path(result["saved"]["file"]), path)
                manifest = json.loads(Path(result["meta_path"]).read_text(encoding="utf-8"))
                self.assertTrue(str(result["meta_path"]).endswith(".meta.json"))
                self.assertEqual(manifest["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertTrue(result["saved"]["verified"])

    def test_bad_json_and_utf8_preserve_verified_archive(self):
        _append_jsonl(self.transcript, _user("已验证的用户裁定"))
        initial = self.archive()
        md = Path(initial["saved"]["file"])
        meta = Path(initial["meta_path"])
        original = md.read_bytes(), meta.read_bytes()
        with self.transcript.open("ab") as stream:
            stream.write(b'broken-json\n\xff\n{"type":')
        result = self.archive()
        self.assertFalse(result["complete"])
        self.assertTrue(result["issues"])
        self.assertTrue(result["saved"]["preserved"])
        self.assertEqual((md.read_bytes(), meta.read_bytes()), original)

    def test_missing_or_rewritten_source_preserves_verified_history(self):
        _append_jsonl(self.transcript, _user("第一轮约束"))
        _append_jsonl(self.transcript, _user("第二轮约束"))
        initial = self.archive()
        path = Path(initial["saved"]["file"])
        original = path.read_bytes()
        self.transcript.write_bytes(b"")
        _append_jsonl(self.transcript, _user("压缩后仅可见新输入"))
        self.assertTrue(self.archive()["saved"]["preserved"])
        self.transcript.unlink()
        self.assertTrue(self.archive()["saved"]["preserved"])
        self.assertEqual(path.read_bytes(), original)

    def test_bom_unicode_separator_and_nonobject_corruption(self):
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_bytes(b"\xef\xbb\xbf" + json.dumps(_user("正文\u2028保留"), ensure_ascii=False).encode() + b"\n[]\n")
        result = user_words.extract_claude_user_words(self.transcript)
        self.assertEqual(result["words"][0]["content"], "正文\u2028保留")
        self.assertFalse(result["complete"])

    def test_session_start_function_and_cli_return_native_json_and_log(self):
        _append_jsonl(self.transcript, _user("用中文核对离散格式。"))
        log = self.logs / "context.jsonl"
        direct = session_context_loader.handle_claude_session_start(self.payload, str(self.state / "session-context"), str(log))
        proc = self.cli("session_context_loader.py", {**self.payload, "source": "compact"},
                        "--state-dir", self.state / "session-context", "--log", log)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        response = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual(response["hookEventName"], "SessionStart")
        self.assertIn("恢复来源：Claude compact", response["additionalContext"])
        self.assertIn(str(self.state / "user-words" / f"{self.sid}.md"), response["additionalContext"])
        self.assertIn("SHA-256", direct["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["client"], "claude")

    def test_context_budget_keeps_initial_and_recent_instructions(self):
        for index in range(20):
            _append_jsonl(self.transcript, _user(f"裁定_{index}：" + "必须核对原始证据。" * 250))
        result = session_context_loader.handle_claude_session_start(self.payload, str(self.state / "session-context"))
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(text), 8000)
        self.assertIn("裁定_0：", text)
        self.assertIn("裁定_19：", text)
        result = session_context_loader.handle_claude_session_start({**self.payload, "source": "s" * 12000}, str(self.state))
        self.assertLessEqual(len(result["hookSpecificOutput"]["additionalContext"]), 8000)

    def test_loader_bad_stdin_exits_two_and_runtime_error_degrades(self):
        for raw in ("{", "[]"):
            proc = self.cli("session_context_loader.py", raw, "--state-dir", self.state)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("JSON parse error", proc.stderr)
            self.assertEqual(proc.stdout, "")
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["session_context_loader.py", "--client", "claude"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(self.payload))), \
                mock.patch.object(sys, "stdout", output), \
                mock.patch.object(user_words, "archive_claude_session_words", side_effect=OSError("测试读取失败")):
            session_context_loader.main()
        self.assertIn("提取状态：异常", json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"])


class TestClaudeStatusGuard(OfflineSandbox):
    def test_project_writes_edits_notebook_and_commands_are_allowed(self):
        for name, args in (
            ("Write", {"file_path": str(self.project / "solver.py")}),
            ("Edit", {"file_path": "solver.py"}), ("MultiEdit", {"path": "solver.py"}),
            ("NotebookEdit", {"notebook_path": "analysis.ipynb"}),
            ("Bash", {"command": "echo ok > output.txt"}),
            ("PowerShell", {"command": "Set-Content -LiteralPath solver.py -Value ok"}),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.guard(name, **args), {})

    def test_only_claude_runtime_directories_are_exempt(self):
        for name in ("projects", "todos", "session-env", "statsig", "telemetry", "hooks/state", "hooks/logs"):
            with self.subTest(name=name):
                self.assertEqual(self.guard("Write", file_path=str(self.claude_home / name / "record.json")), {})
        for name in ("CLAUDE.md", "settings.json", "settings.local.json", "agents/reader.md", "hooks/status_guard.py", "router/router.py"):
            with self.subTest(name=name):
                response = self.guard("Write", file_path=str(self.claude_home / name))["hookSpecificOutput"]
                self.assertEqual(response["permissionDecision"], "ask")
                self.assertIn("项目外目标路径", response["permissionDecisionReason"])

    def test_external_mutation_asks_interactively_and_denies_all_headless_depths(self):
        outside = str(self.root / "business/result.txt")
        for depth, expected in (("", "ask"), ("0", "ask"), ("1", "deny"), ("2", "deny")):
            with self.subTest(depth=depth), mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": depth}):
                self.assertEqual(self.guard("Write", file_path=outside)["hookSpecificOutput"]["permissionDecision"], expected)

    def test_inline_python_ast_worktree_and_installer_external_writes(self):
        commands = [
            "python -c \"from pathlib import Path; root=Path('../business'); (root/'out.txt').write_text('x')\"",
            "git worktree add ../business -b branch_unit",
            "python -B install.py --yes",
        ]
        for command in commands:
            with self.subTest(command=command):
                response = self.guard("Bash", command=command, description="更新测试产物")["hookSpecificOutput"]
                self.assertEqual(response["permissionDecision"], "ask")
                self.assertIn("触发语句", response["permissionDecisionReason"])
                self.assertIn("更新测试产物", response["permissionDecisionReason"])

    def test_read_same_log_denied_but_pagination_and_appends_allowed(self):
        path = self.project / "solver.log"
        path.write_bytes(b"step 1\n")
        args = {"file_path": str(path), "offset": 1, "limit": 20}
        self.assertEqual(self.guard("Read", **args), {})
        result = self.guard("Read", **args)["hookSpecificOutput"]
        self.assertEqual(result["permissionDecision"], "deny")
        self.assertNotIn("Reactive Wakeup", result["permissionDecisionReason"])
        self.assertNotIn("反重力", result["permissionDecisionReason"])
        self.assertEqual(self.guard("Read", **{**args, "offset": 21}), {})
        self.assertEqual(self.guard("Read", **{**args, "limit": 40}), {})
        with path.open("ab") as stream:
            stream.write(b"step 2\n")
        self.assertEqual(self.guard("Read", **args), {})

    def test_grep_queries_include_all_parameters_and_directory_evidence(self):
        (self.project / "solver.log").write_bytes(b"residual=0.01\n")
        args = {"path": str(self.project), "glob": "*.log", "pattern": "residual", "output_mode": "content", "head_limit": 10, "offset": 0}
        self.assertEqual(self.guard("Grep", **args), {})
        self.assertEqual(self.guard("Grep", **args)["hookSpecificOutput"]["permissionDecision"], "deny")
        for key, value in (("pattern", "step"), ("head_limit", 20), ("output_mode", "files_with_matches"), ("offset", 10), ("glob", "solver*.log")):
            with self.subTest(key=key):
                self.assertEqual(self.guard("Grep", **{**args, key: value}), {})
        (self.project / "other.log").write_bytes(b"new evidence\n")
        self.assertEqual(self.guard("Grep", **args), {})

    def test_bash_tail_and_powershell_log_queries_track_changes(self):
        path = self.project / "solver.log"
        path.write_bytes(b"step 1\n")
        for name, command in (("Bash", "tail -n 20 solver.log"), ("PowerShell", "Get-Content solver.log -Tail 20")):
            with self.subTest(name=name):
                self.assertEqual(self.guard(name, command=command), {})
                self.assertEqual(self.guard(name, command=command)["hookSpecificOutput"]["permissionDecision"], "deny")
                with path.open("ab") as stream:
                    stream.write(b"next step\n")
                self.assertEqual(self.guard(name, command=command), {})

    def test_source_files_and_own_runtime_logs_do_not_poll_block(self):
        for path in (self.project / "solver.py", self.logs / "lifecycle.log"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"content\n")
            for _ in range(3):
                self.assertEqual(self.guard("Read", file_path=str(path)), {})

    def test_cli_native_ask_deny_and_allowed_json(self):
        payload = self.tool("Write", file_path=str(self.claude_home / "CLAUDE.md"))
        proc = self.cli("status_guard.py", payload, "--state-dir", self.state, "--log-dir", self.logs, "--unused-flag")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")
        with mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": "1"}):
            proc = self.cli("status_guard.py", payload, "--state-dir", self.state, "--log-dir", self.logs)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        proc = self.cli("status_guard.py", self.tool("Edit", file_path="solver.py"), "--state-dir", self.state, "--log-dir", self.logs)
        self.assertEqual(json.loads(proc.stdout), {})


class TestClaudeReplyGuard(OfflineSandbox):
    def reply(self, text, **fields):
        return reply_repeat_guard.handle_claude_stop_repeat_guard(
            {"session_id": self.sid, "last_assistant_message": text, **fields}, self.state, self.logs,
        )

    def assistant(self, text, identity):
        _append_jsonl(self.transcript, {"type": "assistant", "uuid": identity,
                                       "message": {"content": [{"type": "text", "text": text}]}})

    def test_long_replies_and_questions_are_allowed(self):
        for text in ("任务仍在运行，" + "守恒残差与参考解已逐项核对。" * 40, "仍在运行，是否继续等待？"):
            for _ in range(4):
                self.assertEqual(self.reply(text), {})

    def test_changing_steps_residuals_and_task_subjects_are_allowed(self):
        for step in range(5):
            self.assertEqual(self.reply(f"仍在计算，第 {step} 步，残差 {1 / (step + 1):.3e}。"), {})
        for text in ("求解器仍在运行。", "回归测试仍在运行。", "安装流程仍在运行。"):
            self.assertEqual(self.reply(text), {})

    def test_three_homogeneous_wait_replies_block_once_without_transcript(self):
        replies = ["后台任务仍在运行，请稍候。", "后台任务仍在运行，继续等待。", "后台任务仍在运行，请稍候。"]
        self.assertEqual(self.reply(replies[0]), {})
        self.assertEqual(self.reply(replies[1]), {})
        self.assertEqual(self.reply(replies[2]), {"decision": "block", "reason": reply_repeat_guard.BLOCK_REASON_TEXT})
        self.assertEqual(self.reply(replies[2]), {})

    def test_stop_hook_active_and_invalid_session_do_no_state_work(self):
        with mock.patch.object(user_words, "claude_hook_session") as resolver:
            self.assertEqual(self.reply("仍在运行。", stop_hook_active=True), {})
            resolver.assert_not_called()
        self.assertEqual(self.reply("仍在运行。", session_id="../invalid"), {})
        self.assertFalse(self.state.exists())

    def test_transcript_message_idempotency_and_tool_result_filtering(self):
        _append_jsonl(self.transcript, _user("继续检查", uuid="user_alpha"))
        for index in range(3):
            self.assistant("后台任务仍在运行。", f"assistant_{index}")
            _append_jsonl(self.transcript, _user([{"type": "tool_result", "content": "工具结果"}]))
            result = self.reply("后台任务仍在运行。", transcript_path=str(self.transcript))
            self.assertEqual(result.get("decision"), "block" if index == 2 else None)
            for _ in range(2):
                self.assertEqual(self.reply("后台任务仍在运行。", transcript_path=str(self.transcript)), {})

    def test_new_user_turn_resets_single_block_budget(self):
        for turn in range(2):
            _append_jsonl(self.transcript, _user("同样的用户正文", uuid=f"user_{turn}"))
            for index in range(3):
                self.assistant("仍在计算，等待结果。", f"assistant_{turn}_{index}")
                result = reply_repeat_guard.handle_claude_stop_repeat_guard(self.payload, self.state, self.logs)
                self.assertEqual(result.get("decision"), "block" if index == 2 else None)

    def test_tail_scan_crosses_large_turn_and_ignores_meta_sidechain(self):
        _append_jsonl(self.transcript, _user("主回合", uuid="main_user"))
        _append_jsonl(self.transcript, {"type": "progress", "content": "x" * (reply_repeat_guard.TAIL_SCAN_BYTES + 200)})
        for index in range(3):
            _append_jsonl(self.transcript, _user("不是用户回合", isMeta=True))
            _append_jsonl(self.transcript, _user("子代理回合", isSidechain=True))
            self.assistant("后台任务仍在运行。", f"main_assistant_{index}")
            result = reply_repeat_guard.handle_claude_stop_repeat_guard(self.payload, self.state, self.logs)
            self.assertEqual(result.get("decision"), "block" if index == 2 else None)

    def test_cli_uses_transcript_reply_and_active_stop_never_blocks(self):
        _append_jsonl(self.transcript, _user("运行回归", uuid="cli_user"))
        for index in range(3):
            self.assistant("后台任务仍在运行。", f"cli_assistant_{index}")
            proc = self.cli("reply_repeat_guard.py", self.payload, "--state-dir", self.state, "--log-dir", self.logs)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout).get("decision"), "block" if index == 2 else None)
        proc = self.cli("reply_repeat_guard.py", {**self.payload, "stop_hook_active": True}, "--state-dir", self.state, "--log-dir", self.logs)
        self.assertEqual(json.loads(proc.stdout), {})


class TestClaudeHandoff(OfflineSandbox):
    def test_advisory_never_denies_and_off_has_no_side_effects(self):
        self.assertEqual(self.gate("PreToolUse", mode="off", tool_name="Agent"), {})
        self.assertFalse(self.state.exists())
        self.boundaries(4)
        self.gate("SessionStart", mode="advisory", source="compact")
        self.assertEqual(self.gate("PreToolUse", mode="advisory", tool_name="Agent"), {})
        self.assertEqual(self.gate("Stop", mode="advisory", last_assistant_message="仍在运行"), {})
        self.assertEqual(self.gate_state()["phase"], "advisory")
        self.assertEqual(self.gate_state()["client"], "claude")

    def test_postcompact_protocol_and_duplicate_boundary_observations(self):
        self.boundaries(1)
        response = self.gate("PostCompact", mode="advisory")
        self.assertIn("systemMessage", response)
        self.assertNotIn("hookSpecificOutput", response)
        self.gate("SessionStart", mode="advisory", source="compact")
        self.gate("PostCompact", mode="advisory")
        self.assertEqual(self.gate_state()["compactionCount"], 1)

    def test_event_before_boundary_is_merged_and_microcompact_counted(self):
        _append_jsonl(self.transcript, _user("继续"))
        self.gate("PostCompact", mode="advisory")
        self.gate("SessionStart", mode="advisory", source="compact")
        self.assertEqual(self.gate_state()["compactionCount"], 1)
        self.boundaries(1)
        self.gate("PreToolUse", mode="advisory", tool_name="Read")
        self.assertEqual(self.gate_state()["compactionCount"], 1)
        _append_jsonl(self.transcript, {"type": "system", "subtype": "microcompact_boundary", "uuid": "micro_alpha"})
        self.gate("PostCompact", mode="advisory")
        self.assertEqual(self.gate_state()["compactionCount"], 2)

    def test_enforce_drain_freeze_and_mutating_tool_budget(self):
        self.boundaries()
        response = self.gate("SessionStart", source="compact")["hookSpecificOutput"]
        self.assertEqual(response["hookEventName"], "SessionStart")
        self.assertEqual(self.gate_state()["phase"], "drain")
        for index in range(handoff_gate.CODEX_DRAIN_ACTIONS):
            self.gate("PreToolUse", tool_name="Write", tool_input={"file_path": "solver.py"}, tool_use_id=f"write_{index}")
        self.assertEqual(self.gate_state()["phase"], "freeze")
        response = self.gate("PreToolUse", tool_name="Write", tool_input={"file_path": "solver.py"})
        self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_agent_push_and_harness_dispatch_are_blocked(self):
        self.boundaries()
        for name, args in (("Agent", {}), ("Task", {}), ("Subagent", {}),
                           ("Bash", {"command": "git push origin main"}),
                           ("PowerShell", {"command": "python -B cae_harness.py task --task work"})):
            with self.subTest(name=name):
                result = self.gate("PreToolUse", tool_name=name, tool_input=args)["hookSpecificOutput"]
                self.assertEqual(result["permissionDecision"], "deny")
        self.assertEqual(self.gate_state()["drainActionsRemaining"], handoff_gate.CODEX_DRAIN_ACTIONS)

    def test_readonly_local_git_and_handoff_document_are_exempt(self):
        self.boundaries()
        self.gate("SessionStart", source="compact")
        for name, args in (("Read", {}), ("AskUserQuestion", {}), ("TodoWrite", {}),
                           ("Bash", {"command": "git status --short"}), ("Bash", {"command": "git add . && git commit -m unit"}),
                           ("Write", {"file_path": "HANDOFF.md"}), ("Bash", {"command": "echo progress > handoff.txt"})):
            self.assertEqual(self.gate("PreToolUse", tool_name=name, tool_input=args), {})
        self.assertEqual(self.gate_state()["drainActionsRemaining"], handoff_gate.CODEX_DRAIN_ACTIONS)
        (self.project / "HANDOFF.md").write_text(HANDOFF_TEXT, encoding="utf-8", newline="\n")
        self.assertEqual(self.gate("Stop"), {})
        self.assertTrue(self.gate_state()["handoffRecorded"])

    def test_handoff_path_escape_code_suffix_and_mixed_shell_not_exempt(self):
        for name, args in (("Write", {"file_path": "../handoff.md"}), ("Edit", {"file_path": "resume_solver.py"}),
                           ("Bash", {"command": "echo ready > handoff.md; python solver.py"})):
            self.assertEqual(handoff_gate._claude_tool_kind(self.tool(name, **args))[0], "mutating_other")

    def test_user_override_grace_and_quoted_text_do_not_conflict(self):
        self.boundaries()
        self.gate("SessionStart", source="compact")
        self.gate("UserPromptSubmit", prompt="文档引用：暂缓交接", turn_id="quoted_turn")
        self.assertEqual(self.gate_state()["phase"], "drain")
        response = self.gate("UserPromptSubmit", prompt="暂缓交接", turn_id="override_turn")
        self.assertIn("additionalContext", response["hookSpecificOutput"])
        self.assertEqual(self.gate_state()["phase"], "overridden")
        self.assertEqual(self.gate("PreToolUse", tool_name="Agent"), {})
        for index in range(3):
            _append_jsonl(self.transcript, {"type": "system", "subtype": "compact_boundary", "uuid": f"grace_{index}"})
        self.gate("PostCompact")
        self.assertEqual(self.gate_state()["phase"], "drain")

    def test_stop_requests_correction_once_and_never_loops(self):
        self.boundaries()
        self.assertEqual(self.gate("Stop", last_assistant_message="尚未交接")["decision"], "block")
        self.assertTrue(self.gate_state()["stopCorrectionRequested"])
        result = self.gate("Stop", last_assistant_message="尚未交接")
        self.assertNotIn("decision", result)
        self.assertNotIn("continue", result)
        self.assertEqual(self.gate_state()["phase"], "freeze")
        result = self.gate("Stop", last_assistant_message="尚未交接", stop_hook_active=True)
        self.assertNotIn("decision", result)

    def test_valid_reply_freezes_and_tool_delivery_is_idempotent(self):
        self.boundaries()
        self.gate("PreToolUse", tool_name="Bash", tool_input={"command": "python solver.py"}, tool_use_id="run_alpha")
        before = self.gate_state()["drainActionsRemaining"]
        self.gate("PreToolUse", tool_name="Bash", tool_input={"command": "python solver.py"}, tool_use_id="run_alpha")
        self.assertEqual(self.gate_state()["drainActionsRemaining"], before)
        self.assertEqual(self.gate("Stop", last_assistant_message=HANDOFF_TEXT), {})
        self.assertEqual(self.gate_state()["phase"], "freeze")

    def test_cli_postcompact_uses_standard_output_and_unknown_events_skip(self):
        self.boundaries()
        proc = self.cli("handoff_gate.py", {**self.payload, "hook_event_name": "PostCompact"},
                        "--state-dir", self.state / "handoff", "--mode", "advisory", "--compact-limit", 0)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(set(json.loads(proc.stdout)), {"systemMessage"})
        self.assertEqual(self.gate("UnrecognizedEvent"), {})


class TestClaudeRouter(OfflineSandbox):
    def handler(self, data=None):
        handler = object.__new__(router.Handler)
        cfg = copy.deepcopy(router.DEFAULTS)
        cfg.update(ccswitch_db=str(self.root / "cards.db"), log=str(self.logs / "router.log"),
                   default_key_from_card="Kimi", kimi_effort_map={"xhigh": "max"})
        handler.server = SimpleNamespace(cfg=cfg)
        handler.path = "/v1/messages?stream=true"
        handler.command = "POST"
        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Authorization"] = "Bearer incoming_unit"
        handler.headers["x-api-key"] = "incoming_unit"
        body = json.dumps(data or {}).encode()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.send_error = mock.Mock()
        return handler

    def upstream(self, chunks=None):
        response = mock.Mock(status=200, reason="OK")
        response.getheaders.return_value = [("Content-Type", "text/event-stream"), ("Transfer-Encoding", "chunked"),
                                            ("Connection", "X-Private"), ("X-Private", "strip"), ("X-Trace", "keep")]
        response.read1.side_effect = chunks or [b"data: first\n\n", b"data: second\n\n", b""]
        connection = mock.Mock()
        connection.getresponse.return_value = response
        return connection

    def test_default_config_path_and_loading_are_isolated(self):
        with mock.patch.object(router, "HERE", str(self.root)):
            platform_path = self.root / ("router.windows.json" if os.name == "nt" else "router.wsl.json")
            platform_path.write_bytes(b'\xef\xbb\xbf{"port":8250}')
            self.assertEqual(Path(router.default_config_path()), platform_path)
            primary = self.root / "router.json"
            primary.write_bytes(b'{"port":8251}')
            self.assertEqual(Path(router.default_config_path()), primary)
            cfg = router.load_config(str(platform_path))
            self.assertEqual(cfg["port"], 8250)
            cfg["routes"].clear()
            self.assertTrue(router.DEFAULTS["routes"])
            primary.write_bytes(b'{"follow_windows_proxy":"yes"}')
            with self.assertRaises(ValueError):
                router.load_config(str(primary))

    def test_windows_manual_proxy_scheme_selection_and_pac(self):
        value = {"enabled": True, "server": "http=127.0.0.1:8080;https=127.0.0.1:8443", "override": "", "pac": ""}
        with mock.patch.object(router, "_windows_proxy_settings", return_value=value), \
                mock.patch.object(router, "_probe_proxy_port") as probe:
            self.assertEqual(router.windows_proxy_for("https://example.org").port, 8443)
            self.assertEqual(router.windows_proxy_for("http://example.org").port, 8080)
            probe.assert_not_called()
            value["pac"] = "https://example.org/proxy.pac"
            self.assertIsNone(router.windows_proxy_for("https://example.org"))

    def test_disabled_proxy_requires_loopback_fake_ip_and_live_port(self):
        value = {"enabled": False, "server": "127.0.0.1:8080", "override": "", "pac": ""}
        with mock.patch.object(router, "_windows_proxy_settings", return_value=value), \
                mock.patch.object(router, "_probe_proxy_port", return_value=True) as probe:
            self.assertEqual(router.windows_proxy_for("https://198.18.0.14").hostname, "127.0.0.1")
            probe.assert_called_once_with("127.0.0.1", 8080)
            self.assertIsNone(router.windows_proxy_for("https://192.0.2.10"))
            probe.return_value = False
            self.assertIsNone(router.windows_proxy_for("https://198.18.0.14"))
            value["server"] = "192.0.2.10:8080"
            probe.return_value = True
            self.assertIsNone(router.windows_proxy_for("https://198.18.0.14"))

    def test_proxy_bypass_local_wildcards_and_ipv6(self):
        for host in ("printer", "solver.local", "::1", "2001:db8::1"):
            self.assertTrue(router.proxy_bypassed(host, "<local>;*.local;[::1];[2001:db8::1]"))
        self.assertFalse(router.proxy_bypassed("example.org", "<local>;*.local"))
        with mock.patch.object(router, "_windows_proxy_settings", return_value={
            "enabled": True, "server": "[::1]:8080", "override": "*.local", "pac": "",
        }):
            self.assertIsNone(router.windows_proxy_for("https://solver.local"))
            self.assertEqual(router.windows_proxy_for("https://example.org").hostname, "::1")

    def test_fake_ip_dns_and_port_probe_never_contact_external_network(self):
        with mock.patch.object(router.socket, "getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.14", 0)),
        ]):
            self.assertTrue(router._resolves_to_fake_ip("unit.example"))
        with mock.patch.object(router.socket, "getaddrinfo", side_effect=OSError):
            self.assertFalse(router._resolves_to_fake_ip("unit.example"))
        with mock.patch.object(router.socket, "create_connection") as connect:
            self.assertTrue(router._probe_proxy_port("127.0.0.1", 8080))
            connect.return_value.close.assert_called_once()
        with mock.patch.object(router.socket, "create_connection", side_effect=OSError):
            self.assertFalse(router._probe_proxy_port("127.0.0.1", 8080))

    def test_proxy_registry_lookup_and_five_second_cache(self):
        with mock.patch.object(router.shutil, "which", return_value="powershell.exe"), \
                mock.patch.object(router.subprocess, "run", return_value=SimpleNamespace(stdout=b'{"enabled":true,"server":"127.0.0.1:8080"}')):
            self.assertTrue(router._read_windows_proxy_settings()["enabled"])
        clock = [0.0]
        with mock.patch.object(router.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(router, "_read_windows_proxy_settings", return_value={"enabled": False}) as read:
            router._windows_proxy_settings()
            clock[0] = 4.0
            router._windows_proxy_settings()
            self.assertEqual(read.call_count, 1)
            clock[0] = 6.0
            router._windows_proxy_settings()
            self.assertEqual(read.call_count, 2)

    def test_invalid_proxy_endpoints_and_cached_errors(self):
        for server in ("socks5://127.0.0.1:1080", "http://alice:token@127.0.0.1:8080", "127.0.0.1:0", "http://127.0.0.1:8080/path"):
            with self.subTest(server=server), mock.patch.object(router, "_windows_proxy_settings", return_value={
                "enabled": True, "server": server, "override": "", "pac": "",
            }):
                with self.assertRaises(RuntimeError):
                    router.windows_proxy_for("https://example.org")
        with mock.patch.object(router, "_read_windows_proxy_settings", side_effect=RuntimeError("lookup failed")) as read:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "lookup failed"):
                    router._windows_proxy_settings()
            self.assertEqual(read.call_count, 1)

    def test_end_to_end_headers_remove_connection_nominated_fields(self):
        result = router.end_to_end_headers([("Connection", "X-Remove, Keep-Alive"), ("X-Remove", "secret"),
                                            ("Content-Length", "42"), ("Transfer-Encoding", "chunked"), ("X-Trace", "keep")])
        self.assertEqual(result, [("X-Trace", "keep")])

    def test_card_key_readonly_uri_sixty_second_cache_and_expiry(self):
        db = self.database()
        admin.prepare_cards(db, 8240)
        self.replace_card(db, "Kimi", {"env": {"ANTHROPIC_AUTH_TOKEN": "tok_kimi_unit_test"}})
        cfg = {"ccswitch_db": str(db)}
        clock = [0.0]
        connect = sqlite3.connect
        with mock.patch.object(router.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(router.sqlite3, "connect", wraps=connect) as opened:
            self.assertEqual(router.card_key(cfg, "Kimi"), "tok_kimi_unit_test")
            self.assertIn("?mode=ro", opened.call_args.args[0])
            self.assertTrue(opened.call_args.kwargs["uri"])
            clock[0] = 59.0
            self.assertEqual(router.card_key(cfg, "Kimi"), "tok_kimi_unit_test")
            self.assertEqual(opened.call_count, 1)
            clock[0] = 61.0
            router.card_key(cfg, "Kimi")
            self.assertEqual(opened.call_count, 2)

    def test_card_key_rejects_placeholders_invalid_json_and_missing_cards(self):
        db = self.database()
        cfg = {"ccswitch_db": str(db)}
        for token in ("PASTE_KIMI_API_KEY_HERE", "via-router", "", "line\nbreak", "carriage\rreturn", 42):
            self.replace_card(db, "Official", {"env": {"ANTHROPIC_AUTH_TOKEN": token}})
            with self.subTest(token=token), self.assertRaises(RuntimeError):
                router.card_key(cfg, "Official")
        with self.assertRaisesRegex(RuntimeError, "not found"):
            router.card_key(cfg, "missing")
        with closing(sqlite3.connect(db)) as con:
            con.execute("UPDATE providers SET settings_config='not-json' WHERE name='Official'")
            con.commit()
        with self.assertRaisesRegex(RuntimeError, "invalid settings"):
            router.card_key(cfg, "Official")

    def test_health_endpoint_uses_no_upstream(self):
        for port in (None, 8240, 8241):
            with self.subTest(port=port):
                handler = self.handler()
                if port is not None:
                    handler.server.cfg["port"] = port
                handler.path = "/__router/health"
                with mock.patch.object(router.http.client, "HTTPSConnection") as connection:
                    handler.do_GET()
                    connection.assert_not_called()
                handler.send_response.assert_called_once_with(200)
                self.assertEqual(json.loads(handler.wfile.getvalue()), {"ok": True, "port": handler.server.cfg["port"]})

    def test_kimi_default_route_maps_effort_replaces_auth_and_relays_chunks(self):
        handler = self.handler({"model": "k3.1-pro[1m]", "output_config": {"effort": "xhigh"}, "stream": True})
        connection = self.upstream()
        with mock.patch.object(router.http.client, "HTTPSConnection", return_value=connection) as opened, \
                mock.patch.object(router, "card_key", return_value="tok_kimi_unit_test") as key:
            handler.do_POST()
        opened.assert_called_once_with("api.kimi.com", timeout=900)
        key.assert_called_once_with(handler.server.cfg, "Kimi")
        request = connection.request.call_args
        self.assertEqual(request.args, ("POST", "/coding/v1/messages?stream=true"))
        self.assertEqual(json.loads(request.kwargs["body"])["output_config"]["effort"], "max")
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer tok_kimi_unit_test")
        self.assertEqual(handler.wfile.getvalue(), b"data: first\n\ndata: second\n\n")
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertNotIn("Transfer-Encoding", headers)
        self.assertNotIn("X-Private", headers)
        self.assertEqual(headers["X-Trace"], "keep")
        connection.close.assert_called_once()
        log = (self.logs / "router.log").read_text(encoding="utf-8")
        self.assertNotIn("tok_kimi_unit_test", log)
        self.assertEqual(json.loads(log)["bytes"], len(handler.wfile.getvalue()))

    def test_deepseek_prefix_strips_context_suffix_and_forces_max(self):
        handler = self.handler({"model": "deepseek-flash[1M]", "output_config": {"effort": "high", "format": "json"}})
        connection = self.upstream()
        with mock.patch.object(router.http.client, "HTTPSConnection", return_value=connection) as opened, \
                mock.patch.object(router, "card_key", return_value="tok_ds_unit_test") as key:
            handler.do_POST()
        opened.assert_called_once_with("api.deepseek.com", timeout=900)
        key.assert_called_once_with(handler.server.cfg, "DeepSeek")
        data = json.loads(connection.request.call_args.kwargs["body"])
        self.assertEqual(data["model"], "deepseek-flash")
        self.assertEqual(data["output_config"], {"effort": "max", "format": "json"})

    def test_force_effort_preserves_low_tiers_and_defaults_to_max(self):
        for effort in (None, "low", "LoW", "medium", "MEDIUM", "high", "XHIGH", "max", "unknown"):
            with self.subTest(effort=effort):
                output_config = {"format": "json"}
                if effort is not None:
                    output_config["effort"] = effort
                handler = self.handler({"model": "deepseek-flash", "output_config": output_config})
                connection = self.upstream()
                with mock.patch.object(router.http.client, "HTTPSConnection", return_value=connection), \
                        mock.patch.object(router, "card_key", return_value="tok_fixture"):
                    handler.do_POST()
                config = json.loads(connection.request.call_args.kwargs["body"])["output_config"]
                expected = "max" if effort is None or effort.lower() in ("high", "xhigh", "max") else effort
                self.assertEqual(config, {"format": "json", "effort": expected})

    def test_busy_router_rejects_before_any_body_read(self):
        for chunked in (False, True):
            with self.subTest(chunked=chunked):
                handler = self.handler({"model": "deepseek-flash"})
                if chunked:
                    handler.headers["Transfer-Encoding"] = "chunked"
                handler.rfile = mock.Mock()
                handler.server.inflight_sem = mock.Mock()
                handler.server.inflight_sem.acquire.return_value = False
                handler.do_POST()
                handler.send_error.assert_called_once_with(503, "router max_inflight reached")
                handler.rfile.read.assert_not_called()
                handler.rfile.readline.assert_not_called()
                handler.server.inflight_sem.acquire.assert_called_once_with(blocking=False)
                handler.server.inflight_sem.release.assert_not_called()

    def test_post_slot_released_once_on_body_and_forward_paths(self):
        for case in ("success", "oversize", "invalid_length", "read_error", "chunked", "bad_chunk", "forward_error"):
            with self.subTest(case=case):
                handler = self.handler({"model": "deepseek-flash"})
                handler.server.inflight_sem = mock.Mock()
                handler.server.inflight_sem.acquire.return_value = True
                handler._forward_locked = mock.Mock()
                if case == "oversize":
                    handler.server.cfg["max_body_bytes"] = 1
                elif case == "invalid_length":
                    handler.headers.replace_header("Content-Length", "invalid")
                elif case == "read_error":
                    handler.rfile = mock.Mock()
                    handler.rfile.read.side_effect = OSError("fixture")
                elif case in ("chunked", "bad_chunk"):
                    del handler.headers["Content-Length"]
                    handler.headers["Transfer-Encoding"] = "chunked"
                    handler.rfile = io.BytesIO(b"2\r\n{}\r\n0\r\n\r\n" if case == "chunked" else b"invalid\r\n")
                elif case == "forward_error":
                    handler._forward_locked.side_effect = RuntimeError("fixture")
                if case == "forward_error":
                    with self.assertRaisesRegex(RuntimeError, "fixture"):
                        handler.do_POST()
                else:
                    handler.do_POST()
                handler.server.inflight_sem.acquire.assert_called_once_with(blocking=False)
                handler.server.inflight_sem.release.assert_called_once_with()
                if case in ("success", "chunked", "forward_error"):
                    handler._forward_locked.assert_called_once()
                else:
                    handler._forward_locked.assert_not_called()

    def test_platform_router_port_comes_from_profile(self):
        tunables = {"port": 18240, "wsl_port": 18241}
        with mock.patch.dict(router.DEFAULTS), mock.patch.object(router, "_load_fleet_router_tunables", return_value=tunables):
            for platform, expected in (("nt", 18240), ("posix", 18241)):
                with mock.patch.object(router.os, "name", platform):
                    router._apply_fleet_router_defaults()
                self.assertEqual(router.DEFAULTS["port"], expected)

    def test_proxy_tunnel_and_lookup_failure_direct_fallback(self):
        handler = self.handler({"model": "kimi"})
        handler.server.cfg["follow_windows_proxy"] = True
        connection = self.upstream()
        with mock.patch.object(router.http.client, "HTTPSConnection", return_value=connection) as opened, \
                mock.patch.object(router, "card_key", return_value="tok_kimi_unit_test"), \
                mock.patch.object(router, "windows_proxy_for", return_value=router.urlsplit("http://127.0.0.1:8080")):
            handler.do_POST()
        opened.assert_called_once_with("127.0.0.1", 8080, timeout=900)
        connection.set_tunnel.assert_called_once_with("api.kimi.com", 443)
        handler = self.handler({"model": "kimi"})
        handler.server.cfg["follow_windows_proxy"] = True
        with mock.patch.object(router.http.client, "HTTPSConnection", return_value=self.upstream()) as opened, \
                mock.patch.object(router, "card_key", return_value="tok_kimi_unit_test"), \
                mock.patch.object(router, "windows_proxy_for", side_effect=RuntimeError("lookup failed")):
            handler.do_POST()
        opened.assert_called_once_with("api.kimi.com", timeout=900)

    def test_bad_model_length_missing_key_and_stream_failure(self):
        handler = self.handler({"model": 123})
        handler.do_POST()
        self.assertEqual(handler.send_error.call_args.args[0], 400)
        handler = self.handler()
        handler.headers.replace_header("Content-Length", "-1")
        handler.do_POST()
        self.assertEqual(handler.send_error.call_args.args[0], 400)
        handler = self.handler({"model": "kimi"})
        with mock.patch.object(router, "card_key", side_effect=RuntimeError("no usable key")):
            handler.do_POST()
        self.assertEqual(handler.send_error.call_args.args[0], 502)
        handler = self.handler({"model": "kimi"})
        connection = self.upstream([b"data: partial\n\n", OSError("upstream closed")])
        with mock.patch.object(router.http.client, "HTTPSConnection", return_value=connection), \
                mock.patch.object(router, "card_key", return_value="tok_kimi_unit_test"):
            handler.do_POST()
        handler.send_error.assert_not_called()
        self.assertEqual(handler.wfile.getvalue(), b"data: partial\n\n")
        connection.close.assert_called_once()

    def test_chunked_request_401_cache_invalidation_and_ascii_error_reason(self):
        # 1) Transfer-Encoding: chunked 正常解码转发与超上限 413 拦截
        payload_bytes = json.dumps({"model": "deepseek-chat", "messages": []}).encode("utf-8")
        chunked_stream = f"{len(payload_bytes):x}\r\n".encode("ascii") + payload_bytes + b"\r\n0\r\n\r\n"
        handler = self.handler()
        del handler.headers["Content-Length"]
        handler.headers["Transfer-Encoding"] = "chunked"
        handler.rfile = io.BytesIO(chunked_stream)
        connection = self.upstream()
        with mock.patch.object(router.http.client, "HTTPSConnection", return_value=connection), \
                mock.patch.object(router, "card_key", return_value="tok_ds_unit_test"):
            handler.do_POST()
        sent_body = json.loads(connection.request.call_args.kwargs["body"])
        self.assertEqual(sent_body["model"], "deepseek-chat")

        too_large_handler = self.handler()
        del too_large_handler.headers["Content-Length"]
        too_large_handler.headers["Transfer-Encoding"] = "chunked"
        too_large_handler.server.cfg["max_body_bytes"] = 8
        too_large_handler.rfile = io.BytesIO(chunked_stream)
        too_large_handler.do_POST()
        self.assertEqual(too_large_handler.send_error.call_args.args[0], 413)

        # 2) 上游返回 401 时自动失效密钥缓存，下次请求立即读取轮换后的新密钥
        db = self.database()
        admin.prepare_cards(db, 8240)
        self.replace_card(db, "Kimi", {"env": {"ANTHROPIC_AUTH_TOKEN": "   ", "ANTHROPIC_API_KEY": "tok_old_key_01"}})
        router.invalidate_card_key_cache({"ccswitch_db": str(db)}, "Kimi")
        h401 = self.handler({"model": "kimi-k2"})
        h401.server.cfg["ccswitch_db"] = str(db)
        unauth_resp = mock.Mock(status=401, reason="Unauthorized")
        unauth_resp.getheaders.return_value = [("Content-Type", "application/json")]
        unauth_resp.read1.side_effect = [b'{"error":"invalid_key"}', b""]
        unauth_conn = mock.Mock()
        unauth_conn.getresponse.return_value = unauth_resp
        with mock.patch.object(router.http.client, "HTTPSConnection", return_value=unauth_conn):
            h401.do_POST()
        self.assertEqual(unauth_conn.request.call_args.kwargs["headers"]["Authorization"], "Bearer tok_old_key_01")
        self.replace_card(db, "Kimi", {"env": {"ANTHROPIC_AUTH_TOKEN": "tok_rotated_key_02"}})
        self.assertEqual(router.card_key({"ccswitch_db": str(db)}, "Kimi"), "tok_rotated_key_02")

        # 3) 非 ASCII 卡片名异常信息转换为纯 ASCII reason，确保 BaseHTTPRequestHandler Latin-1 编码安全
        h_cn = self.handler({"model": "custom-cn"})
        h_cn.server.cfg["default_key_from_card"] = "中文自定义卡"
        h_cn.server.cfg["ccswitch_db"] = str(db)
        h_cn.do_POST()
        err_code, err_reason = h_cn.send_error.call_args.args[:2]
        self.assertEqual(err_code, 502)
        err_reason.encode("latin-1")


class TestCCSwitchAdmin(OfflineSandbox):
    def test_status_quota_types_and_caps_never_disclose_amounts(self):
        db = self.database()
        with closing(sqlite3.connect(db)) as con:
            con.execute("UPDATE providers SET meta=?, limit_daily_usd=?, limit_monthly_usd=? WHERE app_type='claude'",
                        (json.dumps({"usage_script": {"templateType": "token_plan", "secret": "tok_private"}}), 12.34, 56.78))
            con.execute("UPDATE providers SET meta=? WHERE app_type='codex'",
                        (json.dumps({"usage_script": {"templateType": "balance"}}),))
            con.commit()
        report = admin.inspect_status(db)
        cards = {card["app_type"]: card for card in report["cards"]}
        self.assertEqual(cards["claude"]["quota_scheme"], "token_plan")
        self.assertEqual(cards["codex"]["quota_scheme"], "balance")
        self.assertEqual(cards["claude"]["quota_caps"], ["daily_cap", "monthly_cap"])
        self.assertEqual(cards["codex"]["quota_caps"], [])
        serialized = json.dumps(report)
        for secret in ("12.34", "56.78", "tok_private"):
            self.assertNotIn(secret, serialized)
        with closing(sqlite3.connect(db)) as con:
            con.execute("UPDATE providers SET meta='[]'")
            con.commit()
        self.assertTrue(all(card["quota_scheme"] is None for card in admin.inspect_status(db)["cards"]))

    def test_switch_dead_loopback_warns_or_blocks_before_writes(self):
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        for host in ("127.0.0.1", "localhost", "[::1]"):
            with self.subTest(host=host):
                self.replace_card(db, "Kimi", {"env": {"ANTHROPIC_BASE_URL": f"http://{host}:8240"}})
                original = (db.read_bytes(), settings.read_bytes())
                backups = set(self.root.rglob("*.bak-before-live-switch-*"))
                opener = mock.Mock()
                opener.open.side_effect = OSError("fixture dead router")
                with mock.patch.object(admin.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaisesRegex(RuntimeError, "Local router"):
                        admin.switch_card(db, settings, "Kimi", require_router_alive=True)
                    with self.assertRaisesRegex(RuntimeError, "Local router"):
                        admin.main(["--db", str(db), "--settings", str(settings),
                                    "switch-card", "Kimi", "--require-router-alive"])
                    self.assertEqual((db.read_bytes(), settings.read_bytes()), original)
                    self.assertEqual(set(self.root.rglob("*.bak-before-live-switch-*")), backups)
                    result = admin.switch_card(db, settings, "Kimi")
                self.assertTrue(any("/__router/health" in warning for warning in result["warnings"]))
                opener.open.assert_called_with(f"http://{host}:8240/__router/health", timeout=2.0)

    def test_switch_live_router_and_direct_card_with_required_health(self):
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        self.replace_card(db, "Kimi", {"env": {"ANTHROPIC_BASE_URL": "http://localhost:8240"}})
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok": true}'
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(admin.urllib.request, "build_opener", return_value=opener):
            result = admin.switch_card(db, settings, "Kimi", require_router_alive=True)
        self.assertFalse(result["warnings"])
        with mock.patch.object(admin.urllib.request, "build_opener") as probe:
            admin.switch_card(db, settings, "DeepSeek", require_router_alive=True)
        probe.assert_not_called()

    def test_card_defaults_are_dynamic_and_incomplete_profile_is_rejected(self):
        profile = self.root / "profile.json"
        rules = {"router_primary_model": "fixture-primary", "router_subagent_model": "fixture-subagent",
                 "max_context_tokens": 123456, "auto_compact_window": 120000, "router_card_name": "fixture"}
        user_words.atomic_write_json(profile, {"client_catalog_rules": {"claude": rules}})
        with mock.patch.dict(os.environ, {"CAE_FLEET_PROFILE": str(profile)}):
            cards = admin._build_card_templates(8240)
            env = cards["fixture"][0]()
            self.assertEqual(env["ANTHROPIC_MODEL"], "fixture-primary")
            self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "fixture-subagent")
            self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "123456")
            self.assertEqual(env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "120000")
            user_words.atomic_write_json(profile, {"client_catalog_rules": {"claude": {}}})
            with self.assertRaisesRegex(ValueError, "missing Claude defaults"):
                admin._claude_profile_defaults()

    def test_schema_supported_newer_warns_and_older_rejected(self):
        db = self.database()
        for version, warns in ((26, False), (admin.TESTED_MAX_SCHEMA, False), (admin.TESTED_MAX_SCHEMA + 1, True)):
            with closing(sqlite3.connect(db)) as con:
                con.execute(f"PRAGMA user_version={version}")
                con.commit()
            report = admin.inspect_status(db)
            self.assertEqual(report["schema_version"], version)
            self.assertEqual(bool(report["warnings"]), warns)
        with closing(sqlite3.connect(db)) as con:
            con.execute("PRAGMA user_version=22")
            con.commit()
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            admin.prepare_cards(db, 8240)

    def test_prepare_cards_insert_update_preserve_real_keys_and_effort_control(self):
        db = self.database()
        first = admin.prepare_cards(db, 8240)
        self.assertTrue(Path(first["backup"]).is_file())
        self.assertEqual([card["action"] for card in first["cards"]], ["insert"] * 3)
        for name, key in (("Kimi", "tok_kimi_unit_test"), ("DeepSeek", "tok_ds_unit_test")):
            self.replace_card(db, name, {"env": {"ANTHROPIC_API_KEY": key, "CLAUDE_CODE_EFFORT_LEVEL": "low"}})
        second = admin.prepare_cards(db, 8241)
        self.assertEqual([card["action"] for card in second["cards"]], ["update"] * 3)
        cards = self.cards(db)
        self.assertEqual(cards["Kimi"]["env"]["ANTHROPIC_AUTH_TOKEN"], "tok_kimi_unit_test")
        self.assertEqual(cards["DeepSeek"]["env"]["ANTHROPIC_AUTH_TOKEN"], "tok_ds_unit_test")
        self.assertEqual(cards["K3.1主+DS子代理"]["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8241")
        for name in ("Kimi", "DeepSeek", "K3.1主+DS子代理"):
            self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", cards[name]["env"])
        self.assertNotIn("tok_kimi_unit_test", json.dumps(second))

    def test_prepare_cards_preserves_valid_meta_and_normalizes_invalid_meta(self):
        db = self.database()
        admin.prepare_cards(db, 8240)
        metadata = '{ "usage_script": {"templateType":"fixture"}, "dailyLimit": 8, "monthlyLimit": 40 }'
        for value in (metadata, "", "[]", "broken", None):
            with self.subTest(meta=value), closing(sqlite3.connect(db)) as con:
                con.execute("UPDATE providers SET meta=? WHERE name='Kimi'", (value,))
                con.commit()
                admin.prepare_cards(db, 8241)
                saved = con.execute("SELECT meta FROM providers WHERE name='Kimi'").fetchone()[0]
                self.assertEqual(saved, metadata if value == metadata else "{}")

    def test_prepare_dry_run_and_duplicate_failure_do_not_modify_database(self):
        db = self.database()
        original = db.read_bytes()
        result = admin.prepare_cards(db, 8240, dry_run=True)
        self.assertIsNone(result["backup"])
        self.assertEqual(db.read_bytes(), original)
        admin.prepare_cards(db, 8240)
        with closing(sqlite3.connect(db)) as con:
            con.execute("INSERT INTO providers (id,app_type,name,settings_config) VALUES ('duplicate_kimi','claude','Kimi','{}')")
            con.commit()
        before = self.cards(db)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            admin.prepare_cards(db, 8240)
        self.assertEqual(self.cards(db), before)

    def test_projection_preserves_hooks_effort_and_clears_floor_exclusive_residue(self):
        original = {
            "hooks": {"SessionStart": [{"hooks": []}]}, "effortLevel": "xhigh", "model": "obsolete",
            "apiKeyHelper": "old-helper", "env": {"ANTHROPIC_BASE_URL": "https://example.org", "AWS_REGION": "unit",
                "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1", "CLAUDE_CODE_DISABLE_THINKING": "1",
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "131072", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": 372000,
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "983616", "PERSONAL_SETTING": "keep"},
        }
        previous = {"env": {"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1", "CLAUDE_CODE_DISABLE_THINKING": "0"}}
        target = {"env": {"ANTHROPIC_MODEL": "deepseek-flash", "ANTHROPIC_AUTH_TOKEN": "tok_ds_unit_test"}}
        snapshot = copy.deepcopy(original)
        result = admin.project_claude_settings(original, previous, target)
        self.assertEqual(original, snapshot)
        self.assertEqual(result["hooks"], original["hooks"])
        self.assertEqual(result["effortLevel"], "xhigh")
        for key in ("model", "apiKeyHelper"):
            self.assertNotIn(key, result)
        for key in ("AWS_REGION", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", *admin.RESIDUE_ENV):
            self.assertNotIn(key, result["env"])
        self.assertEqual(result["env"]["CLAUDE_CODE_DISABLE_THINKING"], "1")
        self.assertEqual(result["env"]["PERSONAL_SETTING"], "keep")

    def test_switch_card_preserves_user_settings_and_other_app_current(self):
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        result = admin.switch_card(db, settings, "DeepSeek")
        self.assertEqual(result["previous"], "Official")
        self.assertTrue(Path(result["settings_backup"]).is_file())
        updated = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(updated["hooks"], {"Stop": [{"hooks": []}]})
        self.assertEqual(updated["effortLevel"], "xhigh")
        self.assertEqual(updated["env"]["PERSONAL_SETTING"], "keep")
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", updated["env"])
        with closing(sqlite3.connect(db)) as con:
            current = con.execute("SELECT app_type,name FROM providers WHERE is_current=1 ORDER BY app_type").fetchall()
        self.assertEqual(current, [("claude", "DeepSeek"), ("codex", "Other")])

    def test_live_proxy_attached_and_pending_refuse_switch_without_writes(self):
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        for live in ({"mode": "proxy"}, {"attached": True}, {"pending": {"card": "Kimi"}}):
            user_words.atomic_write_json(self.root / "live-state.json", {"apps": {"claude": live}})
            before = db.read_bytes(), settings.read_bytes()
            with self.subTest(live=live), self.assertRaisesRegex(RuntimeError, "direct mode"):
                admin.switch_card(db, settings, "Kimi")
            self.assertEqual((db.read_bytes(), settings.read_bytes()), before)

    def test_settings_write_failure_rolls_back_database(self):
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        original = settings.read_bytes()
        with mock.patch.object(admin, "atomic_write_json", side_effect=OSError("write failed")), \
                self.assertRaisesRegex(OSError, "write failed"):
            admin.switch_card(db, settings, "Kimi")
        self.assertEqual(settings.read_bytes(), original)
        self.assertEqual(admin.inspect_status(db)["current_claude_card"]["name"], "Official")

    def test_commit_failure_after_publication_restores_settings_and_database(self):
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        original = settings.read_bytes()
        real_connect = sqlite3.connect

        class FailCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("commit failed")

        def connect(path, *args, **kwargs):
            if isinstance(path, str) and "?mode=rw" in path:
                kwargs["factory"] = FailCommit
            return real_connect(path, *args, **kwargs)

        with mock.patch.object(admin.sqlite3, "connect", side_effect=connect), \
                self.assertRaisesRegex(sqlite3.OperationalError, "commit failed"):
            admin.switch_card(db, settings, "Kimi")
        self.assertEqual(settings.read_bytes(), original)
        self.assertEqual(admin.inspect_status(db)["current_claude_card"]["name"], "Official")

    def test_switch_dry_run_preserves_database_and_settings(self):
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        before = db.read_bytes(), settings.read_bytes()
        result = admin.switch_card(db, settings, "Kimi", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual((db.read_bytes(), settings.read_bytes()), before)

    def test_disable_prompts_backs_up_original_and_only_selected_app(self):
        db = self.database()
        with closing(sqlite3.connect(db)) as con:
            con.executemany("INSERT INTO prompts VALUES (?,?,?,?,?)", [
                ("prompt_a", "claude", "Old rules", 1, "old claude content"),
                ("prompt_b", "codex", "Other rules", 1, "other content"),
                ("prompt_c", "claude", "Disabled rules", 0, "keep disabled"),
            ])
            con.commit()
        result = admin.disable_prompts(db, app_types=["claude"])
        self.assertEqual([row["id"] for row in result["disabled"]], ["prompt_a"])
        with closing(sqlite3.connect(result["backup"])) as con:
            self.assertEqual(con.execute("SELECT enabled FROM prompts WHERE id='prompt_a'").fetchone(), (1,))
        with closing(sqlite3.connect(db)) as con:
            self.assertEqual(con.execute("SELECT enabled,content FROM prompts WHERE id='prompt_a'").fetchone(), (0, "old claude content"))
            self.assertEqual(con.execute("SELECT enabled FROM prompts WHERE id='prompt_b'").fetchone(), (1,))

    def test_disable_prompts_zero_enabled_means_zero_writes_and_no_backup(self):
        db = self.database()
        before = db.read_bytes(), db.stat().st_mtime_ns
        with mock.patch.object(admin, "backup_sqlite_database") as backup:
            result = admin.disable_prompts(db)
            backup.assert_not_called()
        self.assertEqual(result["disabled"], [])
        self.assertIsNone(result["backup"])
        self.assertEqual((db.read_bytes(), db.stat().st_mtime_ns), before)

    def test_verify_alignment_reports_prompts_effort_lock_and_mismatch(self):
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        admin.switch_card(db, settings, "Kimi")
        okay, report = admin.verify_alignment(db, settings)
        self.assertTrue(okay, report["errors"])
        data = json.loads(settings.read_text(encoding="utf-8"))
        data["env"].update(CLAUDE_CODE_EFFORT_LEVEL="low", ANTHROPIC_MODEL="mismatched")
        user_words.atomic_write_json(settings, data)
        with closing(sqlite3.connect(db)) as con:
            con.execute("INSERT INTO prompts VALUES ('prompt_a','claude','Old rules',1,'old content')")
            con.commit()
        okay, report = admin.verify_alignment(db, settings)
        self.assertFalse(okay)
        self.assertEqual(len(report["errors"]), 3)

    def test_admin_cli_prepare_switch_disable_and_verify(self):
        db, settings = self.database(), self.settings()
        script = REPO_ROOT / "template/claude/router/ccswitch_admin.py"
        for command in (["prepare-cards", "--port", "8240"], ["switch-card", "Kimi"], ["disable-prompts"], ["verify"]):
            proc = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", str(script), "--db", str(db), "--settings", str(settings), *command],
                capture_output=True, text=True, encoding="utf-8", cwd=self.project, timeout=20,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertIsInstance(result, dict)
        self.assertTrue(result["ok"])


class TestClaudeInstallerAndUpgradeAudit(OfflineSandbox):
    def setUp(self):
        super().setUp()
        # 独立源码与安装目标互为平级，满足安装器的源码目录重叠检查。
        self.source = self.root / "source"
        self.source.mkdir()
        shutil.copy2(REPO_ROOT / "install.py", self.source / "install.py")
        shutil.copytree(REPO_ROOT / "template", self.source / "template",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        self.plugin = self.root / "target/.gemini/config/plugins/user-guard"
        self.codex = self.root / "target/codex"
        self.default_codex = self.home / ".codex"
        patch = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.default_codex),
                                            "APPDATA": str(self.home / "AppData/Roaming"),
                                            "LOCALAPPDATA": str(self.home / "AppData/Local")})
        patch.start()
        self.addCleanup(patch.stop)

    def install(self, *args, code=0):
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(self.source / "install.py"), *map(str, args),
             "--no-zotero", "--skip-test"], cwd=self.source, capture_output=True,
            text=True, encoding="utf-8", timeout=40,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def all_args(self):
        return ["--app", "all", "--target-dir", self.plugin, "--codex-home", self.codex,
                "--claude-home", self.claude_home, "--with-cc-router"]

    def snapshot(self):
        # 同时核对目录，防止只读探测留下没有缓存文件的空目录。
        return {path.relative_to(self.root).as_posix():
                (path.read_bytes() if path.is_file() else None, path.stat().st_mtime_ns)
                for path in self.root.rglob("*")}

    def prompts(self, db):
        with closing(sqlite3.connect(db)) as con:
            con.executemany("INSERT INTO prompts VALUES (?,?,?,?,?)", [
                ("claude_old", "claude", "Old Claude", 1, "old claude rules"),
                ("codex_old", "codex", "Old Codex", 2, "old codex rules"),
            ])
            con.commit()

    def test_legacy_custom_rules_migrate_and_audit_detects_missing_standard_section(self):
        self.codex.mkdir(parents=True)
        rules = self.codex / "AGENTS.md"
        tail = "## 工具与本机\nR10. 本地编译器。\n- **R11. 本地编辑器。**\n原样保留 R10. 内文。\n"
        template = (self.source / "template/user-guard/rules/AGENTS.md").read_text(encoding="utf-8").rstrip()
        core = template.split("## 工具与运行环境", 1)[0].rstrip()
        rules.write_bytes((core + "\n\n" + tail).encode())
        args = ["--app", "codex", "--codex-home", self.codex]
        report = json.loads(self.install(*args, "--audit-upgrade").stdout)
        self.assertIn("AGENTS.md", report["codex"]["drift_files"])
        self.install(*args)
        expected_tail = tail.replace("\nR10.", "\nL1.").replace("- **R11.", "- **L2.")
        rendered_codex = installer.render_rules_for_client(template, "codex").rstrip()
        self.assertEqual(rules.read_text(encoding="utf-8"), rendered_codex + "\n\n" + expected_tail)
        before = self.snapshot()
        report = json.loads(self.install(*args, "--audit-upgrade").stdout)
        self.assertEqual(report["codex"]["drift_files"], [])
        self.assertTrue(report["codex"]["hooks_aligned"])
        self.assertEqual(self.snapshot(), before)
        self.install(*args)
        self.assertEqual(self.snapshot(), before)

    def test_custom_rule_collision_markdown_variants_and_rules_equal(self):
        template = self.source / "template/user-guard/rules/AGENTS.md"
        target = self.codex / "AGENTS.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        prefixes = ("", "- ", "* ", "+ ", "1. ", "**", "*", "- **", "### ", "  2. **")
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                tail = "## 工具与本机\n" + prefix + "R10. 编译器\n" + prefix + "R11. 编辑器\n"
                target.write_text(template.read_text(encoding="utf-8") + "\n" + tail, encoding="utf-8")
                self.assertFalse(installer._rules_equal(target.read_bytes(), template.read_bytes()))
                installer._sync_global_rules(template, target, "fixture", False)
                text = target.read_text(encoding="utf-8")
                self.assertTrue(text.endswith(tail.replace("R10.", "L1.").replace("R11.", "L2.")))
                self.assertTrue(installer._rules_equal(target.read_bytes(), template.read_bytes()))

    def test_codex_all_hook_scripts_run_and_off_removes_gate(self):
        args = ["--app", "codex", "--codex-home", self.codex]
        self.install(*args)
        names = {"uguard_hook.py", "session_context_loader.py", "status_guard.py", "reply_repeat_guard.py", "handoff_gate.py", "user_words.py"}
        for name in names:
            path = self.codex / "hooks" / name
            self.assertEqual(path.read_bytes(), (HOOKS_DIR / name).read_bytes())
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-I", str(path), "--help"],
                                    cwd=self.project, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads((self.codex / "hooks.json").read_bytes())
        for event in ("PreToolUse", "Stop"):
            handlers = [h for g in doc["hooks"][event] for h in g["hooks"] if "uguard_hook.py" in h["command"]]
            self.assertEqual(len(handlers), 1)
            self.assertEqual(handlers[0]["timeout"], 15)
            self.assertIn(f"--client codex --event {event}", handlers[0]["command"])
            payload = {"session_id": "codex_fixture", "cwd": str(self.project), "hook_event_name": event,
                       "stop_hook_active": True, "tool_name": "Read",
                       "tool_input": {"file_path": str(self.project / "solver.py")}}
            result = subprocess.run(installer._codex_hook_tokens(handlers[0]), input=json.dumps(payload),
                                    cwd=self.project, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertIsInstance(json.loads(result.stdout), dict)
        gate = self.codex / "hooks/handoff_gate.py"
        before = self.snapshot()
        self.install(*args, "--codex-handoff-mode", "off", "--dry-run")
        self.assertEqual(self.snapshot(), before)
        self.install(*args, "--codex-handoff-mode", "off")
        self.assertEqual(gate.read_bytes(), (HOOKS_DIR / "handoff_gate.py").read_bytes())
        self.assertTrue(all((self.codex / "hooks" / name).is_file() for name in names))
        doc = json.loads((self.codex / "hooks.json").read_bytes())
        self.assertNotIn("PostCompact", doc["hooks"])
        self.assertNotIn("UserPromptSubmit", doc["hooks"])
        policy = json.loads((self.codex / "hooks/state/handoff/policy.json").read_bytes())
        self.assertEqual(policy["mode"], "off")
        for event in ("PreToolUse", "Stop"):
            handler = next(h for g in doc["hooks"][event] for h in g["hooks"] if "uguard_hook.py" in h["command"])
            payload = {"session_id": "codex_off_fixture", "cwd": str(self.project), "hook_event_name": event,
                       "stop_hook_active": True, "tool_name": "Read",
                       "tool_input": {"file_path": str(self.project / "solver.py")}}
            result = subprocess.run(installer._codex_hook_tokens(handler), input=json.dumps(payload),
                                    cwd=self.project, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertIsInstance(json.loads(result.stdout), dict)
        report = json.loads(self.install(*args, "--audit-upgrade").stdout)
        self.assertEqual(report["codex"]["drift_files"], [])
        self.assertTrue(report["codex"]["hooks_aligned"])
        gate.write_bytes(b"# stale gate\n")
        report = json.loads(self.install(*args, "--audit-upgrade").stdout)
        self.assertIn("hooks/handoff_gate.py", report["codex"]["drift_files"])
        self.install(*args)
        self.assertEqual(gate.read_bytes(), (HOOKS_DIR / "handoff_gate.py").read_bytes())

    def test_codex_audit_detects_missing_guard_registration_and_script(self):
        args = ["--app", "codex", "--codex-home", self.codex]
        self.install(*args)
        registry = self.codex / "hooks.json"
        original = registry.read_bytes()
        for event, script_name in (("PreToolUse", "status_guard.py"), ("Stop", "reply_repeat_guard.py")):
            with self.subTest(event=event):
                doc = json.loads(original)
                expected_doc = copy.deepcopy(doc)
                for group in doc["hooks"][event]:
                    group["hooks"] = [handler for handler in group["hooks"] if "uguard_hook.py" not in handler["command"]]
                registry.write_bytes((json.dumps(doc) + "\n").encode("utf-8"))
                before = self.snapshot()
                report = json.loads(self.install(*args, "--audit-upgrade").stdout)
                self.assertFalse(report["codex"]["hooks_aligned"])
                self.assertIn("hooks.json", report["codex"]["drift_files"])
                self.assertEqual(self.snapshot(), before)
                self.install(*args)
                self.assertEqual(json.loads(registry.read_bytes()), expected_doc)
                script = self.codex / "hooks" / script_name
                script.unlink()
                before = self.snapshot()
                report = json.loads(self.install(*args, "--audit-upgrade").stdout)
                self.assertIn("hooks/" + script_name, report["codex"]["drift_files"])
                self.assertEqual(self.snapshot(), before)
                self.install(*args)
                self.assertEqual(script.read_bytes(), (HOOKS_DIR / script_name).read_bytes())
                report = json.loads(self.install(*args, "--audit-upgrade").stdout)
                self.assertTrue(report["codex"]["hooks_aligned"])
                self.assertEqual(report["codex"]["drift_files"], [])

    def test_claude_admin_is_installed_without_router_and_audited(self):
        args = ["--app", "claude", "--claude-home", self.claude_home]
        self.install(*args, "--no-claude-hooks")
        self.assertFalse((self.claude_home / "hooks").exists())
        self.install(*args)
        target = self.claude_home / "hooks/ccswitch_admin.py"
        self.assertEqual(target.read_bytes(), (self.source / "template/claude/router/ccswitch_admin.py").read_bytes())
        self.assertFalse((self.claude_home / "router").exists())
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(target), "--help"],
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.install(*args, "--audit-upgrade").stdout)
        self.assertEqual(report["claude"]["drift_files"], [])
        target.write_bytes(b"# drift\n")
        report = json.loads(self.install(*args, "--audit-upgrade").stdout)
        self.assertIn("hooks/ccswitch_admin.py", report["claude"]["drift_files"])

    def test_audit_cli_isolates_only_ccswitch_configuration(self):
        before = dict(os.environ)
        isolated = []

        def probe(command, **kwargs):
            if "cc-switch" in command[-2]:
                env = kwargs["env"]
                paths = {env[key] for key in ("HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA")}
                self.assertEqual(len(paths), 1)
                root = Path(paths.pop())
                self.assertTrue(root.is_dir())
                self.assertNotEqual(root, self.home)
                self.assertEqual(env["USERPROFILE"], before["USERPROFILE"])
                (root / "cc-switch.db").write_bytes(b"isolated defaults")
                isolated.append(root)
            else:
                self.assertNotIn("env", kwargs)
                self.assertEqual(os.environ["USERPROFILE"], before["USERPROFILE"])
            return subprocess.CompletedProcess(command, 0, "fixture 1.2.3\n", "")

        class _ProbePopen:
            def __init__(self, command, **kwargs):
                self.pid = 31337
                self._command = command
                self._kwargs = kwargs
                self.returncode = 0

            def communicate(self, timeout=None):
                res = probe(self._command, **self._kwargs)
                self.returncode = res.returncode
                return res.stdout, res.stderr

            def kill(self):
                pass

        with mock.patch.object(installer.subprocess, "run", side_effect=probe), \
                mock.patch.object(installer.subprocess, "Popen", side_effect=_ProbePopen):
            for binary in ("cc-switch", "cc-switch.exe", "codex", "claude", "agy", "dsh"):
                self.assertEqual(installer._audit_cli({"native_binary": binary})["version"], "1.2.3")
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(len(isolated), 2)
        self.assertTrue(all(not root.exists() for root in isolated))

    def test_audit_cli_cleans_isolated_configuration_when_probe_fails(self):
        before = dict(os.environ)
        for failure in (OSError("fixture executable unavailable"), subprocess.TimeoutExpired("cc-switch", 3)):
            with self.subTest(failure=type(failure).__name__):
                isolated = []

                def probe(command, **kwargs):
                    root = Path(kwargs["env"]["HOME"])
                    self.assertTrue(root.is_dir())
                    self.assertTrue(root.is_relative_to(self.root))
                    (root / "cc-switch.db").write_bytes(b"fixture defaults")
                    isolated.append(root)
                    raise failure

                class _FailingPopen:
                    def __init__(self, command, **kwargs):
                        self.pid = 31338
                        self._command = command
                        self._kwargs = kwargs
                        self.returncode = None
                        self._calls = 0

                    def communicate(self, timeout=None):
                        self._calls += 1
                        if self._calls == 1:
                            return probe(self._command, **self._kwargs)
                        return "", ""

                    def kill(self):
                        pass

                with mock.patch.object(installer.tempfile, "tempdir", str(self.root)), \
                        mock.patch.object(installer.subprocess, "Popen", side_effect=_FailingPopen), \
                        mock.patch.object(installer.os, "killpg", side_effect=lambda *_: None, create=True):
                    report = installer._audit_cli({"native_binary": "cc-switch", "native_installed": True})
                self.assertIsNone(report["version"])
                self.assertTrue(report["native_installed"])
                self.assertEqual(len(isolated), 1)
                self.assertFalse(isolated[0].exists())
                self.assertEqual(dict(os.environ), before)

    def test_subscription_candidates_separate_profile_enablement_and_availability(self):
        db = self.database()
        admin.prepare_cards(db, admin.default_router_port())
        card = self.cards(db)["Kimi"]
        card["env"]["ANTHROPIC_AUTH_TOKEN"] = "fixture-card-token"
        with closing(sqlite3.connect(db)) as con:
            con.execute("UPDATE providers SET settings_config=?, meta=? WHERE app_type='claude' AND name='Kimi'",
                        (json.dumps(card), json.dumps({"usage_script": {"templateType": "balance"}})))
            con.commit()
        profile = {"subscription_matrix": {
            "agents": {name: {"enabled": name != "dsh", "quota_scheme": "fixture_quota"}
                       for name in ("agy", "codex", "claude", "dsh")},
            "models": {
                "fixture-model": {"agent": "codex", "enabled": False, "quota_scheme": "fixture_quota"},
                "missing-model": {"agent": "codex", "enabled": True, "quota_scheme": "fixture_quota"},
                "card-model": {"agent": "claude", "card": "Kimi", "enabled": True, "quota_scheme": "token_plan"},
                "missing-card-model": {"agent": "claude", "card": "absent", "enabled": True, "quota_scheme": "balance"},
                "placeholder-model": {"agent": "claude", "card": "DeepSeek", "enabled": True, "quota_scheme": "balance"},
                "dsh-model": {"agent": "dsh", "enabled": False, "quota_scheme": "api_balance"},
            },
        }}
        self.codex.mkdir(parents=True)
        user_words.atomic_write_json(self.codex / "models_cache.json", {"models": [{"slug": "fixture-model"}]})
        detected = {name: {"native_installed": name != "antigravity"} for name in ("antigravity", "codex", "claude", "dsh")}
        with mock.patch.object(installer, "_load_fleet_profile", return_value=profile):
            report = installer.audit_upgrade_status(
                REPO_ROOT, self.plugin, self.codex, self.claude_home, db,
                detection=detected, is_sandbox_run=True, dsh_home=self.home / ".dsh")
        candidates = report["subscription_candidates"]
        self.assertEqual(set(candidates["agents"]), {"agy", "codex", "claude", "dsh"})
        self.assertFalse(candidates["agents"]["agy"]["currently_available"])
        self.assertTrue(candidates["agents"]["dsh"]["currently_available"])
        self.assertFalse(candidates["agents"]["dsh"]["enabled_in_profile"])
        self.assertFalse(candidates["models"]["fixture-model"]["enabled_in_profile"])
        self.assertTrue(candidates["models"]["fixture-model"]["currently_available"])
        self.assertFalse(candidates["models"]["missing-model"]["currently_available"])
        self.assertFalse(candidates["models"]["missing-card-model"]["currently_available"])
        self.assertTrue(candidates["models"]["card-model"]["currently_available"])
        self.assertFalse(candidates["models"]["placeholder-model"]["currently_available"])
        self.assertEqual(candidates["models"]["card-model"]["quota_scheme"], "balance")
        interop_detected = {
            "antigravity": {"native_installed": False, "interop_available": False},
            "codex": {"native_installed": False, "interop_available": True},
            "claude": {"native_installed": True, "interop_available": False},
            "dsh": {"native_installed": False, "interop_available": True},
        }
        empty_codex = self.root / "wsl-empty-codex"
        empty_codex.mkdir(parents=True)
        with mock.patch.object(installer, "_load_fleet_profile", return_value=profile):
            interop_report = installer.audit_upgrade_status(
                REPO_ROOT, self.plugin, empty_codex, self.claude_home, db,
                detection=interop_detected, is_sandbox_run=True, dsh_home=self.root / "wsl-empty-dsh")
        interop_models = interop_report["subscription_candidates"]["models"]
        self.assertTrue(interop_models["fixture-model"]["currently_available"])
        self.assertTrue(interop_models["dsh-model"]["currently_available"])
        cli_report = json.loads(self.install("--audit-upgrade", "--app", "claude", "--claude-home", self.claude_home,
                                             "--ccswitch-db", db).stdout)
        self.assertIn("subscription_candidates", cli_report)

    def test_router_chmod_failure_preserves_synced_scripts(self):
        source = self.root / "router-template"
        source.mkdir()
        for name in ("router.py", "cc-router.sh", "router.windows.json", "router.wsl.json"):
            (source / name).write_bytes(b"{}\n" if name.endswith(".json") else b"# fixture\n")
        with mock.patch.object(Path, "chmod", side_effect=OSError("fixture permission denied")) as chmod:
            installer._sync_cc_router(source, self.claude_home)
        self.assertGreaterEqual(chmod.call_count, 2)
        for name in ("router.py", "cc-router.sh"):
            self.assertEqual((self.claude_home / "router" / name).read_bytes(), (source / name).read_bytes())

    def test_initial_router_port_uses_profile_and_preserves_existing_config(self):
        source = self.source / "template/claude/router"
        platform_file = "router.windows.json" if os.name == "nt" else "router.wsl.json"
        template = (source / platform_file).read_bytes()
        default_port = json.loads(template)["port"]
        port_key = "port" if os.name == "nt" else "wsl_port"
        for port in (default_port, default_port + 17):
            target_home = self.root / ("claude-port-" + str(port))
            profile = {"runtime_tunables": {"headless_dispatch": {"router": {port_key: port}}}}
            with mock.patch.object(installer, "_load_fleet_profile", return_value=profile):
                installer._sync_cc_router(source, target_home, dry_run=True)
                self.assertFalse((target_home / "router/router.json").exists())
                installer._sync_cc_router(source, target_home)
            primary = target_home / "router/router.json"
            self.assertEqual(json.loads(primary.read_bytes())["port"], port)
            if port == default_port:
                self.assertEqual(primary.read_bytes(), template)
            custom = b'{"port": 8456, "custom": true}\n'
            primary.write_bytes(custom)
            installer._sync_cc_router(source, target_home)
            self.assertEqual(primary.read_bytes(), custom)

    def test_new_claude_hooks_use_selected_interpreter_when_existing_prefix_is_invalid(self):
        path = self.claude_home / "settings.json"
        user_words.atomic_write_json(path, {"hooks": {"SessionStart": [{"matcher": "^(compact|resume)$", "hooks": [{
            "type": "command", "command": sys.executable,
            "args": ["-I", "-B", str(self.claude_home / "hooks/session_context_loader.py"), "--user-only"],
        }]}]}})
        prefix = "fixture-python -X utf8 -I -B"
        with mock.patch.object(installer, "_is_working_python_cmd", return_value=False):
            plan = installer._plan_claude_settings(path, REPO_ROOT / "template/claude/hooks.json", prefix)
        doc = json.loads(plan.change.content)
        for event in ("SessionStart", "PreToolUse", "Stop"):
            handler = next(h for g in doc["hooks"][event] for h in g["hooks"] if "uguard_hook.py" in h["command"])
            self.assertTrue(handler["command"].startswith(prefix + " "))
            self.assertIn(f"--event {event}", handler["command"])

    def test_claude_upgrade_migrates_hooks_preserves_user_assets_and_is_idempotent(self):
        db = self.database()
        self.prompts(db)
        args = ["--app", "claude", "--claude-home", self.claude_home, "--with-cc-router",
                "--ccswitch-db", db, "--disable-ccswitch-prompts"]
        before = self.snapshot()
        self.install(*args, "--dry-run")
        self.assertEqual(self.snapshot(), before)
        self.install(*args)
        self.assertTrue((self.claude_home / "CLAUDE.md").is_file())
        with closing(sqlite3.connect(db)) as con:
            self.assertEqual(con.execute("SELECT enabled FROM prompts ORDER BY id").fetchall(), [(0,), (0,)])
        backups = list(self.root.glob("cc-switch.db.bak-before-disable-prompts-*"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as con:
            self.assertEqual(con.execute("SELECT enabled FROM prompts ORDER BY id").fetchall(), [(1,), (2,)])
        for name in ("reader", "auditor", "collaborator"):
            self.assertEqual((self.claude_home / f"agents/readonly-{name}.md").read_bytes(),
                             (REPO_ROOT / f"template/claude/agents/readonly-{name}.md").read_bytes())
        for name in installer.CLAUDE_HOOK_SCRIPTS:
            self.assertEqual((self.claude_home / "hooks" / name).read_bytes(),
                             (REPO_ROOT / "template/user-guard/hooks" / name).read_bytes())
        exact_chmod = False
        if os.name != "nt":
            chmod_probe = self.root / "chmod-probe"
            chmod_probe.write_bytes(b"probe")
            chmod_probe.chmod(0o755)
            exact_chmod = chmod_probe.stat().st_mode & 0o777 == 0o755
            chmod_probe.unlink()
        for path in (REPO_ROOT / "template/claude/router").iterdir():
            if path.is_file():
                target = self.claude_home / "router" / path.name
                self.assertEqual(target.read_bytes(), path.read_bytes())
                if os.name != "nt" and target.suffix in {".sh", ".py"}:
                    if exact_chmod:
                        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
                    else:
                        self.assertEqual(target.stat().st_mode & 0o111, 0o111)
        platform_file = "router.windows.json" if os.name == "nt" else "router.wsl.json"
        self.assertEqual((self.claude_home / "router/router.json").read_bytes(),
                         (REPO_ROOT / "template/claude/router" / platform_file).read_bytes())

        custom_tail = "## 工具与本机环境\r\n- 保留用户自定义编译器。\r\n"
        (self.claude_home / "CLAUDE.md").write_bytes(("# 旧规则\r\n\r\n" + custom_tail).encode())
        user_words.atomic_write_json(self.claude_home / "router/router.json", {"port": 8250, "personal": True})
        custom_router = (self.claude_home / "router/router.json").read_bytes()
        (self.claude_home / "agents/custom.md").write_bytes(b"custom agent\n")
        for relative in ("hooks/state/history.md", "hooks/logs/history.log", "hooks/__pycache__/old.pyc",
                         "hooks/background_status.py", "hooks/receipts_stop.py", "hooks/read_delegation_guard.py"):
            path = self.claude_home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"keep runtime history or remove retired code\n")
        loader = {"type": "command", "command": sys.executable, "args": ["-I", "-B",
                  str(self.claude_home / "hooks/session_context_loader.py"), "--user-only"]}
        status = {"type": "command", "command": sys.executable, "args": ["-B", str(self.claude_home / "hooks/status_guard.py")]}
        third_party = {"type": "command", "command": "personal-hook", "args": ["keep", "all"], "timeout": 7}
        external = {"type": "command", "command": sys.executable,
                    "args": [str(self.root / "third-party/hooks/session_context_loader.py"), "--user-only"]}
        env = {"ANTHROPIC_MODEL": "personal-model", "ANTHROPIC_AUTH_TOKEN": "tok_installer_private",
               "CLAUDE_CODE_EFFORT_LEVEL": "high", "PERSONAL_SETTING": "keep"}
        legacy = {"env": env, "effortLevel": "high", "permissions": {"allow": ["Read"]}, "hooks": {
            "SessionStart": [{"matcher": "startup", "hooks": [copy.deepcopy(loader)]}],
            "Stop": [{"hooks": [copy.deepcopy(loader), third_party, external]}],
            "PreToolUse": [{"matcher": "Bash|PowerShell|Read|Grep", "hooks": [status]}],
        }}
        user_words.atomic_write_json(self.claude_home / "settings.json", legacy)
        self.install(*args)
        updated = json.loads((self.claude_home / "settings.json").read_bytes())
        self.assertEqual(updated["env"], env)
        self.assertEqual(updated["effortLevel"], "high")
        self.assertEqual(updated["permissions"], legacy["permissions"])
        self.assertTrue((self.claude_home / "CLAUDE.md").read_bytes().endswith(custom_tail.encode()))
        self.assertNotIn(b"\n", (self.claude_home / "CLAUDE.md").read_bytes().replace(b"\r\n", b""))
        self.assertEqual((self.claude_home / "router/router.json").read_bytes(), custom_router)
        self.assertEqual((self.claude_home / "agents/custom.md").read_bytes(), b"custom agent\n")
        for name in (*installer.CLAUDE_RETIRED_SCRIPTS, "__pycache__"):
            self.assertFalse((self.claude_home / "hooks" / name).exists())
        for relative in ("hooks/state/history.md", "hooks/logs/history.log"):
            self.assertEqual((self.claude_home / relative).read_bytes(), b"keep runtime history or remove retired code\n")
        stop_handlers = [handler for group in updated["hooks"]["Stop"] for handler in group["hooks"]]
        self.assertIn(third_party, stop_handlers)
        self.assertIn(external, stop_handlers)
        for event, groups in updated["hooks"].items():
            for group in groups:
                for handler in group["hooks"]:
                    name = installer._claude_managed_script(handler, self.claude_home)
                    if name:
                        self.assertNotIn("args", handler)
                        tokens = installer._claude_hook_tokens(handler)
                        self.assertEqual(tokens[1:5], installer.HOOK_PYTHON_FLAGS)
                        self.assertTrue(installer._same_nonlink_path(Path(tokens[0]), Path(sys.executable)))
                        if event == "Stop":
                            self.assertNotEqual(name, "session_context_loader.py")
        self.assertNotIn("Bash|PowerShell|Read|Grep", [group.get("matcher") for group in updated["hooks"]["PreToolUse"]])
        installed_loader = next(handler for group in updated["hooks"]["SessionStart"] for handler in group["hooks"]
                                if "uguard_hook.py" in handler["command"])
        live = subprocess.run(installer._claude_hook_tokens(installed_loader), cwd=self.project,
                              input=json.dumps({**self.payload, "source": "compact"}), capture_output=True,
                              text=True, encoding="utf-8", timeout=20)
        self.assertEqual(live.returncode, 0, live.stderr)
        self.assertEqual(json.loads(live.stdout)["hookSpecificOutput"]["hookEventName"], "SessionStart")
        before = self.snapshot()
        self.install(*args)
        self.assertEqual(self.snapshot(), before)

    def test_app_modes_and_custom_directory_safety(self):
        for args in (
            ["--app", "all", "--claude-home", self.claude_home],
            ["--app", "all", "--target-dir", self.plugin, "--claude-home", self.claude_home],
            ["--app", "claude", "--target-dir", self.plugin],
            ["--app", "claude", "--codex-home", self.codex],
            ["--app", "both", "--claude-home", self.claude_home],
            ["--app", "codex", "--claude-home", self.claude_home],
            ["--app", "antigravity", "--claude-home", self.claude_home],
            ["--app", "claude", "--claude-home", self.claude_home, "--claude-handoff-compactions", "-1"],
        ):
            with self.subTest(args=args):
                self.install(*args, code=2)
        self.assertFalse(self.default_codex.exists())
        self.assertFalse(self.claude_home.exists())
        self.install(*self.all_args())
        for path in (self.plugin / "plugin.json", self.codex / "AGENTS.md", self.claude_home / "CLAUDE.md"):
            self.assertTrue(path.is_file())
        before = self.snapshot()
        overlapping = self.all_args()
        overlapping[overlapping.index("--claude-home") + 1] = self.codex / "nested"
        self.install(*overlapping, code=2)
        self.assertEqual(self.snapshot(), before)

    def test_sandbox_prompt_guard_and_model_hook_opt_outs(self):
        host_db = self.home / ".cc-switch/cc-switch.db"
        host_db.parent.mkdir(parents=True)
        shutil.copy2(self.database(), host_db)
        self.prompts(host_db)
        before = host_db.read_bytes(), host_db.stat().st_mtime_ns
        args = ["--app", "claude", "--claude-home", self.claude_home, "--disable-ccswitch-prompts",
                "--no-claude-hooks", "--no-claude-rules", "--no-claude-agents", "--no-claude-skill", "--no-model-config"]
        self.install(*args)
        self.assertEqual((host_db.read_bytes(), host_db.stat().st_mtime_ns), before)
        self.assertFalse(self.claude_home.exists())
        self.assertEqual(list(host_db.parent.iterdir()), [host_db])

    def test_dsh_standalone_rules_tail_skill_sync_and_readonly_drift_audit(self):
        dsh = self.root / 'target/dsh'
        dsh.mkdir(parents=True)
        tail = '## 工具与本机\n保留用户自定义工具。\n'
        rules = dsh / 'AGENTS.md'
        rules.write_text('旧规则\n\n' + tail, encoding='utf-8')
        cordis = dsh / 'cordis.patch.yml'
        cordis.write_text('# user config must survive\n', encoding='utf-8')
        credentials = dsh / '.credentials.yaml'
        credentials.write_text('deepseek-official:\n  DEEPSEEK_API_KEY: fixture-secret\n', encoding='utf-8')
        dotenv = dsh / '.env'
        dotenv.write_text('DEEPSEEK_API_KEY=fixture-dotenv-secret\n', encoding='utf-8')
        protected = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (cordis, credentials, dotenv)}
        args = ['--app', 'dsh', '--dsh-home', dsh]
        before = self.snapshot()
        self.install(*args, '--dry-run')
        self.assertEqual(self.snapshot(), before)
        self.install(*args)
        self.assertTrue(rules.read_text(encoding='utf-8').endswith(tail))
        script = dsh / 'skills/cae-harness/scripts/cae_harness.py'
        self.assertEqual(script.read_bytes(), (REPO_ROOT / 'template/user-guard/skills/cae-harness/scripts/cae_harness.py').read_bytes())
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected}, protected)
        self.assertFalse((dsh / 'settings.json').exists())
        self.assertFalse((dsh / 'hooks').exists())
        self.assertFalse(self.default_codex.exists())
        self.assertFalse(self.claude_home.exists())

        def audit():
            return installer.audit_upgrade_status(REPO_ROOT, dsh_home=dsh, app_scope='dsh',
                                                  detection={}, is_sandbox_run=True)

        before = self.snapshot()
        report = audit()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(report['dsh']['drift_files'], [])
        self.assertTrue(report['dsh']['credentials_present'])
        self.assertTrue(report['summary']['all_aligned'])
        self.assertEqual(report['summary']['recommended_actions'], [])
        serialized = json.dumps(report)
        for value in ('fixture-secret', 'fixture-dotenv-secret', str(dsh)):
            self.assertNotIn(value, serialized)
        script.write_text('# drift\n', encoding='utf-8')
        rules.write_text('drift\n\n' + tail, encoding='utf-8')
        report = audit()
        self.assertEqual(set(report['dsh']['drift_files']), {'AGENTS.md', 'skills/cae-harness/scripts/cae_harness.py'})
        self.assertEqual(report['summary']['recommended_actions'], ['sync_dsh'])
        self.install(*args)
        self.assertTrue(audit()['summary']['all_aligned'])
        before = self.snapshot()
        self.install(*args)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected}, protected)

    def test_dsh_opt_outs_invalid_combinations_and_four_directory_all_install(self):
        dsh = self.root / 'target/dsh'
        self.install('--app', 'dsh', '--dsh-home', dsh, '--no-dsh-rules', '--no-dsh-skill')
        self.assertFalse(dsh.exists())
        for mode in ('codex', 'antigravity', 'claude', 'both'):
            with self.subTest(mode=mode):
                before = self.snapshot()
                self.install('--app', mode, '--dsh-home', dsh, code=2)
                self.assertEqual(self.snapshot(), before)
        for flag, target in (('--target-dir', self.plugin), ('--codex-home', self.codex), ('--claude-home', self.claude_home)):
            with self.subTest(flag=flag):
                self.install('--app', 'dsh', '--dsh-home', dsh, flag, target, code=2)
        self.install(*self.all_args())
        self.assertFalse((self.home / '.dsh').exists())
        self.assertTrue((self.claude_home / 'skills/cae-harness/SKILL.md').is_file())
        self.install(*self.all_args(), '--dsh-home', dsh)
        self.assertTrue((dsh / 'AGENTS.md').is_file())
        self.assertTrue((dsh / 'skills/cae-harness/SKILL.md').is_file())
        self.assertFalse((dsh / 'cordis.patch.yml').exists())
        self.assertFalse((dsh / '.credentials.yaml').exists())
        before = self.snapshot()
        self.install(*self.all_args(), '--dsh-home', self.codex / 'nested', code=2)
        self.assertEqual(self.snapshot(), before)

    def test_dsh_auto_planning_handles_standalone_explicit_and_detected_peers(self):
        dsh = self.home / '.dsh'
        detection = {name: {'native_installed': name == 'dsh', 'interop_available': False, 'interop_status': 'not_detected'}
                     for name in ('antigravity', 'codex', 'claude', 'dsh')}

        def run_main(tokens, detected):
            with mock.patch.object(installer, '__file__', str(self.source / 'install.py')), \
                 mock.patch.object(installer, 'detect_installed_apps', return_value=detected), \
                 mock.patch.object(installer, 'detect_hook_python_command', return_value=f'"{sys.executable}" -X utf8 -I -B'), \
                 mock.patch.object(installer, 'detect_zotero_mcp_executable', return_value=None), \
                 mock.patch.object(sys, 'argv', ['install.py', *tokens, '--yes', '--skip-test', '--no-zotero']), \
                 redirect_stdout(io.StringIO()):
                installer.main()

        run_main(['--app', 'auto'], detection)
        self.assertTrue((dsh / 'AGENTS.md').is_file())
        self.assertFalse(self.claude_home.exists())
        self.assertFalse(self.default_codex.exists())
        explicit = self.root / 'explicit-dsh'
        run_main(['--app', 'auto', '--dsh-home', str(explicit)], {})
        self.assertTrue((explicit / 'skills/cae-harness/SKILL.md').is_file())
        detection['claude']['native_installed'] = True
        run_main(['--app', 'auto'], detection)
        self.assertTrue((self.claude_home / 'CLAUDE.md').is_file())
        self.assertTrue((self.claude_home / 'skills/cae-harness/SKILL.md').is_file())
        (dsh / 'AGENTS.md').write_text('stale\n', encoding='utf-8')
        run_main(['--app', 'all'], detection)
        rendered_dsh = installer.render_rules_for_client(
            (REPO_ROOT / 'template/user-guard/rules/AGENTS.md').read_text(encoding='utf-8'), 'dsh'
        ).encode('utf-8')
        self.assertEqual(installer._rules_core((dsh / 'AGENTS.md').read_bytes()),
                         installer._rules_core(rendered_dsh))

    def test_audit_reports_harness_drift_on_each_installed_peer(self):
        dsh = self.root / 'target/dsh'
        self.install(*self.all_args(), '--dsh-home', dsh)
        paths = {'antigravity': self.plugin / 'skills/cae-harness/scripts/cae_harness.py',
                 'codex': self.codex / 'skills/cae-harness/scripts/cae_harness.py',
                 'claude': self.claude_home / 'skills/cae-harness/scripts/cae_harness.py',
                 'dsh': dsh / 'skills/cae-harness/scripts/cae_harness.py'}
        for path in paths.values():
            path.write_text('# drift\n', encoding='utf-8')
        report = installer.audit_upgrade_status(REPO_ROOT, self.plugin, self.codex, self.claude_home,
                                               self.root / 'missing.db', detection={}, is_sandbox_run=True,
                                               app_scope='all', dsh_home=dsh)
        for name in paths:
            self.assertIn('skills/cae-harness/scripts/cae_harness.py', report[name]['drift_files'])
        self.assertIn('sync_dsh', report['summary']['recommended_actions'])

    def test_symmetric_root_overlap_and_missing_skill_drift_detection(self):
        dsh = self.root / 'target/dsh'
        self.install(*self.all_args(), '--dsh-home', dsh)
        shutil.rmtree(self.codex / 'skills/cae-harness')
        shutil.rmtree(self.claude_home / 'skills/cae-harness')
        report = installer.audit_upgrade_status(REPO_ROOT, self.plugin, self.codex, self.claude_home,
                                               self.root / 'missing.db', detection={}, is_sandbox_run=True,
                                               app_scope='all', dsh_home=dsh)
        self.assertIn('skills/cae-harness/scripts/cae_harness.py', report['codex']['drift_files'])
        self.assertIn('skills/cae-harness/scripts/cae_harness.py', report['claude']['drift_files'])
        nested_dsh = self.home / '.codex/skills/cae-harness/dsh-sub'
        with mock.patch.dict(os.environ, {'DSH_HOME': str(nested_dsh)}), \
             mock.patch.object(installer.Path, 'home', return_value=self.home):
            self.install('--app', 'codex', code=2)

    def test_ccswitch_skills_retirement_purge_and_audit_alignment_edges(self):
        db = self.database()
        self.prompts(db)
        skills_dir = db.parent / 'skills/task-alignment'
        skills_dir.mkdir(parents=True)
        (skills_dir / 'SKILL.md').write_text('# retired skill\n', encoding='utf-8')
        with closing(sqlite3.connect(db)) as con:
            con.execute('CREATE TABLE skills (id TEXT, name TEXT, enabled_claude INT, enabled_codex INT, enabled_dsh INT)')
            con.execute('INSERT INTO skills VALUES (?, ?, ?, ?, ?)', ('local:task-alignment', 'task-alignment', 1, 0, 1))
            con.commit()
        self.install(*self.all_args())
        admin.prepare_cards(db, admin.default_router_port())
        admin.switch_card(db, self.claude_home / 'settings.json', 'DeepSeek')
        gemini = self.plugin.parent.parent.parent
        user_words.atomic_write_json(gemini / 'antigravity-cli/settings.json', {'model': 'Gemini 4 Carbon (High)'})
        user_words.atomic_write_json(gemini / 'antigravity-cli/cache/cae_models_cache.json', {
            'models': [{'slug': 'gemini-4-carbon-high'}, {'slug': 'gpt-oss-120b-medium'}],
        })
        detection = {
            'antigravity': {'native_installed': True},
            'codex': {'native_installed': False, 'interop_available': True},
            'claude': {'native_installed': True},
            'dsh': {'native_installed': False},
            'cc_switch': {'native_installed': True},
        }
        report = installer.audit_upgrade_status(REPO_ROOT, self.plugin, self.codex, self.claude_home,
                                               db, detection=detection, is_sandbox_run=True, app_scope='all')
        self.assertEqual(len(report['cc_switch']['enabled_skills']), 1)
        self.assertIn('disable_ccswitch_prompts', report['summary']['recommended_actions'])
        self.assertNotIn('review_antigravity_models', report['summary']['recommended_actions'])
        self.assertNotIn('refresh_codex_model_catalog', report['summary']['recommended_actions'])
        self.assertEqual(report['codex']['catalog_source'], 'interop_delegated')

        self.install(*self.all_args(), '--ccswitch-db', db, '--disable-ccswitch-prompts')
        st = admin.inspect_status(db, self.claude_home / 'settings.json')
        self.assertEqual(st['enabled_prompts'], [])
        self.assertEqual(st['enabled_skills'], [])
        self.assertEqual(st['stored_skill_count'], 1)
        self.assertEqual(st['skills_dir_entries'], ['task-alignment'])

        self.install(*self.all_args(), '--ccswitch-db', db, '--purge-ccswitch-legacy')
        st_after = admin.inspect_status(db, self.claude_home / 'settings.json')
        self.assertEqual(st_after['stored_prompt_count'], 0)
        self.assertEqual(st_after['stored_skill_count'], 0)
        self.assertEqual(st_after['skills_dir_entries'], [])
        self.assertTrue(any((db.parent / 'backups').glob('retired-skills-*')))
        aligned = installer.audit_upgrade_status(REPO_ROOT, self.plugin, self.codex, self.claude_home,
                                                db, detection=detection, is_sandbox_run=True, app_scope='all')
        self.assertTrue(aligned['summary']['all_aligned'], aligned['summary'])
        self.assertFalse(aligned['cc_switch']['legacy_residue_present'])

    def test_dsh_detection_and_protected_global_skill_roots(self):
        with mock.patch.object(installer.Path, 'home', return_value=self.home), \
             mock.patch.object(installer, '_native_cli', side_effect=lambda name, home: 'fixture-dsh' if name == 'dsh' else None), \
             mock.patch.object(installer, '_windows_app_binaries', return_value=(None, None)), \
             mock.patch.object(installer, '_find_windows_codex_package', return_value=(None, None)), \
             mock.patch.object(installer, '_first_executable', return_value=None), \
             mock.patch.object(installer, '_probe_windows_wsl', return_value={'apps': {}, 'status': 'checked'}), \
             mock.patch.object(installer, '_is_wsl', return_value=False), \
             mock.patch.object(installer, 'detect_zotero_mcp_executable', return_value=None):
            report = installer.detect_installed_apps()
            self.assertTrue(report['dsh']['native_installed'])
            self.assertEqual(report['recommended_app_mode'], 'dsh')
            self.assertTrue(report['single_app_only_on_host'])
            for relative in ('.claude', '.claude/skills', '.dsh', '.dsh/skills'):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    installer.validate_install_target(self.home / relative, self.source)

    def test_handoff_policy_persists_and_model_defaults_are_explicit(self):
        args = ["--app", "claude", "--claude-home", self.claude_home]
        self.install(*args, "--claude-handoff-mode", "enforce", "--claude-handoff-compactions", "3")
        path = self.claude_home / "settings.json"
        policy_path = self.claude_home / "hooks/state/handoff/policy.json"
        initial = path.read_bytes(), path.stat().st_mtime_ns
        self.install(*args)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), initial)
        self.assertEqual(json.loads(policy_path.read_bytes()), {"mode": "enforce", "compact_limit": 3})
        self.assertIn(b"uguard_hook.py", path.read_bytes())
        self.install(*args, "--claude-handoff-mode", "off")
        self.assertEqual((self.claude_home / "hooks/handoff_gate.py").read_bytes(), (HOOKS_DIR / "handoff_gate.py").read_bytes())
        off_hooks = json.loads(path.read_bytes())["hooks"]
        self.assertNotIn("PostCompact", off_hooks)
        self.assertNotIn("UserPromptSubmit", off_hooks)
        self.assertEqual(json.loads(policy_path.read_bytes())["mode"], "off")
        for name in ("uguard_hook.py", "status_guard.py", "reply_repeat_guard.py"):
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-I", str(self.claude_home / "hooks" / name), "--help"],
                                    cwd=self.project, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
        initial = self.snapshot()
        self.install(*args)
        self.assertEqual(self.snapshot(), initial)
        doc = json.loads(path.read_bytes())
        doc.update(effortLevel="medium", env={"CLAUDE_CODE_EFFORT_LEVEL": "low", "ANTHROPIC_AUTH_TOKEN": "tok_preserved"})
        user_words.atomic_write_json(path, doc)
        self.install(*args, "--sync-model-defaults")
        updated = json.loads(path.read_bytes())
        self.assertEqual(updated["effortLevel"], "xhigh")
        self.assertEqual(updated["env"], {"ANTHROPIC_AUTH_TOKEN": "tok_preserved"})

    def test_upgrade_audit_reports_drift_models_prompts_and_converges_after_sync(self):
        db = self.database()
        self.prompts(db)
        self.codex.mkdir(parents=True)
        cache = {"models": [
            {"slug": "gpt-6.0-sol", "priority": 0, "max_context_window": 400000,
             "supported_reasoning_levels": [{"effort": "high"}]},
            {"slug": "gpt-6.1-sol", "priority": 9, "max_context_window": 872000,
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "max"}, {"effort": "ultra", "description": "delegation"}]},
            {"slug": "gpt-6-astra", "max_context_window": 872000, "supported_efforts": ["high", "max"]},
        ]}
        user_words.atomic_write_json(self.codex / "models_cache.json", cache)
        (self.codex / "config.toml").write_bytes(b'model = "gpt-6.0-sol"\nmodel_reasoning_effort = "high"\nmodel_context_window = 400000 # retain comment\n')
        self.install(*self.all_args())
        admin.prepare_cards(db, admin.default_router_port())
        admin.switch_card(db, self.claude_home / "settings.json", "Kimi")
        for path in (self.plugin / "rules/AGENTS.md", self.codex / "AGENTS.md", self.claude_home / "CLAUDE.md"):
            path.write_bytes(b"rule drift\n")
        self.install("--app", "claude", "--claude-home", self.claude_home, "--no-claude-rules")
        audit_args = ["--audit-upgrade", "--target-dir", self.plugin, "--codex-home", self.codex,
                      "--claude-home", self.claude_home, "--ccswitch-db", db]
        before = self.snapshot()
        report = json.loads(self.install(*audit_args).stdout)
        self.assertEqual(self.snapshot(), before)
        for name in ("antigravity", "codex", "claude"):
            self.assertTrue(report[name]["drift_files"])
        self.assertTrue(report["codex"]["upgrade_recommended"])
        self.assertEqual(report["codex"]["latest_sol_slug"], "gpt-6.1-sol")
        self.assertEqual(report["codex"]["latest_astra_slug"], "gpt-6-astra")
        self.assertEqual(report["codex"]["declared_max_context_window"], 872000)
        self.assertEqual(report["codex"]["supported_efforts"], ["high", "max"])
        self.assertEqual(len(report["cc_switch"]["enabled_prompts"]), 2)
        self.assertIn("sync_codex_model_defaults", report["summary"]["recommended_actions"])
        self.assertIn("disable_ccswitch_prompts", report["summary"]["recommended_actions"])
        self.install(*self.all_args(), "--sync-model-defaults", "--ccswitch-db", db, "--disable-ccswitch-prompts")
        self.assertIn(b"872000 # retain comment", (self.codex / "config.toml").read_bytes())
        before = self.snapshot()
        report = json.loads(self.install(*audit_args).stdout)
        self.assertEqual(self.snapshot(), before)
        for name in ("antigravity", "codex", "claude"):
            self.assertEqual(report[name]["drift_files"], [])
        self.assertTrue(report["codex"]["model_aligned"])
        self.assertTrue(report["claude"]["hooks_aligned"])
        self.assertEqual(report["cc_switch"]["enabled_prompts"], [])
        self.assertTrue(report["summary"]["all_aligned"])

    def test_audit_redacts_credentials_and_probes_router_only_when_selected(self):
        db = self.database()
        self.install(*self.all_args())
        settings = self.claude_home / "settings.json"
        admin.prepare_cards(db, admin.default_router_port())
        admin.switch_card(db, settings, "Kimi")
        doc = json.loads(settings.read_bytes())
        doc["env"]["ANTHROPIC_AUTH_TOKEN"] = "tok_audit_private"
        user_words.atomic_write_json(settings, doc)
        detection = {name: {"native_installed": False} for name in ("antigravity", "codex", "claude", "cc_switch")}
        loaded_admin = installer._load_ccswitch_admin(REPO_ROOT)
        before = self.snapshot()
        with mock.patch.object(installer, "_load_ccswitch_admin", return_value=loaded_admin), \
                mock.patch.object(loaded_admin.urllib.request, "urlopen") as health:
            report = installer.audit_upgrade_status(self.source, self.plugin, self.codex, self.claude_home,
                                                     db, detection=detection, is_sandbox_run=True)
            health.assert_not_called()
        text = json.dumps(report)
        self.assertNotIn("tok_audit_private", text)
        self.assertNotIn("tok_official_test", text)
        self.assertNotIn(str(self.home), text)
        self.assertEqual(self.snapshot(), before)
        admin.switch_card(db, settings, "K3.1主+DS子代理")
        response = mock.MagicMock(status=200)
        response.read.return_value = json.dumps({"ok": True, "port": admin.default_router_port()}).encode()
        response.__enter__.return_value = response
        with mock.patch.object(installer, "_load_ccswitch_admin", return_value=loaded_admin), \
                mock.patch.object(loaded_admin.urllib.request, "urlopen", return_value=response) as health:
            report = installer.audit_upgrade_status(self.source, self.plugin, self.codex, self.claude_home,
                                                     db, detection=detection, is_sandbox_run=True)
            health.assert_called_once()
            self.assertTrue(report["cc_switch"]["router_health"]["ok"])
        self.assertEqual(len([card for card in report["cc_switch"]["cards"] if card["name"] in ("Kimi", "DeepSeek", "K3.1主+DS子代理")]), 3)

    def test_planning_and_probe_preserve_legacy_contracts(self):
        detection = {name: {"native_installed": False, "interop_available": False, "interop_status": "not_detected"}
                     for name in ("antigravity", "codex", "claude")}
        for mode, expected in (("all", (True, True, True)), ("both", (True, True, False)),
                               ("claude", (False, False, True)), ("codex", (False, True, False)),
                               ("antigravity", (True, False, False))):
            self.assertEqual(installer.resolve_multi_app_plan(mode, False, False, False, detection, False, True, False), expected)
        self.assertEqual(installer.resolve_app_plan("both", False, False, detection, False, True, False), (True, True))
        self.assertEqual(installer.resolve_multi_app_plan("auto", False, False, True, {}, False, True, False), (False, False, True))
        detection["claude"]["native_installed"] = True
        with redirect_stdout(io.StringIO()):
            self.assertEqual(installer.resolve_multi_app_plan("auto", False, False, False, detection, False, True, False), (False, False, True))
        detection["antigravity"]["native_installed"] = True
        self.assertEqual(installer.resolve_multi_app_plan("auto", False, False, False, detection, False, True, False), (True, False, True))
        detection["claude"]["native_installed"] = False
        detection["antigravity"]["native_installed"] = False
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            installer.resolve_multi_app_plan("auto", False, False, False, detection, False, True, False)
        self.assertEqual(stopped.exception.code, 2)
        with mock.patch.object(installer.Path, "home", return_value=self.home), \
                mock.patch.object(installer, "_native_cli", return_value=None), \
                mock.patch.object(installer, "_first_executable", return_value=None), \
                mock.patch.object(installer, "_find_windows_codex_package", return_value=(None, None)), \
                mock.patch.object(installer, "_windows_app_binaries", return_value=(None, None)), \
                mock.patch.object(installer, "_probe_windows_wsl", return_value={"apps": {}, "status": "unknown"}), \
                mock.patch.object(installer, "_is_wsl", return_value=False), \
                mock.patch.object(installer, "detect_zotero_mcp_executable", return_value=None):
            probe = installer.detect_installed_apps()
        self.assertEqual(probe["recommended_app_mode"], "none")
        self.assertEqual(probe["claude"]["target_claude_home"], str(self.claude_home))
        self.assertFalse(probe["cc_switch"]["native_installed"])

    def test_malformed_claude_settings_abort_before_any_client_write(self):
        self.plugin.mkdir(parents=True)
        (self.plugin / "keep.txt").write_bytes(b"preserve before preflight\n")
        self.claude_home.mkdir(parents=True)
        path = self.claude_home / "settings.json"
        for content in (b"{broken", b'{"hooks":[]}', b'{"hooks":{"Stop":[{"hooks":{}}]}}',
                        b'{"env":[]}', b'{"hooks":{},"hooks":{}}'):
            with self.subTest(content=content):
                path.write_bytes(content)
                before = self.snapshot()
                self.install(*self.all_args(), code=2)
                self.assertEqual(self.snapshot(), before)

    def test_probe_reports_all_native_combinations_and_wsl_extensions(self):
        available = set()
        with mock.patch.object(installer.Path, "home", return_value=self.home), \
                mock.patch.object(installer, "_native_cli", side_effect=lambda name, home: str(home / name) if name in available else None), \
                mock.patch.object(installer, "_windows_app_binaries", return_value=(None, None)), \
                mock.patch.object(installer, "_first_executable", return_value=None), \
                mock.patch.object(installer, "_find_windows_codex_package", return_value=(None, None)), \
                mock.patch.object(installer, "_probe_windows_wsl", return_value={"apps": {}, "status": "checked"}), \
                mock.patch.object(installer, "_is_wsl", return_value=False), \
                mock.patch.object(installer, "detect_zotero_mcp_executable", return_value=None):
            for clients, mode in (((), "none"), (("claude",), "claude"), (("codex",), "codex"),
                                   (("agy",), "antigravity"), (("agy", "codex"), "both"),
                                   (("agy", "claude"), "all"), (("agy", "codex", "claude"), "all")):
                available.clear()
                available.update(clients)
                self.assertEqual(installer.detect_installed_apps()["recommended_app_mode"], mode)
        output = "agy\t/opt/bin/agy\ttrue\ncodex\t/opt/bin/codex\ttrue\nclaude\t/opt/bin/claude\ttrue\ndsh\t/opt/bin/dsh\ttrue\ncc-switch\t\ttrue\n"
        with mock.patch.object(installer.shutil, "which", return_value="wsl.exe"), \
                mock.patch.object(installer, "_wsl_distro_list", side_effect=[["running"], ["running"]]), \
                mock.patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output)):
            result = installer._probe_windows_wsl()
        self.assertEqual(result["status"], "checked")
        self.assertEqual(result["apps"]["claude"][0]["binary"], "/opt/bin/claude")
        self.assertTrue(result["apps"]["cc-switch"][0]["config_dir_exists"])

    def test_windows_package_probe_reads_registry_without_spawning_subprocess(self):
        package_old = self.root / "codex-package-old"
        package_new = self.root / "codex-package-new"
        for pkg in (package_old, package_new):
            (pkg / "app/resources").mkdir(parents=True)
            (pkg / "app/resources/codex.exe").write_bytes(b"cli fixture\n")
            (pkg / "app/Codex.exe").write_bytes(b"desktop fixture\n")

        entries = [
            ("OpenAI.CodexPrimaryRuntime.v26_26.9999.0.0_x64__abc", str(package_old)),
            ("OpenAI.Codex_26.1100.1.0_x64__abc", str(package_old)),
            ("OpenAI.Codex_26.1111.2228.0_x64__abc", str(package_new)),
        ]

        class FakeKey:
            def __init__(self, sub=None):
                self.sub = sub

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        fake_winreg = SimpleNamespace(
            HKEY_CURRENT_USER=1,
            OpenKey=lambda root_or_key, path: FakeKey(None if root_or_key == 1 else path),
            QueryInfoKey=lambda key: (len(entries), 0, 0),
            EnumKey=lambda key, idx: entries[idx][0],
            QueryValueEx=lambda key, name: (dict(entries)[key.sub], 1),
        )
        before = self.snapshot()
        with mock.patch.object(installer, "_is_windows", return_value=True), \
                mock.patch.dict(sys.modules, {"winreg": fake_winreg}), \
                mock.patch.object(installer.subprocess, "run", side_effect=AssertionError("must not spawn subprocess")):
            self.assertEqual(
                installer._find_windows_codex_package(),
                (str(package_new / "app/resources/codex.exe"), str(package_new / "app/Codex.exe")),
            )
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.name == "nt", "Windows 应用包探测实测")
    def test_windows_package_probe_does_not_write_powershell_cache(self):
        before = self.snapshot()
        installer._find_windows_codex_package()
        self.assertEqual(self.snapshot(), before)

    def test_audit_reads_carbon_catalog_and_reports_unreadable_configs_without_writes(self):
        self.install(*self.all_args())
        gemini = self.plugin.parent.parent.parent
        user_words.atomic_write_json(gemini / "antigravity-cli/settings.json", {"model": "Claude 4.6"})
        user_words.atomic_write_json(gemini / "antigravity-cli/cache/cae_models_cache.json", {"models": [
            {"slug": "gemini-4-carbon-high", "max_context_window": 1000000},
            {"slug": "gemini-4-carbon-medium"}, {"slug": "gemini-4-carbon-ultra"},
            {"slug": "claude-4.6"}, {"slug": "gpt-oss-120b"},
        ]})
        (self.claude_home / "settings.json").write_bytes(b"{unreadable-settings")
        (self.codex / "config.toml").write_bytes(b"model = [invalid")
        detection = {name: {"native_installed": False} for name in ("antigravity", "codex", "claude", "cc_switch")}
        before = self.snapshot()
        with mock.patch.object(installer, "_is_working_python_cmd", side_effect=AssertionError("audit must not probe hook interpreters")):
            report = installer.audit_upgrade_status(self.source, self.plugin, self.codex, self.claude_home,
                                                     self.root / "missing.db", detection=detection, is_sandbox_run=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(report["antigravity"]["carbon_supported_efforts"], ["medium", "high"])
        self.assertNotIn("gemini-4-carbon-ultra", report["antigravity"]["recommended_carbon_variants"])
        self.assertIn("claude-4.6", report["antigravity"]["retiring_models"])
        self.assertIn("gpt-oss-120b", report["antigravity"]["retiring_models"])
        self.assertFalse(report["claude"]["hooks_aligned"])
        self.assertIn("repair_unreadable_configs", report["summary"]["recommended_actions"])
        self.assertNotIn(str(self.home), json.dumps(report))
        self.assertTrue(all(not key.startswith(str(self.codex)) for key in report["read_errors"]))

    def test_audit_codex_hook_trust_and_agy_retiring_models(self):
        self.install(*self.all_args())
        gemini = self.plugin.parent.parent.parent
        user_words.atomic_write_json(gemini / "antigravity-cli/cache/cae_models_cache.json", {"models": [
            {"slug": "gemini-4-carbon-high"},
            {"slug": "gemini-4-argon-high"},
            {"slug": "gemini-pro-orchestrator-argon"},
            {"slug": "gemini-3.8-flash-low"},
            {"slug": "claude-sonnet-4-6-high"},
            {"slug": "claude-opus-4-6-medium"},
            {"slug": "gpt-oss-120b-medium"},
        ]})
        user_words.atomic_write_json(self.codex / "models_cache.json", {"models": [
            {"slug": "gpt-6.2-sol", "max_context_window": 872000, "supported_efforts": ["low", "medium", "high", "xhigh", "max"]},
        ]})
        cfg_path = self.codex / "config.toml"
        base_cfg = cfg_path.read_text(encoding="utf-8")
        detection_v171 = {
            "antigravity": {"native_installed": False},
            "codex": {"native_installed": True, "version": "0.171.0-alpha.13.2"},
            "claude": {"native_installed": False},
            "cc_switch": {"native_installed": False},
        }
        # 1) 沙箱无 [hooks.state] 不误报 review_codex_hook_trust，但准确识别退役模型
        rep_sbx = installer.audit_upgrade_status(
            self.source, self.plugin, self.codex, self.claude_home,
            self.root / "missing.db", detection=detection_v171, is_sandbox_run=True,
        )
        self.assertEqual(rep_sbx["codex"]["hooks_trust"]["status"], "unrecorded")
        self.assertEqual(rep_sbx["codex"]["hooks_trust"]["managed_count"], 5)
        self.assertEqual(rep_sbx["codex"]["hooks_trust"]["trusted_count"], 0)
        self.assertNotIn("review_codex_hook_trust", rep_sbx["summary"]["recommended_actions"])
        self.assertIn("gemini-4-argon-high", rep_sbx["antigravity"]["retiring_models"])
        self.assertIn("gemini-pro-orchestrator-argon", rep_sbx["antigravity"]["retiring_models"])
        self.assertIn("gemini-3.8-flash-low", rep_sbx["antigravity"]["retiring_models"])
        self.assertIn("claude-sonnet-4-6-high", rep_sbx["antigravity"]["retiring_models"])
        self.assertIn("claude-opus-4-6-medium", rep_sbx["antigravity"]["retiring_models"])
        self.assertIn("gpt-oss-120b-medium", rep_sbx["antigravity"]["retiring_models"])
        self.assertNotIn("gemini-4-carbon-high", rep_sbx["antigravity"]["retiring_models"])

        # 2) 非沙箱且 Codex >= 0.171.0 缺少信任哈希时触发 review_codex_hook_trust，且脱敏不泄露主目录路径
        with mock.patch.object(installer, "_audit_cli", side_effect=lambda info: dict(info)):
            rep_host_missing = installer.audit_upgrade_status(
                self.source, self.plugin, self.codex, self.claude_home,
                self.root / "missing.db", detection=detection_v171, is_sandbox_run=False,
            )
        self.assertEqual(rep_host_missing["codex"]["hooks_trust"]["status"], "untrusted_or_partial")
        self.assertIn("review_codex_hook_trust", rep_host_missing["summary"]["recommended_actions"])
        self.assertTrue(all(item.startswith("<codex>/hooks.json:") for item in rep_host_missing["codex"]["hooks_trust"]["missing_entries"]))
        self.assertNotIn(str(self.home), json.dumps(rep_host_missing))

        # 3) 仅信任部分槽位时在沙箱也报 untrusted_or_partial 与 review_codex_hook_trust
        hooks_path_str = str((self.codex / "hooks.json").resolve())
        all_slots = [
            "session_start:0:0", "post_compact:0:0",
            "user_prompt_submit:0:0", "pre_tool_use:0:0", "stop:0:0",
        ]
        partial_blocks = "\n".join(
            f"\n[hooks.state.'{hooks_path_str}:{slot}']\ntrusted_hash = \"sha256:abc123\"\n"
            for slot in all_slots[:-1]
        )
        cfg_path.write_text(base_cfg + partial_blocks, encoding="utf-8")
        rep_partial = installer.audit_upgrade_status(
            self.source, self.plugin, self.codex, self.claude_home,
            self.root / "missing.db", detection=detection_v171, is_sandbox_run=True,
        )
        self.assertEqual(rep_partial["codex"]["hooks_trust"]["status"], "untrusted_or_partial")
        self.assertEqual(rep_partial["codex"]["hooks_trust"]["trusted_count"], 4)
        self.assertEqual(rep_partial["codex"]["hooks_trust"]["missing_entries"], ["<codex>/hooks.json:stop:0:0"])
        self.assertIn("review_codex_hook_trust", rep_partial["summary"]["recommended_actions"])

        # 4) 5 条受管单入口钩子全部记录且 hooks.json 无漂移时 status == slots_recorded（诚实标明 hash_verified=False）
        full_blocks = "\n".join(
            f"\n[hooks.state.'{hooks_path_str}:{slot}']\ntrusted_hash = \"sha256:abc123\"\n"
            for slot in all_slots
        )
        cfg_path.write_text(base_cfg + full_blocks, encoding="utf-8")
        rep_trusted = installer.audit_upgrade_status(
            self.source, self.plugin, self.codex, self.claude_home,
            self.root / "missing.db", detection=detection_v171, is_sandbox_run=True,
        )
        self.assertEqual(rep_trusted["codex"]["hooks_trust"]["status"], "slots_recorded")
        self.assertFalse(rep_trusted["codex"]["hooks_trust"]["hash_verified"])
        self.assertEqual(rep_trusted["codex"]["hooks_trust"]["trusted_count"], 5)
        self.assertEqual(rep_trusted["codex"]["hooks_trust"]["missing_entries"], [])
        self.assertNotIn("review_codex_hook_trust", rep_trusted["summary"]["recommended_actions"])

        # 5) 即使保留了旧 8 槽信任记录，一旦 hooks.json 发生命令漂移，仍须退回 untrusted_or_partial 并建议 /hooks 复核
        hooks_json_path = self.codex / "hooks.json"
        drifted_hooks = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        drifted_hooks["hooks"]["Stop"][0]["hooks"][0]["command"] += " --extra-drift"
        hooks_json_path.write_text(json.dumps(drifted_hooks, ensure_ascii=False, indent=2), encoding="utf-8")
        rep_drifted = installer.audit_upgrade_status(
            self.source, self.plugin, self.codex, self.claude_home,
            self.root / "missing.db", detection=detection_v171, is_sandbox_run=True,
        )
        self.assertFalse(rep_drifted["codex"]["hooks_aligned"])
        self.assertEqual(rep_drifted["codex"]["hooks_trust"]["status"], "untrusted_or_partial")
        self.assertIn("review_codex_hook_trust", rep_drifted["summary"]["recommended_actions"])

    def test_audit_cli_cc_switch_timeout_cleans_isolated_tempdir(self):
        """cc-switch --version 探测超时时应终止进程（POSIX 下终止整组）并彻底清理 cc-switch-audit-* 临时目录。"""
        tmp_parent = self.root / "audit_tmp"
        tmp_parent.mkdir()
        captured_env = {}

        class _HangingProc:
            def __init__(self, cmd, **kwargs):
                self.pid = 424242
                self.returncode = None
                self.killed = False
                self._calls = 0
                captured_env.update(kwargs.get("env") or {})
                # 模拟子进程在隔离目录内落盘文件
                home_dir = Path(captured_env["HOME"])
                (home_dir / "nested").mkdir(parents=True, exist_ok=True)
                (home_dir / "nested" / "lock").write_text("busy", encoding="utf-8")

            def communicate(self, timeout=None):
                self._calls += 1
                if self._calls == 1:
                    raise subprocess.TimeoutExpired(cmd="cc-switch --version", timeout=timeout)
                return ("", "")

            def kill(self):
                self.killed = True

        killed_groups = []
        orig_mkdtemp = tempfile.mkdtemp

        def _mkdtemp_in_sandbox(*args, **kwargs):
            kwargs["dir"] = str(tmp_parent)
            return orig_mkdtemp(*args, **kwargs)

        with mock.patch.object(installer, "_read_desktop_app_version", return_value=None), \
                mock.patch.object(installer.subprocess, "Popen", side_effect=_HangingProc), \
                mock.patch.object(installer.tempfile, "mkdtemp", side_effect=_mkdtemp_in_sandbox), \
                mock.patch.object(installer.os, "killpg", side_effect=lambda pid, sig: killed_groups.append((pid, sig)), create=True):
            rep = installer._audit_cli({"native_installed": True, "native_binary": "/usr/bin/cc-switch"})

        self.assertIsNone(rep["version"])
        self.assertEqual(list(tmp_parent.iterdir()), [])
        if os.name != "nt":
            self.assertEqual(len(killed_groups), 1)
            self.assertEqual(killed_groups[0][0], 424242)

    def test_audit_cli_cc_switch_prefers_offline_metadata_without_spawning_gui(self):
        """cc-switch 存在离线包元数据时直接零进程返回版本，不拉起 Popen；dpkg 状态文件解析仅认已安装包。"""
        status_file = self.root / "dpkg_status"
        status_file.write_text(
            "Package: cc-switch-old\nStatus: deinstall ok config-files\nVersion: 4.0.10\n\n"
            "Package: cc-switch\nStatus: install ok installed\nVersion: 4.0.42\n\n",
            encoding="utf-8",
        )
        with mock.patch.object(installer.os, "name", "posix"):
            self.assertEqual(installer._read_dpkg_package_version("cc-switch", status_path=status_file), "4.0.42")
            self.assertIsNone(installer._read_dpkg_package_version("cc-switch-old", status_path=status_file))
            self.assertIsNone(installer._read_dpkg_package_version("missing-pkg", status_path=status_file))

        with mock.patch.object(installer, "_read_desktop_app_version", return_value="4.0.42"), \
                mock.patch.object(installer.subprocess, "Popen", side_effect=AssertionError("must not spawn cc-switch GUI")):
            rep = installer._audit_cli({"native_installed": True, "native_binary": "/usr/bin/cc-switch"})
        self.assertEqual(rep["version"], "4.0.42")
        self.assertEqual(rep["desktop_version"], "4.0.42")

    @unittest.skipIf(os.name == "nt", "POSIX 进程组孤儿子进程清理回归仅在类 Unix 环境执行")
    def test_audit_cli_posix_real_orphan_child_killed_and_tempdir_removed(self):
        """POSIX 真实子进程回归：cc-switch 组长先退出但后代继续持有 stdout 管道并写隔离目录时，超时仍可杀清整组并删净临时目录。"""
        tmp_parent = self.root / "posix_orphan_tmp"
        tmp_parent.mkdir()
        pid_file = self.root / "orphan_child.pid"
        fake_bin = self.root / "cc-switch"
        fake_bin.write_text(
            "#!/bin/sh\n"
            f'python3 -c "import os, time, pathlib; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); '
            'pathlib.Path(os.environ[\'HOME\'], \'lock\').write_text(\'x\'); time.sleep(30)" &\n'
            "exit 0\n",
            encoding="utf-8",
        )
        fake_bin.chmod(0o755)
        orig_mkdtemp = tempfile.mkdtemp

        def _mkdtemp_here(*args, **kwargs):
            kwargs["dir"] = str(tmp_parent)
            return orig_mkdtemp(*args, **kwargs)

        with mock.patch.object(installer.tempfile, "mkdtemp", side_effect=_mkdtemp_here):
            rep = installer._audit_cli({"native_installed": True, "native_binary": str(fake_bin)})

        self.assertIsNone(rep["version"])
        self.assertEqual(list(tmp_parent.iterdir()), [])
        self.assertTrue(pid_file.is_file())
        child_pid = int(pid_file.read_text(encoding="utf-8").strip())
        # 给内核极短回收窗口，随后确认孤儿子进程已不存在
        import time as _time
        alive = True
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                alive = False
                break
            except PermissionError:
                break
            _time.sleep(0.05)
        self.assertFalse(alive, f"孤儿子进程 {child_pid} 仍存活")

    def test_status_guard_installer_flags_and_claude_subagent_skips(self):
        project = self.project / "repo"
        project.mkdir()
        base = {"conversationId": "claude-inst-guard", "cwd": str(project), "workspacePaths": [str(project)]}
        safe = (
            "python -B install.py --audit-upgrade",
            "python -B install.py --app claude --claude-home inside --no-zotero",
            "python -B install.py --app all --target-dir inside/p --codex-home inside/cx --claude-home inside/cl --no-zotero",
            "python -B install.py --app claude --claude-home inside --ccswitch-db inside/cc.db --disable-ccswitch-prompts --no-zotero",
        )
        bad = (
            "python -B install.py --app claude",
            "python -B install.py --app all --target-dir inside/p --codex-home inside/cx --no-zotero",
            "python -B install.py --app both --target-dir inside/p --codex-home inside/cx --claude-home inside/cl --no-zotero",
            "python -B install.py --app claude --claude-home inside --ccswitch-db /outside/cc.db --disable-ccswitch-prompts --no-zotero",
            "python -B install.py --claude-home --audit-upgrade",
        )
        for cmd, expected in [(s, "allow") for s in safe] + [(b, "force_ask") for b in bad]:
            with self.subTest(command=cmd):
                res = status_guard.handle_pre_tool_use({**base, "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}}})
                self.assertEqual(res["decision"], expected)

        sub_transcript = self.claude_home / "projects/work/subagents/agent-1.jsonl"
        _append_jsonl(sub_transcript, _user("子代理支线消息"))
        for sub_payload in (
            {**self.payload, "agent_id": "sub_01"},
            {**self.payload, "transcript_path": str(sub_transcript)},
        ):
            self.assertEqual(session_context_loader.handle_claude_session_start({**sub_payload, "source": "compact"}), {})
            self.assertEqual(handoff_gate.handle_claude_handoff({**sub_payload, "hook_event_name": "PostCompact"}, self.state / "handoff", "enforce", 1), {})
            self.assertEqual(reply_repeat_guard.handle_claude_stop_repeat_guard({**sub_payload, "last_assistant_message": "等待中..."}), {})

    def test_unverified_body_preserved_and_compaction_override_edges(self):
        user_words_dir = self.state / "user-words"
        md_path = user_words_dir / f"{self.sid}.md"
        user_words.atomic_write_text(md_path, "# 既有未附清单的重要原话正文\n")
        _append_jsonl(self.transcript, _user("新会话片段"))
        res = user_words.archive_claude_session_words(self.payload, self.state)
        self.assertFalse(res["complete"])
        self.assertEqual(md_path.read_text(encoding="utf-8"), "# 既有未附清单的重要原话正文\n")

        gate_dir = self.state / "handoff-edge"
        handoff_gate.handle_claude_handoff({**self.payload, "hook_event_name": "PostCompact"}, gate_dir, "enforce", 1)
        _append_jsonl(self.transcript, _user("压缩后首条普通消息"))
        handoff_gate.handle_claude_handoff({**self.payload, "hook_event_name": "SessionStart", "source": "compact"}, gate_dir, "enforce", 1)
        gate_state = json.loads((gate_dir / f"{self.sid}_handoff_gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate_state["compactionCount"], 1)

        handoff_gate.handle_claude_handoff({**self.payload, "hook_event_name": "UserPromptSubmit", "prompt": "暂缓交接"}, gate_dir, "enforce", 1)
        for idx in range(3):
            _append_jsonl(self.transcript, {"type": "system", "subtype": "compact_boundary", "uuid": f"cb_{idx}"})
        handoff_gate.handle_claude_handoff({**self.payload, "hook_event_name": "PostCompact"}, gate_dir, "enforce", 1)
        _append_jsonl(self.transcript, _user("继续工作后再次暂缓"))
        handoff_gate.handle_claude_handoff({**self.payload, "hook_event_name": "UserPromptSubmit", "prompt": "暂缓交接"}, gate_dir, "enforce", 1)
        gate_state = json.loads((gate_dir / f"{self.sid}_handoff_gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate_state["phase"], "overridden")

    def test_third_party_wrapper_hook_preserved_and_single_client_audit_aligned(self):
        self.claude_home.mkdir(parents=True)
        wrapper = f'python3 /opt/custom/wrapper.py "{(self.claude_home / "hooks/status_guard.py").as_posix()}"'
        user_words.atomic_write_json(self.claude_home / "settings.json", {
            "hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": wrapper}]}]},
        })
        self.install("--app", "claude", "--claude-home", self.claude_home)
        merged = json.loads((self.claude_home / "settings.json").read_text(encoding="utf-8"))
        commands = [h["command"] for g in merged["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertIn(wrapper, commands)

        report = json.loads(self.install("--audit-upgrade", "--app", "claude", "--claude-home", self.claude_home).stdout)
        self.assertEqual(report["claude"]["drift_files"], [])
        self.assertTrue(report["summary"]["all_aligned"], report["summary"])

    def test_duplicate_cards_verify_alignment_and_loopback_http_router_relay(self):
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        db = self.database()
        settings = self.claude_home / "settings.json"
        user_words.atomic_write_json(settings, {"hooks": {}, "effortLevel": "xhigh"})
        admin.prepare_cards(db, 8240)
        admin.disable_prompts(db)
        admin.switch_card(db, settings, "Kimi")
        ok, _ = admin.verify_alignment(db, settings)
        self.assertTrue(ok)

        with closing(sqlite3.connect(db)) as con:
            con.execute("UPDATE providers SET is_current = 0 WHERE app_type = 'claude'")
            con.commit()
        ok, status = admin.verify_alignment(db, settings)
        self.assertFalse(ok)
        self.assertTrue(any("no active Claude provider card" in err for err in status["errors"]))

        admin.switch_card(db, settings, "Kimi")
        with closing(sqlite3.connect(db)) as con:
            con.execute(
                "INSERT INTO providers (id, app_type, name, settings_config, is_current, sort_index) VALUES (?,?,?,?,?,?)",
                ("dup_kimi", "claude", "Kimi", json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "tok_dup"}}), 0, 9),
            )
            con.commit()
        router._key_cache.clear()
        with self.assertRaisesRegex(RuntimeError, "multiple cc-switch cards"):
            router.card_key({"ccswitch_db": str(db)}, "Kimi")
        with closing(sqlite3.connect(db)) as con:
            con.execute("DELETE FROM providers WHERE id = 'dup_kimi'")
            ds_cfg = json.loads(con.execute("SELECT settings_config FROM providers WHERE app_type='claude' AND name='DeepSeek'").fetchone()[0])
            ds_cfg["env"]["ANTHROPIC_AUTH_TOKEN"] = "tok_ds_loopback"
            con.execute("UPDATE providers SET settings_config = ? WHERE app_type='claude' AND name='DeepSeek'", (json.dumps(ds_cfg),))
            con.commit()
        router._key_cache.clear()

        received = {}

        class UpstreamStub(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                received["path"] = self.path
                received["auth"] = self.headers.get("Authorization") or self.headers.get("x-api-key")
                received["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                payload = json.dumps({"id": "msg_stub", "type": "message", "model": received["body"]["model"]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamStub)
        self.addCleanup(upstream.server_close)
        up_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        up_thread.start()
        self.addCleanup(upstream.shutdown)
        up_port = upstream.server_address[1]

        cfg_path = self.root / "router_loopback.json"
        user_words.atomic_write_json(cfg_path, {
            "listen": "127.0.0.1",
            "port": 8240,
            "default_upstream": f"http://127.0.0.1:{up_port}/coding",
            "routes": [{
                "prefix": "deepseek",
                "upstream": f"http://127.0.0.1:{up_port}/anthropic",
                "key_from_card": "DeepSeek",
                "force_effort": "max",
            }],
            "kimi_effort_map": {"xhigh": "max"},
            "ccswitch_db": str(db),
            "log": str(self.root / "router.log"),
            "timeout_s": 10,
            "default_key_from_card": "Kimi",
            "follow_windows_proxy": False,
        })
        cfg = router.load_config(str(cfg_path))
        relay = ThreadingHTTPServer(("127.0.0.1", 0), router.Handler)
        relay.daemon_threads = True
        relay.cfg = cfg
        self.addCleanup(relay.server_close)
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        self.addCleanup(relay.shutdown)
        relay_port = relay.server_address[1]

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{relay_port}/__router/health", timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(health["ok"])

        req = urllib.request.Request(
            f"http://127.0.0.1:{relay_port}/v1/messages",
            data=json.dumps({"model": "deepseek-flash[1M]", "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-api-key": "via-router"},
            method="POST",
        )
        with opener.open(req, timeout=5) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(out["model"], "deepseek-flash")
        self.assertEqual(received["path"], "/anthropic/v1/messages")
        self.assertEqual(received["body"]["output_config"]["effort"], "max")

    def test_fleet_evolve_four_peer_sync_glob_guard_and_card_name_redaction(self):
        dsh = self.root / "target/dsh"
        db = self.database()
        self.install(*self.all_args(), "--dsh-home", dsh)
        expected_harness = (REPO_ROOT / "template/user-guard/skills/cae-harness/SKILL.md").read_bytes()
        peer_roots = {
            "antigravity": self.plugin,
            "codex": self.codex,
            "claude": self.claude_home,
            "dsh": dsh,
        }
        # v3.0 D5: 上游专属 fleet-evolve 不再分发至宿主机目标目录；残留旧目录会被审计标出并在重装时自动清理
        for root in peer_roots.values():
            self.assertFalse((root / "skills/fleet-evolve").exists())
            self.assertEqual((root / "skills/cae-harness/SKILL.md").read_bytes(), expected_harness)
            stale_fe = root / "skills/fleet-evolve/SKILL.md"
            stale_fe.parent.mkdir(parents=True, exist_ok=True)
            stale_fe.write_text("# legacy fleet-evolve\n", encoding="utf-8")

        admin.prepare_cards(db, admin.default_router_port())
        with closing(sqlite3.connect(db)) as con:
            con.execute(
                "INSERT INTO providers (id, app_type, name, settings_config, is_current, sort_index) VALUES (?,?,?,?,?,?)",
                ("p_prefix", "kimi", "bob", "{}", 0, 8),
            )
            con.execute(
                "INSERT INTO providers (id, app_type, name, settings_config, is_current, sort_index) VALUES (?,?,?,?,?,?)",
                ("p_custom", "kimi", "alice_private_account_label", "{}", 0, 9),
            )
            con.execute(
                "INSERT INTO providers (id, app_type, name, settings_config, is_current, sort_index) VALUES (?,?,?,?,?,?)",
                ("p_broken", "kimi", "bob_broken_secret_card", "{bad json", 0, 10),
            )
            con.commit()
        report = installer.audit_upgrade_status(
            REPO_ROOT, self.plugin, self.codex, self.claude_home, db,
            detection={}, is_sandbox_run=True, app_scope="all", dsh_home=dsh,
        )
        for name in peer_roots:
            self.assertTrue(
                any(item.startswith("skills/fleet-evolve") for item in report[name]["drift_files"]),
                (name, report[name]["drift_files"]),
            )
        # 重新安装自动清理残留的宿主机 fleet-evolve 目录
        self.install(*self.all_args(), "--dsh-home", dsh)
        for root in peer_roots.values():
            self.assertFalse((root / "skills/fleet-evolve").exists())
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("alice_private_account_label", serialized)
        self.assertNotIn("bob_broken_secret_card", serialized)
        self.assertNotIn("_broken_secret_card", serialized)
        self.assertIn("kimi:<custom-card>", serialized)

        self.assertTrue(status_guard._rg_glob_matches(".solver.log", ".solver.log", ".solver.log"))
        self.assertFalse(status_guard._rg_glob_matches("solver.log", "solver.log", ".solver.log"))
        self.assertTrue(status_guard._rg_glob_matches("src/solver.log", "solver.log", "./src/**/*.log"))
        self.assertTrue(status_guard._rg_glob_matches("src/solver.log", "solver.log", "src/**/**/*.log"))
        self.assertTrue(status_guard._rg_glob_matches("src/a/b/solver.log", "solver.log", "src/**/*.log"))
        self.assertTrue(status_guard._rg_glob_matches("].log", "].log", "[]].log"))
        self.assertFalse(status_guard._rg_glob_matches("].log", "].log", "[!]].log"))
        self.assertFalse(status_guard._rg_glob_matches("src/a/b.log", "b.log", "src/*[!]]*.log"))
        self.assertTrue(status_guard._rg_glob_matches("src/solver.log", "solver.log", "src/*.log"))
        self.assertFalse(status_guard._rg_glob_matches("src/sub/solver.log", "solver.log", "src/*.log"))

        absent_dsh = self.root / "target/absent_dsh"
        scoped = installer.audit_upgrade_status(
            REPO_ROOT, self.plugin, self.codex, self.claude_home, db,
            detection={"dsh": {"native_installed": False}}, is_sandbox_run=True,
            app_scope="claude", dsh_home=absent_dsh,
        )
        self.assertFalse(scoped["dsh"]["active"])
        self.assertEqual(scoped["dsh"]["drift_files"], [])

        work = self.project / "grep_case"
        (work / ".git").mkdir(parents=True)
        (work / ".git/git_internal.log").write_text("ignore git\n", encoding="utf-8")
        (work / "solver.log").write_text("iter=1 residual=1e-3\n", encoding="utf-8")
        state_dir, logs_dir = self.root / "sg_state", self.root / "sg_logs"
        py_glob = status_guard.handle_claude_pre_tool_use({
            "session_id": "sid_glob", "cwd": str(self.project), "tool_name": "Grep",
            "tool_input": {"path": str(work), "pattern": "residual", "glob": "**.py"},
        }, state_dir=state_dir, logs_dir=logs_dir)
        self.assertEqual(py_glob, {})
        first_log = status_guard.handle_claude_pre_tool_use({
            "session_id": "sid_glob", "cwd": str(self.project), "tool_name": "Grep",
            "tool_input": {"path": str(work), "pattern": "residual", "glob": "*.{log,out}"},
        }, state_dir=state_dir, logs_dir=logs_dir)
        self.assertEqual(first_log, {})
        second_log = status_guard.handle_claude_pre_tool_use({
            "session_id": "sid_glob", "cwd": str(self.project), "tool_name": "Grep",
            "tool_input": {"path": str(work), "pattern": "residual", "glob": "*.{log,out}"},
        }, state_dir=state_dir, logs_dir=logs_dir)
        self.assertEqual(second_log["hookSpecificOutput"]["permissionDecision"], "deny")

        # 验证 host.toolchain 三级耦合诊断、router 请求体/并发上限与损坏激活卡片识别
        self.assertIn("toolchain", report["host"])
        self.assertIn("python_minor", report["host"]["toolchain"])
        with mock.patch.object(installer.sys, "prefix", "/global_py"), \
                mock.patch.object(installer.sys, "base_prefix", "/global_py"), \
                mock.patch.object(
                    installer.importlib.metadata,
                    "distributions",
                    return_value=[SimpleNamespace(metadata={"Name": "jax"}, version="0.7.0")],
                ):
            tc = installer._audit_host_toolchain(
                is_sandbox_run=False, detection={"wsl_probe": {"installed_distros": ["Ubuntu-24.04"]}},
            )
        self.assertFalse(tc["python_isolated_env"])
        self.assertEqual(tc["compiled_scientific_packages"], ["jax==0.7.0"])
        self.assertTrue(any("jax==0.7.0" in w for w in tc["coupling_warnings"]))

        def make_router_handler(body_bytes: bytes, max_body: int = 64 * 1024 * 1024):
            h = object.__new__(router.Handler)
            h.server = SimpleNamespace(cfg={**copy.deepcopy(router.DEFAULTS), "max_body_bytes": max_body})
            h.path, h.command, h.headers = "/v1/messages", "POST", Message()
            h.headers["Content-Length"] = str(len(body_bytes))
            h.rfile, h.wfile = io.BytesIO(body_bytes), io.BytesIO()
            h.send_error = mock.Mock()
            return h

        oversize_h = make_router_handler(b"x" * 64, max_body=8)
        oversize_h.do_POST()
        self.assertEqual(oversize_h.send_error.call_args.args[0], 413)

        busy_h = make_router_handler(b"{}")
        with mock.patch.object(router, "_INFLIGHT_SEM", SimpleNamespace(acquire=lambda blocking=False: False, release=lambda: None)):
            busy_h.do_POST()
        self.assertEqual(busy_h.send_error.call_args.args[0], 503)

        with closing(sqlite3.connect(db)) as con:
            con.execute("UPDATE providers SET is_current = 0")
            con.execute(
                "INSERT INTO providers (id, app_type, name, settings_config, is_current, sort_index) VALUES (?,?,?,?,?,?)",
                ("p_claude_broken", "claude", "Kimi", "{bad json", 1, 11),
            )
            con.commit()
        broken_status = admin.inspect_status(db, self.claude_home / "settings.json")
        self.assertFalse(broken_status["settings_aligned"])

    def test_runtime_redaction_hardening_router_case_fold_and_corrupt_settings(self):
        # 1) router.py 前缀匹配不区分大小写
        case_body = json.dumps({"model": "DeepSeek-Flash", "messages": [{"role": "user", "content": "hi"}]}).encode("utf-8")
        h_case = object.__new__(router.Handler)
        cfg_case = copy.deepcopy(router.DEFAULTS)
        cfg_case["routes"][0]["prefix"] = "DeepSeek"
        h_case.server = SimpleNamespace(cfg=cfg_case)
        h_case.path, h_case.command, h_case.headers = "/v1/messages", "POST", Message()
        h_case.headers["Content-Type"] = "application/json"
        h_case.send_error = mock.Mock()
        seen_cards: list[str] = []

        def fake_card_key(_cfg, card_name):
            seen_cards.append(card_name)
            raise RuntimeError("stop after route match")

        with mock.patch.object(router, "card_key", side_effect=fake_card_key), \
                mock.patch.object(router, "log_line"):
            h_case._forward_locked(case_body)
        self.assertEqual(seen_cards, ["DeepSeek"])

        # 2) ccswitch_admin.py URL 凭据/查询参数脱敏及损坏 settings.json 容错
        db = self.database()
        admin.prepare_cards(db, 8240)
        secret_url = "https://user:" + "sec_tok@example.com/v1?api_key=" + "z" * 24 + "#frag"
        self.replace_card(db, "Kimi", {"env": {"ANTHROPIC_BASE_URL": secret_url, "ANTHROPIC_AUTH_TOKEN": "tok_k"}})
        settings_path = self.claude_home / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{corrupt settings json", encoding="utf-8")
        st = admin.inspect_status(db, settings_path)
        self.assertFalse(st["settings_aligned"])
        st_serialized = json.dumps(st, ensure_ascii=False)
        self.assertNotIn("sec_tok", st_serialized)
        self.assertNotIn("api_key=", st_serialized)
        self.assertIn("https://example.com/v1", st_serialized)

        # 3) install.py _audit_redact 与 audit_upgrade_status 驼峰令牌、短布尔防误伤、WSL 路径及自定义提示词/技能名脱敏
        fake_ghp = "ghp_" + "z" * 32
        fake_aiza = "AIza" + "z" * 34
        fake_wsl_path = "/mnt/c/" + "Users/private" + "person/project"
        camel_secret = "camel_secret_" + "z" * 16
        redacted_sample = installer._audit_redact(
            {"msg": f"{fake_wsl_path} {fake_ghp} {fake_aiza} {camel_secret}", "count": 1},
            [],
            {camel_secret},
        )
        self.assertEqual(redacted_sample["count"], 1)
        for leaked in ("private" + "person", fake_ghp, fake_aiza, camel_secret):
            self.assertNotIn(leaked, json.dumps(redacted_sample))

        user_words.atomic_write_json(
            settings_path,
            {
                "effortLevel": "xhigh",
                "accessToken": camel_secret,
                "env": {"CLAUDE_CODE_SKIP_BEDROCK_AUTH": "1"},
            },
        )
        user_words.atomic_write_json(self.claude_home / "router/router.json", {"port": 8255})
        priv_dir = db.parent / "skills/private_topic_skill_dir"
        priv_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db)) as con:
            con.execute("INSERT INTO prompts VALUES (?,?,?,?,?)", ("p_priv", "claude", "private_prompt_title", 1, "secret rule"))
            con.execute("CREATE TABLE IF NOT EXISTS skills (id TEXT, name TEXT, enabled_claude INT)")
            con.execute("INSERT INTO skills VALUES (?,?,?)", ("s_priv", "private_skill_title", 1))
            con.commit()
        audit_rep = installer.audit_upgrade_status(
            REPO_ROOT, self.plugin, self.codex, self.claude_home, db,
            detection={}, is_sandbox_run=True, app_scope="claude",
        )
        rep_text = json.dumps(audit_rep, ensure_ascii=False)
        for private_token in ("private_prompt_title", "private_skill_title", "private_topic_skill_dir", camel_secret, str(db.parent)):
            self.assertNotIn(private_token, rep_text)
        self.assertEqual(audit_rep["cc_switch"]["enabled_prompt_count"] if "enabled_prompt_count" in audit_rep["cc_switch"] else len(audit_rep["cc_switch"]["enabled_prompts"]), 1)
        self.assertIn("<custom-prompt>", rep_text)
        self.assertIn("<custom-skill>", rep_text)
        self.assertIn("<custom-skill-dir>", rep_text)

    def test_claude_session_start_recovers_recent_agent_returns_and_sidechain_log_paths(self):
        sid = "claude-subagent-session-01"
        proj_dir = self.claude_home / "projects" / "cfd-solver"
        transcript = proj_dir / f"{sid}.jsonl"
        sidechain_log = proj_dir / sid / "subagents" / "agent-a1b2c3d4.jsonl"
        _append_jsonl(sidechain_log, {"type": "assistant", "isSidechain": True, "message": {"content": []}})

        _append_jsonl(transcript, _user("请核对 HLLC 通量与边界反射实现", timestamp="2026-11-15T02:00:00Z"))
        _append_jsonl(transcript, {
            "type": "assistant", "isSidechain": False, "timestamp": "2026-11-15T02:01:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu-ok", "name": "Agent",
                     "input": {"subagent_type": "readonly-auditor", "description": "Audit HLLC"}},
                    {"type": "tool_use", "id": "tu-async", "name": "Task",
                     "input": {"subagent_type": "readonly-reader", "description": "Async launch"}},
                    {"type": "tool_use", "id": "tu-err", "name": "Agent",
                     "input": {"subagent_type": "readonly-auditor", "description": "Failed run"}},
                ],
            },
        })
        # 1) 异步启动回执不应作为完成结果归档
        _append_jsonl(transcript, {
            "type": "user", "isSidechain": False, "timestamp": "2026-11-15T02:01:05Z",
            "toolUseResult": {"status": "async_launched", "agentId": "bg9999"},
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu-async", "content": "Async agent launched"},
            ]},
        })
        # 2) 报错回执不应归档
        _append_jsonl(transcript, {
            "type": "user", "isSidechain": False, "timestamp": "2026-11-15T02:01:10Z",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu-err", "is_error": True, "content": "Subagent timed out"},
            ]},
        })
        # 3) 正常完成回执应提取首尾摘要并关联 subagents/agent-<id>.jsonl
        _append_jsonl(transcript, {
            "type": "user", "isSidechain": False, "timestamp": "2026-11-15T02:02:00Z",
            "toolUseResult": {"status": "completed", "agentId": "a1b2c3d4"},
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu-ok",
                 "content": [{"type": "text", "text": "核对完毕：src/flux/hllc.py 第 52 行接触波速与 Toro 公式一致。"}]},
            ]},
        })

        state_dir = self.claude_home / "hooks" / "state" / "session-context"
        res = session_context_loader.handle_claude_session_start(
            {"session_id": sid, "transcript_path": str(transcript), "source": "compact", "cwd": str(self.root)},
            state_dir_str=str(state_dir),
        )
        ctx = res["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), session_context_loader.MAX_INLINE_TOTAL_CHARS)
        self.assertIn("最近子代理返回摘要：1 条", ctx)
        self.assertIn("readonly-auditor (Audit HLLC)", ctx)
        self.assertIn("Toro 公式一致", ctx)
        self.assertIn(str(sidechain_log), ctx)
        self.assertNotIn("Async agent launched", ctx)
        self.assertNotIn("Subagent timed out", ctx)

        md_file = self.claude_home / "hooks" / "state" / "user-words" / f"{sid}.md"
        meta_file = self.claude_home / "hooks" / "state" / "user-words" / f"{sid}.meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        self.assertEqual(meta["sha256"], hashlib.sha256(md_file.read_bytes()).hexdigest())
        self.assertEqual(meta["count"], 1)

    def test_direct_card_selected_never_triggers_false_router_start_action(self):
        # 激活直连卡片（如 DeepSeek）且存在空白 ANTHROPIC_AUTH_TOKEN 回退 ANTHROPIC_API_KEY 时，
        # 非沙箱态 --audit-upgrade --with-cc-router 不探测本地路由端口，也不误报 start_cc_router。
        db = self.database()
        self.install("--app", "claude", "--claude-home", self.claude_home, "--with-cc-router",
                     "--ccswitch-db", db, "--disable-ccswitch-prompts")
        admin.prepare_cards(db, 8240)
        self.replace_card(db, "DeepSeek", {
            "env": {
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "   ",
                "ANTHROPIC_API_KEY": "tok_ds_fallback_valid",
                "ANTHROPIC_MODEL": "deepseek-reasoner",
            }
        })
        admin.prepare_cards(db, 8240)
        admin.switch_card(db, self.claude_home / "settings.json", "DeepSeek")
        detection = {
            "antigravity": {"native_installed": False},
            "codex": {"native_installed": False},
            "claude": {"native_installed": True},
            "dsh": {"native_installed": False},
            "cc_switch": {"native_installed": True},
        }
        loaded_admin = installer._load_ccswitch_admin(REPO_ROOT)
        with mock.patch.object(installer, "_load_ccswitch_admin", return_value=loaded_admin), \
                mock.patch.object(loaded_admin.urllib.request, "urlopen") as probe_http:
            report = installer.audit_upgrade_status(
                REPO_ROOT, self.plugin, self.codex, self.claude_home, db,
                detection=detection, expect_cc_router=True, is_sandbox_run=False, app_scope="claude",
            )
        probe_http.assert_not_called()
        self.assertNotIn("start_cc_router", report["summary"]["recommended_actions"])
        self.assertTrue(report["summary"]["all_aligned"], report["summary"])


class TestFleetStateWP3A(OfflineSandbox):
    def setUp(self):
        super().setUp()
        self.repo = self.project / "upstream"
        self.repo.mkdir()
        (self.repo / "install.py").write_text("", encoding="utf-8")
        user_words.atomic_write_json(self.repo / "template/user-guard/plugin.json", {"version": "fixture"})
        self.profile = self.repo / "template/user-guard/skills/cae-harness/fleet_profile.json"
        user_words.atomic_write_json(self.profile, {"runtime_tunables": {"headless_dispatch": {"fleet_state_ttl_sec": 321}}})
        patch = mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(self.root / "fleet-state")})
        patch.start()
        self.addCleanup(patch.stop)
        self.state_file = fleet_state._validated_state_file(self.repo)

    def run_state(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = fleet_state.main(["--repo", str(self.repo), *args])
        return code, json.loads(out.getvalue())

    def test_grounding_missing_spec_loader_and_execution_fail_closed(self):
        result = fleet_state._run_repo_grounding_gate(self.repo)
        self.assertIs(result["ok"], False)
        self.assertIn("missing", result["error"])
        harness = self.profile.parent / "scripts/cae_harness.py"
        harness.parent.mkdir()
        harness.write_text("raise RuntimeError('fixture import failure')\n", encoding="utf-8")
        for spec in (None, SimpleNamespace(loader=None)):
            with mock.patch.object(importlib.util, "spec_from_file_location", return_value=spec):
                result = fleet_state._run_repo_grounding_gate(self.repo)
            self.assertIs(result["ok"], False)
            self.assertIn("ImportError", result["error"])
        result = fleet_state._run_repo_grounding_gate(self.repo)
        self.assertIs(result["ok"], False)
        self.assertIn("fixture import failure", result["error"])
        harness.write_text("def audit_doc_grounding(*args):\n    raise RuntimeError('fixture audit failure')\n", encoding="utf-8")
        result = fleet_state._run_repo_grounding_gate(self.repo)
        self.assertIs(result["ok"], False)
        self.assertIn("fixture audit failure", result["error"])
        harness.write_text("def audit_doc_grounding(*args):\n    return {}\n", encoding="utf-8")
        self.assertIs(fleet_state._run_repo_grounding_gate(self.repo)["ok"], False)

    def test_verified_clear_preserves_active_proposal_unless_forced(self):
        code, _ = self.run_state("propose", "--session-id", "fixture", "--summary", "fixture approval")
        self.assertEqual(code, 0)
        original = self.state_file.read_bytes()
        for extra in ([], ["--readback-done"]):
            with mock.patch.object(fleet_state, "_run_repo_grounding_gate") as grounding:
                code, result = self.run_state("clear", "--verified", *extra)
            self.assertEqual(code, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "active_user_approval")
            self.assertEqual(self.state_file.read_bytes(), original)
            grounding.assert_not_called()
        code, result = self.run_state("clear", "--verified", "--force")
        self.assertEqual(code, 0)
        self.assertTrue(result["verified"])
        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8"))["phase"], "IDLE")

    def test_record_pass1_preserves_active_proposal_unless_forced_or_expired(self):
        self.run_state("propose", "--session-id", "fixture", "--summary", "fixture approval")
        original = self.state_file.read_bytes()
        code, result = self.run_state("record-pass1", "--session-id", "fixture", "--target-master", "codex")
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "active_user_approval")
        self.assertEqual(self.state_file.read_bytes(), original)
        code, result = self.run_state("record-pass1", "--session-id", "fixture", "--target-master", "codex", "--force")
        self.assertEqual(code, 0)
        self.assertEqual(result["state"]["phase"], "AWAITING_MASTER_RELOAD")
        self.state_file.unlink()
        self.run_state("propose", "--session-id", "fixture", "--summary", "fixture approval")
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        saved["updated_at_epoch"] = 1
        user_words.atomic_write_json(self.state_file, saved)
        code, result = self.run_state("record-pass1", "--session-id", "fixture", "--target-master", "codex")
        self.assertEqual(code, 0)

    def test_clear_grounding_runs_under_lock_and_failed_gate_keeps_state(self):
        from contextlib import contextmanager

        code, _ = self.run_state("propose", "--session-id", "fixture", "--summary", "fixture approval")
        self.assertEqual(code, 0)
        original = self.state_file.read_bytes()
        lock_held = False
        actual_lock = fleet_state._state_lock

        @contextmanager
        def tracked_lock(path):
            nonlocal lock_held
            with actual_lock(path):
                lock_held = True
                try:
                    yield
                finally:
                    lock_held = False

        def gate(repo):
            self.assertTrue(lock_held)
            self.assertEqual(repo, self.repo)
            return {"ok": False, "error": "fixture grounding failure"}

        with mock.patch.object(fleet_state, "_state_lock", tracked_lock), \
                mock.patch.object(fleet_state, "_run_repo_grounding_gate", side_effect=gate):
            code, result = self.run_state("clear", "--verified", "--readback-done", "--force")
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "grounding_check_failed")
        self.assertEqual(result["error"], "fixture grounding failure")
        self.assertEqual(self.state_file.read_bytes(), original)
        with mock.patch.object(fleet_state, "_run_repo_grounding_gate", return_value={"ok": True}):
            code, result = self.run_state("clear", "--verified", "--readback-done", "--force")
        self.assertEqual(code, 0)
        self.assertTrue(result["readback_verified"])

    def test_expired_approval_does_not_block_verified_clear(self):
        self.run_state("propose", "--session-id", "fixture", "--summary", "fixture approval")
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        saved["updated_at_epoch"] = 1
        user_words.atomic_write_json(self.state_file, saved)
        code, result = self.run_state("clear", "--verified")
        self.assertEqual(code, 0)
        self.assertTrue(result["verified"])

    def test_ttl_uses_resolved_target_profile_and_explicit_override(self):
        for command, extra in (("propose", ["--summary", "fixture"]), ("record-pass1", ["--target-master", "codex"])):
            for override, expected in (([], 321), (["--ttl", "654"], 654)):
                with self.subTest(command=command, override=override):
                    self.state_file.unlink(missing_ok=True)
                    code, result = self.run_state(command, "--session-id", "fixture", *extra, *override)
                    self.assertEqual(code, 0)
                    self.assertEqual(result["state"]["ttl_seconds"], expected)

    def test_invalid_parameter_values_return_json_without_traceback(self):
        cases = [
            ["propose", "--session-id", " ", "--summary", "fixture"],
            ["propose", "--session-id", "fixture", "--summary", " "],
            ["propose", "--session-id", "fixture", "--summary", "fixture", "--ttl", "0"],
            ["approve", "--session-id", " "],
            ["record-pass1", "--session-id", " ", "--target-master", "codex"],
            ["record-pass1", "--session-id", "fixture", "--target-master", "codex", "--ttl", "-1"],
        ]
        script = REPO_ROOT / "template/user-guard/skills/fleet-evolve/scripts/fleet_state.py"
        for args in cases:
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(script), "--repo", str(self.repo), *args],
                                        capture_output=True, text=True, encoding="utf-8", cwd=self.project, timeout=20)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["reason"], "invalid_arguments")
                self.assertEqual(result.stderr, "")
        self.assertFalse(self.state_file.exists())

    def test_bare_clear_preserves_active_proposal_and_claimed_reload_unless_forced(self):
        self.run_state("propose", "--session-id", "fixture", "--summary", "fixture approval")
        original = self.state_file.read_bytes()
        code, result = self.run_state("clear")
        self.assertEqual(code, 1)
        self.assertEqual(result["reason"], "active_user_approval")
        self.assertEqual(self.state_file.read_bytes(), original)

        self.run_state("record-pass1", "--force", "--session-id", "pass1", "--target-master", "codex")
        self.run_state("verify-pass2", "--session-id", "pass2")
        claimed = self.state_file.read_bytes()
        code2, result2 = self.run_state("clear")
        self.assertEqual(code2, 1)
        self.assertEqual(result2["reason"], "active_pass2_claimed")
        self.assertEqual(self.state_file.read_bytes(), claimed)

        code3, _ = self.run_state("clear", "--force")
        self.assertEqual(code3, 0)
        self.assertFalse(self.state_file.exists())

    def test_v1152_audit_upgrade_filters_blocked_and_light_agy_models(self):
        mixed_models = [
            {"slug": "gpt-5.4-pro", "display_name": "GPT-5.4 Pro"},
            {"slug": "prompt-router", "display_name": "Prompt Router"},
            {"slug": "gemini-3.2-pro-lite", "display_name": "Gemini 3.2 Pro Lite"},
            {"slug": "gemini-3.1-pro-high", "display_name": "Gemini 3.1 Pro (High)"},
            {"slug": "gemini-3.1-pro-low", "display_name": "Gemini 3.1 Pro (Low)"},
        ]
        with tempfile.TemporaryDirectory() as td:
            td_root = Path(td).resolve()
            ag_plugin = td_root / "gemini/config/plugins/user-guard"
            ag_cache = td_root / "gemini/antigravity-cli/cache/cae_models_cache.json"
            user_words.atomic_write_json(ag_cache, {"models": mixed_models})
            rep = installer.audit_upgrade_status(
                REPO_ROOT, ag_plugin, td_root / "cx", td_root / "cl",
                detection={}, is_sandbox_run=True, app_scope="antigravity",
            )
        self.assertEqual(rep["antigravity"]["recommended_primary_variants"], ["gemini-3.1-pro-high", "gemini-3.1-pro-low"])
        self.assertEqual(installer._select_agy_frontier_max_label(mixed_models, REPO_ROOT), "Gemini 3.1 Pro (High)")

    def test_v1152_loopback_health_proxy_bypass_and_router_ctype_log_scrub(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        harness_mod = _load_module("cae_harness_proxy_test", REPO_ROOT / "template/user-guard/skills/cae-harness/scripts/cae_harness.py")
        class OkHealth(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                b = b'{"ok": true, "port": 18241}'
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        class HijackProxy(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self): self.send_error(502, "hijacked")
        hs = HTTPServer(("127.0.0.1", 0), OkHealth)
        ps = HTTPServer(("127.0.0.1", 0), HijackProxy)
        threading.Thread(target=hs.serve_forever, daemon=True).start()
        threading.Thread(target=ps.serve_forever, daemon=True).start()
        try:
            hp, pp = hs.server_address[1], ps.server_address[1]
            with mock.patch.dict(os.environ, {"HTTP_PROXY": f"http://127.0.0.1:{pp}", "http_proxy": f"http://127.0.0.1:{pp}"}, clear=False):
                os.environ.pop("NO_PROXY", None); os.environ.pop("no_proxy", None)
                st = admin.inspect_status(self.root / "none.db", None, router_port=hp)
                self.assertTrue(st["router_health"]["ok"])
                self.assertTrue(harness_mod._probe_router_health({"base_url": f"http://127.0.0.1:{hp}", "windows_interop": False}))
        finally:
            hs.shutdown(); hs.server_close(); ps.shutdown(); ps.server_close()
        # router.py: uppercase Application/JSON routing + key-aware _scrub_log_value
        tok_val = "router_secret_" + "y" * 16
        scrubbed = router._scrub_log_value({"api_key": tok_val, "err": json.dumps({"auth_token": tok_val}), "ok": "true"})
        self.assertEqual(scrubbed["api_key"], "<redacted>")
        self.assertEqual(json.loads(scrubbed["err"]), {"auth_token": "<redacted>"})
        self.assertEqual(scrubbed["ok"], "true")
        seen_cards = []
        h = router.Handler.__new__(router.Handler)
        h.command = "POST"; h.path = "/v1/messages"; h.headers = {"Content-Type": "Application/JSON; charset=utf-8"}
        h.server = SimpleNamespace(cfg=copy.deepcopy(router.DEFAULTS))
        h.send_error = lambda *a: None
        with mock.patch.object(router, "card_key", side_effect=lambda _c, name: (seen_cards.append(name), (_ for _ in ()).throw(RuntimeError("stop")))[1]), \
             mock.patch.object(router, "log_line"):
            h._forward_locked(json.dumps({"model": "deepseek-chat[1M]"}).encode("utf-8"))
        self.assertEqual(seen_cards, ["DeepSeek"])

    def test_v1152_router_combines_repeated_beta_headers_case_insensitively(self):
        merged = router._merge_request_headers(
            router.end_to_end_headers([
                ("Host", "127.0.0.1:3456"),
                ("anthropic-beta", "interleaved-thinking-2025-05-14"),
                ("Anthropic-Beta", "fine-grained-tool-streaming-2025-05-14"),
                ("x-api-key", "old"),
                ("X-API-Key", "new"),
            ])
        )
        self.assertEqual(
            merged["anthropic-beta"],
            "interleaved-thinking-2025-05-14, fine-grained-tool-streaming-2025-05-14",
        )
        self.assertEqual(merged["x-api-key"], "new")


class TestShard3RouterInstallerFixes(OfflineSandbox):
    def _setup_installer_tree(self):
        source = self.root / "source"
        source.mkdir()
        shutil.copy2(REPO_ROOT / "install.py", source / "install.py")
        shutil.copytree(
            REPO_ROOT / "template",
            source / "template",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        plugin = self.root / "target/.gemini/config/plugins/user-guard"
        codex = self.root / "target/codex"

        def run_install(*args, code=0):
            res = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", str(source / "install.py"), *map(str, args), "--no-zotero", "--skip-test"],
                cwd=source, capture_output=True, text=True, encoding="utf-8", timeout=40,
            )
            self.assertEqual(res.returncode, code, res.stdout + res.stderr)
            return res

        return source, plugin, codex, run_install

    def test_r1_router_truncated_body_400_and_deep_json_recursion_guard(self):
        import threading

        full_body = json.dumps({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "verify truncated body never falls back to default Kimi card"}],
        }).encode("utf-8")
        truncated = full_body[:32]
        self.assertLess(len(truncated), len(full_body))

        h_trunc = object.__new__(router.Handler)
        cfg = copy.deepcopy(router.DEFAULTS)
        cfg.update(ccswitch_db=str(self.root / "cards.db"), log=str(self.logs / "router.log"))
        h_trunc.server = SimpleNamespace(cfg=cfg)
        h_trunc.path = "/v1/messages"
        h_trunc.command = "POST"
        h_trunc.headers = Message()
        h_trunc.headers["Content-Type"] = "application/json"
        h_trunc.headers["Content-Length"] = str(len(full_body))
        h_trunc.rfile = io.BytesIO(truncated)
        h_trunc.wfile = io.BytesIO()
        h_trunc.send_error = mock.Mock()
        with mock.patch.object(router, "card_key") as key_mock, \
                mock.patch.object(router.http.client, "HTTPSConnection") as conn_mock:
            h_trunc.do_POST()
        h_trunc.send_error.assert_called_once_with(400, "truncated request body")
        key_mock.assert_not_called()
        conn_mock.assert_not_called()

        deep_body = b"[" * 2500 + b"]" * 2500
        h_deep = object.__new__(router.Handler)
        h_deep.server = SimpleNamespace(cfg=cfg, inflight_sem=threading.BoundedSemaphore(2))
        h_deep.path = "/v1/messages"
        h_deep.command = "POST"
        h_deep.headers = Message()
        h_deep.headers["Content-Type"] = "application/json"
        h_deep.headers["Content-Length"] = str(len(deep_body))
        h_deep.rfile = io.BytesIO(deep_body)
        h_deep.wfile = io.BytesIO()
        h_deep.send_response = mock.Mock()
        h_deep.send_header = mock.Mock()
        h_deep.end_headers = mock.Mock()
        h_deep.send_error = mock.Mock()
        upstream_resp = mock.Mock(status=400, reason="Bad Request")
        upstream_resp.getheaders.return_value = [("Content-Type", "application/json")]
        upstream_resp.read1.side_effect = [b'{"error":"invalid_json"}', b""]
        upstream_conn = mock.Mock()
        upstream_conn.getresponse.return_value = upstream_resp
        with mock.patch.object(router, "card_key", return_value="tok_kimi_safe"), \
                mock.patch.object(router.http.client, "HTTPSConnection", return_value=upstream_conn):
            h_deep.do_POST()
        self.assertTrue(h_deep.server.inflight_sem.acquire(blocking=False))
        h_deep.server.inflight_sem.release()

    def test_r2_fleet_state_propose_reload_lock_arg_guards_and_dynamic_ttl(self):
        import time

        repo = self.project / "upstream_r2"
        repo.mkdir()
        (repo / "install.py").write_text("", encoding="utf-8")
        user_words.atomic_write_json(repo / "template/user-guard/plugin.json", {"version": "1.15.2"})
        profile = repo / "template/user-guard/skills/cae-harness/fleet_profile.json"
        user_words.atomic_write_json(profile, {"runtime_tunables": {"headless_dispatch": {"fleet_state_ttl_sec": 120}}})
        state_dir = self.root / "fleet-state-r2"

        def run_state(*cli_args):
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(state_dir)}), redirect_stdout(buf):
                rc = fleet_state.main(["--repo", str(repo), *cli_args])
            return rc, json.loads(buf.getvalue())

        # R2-2: verify-pass2 empty sid fails before _state_lock (no state dir or lock created)
        rc, out = run_state("verify-pass2", "--session-id", "   ")
        self.assertEqual(rc, 2)
        self.assertEqual(out["reason"], "invalid_arguments")
        self.assertFalse(state_dir.exists())

        # R2-2: record-pass1 empty target-master fails with invalid_arguments
        rc, out = run_state("record-pass1", "--session-id", "p1", "--target-master", "   ")
        self.assertEqual(rc, 2)
        self.assertEqual(out["reason"], "invalid_arguments")
        self.assertFalse(state_dir.exists())

        # R2-1: propose refuses to clobber unclaimed and claimed AWAITING_MASTER_RELOAD without --force
        rc, _ = run_state("record-pass1", "--session-id", "pass1_s", "--target-master", "codex")
        self.assertEqual(rc, 0)
        with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(state_dir)}):
            sf = fleet_state._validated_state_file(repo)
        unclaimed_bytes = sf.read_bytes()
        rc, out = run_state("propose", "--session-id", "rogue", "--summary", "attempt wipe")
        self.assertEqual(rc, 2)
        self.assertEqual(out["reason"], "active_master_reload")
        self.assertEqual(sf.read_bytes(), unclaimed_bytes)

        rc, _ = run_state("verify-pass2", "--session-id", "pass2_s")
        self.assertEqual(rc, 0)
        claimed_bytes = sf.read_bytes()
        rc, out = run_state("propose", "--session-id", "rogue", "--summary", "attempt wipe claimed")
        self.assertEqual(rc, 2)
        self.assertEqual(out["reason"], "active_pass2_claimed")
        self.assertEqual(sf.read_bytes(), claimed_bytes)

        rc, out = run_state("propose", "--force", "--session-id", "admin_s", "--summary", "authorized override")
        self.assertEqual(rc, 0)
        self.assertEqual(out["state"]["phase"], "AWAITING_USER_APPROVAL")

        # R2-3: approve, record-pass1, verify-pass2 use default_ttl (120s) instead of hardcoded 86400s
        now_ts = int(time.time())
        saved = json.loads(sf.read_text(encoding="utf-8"))
        saved.pop("ttl_seconds", None)
        saved["updated_at_epoch"] = now_ts - 200  # > 120s default_ttl, but < 86400s
        user_words.atomic_write_json(sf, saved)
        rc, out = run_state("approve", "--session-id", "approver")
        self.assertEqual(rc, 2)
        self.assertEqual(out["reason"], "expired_ttl")

        # record-pass1 also treats missing ttl_seconds with age 200s > 120s as expired
        rc, _ = run_state("record-pass1", "--session-id", "p1_new", "--target-master", "claude")
        self.assertEqual(rc, 0)
        reload_saved = json.loads(sf.read_text(encoding="utf-8"))
        reload_saved.pop("ttl_seconds", None)
        reload_saved["updated_at_epoch"] = now_ts - 200
        user_words.atomic_write_json(sf, reload_saved)
        rc, out = run_state("verify-pass2", "--session-id", "p2_new")
        self.assertEqual(rc, 2)
        self.assertEqual(out["reason"], "expired_ttl")

        # Fresh proposal without ttl_seconds inherits default_ttl (120s) upon approve
        rc, _ = run_state("propose", "--force", "--session-id", "prop_ok", "--summary", "fresh")
        self.assertEqual(rc, 0)
        fresh_saved = json.loads(sf.read_text(encoding="utf-8"))
        fresh_saved.pop("ttl_seconds", None)
        fresh_saved["updated_at_epoch"] = now_ts - 30
        user_words.atomic_write_json(sf, fresh_saved)
        rc, out = run_state("approve", "--session-id", "approver_ok")
        self.assertEqual(rc, 0)
        self.assertEqual(out["state"]["ttl_seconds"], 120)

    def test_r3_audit_upgrade_agy_hook_prefix_and_four_client_stale_mirror_files(self):
        source, plugin, codex, run_install = self._setup_installer_tree()
        dsh = self.root / "target/dsh"
        all_args = [
            "--app", "all", "--target-dir", plugin, "--codex-home", codex,
            "--claude-home", self.claude_home, "--dsh-home", dsh, "--with-cc-router",
        ]
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home / ".codex")}):
            run_install(*all_args)
            base_report = installer.audit_upgrade_status(
                source, plugin, codex, self.claude_home,
                self.root / "none.db", detection={}, is_sandbox_run=True, app_scope="all", dsh_home=dsh,
            )
            for peer in ("antigravity", "codex", "claude", "dsh"):
                self.assertEqual(base_report[peer]["drift_files"], [], peer)

            # R3-1: Antigravity hooks.json prefix must have valid Python interpreter + exact HOOK_PYTHON_FLAGS
            ag_hooks_path = plugin / "hooks.json"
            good_ag_bytes = ag_hooks_path.read_bytes()
            tpl_ag_bytes = (source / "template/user-guard/hooks.json").read_bytes()
            for bad_prefix in ("sh -X utf8 -I -B", "python3 -I -B", "python3 -B", "python3 -X utf8 -I -B -u", "py.exe -X utf8 -I -B"):
                ag_hooks_path.write_bytes(installer._agy_hooks_bytes(tpl_ag_bytes, bad_prefix))
                rep = installer.audit_upgrade_status(
                    source, plugin, codex, self.claude_home,
                    self.root / "none.db", detection={}, is_sandbox_run=True, app_scope="antigravity",
                )
                self.assertIn("hooks.json", rep["antigravity"]["drift_files"], bad_prefix)
            with mock.patch.object(installer.shutil, "which", return_value=None):
                for valid_prefix in ("python3.12 -X utf8 -I -B", "py.exe -3 -X utf8 -I -B"):
                    ag_hooks_path.write_bytes(installer._agy_hooks_bytes(tpl_ag_bytes, valid_prefix))
                    rep = installer.audit_upgrade_status(
                        source, plugin, codex, self.claude_home,
                        self.root / "none.db", detection={}, is_sandbox_run=True, app_scope="antigravity",
                    )
                    self.assertNotIn("hooks.json", rep["antigravity"]["drift_files"], valid_prefix)
            ag_hooks_path.write_bytes(good_ag_bytes)

            # R3-2: Stale extra files in plugin dir & 4-client managed skill dirs flagged; runtime state/logs/__pycache__ excluded
            stale_targets = {
                "antigravity": (plugin / "hooks/retired_old.py", "hooks/retired_old.py"),
                "codex": (codex / "skills/cae-harness/scripts/retired_cx.py", "skills/cae-harness/scripts/retired_cx.py"),
                "claude": (self.claude_home / "skills/cae-harness/scripts/retired_cl.py", "skills/cae-harness/scripts/retired_cl.py"),
                "dsh": (dsh / "skills/cae-harness/retired_dsh.md", "skills/cae-harness/retired_dsh.md"),
            }
            for path, _ in stale_targets.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# stale\n", encoding="utf-8")
            (plugin / "skills/cae-harness/stale_ag_skill.txt").write_text("stale\n", encoding="utf-8")
            (plugin / "hooks/state/legacy_stop_gate.json").parent.mkdir(parents=True, exist_ok=True)
            (plugin / "hooks/state/legacy_stop_gate.json").write_text("{}\n", encoding="utf-8")

            excluded_files = [
                plugin / "hooks/state/user-words/sess_keep.md",
                plugin / "hooks/logs/lifecycle.log",
                plugin / "hooks/__pycache__/status_guard.cpython-311.pyc",
                codex / "skills/cae-harness/scripts/__pycache__/cae_harness.cpython-311.pyc",
                self.claude_home / "skills/third-party-custom/SKILL.md",
            ]
            for ex in excluded_files:
                ex.parent.mkdir(parents=True, exist_ok=True)
                ex.write_text("runtime keep\n", encoding="utf-8")

            rep = installer.audit_upgrade_status(
                source, plugin, codex, self.claude_home,
                self.root / "none.db", detection={}, is_sandbox_run=True, app_scope="all", dsh_home=dsh,
            )
            for peer, (_, expected_rel) in stale_targets.items():
                self.assertIn(expected_rel, rep[peer]["drift_files"])
            self.assertIn("skills/cae-harness/stale_ag_skill.txt", rep["antigravity"]["drift_files"])
            self.assertIn("hooks/state/legacy_stop_gate.json", rep["antigravity"]["drift_files"])
            self.assertNotIn("hooks/state/user-words/sess_keep.md", rep["antigravity"]["drift_files"])
            self.assertNotIn("hooks/logs/lifecycle.log", rep["antigravity"]["drift_files"])
            self.assertFalse(any("__pycache__" in item for peer in ("antigravity", "codex", "claude", "dsh") for item in rep[peer]["drift_files"]))
            self.assertNotIn("skills/third-party-custom/SKILL.md", rep["claude"]["drift_files"])

            run_install(*all_args)
            rep_after = installer.audit_upgrade_status(
                source, plugin, codex, self.claude_home,
                self.root / "none.db", detection={}, is_sandbox_run=True, app_scope="all", dsh_home=dsh,
            )
            for peer in ("antigravity", "codex", "claude", "dsh"):
                self.assertEqual(rep_after[peer]["drift_files"], [], peer)
            self.assertTrue((plugin / "hooks/state/user-words/sess_keep.md").is_file())
            self.assertTrue((self.claude_home / "skills/third-party-custom/SKILL.md").is_file())

    def test_r4_ccswitch_admin_symlink_guard_live_state_corrupt_cards_and_cli_json_error(self):
        # R4-1: atomic_write_bytes rejects target symlinks, parent symlinks, and reparse points
        real_target = self.root / "real_victim.json"
        real_target.write_text('{"keep": true}\n', encoding="utf-8")
        sym_file = self.root / "sym_settings.json"
        symlink_ok = True
        try:
            sym_file.symlink_to(real_target)
        except OSError:
            symlink_ok = False
        if symlink_ok:
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.atomic_write_bytes(sym_file, b"{}\n")
            self.assertEqual(real_target.read_text(encoding="utf-8"), '{"keep": true}\n')

            outside_dir = self.root / "outside_dir"
            outside_dir.mkdir()
            sym_dir = self.root / "sym_parent"
            sym_dir.symlink_to(outside_dir, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.atomic_write_bytes(sym_dir / "nested/settings.json", b"{}\n")
            self.assertFalse((outside_dir / "nested").exists())

        plain_target = self.root / "reparse_check.json"
        reparse_stat = SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400)
        with mock.patch.object(Path, "lstat", return_value=reparse_stat):
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.atomic_write_bytes(plain_target, b"{}\n")

        # R4-2: switch_card strict fail-closed validation on live-state.json
        db, settings = self.database(), self.settings()
        admin.prepare_cards(db, 8240)
        live_path = db.with_name("live-state.json")
        for bad_live in (
            "[]",
            '"proxy"',
            "{}",
            '{"apps": []}',
            '{"apps": null}',
            '{"apps": {}}',
            '{"apps": {"claude": "direct"}}',
            '{"apps": {"claude": []}}',
            '{"apps": {"claude": null}}',
        ):
            live_path.write_text(bad_live, encoding="utf-8")
            with self.subTest(bad_live=bad_live), self.assertRaisesRegex(RuntimeError, "direct mode"):
                admin.switch_card(db, settings, "Kimi")
        live_path.write_text('{"apps": {"claude": {"mode": "direct", "attached": false, "pending": null}}}', encoding="utf-8")
        self.assertEqual(admin.switch_card(db, settings, "Kimi")["card"], "Kimi")
        live_path.unlink()

        # R4-3: switch_card & prepare_cards tolerate None / corrupt JSON / non-dict in old card settings_config
        for corrupt_val in (None, "", "broken-json", "[]", '{"env": []}', '{"env": null}'):
            with self.subTest(corrupt_switch=corrupt_val):
                with closing(sqlite3.connect(db)) as con:
                    con.execute("UPDATE providers SET is_current=0 WHERE app_type='claude'")
                    con.execute("UPDATE providers SET is_current=1, settings_config=? WHERE app_type='claude' AND name='Official'", (corrupt_val,))
                    con.commit()
                res = admin.switch_card(db, settings, "DeepSeek")
                self.assertEqual(res["card"], "DeepSeek")
                self.assertTrue(any("invalid settings_config" in w for w in res["warnings"]))
                self.replace_card(db, "Official", {"env": {"ANTHROPIC_AUTH_TOKEN": "tok_official_test"}})

            with self.subTest(corrupt_prepare=corrupt_val):
                with closing(sqlite3.connect(db)) as con:
                    con.execute("UPDATE providers SET settings_config=? WHERE app_type='claude' AND name='Kimi'", (corrupt_val,))
                    con.commit()
                prep = admin.prepare_cards(db, 8240)
                self.assertTrue(any("claude:Kimi" in w for w in prep["warnings"]))
                self.assertIsInstance(self.cards(db)["Kimi"]["env"], dict)

        # R4-4: admin.main() preserves direct exception semantics, while CLI __main__ emits single-line JSON on stderr with exit 1
        missing_db = self.root / "nonexistent-cc.db"
        with self.assertRaises(FileNotFoundError):
            admin.main(["--db", str(missing_db), "switch-card", "Kimi"])

        script = REPO_ROOT / "template/claude/router/ccswitch_admin.py"
        bad_sqlite = self.root / "not_a_sqlite.db"
        bad_sqlite.write_bytes(b"corrupt header bytes that are not sqlite3 format")
        cli_cases = [
            (["--db", str(missing_db), "switch-card", "Kimi"], "FileNotFoundError"),
            (["--db", str(db), "--settings", str(settings), "prepare-cards", "--port", "70000"], "ValueError"),
            (["--db", str(bad_sqlite), "prepare-cards", "--port", "8240"], "DatabaseError"),
        ]
        live_path.write_text("[]", encoding="utf-8")
        cli_cases.append((["--db", str(db), "--settings", str(settings), "switch-card", "Kimi"], "RuntimeError"))
        for cli_args, expected_type in cli_cases:
            with self.subTest(expected_type=expected_type):
                proc = subprocess.run(
                    [sys.executable, "-B", "-X", "utf8", str(script), *cli_args],
                    capture_output=True, text=True, encoding="utf-8", cwd=self.project, timeout=20,
                )
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(proc.stdout, "")
                lines = proc.stderr.splitlines()
                self.assertEqual(len(lines), 1, proc.stderr)
                err_obj = json.loads(lines[0])
                self.assertIs(err_obj["ok"], False)
                self.assertEqual(err_obj["error_type"], expected_type)
        live_path.unlink()

    def test_r5_claude_third_party_skill_symlink_preserved_and_handoff_off_leaves_no_empty_events(self):
        _, _, _, run_install = self._setup_installer_tree()
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home / ".codex")}):
            ext_skill = self.root / "external_superpowers"
            ext_skill.mkdir()
            (ext_skill / "SKILL.md").write_text("# external skill\n", encoding="utf-8")
            skills_parent = self.claude_home / "skills"
            skills_parent.mkdir(parents=True)
            third_party_link = skills_parent / "superpowers"
            symlink_ok = True
            try:
                third_party_link.symlink_to(ext_skill, target_is_directory=True)
            except OSError:
                symlink_ok = False
            dangling_third_party = skills_parent / "dangling-third-party"
            if symlink_ok:
                dangling_third_party.symlink_to(self.root / "missing-target")

            args = ["--app", "claude", "--claude-home", self.claude_home, "--with-cc-router"]
            run_install(*args, "--claude-handoff-mode", "enforce")
            if symlink_ok:
                self.assertTrue(third_party_link.is_symlink())
                self.assertTrue(dangling_third_party.is_symlink())

                # Symlink inside managed router or hooks/state subtree MUST still fail preflight with exit 2
                evil_router_link = self.claude_home / "router/evil_link.py"
                evil_router_link.symlink_to(ext_skill / "SKILL.md")
                run_install(*args, code=2)
                evil_router_link.unlink()

                evil_state_link = self.claude_home / "hooks/state/evil_state_link"
                evil_state_link.parent.mkdir(parents=True, exist_ok=True)
                evil_state_link.symlink_to(ext_skill, target_is_directory=True)
                run_install(*args, code=2)
                evil_state_link.unlink()

            # R5-2: migrating to --claude-handoff-mode off removes empty PostCompact and UserPromptSubmit groups
            settings_path = self.claude_home / "settings.json"
            before_off = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertIn("PostCompact", before_off["hooks"])
            self.assertIn("UserPromptSubmit", before_off["hooks"])

            run_install(*args, "--claude-handoff-mode", "off")
            after_off = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertNotIn("PostCompact", after_off["hooks"])
            self.assertNotIn("UserPromptSubmit", after_off["hooks"])
            for kept_event in ("SessionStart", "PreToolUse", "Stop"):
                self.assertIn(kept_event, after_off["hooks"])
                self.assertTrue(all(group["hooks"] for group in after_off["hooks"][kept_event]))

            audit_rep = json.loads(run_install(*args, "--audit-upgrade").stdout)
            self.assertEqual(audit_rep["claude"]["drift_files"], [])
            self.assertTrue(audit_rep["claude"]["hooks_aligned"])

    def test_r6_router_powershell_safe_cwd_case_insensitive_effort_and_nat_loopback_fallback(self):
        # R6-1: _read_windows_proxy_settings passes an existing safe cwd to powershell.exe
        fake_ps_dir = self.root / "fake_win_sys32"
        fake_ps_dir.mkdir()
        fake_ps = fake_ps_dir / "powershell.exe"
        fake_ps.write_text("", encoding="utf-8")
        with mock.patch.object(router.shutil, "which", return_value=str(fake_ps)), \
                mock.patch.object(router.subprocess, "run", return_value=SimpleNamespace(stdout=b'{"enabled":false,"server":"","pac":"","override":""}')) as run_mock:
            router._read_windows_proxy_settings()
        passed_cwd = run_mock.call_args.kwargs.get("cwd")
        self.assertEqual(passed_cwd, str(fake_ps_dir))
        self.assertTrue(os.path.isdir(passed_cwd))

        # R6-2: kimi_effort_map case-insensitive matching
        for raw_effort in ("XHIGH", "XHigh", "xhigh"):
            h = object.__new__(router.Handler)
            cfg = copy.deepcopy(router.DEFAULTS)
            cfg.update(log=str(self.logs / "router_r6.log"), default_key_from_card="Kimi", kimi_effort_map={"xhigh": "max"})
            h.server = SimpleNamespace(cfg=cfg)
            h.path = "/v1/messages"
            h.command = "POST"
            body = json.dumps({"model": "kimi-k2", "output_config": {"effort": raw_effort}}).encode("utf-8")
            h.headers = Message()
            h.headers["Content-Type"] = "application/json"
            h.headers["Content-Length"] = str(len(body))
            h.rfile = io.BytesIO(body)
            h.wfile = io.BytesIO()
            h.send_response = mock.Mock()
            h.send_header = mock.Mock()
            h.end_headers = mock.Mock()
            h.send_error = mock.Mock()
            ok_resp = mock.Mock(status=200, reason="OK")
            ok_resp.getheaders.return_value = [("Content-Type", "application/json")]
            ok_resp.read1.side_effect = [b"{}", b""]
            ok_conn = mock.Mock()
            ok_conn.getresponse.return_value = ok_resp
            with mock.patch.object(router.http.client, "HTTPSConnection", return_value=ok_conn), \
                    mock.patch.object(router, "card_key", return_value="tok_kimi_r6"):
                h.do_POST()
            forwarded = json.loads(ok_conn.request.call_args.kwargs["body"])
            self.assertEqual(forwarded["output_config"]["effort"], "max", raw_effort)

        # R6-3: Loopback proxy ConnectionRefusedError falls back to direct when target is NOT Fake-IP
        log_file = self.logs / "router_nat.log"
        h_nat = object.__new__(router.Handler)
        cfg_nat = copy.deepcopy(router.DEFAULTS)
        cfg_nat.update(follow_windows_proxy=True, log=str(log_file), default_key_from_card="Kimi")
        h_nat.server = SimpleNamespace(cfg=cfg_nat)
        h_nat.path = "/v1/messages"
        h_nat.command = "POST"
        nat_body = json.dumps({"model": "kimi-k2"}).encode("utf-8")
        h_nat.headers = Message()
        h_nat.headers["Content-Type"] = "application/json"
        h_nat.headers["Content-Length"] = str(len(nat_body))
        h_nat.rfile = io.BytesIO(nat_body)
        h_nat.wfile = io.BytesIO()
        h_nat.send_response = mock.Mock()
        h_nat.send_header = mock.Mock()
        h_nat.end_headers = mock.Mock()
        h_nat.send_error = mock.Mock()

        proxy_conn = mock.Mock()
        proxy_conn.request.side_effect = ConnectionRefusedError("WSL2 NAT loopback refused")
        direct_resp = mock.Mock(status=200, reason="OK")
        direct_resp.getheaders.return_value = [("Content-Type", "application/json")]
        direct_resp.read1.side_effect = [b'{"id":"msg_ok"}', b""]
        direct_conn = mock.Mock()
        direct_conn.getresponse.return_value = direct_resp

        with mock.patch.object(router, "windows_proxy_for", return_value=router.urlsplit("http://127.0.0.1:7890")), \
                mock.patch.object(router, "_resolves_to_fake_ip", return_value=False), \
                mock.patch.object(router, "card_key", return_value="tok_kimi_nat"), \
                mock.patch.object(router.http.client, "HTTPSConnection", side_effect=[proxy_conn, direct_conn]) as https_cls:
            h_nat.do_POST()
        self.assertEqual(https_cls.call_count, 2)
        proxy_conn.close.assert_called_once_with()
        direct_conn.close.assert_called_once_with()
        h_nat.send_response.assert_called_once_with(200, "OK")
        rec = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["proxy_fallback"], "loopback_proxy_unreachable")
        self.assertEqual(rec["transport"], "direct")

        # Fake-IP target MUST NOT fall back to direct connection on loopback proxy refusal
        h_fake = object.__new__(router.Handler)
        h_fake.server = SimpleNamespace(cfg=cfg_nat)
        h_fake.path = "/v1/messages"
        h_fake.command = "POST"
        h_fake.headers = Message()
        h_fake.headers["Content-Type"] = "application/json"
        h_fake.headers["Content-Length"] = str(len(nat_body))
        h_fake.rfile = io.BytesIO(nat_body)
        h_fake.wfile = io.BytesIO()
        h_fake.send_error = mock.Mock()
        proxy_conn_fake = mock.Mock()
        proxy_conn_fake.request.side_effect = ConnectionRefusedError("refused")
        with mock.patch.object(router, "windows_proxy_for", return_value=router.urlsplit("http://127.0.0.1:7890")), \
                mock.patch.object(router, "_resolves_to_fake_ip", return_value=True), \
                mock.patch.object(router, "card_key", return_value="tok_kimi_nat"), \
                mock.patch.object(router.http.client, "HTTPSConnection", return_value=proxy_conn_fake) as https_fake:
            h_fake.do_POST()
        self.assertEqual(https_fake.call_count, 1)
        proxy_conn_fake.close.assert_called_once_with()
        self.assertEqual(h_fake.send_error.call_args.args[0], 502)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "requires POSIX bash")
    def test_r7_wsl_ccswitch_scripts_syntax_and_sandboxed_update_behaviors(self):
        update_src = REPO_ROOT / "template/claude/router/update-cc-switch-wsl.sh"
        launch_src = REPO_ROOT / "template/claude/router/launch-cc-switch-wsl.sh"
        for sh_script in (update_src, launch_src):
            chk = subprocess.run(["bash", "-n", str(sh_script)], capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(chk.returncode, 0, chk.stderr)

        launch_txt = launch_src.read_text(encoding="utf-8")
        self.assertIn('WINDOWS_POWERSHELL="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"', launch_txt)
        self.assertIn("-NoLogo -NoProfile -NonInteractive -EncodedCommand", launch_txt)
        self.assertIn("timeout 8s", launch_txt)

        sbx = self.root / "wsl_sbx"
        router_dir = sbx / "router"
        fake_home = sbx / "home"
        state_dir = sbx / "state"
        for d in (router_dir, fake_home, state_dir):
            d.mkdir(parents=True)

        update_sh = router_dir / "update-cc-switch-wsl.sh"
        shutil.copy2(update_src, update_sh)
        update_sh.chmod(0o755)
        launcher_sh = router_dir / "launch-cc-switch-wsl.sh"
        launcher_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        launcher_sh.chmod(0o755)

        # Use BASH_ENV function shims so simulation works identically on both exec and noexec mounts
        bash_env_file = sbx / "bash_stubs.sh"
        bash_env_file.write_text(
            'uname() { echo "x86_64"; }\n'
            'curl() {\n'
            '  local url="${!#}"\n'
            '  if [[ "$url" == *"/releases/tags/"* ]]; then\n'
            '    cat "$SBX_STATE/release.json"\n'
            '  else\n'
            '    local out=""\n'
            '    while [ "$#" -gt 0 ]; do\n'
            '      if [ "$1" = "-o" ]; then out="$2"; shift 2; else shift; fi\n'
            '    done\n'
            '    cp "$SBX_STATE/payload.bin" "$out"\n'
            '  fi\n'
            '}\n'
            'dpkg-deb() {\n'
            '  local deb="$2" field="$3"\n'
            '  [ -f "$deb" ] || return 1\n'
            '  if [ "$#" -eq 3 ] && [ "$field" = "Package" ]; then echo "cc-switch"; else echo "cc-switch 4.0.31"; fi\n'
            '}\n'
            'dpkg-query() { echo "4.0.31"; }\n'
            'sudo() {\n'
            '  shift; shift; shift\n'
            '  local deb_arg="$1"\n'
            '  printf "%s\\n" "$deb_arg" > "$SBX_STATE/apt_arg.txt"\n'
            '  [[ "$deb_arg" == *.deb ]] || { echo "apt requires .deb suffix: $deb_arg" >&2; return 1; }\n'
            '  [ -f "$deb_arg" ] || { echo "missing file: $deb_arg" >&2; return 1; }\n'
            '  return "${SBX_APT_EXIT:-0}"\n'
            '}\n'
            'pgrep() {\n'
            '  if [ -f "$SBX_STATE/gui_running" ]; then echo "4242"; return 0; fi\n'
            '  return 1\n'
            '}\n'
            'pkill() {\n'
            '  echo "pkill:$*" >> "$SBX_STATE/pkill.calls"\n'
            '  rm -f "$SBX_STATE/gui_running"\n'
            '  return 0\n'
            '}\n'
            'mock_launcher_fn() {\n'
            '  echo "launched" >> "$SBX_STATE/launcher.calls"\n'
            '  return "${SBX_LAUNCHER_EXIT:-0}"\n'
            '}\n'
            'mock_win_ps_fn() {\n'
            '  printf "%s\\n" "$*" > "$SBX_STATE/ps_args.txt"\n'
            '  echo "RESULT:VISIBLE"\n'
            '}\n'
            'export -f uname curl dpkg-deb dpkg-query sudo pgrep pkill mock_launcher_fn mock_win_ps_fn\n',
            encoding="utf-8",
        )
        # Ensure LAUNCHER works even when sandbox resides on a noexec mount
        if not os.access(launcher_sh, os.X_OK):
            update_sh.write_text(
                update_src.read_text(encoding="utf-8").replace(
                    'LAUNCHER="$(resolve_launcher || true)"',
                    'LAUNCHER="mock_launcher_fn"',
                ),
                encoding="utf-8",
            )
        else:
            launcher_sh.write_text(
                '#!/usr/bin/env bash\n'
                'echo "launched" >> "$SBX_STATE/launcher.calls"\n'
                'exit "${SBX_LAUNCHER_EXIT:-0}"\n',
                encoding="utf-8",
            )
            launcher_sh.chmod(0o755)

        base_env = {
            **os.environ,
            "HOME": str(fake_home),
            "SBX_STATE": str(state_dir),
            "BASH_ENV": str(bash_env_file),
        }

        # Verify launch-cc-switch-wsl.sh fallback to WINDOWS_POWERSHELL and timeout/PowerShell flags
        true_bin = shutil.which("true") or "/bin/true"
        patched_launch = router_dir / "patched-launch.sh"
        patched_launch.write_text(
            launch_txt.replace("APP=/usr/bin/cc-switch", f'APP="{true_bin}"')
            .replace(
                'WINDOWS_POWERSHELL="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"',
                f'WINDOWS_POWERSHELL="{true_bin}"',
            )
            .replace(
                'timeout 8s "$ps_bin"',
                'mock_win_ps_fn',
            ),
            encoding="utf-8",
        )
        (state_dir / "gui_running").write_text("1\n", encoding="utf-8")
        res_launch = subprocess.run(["bash", str(patched_launch)], env=base_env, capture_output=True, text=True, encoding="utf-8", timeout=15)
        (state_dir / "gui_running").unlink(missing_ok=True)
        self.assertEqual(res_launch.returncode, 0, res_launch.stderr)
        self.assertIn("Window state: VISIBLE", res_launch.stdout)
        ps_args = (state_dir / "ps_args.txt").read_text(encoding="utf-8")
        self.assertIn("-NoLogo -NoProfile -NonInteractive -EncodedCommand", ps_args)

        payload_bytes = b"fake-deb-binary-payload-v4.0.31"
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        (state_dir / "payload.bin").write_bytes(payload_bytes)

        def run_update(*u_args, extra_env=None):
            env = {**base_env, **(extra_env or {})}
            return subprocess.run(["bash", str(update_sh), *u_args], env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)

        # 1) Release missing architecture .deb asset -> exit 1 WITHOUT hinting --no-hash (even with --no-hash)
        (state_dir / "release.json").write_text(
            json.dumps({"assets": [{"name": "CC-Switch-v4.0.31-Windows-x64.msi", "digest": f"sha256:{payload_hash}"}]}),
            encoding="utf-8",
        )
        for flags in (["4.0.31"], ["4.0.31", "--no-hash"]):
            res = run_update(*flags)
            self.assertEqual(res.returncode, 1)
            self.assertIn("does not contain asset", res.stderr)
            self.assertNotIn("--no-hash", res.stderr)

        # 2) Asset exists without digest -> prompts --no-hash by default, succeeds with --no-hash (and does NOT launch GUI when not running)
        (state_dir / "release.json").write_text(
            json.dumps({"assets": [{"name": "CC-Switch-v4.0.31-Linux-x86_64.deb", "digest": None}]}),
            encoding="utf-8",
        )
        res_no_override = run_update("4.0.31")
        self.assertEqual(res_no_override.returncode, 1)
        self.assertIn("--no-hash", res_no_override.stderr)

        res_override = run_update("4.0.31", "--no-hash")
        self.assertEqual(res_override.returncode, 0, res_override.stderr)
        self.assertIn("Installed cc-switch: 4.0.31", res_override.stdout)
        self.assertFalse((state_dir / "launcher.calls").exists())

        # 3) Local package path without .deb suffix gets temporary .deb symlink for apt & cleans up
        raw_blob = sbx / "downloaded_pkg_blob"
        raw_blob.write_bytes(payload_bytes)
        res_blob = run_update(str(raw_blob))
        self.assertEqual(res_blob.returncode, 0, res_blob.stderr)
        apt_passed = Path((state_dir / "apt_arg.txt").read_text(encoding="utf-8").strip())
        self.assertTrue(apt_passed.name.endswith(".deb"))
        self.assertFalse(apt_passed.exists(), "temporary .deb symlink dir must be cleaned up on exit")

        # 4) GUI running before update -> waits for exit after pkill -TERM, relaunches GUI, and relaunch failure only warns without failing exit 0
        (state_dir / "gui_running").write_text("1\n", encoding="utf-8")
        res_relaunch_warn = run_update(str(raw_blob), extra_env={"SBX_LAUNCHER_EXIT": "1"})
        self.assertEqual(res_relaunch_warn.returncode, 0, res_relaunch_warn.stderr)
        self.assertIn("Installed cc-switch: 4.0.31", res_relaunch_warn.stdout)
        self.assertIn("Warning:", res_relaunch_warn.stderr)
        self.assertTrue((state_dir / "pkill.calls").is_file())
        self.assertEqual((state_dir / "launcher.calls").read_text(encoding="utf-8").splitlines(), ["launched"])

    def test_r8_install_ps1_encoding_console_guard_and_unauthorized_catch(self):
        ps1_path = REPO_ROOT / "install.ps1"
        raw = ps1_path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))
        lines = raw.decode("utf-8-sig").splitlines()
        self.assertIn("try {", lines[4])
        self.assertIn("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)", lines[4])
        self.assertIn("catch [System.IO.IOException] {}", lines[4])
        self.assertIn("catch [System.Security.SecurityException] {}", lines[4])
        full_text = "\n".join(lines)
        self.assertIn("catch [System.UnauthorizedAccessException]", full_text)

    def test_r2_f1_codex_hook_cross_interpreter_legacy_flags_and_policy_migration(self):
        codex_root = self.root / "codex_f1"
        hook_dir = codex_root / "hooks"
        hook_dir.mkdir(parents=True)
        hooks_path = codex_root / "hooks.json"
        tpl_path = REPO_ROOT / "template/codex/hooks.json"

        loader_p = (hook_dir / "session_context_loader.py").as_posix()
        status_p = (hook_dir / "status_guard.py").as_posix()
        repeat_p = (hook_dir / "reply_repeat_guard.py").as_posix()
        gate_p = (hook_dir / "handoff_gate.py").as_posix()
        state_p = (hook_dir / "state").as_posix()
        logs_p = (hook_dir / "logs").as_posix()

        custom_sh = {"type": "command", "command": f'/bin/sh "{loader_p}" --client codex'}
        py2_custom = {"type": "command", "command": f'python2.7 -B "{status_p}" --client codex --state-dir "{state_p}" --log-dir "{logs_p}"'}
        py310_custom = {"type": "command", "command": f'python3.10 -I -B "{status_p}" --client codex --state-dir "{state_p}" --log-dir "{logs_p}"'}

        legacy_doc = {
            "hooks": {
                "SessionStart": [{
                    "matcher": "^(compact|resume)$",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'"/opt/removed-py312/bin/python3.12" -B "{loader_p}" --client codex',
                            "timeout": 29,
                            "statusMessage": "legacy loader",
                        },
                        custom_sh,
                    ],
                }],
                "PreToolUse": [{
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'python3.13t "{status_p}" --client codex --state-dir "{state_p}" --log-dir "{logs_p}"',
                        },
                        {
                            "type": "command",
                            "command": f'"/stale/dup/python3.11" -I -B "{status_p}" --client codex --state-dir "{state_p}" --log-dir "{logs_p}"',
                        },
                        py2_custom,
                        py310_custom,
                    ],
                }],
                "Stop": [{
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'"/removed/venv/bin/python3.13t" -I -B "{repeat_p}" --client codex --state-dir "{state_p}" --log-dir "{logs_p}"',
                        },
                        {
                            "type": "command",
                            "command": f'"/removed/venv/bin/python3.12" -I -B "{gate_p}" --client codex --mode enforce --compact-limit 4 --state-dir "{state_p}/handoff"',
                            "timeout": 41,
                        },
                    ],
                }],
            },
        }
        user_words.atomic_write_json(hooks_path, legacy_doc)
        new_py_cmd = f'"{sys.executable}" -X utf8 -I -B' if os.name == "nt" else f"{installer.shlex.quote(sys.executable)} -X utf8 -I -B"
        candidates = [("", sys.executable, [])]
        gate_handler = legacy_doc["hooks"]["Stop"][0]["hooks"][1]
        classified_gate = installer._codex_managed_hook(gate_handler, codex_root, candidates)
        self.assertIsNotNone(classified_gate)
        self.assertEqual(classified_gate.kind, "gate")
        self.assertIsNone(classified_gate.interpreter)
        self.assertEqual(classified_gate.policy, ("enforce", 4))
        self.assertEqual(installer._codex_managed_hook(py2_custom, codex_root, candidates).kind, "custom")
        self.assertEqual(installer._codex_managed_hook(py310_custom, codex_root, candidates).kind, "custom")

        with mock.patch.object(installer, "_hook_python_candidates", return_value=candidates), \
                redirect_stderr(io.StringIO()):
            plan = installer._plan_codex_hooks(hooks_path, tpl_path, new_py_cmd)
        self.assertEqual((plan.mode, plan.compact_limit), ("enforce", 4))
        self.assertIsNotNone(plan.change)
        merged = json.loads(plan.change.content)

        ss_hooks = merged["hooks"]["SessionStart"][0]["hooks"]
        self.assertEqual(len(ss_hooks), 2)
        self.assertTrue(ss_hooks[0]["command"].startswith(new_py_cmd + " "))
        self.assertIn("uguard_hook.py", ss_hooks[0]["command"])
        self.assertEqual(ss_hooks[0]["timeout"], 29)
        self.assertIn(custom_sh, ss_hooks)

        pre_hooks = merged["hooks"]["PreToolUse"][0]["hooks"]
        status_managed = [h for h in pre_hooks if "uguard_hook.py" in h["command"] and h not in (py2_custom, py310_custom)]
        self.assertEqual(len(status_managed), 1)
        self.assertTrue(status_managed[0]["command"].startswith(new_py_cmd + " "))

        stop_hooks = merged["hooks"]["Stop"][0]["hooks"]
        self.assertEqual(len(stop_hooks), 1)
        self.assertTrue(stop_hooks[0]["command"].startswith(new_py_cmd + " "))
        self.assertIn("uguard_hook.py", stop_hooks[0]["command"])
        self.assertIn("--event Stop", stop_hooks[0]["command"])

        # Working interpreter with legacy flags on uguard_hook.py still upgrades flags to HOOK_PYTHON_FLAGS
        hooks_path.write_bytes(plan.change.content)
        doc_working_legacy = json.loads(hooks_path.read_text(encoding="utf-8"))
        for group in doc_working_legacy["hooks"].get("Stop", []):
            for h in group["hooks"]:
                if "uguard_hook.py" in h["command"]:
                    h["command"] = h["command"].replace("-X utf8 -I -B", "-I -B", 1)
        user_words.atomic_write_json(hooks_path, doc_working_legacy)
        with mock.patch.object(installer, "_hook_python_candidates", return_value=candidates), \
                mock.patch.object(installer, "_is_working_python_cmd", return_value=True), \
                redirect_stderr(io.StringIO()):
            plan_flag_up = installer._plan_codex_hooks(hooks_path, tpl_path, new_py_cmd)
        self.assertIsNotNone(plan_flag_up.change)
        up_stop = json.loads(plan_flag_up.change.content)["hooks"]["Stop"][0]["hooks"]
        self.assertTrue(all("-X utf8 -I -B" in h["command"] for h in up_stop))

    def test_r2_f2_fleet_state_symlinks_corrupt_json_git_scrub_and_reload_clear_guards(self):
        repo = self.project / "upstream_f2"
        repo.mkdir()
        (repo / "install.py").write_text("", encoding="utf-8")
        plugin_json = repo / "template/user-guard/plugin.json"
        user_words.atomic_write_json(plugin_json, {"version": "1.15.2"})
        profile = repo / "template/user-guard/skills/cae-harness/fleet_profile.json"
        user_words.atomic_write_json(profile, {"runtime_tunables": {"headless_dispatch": {"fleet_state_ttl_sec": 300}}})

        # 1) Symlink / reparse-point checks on state_dir ancestor, state_dir, state_file, and lock_path
        real_dir = self.root / "real_f2_dir"
        real_dir.mkdir()
        sym_dir = self.root / "sym_f2_dir"
        symlink_ok = True
        try:
            sym_dir.symlink_to(real_dir, target_is_directory=True)
        except OSError:
            symlink_ok = False
        clean_state_dir = self.root / "clean_f2_state"
        clean_state_dir.mkdir()
        with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(clean_state_dir)}):
            expected_sf = fleet_state._validated_state_file(repo)
        if symlink_ok:
            for bad_state_dir in (sym_dir, sym_dir / "child_state"):
                with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(bad_state_dir)}):
                    with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                        fleet_state._validated_state_file(repo)

            victim_file = self.root / "victim_state.json"
            victim_file.write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(clean_state_dir)}):
                expected_sf.symlink_to(victim_file)
                with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                    fleet_state._validated_state_file(repo)
                expected_sf.unlink()

                lock_path = expected_sf.with_suffix(expected_sf.suffix + ".lock")
                lock_path.symlink_to(victim_file)
                with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                    fleet_state._validated_state_file(repo)
                with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                    with fleet_state._state_lock(expected_sf):
                        pass
                lock_path.unlink()
        else:
            reparse_stat = SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)
            with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(real_dir)}), \
                    mock.patch.object(Path, "lstat", return_value=reparse_stat):
                with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                    fleet_state._validated_state_file(repo)

        # 2) _load_state converts RecursionError & NaN/Infinity/-Infinity into ValueError("corrupt state JSON")
        deep_nest = max(20000, sys.getrecursionlimit() * 10)
        for corrupt_text in (
            "[" * deep_nest + "]" * deep_nest,
            '{"ttl_seconds": 100, "bad": NaN}',
            '{"ttl_seconds": 100, "bad": Infinity}',
            '{"ttl_seconds": 100, "bad": -Infinity}',
        ):
            expected_sf.write_text(corrupt_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "corrupt state JSON"):
                fleet_state._load_state(expected_sf)
        expected_sf.unlink()

        # 3) _repo_meta validates isinstance(parsed_plugin, dict) and scrubs GIT_* env vars
        plugin_json.write_text("[]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            fleet_state._repo_meta(repo)
        user_words.atomic_write_json(plugin_json, {"version": "1.15.2"})

        polluted_git_env = {
            "GIT_DIR": "/bad/git_dir",
            "GIT_WORK_TREE": "/bad/work_tree",
            "GIT_INDEX_FILE": "/bad/index",
            "GIT_OBJECT_DIRECTORY": "/bad/objects",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/bad/alt",
            "GIT_COMMON_DIR": "/bad/common",
            "GIT_CEILING_DIRECTORIES": "/bad/ceiling",
            "GIT_PREFIX": "bad/prefix",
        }
        with mock.patch.dict(os.environ, polluted_git_env), \
                mock.patch.object(fleet_state.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="deadbeef\n")) as git_mock:
            meta = fleet_state._repo_meta(repo)
        self.assertEqual(meta["repo_commit"], "deadbeef")
        passed_git_env = git_mock.call_args.kwargs["env"]
        for gk in polluted_git_env:
            self.assertNotIn(gk, passed_git_env)

        # 4) clear guards unclaimed & claimed AWAITING_MASTER_RELOAD; propose/record-pass1/clear refuse corrupt state without --force
        def run_state(*cli_args):
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(clean_state_dir)}), redirect_stdout(buf):
                rc = fleet_state.main(["--repo", str(repo), *cli_args])
            return rc, json.loads(buf.getvalue())

        rc, _ = run_state("record-pass1", "--session-id", "p1_unclaimed", "--target-master", "codex")
        self.assertEqual(rc, 0)
        unclaimed_raw = expected_sf.read_bytes()
        for clr_args in (["clear"], ["clear", "--verified"]):
            rc, out = run_state(*clr_args)
            self.assertEqual(rc, 1)
            self.assertEqual(out["reason"], "active_master_reload")
            self.assertEqual(expected_sf.read_bytes(), unclaimed_raw)

        rc, _ = run_state("verify-pass2", "--session-id", "p2_claimant")
        self.assertEqual(rc, 0)
        for clr_args in (["clear"], ["clear", "--verified"]):
            rc, out = run_state(*clr_args)
            self.assertEqual(rc, 1)
            self.assertEqual(out["reason"], "active_pass2_claimed")

        expected_sf.write_bytes(b'{"phase": "IDLE", "nan": NaN}\n')
        corrupt_bytes = expected_sf.read_bytes()
        for blocked_cmd, exp_rc in (
            (["propose", "--session-id", "s1", "--summary", "sum"], 2),
            (["record-pass1", "--session-id", "s1", "--target-master", "codex"], 2),
            (["clear"], 1),
            (["clear", "--verified"], 1),
        ):
            rc, out = run_state(*blocked_cmd)
            self.assertEqual(rc, exp_rc, blocked_cmd)
            self.assertEqual(out["reason"], "corrupt_state", blocked_cmd)
            self.assertEqual(expected_sf.read_bytes(), corrupt_bytes)

        rc, out = run_state("clear", "--force")
        self.assertEqual(rc, 0)
        self.assertFalse(expected_sf.exists())

        help_buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stdout(help_buf):
            fleet_state.main(["clear", "--help"])
        self.assertEqual(cm.exception.code, 0)
        help_txt = help_buf.getvalue()
        self.assertIn("reload lock", help_txt)
        self.assertIn("corrupt state", help_txt)

    def test_r2_f3_router_http_smuggling_chunked_uri_incomplete_read_config_and_bind(self):
        cfg = copy.deepcopy(router.DEFAULTS)
        cfg.update(log=str(self.logs / "router_f3.log"), default_key_from_card="Kimi")

        def make_post_handler(raw_headers, raw_body=b"{}", path="/v1/messages"):
            h = object.__new__(router.Handler)
            h.server = SimpleNamespace(cfg=cfg)
            h.path = path
            h.command = "POST"
            h.headers = Message()
            for hk, hv in raw_headers:
                h.headers.add_header(hk, hv)
            h.rfile = io.BytesIO(raw_body)
            h.wfile = io.BytesIO()
            h.send_response = mock.Mock()
            h.send_header = mock.Mock()
            h.end_headers = mock.Mock()
            h.send_error = mock.Mock()
            return h

        # 1) Smuggling / malformed Content-Length and Transfer-Encoding -> 400
        bad_header_cases = [
            ([("Content-Length", "2"), ("Content-Length", "5")], b"{}"),
            ([("Content-Length", "2, 5")], b"{}"),
            ([("Content-Length", "+2")], b"{}"),
            ([("Content-Length", "-2")], b"{}"),
            ([("Content-Length", "1_0")], b"{}"),
            ([("Transfer-Encoding", "chunked"), ("Content-Length", "2")], b"2\r\n{}\r\n0\r\n\r\n"),
            ([("Transfer-Encoding", "gzip")], b"{}"),
            ([("Transfer-Encoding", "gzip, chunked")], b"2\r\n{}\r\n0\r\n\r\n"),
            ([("Transfer-Encoding", "")], b"{}"),
        ]
        for hdrs, payload in bad_header_cases:
            with self.subTest(hdrs=hdrs):
                h = make_post_handler(hdrs, payload)
                h.do_POST()
                self.assertEqual(h.send_error.call_args.args[0], 400)

        # 2) Strict _read_chunked_body validation
        for bad_chunk in (
            b"0x2\r\n{}\r\n0\r\n\r\n",
            b"+2\r\n{}\r\n0\r\n\r\n",
            b"1_0\r\n" + b"a" * 16 + b"\r\n0\r\n\r\n",
            b"2\r\n{}\r\n0\r\n",  # EOF without terminating blank line
            b"0",                 # EOF without CRLF
            b"0\r\n" + b"X-T: 1\r\n" * 65 + b"\r\n",  # > 64 trailers
            b"0\r\nX-Long: " + b"a" * 65536 + b"\r\n\r\n",  # > 65536 byte trailer line
        ):
            with self.subTest(bad_chunk=bad_chunk[:24]):
                h = make_post_handler([("Transfer-Encoding", "chunked")], bad_chunk)
                h.do_POST()
                self.assertEqual(h.send_error.call_args.args[0], 400)

        ok_64_trailers = b"2\r\n{}\r\n0\r\n" + b"X-T: 1\r\n" * 64 + b"\r\n"
        self.assertEqual(router._read_chunked_body(io.BytesIO(ok_64_trailers), 1024), b"{}")

        # 3) Absolute request URI via urlsplit + upstream IncompleteRead (err_side: upstream)
        body = json.dumps({"model": "kimi-k2"}).encode("utf-8")
        h_abs = make_post_handler(
            [("Content-Type", "application/json"), ("Content-Length", str(len(body)))],
            body,
            path="http://127.0.0.1:8241/v1/messages?beta=true",
        )
        trunc_resp = mock.Mock(status=200, reason="OK", length=19)
        trunc_resp.getheaders.return_value = [("Content-Type", "application/json"), ("Content-Length", "25")]
        trunc_resp.read1.side_effect = [b'{"id":', b""]
        up_conn = mock.Mock()
        up_conn.getresponse.return_value = trunc_resp
        with mock.patch.object(router, "card_key", return_value="tok_kimi_f3"), \
                mock.patch.object(router.http.client, "HTTPSConnection", return_value=up_conn):
            h_abs.do_POST()
        self.assertEqual(up_conn.request.call_args.args[1], "/coding/v1/messages?beta=true")
        last_log = json.loads(Path(cfg["log"]).read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last_log["path"], "/v1/messages")
        self.assertEqual(last_log["err_side"], "upstream")
        self.assertIn("IncompleteRead", last_log["err"])

        # 4) Client disconnect handling in health check and _safe_send_error
        for disc_exc in (BrokenPipeError("pipe"), ConnectionResetError("reset"), ConnectionAbortedError("abort")):
            h_health = object.__new__(router.Handler)
            h_health.server = SimpleNamespace(cfg={"port": 8241})
            h_health.path = "http://127.0.0.1:8241/__router/health"
            h_health.send_response = mock.Mock()
            h_health.send_header = mock.Mock()
            h_health.end_headers = mock.Mock()
            h_health.wfile = mock.Mock()
            h_health.wfile.write.side_effect = disc_exc
            h_health.do_GET()  # Must not propagate disconnect

            h_err = object.__new__(router.Handler)
            h_err.send_error = mock.Mock(side_effect=disc_exc)
            h_err._safe_send_error(400, "bad request")

        # 5) Explicit missing --config exits 2; load_config aliases + range checks + windows_proxy_ttl_s + bind OSError
        with self.assertRaises(SystemExit) as cm_cfg, redirect_stderr(io.StringIO()):
            router.main(["--config", str(self.root / "missing-router.json")])
        self.assertEqual(cm_cfg.exception.code, 2)

        alias_cfg_file = self.root / "alias_router.json"
        user_words.atomic_write_json(alias_cfg_file, {
            "port": 18249,
            "timeout_sec": 45,
            "key_cache_ttl_sec": 12,
            "windows_proxy_ttl_sec": 19,
        })
        loaded_cfg = router.load_config(str(alias_cfg_file))
        self.assertEqual(loaded_cfg["timeout_s"], 45)
        self.assertEqual(loaded_cfg["key_cache_ttl_s"], 12)
        self.assertEqual(loaded_cfg["windows_proxy_ttl_s"], 19)

        router._proxy_cache = None
        clock = [100.0]
        with mock.patch.object(router.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(router, "_read_windows_proxy_settings", return_value={"enabled": False, "server": "", "pac": "", "override": ""}) as rd_proxy:
            router.windows_proxy_for("https://api.kimi.com/coding/v1/messages", ttl=loaded_cfg["windows_proxy_ttl_s"])
            clock[0] = 115.0  # > default 5s, but < configured 19s
            router.windows_proxy_for("https://api.kimi.com/coding/v1/messages", ttl=loaded_cfg["windows_proxy_ttl_s"])
            self.assertEqual(rd_proxy.call_count, 1)
            clock[0] = 120.0
            router.windows_proxy_for("https://api.kimi.com/coding/v1/messages", ttl=loaded_cfg["windows_proxy_ttl_s"])
            self.assertEqual(rd_proxy.call_count, 2)

        for invalid_patch in (
            {"port": 0},
            {"port": 70000},
            {"port": True},
            {"timeout_s": 0},
            {"timeout_sec": -5},
            {"max_body_bytes": 0},
            {"max_inflight": 0},
            {"key_cache_ttl_sec": 0},
            {"windows_proxy_ttl_sec": -1},
        ):
            bad_cfg_file = self.root / "bad_router.json"
            user_words.atomic_write_json(bad_cfg_file, invalid_patch)
            with self.subTest(invalid_patch=invalid_patch), self.assertRaises(ValueError):
                router.load_config(str(bad_cfg_file))

        bind_err_buf = io.StringIO()
        with mock.patch.object(router, "ThreadingHTTPServer", side_effect=OSError(98, "Address already in use")), \
                redirect_stderr(bind_err_buf), self.assertRaises(SystemExit) as cm_bind:
            router.main(["--config", str(alias_cfg_file)])
        self.assertEqual(cm_bind.exception.code, 1)
        err_lines = bind_err_buf.getvalue().splitlines()
        self.assertEqual(len(err_lines), 1)
        self.assertIn("http://127.0.0.1:18249/__router/health", err_lines[0])

    def test_r2_f4_ccswitch_admin_symlink_guards_minimal_columns_and_locked_db_hint(self):
        real_db = self.database()
        real_settings = self.settings()
        admin.prepare_cards(real_db, 8240)

        sym_db = self.root / "sym_cc.db"
        sym_settings = self.root / "sym_claude_settings.json"
        symlink_ok = True
        try:
            sym_db.symlink_to(real_db)
            sym_settings.symlink_to(real_settings)
        except OSError:
            symlink_ok = False
        if symlink_ok:
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.prepare_cards(sym_db, 8240)
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.switch_card(sym_db, real_settings, "Kimi")
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.switch_card(real_db, sym_settings, "Kimi")
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.disable_prompts(sym_db)
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.retire_prompts_and_skills(sym_db)
            with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                admin.backup_sqlite_database(sym_db, self.root / "out.bak")
            self.assertEqual(list(self.root.glob("sym_*bak*")), [])
        else:
            reparse_stat = SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400)
            with mock.patch.object(Path, "lstat", return_value=reparse_stat):
                with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                    admin.prepare_cards(real_db, 8240)
                with self.assertRaisesRegex(ValueError, "symbolic link or reparse point"):
                    admin.backup_sqlite_database(real_db, self.root / "out.bak")

        # Minimal providers schema without created_at, is_current, or sort_index
        min_db = self.root / "minimal_cc.db"
        with closing(sqlite3.connect(min_db)) as con:
            con.execute(
                "CREATE TABLE providers (id TEXT PRIMARY KEY, app_type TEXT, name TEXT, settings_config TEXT)"
            )
            con.execute(f"PRAGMA user_version={admin.TESTED_MAX_SCHEMA}")
            con.execute(
                "INSERT INTO providers (id, app_type, name, settings_config) VALUES (?, ?, ?, ?)",
                ("k1", "claude", "Kimi", json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "tok_kimi_fixture_present"}})),
            )
            con.commit()
        res_insert_update = admin.prepare_cards(min_db, 8241)
        by_name = {c["name"]: c for c in res_insert_update["cards"]}
        self.assertEqual(by_name["Kimi"]["action"], "update")
        self.assertEqual(by_name["Kimi"]["token"], "present")
        self.assertFalse(by_name["Kimi"]["current"])
        self.assertEqual(by_name["DeepSeek"]["action"], "insert")

        # CLI sqlite3.OperationalError("database is locked") appends CC Switch GUI troubleshooting hint
        locked_runner = (
            "import runpy, sqlite3, sys\n"
            "from unittest import mock\n"
            f"sys.argv = [{str(REPO_ROOT / 'template/claude/router/ccswitch_admin.py')!r}, '--db', {str(real_db)!r}, 'prepare-cards']\n"
            "orig_connect = sqlite3.connect\n"
            "def locked_connect(*a, **kw):\n"
            "    raise sqlite3.OperationalError('database is locked')\n"
            "with mock.patch.object(sqlite3, 'connect', side_effect=locked_connect):\n"
            "    runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "-c", locked_runner],
            capture_output=True, text=True, encoding="utf-8", cwd=self.project, timeout=15,
        )
        self.assertEqual(proc.returncode, 1)
        err_lines = proc.stderr.splitlines()
        self.assertEqual(len(err_lines), 1, proc.stderr)
        err_payload = json.loads(err_lines[0])
        self.assertEqual(err_payload["error_type"], "OperationalError")
        self.assertIn("CC Switch GUI", err_payload["error"])

    def test_r2_f5_f6_wsl_scripts_and_entrypoints_python_probing(self):
        launch_src = REPO_ROOT / "template/claude/router/launch-cc-switch-wsl.sh"
        update_src = REPO_ROOT / "template/claude/router/update-cc-switch-wsl.sh"
        install_sh_src = REPO_ROOT / "install.sh"
        install_ps1_src = REPO_ROOT / "install.ps1"

        has_posix_bash = os.name != "nt" and shutil.which("bash") is not None
        if has_posix_bash:
            for sh in (launch_src, update_src, install_sh_src):
                chk = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True, encoding="utf-8", timeout=10)
                self.assertEqual(chk.returncode, 0, chk.stderr)

        launch_txt = launch_src.read_text(encoding="utf-8")
        update_txt = update_src.read_text(encoding="utf-8")
        self.assertIn("PAT='(^|/)cc-switch( |$)'", launch_txt)
        self.assertIn('DISPLAY="${DISPLAY:-:0}"', launch_txt)
        self.assertIn("APP_PAT='(^|/)cc-switch( |$)'", update_txt)

        if has_posix_bash:
            # update-cc-switch-wsl.sh missing 'assets' list -> stderr + exit 1
            sbx = self.root / "wsl_f5"
            sbx.mkdir()
            update_sh = sbx / "update-cc-switch-wsl.sh"
            shutil.copy2(update_src, update_sh)
            bash_stubs = sbx / "stubs.sh"
            bash_stubs.write_text(
                'uname() { echo "x86_64"; }\n'
                'curl() { cat "$SBX_JSON"; }\n'
                'export -f uname curl\n',
                encoding="utf-8",
            )
            for bad_meta in ('{"message": "API rate limit exceeded"}', '{}', '[]', '{not-json'):
                meta_file = sbx / "meta.json"
                meta_file.write_text(bad_meta, encoding="utf-8")
                res = subprocess.run(
                    ["bash", str(update_sh), "4.0.31"],
                    env={**os.environ, "HOME": str(sbx), "BASH_ENV": str(bash_stubs), "SBX_JSON": str(meta_file)},
                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                )
                self.assertEqual(res.returncode, 1, bad_meta)
                self.assertTrue(res.stderr.strip(), bad_meta)
                self.assertNotIn("--no-hash", res.stderr)

        # install.py checks sys.version_info < (3, 11) BEFORE import tomllib
        install_py_txt = (REPO_ROOT / "install.py").read_text(encoding="utf-8")
        self.assertLess(install_py_txt.index("sys.version_info < (3, 11)"), install_py_txt.index("import tomllib"))

        if has_posix_bash:
            # install.sh uses type -ap to skip older python3 first in PATH and pick later Python 3.11+
            bin_old = self.root / "bin_old"
            bin_new = self.root / "bin_new"
            fake_repo = self.root / "fake_install_repo"
            for d in (bin_old, bin_new, fake_repo):
                d.mkdir()
            shutil.copy2(install_sh_src, fake_repo / "install.sh")
            (fake_repo / "install.py").write_text(
                "import sys; print('LAUNCHED_WITH:' + sys.argv[1])\n",
                encoding="utf-8",
            )
            old_py = bin_old / "python3"
            old_py.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            old_py.chmod(0o755)
            new_py = bin_new / "python3"
            new_py.symlink_to(sys.executable)
            if os.access(old_py, os.X_OK):
                probe_env = {**os.environ, "PATH": f"{bin_old}{os.pathsep}{bin_new}{os.pathsep}{os.environ.get('PATH', '')}"}
                res_sh = subprocess.run(
                    ["bash", str(fake_repo / "install.sh"), "--marker-ok"],
                    env=probe_env, capture_output=True, text=True, encoding="utf-8", timeout=15,
                )
                self.assertEqual(res_sh.returncode, 0, res_sh.stderr)
                self.assertIn("LAUNCHED_WITH:--marker-ok", res_sh.stdout)

        # install.ps1 UTF-8 BOM + strict CRLF + -3.14..-3.11 fallback + bilingual message
        ps1_raw = install_ps1_src.read_bytes()
        self.assertTrue(ps1_raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(ps1_raw.count(b"\r\n"), ps1_raw.count(b"\n"))
        ps1_txt = ps1_raw.decode("utf-8-sig")
        for flag in ('"-3"', '"-3.14"', '"-3.13"', '"-3.12"', '"-3.11"'):
            self.assertIn(flag, ps1_txt)
        self.assertIn("Python 3.11+ executable not found", ps1_txt)

    def test_r2_f7_install_prefix_freethreaded_stale_mirror_zotero_and_env_scrub(self):
        # 1) _is_valid_hook_python_prefix & _claude_managed_script free-threaded support and old/missing path rejection
        existing_abs_py = self.root / "python3.13t"
        existing_abs_py.write_text("", encoding="utf-8")
        missing_abs_py = self.root / "no_such_dir/python3.12"

        with mock.patch.object(installer.os, "name", "posix"):
            self.assertTrue(installer._is_valid_hook_python_prefix("python3.13t -X utf8 -I -B"))
            if sys.platform != "win32":
                self.assertTrue(installer._is_valid_hook_python_prefix(f"{installer.shlex.quote(str(existing_abs_py))} -X utf8 -I -B"))
            self.assertFalse(installer._is_valid_hook_python_prefix("python2 -X utf8 -I -B"))
            self.assertFalse(installer._is_valid_hook_python_prefix("python2.7 -X utf8 -I -B"))
            self.assertFalse(installer._is_valid_hook_python_prefix("python3.10 -X utf8 -I -B"))
            self.assertFalse(installer._is_valid_hook_python_prefix(f"{installer.shlex.quote(str(missing_abs_py))} -X utf8 -I -B"))

        with mock.patch.object(installer.os, "name", "nt"):
            self.assertTrue(installer._is_valid_hook_python_prefix("python3.13t.exe -X utf8 -I -B"))
            self.assertFalse(installer._is_valid_hook_python_prefix("python3.10.exe -X utf8 -I -B"))
            self.assertFalse(installer._is_valid_hook_python_prefix(r'"C:\Python313\python.exe" -X utf8 -I -B'))
            self.assertFalse(installer._is_valid_hook_python_prefix(f'"{existing_abs_py}" -X utf8 -I -B'))

        script_path = (self.claude_home / "hooks/status_guard.py").as_posix()
        (self.claude_home / "hooks").mkdir(parents=True, exist_ok=True)
        self.assertEqual(
            installer._claude_managed_script({"type": "command", "command": f'python3.13t -X utf8 -I -B "{script_path}"'}, self.claude_home),
            "status_guard.py",
        )
        for rejected_exe in ("python2", "python2.7", "python3.10"):
            self.assertIsNone(
                installer._claude_managed_script({"type": "command", "command": f'{rejected_exe} -X utf8 -I -B "{script_path}"'}, self.claude_home)
            )

        # 2) _stale_mirror_files detects symlinks under hooks/{state,logs} AND stale empty dirs
        tpl_ug = REPO_ROOT / "template/user-guard"
        target_ug = self.root / "mirror_f7/plugins/user-guard"
        py_cmd = installer.detect_hook_python_command()
        installer.mirror_sync_plugin(
            tpl_ug, target_ug, hooks_bytes=installer._agy_hooks_bytes((tpl_ug / "hooks.json").read_bytes(), py_cmd)
        )
        self.assertEqual(installer._stale_mirror_files(tpl_ug, target_ug), [])

        stale_empty_a = target_ug / "retired_empty_dir/nested"
        stale_empty_a.mkdir(parents=True)
        stale_state_empty = target_ug / "hooks/state/retired_state_subdir"
        stale_state_empty.mkdir(parents=True)
        kept_runtime_empty = target_ug / "hooks/state/handoff"
        kept_runtime_empty.mkdir(parents=True, exist_ok=True)

        outside_note = self.root / "outside_note.txt"
        outside_note.write_text("x\n", encoding="utf-8")
        sym_state_file = target_ug / "hooks/state/evil_link.json"
        sym_logs_dir = target_ug / "hooks/logs/evil_dir_link"
        sym_logs_dir.parent.mkdir(parents=True, exist_ok=True)
        symlink_ok = True
        try:
            sym_state_file.symlink_to(outside_note)
            sym_logs_dir.symlink_to(self.root, target_is_directory=True)
        except OSError:
            symlink_ok = False

        detected_rels = {p.relative_to(target_ug).as_posix() for p in installer._stale_mirror_files(tpl_ug, target_ug)}
        if symlink_ok:
            self.assertIn("hooks/state/evil_link.json", detected_rels)
            self.assertIn("hooks/logs/evil_dir_link", detected_rels)
        self.assertIn("retired_empty_dir/nested", detected_rels)
        self.assertIn("hooks/state/retired_state_subdir", detected_rels)
        self.assertNotIn("hooks/state/handoff", detected_rels)

        if symlink_ok:
            sym_state_file.unlink()
            sym_logs_dir.unlink()
        installer.mirror_sync_plugin(
            tpl_ug, target_ug, hooks_bytes=installer._agy_hooks_bytes((tpl_ug / "hooks.json").read_bytes(), py_cmd)
        )
        self.assertFalse((target_ug / "retired_empty_dir").exists())
        self.assertFalse(stale_state_empty.exists())
        self.assertEqual(installer._stale_mirror_files(tpl_ug, target_ug), [])

        # 3) detect_zotero_mcp_executable excludes /mnt/<drive>/... and .exe/.cmd/.bat on non-Windows
        fake_win_exe = self.root / "zotero-mcp.exe"
        fake_win_exe.write_text("stub\n", encoding="utf-8")
        fake_win_exe.chmod(0o755)
        fake_cmd = self.root / "zotero-mcp.cmd"
        fake_cmd.write_text("stub\n", encoding="utf-8")
        codex_cfg_dir = self.root / "zotero_codex"
        codex_cfg_dir.mkdir()
        (codex_cfg_dir / "config.toml").write_text(
            '[mcp_servers.zotero]\ncommand = "/mnt/c/Users/alice/zotero-mcp"\n',
            encoding="utf-8",
        )
        native_path_cls = type(Path())
        with mock.patch.object(installer, "Path", native_path_cls), \
                mock.patch.object(installer.os, "name", "posix"), \
                mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_cfg_dir), "HOME": str(self.root / "empty_home"), "PATH": "/mnt/c/Tools:/usr/bin"}), \
                mock.patch.object(native_path_cls, "home", return_value=self.root / "empty_home"):
            self.assertIsNone(installer.detect_zotero_mcp_executable(str(fake_win_exe)))
            self.assertIsNone(installer.detect_zotero_mcp_executable(str(fake_cmd)))
            with mock.patch.object(installer.shutil, "which", return_value="/mnt/c/Tools/zotero-mcp"):
                self.assertIsNone(installer.detect_zotero_mcp_executable())
            with mock.patch.object(installer.shutil, "which", return_value=str(fake_win_exe)):
                self.assertIsNone(installer.detect_zotero_mcp_executable())

        # 4) _resolve_hooks_dir fallback & host env scrubbing verification
        for bad_ug in ("", "   ", str(self.root / "missing_ug_dir"), str(outside_note)):
            with mock.patch.dict(os.environ, {"USER_GUARD_INSTALLED_DIR": bad_ug}):
                self.assertEqual(_resolve_hooks_dir(REPO_ROOT), (REPO_ROOT / "template/user-guard/hooks").resolve())
        for scrubbed_key in (
            "PLUGIN_DATA", "USER_GUARD_STATE_DIR", "AGY_CONVERSATION_ID", "ANTIGRAVITY_TOKEN",
            "CODEX_HOME", "CLAUDECODE", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "DSH_HOME",
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GIT_DIR", "HTTP_PROXY", "https_proxy",
            "ALL_PROXY", "NO_PROXY", "XDG_STATE_HOME", "PYTHONPATH",
        ):
            self.assertTrue(_should_scrub_host_env(scrubbed_key, keep_installed_dirs=True), scrubbed_key)
        self.assertFalse(_should_scrub_host_env("USER_GUARD_INSTALLED_DIR", keep_installed_dirs=True))

    def test_v1160_hooks_lib_shared_modules_synced_and_audited(self):
        from _lib import fs_safety, shell_lex

        self.assertIs(user_words.session_state_lock, fs_safety.session_state_lock)
        self.assertIs(user_words._is_reparse_or_link, fs_safety.is_reparse_or_link)
        self.assertIs(handoff_gate._split_shell_clauses, shell_lex.split_shell_clauses)
        self.assertIs(status_guard._decode_ansi_c_quotes, shell_lex.decode_ansi_c_quotes)
        self.assertIs(status_guard._pos_inside_quotes, shell_lex.pos_inside_quotes)

        # Regression: ${foo:-"{"} quoted brace inside parameter expansion & escaped-backtick command substitution
        self.assertEqual(
            shell_lex.split_shell_clauses('echo ${foo:-"{"}; rm -rf /outside'),
            ['echo ${foo:-"{"}', "rm -rf /outside"],
        )
        self.assertEqual(
            shell_lex.split_shell_clauses("echo ${foo:-'{'}; rm -rf /outside"),
            ["echo ${foo:-'{'}", "rm -rf /outside"],
        )
        escaped_bt = r'echo `printf \`uname\`; rm -rf /outside`'
        self.assertTrue(
            any("rm -rf /outside" in sub for sub in shell_lex.extract_cmd_substitutions(escaped_bt)),
            escaped_bt,
        )
        escaped_bt_dq = r'echo "`printf \`uname\`; rm -rf /outside`"'
        self.assertTrue(
            any("rm -rf /outside" in sub for sub in shell_lex.extract_cmd_substitutions(escaped_bt_dq)),
            escaped_bt_dq,
        )
        self.assertEqual(
            self.guard("Bash", command='echo ${foo:-"{"}; touch ../business/out.txt')["hookSpecificOutput"]["permissionDecision"],
            "ask",
        )
        self.assertEqual(
            self.guard("Bash", command=r'echo `printf \`uname\`; touch ../business/out.txt`')["hookSpecificOutput"]["permissionDecision"],
            "ask",
        )

        src_root = self.root / "v1160_source"
        src_root.mkdir()
        shutil.copy2(REPO_ROOT / "install.py", src_root / "install.py")
        shutil.copytree(REPO_ROOT / "template", src_root / "template",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        ag_dir = self.root / "v1160_target/agy/plugins/user-guard"
        cx_dir = self.root / "v1160_target/codex"
        cl_dir = self.root / "v1160_target/claude"
        dsh_dir = self.root / "v1160_target/dsh"
        res = subprocess.run(
            [
                sys.executable, "-B", "-X", "utf8", str(src_root / "install.py"),
                "--app", "all",
                "--target-dir", str(ag_dir),
                "--codex-home", str(cx_dir),
                "--claude-home", str(cl_dir),
                "--dsh-home", str(dsh_dir),
                "--no-zotero", "--skip-test", "--yes",
            ],
            cwd=src_root, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        for root_dir in (ag_dir, cx_dir, cl_dir):
            for lib_name in ("__init__.py", "fs_safety.py", "shell_lex.py", "adaptive_policy.py"):
                self.assertTrue((root_dir / "hooks" / "_lib" / lib_name).is_file(), str(root_dir / "hooks" / "_lib" / lib_name))

        audit_ok = installer.audit_upgrade_status(
            src_root, ag_dir, cx_dir, cl_dir, dsh_dir, app_scope="all"
        )
        self.assertEqual(audit_ok["antigravity"]["drift_files"], [])
        self.assertEqual(audit_ok["codex"]["drift_files"], [])
        self.assertEqual(audit_ok["claude"]["drift_files"], [])

        (cx_dir / "hooks/_lib/shell_lex.py").write_text("# drifted\n", encoding="utf-8")
        audit_drift = installer.audit_upgrade_status(
            src_root, ag_dir, cx_dir, cl_dir, dsh_dir, app_scope="all"
        )
        self.assertIn("hooks/_lib/shell_lex.py", audit_drift["codex"]["drift_files"])

    def test_v1160_adaptive_per_agent_and_model_meta_policy(self):
        from _lib import adaptive_policy

        sample_profile = {
            "schema_version": 1,
            "runtime_tunables": {
                "compaction_recovery": {
                    "defaults": {
                        "inline_total_chars": 8000,
                        "inline_user_item_chars": 1200,
                        "inline_subagent_total_chars": 1400,
                        "inline_subagent_item_chars": 280,
                        "archive_subagent_item_chars": 1200,
                    },
                    "per_client": {
                        "codex": {"inline_total_chars": 8000},
                        "claude": {"inline_total_chars": 7600},
                    },
                },
                "handoff_thresholds": {
                    "codex": {"default_mode": "advisory", "default_compact_limit": 2},
                    "claude": {"default_mode": "advisory", "default_compact_limit": 2},
                },
                "headless_dispatch": {"router": {"timeout_sec": 720}},
            },
            "subscription_matrix": {
                "models": {
                    "gpt-6.1-sol": {"agent": "codex", "context_window": 272000, "enabled_in_profile": True},
                    "deepseek-flash[1M]": {"agent": "claude", "enabled_in_profile": True, "provider_model": "deepseek-chat-1m"},
                    "kimi-k2.5": {"agent": "claude", "context_window": 262144, "enabled_in_profile": True},
                    "small-local-model": {"agent": "codex", "context_window": 100000, "handoff_mode": "enforce"},
                }
            },
        }

        c_1m = adaptive_policy.resolve_adaptive_compaction_policy(sample_profile, client="claude", model_name="deepseek-flash[1M]", effort="xhigh")
        self.assertEqual(c_1m["effective_context_window"], 1_000_000)
        self.assertGreater(c_1m["inline_total_chars"], 7600)

        h_1m = adaptive_policy.resolve_adaptive_handoff_policy(sample_profile, client="claude", model_name="deepseek-flash[1M]")
        self.assertEqual(h_1m["effective_context_window"], 1_000_000)
        self.assertGreaterEqual(h_1m["default_compact_limit"], 2)

        c_small = adaptive_policy.resolve_adaptive_compaction_policy(sample_profile, client="codex", model_name="small-local-model")
        self.assertEqual(c_small["effective_context_window"], 100_000)
        self.assertLess(c_small["inline_total_chars"], 8000)

        h_small = adaptive_policy.resolve_adaptive_handoff_policy(sample_profile, client="codex", model_name="small-local-model")
        self.assertEqual(h_small["default_mode"], "enforce")

        ccswitch_admin_mod = installer._load_ccswitch_admin(REPO_ROOT)
        card_params = ccswitch_admin_mod.derive_adaptive_card_defaults(sample_profile, port=8241)
        self.assertEqual(card_params["kimi_model"], "kimi-k2.5")
        self.assertEqual(card_params["deepseek_model"], "deepseek-chat-1m")
        self.assertEqual(card_params["max_context"], "1000000")
        self.assertEqual(card_params["compact_window"], "720000")

        src_root = self.root / "v1160_meta_src"
        src_root.mkdir()
        shutil.copy2(REPO_ROOT / "install.py", src_root / "install.py")
        shutil.copytree(REPO_ROOT / "template", src_root / "template",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        ag_dir = self.root / "v1160_meta_dst/agy/plugins/user-guard"
        cx_dir = self.root / "v1160_meta_dst/codex"
        cl_dir = self.root / "v1160_meta_dst/claude"
        dsh_dir = self.root / "v1160_meta_dst/dsh"
        audit = installer.audit_upgrade_status(src_root, ag_dir, cx_dir, cl_dir, dsh_dir, app_scope="all")
        self.assertIn("adaptive_meta", audit)
        self.assertEqual(set(audit["adaptive_meta"].keys()), {"antigravity", "codex", "claude", "dsh"})

        # Verify ccswitch_admin._build_card_templates consumes adaptive defaults when subscription_matrix has enabled claude models
        prof_path = self.root / "adaptive_card_profile.json"
        adaptive_prof = dict(sample_profile)
        adaptive_prof["client_catalog_rules"] = {
            "claude": {
                "router_primary_model": "fallback-k",
                "router_subagent_model": "fallback-d",
                "max_context_tokens": 128000,
                "auto_compact_window": 90000,
                "router_card_name": "router-adaptive",
            }
        }
        user_words.atomic_write_json(prof_path, adaptive_prof)
        with mock.patch.dict(os.environ, {"CAE_FLEET_PROFILE": str(prof_path)}):
            cards = ccswitch_admin_mod._build_card_templates(8241)
            router_env = cards["router-adaptive"][0]()
            self.assertEqual(router_env["ANTHROPIC_MODEL"], "kimi-k2.5")
            self.assertEqual(router_env["CLAUDE_CODE_SUBAGENT_MODEL"], "deepseek-chat-1m")
            self.assertEqual(router_env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")

        # Verify session_context_loader runtime model extraction & AGENTS.md 3-layer constitutional governance table
        loader_mod = _load_module("scl_adaptive", REPO_ROOT / "template/user-guard/hooks/session_context_loader.py")
        m, e = loader_mod._extract_runtime_model_and_effort("claude", {"model": "deepseek-flash[1M]", "effort": "xhigh"})
        self.assertEqual((m, e), ("deepseek-flash[1M]", "xhigh"))
        with mock.patch.dict(os.environ, {"ANTHROPIC_MODEL": "kimi-k2.5", "CODEX_MODEL": "gpt-6.1-sol"}, clear=False):
            self.assertEqual(loader_mod._extract_runtime_model_and_effort("claude", {})[0], "kimi-k2.5")
            self.assertEqual(loader_mod._extract_runtime_model_and_effort("codex", {})[0], "gpt-6.1-sol")
        agents_md = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for layer_label in ("项目主体代码层", "项目自更新层", "分发给其他 Agent 的配置层"):
            self.assertIn(layer_label, agents_md)


    def test_v1160_card_detail_file_folding_and_dsh_meta_trigger(self):
        # 1) user_words.collapse_historical_cards folds 14-row tables
        sample_hist = (
            "背景\n#### 流 S1（14 项完整参数明细）\n"
            "| 序号 | 参数项 | 当前值 |\n| --- | --- | --- |\n| 1 | 通道 | codex |\n"
            "票据：T-123；状态：PENDING"
        )
        folded = user_words.collapse_historical_cards(sample_hist)
        self.assertIn("[已折叠历史确认单/账单明细]", folded)
        self.assertIn("票据：T-123", folded)
        self.assertNotIn("| 1 | 通道 | codex |", folded)

        sci_hist = (
            "背景\n#### 流 S1（14 项完整参数明细）\n"
            "| 序号 | 参数项 | 当前值 |\n| --- | --- | --- |\n| 1 | 通道 | codex |\n"
            "票据：T-123；状态：PENDING\n\n"
            "网格收敛阶实测对比：\n"
            "| 网格尺度 | L2 误差 | 收敛阶 |\n"
            "| --- | --- | --- |\n"
            "| 64 | 1.25e-4 | 3.98 |\n"
            "| 128 | 7.82e-6 | 4.00 |"
        )
        folded_sci = user_words.collapse_historical_cards(sci_hist)
        self.assertIn("[已折叠历史确认单/账单明细]", folded_sci)
        self.assertNotIn("| 1 | 通道 | codex |", folded_sci)
        self.assertIn("| 网格尺度 | L2 误差 | 收敛阶 |", folded_sci)
        self.assertIn("| 64 | 1.25e-4 | 3.98 |", folded_sci)
        self.assertIn("| 128 | 7.82e-6 | 4.00 |", folded_sci)

        # 2) cae_harness._save_plan writes <ticket_id>_card.md and populates compact_summary_card
        harness_mod = _load_module("cae_harness_card_test", REPO_ROOT / "template/user-guard/skills/cae-harness/scripts/cae_harness.py")
        plan_dir = self.root / "plan_cards"
        plan_dir.mkdir(parents=True, exist_ok=True)
        ticket = {
            "ticket_id": "T-CARD-01",
            "status": "PENDING",
            "session_id": "sess-card-01",
            "workflow_bg": "测试背景",
            "workflow_goal": "测试目标",
            "workflow_impact": "无副作用",
            "capability_ledger": {"last_online_refresh_at": "2026-01-01", "local_bench_verified_at": "2026-01-01"},
            "streams": [{"stream_id": "S1", "subtask": "check", "target": ".", "channel": "cae_harness_headless",
                         "client": "codex", "agent_type": "codex", "model_display": "gpt-6.1-sol", "tier": "high",
                         "context_window": 272000, "headless": True, "clean_room": True, "access_mode": "readonly",
                         "inline_readonly": True, "timeout": 300, "currently_available": True,
                         "quota_scheme": "rolling", "quota_summary": "ok", "receipt_location": "S1.json"}],
        }
        harness_mod._save_plan(ticket, plan_dir)
        card_file = plan_dir / "T-CARD-01_card.md"
        self.assertTrue(card_file.is_file())
        card_text = card_file.read_text(encoding="utf-8")
        self.assertIn("工作流背景：测试背景", card_text)
        self.assertIn("要做什么：测试目标", card_text)
        self.assertIn("14 项完整参数明细", card_text)
        self.assertIn("gpt-6.1-sol", card_text)
        self.assertIn("票据：T-CARD-01；状态：PENDING", card_text)
        self.assertEqual(card_text, ticket["human_popup_markdown"] + "\n")
        self.assertIn("T-CARD-01_card.md", ticket["card_detail_file"])
        self.assertIn("工作流背景：测试背景", ticket["compact_summary_card"])
        self.assertIn("工作流目标：测试目标", ticket["compact_summary_card"])
        self.assertIn("预计后果：无副作用", ticket["compact_summary_card"])
        self.assertIn("| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |", ticket["compact_summary_card"])
        self.assertIn("[T-CARD-01_card.md](file://", ticket["compact_summary_card"])
        self.assertIn("2026-01-01", ticket["compact_summary_card"])
        self.assertNotIn("14 项完整参数明细", ticket["compact_summary_card"])
        self.assertIn("【新对话提示词】", ticket["compact_handoff_prompt_block"])
        self.assertIn("[T-CARD-01_card.md](file://", ticket["compact_handoff_prompt_block"])
        self.assertIn("恢复命令：plan --resume-ticket T-CARD-01", ticket["compact_handoff_prompt_block"])
        self.assertNotIn("14 项完整参数明细", ticket["compact_handoff_prompt_block"])

        # _atomic_text refuses symlink targets
        real_target = plan_dir / "real_note.txt"
        real_target.write_text("orig", encoding="utf-8")
        sym_target = plan_dir / "sym_note.txt"
        try:
            sym_target.symlink_to(real_target)
        except OSError:
            sym_target = None
        if sym_target is not None:
            with self.assertRaises(harness_mod.HarnessError):
                harness_mod._atomic_text(sym_target, "hijacked")
            self.assertEqual(real_target.read_text(encoding="utf-8"), "orig")

        # 3) install._probe_dsh_hook_capability & probe_apps dsh native_binary probe
        dsh_none = installer._probe_dsh_hook_capability(None)
        self.assertEqual(dsh_none["hook_protocol"], "no_lifecycle_hooks")
        self.assertEqual(dsh_none["evidence_state"], "unknown_not_probed")
        fake_res = mock.Mock(stdout="Usage: dsh [OPTIONS] --hooks PreToolUse SessionStart", stderr="")
        with mock.patch.object(installer.subprocess, "run", return_value=fake_res):
            up = installer._probe_dsh_hook_capability("/usr/local/bin/dsh")
            self.assertTrue(up["hard_hooks_supported"])
            self.assertEqual(up["evidence_state"], "detected_heuristic")
            self.assertEqual(up["meta_update_trigger"], "ready_for_fleet_evolve_dsh_hooks")
        fake_bin = self.root / "fake_dsh_bin"
        fake_bin.mkdir(parents=True, exist_ok=True)
        dsh_exe = fake_bin / "dsh"
        dsh_exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_home = self.root / "probe_home"
        fake_home.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(installer.Path, "home", return_value=fake_home), \
             mock.patch.object(installer.shutil, "which", side_effect=lambda name: str(dsh_exe) if name == "dsh" else None), \
             mock.patch.object(installer, "_windows_app_binaries", return_value=(None, None)), \
             mock.patch.object(installer.subprocess, "run", return_value=fake_res):
            probed = installer.detect_installed_apps()
            self.assertEqual(probed["dsh"]["native_binary"], str(dsh_exe))
            self.assertTrue(probed["dsh"]["hard_hooks_supported"])
            self.assertEqual(probed["dsh"]["meta_update_trigger"], "ready_for_fleet_evolve_dsh_hooks")


    def test_v1160_approved_holistic_policy_and_runtime_handoff_override(self):
        # 1 & 2 & 6: fleet_profile.json explicit context_window across all 4 clients, 0.72 compact buffer, no dead quota_warn_percent
        live_pf = json.loads((REPO_ROOT / "template/user-guard/skills/cae-harness/fleet_profile.json").read_text(encoding="utf-8"))
        self.assertEqual(live_pf["client_catalog_rules"]["claude"]["auto_compact_window"], 754975)
        self.assertNotIn("quota_warn_percent", live_pf["runtime_tunables"]["dispatch_confirmation"])
        sub_models = live_pf["subscription_matrix"]["models"]
        self.assertEqual({m["agent"] for m in sub_models.values()}, {"agy", "codex", "claude", "dsh"})
        for slug, meta in sub_models.items():
            self.assertIsInstance(meta.get("context_window"), int, slug)
            self.assertGreaterEqual(meta["context_window"], 272000, slug)

        # 3: router.*.json templates omit static timeout_s so router.load_config inherits fleet_profile timeout_sec
        for tpl_name in ("router.windows.json", "router.wsl.json"):
            tpl_doc = json.loads((REPO_ROOT / "template/claude/router" / tpl_name).read_text(encoding="utf-8"))
            self.assertNotIn("timeout_s", tpl_doc)
            cfg_copy = self.root / f"inherit_{tpl_name}"
            cfg_copy.write_bytes((REPO_ROOT / "template/claude/router" / tpl_name).read_bytes())
            custom_pf = self.root / f"pf_{tpl_name}"
            user_words.atomic_write_json(custom_pf, {"schema_version": 1, "runtime_tunables": {"headless_dispatch": {"router": {"timeout_sec": 615}}}})
            with mock.patch.dict(os.environ, {"CAE_FLEET_PROFILE": str(custom_pf)}):
                loaded_r = router.load_config(cfg_copy)
                self.assertEqual(loaded_r["timeout_s"], 615)

        # 4: default CLI (--mode advisory --compact-limit 0) dynamically upgrades at runtime for model-level handoff_mode, while --mode off stays off
        dyn_pf = self.root / "dyn_handoff_pf.json"
        user_words.atomic_write_json(dyn_pf, {
            "schema_version": 1,
            "runtime_tunables": {"handoff_thresholds": {"claude": {"default_mode": "advisory", "default_compact_limit": 0, "enforce_drain_actions": 4}}},
            "subscription_matrix": {"models": {"small-strict": {"agent": "claude", "context_window": 100000, "handoff_mode": "enforce", "compact_limit_override": 1}}},
        })
        self.boundaries(1)
        with mock.patch.dict(os.environ, {"CAE_FLEET_PROFILE": str(dyn_pf)}):
            proc_adv = self.cli(
                "handoff_gate.py",
                {**self.payload, "hook_event_name": "PostCompact", "model": "small-strict"},
                "--state-dir", self.state / "handoff", "--mode", "advisory", "--compact-limit", 0,
            )
            self.assertEqual(proc_adv.returncode, 0, proc_adv.stderr)
            self.assertIn("systemMessage", json.loads(proc_adv.stdout))
            gstate = self.gate_state()
            self.assertEqual(gstate["mode"], "enforce")
            self.assertEqual(gstate["compactLimit"], 1)
            self.assertEqual(gstate["phase"], "drain")

            proc_off = self.cli(
                "handoff_gate.py",
                {**self.payload, "hook_event_name": "PostCompact", "model": "small-strict"},
                "--state-dir", self.root / "off_state", "--mode", "off", "--compact-limit", 0,
            )
            self.assertEqual(proc_off.returncode, 0, proc_off.stderr)
            self.assertEqual(json.loads(proc_off.stdout), {})

        # 5: ci.yml checks Windows install.ps1 --probe-apps and retains dual unittest passes
        ci_body = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("install.ps1 --probe-apps", ci_body)

    def test_v1160_postcompact_model_persistence_and_installed_profile_resolution(self):
        # 1) PostCompact (with model) followed by model-less PreToolUse & Stop keeps dynamic enforce/drain gate
        dyn_pf = self.root / "persist_handoff_pf.json"
        user_words.atomic_write_json(dyn_pf, {
            "schema_version": 1,
            "runtime_tunables": {
                "handoff_thresholds": {
                    "claude": {"default_mode": "advisory", "default_compact_limit": 0, "enforce_drain_actions": 3},
                    "codex": {"default_mode": "advisory", "default_compact_limit": 0, "enforce_drain_actions": 3},
                }
            },
            "subscription_matrix": {
                "models": {
                    "small-strict-cl": {
                        "agent": "claude", "context_window": 100000,
                        "handoff_mode": "enforce", "compact_limit_override": 1,
                    },
                    "small-strict-cx": {
                        "agent": "codex", "context_window": 100000,
                        "handoff_mode": "enforce", "compact_limit_override": 1,
                    },
                }
            },
        })
        self.boundaries(1)
        with mock.patch.dict(os.environ, {"CAE_FLEET_PROFILE": str(dyn_pf)}):
            os.environ.pop("ANTHROPIC_MODEL", None)
            os.environ.pop("CODEX_MODEL", None)
            p_compact = self.cli(
                "handoff_gate.py",
                {**self.payload, "hook_event_name": "PostCompact", "model": "small-strict-cl"},
                "--state-dir", self.state / "handoff", "--mode", "advisory", "--compact-limit", 0,
            )
            self.assertEqual(p_compact.returncode, 0, p_compact.stderr)
            st_after_compact = self.gate_state()
            self.assertEqual(st_after_compact["mode"], "enforce")
            self.assertEqual(st_after_compact["phase"], "drain")

            # Follow-up PreToolUse omits "model" field completely -> must remain enforce/drain and deny git push
            p_pre = self.cli(
                "handoff_gate.py",
                {
                    **self.payload,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "git push origin main"},
                },
                "--state-dir", self.state / "handoff", "--mode", "advisory", "--compact-limit", 0,
            )
            self.assertEqual(p_pre.returncode, 0, p_pre.stderr)
            pre_out = json.loads(p_pre.stdout)
            self.assertEqual(pre_out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny", pre_out)
            st_after_pre = self.gate_state()
            self.assertEqual(st_after_pre["mode"], "enforce")
            self.assertEqual(st_after_pre["phase"], "drain")

            # Follow-up Stop omits "model" field completely -> must remain enforce/drain and block incomplete reply
            p_stop_bad = self.cli(
                "handoff_gate.py",
                {
                    **self.payload,
                    "hook_event_name": "Stop",
                    "last_assistant_message": "仅普通收尾说明，未提供四项交接要素。",
                },
                "--state-dir", self.state / "handoff", "--mode", "advisory", "--compact-limit", 0,
            )
            self.assertEqual(p_stop_bad.returncode, 0, p_stop_bad.stderr)
            stop_bad_out = json.loads(p_stop_bad.stdout)
            self.assertEqual(stop_bad_out.get("decision"), "block", stop_bad_out)
            self.assertEqual(self.gate_state()["mode"], "enforce")

            # Follow-up Stop with complete handoff text (still without "model") records handoff & transitions to freeze
            p_stop_ok = self.cli(
                "handoff_gate.py",
                {
                    **self.payload,
                    "hook_event_name": "Stop",
                    "last_assistant_message": HANDOFF_TEXT,
                },
                "--state-dir", self.state / "handoff", "--mode", "advisory", "--compact-limit", 0,
            )
            self.assertEqual(p_stop_ok.returncode, 0, p_stop_ok.stderr)
            self.assertEqual(json.loads(p_stop_ok.stdout), {})
            st_after_stop = self.gate_state()
            self.assertEqual(st_after_stop["mode"], "enforce")
            self.assertEqual(st_after_stop["phase"], "freeze")
            self.assertTrue(st_after_stop["handoffRecorded"])

            # Codex CLI symmetry: PostCompact (with model) -> model-less PreToolUse & Stop keep enforce/drain
            cx_sid = "codex_persist_alpha"
            cx_tp = self.root / "codex_sessions" / f"{cx_sid}.jsonl"
            _append_jsonl(cx_tp, {"type": "session_meta", "payload": {"id": cx_sid, "source": "cli", "cwd": str(self.project)}})
            _append_jsonl(cx_tp, {"type": "compacted", "payload": {"message": "compacted-1"}})
            cx_state_dir = self.root / "codex_handoff_state"
            cx_base = {"session_id": cx_sid, "transcript_path": str(cx_tp), "cwd": str(self.project)}

            def run_codex_gate(ev_payload):
                return subprocess.run(
                    [
                        sys.executable, "-B", "-X", "utf8", str(HOOKS_DIR / "handoff_gate.py"),
                        "--client", "codex", "--state-dir", str(cx_state_dir),
                        "--mode", "advisory", "--compact-limit", "0",
                    ],
                    input=json.dumps(ev_payload, ensure_ascii=False),
                    capture_output=True, text=True, encoding="utf-8", cwd=self.project, timeout=20,
                )

            cx_c = run_codex_gate({**cx_base, "hook_event_name": "PostCompact", "model": "small-strict-cx"})
            self.assertEqual(cx_c.returncode, 0, cx_c.stderr)
            cx_p = run_codex_gate({
                **cx_base, "hook_event_name": "PreToolUse",
                "tool_name": "exec_command", "tool_input": {"cmd": "git push origin main"},
            })
            self.assertEqual(cx_p.returncode, 0, cx_p.stderr)
            self.assertEqual(json.loads(cx_p.stdout).get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
            cx_s = run_codex_gate({**cx_base, "hook_event_name": "Stop", "last_assistant_message": "普通结束语"})
            self.assertEqual(cx_s.returncode, 0, cx_s.stderr)
            self.assertEqual(json.loads(cx_s.stdout).get("decision"), "block")

        # 2) Installed-mode config resolution outside REPO_ROOT with CAE_FLEET_PROFILE unset
        src_repo = self.root / "isolated_src_repo"
        src_repo.mkdir()
        shutil.copy2(REPO_ROOT / "install.py", src_repo / "install.py")
        shutil.copytree(
            REPO_ROOT / "template",
            src_repo / "template",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        src_pf_path = src_repo / "template/user-guard/skills/cae-harness/fleet_profile.json"
        src_pf = json.loads(src_pf_path.read_text(encoding="utf-8"))
        src_pf["runtime_tunables"]["headless_dispatch"]["router"]["timeout_sec"] = 642
        src_pf["runtime_tunables"]["handoff_thresholds"]["claude"]["enforce_drain_actions"] = 3
        src_pf["client_catalog_rules"]["claude"]["router_card_name"] = "InstalledRouterCard"
        src_pf["subscription_matrix"]["models"]["installed-strict-cl"] = {
            "agent": "claude",
            "enabled": True,
            "context_window": 100000,
            "handoff_mode": "enforce",
            "compact_limit_override": 1,
        }
        user_words.atomic_write_json(src_pf_path, src_pf)

        inst_home = self.root / "inst_home"
        ag_dir = inst_home / ".gemini/config/plugins/user-guard"
        cx_dir = inst_home / ".codex"
        cl_dir = inst_home / ".claude"
        dsh_dir = inst_home / ".dsh"
        outside_work = self.root / "outside_general_project"
        outside_work.mkdir()

        inst_res = subprocess.run(
            [
                sys.executable, "-B", "-X", "utf8", str(src_repo / "install.py"),
                "--app", "all",
                "--target-dir", str(ag_dir),
                "--codex-home", str(cx_dir),
                "--claude-home", str(cl_dir),
                "--dsh-home", str(dsh_dir),
                "--no-claude-skill",
                "--with-cc-router",
                "--no-zotero", "--skip-test", "--yes",
            ],
            cwd=src_repo, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(inst_res.returncode, 0, inst_res.stderr + inst_res.stdout)
        self.assertFalse((cl_dir / "skills/cae-harness/fleet_profile.json").exists())

        inst_env = {
            k: v for k, v in os.environ.items()
            if k != "CAE_FLEET_PROFILE" and not k.startswith("USER_GUARD_")
        }
        inst_env.update({
            "HOME": str(inst_home),
            "USERPROFILE": str(inst_home),
            "CLAUDE_CONFIG_DIR": str(cl_dir),
            "CODEX_HOME": str(cx_dir),
            "DSH_HOME": str(dsh_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
        })

        # 2a) Installed ccswitch_admin.py outside repo without CAE_FLEET_PROFILE resolves peer installed fleet_profile.json
        inst_db = inst_home / ".cc-switch/cc-switch.db"
        inst_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.database(), inst_db)
        inst_settings = cl_dir / "settings.json"
        prep_proc = subprocess.run(
            [
                sys.executable, "-B", "-X", "utf8", str(cl_dir / "hooks/ccswitch_admin.py"),
                "--db", str(inst_db), "prepare-cards", "--port", "18241",
            ],
            cwd=outside_work, env=inst_env, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(prep_proc.returncode, 0, prep_proc.stderr + prep_proc.stdout)
        prep_cards = self.cards(inst_db)
        self.assertIn("InstalledRouterCard", prep_cards)
        status_proc = subprocess.run(
            [
                sys.executable, "-B", "-X", "utf8", str(cl_dir / "hooks/ccswitch_admin.py"),
                "--db", str(inst_db), "--settings", str(inst_settings), "status",
            ],
            cwd=outside_work, env=inst_env, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(status_proc.returncode, 0, status_proc.stderr + status_proc.stdout)

        # 2b) Installed router.py outside repo without CAE_FLEET_PROFILE inherits timeout_sec=642 from peer installed profile
        tpl_router_cfg = cl_dir / "router" / ("router.windows.json" if os.name == "nt" else "router.wsl.json")
        router_probe = subprocess.run(
            [
                sys.executable, "-B", "-X", "utf8", "-c",
                (
                    "import importlib.util, json, sys; "
                    "spec = importlib.util.spec_from_file_location('inst_router', sys.argv[1]); "
                    "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
                    "print(json.dumps(mod.load_config(sys.argv[2])))"
                ),
                str(cl_dir / "router/router.py"),
                str(tpl_router_cfg),
            ],
            cwd=outside_work, env=inst_env, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(router_probe.returncode, 0, router_probe.stderr + router_probe.stdout)
        self.assertEqual(json.loads(router_probe.stdout)["timeout_s"], 642)

        # 2c) Installed handoff_gate.py outside repo without CAE_FLEET_PROFILE resolves installed-strict-cl & preserves enforce
        inst_sid = "inst_cl_sess_01"
        inst_tp = cl_dir / "projects/work" / f"{inst_sid}.jsonl"
        _append_jsonl(inst_tp, {"type": "system", "subtype": "compact_boundary", "uuid": "inst_b0"})
        inst_hstate = cl_dir / "hooks/state/handoff"
        inst_pc = subprocess.run(
            [
                sys.executable, "-B", "-X", "utf8", str(cl_dir / "hooks/handoff_gate.py"),
                "--client", "claude", "--state-dir", str(inst_hstate),
                "--mode", "advisory", "--compact-limit", "0",
            ],
            input=json.dumps({
                "session_id": inst_sid, "transcript_path": str(inst_tp), "cwd": str(outside_work),
                "hook_event_name": "PostCompact", "model": "installed-strict-cl",
            }, ensure_ascii=False),
            cwd=outside_work, env=inst_env, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(inst_pc.returncode, 0, inst_pc.stderr)
        inst_saved = json.loads((inst_hstate / f"{inst_sid}_handoff_gate.json").read_text(encoding="utf-8"))
        self.assertEqual(inst_saved["mode"], "enforce")
        self.assertEqual(inst_saved["phase"], "drain")
        self.assertEqual(inst_saved["drainActionsRemaining"], 3)

        inst_ptu = subprocess.run(
            [
                sys.executable, "-B", "-X", "utf8", str(cl_dir / "hooks/handoff_gate.py"),
                "--client", "claude", "--state-dir", str(inst_hstate),
                "--mode", "advisory", "--compact-limit", "0",
            ],
            input=json.dumps({
                "session_id": inst_sid, "transcript_path": str(inst_tp), "cwd": str(outside_work),
                "hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": "git push origin main"},
            }, ensure_ascii=False),
            cwd=outside_work, env=inst_env, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(inst_ptu.returncode, 0, inst_ptu.stderr)
        self.assertEqual(json.loads(inst_ptu.stdout).get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_audit_upgrade_preserves_dsh_hook_protocol_and_cross_side_status(self):
        ag_dir = self.root / "audit_ag"
        cx_dir = self.root / "audit_cx"
        cl_dir = self.root / "audit_cl"
        dsh_dir = self.root / "audit_dsh"
        for d in (ag_dir, cx_dir, cl_dir, dsh_dir):
            d.mkdir(parents=True, exist_ok=True)
        fake_det = {
            "platform": sys.platform,
            "os_name": os.name,
            "is_wsl": os.name != "nt",
            "wsl_probe": {
                "status": "checked",
                "apps": {
                    "agy": [{"distro": "Ubuntu-24.04", "binary": "<home>/.gemini/bin/agy", "config_dir_exists": True}],
                    "codex": [],
                    "claude": [],
                    "dsh": [{"distro": "Ubuntu-24.04", "binary": None, "config_dir_exists": False}],
                    "cc-switch": [{"distro": "Ubuntu-24.04", "binary": "/usr/bin/cc-switch", "config_dir_exists": True}],
                },
            } if os.name == "nt" else None,
            "antigravity": {"native_installed": True, "interop_available": True},
            "codex": {"native_installed": False, "interop_available": False},
            "claude": {"native_installed": False, "interop_available": False},
            "dsh": {
                "native_installed": True,
                "interop_available": False,
                "hook_protocol": "lifecycle_hooks",
                "hard_hooks_supported": True,
                "meta_update_trigger": "ready_for_fleet_evolve_dsh_hooks",
            },
            "cc_switch": {"native_installed": False, "interop_available": False},
        }
        with mock.patch.object(installer, "detect_installed_apps", return_value=fake_det):
            rep_sbx = installer.audit_upgrade_status(
                REPO_ROOT, ag_dir, cx_dir, cl_dir, dsh_home=dsh_dir, is_sandbox_run=True, app_scope="all",
            )
            rep_host = installer.audit_upgrade_status(
                REPO_ROOT, ag_dir, cx_dir, cl_dir, dsh_home=dsh_dir, is_sandbox_run=False, app_scope="all",
            )
        self.assertEqual(rep_sbx["dsh"]["hook_protocol"], "lifecycle_hooks")
        self.assertTrue(rep_sbx["dsh"]["hard_hooks_supported"])
        self.assertEqual(rep_sbx["dsh"]["meta_update_trigger"], "ready_for_fleet_evolve_dsh_hooks")
        self.assertIn("cross_side_status", rep_sbx)
        self.assertTrue(rep_sbx["cross_side_status"]["peer_side_present"])
        self.assertTrue(rep_sbx["cross_side_status"]["peer_apps_detected"]["antigravity"])
        self.assertFalse(rep_sbx["cross_side_status"]["peer_apps_detected"]["dsh"])
        if os.name == "nt":
            self.assertTrue(rep_sbx["cross_side_status"]["peer_apps_detected"]["cc_switch"])
        self.assertFalse(rep_sbx["cross_side_status"]["dual_side_sync_required"])
        self.assertTrue(rep_host["cross_side_status"]["dual_side_sync_required"])
        self.assertIsNotNone(rep_host["cross_side_status"]["peer_audit_hint"])
        self.assertIsInstance(rep_sbx["fleet_profile"]["native_agent_profiles_configured"], list)

    def test_v1171_fleet_pass2_clear_router_loopback_ssrf_ccswitch_and_installer_fixes(self):
        # 1) fleet_state clear --session-id unlocks claimed Pass 2 lock without --force; wrong sid still blocked
        repo = self.project / "upstream_v1171"
        repo.mkdir()
        (repo / "install.py").write_text("", encoding="utf-8")
        user_words.atomic_write_json(repo / "template/user-guard/plugin.json", {"version": "1.17.1"})
        user_words.atomic_write_json(
            repo / "template/user-guard/skills/cae-harness/fleet_profile.json",
            {"runtime_tunables": {"headless_dispatch": {"fleet_state_ttl_sec": 300}}},
        )
        sdir = self.root / "v1171_fleet_state"
        sdir.mkdir()

        def run_fs(*a):
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(sdir)}), redirect_stdout(buf):
                rc = fleet_state.main(["--repo", str(repo), *a])
            return rc, json.loads(buf.getvalue())

        self.assertEqual(run_fs("record-pass1", "--session-id", "p1", "--target-master", "agy")[0], 0)
        self.assertEqual(run_fs("verify-pass2", "--session-id", "p2")[0], 0)
        rc_wrong, out_wrong = run_fs("clear", "--verified", "--session-id", "other")
        self.assertEqual(rc_wrong, 1)
        self.assertEqual(out_wrong["reason"], "active_pass2_claimed")
        with mock.patch.object(fleet_state, "_run_repo_grounding_gate", return_value={"ok": True}):
            rc_ok, out_ok = run_fs("clear", "--verified", "--readback-done", "--session-id", "p2")
        self.assertEqual(rc_ok, 0)
        self.assertTrue(out_ok["readback_verified"])

        # 2) router load_config enforces loopback & rejects upstream credentials; _request_target_path blocks @attacker
        bad_listen = self.root / "bad_listen.json"
        user_words.atomic_write_json(bad_listen, {"listen": "0.0.0.0", "port": 18299})
        with self.assertRaisesRegex(ValueError, "loopback"):
            router.load_config(str(bad_listen))
        bad_up = self.root / "bad_up.json"
        user_words.atomic_write_json(bad_up, {"default_upstream": "https://user:pass" + "@127.0.0.1:9999/v1"})
        with self.assertRaisesRegex(ValueError, "credentials"):
            router.load_config(str(bad_up))
        with self.assertRaises(ValueError):
            router._request_target_path("@attacker.invalid/v1/messages")
        h_del = object.__new__(router.Handler)
        h_del.server = SimpleNamespace(cfg=copy.deepcopy(router.DEFAULTS))
        h_del.path = "/__router/health"
        h_del.command = "DELETE"
        h_del.forward = mock.Mock()
        h_del.do_DELETE()
        h_del.forward.assert_called_once_with(b"")

        # 3) ccswitch_admin inspect_status still checks router_health on corrupt settings.json
        bad_settings = self.root / "corrupt_settings.json"
        bad_settings.write_text("{broken", encoding="utf-8")
        resp_mock = mock.MagicMock(status=200)
        resp_mock.__enter__.return_value = resp_mock
        resp_mock.read.return_value = b'{"ok": true, "port": 8241}'
        with mock.patch.object(admin.urllib.request, "urlopen", return_value=resp_mock):
            st = admin.inspect_status(self.root / "missing.db", bad_settings, router_port=8241)
        self.assertFalse(st["settings_aligned"])
        self.assertEqual(st["router_health"], {"ok": True, "port": 8241})

        # 4) install.py: Codex window backfill, AGY max generation, fenced tail ignore, IPv6/CGNAT redaction
        cx_dir = self.root / "v1171_codex"
        cx_dir.mkdir()
        (cx_dir / "config.toml").write_text('model = "gpt-6.2-sol"\nmodel_reasoning_effort = "max"\n', encoding="utf-8")
        user_words.atomic_write_json(cx_dir / "models_cache.json", {
            "models": [{"slug": "gpt-6.2-sol", "max_context_window": 872000, "supported_reasoning_levels": [{"effort": "max"}]}]
        })
        with redirect_stdout(io.StringIO()):
            installer._sync_codex_frontier_max(cx_dir)
        self.assertIn("model_context_window = 872000", (cx_dir / "config.toml").read_text(encoding="utf-8"))

        best_agy = installer._select_agy_frontier_max_label([
            {"slug": "gemini-3.5-neon-high", "display_name": "Gemini 3.5 Neon High"},
            {"slug": "gemini-4-neon-xhigh", "display_name": "Gemini 4 Neon XHigh"},
        ])
        self.assertEqual(best_agy, "Gemini 4 Neon XHigh")

        fenced_rules = "# 规则\n```md\n## 工具与假自定义\n```\n## 工具与运行环境\n- R10.\n\n## 我的扩展\n- L1.\n"
        idx = installer._find_custom_rules_tail_start(fenced_rules)
        self.assertIsNotNone(idx)
        self.assertTrue(fenced_rules[idx:].startswith("## 我的扩展"))

        v6_sample = "fe80" + "::1a2b:3c4d"
        cgnat_sample = "100.64" + ".12.34"
        redacted = installer._audit_redact(
            f"addr {v6_sample} cgnat {cgnat_sample} proxy socks5://alice:pw" + "@127.0.0.1:1080/p?k=1",
            [], set(),
        )
        self.assertNotIn(v6_sample, redacted)
        self.assertNotIn(cgnat_sample, redacted)
        self.assertNotIn("alice:pw", redacted)

    def test_windows_ccswitch_desktop_binary_detected_without_cli_spawn(self):
        local_app = self.root / "local_app"
        exe = local_app / "Programs" / "CC Switch" / "cc-switch.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"cc-switch desktop fixture\n")
        cli, desktop = installer._windows_app_binaries(self.home, local_app, "cc-switch")
        self.assertIsNone(cli)
        self.assertEqual(desktop, str(exe))

    def test_probe_windows_wsl_uses_errors_replace_for_stderr(self):
        seen_kwargs = {}
        output = "agy\t\ttrue\ncodex\t\ttrue\nclaude\t/opt/bin/claude\ttrue\ndsh\t\tfalse\ncc-switch\t/usr/bin/cc-switch\ttrue\n"

        def fake_run(cmd, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr="wsl utf16-le proxy warning")

        with mock.patch.object(installer.shutil, "which", return_value="wsl.exe"), \
                mock.patch.object(installer, "_wsl_distro_list", side_effect=[["Ubuntu"], ["Ubuntu"]]), \
                mock.patch.object(installer.subprocess, "run", side_effect=fake_run):
            res = installer._probe_windows_wsl()
        self.assertEqual(res["status"], "checked")
        self.assertEqual(seen_kwargs.get("errors"), "replace")

    def test_fleet_state_json_nesting_depth_precheck_across_loaders(self):
        import importlib.util
        fs_path = REPO_ROOT / "template" / "user-guard" / "skills" / "fleet-evolve" / "scripts" / "fleet_state.py"
        spec = importlib.util.spec_from_file_location("fleet_state_depth_test", fs_path)
        fs_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fs_mod)

        # 字符串字面量中的大量括号与转义引号不得误触发深度超限
        fs_mod._check_json_nesting_depth('{"a": "' + ("{" * 100) + '\\"' + ("[" * 100) + '"}')
        deep = "[" * 70 + "]" * 70
        with self.assertRaisesRegex(ValueError, "corrupt state JSON"):
            fs_mod._check_json_nesting_depth(deep)

        # 1) _load_state 拦截深层嵌套（覆盖 Python 3.14 Linux 8MB 栈行为）
        st_file = self.root / "deep_state.json"
        st_file.write_text(deep, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "corrupt state JSON"):
            fs_mod._load_state(st_file)

        # 2) _default_ttl_seconds 跳过深层嵌套损坏 profile 并安全回退 86400
        bad_prof = self.root / "deep_profile.json"
        bad_prof.write_text(deep, encoding="utf-8")
        with mock.patch.dict(os.environ, {"CAE_FLEET_PROFILE": str(bad_prof)}):
            self.assertEqual(fs_mod._default_ttl_seconds(), 86400)

        # 3) _repo_meta 拦截深层嵌套 plugin.json
        fake_repo = self.root / "deep_repo"
        (fake_repo / "template" / "user-guard").mkdir(parents=True)
        (fake_repo / "template" / "user-guard" / "plugin.json").write_text(deep, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "corrupt plugin.json"):
            fs_mod._repo_meta(fake_repo)

    def test_wsl_linux_antigravity_desktop_probe_and_asar_version_audit(self):
        import struct

        # 1) 构造最小合法 Electron app.asar 并验证零进程只读解析 desktop_version
        fake_opt = self.root / "opt_antigravity"
        res_dir = fake_opt / "resources"
        res_dir.mkdir(parents=True)
        fake_bin = fake_opt / "antigravity"
        fake_bin.write_bytes(b"#!/bin/sh\n")
        pkg_bytes = json.dumps({"name": "antigravity", "version": "2.33.0"}).encode("utf-8")
        tree_bytes = json.dumps({
            "files": {"package.json": {"offset": "0", "size": len(pkg_bytes)}}
        }).encode("utf-8")
        padding = (4 - (len(tree_bytes) % 4)) % 4
        header_size = 8 + len(tree_bytes) + padding
        header = struct.pack("<IIII", 4, header_size, 4 + len(tree_bytes) + padding, len(tree_bytes))
        (res_dir / "app.asar").write_bytes(header + tree_bytes + (b"\0" * padding) + pkg_bytes)

        self.assertEqual(installer._read_desktop_app_version(str(fake_bin)), "2.33.0")
        cli_rep = installer._audit_cli({
            "native_installed": True,
            "native_binary": None,
            "native_desktop_binary": str(fake_bin),
            "native_status": "executable_detected",
            "interop_status": "not_detected",
        })
        self.assertEqual(cli_rep["desktop_version"], "2.33.0")

        # 2) _probe_windows_wsl 兼容第 4 列 desktop_binary，且仅安装 WSL 桌面端时 peer_apps_detected 为 True
        wsl_out = (
            "agy\t\ttrue\t/usr/local/bin/antigravity\n"
            "codex\t\tfalse\t\n"
            "claude\t/home/alice/.local/bin/claude\ttrue\t\n"
            "dsh\t\tfalse\t\n"
            "cc-switch\t/usr/bin/cc-switch\ttrue\t\n"
        )
        with mock.patch.object(installer.shutil, "which", return_value="wsl.exe"), \
                mock.patch.object(installer, "_wsl_distro_list", side_effect=[["Ubuntu-24.04"], ["Ubuntu-24.04"]]), \
                mock.patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=wsl_out, stderr="")):
            wsl_res = installer._probe_windows_wsl()
        self.assertEqual(wsl_res["status"], "checked")
        self.assertEqual(wsl_res["apps"]["agy"][0]["desktop_binary"], "/usr/local/bin/antigravity")
        self.assertIsNone(wsl_res["apps"]["agy"][0]["binary"])

        # 3) product.json(ideVersion) 优先于 package.json(version)，支持 bin/ 父目录定位；router 拦截跨站与路径穿越
        fake_share = self.root / "usr_share_antigravity"
        (fake_share / "bin").mkdir(parents=True)
        (fake_share / "resources" / "app").mkdir(parents=True)
        bin_launcher = fake_share / "bin" / "antigravity"
        bin_launcher.write_bytes(b"#!/bin/sh\n")
        (fake_share / "resources" / "app" / "package.json").write_text(json.dumps({"version": "1.96.0"}), encoding="utf-8")
        (fake_share / "resources" / "app" / "product.json").write_text(json.dumps({"ideVersion": "2.33.1"}), encoding="utf-8")
        self.assertEqual(installer._read_desktop_app_version(str(bin_launcher)), "2.33.1")
        wrap_dir = self.root / "usr_local_bin"
        wrap_dir.mkdir(parents=True)
        wrapper_sh = wrap_dir / "antigravity"
        wrapper_sh.write_text(f"#!/usr/bin/env bash\nexec {fake_bin.as_posix()} \"$@\"\n", encoding="utf-8")
        with mock.patch.object(installer, "_is_posix_native_cli_path", return_value=True):
            self.assertEqual(installer._read_desktop_app_version(str(wrapper_sh)), "2.33.0")

        # 4) Antigravity 大窗口下 cp_threshold 不再二次乘 w_scale 膨胀，保持 3 / 5
        real_prof = json.loads((REPO_ROOT / "template/user-guard/skills/cae-harness/fleet_profile.json").read_text(encoding="utf-8"))
        pol_mod = installer._load_adaptive_policy_readonly(REPO_ROOT)
        self.assertIsNotNone(pol_mod)
        ag_hg = pol_mod.resolve_adaptive_handoff_policy(real_prof, client="antigravity", model_name="gemini-4-carbon-high")
        self.assertEqual(ag_hg["advisory_cp_threshold"], 3)
        self.assertEqual(ag_hg["drain_cp_threshold"], 5)
        self.assertGreater(ag_hg["advisory_transcript_mib"], 3)

        for bad_target in (
            "http://127.0.0.1:8241//evil",
            "http://evil.example:8241/v1/messages",
            "/v1/../admin",
            "/v1/%2e%2e/admin",
            "/v1\\..\\admin",
        ):
            with self.assertRaises(ValueError, msg=bad_target):
                router._request_target_path(bad_target)

        for bad_hdrs in (
            {"Host": "evil.rebind.example:8241"},
            {"Host": "127.0.0.1:8241", "Origin": "https://evil.example"},
            {"Host": "127.0.0.1:8241", "Sec-Fetch-Site": "cross-site"},
        ):
            h_sec = object.__new__(router.Handler)
            h_sec.headers = bad_hdrs
            h_sec.send_error = mock.Mock()
            self.assertFalse(h_sec._validate_local_request_headers())
            h_sec.send_error.assert_called_once()
            self.assertEqual(h_sec.send_error.call_args.args[0], 403)

    def test_lifecycle_router_install_scrubbers_handoff_flags_and_fleet_state_side_guard(self):
        jwt_tok = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        pem_hdr = "-----BEGIN " + "PRIVATE KEY-----"
        pem_ftr = "-----END " + "PRIVATE KEY-----"
        pem_key = f"{pem_hdr}\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSj\n{pem_ftr}"
        at_ch = "@"
        dirty_payload = {
            "msg": (
                f"wss://wsuser:wspass{at_ch}example.com/v1?access_token=wstok123 "
                f"socks5://su:sp{at_ch}127.0.0.1:1080 postgresql://du:dp{at_ch}example.org/app "
                f"Authorization: Basic dXNlcjpwYXNzMTIz ApiKey secret_key_987654 "
                f"{jwt_tok} {'ya29.' + 'a0AfH6SMBx1234567890abcdefGHIJ'} {'ASIA' + 'IOSFODNN7EXAMPLE'} {pem_key}"
            ),
            "passwd": "12345678",
            "passphrase": "horse-staple-battery",
            "x-api-key": "sk-" + "router-secret-abcdef123456",
            "safe_router": "via-router",
            "safe_null": "null",
            "token_count": "123456",
            "nested_tuple": ("Bearer secret_bearer_token_123", {"cookie": "sid=cookie_secret_12345"}),
        }
        for scrubber in (user_words._scrub_lifecycle_entry, router._scrub_log_value):
            cleaned = scrubber(dirty_payload)
            for secret_sub in (
                "wsuser:wspass", "wstok123", "su:sp", "du:dp", "dXNlcjpwYXNzMTIz",
                "secret_key_987654", jwt_tok, "ya29.a0Af", "ASIA" + "IOSFODNN7EXAMPLE",
                "MIIEvQIBADAN", "12345678", "horse-staple", "sk-" + "router-secret",
                "secret_bearer_token_123", "cookie_secret_12345",
            ):
                self.assertNotIn(secret_sub, json.dumps(cleaned), msg=f"{scrubber.__name__} leaked {secret_sub}")
            self.assertEqual(cleaned["passwd"], "<redacted>")
            self.assertEqual(cleaned["safe_router"], "via-router")
            self.assertEqual(cleaned["safe_null"], "null")
            self.assertEqual(cleaned["token_count"], "123456")
            self.assertIsInstance(cleaned["nested_tuple"], tuple)

        # installer._audit_redact 保留括号与表格分隔符，擦除多协议凭据与 PEM/JWT
        audit_sample = installer._audit_redact(
            f"[doc](https://au:ap{at_ch}example.com:8443/p?token=q1) | {'/ho' + 'me/otheru'} | {jwt_tok} {pem_key}",
            [],
            {"12345678"},
        )
        self.assertIn("[doc](https://example.com:8443/p)", audit_sample)
        self.assertIn("| <home> |", audit_sample)
        self.assertNotIn("au:ap", audit_sample)
        self.assertNotIn(jwt_tok, audit_sample)
        self.assertIn("<redacted-private-key>", audit_sample)

        # handoff_gate 区分只读 plan 预览与写盘的 --cancel-ticket / --discover-models
        self.assertEqual(
            handoff_gate._classify_run_command("python -B -X utf8 tools/cae_harness.py plan --target ."),
            "readonly_inspect",
        )
        self.assertEqual(
            handoff_gate._classify_run_command("python -B -X utf8 tools/cae_harness.py plan --cancel-ticket t1"),
            "mutating_other",
        )
        self.assertEqual(
            handoff_gate._classify_run_command("python -B -X utf8 tools/cae_harness.py probe --discover-models"),
            "mutating_other",
        )

        # fleet_state: 跨运行侧 (side_mismatch) 或仓库漂移陈旧记录不死锁 propose / record-pass1 / clear
        repo = self.root / "fs_side_repo"
        (repo / "template" / "user-guard" / "skills" / "cae-harness").mkdir(parents=True)
        (repo / "install.py").write_text("# stub\n", encoding="utf-8")
        (repo / "template" / "user-guard" / "plugin.json").write_text(json.dumps({"version": "1.17.5"}), encoding="utf-8")
        (repo / "template" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json").write_text(
            json.dumps({"runtime_tunables": {"headless_dispatch": {"fleet_state_ttl_sec": 600}}}),
            encoding="utf-8",
        )
        sdir = self.root / "fs_side_state"

        def run_fs(*cli_args):
            import contextlib
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(sdir)}), \
                    contextlib.redirect_stdout(buf):
                rc = fleet_state.main(["--repo", str(repo), *cli_args])
            return rc, json.loads(buf.getvalue())

        rc, _ = run_fs("propose", "--session-id", "other_sess", "--summary", "cross-side proposal")
        self.assertEqual(rc, 0)
        with mock.patch.dict(os.environ, {"CAE_FLEET_STATE_DIR": str(sdir)}):
            sf = fleet_state._validated_state_file(repo)
        # 同侧活跃异会话提案仍被拦截
        rc_blk, out_blk = run_fs("propose", "--session-id", "curr_sess", "--summary", "blocked")
        self.assertEqual(rc_blk, 2)
        self.assertEqual(out_blk["reason"], "active_other_session_proposal")
        # 若状态来自另一运行侧（如 WSL vs Windows），同会话无需 --force 即可正常覆盖或归位
        saved_doc = json.loads(sf.read_text(encoding="utf-8"))
        saved_doc["execution_side"] = "wsl_other_side"
        user_words.atomic_write_json(sf, saved_doc)
        self.assertEqual(run_fs("propose", "--session-id", "curr_sess", "--summary", "same-side fresh")[0], 0)

        saved_doc2 = json.loads(sf.read_text(encoding="utf-8"))
        saved_doc2["execution_side"] = "wsl_other_side"
        user_words.atomic_write_json(sf, saved_doc2)
        self.assertEqual(run_fs("record-pass1", "--session-id", "p1_curr", "--target-master", "agy")[0], 0)

        saved_doc3 = json.loads(sf.read_text(encoding="utf-8"))
        saved_doc3["execution_side"] = "wsl_other_side"
        user_words.atomic_write_json(sf, saved_doc3)
        self.assertEqual(run_fs("clear")[0], 0)

    def test_v1187_router_remote_https_enforcement_and_error_log_redaction(self):
        # 1) load_config rejects plaintext remote http upstream when card credentials are injected
        bad_default = self.root / "bad_remote_http_default.json"
        user_words.atomic_write_json(bad_default, {
            "listen": "127.0.0.1",
            "port": 18291,
            "default_upstream": "http://api.example.com/v1",
            "default_key_from_card": "Kimi",
            "routes": [],
        })
        with self.assertRaisesRegex(ValueError, "must use https for non-loopback upstream"):
            router.load_config(str(bad_default))

        bad_route = self.root / "bad_remote_http_route.json"
        user_words.atomic_write_json(bad_route, {
            "listen": "127.0.0.1",
            "port": 18292,
            "default_upstream": "https://api.example.com/v1",
            "default_key_from_card": "Kimi",
            "routes": [{
                "prefix": "deepseek",
                "upstream": "http://api.example.org/anthropic",
                "key_from_card": "DeepSeek",
            }],
        })
        with self.assertRaisesRegex(ValueError, "must use https for non-loopback upstream"):
            router.load_config(str(bad_route))

        # Loopback http upstream with card credentials remains allowed (e.g., local test/relay)
        ok_loopback = self.root / "ok_loopback_http.json"
        user_words.atomic_write_json(ok_loopback, {
            "listen": "127.0.0.1",
            "port": 18293,
            "default_upstream": "http://127.0.0.1:19999/v1",
            "default_key_from_card": "Kimi",
            "routes": [],
        })
        loaded_ok = router.load_config(str(ok_loopback))
        self.assertEqual(loaded_ok["default_upstream"], "http://127.0.0.1:19999/v1")

        # 2) Runtime _forward_locked rejects remote plaintext http before reading card_key
        log_file = self.root / "router_p0_security.log"
        cfg_rt = copy.deepcopy(router.DEFAULTS)
        cfg_rt.update(
            default_upstream="http://api.example.com/v1",
            default_key_from_card="Kimi",
            routes=[],
            log=str(log_file),
            follow_windows_proxy=False,
        )
        h_http = router.Handler.__new__(router.Handler)
        h_http.command = "POST"
        h_http.path = "/v1/messages"
        h_http.headers = {"Content-Type": "application/json"}
        h_http.server = SimpleNamespace(cfg=cfg_rt)
        h_http.send_error = mock.Mock()
        with mock.patch.object(router, "card_key") as ck_mock:
            h_http._forward_locked(json.dumps({"model": "sample-model"}).encode("utf-8"))
        ck_mock.assert_not_called()
        self.assertEqual(h_http.send_error.call_args.args[0], 502)
        self.assertIn("https", h_http.send_error.call_args.args[1])
        rec_http = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec_http["err"], "InsecureUpstreamError")

        # 3) Card key failure and upstream failure never leak raw exception text into logs or 502 responses
        cfg_https = copy.deepcopy(cfg_rt)
        cfg_https["default_upstream"] = "https://api.example.com/v1"
        h_key = router.Handler.__new__(router.Handler)
        h_key.command = "POST"
        h_key.path = "/v1/messages"
        h_key.headers = {"Content-Type": "application/json"}
        h_key.server = SimpleNamespace(cfg=cfg_https)
        h_key.send_error = mock.Mock()
        with mock.patch.object(router, "card_key", side_effect=RuntimeError("LEAK_DIAG_MARKER_991 internal db path")):
            h_key._forward_locked(json.dumps({"model": "sample-model"}).encode("utf-8"))
        self.assertEqual(h_key.send_error.call_args.args[0], 502)
        self.assertNotIn("LEAK_DIAG_MARKER_991", h_key.send_error.call_args.args[1])
        rec_key = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec_key["err"], "RuntimeError")

        h_up = router.Handler.__new__(router.Handler)
        h_up.command = "POST"
        h_up.path = "/v1/messages"
        h_up.headers = {"Content-Type": "application/json"}
        h_up.server = SimpleNamespace(cfg=cfg_https)
        h_up.send_error = mock.Mock()
        with mock.patch.object(router, "card_key", return_value="tok_p0_fixture"), \
                mock.patch.object(router.http.client, "HTTPSConnection", side_effect=OSError("LEAK_UPSTREAM_MARKER_992")):
            h_up._forward_locked(json.dumps({"model": "sample-model"}).encode("utf-8"))
        self.assertEqual(h_up.send_error.call_args.args[0], 502)
        self.assertNotIn("LEAK_UPSTREAM_MARKER_992", h_up.send_error.call_args.args[1])
        rec_up = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec_up["err"], "OSError")
        full_log = log_file.read_text(encoding="utf-8")
        self.assertNotIn("LEAK_DIAG_MARKER_991", full_log)
        self.assertNotIn("LEAK_UPSTREAM_MARKER_992", full_log)

    def test_v212_codex_package_discovery_isolation_schema29_and_sync_notice(self):
        """V2.1.2: 独立 codex-package 优先级、混放 ~/.local 体检告警、schema 29 与 review_ccswitch_schema 及缺缓存汇总提示。"""
        admin = _load_module("ccswitch_admin_v212", REPO_ROOT / "template/claude/router/ccswitch_admin.py")
        self.assertEqual(admin.TESTED_MAX_SCHEMA, 29)

        # 1) Windows _native_cli 与 detect_installed_apps 均须优先独立 codex-package 而非更新 mtime 的 MSIX
        fake_home = self.root / "v212_home"
        pkg_root = fake_home / ".local" / "share" / "codex-package"
        pkg_exe = pkg_root / "bin" / "codex.exe"
        desktop_cache = fake_home / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "old" / "codex.exe"
        msix_exe = self.root / "v212_msix" / "app" / "resources" / "codex.exe"
        for p in (pkg_exe, desktop_cache, msix_exe):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"MZ-stub")
        (pkg_root / "codex-package.json").write_text('{"layoutVersion":1,"version":"0.172.0"}', encoding="utf-8")
        os.utime(pkg_exe, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        os.utime(msix_exe, ns=(1_900_000_000_000_000_000, 1_900_000_000_000_000_000))

        native_path_cls = type(fake_home)
        with mock.patch.object(installer.os, "name", "nt"), \
             mock.patch.object(installer, "Path", native_path_cls), \
             mock.patch.object(installer.shutil, "which", side_effect=lambda cmd, *a, **kw: str(desktop_cache) if cmd in ("codex", "codex.exe") else None), \
             mock.patch.object(installer, "_first_executable", side_effect=lambda cands, windows=False: next((str(c) for c in cands if os.path.isfile(str(c))), None)), \
             mock.patch.object(native_path_cls, "home", return_value=fake_home), \
             mock.patch.object(installer, "_find_windows_codex_package", return_value=(str(msix_exe), None)), \
             mock.patch.object(installer, "_probe_windows_wsl", return_value=None), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": str(fake_home / "AppData" / "Local")}, clear=False):
            self.assertEqual(installer._native_cli("codex", fake_home), str(pkg_exe))
            det = installer.detect_installed_apps()
            self.assertEqual(native_path_cls(det["codex"]["native_binary"]).resolve(), pkg_exe.resolve())

        # 2) audit_upgrade 检测混放 ~/.local 的 codex-package 告警与超基线 CC Switch schema 建议码
        sbx_home = self.root / "v212_audit_home"
        mixed_bin = sbx_home / ".local" / "bin" / ("codex.exe" if os.name == "nt" else "codex")
        mixed_bin.parent.mkdir(parents=True, exist_ok=True)
        mixed_bin.write_bytes(b"stub")
        if os.name != "nt":
            mixed_bin.chmod(0o755)
        (sbx_home / ".local" / "codex-package.json").write_text('{"layoutVersion":1}', encoding="utf-8")

        sbx_cc_db = sbx_home / ".cc-switch" / "cc-switch.db"
        sbx_cc_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(sbx_cc_db)) as con:
            con.execute("CREATE TABLE providers (id TEXT PRIMARY KEY, app_type TEXT, name TEXT, settings_config TEXT, is_current INTEGER DEFAULT 0)")
            con.execute("CREATE TABLE prompts (id TEXT PRIMARY KEY, app_type TEXT, name TEXT, enabled INTEGER DEFAULT 0)")
            con.execute("CREATE TABLE skills (id TEXT PRIMARY KEY, name TEXT, enabled_claude INTEGER DEFAULT 0, enabled_codex INTEGER DEFAULT 0)")
            con.execute(f"PRAGMA user_version={admin.TESTED_MAX_SCHEMA + 1}")
            con.commit()

        fake_det = {
            "platform": sys.platform, "is_wsl": False, "wsl_probe": None,
            "antigravity": {"native_installed": False, "native_binary": None, "interop_available": False},
            "codex": {"native_installed": True, "native_binary": str(mixed_bin), "interop_available": False},
            "claude": {"native_installed": False, "native_binary": None, "interop_available": False},
            "dsh": {"native_installed": False, "native_binary": None, "interop_available": False},
            "cc_switch": {"native_installed": True, "native_desktop_binary": "cc-switch", "interop_available": False},
        }
        with mock.patch.object(installer.Path, "home", return_value=sbx_home):
            rep = installer.audit_upgrade_status(
                REPO_ROOT, app_scope="all",
                codex_root=sbx_home / ".codex", claude_root=sbx_home / ".claude",
                dsh_home=sbx_home / ".dsh", ccswitch_db=sbx_cc_db,
                detection=fake_det, is_sandbox_run=True,
            )
        self.assertIsNotNone(rep["codex"]["package_isolation_warning"])
        self.assertIn("codex-package", rep["codex"]["package_isolation_warning"])
        self.assertIn("review_ccswitch_schema", rep["summary"]["recommended_actions"])
        self.assertFalse(rep["summary"]["all_aligned"])

        # 3) --sync-model-defaults 在缺少 models_cache.json 时末尾输出醒目 [!] 提示
        sync_cx = self.root / "v212_sync_codex"
        sync_cx.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                sys.executable, "-B", "-X", "utf8", str(REPO_ROOT / "install.py"),
                "--app", "codex", "--codex-home", str(sync_cx),
                "--sync-model-defaults", "--no-codex-hooks", "--no-zotero", "--skip-test", "--yes",
            ],
            capture_output=True, text=True, encoding="utf-8", cwd=str(REPO_ROOT), timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[!] 未找到有效 Codex 模型目录", proc.stdout)
        self.assertIn("--sync-model-defaults --skip-test", proc.stdout)


if __name__ == "__main__":
    unittest.main()
