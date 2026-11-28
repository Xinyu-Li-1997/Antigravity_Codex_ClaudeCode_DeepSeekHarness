---
name: cae-axioms
description: >
  面向外生目标工程、科学计算与理论建模的按需公理审查技能（分层软公理分册 + Python 硬公理状态机 + 零依赖纯核 Lean 4 真内核校验与净室复演协议）。
  仅在用户明确点名调用时按单向公理依赖序位加载执行审查，并支持经脱敏准入守卫反哺改进状态机与演进层。
---

# 公理审查技能与硬公理状态机（cae-axioms）

本技能提供一套正交配对的**分层公理法典**、**零依赖 Python 3.11+ 硬公理状态机**与**纯核 Lean 4 真内核校验驱动**。第一层作为零学科特例的**通用公理机器**（`--profile core`），适用于**外生目标**的系统配置、代码开发、软件架构、文稿论证与科研审查（若目标内生依赖于被评主体信念，可在 `core` 档完成结构与 `VERIFIED_NO_T` 审查，但禁止正向 $T$ 断言并须按需挂载第二层 `2C`）；第二层 `2B` 与 `2C` 跨档通用，层 1.5、层 2M 与第二层 `2A` 作为科学计算扩展档（`--profile cae`）按条款级单向依赖叠加挂载。日常常规编码不主动触发、不增设常驻钩子，**仅在用户明确点名调用时按需启用**。

## 单向公理依赖序位与按需分册

全套公理体系严格按**条款级单向无环图**（DAG）维护，严禁下层学科特例向顶层回灌：

- **条款级依赖边**：
  - `第一层（零.1、零.3、一节、五.1） -> 层 1.5`。
  - `第一层 + 层 1.5（一至二节） + 第二层 2A 前提条目 D1–D2 -> 层 2M`（`D1–D3` 不依赖层 2M）。
  - `第一层 + 层 1.5 + D1–D3 + 层 2M -> 2A 审查规范`。
  - `第一层 + T1（按需挂载层 1.5、2A 与 2M 冲突判据） -> 2B`，`第一层 + R1–R3 -> 2C`。

| 依赖序位 | 文件路径 | 状态与职责边界 |
| --- | --- | --- |
| 第一层 | [layer1-logic-epistemology.md](references/layer1-logic-epistemology.md) | 封板基线（`frozen`）：多类经典逻辑底座（一.1–一.9）、实质前提（P1–P5）、认识论与外生目标反身边界，仅承接严格逻辑反例驱动的原地修订 |
| 层 1.5 | [layer1_5-math-computation.md](references/layer1_5-math-computation.md) | 封板基线（`frozen`）：仅承接第一层零.1、零.3、一节与五.1，涵盖测度-微分化归、弱解间断、仿射或弯曲流形与附加约束四原型，零物理前提 |
| 层 2M | [layer2m-rational-mechanics.md](references/layer2m-rational-mechanics.md) | 演进态（`evolving`）：承接第一层、层 1.5（一至二节）与 `2A·D1–D2`，涵盖正测度积分收支、惯性对称群、凹熵势与算子级耗散正交，零离散前提 |
| 第二层 | [layer2-domain-audit.md](references/layer2-domain-audit.md) | 封板基线（`frozen`）：`2A` 经验物理（`D1–D3` 前置，规范 1–5 按需挂载 `2M`）、`2B` 书面命题评议、`2C` 反身生态与代理生成系统审查推演 |
| 文献溯源 | [layer-citations.md](references/layer-citations.md) | 元层非前提索引：逐层汇总国际公认公开经典教材与奠基文献（仅作术语锚定与溯源，不作诉诸权威前提），并明确外部仓库零内嵌边界 |

- **第三层外部硬出口**：具体课题的领域冲突树、确定性数值反例程序、项目私有台账以及纯数学长证明外部工具属于第三层外部资产，按用户指定路径只读挂载，绝不并入本配置仓库，亦不向本公理层回灌任何私有资产。
- **两类硬禁区与初等边界**：连续实分析与分布论前沿定理（例如一般弯曲曲面上的分布乘积、非光滑切换面正则性、Sobolev 空间不适定细节与 Kolmogorov 宽度渐近界）以及自然语言提取、实验传感器误差与外部生态反身博弈，不强行编入形式证明，而由状态机显式标签 `non_formalized_zones` 与外部证据槽管理。初等但未编入当前纯核骨架的命题（如隐函数非退化判定与凸拉回步长存在性）按标准数学定理或第三层外部证明助手核验。

