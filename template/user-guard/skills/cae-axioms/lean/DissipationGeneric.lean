-- Copyright 2026 Xinyu Li (Xinyu-Li-1997)
-- SPDX-License-Identifier: Apache-2.0
/-
  DissipationGeneric.lean
  层 2M（正测度理性力学与热力学公理底座 · 第三节不可逆耗散与双退化正交）零 Mathlib 纯核 Lean 4 骨架：
  - 非退化有序交换环规格 RingSpec（排除 0 ≤ -1 与单点零环退化序模型）
  - ① 偶宇称反对称矩阵二次型恒为零（含非零矩阵作用而二次型为零的显式见证）
  - ② 正测度相邻控制体有限差交换流非负熵产（含全局对称半正定算子条件与单向求值引理）
  - ③a 给定动力学分解后的逐态配对引理 GenericSystem（dE/dt = 0 且 dS/dt >= 0）
  - ③b 算子级 GENERIC 双退化公设 GenericOperatorSystem（L ∇S = 0, M ∇E = 0 导出逐态守恒与熵增）
       并附三维双活跃非平凡构造见证（R_rev ≠ 0 ∧ D_irr ≠ 0 ∧ dE/dt = 0 ∧ dS/dt = 1 > 0）
-/

namespace CaeAxioms.DissipationGeneric

/-- 自包含非退化有序交换环规格（排除 0 ≤ -1 与 0 < 0 退化序模型）。 -/
structure RingSpec where
  Carrier : Type
  zero : Carrier
  one : Carrier
  add : Carrier → Carrier → Carrier
  neg : Carrier → Carrier
  mul : Carrier → Carrier → Carrier
  lt : Carrier → Carrier → Prop
  le : Carrier → Carrier → Prop
  add_comm : ∀ a b, add a b = add b a
  add_assoc : ∀ a b c, add (add a b) c = add a (add b c)
  zero_add : ∀ a, add zero a = a
  add_neg : ∀ a, add a (neg a) = zero
  mul_comm : ∀ a b, mul a b = mul b a
  mul_assoc : ∀ a b c, mul (mul a b) c = mul a (mul b c)
  one_mul : ∀ a, mul one a = a
  mul_add : ∀ a b c, mul a (add b c) = add (mul a b) (mul a c)
  le_refl : ∀ a, le a a
  le_trans : ∀ a b c, le a b → le b c → le a c
  lt_trans : ∀ a b c, lt a b → lt b c → lt a c
  le_of_lt : ∀ a b, lt a b → le a b
  lt_not_le : ∀ a b, lt a b → ¬ le b a
  add_lt_add_left : ∀ a b c, lt a b → lt (add c a) (add c b)
  zero_lt_one : lt zero one
  mul_pos : ∀ a b, lt zero a → lt zero b → lt zero (mul a b)

private theorem int_mul_pos_core (a b : Int) (ha : (0 : Int) < a) (hb : (0 : Int) < b) :
    (0 : Int) < a * b :=
  Int.mul_pos ha hb

abbrev intRing : RingSpec where
  Carrier := Int
  zero := 0
  one := 1
  add := Int.add
  neg := Int.neg
  mul := Int.mul
  lt := (· < ·)
  le := (· ≤ ·)
  add_comm := Int.add_comm
  add_assoc := Int.add_assoc
  zero_add := Int.zero_add
  add_neg := Int.add_right_neg
  mul_comm := Int.mul_comm
  mul_assoc := Int.mul_assoc
  one_mul := Int.one_mul
  mul_add := Int.mul_add
  le_refl := Int.le_refl
  le_trans := fun _ _ _ => Int.le_trans
  lt_trans := fun _ _ _ => Int.lt_trans
  le_of_lt := fun _ _ => Int.le_of_lt
  lt_not_le := fun _ _ h1 h2 => Int.not_le.mpr h1 h2
  add_lt_add_left := fun _ _ c h => Int.add_lt_add_left h c
  zero_lt_one := by decide
  mul_pos := int_mul_pos_core

theorem ringSpec_nonempty : Nonempty RingSpec :=
  ⟨intRing⟩

theorem lt_irrefl (R : RingSpec) (a : R.Carrier) : ¬ R.lt a a :=
  fun h => R.lt_not_le a a h (R.le_refl a)

theorem lt_ne (R : RingSpec) (a b : R.Carrier) (h : R.lt a b) : a ≠ b := by
  intro heq
  rw [heq] at h
  exact lt_irrefl R b h

