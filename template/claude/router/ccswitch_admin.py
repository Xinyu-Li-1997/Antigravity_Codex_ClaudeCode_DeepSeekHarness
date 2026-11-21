#!/usr/bin/env python3
"""ccswitch_admin.py - CC Switch 供应商卡片配置、settings.json 同步与旧提示词清理工具（仅依赖 Python 标准库）。

子命令：
  * status          只读检查 cc-switch.db、Claude settings.json 及已启用的旧提示词或技能冲突
  * prepare-cards   原子创建或更新 Kimi、DeepSeek 与本地分流卡片（保留用户已填写的真实密钥）
  * switch-card     按 CC Switch 字段合并规则将目标 Claude 卡片同步到 settings.json
  * disable-prompts 停用 cc-switch.db 中已启用的提示词，避免旧内容覆盖全局 AGENTS.md 或 CLAUDE.md
  * retire-legacy   备份后停用或清空 CC Switch 内旧提示词与旧技能目录
  * verify          校验激活卡片与 settings.json 是否一致、推理强度是否被环境变量锁死及本地路由健康状态
"""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import urllib.error
from urllib.parse import urlsplit
import urllib.request
import uuid

_STDLIB_URLOPEN = urllib.request.urlopen
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MIN_SUPPORTED_SCHEMA = 23
TESTED_MAX_SCHEMA = 27

KIMI_PLACEHOLDER = "PASTE_KIMI_API_KEY_HERE"
DEEPSEEK_PLACEHOLDER = "PASTE_DEEPSEEK_API_KEY_HERE"

FLOOR_PREFIXES = ("ANTHROPIC_", "AWS_", "VERTEX_REGION_")
FLOOR_ENV_KEYS = {
    "CLAUDE_CODE_SUBAGENT_MODEL", "CLOUD_ML_REGION",
    "GOOGLE_APPLICATION_CREDENTIALS", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_OAUTH_SCOPES", "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
}
PROTOCOL_SELECTORS = {
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_GATEWAY", "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
}
EXCLUSIVE = {
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "CLAUDE_CODE_DISABLE_ARTIFACT", "ENABLE_TOOL_SEARCH",
    "CLAUDE_CODE_DISABLE_THINKING", "DISABLE_INTERLEAVED_THINKING", "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT",
    "CLAUDE_CODE_EXTRA_BODY", "CLAUDE_CODE_ENABLE_FINE_GRAINED_TOOL_STREAMING", "CLAUDE_CODE_AUTO_MODE_SERVER",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS", "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_DISABLE_1M_CONTEXT", "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
}
FLOOR_TOP = {
    "apiKeyHelper", "apiBaseUrl", "primaryModel", "smallFastModel", "apiKey", "model", "fallbackModel",
    "modelOverrides", "modelPicker", "advisorModel", "awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh",
}
RESIDUE_ENV = (
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
)


def default_router_port() -> int:
    doc = _load_fleet_profile_doc()
    if isinstance(doc, dict):
        rt = (((doc.get("runtime_tunables") or {}).get("headless_dispatch") or {}).get("router") or {})
        if isinstance(rt, dict):
            key = "port" if os.name == "nt" else "wsl_port"
            val = rt.get(key)
            if isinstance(val, int) and not isinstance(val, bool) and 1 <= val <= 65535:
                return val
    return 8240 if os.name == "nt" else 8241


def _read_fleet_profile_candidate(candidate: Path) -> Optional[Dict[str, Any]]:
    try:
        if not candidate.is_file() or _is_reparse_or_symlink(candidate):
            return None
        doc = json.loads(candidate.read_text(encoding="utf-8-sig"))
        if not isinstance(doc, dict):
            return None
        if "schema_version" in doc and (isinstance(doc["schema_version"], bool) or doc["schema_version"] != 1):
            return None
        return doc
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None


def _load_fleet_profile_doc() -> Dict[str, Any]:
    env_override = os.environ.get("CAE_FLEET_PROFILE", "").strip()
    if env_override:
        doc = _read_fleet_profile_candidate(Path(env_override).expanduser())
        return doc if doc is not None else {}
    here = Path(os.path.realpath(__file__))
    home = Path.home()
    codex_env = os.environ.get("CODEX_HOME", "").strip()
    dsh_env = os.environ.get("DSH_HOME", "").strip()
    codex_root = Path(codex_env).expanduser() if codex_env else (home / ".codex")
    dsh_root = Path(dsh_env).expanduser() if dsh_env else (home / ".dsh")
    candidates: List[Path] = [
        here.parent.parent / "skills" / "cae-harness" / "fleet_profile.json",
    ]
    if len(here.parents) > 2:
        candidates.append(here.parents[2] / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json")
    candidates.extend([
        home / ".gemini" / "config" / "plugins" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json",
        home / ".gemini" / "antigravity" / "plugins" / "user-guard" / "skills" / "cae-harness" / "fleet_profile.json",
        codex_root / "skills" / "cae-harness" / "fleet_profile.json",
        dsh_root / "skills" / "cae-harness" / "fleet_profile.json",
    ])
    for candidate in candidates:
        doc = _read_fleet_profile_candidate(candidate)
        if doc is not None:
            return doc
    return {}


def _claude_profile_defaults() -> Dict[str, str]:
    doc = _load_fleet_profile_doc()
    cl_rules: Dict[str, Any] = {}
    if isinstance(doc, dict) and isinstance(doc.get("client_catalog_rules"), dict):
        raw_cl = doc["client_catalog_rules"].get("claude")
        if isinstance(raw_cl, dict):
            cl_rules = raw_cl
    fields = {
        "kimi_model": "router_primary_model",
        "deepseek_model": "router_subagent_model",
        "max_context": "max_context_tokens",
        "compact_window": "auto_compact_window",
        "router_card_name": "router_card_name",
    }
    missing = [key for key in fields.values() if not cl_rules.get(key)]
    if missing:
        raise ValueError("fleet_profile.json missing Claude defaults: " + ", ".join(missing))
    return {key: str(cl_rules[field]) for key, field in fields.items()}


def floor_env(key: str) -> bool:
    return (
        key.startswith(FLOOR_PREFIXES)
        or key in FLOOR_ENV_KEYS
        or key in PROTOCOL_SELECTORS
        or (key.startswith("CLAUDE_CODE_SKIP_") and key.endswith("_AUTH"))
    )


def mask_token_state(raw_token: Any) -> str:
    if not isinstance(raw_token, str) or not raw_token.strip():
        return "missing"
    stripped = raw_token.strip()
    if stripped.lower() == "via-router":
        return "via-router"
    if stripped.upper().startswith("PASTE_"):
        return "placeholder"
    return "present"


def _sanitize_url(raw_url: Any) -> Optional[str]:
    if not isinstance(raw_url, str):
        return None
    stripped = raw_url.strip()
    if not stripped:
        return ""
    if not stripped.lower().startswith(("http://", "https://")):
        return "<redacted-url>" if ("@" in stripped or "?" in stripped or "#" in stripped) else stripped
    try:
        parsed = urlsplit(stripped)
        host = parsed.hostname or ""
        if not host:
            return "<redacted-url>"
        if ":" in host:
            host = "[" + host + "]"
        port_part = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port_part}{parsed.path}"
    except ValueError:
        return "<redacted-url>"