## 硬公理状态机、真内核驱动与纯核 Lean 4 骨架

外层由薄 CLI 入口 [axiom_gate.py](scripts/axiom_gate.py) 与标准库内部包 `_axiom_lib/`（`contract`、`lean_gate`、`evidence`、`rules`、`case`、`replay` 及受控单文件真内核驱动 `KernelAudit.lean`）锁死单源严格 Schema、状态转移与净室复演验签；内层由 `lean/` 目录下四份零 Mathlib 纯核 Lean 4 骨架锁死代数与逻辑核心定理：

| 组件路径 | 确定性职责 |
| --- | --- |
| [scripts/axiom_gate.py](scripts/axiom_gate.py) | 薄 CLI 入口与非符号链接安全装载器，调度 `_axiom_lib/` 全部子命令与统一结构化退出码 |
| [lean/LogicCore.lean](lean/LogicCore.lean) | 精化子类型定义域守卫、一.4 外延语义三分与回锁、一.8 保编号互斥七分与并列三分、一.9 链截断与防消症/菱形反例及映射纤维不可分引理（40 条公开契约定理） |
| [lean/ConvexDirectSum.lean](lean/ConvexDirectSum.lean) | 参数化非退化有序交换环（附 `Int` 构造见证）、幂等直和分解与核方向保投影分量、两点加权二次偏差不等式、双极限矛盾及标量泛函双向反例 |
| [lean/GalileanBox.lean](lean/GalileanBox.lean) | 非退化有序交换环与正测度控制体守卫、横截交换与重合奇异界面合并对消、一维伽利略线性表示与质心系唯一性、平行轴恒等式及共动通量纯化（洛伦兹母体与热力学二节未形式化） |
| [lean/DissipationGeneric.lean](lean/DissipationGeneric.lean) | 非退化有序交换环、反对称零二次型、对称半正定有限差非负熵产及算子级双退化正交（含三维双活跃见证；Onsager-Casimir 宇称互易未形式化） |

### 双档单向短路拓扑与根层吸收态

状态机按 `--profile` 严格隔离通用第一层与科学计算扩展档；全部关口载荷受单源严格 Schema 约束（拒未知字段、重复键、非标准数值、布尔冒充整数及字符串真值强转），且推进前从前驱记录重建并核验 `stage_digest` 链与冲突历史哈希链，杜绝跳步：

**通用第一层档（`--profile core`，零物理与 CAE 字段，仅依赖 `LogicCore.lean` 40 条定理）**：

1. **`L0_WELLFORMED`**（一.1–一.3 良构与保守定义）：核验载体与命题分离、前置类别或接口守卫（支持三类编码体制 `static_multisort`、`refined_subtype`、`base_sort_predicate`）及定义的新颖性、非循环性与 $\exists!$ 存在唯一性。
2. **`L1_ROOT_CONSISTENCY`**（一.4 + 一.9 根层可满足与有限模块 DAG）：核验根前提可满足性、共断言有限模块名唯一性、`depends_on` 引用完整与 DAG 无环性（检测到循环依赖直接锁入 `HALT_ROOT_CONFLICT`）、多组件跨文件边界规范化属性对账（`boundary_contracts`）、备选分支隔离与防消症伪相容。
3. **`L2_CONSTRAINT_ROLE`**（一.6–一.8 七分角色、并列律与比较基准）：核验欠封闭基准显式化、每条规则或测试指标对目标 $T$ 的保编号七分、禁 `unclassified` 放行与越权升格、逻辑并列禁虚构高低、比较三要素及反伪折中；仅纯欠封闭转入 `HOLD_L2` 后开放 `declare_undetermined`。
4. **`L3_EMPIRICAL_EXIT`**（三.1–三.5 + 四.1–四.4 实证防循环与反身退出）：强制分离内部校核（融贯）与外部独立实测（符合）、拦截构造与评估输入泄漏、核验三.4 防空真与非循环确立、要求非空外生性判定依据 `target_exogeneity_basis`（内生目标强制标记 `reflexive_ecology` 并提示挂载 `2C`），并同等拒收未编译 `ast_only` 定理充当外部执行器锚点。

**科学计算扩展档（`--profile cae`，面向科学计算与力学建模的独立五关链，内嵌第一层核心判据并叠加层 1.5、层 2M 与第二层，默认档位；通用软件保守定义与模块 DAG 检查请用 `--profile core`）**：

