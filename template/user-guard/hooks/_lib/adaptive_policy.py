# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""
adaptive_policy.py - Per-(agent, model) adaptive parameter resolution for hooks, context windows, and proxy cards.
Pure Python standard library only.
"""

import os
import re
from typing import Any, Dict, Optional, Tuple

_EFFORT_SCALES = {
    "low": 0.85,
    "medium": 1.0,
    "high": 1.15,
    "xhigh": 1.25,
    "max": 1.25,
}
_BASELINE_WINDOWS = {
    "antigravity": 200_000,
    "agy": 200_000,
    "codex": 272_000,
    "claude": 200_000,
    "dsh": 128_000,
}

_TIER_PAREN_RE = re.compile(
    r"\s*\((?:off|minimal|low|medium|med|high|xhigh|max|ultra|fast|thinking|standard)\)\s*$",
    re.IGNORECASE,
)
_TIER_SUFFIX_RE = re.compile(
    r"-(?:off|minimal|low|medium|med|high|xhigh|max|ultra)$",
    re.IGNORECASE,
)
_SMALL_CTX_SUFFIX_RE = re.compile(r"\[(\d+)k\]$", re.IGNORECASE)


def _normalize_client_name(client: str, default: str = "") -> str:
    c = (client or "").strip().lower()
    if not c:
        return default
    if c == "agy":
        return "antigravity"
    if c == "cc":
        return "claude"
    return c


def _strip_ctx_suffix(model_name: str, *, strip_1m: bool = True) -> str:
    pat = r"\[(?:1m|\d+k)\]$" if strip_1m else r"\[\d+k\]$"
    return re.sub(pat, "", (model_name or "").strip(), flags=re.IGNORECASE).strip()


def _normalize_model_family_base(model_name: str, *, strip_1m: bool = True) -> str:
    s = _strip_ctx_suffix(model_name, strip_1m=strip_1m).lower()
    if s.startswith("models/"):
        s = s[7:]
    s = _TIER_PAREN_RE.sub("", s).strip()
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return _TIER_SUFFIX_RE.sub("", s)


def _valid_window(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool) and val >= 32_000


def _parse_strict_pos_int(val: Any) -> Optional[int]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val if val > 0 else None
    if isinstance(val, str):
        s = val.strip()
        if s.isascii() and re.fullmatch(r"[0-9]+", s):
            iv = int(s)
            return iv if iv > 0 else None
    return None


def _clean_str(val: Any) -> str:
    return val.strip() if isinstance(val, str) else ""


def _extract_model_effort_hint(model_name: str) -> str:
    raw = _strip_ctx_suffix(model_name, strip_1m=True).strip()
    if not raw:
        return ""
    m_paren = _TIER_PAREN_RE.search(raw)
    if m_paren:
        tok = re.sub(r"[\s()]+", "", m_paren.group(0)).lower()
        tok = "medium" if tok == "med" else tok
        if tok in _EFFORT_SCALES:
            return tok
    m_suf = _TIER_SUFFIX_RE.search(raw)
    if m_suf:
        tok = m_suf.group(0).lstrip("-").lower()
        tok = "medium" if tok == "med" else tok
        if tok in _EFFORT_SCALES:
            return tok
    return ""


def extract_runtime_model_and_effort(
    client: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    data = payload if isinstance(payload, dict) else {}
    c_norm = _normalize_client_name(client)
    model = _clean_str(data.get("model")) or _clean_str(data.get("modelName")) or _clean_str(data.get("model_name"))
    effort = (
        _clean_str(data.get("effort"))
        or _clean_str(data.get("reasoning_effort"))
        or _clean_str(data.get("effortLevel"))
    )
    if not model:
        if c_norm == "claude":
            model = _clean_str(os.environ.get("ANTHROPIC_MODEL"))
        elif c_norm == "codex":
            model = _clean_str(os.environ.get("CODEX_MODEL"))
        elif c_norm == "antigravity":
            model = _clean_str(os.environ.get("AGY_MODEL")) or _clean_str(os.environ.get("ANTIGRAVITY_MODEL"))
    if not effort:
        if c_norm == "claude":
            effort = _clean_str(os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"))
        elif c_norm == "codex":
            effort = _clean_str(os.environ.get("CODEX_REASONING_EFFORT"))
        elif c_norm == "antigravity":
            effort = _clean_str(os.environ.get("AGY_EFFORT")) or _clean_str(os.environ.get("ANTIGRAVITY_EFFORT"))
    if not effort and model:
        effort = _extract_model_effort_hint(model)
    return model, effort


def _candidate_matches_query(
    cand_low: str,
    low_model: str,
    norm_target: str,
    family_target: str,
    has_1m_suffix: bool,
    stage: str,
) -> bool:
    if not cand_low:
        return False
    if stage == "exact":
        return cand_low == low_model
    # 禁止无 [1m] 后缀或带小窗口后缀的模型通过剥离后缀误命中 [1m] 专属条目
    if cand_low.endswith("[1m]") and not has_1m_suffix:
        return False
    if stage == "stripped":
        cand_stripped = _strip_ctx_suffix(cand_low, strip_1m=has_1m_suffix)
        return bool(norm_target and cand_stripped == norm_target)
    if stage == "family" and family_target:
        cand_family = _normalize_model_family_base(cand_low, strip_1m=has_1m_suffix)
        return bool(cand_family and cand_family == family_target)
    return False


def _extract_entry_window(entry: Dict[str, Any], has_1m_suffix: bool, has_1m_id: bool) -> Optional[int]:
    if has_1m_suffix:
        for cw_key in ("context_window", "max_context_window", "max_context_tokens"):
            cw = entry.get(cw_key)
            if _valid_window(cw) and int(cw) >= 1_000_000:
                return int(cw)
        return 1_000_000
    for cw_key in ("context_window", "max_context_window", "max_context_tokens"):
        cw = entry.get(cw_key)
        if _valid_window(cw):
            return int(cw)
    if has_1m_id:
        return 1_000_000
    return None


def _match_subscription_and_builtin(
    profile: Dict[str, Any],
    query_low: str,
    client: str = "",
) -> Tuple[Optional[Dict[str, Any]], bool, Optional[Dict[str, Any]], bool]:
    if not query_low:
        return None, False, None, False
    has_1m_suffix = query_low.endswith("[1m]")
    c_norm = _normalize_client_name(client)
    agent_key = "agy" if c_norm == "antigravity" else c_norm
    stripped = _strip_ctx_suffix(query_low, strip_1m=has_1m_suffix)
    family_target = _normalize_model_family_base(query_low, strip_1m=has_1m_suffix)

    sub_match: Optional[Dict[str, Any]] = None
    sub_1m_id = False
    sub = profile.get("subscription_matrix") if isinstance(profile.get("subscription_matrix"), dict) else {}
    models = sub.get("models") if isinstance(sub.get("models"), dict) else {}
    for stage in ("exact", "stripped", "family"):
        for k, v in models.items():
            if not isinstance(v, dict):
                continue
            v_agent_norm = _normalize_client_name(str(v.get("agent") or ""))
            v_agent_key = "agy" if v_agent_norm == "antigravity" else v_agent_norm
            if agent_key and v_agent_key and v_agent_key != agent_key:
                continue
            kl = str(k).strip().lower()
            pl = str(v.get("provider_model") or "").strip().lower()
            if kl.endswith("[1m]") and not has_1m_suffix and (not pl or pl != query_low):
                continue
            if _candidate_matches_query(kl, query_low, stripped, family_target, has_1m_suffix, stage) or (
                pl and _candidate_matches_query(pl, query_low, stripped, family_target, has_1m_suffix, stage)
            ):
                sub_match = v
                sub_1m_id = kl.endswith("[1m]") or pl.endswith("[1m]")
                break
        if sub_match is not None:
            break

    builtin_match: Optional[Dict[str, Any]] = None
    builtin_1m_id = False
    rules = profile.get("client_catalog_rules") if isinstance(profile.get("client_catalog_rules"), dict) else {}
    if client or agent_key:
        raw_c_rule = rules.get(client) or rules.get(c_norm) or rules.get(agent_key)
        candidate_rules = [raw_c_rule] if isinstance(raw_c_rule, dict) else []
    else:
        candidate_rules = [r for r in rules.values() if isinstance(r, dict)]

    for stage in ("exact", "stripped", "family"):
        for c_rule in candidate_rules:
            builtin = c_rule.get("builtin_models")
            if not isinstance(builtin, list):
                continue
            for item in builtin:
                if not isinstance(item, dict):
                    continue
                slug_low = str(item.get("slug") or item.get("model") or "").strip().lower()
                if slug_low.endswith("[1m]") and not has_1m_suffix:
                    continue
                for id_key in ("slug", "model", "name", "display_name"):
                    cand = str(item.get(id_key) or "").strip().lower()
                    if _candidate_matches_query(cand, query_low, stripped, family_target, has_1m_suffix, stage):
                        builtin_match = item
                        builtin_1m_id = cand.endswith("[1m]")
                        break
                if builtin_match is not None:
                    break
            if builtin_match is not None:
                break
        if builtin_match is not None:
            break

    return sub_match, sub_1m_id, builtin_match, builtin_1m_id


def infer_context_window(
    profile: Dict[str, Any],
    client: str,
    model_name: str = "",
    explicit_window: Optional[int] = None,
) -> int:
    if _valid_window(explicit_window):
        return int(explicit_window)  # type: ignore[arg-type]
    raw_model = (model_name or "").strip()
    low_model = raw_model.lower()
    m_small = _SMALL_CTX_SUFFIX_RE.search(low_model)
    if m_small:
        k_val = int(m_small.group(1)) * 1_000
        if _valid_window(k_val):
            return k_val

    has_1m_suffix = low_model.endswith("[1m]")
    c_norm = _normalize_client_name(client)
    agent_key = "agy" if c_norm == "antigravity" else c_norm
    if low_model == "unknown":
        low_model = ""
        has_1m_suffix = False

    if low_model:
        sub_match, sub_1m, builtin_match, builtin_1m = _match_subscription_and_builtin(profile, low_model, client)
        if sub_match is not None:
            cw = _extract_entry_window(sub_match, has_1m_suffix, sub_1m)
            if cw is not None:
                return cw
        if builtin_match is not None:
            cw = _extract_entry_window(builtin_match, has_1m_suffix, builtin_1m)
            if cw is not None:
                return cw

    if has_1m_suffix:
        return 1_000_000

    rules = profile.get("client_catalog_rules") if isinstance(profile.get("client_catalog_rules"), dict) else {}
    raw_c_rule = rules.get(client) or rules.get(c_norm) or rules.get(agent_key)
    c_rule = raw_c_rule if isinstance(raw_c_rule, dict) else {}

    fallback_cw_keys = (
        ("declared_max_context_window", "default_context_window", "max_context_tokens")
        if not low_model
        else ("declared_max_context_window", "default_context_window")
    )
    for cw_key in fallback_cw_keys:
        cw = c_rule.get(cw_key)
        if _valid_window(cw):
            return int(cw)

    native_profiles = profile.get("native_agent_profiles") if isinstance(profile.get("native_agent_profiles"), dict) else {}
    raw_np = native_profiles.get(c_norm) or native_profiles.get(client) or native_profiles.get(agent_key)
    if isinstance(raw_np, dict) and _valid_window(raw_np.get("context_window")):
        return int(raw_np["context_window"])

    return _BASELINE_WINDOWS.get(c_norm) or _BASELINE_WINDOWS.get(agent_key, 200_000)


def _lookup_model_meta(profile: Dict[str, Any], model_name: str, client: str = "") -> Dict[str, Any]:
    raw = (model_name or "").strip().lower()
    if not raw or raw == "unknown":
        return {}
    sub_match, _, builtin_match, _ = _match_subscription_and_builtin(profile, raw, client)
    if builtin_match and sub_match:
        return {**builtin_match, **sub_match}
    return sub_match or builtin_match or {}


def compute_window_scale(client: str, context_window: int) -> float:
    if isinstance(context_window, bool) or not isinstance(context_window, (int, float)):
        return 1.0
    c_norm = _normalize_client_name(client)
    agent_key = "agy" if c_norm == "antigravity" else c_norm
    base = _BASELINE_WINDOWS.get(c_norm) or _BASELINE_WINDOWS.get(agent_key, 200_000)
    if context_window <= 0 or base <= 0:
        return 1.0
    ratio = context_window / float(base)
    if ratio >= 4.0:
        return 1.5
    if ratio >= 2.0:
        return 1.25
    if ratio <= 0.65:
        return 0.75
    return 1.0


def resolve_adaptive_compaction_policy(
    profile: Dict[str, Any],
    client: str = "",
    model_name: str = "",
    effort: str = "",
    explicit_window: Optional[int] = None,
    defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    norm_client = _normalize_client_name(client)
    base_defaults: Dict[str, Any] = {
        key: (list(value) if isinstance(value, list) else value)
        for key, value in (defaults or {}).items()
    }
    tunables = profile.get("runtime_tunables") if isinstance(profile.get("runtime_tunables"), dict) else {}
    cr = tunables.get("compaction_recovery") if isinstance(tunables.get("compaction_recovery"), dict) else {}
    prof_def = cr.get("defaults") if isinstance(cr.get("defaults"), dict) else {}
    for k, v in prof_def.items():
        base_defaults[k] = list(v) if isinstance(v, list) else v
    per_client = cr.get("per_client") if isinstance(cr.get("per_client"), dict) else {}
    c_cfg = None
    if norm_client:
        cand_cfg = per_client.get(norm_client)
        if not isinstance(cand_cfg, dict) and norm_client == "antigravity":
            cand_cfg = per_client.get("agy")
        if not isinstance(cand_cfg, dict) and client:
            cand_cfg = per_client.get(client)
        if isinstance(cand_cfg, dict):
            c_cfg = cand_cfg
    if isinstance(c_cfg, dict):
        for k, v in c_cfg.items():
            base_defaults[k] = list(v) if isinstance(v, list) else v

    eff_explicit = None if isinstance(explicit_window, bool) else explicit_window
    eff_client = norm_client or "codex"
    if not model_name and eff_explicit is None and not effort:
        base_defaults["effective_context_window"] = infer_context_window(profile, eff_client, "", None)
        base_defaults["adaptive_scale"] = 1.0
        return base_defaults

    cw = infer_context_window(profile, eff_client, model_name, eff_explicit)
    meta = _lookup_model_meta(profile, model_name, eff_client)
    w_scale = compute_window_scale(eff_client, cw)
    e_scale = _EFFORT_SCALES.get((effort or "").strip().lower(), 1.0)
    combined_scale = max(0.65, min(1.75, w_scale * (1.0 + (e_scale - 1.0) * 0.4)))

    raw_ibs = meta.get("inline_budget_scale")
    if isinstance(raw_ibs, (int, float)) and not isinstance(raw_ibs, bool):
        combined_scale = max(0.5, min(2.0, float(raw_ibs)))

    if abs(combined_scale - 1.0) > 1e-6:
        for key, floor_val in (
            ("inline_total_chars", 1600),
            ("inline_user_item_chars", 400),
            ("inline_subagent_total_chars", 400),
            ("inline_subagent_item_chars", 160),
            ("archive_subagent_item_chars", 400),
        ):
            cur_val = base_defaults.get(key)
            if isinstance(cur_val, (int, float)) and not isinstance(cur_val, bool):
                base_defaults[key] = max(floor_val, int(round(cur_val * combined_scale)))

    base_defaults["effective_context_window"] = cw
    base_defaults["adaptive_scale"] = round(combined_scale, 3)
    return base_defaults


def resolve_adaptive_handoff_policy(
    profile: Dict[str, Any],
    client: str,
    model_name: str = "",
    effort: str = "",
    explicit_window: Optional[int] = None,
) -> Dict[str, Any]:
    norm_client = _normalize_client_name(client)
    tunables = profile.get("runtime_tunables") if isinstance(profile.get("runtime_tunables"), dict) else {}
    ht = tunables.get("handoff_thresholds") if isinstance(tunables.get("handoff_thresholds"), dict) else {}
    raw_ht_cfg = ht.get(norm_client)
    if not isinstance(raw_ht_cfg, dict) and norm_client == "antigravity":
        raw_ht_cfg = ht.get("agy")
    if not isinstance(raw_ht_cfg, dict) and client:
        raw_ht_cfg = ht.get(client)
    cfg = dict(raw_ht_cfg if isinstance(raw_ht_cfg, dict) else {})

    default_fallback = "advisory"
    raw_mode = str(cfg.get("default_mode") or default_fallback).strip().lower()
    mode = raw_mode if raw_mode in {"advisory", "enforce", "off"} else default_fallback
    raw_lim = cfg.get("default_compact_limit")
    limit = int(raw_lim) if isinstance(raw_lim, int) and not isinstance(raw_lim, bool) and raw_lim >= 0 else 0

    eff_explicit = None if isinstance(explicit_window, bool) else explicit_window
    eff_client = norm_client or client
    meta = _lookup_model_meta(profile, model_name, eff_client)
    cw = infer_context_window(profile, eff_client, model_name, eff_explicit)
    w_scale = compute_window_scale(eff_client, cw)

    if isinstance(meta.get("handoff_mode"), str) and meta["handoff_mode"].strip().lower() in {"advisory", "enforce", "off"}:
        mode = meta["handoff_mode"].strip().lower()
    if isinstance(meta.get("compact_limit_override"), int) and not isinstance(meta.get("compact_limit_override"), bool):
        limit = max(0, int(meta["compact_limit_override"]))
    elif limit > 0 and abs(w_scale - 1.0) > 1e-6:
        limit = max(1, int(round(limit * (1.0 + (w_scale - 1.0) * 0.5))))

    if norm_client == "antigravity" and abs(w_scale - 1.0) > 1e-6:
        for k in ("advisory_transcript_mib", "drain_transcript_mib"):
            cur_v = cfg.get(k)
            if isinstance(cur_v, (int, float)) and not isinstance(cur_v, bool):
                cfg[k] = max(1, int(round(cur_v * w_scale)))

    cfg["default_mode"] = mode
    cfg["default_compact_limit"] = limit
    cfg["effective_context_window"] = cw
    return cfg


def resolve_adaptive_card_params(
    profile: Dict[str, Any],
    port: int = 8241,
    kimi_model: Optional[str] = None,
    deepseek_model: Optional[str] = None,
    max_context: Optional[str] = None,
    compact_window: Optional[str] = None,
) -> Dict[str, Any]:
    """根据 fleet_profile.json 的 subscription_matrix.models 与 runtime_tunables 自适应派生代理卡参数。"""
    sub = profile.get("subscription_matrix") if isinstance(profile.get("subscription_matrix"), dict) else {}
    models = sub.get("models") if isinstance(sub.get("models"), dict) else {}
    rules = profile.get("client_catalog_rules") if isinstance(profile.get("client_catalog_rules"), dict) else {}
    cl_rules = rules.get("claude") if isinstance(rules.get("claude"), dict) else {}
    tunables = profile.get("runtime_tunables") if isinstance(profile.get("runtime_tunables"), dict) else {}
    hd = tunables.get("headless_dispatch") if isinstance(tunables.get("headless_dispatch"), dict) else {}
    router_cfg = hd.get("router") if isinstance(hd.get("router"), dict) else {}

    explicit_kimi = _clean_str(kimi_model)
    explicit_ds = _clean_str(deepseek_model)
    resolved_kimi = explicit_kimi or str(cl_rules.get("router_primary_model") or "kimi-k2.5").strip() or "kimi-k2.5"
    resolved_ds = explicit_ds or str(cl_rules.get("router_subagent_model") or "deepseek-chat").strip() or "deepseek-chat"
    kimi_ctx = infer_context_window(profile, "claude", explicit_kimi) if explicit_kimi else 262144
    ds_ctx = infer_context_window(profile, "claude", explicit_ds) if explicit_ds else 128000
    kimi_matched = False
    ds_matched = False

    for name, meta in models.items():
        if not isinstance(meta, dict) or _normalize_client_name(str(meta.get("agent") or "")) != "claude":
            continue
        if meta.get("enabled") is False or meta.get("enabled_in_profile") is False:
            continue
        nl = str(name).strip().lower()
        card = str(meta.get("card") or "").strip().lower()
        cw = meta.get("context_window")
        is_kimi = card == "kimi" or "kimi" in nl or bool(re.match(r"^k\d", nl))
        if is_kimi and not explicit_kimi:
            kimi_matched = True
            resolved_kimi = str(meta.get("provider_model") or name)
            if _valid_window(cw):
                kimi_ctx = int(cw)
            elif nl.endswith("[1m]") or resolved_kimi.lower().endswith("[1m]"):
                kimi_ctx = 1_000_000
        elif (card == "deepseek" or "deepseek" in nl) and not explicit_ds:
            ds_matched = True
            resolved_ds = str(meta.get("provider_model") or name)
            if _valid_window(cw):
                ds_ctx = int(cw)
            elif nl.endswith("[1m]") or resolved_ds.lower().endswith("[1m]"):
                ds_ctx = 1_000_000

    if not explicit_kimi and not kimi_matched and resolved_kimi.lower().endswith("[1m]"):
        kimi_ctx = 1_000_000
    if not explicit_ds and not ds_matched and resolved_ds.lower().endswith("[1m]"):
        ds_ctx = 1_000_000

    cl_rule_max = _parse_strict_pos_int(cl_rules.get("max_context_tokens"))
    cl_rule_compact = _parse_strict_pos_int(cl_rules.get("auto_compact_window"))
    any_active_or_explicit = bool(kimi_matched or ds_matched or explicit_kimi or explicit_ds)

    if any_active_or_explicit:
        candidates_ctx = []
        if explicit_kimi or kimi_matched:
            candidates_ctx.append(kimi_ctx)
        else:
            candidates_ctx.append(infer_context_window(profile, "claude", resolved_kimi) if resolved_kimi else kimi_ctx)
        if explicit_ds or ds_matched:
            candidates_ctx.append(ds_ctx)
        else:
            candidates_ctx.append(infer_context_window(profile, "claude", resolved_ds) if resolved_ds else ds_ctx)
        fallback_ctx = max(candidates_ctx)
        fallback_compact: Optional[int] = None
    else:
        fallback_ctx = cl_rule_max if cl_rule_max is not None else max(kimi_ctx, ds_ctx)
        fallback_compact = cl_rule_compact

    parsed_max = _parse_strict_pos_int(max_context)
    max_ctx_int = parsed_max if parsed_max is not None else fallback_ctx

    default_compact_int = (
        fallback_compact
        if (parsed_max is None and fallback_compact is not None)
        else (
            cl_rule_compact
            if (parsed_max is None and cl_rule_compact is not None and cl_rule_max is not None and cl_rule_max == max_ctx_int)
            else max(65536, int(round(max_ctx_int * 0.72)))
        )
    )
    parsed_compact = _parse_strict_pos_int(compact_window)
    compact_int = parsed_compact if parsed_compact is not None else default_compact_int

    timeout_s = _parse_strict_pos_int(router_cfg.get("timeout_sec")) or 900
    eff_port = port if (not isinstance(port, bool) and isinstance(port, int) and 1 <= port <= 65535) else 8241
    return {
        "port": eff_port,
        "kimi_model": resolved_kimi,
        "deepseek_model": resolved_ds,
        "max_context": str(max_ctx_int),
        "compact_window": str(compact_int),
        "timeout_sec": timeout_s,
    }