theorem zero_ne_one (R : RingSpec) : R.zero ≠ R.one :=
  lt_ne R R.zero R.one R.zero_lt_one

theorem add_zero (R : RingSpec) (a : R.Carrier) : R.add a R.zero = a := by
  rw [R.add_comm, R.zero_add]

theorem neg_add (R : RingSpec) (a : R.Carrier) : R.add (R.neg a) a = R.zero := by
  rw [R.add_comm, R.add_neg]

theorem not_le_neg_one (R : RingSpec) : ¬ R.le R.zero (R.neg R.one) := by
  have hneg : R.lt (R.neg R.one) R.zero := by
    have h := R.add_lt_add_left R.zero R.one (R.neg R.one) R.zero_lt_one
    rw [add_zero, neg_add] at h
    exact h
  exact R.lt_not_le (R.neg R.one) R.zero hneg

theorem add_mul (R : RingSpec) (a b c : R.Carrier) :
    R.mul (R.add a b) c = R.add (R.mul a c) (R.mul b c) := by
  rw [R.mul_comm, R.mul_add, R.mul_comm c a, R.mul_comm c b]

theorem mul_zero (R : RingSpec) (a : R.Carrier) : R.mul a R.zero = R.zero := by
  have h1 : R.add (R.mul a R.zero) (R.mul a R.zero) = R.mul a R.zero := by
    rw [← R.mul_add, R.zero_add]
  have h2 := congrArg (fun t => R.add t (R.neg (R.mul a R.zero))) h1
  rw [R.add_assoc, R.add_neg, add_zero] at h2
  exact h2

theorem zero_mul (R : RingSpec) (a : R.Carrier) : R.mul R.zero a = R.zero := by
  rw [R.mul_comm, mul_zero]

/-- 二维向量与矩阵二次型（用于精确展示反对称零二次型与有限差对称/反对称分解）。 -/
structure Vec2 (R : RingSpec) where
  x1 : R.Carrier
  x2 : R.Carrier

theorem vec2_nonempty : Nonempty (Vec2 intRing) :=
  ⟨{ x1 := 1, x2 := 0 }⟩

structure Mat2 (R : RingSpec) where
  m11 : R.Carrier
  m12 : R.Carrier
  m21 : R.Carrier
  m22 : R.Carrier

theorem mat2_nonempty : Nonempty (Mat2 intRing) :=
  ⟨{ m11 := 1, m12 := 0, m21 := 0, m22 := 1 }⟩

def vecSub2 (R : RingSpec) (a b : Vec2 R) : Vec2 R where
  x1 := R.add a.x1 (R.neg b.x1)
  x2 := R.add a.x2 (R.neg b.x2)

def inner2 (R : RingSpec) (u v : Vec2 R) : R.Carrier :=
  R.add (R.mul u.x1 v.x1) (R.mul u.x2 v.x2)

def matVec2 (R : RingSpec) (A : Mat2 R) (v : Vec2 R) : Vec2 R where
  x1 := R.add (R.mul A.m11 v.x1) (R.mul A.m12 v.x2)
  x2 := R.add (R.mul A.m21 v.x1) (R.mul A.m22 v.x2)

def matAdd2 (R : RingSpec) (A B : Mat2 R) : Mat2 R where
  m11 := R.add A.m11 B.m11
  m12 := R.add A.m12 B.m12
  m21 := R.add A.m21 B.m21
  m22 := R.add A.m22 B.m22

def quadForm2 (R : RingSpec) (A : Mat2 R) (v : Vec2 R) : R.Carrier :=
  inner2 R v (matVec2 R A v)

/-- ①a 机证定理（反对称矩阵零二次型）：对任意零对角反对称二维矩阵 A，vᵀ A v = 0。 -/
theorem antisym_quadratic_zero (R : RingSpec) (A : Mat2 R) (v : Vec2 R)
    (h11 : A.m11 = R.zero) (h22 : A.m22 = R.zero) (hSkew : R.add A.m12 A.m21 = R.zero) :
    quadForm2 R A v = R.zero := by
  unfold quadForm2 inner2 matVec2
  rw [h11, h22, zero_mul, zero_mul, R.zero_add, add_zero]
  have h1 : R.mul v.x1 (R.mul A.m12 v.x2) = R.mul A.m12 (R.mul v.x1 v.x2) := by
    rw [← R.mul_assoc, R.mul_comm v.x1 A.m12, R.mul_assoc]
  have h2 : R.mul v.x2 (R.mul A.m21 v.x1) = R.mul A.m21 (R.mul v.x1 v.x2) := by
    rw [← R.mul_assoc, R.mul_comm v.x2 A.m21, R.mul_assoc, R.mul_comm v.x2 v.x1]
  rw [h1, h2, ← add_mul, hSkew, zero_mul]