1. **`S0_TYPING`**（偏算子定义域守卫与一.2 编码体制）：核验正质量与正测度下界等定义域守卫；按 `encoding_regime` 区分 `ILL_FORMED_ROOT` 与 `UNSAT_ROOT_CONFLICT`。
2. **`S1_ROOT_MEASURE`**（根层测度-微分化归）：核验收缩极限与正测度下界相容性、奇异界面测度乘积良构性与非线性平均不对易。
3. **`S2_SYMMETRY_JUMP`**（对称群与间断跳跃）：核验同阶惯性时空对称群一致性，以及散度弱解等价性与 $m+1$ 间断未知量超定计数。
4. **`S3_CLOSURE_CONSTRAINT`**（闭合基准与约束原型）：核验欠封闭基准显式化、附加约束原型（`I`、`II`、`III`、`IV.1`、`IV.2`）与一.8 保编号七分角色，拦截 `IV.1` 非退化弯曲封闭冲突（无论单开或与 `II` 同开）、偶宇称可逆项挂耗散不等式及凹熵共轭符号矛盾；仅纯欠封闭 `MISSING_CLOSURE_BASIS` 在 `HOLD_S3` 开放 `declare_undetermined`。
5. **`S4_LOCAL_SPECTRAL_VV`**（下位局部适定与校核确认）：仅在 `S0` 至 `S3` 全通过后开放，核验零点/残差/误差三分、校核与确认分离、标定与确认集互斥、外生性依据 `target_exogeneity_basis`、反身退出三支（同等拒收 `ast_only`）及代理或降阶模型离线总成本。

一旦在根层关口（`L0`/`L1` 或 `S0`–`S2`）确立冲突，状态机立即锁入吸收态 **`HALT_ROOT_CONFLICT`**。单案由跨平台内核文件锁（`msvcrt.locking` / `fcntl.flock`）保护，且文件漂移绝不解除锁定。根冲突合法出边均依一.4 同一律回锁，支持子单写入前后半段中断后的事务幂等续交，且子单永久继承不可变 `root_claim`、`immutable_conflict_history` 与 `effective_claim_chain`：
- `rebase_ontology`：更换构造本体重新立项，可选 `--emit-rebased-case` 生成新子单；
- `downgrade_claim`：显式声明剥离范围与新命题，可选 `--emit-downgraded-case` 生成新降格子单（原命题永久保持 `ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED`，报告页眉强制提示原命题不可满足）。

另在非根冲突的纯欠封闭挂起态（`HOLD_L2` / `HOLD_S3`，且仅有 `MISSING_CLOSURE_BASIS`）下，单独开放 `declare_undetermined` 出边以如实声明语义未定。

### 双正交轴三档终态、双哈希精准失活与净室 `packet_replay`

状态机将**核验完备度轴**（`STRUCTURE_ONLY` vs `VERIFIED`）与**目标断言轴**（`NO_T_ASSERTION` vs `T_ASSERTED`）正交分离，对外通过终态严格分为三档：

1. **`STRUCTURE_PASSED`**（`STRUCTURE_ONLY_UNVERIFIED`）：常规离线 `advance` 走完全部关口后的默认终态；报告显著注明未经独立净室复演，绝不输出 `PASSED_ALL_GATES`，`report --require-verified` 硬返退出码 `2`。
2. **`VERIFIED_NO_T`**（`VERIFIED_NO_T_ASSERTION`）：**缺乏充分证明或有限域穷举证书的通用外生工程案卷最高可达终态**。须通过全单净室 `packet_replay` 逐关一致与盲推比对验签，无未编译 `ast_only` 依赖，不含离线自报探针，且具备当轮实跑探针或真内核编译验签。因仅具有限样本探针（`finite_probe_only` / `falsification_survived_only`）、非充分退出支或内生目标，诚实不对目标 $T$ 下全称肯定断言。
3. **`VERIFIED_T_ASSERTED`**（有效命题 `effective_claim_status` 为 `PASSED_ALL_GATES`；降格或重构子单的 `original_claim_status` 永久锁为 `ORIGINAL_UNSAT_DOWNGRADED_OR_REBASED`）：全核验闭环叠加外生目标，充分证书必须由 `acceptance_witnesses` 本体承载两类载体之一。其一为 **`exhaustive_domain`**（实跑探针穷举有限可达域 $X_S$ 并锁 `domain_spec_file` 哈希），其二为 **`artifact_bound_lean`**（经真内核编译且绑定被审工件的专属 Lean 定理，禁挂内置 `LogicCore.lean` 元定理或跨槽位借证）。报告逐条披露显式桥接前提集 $B$（含外部账本防篡改留痕假设）。

