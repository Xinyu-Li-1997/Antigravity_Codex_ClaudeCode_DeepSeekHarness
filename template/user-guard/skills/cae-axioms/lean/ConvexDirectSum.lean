-- Copyright 2026 Xinyu Li (Xinyu-Li-1997)
-- SPDX-License-Identifier: Apache-2.0
/-
  ConvexDirectSum.lean
  层 1.5（形式数学与有限计算底座）零 Mathlib 纯核 Lean 4 形式化骨架：
  - 显式参数化非退化有序交换环规格 OrderedCommRingSpec（附 Int 构造性 Nonempty 实例见证与反退化推论）
  - ① 模直和分解 DirectSumDecomp（含幂等投影 proj0_idem、存在唯一性分解与核方向缩放保 proj0 分量定理）
  - ② 齐次无分母两点二次偏差恒等式、严格正偏差及两式不等推论（机证抛物线图像对非退化正权加权平均不封闭）
  - ③ 有序交换环双极限矛盾：(∀ε>0, ∃n, μ n < ε) ∧ (∃μ0>0, ∀n, μ0 ≤ μ n) ⊢ False（层 1.5 2A.1，附收敛桥接与前提各自可满足见证）
  - ④ 低维整数向量双向与联立反例：标量二次泛函严格单调递减与正性/线性守恒双向无蕴含且联合可满足（层 1.5 原型 I/II/IV.2）
-/

namespace CaeAxioms.ConvexDirectSum

/-- 显式参数化非退化有序交换环规格（纯 Init 零 Mathlib 依赖，排除 0 < 0 与单点零环退化模型）。 -/
structure OrderedCommRingSpec where
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
  sq_pos_of_ne_zero : ∀ a, a ≠ zero → lt zero (mul a a)

private theorem int_mul_pos_core (a b : Int) (ha : (0 : Int) < a) (hb : (0 : Int) < b) :
    (0 : Int) < a * b :=
  Int.mul_pos ha hb

private theorem int_sq_pos_core (a : Int) (hne : a ≠ 0) : (0 : Int) < a * a := by
  cases Int.lt_trichotomy 0 a with
  | inl hpos => exact Int.mul_pos hpos hpos
  | inr hrest =>
    cases hrest with
    | inl heq => exact False.elim (hne heq.symm)
    | inr hneg =>
      have hpos : (0 : Int) < -a := Int.neg_pos_of_neg hneg
      have hmul : (0 : Int) < (-a) * (-a) := Int.mul_pos hpos hpos
      rw [Int.neg_mul_neg] at hmul
      exact hmul

/-- 具体构造性实例：整数环 Int 满足非退化有序交换环规格 OrderedCommRingSpec。 -/
abbrev intRingSpec : OrderedCommRingSpec where
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
  sq_pos_of_ne_zero := int_sq_pos_core

/-- 构造性非空见证：证明 OrderedCommRingSpec 前提集可满足。 -/
theorem orderedCommRingSpec_nonempty : Nonempty OrderedCommRingSpec :=
  ⟨intRingSpec⟩

def sub (R : OrderedCommRingSpec) (a b : R.Carrier) : R.Carrier :=
  R.add a (R.neg b)

theorem lt_irrefl (R : OrderedCommRingSpec) (a : R.Carrier) : ¬ R.lt a a :=
  fun h => R.lt_not_le a a h (R.le_refl a)

theorem lt_ne (R : OrderedCommRingSpec) (a b : R.Carrier) (h : R.lt a b) : a ≠ b := by
  intro heq
  rw [heq] at h
  exact lt_irrefl R b h

theorem zero_ne_one (R : OrderedCommRingSpec) : R.zero ≠ R.one :=
  lt_ne R R.zero R.one R.zero_lt_one

theorem add_zero (R : OrderedCommRingSpec) (a : R.Carrier) : R.add a R.zero = a := by
  rw [R.add_comm, R.zero_add]

