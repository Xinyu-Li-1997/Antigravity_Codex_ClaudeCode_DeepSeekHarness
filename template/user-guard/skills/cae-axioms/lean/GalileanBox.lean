-- Copyright 2026 Xinyu Li (Xinyu-Li-1997)
-- SPDX-License-Identifier: Apache-2.0
/-
  GalileanBox.lean
  层 2M（正测度理性力学与热力学公理底座 · 第一级无条件收支与对称公理）零 Mathlib 纯核 Lean 4 骨架：
  - 非退化有序交换环规格 RingSpec（排除 0 < 0、0 ≤ -1 与单点零环退化模型）
  - 正面建设性结构体 PositiveMeasureBox（带 V >= V_min > 0, M > 0 守卫、导出 V > 0 与 M ≠ 0，附 Nonempty 见证）
  - ①a 横截内部共享面片成对反对称接触交换总和精确对消守恒
  - ①b 重合于弯曲奇异张力界面的体相-面相合并区域收支对消守恒
  - ② 一维总质量倍能量特化下伽利略变换 G_w 的单位元律、群复合律与线性表示律
  - ③ 缩放内能不变量 I = M(2E) - Q² 严格不变，及附带质量可逆见证时的质心系存在唯一性与内能恢复
  - ④ 不交两块子域清分母平行轴动能恒等式
  - ⑤ 由独立通量元组构造相对通量并显式调用 galileanBoost 推出零和扩散下共动通量消去平动机械功
-/

namespace CaeAxioms.GalileanBox

/-- 自包含非退化有序交换环规格，支持单文件零依赖编译并排除零质量/零体积退化序模型。 -/
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
  lt_of_lt_of_le : ∀ a b c, lt a b → le b c → lt a c
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
  lt_of_lt_of_le := fun _ _ _ => Int.lt_of_lt_of_le
  lt_not_le := fun _ _ h1 h2 => Int.not_le.mpr h1 h2
  add_lt_add_left := fun _ _ c h => Int.add_lt_add_left h c
  zero_lt_one := by decide
  mul_pos := int_mul_pos_core

theorem ringSpec_nonempty : Nonempty RingSpec :=
  ⟨intRing⟩

def sub (R : RingSpec) (a b : R.Carrier) : R.Carrier :=
  R.add a (R.neg b)

def two (R : RingSpec) : R.Carrier :=
  R.add R.one R.one

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

theorem neg_unique (R : RingSpec) (a b : R.Carrier) (h : R.add a b = R.zero) :
    b = R.neg a := by
  have h1 := congrArg (fun t => R.add (R.neg a) t) h
  rw [← R.add_assoc, neg_add, R.zero_add, add_zero] at h1
  exact h1

theorem mul_neg (R : RingSpec) (a b : R.Carrier) :
    R.mul a (R.neg b) = R.neg (R.mul a b) := by
  apply neg_unique
  rw [← R.mul_add, R.add_neg, mul_zero]

theorem neg_mul (R : RingSpec) (a b : R.Carrier) :
    R.mul (R.neg a) b = R.neg (R.mul a b) := by
  rw [R.mul_comm, mul_neg, R.mul_comm]

theorem neg_neg (R : RingSpec) (a : R.Carrier) : R.neg (R.neg a) = a :=
  (neg_unique R (R.neg a) a (neg_add R a)).symm

theorem neg_zero (R : RingSpec) : R.neg R.zero = R.zero :=
  (neg_unique R R.zero R.zero (R.zero_add R.zero)).symm

theorem neg_add_dist (R : RingSpec) (a b : R.Carrier) :
    R.neg (R.add a b) = R.add (R.neg a) (R.neg b) := by
  symm
  apply neg_unique
  calc
    R.add (R.add a b) (R.add (R.neg a) (R.neg b))
      = R.add (R.add a (R.neg a)) (R.add b (R.neg b)) := by
          rw [R.add_assoc, ← R.add_assoc b (R.neg a), R.add_comm b (R.neg a), R.add_assoc (R.neg a) b, ← R.add_assoc]
    _ = R.zero := by rw [R.add_neg, R.add_neg, R.zero_add]

theorem two_mul (R : RingSpec) (a : R.Carrier) : R.mul (two R) a = R.add a a := by
  unfold two
  rw [add_mul, R.one_mul]

/-- 有限列表求和辅助定义。 -/
def sumList (R : RingSpec) : List R.Carrier → R.Carrier
  | [] => R.zero
  | x :: xs => R.add x (sumList R xs)

theorem sumList_add (R : RingSpec) (xs : List α) (f g : α → R.Carrier) :
    R.add (sumList R (xs.map f)) (sumList R (xs.map g)) =
    sumList R (xs.map (fun x => R.add (f x) (g x))) := by
  induction xs with
  | nil =>
    change R.add R.zero R.zero = R.zero
    exact R.zero_add R.zero
  | cons a rest ih =>
    change R.add (R.add (f a) (sumList R (rest.map f))) (R.add (g a) (sumList R (rest.map g))) =
      R.add (R.add (f a) (g a)) (sumList R (rest.map (fun x => R.add (f x) (g x))))
    rw [← ih]
    rw [R.add_assoc (f a), ← R.add_assoc (sumList R (rest.map f)) (g a),
        R.add_comm (sumList R (rest.map f)) (g a), R.add_assoc (g a), ← R.add_assoc (f a)]

