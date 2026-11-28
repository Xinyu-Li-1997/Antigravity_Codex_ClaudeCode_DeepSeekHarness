# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_axiom_lib.contract - 单源严格 Schema、双正交轴枚举、双哈希原语、单案文件锁与路径安全。"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# 一、核心拓扑常量、双正交轴与单源枚举

SCHEMA_VERSION = 3

PROFILES: Tuple[str, ...] = ("core", "cae")

CORE_STAGES: Tuple[str, ...] = (
    "L0_WELLFORMED",
    "L1_ROOT_CONSISTENCY",
    "L2_CONSTRAINT_ROLE",
    "L3_EMPIRICAL_EXIT",
)

STAGES: Tuple[str, ...] = (
    "S0_TYPING",
    "S1_ROOT_MEASURE",
    "S2_SYMMETRY_JUMP",
    "S3_CLOSURE_CONSTRAINT",
    "S4_LOCAL_SPECTRAL_VV",
)

ALL_STAGES: Tuple[str, ...] = CORE_STAGES + STAGES

PROFILE_STAGES: Dict[str, Tuple[str, ...]] = {
    "core": CORE_STAGES,
    "cae": STAGES,
}

ROOT_HALT_STAGES = frozenset({"S0_TYPING", "S1_ROOT_MEASURE", "S2_SYMMETRY_JUMP"})

PROFILE_ROOT_HALT_STAGES: Dict[str, frozenset[str]] = {
    "core": frozenset({"L0_WELLFORMED", "L1_ROOT_CONSISTENCY"}),
    "cae": ROOT_HALT_STAGES,
}

PROFILE_HOLD_STAGE: Dict[str, str] = {
    "core": "L2_CONSTRAINT_ROLE",
    "cae": "S3_CLOSURE_CONSTRAINT",
}

PROFILE_HOLD_STATE: Dict[str, str] = {
    "core": "HOLD_L2",
    "cae": "HOLD_S3",
}

# 双正交轴终态（核验完备度轴 × 目标断言轴）
VERIFICATION_LEVELS: Tuple[str, ...] = ("STRUCTURE_ONLY", "VERIFIED")
T_ASSERTION_LEVELS: Tuple[str, ...] = ("NO_T_ASSERTION", "T_ASSERTED")

VERIFIED_STATES = frozenset({"VERIFIED_NO_T", "VERIFIED_T_ASSERTED"})

TERMINAL_ADVANCE_BLOCK_STATES = frozenset({
    "STRUCTURE_PASSED",
    "VERIFIED_NO_T",
    "VERIFIED_T_ASSERTED",
    "HALT_CLOSURE_CONFLICT",
    "HALT_VV_CONFLICT",
    "REBASED",
    "DOWNGRADED_NEW_CLAIM",
    "DECLARED_UNDETERMINED",
})

LOCKED_HISTORY_STATES = frozenset({
    "HALT_ROOT_CONFLICT",
    "HALT_CLOSURE_CONFLICT",
    "REBASED",
    "DOWNGRADED_NEW_CLAIM",
})

SUFFICIENT_CERTIFICATE_KINDS = frozenset({
    "exhaustive_domain",
    "artifact_bound_lean",
    "finite_probe_only",
})

PROBE_ROLES = frozenset({
    "standard",
    "adversarial_boundary",
    "exhaustive_domain",
})

REPLAY_STAGE_VERDICTS = frozenset({
    "consistent",
    "inconsistent",
    "insufficient_context",
})

CONSTRAINT_PROTOTYPES = frozenset({"I", "II", "III", "IV.1", "IV.2"})

ENCODING_REGIMES = frozenset({"static_multisort", "refined_subtype", "base_sort_predicate"})

CORE_LEAN_MODULES: Tuple[str, ...] = (
    "LogicCore",
    "ConvexDirectSum",
    "GalileanBox",
    "DissipationGeneric",
)

PROFILE_LEAN_MODULES: Dict[str, Tuple[str, ...]] = {
    "core": ("LogicCore",),
    "cae": CORE_LEAN_MODULES,
}

# 一.8 保编号互斥七分原子角色（与 LogicCore.lean inductive ObjectiveRole 严格机械对齐）
OBJECTIVE_ROLES = frozenset({
    "iff",
    "sufficient_only",
    "necessary_only",
    "veto_only",
    "complementary_veto",
    "independent",
    "neg_sufficient",
})

