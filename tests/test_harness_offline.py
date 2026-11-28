# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""双向编排的离线回归，所有模型调用均使用替身。"""
import contextlib
import base64
import datetime
import importlib.util
import io
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'template' / 'user-guard' / 'skills' / 'cae-harness' / 'scripts' / 'cae_harness.py'
for installed_key in (
    'USER_GUARD_INSTALLED_CODEX_DIR',
    'USER_GUARD_INSTALLED_CLAUDE_DIR',
    'USER_GUARD_INSTALLED_DSH_DIR',
    'USER_GUARD_INSTALLED_DIR',
):
    installed_root = os.environ.get(installed_key)
    installed_script = Path(installed_root) / 'skills' / 'cae-harness' / 'scripts' / 'cae_harness.py' if installed_root else None
    if installed_script is not None and installed_script.is_file():
        SCRIPT = installed_script
        break
spec = importlib.util.spec_from_file_location('cae_harness_test', SCRIPT)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def backends(budget=120000):
    catalogs = {'codex': [{'slug': 'fixture-code', 'provider': 'openai', 'supported_efforts': ['high', 'max']}],
                'agy': [{'slug': 'gemini-7-pro-high'}]}
    fetched_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {name: {'available': True, 'binary': name + ('.exe' if os.name == 'nt' else ''),
                   'safe_input_budget_tokens': budget, 'requested_input_budget_tokens': budget,
                   'model': None, 'windows_interop': False, 'models': catalogs[name],
                   'authentication_status': 'authenticated',
                   'model_catalog': {'source': name + '_client_cache', 'model_count': len(catalogs[name]),
                                     'fetched_at': fetched_at, 'freshness': 'within_assessment_window'}}
            for name in ('codex', 'agy')}


AUDIT = {'summary': '已核对', 'findings': [], 'read_files': ['a.py'], 'unread_files': []}
TASK = {'summary': '已推导', 'content': '证明正文', 'evidence': ['输入定义'], 'unverified': []}


_INSTALLED_GUARD_ENV_KEYS = frozenset({
    'USER_GUARD_INSTALLED_CODEX_DIR',
    'USER_GUARD_INSTALLED_CLAUDE_DIR',
    'USER_GUARD_INSTALLED_DSH_DIR',
    'USER_GUARD_INSTALLED_DIR',
})
_STRIPPED_ENV_PREFIXES = (
    'CAE_', 'AGY_', 'ANTIGRAVITY_', 'CODEX_', 'CLAUDE', 'ANTHROPIC_', 'DSH_', 'XDG_',
)
_STRIPPED_ENV_EXACT = frozenset({
    'PLUGIN_DATA', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'PYTHONPATH',
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_COMMON_DIR', 'GIT_CEILING_DIRECTORIES', 'GIT_PREFIX',
})
_STRIPPED_PROXY_UPPER = frozenset({'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'})


def _is_stripped_host_env(key):
    if key in _STRIPPED_ENV_EXACT or key.upper() in _STRIPPED_PROXY_UPPER:
        return True
    if key.startswith('USER_GUARD_'):
        return key not in _INSTALLED_GUARD_ENV_KEYS
    return key.startswith(_STRIPPED_ENV_PREFIXES)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        harness._CANCELLED.clear()
        harness._SESSION_QUOTA_BLOCKED.clear()
        self.temp = tempfile.TemporaryDirectory(prefix='cae-test-')
        self.root = Path(self.temp.name).resolve()
        clean_env = {
            k: v for k, v in os.environ.items()
            if not _is_stripped_host_env(k)
        }
        clean_env['USER_GUARD_STATE_DIR'] = str(self.root / 'state')
        clean_env['LOCALAPPDATA'] = str(self.root / 'local-app-data')
        env_patch = patch.dict(os.environ, clean_env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        home_patch = patch.object(harness.Path, 'home', return_value=self.root)
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def tearDown(self):
        harness._CANCELLED.clear()
        harness._SESSION_QUOTA_BLOCKED.clear()
        self.temp.cleanup()

    def capture(self, function, args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = function(args)
        return code, json.loads(stream.getvalue())

    def args(self, *tokens):
        return harness.build_arg_parser().parse_args(list(tokens))

    def four_backends(self):
        engines = backends()
        with patch.object(harness, '_claude_home', return_value=(self.root / 'claude', False)), \
             patch.object(harness, '_dsh_home', return_value=(self.root / 'dsh', False)), patch.dict(os.environ, {}, clear=True):
            engines['claude'] = harness._claude_info('claude.exe')
            engines['dsh'] = harness._dsh_info('dsh.cmd')
        engines['claude'].update(llm_configured=True, llm_source='settings_env', model='kimi-k3',
                                 models=[{'slug': 'kimi-k3', 'provider': 'claude_configured'},
                                         {'slug': 'deepseek-v4-pro', 'provider': 'claude_configured'}])
        engines['dsh'].update(llm_configured=True, llm_source='api_key', dsh_provider='deepseek-official')
        for name, info in engines.items():
            info['config_home'] = str(self.root / (name + '-config'))
        return engines

    def test_prompt_backend_parallel_cues_preserve_routing(self):
        for prompt in ("用codex等多工作流", "使用 codex 等做检查", "用 codex 和 dsh 审查",
                       "用 codex 与 cc 检查", "用 codex 及 agy 检查", "用 codex、dsh 检查",
                       "用 codex/dsh 检查", "用 codex 或 dsh 检查", "use codex and dsh", "use codex or dsh"):
            with self.subTest(prompt=prompt):
                self.assertEqual(harness._extract_prompt_backend_hint(prompt), '')
        for prompt, backend in (("用 codex 审查", 'codex'), ("请交给 dsh 处理", 'dsh'),
                                ("用 codex 高挡位", 'codex'), ("不要用 codex 审查", '')):
            self.assertEqual(harness._extract_prompt_backend_hint(prompt), backend)

    def test_probe_assess_public_stdout_and_output_options(self):
        for command, function in (('probe', harness.cmd_probe), ('assess', harness.cmd_assess)):
            for redact_stdout in (False, True):
                for raw_file in (False, True):
                    with self.subTest(command=command, redact_stdout=redact_stdout, raw_file=raw_file):
                        output = self.root / (command + '.json')
                        tokens = [command, '--caller', 'codex', '--output', str(output)]
                        if redact_stdout:
                            tokens.append('--redact')
                        if raw_file:
                            tokens.append('--no-redact-output')
                        args = self.args(*tokens)
                        sentinel = 'private-fixture-marker'
                        with patch.object(harness, '_args_backends', return_value={}), \
                             patch.object(harness, '_fleet_profile_summary', return_value={'marker': sentinel}), \
                             patch.object(harness, '_redact_public', side_effect=lambda payload: json.loads(
                                 json.dumps(payload).replace(sentinel, '[redacted]'))):
                            code, result = self.capture(function, args)
                        self.assertEqual(code, 0)
                        self.assertEqual(result['fleet_profile']['marker'], '[redacted]' if redact_stdout else sentinel)
                        saved = json.loads(output.read_text(encoding='utf-8'))
                        self.assertEqual(saved['fleet_profile']['marker'], sentinel if raw_file else '[redacted]')

    def test_probe_assess_redact_real_home_paths(self):
        for command, function in (('probe', harness.cmd_probe), ('assess', harness.cmd_assess)):
            args = self.args(command, '--caller', 'codex', '--redact')
            with patch.object(harness, '_args_backends', return_value={}), \
                 patch.object(harness, '_fleet_profile_summary', return_value={'path': str(self.root / 'private.json')}):
                code, payload = self.capture(function, args)
            self.assertEqual(code, 0)
            self.assertEqual(payload['fleet_profile']['path'], '<HOME>' + os.sep + 'private.json')

    def test_four_peer_alias_callers_routing_and_budgets(self):
        engines = self.four_backends()
        for caller, expected in (('antigravity', 'codex'), ('codex', 'agy'), ('claude', 'codex'), ('cc', 'codex'), ('dsh', 'codex')):
            with self.subTest(caller=caller):
                self.assertEqual(harness.choose_engine(engines, caller), expected + ':default')
                self.assertEqual(harness._engine_parts('cc:kimi-k3'), ('claude', 'kimi-k3'))
                self.assertEqual(harness.choose_engine(engines, caller, 'cc:kimi-k3'), 'claude:kimi-k3')
                parsed = self.args('probe', '--caller', caller, '--claude-input-budget', '30000', '--dsh-input-budget', '40000')
                self.assertEqual((parsed.claude_input_budget, parsed.dsh_input_budget), (30000, 40000))
        with patch.dict(os.environ, {'CAE_HARNESS_CALLER': 'cc'}, clear=True):
            self.assertEqual(harness.resolve_caller('auto'), 'claude')
        self.assertEqual(harness.resolve_caller('agy'), 'antigravity')
        engines['agy']['available'] = engines['codex']['available'] = False
        self.assertEqual(harness.choose_engine(engines, 'codex'), 'claude:default')
        engines['claude']['llm_configured'] = False
        self.assertEqual(harness.choose_engine(engines, 'codex'), 'dsh:default')
        self.assertEqual(harness.choose_engine(engines, 'dsh'), 'dsh:default')
        engines['dsh']['llm_configured'] = False
        self.assertFalse(harness._peer_auto_ready(engines, 'dsh'))
        with self.assertRaises(harness.HarnessError):
            harness.choose_engine(engines, 'dsh')

    def test_backend_cues_reject_negation_compilers_and_filename_suffixes(self):
        positives = {'请用 CC 审查': 'claude', '调用 Claude Code 检查': 'claude', 'use cc cli to review': 'claude',
                     '用 DSH 跑一下': 'dsh', '调用 DeepSeek Harness': 'dsh', 'switch to dsh cli': 'dsh',
                     '请使用 codex 检查': 'codex', 'via antigravity': 'agy', '交给反重力审查': 'agy'}
        for prompt, expected in positives.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(harness._extract_prompt_backend_hint(prompt), expected)
        for prompt in ('不要用 CC', '不用 DSH', 'do not use cc', 'never invoke dsh', 'CC=gcc foo.cc',
                       '编译 foo.cc', '检查 bar.dsh', 'use cc=gcc', 'use cc.flags', '用 DSH.config',
                       '`use cc`', '> use dsh\n审查文件'):
            with self.subTest(prompt=prompt):
                self.assertEqual(harness._extract_prompt_backend_hint(prompt), '')
        self.assertEqual(harness._extract_prompt_backend_hint('不要用 CC，而是用 DSH 检查'), 'dsh')
        engines = self.four_backends()
        self.assertEqual(harness.choose_engine(engines, 'codex', prompt_text='请用 CC 审查'), 'claude:default')
        self.assertEqual(harness.choose_engine(engines, 'codex', 'codex:default', '请用 CC 审查'), 'codex:default')
        for prompt in ('用 CC 模型', '用 DSH 模型', '用 Claude 模型', '用 Harness 模型', '用 Claude Code 模型', '用 DeepSeek Harness 模型'):
            self.assertEqual(harness._extract_prompt_model_hint(prompt), '')
        for prompt, model in (('用 Kimi 模型', 'kimi-k3'), ('用 K3 模型', 'kimi-k3'), ('用 DeepSeek 模型', 'deepseek-v4-pro')):
            self.assertEqual(harness.resolve_execution_profile(engines, 'claude:default', prompt_text=prompt)['model'], model)

    def test_backend_prompt_hints_reach_task_gate_and_audit(self):
        engines = self.four_backends()
        (self.root / 'a.py').write_text('value = 1\n', encoding='utf-8')
        cases = ((harness.cmd_task, ('task', '--kind', 'review', '--task', '请用 CC 审查'), 'engine', 'claude:default'),
                 (harness.cmd_gate, ('gate', '--task', '用 DSH 跑一下', '--verify-cmd', 'verify'), 'engine', 'dsh:default'),
                 (harness.cmd_audit, ('audit', '--single', '--focus', '调用 DeepSeek Harness'), 'plan', 'dsh:default'))
        for function, tokens, key, expected in cases:
            with self.subTest(command=tokens[0]), patch.object(harness, '_args_backends', return_value=engines):
                args = self.args(*tokens, '--caller', 'codex', '--target', str(self.root), '--dry-run')
                code, report = self.capture(function, args)
                self.assertEqual(code, 0)
                self.assertEqual(report[key]['left_engine'] if key == 'plan' else report[key], expected)
        self.assertEqual(harness._audit_engines(engines, True, 'cc:kimi-k3', '', False, 'dsh'),
                         ('claude:kimi-k3', 'agy:default'))

    def test_four_peer_probe_reads_credentials_without_running_processes_or_leaking_tokens(self):
        (self.root / 'settings.json').write_text(json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'fixture-token',
            'ANTHROPIC_MODEL': 'kimi-k3'}}), encoding='utf-8')
        (self.root / '.env').write_text('DEEPSEEK_API_KEY="fixture-dsh-secret"\n', encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True), patch.object(harness, '_find_binary', return_value='fixture'), \
             patch.object(harness, '_codex_home', return_value=(self.root, False)), \
             patch.object(harness, '_agy_home', return_value=(self.root, False)), \
             patch.object(harness, '_claude_home', return_value=(self.root, False)), \
             patch.object(harness, '_dsh_home', return_value=(self.root, False)), \
             patch.object(harness, 'run_process') as run, patch.object(harness.subprocess, 'run') as subprocess_run:
            result = harness.probe_backends(claude_input_budget=30000, dsh_input_budget=40000)
        run.assert_not_called()
        subprocess_run.assert_not_called()
        self.assertEqual(set(result), {'codex', 'agy', 'claude', 'dsh'})
        self.assertEqual(result['claude']['llm_source'], 'settings_env')
        self.assertEqual(result['dsh']['llm_source'], 'api_key')
        self.assertEqual(result['dsh']['safe_input_budget_tokens'], 40000)
        self.assertEqual(result['claude']['safe_input_budget_tokens'], 30000)
        self.assertEqual(result['dsh']['frontier_model'], 'deepseek-v4-pro')
        self.assertNotIn('fixture-token', json.dumps(result))
        self.assertNotIn('fixture-dsh-secret', json.dumps(result))

    def test_claude_sources_placeholder_helper_and_oauth_auth_status(self):
        settings = self.root / 'settings.json'
        cases = (({'env': {'ANTHROPIC_AUTH_TOKEN': ''}}, 'placeholder_token'),
                 ({'env': {'ANTHROPIC_API_KEY': 'PASTE_KEY_HERE'}}, 'placeholder_token'),
                 ({'env': {'ANTHROPIC_AUTH_TOKEN': 'PASTE_TOKEN', 'ANTHROPIC_API_KEY': 'fixture-token'}}, 'placeholder_token'),
                 ({'env': {'ANTHROPIC_AUTH_TOKEN': 'fixture-token'}}, 'settings_env'),
                 ({'apiKeyHelper': 'fixture-helper'}, 'settings_env'), ({}, 'unconfigured'))
        for doc, expected in cases:
            with self.subTest(source=expected, doc=doc):
                settings.write_text(json.dumps(doc), encoding='utf-8')
                with patch.dict(os.environ, {}, clear=True), patch.object(harness, '_claude_home', return_value=(self.root, False)):
                    info = harness._claude_info('claude.exe')
                self.assertEqual(info['llm_source'], expected)
                with patch.object(harness, 'run_process') as run:
                    if expected == 'placeholder_token':
                        with self.assertRaisesRegex(harness.HarnessError, '占位符'):
                            harness._verify_claude_llm_ready(info)
                        run.assert_not_called()
                    elif expected == 'settings_env':
                        harness._verify_claude_llm_ready(info)
                        run.assert_not_called()
        (self.root / '.credentials.json').write_text('{}', encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True), patch.object(harness, '_claude_home', return_value=(self.root, False)):
            info = harness._claude_info('claude.exe')
        self.assertEqual(info['llm_source'], 'credentials_file')
        for payload, succeeds in (({'loggedIn': True}, True), ({'loggedIn': False}, False), ({'loggedIn': 'true'}, False), ([], False)):
            result = subprocess.CompletedProcess([], 0, json.dumps(payload), '')
            with self.subTest(payload=payload), patch.object(harness, 'run_process', return_value=result) as run, \
                 patch.object(harness, '_is_posix', return_value=False):
                if succeeds:
                    harness._verify_claude_llm_ready(info)
                else:
                    with self.assertRaisesRegex(harness.HarnessError, '未登录'):
                        harness._verify_claude_llm_ready(info)
                self.assertEqual(run.call_args.args[0], ['claude.exe', 'auth', 'status'])
                self.assertEqual(run.call_args.kwargs['timeout_sec'], 15)
                self.assertEqual(run.call_args.kwargs['env']['CLAUDE_CONFIG_DIR'], str(self.root))

    def test_claude_native_logout_falls_back_before_execution(self):
        info = {'binary': 'claude', 'available': True, 'windows_interop': False, 'llm_source': 'credentials_file',
                'config_home': str(self.root / 'native')}
        alternate = {**info, 'binary': 'claude.exe', 'windows_interop': True, 'config_home': str(self.root / 'windows')}
        results = [subprocess.CompletedProcess([], 0, '{"loggedIn":false}', ''),
                   subprocess.CompletedProcess([], 0, '{"loggedIn":true}', '')]
        with patch.dict(os.environ, {}, clear=True), patch.object(harness, '_is_posix', return_value=True), \
             patch.object(harness, '_find_windows_claude_in_wsl', return_value='claude.exe'), \
             patch.object(harness, '_claude_info', return_value=alternate), \
             patch.object(harness, '_run_claude_auth', side_effect=results) as auth:
            harness._verify_claude_llm_ready(info)
        self.assertEqual(auth.call_count, 2)
        self.assertTrue(info['windows_interop'])
        self.assertTrue(info['llm_configured'])
        self.assertEqual(info['authentication_status'], 'authenticated')
        self.assertEqual(info['fallback_from']['binary'], 'claude')

    def test_claude_router_health_and_card_credentials_are_required_and_readonly(self):
        cfg_dir = self.root / 'router'
        cfg_dir.mkdir()
        db = self.root / 'cards.db'
        con = sqlite3.connect(db)
        try:
            con.execute('CREATE TABLE providers (name TEXT, app_type TEXT, settings_config TEXT)')
            con.executemany('INSERT INTO providers VALUES (?, ?, ?)', [
                ('Kimi', 'claude', json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'fixture-kimi-token'}})),
                ('DeepSeek', 'claude', json.dumps({'env': {'ANTHROPIC_API_KEY': 'fixture-ds-token'}}))])
            con.commit()
        finally:
            con.close()
        (cfg_dir / 'router.json').write_text(json.dumps({'ccswitch_db': str(db), 'default_key_from_card': 'Kimi',
            'routes': [{'prefix': 'deepseek', 'key_from_card': 'DeepSeek'}]}), encoding='utf-8')
        (self.root / 'settings.json').write_text(json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'via-router',
            'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8240', 'ANTHROPIC_MODEL': 'kimi-k3'}}), encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True), patch.object(harness, '_claude_home', return_value=(self.root, False)):
            info = harness._claude_info('claude.exe')
        self.assertEqual(info['llm_source'], 'local_router')

        def health(ok=True, status=200):
            response = io.BytesIO(json.dumps({'ok': ok}).encode())
            response.status = status
            return response

        before = db.read_bytes(), db.stat().st_mtime_ns
        with patch.object(harness, 'urlopen', side_effect=lambda *a, **kw: health()) as request, \
             patch.object(harness.sqlite3, 'connect', wraps=sqlite3.connect) as connect, patch.object(harness, 'run_process') as run:
            harness._verify_claude_llm_ready(info, 'kimi-k3')
            harness._verify_claude_llm_ready(info, 'deepseek-v4-pro')
        run.assert_not_called()
        request.assert_called_with('http://127.0.0.1:8240/__router/health', timeout=2.0)
        self.assertTrue(all(call.args[0].endswith('?mode=ro') and call.kwargs['uri'] for call in connect.call_args_list))
        self.assertEqual((db.read_bytes(), db.stat().st_mtime_ns), before)
        for token in ('', 'PASTE_TOKEN', 'via-router'):
            con = sqlite3.connect(db)
            try:
                con.execute('UPDATE providers SET settings_config=? WHERE name=?',
                            (json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': token}}), 'DeepSeek'))
                con.commit()
            finally:
                con.close()
            with self.subTest(token=token), patch.object(harness, 'urlopen', side_effect=lambda *a, **kw: health()):
                with self.assertRaisesRegex(harness.HarnessError, '路由卡片'):
                    harness._verify_claude_llm_ready(info, 'deepseek-v4-pro')
        for ok, status in ((False, 200), (True, 503)):
            with patch.object(harness, 'urlopen', side_effect=lambda *a, **kw: health(ok, status)):
                with self.assertRaisesRegex(harness.HarnessError, '路由器未就绪'):
                    harness._verify_claude_llm_ready(info)
        with patch.object(harness, 'urlopen', side_effect=OSError('offline')):
            with self.assertRaises(harness.HarnessError):
                harness._verify_claude_llm_ready(info)

    def test_claude_invocation_inherits_settings_and_extracts_model_usage(self):
        engines = self.four_backends()
        envelope = {'type': 'result', 'is_error': False, 'structured_output': TASK,
                    'modelUsage': {'kimi-k3-actual': {'inputTokens': 1}}}
        for readonly in (True, False):
            def run(command, cwd, input_text, timeout_sec, **kwargs):
                self.assertEqual(cwd, self.root)
                self.assertEqual(input_text, '审查输入')
                self.assertEqual(command[:5], ['claude.exe', '-p', '--output-format', 'stream-json', '--verbose'])
                self.assertEqual(json.loads(command[command.index('--json-schema') + 1]), harness.TASK_JSON_SCHEMA)
                self.assertRegex(command[command.index('--session-id') + 1], r'^[a-f0-9-]{36}$')
                self.assertNotIn('--no-session-persistence', command)
                self.assertNotIn('--bare', command)
                self.assertEqual(command[command.index('--effort') + 1], 'max')
                self.assertEqual(kwargs['env']['CLAUDE_CONFIG_DIR'], engines['claude']['config_home'])
                if readonly:
                    self.assertEqual(command[command.index('--tools') + 1], 'Read,Grep,Glob')
                    self.assertEqual(command[command.index('--permission-prompts') + 1], 'none')
                    self.assertEqual(command[command.index('--permission-mode') + 1], 'dontAsk')
                else:
                    self.assertEqual(command[command.index('--tools') + 1], 'Read,Grep,Glob,Edit,Write,Bash')
                    self.assertEqual(command[command.index('--permission-mode') + 1], 'bypassPermissions')
                return subprocess.CompletedProcess(command, 0, json.dumps(envelope), '[claude-code:unrecognized_model] fixture warning')
            with self.subTest(readonly=readonly), patch.dict(os.environ, {'CAE_CLAUDE_HOME': engines['claude']['config_home']}), \
                 patch.object(harness, 'run_process', side_effect=run):
                receipt = {}
                result = harness.invoke_engine('cc:kimi-k3', self.root, '审查输入', engines, harness.TASK_JSON_SCHEMA,
                                               readonly=readonly, receipt=receipt)
            self.assertEqual(result, TASK)
            self.assertEqual(receipt['actual_model'], 'kimi-k3-actual')
            self.assertEqual(receipt['actual_model_source'], 'claude_result_model_usage')

    def test_dsh_credentials_and_readiness_without_subprocesses(self):
        cases = (({}, '', '', 'unconfigured'),
                 ({'DEEPSEEK_API_KEY': 'fixture-env-token'}, '', '', 'api_key'),
                 ({}, '# comment\nDEEPSEEK_API_KEY = "fixture-dotenv-token" # comment\n', '', 'api_key'),
                 ({}, 'DEEPSEEK_API_KEY=PASTE_KEY\n', 'deepseek-official:\n  DEEPSEEK_API_KEY: PASTE_KEY\n', 'unconfigured'),
                 ({}, '', "deepseek-official:\n  DEEPSEEK_API_KEY: 'fixture-yaml-token'\n", 'api_key'),
                 ({}, '', "'deepseek-official/DEEPSEEK_API_KEY': fixture-yaml-token\n", 'api_key'),
                 ({}, '', 'deepseek-official/DEEPSEEK_API_KEY:\n  value: fixture-yaml-token\n', 'api_key'),
                 ({}, '', "'deepseek-account-platform/fixture': 'fixture-account-token'\n", 'deepseek_account'),
                 ({}, '', 'deepseek-account-platform/fixture:\n  token: fixture-account-token\n', 'deepseek_account'),
                 ({}, '', 'deepseek-account-platform/fixture:\n  token: PASTE_TOKEN\n', 'unconfigured'),
                 ({}, 'DEEPSEEK_API_KEY= # no key\n', 'deepseek-account-platform/fixture: # no token\n', 'unconfigured'))
        for env, dotenv, yaml, expected in cases:
            (self.root / '.env').write_text(dotenv, encoding='utf-8')
            (self.root / '.credentials.yaml').write_text(yaml, encoding='utf-8')
            with self.subTest(source=expected, yaml=yaml), patch.dict(os.environ, env, clear=True), \
                 patch.object(harness, '_dsh_home', return_value=(self.root, False)), \
                 patch.object(harness, 'run_process') as run:
                info = harness._dsh_info('dsh.cmd')
                self.assertEqual(info['llm_source'], expected)
                self.assertEqual(info['llm_configured'], expected != 'unconfigured')
                if expected == 'unconfigured':
                    with self.assertRaisesRegex(harness.HarnessError, '未检测到已登录'):
                        harness._verify_dsh_llm_ready(info)
                else:
                    harness._verify_dsh_llm_ready(info)
                run.assert_not_called()
                self.assertNotIn('fixture-account-token', json.dumps(info))
                self.assertNotIn('fixture-yaml-token', json.dumps(info))

    def test_dsh_models_efforts_and_lightweight_selection(self):
        engines = self.four_backends()
        efforts = {'ultra': 'max', 'max': 'max', 'xhigh': 'max', 'high': 'high', 'medium': 'low',
                   'low': 'low', 'minimal': 'low', 'off': 'off', 'none': 'off'}
        for tier, effort in efforts.items():
            with self.subTest(tier=tier):
                profile = harness.resolve_execution_profile(engines, 'dsh:default', tier)
                self.assertEqual(profile['cli_effort'], effort)
                self.assertEqual(profile['model'], 'deepseek-flash' if tier in ('low', 'minimal') else 'deepseek-v4-pro')
                self.assertEqual(profile['capacity']['model_declared_max_window_tokens'], 1_048_576)
                self.assertEqual(profile['model_category'], 'native')
                self.assertEqual(profile['catalog_usage'], 'builtin_contract')
        for query, expected in (('flash', 'deepseek-flash'), ('deepseek', 'deepseek-v4-pro'),
                                ('deepseek-v4-pro', 'deepseek-v4-pro'), ('deepseek-flash', 'deepseek-flash')):
            self.assertEqual(harness.resolve_execution_profile(engines, 'dsh:' + query)['model'], expected)
        for prefix, provider in (('claude', 'claude_configured'), ('claude', 'anthropic'), ('dsh', 'deepseek-account'), ('dsh', 'deepseek-official')):
            self.assertEqual(harness._model_category(prefix, {'slug': 'fixture-model', 'provider': provider}), 'native')
        self.assertTrue(all(row['effort_source'] == 'dsh_builtin_model_contract'
                            for row in harness._capability_matrix('dsh', engines['dsh'])))

    def test_claude_and_dsh_preflight_blocks_before_launch_and_dry_run_stays_offline(self):
        engines = self.four_backends()
        engines['claude'].update(llm_configured=False, llm_source='placeholder_token')
        engines['dsh'].update(llm_configured=False, llm_source='unconfigured')
        for spec in ('claude:default', 'dsh:default'):
            with self.subTest(spec=spec), patch.object(harness, 'run_process') as run:
                harness._prepare_execution(engines, (spec,), harness._QuotaBatch(), dry_run=True)
                with self.assertRaises(harness.HarnessError) as error:
                    harness._prepare_execution(engines, (spec,), harness._QuotaBatch())
                self.assertEqual(error.exception.details['requested_engine'], spec)
                self.assertIn('execution_environment', error.exception.details)
                run.assert_not_called()

    def test_dsh_cmd_runtime_patch_permissions_stdin_and_untouched_global_files(self):
        engines = self.four_backends()
        root = Path(engines['dsh']['config_home'])
        root.mkdir()
        cordis = root / 'cordis.patch.yml'
        cordis.write_text('# user patch\n', encoding='utf-8')
        credentials = root / '.credentials.yaml'
        credentials.write_text('deepseek-official: fixture-token\n', encoding='utf-8')
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (cordis, credentials)}
        for provider in ('deepseek-official', 'deepseek-account'):
            engines['dsh']['dsh_provider'] = provider
            for supported in (False, True):
                for readonly in (True, False):
                    def run(command, cwd, input_text, timeout_sec, **kwargs):
                        self.assertEqual(cwd, self.root)
                        self.assertEqual(command[:3], ['dsh.cmd', '--profile', 'headless'])
                        self.assertEqual(command[-1], '-')
                        self.assertIn('--json', command)
                        self.assertNotIn('--json-schema', command)
                        if supported:
                            self.assertEqual(command[command.index('--permission-mode') + 1], 'read-only' if readonly else 'workspace-write')
                            self.assertEqual(command[command.index('--approval-policy') + 1], 'never')
                        else:
                            self.assertNotIn('--permission-mode', command)
                            self.assertNotIn('--approval-policy', command)
                            self.assertEqual(kwargs['env']['DSH_PERMISSION_MODE'], 'read-only' if readonly else 'workspace-write')
                        runtime_patch = Path(command[command.index('--patch') + 1]).read_text(encoding='utf-8')
                        for plugin in ('sandbox-policy', 'approval', 'permission'):
                            self.assertEqual('- id: ' + plugin + '\n' in runtime_patch, not supported)
                        for plugin in ('credentials', 'authorization', 'deepseek-account'):
                            self.assertIn(f'- id: {plugin}\n  disabled: false', runtime_patch)
                        self.assertIn(f'provider: {provider}', runtime_patch)
                        self.assertIn('model: "deepseek-v4-pro"', runtime_patch)
                        self.assertIn('reasoningEffort: low', runtime_patch)
                        self.assertTrue(input_text.startswith('执行输入\n\n'))
                        self.assertIn(json.dumps(harness.TASK_JSON_SCHEMA, ensure_ascii=False), input_text)
                        self.assertEqual(kwargs['env']['DSH_HOME'], str(root))
                        self.assertEqual(kwargs['env']['DSH_TELEMETRY_DISABLED'], '1')
                        stream = json.dumps({'type': 'init'}) + '\n' + json.dumps({'type': 'final',
                            'reason': {'kind': 'completed'}, 'text': json.dumps(TASK)})
                        return subprocess.CompletedProcess(command, 0, stream, '')
                    with self.subTest(provider=provider, readonly=readonly, supported=supported), patch.object(harness, 'run_process', side_effect=run), \
                         patch.object(harness, '_dsh_supports_cli_permission_flags', return_value=supported):
                        result = harness.invoke_engine('dsh:default', self.root, '执行输入', engines, harness.TASK_JSON_SCHEMA,
                                                       readonly=readonly, effort='medium')
                    self.assertEqual(result, TASK)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}, before)

    def test_dsh_final_ndjson_fenced_text_and_incomplete_states(self):
        final = {'type': 'final', 'reason': {'kind': 'completed'}, 'text': ' \n```json\n' + json.dumps(TASK) + '\n```\n '}
        for raw in (json.dumps(final), json.dumps({'type': 'init'}) + '\n' + json.dumps(final),
                    'output:\n```json\n' + json.dumps(TASK) + '\n```\n'):
            self.assertEqual(harness._extract_json_object(raw, harness.TASK_JSON_SCHEMA), TASK)
        for reason in (None, 'completed', {}, {'kind': 'cancelled'}, {'kind': 'failed'}):
            for leading in ('', '{"type":"init"}\n'):
                with self.subTest(reason=reason), self.assertRaisesRegex(harness.HarnessError, '未完成状态'):
                    harness._extract_json_object(leading + json.dumps({**final, 'reason': reason}), harness.TASK_JSON_SCHEMA)

    def test_claude_dsh_discovery_home_precedence_and_interop_batch_environment(self):
        native = self.root / '.local/bin/claude.exe'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'fixture')
        dsh = self.root / 'AppData/Local/Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd'
        dsh.parent.mkdir(parents=True)
        dsh.write_bytes(b'fixture')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'AppData/Local')}, clear=True), \
             patch.object(harness, '_is_posix', return_value=False), patch.object(harness.shutil, 'which', side_effect=lambda name: 'claude.cmd' if name == 'claude' else None):
            self.assertEqual(harness._find_claude(), str(native))
            self.assertEqual(harness._find_dsh(), str(dsh))
        with patch.dict(os.environ, {'CAE_CLAUDE_HOME': str(self.root / 'override-cl'), 'CLAUDE_CONFIG_DIR': str(self.root / 'native-cl'),
                                     'CAE_DSH_HOME': str(self.root / 'override-ds'), 'DSH_HOME': str(self.root / 'native-ds')}, clear=True), \
             patch.object(harness, '_is_posix', return_value=True):
            self.assertEqual(harness._claude_home('claude.exe'), (self.root / 'override-cl', True))
            self.assertEqual(harness._dsh_home('dsh.cmd'), (self.root / 'override-ds', True))
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': 'ignored-cl', 'DSH_HOME': 'ignored-ds'}, clear=True), \
             patch.object(harness, '_is_posix', return_value=True), patch.object(harness, '_windows_env', return_value='windows-home'), \
             patch.object(harness, '_wsl_path', return_value=str(self.root)), patch.object(harness.shutil, 'which', return_value='wslpath'):
            self.assertEqual(harness._claude_home('claude.exe'), (self.root / '.claude', True))
            self.assertEqual(harness._dsh_home('dsh.cmd'), (self.root / '.dsh', True))
            self.assertEqual(harness._find_windows_dsh_in_wsl(), str(dsh))
        with patch.object(harness, '_wsl_path', side_effect=lambda path: 'Z:\\fixture\\' + Path(path).name):
            command, _pid = harness._interop_command([str(dsh), '--json', '-'], self.root, self.root, '输入',
                extra_env={'DSH_HOME': 'Z:\\fixture\\.dsh', 'DSH_TELEMETRY_DISABLED': '1'})
        script = base64.b64decode(command[-1]).decode('utf-16le')
        self.assertIn("$info.FileName = 'cmd.exe'", script)
        self.assertIn('/d /s /c "', script)
        self.assertIn("$info.EnvironmentVariables['DSH_HOME'] = 'Z:\\fixture\\.dsh'", script)
        self.assertIn("$info.EnvironmentVariables['DSH_TELEMETRY_DISABLED'] = '1'", script)

    def test_quota_fallback_skips_cooled_peers_and_reaches_claude_then_dsh(self):
        engines = self.four_backends()
        workspace = self.root / 'workspace'
        workspace.mkdir()
        target = workspace / 'a.py'
        target.write_text('value = 1\n', encoding='utf-8')
        for name in ('codex', 'agy'):
            harness._record_quota_exhausted(name, engines[name], 'insufficient balance')
        args = self.args('task', '--caller', 'antigravity', '--kind', 'review', '--target', str(target),
                         '--task', '检查', '--engine', 'codex:default', '--fallback-on-quota')
        for expected in ('claude:default', 'dsh:default'):
            with self.subTest(expected=expected), patch.object(harness, '_args_backends', return_value=engines), \
                 patch.object(harness, 'invoke_engine', return_value=TASK) as invoke:
                code, report = self.capture(harness.cmd_task, args)
            self.assertEqual(code, 0)
            self.assertEqual(report['engine'], expected)
            self.assertEqual(report['quota_fallback']['to_engine'], expected)
            self.assertEqual(invoke.call_count, 1)
            self.assertEqual(harness._engine_parts(invoke.call_args.args[0])[0], expected.split(':')[0])
            harness._record_quota_exhausted(expected.split(':')[0], engines[expected.split(':')[0]], '余额不足')
        self.assertIsNone(harness._local_fallback_engine('antigravity', 'codex:default', engines))
        for message in ('credit balance is too low', 'insufficient_balance', 'insufficient-balance', '账户余额不足'):
            self.assertTrue(harness._is_quota_exhausted(message))

    def test_backend_absolute_paths_match_planned_relative_files(self):
        source = self.root / 'input.py'
        source.write_text('value = 1\n', encoding='utf-8')
        with patch.object(harness.shutil, 'which', return_value='wslpath'), patch.object(harness, '_is_posix', return_value=True):
            with patch.object(harness, '_wsl_path', return_value=str(source)):
                self.assertEqual(harness._normalize_rel_file('X:/task/input.py', self.root), 'input.py')
            with patch.object(harness, '_wsl_path', side_effect=harness.HarnessError('unmounted')):
                self.assertEqual(harness._normalize_rel_file('Q:/unmounted/input.py', self.root), 'Q:/unmounted/input.py')
        self.assertEqual(harness._normalize_rel_file(str(source), self.root), 'input.py')
        self.assertEqual(harness._normalize_rel_file('../outside.py', self.root), '../outside.py')

    def test_caller_symmetry_and_explicit_only_backends(self):
        engines = backends()
        engines['external'] = {'available': True}
        engines['sdk'] = {'available': True}
        self.assertEqual(harness.choose_engine(engines, 'codex'), 'agy:default')
        self.assertEqual(harness.choose_engine(engines, 'antigravity'), 'codex:default')
        for name in ('external:x', 'sdk:x'):
            with self.assertRaises(harness.HarnessError):
                harness.choose_engine(engines, 'codex', name)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(harness.resolve_caller('auto'), 'caller')
        with self.assertRaises(harness.HarnessError):
            harness.choose_engine(engines, 'caller')

    def test_probe_metadata_is_not_verified_capacity(self):
        (self.root / 'config.toml').write_text('model = "chosen"\nmodel_context_window = 999999\n', encoding='utf-8')
        (self.root / 'models_cache.json').write_text(json.dumps({'models': [{'slug': 'chosen', 'context_window': 500000,
            'max_context_window': 872000, 'effective_context_window_percent': 95}]}), encoding='utf-8')
        with patch.object(harness, '_find_binary', return_value='fake'), patch.object(harness, '_codex_home', return_value=(self.root, False)), \
             patch.dict(os.environ, {'CAE_AGY_HOME': str(self.root)}, clear=True), patch.object(harness, 'run_process') as runner:
            result = harness.probe_backends()
        runner.assert_not_called()
        self.assertEqual(set(result), {'codex', 'agy', 'claude', 'dsh'})
        self.assertEqual(result['codex']['inferred_window_tokens'], 872000)
        self.assertIsNone(result['codex']['effective_compaction_tokens'])
        self.assertEqual(result['codex']['verified_status'], 'unverified')
        self.assertIsNone(result['agy']['configured_window_tokens'])
        self.assertNotIn('supports_true_1m', result['codex'])

    def test_top_level_toml_handles_quoted_keys_numbers_and_comments(self):
        path = self.root / 'config.toml'
        path.write_text(
            'arbitrary = [\n  "value", # ]\n]\n'
            'text = """\nmodel_context_window = 17\n[ignored]\n"""\n'
            '"model" = "gpt-9-fixture#literal" # comment\n'
            "'model_context_window' = 500_000 # not 500\n"
            '"model_auto_compact_token_limit" = 0x6_DD_D0 # 450000\n'
            'model_reasoning_effort = "h\\u0069gh"\n'
            '[profiles.fixture]\nmodel_context_window = 7\n',
            encoding='utf-8-sig')
        expected = {'model': 'gpt-9-fixture#literal', 'model_context_window': 500000,
                    'model_auto_compact_token_limit': 450000, 'model_reasoning_effort': 'high'}
        self.assertEqual(harness._top_level_toml(path), expected)
        path.write_text("model_context_window = 500_000x\n'other' = 300\n", encoding='utf-8')
        self.assertEqual(harness._top_level_toml(path), {})

    def test_probe_context_metadata_belongs_to_selected_model(self):
        cache = {'models': [{'slug': 'catalog-first', 'priority': 1, 'context_window': 500000},
                            {'slug': 'user-choice', 'priority': 2, 'context_window': 200000}]}
        (self.root / 'models_cache.json').write_text(json.dumps(cache), encoding='utf-8')
        with patch.object(harness, '_find_binary', return_value='fake'), \
             patch.object(harness, '_codex_home', return_value=(self.root, False)), \
             patch.dict(os.environ, {'CAE_AGY_HOME': str(self.root)}, clear=True):
            for slug, expected in (('user-choice', 200000), ('unknown-choice', None)):
                (self.root / 'config.toml').write_text(f'model = "{slug}"\n', encoding='utf-8')
                result = harness.probe_backends()['codex']
                self.assertEqual(result['cached_default_window_tokens'], expected)
                self.assertEqual(result['context_metadata_model'], slug if expected else None)

    def test_assess_reads_catalogs_and_preserves_frontend_and_cache(self):
        codex_root, agy_root = self.root / 'codex', self.root / 'agy'
        codex_root.mkdir()
        cli = agy_root / 'antigravity-cli'
        (cli / 'cache').mkdir(parents=True)
        (agy_root / 'config').mkdir()
        (codex_root / 'cache').mkdir()
        files = {
            codex_root / 'config.toml': 'model = "interactive-choice"\nmodel_reasoning_effort = "ultra"\n',
            codex_root / 'models_cache.json': json.dumps({'fetched_at': '2020-01-01T08:00:00+08:00', 'models': [
                {'slug': 'code-model', 'provider': 'openai', 'priority': 1, 'context_window': 500000, 'supported_efforts': ['high', 'xhigh', 'ultra']},
                {'slug': 'math-model', 'provider': 'openai', 'priority': 2, 'description': 'Frontier intelligence for the most demanding work.',
                 'supported_efforts': ['high', 'max', 'ultra']},
                {'slug': 'fast-model', 'provider': 'openai', 'priority': 3, 'description': 'Lightweight model', 'supported_efforts': ['low']},
            ]}),
            codex_root / 'cache' / 'cae_quota_state.json': '{"exhausted": true, "cooldown_until_epoch": 1}',
            cli / 'settings.json': '{"model": "Interactive CLI Choice"}',
            agy_root / 'config' / 'config.json': '{"userSettings": {"model": "Interactive Desktop Choice"}}',
            cli / 'cache' / 'cae_models_cache.json': '{"models": [{"slug": "vendor-fast-low"}, {"slug": "vendor-core-high"}]}',
        }
        for path, contents in files.items():
            path.write_text(contents, encoding='utf-8')
        with patch.dict(os.environ, {'CAE_CODEX_HOME': str(codex_root), 'CAE_AGY_HOME': str(agy_root)}, clear=True), \
             patch.object(harness, '_find_binary', side_effect=['native-codex', 'native-agy', None, None]), \
             patch.object(harness, 'run_process') as runner, patch.object(harness, 'invoke_engine') as invoke:
            code, report = self.capture(harness.cmd_assess, self.args('assess', '--caller', 'codex'))
        runner.assert_not_called()
        invoke.assert_not_called()
        self.assertEqual(code, 0)
        self.assertFalse(report['runtime_verified'])
        self.assertFalse(report['controls_native_spawn_agent'])
        codex, agy = (report['backends'][name] for name in ('codex', 'agy'))
        self.assertEqual((codex['caller_relation'], agy['caller_relation']), ('local', 'counterpart'))
        self.assertEqual(codex['model'], 'interactive-choice')
        self.assertEqual(agy['configured_model_source'], str(cli / 'settings.json'))
        self.assertEqual(agy['config_files']['desktop']['path'], str(agy_root / 'config' / 'config.json'))
        self.assertEqual(codex['model_catalog']['fetched_at'], '2020-01-01T00:00:00+00:00')
        self.assertEqual(codex['model_catalog']['freshness'], 'stale')
        self.assertEqual(agy['model_catalog']['freshness'], 'unknown')
        self.assertIsNone(agy['model_catalog']['age_seconds'])
        math = report['recommendations']['codex']['math']
        self.assertEqual((math['model'], math['reasoning_effort'], math['tier_reason'], math['profile_source'], math['effort_source']),
                         ('math-model', 'max', 'task_specialty_math_max', 'specialty_deep_reasoning', 'catalog_declared'))
        surface = report['recommendations']['codex']['surface_review']
        self.assertEqual((surface['model'], surface['selected_tier'], surface['reasoning_effort']), ('code-model', 'max', 'xhigh'))
        eco_review = report['recommendations']['codex']['economy_review']
        self.assertEqual((eco_review['model'], eco_review['reasoning_effort']), ('code-model', 'high'))
        eco_surface = report['recommendations']['codex']['economy_surface_review']
        self.assertEqual((eco_surface['model'], eco_surface['reasoning_effort']), ('fast-model', 'low'))
        matrix = {row['model']: row for row in codex['capability_matrix']}
        self.assertEqual(matrix['code-model']['capacity']['model_default_window_tokens'], 500000)
        self.assertIsNone(matrix['code-model']['capacity']['model_declared_max_window_tokens'])
        self.assertEqual(matrix['math-model']['declared_efforts'], ['high', 'max', 'ultra'])
        self.assertEqual(matrix['math-model']['catalog_freshness'], 'stale')
        self.assertEqual({row['category'] for row in agy['capability_matrix']}, {'unknown'})
        self.assertEqual(set(path for path in self.root.rglob('*') if path.is_file()), set(files))
        for path, contents in files.items():
            self.assertEqual(path.read_text(encoding='utf-8'), contents)

    def test_catalog_freshness_uses_collection_time_only(self):
        cache = self.root / 'cache.json'
        now = datetime.datetime(2026, 1, 8, tzinfo=datetime.timezone.utc)
        self.assertEqual(harness._catalog_metadata(cache, {}, [], 'cache', now)['freshness'], 'missing')
        cache.write_text('{}', encoding='utf-8')
        self.assertEqual(harness._catalog_metadata(cache, {}, [], 'cache', now)['freshness'], 'empty_or_unreadable')
        for stamp, expected, age in (
            (None, 'unknown', None), ('2026-01-08T00:00:00', 'unknown', None),
            ('2026-01-08T00:00:01Z', 'future_timestamp', None), ('2026-01-08T00:00:00Z', 'within_assessment_window', 0),
            ('2026-01-07T23:59:59.509331500Z', 'within_assessment_window', 0),
            ('2026-01-01T00:00:00Z', 'stale', harness.CATALOG_MAX_AGE_SECONDS),
        ):
            with self.subTest(stamp=stamp):
                catalog = harness._catalog_metadata(cache, {'fetched_at': stamp}, [{}], 'cache', now)
                self.assertEqual((catalog['freshness'], catalog['age_seconds']), (expected, age))

    def test_codex_stdio_lists_models_after_initialization_without_turn(self):
        fake = self.root / 'model_list_fixture.py'
        fake.write_text("""
import json, sys

def read():
    return json.loads(sys.stdin.readline())

def reply(request, result):
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)

initialize = read()
assert initialize['method'] == 'initialize'
assert initialize['params']['capabilities']['experimentalApi'] is True
reply(initialize, {})
assert read() == {'method': 'initialized'}
request = read()
assert request['method'] == 'model/list'
assert request['params'] == {'includeHidden': False, 'limit': 100}
reply(request, {'data': [{'model': 'gpt-9-alpha'}], 'nextCursor': 'page-2'})
request = read()
assert request['method'] == 'model/list'
assert request['params']['cursor'] == 'page-2'
reply(request, {'data': [{'model': 'gpt-9-beta'}], 'nextCursor': None})
assert sys.stdin.readline() == ''
""", encoding='utf-8')
        listing = harness._query_codex_stdio([sys.executable, '-B', str(fake)], self.root, None, None, 10)
        self.assertEqual(set(listing), {'gpt-9-alpha', 'gpt-9-beta'})

    def test_codex_refresh_joins_current_catalog_and_exact_cache_slug(self):
        engines = backends()
        info = engines['codex']
        info.update(config_home=str(self.root), models=[
            {'slug': 'gpt-9-obsolete', 'context_window': 900000, 'max_context_window': 950000},
        ], model_catalog={'freshness': 'stale'})
        cache = self.root / 'models_cache.json'

        def refresh(actual_info):
            self.assertEqual(actual_info['config_home'], str(self.root))
            cache.write_text(json.dumps({'fetched_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'models': [
                {'slug': 'gpt-9-current', 'context_window': 300000, 'max_context_window': 500000},
                {'slug': 'gpt-9-obsolete', 'context_window': 900000, 'max_context_window': 950000},
            ]}), encoding='utf-8')
            return {'gpt-9-current': {'model': 'gpt-9-current', 'supportedReasoningEfforts': [{'effort': 'xhigh'}]},
                    'gpt-9-unmatched': {'model': 'gpt-9-unmatched', 'max_context_window': 999999}}

        with patch.object(harness, '_query_codex_listing', side_effect=refresh) as query:
            harness._prepare_execution(engines, ('codex:default',), harness._QuotaBatch())
            harness._prepare_execution(engines, ('codex:default',), harness._QuotaBatch())
        query.assert_called_once()
        chosen = harness.resolve_execution_profile(engines, 'codex:default', 'max', estimated_tokens=170000)
        self.assertEqual(chosen['model'], 'gpt-9-current')
        self.assertEqual(chosen['capacity']['cli_context_window_tokens'], 500000)
        self.assertEqual(chosen['declared_effort'], 'xhigh')
        self.assertEqual(chosen['model_listing']['source'], 'codex_app_server_model_list')
        self.assertIsNone(harness.resolve_execution_profile(
            engines, 'codex:gpt-9-unmatched')['capacity']['cli_context_window_tokens'])
        self.assertIsNone(harness.resolve_execution_profile(
            engines, 'codex:gpt-9-obsolete')['capacity']['cli_context_window_tokens'])

    def test_codex_stale_after_refresh_blocks_exec_and_quota_precedes_listing(self):
        engines = backends()
        info = engines['codex']
        config = self.root / 'config'
        config.mkdir()
        info.update(config_home=str(config), model_catalog={'freshness': 'future_timestamp'})
        cache = config / 'models_cache.json'
        cache.write_text(json.dumps({'fetched_at': '2020-01-01T00:00:00Z', 'models': info['models']}), encoding='utf-8')
        args = self.args('task', '--target', str(self.root), '--kind', 'review', '--task', '核对', '--engine', 'codex:default')
        models = {'gpt-9-current': {'model': 'gpt-9-current'}}
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_query_codex_listing', return_value=models) as query, \
             patch.object(harness, 'run_process') as worker:
            code, report = self.capture(harness.cmd_task, args)
            self.assertEqual(code, 1)
            self.assertEqual(report['model_catalog']['freshness'], 'stale')
            self.assertEqual(report['requested_engine'], 'codex:default')
            query.assert_called_once_with(info)
            query.reset_mock()
            harness._record_quota_exhausted('codex', info, 'hit your usage limit')
            code, report = self.capture(harness.cmd_task, args)
            self.assertEqual(code, 5)
            self.assertTrue(report['preflight_blocked'])
            query.assert_not_called()
            worker.assert_not_called()

    def test_assess_discovery_uses_cli_listing_without_refreshing_cache(self):
        cache = self.root / '.gemini' / 'antigravity-cli' / 'cache' / 'cae_models_cache.json'
        cache.parent.mkdir(parents=True)
        cache.write_text('{"models": [{"slug": "cached-high"}]}', encoding='utf-8')
        response = subprocess.CompletedProcess(['agy', 'models'], 0, 'vendor-core-xhigh\tVendor Core (XHigh)\n', '')
        with patch.dict(os.environ, {}, clear=True), patch.object(harness.Path, 'home', return_value=self.root), \
             patch.object(harness, '_find_binary', side_effect=[None, 'agy', None, None]), \
             patch.object(harness, 'run_process', return_value=response) as runner:
            code, report = self.capture(harness.cmd_assess, self.args('assess', '--discover-models'))
        self.assertEqual(code, 0)
        runner.assert_called_once_with(['agy', 'models'], Path.cwd(), timeout_sec=30, check=False)
        agy = report['backends']['agy']
        self.assertEqual(agy['verified_status'], 'model_listing_only')
        self.assertEqual(agy['model_catalog']['timestamp_source'], 'cli_query_time_utc')
        self.assertEqual(agy['cached_model_catalog']['freshness'], 'unknown')
        self.assertEqual(cache.read_text(encoding='utf-8'), '{"models": [{"slug": "cached-high"}]}')

    def test_non_delegating_profile_does_not_inherit_or_guess_ultra(self):
        engines = backends()
        engines['codex'].update(model='only-delegating', reasoning_effort='ultra', models=[
            {'slug': 'only-delegating', 'provider': 'openai', 'priority': 1, 'supported_efforts': ['ultra']},
            {'slug': 'eligible', 'provider': 'openai', 'priority': 2, 'supported_efforts': ['high']},
        ])
        engines['agy']['models'] = [{'slug': 'gemini-6-pro-ultra'}, {'slug': 'gemini-5-pro-high'}]
        self.assertEqual(harness.resolve_execution_profile(engines, 'codex:default', 'max')['model'], 'eligible')
        self.assertEqual(harness.resolve_execution_profile(engines, 'agy:default', 'max')['model'], 'gemini-5-pro-high')
        self.assertEqual(harness.resolve_execution_profile(engines, 'agy:default', 'ultra')['model'], 'gemini-6-pro-ultra')
        unverified = harness.resolve_execution_profile(engines, 'codex:unlisted', 'high')
        self.assertEqual((unverified['cli_effort'], unverified['effort_source']), ('high', 'unverified_request'))
        unverified = harness.resolve_execution_profile(engines, 'agy:unlisted-high', 'high')
        self.assertEqual(unverified['effort_source'], 'unverified_request')
        for name in engines:
            engines[name]['models'].pop()
            blocked = harness.resolve_execution_profile(engines, name + ':default', 'max')
            self.assertIsNone(blocked['cli_effort'])
            self.assertEqual(blocked['effort_source'], 'no_non_delegating_effort')
            with patch.object(harness, 'probe_backends', return_value=engines), patch.object(harness, 'invoke_engine') as invoke:
                with self.assertRaises(harness.HarnessError):
                    harness.cmd_task(self.args('task', '--engine', name + ':default', '--kind', 'math',
                                               '--target', str(self.root), '--task', '检查结果'))
                invoke.assert_not_called()
        with patch.dict(os.environ, {'CAE_DEFAULT_TIER': 'ultra'}, clear=True):
            self.assertEqual(harness.select_task_tier(text='不要使用 ultra，检查结果')[0], 'max')
        with patch.dict(os.environ, {'CAE_DEFAULT_TIER': 'high'}, clear=True):
            self.assertEqual(harness.select_task_tier(text='检查结果'), ('high', 'env_default_tier'))

    def test_explicit_model_has_own_budget_cap(self):
        engines = backends()
        engines['codex'].update(configured_window_tokens=900000, requested_input_budget_tokens=None, models=[
            {'slug': 'larger', 'context_window': 500000, 'max_context_window': 872000},
            {'slug': 'small', 'context_window': 100000, 'max_context_window': 128000},
            {'slug': 'default-only', 'context_window': 200000}])
        for model, expected_window in (('larger', 872000), ('small', 128000), ('default-only', 200000)):
            with self.subTest(model=model):
                profile = harness.resolve_execution_profile(engines, f'codex:{model}')
                self.assertEqual(profile['capacity']['cli_context_window_tokens'], expected_window)
                self.assertEqual(profile['capacity']['input_budget_tokens'], expected_window * 9 // 10)
        unknown = harness.resolve_execution_profile(engines, 'codex:unlisted')
        self.assertIsNone(unknown['capacity']['cli_context_window_tokens'])
        self.assertIsNotNone(unknown['execution_blocked_reason'])
        engines['codex'].pop('configured_window_tokens')
        unknown = harness.resolve_execution_profile(engines, 'codex:unlisted')
        self.assertIsNone(unknown['execution_blocked_reason'])
        self.assertEqual(unknown['capacity']['input_budget_tokens'], 120000)

    def test_catalog_effective_window_caps_budget_without_changing_cli_window(self):
        engines = backends()
        info = engines['codex']
        info.update(requested_input_budget_tokens=850000, models=[
            {'slug': 'gpt-9-fixture', 'context_window': 500000, 'max_context_window': 872000,
             'effective_context_window_percent': 95},
        ])

        def capacity():
            return harness.resolve_execution_profile(engines, 'codex:gpt-9-fixture')['capacity']

        explicit = capacity()
        self.assertEqual(explicit['cli_context_window_tokens'], 872000)
        self.assertEqual(explicit['catalog_effective_window_tokens'], 828400)
        self.assertEqual(explicit['effective_window_source'], 'catalog_percent_of_selected_window')
        self.assertEqual(explicit['input_budget_tokens'], 828400)
        self.assertFalse(explicit['capacity_verified'])

        info['requested_input_budget_tokens'] = None
        default = capacity()
        self.assertEqual(default['planning_cap_tokens'], 828400)
        self.assertEqual(default['input_budget_tokens'], 745560)
        self.assertEqual(default['policy_reserve_tokens'], 82840)

        info['effective_compaction_tokens'] = 500000
        compact = capacity()
        self.assertEqual(compact['catalog_effective_window_tokens'], 828400)
        self.assertEqual(compact['planning_cap_tokens'], 500000)
        self.assertEqual(compact['input_budget_tokens'], 450000)

        info.pop('effective_compaction_tokens')
        info['models'][0]['effective_context_window_percent'] = 101
        unknown = capacity()
        self.assertIsNone(unknown['catalog_effective_window_tokens'])
        self.assertEqual(unknown['planning_cap_tokens'], 872000)
        self.assertEqual(unknown['input_budget_tokens'], 784800)

    def test_plan_skips_oversize_with_explicit_coverage(self):
        records = [{'rel_path': 'small.lean', 'tokens': 20}, {'rel_path': 'large.f90', 'tokens': 2000}]
        plan = harness.select_audit_plan(2020, records, backends(1000), caller='codex', shard_tokens=100)
        self.assertEqual(plan['left_engine'], 'agy:default')
        self.assertEqual(plan['right_engine'], 'codex:default')
        self.assertEqual(len(plan['skipped_files']), 2)
        self.assertEqual(plan['left_units'][0]['files'], ['small.lean'])
        self.assertIn('跨分片', plan['coverage_notice'])
        with self.assertRaises(harness.HarnessError):
            harness.select_audit_plan(2020, records, backends(1000), strategy='single-pass', caller='codex')

    def test_whole_alias_and_unconfigured_preview(self):
        records = [{'rel_path': 'solver.py', 'tokens': 20}]
        plan = harness.select_audit_plan(20, records, backends(), strategy='whole-1m', caller='antigravity')
        self.assertEqual(plan['mode'], 'single-pass')
        self.assertIn('deprecation_notice', plan)
        plan = harness.select_audit_plan(20, records, {}, allow_unconfigured=True)
        self.assertEqual(plan['mode'], 'unconfigured')

    def test_large_and_research_files_registered(self):
        for name in ('proof.lean', 'paper.tex', 'source.inc'):
            (self.root / name).write_text('内容', encoding='utf-8')
        (self.root / 'large.f90').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        (self.root / 'empty.py').touch()
        skipped = []
        records = harness.collect_target_files(self.root, skipped)
        self.assertEqual(len(records), 4)
        self.assertEqual(skipped[0]['reason'], 'empty_file')

    def test_invalid_outputs_do_not_become_clean_audits(self):
        for raw in ('', 'request failed', '{}', '{"summary":"ok","findings":"none"}',
                    '{"status":"ERROR","structured_output":{"summary":"ok","findings":[]}}',
                    '{"status":"partial","structured_output":{"summary":"ok","findings":[]}}'):
            with self.subTest(raw=raw), self.assertRaises(harness.HarnessError):
                harness._extract_json_object(raw)
        with self.assertRaises(harness.OutputParseError):
            harness._extract_json_object(json.dumps({**TASK, 'content': ''}), harness.TASK_JSON_SCHEMA)

    def test_stream_result_and_schema_validation(self):
        raw = json.dumps({'event': 'init'}) + '\n' + json.dumps({'event': 'result', 'result': {'status': 'OK', 'structured_output': TASK}})
        self.assertEqual(harness._extract_json_object(raw, harness.TASK_JSON_SCHEMA), TASK)
        bad = {**AUDIT, 'findings': [{'file': 'a', 'line_start': True, 'line_end': 2, 'severity': 'high',
                                   'category': 'x', 'description': 'x', 'evidence': 'x'}]}
        with self.assertRaises(harness.OutputParseError):
            harness._extract_json_object(json.dumps(bad))

    def test_codex_model_forwarded_and_failure_with_output_rejected(self):
        def runner(command, cwd, input_text, timeout_sec, **kwargs):
            self.assertIn('-m', command)
            self.assertEqual(command[command.index('-m') + 1], 'chosen')
            Path(command[command.index('-o') + 1]).write_text(json.dumps(AUDIT), encoding='utf-8')
            return subprocess.CompletedProcess(command, 2, '', 'failure')
        with patch.object(harness, 'run_process', side_effect=runner), self.assertRaises(harness.HarnessError):
            harness.invoke_readonly_engine('codex:chosen', self.root, '任务', backends())

    def test_agency_long_prompt_uses_stdin_and_task_agent(self):
        prompt = '中文&|<>"%' * 8000
        def runner(command, cwd, input_text, timeout_sec, **kwargs):
            self.assertNotIn(prompt, command)
            self.assertEqual(json.loads(input_text)['message']['content'], prompt)
            self.assertEqual(command[command.index('--agent') + 1], 'readonly-collaborator')
            self.assertEqual(command[command.index('--print-timeout') + 1], '600s')
            self.assertNotIn('--dangerously-skip-permissions', command)
            self.assertEqual(kwargs['env']['CAE_HARNESS_DEPTH'], '1')
            stream = '\n'.join([
                json.dumps({'event': 'init', 'init': {'agent': 'readonly-collaborator'}}),
                json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'num_turns': 1, 'structured_output': TASK, 'response': json.dumps(TASK)}}),
            ])
            return subprocess.CompletedProcess(command, 0, stream, '')
        with patch.object(harness, 'run_process', side_effect=runner):
            self.assertEqual(harness.invoke_engine('agy:chosen', self.root, prompt, backends(), harness.TASK_JSON_SCHEMA), TASK)
        fallback = json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'structured_output': None, 'response': json.dumps(TASK)}})
        self.assertEqual(harness._extract_json_object(fallback, harness.TASK_JSON_SCHEMA), TASK)

    def test_agy_edit_mode_and_finished_reason_statuses(self):
        def edit_runner(command, cwd, input_text, timeout_sec, **kwargs):
            self.assertIn('--mode', command)
            self.assertEqual(command[command.index('--mode') + 1], 'accept-edits')
            self.assertIn('--dangerously-skip-permissions', command)
            self.assertEqual(command[command.index('--print-timeout') + 1], '45s')
            return subprocess.CompletedProcess(command, 0, json.dumps({'event': 'result', 'result': {'structured_output': TASK}}), '')
        with patch.object(harness, 'run_process', side_effect=edit_runner):
            out = harness.invoke_edit_worker('agy:chosen', self.root, '修改', backends(), timeout_sec=45)
        self.assertEqual(json.loads(out), TASK)
        for reason, expected_status in (('print_timeout', 'timeout'), ('interrupted', 'cancelled'), ('error', 'failed')):
            payload = json.dumps({'event': 'result', 'result': {'structured_output': TASK, 'finished_reason': reason}})
            with self.subTest(reason=reason), self.assertRaises(harness.HarnessError) as caught:
                harness._extract_json_object(payload, harness.TASK_JSON_SCHEMA)
            self.assertEqual(caught.exception.status, expected_status)

    def test_wsl_codex_desktop_discovery_and_agy_model_parsing(self):
        fake_local = self.root / 'AppData' / 'Local'
        bin_dir = fake_local / 'OpenAI' / 'Codex' / 'bin' / 'v1'
        bin_dir.mkdir(parents=True)
        fake_exe = bin_dir / 'codex.exe'
        fake_exe.write_bytes(b'MZ')
        def fake_which(cmd):
            return {'wslpath': '/usr/bin/wslpath', 'cmd.exe': '/mnt/c/Windows/System32/cmd.exe'}.get(cmd)
        with patch.dict(os.environ, {}, clear=True), patch.object(harness, '_is_posix', return_value=True), \
             patch.object(harness.Path, 'home', return_value=self.root / 'linux-home'), \
             patch.object(harness.shutil, 'which', side_effect=fake_which), \
             patch.object(harness, '_windows_env', return_value='X:\\Users\\test\\AppData\\Local'), \
             patch.object(harness, '_wsl_path', return_value=str(fake_local)):
            found = harness._find_binary('codex')
            self.assertEqual(found, str(fake_exe))
            home, interop = harness._codex_home(found)
            self.assertTrue(interop)
            self.assertEqual(home, self.root / '.codex')
        sample_models = 'Fetching available models...\nagy-flagship-high\tFlagship Model (High)\nthirdparty-pro-high\tThirdParty Pro (High)\n'
        with patch.dict(os.environ, {}, clear=True), patch.object(harness.Path, 'home', return_value=self.root), \
             patch.object(harness, '_find_binary', return_value='fake-agy'), \
             patch.object(harness, '_codex_home', return_value=(self.root, False)), \
             patch.object(harness, 'run_process', return_value=subprocess.CompletedProcess(['agy', 'models'], 0, sample_models, '')):
            discovered = harness.probe_backends(discover_models=True)['agy']
        self.assertTrue(discovered['model_list_parsed'])
        self.assertEqual(discovered['models'][0], {'slug': 'agy-flagship-high', 'display_name': 'Flagship Model (High)'})
        self.assertEqual(len(discovered['models']), 2)

    def test_gate_worker_failure_never_calls_passing_verifier(self):
        verify = Mock(return_value=(0, 'base passes'))
        def worker(prompt, number):
            raise RuntimeError('worker failed')
        result = harness.run_physics_gate_loop(self.root, '改代码', 'verify', worker, verify, max_rounds=2)
        verify.assert_not_called()
        self.assertFalse(result['gate_passed'])
        self.assertEqual(result['status'], 'failed')

    def test_gate_retries_failed_verification(self):
        verify = Mock(side_effect=[(1, 'residual too large'), (0, 'ok')])
        result = harness.run_physics_gate_loop(self.root, '改代码', 'verify', lambda p, n: 'done', verify)
        self.assertTrue(result['gate_passed'])
        self.assertEqual(result['rounds_used'], 2)
        self.assertTrue(result['handoff_to_caller'])

    def test_gate_snapshot_failures_stop_at_the_unverified_stage(self):
        phases = (
            ('preflight', [PermissionError('initial denied')], 0, 0),
            ('post_worker', [{}, PermissionError('worker denied')], 1, 0),
            ('post_verify', [{}, {}, PermissionError('verifier denied')], 1, 1),
            ('final', [{}, {}, {}, PermissionError('final denied')], 1, 1),
            ('pre_worker', [{}, {}, {}, PermissionError('retry denied')], 1, 1),
        )
        for phase, snapshots, worker_calls, verifier_calls in phases:
            with self.subTest(phase=phase):
                worker = Mock(return_value='worker receipt')
                verifier = Mock(return_value=(1 if phase == 'pre_worker' else 0, 'verifier receipt'))
                with patch.object(harness, 'snapshot_files', side_effect=snapshots) as snapshot:
                    result = harness.run_physics_gate_loop(self.root, '改代码', 'verify', worker, verifier, max_rounds=3)
                self.assertEqual(snapshot.call_count, len(snapshots))
                self.assertEqual(worker.call_count, worker_calls)
                self.assertEqual(verifier.call_count, verifier_calls)
                self.assertEqual(result['rounds_used'], worker_calls)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['gate_passed'])
                self.assertFalse(result['scope_verified'])
                self.assertEqual(result['scope_check'], 'unverified')
                self.assertEqual(result['scope_error_phase'], phase)
                self.assertIsNone(result['changed_files'])
                if worker_calls:
                    self.assertEqual(result['history'][0]['worker_summary'], 'worker receipt')
                if verifier_calls:
                    self.assertEqual(result['history'][0]['verify_output_tail'], 'verifier receipt')

    def test_gate_cli_retains_actual_worker_profile_on_post_worker_snapshot_failure(self):
        engines = backends()
        denied = self.root / 'balance.py'
        open_file = Path.open

        def unreadable(path, *args, **kwargs):
            if path == denied and args == ('rb',):
                raise PermissionError(13, 'denied after worker', str(path))
            return open_file(path, *args, **kwargs)

        def run(command, cwd, input_text=None, timeout_sec=600, **kwargs):
            self.assertFalse(kwargs.get('shell'))
            denied.write_text('balance = 0\n', encoding='utf-8')
            Path(command[command.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        args = self.args('task', '--engine', 'codex:default', '--target', str(self.root), '--kind', 'code',
                         '--task', '写出平衡量', '--verify-cmd', 'verify', '--write-scope', 'balance.py')
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'run_process', side_effect=run) as runner, \
             patch.object(Path, 'open', unreadable):
            code, report = self.capture(harness.cmd_task, args)
        self.assertEqual(code, 1)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(report['scope_error_phase'], 'post_worker')
        self.assertEqual(report['scope_error_path'], 'balance.py')
        self.assertFalse(report['scope_verified'])
        self.assertEqual(report['rounds_used'], 1)
        entry = report['history'][0]
        self.assertTrue(entry['invocation']['invocation_started'])
        self.assertEqual(entry['invocation']['returncode'], 0)
        self.assertEqual(entry['execution_profile']['model'], 'fixture-code')
        self.assertEqual(json.loads(entry['worker_summary']), TASK)
        self.assertIsNone(entry['verify_exit_code'])

    def test_snapshot_reports_unreadable_root_and_subdirectory(self):
        nested = self.root / 'unreadable'
        nested.mkdir()
        scandir = os.scandir
        for blocked, rel in ((self.root, '.'), (nested, 'unreadable')):
            with self.subTest(blocked=blocked):
                def deny(path):
                    if Path(path) == blocked:
                        raise PermissionError(13, 'scandir denied', str(path))
                    return scandir(path)

                with patch.object(harness.os, 'scandir', side_effect=deny), \
                     self.assertRaises(harness.HarnessError) as error:
                    harness.snapshot_files(self.root)
                self.assertEqual(error.exception.details['snapshot_path'], rel)

    def test_scope_violation_preserves_existing_dirty_files(self):
        existing = self.root / 'existing.txt'
        existing.write_text('user change', encoding='utf-8')
        def worker(prompt, number):
            (self.root / 'outside.txt').write_text('new', encoding='utf-8')
            return 'done'
        verify = Mock(return_value=(0, 'ok'))
        result = harness.run_physics_gate_loop(self.root, '改代码', 'verify', worker, verify, write_scope=['allowed.py'])
        self.assertEqual(result['status'], 'scope_violation')
        self.assertEqual(result['scope_violations'], ['outside.txt'])
        self.assertEqual(existing.read_text(encoding='utf-8'), 'user change')
        self.assertTrue((self.root / 'outside.txt').exists())
        verify.assert_not_called()

    def test_verification_build_outputs_are_recorded_separately(self):
        (self.root / 'src').mkdir()
        def worker(prompt, number):
            (self.root / 'src' / 'main.py').write_text('value=1', encoding='utf-8')
            return 'done'
        def verifier(command, root):
            (root / 'build').mkdir(exist_ok=True)
            (root / 'build' / 'result.txt').write_text('ok', encoding='utf-8')
            return 0, 'ok'
        result = harness.run_physics_gate_loop(self.root, '改代码', 'verify', worker, verifier, write_scope=['src/*.py'])
        self.assertTrue(result['gate_passed'])
        self.assertEqual(result['verification_changed_files'], ['build/result.txt'])
        self.assertEqual(result['worker_changed_files'], ['src/main.py'])
        def bad_worker(prompt, number):
            (self.root / 'build' / 'result.txt').write_text('fabricated', encoding='utf-8')
            return 'done'
        result = harness.run_physics_gate_loop(self.root, '改代码', 'verify', bad_worker, verifier, write_scope=['src/*.py'])
        self.assertEqual(result['status'], 'scope_violation')
        self.assertEqual(result['scope_violations'], ['build/result.txt'])

    def test_task_reports_changes_even_when_worker_times_out(self):
        args = self.args('task', '--target', str(self.root), '--kind', 'math', '--task', '推导', '--caller', 'codex')
        def worker(*args, **kwargs):
            (self.root / 'unexpected.txt').write_text('changed', encoding='utf-8')
            raise harness.HarnessError('timeout', 'timeout')
        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'invoke_engine', side_effect=worker):
            code, report = self.capture(harness.cmd_task, args)
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'scope_violation')
        self.assertEqual(report['changed_files'], ['unexpected.txt'])

    def test_audit_partial_and_all_failed(self):
        (self.root / 'a.py').write_text('x=1', encoding='utf-8')
        args = self.args('audit', '--target', str(self.root), '--caller', 'codex')
        def worker(engine, *args, **kwargs):
            if engine.startswith('codex'):
                raise harness.OutputParseError('empty')
            return AUDIT
        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'invoke_readonly_engine', side_effect=worker):
            code, report = self.capture(harness.cmd_audit, args)
        self.assertEqual(code, 3)
        self.assertEqual(report['completed_count'], 1)
        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'invoke_readonly_engine', side_effect=harness.OutputParseError('bad')):
            code, report = self.capture(harness.cmd_audit, args)
        self.assertEqual(code, 4)
        self.assertEqual(report['completed_count'], 0)

    def test_audit_partial_reading_cannot_report_full_completion(self):
        (self.root / 'a.py').write_text('x=1', encoding='utf-8')
        (self.root / 'b.py').write_text('x=2', encoding='utf-8')
        args = self.args('audit', '--target', str(self.root), '--caller', 'codex', '--single')
        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'invoke_readonly_engine', return_value=AUDIT):
            code, report = self.capture(harness.cmd_audit, args)
        self.assertEqual(code, 3)
        self.assertEqual(report['completed_count'], 0)
        unit = report['completed_units'][0]
        self.assertEqual(unit['unread_files'][0]['file'], 'b.py')
        self.assertFalse(unit['reading_coverage_verified'])

    def test_location_pair_preserves_both_findings(self):
        left = {'file': 'a.py', 'line_start': 2, 'line_end': 2, 'severity': 'high',
                'category': 'mass', 'description': 'left', 'evidence': 'left evidence'}
        right = {**left, 'line_start': 5, 'line_end': 5, 'category': 'energy', 'evidence': 'right evidence'}
        report = harness.merge_blind_findings([left], [right], 'codex:a', 'agy:b', self.root)
        pair = report['consensus_findings'][0]
        self.assertEqual(pair['right_finding']['category'], 'energy')
        self.assertEqual(pair['right_finding']['evidence'], 'right evidence')
        self.assertEqual(pair['match_basis'], 'location_overlap_only')

    def test_task_dry_run_reads_bom_and_creates_nothing(self):
        task_file = self.root / '任务 input.txt'
        task_file.write_text('请推导', encoding='utf-8-sig')
        args = self.args('task', '--target', str(self.root), '--kind', 'math', '--task-file', str(task_file), '--caller', 'antigravity', '--dry-run')
        before = harness.snapshot_files(self.root)
        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'invoke_engine') as worker:
            code, report = self.capture(harness.cmd_task, args)
        worker.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(report['engine'], 'codex:default')
        self.assertEqual(harness.snapshot_files(self.root), before)
        self.assertEqual(harness._task_text(args), '请推导')

    def test_local_process_utf8_and_timeout_tree(self):
        result = harness.run_process([sys.executable, '-B', '-c', 'import sys; print(sys.stdin.read())'], self.root, '中文', 3)
        self.assertEqual(result.stdout.strip(), '中文')
        marker = self.root / 'grandchild-survived'
        child = 'import time; from pathlib import Path; time.sleep(1.2); Path(' + repr(str(marker)) + ').touch()'
        parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-B","-c",' + repr(child) + ']); time.sleep(30)'
        with self.assertRaises(harness.HarnessError) as caught:
            harness.run_process([sys.executable, '-B', '-c', parent], self.root, timeout_sec=0.4)
        self.assertEqual(caught.exception.status, 'timeout')
        time.sleep(1.3)
        self.assertFalse(marker.exists())

    def test_cancelled_process_has_non_success_status(self):
        timer = threading.Timer(0.2, harness._CANCELLED.set)
        timer.start()
        try:
            with self.assertRaises(harness.HarnessError) as caught:
                harness.run_process([sys.executable, '-B', '-c', 'import time; time.sleep(30)'], self.root, timeout_sec=5)
            self.assertEqual(caught.exception.status, 'cancelled')
        finally:
            timer.cancel()

    def test_interop_supervisor_uses_real_pid_and_utf8(self):
        def convert(path, windows=True):
            return 'Z:\\literal path\\' + Path(path).name
        with patch.object(harness, '_wsl_path', side_effect=convert):
            command, pid_file = harness._interop_command(['codex.exe', 'exec', '-C', 'Z:\\dir'], self.root, self.root, '中文', str(self.root))
        script = harness.base64.b64decode(command[-1]).decode('utf-16le')
        self.assertIn('[string]$PID', script)
        self.assertLess(script.index('[string]$PID'), script.index('[void]$p.Start()'))
        self.assertIn('StandardInput.BaseStream.Write', script)
        self.assertIn('finally', script)
        self.assertEqual((self.root / 'input.txt').read_text(encoding='utf-8'), '中文')
        self.assertEqual(pid_file.name, 'native.pid')

    def test_large_window_still_allows_existing_single_pass(self):
        (self.root / 'config.toml').write_text('model = "large"\nmodel_context_window = 872000\n', encoding='utf-8')
        (self.root / 'models_cache.json').write_text(json.dumps({'models': [
            {'slug': 'large', 'provider': 'openai', 'context_window': 500000, 'max_context_window': 872000},
            {'slug': 'small', 'provider': 'openai', 'context_window': 100000, 'max_context_window': 100000}]}), encoding='utf-8')
        with patch.object(harness, '_find_binary', return_value='fake'), patch.object(harness, '_codex_home', return_value=(self.root, False)):
            engines = harness.probe_backends()
        self.assertEqual(engines['codex']['safe_input_budget_tokens'], 784800)
        records = [{'rel_path': 'solver.f90', 'tokens': 193000}]
        plan = harness.select_audit_plan(193000, records, engines, dual_blind=False, caller='antigravity')
        self.assertEqual(plan['mode'], 'single-pass')
        self.assertEqual(harness._budget_for_spec(engines, 'codex:small'), 90000)

    def test_audit_plans_and_calls_the_same_model_and_window(self):
        path = self.root / 'solver.f90'
        path.write_bytes(b'a' * 579000)  # 193k by the planning estimate
        (self.root / 'more.f90').write_bytes(b'b' * 822000)
        engines = backends()
        engines['codex'].update(model='interactive', configured_window_tokens=900000,
                                effective_compaction_tokens=450000, requested_input_budget_tokens=None,
                                model_catalog={**engines['codex']['model_catalog'], 'model_count': 2},
                                models=[
                                    {'slug': 'interactive', 'context_window': 100000, 'max_context_window': 110000},
                                    {'slug': 'gpt-9-code', 'context_window': 272000, 'max_context_window': 500000,
                                     'supported_efforts': ['high', 'max']},
                                ])
        commands = []

        def runner(command, cwd, input_text, timeout_sec, **kwargs):
            self.assertIn('model_context_window=500000', command)
            self.assertIn('model_reasoning_effort="max"', command)
            self.assertEqual(command[command.index('-m') + 1], 'gpt-9-code')
            commands.append(command)
            name = 'solver.f90' if '- solver.f90\n' in input_text else 'more.f90'
            output = {**AUDIT, 'read_files': [name]}
            Path(command[command.index('-o') + 1]).write_text(json.dumps(output), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        args = self.args('audit', '--target', str(self.root), '--single', '--left', 'codex:default', '--focus', '检查格式转换')
        with patch.object(harness, '_args_backends', return_value=engines), patch.object(harness, 'run_process', side_effect=runner):
            code, report = self.capture(harness.cmd_audit, args)
        self.assertEqual(code, 0)
        self.assertEqual(len(commands), 2)
        self.assertEqual(report['plan']['mode'], 'sharded')
        self.assertEqual(report['plan']['skipped_files'], [])
        left = report['plan']['execution_profiles']['left']
        self.assertEqual(left['model'], 'gpt-9-code')
        self.assertEqual(left['catalog_usage'], 'unverified_client_cache')
        self.assertEqual(left['capacity']['selected_window_tokens'], 500000)
        self.assertEqual(left['capacity']['input_budget_tokens'], 405000)
        self.assertTrue(all(unit['exceeds_soft_shard_target'] for unit in report['plan']['left_units']))
        self.assertTrue(all(unit['estimated_prompt_tokens'] > 0 for unit in report['plan']['left_units']))
        self.assertFalse(report['reading_coverage_verified'])

    def test_audit_focus_consumes_budget_and_out_of_scope_findings_are_rejected(self):
        records = [{'rel_path': 'a.py', 'tokens': 20}]
        args = {'dual_blind': False, 'caller': 'antigravity'}
        self.assertEqual(harness.select_audit_plan(20, records, backends(1000), **args)['mode'], 'single-pass')
        plan = harness.select_audit_plan(20, records, backends(1000), focus='x' * 2400, **args)
        self.assertEqual(plan['left_units'], [])
        self.assertEqual(plan['skipped_files'][0]['reason'], 'over_input_budget')
        (self.root / 'a.py').write_text('x=1', encoding='utf-8')
        result = {**AUDIT, 'findings': [{'file': '../other.py', 'line_start': 1, 'line_end': 1,
                                         'severity': 'high', 'category': 'test', 'description': 'outside', 'evidence': 'outside'}]}
        cli_args = self.args('audit', '--target', str(self.root), '--single', '--left', 'codex:default')
        with patch.object(harness, '_args_backends', return_value=backends()), \
             patch.object(harness, 'invoke_readonly_engine', return_value=result):
            code, report = self.capture(harness.cmd_audit, cli_args)
        self.assertEqual(code, 4)
        self.assertEqual(report['received_count'], 0)
        self.assertEqual(report['single_source_count'], 0)

    def test_file_target_is_retained_in_prompt_and_receipt(self):
        file = self.root / 'specific.lean'
        file.write_text('theorem', encoding='utf-8')
        args = self.args('task', '--kind', 'math', '--target', str(file), '--task', '核对', '--caller', 'codex')
        def worker(engine, root, prompt, *args, **kwargs):
            self.assertIn(str(file), prompt)
            return TASK
        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'invoke_engine', side_effect=worker):
            code, report = self.capture(harness.cmd_task, args)
        self.assertEqual(code, 0)
        self.assertEqual(report['requested_target'], str(file))

    def test_directory_symlink_change_is_detected(self):
        target = self.root / 'dir'
        target.mkdir()
        before = harness.snapshot_files(self.root)
        link = self.root / 'link'
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            env = os.environ.copy()
            env['CAE_TEST_LINK'] = str(link)
            env['CAE_TEST_TARGET'] = str(target)
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                "$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path $env:CAE_TEST_LINK -Target $env:CAE_TEST_TARGET | Out-Null"],
                env=env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(harness.changed_files(before, harness.snapshot_files(self.root)), ['link'])
        (target / 'file.py').write_text('x=1', encoding='utf-8')
        skipped = []
        records = harness.collect_target_files(self.root, skipped)
        self.assertEqual([item['rel_path'] for item in records], ['dir/file.py'])
        self.assertEqual(skipped[0]['reason'], 'directory_link')

    def test_wsl_ext4_unc_staging_and_write_guard(self):
        nested = self.root / 'src'
        nested.mkdir()
        (nested / 'flux.f90').write_text('u = u + fl\n', encoding='utf-8')
        win_temp = self.root / 'win-temp'
        win_temp.mkdir()
        engines = backends()
        engines['codex']['binary'] = '/mnt/c/Codex/codex.exe'
        engines['codex']['windows_interop'] = True
        engines['codex']['config_home'] = str(self.root / '.codex')
        def fake_which(cmd):
            return {'powershell.exe': '/mnt/c/ps.exe', 'taskkill.exe': '/mnt/c/tk.exe', 'wslpath': '/usr/bin/wslpath'}.get(cmd)
        def fake_wsl_path(path, windows=True):
            p = Path(path)
            if not windows:
                return str(win_temp)
            if str(p).startswith(str(win_temp)):
                rel = p.relative_to(win_temp).as_posix().replace('/', '\\')
                return 'C:\\Temp\\' + rel
            return '\\\\wsl.localhost\\Ubuntu\\home\\alice\\proj'
        def fake_run(command, cwd, input_text, timeout_sec, **kwargs):
            self.assertTrue(str(cwd).startswith(str(win_temp)))
            staged_file = Path(cwd) / 'src' / 'flux.f90'
            self.assertTrue(staged_file.is_file())
            out_file = Path(cwd).parent / 'result.json'
            out_file.write_text(json.dumps({
                'summary': '已审', 'read_files': ['C:\\Temp\\' + staged_file.relative_to(win_temp).as_posix().replace('/', '\\')],
                'unread_files': [], 'findings': [{
                    'file': 'C:\\Temp\\' + staged_file.relative_to(win_temp).as_posix().replace('/', '\\'),
                    'line_start': 1, 'line_end': 1, 'severity': 'high', 'category': 'flux',
                    'description': '符号错误', 'evidence': 'u = u + fl',
                }],
            }), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')
        with patch.object(harness, '_is_posix', return_value=True), \
             patch.object(harness.shutil, 'which', side_effect=fake_which), \
             patch.object(harness, '_windows_env', return_value='C:\\Temp'), \
             patch.object(harness, '_wsl_path', side_effect=fake_wsl_path), \
             patch.object(harness, 'run_process', side_effect=fake_run):
            parsed = harness.invoke_readonly_engine('codex:default', self.root, '审查', engines, 30, ['src/flux.f90'])
            self.assertEqual(parsed['read_files'], ['src/flux.f90'])
            self.assertEqual(parsed['findings'][0]['file'], 'src/flux.f90')
            with self.assertRaisesRegex(harness.HarnessError, '纯 Linux 文件系统路径无法在 Windows codex.exe 沙箱内直接写入'):
                harness.invoke_edit_worker('codex:default', self.root, '修改', engines, 30)

    def test_specialty_model_routing_and_thinking_tier_grading(self):
        engines = backends()
        engines['codex']['requested_input_budget_tokens'] = None
        engines['codex']['models'] = harness._model_records({'models': [
            {'provider': 'openai', 'slug': 'model-legacy-fast', 'priority': 0, 'visibility': 'list',
             'context_window': 272000, 'max_context_window': 872000,
             'description': 'Older fast and efficient model.',
             'supported_reasoning_levels': [{'effort': 'low'}, {'effort': 'medium'}, {'effort': 'high'}, {'effort': 'max'}]},
            {'provider': 'openai', 'slug': 'model-workhorse', 'priority': 1, 'visibility': 'list',
             'context_window': 500000, 'max_context_window': 872000,
             'description': 'Latest workhorse model for coding and everyday work.',
             'supported_reasoning_levels': [
                 {'effort': 'low', 'description': 'Fast responses'},
                 {'effort': 'medium', 'description': 'Balanced'},
                 {'effort': 'high', 'description': 'Greater reasoning depth'},
                 {'effort': 'xhigh', 'description': 'Extra high reasoning depth'},
                 {'effort': 'max', 'description': 'Maximum reasoning depth for the hardest problems'},
                 {'effort': 'ultra', 'description': 'Maximum reasoning with automatic task delegation'},
             ]},
            {'provider': 'openai', 'slug': 'model-demanding', 'priority': 2, 'visibility': 'list',
             'context_window': 272000, 'max_context_window': 300000,
             'description': 'Frontier intelligence for the most demanding work.',
             'supported_reasoning_levels': [
                 {'effort': 'low', 'description': 'Fast responses'},
                 {'effort': 'medium', 'description': 'Balanced'},
                 {'effort': 'high', 'description': 'Greater reasoning depth'},
                 {'effort': 'xhigh', 'description': 'Extra high reasoning depth'},
                 {'effort': 'max', 'description': 'Maximum reasoning depth for the hardest problems'},
                 {'effort': 'ultra', 'description': 'Maximum reasoning with automatic task delegation'},
             ]},
            {'provider': 'openai', 'slug': 'model-hidden', 'priority': 3, 'visibility': 'hide',
             'description': 'Fast and affordable agentic coding model.',
             'supported_reasoning_levels': [{'effort': 'low'}, {'effort': 'medium'}, {'effort': 'high'}]},
            {'provider': 'openai', 'slug': 'model-fast', 'priority': 4, 'visibility': 'list',
             'context_window': 272000, 'max_context_window': 872000,
             'description': 'Fast and affordable model for easier tasks.',
             'supported_reasoning_levels': [{'effort': 'low'}, {'effort': 'medium'}, {'effort': 'high'}, {'effort': 'max'}]},
        ]})
        # 'gemini' 含 'mini' 子串，不能据此把所有原生模型当成轻量模型
        engines['agy']['models'] = [
            {'slug': 'gemini-7-pro-medium', 'display_name': 'Gemini 7 Pro (Medium)'},
            {'slug': 'gemini-7-pro-high', 'display_name': 'Gemini 7 Pro (High)'},
            {'slug': 'gemini-7-pro-low', 'display_name': 'Gemini 7 Pro (Low)'},
            {'slug': 'gemini-7-deep-high', 'display_name': 'Gemini 7 Deep (High)'},
            {'slug': 'gemini-7-deep-xhigh', 'display_name': 'Gemini 7 Deep (XHigh)'},
            {'slug': 'gemini-7-deep-medium', 'display_name': 'Gemini 7 Deep (Medium)'},
            {'slug': 'gemini-7-deep-low', 'display_name': 'Gemini 7 Deep (Low)'},
            {'slug': 'gemini-7-flash-high', 'display_name': 'Gemini 7 Flash (High)'},
            {'slug': 'gemini-7-flash-low', 'display_name': 'Gemini 7 Flash (Low)'},
            {'slug': 'othervendor-pro-high', 'display_name': 'OtherVendor Pro (High)'},
            {'slug': 'othervendor-pro-low', 'display_name': 'OtherVendor Pro (Low)'},
        ]
        # 1. 数理推导与路线攻坚：选深度推理专长模型 + 单模型纯思考最高挡 (max/xhigh)，默认跳过套娃 ultra
        tier, reason = harness.select_task_tier('auto', 'math', '推导一维守恒律离散残差', 1, 500)
        self.assertEqual((tier, reason), ('max', 'task_specialty_math_max'))
        c_math = harness.resolve_execution_profile(engines, 'codex:default', tier, '推导一维守恒律离散残差', kind='math', estimated_tokens=500)
        self.assertEqual((c_math['model'], c_math['reasoning_effort'], c_math['profile_source']),
                         ('model-demanding', 'max', 'specialty_deep_reasoning'))
        a_math = harness.resolve_execution_profile(engines, 'agy:default', tier, '推导一维守恒律离散残差', kind='math', estimated_tokens=500)
        self.assertEqual((a_math['model'], a_math['reasoning_effort'], a_math['cli_effort'], a_math['profile_source']),
                         ('gemini-7-deep-xhigh', 'xhigh', None, 'specialty_deep_reasoning'))

        # 输入超过深度推理模型的规划预算时，自动回退至大上下文工程主力模型
        c_math_big = harness.resolve_execution_profile(engines, 'codex:default', 'max', '大输入推导', kind='math', estimated_tokens=400000)
        self.assertEqual((c_math_big['model'], c_math_big['reasoning_effort']), ('model-workhorse', 'max'))

        # 显式指定 ultra 时才放行委派挡位
        tier_ultra, reason_ultra = harness.select_task_tier('ultra', 'math', '推导', 1, 500)
        self.assertEqual((tier_ultra, reason_ultra), ('ultra', 'explicit_tier_flag'))
        c_ultra = harness.resolve_execution_profile(engines, 'codex:default', tier_ultra, '推导', kind='math')
        self.assertEqual((c_ultra['model'], c_ultra['reasoning_effort']), ('model-demanding', 'ultra'))

        # 2. 常规编码与审查：默认（选项 A）选大窗口工程旗舰模型 + max 挡；要求省 token/降挡时（选项 B）切为 high 挡
        tier_code, reason_code = harness.select_task_tier('auto', 'code', '整理命令行参数解析辅助函数', 2, 1500)
        self.assertEqual((tier_code, reason_code), ('max', 'default_flagship_max'))
        c_code = harness.resolve_execution_profile(engines, 'codex:default', tier_code, '整理命令行参数解析辅助函数', kind='code', estimated_tokens=1500)
        self.assertEqual((c_code['model'], c_code['reasoning_effort'], c_code['profile_source']),
                         ('model-workhorse', 'max', 'frontier_catalog'))
        a_code = harness.resolve_execution_profile(engines, 'agy:default', tier_code, '整理命令行参数解析辅助函数', kind='code', estimated_tokens=1500)
        self.assertEqual((a_code['model'], a_code['reasoning_effort']), ('gemini-7-pro-high', 'high'))
        tier_code_eco, reason_code_eco = harness.select_task_tier('auto', 'code', '省点 token 整理命令行参数解析辅助函数', 2, 1500)
        self.assertEqual((tier_code_eco, reason_code_eco), ('high', 'economy_adaptive_high'))
        c_code_eco = harness.resolve_execution_profile(engines, 'codex:default', tier_code_eco, '省点 token 整理命令行参数解析辅助函数', kind='code', estimated_tokens=1500)
        self.assertEqual((c_code_eco['model'], c_code_eco['reasoning_effort'], c_code_eco['profile_source']),
                         ('model-workhorse', 'high', 'frontier_catalog'))

        # 否定词不应误触发降挡，且 DGSEM/边界条件审查自动拉满单模型纯思考最高挡并匹配深度推理模型
        tier_neg, reason_neg = harness.select_task_tier('auto', 'review', '不要低档，仔细检查 DGSEM 边界条件与守恒性', 2, 1000)
        self.assertEqual((tier_neg, reason_neg), ('max', 'complexity_high_domain_max'))
        c_rev_deep = harness.resolve_execution_profile(engines, 'codex:default', tier_neg, '仔细检查 DGSEM 边界条件与守恒性', kind='review')
        self.assertEqual((c_rev_deep['model'], c_rev_deep['reasoning_effort']), ('model-demanding', 'max'))

        # 3. 短篇纯文字润色自动用 medium，长篇润色用 high
        tier_pol, reason_pol = harness.select_task_tier('auto', 'polish', '润色这句话使其符合CFD规范书面语：迎风格式在CFL大于1时失稳', 1, 600)
        self.assertEqual((tier_pol, reason_pol), ('medium', 'complexity_lightweight_polish'))
        c_med = harness.resolve_execution_profile(engines, 'codex:default', tier_pol, kind='polish')
        self.assertEqual((c_med['model'], c_med['reasoning_effort']), ('model-workhorse', 'medium'))
        a_med = harness.resolve_execution_profile(engines, 'agy:default', tier_pol, kind='polish')
        self.assertEqual(a_med['model'], 'gemini-7-pro-medium')
        tier_pol_long, reason_pol_long = harness.select_task_tier('auto', 'polish', '整章学术论文语言润色', 5, 6000)
        self.assertEqual((tier_pol_long, reason_pol_long), ('high', 'complexity_long_polish'))

        # 4. 省额度/表面检查自动匹配当代轻量高速模型（跳过隐藏与 Older 旧代，且绝不误烧异构第三方配额池）
        tier_low, reason_low = harness.select_task_tier('auto', 'review', '省额度用低档快速看一下', 1, 400)
        self.assertEqual((tier_low, reason_low), ('low', 'user_description_low'))
        c_low = harness.resolve_execution_profile(engines, 'codex:default', tier_low, '省额度用低档快速看一下', kind='review')
        self.assertEqual((c_low['model'], c_low['reasoning_effort'], c_low['profile_source']),
                         ('model-fast', 'low', 'specialty_fast_lightweight'))
        a_low = harness.resolve_execution_profile(engines, 'agy:default', tier_low, '省额度用低档快速看一下', kind='review')
        self.assertEqual((a_low['model'], a_low['profile_source']), ('gemini-7-flash-low', 'specialty_fast_lightweight'))
        a_low_no_lite = harness.resolve_execution_profile(engines, 'agy:default', 'low', '不要轻量模型，用低档看一下', kind='review')
        self.assertEqual(a_low_no_lite['model'], 'gemini-7-pro-low')
        c_low_no_lite = harness.resolve_execution_profile(engines, 'codex:default', 'low', '不要轻量模型，用低档看一下', kind='review')
        self.assertEqual((c_low_no_lite['model'], c_low_no_lite['reasoning_effort']), ('model-workhorse', 'low'))
        a_explicit_3p = harness.resolve_execution_profile(engines, 'agy:othervendor-pro-high', 'high', '指定模型审查', kind='review')
        self.assertEqual(a_explicit_3p['model'], 'othervendor-pro-high')

        # 5. 新一代轻量高速标识 (hydrogen) 与混动路由器共存：深思与主力锁定 flagship (carbon)，low 挡精准命中同代 hydrogen（不误用旧代 flash），显式/模糊调用均可用
        g4_engines = backends()
        g4_engines['agy']['models'] = [
            {'slug': 'gemini-4-hydrogen-xhigh', 'display_name': 'Gemini 4 Hydrogen XHigh'},
            {'slug': 'gemini-4-carbon-medium', 'display_name': 'Gemini 4 Carbon (Medium)'},
            {'slug': 'gemini-4-carbon-high', 'display_name': 'Gemini 4 Carbon (High)'},
            {'slug': 'gemini-4-carbon-low', 'display_name': 'Gemini 4 Carbon (Low)'},
            {'slug': 'gemini-4-argon-high', 'display_name': 'Gemini 4 Argon (High)'},
            {'slug': 'gemini-4-hydrogen-low', 'display_name': 'Gemini 4 Hydrogen (Low)'},
            {'slug': 'gemini-pro-orchestrator', 'display_name': 'Orchestrator (Carbon)'},
            {'slug': 'gemini-pro-orchestrator-argon', 'display_name': 'Orchestrator (Argon)'},
            {'slug': 'gemini-3.8-flash-low', 'display_name': 'Gemini 3.8 Flash (Low)'},
        ]
        g4_work = harness.resolve_execution_profile(g4_engines, 'agy:default', 'high', '常规功能实现', kind='code')
        g4_deep = harness.resolve_execution_profile(g4_engines, 'agy:default', 'max', '推导熵守恒格式', kind='math')
        g4_fast = harness.resolve_execution_profile(g4_engines, 'agy:default', 'low', '省额度快速扫查', kind='review')
        g4_no_light = harness.resolve_execution_profile(g4_engines, 'agy:default', 'low', '不要 hydrogen 低档看下', kind='review')
        self.assertEqual((g4_work['model'], g4_work['profile_source']), ('gemini-4-carbon-high', 'frontier_catalog'))
        self.assertEqual((g4_deep['model'], g4_deep['profile_source']), ('gemini-4-carbon-high', 'specialty_deep_reasoning'))
        self.assertEqual((g4_fast['model'], g4_fast['profile_source']), ('gemini-4-hydrogen-low', 'specialty_fast_lightweight'))
        self.assertEqual(g4_no_light['model'], 'gemini-4-carbon-low')
        self.assertEqual(harness.resolve_execution_profile(g4_engines, 'agy:Orchestrator (Carbon)', 'high')['model'], 'gemini-pro-orchestrator')
        self.assertEqual(harness.resolve_execution_profile(g4_engines, 'agy:orchestrator-argon', 'high')['model'], 'gemini-pro-orchestrator-argon')
        self.assertEqual(harness.resolve_execution_profile(g4_engines, 'agy:carbon', 'medium')['model'], 'gemini-4-carbon-medium')
        with self.assertRaises(harness.HarnessError):
            harness.resolve_execution_profile(engines, 'codex:fast', 'low')

        # 自然语言显式/模糊点名模型 + CFD 物理术语/否定词零误触 + Codex 模糊预算 + agy:<alias> 目录发现
        nl_argon = harness.resolve_execution_profile(g4_engines, 'agy:default', 'high', '请调用 Argon 模型复核推导', kind='review')
        self.assertEqual((nl_argon['model'], nl_argon['profile_source']), ('gemini-4-argon-high', 'prompt_fuzzy_model'))
        nl_carbon_med = harness.resolve_execution_profile(g4_engines, 'agy:default', 'high', '请调用 Carbon (Medium) 模型复核', kind='review')
        self.assertEqual((nl_carbon_med['model'], nl_carbon_med['profile_source']), ('gemini-4-carbon-medium', 'prompt_fuzzy_model'))
        nl_orch = harness.resolve_execution_profile(g4_engines, 'agy:default', 'high', '请使用 Orchestrator (Carbon) 模型检查', kind='review')
        self.assertEqual((nl_orch['model'], nl_orch['profile_source']), ('gemini-pro-orchestrator', 'prompt_fuzzy_model'))
        nl_codex = harness.resolve_execution_profile(engines, 'codex:default', 'max', '请调用 demanding 模型推导', kind='math')
        self.assertEqual((nl_codex['model'], nl_codex['profile_source']), ('model-demanding', 'prompt_fuzzy_model'))

        cfd_safe = harness.resolve_execution_profile(
            g4_engines, 'agy:default', 'high',
            '计算 hydrogen detonation 爆轰波与 carbon 热流，采用 k-omega 模型与 Navier-Stokes 模型离散；采用 hydrogen 模型计算爆轰', kind='code',
        )
        self.assertEqual((cfd_safe['model'], cfd_safe['profile_source']), ('gemini-4-carbon-high', 'frontier_catalog'))
        self.assertIsNone(harness._match_effort_level(['ultra'], 'high', ['ultra']))
        neg_prompt = harness.resolve_execution_profile(g4_engines, 'agy:default', 'high', '不要用 Argon 模型，常规复核', kind='review')
        self.assertEqual((neg_prompt['model'], neg_prompt['profile_source']), ('gemini-4-carbon-high', 'frontier_catalog'))

        self.assertEqual(harness._budget_for_spec(engines, 'codex:demanding'), 270000)

        # 单文件表面拼写检查默认仍保持旗舰 max；仅在模糊/明确要求省 token 或降挡时降至 low
        tier_typo_default, reason_typo_default = harness.select_task_tier('auto', 'review', '检查注释错别字与 typo', 1, 300)
        self.assertEqual((tier_typo_default, reason_typo_default), ('max', 'default_flagship_max'))
        tier_typo, reason_typo = harness.select_task_tier('auto', 'review', '省点 token 检查注释错别字与 typo', 1, 300)
        self.assertEqual((tier_typo, reason_typo), ('low', 'economy_trivial_surface_low'))

        # 验证 CLI 参数传递：codex 附带 model_reasoning_effort="max"（非 ultra），agy 使用挡位后缀 slug 且不重复附加 --effort
        def check_codex_cmd(command, cwd, input_text, timeout_sec, **kwargs):
            self.assertIn('-m', command)
            self.assertEqual(command[command.index('-m') + 1], 'model-demanding')
            self.assertIn('model_reasoning_effort="max"', command)
            self.assertIn('approval_policy="never"', command)
            Path(command[command.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        def check_agy_cmd(command, cwd, input_text, timeout_sec, **kwargs):
            self.assertIn('--model', command)
            self.assertEqual(command[command.index('--model') + 1], 'gemini-7-deep-xhigh')
            self.assertNotIn('--effort', command)
            stream = json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'structured_output': TASK}})
            return subprocess.CompletedProcess(command, 0, stream, '')

        with patch.object(harness, 'run_process', side_effect=check_codex_cmd):
            out = harness.invoke_engine(c_math['spec'], self.root, '推导', engines, harness.TASK_JSON_SCHEMA, True, 30, 'readonly-collaborator', None, c_math['cli_effort'])
            self.assertEqual(out, TASK)
        with patch.object(harness, 'run_process', side_effect=check_agy_cmd):
            out = harness.invoke_engine(a_math['spec'], self.root, '推导', engines, harness.TASK_JSON_SCHEMA, True, 30, 'readonly-collaborator', None, a_math['cli_effort'])
            self.assertEqual(out, TASK)

    def test_catalog_routing_is_permutation_invariant_and_rejects_ambiguous_aliases(self):
        engines = backends()
        engines['codex']['models'] = [
            {'slug': 'gpt-8-router', 'priority': 0, 'supported_efforts': ['max']},
            {'slug': 'hosted-gpt', 'provider': 'thirdparty', 'priority': 0, 'supported_efforts': ['max']},
            {'slug': 'unidentified', 'priority': 0, 'supported_efforts': ['max']},
            {'slug': 'gpt-8-alpha', 'priority': 1, 'supported_efforts': ['high']},
            {'slug': 'gpt-8-beta', 'priority': 1, 'supported_efforts': ['high']},
        ]
        engines['agy']['models'] = [
            {'slug': 'claude-example-high'}, {'slug': 'gpt-oss-example-high'},
            {'slug': 'unknown-high'}, {'slug': 'gemini-4-pro-orchestrator'},
            {'slug': 'gemini-3-pro-high'}, {'slug': 'gemini-4-carbon-high'}, {'slug': 'gemini-4-argon-high'},
        ]
        for _ in range(2):
            self.assertEqual(harness.resolve_execution_profile(engines, 'codex:default')['model'], 'gpt-8-alpha')
            self.assertEqual(harness.resolve_execution_profile(engines, 'agy:default')['model'], 'gemini-4-carbon-high')
            for alias in ('codex:gpt-8', 'agy:gemini'):
                with self.subTest(alias=alias), self.assertRaises(harness.HarnessError) as exc:
                    harness.resolve_execution_profile(engines, alias)
                self.assertGreater(len(exc.exception.details['matching_models']), 1)
            for name in engines:
                engines[name]['models'].reverse()
        self.assertEqual(harness.resolve_execution_profile(engines, 'agy:unknown-high')['model_category'], 'unknown')
        self.assertEqual(harness.resolve_execution_profile(engines, 'codex:hosted-gpt')['model_category'], 'hosted_or_third_party')

    def test_tier_policy_ignores_terms_in_prose_quotes_and_negations(self):
        for prompt in ('Use a fast Fourier transform on edge nodes',
                       'Refactor bridge dispatch',
                       'Summarize the discussion of ultra and xhigh',
                       'Review "root cause, WENO, concurrency" as sample text',
                       'No need to discuss conservation or deadlock',
                       '请不要分析根因及并发，核对输出排版',
                       '避免推导熵稳定格式，整理结果',
                       '只读核对文档对\u201c最高挡\u201d的描述',
                       '不要省 token，不降档，核对输出排版',
                       '只读核对文档对\u201c省额度\u201d和\u201c用低档\u201d的描述'):
            with self.subTest(prompt=prompt), patch.dict(os.environ, {'CAE_DEFAULT_TIER': 'ultra'}, clear=True):
                self.assertEqual(harness.select_task_tier(text=prompt, file_count=80, estimated_tokens=900000),
                                 ('max', 'default_flagship_max'))
            with self.subTest(prompt=prompt, env='high'), patch.dict(os.environ, {'CAE_DEFAULT_TIER': 'high'}, clear=True):
                self.assertEqual(harness.select_task_tier(text=prompt, file_count=80, estimated_tokens=900000),
                                 ('high', 'env_default_tier'))
        self.assertEqual(harness.select_task_tier(text='Review a race condition in message dispatch'),
                         ('max', 'complexity_high_domain_max'))
        self.assertEqual(harness.select_task_tier(text='核对离散守恒性'),
                         ('max', 'complexity_high_domain_max'))
        self.assertEqual(harness.select_task_tier(text='拼写检查', file_count=12, estimated_tokens=6000),
                         ('max', 'default_flagship_max'))
        self.assertEqual(harness.select_task_tier(text='省额度拼写检查', file_count=12, estimated_tokens=6000),
                         ('low', 'economy_trivial_surface_low'))
        self.assertEqual(harness.select_task_tier(text='省点 token 核对模块接口', file_count=12, estimated_tokens=6000),
                         ('high', 'economy_adaptive_high'))
        self.assertEqual(harness.select_task_tier('ultra', text='审查')[0], 'ultra')

    def test_orchestrator_fixed_configuration_for_slug_display_and_unique_alias(self):
        engines = backends()
        engines['agy']['models'] = [
            {'slug': 'gemini-pro-orchestrator', 'display_name': 'Orchestrator (Carbon)'},
            {'slug': 'gemini-pro-orchestrator-argon', 'display_name': 'Orchestrator (Argon)'},
        ]
        commands = []

        def runner(command, cwd, input_text, timeout_sec, **kwargs):
            commands.append(command)
            self.assertNotIn('--effort', command)
            model = command[command.index('--model') + 1]
            stream = '\n'.join([json.dumps({'event': 'init', 'init': {'model': model}}),
                               json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'structured_output': TASK}})])
            return subprocess.CompletedProcess(command, 0, stream, '')

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'run_process', side_effect=runner):
            for model in ('gemini-pro-orchestrator', 'Orchestrator (Carbon)', 'carbon-orchestrator'):
                with self.subTest(model=model):
                    args = self.args('task', '--target', str(self.root), '--kind', 'review', '--task', '核对说明',
                                     '--engine', 'agy:' + model, '--tier', 'xhigh')
                    code, report = self.capture(harness.cmd_task, args)
                    self.assertEqual(code, 0)
                    self.assertEqual(report['requested_tier'], 'xhigh')
                    self.assertEqual(report['effort_source'], 'fixed_configuration')
                    self.assertIsNone(report['declared_effort'])
                    self.assertIsNone(report['effective_effort'])
                    self.assertEqual(report['invocation']['actual_model'], 'gemini-pro-orchestrator')
            with self.assertRaises(harness.HarnessError):
                harness.resolve_execution_profile(engines, 'agy:orchestrator')
        self.assertEqual(len(commands), 3)

    def test_gate_does_not_claim_escalation_when_command_variant_is_unchanged(self):
        engines = backends()
        engines['agy']['models'] = [{'slug': 'gemini-pro-orchestrator'}]
        seen = []

        def runner(command, cwd, input_text=None, timeout_sec=600, **kwargs):
            if kwargs.get('shell'):
                return subprocess.CompletedProcess(command, 0 if len(seen) > 1 else 1, 'verify', '')
            seen.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(TASK), '')

        args = self.args('gate', '--worker', 'agy:gemini-pro-orchestrator', '--target', str(self.root),
                         '--task', '整理赋值逻辑', '--verify-cmd', 'verify', '--max-rounds', '2')
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'run_process', side_effect=runner):
            code, report = self.capture(harness.cmd_gate, args)
        self.assertEqual(code, 0)
        self.assertEqual(report['rounds_used'], 2)
        self.assertEqual(report['selected_tier'], 'max')
        self.assertEqual(report['effort_source'], 'fixed_configuration')
        self.assertFalse(report['history'][1]['escalated'])
        self.assertNotIn('--effort', report['history'][1]['invocation']['argv'])

    def test_gate_round2_auto_escalates_thinking_tier_to_max(self):
        target_py = self.root / 'helper.py'
        target_py.write_text('x = 0\n', encoding='utf-8')
        engines = backends()
        engines['codex']['models'] = [
            {'slug': 'model-workhorse', 'provider': 'openai', 'priority': 1, 'visibility': 'list',
             'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
             'delegating_efforts': ['ultra']},
        ]
        seen_efforts = []

        def fake_run(command, root, input_text=None, timeout_sec=600, **kwargs):
            if kwargs.get('shell'):
                return subprocess.CompletedProcess(command, 0 if len(seen_efforts) == 2 else 1, 'verify', '')
            effort = next(value for value in command if value.startswith('model_reasoning_effort='))
            seen_efforts.append((command[command.index('-m') + 1], effort))
            target_py.write_text(f'x = {len(seen_efforts)}\n', encoding='utf-8')
            Path(command[command.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        verify_cmd = f'"{sys.executable}" -B -c "import pathlib,sys; sys.exit(0 if \'x = 2\' in pathlib.Path(\'helper.py\').read_text() else 1)"'
        args = self.args('gate', '--caller', 'antigravity', '--target', str(self.root),
                         '--task', '省点 token 整理返回值赋值逻辑', '--write-scope', '*.py', '--verify-cmd', verify_cmd, '--max-rounds', '3')
        with patch.object(harness, '_args_backends', return_value=engines), patch.object(harness, 'run_process', side_effect=fake_run):
            code, report = self.capture(harness.cmd_gate, args)
        self.assertEqual(code, 0)
        self.assertEqual(seen_efforts, [('model-workhorse', 'model_reasoning_effort="high"'),
                                        ('model-workhorse', 'model_reasoning_effort="max"')])
        self.assertEqual(report['rounds_used'], 2)
        self.assertEqual(report['selected_tier'], 'max')
        self.assertEqual(report['tier_reason'], 'gate_retry_escalated_max')
        self.assertFalse(report['history'][0]['escalated'])
        self.assertTrue(report['history'][1]['escalated'])
        self.assertIn('model_reasoning_effort="max"', report['history'][1]['invocation']['argv'])

    def test_wsl_unauthenticated_linux_agy_falls_back_to_windows_agy_exe(self):
        linux_agy = self.root / 'linux-home' / '.gemini' / 'bin' / 'agy'
        linux_agy.parent.mkdir(parents=True)
        linux_agy.write_bytes(b'ELF')
        win_home = self.root / 'win-home'
        win_agy = win_home / '.gemini' / 'bin' / 'agy.exe'
        win_agy.parent.mkdir(parents=True)
        win_agy.write_bytes(b'MZ')
        win_temp = self.root / 'win-temp'
        win_temp.mkdir()

        engines = backends()
        engines['agy'].update(binary=str(linux_agy), windows_interop=False,
                              config_home=str(linux_agy.parents[1]), authentication_status='not_checked',
                              model_catalog={'freshness': 'within_assessment_window'},
                              models=[{'slug': 'gemini-7-pro-high'}])
        windows_settings = win_agy.parents[1] / 'antigravity-cli' / 'settings.json'
        windows_settings.parent.mkdir(parents=True)
        windows_settings.write_text('{"model": "Windows configured model"}', encoding='utf-8')

        def fake_which(cmd):
            return {'powershell.exe': '/mnt/c/ps.exe', 'taskkill.exe': '/mnt/c/tk.exe', 'wslpath': '/usr/bin/wslpath'}.get(cmd)

        def fake_wsl_path(path, windows=True):
            p = Path(path)
            if not windows:
                return str(win_temp)
            return 'D:\\work\\' + p.name

        calls = []

        def fake_run(command, cwd, input_text=None, timeout_sec=600, **kwargs):
            calls.append(command)
            if command == [str(linux_agy), 'models']:
                return subprocess.CompletedProcess(command, 1, '', 'Error: Please sign in to view available models.')
            self.assertEqual(command[0], 'powershell.exe')
            decoded = base64.b64decode(command[-1]).decode('utf-16le')
            if "$info.Arguments = 'models'" in decoded:
                return subprocess.CompletedProcess(command, 0, 'gemini-8-carbon-high\tGemini 8 Carbon (High)\n', '')
            stream = json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'structured_output': TASK}})
            return subprocess.CompletedProcess(command, 0, stream, '')

        with patch.dict(os.environ, {}, clear=True), patch.object(harness, '_is_posix', return_value=True), \
             patch.object(harness.shutil, 'which', side_effect=fake_which), \
             patch.object(harness, '_find_windows_agy_in_wsl', return_value=str(win_agy)), \
             patch.object(harness, '_windows_env', return_value='C:\\Temp'), \
             patch.object(harness, '_wsl_path', side_effect=fake_wsl_path), \
             patch.object(harness, 'run_process', side_effect=fake_run):
            receipt = {}
            out = harness.invoke_engine('agy:default', self.root, '审查', engines, harness.TASK_JSON_SCHEMA, receipt=receipt)
        self.assertEqual(out, TASK)
        self.assertEqual(len(calls), 3)
        self.assertTrue(engines['agy']['windows_interop'])
        self.assertEqual(engines['agy']['binary'], str(win_agy))
        self.assertEqual(engines['agy']['model'], 'Windows configured model')
        self.assertEqual(engines['agy']['model_catalog']['source'], 'agy_models_cli')
        self.assertEqual(receipt['requested_engine'], 'agy:default')
        self.assertEqual(receipt['model_requested_to_cli'], 'gemini-8-carbon-high')
        self.assertEqual(receipt['execution_environment']['fallback_from']['binary'], str(linux_agy))

    def test_quota_exhaustion_cooldown_and_preflight_gate(self):
        import datetime as _dt
        cfg_dir = self.root / 'codex-cfg'
        cfg_dir.mkdir()
        ws_dir = self.root / 'workspace'
        ws_dir.mkdir()
        target_file = ws_dir / 'solver.py'
        target_file.write_text('u = 1\n', encoding='utf-8')
        engines = backends()
        engines['codex']['config_home'] = str(cfg_dir)

        future_dt = (_dt.datetime.now() + _dt.timedelta(days=3)).replace(second=0, microsecond=0)
        reset_str = f"{future_dt.strftime('%b')} {future_dt.day}th, {future_dt.strftime('%Y %I:%M %p')}"
        quota_stderr = f'ERROR: You\u2019ve hit your usage limit. Purchase more credits or try again at {reset_str}.\n'
        fail_proc = subprocess.CompletedProcess(['codex'], 1, '', quota_stderr)
        args_run = self.args('task', '--caller', 'antigravity', '--kind', 'math', '--target', str(target_file), '--task', '核对守恒律')

        # 1. 首次触发限额：返回退出码 5 (quota_exhausted)，写入本地冷却缓存并提供用户二选一选项
        with patch.object(harness, '_args_backends', return_value=engines), patch.object(harness, 'run_process', return_value=fail_proc):
            code1, report1 = self.capture(harness.cmd_task, args_run)
        self.assertEqual(code1, 5)
        self.assertEqual(report1['status'], 'quota_exhausted')
        self.assertFalse(report1['preflight_blocked'])
        self.assertTrue(report1['user_confirmation_required'])
        self.assertEqual(report1['quota_cooldown']['reset_hint'], reset_str)
        self.assertEqual(report1['quota_cooldown']['cooldown_source'], 'parsed_reset_time')
        events = harness._quota_events_dir(harness._quota_state_path('codex', engines['codex']))
        quota_file = events / (report1['quota_cooldown']['event_id'] + '.json')
        self.assertTrue(quota_file.is_file())

        # 2. 跨进程新会话（清空内存态）再次发起时：事前直接拦截，绝不启动后端子进程
        harness._SESSION_QUOTA_BLOCKED.clear()
        with patch.object(harness, '_args_backends', return_value=engines), patch.object(harness, 'run_process') as not_called:
            code2, report2 = self.capture(harness.cmd_task, args_run)
        not_called.assert_not_called()
        self.assertEqual(code2, 5)
        self.assertEqual(report2['status'], 'quota_exhausted')
        self.assertTrue(report2['preflight_blocked'])
        self.assertEqual(len(report2['user_choices']), 2)

        # 3. dry-run 预演仍正常返回 0，并标注 exhausted_cached 状态
        args_dry = self.args('task', '--caller', 'antigravity', '--kind', 'math', '--target', str(target_file), '--task', '核对守恒律', '--dry-run')
        with patch.object(harness, '_args_backends', return_value=engines):
            code_dry, report_dry = self.capture(harness.cmd_task, args_dry)
        self.assertEqual(code_dry, 0)
        self.assertEqual(report_dry['quota_status'], 'exhausted_cached')

        # 4. gate 多轮循环遇到 quota_exhausted 在第 1 轮立即熔断退出
        def quota_worker(_prompt, _round):
            raise harness.HarnessError('quota', 'quota_exhausted', preflight_blocked=False, user_confirmation_required=True, user_choices=['1', '2'])
        gate_res = harness.run_physics_gate_loop(ws_dir, '改代码', 'verify', quota_worker, max_rounds=3)
        self.assertEqual(gate_res['status'], 'quota_exhausted')
        self.assertEqual(gate_res['rounds_used'], 1)
        self.assertTrue(gate_res['user_confirmation_required'])

        # 5. 用户充值确认后传入 --retry-quota：放行执行并在成功后自动清除冷却缓存文件
        def ok_runner(command, *_a, **_kw):
            Path(command[command.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')
        args_retry = self.args('task', '--caller', 'antigravity', '--kind', 'math', '--target', str(target_file), '--task', '核对守恒律', '--retry-quota')
        with patch.object(harness, '_args_backends', return_value=engines), patch.object(harness, 'run_process', side_effect=ok_runner):
            code3, report3 = self.capture(harness.cmd_task, args_retry)
        self.assertEqual(code3, 0)
        self.assertEqual(report3['status'], 'ok')
        self.assertFalse(quota_file.exists())
        self.assertEqual(report3['quota_status'], 'unknown_until_called')

        # 6. 过期冷却文件自动清理放行
        quota_file.write_text(json.dumps({'backend': 'codex', 'exhausted': True, 'cooldown_until_epoch': 1.0}), encoding='utf-8')
        self.assertIsNone(harness._load_active_quota_cooldown('codex', engines['codex']))
        self.assertFalse(quota_file.exists())

    def test_audit_quota_fuse_survives_inflight_success_and_keeps_coverage(self):
        for name in ('a.py', 'b.py', 'c.py'):
            (self.root / name).write_text('x = 1\n', encoding='utf-8')
        engines = backends()
        cfg = tempfile.TemporaryDirectory(prefix='cae-test-config-')
        self.addCleanup(cfg.cleanup)
        engines['codex']['config_home'] = cfg.name
        calls, ready, quota_saved = [], threading.Barrier(2), threading.Event()
        record_quota = harness._record_quota_exhausted

        def signal_after_quota(*args, **kwargs):
            cooldown = record_quota(*args, **kwargs)
            quota_saved.set()
            return cooldown

        def runner(command, cwd, input_text, timeout_sec, **kwargs):
            name = next(line[2:] for line in input_text.splitlines() if line.startswith('- '))
            calls.append(name)
            if name in ('a.py', 'b.py'):
                ready.wait(timeout=5)
            if name == 'a.py':
                return subprocess.CompletedProcess(command, 1, '', 'hit your usage limit')
            if name == 'b.py':
                self.assertTrue(quota_saved.wait(5))
            Path(command[command.index('-o') + 1]).write_text(
                json.dumps({**AUDIT, 'read_files': [name]}), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        original = harness.concurrent.futures.ThreadPoolExecutor
        args = self.args('audit', '--target', str(self.root), '--single', '--left', 'codex:default',
                         '--strategy', 'sharded', '--shard-tokens', '1', '--retry-quota')
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness.concurrent.futures, 'ThreadPoolExecutor', side_effect=lambda **kw: original(max_workers=2)), \
             patch.object(harness, '_record_quota_exhausted', side_effect=signal_after_quota), \
             patch.object(harness, 'run_process', side_effect=runner):
            code, report = self.capture(harness.cmd_audit, args)
        self.assertEqual(code, 3)
        self.assertTrue(report['quota_fuse_triggered'])
        self.assertEqual(set(calls), {'a.py', 'b.py'})
        self.assertEqual(report['completed_units'][0]['read_files'], ['b.py'])
        self.assertEqual(report['completed_count'], 1)
        self.assertTrue(next(error for error in report['errors'] if error['planned_files'] == ['c.py'])['preflight_blocked'])
        events = harness._quota_events_dir(harness._quota_state_path('codex', engines['codex']))
        self.assertTrue((events / (report['quota_cooldown']['event_id'] + '.json')).is_file())

    def test_retry_success_preserves_new_quota_failure_from_another_process(self):
        writer = self.root / 'quota_writer.py'
        writer.write_text("""
import importlib.util, json, os, sys, time
from pathlib import Path

spec = importlib.util.spec_from_file_location('harness_fixture', sys.argv[1])
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
info = {'config_home': sys.argv[2]}
assert sys.stdin.readline().strip() == 'record failure'
if sys.argv[3] == 'legacy':
    state = harness._quota_state_path('codex', info, legacy=True)
    pending = state.with_suffix('.pending')
    pending.write_text(json.dumps({'backend': 'codex', 'exhausted': True, 'event_id': 'new-from-legacy',
                                   'cooldown_until_epoch': time.time() + 3600}), encoding='utf-8')
    os.replace(pending, state)
else:
    harness._record_quota_exhausted('codex', info, 'new failure from another process')
""", encoding='utf-8')
        for mode in ('current', 'legacy'):
            with self.subTest(mode=mode):
                info = {'config_home': str(self.root / mode)}
                state = harness._quota_state_path('codex', info)
                events = harness._quota_events_dir(state)
                if mode == 'legacy':
                    state = harness._quota_state_path('codex', info, legacy=True)
                    state.parent.mkdir(parents=True)
                    first_id = 'old-legacy'
                    state.write_text(json.dumps({'backend': 'codex', 'exhausted': True, 'event_id': first_id,
                                                 'cooldown_until_epoch': time.time() + 300}), encoding='utf-8')
                    old = state
                else:
                    first_id = harness._record_quota_exhausted('codex', info, 'old cooldown')['event_id']
                    old = events / (first_id + '.json')
                retry = harness._QuotaBatch(retry=True)
                harness._enforce_preflight_quota({'codex': info}, ('codex:default',), batch=retry)
                child = subprocess.Popen([sys.executable, '-B', str(writer), str(SCRIPT), info['config_home'], mode],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                original_unlink = Path.unlink
                original_publish = harness._publish_quota_record
                child_started = threading.Event()

                def record_new_failure():
                    child_started.set()
                    _, errors = child.communicate('record failure\n', timeout=10)
                    self.assertEqual(child.returncode, 0, errors)

                def unlink_after_new_failure(path, *args, **kwargs):
                    if path == old:
                        record_new_failure()
                    return original_unlink(path, *args, **kwargs)

                def acknowledge_after_new_failure(path, record):
                    if path.parent.name == 'acknowledged':
                        record_new_failure()
                    return original_publish(path, record)

                try:
                    with patch.object(Path, 'unlink', unlink_after_new_failure), \
                         patch.object(harness, '_publish_quota_record', acknowledge_after_new_failure):
                        harness._mark_quota_success('codex', info, retry)
                    self.assertTrue(child_started.is_set())
                    cooldown = harness._load_active_quota_cooldown('codex', info, cleanup_expired=False)
                    self.assertIsNotNone(cooldown)
                    self.assertNotEqual(cooldown['event_id'], first_id)
                    with self.assertRaises(harness.HarnessError) as blocked:
                        harness._enforce_preflight_quota({'codex': info}, ('codex:default',), batch=retry)
                    self.assertEqual(blocked.exception.status, 'quota_exhausted')
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=10)

    def test_agy_stale_catalog_refresh_checks_quota_and_does_not_swallow_failures(self):
        engines = backends()
        info = engines['agy']
        info.update(model_catalog={'freshness': 'stale'}, config_home=str(self.root / 'native'))
        args = self.args('task', '--target', str(self.root), '--kind', 'review', '--task', '整理正文', '--engine', 'agy:default')
        listing = Mock(return_value=subprocess.CompletedProcess(['agy', 'models'], 1, '', 'network unavailable'))
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_run_agy_listing', listing), patch.object(harness, 'invoke_engine') as worker:
            code, report = self.capture(harness.cmd_task, args)
            self.assertEqual(code, 1)
            self.assertIn('停止派工', report['error'])
            worker.assert_not_called()
            listing.assert_called_once()
            listing.reset_mock()
            harness._record_quota_exhausted('agy', info, 'usage limit')
            code, report = self.capture(harness.cmd_task, args)
            self.assertEqual(code, 5)
            self.assertTrue(report['preflight_blocked'])
            listing.assert_not_called()
            worker.assert_not_called()

    def test_agy_listing_quota_precedes_auth_fallback_and_scopes_by_execution_side(self):
        engines = backends()
        native = engines['agy']
        native.update(config_home=str(self.root), execution_side='posix', model_catalog={'freshness': 'stale'})
        windows = {**native, 'execution_side': 'windows', 'windows_interop': True}
        response = subprocess.CompletedProcess(['agy', 'models'], 1, '', 'Please sign in; resource_exhausted')
        with patch.object(harness, '_run_agy_listing', return_value=response), \
             patch.object(harness, '_fallback_agy_info') as fallback, self.assertRaises(harness.HarnessError) as caught:
            harness._refresh_agy_listing(native, harness._QuotaBatch(), refresh_model_cache=True)
        fallback.assert_not_called()
        self.assertEqual(caught.exception.status, 'quota_exhausted')
        self.assertEqual(caught.exception.details['quota_execution_side'], 'posix')
        harness._enforce_preflight_quota({'agy': windows}, ('agy:default',))
        self.assertFalse(harness._quota_state_path('agy', windows).exists())
        with self.assertRaises(harness.HarnessError):
            harness._enforce_preflight_quota(engines, ('agy:default',))

    def test_agy_auth_fallback_checks_windows_cooldown_before_listing(self):
        engines = backends()
        native = engines['agy']
        native.update(config_home=str(self.root / 'native'), execution_side='posix')
        windows = {**native, 'config_home': str(self.root / 'windows'), 'execution_side': 'windows', 'windows_interop': True}
        harness._record_quota_exhausted('agy', windows, 'usage limit')
        response = subprocess.CompletedProcess(['agy', 'models'], 1, '', 'Please sign in to view available models.')
        with patch.object(harness, '_run_agy_listing', return_value=response) as listing, \
             patch.object(harness, '_fallback_agy_info', return_value=windows), self.assertRaises(harness.HarnessError) as caught:
            harness._refresh_agy_listing(native, harness._QuotaBatch(), refresh_model_cache=True)
        listing.assert_called_once_with(native)
        self.assertTrue(caught.exception.details['preflight_blocked'])
        self.assertEqual(caught.exception.details['quota_execution_side'], 'windows')

    def test_unknown_and_nonpositive_arguments_fail(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args('task', '--kind', 'math', '--target', '.', '--task', 'x', '--timeout', '0')
        with self.assertRaises(harness.HarnessError):
            harness._scope_patterns(['../outside'])
        audit_p = harness._build_audit_prompt({'files': ['a.py']}, '守恒')
        self.assertIn('git worktree add', audit_p)
        captured_prompts = []
        harness.run_physics_gate_loop(self.root, '修通量', 'exit 0', lambda p, _n: (captured_prompts.append(p), '{}')[1],
                                      verify_fn=lambda _c, _d: (0, 'ok'), max_rounds=1)
        self.assertIn('git worktree add', captured_prompts[0])

    def test_codex_multigen_alias_agy_generation_priority_and_math_cues(self):
        engines = backends()
        engines['codex']['models'] = [
            {'slug': 'gpt-6.1-sol', 'display_name': 'GPT-6.1-Sol', 'provider': 'openai', 'priority': 1,
             'context_window': 500000, 'max_context_window': 872000, 'effective_context_window_percent': 95,
             'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']},
            {'slug': 'gpt-6-astra', 'display_name': 'GPT-6-Astra', 'provider': 'openai', 'priority': 2,
             'description': 'Frontier intelligence for the most demanding work.',
             'context_window': 272000, 'max_context_window': 872000, 'effective_context_window_percent': 95,
             'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']},
            {'slug': 'gpt-6-sol', 'display_name': 'GPT-6-Sol', 'provider': 'openai', 'priority': 5,
             'context_window': 400000, 'max_context_window': 872000, 'supported_efforts': ['high', 'max']},
            {'slug': 'gpt-5.6-sol', 'display_name': 'GPT-5.6-Sol', 'provider': 'openai', 'priority': 9,
             'context_window': 272000, 'supported_efforts': ['high']},
        ]
        sol = harness.resolve_execution_profile(engines, 'codex:sol', 'max', '实现牛顿法线性化求解器', 'code')
        self.assertEqual(sol['model'], 'gpt-6.1-sol')
        self.assertEqual(sol['reasoning_effort'], 'max')
        self.assertEqual(sol['capacity']['cli_context_window_tokens'], 872000)

        tier, reason = harness.select_task_tier('auto', 'code', '请调用 Codex 模型核对偏微分方程数学公式与牛顿法通量离散')
        self.assertEqual((tier, reason), ('max', 'complexity_high_domain_max'))
        astra = harness.resolve_execution_profile(engines, 'codex:default', tier, '请调用 Codex 模型推导控制方程特征值', 'math')
        self.assertEqual(astra['model'], 'gpt-6-astra')
        self.assertEqual(astra['reasoning_effort'], 'max')
        self.assertEqual(astra['capacity']['cli_context_window_tokens'], 872000)

        engines['agy']['models'] = [
            {'slug': 'gemini-4-carbon-high'},
            {'slug': 'gemini-3-deep-xhigh'},
        ]
        agy_prof = harness.resolve_execution_profile(engines, 'agy:default', 'max', '推导守恒律', 'math')
        self.assertEqual(agy_prof['model'], 'gemini-4-carbon-high')

    def test_agy_stream_tool_output_quota_phrase_does_not_false_trip(self):
        engines = backends()
        engines['agy']['config_home'] = str(self.root / 'agy-home')
        stream_out = '\n'.join([
            json.dumps({'type': 'init', 'model': 'gemini-7-pro-high'}),
            json.dumps({'type': 'tool_result', 'content': 'Read file says: usage limit reached in fixture doc'}),
            json.dumps({'type': 'error', 'message': 'upstream connection reset'}),
        ])
        proc = subprocess.CompletedProcess(['agy'], 1, stream_out, 'upstream connection reset')
        with patch.object(harness, 'run_process', return_value=proc), self.assertRaises(harness.HarnessError) as caught:
            harness.invoke_engine('agy:default', self.root, '检查文档', engines, harness.TASK_JSON_SCHEMA)
        self.assertEqual(caught.exception.status, 'failed')
        self.assertIsNone(harness._load_active_quota_cooldown('agy', engines['agy']))

    def test_fallback_on_quota_switches_to_local_matched_model_safely(self):
        proj = self.root / 'proj'
        proj.mkdir()
        target = proj / 'solver.py'
        target.write_text('x = 1\n', encoding='utf-8')
        engines = backends()
        engines['codex'].update(config_home=str(self.root / 'codex-home'), models=[
            {'slug': 'gpt-6-astra', 'provider': 'openai', 'description': 'Frontier intelligence for the most demanding work.',
             'context_window': 272000, 'max_context_window': 872000, 'supported_efforts': ['high', 'max']},
        ])
        engines['agy'].update(config_home=str(self.root / 'agy-home'), models=[{'slug': 'gemini-4-carbon-high'}])

        def fake_invoke(spec_name, work_dir, prompt, bk, schema, readonly=True, *a, **kw):
            prefix, _ = harness._engine_parts(spec_name)
            if prefix == 'codex':
                rec = harness._record_quota_exhausted('codex', bk['codex'], 'You have hit your usage limit', batch=kw.get('batch'))
                raise harness.HarnessError('codex 触发额度上限', 'quota_exhausted', **harness._quota_user_choices('codex', rec))
            kw.get('receipt', {})['invocation_started'] = True
            return dict(TASK)

        with patch.object(harness, '_args_backends', return_value=engines), patch.object(harness, 'invoke_engine', side_effect=fake_invoke):
            code, out = self.capture(harness.cmd_task, self.args(
                'task', '--caller', 'antigravity', '--engine', 'codex:default', '--kind', 'math',
                '--target', str(target), '--task', '推导数学公式', '--fallback-on-quota'))
        self.assertEqual(code, 0)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['engine'], 'agy:default')
        self.assertEqual(out['model'], 'gemini-4-carbon-high')
        self.assertEqual(out['quota_fallback']['from_engine'], 'codex:default')
        self.assertEqual(out['quota_fallback']['to_engine'], 'agy:default')
        self.assertFalse(out['quota_fallback']['preflight_blocked'])
        self.assertIsNotNone(harness._load_active_quota_cooldown('codex', engines['codex']))

        # Cooldown is now persisted: preflight fallback in gate and audit also switches cleanly to agy
        def fake_edit_worker(spec_name, work_dir, *a, **kw):
            self.assertTrue(spec_name.startswith('agy:'))
            kw.get('receipt', {})['invocation_started'] = True
            target.write_text('x = 2\n', encoding='utf-8')
            return 'updated'

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'invoke_edit_worker', side_effect=fake_edit_worker):
            g_code, g_out = self.capture(harness.cmd_gate, self.args(
                'gate', '--caller', 'antigravity', '--worker', 'codex:default',
                '--target', str(target), '--task', '更新公式实现', '--verify-cmd', f'"{sys.executable}" -c "import sys; sys.exit(0)"',
                '--fallback-on-quota'))
        self.assertEqual(g_code, 0)
        self.assertEqual(g_out['engine'], 'agy:default')
        self.assertTrue(g_out['quota_fallback']['preflight_blocked'])

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'invoke_readonly_engine', return_value={'summary': 'ok', 'findings': [], 'read_files': ['solver.py'], 'unread_files': []}):
            a_code, a_out = self.capture(harness.cmd_audit, self.args(
                'audit', '--caller', 'antigravity', '--target', str(proj), '--fallback-on-quota'))
        self.assertEqual(a_code, 0)
        self.assertEqual(a_out['plan']['left_engine'], 'agy:default')
        self.assertFalse(a_out['plan']['dual_blind'])
        self.assertEqual(a_out['quota_fallback']['to_engine'], 'agy:default')

        # If worker already modified workspace before hitting quota_exhausted, runtime fallback MUST NOT rerun writes
        harness._SESSION_QUOTA_BLOCKED.clear()
        engines2 = backends()
        engines2['codex']['config_home'] = str(self.root / 'codex-home-2')
        engines2['agy']['config_home'] = str(self.root / 'agy-home-2')

        def dirty_codex(spec_name, work_dir, *a, **kw):
            target.write_text('dirty = True\n', encoding='utf-8')
            rec = harness._record_quota_exhausted('codex', engines2['codex'], 'usage limit', batch=kw.get('batch'))
            raise harness.HarnessError('codex 触发额度上限', 'quota_exhausted', **harness._quota_user_choices('codex', rec))

        with patch.object(harness, '_args_backends', return_value=engines2), patch.object(harness, 'invoke_engine', side_effect=dirty_codex):
            d_code, d_out = self.capture(harness.cmd_task, self.args(
                'task', '--caller', 'antigravity', '--engine', 'codex:default', '--kind', 'code',
                '--target', str(target), '--task', '修改代码', '--fallback-on-quota'))
        self.assertEqual(d_code, harness.EXIT_CODES['quota_exhausted'])
        self.assertEqual(d_out['status'], 'quota_exhausted')
        self.assertNotIn('quota_fallback', d_out)

    def test_default_flagship_max_economy_fallback_and_main_model_independence(self):
        # 1. 反重力原生只读子代理定义不含硬编码 model 字段，保持 inherit 跟随主对话
        agents_dir = ROOT / 'template' / 'user-guard' / 'agents'
        for agent_md in sorted(agents_dir.glob('readonly-*.md')):
            frontmatter = agent_md.read_text(encoding='utf-8').split('---', 2)[1]
            self.assertNotRegex(frontmatter, r'(?m)^\s*model\s*:')

        # 2. 不管反重力前端或 Codex 前端当前选什么主控模型，都不影响 cae-harness 选模、挡位与最大窗口
        base_codex_models = [
            {'slug': 'gpt-6.1-sol', 'provider': 'openai', 'priority': 1,
             'context_window': 500000, 'max_context_window': 872000,
             'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'], 'delegating_efforts': ['ultra']},
            {'slug': 'gpt-6-astra', 'provider': 'openai', 'priority': 2,
             'description': 'Frontier intelligence for the most demanding work.',
             'context_window': 272000, 'max_context_window': 872000,
             'supported_efforts': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'], 'delegating_efforts': ['ultra']},
            {'slug': 'gpt-6-luna', 'provider': 'openai', 'priority': 3,
             'description': 'Fast and affordable for easier tasks.',
             'context_window': 272000, 'max_context_window': 872000,
             'supported_efforts': ['low', 'medium', 'high', 'max']},
        ]
        base_agy_models = [
            {'slug': 'gemini-4-carbon-high', 'display_name': 'Gemini 4 Carbon (High)'},
            {'slug': 'gemini-4-carbon-medium', 'display_name': 'Gemini 4 Carbon (Medium)'},
            {'slug': 'gemini-4-hydrogen-low', 'display_name': 'Gemini 4 Hydrogen (Low)'},
            {'slug': 'gemini-pro-orchestrator', 'display_name': 'Orchestrator (Carbon)'},
        ]
        for frontend_agy_model in ('gemini-pro-orchestrator', 'gemini-4-hydrogen-low', 'claude-sonnet-4', None):
            for frontend_codex_model in ('gpt-6-luna', 'custom-router', None):
                engines = backends()
                engines['codex'].update(model=frontend_codex_model, reasoning_effort='low', models=base_codex_models)
                engines['agy'].update(model=frontend_agy_model, models=base_agy_models)
                tier_math, _ = harness.select_task_tier('auto', 'math', '推导高阶格式')
                tier_code, _ = harness.select_task_tier('auto', 'code', '编写常规辅助函数')
                tier_typo, _ = harness.select_task_tier('auto', 'review', '检查注释 typo')
                self.assertEqual((tier_math, tier_code, tier_typo), ('max', 'max', 'max'))
                p_math = harness.resolve_execution_profile(engines, 'codex:default', tier_math, '推导高阶格式', 'math')
                p_code = harness.resolve_execution_profile(engines, 'codex:default', tier_code, '编写常规辅助函数', 'code')
                p_typo = harness.resolve_execution_profile(engines, 'codex:default', tier_typo, '检查注释 typo', 'review')
                p_agy = harness.resolve_execution_profile(engines, 'agy:default', tier_code, '编写常规辅助函数', 'code')
                self.assertEqual((p_math['model'], p_math['reasoning_effort'], p_math['capacity']['cli_context_window_tokens']),
                                 ('gpt-6-astra', 'max', 872000))
                self.assertEqual((p_code['model'], p_code['reasoning_effort'], p_code['capacity']['cli_context_window_tokens']),
                                 ('gpt-6.1-sol', 'max', 872000))
                self.assertEqual((p_typo['model'], p_typo['reasoning_effort']), ('gpt-6.1-sol', 'max'))
                self.assertEqual((p_agy['model'], p_agy['reasoning_effort']), ('gemini-4-carbon-high', 'high'))

        # 3. 模糊或明确要求降挡／节省 token 时切换选项 B 分级省流；显式点名挡位严格照办
        for cue in ('省token整理逻辑', '节省 token 审查接口', '降档修改配置', '适当降挡核查日志',
                    '要不省点 token 整理逻辑', '能不能降档修改配置', 'save tokens review module'):
            self.assertEqual(harness.select_task_tier('auto', 'code', cue), ('high', 'economy_adaptive_high'))
        for surface_cue in ('省token检查错别字', '降档检查 typo', '省点额度做格式微调',
                            '能不能降档检查 typo', '轻量快速扫一下注释'):
            self.assertEqual(harness.select_task_tier('auto', 'review', surface_cue), ('low', 'economy_trivial_surface_low'))
        self.assertEqual(harness.select_task_tier('auto', 'math', '降档推导离散格式'), ('high', 'economy_adaptive_high'))
        self.assertEqual(harness.select_task_tier('auto', 'code', '用中档整理逻辑'), ('medium', 'user_description_medium'))
        self.assertEqual(harness.select_task_tier('auto', 'review', '不省token，不降档，绝不省额度，检查 typo'), ('max', 'default_flagship_max'))

    def test_agy_print_timeout_exit_code_and_terminal_statuses(self):
        # 1) agy --print-timeout 在 -p 模式退出码 3 或 stream-json 穿插 AGY_ERROR 行时须精确归类为 timeout
        engines = backends()
        observed_timeouts = []

        def fake_timeout_exit3(command, cwd, input_text, timeout_sec, **kwargs):
            observed_timeouts.append(timeout_sec)
            stderr = (
                '[agy] print timeout after 30s\n'
                'AGY_ERROR: {"short_error":"print timeout after 30s","status":"DEADLINE_EXCEEDED","reason":"print_timeout"}\n'
            )
            return subprocess.CompletedProcess(command, 3, '', stderr)

        with patch.object(harness, 'run_process', side_effect=fake_timeout_exit3):
            with self.assertRaises(harness.HarnessError) as ctx:
                harness.invoke_readonly_engine('agy:default', self.root, '审查通量', engines, 30, [])
        self.assertEqual(ctx.exception.status, 'timeout')
        self.assertEqual(ctx.exception.details.get('finished_reason'), 'print_timeout')
        # 外层 run_process 超时在 agy --print-timeout 基础上预留 15s 缓冲窗口
        self.assertEqual(observed_timeouts, [45])

        # 2) NDJSON 中夹带 AGY_ERROR / [agy] print timeout 诊断行且退出码为 0 时，_decode_output_json 同样归类为 timeout
        ndjson_timeout = (
            '{"event":"init","session_id":"s1"}\n'
            '[agy] print timeout after 30s\n'
            'AGY_ERROR: {"short_error":"print timeout after 30s","status":"DEADLINE_EXCEEDED","reason":"print_timeout"}\n'
        )
        with self.assertRaises(harness.HarnessError) as ctx_nd:
            harness._decode_output_json(ndjson_timeout)
        self.assertEqual(ctx_nd.exception.status, 'timeout')

        # 3) 官方 CLI Headless 规范中的 INVALID / WAITING / RUNNING 非正常终态须拦截为 failed（覆盖单行 JSON 与多行 NDJSON）
        for bad_status in ('INVALID', 'WAITING', 'RUNNING'):
            single_json = json.dumps({'event': 'result', 'status': bad_status, 'structured_output': {'summary': 'x'}})
            with self.assertRaises(harness.HarnessError) as ctx_single:
                harness._extract_json_object(single_json)
            self.assertEqual(ctx_single.exception.status, 'failed', bad_status)

            ndjson_stream = '{"event":"init","session_id":"s1"}\n' + single_json + '\n'
            with self.assertRaises(harness.HarnessError) as ctx_st:
                harness._decode_output_json(ndjson_stream)
            self.assertEqual(ctx_st.exception.status, 'failed', bad_status)

    def test_zero_exit_stderr_terminal_diagnostics_override_result(self):
        output = json.dumps({'event': 'result', 'structured_output': TASK})
        cases = (
            ('AGY_ERROR: {"short_error":"connection reset"}', 'failed'),
            ('AGY_ERROR: {"short_error":"usage limit reached"}', 'quota_exhausted'),
            ('AGY_ERROR: {"short_error":"print timeout after 30s","reason":"print_timeout"}', 'timeout'),
            ('[agy] print timeout after 30s', 'timeout'),
            ('error: print timeout after 30s', 'timeout'),
        )
        for index, (stderr, expected) in enumerate(cases):
            engines = backends()
            engines['agy']['config_home'] = str(self.root / ('agy-' + str(index)))
            with self.subTest(stderr=stderr), patch.object(harness, 'run_process', return_value=subprocess.CompletedProcess([], 0, output, stderr)):
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.invoke_engine('agy:default', self.root, 'review', engines, harness.TASK_JSON_SCHEMA, timeout_sec=30)
                self.assertEqual(caught.exception.status, expected)
                self.assertEqual(harness._load_active_quota_cooldown('agy', engines['agy']) is not None, expected == 'quota_exhausted')
        engines = backends()
        engines['agy']['config_home'] = str(self.root / 'warning-only')
        for stderr in ('warning: optional cache missing', 'warning: report discusses usage limit and print timeout after 30s'):
            with self.subTest(stderr=stderr), patch.object(harness, 'run_process', return_value=subprocess.CompletedProcess([], 0, output, stderr)):
                self.assertEqual(harness.invoke_engine('agy:default', self.root, 'review', engines, harness.TASK_JSON_SCHEMA), TASK)
                self.assertIsNone(harness._load_active_quota_cooldown('agy', engines['agy']))

    def test_agy_error_and_success_result_cannot_cancel_each_other(self):
        output = 'AGY_ERROR: {"short_error":"connection reset"}\n' + json.dumps({'event': 'result', 'structured_output': TASK})
        with self.assertRaises(harness.HarnessError) as caught:
            harness._decode_output_json(output)
        self.assertEqual(caught.exception.status, 'failed')

    def test_failure_diagnostics_exclude_result_business_content(self):
        body = {**TASK, 'content': 'The report discusses usage limit reached and print timeout after 30s.'}
        for status in ('success', 'failed'):
            engines = backends()
            engines['agy']['config_home'] = str(self.root / status)
            event = {'event': 'result', 'status': status, 'message': 'upstream connection reset',
                     'structured_output': body, 'result': body, 'content': body}
            proc = subprocess.CompletedProcess([], 1, json.dumps(event), 'upstream connection reset')
            detail = harness._backend_diagnostics(proc)
            self.assertNotIn('usage limit', detail)
            self.assertNotIn('print timeout after', detail)
            with self.subTest(status=status), patch.object(harness, 'run_process', return_value=proc), self.assertRaises(harness.HarnessError) as caught:
                harness.invoke_engine('agy:default', self.root, 'review', engines, harness.TASK_JSON_SCHEMA)
            self.assertEqual(caught.exception.status, 'failed')
            self.assertIsNone(harness._load_active_quota_cooldown('agy', engines['agy']))

    def test_blank_verify_commands_rejected_before_cli_preflight(self):
        for command in ('', '   ', '\t\r\n'):
            for subcommand in ('gate', 'task'):
                for dry_run in (False, True):
                    tokens = [subcommand, '--target', str(self.root), '--task', '修改代码', '--verify-cmd', command]
                    if subcommand == 'task':
                        tokens.extend(['--kind', 'code'])
                    if dry_run:
                        tokens.append('--dry-run')
                    handler = harness.cmd_gate if subcommand == 'gate' else harness.cmd_task
                    with self.subTest(command=command, subcommand=subcommand, dry_run=dry_run), \
                         patch.object(harness, '_target') as target, \
                         patch.object(harness, '_args_backends') as probe, \
                         patch.object(harness, 'run_process') as runner:
                        with self.assertRaisesRegex(harness.HarnessError, '^--verify-cmd 不能为空白命令$'):
                            handler(self.args(*tokens))
                        target.assert_not_called()
                        probe.assert_not_called()
                        runner.assert_not_called()

    def test_blank_verify_commands_rejected_before_gate_worker(self):
        for command in ('', '   ', '\t\n'):
            worker, verifier = Mock(), Mock(return_value=(0, 'ok'))
            with self.subTest(command=command), patch.object(harness, 'snapshot_files') as snapshot:
                with self.assertRaisesRegex(harness.HarnessError, '^--verify-cmd 不能为空白命令$'):
                    harness.run_physics_gate_loop(self.root, '修改代码', command, worker, verifier)
                snapshot.assert_not_called()
            worker.assert_not_called()
            verifier.assert_not_called()

    def test_special_files_rejected_by_snapshot_and_skipped_by_collection(self):
        special = self.root / 'special.py'
        special.write_text('placeholder', encoding='utf-8')
        real_lstat = Path.lstat
        for mode in (stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK):
            fake_stat = os.stat_result((mode | 0o600, 0, 0, 1, 0, 0, 1024, 0, 0, 0))

            def lstat(path, *args, **kwargs):
                return fake_stat if path == special else real_lstat(path, *args, **kwargs)

            with self.subTest(mode=mode), patch.object(Path, 'lstat', autospec=True, side_effect=lstat) as stats, \
                 patch.object(Path, 'open', side_effect=AssertionError('特殊文件不得打开')) as open_file:
                with self.assertRaisesRegex(harness.HarnessError, '工作区包含非常规特殊文件，无法安全快照: special.py') as caught:
                    harness.snapshot_files(self.root)
                self.assertEqual(caught.exception.details['snapshot_path'], 'special.py')
                self.assertEqual(sum(call.args[0] == special for call in stats.call_args_list), 1)
                skipped = []
                self.assertEqual(harness.collect_target_files(self.root, skipped), [])
                self.assertEqual(skipped, [{'file': 'special.py', 'reason': 'special_file'}])
                open_file.assert_not_called()

    def test_snapshot_reuses_one_lstat_for_regular_files_and_reparse_points(self):
        source = self.root / 'a.py'
        data = b'value = 1\n'
        source.write_bytes(data)
        real_lstat = Path.lstat
        for reparse in (False, True):
            fake_stat = Mock(st_mode=stat.S_IFREG | 0o640,
                             st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT if reparse else 0)

            def lstat(path, *args, **kwargs):
                return fake_stat if path == source else real_lstat(path, *args, **kwargs)

            with self.subTest(reparse=reparse), patch.object(Path, 'lstat', autospec=True, side_effect=lstat) as stats, \
                 patch.object(harness.os, 'readlink', return_value='destination') as readlink:
                report = harness.snapshot_files(self.root)
                self.assertEqual(sum(call.args[0] == source for call in stats.call_args_list), 1)
                if reparse:
                    self.assertEqual(report['a.py'], 'symlink:destination')
                    readlink.assert_called_once_with(source)
                else:
                    self.assertEqual(report['a.py'], harness.hashlib.sha256(data).hexdigest() + ':' + str(0o640))
                    readlink.assert_not_called()

    def test_assess_preserves_expired_events_and_acknowledgements_even_with_discovery(self):
        homes = {name: self.root / name for name in ('codex', 'agy')}
        for name, home in homes.items():
            info = {'config_home': str(home), 'windows_interop': False}
            events = harness._quota_events_dir(harness._quota_state_path(name, info))
            (events / 'acknowledged').mkdir(parents=True)
            expired = {'backend': name, 'execution_side': harness._execution_side(info),
                       'exhausted': True, 'cooldown_until_epoch': 1.0}
            contents = json.dumps(expired, indent=2) + '\n'
            (events / ('a' * 32 + '.json')).write_text(contents, encoding='utf-8')
            (events / 'acknowledged' / harness._quota_ack_name(expired)).write_text(contents, encoding='utf-8')
        (homes['codex'] / 'models_cache.json').write_text(json.dumps({'models': backends()['codex']['models']}), encoding='utf-8')
        cache = homes['agy'] / 'antigravity-cli' / 'cache' / 'cae_models_cache.json'
        cache.write_text('{"models": [{"slug": "cached-high"}]}', encoding='utf-8')
        before = harness.snapshot_files(self.root)
        for discover in (False, True):
            response = subprocess.CompletedProcess(['agy', 'models'], 0, 'gemini-7-pro-high\tGemini Pro (High)\n', '')
            tokens = ['assess', '--caller', 'codex'] + (['--discover-models'] if discover else [])
            with self.subTest(discover=discover), patch.dict(os.environ, {}, clear=True), \
                 patch.object(harness, '_find_binary', side_effect=lambda name: 'fixture-' + name), \
                 patch.object(harness, '_codex_home', return_value=(homes['codex'], False)), \
                 patch.object(harness, '_agy_home', return_value=(homes['agy'], False)), \
                 patch.object(harness, '_run_agy_listing', return_value=response) as listing, \
                 patch.object(Path, 'unlink', side_effect=AssertionError('assess 不得删除缓存')) as unlink:
                code, report = self.capture(harness.cmd_assess, self.args(*tokens))
                unlink.assert_not_called()
            self.assertEqual(code, 0)
            self.assertEqual(listing.call_count, int(discover))
            self.assertEqual(harness.snapshot_files(self.root), before)
            self.assertTrue(all(info['quota_cooldown'] is None for info in report['backends'].values()))

    def test_audit_snapshot_failure_retains_completed_units_and_all_findings(self):
        (self.root / 'a.py').write_text('x = 1\n' * 50, encoding='utf-8')
        common = {'file': 'a.py', 'line_start': 2, 'line_end': 2, 'severity': 'high',
                  'category': 'conservation', 'description': '守恒残差异常', 'evidence': 'residual != 0'}
        single = {**common, 'line_start': 40, 'line_end': 40, 'description': '边界通量异常'}

        def audit(engine, *args, **kwargs):
            return {**AUDIT, 'summary': engine, 'findings': [common, single] if engine.startswith('codex:') else [common]}

        args = self.args('audit', '--target', str(self.root), '--caller', 'antigravity',
                         '--left', 'codex:default', '--right', 'agy:default')
        denied = harness.HarnessError('事后文件不可读', snapshot_path='a.py')
        with patch.object(harness, '_args_backends', return_value=backends()), \
             patch.object(harness, 'invoke_readonly_engine', side_effect=audit), \
             patch.object(harness, 'snapshot_files', side_effect=[{}, denied]) as snapshot:
            code, report = self.capture(harness.cmd_audit, args)
        self.assertEqual(code, 1)
        self.assertEqual(snapshot.call_count, 2)
        self.assertEqual(report['status'], 'failed')
        self.assertIs(report['scope_verified'], False)
        self.assertEqual(report['error'], '事后文件不可读')
        self.assertEqual(report['scope_error_path'], 'a.py')
        self.assertEqual(report['completed_count'], 2)
        self.assertEqual(report['received_count'], 2)
        self.assertEqual(len(report['completed_units']), 2)
        self.assertTrue(all(unit['read_files'] == ['a.py'] and not unit['unread_files'] for unit in report['completed_units']))
        self.assertEqual(report['consensus_count'], 1)
        self.assertEqual(report['single_source_count'], 1)
        self.assertEqual(report['consensus_findings'][0]['left_finding'], common)
        self.assertEqual(report['consensus_findings'][0]['right_finding'], common)
        self.assertEqual(report['single_source_findings'][0]['description'], single['description'])

    def test_audit_snapshot_failure_preserves_terminal_failure_statuses(self):
        (self.root / 'a.py').write_text('x = 1\n', encoding='utf-8')
        args = self.args('audit', '--target', str(self.root), '--caller', 'antigravity', '--single')
        for status in ('quota_exhausted', 'cancelled', 'scope_violation'):
            with self.subTest(status=status), patch.object(harness, '_args_backends', return_value=backends()), \
                 patch.object(harness, 'invoke_readonly_engine', side_effect=harness.HarnessError('worker stopped', status)), \
                 patch.object(harness, 'snapshot_files', side_effect=[{}, PermissionError('snapshot denied')]):
                code, report = self.capture(harness.cmd_audit, args)
            self.assertEqual(code, harness.EXIT_CODES[status])
            self.assertEqual(report['status'], status)
            self.assertIs(report['scope_verified'], False)
            self.assertEqual(report['error'], 'snapshot denied')

    def test_preflight_fallback_stops_when_second_preparation_changes_workspace(self):
        for subcommand in ('audit', 'gate', 'task'):
            workspace = self.root / subcommand
            workspace.mkdir()
            (workspace / 'a.py').write_text('x = 1\n', encoding='utf-8')
            calls = []

            def prepare(engines, specs, batch, **kwargs):
                calls.append(specs)
                if len(calls) == 1:
                    raise harness.HarnessError('quota', 'quota_exhausted', quota_backend='codex', preflight_blocked=True)
                (workspace / 'unexpected.py').write_text('changed during refresh', encoding='utf-8')

            tokens = [subcommand, '--target', str(workspace), '--caller', 'antigravity', '--fallback-on-quota']
            if subcommand != 'audit':
                tokens.extend(['--task', '修改代码', '--verify-cmd', 'verify'])
            if subcommand == 'task':
                tokens.extend(['--kind', 'code'])
            handler = {'audit': harness.cmd_audit, 'gate': harness.cmd_gate, 'task': harness.cmd_task}[subcommand]
            with self.subTest(subcommand=subcommand), patch.object(harness, '_args_backends', return_value=backends()), \
                 patch.object(harness, '_prepare_execution', side_effect=prepare), \
                 patch.object(harness, 'invoke_readonly_engine') as auditor, \
                 patch.object(harness, 'invoke_edit_worker') as worker, \
                 patch.object(harness, 'invoke_engine') as invoke, patch.object(harness, 'run_process') as runner:
                code, report = self.capture(handler, self.args(*tokens))
            self.assertEqual(code, 1)
            self.assertEqual(report['status'], 'failed')
            self.assertIn('回退预检后工作区发生变化或无法核对', report['error'])
            self.assertEqual(len(calls), 2)
            self.assertTrue(any(spec and spec.startswith('agy:') for spec in calls[1]))
            auditor.assert_not_called()
            worker.assert_not_called()
            invoke.assert_not_called()
            runner.assert_not_called()
            if subcommand != 'audit':
                self.assertIs(report['verification_executed'], False)

    def test_windows_env_validates_names_before_querying_and_prefers_powershell(self):
        for name in ('', '1BAD', 'TEMP&whoami', 'TEMP|echo', "TEMP'", 'TEMP\n'):
            with self.subTest(name=name), patch.object(harness.shutil, 'which') as which, \
                 patch.object(harness, '_local_command') as query:
                with self.assertRaises(harness.HarnessError):
                    harness._windows_env(name)
                which.assert_not_called()
                query.assert_not_called()
        powershell = str(self.root / 'powershell.exe')
        for value in ('C:\\folder & name|literal%value%', '%literal-start'):
            with self.subTest(value=value), patch.object(harness.shutil, 'which', return_value=powershell) as which, \
                 patch.object(harness, '_local_command', return_value=value) as query:
                self.assertEqual(harness._windows_env('TEMP'), value)
                which.assert_called_once_with('powershell.exe')
                query.assert_called_once_with([powershell, '-NoProfile', '-NonInteractive', '-Command',
                                               "[Environment]::GetEnvironmentVariable('TEMP')"], cwd=str(self.root))

    def test_windows_env_cmd_fallback_and_undefined_values(self):
        cmd_exe = str(self.root / 'cmd.exe')
        for value in ('C:\\temp', '%TEMP%', ''):
            with self.subTest(value=value), \
                 patch.object(harness.shutil, 'which', side_effect=lambda name: cmd_exe if name == 'cmd.exe' else None), \
                 patch.object(harness, '_local_command', return_value=value) as query:
                if value == 'C:\\temp':
                    self.assertEqual(harness._windows_env('TEMP'), value)
                else:
                    with self.assertRaises(harness.HarnessError):
                        harness._windows_env('TEMP')
                query.assert_called_once_with([cmd_exe, '/d', '/c', 'echo', '%TEMP%'], cwd=str(self.root))
        with patch.object(harness.shutil, 'which', return_value='powershell.exe'), \
             patch.object(harness, '_local_command', return_value=''), self.assertRaises(harness.HarnessError):
            harness._windows_env('UNDEFINED')

    def test_nested_structured_quota_errors_are_detected_for_all_error_events(self):
        error = {'message': 'usage limit reached', 'code': 'insufficient_quota'}
        events = (
            {'event': 'result', 'result': {'status': 'ERROR', 'error': error}},
            {'event': 'error', 'result': {'error': error}},
            {'type': 'turn.failed', 'error': {'result': error}},
        )
        for index, event in enumerate(events):
            engines = backends()
            engines['agy']['config_home'] = str(self.root / ('quota-' + str(index)))
            proc = subprocess.CompletedProcess([], 1, json.dumps({'event': 'init'}) + '\n' + json.dumps(event), 'warning: optional cache missing')
            detail = harness._backend_diagnostics(proc)
            self.assertIn('usage limit reached', detail)
            self.assertIn('insufficient_quota', detail)
            with self.subTest(event=event), patch.object(harness, 'run_process', return_value=proc), \
                 self.assertRaises(harness.HarnessError) as caught:
                harness.invoke_engine('agy:default', self.root, '审查', engines, harness.TASK_JSON_SCHEMA)
            self.assertEqual(caught.exception.status, 'quota_exhausted')
            self.assertIsNotNone(harness._load_active_quota_cooldown('agy', engines['agy'], cleanup_expired=False))

    def test_structured_nonquota_errors_override_unrelated_quota_warning_text(self):
        for kind in ('error', 'result', 'turn.failed'):
            event = {'event': kind, 'result': {'status': 'ERROR', 'error': {'message': 'connection reset'},
                                             'structured_output': {**TASK, 'content': 'usage limit reached'}}}
            output = 'report mentions usage limit reached\n' + json.dumps(event)
            proc = subprocess.CompletedProcess([], 1, output, 'warning: documentation mentions usage limit')
            with self.subTest(kind=kind), patch.object(harness, 'run_process', return_value=proc), \
                 patch.object(harness, '_record_quota_exhausted') as record, self.assertRaises(harness.HarnessError) as caught:
                harness.invoke_engine('agy:default', self.root, '审查', backends(), harness.TASK_JSON_SCHEMA)
            self.assertEqual(caught.exception.status, 'failed')
            record.assert_not_called()
        for index, (stdout, stderr) in enumerate((('', 'usage limit reached'), ('usage limit reached', ''))):
            proc = subprocess.CompletedProcess([], 1, stdout, stderr)
            engines = backends()
            engines['agy']['config_home'] = str(self.root / ('unstructured-' + str(index)))
            with self.subTest(stdout=stdout, stderr=stderr), patch.object(harness, 'run_process', return_value=proc), \
                 self.assertRaises(harness.HarnessError) as caught:
                harness.invoke_engine('agy:default', self.root, '审查', engines, harness.TASK_JSON_SCHEMA)
            self.assertEqual(caught.exception.status, 'quota_exhausted')
            self.assertIs(caught.exception.details['preflight_blocked'], False)

    def test_code_task_without_verify_command_explicitly_reports_unverified(self):
        args = self.args('task', '--target', str(self.root), '--caller', 'antigravity', '--kind', 'code', '--task', '修改代码')
        with patch.object(harness, '_args_backends', return_value=backends()), \
             patch.object(harness, 'invoke_engine', return_value=TASK) as worker, \
             patch.object(harness, 'run_process') as verifier:
            code, report = self.capture(harness.cmd_task, args)
        self.assertEqual(code, 0)
        self.assertEqual(report['result'], TASK)
        self.assertIs(report['verification_executed'], False)
        self.assertEqual(report['verification_notice'], '未提供 --verify-cmd，本次代码修改未经宿主机命令实测验证，请总控运行验证后再验收。')
        worker.assert_called_once()
        verifier.assert_not_called()

    def test_readonly_tasks_do_not_receive_code_gate_notices(self):
        for kind in ('math', 'direction', 'review', 'polish'):
            args = self.args('task', '--target', str(self.root), '--caller', 'antigravity', '--kind', kind, '--task', '只读核对')
            with self.subTest(kind=kind), patch.object(harness, '_args_backends', return_value=backends()), \
                 patch.object(harness, 'invoke_engine', return_value=TASK):
                code, report = self.capture(harness.cmd_task, args)
            self.assertEqual(code, 0)
            self.assertNotIn('verification_executed', report)
            self.assertNotIn('verification_notice', report)

    def test_gate_and_code_task_report_real_verifier_execution_on_success_and_failure(self):
        for subcommand in ('gate', 'task'):
            for exit_code in (0, 1):
                marker = self.root / ('verified_' + subcommand + str(exit_code) + '.txt')
                command = (f'"{sys.executable}" -B -c "from pathlib import Path; '
                           f"Path('{marker.name}').write_text('executed', encoding='utf-8'); raise SystemExit({exit_code})\"")
                tokens = [subcommand, '--target', str(self.root), '--caller', 'antigravity', '--task', '修改代码',
                          '--verify-cmd', command, '--max-rounds', '1']
                if subcommand == 'task':
                    tokens.extend(['--kind', 'code'])
                handler = harness.cmd_gate if subcommand == 'gate' else harness.cmd_task
                with self.subTest(subcommand=subcommand, exit_code=exit_code), \
                     patch.object(harness, '_args_backends', return_value=backends()), \
                     patch.object(harness, 'invoke_edit_worker', return_value='代码修改完成'):
                    code, report = self.capture(handler, self.args(*tokens))
                self.assertEqual(code, 0 if exit_code == 0 else harness.EXIT_CODES['verification_failed'])
                self.assertEqual(marker.read_text(encoding='utf-8'), 'executed')
                self.assertIs(report['verification_executed'], True)
                self.assertIs(report['history'][0]['verification_executed'], True)
                self.assertEqual(report['history'][0]['verify_exit_code'], exit_code)
                self.assertEqual(report['gate_passed'], exit_code == 0)
                self.assertNotIn('verification_notice', report)

    def test_gate_and_code_task_report_no_verification_before_worker_success(self):
        for subcommand in ('gate', 'task'):
            for phase in ('dry_run', 'preflight', 'fallback_preflight', 'worker'):
                tokens = [subcommand, '--target', str(self.root), '--caller', 'antigravity', '--task', '修改代码',
                          '--verify-cmd', 'verify', '--max-rounds', '1']
                if subcommand == 'task':
                    tokens.extend(['--kind', 'code'])
                if phase == 'dry_run':
                    tokens.append('--dry-run')
                if phase == 'fallback_preflight':
                    tokens.append('--fallback-on-quota')
                handler = harness.cmd_gate if subcommand == 'gate' else harness.cmd_task
                failure = harness.HarnessError('preflight failed')
                preparation = ([harness.HarnessError('quota', 'quota_exhausted', quota_backend='codex'), failure]
                               if phase == 'fallback_preflight' else failure if phase == 'preflight' else None)
                with self.subTest(subcommand=subcommand, phase=phase), \
                     patch.object(harness, '_args_backends', return_value=backends()), \
                     patch.object(harness, '_prepare_execution', side_effect=preparation), \
                     patch.object(harness, 'invoke_edit_worker', side_effect=harness.HarnessError('worker failed')) as worker, \
                     patch.object(harness, 'run_process') as verifier:
                    code, report = self.capture(handler, self.args(*tokens))
                self.assertEqual(code, 0 if phase == 'dry_run' else 1)
                self.assertIs(report['verification_executed'], False)
                verifier.assert_not_called()
                if phase == 'worker':
                    worker.assert_called_once()
                    self.assertIs(report['history'][0]['verification_executed'], False)
                    self.assertFalse(report['gate_passed'])
                else:
                    worker.assert_not_called()

    def test_gate_verification_flag_survives_verifier_errors_and_snapshot_failures(self):
        before = harness.snapshot_files(self.root)
        for error in (RuntimeError('verifier failed'), harness.HarnessError('quota during verification', 'quota_exhausted')):
            verifier = Mock(side_effect=error)
            with self.subTest(error=str(error)):
                result = harness.run_physics_gate_loop(self.root, '修改代码', 'verify', Mock(return_value='done'), verifier, max_rounds=1)
            verifier.assert_called_once()
            self.assertIs(result['verification_executed'], True)
            self.assertIs(result['history'][0]['verification_executed'], True)
            self.assertIsNone(result['history'][0]['verify_exit_code'])
            self.assertFalse(result['gate_passed'])
            self.assertFalse(harness._gate_can_replay(result, before, self.root))
        for snapshots, expected in (([PermissionError('before denied')], False),
                                    ([{}, PermissionError('worker denied')], False),
                                    ([{}, {}, PermissionError('verifier denied')], True)):
            with self.subTest(expected=expected, snapshots=len(snapshots)), \
                 patch.object(harness, 'snapshot_files', side_effect=snapshots):
                result = harness.run_physics_gate_loop(self.root, '修改代码', 'verify', Mock(return_value='done'), Mock(return_value=(0, 'ok')))
            self.assertEqual(result['status'], 'failed')
            self.assertIs(result['verification_executed'], expected)
            self.assertFalse(result['scope_verified'])

    def test_dsh_stream_tolerates_intermediate_tool_error_when_final_completed(self):
        payload = json.dumps({
            'summary': 'NOTICE 归属声明完整',
            'content': '已核对第 1-2 行项目名与版权声明。',
            'evidence': ['NOTICE:1', 'NOTICE:2'],
            'unverified': [],
        }, ensure_ascii=False)
        stream = '\n'.join((
            json.dumps({'type': 'session', 'sessionId': 'session-1'}),
            '[llm-deepseek-account] model listing from https://api.deepseek.com/models loaded 2 model(s)',
            json.dumps({'type': 'tool_call', 'callId': 'c1', 'tool': 'finish', 'input': {}}),
            json.dumps({'type': 'tool_result', 'callId': 'c1', 'status': 'error',
                        'error': {'name': 'ToolNotFoundError', 'code': 'UNKNOWN_TOOL'}}),
            json.dumps({'type': 'final', 'sessionId': 'session-1', 'reason': {'kind': 'completed'}, 'text': payload}),
        ))
        parsed = harness._extract_json_object(stream, harness.TASK_JSON_SCHEMA)
        self.assertEqual(parsed['summary'], 'NOTICE 归属声明完整')

    def test_negated_backend_cues_dsh_non_lightweight_and_router_alias_preflight(self):
        engines = self.four_backends()
        engines['claude'].update(llm_source='local_router', config_home=str(self.root / 'claude-cfg'),
                                 base_url='http://127.0.0.1:8240', model='claude-custom-pro', models=[])
        engines['dsh'].update(llm_source='deepseek-account', config_home=str(self.root / 'dsh-cfg'),
                              model='deepseek-v4-pro',
                              models=[{'slug': 'deepseek-v4-pro', 'supported_efforts': ['low', 'high', 'max']},
                                      {'slug': 'deepseek-flash', 'supported_efforts': ['low', 'high', 'max']}])
        self.assertEqual(harness.choose_engine(engines, 'antigravity', '', '不要用 codex，做代码审查'), 'claude:default')
        self.assertEqual(harness._local_fallback_engine('codex', 'codex:default', engines, '别走 agy 和 cc'), 'dsh:default')
        self.assertEqual(harness._local_fallback_engine('codex', 'codex:default', engines, '不要用反重力，也别用 claude code'), 'dsh:default')
        profile = harness.resolve_execution_profile(engines, 'dsh:default', kind='polish', tier='low',
                                                    prompt_text='不要轻量模型，润色 README')
        self.assertEqual(profile['model'], 'deepseek-v4-pro')
        self.assertEqual(profile['profile_source'], 'builtin_flagship')

        cfg_dir = self.root / 'claude-cfg/router'
        cfg_dir.mkdir(parents=True)
        db_path = self.root / 'cc-switch.db'
        with contextlib.closing(sqlite3.connect(db_path)) as con:
            con.execute('CREATE TABLE providers (app_type TEXT, name TEXT, settings_config TEXT)')
            con.execute('INSERT INTO providers VALUES (?, ?, ?)',
                        ('claude', 'Kimi', json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'tok_kimi_fixture_active_99'}})))
            con.execute('INSERT INTO providers VALUES (?, ?, ?)',
                        ('claude', 'DeepSeek', json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': ''}})))
            con.commit()
        (cfg_dir / 'router.json').write_text(json.dumps({
            'default_key_from_card': 'Kimi',
            'ccswitch_db': str(db_path),
            'routes': [{'prefix': 'deepseek', 'key_from_card': 'DeepSeek'}],
        }), encoding='utf-8')
        harness._verify_claude_router_card_key(engines['claude'], 'kimi')
        with self.assertRaisesRegex(harness.HarnessError, 'DeepSeek'):
            harness._verify_claude_router_card_key(engines['claude'], 'ds')

        interop_info = {**engines['claude'], 'windows_interop': True}
        with patch.object(harness.shutil, 'which', return_value='/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'), \
             patch.object(harness, 'run_process', return_value=subprocess.CompletedProcess([], 0, '', '')) as proc:
            self.assertTrue(harness._probe_router_health(interop_info))
            self.assertEqual(proc.call_args[0][0][0], 'powershell.exe')

        direct_wsl = {**engines['claude'], 'llm_source': 'settings_env', 'windows_interop': False,
                      'base_url': 'https://api.deepseek.com/anthropic'}
        fake_addr = [(harness.socket.AF_INET, harness.socket.SOCK_STREAM, 6, '', ('198.18.0.212', 0))]
        with patch.object(harness, '_is_posix', return_value=True), \
             patch.dict(os.environ, {'WSL_DISTRO_NAME': 'Ubuntu-24.04'}, clear=True), \
             patch.object(harness.socket, 'getaddrinfo', return_value=fake_addr):
            with self.assertRaisesRegex(harness.HarnessError, 'Fake-IP'):
                harness._verify_claude_llm_ready(direct_wsl)
            with patch.dict(os.environ, {'WSL_DISTRO_NAME': 'Ubuntu-24.04', 'HTTPS_PROXY': 'http://127.0.0.1:7897'}, clear=True):
                harness._verify_claude_llm_ready(direct_wsl)

    def test_decode_output_json_strips_leaked_deepseek_dsml_stop_tags(self):
        payload = {'summary': 'ok', 'content': 'verified', 'evidence': ['a.py:1'], 'unverified': []}
        leaked = (
            json.dumps(payload, ensure_ascii=False)
            + '\n</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>\n'
        )
        self.assertEqual(harness._decode_output_json(leaked), payload)
        fenced = f'```json\n{json.dumps(payload)}\n```\n</||DSML||tool_calls>'
        self.assertEqual(harness._decode_output_json(fenced), payload)

    def test_clean_room_mode_isolates_global_configs_and_strips_agent_instructions(self):
        (self.root / 'AGENTS.md').write_text('# internal maintainer rules\n', encoding='utf-8')
        (self.root / 'CLAUDE.md').write_text('# internal claude rules\n', encoding='utf-8')
        (self.root / 'README.md').write_text('line one\nline two\n', encoding='utf-8')

        engines = self.four_backends()
        cx_home = self.root / 'host-codex'
        cx_home.mkdir()
        (cx_home / 'auth.json').write_text('{"token":"ok"}', encoding='utf-8')
        (cx_home / 'models_cache.json').write_text('{"models":[]}', encoding='utf-8')
        (cx_home / 'AGENTS.md').write_text('# global codex rules', encoding='utf-8')
        (cx_home / 'hooks.json').write_text('{"hooks":{}}', encoding='utf-8')
        engines['codex']['config_home'] = str(cx_home)

        cl_home = self.root / 'host-claude'
        cl_home.mkdir()
        (cl_home / 'settings.json').write_text(json.dumps({
            'env': {'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8240', 'ANTHROPIC_AUTH_TOKEN': 'via-router'},
            'hooks': {'PreToolUse': [{'hooks': [{'command': 'guard'}]}]},
            'effortLevel': 'xhigh',
        }), encoding='utf-8')
        engines['claude']['config_home'] = str(cl_home)

        dsh_home = self.root / 'host-dsh'
        dsh_home.mkdir()
        (dsh_home / '.credentials.yaml').write_text('provider: deepseek\n', encoding='utf-8')
        (dsh_home / 'AGENTS.md').write_text('# global dsh rules', encoding='utf-8')
        engines['dsh']['config_home'] = str(dsh_home)

        with self.assertRaisesRegex(harness.HarnessError, '只读任务'):
            harness.invoke_engine('codex:default', self.root, '写任务', engines, harness.TASK_JSON_SCHEMA,
                                  readonly=False, clean_room=True)
        with self.assertRaisesRegex(harness.HarnessError, 'agy 后端不支持'):
            harness.invoke_engine('agy:default', self.root, '盲读', engines, harness.TASK_JSON_SCHEMA,
                                  readonly=True, clean_room=True)

        def fake_codex_run(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            clean_home = Path(env['CODEX_HOME'])
            self.assertTrue((clean_home / 'auth.json').is_file())
            self.assertFalse((clean_home / 'AGENTS.md').exists())
            self.assertFalse((clean_home / 'hooks.json').exists())
            self.assertFalse((Path(cwd) / 'AGENTS.md').exists())
            self.assertFalse((Path(cwd) / 'CLAUDE.md').exists())
            self.assertTrue((Path(cwd) / 'README.md').is_file())
            self.assertIn('=== FILE: README.md ===\n1: line one', input_text)
            out_idx = command.index('-o') + 1
            Path(command[out_idx]).write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        receipt_cx = {}
        with patch.object(harness, 'run_process', side_effect=fake_codex_run):
            res_cx = harness.invoke_engine('codex:default', self.root, '新手盲读', engines, harness.TASK_JSON_SCHEMA,
                                           readonly=True, receipt=receipt_cx, clean_room=True)
        self.assertEqual(res_cx, TASK)
        self.assertTrue(receipt_cx['clean_room'])
        self.assertTrue(receipt_cx['staged_readonly_workspace'])

        def fake_claude_run(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            self.assertIn('--bare', command)
            settings_idx = command.index('--settings') + 1
            clean_cfg = json.loads(Path(command[settings_idx]).read_text(encoding='utf-8'))
            self.assertEqual(set(clean_cfg.keys()), {'env'})
            self.assertNotIn('hooks', clean_cfg)
            self.assertFalse((Path(cwd) / 'AGENTS.md').exists())
            return subprocess.CompletedProcess(command, 0, json.dumps({'structured_output': TASK}), '')

        with patch.object(harness, 'run_process', side_effect=fake_claude_run):
            res_cl = harness.invoke_engine('claude:default', self.root, '新手盲读', engines, harness.TASK_JSON_SCHEMA,
                                           readonly=True, clean_room=True)
        self.assertEqual(res_cl, TASK)

        def fake_dsh_run(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            clean_dsh = Path(env['DSH_HOME'])
            self.assertTrue((clean_dsh / '.credentials.yaml').is_file())
            self.assertFalse((clean_dsh / 'AGENTS.md').exists())
            stream = json.dumps({'type': 'final', 'reason': {'kind': 'completed'}, 'text': json.dumps(TASK)})
            return subprocess.CompletedProcess(command, 0, stream, '')

        with patch.object(harness, 'run_process', side_effect=fake_dsh_run):
            res_dsh = harness.invoke_engine('dsh:default', self.root, '新手盲读', engines, harness.TASK_JSON_SCHEMA,
                                            readonly=True, clean_room=True)
        self.assertEqual(res_dsh, TASK)

    def test_meta_driven_synthetic_fleet_profile_and_telemetry_quota_fixes(self):
        synth_profile = {
            'schema_version': 1,
            'profile_id': 'synthetic-future-v9',
            'evaluated_at': '2029-01-01',
            'roles': {
                'orchestrator': {'candidates': ['agy:default', 'codex:default']},
                'math': {'candidates': ['codex:math', 'dsh:default']},
                'code': {'candidates': ['codex:code', 'claude:default']},
                'review': {'candidates': ['claude:default', 'codex:code']},
                'fast': {'candidates': ['dsh:fast', 'codex:@fast']},
            },
            'client_catalog_rules': {
                'agy': {
                    'family_priority': ['xenon'],
                    'fast_tags': ['neon', 'lite'],
                    'blocked_auto_tokens': ['orchestrator', 'router', 'claude'],
                },
                'codex': {
                    'workhorse_families': ['gauss', 'euler'],
                    'deep_tags': ['euler'],
                    'fast_tags': ['boson9', 'lite'],
                },
                'claude': {
                    'default_fallback_alias': 'kimi-k9-max[2m]',
                    'router_primary_model': 'kimi-k9-max[2m]',
                    'router_subagent_model': 'deepseek-v9-nano',
                    'alias_map': {'kimi': 'kimi-k9-max[2m]', 'ds': 'deepseek-v9-nano'},
                },
                'dsh': {
                    'default_model': 'deepseek-v9-omega',
                    'fast_model': 'deepseek-v9-nano',
                    'builtin_models': [
                        {'slug': 'deepseek-v9-omega', 'display_name': 'DS-V9-Omega',
                         'context_window': 2097152, 'max_context_window': 2097152,
                         'supported_efforts': ['off', 'low', 'high', 'max']},
                        {'slug': 'deepseek-v9-nano', 'display_name': 'DS-V9-Nano',
                         'context_window': 2097152, 'max_context_window': 2097152,
                         'supported_efforts': ['off', 'low', 'high', 'max']},
                    ],
                    'alias_map': {'flash': 'deepseek-v9-nano'},
                },
            },
            'peer_fallback_order': {
                'antigravity': ['codex', 'claude', 'dsh'],
                'codex': ['agy', 'claude', 'dsh'],
            },
            'legacy_aliases': {},
        }
        profile_path = self.root / 'synthetic_fleet_profile.json'
        profile_path.write_text(json.dumps(synth_profile), encoding='utf-8')

        engines = self.four_backends()
        engines['codex']['models'] = [
            {'slug': 'gpt-9.2-gauss', 'context_window': 1200000, 'max_context_window': 1200000,
             'supported_efforts': ['low', 'medium', 'high', 'max']},
            {'slug': 'gpt-9.1-euler', 'context_window': 1200000, 'max_context_window': 1200000,
             'supported_efforts': ['low', 'medium', 'high', 'max']},
            {'slug': 'gpt-9.0-boson9', 'context_window': 400000, 'max_context_window': 400000,
             'supported_efforts': ['low', 'medium', 'high']},
        ]
        engines['codex']['model'] = 'gpt-9.2-gauss'
        engines['agy']['models'] = [
            {'slug': 'gemini-9-xenon-high', 'supported_efforts': ['high']},
            {'slug': 'gemini-9-neon-low', 'supported_efforts': ['low']},
        ]
        engines['agy']['model'] = 'gemini-9-xenon-high'
        engines['claude']['model'] = 'kimi-k9-max[2m]'

        with patch.dict(os.environ, {'CAE_FLEET_PROFILE': str(profile_path)}):
            engines['dsh']['models'] = [dict(item, provider='deepseek_official')
                                        for item in synth_profile['client_catalog_rules']['dsh']['builtin_models']]
            engines['dsh']['model'] = synth_profile['client_catalog_rules']['dsh']['default_model']

            math_engine = harness.choose_engine(engines, 'antigravity', 'role:math')
            code_engine = harness.choose_engine(engines, 'antigravity', 'role:code')
            review_engine = harness.choose_engine(engines, 'antigravity', 'role:review')
            fast_engine = harness.choose_engine(engines, 'antigravity', 'role:fast')
            self.assertEqual(math_engine, 'codex:math')
            self.assertEqual(code_engine, 'codex:code')
            self.assertEqual(review_engine, 'claude:default')
            self.assertEqual(fast_engine, 'dsh:fast')

            p_math = harness.resolve_execution_profile(engines, math_engine, kind='math')
            p_code = harness.resolve_execution_profile(engines, code_engine, kind='code')
            p_fast = harness.resolve_execution_profile(engines, fast_engine, tier='low', kind='review')
            p_cx_fast = harness.resolve_execution_profile(engines, 'codex:@fast', tier='low', kind='review')
            engines['claude']['llm_source'] = 'local_router'
            p_cc_fast = harness.resolve_execution_profile(engines, 'claude:@fast', tier='low', kind='review')
            self.assertEqual(p_math['model'], 'gpt-9.1-euler')
            self.assertEqual(p_math['capacity']['selected_window_tokens'], 1200000)
            self.assertEqual(p_code['model'], 'gpt-9.2-gauss')
            self.assertEqual(p_fast['model'], 'deepseek-v9-nano')
            self.assertEqual(p_cx_fast['model'], 'gpt-9.0-boson9')
            self.assertEqual(p_cc_fast['model'], 'deepseek-v9-nano')
            self.assertEqual(harness.select_task_tier('auto', 'review', '快速扫一眼', engine='role:fast'), ('low', 'role_fast_low'))

            with patch.object(harness, '_args_backends', return_value=engines):
                code_rc, assess_out = self.capture(harness.cmd_assess, self.args('assess', '--caller', 'antigravity'))
            self.assertEqual(code_rc, 0)
            self.assertEqual(assess_out['resolved_roles']['math']['model'], 'gpt-9.1-euler')
            self.assertEqual(assess_out['resolved_roles']['review']['selected_engine'], 'claude:default')
            self.assertEqual(assess_out['resolved_roles']['fast']['model'], 'deepseek-v9-nano')

        # 验证 Claude JSON 错误信封（is_error=true 且 result 为字符串）识别 session/weekly limit 与无介词 resets 时间
        claude_limit_envelope = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': True,
            'result': "You've hit your session limit · resets 11pm (Asia/Shanghai)",
        })
        with self.assertRaises(harness.HarnessError) as ctx:
            harness._extract_json_object(claude_limit_envelope, harness.TASK_JSON_SCHEMA)
        self.assertEqual(ctx.exception.status, 'quota_exhausted')
        parsed_reset = harness._parse_quota_reset("You've hit your weekly limit · resets 5pm (UTC)")
        self.assertIsNotNone(parsed_reset)
        self.assertEqual(parsed_reset['reset_hint'], '5pm (UTC)')

        # 验证 DSH 无头调用注入单 JSON 对象强约束且不破坏结果解析
        dsh_home = self.root / 'dsh-json-check'
        dsh_home.mkdir()
        (dsh_home / '.credentials.yaml').write_text("'deepseek-official/DEEPSEEK_API_KEY': 'tok'\n", encoding='utf-8')
        engines['dsh']['config_home'] = str(dsh_home)

        def check_dsh_prompt(command, cwd, input_text, timeout_sec, **kwargs):
            self.assertIn('请仅输出一个以 { 开头、以 } 结尾且严格符合以下 JSON Schema', input_text)
            stream = json.dumps({'type': 'final', 'reason': {'kind': 'completed'}, 'text': json.dumps(TASK)})
            return subprocess.CompletedProcess(command, 0, stream, '')

        with patch.object(harness, 'run_process', side_effect=check_dsh_prompt):
            self.assertEqual(harness.invoke_engine('dsh:default', self.root, '检查提示词', engines, harness.TASK_JSON_SCHEMA), TASK)

    def test_runtime_secret_redaction_dsh_clean_room_dotenv_and_audit_fast_tier(self):
        # 1) 运行期错误详情与端点 URL 脱敏，防止配额冷却 JSON 落盘密钥
        fake_sk = 'sk-' + 'r' * 28
        fake_bearer = 'Bearer ' + 'b' * 28
        raw_err = (
            f"quota hit for DEEPSEEK_API_KEY={fake_sk} auth={fake_bearer} "
            "url=https://u:p@example.com/v1?api_key=secret_q_12345678"
        )
        scrubbed = harness._redact_runtime_secret(raw_err)
        self.assertNotIn(fake_sk, scrubbed)
        self.assertNotIn('b' * 28, scrubbed)
        self.assertNotIn('secret_q_12345678', scrubbed)
        self.assertNotIn('u:p@', scrubbed)
        self.assertEqual(
            harness._sanitize_endpoint_url('https://u:p@example.com/anthropic?token=xyz#f'),
            'https://example.com/anthropic',
        )

        # 2) DSH --clean-room 模式支持从 .env 提取官方 DEEPSEEK_API_KEY
        dsh_dotenv_home = self.root / 'dsh-clean-dotenv'
        dsh_dotenv_home.mkdir()
        dotenv_val = 'ds_env_tok_' + 'k' * 16
        (dsh_dotenv_home / '.env').write_text(f'export DEEPSEEK_API_KEY="{dotenv_val}"\n', encoding='utf-8')
        engines = self.four_backends()
        engines['dsh']['config_home'] = str(dsh_dotenv_home)
        target_dir = self.root / 'clean_doc_target'
        target_dir.mkdir()
        (target_dir / 'README.md').write_text('# Doc\n', encoding='utf-8')

        def fake_clean_dsh(command, cwd, input_text, timeout_sec, **kwargs):
            run_env = kwargs.get('env') or {}
            self.assertEqual(run_env.get('DEEPSEEK_API_KEY'), dotenv_val)
            stream = json.dumps({'type': 'final', 'reason': {'kind': 'completed'}, 'text': json.dumps(TASK)})
            return subprocess.CompletedProcess(command, 0, stream, '')

        with patch.object(harness, 'run_process', side_effect=fake_clean_dsh):
            out = harness.invoke_engine(
                'dsh:default', target_dir, '盲读检查', engines, harness.TASK_JSON_SCHEMA, clean_room=True,
            )
        self.assertEqual(out, TASK)

        # 3) audit --single --left role:fast 透传 engine 提示，默认选用 low 挡位而非 max
        captured_efforts: list[str | None] = []

        def fake_readonly(spec, root, prompt, backends, timeout, files, cli_effort, *rest, **kwargs):
            captured_efforts.append(cli_effort)
            return {**AUDIT, 'read_files': list(files), 'unread_files': [], 'findings': []}

        with patch.object(harness, '_args_backends', return_value=engines), \
                patch.object(harness, 'invoke_readonly_engine', side_effect=fake_readonly):
            rc, rep = self.capture(
                harness.cmd_audit,
                self.args('audit', '--caller', 'antigravity', '--target', str(target_dir), '--single', '--left', 'role:fast'),
            )
        self.assertEqual(rc, 0)
        self.assertEqual(rep['selected_tier'], 'low')
        self.assertEqual(captured_efforts, ['low'])

        # 4) agy 自动选模遵守 blocked_auto_tokens 与 agy_retiring_patterns，codex 编码选模遵守 workhorse_families 顺序
        engines['agy']['models'] = [
            {'slug': 'auto-router-high', 'supported_efforts': ['high']},
            {'slug': 'gemini-4-argon-high', 'supported_efforts': ['high']},
            {'slug': 'gemini-3.8-flash-low', 'supported_efforts': ['low']},
            {'slug': 'gemini-4-carbon-high', 'supported_efforts': ['high']},
        ]
        engines['agy']['model'] = ''
        self.assertEqual(harness.resolve_execution_profile(engines, 'agy:default', 'max', kind='code')['model'], 'gemini-4-carbon-high')
        engines['agy']['models'] = [
            {'slug': 'gemini-4-argon-high', 'supported_efforts': ['high']},
            {'slug': 'gemini-3.8-flash-low', 'supported_efforts': ['low']},
        ]
        retiring_only = harness.resolve_execution_profile(engines, 'agy:default', 'max', kind='code')
        self.assertIsNone(retiring_only['model'])
        self.assertEqual(retiring_only['profile_source'], 'no_native_catalog_candidate')
        engines['codex']['models'] = [
            {'slug': 'gpt-6.9-unknown', 'priority': 99, 'supported_efforts': ['max']},
            {'slug': 'gpt-6.1-sol', 'priority': 10, 'supported_efforts': ['max']},
        ]
        self.assertEqual(harness.resolve_execution_profile(engines, 'codex:code', 'max', kind='code')['model'], 'gpt-6.1-sol')

    def test_symmetric_dynamic_roles_workspace_mode_and_zero_blacklist_grounding(self):
        # 1) 五个角色槽位与 <client>:@<role> 对称动态选路，且 role:orchestrator 读取 recommended_tier
        engines = self.four_backends()
        engines['agy']['models'] = [
            {'slug': 'gemini-4-carbon-high', 'display_name': 'Gemini 4 Carbon (High)', 'supported_efforts': ['high']},
            {'slug': 'gemini-4-hydrogen-low', 'display_name': 'Gemini 4 Hydrogen (Low)', 'supported_efforts': ['low']},
        ]
        engines['codex']['models'] = [
            {'slug': 'gpt-6-astra', 'priority': 1, 'visibility': 'list', 'description': 'Deep reasoning model',
             'supported_efforts': ['low', 'medium', 'high', 'max']},
            {'slug': 'gpt-6.1-sol', 'priority': 2, 'visibility': 'list', 'description': 'Agentic coding model',
             'supported_efforts': ['low', 'medium', 'high', 'max']},
            {'slug': 'gpt-6-luna', 'priority': 3, 'visibility': 'list', 'description': 'Fast and affordable model',
             'supported_efforts': ['low', 'medium', 'high']},
        ]
        tier_orch, reason_orch = harness.select_task_tier('auto', 'review', '梳理总体方案', engine='role:orchestrator')
        self.assertEqual((tier_orch, reason_orch), ('high', 'role_profile_orchestrator_high'))
        self.assertEqual(harness._resolve_role_engine(engines, 'codex', 'role:orchestrator'), 'agy:@orchestrator')
        self.assertEqual(harness._resolve_role_engine(engines, 'antigravity', 'role:orchestrator'), 'codex:@orchestrator')
        self.assertEqual(harness.resolve_execution_profile(engines, 'agy:@orchestrator', 'high')['model'], 'gemini-4-carbon-high')
        self.assertEqual(harness.resolve_execution_profile(engines, 'agy:@fast', 'low')['model'], 'gemini-4-hydrogen-low')
        self.assertEqual(harness.resolve_execution_profile(engines, 'codex:@fast', 'low')['model'], 'gpt-6-luna')
        self.assertEqual(harness.resolve_execution_profile(engines, 'codex:@orchestrator', 'high')['model'], 'gpt-6.1-sol')

        # 2) 工作区身份硬判别：上游配置仓库 vs 通用课题项目
        repo_root = Path(__file__).resolve().parent.parent
        up_mode = harness.detect_workspace_mode(repo_root)
        self.assertEqual(up_mode['mode'], 'upstream_config_repo')
        self.assertTrue(up_mode['is_upstream_config_repo'])
        self.assertEqual(up_mode['upstream_only_capabilities'], [])

        solver_dir = self.root / 'cfd_solver_project'
        (solver_dir / 'src').mkdir(parents=True)
        (solver_dir / 'src' / 'hllc_flux.py').write_text(
            'import argparse\n'
            'DEFAULT_MAX_STEPS = 5000\n'
            'def compute_cfd_euler_flux():\n'
            '    """二维可压缩流体有限体积通量分裂求解器"""\n'
            '    p = argparse.ArgumentParser()\n'
            '    p.add_argument("--cfl-limit", type=float, default=0.8)\n'
            '    return DEFAULT_MAX_STEPS\n',
            encoding='utf-8',
        )
        gen_mode = harness.detect_workspace_mode(solver_dir)
        self.assertEqual(gen_mode['mode'], 'general_project')
        self.assertFalse(gen_mode['is_upstream_config_repo'])
        self.assertIn('fleet_evolve_full_hygiene_audit', gen_mode['upstream_only_capabilities'])

        # 3) 零黑名单确定性事实锚定核验：干净文档通过，虚构缩写/虚构举例/失步数字/不存在参数全部被确定性拦截
        readme = solver_dir / 'README.md'
        readme.write_text(
            '# 二维流体有限体积求解器\n\n'
            '支持 `--cfl-limit` 参数调节，面向流体有限体积通量分裂计算。\n',
            encoding='utf-8',
        )
        clean_rep = harness.audit_doc_grounding(solver_dir, solver_dir)
        self.assertTrue(clean_rep['ok'], clean_rep)

        readme.write_text(
            '# 二维流体有限体积求解器\n\n'
            '面向流体通量分裂、刀轨干涉检查与 NURBS 加工，支持 `--ghost-magic-flag` 且缓存上限为 777,777 token。\n',
            encoding='utf-8',
        )
        rc_fail, strict_rep = self.capture(
            harness.cmd_check_grounding,
            self.args('check-grounding', '--target', str(solver_dir), '--root', str(solver_dir), '--strict'),
        )
        self.assertEqual(rc_fail, 2)
        self.assertEqual(strict_rep['status'], 'verification_failed')
        cats = set(strict_rep['counts_by_category'])
        self.assertIn('ungrounded_acronym', cats)
        self.assertIn('ungrounded_exemplar', cats)
        self.assertIn('ungrounded_identifier', cats)
        self.assertIn('ungrounded_number', cats)

        # 4) 全仓真实文档与 SVG 通过零黑名单事实锚定核验
        repo_rep = harness.audit_doc_grounding(repo_root, repo_root)
        self.assertTrue(repo_rep['ok'], repo_rep['findings'])
        self.assertEqual(repo_rep['finding_count'], 0)

    def test_fenced_json_decoding_clean_room_fallback_and_grounding_edges(self):
        # 1) _decode_output_json 解析外层 ```json 围栏且正文内嵌三反引号代码块的回执，以及前缀杂文+平衡 JSON 对象
        nested_task = {
            **TASK,
            'content': '核对通过，示例代码：\n```python\ndef flux(u):\n    return 0.5 * u * u\n```\n以上守恒。',
        }
        wrapped_fenced = '```json\n' + json.dumps(nested_task, ensure_ascii=False, indent=2) + '\n```'
        self.assertEqual(harness._extract_json_object(wrapped_fenced, harness.TASK_JSON_SCHEMA), nested_task)
        prefixed_raw = '以下是结构化审查回执：\n' + json.dumps(nested_task, ensure_ascii=False) + '\n以上核对完毕。'
        self.assertEqual(harness._extract_json_object(prefixed_raw, harness.TASK_JSON_SCHEMA), nested_task)
        noisy_stream = '\n'.join(
            ['[runtime] loading cordis plugin...']
            + [json.dumps({'type': 'step', 'index': idx}) for idx in range(20)]
            + [json.dumps({'type': 'final', 'reason': {'kind': 'completed'}, 'text': prefixed_raw}, ensure_ascii=False)]
        )
        self.assertEqual(harness._extract_json_object(noisy_stream, harness.TASK_JSON_SCHEMA), nested_task)
        unescaped_win_latex = (
            '{"summary": "核对通过", '
            '"content": "控制方程 \\partial_t U + \\Omega = 0，路径 C:\\users\\user\\NOTICE 与已转义 D:\\\\Data\\\\demo 一致。", '
            '"evidence": ["C:\\users\\user\\NOTICE#L1-L6"], '
            '"unverified": ["D:\\Data\\demo\\LICENSE"]}'
        )
        repaired_obj = harness._extract_json_object(unescaped_win_latex, harness.TASK_JSON_SCHEMA)
        self.assertIn('C:\\users\\user\\NOTICE', repaired_obj['content'])
        self.assertIn('D:\\Data\\demo', repaired_obj['content'])
        self.assertEqual(repaired_obj['unverified'], ['D:\\Data\\demo\\LICENSE'])

        # 2) resolve_caller('auto') 环境变量识别与 _local_fallback_engine 的 clean_room / 双盲排除约束
        for env_map, expected_caller in (
            ({'ANTIGRAVITY_AGENT': '1'}, 'antigravity'),
            ({'CODEX_THREAD_ID': 'thread-01'}, 'codex'),
            ({'CLAUDECODE': '1'}, 'claude'),
            ({'DSH_SESSION_ID': 'sess-01'}, 'dsh'),
        ):
            with patch.dict(os.environ, env_map, clear=True):
                self.assertEqual(harness.resolve_caller('auto'), expected_caller)

        engines = self.four_backends()
        fb_clean = harness._local_fallback_engine('antigravity', 'codex:default', engines, clean_room=True)
        self.assertIsNotNone(fb_clean)
        self.assertFalse(fb_clean.startswith('agy:'), f'clean_room 模式回退不得选中 agy: {fb_clean}')

        fb_blind = harness._local_fallback_engine(
            'codex', 'agy:default', engines, exclude_prefixes=('codex', 'claude')
        )
        self.assertEqual(fb_blind, 'dsh:default')

        # 3) audit_doc_grounding：单行三反引号、同行双 `$VAR` 代码跨度与 `<placeholder>` 不误报，中文紧邻大写缩写被拦截
        edge_dir = self.root / 'grounding_edge_case'
        (edge_dir / 'src').mkdir(parents=True)
        (edge_dir / 'src' / 'solver.py').write_text(
            'import argparse\n'
            'MAX_ITER = 200\n'
            'def run_cfd_flux():\n'
            '    """可压缩流体有限体积求解器"""\n'
            '    p = argparse.ArgumentParser()\n'
            '    p.add_argument("--mesh-file")\n'
            '    return MAX_ITER\n',
            encoding='utf-8',
        )
        edge_doc = edge_dir / 'README.md'
        edge_doc.write_text(
            '# 有限体积求解器说明\n\n'
            '支持 `--mesh-file <case_path>`，读取环境变量 `$HOME` 与 `$PATH`（默认上限 200 步）。\n\n'
            '```python ghost_ungrounded_symbol = 999999 ```\n',
            encoding='utf-8',
        )
        self.assertTrue(harness.audit_doc_grounding(edge_dir, edge_dir)['ok'])

        edge_doc.write_text(
            '# 有限体积求解器说明\n\n'
            '本模块处理离散NURBS几何曲面造型。\n',
            encoding='utf-8',
        )
        bad_rep = harness.audit_doc_grounding(edge_dir, edge_dir)
        self.assertFalse(bad_rep['ok'])
        self.assertIn('ungrounded_acronym', {f['category'] for f in bad_rep['findings']})

    def test_specialty_role_preserves_flagship_on_low_tier_staged_scope_and_winapps_codex(self):
        # 1) 显式指定非 fast 专长槽位（codex:code / codex:math / codex:review / dsh:code / agy:@math）传 low 挡不静默换为轻量模型
        engines = self.four_backends()
        engines['codex']['models'] = [
            {'slug': 'gpt-6-astra', 'priority': 1, 'visibility': 'list', 'description': 'Deep reasoning model',
             'context_window': 272000, 'max_context_window': 872000, 'supported_efforts': ['low', 'medium', 'high', 'max']},
            {'slug': 'gpt-6.1-sol', 'priority': 2, 'visibility': 'list', 'description': 'Agentic coding model',
             'context_window': 500000, 'max_context_window': 872000, 'supported_efforts': ['low', 'medium', 'high', 'max']},
            {'slug': 'gpt-6-luna', 'priority': 3, 'visibility': 'list', 'description': 'Fast and affordable model',
             'context_window': 272000, 'max_context_window': 872000, 'supported_efforts': ['low', 'medium', 'high']},
        ]
        engines['agy']['models'] = [
            {'slug': 'gemini-4-carbon-low', 'display_name': 'Gemini 4 Carbon (Low)', 'supported_efforts': ['low']},
            {'slug': 'gemini-4-carbon-high', 'display_name': 'Gemini 4 Carbon (High)', 'supported_efforts': ['high']},
            {'slug': 'gemini-4-hydrogen-low', 'display_name': 'Gemini 4 Hydrogen (Low)', 'supported_efforts': ['low']},
        ]
        p_cx_code_low = harness.resolve_execution_profile(engines, 'codex:code', 'low', kind='review')
        self.assertEqual(p_cx_code_low['model'], 'gpt-6.1-sol')
        self.assertEqual(p_cx_code_low['cli_effort'], 'low')
        self.assertEqual(p_cx_code_low['profile_source'], 'role_specialty_code')

        p_cx_math_low = harness.resolve_execution_profile(engines, 'codex:math', 'low', kind='math')
        self.assertEqual(p_cx_math_low['model'], 'gpt-6-astra')
        self.assertEqual(p_cx_math_low['cli_effort'], 'low')

        p_cx_fast = harness.resolve_execution_profile(engines, 'codex:@fast', 'low', kind='review')
        self.assertEqual(p_cx_fast['model'], 'gpt-6-luna')
        self.assertEqual(harness.resolve_execution_profile(engines, 'codex:default', 'low', kind='review')['model'], 'gpt-6-luna')

        p_dsh_code_low = harness.resolve_execution_profile(engines, 'dsh:code', 'low', kind='code')
        self.assertEqual(p_dsh_code_low['model'], 'deepseek-v4-pro')
        self.assertEqual(harness.resolve_execution_profile(engines, 'dsh:fast', 'low', kind='review')['model'], 'deepseek-flash')

        p_agy_math_low = harness.resolve_execution_profile(engines, 'agy:@math', 'low', kind='math')
        self.assertEqual(p_agy_math_low['model'], 'gemini-4-carbon-low')
        self.assertEqual(harness.resolve_execution_profile(engines, 'agy:@fast', 'low', kind='review')['model'], 'gemini-4-hydrogen-low')

        # 2) cmd_task 在 --clean-room (staged_readonly_workspace) 下对 stage_dir 校验快照：
        #    主目录并发改动不误报 scope_violation；但若子进程篡改 stage_dir 内文件则仍准确拦截为 scope_violation
        proj = self.root / 'staged_scope_proj'
        proj.mkdir()
        (proj / 'README.md').write_text('original doc\n', encoding='utf-8')
        (proj / 'plugin.json').write_text('{"version": "1.0.0"}\n', encoding='utf-8')
        cl_home = self.root / 'clean-cl-home'
        cl_home.mkdir()
        (cl_home / 'settings.json').write_text('{"env": {"ANTHROPIC_API_KEY": "k-ok"}}', encoding='utf-8')
        engines['claude']['config_home'] = str(cl_home)

        def concurrent_host_edit_run(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            # 模拟主会话在子进程运行期间合法编辑 root 下的 plugin.json，而隔离目录 cwd 保持只读零改动
            (proj / 'plugin.json').write_text('{"version": "1.0.1"}\n', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, json.dumps({'structured_output': TASK}), '')

        args_clean = self.args('task', '--target', str(proj), '--caller', 'antigravity',
                               '--engine', 'claude:default', '--kind', 'review', '--clean-room', '--task', '盲读文档')
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_prepare_execution'), \
             patch.object(harness, 'run_process', side_effect=concurrent_host_edit_run):
            rc_ok, rep_ok = self.capture(harness.cmd_task, args_clean)
        self.assertEqual(rc_ok, 0, rep_ok)
        self.assertEqual(rep_ok['status'], 'ok')
        self.assertEqual(rep_ok['scope_violations'], [])

        def rogue_staged_write_run(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            (Path(cwd) / 'README.md').write_text('tampered inside stage_dir\n', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, json.dumps({'structured_output': TASK}), '')

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_prepare_execution'), \
             patch.object(harness, 'run_process', side_effect=rogue_staged_write_run):
            rc_bad, rep_bad = self.capture(harness.cmd_task, args_clean)
        self.assertNotEqual(rc_bad, 0)
        self.assertEqual(rep_bad['status'], 'scope_violation')
        self.assertIn('README.md', rep_bad['scope_violations'])

        # 暂存建立后若复检额度抛出异常，且主目录并发改动，仍保留原异常状态 quota_exhausted 而不误报 scope_violation
        call_idx = {'n': 0}

        def quota_fail_on_second_check(*_a, **_kw):
            call_idx['n'] += 1
            if call_idx['n'] >= 2:
                (proj / 'plugin.json').write_text('{"version": "1.0.2"}\n', encoding='utf-8')
                raise harness.HarnessError('配额冷却中', 'quota_exhausted', preflight_blocked=True)

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_prepare_execution'), \
             patch.object(harness, '_enforce_preflight_quota', side_effect=quota_fail_on_second_check):
            rc_q, rep_q = self.capture(harness.cmd_task, args_clean)
        self.assertEqual(rc_q, 5)
        self.assertEqual(rep_q['status'], 'quota_exhausted')
        self.assertEqual(rep_q['scope_violations'], [])

        # 3) Windows 下 _find_binary('codex') 对比 AppData\Local\OpenAI\Codex\bin 与 WindowsApps MSIX 择新，且保留 .local/bin 优先级
        fake_home = self.root / 'win-home'
        old_bin = fake_home / 'AppData' / 'Local' / 'OpenAI' / 'Codex' / 'bin' / 'oldhash' / 'codex.exe'
        msix_bin = self.root / 'winapps' / 'OpenAI.Codex_26.1111_x64' / 'app' / 'resources' / 'codex.exe'
        old_bin.parent.mkdir(parents=True)
        msix_bin.parent.mkdir(parents=True)
        old_bin.write_bytes(b'MZ-old')
        msix_bin.write_bytes(b'MZ-new')
        os.utime(old_bin, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        os.utime(msix_bin, ns=(1_800_000_000_000_000_000, 1_800_000_000_000_000_000))
        with patch.object(harness, '_is_posix', return_value=False), \
             patch.object(harness.Path, 'home', return_value=fake_home), \
             patch.object(harness.shutil, 'which', return_value=str(old_bin)), \
             patch.object(harness, '_find_windows_codex_package_cli', return_value=msix_bin), \
             patch.dict(os.environ, {'LOCALAPPDATA': str(fake_home / 'AppData' / 'Local')}, clear=True):
            self.assertEqual(harness._find_binary('codex'), str(msix_bin))

        # 4) WSL 跨子系统互操作：_interop_command 不使用 2>$null 避免 PowerShell 5.1 NativeCommandError，异步排空输出并处理超时；
        #    collect_target_files 收录无扩展名 NOTICE/LICENSE/Dockerfile 与 .svg；
        #    Windows 与 WSL 下排除 Microsoft/WindowsApps 执行别名（含仅剩别名回退分支与 windows_home=None 探测分支）并支持非 UTF-8 ACP 解码
        with patch.object(harness, '_wsl_path', side_effect=lambda p, windows=True: 'Z:\\tmp\\' + Path(p).name):
            interop_cmd, _ = harness._interop_command(['codex.exe', 'exec'], self.root, self.root, '输入')
        interop_ps = base64.b64decode(interop_cmd[-1]).decode('utf-16le')
        self.assertIn("ProcessStartInfo('taskkill.exe'", interop_ps)
        self.assertIn('ReadToEndAsync()', interop_ps)
        self.assertIn('$kp.Kill()', interop_ps)
        self.assertNotIn('2>$null', interop_ps)

        doc_proj = self.root / 'doc-stage-proj'
        doc_proj.mkdir()
        for fname in ('NOTICE', 'LICENSE', 'Dockerfile', 'diagram9.svg'):
            (doc_proj / fname).write_text('sample content\n', encoding='utf-8')
        collected_rels = {item['rel_path'] for item in harness.collect_target_files(doc_proj)}
        self.assertEqual(collected_rels, {'NOTICE', 'LICENSE', 'Dockerfile', 'diagram9.svg'})

        alias_stub = fake_home / 'AppData' / 'Local' / 'Microsoft' / 'WindowsApps' / 'codex.exe'
        alias_stub.parent.mkdir(parents=True)
        alias_stub.write_bytes(b'')
        # 仅剩别名占位且无真实包/缓存二进制时，Windows 与 WSL 均不得将别名当作有效 CLI 返回
        empty_home = self.root / 'empty-win-home'
        with patch.object(harness, '_is_posix', return_value=False), \
             patch.object(harness.Path, 'home', return_value=empty_home), \
             patch.object(harness.shutil, 'which', return_value=str(alias_stub)), \
             patch.object(harness, '_find_windows_codex_package_cli', return_value=None), \
             patch.dict(os.environ, {'LOCALAPPDATA': str(empty_home / 'AppData' / 'Local')}, clear=True):
            self.assertIsNone(harness._find_binary('codex'))

        def wsl_which(cmd):
            return {
                'wslpath': '/usr/bin/wslpath',
                'cmd.exe': '/mnt/c/Windows/System32/cmd.exe',
                'reg.exe': '/mnt/c/Windows/System32/reg.exe',
                'codex.exe': str(alias_stub),
            }.get(cmd)
        with patch.object(harness, '_is_posix', return_value=True), \
             patch.object(harness.Path, 'home', return_value=self.root / 'wsl-linux-home'), \
             patch.object(harness.shutil, 'which', side_effect=wsl_which), \
             patch.object(harness, '_windows_env', side_effect=harness.HarnessError('no env')), \
             patch.object(harness, '_find_windows_codex_package_cli', return_value=None), \
             patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(harness._find_binary('codex'))

        # CP936 非 UTF-8 注册表中文路径（含在 UTF-8 中碰巧合法双字节的“目录” \xc4\xbf\xc2\xbc）通过 ACP 代码页无损解码并命中 MSIX CLI
        reg_stdout_gbk = (
            'HKEY_CURRENT_USER\\Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\CurrentVersion\\AppModel\\Repository\\Packages\\OpenAI.CodexPrimaryRuntime_99.0_x64__abc\r\n'
            '    PackageRootFolder    REG_SZ    D:\\运行库\\OldRuntime\r\n'
            'HKEY_CURRENT_USER\\Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\CurrentVersion\\AppModel\\Repository\\Packages\\OpenAI.Codex_26.1111.2228.0_x64__abc\r\n'
            '    PackageRootFolder    REG_SZ    D:\\目录\\OpenAI.Codex_26.1111\r\n'
        ).encode('cp936')
        acp_stdout = b'\r\nHKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\Nls\\CodePage\r\n    ACP    REG_SZ    936\r\n\r\n'
        def reg_run_mock(cmd, **_kwargs):
            if 'CodePage' in ' '.join(str(x) for x in cmd):
                return subprocess.CompletedProcess(cmd, 0, acp_stdout, b'')
            return subprocess.CompletedProcess(cmd, 0, reg_stdout_gbk, b'')
        def wsl_path_mock(raw, windows=True):
            if not windows and '目录\\OpenAI.Codex_26.1111' in str(raw):
                return str(msix_bin.parent.parent.parent)
            return str(fake_home / 'AppData' / 'Local')
        with patch.object(harness, '_is_posix', return_value=True), \
             patch.object(harness.Path, 'home', return_value=self.root / 'wsl-linux-home'), \
             patch.object(harness.shutil, 'which', side_effect=wsl_which), \
             patch.object(harness, '_windows_env', return_value='C:\\Users\\<user>\\AppData\\Local'), \
             patch.object(harness, '_wsl_path', side_effect=wsl_path_mock), \
             patch.object(harness.subprocess, 'run', side_effect=reg_run_mock), \
             patch.dict(os.environ, {}, clear=True):
            self.assertEqual(harness._find_binary('codex'), str(msix_bin))

        # install.detect_installed_apps 在 WSL windows_home=None 且仅剩执行别名时不得将互操作标为可用
        inst_spec = importlib.util.spec_from_file_location('install_alias_guard_test', ROOT / 'install.py')
        inst_mod = importlib.util.module_from_spec(inst_spec)
        inst_spec.loader.exec_module(inst_mod)
        wsl_only_alias = self.root / 'mnt' / 'c' / 'win-home' / 'AppData' / 'Local' / 'Microsoft' / 'WindowsApps' / 'codex.exe'
        wsl_only_alias.parent.mkdir(parents=True)
        wsl_only_alias.write_bytes(b'')
        with patch.object(inst_mod, '_is_wsl', return_value=True), \
             patch.object(inst_mod, '_probe_wsl_windows_profile_dir', return_value=None), \
             patch.object(inst_mod, '_find_wsl_windows_codex_package', return_value=(None, None)), \
             patch.object(inst_mod, '_native_cli', return_value=None), \
             patch.object(inst_mod, '_first_executable', return_value=None), \
             patch.object(inst_mod.Path, 'home', return_value=self.root / 'wsl-linux-home'), \
             patch.object(inst_mod.shutil, 'which', side_effect=lambda c: str(wsl_only_alias) if c == 'codex.exe' else None), \
             patch.dict(os.environ, {}, clear=True):
            detected = inst_mod.detect_installed_apps()
            self.assertFalse(detected['codex']['interop_available'])
            self.assertIsNone(detected['codex']['interop_evidence'][0]['binary'])

    def test_wp1_subscription_gates_and_explicit_disabled_alias(self):
        engines = self.four_backends()
        profile = json.loads(json.dumps(harness._fleet_profile()))
        profile['roles']['review']['candidates'] = ['claude:@review', 'codex:review']
        engines['claude'].update(model='k3.1-pro[1m]', models=[{'slug': 'k3.1-pro[1m]'}])
        profile['subscription_matrix']['models']['k3.1-pro[1m]']['enabled'] = False
        with patch.object(harness, '_fleet_profile', return_value=profile):
            self.assertFalse(harness._is_subscription_enabled('claude', 'k3.1-pro[1M]'))
            self.assertEqual(harness.choose_engine(engines, 'agy', 'role:review'), 'codex:review')
            for spec in ('claude:k3.1-pro', 'claude:k3.1-pro[1m]'):
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.choose_engine(engines, 'agy', spec)
                self.assertEqual(caught.exception.status, 'subscription_disabled')
            profile['subscription_matrix']['agents']['codex']['enabled'] = False
            self.assertFalse(harness._peer_auto_ready(engines, 'codex'))
            self.assertTrue(harness.choose_engine(engines, 'agy').startswith('dsh:'))
            with self.assertRaises(harness.HarnessError) as caught:
                harness.resolve_execution_profile(engines, 'codex:default')
            self.assertEqual(caught.exception.status, 'subscription_disabled')

    def test_wp1_codex_local_rate_limits_reset_redaction_and_plan_switch(self):
        engines = self.four_backends()
        sessions = Path(engines['codex']['config_home']) / 'sessions' / 'day'
        sessions.mkdir(parents=True)
        rollout = sessions / 'rollout-test.jsonl'
        (self.root / 'a.py').write_text('x = 1\n', encoding='utf-8')
        for percent, reset, expected in ((46.0, time.time() + 3600, False),
                                         (100.0, time.time() + 3600, True),
                                         (100.0, time.time() - 1, False)):
            rollout.write_text(json.dumps({'payload': {'rate_limits': {'plan_type': 'test',
                'primary': {'used_percent': percent, 'window_minutes': 300, 'resets_at': reset}}}}), encoding='utf-8')
            info = harness.inspect_backend_quota_and_availability('codex', engines['codex'])
            self.assertEqual(info['quota_exceeded'], expected)
            self.assertEqual(info['currently_available'], not expected)
            self.assertIn('5h_window:', info['quota_summary'])
            redacted = harness.inspect_backend_quota_and_availability('codex', engines['codex'], redact_usage=True)
            self.assertNotIn('%', redacted['quota_summary'])
            if expected:
                args = self.args('plan', '--caller', 'antigravity', '--target', str(self.root / 'a.py'),
                                 '--engine', 'role:review', '--timeout', '60', '--plan-dir', str(self.root / 'plans'))
                profile = json.loads(json.dumps(harness._fleet_profile()))
                profile['roles']['review']['candidates'] = ['codex:review', 'dsh:review']
                with patch.object(harness, '_args_backends', return_value=engines), \
                     patch.object(harness, '_fleet_profile', return_value=profile):
                    code, plan = self.capture(harness.cmd_plan, args)
                self.assertEqual(code, 0)
                self.assertTrue(plan['is_heavy'])
                self.assertFalse(plan['streams'][0]['engine'].startswith('codex:'))
                self.assertTrue(plan['streams'][0]['currently_available'])
                for engine, prompt in (('codex:review', ''), ('claude:default', '请用 Codex 审查')):
                    args.engine, args.user_prompt = engine, prompt
                    with self.assertRaises(harness.HarnessError) as caught:
                        harness._resolve_plan_streams(args, [{}], engines)
                    self.assertEqual(caught.exception.status, 'quota_exhausted')

    def test_wp1_ccswitch_readonly_card_limits_and_dead_router(self):
        engines = self.four_backends()
        info = engines['claude']
        db = self.root / 'cc-switch.db'
        with sqlite3.connect(db) as con:
            con.execute('CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, is_current INTEGER, meta TEXT)')
            con.execute('CREATE TABLE usage_daily_rollups (provider_id TEXT, date TEXT, total_cost_usd REAL)')
            con.executemany('INSERT INTO providers VALUES (?, ?, ?, ?, ?)', [
                ('p', 'claude', 'Kimi', 1, json.dumps({'usage_script': {'templateType': 'token_plan'}, 'limit_daily_usd': 2})),
                ('f', 'claude', 'DeepSeek', 0, json.dumps({'usage_script': {'templateType': 'balance'}}))])
            con.execute('INSERT INTO usage_daily_rollups VALUES (?, ?, ?)', ('p', datetime.date.today().isoformat(), 2))
        con.close()
        before = db.read_bytes()
        with patch.dict(os.environ, {'CAE_CCSWITCH_DB': str(db)}):
            quota = harness.inspect_backend_quota_and_availability('claude', info)
            self.assertEqual(quota['quota_scheme'], 'token_plan')
            self.assertTrue(quota['quota_exceeded'])
            cfg = Path(info['config_home']) / 'router'
            cfg.mkdir(parents=True)
            (cfg / 'router.json').write_text(json.dumps({'default_key_from_card': 'Kimi',
                'routes': [{'prefix': 'deepseek', 'key_from_card': 'DeepSeek'}]}), encoding='utf-8')
            info.update(llm_source='local_router', uses_local_router=True, base_url='http://127.0.0.1:1')
            with patch.object(harness, '_loopback_router_alive', return_value=False):
                quota = harness.inspect_backend_quota_and_availability('claude', info, 'deepseek-flash[1M]')
            self.assertEqual(quota['quota_scheme'], 'balance')
            self.assertFalse(quota['currently_available'])
            self.assertEqual(quota['unavailable_reason'], 'local_router_unavailable')
        self.assertEqual(db.read_bytes(), before)

    def test_wp1_lightweight_specialty_conflict_and_cooled_role_fallback(self):
        engines = self.four_backends()
        engines['claude'].update(model='deepseek-flash[1M]', models=[{'slug': 'deepseek-flash[1M]'}])
        profile = json.loads(json.dumps(harness._fleet_profile()))
        profile['roles']['review']['candidates'] = ['claude:@review', 'codex:review', 'dsh:review']
        with patch.object(harness, '_fleet_profile', return_value=profile):
            resolved = harness.resolve_execution_profile(engines, 'claude:@review')
            self.assertIn('轻量模型', resolved['execution_blocked_reason'])
            self.assertIsNone(harness.resolve_execution_profile(engines, 'claude:deepseek-flash[1M]').get('execution_blocked_reason'))
            self.assertEqual(harness.choose_engine(engines, 'agy', 'role:review'), 'codex:review')
            harness._record_quota_exhausted('codex', engines['codex'], 'quota exceeded')
            self.assertEqual(harness.choose_engine(engines, 'agy', 'role:review'), 'dsh:review')
            harness._record_quota_exhausted('dsh', engines['dsh'], 'quota exceeded')
            with self.assertRaises(harness.HarnessError) as caught:
                harness.choose_engine(engines, 'agy', 'role:review')
            self.assertEqual(caught.exception.status, 'quota_exhausted')

    def test_wp1_user_prompt_p0_quoted_identifiers_tiers_and_fast_symmetry(self):
        engines = self.four_backends()
        engines['codex']['models'].append({'slug': 'gpt-6.1-sol', 'provider': 'openai',
            'max_context_window': 1000000, 'supported_efforts': ['low', 'high', 'max']})
        self.assertEqual(harness._extract_prompt_model_hint('请用 `gpt-6.1-sol` 审查'), 'gpt-6.1-sol')
        self.assertEqual(harness.choose_engine(engines, 'agy', 'role:review', user_prompt='请用 `gpt-6.1-sol` 审查'), 'codex:gpt-6.1-sol')
        with self.assertRaises(harness.HarnessError) as disabled_prompt_k3:
            harness.choose_engine(engines, 'agy', 'role:review', user_prompt='让 Kimi 高挡位看看')
        self.assertEqual(disabled_prompt_k3.exception.status, 'subscription_disabled')
        enabled_profile_head = json.loads(json.dumps(harness._fleet_profile()))
        enabled_profile_head['subscription_matrix']['models']['k3.1-pro[1m]']['enabled'] = True
        with patch.object(harness, '_fleet_profile', return_value=enabled_profile_head):
            kimi = harness.choose_engine(engines, 'agy', 'role:review', user_prompt='让 Kimi 高挡位看看')
        self.assertTrue(kimi.startswith('claude:'))
        self.assertEqual(harness.choose_engine(engines, 'agy', 'role:review', user_prompt='用 DSH 审'), 'dsh:@review')
        for text in ('`use cc`', '> 请用 `gpt-6.1-sol`', '```\n请用 `gpt-6.1-sol`\n```'):
            self.assertEqual(harness._extract_prompt_backend_hint(text), '')
            self.assertEqual(harness._extract_prompt_model_hint(text), '')
        for level in ('low', 'medium', 'high', 'xhigh', 'max'):
            self.assertEqual(harness.select_task_tier(text=f'挂 {level} 挡位看看')[0], level)
        for prefix in ('agy', 'codex', 'claude', 'dsh'):
            suffixes = ('fast', '@fast') if prefix in ('claude', 'dsh') else ('@fast',)
            for suffix in suffixes:
                self.assertTrue(harness._is_fast_engine_spec(prefix + ':' + suffix))
                self.assertEqual(harness._per_backend_role_target(prefix, suffix), 'fast')
        for prefix in ('agy', 'codex'):
            self.assertFalse(harness._is_fast_engine_spec(prefix + ':fast'))
            self.assertEqual(harness._per_backend_role_target(prefix, 'fast'), '')
        self.assertEqual(harness.choose_engine(engines, 'agy', 'claude:1m'), 'claude:default')
        self.assertFalse(harness._is_subscription_enabled('claude', 'k3.1-pro[1m]'))
        with self.assertRaises(harness.HarnessError) as disabled_k3:
            harness.resolve_execution_profile(engines, 'claude:k3.1-pro[1m]')
        self.assertEqual(disabled_k3.exception.status, 'subscription_disabled')
        enabled_profile = json.loads(json.dumps(harness._fleet_profile()))
        enabled_profile['subscription_matrix']['models']['k3.1-pro[1m]']['enabled'] = True
        (self.root / 'a.py').write_text('x = 1', encoding='utf-8')
        args = self.args('task', '--caller', 'antigravity', '--engine', 'role:review',
                         '--kind', 'review', '--target', str(self.root / 'a.py'), '--task', '检查代码',
                         '--user-prompt', '让 Kimi 高挡位看看', '--dry-run')
        with patch.object(harness, '_fleet_profile', return_value=enabled_profile), \
                patch.object(harness, '_args_backends', return_value=engines):
            code, result = self.capture(harness.cmd_task, args)
        self.assertEqual(code, 0)
        self.assertTrue(result['engine'].startswith('claude:'))
        self.assertEqual(result['reasoning_effort'], 'high')

    def test_wp1_inline_all_backends_budget_binary_secrets_and_staging_filter(self):
        engines = self.four_backends()
        target = self.root / 'target'
        target.mkdir()
        (target / 'a.py').write_text('one\ntwo', encoding='utf-8')
        (target / '.env').write_text('SECRET=value', encoding='utf-8')
        (target / 'auth.json').write_text('{"token":"private"}', encoding='utf-8')
        (target / 'blob.txt').write_bytes(b'\x00binary')
        for prefix in ('codex', 'agy', 'claude', 'dsh'):
            def runner(command, cwd, input_text, timeout_sec, **kwargs):
                self.assertIn('=== FILE: a.py ===', input_text)
                self.assertIn('1: one', input_text)
                self.assertNotIn('SECRET=value', input_text)
                self.assertNotIn('private', input_text)
                self.assertIn('请勿再重复调用', input_text)
                if prefix == 'codex':
                    Path(command[command.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, json.dumps(TASK), '')
            with patch.object(harness, 'run_process', side_effect=runner):
                self.assertEqual(harness.invoke_engine(prefix + ':default', target, '读文件', engines,
                                 harness.TASK_JSON_SCHEMA, inline_readonly=True), TASK)
        (target / 'large.py').write_text('z' * 1024, encoding='utf-8')
        with patch.object(harness, '_headless_tunables', return_value={'inline_readonly_max_kib': 1}):
            inline = harness._inline_readonly_prompt('', target, ['large.py', 'a.py', 'blob.txt', '.env'])
        self.assertIn('=== FILE: a.py ===', inline)
        self.assertNotIn('=== FILE: large.py ===', inline)
        self.assertIn('未内联清单', inline)
        (target / 'AGENTS.md').write_text('rules', encoding='utf-8')
        staged = harness._stage_wsl_readonly_workspace(target, self.root / 'stage', ['AGENTS.md', 'a.py'], exclude_agent_instructions=True)
        self.assertFalse((staged / 'AGENTS.md').exists())
        self.assertTrue((staged / 'a.py').is_file())

    def test_wp1_snapshot_derived_dirs_agentignore_and_explicit_full_verification(self):
        for directory in ('.venv', 'runs', 'build', 'raw-data', 'src'):
            (self.root / directory).mkdir()
            (self.root / directory / 'a.py').write_text('x = 1', encoding='utf-8')
        (self.root / 'src' / 'skip.txt').write_text('ignored', encoding='utf-8')
        (self.root / '.agentignore').write_text('# data\n\nraw-data/\nsrc/*.txt\n', encoding='utf-8')
        snap = harness.snapshot_files(self.root)
        self.assertIn('src/a.py', snap)
        for name in ('.venv/a.py', 'runs/a.py', 'build/a.py', 'raw-data/a.py', 'src/skip.txt'):
            self.assertNotIn(name, snap)
        self.assertIn('build/a.py', harness.snapshot_files(self.root, ignore_dirs=['.git']))

    def test_wp1_dsh_dual_cli_versions_dotenv_and_oauth_clean_room(self):
        engines = self.four_backends()
        home = Path(engines['dsh']['config_home'])
        home.mkdir()
        key = 'fixture_deepseek_key_0123456789'
        (home / '.env').write_text('DEEPSEEK_API_KEY=' + key, encoding='utf-8')
        with patch.object(harness, '_dsh_home', return_value=(home, False)), patch.dict(os.environ, {}, clear=True):
            info = harness._dsh_info('dsh')
        self.assertEqual(info['credential_source'], 'dotenv_api_key')
        self.assertNotIn(key, json.dumps(info))
        binary = self.root / 'dsh-fixture.exe'
        binary.write_bytes(b'not executable')
        for help_text, supported in (('Usage: dsh --profile --help', False), ('Usage: dsh --permission-mode --help', True)):
            harness._DSH_PERMISSION_CACHE.clear()
            with patch.object(harness, 'run_process', return_value=subprocess.CompletedProcess([], 0, help_text, '')) as run:
                self.assertEqual(harness._dsh_supports_cli_permission_flags(str(binary)), supported)
                self.assertEqual(harness._dsh_supports_cli_permission_flags(str(binary)), supported)
            self.assertEqual(run.call_count, 1)
        target = self.root / 'target'
        target.mkdir()
        (target / 'a.py').write_text('x=1', encoding='utf-8')
        for supported in (False, True):
            def runner(command, cwd, input_text, timeout_sec, env, **kwargs):
                self.assertEqual('--permission-mode' in command, supported)
                self.assertEqual(env['DEEPSEEK_API_KEY'], key)
                if not supported:
                    self.assertEqual(env['DSH_PERMISSION_MODE'], 'read-only')
                patch_text = Path(command[command.index('--patch') + 1]).read_text(encoding='utf-8')
                for plugin in ('agent-default-model', 'credentials', 'authorization', 'deepseek-account'):
                    self.assertIn('- id: ' + plugin, patch_text)
                for plugin in ('sandbox-policy', 'approval', 'permission'):
                    self.assertEqual('- id: ' + plugin + '\n' in patch_text, not supported)
                return subprocess.CompletedProcess(command, 0, json.dumps(TASK), '')
            with patch.object(harness, '_dsh_supports_cli_permission_flags', return_value=supported), \
                 patch.object(harness, 'run_process', side_effect=runner), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(harness.invoke_engine('dsh:default', target, '读', engines,
                    harness.TASK_JSON_SCHEMA, clean_room=True), TASK)
        (home / '.credentials.yaml').write_text('deepseek-account-platform/main:\n  token: account_fixture_valid_0123456789\n', encoding='utf-8')
        (home / '.env').unlink()
        with patch.object(harness, '_dsh_home', return_value=(home, False)), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'stale_key_0123456789'}, clear=True):
            engines['dsh'] = harness._dsh_info('dsh')
        self.assertEqual(engines['dsh']['credential_source'], 'account')
        def account_runner(command, cwd, input_text, timeout_sec, env, **kwargs):
            self.assertNotIn('DEEPSEEK_API_KEY', env)
            self.assertTrue((Path(env['DSH_HOME']) / '.credentials.yaml').is_file())
            text = Path(command[command.index('--patch') + 1]).read_text(encoding='utf-8')
            self.assertIn('provider: deepseek-account', text)
            self.assertNotIn('disabled: true', text)
            return subprocess.CompletedProcess(command, 0, json.dumps(TASK), '')
        with patch.object(harness, 'run_process', side_effect=account_runner), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'stale_key_0123456789'}, clear=True):
            harness.invoke_engine('dsh:default', target, '读', engines, harness.TASK_JSON_SCHEMA, clean_room=True)

    @patch.dict(os.environ, {'CAE_HARNESS_DEPTH': '', 'CAE_DISABLE_HEAVY_COMPLETIONS': ''})
    def test_wp1_plan_lifecycle_batch_receipt_and_pending_accounts(self):
        engines = self.four_backends()
        target = self.root / 'target'
        target.mkdir()
        (target / 'a.py').write_text('x=1', encoding='utf-8')
        plans = self.root / 'state' / 'dispatch_plans'
        common = ['--caller', 'antigravity', '--plan-dir', str(plans), '--target', str(target), '--timeout', '60']
        with patch.object(harness, '_args_backends', return_value=engines):
            _, light = self.capture(harness.cmd_plan, self.args('plan', *common, '--engine', 'codex:review'))
            self.assertFalse(light['is_heavy'])
            self.assertFalse(plans.exists())
            spec = self.root / 'spec.json'
            spec.write_text(json.dumps([{'stream_id': sid, 'subtask': '审查', 'target': str(target),
                                         'engine': engine, 'timeout': 60} for sid, engine in
                                        [('S1', 'codex:review'), ('S2', 'dsh:review')]]), encoding='utf-8')
            _, ticket = self.capture(harness.cmd_plan, self.args('plan', *common, '--spec-file', str(spec),
                '--session-id', 'session1', '--workflow-bg', '接口变更', '--workflow-goal', '先审查再核对', '--workflow-impact', '只读并返回证据'))
            self.assertTrue(ticket['is_heavy'])
            tid = ticket['ticket_id']
            self.assertTrue((plans / 'session_session1_pending.json').is_file())
            self.capture(harness.cmd_plan, self.args('plan', *common, '--mark-handed-off', tid))
            _, resumed = self.capture(harness.cmd_plan, self.args('plan', *common, '--resume-ticket', tid, '--full-json'))
            self.assertEqual(resumed['status'], 'HANDED_OFF')
            for key in ('workflow_bg', 'workflow_goal', 'workflow_impact'):
                self.assertEqual(resumed[key], ticket[key])
                self.assertIn(ticket[key], resumed['handoff_prompt_block'])
            _, approved = self.capture(harness.cmd_plan, self.args('plan', *common, '--approve-ticket', tid,
                '--override-json', json.dumps({'S1': {'tier': 'high'}})))
            self.assertEqual(approved['status'], 'APPROVED')
            self.assertNotEqual(approved['plan_hash'], ticket['plan_hash'])
            self.assertFalse((plans / 'session_session1_pending.json').exists())
            output = self.root / 'receipt.json'
            def invoke(*args, receipt, **kwargs):
                receipt['token_usage'] = harness.extract_cli_token_usage('codex', '{"usage":{"input_tokens":10,"output_tokens":2}}')
                return dict(TASK)
            with patch.object(harness, 'invoke_engine', side_effect=invoke):
                code, result = self.capture(harness.cmd_batch, self.args('batch', '--caller', 'antigravity',
                    '--plan-dir', str(plans), '--ticket', tid, '--output', str(output), '--session-id', 'session1'))
            self.assertEqual(code, 0)
            self.assertTrue(all(s['succeeded'] for s in result['streams']))
            self.assertEqual(result['token_usage_summary']['total_tokens'], 24)
            self.assertTrue(output.is_file())
            public = json.loads(output.read_text(encoding='utf-8'))
            self.assertIn('sanitized_invocation', public)
            self.assertNotIn(str(self.root), output.read_text(encoding='utf-8'))
            pending = json.loads((plans.parent / 'heavy_completions' / 'session1.json').read_text(encoding='utf-8'))
            self.assertFalse(pending[0]['reported_to_user'])
            self.assertTrue(pending[0]['all_succeeded'])
            self.assertEqual(pending[0]['receipt_file'], str(output))
            gate_payload = {
                'status': 'ok',
                'engine': 'codex:gpt-6.1-sol',
                'model': 'gpt-6.1-sol',
                'history': [{'round': 1, 'passed': True, 'succeeded': True, 'token_usage': {'total_tokens': 5, 'usage_source': 'cli_reported'}}],
                'token_usage_summary': {'total_tokens': 5, 'reported_streams': 1, 'unreported_streams': 0},
            }
            harness.record_heavy_completion_for_stop_hook(
                self.args('gate', '--target', str(self.root), '--verify-cmd', 'true', '--task', 'x', '--plan-dir', str(plans), '--session-id', 'session_gate'), gate_payload)
            gate_pending = json.loads((plans.parent / 'heavy_completions' / 'session_gate.json').read_text(encoding='utf-8'))
            self.assertEqual(gate_pending[0]['streams'][0]['status'], 'ok')

    def test_wp1b_plan_mixed_channels_full_matrix_and_handoff(self):
        engines = self.four_backends()
        home = Path(engines['codex']['config_home'])
        home.mkdir()
        (home / 'config.toml').write_text('model = "fixture-code"\nmodel_context_window = 65536\n', encoding='utf-8')
        target = self.root / 'a.py'
        target.write_text('x=1\n', encoding='utf-8')
        spec = self.root / 'matrix.json'
        spec.write_text(json.dumps([
            {'stream_id': 'N1', 'subtask': '核对原文', 'channel': 'native_subagent',
             'agent_type': 'readonly-reader', 'model': 'inherit', 'target': str(target),
             'clean_room': True, 'inline_readonly': True, 'receipt_location': 'native-log.jsonl'},
            {'stream_id': 'H1', 'subtask': '独立审查', 'channel': 'cae_harness_headless',
             'engine': 'dsh:review', 'agent_type': 'readonly-auditor', 'target': str(target),
             'clean_room': True, 'receipt_location': 'headless-receipt.json'}]), encoding='utf-8')
        args = self.args('plan', '--caller', 'codex', '--spec-file', str(spec), '--target', str(target),
                         '--plan-dir', str(self.root / 'plans'), '--timeout', '60', '--full-json',
                         '--workflow-bg', '接口调整', '--workflow-goal', '核对实现', '--workflow-impact', '返回只读证据')
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'inspect_backend_quota_and_availability', wraps=harness.inspect_backend_quota_and_availability) as inspect:
            _, ticket = self.capture(harness.cmd_plan, args)
        self.assertEqual([call.args[0] for call in inspect.call_args_list], ['codex', 'dsh'])
        native, headless = ticket['streams']
        required = ('stream_id', 'subtask', 'target', 'channel', 'client', 'agent_type', 'model_display',
                    'tier', 'context_window', 'headless', 'clean_room', 'access_mode', 'inline_readonly',
                    'timeout', 'currently_available', 'quota_scheme', 'quota_summary', 'receipt_location')
        persisted = json.loads((self.root / 'plans' / (ticket['ticket_id'] + '.json')).read_text(encoding='utf-8'))
        for stream in persisted['streams']:
            self.assertTrue(all(key in stream for key in required))
        self.assertEqual(native['model'], 'fixture-code')
        self.assertEqual(native['model_display'], 'inherit (fixture-code)')
        self.assertEqual(native['context_window'], 65536)
        self.assertFalse(native['headless'])
        self.assertFalse(native['clean_room'])
        self.assertFalse(native['inline_readonly'])
        self.assertTrue(headless['headless'])
        self.assertTrue(headless['clean_room'])
        self.assertTrue(headless['inline_readonly'])
        for key in ('human_popup_markdown', 'handoff_prompt_block'):
            text = ticket[key]
            rows = [line for line in text.splitlines() if line.startswith('|')]
            self.assertTrue(all(len(re.split(r'(?<!\\)\|', line)[1:-1]) <= 4 for line in rows))
            self.assertEqual(rows[0], '| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |')
            self.assertEqual(len(rows), 4 + 16 * len(ticket['streams']))
            columns = ['流号', '子任务与范围', '通道', '客户端及子代理类型', '模型名字', '挡位', '上下文窗口',
                       '是否无头', '是否净室', '访问模式、写入范围与验收命令', '内联与超时', '当前是否可用',
                       '本机配置额度类型与当前状态', '回执或子代理日志位置']
            for number, stream in enumerate(ticket['streams']):
                detail = rows[4 + number * 16:4 + (number + 1) * 16]
                self.assertEqual(detail[0], '| 序号 | 参数项 | 当前值 |')
                cells = [re.split(r'(?<!\\)\|', line)[1:-1] for line in detail[2:]]
                self.assertEqual([cell[0].strip() for cell in cells], [str(i) for i in range(1, 15)])
                self.assertEqual([cell[1].strip() for cell in cells], columns)
                values = [cell[2].strip() for cell in cells]
                yes_no = lambda value: '是' if value else '否'
                available = yes_no(stream['currently_available'])
                if stream.get('unavailable_reason'):
                    available += '（' + stream['unavailable_reason'] + '）'
                expected = [stream['stream_id'], stream['subtask'] + '；范围：' + stream['target'],
                            harness._PLAN_CHANNEL_LABELS[stream['channel']] + ' (' + stream['channel'] + ')',
                            stream['client'] + '；' + stream['agent_type'], stream['model_display'], stream['tier'],
                            str(stream['context_window']) + ' tokens' if stream['context_window'] else '未声明',
                            yes_no(stream['headless']), yes_no(stream['clean_room']), stream['access_mode'],
                            '内联：' + yes_no(stream['inline_readonly']) + '；超时：' + str(stream['timeout']) + ' 秒',
                            available, stream['quota_scheme'] + '；' + stream['quota_summary'], stream['receipt_location']]
                self.assertEqual(values, [str(value).replace('|', r'\|').replace('\n', ' ') for value in expected])
                self.assertEqual(values[7:9], ['是' if stream['headless'] else '否', '是' if stream['clean_room'] else '否'])
                self.assertEqual(values[11], '是')
                for field in ('stream_id', 'subtask', 'target', 'channel', 'client', 'agent_type', 'model_display',
                              'tier', 'access_mode', 'quota_scheme', 'quota_summary', 'receipt_location'):
                    self.assertIn(str(stream[field]), '\n'.join(detail))
                self.assertIn(str(stream['timeout']), values[10])
            for field in ('workflow_bg', 'workflow_goal', 'workflow_impact'):
                self.assertIn(ticket[field], text)
            for date in ticket['capability_ledger'].values():
                if isinstance(date, str) and date[:4].isdigit():
                    self.assertIn(date, text)
            self.assertIn('①', text)
            self.assertIn('②', text)
            self.assertIn('③', text)
        with patch.object(harness, '_args_backends', return_value=engines):
            _, resumed = self.capture(harness.cmd_plan, self.args('plan', '--caller', 'codex', '--resume-ticket',
                ticket['ticket_id'], '--plan-dir', str(self.root / 'plans')))
        self.assertEqual(resumed['streams'][0]['model_display'], native['model_display'])
        (home / 'config.toml').write_text('model = "fixture-next"\nmodel_context_window = 32768\n', encoding='utf-8')
        with patch.object(harness, '_args_backends', return_value=engines):
            _, refreshed = self.capture(harness.cmd_plan, self.args('plan', '--caller', 'codex', '--resume-ticket',
                ticket['ticket_id'], '--plan-dir', str(self.root / 'plans')))
        self.assertEqual(refreshed['streams'][0]['model_display'], 'inherit (fixture-next)')
        self.assertEqual(refreshed['streams'][0]['context_window'], 32768)

    def test_wp1b_plan_implicit_subtasks_preserve_complete_streams(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1\n', encoding='utf-8')
        args = self.args('plan', '--caller', 'codex', '--target', str(target), '--timeout', '60')
        streams, _ = harness._resolve_plan_streams(args, [
            {'channel': 'native_subagent', 'agent_type': 'readonly-reader'},
            {'channel': 'cae_harness_headless', 'engine': 'dsh:review'}], engines)
        for stream in streams:
            self.assertEqual(stream['subtask'], '检查目标')
            self.assertIn('quota_summary', stream)
            self.assertIn('receipt_location', stream)
        self.assertEqual([stream['headless'] for stream in streams], [False, True])
        args.workflow_goal = '核对接口定义'
        streams, _ = harness._resolve_plan_streams(args, [{'channel': 'native_workflow'}], engines)
        self.assertEqual(streams[0]['subtask'], '核对接口定义')

    def test_wp1b_native_models_environment_local_config_and_explicit_model(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        def plan(caller, channel='native_subagent', *extra):
            return self.capture(harness.cmd_plan, self.args('plan', '--caller', caller, '--channel', channel,
                '--agent-type', 'Explore', '--target', str(target), '--timeout', '60', *extra))[1]['streams'][0]
        with patch.object(harness, '_args_backends', return_value=engines), patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {'CLAUDE_CODE_SUBAGENT_MODEL': 'fixture-sub', 'ANTHROPIC_MODEL': 'fixture-parent'}):
                stream = plan('cc')
                self.assertEqual(stream['model_display'], 'inherit -> fixture-sub')
                self.assertEqual(stream['model'], 'fixture-sub')
                self.assertEqual(plan('cc', 'native_workflow', '--model', 'fixture-explicit')['model_display'], 'fixture-explicit')
            with patch.dict(os.environ, {'ANTHROPIC_MODEL': 'fixture-parent'}):
                self.assertEqual(plan('claude')['model_display'], 'inherit (fixture-parent)')
            agy_home = Path(engines['agy']['config_home'])
            (agy_home / 'config').mkdir(parents=True)
            (agy_home / 'config' / 'config.json').write_text(json.dumps({'userSettings': {'model': 'fixture-agy'}}), encoding='utf-8')
            for channel in ('native_subagent', 'native_workflow', 'native_swarm'):
                stream = plan('antigravity', channel, '--clean-room')
                self.assertEqual(stream['model_display'], 'inherit (fixture-agy)')
                self.assertEqual(stream['agent_type'], 'Explore')
                self.assertFalse(stream['headless'])
                self.assertFalse(stream['clean_room'])
            (agy_home / 'config' / 'config.json').unlink()
            self.assertEqual(plan('antigravity')['model_display'], 'inherit (跟随当前前端所选模型)')

    def test_wp1b_native_availability_quota_recheck_and_batch_refusal(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        plans = self.root / 'plans'
        common = ['--caller', 'codex', '--plan-dir', str(plans)]
        with patch.object(harness, '_args_backends', return_value=engines):
            _, ticket = self.capture(harness.cmd_plan, self.args('plan', *common, '--channel', 'native_subagent',
                '--target', str(target), '--timeout', '60', '--save-ticket'))
            self.capture(harness.cmd_plan, self.args('plan', *common, '--approve-ticket', ticket['ticket_id']))
            with patch.object(harness, 'invoke_engine') as never:
                with self.assertRaisesRegex(harness.HarnessError, '原生通道'):
                    harness.cmd_batch(self.args('batch', *common, '--ticket', ticket['ticket_id']))
                never.assert_not_called()
            harness._record_quota_exhausted('codex', engines['codex'], 'quota exceeded')
            _, resumed = self.capture(harness.cmd_plan, self.args('plan', *common, '--resume-ticket', ticket['ticket_id'], '--full-json'))
            self.assertFalse(resumed['streams'][0]['currently_available'])
            self.assertTrue(resumed['streams'][0]['quota_exceeded'])
            self.assertEqual(resumed['streams'][0]['client'], 'codex')
            for key in ('human_popup_markdown', 'handoff_prompt_block'):
                self.assertIn('quota_exhausted', resumed[key])
                self.assertIn(resumed['streams'][0]['quota_summary'], resumed[key])
            with self.assertRaises(harness.HarnessError) as caught:
                harness.cmd_plan(self.args('plan', *common, '--approve-ticket', ticket['ticket_id']))
            self.assertEqual(caught.exception.status, 'quota_exhausted')
            engines['codex']['available'] = False
            with patch.object(harness, '_active_quota_record', return_value=None):
                _, unavailable = self.capture(harness.cmd_plan, self.args('plan', *common, '--channel', 'native_swarm',
                    '--target', str(target), '--timeout', '60'))
            self.assertFalse(unavailable['streams'][0]['currently_available'])
            self.assertEqual(unavailable['streams'][0]['unavailable_reason'], 'backend_unavailable')

    def test_wp1b_plan_single_headless_write_access_and_lf_files(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        args = self.args('plan', '--caller', 'antigravity', '--engine', 'codex:default', '--model', 'fixture-code',
            '--channel', 'cae_harness_headless', '--agent-type', 'self', '--kind', 'code', '--target', str(target),
            '--write-scope', 'a.py', '--verify-cmd', 'python -B a.py', '--receipt-location', 'result.json',
            '--plan-dir', str(self.root / 'plans'), '--full-json')
        with patch.object(harness, '_args_backends', return_value=engines):
            _, ticket = self.capture(harness.cmd_plan, args)
        stream = ticket['streams'][0]
        self.assertTrue(stream['headless'])
        self.assertFalse(stream['clean_room'])
        self.assertFalse(stream['inline_readonly'])
        self.assertEqual(stream['model_display'], 'fixture-code')
        self.assertEqual(stream['write_scope'], ['a.py'])
        self.assertIn('a.py', stream['access_mode'])
        self.assertIn('python -B a.py', stream['access_mode'])
        for key in ('human_popup_markdown', 'handoff_prompt_block'):
            self.assertIn(stream['access_mode'], ticket[key])
            self.assertIn('result.json', ticket[key])
        for path in (SCRIPT, ROOT / 'template/user-guard/skills/cae-harness/fleet_profile.json', Path(__file__)):
            data = path.read_bytes()
            self.assertFalse(data.startswith(b'\xef\xbb\xbf'), str(path))
            self.assertNotIn(b'\r', data, str(path))
            data.decode('utf-8')

    def test_wp1b_gate_ignored_dirs_and_explicit_scope(self):
        for name in ('.pytest_cache', '.venv', 'runs'):
            (self.root / name).mkdir()
            (self.root / name / 'a.py').write_text('old', encoding='utf-8')
        traversed = []
        walk = os.walk
        def observed_walk(*args, **kwargs):
            for entry in walk(*args, **kwargs):
                traversed.append(Path(entry[0]).relative_to(self.root).as_posix())
                yield entry
        def worker(prompt, number):
            (self.root / 'allowed.py').write_text('x=1', encoding='utf-8')
            return 'done'
        with patch.object(harness.os, 'walk', side_effect=observed_walk):
            result = harness.run_physics_gate_loop(self.root, '改代码', 'verify', worker,
                lambda *_: (0, 'ok'), write_scope=['allowed.py'])
        self.assertTrue(result['gate_passed'])
        self.assertEqual(set(traversed), {'.'})
        def scoped_worker(prompt, number):
            (self.root / 'runs' / 'a.py').write_text('new', encoding='utf-8')
            return 'done'
        result = harness.run_physics_gate_loop(self.root, '改产物', 'verify', scoped_worker,
            lambda *_: (0, 'ok'), write_scope=['runs/a.py'])
        self.assertTrue(result['gate_passed'])
        self.assertEqual(result['worker_changed_files'], ['runs/a.py'])

    def test_wp1b_fast_engine_syntax_matches_role_targets(self):
        self.assertTrue(harness._is_fast_engine_spec('role:fast'))
        for prefix in ('agy', 'codex', 'claude', 'dsh'):
            for suffix in ('fast', '@fast'):
                expected = suffix == '@fast' or prefix in ('claude', 'dsh')
                with self.subTest(engine=prefix + ':' + suffix):
                    self.assertEqual(harness._is_fast_engine_spec(prefix + ':' + suffix), expected)
                    self.assertEqual(harness._per_backend_role_target(prefix, suffix), 'fast' if expected else '')

    def test_wp1b_integer_process_timeout_and_client_name_model_exclusion(self):
        engines = backends()
        for elapsed, expected in ((0.01, 30), (0.99, 30), (1.01, 29), (7.8, 23), (29.9, 1)):
            def runner(command, cwd, input_text, timeout_sec, **kwargs):
                self.assertEqual(timeout_sec, expected)
                self.assertIsInstance(timeout_sec, int)
                Path(command[command.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch.object(harness.time, 'monotonic', side_effect=[0, elapsed, elapsed, elapsed]), \
                 patch.object(harness, 'run_process', side_effect=runner):
                self.assertEqual(harness.invoke_engine('codex:default', self.root, '审查', engines,
                    harness.TASK_JSON_SCHEMA, timeout_sec=30), TASK)
        for name in ('CC', 'DSH', 'Claude Code', 'Claude CLI', 'DeepSeek Harness'):
            for quote in ('', '`', '"'):
                self.assertEqual(harness._extract_prompt_model_hint(f'用 {quote}{name}{quote} 模型'), '')
        self.assertEqual(harness._extract_prompt_model_hint('用 `DeepSeek` Harness 模型'), '')
        self.assertEqual(harness._extract_prompt_model_hint('用 DeepSeek 模型'), 'DeepSeek')

    @patch.dict(os.environ, {'CAE_HARNESS_DEPTH': '', 'CAE_DISABLE_HEAVY_COMPLETIONS': ''})
    def test_wp1_batch_quota_fuse_queue_and_other_backend_timeout_isolation(self):
        engines = self.four_backends()
        target = self.root / 'target'
        target.mkdir()
        (target / 'a.py').write_text('x=1', encoding='utf-8')
        spec = self.root / 'spec.json'
        spec.write_text(json.dumps([{'stream_id': str(i), 'target': str(target), 'subtask': '审查',
            'engine': engine, 'timeout': 60} for i, engine in enumerate(['codex:review'] * 3 + ['dsh:review', 'agy:review'])]), encoding='utf-8')
        calls = []
        hd = {**harness._headless_tunables(), 'batch_max_workers_per_backend': {p: 1 for p in engines}}
        def invoke(spec, *args, **kwargs):
            prefix = harness._engine_parts(spec)[0]
            calls.append(prefix)
            if prefix == 'codex':
                raise harness.HarnessError('quota exceeded', 'quota_exhausted')
            if prefix == 'dsh':
                raise harness.HarnessError('deadline', 'timeout')
            return dict(TASK)
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_headless_tunables', return_value=hd), patch.object(harness, 'invoke_engine', side_effect=invoke):
            code, result = self.capture(harness.cmd_batch, self.args('batch', '--caller', 'antigravity', '--spec-file', str(spec),
                '--plan-dir', str(self.root / 'state' / 'plans')))
        self.assertEqual(code, 3)
        self.assertEqual(calls.count('codex'), 1)
        self.assertEqual([s['status'] for s in result['streams']], ['quota_exhausted'] * 3 + ['timeout', 'ok'])
        self.assertIsNotNone(harness._active_quota_record('codex', engines['codex']))
        self.assertTrue(Path(result['receipt_file']).is_file())

    def test_wp1_ticket_invalid_expired_tampered_and_pending_rejected(self):
        plans = self.root / 'plans'
        plans.mkdir()
        base = {'ticket_id': 'fixture', 'expires_at': time.time() + 60, 'status': 'PENDING', 'streams': []}
        base['plan_hash'] = harness._plan_hash(base)
        path = plans / 'fixture.json'
        for change in ({}, {'status': 'APPROVED', 'expires_at': 0}, {'status': 'APPROVED', 'plan_hash': 'changed'}):
            path.write_text(json.dumps({**base, **change}), encoding='utf-8')
            with patch.object(harness, '_args_backends') as never, self.assertRaises(harness.HarnessError) as caught:
                harness.cmd_batch(self.args('batch', '--ticket', 'fixture', '--plan-dir', str(plans)))
            self.assertEqual(caught.exception.status, 'ticket_invalid')
            never.assert_not_called()
        with self.assertRaises(harness.HarnessError):
            harness._load_ticket('../escape', plans)

    def test_wp1_token_usage_extraction_and_no_fabricated_totals(self):
        claude = harness.extract_cli_token_usage('claude', json.dumps({'type': 'result', 'usage': {
            'input_tokens': 10, 'cache_creation_input_tokens': 3, 'cache_read_input_tokens': 5, 'output_tokens': 2},
            'total_cost_usd': 0.1, 'modelUsage': {'model': {}}}))
        self.assertEqual((claude['input_tokens'], claude['cached_input_tokens'], claude['output_tokens'], claude['total_tokens']), (18, 5, 2, 20))
        events = '\n'.join(json.dumps(event) for event in [
            {'usage': {'prompt_tokens': 2, 'completion_tokens': 1}},
            {'type': 'final', 'tokenUsage': {'prompt_tokens': 9, 'completion_tokens': 4, 'cache_hit_tokens': 3, 'total_tokens': 13}}])
        dsh = harness.extract_cli_token_usage('dsh', events)
        self.assertEqual((dsh['input_tokens'], dsh['total_tokens']), (9, 13))
        codex = harness.extract_cli_token_usage('codex', '{"type":"turn.completed","usage":{"input_tokens":8,"cached_input_tokens":2,"output_tokens":3}}')
        self.assertEqual(codex['total_tokens'], 11)
        for prefix in ('codex', 'agy', 'dsh', 'claude'):
            unknown = harness.extract_cli_token_usage(prefix, 'plain text')
            self.assertEqual(unknown['usage_source'], 'unreported_by_cli')
            self.assertIsNone(unknown['total_tokens'])
        partial = harness.extract_cli_token_usage('agy', '{"usage":{"input_tokens":3}}')
        self.assertIsNone(partial['output_tokens'])
        self.assertIsNone(partial['total_tokens'])

    def test_wp1_wall_clock_staging_timeout_prevents_launch_and_flags_not_suspended(self):
        engines = self.four_backends()
        (self.root / 'a.py').write_text('x=1', encoding='utf-8')
        with patch.object(harness.time, 'monotonic', side_effect=[0, 11, 11]), \
             patch.object(harness, 'run_process') as never, self.assertRaises(harness.HarnessError) as caught:
            harness.invoke_engine('codex:default', self.root, '读', engines, harness.TASK_JSON_SCHEMA, timeout_sec=10)
        self.assertEqual(caught.exception.status, 'timeout')
        never.assert_not_called()
        if os.name == 'nt':
            self.assertFalse(harness._process_options()['creationflags'] & 0x00000004)

    @patch.dict(os.environ, {'CAE_HARNESS_DEPTH': '', 'CAE_DISABLE_HEAVY_COMPLETIONS': ''})
    def test_wp1_heavy_receipt_disable_dryrun_redaction_and_atomic_output(self):
        args = self.args('batch', '--session-id', 'session1', '--plan-dir', str(self.root / 'state' / 'plans'))
        harness._begin_dispatch(args)
        sample_lan_ip = '192.' + '168.1.2'
        payload = {'status': 'ok', 'streams': [{'stream_id': 'S1', 'engine': 'codex:test', 'status': 'ok',
            'invocation': {'argv': [str(self.root / 'cae-harness-staging'), 'http://' + sample_lan_ip], 'total_cost_usd': 1.234}}]}
        _, result = self.capture(lambda unused: harness._dispatch_print(args, payload), args)
        self.assertTrue(Path(result['receipt_file']).is_file())
        public = Path(result['receipt_file']).read_text(encoding='utf-8')
        self.assertNotIn(sample_lan_ip, public)
        self.assertNotIn('1.234', public)
        self.assertNotIn(str(self.root), public)
        scrubbed = harness._redact_public({
            '/mnt/c/Users/<user>/proj': 'HTTPS://u:p@example.com/v1?api_key=rawsecret123',
            'api_key': 'plain_unprefixed_secret',
            'secret_key': 'plain_secret_key_val',
            'secretKey': 'plain_camel_secret_val',
            'ipv6_note': 'peer FD00' + '::1 and fe80' + '::1 connected',
        })
        self.assertIn('<HOME>/proj', scrubbed)
        self.assertEqual(scrubbed['api_key'], '<redacted>')
        self.assertEqual(scrubbed['secret_key'], '<redacted>')
        self.assertEqual(scrubbed['secretKey'], '<redacted>')
        self.assertNotIn('rawsecret123', json.dumps(scrubbed))
        self.assertEqual(scrubbed['ipv6_note'], 'peer <PRIVATE_IP> and <PRIVATE_IP> connected')
        self.assertIsNone(harness._local_fallback_engine('antigravity', 'codex:default', self.four_backends(), '用 codex 跑这次推导'))
        self.assertIsNone(harness._local_fallback_engine('antigravity', 'codex:gpt-6-astra', self.four_backends(), '请交给 gpt-6-astra 推导'))
        self.assertEqual(result['token_usage_summary']['unreported_streams'], 1)
        dry = self.args('batch', '--dry-run', '--plan-dir', str(self.root / 'never'))
        _, result = self.capture(lambda unused: harness._dispatch_print(dry, {'status': 'ok', 'streams': []}), dry)
        self.assertNotIn('receipt_file', result)
        self.assertFalse((self.root / 'never').exists())


    def test_wp6a_probe_disabled_agents_and_all_models_keeps_static_budgets(self):
        engines = self.four_backends()
        engines['codex']['models'][0]['context_window'] = 64000
        for info in engines.values():
            info['models'][0]['context_window'] = 64000
        base = json.loads(json.dumps(harness._fleet_profile()))
        for name in engines:
            for disable_agent in (True, False):
                profile = json.loads(json.dumps(base))
                if disable_agent:
                    profile['subscription_matrix']['agents'].setdefault(name, {})['enabled'] = False
                else:
                    for record in engines[name]['models']:
                        profile['subscription_matrix']['models'].setdefault(record['slug'], {})['enabled'] = False
                with self.subTest(backend=name, agent=disable_agent), \
                     patch.object(harness, '_fleet_profile', return_value=profile), \
                     patch.object(harness, '_find_binary', side_effect=lambda name: name), \
                     patch.object(harness, '_codex_home', return_value=(self.root / 'codex', False)), \
                     patch.object(harness, '_read_codex_catalog', return_value=(engines['codex']['models'], {})), \
                     patch.object(harness, '_agy_info', return_value=dict(engines['agy'])), \
                     patch.object(harness, '_claude_info', return_value=dict(engines['claude'])), \
                     patch.object(harness, '_dsh_info', return_value=dict(engines['dsh'])):
                    actual = harness.probe_backends(refresh_model_cache=False)
                    self.assertEqual(set(actual), set(engines))
                    self.assertFalse(actual[name]['auto_selectable'])
                    self.assertEqual(actual[name]['subscription_enabled'], not disable_agent)
                    self.assertIsNotNone(actual[name]['frontier_model'])
                    self.assertGreater(actual[name]['safe_input_budget_tokens'], 0)
                    with self.assertRaises(harness.HarnessError):
                        harness.resolve_execution_profile(actual, name + ':' + engines[name]['models'][0]['slug'])

    def test_wp6a_p0_client_model_and_role_constraints(self):
        engines = self.four_backends()
        # 同一个模型也在其他端出现，必须遵守原话指定的客户端。
        engines['codex']['models'].append({'slug': 'gpt-fixture', 'provider': 'openai'})
        engines['claude']['models'].append({'slug': 'gpt-fixture', 'provider': 'claude_configured'})
        self.assertEqual(harness._prompt_engine_spec(engines, '请用 CC，使用 gpt-fixture 模型审查'), 'claude:gpt-fixture')
        self.assertEqual(harness._prompt_engine_spec(engines, '请用 DSH，使用 gpt-fixture 模型审查'), 'dsh:gpt-fixture')
        engines['claude'].update(model='deepseek-flash[1M]', models=[{'slug': 'deepseek-flash[1M]'}])
        for role in ('review', 'math', 'code', 'orchestrator'):
            engine = harness.choose_engine(engines, 'codex', 'role:' + role, user_prompt='请用 CC 审查')
            self.assertEqual(engine, 'claude:@' + role)
            profile = harness.resolve_execution_profile(engines, engine, tier='low')
            with self.assertRaisesRegex(harness.HarnessError, '轻量模型'):
                harness._require_execution_profile(profile)

    def test_wp8_backend_prompt_preserves_explicit_role_and_audit_rejects_light_card(self):
        engines = self.four_backends()
        engines['claude'].update(model='deepseek-flash[1M]', models=[{'slug': 'deepseek-flash[1M]'}])
        for explicit in ('claude:@review', 'codex:math', 'claude:code'):
            with self.subTest(explicit=explicit):
                engine = harness.choose_engine(engines, 'codex', explicit, user_prompt='请用 CC 审查')
                slot = harness._per_backend_role_target(*harness._engine_parts(explicit))
                self.assertEqual(engine, 'claude:@' + slot)
                with self.assertRaisesRegex(harness.HarnessError, '轻量模型'):
                    harness._require_execution_profile(harness.resolve_execution_profile(engines, engine, tier='low'))
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        for left in ('', 'role:review', 'claude:@review'):
            with self.subTest(left=left), patch.object(harness, '_args_backends', return_value=engines), \
                 patch.object(harness, '_prepare_execution'), patch.object(harness, 'invoke_readonly_engine') as invoke:
                args = self.args('audit', '--target', str(target), '--caller', 'codex', '--single', '--left', left,
                                 '--user-prompt', '请用 CC 审查')
                with self.assertRaisesRegex(harness.HarnessError, '轻量模型'):
                    harness.cmd_audit(args)
                invoke.assert_not_called()

    def test_wp8_plan_explicit_model_survives_tier_or_economy_prompt(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        for prompt in ('使用 low 挡', '省额度，只看拼写'):
            with self.subTest(prompt=prompt), patch.object(harness, '_args_backends', return_value=engines):
                _, result = self.capture(harness.cmd_plan, self.args('plan', '--caller', 'codex', '--target', str(target),
                    '--engine', 'claude:default', '--model', 'deepseek-v4-pro', '--user-prompt', prompt, '--timeout', '60'))
                self.assertEqual(result['streams'][0]['model'], 'deepseek-v4-pro')
                self.assertEqual(result['streams'][0]['tier'], 'low')

    def test_wp6a_effective_tier_all_entry_points_and_ticket_prompt_recovery(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1\n', encoding='utf-8')
        for prompt, expected in (('请用 CC 审查', 'medium'), ('请用 CC，使用 low 挡', 'low'),
                                 ('请用 CC，省额度，只看拼写', 'low')):
            tier, _ = harness._resolve_effective_tier('medium', 'review', prompt, '检查')
            self.assertEqual(tier, expected)
            for command in ('plan', 'task', 'gate', 'audit'):
                tokens = [command, '--caller', 'codex', '--target', str(target), '--tier', 'medium',
                          '--user-prompt', prompt, '--dry-run', '--plan-dir', str(self.root / 'plans')]
                if command == 'audit':
                    tokens += ['--single', '--left', 'role:review']
                if command == 'task':
                    tokens += ['--kind', 'review', '--engine', 'claude:default']
                if command == 'gate':
                    tokens += ['--verify-cmd', 'verify', '--worker', 'claude:default']
                if command in ('task', 'gate'):
                    tokens += ['--task', '检查目标']
                with self.subTest(command=command, prompt=prompt), \
                     patch.object(harness, '_args_backends', return_value=engines):
                    _, result = self.capture(getattr(harness, 'cmd_' + command), self.args(*tokens))
                    # gate 的省额度常规任务按自适应规则选 high。
                    expected_tier = 'high' if command == 'gate' and '省额度' in prompt else expected
                    if command == 'audit':
                        self.assertEqual(result['plan']['selected_tier'], expected_tier)
                        self.assertEqual(result['plan']['execution_profiles']['left']['tier'], expected_tier)
                    else:
                        self.assertEqual(result['streams'][0]['tier'] if command == 'plan' else result['selected_tier'], expected_tier)
        plans = self.root / 'saved-plans'
        common = ['--caller', 'codex', '--target', str(target), '--plan-dir', str(plans), '--timeout', '60']
        with patch.object(harness, '_args_backends', return_value=engines):
            _, ticket = self.capture(harness.cmd_plan, self.args('plan', *common, '--save-ticket', '--tier', 'max',
                '--user-prompt', '请用 CC，使用 low 挡'))
            self.assertEqual(ticket['user_prompt'], '请用 CC，使用 low 挡')
            _, resumed = self.capture(harness.cmd_plan, self.args('plan', *common, '--resume-ticket', ticket['ticket_id']))
            _, approved = self.capture(harness.cmd_plan, self.args('plan', *common, '--approve-ticket', ticket['ticket_id']))
        self.assertEqual(resumed['streams'][0]['tier'], 'low')
        self.assertEqual(approved['status'], 'APPROVED')
        self.assertEqual(approved['streams'][0]['tier'], 'low')

    def test_wp6a_approval_routing_drift_stays_pending_then_can_be_reapproved(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        profile = json.loads(json.dumps(harness._fleet_profile()))
        profile['roles']['review']['candidates'] = ['codex:review', 'dsh:review']
        plans = self.root / 'plans'
        common = ['--caller', 'antigravity', '--target', str(target), '--plan-dir', str(plans), '--timeout', '60']
        with patch.object(harness, '_fleet_profile', return_value=profile), \
             patch.object(harness, '_args_backends', return_value=engines):
            _, ticket = self.capture(harness.cmd_plan, self.args('plan', *common, '--engine', 'role:review', '--save-ticket'))
            sessions = Path(engines['codex']['config_home']) / 'sessions'
            sessions.mkdir(parents=True)
            (sessions / 'rollout-limit.jsonl').write_text(json.dumps({'payload': {'rate_limits': {
                'primary': {'used_percent': 100, 'resets_at': time.time() + 3600}}}}), encoding='utf-8')
            for extra in ([], ['--override-json', json.dumps({'S1': {'timeout': 90}})]):
                if extra:
                    # 恢复原待批选路，确认 override 也不能掩盖随后发生的漂移。
                    harness._save_plan(ticket, plans)
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.cmd_plan(self.args('plan', *common, '--approve-ticket', ticket['ticket_id'], *extra))
                self.assertEqual(caught.exception.status, 'ticket_changed')
                pending = harness._load_ticket(ticket['ticket_id'], plans)
                self.assertEqual(pending['status'], 'PENDING')
                self.assertEqual(pending['streams'][0]['engine'], 'dsh:review')
                _, approved = self.capture(harness.cmd_plan, self.args('plan', *common, '--approve-ticket', ticket['ticket_id']))
                self.assertEqual(approved['status'], 'APPROVED')

    def test_wp6a_approval_explicit_channel_availability_drift(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        plans = self.root / 'plans'
        common = ['--caller', 'codex', '--target', str(target), '--plan-dir', str(plans), '--timeout', '60']
        with patch.object(harness, '_args_backends', return_value=engines):
            _, ticket = self.capture(harness.cmd_plan, self.args('plan', *common, '--engine', 'claude:default', '--save-ticket'))
            engines['claude']['available'] = False
            with self.assertRaises(harness.HarnessError) as caught:
                harness.cmd_plan(self.args('plan', *common, '--approve-ticket', ticket['ticket_id']))
            self.assertEqual(caught.exception.status, 'ticket_changed')
            pending = harness._load_ticket(ticket['ticket_id'], plans)
            self.assertEqual(pending['status'], 'PENDING')
            self.assertEqual(pending['streams'][0]['engine'], 'claude:default')
            self.assertFalse(pending['streams'][0]['currently_available'])

    def test_wp6a_latest_rate_event_across_rollouts_and_unknown_quota(self):
        engines = self.four_backends()
        for prefix in engines:
            quota = harness.inspect_backend_quota_and_availability(prefix, engines[prefix])
            self.assertTrue(quota['currently_available'])
            self.assertFalse(quota['quota_exceeded'])
            self.assertIn('no_local_cooldown (remaining_unknown)', quota['quota_summary'])
        sessions = Path(engines['codex']['config_home']) / 'sessions'
        sessions.mkdir(parents=True)
        reset = int(time.time()) + 3600
        def event(stamp, percent):
            return {'timestamp': stamp, 'payload': {'rate_limits': {'primary': {
                'used_percent': percent, 'window_minutes': 300, 'resets_at': reset}}}}
        newer = sessions / 'rollout-newer-event.jsonl'
        older = sessions / 'rollout-older-event.jsonl'
        newer.write_text(json.dumps(event('2020-01-02T00:00:00Z', 25)), encoding='utf-8')
        older.write_text(json.dumps(event('2020-01-01T00:00:00Z', 100)), encoding='utf-8')
        os.utime(newer, (10, 10))
        os.utime(older, (20, 20))
        quota = harness.inspect_backend_quota_and_availability('codex', engines['codex'])
        self.assertFalse(quota['quota_exceeded'])
        self.assertEqual(quota['resets_at'], reset)
        self.assertIn('within_quota', quota['quota_summary'])
        self.assertIn('5h_window:25%', quota['quota_summary'])
        self.assertIn('resets_at=' + str(reset), quota['quota_summary'])
        newer.write_text(json.dumps({'payload': {'rate_limits': {'primary': {'used_percent': 30, 'resets_at': reset}}}}), encoding='utf-8')
        os.utime(older, (1, 1))
        self.assertIn('primary:30%', harness.inspect_backend_quota_and_availability('codex', engines['codex'])['quota_summary'])
        for window, expected in (({'resets_at': reset}, False),
                                 ({'resets_at': reset, 'spend_control_reached': True}, True),
                                 ({'resets_at': reset, 'used_percent': 101}, True)):
            newer.write_text(json.dumps({'payload': {'rate_limits': {'primary': window}}}), encoding='utf-8')
            quota = harness.inspect_backend_quota_and_availability('codex', engines['codex'])
            self.assertEqual(quota['quota_exceeded'], expected)
            self.assertEqual(quota['resets_at'], reset)
            self.assertIn('resets_at=' + str(reset), quota['quota_summary'])
            if not expected:
                self.assertIn('remaining_unknown', quota['quota_summary'])

    def test_wp6a_approval_explicit_engine_override_and_unrequested_model_drift(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        plans = self.root / 'plans'
        common = ['--caller', 'codex', '--target', str(target), '--plan-dir', str(plans), '--timeout', '60']
        with patch.object(harness, '_args_backends', return_value=engines):
            _, ticket = self.capture(harness.cmd_plan, self.args('plan', *common, '--engine', 'claude:default', '--save-ticket'))
            _, approved = self.capture(harness.cmd_plan, self.args('plan', *common, '--approve-ticket', ticket['ticket_id'],
                '--override-json', json.dumps({'S1': {'engine': 'dsh:@review'}})))
            self.assertEqual(approved['status'], 'APPROVED')
            self.assertEqual(approved['streams'][0]['engine'], 'dsh:@review')
            _, ticket = self.capture(harness.cmd_plan, self.args('plan', *common, '--engine', 'claude:default', '--save-ticket'))
            engines['claude'].update(model='fixture-changed', models=[{'slug': 'fixture-changed'}])
            for override in ({}, {'tier': 'high'}):
                harness._save_plan(ticket, plans)
                extra = ['--override-json', json.dumps({'S1': override})] if override else []
                with self.subTest(override=override), self.assertRaises(harness.HarnessError) as caught:
                    harness.cmd_plan(self.args('plan', *common, '--approve-ticket', ticket['ticket_id'], *extra))
                self.assertEqual(caught.exception.status, 'ticket_changed')
                pending = harness._load_ticket(ticket['ticket_id'], plans)
                self.assertEqual(pending['status'], 'PENDING')
                self.assertEqual(pending['streams'][0]['model'], 'fixture-changed')

    def test_wp6a_ccswitch_configured_db_and_quantitative_evidence(self):
        info = self.four_backends()['claude']
        cfg = Path(info['config_home']) / 'router'
        cfg.mkdir(parents=True)
        db = self.root / 'configured.db'
        other = self.root / 'override.db'
        (cfg / 'router.json').write_text(json.dumps({'ccswitch_db': str(db)}), encoding='utf-8')
        for path, scheme in ((db, 'token_plan'), (other, 'balance')):
            with contextlib.closing(sqlite3.connect(path)) as con, con:
                con.execute('CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, is_current INTEGER, meta TEXT)')
                con.execute('INSERT INTO providers VALUES (?, ?, ?, ?, ?)',
                            ('card', 'claude', 'Fixture', 1, json.dumps({'usage_script': {'templateType': scheme}})))
        with patch.dict(os.environ, {}, clear=True):
            quota = harness.inspect_backend_quota_and_availability('claude', info)
            self.assertIn('token_plan:no_local_cooldown (remaining_unknown)', quota['quota_summary'])
            with patch.dict(os.environ, {'CAE_CCSWITCH_DB': str(other)}):
                self.assertEqual(harness._claude_card_quota(info, '')['quota_scheme'], 'balance')
            with contextlib.closing(sqlite3.connect(db)) as con, con:
                con.execute('UPDATE providers SET meta=?', (json.dumps({'limit_daily_usd': 5}),))
            self.assertIn('remaining_unknown', harness.inspect_backend_quota_and_availability('claude', info)['quota_summary'])
            with contextlib.closing(sqlite3.connect(db)) as con, con:
                con.execute('CREATE TABLE usage_daily_rollups (provider_id TEXT, date TEXT, total_cost_usd REAL)')
                con.execute('INSERT INTO usage_daily_rollups VALUES (?, ?, ?)', ('card', datetime.date.today().isoformat(), 2))
            quota = harness.inspect_backend_quota_and_availability('claude', info)
            self.assertIn('within_quota', quota['quota_summary'])
            self.assertFalse(quota['quota_exceeded'])

    def test_wp6a_native_settings_subagent_and_model_specific_window(self):
        engines = self.four_backends()
        home = Path(engines['claude']['config_home'])
        home.mkdir(parents=True)
        (home / 'settings.json').write_text(json.dumps({'env': {'CLAUDE_CODE_SUBAGENT_MODEL': 'fixture-sub'}}), encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(harness._native_plan_model('claude', engines['claude'], 'inherit')[0], 'fixture-sub')
            with patch.dict(os.environ, {'CLAUDE_CODE_SUBAGENT_MODEL': 'fixture-env'}):
                self.assertEqual(harness._native_plan_model('claude', engines['claude'], 'inherit')[0], 'fixture-env')
        home = Path(engines['codex']['config_home'])
        home.mkdir(parents=True)
        (home / 'config.toml').write_text('model = "fixture-code"\nmodel_context_window = 80000\n', encoding='utf-8')
        engines['codex']['models'] += [{'slug': 'fixture-other', 'context_window': 32000}]
        args = self.args('plan', '--caller', 'codex', '--target', str(self.root), '--channel', 'native_subagent')
        for model, expected in (('inherit', 80000), ('fixture-code', 80000), ('fixture-other', 32000)):
            streams, _ = harness._resolve_plan_streams(args, [{'model': model}], engines)
            self.assertEqual(streams[0]['context_window'], expected)

    def test_wp6a_dsh_failed_help_is_uncached_and_conservative(self):
        binary = self.root / 'dsh'
        binary.write_bytes(b'fixture')
        for outcome in (OSError('unavailable'), subprocess.TimeoutExpired('dsh', 1),
                        subprocess.CompletedProcess([], 1, 'Usage: --permission-mode', ''),
                        subprocess.CompletedProcess([], 0, 'unrecognized response', '')):
            with self.subTest(outcome=outcome), patch.object(harness, '_DSH_PERMISSION_CACHE', {}), \
                 patch.object(harness, 'run_process') as run:
                if isinstance(outcome, Exception):
                    run.side_effect = outcome
                else:
                    run.return_value = outcome
                self.assertFalse(harness._dsh_supports_cli_permission_flags(binary))
                self.assertEqual(harness._DSH_PERMISSION_CACHE, {})
                run.side_effect = None
                run.return_value = subprocess.CompletedProcess([], 0, 'Usage: dsh --permission-mode', '')
                self.assertTrue(harness._dsh_supports_cli_permission_flags(binary))
                self.assertTrue(harness._dsh_supports_cli_permission_flags(binary))
                self.assertEqual(run.call_count, 2)
        with patch.object(harness, '_DSH_PERMISSION_CACHE', {}), patch.object(harness, 'run_process',
                return_value=subprocess.CompletedProcess([], 0, 'Usage: dsh --patch', '')) as run:
            self.assertFalse(harness._dsh_supports_cli_permission_flags(binary))
            self.assertFalse(harness._dsh_supports_cli_permission_flags(binary))
            self.assertEqual(run.call_count, 1)

    def test_wp6a_claude_model_usage_fallback_and_no_double_count(self):
        models = {'first': {'inputTokens': 10, 'outputTokens': 2, 'cacheReadInputTokens': 5, 'cacheCreationInputTokens': 3},
                  'second': {'inputTokens': 20, 'outputTokens': 4, 'cacheReadInputTokens': 7, 'cacheCreationInputTokens': 1}}
        for usage in ({}, {'input_tokens': 'unknown', 'output_tokens': None}):
            result = harness.extract_cli_token_usage('claude', json.dumps({'type': 'result', 'usage': usage, 'modelUsage': models}))
            self.assertEqual(result, {'input_tokens': 46, 'output_tokens': 6, 'cached_input_tokens': 12,
                                      'total_tokens': 52, 'usage_source': 'cli_reported'})
        result = harness.extract_cli_token_usage('claude', json.dumps({'type': 'result', 'usage': {
            'input_tokens': 1, 'output_tokens': 2}, 'modelUsage': models}))
        self.assertEqual(result['total_tokens'], 3)

    def test_wp6a_batch_gate_requires_verification_and_staged_scope_is_checked(self):
        engines = self.four_backends()
        target = self.root / 'target'
        target.mkdir()
        (target / 'a.py').write_text('x=1', encoding='utf-8')
        spec = self.root / 'spec.json'
        common = ['--caller', 'antigravity', '--spec-file', str(spec), '--plan-dir', str(self.root / 'plans')]
        base = {'target': str(target), 'engine': 'codex:review', 'timeout': 60}
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            for command in ('', '   '):
                spec.write_text(json.dumps([{**base, 'kind': 'gate', 'write_scope': ['a.py'], 'verify_cmd': command}]), encoding='utf-8')
                with patch.object(harness, 'invoke_engine') as invoke, patch.object(harness, 'run_process') as verify:
                    code, result = self.capture(harness.cmd_batch, self.args('batch', *common))
                self.assertEqual(code, 3)
                self.assertEqual(result['streams'][0]['status'], 'failed')
                self.assertIn('批处理 gate 任务必须声明非空 verify_cmd', result['streams'][0]['error'])
                invoke.assert_not_called()
                verify.assert_not_called()
            for clean_room in (False, True):
                spec.write_text(json.dumps([{**base, 'kind': 'review', 'clean_room': clean_room}]), encoding='utf-8')
                def staged_edit(*args, receipt, **kwargs):
                    receipt['staged_readonly_workspace'] = True
                    receipt['staged_scope'] = {'scope_verified': True, 'scope_violations': ['a.py'], 'changed_files': ['a.py']}
                    return dict(TASK)
                with patch.object(harness, 'invoke_engine', side_effect=staged_edit):
                    code, result = self.capture(harness.cmd_batch, self.args('batch', *common))
                self.assertEqual(code, 3)
                self.assertEqual(result['streams'][0]['status'], 'scope_violation')
                self.assertEqual(result['streams'][0]['scope_violations'], ['a.py'])
                self.assertEqual((target / 'a.py').read_text(encoding='utf-8'), 'x=1')
            spec.write_text(json.dumps([{**base, 'kind': 'gate', 'write_scope': ['a.py'], 'verify_cmd': 'verify'}]), encoding='utf-8')
            with patch.object(harness, 'invoke_engine', return_value=dict(TASK)), \
                 patch.object(harness, 'run_process', return_value=subprocess.CompletedProcess([], 1, '', '')) as verify:
                _, result = self.capture(harness.cmd_batch, self.args('batch', *common))
            self.assertEqual(result['streams'][0]['status'], 'verification_failed')
            self.assertTrue(result['streams'][0]['verification_executed'])
            verify.assert_called_once()

    def test_wp6a_batch_dry_run_no_cooldown_writes_and_retry_ignores_cached_cooldown(self):
        engines = self.four_backends()
        target = self.root / 'a.py'
        target.write_text('x=1', encoding='utf-8')
        args = self.args('batch', '--caller', 'antigravity', '--plan-dir', str(self.root / 'plans'),
                         '--user-prompt', '省额度，只看拼写')
        streams, _ = harness._resolve_plan_streams(args, [{'target': str(target), 'engine': 'codex:review'}], engines)
        ticket = {'streams': streams, 'user_prompt': '省额度，只看拼写'}
        harness._record_quota_exhausted('codex', engines['codex'], 'quota exceeded')
        with patch.object(harness, '_begin_dispatch', side_effect=lambda args: setattr(args, '_approved_ticket', ticket)), \
             patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_record_quota_exhausted') as record, \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            with patch.object(harness, 'invoke_engine') as invoke:
                _, result = self.capture(harness.cmd_batch, self.args('batch', '--dry-run'))
                self.assertEqual(result['streams'][0]['status'], 'quota_exhausted')
                record.assert_not_called()
                invoke.assert_not_called()
            def run(*args, profile, batch, **kwargs):
                self.assertTrue(batch.retry)
                self.assertEqual(profile['tier'], 'low')
                return dict(TASK)
            with patch.object(harness, 'invoke_engine', side_effect=run):
                _, result = self.capture(harness.cmd_batch, self.args('batch', '--retry-quota'))
            self.assertEqual(result['streams'][0]['status'], 'ok')
            record.assert_not_called()
        retry_args = self.args('batch', '--caller', 'antigravity', '--target', str(target), '--retry-quota')
        role_profile = json.loads(json.dumps(harness._fleet_profile()))
        role_profile['roles']['review']['candidates'] = ['codex:review']
        with patch.object(harness, '_fleet_profile', return_value=role_profile):
            streams, _ = harness._resolve_plan_streams(retry_args, [{'engine': 'role:review'}], engines)
        self.assertEqual(streams[0]['engine'], 'codex:review')

    def test_wp6a_batch_backend_pools_cannot_starve_other_backend(self):
        engines = self.four_backends()
        target = self.root / 'target'
        target.mkdir()
        (target / 'a.py').write_text('x=1', encoding='utf-8')
        spec = self.root / 'spec.json'
        specs = ['codex:review'] * 8 + ['dsh:review']
        spec.write_text(json.dumps([{'stream_id': str(index), 'target': str(target), 'engine': engine,
                                    'subtask': '核对', 'timeout': 60} for index, engine in enumerate(specs)]), encoding='utf-8')
        other_started = threading.Event()
        counts, lock = {}, threading.Lock()
        def invoke(engine, *args, **kwargs):
            prefix, _ = harness._engine_parts(engine)
            with lock:
                counts[prefix] = counts.get(prefix, 0) + 1
            if prefix == 'codex':
                self.assertTrue(other_started.wait(3), '其他后端被同端排队流阻塞')
            else:
                other_started.set()
            with lock:
                counts[prefix] -= 1
            return dict(TASK)
        hd = {**harness._headless_tunables(), 'batch_max_workers_per_backend': {prefix: 1 for prefix in engines}}
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_headless_tunables', return_value=hd), \
             patch.object(harness, 'invoke_engine', side_effect=invoke), \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            _, result = self.capture(harness.cmd_batch, self.args('batch', '--caller', 'antigravity',
                '--spec-file', str(spec), '--plan-dir', str(self.root / 'plans')))
        self.assertTrue(all(stream['succeeded'] for stream in result['streams']))
        self.assertEqual([stream['stream_id'] for stream in result['streams']], [str(i) for i in range(len(specs))])


    def test_v1152_latex_escapes_batch_workspace_contention_and_handed_off_resume(self):
        raw_latex = (
            '{"win_root": "C:\\\\", "lines": "row1\\nrow2\\tcol2", '
            '"pde": "\\alpha + \\beta + \\frac{\\partial \\rho}{\\partial t} + \\nabla \\cdot \\tau + \\theta \\times \\text{const} + \\right)"}'
        )
        parsed = harness._loads_relaxed_dict(raw_latex)
        self.assertEqual(parsed['win_root'], 'C:\\')
        self.assertEqual(parsed['lines'], 'row1\nrow2\tcol2')
        for macro in ('\\alpha', '\\beta', '\\frac', '\\rho', '\\nabla', '\\tau', '\\theta', '\\times', '\\text{const}', '\\right)'):
            self.assertIn(macro, parsed['pde'])

        engines = self.four_backends()
        proj = self.root / 'shared-ws'
        proj.mkdir()
        (proj / 'a.py').write_text('x = 1\n', encoding='utf-8')
        (proj / 'b.py').write_text('y = 2\n', encoding='utf-8')
        spec = self.root / 'shared_spec.json'
        spec.write_text(json.dumps([
            {'stream_id': 'S1', 'channel': 'cae_harness_headless', 'kind': 'code', 'engine': 'codex:code',
             'target': str(proj), 'write_scope': ['a.py'], 'subtask': '修改 a.py'},
            {'stream_id': 'S2', 'channel': 'cae_harness_headless', 'kind': 'review', 'engine': 'dsh:review',
             'target': str(proj / 'b.py'), 'subtask': '审查 b.py'},
        ]), encoding='utf-8')

        def invoke_shared(spec_str, root, prompt, backends_arg, schema, readonly=True, *a, **kw):
            if not readonly:
                time.sleep(0.03)
                (Path(root) / 'a.py').write_text('x = 2\n', encoding='utf-8')
            else:
                time.sleep(0.03)
            return dict(TASK)

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'invoke_engine', side_effect=invoke_shared), \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            rc, batch_out = self.capture(harness.cmd_batch, self.args(
                'batch', '--caller', 'antigravity', '--spec-file', str(spec), '--plan-dir', str(self.root / 'plans')))
        self.assertEqual(rc, 0)
        self.assertEqual([s['status'] for s in batch_out['streams']], ['ok', 'ok'])

        plans = self.root / 'resume-plans'
        with patch.object(harness, '_args_backends', return_value=engines):
            _, ticket = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--target', str(proj / 'b.py'), '--engine', 'codex:review',
                '--session-id', 'old_sess', '--plan-dir', str(plans), '--save-ticket',
                '--workflow-bg', '背景', '--workflow-goal', '目标', '--workflow-impact', '只读'))
            tid = ticket['ticket_id']
            self.capture(harness.cmd_plan, self.args('plan', '--mark-handed-off', tid, '--plan-dir', str(plans)))
            with patch.object(harness, 'inspect_backend_quota_and_availability', return_value={
                'currently_available': True, 'unavailable_reason': '', 'quota_exceeded': False,
                'quota_scheme': 'rolling', 'quota_summary': '5h 已用 19%'
            }):
                _, resumed = self.capture(harness.cmd_plan, self.args('plan', '--resume-ticket', tid, '--plan-dir', str(plans)))
        self.assertEqual(resumed['status'], 'HANDED_OFF')
        self.assertEqual(json.loads((plans / 'session_old_sess_pending.json').read_text(encoding='utf-8'))['status'], 'HANDED_OFF')

    def test_v1152_scope_dir_globstar_plugin_data_align_and_router_defaults(self):
        # 1) --write-scope 目录前缀与零层 /**/ 通配匹配
        dir_scopes = harness._scope_patterns(['src/', 'include', 'lib/**/*.py'])
        for allowed in ('src/solver.py', 'src/sub/flux.py', 'src/', 'include/header.h', 'lib/top.py', 'lib/a/b/deep.py'):
            self.assertTrue(harness._scope_matches(allowed, dir_scopes), allowed)
        for forbidden in ('src_other/solver.py', 'include_bak/a.h', 'tests/top.py'):
            self.assertFalse(harness._scope_matches(forbidden, dir_scopes), forbidden)
        # 2) PLUGIN_DATA / USER_GUARD_STATE_DIR 与 resolve_dispatch_plan_dir 对齐
        custom_pd = self.root / 'custom-plugin-data'
        with patch.dict(os.environ, {'PLUGIN_DATA': str(custom_pd)}, clear=True):
            self.assertEqual(harness.resolve_dispatch_plan_dir(), (custom_pd / 'state' / 'dispatch_plans').resolve())
        custom_st = self.root / 'custom-state'
        with patch.dict(os.environ, {'PLUGIN_DATA': str(custom_pd), 'USER_GUARD_STATE_DIR': str(custom_st)}, clear=True):
            self.assertEqual(harness.resolve_dispatch_plan_dir(), (custom_st / 'dispatch_plans').resolve())
        # 3) 局部覆盖 router.json 合并默认路由表且 _claude_card_quota 展开简称别名
        cl_home = self.root / 'partial-router-claude'
        (cl_home / 'router').mkdir(parents=True)
        db_p = self.root / 'partial-cc.db'
        with contextlib.closing(sqlite3.connect(db_p)) as con, con:
            con.execute('CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, is_current INT, settings_config TEXT, meta TEXT)')
            con.execute('INSERT INTO providers VALUES (?,?,?,?,?,?)', ('k1', 'claude', 'Kimi', 0, json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'PASTE_KIMI_API_KEY_HERE'}}), json.dumps({'usage_script': {'templateType': 'token_plan'}})))
            con.execute('INSERT INTO providers VALUES (?,?,?,?,?,?)', ('d1', 'claude', 'DeepSeek', 0, json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'tok_ds_real_99'}}), json.dumps({'usage_script': {'templateType': 'balance'}})))
        (cl_home / 'router/router.json').write_text(json.dumps({'port': 8255, 'ccswitch_db': str(db_p)}), encoding='utf-8')
        r_info = {'config_home': str(cl_home), 'uses_local_router': True, 'model': 'k3.1-pro[1m]'}
        with self.assertRaisesRegex(harness.HarnessError, 'Kimi'):
            harness._verify_claude_router_card_key(r_info, 'kimi')
        harness._verify_claude_router_card_key(r_info, 'ds')
        self.assertEqual(harness._claude_card_quota(r_info, 'ds')['quota_scheme'], 'balance')

    def test_v1152_override_json_validation_snapshot_build_and_ticket_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'src').mkdir()
            (root / 'src' / 'safe.py').write_text('x = 1\n', encoding='utf-8')
            (root / 'src' / 'critical.py').write_text('y = 2\n', encoding='utf-8')
            (root / 'build').mkdir()
            (root / 'build' / 'verify.sh').write_text('exit 1\n', encoding='utf-8')

            # B5: build/ & authorized ignored dirs must not be blind-spotted
            scopes = harness._scope_patterns(['src/*'])
            gate_ignored = harness._snapshot_ignore_dirs_for(scopes, include_build=True)
            before = harness.snapshot_files(root, ignore_dirs=gate_ignored)
            (root / 'build' / 'verify.sh').write_text('exit 0\n', encoding='utf-8')
            rep = harness._safe_changes_report(before, root, scopes, readonly=False, ignore_dirs=gate_ignored)
            self.assertEqual(rep['changed_files'], ['build/verify.sh'])
            self.assertEqual(rep['scope_violations'], ['build/verify.sh'])

            # B7: _check_ticket_execution must reject tampered write_scope or verify_cmd
            class FakeArgs:
                timeout = 600
                _approved_ticket = {
                    'streams': [{
                        'channel': 'cae_harness_headless',
                        'target': str(root),
                        'kind': 'gate',
                        'execution_profile': {'spec': 'codex:gpt-5.4'},
                        'tier': 'max',
                        'timeout': 600,
                        'subtask': 'fix safe',
                        'write_scope': ['src/safe.py'],
                        'verify_cmd': 'pytest -q',
                    }]
                }
            prof = {'spec': 'codex:gpt-5.4', 'tier': 'max'}
            harness._check_ticket_execution(FakeArgs(), prof, root, 'gate', 'fix safe',
                                            write_scope=['src/safe.py'], verify_cmd='pytest -q')
            with self.assertRaisesRegex(harness.HarnessError, '写入范围.*src/safe.py'):
                harness._check_ticket_execution(FakeArgs(), prof, root, 'gate', 'fix safe',
                                                write_scope=['src/critical.py'], verify_cmd='pytest -q')
            with self.assertRaisesRegex(harness.HarnessError, '验收命令.*pytest -q'):
                harness._check_ticket_execution(FakeArgs(), prof, root, 'gate', 'fix safe',
                                                write_scope=['src/safe.py'], verify_cmd='true')

            # B4: cmd_plan --approve-ticket --override-json rejects unknown stream_id, null value, bogus tier
            plan_dir = root / 'plans'
            ticket = {
                'ticket_id': 't_b4_01',
                'status': 'PENDING',
                'created_at': 1.0,
                'expires_at': 9999999999.0,
                'session_id': 's1',
                'workflow_bg': 'bg',
                'user_prompt': '',
                'workflow_goal': 'goal',
                'workflow_impact': 'impact',
                'streams': [{
                    'stream_id': 'S1', 'subtask': 'check', 'target': str(root), 'kind': 'review',
                    'channel': 'cae_harness_headless', 'engine': 'codex:gpt-5.4', 'requested_engine': 'codex:gpt-5.4',
                    'requested_model': '', 'model': 'gpt-5.4', 'model_display': 'gpt-5.4', 'tier': 'max',
                    'timeout': 600, 'context_window': 200000, 'currently_available': True,
                    'unavailable_reason': None, 'quota_exceeded': False, 'quota_scheme': 'x',
                    'quota_summary': 'ok', 'client': 'codex', 'agent_type': 'readonly-collaborator',
                    'headless': True, 'clean_room': False, 'access_mode': '只读', 'write_scope': [],
                    'verify_cmd': '', 'inline_readonly': True, 'receipt_location': 'r',
                    'execution_profile': {'spec': 'codex:gpt-5.4', 'model': 'gpt-5.4', 'reasoning_effort': 'high', 'cli_effort': 'high'},
                }],
                'is_heavy': False,
                'capability_ledger': {},
            }
            harness._save_plan(ticket, plan_dir)
            for bad_ov in ('{"s1": {"tier": "low"}}', '{"S1": null}', '{"S1": {"tier": "bogus"}}', '{"S1": {"timeout": -5}}'):
                with self.subTest(bad_ov=bad_ov), self.assertRaises(harness.HarnessError):
                    harness.cmd_plan(self.args('plan', '--plan-dir', str(plan_dir), '--approve-ticket', 't_b4_01', '--override-json', bad_ov))

    def test_v1152_heavy_completion_cross_process_lock_and_batch_verify_tail(self):
        import concurrent.futures
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            plan_dir = td_p / 'state' / 'dispatch_plans'
            plan_dir.mkdir(parents=True)
            (td_p / 'state' / 'heavy_completions').mkdir(parents=True)
            ws = td_p / 'ws'
            ws.mkdir()
            (ws / 'a.py').write_text('x = 1\n', encoding='utf-8')
            worker_code = (
                "import importlib.util, os, sys; from types import SimpleNamespace; "
                "spec = importlib.util.spec_from_file_location('ch', sys.argv[1]); "
                "ch = importlib.util.module_from_spec(spec); spec.loader.exec_module(ch); "
                "args = SimpleNamespace(dry_run=False, subcommand='gate', ticket='', kind='code', target=sys.argv[3], "
                "user_prompt='hi', timeout=600, output='', session_id=sys.argv[4], plan_dir=sys.argv[2]); "
                "p = {'status': 'ok', 'engine': 'codex:gpt-5.4', 'model': 'gpt-5.4', 'token_usage_summary': {'total_tokens': 10}}; "
                "[ch.record_heavy_completion_for_stop_hook(args, dict(p)) for _ in range(6)]"
            )
            script_path = str(Path(harness.__file__).resolve())
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                futs = [pool.submit(subprocess.run, [sys.executable, '-B', '-c', worker_code, script_path, str(plan_dir), str(ws), 'race-o1'],
                                    capture_output=True, text=True) for _ in range(3)]
                for f in futs:
                    self.assertEqual(f.result().returncode, 0, f.result().stderr)
            hc_entries = json.loads((td_p / 'state' / 'heavy_completions' / 'race-o1.json').read_text(encoding='utf-8'))
            self.assertEqual(len(hc_entries), 18)

            # O3: cmd_batch gate failure retains verify_exit_code and verify_output_tail
            spec_file = td_p / 'spec.json'
            spec_file.write_text(json.dumps([{
                'stream_id': 'S1', 'target': str(ws), 'kind': 'gate', 'engine': 'codex:gpt-5.4',
                'write_scope': ['a.py'], 'verify_cmd': f'"{sys.executable}" -c "import sys; print(\'boom-diag\'); sys.exit(7)"',
            }]), encoding='utf-8')
            codex_home = td_p / 'codex_home'
            codex_home.mkdir()
            (codex_home / 'auth.json').write_text(json.dumps({'tokens': {'access_token': 'tok_valid_123456'}}), encoding='utf-8')
            (codex_home / 'config.toml').write_text('model = "gpt-5.4"\n', encoding='utf-8')
            (codex_home / 'models_cache.json').write_text(json.dumps({
                'models': [{'slug': 'gpt-5.4', 'supported_reasoning_levels': [{'effort': 'high'}]}]
            }), encoding='utf-8')
            fake_bin = td_p / 'codex'
            fake_bin.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            fake_bin.chmod(0o755)
            with unittest.mock.patch.dict(os.environ, {'CODEX_HOME': str(codex_home), 'CAE_CODEX_BINARY': str(fake_bin), 'CAE_PLAN_STATE_DIR': str(plan_dir)}),                  unittest.mock.patch.object(harness, 'invoke_engine', return_value={'summary': 'ok', 'content': 'done', 'evidence': [], 'unverified': []}):
                code, out = self.capture(harness.cmd_batch, self.args('batch', '--spec-file', str(spec_file), '--caller', 'claude'))
            self.assertNotEqual(code, 0)
            self.assertEqual(out['streams'][0]['status'], 'verification_failed')
            self.assertEqual(out['streams'][0]['verify_exit_code'], 7)
            self.assertIn('boom-diag', out['streams'][0]['verify_output_tail'])

    def test_shard1_f1_f4_agentignore_pinning_and_wildcard_ignore_dirs(self):
        ws = self.root / 'f1-ws'
        ws.mkdir()
        (ws / 'safe.py').write_text('x = 1\n', encoding='utf-8')
        (ws / '.agentignore').write_text('*\n.agentignore\n', encoding='utf-8')
        snap_star = harness.snapshot_files(ws)
        self.assertIn('.agentignore', snap_star)
        self.assertNotIn('safe.py', snap_star)

        # F1(2): 子进程运行期篡改/新建 .agentignore 不得掩盖越界写入
        ws2 = self.root / 'f1-tamper'
        ws2.mkdir()
        (ws2 / 'safe.py').write_text('x = 1\n', encoding='utf-8')

        def sneaky_worker(*_a, **_kw):
            (ws2 / '.agentignore').write_text('*\nevil.py\n.agentignore\n', encoding='utf-8')
            (ws2 / 'evil.py').write_text('pwned = True\n', encoding='utf-8')
            return dict(TASK)

        with patch.object(harness, '_args_backends', return_value=backends()), \
             patch.object(harness, 'invoke_engine', side_effect=sneaky_worker):
            rc, rep = self.capture(harness.cmd_task, self.args(
                'task', '--target', str(ws2), '--caller', 'antigravity', '--kind', 'code',
                '--write-scope', 'safe.py', '--task', '只改 safe.py'))
        self.assertEqual(rc, harness.EXIT_CODES['scope_violation'])
        self.assertEqual(rep['status'], 'scope_violation')
        self.assertIn('.agentignore', rep['scope_violations'])
        self.assertIn('evil.py', rep['scope_violations'])

        # F4: 通配首段目录解封 + cmd_gate/cmd_task 统一透传 ignore_dirs/ignore_patterns
        ign = harness._snapshot_ignore_dirs_for(['dist*/*'], include_build=True)
        self.assertNotIn('dist', ign)
        self.assertNotIn('build', ign)
        self.assertIn('.git', ign)

        ws3 = self.root / 'f4-ws'
        (ws3 / 'dist').mkdir(parents=True)
        (ws3 / 'build').mkdir(parents=True)
        (ws3 / 'dist' / 'artifact.py').write_text('a = 1\n', encoding='utf-8')
        (ws3 / 'build' / 'existing.o').write_text('obj\n', encoding='utf-8')
        (ws3 / '.agentignore').write_text('ignored_note.txt\n', encoding='utf-8')
        (ws3 / 'ignored_note.txt').write_text('note\n', encoding='utf-8')

        calls = []
        def invoke_fallback(spec_str, *_a, receipt=None, **_kw):
            calls.append(spec_str)
            if spec_str.startswith('codex:'):
                if receipt is not None:
                    receipt['startup_only'] = True
                raise harness.HarnessError('quota', 'quota_exhausted', preflight_blocked=True)
            return dict(TASK)

        with patch.object(harness, '_args_backends', return_value=backends()), \
             patch.object(harness, 'invoke_engine', side_effect=invoke_fallback):
            rc_ok, rep_ok = self.capture(harness.cmd_task, self.args(
                'task', '--target', str(ws3), '--caller', 'antigravity', '--kind', 'code',
                '--write-scope', 'dist*/*', '--fallback-on-quota', '--task', '更新 dist'))
        self.assertEqual(rc_ok, 0)
        self.assertEqual(rep_ok['status'], 'ok')
        self.assertTrue(rep_ok['engine'].startswith('agy:'))
        self.assertEqual(len(calls), 2)

        gate_before = harness.snapshot_files(ws3, ignore_dirs=ign, ignore_patterns=harness.read_agentignore_patterns(ws3))
        (ws3 / 'build' / 'dirty.o').write_text('dirty\n', encoding='utf-8')
        fake_gate_res = {
            'status': 'quota_exhausted', 'scope_verified': True, 'changed_files': [],
            'worker_changed_files': [], 'verification_changed_files': [], 'rounds_used': 1,
            'history': [{'verification_executed': False, 'verify_exit_code': None, 'preflight_blocked': True, 'invocation': {}}],
        }
        self.assertFalse(harness._gate_can_replay(fake_gate_res, gate_before, ws3, ignore_dirs=ign))

    def test_shard1_f2_subscription_model_case_insensitive_and_1m_suffix(self):
        fp = json.loads(json.dumps(harness._fleet_profile()))
        fp.setdefault('subscription_matrix', {}).setdefault('models', {})['k3.1-pro'] = {
            'agent': 'claude', 'enabled': False, 'quota_scheme': 'token_plan',
        }
        fp['subscription_matrix']['models']['deepseek-r2-pro'] = {
            'agent': 'dsh', 'enabled': True, 'quota_scheme': 'api_balance',
        }
        engines = self.four_backends()
        with patch.object(harness, '_fleet_profile', return_value=fp):
            self.assertIs(harness._subscription_model('K3.1-Pro[1m]').get('enabled'), False)
            self.assertIs(harness._subscription_model('k3.1-pro[1M]').get('enabled'), False)
            self.assertFalse(harness._is_subscription_enabled('claude', 'K3.1-Pro[1m]'))
            with self.assertRaises(harness.HarnessError) as ctx:
                harness.choose_engine(engines, 'antigravity', 'claude:K3.1-Pro[1m]')
            self.assertEqual(ctx.exception.status, 'subscription_disabled')
            self.assertEqual(harness._prompt_engine_spec(engines, '请使用 DeepSeek-R2-Pro[1m] 检查'), 'dsh:DeepSeek-R2-Pro[1m]')
            with self.assertRaises(harness.HarnessError) as ctx2:
                harness.choose_engine(engines, 'antigravity', '', user_prompt='请用 K3.1-Pro[1m] 审查')
            self.assertEqual(ctx2.exception.status, 'subscription_disabled')

    def test_shard1_f3_plan_override_native_engine_user_prompt_agy_family_and_batch_ticket(self):
        engines = self.four_backends()
        engines['agy']['models'] = [
            {'slug': 'gemini-7-pro-low', 'display_name': 'Gemini 7 Pro Low'},
            {'slug': 'gemini-7-pro-high', 'display_name': 'Gemini 7 Pro High'},
            {'slug': 'gemini-7-flash-low', 'display_name': 'Gemini 7 Flash Low'},
        ]
        engines['codex']['models'] = [
            {'slug': 'gpt-5.4-mini', 'provider': 'openai', 'model_specialty': 'fast',
             'context_window': 128000, 'max_context_window': 128000, 'supported_efforts': ['low', 'medium']},
            {'slug': 'gpt-5.4-sol', 'provider': 'openai', 'model_specialty': 'engineering',
             'context_window': 800000, 'max_context_window': 800000, 'supported_efforts': ['low', 'high', 'max']},
        ]
        target = self.root / 'solver.py'
        target.write_text('u = 1\n', encoding='utf-8')
        plans = self.root / 'f3-plans'

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            _, native_ticket = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'codex', '--channel', 'native_subagent', '--target', str(target),
                '--plan-dir', str(plans), '--save-ticket'))
            with self.assertRaises(harness.HarnessError) as nat_err:
                harness.cmd_plan(self.args(
                    'plan', '--caller', 'codex', '--plan-dir', str(plans),
                    '--approve-ticket', native_ticket['ticket_id'],
                    '--override-json', json.dumps({'S1': {'engine': 'dsh:default'}})))
            self.assertEqual(nat_err.exception.status, 'ticket_invalid')

            _, prompt_ticket = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--target', str(target), '--plan-dir', str(plans),
                '--user-prompt', '用 dsh 低挡快速看一下拼写', '--save-ticket'))
            self.assertEqual(prompt_ticket['streams'][0]['tier'], 'low')
            _, approved = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--approve-ticket', prompt_ticket['ticket_id'],
                '--override-json', json.dumps({'S1': {'engine': 'codex:default', 'tier': 'max'}})))
            self.assertEqual(approved['status'], 'APPROVED')
            self.assertEqual(approved['streams'][0]['tier'], 'max')
            self.assertEqual(approved['streams'][0]['model'], 'gpt-5.4-sol')
            saved_raw = json.loads((plans / f"{prompt_ticket['ticket_id']}.json").read_text(encoding='utf-8'))
            self.assertFalse(any(k.startswith('_') for k in saved_raw['streams'][0]))
            saved_raw['streams'][0]['estimated_tokens'] = 300000
            harness._save_plan(saved_raw, plans)

            seen_profiles = []
            def fake_invoke(_spec, *_a, profile=None, **_kw):
                seen_profiles.append(profile)
                return dict(TASK)

            with patch.object(harness, 'invoke_engine', side_effect=fake_invoke):
                rc_b, batch_ok = self.capture(harness.cmd_batch, self.args(
                    'batch', '--caller', 'antigravity', '--plan-dir', str(plans), '--ticket', prompt_ticket['ticket_id']))
            self.assertEqual(rc_b, 0)
            self.assertEqual(batch_ok['streams'][0]['status'], 'ok')
            self.assertEqual(seen_profiles[-1]['tier'], 'max')
            self.assertEqual(seen_profiles[-1]['cli_effort'], 'max')

            drifted_engines = json.loads(json.dumps(engines))
            drifted_engines['codex']['models'][1]['supported_efforts'] = ['low', 'high']
            with patch.object(harness, '_args_backends', return_value=drifted_engines), \
                 patch.object(harness, 'invoke_engine', side_effect=fake_invoke):
                rc_drift, batch_drift = self.capture(harness.cmd_batch, self.args(
                    'batch', '--caller', 'antigravity', '--plan-dir', str(plans), '--ticket', prompt_ticket['ticket_id']))
            self.assertNotEqual(rc_drift, 0)
            self.assertEqual(batch_drift['streams'][0]['status'], 'ticket_invalid')

            _, agy_ticket = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'codex', '--engine', 'agy:default', '--target', str(target),
                '--plan-dir', str(plans), '--save-ticket'))
            self.assertEqual(agy_ticket['streams'][0]['model'], 'gemini-7-pro-high')
            _, agy_approved = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'codex', '--plan-dir', str(plans),
                '--approve-ticket', agy_ticket['ticket_id'], '--full-json',
                '--override-json', json.dumps({'S1': {'tier': 'low'}})))
            self.assertEqual(agy_approved['status'], 'APPROVED')
            self.assertEqual(agy_approved['streams'][0]['model'], 'gemini-7-pro-low')
            self.assertEqual(agy_approved['streams'][0]['execution_profile']['spec'], 'agy:gemini-7-pro-low')
            with patch.object(harness, 'invoke_engine', return_value=dict(TASK)):
                rc_agy, batch_agy = self.capture(harness.cmd_batch, self.args(
                    'batch', '--caller', 'codex', '--plan-dir', str(plans), '--ticket', agy_ticket['ticket_id']))
            self.assertEqual(rc_agy, 0)
            self.assertEqual(batch_agy['streams'][0]['status'], 'ok')

    def test_shard1_f5_pure_control_collision_latex_escapes_repaired(self):
        formula = r'\frac{\rho}{\beta} + \nabla \cdot \tau + \theta \times \text{flux} + \right)'
        raw_json = (
            '{"summary": "已核验", "content": "' + formula + '", '
            '"evidence": ["line1\\nline2\\tcol"], "unverified": []}'
        )
        self.assertIn(r'\frac', raw_json)
        self.assertNotIn(r'\\frac', raw_json)
        self.assertTrue(harness._has_unrepaired_latex_escape(raw_json))
        self.assertFalse(harness._has_unrepaired_latex_escape(json.dumps({'content': r'\frac{1}{2}'})))

        expected_macros = (r'\frac', r'\rho', r'\beta', r'\nabla', r'\tau', r'\theta', r'\times', r'\text{flux}', r'\right)')
        for variant in (
            raw_json,
            '```json\n' + raw_json + '\n```',
            '说明文字 "未闭合引号:\n' + raw_json,
        ):
            decoded = harness._extract_json_object(variant, harness.TASK_JSON_SCHEMA)
            for macro in expected_macros:
                self.assertIn(macro, decoded['content'])
            self.assertEqual(decoded['evidence'], ['line1\nline2\tcol'])
        relaxed = harness._loads_relaxed_dict(raw_json)
        for macro in expected_macros:
            self.assertIn(macro, relaxed['content'])

    def test_shard1_f6_input_type_guards_router_corruption_and_ccswitch_null_meta(self):
        self.assertEqual(harness._scope_patterns(None), [])
        self.assertEqual(harness._scope_patterns('src/a.py'), ['src/a.py'])
        for bad_scope in (123, {'a': 'b'}, [123], [None]):
            with self.subTest(bad_scope=bad_scope), self.assertRaises(harness.HarnessError):
                harness._scope_patterns(bad_scope)

        for bad_tier in ('bogus', 'minimal', 123):
            with self.subTest(bad_tier=bad_tier), self.assertRaises(harness.HarnessError):
                harness.select_task_tier(bad_tier)

        engines = self.four_backends()
        target = self.root / 'ok.py'
        target.write_text('x = 1\n', encoding='utf-8')
        plan_args = self.args('plan', '--caller', 'antigravity', '--target', str(target))
        for bad_stream in (
            {'target': 123},
            {'engine': ['codex']},
            {'model': 456},
            {'subtask': ['not-str']},
            {'agent_type': 789},
            {'tier': 'bogus'},
            {'tier': 10},
        ):
            with self.subTest(bad_stream=bad_stream), self.assertRaises(harness.HarnessError):
                harness._resolve_plan_streams(plan_args, [bad_stream], engines)

        cl_home = self.root / 'corrupt-router-claude'
        (cl_home / 'router').mkdir(parents=True)
        (cl_home / 'router' / 'router.json').write_text('{corrupt json', encoding='utf-8')
        with self.assertRaises(harness.HarnessError):
            harness._load_claude_router_config(cl_home, require_valid_file=True)
        fallback_cfg = harness._load_claude_router_config(cl_home, require_valid_file=False)
        self.assertEqual(fallback_cfg['default_key_from_card'], 'Kimi')

        db_p = self.root / 'null-meta.db'
        with contextlib.closing(sqlite3.connect(db_p)) as con, con:
            con.execute('CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, is_current INT, meta TEXT)')
            con.execute('INSERT INTO providers VALUES (?,?,?,?,?)', ('p1', 'claude', 'Kimi', 1, 'null'))
            con.execute('INSERT INTO providers VALUES (?,?,?,?,?)', ('p2', 'claude', 'BadUsage', 0, json.dumps({'usage_script': 'str-val'})))
        (cl_home / 'router' / 'router.json').write_text(json.dumps({'ccswitch_db': str(db_p)}), encoding='utf-8')
        q1 = harness._claude_card_quota({'config_home': str(cl_home)}, 'kimi-k3')
        self.assertEqual(q1['quota_scheme'], 'ccswitch_card_dependent')
        with contextlib.closing(sqlite3.connect(db_p)) as con, con:
            con.execute("UPDATE providers SET is_current=0")
            con.execute("UPDATE providers SET is_current=1 WHERE name='BadUsage'")
        q2 = harness._claude_card_quota({'config_home': str(cl_home)}, '')
        self.assertEqual(q2['quota_scheme'], 'ccswitch_card_dependent')

    def test_shard1_f7_f9_batch_quota_short_reset_preservation_and_claude_errors_list(self):
        claude_err_env = {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                          'errors': ['API Error: Credit balance is too low']}
        with self.assertRaises(harness.HarnessError) as c_exc:
            harness._check_output_status(claude_err_env)
        self.assertEqual(c_exc.exception.status, 'quota_exhausted')
        self.assertIn('Credit balance is too low', c_exc.exception.details['error'])

        proc = subprocess.CompletedProcess(['claude'], 1, json.dumps(claude_err_env), '')
        diag = harness._backend_diagnostics(proc, structured_only=True)
        self.assertTrue(harness._is_quota_exhausted(diag))

        ok_env = {'type': 'result', 'is_error': False, 'errors': [], 'structured_output': dict(TASK)}
        self.assertEqual(harness._extract_json_object(json.dumps(ok_env), harness.TASK_JSON_SCHEMA), TASK)

        engines = self.four_backends()
        ws = self.root / 'f7-ws'
        ws.mkdir()
        (ws / 'a.py').write_text('x = 1\n', encoding='utf-8')
        spec = self.root / 'f7-spec.json'
        spec.write_text(json.dumps([
            {'stream_id': 'S1', 'target': str(ws / 'a.py'), 'engine': 'codex:review', 'subtask': '审1'},
            {'stream_id': 'S2', 'target': str(ws / 'a.py'), 'engine': 'codex:review', 'subtask': '审2'},
        ]), encoding='utf-8')
        hd = {**harness._headless_tunables(), 'batch_max_workers_per_backend': {'codex': 1}}

        def invoke_short_quota(_spec, *_a, batch=None, **_kw):
            cd = harness._record_quota_exhausted('codex', engines['codex'], 'usage limit reached, resets in 2m', batch=batch)
            raise harness.HarnessError('codex 额度不足', 'quota_exhausted', preflight_blocked=False,
                                       **harness._quota_user_choices('codex', cd))

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_headless_tunables', return_value=hd), \
             patch.object(harness, 'invoke_engine', side_effect=invoke_short_quota), \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            _, res = self.capture(harness.cmd_batch, self.args('batch', '--caller', 'antigravity', '--spec-file', str(spec)))
        self.assertEqual([s['status'] for s in res['streams']], ['quota_exhausted', 'quota_exhausted'])
        self.assertTrue(res['streams'][1].get('queue_aborted'))
        active_cd = harness._active_quota_record('codex', engines['codex'])
        self.assertLess(active_cd['cooldown_until_epoch'] - time.time(), 300)

        approved_f7 = {
            'streams': [{
                'stream_id': 'S1', 'channel': 'cae_harness_headless', 'target': str(ws / 'a.py'),
                'kind': 'review', 'engine': 'codex:fixture-code', 'tier': 'max', 'timeout': 60, 'subtask': '审1',
                'execution_profile': {'spec': 'codex:fixture-code', 'model': 'fixture-code', 'cli_effort': 'max'},
            }],
            'user_prompt': '',
        }
        with patch.object(harness, '_begin_dispatch', side_effect=lambda a: setattr(a, '_approved_ticket', approved_f7)), \
             patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'invoke_engine') as never_inv, \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            _, res2 = self.capture(harness.cmd_batch, self.args('batch', '--caller', 'antigravity'))
        never_inv.assert_not_called()
        self.assertTrue(res2['streams'][0].get('preflight_blocked'))
        active_cd2 = harness._active_quota_record('codex', engines['codex'])
        self.assertLess(active_cd2['cooldown_until_epoch'] - time.time(), 300)

    def test_shard1_f8_redact_public_hyphen_keys_unc_macos_paths_and_quota_summary(self):
        sample = {
            'x-api-key': 'secret-val-1',
            'auth-token': 'secret-val-2',
            'access-token': 'secret-val-3',
            'private-key': 'secret-val-4',
            'win_multi': r'C:\\Users\\alice\\project\\main.py',
            'wsl_loc': r'\\wsl.localhost\Ubuntu-24.04\home\alice\solver\run.py',
            'wsl_dollar': r'\\wsl$\Debian\home\bob\mesh.py',
            'mac_home': '/Users/charlie/Documents/paper.tex',
            'mixed_summary': 'field quota_summary shows 5h_window:42.5% used',
        }
        redacted = harness._redact_public(sample)
        for k in ('x-api-key', 'auth-token', 'access-token', 'private-key'):
            self.assertEqual(redacted[k], '<redacted>')
        for path_key, leaked_user in (('win_multi', 'alice'), ('wsl_loc', 'alice'), ('wsl_dollar', 'bob'), ('mac_home', 'charlie')):
            self.assertIn('<HOME>', redacted[path_key])
            self.assertNotIn(leaked_user, redacted[path_key])
        self.assertNotIn('42.5%', redacted['mixed_summary'])
        self.assertIn('<USAGE>', redacted['mixed_summary'])

    def test_shard1_f10_grounding_git_anchor_root_validation_base_ref_and_baseline_exemptions(self):
        repo_dir = self.root / 'f10-git-proj'
        (repo_dir / 'src').mkdir(parents=True)
        (repo_dir / 'docs' / 'guide').mkdir(parents=True)
        (repo_dir / 'src' / 'flux_solver.py').write_text(
            'parser.add_argument("--cfl-max")\nDGSEM_FLUX_LIMIT = 4096\n', encoding='utf-8'
        )
        doc_file = repo_dir / 'docs' / 'guide' / 'manual.md'
        doc_file.write_text('使用 `flux_solver.py --cfl-max` 与历史参数 `runner --legacy-flag` 及 `legacy_cfg.toml`（阈值 4096 tokens）。\n', encoding='utf-8')
        subprocess.run(['git', 'init'], cwd=repo_dir, check=True, capture_output=True, timeout=15)
        subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=repo_dir, check=True, capture_output=True, timeout=15)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=repo_dir, check=True, capture_output=True, timeout=15)
        subprocess.run(['git', 'add', '.'], cwd=repo_dir, check=True, capture_output=True, timeout=15)
        subprocess.run(['git', 'commit', '-m', 'init'], cwd=repo_dir, check=True, capture_output=True, timeout=15)

        self.assertEqual(harness._infer_project_anchor_root(doc_file), repo_dir.resolve())

        rep_clean = harness.audit_doc_grounding(doc_file, root_dir=repo_dir / 'docs', base_ref='HEAD',
                                                allow_files=[repo_dir / 'src' / 'flux_solver.py'])
        self.assertTrue(rep_clean['ok'], rep_clean['findings'])

        with self.assertRaises(harness.HarnessError):
            harness.audit_doc_grounding(doc_file, root_dir=self.root / 'no-such-root')
        with self.assertRaises(harness.HarnessError):
            harness.audit_doc_grounding(doc_file, allow_files=[self.root / 'no-such-allow.txt'])
        for bad_ref in ('nonexistent_branch_xyz', '-bad-opt'):
            with self.assertRaises(harness.HarnessError):
                harness.audit_doc_grounding(doc_file, base_ref=bad_ref)

        polished_ok = {'summary': '润色完成', 'content': doc_file.read_text(encoding='utf-8'), 'evidence': [], 'unverified': []}
        polished_bad = {'summary': '润色完成', 'content': doc_file.read_text(encoding='utf-8') + '新增 `runner --invented-flag` 与 `ghost_cfg.toml`。\n',
                        'evidence': [], 'unverified': []}
        with patch.object(harness, '_args_backends', return_value=backends()), \
             patch.object(harness, 'invoke_engine', return_value=polished_ok):
            _, pol_rep1 = self.capture(harness.cmd_task, self.args(
                'task', '--target', str(doc_file), '--caller', 'antigravity', '--kind', 'polish', '--task', '润色文档'))
        self.assertTrue(pol_rep1['grounding_audit']['ok'], pol_rep1['grounding_audit']['findings'])

        with patch.object(harness, '_args_backends', return_value=backends()), \
             patch.object(harness, 'invoke_engine', return_value=polished_bad):
            _, pol_rep2 = self.capture(harness.cmd_task, self.args(
                'task', '--target', str(doc_file), '--caller', 'antigravity', '--kind', 'polish', '--task', '润色文档'))
        self.assertFalse(pol_rep2['grounding_audit']['ok'])
        tokens_hit = {f['token'] for f in pol_rep2['grounding_audit']['findings']}
        self.assertIn('--invented-flag', tokens_hit)
        self.assertIn('ghost_cfg.toml', tokens_hit)

    def test_shard1_f11_f12_i1_clean_room_four_files_batch_scope_heavy_hook_and_interop_stdin(self):
        cr_dir = self.root / 'f11-dir'
        cr_dir.mkdir()
        (cr_dir / 'solver.py').write_text('k = 1\n', encoding='utf-8')
        instr_names = ('AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md', 'CLAUDE.local.md')
        for fn in instr_names:
            (cr_dir / fn).write_text(f'rule in {fn}\n', encoding='utf-8')

        staged_dir = harness._stage_wsl_readonly_workspace(cr_dir, self.root / 'f11-stage-dir', None, exclude_agent_instructions=True)
        self.assertTrue((staged_dir / 'solver.py').is_file())
        for fn in instr_names:
            self.assertFalse((staged_dir / fn).exists(), fn)
        dir_prompt = harness._inline_readonly_prompt('检查', cr_dir, None, exclude_agent_instructions=True)
        self.assertIn('=== FILE: solver.py ===', dir_prompt)
        for fn in instr_names:
            self.assertNotIn(f'=== FILE: {fn} ===', dir_prompt)

        for idx, fn in enumerate(instr_names):
            single_stage = harness._stage_wsl_readonly_workspace(
                cr_dir, self.root / f'f11-single-{idx}', [fn], exclude_agent_instructions=True
            )
            self.assertFalse((single_stage / fn).exists())
            staged_files = [p.name for p in single_stage.iterdir()]
            self.assertEqual(len(staged_files), 1)
            neutral_name = staged_files[0]
            self.assertNotIn(neutral_name.upper(), harness._AGENT_INSTRUCTION_FILENAMES)
            single_prompt = harness._inline_readonly_prompt('单文件检查', single_stage, [fn], exclude_agent_instructions=True)
            self.assertIn(f'=== FILE: {fn} ===', single_prompt)
            self.assertIn(f'rule in {fn}', single_prompt)
            rewritten = harness._rewrite_staged_paths({
                'read_files': [neutral_name],
                'unread_files': [{'file': neutral_name, 'reason': 'none'}],
                'findings': [{'file': neutral_name, 'line_start': 1, 'line_end': 1, 'severity': 'low',
                              'category': 'doc', 'description': 'd', 'evidence': 'e'}],
                'evidence': [f'{neutral_name}:1'],
            }, single_stage)
            self.assertEqual(rewritten['read_files'], [fn])
            self.assertEqual(rewritten['unread_files'][0]['file'], fn)
            self.assertEqual(rewritten['findings'][0]['file'], fn)
            self.assertEqual(rewritten['evidence'], [f'{fn}:1'])

        engines = self.four_backends()
        fake_ticket = {
            'streams': [{
                'stream_id': 'M1', 'channel': 'cae_harness_headless', 'target': str(self.root / 'does_not_exist.py'),
                'kind': 'review', 'engine': 'codex:fixture-code', 'tier': 'max', 'timeout': 30, 'subtask': '读',
                'execution_profile': {'spec': 'codex:fixture-code', 'model': 'fixture-code', 'cli_effort': 'max'},
            }],
            'user_prompt': '',
        }
        with patch.object(harness, '_begin_dispatch', side_effect=lambda a: setattr(a, '_approved_ticket', fake_ticket)), \
             patch.object(harness, '_args_backends', return_value=engines), \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            rc_m, res_m = self.capture(harness.cmd_batch, self.args('batch'))
        self.assertNotEqual(rc_m, 0)
        self.assertEqual(res_m['streams'][0]['status'], 'failed')
        self.assertIn('不存在', res_m['streams'][0]['error'])

        b_ws = self.root / 'f12-merge-ws'
        b_ws.mkdir()
        (b_ws / 'a.py').write_text('a = 1\n', encoding='utf-8')
        merge_spec = self.root / 'f12-merge.json'
        merge_spec.write_text(json.dumps([
            {'stream_id': 'S1', 'target': str(b_ws / 'a.py'), 'kind': 'review', 'engine': 'codex:review', 'subtask': '审'}
        ]), encoding='utf-8')

        def touch_both(*_a, receipt=None, **_kw):
            (b_ws / 'host_leak.py').write_text('1\n', encoding='utf-8')
            receipt['staged_scope'] = {
                'scope_verified': True, 'changed_files': ['staged_leak.py'], 'scope_violations': ['staged_leak.py'],
            }
            return dict(TASK)

        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, 'invoke_engine', side_effect=touch_both), \
             patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
            _, merge_out = self.capture(harness.cmd_batch, self.args('batch', '--caller', 'antigravity', '--spec-file', str(merge_spec)))
        s0 = merge_out['streams'][0]
        self.assertEqual(s0['status'], 'scope_violation')
        self.assertEqual(s0['changed_files'], ['host_leak.py', 'staged_leak.py'])
        self.assertNotIn('staged_scope', s0['invocation'])

        plans_dir = self.root / 'f12-state' / 'dispatch_plans'
        small_file = self.root / 'tiny.py'
        small_file.write_text('x = 1\n', encoding='utf-8')
        huge_task = '推导通量守恒条件 ' * 6000
        task_args = self.args('task', '--target', str(small_file), '--kind', 'math', '--task', huge_task,
                              '--plan-dir', str(plans_dir), '--session-id', 'sess_heavy_task')
        task_payload = {'status': 'ok', 'engine': 'codex:fixture-code', 'model': 'fixture-code',
                        'token_usage_summary': {'total_tokens': 100}}
        harness.record_heavy_completion_for_stop_hook(task_args, task_payload)
        hc_task = json.loads((plans_dir.parent / 'heavy_completions' / 'sess_heavy_task.json').read_text(encoding='utf-8'))
        self.assertEqual(len(hc_task), 1)

        audit_args = self.args('audit', '--target', str(small_file), '--plan-dir', str(plans_dir), '--session-id', 'sess_dual_audit')
        audit_payload = {
            'status': 'ok',
            'completed_units': [
                {'side': 'left', 'shard_id': 1, 'engine': 'codex:fixture-code', 'model': 'fixture-code', 'status': 'ok'},
                {'side': 'right', 'shard_id': 1, 'engine': 'agy:gemini-7-pro-high', 'model': 'gemini-7-pro-high', 'status': 'ok'},
            ],
            'errors': [],
            'token_usage_summary': {'total_tokens': 50},
        }
        harness.record_heavy_completion_for_stop_hook(audit_args, audit_payload)
        hc_audit = json.loads((plans_dir.parent / 'heavy_completions' / 'sess_dual_audit.json').read_text(encoding='utf-8'))
        self.assertEqual([s['stream_id'] for s in hc_audit[0]['streams']], ['left-S1', 'right-S1'])

        with patch.object(harness, '_wsl_path', side_effect=lambda p, windows=True: 'Z:\\tmp\\' + Path(p).name):
            cmd_buf, _ = harness._interop_command(['codex.exe', 'exec'], self.root, self.root, '输入文本')
            cmd_stream, _ = harness._interop_command(['codex.exe', 'app-server'], self.root, self.root, None)
        for encoded_cmd in (cmd_buf, cmd_stream):
            ps_src = base64.b64decode(encoded_cmd[-1]).decode('utf-16le')
            self.assertIn('catch [System.IO.IOException] {}', ps_src)
            self.assertLess(ps_src.index('$p.StandardInput.Close()'), ps_src.index('catch [System.IO.IOException] {}'))
            self.assertLess(ps_src.index('catch [System.IO.IOException] {}'), ps_src.index('$p.WaitForExit()'))

    def test_r2_01_plan_override_deepcopy_profile_guard_non_agy_tier_lock_and_batch_subtask(self):
        engines = self.four_backends()
        engines['codex']['models'] = [
            {'slug': 'gpt-5.4-deep', 'provider': 'openai', 'model_specialty': 'reasoning',
             'description': 'Deep math reasoning model', 'context_window': 400000, 'max_context_window': 400000,
             'supported_efforts': ['low', 'medium', 'high', 'max']},
            {'slug': 'gpt-5.4-sol', 'provider': 'openai', 'model_specialty': 'engineering',
             'description': 'Agentic coding model', 'context_window': 400000, 'max_context_window': 400000,
             'supported_efforts': ['low', 'medium', 'high', 'max']},
            {'slug': 'gpt-5.4-mini', 'provider': 'openai', 'model_specialty': 'fast',
             'description': 'Fast lightweight model', 'context_window': 128000, 'max_context_window': 128000,
             'supported_efforts': ['high'], 'delegating_efforts': ['high']},
        ]
        target = self.root / 'r2_01_solver.py'
        target.write_text('u = 1.0\n', encoding='utf-8')
        plans = self.root / 'r2-01-plans'

        with patch.object(harness, '_args_backends', return_value=engines):
            _, tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--engine', 'codex:default', '--kind', 'code',
                '--write-scope', 'r2_01_solver.py', '--target', str(target), '--plan-dir', str(plans), '--save-ticket'))
            tid = tk['ticket_id']
            disk_before = json.loads((plans / f'{tid}.json').read_text(encoding='utf-8'))
            self.assertEqual(disk_before['streams'][0]['model'], 'gpt-5.4-sol')

            # 1) Override engine to unavailable backend or delegating-only profile must re-raise without dirtying disk
            unavail_engines = json.loads(json.dumps(engines))
            unavail_engines['dsh']['available'] = False
            with patch.object(harness, '_args_backends', return_value=unavail_engines):
                with self.assertRaises(harness.HarnessError):
                    harness.cmd_plan(self.args(
                        'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                        '--approve-ticket', tid,
                        '--override-json', json.dumps({'S1': {'engine': 'dsh:default'}})))
            disk_after_fail1 = json.loads((plans / f'{tid}.json').read_text(encoding='utf-8'))
            self.assertEqual(disk_after_fail1['streams'], disk_before['streams'])
            self.assertEqual(disk_after_fail1['status'], 'PENDING')

            with self.assertRaises(harness.HarnessError):
                harness.cmd_plan(self.args(
                    'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                    '--approve-ticket', tid,
                    '--override-json', json.dumps({'S1': {'engine': 'codex:gpt-5.4-mini', 'tier': 'high'}})))
            disk_after_fail2 = json.loads((plans / f'{tid}.json').read_text(encoding='utf-8'))
            self.assertEqual(disk_after_fail2['streams'], disk_before['streams'])

            # 2) Tier-only override on non-agy (codex:default & dsh:default) locks orig_model without false ticket_changed
            _, codex_low = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--approve-ticket', tid, '--override-json', json.dumps({'S1': {'tier': 'low'}})))
            self.assertEqual(codex_low['status'], 'APPROVED')
            self.assertEqual(codex_low['streams'][0]['model'], 'gpt-5.4-sol')
            self.assertEqual(codex_low['streams'][0]['tier'], 'low')

            _, dsh_tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--engine', 'dsh:default', '--kind', 'review',
                '--target', str(target), '--plan-dir', str(plans), '--save-ticket'))
            orig_dsh_model = dsh_tk['streams'][0]['model']
            _, dsh_low = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--approve-ticket', dsh_tk['ticket_id'], '--override-json', json.dumps({'S1': {'tier': 'low'}})))
            self.assertEqual(dsh_low['status'], 'APPROVED')
            self.assertEqual(dsh_low['streams'][0]['model'], orig_dsh_model)

            # External catalog drift at original tier still triggers ticket_changed
            _, drift_tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--engine', 'codex:default', '--kind', 'code',
                '--write-scope', 'r2_01_solver.py', '--target', str(target), '--plan-dir', str(plans), '--save-ticket'))
            drifted_cat = json.loads(json.dumps(engines))
            drifted_cat['codex']['models'].insert(0, {
                'slug': 'gpt-6.0-sol', 'provider': 'openai', 'model_specialty': 'engineering',
                'description': 'Newer coding model', 'context_window': 400000, 'max_context_window': 400000,
                'supported_efforts': ['low', 'high', 'max'],
            })
            with patch.object(harness, '_args_backends', return_value=drifted_cat):
                with self.assertRaises(harness.HarnessError) as drift_exc:
                    harness.cmd_plan(self.args(
                        'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                        '--approve-ticket', drift_tk['ticket_id'],
                        '--override-json', json.dumps({'S1': {'tier': 'low'}})))
                self.assertEqual(drift_exc.exception.status, 'ticket_changed')

            # 3) cmd_batch --ticket with residual HIGH_COMPLEXITY_CUES in user_prompt re-checks subtask
            engines['agy']['models'] = [
                {'slug': 'gemini-7-pro-low', 'display_name': 'Gemini 7 Pro Low'},
                {'slug': 'gemini-7-pro-high', 'display_name': 'Gemini 7 Pro High'},
                {'slug': 'gemini-7-flash-low', 'display_name': 'Gemini 7 Flash Low'},
            ]
            spec_p = self.root / 'r2-01-spec.json'
            spec_p.write_text(json.dumps([
                {'stream_id': 'S1', 'target': str(target), 'kind': 'review', 'engine': 'dsh:default', 'subtask': '检查注释拼写'}
            ]), encoding='utf-8')
            _, hc_tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'codex', '--spec-file', str(spec_p), '--plan-dir', str(plans),
                '--user-prompt', '推导黎曼通量守恒条件并检查注释', '--save-ticket'))
            _, hc_app = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'codex', '--plan-dir', str(plans),
                '--approve-ticket', hc_tk['ticket_id'],
                '--override-json', json.dumps({'S1': {'engine': 'agy:default', 'tier': 'medium'}})))
            self.assertEqual(hc_app['status'], 'APPROVED')
            with patch.object(harness, 'invoke_engine', return_value=dict(TASK)):
                rc_b, batch_res = self.capture(harness.cmd_batch, self.args(
                    'batch', '--caller', 'codex', '--plan-dir', str(plans), '--ticket', hc_tk['ticket_id']))
            self.assertEqual(rc_b, 0, batch_res)
            self.assertEqual(batch_res['streams'][0]['status'], 'ok')

    def test_r2_02_ticket_raw_task_body_scope_subset_and_explicit_engine_tier_prompt_skip(self):
        ws = self.root / 'r2-02-ws'
        (ws / 'src' / 'flux').mkdir(parents=True)
        f_flux = ws / 'src' / 'flux' / 'hllc.py'
        f_flux.write_text('u = 0\n', encoding='utf-8')
        plans = self.root / 'r2-02-plans'
        engines = self.four_backends()

        self.assertTrue(harness._scope_subset_of(['src/flux/hllc.py'], ['src/*'], target_root=ws))
        self.assertTrue(harness._scope_subset_of(['src/flux/hllc.py'], ['src/flux/*.py'], target_root=ws))
        self.assertTrue(harness._scope_subset_of(['src/flux/*.py'], ['src/**'], target_root=ws))
        self.assertTrue(harness._scope_subset_of([], [], target_root=ws))
        self.assertFalse(harness._scope_subset_of(['src/flux/hllc.py'], [], target_root=ws))
        self.assertFalse(harness._scope_subset_of([], ['src/*'], target_root=ws))
        self.assertFalse(harness._scope_subset_of(['tests/test_a.py'], ['src/*'], target_root=ws))
        self.assertFalse(harness._scope_subset_of(['src/flux'], ['src/flux/*.py'], target_root=ws))

        with patch.object(harness, '_args_backends', return_value=engines):
            _, tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--kind', 'gate', '--target', str(ws),
                '--engine', 'dsh:default', '--write-scope', 'src/*', '--verify-cmd', 'python3 -c "pass"',
                '--workflow-goal', '实现 HLLC 通量修复', '--user-prompt', '用 dsh 低挡省额度看一下',
                '--plan-dir', str(plans), '--save-ticket'))
            tid = tk['ticket_id']
            _, app = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans), '--approve-ticket', tid,
                '--override-json', json.dumps({'S1': {'engine': 'codex:fixture-code', 'tier': 'max'}})))
            self.assertEqual(app['status'], 'APPROVED')

            # Narrowed write_scope + carrying original --user-prompt + explicit --worker / --tier succeeds
            with patch.object(harness, 'invoke_edit_worker', return_value=json.dumps(TASK)), \
                 patch.object(harness, 'run_process', return_value=subprocess.CompletedProcess(['sh'], 0, 'ok', '')):
                rc_g, rep_g = self.capture(harness.cmd_gate, self.args(
                    'gate', '--caller', 'antigravity', '--target', str(ws),
                    '--worker', 'codex:fixture-code', '--tier', 'max',
                    '--write-scope', 'src/flux/hllc.py', '--verify-cmd', 'python3 -c "pass"',
                    '--user-prompt', '用 dsh 低挡省额度看一下', '--task', '实现 HLLC 通量修复',
                    '--plan-dir', str(plans), '--ticket', tid, '--dry-run'))
            self.assertEqual(rc_g, 0, rep_g)
            self.assertEqual(rep_g['engine'], 'codex:fixture-code')
            self.assertEqual(rep_g['selected_tier'], 'max')

            # Ticket without write_scope rejects non-empty req_scopes
            _, no_scope_tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--kind', 'code', '--target', str(ws),
                '--engine', 'codex:fixture-code', '--workflow-goal', '修文件',
                '--plan-dir', str(plans), '--save-ticket'))
            self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--approve-ticket', no_scope_tk['ticket_id']))
            with self.assertRaises(harness.HarnessError) as ns_err:
                harness.cmd_task(self.args(
                    'task', '--caller', 'antigravity', '--kind', 'code', '--target', str(ws),
                    '--engine', 'codex:fixture-code', '--write-scope', 'src/flux/hllc.py',
                    '--task', '修文件', '--plan-dir', str(plans), '--ticket', no_scope_tk['ticket_id'], '--dry-run'))
            self.assertEqual(ns_err.exception.status, 'ticket_invalid')

    def test_r2_03_json_escape_preserve_valid_newlines_brace_slice_win_drive_and_latex_macros(self):
        # 1) Valid JSON newlines followed by u_t, eq = 0, and :\\d+\\nu_i must never be corrupted
        valid_obj = {
            'summary': '已核对代码行',
            'content': 'u = 0.0\nu_t = -c * u_x\na = 1\neq = 0',
            'evidence': ['src/flux.py:42\nu_i = 0.5', 'src/flux.py:108\nnorm = 1.0'],
            'unverified': [],
        }
        valid_raw = json.dumps(valid_obj, ensure_ascii=False)
        self.assertFalse(harness._has_unrepaired_latex_escape(valid_raw))
        self.assertEqual(harness._extract_json_object(valid_raw, harness.TASK_JSON_SCHEMA), valid_obj)

        # 2) Odd unclosed quote + \\frac in preamble before { must not invert string polarity inside JSON slice
        preamble_odd_quote = (
            '前置说明含奇数引号 "未闭合且带 \\frac{1}{2}:\n'
            '{\n'
            '  "summary": "公式与路径通过",\n'
            '  "content": "$\\lvert u \\rvert + \\lVert u \\rVert_2 + {\\rm d}x + \\tanh x + \\tan x + A^\\top '
            '+ x \\to 0 + \\therefore + \\textstyle + \\norm{u} + A \\ni x + x \\nleq y + x \\ngeq y '
            '+ a \\neq 0 + b \\ne c + \\nu_{t} + \\textbf{B} + \\textit{I} + \\texttt{T} + \\textrm{R} '
            '+ \\textsf{S} + \\textsc{C} + \\textnormal{N} + \\tag{1}$",\n'
            '  "evidence": ["C:\\Users\\alice\\temp\\test.py", "D:\\build\\foo\\norm\\raw\\test.py:15\\nu_i = 1"],\n'
            '  "unverified": []\n'
            '}'
        )
        parsed = harness._extract_json_object(preamble_odd_quote, harness.TASK_JSON_SCHEMA)
        for macro in (
            r'\lvert', r'\rvert', r'\rVert', r'\rm', r'\tanh', r'\tan', r'\top', r'\to',
            r'\therefore', r'\textstyle', r'\norm{u}', r'\ni', r'\nleq', r'\ngeq',
            r'\neq 0', r'\ne c', r'\nu_{t}', r'\textbf{B}', r'\textit{I}', r'\texttt{T}',
            r'\textrm{R}', r'\textsf{S}', r'\textsc{C}', r'\textnormal{N}', r'\tag{1}',
        ):
            self.assertIn(macro, parsed['content'], macro)
        self.assertEqual(parsed['evidence'][0], r'C:\Users\alice\temp\test.py')
        self.assertEqual(parsed['evidence'][1], 'D:\\build\\foo\\norm\\raw\\test.py:15\nu_i = 1')

    def test_r2_04_role_cooldown_metadata_retry_quota_and_audit_fallback_prefix(self):
        engines = self.four_backends()
        role_prof = json.loads(json.dumps(harness._fleet_profile()))
        role_prof['roles']['review']['candidates'] = ['codex:review']
        harness._record_quota_exhausted('codex', engines['codex'], 'usage limit reached, resets in 10m')

        with patch.object(harness, '_fleet_profile', return_value=role_prof):
            with self.assertRaises(harness.HarnessError) as r_err:
                harness._resolve_role_engine(engines, 'antigravity', 'role:review')
            self.assertEqual(r_err.exception.status, 'quota_exhausted')
            self.assertTrue(r_err.exception.details.get('preflight_blocked'))
            self.assertEqual(r_err.exception.details.get('quota_backend'), 'codex')
            self.assertIn('user_choices', r_err.exception.details)

            # --retry-quota forwards ignore_cooldown across _choose_dispatch_engine, _audit_engines, cmd_audit
            t_file = self.root / 'r2_04.py'
            t_file.write_text('x = 1\n', encoding='utf-8')
            retry_task_args = self.args('task', '--caller', 'antigravity', '--kind', 'review',
                                        '--engine', 'role:review', '--retry-quota', '--target', str(t_file), '--task', '审')
            self.assertEqual(
                harness._choose_dispatch_engine(retry_task_args, engines, 'antigravity', 'role:review', '审'),
                'codex:review',
            )
            self.assertEqual(
                harness._audit_engines(engines, False, 'role:review', '', False, 'antigravity', ignore_cooldown=True),
                ('codex:review', None),
            )
            with patch.object(harness, '_args_backends', return_value=engines):
                rc_r, rep_r = self.capture(harness.cmd_audit, self.args(
                    'audit', '--single', '--left', 'role:review', '--retry-quota',
                    '--caller', 'antigravity', '--target', str(t_file), '--dry-run'))
            self.assertEqual(rc_r, 0, rep_r)
            self.assertEqual(rep_r['plan']['left_engine'], 'codex:review')

        # cmd_audit --fallback-on-quota does not crash with NoneType when quota_backend is omitted
        t_file = self.root / 'r2_04_fb.py'
        t_file.write_text('y = 2\n', encoding='utf-8')
        bare_quota_exc = harness.HarnessError('cooled', 'quota_exhausted')
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_audit_engines', side_effect=bare_quota_exc):
            rc_fb, rep_fb = self.capture(harness.cmd_audit, self.args(
                'audit', '--single', '--fallback-on-quota', '--caller', 'antigravity',
                '--target', str(t_file), '--dry-run'))
        self.assertEqual(rc_fb, 0, rep_fb)
        self.assertEqual(rep_fb['plan']['left_engine'], 'agy:default')

    def test_r2_05_host_env_and_git_dir_isolation(self):
        self.assertEqual(os.environ.get('USER_GUARD_STATE_DIR'), str(self.root / 'state'))
        self.assertNotIn('PLUGIN_DATA', os.environ)
        self.assertNotIn('GIT_DIR', os.environ)
        for bad_k in ('PLUGIN_DATA', 'AGY_CONVERSATION_ID', 'CLAUDECODE', 'GIT_DIR', 'HTTPS_PROXY', 'https_proxy'):
            self.assertTrue(_is_stripped_host_env(bad_k), bad_k)
        self.assertFalse(_is_stripped_host_env('USER_GUARD_INSTALLED_DIR'))
        self.assertTrue(_is_stripped_host_env('USER_GUARD_STATE_DIR'))

        repo_dir = self.root / 'r2-05-git'
        repo_dir.mkdir()
        (repo_dir / 'solver.py').write_text('parser.add_argument("--dt-max")\n', encoding='utf-8')
        (repo_dir / 'README.md').write_text('支持 `solver.py --dt-max`。\n', encoding='utf-8')
        subprocess.run(['git', 'init'], cwd=repo_dir, check=True, capture_output=True, timeout=15)
        subprocess.run(['git', 'config', 'user.email', 't@example.com'], cwd=repo_dir, check=True, capture_output=True, timeout=15)
        subprocess.run(['git', 'config', 'user.name', 'Tester'], cwd=repo_dir, check=True, capture_output=True, timeout=15)
        subprocess.run(['git', 'add', '.'], cwd=repo_dir, check=True, capture_output=True, timeout=15)
        subprocess.run(['git', 'commit', '-m', 'init'], cwd=repo_dir, check=True, capture_output=True, timeout=15)

        poison_git = {
            'GIT_DIR': str(self.root / 'nonexistent-git-dir'),
            'GIT_WORK_TREE': str(self.root / 'nonexistent-wt'),
            'GIT_INDEX_FILE': str(self.root / 'nonexistent-idx'),
        }
        with patch.dict(os.environ, poison_git):
            head = harness._git_head(repo_dir)
            self.assertIsNotNone(head)
            self.assertEqual(len(head), 40)
            rev = harness._local_command(['git', '-C', str(repo_dir), 'rev-parse', 'HEAD'])
            self.assertEqual(rev, head)
            rep = harness.audit_doc_grounding(repo_dir / 'README.md', root_dir=repo_dir, base_ref='HEAD')
            self.assertTrue(rep['ok'], rep['findings'])

    def test_r2_06_scope_matches_multi_globstar_closure_and_agentignore_normalization(self):
        multi_pat = ['a/**/b/**/c.py']
        for valid_p in ('a/b/c.py', 'a/x/b/c.py', 'a/b/y/c.py', 'a/x/u/b/y/v/c.py'):
            self.assertTrue(harness._scope_matches(valid_p, multi_pat), valid_p)
        self.assertFalse(harness._scope_matches('a/x/d/c.py', multi_pat))

        for dir_self in ('src', 'src/', 'src/a.py', 'src/sub/deep.py'):
            self.assertTrue(harness._scope_matches(dir_self, ['src/**']), dir_self)

        ig_root = self.root / 'r2-06-ig'
        ig_root.mkdir()
        (ig_root / '.agentignore').write_text(
            '\ufeff# BOM 注释行不应作为模式\n'
            '\ufeff./cache\\sub/\n'
            '.\\logs\\\n'
            './tmp/out.dat\n',
            encoding='utf-8',
        )
        pats = harness.read_agentignore_patterns(ig_root)
        self.assertEqual(pats, ['cache/sub', 'logs', 'tmp/out.dat'])

    def test_r2_07_clean_room_subpath_slashes_full_field_neutral_restore_and_instr_only_dir_hint(self):
        only_instr = self.root / 'r2-07-only-instr'
        only_instr.mkdir()
        (only_instr / 'AGENTS.md').write_text('agent rules\n', encoding='utf-8')
        (only_instr / 'CLAUDE.md').write_text('claude rules\n', encoding='utf-8')
        with self.assertRaisesRegex(harness.HarnessError, '--target.*单文件'):
            harness._stage_wsl_readonly_workspace(only_instr, self.root / 'r2-07-s0', None, exclude_agent_instructions=True)
        with self.assertRaisesRegex(harness.HarnessError, '--target.*单文件'):
            harness._inline_readonly_prompt('检查', only_instr, None, exclude_agent_instructions=True)
        with patch.object(harness, '_args_backends', return_value=self.four_backends()):
            with self.assertRaisesRegex(harness.HarnessError, '--target.*单文件'):
                harness.cmd_task(self.args(
                    'task', '--caller', 'antigravity', '--kind', 'review', '--engine', 'codex:default',
                    '--clean-room', '--target', str(only_instr), '--task', '检查目录', '--dry-run'))

        ws = self.root / 'r2-07-sub'
        (ws / 'sub').mkdir(parents=True)
        (ws / 'sub' / 'AGENTS.md').write_text('nested agent line\n', encoding='utf-8')
        stage_dir = harness._stage_wsl_readonly_workspace(
            ws, self.root / 'r2-07-stage', [r'sub\AGENTS.md'], exclude_agent_instructions=True
        )
        neutral_name = f'{harness._STAGED_NEUTRAL_PREFIX}AGENTS.md{harness._STAGED_NEUTRAL_SUFFIX}'
        self.assertTrue((stage_dir / 'sub' / neutral_name).is_file())
        inlined = harness._inline_readonly_prompt('审', stage_dir, [r'sub\AGENTS.md'], exclude_agent_instructions=True)
        self.assertIn('=== FILE: sub/AGENTS.md ===', inlined)
        self.assertIn('nested agent line', inlined)

        raw_parsed = {
            'summary': f'已核查 sub/{neutral_name} 与 {neutral_name}',
            'content': f'Windows 路径 sub\\{neutral_name} 与基名 {neutral_name} 一致',
            'read_files': [neutral_name],
            'unread_files': [{'file': f'sub\\{neutral_name}', 'reason': f'skip {neutral_name}'}],
            'evidence': [f'sub/{neutral_name}:1', f'sub\\{neutral_name}:1', f'{neutral_name}:1'],
            'unverified': [f'待复核 sub\\{neutral_name}'],
            'findings': [{
                'file': f'sub\\{neutral_name}', 'line_start': 1, 'line_end': 1, 'severity': 'low',
                'category': 'doc', 'description': f'{neutral_name} 描述', 'evidence': f'sub/{neutral_name}#L1',
            }],
        }
        restored = harness._rewrite_staged_paths(raw_parsed, stage_dir)
        self.assertNotIn(neutral_name, json.dumps(restored, ensure_ascii=False))
        self.assertEqual(restored['read_files'], ['sub/AGENTS.md'])
        self.assertEqual(restored['unread_files'][0]['file'], 'sub/AGENTS.md')
        self.assertEqual(restored['findings'][0]['file'], 'sub/AGENTS.md')
        self.assertEqual(restored['evidence'], ['sub/AGENTS.md:1', 'sub/AGENTS.md:1', 'sub/AGENTS.md:1'])

    def test_r2_08_stop_roots_agents_audit_codex_toml_fenced_script_flags_and_changelog_head(self):
        # 1) stop_roots prevents climbing into $HOME/.git or TMPDIR/.git
        fake_home = self.root / 'fake-home'
        (fake_home / '.git').mkdir(parents=True)
        sub_proj = fake_home / 'notes'
        sub_proj.mkdir()
        (sub_proj / 'memo.md').write_text('# Memo\n', encoding='utf-8')
        with patch.object(harness.Path, 'home', return_value=fake_home):
            self.assertEqual(harness._infer_project_anchor_root(sub_proj / 'memo.md'), sub_proj.resolve())
        fake_tmp = self.root / 'fake-tmp'
        (fake_tmp / '.git').mkdir(parents=True)
        tmp_sub = fake_tmp / 'work'
        tmp_sub.mkdir()
        (tmp_sub / 'memo.md').write_text('# Work\n', encoding='utf-8')
        with patch.object(harness.tempfile, 'gettempdir', return_value=str(fake_tmp)):
            self.assertEqual(harness._infer_project_anchor_root(tmp_sub / 'memo.md'), tmp_sub.resolve())

        # 2) Fake upstream repo: agents/*.md, codex/agents/*.toml, fenced bash/ps1 flags, CHANGELOG latest section
        up = self.root / 'r2-08-upstream'
        (up / 'template' / 'user-guard' / 'agents').mkdir(parents=True)
        (up / 'template' / 'claude' / 'agents').mkdir(parents=True)
        (up / 'template' / 'codex' / 'agents').mkdir(parents=True)
        (up / 'install.py').write_text('p.add_argument("--dry-run")\n', encoding='utf-8')
        (up / 'template' / 'user-guard' / 'plugin.json').write_text('{"name":"user-guard"}\n', encoding='utf-8')
        (up / 'template' / 'user-guard' / 'agents' / 'readonly-auditor.md').write_text(
            '仅使用 `view_file`、`grep_search`、`find_by_name`、`Read`、`Grep`、`Glob`。\n', encoding='utf-8'
        )
        (up / 'template' / 'codex' / 'agents' / 'readonly-auditor.toml').write_text(
            'name = "readonly-auditor"\n'
            'description = "仅使用 Get-Content、Select-String、Get-ChildItem 只读工具。"\n'
            'developer_instructions = "严禁修改文件。"\n'
            '[features]\nrequest_permissions_tool = false\n',
            encoding='utf-8',
        )
        (up / 'README.md').write_text(
            '支持 `request_permissions_tool` 配置。\n\n```powershell\npython "$HARNESS" --dry-run\n```\n',
            encoding='utf-8',
        )
        (up / 'CHANGELOG.md').write_text(
            '# 更新日志\n\n## [2.0.0] - 2026-11-17\n\n支持 `install.py --dry-run`。\n\n'
            '## [1.0.0] - 2025-01-01\n\n旧版曾有 `install.py --retired-ancient-flag` 与 NURBS 刀轨。\n',
            encoding='utf-8',
        )
        clean_up = harness.audit_doc_grounding(up)
        self.assertTrue(clean_up['ok'], clean_up['findings'])
        self.assertIn('CHANGELOG.md', clean_up['checked_files'])
        self.assertIn('template/user-guard/agents/readonly-auditor.md', clean_up['checked_files'])
        self.assertIn('template/codex/agents/readonly-auditor.toml', clean_up['checked_files'])

        # Inject ungrounded items into agent md, codex toml instructions, fenced block, and latest CHANGELOG
        (up / 'template' / 'claude' / 'agents' / 'bad.md').write_text('处理 NURBS 曲面。\n', encoding='utf-8')
        (up / 'template' / 'codex' / 'agents' / 'readonly-auditor.toml').write_text(
            'name = "readonly-auditor"\n'
            'description = "调用 `status_guard.py --ghost-guard-flag`。"\n',
            encoding='utf-8',
        )
        (up / 'README.md').write_text(
            '```bash\nbash install.sh \\\n  --ghost-bash-flag\n```\n'
            '```powershell\npython "$HARNESS" `\n  --ghost-ps-flag\n```\n',
            encoding='utf-8',
        )
        (up / 'CHANGELOG.md').write_text(
            '# 更新日志\n\n## [2.0.0] - 2026-11-17\n\n新增 `launch-cc-switch-wsl.sh --ghost-cl-flag`。\n\n'
            '## [1.0.0] - 2025-01-01\n\n旧版 `install.py --retired-ancient-flag`。\n',
            encoding='utf-8',
        )
        bad_up = harness.audit_doc_grounding(up)
        found_tokens = {f['token'] for f in bad_up['findings']}
        self.assertIn('NURBS', found_tokens)
        self.assertIn('--ghost-guard-flag', found_tokens)
        self.assertIn('--ghost-bash-flag', found_tokens)
        self.assertIn('--ghost-ps-flag', found_tokens)
        self.assertIn('--ghost-cl-flag', found_tokens)
        self.assertNotIn('--retired-ancient-flag', found_tokens)

    def test_r2_09_redact_public_nested_secret_leaves_and_heavy_completion_symlink_inode_lock(self):
        nested_payload = {
            'Authorization': 'CustomSecretHeader 12345',
            'authorizations': ['tok-alpha-1', 'tok-beta-2'],
            'api_keys': ['raw-key-1', {'backup': 'raw-key-2', 'empty': ''}],
            'apiKeys': ['camel-1'],
            'credentials': {'primary_secret_val': 'opaque-secret', 'enabled': True, 'count': 2},
            'token': '--keep-grounding-token',
            'input_tokens': 42,
        }
        redacted = harness._redact_public(nested_payload)
        self.assertEqual(redacted['Authorization'], '<redacted>')
        self.assertEqual(redacted['authorizations'], ['<redacted>', '<redacted>'])
        self.assertEqual(redacted['api_keys'], ['<redacted>', {'backup': '<redacted>', 'empty': ''}])
        self.assertEqual(redacted['apiKeys'], ['<redacted>'])
        self.assertEqual(redacted['credentials'], {'primary_secret_val': '<redacted>', 'enabled': True, 'count': 2})
        self.assertEqual(redacted['token'], '--keep-grounding-token')
        self.assertEqual(redacted['input_tokens'], 42)

        # Symlink / reparse-point rejection in record_heavy_completion_for_stop_hook
        victim = self.root / 'victim.json'
        victim.write_text('{"safe": true}\n', encoding='utf-8')
        plans_dir = self.root / 'r2-09-state' / 'dispatch_plans'
        hc_dir = plans_dir.parent / 'heavy_completions'
        hc_dir.mkdir(parents=True)
        sym_hc = hc_dir / 'sess_sym.json'
        symlink_ok = True
        try:
            sym_hc.symlink_to(victim)
        except OSError:
            symlink_ok = False
        t_file = self.root / 'r2_09.py'
        t_file.write_text('x = 1\n', encoding='utf-8')
        gate_args = self.args('gate', '--target', str(t_file), '--verify-cmd', 'true', '--task', 'fix',
                              '--plan-dir', str(plans_dir), '--session-id', 'sess_sym')
        payload = {'status': 'ok', 'engine': 'codex:fixture-code', 'model': 'fixture-code',
                   'token_usage_summary': {'total_tokens': 10}}
        if symlink_ok:
            with self.assertRaisesRegex(harness.HarnessError, '符号链接'):
                harness.record_heavy_completion_for_stop_hook(gate_args, payload)
            self.assertEqual(json.loads(victim.read_text(encoding='utf-8')), {'safe': True})
        else:
            sym_hc.write_text('[]\n', encoding='utf-8')
            from types import SimpleNamespace as _NS
            orig_lstat = Path.lstat
            def _fake_reparse_lstat(path_self):
                if path_self.name == 'sess_sym.json':
                    return _NS(st_mode=0o100644, st_file_attributes=0x400)
                return orig_lstat(path_self)
            with patch.object(Path, 'lstat', autospec=True, side_effect=_fake_reparse_lstat):
                with self.assertRaisesRegex(harness.HarnessError, '符号链接'):
                    harness.record_heavy_completion_for_stop_hook(gate_args, payload)

        # POSIX flock inode verification retries when lock file is unlinked once during flock
        if os.name != 'nt':
            import fcntl as real_fcntl
            lock_dir = self.root / 'r2-09-locks'
            unlinked_once = {'done': False}
            orig_flock = real_fcntl.flock

            def racing_flock(fd, op):
                orig_flock(fd, op)
                if (op & real_fcntl.LOCK_EX) and not unlinked_once['done']:
                    unlinked_once['done'] = True
                    (lock_dir / '.sess_race.lock').unlink(missing_ok=True)

            with patch.object(real_fcntl, 'flock', side_effect=racing_flock):
                with harness._heavy_completion_file_lock(lock_dir, 'sess_race'):
                    self.assertTrue((lock_dir / '.sess_race.lock').is_file())
            self.assertTrue(unlinked_once['done'])

    def test_r2_10_cli_misuse_guards_unavailable_stream_msg_and_batch_kind_choices(self):
        t_file = self.root / 'r2_10.py'
        t_file.write_text('x = 1\n', encoding='utf-8')
        plans = self.root / 'r2-10-plans'

        with self.assertRaisesRegex(harness.HarnessError, '不支持 --ticket.*--approve-ticket') as e1:
            harness.cmd_plan(self.args('plan', '--ticket', 'abc123'))
        self.assertEqual(e1.exception.status, 'ticket_invalid')

        with self.assertRaisesRegex(harness.HarnessError, '--override-json 仅可配合 --approve-ticket') as e2:
            harness.cmd_plan(self.args('plan', '--override-json', '{"S1":{"tier":"low"}}'))
        self.assertEqual(e2.exception.status, 'ticket_invalid')

        # Headless stream unavailable at approval reports stream_id, channel, and reason (not "原生通道")
        engines = self.four_backends()
        with patch.object(harness, '_args_backends', return_value=engines):
            _, tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--engine', 'codex:fixture-code',
                '--target', str(t_file), '--plan-dir', str(plans), '--save-ticket'))
        # Simulate pre-resolved unavailable headless stream where expected also had unavailable state
        saved = json.loads((plans / f"{tk['ticket_id']}.json").read_text(encoding='utf-8'))
        saved['streams'][0].update(currently_available=False, unavailable_reason='quota_exhausted', quota_exceeded=True)
        harness._save_plan(saved, plans)
        harness._record_quota_exhausted('codex', engines['codex'], 'quota exceeded')
        with patch.object(harness, '_args_backends', return_value=engines), \
             patch.object(harness, '_resolve_plan_streams', return_value=(saved['streams'], False)):
            with self.assertRaises(harness.HarnessError) as unavail_exc:
                harness.cmd_plan(self.args('plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                                           '--approve-ticket', tk['ticket_id']))
        msg = str(unavail_exc.exception)
        self.assertNotIn('原生通道', msg)
        self.assertIn('S1', msg)
        self.assertIn('cae_harness_headless', msg)
        self.assertIn('quota_exhausted', msg)

        # batch --kind choices validation and help text presence
        parser = harness.build_arg_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['batch', '--kind', 'invalid_kind'])
        for sub_name in ('plan', 'batch', 'task', 'gate', 'audit', 'probe'):
            sub_parser = parser._subparsers._group_actions[0].choices[sub_name]
            help_txt = sub_parser.format_help()
            self.assertIn('--redact', help_txt)
            self.assertIn('脱敏', help_txt)

    def test_orthogonal_isolation_modes_and_native_agent_profiles(self):
        target_py = self.root / 'iso_mod.py'
        target_py.write_text('val = 1\n', encoding='utf-8')
        engines = self.four_backends()

        cx_home = self.root / 'iso-codex-home'
        (cx_home / 'hooks').mkdir(parents=True)
        (cx_home / 'agents').mkdir(parents=True)
        (cx_home / 'skills' / 'demo').mkdir(parents=True)
        (cx_home / 'auth.json').write_text('{"token":"ok"}', encoding='utf-8')
        (cx_home / 'models_cache.json').write_text('{"models":[]}', encoding='utf-8')
        (cx_home / 'config.toml').write_text('model = "fixture-code"\n', encoding='utf-8')
        (cx_home / 'AGENTS.md').write_text('# global codex rules\n', encoding='utf-8')
        (cx_home / 'hooks.json').write_text('{"hooks":{}}', encoding='utf-8')
        (cx_home / 'hooks' / 'status_guard.py').write_text('# guard\n', encoding='utf-8')
        (cx_home / 'agents' / 'readonly-auditor.toml').write_text('name = "readonly-auditor"\nmodel = "codex-agent-custom"\n', encoding='utf-8')
        (cx_home / 'skills' / 'demo' / 'SKILL.md').write_text('# skill\n', encoding='utf-8')
        engines['codex']['config_home'] = str(cx_home)

        cl_home = self.root / 'iso-claude-home'
        (cl_home / 'hooks').mkdir(parents=True)
        (cl_home / 'agents').mkdir(parents=True)
        (cl_home / 'skills' / 'demo').mkdir(parents=True)
        (cl_home / 'CLAUDE.md').write_text('# global claude rules\n', encoding='utf-8')
        (cl_home / 'hooks' / 'status_guard.py').write_text('# guard\n', encoding='utf-8')
        (cl_home / 'agents' / 'readonly-auditor.md').write_text('---\nname: readonly-auditor\nmodel: claude-agent-custom\n---\n', encoding='utf-8')
        (cl_home / 'skills' / 'demo' / 'SKILL.md').write_text('# skill\n', encoding='utf-8')
        (cl_home / 'settings.json').write_text(json.dumps({
            'env': {'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8240', 'ANTHROPIC_AUTH_TOKEN': 'via-router'},
            'hooks': {'PreToolUse': [{'hooks': [{'command': 'guard'}]}]},
            'effortLevel': 'xhigh',
        }), encoding='utf-8')
        engines['claude']['config_home'] = str(cl_home)

        dsh_home = self.root / 'iso-dsh-home'
        (dsh_home / 'skills' / 'demo').mkdir(parents=True)
        (dsh_home / '.credentials.yaml').write_text('provider: deepseek\n', encoding='utf-8')
        (dsh_home / 'AGENTS.md').write_text('# global dsh rules\n', encoding='utf-8')
        (dsh_home / 'skills' / 'demo' / 'SKILL.md').write_text('# skill\n', encoding='utf-8')
        engines['dsh']['config_home'] = str(dsh_home)

        # Fail-closed checks
        with self.assertRaisesRegex(harness.HarnessError, 'hooks_only'):
            harness.invoke_engine('agy:default', self.root, 'x', engines, harness.TASK_JSON_SCHEMA, isolation='hooks_only')
        with self.assertRaisesRegex(harness.HarnessError, 'soft_rules_only'):
            harness.invoke_engine('dsh:default', self.root, 'x', engines, harness.TASK_JSON_SCHEMA, isolation='hooks_only')
        with self.assertRaisesRegex(harness.HarnessError, '冲突'):
            harness.invoke_engine('codex:default', self.root, 'x', engines, harness.TASK_JSON_SCHEMA, clean_room=True, isolation='full')

        # agy full omits --disable-slash-commands; default (no_skills) keeps it
        seen_agy_cmds = []
        def fake_agy_run(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            seen_agy_cmds.append(list(command))
            line = json.dumps({'type': 'result', 'structured_output': TASK})
            return subprocess.CompletedProcess(command, 0, line, '')

        with patch.object(harness, 'run_process', side_effect=fake_agy_run):
            r_def = {}
            harness.invoke_engine('agy:default', self.root, '默认', engines, harness.TASK_JSON_SCHEMA, receipt=r_def)
            self.assertEqual(r_def['isolation'], 'no_skills')
            self.assertIn('--disable-slash-commands', seen_agy_cmds[-1])
            r_full = {}
            harness.invoke_engine('agy:default', self.root, '全挂', engines, harness.TASK_JSON_SCHEMA, receipt=r_full, isolation='full')
            self.assertEqual(r_full['isolation'], 'full')
            self.assertNotIn('--disable-slash-commands', seen_agy_cmds[-1])

        # codex no_skills (writable) & hooks_only (writable)
        def check_codex_iso(expected_agents_md):
            def _run(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
                iso_dir = Path(env['CODEX_HOME'])
                self.assertNotEqual(iso_dir.resolve(), cx_home.resolve())
                self.assertTrue((iso_dir / 'auth.json').is_file())
                self.assertTrue((iso_dir / 'hooks.json').is_file())
                self.assertTrue((iso_dir / 'hooks' / 'status_guard.py').is_file())
                self.assertFalse((iso_dir / 'skills').exists())
                self.assertEqual((iso_dir / 'AGENTS.md').is_file(), expected_agents_md)
                out_idx = command.index('-o') + 1
                Path(command[out_idx]).write_text(json.dumps(TASK), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', '')
            return _run

        with patch.object(harness, 'run_process', side_effect=check_codex_iso(True)):
            rc = {}
            harness.invoke_engine('codex:default', self.root, '写', engines, harness.TASK_JSON_SCHEMA, readonly=False, receipt=rc, isolation='no_skills')
            self.assertEqual(rc['isolation'], 'no_skills')
            self.assertFalse(rc['clean_room'])
        with patch.object(harness, 'run_process', side_effect=check_codex_iso(False)):
            rc2 = {}
            harness.invoke_engine('codex:default', self.root, '写', engines, harness.TASK_JSON_SCHEMA, readonly=False, receipt=rc2, isolation='hooks_only')
            self.assertEqual(rc2['isolation'], 'hooks_only')
            self.assertFalse(rc2['clean_room'])

        # claude no_skills, hooks_only (writable), and clean_room (--bare + --disable-slash-commands)
        def fake_cl_no_skills(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            self.assertIn('--disable-slash-commands', command)
            self.assertNotIn('--bare', command)
            return subprocess.CompletedProcess(command, 0, json.dumps({'structured_output': TASK}), '')

        with patch.object(harness, 'run_process', side_effect=fake_cl_no_skills):
            harness.invoke_engine('claude:default', self.root, '写', engines, harness.TASK_JSON_SCHEMA, readonly=False, isolation='no_skills')

        def fake_cl_hooks_only(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            self.assertIn('--disable-slash-commands', command)
            self.assertNotIn('--bare', command)
            iso_cl = Path(env['CLAUDE_CONFIG_DIR'])
            self.assertNotEqual(iso_cl.resolve(), cl_home.resolve())
            self.assertTrue((iso_cl / 'hooks' / 'status_guard.py').is_file())
            self.assertFalse((iso_cl / 'CLAUDE.md').exists())
            self.assertFalse((iso_cl / 'skills').exists())
            cfg = json.loads((iso_cl / 'settings.json').read_text(encoding='utf-8'))
            self.assertIn('hooks', cfg)
            self.assertIn('env', cfg)
            return subprocess.CompletedProcess(command, 0, json.dumps({'structured_output': TASK}), '')

        with patch.object(harness, 'run_process', side_effect=fake_cl_hooks_only):
            harness.invoke_engine('claude:default', self.root, '写', engines, harness.TASK_JSON_SCHEMA, readonly=False, isolation='hooks_only')

        def fake_cl_clean_room(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            self.assertIn('--bare', command)
            self.assertIn('--disable-slash-commands', command)
            return subprocess.CompletedProcess(command, 0, json.dumps({'structured_output': TASK}), '')

        with patch.object(harness, 'run_process', side_effect=fake_cl_clean_room):
            harness.invoke_engine('claude:default', self.root, '盲读', engines, harness.TASK_JSON_SCHEMA, readonly=True, isolation='clean_room')

        # dsh no_skills keeps AGENTS.md and drops skills/
        def fake_dsh_no_skills(command, cwd, input_text, timeout_sec, env=None, native_pid_file=None):
            iso_dsh = Path(env['DSH_HOME'])
            self.assertTrue((iso_dsh / '.credentials.yaml').is_file())
            self.assertTrue((iso_dsh / 'AGENTS.md').is_file())
            self.assertFalse((iso_dsh / 'skills').exists())
            return subprocess.CompletedProcess(command, 0, json.dumps({'type': 'final', 'reason': {'kind': 'completed'}, 'text': json.dumps(TASK)}), '')

        with patch.object(harness, 'run_process', side_effect=fake_dsh_no_skills):
            harness.invoke_engine('dsh:default', self.root, '写', engines, harness.TASK_JSON_SCHEMA, readonly=False, isolation='no_skills')

        # plan card renders explicit isolation & ticket execution rejects mismatched isolation
        plans = self.root / 'iso-plans'
        with patch.object(harness, '_args_backends', return_value=engines):
            _, tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--engine', 'codex:fixture-code', '--kind', 'review',
                '--isolation', 'hooks_only', '--target', str(target_py), '--plan-dir', str(plans),
                '--session-id', 'iso-sess', '--save-ticket', '--full-json', '--workflow-goal', '核对隔离'))
            self.assertEqual(tk['streams'][0]['isolation'], 'hooks_only')
            self.assertFalse(tk['streams'][0]['clean_room'])
            self.assertIn('否（隔离：hooks_only）', tk['human_popup_markdown'])
            self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--session-id', 'iso-sess', '--approve-ticket', tk['ticket_id']))
            with self.assertRaisesRegex(harness.HarnessError, '隔离模式'):
                harness.cmd_task(self.args(
                    'task', '--caller', 'antigravity', '--engine', 'codex:fixture-code', '--kind', 'review',
                    '--target', str(target_py), '--task', '核对隔离', '--plan-dir', str(plans),
                    '--session-id', 'iso-sess', '--ticket', tk['ticket_id'], '--dry-run'))

        # Native agent per-file model resolution & native_agent_profiles metadata
        model_cx, disp_cx, _, _ = harness._native_plan_model('codex', engines['codex'], 'inherit', agent_type='readonly-auditor')
        self.assertEqual(model_cx, 'codex-agent-custom')
        self.assertEqual(disp_cx, 'inherit -> codex-agent-custom (agent:readonly-auditor)')
        model_cl, disp_cl, _, _ = harness._native_plan_model('claude', engines['claude'], 'inherit', agent_type='readonly-auditor')
        self.assertEqual(model_cl, 'claude-agent-custom')
        self.assertEqual(disp_cl, 'inherit -> claude-agent-custom (agent:readonly-auditor)')

        custom_pf = self.root / 'native-pf.json'
        base_pf = json.loads((Path(harness.__file__).resolve().parent.parent / 'fleet_profile.json').read_text(encoding='utf-8'))
        base_pf['native_agent_profiles'] = {'codex': {'main_model': 'fixture-code', 'max_parallel_subagents': 3}}
        custom_pf.write_text(json.dumps(base_pf, ensure_ascii=False), encoding='utf-8')
        with patch.dict(os.environ, {'CAE_FLEET_PROFILE': str(custom_pf)}), \
             patch.object(harness, '_args_backends', return_value=engines):
            self.assertIn('native_agent_profiles', harness._fleet_profile_summary())
            _, ntk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'codex', '--channel', 'native_subagent', '--agent-type', 'readonly-auditor',
                '--target', str(target_py), '--plan-dir', str(plans)))
            self.assertEqual(ntk['streams'][0]['model'], 'codex-agent-custom')
            self.assertEqual(ntk['streams'][0]['execution_profile']['native_agent_profile']['max_parallel_subagents'], 3)

    def test_v1171_audit_ticket_verify_cmd_resume_approved_and_redaction(self):
        engines = self.four_backends()
        target_py = self.root / 'v1171_mod.py'
        target_py.write_text('val = 1\n', encoding='utf-8')
        plans = self.root / 'v1171-plans'

        with patch.object(harness, '_args_backends', return_value=engines):
            _, tk = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--engine', 'codex:fixture-code', '--kind', 'gate',
                '--target', str(self.root), '--write-scope', 'v1171_mod.py',
                '--verify-cmd', 'pytest -q', '--plan-dir', str(plans),
                '--session-id', 'v1171-s', '--user-prompt', '用 codex 写并跑测试', '--save-ticket'))
            _, app = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--session-id', 'v1171-s', '--approve-ticket', tk['ticket_id'], '--full-json'))
            self.assertEqual(app['status'], 'APPROVED')

            # 1) _check_ticket_execution rejects empty/None/mismatched verify_cmd on writable gate
            prof = app['streams'][0]['execution_profile']
            for bad_verify in ('', None, 'echo evil'):
                gate_args = self.args(
                    'gate', '--caller', 'antigravity', '--worker', 'codex:fixture-code',
                    '--target', str(self.root), '--task', app['streams'][0]['subtask'],
                    '--write-scope', 'v1171_mod.py',
                    '--verify-cmd', bad_verify or 'pytest -q', '--plan-dir', str(plans),
                    '--session-id', 'v1171-s', '--ticket', tk['ticket_id'])
                harness._begin_dispatch(gate_args)
                with self.subTest(bad_verify=bad_verify), self.assertRaisesRegex(harness.HarnessError, '验收命令'):
                    harness._check_ticket_execution(
                        gate_args, prof, self.root, 'gate', app['streams'][0]['subtask'],
                        write_scope=['v1171_mod.py'], verify_cmd=bad_verify)

            # 2) plan --resume-ticket on APPROVED ticket preserves user_prompt & doesn't rewrite disk
            disk_before = (plans / f"{tk['ticket_id']}.json").read_bytes()
            _, resumed = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--resume-ticket', tk['ticket_id']))
            self.assertEqual(resumed['status'], 'APPROVED')
            self.assertEqual(resumed['user_prompt'], '用 codex 写并跑测试')
            self.assertEqual((plans / f"{tk['ticket_id']}.json").read_bytes(), disk_before)

            # 3) _plan_dir_lock does not deadlock with ticket_plan_dir lock namespace
            with harness._plan_dir_lock(plans), harness._directory_file_lock(plans, 'ticket_plan_dir', label='计划票据'):
                pass

            # 4) _redact_runtime_secret masks hyphenated headers, Basic auth & proxy URLs
            red = harness._redact_runtime_secret(
                'x-api-key: secret_tok_123456 Authorization: Basic dXNlcjpwYXNz socks5://u:p@127.0.0.1:1080'
            )
            self.assertNotIn('secret_tok_123456', red)
            self.assertNotIn('dXNlcjpwYXNz', red)
            self.assertNotIn('u:p@', red)

    def test_v118_compact_plan_cancel_supersede_mixed_batch_redact_dsh_agy_and_fast_metrics(self):
        engines = self.four_backends()
        target_py = self.root / 'v118_mod.py'
        target_py.write_text('val = 1\n', encoding='utf-8')
        plans = self.root / 'v118-plans'

        with patch.object(harness, '_args_backends', return_value=engines):
            # 1) 落盘票据默认 stdout 为精简视图（置顶 compact_summary_card，不含 14 项长表与 execution_profile）
            _, tk_old = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--engine', 'codex:fixture-code', '--kind', 'review',
                '--target', str(target_py), '--plan-dir', str(plans),
                '--session-id', 'v118-sess', '--save-ticket',
                '--workflow-bg', '草案1', '--workflow-goal', '初版检查', '--workflow-impact', '只读'))
            self.assertEqual(tk_old.get('stdout_view'), 'compact')
            self.assertIn('compact_summary_card', tk_old)
            self.assertIn('compact_handoff_prompt_block', tk_old)
            self.assertNotIn('human_popup_markdown', tk_old)
            self.assertNotIn('handoff_prompt_block', tk_old)
            self.assertNotIn('execution_profile', tk_old['streams'][0])
            # 磁盘 JSON 与 _card.md 依然完整落盘
            disk_old = json.loads((plans / f"{tk_old['ticket_id']}.json").read_text(encoding='utf-8'))
            self.assertIn('human_popup_markdown', disk_old)
            self.assertIn('execution_profile', disk_old['streams'][0])
            self.assertTrue(Path(disk_old['card_detail_file']).is_file())

            # 同会话另建一张不同通道（native_workflow）的并发 PENDING 票据 + 一张同通道（headless）的新票据
            _, tk_native_concurrent = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--channel', 'native_workflow', '--kind', 'review',
                '--target', str(target_py), '--plan-dir', str(plans),
                '--session-id', 'v118-sess', '--save-ticket',
                '--workflow-bg', '并发原生', '--workflow-goal', '原生只读', '--workflow-impact', '只读'))
            spec_mixed = self.root / 'v118-mixed.json'
            spec_mixed.write_text(json.dumps([
                {'stream_id': 'H1', 'channel': 'cae_harness_headless', 'kind': 'review',
                 'engine': 'codex:fixture-code', 'target': str(target_py), 'subtask': '无头审查', 'timeout': 60},
                {'stream_id': 'N1', 'channel': 'native_subagent', 'kind': 'review',
                 'agent_type': 'readonly-reader', 'target': str(target_py), 'subtask': '原生读取', 'timeout': 60},
            ]), encoding='utf-8')
            _, tk_new = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--spec-file', str(spec_mixed), '--plan-dir', str(plans),
                '--session-id', 'v118-sess', '--workflow-bg', '定版方案', '--workflow-goal', '混合执行', '--workflow-impact', '只读'))

            # 2) 批准新票据：自动将同通道更早 PENDING 标为 SUPERSEDED，不误伤不同通道并发 PENDING；APPROVED 精简输出省略 compact_handoff_prompt_block
            _, app_new = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--session-id', 'v118-sess', '--approve-ticket', tk_new['ticket_id']))
            self.assertEqual(app_new['status'], 'APPROVED')
            self.assertNotIn('compact_handoff_prompt_block', app_new)
            self.assertIn(tk_old['ticket_id'], app_new.get('superseded_ticket_ids', []))
            self.assertEqual(json.loads((plans / f"{tk_old['ticket_id']}.json").read_text(encoding='utf-8'))['status'], 'SUPERSEDED')
            self.assertEqual(json.loads((plans / f"{tk_native_concurrent['ticket_id']}.json").read_text(encoding='utf-8'))['status'], 'PENDING')

            # 3) --cancel-ticket 可取消待决票据并清理 pending 指针
            _, cancelled = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                '--session-id', 'v118-sess', '--cancel-ticket', tk_native_concurrent['ticket_id']))
            self.assertEqual(cancelled['status'], 'CANCELLED')
            self.assertEqual(json.loads((plans / f"{tk_native_concurrent['ticket_id']}.json").read_text(encoding='utf-8'))['status'], 'CANCELLED')

            # 4) cmd_batch 混合通道票据：执行无头流并报告 skipped_native_streams，且透传 effort/retry_quota/安全包装 prompt
            captured_calls = []
            def fake_batch_invoke(spec, root, prompt, backends, schema, readonly, timeout, *pargs, **kwargs):
                captured_calls.append({'spec': spec, 'prompt': prompt, 'kwargs': kwargs})
                return dict(TASK)
            with patch.object(harness, 'invoke_engine', side_effect=fake_batch_invoke), \
                 patch.dict(os.environ, {'CAE_DISABLE_HEAVY_COMPLETIONS': '1'}):
                rc_mix, mix_res = self.capture(harness.cmd_batch, self.args(
                    'batch', '--caller', 'antigravity', '--plan-dir', str(plans),
                    '--session-id', 'v118-sess', '--ticket', tk_new['ticket_id'], '--retry-quota'))
            self.assertEqual(rc_mix, 0)
            self.assertEqual(len(mix_res['streams']), 1)
            self.assertEqual(mix_res['streams'][0]['stream_id'], 'H1')
            self.assertEqual(len(mix_res.get('skipped_native_streams', [])), 1)
            self.assertEqual(mix_res['skipped_native_streams'][0]['stream_id'], 'N1')
            self.assertEqual(len(captured_calls), 1)
            self.assertTrue(captured_calls[0]['kwargs'].get('retry_quota'))
            self.assertIn('不要调用本编排器', captured_calls[0]['prompt'])
            self.assertIn('无头审查', captured_calls[0]['prompt'])

        # 5) _redact_public 保留科学百分比，精准脱敏额度百分比
        sci_payload = {
            'quota_summary': '5h_window: 42.5% used',
            'human_popup_markdown': '| 2 | 子任务 | 收敛率 99.5% 与 100% 覆盖 |\n| 13 | 额度状态 | 5h_window: 38% used |',
            'result': {
                'summary': '覆盖率达到 100%，误差降至 0.1%',
                'content': '残差下降 95.2%，已用 20% 显存仍在阈值内',
                'evidence': ['solver.py:10 (99.8% match)'],
                'unverified': ['边界 0.5% 扰动未测'],
            },
        }
        red_sci = harness._redact_public(sci_payload)
        self.assertIn('<USAGE>', red_sci['quota_summary'])
        self.assertNotIn('42.5%', red_sci['quota_summary'])
        self.assertIn('99.5%', red_sci['human_popup_markdown'])
        self.assertIn('100%', red_sci['human_popup_markdown'])
        self.assertIn('<USAGE>', red_sci['human_popup_markdown'])
        self.assertNotIn('38%', red_sci['human_popup_markdown'])
        self.assertIn('100%', red_sci['result']['summary'])
        self.assertIn('0.1%', red_sci['result']['summary'])
        self.assertIn('95.2%', red_sci['result']['content'])
        self.assertIn('20%', red_sci['result']['content'])
        self.assertIn('99.8%', red_sci['result']['evidence'][0])
        self.assertIn('0.5%', red_sci['result']['unverified'][0])

        # 6) extract_cli_token_usage 解析 DSH cacheReadTokens（含防重复计数）
        dsh_usage_raw = json.dumps({
            'type': 'final',
            'usage': {'inputTokens': 800, 'cacheReadTokens': 12000, 'cacheWriteTokens': 0, 'outputTokens': 500, 'totalTokens': 13300},
        })
        dsh_tok = harness.extract_cli_token_usage('dsh', dsh_usage_raw)
        self.assertEqual(dsh_tok['input_tokens'], 12800)
        self.assertEqual(dsh_tok['cached_input_tokens'], 12000)
        self.assertEqual(dsh_tok['output_tokens'], 500)
        self.assertEqual(dsh_tok['total_tokens'], 13300)
        dsh_already_incl = json.dumps({
            'type': 'final',
            'usage': {'inputTokens': 12800, 'cacheReadTokens': 12000, 'outputTokens': 500, 'totalTokens': 13300},
        })
        self.assertEqual(harness.extract_cli_token_usage('dsh', dsh_already_incl)['input_tokens'], 12800)

        # 7) _extract_json_object 保守修复尾部缺失 ]，但不修复截断未闭合字符串
        missing_bracket = json.dumps({
            'type': 'final',
            'reason': {'kind': 'completed'},
            'text': '{"summary": "完成核查", "content": "正文", "evidence": ["a.py:1"], "unverified": ["未经运行验证。"}',
        }, ensure_ascii=False)
        repaired = harness._extract_json_object(missing_bracket, harness.TASK_JSON_SCHEMA)
        self.assertEqual(repaired['unverified'], ['未经运行验证。'])
        truncated_str = '{"summary": "完成核查", "content": "未闭合'
        with self.assertRaises(harness.OutputParseError):
            harness._extract_json_object(truncated_str, harness.TASK_JSON_SCHEMA)

        # 8) agy 只读在 Git 仓库子目录附带 --add-dir，且空输出附带 stderr/diagnostic_hint
        git_repo = self.root / 'v118-git-repo'
        (git_repo / '.git').mkdir(parents=True)
        sub_dir = git_repo / 'docs' / 'sub'
        sub_dir.mkdir(parents=True)
        (sub_dir / 'note.md').write_text('# note\n', encoding='utf-8')
        seen_agy_cmds = []
        def fake_agy_soft_deny(cmd, cwd, input_text=None, timeout_sec=60, env=None, native_pid_file=None):
            seen_agy_cmds.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, '', 'Print mode: soft-denying tool confirmation "ReadFile" at step 2')
        with patch.object(harness, 'run_process', side_effect=fake_agy_soft_deny):
            with self.assertRaises(harness.OutputParseError) as parse_ctx:
                harness.invoke_engine('agy:default', sub_dir, '审查', engines, harness.TASK_JSON_SCHEMA, readonly=True)
        self.assertTrue(seen_agy_cmds)
        self.assertIn('--add-dir', seen_agy_cmds[0])
        self.assertEqual(seen_agy_cmds[0][seen_agy_cmds[0].index('--add-dir') + 1], str(git_repo.resolve()))
        self.assertIn('soft-denying', parse_ctx.exception.details.get('stderr', ''))
        self.assertIn('diagnostic_hint', parse_ctx.exception.details)

        # 9) _dsh_supports_cli_permission_flags 并发加锁仅探测 1 次且超时为 5.0s
        import concurrent.futures
        fake_dsh_bin = self.root / 'fake_dsh_v118.cmd'
        fake_dsh_bin.write_text('@echo off\n', encoding='utf-8')
        probe_calls = []
        def fake_dsh_help(cmd, cwd, input_text=None, timeout_sec=60, env=None, native_pid_file=None):
            probe_calls.append(timeout_sec)
            time.sleep(0.05)
            return subprocess.CompletedProcess(cmd, 0, 'Usage: dsh --profile headless --permission-mode read-only', '')
        harness._DSH_PERMISSION_CACHE.clear()
        with patch.object(harness, 'run_process', side_effect=fake_dsh_help):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                flags = list(pool.map(lambda _: harness._dsh_supports_cli_permission_flags(str(fake_dsh_bin)), range(4)))
        self.assertEqual(flags, [True, True, True, True])
        self.assertEqual(len(probe_calls), 1)
        self.assertEqual(probe_calls[0], 5.0)

        # 10) _target_metrics 大目录零 open() 读内容、遵循 .agentignore、优先按 write_scope 估算并缓存
        big_ws = self.root / 'v118-big-ws'
        (big_ws / 'src').mkdir(parents=True)
        (big_ws / 'results').mkdir(parents=True)
        (big_ws / '.agentignore').write_text('results/\n', encoding='utf-8')
        (big_ws / 'src' / 'a.py').write_text('x = 1\n' * 10, encoding='utf-8')
        (big_ws / 'src' / 'b.py').write_text('y = 2\n' * 10, encoding='utf-8')
        for idx in range(5):
            (big_ws / 'results' / f'out_{idx}.json').write_text('{"v": 1}\n' * 500, encoding='utf-8')
        cache = {}
        cnt_dir, tok_dir = harness._target_metrics(big_ws, 'check', cache=cache)
        self.assertEqual(cnt_dir, 2)  # results/ ignored by .agentignore
        cnt_scoped, tok_scoped = harness._target_metrics(big_ws, 'edit', write_scope=['src/a.py'], cache=cache)
        self.assertEqual(cnt_scoped, 1)
        self.assertLess(tok_scoped, tok_dir)

    def test_agy_carbon_main_neon_coding_and_native_swarm_routing(self):
        engines = self.four_backends()
        engines['agy']['models'] = [
            {'slug': 'gemini-4-neon-xhigh', 'display_name': 'Gemini 4 Neon (XHigh)'},
            {'slug': 'gemini-4-neon-high', 'display_name': 'Gemini 4 Neon (High)'},
            {'slug': 'gemini-4-neon-medium', 'display_name': 'Gemini 4 Neon (Medium)'},
            {'slug': 'gemini-4-neon-low', 'display_name': 'Gemini 4 Neon (Low)'},
            {'slug': 'gemini-4-carbon-high', 'display_name': 'Gemini 4 Carbon (High)'},
            {'slug': 'gemini-4-carbon-medium', 'display_name': 'Gemini 4 Carbon (Medium)'},
            {'slug': 'gemini-4-carbon-low', 'display_name': 'Gemini 4 Carbon (Low)'},
            {'slug': 'gemini-4-argon-high', 'display_name': 'Gemini 4 Argon (High)'},
            {'slug': 'gemini-4-hydrogen-xhigh', 'display_name': 'Gemini 4 Hydrogen (XHigh)'},
            {'slug': 'gemini-4-hydrogen-low', 'display_name': 'Gemini 4 Hydrogen (Low)'},
            {'slug': 'gemini-pro-orchestrator-neon', 'display_name': 'Orchestrator (Neon)'},
            {'slug': 'gemini-pro-orchestrator', 'display_name': 'Orchestrator (Carbon)'},
        ]

        # 1) 外部无头路由：主控/审查/数理默认低幻觉 carbon，编码与门禁自动路由 neon，省额度路由 hydrogen
        orch = harness.resolve_execution_profile(engines, 'agy:@orchestrator', 'high', '需求权衡与统筹', kind='review')
        rev = harness.resolve_execution_profile(engines, 'agy:@review', 'max', '逐行审查离散边界条件', kind='review')
        rev_deep = harness.resolve_execution_profile(engines, 'agy:default', 'max', '仔细核查 DGSEM 熵守恒边界', kind='review')
        math_p = harness.resolve_execution_profile(engines, 'agy:@math', 'max', '推导 Roe 通量雅可比矩阵', kind='math')
        pol_p = harness.resolve_execution_profile(engines, 'agy:default', 'medium', '润色段落', kind='polish')
        self.assertEqual((orch['model'], orch['profile_source']), ('gemini-4-carbon-high', 'role_specialty_orchestrator'))
        self.assertEqual((rev['model'], rev['profile_source']), ('gemini-4-carbon-high', 'role_specialty_review'))
        self.assertEqual((rev_deep['model'], rev_deep['profile_source']), ('gemini-4-carbon-high', 'specialty_deep_reasoning'))
        self.assertEqual((math_p['model'], math_p['profile_source']), ('gemini-4-carbon-high', 'role_specialty_math'))
        self.assertEqual((pol_p['model'], pol_p['profile_source']), ('gemini-4-carbon-medium', 'frontier_catalog'))

        code_role = harness.resolve_execution_profile(engines, 'agy:@code', 'max', '实现稀疏矩阵装配', kind='code')
        code_def = harness.resolve_execution_profile(engines, 'agy:default', 'max', '实现稀疏矩阵装配', kind='code')
        gate_def = harness.resolve_execution_profile(engines, 'agy:default', 'max', '修复并验证单元测试', kind='gate')
        code_low = harness.resolve_execution_profile(engines, 'agy:@code', 'low', '显式编码角色降挡', kind='code')
        fast_role = harness.resolve_execution_profile(engines, 'agy:@fast', 'low', '省额度快检', kind='review')
        self.assertEqual((code_role['model'], code_role['reasoning_effort'], code_role['profile_source']),
                         ('gemini-4-neon-xhigh', 'xhigh', 'role_specialty_code'))
        self.assertEqual((code_def['model'], code_def['profile_source']), ('gemini-4-neon-xhigh', 'frontier_catalog'))
        self.assertEqual((gate_def['model'], gate_def['profile_source']), ('gemini-4-neon-xhigh', 'frontier_catalog'))
        self.assertEqual((code_low['model'], code_low['profile_source']), ('gemini-4-neon-low', 'role_specialty_code'))
        self.assertEqual((fast_role['model'], fast_role['profile_source']), ('gemini-4-hydrogen-low', 'role_specialty_fast'))

        # P0 显式/口语点名仍优先覆盖自动专长分流
        p0_neon_rev = harness.resolve_execution_profile(engines, 'agy:neon', 'max', '显式点名 neon 审查', kind='review')
        p0_carbon_code = harness.resolve_execution_profile(engines, 'agy:carbon', 'max', '显式点名 carbon 编码', kind='code')
        p0_fuzzy = harness.resolve_execution_profile(engines, 'agy:default', 'max', '请调用 neon 模型审查', kind='review')
        self.assertEqual(p0_neon_rev['model'], 'gemini-4-neon-xhigh')
        self.assertEqual(p0_carbon_code['model'], 'gemini-4-carbon-high')
        self.assertEqual((p0_fuzzy['model'], p0_fuzzy['profile_source']), ('gemini-4-neon-xhigh', 'prompt_fuzzy_model'))

        # role:code 在 codex 不可用且跨端选路时，agy:@code 排在 claude/dsh 之前并路由至 gemini-4-neon-xhigh
        no_codex = {k: dict(v) for k, v in engines.items()}
        no_codex['codex'] = {**engines['codex'], 'available': False}
        chosen_spec = harness.choose_engine(no_codex, 'dsh', 'role:code', '多文件编码实现')
        self.assertEqual(chosen_spec, 'agy:@code')
        chosen_prof = harness.resolve_execution_profile(no_codex, chosen_spec, 'max', '多文件编码实现', kind='code')
        self.assertEqual(chosen_prof['model'], 'gemini-4-neon-xhigh')

        # 2) 内部原生 native_swarm：code/gate 自动推荐 Neon High（可审批与微调），review/显式 inherit/P0/native_subagent 保持 inherit
        target = self.root / 'swarm_mod.py'
        target.write_text('def solve():\n    return 1\n', encoding='utf-8')
        plans = self.root / 'swarm_plans'
        agy_home = Path(engines['agy']['config_home'])
        (agy_home / 'config').mkdir(parents=True, exist_ok=True)
        (agy_home / 'config' / 'config.json').write_text(
            json.dumps({'userSettings': {'model': 'Gemini 4 Carbon (High)'}}), encoding='utf-8',
        )
        common = ['--caller', 'antigravity', '--plan-dir', str(plans), '--target', str(target), '--timeout', '120']
        with patch.object(harness, '_args_backends', return_value=engines):
            _, sw_code = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_swarm', '--kind', 'code',
                          '--write-scope', 'swarm_mod.py', '--verify-cmd', 'python -B swarm_mod.py',
                          '--save-ticket', '--full-json'),
            )
            s1 = sw_code['streams'][0]
            self.assertEqual(s1['model'], 'gemini-4-neon-high')
            self.assertEqual(s1['tier'], 'high')
            self.assertIn('Gemini 4 Neon (High)', s1['model_display'])
            self.assertEqual(s1['execution_profile']['profile_source'], 'native_swarm_worker_profile')

            # 直接批准与微调 tier -> xhigh 均无漂移报错
            _, sw_approved = self.capture(
                harness.cmd_plan,
                self.args('plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                          '--approve-ticket', sw_code['ticket_id'], '--full-json'),
            )
            self.assertEqual(sw_approved['status'], 'APPROVED')
            self.assertEqual(sw_approved['streams'][0]['model'], 'gemini-4-neon-high')

            _, sw_code2 = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_swarm', '--kind', 'code',
                          '--write-scope', 'swarm_mod.py', '--save-ticket', '--full-json'),
            )
            _, sw_ov = self.capture(
                harness.cmd_plan,
                self.args('plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                          '--approve-ticket', sw_code2['ticket_id'],
                          '--override-json', '{"S1":{"tier":"xhigh"}}', '--full-json'),
            )
            self.assertEqual(sw_ov['status'], 'APPROVED')
            self.assertEqual(sw_ov['streams'][0]['model'], 'gemini-4-neon-xhigh')
            self.assertEqual(sw_ov['streams'][0]['tier'], 'xhigh')

            # gate + 显式 --tier xhigh 命中 gemini-4-neon-xhigh
            _, sw_gate = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_swarm', '--kind', 'gate', '--tier', 'xhigh',
                          '--write-scope', 'swarm_mod.py', '--verify-cmd', 'python -B swarm_mod.py', '--full-json'),
            )
            self.assertEqual(sw_gate['streams'][0]['model'], 'gemini-4-neon-xhigh')

            # review（Swarm verifier）、显式 --model inherit、P0 点名 carbon 与 native_subagent 均保持 inherit
            _, sw_rev = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_swarm', '--kind', 'review', '--full-json'),
            )
            self.assertEqual(sw_rev['streams'][0]['model_display'], 'inherit (Gemini 4 Carbon (High))')
            _, sw_inh = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_swarm', '--kind', 'code',
                          '--model', 'inherit', '--write-scope', 'swarm_mod.py', '--full-json'),
            )
            self.assertEqual(sw_inh['streams'][0]['model_display'], 'inherit (Gemini 4 Carbon (High))')
            _, sw_p0 = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_swarm', '--kind', 'code',
                          '--user-prompt', '请用 carbon 模型跑 swarm', '--write-scope', 'swarm_mod.py', '--full-json'),
            )
            self.assertEqual(sw_p0['streams'][0]['model_display'], 'inherit (Gemini 4 Carbon (High))')
            _, sub_code = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_subagent', '--kind', 'code',
                          '--write-scope', 'swarm_mod.py', '--full-json'),
            )
            self.assertEqual(sub_code['streams'][0]['model_display'], 'inherit (Gemini 4 Carbon (High))')

            # 3) 当成熟工程仓前端主控切至 Neon High 时，native_swarm 的 review/math 验收流无感推荐异构低幻觉 Carbon High
            (agy_home / 'config' / 'config.json').write_text(
                json.dumps({'userSettings': {'model': 'Gemini 4 Neon (High)'}}), encoding='utf-8',
            )
            _, sw_het_rev = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_swarm', '--kind', 'review',
                          '--save-ticket', '--full-json'),
            )
            self.assertEqual(sw_het_rev['streams'][0]['model'], 'gemini-4-carbon-high')
            self.assertEqual(sw_het_rev['streams'][0]['tier'], 'high')
            self.assertIn('native_swarm 异构验收推荐', sw_het_rev['streams'][0]['model_display'])
            _, sw_het_ok = self.capture(
                harness.cmd_plan,
                self.args('plan', '--caller', 'antigravity', '--plan-dir', str(plans),
                          '--approve-ticket', sw_het_rev['ticket_id'], '--full-json'),
            )
            self.assertEqual(sw_het_ok['status'], 'APPROVED')
            self.assertEqual(sw_het_ok['streams'][0]['model'], 'gemini-4-carbon-high')

            # 显式 --model inherit 仍可覆盖异构推荐
            _, sw_het_inh = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--channel', 'native_swarm', '--kind', 'review',
                          '--model', 'inherit', '--full-json'),
            )
            self.assertEqual(sw_het_inh['streams'][0]['model_display'], 'inherit (Gemini 4 Neon (High))')

        # 4) 单端受限或排除其余后端时，audit 自动配对同端异构双子星（Carbon High + Neon XHigh）
        left_excl, right_excl = harness._audit_engines(
            engines, True, '', '', False, 'antigravity', '不要用 codex、claude、dsh，只用反重力双路审查',
        )
        self.assertEqual((left_excl, right_excl), ('agy:default', 'agy:neon'))
        audit_plan = harness.select_audit_plan(
            120, [{'rel_path': 'swarm_mod.py', 'tokens': 20}], engines,
            dual_blind=True, caller='antigravity', focus='不要用 codex、claude、dsh，只用反重力双路审查',
        )
        self.assertEqual(audit_plan['execution_profiles']['left']['model'], 'gemini-4-carbon-high')
        self.assertEqual(audit_plan['execution_profiles']['right']['model'], 'gemini-4-neon-xhigh')

        only_agy = {k: ({**v, 'available': False} if k != 'agy' else dict(v)) for k, v in engines.items()}
        left_only, right_only = harness._audit_engines(only_agy, True, '', '', False, 'antigravity', '审查离散通量')
        self.assertEqual((left_only, right_only), ('agy:default', 'agy:neon'))
        left_only_phrase, right_only_phrase = harness._audit_engines(
            engines, True, '', '', False, 'antigravity', '只用反重力双路审查',
        )
        self.assertEqual((left_only_phrase, right_only_phrase), ('agy:default', 'agy:neon'))
        self.assertEqual(harness._extract_negated_backends('不只用反重力审查'), set())

        # 5) 正向单端 P0 点名在双盲 audit 下锁定同端，不将右路泄漏至未授权客户端；终态票据防复活
        left_pos, right_pos = harness._audit_engines(
            engines, True, '', '', False, 'codex', '请用反重力审查这段离散通量',
        )
        self.assertEqual((left_pos, right_pos), ('agy:default', 'agy:neon'))

        engines_lite_cc = json.loads(json.dumps(engines))
        engines_lite_cc['claude'].update(model='deepseek-flash[1M]', models=[{'slug': 'deepseek-flash[1M]'}], llm_source='ccswitch_card')
        self.assertFalse(harness._peer_auto_ready(engines_lite_cc, 'claude'))
        self.assertTrue(harness._peer_auto_ready(engines_lite_cc, 'claude', allow_lightweight=True))

        plan_dir = self.root / 'terminal_tickets'
        with patch.object(harness, '_args_backends', return_value=engines):
            _, created = self.capture(
                harness.cmd_plan,
                self.args('plan', '--target', str(target), '--caller', 'antigravity',
                          '--channel', 'native_swarm', '--kind', 'code', '--session-id', 'sess-term',
                          '--plan-dir', str(plan_dir), '--save-ticket', '--full-json'),
            )
            tid = created['ticket_id']
            harness.cmd_plan(self.args('plan', '--cancel-ticket', tid, '--session-id', 'sess-term', '--plan-dir', str(plan_dir)))
            with self.assertRaisesRegex(harness.HarnessError, '终态票据'):
                harness.cmd_plan(self.args('plan', '--approve-ticket', tid, '--session-id', 'sess-term', '--plan-dir', str(plan_dir)))
            harness.cmd_plan(self.args('plan', '--resume-ticket', tid, '--session-id', 'sess-term', '--plan-dir', str(plan_dir)))
            disk_after = json.loads((plan_dir / f'{tid}.json').read_text(encoding='utf-8'))
            self.assertEqual(disk_after['status'], 'CANCELLED')

    def test_desensitization_cgnat_jwt_pem_url_punct_and_batch_polish_verify_env(self):
        # 1) _redact_public 覆盖 RFC 6598 共享内网段、IPv6 链路本地与 Markdown 分隔符边界
        cgnat_a = '100.' + '64.12.34'
        cgnat_b = '100.' + '100.100.100'
        v6_ll = 'febf' + '::1a2b'
        ip_sample = harness._redact_public(f'cgnat={cgnat_a} tailscale={cgnat_b} v6={v6_ll} pub=8.8.8.8')
        self.assertEqual(ip_sample, 'cgnat=<PRIVATE_IP> tailscale=<PRIVATE_IP> v6=<PRIVATE_IP> pub=8.8.8.8')
        md_in = '| /ho' + 'me/otheralice | (/Us' + 'ers/otherbob) | `C:\\Us' + 'ers\\othercarol` |'
        md_home = harness._redact_public(md_in)
        self.assertEqual(md_home, '| <HOME> | (<HOME>) | `<HOME>` |')
        self.assertIn('<HOME>/proj', harness._redact_public({'/mnt/c/Users/<user>/proj': 'ok'}))

        # 2) _redact_runtime_secret 保留 URL 外侧括号/引号，擦除多协议凭据、JWT、ya29.、ASIA、xox 与 PEM 私钥
        jwt_tok = 'eyJhbGciOiJIUzI1NiJ9.' + 'eyJzdWIiOiIxMjM0NTY3ODkwIn0.' + 'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c'
        pem_hdr = '-----BEGIN ' + 'EC PRIVATE KEY-----'
        pem_ftr = '-----END ' + 'EC PRIVATE KEY-----'
        pem_block = f'{pem_hdr}\nMHcCAQEEII1234567890abcdef==\n{pem_ftr}'
        at_ch = '@'
        raw_secret_msg = (
            f"[endpoint](https://u:p{at_ch}example.com:8443/v1/chat?api_key=sec123) "
            f"('socks4://proxyu:proxyp{at_ch}127.0.0.1:1080') "
            f"dsn=postgresql://dbu:dbp{at_ch}example.org:5432/prod "
            f"jwt={jwt_tok} gtok={'ya29.' + 'a0AfH6SMBx1234567890abcdefGHIJ'} "
            f"aws={'ASIA' + 'IOSFODNN7EXAMPLE'} slack={'xoxb-' + '1234567890-abcdefGHIJ'} {pem_block}"
        )
        scrubbed_msg = harness._redact_runtime_secret(raw_secret_msg)
        self.assertIn('[endpoint](https://example.com:8443/v1/chat)', scrubbed_msg)
        self.assertIn("('<redacted-url>')", scrubbed_msg)
        self.assertIn('postgresql://<redacted>@example.org:5432/prod', scrubbed_msg)
        for leaked in ('u:p', 'sec123', 'proxyu', 'dbu:dbp', jwt_tok, 'ya29.a0Af', 'ASIA' + 'IOSFODNN7EXAMPLE', 'xoxb-123456', 'MHcCAQEE'):
            self.assertNotIn(leaked, scrubbed_msg)
        self.assertIn('<redacted-private-key>', scrubbed_msg)

        dict_redacted = harness._redact_public({
            'id_token': jwt_tok,
            'sessionToken': 'sess_secret_abcdef1234',
            'passwd': '12345678',
            'passphrase': 'correct-horse-battery',
            'Set-Cookie': 'sid=secretcookievalue123; HttpOnly',
            'token': '--keep-grounding-token',
            'input_tokens': 42,
        })
        for secret_k in ('id_token', 'sessionToken', 'passwd', 'passphrase', 'Set-Cookie'):
            self.assertEqual(dict_redacted[secret_k], '<redacted>')
        self.assertEqual(dict_redacted['token'], '--keep-grounding-token')
        self.assertEqual(dict_redacted['input_tokens'], 42)

        # 3) cmd_batch polish 流生成 grounding_audit，且 gate verify_cmd 使用清洗后的隔离环境变量
        ws = self.root / 'batch_polish_gate_ws'
        ws.mkdir()
        doc_file = ws / 'README.md'
        doc_file.write_text('# Solver Guide\n\nRun `solver.py --mesh grid.msh`.\n', encoding='utf-8')
        (ws / 'solver.py').write_text('def main():\n    return "--mesh"\n', encoding='utf-8')
        env_dump = self.root / 'verify_env_dump.json'
        verify_py = (
            f'"{sys.executable}" -c "import json, os, pathlib; '
            f'pathlib.Path({str(env_dump)!r}).write_text(json.dumps({{'
            f'\'depth\': os.environ.get(\'CAE_HARNESS_DEPTH\'), '
            f'\'git_dir\': os.environ.get(\'GIT_DIR\'), '
            f'\'agy_cid\': os.environ.get(\'AGY_CONVERSATION_ID\')}}), encoding=\'utf-8\')"'
        )
        spec_file = self.root / 'batch_polish_gate_spec.json'
        spec_file.write_text(json.dumps([
            {
                'stream_id': 'S1',
                'target': str(doc_file),
                'kind': 'polish',
                'engine': 'codex:gpt-5.4',
                'subtask': '润色说明文档',
            },
            {
                'stream_id': 'S2',
                'target': str(ws),
                'kind': 'gate',
                'engine': 'codex:gpt-5.4',
                'write_scope': ['solver.py'],
                'verify_cmd': verify_py,
            },
        ]), encoding='utf-8')
        codex_home = self.root / 'batch_codex_home'
        codex_home.mkdir()
        (codex_home / 'auth.json').write_text(json.dumps({'tokens': {'access_token': 'tok_valid_123456'}}), encoding='utf-8')
        (codex_home / 'config.toml').write_text('model = "gpt-5.4"\n', encoding='utf-8')
        (codex_home / 'models_cache.json').write_text(json.dumps({
            'models': [{'slug': 'gpt-5.4', 'supported_reasoning_levels': [{'effort': 'high'}, {'effort': 'medium'}]}]
        }), encoding='utf-8')
        fake_bin = self.root / 'fake_codex_bin'
        fake_bin.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        fake_bin.chmod(0o755)
        plan_dir = self.root / 'batch_plans'

        def fake_invoke(spec, root_arg, prompt, *pos_args, **kwargs):
            if '润色说明文档' in prompt:
                return {
                    'summary': 'polished',
                    'content': '# Solver Guide\n\nRun `solver.py --mesh grid.msh --invented-batch-flag`.\n',
                    'evidence': [],
                    'unverified': [],
                }
            return {'summary': 'ok', 'content': 'done', 'evidence': [], 'unverified': []}

        with patch.dict(os.environ, {
            'CODEX_HOME': str(codex_home),
            'CAE_CODEX_BINARY': str(fake_bin),
            'CAE_PLAN_STATE_DIR': str(plan_dir),
            'GIT_DIR': '/tmp/leaked-git-dir',
            'AGY_CONVERSATION_ID': 'leaked-parent-conv',
        }), patch.object(harness, 'invoke_engine', side_effect=fake_invoke):
            _, plan_out = self.capture(
                harness.cmd_plan,
                self.args('plan', '--spec-file', str(spec_file), '--caller', 'claude',
                          '--session-id', 'sess-bpg', '--plan-dir', str(plan_dir), '--save-ticket', '--full-json'),
            )
            tid = plan_out['ticket_id']
            self.capture(
                harness.cmd_plan,
                self.args('plan', '--approve-ticket', tid, '--caller', 'claude',
                          '--session-id', 'sess-bpg', '--plan-dir', str(plan_dir)),
            )
            rc_b, batch_out = self.capture(
                harness.cmd_batch,
                self.args('batch', '--spec-file', str(spec_file), '--caller', 'claude',
                          '--ticket', tid, '--session-id', 'sess-bpg', '--plan-dir', str(plan_dir)),
            )
        self.assertEqual(rc_b, 0)
        by_id = {s['stream_id']: s for s in batch_out['streams']}
        self.assertIn('grounding_audit', by_id['S1'])
        self.assertFalse(by_id['S1']['grounding_audit']['ok'])
        self.assertGreaterEqual(by_id['S1']['grounding_audit']['finding_count'], 1)
        self.assertTrue(by_id['S2']['verification_executed'])
        dumped = json.loads(env_dump.read_text(encoding='utf-8'))
        self.assertEqual(dumped, {'depth': '1', 'git_dir': None, 'agy_cid': None})

    def test_v118_process_streams_survive_timeout_cancellation_and_failure(self):
        child = ('import os,sys,time\n'
                 'print(os.environ["PAGER"], os.environ["GCM_INTERACTIVE"], os.environ["HWLOC_COMPONENTS"], flush=True)\n'
                 'print("diagnostic", file=sys.stderr, flush=True)\n')
        for status, tail in (('timeout', 'time.sleep(30)'), ('cancelled', 'time.sleep(30)'), ('failed', 'sys.exit(7)')):
            with self.subTest(status=status):
                timer = threading.Timer(0.8, harness._CANCELLED.set) if status == 'cancelled' else None
                if timer:
                    timer.start()
                try:
                    with self.assertRaises(harness.HarnessError) as caught:
                        harness.run_process([sys.executable, '-B', '-c', child + tail], self.root,
                                            input_text='x' * (1024 * 1024), timeout_sec=1.5)
                    self.assertEqual(caught.exception.status, status)
                    details = caught.exception.details
                    self.assertEqual(details['partial_stdout'].strip(), 'cat never -gl')
                    self.assertEqual(details['partial_stderr'].strip(), 'diagnostic')
                    if status == 'failed':
                        self.assertEqual(details['returncode'], 7)
                finally:
                    if timer:
                        timer.cancel()
                        timer.join()
                    harness._CANCELLED.clear()

    def test_v118_soft_timeout_wrapup_and_complete_schema_before_resume(self):
        events = '\n'.join((json.dumps({'type': 'thread.started', 'thread_id': 'session-soft'}),
                            json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 30, 'output_tokens': 10}})))
        for valid in (False, True):
            now = [0]
            receipt = {}

            def run(argv, cwd, prompt, timeout, **kwargs):
                output = Path(argv[argv.index('-o') + 1])
                if 'resume' not in argv:
                    self.assertEqual(timeout, 90)
                    self.assertNotIn('--ephemeral', argv)
                    output.write_text(json.dumps(TASK) if valid else json.dumps(TASK)[:-1], encoding='utf-8')
                    now[0] = 91
                    raise harness.HarnessError('deadline', 'timeout', process_started=True, partial_stdout=events)
                self.assertEqual(argv[argv.index('resume') + 1], 'session-soft')
                self.assertEqual(argv[argv.index('-s') + 1], 'read-only')
                self.assertIn('停止工具调用', prompt)
                self.assertEqual(timeout, 29)
                output.write_text(json.dumps(TASK), encoding='utf-8')
                usage = {'type': 'turn.completed', 'usage': {'input_tokens': 7, 'output_tokens': 3}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(usage), '')

            with self.subTest(valid=valid), patch.object(harness.time, 'monotonic', side_effect=lambda: now[0]), \
                 patch.object(harness, 'run_process', side_effect=run) as process:
                result = harness.invoke_engine('codex:default', self.root, '继续推导', backends(), harness.TASK_JSON_SCHEMA,
                                               readonly=False, timeout_sec=120, receipt=receipt)
            self.assertEqual(result, TASK)
            self.assertEqual(receipt['provider_session_id'], 'session-soft')
            self.assertEqual(process.call_count, 1 if valid else 2)
            self.assertEqual(receipt.get('soft_timeout_wrapup', False), not valid)
            self.assertEqual(receipt['token_usage']['total_tokens'], 40 if valid else 50)

    def test_v118_native_session_arguments_and_ephemeral_opt_out(self):
        providers = (
            ('agy', '--conversation', {'event': 'init', 'conversation_id': 'agy-s1'},
             {'event': 'result', 'result': {'structured_output': TASK}}),
            ('claude', '--resume', {'type': 'system', 'subtype': 'init', 'session_id': 'claude-s1'},
             {'type': 'result', 'structured_output': TASK}),
            ('dsh', '--session-id', {'type': 'session', 'sessionId': 'dsh-s1'},
             {'type': 'final', 'reason': {'kind': 'completed'}, 'text': json.dumps(TASK)}),
        )
        for provider, resume_flag, init, final in providers:
            for ephemeral in (True, False):
                with self.subTest(provider=provider, ephemeral=ephemeral):
                    receipt, now = {}, [0]

                    def run(argv, cwd, prompt, timeout, **kwargs):
                        if resume_flag in argv:
                            self.assertEqual(argv[argv.index(resume_flag) + 1], provider + '-s1')
                            if provider == 'claude':
                                self.assertIn('--tools', argv)
                            return subprocess.CompletedProcess(argv, 0, json.dumps(final), '')
                        if provider == 'claude':
                            self.assertEqual('--no-session-persistence' in argv, ephemeral)
                            self.assertEqual('--session-id' in argv, not ephemeral)
                        if provider == 'dsh':
                            patch = Path(argv[argv.index('--patch') + 1]).read_text(encoding='utf-8')
                            self.assertIn('compression: none', patch)
                        now[0] = 90
                        raise harness.HarnessError('deadline', 'timeout', process_started=True, partial_stdout=json.dumps(init))

                    with patch.object(harness.time, 'monotonic', side_effect=lambda: now[0]), \
                         patch.object(harness, 'run_process', side_effect=run) as process, \
                         patch.object(harness, '_prepare_execution'):
                        invoke = lambda: harness.invoke_engine(provider + ':default', self.root, '复核', self.four_backends(),
                                                               harness.TASK_JSON_SCHEMA, timeout_sec=120, receipt=receipt,
                                                               ephemeral=ephemeral)
                        if ephemeral:
                            with self.assertRaises(harness.HarnessError) as caught:
                                invoke()
                            self.assertEqual(caught.exception.status, 'timeout')
                            self.assertIsNone(receipt['provider_session_id'])
                            self.assertFalse(receipt['timeout_salvage']['resumable'])
                            self.assertEqual(process.call_count, 1)
                        elif provider == 'agy':
                            # agy 不再按 soft_timeout_ratio 倒扣主回合预算，也不自动同会话 wrapup；
                            # 但非 ephemeral 时仍持久化 provider_session_id 供非锁定场景显式 followup。
                            with self.assertRaises(harness.HarnessError) as caught:
                                invoke()
                            self.assertEqual(caught.exception.status, 'timeout')
                            self.assertEqual(receipt['provider_session_id'], 'agy-s1')
                            self.assertTrue(receipt['timeout_salvage']['resumable'])
                            self.assertEqual(process.call_count, 1)
                        else:
                            self.assertEqual(invoke(), TASK)
                            self.assertEqual(receipt['provider_session_id'], provider + '-s1')
                            self.assertEqual(process.call_count, 2)

    def test_v118_hard_timeout_archives_rollout_and_followup_restores_it(self):
        root = self.root / 'work'
        root.mkdir()
        source = root / 'flux.py'
        source.write_text('x = 1\n', encoding='utf-8')
        sessions = self.root / 'plans'
        receipt_file = self.root / 'receipt.json'
        sid = 'codex-fixture-s1'
        rollout = Path('sessions') / 'rollout-2026-11-21T09-00-00-' / f'rollout-{sid}.jsonl'
        failure = harness.HarnessError('deadline', 'timeout', process_started=True, partial_stderr='diagnostic')

        def run(argv, cwd, prompt, timeout, *, env, **kwargs):
            trace = Path(env['CODEX_HOME']) / rollout
            if 'resume' in argv:
                self.assertTrue(trace.is_file())
                self.assertEqual(argv[argv.index('resume') + 1], sid)
                self.assertEqual(cwd, root)
                self.assertEqual(argv[argv.index('-s') + 1], 'workspace-write')
                self.assertIn('核对局部守恒', prompt)
                Path(argv[argv.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
                usage = {'type': 'turn.completed', 'usage': {'input_tokens': 12, 'output_tokens': 4}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(usage), '')
            trace.parent.mkdir(parents=True)
            rows = [
                {'type': 'session_meta', 'payload': {'id': sid}},
                {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'cat flux.py', 'exit_code': 0}},
                {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {
                    'input_tokens': 110, 'cached_input_tokens': 20, 'output_tokens': 33}}}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [
                    {'type': 'output_text', 'text': '已有中间结论'}]}},
            ]
            trace.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
            source.write_text('x = 2\n', encoding='utf-8')
            raise failure

        args = self.args('task', '--target', str(root), '--kind', 'code', '--engine', 'codex:default',
                         '--task', '修改通量', '--write-scope', 'flux.py', '--isolation', 'no_skills',
                         '--timeout', '30', '--plan-dir', str(sessions), '--output', str(receipt_file))
        with patch.object(harness, '_args_backends', side_effect=lambda *a, **k: backends()), \
             patch.object(harness, 'run_process', side_effect=run):
            rc, report = self.capture(harness.cmd_task, args)
            self.assertEqual(rc, 124)
            self.assertEqual(report['token_usage']['usage_source'], 'cli_partial_on_timeout')
            self.assertEqual(report['token_usage_summary']['total_tokens'], 143)
            salvage = report['timeout_salvage']
            self.assertEqual(salvage, failure.details['timeout_salvage'])
            self.assertEqual(salvage['changed_files_on_disk'], ['flux.py'])
            self.assertEqual(salvage['actual_files_read'], ['flux.py'])
            self.assertEqual(salvage['provider_session_id'], sid)
            self.assertEqual(salvage['last_messages'], ['已有中间结论'])
            self.assertTrue(salvage['resumable'])
            store = sessions / 'headless_sessions' / 'codex' / report['invocation']['session_store_id']
            self.assertTrue((store / rollout).is_file())
            rc, followup = self.capture(harness.cmd_followup, self.args(
                'followup', '--receipt', str(receipt_file), '--task', '核对局部守恒', '--timeout', '30'))
        self.assertEqual(rc, 0)
        self.assertEqual(followup['provider_session_id'], sid)
        self.assertEqual(followup['token_usage_summary']['total_tokens'], 16)
        self.assertEqual(followup['changed_files'], [])

    def test_v118_followup_selects_stream_and_checks_readonly_snapshot(self):
        file = self.root / 'model.py'
        file.write_text('x = 1\n', encoding='utf-8')
        receipt = self.root / 'batch.json'
        inv = {'target_dir': str(self.root), 'engine': 'codex:default', 'readonly': True}
        receipt.write_text(json.dumps({'streams': [
            {'stream_id': 'first', 'invocation': {**inv, 'provider_session_id': 'first-id'}},
            {'stream_id': 'second', 'invocation': {**inv, 'provider_session_id': 'second-id'}},
        ]}), encoding='utf-8')
        question = self.root / 'question.txt'
        question.write_text('核对残差定义', encoding='utf-8-sig')

        def run(argv, cwd, prompt, timeout, **kwargs):
            self.assertEqual(argv[argv.index('resume') + 1], 'second-id')
            self.assertEqual(argv[argv.index('-s') + 1], 'read-only')
            self.assertIn('核对残差定义', prompt)
            file.write_text('x = 2\n', encoding='utf-8')
            Path(argv[argv.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'run_process', side_effect=run):
            rc, report = self.capture(harness.cmd_followup, self.args(
                'followup', '--receipt', str(receipt), '--stream', 'second', '--task-file', str(question)))
        self.assertEqual(rc, 1)
        self.assertEqual(report['status'], 'scope_violation')
        self.assertEqual(report['scope_violations'], ['model.py'])

    def test_v118_exact_input_envelope_and_citation_audit(self):
        for relative, content in (('src/flux.py', 'x=1\nx=2\n'), ('notes/setup.md', 'notes\n'), ('reserved.py', 'outside')):
            file = self.root / relative
            file.parent.mkdir(exist_ok=True)
            file.write_text(content, encoding='utf-8')
        envelope = ('--files', 'src/flux.py', '--files', 'notes/setup.md', '--negative-scope-file', 'reserved.py')
        args = self.args('plan', '--target', str(self.root), '--engine', 'codex:default', *envelope)
        streams, _ = harness._resolve_plan_streams(args, [{'env_contract': '只用指定网格'}], backends())
        self.assertEqual(streams[0]['files'], ['src/flux.py', 'notes/setup.md'])
        self.assertEqual(streams[0]['file_count'], 2)
        self.assertEqual(streams[0]['negative_scope_files'], ['reserved.py'])
        self.assertEqual(streams[0]['env_contract'], '只用指定网格')
        with self.assertRaises(harness.HarnessError):
            harness._resolve_plan_streams(args, [{'files': ['../outside.py']}], backends())
        with self.assertRaises(harness.HarnessError):
            harness._literal_input_path('/outside.py')

        def run(argv, cwd, prompt, timeout, **kwargs):
            staged = {p.relative_to(cwd).as_posix() for p in cwd.rglob('*') if p.is_file()}
            self.assertEqual(staged, {'src/flux.py', 'notes/setup.md'})
            self.assertIn('禁止修改：reserved.py', prompt)
            self.assertNotIn('=== FILE:', prompt)
            evidence = ['src/flux.py#L1-L2', 'notes/setup.md:1-99']
            Path(argv[argv.index('-o') + 1]).write_text(
                json.dumps({**TASK, 'content': '正文引用 ghost.py#L3-L4', 'evidence': evidence}), encoding='utf-8')
            read = {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'cat src/flux.py', 'exit_code': 0}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(read), '')

        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'run_process', side_effect=run):
            rc, report = self.capture(harness.cmd_task, self.args(
                'task', '--target', str(self.root), '--kind', 'math', '--engine', 'codex:default',
                '--task', '核对行号', '--clean-room', '--no-inline-readonly', *envelope))
        self.assertEqual(rc, 0)
        citation = report['citation_audit']
        self.assertEqual(citation['actual_files_read'], ['src/flux.py'])
        self.assertEqual(citation['missing_files'], ['ghost.py'])
        self.assertEqual(citation['unopened_cited_files'], ['ghost.py', 'notes/setup.md'])
        self.assertEqual(citation['out_of_range_lines'], [
            {'file': 'notes/setup.md', 'line_start': 1, 'line_end': 99, 'line_count': 1}])

    def test_v118_batch_wave_barrier_and_disjoint_scopes(self):
        root = self.root / 'work'
        root.mkdir()
        for name in ('a.py', 'b.py'):
            (root / name).write_text('old\n', encoding='utf-8')
        fixture = self.root / 'batch.json'
        base = {'target': str(root), 'engine': 'codex:default', 'kind': 'code'}
        fixture.write_text(json.dumps([
            {**base, 'stream_id': 'blocked', 'wave': 2, 'write_scope': ['a.py'], 'subtask': 'Blocked'},
            {**base, 'stream_id': 'a', 'write_scope': ['a.py'], 'files': ['a.py', 'b.py'],
             'negative_scope_files': ['reserved.py'], 'env_contract': '使用离线夹具', 'subtask': 'Alpha'},
            {**base, 'stream_id': 'b', 'write_scope': ['b.py'], 'subtask': 'Beta'},
            {**base, 'stream_id': 'read_blocked', 'kind': 'review', 'wave': 2, 'subtask': 'BlockedObserve'},
            {**base, 'stream_id': 'read_opt_in', 'kind': 'review', 'wave': 2, 'run_on_upstream_failure': True, 'subtask': 'Observe'},
        ]), encoding='utf-8')
        barrier = threading.Barrier(2, timeout=5)

        def run(spec, cwd, prompt, *args, **kwargs):
            self.assertNotIn('BlockedObserve', prompt)
            if 'Observe' in prompt:
                self.assertEqual((root / 'a.py').read_text(encoding='utf-8'), 'new\n')
                return TASK
            self.assertNotIn('Blocked', prompt)
            name = 'a.py' if 'Alpha' in prompt else 'b.py'
            if name == 'a.py':
                self.assertEqual(kwargs['stage_files'], ['a.py', 'b.py'])
                self.assertIn('环境约定：使用离线夹具', prompt)
                self.assertIn('禁止修改：reserved.py', prompt)
            barrier.wait()
            (cwd / name).write_text('new\n', encoding='utf-8')
            barrier.wait()
            if name == 'b.py':
                raise harness.HarnessError('fixture failed')
            return TASK

        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'invoke_engine', side_effect=run) as engine:
            rc, report = self.capture(harness.cmd_batch, self.args('batch', '--spec-file', str(fixture)))
        self.assertEqual(rc, 3)
        self.assertEqual(engine.call_count, 3)
        self.assertEqual([s['status'] for s in report['streams']],
                         ['upstream_wave_failed', 'ok', 'failed', 'upstream_wave_failed', 'ok'])
        self.assertEqual(report['streams'][1]['changed_files'], ['a.py'])
        self.assertEqual(report['streams'][2]['changed_files'], ['b.py'])

    def test_v118_wsl_interop_plan_rejects_writes_before_launch(self):
        engines = backends()
        engines['codex'].update(windows_interop=True)
        for kind in ('code', 'gate', 'review'):
            args = self.args('plan', '--kind', kind, '--target', str(self.root), '--engine', 'codex:default')
            with self.subTest(kind=kind), patch.object(harness, '_wsl_path', return_value=r'\\wsl.localhost\Ubuntu\work'):
                if kind == 'review':
                    streams, _ = harness._resolve_plan_streams(args, [{}], engines)
                    self.assertTrue(streams[0]['currently_available'])
                else:
                    with self.assertRaises(harness.HarnessError) as caught:
                        harness._resolve_plan_streams(args, [{}], engines)
                    self.assertEqual(caught.exception.status, 'wsl_interop_write_unsupported')

    def test_v118_grounding_samples_large_anchors_and_prunes_archives(self):
        with (self.root / 'results.json').open('wb') as data:
            data.write(b'{"first_anchor": 1, ')
            data.seek(1024 ** 2)
            data.write(b'"unseen_middle": 5, ')
            data.seek(3 * 1024 ** 2)
            data.write(b'"last_anchor": 9}')
        for directory in ('archive', 'archive_2025', 'outdated_old', '.micromamba', 'conda-meta'):
            site = self.root / directory
            site.mkdir()
            (site / 'input.py').write_text('archived_value = 1\n', encoding='utf-8')
        anchor = harness._build_grounding_anchor(self.root, set())
        self.assertTrue({'first_anchor', 'last_anchor'} <= anchor['words'])
        self.assertTrue({'archived_value', 'unseen_middle'}.isdisjoint(anchor['words']))
        self.assertNotIn('input.py', anchor['repo_names'])
        selected = harness._build_grounding_anchor(self.root, set(), target=self.root / 'archive_2025' / 'input.py')
        self.assertIn('archived_value', selected['words'])
        self.assertIn('archive_2025/input.py', selected['repo_names'])
        self.assertNotIn('archive/input.py', selected['repo_names'])

    def test_v118_hardening_cleanup_errors_archive_and_unclaimed_peer_writes(self):
        # 1. cleanup_errors blocks soft-timeout wrapup
        calls = 0

        def dirty_timeout(argv, cwd, prompt, timeout, **kwargs):
            nonlocal calls
            calls += 1
            raise harness.HarnessError(
                'timeout', 'timeout',
                partial_stdout=json.dumps({'type': 'thread.started', 'thread_id': 'codex-fixture-s2'}),
                partial_stderr='', cleanup_errors=['tree_leftover'],
            )

        receipt = {}
        with patch.object(harness, 'run_process', side_effect=dirty_timeout):
            with self.assertRaises(harness.HarnessError) as caught:
                harness.invoke_engine('codex:default', self.root, 'check', backends(), harness.TASK_JSON_SCHEMA, True, 180, receipt=receipt)
        self.assertEqual(calls, 1)
        self.assertEqual(caught.exception.details.get('cleanup_errors'), ['tree_leftover'])

        # 2. Unclaimed peer-scope violation is restored at wave barrier & WSL /mnt/ casefold
        work = self.root / 'peer_violation'
        work.mkdir()
        for name in ('a.py', 'b.py'):
            (work / name).write_text('old\n', encoding='utf-8')
        self.assertTrue(harness._scope_matches('Src/Flux.py', ['src/flux.py'], casefold=True))
        spec = self.root / 'peer_spec.json'
        base = {'target': str(work), 'engine': 'codex:default', 'kind': 'code'}
        spec.write_text(json.dumps([
            {**base, 'stream_id': 'a', 'write_scope': ['a.py'], 'subtask': 'Sneaky'},
            {**base, 'stream_id': 'b', 'write_scope': ['b.py'], 'subtask': 'Idle'},
        ]), encoding='utf-8')
        sneaky_wrote = threading.Event()
        idle_entered = threading.Event()
        orig_snap = harness.snapshot_files

        def synced_snap(root_dir, *args, **kwargs):
            if threading.current_thread().name.endswith('_1'):
                sneaky_wrote.wait(5)
                snap = orig_snap(root_dir, *args, **kwargs)
                idle_entered.set()
                return snap
            return orig_snap(root_dir, *args, **kwargs)

        def run_sneaky(engine_spec, cwd, prompt, *args, **kwargs):
            if 'Sneaky' in prompt:
                (cwd / 'a.py').write_text('new\n', encoding='utf-8')
                (cwd / 'b.py').write_text('tampered\n', encoding='utf-8')
                sneaky_wrote.set()
                idle_entered.wait(5)
            return TASK

        with (
            patch.object(harness, '_args_backends', return_value=backends()),
            patch.object(harness, 'snapshot_files', side_effect=synced_snap),
            patch.object(harness, 'invoke_engine', side_effect=run_sneaky),
        ):
            rc, report = self.capture(harness.cmd_batch, self.args('batch', '--spec-file', str(spec)))
        self.assertEqual(rc, 3)
        self.assertEqual(report['streams'][0]['status'], 'scope_violation')
        self.assertIn('b.py', report['streams'][0]['scope_violations'])

        # 3. followup restores <TEMP> and accepts left-S1 alias
        audit_receipt = self.root / 'audit_receipt.json'
        temp_proj = Path(tempfile.gettempdir()) / f'followup_proj_{os.getpid()}'
        temp_proj.mkdir(exist_ok=True)
        try:
            audit_receipt.write_text(json.dumps({
                'completed_units': [{
                    'side': 'left', 'shard_id': 1, 'readonly': True, 'engine': 'codex:default',
                    'target_dir': f'<TEMP>/{temp_proj.name}',
                    'invocation': {'provider_session_id': 'codex-fixture-left1'},
                }],
            }), encoding='utf-8')
            rc_dry, dry_rep = self.capture(harness.cmd_followup, self.args(
                'followup', '--receipt', str(audit_receipt), '--stream', 'left-S1', '--dry-run', '--task', 'ping'))
            self.assertEqual(rc_dry, 0)
            self.assertTrue(dry_rep['dry_run'])
        finally:
            if temp_proj.exists():
                temp_proj.rmdir()

    def test_v1182_short_alias_stage_citation_backtick_followup_plan_dir_and_explicit_files_light(self):
        # 1. _session_trace & _citation_audit handle non-canonical/8.3 base_dir spelling and inline backticks in prose
        real_stage = self.root / 'runneradmin_temp' / 'staged'
        (real_stage / 'src').mkdir(parents=True)
        (real_stage / 'src' / 'flux.py').write_text('a = 1\nb = 2\n', encoding='utf-8')
        orig_resolve = Path.resolve

        def expand_short_runner(p_self, *a, **kw):
            s = str(p_self)
            if 'RUNNER~1' in s:
                return orig_resolve(Path(s.replace('RUNNER~1', 'runneradmin_temp')), *a, **kw)
            return orig_resolve(p_self, *a, **kw)

        short_stage = self.root / 'RUNNER~1' / 'staged'
        with patch.object(Path, 'resolve', autospec=True, side_effect=expand_short_runner):
            reads, _, _ = harness._session_trace(
                [{'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'cat src/flux.py', 'exit_code': 0}}],
                self.root,
                short_stage,
            )
            prose_result = {
                'content': 'WSL 查询解码见 `cae_harness.py` 第 10 行。参考 [flux.py](src/flux.py:1-2)。',
                'evidence': ['回执见 `src/flux.py#L1-L2` 与 `src/flux.py`:2。'],
            }
            audit = harness._citation_audit(prose_result, real_stage, reads, [], stage_dir=short_stage)
        self.assertEqual(reads, ['src/flux.py'])
        self.assertEqual(audit['missing_files'], [])
        self.assertEqual(audit['out_of_range_lines'], [])
        self.assertEqual(audit['unopened_cited_files'], [])

        # 2. cmd_followup without session_plan_dir does not default plan_dir to '.' inside workspace
        proj = self.root / 'followup_clean_ws'
        proj.mkdir()
        (proj / 'solver.py').write_text('u = 0\n', encoding='utf-8')
        rec_file = self.root / 'no_plan_dir_receipt.json'
        rec_file.write_text(json.dumps({
            'target': str(proj), 'engine': 'codex:default', 'readonly': True,
            'invocation': {'provider_session_id': 'sid-clean-1', 'session_store_id': 'abcd1234'},
        }), encoding='utf-8')
        seen_plan_dirs = []

        def fake_invoke(engine, root, prompt, backends_map, schema, readonly, timeout, *a, **kw):
            seen_plan_dirs.append(kw.get('plan_dir'))
            return TASK

        with patch.object(harness, '_args_backends', return_value=backends()), patch.object(harness, 'invoke_engine', side_effect=fake_invoke):
            rc_fu, rep_fu = self.capture(harness.cmd_followup, self.args(
                'followup', '--receipt', str(rec_file), '--task', '核对守恒律'))
        self.assertEqual(rc_fu, 0)
        self.assertEqual(seen_plan_dirs, [''])
        self.assertEqual(rep_fu['scope_violations'], [])

        # 3. Explicit --files up to 4 small files is treated as light single-stream task
        multi = self.root / 'multi_repo'
        multi.mkdir()
        for idx in range(6):
            (multi / f'f{idx}.py').write_text('x = 1\n', encoding='utf-8')
        self.assertFalse(harness._is_heavy_streams([{'kind': 'review', 'file_count': 4, 'estimated_tokens': 200, 'timeout': 300, 'files': ['f0.py', 'f1.py', 'f2.py', 'f3.py']}]))
        self.assertTrue(harness._is_heavy_streams([{'kind': 'review', 'file_count': 5, 'estimated_tokens': 200, 'timeout': 300, 'files': ['f0.py', 'f1.py', 'f2.py', 'f3.py', 'f4.py']}]))
        self.assertTrue(harness._is_heavy_streams([{'kind': 'review', 'file_count': 3, 'estimated_tokens': 200, 'timeout': 300}]))

    def test_v1183_chat_brief_and_winapps_interop_codex_inline_readonly_guard(self):
        target_file = self.root / 'doc.md'
        target_file.write_text('# manual\n', encoding='utf-8')
        plans = self.root / 'v1183-plans'
        engines = self.four_backends()

        # 1. Persisted plan produces 3-line chat_brief without Markdown tables while preserving card_detail_file & compact_summary_card
        with patch.object(harness, '_args_backends', return_value=engines):
            _, view = self.capture(harness.cmd_plan, self.args(
                'plan', '--caller', 'antigravity', '--engine', 'codex:fixture-code', '--kind', 'review',
                '--target', str(target_file), '--plan-dir', str(plans), '--session-id', 'v1183-s1', '--save-ticket',
                '--workflow-bg', '背景', '--workflow-goal', '模拟读者复核交付文档', '--workflow-impact', '严格只读'))
        brief = view.get('chat_brief', '')
        self.assertIn('【待确认派工 · 严格只读】', brief)
        self.assertIn(f"[{view['ticket_id']}_card.md](file://", brief)
        self.assertIn('回复 **「批准」** 启动', brief)
        self.assertNotIn('| 流号 |', brief)
        self.assertEqual(len(brief.strip().splitlines()), 3)
        self.assertIn('| 流号 | 通道与执行端 | 模型与挡位 | 可用性与额度状态 |', view['compact_summary_card'])

        # 2. WindowsApps / WSL interop codex.exe rejects --no-inline-readonly in plan & invoke_engine, allows resume, and auto-routes away
        for variant_info in (
            {**engines['codex'], 'windows_interop': True, 'binary': '/mnt/c/Codex/codex.exe'},
            {**engines['codex'], 'windows_interop': False, 'binary': r'C:\Program Files\WindowsApps\OpenAI.Codex_26.1117\app\resources\codex.exe'},
        ):
            scoped_engines = {**engines, 'codex': variant_info}
            explicit_args = self.args(
                'plan', '--kind', 'review', '--target', str(target_file),
                '--engine', 'codex:fixture-code', '--no-inline-readonly',
            )
            with self.assertRaises(harness.HarnessError) as ctx:
                harness._resolve_plan_streams(explicit_args, [{}], scoped_engines)
            self.assertEqual(ctx.exception.status, 'inline_readonly_required')
            with self.assertRaises(harness.HarnessError) as ctx_inv:
                harness._check_readonly_inline_transport('codex', variant_info, True, False, is_resume=False)
            self.assertEqual(ctx_inv.exception.status, 'inline_readonly_required')
            # Followup resume with inline_readonly=False remains allowed
            harness._check_readonly_inline_transport('codex', variant_info, True, False, is_resume=True)
            # Auto-route role:review with --no-inline-readonly skips WindowsApps/interop codex and routes to another available backend
            auto_args = self.args('plan', '--kind', 'review', '--target', str(target_file), '--engine', 'role:review', '--no-inline-readonly')
            resolved_auto, _ = harness._resolve_plan_streams(auto_args, [{}], scoped_engines)
            self.assertNotEqual(resolved_auto[0]['client'], 'codex')

    def test_v1188_wrapup_downgrade_trace_choose_engine_sandbox_hint_no_op_and_julia_ignore(self):
        # 1. Soft-timeout wrapup downgrades effort safely to lowest supported level ('low' when supported, else lowest declared)
        #    and reports true total timeout_sec + primary_timeout_sec + wrapup_timeout_sec on double timeout
        events = json.dumps({'type': 'thread.started', 'thread_id': 'session-wrap-low'})
        for supported_levels, expected_wrapup in (
            (['low', 'medium', 'high', 'max'], 'low'),
            (['high', 'max'], 'high'),
        ):
            now = [0]
            receipt = {}
            seen_efforts = []
            b_case = backends()
            for item in b_case['codex']['models']:
                item['supported_efforts'] = list(supported_levels)

            def run_double_timeout(argv, cwd, prompt, timeout, **kwargs):
                eff_args = [argv[i + 1] for i in range(len(argv) - 1) if argv[i] == '-c' and 'model_reasoning_effort=' in argv[i + 1]]
                seen_efforts.append(('resume' in argv, eff_args))
                if 'resume' not in argv:
                    now[0] = 91
                    raise harness.HarnessError('子进程超时', 'timeout', timeout_sec=timeout, process_started=True, partial_stdout=events)
                now[0] = 121
                raise harness.HarnessError('子进程超时', 'timeout', timeout_sec=timeout, process_started=True, partial_stdout='')

            with self.subTest(supported_levels=supported_levels), \
                 patch.object(harness.time, 'monotonic', side_effect=lambda: now[0]), \
                 patch.object(harness, 'run_process', side_effect=run_double_timeout):
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.invoke_engine('codex:default', self.root, '推导', b_case, harness.TASK_JSON_SCHEMA,
                                          readonly=False, timeout_sec=120, effort='max', receipt=receipt)
            self.assertEqual(seen_efforts[0], (False, ['model_reasoning_effort="max"']))
            self.assertEqual(seen_efforts[1], (True, [f'model_reasoning_effort="{expected_wrapup}"']))
            self.assertEqual(receipt.get('wrapup_effort'), expected_wrapup)
            self.assertEqual(caught.exception.details.get('timeout_sec'), 120)
            self.assertEqual(caught.exception.details.get('primary_timeout_sec'), 90)
            self.assertEqual(caught.exception.details.get('wrapup_timeout_sec'), 29)
            self.assertEqual(caught.exception.details.get('wrapup_status'), 'timeout')

        # Claude & DSH also downgrade to low on wrapup
        for provider, resume_flag, init, final in (
            ('claude', '--resume', {'type': 'system', 'subtype': 'init', 'session_id': 'claude-w1'},
             {'type': 'result', 'structured_output': TASK}),
            ('dsh', '--session-id', {'type': 'session', 'sessionId': 'dsh-w1'},
             {'type': 'final', 'reason': {'kind': 'completed'}, 'text': json.dumps(TASK)}),
        ):
            rec_p, clk = {}, [0]

            def run_p(argv, cwd, prompt, timeout, **kwargs):
                if resume_flag in argv:
                    if provider == 'claude':
                        self.assertEqual(argv[argv.index('--effort') + 1], 'low')
                    else:
                        patch_txt = Path(argv[argv.index('--patch') + 1]).read_text(encoding='utf-8')
                        self.assertIn('reasoningEffort: low', patch_txt)
                    return subprocess.CompletedProcess(argv, 0, json.dumps(final), '')
                clk[0] = 90
                raise harness.HarnessError('deadline', 'timeout', timeout_sec=timeout, process_started=True, partial_stdout=json.dumps(init))

            with self.subTest(provider=provider), patch.object(harness.time, 'monotonic', side_effect=lambda: clk[0]), \
                 patch.object(harness, 'run_process', side_effect=run_p), patch.object(harness, '_prepare_execution'):
                out = harness.invoke_engine(provider + ':default', self.root, '复核', self.four_backends(),
                                            harness.TASK_JSON_SCHEMA, timeout_sec=120, receipt=rec_p)
            self.assertEqual(out, TASK)
            self.assertEqual(rec_p.get('wrapup_effort'), 'low')

        # 2. _session_trace preserves assistant agent_message ahead of reasoning headers & parses PowerShell read commands accurately
        for rel in ('src/flux.jl', 'notes/spec.md', 'scripts/check_all.jl'):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('ok\n', encoding='utf-8')
        trace_events = [
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 0,
             'command': '''"powershell.exe" -Command '$lines = Get-Content -LiteralPath \'"\'src/flux.jl\' -Encoding utf8' '''}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 0,
             'command': '''"powershell.exe" -Command '$a = @(Get-Content -Encoding UTF8 -LiteralPath \'"\'notes/spec.md\')' '''}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 0,
             'command': '''"powershell.exe" -Command "Select-String -LiteralPath 'scripts/check_all.jl' -Pattern 'foo'; julia -h | Select-String -Pattern 'bar' -Context 0,2"'''}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '已定位核心计数缺陷：CPU 磁盘路径峰值误计。'}},
            {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': '**Checking A**'}},
            {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': '**Checking B**'}},
            {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': '**Checking C**'}},
        ]
        reads, msgs, _ = harness._session_trace(trace_events, self.root, None)
        self.assertEqual(reads, ['notes/spec.md', 'scripts/check_all.jl', 'src/flux.jl'])
        self.assertEqual(msgs, ['已定位核心计数缺陷：CPU 磁盘路径峰值误计。'])
        # Fallback to reasoning when no assistant message exists
        _, only_reason, _ = harness._session_trace(trace_events[-3:], self.root, None)
        self.assertEqual(only_reason, ['**Checking A**', '**Checking B**', '**Checking C**'])

        # 3. choose_engine keeps explicit same-backend model when user_prompt only names backend generically
        engines = self.four_backends()
        engines['codex']['models'] = [
            {'slug': 'gpt-6.2-sol', 'supported_efforts': ['low', 'high', 'max'], 'context_window': 872000},
            {'slug': 'gpt-6.2-astra', 'supported_efforts': ['low', 'high', 'max'], 'context_window': 872000},
        ]
        self.assertEqual(
            harness.choose_engine(engines, 'antigravity', 'codex:gpt-6.2-astra',
                                  user_prompt='优先并发调用 Codex max 旗舰模型（gpt-6.2-astra、gpt-6.2-sol）'),
            'codex:gpt-6.2-astra',
        )
        # Cross-backend P0 hint still overrides
        self.assertEqual(
            harness.choose_engine(engines, 'antigravity', 'codex:gpt-6.2-astra', user_prompt='请用 CC 审查'),
            'claude:default',
        )
        # Explicit single model P0 hint still overrides
        self.assertEqual(
            harness.choose_engine(engines, 'antigravity', 'codex:gpt-6.2-astra', user_prompt='请用 gpt-6.2-sol 审查'),
            'codex:gpt-6.2-sol',
        )

        # 4. WindowsApp codex injects concise sandbox boundary hint (writable & readonly), skipped in clean_room
        win_engines = {**engines, 'codex': {
            **engines['codex'],
            'binary': r'C:\Program Files\WindowsApps\OpenAI.Codex_26.1117\app\resources\codex.exe',
        }}
        prompts_seen = []

        def capture_prompt(argv, cwd, prompt, timeout, **kwargs):
            prompts_seen.append(prompt)
            Path(argv[argv.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(argv, 0, '', '')

        with patch.object(harness, 'run_process', side_effect=capture_prompt):
            harness.invoke_engine('codex:gpt-6.2-sol', self.root, '编码任务', win_engines, harness.TASK_JSON_SCHEMA,
                                  readonly=False, timeout_sec=60)
            harness.invoke_engine('codex:gpt-6.2-sol', self.root, '审查任务', win_engines, harness.TASK_JSON_SCHEMA,
                                  readonly=True, timeout_sec=60, stage_files=['src/flux.jl'], inline_readonly=True)
            harness.invoke_engine('codex:gpt-6.2-sol', self.root, '净室任务', win_engines, harness.TASK_JSON_SCHEMA,
                                  readonly=True, timeout_sec=60, stage_files=['src/flux.jl'], clean_room=True, inline_readonly=True)
        self.assertIn('[Windows Codex 沙箱边界]', prompts_seen[0])
        self.assertIn('[Windows Codex 只读沙箱边界]', prompts_seen[1])
        self.assertNotIn('[Windows Codex', prompts_seen[2])

        # 5. Writable code task with 0 changed files sets no_files_changed=True, and .julia cache is ignored by snapshot
        work = self.root / 'julia_work'
        work.mkdir()
        (work / 'solver.jl').write_text('x = 1\n', encoding='utf-8')

        def noop_with_julia_cache(*args, **kwargs):
            cache_file = work / '.julia' / 'demo-pkg-tmp' / 'compiled.ji'
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text('cache\n', encoding='utf-8')
            return TASK

        with patch.object(harness, '_args_backends', return_value=backends()), \
             patch.object(harness, 'invoke_engine', side_effect=noop_with_julia_cache):
            rc_t, rep_t = self.capture(harness.cmd_task, self.args(
                'task', '--target', str(work), '--kind', 'code', '--engine', 'codex:default',
                '--write-scope', 'solver.jl', '--task', '检查门禁后编码'))
        self.assertEqual(rc_t, 0)
        self.assertEqual(rep_t['status'], 'ok')
        self.assertEqual(rep_t['scope_violations'], [])
        self.assertTrue(rep_t.get('no_files_changed'))
        # Explicit .julia write_scope un-ignores .julia so scoped edits remain tracked
        self.assertNotIn('.julia', harness._snapshot_ignore_dirs_for(['.julia/config.toml']))

    def test_v11810_agy_wrapup_downgrade_grace_stalled_guard_and_plan_budget_gates(self):
        # 1. agy 不再被 soft_timeout_ratio 倒扣 15% 主预算：timeout=600 完整传入 --print-timeout 600s，
        #    且外层 proc_timeout 仍严格大于 --print-timeout（600 + grace=15 -> 615s）
        agy_engines = self.four_backends()
        agy_engines['agy']['models'] = [
            {'slug': 'gemini-4-carbon-high', 'context_window': 512000, 'priority': 1},
            {'slug': 'gemini-4-carbon-medium', 'context_window': 512000, 'priority': 2},
            {'slug': 'gemini-4-carbon-low', 'context_window': 512000, 'priority': 3},
        ]
        agy_engines['agy']['model'] = 'gemini-4-carbon-high'
        now = [0.0]
        seen_calls = []
        receipt = {}

        def run_agy_full_budget(argv, cwd, prompt, timeout, **kwargs):
            seen_calls.append({
                'argv': list(argv),
                'print_timeout': argv[argv.index('--print-timeout') + 1],
                'model': argv[argv.index('--model') + 1],
                'has_effort': '--effort' in argv,
                'proc_timeout': timeout,
                'is_resume': '--conversation' in argv,
            })
            now[0] = 526.4
            final = json.dumps({'event': 'result', 'result': {'structured_output': TASK}})
            return subprocess.CompletedProcess(argv, 0, final, '')

        with patch.object(harness.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(harness, 'run_process', side_effect=run_agy_full_budget):
            out = harness.invoke_engine(
                'agy:gemini-4-carbon-high', self.root, '复核双语文档', agy_engines,
                harness.TASK_JSON_SCHEMA, readonly=True, timeout_sec=600, receipt=receipt,
            )
        self.assertEqual(out, TASK)
        self.assertEqual(len(seen_calls), 1)
        self.assertEqual(seen_calls[0]['model'], 'gemini-4-carbon-high')
        self.assertFalse(seen_calls[0]['has_effort'])
        self.assertEqual(seen_calls[0]['print_timeout'], '600s')
        self.assertGreater(seen_calls[0]['proc_timeout'], 600)
        self.assertEqual(seen_calls[0]['proc_timeout'], 615)
        self.assertFalse(receipt.get('soft_timeout_wrapup', False))

        # 2. agy 触发 --print-timeout with turn in progress 时标记 wrapup_stalled=True & resumable=False，
        #    阻断 cmd_followup 返回 session_not_resumable；Codex 双超时保持 resumable=True
        stall_now = [0.0]
        stall_receipt = {}

        def run_agy_bg_timeout(argv, cwd, prompt, timeout, **kwargs):
            stall_now[0] = 600.0
            out1 = '\n'.join((
                json.dumps({'event': 'init', 'conversation_id': 'agy-stall-s1'}),
                json.dumps({'event': 'result', 'result': {'finished_reason': 'print_timeout', 'response': ''}}),
            ))
            return subprocess.CompletedProcess(
                argv, 0, out1 + '\n',
                '[agy] print timeout after 10m0s with turn in progress; returning partial output\n'
                'AGY_ERROR: {"reason":"print_timeout"}\n',
            )

        with patch.object(harness.time, 'monotonic', side_effect=lambda: stall_now[0]), \
             patch.object(harness, 'run_process', side_effect=run_agy_bg_timeout):
            with self.assertRaises(harness.HarnessError) as stall_exc:
                harness.invoke_engine(
                    'agy:gemini-4-carbon-high', self.root, '复核', agy_engines,
                    harness.TASK_JSON_SCHEMA, readonly=True, timeout_sec=600, receipt=stall_receipt,
                )
        self.assertEqual(stall_exc.exception.status, 'timeout')
        self.assertTrue(stall_receipt.get('wrapup_stalled'))
        self.assertTrue(stall_exc.exception.details.get('wrapup_stalled'))
        self.assertFalse(stall_receipt['timeout_salvage']['resumable'])
        self.assertEqual(stall_receipt['timeout_salvage']['resume_blocked_reason'], 'agy_background_turn_in_progress')

        stall_receipt_path = self.root / 'agy_stalled_receipt.json'
        stall_receipt_path.write_text(json.dumps({
            'status': 'timeout', 'target': str(self.root), 'engine': 'agy:gemini-4-carbon-high',
            'readonly': True, 'timeout_salvage': stall_receipt['timeout_salvage'],
            'invocation': stall_receipt,
        }, ensure_ascii=False), encoding='utf-8')
        with patch.object(harness, 'run_process', side_effect=AssertionError('should not invoke cli on non-resumable receipt')):
            with self.assertRaises(harness.HarnessError) as f_exc:
                harness.cmd_followup(self.args(
                    'followup', '--receipt', str(stall_receipt_path), '--task', '继续收口', '--timeout', '120',
                ))
        self.assertEqual(f_exc.exception.status, 'session_not_resumable')
        self.assertEqual(f_exc.exception.details.get('resume_blocked_reason'), 'agy_background_turn_in_progress')

        # Codex double timeout with empty wrapup stdout still preserves resumable=True (R5 scope guard)
        cx_now, cx_rec = [0.0], {}
        cx_init = json.dumps({'type': 'thread.started', 'thread_id': 'codex-double-t1'})

        def run_cx_double(argv, cwd, prompt, timeout, **kwargs):
            if 'resume' not in argv:
                cx_now[0] = 91.0
                raise harness.HarnessError('timeout', 'timeout', timeout_sec=timeout, process_started=True, partial_stdout=cx_init)
            cx_now[0] = 121.0
            raise harness.HarnessError('timeout', 'timeout', timeout_sec=timeout, process_started=True, partial_stdout='')

        with patch.object(harness.time, 'monotonic', side_effect=lambda: cx_now[0]), \
             patch.object(harness, 'run_process', side_effect=run_cx_double):
            with self.assertRaises(harness.HarnessError):
                harness.invoke_engine('codex:default', self.root, '推导', backends(), harness.TASK_JSON_SCHEMA,
                                      readonly=True, timeout_sec=120, receipt=cx_rec)
        self.assertFalse(cx_rec.get('wrapup_stalled', False))
        self.assertTrue(cx_rec['timeout_salvage']['resumable'])

        # 3. plan over-window hard block, suggested_shards, auto_route fallback to larger window,
        #    --allow-over-budget override, directory non-blocking, and inline_readonly_summary telemetry
        big_dir = self.root / 'big_docs'
        big_dir.mkdir()
        big_files = []
        for idx in range(4):
            rel = f'part_{idx + 1}.md'
            # 4 * 420 KiB = 1.64 MiB (~573K tokens > 512K agy window, < 872K codex window)
            (big_dir / rel).write_bytes(b'a' * (420 * 1024))
            big_files.append(rel)

        cap_engines = self.four_backends()
        cap_engines['agy']['models'] = [
            {'slug': 'gemini-4-carbon-high', 'context_window': 512000, 'priority': 1},
            {'slug': 'gemini-4-carbon-low', 'context_window': 512000, 'priority': 2},
        ]
        cap_engines['agy']['model'] = 'gemini-4-carbon-high'
        cap_engines['codex']['models'] = [
            {'slug': 'gpt-6.2-astra', 'context_window': 872000, 'supported_efforts': ['low', 'high', 'max']},
        ]
        cap_engines['codex']['model'] = 'gpt-6.2-astra'

        plan_dir = self.root / 'budget_plans'
        file_flags = []
        for rel in big_files:
            file_flags.extend(['--files', rel])

        # Explicit agy:gemini-4-carbon-high over 512K window -> hard-blocked with over_context_window + suggested_shards
        with patch.object(harness, '_args_backends', return_value=cap_engines):
            with self.assertRaises(harness.HarnessError) as ow_exc:
                harness.cmd_plan(self.args(
                    'plan', '--target', str(big_dir), '--kind', 'review',
                    '--engine', 'agy:gemini-4-carbon-high', '--session-id', 'sess-ow',
                    '--plan-dir', str(plan_dir), *file_flags,
                ))
        self.assertEqual(ow_exc.exception.status, 'over_context_window')
        shards = ow_exc.exception.details.get('suggested_shards') or []
        self.assertGreaterEqual(len(shards), 4)
        self.assertEqual([f for sh in shards for f in sh['files']], big_files)

        # --allow-over-budget explicitly overrides hard block, surfaces warnings & shards in card & 3-line chat_brief
        with patch.object(harness, '_args_backends', return_value=cap_engines):
            rc_allow, plan_allow = self.capture(harness.cmd_plan, self.args(
                'plan', '--target', str(big_dir), '--kind', 'review',
                '--engine', 'agy:gemini-4-carbon-high', '--session-id', 'sess-ow',
                '--plan-dir', str(plan_dir), '--allow-over-budget', '--save-ticket', *file_flags,
            ))
        self.assertEqual(rc_allow, 0)
        st0 = plan_allow['streams'][0]
        self.assertTrue(st0['over_context_window'])
        self.assertTrue(st0['over_input_budget'])
        self.assertTrue(st0['exceeds_inline_cap'])
        self.assertEqual(len(st0['suggested_shards']), len(shards))
        self.assertEqual(len(plan_allow['chat_brief'].splitlines()), 3)
        self.assertIn('超安全预算或内联上限', plan_allow['chat_brief'])
        card_text = Path(plan_allow['card_detail_file']).read_text(encoding='utf-8')
        self.assertIn('容量与内联预警', card_text)
        self.assertIn('建议分片', card_text)

        # Directory target without explicit --files must NOT hard-block
        with patch.object(harness, '_args_backends', return_value=cap_engines):
            rc_dir, plan_dir_out = self.capture(harness.cmd_plan, self.args(
                'plan', '--target', str(big_dir), '--kind', 'review',
                '--engine', 'agy:gemini-4-carbon-high', '--session-id', 'sess-ow',
                '--plan-dir', str(plan_dir),
            ))
        self.assertEqual(rc_dir, 0)
        self.assertFalse(plan_dir_out['streams'][0]['over_context_window'])

        # Auto-route role:review when primary 512K agy cannot fit skips to larger-window codex
        with patch.object(harness, '_args_backends', return_value=cap_engines):
            rc_auto, plan_auto = self.capture(harness.cmd_plan, self.args(
                'plan', '--target', str(big_dir), '--kind', 'review', '--caller', 'antigravity',
                '--engine', 'role:review', '--session-id', 'sess-ow',
                '--plan-dir', str(plan_dir), *file_flags,
            ))
        self.assertEqual(rc_auto, 0)
        self.assertNotEqual(plan_auto['streams'][0]['client'], 'agy')
        self.assertTrue(plan_auto['streams'][0]['exceeds_inline_cap'])

        # inline_readonly_summary records exact inlined vs cap-omitted files on readonly execution
        # codex uses default 512 KiB cap (1 of 4 420 KiB files inlined), while agy uses 128 KiB cap (0 of 4 inlined)
        codex_inline_rec = {}

        def _fake_cx_inline(command, cwd, stdin_text, timeout, **kwargs):
            Path(command[command.index('-o') + 1]).write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        with patch.object(harness, 'run_process', side_effect=_fake_cx_inline):
            harness.invoke_engine(
                'codex:gpt-6.2-astra', big_dir, '审查', cap_engines, harness.TASK_JSON_SCHEMA,
                readonly=True, timeout_sec=60, stage_files=big_files, inline_readonly=True, receipt=codex_inline_rec,
            )
        cx_sum = codex_inline_rec.get('inline_readonly_summary') or {}
        self.assertEqual(cx_sum.get('inlined_count'), 1)
        self.assertEqual(cx_sum.get('omitted_count'), 3)
        self.assertEqual(cx_sum.get('cap_omitted_count'), 3)
        self.assertEqual(cx_sum.get('cap_bytes'), 512 * 1024)
        self.assertEqual(cx_sum.get('omitted_files'), big_files[1:])

        agy_inline_rec = {}
        with patch.object(harness, 'run_process', return_value=subprocess.CompletedProcess(
            ['agy'], 0, json.dumps({'event': 'result', 'result': {'structured_output': TASK}}), '',
        )):
            harness.invoke_engine(
                'agy:gemini-4-carbon-high', big_dir, '审查', cap_engines, harness.TASK_JSON_SCHEMA,
                readonly=True, timeout_sec=60, stage_files=big_files, inline_readonly=True, receipt=agy_inline_rec,
            )
        agy_sum = agy_inline_rec.get('inline_readonly_summary') or {}
        self.assertEqual(agy_sum.get('inlined_count'), 0)
        self.assertEqual(agy_sum.get('omitted_count'), 4)
        self.assertEqual(agy_sum.get('cap_omitted_count'), 4)
        self.assertEqual(agy_sum.get('cap_bytes'), 128 * 1024)
        self.assertEqual(agy_sum.get('omitted_files'), big_files)

        # 4. _terminate_tree sends SIGTERM before SIGKILL on POSIX
        class _FakeProc:
            def __init__(self, polls):
                self.pid = 42424
                self._polls = list(polls)
            def poll(self):
                return self._polls.pop(0) if len(self._polls) > 1 else self._polls[0]

        sent_signals = []

        def fake_killpg(pid, sig):
            sent_signals.append((pid, sig))
            if sig == 9:
                raise ProcessLookupError()

        with patch.object(harness.os, 'name', 'posix'), \
             patch.object(harness.os, 'killpg', side_effect=fake_killpg, create=True), \
             patch.object(harness.signal, 'SIGTERM', 15, create=True), \
             patch.object(harness.signal, 'SIGKILL', 9, create=True), \
             patch.object(harness.time, 'sleep'):
            errs = harness._terminate_tree(_FakeProc([None, None, 0]))
        self.assertEqual(errs, [])
        self.assertEqual(sent_signals, [(42424, 15), (42424, 9)])

    def test_v11811_root_cause_hardening_caps_winerr32_agy_dsh_snapshot_followup_citations(self):
        # 1. Per-backend inline cap (agy 128 KiB vs codex 512 KiB), omitted list truncation, and Codex 1M char guard
        cap_dir = self.root / 'v11811_cap'
        cap_dir.mkdir()
        (cap_dir / 'mid.md').write_bytes(b'm' * (180 * 1024))
        cap_engines = self.four_backends()
        cap_engines['agy']['models'] = [{'slug': 'gemini-4-carbon-high', 'context_window': 512000, 'priority': 1}]
        cap_engines['agy']['model'] = 'gemini-4-carbon-high'
        cap_engines['codex']['models'] = [{'slug': 'gpt-6.2-astra', 'context_window': 872000, 'supported_efforts': ['low', 'medium', 'high', 'max']}]
        cap_engines['codex']['model'] = 'gpt-6.2-astra'
        plan_dir = self.root / 'v11811_plans'
        with patch.object(harness, '_args_backends', return_value=cap_engines):
            rc_agy, p_agy = self.capture(harness.cmd_plan, self.args(
                'plan', '--target', str(cap_dir), '--kind', 'review', '--engine', 'agy:gemini-4-carbon-high',
                '--session-id', 'sess-v11811', '--plan-dir', str(plan_dir), '--save-ticket', '--files', 'mid.md',
            ))
            rc_cx, p_cx = self.capture(harness.cmd_plan, self.args(
                'plan', '--target', str(cap_dir), '--kind', 'review', '--engine', 'codex:gpt-6.2-astra',
                '--session-id', 'sess-v11811', '--plan-dir', str(plan_dir), '--files', 'mid.md',
            ))
        self.assertEqual((rc_agy, rc_cx), (0, 0))
        self.assertTrue(p_agy['streams'][0]['exceeds_inline_cap'])
        self.assertEqual(p_agy['streams'][0]['inline_cap_bytes'], 128 * 1024)
        self.assertIn('128 KiB', Path(p_agy['card_detail_file']).read_text(encoding='utf-8'))
        self.assertFalse(p_cx['streams'][0]['exceeds_inline_cap'])
        self.assertEqual(p_cx['streams'][0]['inline_cap_bytes'], 512 * 1024)

        many_dir = self.root / 'v11811_many'
        many_dir.mkdir()
        many_rels = []
        for idx in range(230):
            rel = f'sub_dir_long_name/file_{idx:04d}.txt'
            (many_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (many_dir / rel).write_bytes(b'x' * 5000)
            many_rels.append(rel)
        many_summary = {}
        many_prompt = harness._inline_readonly_prompt('核查', many_dir, many_rels, summary=many_summary, prefix='agy')
        self.assertIn('个文件已省略', many_prompt)
        self.assertLessEqual(len(many_prompt.encode('utf-8')), 160 * 1024)
        self.assertEqual(len(many_summary['omitted_files']), harness._INLINE_OMITTED_RECEIPT_MAX_ITEMS)
        self.assertTrue(many_summary['omitted_files_truncated'])

        codex_guard_dir = self.root / 'v11811_cx_guard'
        codex_guard_dir.mkdir()
        (codex_guard_dir / 'a.txt').write_text('a' * 1000, encoding='utf-8')
        (codex_guard_dir / 'b.txt').write_text('b' * 1000, encoding='utf-8')
        guard_summary = {}
        with patch.object(harness, '_CODEX_MAX_PROMPT_CHARS', 1500):
            guarded = harness._inline_readonly_prompt('审查', codex_guard_dir, ['a.txt', 'b.txt'], summary=guard_summary, prefix='codex')
        self.assertLessEqual(len(guarded), 1500)
        self.assertEqual(guard_summary['inlined_count'], 1)
        self.assertEqual(guard_summary['omitted_files'], ['b.txt'])

        # 2. Windows [WinError 32] resilience: wrapup writes to result_wrapup.json without unlinking result.json,
        #    _rmtree_retry tolerates transient/persistent locks, and wrapup prompt enforces 1500-token compact contract
        retry_dir = self.root / 'v11811_retry_rm'
        retry_dir.mkdir()
        (retry_dir / 'lock.tmp').write_text('x', encoding='utf-8')
        rm_calls = []
        orig_rmtree = harness.shutil.rmtree

        def flaky_rmtree(p, *a, **kw):
            rm_calls.append(p)
            if len(rm_calls) < 3:
                raise PermissionError(13, 'WinError 32 locked')
            return orig_rmtree(p, *a, **kw)

        with patch.object(harness.shutil, 'rmtree', side_effect=flaky_rmtree), patch.object(harness.time, 'sleep'):
            harness._rmtree_retry(retry_dir, retries=5, delay=0.01)
        self.assertFalse(retry_dir.exists())
        self.assertEqual(len(rm_calls), 3)

        cx_wrap_cmds = []
        cx_wrap_prompts = []
        cx_now = [0.0]

        def run_cx_wrapup(command, cwd, stdin_text, timeout, **kwargs):
            cx_wrap_cmds.append(list(command))
            cx_wrap_prompts.append(stdin_text or '')
            out_idx = command.index('-o') + 1
            out_path = Path(command[out_idx])
            if len(cx_wrap_cmds) == 1:
                cx_now[0] = 760.0
                out_path.write_text('partial', encoding='utf-8')
                raise harness.HarnessError(
                    'soft timeout', 'timeout', process_started=True,
                    partial_stdout=json.dumps({'type': 'thread.started', 'thread_id': 'thr-win32'}) + '\n',
                    partial_stderr='',
                )
            out_path.write_text(json.dumps(TASK), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '', '')

        orig_unlink = Path.unlink

        def forbid_result_unlink(path_self, *args, **kwargs):
            if path_self.name == 'result.json':
                raise PermissionError(13, 'WinError 32: result.json locked')
            return orig_unlink(path_self, *args, **kwargs)

        with patch.object(harness.time, 'monotonic', side_effect=lambda: cx_now[0]), \
             patch.object(harness, 'run_process', side_effect=run_cx_wrapup), \
             patch.object(Path, 'unlink', autospec=True, side_effect=forbid_result_unlink), \
             patch.object(harness, '_rmtree_retry', side_effect=lambda p, ignore_errors=False, **kw: None if ignore_errors else orig_rmtree(p)):
            res_win32 = harness.invoke_engine(
                'codex:gpt-6.2-astra', self.root, '主任务', cap_engines, harness.TASK_JSON_SCHEMA,
                readonly=True, timeout_sec=900,
            )
        self.assertEqual(res_win32['summary'], TASK['summary'])
        self.assertEqual(len(cx_wrap_cmds), 2)
        self.assertEqual(Path(cx_wrap_cmds[0][cx_wrap_cmds[0].index('-o') + 1]).name, 'result.json')
        self.assertEqual(Path(cx_wrap_cmds[1][cx_wrap_cmds[1].index('-o') + 1]).name, 'result_wrapup.json')
        self.assertIn('1500 tokens', cx_wrap_prompts[1])

        # 3. agy 'with turn in progress' skips deadlocked wrapup; real step_update + cumulative result.usage detects stalled wrapup
        agy_bg_calls = []

        def run_agy_bg(command, cwd, stdin_text, timeout, **kwargs):
            agy_bg_calls.append(list(command))
            stdout = (
                json.dumps({'event': 'init', 'init': {'conversation_id': 'conv-bg-1'}}) + '\n'
                + json.dumps({'event': 'result', 'result': {'finished_reason': 'print_timeout', 'response': ''}}) + '\n'
            )
            return subprocess.CompletedProcess(command, 0, stdout, 'Print timed out after 500s with turn in progress.\n')

        bg_rec = {}
        with patch.object(harness, 'run_process', side_effect=run_agy_bg):
            with self.assertRaises(harness.HarnessError) as bg_exc:
                harness.invoke_engine(
                    'agy:gemini-4-carbon-high', self.root, '审查', cap_engines, harness.TASK_JSON_SCHEMA,
                    readonly=True, timeout_sec=600, receipt=bg_rec,
                )
        self.assertEqual(len(agy_bg_calls), 1, 'with turn in progress 不得继续发起必死锁的同会话 wrapup')
        self.assertTrue(bg_rec.get('wrapup_stalled'))
        self.assertEqual(bg_exc.exception.details.get('wrapup_status'), 'skipped_background_turn_active')
        self.assertFalse(bg_rec['timeout_salvage']['resumable'])
        self.assertEqual(bg_rec['timeout_salvage']['resume_blocked_reason'], 'agy_background_turn_in_progress')

        # Real agy step_update envelope + cumulative session usage on empty print_timeout must NOT be mistaken for progress
        stalled_events = [
            {'event': 'init', 'init': {'conversation_id': 'conv-stalled-2'}},
            {'event': 'step_update', 'step_update': {'step_type': 'user_input'}},
            {'event': 'step_update', 'step_update': {'step_type': 'system_message'}},
            {'event': 'result', 'result': {'finished_reason': 'print_timeout', 'response': '', 'usage': {'input_tokens': 50000, 'output_tokens': 1000, 'total_tokens': 51000}}},
        ]
        self.assertFalse(harness._turn_made_model_progress('agy', '\n'.join(json.dumps(e) for e in stalled_events), stalled_events))
        progressed_events = stalled_events[:2] + [{'event': 'step_update', 'step_update': {'step_type': 'planner_response'}}]
        self.assertTrue(harness._turn_made_model_progress('agy', '', progressed_events))

        # 4. agy cache_read_tokens normalization and multi-turn cumulative usage deduplication
        agy_usage_raw = json.dumps({'event': 'result', 'result': {
            'usage': {'input_tokens': 1000, 'cache_read_tokens': 5000, 'output_tokens': 200, 'total_tokens': 1200},
        }})
        norm_u = harness.extract_cli_token_usage('agy', agy_usage_raw)
        self.assertEqual(norm_u['input_tokens'], 6000)
        self.assertEqual(norm_u['cached_input_tokens'], 5000)
        self.assertEqual(norm_u['output_tokens'], 200)
        self.assertEqual(norm_u['total_tokens'], 6200)

        agy_multi_calls = []

        def run_agy_single_full_budget(command, cwd, stdin_text, timeout, **kwargs):
            agy_multi_calls.append((list(command), timeout))
            out = (
                json.dumps({'event': 'init', 'init': {'conversation_id': 'conv-dedup-1'}}) + '\n'
                + json.dumps({'event': 'step_update', 'step_update': {
                    'step_type': 'agent_response',
                    'usage': {'input_tokens': 1000, 'cache_read_tokens': 4000, 'output_tokens': 300, 'total_tokens': 1300},
                }}) + '\n'
                + json.dumps({'event': 'result', 'result': {
                    'structured_output': TASK,
                    'usage': {'input_tokens': 1200, 'cache_read_tokens': 4500, 'output_tokens': 500, 'total_tokens': 1700},
                }}) + '\n'
            )
            return subprocess.CompletedProcess(command, 0, out, '')

        dedup_rec = {}
        with patch.object(harness, 'run_process', side_effect=run_agy_single_full_budget):
            harness.invoke_engine(
                'agy:gemini-4-carbon-high', self.root, '审查', cap_engines, harness.TASK_JSON_SCHEMA,
                readonly=True, timeout_sec=600, receipt=dedup_rec,
            )
        self.assertEqual(len(agy_multi_calls), 1)
        self.assertEqual(agy_multi_calls[0][0][agy_multi_calls[0][0].index('--print-timeout') + 1], '600s')
        self.assertEqual(dedup_rec['token_usage']['input_tokens'], 5700)
        self.assertEqual(dedup_rec['token_usage']['cached_input_tokens'], 4500)
        self.assertEqual(dedup_rec['token_usage']['output_tokens'], 500)
        self.assertEqual(dedup_rec['token_usage']['total_tokens'], 6200)

        # 5. dsh writes isolated headless-sessions root and _session_logs scans headless-sessions first
        dsh_home = self.root / 'v11811_dsh_home'
        (dsh_home / 'sessions' / 'sid-old').mkdir(parents=True)
        (dsh_home / 'sessions' / 'sid-old' / 'session.jsonl.zstd').write_bytes(b'zstd')
        (dsh_home / 'headless-sessions' / 'sid-new').mkdir(parents=True)
        new_log = dsh_home / 'headless-sessions' / 'sid-new' / 'session.jsonl'
        new_log.write_text('{}\n', encoding='utf-8')
        self.assertEqual(harness._session_logs('dsh', dsh_home, 'sid-new', False), [new_log])

        captured_patch = []

        def run_dsh_check(command, cwd, stdin_text, timeout, **kwargs):
            p_idx = command.index('--patch') + 1
            captured_patch.append(Path(command[p_idx]).read_text(encoding='utf-8'))
            return subprocess.CompletedProcess(command, 0, json.dumps(TASK), '')

        cap_engines['dsh']['config_home'] = str(dsh_home)
        with patch.object(harness, '_dsh_supports_cli_permission_flags', return_value=True), \
             patch.object(harness, 'run_process', side_effect=run_dsh_check):
            harness.invoke_engine(
                'dsh:default', self.root, '检查', cap_engines, harness.TASK_JSON_SCHEMA,
                readonly=True, timeout_sec=60,
            )
        self.assertEqual(len(captured_patch), 1)
        self.assertIn('headless-sessions', captured_patch[0])

        # 6. Two-stage adaptive fast snapshot_files and symmetric _changes_report
        snap_dir = self.root / 'v11811_snap'
        snap_dir.mkdir()
        for idx in range(7):
            (snap_dir / f'f{idx}.txt').write_text(f'body-{idx}\n', encoding='utf-8')
        with patch.object(harness, '_FAST_SNAPSHOT_FILE_THRESHOLD', 5):
            before_fast = harness.snapshot_files(snap_dir)
            self.assertTrue(before_fast['f0.txt'].startswith('stat:'))
            # Delete 4 files (leaving 3 < threshold) and edit f1.txt
            for idx in range(3, 7):
                (snap_dir / f'f{idx}.txt').unlink()
            (snap_dir / 'f1.txt').write_text('body-1-edited-longer\n', encoding='utf-8')
            rep_fast = harness._changes_report(before_fast, snap_dir, (), readonly=True)
        self.assertEqual(sorted(rep_fast['changed_files']), ['f1.txt', 'f3.txt', 'f4.txt', 'f5.txt', 'f6.txt'])

        # 7. cmd_followup defaults timeout receipt resume to medium (respecting explicit --effort/--tier) and sets model
        fu_rec_path = self.root / 'v11811_timeout_rec.json'
        fu_rec_path.write_text(json.dumps({
            'status': 'timeout',
            'target': str(self.root),
            'engine': 'codex:gpt-6.2-astra',
            'readonly': True,
            'invocation': {
                'provider_session_id': 'thr-fu-1',
                'target_dir': str(self.root),
                'engine': 'codex:gpt-6.2-astra',
                'model_requested_to_cli': 'gpt-6.2-astra',
                'readonly': True,
                'tier': 'auto',
                'timeout_salvage': {'provider_session_id': 'thr-fu-1', 'resumable': True},
            },
        }), encoding='utf-8')
        rc_fu_def, out_fu_def = self.capture(harness.cmd_followup, self.args(
            'followup', '--receipt', str(fu_rec_path), '--task', '继续核对', '--dry-run',
        ))
        self.assertEqual(rc_fu_def, 0)
        self.assertEqual(out_fu_def['followup_tier'], 'medium')
        self.assertEqual(out_fu_def['model'], 'gpt-6.2-astra')

        rc_fu_exp, out_fu_exp = self.capture(harness.cmd_followup, self.args(
            'followup', '--receipt', str(fu_rec_path), '--task', '继续核对', '--effort', 'high', '--dry-run',
        ))
        self.assertEqual(rc_fu_exp, 0)
        self.assertEqual(out_fu_exp['followup_tier'], 'high')

        # 8. _citation_audit filters non-path noise while preserving real missing files and dotfiles
        cit_dir = self.root / 'v11811_cit'
        (cit_dir / 'paper').mkdir(parents=True)
        (cit_dir / 'paper' / 'existing.md').write_text('l1\nl2\nl3\nl4\nl5\nl6\n', encoding='utf-8')
        (cit_dir / '.gitignore').write_text('*.pyc\n', encoding='utf-8')
        cit_res = {
            'content': '噪声测试 #L22、2026/11/24:10、.en.md:8、100:2、docs/4_STUDIES.{zh,en}.md:10、`10–11｜paper/existing.md:5`',
            'evidence': ['真实存在 10–11｜paper/existing.md:5 与 .gitignore:1，真实缺失 src/missing_mod.py#L10'],
            'findings': [],
        }
        cit_audit = harness._citation_audit(cit_res, cit_dir, ['paper/existing.md', '.gitignore'], [])
        self.assertEqual(cit_audit['missing_files'], ['src/missing_mod.py'])
        self.assertEqual(cit_audit['out_of_range_lines'], [])
        self.assertEqual(cit_audit['unopened_cited_files'], ['src/missing_mod.py'])

    def test_review_severity_prompt_and_risk_tiered_effort(self):
        # 1. Audit and review task prompts enforce blocking vs advisory severity separation
        audit_p = harness._build_audit_prompt({'files': ['solver.py']}, '边界与一致性')
        self.assertIn('critical/high', audit_p)
        self.assertIn('medium/low', audit_p)
        review_p = harness._task_prompt('review', 'antigravity', self.root, True, [], '检查辅助脚本', '')
        self.assertIn('阻断级', review_p)
        non_review_p = harness._task_prompt('code', 'antigravity', self.root, False, ['a.py'], '实现函数', '')
        self.assertNotIn('【审查分级】', non_review_p)

        # 2. When role:review recommends high, routine reviews stay high while high-risk domain/security reviews escalate to max
        high_review_profile = json.loads(json.dumps(harness._fleet_profile()))
        high_review_profile['roles']['review']['recommended_tier'] = 'high'
        with patch.object(harness, '_fleet_profile', return_value=high_review_profile):
            self.assertEqual(
                harness.select_task_tier('auto', 'review', '审查普通辅助脚本与文档表述', engine='role:review'),
                ('high', 'role_profile_review_high'),
            )
            self.assertEqual(
                harness.select_task_tier('auto', 'review', '核查守恒通量与边界条件离散', engine='role:review'),
                ('max', 'complexity_high_domain_max'),
            )
            self.assertEqual(
                harness.select_task_tier('auto', 'review', '审查安全钩子权限越界拦截逻辑', engine='role:review'),
                ('max', 'complexity_high_domain_max'),
            )
            self.assertEqual(
                harness.select_task_tier('auto', 'direction', '梳理整体路线方案', engine='role:orchestrator'),
                ('high', 'role_profile_orchestrator_high'),
            )

    def test_stage1_reconcile_baseline_dependencies_obligations_and_cli(self):
        repo = self.root / 'reconcile_repo'
        (repo / 'src').mkdir(parents=True)
        (repo / 'tests').mkdir(parents=True)
        (repo / 'docs').mkdir(parents=True)
        (repo / 'src' / '__init__.py').write_text('', encoding='utf-8')
        (repo / 'src' / 'solver.py').write_text('def compute_flux(u):\n    return u\n', encoding='utf-8')
        (repo / 'src' / 'driver.py').write_text('from src.solver import compute_flux\n\ndef step(u):\n    return compute_flux(u)\n', encoding='utf-8')
        (repo / 'tests' / 'test_solver.py').write_text('import src.solver\n\ndef test_ok():\n    assert src.solver.compute_flux(1) == 1\n', encoding='utf-8')
        (repo / 'docs' / 'design.md').write_text('# 设计\n\n见 [求解器](../src/solver.py) 中的 `compute_flux` 函数。\n', encoding='utf-8')
        (repo / 'docs' / 'notes.md').write_text('# 杂记\n\n今天讨论了 solver 性能想法，未引用具体符号或路径。\n', encoding='utf-8')
        (repo / 'docs' / 'preexisting.md').write_text('# 旧脏文档\n初始内容\n', encoding='utf-8')

        git_ok = False
        try:
            env = harness._clean_git_env()
            subprocess.run(['git', '-C', str(repo), 'init'], env=env, check=True, capture_output=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], env=env, check=True, capture_output=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], env=env, check=True, capture_output=True)
            subprocess.run(['git', '-C', str(repo), 'add', 'src', 'tests', 'docs/design.md', 'docs/notes.md', 'docs/preexisting.md'], env=env, check=True, capture_output=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-m', 'init'], env=env, check=True, capture_output=True)
            # 制造开工前已脏但本轮不再改动的 preexisting.md
            (repo / 'docs' / 'preexisting.md').write_text('# 旧脏文档\n开工前已脏\n', encoding='utf-8')
            git_ok = True
        except (OSError, subprocess.SubprocessError):
            git_ok = False

        baseline_file = self.root / 'frozen_baseline.json'
        code_save, saved = self.capture(
            harness.cmd_reconcile,
            self.args('reconcile', '--target', str(repo), '--save-baseline', str(baseline_file)),
        )
        self.assertEqual(code_save, 0)
        self.assertTrue(baseline_file.is_file())
        if git_ok:
            self.assertEqual(saved['preexisting_dirty_count'], 1)

        # 修改 src/solver.py 并在任务窗口内直接 commit（验证不因已提交而漏检）
        (repo / 'src' / 'solver.py').write_text('def compute_flux(u):\n    return u * 2\n', encoding='utf-8')
        if git_ok:
            subprocess.run(['git', '-C', str(repo), 'add', 'src/solver.py'], env=harness._clean_git_env(), check=True, capture_output=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-m', 'update flux'], env=harness._clean_git_env(), check=True, capture_output=True)

        receipt_file = self.root / 'sample_receipt.json'
        receipt_file.write_text(json.dumps({
            'status': 'ok',
            'streams': [{
                'stream_id': 'S1', 'engine': 'codex:default', 'model': 'm1', 'status': 'ok', 'succeeded': True,
                'planned_files': ['src/solver.py', 'docs/design.md'],
                'citation_audit': {
                    'inlined_files': ['src/solver.py'],
                    'actual_files_read': [],
                    'missing_files': ['src/ghost.py'],
                    'unopened_cited_files': [],
                    'out_of_range_lines': [],
                },
                'verification_executed': True,
                'verify_exit_code': 0,
            }],
        }), encoding='utf-8')
        obligations_file = self.root / 'obligations.json'
        obligations_file.write_text(json.dumps([
            {'id': 'OB1', 'kind': 'modify', 'target': 'src/solver.py'},
            {'id': 'OB2', 'kind': 'modify', 'target': 'docs/design.md'},
            {'id': 'OB3', 'kind': 'read', 'target': 'src/solver.py'},
            {'id': 'OB4', 'kind': 'read', 'target': 'docs/design.md'},
            {'id': 'OB5', 'kind': 'test'},
            {'id': 'OB6', 'kind': 'final_audit'},
        ]), encoding='utf-8')

        out_md = self.root / 'out_reconcile.md'
        code_rec, out = self.capture(
            harness.cmd_reconcile,
            self.args(
                'reconcile', '--target', str(repo),
                '--baseline-file', str(baseline_file),
                '--receipt', str(receipt_file),
                '--obligations-file', str(obligations_file),
                '--output-md', str(out_md),
            ),
        )
        self.assertEqual(code_rec, 0)
        self.assertEqual(out['changes_status'], 'verified')
        self.assertEqual(out['counts']['changed_total'], 1)
        self.assertEqual(out['counts']['changed_by_category']['code'], 1)
        self.assertEqual(out['counts']['weak_mention_docs'], 1)
        pending_targets = {item['target'] for item in out['pending_review_top']}
        self.assertIn('docs/design.md', pending_targets)
        self.assertIn('src/driver.py', pending_targets)
        self.assertIn('tests/test_solver.py', pending_targets)
        self.assertNotIn('docs/notes.md', pending_targets)
        self.assertTrue(out_md.is_file())
        md_text = out_md.read_text(encoding='utf-8')
        self.assertIn('确定性边界', md_text)
        self.assertIn('`src/solver.py`', md_text)
        self.assertIn('`docs/design.md`', md_text)
        self.assertIn('**已满足**', md_text)
        self.assertIn('**未改·待复核**', md_text)
        self.assertIn('**缺凭据**', md_text)
        if git_ok:
            self.assertIn('docs/preexisting.md', md_text)

        # 非 Git 且无基线时诚实报告 changes_status == unknown
        plain_dir = self.root / 'plain_no_git'
        plain_dir.mkdir()
        (plain_dir / 'a.py').write_text('x = 1\n', encoding='utf-8')
        plain_md = self.root / 'plain_reconcile.md'
        code_plain, out_plain = self.capture(
            harness.cmd_reconcile,
            self.args('reconcile', '--target', str(plain_dir), '--output-md', str(plain_md)),
        )
        self.assertEqual(code_plain, 0)
        self.assertEqual(out_plain['changes_status'], 'unknown')
        self.assertIn('未知', out_plain['summary_line'])

    @patch.dict(os.environ, {'CAE_HARNESS_DEPTH': '', 'CAE_DISABLE_HEAVY_COMPLETIONS': ''})
    def test_stage1_dispatch_completion_hard_triggers_reconcile_without_polluting_target(self):
        engines = self.four_backends()
        home = Path(engines['codex']['config_home'])
        home.mkdir()
        (home / 'config.toml').write_text('model = "fixture-code"\nmodel_context_window = 65536\n', encoding='utf-8')
        proj = self.root / 'biz_proj'
        (proj / 'src').mkdir(parents=True)
        (proj / 'docs').mkdir(parents=True)
        (proj / 'src' / 'core.py').write_text('def calc():\n    return 1\n', encoding='utf-8')
        (proj / 'docs' / 'spec.md').write_text('# 规范\n详见 [core](../src/core.py) 的 `calc`。\n', encoding='utf-8')

        ob_file = self.root / 'ticket_obs.json'
        ob_file.write_text(json.dumps([
            {'id': 'M1', 'kind': 'modify', 'target': 'src/core.py'},
            {'id': 'M2', 'kind': 'modify', 'target': 'docs/spec.md'},
        ]), encoding='utf-8')
        spec = self.root / 'batch_reconcile_spec.json'
        spec.write_text(json.dumps([{
            'stream_id': 'S1', 'subtask': '修改 core.py', 'kind': 'code',
            'engine': 'codex:default', 'target': str(proj),
            'write_scope': ['src/core.py'], 'timeout': 60,
        }]), encoding='utf-8')
        plans = self.root / 'state_reconcile' / 'dispatch_plans'
        common = ['--caller', 'antigravity', '--plan-dir', str(plans), '--session-id', 'rec-sess-1']

        with patch.object(harness, '_args_backends', return_value=engines):
            _, ticket = self.capture(
                harness.cmd_plan,
                self.args('plan', *common, '--spec-file', str(spec), '--obligations-file', str(ob_file), '--full-json'),
            )
            self.assertEqual(len(ticket.get('obligations') or []), 2)
            tid = ticket['ticket_id']
            _, approved = self.capture(harness.cmd_plan, self.args('plan', *common, '--approve-ticket', tid))
            self.assertEqual(approved['status'], 'APPROVED')

            def edit_worker(*_a, receipt, **_kw):
                (proj / 'src' / 'core.py').write_text('def calc():\n    return 2\n', encoding='utf-8')
                receipt['token_usage'] = harness.extract_cli_token_usage('codex', '{"usage":{"input_tokens":15,"output_tokens":5}}')
                return dict(TASK)

            with patch.object(harness, 'invoke_engine', side_effect=edit_worker):
                code, res = self.capture(
                    harness.cmd_batch,
                    self.args('batch', *common, '--ticket', tid),
                )
        self.assertEqual(code, 0)
        self.assertIn('reconciliation', res)
        rec_info = res['reconciliation']
        rec_path = Path(rec_info['report_file'])
        self.assertTrue(rec_path.is_file())
        self.assertTrue(rec_path.is_relative_to(plans / 'reconcile'))
        # 绝不污染目标业务仓库目录
        self.assertFalse((proj / 'reconcile').exists())
        self.assertEqual(sorted(p.relative_to(proj).as_posix() for p in proj.rglob('*') if p.is_file()),
                         ['docs/spec.md', 'src/core.py'])
        md_body = rec_path.read_text(encoding='utf-8')
        self.assertIn('`src/core.py`', md_body)
        self.assertIn('`docs/spec.md`', md_body)
        self.assertIn('M2', md_body)
        self.assertIn('未改·待复核', md_body)
        hc_rows = json.loads((plans.parent / 'heavy_completions' / 'rec-sess-1.json').read_text(encoding='utf-8'))
        self.assertEqual(hc_rows[0]['reconcile_file'], str(rec_path))
        self.assertTrue(hc_rows[0]['reconcile_summary'])

    def test_stage2_read_plan_dual_budget_oversized_split_dependencies_and_waves(self):
        engines = self.four_backends()
        proj = self.root / 'read_plan_proj'
        (proj / 'src').mkdir(parents=True)
        (proj / 'docs').mkdir(parents=True)
        (proj / '历史探索').mkdir(parents=True)
        (proj / 'docs' / 'constitution.md').write_text('# 宪法\n- 物理守恒红线。\n', encoding='utf-8')
        (proj / 'src' / 'flux.py').write_text('def hllc(u_l, u_r):\n    return 0.5 * (u_l + u_r)\n', encoding='utf-8')
        (proj / 'src' / 'driver.py').write_text('from src.flux import hllc\n\ndef step(a, b):\n    return hllc(a, b)\n', encoding='utf-8')
        (proj / 'docs' / 'flux_spec.md').write_text('# 通量规范\n详见 [flux](../src/flux.py)。\n', encoding='utf-8')
        # 构造超过 150 KiB 的超大文档，带清晰章节边界（验证连续行段自动切分且 1..N 全覆盖无跳行）
        long_sections = []
        for sec in range(1, 26):
            long_sections.append(f'## §{sec}. 数值实验记录与判据\n')
            for ln in range(1, 120):
                long_sections.append(f'- 第 {sec} 节第 {ln} 行：验证守恒残差与高阶通量离散稳定性指标 1.23456789e-14。\n')
        (proj / 'docs' / 'long_record.md').write_text(''.join(long_sections), encoding='utf-8')
        total_long_lines = len((proj / 'docs' / 'long_record.md').read_text(encoding='utf-8').splitlines())
        (proj / '历史探索' / 'old_rejection.md').write_text('# 历史否决\n禁止全局 Krylov。\n', encoding='utf-8')

        plans = self.root / 'rp_state' / 'dispatch_plans'
        spec_out = self.root / 'rp_emitted_spec.json'
        with patch.object(harness, '_args_backends', return_value=engines):
            code, out = self.capture(
                harness.cmd_read_plan,
                self.args(
                    'read-plan', '--target', str(proj),
                    '--shared-files', 'docs/constitution.md',
                    '--core-files', 'src/flux.py',
                    '--engines', 'codex:default,agy:default',
                    '--cross-client-mode', 'risk-tiered',
                    '--max-concurrency', '2',
                    '--plan-dir', str(plans),
                    '--emit-spec', str(spec_out),
                    '--session-id', 'rp-sess-1',
                ),
            )
        self.assertEqual(code, 0)
        self.assertEqual(out['budget']['min_inline_cap_bytes'], 128 * 1024)
        self.assertEqual(out['budget']['effective_concurrency'], 2)
        self.assertGreaterEqual(out['counts']['oversized_split_count'], 1)
        self.assertGreaterEqual(out['counts']['shard_count'], 2)
        self.assertFalse(out['single_session_safe'])
        self.assertTrue(Path(out['report_file']).is_file())
        self.assertTrue(Path(out['plan_json_file']).is_file())
        self.assertTrue(spec_out.is_file())
        # 验证超大单文件切分的连续行段并集严格覆盖 1..total_long_lines（零跳行缺口）
        full_plan = json.loads(Path(out['plan_json_file']).read_text(encoding='utf-8'))
        long_ranges = [
            lr for sh in full_plan['shards'] for lr in sh.get('line_ranges') or []
            if lr['file'] == 'docs/long_record.md'
        ]
        self.assertGreaterEqual(len(long_ranges), 2)
        covered_lines = set()
        for lr in long_ranges:
            covered_lines.update(range(lr['line_start'], lr['line_end'] + 1))
        self.assertEqual(covered_lines, set(range(1, total_long_lines + 1)))
        # 验证保守波次并发上限 <= 2 且导出 spec 无内部私有字段
        emitted = json.loads(spec_out.read_text(encoding='utf-8'))
        self.assertTrue(emitted)
        by_wave = {}
        for st in emitted:
            self.assertFalse(any(k.startswith('_') for k in st))
            by_wave.setdefault(st['wave'], []).append(st)
        self.assertTrue(all(len(items) <= 2 for items in by_wave.values()))
        # 验证归档/历史目录默认如实披露，且传 --include-dirs 后可显式纳入
        self.assertTrue(any(d['dir'] == '历史探索' for d in out['excluded_archive_dirs']))
        self.assertNotIn('历史探索/old_rejection.md', full_plan['planned_files'])
        ptr = plans / 'read_plans' / 'session_rp-sess-1_latest.json'
        self.assertTrue(ptr.is_file())

        with patch.object(harness, '_args_backends', return_value=engines):
            code_inc, out_inc = self.capture(
                harness.cmd_read_plan,
                self.args(
                    'read-plan', '--target', str(proj),
                    '--engines', 'codex:default,dsh:default',
                    '--cross-client-mode', 'all-engines',
                    '--include-dirs', '历史探索',
                    '--plan-dir', str(plans),
                ),
            )
        self.assertEqual(code_inc, 0)
        inc_plan = json.loads(Path(out_inc['plan_json_file']).read_text(encoding='utf-8'))
        self.assertIn('历史探索/old_rejection.md', inc_plan['planned_files'])
        # all-engines 模式下每片都应分配给两个引擎
        self.assertTrue(all(len(sh['assigned_engines']) == 2 for sh in inc_plan['shards']))

        # 超大共享材料超内联上限时自动降级为 anchor_reference_only 并标红告警
        with patch.object(harness, '_args_backends', return_value=engines):
            _, out_heavy = self.capture(
                harness.cmd_read_plan,
                self.args(
                    'read-plan', '--target', str(proj),
                    '--shared-files', 'docs/long_record.md',
                    '--engines', 'agy:default',
                    '--plan-dir', str(plans),
                ),
            )
        self.assertEqual(out_heavy['budget']['shared_mode'], 'anchor_reference_only')
        self.assertTrue(any(a['kind'] == 'shared_context_exceeds_budget' for a in out_heavy['capacity_alerts']))

    def test_stage3_cross_check_coverage_blind_spots_divergence_and_reconcile_merge(self):
        proj = self.root / 'cross_proj'
        (proj / 'src').mkdir(parents=True)
        (proj / 'docs').mkdir(parents=True)
        (proj / 'src' / 'flux.py').write_text('def hllc(u):\n    return u\n', encoding='utf-8')
        (proj / 'src' / 'driver.py').write_text('from src.flux import hllc\n', encoding='utf-8')
        (proj / 'docs' / 'flux_spec.md').write_text('# 规范\n见 [flux](../src/flux.py)\n', encoding='utf-8')
        (proj / 'docs' / 'missing_core.md').write_text('# 必读核心\n', encoding='utf-8')

        rp_json = self.root / 'sample_read_plan.json'
        rp_json.write_text(json.dumps({
            'plan_id': 'rp-cross-1',
            'engines': [{'engine': 'codex:gpt-6.2-sol'}, {'engine': 'dsh:deepseek-v4-pro'}],
            'planned_files': ['src/flux.py', 'src/driver.py', 'docs/flux_spec.md', 'docs/missing_core.md'],
            'shared_files': [],
            'budget': {'shared_mode': 'none'},
            'shards': [
                {'shard_id': 1, 'files': ['src/flux.py', 'docs/flux_spec.md'], 'assigned_engines': ['codex:gpt-6.2-sol', 'dsh:deepseek-v4-pro']},
                {'shard_id': 2, 'files': ['src/driver.py', 'docs/missing_core.md'], 'assigned_engines': ['codex:gpt-6.2-sol', 'dsh:deepseek-v4-pro']},
            ],
            'cross_shard_edges': [
                {'from_shard': 2, 'to_shard': 1, 'source': 'src/driver.py', 'target': 'src/flux.py', 'relation': 'py_import'},
            ],
        }), encoding='utf-8')

        receipt_path = self.root / 'cross_batch_receipt.json'
        receipt_path.write_text(json.dumps({
            'status': 'partial',
            'streams': [
                {
                    'stream_id': 'S1_CODEX', 'engine': 'codex:gpt-6.2-sol', 'model': 'gpt-6.2-sol',
                    'status': 'ok', 'succeeded': True,
                    'planned_files': ['src/flux.py', 'docs/flux_spec.md', 'src/driver.py'],
                    'citation_audit': {'inlined_files': ['src/flux.py', 'docs/flux_spec.md', 'src/driver.py'], 'actual_files_read': []},
                    'result': {'findings': [
                        {'file': 'src/flux.py', 'line_start': 1, 'line_end': 2, 'severity': 'high', 'description': '界面法向方向错误，导致通量不守恒', 'evidence': 'hllc L1-2'},
                        {'file': 'src/driver.py', 'line_start': 1, 'line_end': 1, 'severity': 'medium', 'description': '缺少边界条件检查', 'evidence': 'driver L1'},
                    ]},
                },
                {
                    'stream_id': 'S2_DSH_OK', 'engine': 'dsh:deepseek-v4-pro', 'model': 'deepseek-v4-pro',
                    'status': 'ok', 'succeeded': True,
                    'planned_files': ['src/flux.py'],
                    'citation_audit': {'inlined_files': ['src/flux.py'], 'actual_files_read': []},
                    'result': {'findings': [
                        {'file': 'src/flux.py', 'line_start': 1, 'line_end': 2, 'severity': 'low', 'description': '法向符号已满足约定，无需修改', 'evidence': 'hllc L1-2'},
                    ]},
                },
                {
                    'stream_id': 'S3_DSH_TIMEOUT', 'engine': 'dsh:deepseek-v4-pro', 'model': 'deepseek-v4-pro',
                    'status': 'timeout', 'succeeded': False,
                    'planned_files': ['src/driver.py'],
                    'citation_audit': {'inlined_files': [], 'actual_files_read': []},
                    'result': {},
                },
            ],
        }), encoding='utf-8')

        plans = self.root / 'cross_state' / 'dispatch_plans'
        code_cc, out_cc = self.capture(
            harness.cmd_cross_check,
            self.args(
                'cross-check', '--target', str(proj),
                '--receipt', str(receipt_path),
                '--read-plan', str(rp_json),
                '--plan-dir', str(plans),
            ),
        )
        self.assertEqual(code_cc, 0)
        self.assertEqual(out_cc['counts']['multi_client_delivered'], 1)  # src/flux.py
        self.assertIn('docs/missing_core.md', out_cc['shared_blind_spots'])
        self.assertGreaterEqual(out_cc['counts']['failed_or_timeout_cells'], 1)
        self.assertEqual(out_cc['counts']['divergent_findings'], 1)
        cc_full = json.loads(Path(out_cc['cross_check_json_file']).read_text(encoding='utf-8'))
        co_item = cc_full['co_located_multi_engine'][0]
        self.assertTrue(co_item['cross_client'])
        self.assertTrue(co_item['severity_divergent'])
        self.assertTrue(co_item['polarity_divergence_hint'])
        self.assertTrue(co_item['r6_host_verification_required'])
        self.assertEqual(len(co_item['items']), 2)
        # 单端发现中必须如实暴露对端 dsh 为 failed_or_timeout 而非无异议
        single_drv = next(x for x in cc_full['single_engine_findings'] if x['file'] == 'src/driver.py')
        self.assertTrue(single_drv['other_engine_failed_or_missing'])
        self.assertEqual(single_drv['other_engines_coverage']['dsh:deepseek-v4-pro'], 'failed_or_timeout')
        # 并轨进阶段 1 机械对账表
        merged_md = self.root / 'merged_reconcile.md'
        code_rec, out_rec = self.capture(
            harness.cmd_reconcile,
            self.args(
                'reconcile', '--target', str(proj),
                '--receipt', str(receipt_path),
                '--cross-check', str(out_cc['cross_check_json_file']),
                '--output-md', str(merged_md),
            ),
        )
        self.assertEqual(code_rec, 0)
        merged_text = merged_md.read_text(encoding='utf-8')
        self.assertIn('跨端汇总核对并轨', merged_text)
        self.assertIn('docs/missing_core.md', merged_text)
        self.assertIn('shared_blind_spot', merged_text)
        self.assertIn('cross_client_finding_divergence', merged_text)

    def test_doc_hygiene_universal_checks(self):
        """通用文档卫生守卫：拦截计数失步、补丁超长句与斜杠串、跨文档逐字复读及入口页内部符号与密度超标。"""
        proj = self.root / 'doc_hygiene_proj'
        (proj / 'docs').mkdir(parents=True)
        (proj / 'src').mkdir(parents=True)
        (proj / 'src' / 'solver.py').write_text(
            'def _internal_helper():\n    return 1\n\ndef compute_flux(dt, cfl, rtol, atol, nstep, mesh, bc, lim):\n    return dt\n',
            encoding='utf-8',
        )
        dup_clause = '单路求解流在省略默认通道编号时必须匹配具体模型名称并且不得以占位词替代核心校验项'
        long_sent = '本求解器支持' + ('高阶间断有限元离散与隐式时间推进及自适应网格加密并保持守恒性' * 8) + '。'
        (proj / 'README.md').write_text(
            '# 求解器总览\n\n'
            '下面把最容易出错的三个环节列成清单：\n\n'
            '| 环节 | 说明 |\n'
            '| --- | --- |\n'
            '| 1 | 网格 |\n'
            '| 2 | 通量 |\n'
            '| 3 | 边界 |\n'
            '| 4 | 验收 |\n\n'
            '核心流程调用 `_internal_helper` 处理异常，并联动 `dt`、`cfl`、`rtol`、`atol`、`nstep`、`mesh`、`bc`、`lim` 八个参数。\n\n'
            '注意防范 零复跑/局部补测/矛盾宣称/漏报 这类斜杠串。\n\n'
            f'{long_sent}\n\n'
            f'说明（{dup_clause}）。\n',
            encoding='utf-8',
        )
        (proj / 'docs' / 'guide.md').write_text(
            '# 使用指南\n\n'
            f'执行约束（补充：{dup_clause}）。\n',
            encoding='utf-8',
        )
        (proj / 'docs' / 'arch.md').write_text(
            '# 架构说明\n\n'
            f'底层规则（细则：{dup_clause}）。\n',
            encoding='utf-8',
        )

        # 默认 check-grounding 仅做词法锚定，不报错；开启 --doc-hygiene 检出全部结构卫生缺陷
        code_hyg, rep_hyg = self.capture(
            harness.cmd_check_grounding,
            self.args('check-grounding', '--target', str(proj), '--root', str(proj), '--strict', '--doc-hygiene'),
        )
        self.assertEqual(code_hyg, 2)
        self.assertFalse(rep_hyg['ok'])
        cats = set(rep_hyg['counts_by_category'])
        self.assertIn('count_structure_mismatch', cats)
        self.assertIn('landing_private_symbol', cats)
        self.assertIn('landing_jargon_density', cats)
        self.assertIn('slash_chain', cats)
        self.assertIn('runon_patch_sentence', cats)
        self.assertIn('cross_doc_duplication', cats)

        # 超大入口 README 触发 landing_size_budget
        big_proj = self.root / 'doc_hygiene_big'
        big_proj.mkdir()
        (big_proj / 'README.md').write_text('# Big\n\n' + ('简洁说明段落。\n\n' * 1000), encoding='utf-8')
        rep_big = harness.audit_doc_grounding(big_proj, root_dir=big_proj, doc_hygiene=True)
        self.assertIn('landing_size_budget', rep_big['counts_by_category'])

        # 干净的分层文档（含合法 2 项列表、带引号口诀、POSIX 路径与唯一真源细则）应零告警通过
        clean_proj = self.root / 'doc_hygiene_clean'
        (clean_proj / 'docs').mkdir(parents=True)
        (clean_proj / 'src').mkdir(parents=True)
        (clean_proj / 'src' / 'solver.py').write_text('def compute_flux(u):\n    return u\n', encoding='utf-8')
        (clean_proj / 'README.md').write_text(
            '# 求解器总览\n\n'
            '只需记住“两步定位，三步验算”就行：\n\n'
            '1. 安装依赖。\n'
            '2. 运行算例，脚本位于 `src/solver.py`。\n\n'
            '以下两项为核心特性：\n\n'
            '| 特性 | 说明 |\n'
            '| --- | --- |\n'
            '| 守恒 | 离散质量守恒 |\n'
            '| 稳定 | 显隐时间推进 |\n',
            encoding='utf-8',
        )
        (clean_proj / 'docs' / 'arch.md').write_text(
            '# 架构说明\n\n'
            f'唯一真源细则（{dup_clause}）。\n',
            encoding='utf-8',
        )
        code_clean, rep_clean = self.capture(
            harness.cmd_check_grounding,
            self.args('check-grounding', '--target', str(clean_proj), '--root', str(clean_proj), '--strict', '--doc-hygiene'),
        )
        self.assertEqual(code_clean, 0, rep_clean)
        self.assertTrue(rep_clean['ok'])

    def test_doc_hygiene_cross_format_media_and_theorem_shells(self):
        """跨格式交付卫生：覆盖 .tex/.md/.rst 的本地图件断链与多命题零证明空壳，且不误报合法LaTeX宏与大纲文件。"""
        proj = self.root / 'cross_format_hygiene'
        (proj / 'paper' / 'figures').mkdir(parents=True)
        (proj / 'docs').mkdir(parents=True)
        (proj / 'src').mkdir(parents=True)
        (proj / 'src' / 'dgsem.f90').write_text('! DGSEM flux solver\nsubroutine dgsem_step()\nend subroutine\n', encoding='utf-8')
        (proj / 'paper' / 'refs.bib').write_text('@article{Cahn1958, title={Free Energy}, journal={JCP}}\n', encoding='utf-8')
        (proj / 'paper' / 'figures' / 'valid_plot.pdf').write_bytes(b'%PDF-1.4\n')
        (proj / 'paper' / 'old').mkdir(parents=True)
        (proj / 'paper' / 'old' / 'old_only.pdf').write_bytes(b'%PDF-1.4\n')
        (proj / 'root_only.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (proj / 'docs' / 'ok.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (proj / 'docs' / 'mesh(v2).png').write_bytes(b'\x89PNG\r\n\x1a\n')

        # 1) .tex 含有效图（依赖 \graphicspath 与自动扩展名）、注释掉的 \graphicspath、断链图、以及自定义 \newtheorem 2 条 0 证明
        (proj / 'paper' / 'manuscript.tex').write_text(
            '\\documentclass{article}\n'
            '\\usepackage{graphicx}\n'
            '\\graphicspath{{figures/}}\n'
            '% \\graphicspath{{old/}}\n'
            '\\newtheorem{thm}{Theorem}\n'
            '\\begin{document}\n'
            '\\section{Method}\n'
            'The DGSEM solver obeys energy stability \\cite{Cahn1958}.\n'
            '% \\includegraphics{commented_missing.pdf}\n'
            '\\includegraphics[width=0.8\\linewidth]{valid_plot}\n'
            '\\includegraphics[width=0.8\\linewidth]{old_only}\n'
            '\\includegraphics[width=0.8\\linewidth]{missing_fig.pdf}\n'
            '\\begin{thm}[Energy bound]\n'
            'Discrete free energy decays.\n'
            '\\end{thm}\n'
            '\\begin{thm}[Mass conservation]\n'
            'Total mass is conserved.\n'
            '\\end{thm}\n'
            '\\end{document}\n',
            encoding='utf-8',
        )
        # 1b) 被 \input 纳入的章节文件：首行含注释 % \begin{document} 与同行公式后跟无锚定大写缩写，不应被吞掉
        (proj / 'paper' / 'chapter.tex').write_text(
            '% \\begin{document}\n'
            '\\begin{equation}x=1\\end{equation} BOGUSACR appears after inline equation.\n',
            encoding='utf-8',
        )
        # 2) .md 含引用框命题 2 条 0 证明、断链本地图（含仅存在于仓库根的同名图）、带括号合法图名、以及合法外链
        (proj / 'docs' / 'report.md').write_text(
            '# 数值分析报告\n\n'
            '![有效图](ok.png)\n'
            '![带括号图](mesh(v2).png)\n'
            '![外链图](https://example.com/demo.png)\n'
            '![仅根存在断链](root_only.png)\n'
            '![断链图](figures/missing_chart.png)\n\n'
            '> **命题 1（半离散守恒）**\n'
            '> 总质量守恒。\n\n'
            '> **命题 2（自由能衰减）**\n'
            '> 自由能单调衰减。\n',
            encoding='utf-8',
        )
        # 3) .rst 含断链本地图与原生下划线标题命题 2 条 0 证明
        (proj / 'docs' / 'notes.rst').write_text(
            'Analysis\n========\n\n'
            '.. figure:: missing_rst.svg\n\n'
            'Theorem 1\n---------\n\nMass holds.\n\n'
            'Theorem 2\n---------\n\nEnergy holds.\n',
            encoding='utf-8',
        )
        # 4) 大纲文档声明命题清单但不应误报 unproved_theorem_shell
        (proj / 'docs' / 'paper_outline.md').write_text(
            '# 论文大纲\n\n'
            '**Theorem 1.** Energy stability.\n\n'
            '**Theorem 2.** Mass conservation.\n',
            encoding='utf-8',
        )

        rep = harness.audit_doc_grounding(proj, root_dir=proj, doc_hygiene=True)
        self.assertIn('paper/manuscript.tex', rep['checked_files'])
        self.assertFalse(rep['ok'])
        by_file_cat = {(f['file'], f['category'], f['token']) for f in rep['findings']}
        self.assertIn(('paper/manuscript.tex', 'broken_local_media_ref', 'missing_fig.pdf'), by_file_cat)
        self.assertIn(('paper/manuscript.tex', 'broken_local_media_ref', 'old_only'), by_file_cat)
        self.assertIn(('paper/manuscript.tex', 'unproved_theorem_shell', '2 formal claims, 0 proofs'), by_file_cat)
        self.assertIn(('paper/chapter.tex', 'ungrounded_acronym', 'BOGUSACR'), by_file_cat)
        self.assertIn(('docs/report.md', 'broken_local_media_ref', 'figures/missing_chart.png'), by_file_cat)
        self.assertIn(('docs/report.md', 'broken_local_media_ref', 'root_only.png'), by_file_cat)
        self.assertIn(('docs/report.md', 'unproved_theorem_shell', '2 formal claims, 0 proofs'), by_file_cat)
        self.assertIn(('docs/notes.rst', 'broken_local_media_ref', 'missing_rst.svg'), by_file_cat)
        self.assertIn(('docs/notes.rst', 'unproved_theorem_shell', '2 formal claims, 0 proofs'), by_file_cat)
        # 不误报有效图、带括号有效图、注释图、外链图、大纲文件与常规 LaTeX 宏
        self.assertFalse(any(f['file'] == 'docs/paper_outline.md' for f in rep['findings']))
        self.assertFalse(any(f['token'] in ('valid_plot', 'commented_missing.pdf', 'ok.png', 'mesh(v2).png') for f in rep['findings']))
        self.assertFalse(any(f['file'] == 'paper/manuscript.tex' and f['category'].startswith('ungrounded_') for f in rep['findings']))

        # 补齐证明（或显式指向补充材料）并修复图件与无锚定词后应零告警通过
        (proj / 'paper' / 'chapter.tex').write_text('% \\begin{document}\n\\begin{equation}x=1\\end{equation} DGSEM text.\n', encoding='utf-8')
        (proj / 'paper' / 'manuscript.tex').write_text(
            '\\documentclass{article}\n'
            '\\graphicspath{{figures/}}\n'
            '\\newtheorem{thm}{Theorem}\n'
            '\\begin{document}\n'
            'The DGSEM solver obeys energy stability \\cite{Cahn1958}.\n'
            '\\includegraphics{valid_plot}\n'
            '\\begin{thm}A\\end{thm}\n'
            '\\begin{proof}Direct integration.\\end{proof}\n'
            '\\begin{thm}B\\end{thm}\n'
            '\\end{document}\n',
            encoding='utf-8',
        )
        (proj / 'docs' / 'report.md').write_text(
            '# 数值分析报告\n\n'
            '![有效图](ok.png)\n'
            '![带括号图](mesh(v2).png)\n\n'
            '> **命题 1（半离散守恒）**\n\n'
            '> **命题 2（自由能衰减）**\n\n'
            '上述两条命题的完整证明详见补充材料。\n',
            encoding='utf-8',
        )
        (proj / 'docs' / 'notes.rst').write_text(
            'Analysis\n========\n\n'
            '.. image:: ok.png\n\n'
            'Theorem 1\n---------\n\nMass holds.\n\n'
            '.. proof::\n   Direct integration.\n\n'
            'Theorem 2\n---------\n\nEnergy holds.\n',
            encoding='utf-8',
        )
        rep_fixed = harness.audit_doc_grounding(proj, root_dir=proj, doc_hygiene=True)
        self.assertTrue(rep_fixed['ok'], rep_fixed['findings'])

    def test_v204_codex_custom_exec_trace_citation_grounding_and_batch_concurrent(self):
        """回归测试：Codex 0.171 custom_tool_call('exec') 读盘追踪、citation_audit 子路径消歧、
        check-grounding 统一排除归档与去 fleet_profile.json 文案泄露及 --output/--hygiene-only、
        cmd_batch OS 只读沙箱外部并发变动不误判 scope_violation。"""
        proj = self.root / 'v204_proj'
        (proj / 'src' / 'paper').mkdir(parents=True)
        (proj / '归档').mkdir(parents=True)
        (proj / '.runtime').mkdir(parents=True)
        (proj / 'archive_old').mkdir(parents=True)
        (proj / 'src' / 'paper' / 'manuscript.md').write_text(
            '# 离散格式分析\n\n第 2 节给出了离散守恒形式。\n', encoding='utf-8',
        )
        (proj / 'src' / 'solver.py').write_text('CFL_MAX = 0.5\n', encoding='utf-8')
        # 归档/.runtime/archive_old 里的旧草稿含坏链接与陌生缩写，应被统一排除
        (proj / '归档' / 'old_draft.md').write_text('旧草稿含 BOGUSACR 与 ![坏图](missing.png)\n', encoding='utf-8')
        (proj / '.runtime' / 'scratch.md').write_text('运行时临时文档含 BOGUSRUN\n', encoding='utf-8')
        (proj / 'archive_old' / 'legacy.md').write_text('旧版含 BOGUSOLD\n', encoding='utf-8')

        # 1) _session_trace 解析 Codex 0.171 custom_tool_call("exec") + custom_tool_call_output
        events = [
            {
                'type': 'response_item',
                'payload': {
                    'type': 'custom_tool_call',
                    'status': 'completed',
                    'call_id': 'call_exec_1',
                    'name': 'exec',
                    'input': 'const r = await tools.exec_command({cmd: "Get-Content -LiteralPath \'src/paper/manuscript.md\'"});',
                },
            },
            {
                'type': 'response_item',
                'payload': {
                    'type': 'custom_tool_call_output',
                    'call_id': 'call_exec_1',
                    'output': [
                        {'type': 'input_text', 'text': 'Script completed\nWall time 0.3 seconds\nOutput:\n'},
                        {'type': 'input_text', 'text': '{"exit_code":0,"output":"ok"}'},
                    ],
                },
            },
            {
                'type': 'response_item',
                'payload': {
                    'type': 'custom_tool_call',
                    'status': 'completed',
                    'call_id': 'call_exec_fail',
                    'name': 'exec',
                    'input': 'const r = await tools.exec_command({cmd: "cat src/solver.py"});',
                },
            },
            {
                'type': 'response_item',
                'payload': {
                    'type': 'custom_tool_call_output',
                    'call_id': 'call_exec_fail',
                    'output': [{'type': 'input_text', 'text': '{"exit_code":1,"output":"err"}'}],
                },
            },
        ]
        reads, _, _ = harness._session_trace(events, proj, None)
        self.assertEqual(reads, ['src/paper/manuscript.md'])

        # 2) _citation_audit 支持简写文件名/子路径唯一匹配已打开文件
        cit = harness._citation_audit(
            {'content': '参见 `manuscript.md#L1-L3` 与 `paper/manuscript.md:2`', 'evidence': [], 'findings': []},
            proj,
            reads=['src/paper/manuscript.md'],
            inlined=[],
        )
        self.assertEqual(cit['missing_files'], [])
        self.assertEqual(cit['unopened_cited_files'], [])
        self.assertEqual(cit['out_of_range_lines'], [])

        # 3) check-grounding 统一跳过 归档/.runtime/archive_old；通用项目报错不再提及 fleet_profile.json；支持 --output 与 --hygiene-only
        (proj / 'report.md').write_text('# 汇报\n\n本轮测试共输出 999999 tokens。\n', encoding='utf-8')
        out_json = self.root / 'grounding_out.json'
        rc, rep = self.capture(
            harness.cmd_check_grounding,
            self.args('check-grounding', '--target', str(proj), '--root', str(proj), '--strict', '--doc-hygiene', '--output', str(out_json)),
        )
        self.assertEqual(rc, harness.EXIT_CODES['verification_failed'])
        self.assertTrue(out_json.is_file())
        checked = set(rep['checked_files'])
        self.assertNotIn('归档/old_draft.md', checked)
        self.assertNotIn('.runtime/scratch.md', checked)
        self.assertNotIn('archive_old/legacy.md', checked)
        num_findings = [f for f in rep['findings'] if f['category'] == 'ungrounded_number']
        self.assertTrue(num_findings)
        self.assertNotIn('fleet_profile.json', num_findings[0]['reason'])
        # --hygiene-only 跳过源码词法锚定，保留卫生校验（无坏图/空壳/超长句时通过）
        rc_hyg, rep_hyg = self.capture(
            harness.cmd_check_grounding,
            self.args('check-grounding', '--target', str(proj), '--root', str(proj), '--strict', '--hygiene-only'),
        )
        self.assertEqual(rc_hyg, 0, rep_hyg['findings'])

        # 4) cmd_batch: OS 强制只读沙箱遇到外部并发改动记录 external_concurrent_changes，不误判 scope_violation
        spec_file = self.root / 'v204_batch_spec.json'
        spec_file.write_text(json.dumps([
            {'stream_id': 'R1', 'wave': 1, 'kind': 'review', 'engine': 'codex:review',
             'target': str(proj), 'subtask': '只读核查'},
        ], ensure_ascii=False), encoding='utf-8')

        def fake_invoke(*a, **kw):
            receipt = kw.get('receipt')
            if isinstance(receipt, dict):
                receipt['os_readonly_enforced'] = True
                receipt['launch_attempted'] = True
            # 模拟外部进程并发写入工作区
            (proj / 'external_edit.tmp').write_text('concurrent', encoding='utf-8')
            return {'summary': 'ok', 'content': 'done', 'evidence': [], 'findings': []}

        backends = self.four_backends()
        with patch.object(harness, '_args_backends', return_value=backends), \
             patch.object(harness, 'invoke_engine', side_effect=fake_invoke):
            rc_b, rep_b = self.capture(
                harness.cmd_batch,
                self.args('batch', '--spec-file', str(spec_file), '--tier', 'high'),
            )
        self.assertEqual(rc_b, 0, rep_b)
        st0 = rep_b['streams'][0]
        self.assertEqual(st0['status'], 'ok')
        self.assertEqual(st0['scope_violations'], [])
        self.assertEqual(st0['scope_check'], 'external_concurrent_drift')
        self.assertIn('external_edit.tmp', st0.get('external_concurrent_changes', []))

    def test_v204_codex_adversarial_harness_hardening(self):
        proj = self.root / 'proj_v204_adv'
        (proj / 'src').mkdir(parents=True)
        (proj / 'manuscript.md').write_text('root\nline2\n', encoding='utf-8')
        (proj / 'src' / 'manuscript.md').write_text('src\nline2\n', encoding='utf-8')
        (proj / 'diagram.svg').write_text('<svg/>', encoding='utf-8')

        # 1) S2-2: _unescape_js_literal 单遍解码不破坏 Windows 路径 \\n；混合退出码判错；非工具字面量不计入读文件
        self.assertEqual(harness._unescape_js_literal(r'C:\\notes\\a.py'), r'C:\notes\a.py')
        self.assertTrue(harness._output_block_has_error({
            'output': [{'type': 'input_text', 'text': '{"exit_code":0}\n{"exit_code":1}'}],
        }))
        self.assertEqual(harness._extract_js_exec_read_paths('const note = "cat src/manuscript.md";'), [])

        # 2) S2-3: _citation_audit 对磁盘存在的未读文件绝不误别名；../ 绝不误剥成子路径
        cit_real = harness._citation_audit(
            {'content': '参见 `manuscript.md#L1-L2` 与 `../manuscript.md:1`', 'evidence': [], 'findings': []},
            proj,
            reads=['src/manuscript.md'],
            inlined=[],
        )
        self.assertIn('manuscript.md', cit_real['unopened_cited_files'])
        self.assertIn('../manuscript.md', cit_real['missing_files'])

        # 3) S2-4 & S2-5: --output 禁止覆盖目标文档；--hygiene-only 拒绝对 .svg 空跑通过且拒绝与 --base-ref 混用
        with self.assertRaises(harness.HarnessError):
            harness.cmd_check_grounding(self.args(
                'check-grounding', '--target', str(proj / 'manuscript.md'), '--root', str(proj),
                '--output', str(proj / 'manuscript.md'),
            ))
        with self.assertRaises(harness.HarnessError):
            harness.audit_doc_grounding(proj / 'manuscript.md', proj, base_ref='HEAD', hygiene_only=True)
        rep_svg = harness.audit_doc_grounding(proj / 'diagram.svg', proj, hygiene_only=True)
        self.assertFalse(rep_svg['ok'])
        self.assertEqual(rep_svg['checked_files'], [])
        self.assertEqual(rep_svg['findings'][0]['category'], 'no_applicable_hygiene_files')

        # 4) S2-7: 同波次流 A 擅写兄弟流 B 的 write_scope 时（B 未改该文件），A 须报 scope_violation
        (proj / 'a.txt').write_text('a0', encoding='utf-8')
        (proj / 'b.txt').write_text('b0', encoding='utf-8')
        spec_peer = self.root / 'v204_peer_spec.json'
        spec_peer.write_text(json.dumps([
            {'stream_id': 'A', 'wave': 1, 'kind': 'code', 'engine': 'codex:code',
             'target': str(proj), 'write_scope': ['a.txt'], 'subtask': '改 a'},
            {'stream_id': 'B', 'wave': 1, 'kind': 'code', 'engine': 'claude:code',
             'target': str(proj), 'write_scope': ['b.txt'], 'subtask': '改 b'},
        ], ensure_ascii=False), encoding='utf-8')

        b_done = threading.Event()

        def fake_peer_invoke(spec, *a, **kw):
            rec = kw.get('receipt')
            if isinstance(rec, dict):
                rec['launch_attempted'] = True
            if spec.startswith('claude'):
                b_done.set()
                return {'summary': 'ok', 'content': 'done', 'evidence': [], 'unverified': []}
            # 等待兄弟流 B 完成快照退出（changed_files=[]）后，流 A 才越界篡改 B 的 write_scope
            b_done.wait(timeout=5.0)
            time.sleep(0.15)
            (proj / 'b.txt').write_text('hijacked-by-A', encoding='utf-8')
            return {'summary': 'ok', 'content': 'done', 'evidence': [], 'unverified': []}

        with patch.object(harness, '_args_backends', return_value=self.four_backends()), \
             patch.object(harness, 'invoke_engine', side_effect=fake_peer_invoke):
            rc_p, rep_p = self.capture(harness.cmd_batch, self.args('batch', '--spec-file', str(spec_peer)))
        self.assertNotEqual(rc_p, 0)
        by_id = {s['stream_id']: s for s in rep_p['streams']}
        self.assertEqual(by_id['A']['status'], 'scope_violation')
        self.assertIn('b.txt', by_id['A']['scope_violations'])

    def test_v205_cmd_batch_staggers_concurrent_agy_wave_launches(self):
        """v2.0.5: 同波次多个 agy 流启动按 _AGY_BATCH_LAUNCH_STAGGER_SEC 轻微错峰，codex 与 dry_run 不受影响。"""
        proj = self.root / 'v205_stagger_proj'
        proj.mkdir()
        (proj / 'a.txt').write_text('ok', encoding='utf-8')
        spec_agy = self.root / 'v205_agy_wave.json'
        spec_agy.write_text(json.dumps([
            {'stream_id': 'S1', 'wave': 1, 'kind': 'review', 'engine': 'agy:review', 'target': str(proj), 'subtask': '审 1'},
            {'stream_id': 'S2', 'wave': 1, 'kind': 'review', 'engine': 'agy:review', 'target': str(proj), 'subtask': '审 2'},
            {'stream_id': 'S3', 'wave': 1, 'kind': 'review', 'engine': 'codex:review', 'target': str(proj), 'subtask': '审 3'},
            {'stream_id': 'S4', 'wave': 1, 'kind': 'review', 'engine': 'codex:review', 'target': str(proj), 'subtask': '审 4'},
        ], ensure_ascii=False), encoding='utf-8')

        sleep_calls = []

        def fake_invoke(spec, *a, **kw):
            rec = kw.get('receipt')
            if isinstance(rec, dict):
                rec['launch_attempted'] = True
            return {'summary': 'ok', 'content': 'ok', 'evidence': [], 'unverified': []}

        with patch.object(harness, '_args_backends', return_value=self.four_backends()), \
             patch.object(harness, 'invoke_engine', side_effect=fake_invoke), \
             patch.object(harness, '_AGY_BATCH_LAUNCH_STAGGER_SEC', 0.25), \
             patch.object(harness.time, 'sleep', side_effect=lambda sec: sleep_calls.append(sec)):
            rc, rep = self.capture(harness.cmd_batch, self.args('batch', '--spec-file', str(spec_agy)))
            self.assertEqual(rc, 0)
            self.assertEqual(rep['status'], 'ok')
            # 两个同波次 agy 流应触发 1 次约 0.25s 错峰等待，codex 流不触发
            self.assertEqual(len(sleep_calls), 1)
            self.assertGreater(sleep_calls[0], 0.0)
            self.assertLessEqual(sleep_calls[0], 0.30)

            sleep_calls.clear()
            rc_dry, _ = self.capture(harness.cmd_batch, self.args('batch', '--spec-file', str(spec_agy), '--dry-run'))
            self.assertEqual(rc_dry, 0)
            self.assertEqual(sleep_calls, [])

    def test_axiom_gate_grounding_script_token_guard(self):
        """Phase 1A: check-grounding --strict 须守卫 axiom_gate.py 与 $AXIOM_GATE 的命令行参数。"""
        proj = self.root / 'proj_axiom_grounding'
        proj.mkdir()
        (proj / 'axiom_gate.py').write_text(
            'import argparse\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--profile", default="core")\n'
            'p.add_argument("--run-lean", action="store_true")\n',
            encoding='utf-8',
        )
        doc_ok = proj / 'guide_ok.md'
        doc_ok.write_text(
            '# 用法\n\n运行 `python -B -X utf8 "$AXIOM_GATE" --profile core --run-lean`。\n',
            encoding='utf-8',
        )
        rc_ok, rep_ok = self.capture(
            harness.cmd_check_grounding,
            self.args('check-grounding', '--target', str(doc_ok), '--root', str(proj), '--strict'),
        )
        self.assertEqual(rc_ok, 0, rep_ok.get('findings'))

        doc_bad = proj / 'guide_bad.md'
        doc_bad.write_text(
            '# 用法\n\n运行 `python -B -X utf8 "$AXIOM_GATE" --bogus-axiom-flag`。\n',
            encoding='utf-8',
        )
        rc_bad, rep_bad = self.capture(
            harness.cmd_check_grounding,
            self.args('check-grounding', '--target', str(doc_bad), '--root', str(proj), '--strict'),
        )
        self.assertEqual(rc_bad, harness.EXIT_CODES['verification_failed'])
        bad_tokens = {f['token'] for f in rep_bad['findings'] if f['category'] == 'ungrounded_identifier'}
        self.assertIn('--bogus-axiom-flag', bad_tokens)


if __name__ == '__main__':
    unittest.main(verbosity=2)
