# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""
test_e2e_integration.py - 全系统端到端集成验证、隐私脱敏审计与沙箱安装测试套件

覆盖验证：
1. 插件清单、规则 (rules/AGENTS.md)、钩子编排 (hooks.json) 与只读子代理静态合规校验；
2. 仓库文本中的主目录、硬件型号和凭据字面量模式扫描；
3. 真实 CLI 子进程级联管道联调（UTF-8 中文符号无损传输、PreInvocation 增量压缩注入、PreToolUse 门禁、Stop 门禁）；
4. install.py 镜像同步安装器沙箱测试（自动清除旧版脏文件与废弃 *_stop_gate.json、保护有效原话归档、零 __pycache__ 污染）。
"""

import ast
import contextlib
import datetime
import fnmatch
import getpass
import io
import json
import importlib.util
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
import unicodedata
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "template" / "user-guard"
def _resolve_active_plugin_dir(env: dict[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    raw = source.get("USER_GUARD_INSTALLED_DIR", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            pass
    return TEMPLATE_DIR


ACTIVE_PLUGIN_DIR = _resolve_active_plugin_dir()
INSTALLER_SPEC = importlib.util.spec_from_file_location("user_guard_installer", REPO_ROOT / "install.py")
INSTALLER = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(INSTALLER)


ALLOWED_PLACEHOLDERS = {
    "...", "<user>", "user", "username", "yourname", "example", "alice", "bob", "charlie",
    "public", "default", "test", "mock", "fixture", "guest", "sample", "runner", "developer",
}
HARDCODED_HOME_PATTERNS = (
    re.compile(r"(?:[A-Za-z]:|/mnt/[A-Za-z])[/\\]{1,2}users[/\\]{1,2}([^/\\\'\"\s`<>]+)", re.I),
    re.compile(r"[/\\]{1,2}(?:home|users)[/\\]{1,2}([^/\\\'\"\s`<>]+)", re.I),
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16}|hf_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"\b(?:access_token|refresh_token|id_token|api[_-]?key|client_secret)['\"]?\s*[:=]\s*['\"][A-Za-z0-9_.-]{24,}['\"]", re.I),
)
HARDWARE_PATTERNS = (
    re.compile(r"\b(?:NVIDIA\s+)?(?:GeForce\s+)?(?:RTX|GTX)\s+(?:PRO\s+)?[A-Z]?\d{3,4}\s*(?:Ti|Super|Ada)?\b", re.I),
    re.compile(r"\b(?:NVIDIA\s+)?(?:[AHV](?:100|200)|L40[S])\b", re.I),
    re.compile(r"\b(?:Radeon\s+)?RX\s+[5679]\d{3}\s*(?:XT|XTX)?\b", re.I),
    re.compile(r"\b(?:HP\s+)?OMEN\s+\d{2}[A-Za-z]\b", re.I),
    re.compile(r"\b(?:Intel\s+)?(?:Core\s+)?i[3579]-\d{4,5}[A-Za-z]{0,3}\b", re.I),
    re.compile(r"\b(?:Intel\s+)?Core\s+Ultra\s+[3579]\s+\d{3}[A-Za-z]{0,3}\b", re.I),
    re.compile(r"\b(?:Intel\s+)?Xeon(?:\s+(?:Platinum|Gold|Silver|Bronze|Max|W|D|E))?\s+(?:[A-Z]\d[- ]?)?\d{3,5}[A-Za-z+]*\b", re.I),
    re.compile(r"\b(?:AMD\s+)?(?:Ryzen(?:\s+Threadripper)?|Threadripper|EPYC)\s+(?:(?:[3579]|PRO)\s+)*[A-Za-z]?\d{3,5}[A-Za-z0-9]*\b", re.I),
    re.compile(r"\b(?:Apple\s+)?M[1-4]\s+(?:Pro|Max|Ultra)\b", re.I),
)
UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
POSIX_DEVICE_PATTERN = re.compile("/de" + r"v/(?:null|zero|full|random|urandom|tty|stdin|stdout|stderr|fd|shm|pts)\b", re.I)
SEASONAL_MODEL_PATTERN = re.compile(
    r"\b(?:astra|sol|terra|carbon|neon|argon|hydrogen|luna|k3\.1-pro|deepseek-v4|deepseek-flash)\b",
    re.I,
)
GIT_ENV_KEYS = frozenset({
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
    "GIT_PREFIX",
})
PROXY_ENV_KEYS = frozenset({
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
})
USER_GUARD_INSTALL_ENV_KEYS = frozenset({
    "USER_GUARD_INSTALLED_DIR",
    "USER_GUARD_INSTALLED_CODEX_DIR",
    "USER_GUARD_INSTALLED_CLAUDE_DIR",
    "USER_GUARD_INSTALLED_DSH_DIR",
})
STRIPPED_HOST_ENV_PREFIXES = (
    "CAE_",
    "AGY_",
    "ANTIGRAVITY_",
    "CODEX_",
    "CLAUDE",
    "ANTHROPIC_",
    "DSH_",
    "XDG_",
)
STRIPPED_HOST_ENV_KEYS = (
    frozenset({
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "PLUGIN_DATA",
        "PYTHONPATH",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "CLAUDECODE",
        "DSH_HOME",
        "USER_GUARD_STATE_DIR",
        "USER_GUARD_LOGS_DIR",
        "USER_GUARD_DISABLE_TICKET_GATE",
        "USER_GUARD_ENFORCE_TICKET_GATE",
    })
    | GIT_ENV_KEYS
    | PROXY_ENV_KEYS
)


def _should_strip_host_env(key: str, value: str | None = None) -> bool:
    upper_key = key.upper()
    if key in STRIPPED_HOST_ENV_KEYS or upper_key in STRIPPED_HOST_ENV_KEYS:
        return True
    if upper_key.startswith(STRIPPED_HOST_ENV_PREFIXES):
        return True
    if upper_key.startswith("USER_GUARD_"):
        if upper_key not in USER_GUARD_INSTALL_ENV_KEYS:
            return True
        if value is not None:
            stripped = value.strip()
            if not stripped:
                return True
            try:
                if not Path(stripped).expanduser().is_dir():
                    return True
            except OSError:
                return True
        return False
    return False


def _without_git_env(env: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if k.upper() not in GIT_ENV_KEYS}
HTML_ANCHOR_ATTR_PATTERN = re.compile('<a\\b[^>]*\\b(?:id|name)=["\']([^"\']+)["\']', re.I)


STATIC_BENIGN_REPO_TOKENS = frozenset({
    "admin", "administrator", "agent", "antigravity", "app", "arch", "audit", "azure",
    "bash", "build", "builder", "cae", "cae-harness", "centos", "ci", "claude", "cli",
    "client", "cloud", "code", "coder", "codex", "compute", "config", "container", "core",
    "cpu", "credentials", "data", "debian", "default", "desktop", "dev", "devel",
    "developer", "docker", "dsh", "ec2", "eng", "env", "evolve", "fedora", "fleet",
    "fleet-evolve", "git", "github", "gitlab", "gnu", "gpu", "guard", "guest", "harness",
    "home", "host", "hpc", "kali", "kernel", "kube", "lab", "laptop", "latest", "linux",
    "local", "localhost", "mac", "macbook", "main", "master", "nixos", "node", "office",
    "opt", "pc", "persist", "persist-credentials", "podman", "pro", "prod", "python",
    "readonly", "readonly-auditor", "readonly-collaborator", "readonly-reader", "redhat",
    "remote", "repo", "research", "rocky", "root", "router", "run", "runner", "sandbox",
    "science", "server", "shell", "sim", "solver", "ssh", "staff", "staging", "station",
    "std", "student", "sudo", "sys", "system", "team", "temp", "test", "tester", "tmp",
    "ubuntu", "ubuntu-latest", "unix", "user", "user-guard", "users", "usr", "vagrant",
    "vm", "win", "windows", "windows-latest", "work", "worker", "workspace",
    "workspace-write", "workstation", "write", "wsl", "wsl2",
})


def _collect_repo_benign_tokens(repo_root: Path) -> set[str]:
    """返回静态受控的公共环境/协议词表，不动态读取待扫源码以杜绝泄露主机名或用户名自豁免。"""
    _ = repo_root
    return set(STATIC_BENIGN_REPO_TOKENS)


def _extract_md_heading_anchors(markdown_text: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    fence = ""
    for line in markdown_text.splitlines():
        m_fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if m_fence:
            marker = m_fence.group(1)
            if not fence:
                if marker[0] not in line[m_fence.end():]:
                    fence = marker
                continue
            if marker[0] == fence[0] and len(marker) >= len(fence) and not line[m_fence.end():].strip():
                fence = ""
            continue
        if fence:
            continue
        for attr_id in HTML_ANCHOR_ATTR_PATTERN.findall(line):
            if attr_id.strip():
                anchors.add(urllib.parse.unquote(attr_id.strip()).lower())
        m_head = re.match(r"^ {0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$", line)
        if not m_head:
            continue
        heading = m_head.group(1)
        heading = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", heading)
        heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
        heading = heading.replace("`", "").replace("*", "").replace("~", "")
        slug_chars: list[str] = []
        for ch in heading.strip().lower():
            if ch in (" ", "\t"):
                slug_chars.append("-")
            elif ch in ("-", "_") or unicodedata.category(ch)[0] in ("L", "N", "M"):
                slug_chars.append(ch)
        base_slug = "".join(slug_chars)
        if not base_slug:
            continue
        seen = counts.get(base_slug, 0)
        anchors.add(base_slug if seen == 0 else f"{base_slug}-{seen}")
        counts[base_slug] = seen + 1
    return anchors


def _path_spelling_variants(paths):
    variants = set()
    for path in paths:
        posix = str(path).replace("\\", "/")
        spellings = {posix, posix.replace("/", "\\")}
        if re.match(r"^[A-Za-z]:/", posix):
            spellings.add("/mnt/" + posix[0].lower() + posix[2:])
        match = re.match(r"^/mnt/([A-Za-z])/(.*)$", posix)
        if match:
            windows = match[1] + ":/" + match[2]
            spellings.update((windows, windows.replace("/", "\\")))
        for spelling in spellings:
            variants.add(spelling.casefold())
            variants.add(spelling.replace("\\", "\\\\").casefold())
    return variants


def _sensitive_findings(content, home_variants=(), user_name="", hostname=""):
    """纯函数：扫描所有文本，测试夹具与规则定义使用同一套判据。"""
    findings = []
    low = content.casefold()
    if any(len(path) >= 6 and path.casefold() in low for path in home_variants):
        findings.append("本机路径")
    token_text = POSIX_DEVICE_PATTERN.sub("", content)
    for label, value in (("用户名", user_name), ("主机名", hostname)):
        if (len(value) >= 3
                and value.casefold() not in ALLOWED_PLACEHOLDERS | {"localhost", "root", "ubuntu", "docker"}
                and re.search(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", token_text, re.I)):
            findings.append(label)
    regex_spans = []
    if "re." in content:
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            tree = None
        lines = content.splitlines(keepends=True)
        def offset(line, column):
            return sum(map(len, lines[:line - 1])) + len(lines[line - 1].encode("utf-8")[:column].decode("utf-8"))
        for node in ast.walk(tree) if tree is not None else ():
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
                    and node.func.attr in ("compile", "search", "match", "fullmatch", "split", "findall", "finditer", "sub", "subn") and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                literal = node.args[0]
                regex_spans.append((offset(literal.lineno, literal.col_offset), offset(literal.end_lineno, literal.end_col_offset)))
    for pattern in HARDCODED_HOME_PATTERNS:
        for match in pattern.finditer(content):
            captured = match[1].strip().casefold()
            if any(start <= match.start() and match.end() <= end for start, end in regex_spans):
                continue  # 仅豁免正则模式字面量中的匹配区间。
            if captured and captured not in ALLOWED_PLACEHOLDERS and not captured.startswith("$"):
                findings.append("未脱敏主目录")
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        findings.append("凭据")
    if any(pattern.search(content) for pattern in HARDWARE_PATTERNS):
        findings.append("硬件型号")
    if UUID_PATTERN.search(content):
        findings.append("会话标识")
    for match in re.finditer(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", content):
        if match[1].lower() not in ("example.com", "example.org", "example.net", "example.invalid"):
            findings.append("邮箱")
    if re.search(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
        r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2})\b"
        r"|(?i:\b(?:fd[0-9a-f]{2}|fe80):[0-9a-f:]{2,}\b)",
        content,
    ):
        findings.append("内网地址")
    return findings


def _build_subprocess_cmd(hook_cmd: str) -> list:
    """将 hooks.json 中的命令安全替换为当前测试 Python 解释器执行，跨 Windows/Linux 通用"""
    tokens = [t.strip('"') for t in shlex.split(hook_cmd, posix=(os.name != "nt"))]
    # 找到第一个 .py 脚本位置，保留 -X utf8 -I -B 及脚本参数
    script_idx = next((i for i, t in enumerate(tokens) if t.endswith(".py")), 1)
    return [sys.executable, "-X", "utf8", "-I", "-B"] + tokens[script_idx:]


def _create_directory_link(link: Path, target: Path) -> None:
    """Windows 无 symlink 权限时使用同一临时目录内的目录联接。"""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        env = os.environ.copy()
        env["USER_GUARD_TEST_LINK"] = str(link)
        env["USER_GUARD_TEST_TARGET"] = str(target)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction "
             "-Path $env:USER_GUARD_TEST_LINK -Target $env:USER_GUARD_TEST_TARGET | Out-Null"],
            env=env, capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            raise OSError("当前系统无法建立测试目录链接或联接")


def _system_temp_is_usable() -> bool:
    # 仓库内临时目录需要复制安装器源码，避免安装目标与源码重叠；含祖先软链的 TMPDIR 亦走仓内真实路径回退。
    if REPO_ROOT == Path(tempfile.gettempdir()).resolve() or REPO_ROOT in Path(tempfile.gettempdir()).resolve().parents:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="user-guard-probe-") as tmp:
            probe = Path(tmp) / "probe.tmp"
            target = Path(tmp) / "probe.ok"
            probe.write_bytes(b"ok")
            INSTALLER._validate_path(probe, directory=False)
            os.replace(probe, target)
            return target.read_bytes() == b"ok"
    except (OSError, ValueError):
        return False


class TestEndToEndIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        env_patch = mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"})
        env_patch.start()
        cls.addClassCleanup(env_patch.stop)
        if _system_temp_is_usable():
            cls.installer_root = REPO_ROOT
            return
        # 受限沙箱（如禁止写入系统 %TEMP% 或系统临时目录含祖先链接）下回退至 .pytest_cache 内真实路径运行。
        fallback_base = (REPO_ROOT / ".pytest_cache" / "e2e_sandbox").resolve()
        fallback_base.mkdir(parents=True, exist_ok=True)
        temp_patch = mock.patch.object(tempfile, "tempdir", str(fallback_base))
        temp_patch.start()
        cls.addClassCleanup(temp_patch.stop)
        sandbox_env = mock.patch.dict(os.environ, {
            "TEMP": str(fallback_base), "TMP": str(fallback_base), "TMPDIR": str(fallback_base),
        })
        sandbox_env.start()
        cls.addClassCleanup(sandbox_env.stop)
        sandbox = tempfile.TemporaryDirectory(prefix="e2e-source-", dir=fallback_base)
        cls.addClassCleanup(sandbox.cleanup)
        cls.installer_root = Path(sandbox.name)
        for name in ("install.py", "install.ps1", "install.sh"):
            shutil.copy2(REPO_ROOT / name, cls.installer_root / name)
        shutil.copytree(REPO_ROOT / "template", cls.installer_root / "template",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        (cls.installer_root / "tests").mkdir()
        installer_patch = mock.patch.object(INSTALLER, "__file__", str(cls.installer_root / "install.py"))
        installer_patch.start()
        cls.addClassCleanup(installer_patch.stop)

    def setUp(self) -> None:
        clean_env = {
            k: v for k, v in os.environ.items()
            if not _should_strip_host_env(k, v)
        }
        self._env_patch = mock.patch.dict(os.environ, clean_env, clear=True)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()

    def _managed_repo_files(self, pattern=None):
        if (REPO_ROOT / ".git").exists():
            cmd = ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
            if pattern:
                cmd.extend(["--", pattern])
            proc = subprocess.run(
                cmd, cwd=REPO_ROOT, env=_without_git_env(), capture_output=True, timeout=15
            )
            if proc.returncode == 0:
                return [item for item in proc.stdout.decode("utf-8").split("\0") if item]
        ignored = {".git", ".pytest_cache", "__pycache__", ".venv", "venv"}
        pat = pattern.replace("\\", "/").removeprefix("./").rstrip("/") if pattern else None
        if pat in ("", "."):
            pat = None
        has_glob = bool(pat and any(ch in pat for ch in ("*", "?", "[")))
        matched: list[str] = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT)
            if any(part in ignored for part in rel.parts):
                continue
            rel_posix = rel.as_posix()
            if pat is None:
                matched.append(rel_posix)
            elif has_glob:
                if fnmatch.fnmatch(rel_posix, pat) or ("/" not in pat and fnmatch.fnmatch(path.name, pat)):
                    matched.append(rel_posix)
            elif rel_posix == pat or rel_posix.startswith(pat + "/"):
                matched.append(rel_posix)
        return sorted(matched)

    def test_markdown_tables_never_exceed_five_columns(self):
        managed = self._managed_repo_files("*.md")
        self.assertTrue(any(managed))
        for name in filter(None, managed):
            fence = ""
            for number, line in enumerate((REPO_ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
                match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
                if match:
                    marker = match[1]
                    if not fence:
                        fence = marker
                    elif marker[0] == fence[0] and len(marker) >= len(fence) and not line[match.end():].strip():
                        fence = ""
                    continue
                if fence or "|" not in line:
                    continue
                cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
                if len(cells) > 1:
                    self.assertLessEqual(len(cells), 5, f"{name}:{number} {line}")

    def test_rules_preserve_arbitrary_heading_after_standard_environment(self):
        template = TEMPLATE_DIR / "rules/AGENTS.md"
        expected = template.read_text(encoding="utf-8").rstrip()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rules.txt"
            for newline in ("\n", "\r\n"):
                with self.subTest(newline=repr(newline)):
                    tail = newline.join(["## 本机特定约束", "R10. 本机构建。", "R11. 本机调试。",
                                         "## 附加约束", "完整保留后续内容。", ""])
                    original = ("# 旧规则" + newline + "## 工具与运行环境  " + newline
                                + "R10. 旧标准规则。" + newline + tail).encode("utf-8")
                    target.write_bytes(original)
                    with contextlib.redirect_stdout(io.StringIO()):
                        INSTALLER._sync_global_rules(template, target, "fixture", dry_run=True)
                    self.assertEqual(target.read_bytes(), original)
                    with contextlib.redirect_stdout(io.StringIO()):
                        INSTALLER._sync_global_rules(template, target, "fixture", dry_run=False)
                    local = tail.replace("R10.", "L1.").replace("R11.", "L2.")
                    merged = (expected.replace("\n", newline) + newline * 2 + local).encode("utf-8")
                    self.assertEqual(target.read_bytes(), merged)
                    self.assertTrue(INSTALLER._rules_equal(target.read_bytes(), template.read_bytes()))
                    before = target.stat().st_mtime_ns
                    with contextlib.redirect_stdout(io.StringIO()):
                        INSTALLER._sync_global_rules(template, target, "fixture", dry_run=False)
                    self.assertEqual(target.read_bytes(), merged)
                    self.assertEqual(target.stat().st_mtime_ns, before)

    def test_01_plugin_manifest_rules_and_agents_compliance(self) -> None:
        """验证插件结构严格符合 Antigravity 官方 Native Plugin 规范"""
        plugin_json = json.loads((ACTIVE_PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
        allowed_keys = {
            "$schema",
            "name",
            "description",
            "logo",
            "license",
            "suggestedPrompts",
            "displayName",
            "version",
            "author",
            "homepage",
            "repository",
            "disabled",
            "keywords",
            "skills",
            "extensions",
        }
        self.assertEqual(plugin_json.get("name"), "user-guard")
        self.assertEqual(plugin_json.get("$schema"), "https://antigravity.google/schemas/v1/plugin.json")
        self.assertEqual(plugin_json.get("license"), "Apache-2.0")
        notice = REPO_ROOT / "NOTICE"
        self.assertTrue(notice.is_file())
        notice_text = notice.read_text(encoding="utf-8")
        self.assertIn("Antigravity_Codex_ClaudeCode_DeepSeekHarness", notice_text)
        self.assertIn("Xinyu Li (Xinyu-Li-1997)", notice_text)
        self.assertEqual(plugin_json.get("logo"), "assets/logo.svg")
        self.assertTrue((ACTIVE_PLUGIN_DIR / "assets" / "logo.svg").is_file(), "plugin.json 声明的 logo.svg 必须存在")
        self.assertTrue(set(plugin_json.keys()).issubset(allowed_keys), "plugin.json 不可包含未定义顶层字段")

        hooks_json = json.loads((ACTIVE_PLUGIN_DIR / "hooks.json").read_text(encoding="utf-8"))
        lifecycle = hooks_json.get("user-guard-lifecycle", {})
        self.assertIn("SessionStart", lifecycle)
        self.assertIn("PreInvocation", lifecycle)
        self.assertIn("PreToolUse", lifecycle)
        self.assertIn("Stop", lifecycle)
        self.assertEqual(len(lifecycle["Stop"]), 1, "Stop 阶段仅挂载防刷屏守卫，不挂载生硬日常拦截")

        rules_file = ACTIVE_PLUGIN_DIR / "rules" / "AGENTS.md"
        self.assertTrue(rules_file.exists(), "应使用官方推荐的 rules/AGENTS.md")
        rules_text = rules_file.read_text(encoding="utf-8")
        self.assertFalse(rules_text.startswith("---"), "rules/AGENTS.md 按官方规范应为无 frontmatter 的纯 Markdown")
        for r_code in ("R0.", "R1.", "R2.", "R3.", "R3.1.", "R3.6.", "R4.", "R5.", "R6.", "R7.", "R8.", "R9.", "R10.", "R11."):
            self.assertIn(r_code, rules_text)
        self.assertIn("CAE", rules_text)
        self.assertLess(
            len(rules_file.read_bytes()),
            16000,
            "rules/AGENTS.md UTF-8 字节数须显著低于 Antigravity 24,000 字节规则截断上限",
        )

        for agent_name in ("readonly-auditor.md", "readonly-reader.md", "readonly-collaborator.md"):
            agent_path = ACTIVE_PLUGIN_DIR / "agents" / agent_name
            self.assertTrue(agent_path.exists())
            agent_text = agent_path.read_text(encoding="utf-8")
            self.assertIn(
                "tools: [read_file, search_web, send_message, finish]",
                agent_text,
                "只读代理须精确限定为只读工具、父代理回传工具 send_message 及 agy --json-schema 收尾工具 finish",
            )
            self.assertIn("subagent: true", agent_text)
            self.assertIn("mainAgent: true", agent_text, "agy CLI --agent 需要 mainAgent: true 方可识别")
            self.assertNotIn("hidden: true", agent_text, "App v2.32.0 下 hidden: true 会将子代理从 <subagents> 目录隐藏")
            self.assertIn("#L10-L25", agent_text)
            self.assertIsNone(SEASONAL_MODEL_PATTERN.search(agent_text))
            if agent_name == "readonly-collaborator.md":
                self.assertIn("计算与内存开销", agent_text)
                self.assertIn("引用外部或本地文献须注明出处", agent_text)

        skill_md = ACTIVE_PLUGIN_DIR / "skills" / "cae-harness" / "SKILL.md"
        skill_script = ACTIVE_PLUGIN_DIR / "skills" / "cae-harness" / "scripts" / "cae_harness.py"
        self.assertTrue(skill_md.exists(), "应包含渐进式加载按需技能 cae-harness/SKILL.md")
        self.assertTrue(skill_script.exists(), "应包含确定性总控脚本 cae_harness.py")
        skill_text = skill_md.read_text(encoding="utf-8")
        self.assertTrue(skill_text.startswith("---\nname: cae-harness"), "SKILL.md 必须具备合规 YAML frontmatter")
        self.assertIn("scripts/cae_harness.py", skill_text)

        license_file = REPO_ROOT / "LICENSE"
        self.assertTrue(license_file.is_file(), "开源仓库根目录必须包含标准 LICENSE 许可证文件")
        license_text = license_file.read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)

        changelog_file = REPO_ROOT / "CHANGELOG.md"
        self.assertTrue(changelog_file.is_file(), "仓库根目录须维护规范的 CHANGELOG.md 更新日志")
        changelog_text = changelog_file.read_text(encoding="utf-8")
        self.assertIn(f"[{plugin_json.get('version')}]", changelog_text, "CHANGELOG.md 须记录当前 plugin.json 版本号")

        root_agents_md = REPO_ROOT / "AGENTS.md"
        self.assertTrue(root_agents_md.is_file(), "仓库根目录须包含供双端共读自举配置的 AGENTS.md")
        self.assertLess(
            len(root_agents_md.read_bytes()),
            16000,
            "根目录 AGENTS.md UTF-8 字节数须显著低于 Antigravity 24,000 字节规则截断上限",
        )
        root_agents_text = root_agents_md.read_text(encoding="utf-8")
        self.assertIn("--probe-apps", root_agents_text)
        self.assertIn("--app codex", root_agents_text)
        self.assertIn("--app antigravity", root_agents_text)

        codex_tpl = REPO_ROOT / "template" / "codex"
        self.assertTrue((codex_tpl / "hooks.json").is_file())
        expected_codex_tomls = ("readonly-auditor.toml", "readonly-reader.toml", "readonly-collaborator.toml")
        actual_codex_tomls = sorted(p.name for p in (codex_tpl / "agents").glob("*.toml"))
        self.assertEqual(set(actual_codex_tomls), set(expected_codex_tomls))
        expected_codex_features = {
            "request_permissions_tool": False,
            "apps": False,
            "plugins": False,
            "memories": False,
        }
        for toml_name in expected_codex_tomls:
            toml_path = codex_tpl / "agents" / toml_name
            self.assertTrue(toml_path.is_file(), f"缺少 Codex 只读子代理模板: {toml_name}")
            toml_text = toml_path.read_text(encoding="utf-8")
            self.assertIsNone(SEASONAL_MODEL_PATTERN.search(toml_text), f"{toml_name} 严禁硬编码季节性模型名")
            parsed_toml = tomllib.loads(toml_text)
            self.assertEqual(
                set(parsed_toml.keys()),
                {"name", "description", "sandbox_mode", "developer_instructions", "features"},
                f"{toml_name} 顶层标量键必须位于所有 [table] 段之前且集合精确匹配",
            )
            self.assertEqual(parsed_toml["name"], toml_path.stem)
            self.assertTrue(isinstance(parsed_toml["description"], str) and parsed_toml["description"].strip())
            self.assertEqual(parsed_toml["sandbox_mode"], "read-only")
            dev_inst = parsed_toml["developer_instructions"]
            self.assertTrue(isinstance(dev_inst, str) and "严禁修改任何文件" in dev_inst)
            self.assertIn("#L10-L25", dev_inst)
            if toml_name == "readonly-auditor.toml":
                self.assertIn("需要编译、运行算例或执行脚本才能确认的事项统一交回主会话处理。", dev_inst)
                self.assertNotIn("需要执行命令才能确认的事项", dev_inst)
            if toml_name == "readonly-collaborator.toml":
                self.assertIn("计算与内存开销", dev_inst)
                self.assertIn("引用外部或本地文献须注明出处", dev_inst)
            self.assertEqual(parsed_toml["features"], expected_codex_features)

        claude_tpl = REPO_ROOT / "template" / "claude"
        claude_hooks_file = claude_tpl / "hooks.json"
        self.assertTrue(claude_hooks_file.is_file(), "缺少 Claude 生命周期钩子模板 template/claude/hooks.json")
        claude_hooks_doc = json.loads(claude_hooks_file.read_text(encoding="utf-8"))
        claude_events = claude_hooks_doc.get("hooks", {})
        self.assertEqual(
            set(claude_events.keys()),
            {"SessionStart", "PostCompact", "UserPromptSubmit", "PreToolUse", "Stop"},
        )
        claude_cmds = [
            handler["command"]
            for groups in claude_events.values()
            for group in groups
            for handler in group.get("hooks", [])
        ]
        for script_name in ("session_context_loader.py", "status_guard.py", "reply_repeat_guard.py", "handoff_gate.py"):
            self.assertTrue(any(script_name in cmd for cmd in claude_cmds), f"Claude hooks.json 缺少 {script_name}")
        for md_name in ("readonly-auditor.md", "readonly-reader.md", "readonly-collaborator.md"):
            claude_agent_path = claude_tpl / "agents" / md_name
            self.assertTrue(claude_agent_path.is_file(), f"缺少 Claude 只读子代理模板: {md_name}")
            claude_agent_text = claude_agent_path.read_text(encoding="utf-8")
            self.assertTrue(claude_agent_text.startswith(f"---\nname: {claude_agent_path.stem}\n"))
            self.assertIn("tools: Read, Grep, Glob", claude_agent_text)
            self.assertIn("model: inherit", claude_agent_text)
            self.assertIn("严禁修改任何文件", claude_agent_text)
            self.assertIn("#L10-L25", claude_agent_text)
            self.assertIsNone(SEASONAL_MODEL_PATTERN.search(claude_agent_text), f"Claude {md_name} 严禁硬编码季节性模型名")
            if md_name == "readonly-collaborator.md":
                self.assertIn("计算与内存开销", claude_agent_text)
                self.assertIn("引用外部或本地文献须注明出处", claude_agent_text)

    def test_02_repository_personal_path_hardware_and_credential_patterns(self) -> None:
        """扫描仓库文本中与个人路径、具体硬件型号和常见凭据匹配的内容。"""
        variant_roots = [Path.home()] + ([REPO_ROOT] if len(REPO_ROOT.parts) > 2 else [])
        home_variants = _path_spelling_variants(variant_roots)
        raw_user_name = getpass.getuser()
        raw_hostname = os.environ.get("COMPUTERNAME") or socket.gethostname()
        code_tokens = _collect_repo_benign_tokens(REPO_ROOT)
        user_name = "" if raw_user_name.casefold() in code_tokens else raw_user_name
        hostname = "" if raw_hostname.casefold() in code_tokens else raw_hostname
        checked_count = 0
        for path in REPO_ROOT.rglob("*"):
            rel_parts = path.relative_to(REPO_ROOT).parts
            if not path.is_file() or any(p in rel_parts for p in (".git", "__pycache__", ".pytest_cache", ".venv", "venv")):
                continue
            rel_name = path.relative_to(REPO_ROOT).as_posix()
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # RFC 3161 时间戳查询与响应是 DER 二进制；按单字节解码后仍继续扫描敏感信息，
                # 其余文件继续要求 UTF-8，避免以放宽编码要求换取通过。
                if path.suffix.lower() not in {".tsq", ".tsr"}:
                    self.fail(f"non-UTF8 file: {rel_name}")
                content = path.read_bytes().decode("latin-1")
            checked_count += 1
            findings = _sensitive_findings(content, home_variants, user_name, hostname)
            self.assertEqual(findings, [], f"{rel_name}: {', '.join(findings)}")
        self.assertGreaterEqual(checked_count, 16)

    def test_sanitization_helpers_positive_and_negative_examples(self):
        private_user = "private" + "person"
        homes = (
            "C:" + "\\Users\\" + private_user,
            "c:/" + "users/" + private_user,
            "/mnt/c/" + "Users/" + private_user,
            "\\\\wsl.localhost\\Ubuntu" + "\\home\\" + private_user,
            "/Users/" + private_user,
            "/home/" + private_user,
        )
        models = (
            "Core " + "i9-" + "14900K", "i5-" + "12400", "Core " + "Ultra " + "9 " + "285K",
            "Xeon " + "Gold " + "6430", "Xeon " + "E5-" + "2690",
            "AMD " + "Ryzen " + "9 " + "9950X", "Threadripper " + "PRO " + "7995WX", "EPYC " + "9654",
            "Apple " + "M4 " + "Max", "RTX " + "5090", "GTX " + "1080", "RTX " + "A6000",
            "A" + "100", "H" + "100", "H" + "200", "V" + "100", "L" + "40S",
            "Radeon RX " + "7900 " + "XTX", "OMEN " + "45L",
        )
        credentials = (
            "sk-" + "a" * 32, "AIza" + "a" * 34, "ghp_" + "a" * 32,
            "github_pat_" + "a" * 42, "AKIA" + "A" * 16,
            "hf_" + "a" * 30, "glpat-" + "a" * 30,
            "-----BEGIN " + "PRIVATE KEY-----",
        ) + tuple('"' + key + '": "' + "a" * 32 + '"' for key in (
            "api_key", "api-key", "access_token", "refresh_token", "id_token", "client_secret"))
        for text in homes + models + credentials:
            with self.subTest(text=text):
                self.assertTrue(_sensitive_findings(text))
        for name in ("private(name)", "private+name", "private{name}", "private[name]", "private[^name]", "private$name", "private.*name"):
            with self.subTest(directory=name):
                self.assertIn("未脱敏主目录", _sensitive_findings("/home/" + name + "/work"))
        regex_literal = 're.compile(r"' + "/home/" + '([^name]+)")'
        self.assertEqual(_sensitive_findings(regex_literal), [])
        substitution = 're.sub(r"' + "/home/" + '([^name]+)", "replacement", value)'
        self.assertEqual(_sensitive_findings(substitution), [])
        private_replacement = 're.sub("pattern", "' + "/home/" + 'private[^name]/work", value)'
        self.assertIn("未脱敏主目录", _sensitive_findings(private_replacement))
        outside_literal = regex_literal + '; path = "' + "/home/" + 'private[^name]/work"'
        self.assertIn("未脱敏主目录", _sensitive_findings(outside_literal))
        self.assertIn("未脱敏主目录", _sensitive_findings('path = "' + "/home/" + 'private[^name]/work"'))
        for text in (
            "/home/alice/work", "C:/Users/bob/work", "/home/charlie/work", "/Users/<user>/work",
            "CPU GPU Intel Core AMD Ryzen Threadripper Xeon EPYC NVIDIA RTX GTX",
            '"api_key": "<placeholder>"', "refresh_token = None", "user@example.com",
            "https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness", "Apache License 2.0",
            HARDCODED_HOME_PATTERNS[0].pattern, HARDCODED_HOME_PATTERNS[1].pattern,
        ):
            with self.subTest(text=text):
                self.assertEqual(_sensitive_findings(text), [])
        for path in _path_spelling_variants(("Q:/Private/Checkout",)):
            self.assertTrue(_sensitive_findings(path.upper(), home_variants=(path,)))
        self.assertTrue(_sensitive_findings(private_user, user_name=private_user.upper()))
        self.assertTrue(_sensitive_findings("owner: " + "d" + "ev", user_name="D" + "EV"))
        self.assertTrue(_sensitive_findings("/home/" + "d" + "ev/work"))
        self.assertEqual(_sensitive_findings("2>/dev/null\ndeveloper guide", user_name="d" + "ev"), [])
        self.assertTrue(_sensitive_findings("private" + "host", hostname="PRIVATE" + "HOST"))
        uuid = "-".join(("a" * 8, "b" * 4, "4" + "c" * 3, "8" + "d" * 3, "e" * 12))
        self.assertTrue(_sensitive_findings(uuid))
        self.assertTrue(_sensitive_findings("private" + "@" + "private" + ".invalid"))
        self.assertTrue(_sensitive_findings("192." + "168." + "42." + "7"))

    def test_three_install_modes_ship_complete_artifacts_and_runnable_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mode in ("codex", "antigravity", "both"):
                with self.subTest(mode=mode):
                    sandbox = root / mode
                    plugin, codex = sandbox / "plugins" / "user-guard", sandbox / "codex"
                    command = [sys.executable, "-B", "-X", "utf8", str(self.installer_root / "install.py"),
                               "--app", mode, "--skip-test", "--no-zotero", "--yes"]
                    if mode != "codex":
                        command += ["--target-dir", str(plugin)]
                    if mode != "antigravity":
                        command += ["--codex-home", str(codex)]
                    env = os.environ.copy()
                    for key in ("USER_GUARD_STATE_DIR", "USER_GUARD_LOGS_DIR", "USER_GUARD_INSTALLED_DIR", "USER_GUARD_INSTALLED_CODEX_DIR", "PLUGIN_DATA"):
                        env.pop(key, None)
                    env.update(PYTHONDONTWRITEBYTECODE="1", PLUGIN_DATA=str(sandbox / "data"), CODEX_HOME=str(codex))
                    proc = subprocess.run(command, cwd=REPO_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    harnesses = []
                    if mode != "codex":
                        for relative in ("plugin.json", "assets/logo.svg", "hooks/status_guard.py", "hooks/handoff_gate.py"):
                            self.assertTrue((plugin / relative).is_file(), relative)
                        harnesses.append(plugin / "skills" / "cae-harness" / "scripts" / "cae_harness.py")
                        hooks = json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))["user-guard-lifecycle"]
                        command = _build_subprocess_cmd(hooks["PreToolUse"][0]["hooks"][0]["command"])
                        proc = subprocess.run(command, cwd=plugin, env=env, input=json.dumps({"conversationId": "installed-hook", "toolCall": {"name": "read_file", "args": {}}}), capture_output=True, text=True, encoding="utf-8", timeout=15)
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        self.assertEqual(json.loads(proc.stdout)["decision"], "allow")
                    if mode != "antigravity":
                        for relative in ("hooks.json", "hooks/user_words.py", "hooks/handoff_gate.py", "hooks/session_context_loader.py"):
                            self.assertTrue((codex / relative).is_file(), relative)
                        harnesses.append(codex / "skills" / "cae-harness" / "scripts" / "cae_harness.py")
                        proc = subprocess.run([sys.executable, "-B", "-X", "utf8", str(codex / "hooks" / "handoff_gate.py"), "--client", "codex"], env=env, input=json.dumps({"hook_event_name": "PreToolUse"}), capture_output=True, text=True, encoding="utf-8", timeout=15)
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        self.assertIsInstance(json.loads(proc.stdout), dict)
                    for script in harnesses:
                        self.assertTrue(script.is_file())
                        proc = subprocess.run([sys.executable, "-B", "-X", "utf8", str(script), "--help"], env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        self.assertIn("assess", proc.stdout)

    def test_dsh_installer_guard_cli_checks_all_peer_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            env = {**os.environ, 'CAE_HARNESS_DEPTH': '1', 'PLUGIN_DATA': str(root / 'guard-data')}
            for flags, expected in (
                ('--app dsh --dsh-home inside/dsh', 'allow'),
                ('--app dsh', 'deny'),
                ('--app all --target-dir inside/agy --codex-home inside/codex --claude-home inside/claude --dsh-home /outside/dsh', 'deny'),
            ):
                with self.subTest(flags=flags):
                    payload = {'conversationId': 'dsh-cli-guard', 'cwd': str(root), 'workspacePaths': [str(root)],
                               'toolCall': {'name': 'run_command', 'args': {'CommandLine':
                                   'python -B install.py ' + flags + ' --no-zotero --skip-test'}}}
                    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ACTIVE_PLUGIN_DIR / 'hooks/status_guard.py')],
                                            cwd=root, env=env, input=json.dumps(payload), capture_output=True,
                                            text=True, encoding='utf-8', timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['decision'], expected)

    def test_03_full_cli_subprocess_utf8_pipeline(self) -> None:
        """通过真实操作系统子进程管道运行全部 4 个生命周期事件，验证 UTF-8 稳定性与协同契约"""
        hooks_cfg = json.loads((ACTIVE_PLUGIN_DIR / "hooks.json").read_text(encoding="utf-8"))["user-guard-lifecycle"]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_data = tmp_path / "plugin_data"
            conv_id = "e2e-subprocess-conv"
            tp = tmp_path / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
            tp.parent.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["PLUGIN_DATA"] = str(plugin_data)
            env["PYTHONDONTWRITEBYTECODE"] = "1"

            def run_hook(cmd_str: str, payload: dict) -> dict:
                cmd = _build_subprocess_cmd(cmd_str)
                proc = subprocess.run(
                    cmd,
                    cwd=str(ACTIVE_PLUGIN_DIR),
                    input=json.dumps(payload, ensure_ascii=False),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=env,
                    timeout=15,
                )
                self.assertEqual(proc.returncode, 0, f"Hook 执行失败 stderr={proc.stderr}")
                return json.loads(proc.stdout.strip())

            # 1) 写入首轮原话，未压缩时 PreInvocation 零注入
            tp.write_text(
                json.dumps(
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "content": "<USER_REQUEST>\n【立项要求】保持通量守恒，输出中文技术报告。\n</USER_REQUEST>",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            pre_cmd = hooks_cfg["PreInvocation"][0]["command"]
            out1 = run_hook(pre_cmd, {"conversationId": conv_id, "transcriptPath": str(tp), "hookEventName": "PreInvocation"})
            self.assertEqual(out1["injectSteps"], [])

            # 2) 写入 CHECKPOINT 后，PreInvocation 精确注入 1 次，二次调用 0 注入
            with open(tp, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"step_index": 5, "source": "SYSTEM", "type": "CHECKPOINT"}) + "\n")
            out2 = run_hook(pre_cmd, {"conversationId": conv_id, "transcriptPath": str(tp), "hookEventName": "PreInvocation"})
            self.assertEqual(len(out2["injectSteps"]), 1)
            self.assertIn("【立项要求】保持通量守恒", out2["injectSteps"][0]["systemMessage"]["systemMessage"])

            out3 = run_hook(pre_cmd, {"conversationId": conv_id, "transcriptPath": str(tp), "hookEventName": "PreInvocation"})
            self.assertEqual(out3["injectSteps"], [])

            # 3) PreToolUse status_guard 子进程测试
            ptu_cmd = hooks_cfg["PreToolUse"][0]["hooks"][0]["command"]
            sim_log = tmp_path / "sim.log"
            sim_log.write_text("residual=1e-3\n", encoding="utf-8")
            p_read = {
                "conversationId": conv_id,
                "cwd": str(tmp_path),
                "toolCall": {"name": "read_file", "args": {"Path": str(sim_log), "StartLine": 1, "EndLine": 50}},
            }
            self.assertEqual(run_hook(ptu_cmd, p_read)["decision"], "allow")
            self.assertEqual(run_hook(ptu_cmd, p_read)["decision"], "deny")

            # 3b) PreToolUse 项目目录硬边界子进程测试（越界写入返回 force_ask）
            proj_sub = tmp_path / "workspace_proj"
            proj_sub.mkdir()
            p_ext = {
                "conversationId": conv_id,
                "cwd": str(proj_sub),
                "workspacePaths": [str(proj_sub)],
                "toolCall": {"name": "write_file", "args": {"Path": str(tmp_path / ".codex-worktrees" / "leak.txt")}},
            }
            out_ext = run_hook(ptu_cmd, p_ext)
            self.assertEqual(out_ext["decision"], "force_ask")
            self.assertIn("项目目录外", out_ext["reason"])

            # 4) Stop reply_repeat_guard 子进程日常回复秒放行测试
            stop_cmd = hooks_cfg["Stop"][0]["command"]
            with open(tp, "a", encoding="utf-8", newline="\n") as f:
                f.write(
                    json.dumps({"step_index": 6, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "已完成通量守恒校验。"}, ensure_ascii=False)
                    + "\n"
                )
            out_stop = run_hook(stop_cmd, {"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp)})
            self.assertEqual(out_stop["decision"], "allow")

    def test_04_installer_mirror_sync_cleans_stale_files_without_pycache(self) -> None:
        """验证 install.py 镜像同步自动清除陈旧文件与废弃 *_stop_gate.json，保留原话归档且零 .pyc 残留"""
        with tempfile.TemporaryDirectory() as tmp:
            sandbox_plugin = Path(tmp) / "plugins" / "user-guard"
            # 预置模拟的历史遗留脏文件
            (sandbox_plugin / "rules").mkdir(parents=True)
            (sandbox_plugin / "hooks" / "__pycache__").mkdir(parents=True)
            (sandbox_plugin / "hooks" / "state").mkdir(parents=True)

            stale_rules = sandbox_plugin / "rules" / "global_rules.md"
            stale_mcp = sandbox_plugin / "mcp_config.json"
            stale_test = sandbox_plugin / "hooks" / "test_old.py"
            stale_pyc = sandbox_plugin / "hooks" / "__pycache__" / "old.cpython-313.pyc"
            stale_stop_gate = sandbox_plugin / "hooks" / "state" / "abc_stop_gate.json"
            valid_words_md = sandbox_plugin / "hooks" / "state" / "abc_user_words.md"

            for sf in (stale_rules, stale_mcp, stale_test, stale_pyc, stale_stop_gate):
                sf.write_text("stale", encoding="utf-8")
            valid_words_md.write_text("# 历史有效原话归档", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-X",
                    "utf8",
                    str(self.installer_root / "install.py"),
                    "--target-dir",
                    str(sandbox_plugin),
                    "--skip-test",
                    "--no-zotero",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, f"install.py 失败: {proc.stderr}\n{proc.stdout}")

            # 断言脏文件已全部清除，新文件已就位，历史原话归档完好无损
            self.assertFalse(stale_rules.exists())
            self.assertFalse(stale_mcp.exists())
            self.assertFalse(stale_test.exists())
            self.assertFalse(stale_pyc.exists())
            self.assertFalse(stale_stop_gate.exists())
            self.assertTrue((sandbox_plugin / "rules" / "AGENTS.md").exists())
            self.assertTrue((sandbox_plugin / "README.md").exists())
            self.assertTrue(valid_words_md.exists())
            self.assertEqual(list(sandbox_plugin.rglob("__pycache__")), [])

            # 直接执行安装后生成的平台原生 hooks.json 命令，验证解释器探测真实可用
            installed_hooks = json.loads((sandbox_plugin / "hooks.json").read_text(encoding="utf-8"))
            raw_hook_cmd = installed_hooks["user-guard-lifecycle"]["SessionStart"][0]["command"]
            hook_env = os.environ.copy()
            hook_env["PLUGIN_DATA"] = str(Path(tmp) / "plugin_data")
            hook_proc = subprocess.run(
                shlex.split(raw_hook_cmd, posix=(os.name != "nt")),
                cwd=str(sandbox_plugin),
                input=json.dumps({"conversationId": "installed-cmd-check", "hookEventName": "SessionStart"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=hook_env,
                timeout=15,
            )
            self.assertEqual(hook_proc.returncode, 0, f"原生安装命令执行失败: {hook_proc.stderr}")
            self.assertEqual(json.loads(hook_proc.stdout.strip())["injectSteps"], [])
            first_snapshot = {file.relative_to(sandbox_plugin): (file.read_bytes(), file.stat().st_mtime_ns)
                              for file in sandbox_plugin.rglob("*") if file.is_file()}
            repeated = subprocess.run(proc.args, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual({file.relative_to(sandbox_plugin): (file.read_bytes(), file.stat().st_mtime_ns)
                              for file in sandbox_plugin.rglob("*") if file.is_file()}, first_snapshot)

    def test_05_dry_run_preserves_source_and_all_install_targets(self) -> None:
        """预演保留源树缓存、目标陈旧文件与其他技能，且不创建新目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            shutil.copy2(REPO_ROOT / "install.py", source / "install.py")
            shutil.copy2(REPO_ROOT / "CHANGELOG.md", source / "CHANGELOG.md")
            shutil.copytree(TEMPLATE_DIR, source / "template" / "user-guard")
            source_cache = source / "__pycache__" / "existing.pyc"
            source_cache.parent.mkdir()
            source_cache.write_bytes(b"retain source cache")
            plugin = root / "plugin target"
            plugin.mkdir()
            (plugin / "old.txt").write_text("retain during dry run", encoding="utf-8")
            codex_root = root / "codex"
            skills = codex_root / "skills"
            (skills / ".system").mkdir(parents=True)
            (skills / ".system" / "existing.txt").write_text("retain system skill", encoding="utf-8")

            def snapshot() -> dict:
                return {
                    entry.relative_to(root).as_posix(): entry.read_bytes() if entry.is_file() else None
                    for entry in root.rglob("*")
                }

            before = snapshot()
            result = subprocess.run(
                [sys.executable, "-B", str(source / "install.py"), "--target-dir", str(plugin),
                 "--codex-skills-dir", str(skills), "--dry-run", "--no-zotero"],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(snapshot(), before)

    def test_06_explicit_dual_install_preserves_other_codex_skills(self) -> None:
        """同源安装两个目标，仅镜像 Codex 的 cae-harness 子目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "插件 空间" / "user-guard"
            skills = root / "codex" / "skills"
            peer = skills / ".system" / "keep.txt"
            peer.parent.mkdir(parents=True)
            peer.write_bytes(b"existing system skill")
            stale = skills / "cae-harness" / "old.txt"
            stale.parent.mkdir()
            stale.write_bytes(b"old skill")
            result = subprocess.run(
                [sys.executable, "-B", str(self.installer_root / "install.py"), "--target-dir", str(plugin),
                 "--codex-skills-dir", str(skills), "--skip-test", "--no-zotero"],
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(peer.read_bytes(), b"existing system skill")
            self.assertFalse(stale.exists())
            for rel in (Path("SKILL.md"), Path("scripts") / "cae_harness.py"):
                source_bytes = (TEMPLATE_DIR / "skills" / "cae-harness" / rel).read_bytes()
                self.assertEqual((skills / "cae-harness" / rel).read_bytes(), source_bytes)
                self.assertEqual((plugin / "skills" / "cae-harness" / rel).read_bytes(), source_bytes)

    def test_07_custom_plugin_target_does_not_write_default_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_root = root / "configured-codex"
            codex_root.mkdir()
            marker = codex_root / "config.toml"
            marker.write_bytes(b"retain configuration")
            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_root)
            result = subprocess.run(
                [sys.executable, "-B", str(self.installer_root / "install.py"), "--target-dir",
                 str(root / "plugin"), "--skip-test", "--no-zotero"],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(marker.read_bytes(), b"retain configuration")
            self.assertEqual(sorted(entry.name for entry in codex_root.iterdir()), ["config.toml"])

    def test_08_codex_target_discovery_respects_home_and_switches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_root = root / "user"
            user_root.mkdir()
            custom = root / "configured-codex"
            custom.mkdir()
            with mock.patch.object(INSTALLER.Path, "home", return_value=user_root):
                with mock.patch.dict(os.environ, {"CODEX_HOME": ""}):
                    self.assertIsNone(INSTALLER.codex_skill_target("", False, False))
                    (user_root / ".codex").mkdir()
                    self.assertEqual(INSTALLER.codex_skill_target("", False, False),
                                     user_root / ".codex" / "skills" / "cae-harness")
                with mock.patch.dict(os.environ, {"CODEX_HOME": str(custom)}):
                    self.assertEqual(INSTALLER.codex_skill_target("", False, False),
                                     custom / "skills" / "cae-harness")
                    self.assertIsNone(INSTALLER.codex_skill_target("", True, False))
                    self.assertIsNone(INSTALLER.codex_skill_target("", False, True))

    def test_09_rejects_source_overlap_and_linked_install_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repository"
            repo.mkdir()
            target = root / "custom installation"
            target.mkdir()
            external_dir = root / "external"
            external_dir.mkdir()
            external = external_dir / "keep.txt"
            external.write_bytes(b"outside target")
            for invalid in (repo, repo / "nested", repo.parent, Path.home()):
                with self.subTest(target=invalid):
                    with self.assertRaises(ValueError):
                        INSTALLER.validate_install_target(invalid, repo)
            self.assertEqual(INSTALLER.validate_install_target(target, repo), target.resolve())
            link = target / "linked-directory"
            try:
                _create_directory_link(link, external_dir)
            except OSError:
                self.skipTest("当前系统没有创建符号链接的权限")
            with self.assertRaises(ValueError):
                INSTALLER.validate_install_target(target, repo)
            self.assertEqual(external.read_bytes(), b"outside target")

    def test_10_rejects_ancestor_links_into_production_install_targets(self) -> None:
        """临时目标的祖先链接不能把隔离安装重定向到默认生产目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repository"
            repo.mkdir()
            user_root = root / "fake-user"
            codex_root = root / "configured-codex"
            production_targets = [
                user_root / ".gemini" / "config" / "plugins" / "user-guard",
                user_root / ".codex" / "skills" / "cae-harness",
                codex_root / "skills" / "cae-harness",
            ]
            with mock.patch.object(INSTALLER.Path, "home", return_value=user_root):
                with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_root)}):
                    for index, production in enumerate(production_targets):
                        production.mkdir(parents=True)
                        marker = production / "keep.txt"
                        marker.write_bytes(b"active installation")
                        alias = root / f"temporary-{index}"
                        try:
                            _create_directory_link(alias, production.parent)
                        except OSError:
                            self.skipTest("当前系统没有创建符号链接的权限")
                        with self.subTest(target=production):
                            with self.assertRaisesRegex(ValueError, "祖先链接"):
                                INSTALLER.validate_install_target(alias / production.name, repo)
                            self.assertEqual(INSTALLER.validate_install_target(production, repo), production.resolve())
                            self.assertEqual(marker.read_bytes(), b"active installation")

                    custom = root / "custom installation"
                    custom.mkdir()
                    alias = root / "custom alias"
                    _create_directory_link(alias, custom)
                    with self.assertRaisesRegex(ValueError, "祖先链接"):
                        INSTALLER.validate_install_target(alias / "new-plugin", repo)
                    self.assertEqual(list(custom.iterdir()), [])

    def test_11_custom_sibling_target_never_mutates_default_mcp_config(self) -> None:
        """显式安装至 ~/.gemini/config/plugins/<staging> 时禁止修改真实 mcp_config.json。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            real_cfg = fake_home / ".gemini" / "config"
            real_cfg.mkdir(parents=True)
            mcp_cfg = real_cfg / "mcp_config.json"
            mcp_cfg.write_text('{"mcpServers":{}}\n', encoding="utf-8")
            fake_zotero = root / "zotero-mcp"
            fake_zotero.write_text("stub", encoding="utf-8")
            staging = real_cfg / "plugins" / "user-guard-staging"
            with mock.patch.object(INSTALLER.Path, "home", return_value=fake_home):
                with mock.patch.object(
                    sys,
                    "argv",
                    [
                        "install.py",
                        "--target-dir",
                        str(staging),
                        "--zotero-cmd",
                        str(fake_zotero),
                        "--skip-test",
                        "--no-codex-skill",
                    ],
                ):
                    with contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()), \
                            self.assertRaises(SystemExit):
                        INSTALLER.main()
            self.assertEqual(mcp_cfg.read_text(encoding="utf-8"), '{"mcpServers":{}}\n')

    def test_12_installer_frontier_max_model_sync(self) -> None:
        """验证安装器动态选取首族最高挡模型且不破坏 config.toml 下游表结构。"""
        label = INSTALLER._select_agy_frontier_max_label(
            [
                {"slug": "alpha-hydrogen-xhigh", "display_name": "Alpha Hydrogen XHigh"},
                {"slug": "alpha-pro-orchestrator", "display_name": "Orchestrator (Core)"},
                {"slug": "alpha-core-medium", "display_name": "Alpha Core (Medium)"},
                {"slug": "alpha-core-high", "display_name": "Alpha Core (High)"},
                {"slug": "alpha-core-ultra", "display_name": "Alpha Core (Ultra)"},
                {"slug": "alpha-core-low", "display_name": "Alpha Core (Low)"},
                {"slug": "alpha-lite-high", "display_name": "Alpha Lite (High)"},
            ]
        )
        self.assertEqual(label, "Alpha Core (High)")

        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = Path(tmp) / ".codex"
            codex_dir.mkdir()
            (codex_dir / "config.toml").write_text(
                'model = "older-model"\nmodel_reasoning_effort = "ultra"\n\n[projects."demo"]\ntrust_level = "trusted"\n',
                encoding="utf-8",
            )
            (codex_dir / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "frontier-top",
                                "priority": 1,
                                "visibility": "list",
                                "supported_reasoning_levels": [
                                    {"effort": "low", "description": "Fast"},
                                    {"effort": "high", "description": "Greater reasoning"},
                                    {"effort": "max", "description": "Maximum reasoning depth"},
                                    {"effort": "ultra", "description": "Maximum reasoning with automatic task delegation"},
                                ],
                            },
                            {
                                "slug": "older-model",
                                "priority": 2,
                                "supported_reasoning_levels": [{"effort": "medium"}],
                            },
                            {"slug": "missing-metadata", "priority": 3, "supported_reasoning_levels": None},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cfg = codex_dir / "config.toml"
            initial = cfg.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_frontier_max(codex_dir)
            self.assertEqual(cfg.read_bytes(), initial)

            for slug in ("custom-unknown", "missing-metadata"):
                with self.subTest(slug=slug):
                    cfg.write_text(f'model = "{slug}"\n', encoding="utf-8")
                    original = cfg.read_bytes()
                    with contextlib.redirect_stdout(io.StringIO()) as report:
                        INSTALLER._sync_codex_frontier_max(codex_dir)
                    self.assertEqual(cfg.read_bytes(), original)
                    self.assertNotIn("已写入", report.getvalue())

            cfg.write_text('"model" = "older-model" # 用户模型\nmodel_reasoning_effort = "" # 待补齐\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_frontier_max(codex_dir)
            self.assertEqual(cfg.read_text(encoding="utf-8"),
                             '"model" = "older-model" # 用户模型\nmodel_reasoning_effort = "medium" # 待补齐\n')

            cfg.write_bytes(initial)
            cmd = [sys.executable, "-B", "-X", "utf8", str(self.installer_root / "install.py"), "--app", "codex",
                   "--codex-home", str(codex_dir), "--skip-test", "--no-zotero"]
            result = subprocess.run(cmd + ["--sync-model-defaults", "--no-model-config"],
                                    capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(cfg.read_bytes(), initial)
            cfg.write_text('model = "older-model"\n', encoding="utf-8")
            result = subprocess.run(cmd + ["--no-model-config"], capture_output=True,
                                    text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("model_reasoning_effort", cfg.read_text(encoding="utf-8"))
            cfg.write_bytes(initial)
            result = subprocess.run(cmd + ["--sync-model-defaults"], capture_output=True,
                                    text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            updated = cfg.read_text(encoding="utf-8")
            self.assertIn('model = "frontier-top"', updated)
            self.assertIn('model_reasoning_effort = "max"', updated)
            self.assertNotIn('model_reasoning_effort = "ultra"', updated)
            self.assertIn('[projects."demo"]', updated)

            # 无 delegation/max 的模型列表应正确选出最高非委派挡位 high
            (codex_dir / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "frontier-high-only",
                                "priority": 1,
                                "visibility": "list",
                                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_frontier_max(codex_dir, overwrite=True)
            updated_high = (codex_dir / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model = "frontier-high-only"', updated_high)
            self.assertIn('model_reasoning_effort = "high"', updated_high)
            cfg.unlink()
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_frontier_max(codex_dir)
            self.assertIn('model = "frontier-high-only"', cfg.read_text(encoding="utf-8"))
            self.assertIn('model_reasoning_effort = "high"', cfg.read_text(encoding="utf-8"))

    def test_12_agy_default_model_preservation_and_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gemini_root = Path(tmp) / ".gemini"
            cli_dir = gemini_root / "antigravity-cli"
            cli_dir.mkdir(parents=True)
            settings = cli_dir / "settings.json"
            settings.write_bytes(b'{"model":"Account custom (Ultra)","other":true}\n')
            source = settings.read_bytes()
            models = [{"slug": "alpha-core-high", "display_name": "Alpha Core (High)"},
                      {"slug": "alpha-core-ultra", "display_name": "Alpha Core (Ultra)"}]
            cache = cli_dir / "cache" / "cae_models_cache.json"
            with mock.patch.object(INSTALLER, "_native_cli", return_value="native-agy") as discover, \
                    mock.patch.object(INSTALLER.subprocess, "run", return_value=subprocess.CompletedProcess(
                        ["native-agy", "models"], 0, stdout="".join(f'{item["slug"]}\t{item["display_name"]}\n' for item in models)
                    )) as query, \
                    contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.configure_agy_frontier_max_default(gemini_root)
                self.assertEqual(settings.read_bytes(), source)
                discover.assert_not_called()
                query.assert_not_called()
                self.assertFalse(cache.exists())
                INSTALLER.configure_agy_frontier_max_default(gemini_root, dry_run=True, overwrite=True)
                discover.assert_not_called()
                query.assert_not_called()
                self.assertFalse(cache.exists())
                self.assertEqual(settings.read_bytes(), source)
                INSTALLER.configure_agy_frontier_max_default(gemini_root, overwrite=True)
                query.assert_called_once()
                self.assertEqual(json.loads(settings.read_bytes()), {"model": "Alpha Core (High)", "other": True})
                snapshot = json.loads(cache.read_bytes())
                self.assertEqual(snapshot["source"], "agy_models_cli")
                self.assertEqual(snapshot["models"], models)
                self.assertEqual(datetime.datetime.fromisoformat(snapshot["fetched_at"].replace("Z", "+00:00")).utcoffset(), datetime.timedelta(0))
                cache_before = (cache.read_bytes(), cache.stat().st_mtime_ns)
                settings.unlink()
                desktop_settings = gemini_root / "config" / "config.json"
                desktop_settings.parent.mkdir()
                desktop_settings.write_bytes(b'{"userSettings":{"model":"Orchestrator (Core)"}}\n')
                INSTALLER.configure_agy_frontier_max_default(gemini_root)
                self.assertFalse(settings.exists())
                self.assertEqual((cache.read_bytes(), cache.stat().st_mtime_ns), cache_before)
                query.assert_called_once()
                desktop_settings.unlink()
                INSTALLER.configure_agy_frontier_max_default(gemini_root)
                self.assertEqual(json.loads(settings.read_bytes())["model"], "Alpha Core (High)")

    def test_13_installer_codex_workspace_boundary_sync(self) -> None:
        """验证安装器将 Codex 沙箱收紧为 workspace-write + on-request + network_access=true，幂等且保留下游表。"""
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = Path(tmp) / ".codex"
            codex_dir.mkdir()
            cfg = codex_dir / "config.toml"
            cfg.write_text(
                'sandbox_mode = "danger-full-access"\n'
                'model = "frontier-top"\n\n'
                '[projects."demo"]\n'
                'trust_level = "trusted"\n\n'
                '[hooks.state."session_start"]\n'
                'trusted_hash = "abc123"\n',
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_workspace_boundary(codex_dir, dry_run=True)
            self.assertIn('sandbox_mode = "danger-full-access"', cfg.read_text(encoding="utf-8"), "dry-run 不应改写文件")

            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_workspace_boundary(codex_dir, dry_run=False)
            first_pass = cfg.read_text(encoding="utf-8")
            self.assertNotIn("danger-full-access", first_pass)
            self.assertIn('sandbox_mode = "workspace-write"', first_pass)
            self.assertIn('approval_policy = "on-request"', first_pass)
            self.assertIn("[sandbox_workspace_write]\nnetwork_access = true", first_pass)
            self.assertIn('[projects."demo"]', first_pass)
            self.assertIn('trusted_hash = "abc123"', first_pass)

            # 二次调用严格幂等
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_workspace_boundary(codex_dir, dry_run=False)
            self.assertEqual(cfg.read_text(encoding="utf-8"), first_pass)

    def test_wp3b_rules_preserve_full_template_and_only_rename_local_collisions(self):
        template = TEMPLATE_DIR / "rules/AGENTS.md"
        expected = template.read_text(encoding="utf-8").rstrip()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AGENTS.md"
            for newline in ("\n", "\r\n"):
                with self.subTest(newline=repr(newline)):
                    tail = newline.join(("## 工具与本机  ", "R10. 编译器。", "R11. 编辑器。",
                                         "- **R10. 构建说明。**", "- **R11. 调试说明。**",
                                         "R12. 其他编号不变。", "正文中的 R10. 与 R11. 不变。", ""))
                    raw = ("# 旧规则" + newline * 2 + tail).encode("utf-8")
                    target.write_bytes(raw)
                    self.assertFalse(INSTALLER._rules_equal(raw, template.read_bytes()))
                    with contextlib.redirect_stdout(io.StringIO()):
                        INSTALLER._sync_global_rules(template, target, "fixture", dry_run=False)
                    local = tail.replace(newline + "R10.", newline + "L1.").replace(newline + "R11.", newline + "L2.")
                    local = local.replace("- **R10.", "- **L1.").replace("- **R11.", "- **L2.")
                    merged = (expected.replace("\n", newline) + newline * 2 + local).encode("utf-8")
                    self.assertEqual(target.read_bytes(), merged)
                    self.assertTrue(INSTALLER._rules_equal(merged, template.read_bytes()))
                    collision = (expected + "\n\n## 工具与本机\n- **R11. 撞号。**\n").encode("utf-8")
                    self.assertFalse(INSTALLER._rules_equal(collision, template.read_bytes()))
                    before = target.stat().st_mtime_ns
                    with contextlib.redirect_stdout(io.StringIO()):
                        INSTALLER._sync_global_rules(template, target, "fixture", dry_run=False)
                    self.assertEqual(target.read_bytes(), merged)
                    self.assertEqual(target.stat().st_mtime_ns, before)

    def test_global_rules_custom_tail_does_not_hide_missing_standard_rules(self) -> None:
        template = TEMPLATE_DIR / "rules/AGENTS.md"
        expected = template.read_text(encoding="utf-8").rstrip()
        core = expected.split("## 工具与运行环境", 1)[0].rstrip()
        tail = "## 工具与本机\nL1. 本地编译器。\n\n逐字保留尾段。  \n\n"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "AGENTS.md"
            for incomplete in (core, core + "\n\n## 工具与运行环境"):
                with self.subTest(incomplete=incomplete[-30:]):
                    raw = (incomplete + "\n\n" + tail).encode("utf-8")
                    self.assertFalse(INSTALLER._rules_equal(raw, template.read_bytes()))
                    target.write_bytes(raw)
                    with contextlib.redirect_stdout(io.StringIO()):
                        INSTALLER._sync_global_rules(template, target, "fixture", dry_run=False)
                    self.assertEqual(target.read_bytes(), (expected + "\n\n" + tail).encode("utf-8"))
                    self.assertTrue(INSTALLER._rules_equal(target.read_bytes(), template.read_bytes()))

    def test_14_installer_codex_global_rules_sync_preserves_custom_env_section(self) -> None:
        """验证完整标准规则同步、自定义环境尾段的冲突编号迁移与重复同步幂等性。"""
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = Path(tmp) / ".codex"
            codex_dir.mkdir()
            codex_agents = codex_dir / "AGENTS.md"
            custom_env_tail = (
                "## 工具与本机运行环境（自定义）\n"
                "R10. 自定义子系统内存隔离上限配置说明。\n"
                "R11. 自定义本地开发工作站环境与默认编辑器说明。"
            )
            legacy_content = "# 用户规则（旧版）\n\n## 表达与背景\nR0. 旧版精简规则。\n\n" + custom_env_tail
            codex_agents.write_text(legacy_content, encoding="utf-8")

            tpl_rules = TEMPLATE_DIR / "rules" / "AGENTS.md"
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_global_rules(tpl_rules, codex_dir, dry_run=True)
            self.assertEqual(codex_agents.read_text(encoding="utf-8"), legacy_content, "dry-run 不应修改 AGENTS.md")

            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_global_rules(tpl_rules, codex_dir, dry_run=False)
            synced = codex_agents.read_text(encoding="utf-8")
            self.assertIn("R3.1. 四端对等层级与按专长协作编排", synced)
            self.assertIn("R3.6. 验收红线与诚实披露", synced)
            migrated_env_tail = (
                "## 工具与本机运行环境（自定义）\n"
                "L1. 自定义子系统内存隔离上限配置说明。\n"
                "L2. 自定义本地开发工作站环境与默认编辑器说明。"
            )
            self.assertTrue(synced.endswith("\n\n" + migrated_env_tail + "\n"))
            self.assertIn("## 工具与运行环境", synced)
            self.assertIn("R10.", synced)
            self.assertIn("R11.", synced)

            # 二次调用严格幂等
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_global_rules(tpl_rules, codex_dir, dry_run=False)
            self.assertEqual(codex_agents.read_text(encoding="utf-8"), synced)

    def test_15_standalone_codex_full_sandbox_install_and_hook_subprocess(self) -> None:
        """验证单 Codex 模式 (--app codex --codex-home) 全量独立部署、Zotero 合并、信任哈希保持及钩子子进程运行"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "standalone_codex_home"
            fake_zotero = root / "zotero-mcp-stub"
            fake_zotero.write_text("stub", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-X",
                    "utf8",
                    str(self.installer_root / "install.py"),
                    "--app",
                    "codex",
                    "--codex-home",
                    str(codex_home),
                    "--zotero-cmd",
                    str(fake_zotero),
                    "--skip-test",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=25,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

            # 1) 验证全套 Codex 独立工作流资产已生成，且未生成反重力目录
            self.assertFalse((root / "plugins").exists())
            self.assertTrue((codex_home / "AGENTS.md").is_file())
            for name in ("readonly-reader.toml", "readonly-auditor.toml", "readonly-collaborator.toml"):
                self.assertTrue((codex_home / "agents" / name).is_file())
            self.assertTrue((codex_home / "hooks" / "session_context_loader.py").is_file())
            self.assertTrue((codex_home / "hooks" / "user_words.py").is_file())
            self.assertTrue((codex_home / "skills" / "cae-harness" / "SKILL.md").is_file())

            cfg_text = (codex_home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('sandbox_mode = "workspace-write"', cfg_text)
            self.assertIn('approval_policy = "on-request"', cfg_text)
            self.assertIn('model_reasoning_summary = "auto"', cfg_text)
            self.assertIn("network_access = true", cfg_text)
            self.assertIn("HOOKS = false", cfg_text)
            self.assertIn("[mcp_servers.zotero]", cfg_text)

            # 2) 二次运行必须保持已有有效 hooks.json 字节完全不变（保护 Codex trusted_hash）
            hooks_json_path = codex_home / "hooks.json"
            first_hooks_bytes = hooks_json_path.read_bytes()
            proc2 = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-X",
                    "utf8",
                    str(self.installer_root / "install.py"),
                    "--app",
                    "codex",
                    "--codex-home",
                    str(codex_home),
                    "--no-zotero",
                    "--skip-test",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=25,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stderr + proc2.stdout)
            self.assertEqual(hooks_json_path.read_bytes(), first_hooks_bytes)

            # 3) 真实子进程调用生成的 Codex hooks.json 恢复命令
            hooks_doc = json.loads(hooks_json_path.read_text(encoding="utf-8"))
            raw_cmd = hooks_doc["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            sid = "standalone-codex-e2e-001"
            rollout = codex_home / "sessions" / "2026" / "11" / "13" / f"rollout-2026-11-13T00-00-00-{sid}.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(root), "source": "cli"}})
                + "\n"
                + json.dumps(
                    {
                        "type": "response_item",
                        "ordinal": 1,
                        "timestamp": "2026-11-13T00:00:05Z",
                        "payload": {"id": "u1", "type": "message", "role": "user", "content": "单 Codex 独立工作流原话测试"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            hook_proc = subprocess.run(
                _build_subprocess_cmd(raw_cmd),
                input=json.dumps(
                    {
                        "session_id": sid,
                        "transcript_path": str(rollout),
                        "cwd": str(root),
                        "hook_event_name": "SessionStart",
                        "source": "compact",
                    },
                    ensure_ascii=False,
                ),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
            self.assertEqual(hook_proc.returncode, 0, hook_proc.stderr)
            out_json = json.loads(hook_proc.stdout.strip())
            self.assertEqual(out_json["hookSpecificOutput"]["hookEventName"], "SessionStart")
            self.assertIn("单 Codex 独立工作流原话测试", out_json["hookSpecificOutput"]["additionalContext"])
            self.assertTrue((codex_home / "hooks" / "state" / "user-words" / f"{sid}.md").is_file())

    def test_16_single_app_detection_prompt_and_standalone_modes(self) -> None:
        """验证仅安装单客户端时的交互提示、拒绝安全退出、确认单独安装及跨子系统互操作免打扰"""
        only_codex_det = {
            "antigravity": {"native_installed": False, "interop_available": False},
            "codex": {"native_installed": True, "interop_available": False},
        }
        # 用户选择 'n'（先去安装反重力）-> 干净退出码 0
        with mock.patch("builtins.input", return_value="n"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                INSTALLER.resolve_app_plan("auto", False, False, only_codex_det, interactive=True, auto_yes=False, dry_run=False)
            self.assertEqual(cm.exception.code, 0)

        # 用户选择 'y'（不装反重力，仅配单 Codex）-> (False, True)
        with mock.patch("builtins.input", return_value="y"), contextlib.redirect_stdout(io.StringIO()):
            plan_cx = INSTALLER.resolve_app_plan("auto", False, False, only_codex_det, interactive=True, auto_yes=False, dry_run=False)
        self.assertEqual(plan_cx, (False, True))

        # 仅安装反重力且 --yes -> (True, False)
        only_ag_det = {
            "antigravity": {"native_installed": True, "interop_available": False},
            "codex": {"native_installed": False, "interop_available": False},
        }
        with contextlib.redirect_stdout(io.StringIO()):
            plan_ag = INSTALLER.resolve_app_plan("auto", False, False, only_ag_det, interactive=True, auto_yes=True, dry_run=False)
        self.assertEqual(plan_ag, (True, False))

        # WSL 原生仅装反重力、但 Windows 侧已装 Codex (interop_available=True) -> 免打扰直接返回 (True, False)
        wsl_interop_det = {
            "antigravity": {"native_installed": True, "interop_available": False},
            "codex": {"native_installed": False, "interop_available": True},
        }
        with contextlib.redirect_stdout(io.StringIO()):
            plan_wsl = INSTALLER.resolve_app_plan("auto", False, False, wsl_interop_det, interactive=True, auto_yes=False, dry_run=False)
        self.assertEqual(plan_wsl, (True, False))

    def test_17_handoff_gate_subprocess_pipeline_and_codex_isolation(self) -> None:
        """分别执行反重力门禁和独立 Codex 提示钩子的子进程。"""
        hooks_cfg = json.loads((ACTIVE_PLUGIN_DIR / "hooks.json").read_text(encoding="utf-8"))["user-guard-lifecycle"]
        pre_cmd = hooks_cfg["PreInvocation"][0]["command"]
        stop_cmd = hooks_cfg["Stop"][0]["command"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_data = root / "plugin_data"
            ws_dir = root / "proj"
            brain_dir = root / "brain" / "e2e-handoff-conv"
            ws_dir.mkdir(parents=True)
            tp = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
            tp.parent.mkdir(parents=True)

            env = {key: value for key, value in os.environ.items() if not key.startswith("USER_GUARD_")}
            env["PLUGIN_DATA"] = str(plugin_data)

            def run_sub(raw_cmd: str, payload_dict: dict) -> dict:
                proc = subprocess.run(
                    _build_subprocess_cmd(raw_cmd),
                    input=json.dumps(payload_dict, ensure_ascii=False),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=str(ACTIVE_PLUGIN_DIR),
                    env=env,
                    timeout=15,
                )
                self.assertEqual(proc.returncode, 0, f"{raw_cmd} 失败: {proc.stderr}")
                return json.loads(proc.stdout.strip())

            def dispatch_pre_tool(name: str, args: dict) -> dict:
                commands = [
                    handler["command"] for group in hooks_cfg["PreToolUse"]
                    if re.search(group["matcher"], name)
                    for handler in group["hooks"]
                ]
                self.assertEqual(len(commands), 1, f"{name} 应由已注册的 PreToolUse 处理")
                return run_sub(commands[0], {
                    "conversationId": "e2e-handoff-conv", "cwd": str(ws_dir),
                    "workspacePaths": [str(ws_dir)], "toolCall": {"name": name, "args": args},
                })

            # 写入人类首条含 /goal 指令与 16 次 CHECKPOINT，直接激发 Stage-2 drain
            lines = [json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/goal\n执行端到端长会话验证"}, ensure_ascii=False)]
            for idx in range(1, 17):
                lines.append(json.dumps({"step_index": idx * 100, "source": "SYSTEM", "type": "CHECKPOINT"}))
            tp.write_text("\n".join(lines) + "\n", encoding="utf-8")

            conv_id = "e2e-handoff-conv"
            out_pre = run_sub(pre_cmd, {"conversationId": conv_id, "transcriptPath": str(tp), "hookEventName": "PreInvocation"})
            self.assertEqual(len(out_pre["injectSteps"]), 1)
            self.assertIn("Stage-2 drain", out_pre["injectSteps"][0]["systemMessage"]["systemMessage"])

            # 子进程调用 invoke_subagent 必被阻断；写交接档放行
            out_subagent = dispatch_pre_tool("invoke_subagent", {})
            self.assertEqual(out_subagent["decision"], "deny")

            out_handoff = dispatch_pre_tool(
                "write_to_file", {"TargetFile": str(ws_dir / "HANDOFF_NEXT.md"), "CodeContent": "# handoff"},
            )
            self.assertEqual(out_handoff["decision"], "allow")
            gate_path = next(plugin_data.rglob(f"{conv_id}_handoff_gate.json"))
            gate_state = json.loads(gate_path.read_bytes())
            gate_state["phase"] = "freeze"
            gate_path.write_text(json.dumps(gate_state), encoding="utf-8")
            self.assertEqual(dispatch_pre_tool("new_tool_without_guard_alias", {})["decision"], "deny")
            self.assertEqual(dispatch_pre_tool("finish", {})["decision"], "allow")

            # Stop 子进程验收缺失 prompt / GOAL_CANCELLED 时阻断，补全后放行
            with open(tp, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"step_index": 1605, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "准备收尾"}, ensure_ascii=False) + "\n")
            out_stop_bad = run_sub(stop_cmd, {"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp)})
            self.assertEqual(out_stop_bad["decision"], "continue")

            with open(tp, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"step_index": 1606, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "补齐交接"}, ensure_ascii=False) + "\n")
                f.write(
                    json.dumps(
                        {
                            "step_index": 1607,
                            "source": "MODEL",
                            "type": "PLANNER_RESPONSE",
                            "content": (
                                "已验证安装钩子可运行。Git 工作区已核对。未完成真实宿主验收。\n\n"
                                "### 新对话提示词\n```markdown\n请先读取用户原话，再核对当前工作区并完成宿主验收。\n"
                                "```\n<!-- GOAL_CANCELLED -->"
                            ),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            out_stop_ok = run_sub(stop_cmd, {"conversationId": conv_id, "terminationReason": "NO_TOOL_CALL", "transcriptPath": str(tp)})
            self.assertEqual(out_stop_ok["decision"], "allow")

            # 运行单 Codex 安装生成的默认提示钩子。
            codex_only_home = root / "codex_only"
            proc_cx = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-X",
                    "utf8",
                    str(self.installer_root / "install.py"),
                    "--app",
                    "codex",
                    "--codex-home",
                    str(codex_only_home),
                    "--no-zotero",
                    "--skip-test",
                    "--yes",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(proc_cx.returncode, 0, proc_cx.stderr)
            self.assertTrue((codex_only_home / "hooks" / "handoff_gate.py").is_file())
            self.assertTrue((codex_only_home / "hooks" / "session_context_loader.py").is_file())
            self.assertTrue((codex_only_home / "hooks" / "status_guard.py").is_file())
            self.assertTrue((codex_only_home / "hooks" / "reply_repeat_guard.py").is_file())
            codex_hooks = json.loads((codex_only_home / "hooks.json").read_text(encoding="utf-8"))["hooks"]
            self.assertEqual(set(codex_hooks), {"SessionStart", "PostCompact", "UserPromptSubmit", "PreToolUse", "Stop"})
            sid = "isolated-codex-handoff"
            rollout = codex_only_home / f"rollout-{sid}.jsonl"
            rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": sid, "source": "cli"}}) + "\n", encoding="utf-8")
            codex_env = {key: value for key, value in env.items() if key != "PLUGIN_DATA"}
            for event, extra, output_key in (
                ("PostCompact", {"turn_id": "turn-1", "trigger": "auto"}, "systemMessage"),
                ("SessionStart", {"source": "compact"}, "hookSpecificOutput"),
            ):
                handler = next(h for h in codex_hooks[event][0]["hooks"] if "handoff_gate.py" in h["command"])
                self.assertIn("--mode advisory --compact-limit 0", handler["command"])
                result = subprocess.run(
                    _build_subprocess_cmd(handler["command"]),
                    input=json.dumps({"hook_event_name": event, "session_id": sid, "cwd": str(root),
                                      "transcript_path": str(rollout), **extra}),
                    capture_output=True, text=True, encoding="utf-8", cwd=codex_only_home, env=codex_env, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                outcome = json.loads(result.stdout)
                self.assertNotIn("decision", outcome)
                self.assertIn(output_key, outcome)
                self.assertIn("Codex 长会话", json.dumps(outcome, ensure_ascii=False))
            self.assertTrue(any((codex_only_home / "hooks" / "state" / "handoff").glob("*.json")))
            self.assertFalse((plugin_data / "state" / "handoff" / f"{sid}.json").exists())

    def test_18_codex_hook_upgrade_preserves_handlers_and_rejects_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp).resolve() / "codex with spaces"
            codex_root.mkdir()
            hooks_path = codex_root / "hooks.json"
            loader_path = codex_root / "hooks" / "session_context_loader.py"
            loader_path.parent.mkdir()
            loader_path.write_bytes((TEMPLATE_DIR / "hooks" / loader_path.name).read_bytes())
            state_dir = (loader_path.parent / "state" / "session-context").as_posix()
            valid = {"type": "command", "command": f'"{sys.executable}" -B "{loader_path.as_posix()}" --client codex --state-dir "{state_dir}"',
                     "timeout": 33, "statusMessage": "保留已有设置"}
            sibling = {"type": "command", "command": "custom-session-start"}
            other_group = {"matcher": "startup", "hooks": [{"type": "command", "command": "custom-startup"}]}
            other_event = [{"hooks": [{"type": "command", "command": "custom-notification"}]}]
            other_gate = {"type": "command", "command": f'"{sys.executable}" "{(Path(tmp) / "custom" / "handoff_gate.py").as_posix()}"'}
            shared_path_gate = {"type": "command", "command": (
                f'"{sys.executable}" -B "{(loader_path.parent / "handoff_gate.py").as_posix()}" '
                f'--client codex --mode enforce --compact-limit 1 --state-dir "{(loader_path.parent / "state" / "handoff").as_posix()}"'
            )}
            legacy = {"description": "用户原有说明\n第二行", "hooks": {"SessionStart": [{"matcher": "^(compact|resume)$", "hooks": [valid, sibling]}, other_group],
                                "Notification": other_event, "Stop": [{"hooks": [other_gate, shared_path_gate]}]}}
            quote = (lambda value: '"' + value + '"') if os.name == "nt" else shlex.quote
            log = (loader_path.parent / "logs" / "session-context.jsonl").as_posix()
            expected_loader = {**valid, "command": (
                f'{quote(sys.executable)} -X utf8 -I -B {quote(loader_path.as_posix())} '
                f'--client codex --state-dir {quote(state_dir)} --log {quote(log)}'
            )}
            expected_legacy = json.loads(json.dumps(legacy))
            expected_legacy["hooks"]["SessionStart"][0]["hooks"][0] = expected_loader
            hooks_path.write_text(json.dumps(legacy), encoding="utf-8")
            cmd = [sys.executable, "-B", "-X", "utf8", str(self.installer_root / "install.py"), "--app", "codex",
                   "--codex-home", str(codex_root), "--no-zotero", "--skip-test"]

            def install(*options):
                return subprocess.run(cmd + list(options), capture_output=True, text=True, encoding="utf-8", timeout=20)

            result = install()
            self.assertEqual(result.returncode, 0, result.stderr)
            upgraded_bytes = hooks_path.read_bytes()
            upgraded = json.loads(upgraded_bytes)["hooks"]
            self.assertEqual(upgraded["SessionStart"][0]["hooks"][:2], [expected_loader, sibling])
            self.assertEqual(upgraded["SessionStart"][1], other_group)
            self.assertEqual(upgraded["Notification"], other_event)
            self.assertEqual(upgraded["Stop"][0]["hooks"][:2], [other_gate, shared_path_gate])
            self.assertTrue({"PostCompact", "UserPromptSubmit", "PreToolUse"} <= set(upgraded))
            result = install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(hooks_path.read_bytes(), upgraded_bytes)

            result = install("--codex-handoff-mode", "enforce", "--codex-handoff-compactions", "2")
            self.assertEqual(result.returncode, 0, result.stderr)
            enforce_bytes = hooks_path.read_bytes()
            self.assertIn(b"--mode enforce --compact-limit 2", enforce_bytes)
            result = install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(hooks_path.read_bytes(), enforce_bytes)
            migrated = json.loads(enforce_bytes)
            migrated["description"] = legacy["description"]
            hooks_path.write_text(json.dumps(migrated), encoding="utf-8")
            result = install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(hooks_path.read_bytes(), enforce_bytes)
            result = install("--codex-handoff-mode", "off")
            self.assertEqual(result.returncode, 0, result.stderr)
            disabled_bytes = hooks_path.read_bytes()
            py_prefix = INSTALLER.detect_hook_python_command()
            guard_state = quote((loader_path.parent / "state").as_posix())
            guard_logs = quote((loader_path.parent / "logs").as_posix())
            expected_status = {
                "type": "command",
                "command": f'{py_prefix} {quote((loader_path.parent / "status_guard.py").as_posix())} '
                           f'--client codex --state-dir {guard_state} --log-dir {guard_logs}',
                "timeout": 15, "statusMessage": "检查工作区写边界与日志重复读取",
            }
            expected_reply = {
                "type": "command",
                "command": f'{py_prefix} {quote((loader_path.parent / "reply_repeat_guard.py").as_posix())} '
                           f'--client codex --state-dir {guard_state} --log-dir {guard_logs}',
                "timeout": 15, "statusMessage": "检查重复状态回复",
            }
            self.assertEqual(json.loads(disabled_bytes), {
                **expected_legacy, "description": legacy["description"] + "\n[user-guard codex-handoff v1 mode=off compact_limit=2]",
                "hooks": {
                    "SessionStart": [{"matcher": "^(compact|resume)$", "hooks": [expected_loader, sibling]}, other_group],
                    "Notification": other_event,
                    "PreToolUse": [{"matcher": ".*", "hooks": [expected_status]}],
                    "Stop": [{"hooks": [other_gate, shared_path_gate, expected_reply]}],
                },
            })
            result = install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(hooks_path.read_bytes(), disabled_bytes)
            result = install("--codex-handoff-mode", "advisory")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"--mode advisory --compact-limit 2", hooks_path.read_bytes())

            for malformed in (b"{bad json", b'{"hooks":{"SessionStart":{}}}', b'{"hooks":{},"hooks":{}}'):
                with self.subTest(malformed=malformed):
                    hooks_path.write_bytes(malformed)
                    result = install()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("无法安全合并", result.stderr)
                    self.assertEqual(hooks_path.read_bytes(), malformed)


    def test_20_codex_loader_upgrade_probes_only_trusted_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "codex"
            root.mkdir()
            loader = (root / "hooks" / "session_context_loader.py").as_posix()
            old = {"type": "command", "command": f'"{sys.executable}" -B "{loader}" --client codex',
                   "timeout": 33, "statusMessage": "custom status"}
            sibling = {"type": "command", "command": "custom-hook"}
            untrusted_dir = Path(tmp) / "untrusted"
            untrusted_dir.mkdir()
            untrusted = untrusted_dir / Path(sys.executable).name
            untrusted.write_bytes(b"not an interpreter")
            custom_program = {"type": "command", "command": f'"{untrusted}" -B "{loader}" --client codex'}
            custom_flags = {"type": "command", "command": f'"{sys.executable}" -c "raise SystemExit(0)" "{loader}" --client codex'}
            path = root / "hooks.json"
            path.write_text(json.dumps({"hooks": {"SessionStart": [
                {"matcher": "^(compact|resume)$", "hooks": [old, sibling, custom_program, custom_flags]},
            ]}}), encoding="utf-8")
            before = path.read_bytes()
            with mock.patch.object(INSTALLER.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as check, \
                    contextlib.redirect_stderr(io.StringIO()) as diagnostics:
                plan = INSTALLER._plan_codex_hooks(path, REPO_ROOT / "template/codex/hooks.json", "python -X utf8 -I -B", "advisory", 0)
            self.assertEqual(check.call_count, 1)
            invoked = check.call_args.args[0]
            self.assertEqual(Path(invoked[0]), Path(sys.executable))
            self.assertEqual(invoked[1:], ["-X", "utf8", "-I", "-B", "-c",
                                          "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"])
            self.assertIn("自定义脚本命令", diagnostics.getvalue())
            self.assertEqual(path.read_bytes(), before)
            handlers = json.loads(plan.change.content)["hooks"]["SessionStart"][0]["hooks"]
            self.assertNotEqual(handlers[0]["command"], old["command"])
            self.assertEqual(handlers[0]["timeout"], 33)
            self.assertEqual(handlers[0]["statusMessage"], "custom status")
            self.assertEqual(handlers[1:4], [sibling, custom_program, custom_flags])

    def test_20_codex_loader_upgrade_keeps_working_interpreter_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "codex"
            root.mkdir()
            path = root / "hooks.json"
            template = REPO_ROOT / "template/codex/hooks.json"
            replacement = "python -X utf8 -I -B"
            baseline = INSTALLER._plan_codex_hooks(path, template, replacement).change.content
            interpreter = str(Path(sys.executable).resolve())
            prefixes = [(interpreter, []), ("python.exe" if os.name == "nt" else "python3", [])]
            if os.name == "nt":
                prefixes.append(("py.exe", ["-3"]))
            quote = (lambda value: '"' + value + '"') if os.name == "nt" else shlex.quote
            script_args = [(root / "hooks" / "session_context_loader.py").as_posix(), "--client", "codex",
                           "--state-dir", (root / "hooks" / "state" / "session-context").as_posix(),
                           "--log", (root / "hooks" / "logs" / "session-context.jsonl").as_posix()]
            for executable, launcher in prefixes:
                for flags in (["-I", "-B"], INSTALLER.HOOK_PYTHON_FLAGS):
                    for arg_count in (3, 5, 7):
                        with self.subTest(executable=executable, flags=flags, arg_count=arg_count):
                            doc = json.loads(baseline)
                            old = doc["hooks"]["SessionStart"][0]["hooks"][0]
                            old.update(command="  ".join([quote(executable), *launcher, *flags,
                                                         *(quote(arg) for arg in script_args[:arg_count])]),
                                       timeout=33, statusMessage="用户状态提示", trusted_hash="fixture-trust")
                            original = b"\xef\xbb\xbf" + json.dumps(doc, ensure_ascii=False, indent=4).replace("\n", "\r\n").encode("utf-8")
                            path.write_bytes(original)
                            name = "" if Path(executable).is_absolute() else executable
                            with mock.patch.object(INSTALLER, "_hook_python_candidates", return_value=[(name, interpreter, launcher)]), \
                                    mock.patch.object(INSTALLER.shutil, "which", return_value=interpreter), \
                                    mock.patch.object(INSTALLER, "_is_working_python_cmd", return_value=True) as probe:
                                plan = INSTALLER._plan_codex_hooks(path, template, replacement)
                                probe.assert_called_once_with(interpreter, launcher)
                                self.assertEqual(path.read_bytes(), original)
                                if flags == INSTALLER.HOOK_PYTHON_FLAGS and arg_count == len(script_args):
                                    self.assertIsNone(plan.change)
                                else:
                                    self.assertIsNotNone(plan.change)
                                    handler = json.loads(plan.change.content)["hooks"]["SessionStart"][0]["hooks"][0]
                                    self.assertEqual(INSTALLER._codex_hook_tokens(handler),
                                                     [executable, *launcher, *INSTALLER.HOOK_PYTHON_FLAGS, *script_args])
                                    self.assertEqual(handler, {**old, "command": handler["command"]})
                                    path.write_bytes(plan.change.content)
                                installed = path.read_bytes()
                                self.assertIsNone(INSTALLER._plan_codex_hooks(path, template, replacement).change)
                                self.assertEqual(path.read_bytes(), installed)

    @unittest.skipUnless(os.name == "nt", "Windows PATH 的应用执行别名")
    def test_20_shadowed_windows_python_loader_uses_working_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            alias = str(root / "WindowsApps" / "python.exe")
            python = str(root / "python" / "python.exe")
            launcher = str(root / "py.exe")
            candidates = [("python.exe", python, []), ("py.exe", launcher, ["-3"])]
            resolved = {"python.exe": alias, "py.exe": launcher}
            script = (root / "hooks" / "session_context_loader.py").as_posix()
            old = {"type": "command", "command": f'python.exe -I -B "{script}" --client codex',
                   "statusMessage": "已有状态提示"}
            hooks_path = root / "hooks.json"
            hooks_path.write_text(json.dumps({"hooks": {"SessionStart": [
                {"matcher": "^(compact|resume)$", "hooks": [old]},
            ]}}), encoding="utf-8")
            original = hooks_path.read_bytes()
            template = REPO_ROOT / "template/codex/hooks.json"
            with mock.patch.object(INSTALLER, "_hook_python_candidates", return_value=candidates), \
                    mock.patch.object(INSTALLER.shutil, "which", side_effect=resolved.get), \
                    mock.patch.object(INSTALLER.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as probe:
                cmd = INSTALLER.detect_hook_python_command()
                plan = INSTALLER._plan_codex_hooks(hooks_path, template, cmd)
            self.assertEqual(cmd, "py.exe -3 -X utf8 -I -B")
            probe.assert_called_once()
            self.assertEqual(probe.call_args.args[0][:2], [launcher, "-3"])
            self.assertEqual(hooks_path.read_bytes(), original)
            loader = json.loads(plan.change.content)["hooks"]["SessionStart"][0]["hooks"][0]
            self.assertTrue(loader["command"].startswith(cmd + " "))
            self.assertEqual(loader["statusMessage"], old["statusMessage"])

    @unittest.skipIf(os.name == "nt", "POSIX Shell 路径转义")
    def test_21_posix_codex_hook_special_path_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "codex$cash`tick' space"
            root.mkdir()
            path = root / "hooks.json"
            template = REPO_ROOT / "template/codex/hooks.json"
            interpreter = root / 'python$cash`tick" space'
            interpreter.symlink_to(sys.executable)
            with mock.patch.object(INSTALLER.sys, "executable", str(interpreter)), \
                    mock.patch.object(INSTALLER.shutil, "which", return_value=None):
                cmd = INSTALLER.detect_hook_python_command()
                first = INSTALLER._plan_codex_hooks(path, template, cmd)
                path.write_bytes(first.change.content)
                self.assertIsNone(INSTALLER._plan_codex_hooks(path, template, cmd).change)
                scripts = root / "hooks"
                scripts.mkdir()
                for name in ("session_context_loader.py", "user_words.py"):
                    shutil.copy2(TEMPLATE_DIR / "hooks" / name, scripts / name)
                if (TEMPLATE_DIR / "hooks" / "_lib").is_dir():
                    shutil.copytree(TEMPLATE_DIR / "hooks" / "_lib", scripts / "_lib")
                installed = json.loads(first.change.content)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
                payload = {"hook_event_name": "SessionStart", "session_id": "fallback-round-trip", "source": "startup"}
                result = subprocess.run(["/bin/sh", "-c", installed], input=json.dumps(payload), cwd=root,
                                        capture_output=True, text=True, encoding="utf-8", timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"], "SessionStart")
                self.assertTrue((root / "hooks" / "logs" / "session-context.jsonl").is_file())
                disabled = INSTALLER._plan_codex_hooks(path, template, cmd, "off", 0)
            hooks = json.loads(disabled.change.content)["hooks"]
            remaining = [handler for groups in hooks.values() for group in groups for handler in group["hooks"]]
            self.assertEqual(len(remaining), 3)
            commands = "\n".join(handler["command"] for handler in remaining)
            for name in ("session_context_loader.py", "status_guard.py", "reply_repeat_guard.py"):
                self.assertIn(name, commands)
            self.assertNotIn("handoff_gate.py", commands)

    def test_22_codex_toml_preflight_preserves_quoted_keys_comments_and_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            codex.mkdir()
            config = codex / "config.toml"
            config.write_bytes(
                b'"sandbox_mode" = "danger-full-access" # keep sandbox comment\r\n'
                b'"approval_policy" = "never" # keep approval\r\n'
                b'"model" = "account-choice" # keep model\r\n'
                b"'model_reasoning_effort' = 'high' # keep effort\r\n"
                b'"model_reasoning_summary" = "detailed"\r\n'
                b'["sandbox_workspace_write"] # sandbox header\r\n'
                b'"network_access" = false # keep network\r\n'
                b'["features"] # features header\r\n'
                b'"memories" = true # keep opt in\r\n'
                b'[memories] # memory header\r\n'
                b'"generate_memories" = true\r\n'
                b'["desktop"."external-agent-import-sync-item-types"] # imports\r\n'
                b"'HOOKS' = true # keep import\r\n"
                b'[hooks.state."handler#1"] # preserve trust\r\n'
                b'"trusted_hash" = "sha256:example"\r\n'
                b'["mcp_servers"."zotero"] # remote transport\r\n'
                b'"url" = "https://example.org/mcp" # keep remote URL\r\n'
                b'"bearer_token_env_var" = "ZOTERO_TOKEN"\r\n'
            )
            before = config.read_bytes()
            zotero = root / "zotero-mcp-stub"
            zotero.write_bytes(b"stub")
            cmd = [sys.executable, "-B", str(self.installer_root / "install.py"), "--app", "codex", "--codex-home", str(codex),
                   "--skip-test", "--zotero-cmd", str(zotero)]
            dry = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse((codex / "hooks.json").exists())
            run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            updated = config.read_bytes()
            self.assertIn(b'"sandbox_mode" = "workspace-write" # keep sandbox comment\r\n', updated)
            for line in before.splitlines(keepends=True)[1:]:
                self.assertIn(line, updated)
            parsed = INSTALLER._parse_toml(updated.decode("utf-8"), config)
            self.assertEqual(parsed["features"], {"memories": True, "chronicle": False})
            self.assertEqual(parsed["approval_policy"], "never")
            self.assertEqual(parsed["sandbox_workspace_write"], {"network_access": False})
            self.assertEqual(parsed["hooks"]["state"]["handler#1"]["trusted_hash"], "sha256:example")
            self.assertEqual(parsed["mcp_servers"]["zotero"], {
                "url": "https://example.org/mcp", "bearer_token_env_var": "ZOTERO_TOKEN",
            })
            mtime = config.stat().st_mtime_ns
            run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual((config.read_bytes(), config.stat().st_mtime_ns), (updated, mtime))

    def test_23_config_errors_abort_both_targets_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "agy-config" / "plugins" / "user-guard"
            mcp = plugin.parent.parent / "mcp_config.json"
            codex = root / "codex"
            plugin.mkdir(parents=True)
            codex.mkdir()
            config = codex / "config.toml"
            hooks_path = codex / "hooks.json"
            (plugin / "old.txt").write_bytes(b"must not be cleaned")
            zotero = root / "zotero-mcp-stub"
            zotero.write_bytes(b"stub")
            cmd = [sys.executable, "-B", str(self.installer_root / "install.py"), "--app", "both", "--target-dir", str(plugin),
                   "--codex-home", str(codex), "--skip-test", "--zotero-cmd", str(zotero)]

            def snapshot() -> dict:
                return {entry.relative_to(root): (entry.read_bytes(), entry.stat().st_mtime_ns) if entry.is_file() else None
                        for entry in root.rglob("*")}

            cases = [(config, data) for data in (b"invalid TOML", b'a = 1\na = 2\n', b"[features\n")]
            cases += [(mcp, data) for data in (
                b"", b"{bad json", b"[]", b'{"mcpServers": []}', b'{"mcpServers": {"zotero": false}}',
                b'{"mcpServers": {"zotero": {}, "zotero": {}}}', b'{"mcpServers": {"other": NaN}}',
            )]
            cases += [(hooks_path, data) for data in (
                br'{"description":"\ud800","hooks":{}}',
                b'{"hooks":{"Notification":[{"hooks":[{"type":"command","command":"custom","custom":1e999}]}]}}',
            )]
            for path, content in cases:
                with self.subTest(path=path.name, content=content):
                    mcp.write_bytes(b'{"mcpServers": {}}\n')
                    config.write_bytes(b'model = "example"\n')
                    hooks_path.write_bytes(b'{"hooks":{}}\n')
                    path.write_bytes(content)
                    before = snapshot()
                    for options in (["--dry-run"], []):
                        result = subprocess.run(cmd + options, capture_output=True, text=True, encoding="utf-8", timeout=20)
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn(path.name, result.stderr)
                        self.assertEqual(snapshot(), before)

    def test_24_early_link_rejection_leaves_all_install_targets_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "agy-config" / "plugins" / "user-guard"
            plugin.mkdir(parents=True)
            (plugin / "old.txt").write_bytes(b"retain")
            external = root / "external"
            external.mkdir()
            marker = external / "keep.txt"
            marker.write_bytes(b"external sentinel")
            zotero = root / "zotero-mcp-stub"
            zotero.write_bytes(b"stub")
            probe = root / "link-probe"
            try:
                _create_directory_link(probe, external)
            except OSError:
                self.skipTest("当前系统没有创建目录链接或联接的权限")
            cmd = [sys.executable, "-B", str(self.installer_root / "install.py"), "--app", "both", "--target-dir", str(plugin),
                   "--skip-test", "--zotero-cmd", str(zotero)]

            def reject(codex: Path, link: Path) -> None:
                result = subprocess.run(cmd + ["--codex-home", str(codex)], capture_output=True, text=True,
                                        encoding="utf-8", timeout=20)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(link.name, result.stderr)
                self.assertEqual(list(external.iterdir()), [marker])
                self.assertEqual(marker.read_bytes(), b"external sentinel")
                self.assertEqual(list(plugin.iterdir()), [plugin / "old.txt"])
                self.assertEqual((plugin / "old.txt").read_bytes(), b"retain")
                self.assertFalse((codex / "hooks.json").exists())
                self.assertFalse((codex / "config.toml").exists())
                self.assertFalse((codex / "AGENTS.md").exists())

            for name in ("agents", "hooks", "skills"):
                with self.subTest(name=name):
                    codex = root / ("codex-" + name)
                    codex.mkdir()
                    link = codex / name
                    _create_directory_link(link, external)
                    reject(codex, link)
                    self.assertEqual(list(codex.iterdir()), [link])
            reject(probe / "codex", probe)
            mcp = plugin.parent.parent / "mcp_config.json"
            _create_directory_link(mcp, external)
            unused_codex = root / "unused-codex"
            reject(unused_codex, mcp)
            self.assertFalse(unused_codex.exists())

            for name in ("AGENTS.md", "config.toml", "hooks.json", "agents/readonly-reader.toml", "hooks/user_words.py"):
                with self.subTest(file=name):
                    codex = root / ("files-" + name.replace("/", "-"))
                    target = codex / name
                    target.parent.mkdir(parents=True)
                    # 硬链接在 Windows 无需符号链接特权，验证直接文件别名同样拒绝。
                    try:
                        os.link(marker, target)
                    except OSError:
                        self.skipTest("当前文件系统不支持硬链接")
                    result = subprocess.run(cmd + ["--codex-home", str(codex), "--no-zotero"], capture_output=True,
                                            text=True, encoding="utf-8", timeout=20)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(target.name, result.stderr)
                    self.assertEqual(target.read_bytes(), b"external sentinel")
                    self.assertEqual(marker.read_bytes(), b"external sentinel")
                    self.assertEqual(list(plugin.iterdir()), [plugin / "old.txt"])

    def test_25_agy_zotero_preserves_existing_remote_and_stdio_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plugin = root / "agy-config" / "plugins" / "user-guard"
            config = plugin.parent.parent / "mcp_config.json"
            config.parent.mkdir()
            zotero = root / "zotero-mcp-stub"
            zotero.write_bytes(b"stub")
            cmd = [sys.executable, "-B", str(self.installer_root / "install.py"), "--app", "antigravity",
                   "--target-dir", str(plugin), "--skip-test", "--zotero-cmd", str(zotero)]
            for data in (
                b'{"mcpServers":{"zotero":{"serverUrl":"https://example.org/mcp","headers":{"X-Example":"yes"}}}}\n',
                b'{"mcpServers":{"zotero":{"command":"host-managed-zotero","args":["serve"],"env":{"CUSTOM":"true"}}}}\n',
            ):
                with self.subTest(data=data):
                    config.write_bytes(data)
                    mtime = config.stat().st_mtime_ns
                    run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertEqual((config.read_bytes(), config.stat().st_mtime_ns), (data, mtime))
            config.write_bytes(b'{"mcpServers":{"other":{"serverUrl":"https://example.org/mcp"}},"custom":true}\n')
            run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            merged = json.loads(config.read_bytes())
            self.assertTrue(merged["custom"])
            self.assertEqual(merged["mcpServers"]["other"], {"serverUrl": "https://example.org/mcp"})
            self.assertEqual(merged["mcpServers"]["zotero"]["command"], str(zotero))

    def test_26_executable_evidence_does_not_treat_config_as_installation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            (home / ".gemini" / "config").mkdir(parents=True)
            wsl = {"status": "unknown", "installed_distros": ["stopped"], "running_distros": [], "apps": {}}
            with mock.patch.object(INSTALLER.Path, "home", return_value=home), \
                    mock.patch.object(INSTALLER, "_native_cli", return_value=None), \
                    mock.patch.object(INSTALLER, "_first_executable", return_value=None), \
                    mock.patch.object(INSTALLER, "_find_windows_codex_package", return_value=(None, None)), \
                    mock.patch.object(INSTALLER, "_is_wsl", return_value=False), \
                    mock.patch.object(INSTALLER, "_probe_windows_wsl", return_value=wsl), \
                    mock.patch.object(INSTALLER, "detect_zotero_mcp_executable", return_value=None), \
                    mock.patch.dict(os.environ, {"CODEX_HOME": ""}):
                result = INSTALLER.detect_installed_apps()
                self.assertEqual(result["recommended_app_mode"], "none")
                for name in ("antigravity", "codex"):
                    self.assertFalse(result[name]["native_installed"])
                    self.assertEqual(result[name]["native_status"], "config_only")
                    self.assertEqual(result[name]["authentication_status"], "not_checked")
                    self.assertFalse(result[name]["interop_available"])

            with mock.patch.object(INSTALLER.shutil, "which", return_value="wsl.exe"), \
                    mock.patch.object(INSTALLER, "_wsl_distro_list", side_effect=[["running", "stopped"], ["running"]]), \
                    mock.patch.object(INSTALLER.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, stdout="agy\t/opt/bin/agy\ttrue\ncodex\t\ttrue\n",
                    )) as probe:
                result = INSTALLER._probe_windows_wsl()
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["installed_distros"], ["running", "stopped"])
                self.assertEqual(result["apps"]["agy"][0]["binary"], "/opt/bin/agy")
                self.assertIsNone(result["apps"]["codex"][0]["binary"])
                probe.assert_called_once()
                self.assertEqual(probe.call_args.args[0][:4], ["wsl.exe", "--distribution", "running", "--exec"])

    def test_26_wsl_windows_profile_requires_successful_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = r"C:\Users\example"
            result = subprocess.CompletedProcess
            cases = (
                ([result([], 1, stdout=profile)], None),
                ([result([], 0, stdout="")], None),
                ([result([], 0, stdout=r"Users\example")], None),
                ([result([], 0, stdout=profile), result([], 1, stdout=str(root))], None),
                ([result([], 0, stdout=profile), result([], 0, stdout="\n")], None),
                ([result([], 0, stdout=profile), result([], 0, stdout=".")], None),
                ([result([], 0, stdout=profile), result([], 0, stdout=str(root / "missing"))], None),
                ([result([], 0, stdout=profile), result([], 0, stdout=str(root))], root),
            )
            executables = {"cmd.exe": str(root / "cmd.exe"), "wslpath": str(root / "wslpath")}
            for commands, expected in cases:
                with self.subTest(commands=commands), \
                        mock.patch.object(INSTALLER, "_is_wsl", return_value=True), \
                        mock.patch.object(INSTALLER.shutil, "which", side_effect=executables.get), \
                        mock.patch.object(INSTALLER.subprocess, "run", side_effect=commands) as run:
                    self.assertEqual(INSTALLER._probe_wsl_windows_profile_dir(), expected)
                    self.assertEqual(run.call_count, len(commands))

    def test_28_installer_regression_does_not_inherit_runtime_state_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "agy" / "plugins" / "user-guard"
            codex = root / "codex"
            state, logs = root / "external-state", root / "external-logs"
            state.mkdir()
            logs.mkdir()
            for path in (state, logs):
                (path / "keep").write_bytes(b"runtime sentinel")
            argv = ["install.py", "--app", "both", "--target-dir", str(plugin), "--codex-home", str(codex), "--no-zotero"]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.dict(os.environ, {"USER_GUARD_STATE_DIR": str(state), "USER_GUARD_LOGS_DIR": str(logs)}), \
                    mock.patch.object(INSTALLER, "detect_hook_python_command", return_value="python -X utf8 -I -B"), \
                    mock.patch.object(INSTALLER.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.main()
                self.assertEqual(os.environ["USER_GUARD_STATE_DIR"], str(state))
                self.assertEqual(os.environ["USER_GUARD_LOGS_DIR"], str(logs))
            run.assert_called_once()
            self.assertIn("unittest", run.call_args.args[0])
            env = run.call_args.kwargs["env"]
            self.assertNotIn("USER_GUARD_STATE_DIR", env)
            self.assertNotIn("USER_GUARD_LOGS_DIR", env)
            self.assertEqual(Path(env["USER_GUARD_INSTALLED_DIR"]), plugin.resolve())
            self.assertEqual(Path(env["USER_GUARD_INSTALLED_CODEX_DIR"]), codex.resolve())
            for path in (state, logs):
                self.assertEqual(list(path.iterdir()), [path / "keep"])
                self.assertEqual((path / "keep").read_bytes(), b"runtime sentinel")

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "需要 POSIX Bash")
    def test_27_shell_probe_stdout_is_json_and_old_python_does_not_block_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "python3"
            old.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            old.chmod(0o755)
            (root / "python").symlink_to(sys.executable)
            env = os.environ.copy()
            env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                ["bash", str(REPO_ROOT / "install.sh"), "--probe-apps"], env=env,
                capture_output=True, text=True, encoding="utf-8", timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("recommended_app_mode", json.loads(result.stdout))
            self.assertIn("启动", result.stderr)

    def test_29_empty_mcp_no_zotero_rules_upgrade_dryrun_sync_and_both_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_root = root / ".gemini" / "config"
            cfg_root.mkdir(parents=True)
            (cfg_root / "mcp_config.json").write_bytes(b"")
            self.assertIsNone(INSTALLER._plan_user_zotero(cfg_root, None))
            with self.assertRaises(ValueError):
                INSTALLER._plan_user_zotero(cfg_root, str(root / "zotero-mcp"))

            codex_dir = root / "codex_rules"
            codex_dir.mkdir()
            codex_agents = codex_dir / "AGENTS.md"
            codex_agents.write_text(
                "# 用户规则（全局生效）\n- 旧规则\n\n## 工具与运行环境\n- **R10. 旧版环境说明**：旧内容\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER._sync_codex_global_rules(TEMPLATE_DIR / "rules" / "AGENTS.md", codex_dir, dry_run=False)
            upgraded = codex_agents.read_text(encoding="utf-8")
            self.assertIn("## 工具与运行环境", upgraded)
            self.assertNotIn("旧版环境说明", upgraded)
            self.assertIn("R11.", upgraded)

            plugin_dir = root / "plugins" / "user-guard"
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.mirror_sync_plugin(TEMPLATE_DIR, plugin_dir, dry_run=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                INSTALLER.mirror_sync_plugin(TEMPLATE_DIR, plugin_dir, dry_run=True)
            self.assertNotIn("[SYNC]", buf.getvalue())

            bad_argv = ["install.py", "--app", "both", "--target-dir", str(plugin_dir), "--no-zotero", "--dry-run"]
            with mock.patch.object(sys, "argv", bad_argv), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                INSTALLER.main()
            self.assertNotEqual(cm.exception.code, 0)

    def test_30_repository_python_sources_support_python311_syntax(self) -> None:
        sources = sorted(
            path for path in REPO_ROOT.rglob("*.py")
            if not any(p in path.parts for p in (".git", "__pycache__", ".pytest_cache")) and path.is_file()
        )
        self.assertIn(REPO_ROOT / "install.py", sources)
        self.assertIn(TEMPLATE_DIR / "skills" / "cae-harness" / "scripts" / "cae_harness.py", sources)
        self.assertTrue(list((TEMPLATE_DIR / "hooks").glob("*.py")))
        self.assertIn(Path(__file__).resolve(), sources)
        for path in sources:
            with self.subTest(source=path.relative_to(REPO_ROOT).as_posix()):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))

    def test_31_toml_key_parts_preserves_quoted_and_dotted_keys(self) -> None:
        for source, expected in (
            ("model", ("model",)),
            (" mcp_servers . 'zotero.server' ", ("mcp_servers", "zotero.server")),
            (r'"mcp\u005fservers"."zotero\u002eserver"', ("mcp_servers", "zotero.server")),
            (r'"quote\"slash\\".model', ('quote"slash\\', "model")),
        ):
            with self.subTest(source=source):
                self.assertEqual(INSTALLER._toml_key_parts(source), expected)

    def test_32_hook_python_candidates_accept_quoted_windows_path_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "Python Space"
            directory.mkdir()
            alias = root / "WindowsApps"
            alias.mkdir()
            for path in (directory / "python.exe", directory / "py.exe", alias / "python.exe"):
                path.write_bytes(b"candidate")
            windows_os = mock.Mock(wraps=os)
            windows_os.name = "nt"
            windows_os.get_exec_path.return_value = [f'"{alias}"', f'  "{directory}"  ', '"relative"', ""]
            with mock.patch.object(INSTALLER, "os", windows_os):
                candidates = INSTALLER._hook_python_candidates()
            self.assertEqual(candidates[:2], [
                ("python.exe", str(directory / "python.exe"), []),
                ("py.exe", str(directory / "py.exe"), ["-3"]),
            ])
            self.assertNotIn(str(alias / "python.exe"), [entry[1] for entry in candidates])

    def test_33_mirror_upgrade_removes_only_stale_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, target = root / "source", root / "target"
            for tree in (source, target):
                (tree / "active" / "empty").mkdir(parents=True)
            (source / "active" / "keep.txt").write_bytes(b"current template")
            stale = target / "obsolete"
            (stale / "nested").mkdir(parents=True)
            (stale / "nested" / "old.txt").write_bytes(b"old template")
            empty = target / "already-empty" / "nested"
            empty.mkdir(parents=True)
            words = target / "hooks" / "state" / "history" / "session_user_words.md"
            log = target / "hooks" / "logs" / "lifecycle.log"
            for path in (words, log):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"runtime history")

            def snapshot():
                return {path.relative_to(target): (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
                        for path in target.rglob("*")}

            before = snapshot()
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.mirror_sync_plugin(source, target, dry_run=True)
            self.assertEqual(snapshot(), before)
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.mirror_sync_plugin(source, target)
            self.assertFalse(stale.exists())
            self.assertFalse(empty.parent.exists())
            self.assertTrue((target / "active" / "empty").is_dir())
            self.assertEqual((target / "active" / "keep.txt").read_bytes(), b"current template")
            self.assertEqual(words.read_bytes(), b"runtime history")
            self.assertEqual(log.read_bytes(), b"runtime history")
            synchronized = snapshot()
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.mirror_sync_plugin(source, target)
            self.assertEqual(snapshot(), synchronized)

    @unittest.skipUnless(os.name == "nt", "需要 Windows PowerShell 5.1")
    def test_34_windows_powershell_entrypoint_codex_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex = Path(tmp) / "codex home"
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.installer_root / "install.ps1"),
                 "--dry-run", "--app", "codex", "--codex-home", str(codex), "--no-zotero"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Dry-Run", result.stdout)
            self.assertFalse(codex.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "需要 Windows PowerShell 5.1")
    def test_35_windows_powershell_forwards_native_stderr_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = root / "install.ps1"
            shutil.copy2(REPO_ROOT / "install.ps1", entry)
            (root / "install.py").write_text(
                'import sys\nprint("installer-stdout:" + "|".join(sys.argv[1:]))\n'
                'print("installer-stderr", file=sys.stderr)\nraise SystemExit(int(sys.argv[-1]))\n',
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["BATCH4_PS_ENTRY"] = str(entry)
            env["BATCH4_SPACE_ARG"] = r"C:\Path With Spaces\trail" + "\\"
            for code in (0, 7):
                with self.subTest(code=code):
                    env["BATCH4_EXIT_CODE"] = str(code)
                    result = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                         "$ErrorActionPreference = 'Stop'; & $env:BATCH4_PS_ENTRY $env:BATCH4_SPACE_ARG $env:BATCH4_EXIT_CODE 2>&1; exit $LASTEXITCODE"],
                        env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
                    )
                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                    self.assertIn(r"installer-stdout:C:\Path With Spaces\trail\|" + str(code), result.stdout)
                    self.assertIn("installer-stderr", result.stdout + result.stderr)

    def test_36_windows_reparse_tags_distinguish_links_from_cloud_directories(self) -> None:
        path = mock.Mock(spec=Path)
        path.is_symlink.return_value = False
        windows_os = mock.Mock(wraps=os)
        windows_os.name = "nt"
        with mock.patch.object(INSTALLER, "os", windows_os), \
                mock.patch.object(INSTALLER.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, create=True):
            for tag, expected in ((0xA000000C, True), (0xA0000003, True), (0x9000001A, False), (0, True)):
                with self.subTest(tag=hex(tag)):
                    path.lstat.return_value = mock.Mock(st_file_attributes=0x400, st_reparse_tag=tag)
                    self.assertEqual(INSTALLER._is_link_or_junction(path), expected)
            path.lstat.return_value = mock.Mock(spec=["st_file_attributes"], st_file_attributes=0x400)
            self.assertTrue(INSTALLER._is_link_or_junction(path))
            path.lstat.return_value = mock.Mock(st_file_attributes=0, st_reparse_tag=0)
            self.assertFalse(INSTALLER._is_link_or_junction(path))

    def test_38_codex_gate_policy_update_keeps_working_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "codex"
            root.mkdir()
            path = root / "hooks.json"
            interpreter = str(Path(sys.executable).resolve())
            quote = (lambda value: '"' + value + '"') if os.name == "nt" else shlex.quote
            script = (root / "hooks" / "handoff_gate.py").as_posix()
            state = (root / "hooks" / "state" / "handoff").as_posix()
            old_prefix = f'{quote(interpreter)} -X utf8 -I -B'
            old = {"type": "command", "command": (
                f'{old_prefix} {quote(script)} --client codex --mode advisory --compact-limit 0 --state-dir {quote(state)}'
            ), "timeout": 17, "statusMessage": "用户状态提示"}
            path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [old]}]}}), encoding="utf-8")
            original = path.read_bytes()
            replacement = "python -X utf8 -I -B"
            for working in (True, False):
                with self.subTest(working=working), \
                        mock.patch.object(INSTALLER, "_hook_python_candidates", return_value=[("", interpreter, [])]), \
                        mock.patch.object(INSTALLER, "_is_working_python_cmd", return_value=working) as probe:
                    plan = INSTALLER._plan_codex_hooks(path, REPO_ROOT / "template/codex/hooks.json", replacement, "enforce", 3)
                    probe.assert_called_once_with(interpreter, [])
                    self.assertIsNotNone(plan.change)
                    handler = json.loads(plan.change.content)["hooks"]["Stop"][0]["hooks"][0]
                    tokens = INSTALLER._codex_hook_tokens(handler)
                    expected_prefix = old_prefix if working else replacement
                    prefix_tokens = INSTALLER._codex_hook_tokens({"type": "command", "command": expected_prefix})
                    self.assertEqual(tokens[:-9], prefix_tokens)
                    self.assertEqual(tokens[-9:], [script, "--client", "codex", "--mode", "enforce",
                                                  "--compact-limit", "3", "--state-dir", state])
                    self.assertEqual(handler["timeout"], old["timeout"])
                    self.assertEqual(handler["statusMessage"], old["statusMessage"])
                    self.assertEqual(path.read_bytes(), original)
            with mock.patch.object(INSTALLER, "_hook_python_candidates", return_value=[("", interpreter, [])]), \
                    mock.patch.object(INSTALLER, "_is_working_python_cmd", return_value=True):
                plan = INSTALLER._plan_codex_hooks(path, REPO_ROOT / "template/codex/hooks.json", replacement, "advisory", 0)
            self.assertEqual(json.loads(plan.change.content)["hooks"]["Stop"][0]["hooks"][0], old)

    def test_39_installer_entrypoints_preserve_required_encodings(self) -> None:
        ps1 = (REPO_ROOT / "install.ps1").read_bytes()
        self.assertTrue(ps1.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(ps1.count(b"\r\n"), ps1.count(b"\n"))
        ps1.decode("utf-8-sig")
        for name in ("install.py", "install.sh", "tests/test_e2e_integration.py"):
            with self.subTest(name=name):
                data = (REPO_ROOT / name).read_bytes()
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                data.decode("utf-8")
        self.assertNotIn(b"\r", (REPO_ROOT / "install.sh").read_bytes())

    def test_40_default_user_tokens_still_reject_hardcoded_home_paths(self) -> None:
        for name in ("root", "ubuntu", "docker"):
            with self.subTest(user=name):
                self.assertNotIn(name, ALLOWED_PLACEHOLDERS)
                self.assertEqual(_sensitive_findings(name + " = Path(tmp)", user_name=name), [])
                self.assertIn("未脱敏主目录", _sensitive_findings("/home/" + name + "/work", user_name=name))

    def test_41_repository_ascii_paths_links_and_single_version_source(self) -> None:
        """校验受控源文件均为纯 ASCII 路径、Markdown 本地相对链接目标存在且文档不重复硬编码插件版本号。"""
        ci_workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        self.assertTrue(ci_workflow.is_file(), "须包含跨平台离线回归 GitHub Actions 工作流")
        ci_bytes = ci_workflow.read_bytes()
        self.assertFalse(ci_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", ci_bytes)

        plugin_version = json.loads((ACTIVE_PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))["version"]
        plugin_readme = (ACTIVE_PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
        self.assertIsNone(
            re.search(r"当前版本\s*`?\d+\.\d+\.\d+", plugin_readme),
            "插件子目录 README 不应重复硬编码静态版本号，须以 plugin.json 为唯一真源",
        )

        def extract_md_targets(markdown_text: str) -> list[str]:
            stripped = re.sub(r"(?m)^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$", "", markdown_text, flags=re.DOTALL)
            stripped = re.sub(r"(`+)([^`]*?)\1", "", stripped)
            targets: list[str] = []
            idx = 0
            while True:
                open_pos = stripped.find("](", idx)
                if open_pos == -1:
                    break
                pos = open_pos + 2
                while pos < len(stripped) and stripped[pos] in " \t":
                    pos += 1
                if pos >= len(stripped):
                    break
                if stripped[pos] == "<":
                    end_angle = stripped.find(">", pos + 1)
                    if end_angle == -1 or "\n" in stripped[pos + 1 : end_angle]:
                        idx = open_pos + 2
                        continue
                    dest = stripped[pos + 1 : end_angle]
                    close_paren = stripped.find(")", end_angle + 1)
                    if close_paren != -1 and "\n" not in stripped[end_angle + 1 : close_paren]:
                        targets.append(dest)
                        idx = close_paren + 1
                        continue
                else:
                    depth = 0
                    buf: list[str] = []
                    cur = pos
                    closed = False
                    while cur < len(stripped) and stripped[cur] not in "\r\n":
                        ch = stripped[cur]
                        if ch == "\\" and cur + 1 < len(stripped):
                            buf.append(stripped[cur + 1])
                            cur += 2
                            continue
                        if ch == "(":
                            depth += 1
                            buf.append(ch)
                        elif ch == ")":
                            if depth > 0:
                                depth -= 1
                                buf.append(ch)
                            else:
                                targets.append("".join(buf))
                                idx = cur + 1
                                closed = True
                                break
                        elif ch in " \t" and depth == 0:
                            rest = cur
                            while rest < len(stripped) and stripped[rest] not in "\r\n)":
                                if stripped[rest] == "\\" and rest + 1 < len(stripped):
                                    rest += 2
                                    continue
                                rest += 1
                            if rest < len(stripped) and stripped[rest] == ")":
                                targets.append("".join(buf))
                                idx = rest + 1
                                closed = True
                            break
                        else:
                            buf.append(ch)
                        cur += 1
                    if closed:
                        continue
                idx = open_pos + 2
            return targets

        sample_targets = extract_md_targets(
            "[a](docs/a(b).md#sec) [b](<https://example.com>) [c](missing.md 'title') `[skip](nope.md)`"
        )
        self.assertEqual(sample_targets, ["docs/a(b).md#sec", "https://example.com", "missing.md"])

        repo_files: list[Path] = []
        if (REPO_ROOT / ".git").exists():
            git_proc = subprocess.run(
                ["git", "-c", "core.quotepath=false", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=REPO_ROOT, env=_without_git_env(), capture_output=True, text=True, encoding="utf-8", check=False, timeout=15,
            )
            if git_proc.returncode == 0:
                repo_files = [REPO_ROOT / line for line in git_proc.stdout.splitlines() if line and (REPO_ROOT / line).is_file()]
        if not repo_files:
            ignored_parts = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "env", "build", "dist", "htmlcov", ".idea", ".vscode"}
            ignored_prefixes = ("template/user-guard/hooks/state/", "template/user-guard/hooks/logs/")
            for p in REPO_ROOT.rglob("*"):
                rel_parts = p.relative_to(REPO_ROOT).parts
                rel_str = p.relative_to(REPO_ROOT).as_posix()
                if any(part in ignored_parts for part in rel_parts) or rel_str.startswith(ignored_prefixes) or p.suffix == ".log":
                    continue
                self.assertFalse(p.is_symlink() and not p.exists(), f"仓库包含悬空符号链接: {rel_str}")
                if p.is_file():
                    repo_files.append(p)

        repo_resolved = REPO_ROOT.resolve()
        version_token_re = re.compile(rf"(?<![\d.]){re.escape(plugin_version)}(?![\d.])")
        static_date_re = re.compile(r"\b202\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")
        patch_prefix_re = re.compile(r"(?m)^\s*(?:>\s*)?(?:\*{0,2})(?:补充说明|更新说明|补丁说明|历史补丁)\s*[：:]")
        anchor_cache: dict[Path, set[str]] = {}

        def anchors_for_md(md_file: Path) -> set[str]:
            resolved_md = md_file.resolve()
            if resolved_md not in anchor_cache:
                anchor_cache[resolved_md] = _extract_md_heading_anchors(resolved_md.read_text(encoding="utf-8"))
            return anchor_cache[resolved_md]

        checked_md_count = 0
        checked_svg_count = 0
        checked_anchor_count = 0
        for path in repo_files:
            rel_posix = path.relative_to(REPO_ROOT).as_posix()
            self.assertTrue(rel_posix.isascii(), f"仓库文件路径须为纯 ASCII 以免跨平台转义: {rel_posix}")
            suffix_low = path.suffix.lower()
            if rel_posix.startswith("docs/assets/") and suffix_low == ".svg":
                checked_svg_count += 1
                svg_text = path.read_text(encoding="utf-8")
                self.assertIsNone(
                    version_token_re.search(svg_text),
                    f"{rel_posix} 不应硬编码当前插件版本号 {plugin_version}",
                )
                self.assertIsNone(
                    static_date_re.search(svg_text),
                    f"{rel_posix} 不应硬编码静态日期戳",
                )
                continue
            if suffix_low != ".md":
                continue
            checked_md_count += 1
            md_text = path.read_text(encoding="utf-8")
            if rel_posix != "CHANGELOG.md":
                self.assertIsNone(
                    version_token_re.search(md_text),
                    f"{rel_posix} 不应硬编码当前插件版本号 {plugin_version}，须以 plugin.json 为唯一真源",
                )
                self.assertIsNone(
                    static_date_re.search(md_text),
                    f"{rel_posix} 不应残留静态日期戳补丁，时效演进统一归入 CHANGELOG.md",
                )
                self.assertIsNone(
                    patch_prefix_re.search(md_text),
                    f"{rel_posix} 检出补丁式堆叠抬头，请整篇通顺重写而非追加附注",
                )
            for raw_target in extract_md_targets(md_text):
                stripped_raw = raw_target.strip()
                if not stripped_raw or stripped_raw.startswith("//") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", stripped_raw):
                    continue
                path_part, has_hash, frag_part = stripped_raw.partition("#")
                target = urllib.parse.unquote(path_part.split("?", 1)[0].strip())
                anchor = urllib.parse.unquote(frag_part.strip()).lower() if has_hash else ""
                if not target:
                    if has_hash:
                        self.assertTrue(bool(anchor), f"{rel_posix} 包含空页内锚点: {raw_target}")
                        self.assertIn(anchor, anchors_for_md(path), f"{rel_posix} 页内标题锚点不存在: #{frag_part}")
                        checked_anchor_count += 1
                    continue
                self.assertFalse(target.startswith("/"), f"{rel_posix} 本地链接不应使用根前导斜杠: {raw_target}")
                resolved = (path.parent / target).resolve()
                self.assertTrue(
                    resolved == repo_resolved or repo_resolved in resolved.parents,
                    f"{rel_posix} 本地链接越出仓库根目录: {raw_target}",
                )
                self.assertTrue(resolved.exists(), f"{rel_posix} 存在失效的本地链接: {raw_target}")
                if has_hash and resolved.is_file() and resolved.suffix.lower() == ".md":
                    if not re.fullmatch(r"l\d+(?:-l?\d+)?", anchor):
                        self.assertIn(anchor, anchors_for_md(resolved), f"{rel_posix} 跨文档标题锚点不存在: {raw_target}")
                        checked_anchor_count += 1
        self.assertGreaterEqual(checked_md_count, 6)
        self.assertGreaterEqual(checked_svg_count, 3)
        self.assertGreaterEqual(checked_anchor_count, 8)

    @unittest.skipUnless(os.name == "nt", "验证 Windows NTFS 8.3 短路径下 Codex 托管钩子升级不重复追加")
    def test_42_windows_8dot3_short_path_codex_hook_upgrade(self) -> None:
        import ctypes

        with tempfile.TemporaryDirectory() as tmp:
            long_root = Path(tmp).resolve() / "long_8dot3_codex_home"
            (long_root / "hooks").mkdir(parents=True)
            buf = ctypes.create_unicode_buffer(1024)
            if not ctypes.windll.kernel32.GetShortPathNameW(str(long_root), buf, 1024) or "~" not in buf.value:
                self.skipTest("当前卷未启用 NTFS 8.3 短文件名")
            short_root = Path(buf.value)
            interpreter = str(Path(sys.executable).resolve())
            short_loader = (short_root / "hooks" / "session_context_loader.py").as_posix()
            short_state = (short_root / "hooks" / "state" / "session-context").as_posix()
            old = {
                "type": "command",
                "command": f'"{interpreter}" -B "{short_loader}" --client codex --state-dir "{short_state}"',
                "timeout": 29,
                "statusMessage": "保留既有状态提示",
            }
            hooks_path = short_root / "hooks.json"
            hooks_path.write_text(
                json.dumps({"hooks": {"SessionStart": [{"matcher": "^(compact|resume)$", "hooks": [old]}]}}),
                encoding="utf-8",
            )
            template = REPO_ROOT / "template/codex/hooks.json"
            with mock.patch.object(INSTALLER, "_hook_python_candidates", return_value=[("", interpreter, [])]), \
                    mock.patch.object(INSTALLER, "_is_working_python_cmd", return_value=True) as probe:
                plan = INSTALLER._plan_codex_hooks(hooks_path, template, "python -X utf8 -I -B")
                probe.assert_called_once_with(interpreter, [])
                self.assertIsNotNone(plan.change)
                upgraded = json.loads(plan.change.content)["hooks"]["SessionStart"][0]["hooks"]
                self.assertEqual(len(upgraded), 2)
                self.assertEqual(sum("session_context_loader.py" in h["command"] for h in upgraded), 1)
                self.assertEqual(upgraded[0]["timeout"], 29)
                self.assertEqual(upgraded[0]["statusMessage"], "保留既有状态提示")
                hooks_path.write_bytes(plan.change.content)
                self.assertIsNone(INSTALLER._plan_codex_hooks(hooks_path, template, "python -X utf8 -I -B").change)

    def _copy_fleet_repo(self, sandbox):
        repo = sandbox / "upstream"
        managed = self._managed_repo_files()
        for name in filter(None, managed):
            source = REPO_ROOT / name
            if source.is_file():
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        return repo

    def _fleet_state_command(self, script, sandbox, *args):
        # 仓内临时目录的上级仍有真实仓库。仅模拟夹具的发现边界，状态重叠检查保持原实现。
        bootstrap = """
import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("fleet_state_sandbox", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sandbox = Path(sys.argv[2]).resolve()
original = module._is_upstream_repo
def within_sandbox(candidate):
    return candidate.resolve().is_relative_to(sandbox) and original(candidate)
module._is_upstream_repo = within_sandbox
raise SystemExit(module.main(sys.argv[3:]))
"""
        return [sys.executable, "-B", "-X", "utf8", "-c", bootstrap, str(script), str(sandbox), *args]

    def test_43_fleet_evolve_skill_and_two_pass_bootstrap_state_lock(self) -> None:
        skill_md = TEMPLATE_DIR / "skills" / "fleet-evolve" / "SKILL.md"
        state_py = TEMPLATE_DIR / "skills" / "fleet-evolve" / "scripts" / "fleet_state.py"
        self.assertTrue(skill_md.is_file())
        self.assertTrue(state_py.is_file())
        self.assertTrue(skill_md.read_text(encoding="utf-8").startswith("---\nname: fleet-evolve"))

        label = INSTALLER._select_agy_frontier_max_label([
            {"slug": "claude-opus-5-5-high", "display_name": "Claude Opus 5.5 (High)"},
            {"slug": "gpt-oss-120b-medium", "display_name": "GPT-OSS 120B (Medium)"},
            {"slug": "gemini-4-carbon-medium", "display_name": "Gemini 4 Carbon (Medium)"},
            {"slug": "gemini-4-carbon-high", "display_name": "Gemini 4 Carbon (High)"},
        ])
        self.assertEqual(label, "Gemini 4 Carbon (High)")
        self.assertIsNone(INSTALLER._select_agy_frontier_max_label([
            {"slug": "claude-opus-5-5-high", "display_name": "Claude Opus 5.5 (High)"},
            {"slug": "gpt-oss-120b-medium", "display_name": "GPT-OSS 120B (Medium)"},
            {"slug": "auto-router-max", "display_name": "Auto Router (Max)"},
        ]))
        self.assertEqual(INSTALLER._select_agy_frontier_max_label([
            {"slug": "claude-opus-5-5-high", "display_name": "Claude Opus 5.5 (High)"},
            {"slug": "gemini-3-flash-low", "display_name": "Gemini 3 Flash (Low)"},
            {"slug": "gemini-3-flash-high", "display_name": "Gemini 3 Flash (High)"},
        ]), "Gemini 3 Flash (High)")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            repo = self._copy_fleet_repo(tmp_root)
            state_py = repo / state_py.relative_to(REPO_ROOT)
            state_dir = tmp_root / "fleet_state"
            env = {**os.environ, "CAE_FLEET_STATE_DIR": str(state_dir)}

            auto_repo = subprocess.run(
                self._fleet_state_command(state_py, tmp_root, "status"),
                cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            )
            self.assertEqual(auto_repo.returncode, 0, auto_repo.stderr)
            self.assertEqual(json.loads(auto_repo.stdout)["phase"], "IDLE")

            installed_copy = tmp_root / "isolated_skill" / "fleet_state.py"
            installed_copy.parent.mkdir(parents=True)
            installed_copy.write_bytes(state_py.read_bytes())
            no_repo_err = subprocess.run(
                self._fleet_state_command(installed_copy, tmp_root, "status"),
                cwd=tmp_root, capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            )
            self.assertNotEqual(no_repo_err.returncode, 0)

            tilde_env = {
                **os.environ,
                "HOME": str(tmp_root),
                "USERPROFILE": str(tmp_root),
                "CODEX_HOME": "~/custom_codex",
                "CAE_FLEET_STATE_DIR": str(tmp_root / "custom_codex" / "sub"),
            }
            tilde_overlap = subprocess.run(
                self._fleet_state_command(state_py, tmp_root, "--repo", str(repo), "status"),
                capture_output=True, text=True, encoding="utf-8", env=tilde_env, timeout=15,
            )
            self.assertNotEqual(tilde_overlap.returncode, 0)

            def run_state(*cli_args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    self._fleet_state_command(state_py, tmp_root, "--repo", str(repo), *cli_args),
                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
                )

            st0 = json.loads(run_state("status").stdout)
            self.assertEqual(st0["phase"], "IDLE")

            bad_env = {**os.environ, "CAE_FLEET_STATE_DIR": str(repo / "bad_state")}
            bad = subprocess.run(
                self._fleet_state_command(state_py, tmp_root, "--repo", str(repo), "status"),
                capture_output=True, text=True, encoding="utf-8", env=bad_env, timeout=15,
            )
            self.assertNotEqual(bad.returncode, 0)

            rec = run_state("record-pass1", "--session-id", "pass1_sess", "--target-master", "Antigravity Carbon High")
            self.assertEqual(rec.returncode, 0, rec.stderr)
            self.assertEqual(json.loads(run_state("status").stdout)["phase"], "AWAITING_MASTER_RELOAD")

            same = run_state("verify-pass2", "--session-id", "pass1_sess")
            self.assertEqual(same.returncode, 2)
            self.assertEqual(json.loads(same.stdout)["reason"], "same_session_loop")

            ready = run_state("verify-pass2", "--session-id", "pass2_sess")
            self.assertEqual(ready.returncode, 0, ready.stderr)
            self.assertEqual(json.loads(ready.stdout)["reason"], "ready_for_pass2")
            # 同一 Pass 2 会话重入幂等通过，并发竞争会话被原子认领拦截
            ready_again = run_state("verify-pass2", "--session-id", "pass2_sess")
            self.assertEqual(ready_again.returncode, 0, ready_again.stderr)
            competing = run_state("verify-pass2", "--session-id", "competing_pass2_sess")
            self.assertEqual(competing.returncode, 2)
            self.assertEqual(json.loads(competing.stdout)["reason"], "already_claimed")
            overwrite_blocked = run_state("record-pass1", "--session-id", "other_pass1", "--target-master", "Antigravity Carbon High")
            self.assertEqual(overwrite_blocked.returncode, 2)
            self.assertEqual(json.loads(overwrite_blocked.stdout)["reason"], "active_pass2_claimed")
            overwrite_forced = run_state("record-pass1", "--force", "--session-id", "other_pass1", "--target-master", "Antigravity Carbon High")
            self.assertEqual(overwrite_forced.returncode, 0)

            state_file = Path(json.loads(rec.stdout)["state_file"])
            state_file.write_text("{corrupt json", encoding="utf-8")
            corrupt_st = json.loads(run_state("status").stdout)
            self.assertEqual(corrupt_st["phase"], "IDLE")
            self.assertEqual(corrupt_st["stale_reason"], "corrupt_state")

            bad_field = json.loads(rec.stdout)["state"]
            bad_field["ttl_seconds"] = "bad_int"
            state_file.write_text(json.dumps(bad_field), encoding="utf-8")
            bad_field_st = json.loads(run_state("status").stdout)
            self.assertEqual(bad_field_st["phase"], "IDLE")
            self.assertEqual(bad_field_st["stale_reason"], "corrupt_state")
            bad_field_ver = run_state("verify-pass2", "--session-id", "pass2_sess")
            self.assertEqual(bad_field_ver.returncode, 2)
            self.assertEqual(json.loads(bad_field_ver.stdout)["reason"], "corrupt_state")

            drifted = json.loads(rec.stdout)["state"]
            drifted["plugin_version"] = "0.0.1"
            state_file.write_text(json.dumps(drifted), encoding="utf-8")
            drift_st = json.loads(run_state("status").stdout)
            self.assertEqual(drift_st["phase"], "IDLE")
            self.assertEqual(drift_st["stale_reason"], "repo_drift")

            saved = json.loads(rec.stdout)["state"]
            saved["updated_at_epoch"] = 1
            state_file.write_text(json.dumps(saved), encoding="utf-8")
            expired = run_state("verify-pass2", "--session-id", "pass2_sess")
            self.assertEqual(expired.returncode, 2)
            self.assertEqual(json.loads(expired.stdout)["reason"], "expired_ttl")
            self.assertEqual(json.loads(run_state("status").stdout)["phase"], "IDLE")

            clr_pending = json.loads(run_state("clear", "--verified").stdout)
            self.assertFalse(clr_pending["readback_verified"])
            self.assertEqual(clr_pending["notice"], "pending_cold_readback")
            clr_done = json.loads(run_state("clear", "--verified", "--readback-done").stdout)
            self.assertTrue(clr_done["readback_verified"])
            self.assertNotIn("notice", clr_done)
            ver_st = json.loads(run_state("status").stdout)
            self.assertEqual(ver_st["phase"], "IDLE")
            self.assertIn("last_verified_epoch", ver_st["saved"])
            self.assertTrue(ver_st["saved"]["readback_verified"])
            self.assertEqual(run_state("clear").returncode, 0)
            self.assertEqual(json.loads(run_state("status").stdout)["phase"], "IDLE")
            self.assertIsNone(json.loads(run_state("status").stdout)["saved"])

        # 验证 _rules_equal 精确捕获 R10/R11 尾段漂移并保留用户自定义 ## 工具与本机 尾段
        tmpl_rules = (TEMPLATE_DIR / "rules" / "AGENTS.md").read_bytes()
        self.assertTrue(INSTALLER._rules_equal(tmpl_rules, tmpl_rules))
        drifted_r11 = tmpl_rules.replace(b"R11.", b"R11-OLD.")
        self.assertFalse(INSTALLER._rules_equal(drifted_r11, tmpl_rules))
        with_custom_tail = tmpl_rules.rstrip(b"\r\n") + "\n\n## 工具与本机求解器路径\n- OpenFOAM v2406\n".encode("utf-8")
        self.assertTrue(INSTALLER._rules_equal(with_custom_tail, tmpl_rules))
        drifted_core_with_tail = with_custom_tail.replace(b"R10.", b"R10-OLD.")
        self.assertFalse(INSTALLER._rules_equal(drifted_core_with_tail, tmpl_rules))

    def test_44_meta_driven_profile_decoupling_and_user_approval_gate(self) -> None:
        profile_path = TEMPLATE_DIR / "skills" / "cae-harness" / "fleet_profile.json"
        self.assertTrue(profile_path.is_file(), "缺少单点模型评估快照清单 fleet_profile.json")
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        self.assertEqual(profile.get("schema_version"), 1)
        policy = profile.get("evaluation_policy", {})
        self.assertTrue(policy.get("bidirectional_strength_and_defect_audit"))
        self.assertTrue(policy.get("user_approval_required_before_apply"))
        self.assertGreaterEqual(len(policy.get("approval_checkpoints", [])), 4)

        roles = profile.get("roles", {})
        for role_name in ("orchestrator", "math", "code", "review", "fast"):
            self.assertIn(role_name, roles)
            self.assertTrue(roles[role_name].get("candidates"))
            self.assertTrue(roles[role_name].get("strengths"))
            self.assertTrue(roles[role_name].get("defects_and_mitigations"))

        excluded_vendors = {
            item.get("vendor") for item in profile.get("excluded_or_retiring", {}).get("excluded_vendors", [])
        }
        self.assertIn("anthropic_native_claude", excluded_vendors)

        # 常驻规则与技能说明中严禁硬编码季节性模型名
        seasonal_re = re.compile(
            r"\b(?:astra|sol|terra|carbon|neon|argon|hydrogen|luna|k3\.1-pro|deepseek-v4|deepseek-flash)\b",
            re.I,
        )
        for permanent_doc in (
            TEMPLATE_DIR / "rules" / "AGENTS.md",
            REPO_ROOT / "AGENTS.md",
            TEMPLATE_DIR / "skills" / "cae-harness" / "SKILL.md",
            TEMPLATE_DIR / "skills" / "fleet-evolve" / "SKILL.md",
            REPO_ROOT / "docs" / "workflow-guide.md",
            REPO_ROOT / "docs" / "architecture-and-hooks.md",
        ):
            doc_text = permanent_doc.read_text(encoding="utf-8")
            self.assertIsNone(
                seasonal_re.search(doc_text),
                f"常驻契约文件 {permanent_doc.relative_to(REPO_ROOT).as_posix()} 检出季节性模型名硬编码",
            )

        # 验证 fleet_state.py 推荐搭配表用户拍板硬门禁 (propose -> AWAITING_USER_APPROVAL -> approve -> IDLE)
        state_py = TEMPLATE_DIR / "skills" / "fleet-evolve" / "scripts" / "fleet_state.py"
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            repo = self._copy_fleet_repo(sandbox)
            state_dir = sandbox / "approval_state"
            env = {**os.environ, "CAE_FLEET_STATE_DIR": str(state_dir)}

            def run_state(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    self._fleet_state_command(state_py, sandbox, "--repo", str(repo), *args),
                    cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
                )

            premature = run_state("approve", "--session-id", "sess_a")
            self.assertEqual(premature.returncode, 2)
            self.assertEqual(json.loads(premature.stdout)["reason"], "no_pending_proposal")

            prop = run_state("propose", "--session-id", "sess_a", "--summary", "更新客户端×模型×挡位推荐表")
            self.assertEqual(prop.returncode, 0, prop.stderr)
            prop_json = json.loads(prop.stdout)
            self.assertTrue(prop_json["user_approval_required"])
            self.assertEqual(prop_json["state"]["phase"], "AWAITING_USER_APPROVAL")
            self.assertTrue(prop_json["state"]["profile_sha256"])

            cur_st = json.loads(run_state("status").stdout)
            self.assertEqual(cur_st["phase"], "AWAITING_USER_APPROVAL")

            appr = run_state("approve", "--session-id", "sess_a")
            self.assertEqual(appr.returncode, 0, appr.stderr)
            appr_json = json.loads(appr.stdout)
            self.assertEqual(appr_json["phase"], "IDLE")
            self.assertTrue(appr_json["user_approved"])

            # 若提请拍板后 fleet_profile.json 的 sha256 发生变更，approve 必须拦截重审
            prop2 = run_state("propose", "--session-id", "sess_b", "--summary", "测试哈希篡改拦截")
            self.assertEqual(prop2.returncode, 0, prop2.stderr)
            saved_file = Path(json.loads(prop2.stdout)["state_file"])
            tampered = json.loads(saved_file.read_text(encoding="utf-8"))
            tampered["profile_sha256"] = "0" * 64
            saved_file.write_text(json.dumps(tampered), encoding="utf-8")
            stale_appr = run_state("approve", "--session-id", "sess_b")
            self.assertEqual(stale_appr.returncode, 2)
            self.assertEqual(json.loads(stale_appr.stdout)["reason"], "repo_or_profile_changed_since_propose")

    def test_45_workspace_mode_guard_grounding_gate_and_csc_funding(self) -> None:
        # 1. 验证 fleet_profile.json 全动态策略、runtime_tunables 结构与致谢声明
        profile_path = TEMPLATE_DIR / "skills" / "cae-harness" / "fleet_profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        policy = profile.get("evaluation_policy", {})
        self.assertTrue(policy.get("all_roles_dynamic"))
        self.assertTrue(policy.get("dynamic_tunables_enabled"))
        tunables = profile.get("runtime_tunables", {})
        for group_key in ("compaction_recovery", "handoff_thresholds", "loop_guards", "headless_dispatch"):
            self.assertIn(group_key, tunables)
        for role_name in ("orchestrator", "math", "code", "review", "fast"):
            self.assertIn("recommended_tier", profile["roles"][role_name])

        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("国家留学基金管理委员会", readme_text)
        self.assertIn("China Scholarship Council", readme_text)
        self.assertIn("202506280162", readme_text)
        self.assertIn("留金选[2025]37号", readme_text)
        arch_text_mode = (REPO_ROOT / "docs" / "architecture-and-hooks.md").read_text(encoding="utf-8")
        self.assertIn("general_project", arch_text_mode)
        self.assertIn("upstream_config_repo", arch_text_mode)

        # 2. 验证安装后的 fleet_state.py 在通用课题目录返回结构化退出码 2，且终审归位强制校验文档事实锚定
        state_py = TEMPLATE_DIR / "skills" / "fleet-evolve" / "scripts" / "fleet_state.py"
        harness_py = TEMPLATE_DIR / "skills" / "cae-harness" / "scripts" / "cae_harness.py"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            env = {**os.environ, "CAE_FLEET_STATE_DIR": str(tmp_root / "fleet_state")}

            general_proj = tmp_root / "my_cfd_project"
            general_proj.mkdir()
            installed_state = tmp_root / "installed_skills" / "fleet_state.py"
            installed_state.parent.mkdir(parents=True)
            installed_state.write_bytes(state_py.read_bytes())

            gen_res = subprocess.run(
                self._fleet_state_command(installed_state, tmp_root, "status"),
                cwd=general_proj, capture_output=True, text=True, encoding="utf-8", env=env, timeout=15,
            )
            self.assertEqual(gen_res.returncode, 2)
            gen_json = json.loads(gen_res.stdout)
            self.assertEqual(gen_json["workspace_mode"], "general_project")
            self.assertEqual(gen_json["reason"], "not_upstream_config_repo")

            # 构造最小上游仓库副本并注入虚构制造术语，验证 clear --verified --readback-done 硬拦截
            fake_repo = tmp_root / "fake_upstream"
            (fake_repo / "template" / "user-guard" / "skills" / "cae-harness" / "scripts").mkdir(parents=True)
            (fake_repo / "template" / "user-guard" / "skills" / "fleet-evolve").mkdir(parents=True)
            (fake_repo / "install.py").write_text("# installer\n", encoding="utf-8")
            (fake_repo / "template" / "user-guard" / "plugin.json").write_text('{"version": "1.13.0"}\n', encoding="utf-8")
            (fake_repo / "template" / "user-guard" / "skills" / "fleet-evolve" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
            (fake_repo / "template" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json").write_bytes(profile_path.read_bytes())
            (fake_repo / "template" / "user-guard" / "skills" / "cae-harness" / "scripts" / "cae_harness.py").write_bytes(harness_py.read_bytes())
            src_harness_pkg = harness_py.parent / "_harness"
            if src_harness_pkg.is_dir():
                dst_harness_pkg = fake_repo / "template" / "user-guard" / "skills" / "cae-harness" / "scripts" / "_harness"
                dst_harness_pkg.mkdir(parents=True, exist_ok=True)
                for sub_f in src_harness_pkg.glob("*.py"):
                    (dst_harness_pkg / sub_f.name).write_bytes(sub_f.read_bytes())
            (fake_repo / "README.md").write_text(
                "# 说明\n\n面向流体求解器、CAM 刀轨规划与曲面求交。\n",
                encoding="utf-8",
            )
            bad_clear = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", str(state_py), "--repo", str(fake_repo), "clear", "--verified", "--readback-done"],
                capture_output=True, text=True, encoding="utf-8", env=env, timeout=20,
            )
            self.assertEqual(bad_clear.returncode, 2)
            bad_json = json.loads(bad_clear.stdout)
            self.assertFalse(bad_json["readback_verified"])
            self.assertEqual(bad_json["reason"], "grounding_check_failed")
            self.assertGreaterEqual(bad_json["finding_count"], 1)

    def test_49_markdown_commonmark_flanking_bold_and_winapps_codex_freshness(self) -> None:
        import unicodedata

        ascii_punct = set(r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~""")

        def is_punct(ch: str) -> bool:
            return bool(ch) and (ch in ascii_punct or unicodedata.category(ch).startswith("P"))

        def is_space(ch: str) -> bool:
            return not ch or ch in " \t\n\r\f\v" or unicodedata.category(ch).startswith("Z")

        def mask_inline_code(line: str) -> str:
            chars = list(line)
            idx, n = 0, len(chars)
            while idx < n:
                if chars[idx] == "`":
                    run = 1
                    while idx + run < n and chars[idx + run] == "`":
                        run += 1
                    end = line.find("`" * run, idx + run)
                    if end != -1:
                        for pos in range(idx + run, end):
                            chars[pos] = "x"
                        idx = end + run
                        continue
                    idx += run
                else:
                    idx += 1
            return "".join(chars)

        def broken_bold_spans(md_text: str) -> list[tuple[int, str]]:
            issues: list[tuple[int, str]] = []
            in_fence = False
            fence_marker = ""
            for lineno, raw_line in enumerate(md_text.splitlines(), 1):
                stripped = raw_line.lstrip()
                m_fence = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
                if m_fence:
                    ticks, rest = m_fence.group(1), m_fence.group(2)
                    if not in_fence:
                        if ticks[0] not in rest:
                            in_fence = True
                            fence_marker = ticks
                        continue
                    if ticks[0] == fence_marker[0] and len(ticks) >= len(fence_marker) and not rest.strip():
                        in_fence = False
                        fence_marker = ""
                    continue
                if in_fence:
                    continue
                masked = mask_inline_code(raw_line)
                runs = [m.span() for m in re.finditer(r"\*{2,}", masked)]
                if len(runs) >= 2 and len(runs) % 2 == 0:
                    for idx in range(0, len(runs), 2):
                        s1, e1 = runs[idx]
                        s2, e2 = runs[idx + 1]
                        b1 = masked[s1 - 1] if s1 > 0 else ""
                        a1 = masked[e1] if e1 < len(masked) else ""
                        b2 = masked[s2 - 1] if s2 > 0 else ""
                        a2 = masked[e2] if e2 < len(masked) else ""
                        lf = (not is_space(a1)) and ((not is_punct(a1)) or is_space(b1) or is_punct(b1))
                        rf = (not is_space(b2)) and ((not is_punct(b2)) or is_space(a2) or is_punct(a2))
                        if not (lf and rf):
                            issues.append((lineno, raw_line[s1:e2]))
            return issues

        # 验证检测器对 GitHub 失败样例敏感，且全仓所有 Markdown 文件零违规
        self.assertTrue(broken_bold_spans("本项目获**中国国家留学基金委（CSC）**资助"))
        self.assertTrue(broken_bold_spans("专享**“全面更新”**闭环"))
        self.assertEqual(broken_bold_spans("本项目获**中国国家留学基金委**（**CSC**）资助，专享“**全面更新**”闭环"), [])

        md_files = sorted(
            p for p in REPO_ROOT.rglob("*.md")
            if not any(part in p.relative_to(REPO_ROOT).parts for part in (".git", "__pycache__", ".pytest_cache"))
        )
        self.assertGreaterEqual(len(md_files), 10)
        for md_path in md_files:
            rel = md_path.relative_to(REPO_ROOT).as_posix()
            self.assertEqual(broken_bold_spans(md_path.read_text(encoding="utf-8")), [], f"{rel} 存在 GitHub CommonMark 失效粗体定界符")

        # 验证 install.py 在 Windows 下遇到新版 MSIX Codex 包时自动覆盖旧版 AppData\Local\OpenAI\Codex\bin 缓存
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            old_cli = fake_home / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "old" / "codex.exe"
            msix_cli = Path(tmp) / "winapps" / "codex.exe"
            old_cli.parent.mkdir(parents=True)
            msix_cli.parent.mkdir(parents=True)
            old_cli.write_bytes(b"MZ-old")
            msix_cli.write_bytes(b"MZ-new")
            os.utime(old_cli, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
            os.utime(msix_cli, ns=(1_800_000_000_000_000_000, 1_800_000_000_000_000_000))
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(fake_home / "AppData" / "Local")}, clear=False):
                windows_os = mock.Mock(wraps=os)
                windows_os.name = "nt"
                windows_os.environ = os.environ
                with mock.patch.object(INSTALLER, "os", windows_os), \
                     mock.patch.object(INSTALLER, "_is_wsl", return_value=False), \
                     mock.patch.object(INSTALLER, "_probe_windows_wsl", return_value={"status": "checked", "apps": {}}), \
                     mock.patch.object(INSTALLER.Path, "home", return_value=fake_home), \
                     mock.patch.object(INSTALLER.shutil, "which", return_value=None), \
                     mock.patch.object(INSTALLER, "_find_windows_codex_package", return_value=(str(msix_cli), None)):
                    detected = INSTALLER.detect_installed_apps()
                    self.assertEqual(detected["codex"]["native_binary"], str(msix_cli))
                    standalone_cli = fake_home / ".local" / "bin" / "codex.exe"
                    standalone_cli.parent.mkdir(parents=True)
                    standalone_cli.write_bytes(b"MZ-standalone")
                    detected_user = INSTALLER.detect_installed_apps()
                    self.assertEqual(detected_user["codex"]["native_binary"], str(standalone_cli))

            uw_spec = importlib.util.spec_from_file_location("uw_headless_test", TEMPLATE_DIR / "hooks" / "user_words.py")
            uw_mod = importlib.util.module_from_spec(uw_spec)
            uw_spec.loader.exec_module(uw_mod)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(fake_home / ".claude")}, clear=True):
                self.assertFalse(uw_mod.claude_hook_session({"session_id": "sess-headless-01"})[3])
                self.assertFalse(uw_mod.codex_hook_session({"session_id": "sess-headless-01"})[3])
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(fake_home / ".claude"), "CAE_HARNESS_DEPTH": "1"}, clear=True):
                self.assertTrue(uw_mod.claude_hook_session({"session_id": "sess-headless-01"})[3])
                self.assertTrue(uw_mod.codex_hook_session({"session_id": "sess-headless-01"})[3])


    def test_50_v1152_shard4_env_isolation_ci_and_anchor_contracts(self) -> None:
        for stripped_key in STRIPPED_HOST_ENV_KEYS:
            self.assertNotIn(stripped_key, os.environ)
        self.assertFalse(any(k.upper().startswith(STRIPPED_HOST_ENV_PREFIXES) for k in os.environ))
        self.assertFalse(any(
            k.upper().startswith("USER_GUARD_") and k.upper() not in USER_GUARD_INSTALL_ENV_KEYS
            for k in os.environ
        ))


        benign = _collect_repo_benign_tokens(REPO_ROOT)
        for expected_tok in ("ubuntu-latest", "persist-credentials", "persist", "readonly-auditor", "workspace-write"):
            self.assertIn(expected_tok, benign)
        sample_yml = "with:\n  persist-credentials: false\nruns-on: ubuntu-latest\n"
        for colliding in ("persist-credentials", "persist", "ubuntu-latest", "readonly-auditor"):
            u = "" if colliding.casefold() in benign else colliding
            h = "" if colliding.casefold() in benign else colliding
            self.assertEqual(_sensitive_findings(sample_yml, user_name=u, hostname=h), [])
            self.assertIn("未脱敏主目录", _sensitive_findings("/home/" + colliding + "/solver", user_name=u, hostname=h))
            fake_home_vars = _path_spelling_variants(["/home/" + colliding])
            self.assertIn("本机路径", _sensitive_findings("cwd=" + "/home/" + colliding + "/proj", home_variants=fake_home_vars, user_name=u, hostname=h))

        md_sample = "# 导言\n## 一、一键安装与课题接入\n### 1. 常驻机制层（`fleet_profile.json`）\n## 导言\n"
        anchors = _extract_md_heading_anchors(md_sample)
        self.assertIn("导言", anchors)
        self.assertIn("导言-1", anchors)
        self.assertIn("一一键安装与课题接入", anchors)
        self.assertIn("1-常驻机制层fleet_profilejson", anchors)
        self.assertNotIn("不存在的锚点", anchors)

        ci_text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn('.replace("%", "%25")', ci_text)
        self.assertIn('"check-grounding", "--target", ".", "--strict"', ci_text)
        self.assertIn('report.get("errors") == []', ci_text)
        self.assertIn('set(report["summary"]["recommended_actions"]) <= {"refresh_codex_model_catalog"}', ci_text)
        self.assertIn('["git", "status", "--porcelain"]', ci_text)


    def test_51_v1152_r2_doc_skill_auditor_and_env_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            existing_dir = Path(tmp) / "installed_plugin"
            existing_dir.mkdir()
            existing_file = Path(tmp) / "not_a_dir.txt"
            existing_file.write_text("x", encoding="utf-8")
            self.assertEqual(_resolve_active_plugin_dir({}), TEMPLATE_DIR)
            self.assertEqual(_resolve_active_plugin_dir({"USER_GUARD_INSTALLED_DIR": ""}), TEMPLATE_DIR)
            self.assertEqual(_resolve_active_plugin_dir({"USER_GUARD_INSTALLED_DIR": "   "}), TEMPLATE_DIR)
            self.assertEqual(_resolve_active_plugin_dir({"USER_GUARD_INSTALLED_DIR": str(Path(tmp) / "missing")}), TEMPLATE_DIR)
            self.assertEqual(_resolve_active_plugin_dir({"USER_GUARD_INSTALLED_DIR": str(existing_file)}), TEMPLATE_DIR)
            self.assertEqual(_resolve_active_plugin_dir({"USER_GUARD_INSTALLED_DIR": str(existing_dir)}), existing_dir)
            self.assertFalse(_should_strip_host_env("USER_GUARD_INSTALLED_DIR", str(existing_dir)))
            self.assertFalse(_should_strip_host_env("USER_GUARD_INSTALLED_CODEX_DIR", str(existing_dir)))
            self.assertTrue(_should_strip_host_env("USER_GUARD_INSTALLED_DIR", ""))
            self.assertTrue(_should_strip_host_env("USER_GUARD_INSTALLED_DIR", str(Path(tmp) / "missing")))

        for polluted_key in (
            "PLUGIN_DATA", "USER_GUARD_STATE_DIR", "USER_GUARD_CUSTOM_FLAG",
            "AGY_CONVERSATION_ID", "ANTIGRAVITY_AGENT", "CODEX_THREAD_ID",
            "CLAUDECODE", "CLAUDE_SESSION_ID", "ANTHROPIC_API_KEY", "DSH_HOME",
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GIT_DIR", "GIT_WORK_TREE",
            "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES", "GIT_PREFIX",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "XDG_STATE_HOME", "PYTHONPATH",
        ):
            self.assertTrue(_should_strip_host_env(polluted_key, "polluted"), polluted_key)

        with mock.patch.dict(os.environ, {
            "GIT_DIR": "/bad", "GIT_WORK_TREE": "/bad", "GIT_INDEX_FILE": "/bad",
            "GIT_COMMON_DIR": "/bad", "GIT_OBJECT_DIRECTORY": "/bad",
        }):
            self.assertEqual(self._managed_repo_files("README.md"), ["README.md"])

        orig_exists = Path.exists

        def _hide_dot_git(path_obj: Path) -> bool:
            if path_obj == REPO_ROOT / ".git":
                return False
            return orig_exists(path_obj)

        with mock.patch.object(Path, "exists", autospec=True, side_effect=_hide_dot_git):
            self.assertEqual(self._managed_repo_files("README.md"), ["README.md"])
            self.assertEqual(
                self._managed_repo_files("template/user-guard/README.md"),
                ["template/user-guard/README.md"],
            )
            docs_only = self._managed_repo_files("docs")
            self.assertIn("docs/architecture-and-hooks.md", docs_only)
            self.assertIn("docs/workflow-guide.md", docs_only)
            self.assertTrue(all(item.startswith("docs/") for item in docs_only))
            wildcard_mds = self._managed_repo_files("*.md")
            self.assertIn("README.md", wildcard_mds)
            self.assertIn("template/user-guard/README.md", wildcard_mds)
            all_managed = self._managed_repo_files()
            self.assertIn("install.py", all_managed)
            self.assertIn("README.md", all_managed)

        # 按 R0 单点归属原则：单流省略 S1 销账细则由 cae-harness/SKILL.md 单点维护，不再强制广播复读进 6 份文档；
        # 全仓所有文档均不得残留旧版模糊表述。
        s1_clause = "省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代"
        s1_docs = (
            TEMPLATE_DIR / "rules" / "AGENTS.md",
            REPO_ROOT / "AGENTS.md",
            TEMPLATE_DIR / "README.md",
            REPO_ROOT / "docs" / "workflow-guide.md",
            TEMPLATE_DIR / "skills" / "cae-harness" / "SKILL.md",
            TEMPLATE_DIR / "skills" / "fleet-evolve" / "SKILL.md",
        )
        harness_skill_text = (TEMPLATE_DIR / "skills" / "cae-harness" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(s1_clause, harness_skill_text, "cae-harness/SKILL.md 须作为单流省略 S1 销账契约的唯一真源")
        for doc_path in s1_docs:
            rel_doc = doc_path.relative_to(REPO_ROOT).as_posix()
            doc_body = doc_path.read_text(encoding="utf-8")
            self.assertNotIn("可用模型名或任务状态替代", doc_body, f"{rel_doc} 残留旧版 S1 销账表述")
            self.assertNotIn("可用模型名或任务状态代替默认流号", doc_body, f"{rel_doc} 残留旧版 S1 销账表述")

        wf_text = (REPO_ROOT / "docs" / "workflow-guide.md").read_text(encoding="utf-8")
        self.assertNotIn("单轮 `gate`", wf_text)

        self.assertIn("禁止微调 `engine`", harness_skill_text)
        self.assertIn('"$HARNESS" probe --caller auto --redact', harness_skill_text)
        self.assertIn('"$HARNESS" assess --caller auto --redact', harness_skill_text)
        self.assertIn("还会校验 `--write-scope`（须为获批范围子集）与 `--verify-cmd` 一致性", harness_skill_text)
        self.assertNotIn("以下从技能目录运行", harness_skill_text)

        auditor_toml = tomllib.loads((REPO_ROOT / "template" / "codex" / "agents" / "readonly-auditor.toml").read_text(encoding="utf-8"))
        self.assertIn("需要编译、运行算例或执行脚本才能确认的事项统一交回主会话处理。", auditor_toml["developer_instructions"])
        self.assertNotIn("需要执行命令才能确认的事项", auditor_toml["developer_instructions"])

        arch_text = (REPO_ROOT / "docs" / "architecture-and-hooks.md").read_text(encoding="utf-8")
        self.assertIn("两侧均保留双引号内的 `$HOME`", arch_text)
        self.assertNotIn("改用 `python3 -B -X utf8` 与 `~`", arch_text)
        self.assertIsNone(re.search(r'"~[/\\]', arch_text))
        self.assertIn("活跃 `AWAITING_MASTER_RELOAD` Pass 2 重启锁及损坏状态文件", arch_text)
        self.assertIn("`sync_codex_model_defaults`", arch_text)
        self.assertIn("`sync_claude_model_defaults`", arch_text)

        # 全仓文档同时通过零黑名单事实锚定与通用文档卫生守卫（受众分层、计数一致、防补丁长句与跨文档复读）
        harness_py = TEMPLATE_DIR / "skills" / "cae-harness" / "scripts" / "cae_harness.py"
        hyg_proc = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(harness_py), "check-grounding", "--target", ".", "--strict", "--doc-hygiene"],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(hyg_proc.returncode, 0, hyg_proc.stdout + hyg_proc.stderr)
        hyg_rep = json.loads(hyg_proc.stdout)
        self.assertTrue(hyg_rep.get("ok"), hyg_rep)
        self.assertEqual(hyg_rep.get("finding_count"), 0, hyg_rep)

    def test_52_legal_governance_and_spdx_headers(self) -> None:
        """机械守卫开源法律治理文件完整度与全仓脚本 SPDX Apache-2.0 版权头。"""
        required_docs = (
            "LICENSE",
            "NOTICE",
            "LEGAL.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/compliance_and_ip_notice.md",
        )
        for rel in required_docs:
            doc_path = REPO_ROOT / rel
            self.assertTrue(doc_path.is_file(), f"缺少法律与开源治理文件: {rel}")
            self.assertGreater(doc_path.stat().st_size, 0, f"治理文件不能为空: {rel}")

        notice_text = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("Antigravity_Codex_ClaudeCode_DeepSeekHarness", notice_text)
        self.assertIn("Xinyu Li (Xinyu-Li-1997)", notice_text)
        self.assertNotIn("LEGAL.md", notice_text)
        legal_text = (REPO_ROOT / "LEGAL.md").read_text(encoding="utf-8")
        self.assertIn("Apache License 2.0", legal_text)
        self.assertIn("无官方关联", legal_text)
        self.assertIn("not affiliated with", legal_text)
        self.assertIn("按现状", legal_text)
        self.assertIn("第五百零六条", legal_text)
        self.assertIn("第一千一百九十五条", legal_text)
        self.assertIn("submitting-content-removal-requests", legal_text)
        citation_text = (REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8")
        self.assertNotIn("未接受任何其他单位或个人任何形式的资助", citation_text)
        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("LEGAL.md", readme_text)
        self.assertIn("SECURITY.md", readme_text)
        self.assertIn("CONTRIBUTING.md", readme_text)
        self.assertIn("CODE_OF_CONDUCT.md", readme_text)
        self.assertLessEqual(len((REPO_ROOT / "README.md").read_bytes()), 18000)

        script_files = [
            rel for rel in self._managed_repo_files()
            if rel.endswith((".py", ".sh", ".ps1"))
        ]
        self.assertGreaterEqual(len(script_files), 30)
        for rel in script_files:
            head = "\n".join((REPO_ROOT / rel).read_text(encoding="utf-8-sig").splitlines()[:6])
            self.assertIn("SPDX-License-Identifier: Apache-2.0", head, f"{rel} 缺少 SPDX Apache-2.0 许可头")
            self.assertRegex(head, r"Copyright\s+20\d\d", f"{rel} 缺少版权年份声明头")


if __name__ == "__main__":
    unittest.main(verbosity=2)
