# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_axiom_lib.rules - 双档（core L0-L3 / cae S0-S4）单源严格 Schema 判定引擎。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .contract import (
    COGNITIVE_ROLES,
    CONSTRAINT_PROTOTYPES,
    ENCODING_REGIMES,
    ENTROPY_SIGN_CONVENTIONS,
    EXIT_KINDS,
    LEGACY_ROLE_ALIASES,
    NON_FORMALIZED_ZONES,
    OBJECTIVE_ROLES,
    PARITY_TYPES,
    REVIEW_EXIT_FIELD_VISIBILITY,
    ROLE_IMPLIED_CLAIMS,
    STAGE_FIELD_SPECS,
    STANDARD_CLAIMS,
    SUFFICIENT_CERTIFICATE_KINDS,
    GateError,
    reject_unknown_fields,
    strict_bool,
    strict_int,
    strict_str,
    strict_str_list,
)
from .evidence import _verify_witness_list


def _mk_issue(kind: str, code: str, message: str) -> Dict[str, str]:
    return {"kind": kind, "code": code, "message": message}


def _validate_stage_top_fields(stage: str, payload: Dict[str, Any]) -> None:
    spec = STAGE_FIELD_SPECS.get(stage)
    if spec is not None:
        reject_unknown_fields(payload, spec.keys(), stage)
    if payload.get("verdict") is not None:
        strict_str(payload.get("verdict"), f"{stage}.verdict", allowed={"pass", "conflict"})
    if payload.get("conflict_summary") is not None:
        strict_str(payload.get("conflict_summary"), f"{stage}.conflict_summary", non_empty=False)
    if payload.get("notes") is not None:
        strict_str(payload.get("notes"), f"{stage}.notes", non_empty=False)
    if payload.get("scope_exclusions") is not None:
        strict_str_list(payload.get("scope_exclusions"), f"{stage}.scope_exclusions", allow_none=False)


def _validate_non_formalized_zones(payload: Dict[str, Any]) -> List[str]:
    zones = payload.get("non_formalized_zones", [])
    if zones is None:
        return []
    if not isinstance(zones, list):
        raise GateError("SCHEMA_TYPE_ERROR", "non_formalized_zones 必须为字符串数组", {"field": "non_formalized_zones"})
    cleaned: List[str] = []
    for idx, item in enumerate(zones, 1):
        tag = strict_str(item, f"non_formalized_zones[{idx}]")
        if tag not in NON_FORMALIZED_ZONES:
            raise GateError("UNKNOWN_ZONE_TAG", f"未登记的禁区标签: {tag!r}")
        cleaned.append(tag)
    return cleaned


def _normalize_objective_role(raw_role: str) -> Optional[str]:
    if raw_role in OBJECTIVE_ROLES or raw_role in COGNITIVE_ROLES:
        return raw_role
    return LEGACY_ROLE_ALIASES.get(raw_role)


def _detect_dependency_cycle(dep_graph: Dict[str, List[str]]) -> Optional[List[str]]:
    """对有限模块依赖图执行三色 DFS 环检测；存在环时返回环路径列表。"""
    color: Dict[str, int] = {node: 0 for node in dep_graph}
    stack: List[str] = []

    def dfs(u: str) -> Optional[List[str]]:
        color[u] = 1
        stack.append(u)
        for v in dep_graph.get(u, ()):
            if color[v] == 1:
                idx = stack.index(v)
                return stack[idx:] + [v]
            if color[v] == 0:
                cyc = dfs(v)
                if cyc is not None:
                    return cyc
        stack.pop()
        color[u] = 2
        return None

    for node in dep_graph:
        if color[node] == 0:
            cyc = dfs(node)
            if cyc is not None:
                return cyc
    return None