theorem sumList_zero (R : RingSpec) (xs : List α) :
    sumList R (xs.map (fun _ => R.zero)) = R.zero := by
  induction xs with
  | nil => rfl
  | cons _ rest ih =>
    change R.add R.zero (sumList R (rest.map (fun _ => R.zero))) = R.zero
    rw [ih, R.zero_add]

/-- ①a 横截内部共享面片全对接触交换总和定义（涵盖无奇异面片或横截交线反对称对消，不直接涵盖重合弯曲奇异面片）。 -/
def totalExchange (R : RingSpec) (nodes : List Nat) (F : Nat → Nat → R.Carrier) : R.Carrier :=
  sumList R (nodes.map (fun i => sumList R (nodes.map (fun j => F i j))))

/-- ①a 机证定理：满足对角零与反对称 F(i,j) + F(j,i) = 0 的有限控制体图总交换精确对消为零。 -/
theorem antisym_contact_exchange_cancels (R : RingSpec) (nodes : List Nat)
    (F : Nat → Nat → R.Carrier)
    (hDiag : ∀ i, F i i = R.zero)
    (hAnti : ∀ i j, R.add (F i j) (F j i) = R.zero) :
    totalExchange R nodes F = R.zero := by
  induction nodes with
  | nil => rfl
  | cons x xs ih =>
    unfold totalExchange at ih ⊢
    change R.add
        (R.add (F x x) (sumList R (xs.map (fun j => F x j))))
        (sumList R (xs.map (fun i => R.add (F i x) (sumList R (xs.map (fun j => F i j))))))
      = R.zero
    rw [hDiag x, R.zero_add]
    rw [← sumList_add R xs (fun i => F i x) (fun i => sumList R (xs.map (fun j => F i j)))]
    rw [ih, add_zero]
    rw [sumList_add R xs (fun j => F x j) (fun i => F i x)]
    have hFun : xs.map (fun j => R.add (F x j) (F j x)) = xs.map (fun _ => R.zero) := by
      clear ih
      induction xs with
      | nil => rfl
      | cons y ys ihy =>
        change R.add (F x y) (F y x) :: ys.map (fun j => R.add (F x j) (F j x)) =
          R.zero :: ys.map (fun _ => R.zero)
        rw [hAnti x y, ihy]
    rw [hFun, sumList_zero]

/-- ①b 机证定理（重合于奇异张力界面的合并控制体收支对消，对应层 2M 一.2）：
    当两侧体相进入界面片 Σ 的完整相对通量 F12、F21 与界面储量及周界线张力总收支 S_sigma 满足
    F12 + F21 + S_sigma = 0 时，合并区域总收支精确化归为两侧外边界通量 B1 + B2。 -/
theorem coincident_interface_merged_balance (R : RingSpec)
    (B1 B2 F12 F21 S_sigma : R.Carrier)
    (hJump : R.add (R.add F12 F21) S_sigma = R.zero) :
    R.add (R.add (R.add B1 F12) (R.add B2 F21)) S_sigma = R.add B1 B2 := by
  calc
    R.add (R.add (R.add B1 F12) (R.add B2 F21)) S_sigma
      = R.add (R.add (R.add B1 B2) (R.add F12 F21)) S_sigma := by
          apply congrArg (fun t => R.add t S_sigma)
          rw [R.add_assoc B1 F12 (R.add B2 F21), ← R.add_assoc F12 B2 F21,
              R.add_comm F12 B2, R.add_assoc B2 F12 F21, ← R.add_assoc B1 B2]
    _ = R.add (R.add B1 B2) (R.add (R.add F12 F21) S_sigma) := by
          rw [R.add_assoc]
    _ = R.add B1 B2 := by
          rw [hJump, add_zero]

/-- 一维总质量倍能量伽利略控制体状态（E2 表示 2E，在特征非 2 且 2 可逆的有序环或有序域上与 (M, Q, E) 同构）。 -/
structure GalileanState (R : RingSpec) where
  M : R.Carrier
  Q : R.Carrier
  E2 : R.Carrier

theorem galileanState_nonempty : Nonempty (GalileanState intRing) :=
  ⟨{ M := 2, Q := 4, E2 := 10 }⟩

def stateAdd (R : RingSpec) (s1 s2 : GalileanState R) : GalileanState R where
  M := R.add s1.M s2.M
  Q := R.add s1.Q s2.Q
  E2 := R.add s1.E2 s2.E2

def stateSmul (R : RingSpec) (c : R.Carrier) (s : GalileanState R) : GalileanState R where
  M := R.mul c s.M
  Q := R.mul c s.Q
  E2 := R.mul c s.E2

/-- 正面建设性公理结构体：带测度下界 V >= V_min > 0 与正质量 M > 0 守卫的正测度控制体盒。 -/
structure PositiveMeasureBox (R : RingSpec) where
  Vmin : R.Carrier
  V : R.Carrier
  state : GalileanState R
  hVminPos : R.lt R.zero Vmin
  hVolLower : R.le Vmin V
  hMassPos : R.lt R.zero state.M

