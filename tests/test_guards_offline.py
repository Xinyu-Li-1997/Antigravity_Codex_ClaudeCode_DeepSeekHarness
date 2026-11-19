"""
test_guards_offline.py - 防空转门禁 (status_guard) 与防刷屏门禁 (reply_repeat_guard) 离线单元测试套件

覆盖验证：
1. status_guard 对未变化日志文件的同参重复读取精准 deny，文件更新后自动恢复 allow；
2. 【防翻页误杀】长输出文件（如 .system_generated/steps/17/output.txt 或长 .log）分段翻页读取（不同 StartLine/EndLine/ByteOffset）100% allow；
3. 【防变参检索误杀】run_command 先 grep 再 tail 同一日志（或含 powershell/bash 包装器）按命令签名区分，变参 allow、同参重复 deny；
4. 【防 manage_task 永久锁死】manage_task(Action="status") 联动 .system_generated/tasks/<TaskId>.log，日志新增输出后立即恢复 allow；
5. 【防 WSL 死锁与业务文件误伤】wsl 查看子系统内部不可达路径不锁死；普通配置 status.json / test_result.json 及插件自身状态目录绝不误拦截；
6. reply_repeat_guard 连续 3 次同质短等待回复精准 continue 拦截，且重入 continuationActive=True 时 100% allow 熔断防死循环；
7. 【防误杀真实进展】连续 3 次包含不同迭代步数/残差数值的短回复、或含等待用户指示/提问的回复绝不误拦截。
"""

import concurrent.futures
import json
import os
import re
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

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

import reply_repeat_guard  # noqa: E402
import status_guard  # noqa: E402
import user_words  # noqa: E402


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