COGNITIVE_ROLES = frozenset({"unclassified"})

LEGACY_ROLE_ALIASES: Dict[str, str] = {
    "veto": "veto_only",
    "non_decisive": "unclassified",
}

STANDARD_CLAIMS = frozenset({"A->T", "A->not_T", "not_A->T", "not_A->not_T"})

ROLE_IMPLIED_CLAIMS: Dict[str, frozenset[str]] = {
    "iff": frozenset({"A->T", "not_A->not_T"}),
    "sufficient_only": frozenset({"A->T"}),
    "necessary_only": frozenset({"not_A->not_T"}),
    "veto_only": frozenset({"A->not_T"}),
    "complementary_veto": frozenset({"A->not_T", "not_A->T"}),
    "independent": frozenset(),
    "neg_sufficient": frozenset({"not_A->T"}),
    "unclassified": frozenset(),
}

PARITY_TYPES = frozenset({"even_reversible", "odd_dissipative"})

ENTROPY_SIGN_CONVENTIONS = frozenset({"concave_s_plus_ds_dU", "convex_eta_minus_deta_dU"})

EXIT_KINDS = frozenset({"nonperceivable_constraint", "sufficient_certificate", "evolution_decoupled"})

NON_FORMALIZED_ZONES = frozenset({
    "natural_language_extraction",
    "human_stakeholder_preference",
    "empirical_environment_drift",
    "schwartz_distribution",
    "sobolev_illposed",
    "kolmogorov_n_width",
    "subcontinuum_cluster",
    "empirical_validation_sensor",
    "reflexive_ecology",
})

FEEDBACK_LAYERS = frozenset({"1", "1.5", "2", "2M", "state_machine", "lean"})

ISSUE_KIND_PRIORITY: Dict[str, int] = {
    "ILL_FORMED": 5,
    "UNSAT": 4,
    "OVERREACH": 3,
    "UNDETERMINED": 2,
    "VV_FAILED": 1,
}

# 二、Lean 4 纯核门禁与受控真内核驱动 SHA-256 锁定

KERNEL_AUDIT_DRIVER_FILENAME = "KernelAudit.lean"
KERNEL_AUDIT_DRIVER_SHA256 = "48feac222b05c309dbaab102253887d8d3314f8853c716ef0f620538cf6958a8"
GOLDEN_LEAN_VERSION_PREFIX = "4.36."

FORBIDDEN_LEAN_TOKENS = (
    "sorry",
    "sorryAx",
    "admit",
    "axiom",
    "unsafe",
    "partial",
    "implemented_by",
    "extern",
    "extern_spec",
    "opaque",
    "csimp",
    "initialize",
    "builtin_initialize",
    "run_cmd",
    "run_meta",
    "skipKernelTC",
    "native_decide",
    "ofReduceBool",
    "trustCompiler",
    "macro",
    "elab",
    "syntax",
    "variable",
    "include",
)

RESERVED_LOGIC_NAMES = frozenset({
    "Nonempty",
    "False",
    "True",
    "Not",
    "And",
    "Or",
    "Exists",
    "Eq",
    "Iff",
})

FORBIDDEN_LEAN_IMPORTS = ("Mathlib", "Batteries", "Aesop", "Std")

ALLOWED_LEAN_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