/-- 构造性非空见证：显式给出满足全部正测度与正质量守卫的具体控制体实例（M=2, Q=4, E2=10）。 -/
theorem positiveMeasureBox_nonempty : Nonempty (PositiveMeasureBox intRing) :=
  ⟨{
    Vmin := 1
    V := 2
    state := { M := 2, Q := 4, E2 := 10 }
    hVminPos := by decide
    hVolLower := by decide
    hMassPos := by decide
  }⟩

theorem box_mass_ne_zero (R : RingSpec) (box : PositiveMeasureBox R) : box.state.M ≠ R.zero :=
  (lt_ne R R.zero box.state.M box.hMassPos).symm

theorem box_vol_pos (R : RingSpec) (box : PositiveMeasureBox R) : R.lt R.zero box.V :=
  R.lt_of_lt_of_le R.zero box.Vmin box.V box.hVminPos box.hVolLower

theorem box_vol_ne_zero (R : RingSpec) (box : PositiveMeasureBox R) : box.V ≠ R.zero :=
  (lt_ne R R.zero box.V (box_vol_pos R box)).symm

/-- 伽利略线性群表示（一维总质量倍能量特化）：G_w(M, Q, 2E) = (M, Q - M w, 2E - 2 w Q + M w^2)。 -/
def galileanBoost (R : RingSpec) (w : R.Carrier) (s : GalileanState R) : GalileanState R where
  M := s.M
  Q := sub R s.Q (R.mul s.M w)
  E2 := R.add (sub R s.E2 (R.mul (two R) (R.mul w s.Q))) (R.mul s.M (R.mul w w))

/-- ②a 单位元律：G_0(s) = s。 -/
theorem galilean_boost_zero (R : RingSpec) (s : GalileanState R) :
    galileanBoost R R.zero s = s := by
  cases s with
  | mk M Q E2 =>
    unfold galileanBoost sub
    simp only [GalileanState.mk.injEq]
    refine ⟨trivial, ?_, ?_⟩
    · rw [mul_zero, neg_zero, add_zero]
    · rw [zero_mul, mul_zero, neg_zero, add_zero, zero_mul, mul_zero, add_zero]

/-- ②b 线性表示加法律：G_w(s1 + s2) = G_w(s1) + G_w(s2)。 -/
theorem galilean_boost_linear_add (R : RingSpec) (w : R.Carrier) (s1 s2 : GalileanState R) :
    galileanBoost R w (stateAdd R s1 s2) = stateAdd R (galileanBoost R w s1) (galileanBoost R w s2) := by
  cases s1 with | mk M1 Q1 E1 =>
  cases s2 with | mk M2 Q2 E2 =>
  unfold galileanBoost stateAdd sub
  simp only [GalileanState.mk.injEq]
  refine ⟨trivial, ?_, ?_⟩
  · rw [add_mul, neg_add_dist]
    rw [R.add_assoc Q1 Q2, ← R.add_assoc Q2 (R.neg (R.mul M1 w)), R.add_comm Q2 (R.neg (R.mul M1 w)),
        R.add_assoc (R.neg (R.mul M1 w)) Q2, ← R.add_assoc Q1]
  · rw [R.mul_add w Q1 Q2, R.mul_add (two R), neg_add_dist, add_mul R M1 M2]
    let p1 := R.neg (R.mul (two R) (R.mul w Q1))
    let p2 := R.neg (R.mul (two R) (R.mul w Q2))
    let k1 := R.mul M1 (R.mul w w)
    let k2 := R.mul M2 (R.mul w w)
    change R.add (R.add (R.add E1 E2) (R.add p1 p2)) (R.add k1 k2) =
           R.add (R.add (R.add E1 p1) k1) (R.add (R.add E2 p2) k2)
    have hE : R.add (R.add E1 E2) (R.add p1 p2) = R.add (R.add E1 p1) (R.add E2 p2) := by
      rw [R.add_assoc E1 E2, ← R.add_assoc E2 p1, R.add_comm E2 p1, R.add_assoc p1 E2, ← R.add_assoc E1]
    rw [hE]
    rw [R.add_assoc (R.add E1 p1) (R.add E2 p2), ← R.add_assoc (R.add E2 p2) k1,
        R.add_comm (R.add E2 p2) k1, R.add_assoc k1 (R.add E2 p2), ← R.add_assoc (R.add E1 p1)]

/-- ②c 线性表示数乘律：G_w(c • s) = c • G_w(s)。 -/
theorem galilean_boost_linear_smul (R : RingSpec) (w c : R.Carrier) (s : GalileanState R) :
    galileanBoost R w (stateSmul R c s) = stateSmul R c (galileanBoost R w s) := by
  cases s with | mk M Q E2 =>
  unfold galileanBoost stateSmul sub
  simp only [GalileanState.mk.injEq]
  refine ⟨trivial, ?_, ?_⟩
  · rw [R.mul_add, mul_neg, R.mul_assoc c M w]
  · rw [R.mul_add, R.mul_add, mul_neg, R.mul_assoc c M (R.mul w w)]
    have hCross : R.mul (two R) (R.mul w (R.mul c Q)) = R.mul c (R.mul (two R) (R.mul w Q)) := by
      rw [← R.mul_assoc w c Q, R.mul_comm w c, R.mul_assoc c w Q, ← R.mul_assoc (two R) c,
          R.mul_comm (two R) c, R.mul_assoc c (two R)]
    rw [hCross]