def _validate_nonlink_path(path: Path) -> Path:
    target = path.expanduser().absolute()
    if ".." in target.parts:
        raise ValueError(f"Refusing to write through parent traversal path: {path}")
    for candidate in (*reversed(target.parents), target):
        if _is_reparse_or_symlink(candidate):
            raise ValueError(f"Refusing to write through symbolic link or reparse point: {candidate}")
    return target


def atomic_write_bytes(path: Path, data: bytes) -> None:
    target = _validate_nonlink_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".claude-switch-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    atomic_write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def backup_sqlite_database(db_path: Path, backup_path: Path) -> None:
    _validate_nonlink_path(db_path)
    _validate_nonlink_path(backup_path)
    fd = os.open(str(backup_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)) as source:
            with closing(sqlite3.connect(str(backup_path), timeout=15)) as target:
                source.backup(target)
    except BaseException:
        if backup_path.exists():
            backup_path.unlink()
        raise


def _check_schema(con: sqlite3.Connection) -> Tuple[int, List[str]]:
    row = con.execute("PRAGMA user_version").fetchone()
    version = int(row[0]) if row else 0
    warnings: List[str] = []
    if version < MIN_SUPPORTED_SCHEMA:
        raise ValueError(f"Unsupported CC Switch schema {version} (< {MIN_SUPPORTED_SCHEMA})")
    if version > TESTED_MAX_SCHEMA:
        warnings.append(
            f"CC Switch schema {version} is newer than tested baseline {TESTED_MAX_SCHEMA}; verify column compatibility."
        )
    return version, warnings


def _table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


_WIN_LINK_REPARSE_TAGS = frozenset({
    getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
})


def _quote_sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if bool(getattr(st, "st_file_attributes", 0) & reparse_flag):
        tag = getattr(st, "st_reparse_tag", 0)
        return tag == 0 or tag in _WIN_LINK_REPARSE_TAGS
    return False


def _query_skills_state(con: sqlite3.Connection) -> Tuple[int, List[str], List[Dict[str, Any]]]:
    if not _table_exists(con, "skills"):
        return 0, [], []
    total = int(con.execute("SELECT COUNT(*) FROM skills").fetchone()[0])
    skill_cols = [r[1] for r in con.execute("PRAGMA table_info(skills)").fetchall()]
    enabled_cols = [c for c in skill_cols if c.startswith("enabled_")]
    enabled_skills: List[Dict[str, Any]] = []
    if {"id", "name"}.issubset(skill_cols) and enabled_cols:
        select_cols = ", ".join(['"id"', '"name"', *(_quote_sql_ident(c) for c in enabled_cols)])
        for row in con.execute(f"SELECT {select_cols} FROM skills ORDER BY id").fetchall():
            apps = [
                c.removeprefix("enabled_")
                for c, val in zip(enabled_cols, row[2:])
                if str(val) not in ("0", "", "None")
            ]
            if apps:
                enabled_skills.append({"id": row[0], "name": row[1], "enabled_apps": apps})
    return total, enabled_cols, enabled_skills


def _build_card_templates(
    port: int,
    kimi_model: Optional[str] = None,
    deepseek_model: Optional[str] = None,
    max_context: Optional[str] = None,
    compact_window: Optional[str] = None,
) -> Dict[str, Tuple[Any, Optional[str], str]]:
    defaults = _claude_profile_defaults()
    profile_doc = _load_fleet_profile_doc()
    sub_models = ((profile_doc or {}).get("subscription_matrix") or {}).get("models")
    has_kimi_sub = isinstance(sub_models, dict) and any(
        isinstance(m, dict)
        and m.get("agent") == "claude"
        and m.get("enabled") is not False
        and m.get("enabled_in_profile") is not False
        and (str(m.get("card") or "").strip().lower() == "kimi" or "kimi" in str(k).strip().lower() or bool(re.match(r"^k\d", str(k).strip().lower())))
        for k, m in sub_models.items()
    )
    has_ds_sub = isinstance(sub_models, dict) and any(
        isinstance(m, dict)
        and m.get("agent") == "claude"
        and m.get("enabled") is not False
        and m.get("enabled_in_profile") is not False
        and ("deepseek" in str(k).lower() or str(m.get("card") or "").strip().lower() == "deepseek")
        for k, m in sub_models.items()
    )
    has_claude_sub = has_kimi_sub or has_ds_sub
    if has_claude_sub:
        adaptive = derive_adaptive_card_defaults(
            profile_doc,
            port=port,
            kimi_model=kimi_model if kimi_model is not None else (None if has_kimi_sub else defaults["kimi_model"]),
            deepseek_model=deepseek_model if deepseek_model is not None else (None if has_ds_sub else defaults["deepseek_model"]),
            max_context=max_context,
            compact_window=compact_window,
        )
        kimi_model = str(adaptive["kimi_model"])
        deepseek_model = str(adaptive["deepseek_model"])
        max_context = str(adaptive["max_context"])
        compact_window = str(adaptive["compact_window"])
    else:
        kimi_model = kimi_model or defaults["kimi_model"]
        deepseek_model = deepseek_model or defaults["deepseek_model"]
        max_context = max_context or defaults["max_context"]
        compact_window = compact_window or defaults["compact_window"]
    router_card_name = defaults["router_card_name"]
    ds_base = deepseek_model.replace("[1m]", "").replace("[1M]", "")
    ds_main = f"{ds_base}[1M]"

    def kimi_env(token: str) -> Dict[str, str]:
        return {
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_BASE_URL": "https://api.kimi.com/coding/",
            "ANTHROPIC_DEFAULT_FABLE_MODEL": kimi_model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": kimi_model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": kimi_model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": kimi_model,
            "ANTHROPIC_MODEL": kimi_model,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": compact_window,
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": max_context,
            "CLAUDE_CODE_SUBAGENT_MODEL": kimi_model,
            "ENABLE_TOOL_SEARCH": "true",
        }

    def deepseek_env(token: str) -> Dict[str, str]:
        return {
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_DEFAULT_FABLE_MODEL": ds_base,
            "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME": ds_base,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": ds_base,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": ds_base,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": ds_base,
            "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": ds_base,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": ds_base,
            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": ds_base,
            "ANTHROPIC_MODEL": ds_main,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": compact_window,
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": max_context,
            "CLAUDE_CODE_SUBAGENT_MODEL": ds_base,
            "ENABLE_TOOL_SEARCH": "true",
        }

    def router_env() -> Dict[str, str]:
        return {
            "ANTHROPIC_AUTH_TOKEN": "via-router",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
            "ANTHROPIC_DEFAULT_FABLE_MODEL": kimi_model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": kimi_model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": kimi_model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": ds_base,
            "ANTHROPIC_MODEL": kimi_model,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": compact_window,
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": max_context,
            "CLAUDE_CODE_SUBAGENT_MODEL": ds_base,
            "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1",
            "ENABLE_TOOL_SEARCH": "true",
        }

    return {
        "Kimi": (kimi_env, KIMI_PLACEHOLDER, f"Kimi ({kimi_model}) direct connection."),
        router_card_name: (
            router_env,
            None,
            f"Kimi ({kimi_model}) main + DeepSeek ({ds_base}) subagent via local router 127.0.0.1:{port}.",
        ),
        "DeepSeek": (deepseek_env, DEEPSEEK_PLACEHOLDER, f"DeepSeek ({ds_main}) direct connection."),
    }