/-- ①b 非平凡反对称见证：A = [[0,1],[-1,0]], v = (1,1) 满足 A v ≠ (0,0) 而 vᵀ A v = 0。 -/
theorem antisym_nontrivial_witness :
    let A : Mat2 intRing := { m11 := 0, m12 := 1, m21 := -1, m22 := 0 }
    let v : Vec2 intRing := { x1 := 1, x2 := 1 }
    matVec2 intRing A v ≠ { x1 := 0, x2 := 0 } ∧ quadForm2 intRing A v = 0 := by
  refine ⟨?_, by decide⟩
  intro h
  have h1 : (1 : Int) = 0 := congrArg Vec2.x1 h
  exact (by decide : (1 : Int) ≠ 0) h1

/-- ②a 机证定理（单向求值引理）：加入零对角反对称部 L_skew 不改变指定 dw 上的二次型非负性。 -/
theorem finite_diff_onsager_nonneg (R : RingSpec) (L_sym L_skew : Mat2 R) (dw : Vec2 R)
    (h11 : L_skew.m11 = R.zero) (h22 : L_skew.m22 = R.zero)
    (hSkew : R.add L_skew.m12 L_skew.m21 = R.zero)
    (hPsd : R.le R.zero (quadForm2 R L_sym dw)) :
    R.le R.zero (quadForm2 R (matAdd2 R L_sym L_skew) dw) := by
  have hSplit :
      quadForm2 R (matAdd2 R L_sym L_skew) dw =
      R.add (quadForm2 R L_sym dw) (quadForm2 R L_skew dw) := by
    unfold quadForm2 inner2 matVec2 matAdd2
    dsimp only
    rw [add_mul, add_mul, add_mul, add_mul]
    rw [R.mul_add, R.mul_add, R.mul_add, R.mul_add, R.mul_add, R.mul_add]
    rw [R.mul_add, R.mul_add, R.mul_add, R.mul_add]
    let a1 := R.mul dw.x1 (R.mul L_sym.m11 dw.x1)
    let b1 := R.mul dw.x1 (R.mul L_skew.m11 dw.x1)
    let a2 := R.mul dw.x1 (R.mul L_sym.m12 dw.x2)
    let b2 := R.mul dw.x1 (R.mul L_skew.m12 dw.x2)
    let a3 := R.mul dw.x2 (R.mul L_sym.m21 dw.x1)
    let b3 := R.mul dw.x2 (R.mul L_skew.m21 dw.x1)
    let a4 := R.mul dw.x2 (R.mul L_sym.m22 dw.x2)
    let b4 := R.mul dw.x2 (R.mul L_skew.m22 dw.x2)
    change R.add (R.add (R.add a1 b1) (R.add a2 b2)) (R.add (R.add a3 b3) (R.add a4 b4)) =
           R.add (R.add (R.add a1 a2) (R.add a3 a4)) (R.add (R.add b1 b2) (R.add b3 b4))
    have p1 : R.add (R.add a1 b1) (R.add a2 b2) = R.add (R.add a1 a2) (R.add b1 b2) := by
      rw [R.add_assoc a1, ← R.add_assoc b1 a2, R.add_comm b1 a2, R.add_assoc a2, ← R.add_assoc a1 a2]
    have p2 : R.add (R.add a3 b3) (R.add a4 b4) = R.add (R.add a3 a4) (R.add b3 b4) := by
      rw [R.add_assoc a3, ← R.add_assoc b3 a4, R.add_comm b3 a4, R.add_assoc a4, ← R.add_assoc a3 a4]
    rw [p1, p2]
    rw [R.add_assoc (R.add a1 a2), ← R.add_assoc (R.add b1 b2) (R.add a3 a4),
        R.add_comm (R.add b1 b2) (R.add a3 a4), R.add_assoc (R.add a3 a4), ← R.add_assoc (R.add a1 a2)]
  rw [hSplit, antisym_quadratic_zero R L_skew dw h11 h22 hSkew, add_zero]
  exact hPsd