/-- ②d 群复合律：G_{w1} (G_{w2} s) = G_{w1 + w2} s。 -/
theorem galilean_group_composition (R : RingSpec) (w1 w2 : R.Carrier) (s : GalileanState R) :
    galileanBoost R w1 (galileanBoost R w2 s) = galileanBoost R (R.add w1 w2) s := by
  cases s with
  | mk M Q E2 =>
    unfold galileanBoost sub
    simp only [GalileanState.mk.injEq]
    refine ⟨trivial, ?_, ?_⟩
    · rw [R.mul_add, neg_add_dist, R.add_assoc, R.add_comm (R.neg (R.mul M w2)) (R.neg (R.mul M w1))]
    · have hQterm :
          R.neg (R.mul (two R) (R.mul w1 (R.add Q (R.neg (R.mul M w2))))) =
          R.add (R.neg (R.mul (two R) (R.mul w1 Q))) (R.mul (two R) (R.mul M (R.mul w1 w2))) := by
        rw [R.mul_add, mul_neg, R.mul_add, mul_neg, neg_add_dist, neg_neg]
        have hperm : R.mul w1 (R.mul M w2) = R.mul M (R.mul w1 w2) := by
          rw [← R.mul_assoc, R.mul_comm w1 M, R.mul_assoc]
        rw [hperm]
      have hWsumQ :
          R.neg (R.mul (two R) (R.mul (R.add w1 w2) Q)) =
          R.add (R.neg (R.mul (two R) (R.mul w1 Q))) (R.neg (R.mul (two R) (R.mul w2 Q))) := by
        rw [add_mul, R.mul_add, neg_add_dist]
      have hWsumSq :
          R.mul M (R.mul (R.add w1 w2) (R.add w1 w2)) =
          R.add (R.add (R.mul M (R.mul w1 w1)) (R.mul M (R.mul w2 w2)))
                (R.mul (two R) (R.mul M (R.mul w1 w2))) := by
        have hSq :
            R.mul (R.add w1 w2) (R.add w1 w2) =
            R.add (R.add (R.mul w1 w1) (R.mul w2 w2)) (R.add (R.mul w1 w2) (R.mul w1 w2)) := by
          rw [add_mul, R.mul_add, R.mul_add, R.mul_comm w2 w1]
          rw [R.add_assoc (R.mul w1 w1), ← R.add_assoc (R.mul w1 w2) (R.mul w1 w2) (R.mul w2 w2),
              R.add_comm (R.add (R.mul w1 w2) (R.mul w1 w2)) (R.mul w2 w2),
              ← R.add_assoc (R.mul w1 w1) (R.mul w2 w2)]
        rw [hSq, R.mul_add, R.mul_add, R.mul_add, two_mul]
      rw [hQterm, hWsumQ, hWsumSq]
      let p1 := R.neg (R.mul (two R) (R.mul w2 Q))
      let p2 := R.mul M (R.mul w2 w2)
      let p3 := R.neg (R.mul (two R) (R.mul w1 Q))
      let p4 := R.mul (two R) (R.mul M (R.mul w1 w2))
      let p5 := R.mul M (R.mul w1 w1)
      change R.add (R.add (R.add (R.add E2 p1) p2) (R.add p3 p4)) p5 =
             R.add (R.add E2 (R.add p3 p1)) (R.add (R.add p5 p2) p4)
      calc
        R.add (R.add (R.add (R.add E2 p1) p2) (R.add p3 p4)) p5
          = R.add (R.add (R.add E2 p1) (R.add p3 (R.add p2 p4))) p5 := by
              rw [R.add_assoc (R.add E2 p1) p2 (R.add p3 p4),
                  ← R.add_assoc p2 p3 p4, R.add_comm p2 p3, R.add_assoc p3 p2 p4]
        _ = R.add (R.add E2 (R.add p1 p3)) (R.add (R.add p2 p4) p5) := by
              rw [← R.add_assoc (R.add E2 p1) p3 (R.add p2 p4),
                  R.add_assoc E2 p1 p3, R.add_assoc (R.add E2 (R.add p1 p3))]
        _ = R.add (R.add E2 (R.add p3 p1)) (R.add (R.add p5 p2) p4) := by
              rw [R.add_comm p1 p3, R.add_comm (R.add p2 p4) p5, ← R.add_assoc p5 p2 p4]

/-- 缩放内能不变量 I(s) = M * (2E) - Q^2 = 2 M E_int。 -/
def scaledInternalEnergy (R : RingSpec) (s : GalileanState R) : R.Carrier :=
  sub R (R.mul s.M s.E2) (R.mul s.Q s.Q)

