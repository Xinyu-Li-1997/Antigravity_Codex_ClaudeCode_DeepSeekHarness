# 分层公开经典文献溯源与第三方边界声明

> **元层非前提声明（对齐第一层 `一.6` 与第二层 `2C·R2`）**：本文件仅作**概念溯源、标准术语锚定与独立查证参考**，不作为任何演绎证明的权威前提。公理审查中的命题真值与冲突归因一律由形式逻辑推演、纯核 Lean 4 证明与独立客观见证裁决，不诉诸人名、专著或机构背书。

---

## 一、四层公理分册的公开经典文献依据

下表按条款级单向无环图（DAG）顺序，列出各层核心定义与结构边界对应的国际公认公开经典教材与基础文献（仅作学术溯源与术语锚定）：

### 1. 第一层：多类经典逻辑、认识论与代理反身边界（`layer1-logic-epistemology.md`）

| 涉及条款与概念 | 公开经典文献依据 |
| --- | --- |
| 多类一阶逻辑、良构性、保守定义与语法/语义区分（零.1、一.1–一.4） | H. B. Enderton, *A Mathematical Introduction to Logic* (2nd ed., Academic Press, 2001); M. Manzano, *Extensions of First Order Logic* (Cambridge University Press, 1996); A. Tarski, "The Semantic Conception of Truth and the Foundations of Semantics", *Philosophy and Phenomenological Research*, 1944 |
| 空指称与预设的经典争论背景（一.2、一.8 与 `LogicCore.lean` 二值坍缩律） | B. Russell, "On Denoting", *Mind*, 1905; P. F. Strawson, "On Referring", *Mind*, 1950（二者对空指称持不同分析，此处标明争论背景） |
| 融贯与符合分离、检验整体论与不可充分决定性（三.1–三.5） | P. Duhem, *La Théorie Physique: Son Objet, Sa Structure* (Chevalier & Rivière, 1906); W. V. O. Quine, "Two Dogmas of Empiricism", *The Philosophical Review*, 1951; K. R. Popper, *The Logic of Scientific Discovery* (Hutchinson, 1959) |
| 能动反身性与非充分评价代理偏离（四.1–四.4，P4–P5 背景） | C. A. E. Goodhart, "Problems of Monetary Management: The U.K. Experience" (Reserve Bank of Australia 1975 conference paper; *Papers in Monetary Economics*, 1976); D. T. Campbell, "Assessing the Impact of Planned Social Change", *Evaluation and Program Planning*, 1979 |

### 2. 层 1.5：形式数学与有限计算底座（`layer1_5-math-computation.md`）

| 涉及条款与概念 | 公开经典文献依据 |
| --- | --- |
| 测度论、Lebesgue 密度点与 Schwartz 分布结合代数乘法不可能性（一.3、2A.1） | W. Rudin, *Real and Complex Analysis* (3rd ed., McGraw-Hill, 1987); L. Schwartz, "Sur l'impossibilité de la multiplication des distributions", *Comptes Rendus de l'Académie des Sciences*, 1954（无法在保持结合律与连续函数乘法及微分的代数中普遍相乘）; L. C. Evans, *Partial Differential Equations* (2nd ed., American Mathematical Society, 2010) |
| 散度型守恒律弱解、跳跃条件、非守恒路径依赖与适定性（2A.2–2A.3） | J. Hadamard, *Lectures on Cauchy's Problem in Linear Partial Differential Equations* (Yale University Press, 1923); P. D. Lax, *Hyperbolic Systems of Conservation Laws and the Mathematical Theory of Shock Waves* (Society for Industrial and Applied Mathematics, 1973); C. M. Dafermos, *Hyperbolic Conservation Laws in Continuum Physics* (4th ed., Springer, 2016); G. Dal Maso, P. G. LeFloch & F. Murat, "Definition and Weak Stability of Nonconservative Products", *Journal de Mathématiques Pures et Appliquées*, 1995 |
| Kolmogorov 线性逼近宽度、凸分析、隐函数定理与有限维多项式逼近（2A.4、2B.1–2B.3、三.1） | A. N. Kolmogorov, "Über die beste Annäherung von Funktionen einer gegebenen Funktionenklasse", *Annals of Mathematics*, 1936; R. T. Rockafellar, *Convex Analysis* (Princeton University Press, 1970); W. Rudin, *Principles of Mathematical Analysis* (3rd ed., McGraw-Hill, 1976, Theorem 9.28); R. A. DeVore & G. G. Lorentz, *Constructive Approximation* (Springer, 1993) |
| 有限精度浮点舍入扰动与数值稳定性界（三.C3、三.2） | N. J. Higham, *Accuracy and Stability of Numerical Algorithms* (2nd ed., Society for Industrial and Applied Mathematics, 2002) |

### 3. 层 2M：正测度理性力学与热力学公理（`layer2m-rational-mechanics.md`）