/-- ②b 机证定理（对称半正定有限差交换流，对应层 2M 三.2）：
    当 L_sym 对称且全域半正定（∀ v, 0 ≤ vᵀ L_sym v）、L_skew 反对称时，
    对任意相邻共轭强度量 w1, w2 及其有限差交换流 J = (L_sym + L_skew)(w2 - w1)，恒有 (w2 - w1)·J ≥ 0。 -/
theorem finite_diff_onsager_symmetric_psd_nonneg (R : RingSpec)
    (L_sym L_skew : Mat2 R) (w1 w2 : Vec2 R)
    (_hSym : L_sym.m12 = L_sym.m21)
    (hGlobalPsd : ∀ v : Vec2 R, R.le R.zero (quadForm2 R L_sym v))
    (h11 : L_skew.m11 = R.zero) (h22 : L_skew.m22 = R.zero)
    (hSkew : R.add L_skew.m12 L_skew.m21 = R.zero) :
    let dw := vecSub2 R w2 w1
    let J := matVec2 R (matAdd2 R L_sym L_skew) dw
    R.le R.zero (inner2 R dw J) :=
  finite_diff_onsager_nonneg R L_sym L_skew (vecSub2 R w2 w1) h11 h22 hSkew (hGlobalPsd (vecSub2 R w2 w1))

/-- ③a 给定动力学分解后的逐态配对结构体 GenericSystem（不含算子来源，用于已分解态的瞬时配对检验）。 -/
structure GenericSystem (R : RingSpec) where
  V : Type
  vadd : V → V → V
  inner : V → V → R.Carrier
  inner_add_right : ∀ u x y, inner u (vadd x y) = R.add (inner u x) (inner u y)
  gradE : V
  gradS : V
  R_rev : V
  D_irr : V
  rev_energy_zero : inner gradE R_rev = R.zero
  rev_entropy_zero : inner gradS R_rev = R.zero
  irr_energy_zero : inner gradE D_irr = R.zero
  irr_entropy_nonneg : R.le R.zero (inner gradS D_irr)

/-- 三维向量与内积（使可逆分支 R_rev 与耗散分支 D_irr 可同时非零且与 ∇E、∇S 正交）。 -/
structure Vec3 (R : RingSpec) where
  x1 : R.Carrier
  x2 : R.Carrier
  x3 : R.Carrier

theorem vec3_nonempty : Nonempty (Vec3 intRing) :=
  ⟨{ x1 := 1, x2 := 0, x3 := 0 }⟩

def vec3Add (R : RingSpec) (a b : Vec3 R) : Vec3 R where
  x1 := R.add a.x1 b.x1
  x2 := R.add a.x2 b.x2
  x3 := R.add a.x3 b.x3

def inner3 (R : RingSpec) (u v : Vec3 R) : R.Carrier :=
  R.add (R.add (R.mul u.x1 v.x1) (R.mul u.x2 v.x2)) (R.mul u.x3 v.x3)

/-- ③b 算子级 GENERIC 双退化公设结构体（对应层 2M 三.3 标准 Grmela-Öttinger 算子退化条件）：
    显式给出反对称算子 L（∀u, ⟨u, L u⟩ = 0）、自伴半正定算子 M（∀u, 0 ≤ ⟨u, M u⟩），
    以及强算子退化公设 L ∇S = 0 与 M ∇E = 0。 -/
structure GenericOperatorSystem (R : RingSpec) where
  V : Type
  vzero : V
  vadd : V → V → V
  inner : V → V → R.Carrier
  inner_add_right : ∀ u x y, inner u (vadd x y) = R.add (inner u x) (inner u y)
  inner_zero_right : ∀ u, inner u vzero = R.zero
  L : V → V
  M : V → V
  L_skew_bilin : ∀ u v, R.add (inner u (L v)) (inner v (L u)) = R.zero
  L_skew_diag : ∀ u, inner u (L u) = R.zero
  M_sym : ∀ u v, inner u (M v) = inner v (M u)
  M_psd : ∀ u, R.le R.zero (inner u (M u))
  gradE : V
  gradS : V
  op_degen_L_S : L gradS = vzero
  op_degen_M_E : M gradE = vzero

/-- 构造性三维双活跃算子级 GENERIC 实例：
    gradE = e1, gradS = e2, L(x1,x2,x3) = (-x3, 0, x1), M(x1,x2,x3) = (0, x2, 0)，
    由此 R_rev = L gradE = e3 ≠ 0, D_irr = M gradS = e2 ≠ 0, L gradS = 0, M gradE = 0, dS/dt = 1 > 0。 -/