theorem neg_add (R : OrderedCommRingSpec) (a : R.Carrier) : R.add (R.neg a) a = R.zero := by
  rw [R.add_comm, R.add_neg]

theorem add_mul (R : OrderedCommRingSpec) (a b c : R.Carrier) :
    R.mul (R.add a b) c = R.add (R.mul a c) (R.mul b c) := by
  rw [R.mul_comm, R.mul_add, R.mul_comm c a, R.mul_comm c b]

theorem mul_zero (R : OrderedCommRingSpec) (a : R.Carrier) : R.mul a R.zero = R.zero := by
  have h1 : R.add (R.mul a R.zero) (R.mul a R.zero) = R.mul a R.zero := by
    rw [← R.mul_add, R.zero_add]
  have h2 : R.add (R.add (R.mul a R.zero) (R.mul a R.zero)) (R.neg (R.mul a R.zero)) =
      R.add (R.mul a R.zero) (R.neg (R.mul a R.zero)) :=
    congrArg (fun t => R.add t (R.neg (R.mul a R.zero))) h1
  rw [R.add_assoc, R.add_neg, add_zero] at h2
  exact h2

theorem neg_unique (R : OrderedCommRingSpec) (a b : R.Carrier) (h : R.add a b = R.zero) :
    b = R.neg a := by
  have h1 := congrArg (fun t => R.add (R.neg a) t) h
  rw [← R.add_assoc, neg_add, R.zero_add, add_zero] at h1
  exact h1

theorem mul_neg (R : OrderedCommRingSpec) (a b : R.Carrier) :
    R.mul a (R.neg b) = R.neg (R.mul a b) := by
  apply neg_unique
  rw [← R.mul_add, R.add_neg, mul_zero]

theorem neg_mul (R : OrderedCommRingSpec) (a b : R.Carrier) :
    R.mul (R.neg a) b = R.neg (R.mul a b) := by
  rw [R.mul_comm, mul_neg, R.mul_comm]

theorem neg_neg (R : OrderedCommRingSpec) (a : R.Carrier) :
    R.neg (R.neg a) = a := by
  exact (neg_unique R (R.neg a) a (neg_add R a)).symm

theorem neg_add_dist (R : OrderedCommRingSpec) (a b : R.Carrier) :
    R.neg (R.add a b) = R.add (R.neg a) (R.neg b) := by
  symm
  apply neg_unique
  calc
    R.add (R.add a b) (R.add (R.neg a) (R.neg b))
      = R.add (R.add a (R.neg a)) (R.add b (R.neg b)) := by
          rw [R.add_assoc, ← R.add_assoc b (R.neg a), R.add_comm b (R.neg a), R.add_assoc (R.neg a) b, ← R.add_assoc]
    _ = R.zero := by rw [R.add_neg, R.add_neg, R.zero_add]

theorem add_cancel_left (R : OrderedCommRingSpec) (x y : R.Carrier) :
    R.add (R.add x y) (R.neg x) = y := by
  rw [R.add_comm x y, R.add_assoc, R.add_neg, add_zero]

theorem sub_ne_zero_of_ne (R : OrderedCommRingSpec) (a b : R.Carrier) (hne : a ≠ b) :
    sub R a b ≠ R.zero := by
  intro hsub
  have h1 := congrArg (fun t => R.add t b) hsub
  unfold sub at h1
  rw [R.add_assoc, neg_add, add_zero, R.zero_add] at h1
  exact hne h1

theorem ne_of_sub_pos (R : OrderedCommRingSpec) (x y : R.Carrier) (hpos : R.lt R.zero (sub R x y)) :
    x ≠ y := by
  intro heq
  rw [heq] at hpos
  unfold sub at hpos
  rw [R.add_neg] at hpos
  exact lt_irrefl R R.zero hpos

