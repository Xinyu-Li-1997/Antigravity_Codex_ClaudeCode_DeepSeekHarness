# 科学计算与工程求解器研发实战指南

本文面向已完成本机全局部署（安装步骤见 [仓库总 README](../README.md) 第二节）的研发人员，通过具体示例说明：打开自己的 C++、Fortran、Rust 或 Python 课题仓库后，如何编写跨进程任务单、如何通过与模型换代解耦的**角色槽位（`role:*`）**发起推导与闭环测试、如何解读结构化回执，以及如何核对 L1–L4 定量验证证据。命令行参数详见 [`cae-harness` 说明](../template/user-guard/skills/cae-harness/SKILL.md)，底层钩子与状态机协议详见 [架构与钩子说明](architecture-and-hooks.md)。

---

## 一、可直接复用的任务单模板与回执核验

### 1. 为什么后台跨进程任务必须写清五项边界

在当前主窗口对话时，模型拥有上下文记忆；而通过 `cae-harness` 拉起后台独立 CLI 子进程时，新进程看不到主窗口此前的口头讨论。因此传给 `--task-file` 的 Markdown 任务单须一次写清五项要素：

1. **数学／物理定义与符号约定**：如守恒型或原始变量形式、无量纲参考量（$L_{\text{ref}}, U_{\text{ref}}, \rho_{\text{ref}}$）、单元界面外法向 $\mathbf{n}$ 指向、几何容差定义或接口状态不变量；
2. **项目根目录与分支状态**：对应 `--target` 工作目录；
3. **必读源码路径与精确行号范围**：直接指明关键头文件与实现文件的行号区间（尤其在 Windows 下可避免后台进程全盘扫描大型仓库超时）；
4. **读写范围与红线约束**：写代码任务配合 `--write-scope` 限定白名单通配符，推导与审查任务明确声明“零文件修改”，严禁放宽测试容差；
5. **可执行的定量验证命令**：配合 `--verify-cmd` 指定守恒残差、有限差分导数核对、几何退化单测或网格收敛阶测试脚本。

### 2. `<任务单.md>` 示例（以二维可压缩 Euler 方程 HLLC 通量为例）

````markdown
# 任务：实现二维可压缩 Euler 方程 HLLC 界面通量并通过离散守恒测试

## 1. 物理与符号约定
- 控制方程：二维守恒型可压缩 Euler 方程，守恒变量顺序 `U = [rho, rho*u, rho*v, E]^T`，理想气体状态方程 `p = (gamma - 1) * (E - 0.5 * rho * (u^2 + v^2))`，`gamma = 1.4`。
- 法向与通量方向：单元界面单位外法向 `n = (nx, ny)` 由左单元 `L` 指向右单元 `R`；返回值 `F_hat` 为沿 `n` 流出左单元 `L` 的数值通量（右单元累加 `-F_hat`）。

## 2. 必读源码与参考接口
- `src/flux/rusanov.py` 第 1–68 行：对齐现有函数签名 `compute_flux(u_left, u_right, normal, gamma)` 与输入校验逻辑。
- `tests/test_flux_conservation.py` 第 1–95 行：现有均匀自由流保持与周期网格离散守恒测试夹具。

## 3. 允许修改范围与禁止事项
- 仅允许新建或修改 `src/flux/hllc.py`、`src/flux/__init__.py` 与 `tests/test_flux_conservation.py`。
- 严禁放宽双精度容差（`rtol=1e-13, atol=1e-14`）或删改已有测试断言。

## 4. 定量验收判据
- 静止接触间断算例精确保持密度阶跃无数值抹平；
- 周期网格随机扰动初值单步推进后，全局质量、动量、总能离散守恒残差均小于 `1e-14`。
````

使用角色槽位 `role:code` 启动带自动验证闭环的 `gate` 命令（脚本自动根据 [fleet_profile.json](../template/user-guard/skills/cae-harness/fleet_profile.json) 解析当前就绪的编码主力模型与最高单模型挡位）：

```sh
python -B template/user-guard/skills/cae-harness/scripts/cae_harness.py gate --caller auto \
  --worker role:code --tier max --fallback-on-quota --target "<项目根目录>" \
  --task-file "task_hllc.md" \
  --write-scope "src/flux/*.py" --write-scope "tests/test_flux_conservation.py" \
  --verify-cmd "pytest -q tests/test_flux_conservation.py"
```

### 3. 结构化 JSON 回执的四个必看字段