REQUIRED_THEOREMS: Dict[str, Tuple[str, ...]] = {
    "LogicCore": (
        "domainGuarded_nonempty",
        "domainGuarded_inhabited_eval",
        "strawson_russell_bivalent_collapse",
        "semantic_trichotomy_exhaustive",
        "semantic_trichotomy_disjoint",
        "identity_relock_theory_nonequiv",
        "definition_relaxation_violates_original",
        "admissibleConstraintProblem_nonempty",
        "disjunctive_guard_not_entails_domain",
        "branchSign_nonempty",
        "objectiveRole_nonempty",
        "entails_neg_iff_contrapositive",
        "branch_pos_iff_cells",
        "branch_neg_iff_cells",
        "constraint_seven_way_exhaustive",
        "constraint_role_unique",
        "validOccupancy_iff_seven_patterns",
        "exists_role_iff",
        "exists_role_sufficientOnly",
        "exists_role_necessaryOnly",
        "exists_role_vetoOnly",
        "exists_role_complementaryVeto",
        "exists_role_independent",
        "exists_role_negSufficient",
        "admissiblePairProblem_nonempty",
        "pair_three_way_exhaustive",
        "pair_three_way_disjoint",
        "unsat_monotone_superset",
        "monotonePremiseChain_nonempty",
        "chain_model_antitone",
        "earliest_root_conflict_unique",
        "exists_earliest_root_conflict",
        "root_conflict_propagates_all_downstream",
        "surviving_unsat_core_blocks_repair",
        "finite_module_repair_must_hit_core",
        "finite_module_repair_drops_core_member",
        "branch_symptom_erasure_counterexample",
        "diamond_joint_conflict_no_single_bad_node",
        "noninjective_map_no_left_inverse_on_fiber",
        "same_repr_checker_cannot_separate_mixed_fiber",
    ),
}

# Lean 4.36.x 双端实测锁定的 LogicCore 40 条公开契约定理内核类型签名摘要（防空真签名掉包）
LOGIC_CORE_GOLDEN_TYPE_HASHES: Dict[str, str] = {
    "domainGuarded_nonempty": "de67f6d09231b54f",
    "domainGuarded_inhabited_eval": "5e079a6b3dd895bd",
    "strawson_russell_bivalent_collapse": "80e71d068be21f24",
    "semantic_trichotomy_exhaustive": "ca39d509951081ef",
    "semantic_trichotomy_disjoint": "c145bcdcd8cd697e",
    "identity_relock_theory_nonequiv": "9978b5621fc21e62",
    "definition_relaxation_violates_original": "3a68aab2fbfd9ef9",
    "admissibleConstraintProblem_nonempty": "d2d9b4b3029a0d7a",
    "disjunctive_guard_not_entails_domain": "5ddfead3df6e62b8",
    "branchSign_nonempty": "430c4e05d05b82e1",
    "objectiveRole_nonempty": "52ad6270db9f1be3",
    "entails_neg_iff_contrapositive": "5b20862e760bcf46",
    "branch_pos_iff_cells": "18d177fba1a5bfd4",
    "branch_neg_iff_cells": "4fe8d276a383d527",
    "constraint_seven_way_exhaustive": "6e0da20f12411ed8",
    "constraint_role_unique": "fe8826832995392a",
    "validOccupancy_iff_seven_patterns": "308fa60a6cad3ade",
    "exists_role_iff": "c109fafc8ae4cf74",
    "exists_role_sufficientOnly": "6a5d2bd0852351ab",
    "exists_role_necessaryOnly": "6f1e328f26c4c442",
    "exists_role_vetoOnly": "342803ca5bbe6062",
    "exists_role_complementaryVeto": "d4b7d95e6f2a1117",
    "exists_role_independent": "6d2b10c746fdf256",
    "exists_role_negSufficient": "6df39c4868b29dbc",
    "admissiblePairProblem_nonempty": "7b056b851c085f89",
    "pair_three_way_exhaustive": "06b3f41a448259c3",
    "pair_three_way_disjoint": "59c15d87735012d5",
    "unsat_monotone_superset": "204850ed00a6a5ae",
    "monotonePremiseChain_nonempty": "086d8834cd98408e",
    "chain_model_antitone": "f75807d7a5dda28e",
    "earliest_root_conflict_unique": "03acc0ef83160a47",
    "exists_earliest_root_conflict": "9739d14d6069f266",
    "root_conflict_propagates_all_downstream": "b194d74b721aeef6",
    "surviving_unsat_core_blocks_repair": "5a27dc3820987041",
    "finite_module_repair_must_hit_core": "1eb57d092afd214a",
    "finite_module_repair_drops_core_member": "633b559c5f1c1dd5",
    "branch_symptom_erasure_counterexample": "7f3e746dcb10e533",
    "diamond_joint_conflict_no_single_bad_node": "bdedf0ec5c1b91a5",
    "noninjective_map_no_left_inverse_on_fiber": "8410782a034aee7c",
    "same_repr_checker_cannot_separate_mixed_fiber": "b0bca3e61a34e6b0",
}

