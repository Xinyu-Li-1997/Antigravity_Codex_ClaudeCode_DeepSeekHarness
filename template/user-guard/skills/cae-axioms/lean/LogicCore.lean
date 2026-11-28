-- Copyright 2026 Xinyu Li (Xinyu-Li-1997)
-- SPDX-License-Identifier: Apache-2.0
/-
  LogicCore.lean
  第一层（纯哲学与多类逻辑底座）纯 Init.Prelude 外延语义骨架（零外部库导入）：
  - 公理足迹：仅依赖 Lean 标准古典内核（#print axioms ⊆ {propext, Classical.choice, Quot.sound}）
  - 一.2 精化子类型定义域守卫、非空求值见证、经典二值坍缩与析取豁免反例
  - 一.4 外延语义互斥三分（显式含 Satisfiable）与非平凡定义松弛回锁定理
  - 一.8 保编号互斥七分（(σ_A, σ_¬A) 生成式、逆否桥接、四格占用等价、7 类独立见证）与约束间互斥三分并列律
  - 一.9 单调层级链最早根冲突唯一性与向下传染、多冲突核命中必要性、Δ1↦Δ2 防消症反例与菱形合取反例
  - 纯映射纤维引理（支撑三.4 非单射逆推非唯一与四.2/四.4 同源表征复核不可分性，零经验公理）
-/

set_option autoImplicit false
universe u v

namespace CaeAxioms.LogicCore

-- ============================================================================
-- 一、一.2 精化子类型定义域守卫与单类二值编码边界
-- ============================================================================