/-- ① 模直和分解结构 V = Im(proj0) ⊕ Ker(proj0) 与幂等线性投影 proj0（原型 II 底座）。 -/
structure DirectSumDecomp (R : OrderedCommRingSpec) where
  V : Type
  vzero : V
  vadd : V → V → V
  vneg : V → V
  vsmul : R.Carrier → V → V
  proj0 : V → V
  vadd_comm : ∀ x y, vadd x y = vadd y x
  vadd_assoc : ∀ x y z, vadd (vadd x y) z = vadd x (vadd y z)
  vadd_zero : ∀ x, vadd x vzero = x
  vadd_neg : ∀ x, vadd x (vneg x) = vzero
  vsmul_zero : ∀ c, vsmul c vzero = vzero
  proj0_add : ∀ x y, proj0 (vadd x y) = vadd (proj0 x) (proj0 y)
  proj0_smul : ∀ c x, proj0 (vsmul c x) = vsmul c (proj0 x)
  proj0_idem : ∀ x, proj0 (proj0 x) = proj0 x

/-- 构造性非空见证：Int × Int 按第一分量幂等投影构成直和分解。 -/
theorem directSumDecomp_nonempty : Nonempty (DirectSumDecomp intRingSpec) :=
  ⟨{
    V := Int × Int
    vzero := (0, 0)
    vadd := fun p q => (p.1 + q.1, p.2 + q.2)
    vneg := fun p => (-p.1, -p.2)
    vsmul := fun c p => (c * p.1, c * p.2)
    proj0 := fun p => (p.1, 0)
    vadd_comm := fun p q => by
      cases p with | mk a1 b1 =>
      cases q with | mk a2 b2 =>
      change (a1 + a2, b1 + b2) = (a2 + a1, b2 + b1)
      rw [Int.add_comm a1 a2, Int.add_comm b1 b2]
    vadd_assoc := fun p q r => by
      cases p with | mk a1 b1 =>
      cases q with | mk a2 b2 =>
      cases r with | mk a3 b3 =>
      change ((a1 + a2) + a3, (b1 + b2) + b3) = (a1 + (a2 + a3), b1 + (b2 + b3))
      rw [Int.add_assoc, Int.add_assoc]
    vadd_zero := fun p => by
      cases p with | mk a b =>
      change (a + 0, b + 0) = (a, b)
      rw [Int.add_zero, Int.add_zero]
    vadd_neg := fun p => by
      cases p with | mk a b =>
      change (a + (-a), b + (-b)) = (0, 0)
      rw [Int.add_right_neg, Int.add_right_neg]
    vsmul_zero := fun c => by
      change (c * 0, c * 0) = (0, 0)
      rw [Int.mul_zero]
    proj0_add := fun _ _ => rfl
    proj0_smul := fun c p => by
      change (c * p.1, 0) = (c * p.1, c * 0)
      rw [Int.mul_zero]
    proj0_idem := fun _ => rfl
  }⟩

def vsub {R : OrderedCommRingSpec} (D : DirectSumDecomp R) (x y : D.V) : D.V :=
  D.vadd x (D.vneg y)

theorem vzero_add {R : OrderedCommRingSpec} (D : DirectSumDecomp R) (x : D.V) :
    D.vadd D.vzero x = x := by
  rw [D.vadd_comm, D.vadd_zero]

theorem vneg_add {R : OrderedCommRingSpec} (D : DirectSumDecomp R) (x : D.V) :
    D.vadd (D.vneg x) x = D.vzero := by
  rw [D.vadd_comm, D.vadd_neg]

theorem vneg_unique {R : OrderedCommRingSpec} (D : DirectSumDecomp R) (x y : D.V)
    (h : D.vadd x y = D.vzero) : y = D.vneg x := by
  have h1 := congrArg (fun t => D.vadd (D.vneg x) t) h
  rw [← D.vadd_assoc, vneg_add, vzero_add, D.vadd_zero] at h1
  exact h1