- `status` 与 `invocation.returncode`：`status == "ok"` 且 `returncode == 0` 代表无头子进程与 `--verify-cmd` 均通过；若返回退出码 `5`（`quota_exhausted`），代表命中会话／周度限流并已记录冷却时间；
- `verification_executed`：`true` 表示 `--verify-cmd` 已真实跑通；未提供验证命令的普通 `code` 任务会诚实标为 `false`；
- `changed_files` 与 `scope_violations`：列出实际改动文件及是否存在越出 `--write-scope`（或违反只读零改动快照）的路径；
- `result.evidence` 与 `result.unverified`：后台引擎给出的源码行号证据与待实测事项。**采纳任何结论前，主会话必须按 `R6` 亲自打开原文件核对精确行号与符号。**

---

## 二、常规局部修改 vs. 复杂核心模块四步研发流

- **局部确定性修改（调整参数、修复明确下标错误、补充后处理脚本）**：直接在当前单客户端窗口内读清源码、修改并跑通本地测试即可，无需跨进程派工。
- **高风险核心模块研发（高阶离散算子、弯曲网格度量项、隐式雅可比矩阵、复杂曲面求交／刀轨干涉检查或核心状态机）**：按四步角色分工推进：

1. **文献与现有接口并行摸底（当前客户端 `readonly-*` 子代理）**：由 `readonly-reader` 或 `readonly-collaborator` 在独立子窗口内读完参考论文、时间推进／几何核接口与边界赋值代码，返回精确行号索引（10 个以上同构子目录盘点可用 `run_workflow`）。
2. **公式推导与退化边界证明（`cae-harness task --kind math --engine role:math`）**：将离散度量恒等式、熵稳定通量对称性、解析雅可比求导或几何奇异退化条件交给 `role:math`（`--tier max --timeout 900`）在只读快照保护下独立推导，主窗口逐项复核。
3. **限定范围实现与验证闭环（`cae-harness gate --worker role:code`）**：绑定 `--write-scope` 与 `--verify-cmd`。若残差超标、奇异算例失败或收敛阶退化，真实报错会回传给工作引擎在 `--max-rounds` 轮内修复直至测试真实通过。
4. **目录分片交叉审查（`cae-harness audit` 或 `--engine role:review`）**：对核心源码做异构模型只读复核；超出单次安全上下文预算的大目录自动分片送审，主窗口重点联立核对跨文件耦合项（如体积分与面积分求积权重配对、右端残差与预处理雅可比正负号、无量纲物性常数一致性）。

---

## 三、定量验证的四个证据等级（`R3.6`）

阶段汇报与代码合入时，严格按实测深度区分四个证据等级，未执行的层级如实注明“未验证”：

- **L1 源码与配置已就位**：代码、接口或配置文件已写完，尚未编译或未执行定量测试（`verification_executed: false`）。
- **L2 编译与函数级单测通过**：目标编译器零错误通过，单函数输出与解析解、有限差分／复步导数或基准构造算例一致。
- **L3 离散物理或几何不变量保持**：均匀自由流保持、封闭／周期域全局质量／动量／能量离散守恒残差、几何拓扑闭合性或对称性达到双精度机器零量级（约 $10^{-14} \sim 10^{-12}$）。
- **L4 网格／步长加密收敛阶或大规模基准达标**：在光滑精确解或制造解（MMS）加密序列（$h, h/2, h/4, \dots$）中，经验收敛阶（EOC）稳定达到理论阶数 $p$，或完整通过行业标准基准算例。

---

## 四、长会话恢复、配额无损切换与计算资源隔离

1. **压缩恢复与分段交接（`R9`）**：发生上下文自动压缩后，先重读磁盘归档的历轮用户原话并核对 `git status`，直白交代已完成与待办项后再继续；收到长对话交接提醒时停止新开后台任务，收拢局部改动并输出【新对话提示词】。
2. **配额耗尽保护（`--fallback-on-quota`）**：仅当工作区文件尚未发生任何修改时，才优先切回当前主窗口客户端或切往 `fleet_profile.json` 回退链中下一个已就绪且未冷却的对等客户端；若中途已产生代码改动，原样保留现场 `git diff` 交由研发人员裁决。
3. **重算力与编译包环境隔离（`R10`）**：大规模并行编译、细密网格生成与长时 MPI／GPU 算例建议置于 WSL2、容器或计算节点运行防桌面 OOM；课题自身的 `jax`、`scipy`、`mpi4py`、`petsc4py` 等编译依赖统一用 `uv` 或项目独立 `venv` 隔离，不混入宿主机全局 Python。