def project_claude_settings(
    original: Dict[str, Any],
    previous: Dict[str, Any],
    target: Dict[str, Any],
) -> Dict[str, Any]:
    for name, config in (("settings", original), ("previous card", previous), ("target card", target)):
        if not isinstance(config, dict) or not isinstance(config.get("env", {}), dict):
            raise ValueError(f"{name} and its env must be JSON objects")
    target = copy.deepcopy(target)
    target_env = target.setdefault("env", {})
    bedrock = target_env.get("CLAUDE_CODE_USE_BEDROCK")
    if (
        bedrock is True
        or bedrock == 1
        or (isinstance(bedrock, str) and bedrock.strip().lower() in ("1", "true"))
    ) and "apiKey" in target:
        target_env.setdefault("AWS_BEARER_TOKEN_BEDROCK", target.pop("apiKey"))
    updated = copy.deepcopy(original)
    for key in FLOOR_TOP:
        updated.pop(key, None)
    for key, value in target.items():
        if key != "env" and key in FLOOR_TOP:
            updated[key] = copy.deepcopy(value)
    env = updated.setdefault("env", {})
    for key in list(env):
        if floor_env(key):
            env.pop(key, None)
    previous_env = previous.get("env", {})
    for key, value in previous_env.items():
        if key in EXCLUSIVE and env.get(key) == value:
            env.pop(key, None)
    for key in RESIDUE_ENV:
        env.pop(key, None)
    for key, value in target.get("env", {}).items():
        if floor_env(key) or key in EXCLUSIVE:
            env[key] = copy.deepcopy(value)
    return updated


def inspect_status(
    db_path: Path,
    settings_path: Optional[Path] = None,
    router_port: Optional[int] = None,
) -> Dict[str, Any]:
    if router_port is not None and (
        isinstance(router_port, bool) or not isinstance(router_port, int) or not (1 <= router_port <= 65535)
    ):
        raise ValueError("router_port must be between 1 and 65535")
    report: Dict[str, Any] = {
        "db_path": str(db_path),
        "db_exists": db_path.is_file(),
        "schema_version": None,
        "cards": [],
        "current_claude_card": None,
        "enabled_prompts": [],
        "stored_prompt_count": 0,
        "enabled_skills": [],
        "stored_skill_count": 0,
        "skills_dir_entries": [],
        "settings_exists": bool(settings_path and settings_path.is_file()),
        "settings_aligned": None,
        "effort_level": None,
        "locked_effort_env": False,
        "router_health": None,
        "warnings": [],
    }
    active_cfg_obj: Optional[Dict[str, Any]] = None
    active_card_invalid_json = False
    claude_current_count = 0
    if db_path.is_file():
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as con:
            schema_ver, warnings = _check_schema(con)
            report["schema_version"] = schema_ver
            report["warnings"].extend(warnings)
            if _table_exists(con, "providers"):
                columns = {r[1] for r in con.execute("PRAGMA table_info(providers)")}
                quota_columns = ", ".join(
                    _quote_sql_ident(key) if key in columns else "NULL"
                    for key in ("meta", "limit_daily_usd", "limit_monthly_usd")
                )
                order_clause = "ORDER BY app_type, sort_index, name" if "sort_index" in columns else "ORDER BY app_type, name"
                rows = con.execute(
                    "SELECT id, app_type, name, settings_config, is_current, " + quota_columns
                    + " FROM providers " + order_clause
                ).fetchall()
                for row_id, app_type, name, raw_cfg, is_current, raw_meta, daily_cap, monthly_cap in rows:
                    try:
                        meta = json.loads(raw_meta) if raw_meta else {}
                    except (ValueError, TypeError, RecursionError):
                        meta = {}
                    usage = meta.get("usage_script") if isinstance(meta, dict) else None
                    scheme = usage.get("templateType") if isinstance(usage, dict) else None
                    invalid_json = False
                    cfg_obj: Any = {}
                    try:
                        cfg_obj = json.loads(raw_cfg) if raw_cfg else {}
                        if not isinstance(cfg_obj, dict) or not isinstance(cfg_obj.get("env", {}), dict):
                            invalid_json = True
                            cfg_obj = {}
                    except (ValueError, TypeError, RecursionError):
                        invalid_json = True
                        cfg_obj = {}
                    if invalid_json:
                        report["warnings"].append(f"Invalid JSON in provider card {app_type}:{name}")
                    env = cfg_obj.get("env") if isinstance(cfg_obj, dict) and isinstance(cfg_obj.get("env"), dict) else {}
                    raw_auth = env.get("ANTHROPIC_AUTH_TOKEN")
                    raw_api = env.get("ANTHROPIC_API_KEY")
                    token_raw = (
                        raw_auth
                        if isinstance(raw_auth, str) and raw_auth.strip()
                        else (raw_api if raw_api is not None else raw_auth)
                    )
                    entry = {
                        "id": row_id,
                        "app_type": app_type,
                        "name": name,
                        "current": bool(is_current),
                        "quota_scheme": scheme if scheme in ("token_plan", "balance") else None,
                        "quota_caps": [label for label, value in (
                            ("daily_cap", daily_cap), ("monthly_cap", monthly_cap)
                        ) if value is not None],
                        "base_url": _sanitize_url(env.get("ANTHROPIC_BASE_URL")),
                        "model": env.get("ANTHROPIC_MODEL"),
                        "subagent_model": env.get("CLAUDE_CODE_SUBAGENT_MODEL"),
                        "max_context": env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"),
                        "token": mask_token_state(token_raw) if app_type == "claude" else "n/a",
                    }
                    if invalid_json:
                        entry["invalid_json"] = True
                    report["cards"].append(entry)
                    if app_type == "claude" and is_current:
                        claude_current_count += 1
                        report["current_claude_card"] = entry
                        active_cfg_obj = cfg_obj
                        active_card_invalid_json = invalid_json
                if claude_current_count > 1:
                    report["multiple_current_claude_cards"] = True
                    report["warnings"].append(f"Multiple Claude provider cards ({claude_current_count}) have is_current=1.")
            if _table_exists(con, "prompts"):
                prompt_cols = {r[1] for r in con.execute("PRAGMA table_info(prompts)").fetchall()}
                report["stored_prompt_count"] = int(con.execute("SELECT COUNT(*) FROM prompts").fetchone()[0])
                if {"id", "app_type", "name", "enabled"}.issubset(prompt_cols):
                    p_rows = con.execute(
                        "SELECT id, app_type, name FROM prompts WHERE enabled != 0 ORDER BY app_type, id"
                    ).fetchall()
                    report["enabled_prompts"] = [
                        {"id": r[0], "app_type": r[1], "name": r[2]} for r in p_rows
                    ]
                    if report["enabled_prompts"]:
                        report["warnings"].append(
                            "Enabled prompts exist in cc-switch.db and may overwrite AGENTS.md or CLAUDE.md if triggered."
                        )
            total_skills, _, enabled_skills = _query_skills_state(con)
            report["stored_skill_count"] = total_skills
            report["enabled_skills"] = enabled_skills
            if enabled_skills:
                report["warnings"].append(
                    "Enabled skills exist in cc-switch.db and may overwrite client global skills directories if triggered."
                )
        skills_dir = db_path.parent / "skills"
        if _is_reparse_or_symlink(skills_dir):
            report["warnings"].append("CC Switch skills directory is a symbolic link or reparse point.")
        elif skills_dir.is_dir():
            report["skills_dir_entries"] = sorted(p.name for p in skills_dir.iterdir() if p.name != ".DS_Store")

    if settings_path and settings_path.is_file():
        settings_obj: Optional[Dict[str, Any]] = None
        try:
            parsed_settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            if not isinstance(parsed_settings, dict):
                raise ValueError("settings.json root must be an object")
            settings_obj = parsed_settings
        except (OSError, UnicodeError, ValueError, RecursionError):
            report["settings_aligned"] = False
            report["warnings"].append("Claude settings.json is unreadable or contains invalid JSON.")
        if settings_obj is not None:
            env_obj = settings_obj.get("env") if isinstance(settings_obj.get("env"), dict) else {}
            report["effort_level"] = settings_obj.get("effortLevel")
            report["locked_effort_env"] = "CLAUDE_CODE_EFFORT_LEVEL" in env_obj
            if report["locked_effort_env"]:
                report["warnings"].append("CLAUDE_CODE_EFFORT_LEVEL is set in settings.json env, locking interactive /effort.")
            cur = report["current_claude_card"]
            if claude_current_count > 1 or active_card_invalid_json:
                report["settings_aligned"] = False
                if active_card_invalid_json:
                    report["warnings"].append("Current active CC Switch Claude card contains invalid JSON.")
            elif cur and active_cfg_obj is not None:
                active_env = active_cfg_obj.get("env") if isinstance(active_cfg_obj.get("env"), dict) else {}
                try:
                    projected = project_claude_settings(settings_obj, {}, active_cfg_obj)
                    report["settings_aligned"] = (
                        projected == settings_obj
                        and env_obj.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE") == active_env.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE")
                    )
                except ValueError:
                    report["settings_aligned"] = False
                if not report["settings_aligned"]:
                    report["warnings"].append("Claude settings.json env differs from current active CC Switch card.")

    if router_port is not None:
        url = f"http://127.0.0.1:{router_port}/__router/health"
        try:
            open_fn = (
                urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    urllib.request.HTTPHandler(),
                    urllib.request.HTTPSHandler(),
                ).open
                if urllib.request.urlopen is _STDLIB_URLOPEN
                else urllib.request.urlopen
            )
            with open_fn(url, timeout=2.0) as resp:
                payload = json.loads(resp.read(65536).decode("utf-8"))
                report["router_health"] = {
                    "ok": bool(resp.status == 200 and isinstance(payload, dict) and payload.get("ok") is True),
                    "port": payload.get("port") if isinstance(payload, dict) else None,
                }
        except (OSError, ValueError, RecursionError, urllib.error.URLError) as exc:
            report["router_health"] = {"ok": False, "port": router_port, "error": type(exc).__name__}

    return report