**双哈希精准失活**：每个锚定文件同时计算 `raw_sha256` 与严格 `\r\n -> \n` 归一化 `normalized_sha256`——带 shebang（`#!`）脚本、含 UTF-8 BOM 文件、`.sh`/`.ps1`/`.bat`/`.cmd`/`.gitattributes`/`.olean` 及 `probe_exec` 目标文件按 `raw_sha256` 判失活，普通文本按 `normalized_sha256` 跨平台稳定比对。`advance`、`export-packet`、`verify-replay` 与 `report` 均现场重算磁盘哈希与前驱摘要链，第 $k$ 关漂移仅精准将 $k$ 及下游（含作为第 $N+1$ 关的 `replay_certificate`）置为 `STALE_INVALIDATED`；若仅第 $N+1$ 关因代码升级失活则回落 `STRUCTURE_PASSED` 以便重导包复演。

**轻量 `packet_replay` 协议（100% 复用 `cae_harness.py --clean-room`）**：
- `export-packet` 输出纯净包目录（`manifest.json` + `blobs/<sha256>`），按单源 Schema 机械剥离主会话自评结论与自评载荷 diff，自动打包全部锚定文件的完整文件 blob、受跟踪文件树路径与哈希清单、基线机械 diff 与 `effective_claim_chain`；
- 常规推进可离线跑至 `STRUCTURE_PASSED`。需要独立终审封板（`VERIFIED_*`）时，先经 `cae_harness.py plan` 获用户批准，再对独立包目录执行 `cae_harness.py task --clean-room --target <packet_dir> --kind review` 完成 1 次整单汇聚包隔离复演。`cae_harness` 净室镜像与内联会自动识别复演包并完整包含 `manifest.json` 与全部 `blobs/<sha256>`，亦支持 `--stage` 按需单关导包。
- `verify-replay` 整单升格时强制传入 `--harness-plan-dir` 与 `--generator-session-id`，核验包哈希、文件树与代码版本一致、净室零改动回执、拒 `timeout_salvage`、拒顶层旁路篡改，并与 `cae_harness` 的 `APPROVED` 票据及机械对账账本交叉验签；
- 复演强制采用逐关三值判决（`consistent`、`inconsistent`、`insufficient_context`）与关键剥离字段独立盲推比对（`blind_assertions`），历次尝试记入哈希链 `replay_attempts` 防删改与刷榜（已升格案卷遇 `inconsistent` 立即撤销旧证书）。工具绝不自动执行包内命令，含实跑探针时升格必须传 `--allow-probe-exec` 当轮现场重跑本地案卷已锁定的 `execute: true` 探针。

### 客观见证与真内核 Lean 4 反空真门禁

每个关口及关键判定项必须绑定客观见证：
- `source_anchor`：项目内非符号链接文件路径、严格整数起止行号及原文摘录，落盘双哈希与切片哈希；
- `probe_exec`：实时执行（`execute: true`，须传 `--allow-probe-exec`，满足 `VERIFIED_*` 实跑要求）或离线回执锚定（`execute: false`，回执文件必须含可解析且匹配的显式退出码标记 `exit_code`/`return_code`/`rc`，否则报 `PROBE_RECEIPT_MISSING_EXIT_CODE`，且离线自报回执最高封顶 `STRUCTURE_PASSED`）；
- `lean_theorem`：核验通过的精确模块与 `thmInfo` 定理（真内核模式拦截 `defnInfo` 冒充报 `NOT_A_KERNEL_THEOREM`；未运行 `--run-lean` 标为 `ast_only` 且 `verified: false`，不可充当 `L3`/`S4` 外部执行器锚点，亦不可升格 `VERIFIED_*`）。