theorem proj0_zero {R : OrderedCommRingSpec} (D : DirectSumDecomp R) :
    D.proj0 D.vzero = D.vzero := by
  have h1 : D.vadd (D.proj0 D.vzero) (D.proj0 D.vzero) = D.proj0 D.vzero := by
    rw [← D.proj0_add, D.vadd_zero]
  have h2 := congrArg (fun t => D.vadd t (D.vneg (D.proj0 D.vzero))) h1
  rw [D.vadd_assoc, D.vadd_neg, D.vadd_zero] at h2
  exact h2

theorem proj0_vneg {R : OrderedCommRingSpec} (D : DirectSumDecomp R) (x : D.V) :
    D.proj0 (D.vneg x) = D.vneg (D.proj0 x) := by
  apply vneg_unique D (D.proj0 x) (D.proj0 (D.vneg x))
  rw [← D.proj0_add, D.vadd_neg, proj0_zero]

/-- ①a 机证定理：由幂等线性投影导出任意元素的直和分解存在性 v = proj0 v + (v - proj0 v)。 -/
theorem directSum_decomp_exists {R : OrderedCommRingSpec} (D : DirectSumDecomp R) (v : D.V) :
    D.vadd (D.proj0 v) (vsub D v (D.proj0 v)) = v ∧
    D.proj0 (D.proj0 v) = D.proj0 v ∧
    D.proj0 (vsub D v (D.proj0 v)) = D.vzero := by
  refine ⟨?_, D.proj0_idem v, ?_⟩
  · unfold vsub
    rw [D.vadd_comm v (D.vneg (D.proj0 v)), ← D.vadd_assoc, D.vadd_neg, vzero_add]
  · unfold vsub
    rw [D.proj0_add, proj0_vneg, D.proj0_idem, D.vadd_neg]

/-- ①b 机证定理：直和分解 Im(proj0) ⊕ Ker(proj0) 的唯一性。 -/
theorem directSum_decomp_unique {R : OrderedCommRingSpec} (D : DirectSumDecomp R)
    (v u0 w : D.V) (hsum : D.vadd u0 w = v) (hu0 : D.proj0 u0 = u0) (hw : D.proj0 w = D.vzero) :
    u0 = D.proj0 v ∧ w = vsub D v (D.proj0 v) := by
  have hproj : D.proj0 v = u0 := by
    rw [← hsum, D.proj0_add, hu0, hw, D.vadd_zero]
  refine ⟨hproj.symm, ?_⟩
  have hsub := congrArg (fun t => D.vadd t (D.vneg u0)) hsum.symm
  rw [D.vadd_comm u0 w, D.vadd_assoc, D.vadd_neg, D.vadd_zero] at hsub
  unfold vsub
  rw [hproj]
  exact hsub.symm

/-- ①c 机证定理：核方向 w ∈ Ker(proj0) 的任意标量缩放 u_bar + θ • w 严格保持 proj0 分量不变（与凸集及 θ 范围正交）。 -/
theorem convex_shrink_preserves_proj0 {R : OrderedCommRingSpec} (D : DirectSumDecomp R)
    (u_bar w : D.V) (θ : R.Carrier) (hw : D.proj0 w = D.vzero) :
    D.proj0 (D.vadd u_bar (D.vsmul θ w)) = D.proj0 u_bar := by
  rw [D.proj0_add, D.proj0_smul, hw, D.vsmul_zero, D.vadd_zero]

/-- ②a 机证定理：任意权两点加权二次偏差的齐次无分母精确恒等式：
    (w1 + w2) * (w1 * a^2 + w2 * b^2) - (w1 * a + w2 * b)^2 = (w1 * w2) * (a - b)^2。 -/