abbrev activeGenericOpInt : GenericOperatorSystem intRing where
  V := Vec3 intRing
  vzero := { x1 := 0, x2 := 0, x3 := 0 }
  vadd := vec3Add intRing
  inner := inner3 intRing
  inner_add_right := fun u x y => by
    unfold inner3 vec3Add
    change ((u.x1 * (x.x1 + y.x1) + u.x2 * (x.x2 + y.x2)) + u.x3 * (x.x3 + y.x3)) =
      ((u.x1 * x.x1 + u.x2 * x.x2) + u.x3 * x.x3) + ((u.x1 * y.x1 + u.x2 * y.x2) + u.x3 * y.x3)
    rw [Int.mul_add, Int.mul_add, Int.mul_add]
    let a1 := u.x1 * x.x1; let b1 := u.x1 * y.x1
    let a2 := u.x2 * x.x2; let b2 := u.x2 * y.x2
    let a3 := u.x3 * x.x3; let b3 := u.x3 * y.x3
    have h12 : (a1 + b1) + (a2 + b2) = (a1 + a2) + (b1 + b2) := by
      rw [Int.add_assoc a1 b1, ← Int.add_assoc b1 a2 b2, Int.add_comm b1 a2, Int.add_assoc a2 b1 b2, ← Int.add_assoc a1 a2]
    rw [h12]
    rw [Int.add_assoc (a1 + a2) (b1 + b2), ← Int.add_assoc (b1 + b2) a3 b3, Int.add_comm (b1 + b2) a3,
        Int.add_assoc a3 (b1 + b2) b3, ← Int.add_assoc (a1 + a2) a3]
  inner_zero_right := fun u => by
    unfold inner3
    change (u.x1 * 0 + u.x2 * 0) + u.x3 * 0 = 0
    rw [Int.mul_zero, Int.mul_zero, Int.mul_zero, Int.zero_add, Int.zero_add]
  L := fun v => { x1 := -v.x3, x2 := 0, x3 := v.x1 }
  M := fun v => { x1 := 0, x2 := v.x2, x3 := 0 }
  L_skew_bilin := fun u v => by
    unfold inner3
    change ((u.x1 * (-v.x3) + u.x2 * 0) + u.x3 * v.x1) + ((v.x1 * (-u.x3) + v.x2 * 0) + v.x3 * u.x1) = 0
    rw [Int.mul_zero, Int.mul_zero, Int.add_zero, Int.add_zero]
    rw [Int.mul_neg, Int.mul_neg, Int.mul_comm v.x3 u.x1, Int.mul_comm v.x1 u.x3]
    have hPair : (-(u.x1 * v.x3) + u.x3 * v.x1) + (-(u.x3 * v.x1) + u.x1 * v.x3) =
        (-(u.x1 * v.x3) + u.x1 * v.x3) + (u.x3 * v.x1 + -(u.x3 * v.x1)) := by
      rw [Int.add_assoc (-(u.x1 * v.x3)), ← Int.add_assoc (u.x3 * v.x1) (-(u.x3 * v.x1)) (u.x1 * v.x3),
          Int.add_comm (u.x3 * v.x1 + -(u.x3 * v.x1)) (u.x1 * v.x3), ← Int.add_assoc (-(u.x1 * v.x3))]
    rw [hPair, Int.add_left_neg, Int.add_right_neg, Int.zero_add]
  L_skew_diag := fun u => by
    unfold inner3
    change (u.x1 * (-u.x3) + u.x2 * 0) + u.x3 * u.x1 = 0
    rw [Int.mul_zero, Int.add_zero, Int.mul_neg, Int.mul_comm u.x3 u.x1, Int.add_left_neg]
  M_sym := fun u v => by
    unfold inner3
    change (u.x1 * 0 + u.x2 * v.x2) + u.x3 * 0 = (v.x1 * 0 + v.x2 * u.x2) + v.x3 * 0
    rw [Int.mul_zero, Int.mul_zero, Int.mul_zero, Int.mul_zero,
        Int.zero_add, Int.add_zero, Int.zero_add, Int.add_zero, Int.mul_comm]
  M_psd := fun u => by
    unfold inner3
    change (0 : Int) ≤ (u.x1 * 0 + u.x2 * u.x2) + u.x3 * 0
    rw [Int.mul_zero, Int.mul_zero, Int.zero_add, Int.add_zero]
    cases Int.lt_trichotomy 0 u.x2 with
    | inl hpos => exact Int.le_of_lt (Int.mul_pos hpos hpos)
    | inr hrest =>
      cases hrest with
      | inl heq =>
        rw [← heq, Int.mul_zero]
        exact Int.le_refl 0
      | inr hneg =>
        have hpos : (0 : Int) < -u.x2 := Int.neg_pos_of_neg hneg
        have hmul : (0 : Int) < (-u.x2) * (-u.x2) := Int.mul_pos hpos hpos
        rw [Int.neg_mul_neg] at hmul
        exact Int.le_of_lt hmul
  gradE := { x1 := 1, x2 := 0, x3 := 0 }
  gradS := { x1 := 0, x2 := 1, x3 := 0 }
  op_degen_L_S := rfl
  op_degen_M_E := rfl