# 三、单源关口字段表与净室盲包可见性（carry | strip）

_COMMON_STAGE_FIELDS: Dict[str, str] = {
    "witnesses": "carry",
    "non_formalized_zones": "carry",
    "scope_exclusions": "carry",
    "verdict": "strip",
    "conflict_summary": "strip",
    "notes": "strip",
}

STAGE_FIELD_SPECS: Dict[str, Dict[str, str]] = {
    "L0_WELLFORMED": {
        **_COMMON_STAGE_FIELDS,
        "encoding_regime": "carry",
        "carrier_vs_proposition_checked": "strip",
        "wellformed_guards": "carry",
        "definitions": "carry",
    },
    "L1_ROOT_CONSISTENCY": {
        **_COMMON_STAGE_FIELDS,
        "root_premises_satisfiable": "strip",
        "modules": "carry",
        "boundary_contracts": "carry",
        "alternative_branches_isolated": "strip",
        "symptom_erasure_detected": "strip",
    },
    "L2_CONSTRAINT_ROLE": {
        **_COMMON_STAGE_FIELDS,
        "decision_basis_explicit": "strip",
        "decision_basis_witnesses": "carry",
        "declared_constraints": "carry",
        "constraint_pairs": "carry",
        "comparisons": "carry",
        "pseudo_moderation_detected": "strip",
    },
    "L3_EMPIRICAL_EXIT": {
        **_COMMON_STAGE_FIELDS,
        "internal_verification": "carry",
        "external_validation": "carry",
        "construction_inputs": "carry",
        "evaluation_inputs": "carry",
        "positive_inference_audit": "strip",
        "review_exit": "carry",
    },
    "S0_TYPING": {
        **_COMMON_STAGE_FIELDS,
        "encoding_regime": "carry",
        "domain_guards": "carry",
    },
    "S1_ROOT_MEASURE": {
        **_COMMON_STAGE_FIELDS,
        "measure_checks": "carry",
    },
    "S2_SYMMETRY_JUMP": {
        **_COMMON_STAGE_FIELDS,
        "symmetry_group": "carry",
        "formulation_type": "carry",
        "mixed_symmetry_groups": "strip",
        "state_dim": "carry",
        "independent_jump_relations": "carry",
        "extra_jump_proven_compatible": "strip",
        "overdetermined_jump_conflict": "strip",
    },
    "S3_CLOSURE_CONSTRAINT": {
        **_COMMON_STAGE_FIELDS,
        "closure_assumptions_explicit": "strip",
        "closure_witnesses": "carry",
        "declared_constraints": "carry",
        "mechanisms": "carry",
        "entropy_sign_convention": "carry",
        "operator_sign_consistent": "strip",
    },
    "S4_LOCAL_SPECTRAL_VV": {
        **_COMMON_STAGE_FIELDS,
        "verification": "carry",
        "validation": "carry",
        "calibration_datasets": "carry",
        "validation_datasets": "carry",
        "residual_error_separated": "strip",
        "residual_error_witnesses": "carry",
        "review_exit": "carry",
        "is_surrogate_or_rom": "carry",
        "strong_classical_baseline": "carry",
        "offline_cost_included": "strip",
        "offline_cost_witnesses": "carry",
    },
}

REVIEW_EXIT_FIELD_VISIBILITY: Dict[str, str] = {
    "exit_kind": "strip",
    "target_exogenous": "strip",
    "target_exogeneity_basis": "carry",
    "target_exogeneity_witnesses": "carry",
    "shares_representation_with_generator": "strip",
    "has_external_executor": "strip",
    "executor_witnesses": "carry",
    "sufficient_certificate_kind": "strip",
    "bridge_premise_ref": "carry",
    "bridge_witnesses": "carry",
    "actual_state_in_reachable_domain": "strip",
    "actual_state_accepted": "strip",
    "acceptance_witnesses": "carry",
    "exit_basis_ref": "carry",
    "exit_witnesses": "carry",
}

RAW_HASH_SENSITIVE_EXTS = frozenset({
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".olean", ".exe", ".dll", ".so", ".pyd", ".bin",
})

RAW_HASH_SENSITIVE_NAMES = frozenset({
    ".gitattributes",
    ".editorconfig",
})