theorem weighted_convex_quadratic_gap (R : OrderedCommRingSpec) (w1 w2 a b : R.Carrier) :
    sub R
      (R.mul (R.add w1 w2) (R.add (R.mul w1 (R.mul a a)) (R.mul w2 (R.mul b b))))
      (R.mul (R.add (R.mul w1 a) (R.mul w2 b)) (R.add (R.mul w1 a) (R.mul w2 b)))
    = R.mul (R.mul w1 w2) (R.mul (sub R a b) (sub R a b)) := by
  let A := R.mul (R.mul w1 w1) (R.mul a a)
  let B := R.mul (R.mul w2 w2) (R.mul b b)
  let C1 := R.mul (R.mul w1 w2) (R.mul a a)
  let C2 := R.mul (R.mul w1 w2) (R.mul b b)
  let X := R.mul (R.mul w1 w2) (R.mul a b)
  have hLHS1 :
      R.mul (R.add w1 w2) (R.add (R.mul w1 (R.mul a a)) (R.mul w2 (R.mul b b))) =
      R.add (R.add A B) (R.add C1 C2) := by
    rw [add_mul, R.mul_add, R.mul_add]
    have t1 : R.mul w1 (R.mul w1 (R.mul a a)) = A := by rw [← R.mul_assoc]
    have t2 : R.mul w1 (R.mul w2 (R.mul b b)) = C2 := by rw [← R.mul_assoc]
    have t3 : R.mul w2 (R.mul w1 (R.mul a a)) = C1 := by rw [← R.mul_assoc, R.mul_comm w2 w1]
    have t4 : R.mul w2 (R.mul w2 (R.mul b b)) = B := by rw [← R.mul_assoc]
    rw [t1, t2, t3, t4]
    rw [R.add_assoc A C2, ← R.add_assoc C2 C1 B, R.add_comm C2 C1,
        R.add_comm (R.add C1 C2) B, ← R.add_assoc A B]
  have hLHS2 :
      R.mul (R.add (R.mul w1 a) (R.mul w2 b)) (R.add (R.mul w1 a) (R.mul w2 b)) =
      R.add (R.add A B) (R.add X X) := by
    rw [add_mul, R.mul_add, R.mul_add]
    have s1 : R.mul (R.mul w1 a) (R.mul w1 a) = A := by
      rw [R.mul_assoc, ← R.mul_assoc a w1 a, R.mul_comm a w1, R.mul_assoc w1 a a, ← R.mul_assoc]
    have s2 : R.mul (R.mul w1 a) (R.mul w2 b) = X := by
      rw [R.mul_assoc, ← R.mul_assoc a w2 b, R.mul_comm a w2, R.mul_assoc w2 a b, ← R.mul_assoc]
    have s3 : R.mul (R.mul w2 b) (R.mul w1 a) = X := by
      rw [R.mul_comm (R.mul w2 b) (R.mul w1 a), s2]
    have s4 : R.mul (R.mul w2 b) (R.mul w2 b) = B := by
      rw [R.mul_assoc, ← R.mul_assoc b w2 b, R.mul_comm b w2, R.mul_assoc w2 b b, ← R.mul_assoc]
    rw [s1, s2, s3, s4]
    rw [R.add_assoc A X, ← R.add_assoc X X B, R.add_comm (R.add X X) B, ← R.add_assoc A B]
  have hRHS :
      R.mul (R.mul w1 w2) (R.mul (sub R a b) (sub R a b)) =
      R.add (R.add C1 C2) (R.neg (R.add X X)) := by
    have hDiffSq :
        R.mul (sub R a b) (sub R a b) =
        R.add (R.add (R.mul a a) (R.mul b b)) (R.neg (R.add (R.mul a b) (R.mul a b))) := by
      unfold sub
      rw [add_mul, R.mul_add, R.mul_add, mul_neg, neg_mul, mul_neg, neg_mul, neg_neg, R.mul_comm b a]
      rw [neg_add_dist]
      rw [R.add_assoc (R.mul a a), ← R.add_assoc (R.neg (R.mul a b)) (R.neg (R.mul a b)) (R.mul b b),
          R.add_comm (R.add (R.neg (R.mul a b)) (R.neg (R.mul a b))) (R.mul b b),
          ← R.add_assoc (R.mul a a) (R.mul b b)]
    rw [hDiffSq, R.mul_add, R.mul_add, mul_neg, R.mul_add]
  rw [hRHS]
  unfold sub
  rw [hLHS1, hLHS2, neg_add_dist]
  rw [R.add_assoc (R.add A B), ← R.add_assoc (R.add C1 C2), R.add_comm (R.add C1 C2) (R.neg (R.add A B)),
      R.add_assoc (R.neg (R.add A B)), ← R.add_assoc (R.add A B), R.add_neg, R.zero_add]