/-- ③a 机证定理（缩放内能伽利略不变性）：对任意正测度控制体，I(G_w(s)) = I(s)。 -/
theorem internal_energy_galilean_invariant (R : RingSpec) (box : PositiveMeasureBox R) (w : R.Carrier) :
    scaledInternalEnergy R (galileanBoost R w box.state) = scaledInternalEnergy R box.state := by
  let M := box.state.M
  let Q := box.state.Q
  let E2 := box.state.E2
  unfold scaledInternalEnergy galileanBoost sub
  change R.add
      (R.mul M (R.add (R.add E2 (R.neg (R.mul (two R) (R.mul w Q)))) (R.mul M (R.mul w w))))
      (R.neg (R.mul (R.add Q (R.neg (R.mul M w))) (R.add Q (R.neg (R.mul M w)))))
    = R.add (R.mul M E2) (R.neg (R.mul Q Q))
  let tCross := R.mul (two R) (R.mul M (R.mul w Q))
  let tSq := R.mul M (R.mul M (R.mul w w))
  have hLeft :
      R.mul M (R.add (R.add E2 (R.neg (R.mul (two R) (R.mul w Q)))) (R.mul M (R.mul w w))) =
      R.add (R.mul M E2) (R.add (R.neg tCross) tSq) := by
    rw [R.mul_add, R.mul_add, mul_neg]
    have hC : R.mul M (R.mul (two R) (R.mul w Q)) = tCross := by
      rw [← R.mul_assoc, R.mul_comm M (two R), R.mul_assoc]
    rw [hC, R.add_assoc]
  have hRight :
      R.neg (R.mul (R.add Q (R.neg (R.mul M w))) (R.add Q (R.neg (R.mul M w)))) =
      R.add (R.neg (R.mul Q Q)) (R.add tCross (R.neg tSq)) := by
    rw [add_mul, R.mul_add, R.mul_add, mul_neg, neg_mul, mul_neg, neg_mul, neg_neg]
    have h1 : R.mul Q (R.mul M w) = R.mul M (R.mul w Q) := by
      rw [R.mul_comm Q (R.mul M w), R.mul_assoc, R.mul_comm w Q]
    have h2 : R.mul (R.mul M w) Q = R.mul M (R.mul w Q) := by
      rw [R.mul_assoc]
    have h3 : R.mul (R.mul M w) (R.mul M w) = tSq := by
      rw [R.mul_assoc, ← R.mul_assoc w M w, R.mul_comm w M, R.mul_assoc M w w]
    rw [h1, h2, h3]
    rw [neg_add_dist, neg_add_dist, neg_add_dist, neg_neg]
    have hPair : R.add (R.mul M (R.mul w Q)) (R.mul M (R.mul w Q)) = tCross :=
      (two_mul R (R.mul M (R.mul w Q))).symm
    rw [R.add_assoc (R.neg (R.mul Q Q)), ← R.add_assoc (R.mul M (R.mul w Q)) (R.mul M (R.mul w Q)), hPair]
  rw [hLeft, hRight]
  let a := R.mul M E2
  let b := R.neg (R.mul Q Q)
  change R.add (R.add a (R.add (R.neg tCross) tSq)) (R.add b (R.add tCross (R.neg tSq))) = R.add a b
  have hCancel : R.add (R.add (R.neg tCross) tSq) (R.add tCross (R.neg tSq)) = R.zero := by
    calc
      R.add (R.add (R.neg tCross) tSq) (R.add tCross (R.neg tSq))
        = R.add (R.add (R.neg tCross) tCross) (R.add tSq (R.neg tSq)) := by
            rw [R.add_assoc (R.neg tCross), ← R.add_assoc tSq tCross, R.add_comm tSq tCross,
                R.add_assoc tCross tSq, ← R.add_assoc (R.neg tCross)]
      _ = R.zero := by rw [neg_add, R.add_neg, R.zero_add]
  calc
    R.add (R.add a (R.add (R.neg tCross) tSq)) (R.add b (R.add tCross (R.neg tSq)))
      = R.add (R.add a b) (R.add (R.add (R.neg tCross) tSq) (R.add tCross (R.neg tSq))) := by
          rw [R.add_assoc a, ← R.add_assoc (R.add (R.neg tCross) tSq) b,
              R.add_comm (R.add (R.neg tCross) tSq) b, R.add_assoc b, ← R.add_assoc a b]
    _ = R.add a b := by rw [hCancel, add_zero]

/-- ③b 机证定理（质心系存在唯一性与内能分离）：
    在显式给出正质量逆元 M_inv（M * M_inv = 1，对应域上正质量可逆前提）时，
    u = Q * M_inv 是唯一使动量为零的质心速度，且质心系倍内能精确满足 M * E2_com = I(s)。 -/
