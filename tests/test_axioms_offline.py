# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""tests/test_axioms_offline.py - cae-axioms 软公理分册、双档硬公理状态机、净室复演与纯核 Lean 4 反空真门禁离线回归。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AXIOMS_DIR = REPO_ROOT / "template" / "user-guard" / "skills" / "cae-axioms"
GATE_SCRIPT = AXIOMS_DIR / "scripts" / "axiom_gate.py"
LEAN_DIR = AXIOMS_DIR / "lean"


def _run_gate(*args: str, cwd: Path = REPO_ROOT) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(GATE_SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(
            f"axiom_gate.py 输出非法 JSON (rc={proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        ) from exc
    return proc.returncode, payload


class TestCaeAxiomsOffline(unittest.TestCase):
    def test_01_skill_structure_and_lean_anti_vacuity_gate_passes(self) -> None:
        self.assertTrue((AXIOMS_DIR / "SKILL.md").is_file())
        skill_text = (AXIOMS_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill_text.startswith("---\nname: cae-axioms"))
        for ref_name in (
            "layer1-logic-epistemology.md",
            "layer1_5-math-computation.md",
            "layer2-domain-audit.md",
            "layer2m-rational-mechanics.md",
            "layer-citations.md",
        ):
            self.assertTrue((AXIOMS_DIR / "references" / ref_name).is_file(), ref_name)

        rc, rep = _run_gate("check-lean")
        self.assertEqual(rc, 0, rep)
        self.assertTrue(rep["ok"])
        self.assertTrue(rep["ast_gate_ok"])
        self.assertEqual(rep["violation_count"], 0)
        self.assertEqual(
            set(rep["checked_files"]),
            {"LogicCore.lean", "ConvexDirectSum.lean", "GalileanBox.lean", "DissipationGeneric.lean"},
        )
        # 核验四个模块核心定理（含 LogicCore 40 条纯核定理）与全部 structure/inductive Nonempty 见证
        for thm in (
            "strawson_russell_bivalent_collapse",
            "semantic_trichotomy_exhaustive",
            "identity_relock_theory_nonequiv",
            "definition_relaxation_violates_original",
            "constraint_seven_way_exhaustive",
            "constraint_role_unique",
            "validOccupancy_iff_seven_patterns",
            "exists_role_negSufficient",
            "pair_three_way_exhaustive",
            "earliest_root_conflict_unique",
            "exists_earliest_root_conflict",
            "finite_module_repair_must_hit_core",
            "finite_module_repair_drops_core_member",
            "branch_symptom_erasure_counterexample",
            "diamond_joint_conflict_no_single_bad_node",
            "noninjective_map_no_left_inverse_on_fiber",
            "same_repr_checker_cannot_separate_mixed_fiber",
        ):
            self.assertIn(thm, rep["public_theorems"]["LogicCore"])
        self.assertEqual(len(rep["public_theorems"]["LogicCore"]), 40)
        self.assertIn("convex_shrink_preserves_proj0", rep["public_theorems"]["ConvexDirectSum"])
        self.assertIn("directSum_decomp_unique", rep["public_theorems"]["ConvexDirectSum"])
        self.assertIn("convex_quadratic_gap_ne", rep["public_theorems"]["ConvexDirectSum"])
        self.assertIn("double_limit_contradiction", rep["public_theorems"]["ConvexDirectSum"])
        self.assertIn(
            "conservation_and_positivity_not_implies_scalar_functional_monotone",
            rep["public_theorems"]["ConvexDirectSum"],
        )
        self.assertIn("parallel_axis_internal_energy", rep["public_theorems"]["GalileanBox"])
        self.assertIn("box_vol_pos", rep["public_theorems"]["GalileanBox"])
        self.assertIn("galilean_com_frame_unique", rep["public_theorems"]["GalileanBox"])
        self.assertIn("coincident_interface_merged_balance", rep["public_theorems"]["GalileanBox"])
        self.assertIn("comoving_flux_cancels_translational_work", rep["public_theorems"]["GalileanBox"])
        self.assertIn("generic_double_degeneracy_laws", rep["public_theorems"]["DissipationGeneric"])
        self.assertIn("not_le_neg_one", rep["public_theorems"]["DissipationGeneric"])
        self.assertIn("finite_diff_onsager_symmetric_psd_nonneg", rep["public_theorems"]["DissipationGeneric"])
        self.assertIn("generic_operator_implies_double_degeneracy", rep["public_theorems"]["DissipationGeneric"])
        self.assertIn(
            "genericOperatorSystem_nontrivial_active_witness",
            rep["public_theorems"]["DissipationGeneric"],
        )
        for mod, structs in rep["structures"].items():
            inds = rep["inductives"][mod]
            self.assertEqual(
                len(structs) + len(inds),
                len(rep["nonempty_witnesses"][mod]),
                f"{mod} 存在缺失 Nonempty 见证的结构体或归纳类型",
            )

    def test_02_lean_anti_vacuity_blocks_all_bypass_vectors_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="axioms-lean-neg-") as tmp:
            tmp_lean = Path(tmp)
            # 1) 合法纯核文件（注释里提 sorry/axiom 不误报，白名单 set_option autoImplicit false 放行）
            (tmp_lean / "Clean.lean").write_text(
                "-- comment mentioning sorry, sorryAx, axiom, Mathlib, native_decide\n"
                "/- nested /- sorry -/ axiom -/\n"
                "set_option autoImplicit false\n"
                "structure Box where\n  val : Nat\n"
                "theorem box_nonempty : Nonempty Box := ⟨{ val := 1 }⟩\n"
                "inductive Flag : Type where\n  | on\n  | off\n"
                "theorem flag_nonempty : Nonempty Flag := ⟨Flag.on⟩\n",
                encoding="utf-8",
            )
            rc_clean, rep_clean = _run_gate("check-lean", "--lean-dir", str(tmp_lean))
            self.assertEqual(rc_clean, 0, rep_clean)

            # 2) --run-lean 指定不存在编译器时必须 fail-closed（ok=False，返回码 2）
            rc_fc, rep_fc = _run_gate("check-lean", "--lean-dir", str(tmp_lean), "--run-lean", "--lean-cmd", "definitely_missing_lean_xyz")
            self.assertEqual(rc_fc, 2, rep_fc)
            self.assertFalse(rep_fc["ok"])
            self.assertEqual(rep_fc["lean_compiler"]["status"], "not_installed")

            # 3) 反空真绕过攻击向量：带前提假设的 Nonempty、否定 Nonempty、尾随箭头、函数类型包装 (Bad → Unit)、法式引号标识符、同行属性定理、同文件重名结构、sorryAx、native_decide、行中 set_option、#exit/#eval、空 inductive
            (tmp_lean / "Bypass.lean").write_text(
                "import Mathlib.Data.Real.Basic\n"
                "theorem inline_opt : True := by set_option debug.skipKernelTC true in trivial\n"
                "theorem «extra proof» : True := True.intro\n"
                "@[simp] theorem marked_pub : True := True.intro\n"
                "axiom fake_postulate : False\n"
                "structure EmptyA where\n  h : False\n"
                "theorem assumed_a (h : Nonempty EmptyA) : Nonempty EmptyA := h\n"
                "structure EmptyB where\n  h : False\n"
                "theorem neg_b : ¬ Nonempty EmptyB := by sorryAx\n"
                "structure EmptyC where\n  h : False\n"
                "theorem arrow_c : Nonempty (EmptyC) → False := fun _ => native_decide\n"
                "structure EmptyD where\n  h : False\n"
                "theorem fn_wrap_d : Nonempty (EmptyD → Unit) := ⟨fun _ => ()⟩\n"
                "namespace A\nstructure DupS where\n  h : False\nend A\n"
                "namespace B\nstructure DupS where\n  u : Unit\nexample : Nonempty DupS := ⟨{ u := () }⟩\nend B\n"
                "inductive VoidInd : Type where\n"
                "#eval 1 + 1\n"
                "#exit\n",
                encoding="utf-8",
            )
            rc_bad, rep_bad = _run_gate("check-lean", "--lean-dir", str(tmp_lean))
            self.assertEqual(rc_bad, 2, rep_bad)
            self.assertFalse(rep_bad["ast_gate_ok"])
            self.assertIn("marked_pub", rep_bad["public_theorems"]["Bypass"])
            codes = {v["code"] for v in rep_bad["violations"]}
            for expected_code in (
                "FORBIDDEN_IMPORT",
                "FORBIDDEN_SET_OPTION",
                "FORBIDDEN_QUOTED_IDENT",
                "FORBIDDEN_LEAN_TOKEN",
                "FORBIDDEN_LEAN_DIRECTIVE",
                "DUPLICATE_TYPE_NAME",
                "MISSING_NONEMPTY_WITNESS",
                "EMPTY_INDUCTIVE_TYPE",
            ):
                self.assertIn(expected_code, codes, rep_bad["violations"])
            # 确认四种绕过 EmptyA/EmptyB/EmptyC/EmptyD 均未通过 Nonempty 检查
            for bad_s in ("EmptyA", "EmptyB", "EmptyC", "EmptyD"):
                self.assertFalse(any(w.startswith(f"{bad_s}:") for w in rep_bad["nonempty_witnesses"]["Bypass"]))

            # 4) LogicCore 角色漂移与缺必需定理检测（ROLE_DRIFT + MISSING_REQUIRED_THEOREM）
            drift_dir = Path(tmp) / "drift"
            drift_dir.mkdir()
            (drift_dir / "LogicCore.lean").write_text(
                "inductive ObjectiveRole : Type where\n  | iff\n  | onlyTwo\n"
                "theorem objectiveRole_nonempty : Nonempty ObjectiveRole := ⟨ObjectiveRole.iff⟩\n",
                encoding="utf-8",
            )
            rc_dr, rep_dr = _run_gate("check-lean", "--lean-dir", str(drift_dir))
            self.assertEqual(rc_dr, 2, rep_dr)
            dr_codes = {v["code"] for v in rep_dr["violations"]}
            self.assertIn("ROLE_DRIFT", dr_codes)
            self.assertIn("MISSING_REQUIRED_THEOREM", dr_codes)

    def test_03_happy_path_five_stage_flow_probe_report_and_feedback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="axioms-pass-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "solver_proj"
            proj.mkdir()
            src_file = proj / "box_model.py"
            src_file.write_text(
                "# positive measure control volume box\n"
                "M_MIN = 1e-12\n"
                "V_MIN = 1e-9\n"
                "def exchange(w1, w2, L):\n"
                "    return L * (w2 - w1)\n",
                encoding="utf-8",
            )
            probe_receipt = proj / "probe_ok.log"
            probe_receipt.write_text("jacobian_eigenvalues_real=True\nexit_code=0\n", encoding="utf-8")
            case_file = root / "case.json"

            src_wit = {"type": "source_anchor", "file": "box_model.py", "line_start": 2, "line_end": 3, "excerpt": "V_MIN = 1e-9"}
            offline_probe = {
                "type": "probe_exec",
                "command": "python probe_spectral.py",
                "execute": False,
                "receipt_file": "probe_ok.log",
                "recorded_exit_code": 0,
                "expected_output_substring": "jacobian_eigenvalues_real=True",
            }

            rc, init_out = _run_gate("init", "--case", str(case_file), "--project-root", str(proj), "--subject", "PositiveMeasureBox+Onsager")
            self.assertEqual(rc, 0, init_out)
            self.assertEqual(init_out["next_stage"], "S0_TYPING")

            def submit(cfile: Path, stage: str, body: dict, extra: tuple[str, ...] = ()) -> tuple[int, dict]:
                pfile = root / f"{cfile.stem}_{stage}.json"
                pfile.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
                return _run_gate("advance", "--case", str(cfile), "--stage", stage, "--payload", str(pfile), *extra)

            # 禁止跳步
            rc_skip, skip_out = submit(case_file, "S4_LOCAL_SPECTRAL_VV", {"witnesses": []})
            self.assertEqual(rc_skip, 2)
            self.assertEqual(skip_out["error_code"], "OUT_OF_ORDER_STAGE")

            # 禁止裸布尔无见证填表（含 G7 项级绑定）
            rc_nowit, nowit_out = submit(case_file, "S0_TYPING", {
                "domain_guards": [{"expression": "M > 0", "status": "satisfied"}],
                "witnesses": [src_wit],
            })
            self.assertEqual(rc_nowit, 2)
            self.assertEqual(nowit_out["error_code"], "MISSING_OBJECTIVE_WITNESS")

            # S0_TYPING（项级见证绑定）
            rc, out = submit(case_file, "S0_TYPING", {
                "domain_guards": [
                    {"expression": "M > 0, V >= V_min > 0", "status": "satisfied", "witnesses": [src_wit]},
                ],
                "witnesses": [
                    src_wit,
                    {"type": "lean_theorem", "module": "GalileanBox", "theorem": "positiveMeasureBox_nonempty"},
                ],
            })
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["next_stage"], "S1_ROOT_MEASURE")

            # S1_ROOT_MEASURE
            rc, out = submit(case_file, "S1_ROOT_MEASURE", {
                "measure_checks": {
                    "shrink_limit_vs_positive_measure": {"status": "pass"},
                    "singular_measure_product": {"status": "not_applicable", "reason": "采用有限正测度控制体，无逐点 delta_Gamma 乘积"},
                    "nonlinear_average_commutativity": {"status": "pass"},
                },
                "witnesses": [
                    {"type": "lean_theorem", "module": "GalileanBox", "theorem": "antisym_contact_exchange_cancels"},
                ],
                "non_formalized_zones": ["subcontinuum_cluster"],
            })
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["next_stage"], "S2_SYMMETRY_JUMP")

            # S2_SYMMETRY_JUMP（含实时探针执行）
            rc, out = submit(
                case_file,
                "S2_SYMMETRY_JUMP",
                {
                    "symmetry_group": "galilean",
                    "formulation_type": "positive_measure_box",
                    "mixed_symmetry_groups": False,
                    "state_dim": 4,
                    "independent_jump_relations": 4,
                    "witnesses": [
                        {"type": "lean_theorem", "module": "GalileanBox", "theorem": "parallel_axis_internal_energy"},
                        {
                            "type": "probe_exec",
                            "command": f'"{sys.executable}" -B -c "print(4)"',
                            "execute": True,
                            "expected_exit_code": 0,
                            "expected_output_substring": "4",
                        },
                    ],
                },
                extra=("--allow-probe-exec",),
            )
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["next_stage"], "S3_CLOSURE_CONSTRAINT")

            # S3_CLOSURE_CONSTRAINT（方向性 standard_claims + 项级见证）
            rc, out = submit(case_file, "S3_CLOSURE_CONSTRAINT", {
                "closure_assumptions_explicit": True,
                "closure_witnesses": [src_wit],
                "declared_constraints": [
                    {
                        "name": "positivity_domain",
                        "prototype": "I",
                        "objective_role": "necessary_only",
                        "standard_claims": ["not_A->not_T"],
                        "witnesses": [src_wit],
                    },
                    {
                        "name": "zero_order_mean_conservation",
                        "prototype": "II",
                        "objective_role": "sufficient_only",
                        "standard_claims": ["A->T"],
                        "witnesses": [{"type": "lean_theorem", "module": "ConvexDirectSum", "theorem": "convex_shrink_preserves_proj0"}],
                    },
                ],
                "mechanisms": [
                    {"name": "advection_pressure_work", "parity": "even_reversible", "attached_dissipation_inequality": False},
                    {"name": "onsager_exchange", "parity": "odd_dissipative", "attached_dissipation_inequality": True},
                ],
                "entropy_sign_convention": "concave_s_plus_ds_dU",
                "operator_sign_consistent": True,
                "witnesses": [
                    {"type": "lean_theorem", "module": "DissipationGeneric", "theorem": "finite_diff_onsager_nonneg"},
                ],
            })
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["next_stage"], "S4_LOCAL_SPECTRAL_VV")

            # S4_LOCAL_SPECTRAL_VV（离线单会话推进封顶于 STRUCTURE_PASSED / STRUCTURE_ONLY_UNVERIFIED，绝不直授 PASSED_ALL_GATES）
            rc, out = submit(case_file, "S4_LOCAL_SPECTRAL_VV", {
                "verification": {"status": "passed", "note": "MMS L4 order verified"},
                "validation": {"status": "passed", "note": "Independent shock-tube validation passed"},
                "calibration_datasets": ["exp_cal_01"],
                "validation_datasets": ["exp_val_02"],
                "residual_error_separated": True,
                "residual_error_witnesses": [offline_probe],
                "review_exit": {
                    "exit_kind": "sufficient_certificate",
                    "bridge_premise_ref": "B1: box_model.py matches discrete energy conservation spec",
                    "bridge_witnesses": [src_wit],
                    "actual_state_in_reachable_domain": True,
                    "actual_state_accepted": True,
                    "acceptance_witnesses": [offline_probe],
                    "target_exogenous": True,
                    "target_exogeneity_basis": "物理守恒率与实验激波管观测不依赖代理内部信念",
                    "shares_representation_with_generator": False,
                    "has_external_executor": True,
                    "executor_witnesses": [offline_probe],
                },
                "witnesses": [offline_probe],
            })
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["current_state"], "STRUCTURE_PASSED")
            self.assertEqual(out["verification_level"], "STRUCTURE_ONLY")
            self.assertEqual(out["t_assertion_level"], "NO_T_ASSERTION")
            self.assertEqual(out["original_claim_status"], "STRUCTURE_ONLY_UNVERIFIED")

            # 测 S4 未提供实际放行见证时亦封顶 STRUCTURE_PASSED 且 t_assertion_eligible=False
            case_no_acc = root / "case_no_acc.json"
            _run_gate("init", "--case", str(case_no_acc), "--project-root", str(proj), "--subject", "NoAcceptanceCase")
            for st in ("S0_TYPING", "S1_ROOT_MEASURE", "S2_SYMMETRY_JUMP", "S3_CLOSURE_CONSTRAINT"):
                pfile = root / f"case_{st}.json"
                extra = ("--allow-probe-exec",) if st == "S2_SYMMETRY_JUMP" else ()
                rc_step, out_step = _run_gate("advance", "--case", str(case_no_acc), "--stage", st, "--payload", str(pfile), *extra)
                self.assertEqual(rc_step, 0, out_step)
            rc_na, out_na = submit(case_no_acc, "S4_LOCAL_SPECTRAL_VV", {
                "verification": {"status": "passed"},
                "validation": {"status": "passed"},
                "residual_error_separated": True,
                "residual_error_witnesses": [offline_probe],
                "review_exit": {
                    "exit_kind": "sufficient_certificate",
                    "bridge_premise_ref": "B1: spec",
                    "bridge_witnesses": [src_wit],
                    "target_exogenous": True,
                    "target_exogeneity_basis": "外生实验守恒观测",
                    "shares_representation_with_generator": False,
                    "has_external_executor": True,
                    "executor_witnesses": [offline_probe],
                },
                "witnesses": [offline_probe],
            })
            self.assertEqual(rc_na, 0, out_na)
            self.assertEqual(out_na["current_state"], "STRUCTURE_PASSED")
            saved_na = json.loads(case_no_acc.read_text(encoding="utf-8"))
            self.assertFalse(saved_na["stage_records"]["S4_LOCAL_SPECTRAL_VV"]["meta"]["t_assertion_eligible"])

            # 登记科研反哺建议 + 输出 Markdown 报告（STRUCTURE_PASSED 报告绝不含 PASSED_ALL_GATES）
            rc_fb, fb_out = _run_gate(
                "feedback", "--case", str(case_file), "--target-layer", "2M",
                "--violated-axiom", "2M.一.1",
                "--summary", "补充多相毛细周界线张力的离散化界面投影检验", "--rationale", "两相微通道实验算例暴露界面自由能项需进一步细化",
            )
            self.assertEqual(rc_fb, 0, fb_out)
            self.assertEqual(fb_out["feedback_count"], 1)

            md_file = root / "audit_report.md"
            rc_rep, rep_out = _run_gate("report", "--case", str(case_file), "--output-md", str(md_file))
            self.assertEqual(rc_rep, 0, rep_out)
            self.assertTrue(md_file.is_file())
            md_text = md_file.read_text(encoding="utf-8")
            self.assertIn("STRUCTURE_ONLY_UNVERIFIED", md_text)
            self.assertNotIn("PASSED_ALL_GATES", md_text)
            self.assertIn("目标层 `2M`", md_text)

            # report --require-verified 对 STRUCTURE_PASSED 必须硬阻断（rc=2, UNVERIFIED_STRUCTURE_ONLY）
            rc_rv, rep_rv = _run_gate("report", "--case", str(case_file), "--require-verified")
            self.assertEqual(rc_rv, 2, rep_rv)
            self.assertEqual(rep_rv["error_code"], "UNVERIFIED_STRUCTURE_ONLY")

            # 另测 S4 诚实降格：validation=not_executed 或 exit_kind=nonperceivable_constraint -> t_assertion_eligible=False
            case_no_t = root / "case_no_t.json"
            _run_gate("init", "--case", str(case_no_t), "--project-root", str(proj), "--subject", "NoTAssertionCase")
            for st in ("S0_TYPING", "S1_ROOT_MEASURE", "S2_SYMMETRY_JUMP", "S3_CLOSURE_CONSTRAINT"):
                pfile = root / f"case_{st}.json"
                extra = ("--allow-probe-exec",) if st == "S2_SYMMETRY_JUMP" else ()
                rc_step, out_step = _run_gate("advance", "--case", str(case_no_t), "--stage", st, "--payload", str(pfile), *extra)
                self.assertEqual(rc_step, 0, out_step)
            rc_nt, out_nt = submit(case_no_t, "S4_LOCAL_SPECTRAL_VV", {
                "verification": {"status": "passed"},
                "validation": {"status": "not_executed"},
                "calibration_datasets": ["cal1"],
                "validation_datasets": ["val1"],
                "residual_error_separated": True,
                "residual_error_witnesses": [offline_probe],
                "review_exit": {
                    "exit_kind": "nonperceivable_constraint",
                    "exit_basis_ref": "B_ext: 物理守恒硬约束不经主体感知通道直接作用于控制体状态",
                    "exit_witnesses": [offline_probe],
                    "target_exogenous": True,
                    "target_exogeneity_basis": "外生守恒通量不依赖主体表征",
                    "shares_representation_with_generator": False,
                    "has_external_executor": True,
                    "executor_witnesses": [offline_probe],
                },
                "witnesses": [offline_probe],
            })
            self.assertEqual(rc_nt, 0, out_nt)
            self.assertEqual(out_nt["current_state"], "STRUCTURE_PASSED")
            saved_nt = json.loads(case_no_t.read_text(encoding="utf-8"))
            self.assertFalse(saved_nt["stage_records"]["S4_LOCAL_SPECTRAL_VV"]["meta"]["allow_t_assertion"])

    def test_04_root_conflict_ill_formed_vs_unsat_and_resolve_guards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="axioms-halt-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "two_fluid_proj"
            proj.mkdir()
            (proj / "eqs.tex").write_text("alpha_k * d_t rho_k + div(...) = 0\n", encoding="utf-8")
            tex_wit = [{"type": "source_anchor", "file": "eqs.tex", "line_start": 1, "line_end": 1, "excerpt": "alpha_k"}]

            # 1) S0 定义域违约：默认 refined_subtype -> ILL_FORMED_ROOT；基类谓词编码 base_sort_predicate -> UNSAT_ROOT_CONFLICT
            c_s0 = root / "s0_bad_case.json"
            _run_gate("init", "--case", str(c_s0), "--project-root", str(proj), "--subject", "ZeroMassDivision")
            p_s0_bad = root / "s0_bad.json"
            p_s0_bad.write_text(json.dumps({
                "domain_guards": [{"expression": "0 < 0", "status": "violated", "witnesses": tex_wit}],
                "witnesses": tex_wit,
            }), encoding="utf-8")
            # 1a) advance --run-lean 在仅有 source_anchor 且编译器缺失时必须 fail-closed（B11）
            rc_adv_fc, out_adv_fc = _run_gate(
                "advance", "--case", str(c_s0), "--stage", "S0_TYPING", "--payload", str(p_s0_bad),
                "--run-lean", "--lean-cmd", "definitely_missing_lean_xyz",
            )
            self.assertEqual(rc_adv_fc, 2, out_adv_fc)
            self.assertEqual(out_adv_fc["error_code"], "LEAN_GATE_FAILED")

            rc0, out0 = _run_gate("advance", "--case", str(c_s0), "--stage", "S0_TYPING", "--payload", str(p_s0_bad))
            self.assertEqual(rc0, 2, out0)
            self.assertEqual(out0["current_state"], "HALT_ROOT_CONFLICT")
            self.assertEqual(out0["original_claim_status"], "ILL_FORMED_ROOT")
            self.assertEqual(out0["primary_issue_kind"], "ILL_FORMED")

            # 1b) 禁止用 init --force 覆写已锁定的 HALT_ROOT_CONFLICT 审查单（B5），也禁止 report --output-md 覆写母单自身或已有冲突单（G2）
            rc_wipe, out_wipe = _run_gate("init", "--case", str(c_s0), "--project-root", str(proj), "--subject", "WipeAttempt", "--force")
            self.assertEqual(rc_wipe, 2, out_wipe)
            self.assertEqual(out_wipe["error_code"], "IMMUTABLE_HISTORY_LOCKED")

            rc_rep_self, out_rep_self = _run_gate("report", "--case", str(c_s0), "--output-md", str(c_s0))
            self.assertEqual(rc_rep_self, 2, out_rep_self)
            self.assertEqual(out_rep_self["error_code"], "SAME_CASE_PATH")

            # 非 JSON 损坏文件也不能借 init --force 静默清空
            corrupt_case = root / "corrupt_case.json"
            corrupt_case.write_text("# not json\n", encoding="utf-8")
            rc_cor, out_cor = _run_gate("init", "--case", str(corrupt_case), "--project-root", str(proj), "--subject", "CorruptWipe", "--force")
            self.assertEqual(rc_cor, 2, out_cor)
            self.assertEqual(out_cor["error_code"], "IMMUTABLE_HISTORY_LOCKED")

            # 1c) base_sort_predicate 编码下定义域违约记为 UNSAT_ROOT_CONFLICT（B3）
            c_s0_base = root / "s0_base_case.json"
            _run_gate("init", "--case", str(c_s0_base), "--project-root", str(proj), "--subject", "BaseSortPredConflict")
            p_s0_base = root / "s0_base.json"
            p_s0_base.write_text(json.dumps({
                "encoding_regime": "base_sort_predicate",
                "domain_guards": [{"expression": "t != 0", "status": "violated", "witnesses": tex_wit}],
                "witnesses": tex_wit,
            }), encoding="utf-8")
            rc0b, out0b = _run_gate("advance", "--case", str(c_s0_base), "--stage", "S0_TYPING", "--payload", str(p_s0_base))
            self.assertEqual(rc0b, 2, out0b)
            self.assertEqual(out0b["current_state"], "HALT_ROOT_CONFLICT")
            self.assertEqual(out0b["original_claim_status"], "UNSAT_ROOT_CONFLICT")
            self.assertEqual(out0b["primary_issue_kind"], "UNSAT")

            # 2) S1 双极限根冲突 -> UNSAT_ROOT_CONFLICT + 锁死 S4 + resolve 边界守卫
            case_file = root / "saurel_case.json"
            _run_gate("init", "--case", str(case_file), "--project-root", str(proj), "--subject", "SixEqTwoFluid", "--claim", "共点微分六方程守恒物理本体")
            s0_file = root / "s0.json"
            s0_file.write_text(json.dumps({
                "domain_guards": [{"expression": "alpha_k in (0, 1)", "status": "satisfied", "witnesses": tex_wit}],
                "witnesses": tex_wit,
            }), encoding="utf-8")
            rc, _ = _run_gate("advance", "--case", str(case_file), "--stage", "S0_TYPING", "--payload", str(s0_file))
            self.assertEqual(rc, 0)

            s1_file = root / "s1.json"
            s1_file.write_text(json.dumps({
                "measure_checks": {
                    "shrink_limit_vs_positive_measure": {"status": "conflict", "detail": "(mu -> 0) ∧ (mu >= V_min > 0)"},
                    "singular_measure_product": {"status": "conflict", "detail": "p_I * grad(alpha) 无定义"},
                    "nonlinear_average_commutativity": {"status": "pass"},
                },
                "witnesses": [{"type": "lean_theorem", "module": "ConvexDirectSum", "theorem": "double_limit_contradiction"}],
            }), encoding="utf-8")
            rc1, out1 = _run_gate("advance", "--case", str(case_file), "--stage", "S1_ROOT_MEASURE", "--payload", str(s1_file))
            self.assertEqual(rc1, 2, out1)
            self.assertEqual(out1["current_state"], "HALT_ROOT_CONFLICT")
            self.assertEqual(out1["original_claim_status"], "UNSAT_ROOT_CONFLICT")

            # 硬拒跳 S4
            s4_file = root / "s4.json"
            s4_file.write_text(json.dumps({"witnesses": []}), encoding="utf-8")
            rc_lock, lock_out = _run_gate("advance", "--case", str(case_file), "--stage", "S4_LOCAL_SPECTRAL_VV", "--payload", str(s4_file))
            self.assertEqual(rc_lock, 2)
            self.assertEqual(lock_out["error_code"], "ROOT_CONFLICT_LOCKED")

            # resolve 守卫 1：downgrade_claim 拒绝与原宣称完全相同文本
            rc_same, same_out = _run_gate(
                "resolve", "--case", str(case_file), "--action", "downgrade_claim",
                "--new-claim", "  共点微分六方程守恒物理本体 ",
                "--stripped-claims", "物理本体宣称",
                "--rationale", "测试同宣称拦截",
            )
            self.assertEqual(rc_same, 2, same_out)
            self.assertEqual(same_out["error_code"], "IDENTICAL_CLAIM_REJECTED")

            # resolve 守卫 2：downgrade_claim 缺少剥离范围字段应拒绝
            rc_noscope, noscope_out = _run_gate(
                "resolve", "--case", str(case_file), "--action", "downgrade_claim",
                "--new-claim", "仅作局部正则化工程经验拟合格式",
                "--rationale", "测试缺剥离范围拦截",
            )
            self.assertEqual(rc_noscope, 2, noscope_out)
            self.assertEqual(noscope_out["error_code"], "MISSING_DOWNGRADE_SCOPE")

            # 合法 downgrade_claim
            rc_dg, dg_out = _run_gate(
                "resolve", "--case", str(case_file), "--action", "downgrade_claim",
                "--new-claim", "仅作局部正则化工程经验拟合格式",
                "--stripped-claims", "物理本体宣称",
                "--nonconvergence-tag", "Hou-LeFloch",
                "--rationale", "按第一层一.4 剥离不可满足的根层宣称",
            )
            self.assertEqual(rc_dg, 0, dg_out)
            self.assertEqual(dg_out["current_state"], "DOWNGRADED_NEW_CLAIM")
            self.assertEqual(dg_out["original_claim_status"], "UNSAT_PERMANENT_DOWNGRADED")
            saved = json.loads(case_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["immutable_conflict_history"][0]["prev_hash"], "GENESIS")
            self.assertTrue(saved["immutable_conflict_history"][0]["entry_hash"])

            # resolve 守卫 3：rebase_ontology 拒绝 --emit-rebased-case 与 --case 同路径，且不静默覆盖既有单
            case2 = root / "overdet_case.json"
            _run_gate("init", "--case", str(case2), "--project-root", str(proj), "--subject", "OverdetJumpModel")
            _run_gate("advance", "--case", str(case2), "--stage", "S0_TYPING", "--payload", str(s0_file))
            s1_ok = root / "s1_ok.json"
            s1_ok.write_text(json.dumps({
                "measure_checks": {
                    "shrink_limit_vs_positive_measure": {"status": "pass"},
                    "singular_measure_product": {"status": "pass"},
                    "nonlinear_average_commutativity": {"status": "pass"},
                },
                "witnesses": tex_wit,
            }), encoding="utf-8")
            _run_gate("advance", "--case", str(case2), "--stage", "S1_ROOT_MEASURE", "--payload", str(s1_ok))
            s2_bad = root / "s2_bad.json"
            s2_bad.write_text(json.dumps({
                "symmetry_group": "galilean",
                "formulation_type": "nonconservative_product",
                "state_dim": 6,
                "independent_jump_relations": 7,
                "witnesses": tex_wit,
            }), encoding="utf-8")
            rc2, out2 = _run_gate("advance", "--case", str(case2), "--stage", "S2_SYMMETRY_JUMP", "--payload", str(s2_bad))
            self.assertEqual(rc2, 2, out2)

            rc_sp, sp_out = _run_gate(
                "resolve", "--case", str(case2), "--action", "rebase_ontology",
                "--new-ontology", "正测度控制体盒 + Onsager 对偶梯度",
                "--emit-rebased-case", str(case2),
                "--rationale", "测试同路径拒绝",
            )
            self.assertEqual(rc_sp, 2, sp_out)
            self.assertEqual(sp_out["error_code"], "SAME_CASE_PATH")

            rc_ex, ex_out = _run_gate(
                "resolve", "--case", str(case2), "--action", "rebase_ontology",
                "--new-ontology", "正测度控制体盒 + Onsager 对偶梯度",
                "--emit-rebased-case", str(case_file),
                "--rationale", "测试拒绝静默覆盖其他单",
            )
            self.assertEqual(rc_ex, 2, ex_out)
            self.assertEqual(ex_out["error_code"], "REBASED_CASE_EXISTS")

            # resolve 守卫 4：rebase_ontology 禁止 --emit-rebased-case --force 覆写其他含冲突历史的审查单或指向目录（G2/G3）
            rc_ow_lock, ow_lock_out = _run_gate(
                "resolve", "--case", str(case2), "--action", "rebase_ontology",
                "--new-ontology", "正测度控制体盒 + Onsager 对偶梯度",
                "--emit-rebased-case", str(case_file), "--force",
                "--rationale", "测试拒绝强制覆写其他含历史审查单",
            )
            self.assertEqual(rc_ow_lock, 2, ow_lock_out)
            self.assertEqual(ow_lock_out["error_code"], "IMMUTABLE_HISTORY_LOCKED")
            self.assertEqual(json.loads(case2.read_text(encoding="utf-8"))["current_state"], "HALT_ROOT_CONFLICT")

            existing_dir = root / "existing_dir_target"
            existing_dir.mkdir()
            rc_dir_tgt, dir_tgt_out = _run_gate(
                "resolve", "--case", str(case2), "--action", "rebase_ontology",
                "--new-ontology", "正测度控制体盒 + Onsager 对偶梯度",
                "--emit-rebased-case", str(existing_dir), "--force",
                "--rationale", "测试目录目标先于母单落盘被拒",
            )
            self.assertEqual(rc_dir_tgt, 2, dir_tgt_out)
            self.assertEqual(dir_tgt_out["error_code"], "INVALID_TARGET_PATH")
            self.assertEqual(json.loads(case2.read_text(encoding="utf-8"))["current_state"], "HALT_ROOT_CONFLICT")

            # resolve 守卫 5：rebase_ontology 若 --lean-dir 不存在，须在母单写入 REBASED 前失败，保持母单原状态（B6）
            rebased_file = root / "rebased_case.json"
            rc_bad_ld, bad_ld_out = _run_gate(
                "resolve", "--case", str(case2), "--action", "rebase_ontology",
                "--new-ontology", "正测度控制体盒 + Onsager 对偶梯度",
                "--emit-rebased-case", str(rebased_file),
                "--lean-dir", str(root / "nonexistent_lean_dir"),
                "--rationale", "测试子单校验先于母单落盘",
            )
            self.assertEqual(rc_bad_ld, 2, bad_ld_out)
            self.assertEqual(bad_ld_out["error_code"], "LEAN_DIR_MISSING")
            self.assertEqual(json.loads(case2.read_text(encoding="utf-8"))["current_state"], "HALT_ROOT_CONFLICT")

            rc_rb, rb_out = _run_gate(
                "resolve", "--case", str(case2), "--action", "rebase_ontology",
                "--new-ontology", "正测度控制体盒 + Onsager 对偶梯度",
                "--emit-rebased-case", str(rebased_file),
                "--rationale", "放弃非守恒共点微分本体，重构至正测度控制体",
            )
            self.assertEqual(rc_rb, 0, rb_out)
            rebased_doc = json.loads(rebased_file.read_text(encoding="utf-8"))
            self.assertEqual(rebased_doc["next_stage"], "S0_TYPING")
            self.assertTrue(rebased_doc["lean_ast_gate_at_init"]["ast_gate_ok"])

    def test_05_s3_hold_overreach_and_s4_strict_vv_blocks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="axioms-s3s4-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "proj"
            proj.mkdir()
            (proj / "scheme.py").write_text("# scheme\n", encoding="utf-8")
            (proj / "probe.log").write_text("ok=1\nexit_code=0\n", encoding="utf-8")
            wit = [{"type": "source_anchor", "file": "scheme.py", "line_start": 1, "line_end": 1, "excerpt": "# scheme"}]
            probe_w = [{
                "type": "probe_exec", "command": "run", "execute": False,
                "receipt_file": "probe.log", "recorded_exit_code": 0, "expected_output_substring": "ok=1",
            }]

            def prepare_to_s3(name: str) -> Path:
                cfile = root / f"{name}.json"
                _run_gate("init", "--case", str(cfile), "--project-root", str(proj), "--subject", name)
                for st, body in (
                    ("S0_TYPING", {"domain_guards": [{"expression": "rho > 0", "status": "satisfied", "witnesses": wit}], "witnesses": wit}),
                    ("S1_ROOT_MEASURE", {
                        "measure_checks": {
                            "shrink_limit_vs_positive_measure": {"status": "pass"},
                            "singular_measure_product": {"status": "pass"},
                            "nonlinear_average_commutativity": {"status": "pass"},
                        },
                        "witnesses": wit,
                    }),
                    ("S2_SYMMETRY_JUMP", {
                        "symmetry_group": "galilean",
                        "formulation_type": "conservative_divergence",
                        "witnesses": wit,
                    }),
                ):
                    pf = root / f"{name}_{st}.json"
                    pf.write_text(json.dumps(body), encoding="utf-8")
                    rc, out = _run_gate("advance", "--case", str(cfile), "--stage", st, "--payload", str(pf))
                    self.assertEqual(rc, 0, out)
                return cfile

            # 1) S3 非吸收 HOLD_S3：含越权或 unclassified 时严禁走 declare_undetermined 伪装语义未定（B4）
            c_hold = prepare_to_s3("hold_s3")
            s3_hold = root / "s3_hold.json"
            s3_hold.write_text(json.dumps({
                "closure_assumptions_explicit": False,
                "declared_constraints": [
                    {"name": "proto_i_indep_overreach", "prototype": "I", "objective_role": "independent", "standard_claims": ["A->T"], "witnesses": wit},
                    {"name": "legacy_ambiguous", "prototype": "II", "objective_role": "non_decisive", "witnesses": wit},
                ],
                "witnesses": wit,
            }), encoding="utf-8")
            rc_h, out_h = _run_gate("advance", "--case", str(c_hold), "--stage", "S3_CLOSURE_CONSTRAINT", "--payload", str(s3_hold))
            self.assertEqual(rc_h, 2, out_h)
            self.assertEqual(out_h["current_state"], "HOLD_S3")
            self.assertEqual(out_h["next_stage"], "S3_CLOSURE_CONSTRAINT")
            self.assertEqual(out_h["first_conflict_stage"], "S3_CLOSURE_CONSTRAINT")
            self.assertEqual(out_h["primary_issue_kind"], "OVERREACH")
            # 含越权与 unclassified 时拒绝 declare_undetermined
            rc_du_bad, out_du_bad = _run_gate(
                "resolve", "--case", str(c_hold), "--action", "declare_undetermined",
                "--rationale", "测试越权/unclassified不得伪装语义未定",
            )
            self.assertEqual(rc_du_bad, 2, out_du_bad)
            self.assertEqual(out_du_bad["error_code"], "INVALID_UNDETERMINED_EXIT")

            # 修正越权与未判定后仅保留纯欠封闭 MISSING_CLOSURE_BASIS，此时合法开放 declare_undetermined
            s3_pure_undet = root / "s3_pure_undet.json"
            s3_pure_undet.write_text(json.dumps({
                "closure_assumptions_explicit": False,
                "declared_constraints": [
                    {"name": "valid_domain", "prototype": "I", "objective_role": "necessary_only", "witnesses": wit},
                ],
                "witnesses": wit,
            }), encoding="utf-8")
            rc_h2, out_h2 = _run_gate("advance", "--case", str(c_hold), "--stage", "S3_CLOSURE_CONSTRAINT", "--payload", str(s3_pure_undet))
            self.assertEqual(rc_h2, 2, out_h2)
            self.assertEqual(out_h2["current_state"], "HOLD_S3")
            self.assertEqual(out_h2["primary_issue_kind"], "UNDETERMINED")
            rc_du, out_du = _run_gate(
                "resolve", "--case", str(c_hold), "--action", "declare_undetermined",
                "--rationale", "当前前提集缺少唯一闭合准则，按第一层一.4 如实声明语义未定",
            )
            self.assertEqual(rc_du, 0, out_du)
            self.assertEqual(out_du["current_state"], "DECLARED_UNDETERMINED")

            # 2) S3 吸收态 HALT_CLOSURE_CONFLICT：IV.2 缺 norm_equiv_witnesses + IV.1/II 互斥 + 偶宇称耗散 + veto
            c_s3 = prepare_to_s3("bad_s3")
            s3_bad = root / "s3_bad.json"
            s3_bad.write_text(json.dumps({
                "closure_assumptions_explicit": True,
                "closure_witnesses": wit,
                "declared_constraints": [
                    {"name": "es_dg_scalar_entropy", "prototype": "IV.2", "objective_role": "independent", "claimed_as_axiom_standard": True, "witnesses": wit},
                    {"name": "curved_manifold_inv", "prototype": "IV.1", "objective_role": "independent", "witnesses": wit},
                    {"name": "mean_conservation", "prototype": "II", "objective_role": "sufficient_only", "witnesses": wit},
                ],
                "mechanisms": [
                    {"name": "euler_advection", "parity": "even_reversible", "attached_dissipation_inequality": True},
                ],
                "witnesses": wit,
            }), encoding="utf-8")
            rc, out = _run_gate("advance", "--case", str(c_s3), "--stage", "S3_CLOSURE_CONSTRAINT", "--payload", str(s3_bad))
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["current_state"], "HALT_CLOSURE_CONFLICT")
            self.assertEqual(out["first_conflict_stage"], "S3_CLOSURE_CONSTRAINT")
            codes = {it["code"] for it in out["structured_issues"]}
            self.assertIn("CONSTRAINT_STANDARD_OVERREACH", codes)
            self.assertIn("PROTO_IV2_MISSING_NORM_WITNESS", codes)
            self.assertIn("PROTO_IV1_II_CONFLICT", codes)
            self.assertIn("EVEN_PARITY_DISSIPATION", codes)

            # 2b) 单开原型 IV.1（不声明原型 II）亦须独立拦截，防撤除原型 II 的消症伪相容
            c_iv1_solo = prepare_to_s3("bad_s3_iv1_solo")
            s3_iv1_solo = root / "s3_iv1_solo.json"
            s3_iv1_solo.write_text(json.dumps({
                "closure_assumptions_explicit": True,
                "closure_witnesses": wit,
                "declared_constraints": [
                    {"name": "curved_manifold_solo", "prototype": "IV.1", "objective_role": "independent", "witnesses": wit},
                ],
                "witnesses": wit,
            }), encoding="utf-8")
            rc_iv1, out_iv1 = _run_gate("advance", "--case", str(c_iv1_solo), "--stage", "S3_CLOSURE_CONSTRAINT", "--payload", str(s3_iv1_solo))
            self.assertEqual(rc_iv1, 2, out_iv1)
            self.assertEqual(out_iv1["current_state"], "HALT_CLOSURE_CONFLICT")
            self.assertIn("PROTO_IV1_CURVED_CLOSURE_CONFLICT", {it["code"] for it in out_iv1["structured_issues"]})

            # 3) S4 拦截：明确 failed 不得放行 + 空 review_exit 被拒 + 数据泄漏 + 目标非外生未登记禁区 + 降阶缺基线与成本
            c_s4 = prepare_to_s3("bad_s4")
            s3_ok = root / "s3_ok.json"
            s3_ok.write_text(json.dumps({
                "closure_assumptions_explicit": True,
                "closure_witnesses": wit,
                "declared_constraints": [{"name": "domain", "prototype": "I", "objective_role": "necessary_only", "witnesses": wit}],
                "witnesses": wit,
            }), encoding="utf-8")
            rc_ok, _ = _run_gate("advance", "--case", str(c_s4), "--stage", "S3_CLOSURE_CONSTRAINT", "--payload", str(s3_ok))
            self.assertEqual(rc_ok, 0)

            # 3a) review_exit 为空直接报 SCHEMA_TYPE_ERROR（不再漏放行）
            s4_empty_exit = root / "s4_empty_exit.json"
            s4_empty_exit.write_text(json.dumps({
                "verification": {"status": "failed"},
                "validation": {"status": "failed"},
                "residual_error_separated": True,
                "residual_error_witnesses": wit,
                "review_exit": {},
                "witnesses": wit,
            }), encoding="utf-8")
            rc_ee, out_ee = _run_gate("advance", "--case", str(c_s4), "--stage", "S4_LOCAL_SPECTRAL_VV", "--payload", str(s4_empty_exit))
            self.assertEqual(rc_ee, 2, out_ee)
            self.assertEqual(out_ee["error_code"], "SCHEMA_TYPE_ERROR")

            # 3b) 结构合规但 V&V failed + 泄漏 + 同源无执行器 + 非外生未标禁区 -> HALT_VV_CONFLICT
            s4_bad = root / "s4_bad.json"
            s4_bad.write_text(json.dumps({
                "verification": {"status": "failed"},
                "validation": {"status": "failed"},
                "calibration_datasets": ["exp_A", "exp_B"],
                "validation_datasets": ["exp_B", "exp_C"],
                "residual_error_separated": False,
                "review_exit": {
                    "exit_kind": "nonperceivable_constraint",
                    "target_exogenous": False,
                    "target_exogeneity_basis": "目标受代理生态策略反身影响，属内生目标",
                    "shares_representation_with_generator": True,
                    "has_external_executor": False,
                },
                "is_surrogate_or_rom": True,
                "strong_classical_baseline": "",
                "offline_cost_included": False,
                "witnesses": probe_w,
            }), encoding="utf-8")
            rc4, out4 = _run_gate("advance", "--case", str(c_s4), "--stage", "S4_LOCAL_SPECTRAL_VV", "--payload", str(s4_bad))
            self.assertEqual(rc4, 2, out4)
            self.assertEqual(out4["current_state"], "HALT_VV_CONFLICT")
            codes4 = {it["code"] for it in out4["structured_issues"]}
            for expected in (
                "VERIFICATION_FAILED",
                "VALIDATION_FAILED",
                "DATASET_LEAKAGE",
                "RESIDUAL_ERROR_UNSEPARATED",
                "ENDOGENOUS_TARGET_UNMARKED",
                "REFLEXIVE_SAME_REPR_LOOP",
                "MISSING_STRONG_BASELINE",
                "MISSING_OFFLINE_COST",
            ):
                self.assertIn(expected, codes4, out4["structured_issues"])

            # 3c) 共享表征但裸布尔声称 has_external_executor=True 且缺 executor_witnesses，及裸枚举缺 exit_basis/exit_witnesses 均被拒（B8）
            c_s4_bare = prepare_to_s3("bad_s4_bare_exec")
            rc_ok2, _ = _run_gate("advance", "--case", str(c_s4_bare), "--stage", "S3_CLOSURE_CONSTRAINT", "--payload", str(s3_ok))
            self.assertEqual(rc_ok2, 0)
            s4_bare = root / "s4_bare_exec.json"
            s4_bare.write_text(json.dumps({
                "verification": {"status": "passed"},
                "validation": {"status": "passed"},
                "residual_error_separated": True,
                "residual_error_witnesses": probe_w,
                "review_exit": {
                    "exit_kind": "nonperceivable_constraint",
                    "target_exogenous": True,
                    "target_exogeneity_basis": "外生物理守恒基准",
                    "shares_representation_with_generator": True,
                    "has_external_executor": True,
                },
                "witnesses": probe_w,
            }), encoding="utf-8")
            rc_be, out_be = _run_gate("advance", "--case", str(c_s4_bare), "--stage", "S4_LOCAL_SPECTRAL_VV", "--payload", str(s4_bare))
            self.assertEqual(rc_be, 2, out_be)
            self.assertEqual(out_be["current_state"], "HALT_VV_CONFLICT")
            be_codes = {it["code"] for it in out_be["structured_issues"]}
            self.assertIn("UNANCHORED_EXTERNAL_EXIT", be_codes)
            self.assertIn("MISSING_EXIT_BASIS", be_codes)
            self.assertIn("MISSING_EXIT_WITNESS", be_codes)

            # 3d) 未经 --run-lean 内核编译的 ast_only lean_theorem 不得充当 S4 外部执行器锚点（B7）
            c_s4_ast = prepare_to_s3("bad_s4_ast_only")
            rc_ok3, _ = _run_gate("advance", "--case", str(c_s4_ast), "--stage", "S3_CLOSURE_CONSTRAINT", "--payload", str(s3_ok))
            self.assertEqual(rc_ok3, 0)
            s4_ast = root / "s4_ast_only.json"
            s4_ast.write_text(json.dumps({
                "verification": {"status": "passed"},
                "validation": {"status": "passed"},
                "residual_error_separated": True,
                "residual_error_witnesses": probe_w,
                "review_exit": {
                    "exit_kind": "evolution_decoupled",
                    "exit_basis_ref": "B_dec: 演化与代理脱钩说明",
                    "exit_witnesses": probe_w,
                    "target_exogenous": True,
                    "target_exogeneity_basis": "外生求解器验证基准",
                    "shares_representation_with_generator": False,
                    "has_external_executor": True,
                    "executor_witnesses": [{"type": "lean_theorem", "module": "LogicCore", "theorem": "domainGuarded_nonempty"}],
                },
                "witnesses": probe_w,
            }), encoding="utf-8")
            rc_ast, out_ast = _run_gate("advance", "--case", str(c_s4_ast), "--stage", "S4_LOCAL_SPECTRAL_VV", "--payload", str(s4_ast))
            self.assertEqual(rc_ast, 2, out_ast)
            self.assertEqual(out_ast["current_state"], "HALT_VV_CONFLICT")
            self.assertIn("EXECUTOR_WITNESS_TYPE_WEAK", {it["code"] for it in out_ast["structured_issues"]})

            # 3e) 离线 probe_exec 回执内显式记录 exit_code=1 与声称 recorded_exit_code=0 冲突时直接拒绝（G5）
            (proj / "fail_probe.log").write_text("ok=1\nexit_code=1\n", encoding="utf-8")
            c_s4_failrc = prepare_to_s3("bad_s4_fail_receipt")
            rc_ok4, _ = _run_gate("advance", "--case", str(c_s4_failrc), "--stage", "S3_CLOSURE_CONSTRAINT", "--payload", str(s3_ok))
            self.assertEqual(rc_ok4, 0)
            s4_failrc = root / "s4_failrc.json"
            s4_failrc.write_text(json.dumps({
                "verification": {"status": "passed"},
                "validation": {"status": "passed"},
                "residual_error_separated": True,
                "residual_error_witnesses": probe_w,
                "review_exit": {
                    "exit_kind": "evolution_decoupled",
                    "exit_basis_ref": "B_dec",
                    "exit_witnesses": probe_w,
                    "target_exogenous": True,
                    "target_exogeneity_basis": "外生测试退出码不依赖主体表征",
                    "shares_representation_with_generator": False,
                    "has_external_executor": True,
                    "executor_witnesses": [{
                        "type": "probe_exec", "command": "run", "execute": False,
                        "receipt_file": "fail_probe.log", "recorded_exit_code": 0, "expected_output_substring": "ok=1",
                    }],
                },
                "witnesses": probe_w,
            }), encoding="utf-8")
            rc_frc, out_frc = _run_gate("advance", "--case", str(c_s4_failrc), "--stage", "S4_LOCAL_SPECTRAL_VV", "--payload", str(s4_failrc))
            self.assertEqual(rc_frc, 2, out_frc)
            self.assertEqual(out_frc["error_code"], "PROBE_EXIT_MISMATCH")

    def test_06_installer_syncs_cae_axioms_across_all_four_clients_with_zero_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="axioms-install-") as tmp:
            base = Path(tmp).resolve()
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("USER_GUARD_", "CAE_", "CODEX_", "CLAUDE", "DSH_"))}
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            cmd = [
                sys.executable, "-B", "-X", "utf8", str(REPO_ROOT / "install.py"),
                "--app", "all",
                "--target-dir", str(base / "agy" / "plugins" / "user-guard"),
                "--codex-home", str(base / "codex"),
                "--claude-home", str(base / "claude"),
                "--dsh-home", str(base / "dsh"),
                "--no-zotero", "--skip-test", "--yes",
            ]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            for skill_root in (
                base / "agy" / "plugins" / "user-guard" / "skills" / "cae-axioms",
                base / "codex" / "skills" / "cae-axioms",
                base / "claude" / "skills" / "cae-axioms",
                base / "dsh" / "skills" / "cae-axioms",
            ):
                self.assertTrue((skill_root / "SKILL.md").is_file(), skill_root)
                self.assertTrue((skill_root / "scripts" / "axiom_gate.py").is_file(), skill_root)
                for mod in ("__init__.py", "contract.py", "lean_gate.py", "evidence.py", "rules.py", "case.py", "replay.py", "KernelAudit.lean"):
                    self.assertTrue((skill_root / "scripts" / "_axiom_lib" / mod).is_file(), skill_root / "scripts" / "_axiom_lib" / mod)
                self.assertTrue((skill_root / "lean" / "GalileanBox.lean").is_file(), skill_root)
                self.assertTrue((skill_root / "references" / "layer2m-rational-mechanics.md").is_file(), skill_root)
                # 从四端各自安装目录真启动拆包后的薄入口，确认 _axiom_lib 与内置 lean/ 相对定位正常
                probe_proc = subprocess.run(
                    [sys.executable, "-B", "-X", "utf8", str(skill_root / "scripts" / "axiom_gate.py"), "check-lean", "--profile", "core"],
                    cwd=str(base),
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
                self.assertEqual(probe_proc.returncode, 0, probe_proc.stdout + probe_proc.stderr)
                probe_rep = json.loads(probe_proc.stdout)
                self.assertTrue(probe_rep["ok"], probe_rep)
                self.assertEqual(probe_rep["checked_files"], ["LogicCore.lean"])

            audit = subprocess.run(
                [
                    sys.executable, "-B", "-X", "utf8", str(REPO_ROOT / "install.py"),
                    "--audit-upgrade",
                    "--target-dir", str(base / "agy" / "plugins" / "user-guard"),
                    "--codex-home", str(base / "codex"),
                    "--claude-home", str(base / "claude"),
                    "--dsh-home", str(base / "dsh"),
                ],
                cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
            report = json.loads(audit.stdout)
            for client in ("antigravity", "codex", "claude", "dsh"):
                self.assertEqual(report[client]["drift_files"], [], report[client])

    def test_07_real_lean_kernel_and_axioms_when_installed(self) -> None:
        elan_bin = Path.home() / ".elan" / "bin" / ("lean.exe" if os.name == "nt" else "lean")
        has_native_lean = bool(shutil.which("lean")) or elan_bin.is_file()
        if not has_native_lean:
            self.skipTest("本机未安装原生 Lean 4，跳过真实内核编译与公理白名单实测")

        rc, rep = _run_gate("check-lean", "--run-lean")
        self.assertEqual(rc, 0, rep)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["lean_compiler"]["status"], "passed")
        self.assertIn("Lean (version", str(rep["lean_compiler"].get("version") or ""))
        for fentry in rep["lean_compiler"]["files"]:
            self.assertTrue(fentry["ok"], fentry)
            self.assertTrue(fentry["axioms_ok"], fentry)
            self.assertEqual(fentry["disallowed_axioms"], [], fentry)
            self.assertEqual(fentry["checked_axioms_count"], fentry["expected_axioms_count"], fentry)
            self.assertGreater(fentry["checked_axioms_count"], 0, fentry)

        # 验证 advance --run-lean 将 lean_theorem 见证提升为 kernel_compiled 且 verified=True
        with tempfile.TemporaryDirectory(prefix="axioms-real-lean-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "proj"
            proj.mkdir()
            (proj / "m.py").write_text("M_MIN = 1e-12\n", encoding="utf-8")
            case_file = root / "case.json"
            _run_gate("init", "--case", str(case_file), "--project-root", str(proj), "--subject", "RealLeanWitness")
            s0 = root / "s0.json"
            s0.write_text(json.dumps({
                "domain_guards": [{
                    "expression": "M > 0",
                    "status": "satisfied",
                    "witnesses": [{"type": "lean_theorem", "module": "LogicCore", "theorem": "same_repr_checker_cannot_separate_mixed_fiber"}],
                }],
                "witnesses": [{"type": "lean_theorem", "module": "LogicCore", "theorem": "domainGuarded_nonempty"}],
            }), encoding="utf-8")
            rc_adv, out_adv = _run_gate("advance", "--case", str(case_file), "--stage", "S0_TYPING", "--payload", str(s0), "--run-lean")
            self.assertEqual(rc_adv, 0, out_adv)
            saved = json.loads(case_file.read_text(encoding="utf-8"))
            wits = saved["stage_records"]["S0_TYPING"]["witnesses"]
            self.assertEqual(len(wits), 2)
            for w in wits:
                self.assertEqual(w["verification_level"], "kernel_compiled")
                self.assertTrue(w["verified"])
            rc_rep, out_rep = _run_gate("report", "--case", str(case_file), "--run-lean")
            self.assertEqual(rc_rep, 0, out_rep)
            self.assertTrue(out_rep["ok"])
            self.assertEqual(out_rep["lean_compiler_status"], "passed")

    def test_08_core_profile_universal_axiom_machine_for_non_cae_projects(self) -> None:
        # 1) check-lean --profile core 仅核验 LogicCore.lean（40 条定理，零 CFD 耦合）
        rc_cl, rep_cl = _run_gate("check-lean", "--profile", "core")
        self.assertEqual(rc_cl, 0, rep_cl)
        self.assertEqual(rep_cl["checked_files"], ["LogicCore.lean"])
        self.assertEqual(len(rep_cl["public_theorems"]["LogicCore"]), 40)

        with tempfile.TemporaryDirectory(prefix="axioms-core-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "config_sys_proj"
            proj.mkdir()
            (proj / "guard.py").write_text(
                "def check_path(p):\n    return not p.is_symlink()\n",
                encoding="utf-8",
            )
            (proj / "ci_probe.log").write_text("all_boundary_tests=passed\nexit_code=0\n", encoding="utf-8")
            src_wit = [{"type": "source_anchor", "file": "guard.py", "line_start": 1, "line_end": 2, "excerpt": "is_symlink"}]
            probe_wit = [{
                "type": "probe_exec", "command": "pytest tests/", "execute": False,
                "receipt_file": "ci_probe.log", "recorded_exit_code": 0, "expected_output_substring": "all_boundary_tests=passed",
            }]

            def submit(cfile: Path, stage: str, body: dict) -> tuple[int, dict]:
                pf = root / f"{cfile.stem}_{stage}.json"
                pf.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
                return _run_gate("advance", "--case", str(cfile), "--stage", stage, "--payload", str(pf))

            # 2) 全链路正常通过：系统配置/代码开发项目（零物理/CAE 字段）L0 -> L1 -> L2 -> L3 -> PASSED_ALL_GATES
            c_ok = root / "core_pass.json"
            rc_i, out_i = _run_gate(
                "init", "--profile", "core", "--case", str(c_ok), "--project-root", str(proj),
                "--subject", "PathBoundaryGuard", "--claim", "写边界守卫拒绝全部符号链接与越界写入",
            )
            self.assertEqual(rc_i, 0, out_i)
            self.assertEqual(out_i["profile"], "core")
            self.assertEqual(out_i["next_stage"], "L0_WELLFORMED")

            # 跨档关口必须拒绝（STAGE_PROFILE_MISMATCH）
            rc_mm, out_mm = submit(c_ok, "S0_TYPING", {"witnesses": src_wit})
            self.assertEqual(rc_mm, 2, out_mm)
            self.assertEqual(out_mm["error_code"], "STAGE_PROFILE_MISMATCH")

            rc0, out0 = submit(c_ok, "L0_WELLFORMED", {
                "encoding_regime": "refined_subtype",
                "carrier_vs_proposition_checked": True,
                "wellformed_guards": [
                    {"expression": "target_path is non-symlink Path", "status": "satisfied", "witnesses": src_wit},
                ],
                "definitions": [
                    {"name": "SafeWriteTarget", "kind": "predicate_explicit", "novel_and_noncircular": True, "witnesses": src_wit},
                ],
                "witnesses": src_wit,
            })
            self.assertEqual(rc0, 0, out0)
            self.assertEqual(out0["next_stage"], "L1_ROOT_CONSISTENCY")

            rc1, out1 = submit(c_ok, "L1_ROOT_CONSISTENCY", {
                "root_premises_satisfiable": True,
                "alternative_branches_isolated": True,
                "symptom_erasure_detected": False,
                "modules": [
                    {"name": "symlink_rejector", "status": "consistent", "witnesses": src_wit},
                    {"name": "atomic_writer", "status": "consistent", "depends_on": ["symlink_rejector"], "witnesses": src_wit},
                ],
                "witnesses": [{"type": "lean_theorem", "module": "LogicCore", "theorem": "finite_module_repair_must_hit_core"}],
            })
            self.assertEqual(rc1, 0, out1)
            self.assertEqual(out1["next_stage"], "L2_CONSTRAINT_ROLE")

            rc2, out2 = submit(c_ok, "L2_CONSTRAINT_ROLE", {
                "decision_basis_explicit": True,
                "decision_basis_witnesses": src_wit,
                "declared_constraints": [
                    {"name": "unit_tests_green", "objective_role": "necessary_only", "standard_claims": ["not_A->not_T"], "witnesses": probe_wit},
                    {"name": "path_check_passes", "objective_role": "sufficient_only", "standard_claims": ["A->T"], "witnesses": src_wit},
                ],
                "constraint_pairs": [
                    {"constraint_a": "unit_tests_green", "constraint_b": "path_check_passes", "relation": "juxtaposed", "claimed_hierarchy": False},
                ],
                "comparisons": [
                    {"claim": "atomic_replace优于原地覆写", "objects_fixed": True, "relation_fixed": True, "constraints_fixed": True},
                ],
                "pseudo_moderation_detected": False,
                "witnesses": [{"type": "lean_theorem", "module": "LogicCore", "theorem": "constraint_seven_way_exhaustive"}],
            })
            self.assertEqual(rc2, 0, out2)
            self.assertEqual(out2["next_stage"], "L3_EMPIRICAL_EXIT")

            rc3, out3 = submit(c_ok, "L3_EMPIRICAL_EXIT", {
                "internal_verification": {"status": "passed"},
                "external_validation": {"status": "passed"},
                "construction_inputs": ["dev_fixture_01"],
                "evaluation_inputs": ["independent_host_test_02"],
                "positive_inference_audit": {
                    "premise_observation_satisfiable": True,
                    "observation_non_idle": True,
                    "dependency_chain_free_of_H": True,
                },
                "review_exit": {
                    "exit_kind": "sufficient_certificate",
                    "bridge_premise_ref": "B_sys: guard.py 实测与符号链接语义桥接前提",
                    "bridge_witnesses": src_wit,
                    "actual_state_in_reachable_domain": True,
                    "actual_state_accepted": True,
                    "acceptance_witnesses": probe_wit,
                    "target_exogenous": True,
                    "target_exogeneity_basis": "文件系统路径与符号链接状态属外生客观状态，不依赖代理信念",
                    "shares_representation_with_generator": False,
                    "has_external_executor": True,
                    "executor_witnesses": probe_wit,
                },
                "witnesses": probe_wit,
            })
            self.assertEqual(rc3, 0, out3)
            self.assertEqual(out3["current_state"], "STRUCTURE_PASSED")
            self.assertEqual(out3["original_claim_status"], "STRUCTURE_ONLY_UNVERIFIED")

            md_out = root / "core_report.md"
            rc_r, out_r = _run_gate("report", "--case", str(c_ok), "--output-md", str(md_out))
            self.assertEqual(rc_r, 0, out_r)
            self.assertEqual(out_r["profile"], "core")
            md_txt = md_out.read_text(encoding="utf-8")
            self.assertIn("`core`", md_txt)
            self.assertIn("L0_WELLFORMED", md_txt)
            self.assertIn("L3_EMPIRICAL_EXIT", md_txt)
            self.assertNotIn("PASSED_ALL_GATES", md_txt)

            # 3) L0 拦截非保守定义与缺存在唯一性
            c_l0 = root / "core_l0_bad.json"
            _run_gate("init", "--profile", "core", "--case", str(c_l0), "--project-root", str(proj), "--subject", "BadDef")
            rc_l0, out_l0 = submit(c_l0, "L0_WELLFORMED", {
                "carrier_vs_proposition_checked": False,
                "wellformed_guards": [{"expression": "p != None", "status": "satisfied", "witnesses": src_wit}],
                "definitions": [
                    {"name": "CircularPred", "kind": "predicate_explicit", "novel_and_noncircular": False, "witnesses": src_wit},
                    {"name": "UniqueConfig", "kind": "definite_description", "novel_and_noncircular": True, "existence_uniqueness_verified": False, "witnesses": src_wit},
                ],
                "witnesses": src_wit,
            })
            self.assertEqual(rc_l0, 2, out_l0)
            self.assertEqual(out_l0["current_state"], "HALT_ROOT_CONFLICT")
            self.assertEqual(out_l0["original_claim_status"], "ILL_FORMED_ROOT")
            l0_codes = {it["code"] for it in out_l0["structured_issues"]}
            self.assertIn("CARRIER_PROPOSITION_CONFLATION", l0_codes)
            self.assertIn("NONCONSERVATIVE_DEFINITION", l0_codes)
            self.assertIn("MISSING_EXISTENCE_UNIQUENESS", l0_codes)

            # 4) L1 根层冲突锁定 + rebase_ontology 继承 profile="core"
            c_l1 = root / "core_l1_bad.json"
            _run_gate("init", "--profile", "core", "--case", str(c_l1), "--project-root", str(proj), "--subject", "BadDAG")
            self.assertEqual(submit(c_l1, "L0_WELLFORMED", {
                "wellformed_guards": [{"expression": "ok", "status": "satisfied", "witnesses": src_wit}],
                "witnesses": src_wit,
            })[0], 0)
            rc_l1, out_l1 = submit(c_l1, "L1_ROOT_CONSISTENCY", {
                "root_premises_satisfiable": False,
                "symptom_erasure_detected": True,
                "modules": [{"name": "conflicting_policy", "status": "unsat_core", "witnesses": src_wit}],
                "witnesses": src_wit,
            })
            self.assertEqual(rc_l1, 2, out_l1)
            self.assertEqual(out_l1["current_state"], "HALT_ROOT_CONFLICT")
            self.assertEqual(out_l1["original_claim_status"], "UNSAT_ROOT_CONFLICT")
            c_l1_rebased = root / "core_l1_rebased.json"
            rc_rb, out_rb = _run_gate(
                "resolve", "--case", str(c_l1), "--action", "rebase_ontology",
                "--new-ontology", "单源不可变配置契约",
                "--emit-rebased-case", str(c_l1_rebased),
                "--rationale", "消除根层配置冲突后以新本体重建",
            )
            self.assertEqual(rc_rb, 0, out_rb)
            rb_doc = json.loads(c_l1_rebased.read_text(encoding="utf-8"))
            self.assertEqual(rb_doc["profile"], "core")
            self.assertEqual(rb_doc["next_stage"], "L0_WELLFORMED")

            # 5) L2 拦截并列虚构高低、比较缺要素、伪折中 -> HOLD_L2 拒 declare_undetermined；纯欠封闭才开放
            c_l2 = root / "core_l2_hold.json"
            _run_gate("init", "--profile", "core", "--case", str(c_l2), "--project-root", str(proj), "--subject", "L2Checks")
            self.assertEqual(submit(c_l2, "L0_WELLFORMED", {
                "wellformed_guards": [{"expression": "ok", "status": "satisfied", "witnesses": src_wit}],
                "witnesses": src_wit,
            })[0], 0)
            self.assertEqual(submit(c_l2, "L1_ROOT_CONSISTENCY", {
                "root_premises_satisfiable": True,
                "modules": [{"name": "m1", "status": "consistent", "witnesses": src_wit}],
                "witnesses": src_wit,
            })[0], 0)
            rc_l2, out_l2 = submit(c_l2, "L2_CONSTRAINT_ROLE", {
                "decision_basis_explicit": False,
                "declared_constraints": [{"name": "style_lint", "objective_role": "independent", "standard_claims": ["A->T"], "witnesses": src_wit}],
                "constraint_pairs": [{"constraint_a": "style_lint", "constraint_b": "speed", "relation": "juxtaposed", "claimed_hierarchy": True}],
                "comparisons": [{"claim": "方案A更好", "objects_fixed": True, "relation_fixed": False, "constraints_fixed": False}],
                "pseudo_moderation_detected": True,
                "witnesses": src_wit,
            })
            self.assertEqual(rc_l2, 2, out_l2)
            self.assertEqual(out_l2["current_state"], "HOLD_L2")
            l2_codes = {it["code"] for it in out_l2["structured_issues"]}
            for exp in ("CONSTRAINT_STANDARD_OVERREACH", "JUXTAPOSED_FAKE_HIERARCHY", "INCOMPLETE_COMPARISON_SPEC", "PSEUDO_MODERATION_VIOLATION"):
                self.assertIn(exp, l2_codes)
            rc_du_no, out_du_no = _run_gate("resolve", "--case", str(c_l2), "--action", "declare_undetermined", "--rationale", "越权禁止走未定")
            self.assertEqual(rc_du_no, 2, out_du_no)
            self.assertEqual(out_du_no["error_code"], "INVALID_UNDETERMINED_EXIT")

            rc_l2p, out_l2p = submit(c_l2, "L2_CONSTRAINT_ROLE", {
                "decision_basis_explicit": False,
                "declared_constraints": [{"name": "style_lint", "objective_role": "independent", "witnesses": src_wit}],
                "witnesses": src_wit,
            })
            self.assertEqual(rc_l2p, 2, out_l2p)
            self.assertEqual(out_l2p["current_state"], "HOLD_L2")
            rc_du_ok, out_du_ok = _run_gate("resolve", "--case", str(c_l2), "--action", "declare_undetermined", "--rationale", "纯欠封闭诚实声明语义未定")
            self.assertEqual(rc_du_ok, 0, out_du_ok)
            self.assertEqual(out_du_ok["current_state"], "DECLARED_UNDETERMINED")

            # 6) L3 拦截数据泄漏、空真预测、闲置观测、循环论证
            c_l3 = root / "core_l3_bad.json"
            _run_gate("init", "--profile", "core", "--case", str(c_l3), "--project-root", str(proj), "--subject", "L3Checks")
            for st, body in (
                ("L0_WELLFORMED", {"wellformed_guards": [{"expression": "ok", "status": "satisfied", "witnesses": src_wit}], "witnesses": src_wit}),
                ("L1_ROOT_CONSISTENCY", {"root_premises_satisfiable": True, "modules": [{"name": "m1", "status": "consistent", "witnesses": src_wit}], "witnesses": src_wit}),
                ("L2_CONSTRAINT_ROLE", {
                    "decision_basis_explicit": True, "decision_basis_witnesses": src_wit,
                    "declared_constraints": [{"name": "c1", "objective_role": "necessary_only", "witnesses": src_wit}],
                    "witnesses": src_wit,
                }),
            ):
                self.assertEqual(submit(c_l3, st, body)[0], 0)
            rc_l3, out_l3 = submit(c_l3, "L3_EMPIRICAL_EXIT", {
                "internal_verification": {"status": "passed"},
                "external_validation": {"status": "passed"},
                "construction_inputs": ["case_X"],
                "evaluation_inputs": ["case_X"],
                "positive_inference_audit": {
                    "premise_observation_satisfiable": False,
                    "observation_non_idle": False,
                    "dependency_chain_free_of_H": False,
                },
                "review_exit": {
                    "exit_kind": "nonperceivable_constraint",
                    "exit_basis_ref": "B",
                    "exit_witnesses": probe_wit,
                    "target_exogenous": True,
                    "target_exogeneity_basis": "外生测试断言",
                    "shares_representation_with_generator": True,
                    "has_external_executor": False,
                },
                "witnesses": probe_wit,
            })
            self.assertEqual(rc_l3, 2, out_l3)
            self.assertEqual(out_l3["current_state"], "HALT_VV_CONFLICT")
            l3_codes = {it["code"] for it in out_l3["structured_issues"]}
            for exp in ("DATASET_LEAKAGE", "VACUOUS_PREDICTION_AGREEMENT", "IDLE_OBSERVATION_CLAIM", "CIRCULAR_CONFIRMATION", "REFLEXIVE_SAME_REPR_LOOP"):
                self.assertIn(exp, l3_codes)

    def test_07b_real_lean_kernel_blocks_six_malicious_bypass_payloads(self) -> None:
        """L3 层：真实 Lean 4 内核对抗回归——6+1 组恶意绕过样本（直接经 KernelAudit.lean 实编拦截）。"""
        elan_bin = Path.home() / ".elan" / "bin" / ("lean.exe" if os.name == "nt" else "lean")
        has_native_lean = bool(shutil.which("lean")) or elan_bin.is_file()
        if not has_native_lean:
            self.skipTest("本机未安装原生 Lean 4，跳过真实内核对抗样本实测")

        scripts_dir = str(AXIOMS_DIR / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from _axiom_lib.lean_gate import audit_lean_suite

        with tempfile.TemporaryDirectory(prefix="axioms-kernel-adv-") as tmp:
            base = Path(tmp).resolve()

            # 07-N0（B1 硬短路）：静态违禁文件（含 #eval）在 --run-lean 下必须直接短路，绝不启动编译
            d_b1 = base / "n0_b1"
            d_b1.mkdir()
            (d_b1 / "BadStatic.lean").write_text("#eval 1 + 1\ntheorem ok_t : True := trivial\n", encoding="utf-8")
            rc_b1, rep_b1 = _run_gate("check-lean", "--lean-dir", str(d_b1), "--run-lean")
            self.assertEqual(rc_b1, 2, rep_b1)
            self.assertFalse(rep_b1["lean_compiler"]["executed"])
            self.assertEqual(rep_b1["lean_compiler"]["status"], "skipped_due_to_static_violations")

            # 07-N1：@[extern] opaque 常量 -> KERNEL_FORBIDDEN_EXTERN_OR_OPAQUE
            d_n1 = base / "n1_extern"
            d_n1.mkdir()
            (d_n1 / "N1.lean").write_text('@[extern "c_abort"] opaque fake_nat : Nat\ntheorem t1 : fake_nat = fake_nat := rfl\n', encoding="utf-8")
            r1 = audit_lean_suite(d_n1, run_lean=True, allow_static_bypass_for_kernel_test=True)
            self.assertFalse(r1["ok"], r1)
            self.assertIn("KERNEL_FORBIDDEN_EXTERN_OR_OPAQUE", {v["code"] for v in r1["violations"]})

            # 07-N2：unsafe def + @[implemented_by] + partial def -> KERNEL_UNSAFE_OR_PARTIAL + KERNEL_FORBIDDEN_EXTERN_OR_OPAQUE
            d_n2 = base / "n2_unsafe"
            d_n2.mkdir()
            (d_n2 / "N2.lean").write_text(
                "unsafe def loopFalse : False := loopFalse\n"
                "unsafe def evilImpl (_ : Bool) : Bool := true\n"
                "@[implemented_by evilImpl]\n"
                "def guardCheck (x : Bool) : Bool := x\n"
                "partial def partialLoop (x : Nat) : Nat := partialLoop x\n",
                encoding="utf-8",
            )
            r2 = audit_lean_suite(d_n2, run_lean=True, allow_static_bypass_for_kernel_test=True)
            self.assertFalse(r2["ok"], r2)
            c2 = {v["code"] for v in r2["violations"]}
            self.assertIn("KERNEL_UNSAFE_OR_PARTIAL", c2)
            self.assertIn("KERNEL_FORBIDDEN_EXTERN_OR_OPAQUE", c2)

            # 07-N3：def fake_thm : Nat := 42 冒充定理名 -> NOT_A_KERNEL_THEOREM
            d_n3 = base / "n3_defn"
            d_n3.mkdir()
            (d_n3 / "N3.lean").write_text("def fake_thm : Nat := 42\n", encoding="utf-8")
            proj_n3 = base / "proj_n3"
            proj_n3.mkdir()
            (proj_n3 / "a.py").write_text("x = 1\n", encoding="utf-8")
            case_n3 = base / "case_n3.json"
            _run_gate("init", "--profile", "core", "--case", str(case_n3), "--project-root", str(proj_n3), "--subject", "N3", "--lean-dir", str(d_n3))
            p_n3 = base / "l0_n3.json"
            p_n3.write_text(json.dumps({
                "wellformed_guards": [{"expression": "x=1", "status": "satisfied", "witnesses": [{"type": "lean_theorem", "module": "N3", "theorem": "fake_thm"}]}],
                "witnesses": [{"type": "source_anchor", "file": "a.py", "line_start": 1, "line_end": 1, "excerpt": "x = 1"}],
            }), encoding="utf-8")
            rc_n3, out_n3 = _run_gate("advance", "--case", str(case_n3), "--stage", "L0_WELLFORMED", "--payload", str(p_n3), "--lean-dir", str(d_n3), "--run-lean")
            self.assertEqual(rc_n3, 2, out_n3)
            self.assertEqual(out_n3["error_code"], "NOT_A_KERNEL_THEOREM")

            # 07-N4：namespace 阴影 def Nonempty := True + stdout 欺骗 #check + sorry 定理 -> KERNEL_MISSING_NONEMPTY_WITNESS + DISALLOWED_KERNEL_AXIOM
            d_n4 = base / "n4_shadow"
            d_n4.mkdir()
            (d_n4 / "N4.lean").write_text(
                "namespace Evil\n"
                "def Nonempty (_ : Sort u) : Prop := True\n"
                "structure EmptyTrap where\n  boom : False\n"
                "theorem emptyTrap_nonempty : Nonempty EmptyTrap := trivial\n"
                "theorem fake_clean : False := sorry\n"
                '#check "\'Evil.fake_clean\' does not depend on any axioms"\n'
                "end Evil\n",
                encoding="utf-8",
            )
            r4 = audit_lean_suite(d_n4, run_lean=True, allow_static_bypass_for_kernel_test=True)
            self.assertFalse(r4["ok"], r4)
            c4 = {v["code"] for v in r4["violations"]}
            self.assertIn("KERNEL_MISSING_NONEMPTY_WITNESS", c4)
            self.assertIn("DISALLOWED_KERNEL_AXIOM", c4)

            # 07-N5：initialize IO.Process.exit 0 劫持尝试（不触发退出）+ 嵌套块注释 import Init.System.IO（07-N6）
            d_n5 = base / "n5_init"
            d_n5.mkdir()
            (d_n5 / "N5.lean").write_text(
                "/- outer /- inner -/ -/\n"
                "import Init.System.IO\n"
                "initialize IO.Process.exit 0\n"
                "theorem ok_t : True := trivial\n",
                encoding="utf-8",
            )
            r5 = audit_lean_suite(d_n5, run_lean=True, allow_static_bypass_for_kernel_test=True)
            self.assertFalse(r5["ok"], r5)
            self.assertIn("FORBIDDEN_HEADER_IMPORT", {v["code"] for v in r5["violations"]})

            # 07-N7：仅靠 native_decide（源码未写 ofReduceBool 字面量）触发非白名单公理 -> DISALLOWED_KERNEL_AXIOM
            d_n7 = base / "n7_nativedec"
            d_n7.mkdir()
            (d_n7 / "N7.lean").write_text("theorem t_nd : 1 + 1 = 2 := by native_decide\n", encoding="utf-8")
            r7 = audit_lean_suite(d_n7, run_lean=True, allow_static_bypass_for_kernel_test=True)
            self.assertFalse(r7["ok"], r7)
            self.assertIn("DISALLOWED_KERNEL_AXIOM", {v["code"] for v in r7["violations"]})

            # 07-N8（Astra #3 实测）：@[csimp] 编译器替换定理在 loadExts=false 下由 CSimp.ext.ext.getModuleEntries 拦截 -> KERNEL_FORBIDDEN_INIT_OR_CSIMP
            d_n8 = base / "n8_csimp"
            d_n8.mkdir()
            (d_n8 / "N8.lean").write_text(
                "def f1 (n : Nat) : Nat := n\n"
                "def f2 (n : Nat) : Nat := n + 0\n"
                "@[csimp] theorem f1_eq_f2 : @f1 = @f2 := by funext n; simp [f1, f2]\n",
                encoding="utf-8",
            )
            r8 = audit_lean_suite(d_n8, run_lean=True, allow_static_bypass_for_kernel_test=True)
            self.assertFalse(r8["ok"], r8)
            self.assertIn("KERNEL_FORBIDDEN_INIT_OR_CSIMP", {v["code"] for v in r8["violations"]})

            # 07-N9（Astra #4 实测）：字符字面量 '"' 后接 set_option debug.skipKernelTC true 必须被词法扫描器拦截硬短路
            d_n9 = base / "n9_charlit"
            d_n9.mkdir()
            (d_n9 / "N9.lean").write_text(
                "def quoteChar : Char := '\"'\n"
                "set_option debug.skipKernelTC true\n"
                "theorem ok_t : True := trivial\n",
                encoding="utf-8",
            )
            rc_n9, rep_n9 = _run_gate("check-lean", "--lean-dir", str(d_n9), "--run-lean")
            self.assertEqual(rc_n9, 2, rep_n9)
            self.assertFalse(rep_n9["lean_compiler"]["executed"])
            self.assertEqual(rep_n9["lean_compiler"]["status"], "skipped_due_to_static_violations")
            self.assertIn("FORBIDDEN_SET_OPTION", {v["code"] for v in rep_n9["violations"]})

    def test_09_strict_schema_rejects_bool_as_int_unknown_fields_and_l1_cycles(self) -> None:
        """L1 层：单源严格 Schema 拒 bool 冒充 int、字符串冒充 bool/list、重复键、未知键及 L1 依赖环与跨层边界错配。"""
        with tempfile.TemporaryDirectory(prefix="axioms-schema-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "proj"
            proj.mkdir()
            (proj / "app.py").write_text("def check():\n    return True\n", encoding="utf-8")
            (proj / "probe.log").write_text("ok=1\nexit_code=0\n", encoding="utf-8")
            (proj / "plain.log").write_text("all tests ok without rc marker\n", encoding="utf-8")
            src_wit = [{"type": "source_anchor", "file": "app.py", "line_start": 1, "line_end": 2, "excerpt": "return True"}]

            c_cae = root / "c_cae.json"
            _run_gate("init", "--profile", "cae", "--case", str(c_cae), "--project-root", str(proj), "--subject", "SchemaTest")

            def adv(cfile: Path, stage: str, obj: dict | str) -> tuple[int, dict]:
                pf = root / f"p_{stage}_{abs(hash(str(obj))) % 100000}.json"
                if isinstance(obj, str):
                    pf.write_text(obj, encoding="utf-8")
                else:
                    pf.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
                return _run_gate("advance", "--case", str(cfile), "--stage", stage, "--payload", str(pf))

            # 9a: source_anchor line_start=True 冒充 int
            rc, out = adv(c_cae, "S0_TYPING", {
                "domain_guards": [{"expression": "M>0", "status": "satisfied", "witnesses": [{"type": "source_anchor", "file": "app.py", "line_start": True, "line_end": 2, "excerpt": "return True"}]}],
                "witnesses": src_wit,
            })
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error_code"], "SCHEMA_TYPE_ERROR")
            self.assertEqual(out["details"].get("reason"), "bool_not_allowed_for_int")

            # 9b: probe_exec recorded_exit_code=False 冒充 0 + 无退出码纯文本回执拒收
            rc_b, out_b = adv(c_cae, "S0_TYPING", {
                "domain_guards": [{"expression": "M>0", "status": "satisfied", "witnesses": src_wit}],
                "witnesses": [{"type": "probe_exec", "command": "run", "execute": False, "receipt_file": "probe.log", "recorded_exit_code": False, "expected_exit_code": 0, "expected_output_substring": "ok=1"}],
            })
            self.assertEqual(rc_b, 2, out_b)
            self.assertEqual(out_b["error_code"], "SCHEMA_TYPE_ERROR")
            self.assertEqual(out_b["details"].get("reason"), "bool_not_allowed_for_int")

            rc_pl, out_pl = adv(c_cae, "S0_TYPING", {
                "domain_guards": [{"expression": "M>0", "status": "satisfied", "witnesses": src_wit}],
                "witnesses": [{"type": "probe_exec", "command": "run", "execute": False, "receipt_file": "plain.log", "recorded_exit_code": 0, "expected_exit_code": 0, "expected_output_substring": "all tests ok"}],
            })
            self.assertEqual(rc_pl, 2, out_pl)
            self.assertEqual(out_pl["error_code"], "PROBE_RECEIPT_MISSING_EXIT_CODE")

            # 9c: 未知字段硬拒 SCHEMA_UNKNOWN_FIELD + 重复键硬拒 SCHEMA_DUPLICATE_KEY
            rc_u, out_u = adv(c_cae, "S0_TYPING", {
                "domain_guards": [{"expression": "M>0", "status": "satisfied", "witnesses": src_wit}],
                "witnesses": src_wit,
                "bogus_typo_field": True,
            })
            self.assertEqual(rc_u, 2, out_u)
            self.assertEqual(out_u["error_code"], "SCHEMA_UNKNOWN_FIELD")

            rc_dup, out_dup = adv(c_cae, "S0_TYPING", '{"witnesses": [], "witnesses": []}')
            self.assertEqual(rc_dup, 2, out_dup)
            self.assertEqual(out_dup["error_code"], "SCHEMA_DUPLICATE_KEY")

            # 9c-2（Astra #6）：极大浮点指数 1e400 溢出为 inf 硬拒 + 离线 JSON 回执重复 exit_code 键硬拒
            rc_ovf, out_ovf = adv(c_cae, "S0_TYPING", '{"witnesses": [], "x": 1e400}')
            self.assertEqual(rc_ovf, 2, out_ovf)
            self.assertEqual(out_ovf["error_code"], "SCHEMA_NONSTANDARD_NUMBER")

            (proj / "dup_rc.json").write_text('{"exit_code": 1, "exit_code": 0, "msg": "ok=1"}\n', encoding="utf-8")
            rc_jdup, out_jdup = adv(c_cae, "S0_TYPING", {
                "domain_guards": [{"expression": "M>0", "status": "satisfied", "witnesses": src_wit}],
                "witnesses": [{"type": "probe_exec", "command": "run", "execute": False, "receipt_file": "dup_rc.json", "recorded_exit_code": 0, "expected_exit_code": 0, "expected_output_substring": "ok=1"}],
            })
            self.assertEqual(rc_jdup, 2, out_jdup)
            self.assertEqual(out_jdup["error_code"], "SCHEMA_DUPLICATE_KEY")

            # 9d: S2 state_dim=True 与 extra_jump_proven_compatible="false"（字符串冒充 bool）均被拒
            self.assertEqual(adv(c_cae, "S0_TYPING", {"domain_guards": [{"expression": "M>0", "status": "satisfied", "witnesses": src_wit}], "witnesses": src_wit})[0], 0)
            self.assertEqual(adv(c_cae, "S1_ROOT_MEASURE", {
                "measure_checks": {
                    "shrink_limit_vs_positive_measure": {"status": "pass"},
                    "singular_measure_product": {"status": "pass"},
                    "nonlinear_average_commutativity": {"status": "pass"},
                },
                "witnesses": src_wit,
            })[0], 0)
            rc_s2a, out_s2a = adv(c_cae, "S2_SYMMETRY_JUMP", {
                "symmetry_group": "galilean", "formulation_type": "conservative_divergence",
                "state_dim": True, "independent_jump_relations": False, "witnesses": src_wit,
            })
            self.assertEqual(rc_s2a, 2, out_s2a)
            self.assertEqual(out_s2a["error_code"], "SCHEMA_TYPE_ERROR")
            rc_s2b, out_s2b = adv(c_cae, "S2_SYMMETRY_JUMP", {
                "symmetry_group": "galilean", "formulation_type": "conservative_divergence",
                "state_dim": 4, "independent_jump_relations": 5, "extra_jump_proven_compatible": "false", "witnesses": src_wit,
            })
            self.assertEqual(rc_s2b, 2, out_s2b)
            self.assertEqual(out_s2b["error_code"], "SCHEMA_TYPE_ERROR")

            # 9e: L1 依赖环检测（B3）与跨层边界规范化属性冲突（ATK-3）
            c_cyc = root / "c_cyc.json"
            _run_gate("init", "--profile", "core", "--case", str(c_cyc), "--project-root", str(proj), "--subject", "CycleAndBoundary")
            self.assertEqual(adv(c_cyc, "L0_WELLFORMED", {
                "wellformed_guards": [{"expression": "ok", "status": "satisfied", "witnesses": src_wit}],
                "witnesses": src_wit,
            })[0], 0)
            rc_c, out_c = adv(c_cyc, "L1_ROOT_CONSISTENCY", {
                "root_premises_satisfiable": True,
                "modules": [
                    {"name": "mod_a", "status": "consistent", "depends_on": ["mod_b"], "witnesses": src_wit},
                    {"name": "mod_b", "status": "consistent", "depends_on": ["mod_a"], "witnesses": src_wit},
                ],
                "boundary_contracts": [{
                    "object_name": "http_admin_path",
                    "components": [
                        {"component": "nginx_gateway", "attributes": {"case_policy": "sensitive", "dot_norm": "raw"}},
                        {"component": "backend_router", "attributes": {"case_policy": "insensitive", "dot_norm": "posix_norm"}},
                    ],
                }],
                "witnesses": src_wit,
            })
            self.assertEqual(rc_c, 2, out_c)
            self.assertEqual(out_c["current_state"], "HALT_ROOT_CONFLICT")
            c_codes = {it["code"] for it in out_c["structured_issues"]}
            self.assertIn("L1_DEPENDENCY_CYCLE", c_codes)
            self.assertIn("CROSS_LAYER_NORMALIZATION_MISMATCH", c_codes)

            # 9f（Sol B08）：同一 boundary_contracts object_name 拆分两条记录 + 互补空键绕过必须被全局聚合拦截
            c_b08 = root / "c_b08.json"
            _run_gate("init", "--profile", "core", "--case", str(c_b08), "--project-root", str(proj), "--subject", "SplitBoundary")
            self.assertEqual(adv(c_b08, "L0_WELLFORMED", {
                "wellformed_guards": [{"expression": "ok", "status": "satisfied", "witnesses": src_wit}],
                "witnesses": src_wit,
            })[0], 0)
            rc_b08, out_b08 = adv(c_b08, "L1_ROOT_CONSISTENCY", {
                "root_premises_satisfiable": True,
                "modules": [{"name": "m1", "status": "consistent", "witnesses": src_wit}],
                "boundary_contracts": [
                    {
                        "object_name": "shared_path",
                        "components": [
                            {"component": "edge_a", "attributes": {"case_policy": "sensitive"}},
                            {"component": "edge_b", "attributes": {"case_policy": "sensitive"}},
                        ],
                    },
                    {
                        "object_name": "shared_path",
                        "components": [
                            {"component": "app_c", "attributes": {"case_policy": "insensitive", "dot_norm": "posix"}},
                            {"component": "app_d", "attributes": {"dot_norm": "posix"}},
                        ],
                    },
                ],
                "witnesses": src_wit,
            })
            self.assertEqual(rc_b08, 2, out_b08)
            b08_codes = {it["code"] for it in out_b08["structured_issues"]}
            self.assertIn("CROSS_LAYER_NORMALIZATION_MISMATCH", b08_codes)
            self.assertIn("CROSS_LAYER_BOUNDARY_KEY_MISSING", b08_codes)

    def test_12_content_hash_precise_invalidation_broken_chain_and_tx_resume(self) -> None:
        """L2 层：精准失活（保留有效前缀）、根前提漂移、跳步链篡改检测与重构中断幂等续交。"""
        with tempfile.TemporaryDirectory(prefix="axioms-hash-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "proj"
            proj.mkdir()
            fa = proj / "a.py"
            fb = proj / "b.py"
            fa.write_text("A_GUARD = True\n", encoding="utf-8")
            fb.write_text("B_POLICY = 'strict'\n", encoding="utf-8")
            wa = [{"type": "source_anchor", "file": "a.py", "line_start": 1, "line_end": 1, "excerpt": "A_GUARD = True"}]
            wb = [{"type": "source_anchor", "file": "b.py", "line_start": 1, "line_end": 1, "excerpt": "B_POLICY = 'strict'"}]

            cfile = root / "case.json"
            _run_gate("init", "--profile", "core", "--case", str(cfile), "--project-root", str(proj), "--subject", "PreciseInvalidation")

            def sub(st: str, body: dict) -> tuple[int, dict]:
                pf = root / f"{st}.json"
                pf.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
                return _run_gate("advance", "--case", str(cfile), "--stage", st, "--payload", str(pf))

            self.assertEqual(sub("L0_WELLFORMED", {"wellformed_guards": [{"expression": "A_GUARD", "status": "satisfied", "witnesses": wa}], "witnesses": wa})[0], 0)
            self.assertEqual(sub("L1_ROOT_CONSISTENCY", {"root_premises_satisfiable": True, "modules": [{"name": "m_b", "status": "consistent", "witnesses": wb}], "witnesses": wb})[0], 0)

            # 12a: 篡改 b.py 后尝试推进 L2 -> 仅 L1 精准失活，L0 保持 CURRENT 有效前缀
            fb.write_text("B_POLICY = 'relaxed'\n", encoding="utf-8")
            rc_st, out_st = sub("L2_CONSTRAINT_ROLE", {
                "decision_basis_explicit": True, "decision_basis_witnesses": wa,
                "declared_constraints": [{"name": "c1", "objective_role": "necessary_only", "witnesses": wa}],
                "witnesses": wa,
            })
            self.assertEqual(rc_st, 2, out_st)
            self.assertEqual(out_st["error_code"], "STALE_WITNESS_HASH")
            doc = json.loads(cfile.read_text(encoding="utf-8"))
            self.assertEqual(doc["stage_records"]["L0_WELLFORMED"]["freshness_status"], "CURRENT")
            self.assertEqual(doc["stage_records"]["L1_ROOT_CONSISTENCY"]["freshness_status"], "STALE_INVALIDATED")
            self.assertEqual(doc["next_stage"], "L1_ROOT_CONSISTENCY")

            # 修复 b.py 并重新提交 L1 恢复推进（无需重跑 L0）
            fb.write_text("B_POLICY = 'strict'\n", encoding="utf-8")
            self.assertEqual(sub("L1_ROOT_CONSISTENCY", {"root_premises_satisfiable": True, "modules": [{"name": "m_b", "status": "consistent", "witnesses": wb}], "witnesses": wb})[0], 0)

            # 12b: 篡改磁盘 root_claim -> PREMISE_DRIFT_INVALIDATED 且 L0 起全链失活
            doc2 = json.loads(cfile.read_text(encoding="utf-8"))
            doc2["root_claim"] = "篡改后的弱命题"
            cfile.write_text(json.dumps(doc2, ensure_ascii=False, indent=2), encoding="utf-8")
            rc_pm, out_pm = _run_gate("report", "--case", str(cfile))
            self.assertEqual(rc_pm, 2, out_pm)
            self.assertEqual(out_pm["error_code"], "PREMISE_DRIFT_INVALIDATED")
            doc3 = json.loads(cfile.read_text(encoding="utf-8"))
            self.assertEqual(doc3["stage_records"]["L0_WELLFORMED"]["freshness_status"], "STALE_INVALIDATED")

            # 12c: 篡改磁盘 next_stage 企图跳步 -> BROKEN_STAGE_CHAIN
            c_jump = root / "jump.json"
            _run_gate("init", "--profile", "core", "--case", str(c_jump), "--project-root", str(proj), "--subject", "JumpAttack")
            jdoc = json.loads(c_jump.read_text(encoding="utf-8"))
            jdoc["next_stage"] = "L3_EMPIRICAL_EXIT"
            c_jump.write_text(json.dumps(jdoc, ensure_ascii=False, indent=2), encoding="utf-8")
            pf_j = root / "l3_jump.json"
            pf_j.write_text(json.dumps({"witnesses": wa}), encoding="utf-8")
            rc_j, out_j = _run_gate("advance", "--case", str(c_jump), "--stage", "L3_EMPIRICAL_EXIT", "--payload", str(pf_j))
            self.assertEqual(rc_j, 2, out_j)
            self.assertEqual(out_j["error_code"], "BROKEN_STAGE_CHAIN")

            # 12d: downgrade_claim --emit-downgraded-case 继承不可变冲突历史 + pending_child_tx 双写中断后幂等续交（O-B4 / B5）
            c_dg = root / "dg_parent.json"
            _run_gate("init", "--profile", "core", "--case", str(c_dg), "--project-root", str(proj), "--subject", "DgParent", "--claim", "原强命题A")
            pf_dg0 = root / "dg_l0.json"
            pf_dg0.write_text(json.dumps({"wellformed_guards": [{"expression": "g", "status": "violated", "witnesses": wa}], "witnesses": wa}), encoding="utf-8")
            self.assertEqual(_run_gate("advance", "--case", str(c_dg), "--stage", "L0_WELLFORMED", "--payload", str(pf_dg0))[0], 2)
            dg_child = root / "dg_child.json"
            rc_dg, out_dg = _run_gate(
                "resolve", "--case", str(c_dg), "--action", "downgrade_claim",
                "--new-claim", "受限域命题A1", "--stripped-claims", "全局无条件成立",
                "--emit-downgraded-case", str(dg_child), "--rationale", "降格为受限子域命题",
            )
            self.assertEqual(rc_dg, 0, out_dg)
            child_doc = json.loads(dg_child.read_text(encoding="utf-8"))
            self.assertEqual(child_doc["root_claim"], "原强命题A")
            self.assertEqual(child_doc["effective_claim"], "受限域命题A1")
            self.assertEqual(len(child_doc["effective_claim_chain"]), 2)
            self.assertEqual(len(child_doc["immutable_conflict_history"]), 1)

            # 模拟双写子单前崩溃（母单已落盘终态且保留 pending_child_tx，子单未落盘），重调用同命令须幂等续交
            dg_child_resume = root / "dg_child_resume.json"
            pdoc = json.loads(c_dg.read_text(encoding="utf-8"))
            pdoc["pending_child_tx"] = {
                "tx_id": "tx-sim-001",
                "action": "downgrade_claim",
                "target_path": str(dg_child_resume.resolve()),
                "child_doc": child_doc,
            }
            c_dg.write_text(json.dumps(pdoc, ensure_ascii=False, indent=2), encoding="utf-8")
            rc_res, out_res = _run_gate(
                "resolve", "--case", str(c_dg), "--action", "downgrade_claim",
                "--new-claim", "受限域命题A1", "--stripped-claims", "全局无条件成立",
                "--emit-downgraded-case", str(dg_child_resume), "--rationale", "幂等续交",
            )
            self.assertEqual(rc_res, 0, out_res)
            self.assertTrue(out_res.get("resumed_transaction"), out_res)
            self.assertTrue(dg_child_resume.is_file())
            # 模拟后半段中断（子单已落盘与事务同哈希但母单未清空 pending_child_tx），重调用同命令须幂等成功（Sol B06）
            pdoc2 = json.loads(c_dg.read_text(encoding="utf-8"))
            pdoc2["pending_child_tx"] = {
                "tx_id": "tx-sim-002",
                "action": "downgrade_claim",
                "target_path": str(dg_child_resume.resolve()),
                "child_doc": child_doc,
            }
            c_dg.write_text(json.dumps(pdoc2, ensure_ascii=False, indent=2), encoding="utf-8")
            rc_res2, out_res2 = _run_gate(
                "resolve", "--case", str(c_dg), "--action", "downgrade_claim",
                "--new-claim", "受限域命题A1", "--stripped-claims", "全局无条件成立",
                "--emit-downgraded-case", str(dg_child_resume), "--rationale", "后半段幂等续交",
            )
            self.assertEqual(rc_res2, 0, out_res2)
            self.assertTrue(out_res2.get("child_already_materialized"), out_res2)
            self.assertIsNone(json.loads(c_dg.read_text(encoding="utf-8")).get("pending_child_tx"))

            # 12e（Sol B05）：HALT_ROOT_CONFLICT 案卷修改锚定源码后调 report 绝不解除吸收态锁定
            c_lock = root / "halt_locked.json"
            _run_gate("init", "--profile", "core", "--case", str(c_lock), "--project-root", str(proj), "--subject", "HaltLock")
            pf_hl = root / "hl_l0.json"
            pf_hl.write_text(json.dumps({"wellformed_guards": [{"expression": "g", "status": "violated", "witnesses": wa}], "witnesses": wa}), encoding="utf-8")
            self.assertEqual(_run_gate("advance", "--case", str(c_lock), "--stage", "L0_WELLFORMED", "--payload", str(pf_hl))[0], 2)
            fa.write_text("A_GUARD = False  # modified after root conflict\n", encoding="utf-8")
            rc_hlr, out_hlr = _run_gate("report", "--case", str(c_lock))
            self.assertEqual(rc_hlr, 0, out_hlr)
            self.assertEqual(out_hlr["current_state"], "HALT_ROOT_CONFLICT")
            fa.write_text("A_GUARD = True\n", encoding="utf-8")

            # 12f（Sol B04）：静默篡改已通过关口的 payload 内容（保留旧 stage_digest）-> BROKEN_STAGE_CHAIN
            c_tamp = root / "tamper_payload.json"
            _run_gate("init", "--profile", "core", "--case", str(c_tamp), "--project-root", str(proj), "--subject", "TamperPayload")
            pf_tp0 = root / "tp_l0.json"
            pf_tp0.write_text(json.dumps({"wellformed_guards": [{"expression": "A_GUARD", "status": "satisfied", "witnesses": wa}], "witnesses": wa}), encoding="utf-8")
            self.assertEqual(_run_gate("advance", "--case", str(c_tamp), "--stage", "L0_WELLFORMED", "--payload", str(pf_tp0))[0], 0)
            tdoc = json.loads(c_tamp.read_text(encoding="utf-8"))
            tdoc["stage_records"]["L0_WELLFORMED"]["payload"]["wellformed_guards"][0]["expression"] = "FORGED"
            c_tamp.write_text(json.dumps(tdoc, ensure_ascii=False, indent=2), encoding="utf-8")
            pf_tp1 = root / "tp_l1.json"
            pf_tp1.write_text(json.dumps({"root_premises_satisfiable": True, "modules": [{"name": "m", "status": "consistent", "witnesses": wb}], "witnesses": wb}), encoding="utf-8")
            rc_tp1, out_tp1 = _run_gate("advance", "--case", str(c_tamp), "--stage", "L1_ROOT_CONSISTENCY", "--payload", str(pf_tp1))
            self.assertEqual(rc_tp1, 2, out_tp1)
            self.assertEqual(out_tp1["error_code"], "BROKEN_STAGE_CHAIN")

    def test_14_dual_hash_crlf_stability_shebang_bom_sensitivity_and_relocation(self) -> None:
        """L4 层：普通文本 CRLF/LF 哈希一致 + shebang/UTF-8 BOM 按 raw_sha256 失活 + 跨目录 --project-root 重定位。"""
        with tempfile.TemporaryDirectory(prefix="axioms-dualhash-") as tmp:
            root = Path(tmp).resolve()
            p_lf = root / "proj_lf"
            p_crlf = root / "proj_crlf"
            (p_lf / "src").mkdir(parents=True)
            (p_crlf / "src").mkdir(parents=True)
            (p_lf / "src" / "box.py").write_bytes(b"TOL = 1e-9\nVAL = 1\n")
            (p_crlf / "src" / "box.py").write_bytes(b"TOL = 1e-9\r\nVAL = 1\r\n")

            c_lf = root / "c_lf.json"
            c_crlf = root / "c_crlf.json"
            _run_gate("init", "--profile", "core", "--case", str(c_lf), "--project-root", str(p_lf), "--subject", "DualHash", "--claim", "SameClaim")
            _run_gate("init", "--profile", "core", "--case", str(c_crlf), "--project-root", str(p_crlf), "--subject", "DualHash", "--claim", "SameClaim")

            pay = root / "l0.json"
            pay.write_text(json.dumps({
                "wellformed_guards": [{"expression": "TOL > 0", "status": "satisfied", "witnesses": [{"type": "source_anchor", "file": "src/box.py", "line_start": 1, "line_end": 2, "excerpt": "TOL = 1e-9"}]}],
                "witnesses": [{"type": "source_anchor", "file": "src/box.py", "line_start": 1, "line_end": 2, "excerpt": "VAL = 1"}],
            }), encoding="utf-8")
            self.assertEqual(_run_gate("advance", "--case", str(c_lf), "--stage", "L0_WELLFORMED", "--payload", str(pay))[0], 0)
            self.assertEqual(_run_gate("advance", "--case", str(c_crlf), "--stage", "L0_WELLFORMED", "--payload", str(pay))[0], 0)
            d_lf = json.loads(c_lf.read_text(encoding="utf-8"))
            d_crlf = json.loads(c_crlf.read_text(encoding="utf-8"))
            self.assertEqual(
                d_lf["stage_records"]["L0_WELLFORMED"]["witnesses"][0]["artifact_sha256"],
                d_crlf["stage_records"]["L0_WELLFORMED"]["witnesses"][0]["artifact_sha256"],
            )
            self.assertEqual(d_lf["stage_records"]["L0_WELLFORMED"]["witnesses"][0]["file"], "src/box.py")

            # 14b: 带 shebang 的可执行脚本从 LF 变为 CRLF 必须触发 raw_sha256 失活（O-B5）
            p_sh = root / "proj_sh"
            p_sh.mkdir()
            hook_file = p_sh / "hook.py"
            hook_file.write_bytes(b"#!/usr/bin/env python3\nprint('ok')\n")
            c_sh = root / "c_sh.json"
            _run_gate("init", "--profile", "core", "--case", str(c_sh), "--project-root", str(p_sh), "--subject", "ShebangRaw")
            pay_sh = root / "l0_sh.json"
            pay_sh.write_text(json.dumps({
                "wellformed_guards": [{"expression": "shebang", "status": "satisfied", "witnesses": [{"type": "source_anchor", "file": "hook.py", "line_start": 1, "line_end": 2, "excerpt": "print('ok')"}]}],
                "witnesses": [{"type": "source_anchor", "file": "hook.py", "line_start": 1, "line_end": 2, "excerpt": "print('ok')"}],
            }), encoding="utf-8")
            self.assertEqual(_run_gate("advance", "--case", str(c_sh), "--stage", "L0_WELLFORMED", "--payload", str(pay_sh))[0], 0)
            hook_file.write_bytes(b"#!/usr/bin/env python3\r\nprint('ok')\r\n")
            rc_sh, out_sh = _run_gate("report", "--case", str(c_sh))
            self.assertEqual(rc_sh, 2, out_sh)
            self.assertEqual(out_sh["error_code"], "STALE_WITNESS_HASH")

            # 14c: UTF-8 BOM 增删必须触发 raw_sha256 失活（O-B5）
            p_bom = root / "proj_bom"
            p_bom.mkdir()
            bom_file = p_bom / "cfg.json"
            bom_file.write_bytes(b"\xef\xbb\xbf{\"k\": 1}\n")
            c_bom = root / "c_bom.json"
            _run_gate("init", "--profile", "core", "--case", str(c_bom), "--project-root", str(p_bom), "--subject", "BomRaw")
            pay_bom = root / "l0_bom.json"
            pay_bom.write_text(json.dumps({
                "wellformed_guards": [{"expression": "bom", "status": "satisfied", "witnesses": [{"type": "source_anchor", "file": "cfg.json", "line_start": 1, "line_end": 1, "excerpt": "\"k\": 1"}]}],
                "witnesses": [{"type": "source_anchor", "file": "cfg.json", "line_start": 1, "line_end": 1, "excerpt": "\"k\": 1"}],
            }), encoding="utf-8")
            self.assertEqual(_run_gate("advance", "--case", str(c_bom), "--stage", "L0_WELLFORMED", "--payload", str(pay_bom))[0], 0)
            bom_file.write_bytes(b"{\"k\": 1}\n")
            rc_bom, out_bom = _run_gate("report", "--case", str(c_bom))
            self.assertEqual(rc_bom, 2, out_bom)
            self.assertEqual(out_bom["error_code"], "STALE_WITNESS_HASH")

            # 14d: 跨目录重定位 --project-root
            p_moved = root / "proj_relocated"
            shutil.copytree(p_lf, p_moved)
            rc_reloc, out_reloc = _run_gate("report", "--case", str(c_lf), "--project-root", str(p_moved))
            self.assertEqual(rc_reloc, 0, out_reloc)
            self.assertEqual(out_reloc["freshness"], "CURRENT")

    def test_10_feedback_anti_leakage_and_dag_pollution_guard(self) -> None:
        """L1 层：feedback 防宿主机路径/IP 泄漏、防层 1 物理词倒灌、防评估集名称泄漏与层 1 强制逻辑反例。"""
        with tempfile.TemporaryDirectory(prefix="axioms-fb-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "proj"
            proj.mkdir()
            (proj / "g.py").write_text("OK = True\n", encoding="utf-8")
            (proj / "p.log").write_text("passed\nexit_code=0\n", encoding="utf-8")
            src_w = [{"type": "source_anchor", "file": "g.py", "line_start": 1, "line_end": 1, "excerpt": "OK = True"}]
            prb_w = [{"type": "probe_exec", "command": "run", "execute": False, "receipt_file": "p.log", "recorded_exit_code": 0, "expected_output_substring": "passed"}]
            cfile = root / "case.json"
            _run_gate("init", "--profile", "core", "--case", str(cfile), "--project-root", str(proj), "--subject", "FeedbackGuard")
            for st, body in (
                ("L0_WELLFORMED", {"wellformed_guards": [{"expression": "OK", "status": "satisfied", "witnesses": src_w}], "witnesses": src_w}),
                ("L1_ROOT_CONSISTENCY", {"root_premises_satisfiable": True, "modules": [{"name": "m1", "status": "consistent", "witnesses": src_w}], "witnesses": src_w}),
                ("L2_CONSTRAINT_ROLE", {"decision_basis_explicit": True, "decision_basis_witnesses": src_w, "declared_constraints": [{"name": "c1", "objective_role": "necessary_only", "witnesses": src_w}], "witnesses": src_w}),
                ("L3_EMPIRICAL_EXIT", {
                    "internal_verification": {"status": "passed"}, "external_validation": {"status": "passed"},
                    "construction_inputs": ["train_01"], "evaluation_inputs": ["exp_val_02"],
                    "review_exit": {
                        "exit_kind": "nonperceivable_constraint", "exit_basis_ref": "B", "exit_witnesses": prb_w,
                        "target_exogenous": True, "target_exogeneity_basis": "外生测试断言",
                        "shares_representation_with_generator": False, "has_external_executor": True, "executor_witnesses": prb_w,
                    },
                    "witnesses": prb_w,
                }),
            ):
                pf = root / f"{st}.json"
                pf.write_text(json.dumps(body), encoding="utf-8")
                self.assertEqual(_run_gate("advance", "--case", str(cfile), "--stage", st, "--payload", str(pf))[0], 0)

            # 10a: 宿主机路径与私网 IP 泄漏拦截，且错误文本绝不回显真实敏感原值（Sol B11）
            fake_host_path = "\\".join(("C:", "Users", "demo_user", "secret.py"))
            fake_priv_ip = ".".join(("192", "168", "1", "10"))
            rc_a, out_a = _run_gate(
                "feedback", "--case", str(cfile), "--target-layer", "state_machine",
                "--violated-axiom", "R4", "--summary", f"在 {fake_host_path} 暴露问题", "--rationale", "日志记录",
            )
            self.assertEqual(rc_a, 2, out_a)
            self.assertEqual(out_a["error_code"], "FEEDBACK_SENSITIVE_PATH_LEAK")
            self.assertNotIn("demo_user", out_a["error"])

            rc_ip, out_ip = _run_gate(
                "feedback", "--case", str(cfile), "--target-layer", "state_machine",
                "--violated-axiom", "R4", "--summary", f"在 {fake_priv_ip} 暴露问题", "--rationale", "日志记录",
            )
            self.assertEqual(rc_ip, 2, out_ip)
            self.assertEqual(out_ip["error_code"], "FEEDBACK_SENSITIVE_PATH_LEAK")
            self.assertNotIn(fake_priv_ip, out_ip["error"])

            # 10b: 向第一层倒灌英文及中文物理词（含“雷诺数”）拦截（Sol B11）
            rc_b, out_b = _run_gate(
                "feedback", "--case", str(cfile), "--target-layer", "1",
                "--violated-axiom", "一.4", "--logic-counterexample", "2-element Boolean countermodel M2",
                "--summary", "补充高雷诺数湍流边界层经验", "--rationale", "流体算例经验",
            )
            self.assertEqual(rc_b, 2, out_b)
            self.assertEqual(out_b["error_code"], "LAYER_UPWARD_POLLUTION")

            # 10c: 评估集名称泄漏拦截且报错不回显原名（Sol B11）
            rc_c, out_c = _run_gate(
                "feedback", "--case", str(cfile), "--target-layer", "state_machine",
                "--violated-axiom", "三.2", "--summary", "在 exp_val_02 样本中出现误判", "--rationale", "实测观察",
            )
            self.assertEqual(rc_c, 2, out_c)
            self.assertEqual(out_c["error_code"], "FEEDBACK_EVAL_DATASET_LEAK")
            self.assertNotIn("exp_val_02", out_c["error"])

            # 10d: 第一层缺少或仅填占位词“待补” --logic-counterexample 拦截（Sol B11）
            rc_d, out_d = _run_gate(
                "feedback", "--case", str(cfile), "--target-layer", "1",
                "--violated-axiom", "一.8", "--logic-counterexample", "待补",
                "--summary", "增补抽象分类注记", "--rationale", "文字清晰",
            )
            self.assertEqual(rc_d, 2, out_d)
            self.assertEqual(out_d["error_code"], "INVALID_FEEDBACK_SCHEMA")

            # 10e: 合法脱敏抽象逻辑反例写入第一层，强制标 reference_only=True 与 veto_only
            rc_e, out_e = _run_gate(
                "feedback", "--case", str(cfile), "--target-layer", "1",
                "--violated-axiom", "一.8", "--logic-counterexample", "2-element Boolean countermodel for non-disjoint role",
                "--preconditions", "经典二值闭语句前提集", "--contraindications", "非经典多值逻辑系统",
                "--summary", "补充四格可满足性边界反例", "--rationale", "逻辑反模型确立的最小反例",
            )
            self.assertEqual(rc_e, 0, out_e)
            self.assertTrue(out_e["latest"]["reference_only"])
            self.assertEqual(out_e["latest"]["evidence_ceiling"], "veto_only")

    def test_11_and_13_dual_axis_accounting_and_isolated_packet_replay_contract(self) -> None:
        """L2 层：双正交轴三档终态（STRUCTURE_PASSED / VERIFIED_NO_T / VERIFIED_T_ASSERTED）与净室 packet_replay 全对抗验收。"""
        with tempfile.TemporaryDirectory(prefix="axioms-replay-") as tmp:
            root = Path(tmp).resolve()
            proj = root / "proj"
            proj.mkdir()
            guard_py = proj / "guard.py"
            guard_py.write_text("ALLOWED = {'a', 'b'}\ndef ok(x):\n    return x in ALLOWED\n", encoding="utf-8")
            dom_spec = proj / "domain_enum.json"
            dom_spec.write_text('{"domain": ["a", "b"]}\n', encoding="utf-8")
            (proj / "probe_off.log").write_text("OK\nexit_code=0\n", encoding="utf-8")

            src_w = [{"type": "source_anchor", "file": "guard.py", "line_start": 1, "line_end": 3, "excerpt": "return x in ALLOWED"}]
            live_finite_probe = [{
                "type": "probe_exec",
                "command": f'"{sys.executable}" -B -c "import guard; assert guard.ok(\'a\'); print(\'PROBE_FINITE_OK\')"',
                "execute": True, "expected_exit_code": 0, "expected_output_substring": "PROBE_FINITE_OK",
                "target_files": ["guard.py"],
            }]
            live_exhaustive_probe = [{
                "type": "probe_exec",
                "command": f'"{sys.executable}" -B -c "import guard; assert all(guard.ok(x) for x in (\'a\', \'b\')); print(\'EXHAUSTIVE_OK\')"',
                "execute": True, "expected_exit_code": 0, "expected_output_substring": "EXHAUSTIVE_OK",
                "probe_role": "exhaustive_domain", "domain_spec_file": "domain_enum.json", "exhaustive_domain_size": 2,
                "target_files": ["guard.py"],
            }]

            def run_core_case(name: str, l3_probe: list, extra_l3_exit: dict | None = None) -> Path:
                cf = root / f"{name}.json"
                _run_gate("init", "--profile", "core", "--case", str(cf), "--project-root", str(proj), "--subject", name, "--claim", f"{name} 安全守卫")
                for st, body, ex in (
                    ("L0_WELLFORMED", {"wellformed_guards": [{"expression": "x in ALLOWED", "status": "satisfied", "witnesses": src_w}], "witnesses": src_w}, ()),
                    ("L1_ROOT_CONSISTENCY", {"root_premises_satisfiable": True, "modules": [{"name": "guard_mod", "status": "consistent", "witnesses": src_w}], "witnesses": src_w}, ()),
                    ("L2_CONSTRAINT_ROLE", {
                        "decision_basis_explicit": True, "decision_basis_witnesses": src_w,
                        "declared_constraints": [{"name": "allowed_set", "objective_role": "sufficient_only", "standard_claims": ["A->T"], "witnesses": src_w}],
                        "witnesses": src_w,
                    }, ()),
                    ("L3_EMPIRICAL_EXIT", {
                        "internal_verification": {"status": "passed"}, "external_validation": {"status": "passed"},
                        "construction_inputs": ["dev1"], "evaluation_inputs": ["eval1"],
                        "review_exit": {
                            "exit_kind": "sufficient_certificate",
                            "bridge_premise_ref": "B_guard: guard.py 全集对应有限可达域",
                            "bridge_witnesses": src_w,
                            "actual_state_in_reachable_domain": True,
                            "actual_state_accepted": True,
                            "acceptance_witnesses": l3_probe,
                            "target_exogenous": True,
                            "target_exogeneity_basis": "有限集合成员关系属外生确定性谓词",
                            "shares_representation_with_generator": False,
                            "has_external_executor": True,
                            "executor_witnesses": l3_probe,
                            **(extra_l3_exit or {}),
                        },
                        "witnesses": l3_probe,
                    }, ("--allow-probe-exec",)),
                ):
                    pf = root / f"{name}_{st}.json"
                    pf.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
                    rc, out = _run_gate("advance", "--case", str(cf), "--stage", st, "--payload", str(pf), *ex)
                    self.assertEqual(rc, 0, out)
                return cf

            # 11-bad: L3 企图挂内置 LogicCore.lean 元定理充当 artifact_bound_lean -> UNBOUND_META_THEOREM
            c_meta = root / "meta_abuse.json"
            _run_gate("init", "--profile", "core", "--case", str(c_meta), "--project-root", str(proj), "--subject", "MetaAbuse")
            for st, body in (
                ("L0_WELLFORMED", {"wellformed_guards": [{"expression": "ok", "status": "satisfied", "witnesses": src_w}], "witnesses": src_w}),
                ("L1_ROOT_CONSISTENCY", {"root_premises_satisfiable": True, "modules": [{"name": "m1", "status": "consistent", "witnesses": src_w}], "witnesses": src_w}),
                ("L2_CONSTRAINT_ROLE", {"decision_basis_explicit": True, "decision_basis_witnesses": src_w, "declared_constraints": [{"name": "c1", "objective_role": "necessary_only", "witnesses": src_w}], "witnesses": src_w}),
            ):
                pf = root / f"ma_{st}.json"
                pf.write_text(json.dumps(body), encoding="utf-8")
                self.assertEqual(_run_gate("advance", "--case", str(c_meta), "--stage", st, "--payload", str(pf))[0], 0)
            pf_ma = root / "ma_l3.json"
            pf_ma.write_text(json.dumps({
                "internal_verification": {"status": "passed"}, "external_validation": {"status": "passed"},
                "review_exit": {
                    "exit_kind": "sufficient_certificate", "sufficient_certificate_kind": "artifact_bound_lean",
                    "bridge_premise_ref": "B", "bridge_witnesses": src_w,
                    "actual_state_in_reachable_domain": True, "actual_state_accepted": True,
                    "acceptance_witnesses": [{"type": "lean_theorem", "module": "LogicCore", "theorem": "branch_pos_iff_cells"}],
                    "target_exogenous": True, "target_exogeneity_basis": "外生",
                    "shares_representation_with_generator": False, "has_external_executor": True, "executor_witnesses": live_finite_probe,
                },
                "witnesses": live_finite_probe,
            }), encoding="utf-8")
            rc_ma, out_ma = _run_gate("advance", "--case", str(c_meta), "--stage", "L3_EMPIRICAL_EXIT", "--payload", str(pf_ma), "--allow-probe-exec")
            self.assertEqual(rc_ma, 2, out_ma)
            self.assertIn("UNBOUND_META_THEOREM", {i["code"] for i in out_ma["structured_issues"]})

            # 11-bad-2（Sol B07）：acceptance_witnesses 为有限探针却试图从 executor_witnesses 借穷举探针充当 exhaustive_domain -> 拒收
            c_borrow = root / "borrow_abuse.json"
            _run_gate("init", "--profile", "core", "--case", str(c_borrow), "--project-root", str(proj), "--subject", "BorrowAbuse")
            for st, body in (
                ("L0_WELLFORMED", {"wellformed_guards": [{"expression": "ok", "status": "satisfied", "witnesses": src_w}], "witnesses": src_w}),
                ("L1_ROOT_CONSISTENCY", {"root_premises_satisfiable": True, "modules": [{"name": "m1", "status": "consistent", "witnesses": src_w}], "witnesses": src_w}),
                ("L2_CONSTRAINT_ROLE", {"decision_basis_explicit": True, "decision_basis_witnesses": src_w, "declared_constraints": [{"name": "c1", "objective_role": "necessary_only", "witnesses": src_w}], "witnesses": src_w}),
            ):
                pf = root / f"br_{st}.json"
                pf.write_text(json.dumps(body), encoding="utf-8")
                self.assertEqual(_run_gate("advance", "--case", str(c_borrow), "--stage", st, "--payload", str(pf))[0], 0)
            pf_br = root / "br_l3.json"
            pf_br.write_text(json.dumps({
                "internal_verification": {"status": "passed"}, "external_validation": {"status": "passed"},
                "review_exit": {
                    "exit_kind": "sufficient_certificate", "sufficient_certificate_kind": "exhaustive_domain",
                    "bridge_premise_ref": "B", "bridge_witnesses": src_w,
                    "actual_state_in_reachable_domain": True, "actual_state_accepted": True,
                    "acceptance_witnesses": live_finite_probe,
                    "target_exogenous": True, "target_exogeneity_basis": "外生",
                    "shares_representation_with_generator": False, "has_external_executor": True,
                    "executor_witnesses": live_exhaustive_probe,
                },
                "witnesses": live_finite_probe,
            }), encoding="utf-8")
            rc_br, out_br = _run_gate("advance", "--case", str(c_borrow), "--stage", "L3_EMPIRICAL_EXIT", "--payload", str(pf_br), "--allow-probe-exec")
            self.assertEqual(rc_br, 2, out_br)
            self.assertIn("MISSING_EXHAUSTIVE_DOMAIN_WITNESS", {i["code"] for i in out_br["structured_issues"]})

            # Case A：有限样本实跑探针 -> STRUCTURE_PASSED -> 净室复演升格 VERIFIED_NO_T（绝非 PASSED_ALL_GATES！）
            case_finite = run_core_case("case_finite", live_finite_probe)
            pkt_a = root / "pkt_a"
            rc_exp, out_exp = _run_gate("export-packet", "--case", str(case_finite), "--output-dir", str(pkt_a))
            self.assertEqual(rc_exp, 0, out_exp)
            pkt_sha_a = out_exp["packet_sha256"]
            manifest_a = json.loads((pkt_a / "manifest.json").read_text(encoding="utf-8"))
            # 13a: 防诱导盲包剥离核验——不含主会话自评布尔与原始 witnesses 泄漏（O-B6 / S1）
            l3_blind = [s for s in manifest_a["blind_stages"] if s["stage"] == "L3_EMPIRICAL_EXIT"][0]
            self.assertNotIn("actual_state_accepted", l3_blind["carried_facts"].get("review_exit", {}))
            self.assertNotIn("witnesses", l3_blind["carried_facts"])
            self.assertNotIn("verdict", l3_blind["carried_facts"])
            self.assertTrue((pkt_a / "blobs" / manifest_a["file_tree_blob_sha256"]).is_file())

            # 构建强绑定 harness 账本目录（含与 packet_dir + clean_room + packet_sha 绑定的 APPROVED 票据及 reconcile 文件）
            plan_dir = root / "harness_plans"
            (plan_dir / "reconcile").mkdir(parents=True)

            def register_ledger(tid: str, rid: str, pkt_dir: Path, pkt_sha: str) -> None:
                (plan_dir / f"{tid}.json").write_text(json.dumps({
                    "ticket_id": tid,
                    "status": "APPROVED",
                    "streams": [{
                        "stream_id": "S1",
                        "isolation": "clean_room",
                        "clean_room": True,
                        "target": str(pkt_dir.resolve()),
                        "files": ["manifest.json"],
                        "subtask": f"独立复演包 {pkt_sha}",
                    }],
                }, ensure_ascii=False), encoding="utf-8")
                (plan_dir / "reconcile" / f"{rid}_reconcile.md").write_text(
                    f"# 机械对账 {rid}\npacket_sha256={pkt_sha}\n", encoding="utf-8"
                )

            register_ledger("tkt_case_a", "run_case_a", pkt_a, pkt_sha_a)

            # 13a-stage: 验证 cae_harness --clean-room 不显式传 --files 时自动镜像并内联 manifest.json 与全部无扩展名 blobs/<sha256>
            import importlib.util
            harness_py = REPO_ROOT / "template" / "user-guard" / "skills" / "cae-harness" / "scripts" / "cae_harness.py"
            h_spec = importlib.util.spec_from_file_location("_test_cae_harness_packet_stage", harness_py)
            assert h_spec is not None and h_spec.loader is not None
            h_mod = importlib.util.module_from_spec(h_spec)
            h_spec.loader.exec_module(h_mod)
            stage_root = root / "clean_room_stage_check"
            stage_root.mkdir()
            staged_ws = h_mod._stage_wsl_readonly_workspace(pkt_a, stage_root, rel_files=None, exclude_agent_instructions=True)
            self.assertTrue((staged_ws / "manifest.json").is_file())
            manifest_obj = json.loads((pkt_a / "manifest.json").read_text(encoding="utf-8"))
            expected_blobs = sorted({
                bsha for bsha in (
                    [manifest_obj.get("file_tree_blob_sha256"), manifest_obj.get("git_diff_blob_sha256")]
                    + [item.get("blob_sha256") for item in (manifest_obj.get("artifacts") or [])]
                )
                if isinstance(bsha, str) and len(bsha) == 64
            })
            self.assertGreaterEqual(len(expected_blobs), 2)
            for bsha in expected_blobs:
                staged_blob = staged_ws / "blobs" / bsha
                self.assertTrue(staged_blob.is_file(), f"clean-room 镜像遗漏复演包 blob: blobs/{bsha}")
                self.assertEqual(staged_blob.read_bytes(), (pkt_a / "blobs" / bsha).read_bytes())
            inlined_list: list[str] = []
            h_mod._inline_readonly_prompt("audit", staged_ws, rel_files=None, exclude_agent_instructions=True, included=inlined_list, prefix="codex")
            self.assertIn("manifest.json", inlined_list)
            for bsha in expected_blobs:
                self.assertIn(f"blobs/{bsha}", inlined_list)

            def mk_receipt(
                fname: str,
                pkt_sha: str,
                verdicts: dict,
                *,
                pkt_dir: Path = pkt_a,
                tid: str = "tkt_case_a",
                rid: str = "run_case_a",
                sid: str = "replayer-sess-99",
                clean_room: bool = True,
                salvage: bool = False,
            ) -> Path:
                rp = root / fname
                rec = {
                    "status": "ok", "succeeded": True, "readonly": True, "scope_verified": True, "changed_files": [],
                    "target": str(pkt_dir.resolve()),
                    "isolation": "clean_room" if clean_room else "full", "clean_room": clean_room,
                    "provider_session_id": sid, "engine": "codex:gpt-6.2-sol", "model": "gpt-6.2-sol",
                    "ticket_id": tid,
                    "reconciliation": {"run_id": rid},
                    "result": {"content": json.dumps({"packet_sha256": pkt_sha, "stage_verdicts": verdicts}, ensure_ascii=False)},
                }
                if salvage:
                    rec["timeout_salvage"] = {"resumable": False}
                rp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                return rp

            def mk_all_ok(cert_kind: str = "finite_probe_only") -> dict:
                return {
                    "L0_WELLFORMED": {"verdict": "consistent", "requested_paths": [], "rationale": "L0 源码与良构守卫一致"},
                    "L1_ROOT_CONSISTENCY": {"verdict": "consistent", "requested_paths": [], "rationale": "L1 模块无环一致"},
                    "L2_CONSTRAINT_ROLE": {
                        "verdict": "consistent", "requested_paths": [], "rationale": "L2 约束角色与源码一致",
                        "blind_assertions": {"objective_roles": {"allowed_set": "sufficient_only"}},
                    },
                    "L3_EMPIRICAL_EXIT": {
                        "verdict": "consistent", "requested_paths": [], "rationale": "L3 外生探针实证一致",
                        "blind_assertions": {
                            "target_exogenous": True,
                            "exit_kind": "sufficient_certificate",
                            "sufficient_certificate_kind": cert_kind,
                        },
                    },
                }

            all_ok_a = mk_all_ok("finite_probe_only")

            # 13b: 缺少 --harness-plan-dir / 同上下文自签 / 非净室 / timeout_salvage / 盲推不一致 全部拒收
            r_no_ledger = mk_receipt("r_no_ledger.json", pkt_sha_a, all_ok_a)
            rc_nl, out_nl = _run_gate("verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_no_ledger), "--generator-session-id", "main-sess-1")
            self.assertEqual(rc_nl, 2, out_nl)
            self.assertEqual(out_nl["error_code"], "REPLAY_HARNESS_LEDGER_REQUIRED")

            r_same = mk_receipt("r_same.json", pkt_sha_a, all_ok_a, sid="main-sess-1")
            rc_s, out_s = _run_gate("verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_same), "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-1")
            self.assertEqual(rc_s, 2, out_s)
            self.assertEqual(out_s["error_code"], "SAME_CONTEXT_REPLAY_FORBIDDEN")

            r_ncr = mk_receipt("r_ncr.json", pkt_sha_a, all_ok_a, clean_room=False)
            rc_nc, out_nc = _run_gate("verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_ncr), "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-1")
            self.assertEqual(rc_nc, 2, out_nc)
            self.assertEqual(out_nc["error_code"], "REPLAY_NOT_CLEAN_ROOM")

            r_salv = mk_receipt("r_salv.json", pkt_sha_a, all_ok_a, salvage=True)
            rc_sv, out_sv = _run_gate("verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_salv), "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-1")
            self.assertEqual(rc_sv, 2, out_sv)
            self.assertEqual(out_sv["error_code"], "REPLAY_TIMEOUT_SALVAGE_REJECTED")

            # Opus B1：盲推角色与作者原值冲突 -> REPLAY_ASSERTION_MISMATCH
            bad_blind = {**all_ok_a, "L2_CONSTRAINT_ROLE": {**all_ok_a["L2_CONSTRAINT_ROLE"], "blind_assertions": {"objective_roles": {"allowed_set": "necessary_only"}}}}
            r_bb = mk_receipt("r_bb.json", pkt_sha_a, bad_blind)
            rc_bb, out_bb = _run_gate("verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_bb), "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-1")
            self.assertEqual(rc_bb, 2, out_bb)
            self.assertEqual(out_bb["error_code"], "REPLAY_ASSERTION_MISMATCH")

            # 13c: insufficient_context 与 inconsistent 均拒授并记入只追加哈希链 replay_attempts 账本
            insuff_v = {**all_ok_a, "L1_ROOT_CONSISTENCY": {"verdict": "insufficient_context", "requested_paths": ["domain_enum.json"], "rationale": "缺完整域定义"}}
            r_ins = mk_receipt("r_ins.json", pkt_sha_a, insuff_v)
            rc_in, out_in = _run_gate("verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_ins), "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-1")
            self.assertEqual(rc_in, 2, out_in)
            self.assertEqual(out_in["error_code"], "REPLAY_INSUFFICIENT_CONTEXT")
            self.assertIn("domain_enum.json", out_in["details"]["requested_paths"])

            incons_v = {**all_ok_a, "L2_CONSTRAINT_ROLE": {"verdict": "inconsistent", "requested_paths": [], "rationale": "角色与宣称矛盾"}}
            r_inc = mk_receipt("r_inc.json", pkt_sha_a, incons_v)
            rc_ic, out_ic = _run_gate("verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_inc), "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-1")
            self.assertEqual(rc_ic, 2, out_ic)
            self.assertEqual(out_ic["error_code"], "REPLAY_VERDICT_CONFLICT")

            # Opus B4：未传 --allow-probe-exec 时有实跑探针禁止跳过当轮重跑 -> PROBE_RERUN_REQUIRED_FOR_VERIFIED
            r_ok_a = mk_receipt("r_ok_a.json", pkt_sha_a, all_ok_a)
            rc_norun, out_norun = _run_gate(
                "verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_ok_a),
                "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-1",
            )
            self.assertEqual(rc_norun, 2, out_norun)
            self.assertEqual(out_norun["error_code"], "PROBE_RERUN_REQUIRED_FOR_VERIFIED")

            # 13d: 合法净室回执 + 强绑定账本交叉验签 + 盲推比对 + 实跑重演 -> 升格 VERIFIED_NO_T
            rc_oka, out_oka = _run_gate(
                "verify-replay", "--case", str(case_finite), "--packet", str(pkt_a), "--receipt", str(r_ok_a),
                "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-1", "--allow-probe-exec",
            )
            self.assertEqual(rc_oka, 0, out_oka)
            self.assertEqual(out_oka["current_state"], "VERIFIED_NO_T")
            self.assertEqual(out_oka["verification_level"], "VERIFIED")
            self.assertEqual(out_oka["t_assertion_level"], "NO_T_ASSERTION")
            self.assertEqual(out_oka["original_claim_status"], "VERIFIED_NO_T_ASSERTION")
            self.assertTrue(out_oka["packet_fitting_suspected"])

            # report --require-verified 通过 VERIFIED_NO_T，但 --require-t-asserted 拒绝（O-B1 / A7）
            self.assertEqual(_run_gate("report", "--case", str(case_finite), "--require-verified")[0], 0)
            rc_rt, out_rt = _run_gate("report", "--case", str(case_finite), "--require-t-asserted")
            self.assertEqual(rc_rt, 2, out_rt)
            self.assertEqual(out_rt["error_code"], "T_ASSERTION_NOT_GRANTED")

            # Case B：exhaustive_domain 穷举有限可达域 + 首次即通过净室复演 -> 升格 VERIFIED_T_ASSERTED（PASSED_ALL_GATES）
            case_exh = run_core_case("case_exh", live_exhaustive_probe, {"sufficient_certificate_kind": "exhaustive_domain"})
            # Sol B04：仅篡改磁盘 current_state=VERIFIED_T_ASSERTED 伪造终态 -> report --require-t-asserted 必须拒收
            fake_exh = root / "fake_exh.json"
            fdoc = json.loads(case_exh.read_text(encoding="utf-8"))
            fdoc["current_state"] = "VERIFIED_T_ASSERTED"
            fake_exh.write_text(json.dumps(fdoc, ensure_ascii=False, indent=2), encoding="utf-8")
            rc_fex, out_fex = _run_gate("report", "--case", str(fake_exh), "--require-t-asserted")
            self.assertEqual(rc_fex, 2, out_fex)
            self.assertEqual(out_fex["error_code"], "CASE_NOT_VERIFIED")

            pkt_b = root / "pkt_b"
            rc_eb, out_eb = _run_gate("export-packet", "--case", str(case_exh), "--output-dir", str(pkt_b))
            self.assertEqual(rc_eb, 0, out_eb)
            pkt_sha_b = out_eb["packet_sha256"]
            register_ledger("tkt_case_b", "run_case_b", pkt_b, pkt_sha_b)

            # 跨单重放攻击：将 Case A 的包/回执注入 Case B -> REPLAY_PACKET_HASH_MISMATCH
            rc_cross, out_cross = _run_gate(
                "verify-replay", "--case", str(case_exh), "--packet", str(pkt_a), "--receipt", str(r_ok_a),
                "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-2",
            )
            self.assertEqual(rc_cross, 2, out_cross)
            self.assertEqual(out_cross["error_code"], "REPLAY_PACKET_HASH_MISMATCH")

            all_ok_b = mk_all_ok("exhaustive_domain")
            r_ok_b = mk_receipt("r_ok_b.json", pkt_sha_b, all_ok_b, pkt_dir=pkt_b, tid="tkt_case_b", rid="run_case_b", sid="replayer-sess-100")
            rc_okb, out_okb = _run_gate(
                "verify-replay", "--case", str(case_exh), "--packet", str(pkt_b), "--receipt", str(r_ok_b),
                "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-2", "--allow-probe-exec",
            )
            self.assertEqual(rc_okb, 0, out_okb)
            self.assertEqual(out_okb["current_state"], "VERIFIED_T_ASSERTED")
            self.assertEqual(out_okb["original_claim_status"], "PASSED_ALL_GATES")
            self.assertEqual(_run_gate("report", "--case", str(case_exh), "--require-t-asserted", "--allow-probe-exec")[0], 0)

            # Sol B10：已升格 VERIFIED_T_ASSERTED 的案卷收到 inconsistent 复演回执 -> 立即撤销旧证书并降回 STRUCTURE_PASSED
            case_revoke = root / "case_revoke.json"
            shutil.copyfile(case_exh, case_revoke)
            incons_b = {**all_ok_b, "L1_ROOT_CONSISTENCY": {"verdict": "inconsistent", "requested_paths": [], "rationale": "复演发现模块反例"}}
            r_rev = mk_receipt("r_rev.json", pkt_sha_b, incons_b, pkt_dir=pkt_b, tid="tkt_case_b", rid="run_case_b", sid="replayer-sess-101")
            rc_rv, out_rv = _run_gate(
                "verify-replay", "--case", str(case_revoke), "--packet", str(pkt_b), "--receipt", str(r_rev),
                "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-sess-2",
            )
            self.assertEqual(rc_rv, 2, out_rv)
            self.assertEqual(out_rv["error_code"], "REPLAY_VERDICT_CONFLICT")
            rev_doc = json.loads(case_revoke.read_text(encoding="utf-8"))
            self.assertEqual(rev_doc["replay_certificate"]["status"], "REVOKED_BY_REPLAY_CONFLICT")
            self.assertEqual(rev_doc["current_state"], "STRUCTURE_PASSED")
            self.assertEqual(_run_gate("report", "--case", str(case_revoke), "--require-verified")[0], 2)

            # Sol B05：仅第 N+1 关（gate_sha256 升级）失活时回落 STRUCTURE_PASSED（不永久卡死）
            case_cert_stale = root / "case_cert_stale.json"
            sdoc = json.loads(case_exh.read_text(encoding="utf-8"))
            sdoc["replay_certificate"]["gate_sha256"] = "0" * 64
            case_cert_stale.write_text(json.dumps(sdoc, ensure_ascii=False, indent=2), encoding="utf-8")
            rc_cs, out_cs = _run_gate("report", "--case", str(case_cert_stale))
            self.assertEqual(rc_cs, 2, out_cs)
            self.assertEqual(out_cs["error_code"], "STALE_WITNESS_HASH")
            self.assertEqual(json.loads(case_cert_stale.read_text(encoding="utf-8"))["current_state"], "STRUCTURE_PASSED")

            # Opus B5：降格子单升格后 original_claim_status 必须永久保持 ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED
            c_parent_b5 = root / "parent_b5.json"
            _run_gate("init", "--profile", "core", "--case", str(c_parent_b5), "--project-root", str(proj), "--subject", "ParentB5", "--claim", "原不可满足强命题")
            pf_p0 = root / "p0_b5.json"
            pf_p0.write_text(json.dumps({"wellformed_guards": [{"expression": "bad", "status": "violated", "witnesses": src_w}], "witnesses": src_w}), encoding="utf-8")
            self.assertEqual(_run_gate("advance", "--case", str(c_parent_b5), "--stage", "L0_WELLFORMED", "--payload", str(pf_p0))[0], 2)
            c_child_b5 = root / "child_b5.json"
            self.assertEqual(_run_gate(
                "resolve", "--case", str(c_parent_b5), "--action", "downgrade_claim",
                "--new-claim", "受限子域命题B5", "--stripped-claims", "全域宣称",
                "--emit-downgraded-case", str(c_child_b5), "--rationale", "降格验证",
            )[0], 0)
            for st, body, ex in (
                ("L0_WELLFORMED", {"wellformed_guards": [{"expression": "x in ALLOWED", "status": "satisfied", "witnesses": src_w}], "witnesses": src_w}, ()),
                ("L1_ROOT_CONSISTENCY", {"root_premises_satisfiable": True, "modules": [{"name": "guard_mod", "status": "consistent", "witnesses": src_w}], "witnesses": src_w}, ()),
                ("L2_CONSTRAINT_ROLE", {"decision_basis_explicit": True, "decision_basis_witnesses": src_w, "declared_constraints": [{"name": "allowed_set", "objective_role": "sufficient_only", "standard_claims": ["A->T"], "witnesses": src_w}], "witnesses": src_w}, ()),
                ("L3_EMPIRICAL_EXIT", {
                    "internal_verification": {"status": "passed"}, "external_validation": {"status": "passed"},
                    "review_exit": {
                        "exit_kind": "sufficient_certificate", "sufficient_certificate_kind": "exhaustive_domain",
                        "bridge_premise_ref": "B5", "bridge_witnesses": src_w,
                        "actual_state_in_reachable_domain": True, "actual_state_accepted": True, "acceptance_witnesses": live_exhaustive_probe,
                        "target_exogenous": True, "target_exogeneity_basis": "外生",
                        "shares_representation_with_generator": False, "has_external_executor": True, "executor_witnesses": live_exhaustive_probe,
                    },
                    "witnesses": live_exhaustive_probe,
                }, ("--allow-probe-exec",)),
            ):
                pf = root / f"cb5_{st}.json"
                pf.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(_run_gate("advance", "--case", str(c_child_b5), "--stage", st, "--payload", str(pf), *ex)[0], 0)
            pkt_c5 = root / "pkt_c5"
            rc_ec5, out_ec5 = _run_gate("export-packet", "--case", str(c_child_b5), "--output-dir", str(pkt_c5))
            self.assertEqual(rc_ec5, 0, out_ec5)
            register_ledger("tkt_c5", "run_c5", pkt_c5, out_ec5["packet_sha256"])
            r_c5 = mk_receipt("r_c5.json", out_ec5["packet_sha256"], all_ok_b, pkt_dir=pkt_c5, tid="tkt_c5", rid="run_c5", sid="replayer-c5")
            rc_vc5, out_vc5 = _run_gate(
                "verify-replay", "--case", str(c_child_b5), "--packet", str(pkt_c5), "--receipt", str(r_c5),
                "--harness-plan-dir", str(plan_dir), "--generator-session-id", "main-c5", "--allow-probe-exec",
            )
            self.assertEqual(rc_vc5, 0, out_vc5)
            self.assertEqual(out_vc5["current_state"], "VERIFIED_T_ASSERTED")
            self.assertEqual(out_vc5["original_claim_status"], "ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED")
            self.assertEqual(out_vc5["effective_claim_status"], "PASSED_ALL_GATES")

            # O-B3 防 TOCTOU：升格 VERIFIED_T_ASSERTED 后篡改 guard.py -> report 现场重算将第 N+1 关复演证书标为 STALE_INVALIDATED
            guard_py.write_text("ALLOWED = {'a', 'b', 'backdoor'}\ndef ok(x):\n    return x in ALLOWED\n", encoding="utf-8")
            rc_toctou, out_toctou = _run_gate("report", "--case", str(case_exh), "--require-verified")
            self.assertEqual(rc_toctou, 2, out_toctou)
            self.assertEqual(out_toctou["error_code"], "STALE_WITNESS_HASH")
            exh_doc = json.loads(case_exh.read_text(encoding="utf-8"))
            self.assertEqual(exh_doc["replay_certificate"]["status"], "STALE_INVALIDATED")
            self.assertEqual(exh_doc["current_state"], "STALE_INVALIDATED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