def _resolve_card_params(
    profile: Dict[str, Any],
    port: int = 8241,
    kimi_model: Optional[str] = None,
    deepseek_model: Optional[str] = None,
    max_context: Optional[str] = None,
    compact_window: Optional[str] = None,
) -> Dict[str, Any]:
    sub = profile.get("subscription_matrix") if isinstance(profile.get("subscription_matrix"), dict) else {}
    models = sub.get("models") if isinstance(sub.get("models"), dict) else {}
    cat_rules = profile.get("client_catalog_rules") if isinstance(profile.get("client_catalog_rules"), dict) else {}
    cl_rules = cat_rules.get("claude") if isinstance(cat_rules.get("claude"), dict) else {}
    tunables = profile.get("runtime_tunables") if isinstance(profile.get("runtime_tunables"), dict) else {}
    hd = tunables.get("headless_dispatch") if isinstance(tunables.get("headless_dispatch"), dict) else {}
    router_cfg = hd.get("router") if isinstance(hd.get("router"), dict) else {}

    def_kimi = str(cl_rules.get("router_primary_model") or "").strip() or "kimi-k2.5"
    def_ds = str(cl_rules.get("router_subagent_model") or "").strip() or "deepseek-chat"
    rk = str(kimi_model or def_kimi)
    rd = str(deepseek_model or def_ds)

    raw_rule_max = cl_rules.get("max_context_tokens")
    rule_max = (
        int(raw_rule_max)
        if isinstance(raw_rule_max, int) and not isinstance(raw_rule_max, bool) and raw_rule_max >= 32000
        else None
    )
    one_m_ctx = rule_max if (rule_max is not None and rule_max >= 1_000_000) else 1_000_000
    k_ctx = rule_max if rule_max is not None else 262144
    d_ctx = 128000
    kimi_matched = False
    ds_matched = False

    for name, meta in models.items():
        if (
            not isinstance(meta, dict)
            or meta.get("agent") != "claude"
            or meta.get("enabled") is False
            or meta.get("enabled_in_profile") is False
        ):
            continue
        nl = str(name).strip().lower()
        card = str(meta.get("card") or "").strip().lower()
        cw = meta.get("context_window")
        is_kimi = card == "kimi" or "kimi" in nl or bool(re.match(r"^k\d", nl))
        if is_kimi and kimi_model is None:
            kimi_matched = True
            rk = str(meta.get("provider_model") or name)
            if isinstance(cw, int) and not isinstance(cw, bool) and cw >= 32000:
                k_ctx = int(cw)
            elif "[1m]" in nl or "[1m]" in rk.lower():
                k_ctx = one_m_ctx
            else:
                k_ctx = 262144
        elif (card == "deepseek" or "deepseek" in nl) and deepseek_model is None:
            ds_matched = True
            rd = str(meta.get("provider_model") or name)
            if isinstance(cw, int) and not isinstance(cw, bool) and cw >= 32000:
                d_ctx = int(cw)
            elif "[1m]" in nl or "[1m]" in rd.lower():
                d_ctx = one_m_ctx
            else:
                d_ctx = 128000

    if not kimi_matched and "[1m]" in rk.lower():
        k_ctx = max(k_ctx, one_m_ctx)
    if not ds_matched and "[1m]" in rd.lower():
        d_ctx = max(d_ctx, one_m_ctx)

    fallback_ctx = max(k_ctx, d_ctx) if (kimi_matched or ds_matched or rule_max is None) else max(rule_max, k_ctx, d_ctx)
    eff_max_int = fallback_ctx
    if max_context is not None and not isinstance(max_context, bool) and str(max_context).strip():
        try:
            parsed_max = int(str(max_context).strip())
            if parsed_max > 0:
                eff_max_int = parsed_max
        except (TypeError, ValueError):
            pass
    eff_max = str(eff_max_int)

    raw_rule_compact = cl_rules.get("auto_compact_window")
    rule_compact = (
        int(raw_rule_compact)
        if isinstance(raw_rule_compact, int) and not isinstance(raw_rule_compact, bool) and raw_rule_compact > 0
        else None
    )
    default_cmp_int = (
        rule_compact
        if (rule_compact is not None and rule_max is not None and rule_max == eff_max_int)
        else max(65536, int(round(eff_max_int * 0.72)))
    )
    eff_cmp_int = default_cmp_int
    if compact_window is not None and not isinstance(compact_window, bool) and str(compact_window).strip():
        try:
            parsed_cmp = int(str(compact_window).strip())
            if parsed_cmp > 0:
                eff_cmp_int = parsed_cmp
        except (TypeError, ValueError):
            pass
    eff_cmp = str(eff_cmp_int)

    raw_timeout = router_cfg.get("timeout_sec")
    timeout_s = 900
    if raw_timeout is not None and not isinstance(raw_timeout, bool):
        try:
            parsed_timeout = int(raw_timeout)
            if parsed_timeout > 0:
                timeout_s = parsed_timeout
        except (TypeError, ValueError):
            pass

    eff_port = 8241 if isinstance(port, bool) or not isinstance(port, int) else port
    return {
        "port": eff_port,
        "kimi_model": rk,
        "deepseek_model": rd,
        "max_context": eff_max,
        "compact_window": eff_cmp,
        "timeout_sec": timeout_s,
    }