Lean 4 反空真门禁实行**静态预检硬短路 + 真内核审计**双层防线：
1. **词法与静态预检**：拦截全部公理与不安全原语（`sorry`、`sorryAx`、`admit`、`axiom`、`unsafe`、`partial`、`opaque`、`implemented_by`、`extern`、`csimp`、`initialize`、`builtin_initialize`）。同时禁绝元编程与编译器逃逸指令（`run_cmd`、`run_meta`、`skipKernelTC`、`native_decide`、`ofReduceBool`、`trustCompiler`、`macro`、`elab`、`syntax`、`variable`、`include`）。
2. **选项白名单与硬短路**：词法级 `set_option` 独占行仅放行 `set_option autoImplicit false`，并禁阴影核心逻辑保留名与零前提构造性 `Nonempty` 缺失。静态预检一旦失败立即短路，绝不启动编译器。
3. **真内核编译与重放（`--run-lean`）**：先锁验受控驱动 `KernelAudit.lean` 自身 SHA-256，在洁净空临时目录与隔离 `LEAN_PATH` 中以 `Parser.parseHeader` 校验导入白名单，现场编译 `.olean` 并双哈希绑定，随后不启初始化器加载 `.olean` 并运行 `Kernel.Environment.replay`。
4. **全常量公理与签名验签**：全常量拦截 `axiomInfo`、`opaqueInfo`、`isUnsafe`、`implemented_by`、`extern`、`csimp`、`initFn`，要求全量 `collectAxioms` 闭包属于 `{propext, Classical.choice, Quot.sound}`，并核验 `_root_.Nonempty` 精确常量、`LogicCore.ObjectiveRole` 七构造子及 40 条契约定理黄金类型签名。

## 命令示例

将 `AXIOM_GATE` 指向本机已安装的脚本（上游维护指向 `template/user-guard/skills/cae-axioms/scripts/axiom_gate.py`）：

```powershell
# 1. 核验纯核 Lean 4 骨架（加 --run-lean 执行真内核校验；Windows 调 WSL2 加 --wsl-lean）
python -B -X utf8 "$AXIOM_GATE" check-lean --profile core --run-lean
python -B -X utf8 "$AXIOM_GATE" check-lean --profile cae --run-lean

# 2. 初始化与逐关推进（离线至 STRUCTURE_PASSED；实时探针加 --allow-probe-exec）
python -B -X utf8 "$AXIOM_GATE" init --profile core --case "<审查单.json>" --project-root "<项目根目录>" --subject "<待审模块>"
python -B -X utf8 "$AXIOM_GATE" advance --case "<审查单.json>" --stage L0_WELLFORMED --payload "<关口见证.json>"

# 3. 根冲突锁定后执行合法出边（可派生子单继承不可变冲突历史与有效命题链）
python -B -X utf8 "$AXIOM_GATE" resolve --case "<审查单.json>" --action downgrade_claim --new-claim "<新命题>" --stripped-claims "<剥离项>" --emit-downgraded-case "<降格子单.json>" --rationale "<依据>"

# 4. 导出盲包、经 cae_harness --clean-room 复演后验签升格 VERIFIED_NO_T / VERIFIED_T_ASSERTED
python -B -X utf8 "$AXIOM_GATE" export-packet --case "<审查单.json>" --output-dir "<复演包目录>"
python -B -X utf8 "$AXIOM_GATE" verify-replay --case "<审查单.json>" --packet "<复演包目录>" --receipt "<净室回执.json>" --harness-plan-dir "<状态目录>" --generator-session-id "<主会话ID>" --allow-probe-exec

# 5. 生成审计报告（--require-verified 仅放行未失活 VERIFIED_*）或登记脱敏反哺建议
python -B -X utf8 "$AXIOM_GATE" report --case "<审查单.json>" --output-md "<审计报告.md>" --require-verified
python -B -X utf8 "$AXIOM_GATE" feedback --case "<审查单.json>" --target-layer 2M --violated-axiom "2M.三.1" --summary "<脱敏抽象形式>" --rationale "<实战依据>"
```

## 输出前六问自检与反哺准入闭环

1. **输出前六问自检**：落笔前逐项核对六个问题。其一，是否用权威代理替代直接拆解。其二，是否混淆逻辑状态与载体错误。其三，是否跳过显式前提注入而塞入未声明指令。其四，是否违反一.8 将无演绎裁决效力的附加约束升格为公理标准，或对并列约束虚构先后层级。其五，是否违反一.6 在逻辑状态已确立时作无前提支撑的定性软化或对称让步。其六，是否违反一.9 被下层派生症状吸引而漏检上层根冲突，或将消除下层症状误判为根体系相容。
2. **脱敏反哺准入与分级修订**：实战发现新边界或反例时通过 `axiom_gate.py feedback` 留档（强制脱敏禁宿主机路径与私网 IP、禁评估集名称泄漏、禁层 1 物理词倒灌，硬编码反例仅具否决效力 `reference_only: true`）。回到配置仓库后，封板基线（第一层、层 1.5、第二层）仅在存在严格逻辑、数学反例（层 1 须附 `--logic-counterexample`）或一手原文核对错误时审慎原地修订；演进层（层 2M）与状态机按全局规则 R0 原地更新，并同步补齐 Lean 见证与离线回归测试。