/-- ②b 推论：正权加权两点在非退化条件 a - b ≠ 0 下的齐次二次偏差严格为正（抛物线图像对凸平均不封闭）。 -/
theorem convex_quadratic_gap_strict_pos (R : OrderedCommRingSpec) (w1 w2 a b : R.Carrier)
    (hw1 : R.lt R.zero w1) (hw2 : R.lt R.zero w2) (hne : sub R a b ≠ R.zero) :
    R.lt R.zero
      (sub R
        (R.mul (R.add w1 w2) (R.add (R.mul w1 (R.mul a a)) (R.mul w2 (R.mul b b))))
        (R.mul (R.add (R.mul w1 a) (R.mul w2 b)) (R.add (R.mul w1 a) (R.mul w2 b)))) := by
  rw [weighted_convex_quadratic_gap]
  exact R.mul_pos (R.mul w1 w2) (R.mul (sub R a b) (sub R a b))
    (R.mul_pos w1 w2 hw1 hw2)
    (R.sq_pos_of_ne_zero (sub R a b) hne)

/-- ②c 推论（非退化不等式证书）：在有序交换环上，正权与 a ≠ b 严格导出两项不相等。 -/
theorem convex_quadratic_gap_ne (R : OrderedCommRingSpec) (w1 w2 a b : R.Carrier)
    (hw1 : R.lt R.zero w1) (hw2 : R.lt R.zero w2) (hne : a ≠ b) :
    R.mul (R.add w1 w2) (R.add (R.mul w1 (R.mul a a)) (R.mul w2 (R.mul b b))) ≠
      R.mul (R.add (R.mul w1 a) (R.mul w2 b)) (R.add (R.mul w1 a) (R.mul w2 b)) :=
  ne_of_sub_pos R _ _ (convex_quadratic_gap_strict_pos R w1 w2 a b hw1 hw2 (sub_ne_zero_of_ne R a b hne))

/-- ②d 具体数值反退化见证：在 Int 上取 w1=w2=1, a=0, b=2，齐次二次偏差为 4 > 0 且两式不等。 -/
theorem intRingSpec_quadratic_gap_witness :
    intRingSpec.lt intRingSpec.zero
      (sub intRingSpec
        (intRingSpec.mul (intRingSpec.add 1 1)
          (intRingSpec.add (intRingSpec.mul 1 (intRingSpec.mul 0 0)) (intRingSpec.mul 1 (intRingSpec.mul 2 2))))
        (intRingSpec.mul (intRingSpec.add (intRingSpec.mul 1 0) (intRingSpec.mul 1 2))
          (intRingSpec.add (intRingSpec.mul 1 0) (intRingSpec.mul 1 2)))) ∧
    intRingSpec.mul (intRingSpec.add 1 1)
      (intRingSpec.add (intRingSpec.mul 1 (intRingSpec.mul 0 0)) (intRingSpec.mul 1 (intRingSpec.mul 2 2))) ≠
      intRingSpec.mul (intRingSpec.add (intRingSpec.mul 1 0) (intRingSpec.mul 1 2))
        (intRingSpec.add (intRingSpec.mul 1 0) (intRingSpec.mul 1 2)) :=
  ⟨by decide, by decide⟩