theorem genericOperatorSystem_nonempty : Nonempty (GenericOperatorSystem intRing) :=
  ⟨activeGenericOpInt⟩

/-- 由算子级 GENERIC 系统构造逐态配对系统 GenericSystem。 -/
def GenericOperatorSystem.toGenericSystem {R : RingSpec} (G : GenericOperatorSystem R) : GenericSystem R where
  V := G.V
  vadd := G.vadd
  inner := G.inner
  inner_add_right := G.inner_add_right
  gradE := G.gradE
  gradS := G.gradS
  R_rev := G.L G.gradE
  D_irr := G.M G.gradS
  rev_energy_zero := G.L_skew_diag G.gradE
  rev_entropy_zero := by
    have hBilin := G.L_skew_bilin G.gradS G.gradE
    rw [G.op_degen_L_S, G.inner_zero_right, add_zero] at hBilin
    exact hBilin
  irr_energy_zero := by
    rw [G.M_sym, G.op_degen_M_E, G.inner_zero_right]
  irr_entropy_nonneg := G.M_psd G.gradS

/-- 构造性三维双活跃非空见证（R_rev = e3 ≠ 0 且 D_irr = e2 ≠ 0）。 -/
theorem genericSystem_nonempty : Nonempty (GenericSystem intRing) :=
  ⟨activeGenericOpInt.toGenericSystem⟩

/-- ③a 机证定理：给定动力学分解下的双正交条件导出总能配对为零且总熵配对非负。 -/
theorem generic_double_degeneracy_laws {R : RingSpec} (G : GenericSystem R) :
    let zDot := G.vadd G.R_rev G.D_irr
    G.inner G.gradE zDot = R.zero ∧ R.le R.zero (G.inner G.gradS zDot) := by
  dsimp only
  refine ⟨?_, ?_⟩
  · rw [G.inner_add_right, G.rev_energy_zero, G.irr_energy_zero, R.zero_add]
  · rw [G.inner_add_right, G.rev_entropy_zero, R.zero_add]
    exact G.irr_entropy_nonneg

/-- ③b 机证定理：算子级 GENERIC 双退化（L ∇S = 0, M ∇E = 0）精确导出总能守恒与熵产非负。 -/
theorem generic_operator_implies_double_degeneracy {R : RingSpec} (G : GenericOperatorSystem R) :
    let zDot := G.vadd (G.L G.gradE) (G.M G.gradS)
    G.inner G.gradE zDot = R.zero ∧ R.le R.zero (G.inner G.gradS zDot) :=
  generic_double_degeneracy_laws G.toGenericSystem

/-- ③c 三维双活跃非平凡存在见证：同时公开 R_rev ≠ 0, D_irr ≠ 0, dE/dt = 0, dS/dt = 1 > 0。 -/
theorem genericOperatorSystem_nontrivial_active_witness :
    let G := activeGenericOpInt
    let R_rev := G.L G.gradE
    let D_irr := G.M G.gradS
    let zDot := G.vadd R_rev D_irr
    R_rev ≠ G.vzero ∧
    D_irr ≠ G.vzero ∧
    G.inner G.gradE zDot = 0 ∧
    G.inner G.gradS zDot = 1 ∧
    intRing.lt 0 (G.inner G.gradS zDot) := by
  refine ⟨?_, ?_, by decide, by decide, by decide⟩
  · intro h
    have h3 : (1 : Int) = 0 := congrArg Vec3.x3 h
    exact (by decide : (1 : Int) ≠ 0) h3
  · intro h
    have h2 : (1 : Int) = 0 := congrArg Vec3.x2 h
    exact (by decide : (1 : Int) ≠ 0) h2

end CaeAxioms.DissipationGeneric