theorem galilean_com_frame_unique (R : RingSpec) (box : PositiveMeasureBox R)
    (M_inv : R.Carrier) (hInv : R.mul box.state.M M_inv = R.one) :
    let u := R.mul box.state.Q M_inv
    (galileanBoost R u box.state).Q = R.zero ∧
    (∀ w : R.Carrier, (galileanBoost R w box.state).Q = R.zero → w = u) ∧
    R.mul box.state.M (galileanBoost R u box.state).E2 = scaledInternalEnergy R box.state := by
  dsimp only
  let u := R.mul box.state.Q M_inv
  have hQzero : (galileanBoost R u box.state).Q = R.zero := by
    unfold galileanBoost sub
    dsimp only
    have hMu : R.mul box.state.M u = box.state.Q := by
      calc
        R.mul box.state.M (R.mul box.state.Q M_inv)
          = R.mul (R.mul box.state.M M_inv) box.state.Q := by
              rw [← R.mul_assoc, R.mul_comm box.state.M box.state.Q, R.mul_assoc,
                  R.mul_comm box.state.Q (R.mul box.state.M M_inv)]
        _ = box.state.Q := by rw [hInv, R.one_mul]
    rw [hMu, R.add_neg]
  refine ⟨hQzero, ?_, ?_⟩
  · intro w hw
    unfold galileanBoost sub at hw
    dsimp only at hw
    have hMw : R.mul box.state.M w = box.state.Q := by
      have h1 := congrArg (fun t => R.add t (R.mul box.state.M w)) hw
      rw [R.add_assoc, neg_add, add_zero, R.zero_add] at h1
      exact h1.symm
    have h2 := congrArg (fun t => R.mul t M_inv) hMw
    calc
      w = R.mul R.one w := (R.one_mul w).symm
      _ = R.mul (R.mul box.state.M M_inv) w := by rw [hInv]
      _ = R.mul (R.mul box.state.M w) M_inv := by
            rw [R.mul_assoc, R.mul_comm M_inv w, ← R.mul_assoc]
      _ = R.mul box.state.Q M_inv := h2
  · have hInvE := internal_energy_galilean_invariant R box u
    unfold scaledInternalEnergy sub at hInvE
    rw [hQzero, mul_zero, neg_zero, add_zero] at hInvE
    exact hInvE

/-- ③c 非平凡数值质心系见证：M=2, Q=4, E2=10 经质心速度 w=2 变换后动量归零且 I=4 不变。 -/
theorem positiveMeasureBox_com_witness :
    let s : GalileanState intRing := { M := 2, Q := 4, E2 := 10 }
    (galileanBoost intRing 2 s).Q = 0 ∧
    scaledInternalEnergy intRing (galileanBoost intRing 2 s) = 4 ∧
    scaledInternalEnergy intRing s = 4 :=
  ⟨by decide, by decide, by decide⟩

/-- ④ 机证定理（不交两块子域平行轴动能恒等式，清分母精确形式）：
    M1 M2 I(s1 + s2) = M2 (M1 + M2) I(s1) + M1 (M1 + M2) I(s2) + (M2 Q1 - M1 Q2)^2。 -/
