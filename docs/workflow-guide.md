# Antigravity、Codex、Claude Code 与 DeepSeek Harness 工程及科研协作实践指南

本文面向从事计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值求解器与高性能科学计算的开发者，说明宿主机全局落盘后的 `user-guard`、四端无头验证台架 `cae-harness` 以及 Claude Code 本地路由／CC Switch 协同工具在零门槛起步、课题统管、公式推导、求解器编码、跨模块审计及例行升级回归中的实战配合方法。命令行细则见 [`cae-harness` 技能说明](../template/user-guard/skills/cae-harness/SKILL.md)，底层钩子协议与无头执行机制见[架构设计与生命周期钩子规范](architecture-and-hooks.md)，部署方法见[仓库总 README](../README.md)。

---

## 一、宿主机全局落盘、四端对等分工与“五要素任务单”

运行一次 `install.py` 后，四端配置直接落盘在各自的宿主机全局配置根目录（`~/.gemini/config/plugins/user-guard`、`~/.codex`、`~/.claude`、`~/.dsh`），日常打开任何 CFD/FEA 业务仓库都会自动生效，无需把本配置仓拷进业务项目。四个客户端在逻辑层级上完全对等，按团队阶段与项目性质灵活分工：

| 典型研发场景 | 推荐主控端 | 四端实战配合方式 |
| --- | --- | --- |
| **科研课题与数值求解器综合研发（主力推荐）** | **反重力**（单模型 `Gemini 4 Carbon (High)`） | 由反重力居中统管四端更新、额度状态与上下文预算，承担多篇文献对照、跨模块接口梳理、中文技术写作与最终验收；凡涉及高难度方程推导、离散守恒／稳定性证明、核心求解器编码、深层边界审查与验证门禁闭环，**优先经 `cae-harness` 调 Codex（`Astra`／最新代 `Sol`）无头攻坚**；按需调 `Claude Code`（`cc`）做独立交叉审查，三端配额受限或网络波动时切 `DSH`（`dsh`）保底调试；阶段收口前由 Codex 负责全仓代码回归 Debug 与门禁，反重力负责文档润色、清淤精简与脱敏把关 |
| **首次接触本套规范／纯内网或国内直连起步** | **DeepSeek Harness**（`DSH + DeepSeek`） | 无需任何反代等灰色操作，直接凭官方 `DEEPSEEK_API_KEY` 或官方账号在 `DSH` 内跑通整套 `R0–R11` 规范与 `cae-harness` 门禁闭环（主力 `DeepSeek-V4-Pro`、轻量档 `DeepSeek-V4.1-Flash`）；后续按需开通反重力、Codex 或 Claude Code 即可无缝接手 |
| **用户指定的终端纯代码重构项目** | **Codex** 或 **Claude Code** | 在当前终端宿主内完成代码修改与本地测试闭环（Claude Code 接入 `router.py` 时主会话默认走 `k3.1-pro[1m]`、三个只读子代理走 `deepseek-flash`，也可随时通过 CC Switch 在 Windows 与 WSL2 双端同步切卡），按需跨端调反重力做物理／架构复核与技术写作润色 |

无论由哪一端担任总控，跨端调用都运行在全新的无头 CLI 子进程中，**看不到桌面或终端交互窗口里的历史对话和口头补充**。因此，下发给 `cae-harness` 的任务单（`--task-file`）必须一次性写清五项要素：

1. **物理数学前提与符号约定**：如控制方程守恒形式、无量纲参考量、单元界面法向指向、Riemann 通量正负号及用户最新拍板的边界条件；
2. **Git 基线与项目根目录**：明确 `--target` 所在路径与当前代码基线；
3. **必读源码与参考文档清单**：列出需要核对的头文件、算子实现或公式文档路径，避免无头子进程盲目全盘搜索；
4. **严格的文件写入白名单**：写入任务明确 `--write-scope`（如 `src/flux/*.cpp`），只读推导／审查任务明确声明严禁改动任何文件；
5. **可量化的交付格式或验收命令**：如要求通过 `--verify-cmd` 运行离散守恒残差检查、雅可比有限差分对偶检验或制造解（MMS）网格收敛阶脚本。

无头子任务返回后，总控必须按 `R6` 亲自打开原文件核对行号、公式步骤与命令输出，确认无误后再整合交付。

---

## 二、主会话、原生只读子代理与 `cae-harness` 四端选路

日常研发切忌把所有材料硬塞进同一个主会话窗口，应按“同端只读检索”与“跨端独立攻坚／交叉门禁”清晰分流：