/-- ③a 机证定理（层 1.5 2A.1 固定绝对测度下界矛盾）：任意正阈值被跌破（趋零所蕴含的更弱阈值条件）与固定严格正下界矛盾。 -/
theorem double_limit_contradiction (R : OrderedCommRingSpec) (μ : Nat → R.Carrier)
    (hVanish : ∀ ε : R.Carrier, R.lt R.zero ε → ∃ n : Nat, R.lt (μ n) ε)
    (hLower : ∃ μ0 : R.Carrier, R.lt R.zero μ0 ∧ ∀ n : Nat, R.le μ0 (μ n)) : False := by
  cases hLower with
  | intro μ0 hμ0 =>
    cases hVanish μ0 hμ0.1 with
    | intro n hn =>
      exact R.lt_not_le (μ n) μ0 hn (hμ0.2 n)

/-- ③b 桥接引理：由经典最终趋零（∀ε>0, ∃N, ∀n≥N, μ n < ε）直接导出双极限矛盾。 -/
theorem double_limit_from_eventual_vanish (R : OrderedCommRingSpec) (μ : Nat → R.Carrier)
    (hConv : ∀ ε : R.Carrier, R.lt R.zero ε → ∃ N : Nat, ∀ n : Nat, N ≤ n → R.lt (μ n) ε)
    (hLower : ∃ μ0 : R.Carrier, R.lt R.zero μ0 ∧ ∀ n : Nat, R.le μ0 (μ n)) : False :=
  double_limit_contradiction R μ
    (fun ε hε =>
      let ⟨N, hN⟩ := hConv ε hε
      ⟨N, hN N (Nat.le_refl N)⟩)
    hLower

/-- ③c 反空真见证：双极限矛盾的两条前提在 Int 上各自独立可满足（常零序列与常一序列）。 -/
theorem double_limit_premises_individually_satisfiable :
    (∃ μ : Nat → intRingSpec.Carrier, ∀ ε : intRingSpec.Carrier, intRingSpec.lt intRingSpec.zero ε → ∃ n : Nat, intRingSpec.lt (μ n) ε) ∧
    (∃ μ : Nat → intRingSpec.Carrier, ∃ μ0 : intRingSpec.Carrier, intRingSpec.lt intRingSpec.zero μ0 ∧ ∀ n : Nat, intRingSpec.le μ0 (μ n)) :=
  ⟨⟨fun _ => 0, fun _ hε => ⟨0, hε⟩⟩,
   ⟨fun _ => 1, 1, intRingSpec.zero_lt_one, fun _ => intRingSpec.le_refl 1⟩⟩

/-- ④ 低维整数向量反例（原型 IV.2 与原型 I 正性 / 原型 II 线性守恒双向无蕴含且联合可满足）。 -/
def quadFunctional (u : Int × Int) : Int :=
  u.1 * u.1 + u.2 * u.2

theorem scalar_functional_monotone_not_implies_positivity_or_conservation :
    ∃ (u0 u1 : Int × Int),
      (0 < u0.1 ∧ 0 < u0.2) ∧
      quadFunctional u1 < quadFunctional u0 ∧
      quadFunctional u1 ≤ quadFunctional u0 ∧
      ¬ (0 < u1.1 ∧ 0 < u1.2) ∧
      (u1.1 + u1.2 ≠ u0.1 + u0.2) :=
  ⟨(2, 1), (1, -1), by decide⟩

theorem conservation_and_positivity_not_implies_scalar_functional_monotone :
    ∃ (u0 u1 : Int × Int),
      (0 < u0.1 ∧ 0 < u0.2) ∧
      (0 < u1.1 ∧ 0 < u1.2) ∧
      (u1.1 + u1.2 = u0.1 + u0.2) ∧
      quadFunctional u0 < quadFunctional u1 :=
  ⟨(2, 2), (3, 1), by decide⟩

theorem scalar_functional_positivity_conservation_jointly_satisfiable :
    ∃ (u0 u1 : Int × Int),
      (0 < u0.1 ∧ 0 < u0.2) ∧
      (0 < u1.1 ∧ 0 < u1.2) ∧
      (u1.1 + u1.2 = u0.1 + u0.2) ∧
      quadFunctional u1 ≤ quadFunctional u0 :=
  ⟨(1, 1), (1, 1), by decide⟩

end CaeAxioms.ConvexDirectSum