def derive_adaptive_card_defaults(
    fleet_profile: Optional[Dict[str, Any]],
    port: int = 8241,
    kimi_model: Optional[str] = None,
    deepseek_model: Optional[str] = None,
    max_context: Optional[str] = None,
    compact_window: Optional[str] = None,
) -> Dict[str, Any]:
    """结合 fleet_profile.json 启用模型矩阵动态派生代理卡默认模型与上下文窗口。"""
    profile = fleet_profile if isinstance(fleet_profile, dict) else {}
    here = Path(os.path.realpath(__file__))
    hook_candidates = [here.parent, here.parent.parent / "hooks"]
    if len(here.parents) > 2:
        hook_candidates.append(here.parents[2] / "user-guard" / "hooks")
    for hooks_dir in hook_candidates:
        policy_path = hooks_dir / "_lib" / "adaptive_policy.py"
        if policy_path.is_file():
            try:
                import types
                mod = types.ModuleType("_ccswitch_adaptive_policy")
                mod.__file__ = str(policy_path)
                exec(compile(policy_path.read_text(encoding="utf-8"), str(policy_path), "exec"), mod.__dict__)
                return mod.resolve_adaptive_card_params(
                    profile, port=port, kimi_model=kimi_model, deepseek_model=deepseek_model,
                    max_context=max_context, compact_window=compact_window,
                )
            except (OSError, SyntaxError, ValueError, RuntimeError):
                break
    return _resolve_card_params(
        profile, port=port, kimi_model=kimi_model, deepseek_model=deepseek_model,
        max_context=max_context, compact_window=compact_window,
    )


def prepare_cards(
    db_path: Path,
    port: int,
    kimi_model: Optional[str] = None,
    deepseek_model: Optional[str] = None,
    max_context: Optional[str] = None,
    compact_window: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if not dry_run:
        _validate_nonlink_path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    cards_spec = _build_card_templates(port, kimi_model, deepseek_model, max_context, compact_window)
    timestamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns()}"
    backup_path: Optional[Path] = Path(f"{db_path}.bak-before-cards-{timestamp}")

    mode_flag = "ro" if dry_run else "rw"
    con = sqlite3.connect(db_path.resolve().as_uri() + f"?mode={mode_flag}", uri=True, timeout=15)
    try:
        if not dry_run:
            con.execute("BEGIN IMMEDIATE")
        schema_ver, warnings = _check_schema(con)
        if not _table_exists(con, "providers"):
            raise ValueError("providers table missing in cc-switch.db")
        if not dry_run:
            backup_sqlite_database(db_path, backup_path)

        columns = [row[1] for row in con.execute("PRAGMA table_info(providers)").fetchall()]
        official = con.execute(
            "SELECT * FROM providers WHERE app_type='claude' AND id='claude-official'"
        ).fetchone()
        template = dict(zip(columns, official)) if official else {}
        next_sort = (
            int(con.execute("SELECT COALESCE(MAX(sort_index), 0) FROM providers WHERE app_type='claude'").fetchone()[0])
            if "sort_index" in columns
            else 0
        )

        results: List[Dict[str, Any]] = []
        for name, (env_factory, placeholder, notes) in cards_spec.items():
            rows = con.execute(
                "SELECT * FROM providers WHERE app_type='claude' AND name=?",
                (name,),
            ).fetchall()
            if len(rows) > 1:
                raise ValueError(f"Duplicate Claude card: {name}")
            current = dict(zip(columns, rows[0])) if rows else None
            token = placeholder
            if current:
                raw_cfg = current.get("settings_config")
                cfg: Dict[str, Any] = {}
                invalid_old_cfg = False
                if raw_cfg is None or not isinstance(raw_cfg, str) or not raw_cfg.strip():
                    invalid_old_cfg = True
                else:
                    try:
                        parsed_cfg = json.loads(raw_cfg)
                        if isinstance(parsed_cfg, dict) and isinstance(parsed_cfg.get("env", {}), dict):
                            cfg = parsed_cfg
                        else:
                            invalid_old_cfg = True
                    except (ValueError, TypeError, RecursionError):
                        invalid_old_cfg = True
                if invalid_old_cfg:
                    warnings.append(f"Invalid settings_config in existing card claude:{name}; resetting to template defaults")
                old_env = cfg.get("env", {})
                for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
                    old_token = old_env.get(key)
                    if (
                        placeholder
                        and isinstance(old_token, str)
                        and old_token.strip()
                        and not old_token.strip().upper().startswith("PASTE_")
                        and old_token.strip().lower() != "via-router"
                    ):
                        token = old_token.strip()
                        break
            env = env_factory(token) if placeholder else env_factory()
            record = dict(current if current else template)
            record.update(
                {
                    "id": current["id"] if current else str(uuid.uuid4()),
                    "app_type": "claude",
                    "name": name,
                    "settings_config": json.dumps({"env": env}, ensure_ascii=False),
                    "website_url": None,
                    "category": "third_party",
                    "created_at": current.get("created_at") if current else int(time.time() * 1000),
                    "notes": notes,
                    "icon": None,
                    "icon_color": None,
                    "meta": "{}",
                    "is_current": current.get("is_current", 0) if current else 0,
                    "in_failover_queue": 0,
                    "cost_multiplier": record.get("cost_multiplier") or "1.0",
                    "limit_daily_usd": None,
                    "limit_monthly_usd": None,
                    "provider_type": None,
                }
            )
            if not dry_run:
                if current:
                    meta = current.get("meta")
                    try:
                        valid_meta = isinstance(meta, str) and bool(meta.strip()) and isinstance(json.loads(meta), dict)
                    except (ValueError, RecursionError):
                        valid_meta = False
                    update_pairs = [
                        ("settings_config", record["settings_config"]),
                        ("notes", notes),
                        ("category", "third_party"),
                        ("meta", meta if valid_meta else "{}"),
                        ("in_failover_queue", 0),
                    ]
                    valid_pairs = [(col, val) for col, val in update_pairs if col in columns]
                    set_clause = ", ".join(f"{_quote_sql_ident(col)}=?" for col, _ in valid_pairs)
                    con.execute(
                        f"UPDATE providers SET {set_clause} WHERE id=? AND app_type='claude'",
                        [val for _, val in valid_pairs] + [record["id"]],
                    )
                else:
                    next_sort += 1
                    record["sort_index"] = next_sort
                    insert_cols = [col for col in columns if col in record]
                    placeholders_sql = ",".join("?" for _ in insert_cols)
                    cols_sql = ",".join('"' + col.replace('"', '""') + '"' for col in insert_cols)
                    con.execute(
                        f"INSERT INTO providers ({cols_sql}) VALUES ({placeholders_sql})",
                        [record[col] for col in insert_cols],
                    )
            results.append(
                {
                    "name": name,
                    "action": "update" if current else "insert",
                    "current": bool(record["is_current"]),
                    "base_url": _sanitize_url(env.get("ANTHROPIC_BASE_URL")),
                    "model": env.get("ANTHROPIC_MODEL"),
                    "subagent": env.get("CLAUDE_CODE_SUBAGENT_MODEL"),
                    "context": env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"),
                    "token": mask_token_state(env.get("ANTHROPIC_AUTH_TOKEN")),
                }
            )
        if not dry_run:
            con.commit()
        return {
            "dry_run": dry_run,
            "schema_version": schema_ver,
            "backup": None if dry_run else str(backup_path),
            "cards": results,
            "warnings": warnings,
        }
    except BaseException:
        if not dry_run:
            con.rollback()
            if backup_path is not None and backup_path.exists():
                try:
                    backup_path.unlink()
                except OSError:
                    pass
        raise
    finally:
        con.close()