1. **前端主会话 + 本侧原生只读子代理（文献通读与多文件静态核查）**：
   - 前端主会话始终保留用户自选的模型与挡位（反重力推荐单模型 `Gemini 4 Carbon (High)`，避免固定复合模式 `Orchestrator` 在同一窗口内交替混用模型挤占容量）。
   - 并行通读多篇论文、摘录几十个源文件中的边界条件实现或核对汇报行号时，直接唤起本侧原生 `readonly-reader`、`readonly-auditor` 或 `readonly-collaborator`（反重力限制 `read_file/search_web`，Codex 启用 `read-only` 沙箱，Claude Code 限制 `Read/Grep/Glob`），把大体积原始文本隔离在独立子窗口内；10 个以上同构算例目录或子模块的结构化只读盘点可使用反重力原生 `run_workflow` 并行扇出（`/boost`、`/swarm`、`/teamwork` 仅在用户显式点名时启用）。
2. **`cae-harness` 四端无头调度（发包前预检就绪 + 默认满血旗舰）**：
   - **Codex 硬核推导与求解器门禁（`codex:default`）**：`math`／`direction` 自动优选深度推理旗舰 **`Astra`**（`gpt-6-astra`，聚焦预提炼方程，复杂推导建议设 `--timeout 900–1200`）；`code`／`review`／`audit`／`gate` 自动优选最新代工程旗舰 **`Sol`**（`gpt-6.1-sol`），默认启用单模型纯思考最高挡 **`max`** 并显式下发 **`872,000` token** 声明最大窗口（默认排除内部二次派生的 `ultra` 挡防套娃耗尽配额）。
   - **Claude Code 原生配置继承与按需调用（`cc:default` / `claude:default`）**：发包前自动检查是否存在有效 API 凭据、运行中的本地 `router.py`（探活 `/__router/health` 并核查 `cc-switch.db` 卡片密钥）或有效登录态，拦截 `PASTE_*` 等未填占位符；执行 `claude -p` 时不加 `--bare`，完整继承 `~/.claude` 下的 `CLAUDE.md`、`settings.json`、生命周期钩子与 CC Switch 当前卡片（支持 `cc:kimi`、`cc:deepseek` 别名及自然语言点名“让 CC 检查”）。
   - **DeepSeek Harness 合规直连与应急兜底（`dsh:default`）**：发包前核验环境变量、`.env` 或 `.credentials.yaml` 中的官方 API Key／官方账号凭据，通过临时运行补丁（`--profile headless --patch`）按任务下发旗舰 **`deepseek-v4-pro`**（`max` 挡）或轻量 **`deepseek-flash`**（`low` 挡），绝不污染 `~/.dsh` 持久化配置文件。
   - **纯文字润色与按需分级省流**：`polish` 任务采用短篇 `medium`、长篇 `high` 挡防 `R5` 无声放大；仅当用户显式传 `--tier` 或在提示词中提出降挡、省额度、省 token 时，才切换为分级省流模式。

---

## 三、额度熔断与 `--fallback-on-quota` 四端顺位接管

四端 CLI 各自使用独立的官方账号或 API 配额池。目标端配额触顶时按三级机制平稳过渡：

1. **即时熔断与本侧冷却缓存**：一旦某端返回配额／速率耗尽，`cae-harness` 立即中止同批次对该端的后续调用，返回 `quota_exhausted`（退出码 `5`），并按实际运行侧（`windows`／`posix`）记录恢复时间；冷却期内后续请求在启动子进程前即被拦截（`preflight_blocked: true`）。
2. **零改动顺位接管（`--fallback-on-quota`）**：
   - 执行 `task`、`gate` 或 `audit` 时附带 `--fallback-on-quota`，若目标端在预检或启动阶段触发额度熔断且当前工作区文件**尚无任何改动**，脚本自动切回发起方本侧或顺位已就绪的对等端（含保底调试端 `dsh`）继续完成任务，并在回执 `quota_fallback` 中完整记录切换原因与冷却时间。
   - 若子任务在**已修改部分工作区文件后**才中断，脚本绝不自动换端重跑写入，原样保留现场代码差异交总控裁处。
3. **人工拍板接管或恢复重试**：未开启 `--fallback-on-quota` 时，由用户拍板 **① 改由本侧／其他就绪对等端接管**，或 **② 配额恢复后附 `--retry-quota` 重试**。

---

## 四、大型求解器审计（`audit`）与跨模块强耦合算子复核

- **扣除协议开销后的安全分片预算**：系统提示词、文件清单、结构化 JSON Schema、工具调用与内部推理链均占用窗口。`cae-harness audit` 在目标后端有效窗口内预留安全余量：预算内优先单次全量送审（`single-pass`），超预算按目录与模块内聚性自动切分（`sharded`），支持 `codex`、`agy`、`cc`、`dsh` 任意两两交叉审计或单端分片审计。
- **跨分片数值强耦合必须由总控联合复核**：在 PDE 求解器中，离散守恒性、自由流保持（Free-Stream Preservation）与线性化稳定性往往横跨多个目录——如间断伽辽金谱元法（DGSEM）的体积分分裂导数算子、单元界面数值通量与弯曲网格离散度量恒等式，或隐式求解器的残差右端项与雅可比非对角块。总控必须把跨分片的接口签名、全局物理常数、求积权重与法向正负号拉通做联合核对。