_WIN_LINK_REPARSE_TAGS = frozenset({
    getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
})

_NONINTERACTIVE_ENV = {
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "LESS": "-FRX",
    "GCM_INTERACTIVE": "never",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class GateError(Exception):
    """带有结构化错误码的状态机异常。"""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# 四、单源严格类型解码原语（彻底清零 bool 冒充 int、字符串真值强转与未知键放行）


def reject_unknown_fields(obj: Dict[str, Any], allowed_keys: Iterable[str], field_path: str) -> None:
    allowed = set(allowed_keys)
    unknown = sorted(k for k in obj.keys() if k not in allowed)
    if unknown:
        raise GateError(
            "SCHEMA_UNKNOWN_FIELD",
            f"{field_path} 包含未声明的未知字段: {unknown}",
            {"field": field_path, "unknown_fields": unknown, "allowed_fields": sorted(allowed)},
        )


def strict_bool(value: Any, field_path: str, *, default: Optional[bool] = None) -> bool:
    if value is None and default is not None:
        return default
    if type(value) is not bool:
        raise GateError(
            "SCHEMA_TYPE_ERROR",
            f"{field_path} 必须为精确布尔值（true/false），收到 {type(value).__name__}: {value!r}",
            {"field": field_path, "expected": "bool", "actual_type": type(value).__name__},
        )
    return value


def strict_int(
    value: Any,
    field_path: str,
    *,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> int:
    if isinstance(value, bool):
        raise GateError(
            "SCHEMA_TYPE_ERROR",
            f"{field_path} 必须为整数，禁止使用布尔值 {value!r} 冒充整数",
            {"field": field_path, "expected": "int", "actual_type": "bool", "reason": "bool_not_allowed_for_int"},
        )
    if type(value) is not int:
        raise GateError(
            "SCHEMA_TYPE_ERROR",
            f"{field_path} 必须为整数，收到 {type(value).__name__}: {value!r}",
            {"field": field_path, "expected": "int", "actual_type": type(value).__name__},
        )
    if min_value is not None and value < min_value:
        raise GateError(
            "SCHEMA_VALUE_ERROR",
            f"{field_path} 必须 >= {min_value}，收到 {value}",
            {"field": field_path, "min_value": min_value, "actual": value},
        )
    if max_value is not None and value > max_value:
        raise GateError(
            "SCHEMA_VALUE_ERROR",
            f"{field_path} 必须 <= {max_value}，收到 {value}",
            {"field": field_path, "max_value": max_value, "actual": value},
        )
    return value


def strict_str(
    value: Any,
    field_path: str,
    *,
    non_empty: bool = True,
    allowed: Optional[Iterable[str]] = None,
    default: Optional[str] = None,
) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise GateError(
            "SCHEMA_TYPE_ERROR",
            f"{field_path} 必须为字符串，收到 {type(value).__name__}: {value!r}",
            {"field": field_path, "expected": "str", "actual_type": type(value).__name__},
        )
    cleaned = value.strip()
    if non_empty and not cleaned:
        raise GateError(
            "SCHEMA_VALUE_ERROR",
            f"{field_path} 必须为非空字符串",
            {"field": field_path},
        )
    if allowed is not None:
        allowed_set = set(allowed)
        if cleaned not in allowed_set:
            raise GateError(
                "SCHEMA_VALUE_ERROR",
                f"{field_path} 值非法: {cleaned!r}（允许值: {sorted(allowed_set)}）",
                {"field": field_path, "allowed": sorted(allowed_set), "actual": cleaned},
            )
    return cleaned


def strict_str_list(
    value: Any,
    field_path: str,
    *,
    non_empty: bool = False,
    allowed: Optional[Iterable[str]] = None,
    allow_none: bool = True,
) -> List[str]:
    if value is None:
        if allow_none and not non_empty:
            return []
        raise GateError("SCHEMA_TYPE_ERROR", f"{field_path} 必须为非空字符串数组", {"field": field_path, "expected": "list[str]"})
    if not isinstance(value, list):
        raise GateError(
            "SCHEMA_TYPE_ERROR",
            f"{field_path} 必须为字符串数组（禁止传裸字符串或其他类型），收到 {type(value).__name__}",
            {"field": field_path, "expected": "list[str]", "actual_type": type(value).__name__},
        )
    allowed_set = set(allowed) if allowed is not None else None
    out: List[str] = []
    for idx, item in enumerate(value, 1):
        s = strict_str(item, f"{field_path}[{idx}]", non_empty=True, allowed=allowed_set)
        out.append(s)
    if non_empty and not out:
        raise GateError("SCHEMA_VALUE_ERROR", f"{field_path} 不能为空数组", {"field": field_path})
    return out


# 五、路径安全、双哈希策略、文本归一化、单案提交锁与原子 JSON 落盘


def _normalize_claim_text(text: str) -> str:
    nfkc = unicodedata.normalize("NFKC", str(text or ""))
    collapsed = re.sub(r"\s+", " ", nfkc).strip()
    return collapsed.casefold()


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _is_link_or_reparse(path: Path) -> bool:
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


def _reject_links(path: Path, *, label: str = "路径") -> Path:
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise GateError("UNSAFE_PATH", f"{label}不允许包含未解析的上级目录引用: {path}")
    absolute = expanded.absolute()
    for ancestor in (*reversed(absolute.parents), absolute):
        if os.path.lexists(ancestor) and _is_link_or_reparse(ancestor):
            raise GateError("SYMLINK_REJECTED", f"{label}拒绝符号链接或重解析点: {ancestor}")
    return absolute


def canonical_json_sha256(obj: Any) -> str:
    try:
        encoded = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except ValueError as exc:
        raise GateError("SCHEMA_NONSTANDARD_NUMBER", f"规范 JSON 摘要禁止 NaN/Infinity 等非有限数值: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def compute_bytes_hashes(raw_bytes: bytes, path: Path, *, force_raw: bool = False) -> Dict[str, Any]:
    """基于单次读取的原始字节计算双哈希（raw_sha256 与严格 \\r\\n -> \\n 归一化 normalized_sha256）。"""
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    has_shebang = raw_bytes.startswith(b"#!")
    is_binary = b"\x00" in raw_bytes
    suffix = path.suffix.lower()
    name_low = path.name.lower()

    if is_binary:
        norm_sha = raw_sha
    else:
        # 严格只做 CRLF -> LF（O-B5，与 SKILL.md 及 compute_gate_code_sha256 口径唯一一致）
        norm_bytes = raw_bytes.replace(b"\r\n", b"\n")
        norm_sha = hashlib.sha256(norm_bytes).hexdigest()

    raw_sensitive = (
        force_raw
        or is_binary
        or has_bom
        or has_shebang
        or suffix in RAW_HASH_SENSITIVE_EXTS
        or name_low in RAW_HASH_SENSITIVE_NAMES
    )
    hash_mode = "raw" if raw_sensitive else "normalized"
    effective_sha = raw_sha if hash_mode == "raw" else norm_sha
    return {
        "raw_sha256": raw_sha,
        "normalized_sha256": norm_sha,
        "hash_mode": hash_mode,
        "artifact_sha256": effective_sha,
        "has_utf8_bom": has_bom,
        "has_shebang": has_shebang,
        "size_bytes": len(raw_bytes),
    }


def compute_file_hashes(path: Path, *, force_raw: bool = False) -> Dict[str, Any]:
    """计算文件双哈希（raw_sha256 与 CRLF 归一化 normalized_sha256）并按文件语义决定生效模式。"""
    return compute_bytes_hashes(path.read_bytes(), path, force_raw=force_raw)


def hashes_match_disk(
    recorded: Dict[str, Any],
    path: Path,
    *,
    force_raw: bool = False,
    project_root: Optional[Path] = None,
) -> Tuple[bool, str]:
    try:
        checked = _reject_links(path, label="复核锚定路径")
    except GateError as exc:
        return False, f"unsafe_or_linked_path:{exc.code}"
    if project_root is not None:
        try:
            root_res = _reject_links(project_root, label="课题根目录").resolve()
            if not checked.resolve().is_relative_to(root_res):
                return False, "witness_out_of_scope"
        except (GateError, OSError):
            return False, "witness_out_of_scope"
    if not checked.is_file() or _is_link_or_reparse(checked):
        return False, "file_missing_or_link"
    rec_mode = str(recorded.get("hash_mode") or "normalized")
    cur = compute_file_hashes(checked, force_raw=force_raw or (rec_mode == "raw"))
    if cur["hash_mode"] != rec_mode:
        return False, f"hash_mode_changed:{rec_mode}->{cur['hash_mode']}"
    if rec_mode == "raw":
        if cur["raw_sha256"] != recorded.get("raw_sha256"):
            return False, "raw_sha256_mismatch"
    else:
        if cur["normalized_sha256"] != recorded.get("normalized_sha256"):
            return False, "normalized_sha256_mismatch"
    return True, "ok"


def _atomic_write_text(path: Path, text: str) -> Path:
    dest = _reject_links(path, label="写入目标")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _reject_links(dest, label="写入目标")
    data = text.encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".axiom-gate-", suffix=".tmp", dir=str(dest.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.lexists(dest) and _is_link_or_reparse(dest):
            raise GateError("SYMLINK_REJECTED", f"写入目标在替换前变为链接: {dest}")
        os.replace(tmp_path, dest)
    finally:
        with contextlib.suppress(OSError):
            if tmp_path.exists():
                tmp_path.unlink()
    return dest


def _strict_pairs_hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise GateError("SCHEMA_DUPLICATE_KEY", f"JSON 对象包含重复键: {k!r}", {"duplicate_key": k})
        out[k] = v
    return out


def _reject_nonstandard_json_const(token: str) -> Any:
    raise GateError("SCHEMA_NONSTANDARD_NUMBER", f"JSON 禁止非标准数值常量: {token!r}", {"token": token})


def _strict_parse_float(token: str) -> float:
    val = float(token)
    if not math.isfinite(val):
        raise GateError("SCHEMA_NONSTANDARD_NUMBER", f"JSON 浮点数值溢出为非有限值: {token!r}", {"token": token})
    return val


def loads_strict_json(raw_text: str, *, label: str = "JSON") -> Any:
    try:
        return json.loads(
            raw_text,
            object_pairs_hook=_strict_pairs_hook,
            parse_constant=_reject_nonstandard_json_const,
            parse_float=_strict_parse_float,
        )
    except GateError:
        raise
    except (ValueError, TypeError) as exc:
        raise GateError("INVALID_JSON", f"无法严格解析{label}: {exc}") from exc


def _read_json(path: Path, *, label: str = "JSON 文件") -> Dict[str, Any]:
    checked = _reject_links(path, label=label)
    if not checked.is_file():
        raise GateError("FILE_NOT_FOUND", f"{label}不存在: {checked}")
    try:
        raw_text = checked.read_text(encoding="utf-8-sig")
        parsed = loads_strict_json(raw_text, label=f"{label} {checked}")
    except GateError:
        raise
    except (OSError, UnicodeError) as exc:
        raise GateError("INVALID_JSON", f"无法读取{label} {checked}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise GateError("INVALID_JSON", f"{label}顶层必须为 JSON 对象: {checked}")
    return parsed


def _unlock_and_close_lock_fd(fd: int, locked: bool) -> None:
    if fd >= 0:
        if locked:
            with contextlib.suppress(OSError):
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


@contextlib.contextmanager
def case_file_lock(case_path: Path, *, timeout_sec: float = 10.0, stale_sec: float = 300.0):
    """跨平台内核级单案提交锁（Windows msvcrt.locking / POSIX fcntl.flock，进程退出由内核自动释放，禁 os.kill 与无所有权删锁）。"""
    del stale_sec
    import errno as _errno

    checked = _reject_links(case_path, label="审查单路径")
    checked.parent.mkdir(parents=True, exist_ok=True)
    _reject_links(checked.parent, label="审查单父目录")
    lock_path = checked.parent / f".{checked.name}.lock"
    deadline = time.monotonic() + timeout_sec
    fd = -1
    locked = False
    busy_errnos = (_errno.EACCES, _errno.EAGAIN, getattr(_errno, "EWOULDBLOCK", _errno.EAGAIN), _errno.EDEADLK)

    def _release() -> None:
        nonlocal fd, locked
        _unlock_and_close_lock_fd(fd, locked)
        fd = -1
        locked = False

    try:
        while True:
            if os.path.lexists(lock_path) and _is_link_or_reparse(lock_path):
                raise GateError("SYMLINK_REJECTED", f"审查单锁文件禁止为符号链接: {lock_path}")
            try:
                fd = os.open(
                    str(lock_path),
                    os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except OSError as exc:
                retryable = exc.errno == _errno.ENOENT or (os.name == "nt" and exc.errno == _errno.EACCES)
                if retryable and time.monotonic() < deadline:
                    time.sleep(0.01)
                    continue
                raise GateError("CASE_LOCK_FAILED", f"无法打开审查单提交锁 {lock_path}: {exc}") from exc

            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink > 1:
                _release()
                raise GateError("SYMLINK_REJECTED", f"审查单提交锁必须为独立普通文件: {lock_path}")
            try:
                anchor = lock_path.lstat()
            except FileNotFoundError:
                anchor = None
            if anchor is None or _is_link_or_reparse(lock_path) or not stat.S_ISREG(anchor.st_mode) or anchor.st_nlink != 1 or (anchor.st_dev, anchor.st_ino) != (opened.st_dev, opened.st_ino):
                _release()
                if time.monotonic() >= deadline:
                    raise GateError("CASE_LOCK_TIMEOUT", f"获取审查单提交锁超时: {lock_path}")
                time.sleep(0.01)
                continue

            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError as exc:
                    if exc.errno not in busy_errnos:
                        _release()
                        raise GateError("CASE_LOCK_FAILED", f"审查单提交锁加锁异常: {exc}") from exc
                    if time.monotonic() >= deadline:
                        _release()
                        raise GateError("CASE_LOCK_TIMEOUT", f"获取审查单提交锁超时: {lock_path}") from exc
                    time.sleep(0.02)

            opened_after = os.fstat(fd)
            try:
                anchor_after = lock_path.lstat()
            except FileNotFoundError:
                anchor_after = None
            if (
                anchor_after is None
                or _is_link_or_reparse(lock_path)
                or not stat.S_ISREG(anchor_after.st_mode)
                or opened_after.st_nlink != 1
                or anchor_after.st_nlink != 1
                or (anchor_after.st_dev, anchor_after.st_ino) != (opened_after.st_dev, opened_after.st_ino)
            ):
                _release()
                if time.monotonic() >= deadline:
                    raise GateError("CASE_LOCK_TIMEOUT", f"获取审查单提交锁超时: {lock_path}")
                time.sleep(0.01)
                continue
            break

        with contextlib.suppress(OSError):
            os.lseek(fd, 1, os.SEEK_SET)
            os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
        yield lock_path
    finally:
        _release()


def _now_utc_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_lean_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "lean"


def _kernel_audit_driver_path() -> Path:
    return Path(__file__).resolve().parent / KERNEL_AUDIT_DRIVER_FILENAME


def compute_gate_code_sha256() -> Dict[str, str]:
    """计算状态机自身代码与受控 Lean 驱动的规范化内容摘要，任一核心文件缺失立即报错（防静默跳过）。"""
    lib_dir = Path(__file__).resolve().parent
    scripts_dir = lib_dir.parent
    h_gate = hashlib.sha256()
    for rel_path in (
        scripts_dir / "axiom_gate.py",
        lib_dir / "__init__.py",
        lib_dir / "contract.py",
        lib_dir / "lean_gate.py",
        lib_dir / "evidence.py",
        lib_dir / "rules.py",
        lib_dir / "case.py",
        lib_dir / "replay.py",
    ):
        checked = _reject_links(rel_path, label="状态机源码")
        if not checked.is_file():
            raise GateError("GATE_FILE_MISSING", f"状态机核心模块缺失，拒绝生成代码摘要: {rel_path.name}")
        norm = checked.read_bytes().replace(b"\r\n", b"\n")
        h_gate.update(rel_path.name.encode("utf-8") + b"\0" + hashlib.sha256(norm).digest())
    drv = _reject_links(_kernel_audit_driver_path(), label="Lean 审计驱动")
    if not drv.is_file():
        raise GateError("GATE_FILE_MISSING", f"受控 Lean 审计驱动缺失: {drv.name}")
    drv_sha = hashlib.sha256(drv.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return {
        "gate_sha256": h_gate.hexdigest(),
        "lean_driver_sha256": drv_sha,
    }


def _check_finite_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)