| 涉及条款与概念 | 公开经典文献依据 |
| --- | --- |
| 正测度控制体积分收支、奇异界面平衡与材料客观性/对称群（一.1–一.3） | C. Truesdell & W. Noll, *The Non-Linear Field Theories of Mechanics* (3rd ed., Springer, 2004); M. E. Gurtin, E. Fried & L. Anand, *The Mechanics and Thermodynamics of Continua* (Cambridge University Press, 2010); L. D. Landau & E. M. Lifshitz, *The Classical Theory of Fields* (4th ed., Butterworth-Heinemann, 1975) 与 *Fluid Mechanics* (2nd ed., Butterworth-Heinemann, 1987) |
| 平衡态凹熵势、一阶齐次性与复合上卷积/相变退化（二.1–二.2） | J. W. Gibbs, "On the Equilibrium of Heterogeneous Substances", *Transactions of the Connecticut Academy of Arts and Sciences*, 1876–1878; H. B. Callen, *Thermodynamics and an Introduction to Thermostatistics* (2nd ed., Wiley, 1985); A. S. Wightman, "Convexity and the Notion of Equilibrium State in Thermodynamics and Statistical Mechanics" (Introduction to R. B. Israel, *Convexity in the Theory of Lattice Gases*, Princeton University Press, 1979) |
| 不可逆熵产、Onsager-Casimir 互易与算子级双退化正交结构（三.1–三.3） | L. Onsager, "Reciprocal Relations in Irreversible Processes. I & II", *Physical Review*, 1931; H. B. G. Casimir, "On Onsager's Principle of Microscopic Reversibility", *Reviews of Modern Physics*, 1945; S. R. de Groot & P. Mazur, *Non-Equilibrium Thermodynamics* (North-Holland, 1962 / Dover, 1984); M. Grmela & H. C. Öttinger, "Dynamics and thermodynamics of complex fluids. I. Development of a general formalism", *Physical Review E*, 56(6), 6620, 1997; H. C. Öttinger & M. Grmela, "Dynamics and thermodynamics of complex fluids. II. Illustrations of a general formalism", *Physical Review E*, 56(6), 6633, 1997 |

### 4. 第二层：领域审查与反身生态分支（`layer2-domain-audit.md`）

| 涉及条款与概念 | 公开经典文献依据 |
| --- | --- |
| 校核与确认（Verification 与 Validation）分离与误差归因追溯（2A.4；2A.5 降阶总成本比较为本项目工程规范） | P. J. Roache, *Verification and Validation in Computational Science and Engineering* (Hermosa Publishers, 1998); W. L. Oberkampf & C. J. Roy, *Verification and Validation in Scientific Computing* (Cambridge University Press, 2010) |
| 书面命题评议与非形式谬误剥离（2B.1–2B.2） | I. M. Copi, C. Cohen & V. Rodych, *Introduction to Logic* (15th ed., Routledge, 2019) |
| 科学规范社会学背景、偏好学习实验与代理失真分类（2C.1–2C.3，R1–R3 背景） | 社会学背景：R. K. Merton, *The Sociology of Science: Theoretical and Empirical Investigations* (University of Chicago Press, 1973)；人类偏好强化学习实验：P. F. Christiano et al., "Deep Reinforcement Learning from Human Preferences", *Advances in Neural Information Processing Systems*, 2017；代理偏离分类预印本：D. Manheim & S. Garrabrant, "Categorizing Variants of Goodhart's Law", arXiv:1803.04585 (2018) |

---

## 二、原创来源与第三方组件边界声明

1. **维护者原创来源声明与本版分发范围**：据维护者记录，本技能随本仓库分发的公理文本（`references/`）、Python 3.11+ 标准库硬状态机（`scripts/axiom_gate.py` 与 `scripts/_axiom_lib/`）、受控真内核驱动（`KernelAudit.lean`）及四份零 Mathlib 纯核 Lean 4 骨架（`lean/*.lean`）均由作者独立编写并依 Apache License 2.0 发布。本版分发包未复制或内嵌任何第三方 Git 仓库的源码、证明库或提示词模板。
2. **外部证明工具链与第三层独立仓库隔离**：
   - **Lean 4 与 `elan` 工具链**：由 Lean 社区与 Lean Focused Research Organization 独立维护。Lean 4 主仓库采用 Apache License 2.0，`elan` 包清单声明为 `MIT OR Apache-2.0` 双许可。本仓库仅在用户自行安装并显式传入 `--run-lean` 或 `--wsl-lean` 时调用本机 `lean` 可执行文件，不分发也不内嵌 Lean 编译器或 Mathlib 库。
   - **第三层外部证明或数值工具仓库**：用户在具体科研或工程课题中可自行按需调用任何独立开源形式化证明工具、数值反例程序或第三方代码仓库作为第三层外部核验手段；本配置仓库不合并、不捆绑也不转授任何第三方项目，亦与相关外部仓库作者无官方隶属或背书关系。
3. **跨模块外部组件边界**：本仓库其余模块所适配或可选调用的外部独立项目（包括 CC Switch、Zotero、`zotero-mcp`、FreeTSA 公开根证书及四家官方客户端）的商标与第三方组件边界，统一见仓库根目录 `LEGAL.md` 第一节与第五节；仓库根目录 `NOTICE` 仅载本项目依 Apache License 2.0 第 4(d) 条提供的原创归属声明。若任何作者或权利人认为引用或表述存在疏漏，请依根目录 `LEGAL.md` 第六节程序联系维护者核实调整。