---

## 五、反过度工程（R3.3）、项目目录硬边界（R4）与数值验收红线（R3.6）

1. **反过度工程与代码精简红线（`R0`／`R3.3`）**：求解器与工程脚本严格聚焦当前明确需求与主力运行环境，严禁为未要求的老旧版本、未使用的外围平台或假想未来需求编写回退解析器、多余抽象层与防御性样板代码。
2. **项目目录硬边界多重防护（`R4`）**：除用户明确批准外，严禁在当前项目根目录外创建、修改、移动或删除任何业务文件与全局配置（含严禁外部 `.codex-worktrees` 或 `git worktree add`）。反重力与 Claude Code 侧由 `status_guard.py` 静态解析工具参数、Python AST、动态工作流与 Shell 子句（交互会话弹窗请示，无头子任务直接拒绝）；Codex 与 DSH 默认启用工作区沙箱，`cae-harness` 同时在任务前后对比全目录快照，任何白名单外写删均判定为 `scope_violation`。
3. **科学计算四级验收证据与红线（`R3.6`）**：严格区分 **① 源码／配置静态存在**、**② 编译通过且离线单元测试绿灯**、**③ 离散守恒残差／对称性／自由流保持达机器精度**、**④ 网格加密与时间步缩小的经验收敛阶（EOC）达理论阶数**（空白 `--verify-cmd` 直接报错拦截，未附 `--verify-cmd` 的 `code` 任务显式标记 `"verification_executed": false`）。测试不过时必须排查离散公式、边界条件或通量符号根因，**严禁放宽 `rtol`／`atol`、删改测试断言或硬编码网格特例**。

---

## 六、Windows 与 WSL2 跨子系统协同及 CC Switch 职责边界

1. **计算环境隔离与无痕会话**：大规模 C++/Fortran 编译、细密网格生成或高内存 MPI 数值算例建议置于 WSL2 或独立计算节点执行（`R10`）。四端无头调用均保持会话隔离（`codex exec --ephemeral`、`~/.gemini/antigravity-cli`、`claude -p --no-session-persistence`、`dsh --profile headless`），不污染交互历史；Windows 与 WSL2 各自维护独立主目录与冷却缓存，升级后两侧分别运行一次安装器即可同源对齐。
2. **Claude Code 本地分流路由与 CC Switch 卡片解耦**：全局规则、只读子代理与钩子统一由 `install.py` 从本仓库同步；CC Switch 仅管供应商凭据与卡片切换，通过 `--disable-ccswitch-prompts` 保持内置提示词禁用以免冲掉全局规则。启用 `--with-cc-router` 时，Windows 侧默认监听 `127.0.0.1:8240`、WSL2 侧默认监听 `127.0.0.1:8241`（自动识别 WSL 网关／系统代理透传）；`ccswitch_admin.py switch-card` 切卡时仅原子投影受管的模型／鉴权字段，100% 保留 `hooks`、`effortLevel` 与自定义键，且 `cae-harness` 调 `cc` 时实时继承切卡结果。
3. **WSL2 `<->` Windows 自动互操作**：WSL2 下运行 `cae-harness` 优先调用本侧已就绪的原生 Linux CLI，未安装或未登录时自动回退调用 Windows 侧 CLI（`/mnt/<盘符>` 共享盘可直接读写；纯 Linux 路径支持只读临时镜像审查，写入任务须置于共享盘或使用 WSL 原生 CLI）。

---

## 七、客户端、CC Switch 与大模型升级例行回归流程

当任一客户端（反重力、Codex、Claude Code、DSH）、CC Switch 或上游大模型升级，用户提出**“根据各自升级做适配与回归测试”**时，按 [AGENTS.md](../AGENTS.md) 第三章的触发式六步流程闭环执行：

1. **全栈只读体检**：运行 `python -B install.py --audit-upgrade` 与 `cae_harness.py probe --discover-models`／`assess`，一次查清四端版本、规则／钩子／技能漂移、CC Switch Schema 与卡片状态、本地路由健康度及最新大模型目录。
2. **能力匹配与精简修订**：针对新模型代际、窗口扩容或无头 CLI 参数演进更新模板与选路映射，清理已下线模型配置（遵循 `R3.3` 不留废弃兼容层）。
3. **跨端代码门禁与六模式沙箱测试**：调 Codex `Sol (max)` 完成边界审查与 `--verify-cmd` 门禁，在临时目录跑通 `codex`、`antigravity`、`claude --with-cc-router`、`dsh`、`both`、`all --with-cc-router` 六套沙箱安装、沙箱 `--audit-upgrade` 零漂移校验及全量 `unittest` 回归。
4. **润色脱敏与双侧预演收口**：更新文档能力矩阵与 `CHANGELOG.md`，通过全仓脱敏审计后输出 Windows 与 WSL 双侧 `--dry-run --yes` 预演清单，获用户批准后同步生产配置并复核零漂移收口。