def _evaluate_s0_typing(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    _validate_stage_top_fields("S0_TYPING", payload)
    default_regime = strict_str(
        payload.get("encoding_regime"),
        "S0_TYPING.encoding_regime",
        allowed=ENCODING_REGIMES,
        default="refined_subtype",
    )
    guards = payload.get("domain_guards")
    if not isinstance(guards, list) or not guards:
        raise GateError("MISSING_DOMAIN_GUARDS", "S0_TYPING 必须声明非空 domain_guards 列表（如 M > 0, V_k >= V_min > 0）")
    issues: List[Dict[str, str]] = []
    extra_witnesses: List[Dict[str, Any]] = []
    for idx, entry in enumerate(guards, 1):
        if not isinstance(entry, dict):
            raise GateError("SCHEMA_TYPE_ERROR", f"domain_guards[{idx}] 必须为对象")
        reject_unknown_fields(entry, ("expression", "status", "encoding_regime", "witnesses", "note"), f"domain_guards[{idx}]")
        expr = strict_str(entry.get("expression"), f"domain_guards[{idx}].expression")
        status = strict_str(entry.get("status"), f"domain_guards[{idx}].status", allowed={"satisfied", "violated"})
        regime = strict_str(entry.get("encoding_regime"), f"domain_guards[{idx}].encoding_regime", allowed=ENCODING_REGIMES, default=default_regime)
        gw = _verify_witness_list(
            entry.get("witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label=f"domain_guards[{idx}].witnesses",
        )
        extra_witnesses.extend(gw)
        if status == "violated":
            if regime == "base_sort_predicate":
                issues.append(_mk_issue("UNSAT", "DOMAIN_PREDICATE_CONFLICT", f"基类谓词编码下定义域前提冲突（一.2 / 一.4 良构不可满足）: {expr}"))
            else:
                issues.append(_mk_issue("ILL_FORMED", "DOMAIN_GUARD_VIOLATED", f"偏算子定义域守卫违约（一.2 {regime} 不良构）: {expr}"))
    if payload.get("verdict") == "conflict":
        summary = payload.get("conflict_summary") or ""
        if default_regime == "base_sort_predicate":
            issues.append(_mk_issue("UNSAT", "S0_EXPLICIT_CONFLICT", summary or "S0_TYPING 基类谓词编码报告良构不可满足冲突"))
        else:
            issues.append(_mk_issue("ILL_FORMED", "S0_EXPLICIT_CONFLICT", summary or "S0_TYPING 显式报告定义域不良构"))
    meta = {"encoding_regime": default_regime}
    return ("conflict", issues, extra_witnesses, meta) if issues else ("pass", [], extra_witnesses, meta)


def _evaluate_s1_root_measure(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    del project_root, lean_audit, allow_probe_exec
    _validate_stage_top_fields("S1_ROOT_MEASURE", payload)
    checks = payload.get("measure_checks")
    required_keys = (
        "shrink_limit_vs_positive_measure",
        "singular_measure_product",
        "nonlinear_average_commutativity",
    )
    if not isinstance(checks, dict):
        raise GateError("SCHEMA_TYPE_ERROR", "S1_ROOT_MEASURE 必须提供 measure_checks 对象")
    reject_unknown_fields(checks, required_keys, "S1_ROOT_MEASURE.measure_checks")
    issues: List[Dict[str, str]] = []
    for key in required_keys:
        entry = checks.get(key)
        if not isinstance(entry, dict):
            raise GateError("MISSING_MEASURE_CHECK", f"S1_ROOT_MEASURE 缺少必填检查项: {key}")
        reject_unknown_fields(entry, ("status", "reason", "detail", "note"), f"measure_checks.{key}")
        status = strict_str(entry.get("status"), f"measure_checks.{key}.status", allowed={"pass", "conflict", "not_applicable"})
        if entry.get("reason") is not None:
            strict_str(entry.get("reason"), f"measure_checks.{key}.reason", non_empty=False)
        if entry.get("detail") is not None:
            strict_str(entry.get("detail"), f"measure_checks.{key}.detail", non_empty=False)
        if status == "not_applicable":
            if entry.get("reason") is None or not strict_str(entry.get("reason"), f"measure_checks.{key}.reason", non_empty=False):
                raise GateError("MISSING_NA_REASON", f"measure_checks.{key} 标为 not_applicable 时必须填写非空 reason")
        if status == "conflict":
            detail = strict_str(entry.get("detail"), f"measure_checks.{key}.detail", non_empty=False, default=key) or key
            issues.append(_mk_issue("UNSAT", f"S1_{key.upper()}", f"S1 根层测度-微分化归冲突 ({key}): {detail}"))
    if payload.get("verdict") == "conflict":
        issues.append(_mk_issue("UNSAT", "S1_EXPLICIT_CONFLICT", payload.get("conflict_summary") or "S1_ROOT_MEASURE 显式报告根层不可满足冲突"))
    return ("conflict", issues, [], {}) if issues else ("pass", [], [], {})


def _evaluate_s2_symmetry_jump(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    del project_root, lean_audit, allow_probe_exec
    _validate_stage_top_fields("S2_SYMMETRY_JUMP", payload)
    allowed_groups = {"galilean", "lorentz_poincare", "none_pure_math"}
    allowed_forms = {"conservative_divergence", "nonconservative_product", "positive_measure_box", "pure_math_no_pde"}
    strict_str(payload.get("symmetry_group"), "S2_SYMMETRY_JUMP.symmetry_group", allowed=allowed_groups)
    strict_str(payload.get("formulation_type"), "S2_SYMMETRY_JUMP.formulation_type", allowed=allowed_forms)
    issues: List[Dict[str, str]] = []
    if strict_bool(payload.get("mixed_symmetry_groups"), "S2_SYMMETRY_JUMP.mixed_symmetry_groups", default=False):
        issues.append(_mk_issue("UNSAT", "MIXED_SYMMETRY_GROUPS", "S2 对称群冲突：禁止同阶拼接服从不同惯性时空群的子方程（层 2M 一.3）"))
    extra_compat = strict_bool(payload.get("extra_jump_proven_compatible"), "S2_SYMMETRY_JUMP.extra_jump_proven_compatible", default=False)
    state_dim = payload.get("state_dim")
    jump_eqs = payload.get("independent_jump_relations")
    if state_dim is not None or jump_eqs is not None:
        sd = strict_int(state_dim, "S2_SYMMETRY_JUMP.state_dim", min_value=1)
        je = strict_int(jump_eqs, "S2_SYMMETRY_JUMP.independent_jump_relations", min_value=0)
        if je > sd and not extra_compat:
            issues.append(_mk_issue(
                "UNSAT",
                "OVERDETERMINED_JUMP",
                f"S2 间断超定冲突：m={sd} 个状态变量（间断处 m+1={sd + 1} 未知量）却施加 {je} 条独立跳跃等式（层 1.5 二.A.2）",
            ))
    if strict_bool(payload.get("overdetermined_jump_conflict"), "S2_SYMMETRY_JUMP.overdetermined_jump_conflict", default=False):
        issues.append(_mk_issue("UNSAT", "OVERDETERMINED_JUMP_EXPLICIT", payload.get("conflict_summary") or "S2 报告非守恒路径与额外散度跳跃超定自相矛盾"))
    if payload.get("verdict") == "conflict":
        issues.append(_mk_issue("UNSAT", "S2_EXPLICIT_CONFLICT", payload.get("conflict_summary") or "S2_SYMMETRY_JUMP 显式报告对称或跳跃冲突"))
    return ("conflict", issues, [], {}) if issues else ("pass", [], [], {})


def _evaluate_s3_closure_constraint(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    _validate_stage_top_fields("S3_CLOSURE_CONSTRAINT", payload)
    issues: List[Dict[str, str]] = []
    extra_witnesses: List[Dict[str, Any]] = []

    closure_explicit = strict_bool(payload.get("closure_assumptions_explicit"), "S3_CLOSURE_CONSTRAINT.closure_assumptions_explicit", default=False)
    if not closure_explicit:
        issues.append(_mk_issue(
            "UNDETERMINED",
            "MISSING_CLOSURE_BASIS",
            "S3 欠封闭基准未显式化（一.4 语义未定）：须明示闭合假设、选解准则与适用边界 Λ，或显式声明未定",
        ))
    else:
        cw = _verify_witness_list(
            payload.get("closure_witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label="closure_witnesses",
        )
        extra_witnesses.extend(cw)

    constraints = payload.get("declared_constraints")
    if not isinstance(constraints, list) or not constraints:
        raise GateError("MISSING_CONSTRAINTS", "S3_CLOSURE_CONSTRAINT 必须提供非空 declared_constraints 列表")

    active_prototypes: Set[str] = set()
    s3_constraint_roles: Dict[str, str] = {}
    allowed_roles_desc = sorted(OBJECTIVE_ROLES | COGNITIVE_ROLES)
    for idx, item in enumerate(constraints, 1):
        if not isinstance(item, dict):
            raise GateError("SCHEMA_TYPE_ERROR", f"declared_constraints[{idx}] 必须为对象")
        reject_unknown_fields(
            item,
            ("name", "prototype", "objective_role", "witnesses", "standard_claims", "claimed_as_axiom_standard", "norm_equiv_witnesses", "note"),
            f"declared_constraints[{idx}]",
        )
        cname = strict_str(item.get("name"), f"declared_constraints[{idx}].name")
        proto = strict_str(item.get("prototype"), f"declared_constraints[{idx}].prototype", allowed=CONSTRAINT_PROTOTYPES)
        raw_role = strict_str(item.get("objective_role"), f"declared_constraints[{idx}].objective_role")
        role = _normalize_objective_role(raw_role)
        if role is None:
            raise GateError("SCHEMA_VALUE_ERROR", f"declared_constraints[{idx}].objective_role 非法: {raw_role!r}（允许: {allowed_roles_desc}）")
        s3_constraint_roles[cname] = role
        item_w = _verify_witness_list(
            item.get("witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label=f"declared_constraints[{idx}]({cname}).witnesses",
        )
        extra_witnesses.extend(item_w)
        active_prototypes.add(proto)

        claims: Set[str] = set()
        if item.get("standard_claims") is not None:
            claims.update(strict_str_list(item.get("standard_claims"), f"declared_constraints[{idx}].standard_claims", allowed=STANDARD_CLAIMS, allow_none=False))
        claimed_std = strict_bool(item.get("claimed_as_axiom_standard"), f"declared_constraints[{idx}].claimed_as_axiom_standard", default=False)
        if claimed_std and not claims:
            claims = {"A->T", "not_A->not_T"}

        if role == "unclassified":
            issues.append(_mk_issue(
                "UNDETERMINED",
                "UNCLASSIFIED_CONSTRAINT",
                f"S3 约束 '{cname}' 尚处认知态 unclassified（一.8 严禁未核验直接放行或冒充第 5 类）",
            ))
            if claims:
                issues.append(_mk_issue(
                    "OVERREACH",
                    "UNCLASSIFIED_STANDARD_CLAIM",
                    f"S3 约束越权：未判定约束 '{cname}' 不得提出任何方向性标准申称 {sorted(claims)}",
                ))
            continue

        if role in ("veto_only", "complementary_veto"):
            issues.append(_mk_issue(
                "UNSAT",
                "CONSTRAINT_VETO_CONFLICT",
                f"S3 约束否决冲突：约束 '{cname}' 相对联合断言目标处于一.8 第 4 类否决状态（{role}）",
            ))

        allowed_for_role = ROLE_IMPLIED_CLAIMS[role]
        bad_claims = sorted(claims - allowed_for_role)
        if bad_claims:
            issues.append(_mk_issue(
                "OVERREACH",
                "CONSTRAINT_STANDARD_OVERREACH",
                f"S3 约束越权（一.8 全原型禁令）：原型 {proto} 约束 '{cname}'（角色 {role}）不得申称未获蕴含的标准 {bad_claims}",
            ))

        if proto == "IV.2" and (role in ("iff", "sufficient_only", "necessary_only") or bool(claims)):
            if not item.get("norm_equiv_witnesses"):
                issues.append(_mk_issue(
                    "OVERREACH",
                    "PROTO_IV2_MISSING_NORM_WITNESS",
                    f"S3 原型 IV.2 约束越权：'{cname}' 宣称正向/必要地位或标准时须绑定 norm_equiv_witnesses（比较函数/强制性/局部等价与定理前提见证，禁止裸布尔证明位）",
                ))
            else:
                nw = _verify_witness_list(
                    item.get("norm_equiv_witnesses"),
                    project_root,
                    lean_audit,
                    allow_probe_exec=allow_probe_exec,
                    context_label=f"declared_constraints[{idx}]({cname}).norm_equiv_witnesses",
                )
                extra_witnesses.extend(nw)

    if "IV.1" in active_prototypes:
        if "II" in active_prototypes:
            issues.append(_mk_issue(
                "UNSAT",
                "PROTO_IV1_II_CONFLICT",
                "S3 原型 IV.1 冲突：非退化弯曲非线性子流形在普遍凸平均或跨象空间线性投影下不可封闭（层 1.5 2B.1；极小冲突核由 IV.1 自身与弯曲非退化点构成，与原型 II 同开时叠加冲突且撤除原型 II 亦不能解除）",
            ))
        else:
            issues.append(_mk_issue(
                "UNSAT",
                "PROTO_IV1_CURVED_CLOSURE_CONFLICT",
                "S3 原型 IV.1 独立冲突（层 1.5 2B.1 / 第一层一.4）：外加非退化弯曲非线性子流形普遍平均封闭或跨象空间线性投影不变性自身即不可满足，严禁仅靠未声明原型 II 绕过拦截（防消症伪相容）",
            ))

    mechanisms = payload.get("mechanisms")
    if mechanisms is not None:
        if not isinstance(mechanisms, list):
            raise GateError("SCHEMA_TYPE_ERROR", "mechanisms 必须为列表")
        for idx, mech in enumerate(mechanisms, 1):
            if not isinstance(mech, dict):
                raise GateError("SCHEMA_TYPE_ERROR", f"mechanisms[{idx}] 必须为对象")
            reject_unknown_fields(mech, ("name", "parity", "attached_dissipation_inequality", "note"), f"mechanisms[{idx}]")
            mname = strict_str(mech.get("name"), f"mechanisms[{idx}].name")
            parity = strict_str(mech.get("parity"), f"mechanisms[{idx}].parity", allowed=PARITY_TYPES)
            attached_diss = strict_bool(mech.get("attached_dissipation_inequality"), f"mechanisms[{idx}].attached_dissipation_inequality", default=False)
            if parity == "even_reversible" and attached_diss:
                issues.append(_mk_issue(
                    "UNSAT",
                    "EVEN_PARITY_DISSIPATION",
                    f"S3 宇称违规（层 2M 三.1）：偶宇称可逆机制 '{mname}' 熵产生恒为零，严禁挂载耗散不等式",
                ))

    op_consistent_val: Optional[bool] = None
    if payload.get("operator_sign_consistent") is not None:
        op_consistent_val = strict_bool(payload.get("operator_sign_consistent"), "S3_CLOSURE_CONSTRAINT.operator_sign_consistent")
    if payload.get("entropy_sign_convention") is not None:
        sign_conv = strict_str(payload.get("entropy_sign_convention"), "S3_CLOSURE_CONSTRAINT.entropy_sign_convention", allowed=ENTROPY_SIGN_CONVENTIONS)
        if op_consistent_val is False:
            issues.append(_mk_issue(
                "UNSAT",
                "ENTROPY_SIGN_INCONSISTENT",
                f"S3 凹熵共轭符号矛盾（层 2M 三.2）：声明 {sign_conv} 但本构算子符号不一致",
            ))

    if payload.get("verdict") == "conflict":
        issues.append(_mk_issue("UNSAT", "S3_EXPLICIT_CONFLICT", payload.get("conflict_summary") or "S3_CLOSURE_CONSTRAINT 显式报告闭合或约束冲突"))
    s3_meta = {"constraint_count": len(constraints), "constraint_roles": s3_constraint_roles}
    return ("conflict", issues, extra_witnesses, s3_meta) if issues else ("pass", [], extra_witnesses, s3_meta)


def _evaluate_review_exit_block(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
    stage_label: str,
) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    """第一层四.1–四.4 通用反身退出三支、外生性依据与充分证书证明载体分级核验（core/L3 与 cae/S4 共用）。"""
    issues: List[Dict[str, str]] = []
    extra_witnesses: List[Dict[str, Any]] = []

    review_exit = payload.get("review_exit")
    if not isinstance(review_exit, dict):
        raise GateError("MISSING_REVIEW_EXIT", f"{stage_label} 必须提供 review_exit 字段核验退出代理循环条件（第一层四.4）")
    reject_unknown_fields(review_exit, REVIEW_EXIT_FIELD_VISIBILITY.keys(), f"{stage_label}.review_exit")

    exit_kind = strict_str(review_exit.get("exit_kind"), f"{stage_label}.review_exit.exit_kind", allowed=EXIT_KINDS)
    target_exogenous = strict_bool(review_exit.get("target_exogenous"), f"{stage_label}.review_exit.target_exogenous")
    shares_repr = strict_bool(review_exit.get("shares_representation_with_generator"), f"{stage_label}.review_exit.shares_representation_with_generator")
    has_ext_exec = strict_bool(review_exit.get("has_external_executor"), f"{stage_label}.review_exit.has_external_executor")

    # O-B7：强制外生性判定依据（拒裸布尔）
    exog_basis = strict_str(review_exit.get("target_exogeneity_basis"), f"{stage_label}.review_exit.target_exogeneity_basis")
    if review_exit.get("target_exogeneity_witnesses") is not None:
        tw = _verify_witness_list(
            review_exit.get("target_exogeneity_witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label="review_exit.target_exogeneity_witnesses",
        )
        extra_witnesses.extend(tw)

    zones = _validate_non_formalized_zones(payload)
    endogenous_requires_2c = False
    if not target_exogenous:
        endogenous_requires_2c = True
        if "reflexive_ecology" not in zones:
            issues.append(_mk_issue(
                "VV_FAILED",
                "ENDOGENOUS_TARGET_UNMARKED",
                f"{stage_label} 目标非外生（target_exogenous=false）时须在 non_formalized_zones 登记 reflexive_ecology 并按需挂载第二层 2C（第一层第四节外生边界）",
            ))

    exec_w_raw = review_exit.get("executor_witnesses")
    if shares_repr and not has_ext_exec:
        issues.append(_mk_issue(
            "VV_FAILED",
            "REFLEXIVE_SAME_REPR_LOOP",
            f"{stage_label} 反身互评违规（第一层四.2）：共享同一表征映射且无外部确定性执行器，无法退出代理失真循环",
        ))
    elif shares_repr and has_ext_exec and not exec_w_raw:
        issues.append(_mk_issue(
            "VV_FAILED",
            "UNANCHORED_EXTERNAL_EXIT",
            f"{stage_label} 共享表征且声明存在外部执行器（has_external_executor=true）时须提供 executor_witnesses 回执，禁止裸布尔绕过",
        ))
    elif not shares_repr and (not has_ext_exec or not exec_w_raw):
        issues.append(_mk_issue(
            "VV_FAILED",
            "UNANCHORED_EXTERNAL_EXIT",
            f"{stage_label} 声明独立表征（shares_representation_with_generator=false）须同时提供 has_external_executor=true 与 executor_witnesses 回执，否则按同源处理",
        ))

    verified_exec_witnesses: List[Dict[str, Any]] = []
    if has_ext_exec and exec_w_raw:
        ew = _verify_witness_list(
            exec_w_raw,
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label="review_exit.executor_witnesses",
        )
        # L3 与 S4 同等拒收未编译 ast_only 占位（Lean 见证显式要求 kernel_compiled）
        if not any(
            (item["type"] == "probe_exec" and item.get("verified") is True)
            or (item["type"] == "lean_theorem" and item.get("verified") is True and item.get("verification_level") == "kernel_compiled")
            for item in ew
        ):
            issues.append(_mk_issue(
                "VV_FAILED",
                "EXECUTOR_WITNESS_TYPE_WEAK",
                f"{stage_label} executor_witnesses 至少须含一条已核验通过（verified=true）的 probe_exec 或经 --run-lean 内核编译（kernel_compiled）的 lean_theorem 外部执行锚点，拒绝未编译 ast_only 占位",
            ))
        verified_exec_witnesses = ew
        extra_witnesses.extend(ew)

    actual_state_accepted_verified = False
    bridge_premises: List[str] = []
    sufficient_cert_kind = "finite_probe_only"
    falsification_survived_only = False
    t_assertion_eligible = False
    scope_exclusions = strict_str_list(payload.get("scope_exclusions"), f"{stage_label}.scope_exclusions", allow_none=True)

    if exit_kind == "sufficient_certificate":
        bridge_ref = strict_str(review_exit.get("bridge_premise_ref"), f"{stage_label}.review_exit.bridge_premise_ref", non_empty=False, default="")
        if not bridge_ref:
            issues.append(_mk_issue(
                "VV_FAILED",
                "MISSING_BRIDGE_PREMISE",
                f"{stage_label} exit_kind=sufficient_certificate 必须声明非空 bridge_premise_ref（第一层四.4 显式桥接前提集 B）",
            ))
        else:
            bridge_premises.append(bridge_ref)
        bw = _verify_witness_list(
            review_exit.get("bridge_witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label="review_exit.bridge_witnesses",
        )
        extra_witnesses.extend(bw)
        in_reachable = strict_bool(review_exit.get("actual_state_in_reachable_domain"), f"{stage_label}.review_exit.actual_state_in_reachable_domain", default=False)
        state_accepted = strict_bool(review_exit.get("actual_state_accepted"), f"{stage_label}.review_exit.actual_state_accepted", default=False)
        declared_cert_kind = (
            strict_str(review_exit.get("sufficient_certificate_kind"), f"{stage_label}.review_exit.sufficient_certificate_kind", allowed=SUFFICIENT_CERTIFICATE_KINDS)
            if review_exit.get("sufficient_certificate_kind") is not None
            else None
        )

        if in_reachable and state_accepted:
            aw = _verify_witness_list(
                review_exit.get("acceptance_witnesses"),
                project_root,
                lean_audit,
                allow_probe_exec=allow_probe_exec,
                context_label="review_exit.acceptance_witnesses",
            )
            extra_witnesses.extend(aw)
            all_aw_verified = all(w.get("verified") is True for w in aw)
            actual_state_accepted_verified = all_aw_verified and bool(aw)

            # B07 清零：充分证书证明载体必须由 acceptance_witnesses（aw）本体承载，严禁从 bridge/executor 跨用途借证
            has_exhaustive_probe = any(
                w.get("type") == "probe_exec"
                and w.get("probe_role") == "exhaustive_domain"
                and w.get("live_verified") is True
                and isinstance(w.get("domain_spec"), dict)
                for w in aw
            )
            has_artifact_bound_lean = any(
                w.get("type") == "lean_theorem"
                and w.get("verified") is True
                and w.get("verification_level") == "kernel_compiled"
                and (not w.get("is_builtin_meta_module"))
                and (bool(w.get("artifact_files")) or bool(w.get("artifact_binding")))
                for w in aw
            )
            has_builtin_meta_in_aw = any(
                w.get("type") == "lean_theorem" and w.get("is_builtin_meta_module")
                for w in aw
            )

            if declared_cert_kind == "artifact_bound_lean":
                if has_builtin_meta_in_aw or not has_artifact_bound_lean:
                    issues.append(_mk_issue(
                        "VV_FAILED",
                        "UNBOUND_META_THEOREM",
                        f"{stage_label} 申报 artifact_bound_lean 充分证书，acceptance_witnesses 必须全部为经真内核编译且绑定被审工件的项目专属 Lean 见证（严禁混入内置 LogicCore.lean 元定理或跨用途借证）",
                    ))
                else:
                    sufficient_cert_kind = "artifact_bound_lean"
            elif declared_cert_kind == "exhaustive_domain":
                if not has_exhaustive_probe:
                    issues.append(_mk_issue(
                        "VV_FAILED",
                        "MISSING_EXHAUSTIVE_DOMAIN_WITNESS",
                        f"{stage_label} 申报 exhaustive_domain 充分证书，acceptance_witnesses 中缺少实跑通过且绑定 domain_spec_file 哈希的穷举探针（禁止从其他槽位借证）",
                    ))
                else:
                    sufficient_cert_kind = "exhaustive_domain"
            else:
                if has_artifact_bound_lean and not has_builtin_meta_in_aw:
                    sufficient_cert_kind = "artifact_bound_lean"
                elif has_exhaustive_probe:
                    sufficient_cert_kind = "exhaustive_domain"
                else:
                    sufficient_cert_kind = "finite_probe_only"
                    falsification_survived_only = True

            if (
                actual_state_accepted_verified
                and target_exogenous
                and not scope_exclusions
                and sufficient_cert_kind in ("exhaustive_domain", "artifact_bound_lean")
            ):
                t_assertion_eligible = True
    else:
        exit_basis_ref = strict_str(review_exit.get("exit_basis_ref"), f"{stage_label}.review_exit.exit_basis_ref", non_empty=False, default="")
        if not exit_basis_ref:
            issues.append(_mk_issue(
                "VV_FAILED",
                "MISSING_EXIT_BASIS",
                f"{stage_label} exit_kind={exit_kind} 必须声明非空 exit_basis_ref 与 exit_witnesses 以核验退出分支前提（禁止裸枚举放行）",
            ))
        if review_exit.get("exit_witnesses"):
            ebw = _verify_witness_list(
                review_exit.get("exit_witnesses"),
                project_root,
                lean_audit,
                allow_probe_exec=allow_probe_exec,
                context_label="review_exit.exit_witnesses",
            )
            extra_witnesses.extend(ebw)
        else:
            issues.append(_mk_issue(
                "VV_FAILED",
                "MISSING_EXIT_WITNESS",
                f"{stage_label} exit_kind={exit_kind} 必须提供非空 exit_witnesses 客观见证",
            ))

    exit_meta = {
        "exit_kind": exit_kind,
        "target_exogenous": target_exogenous,
        "target_exogeneity_basis": exog_basis,
        "endogenous_requires_2c": endogenous_requires_2c,
        "actual_state_accepted_verified": actual_state_accepted_verified,
        "sufficient_certificate_kind": sufficient_cert_kind,
        "falsification_survived_only": falsification_survived_only,
        "scope_exclusions": scope_exclusions,
        "bridge_premises": bridge_premises,
        "t_assertion_eligible": t_assertion_eligible,
    }
    return issues, extra_witnesses, exit_meta


def _evaluate_l0_wellformed(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    """通用第一层 L0_WELLFORMED（一.1 载体/命题分离、一.2 良构与三类编码体制、一.3 保守定义）。"""
    _validate_stage_top_fields("L0_WELLFORMED", payload)
    default_regime = strict_str(
        payload.get("encoding_regime"),
        "L0_WELLFORMED.encoding_regime",
        allowed=ENCODING_REGIMES,
        default="refined_subtype",
    )
    guards = payload.get("wellformed_guards")
    if not isinstance(guards, list) or not guards:
        raise GateError("MISSING_DOMAIN_GUARDS", "L0_WELLFORMED 必须声明非空 wellformed_guards 列表（前置类别、路径、接口或契约良构守卫）")

    issues: List[Dict[str, str]] = []
    extra_witnesses: List[Dict[str, Any]] = []

    if payload.get("carrier_vs_proposition_checked") is not None:
        if not strict_bool(payload.get("carrier_vs_proposition_checked"), "L0_WELLFORMED.carrier_vs_proposition_checked"):
            issues.append(_mk_issue("ILL_FORMED", "CARRIER_PROPOSITION_CONFLATION", "L0 违反一.1：未将符号载体局部差错与命题语义及推演污染独立核验"))

    for idx, entry in enumerate(guards, 1):
        if not isinstance(entry, dict):
            raise GateError("SCHEMA_TYPE_ERROR", f"wellformed_guards[{idx}] 必须为对象")
        reject_unknown_fields(entry, ("expression", "status", "encoding_regime", "witnesses", "note"), f"wellformed_guards[{idx}]")
        expr = strict_str(entry.get("expression"), f"wellformed_guards[{idx}].expression")
        status = strict_str(entry.get("status"), f"wellformed_guards[{idx}].status", allowed={"satisfied", "violated"})
        regime = strict_str(entry.get("encoding_regime"), f"wellformed_guards[{idx}].encoding_regime", allowed=ENCODING_REGIMES, default=default_regime)
        gw = _verify_witness_list(
            entry.get("witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label=f"wellformed_guards[{idx}].witnesses",
        )
        extra_witnesses.extend(gw)
        if status == "violated":
            if regime == "base_sort_predicate":
                issues.append(_mk_issue("UNSAT", "DOMAIN_PREDICATE_CONFLICT", f"L0 基类谓词编码下前置域前提冲突（一.2 / 一.4 良构不可满足）: {expr}"))
            else:
                issues.append(_mk_issue("ILL_FORMED", "DOMAIN_GUARD_VIOLATED", f"L0 类型或前置守卫违约（一.2 {regime} 不良构）: {expr}"))

    definitions = payload.get("definitions")
    if definitions is None:
        definitions = []
    if not isinstance(definitions, list):
        raise GateError("SCHEMA_TYPE_ERROR", "L0_WELLFORMED.definitions 必须为列表")
    for idx, item in enumerate(definitions, 1):
        if not isinstance(item, dict):
            raise GateError("SCHEMA_TYPE_ERROR", f"definitions[{idx}] 必须为对象")
        reject_unknown_fields(
            item,
            ("name", "kind", "novel_and_noncircular", "existence_uniqueness_verified", "witnesses", "note"),
            f"definitions[{idx}]",
        )
        dname = strict_str(item.get("name"), f"definitions[{idx}].name")
        dkind = strict_str(item.get("kind"), f"definitions[{idx}].kind", allowed={"predicate_explicit", "definite_description"})
        novel = strict_bool(item.get("novel_and_noncircular"), f"definitions[{idx}].novel_and_noncircular")
        dw = _verify_witness_list(
            item.get("witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label=f"definitions[{idx}]({dname}).witnesses",
        )
        extra_witnesses.extend(dw)
        if not novel:
            issues.append(_mk_issue("ILL_FORMED", "NONCONSERVATIVE_DEFINITION", f"L0 违反一.3：定义 '{dname}' 不满足新颖性或非循环性"))
        if dkind == "definite_description":
            eu = strict_bool(item.get("existence_uniqueness_verified"), f"definitions[{idx}].existence_uniqueness_verified")
            if not eu:
                issues.append(_mk_issue("ILL_FORMED", "MISSING_EXISTENCE_UNIQUENESS", f"L0 违反一.3：摹状词/函数定义 '{dname}' 缺少存在唯一性证明（∃!）"))

    if payload.get("verdict") == "conflict":
        summary = payload.get("conflict_summary") or ""
        if default_regime == "base_sort_predicate":
            issues.append(_mk_issue("UNSAT", "L0_EXPLICIT_CONFLICT", summary or "L0_WELLFORMED 基类谓词编码报告良构不可满足冲突"))
        else:
            issues.append(_mk_issue("ILL_FORMED", "L0_EXPLICIT_CONFLICT", summary or "L0_WELLFORMED 显式报告良构性或定义违约"))

    meta = {"encoding_regime": default_regime, "definition_count": len(definitions)}
    return ("conflict", issues, extra_witnesses, meta) if issues else ("pass", [], extra_witnesses, meta)


def _evaluate_l1_root_consistency(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    """通用第一层 L1_ROOT_CONSISTENCY（一.4 根前提可满足、一.9 有限模块 DAG 无环/唯一性 + 跨层边界属性对账）。"""
    _validate_stage_top_fields("L1_ROOT_CONSISTENCY", payload)
    root_sat = strict_bool(payload.get("root_premises_satisfiable"), "L1_ROOT_CONSISTENCY.root_premises_satisfiable")
    modules = payload.get("modules")
    if not isinstance(modules, list) or not modules:
        raise GateError("MISSING_MODULES", "L1_ROOT_CONSISTENCY 必须声明非空共断言有限模块列表 modules（一.9 DAG 极小核核验）")

    issues: List[Dict[str, str]] = []
    extra_witnesses: List[Dict[str, Any]] = []

    if not root_sat:
        issues.append(_mk_issue("UNSAT", "ROOT_PREMISES_UNSAT", payload.get("conflict_summary") or "L1 根层前提集不可满足（一.4 / 一.9 根层截断）"))

    seen_names: Set[str] = set()
    dep_graph: Dict[str, List[str]] = {}
    for idx, mod in enumerate(modules, 1):
        if not isinstance(mod, dict):
            raise GateError("SCHEMA_TYPE_ERROR", f"modules[{idx}] 必须为对象")
        reject_unknown_fields(mod, ("name", "status", "depends_on", "witnesses", "detail", "note"), f"modules[{idx}]")
        mname = strict_str(mod.get("name"), f"modules[{idx}].name")
        mstatus = strict_str(mod.get("status"), f"modules[{idx}].status", allowed={"consistent", "unsat_core", "inherited_failure"})
        if mname in seen_names:
            raise GateError("L1_DUPLICATE_MODULE_NAME", f"L1 共断言模块名称重复: {mname!r}")
        seen_names.add(mname)
        deps = strict_str_list(mod.get("depends_on"), f"modules[{idx}].depends_on", allow_none=True)
        dep_graph[mname] = deps

        mw = _verify_witness_list(
            mod.get("witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label=f"modules[{idx}]({mname}).witnesses",
        )
        extra_witnesses.extend(mw)
        if mstatus == "unsat_core":
            detail = strict_str(mod.get("detail"), f"modules[{idx}].detail", non_empty=False, default=mname) or mname
            issues.append(_mk_issue("UNSAT", "L1_MINIMAL_UNSAT_CORE", f"L1 共断言模块存在极小不可满足核 ({mname}): {detail}"))
        elif mstatus == "inherited_failure":
            detail = strict_str(mod.get("detail"), f"modules[{idx}].detail", non_empty=False, default=mname) or mname
            issues.append(_mk_issue("UNSAT", "L1_INHERITED_UPSTREAM_FAILURE", f"L1 模块继承上游冲突或派生失效 ({mname}): {detail}"))

    # B3 清零：验证 depends_on 引用存在性与 DAG 无环性
    for mname, deps in dep_graph.items():
        for dep in deps:
            if dep not in seen_names:
                raise GateError("L1_UNKNOWN_DEPENDENCY", f"L1 模块 {mname!r} 依赖未声明的模块 {dep!r}")
    cycle = _detect_dependency_cycle(dep_graph)
    if cycle is not None:
        issues.append(_mk_issue(
            "UNSAT",
            "L1_DEPENDENCY_CYCLE",
            f"L1 违反一.9 有限依赖有向无环图（DAG）：检测到模块循环依赖 {' -> '.join(cycle)}",
        ))

    # ATK-3 + B08 清零：跨记录按 object_name 全局聚合边界属性对账并要求共享属性键全覆盖
    boundary_contracts = payload.get("boundary_contracts")
    if boundary_contracts is not None:
        if not isinstance(boundary_contracts, list):
            raise GateError("SCHEMA_TYPE_ERROR", "L1_ROOT_CONSISTENCY.boundary_contracts 必须为列表")
        global_obj_attrs: Dict[str, Dict[str, Tuple[Any, str]]] = {}
        global_obj_comp_keys: Dict[str, List[Tuple[str, Set[str]]]] = {}
        for bidx, bc in enumerate(boundary_contracts, 1):
            if not isinstance(bc, dict):
                raise GateError("SCHEMA_TYPE_ERROR", f"boundary_contracts[{bidx}] 必须为对象")
            reject_unknown_fields(bc, ("object_name", "components", "note"), f"boundary_contracts[{bidx}]")
            obj_name = strict_str(bc.get("object_name"), f"boundary_contracts[{bidx}].object_name")
            comps = bc.get("components")
            if not isinstance(comps, list) or len(comps) < 2:
                raise GateError("SCHEMA_VALUE_ERROR", f"boundary_contracts[{bidx}].components 至少需包含 2 个待对账组件")
            attr_seen = global_obj_attrs.setdefault(obj_name, {})
            comp_keys_list = global_obj_comp_keys.setdefault(obj_name, [])
            for cidx, comp in enumerate(comps, 1):
                if not isinstance(comp, dict):
                    raise GateError("SCHEMA_TYPE_ERROR", f"boundary_contracts[{bidx}].components[{cidx}] 必须为对象")
                reject_unknown_fields(comp, ("component", "source_file", "attributes", "note"), f"boundary_contracts[{bidx}].components[{cidx}]")
                comp_name = strict_str(comp.get("component"), f"boundary_contracts[{bidx}].components[{cidx}].component")
                if comp.get("source_file") is not None:
                    strict_str(comp.get("source_file"), f"boundary_contracts[{bidx}].components[{cidx}].source_file")
                attrs = comp.get("attributes")
                if not isinstance(attrs, dict) or not attrs:
                    raise GateError("SCHEMA_TYPE_ERROR", f"boundary_contracts[{bidx}].components[{cidx}].attributes 必须为非空对象")
                comp_keys_list.append((comp_name, set(attrs.keys())))
                for ak, av in attrs.items():
                    if not isinstance(av, (str, bool)):
                        raise GateError("SCHEMA_TYPE_ERROR", f"boundary_contracts[{bidx}].components[{cidx}].attributes.{ak} 仅允许字符串或布尔值")
                    if ak in attr_seen:
                        prev_val, prev_comp = attr_seen[ak]
                        if prev_val != av or type(prev_val) is not type(av):
                            issues.append(_mk_issue(
                                "UNSAT",
                                "CROSS_LAYER_NORMALIZATION_MISMATCH",
                                f"L1 跨层边界规范化冲突（对象 {obj_name} 属性 {ak}）：{prev_comp}={prev_val!r} 与 {comp_name}={av!r} 不一致",
                            ))
                    else:
                        attr_seen[ak] = (av, comp_name)
        for obj_name, comp_keys_list in global_obj_comp_keys.items():
            union_keys: Set[str] = set()
            for _, ks in comp_keys_list:
                union_keys.update(ks)
            for comp_name, ks in comp_keys_list:
                missing_k = sorted(union_keys - ks)
                if missing_k:
                    issues.append(_mk_issue(
                        "UNSAT",
                        "CROSS_LAYER_BOUNDARY_KEY_MISSING",
                        f"L1 跨层边界对账缺失（对象 {obj_name}）：组件 {comp_name} 遗漏共享边界属性键 {missing_k}（禁止互补空键绕过对账）",
                    ))

    if payload.get("alternative_branches_isolated") is not None:
        if not strict_bool(payload.get("alternative_branches_isolated"), "L1_ROOT_CONSISTENCY.alternative_branches_isolated"):
            issues.append(_mk_issue("UNSAT", "L1_ALT_BRANCH_CONFLATION", "L1 违反一.9：将互斥备选分支与共断言模块混同联合断言"))
    if payload.get("symptom_erasure_detected") is not None:
        if strict_bool(payload.get("symptom_erasure_detected"), "L1_ROOT_CONSISTENCY.symptom_erasure_detected"):
            issues.append(_mk_issue("UNSAT", "L1_SYMPTOM_ERASURE_FALLACY", "L1 违反一.9 防消症伪相容：仅消除下游局部症状而未命中根层不可满足核"))
    if payload.get("verdict") == "conflict":
        issues.append(_mk_issue("UNSAT", "L1_EXPLICIT_CONFLICT", payload.get("conflict_summary") or "L1_ROOT_CONSISTENCY 显式报告根层不可满足冲突"))

    meta = {"module_count": len(modules), "root_premises_satisfiable": root_sat}
    return ("conflict", issues, extra_witnesses, meta) if issues else ("pass", [], extra_witnesses, meta)


def _evaluate_l2_constraint_role(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    """通用第一层 L2_CONSTRAINT_ROLE（一.6 反伪折中、一.7 比较三要素、一.8 保编号七分与约束间并列三分）。"""
    _validate_stage_top_fields("L2_CONSTRAINT_ROLE", payload)
    issues: List[Dict[str, str]] = []
    extra_witnesses: List[Dict[str, Any]] = []

    basis_explicit = strict_bool(payload.get("decision_basis_explicit"), "L2_CONSTRAINT_ROLE.decision_basis_explicit", default=False)
    if not basis_explicit:
        issues.append(_mk_issue(
            "UNDETERMINED",
            "MISSING_CLOSURE_BASIS",
            "L2 欠封闭判定基准未显式化（一.4 语义未定）：须明示附加选解准则与适用边界，或显式声明未定",
        ))
    else:
        dbw = _verify_witness_list(
            payload.get("decision_basis_witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label="decision_basis_witnesses",
        )
        extra_witnesses.extend(dbw)

    constraints = payload.get("declared_constraints")
    if not isinstance(constraints, list) or not constraints:
        raise GateError("MISSING_CONSTRAINTS", "L2_CONSTRAINT_ROLE 必须提供非空 declared_constraints 列表（一.8 保编号七分核验）")

    allowed_roles_desc = sorted(OBJECTIVE_ROLES | COGNITIVE_ROLES)
    l2_constraint_roles: Dict[str, str] = {}
    for idx, item in enumerate(constraints, 1):
        if not isinstance(item, dict):
            raise GateError("SCHEMA_TYPE_ERROR", f"declared_constraints[{idx}] 必须为对象")
        reject_unknown_fields(
            item,
            ("name", "objective_role", "witnesses", "standard_claims", "claimed_as_axiom_standard", "note"),
            f"declared_constraints[{idx}]",
        )
        cname = strict_str(item.get("name"), f"declared_constraints[{idx}].name")
        raw_role = strict_str(item.get("objective_role"), f"declared_constraints[{idx}].objective_role")
        role = _normalize_objective_role(raw_role)
        if role is None:
            raise GateError("SCHEMA_VALUE_ERROR", f"declared_constraints[{idx}].objective_role 非法: {raw_role!r}（允许: {allowed_roles_desc}）")
        l2_constraint_roles[cname] = role
        item_w = _verify_witness_list(
            item.get("witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label=f"declared_constraints[{idx}]({cname}).witnesses",
        )
        extra_witnesses.extend(item_w)

        claims: Set[str] = set()
        if item.get("standard_claims") is not None:
            claims.update(strict_str_list(item.get("standard_claims"), f"declared_constraints[{idx}].standard_claims", allowed=STANDARD_CLAIMS, allow_none=False))
        claimed_std = strict_bool(item.get("claimed_as_axiom_standard"), f"declared_constraints[{idx}].claimed_as_axiom_standard", default=False)
        if claimed_std and not claims:
            claims = {"A->T", "not_A->not_T"}

        if role == "unclassified":
            issues.append(_mk_issue(
                "UNDETERMINED",
                "UNCLASSIFIED_CONSTRAINT",
                f"L2 约束 '{cname}' 尚处认知态 unclassified（一.8 严禁未核验直接放行或冒充第 5 类）",
            ))
            if claims:
                issues.append(_mk_issue(
                    "OVERREACH",
                    "UNCLASSIFIED_STANDARD_CLAIM",
                    f"L2 约束越权：未判定约束 '{cname}' 不得提出任何方向性标准申称 {sorted(claims)}",
                ))
            continue

        if role in ("veto_only", "complementary_veto"):
            issues.append(_mk_issue(
                "UNSAT",
                "CONSTRAINT_VETO_CONFLICT",
                f"L2 约束否决冲突：约束 '{cname}' 相对联合断言目标处于一.8 第 4 类否决状态（{role}）",
            ))

        allowed_for_role = ROLE_IMPLIED_CLAIMS[role]
        bad_claims = sorted(claims - allowed_for_role)
        if bad_claims:
            issues.append(_mk_issue(
                "OVERREACH",
                "CONSTRAINT_STANDARD_OVERREACH",
                f"L2 约束越权（一.8）：约束 '{cname}'（角色 {role}）不得申称未获蕴含的标准 {bad_claims}",
            ))

    pairs = payload.get("constraint_pairs")
    if pairs is None:
        pairs = []
    if not isinstance(pairs, list):
        raise GateError("SCHEMA_TYPE_ERROR", "L2_CONSTRAINT_ROLE.constraint_pairs 必须为列表")
    for idx, pair in enumerate(pairs, 1):
        if not isinstance(pair, dict):
            raise GateError("SCHEMA_TYPE_ERROR", f"constraint_pairs[{idx}] 必须为对象")
        reject_unknown_fields(
            pair,
            ("constraint_a", "constraint_b", "relation", "claimed_hierarchy", "explicit_preference_declared", "co_asserted", "note"),
            f"constraint_pairs[{idx}]",
        )
        ca = strict_str(pair.get("constraint_a"), f"constraint_pairs[{idx}].constraint_a")
        cb = strict_str(pair.get("constraint_b"), f"constraint_pairs[{idx}].constraint_b")
        if ca not in l2_constraint_roles or cb not in l2_constraint_roles:
            missing_refs = [x for x in (ca, cb) if x not in l2_constraint_roles]
            issues.append(_mk_issue(
                "OVERREACH",
                "UNDECLARED_PAIR_CONSTRAINT",
                f"L2 约束对 ({ca}, {cb}) 引用了未在 declared_constraints 中声明的约束: {missing_refs}",
            ))
        rel = strict_str(pair.get("relation"), f"constraint_pairs[{idx}].relation", allowed={"subordinate", "mutually_exclusive", "juxtaposed"})
        claimed_h = strict_bool(pair.get("claimed_hierarchy"), f"constraint_pairs[{idx}].claimed_hierarchy", default=False)
        pref_decl = strict_bool(pair.get("explicit_preference_declared"), f"constraint_pairs[{idx}].explicit_preference_declared", default=False)
        co_asserted = strict_bool(pair.get("co_asserted"), f"constraint_pairs[{idx}].co_asserted", default=False)
        if rel == "juxtaposed" and claimed_h and not pref_decl:
            issues.append(_mk_issue(
                "OVERREACH",
                "JUXTAPOSED_FAKE_HIERARCHY",
                f"L2 违反一.8 并列律：逻辑并列约束 ({ca}, {cb}) 无先天高低，未显式声明偏好序前严禁虚构先后层级",
            ))
        if rel == "mutually_exclusive" and co_asserted:
            issues.append(_mk_issue(
                "UNSAT",
                "MUTUALLY_EXCLUSIVE_COASSERTED",
                f"L2 违反一.8：互斥约束 ({ca}, {cb}) 被同时联合断言",
            ))

    comparisons = payload.get("comparisons")
    if comparisons is None:
        comparisons = []
    if not isinstance(comparisons, list):
        raise GateError("SCHEMA_TYPE_ERROR", "L2_CONSTRAINT_ROLE.comparisons 必须为列表")
    for idx, comp in enumerate(comparisons, 1):
        if not isinstance(comp, dict):
            raise GateError("SCHEMA_TYPE_ERROR", f"comparisons[{idx}] 必须为对象")
        reject_unknown_fields(comp, ("claim", "objects_fixed", "relation_fixed", "constraints_fixed", "note"), f"comparisons[{idx}]")
        cclaim = strict_str(comp.get("claim"), f"comparisons[{idx}].claim")
        obj_f = strict_bool(comp.get("objects_fixed"), f"comparisons[{idx}].objects_fixed", default=False)
        rel_f = strict_bool(comp.get("relation_fixed"), f"comparisons[{idx}].relation_fixed", default=False)
        con_f = strict_bool(comp.get("constraints_fixed"), f"comparisons[{idx}].constraints_fixed", default=False)
        if not (obj_f and rel_f and con_f):
            issues.append(_mk_issue(
                "OVERREACH",
                "INCOMPLETE_COMPARISON_SPEC",
                f"L2 违反一.7 比较语义完备性：'{cclaim}' 未同时显式固定比较对象、比较关系与约束条件",
            ))

    if payload.get("pseudo_moderation_detected") is not None:
        if strict_bool(payload.get("pseudo_moderation_detected"), "L2_CONSTRAINT_ROLE.pseudo_moderation_detected"):
            issues.append(_mk_issue(
                "OVERREACH",
                "PSEUDO_MODERATION_VIOLATION",
                "L2 违反一.6：逻辑状态已确立时存在无前提支撑的定性软化或伪折中",
            ))

    if payload.get("verdict") == "conflict":
        issues.append(_mk_issue("UNSAT", "L2_EXPLICIT_CONFLICT", payload.get("conflict_summary") or "L2_CONSTRAINT_ROLE 显式报告约束冲突"))

    meta = {
        "constraint_count": len(constraints),
        "constraint_roles": l2_constraint_roles,
        "pair_count": len(pairs),
        "comparison_count": len(comparisons),
    }
    return ("conflict", issues, extra_witnesses, meta) if issues else ("pass", [], extra_witnesses, meta)


def _evaluate_l3_empirical_exit(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    """通用第一层 L3_EMPIRICAL_EXIT（三.1–三.5 融贯与符合分离、防泄漏与防循环；四.1–四.4 反身三支退出）。"""
    _validate_stage_top_fields("L3_EMPIRICAL_EXIT", payload)
    issues: List[Dict[str, str]] = []
    extra_witnesses: List[Dict[str, Any]] = []

    int_ver = payload.get("internal_verification")
    ext_val = payload.get("external_validation")
    if not isinstance(int_ver, dict) or not isinstance(ext_val, dict):
        raise GateError("MISSING_VV_SEPARATION", "L3_EMPIRICAL_EXIT 必须提供独立的 internal_verification（融贯/内部校核）与 external_validation（符合/独立实测）字段（三.1）")
    reject_unknown_fields(int_ver, ("status", "note"), "L3_EMPIRICAL_EXIT.internal_verification")
    reject_unknown_fields(ext_val, ("status", "note"), "L3_EMPIRICAL_EXIT.external_validation")

    ver_status = strict_str(int_ver.get("status"), "internal_verification.status", allowed={"passed", "failed"})
    val_status = strict_str(ext_val.get("status"), "external_validation.status", allowed={"passed", "failed", "not_executed"})
    if ver_status == "failed":
        issues.append(_mk_issue("VV_FAILED", "VERIFICATION_FAILED", "L3 内部校核失败：internal_verification.status 为 failed"))
    if val_status == "failed":
        issues.append(_mk_issue("VV_FAILED", "VALIDATION_FAILED", "L3 外部独立实测失败：external_validation.status 为 failed"))

    con_inputs = set(strict_str_list(payload.get("construction_inputs"), "L3_EMPIRICAL_EXIT.construction_inputs", allow_none=True))
    eval_inputs = set(strict_str_list(payload.get("evaluation_inputs"), "L3_EMPIRICAL_EXIT.evaluation_inputs", allow_none=True))
    overlap = sorted(con_inputs & eval_inputs)
    if overlap:
        issues.append(_mk_issue("VV_FAILED", "DATASET_LEAKAGE", f"L3 拟合/构造与独立检验泄漏（三.2）：条目 {overlap} 同时出现在 construction_inputs 与 evaluation_inputs"))

    pos_audit = payload.get("positive_inference_audit")
    if pos_audit is not None:
        if not isinstance(pos_audit, dict):
            raise GateError("SCHEMA_TYPE_ERROR", "positive_inference_audit 必须为对象")
        reject_unknown_fields(pos_audit, ("premise_observation_satisfiable", "observation_non_idle", "dependency_chain_free_of_H", "note"), "positive_inference_audit")
        if pos_audit.get("premise_observation_satisfiable") is not None and not strict_bool(pos_audit.get("premise_observation_satisfiable"), "positive_inference_audit.premise_observation_satisfiable"):
            issues.append(_mk_issue("VV_FAILED", "VACUOUS_PREDICTION_AGREEMENT", "L3 违反三.4：A ∪ {H, o_act} 不可满足，存在空真预测相合"))
        if pos_audit.get("observation_non_idle") is not None and not strict_bool(pos_audit.get("observation_non_idle"), "positive_inference_audit.observation_non_idle"):
            issues.append(_mk_issue("VV_FAILED", "IDLE_OBSERVATION_CLAIM", "L3 违反三.4：背景前提已蕴含 H（A ⊨ H），本次观测属闲置观测"))
        if pos_audit.get("dependency_chain_free_of_H") is not None and not strict_bool(pos_audit.get("dependency_chain_free_of_H"), "positive_inference_audit.dependency_chain_free_of_H"):
            issues.append(_mk_issue("VV_FAILED", "CIRCULAR_CONFIRMATION", "L3 违反三.4：辅助前提或实测陈述的取得/解释依赖链含待检假说 H，退化为循环论证"))

    exit_issues, exit_witnesses, exit_meta = _evaluate_review_exit_block(
        payload,
        project_root,
        lean_audit,
        allow_probe_exec=allow_probe_exec,
        stage_label="L3",
    )
    issues.extend(exit_issues)
    extra_witnesses.extend(exit_witnesses)

    if payload.get("verdict") == "conflict":
        issues.append(_mk_issue("VV_FAILED", "L3_EXPLICIT_CONFLICT", payload.get("conflict_summary") or "L3_EMPIRICAL_EXIT 显式报告实证或反身退出冲突"))

    allow_t_assertion = (
        ver_status == "passed"
        and val_status == "passed"
        and exit_meta["t_assertion_eligible"] is True
    )
    meta = {
        **exit_meta,
        "verification_status": ver_status,
        "validation_status": val_status,
        "allow_t_assertion": allow_t_assertion,
        "evaluation_inputs": sorted(eval_inputs),
        "construction_inputs": sorted(con_inputs),
    }
    return ("conflict", issues, extra_witnesses, meta) if issues else ("pass", [], extra_witnesses, meta)


def _evaluate_s4_local_spectral_vv(
    payload: Dict[str, Any],
    project_root: Path,
    lean_audit: Dict[str, Any],
    *,
    allow_probe_exec: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, Any]], Dict[str, Any]]:
    _validate_stage_top_fields("S4_LOCAL_SPECTRAL_VV", payload)
    issues: List[Dict[str, str]] = []
    extra_witnesses: List[Dict[str, Any]] = []

    ver = payload.get("verification")
    val = payload.get("validation")
    if not isinstance(ver, dict) or not isinstance(val, dict):
        raise GateError("MISSING_VV_SEPARATION", "S4_LOCAL_SPECTRAL_VV 必须同时提供独立的 verification 与 validation 对象字段（2A.4）")
    reject_unknown_fields(ver, ("status", "note"), "S4_LOCAL_SPECTRAL_VV.verification")
    reject_unknown_fields(val, ("status", "note"), "S4_LOCAL_SPECTRAL_VV.validation")

    ver_status = strict_str(ver.get("status"), "verification.status", allowed={"passed", "failed"})
    val_status = strict_str(val.get("status"), "validation.status", allowed={"passed", "failed", "not_executed"})
    if ver_status == "failed":
        issues.append(_mk_issue("VV_FAILED", "VERIFICATION_FAILED", "S4 校核失败：verification.status 为 failed"))
    if val_status == "failed":
        issues.append(_mk_issue("VV_FAILED", "VALIDATION_FAILED", "S4 确认失败：validation.status 为 failed"))

    cal_sets = set(strict_str_list(payload.get("calibration_datasets"), "S4_LOCAL_SPECTRAL_VV.calibration_datasets", allow_none=True))
    val_sets = set(strict_str_list(payload.get("validation_datasets"), "S4_LOCAL_SPECTRAL_VV.validation_datasets", allow_none=True))
    overlap = sorted(cal_sets & val_sets)
    if overlap:
        issues.append(_mk_issue("VV_FAILED", "DATASET_LEAKAGE", f"S4 标定与确认数据泄漏（2A.4）：数据集 {overlap} 同时出现在标定集与独立确认集"))

    res_sep = strict_bool(payload.get("residual_error_separated"), "S4_LOCAL_SPECTRAL_VV.residual_error_separated", default=False)
    if not res_sep:
        issues.append(_mk_issue("VV_FAILED", "RESIDUAL_ERROR_UNSEPARATED", "S4 残差与真解误差未分离（层 1.5 二.A.4）：须显式区分精确零点、值域残差与定义域误差"))
    else:
        rw = _verify_witness_list(
            payload.get("residual_error_witnesses"),
            project_root,
            lean_audit,
            allow_probe_exec=allow_probe_exec,
            context_label="residual_error_witnesses",
        )
        extra_witnesses.extend(rw)

    exit_issues, exit_witnesses, exit_meta = _evaluate_review_exit_block(
        payload,
        project_root,
        lean_audit,
        allow_probe_exec=allow_probe_exec,
        stage_label="S4",
    )
    issues.extend(exit_issues)
    extra_witnesses.extend(exit_witnesses)

    if strict_bool(payload.get("is_surrogate_or_rom"), "S4_LOCAL_SPECTRAL_VV.is_surrogate_or_rom", default=False):
        if payload.get("strong_classical_baseline") is None or not strict_str(payload.get("strong_classical_baseline"), "strong_classical_baseline", non_empty=False):
            issues.append(_mk_issue("VV_FAILED", "MISSING_STRONG_BASELINE", "S4 降阶/代理比较违规（2A.5）：须声明无需训练的最强经典求解器基线或限定比较宣称范围"))
        if not strict_bool(payload.get("offline_cost_included"), "S4_LOCAL_SPECTRAL_VV.offline_cost_included", default=False):
            issues.append(_mk_issue("VV_FAILED", "MISSING_OFFLINE_COST", "S4 降阶/代理比较违规（2A.5）：须将数据生成、训练与再训练全部离线与在线开销计入总成本"))
        else:
            ow = _verify_witness_list(
                payload.get("offline_cost_witnesses"),
                project_root,
                lean_audit,
                allow_probe_exec=allow_probe_exec,
                context_label="offline_cost_witnesses",
            )
            extra_witnesses.extend(ow)

    if payload.get("verdict") == "conflict":
        issues.append(_mk_issue("VV_FAILED", "S4_EXPLICIT_CONFLICT", payload.get("conflict_summary") or "S4_LOCAL_SPECTRAL_VV 显式报告局部适定或 V&V 冲突"))

    allow_t_assertion = (
        ver_status == "passed"
        and val_status == "passed"
        and exit_meta["t_assertion_eligible"] is True
    )
    meta = {
        **exit_meta,
        "verification_status": ver_status,
        "validation_status": val_status,
        "allow_t_assertion": allow_t_assertion,
        "validation_datasets": sorted(val_sets),
        "calibration_datasets": sorted(cal_sets),
    }
    return ("conflict", issues, extra_witnesses, meta) if issues else ("pass", [], extra_witnesses, meta)


_STAGE_EVALUATORS = {
    "L0_WELLFORMED": _evaluate_l0_wellformed,
    "L1_ROOT_CONSISTENCY": _evaluate_l1_root_consistency,
    "L2_CONSTRAINT_ROLE": _evaluate_l2_constraint_role,
    "L3_EMPIRICAL_EXIT": _evaluate_l3_empirical_exit,
    "S0_TYPING": _evaluate_s0_typing,
    "S1_ROOT_MEASURE": _evaluate_s1_root_measure,
    "S2_SYMMETRY_JUMP": _evaluate_s2_symmetry_jump,
    "S3_CLOSURE_CONSTRAINT": _evaluate_s3_closure_constraint,
    "S4_LOCAL_SPECTRAL_VV": _evaluate_s4_local_spectral_vv,
}