def switch_card(
    db_path: Path,
    settings_path: Path,
    card_name: str,
    dry_run: bool = False,
    require_router_alive: bool = False,
) -> Dict[str, Any]:
    if not dry_run:
        _validate_nonlink_path(db_path)
        _validate_nonlink_path(settings_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    if not settings_path.is_file():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")
    state_path = db_path.with_name("live-state.json")
    if state_path.exists():
        try:
            raw_state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise RuntimeError("Claude must be in direct mode (live-state.json is unreadable)") from exc
        if not isinstance(raw_state, dict):
            raise RuntimeError("Claude must be in direct mode (live-state.json root must be an object)")
        apps = raw_state.get("apps")
        if not isinstance(apps, dict):
            raise RuntimeError("Claude must be in direct mode (live-state.json apps must be an object)")
        state = apps.get("claude")
        if not isinstance(state, dict):
            raise RuntimeError("Claude must be in direct mode (live-state.json apps.claude must be an object)")
        if state.get("mode") == "proxy" or state.get("attached") or state.get("pending") is not None:
            raise RuntimeError("Claude must be in direct mode with no pending CC Switch operation")

    mode_flag = "ro" if dry_run else "rw"
    con = sqlite3.connect(db_path.resolve().as_uri() + f"?mode={mode_flag}", uri=True, timeout=15)
    try:
        # Probe local router health BEFORE acquiring SQLite BEGIN IMMEDIATE write lock.
        schema_ver, warnings = _check_schema(con)
        pre_rows = con.execute(
            "SELECT id, name, settings_config, is_current FROM providers WHERE app_type='claude'"
        ).fetchall()
        pre_targets = [r for r in pre_rows if r[1] == card_name]
        pre_current = [r for r in pre_rows if r[3]]
        if len(pre_targets) != 1 or len(pre_current) > 1:
            raise ValueError(f"Expected exactly one target card ({card_name!r}) and at most one current Claude card")
        pre_raw_target = pre_targets[0][2]
        if not isinstance(pre_raw_target, str):
            raise ValueError("Target card settings_config must be a JSON string")
        try:
            pre_target_cfg = json.loads(pre_raw_target)
        except RecursionError as exc:
            raise ValueError("Card or settings JSON exceeds recursion limit") from exc
        pre_base_url = (
            pre_target_cfg.get("env", {}).get("ANTHROPIC_BASE_URL")
            if isinstance(pre_target_cfg, dict) and isinstance(pre_target_cfg.get("env"), dict)
            else None
        )
        try:
            endpoint = urlsplit(pre_base_url) if isinstance(pre_base_url, str) else None
            ep_scheme = (endpoint.scheme or "").lower() if endpoint else ""
            ep_host = (endpoint.hostname or "").lower() if endpoint else ""
            ep_port = endpoint.port if endpoint else None
        except ValueError:
            endpoint, ep_scheme, ep_host, ep_port = None, "", "", None
        router_health_warning: Optional[str] = None
        if endpoint and ep_scheme in ("http", "https") and ep_host in ("127.0.0.1", "localhost", "::1"):
            host = "[::1]" if ep_host == "::1" else ep_host
            port = ep_port or (443 if ep_scheme == "https" else 80)
            health_url = f"{ep_scheme}://{host}:{port}/__router/health"
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                     urllib.request.HTTPHandler(), urllib.request.HTTPSHandler())
                with opener.open(health_url, timeout=2.0) as resp:
                    health = json.loads(resp.read(65536).decode("utf-8"))
                    alive = resp.status == 200 and isinstance(health, dict) and health.get("ok") is True
            except (OSError, ValueError, RecursionError, urllib.error.URLError):
                alive = False
            if not alive:
                router_health_warning = "Local router /__router/health is unreachable or unhealthy; switching may leave Claude without a working route"
                if require_router_alive:
                    raise RuntimeError(router_health_warning)

        if not dry_run:
            con.execute("BEGIN IMMEDIATE")
        schema_ver, warnings = _check_schema(con)
        if router_health_warning:
            warnings.append(router_health_warning)
        rows = con.execute(
            "SELECT id, name, settings_config, is_current FROM providers WHERE app_type='claude'"
        ).fetchall()
        targets = [r for r in rows if r[1] == card_name]
        current = [r for r in rows if r[3]]
        if len(targets) != 1 or len(current) > 1:
            raise ValueError(f"Expected exactly one target card ({card_name!r}) and at most one current Claude card")
        target_id, _, raw_target, _ = targets[0]
        previous: Dict[str, Any] = {}
        if current:
            prev_name, raw_prev = current[0][1], current[0][2]
            invalid_prev = False
            if raw_prev is None or not isinstance(raw_prev, str) or not raw_prev.strip():
                invalid_prev = True
            else:
                try:
                    parsed_prev = json.loads(raw_prev)
                    if isinstance(parsed_prev, dict) and isinstance(parsed_prev.get("env", {}), dict):
                        previous = parsed_prev
                    else:
                        invalid_prev = True
                except (ValueError, TypeError, RecursionError):
                    invalid_prev = True
            if invalid_prev:
                warnings.append(
                    f"Previous active Claude card {prev_name!r} had invalid settings_config; fallback to empty baseline"
                )
        if not isinstance(raw_target, str):
            raise ValueError("Target card settings_config must be a JSON string")
        original_bytes = settings_path.read_bytes()
        try:
            target_cfg = json.loads(raw_target)
            original_cfg = json.loads(original_bytes.decode("utf-8-sig"))
        except RecursionError as exc:
            raise ValueError("Card or settings JSON exceeds recursion limit") from exc
        tx_base_url = (
            target_cfg.get("env", {}).get("ANTHROPIC_BASE_URL")
            if isinstance(target_cfg, dict) and isinstance(target_cfg.get("env"), dict)
            else None
        )
        if require_router_alive and tx_base_url != pre_base_url:
            raise RuntimeError("Target card ANTHROPIC_BASE_URL changed concurrently before lock acquisition; retry switch-card")
        updated = project_claude_settings(
            original_cfg,
            previous,
            target_cfg,
        )
        if dry_run:
            return {
                "dry_run": True,
                "schema_version": schema_ver,
                "card": card_name,
                "previous": current[0][1] if current else None,
                "model": updated.get("env", {}).get("ANTHROPIC_MODEL"),
                "subagent": updated.get("env", {}).get("CLAUDE_CODE_SUBAGENT_MODEL"),
                "warnings": warnings,
            }
        timestamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns()}"
        db_backup = Path(f"{db_path}.bak-before-live-switch-{timestamp}")
        settings_backup = Path(f"{settings_path}.bak-before-live-switch-{timestamp}")
        _validate_nonlink_path(settings_path)
        _validate_nonlink_path(settings_backup)
        backup_sqlite_database(db_path, db_backup)
        sb_fd = os.open(str(settings_backup), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(sb_fd, "wb") as sb_handle:
                sb_fd = -1
                sb_handle.write(original_bytes)
                sb_handle.flush()
                os.fsync(sb_handle.fileno())
            if os.name == "posix":
                os.chmod(settings_backup, 0o600)
        finally:
            if sb_fd >= 0:
                os.close(sb_fd)
        published = False
        restore_ok = True
        try:
            con.execute("UPDATE providers SET is_current=0 WHERE app_type='claude'")
            changed = con.execute(
                "UPDATE providers SET is_current=1 WHERE app_type='claude' AND id=?",
                (target_id,),
            )
            if changed.rowcount != 1:
                raise RuntimeError("Target card disappeared during switch")
            atomic_write_json(settings_path, updated)
            published = True
            con.commit()
        except BaseException:
            try:
                if published:
                    try:
                        atomic_write_bytes(settings_path, original_bytes)
                    except BaseException:
                        restore_ok = False
                        raise
            finally:
                con.rollback()
                if restore_ok:
                    for orphan in (db_backup, settings_backup):
                        if orphan.exists():
                            try:
                                orphan.unlink()
                            except OSError:
                                pass
            raise
        return {
            "dry_run": False,
            "schema_version": schema_ver,
            "card": card_name,
            "previous": current[0][1] if current else None,
            "model": updated.get("env", {}).get("ANTHROPIC_MODEL"),
            "subagent": updated.get("env", {}).get("CLAUDE_CODE_SUBAGENT_MODEL"),
            "resident_effort": updated.get("env", {}).get("CLAUDE_CODE_EFFORT_LEVEL"),
            "db_backup": str(db_backup),
            "settings_backup": str(settings_backup),
            "warnings": warnings,
        }
    finally:
        con.close()


def disable_prompts(
    db_path: Path,
    app_types: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    if not dry_run:
        _validate_nonlink_path(db_path)
    if not db_path.is_file():
        return {"db_exists": False, "dry_run": dry_run, "disabled": [], "backup": None, "warnings": []}

    mode_flag = "ro" if dry_run else "rw"
    backup_path: Optional[Path] = None
    con = sqlite3.connect(db_path.resolve().as_uri() + f"?mode={mode_flag}", uri=True, timeout=15)
    try:
        if not dry_run:
            con.execute("BEGIN IMMEDIATE")
        schema_ver, warnings = _check_schema(con)
        if not _table_exists(con, "prompts"):
            if not dry_run:
                con.rollback()
            return {
                "db_exists": True,
                "schema_version": schema_ver,
                "dry_run": dry_run,
                "disabled": [],
                "backup": None,
                "warnings": warnings,
            }
        if app_types:
            placeholders = ",".join("?" for _ in app_types)
            rows = con.execute(
                f"SELECT id, app_type, name FROM prompts WHERE enabled != 0 AND app_type IN ({placeholders}) ORDER BY app_type, id",
                list(app_types),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, app_type, name FROM prompts WHERE enabled != 0 ORDER BY app_type, id"
            ).fetchall()
        disabled = [{"id": r[0], "app_type": r[1], "name": r[2]} for r in rows]
        if not disabled or dry_run:
            if not dry_run:
                con.rollback()
            return {
                "db_exists": True,
                "schema_version": schema_ver,
                "dry_run": dry_run,
                "disabled": disabled,
                "backup": None,
                "warnings": warnings,
            }
        timestamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns()}"
        backup_path = Path(f"{db_path}.bak-before-disable-prompts-{timestamp}")
        backup_sqlite_database(db_path, backup_path)
        if app_types:
            placeholders = ",".join("?" for _ in app_types)
            con.execute(
                f"UPDATE prompts SET enabled = 0 WHERE enabled != 0 AND app_type IN ({placeholders})",
                list(app_types),
            )
        else:
            con.execute("UPDATE prompts SET enabled = 0 WHERE enabled != 0")
        con.commit()
        return {
            "db_exists": True,
            "schema_version": schema_ver,
            "dry_run": False,
            "disabled": disabled,
            "backup": str(backup_path),
            "warnings": warnings,
        }
    except BaseException:
        if not dry_run:
            con.rollback()
            if backup_path is not None and backup_path.exists():
                try:
                    backup_path.unlink()
                except OSError:
                    pass
        raise
    finally:
        con.close()


def retire_prompts_and_skills(
    db_path: Path,
    purge: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    if not dry_run:
        _validate_nonlink_path(db_path)
    skills_dir = db_path.parent / "skills"
    archive_parent = db_path.parent / "backups"
    if _is_reparse_or_symlink(skills_dir) or _is_reparse_or_symlink(archive_parent):
        raise ValueError("Refusing to operate on symbolic link or reparse point under ~/.cc-switch")
    dir_entries = sorted(p.name for p in skills_dir.iterdir() if p.name != ".DS_Store") if skills_dir.is_dir() else []
    if not db_path.is_file():
        return {
            "db_exists": False, "dry_run": dry_run, "purge": purge,
            "disabled_prompts": [], "disabled_skills": [],
            "purged_prompts": 0, "purged_skills": 0,
            "archived_skills_dir": None, "skills_dir_entries": dir_entries if purge else [],
            "backup": None, "warnings": [],
        }

    mode_flag = "ro" if dry_run else "rw"
    backup_path: Optional[Path] = None
    con = sqlite3.connect(db_path.resolve().as_uri() + f"?mode={mode_flag}", uri=True, timeout=15)
    try:
        if not dry_run:
            con.execute("BEGIN IMMEDIATE")
        schema_ver, warnings = _check_schema(con)
        disabled_prompts: List[Dict[str, Any]] = []
        total_prompts = 0

        if _table_exists(con, "prompts"):
            total_prompts = int(con.execute("SELECT COUNT(*) FROM prompts").fetchone()[0])
            p_cols = {r[1] for r in con.execute("PRAGMA table_info(prompts)").fetchall()}
            if {"id", "app_type", "name", "enabled"}.issubset(p_cols):
                rows = con.execute("SELECT id, app_type, name FROM prompts WHERE enabled != 0 ORDER BY app_type, id").fetchall()
                disabled_prompts = [{"id": r[0], "app_type": r[1], "name": r[2]} for r in rows]

        total_skills, enabled_cols, disabled_skills = _query_skills_state(con)

        db_changes = bool(total_prompts or total_skills) if purge else bool(disabled_prompts or disabled_skills)
        fs_changes = bool(purge and skills_dir.is_dir())
        if dry_run or not (db_changes or fs_changes):
            if not dry_run:
                con.rollback()
            return {
                "db_exists": True, "schema_version": schema_ver, "dry_run": dry_run, "purge": purge,
                "disabled_prompts": disabled_prompts, "disabled_skills": disabled_skills,
                "purged_prompts": total_prompts if purge else 0,
                "purged_skills": total_skills if purge else 0,
                "archived_skills_dir": None,
                "skills_dir_entries": dir_entries if purge else [],
                "backup": None, "warnings": warnings,
            }

        timestamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns()}"
        if db_changes:
            backup_tag = "retire-legacy" if purge else "disable-prompts"
            backup_path = Path(f"{db_path}.bak-before-{backup_tag}-{timestamp}")
            backup_sqlite_database(db_path, backup_path)
            if purge:
                if _table_exists(con, "prompts"):
                    con.execute("DELETE FROM prompts")
                if _table_exists(con, "skills"):
                    con.execute("DELETE FROM skills")
            else:
                if disabled_prompts:
                    con.execute("UPDATE prompts SET enabled = 0 WHERE enabled != 0")
                if disabled_skills and enabled_cols:
                    set_zero = ", ".join(f"{_quote_sql_ident(c)} = 0" for c in enabled_cols)
                    where_any = " OR ".join(f"({_quote_sql_ident(c)} IS NOT NULL AND CAST({_quote_sql_ident(c)} AS TEXT) NOT IN ('0', ''))" for c in enabled_cols)
                    con.execute(f"UPDATE skills SET {set_zero} WHERE {where_any}")

        archived_skills_dir: Optional[Path] = None
        if fs_changes:
            archive_parent.mkdir(parents=True, exist_ok=True)
            if _is_reparse_or_symlink(archive_parent):
                raise ValueError("Refusing to archive into symbolic link or reparse point")
            archived_skills_dir = archive_parent / f"retired-skills-{timestamp}"
            shutil.move(str(skills_dir), str(archived_skills_dir))

        try:
            if db_changes:
                con.commit()
            else:
                con.rollback()
        except BaseException:
            if archived_skills_dir and archived_skills_dir.exists() and not skills_dir.exists():
                shutil.move(str(archived_skills_dir), str(skills_dir))
            raise

        return {
            "db_exists": True, "schema_version": schema_ver, "dry_run": False, "purge": purge,
            "disabled_prompts": disabled_prompts, "disabled_skills": disabled_skills,
            "purged_prompts": total_prompts if purge else 0,
            "purged_skills": total_skills if purge else 0,
            "archived_skills_dir": str(archived_skills_dir) if archived_skills_dir else None,
            "skills_dir_entries": dir_entries if purge else [],
            "backup": str(backup_path) if backup_path else None, "warnings": warnings,
        }
    except BaseException:
        if not dry_run:
            con.rollback()
            if backup_path is not None and backup_path.exists():
                try:
                    backup_path.unlink()
                except OSError:
                    pass
        raise
    finally:
        con.close()


def verify_alignment(
    db_path: Path,
    settings_path: Path,
    router_port: Optional[int] = None,
) -> Tuple[bool, Dict[str, Any]]:
    status = inspect_status(db_path, settings_path, router_port=router_port)
    errors: List[str] = []
    if not status["db_exists"]:
        errors.append("cc-switch.db does not exist")
    if not status["settings_exists"]:
        errors.append("Claude settings.json does not exist")
    if status["enabled_prompts"]:
        errors.append(f"cc-switch.db still has {len(status['enabled_prompts'])} enabled prompt(s)")
    if status["enabled_skills"]:
        errors.append(f"cc-switch.db still has {len(status['enabled_skills'])} enabled skill(s)")
    if status["locked_effort_env"]:
        errors.append("CLAUDE_CODE_EFFORT_LEVEL is locked in settings.json env")
    if status["db_exists"] and status["current_claude_card"] is None:
        errors.append("cc-switch.db has no active Claude provider card (is_current=1)")
    if status.get("multiple_current_claude_cards"):
        errors.append("cc-switch.db has multiple active Claude provider cards (is_current=1)")
    if status["settings_aligned"] is not True and status["db_exists"] and status["settings_exists"]:
        errors.append("Claude settings.json env does not match current CC Switch card")
    if router_port is not None and not (status.get("router_health") or {}).get("ok"):
        errors.append(f"Local router on 127.0.0.1:{router_port} failed health check")
    status["ok"] = len(errors) == 0
    status["errors"] = errors
    return status["ok"], status


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    common_opts = argparse.ArgumentParser(add_help=False)
    common_opts.add_argument("--db", default=argparse.SUPPRESS, help="Path to cc-switch.db")
    common_opts.add_argument("--settings", default=argparse.SUPPRESS, help="Path to Claude settings.json")

    parser = argparse.ArgumentParser(description="CC Switch & Claude Code card/prompt management CLI")
    parser.add_argument("--db", default="~/.cc-switch/cc-switch.db", help="Path to cc-switch.db")
    parser.add_argument("--settings", default="~/.claude/settings.json", help="Path to Claude settings.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", parents=[common_opts], help="Inspect CC Switch DB, Claude settings, and prompt status")
    p_status.add_argument("--check-router-port", type=int, default=None)

    p_prep = sub.add_parser("prepare-cards", parents=[common_opts], help="Create or update Kimi, DeepSeek, and hybrid router cards")
    p_prep.add_argument("--port", type=int, default=default_router_port())
    p_prep.add_argument("--kimi-model", default=None)
    p_prep.add_argument("--deepseek-model", default=None)
    p_prep.add_argument("--max-context", default=None)
    p_prep.add_argument("--compact-window", default=None)
    p_prep.add_argument("--dry-run", action="store_true")

    p_switch = sub.add_parser("switch-card", parents=[common_opts], help="Switch active Claude card and project into settings.json")
    p_switch.add_argument("card", help="Target Claude card name")
    p_switch.add_argument("--dry-run", action="store_true")
    p_switch.add_argument("--require-router-alive", action="store_true")

    p_prompts = sub.add_parser("disable-prompts", parents=[common_opts], help="Disable enabled prompts in cc-switch.db")
    p_prompts.add_argument(
        "--app-type",
        action="append",
        choices=("claude", "codex", "agy", "opencode", "gemini"),
        help="Optional app_type filter (repeatable; default disables all enabled prompts)",
    )
    p_prompts.add_argument("--dry-run", action="store_true")

    p_retire = sub.add_parser("retire-legacy", parents=[common_opts], help="Disable or purge retired prompts and skills in CC Switch")
    p_retire.add_argument("--purge", action="store_true", help="Backup and delete retired prompt/skill rows and ~/.cc-switch/skills")
    p_retire.add_argument("--dry-run", action="store_true")

    p_verify = sub.add_parser("verify", parents=[common_opts], help="Verify alignment and prompt/skill safety")
    p_verify.add_argument("--check-router-port", type=int, default=None)

    args = parser.parse_args(argv)
    db_path = Path(args.db).expanduser()
    settings_path = Path(args.settings).expanduser()

    if args.cmd == "status":
        out = inspect_status(db_path, settings_path, router_port=args.check_router_port)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "prepare-cards":
        out = prepare_cards(
            db_path,
            port=args.port,
            kimi_model=args.kimi_model,
            deepseek_model=args.deepseek_model,
            max_context=args.max_context,
            compact_window=args.compact_window,
            dry_run=args.dry_run,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "switch-card":
        out = switch_card(db_path, settings_path, args.card, dry_run=args.dry_run,
                          require_router_alive=args.require_router_alive)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "disable-prompts":
        out = disable_prompts(db_path, app_types=args.app_type, dry_run=args.dry_run)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "retire-legacy":
        out = retire_prompts_and_skills(db_path, purge=args.purge, dry_run=args.dry_run)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "verify":
        ok, out = verify_alignment(db_path, settings_path, router_port=args.check_router_port)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        err_msg = str(exc)
        if isinstance(exc, sqlite3.OperationalError) and "locked" in err_msg.lower():
            err_msg = f"{err_msg} — 请先关闭 CC Switch GUI 后重试 (close CC Switch GUI and retry)"
        sys.stderr.write(
            json.dumps({"ok": False, "error_type": type(exc).__name__, "error": err_msg}, ensure_ascii=False) + "\n"
        )
        raise SystemExit(1)