class TestGuardsOffline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        sandbox_root = Path(self.tmp_obj.name).resolve()
        self.tmp_root = sandbox_root / "workspace"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.fake_home = sandbox_root / "home"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.plugin_data = self.tmp_root / "plugin_data"
        # 原有夹具独立验证路径、交接和轮询；派工票据由下方专用测试启用并覆盖。
        clean_env = _scrub_host_env(
            HOME=str(self.fake_home),
            USERPROFILE=str(self.fake_home),
            PLUGIN_DATA=str(self.plugin_data),
            USER_GUARD_DISABLE_TICKET_GATE="1",
        )
        self.ticket_gate_env = unittest.mock.patch.dict(os.environ, clean_env, clear=True)
        self.ticket_gate_env.start()
        self._home_patch = unittest.mock.patch.object(Path, "home", return_value=self.fake_home)
        self._home_patch.start()

    def tearDown(self) -> None:
        self._home_patch.stop()
        self.ticket_gate_env.stop()
        self.tmp_obj.cleanup()

    def test_dsh_installer_sandbox_requires_explicit_contained_peer_directories(self):
        base = {'conversationId': 'dsh-guard', 'cwd': str(self.tmp_root), 'workspacePaths': [str(self.tmp_root)]}
        cases = (
            ('--app dsh --dsh-home inside/dsh', 'allow'),
            ('--app auto --dsh-home inside/dsh', 'allow'),
            ('--app dsh', 'deny'),
            ('--app dsh --dsh-home /outside/dsh', 'deny'),
            ('--app all --target-dir inside/agy --codex-home inside/codex --claude-home inside/claude --dsh-home inside/dsh', 'allow'),
            ('--app all --target-dir inside/agy --codex-home inside/codex --claude-home inside/claude --dsh-home /outside/dsh', 'deny'),
            ('--app claude --claude-home inside/claude --dsh-home inside/dsh', 'deny'),
            ('--app dsh --dsh-home inside/dsh --claude-home inside/claude', 'deny'),
        )
        with unittest.mock.patch.dict(os.environ, {'CAE_HARNESS_DEPTH': '1'}):
            for flags, expected in cases:
                with self.subTest(flags=flags):
                    result = status_guard.handle_pre_tool_use({**base, 'toolCall': {'name': 'run_command',
                        'args': {'CommandLine': 'python -B install.py ' + flags + ' --no-zotero --skip-test'}}})
                    self.assertEqual(result['decision'], expected)

    def test_01_status_guard_unchanged_deny_and_updated_allow(self) -> None:
        """未变动日志文件同参重复读取被 deny，文件产生增量输出后恢复 allow"""
        conv_id = "guard-001"
        log_file = self.tmp_root / "cfd_solver.log"
        log_file.write_text("Iter 1: residual = 1.0e-1\n", encoding="utf-8")

        payload = {
            "conversationId": conv_id,
            "cwd": str(self.tmp_root),
            "toolCall": {"name": "read_file", "args": {"Path": str(log_file), "StartLine": 1, "EndLine": 100}},
        }

        self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "allow")
        r_dup = status_guard.handle_pre_tool_use(payload)
        self.assertEqual(r_dup["decision"], "deny")
        self.assertIn("Reactive Wakeup", r_dup["reason"])

        # 算例追加新输出后自动恢复放行
        time.sleep(0.01)
        log_file.write_text("Iter 1: residual = 1.0e-1\nIter 2: residual = 3.2e-4\n", encoding="utf-8")
        self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "allow")

    def test_02_status_guard_allows_pagination_on_large_output(self) -> None:
        """分段翻页读取 .system_generated/steps/17/output.txt 或大日志文件绝不误杀"""
        conv_id = "guard-pagination-002"
        step_out = self.tmp_root / ".system_generated" / "steps" / "17" / "output.txt"
        step_out.parent.mkdir(parents=True, exist_ok=True)
        step_out.write_text("Line\n" * 2000, encoding="utf-8")

        p_page1 = {
            "conversationId": conv_id,
            "toolCall": {"name": "read_file", "args": {"Path": str(step_out), "StartLine": 1, "EndLine": 800}},
        }
        p_page2 = {
            "conversationId": conv_id,
            "toolCall": {"name": "read_file", "args": {"Path": str(step_out), "StartLine": 801, "EndLine": 1600}},
        }
        p_page3_offset = {
            "conversationId": conv_id,
            "toolCall": {
                "name": "read_file",
                "args": {"Path": str(step_out), "StartLine": 801, "EndLine": 1600, "ByteOffset": 46080},
            },
        }

        self.assertEqual(status_guard.handle_pre_tool_use(p_page1)["decision"], "allow")
        self.assertEqual(status_guard.handle_pre_tool_use(p_page2)["decision"], "allow", "翻读第 2 页必须放行！")
        self.assertEqual(status_guard.handle_pre_tool_use(p_page3_offset)["decision"], "allow", "ByteOffset 续读必须放行！")
        # 原地重复读第 1 页则精准拦截
        self.assertEqual(status_guard.handle_pre_tool_use(p_page1)["decision"], "deny")

    def test_03_status_guard_run_command_params_and_shell_wrappers(self) -> None:
        """run_command 支持变参放行（先 grep 后 tail）与包装器剥离同参拦截"""
        conv_id = "guard-cmd-003"
        out_file = self.tmp_root / "output.txt"
        out_file.write_text("INFO start\nERROR divergence at cell 42\n", encoding="utf-8")

        p_grep = {
            "conversationId": conv_id,
            "cwd": str(self.tmp_root),
            "toolCall": {"name": "run_command", "args": {"CommandLine": "grep ERROR output.txt"}},
        }
        p_tail = {
            "conversationId": conv_id,
            "cwd": str(self.tmp_root),
            "toolCall": {"name": "run_command", "args": {"CommandLine": 'bash -c "tail -n 20 output.txt"'}},
        }

        self.assertEqual(status_guard.handle_pre_tool_use(p_grep)["decision"], "allow")
        self.assertEqual(status_guard.handle_pre_tool_use(p_tail)["decision"], "allow", "变参切换命令必须放行")
        self.assertEqual(status_guard.handle_pre_tool_use(p_tail)["decision"], "deny", "完全相同的包装命令重复轮询必须阻断")

    def test_04_manage_task_status_tracks_real_task_log_updates(self) -> None:
        """manage_task(Action='status') 联动后台任务日志指纹，日志更新后立即恢复放行，不永久锁死"""
        conv_id = "guard-task-004"
        sg_dir = self.tmp_root / "brain" / conv_id / ".system_generated"
        tp = sg_dir / "logs" / "transcript.jsonl"
        task_log = sg_dir / "tasks" / "task-323.log"
        tp.parent.mkdir(parents=True, exist_ok=True)
        task_log.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text("", encoding="utf-8")
        task_log.write_text("Step 10/100...\n", encoding="utf-8")

        payload = {
            "conversationId": conv_id,
            "transcriptPath": str(tp),
            "toolCall": {"name": "manage_task", "args": {"Action": "status", "TaskId": "task-323"}},
        }

        self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "allow")
        self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "deny", "任务日志未变时应拦截空转轮询")

        # 后台算例向 task-323.log 写入新进展 -> 必须自动放行！
        time.sleep(0.01)
        task_log.write_text("Step 10/100...\nStep 80/100...\n", encoding="utf-8")
        self.assertEqual(
            status_guard.handle_pre_tool_use(payload)["decision"],
            "allow",
            "后台任务日志更新后 manage_task(status) 必须自动放行，严禁永久封死！",
        )

    def test_05_wsl_unreachable_paths_and_normal_json_files_not_blocked(self) -> None:
        """WSL 子系统不可达路径不锁死，普通代码配置 status.json / test_result.json 不误伤"""
        conv_id = "guard-safe-005"

        # 1. WSL 内部路径在宿主不可见，连续查询不应被 non_existent 锁死
        wsl_payload = {
            "conversationId": conv_id,
            "cwd": str(self.tmp_root),
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": 'wsl bash -c "tail -n 20 /nonexistent_wsl_sim_dir/run.log"'},
            },
        }
        self.assertEqual(status_guard.handle_pre_tool_use(wsl_payload)["decision"], "allow")
        self.assertEqual(status_guard.handle_pre_tool_use(wsl_payload)["decision"], "allow")

        # 2. 普通项目文件 http_status.json / test_result_schema.json 连续读取完全放行
        for fname in ("http_status.json", "test_result_schema.json", "solver_core.f90"):
            f = self.tmp_root / fname
            f.write_text('{"ok": true}\n', encoding="utf-8")
            p = {"conversationId": conv_id, "toolCall": {"name": "read_file", "args": {"Path": str(f)}}}
            self.assertEqual(status_guard.handle_pre_tool_use(p)["decision"], "allow")
            self.assertEqual(status_guard.handle_pre_tool_use(p)["decision"], "allow", f"普通文件 {fname} 绝不应被拦截")

    def test_06_repeat_guard_blocks_homogeneous_and_respects_continuation_active(self) -> None:
        """连续 3 次同质短等待回复触发拦截，且重入 continuationActive=True 时立即熔断放行防死循环"""
        conv_id = "repeat-006"
        tp = self.tmp_root / "transcript.jsonl"
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "跑算例"})

        replies = [
            "后台算例仍在运行中，请稍候。",
            "后台算例仍在运行中，请耐心等待。",
            "后台算例还在运行中，请稍候。",
        ]
        decisions = []
        for idx, text in enumerate(replies, start=1):
            _append_jsonl(tp, {"step_index": idx, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": text})
            res = reply_repeat_guard.handle_stop_repeat_guard(
                {
                    "conversationId": conv_id,
                    "terminationReason": "NO_TOOL_CALL",
                    "continuationActive": False,
                    "transcriptPath": str(tp),
                }
            )
            decisions.append(res["decision"])

        self.assertEqual(decisions, ["allow", "allow", "continue"])

        # 紧接着宿主携带 continuationActive=True 重入 Stop -> 必须无条件放行防无限唤醒！
        _append_jsonl(tp, {"step_index": 4, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "后台仍在运行，现挂起等待通知。"})
        res_reentry = reply_repeat_guard.handle_stop_repeat_guard(
            {
                "conversationId": conv_id,
                "terminationReason": "NO_TOOL_CALL",
                "continuationActive": True,
                "transcriptPath": str(tp),
            }
        )
        self.assertEqual(res_reentry["decision"], "allow")

    def test_07_repeat_guard_allows_distinct_numerical_progress_and_user_questions(self) -> None:
        """包含不同迭代步数/残差的真实进度汇报，以及等待用户裁决的回复，绝不误判为刷屏"""
        conv_id = "repeat-progress-007"
        tp = self.tmp_root / "transcript_prog.jsonl"
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "监控收敛"})

        progress_replies = [
            "算例正在计算：已完成 100 步，当前残差 1.2e-2，继续等待完成。",
            "算例正在计算：已完成 500 步，当前残差 4.5e-5，继续等待完成。",
            "算例正在计算：已完成 900 步，当前残差 8.1e-8，继续等待完成。",
            "网格切换方案已就绪，后台尚未完成合并，等待您的指示。",
        ]
        for idx, text in enumerate(progress_replies, start=1):
            _append_jsonl(tp, {"step_index": idx, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": text})
            res = reply_repeat_guard.handle_stop_repeat_guard(
                {
                    "conversationId": conv_id,
                    "terminationReason": "NO_TOOL_CALL",
                    "continuationActive": False,
                    "transcriptPath": str(tp),
                }
            )
            self.assertEqual(res["decision"], "allow", f"第 {idx} 条真实进展或用户请示不应被误拦截")

        # 补充验证：无数值但属于不同子任务主语的短状态汇报，绝不误判为同质复读
        conv_id_distinct = "repeat-distinct-subject-007b"
        tp_distinct = self.tmp_root / "transcript_distinct.jsonl"
        _append_jsonl(tp_distinct, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "全流程运行"})
        distinct_subject_replies = [
            "前处理非结构网格划分仍在运行中，请稍候。",
            "三维可压缩流场求解器正在计算，请耐心等待。",
            "后处理流线与涡量场导出还在运行中，请稍候。",
        ]
        for idx, text in enumerate(distinct_subject_replies, start=1):
            _append_jsonl(tp_distinct, {"step_index": idx, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": text})
            res = reply_repeat_guard.handle_stop_repeat_guard(
                {
                    "conversationId": conv_id_distinct,
                    "terminationReason": "NO_TOOL_CALL",
                    "continuationActive": False,
                    "transcriptPath": str(tp_distinct),
                }
            )
            self.assertEqual(res["decision"], "allow", f"不同子任务主语第 {idx} 条状态汇报不应被误拦截")

    def test_08_workspace_boundary_allows_project_artifacts_and_cross_env_paths(self) -> None:
        """项目目录内写入、跨端 D:/ <-> /mnt/d/ 同项目等价路径、会话专属制品与标准临时目录 100% 放行"""
        conv_id = "boundary-allow-008"
        proj = self.tmp_root / "myproj"
        proj.mkdir()
        art_dir = self.tmp_root / "brain" / conv_id
        tp = art_dir / ".system_generated" / "logs" / "transcript.jsonl"

        base_payload = {
            "conversationId": conv_id,
            "cwd": str(proj),
            "workspacePaths": [str(proj)],
            "artifactDirectoryPath": str(art_dir),
            "transcriptPath": str(tp),
        }

        for target_p in (
            str(proj / "src" / "solver.py"),
            "docs/report.md",
            str(art_dir / "scratch" / "calc.py"),
            str(self.plugin_data / "state" / "note.json"),
        ):
            res = status_guard.handle_pre_tool_use(
                {**base_payload, "toolCall": {"name": "write_file", "args": {"Path": target_p}}}
            )
            self.assertEqual(res["decision"], "allow", f"允许范围内的路径 {target_p} 必须放行")

        # 验证非临时目录项目支持跨端等价路径、系统临时目录、子代理/宿主对话记忆及 ~/.cache 正常放行
        cross_payload = {
            "conversationId": conv_id,
            "cwd": r"X:\Projects\myproj",
            "workspacePaths": [r"X:\Projects\myproj"],
        }
        for allowed_runtime_path in (
            "/mnt/x/Projects/myproj/src/a.py",
            str(Path(tempfile.gettempdir()) / "cae_stage_check.json"),
            str(Path.home() / ".gemini/antigravity/brain/subagent-uuid-001/scratch/note.md"),
            str(Path.home() / ".gemini/antigravity/plugin_data/user-guard/state/conv_user_words.md"),
            str(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "memories/proj_memo.json"),
            str(Path.home() / ".cache/solver-venv/pip-wheel.json"),
        ):
            self.assertEqual(
                status_guard.handle_pre_tool_use(
                    {**cross_payload, "toolCall": {"name": "write_file", "args": {"Path": allowed_runtime_path}}}
                )["decision"],
                "allow",
                f"临时目录、对话记忆与工具缓存路径应当放行: {allowed_runtime_path}",
            )

    def test_09_workspace_boundary_blocks_external_mutations_worktrees_and_link_escapes(self) -> None:
        """项目目录外业务写删、外部 .codex-worktrees、全局核心配置篡改与软链接逃逸：交互清晰 force_ask、无头子任务硬 deny"""
        import subprocess as _sp

        conv_id = "boundary-block-009"
        proj = self.tmp_root / "myproj"
        outside = self.tmp_root / "outside_dir"
        proj.mkdir()
        outside.mkdir()

        base_payload = {
            "conversationId": conv_id,
            "cwd": str(proj),
            "workspacePaths": [str(proj)],
        }

        # 1) 交互会话：外部业务目录与全局核心配置文件触发 force_ask，且弹窗清晰展示动作与目标路径
        r_ext_write = status_guard.handle_pre_tool_use(
            {
                **base_payload,
                "toolCall": {
                    "name": "write_file",
                    "args": {"Path": str(self.tmp_root / "myproj_bak" / "a.py"), "Content": "x = 1\n"},
                },
            }
        )
        self.assertEqual(r_ext_write["decision"], "force_ask")
        self.assertIn("具体写删动作：", r_ext_write["reason"])
        self.assertIn("项目外目标路径：", r_ext_write["reason"])

        for protected_cfg in (
            "/home/alice/.codex/config.toml",
            "/home/alice/.codex/AGENTS.md",
            "/home/alice/.gemini/config/settings.json",
        ):
            r_ext_cfg = status_guard.handle_pre_tool_use(
                {**base_payload, "toolCall": {"name": "edit_file", "args": {"Path": protected_cfg, "Target": "a", "Replacement": "b"}}}
            )
            self.assertEqual(r_ext_cfg["decision"], "force_ask", f"全局核心配置必须受保护: {protected_cfg}")

        # 2) 交互会话：外部 git worktree add / 外部删除（含 wsl 直调）/ 非沙箱全局 install.py 均返回含意图说明的 force_ask
        win_proj_payload = {
            "conversationId": conv_id,
            "cwd": r"X:\Projects\myproj",
            "workspacePaths": [r"X:\Projects\myproj"],
        }
        for bad_cmd in (
            'git worktree add "../.codex-worktrees/myproj/branch-a"',
            r'Remove-Item -Force "X:\Projects\.codex-worktrees"',
            'wsl.exe -d Ubuntu -- rm -rf /mnt/x/Projects/.codex-worktrees',
            'python -B install.py',
            'python -B install.py --app codex',
            r'python -B "X:\Projects\myproj\install.py"',
        ):
            r_cmd = status_guard.handle_pre_tool_use(
                {
                    **win_proj_payload,
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": bad_cmd, "toolSummary": "清理或同步项目外目标测试"},
                    },
                }
            )
            self.assertEqual(r_cmd["decision"], "force_ask", f"越界命令必须触发 force_ask: {bad_cmd}")
            self.assertIn("当前任务意图：清理或同步项目外目标测试", r_cmd["reason"])
            self.assertIn("具体写删动作：", r_cmd["reason"])
            self.assertIn("触发语句：", r_cmd["reason"])

        # 3) 安全对照：--dry-run、项目内沙箱安装、只读查询外部目录、含外部路径字面量的 git commit 均放行
        for safe_cmd in (
            "python -B install.py --dry-run",
            "python -B install.py --probe-apps",
            r'python -B install.py --target-dir "X:\Projects\myproj\.sandbox\plugin" --no-zotero',
            r'python -B install.py --app codex --codex-home "X:\Projects\myproj\.sandbox\codex" --no-zotero',
            r'Get-Item -Force "X:\Projects\.codex-worktrees"',
            r'git commit -m "fix: block X:\Projects\.codex-worktrees and ../.codex-worktrees"',
        ):
            r_ok = status_guard.handle_pre_tool_use(
                {**win_proj_payload, "toolCall": {"name": "run_command", "args": {"CommandLine": safe_cmd}}}
            )
            self.assertEqual(r_ok["decision"], "allow", f"安全命令不应被误拦截: {safe_cmd}")

        # 4) 跨端无头子任务（CAE_HARNESS_DEPTH=1）：越界操作一律硬 deny
        old_depth = os.environ.get("CAE_HARNESS_DEPTH")
        os.environ["CAE_HARNESS_DEPTH"] = "1"
        try:
            r_deny_w = status_guard.handle_pre_tool_use(
                {**base_payload, "toolCall": {"name": "write_file", "args": {"Path": str(outside / "x.txt")}}}
            )
            r_deny_wt = status_guard.handle_pre_tool_use(
                {
                    **win_proj_payload,
                    "toolCall": {"name": "run_command", "args": {"CommandLine": 'git worktree add "../.codex-worktrees/wt"'}},
                }
            )
            self.assertEqual(r_deny_w["decision"], "deny")
            self.assertEqual(r_deny_wt["decision"], "deny")
        finally:
            if old_depth is None:
                os.environ.pop("CAE_HARNESS_DEPTH", None)
            else:
                os.environ["CAE_HARNESS_DEPTH"] = old_depth

        # 5) 软链接 / 目录联接逃逸拦截
        escape_link = proj / "escape_link"
        link_created = False
        try:
            escape_link.symlink_to(outside, target_is_directory=True)
            link_created = True
        except OSError:
            if os.name == "nt":
                env = os.environ.copy()
                env["UG_LINK"] = str(escape_link)
                env["UG_TARGET"] = str(outside)
                p = _sp.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "New-Item -ItemType Junction -Path $env:UG_LINK -Target $env:UG_TARGET | Out-Null",
                    ],
                    env=env,
                    capture_output=True,
                    timeout=10,
                )
                link_created = p.returncode == 0
        if link_created:
            r_link = status_guard.handle_pre_tool_use(
                {**base_payload, "toolCall": {"name": "write_file", "args": {"Path": str(escape_link / "escaped.txt")}}}
            )
            self.assertEqual(r_link["decision"], "force_ask", "通过项目内链接写向项目外目录必须被拦截")

    def test_10_real_world_readonly_python_and_wsl_venv_never_false_positive(self) -> None:
        """真实场景回归：含 .md 字面量、子代理 brain 只读检查、WSL ~/.cache 解释器与数组 .copy() 零误报；真实外部写必拦截"""
        # 场景 A: 项目内 python3 -c 检查 .md 文件名列表并只读打开子代理 brain/<sub-id>/transcript.jsonl
        linux_proj_payload = {
            "conversationId": "parent-conv-010a",
            "cwd": "/home/alice/cfd_proj",
            "workspacePaths": ["/home/alice/cfd_proj"],
        }
        cmd_subagent_check = (
            "python3 -c '\n"
            "import sys\n"
            'mods = ["AGENTS.md", "docs/README.md", "docs/handover/START-HERE.en.md"]\n'
            'with open("/home/alice/.gemini/antigravity/brain/subagent-g5/.system_generated/logs/transcript.jsonl", "r", encoding="utf-8") as f:\n'
            "    print(len(f.readlines()), len(mods))\n"
            "'"
        )
        res_a = status_guard.handle_pre_tool_use(
            {
                **linux_proj_payload,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd_subagent_check, "toolSummary": "Check wording hits and subagent state"},
                },
            }
        )
        self.assertEqual(res_a["decision"], "allow", "只读检查 .md 列表与子代理轨迹严禁误报")

        # 场景 B: Windows 经 wsl.exe -e bash -lc 调用 $HOME/.cache/.../python 执行含 .copy()、.open('rb') 与 > 比较的只读后处理脚本
        win_cfd_payload = {
            "conversationId": "parent-conv-010b",
            "cwd": r"x:/Projects/cfd_post/case_data 数据处理",
            "workspacePaths": [r"x:/Projects/cfd_post/case_data 数据处理"],
        }
        cmd_wsl_venv_readonly = (
            "wsl.exe -e bash -lc '$HOME/.cache/step1-venv/bin/python -B -c \"\n"
            "from pathlib import Path\n"
            "arr = [1, 2, 3].copy()\n"
            'p = Path(\\"data/simbf.bin\\")\n'
            'if len(arr) > 0 and p.exists():\n'
            '    with p.open(\\"rb\\") as st:\n'
            "        print(len(st.read()))\n"
            "\"'"
        )
        res_b = status_guard.handle_pre_tool_use(
            {
                **win_cfd_payload,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd_wsl_venv_readonly, "toolSummary": "Inspect profile sample stats"},
                },
            }
        )
        self.assertEqual(res_b["decision"], "allow", "WSL 缓存 venv 只读脚本（含 .copy() 与 > 比较）严禁误报")

        # 对照场景 C: 同样通过 WSL venv python -c，但脚本内真正向项目外业务目录 open(..., 'w') 写文件 -> 必须精准拦截并说明原因
        cmd_wsl_venv_real_write = (
            "wsl.exe -e bash -lc '$HOME/.cache/step1-venv/bin/python -B -c \""
            'open(\\"/mnt/x/Projects/outside_garbage.txt\\", \\"w\\").write(\\"leak\\")'
            "\"'"
        )
        res_c = status_guard.handle_pre_tool_use(
            {
                **win_cfd_payload,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": cmd_wsl_venv_real_write, "toolSummary": "Export debug dump"},
                },
            }
        )
        self.assertEqual(res_c["decision"], "force_ask", "Python 内联真实写向项目外业务盘必须拦截")
        self.assertIn("当前任务意图：Export debug dump", res_c["reason"])
        self.assertIn("Python 写入/修改文件", res_c["reason"])
        self.assertIn("/mnt/x/Projects/outside_garbage.txt", res_c["reason"])

    def test_11_handoff_gate_image_flood_drain_freeze_and_stop_goal_verification(self) -> None:
        """验证单回合读图过载自动进 drain/freeze、派工/读图禁令、R4 优先级、本地 commit 及 Stop /goal 终审"""
        import session_context_loader

        conv_id = "guard-handoff-011"
        ws_dir = self.tmp_root / "workspace"
        brain_dir = self.tmp_root / ".gemini" / "antigravity" / "brain" / conv_id
        ws_dir.mkdir(parents=True)
        brain_dir.mkdir(parents=True)
        tp = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/goal\n逐批执行并核图"})
        session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(tp)})

        # 构造 4 张合计 >100MB 的模拟 PNG 并在单回合内连续 read_file，第 4 张跨过 100MB 阈值当场进 drain 并阻断读图
        for i in range(4):
            img = ws_dir / f"page_{i}.png"
            with open(img, "wb") as f:
                f.seek(26 * 1024 * 1024 - 1)
                f.write(b"\0")
            res = status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "transcriptPath": str(tp),
                    "toolCall": {"name": "read_file", "args": {"Path": str(img)}},
                }
            )
            if i < 3:
                self.assertEqual(res["decision"], "allow")
            else:
                self.assertEqual(res["decision"], "deny")
                self.assertIn("禁止继续调用 `read_file` 看图", res["reason"])

        # 非图片文本 read_file 仍放行
        txt_file = ws_dir / "summary.txt"
        txt_file.write_text("ok", encoding="utf-8")
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "read_file", "args": {"Path": str(txt_file)}},
                }
            )["decision"],
            "allow",
        )

        # drain 期禁止新开子代理 / swarm / browser / cae_harness 派工 / git push
        for tool_nm in ("invoke_subagent", "swarm", "schedule", "browser_click"):
            r_blk = status_guard.handle_pre_tool_use(
                {"conversationId": conv_id, "cwd": str(ws_dir), "toolCall": {"name": tool_nm, "args": {}}}
            )
            self.assertEqual(r_blk["decision"], "deny", f"{tool_nm} 必须在 drain 期被阻断")

        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": "python cae_harness.py task --kind code"}},
                }
            )["decision"],
            "deny",
        )
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": "git push origin main"}},
                }
            )["decision"],
            "deny",
        )
        # drain 期禁止多子句串联偷跑 cae_harness 派工、& git push 与 bash -lc 包装 push
        for sneaky_cmd in (
            "python cae_harness.py probe && python cae_harness.py task --kind review",
            "& git push origin main",
            'bash -lc "git status && git push origin main"',
        ):
            self.assertEqual(
                status_guard.handle_pre_tool_use(
                    {
                        "conversationId": conv_id,
                        "cwd": str(ws_dir),
                        "workspacePaths": [str(ws_dir)],
                        "toolCall": {"name": "run_command", "args": {"CommandLine": sneaky_cmd}},
                    }
                )["decision"],
                "deny",
                f"drain 期必须硬阻断变体派工/推送命令: {sneaky_cmd}",
            )

        # 含引号内 '>' 的 git commit -m "fix > ok" 须识别为本地 commit 放行（且不消耗业务修改预算）
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": 'git add -A && git commit -m "chore: > ok"'}},
                }
            )["decision"],
            "allow",
        )

        # drain 中普通项目外请求仍走 R4 审批
        outside_path = self.tmp_root / "outside_dir" / "bad.txt"
        res_r4 = status_guard.handle_pre_tool_use(
            {
                "conversationId": conv_id,
                "cwd": str(ws_dir),
                "workspacePaths": [str(ws_dir)],
                "toolCall": {"name": "write_file", "args": {"Path": str(outside_path), "Content": "x"}},
            }
        )
        self.assertEqual(res_r4["decision"], "force_ask")

        # 消耗 8 次 drain 业务写动作进入 freeze；freeze 后普通源码 write_file 阻断，但交接文档写入放行
        for k in range(8):
            self.assertEqual(
                status_guard.handle_pre_tool_use(
                    {
                        "conversationId": conv_id,
                        "cwd": str(ws_dir),
                        "workspacePaths": [str(ws_dir)],
                        "toolCall": {"name": "write_file", "args": {"Path": str(ws_dir / f"src_{k}.py"), "Content": "pass"}},
                    }
                )["decision"],
                "allow",
            )
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "write_file", "args": {"Path": str(ws_dir / "src_8.py"), "Content": "pass"}},
                }
            )["decision"],
            "deny",
        )

        # freeze 阶段放行带引号路径的 cae_harness.py probe，阻断带引号路径的 cae_harness.py task 派工
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": 'python -B "d:/repo/cae_harness.py" probe'}},
                }
            )["decision"],
            "allow",
        )
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": 'python -B "d:/repo/cae_harness.py" task --kind review'}},
                }
            )["decision"],
            "deny",
        )
        # freeze 阶段放行只读 find 检索，拦截带删除副作用的 find -delete
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": 'find . -maxdepth 2 -name "*.py"'}},
                }
            )["decision"],
            "allow",
        )
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": 'find . -name "*.tmp" -delete'}},
                }
            )["decision"],
            "deny",
        )
        # freeze 阶段放行 git 操作 cae_harness.py 文件及 wsl bash -lc 只读 git status（不得被全局正则误杀）
        for safe_freeze_cmd in (
            "git diff template/cae_harness.py",
            'git add template/cae_harness.py && git commit -m "fix cae_harness.py"',
            'wsl -d Ubuntu-24.04 -- bash -lc "git status"',
        ):
            self.assertEqual(
                status_guard.handle_pre_tool_use(
                    {
                        "conversationId": conv_id,
                        "cwd": str(ws_dir),
                        "workspacePaths": [str(ws_dir)],
                        "toolCall": {"name": "run_command", "args": {"CommandLine": safe_freeze_cmd}},
                    }
                )["decision"],
                "allow",
                f"freeze 期应放行合法本地 Git/shell 命令: {safe_freeze_cmd}",
            )

        handoff_doc = ws_dir / "handoff_next.md"
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {
                    "conversationId": conv_id,
                    "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)],
                    "toolCall": {"name": "write_file", "args": {"Path": str(handoff_doc), "Content": "# Handoff"}},
                }
            )["decision"],
            "allow",
        )
        handoff_doc.write_text(
            "# 本会话交接档案\n\n已验证：已核对前三批。\nGit 工作区：保留当前结果。\n"
            "未完成：第 4 批待运行。\n\n## 新对话提示词\n请读取本文件和用户裁定，核对前三批结果后运行第 4 批。\n",
            encoding="utf-8",
        )

        # Stop 门禁：已落盘交接档但 /goal 未带 <!-- GOAL_CANCELLED --> 时先拦截 1 次；同回合续跑必放行
        _append_jsonl(tp, {"step_index": 20, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "已写入交接文档，停机等待新对话。"})
        stop_incomplete = reply_repeat_guard.handle_stop_repeat_guard(
            {"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp)}
        )
        self.assertEqual(stop_incomplete["decision"], "continue")
        self.assertIn("GOAL_CANCELLED", stop_incomplete["reason"])

        # 同回合 continuationActive=True 必放行；新回合带全交接内容 + <!-- GOAL_CANCELLED --> 也放行
        self.assertEqual(
            reply_repeat_guard.handle_stop_repeat_guard(
                {"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL", "continuationActive": True, "transcriptPath": str(tp)}
            )["decision"],
            "allow",
        )
        _append_jsonl(tp, {"step_index": 21, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "补充停机"})
        _append_jsonl(
            tp,
            {
                "step_index": 22,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "已核验并整理交接。\n```markdown\n继续下一批\n```\n<!-- GOAL_CANCELLED -->",
            },
        )
        self.assertEqual(
            reply_repeat_guard.handle_stop_repeat_guard(
                {"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp)}
            )["decision"],
            "allow",
        )

        # 验证 Stop 门禁面对带 truncated_fields: ["content"] 的截断交接回复时，能回溯 transcript_full.jsonl 放行
        _append_jsonl(tp, {"step_index": 23, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/goal\n再核一次截断回复"})
        full_tp = tp.parent / "transcript_full.jsonl"
        full_reply = (
            "已验证：前三批通过本地核对。\nGit 工作区：保留当前结果。\n未完成：第 4 批。\n"
            "## 新对话提示词\n```markdown\n请核对前三批并完成第 4 批的验收。\n```\n<!-- GOAL_CANCELLED -->"
        )
        _append_jsonl(
            tp,
            {
                "step_index": 24,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "完整收口交接已完成。\n<truncated 128 bytes>",
                "truncated_fields": ["content"],
            },
        )
        _append_jsonl(full_tp, {"step_index": 24, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": full_reply})
        self.assertEqual(
            reply_repeat_guard.handle_stop_repeat_guard(
                {"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp)}
            )["decision"],
            "allow",
        )

    def test_12_long_turn_over_256kb_preserves_exact_turn_key(self) -> None:
        """单回合产出超过 256KB 时，extract_current_turn_latest_reply 仍向前溯源锁定真实回合键"""
        tp = self.tmp_root / "long_turn.jsonl"
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "第 1 轮"})
        _append_jsonl(tp, {"step_index": 10, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "第 2 轮真实起点"})
        expected_key, _, _ = reply_repeat_guard.extract_current_turn_latest_reply(str(tp))
        self.assertTrue(expected_key.startswith("turn_10_"))

        chunk_payload = "data_" + ("A" * 40000)
        for k in range(11, 20):
            _append_jsonl(tp, {"step_index": k, "source": "SYSTEM", "type": "GENERIC", "content": chunk_payload})
        _append_jsonl(tp, {"step_index": 20, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "阶段汇报已完成。"})

        self.assertGreater(tp.stat().st_size, reply_repeat_guard.TAIL_SCAN_BYTES)
        actual_key, step_idx, latest_txt = reply_repeat_guard.extract_current_turn_latest_reply(str(tp))
        self.assertEqual(actual_key, expected_key)
        self.assertEqual(step_idx, 20)
        self.assertEqual(latest_txt, "阶段汇报已完成。")

    def test_13_ast_anchored_path_cmd_subst_and_handoff_false_positive_guards(self) -> None:
        """覆盖 Python AST 绝对右操作数拼接、$(git push) 替换、源码假交接与普通代码回答误放行防护"""
        import handoff_gate
        import session_context_loader

        conv_id = "guard-edge-013"
        ws_dir = self.tmp_root / "ws13"
        ws_dir.mkdir(parents=True)
        outside_abs = (self.tmp_root / "outside13" / "leak.txt").as_posix()
        tp = self.tmp_root / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
        _append_jsonl(tp, {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "开始"})
        for k in range(16):
            _append_jsonl(tp, {"step_index": (k + 1) * 100, "source": "SYSTEM", "type": "CHECKPOINT"})
        session_context_loader.handle_pre_invocation({"conversationId": conv_id, "transcriptPath": str(tp)})

        state_dir = self.plugin_data / "state"
        gs = handoff_gate.load_gate_state(state_dir, conv_id)
        gs["phase"] = "freeze"
        handoff_gate.save_gate_state(state_dir, conv_id, gs)

        # 1) 同一 AST 越界在常态要求审批，在冻结阶段必须直接阻断。
        ast_payload = {
                "conversationId": conv_id,
                "cwd": str(ws_dir),
                "workspacePaths": [str(ws_dir)],
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": f'python -c "from pathlib import Path; (Path(\'src\') / \'{outside_abs}\').write_text(\'x\')"'},
                },
        }
        self.assertEqual(status_guard.handle_pre_tool_use(ast_payload)["decision"], "deny")
        gs["phase"] = "overridden"
        gs["overrideUntilCheckpoint"] = 100
        gs["overrideUntilStep"] = 10000
        handoff_gate.save_gate_state(state_dir, conv_id, gs)
        self.assertEqual(status_guard.handle_pre_tool_use(ast_payload)["decision"], "force_ask")
        gs["phase"] = "freeze"
        handoff_gate.save_gate_state(state_dir, conv_id, gs)

        # 2) echo "$(git push origin main)" 在 freeze/drain 期必须阻断
        res_sub = status_guard.handle_pre_tool_use(
            {
                "conversationId": conv_id,
                "cwd": str(ws_dir),
                "workspacePaths": [str(ws_dir)],
                "toolCall": {"name": "run_command", "args": {"CommandLine": 'echo "$(git push origin main)"'}},
            }
        )
        self.assertEqual(res_sub["decision"], "deny")

        # 3) src/resume_solver.py 与 handoff/solver.cpp 在 freeze 期不得作为交接文档放行
        for bad_src in (ws_dir / "src" / "resume_solver.py", ws_dir / "handoff" / "solver.cpp"):
            self.assertEqual(
                status_guard.handle_pre_tool_use(
                    {
                        "conversationId": conv_id,
                        "cwd": str(ws_dir),
                        "workspacePaths": [str(ws_dir)],
                        "toolCall": {"name": "write_file", "args": {"Path": str(bad_src), "Content": "pass"}},
                    }
                )["decision"],
                "deny",
            )

        # 4) 普通排错回答附带代码块不得误通过 Stop 交接验收
        _append_jsonl(
            tp,
            {
                "step_index": 2000,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "定位到计算错误，示例代码如下：\n```python\nx = 1 + 2\nprint(x)\n```\n已检查完毕。",
            },
        )
        res_stop = reply_repeat_guard.handle_stop_repeat_guard(
            {"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp)}
        )
        self.assertEqual(res_stop["decision"], "continue")

    def test_native_stop_model_reason_and_concurrent_correction(self) -> None:
        import handoff_gate
        import session_context_loader

        cid = "agy-native-model-stop"
        transcript = self.tmp_root / "transcript.jsonl"
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": "请整理交接并停机"})
        payload = {"conversationId": cid, "transcriptPath": str(transcript), "lastUserInput": "请整理交接并停机",
                   "executionNum": 1, "fullyIdle": False, "error": "", "terminationReason": "model_stop"}
        session_context_loader.handle_pre_invocation(payload)
        _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "source": "MODEL", "step_index": 1, "content": "尚未写好"})
        for changes in ({"terminationReason": "error"}, {"terminationReason": "max_steps_exceeded"},
                        {"error": "runtime error"}, {"continuationActive": True}):
            self.assertEqual(reply_repeat_guard.handle_stop_repeat_guard({**payload, **changes})["decision"], "allow")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: reply_repeat_guard.handle_stop_repeat_guard(payload), range(8)))
        self.assertEqual([reply["decision"] for reply in replies].count("continue"), 1)
        state_dir = self.plugin_data / "state"
        state = handoff_gate.load_gate_state(state_dir, cid)
        self.assertEqual(len(state["stopGateBlockedTurnKeys"]), 1)
        state_file = state_dir / f"{cid}_handoff_gate.json"
        state_file.write_text("{bad", encoding="utf-8")
        self.assertEqual(reply_repeat_guard.handle_stop_repeat_guard(payload)["decision"], "allow")
        self.assertEqual(state_file.read_text(encoding="utf-8"), "{bad")
        log = self.plugin_data / "logs" / "lifecycle.log"
        self.assertIn("Stop 状态未更新", log.read_text(encoding="utf-8"))

    def test_stop_requires_changed_handoff_document_and_unquoted_goal_marker(self) -> None:
        import session_context_loader

        cid = "agy-stop-handoff-file"
        project = self.tmp_root / "project"
        project.mkdir()
        handoff = project / "handoff.md"
        transcript = self.tmp_root / "transcript.jsonl"
        document = (
            "已验证：已完成一轮。\nGit 工作区：当前仍有修改。\n未完成：下一轮验收。\n"
            "## 新对话提示词\n请从当前工作区继续未完成的数值验收并保存原始记录。\n"
        )
        handoff.write_text(document, encoding="utf-8")
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": "/goal\n请整理交接并停机"})
        payload = {"conversationId": cid, "transcriptPath": str(transcript), "cwd": str(project),
                   "workspacePaths": [str(project)], "terminationReason": "model_stop", "fullyIdle": True}
        session_context_loader.handle_pre_invocation(payload)
        approved = status_guard.handle_pre_tool_use({
            **payload, "toolCall": {"name": "write_to_file", "args": {"TargetFile": str(handoff), "CodeContent": document}},
        })
        self.assertEqual(approved["decision"], "allow")
        _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "source": "MODEL", "step_index": 1,
                                  "content": "已整理。\n<!-- GOAL_CANCELLED -->"})
        no_change = reply_repeat_guard.handle_stop_repeat_guard(payload)
        self.assertEqual(no_change["decision"], "continue")
        self.assertIn("尚未落盘", no_change["reason"])
        handoff.write_text(document + "\n本轮新核对：保留最近一次日志。\n", encoding="utf-8")
        for index, content in enumerate((
            "    <!-- GOAL_CANCELLED -->",
            "文档已更新。\n> <!-- GOAL_CANCELLED -->",
            "文档已更新。\n~~~html\n<!-- GOAL_CANCELLED -->",
            "文档已更新。\n<!-- GOAL_CANCELLED -->\n附注。",
            "文档已更新。\n<!-- GOAL_CANCELLED -->",
        )):
            _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": index * 2 + 2, "content": "/goal\n继续交接验收"})
            _append_jsonl(transcript, {"type": "PLANNER_RESPONSE", "source": "MODEL", "step_index": index * 2 + 3,
                                      "content": content})
            result = reply_repeat_guard.handle_stop_repeat_guard(payload)
            if index == 4:
                self.assertEqual(result["decision"], "allow")
            else:
                self.assertEqual(result["decision"], "continue")
                self.assertIn("GOAL_CANCELLED", result["reason"])
                self.assertNotIn("尚未落盘", result["reason"])

    def test_standard_temp_does_not_allow_business_name_or_link_escape(self) -> None:
        from unittest import mock

        root = self.tmp_root
        project = root / "workspace"
        runtime_temp = root / "actual-temp"
        other = root / "outside"
        for path in (project, runtime_temp, other):
            path.mkdir()
        payload = {"conversationId": "temp-boundary", "cwd": str(project), "workspacePaths": [str(project)]}

        def write(target):
            return status_guard.handle_pre_tool_use({
                **payload, "toolCall": {"name": "write_to_file", "args": {"TargetFile": str(target), "CodeContent": "x"}},
            })["decision"]

        with mock.patch.object(status_guard.tempfile, "gettempdir", return_value=str(runtime_temp)):
            self.assertEqual(write(runtime_temp / "probe.txt"), "allow")
            self.assertEqual(write(other / "AppData/Local/Temp/probe.txt"), "force_ask")
            self.assertEqual(write(other / "Users/not-the-current-user/.cache/probe.txt"), "force_ask")
            link = runtime_temp / "escape"
            try:
                link.symlink_to(other, target_is_directory=True)
            except OSError:
                if os.name != "nt":
                    raise
                import subprocess

                proc = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     "New-Item -ItemType Junction -Path $env:UG_LINK -Target $env:UG_TARGET | Out-Null"],
                    env={**os.environ, "UG_LINK": str(link), "UG_TARGET": str(other)},
                    capture_output=True, timeout=10,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(write(link / "probe.txt"), "force_ask")

    @unittest.skipUnless(os.name == "nt", "仅 Windows 钩子负责识别显式 WSL 调用")
    def test_wsl_temp_exception_tracks_execution_scope_and_physical_probe(self) -> None:
        from unittest import mock

        project = self.tmp_root / "workspace"
        project.mkdir()
        payload = {"conversationId": "wsl-temp-boundary", "cwd": str(project), "workspacePaths": [str(project)]}

        def check(command):
            return status_guard.handle_pre_tool_use({
                **payload, "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
            })["decision"]

        runtime_root = self.tmp_root / "runtime-temp"
        runtime_root.mkdir()
        with mock.patch.object(status_guard.tempfile, "gettempdir", return_value=str(runtime_root)), \
                mock.patch.object(status_guard.subprocess, "run") as run:
            def probe(args, **kwargs):
                self.assertEqual(args[1:3], ["-d", "Ubuntu-24.04"])
                self.assertEqual(args[3:8], ["--exec", "/usr/bin/python3", "-B", "-I", "-c"])
                self.assertNotIn("/tmp/nested/out.txt", args)
                targets = json.loads(kwargs["input"])
                if targets == ["/tmp/nested/out.txt"]:
                    return mock.Mock(returncode=0, stdout=json.dumps(targets).encode())
                return mock.Mock(returncode=0, stdout=b"[]")

            run.side_effect = probe
            self.assertEqual(check("wsl -d Ubuntu-24.04 --exec touch /tmp/nested/out.txt"), "allow")
            self.assertEqual(check("wsl.exe -d Ubuntu-24.04 -- bash -lc 'printf x > /tmp/nested/out.txt'"), "allow")
            self.assertEqual(check("wsl -d Ubuntu-24.04 --exec touch /tmp/link-out/out.txt"), "force_ask")
            self.assertEqual(check("wsl -d Ubuntu-24.04 --exec touch /tmp/../etc/out.txt"), "force_ask")
            previous = run.call_count
            self.assertEqual(check("wsl -d Ubuntu-24.04 --exec touch /tmp/nested/out.txt > /tmp/host-output"), "force_ask")
            self.assertEqual(check("wsl -d Ubuntu-24.04 --exec touch /var/tmp/out.txt"), "force_ask")
            self.assertEqual(check("touch /tmp/nested/out.txt"), "force_ask")
            self.assertEqual(run.call_count, previous)

    def test_freeze_overrides_external_approval_and_recognizes_documented_tools(self) -> None:
        import handoff_gate
        import session_context_loader

        cid = "agy-freeze-r4"
        project = self.tmp_root / "project"
        project.mkdir()
        transcript = self.tmp_root / "transcript.jsonl"
        _append_jsonl(transcript, {"type": "USER_INPUT", "step_index": 0, "content": "请整理交接并停机"})
        payload = {"conversationId": cid, "transcriptPath": str(transcript), "cwd": str(project),
                   "workspacePaths": [str(project)]}
        session_context_loader.handle_pre_invocation(payload)
        def call(name, args):
            return status_guard.handle_pre_tool_use({**payload, "toolCall": {"name": name, "args": args}})
        outside = self.tmp_root / "outside" / "solver.py"
        self.assertEqual(call("write_to_file", {"TargetFile": str(outside)})["decision"], "force_ask")
        state_dir = self.plugin_data / "state"
        state = handoff_gate.load_gate_state(state_dir, cid)
        state["phase"] = "freeze"
        handoff_gate.save_gate_state(state_dir, cid, state)
        for name, args in (
            ("replace_file_content", {"TargetFile": str(outside)}),
            ("multi_replace_file_content", {"TargetFile": str(outside)}),
            ("view_file", {"AbsolutePath": str(project / "plot.png")}),
            ("run_command", {"CommandLine": "cat plot.png"}),
            ("generate_image", {}),
            ("unknown_tool", {}),
        ):
            self.assertEqual(call(name, args)["decision"], "deny", name)
        self.assertEqual(call("view_file", {"AbsolutePath": str(project / "readme.md")})["decision"], "allow")
        self.assertEqual(call("finish", {})["decision"], "allow")
        self.assertEqual(call("write_to_file", {"TargetFile": str(outside.with_name("handoff.md"))})["decision"], "force_ask")

    def test_14_sed_script_quoted_redirect_ast_home_handoff_heading_and_missing_full_tp(self) -> None:
        import handoff_gate

        project = self.tmp_root / "proj14"
        project.mkdir()
        (project / "local.txt").write_text("foo\nbar\n", encoding="utf-8")
        base_payload = {"conversationId": "guard-14", "cwd": str(project), "workspacePaths": [str(project)]}

        def run_cmd(cmd: str) -> str:
            return status_guard.handle_pre_tool_use(
                {**base_payload, "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}}}
            )["decision"]

        self.assertEqual(run_cmd("sed -i '/^foo/d' local.txt"), "allow")
        self.assertEqual(run_cmd('rg "cmd > /var/log/a.log" local.txt'), "allow")
        self.assertEqual(
            run_cmd('python -c "from pathlib import Path; (Path.home() / \'.codex/config.toml\').write_text(\'x\')"'),
            "force_ask",
        )
        self.assertIsNone(
            status_guard._eval_ast_path_expr(
                status_guard.ast.parse("unknown_base / '.codex'", mode="eval").body, {}
            )
        )

        for valid_reply in (
            "已验证：完成回归。\nGit 工作区：干净。\n未完成：待推送。\n### 4. 新对话提示词\n```text\n请读取当前仓库状态与测试记录，继续完成远端推送验收。\n```",
            "已验证：完成回归。\nGit 工作区：干净。\n未完成：待推送。\n新对话提示词\n请复制以下内容到新会话：\n```markdown\n请读取当前仓库状态与测试记录，继续完成远端推送验收。\n```",
        ):
            self.assertTrue(handoff_gate._handoff_structure_present(valid_reply), valid_reply)

        cid = "guard-missing-full"
        tp = self.tmp_root / "missing_full" / "transcript.jsonl"
        _append_jsonl(tp, {"type": "USER_INPUT", "step_index": 0, "content": "请整理交接并停机"})
        import session_context_loader
        session_context_loader.handle_pre_invocation({"conversationId": cid, "transcriptPath": str(tp)})
        compact_handoff = (
            "已验证：通过本地测试。\nGit 工作区：保留当前修改。\n未完成：跨端补测。\n"
            "## 新对话提示词\n```text\n请读取当前仓库状态与测试记录，继续跨端补测并核对回执。\n```"
        )
        _append_jsonl(
            tp,
            {"type": "PLANNER_RESPONSE", "source": "MODEL", "step_index": 1,
             "content": compact_handoff, "truncated_fields": ["content"]},
        )
        res = reply_repeat_guard.handle_stop_repeat_guard(
            {"conversationId": cid, "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp)}
        )
        self.assertEqual(res["decision"], "allow")

    def test_15_run_workflow_boundary_cross_surface_file_aliases_and_cli_continuation(self) -> None:
        project = self.tmp_root / "proj15"
        project.mkdir()
        outside = self.tmp_root / "outside15"
        outside.mkdir()
        base_payload = {"conversationId": "guard-15", "cwd": str(project), "workspacePaths": [str(project)]}

        def call(name: str, args: dict) -> dict:
            return status_guard.handle_pre_tool_use({**base_payload, "toolCall": {"name": name, "args": args}})

        # 1) run_workflow R4 边界：项目内只读放行，项目外 Cwd / Script / ScriptPath 拦截
        self.assertEqual(
            call("run_workflow", {"Script": "res = call_tool('read_file', {'Path': 'solver.py'})\n"})["decision"],
            "allow",
        )
        self.assertEqual(
            call("run_workflow", {"Cwd": str(outside), "Script": "x = 1\n"})["decision"],
            "force_ask",
        )
        with unittest.mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": "1"}):
            self.assertEqual(
                call("run_workflow", {"Cwd": str(outside), "Script": "x = 1\n"})["decision"],
                "deny",
            )

        out_file_posix = (outside / "leak.txt").as_posix()
        for unsafe_script in (
            f"from pathlib import Path\nPath({out_file_posix!r}).write_text('x')\n",
            f"call_tool('write_file', {{'Path': {out_file_posix!r}, 'Content': 'x'}})\n",
            # 源码顺序：先赋外部路径并调用写工具，后赋项目内路径，严禁被后置安全赋值掩盖
            f"p = {out_file_posix!r}\ncall_tool('write_file', {{'Path': p, 'Content': 'x'}})\np = 'inside.txt'\n",
            f"call_tool('run_command', {{'CommandLine': 'touch {out_file_posix}'}})\n",
            f"agent('review', cwd={outside.as_posix()!r})\n",
        ):
            self.assertEqual(call("run_workflow", {"Script": unsafe_script})["decision"], "force_ask", unsafe_script)

        safe_wf = project / "workflows" / "readonly_check.py"
        safe_wf.parent.mkdir()
        safe_wf.write_text("out = call_tool('read_file', {'Path': 'README.md'})\n", encoding="utf-8")
        self.assertEqual(call("run_workflow", {"ScriptPath": str(safe_wf)})["decision"], "allow")

        unsafe_in_proj_wf = project / "workflows" / "write_outside.py"
        unsafe_in_proj_wf.write_text(
            f"call_tool('sed_file', {{'Path': {out_file_posix!r}}})\n",
            encoding="utf-8",
        )
        self.assertEqual(call("run_workflow", {"ScriptPath": str(unsafe_in_proj_wf)})["decision"], "force_ask")

        ext_wf = outside / "ext_wf.py"
        ext_wf.write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(call("run_workflow", {"ScriptPath": str(ext_wf)})["decision"], "force_ask")

        # 2) 跨宿主表面写改别名 R4 硬边界
        for tool_name, path_key in (
            ("write_to_file", "TargetFile"),
            ("replace_file_content", "TargetFile"),
            ("multi_replace_file_content", "TargetFile"),
            ("sed_file", "Path"),
            ("notebook_edit", "NotebookPath"),
        ):
            self.assertEqual(call(tool_name, {path_key: str(project / "ok.ipynb")})["decision"], "allow", tool_name)
            self.assertEqual(call(tool_name, {path_key: str(outside / "bad.ipynb")})["decision"], "force_ask", tool_name)

        # 3) view_file 与 read_file 共用防空转轮询门禁（含跨别名同参重复读取拦截）
        log_file = project / "residual.log"
        log_file.write_text("iter=1 res=1e-3\n", encoding="utf-8")
        poll_payload = {**base_payload, "conversationId": "poll-view-15"}
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {**poll_payload, "toolCall": {"name": "read_file", "args": {"Path": str(log_file)}}}
            )["decision"],
            "allow",
        )
        self.assertEqual(
            status_guard.handle_pre_tool_use(
                {**poll_payload, "toolCall": {"name": "view_file", "args": {"TargetFile": str(log_file)}}}
            )["decision"],
            "deny",
        )

        # 4) 先证明短等待回复确实触发门禁，再验证两种字段均能熔断。
        tp_stop = self.tmp_root / "stop_snake.jsonl"
        waiting_reply = "正在等待后台算例完成通知..."
        decisions = []
        for idx in (1, 2, 3):
            _append_jsonl(
                tp_stop,
                {"type": "PLANNER_RESPONSE", "source": "MODEL", "step_index": idx, "content": waiting_reply},
            )
            decisions.append(reply_repeat_guard.handle_stop_repeat_guard({
                "conversationId": "cli-snake-stop", "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp_stop),
            })["decision"])
        self.assertEqual(decisions, ["allow", "allow", "continue"])
        for flag in ("continuationActive", "continuation_active"):
            self.assertEqual(reply_repeat_guard.handle_stop_repeat_guard({
                "conversationId": "cli-snake-stop", "terminationReason": "NO_TOOL_CALL",
                flag: True, "transcriptPath": str(tp_stop),
            })["decision"], "allow")

    def test_16_ast_dynamic_bindings_control_flow_and_call_order(self):
        project = self.tmp_root / "ast-project"
        project.mkdir()
        (project / "sub").mkdir()
        base = {"conversationId": "ast-16", "cwd": str(project), "workspacePaths": [str(project)]}
        bad_scripts = (
            "p = '../outside/x'\nif False:\n    p = 'inside'\nopen(p, 'w')",
            "p = '../outside/x'\ntry:\n    p = 'inside'\nexcept Exception:\n    pass\nopen(p, 'w')",
            "p = '../outside/x'\nfor i in []:\n    p = 'inside'\nopen(p, 'w')",
            "p = '../outside/x'\nwhile False:\n    p = 'inside'\nopen(p, 'w')",
            "p = 'inside'\ndef f():\n    open(p, 'w')\np = '../outside/x'\nf()",
            "def f(p):\n    open(p, 'w')\nf('inside')\nf('../outside/x')",
            "p = 'inside'\nopen((p := '../outside/x'), 'w')",
            "p = 'inside'\nopen(p, (mode := 'w'), encoding=(p := '../outside/x'))",
            "p, q = ('inside', '../outside/x')\nopen(q, 'w')",
            "[p, q] = ['inside', '../outside/x']\nopen(q, 'w')",
            "p = 'inside'\nwith unknown_context() as p:\n    open(p, 'w')",
            "ext = '../outside'\nopen(f'{ext}/x', 'w')",
            "ext = get_external()\nopen(f'{ext}/x', 'w')",
            "from pathlib import Path\nPath(unknown_path()).write_text('x')",
            "import os\nos.remove(unknown_path())",
            "import shutil\nshutil.rmtree(unknown_path())",
            "mode = get_mode()\nopen('../outside/x', mode)",
            "mode = 'r'\nif condition:\n    mode = 'w'\nopen('../outside/x', mode=mode)",
            "import os\nos.chdir('../outside')\nopen('x', 'w')",
            "from os import chdir as cd\ncd(unknown_path())\nopen('x', 'w')",
            "import os as operating\noperating.chdir('../outside')",
            "import os\nos.chdir('sub')\nopen('../../outside/x', 'w')",
        )
        safe_scripts = (
            "mode = 'rb'\nopen('../outside/input', mode)",
            "mode = 'rt'\nopen('../outside/input', mode=mode)",
            "p, q = ('inside', 'also-inside')\nopen(q, 'w')",
            "def f(p):\n    open(p, 'w')\nf('inside')",
            "import os\nos.chdir('sub')\nopen('../inside', 'w')",
            "from os import chdir\nchdir('sub')\nopen('inside', 'w')",
            "import tempfile\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory() as tmp_dir:\n    Path(tmp_dir).joinpath('x').write_text('x')",
            "from pathlib import Path\nPath('inside').write_text('<DYNAMIC_UNRESOLVED>')",
            "arr = [1, 2, 3].copy()\nprint(arr)",
        )
        for script, expected in [(s, "force_ask") for s in bad_scripts] + [(s, "allow") for s in safe_scripts]:
            for tool, args in (("run_workflow", {"Script": script}),
                               ("run_command", {"CommandLine": "@'\n" + script + "\n'@ | python -B -"})):
                with self.subTest(script=script, tool=tool):
                    self.assertEqual(status_guard.handle_pre_tool_use({**base, "toolCall": {"name": tool, "args": args}})["decision"], expected)

    def test_17_workflow_dicts_dynamic_names_and_unreviewable_scripts(self):
        project = self.tmp_root / "workflow-project"
        project.mkdir()
        base = {"conversationId": "workflow-17", "cwd": str(project), "workspacePaths": [str(project)]}

        def call(args):
            return status_guard.handle_pre_tool_use({**base, "toolCall": {"name": "run_workflow", "args": args}})

        bad_scripts = (
            "name = 'write_file'\nargs = {'AbsolutePath': '../outside/x'}\ncall_tool(name, args)",
            "call_tool('write_file', {'Path': 'inside', 'Path': '../outside/x'})",
            "call_tool('write_file', {'Path': 'inside', **unknown_args})",
            "call_tool('write_file', {**unknown_args, 'Path': 'inside'})",
            "call_tool(unknown_name, {'Path': 'inside'})",
            "call_tool('write_file', unknown_args)",
            "call_tool('write_file', {'Path': get_path()})",
            "call_tool('run_command', {'CommandLine': get_command()})",
            "call_tool('run_workflow', {'Script': get_script()})",
            "call_tool('run_workflow', {'Script': \"open('../outside/x', 'w')\"})",
            "args = {'Path': 'inside'}\nargs['Path'] = '../outside/x'\ncall_tool('write_file', args)",
            "import os\nos.chdir('sub')\ncall_tool('write_file', {'FilePath': '../../outside/x'})",
        )
        for script in bad_scripts:
            with self.subTest(script=script):
                self.assertEqual(call({"Script": script})["decision"], "force_ask")
        for script in (
            "name = 'write_file'\nargs = {'Path': '../outside/x', 'Path': 'inside'}\ncall_tool(name, args)",
            "args = {'Path': 'inside'}\ncall_tool('write_file', {**args})",
            "call_tool('run_command', {'CommandLine': 'git status --short'})",
            "call_tool('read_file', {'AbsolutePath': '../outside/input'})",
            "call_tool('run_workflow', {'Script': 'print(1)'})",
        ):
            with self.subTest(script=script):
                self.assertEqual(call({"Script": script})["decision"], "allow")
        script_path = project / "script.py"
        script_path.write_text("if broken\n", encoding="utf-8")
        self.assertEqual(call({"ScriptPath": str(script_path)})["decision"], "force_ask")
        script_path.write_text("#" * 32, encoding="utf-8")
        with unittest.mock.patch.object(status_guard, "MAX_WORKFLOW_SCRIPT_BYTES", 16):
            self.assertEqual(call({"ScriptPath": str(script_path)})["decision"], "force_ask")
        for target in (project / "missing.py", project):
            self.assertEqual(call({"ScriptPath": str(target)})["decision"], "force_ask")
        with unittest.mock.patch.object(Path, "read_text", side_effect=OSError("unreadable")):
            hit = status_guard._detect_workflow_external_mutation(
                {"ScriptPath": str(script_path)}, str(project), str(project), [str(project)], [str(project)], [str(project)], False,
            )
            self.assertIsNotNone(hit)

    def test_18_duplicate_installer_flags_relative_cwd_and_original_tool_name(self):
        project = self.tmp_root / "fields-project"
        project.mkdir()
        (project / "sub").mkdir()
        base = {"conversationId": "fields-18", "cwd": str(project), "workspacePaths": [str(project)]}
        for flag in ("--codex-home", "--target-dir", "--codex-skills-dir"):
            for first, second in (("inside", "../outside"), ("../outside", "inside")):
                command = f"python -B install.py --codex-home inside --no-zotero {flag} {first} {flag}={second}"
                self.assertEqual(status_guard.handle_pre_tool_use({**base, "toolCall": {"name": "run_command", "args": {"CommandLine": command}}})["decision"], "force_ask")
        for tool in ("sed_file", "notebook_edit", "write_to_file"):
            for key in ("AbsolutePath", "absolute_path", "FilePath"):
                result = status_guard.handle_pre_tool_use({**base, "toolCall": {"tool_name": tool, "args": {key: "../outside/x"}}})
                self.assertEqual(result["decision"], "force_ask")
                self.assertIn(tool, result["reason"])
        for tool, key, script in (("run_command", "CommandLine", "touch ../inside"),
                                   ("run_workflow", "Script", "open('../inside', 'w')")):
            result = status_guard.handle_pre_tool_use({**base, "toolCall": {"tool_name": tool, "args": {"Cwd": "sub", key: script}}})
            self.assertEqual(result["decision"], "allow")

    def test_19_ast_match_captures(self):
        project = self.tmp_root / "match-project"
        project.mkdir()
        keys = [status_guard._normalize_cross_env_lexical(str(project))]

        def scan(script):
            return status_guard._scan_inline_python_code(script, str(project), keys, keys, False)

        for pattern, guard in (("p", ""), ("[*p]", ""), ("{'key': _, **p}", ""),
                               ("{'key': p}", " if open(p, 'w')")):
            script = "p = 'inside'\nmatch unknown:\n    case " + pattern + guard + ":\n        open(p, 'w')"
            with self.subTest(pattern=pattern, guard=guard):
                self.assertIsNotNone(scan(script))
        self.assertIsNone(scan("p = 'inside'\nmatch unknown:\n    case _:\n        open(p, 'w')"))
        self.assertIsNotNone(scan("p = 'inside'\ntry:\n    pass\nexcept* Exception as p:\n    open(p, 'w')"))

    def test_20_ast_string_replace_path_targets_and_links(self):
        project = self.tmp_root / "path-project"
        project.mkdir()
        base = {"conversationId": "batch1-path", "cwd": str(project), "workspacePaths": [str(project)]}
        bad = (
            "Path('inside').replace('../outside/x')", "Path('inside').replace(target='../outside/x')",
            "Path('inside').rename(target='../outside/x')", "Path('../outside/x').rename('inside')",
            "Path('../outside/link').symlink_to('inside')", "Path('../outside/link').hardlink_to('inside')",
            "Path('inside').rename(*targets)", "Path('inside').replace('inside2', **options)",
            "Path('inside').rename()",
        )
        safe = (
            "s = unknown_string(); s.replace('a', 'b')", "s = unknown_string(); s.replace('a', 'b', 1)",
            "s = unknown_string(); s.replace(old='a', new='b')", "s = unknown_string(); s.replace('a', new='b', count=1)",
            "Path('inside').rename(target='inside2')", "Path('inside').replace('inside2')",
            "Path('inside-link').symlink_to('../outside/input')", "Path('inside-link').hardlink_to('../outside/input')",
        )
        for script, expected in [(s, "force_ask") for s in bad] + [(s, "allow") for s in safe]:
            script = "from pathlib import Path\n" + script
            for tool, args in (("run_workflow", {"Script": script}),
                               ("run_command", {"CommandLine": "@'\n" + script + "\n'@ | python -B -"})):
                with self.subTest(script=script, tool=tool):
                    self.assertEqual(status_guard.handle_pre_tool_use({**base, "toolCall": {"name": tool, "args": args}})["decision"], expected)

    def test_21_ast_module_aliases_keywords_and_unpacked_write_arguments(self):
        project = self.tmp_root / "module-project"
        project.mkdir()
        keys = [status_guard._normalize_cross_env_lexical(str(project))]

        def scan(script):
            return status_guard._scan_inline_python_code(script, str(project), keys, keys, False)

        cases = [("os", name, "path='../outside/x'") for name in
                 ("chdir", "remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs", "truncate")]
        cases += [("os", name, args) for name in ("rename", "replace", "renames")
                  for args in ("src='inside', dst='../outside/x'", "src='../outside/x', dst='inside'")]
        cases += [("os", name, "src='inside', dst='../outside/link'") for name in ("symlink", "link")]
        cases += [("shutil", "rmtree", "path='../outside/x'"),
                  ("shutil", "move", "src='../outside/x', dst='inside'"),
                  ("shutil", "move", "src='inside', dst='../outside/x'"),
                  ("shutil", "make_archive", "base_name='../outside/x', format='zip'"),
                  ("shutil", "unpack_archive", "filename='input.zip', extract_dir='../outside/x'")]
        cases += [("shutil", name, "src='inside', dst='../outside/x'") for name in ("copy", "copy2", "copyfile", "copytree")]
        for module, name, args in cases:
            for header, fn in ((f"import {module} as m", "m." + name),
                               (f"from {module} import {name} as f", "f")):
                for call_args in (args, "*unknown_args", "**unknown_options"):
                    script = header + "\n" + fn + "(" + call_args + ")"
                    with self.subTest(script=script):
                        self.assertIsNotNone(scan(script))
                inside = header + "\n" + fn + "(" + args.replace("../outside/", "inside/") + ")"
                self.assertIsNone(scan(inside), inside)
        for header, fn in (("import os", "os.open"), ("import os as m", "m.open"),
                           ("from os import open as f", "f"), ("from os import open", "open")):
            for args in ("'../outside/x', os.O_WRONLY | os.O_CREAT", "path='../outside/x', flags=1",
                         "*unknown_args", "path='inside', **unknown_options"):
                self.assertIsNotNone(scan(header + "\n" + fn + "(" + args + ")"), (header, args))
            for args in ("'../outside/x', 0", "path='../outside/x', flags=0"):
                self.assertIsNone(scan(header + "\n" + fn + "(" + args + ")"), (header, args))
        for script in ("open(file='../outside/x', mode='w')", "open(*unknown_args)",
                       "open('../outside/x', *unknown_modes)", "open(file='inside', **unknown_options)"):
            self.assertIsNotNone(scan(script), script)
        for script in ("open(file='../outside/x', mode='rb')", "flags=0\nimport os\nos.open('../outside/x', flags)",
                       "import shutil\nshutil.copyfile(src='../outside/input', dst='inside')",
                       "import os\nos.link(src='../outside/input', dst='inside')",
                       "import shutil\nshutil.unpack_archive('input.zip')"):
            self.assertIsNone(scan(script), script)
        for value in (status_guard._AstModule("os"), status_guard._AstCallable("os.open")):
            self.assertNotIsInstance(value, str)
            expr = status_guard.ast.parse("m / 'inside'", mode="eval").body
            self.assertIn(status_guard._AST_DYNAMIC, status_guard._eval_ast_path_candidates(expr, {"m": frozenset((value,))}))

    def test_22_ast_tempfile_custom_dirs_defaults_and_aliases(self):
        project = self.tmp_root / "temp-project"
        project.mkdir()
        keys = [status_guard._normalize_cross_env_lexical(str(project))]

        def scan(script):
            return status_guard._scan_inline_python_code(script, str(project), keys, keys, False)

        for name, index in (("mkdtemp", 2), ("TemporaryDirectory", 2), ("mkstemp", 2), ("NamedTemporaryFile", 6)):
            for header, fn in (("import tempfile as t", "t." + name),
                               (f"from tempfile import {name} as make", "make")):
                prefix = "'w+', -1, None, None, '', 'tmp', " if index == 6 else "'', 'tmp', "
                for args in ("dir='../outside'", prefix + "'../outside'", "*unknown_args", "**unknown_options"):
                    script = header + "\n" + fn + "(" + args + ")"
                    with self.subTest(script=script):
                        self.assertIsNotNone(scan(script))
                for args in ("", "dir=None", "dir='inside'", prefix + "'inside'"):
                    script = header + "\n" + fn + "(" + args + ")"
                    self.assertIsNone(scan(script), script)
                expr = status_guard.ast.parse(fn + "(dir='../outside')", mode="eval").body
                bindings = {"t": frozenset((status_guard._AstModule("tempfile"),)),
                            "make": frozenset((status_guard._AstCallable("tempfile." + name),))}
                self.assertEqual(status_guard._eval_ast_path_candidates(expr, bindings), frozenset(("../outside",)))
                for args in ("dir=None", "*unknown_args", "**unknown_options"):
                    expr = status_guard.ast.parse(fn + "(" + args + ")", mode="eval").body
                    values = status_guard._eval_ast_path_candidates(expr, bindings)
                    if args == "dir=None":
                        self.assertTrue(all(isinstance(v, status_guard._AstTempPath) for v in values))
                    else:
                        self.assertIn(status_guard._AST_DYNAMIC, values)
        for header, fn in (("import tempfile as t", "t.gettempdir"),
                           ("from tempfile import gettempdir as where", "where")):
            self.assertIsNone(scan(header + "\nfrom pathlib import Path\nPath(" + fn + "()).joinpath('x').write_text('x')"))
        self.assertIsNone(scan("import tempfile\np = tempfile.mkdtemp()\nopen(p + '/x', 'w')"))
        self.assertIsNotNone(scan("import tempfile\np = tempfile.mkdtemp(dir='../outside')\nopen(p + '/x', 'w')"))

    def test_23_ast_lambda_decorators_handlers_and_comprehension_scopes(self):
        project = self.tmp_root / "scope-project"
        project.mkdir()
        base = {"conversationId": "batch1-scope", "cwd": str(project), "workspacePaths": [str(project)]}
        bad = ["(lambda p: open(p, 'w'))('../outside/x')", "f = lambda p: open(p, 'w')\nf('../outside/x')",
               "p = 'inside'\ntry:\n    pass\nexcept Exception as p:\n    open(p, 'w')"]
        bad += ["@decorate(open('../outside/x', 'w'))\n" + definition for definition in
                ("def f():\n    pass", "async def f():\n    pass", "class C:\n    pass")]
        bad += ["p = 'inside'\n" + comp for comp in (
            "[open(p, 'w') for p in paths]", "{open(p, 'w') for p in paths}",
            "{p: open(p, 'w') for p in paths}", "(open(p, 'w') for p in paths)",
            "[p for p in paths if open(p, 'w')]", "[q for p in paths for q in open(p, 'w')]",
        )]
        safe = ("(lambda p: open(p, 'w'))('inside')", "f = lambda p='inside': open(p, 'w')\nf()",
                "p = 'inside'\n[p for p in paths]\nopen(p, 'w')",
                "p = 'inside'\n{p: p for p in paths}\nopen(p, 'w')",
                "p = 'inside'\n[p for p in open(p, 'r')]\nopen(p, 'w')")
        for script, expected in [(s, "force_ask") for s in bad] + [(s, "allow") for s in safe]:
            for tool, args in (("run_workflow", {"Script": script}),
                               ("run_command", {"CommandLine": "@'\n" + script + "\n'@ | python -B -"})):
                with self.subTest(script=script, tool=tool):
                    self.assertEqual(status_guard.handle_pre_tool_use({**base, "toolCall": {"name": tool, "args": args}})["decision"], expected)

    def test_24_ast_scientific_exports_keyword_and_unpacking_targets(self):
        project = self.tmp_root / "export-project"
        project.mkdir()
        keys = [status_guard._normalize_cross_env_lexical(str(project))]

        def scan(script):
            return status_guard._scan_inline_python_code(script, str(project), keys, keys, False)

        calls = [("fig.savefig", "fname"), ("df.to_excel", "excel_writer")]
        calls += [("df." + name, keyword) for name in ("to_csv", "to_parquet", "to_feather", "to_pickle")
                  for keyword in ("path_or_buf", "path")]
        calls += [("np." + name, keyword) for name in ("save", "savez", "savez_compressed", "savetxt")
                  for keyword in ("file", "fname")]
        for fn, keyword in calls:
            for args in ("'../outside/x'", keyword + "='../outside/x'", "*unknown_args", "**unknown_options"):
                script = "import numpy as np\n" + fn + "(" + args + ")"
                self.assertIsNotNone(scan(script), script)
            self.assertIsNone(scan("import numpy as np\n" + fn + "(" + keyword + "='inside')"), fn)
        self.assertIsNotNone(scan("import numpy as arrays\narrays.save(file='../outside/x', arr=data)"))
        self.assertIsNone(scan("df.to_csv()\ndf.to_parquet(path=None)"))

    def test_25_ast_subprocess_keywords_depth_and_all_cwd_candidates(self):
        project = self.tmp_root / "process-project"
        project.mkdir()
        (project / "sub").mkdir()
        keys = [status_guard._normalize_cross_env_lexical(str(project))]

        def scan(script, depth=0):
            return status_guard._scan_inline_python_code(script, str(project), keys, keys, False, depth)

        for name in ("run", "Popen", "call", "check_call", "check_output"):
            for header, fn in (("import subprocess as sp", "sp." + name),
                               (f"from subprocess import {name} as launch", "launch")):
                for args in ("args='touch ../outside/x'", "command='touch ../outside/x'", "args=['touch', '../outside/x']",
                             "args='git status', cwd='../outside'", "*unknown_args", "**unknown_options"):
                    script = header + "\n" + fn + "(" + args + ")"
                    self.assertIsNotNone(scan(script), script)
                self.assertIsNone(scan(header + "\n" + fn + "(args='git status', cwd='sub')"))
        for script in ("import os as system\nsystem.system(command='touch ../outside/x')",
                       "from os import system as launch\nlaunch(command='touch ../outside/x')",
                       "import subprocess\nsubprocess.run(args='touch ../outside/x')"):
            self.assertIsNotNone(scan(script, depth=2), script)
        safe = "import subprocess\nsubprocess.run(args='git status', cwd='.' if condition else 'sub')"
        with unittest.mock.patch.object(status_guard, "_detect_command_external_mutation",
                                        wraps=status_guard._detect_command_external_mutation) as detect:
            self.assertIsNone(scan(safe))
            self.assertEqual({Path(call.args[1]).resolve() for call in detect.call_args_list}, {project.resolve(), (project / "sub").resolve()})
        self.assertIsNotNone(scan(safe.replace("git status", "touch ../outside/x")))
        self.assertIsNotNone(scan("import subprocess\nsubprocess.run(args='git status', cwd=unknown_cwd)"))
        self.assertIsNone(scan("import subprocess\nsubprocess.run(args='git status', cwd=None)"))

    def test_26_workflow_unpacking_all_cwds_bom_and_single_parse(self):
        project = self.tmp_root / "workflow-batch1"
        project.mkdir()
        (project / "sub").mkdir()
        keys = [status_guard._normalize_cross_env_lexical(str(project))]

        def scan(script):
            return status_guard._scan_workflow_python_code(script, str(project), keys, keys, False)

        for script in ("call_tool(*unknown_args)", "call_tool('write_file', {'Path': 'inside'}, **unknown_options)",
                       "call_tool(name='write_file', args={'Path': 'inside'}, *unknown_args)",
                       "call_tool(**{'name': 'write_file', 'args': {'Path': '../outside/x'}})",
                       "call_tool(*('write_file', {'Path': '../outside/x'}))",
                       "agent(**unknown_options)", "agent(**{'cwd': '../outside'})", "agent(**{'Cwd': '../outside'})",
                       "agent(**{'cwd': 'inside', **unknown_options})"):
            self.assertIsNotNone(scan(script), script)
        for script in ("call_tool(**{'name': 'write_file', 'args': {'Path': 'inside'}})",
                       "call_tool(*('write_file', {'Path': 'inside'}))", "agent(**{'cwd': 'inside'})",
                       "agent(**{'description': 'review'})"):
            self.assertIsNone(scan(script), script)
        script = "import os\nif condition:\n    os.chdir('sub')\ncall_tool('run_command', {'CommandLine': 'git status'})"
        with unittest.mock.patch.object(status_guard, "_detect_command_external_mutation",
                                        wraps=status_guard._detect_command_external_mutation) as detect:
            self.assertIsNone(scan(script))
            self.assertEqual({Path(call.args[1]).resolve() for call in detect.call_args_list}, {project.resolve(), (project / "sub").resolve()})
        self.assertIsNotNone(scan(script.replace("git status", "touch ../outside/x")))
        with unittest.mock.patch.object(status_guard.ast, "parse", wraps=status_guard.ast.parse) as parse:
            self.assertIsNone(scan("print(1)"))
            self.assertEqual(parse.call_count, 1)
        script_path = project / "bom.py"
        base = {"conversationId": "batch1-bom", "cwd": str(project), "workspacePaths": [str(project)]}
        for source, expected in (("print(1)", "allow"), ("open('../outside/x', 'w')", "force_ask")):
            script_path.write_text(source, encoding="utf-8-sig")
            result = status_guard.handle_pre_tool_use({**base, "toolCall": {"name": "run_workflow", "args": {"ScriptPath": str(script_path)}}})
            self.assertEqual(result["decision"], expected)

    def test_27_cd_host_paths_and_known_home_variables(self):
        project = self.tmp_root / "cd-project"
        project.mkdir()
        (project / "sub").mkdir()
        base = {"conversationId": "batch1-cd", "cwd": str(project), "workspacePaths": [str(project)]}

        def command(text):
            return status_guard.handle_pre_tool_use({**base, "toolCall": {"name": "run_command", "args": {"CommandLine": text}}})["decision"]

        self.assertEqual(command("cd sub && touch ok.txt"), "allow")
        self.assertEqual(command("cd sub && touch ../../outside/bad.txt"), "force_ask")
        for cd in ("chdir", "set-location"):
            self.assertEqual(command(cd + " sub; touch ok.txt"), "allow")
            self.assertEqual(command(cd + " sub; touch ../../outside/bad.txt"), "force_ask")
        fake_home = self.tmp_root / "home"
        with unittest.mock.patch.object(Path, "home", return_value=fake_home), \
                unittest.mock.patch.dict(os.environ, {"USERPROFILE": str(fake_home)}):
            for home in ("$HOME", "~", "%USERPROFILE%", "$env:USERPROFILE"):
                self.assertEqual(command("cd " + home + " && touch bad.txt"), "force_ask", home)
        if os.name == "nt":
            mounted = status_guard._normalize_cross_env_lexical(str(project / "sub"))
            self.assertEqual(status_guard._resolve_nearest_existing("ok.txt", mounted), (project / "sub" / "ok.txt").resolve())
            self.assertEqual(status_guard._resolve_nearest_existing(mounted + "/ok.txt", str(project)), (project / "sub" / "ok.txt").resolve())
            self.assertEqual(status_guard._host_path_string(mounted)[:2].upper(), project.drive.upper())
        else:
            self.assertEqual(status_guard._host_path_string("X:/Projects/work"), "/mnt/x/Projects/work")

    def test_28_installer_modes_value_flags_and_script_path_words(self):
        project = self.tmp_root / "installer-project"
        project.mkdir()
        base = {"conversationId": "batch1-installer", "cwd": str(project), "workspacePaths": [str(project)]}
        bad = (
            "python install.py --app both --target-dir inside --no-zotero",
            "python install.py --app antigravity --codex-home inside --no-zotero",
            "python /git/install.py", "python install.py --zotero-cmd --dry-run",
            "python install.py --zotero-cmd=--dry-run", "python install.py --app --probe-apps",
            "python install.py --app codex --codex-home inside", "python install.py --app unknown --target-dir inside --no-zotero",
            "python install.py --app both --codex-home inside --no-zotero",
            "python install.py --app codex --target-dir inside --no-zotero", "python install.py --zotero-cmd cat",
            "python install.py --zotero-cmd py_compile", "python install.py --codex-home= --no-zotero",
            "python install.py --codex-home inside --codex-skills-dir= --no-zotero",
        )
        bad += tuple("python install.py " + flag + " --dry-run" for flag in (
            "--target-dir", "--codex-home", "--codex-skills-dir", "--codex-handoff-mode", "--codex-handoff-compactions"))
        safe = (
            "python install.py --dry-run", "python install.py --probe-apps", "python -m py_compile install.py",
            "python install.py --target-dir inside --no-zotero", "python install.py --codex-skills-dir inside --no-zotero",
            "python install.py --app auto --codex-home inside --no-zotero",
            "python install.py --app antigravity --target-dir inside --no-zotero",
            "python install.py --app codex --codex-skills-dir inside --no-zotero",
            "python install.py --app both --target-dir inside --codex-home inside --no-zotero",
            "python install.py --app both --target-dir inside --codex-skills-dir inside --no-zotero",
        )
        for text, expected in [(s, "force_ask") for s in bad] + [(s, "allow") for s in safe]:
            with self.subTest(command=text):
                result = status_guard.handle_pre_tool_use({**base, "toolCall": {"name": "run_command", "args": {"CommandLine": text}}})
                self.assertEqual(result["decision"], expected)

    def test_29_polling_readonly_clauses_search_options_and_shell_wrappers(self):
        for text in ("./solver && tail -n 20 run.log", "tail -n 20 run.log && ./solver", "cat run.log | ./solver",
                     'rg "solver.log" src/', 'grep "solver.log" src/', 'sls -Encoding utf8 "solver.log" src/',
                     'bash -lc "./solver && tail -n 20 run.log"', "cat results", "cat result"):
            with self.subTest(command=text):
                self.assertEqual(status_guard.parse_run_command_evidence(text), (None, False, text))
        cases = [("grep \"INFO|ERROR\" output.txt", "output.txt"), ("bash -lc 'grep \"INFO|ERROR\" output.txt'", "output.txt"),
                 ('powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "tail -n 20 run.log"', "run.log"),
                 ('pwsh -ep Bypass -w Hidden -Command "tail -n 20 run.log"', "run.log"),
                 ('tail -n 20 run.log | grep "INFO|ERROR"', "run.log")]
        cases += [("rg " + flag + ' "solver.log" ERROR output.txt', "output.txt") for flag in
                  ("-g", "--glob", "-t", "--type", "-A", "-B", "-C", "-m")]
        cases += [("rg " + flag + ' "solver.log" output.txt', "output.txt") for flag in ("-e", "--regexp", "-f", "--file")]
        cases += [('Select-String -Pattern "solver.log" -Path output.txt', "output.txt"),
                  ('sls -Pattern:"solver.log" output.txt', "output.txt"),
                  ('rg --regexp="solver.log" output.txt', "output.txt")]
        for text, target in cases:
            with self.subTest(command=text):
                self.assertEqual(status_guard.parse_run_command_evidence(text), (target, False, text))
        self.assertTrue(status_guard.is_evidence_file("results.json"))
        self.assertFalse(status_guard.is_evidence_file("results"))
        self.assertFalse(status_guard.is_evidence_file("result"))

    def test_30_relative_command_cwd_polling_uses_payload_or_workspace(self):
        project = self.tmp_root / "poll-project"
        project.mkdir()
        (project / "sub").mkdir()
        root_log = project / "run.log"
        sub_log = project / "sub" / "run.log"
        root_log.write_text("root\n", encoding="utf-8")
        sub_log.write_text("sub\n", encoding="utf-8")
        for with_payload_cwd in (True, False):
            base = {"conversationId": "batch1-poll-" + str(with_payload_cwd), "workspacePaths": [str(project)]}
            if with_payload_cwd:
                base["cwd"] = str(project)
            args = {"Cwd": "sub", "CommandLine": "tail -n 20 run.log"}
            payload = {**base, "toolCall": {"name": "run_command", "args": args}}
            self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "allow")
            self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "deny")
            root_log.write_text("changed root\n" + str(with_payload_cwd), encoding="utf-8")
            self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "deny")
            sub_log.write_text("changed sub\n" + str(with_payload_cwd), encoding="utf-8")
            self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "allow")
            for text in ('./solver && tail -n 20 run.log', 'rg "solver.log" src/'):
                other = {**base, "toolCall": {"name": "run_command", "args": {"Cwd": "sub", "CommandLine": text}}}
                self.assertEqual(status_guard.handle_pre_tool_use(other)["decision"], "allow")
                self.assertEqual(status_guard.handle_pre_tool_use(other)["decision"], "allow")
            for tool, args in (("run_command", {"Cwd": "sub", "CommandLine": "git status"}),
                               ("run_workflow", {"Cwd": "sub", "Script": "print(1)"})):
                with unittest.mock.patch.object(status_guard, "_check_polling_locked", return_value={"decision": "allow"}) as polling:
                    result = status_guard.handle_pre_tool_use({**base, "toolCall": {"name": tool, "args": args}})
                    self.assertEqual(result["decision"], "allow")
                    self.assertEqual(Path(polling.call_args.args[3]).resolve(), (project / "sub").resolve())

    @unittest.skipUnless(os.name == "nt", "验证 Windows NTFS 8.3 短路径归一化与边界门禁一致性")
    def test_31_windows_8dot3_short_temp_and_workspace_boundary(self):
        import ctypes

        long_base = self.tmp_root / "long_8dot3_boundary_dir"
        temp_dir = long_base / "Temp"
        proj_dir = long_base / "WorkspaceProject"
        (proj_dir / "sub").mkdir(parents=True)
        temp_dir.mkdir(parents=True)

        buf = ctypes.create_unicode_buffer(1024)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(long_base), buf, 1024) or "~" not in buf.value:
            self.skipTest("当前卷未启用 NTFS 8.3 短文件名")
        short_base = Path(buf.value)
        short_temp = short_base / "Temp"
        short_proj = short_base / "WorkspaceProject"
        escape_link = temp_dir / "escape_junction"
        try:
            escape_link.symlink_to(long_base, target_is_directory=True)
        except OSError:
            import subprocess

            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "New-Item -ItemType Junction -Path $env:UG_JUNC -Target $env:UG_TGT | Out-Null"],
                env={**os.environ, "UG_JUNC": str(escape_link), "UG_TGT": str(long_base)},
                capture_output=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

        with unittest.mock.patch.object(status_guard.tempfile, "gettempdir", return_value=str(short_temp)):
            base = {"conversationId": "win-8dot3", "cwd": str(short_proj), "workspacePaths": [str(short_proj)]}
            for cmd, expected in (
                ("cd sub && touch ok.txt", "allow"),
                (f'python -c "open({str(proj_dir / "inside.txt")!r}, \'w\')"', "allow"),
                (f'python -c "open({str(temp_dir / "scratch.txt")!r}, \'w\')"', "allow"),
                (f'python -c "open({str(short_temp / "scratch2.txt")!r}, \'w\')"', "allow"),
                (f'python -c "open({str(short_temp / "escape_junction" / "bad.txt")!r}, \'w\')"', "force_ask"),
                (f'python -c "open({str(long_base / "outside.txt")!r}, \'w\')"', "force_ask"),
            ):
                res = status_guard.handle_pre_tool_use({**base, "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}}})
                self.assertEqual(res["decision"], expected, cmd)

    def test_32_shell_control_wrappers_tight_redirect_and_cae_ast_paths(self) -> None:
        project = self.tmp_root / "proj32_root" / "solver_case"
        outside = self.tmp_root / "proj32_root" / "outside_sibling"
        project.mkdir(parents=True)
        outside.mkdir(parents=True)
        base = {"conversationId": "guard-32", "cwd": str(project), "workspacePaths": [str(project)]}
        out_posix = outside.as_posix()

        def decide(cmd: str) -> str:
            return status_guard.handle_pre_tool_use(
                {**base, "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}}}
            )["decision"]

        for blocked_cmd in (
            f"if rm -rf {out_posix}; then echo done; fi",
            f"while test -d {out_posix}; do rm -rf {out_posix}; break; done",
            f"env -i FOO=bar rm -rf {out_posix}",
            f"env -C {out_posix} touch leaked.dat",
            "echo preflight && cd .. && rm -rf outside_sibling",
            f"echo payload>{out_posix}/tight.txt",
            f"echo payload&>{out_posix}/tight_both.txt",
            "echo $(echo $(echo $(echo $(echo $(ls)))))",
            f'py -3 -Bc "open({(out_posix + "/bad.vtk")!r}, \'w\')"',
        ):
            self.assertEqual(decide(blocked_cmd), "force_ask", blocked_cmd)

        for allowed_cmd in (
            "command -v rm && command -V python3",
            'python -Bc "from pathlib import Path; step = 12; (Path() / f\'sol_{step:04d}.vtk\').write_text(\'ok\')"',
            'python -c "from pathlib import Path; (Path(\'mesh\').resolve()).mkdir(exist_ok=True)"',
        ):
            self.assertEqual(decide(allowed_cmd), "allow", allowed_cmd)

    def test_33_handoff_env_sudo_command_unwrap_and_nested_headless_depth(self) -> None:
        import handoff_gate
        import session_context_loader

        project = self.tmp_root / "proj33"
        outside = self.tmp_root / "outside33"
        project.mkdir()
        outside.mkdir()

        # 1) CAE_HARNESS_DEPTH=2 多层后台无头嵌套时，Antigravity 项目外写操作必须直接 deny 而非弹框
        with unittest.mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": "2"}):
            nested_res = status_guard.handle_pre_tool_use({
                "conversationId": "depth-2-conv",
                "cwd": str(project),
                "workspacePaths": [str(project)],
                "toolCall": {"name": "write_file", "args": {"Path": str(outside / "leak.txt"), "Content": "x"}},
            })
            self.assertEqual(nested_res["decision"], "deny")

        # 2) drain/freeze 阶段阻断 env -u / sudo -u 包裹的 git push，放行 command -v 只读查询
        cid = "handoff-unwrap-33"
        tp = self.tmp_root / "tp33.jsonl"
        _append_jsonl(tp, {"type": "USER_INPUT", "step_index": 0, "content": "请整理交接"})
        session_context_loader.handle_pre_invocation({"conversationId": cid, "transcriptPath": str(tp)})
        state_dir = self.plugin_data / "state"
        gs = handoff_gate.load_gate_state(state_dir, cid)
        gs["phase"] = "freeze"
        handoff_gate.save_gate_state(state_dir, cid, gs)

        def freeze_cmd(cmd: str) -> str:
            return status_guard.handle_pre_tool_use({
                "conversationId": cid,
                "cwd": str(project),
                "workspacePaths": [str(project)],
                "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}},
            })["decision"]

        self.assertEqual(freeze_cmd("env -u GIT_SSH git push origin main"), "deny")
        self.assertEqual(freeze_cmd("sudo -u root git push origin main"), "deny")
        self.assertEqual(freeze_cmd("command -v git"), "allow")

    def test_34_powershell_encoded_cmd_aliases_gnu_target_dir_and_clause_cwd(self) -> None:
        import base64

        parent_root = self.tmp_root / "proj34_parent"
        project = parent_root / "solver_ws"
        outside = parent_root / "outside_neighbor"
        project.mkdir(parents=True)
        outside.mkdir(parents=True)
        out_posix = outside.as_posix()
        base = {"conversationId": "guard-34", "cwd": str(project), "workspacePaths": [str(project)]}

        def decide(cmd: str) -> str:
            return status_guard.handle_pre_tool_use(
                {**base, "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}}}
            )["decision"]

        # 1) PowerShell -EncodedCommand / -ec Base64 拆壳检查与坏编码保守拦截
        ps_bad = base64.b64encode(f"Remove-Item -Force '{out_posix}/secret.dat'".encode("utf-16le")).decode("ascii")
        ps_good = base64.b64encode("Get-ChildItem -Name .".encode("utf-16le")).decode("ascii")
        self.assertEqual(decide(f"powershell -NoProfile -EncodedCommand {ps_bad}"), "force_ask")
        self.assertEqual(decide(f"pwsh -ec {ps_good}"), "allow")
        self.assertEqual(decide("powershell -EncodedCommand !!!not_base64!!!"), "force_ask")

        # 2) PowerShell 写删别名拦截（rd / ri / ni / cpi）与 sc.exe 只读服务查询放行
        for ps_alias_cmd in (
            f"rd {out_posix}/cache_dir",
            f"ri {out_posix}/old.vtk",
            f"ni {out_posix}/created.txt",
            f"cpi local.txt {out_posix}/copied.txt",
        ):
            self.assertEqual(decide(ps_alias_cmd), "force_ask", ps_alias_cmd)
        self.assertEqual(decide("sc query Spooler"), "allow")

        # 3) GNU cp/mv -t / --target-directory 越界拦截与 -T 项目内放行
        self.assertEqual(decide(f"cp -t {out_posix} mesh.msh"), "force_ask")
        self.assertEqual(decide(f"mv --target-directory={out_posix} mesh.msh"), "force_ask")
        self.assertEqual(decide("cp -T src_mesh.msh dst_mesh.msh"), "allow")

        # 4) 非首句切换目录后的重定向/内联 Python 拦截，以及 sys.executable 子进程递归 AST 拦截
        self.assertEqual(decide("echo step1 && cd .. && echo leaked > outside_neighbor/leak.txt"), "force_ask")
        self.assertEqual(decide("echo step1 && cd .. && python -c \"open('outside_neighbor/leak.txt', 'w')\""), "force_ask")
        self.assertEqual(
            decide("python -c \"import subprocess, sys; subprocess.run([sys.executable, '-c', \\\"open('../outside_neighbor/x.txt', 'w')\\\"])\""),
            "force_ask",
        )


class TestDispatchAndBillingGuards(unittest.TestCase):
    def setUp(self):
        try:
            self.temp = tempfile.TemporaryDirectory(prefix="guards-dispatch-")
        except OSError:
            cache_dir = (REPO_ROOT / ".pytest_cache" / "guards_dispatch").resolve()
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.temp = tempfile.TemporaryDirectory(prefix="guards-dispatch-", dir=cache_dir)
        self.addCleanup(self.temp.cleanup)
        sandbox_root = Path(self.temp.name).resolve()
        self.root = sandbox_root / "workspace"
        self.root.mkdir(parents=True, exist_ok=True)
        self.fake_home = sandbox_root / "home"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.state = self.root / "state"
        self.logs = self.root / "logs"
        self.project = self.root / "project"
        self.project.mkdir()
        self.trace = self.root / "transcript.jsonl"
        clean_env = _scrub_host_env(
            HOME=str(self.fake_home),
            USERPROFILE=str(self.fake_home),
            USER_GUARD_STATE_DIR=str(self.state),
            USER_GUARD_LOGS_DIR=str(self.logs),
            CAE_PLAN_STATE_DIR=str(self.state / "dispatch_plans"),
            USER_GUARD_ENFORCE_TICKET_GATE="1",
            USER_GUARD_DISABLE_TICKET_GATE="0",
            CAE_HARNESS_DEPTH="",
        )
        self.env = unittest.mock.patch.dict(os.environ, clean_env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self._home_patch = unittest.mock.patch.object(Path, "home", return_value=self.fake_home)
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)
        _append_jsonl(self.trace, {"type": "USER_INPUT", "step_index": 1, "content": "检查工程"})

    def pre(self, name, args=None, sid="fixture"):
        return status_guard.handle_pre_tool_use({"conversationId": sid,
            "cwd": str(self.project), "transcriptPath": str(self.trace),
            "toolCall": {"name": name, "args": args or {}}})

    def approve(self, channel="native_subagent", sid="fixture", expires=None, status="APPROVED", count=1):
        directory = self.state / "dispatch_plans"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "ticket-fixture.json").write_text(json.dumps({"ticket_id": "ticket-fixture",
            "session_id": sid, "status": status, "expires_at": expires or time.time() + 3600,
            "streams": [{"stream_id": "S" + str(i + 1), "channel": channel} for i in range(count)]}), encoding="utf-8")

    def test_custom_plan_directory_ticket_is_command_scoped(self):
        self.approve(channel="cae_harness_headless")
        custom = self.project / "custom plans"
        custom.mkdir()
        source = self.state / "dispatch_plans/ticket-fixture.json"
        (custom / source.name).write_bytes(source.read_bytes())
        source.unlink()
        command = 'python cae_harness.py batch --plan-dir "custom plans" --ticket ticket-fixture'
        self.assertEqual(self.pre("run_command", {"CommandLine": command})["decision"], "allow")
        for changed in (command.replace("ticket-fixture", "missing"), command.replace("custom plans", "missing plans"),
                        command + " && python cae_harness.py batch --ticket ticket-fixture"):
            self.assertEqual(self.pre("run_command", {"CommandLine": changed})["decision"], "deny")
        self.assertEqual(user_words.candidate_dispatch_plan_dirs(self.state, (custom,))[0], custom.resolve())
        with unittest.mock.patch.object(user_words, "_is_reparse_or_link", side_effect=lambda path: path == custom):
            self.assertNotIn(custom.resolve(), user_words.candidate_dispatch_plan_dirs(self.state, (custom,)))
        for field, value in (("status", "PENDING"), ("expires_at", time.time() - 1), ("session_id", "other")):
            ticket = json.loads((custom / source.name).read_text(encoding="utf-8"))
            ticket[field] = value
            (custom / source.name).write_text(json.dumps(ticket), encoding="utf-8")
            self.assertEqual(self.pre("run_command", {"CommandLine": command})["decision"], "deny")
            self.approve(channel="cae_harness_headless")
            (custom / source.name).write_bytes(source.read_bytes())
            source.unlink()

    def test_review_without_target_checks_current_directory_size(self):
        for i in range(3):
            (self.project / f"source{i}.py").write_bytes(b"value = 1\n")
        command = "python cae_harness.py task --kind review"
        self.assertEqual(self.pre("run_command", {"CommandLine": command})["decision"], "deny")
        self.assertEqual(self.pre("run_command", {"CommandLine": command + " --dry-run"})["decision"], "allow")
        self.approve(channel="cae_harness_headless")
        self.assertEqual(self.pre("run_command", {"CommandLine": command + " --ticket ticket-fixture"})["decision"], "allow")
        small = self.project / "small"
        small.mkdir()
        self.assertEqual(self.pre("run_command", {"CommandLine": command, "Cwd": str(small)})["decision"], "allow")

    def test_heavy_bill_four_columns_preserve_fields_and_escaping(self):
        entry = {"receipt_file": "receipt|all.json", "token_usage_summary": {"total_tokens": 42},
                 "streams": [{"stream_id": "S1", "channel": "native_subagent", "engine": "fixture-engine",
                              "model": "fixture-model", "status": "ok", "succeeded": True,
                              "token_usage": {"input_tokens": 12, "output_tokens": 30}, "receipt_file": "stream|one.json"}]}
        text = reply_repeat_guard._render_heavy_bill([entry])
        rows = [line for line in text.splitlines() if line.startswith("|")]
        self.assertEqual(rows[0], "| 流号 | 通道与模型 | 执行结果 | Token 明细与回执路径 |")
        self.assertTrue(all(line.endswith("|") and len(re.split(r"(?<!\\)\|", line)[1:-1]) == 4 for line in rows))
        for value in ("S1", "native_subagent", "fixture-engine", "fixture-model", "成功 / ok", '"input_tokens": 12',
                      '"output_tokens": 30', r"stream\|one.json", 'Token 汇总：{"total_tokens": 42}；回执：receipt|all.json'):
            self.assertIn(value, text)

    def test_bare_cli_and_readonly_self_checks(self):
        for command in ("codex exec work", "claude -p work", "claude --print work",
                        "dsh --profile headless work", "agy task work",
                        "VAR=fixture codex exec work", "bash -lc 'claude -p work'",
                        "python cae_harness.py probe; codex exec work",
                        "cmd /c codex exec work", "cmd.exe /d /s /c codex exec work",
                        "cmd /c call codex exec work", "call codex exec work", "start /b codex exec work",
                        'start "" /b codex exec work', 'cmd /c start "fixture" /b codex exec work',
                        'cmd.exe /d /s /c "codex exec work"',
                        "wsl.exe -e codex exec work", "wsl --exec claude -p work",
                        "wsl -d Ubuntu -u root --cd /tmp -- codex exec work",
                        "wsl --distribution Ubuntu --user root --exec codex exec work",
                        "sudo -u root codex exec work", "sudo --user=root -- codex exec work",
                        "timeout --signal TERM -k 5 30 nohup nice -n 10 codex exec work",
                        "sudo -u root bash -lc 'codex exec work'"):
            with self.subTest(command=command):
                result = self.pre("run_command", {"CommandLine": command})
                self.assertEqual(result["decision"], "deny")
                self.assertIn("声明窗口", result["reason"])
        for command in ("codex --version", "claude --help", "dsh models", "agy auth status",
                        "agy plugin validate", "codex exec --help", "echo codex exec",
                        "echo cae_harness.py batch", "python cae_harness.py probe"):
            self.assertEqual(self.pre("run_command", {"CommandLine": command})["decision"], "allow", command)
        for key, value in (("CAE_HARNESS_DEPTH", "2"), ("USER_GUARD_DISABLE_TICKET_GATE", "1")):
            with unittest.mock.patch.dict(os.environ, {key: value}):
                self.assertEqual(self.pre("run_command", {"CommandLine": "codex exec work"})["decision"], "allow")

    def test_native_ticket_count_turn_reset_and_ticket_validity(self):
        no_trace = {"conversationId": "native-offline", "toolCall": {"name": "run_workflow", "args": {"Script": "print(1)"}}}
        with unittest.mock.patch.dict(os.environ, {"USER_GUARD_ENFORCE_TICKET_GATE": "0"}):
            self.assertEqual(status_guard.handle_pre_tool_use(no_trace)["decision"], "deny")
        self.assertEqual(self.pre("invoke_subagent", {"TypeName": "readonly-reader"})["decision"], "allow")
        self.assertEqual(self.pre("invoke_subagent", {"TypeName": "readonly-auditor"})["decision"], "deny")
        for tool, args in (("run_workflow", {}), ("swarm", {"action": "add"}),
                           ("invoke_subagent", {"TypeName": "self"}), ("spawn_agent", {}), ("Agent", {"subagent_type": "general-purpose"})):
            self.assertEqual(self.pre(tool, args)["decision"], "deny")
        self.approve(channel="cae_harness_headless")
        self.assertEqual(self.pre("run_workflow")["decision"], "deny")
        self.approve(expires=time.time() - 1)
        self.assertEqual(self.pre("run_workflow")["decision"], "deny")
        self.approve(status="PENDING")
        self.assertEqual(self.pre("run_workflow")["decision"], "deny")
        self.approve(sid="bob")
        self.assertEqual(self.pre("run_workflow")["decision"], "deny")
        self.approve(channel="native_workflow")
        self.assertEqual(self.pre("run_workflow")["decision"], "allow")
        self.assertEqual(self.pre("run_workflow")["decision"], "deny")
        self.assertEqual(self.pre("swarm", {"action": "add"})["decision"], "deny")
        self.assertEqual(self.pre("invoke_subagent", {"TypeName": "self"})["decision"], "deny")
        _append_jsonl(self.trace, {"type": "USER_INPUT", "step_index": 7, "content": "下一项"})
        (self.state / "dispatch_plans/ticket-fixture.json").unlink()
        self.assertEqual(self.pre("invoke_subagent", {"TypeName": "readonly-reader"})["decision"], "allow")
        with unittest.mock.patch.object(status_guard, "_heavy_min_streams", return_value=3):
            self.assertEqual(self.pre("invoke_subagent", {"TypeName": "research"})["decision"], "allow")
            self.assertEqual(self.pre("invoke_subagent", {"TypeName": "research"})["decision"], "deny")

    def test_harness_heavy_and_offline_fixture_switch(self):
        for command in ("batch", "gate", "audit", "task --kind code", "task --kind=code"):
            result = self.pre("run_command", {"CommandLine": "python -B cae_harness.py " + command})
            self.assertEqual(result["decision"], "deny", command)
            self.assertIn("14 项", result["reason"])
        for flags in ("batch --dry-run", "task --kind review"):
            self.assertEqual(self.pre("run_command", {"CommandLine": "python cae_harness.py " + flags})["decision"], "allow")
        payload = {"conversationId": "offline", "cwd": str(self.project),
                   "toolCall": {"name": "run_command", "args": {"CommandLine": "python cae_harness.py gate"}}}
        with unittest.mock.patch.dict(os.environ, {"USER_GUARD_ENFORCE_TICKET_GATE": "0"}):
            self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "allow")
            payload["transcriptPath"] = str(self.trace)
            self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "deny")
        self.approve(channel="cae_harness_headless")
        self.assertEqual(self.pre("run_command", {"CommandLine": "python cae_harness.py batch"})["decision"], "deny")
        for flags in ("gate --ticket missing", "gate --ticket ticket-fixture", "batch --ticket=ticket-fixture"):
            self.assertEqual(self.pre("run_command", {"CommandLine": "python cae_harness.py " + flags})["decision"],
                             "deny" if "missing" in flags else "allow")
        self.approve(channel="native_subagent")
        self.assertEqual(self.pre("run_command", {"CommandLine": "python cae_harness.py gate --ticket ticket-fixture"})["decision"], "deny")

    def test_heavy_task_timeout_uses_dynamic_profile_and_explicit_ticket(self):
        profile = {"runtime_tunables": {"dispatch_confirmation": {"heavy_max_light_timeout_sec": 40}}}
        with unittest.mock.patch.object(status_guard.user_words, "load_fleet_profile", return_value=profile):
            for flags, expected in (("--timeout 40", "allow"), ("--timeout=41", "deny"), ("--timeout 41 --ticket missing", "deny")):
                self.assertEqual(self.pre("run_command", {"CommandLine": "python cae_harness.py task --kind review " + flags})["decision"], expected)
            self.approve(channel="cae_harness_headless")
            self.assertEqual(self.pre("run_command", {"CommandLine": "python cae_harness.py task --timeout 41 --ticket ticket-fixture"})["decision"], "allow")

    def test_wp8_default_timeout_and_target_metrics_match_heavy_limits(self):
        profile = json.loads((HOOKS_DIR.parent / "skills/cae-harness/fleet_profile.json").read_text(encoding="utf-8"))
        tunables = profile["runtime_tunables"]
        config = tunables["dispatch_confirmation"]
        self.assertEqual(config["heavy_max_light_timeout_sec"], tunables["headless_dispatch"]["default_timeout_sec"])
        command = "python cae_harness.py task --kind review"
        with unittest.mock.patch.object(status_guard.user_words, "load_fleet_profile", return_value=profile):
            self.assertEqual(self.pre("run_command", {"CommandLine": command})["decision"], "allow")
            tunables["headless_dispatch"]["default_timeout_sec"] = config["heavy_max_light_timeout_sec"] + 1
            self.assertEqual(self.pre("run_command", {"CommandLine": command})["decision"], "deny")
            self.assertEqual(self.pre("run_command", {"CommandLine": command + " --timeout 60"})["decision"], "allow")
            tunables["headless_dispatch"]["default_timeout_sec"] = config["heavy_max_light_timeout_sec"]
            for index in range(config["heavy_max_light_files"]):
                (self.project / (str(index) + ".py")).write_text("x=1", encoding="utf-8")
            relative = command + " --target ."
            self.assertEqual(self.pre("run_command", {"CommandLine": relative})["decision"], "allow")
            extra = self.project / "extra.py"
            extra.write_text("x=1", encoding="utf-8")
            self.assertEqual(self.pre("run_command", {"CommandLine": relative})["decision"], "deny")
            self.assertEqual(self.pre("run_command", {"CommandLine": relative + " --dry-run"})["decision"], "allow")
            extra.unlink()
            large = self.project / "large.py"
            large.write_bytes(b"x" * (config["heavy_max_light_tokens"] * 3))
            single = command + ' --target="' + str(large) + '"'
            self.assertEqual(self.pre("run_command", {"CommandLine": single})["decision"], "allow")
            large.write_bytes(large.read_bytes() + b"x")
            self.assertEqual(self.pre("run_command", {"CommandLine": single})["decision"], "deny")
            self.approve(channel="cae_harness_headless")
            self.assertEqual(self.pre("run_command", {"CommandLine": single + " --ticket ticket-fixture"})["decision"], "allow")

    def test_native_ticket_capacity_is_per_channel(self):
        self.approve(channel="native_subagent", count=2)
        for _ in range(2):
            self.assertEqual(self.pre("spawn_agent", {"agent_type": "self"})["decision"], "allow")
        self.assertEqual(self.pre("Agent", {"subagent_type": "general-purpose"})["decision"], "deny")
        self.assertEqual(self.pre("run_workflow")["decision"], "deny")
        self.approve(channel="native_swarm")
        self.assertEqual(self.pre("swarm", {"action": "add"})["decision"], "allow")
        self.assertEqual(self.pre("swarm", {"action": "add"})["decision"], "deny")

    def test_native_swarm_stream_weight_counts_tasks_and_n(self):
        self.approve(channel="native_swarm", count=2)
        self.assertEqual(self.pre("swarm", {"action": "add", "name": "w", "tasks": ["a", "b", "c"]})["decision"], "deny")
        self.assertEqual(self.pre("swarm", {"action": "add", "name": "w", "n": 3})["decision"], "deny")
        self.assertEqual(self.pre("swarm", {"action": "add", "name": "w", "tasks": ["a", "b"]})["decision"], "allow")
        self.assertEqual(self.pre("swarm", {"action": "add", "name": "w2"})["decision"], "deny")

        self.approve(channel="native_swarm", sid="swarm-n-sess", count=2)
        self.assertEqual(self.pre("swarm", {"action": "add", "name": "w", "n": 2}, sid="swarm-n-sess")["decision"], "allow")
        self.assertEqual(self.pre("swarm", {"action": "add", "name": "w2", "n": 1}, sid="swarm-n-sess")["decision"], "deny")

    def test_path_regressions_and_invalid_session(self):
        outside = self.root / "outside"
        outside.mkdir()
        for command in ("touch data.txt", "VAR=../fixture touch data.txt", "pushd ../outside && touch data.txt",
                        "sed --in-place s/a/b/ data.txt", "robocopy ../project . data.txt /MIR"):
            with self.subTest(command=command):
                self.assertEqual(self.pre("run_command", {"Cwd": "../outside", "CommandLine": command})["decision"], "force_ask")
        for command in ("pushd ../outside && touch data.txt", "VAR=../fixture touch ../outside/data.txt"):
            self.assertEqual(self.pre("run_command", {"CommandLine": command})["decision"], "force_ask")
        self.assertEqual(status_guard._normalize_cross_env_lexical("//host/share/.."), "//host/share")
        self.assertEqual(status_guard._normalize_cross_env_lexical("//host/share/../../file"), "//host/share/file")
        self.assertEqual(status_guard._extract_clause_path_targets(
            ["robocopy", "source", "../outside", "*.py", "/R:0", "/MIR"], True, "robocopy"), ["../outside"])
        with unittest.mock.patch.object(status_guard.user_words, "session_state_lock", side_effect=AssertionError("非法会话不得加锁")):
            self.assertEqual(self.pre("read_file", {"Path": "data.py"}, "../outside")["decision"], "allow")

    def test_state_logs_and_temp_resolved_exemptions(self):
        physical = self.root / "physical"
        physical.mkdir()
        link = self.root / "state-link"
        try:
            link.symlink_to(physical, target_is_directory=True)
        except OSError:
            # Windows 无软链接权限时核对目录联接等价的解析差异。
            original = status_guard._resolve_nearest_existing
            def resolve(raw, base, *, from_shell=False):
                if str(raw).startswith(str(link)):
                    return physical / Path(str(raw)).relative_to(link)
                return original(raw, base, from_shell=from_shell)
            patch = unittest.mock.patch.object(status_guard, "_resolve_nearest_existing", side_effect=resolve)
        else:
            patch = unittest.mock.patch.object(status_guard, "_resolve_nearest_existing", wraps=status_guard._resolve_nearest_existing)
        with patch:
            for field in ("state", "logs", "temp"):
                state = link if field == "state" else self.state
                logs = link if field == "logs" else self.logs
                with unittest.mock.patch.object(status_guard.tempfile, "gettempdir", return_value=str(link) if field == "temp" else tempfile.gettempdir()):
                    _, lexical, resolved, _, cwd = status_guard._collect_boundary_context({"cwd": str(self.project)}, state, logs)
                    for target in (link / "guard.json", physical / "guard.json"):
                        self.assertTrue(status_guard._is_write_target_allowed(str(target), cwd, lexical, resolved), str(target))

    def test_codex_pretool_protocol(self):
        import subprocess
        script = HOOKS_DIR / "status_guard.py"
        for command, expected in (("codex exec work", "deny"), ("touch ../outside/data", "ask"), ("python --version", None)):
            payload = {"session_id": "codex-fixture", "tool_name": "exec_command", "tool_input": {"cmd": command}, "cwd": str(self.project)}
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(script), "--client", "codex",
                                     "--state-dir", str(self.state), "--log-dir", str(self.logs)],
                                    input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True, check=True, timeout=15)
            response = json.loads(result.stdout)
            if expected:
                specific = response["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], "PreToolUse")
                self.assertEqual(specific["permissionDecision"], expected)
            else:
                self.assertEqual(response, {})

    def completion(self, sid):
        directory = self.state / "heavy_completions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (sid + ".json")
        path.write_text(json.dumps([{"run_id": sid, "reported_to_user": False,
            "receipt_file": str(self.root / "receipt.json"), "all_succeeded": False,
            "token_usage_summary": {"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
            "streams": [{"stream_id": "S1", "engine": "fixture", "model": "inherit", "succeeded": True, "status": "ok",
                         "token_usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}},
                        {"stream_id": "S2", "engine": "fixture", "model": "inherit", "succeeded": False, "status": "timeout",
                         "token_usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}}]}]), encoding="utf-8")
        return path

    def stop(self, client, sid, reply, active=False):
        if client == "antigravity":
            _append_jsonl(self.trace, {"type": "PLANNER_RESPONSE", "source": "MODEL", "step_index": time.time_ns(), "content": reply})
            return reply_repeat_guard.handle_stop_repeat_guard({"conversationId": sid, "transcriptPath": str(self.trace),
                "terminationReason": "model_stop", "continuationActive": active})
        return reply_repeat_guard.handle_claude_stop_repeat_guard({"_client": client, "session_id": sid,
            "transcript_path": str(self.trace), "last_assistant_message": reply, "stop_hook_active": active}, self.state, self.logs)

    def test_all_clients_missing_each_bill_element_once_and_mark_reported(self):
        for client in ("antigravity", "claude", "codex"):
            for index, reply in enumerate(("S1 成功，S2 超时；回执 receipt.json", "Token 26；回执 receipt.json", "S1 成功，S2 超时；Token 26",
                                           "完成；Token 1；回执稍后补", "S1 完成；Token 26；回执 receipt.json",
                                           "S1 完成，S2 超时；Token 26；回执稍后补",
                                           "S10 成功，S2 超时；Token 26；回执 receipt.json",
                                           "S1 成功，S2 超时；Token 26；回执 old_receipt.json.bak",
                                           "S1 成功，S2 超时；Token 26；回执 receipt.json.bak")):
                sid = client + str(index)
                ledger = self.completion(sid)
                with self.subTest(client=client, reply=reply):
                    blocked = self.stop(client, sid, reply)
                    self.assertEqual(blocked["decision"], "continue" if client == "antigravity" else "block")
                    for detail in ("S1", "S2", "timeout", "13", "26", str(self.root / "receipt.json")):
                        self.assertIn(detail, blocked["reason"])
                    self.assertNotIn("reason", self.stop(client, sid, reply, active=True))
                    self.assertNotIn("reason", self.stop(client, sid, reply))
                    self.assertFalse(json.loads(ledger.read_text(encoding="utf-8"))[0]["reported_to_user"])
                    self.assertNotIn("reason", self.stop(client, sid, "S1 成功，S2 超时；Token 26；回执 receipt.json"))
                    self.assertTrue(json.loads(ledger.read_text(encoding="utf-8"))[0]["reported_to_user"])

    def test_wp8_native_stream_identifier_word_boundaries(self):
        native = {"native": True, "turn_key": "turn", "receipt_file": str(self.trace),
                  "streams": [{"stream_id": "S1", "native_id": "agent1", "engine": "readonly-reader"},
                              {"stream_id": "S2", "native_id": "agent2", "engine": "readonly-auditor"}]}
        with unittest.mock.patch.object(reply_repeat_guard, "_native_completion_bill", return_value=native):
            blocked = reply_repeat_guard._check_heavy_report_locked({}, self.state, "native-boundary", "turn",
                "S10 成功，S2 成功；Token 26；子代理日志见轨迹", "block")
            self.assertEqual(blocked["decision"], "block")
            accepted = reply_repeat_guard._check_heavy_report_locked({}, self.state, "native-boundary", "turn",
                "S1 成功，S2 成功；Token 26；子代理日志见轨迹", "block")
            self.assertIsNone(accepted)
            saved = json.loads((self.state / "native-boundary_heavy_report_guard.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["reported_native_ids"], ["agent1", "agent2"])

    def test_native_workflow_and_subagents_completion_not_start_handles(self):
        for index in range(2):
            _append_jsonl(self.trace, {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "id": "call-" + str(index), "args": {"TypeName": "readonly-reader"}}]})
            _append_jsonl(self.trace, {"type": "GENERIC", "tool_result": {"call_id": "call-" + str(index)},
                "content": 'Created the following subagent: ' + json.dumps({"conversationId": "agent-" + str(index), "logAbsoluteUri": str(self.root / ("agent-" + str(index) + ".jsonl"))})})
        self.assertIsNone(reply_repeat_guard._native_completion_bill({"transcriptPath": str(self.trace)}))
        for index in range(2):
            _append_jsonl(self.trace, {"type": "SYSTEM_MESSAGE", "content": "<SYSTEM_MESSAGE>\n[Message] sender=agent-" + str(index) + " content=核对完成\n</SYSTEM_MESSAGE>"})
        response = self.stop("antigravity", "native", "核对完成")
        self.assertEqual(response["decision"], "continue")
        self.assertIn("未单独暴露 Token 整数", response["reason"])
        self.assertIn("agent-0.jsonl", response["reason"])
        self.assertNotIn("reason", self.stop("antigravity", "native", "两路完成；未单独暴露 Token 整数；子代理日志在轨迹中"))
        self.assertNotIn("reason", self.stop("antigravity", "native", "已汇报"))
        _append_jsonl(self.trace, {"type": "USER_INPUT", "step_index": 20, "content": "执行工作流"})
        _append_jsonl(self.trace, {"type": "PLANNER_RESPONSE", "tool_calls": [{"id": "workflow", "name": "run_workflow", "args": {}}]})
        _append_jsonl(self.trace, {"type": "GENERIC", "tool_result": {"call_id": "workflow"}, "content": "完成"})
        self.assertEqual(self.stop("antigravity", "native", "已结束")["decision"], "continue")

    def test_single_task_receipt_binding_and_single_swarm_bill(self):
        for command in ("task", "gate", "batch"):
            sid = "single-" + command
            ledger = self.completion(sid)
            entries = json.loads(ledger.read_text(encoding="utf-8"))
            entries[0]["streams"] = entries[0]["streams"][:1]
            entries[0]["streams"][0]["model"] = "fixture-model"
            entries[0]["subcommand"] = command
            ledger.write_text(json.dumps(entries), encoding="utf-8")
            self.assertIn("reason", self.stop("antigravity", sid, "完成；Token 1；回执稍后补"))
            self.stop("antigravity", sid, "fixture-model 成功；Token 13；回执 receipt.json")
            reported = json.loads(ledger.read_text(encoding="utf-8"))[0]["reported_to_user"]
            self.assertEqual(reported, command in ("task", "gate"))
            if not reported:
                self.stop("antigravity", sid, "S1 成功；Token 13；回执 receipt.json")
                self.assertTrue(json.loads(ledger.read_text(encoding="utf-8"))[0]["reported_to_user"])
        _append_jsonl(self.trace, {"type": "PLANNER_RESPONSE", "tool_calls": [{
            "id": "swarm-call", "name": "swarm", "args": {"action": "add", "TypeName": "readonly-reader"}}]})
        _append_jsonl(self.trace, {"type": "GENERIC", "tool_result": {"call_id": "swarm-call"}, "content": "完成"})
        bill = reply_repeat_guard._native_completion_bill({"transcript_path": str(self.trace)})
        self.assertEqual(bill["streams"][0]["channel"], "native_swarm")

    def test_background_task_notice_does_not_reset_short_wait_counter(self):
        for index in range(3):
            _append_jsonl(self.trace, {"type": "SYSTEM_MESSAGE", "content": "<SYSTEM_MESSAGE>\n[Message] sender=fixture/task-42 content=Task finished\n</SYSTEM_MESSAGE>"})
            result = self.stop("antigravity", "wait-fixture", "后台任务还在运行，继续等待。")
        self.assertEqual(result["decision"], "continue")

    def test_native_claude_and_codex_returns_and_codex_stop_cli(self):
        import subprocess

        for client in ("claude", "codex"):
            self.trace.write_text("", encoding="utf-8")
            if client == "claude":
                _append_jsonl(self.trace, {"type": "user", "message": {"role": "user", "content": "核对文件"}})
            else:
                _append_jsonl(self.trace, {"type": "response_item", "payload": {
                    "type": "message", "role": "user", "content": [{"type": "input_text", "text": "核对文件"}]}})
            for index in range(2):
                key = "call-" + str(index)
                if client == "claude":
                    _append_jsonl(self.trace, {"type": "assistant", "message": {"content": [{
                        "type": "tool_use", "id": key, "name": "Agent", "input": {"subagent_type": "readonly-reader"}}]}})
                    _append_jsonl(self.trace, {"type": "user", "message": {"content": [{
                        "type": "tool_result", "tool_use_id": key, "content": "核对完成"}]},
                        "toolUseResult": {"status": "completed", "agentId": "agent-" + str(index),
                                          "usage": {"total_tokens": 8}}})
                else:
                    _append_jsonl(self.trace, {"type": "response_item", "payload": {
                        "type": "function_call", "name": "functions.spawn_agent", "call_id": key,
                        "arguments": json.dumps({"agent_type": "readonly-reader"})}})
                    _append_jsonl(self.trace, {"type": "response_item", "payload": {
                        "type": "function_call_output", "call_id": key,
                        "output": json.dumps({"agent_id": "agent-" + str(index)})}})
            if client == "codex":
                self.assertIsNone(reply_repeat_guard._native_completion_bill({"transcript_path": str(self.trace)}))
                _append_jsonl(self.trace, {"type": "response_item", "payload": {
                    "type": "function_call_output", "call_id": "wait-call", "output": json.dumps({"status": {
                        "agent-0": {"completed": "核对完成"}, "agent-1": {"failed": "核对失败"}}})}})
            response = self.stop(client, "native-" + client, "核对完成")
            self.assertEqual(response["decision"], "block", client)
            self.assertIn(str(self.trace.resolve()), response["reason"])
            if client == "claude":
                self.assertIn('"total_tokens": 16', response["reason"])
            else:
                self.assertIn("失败", response["reason"])
            self.assertNotIn("reason", self.stop(client, "native-" + client,
                "两路完成返回；Token 未单独暴露整数；子代理轨迹见 transcript.jsonl"))

        self.completion("cli-fixture")
        payload = {"session_id": "cli-fixture", "transcript_path": str(self.trace), "last_assistant_message": "结束"}
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(HOOKS_DIR / "reply_repeat_guard.py"),
                                 "--client", "codex", "--state-dir", str(self.state), "--log-dir", str(self.logs)],
                                input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True, check=True, timeout=15)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_handoff_allow_still_requires_completion_bill(self):
        self.completion("handoff-bill")
        for stage in ("drain", "freeze"):
            sid = "handoff-bill-" + stage
            ledger = self.completion(sid)
            with unittest.mock.patch.object(reply_repeat_guard.handoff_gate, "check_stop_handoff_locked", return_value={"decision": "allow"}):
                result = self.stop("antigravity", sid, "交接完成")
                self.assertEqual(result["decision"], "continue")
                self.assertIn("receipt.json", result["reason"])
                self.assertNotIn("reason", self.stop("antigravity", sid, "S1 成功，S2 超时；Token 26；回执 receipt.json"))
            self.assertTrue(json.loads(ledger.read_text(encoding="utf-8"))[0]["reported_to_user"])

    def test_single_writable_and_cross_turn_async_native_completion(self):
        for client in ("antigravity", "claude", "codex"):
            with self.subTest(client=client):
                self.trace.write_text("", encoding="utf-8")
                if client == "antigravity":
                    _append_jsonl(self.trace, {"type": "PLANNER_RESPONSE", "tool_calls": [
                        {"id": "write-call", "name": "invoke_subagent", "args": {"TypeName": "self"}}]})
                    _append_jsonl(self.trace, {"type": "GENERIC", "tool_result": {"call_id": "write-call"},
                        "content": 'Created the following subagent: {"conversationId":"writer"}'})
                    later_user = {"type": "USER_INPUT", "content": "补充要求"}
                    done = {"type": "SYSTEM_MESSAGE", "content": "<SYSTEM_MESSAGE>\n[Message] sender=writer content=完成\n</SYSTEM_MESSAGE>"}
                elif client == "claude":
                    _append_jsonl(self.trace, {"type": "assistant", "message": {"content": [{
                        "type": "tool_use", "id": "write-call", "name": "Agent", "input": {"subagent_type": "general-purpose"}}]}})
                    _append_jsonl(self.trace, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "write-call"}]},
                        "toolUseResult": {"status": "async_launched", "agentId": "writer"}})
                    later_user = {"type": "user", "message": {"content": "补充要求"}}
                    _append_jsonl(self.trace, {"type": "assistant", "message": {"content": [{
                        "type": "tool_use", "id": "wait-call", "name": "TaskOutput", "input": {"task_id": "writer"}}]}})
                    done = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "wait-call", "content": "完成"}]},
                        "toolUseResult": {"status": "completed"}}
                else:
                    _append_jsonl(self.trace, {"type": "response_item", "payload": {"type": "function_call", "call_id": "write-call",
                        "name": "functions.spawn_agent", "arguments": json.dumps({"agent_type": "self"})}})
                    _append_jsonl(self.trace, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "write-call",
                        "output": json.dumps({"agent_id": "writer"})}})
                    later_user = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "补充要求"}]}}
                    done = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "wait-call",
                        "output": json.dumps({"status": {"writer": {"completed": "完成"}}})}}
                _append_jsonl(self.trace, later_user)
                self.assertIsNone(reply_repeat_guard._native_completion_bill({"transcript_path": str(self.trace)}))
                _append_jsonl(self.trace, done)
                sid = "async-writer-" + client
                result = self.stop(client, sid, "完成；Token 1；回执稍后补")
                self.assertIn(result["decision"], ("continue", "block"))
                self.assertIn("write", result["reason"])
                self.assertNotIn("reason", self.stop(client, sid, "1 路完成；Token 无法统计；子代理日志见 transcript.jsonl"))
                state = json.loads((self.state / (sid + "_heavy_report_guard.json")).read_text(encoding="utf-8"))
                self.assertIn("write-call", state["reported_native_ids"])
                _append_jsonl(self.trace, later_user)
                self.assertNotIn("reason", self.stop(client, sid, "已汇报"))

    def test_native_call_id_idempotence_and_identical_user_turns(self):
        payload = {"conversationId": "idempotent", "transcriptPath": str(self.trace),
                   "tool_use_id": "first", "toolCall": {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader"}}}
        for _ in range(2):
            self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "allow")
        payload["tool_use_id"] = "second"
        self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "deny")
        _append_jsonl(self.trace, {"type": "USER_INPUT", "step_index": 1, "content": "检查工程"})
        self.assertEqual(status_guard.handle_pre_tool_use(payload)["decision"], "allow")

    def test_async_workflow_completion_and_same_turn_single_correction(self):
        payload = {"transcriptPath": str(self.trace)}
        for index in range(2):
            key = "workflow-" + str(index)
            _append_jsonl(self.trace, {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"id": key, "name": "run_workflow", "args": {}}]})
            _append_jsonl(self.trace, {"type": "GENERIC", "tool_result": {"call_id": key},
                "content": "Command is running in background with ID: task-" + str(index)})
            if index == 0:
                self.assertIsNone(reply_repeat_guard._native_completion_bill(payload))
            _append_jsonl(self.trace, {"type": "SYSTEM_MESSAGE", "content": "<SYSTEM_MESSAGE>\n[Message] sender=fixture/task-" +
                str(index) + " content=Task finished\n</SYSTEM_MESSAGE>"})
            response = self.stop("antigravity", "async", "执行结束")
            if index == 0:
                self.assertEqual(response["decision"], "continue")
            else:
                self.assertNotIn("reason", response)
        # 子代理返回可能在宿主中形成新的消息步骤，同一用户回合仍只能补正一次。
        _append_jsonl(self.trace, {"type": "SYSTEM_MESSAGE", "content":
            "<SYSTEM_MESSAGE>\n[Message] sender=fixture content=核对完成\n</SYSTEM_MESSAGE>"})
        self.assertNotIn("reason", self.stop("antigravity", "async", "执行结束"))

    @unittest.skipUnless(os.name == "nt", "Windows 路径 API 缓冲扩容边界")
    def test_long_path_api_resizes_when_required_size_equals_capacity(self):
        calls = []
        def expand(raw, buffer, capacity):
            calls.append(capacity)
            if len(calls) == 1:
                return capacity
            buffer.value = "C:\\fixture\\long-path"
            return len(buffer.value)
        with unittest.mock.patch.object(status_guard, "_WIN_GET_LONG_PATH", side_effect=expand):
            self.assertEqual(status_guard._win_expand_short_path("C:\\fixture\\LONG~1"), "C:\\fixture\\long-path")
        self.assertEqual(len(calls), 2)


    def test_v1152_wrapper_flags_uv_run_dispatch_and_polling_unwrapping(self):
        for cmd in (
            'env -u FOO codex exec "task"',
            'env -i claude -p "task"',
            'env -S "codex exec task"',
            'command -p dsh --profile headless "task"',
            'uv run codex exec "task"',
            'uv run --python 3.11 python3 tools/cae_harness.py gate --worker role:code',
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.pre("run_command", {"CommandLine": cmd})["decision"], "deny")
        self.assertEqual(self.pre("run_command", {"CommandLine": "command -v codex"})["decision"], "allow")
        self.assertEqual(status_guard.parse_run_command_evidence("env -u LC_ALL cat build.log")[0], "build.log")
        self.assertEqual(status_guard.parse_run_command_evidence("sudo -u root tail -n 20 run.log")[0], "run.log")

    def test_v1152_codex_stop_turn_reset_subagent_skip_and_heavy_ok_word_boundary(self):
        # 1) Codex 第 2 轮未刷盘短等待不得卡在第 1 轮 step，且日志记录真实 client="codex"
        rollout = self.root / "rollout-main.jsonl"
        for row in (
            {"type": "session_meta", "payload": {"id": "codex-main-1", "source": "cli", "cwd": str(self.project)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "第一轮"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "第一轮完整实质回复结论。"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "第二轮"}]}},
        ):
            _append_jsonl(rollout, row)
        _, empty_step, empty_reply, _ = reply_repeat_guard._codex_turn_reply({"session_id": "codex-main-1", "transcript_path": str(rollout)})
        self.assertIsNone(empty_step)
        self.assertEqual(empty_reply, "")
        decisions = []
        for idx in range(3):
            resp = reply_repeat_guard.handle_claude_stop_repeat_guard(
                {"_client": "codex", "session_id": "codex-main-1", "transcript_path": str(rollout), "last_assistant_message": "正在等待编译完成，请稍候..."},
                state_dir=self.state, logs_dir=self.logs,
            )
            decisions.append(resp.get("decision"))
            _append_jsonl(rollout, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": f"c{idx}", "output": "running"}})
        self.assertEqual(decisions, [None, None, "block"])
        log_rows = [json.loads(line) for line in (self.logs / "lifecycle.log").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(any(r.get("event") == "Stop:reply_repeat_guard" and r.get("client") == "codex" for r in log_rows))
        # 2) Codex 子代理 rollout 必须跳过主会话 Stop 门禁
        sub_rollout = self.root / "rollout-sub.jsonl"
        _append_jsonl(sub_rollout, {"type": "session_meta", "payload": {"id": "codex-sub-1", "source": "subagent", "parent_thread_id": "codex-main-1", "cwd": str(self.project)}})
        for _ in range(4):
            sub_resp = reply_repeat_guard.handle_claude_stop_repeat_guard(
                {"_client": "codex", "session_id": "codex-sub-1", "transcript_path": str(sub_rollout), "last_assistant_message": "正在等待编译完成，请稍候..."},
                state_dir=self.state, logs_dir=self.logs,
            )
            self.assertEqual(sub_resp, {})
        # 3) 重负载单流 task 状态 "ok" 不得因命中 "tokens" 子串而绕过模型与流号绑定
        ledger = self.completion("single-ok-sub")
        entries = json.loads(ledger.read_text(encoding="utf-8"))
        entries[0]["streams"] = [{"stream_id": "S1", "engine": "codex", "model": "fixture-target-model", "succeeded": True, "status": "ok", "token_usage": {"total_tokens": 120}}]
        entries[0]["subcommand"] = "task"
        ledger.write_text(json.dumps(entries), encoding="utf-8")
        wrong_model_resp = self.stop("codex", "single-ok-sub", "验证已完成，共消耗 120 tokens，回执 receipt.json（使用 wrong-other-model）。")
        self.assertEqual(wrong_model_resp.get("decision"), "block")
        self.assertFalse(json.loads(ledger.read_text(encoding="utf-8"))[0]["reported_to_user"])
        right_model_resp = self.stop("codex", "single-ok-sub", "fixture-target-model 验证已完成，共消耗 120 tokens，回执 receipt.json。")
        self.assertEqual(right_model_resp, {})
        self.assertTrue(json.loads(ledger.read_text(encoding="utf-8"))[0]["reported_to_user"])


    def test_v1152_wrapper_bypasses_blocked_in_boundary_and_handoff_gate(self):
        with tempfile.TemporaryDirectory() as td:
            td_root = Path(td).resolve()
            ws = td_root / "workspace"
            ws.mkdir()
            outside = "/opt/outside_project/secret_target"
            for cmd in (
                f'env -S "rm -rf {outside}"',
                f'env --split-string="rm -rf {outside}"',
                f'nohup rm -rf {outside}',
                f'nice -n 5 mv {outside} {outside}_2',
                f'uv run rm -rf {outside}',
            ):
                with self.subTest(cmd=cmd), unittest.mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": "1"}):
                    resp = status_guard.handle_claude_pre_tool_use(
                        {"hook_event_name": "PreToolUse", "session_id": "wrap-b1", "cwd": str(ws),
                         "tool_name": "Bash", "tool_input": {"command": cmd}},
                        td_root / "sg_state", td_root / "sg_logs",
                    )
                    self.assertEqual(resp.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

        script_p = REPO_ROOT / "template/user-guard/skills/cae-harness/scripts/cae_harness.py"
        m1 = status_guard._load_harness_metrics_module(script_p)
        m2 = status_guard._load_harness_metrics_module(script_p)
        self.assertIs(m1, m2)

        for cmd in (
            'env -S "git push origin main"',
            'env --split-string="git push origin main"',
            'env -S "rm -rf src"',
            'env -S "python3 cae_harness.py task --target . --kind code --task hi"',
            'uv run git push origin main',
            'nohup git push origin main',
            'nice -n 5 git push origin main',
        ):
            with self.subTest(handoff_cmd=cmd):
                kind = status_guard.handoff_gate._classify_run_command(cmd)
                self.assertNotEqual(kind, "readonly_inspect")
                if "git push" in cmd or "cae_harness.py" in cmd:
                    self.assertEqual(kind, "blocked_always")
                else:
                    self.assertEqual(kind, "mutating_other")

    def test_v1153_h1_d1_prefixed_tools_lowercase_apply_patch_and_codex_readonly_subagent(self):
        outside = self.root / "outside_h1"
        outside.mkdir()
        log_file = self.project / "solver_run.log"
        log_file.write_text("step 1\n", encoding="utf-8")

        def pre_native(client, tool_name, tool_input, transcript=None, sid="h1-sess"):
            payload = {
                "_client": client,
                "session_id": sid,
                "cwd": str(self.project),
                "tool_name": tool_name,
                "tool_input": tool_input,
            }
            if transcript is not None:
                payload["transcript_path"] = str(transcript)
            return status_guard.handle_claude_pre_tool_use(payload, self.state, self.logs)

        # 1) H1: functions. / mcp__ 前缀剥离、小写 write/edit、apply_patch 多头核验与未解析补丁拦截
        self.assertEqual(pre_native("codex", "functions.write", {"file_path": str(self.project / "ok.py")}), {})
        self.assertEqual(
            pre_native("codex", "functions.write", {"file_path": str(outside / "bad.py")})["hookSpecificOutput"]["permissionDecision"],
            "ask",
        )
        self.assertEqual(
            pre_native("claude", "mcp__fs__edit", {"path": str(outside / "bad.py")})["hookSpecificOutput"]["permissionDecision"],
            "ask",
        )
        valid_patch = (
            "*** Begin Patch\n"
            "*** Add File: src/new_mod.py\n+x = 1\n"
            "*** Update File: src/old_mod.py\n*** Move to: src/renamed_mod.py\n"
            "*** Delete File: src/obsolete.py\n"
            "*** End Patch\n"
        )
        self.assertEqual(pre_native("codex", "functions.apply_patch", {"patch": valid_patch}), {})
        for bad_patch in (
            "*** Begin Patch\n*** Add File: src/ok.py\n*** Update File: ../outside_h1/leak.py\n*** End Patch\n",
            "*** Begin Patch\n*** Update File: src/ok.py\n*** Move to: ../outside_h1/moved.py\n*** End Patch\n",
            "--- a/src/ok.py\n+++ b/src/ok.py\n@@ -1 +1 @@\n-old\n+new\n",
        ):
            res_patch = pre_native("codex", "functions.apply_patch", {"patch": bad_patch})
            self.assertEqual(res_patch["hookSpecificOutput"]["permissionDecision"], "ask", bad_patch)

        # _claude_check_polling 前缀剥离
        self.assertEqual(pre_native("codex", "functions.exec_command", {"cmd": "tail -n 5 solver_run.log"}, sid="poll-codex"), {})
        self.assertEqual(
            pre_native("codex", "functions.exec_command", {"cmd": "tail -n 5 solver_run.log"}, sid="poll-codex")["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )
        self.assertEqual(pre_native("claude", "mcp__acp__Read", {"file_path": str(log_file)}, sid="poll-claude"), {})
        self.assertEqual(
            pre_native("claude", "mcp__acp__Read", {"file_path": str(log_file)}, sid="poll-claude")["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        # 2) D1 钩子侧：Codex readonly-* 子代理会话严禁项目内外写文件、apply_patch 与写删命令，不影响普通父会话或可写子代理
        for idx, meta_payload in enumerate((
            {"id": f"ro-{idx_s}", **extra}
            for idx_s, extra in enumerate((
                {"source": "subagent", "agent_nickname": "readonly-auditor", "cwd": str(self.project)},
                {"source": "subagent", "agent_role": "readonly-reader", "cwd": str(self.project)},
                {"subagent": "readonly-collaborator", "cwd": str(self.project)},
                {"source": {"subagent": {"thread_spawn": {"parent_thread_id": "main-1", "agent_role": "readonly-auditor"}}}, "cwd": str(self.project)},
            ))
        )):
            ro_tp = self.root / f"rollout-ro-{idx}.jsonl"
            _append_jsonl(ro_tp, {"type": "session_meta", "payload": meta_payload})
            for tool_nm, t_inp in (
                ("functions.write", {"file_path": str(self.project / "inside.py")}),
                ("Edit", {"file_path": str(outside / "ext.py")}),
                ("functions.apply_patch", {"patch": "*** Begin Patch\n*** Update File: inside.py\n*** End Patch\n"}),
                ("functions.exec_command", {"cmd": "rm -f inside.py"}),
                ("functions.exec_command", {"cmd": "echo hi > inside.py"}),
                ("functions.exec_command", {"cmd": 'python3 -c "open(\'inside.py\', \'w\')"'}),
                ("functions.exec_command", {"cmd": "git commit -m test"}),
                ("functions.exec_command", {"cmd": "sed -i 's/a/b/' inside.py"}),
            ):
                denied = pre_native("codex", tool_nm, t_inp, transcript=ro_tp, sid=f"ro-{idx}")
                self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny", (idx, tool_nm, t_inp))
            for ro_idx, ro_ok_cmd in enumerate(("rg step solver_run.log", "sed -n '1,5p' solver_run.log", "git status", 'python3 -c "print(1 + 1)"')):
                self.assertEqual(
                    pre_native("codex", "functions.exec_command", {"cmd": ro_ok_cmd}, transcript=ro_tp, sid=f"ro-ok-{idx}-{ro_idx}"),
                    {},
                )

        parent_tp = self.root / "rollout-parent-ok.jsonl"
        _append_jsonl(parent_tp, {"type": "session_meta", "payload": {"id": "parent-1", "source": "cli", "cwd": str(self.project)}})
        writable_sub_tp = self.root / "rollout-writable-sub.jsonl"
        _append_jsonl(writable_sub_tp, {"type": "session_meta", "payload": {"id": "wsub-1", "source": "subagent", "agent_role": "worker", "cwd": str(self.project)}})
        for ok_tp, ok_sid in ((parent_tp, "parent-1"), (writable_sub_tp, "wsub-1")):
            self.assertEqual(pre_native("codex", "functions.write", {"file_path": str(self.project / "inside.py")}, transcript=ok_tp, sid=ok_sid), {})
            self.assertEqual(pre_native("codex", "functions.apply_patch", {"patch": valid_patch}, transcript=ok_tp, sid=ok_sid), {})
            self.assertEqual(pre_native("codex", "functions.exec_command", {"cmd": "touch inside.py"}, transcript=ok_tp, sid=ok_sid), {})

    def test_v1153_h2_inline_py_flags_ansic_concat_ast_os_subprocess_and_max_depth(self):
        outside = self.root / "outside_h2"
        outside.mkdir()
        out_posix = (outside / "target.txt").as_posix()

        def run_agy(cmd: str) -> str:
            with unittest.mock.patch.dict(os.environ, {"USER_GUARD_DISABLE_TICKET_GATE": "1"}):
                return status_guard.handle_pre_tool_use({
                    "conversationId": "h2-conv",
                    "cwd": str(self.project),
                    "workspacePaths": [str(self.project)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}},
                })["decision"]

        # 1) 连写 -Wignore / -Xutf8 / --check-hash-based-pycs 及 ANSI-C $'...' 与复合拼接参数
        for blocked_py_cmd in (
            f'python3 -Wignore -Xutf8 --check-hash-based-pycs=never -c "open(\'{out_posix}\', \'w\')"',
            f"python3 --check-hash-based-pycs always -Bc$'import os\\nos.remove(\\x27{out_posix}\\x27)'",
            f"python3 -Wignore -c $'open(\"{out_posix}\", \"w\")'",
            f"python3 -c 'import os; os.remove('\"'{out_posix}'\"',)'",
        ):
            self.assertEqual(run_agy(blocked_py_cmd), "force_ask", blocked_py_cmd)
        self.assertEqual(
            run_agy("python3 -Wignore -Xutf8 --check-hash-based-pycs=default -c $'x = [1, 2].copy()\nprint(len(x))'"),
            "allow",
        )

        # 2) AST os 权限/属主/时间、Path.chmod/lchmod、popen、getoutput/getstatusoutput、exec*/spawn*/posix_spawn*
        keys = [status_guard._normalize_cross_env_lexical(str(self.project))]
        for bad_ast in (
            f"import os\nos.chmod('{out_posix}', 0o600)",
            f"import os\nos.lchmod('{out_posix}', 0o600)",
            f"import os\nos.chown('{out_posix}', 0, 0)",
            f"import os\nos.lchown('{out_posix}', 0, 0)",
            f"import os\nos.utime('{out_posix}', None)",
            f"from pathlib import Path\nPath('{out_posix}').chmod(0o644)",
            f"from pathlib import Path\nPath('{out_posix}').lchmod(0o644)",
            f"import os\nos.popen('touch {out_posix}')",
            f"import subprocess\nsubprocess.getoutput('rm -f {out_posix}')",
            f"import subprocess\nsubprocess.getstatusoutput('touch {out_posix}')",
            "import os\nos.execvp('sh', ['sh', '-c', 'echo hi'])",
            "import os\nos.spawnlp(os.P_WAIT, 'sh', 'sh', '-c', 'echo hi')",
            "import os\nos.posix_spawnp('sh', ['sh'], os.environ)",
            "import os\ndef f():\n    os.execv('/bin/sh', ['sh'])\nf()",
        ):
            self.assertIsNotNone(
                status_guard._scan_inline_python_code(bad_ast, str(self.project), keys, keys, False),
                bad_ast,
            )
        for safe_ast in (
            "import os\nos.chmod('inside.txt', 0o600)",
            "from pathlib import Path\nPath('inside.txt').chmod(0o644)",
            "import os\ndef unused_helper():\n    os.execvp('sh', ['sh'])",
            "import subprocess\nsubprocess.getoutput('git status')",
            "import os\nos.popen('git status')",
        ):
            self.assertIsNone(
                status_guard._scan_inline_python_code(safe_ast, str(self.project), keys, keys, False),
                safe_ast,
            )

        # 3) depth == MAX_WRAPPER_DEPTH 闭路阻断
        for wrapper_call in (
            "import os\nos.system('git status')",
            "import os\nos.popen('git status')",
            "import subprocess\nsubprocess.run(['git', 'status'])",
            "import subprocess\nsubprocess.getoutput('git status')",
        ):
            hit_max = status_guard._scan_inline_python_code(
                wrapper_call, str(self.project), keys, keys, False, depth=status_guard.MAX_WRAPPER_DEPTH
            )
            self.assertIsNotNone(hit_max, wrapper_call)
            self.assertEqual(hit_max[1], "<NESTED_DEPTH_EXCEEDED>")

    def test_v1153_h3_h4_proc_subst_control_flow_worktree_concat_redirect_and_dyn_vars(self):
        outside = self.root / "outside_h34"
        outside.mkdir()
        (self.project / "sub").mkdir(exist_ok=True)
        out_posix = outside.as_posix()

        # H3: _dispatch_commands 递归扫描进程替换 <(...) / >(...) 与控制流关键字
        for dispatch_bypass in (
            "cat <(codex exec work)",
            "tee >(claude -p work)",
            "if codex exec work; then echo ok; fi",
            "while false; do dsh --profile headless work; done",
            "if false; then echo no; elif python cae_harness.py batch; then echo yes; fi",
        ):
            self.assertEqual(self.pre("run_command", {"CommandLine": dispatch_bypass})["decision"], "deny", dispatch_bypass)

        def boundary_decide(cmd: str) -> str:
            with unittest.mock.patch.dict(os.environ, {"USER_GUARD_DISABLE_TICKET_GATE": "1"}):
                return status_guard.handle_pre_tool_use({
                    "conversationId": "h4-conv",
                    "cwd": str(self.project),
                    "workspacePaths": [str(self.project)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}},
                })["decision"]

        # H4-1: git worktree add 结合子句 cd / env -C / git -C / --work-tree
        for wt_bad in (
            "echo pre && cd .. && git worktree add outside_h34/wt1",
            f"git -C {out_posix} worktree add wt2",
            f"git -C{out_posix} worktree add wt3",
            f"git --work-tree={out_posix} worktree add wt4",
            f"env -C {out_posix} git worktree add wt5",
            "git -C $UNKNOWN_REPO worktree add wt6",
        ):
            self.assertEqual(boundary_decide(wt_bad), "force_ask", wt_bad)
        self.assertEqual(boundary_decide("git -C sub worktree add ../wt_inside"), "allow")

        # H4-2: 复合拼接引号重定向与 $ / % 未解析动态变量拦截（保留空设备放行）
        for redir_bad in (
            'echo pwn > "."/../outside_h34/leak.txt',
            "echo pwn > ''../outside_h34/leak2.txt",
            "echo pwn > $'../outside_h34/leak3.txt'",
            "echo pwn > $OUT",
            'echo pwn > "$DST_PATH"',
            "echo pwn > %CUSTOM_OUT%",
        ):
            self.assertEqual(boundary_decide(redir_bad), "force_ask", redir_bad)
        for dyn_write_target in ("$OUT", "${STAGE_DIR}/a.txt", "%CUSTOM_OUT%/a.txt"):
            with unittest.mock.patch.dict(os.environ, {"USER_GUARD_DISABLE_TICKET_GATE": "1"}):
                self.assertEqual(
                    status_guard.handle_pre_tool_use({
                        "conversationId": "h4-conv",
                        "cwd": str(self.project),
                        "workspacePaths": [str(self.project)],
                        "toolCall": {"name": "write_file", "args": {"Path": dyn_write_target, "Content": "x"}},
                    })["decision"],
                    "force_ask",
                    dyn_write_target,
                )
        for redir_ok in ("git status 2>/dev/null", "git status 2>$null", "echo ok > inside_out.txt"):
            self.assertEqual(boundary_decide(redir_ok), "allow", redir_ok)

    def test_v1153_h6_h7_h8_h10_repeat_guard_unflushed_tail_cursor_raw_decode_and_client_logs(self):
        # 1) H6: Claude 与 Codex 首条短等待已刷盘后，后续未刷盘短等待（有新工具结果或文本变化）正常累加至阻断
        for client in ("claude", "codex"):
            tp = self.root / f"h6-{client}.jsonl"
            sid = f"h6-{client}-sid"
            if client == "claude":
                _append_jsonl(tp, {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "跑算例"}})
                _append_jsonl(tp, {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "后台仍在运行，请稍候。"}]}})
            else:
                _append_jsonl(tp, {"type": "session_meta", "payload": {"id": sid, "source": "cli", "cwd": str(self.project)}})
                _append_jsonl(tp, {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "跑算例"}]}})
                _append_jsonl(tp, {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "后台仍在运行，请稍候。"}]}})

            r1 = reply_repeat_guard.handle_claude_stop_repeat_guard(
                {"_client": client, "session_id": sid, "transcript_path": str(tp), "last_assistant_message": "后台仍在运行，请稍候。"},
                self.state, self.logs,
            )
            r1_dup = reply_repeat_guard.handle_claude_stop_repeat_guard(
                {"_client": client, "session_id": sid, "transcript_path": str(tp), "last_assistant_message": "后台仍在运行，请稍候。"},
                self.state, self.logs,
            )
            self.assertEqual(r1, {})
            self.assertEqual(r1_dup, {})

            r2 = reply_repeat_guard.handle_claude_stop_repeat_guard(
                {"_client": client, "session_id": sid, "transcript_path": str(tp), "last_assistant_message": "后台仍在运行，请耐心等待。"},
                self.state, self.logs,
            )
            self.assertEqual(r2, {})

            if client == "claude":
                _append_jsonl(tp, {"type": "user", "uuid": "tr1", "message": {"content": [{"type": "tool_result", "tool_use_id": "tc1", "content": "still running"}]}})
            else:
                _append_jsonl(tp, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "tc1", "output": "still running"}})
            r3 = reply_repeat_guard.handle_claude_stop_repeat_guard(
                {"_client": client, "session_id": sid, "transcript_path": str(tp), "last_assistant_message": "后台仍在运行，请稍候。"},
                self.state, self.logs,
            )
            self.assertEqual(r3.get("decision"), "block", client)

        # 2) H7: 缺失 step_index 滑窗 row_offset 单调游标、主会话忽略子代理 SYSTEM_MESSAGE 边界、超长回复截断回查
        tp_nostep = self.root / "h7-nostep.jsonl"
        _append_jsonl(tp_nostep, {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "主会话监控"})
        steps_init, _ = reply_repeat_guard._read_tail_steps_with_offset(tp_nostep)
        self.assertEqual(steps_init[0]["row_offset"], 0)
        decisions = []
        for i in range(3):
            _append_jsonl(tp_nostep, {"type": "GENERIC", "content": "pad_" + ("X" * (reply_repeat_guard.TAIL_SCAN_BYTES // 2 + 1024))})
            _append_jsonl(tp_nostep, {"type": "SYSTEM_MESSAGE", "content": f"<SYSTEM_MESSAGE>\n[Message] sender=subagent-{i} content=阶段反馈\n</SYSTEM_MESSAGE>"})
            _append_jsonl(tp_nostep, {"type": "PLANNER_RESPONSE", "source": "MODEL", "content": "仍在等待全部子任务完成，请稍候。"})
            res = reply_repeat_guard.handle_stop_repeat_guard({
                "conversationId": "h7-main-sid", "terminationReason": "model_stop", "transcriptPath": str(tp_nostep),
            })
            decisions.append(res["decision"])
        self.assertEqual(decisions, ["allow", "allow", "continue"])

        tp_sub_only = self.root / "h7-sub-only.jsonl"
        _append_jsonl(tp_sub_only, {"type": "SYSTEM_MESSAGE", "content": "<SYSTEM_MESSAGE>\n[Message] sender=parent content=请只读核查\n</SYSTEM_MESSAGE>"})
        _append_jsonl(tp_sub_only, {"type": "PLANNER_RESPONSE", "source": "MODEL", "content": "核查完毕。"})
        sub_turn_key, _, sub_reply = reply_repeat_guard.extract_current_turn_latest_reply(str(tp_sub_only))
        self.assertNotEqual(sub_turn_key, "turn_0_initial")
        self.assertEqual(sub_reply, "核查完毕。")

        tp_huge = self.root / "h7-huge-dropped.jsonl"
        _append_jsonl(tp_huge, {"type": "USER_INPUT", "step_index": 1, "content": "输出完整报告"})
        huge_body = "报告正文：" + ("R" * (reply_repeat_guard.TAIL_SCAN_BYTES + 4096))
        _append_jsonl(tp_huge, {"type": "PLANNER_RESPONSE", "source": "MODEL", "step_index": 2, "content": huge_body})
        _append_jsonl(tp_huge, {"type": "CHECKPOINT", "step_index": 3})
        h_turn, h_step, h_text = reply_repeat_guard.extract_current_turn_latest_reply(str(tp_huge))
        self.assertTrue(h_turn.startswith("turn_1_"))
        self.assertEqual(h_step, 2)
        self.assertEqual(h_text, huge_body)

        # 3) H8: parse_agy_created_subagent 兼容嵌套对象与尾随花括号；_native_completion_bill 按 len(completed) 判重负载
        raw_created = (
            'Created the following subagent: {"conversationId": "ag-nested-1", '
            '"config": {"nested": {"depth": 1}}, "logAbsoluteUri": "/tmp/ag1.jsonl"}\nTrailing note {ok}'
        )
        parsed_created = user_words.parse_agy_created_subagent(raw_created)
        self.assertEqual(parsed_created["conversationId"], "ag-nested-1")

        tp_bill = self.root / "h8-bill.jsonl"
        _append_jsonl(tp_bill, {"type": "USER_INPUT", "step_index": 1, "content": "并行读取"})
        for idx in range(2):
            _append_jsonl(tp_bill, {"type": "PLANNER_RESPONSE", "tool_calls": [{"id": f"c-{idx}", "name": "invoke_subagent", "args": {"TypeName": "readonly-reader"}}]})
            _append_jsonl(tp_bill, {"type": "GENERIC", "tool_result": {"call_id": f"c-{idx}"},
                                    "content": f'Created the following subagent: {{"conversationId": "ag-{idx}", "meta": {{"k": 1}}}} trailing {{brace}}'})
            _append_jsonl(tp_bill, {"type": "SYSTEM_MESSAGE", "content": f"<SYSTEM_MESSAGE>\n[Message] sender=ag-{idx} content=完成{idx}\n</SYSTEM_MESSAGE>"})
        bill = reply_repeat_guard._native_completion_bill({"transcriptPath": str(tp_bill)}, state={"reported_native_ids": ["c-0"]})
        self.assertIsNotNone(bill)
        self.assertEqual([r["native_id"] for r in bill["streams"]], ["c-1"])

        # 4) H10: status_guard.log_event 与 reply_repeat_guard._load_repeat_state 透传真实 client="codex"
        bad_repeat = self.state / "codex-err-sid_repeat_guard.json"
        bad_repeat.parent.mkdir(parents=True, exist_ok=True)
        bad_repeat.write_text("{corrupted", encoding="utf-8")
        codex_tp = self.root / "h10-codex.jsonl"
        _append_jsonl(codex_tp, {"type": "session_meta", "payload": {"id": "codex-err-sid", "source": "cli", "cwd": str(self.project)}})
        reply_repeat_guard.handle_claude_stop_repeat_guard(
            {"_client": "codex", "session_id": "codex-err-sid", "transcript_path": str(codex_tp), "last_assistant_message": "请稍候"},
            self.state, self.logs,
        )
        status_guard.handle_claude_pre_tool_use(
            {"_client": "codex", "session_id": "codex-err-sid", "cwd": str(self.project), "tool_name": "functions.write", "tool_input": {"file_path": "../outside_h10/x.py"}},
            self.state, self.logs,
        )
        entries = [json.loads(ln) for ln in (self.logs / "lifecycle.log").read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertTrue(any(e.get("event") == "Stop:repeat_state_error" and e.get("client") == "codex" for e in entries))
        self.assertTrue(any(e.get("event") == "PreToolUse:workspace_boundary" and e.get("client") == "codex" for e in entries))

    def test_r2_s2_01_ansi_c_quotes_clause_split_and_quoted_strip(self):
        outside = self.root / "outside_r2_01"
        outside.mkdir()
        out_posix = outside.as_posix()

        # 1) ANSI-C $'...' 转义单引号不得吞并后续 && 子句（R4 写边界 / 裸 CLI 派工 / 交接门禁三处同步拦截）
        with unittest.mock.patch.dict(os.environ, {"USER_GUARD_DISABLE_TICKET_GATE": "1"}):
            res_r4 = status_guard.handle_pre_tool_use({
                "conversationId": "r2-ansi-r4",
                "cwd": str(self.project),
                "workspacePaths": [str(self.project)],
                "toolCall": {"name": "run_command", "args": {"CommandLine": f"echo $'it\\'s' && rm -rf {out_posix}"}},
            })
            self.assertEqual(res_r4["decision"], "force_ask")

        res_disp = self.pre("run_command", {"CommandLine": r"echo $'it\'s' && codex exec work"})
        self.assertEqual(res_disp["decision"], "deny")

        self.assertEqual(
            status_guard.handoff_gate._classify_run_command(r"echo $'it\'s' && git push origin main"),
            "blocked_always",
        )
        # ANSI-C $'...' 内部含 `>` 时不得被误判为输出重定向
        self.assertEqual(
            status_guard.handoff_gate._classify_run_command(r"echo $'it\'s a > b'"),
            "readonly_inspect",
        )

    def test_r2_s2_02_inline_python_posix_dquote_nul_ast_and_flags(self):
        import subprocess

        outside = self.root / "outside_r2_02"
        outside.mkdir()
        out_file = (outside / "pwn.txt").as_posix()

        def decide_cmd(cmd: str) -> str:
            with unittest.mock.patch.dict(os.environ, {"USER_GUARD_DISABLE_TICKET_GATE": "1"}):
                return status_guard.handle_pre_tool_use({
                    "conversationId": "r2-py-conv",
                    "cwd": str(self.project),
                    "workspacePaths": [str(self.project)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}},
                })["decision"]

        # 1) POSIX 双引号保留 \n / \t 字面量，print('\n') 不破坏 ast.parse，数组 .copy() 零误报，真实外部写精准拦截
        self.assertEqual(decide_cmd(r'''python3 -c "print('\n\t'); arr = [1, 2].copy()"'''), "allow")
        self.assertEqual(
            decide_cmd(rf'''python3 -c "print('\n'); import os as o; o.remove('{out_file}')"'''),
            "force_ask",
        )

        # 2) _decode_ansi_c_quotes 在 \0 / \x00 处截断有效载荷
        self.assertEqual(status_guard._decode_ansi_c_quotes(r"$'abc\x00def'", 0), ("abc", 13))
        self.assertEqual(status_guard._decode_ansi_c_quotes(r"$'xy\0zz'", 0), ("xy", 9))
        self.assertEqual(decide_cmd(r"python3 -c $'print(1)\x00(((syntax_err'"), "allow")
        self.assertEqual(
            decide_cmd(rf"python3 -c $'import os as o; o.remove(\"{out_file}\")\x00(((syntax_err'"),
            "force_ask",
        )

        # 3) 原始 \x00 空字节与超深 AST 递归严禁穿透 main() 造成 Fail-Open
        keys = [status_guard._normalize_cross_env_lexical(str(self.project))]
        nul = chr(0)
        self.assertIsNotNone(
            status_guard._scan_inline_python_code(f"import os as o; o.remove('{out_file}'){nul}", str(self.project), keys, keys, False)
        )
        self.assertIsNotNone(
            status_guard._scan_inline_python_code(f"print(1){nul}", str(self.project), keys, keys, False)
        )
        deep_expr = "import os as o; x=1" + "+1" * 600 + f"; o.remove('{out_file}')"
        self.assertIsNotNone(
            status_guard._scan_inline_python_code(deep_expr, str(self.project), keys, keys, False)
        )
        with unittest.mock.patch.dict(os.environ, {"CAE_HARNESS_DEPTH": "1"}):
            proc = subprocess.run(
                [sys.executable, "-B", str(HOOKS_DIR / "status_guard.py"), "--client", "claude",
                 "--state-dir", str(self.state), "--log-dir", str(self.logs)],
                input=json.dumps({
                    "session_id": "nul-cli-check",
                    "cwd": str(self.project),
                    "tool_name": "Bash",
                    "tool_input": {"command": f"python3 -c \"import os as o; o.remove('{out_file}'){nul}\""},
                }),
                capture_output=True, text=True, timeout=10,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        cli_out = json.loads(proc.stdout)
        self.assertEqual(cli_out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

        # 4) --safe-path、"-c" / '-c'、未闭合 -c $'...' 哨兵阻断、子进程列表连写 -c<code> 与首个脚本名 break
        for blocked_cmd in (
            f'''python3 --safe-path -c "open('{out_file}', 'w')"''',
            f'''python3 "-c" "open('{out_file}', 'w')"''',
            f'''python3 '-c' "open('{out_file}', 'w')"''',
            f'''python3 "-copen('{out_file}', 'w')"''',
            f"python3 -c $'open(\"{out_file}\", \"w\")",
        ):
            self.assertEqual(decide_cmd(blocked_cmd), "force_ask", blocked_cmd)

        self.assertIsNotNone(
            status_guard._scan_inline_python_code(
                f"import subprocess\nsubprocess.run(['python3', '--safe-path', \"-copen('{out_file}', 'w')\"])",
                str(self.project), keys, keys, False,
            )
        )
        # 首个非选项脚本名后出现的 -c 属于脚本自身参数，不得误判为 python -c 代码
        self.assertIsNone(
            status_guard._scan_inline_python_code(
                f"import subprocess\nsubprocess.run(['python3', 'runner.py', '-c', \"open('{out_file}', 'w')\"])",
                str(self.project), keys, keys, False,
            )
        )
        self.assertEqual(
            status_guard.handoff_gate._classify_run_command("python3 --safe-path cae_harness.py probe -c dummy"),
            "readonly_inspect",
        )
        self.assertEqual(
            status_guard.handoff_gate._classify_run_command('python3 -c"import cae_harness" cae_harness.py probe'),
            "blocked_always",
        )

    def test_r2_s2_03_unquoted_backslash_escape_shell_unwrap_write_stdin_and_list_shell(self):
        outside = self.root / "outside_r2_03"
        outside.mkdir()
        out_file = (outside / "pwn.txt").as_posix()

        def decide_agy(cmd: str) -> str:
            with unittest.mock.patch.dict(os.environ, {"USER_GUARD_DISABLE_TICKET_GATE": "1"}):
                return status_guard.handle_pre_tool_use({
                    "conversationId": "r2-sh-conv",
                    "cwd": str(self.project),
                    "workspacePaths": [str(self.project)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}},
                })["decision"]

        # 1) 引号外反斜杠转义（#3）：printf \" > /outside/pwn.txt 绝不翻转引号状态
        for esc_cmd in (
            rf'printf \" > {out_file}',
            rf"printf \' > {out_file}",
            rf"echo \$ > {out_file}",
        ):
            self.assertEqual(decide_agy(esc_cmd), "force_ask", esc_cmd)

        # 2) _unwrap_shell_script_layer 支持路径前缀与尾随重定向/位置参数同时扫描（#4）
        for wrapped_cmd in (
            f'/bin/bash -c "rm -rf {out_file}" 2>&1',
            f'/bin/sh -c "echo ok" > {out_file}',
            '/bin/bash -c \'rm -rf "$1"\' _ /outside',
            f'/bin/sh -c "echo ok" _ "$(touch {out_file})"',
        ):
            self.assertEqual(decide_agy(wrapped_cmd), "force_ask", wrapped_cmd)
        self.assertEqual(decide_agy('/bin/bash -c "echo ok" 2>&1'), "allow")

        # 3) Codex readonly-* 子代理无条件拒绝 write_stdin；普通会话对 write_stdin 的 chars/input 执行 R4 扫描（#4）
        ro_tp = self.root / "ro-stdin.jsonl"
        _append_jsonl(ro_tp, {"type": "session_meta", "payload": {"id": "ro-stdin-1", "source": "subagent", "agent_role": "readonly-auditor", "cwd": str(self.project)}})
        for ws_input in ({"session_id": 7}, {"session_id": 7, "chars": "ls\n"}):
            ro_res = status_guard.handle_claude_pre_tool_use(
                {"_client": "codex", "session_id": "ro-stdin-1", "cwd": str(self.project), "transcript_path": str(ro_tp),
                 "tool_name": "functions.write_stdin", "tool_input": ws_input},
                self.state, self.logs,
            )
            self.assertEqual(ro_res.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

        main_tp = self.root / "main-stdin.jsonl"
        _append_jsonl(main_tp, {"type": "session_meta", "payload": {"id": "main-stdin-1", "source": "cli", "cwd": str(self.project)}})
        self.assertEqual(
            status_guard.handle_claude_pre_tool_use(
                {"_client": "codex", "session_id": "main-stdin-1", "cwd": str(self.project), "transcript_path": str(main_tp),
                 "tool_name": "functions.write_stdin", "tool_input": {"session_id": 7, "chars": ""}},
                self.state, self.logs,
            ),
            {},
        )
        for field in ("chars", "input"):
            ws_hit = status_guard.handle_claude_pre_tool_use(
                {"_client": "codex", "session_id": "main-stdin-1", "cwd": str(self.project), "transcript_path": str(main_tp),
                 "tool_name": "functions.write_stdin", "tool_input": {"session_id": 7, field: f"rm -rf {out_file}\n"}},
                self.state, self.logs,
            )
            self.assertEqual(ws_hit.get("hookSpecificOutput", {}).get("permissionDecision"), "ask", field)

        # 4) _check_dispatch_locked 纳入 shell / shell_command 并兼容列表态 command；轮询传入 base_cwd or cwd
        for s_tool, s_input in (
            ("functions.shell", {"command": ["codex", "exec", "work"]}),
            ("shell_command", {"command": ["/bin/bash", "-lc", "claude -p work"]}),
        ):
            d_res = status_guard.handle_claude_pre_tool_use(
                {"_client": "codex", "session_id": "main-stdin-1", "cwd": str(self.project), "transcript_path": str(main_tp),
                 "tool_name": s_tool, "tool_input": s_input},
                self.state, self.logs,
            )
            self.assertEqual(d_res.get("hookSpecificOutput", {}).get("permissionDecision"), "deny", s_tool)

        sub_dir = self.project / "sub_poll"
        sub_dir.mkdir(exist_ok=True)
        (sub_dir / "solver.log").write_text("iter 1\n", encoding="utf-8")
        poll_payload = {
            "_client": "codex", "session_id": "poll-workdir-sid", "cwd": str(self.project),
            "transcript_path": str(main_tp), "tool_name": "functions.shell",
            "tool_input": {"command": ["tail", "-n", "5", "solver.log"], "workdir": "sub_poll"},
        }
        self.assertEqual(status_guard.handle_claude_pre_tool_use(poll_payload, self.state, self.logs), {})
        self.assertEqual(
            status_guard.handle_claude_pre_tool_use(poll_payload, self.state, self.logs)["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_r2_s2_04_cd_tracking_from_shell_diff_host_vars_and_empty_patch(self):
        outside = self.root / "outside_r2_04"
        outside.mkdir()
        out_posix = outside.as_posix()
        in_posix = (self.project / "anchored_ok.txt").as_posix()

        def decide_agy(cmd: str) -> str:
            with unittest.mock.patch.dict(os.environ, {"USER_GUARD_DISABLE_TICKET_GATE": "1"}):
                return status_guard.handle_pre_tool_use({
                    "conversationId": "r2-cd-conv",
                    "cwd": str(self.project),
                    "workspacePaths": [str(self.project)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}},
                })["decision"]

        # 1) 移除递归 cd 剥离后：cd "./$(...)" 必被扫描，连续多层 cd . 不触发深度超限
        self.assertEqual(decide_agy(f'cd "./$(touch {out_posix}/pwn)" && echo ok'), "force_ask")
        self.assertEqual(decide_agy("cd . && cd . && cd . && cd . && cd . && cd . && cd . && echo ok"), "allow")
        # 裸 cd / pushd 切至 Path.home()；cd - 与 cd "$OUT" 将 clause_cwd 置为 None 拦截后续相对写删
        for cd_bad in (
            "cd && touch leak_in_home.txt",
            "pushd && touch leak_in_home.txt",
            "cd - && rm -f rel_target.txt",
            'cd "$OUT_DIR" && touch rel_target.txt',
            '''cd "${WORK:-/outside}" && python3 -c "open('rel.txt', 'w')"''',
        ):
            self.assertEqual(decide_agy(cd_bad), "force_ask", cd_bad)
        self.assertEqual(decide_agy(f'cd - && touch "{in_posix}"'), "allow")

        # 2) _is_write_target_allowed(..., from_shell=False) 不剥离尾随 ;,) 或单引号（防软链同名绕过）
        self.assertFalse(hasattr(status_guard, "_LOCAL_VAR_TOKEN_RE"))
        symlink_ok = True
        try:
            (self.project / "safe_base").write_text("ok", encoding="utf-8")
            (self.project / "safe_base)").symlink_to(outside / "secret_paren.txt")
            (self.project / "safe_quote'").symlink_to(outside / "secret_quote.txt")
        except OSError:
            symlink_ok = False
        if symlink_ok:
            for tricky_name in ("safe_base)", "safe_quote'"):
                ag_w = status_guard.handle_pre_tool_use({
                    "conversationId": "r2-from-shell",
                    "cwd": str(self.project),
                    "workspacePaths": [str(self.project)],
                    "toolCall": {"name": "write_to_file", "args": {"TargetFile": str(self.project / tricky_name), "CodeContent": "x"}},
                })
                self.assertEqual(ag_w["decision"], "force_ask", tricky_name)
                cl_w = status_guard.handle_claude_pre_tool_use(
                    {"_client": "claude", "session_id": "r2-fs-cl", "cwd": str(self.project),
                     "tool_name": "Write", "tool_input": {"file_path": str(self.project / tricky_name)}},
                    self.state, self.logs,
                )
                self.assertEqual(cl_w.get("hookSpecificOutput", {}).get("permissionDecision"), "ask", tricky_name)

        # 3) 非首字符 ${OUT:-...} 与 $1 拦截；$PWD / $TMP / ${env:TEMP} 放行；POSIX %20 文件名不误拦；空 apply_patch 保守拦截
        for dyn_bad in (
            "echo hi > ./${OUT:-/outside/a}",
            "rm -rf sub/$1",
            'touch "prefix_${TARGET_DIR}/a.txt"',
        ):
            self.assertEqual(decide_agy(dyn_bad), "force_ask", dyn_bad)
        os_tmp = self.root / "isolated_os_tmp"
        os_tmp.mkdir(exist_ok=True)
        with unittest.mock.patch.object(tempfile, "tempdir", str(os_tmp)), \
                unittest.mock.patch.dict(os.environ, {"TMPDIR": str(os_tmp), "TEMP": str(os_tmp), "TMP": str(os_tmp)}):
            for env_ok in (
                'echo hi > "$PWD/inside.txt"',
                'touch "${PWD}/inside2.txt"',
                'echo hi > "$TMP/tmp_ok.txt"',
                'echo hi > "${TMP}/tmp_ok2.txt"',
                'echo hi > "${env:TEMP}/tmp_ok3.txt"',
                'echo hi > "${env:TMP}/tmp_ok4.txt"',
                'echo hi > "${env:TMPDIR}/tmp_ok5.txt"',
                'echo hi > "my%20file%20name.txt"',
                'echo hi > "%20leading_space.txt"',
            ):
                self.assertEqual(decide_agy(env_ok), "allow", env_ok)
            pct_utf8_cmd = 'echo hi > "%E4%B8%AD%E6%96%87.md"'
            with unittest.mock.patch.object(status_guard.os, "name", "posix"):
                self.assertFalse(status_guard._has_unexpanded_dynamic_var("%E4%B8%AD%E6%96%87.md"))
            if os.name != "nt":
                self.assertEqual(decide_agy(pct_utf8_cmd), "allow", pct_utf8_cmd)
            else:
                # Windows 下 %E4% 与 cmd.exe 环境变量语法重叠，按保守策略交由人工确认
                self.assertEqual(decide_agy(pct_utf8_cmd), "force_ask", pct_utf8_cmd)

        for empty_patch_inp in ({}, {"patch": ""}, {"patch": "   \n  "}):
            ap_res = status_guard.handle_claude_pre_tool_use(
                {"_client": "codex", "session_id": "r2-empty-patch", "cwd": str(self.project),
                 "tool_name": "functions.apply_patch", "tool_input": empty_patch_inp},
                self.state, self.logs,
            )
            self.assertEqual(ap_res.get("hookSpecificOutput", {}).get("permissionDecision"), "ask", empty_patch_inp)

    def test_r2_s2_05_ux_messages_pending_ticket_codex_stderr_and_env_isolation(self):
        import subprocess

        # 1) reply_repeat_guard 在 claude/codex 下不提 Reactive Wakeup，Antigravity 下保留
        self.assertIn("Reactive Wakeup", reply_repeat_guard._build_block_reason_text("antigravity"))
        self.assertEqual(reply_repeat_guard._build_block_reason_text("claude"), reply_repeat_guard.BLOCK_REASON_TEXT)
        self.assertEqual(reply_repeat_guard._build_block_reason_text("codex"), reply_repeat_guard.BLOCK_REASON_TEXT)

        # 2) _ticket_required_reason 区分已有未过期 PENDING 票据与完全无票据，且使用完整脚本路径而非裸 cae_harness.py
        no_ticket_msg = status_guard._ticket_required_reason("fixture", self.state)
        self.assertIn("尚无有效且未过期的批次票据", no_ticket_msg)
        self.assertIn(str(HOOKS_DIR.parent / "skills" / "cae-harness" / "scripts" / "cae_harness.py"), no_ticket_msg)
        self.approve(status="PENDING")
        pending_msg = status_guard._ticket_required_reason("fixture", self.state)
        self.assertIn("ticket-fixture", pending_msg)
        self.assertIn("PENDING", pending_msg)
        self.assertIn("--approve-ticket ticket-fixture", pending_msg)
        self.assertIn("compact_summary_card", pending_msg)
        plan_json = self.state / "dispatch_plans" / "ticket-fixture.json"
        plan_obj = json.loads(plan_json.read_text(encoding="utf-8"))
        plan_obj["card_detail_file"] = str(self.state / "dispatch_plans" / "ticket-fixture_card.md")
        user_words.atomic_write_json(plan_json, plan_obj)
        pending_with_link = status_guard._ticket_required_reason("fixture", self.state)
        self.assertIn("[ticket-fixture_card.md](file://", pending_with_link)

        # 3) --client codex 下 stderr 标签为 Codex，且显式传入沙箱目录避免触碰真实 ~/.codex
        for script_name, expected_tag in (
            ("status_guard.py", "Codex PreToolUse"),
            ("reply_repeat_guard.py", "Codex Stop"),
        ):
            proc = subprocess.run(
                [
                    sys.executable, "-B", str(HOOKS_DIR / script_name), "--client", "codex",
                    "--state-dir", str(self.state), "--log-dir", str(self.logs),
                ],
                input="{not-valid-json",
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn(expected_tag, proc.stderr)
            self.assertNotIn("Claude ", proc.stderr)
        self.assertFalse((self.fake_home / ".codex").exists())

        # 4) USER_GUARD_INSTALLED_DIR 空串或不存在目录时安全回退
        expected_hooks = (REPO_ROOT / "template" / "user-guard" / "hooks").resolve()
        self.assertEqual(_resolve_hooks_dir(""), expected_hooks)
        self.assertEqual(_resolve_hooks_dir("   "), expected_hooks)
        self.assertEqual(_resolve_hooks_dir(str(self.root / "nonexistent_install_dir")), expected_hooks)

    def test_v1171_powershell_prefix_escape_redirect_colon_and_readonly_verbs(self):
        import base64
        outside = self.root / "outside_v1171"
        outside.mkdir()
        enc_touch = base64.b64encode(f'Set-Content "{outside / "x.txt"}" "bad"'.encode("utf-16-le")).decode("ascii")
        for cmd in (
            f"powershell -e {enc_touch}",
            f"pwsh -ec {enc_touch}",
            f'powershell -com "Set-Content {outside / "a.txt"} 1"',
            f'git commit -m "$(touch {outside / "sub.txt"})"',
            f"echo hi >&{outside / 'redir.txt'}",
            f"Set-Content -Path:{outside / 'colon.txt'} hi",
            f"cp -t{outside} data.txt",
            f'python -c "f = open; f(r\'{outside / "alias.txt"}\', \'w\').write(\'x\')"',
        ):
            with self.subTest(cmd=cmd):
                res = self.pre("run_command", {"CommandLine": cmd})
                self.assertIn(res["decision"], ("force_ask", "deny"), cmd)

        # 2>&1 is safe fd duplication, not external file redirect
        self.assertEqual(self.pre("run_command", {"CommandLine": "git status 2>&1"})["decision"], "allow")

        # Readonly subagent blocks pytest / curl / make
        for verb_cmd in ("pytest -q", "curl https://example.com", "cmake --build build"):
            with self.subTest(verb_cmd=verb_cmd):
                self.assertIsNotNone(
                    status_guard._detect_readonly_subagent_command_mutation(verb_cmd, str(self.project)),
                    verb_cmd,
                )

    def test_v118_agy_parallel_subagent_ast_blowup_gzip_open_and_native_bill(self):
        import time as _time

        # 1) Antigravity 无 call_id 并发 invoke_subagent 计数、轻任务放行与拒绝项排除（Bug P0-1）
        tp_light = self.root / "agy_light.jsonl"
        _append_jsonl(tp_light, {"type": "USER_INPUT", "step_index": 0, "content": "读取文档"})
        _append_jsonl(tp_light, {
            "type": "PLANNER_RESPONSE", "step_index": 1,
            "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": "R1"}}],
        })
        res_light = status_guard.handle_pre_tool_use({
            "conversationId": "v118-light",
            "cwd": str(self.project),
            "workspacePaths": [str(self.project)],
            "transcriptPath": str(tp_light),
            "toolCall": {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": "R1"}},
        })
        self.assertEqual(res_light["decision"], "allow")

        tp_unapproved = self.root / "agy_unapproved_2.jsonl"
        _append_jsonl(tp_unapproved, {"type": "USER_INPUT", "step_index": 0, "content": "并行读两份"})
        _append_jsonl(tp_unapproved, {
            "type": "PLANNER_RESPONSE", "step_index": 1,
            "tool_calls": [
                {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": "R1"}},
                {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": "R2"}},
            ],
        })
        res_unapp = status_guard.handle_pre_tool_use({
            "conversationId": "v118-unapp",
            "cwd": str(self.project),
            "workspacePaths": [str(self.project)],
            "transcriptPath": str(tp_unapproved),
            "toolCall": {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": "R1"}},
        })
        self.assertEqual(res_unapp["decision"], "deny")

        # 获批 4 路并发 + 前序曾有被 deny 记录：4 路应全部 allow
        self.approve(channel="native_subagent", sid="v118-par4", count=4)
        tp_par4 = self.root / "agy_par4.jsonl"
        _append_jsonl(tp_par4, {"type": "USER_INPUT", "step_index": 0, "content": "四路并行读取"})
        _append_jsonl(tp_par4, {
            "type": "PLANNER_RESPONSE", "step_index": 1,
            "tool_calls": [{"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": "OldDenied"}}],
        })
        _append_jsonl(tp_par4, {"type": "GENERIC", "step_index": 2, "content": "tool call denied by pre-tool hook"})
        _append_jsonl(tp_par4, {
            "type": "PLANNER_RESPONSE", "step_index": 3,
            "tool_calls": [
                {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": f"Worker{i}"}}
                for i in range(1, 5)
            ],
        })
        for i in range(1, 5):
            r = status_guard.handle_pre_tool_use({
                "conversationId": "v118-par4",
                "cwd": str(self.project),
                "workspacePaths": [str(self.project)],
                "transcriptPath": str(tp_par4),
                "toolCall": {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": f"Worker{i}"}},
            })
            self.assertEqual(r["decision"], "allow", f"stream {i} should be allowed: {r}")

        # 2) AST _AstDict 多分支性能（Bug P0-2）与节点预算防挂死护栏
        keys = [status_guard._normalize_cross_env_lexical(str(self.project))]
        outside = self.root / "outside_v118"
        outside.mkdir(exist_ok=True)
        out_posix = (outside / "leak.txt").as_posix()
        branches_code = ["d = {'a': '1', 'b': '2', 'c': '3'}"]
        for idx in range(18):
            branches_code.append(
                f"if cond_{idx}:\n"
                f"    d['k{idx % 6}'] = 'x{idx}' or 'y{idx}'\n"
                f"else:\n"
                f"    d['k{(idx + 1) % 6}'] = 'z{idx}'\n"
            )
        safe_code = "\n".join(branches_code) + "\nprint(d.get('a'))\n"
        t0 = _time.monotonic()
        self.assertIsNone(status_guard._scan_inline_python_code(safe_code, str(self.project), keys, keys, False))
        self.assertLess(_time.monotonic() - t0, 1.0)
        unsafe_code = "\n".join(branches_code) + f"\nopen('{out_posix}', 'w').write('x')\n"
        self.assertIsNotNone(status_guard._scan_inline_python_code(unsafe_code, str(self.project), keys, keys, False))
        with unittest.mock.patch.object(status_guard, "_AST_MAX_VISITED_NODES", 20):
            budget_hit = status_guard._scan_inline_python_code(safe_code, str(self.project), keys, keys, False)
            self.assertIsNotNone(budget_hit)
            self.assertEqual(budget_hit[1], "<AST_BUDGET_LIMIT>")

        # 3) gzip/bz2/lzma/codecs/tarfile/Image 只读 open 放行、写入拦截（Bug P1-6）
        ext_gz = (outside / "events.jsonl.gz").as_posix()
        ext_tar = (outside / "archive.tar.xz").as_posix()
        for ro_py in (
            f"import gzip\nwith gzip.open('{ext_gz}', 'rt', encoding='utf-8') as f: print(f.readline())",
            f"import bz2\nwith bz2.open('{ext_gz}', 'rb') as f: f.read(8)",
            f"import lzma\nwith lzma.open('{ext_gz}', 'rt') as f: f.read(8)",
            f"import codecs\nwith codecs.open('{ext_gz}', 'r', 'utf-8') as f: f.read(8)",
            f"import tarfile\nwith tarfile.open('{ext_tar}', 'r:xz') as tf: tf.getnames()",
            f"from PIL import Image\nimg = Image.open('{ext_gz}')",
        ):
            with self.subTest(ro_py=ro_py):
                self.assertIsNone(status_guard._scan_inline_python_code(ro_py, str(self.project), keys, keys, False), ro_py)
        for rw_py in (
            f"import gzip\nwith gzip.open('{ext_gz}', 'wt') as f: f.write('x')",
            f"import tarfile\nwith tarfile.open('{ext_tar}', 'w:gz') as tf: pass",
        ):
            with self.subTest(rw_py=rw_py):
                self.assertIsNotNone(status_guard._scan_inline_python_code(rw_py, str(self.project), keys, keys, False), rw_py)

        # 4) reply_repeat_guard._native_completion_bill 排除 deny 并识别无 call_id 子代理与后台 workflow（Bug P1-5）
        tp_bill = self.root / "agy_native_bill.jsonl"
        _append_jsonl(tp_bill, {"type": "USER_INPUT", "step_index": 0, "content": "执行并汇报"})
        _append_jsonl(tp_bill, {
            "type": "PLANNER_RESPONSE", "step_index": 1,
            "tool_calls": [
                {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": "DeniedOne"}},
                {"name": "invoke_subagent", "args": {"TypeName": "readonly-reader", "Role": "ReaderA"}},
                {"name": "invoke_subagent", "args": {"TypeName": "readonly-auditor", "Role": "AuditB"}},
                {"name": "run_workflow", "args": {"Description": "并行扫描"}},
            ],
        })
        _append_jsonl(tp_bill, {"type": "GENERIC", "step_index": 2, "content": "Encountered error in tool execution: tool call denied by pre-tool hook"})
        _append_jsonl(tp_bill, {"type": "GENERIC", "step_index": 3, "content": 'Created the following subagent: {"conversationId": "conv-a", "logAbsoluteUri": "file:///tmp/conv-a.jsonl"}'})
        _append_jsonl(tp_bill, {"type": "GENERIC", "step_index": 4, "content": 'Created the following subagent: {"conversationId": "conv-b", "logAbsoluteUri": "file:///tmp/conv-b.jsonl"}'})
        _append_jsonl(tp_bill, {"type": "GENERIC", "step_index": 5, "content": "Workflow is running as a background task with id: task-99"})
        _append_jsonl(tp_bill, {"type": "SYSTEM_MESSAGE", "step_index": 6, "content": "[Message] sender=conv-a content=ReaderA 完成摘录"})
        _append_jsonl(tp_bill, {"type": "SYSTEM_MESSAGE", "step_index": 7, "content": "[Message] sender=conv-b content=AuditB 完成复核"})
        _append_jsonl(tp_bill, {"type": "SYSTEM_MESSAGE", "step_index": 8, "content": "[Message] sender=v118-bill/task-99 content=Task finished with result: ok"})
        bill = reply_repeat_guard._native_completion_bill({"transcriptPath": str(tp_bill)}, {})
        self.assertIsNotNone(bill)
        self.assertEqual(len(bill["streams"]), 3)
        native_ids = {s["native_id"] for s in bill["streams"]}
        self.assertEqual(native_ids, {"conv-a", "conv-b", "task-99"})
        self.assertTrue(any(s["channel"] == "native_workflow" for s in bill["streams"]))

    def test_v1175_shell_comment_quotes_ast_indirect_and_bash_flag_unwrapping(self):
        project = self.root / "proj_v1175"
        project.mkdir()
        outside = self.root / "outside_v1175"
        outside.mkdir()
        out_file = (outside / "escape.txt").as_posix()
        keys = [status_guard._normalize_cross_env_lexical(str(project))]

        # 1) Shell 行注释中的未闭合单引号不可吞并下一行子句绕过 R4 与交接冻结门禁
        comment_bypass = f"echo ok # '\nrm -rf {out_file}"
        self.assertIsNotNone(
            status_guard._detect_command_external_mutation(comment_bypass, str(project), keys, keys, False)
        )
        self.assertEqual(status_guard.handoff_gate._classify_run_command("echo ok # '\ngit push origin main"), "blocked_always")

        # 2) git commit -m 无引号消息紧贴重定向、bash -v / -o pipefail / +e -c 拆壳与 python -R -c
        for bad_shell in (
            f"git commit -m msg>{out_file}",
            f"bash -v -c 'rm -rf {out_file}'",
            f"bash -o pipefail -c 'rm -rf {out_file}'",
            f"bash +e -c 'rm -rf {out_file}'",
            f"python3 -R -c \"open('{out_file}', 'w').write('x')\"",
        ):
            self.assertIsNotNone(
                status_guard._detect_command_external_mutation(bad_shell, str(project), keys, keys, False),
                bad_shell,
            )
        self.assertEqual(status_guard.handoff_gate._classify_run_command("bash -v -c 'git push'"), "blocked_always")
        self.assertEqual(status_guard.handoff_gate._classify_run_command("bash -o pipefail -c 'git push'"), "blocked_always")

        # 3) Python AST 通配导入、动态导入、类方法、容器下标函数、字典原地修改与 Image 遮蔽拦截
        for bad_py in (
            f"from os import *\nremove('{out_file}')",
            f"__import__('os').remove('{out_file}')",
            f"import importlib\nimportlib.import_module('shutil').rmtree('{outside.as_posix()}')",
            f"exec(\"open('{out_file}', 'w').write('x')\")",
            f"class C:\n    def w(self, p):\n        open(p, 'w').write('x')\nC().w('{out_file}')",
            f"def w(p):\n    open(p, 'w').write('x')\nlist(map(w, ['{out_file}']))",
            f"fns = [lambda p: open(p, 'w').write('x')]\nfns[0]('{out_file}')",
            f"d = {{'p': 'inside.txt'}}\nd.update({{'p': '{out_file}'}})\nopen(d['p'], 'w')",
            f"from pathlib import Path\nImage = Path('{out_file}')\nImage.open('w').write('x')",
            f"import zipfile\nzipfile.ZipFile('{out_file}', 'w')",
        ):
            self.assertIsNotNone(
                status_guard._scan_inline_python_code(bad_py, str(project), keys, keys, False),
                bad_py,
            )
        for safe_py in (
            "class C:\n    def w(self, p):\n        open(p, 'w').write('x')\nC().w('inside.txt')",
            "fns = [lambda p: open(p, 'w').write('x')]\nfns[0]('inside.txt')",
            "import zipfile\nzipfile.ZipFile('archive.zip', 'r')",
        ):
            self.assertIsNone(
                status_guard._scan_inline_python_code(safe_py, str(project), keys, keys, False),
                safe_py,
            )

        # 4) _INSTALLER_INVOKE_RE 在长链 env/sudo 下保持毫秒级线性时间无 ReDoS
        t0 = time.monotonic()
        long_env = "env " * 30 + "echo ok"
        self.assertIsNone(status_guard._detect_command_external_mutation(long_env, str(project), keys, keys, False))
        self.assertLess(time.monotonic() - t0, 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