theorem parallel_axis_internal_energy (R : RingSpec) (s1 s2 : GalileanState R) :
    R.mul (R.mul s1.M s2.M)
      (scaledInternalEnergy R (stateAdd R s1 s2))
    = R.add
        (R.add
          (R.mul (R.mul s2.M (R.add s1.M s2.M)) (scaledInternalEnergy R s1))
          (R.mul (R.mul s1.M (R.add s1.M s2.M)) (scaledInternalEnergy R s2)))
        (R.mul (sub R (R.mul s2.M s1.Q) (R.mul s1.M s2.Q))
               (sub R (R.mul s2.M s1.Q) (R.mul s1.M s2.Q))) := by
  let M1 := s1.M; let M2 := s2.M; let Q1 := s1.Q; let Q2 := s2.Q; let E1 := s1.E2; let E2 := s2.E2
  let Eterm := R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) (R.add E1 E2)
  let q11 := R.mul (R.mul M1 M2) (R.mul Q1 Q1)
  let q22 := R.mul (R.mul M1 M2) (R.mul Q2 Q2)
  let q12 := R.mul (R.mul M1 M2) (R.mul Q1 Q2)
  let d1 := R.mul (R.mul M2 M2) (R.mul Q1 Q1)
  let d2 := R.mul (R.mul M1 M1) (R.mul Q2 Q2)
  unfold scaledInternalEnergy stateAdd sub
  have hLHS :
      R.mul (R.mul M1 M2) (R.add (R.mul (R.add M1 M2) (R.add E1 E2)) (R.neg (R.mul (R.add Q1 Q2) (R.add Q1 Q2)))) =
      R.add Eterm (R.add (R.add (R.neg q11) (R.neg q22)) (R.add (R.neg q12) (R.neg q12))) := by
    rw [R.mul_add, ← R.mul_assoc, mul_neg]
    have hQ :
        R.mul (R.mul M1 M2) (R.mul (R.add Q1 Q2) (R.add Q1 Q2)) =
        R.add (R.add q11 q22) (R.add q12 q12) := by
      have hQsq :
          R.mul (R.add Q1 Q2) (R.add Q1 Q2) =
          R.add (R.add (R.mul Q1 Q1) (R.mul Q2 Q2)) (R.add (R.mul Q1 Q2) (R.mul Q1 Q2)) := by
        rw [add_mul, R.mul_add, R.mul_add, R.mul_comm Q2 Q1]
        rw [R.add_assoc (R.mul Q1 Q1), ← R.add_assoc (R.mul Q1 Q2) (R.mul Q1 Q2) (R.mul Q2 Q2),
            R.add_comm (R.add (R.mul Q1 Q2) (R.mul Q1 Q2)) (R.mul Q2 Q2),
            ← R.add_assoc (R.mul Q1 Q1) (R.mul Q2 Q2)]
      rw [hQsq, R.mul_add, R.mul_add, R.mul_add]
    rw [hQ, neg_add_dist, neg_add_dist, neg_add_dist]
  have hR1 :
      R.mul (R.mul M2 (R.add M1 M2)) (R.add (R.mul M1 E1) (R.neg (R.mul Q1 Q1))) =
      R.add (R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) E1) (R.add (R.neg q11) (R.neg d1)) := by
    rw [R.mul_add, mul_neg]
    have hE : R.mul (R.mul M2 (R.add M1 M2)) (R.mul M1 E1) =
        R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) E1 := by
      rw [← R.mul_assoc, R.mul_comm (R.mul M2 (R.add M1 M2)) M1, ← R.mul_assoc M1 M2 (R.add M1 M2)]
    have hQ : R.mul (R.mul M2 (R.add M1 M2)) (R.mul Q1 Q1) = R.add q11 d1 := by
      rw [R.mul_add, add_mul, R.mul_comm M2 M1]
    rw [hE, hQ, neg_add_dist]
  have hR2 :
      R.mul (R.mul M1 (R.add M1 M2)) (R.add (R.mul M2 E2) (R.neg (R.mul Q2 Q2))) =
      R.add (R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) E2) (R.add (R.neg d2) (R.neg q22)) := by
    rw [R.mul_add, mul_neg]
    have hE : R.mul (R.mul M1 (R.add M1 M2)) (R.mul M2 E2) =
        R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) E2 := by
      rw [← R.mul_assoc, R.mul_assoc M1 (R.add M1 M2) M2, R.mul_comm (R.add M1 M2) M2, ← R.mul_assoc M1 M2 (R.add M1 M2)]
    have hQ : R.mul (R.mul M1 (R.add M1 M2)) (R.mul Q2 Q2) = R.add d2 q22 := by
      rw [R.mul_add, add_mul]
    rw [hE, hQ, neg_add_dist]
  have hR3 :
      R.mul (R.add (R.mul M2 Q1) (R.neg (R.mul M1 Q2))) (R.add (R.mul M2 Q1) (R.neg (R.mul M1 Q2))) =
      R.add (R.add d1 d2) (R.add (R.neg q12) (R.neg q12)) := by
    rw [add_mul, R.mul_add, R.mul_add, mul_neg, neg_mul, mul_neg, neg_mul, neg_neg]
    have t1 : R.mul (R.mul M2 Q1) (R.mul M2 Q1) = d1 := by
      rw [R.mul_assoc, ← R.mul_assoc Q1 M2 Q1, R.mul_comm Q1 M2, R.mul_assoc M2 Q1 Q1, ← R.mul_assoc]
    have t2 : R.mul (R.mul M2 Q1) (R.mul M1 Q2) = q12 := by
      rw [R.mul_assoc, ← R.mul_assoc Q1 M1 Q2, R.mul_comm Q1 M1, R.mul_assoc M1 Q1 Q2, ← R.mul_assoc, R.mul_comm M2 M1]
    have t3 : R.mul (R.mul M1 Q2) (R.mul M2 Q1) = q12 := by
      rw [R.mul_comm (R.mul M1 Q2) (R.mul M2 Q1), t2]
    have t4 : R.mul (R.mul M1 Q2) (R.mul M1 Q2) = d2 := by
      rw [R.mul_assoc, ← R.mul_assoc Q2 M1 Q2, R.mul_comm Q2 M1, R.mul_assoc M1 Q2 Q2, ← R.mul_assoc]
    rw [t1, t2, t3, t4]
    rw [R.add_assoc d1 (R.neg q12), ← R.add_assoc (R.neg q12) (R.neg q12) d2,
        R.add_comm (R.add (R.neg q12) (R.neg q12)) d2, ← R.add_assoc d1 d2]
  rw [hLHS, hR1, hR2, hR3]
  have hEmerge :
      R.add (R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) E1)
            (R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) E2) = Eterm :=
    (R.mul_add (R.mul (R.mul M1 M2) (R.add M1 M2)) E1 E2).symm
  let a1 := R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) E1
  let a2 := R.mul (R.mul (R.mul M1 M2) (R.add M1 M2)) E2
  let tailQ := R.add (R.neg q12) (R.neg q12)
  change R.add Eterm (R.add (R.add (R.neg q11) (R.neg q22)) tailQ) =
         R.add (R.add (R.add a1 (R.add (R.neg q11) (R.neg d1)))
                      (R.add a2 (R.add (R.neg d2) (R.neg q22))))
               (R.add (R.add d1 d2) tailQ)
  have hMergeStep :
      R.add (R.add a1 (R.add (R.neg q11) (R.neg d1))) (R.add a2 (R.add (R.neg d2) (R.neg q22))) =
      R.add Eterm (R.add (R.add (R.neg q11) (R.neg q22)) (R.add (R.neg d1) (R.neg d2))) := by
    calc
      R.add (R.add a1 (R.add (R.neg q11) (R.neg d1))) (R.add a2 (R.add (R.neg d2) (R.neg q22)))
        = R.add (R.add a1 a2) (R.add (R.add (R.neg q11) (R.neg d1)) (R.add (R.neg d2) (R.neg q22))) := by
            rw [R.add_assoc a1, ← R.add_assoc (R.add (R.neg q11) (R.neg d1)) a2,
                R.add_comm (R.add (R.neg q11) (R.neg d1)) a2, R.add_assoc a2, ← R.add_assoc a1 a2]
      _ = R.add Eterm (R.add (R.add (R.neg q11) (R.neg q22)) (R.add (R.neg d1) (R.neg d2))) := by
            rw [hEmerge]
            apply congrArg (R.add Eterm)
            rw [R.add_comm (R.neg d2) (R.neg q22)]
            rw [R.add_assoc (R.neg q11), ← R.add_assoc (R.neg d1) (R.neg q22),
                R.add_comm (R.neg d1) (R.neg q22), R.add_assoc (R.neg q22), ← R.add_assoc (R.neg q11)]
  rw [hMergeStep]
  have hCancelD : R.add (R.add (R.neg d1) (R.neg d2)) (R.add d1 d2) = R.zero := by
    calc
      R.add (R.add (R.neg d1) (R.neg d2)) (R.add d1 d2)
        = R.add (R.add (R.neg d1) d1) (R.add (R.neg d2) d2) := by
            rw [R.add_assoc (R.neg d1), ← R.add_assoc (R.neg d2) d1,
                R.add_comm (R.neg d2) d1, R.add_assoc d1, ← R.add_assoc (R.neg d1)]
      _ = R.zero := by rw [neg_add, neg_add, R.zero_add]
  rw [R.add_assoc Eterm]
  apply congrArg (R.add Eterm)
  rw [R.add_assoc (R.add (R.neg q11) (R.neg q22)),
      ← R.add_assoc (R.add (R.neg d1) (R.neg d2)) (R.add d1 d2) tailQ,
      hCancelD, R.zero_add]