structure DomainGuarded (α : Type u) (β : Type v) (D : α → Prop) where
  eval : { x : α // D x } → β

def applyGuarded {α : Type u} {β : Type v} {D : α → Prop}
    (op : DomainGuarded α β D) (x : α) (hx : D x) : β :=
  op.eval ⟨x, hx⟩

theorem domainGuarded_nonempty : Nonempty (DomainGuarded Nat Nat (fun n => 0 < n)) :=
  ⟨{ eval := fun x => x.val }⟩

theorem domainGuarded_inhabited_eval :
    ∃ (op : DomainGuarded Nat Nat (fun n => 0 < n)) (x : Nat) (hx : 0 < x),
      applyGuarded op x hx = 1 :=
  ⟨{ eval := fun z => z.val }, 1, by decide, rfl⟩

/-- 外延模型谓词语义（全部采用 reducible 的 abbrev，保障类型类合成与穿透展开）。 -/
abbrev Theory (M : Type u) := M → Prop
abbrev Entails {M : Type u} (Γ : Theory M) (Q : M → Prop) : Prop := ∀ m : M, Γ m → Q m
abbrev Satisfiable {M : Type u} (Γ : Theory M) : Prop := ∃ m : M, Γ m
abbrev Unsat {M : Type u} (Γ : Theory M) : Prop := ∀ m : M, ¬ Γ m
abbrev Extend {M : Type u} (Γ Δ : Theory M) : Theory M := fun m => Γ m ∧ Δ m
abbrev WithProp {M : Type u} (Γ : Theory M) (P : M → Prop) : Theory M := fun m => Γ m ∧ P m

private theorem bool2_forall {P : Bool × Bool → Prop}
    (hff : P (false, false)) (hft : P (false, true))
    (htf : P (true, false)) (htt : P (true, true)) :
    ∀ p : Bool × Bool, P p := by
  intro p
  cases p with
  | mk a b =>
    cases a with
    | false => cases b with | false => exact hff | true => exact hft
    | true  => cases b with | false => exact htf | true => exact htt

theorem strawson_russell_bivalent_collapse {M : Type u} {Γ0 : Theory M} {A B : M → Prop}
    (hPos : Entails (WithProp Γ0 A) B)
    (hNeg : Entails (WithProp Γ0 (fun m => ¬ A m)) B) :
    Entails Γ0 B := by
  intro m hΓ
  cases Classical.em (A m) with
  | inl hA => exact hPos m ⟨hΓ, hA⟩
  | inr hnA => exact hNeg m ⟨hΓ, hnA⟩

-- ============================================================================
-- 二、一.4 外延语义互斥三分与同一律回锁定理
-- ============================================================================

abbrev SemanticallyDetermined {M : Type u} (Γ : Theory M) (Q : M → Prop) : Prop :=
  Satisfiable Γ ∧ (Entails Γ Q ∨ Entails Γ (fun m => ¬ Q m))

abbrev SemanticallyUndetermined {M : Type u} (Γ : Theory M) (Q : M → Prop) : Prop :=
  Satisfiable (WithProp Γ Q) ∧ Satisfiable (WithProp Γ (fun m => ¬ Q m))

private theorem sat_not_of_not_entails {M : Type u} {Γ : Theory M} {P : M → Prop}
    (h : ¬ Entails Γ P) : Satisfiable (WithProp Γ (fun m => ¬ P m)) := by
  cases Classical.em (Satisfiable (WithProp Γ (fun m => ¬ P m))) with
  | inl hSat => exact hSat
  | inr hNoWit =>
    exact False.elim (h (fun m hΓ =>
      match Classical.em (P m) with
      | Or.inl hP => hP
      | Or.inr hnP => False.elim (hNoWit ⟨m, ⟨hΓ, hnP⟩⟩)))

private theorem sat_pos_of_not_entails_neg {M : Type u} {Γ : Theory M} {P : M → Prop}
    (h : ¬ Entails Γ (fun m => ¬ P m)) : Satisfiable (WithProp Γ P) := by
  cases sat_not_of_not_entails h with
  | intro m hm =>
    cases Classical.em (P m) with
    | inl hP => exact ⟨m, ⟨hm.1, hP⟩⟩
    | inr hnP => exact False.elim (hm.2 hnP)

theorem semantic_trichotomy_exhaustive {M : Type u} (Γ : Theory M) (Q : M → Prop) :
    Unsat Γ ∨ SemanticallyDetermined Γ Q ∨ SemanticallyUndetermined Γ Q := by
  cases Classical.em (Satisfiable Γ) with
  | inl hSat =>
    cases Classical.em (Entails Γ Q) with
    | inl hQ => exact Or.inr (Or.inl ⟨hSat, Or.inl hQ⟩)
    | inr hNotQ =>
      cases Classical.em (Entails Γ (fun m => ¬ Q m)) with
      | inl hnQ => exact Or.inr (Or.inl ⟨hSat, Or.inr hnQ⟩)
      | inr hNotnQ =>
        exact Or.inr (Or.inr ⟨sat_pos_of_not_entails_neg hNotnQ, sat_not_of_not_entails hNotQ⟩)
  | inr hNotSat =>
    exact Or.inl (fun m hΓ => hNotSat ⟨m, hΓ⟩)

theorem semantic_trichotomy_disjoint {M : Type u} (Γ : Theory M) (Q : M → Prop) :
    (¬ (Unsat Γ ∧ SemanticallyDetermined Γ Q)) ∧
    (¬ (Unsat Γ ∧ SemanticallyUndetermined Γ Q)) ∧
    (¬ (SemanticallyDetermined Γ Q ∧ SemanticallyUndetermined Γ Q)) := by
  refine ⟨?_, ?_, ?_⟩
  · intro h; cases h.2.1 with | intro m hΓ => exact h.1 m hΓ
  · intro h; cases h.2.1 with | intro m hm => exact h.1 m hm.1
  · intro h
    cases h.1.2 with
    | inl hEntQ => cases h.2.2 with | intro m hm => exact hm.2 (hEntQ m hm.1)
    | inr hEntNotQ => cases h.2.1 with | intro m hm => exact hEntNotQ m hm.1 hm.2

theorem identity_relock_theory_nonequiv {M : Type u} {Γ_orig Γ_new : Theory M}
    (hOrig : Unsat Γ_orig) (hNew : Satisfiable Γ_new) :
    ¬ (∀ m : M, Γ_orig m ↔ Γ_new m) := by
  intro hEq
  cases hNew with
  | intro m hm => exact hOrig m ((hEq m).mpr hm)

theorem definition_relaxation_violates_original {M : Type u} {Base C_orig C_new : Theory M}
    (hOrig : Unsat (Extend Base C_orig))
    (hNew : Satisfiable (Extend Base C_new)) :
    Entails (Extend Base C_new) (fun m => ¬ C_orig m) ∧
    Satisfiable (WithProp (Extend Base C_new) (fun m => ¬ C_orig m)) := by
  have hEnt : Entails (Extend Base C_new) (fun m => ¬ C_orig m) :=
    fun m hm hCo => hOrig m ⟨hm.1, hCo⟩
  cases hNew with
  | intro m hm => exact ⟨hEnt, ⟨m, ⟨hm, hEnt m hm⟩⟩⟩

-- ============================================================================
-- 三、一.8 保编号互斥七分（生成式 + 四格桥接 + 7 实例）与约束间三分并列律
-- ============================================================================

structure AdmissibleConstraintProblem (M : Type u) where
  Γ0 : Theory M
  A : M → Prop
  T : M → Prop
  satA : Satisfiable (WithProp Γ0 A)
  satNotA : Satisfiable (WithProp Γ0 (fun m => ¬ A m))
  satT : Satisfiable (WithProp Γ0 T)
  satNotT : Satisfiable (WithProp Γ0 (fun m => ¬ T m))

theorem admissibleConstraintProblem_nonempty :
    Nonempty (AdmissibleConstraintProblem (Bool × Bool)) :=
  ⟨{
    Γ0 := fun _ => True
    A := fun p => p.1 = true
    T := fun p => p.2 = true
    satA := ⟨(true, true), by decide⟩
    satNotA := ⟨(false, true), by decide⟩
    satT := ⟨(true, true), by decide⟩
    satNotT := ⟨(true, false), by decide⟩
  }⟩

theorem disjunctive_guard_not_entails_domain :
    ∃ P : AdmissibleConstraintProblem (Bool × Bool),
      (∀ p, P.A p ↔ (p.1 = true ∨ (P.T p ∧ p.1 = false))) ∧
      ¬ Entails (WithProp P.Γ0 P.A) P.T :=
  ⟨{
    Γ0 := fun _ => True
    A := fun p => p.1 = true ∨ (p.2 = true ∧ p.1 = false)
    T := fun p => p.2 = true
    satA := ⟨(true, false), by decide⟩
    satNotA := ⟨(false, false), by decide⟩
    satT := ⟨(false, true), by decide⟩
    satNotT := ⟨(true, false), by decide⟩
  }, ⟨fun _ => Iff.rfl, fun h => Bool.noConfusion (h (true, false) (by decide))⟩⟩

inductive BranchSign : Type where
  | pos | neg | undet

theorem branchSign_nonempty : Nonempty BranchSign := ⟨BranchSign.pos⟩

abbrev BranchHolds {M : Type u} (Δ : Theory M) (T : M → Prop) : BranchSign → Prop
  | .pos   => Entails Δ T
  | .neg   => Entails Δ (fun m => ¬ T m)
  | .undet => SemanticallyUndetermined Δ T

inductive ObjectiveRole : Type where
  | iff
  | sufficientOnly
  | necessaryOnly
  | vetoOnly
  | complementaryVeto
  | independent
  | negSufficient

theorem objectiveRole_nonempty : Nonempty ObjectiveRole := ⟨ObjectiveRole.iff⟩

def roleSigns : ObjectiveRole → BranchSign × BranchSign
  | .iff               => (.pos, .neg)
  | .sufficientOnly    => (.pos, .undet)
  | .necessaryOnly     => (.undet, .neg)
  | .vetoOnly          => (.neg, .undet)
  | .complementaryVeto => (.neg, .pos)
  | .independent       => (.undet, .undet)
  | .negSufficient     => (.undet, .pos)

abbrev HasRole {M : Type u} (P : AdmissibleConstraintProblem M) (r : ObjectiveRole) : Prop :=
  BranchHolds (WithProp P.Γ0 P.A) P.T (roleSigns r).1 ∧
  BranchHolds (WithProp P.Γ0 (fun m => ¬ P.A m)) P.T (roleSigns r).2

theorem entails_neg_iff_contrapositive {M : Type u} (Γ0 : Theory M) (A T : M → Prop) :
    Entails (WithProp Γ0 (fun m => ¬ A m)) (fun m => ¬ T m) ↔
    Entails (WithProp Γ0 T) A := by
  refine ⟨?_, ?_⟩
  · intro hn m hm
    cases Classical.em (A m) with
    | inl hA => exact hA
    | inr hnA => exact False.elim (hn m ⟨hm.1, hnA⟩ hm.2)
  · intro hTA m hm hT
    exact hm.2 (hTA m ⟨hm.1, hT⟩)

theorem branch_pos_iff_cells {M : Type u} {Δ : Theory M} (hSat : Satisfiable Δ) (T : M → Prop) :
    BranchHolds Δ T .pos ↔
      (Satisfiable (WithProp Δ T) ∧ ¬ Satisfiable (WithProp Δ (fun m => ¬ T m))) := by
  refine ⟨?_, ?_⟩
  · intro hPos
    cases hSat with
    | intro m hΔ =>
      exact ⟨⟨m, ⟨hΔ, hPos m hΔ⟩⟩, fun ⟨m', hm'⟩ => hm'.2 (hPos m' hm'.1)⟩
  · intro hCells m hΔ
    cases Classical.em (T m) with
    | inl hT => exact hT
    | inr hnT => exact False.elim (hCells.2 ⟨m, ⟨hΔ, hnT⟩⟩)

theorem branch_neg_iff_cells {M : Type u} {Δ : Theory M} (hSat : Satisfiable Δ) (T : M → Prop) :
    BranchHolds Δ T .neg ↔
      (¬ Satisfiable (WithProp Δ T) ∧ Satisfiable (WithProp Δ (fun m => ¬ T m))) := by
  refine ⟨?_, ?_⟩
  · intro hNeg
    cases hSat with
    | intro m hΔ =>
      exact ⟨fun ⟨m', hm'⟩ => hNeg m' hm'.1 hm'.2, ⟨m, ⟨hΔ, hNeg m hΔ⟩⟩⟩
  · intro hCells m hΔ hT
    exact hCells.1 ⟨m, ⟨hΔ, hT⟩⟩

private theorem branch_trichotomy {M : Type u} {Δ : Theory M} (hSat : Satisfiable Δ) (T : M → Prop) :
    BranchHolds Δ T .pos ∨ BranchHolds Δ T .neg ∨ BranchHolds Δ T .undet := by
  cases semantic_trichotomy_exhaustive Δ T with
  | inl hUnsat => cases hSat with | intro m hΔ => exact False.elim (hUnsat m hΔ)
  | inr hRest =>
    cases hRest with
    | inl hDet =>
      cases hDet.2 with
      | inl hPos => exact Or.inl hPos
      | inr hNeg => exact Or.inr (Or.inl hNeg)
    | inr hUndet => exact Or.inr (Or.inr hUndet)

private theorem branchHolds_unique {M : Type u} {Δ : Theory M} {T : M → Prop}
    (hSat : Satisfiable Δ) {s1 s2 : BranchSign}
    (h1 : BranchHolds Δ T s1) (h2 : BranchHolds Δ T s2) : s1 = s2 := by
  cases s1 with
  | pos =>
    cases s2 with
    | pos => rfl
    | neg => cases hSat with | intro m hΔ => exact False.elim (h2 m hΔ (h1 m hΔ))
    | undet => cases h2.2 with | intro m hm => exact False.elim (hm.2 (h1 m hm.1))
  | neg =>
    cases s2 with
    | pos => cases hSat with | intro m hΔ => exact False.elim (h1 m hΔ (h2 m hΔ))
    | neg => rfl
    | undet => cases h2.1 with | intro m hm => exact False.elim (h1 m hm.1 hm.2)
  | undet =>
    cases s2 with
    | pos => cases h1.2 with | intro m hm => exact False.elim (hm.2 (h2 m hm.1))
    | neg => cases h1.1 with | intro m hm => exact False.elim (h2 m hm.1 hm.2)
    | undet => rfl

private def signPairDecode : BranchSign × BranchSign → ObjectiveRole
  | (.pos, .neg)     => .iff
  | (.pos, .undet)   => .sufficientOnly
  | (.undet, .neg)   => .necessaryOnly
  | (.neg, .undet)   => .vetoOnly
  | (.neg, .pos)     => .complementaryVeto
  | (.undet, .undet) => .independent
  | (.undet, .pos)   => .negSufficient
  | (.pos, .pos)     => .iff
  | (.neg, .neg)     => .iff

private theorem decode_roleSigns (r : ObjectiveRole) : signPairDecode (roleSigns r) = r := by
  cases r with
  | iff               => rfl
  | sufficientOnly    => rfl
  | necessaryOnly     => rfl
  | vetoOnly          => rfl
  | complementaryVeto => rfl
  | independent       => rfl
  | negSufficient     => rfl

private theorem prod_eq {α : Type u} {β : Type v} {p q : α × β}
    (h1 : p.1 = q.1) (h2 : p.2 = q.2) : p = q := by
  cases p with
  | mk a1 b1 =>
    cases q with
    | mk a2 b2 =>
      change a1 = a2 at h1
      change b1 = b2 at h2
      rw [h1, h2]

theorem constraint_seven_way_exhaustive {M : Type u} (P : AdmissibleConstraintProblem M) :
    ∃ r : ObjectiveRole, HasRole P r := by
  cases branch_trichotomy P.satA P.T with
  | inl hAp =>
    cases branch_trichotomy P.satNotA P.T with
    | inl hnAp =>
      cases P.satNotT with
      | intro m hm =>
        cases Classical.em (P.A m) with
        | inl hA => exact False.elim (hm.2 (hAp m ⟨hm.1, hA⟩))
        | inr hnA => exact False.elim (hm.2 (hnAp m ⟨hm.1, hnA⟩))
    | inr hRest =>
      cases hRest with
      | inl hnAn => exact ⟨.iff, ⟨hAp, hnAn⟩⟩
      | inr hnAu => exact ⟨.sufficientOnly, ⟨hAp, hnAu⟩⟩
  | inr hRestA =>
    cases hRestA with
    | inl hAn =>
      cases branch_trichotomy P.satNotA P.T with
      | inl hnAp => exact ⟨.complementaryVeto, ⟨hAn, hnAp⟩⟩
      | inr hRest =>
        cases hRest with
        | inl hnAn =>
          cases P.satT with
          | intro m hm =>
            cases Classical.em (P.A m) with
            | inl hA => exact False.elim (hAn m ⟨hm.1, hA⟩ hm.2)
            | inr hnA => exact False.elim (hnAn m ⟨hm.1, hnA⟩ hm.2)
        | inr hnAu => exact ⟨.vetoOnly, ⟨hAn, hnAu⟩⟩
    | inr hAu =>
      cases branch_trichotomy P.satNotA P.T with
      | inl hnAp => exact ⟨.negSufficient, ⟨hAu, hnAp⟩⟩
      | inr hRest =>
        cases hRest with
        | inl hnAn => exact ⟨.necessaryOnly, ⟨hAu, hnAn⟩⟩
        | inr hnAu => exact ⟨.independent, ⟨hAu, hnAu⟩⟩

theorem constraint_role_unique {M : Type u} (P : AdmissibleConstraintProblem M)
    {r1 r2 : ObjectiveRole} (h1 : HasRole P r1) (h2 : HasRole P r2) : r1 = r2 := by
  have hp : roleSigns r1 = roleSigns r2 :=
    prod_eq (branchHolds_unique P.satA h1.1 h2.1) (branchHolds_unique P.satNotA h1.2 h2.2)
  have hdec : signPairDecode (roleSigns r1) = signPairDecode (roleSigns r2) :=
    congrArg signPairDecode hp
  rw [decode_roleSigns, decode_roleSigns] at hdec
  exact hdec

def validOccupancy (c11 c10 c01 c00 : Bool) : Bool :=
  (c11 || c10) && (c01 || c00) && (c11 || c01) && (c10 || c00)

theorem validOccupancy_iff_seven_patterns (c11 c10 c01 c00 : Bool) :
    validOccupancy c11 c10 c01 c00 = true ↔
      (c11, c10, c01, c00) = (true, false, false, true) ∨
      (c11, c10, c01, c00) = (true, false, true, true) ∨
      (c11, c10, c01, c00) = (true, true, false, true) ∨
      (c11, c10, c01, c00) = (false, true, true, true) ∨
      (c11, c10, c01, c00) = (false, true, true, false) ∨
      (c11, c10, c01, c00) = (true, true, true, true) ∨
      (c11, c10, c01, c00) = (true, true, true, false) := by
  cases c11 <;> cases c10 <;> cases c01 <;> cases c00 <;> decide

theorem exists_role_iff : ∃ P : AdmissibleConstraintProblem (Bool × Bool), HasRole P .iff :=
  ⟨{ Γ0 := fun p => p.1 = p.2, A := fun p => p.1 = true, T := fun p => p.2 = true,
     satA := ⟨(true, true), by decide⟩, satNotA := ⟨(false, false), by decide⟩,
     satT := ⟨(true, true), by decide⟩, satNotT := ⟨(false, false), by decide⟩ },
   ⟨bool2_forall (by decide) (by decide) (by decide) (by decide),
    bool2_forall (by decide) (by decide) (by decide) (by decide)⟩⟩

theorem exists_role_sufficientOnly : ∃ P : AdmissibleConstraintProblem (Bool × Bool), HasRole P .sufficientOnly :=
  ⟨{ Γ0 := fun p => p.1 = false ∨ p.2 = true, A := fun p => p.1 = true, T := fun p => p.2 = true,
     satA := ⟨(true, true), by decide⟩, satNotA := ⟨(false, true), by decide⟩,
     satT := ⟨(true, true), by decide⟩, satNotT := ⟨(false, false), by decide⟩ },
   ⟨bool2_forall (by decide) (by decide) (by decide) (by decide),
    ⟨⟨(false, true), by decide⟩, ⟨(false, false), by decide⟩⟩⟩⟩

theorem exists_role_necessaryOnly : ∃ P : AdmissibleConstraintProblem (Bool × Bool), HasRole P .necessaryOnly :=
  ⟨{ Γ0 := fun p => p.1 = true ∨ p.2 = false, A := fun p => p.1 = true, T := fun p => p.2 = true,
     satA := ⟨(true, true), by decide⟩, satNotA := ⟨(false, false), by decide⟩,
     satT := ⟨(true, true), by decide⟩, satNotT := ⟨(true, false), by decide⟩ },
   ⟨⟨⟨(true, true), by decide⟩, ⟨(true, false), by decide⟩⟩,
    bool2_forall (by decide) (by decide) (by decide) (by decide)⟩⟩

theorem exists_role_vetoOnly : ∃ P : AdmissibleConstraintProblem (Bool × Bool), HasRole P .vetoOnly :=
  ⟨{ Γ0 := fun p => p.1 = false ∨ p.2 = false, A := fun p => p.1 = true, T := fun p => p.2 = true,
     satA := ⟨(true, false), by decide⟩, satNotA := ⟨(false, true), by decide⟩,
     satT := ⟨(false, true), by decide⟩, satNotT := ⟨(true, false), by decide⟩ },
   ⟨bool2_forall (by decide) (by decide) (by decide) (by decide),
    ⟨⟨(false, true), by decide⟩, ⟨(false, false), by decide⟩⟩⟩⟩

theorem exists_role_complementaryVeto : ∃ P : AdmissibleConstraintProblem (Bool × Bool), HasRole P .complementaryVeto :=
  ⟨{ Γ0 := fun p => p.1 ≠ p.2, A := fun p => p.1 = true, T := fun p => p.2 = true,
     satA := ⟨(true, false), by decide⟩, satNotA := ⟨(false, true), by decide⟩,
     satT := ⟨(false, true), by decide⟩, satNotT := ⟨(true, false), by decide⟩ },
   ⟨bool2_forall (by decide) (by decide) (by decide) (by decide),
    bool2_forall (by decide) (by decide) (by decide) (by decide)⟩⟩

theorem exists_role_independent : ∃ P : AdmissibleConstraintProblem (Bool × Bool), HasRole P .independent :=
  ⟨{ Γ0 := fun _ => True, A := fun p => p.1 = true, T := fun p => p.2 = true,
     satA := ⟨(true, true), by decide⟩, satNotA := ⟨(false, true), by decide⟩,
     satT := ⟨(true, true), by decide⟩, satNotT := ⟨(true, false), by decide⟩ },
   ⟨⟨⟨(true, true), by decide⟩, ⟨(true, false), by decide⟩⟩,
    ⟨⟨(false, true), by decide⟩, ⟨(false, false), by decide⟩⟩⟩⟩

theorem exists_role_negSufficient : ∃ P : AdmissibleConstraintProblem (Bool × Bool), HasRole P .negSufficient :=
  ⟨{ Γ0 := fun p => p.1 = true ∨ p.2 = true, A := fun p => p.1 = true, T := fun p => p.2 = true,
     satA := ⟨(true, true), by decide⟩, satNotA := ⟨(false, true), by decide⟩,
     satT := ⟨(true, true), by decide⟩, satNotT := ⟨(true, false), by decide⟩ },
   ⟨⟨⟨(true, true), by decide⟩, ⟨(true, false), by decide⟩⟩,
    bool2_forall (by decide) (by decide) (by decide) (by decide)⟩⟩

structure AdmissiblePairProblem (M : Type u) where
  Γ0 : Theory M
  A : M → Prop
  B : M → Prop
  satA : Satisfiable (WithProp Γ0 A)
  satNotA : Satisfiable (WithProp Γ0 (fun m => ¬ A m))
  satB : Satisfiable (WithProp Γ0 B)
  satNotB : Satisfiable (WithProp Γ0 (fun m => ¬ B m))

theorem admissiblePairProblem_nonempty :
    Nonempty (AdmissiblePairProblem (Bool × Bool)) :=
  ⟨{ Γ0 := fun _ => True, A := fun p => p.1 = true, B := fun p => p.2 = true,
     satA := ⟨(true, true), by decide⟩, satNotA := ⟨(false, true), by decide⟩,
     satB := ⟨(true, true), by decide⟩, satNotB := ⟨(true, false), by decide⟩ }⟩

abbrev PairSubordinate {M : Type u} (Γ0 : Theory M) (A B : M → Prop) : Prop :=
  Entails (WithProp Γ0 A) B ∨ Entails (WithProp Γ0 B) A

abbrev PairMutuallyExclusive {M : Type u} (Γ0 : Theory M) (A B : M → Prop) : Prop :=
  Unsat (WithProp (WithProp Γ0 A) B)

abbrev PairJuxtaposed {M : Type u} (Γ0 : Theory M) (A B : M → Prop) : Prop :=
  ¬ Entails (WithProp Γ0 A) B ∧
  ¬ Entails (WithProp Γ0 B) A ∧
  Satisfiable (WithProp (WithProp Γ0 A) B)

theorem pair_three_way_exhaustive {M : Type u} (P : AdmissiblePairProblem M) :
    PairSubordinate P.Γ0 P.A P.B ∨
    PairMutuallyExclusive P.Γ0 P.A P.B ∨
    PairJuxtaposed P.Γ0 P.A P.B := by
  cases Classical.em (Entails (WithProp P.Γ0 P.A) P.B) with
  | inl hAB => exact Or.inl (Or.inl hAB)
  | inr hnAB =>
    cases Classical.em (Entails (WithProp P.Γ0 P.B) P.A) with
    | inl hBA => exact Or.inl (Or.inr hBA)
    | inr hnBA =>
      cases Classical.em (Satisfiable (WithProp (WithProp P.Γ0 P.A) P.B)) with
      | inl hSat => exact Or.inr (Or.inr ⟨hnAB, hnBA, hSat⟩)
      | inr hnSat => exact Or.inr (Or.inl (fun m hm => hnSat ⟨m, hm⟩))

theorem pair_three_way_disjoint {M : Type u} (P : AdmissiblePairProblem M) :
    (¬ (PairSubordinate P.Γ0 P.A P.B ∧ PairMutuallyExclusive P.Γ0 P.A P.B)) ∧
    (¬ (PairSubordinate P.Γ0 P.A P.B ∧ PairJuxtaposed P.Γ0 P.A P.B)) ∧
    (¬ (PairMutuallyExclusive P.Γ0 P.A P.B ∧ PairJuxtaposed P.Γ0 P.A P.B)) := by
  refine ⟨?_, ?_, ?_⟩
  · intro h
    cases h.1 with
    | inl hAB => cases P.satA with | intro m hm => exact h.2 m ⟨hm, hAB m hm⟩
    | inr hBA => cases P.satB with | intro m hm => exact h.2 m ⟨⟨hm.1, hBA m hm⟩, hm.2⟩
  · intro h
    cases h.1 with
    | inl hAB => exact h.2.1 hAB
    | inr hBA => exact h.2.2.1 hBA
  · intro h
    cases h.2.2.2 with | intro m hm => exact h.1 m hm

-- ============================================================================
-- 四、一.9 单调层级链截断、多冲突核命中必要性与防消症/菱形合取反例
-- ============================================================================

theorem unsat_monotone_superset {M : Type u} {Γ Δ : Theory M}
    (hRoot : Unsat Γ) : Unsat (Extend Γ Δ) :=
  fun m hm => hRoot m hm.1

structure MonotonePremiseChain (M : Type u) where
  level : Nat → Theory M
  step_mono : ∀ {i : Nat} {m : M}, level (i + 1) m → level i m

theorem monotonePremiseChain_nonempty : Nonempty (MonotonePremiseChain Bool) :=
  ⟨{ level := fun _ _ => True, step_mono := fun h => h }⟩

theorem chain_model_antitone {M : Type u} (C : MonotonePremiseChain M)
    {i j : Nat} (hij : i ≤ j) {m : M} (hm : C.level j m) : C.level i m := by
  induction hij with
  | refl => exact hm
  | step _ ih => exact ih (C.step_mono hm)

abbrev IsEarliestRootConflict {M : Type u} (C : MonotonePremiseChain M) (r : Nat) : Prop :=
  (∀ j : Nat, j < r → Satisfiable (C.level j)) ∧ Unsat (C.level r)

private theorem nat_zero_lt_succ (n : Nat) : 0 < Nat.succ n := by
  induction n with
  | zero => exact Nat.le.refl
  | succ _ ih => exact Nat.le.step ih

private theorem nat_succ_lt_succ {a b : Nat} (h : a < b) : Nat.succ a < Nat.succ b := by
  induction h with
  | refl => exact Nat.le.refl
  | step _ ih => exact Nat.le.step ih

private theorem nat_trichotomy (a : Nat) : ∀ b : Nat, a < b ∨ a = b ∨ b < a := by
  induction a with
  | zero =>
    intro b
    cases b with
    | zero => exact Or.inr (Or.inl rfl)
    | succ k => exact Or.inl (nat_zero_lt_succ k)
  | succ k ih =>
    intro b
    cases b with
    | zero => exact Or.inr (Or.inr (nat_zero_lt_succ k))
    | succ m =>
      cases ih m with
      | inl hlt => exact Or.inl (nat_succ_lt_succ hlt)
      | inr hrest =>
        cases hrest with
        | inl heq => exact Or.inr (Or.inl (congrArg Nat.succ heq))
        | inr hgt => exact Or.inr (Or.inr (nat_succ_lt_succ hgt))

theorem earliest_root_conflict_unique {M : Type u} (C : MonotonePremiseChain M)
    {r1 r2 : Nat} (h1 : IsEarliestRootConflict C r1) (h2 : IsEarliestRootConflict C r2) :
    r1 = r2 := by
  cases nat_trichotomy r1 r2 with
  | inl hlt => cases h2.1 r1 hlt with | intro m hm => exact False.elim (h1.2 m hm)
  | inr hrest =>
    cases hrest with
    | inl heq => exact heq
    | inr hgt => cases h1.1 r2 hgt with | intro m hm => exact False.elim (h2.2 m hm)

private theorem nat_lt_succ_cases {j k : Nat} (h : j < Nat.succ k) : j < k ∨ j = k := by
  have hle : j ≤ k := Nat.le_of_succ_le_succ h
  cases nat_trichotomy j k with
  | inl hlt => exact Or.inl hlt
  | inr hrest =>
    cases hrest with
    | inl heq => exact Or.inr heq
    | inr hgt => exact False.elim (Nat.not_lt.mpr hle hgt)

theorem exists_earliest_root_conflict {M : Type u} (C : MonotonePremiseChain M) :
    ∀ n : Nat, Unsat (C.level n) → ∃ r : Nat, r ≤ n ∧ IsEarliestRootConflict C r := by
  intro n
  induction n with
  | zero =>
    intro h0
    exact ⟨0, Nat.le.refl, ⟨fun j hj => False.elim (Nat.not_lt_zero j hj), h0⟩⟩
  | succ k ih =>
    intro hSk
    cases Classical.em (Satisfiable (C.level k)) with
    | inl hSatK =>
      refine ⟨Nat.succ k, Nat.le.refl, ⟨?_, hSk⟩⟩
      intro j hj
      cases nat_lt_succ_cases hj with
      | inl hlt =>
        cases hSatK with
        | intro m hm => exact ⟨m, chain_model_antitone C (Nat.le_of_lt hlt) hm⟩
      | inr heq =>
        rw [heq]
        exact hSatK
    | inr hNotSatK =>
      have hUnsatK : Unsat (C.level k) := fun m hm => hNotSatK ⟨m, hm⟩
      cases ih hUnsatK with
      | intro r hr =>
        exact ⟨r, Nat.le.step hr.1, hr.2⟩

theorem root_conflict_propagates_all_downstream {M : Type u} (C : MonotonePremiseChain M)
    {r n : Nat} (hRoot : IsEarliestRootConflict C r) (hrn : r ≤ n) :
    Unsat (C.level n) :=
  fun m hm => hRoot.2 m (chain_model_antitone C hrn hm)

theorem surviving_unsat_core_blocks_repair {M : Type u} {K1 Γ' : Theory M}
    (hCore1 : Unsat K1) (hRetains1 : Entails Γ' K1) : Unsat Γ' :=
  fun m h => hCore1 m (hRetains1 m h)

abbrev ConjoinMods {Idx : Type u} {M : Type v} (Mod : Idx → Theory M) : List Idx → Theory M
  | [] => fun _ => True
  | i :: rest => Extend (Mod i) (ConjoinMods Mod rest)

abbrev KeptTheory {Idx : Type u} {M : Type v} (Mod : Idx → Theory M) (Keep : Idx → Prop) : Theory M :=
  fun m => ∀ i, Keep i → Mod i m

private theorem list_mem_of_cons_tail {α : Type u} {a x : α} {xs : List α}
    (h : x ∈ xs) : x ∈ a :: xs :=
  List.Mem.tail a h

private theorem kept_entails_conjoin_core {Idx : Type u} {M : Type v}
    (Mod : Idx → Theory M) (Keep : Idx → Prop) :
    ∀ core : List Idx, (∀ i, i ∈ core → Keep i) → Entails (KeptTheory Mod Keep) (ConjoinMods Mod core) := by
  intro core
  induction core with
  | nil =>
    intro _ _ _
    exact trivial
  | cons a rest ih =>
    intro hAll m hm
    refine ⟨hm a (hAll a (List.Mem.head rest)), ?_⟩
    exact ih (fun i hi => hAll i (list_mem_of_cons_tail hi)) m hm

theorem finite_module_repair_must_hit_core {Idx : Type u} {M : Type v}
    (Mod : Idx → Theory M) (Keep : Idx → Prop) (core : List Idx)
    (hCoreUnsat : Unsat (ConjoinMods Mod core))
    (hSatRepair : Satisfiable (KeptTheory Mod Keep)) :
    ¬ (∀ i, i ∈ core → Keep i) := by
  intro hAll
  have hEnt := kept_entails_conjoin_core Mod Keep core hAll
  cases hSatRepair with
  | intro m hm => exact hCoreUnsat m (hEnt m hm)

theorem finite_module_repair_drops_core_member {Idx : Type u} {M : Type v}
    (Mod : Idx → Theory M) (Keep : Idx → Prop) (core : List Idx)
    (hCoreUnsat : Unsat (ConjoinMods Mod core))
    (hSatRepair : Satisfiable (KeptTheory Mod Keep)) :
    ∃ i : Idx, i ∈ core ∧ ¬ Keep i := by
  cases Classical.em (∃ i : Idx, i ∈ core ∧ ¬ Keep i) with
  | inl hEx => exact hEx
  | inr hNo =>
    refine False.elim (finite_module_repair_must_hit_core Mod Keep core hCoreUnsat hSatRepair ?_)
    intro i hi
    cases Classical.em (Keep i) with
    | inl hK => exact hK
    | inr hnK => exact False.elim (hNo ⟨i, hi, hnK⟩)

theorem branch_symptom_erasure_counterexample :
    ∃ (Γ_root Γ_sub Δ1 Δ2 : Theory (Bool × Bool)),
      Unsat Γ_root ∧
      Satisfiable Γ_sub ∧
      Unsat (Extend Γ_sub Δ1) ∧
      Satisfiable (Extend Γ_sub Δ2) ∧
      Unsat (Extend Γ_root Δ1) ∧
      Unsat (Extend Γ_root Δ2) :=
  ⟨fun _ => False,
   fun p => p.1 = true,
   fun p => p.1 = false,
   fun p => p.2 = true,
   ⟨fun _ h => h,
    ⟨(true, true), by decide⟩,
    bool2_forall (by decide) (by decide) (by decide) (by decide),
    ⟨(true, true), by decide⟩,
    fun _ h => h.1,
    fun _ h => h.1⟩⟩

theorem diamond_joint_conflict_no_single_bad_node :
    ∃ (Γ0 Λ1 Λ2 : Theory Bool),
      Satisfiable Γ0 ∧
      Satisfiable (Extend Γ0 Λ1) ∧
      Satisfiable (Extend Γ0 Λ2) ∧
      Unsat (Extend (Extend Γ0 Λ1) Λ2) :=
  ⟨fun _ => True,
   fun b => b = true,
   fun b => b = false,
   ⟨⟨true, trivial⟩,
    ⟨true, by decide⟩,
    ⟨false, by decide⟩,
    fun b h => by cases b with | false => exact Bool.noConfusion h.1.2 | true => exact Bool.noConfusion h.2⟩⟩

-- ============================================================================
-- 五、纯映射纤维引理（支撑三.4 非单射逆推与四.2/四.4 同源表征不可分性，零经验公理）
-- ============================================================================

theorem noninjective_map_no_left_inverse_on_fiber {X : Type u} {R : Type v}
    (r : X → R) (x1 x2 : X) (hFiber : r x1 = r x2) (hNe : x1 ≠ x2) :
    ∀ g : R → X, ¬ (g (r x1) = x1 ∧ g (r x2) = x2) := by
  intro g h
  have hEq : x1 = x2 := by
    rw [← h.1, hFiber, h.2]
  exact hNe hEq

theorem same_repr_checker_cannot_separate_mixed_fiber {X : Type u} {R : Type v}
    (r : X → R) (T : X → Prop)
    (x1 x2 : X) (hFiber : r x1 = r x2) (hNotT2 : ¬ T x2)
    (E' : X → Prop) (hFact : ∃ Ehat : R → Prop, ∀ x, E' x ↔ Ehat (r x)) :
    ¬ (E' x1 ∧ ∀ x, E' x → T x) := by
  intro h
  cases hFact with
  | intro Ehat hE =>
    have hEhat1 : Ehat (r x1) := (hE x1).mp h.1
    rw [hFiber] at hEhat1
    have hE2 : E' x2 := (hE x2).mpr hEhat1
    exact hNotT2 (h.2 x2 hE2)

end CaeAxioms.LogicCore
