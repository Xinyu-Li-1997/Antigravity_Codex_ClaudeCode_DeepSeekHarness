# 面向科学计算与工程研发的四客户端配置与验证工具

本仓库 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 为 **Google Antigravity**（`agy`）、**OpenAI Codex**（`codex`）、**Claude Code**（`claude` 或 `cc`）与 **DeepSeek Harness**（`dsh`）提供一套**共用的全局工程规则、会话生命周期门禁、只读审查代理与跨客户端无头验证机制**。全仓仅依赖 Python 3.11+ 标准库，支持 Windows、WSL2 与 Linux；**只安装任意一个客户端即可独立工作**，多端并存时可按各自专长协同推导、编码与审查。

全文分为**用户层**（快速安装、用户明确点名与角色选路、配置仓库自我更新）与**原理层**（常驻机制与动态配置解耦、共有生命周期门禁与四客户端协议适配）上下两篇，底层细节通过指针跳转查阅：

- **导言**：[目标群体与解决的问题](#目标群体与解决的问题)
- **上篇 · 用户层**：[一、一键安装与课题接入](#一一键安装与课题接入) · [二、日常课题用法与点名矩阵](#二日常课题用法与点名矩阵) · [三、本配置仓库的自我更新与维护闭环](#三本配置仓库的自我更新与维护闭环)
- **下篇 · 原理层**：[四、两层解耦与双门额度机制](#四两层解耦与双门额度机制) · [五、共有会话门禁与四端协议适配](#五共有会话门禁与四端协议适配) · [六、进阶文档指针与目录速览](#六进阶文档指针与目录速览)
- **附录**：[七、致谢、英文概览与开源许可](#七致谢英文概览与开源许可)

---

## 目标群体与解决的问题

本工具专为**科学计算、计算机辅助工程（CAE）、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值离散、高性能计算（HPC）及高可靠系统软件工程**领域的科研人员与工程师打造。

各家官方客户端已具备成熟的交互编辑循环与**服务端自动前缀缓存**（连续工具调用保持消息前缀一致时可复用缓存）。本仓库不重写客户端内核，而是通过一套全局规则（编号 `R0–R11`）、生命周期钩子与验证脚本，在外围挡住工程研发中最常见的六类失误：

| 研发常见痛点 | 客户端默认行为的局限 | 本仓库的共有确定性保护 |
| --- | --- | --- |
| **长对话压缩失忆与待批计划丢失** | 自动摘要易丢掉早期物理符号、法向约定与容差红线；简短状态回执会挤掉长篇审查行号 | 压缩恢复时分两块独立还原：**历轮用户原话 + 达到有效篇幅的子代理关键结论**，以及磁盘原样保存的**未决重负载确认单** |
| **点名被忽略，或无头调用参数残缺降级** | 口头点名客户端或挡位易被覆盖；手写无头命令常漏传最大窗口、单模型高挡或快照检查 | **用户明确点名**（P0 优先）与**角色槽位**并列：无头调用自动补全声明最大窗口、小文件带行号内联与快照；不可用直接报错，绝不静默降级 |
| **测试未过擅改容差，或跑完漏报账单** | 倾向把 `rtol=1e-13` 放宽、删改断言，或多路任务跑完漏报成败与 Token 消耗 | 全局规则 `R3.6` 严禁弱化容差、守恒残差与收敛阶判据；`gate` 强制跑通验收命令；回合结束检查成败词、Token 说明与回执标识 |
| **只读审查误改源码、越界写目录或死轮询** | 审查时顺手改代码、越界改动项目外目录或创建外部 `git worktree`，或反复读旧日志空转 | 原生 `readonly-*` 代理禁写文件、禁跑构建测试；无头只读任务前后比对文件哈希快照；工具前静态拦截越界写删与重复读日志 |
| **文档重写或润色凭空编造外行术语** | 润色时易混入源码从未涉及的陌生缩写、未实现参数或失步数字 | `check-grounding --strict` 自动扫描仓库源码与配置构建词法依据集合，配合 `--clean-room` 空上下文新读者盲读校核 |
| **模型换代返工，或环境升级破坏 ABI** | 在规则和编排中写死时期性模型名极易失步；跨次版本升级系统级 Python 会破坏编译扩展 | 常驻机制与 `fleet_profile.json` 动态参数分层维护，并提供 `--audit-upgrade` 检查配置漂移与科学计算包 ABI 风险 |

---

## 上篇 · 用户层：怎么装、日常怎么用、怎么自我更新

下图展示了安装接入后的两类使用路径：左侧蓝色泳道为**日常业务课题仓**的点名与角色选路闭环，右侧紫色泳道为**本配置仓库**的自我更新闭环，中间通过工作区身份识别严格隔离。

![用户层总览：日常课题协作与配置仓库自我更新双闭环](docs/assets/user-and-evolution-flow.svg)

### 一、一键安装与课题接入

请先准备至少一个官方客户端、合规网络及官方登录（OAuth）或 API Key（支持配合 CC Switch 管理供应商配置卡片）。本仓库**不内置任何第三方反代中转，不创建也不修改任何账号凭据**。

在本仓库根目录运行一次 `install.py`，模板即同步至本机客户端全局目录（`~/.gemini/config/plugins/user-guard`、`~/.codex`、`~/.claude`、`~/.dsh`）。此后打开任意业务课题仓库即可使用，**无需向课题目录复制任何配置文件**：

```powershell
# Windows 使用 python -B -X utf8；WSL2 与 Linux 改用 python3 -B -X utf8（也可运行 install.ps1 或 install.sh）
python -B -X utf8 install.py --probe-apps        # 1. 只读探测本机客户端程序、配置目录与 WSL2 互操作状态
python -B -X utf8 install.py --dry-run --yes     # 2. 跳过交互提示，纯只读预览拟写入路径（零文件修改）
python -B -X utf8 install.py                     # 3. 交互确认后幂等同步本机已安装的客户端（非交互可加 --yes）
python -B -X utf8 install.py --audit-upgrade     # 4. 校验 summary.all_aligned == true 及科学计算扩展 ABI 状态
```

- **按需选择客户端与本地路由**：默认 `--app auto` 自动同步已装端；也可指定 `--app antigravity`、`codex`、`claude`、`dsh`、`both`（指 `Antigravity + Codex`）或 `all`。Claude Code 如需本地 API 协议转换分流或清理旧提示词，可显式附加 `--with-cc-router`、`--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy`（修改前自动对 `cc-switch.db` 做本地在线热备份）。
- **自定义全局规则保留边界**：Antigravity 以独立插件目录 `plugins/user-guard` 安装，不触碰用户自有的 `~/.gemini/GEMINI.md`；Codex、Claude Code 与 DSH 在更新顶层全局规则文件（`AGENTS.md` 或 `CLAUDE.md`）时，完整保留标准 `## 工具与运行环境` 及 `R10.`、`R11.`，同时自动保留排在其后的用户自定义二级标题尾段（含 `## 工具与` 开头的自定义环境段落），并将冲突编号迁为 `L1.`、`L2.`。

---

### 二、日常课题用法与点名矩阵

打开任意业务课题仓库时，系统自动识别为通用课题模式（`workspace_mode: "general_project"`）。你只需在习惯的客户端窗口中用自然中文提需求，主会话会自动调用已安装的 `cae-harness` 技能（入口脚本 `cae_harness.py`）编排后台子任务，你也可按需直接运行对应命令。

#### 1. 明确点名矩阵（P0 第一优先级 + 底层参数自动补全）

只要你在对话中明确指定了客户端、具体模型、推理挡位或省额度要求，外部无头调用一律通过 `--user-prompt` 将其作为 **P0 硬约束**锁定，并自动补齐手写命令容易遗漏的底层参数（客户端内部原生子代理则按当前宿主配置解析 `inherit` 模型与挡位）：

| 你的点名方式 | 自然中文指令示例 | P0 锁定的约束 | 系统自动补全与安全边界 |
| --- | --- | --- | --- |
| **点名客户端或具体模型** | “这步编码用 Codex 高挡位做”“请交给 gpt-6-astra 做脱敏审查” | 锁定指定的客户端、模型标识或模型系列 | 自动补全模型声明的最大上下文窗口、无头标志、小文件带行号内联、前后哈希快照与脱敏；目标不可用或已停用时直接报错，不静默换端 |
| **点名推理挡位** | “这轮用高挡位推导”“用中挡润色这段说明” | 锁定指定的推理强度挡位 | 显式专长角色即使降到 `low` 挡仍保留主力模型，防止静默退化为轻量模型或轻量套餐 |
| **点名省额度或回退策略** | “这轮省点额度快速扫一遍”“加 `--fallback-on-quota` 遇限额再回退” | 切入低消耗策略或启用未改文件时的配额回退 | 遇 `quota_exhausted` 立即停试熔断；仅在未锁死单端 P0 点名、未绑定重负载票据且工作区零改动时按配置回退 |
| **多端并列点名** | “用 Codex 等多工作流全仓校核验证一下” | 识别“等、和、与、及”多端并列语义，不误锁单端 | 完整保留多流计划中各分路配置的不同客户端与模型组合 |

#### 2. 模糊交办与日常课题任务矩阵（按五个动态角色选路）

当你只交代物理或软件目标而不指定具体模型时，系统通过 `orchestrator`、`math`、`code`、`review`、`fast` 五个动态角色槽位自动选取本机已启用且额度正常的对等端：

| 日常课题场景 | 自然中文指令示例 | 调度的角色槽位与验证机制 |
| --- | --- | --- |
| **需求沟通、方案权衡与局部修改** | 直接在主窗口讨论方案、改局部脚本，或发起小范围单路只读检查 | `role:orchestrator`（主会话保留你自己选定的客户端与模型；轻任务静默执行不弹窗） |
| **多文献摘录与大源码行号初筛** | “把这三篇文献的控制方程和对应源码行号摘出来” | 当前客户端内部 `readonly-*` 只读代理并行读取，禁写文件、禁跑构建与测试 |
| **控制方程与离散守恒推导** | “推导弯曲网格几何守恒律（GCL）离散条件与熵稳定判据” | `role:math`，默认采用单模型最高非委派挡位（跳过会自动派生多层子代理的 `ultra`） |
| **核心求解器编码与验证闭环** | “实现二维 Euler 方程 HLLC 界面通量并跑通守恒单测” | `role:code` 配合 `gate`，锁定 `--write-scope` 白名单并强制跑通非空 `--verify-cmd` |
| **关键代码审查与新读者盲读** | “独立审查 `src/flux/` 边界相容性”“按新读者视角检查文档” | 优先跨客户端调度 `role:review` 或双源双盲 `audit`；文档可读性用 `--clean-room` 空上下文盲读 |
| **表面格式快检与低消耗初筛** | “用快速槽位低成本扫一遍目录格式与明显拼写问题” | `role:fast`，采用轻量快速模型低消耗执行，不替代核心数理与代码审查 |
| **技术文档重写、润色与供应商切卡** | “整篇理顺说明文档并核对源码事实”“切换到备用供应商卡片” | 主会话整篇重写（或 `polish` 按篇幅自动选 `medium` 或 `high`）后跑 `check-grounding --strict`；切卡由 `ccswitch_admin.py` 热备数据库后执行 |

#### 3. 多路或可写重负载：纵向 14 项确认单与完工报账

- **先看清再启动**：无论是客户端内部多路或可写原生子代理（如 `invoke_subagent`、`run_workflow`、`swarm`、`spawn_agent`、`Agent`），还是外部无头 `batch`、`gate`、`audit`，启动前均由 `cae_harness.py plan` 生成平实中文**背景、要做什么、预计后果**，配合 **4 列总览表 + 每路 3 列纵向 14 项参数明细表**及离线模型能力台账日期（提示上次联网评估的新旧程度）。
- **三选一拍板与不断线接力**：提供**批准当前计划、微调后批准、暂缓并交接**三个选项；用户批准并登记 `--approve-ticket` 后门禁才放行。等待拍板期间若遇自动压缩或长会话交接，待批确认单会原样独立恢复或完整写入【新对话提示词】。
- **跑完必报账**：重负载结束后，回合结束门禁自动检查回复是否包含成败结论词、Token 用量数字（通道未暴露整数时须明确说明无法统计）以及回执或轨迹标识；主会话再亲自核对原文行号与汇总算术。

---

### 三、本配置仓库的自我更新与维护闭环

当打开本配置仓库本身时，系统自动识别为上游维护模式（`workspace_mode: "upstream_config_repo"`）。**面对外部大模型季度换代、客户端 CLI 升级或你增减订阅套餐，无需手工翻改散落的规则和脚本**——直接在本仓库对话中说出维护需求，即可通过 `fleet-evolve` 技能驱动仓库完成自我演进。

#### 1. 三种维护模式的触发方式

| 维护模式 | 你在本仓库怎么说 | 适用时机与自动执行的闭环动作 |
| --- | --- | --- |
| **全面更新**（`full`） | “全面更新一下四端配置”“我停用了某套餐，重算一下推荐矩阵” | **外部模型换代、客户端升级或订阅变动时使用**。联网核查最新客户端与模型能力，探测本机可用性与额度类型，呈交客户端与模型启停表及五角色推荐草案供你拍板 |
| **全量优化**（`hygiene`） | “多工作流全仓 debug 优化校核一下，再润色脱敏” | **模型选型已定、需打磨代码与文档质量时使用**。不改模型矩阵，基于已审定配置开展跨端代码加固、文档整篇重写、公开脱敏审查、Windows 与 Linux 回归单测及严格锚定校验 |
| **只读巡检**（`audit`） | “巡检一下本机四端对齐和 ABI 状态” | **日常健康检查时使用**。纯只读运行 `--probe-apps`、`--audit-upgrade`、`cae_harness.py probe` 与 `check-grounding --strict`，核查配置漂移、退订候选与科学计算包 ABI 风险，全程零写入 |

#### 2. 四个必停拍板节点与防误触硬隔离

上游维护流程遵循以下四道人工审批边界，未获明确同意绝不落盘或推送：

1. **角色推荐矩阵、运行参数与订阅启停变更**：通过 `fleet_state.py propose` 登记具体提案；你退订停用的客户端或模型在 `subscription_matrix` 中置为 `enabled: false` 后，后续所有选路全局自动跳过；
2. **写入宿主机全局配置目录或切换主客户端**：先运行 `install.py --dry-run --yes` 展示完整目标路径与自定义尾段保留方式，获批后才执行同步；
3. **科学计算扩展 ABI 强耦合升级**：涉及系统级 Python 跨次版本、CUDA 跨大版本或 WSL 跨 LTS 发行版升级时，必须先评估编译型科学计算包兼容性并获专门批准；
4. **远端推送**：完成本地验证、润色与脱敏核查后，经你明确同意才执行 `git push`。

若同步涉及当前正在使用的主会话客户端自身配置，状态机通过 `record-pass1` 与 `verify-pass2` 完成**跨越当前主客户端重启前后的两步接力验收**，最后执行 `fleet_state.py clear --verified --readback-done` 强制跑通事实锚定门禁后归位。**在普通业务课题目录误调已安装的 `fleet_state.py` 会直接返回 `not_upstream_config_repo`（退出码 `2`）拒绝执行**，彻底隔离日常课题与全局维护。

---

## 下篇 · 原理层：为什么跨端通用、不失忆、不幻觉

### 四、两层解耦与双门额度机制

下图自上而下展示了常驻机制层与动态配置层的分工、两道可用性检查门，以及外部无头与内部原生双通道的受控派工链路：

![原理层一：两层解耦、双门额度检查与全通道派工架构](docs/assets/architecture-overview.svg)

#### 1. 常驻机制层与动态参数层（`fleet_profile.json`）分离

- **常驻机制层不绑死时期性模型**：全局规则模板（`R0–R11`）、5 个共用钩子脚本模块、3 类 `readonly-*` 只读代理、`cae_harness.py`、`install.py`、`fleet_state.py`、默认随 Claude 钩子同步的 `ccswitch_admin.py` 以及可选启用的本地 `router.py` 均面向抽象角色槽位与配置键工作，不在规则和选路逻辑中写死时期性模型名。
- **动态参数层集中收口**：`orchestrator`、`math`、`code`、`review`、`fast` 五个角色槽位、运行期核心定量阈值 `runtime_tunables`（恢复预算、交接阈值、防轮询、分片安全系数、轻重任务与额度阈值）、`subscription_matrix` 与 `capability_ledger` 统一维护在 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json)。

下表为仓库随附配置的默认角色分工快照（仅供说明机制，不代表读者本机的安装或实时订阅状态）：

| 角色槽位 | 随附默认推荐端与模型系列 | 推荐挡位 | 核心职责与短板隔离设计 |
| --- | --- | --- | --- |
| `orchestrator` | Antigravity，`carbon` 系列 | `high` | 中文需求沟通、物理与数值方案权衡及总控协调；未暴露整数窗口时采用保守分片预算 |
| `math` | Codex，`astra` 系列 | `max` | 长链条 PDE 离散推导、守恒与熵稳定证明；显式声明最大窗口，过滤 `ultra` 自动委派挡 |
| `code` | Codex，`sol` 系列 | `max` | 核心求解器实现与 `gate --verify-cmd` 闭环修正；锁定 `--write-scope` 与目标行号范围 |
| `review` | Codex `sol` 系列（备选含 DSH、Antigravity、Claude Code） | `max` | 优先跨客户端独立审查（同端 `readonly-*` 仅作摘录与行号初筛）；拦截轻量模型或套餐冒充主力 |
| `fast` | DeepSeek Harness，`deepseek-flash` | `low` | 用户明确要求省额度或快速表面初筛；按本机 CLI 能力自适应权限参数，不替代核心审查 |

#### 2. 两道可用性与本地动态额度检查门

1. **第一道门（维护期联网评估与启停拍板）**：在独立 `fleet-evolve` 维护流程中完成模型性能联网核查与本机可用性探测，由用户拍板写入 `subscription_matrix` 并在 `capability_ledger` 记录评估日期；**日常课题派工绝不联网查性能榜单**。
2. **第二道门（日常确认前纯离线复查）**：`cae_harness.py plan` 在生成确认单前纯离线检查本机可执行文件、凭据配置与所需本地回环路由健康状态。同时读取本地可得的滚动窗口已用百分比与重置时间、CC Switch 卡片套餐或余额计费类型与日月限额、以及当前运行侧配额冷却记录，自动过滤不可用或超限通道；本机未暴露服务端实时余额时如实标为未知，绝不虚构余额充足。

---

### 五、共有会话门禁与四端协议适配

下图左侧为“计划确认—受控执行—完工核验”主流程，右侧为等待确认期间遇到自动压缩或长会话交接时的无损接力分支，底部汇总五个共用钩子模块的职责分工：

![原理层二：共有生命周期门禁、中断接力与完工核验](docs/assets/lifecycle-and-verification.svg)

#### 1. 五个共用钩子模块承载的四阶段会话防护

`template/user-guard/hooks/` 下共有 5 个脚本文件（底层共享库 `user_words.py` 负责跨平台锁、动态参数读取、轨迹解析与日志密钥脱敏，其余 4 个入口脚本均为事件触发后即退出的短生命周期进程），共同承载四阶段确定性保障：

1. **压缩双块恢复与前缀缓存保护**（`session_context_loader.py` + `handoff_gate.py`）：既无新压缩也未触发交接提醒的普通回合保持零字节注入，不扰动服务端前缀缓存；发生压缩或恢复时，一块按客户端专属预算注入历轮原话与关键子代理返回首尾摘要（含截断全文回查与代码围栏自动闭合），另一块从磁盘票据原样加载未决重负载确认单。
2. **工具前写边界、防轮询与票据门禁**（`status_guard.py` + `handoff_gate.py`）：静态拆解文件工具、多层 Shell 包装、重定向与内联 Python 语法树，拦截未获批的项目外写删与外部 `git worktree`；阻止对未变化日志重复轮询；拦截绕过 `cae_harness.py` 直接调用底层无头 CLI，并核验有效 `APPROVED` 票据。
3. **长会话分级交接与跨会话接力**（`handoff_gate.py`）：达到阈值进入强制收尾（`drain` 或 `freeze`）阶段时限制新派工，并在回合结束核验【新对话提示词】完整内嵌待选工作流、14 项参数表、台账记录日期、选项卡与票据号；新会话通过 `plan --resume-ticket` 离线复核后重建当前会话票据。
4. **回合结束防空等与完工词项检查**（`reply_repeat_guard.py` + `handoff_gate.py`）：拦截连续无新数值进展的等待短句；重负载完工后在 `Stop` 检查成败词、Token 说明，以及外部任务的各流标识与回执文件名（或内部原生任务的完成数量、角色名与轨迹说明），漏报单次阻断并附磁盘账单。

#### 2. 零黑名单文档事实锚定（`check-grounding --strict`）

文档校核不依赖人工维护违禁词黑名单，而是直接扫描当前仓库源码、配置与测试文件，自动构建合法命令行选项、文件名、全大写缩写、定量数值与中文双字技术词干集合，逐行拦截无依据标志、陌生缩写与虚构举例。该检查属于静态词法与参数锚定门禁，不替代数学证明与算例运行验证。

#### 3. 共有机制在四个对等客户端的协议适配

不同客户端的宿主事件名称与配置格式各异，安装器按各端原生协议接入同一套共有机制，不将单一客户端的事件结构强加给其他端：

| 对等客户端 | 宿主接入方式与事件映射 | 默认交接模式与专属适配 |
| --- | --- | --- |
| **Antigravity** (`agy`) | 原生插件挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类事件（在 `PreInvocation` 内完成压缩恢复与交接控制步对账，运行态写入 `PLUGIN_DATA`） | 默认启用长会话分段收口；遇 `truncated_fields` 自动回查 `transcript_full.jsonl` |
| **Codex** (`codex`) | `~/.codex/hooks.json` 挂载 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件（同样联动 `handoff_gate.py`） | 默认 `advisory` 提醒；钩子实质内容未变时不重写文件以保留信任指纹 |
| **Claude Code** (`claude` 或 `cc`) | `~/.claude/settings.json` 合并上述五类事件，默认随钩子同步 `ccswitch_admin.py` | 默认 `advisory` 提醒；可选启用本地 `127.0.0.1` `router.py` 协议转换分流 |
| **DeepSeek Harness** (`dsh`) | 当前 CLI 未开放交互式事件钩子接口，同步全局 `AGENTS.md` 与 `skills/` | 经 `cae_harness.py` 无头调用时自适应新旧 CLI 权限参数，同样享有双门检查、行号内联、净室盲读、尾随工具标记（`DSML`）清洗与文件哈希快照保护 |

---

### 六、进阶文档指针与目录速览

| 查阅需求 | 对应文档指针 |
| --- | --- |
| **点名与角色命令对照、纵向 14 项确认单详解、Euler HLLC 任务单模板、中断接力与四级（L1–L4）验证矩阵** | [日常工作流实战指南](docs/workflow-guide.md) |
| **共用钩子模块映射、`runtime_tunables` 参数表、压缩恢复细节、单分片上下文预算公式、本地路由与卸载还原** | [架构设计与钩子说明](docs/architecture-and-hooks.md) |
| **`cae_harness.py` 全部子命令（`probe`、`assess`、`plan`、`batch`、`gate`、`audit`、`task`、`check-grounding`）规程** | [cae-harness 技能规程](template/user-guard/skills/cae-harness/SKILL.md) |
| **配置仓库自我更新（`full`、`hygiene`、`audit`）、`fleet_state.py` 状态机与 `install.py` 维护说明** | [fleet-evolve 维护规程](template/user-guard/skills/fleet-evolve/SKILL.md) · [仓库维护说明](AGENTS.md) |
| **插件模板目录归属与历次版本演进记录** | [插件模板说明](template/user-guard/README.md) · [更新日志](CHANGELOG.md) |

```text
Antigravity_Codex_ClaudeCode_DeepSeekHarness/
├── README.md                              # 总览：目标群体、用户层（安装、点名与自我更新）、原理层与指针
├── AGENTS.md / CHANGELOG.md               # 上游配置仓维护说明与版本演进记录
├── LICENSE / NOTICE                       # Apache License 2.0 许可证与第 4(d) 条原创归属声明
├── install.py / install.ps1 / install.sh  # 跨平台幂等安装器、配置漂移与科学计算包 ABI 耦合审计
├── docs/                                  # 工作流实战指南、底层架构与钩子说明及 3 张分层 SVG 架构图
├── template/
│   ├── user-guard/                        # 四端共用规则 (R0-R11)、5 个共用钩子模块、只读代理、fleet_profile.json 与双技能
│   ├── codex/                             # Codex 事件适配模板与 read-only 只读子代理配置
│   └── claude/                            # Claude Code 事件适配模板、只读代理及 router/ 本地分流与切卡管理脚本
└── tests/                                 # Windows 与 Linux 六模式沙箱安装、钩子门禁、编排器与事实锚定离线回归套件
```

---

## 七、致谢、英文概览与开源许可

### 1. 资助致谢

本项目获**国家留学基金管理委员会**（简称**国家留学基金委**，**China Scholarship Council, CSC**）资助，学号为 `202506280162`，批准文号为 `留金选[2025]37号`；该项资助为本项目目前的唯一资助来源。

### 2. English Overview

`Antigravity_Codex_ClaudeCode_DeepSeekHarness` provides shared engineering rules, deterministic session lifecycle hooks, read-only review subagents, zero-blacklist lexical grounding checks, and cross-client headless verification workflows across **Google Antigravity** (`agy`), **OpenAI Codex** (`codex`), **Claude Code** (`claude` / `cc`), and **DeepSeek Harness** (`dsh`). Built solely on the Python 3.11+ standard library across Windows, WSL2, and Linux, it targets scientific computing, CAE, CFD, FEA, numerical PDE solvers, HPC, and high-reliability software engineering—preserving official client prefix caching and subscription quotas while preventing post-compaction context loss, silent model downgrades, tolerance relaxation, out-of-workspace writes, and ungrounded documentation drift.

On the **user layer**, running `install.py` once equips all general research repositories (`general_project`) without copying config files: explicit user naming takes top priority (P0) on headless dispatches alongside five dynamic role slots (`orchestrator`, `math`, `code`, `review`, `fast`), automatically completing maximum declared context windows, non-delegating effort tiers, line-numbered read-only inlining, and hash snapshots, while multi-stream or write-capable workloads present a narrow-viewport-friendly 14-item vertical confirmation plan before execution. Opening this upstream repository (`upstream_config_repo`) unlocks self-evolution via `fleet-evolve` (`full`, `hygiene`, `audit`) with four mandatory user approval checkpoints and hard `workspace_mode` isolation. On the **principle layer**, permanent mechanisms are decoupled from `fleet_profile.json`, gated by two-stage availability and local quota checks, and adapted cleanly to each client's native event and CLI protocol.

Network access, official client installations, and credentials are prepared by the user. This project is currently supported solely by the China Scholarship Council (CSC), CSC No. `202506280162`, Approval No. `留金选[2025]37号`. Licensed under the [Apache License 2.0](LICENSE); redistributions and derivative works must retain the attribution notices in [NOTICE](NOTICE) (`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`) per Section 4.

### 3. 开源许可

本项目采用 [Apache License 2.0](LICENSE) 开源许可，版权归属 `Copyright 2026 Xinyu Li (Xinyu-Li-1997)`。复制或分发本仓库及其衍生作品时，须遵守协议第 4 条及第 4(d) 条，附带许可证副本并保留 [NOTICE](NOTICE) 中的完整原创归属声明。