/-- 相对面通量构造：由独立通量元组 F 与状态密度 U 及法向速度 un 构造 F - un * U。 -/
def relativeFlux (R : RingSpec) (un : R.Carrier) (U F : GalileanState R) : GalileanState R where
  M := sub R F.M (R.mul un U.M)
  Q := sub R F.Q (R.mul un U.Q)
  E2 := sub R F.E2 (R.mul un U.E2)

/-- ⑤ 机证定理（由独立通量元组经 galileanBoost 显式推出共动通量纯化，统一 E2=2E 尺度）：
    给定任意独立面通量 F 与状态密度 U，记相对通量 F_rel = F - un * U。
    当相对质量通量为零和扩散通量之和 ∑ j_a = 0 时，直接对 F_rel 施加同一伽利略变换 G_u，
    逐分量精确化归为：零总质量通量、压应力牵引 F_rel.Q 与去功后的倍共动内能流 F_rel.E2 - 2 u F_rel.Q。 -/
theorem comoving_flux_cancels_translational_work (R : RingSpec)
    (diffMassFluxes : List R.Carrier) (u un : R.Carrier) (U F : GalileanState R)
    (hMassRel : (relativeFlux R un U F).M = sumList R diffMassFluxes)
    (hZeroSum : sumList R diffMassFluxes = R.zero) :
    galileanBoost R u (relativeFlux R un U F) =
      { M := R.zero
        Q := (relativeFlux R un U F).Q
        E2 := sub R (relativeFlux R un U F).E2 (R.mul (two R) (R.mul u (relativeFlux R un U F).Q)) } := by
  have hM0 : (relativeFlux R un U F).M = R.zero := by rw [hMassRel, hZeroSum]
  unfold galileanBoost sub
  simp only [GalileanState.mk.injEq]
  refine ⟨hM0, ?_, ?_⟩
  · rw [hM0, zero_mul, neg_zero, add_zero]
  · rw [hM0, zero_mul, add_zero]

/-- ⑤b 推论（特化至标准体相分解 F_rel = (0, Pn, 2(u*Pn + jInt))）：
    直接由 galileanBoost 精确消去平动机械功 2 u Pn，得到 (0, Pn, 2 jInt)。 -/
theorem comoving_flux_standard_bulk_purification (R : RingSpec)
    (diffMassFluxes : List R.Carrier) (u un Pn jInt : R.Carrier) (U F : GalileanState R)
    (hMassRel : (relativeFlux R un U F).M = sumList R diffMassFluxes)
    (hZeroSum : sumList R diffMassFluxes = R.zero)
    (hMomRel : (relativeFlux R un U F).Q = Pn)
    (hEnergyRel : (relativeFlux R un U F).E2 = R.add (R.mul (two R) (R.mul u Pn)) (R.mul (two R) jInt)) :
    galileanBoost R u (relativeFlux R un U F) =
      { M := R.zero, Q := Pn, E2 := R.mul (two R) jInt } := by
  rw [comoving_flux_cancels_translational_work R diffMassFluxes u un U F hMassRel hZeroSum]
  rw [hMomRel, hEnergyRel]
  unfold sub
  simp only [GalileanState.mk.injEq]
  refine ⟨trivial, trivial, ?_⟩
  rw [R.add_comm (R.mul (two R) (R.mul u Pn)) (R.mul (two R) jInt), R.add_assoc, R.add_neg, add_zero]

end CaeAxioms.GalileanBox
