# 科学计算与工程研发的四客户端配置工具

本仓库 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 为 Antigravity（`agy`）、Codex（`codex`）、Claude Code（`claude` 或 `cc`）和 DeepSeek Harness（`dsh`）提供共用的工程规则、会话事件钩子、只读子代理与后台验证工具。适用场景包括科学计算、CAE、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值离散和高性能计算（HPC）。工具全部基于 Python 3.11+ 标准库编写，可在 Windows、WSL2 与 Linux 环境运行。

四个客户端处于同一对等层级，只安装其中一个客户端也能独立完成研发工作。日常研发时，主会话在课题仓库内直接调用已安装的全局规则、钩子与 `cae-harness`，无需向业务仓库复制任何模板文件；仅在更新或维护本配置仓库时，才使用安装器 `install.py` 和工具链维护流程 `fleet-evolve`。交互主会话始终保留用户自己选定的模型与推理强度，后台子进程按动态配置单独选择执行客户端与模型。

导航：[一、架构与使用边界](#一架构与使用边界) · [二、准备与安装](#二准备与安装) · [三、任务派工与中断接续](#三任务派工与中断接续) · [四、默认角色快照与事件绑定](#四默认角色快照与事件绑定) · [五、日常与维护入口](#五日常与维护入口) · [六、致谢与许可](#六致谢english-overview--license)

## 一、架构与使用边界

![四客户端配置、双门可用性与全通道派工架构](docs/assets/architecture-overview.svg)

本仓库将稳定的执行机制与随模型迭代更新的参数配置分开维护。工程规则只在 [全局规则模板](template/user-guard/rules/AGENTS.md) 中维护一份；五个角色槽位、运行阈值、启用矩阵与性能记录时间统一保存在 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 中。第四章的角色表格仅供阅读审阅，不作为第二份配置来源。

| 模块组成 | 职责范围 | 验证与约束方式 |
| --- | --- | --- |
| 全局规则与 `readonly-*` 子代理 | 约束物理符号约定、目录写入边界、只读审查权限及主会话原文复核责任 | 主会话采纳审查意见前必须打开原文件核对精确行号；审查任务严禁修改源码或运行构建测试 |
| 会话生命周期钩子 | 自动压缩后恢复用户原话与待确认任务单，拦截越界写入、重复轮询及遗漏汇报 | 依据宿主事件载荷、保存在磁盘的确认记录与会话轨迹做确定性检查 |
| `cae-harness` | 解析用户明确指定的客户端或角色槽位，复查本机可用性，组织无头子进程执行与验证 | 只读任务前后比对文件哈希快照；可写任务锁定 `--write-scope` 白名单，`gate` 强制执行非空 `--verify-cmd` |
| `fleet-evolve` | 在本配置仓库中评估外部工具升级、角色分工与运行期参数 | 变更方案经用户批准后应用；归位前运行 `check-grounding --strict` 检查文档词法依据 |

脚本通过 `workspace_mode` 区分两类工作目录：普通业务课题识别为 `general_project`，仅开放日常研发与验证能力；工作区或父目录同时包含 `install.py` 与 `template/user-guard/plugin.json` 时识别为 `upstream_config_repo`，额外开放全局分发与状态锁维护入口。

调整推荐角色矩阵或 `runtime_tunables`、向宿主机全局配置目录写入文件或切换主会话客户端、升级与编译型科学计算包强耦合的底层环境（如系统 Python 次版本、CUDA 大版本、WSL LTS 发行版），以及执行 `git push` 推送远端仓库，均须单独取得用户明确批准。单次后台任务派工的确认不包含上述长期变更授权。

## 二、准备与安装

安装前请准备至少一个客户端程序、可用网络以及官方账号登录或 API Key。客户端可执行文件已安装不代表登录有效，两项状态由探测命令分别核实。安装器不会代办账号登录，也不会改动凭据文件中的密钥。

在仓库根目录执行下列只读探测与试运行（`--dry-run`）命令。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 改用 `python3 -B -X utf8`：

```powershell
python -B -X utf8 install.py --probe-apps
python -B -X utf8 install.py --audit-upgrade
python -B -X utf8 install.py --dry-run --yes
```

先检查试运行输出中的完整目标路径、默认模型补齐行为，以及现有全局规则末尾自定义段落的保留方式，确认无误后再执行正式安装。`--yes` 仅跳过安装器命令行交互提问，不能替代用户对全局目录写入的授权。

| 安装目标 | 正式安装命令 | 同步的全局目录内容 |
| --- | --- | --- |
| Codex | `python -B -X utf8 install.py --app codex` | `~/.codex/AGENTS.md`、`agents/`、`hooks/`、`hooks.json` 与 `skills/` |
| Antigravity | `python -B -X utf8 install.py --app antigravity` | `~/.gemini/config/plugins/user-guard` |
| Claude Code | `python -B -X utf8 install.py --app claude` | `~/.claude/CLAUDE.md`、`agents/`、`skills/`、`hooks/` 及 `settings.json` 事件绑定 |
| DeepSeek Harness | `python -B -X utf8 install.py --app dsh` | `~/.dsh/AGENTS.md` 与 `skills/`，原样保留 `.credentials.yaml` |
| 双端组合（`both`） | `python -B -X utf8 install.py --app both` | 同时同步 Antigravity 与 Codex 两端配置 |
| 四端全量或自动识别 | `python -B -X utf8 install.py` | 默认 `--app auto` 按本机已检测客户端同步；`--app all` 强制指定全部四端 |

同步全局规则时，安装器写入完整的标准模板（含标准章节 `## 工具与运行环境` 及 `R10.`、`R11.`），并保留用户规则末尾另起标题的本机环境自定义段落。若自定义段落中使用了与标准条目重名的 `R10.`、`R11.` 编号，安装器自动将其改标为 `L1.`、`L2.`，防止遮蔽标准规则。

对于使用 CC Switch 管理 API 供应商配置卡片的环境，`--app claude` 默认随钩子同步卡片读取与切换脚本 `~/.claude/hooks/ccswitch_admin.py`；加 `--with-cc-router` 时另行同步本地协议转发脚本 `~/.claude/router/`；加 `--no-claude-hooks` 则跳过钩子与管理脚本同步。如需停用或清理 CC Switch 中的旧提示词与旧技能记录，须显式传入对应清理选项，脚本会先在本地对正在使用的 `cc-switch.db` 生成一致性热备份文件再执行修改。

安装完成后可再次运行 `--audit-upgrade` 确认 `summary.all_aligned` 为 `true`。卸载时只需移除对应全局目录下的托管文件与事件注册项，或使用本地备份还原，详见 [架构与钩子说明](docs/architecture-and-hooks.md)。

## 三、任务派工与中断接续

### 1. 用户明确指定与角色槽位并列

用户明确指定的客户端、模型简称、推理强度或省额度要求具有最高优先级（规则中记为 **P0 第一优先级**）。调用 `cae_harness.py` 时通过 `--user-prompt` 传入用户原话，脚本锁定用户已指定的维度，其余未指定参数按模型目录与配置补齐。用户未点名时，使用 `role:*` 角色槽位按任务职责选择候选客户端与模型；角色推荐不会覆盖用户点名。若点名对象在本机不可用或已在配置中停用，脚本直接报错说明原因，不会静默换用其他客户端或降级模型。

| 指定方式 | 锁定的选择 | 自动补齐的参数与边界 |
| --- | --- | --- |
| 用户点名客户端、模型或推理强度 | 保留用户指定的维度 | 自动补齐模型声明的最大上下文窗口、无头参数与快照检查；仍执行可用性复查 |
| `role:orchestrator`、`role:math`、`role:code`、`role:review`、`role:fast` | 按工作职责选择槽位 | 按配置顺序选取就绪客户端与模型；常规后台任务使用单个模型，默认选非自动委派的最高强度 |
| `<client>:@<role>` | 指定客户端及职责槽位 | 在该客户端内选取对应职责模型；显式非 `fast` 角色即使降到 `low` 强度也保留主力模型 |
| `<client>:default`、`<client>:<model>` | 指定该端默认或具体模型 | 补齐上下文窗口与非交互运行标志；不以轻量模型冒充非 `fast` 角色主力 |
| 客户端内部原生 `readonly-*` | 默认 `inherit` 继承当前主会话模型 | 任务确认单展示从客户端配置解析出的实际模型名；无法解析时如实标明，不套用外部无头隔离承诺 |

术语说明：**无头**（headless）指启动独立的非交互命令行子进程运行任务，不占用主会话上下文；**净室**（`task --clean-room`）指在支持的外部只读任务中构造临时配置目录，剥离全局规则、钩子、技能与仓库自动说明，用于模拟新读者检查文档可读性。外部调用默认开启 `--inline-readonly`，在配置体积上限内将只读目标小文件附带精确行号直接嵌入提示词；`--output` 默认对写入磁盘的结构化回执做公开路径脱敏。常规任务跳过会自动派生多层子代理的 `ultra` 挡位；纯文字润色 `polish` 按篇幅自动选用 `medium` 或 `high`。命令行中 `--kind` 表示工作性质，`--engine` 或 `--worker` 表示执行引擎，两者须明确搭配。

### 2. 两阶段可用性与本地额度复查

| 检查阶段 | 数据来源与判断内容 | 结论边界 |
| --- | --- | --- |
| 第一阶段：独立 `fleet-evolve` 维护评估 | 结合官方更新与本机探测，展示各客户端和模型的本机可用性及配置的额度类型；由用户决定启停并写入 `subscription_matrix.agents`、`subscription_matrix.models` | 设为 `enabled: false` 的客户端或模型在全局自动跳过；修改长期启用矩阵须经用户批准 |
| 第二阶段：展示任务确认单前 `cae_harness.py plan` 离线复查 | 纯离线检查本机可执行文件、凭据配置、本地回环路由健康、本地会话记录中的滚动窗口已用百分比与重置时间、CC Switch 卡片计费类型与日月费用上限，以及运行侧冷却记录 | 自动过滤不可用或已超限通道；本机未记录服务端实时余额时标明剩余额度未知，不推断为余额充足 |
| 离线性能记录：`capability_ledger` | 在确认单末尾展示 `last_online_refresh_at` 与 `local_bench_verified_at` | 日常派工只读取本地记录时间，不联网查询模型榜单；在线刷新由 `fleet-evolve` 独立完成 |

运行中若触发 `quota_exhausted`（配额耗尽），脚本立即停止重试并写入运行侧冷却记录。仅当显式传入 `--fallback-on-quota` 且工作区尚未发生任何文件修改时，才允许按配置顺序回退；用户明确点名的约束及重负载重新确认要求仍然生效。

### 3. 日常最小流程与全通道 14 列确认单

![确认票据、压缩接力与完成核验流程](docs/assets/lifecycle-and-verification.svg)

单路小规模只读检查（文件数、估算 Token 与超时均未超过 `runtime_tunables.dispatch_confirmation` 轻任务上限）可直接执行。多路并发任务、可写编码任务，或超出轻任务阈值的调用均属于**重负载**——无论调用外部无头进程，还是在客户端内部发起多路或可写原生子代理（如 `invoke_subagent`、`run_workflow`、`spawn_agent`、`Agent`），都必须先生成任务确认单，获用户批准后再执行：

```powershell
# 1. 离线生成任务确认单与待批票据（不启动子代理）
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py plan --spec-file "<工作流配置.json>" --session-id "<当前会话标识>" --workflow-bg "核心模块已修改" --workflow-goal "分两路只读核查接口与测试" --workflow-impact "只读检查并保存回执，不改动源码"

# 2. 用户明确批准后登记票据状态为 APPROVED，再绑定票据执行
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py plan --approve-ticket "<票据标识>" --session-id "<当前会话标识>"
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py batch --ticket "<票据标识>" --session-id "<当前会话标识>" --output "<回执输出路径.json>" --redact
```

`plan` 先用平实中文说明本次工作流的背景、要做什么与预计后果，再生成包含以下 **14 列完整字段** 的参数矩阵（任何未知项均如实保留，不用角色名代替具体模型名）：

| 流号 | 子任务与范围 | 通道 | 客户端及子代理类型 | 模型名字 | 挡位 | 上下文窗口 | 是否无头 | 是否净室 | 访问模式、写入范围与验收命令 | 内联与超时 | 当前是否可用 | 本机配置额度类型与当前状态 | 回执或子代理日志位置 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 每路唯一标识 | 任务描述与目标路径 | 外部无头或内部原生 | 实际执行端与代理类型 | 实际模型及 `inherit` 解析结果 | 推理强度 | 声明值或未声明 | 布尔状态 | 布尔状态 | 只读，或写入白名单及验证命令 | 行号内联与秒数 | 可用判断及原因 | 额度来源、可读状态或未知说明 | 回执文件或原生轨迹路径 |

矩阵下方附离线性能记录更新时间，并提供三个选项：批准当前计划、微调引擎或挡位或超时后批准、暂缓并交接。工具执行前钩子 `status_guard.py` 在 `PreToolUse` 阶段核验当前会话未过期的 `APPROVED` 票据，并拦截绕过 `cae_harness.py` 直接调用底层客户端无头 CLI 的命令。`batch` 仅执行外部 `cae_harness_headless` 通道；含内部原生子代理的计划由当前客户端按获批内容直接调用。

### 4. 中断接续与完成报告检查

- **同会话自动压缩恢复**：等待用户确认期间若发生上下文自动压缩，`session_context_loader.py` 将恢复内容严格分为两块：一块是常规的历轮用户原话与实质子代理结论摘要；另一块直接从磁盘待批票据加载本次重负载工作流的背景、目标、预计后果、完整 14 列矩阵与选项卡。票据缺失或损坏时直接报告缺口，不凭摘要猜补，也不将压缩恢复视同用户已批准。
- **长会话交接与新会话接续**：当会话达到 `runtime_tunables.handoff_thresholds` 阈值进入 `drain`（收尾）或 `freeze`（冻结）强制交接模式时（注：Codex 与 Claude Code 默认配置为 `advisory` 仅提醒模式，是否强制阻断取决于实际启用模式），`handoff_gate.py` 在 `Stop` 阶段核验【新对话提示词】是否已完整嵌入待确认工作流详述、14 列矩阵、性能更新时间、选项卡及票据号。进入新会话后先运行 `plan --resume-ticket` 读取复核旧票据；该命令保留原票据的 `session_id` 不自动改绑，跨会话正式执行前须按原流内容生成绑定当前新会话标识的确认票据。
- **重负载完成报告检查**：重负载执行结束后，`reply_repeat_guard.py` 在回合结束 `Stop` 事件做静态词项检查：
  1. 回复须包含成败结果词、Token 用量数字或“该通道未提供、无法统计”的明确说明，以及回执或轨迹与日志词项；
  2. 外部无头任务须按完整词项覆盖各流标识（单路 `task` 或 `gate` 的默认流号 `S1` 允许用模型名或任务状态替代）及非空回执的实际文件名；内部原生任务须覆盖完成数量、全部流标识或互不重复的角色名之一，并提供日志或轨迹说明；
  3. 若有遗漏且同一回合尚未触发重复回复门禁或交接门禁阻断，钩子单次阻断并附上磁盘记录供补齐；钩子仅检查上述要素是否出现，各路真实状态、Token 明细核对与汇总算术仍由主会话负责逐路核实。详细命令模板见 [工作流指南](docs/workflow-guide.md)。

## 四、默认角色快照与事件绑定

### 表一：仓库随附默认配置中的角色分工

下表展示本仓库随附 `fleet_profile.json` 当前记录的默认角色配置，用于说明设计分工，不代表读者本机的安装、登录或实时额度状态。执行任务前仍须通过第二阶段离线可用性复查；具体模型版本、上下文窗口与支持挡位以客户端本地目录解析结果为准。

| 角色槽位 | 随附配置推荐端与模型系列 | 推荐挡位 | 适用工作与运行约束 |
| --- | --- | --- | --- |
| `orchestrator` | Antigravity，`carbon` 系列 | `high` | 适合中文工程需求沟通与方案统筹；压缩恢复按客户端预算执行，目录未提供整数窗口时采用保守分片预算 |
| `math` | Codex，`astra` 系列（如 `gpt-6-astra`） | `max` | 适合长链条偏微分方程离散推导、守恒性与边界条件论证；显式传入模型声明的最大窗口，跳过自动委派挡位 |
| `code` | Codex，`sol` 系列（如 `gpt-6.1-sol`） | `max` | 适合核心求解器实现、跨文件重构与 `gate --verify-cmd` 闭环验证；须限定目标文件与行号范围，避免全目录遍历超时 |
| `review` | Codex `sol` 系列，候选含 DeepSeek Harness `deepseek-v4-pro` | `max` | 优先跨客户端执行独立代码与文档审查；Claude Code 按已启用主力卡片参与调度，轻量模型卡片不冒充非 `fast` 主力 |
| `fast` | DeepSeek Harness，`deepseek-flash` | `low` | 用于用户明确要求省额度或快速表面初筛；按本机 CLI 能力自动适配权限参数，不替代核心独立审查 |

五个槽位均维护有序候选列表，不与单一客户端永久绑定。离线性能记录时间保存在 `capability_ledger.last_online_refresh_at` 与 `capability_ledger.local_bench_verified_at`。

### Codex 与 Claude Code 五类事件对齐

Codex 的 [hooks.json](template/codex/hooks.json) 与 Claude Code 的事件配置均挂载以下五类生命周期事件（共用脚本模块共五个，其中 `user_words.py` 为底层共享模块，事件数与脚本文件数分别统计）：

| 事件名称 | 触发时机与检查职责 |
| --- | --- |
| `SessionStart` | 在 `compact` 或 `resume` 入口运行 `session_context_loader.py` 恢复用户原话、实质子代理摘要及待确认工作流，随后运行 `handoff_gate.py` |
| `PostCompact` | 运行 `handoff_gate.py`，更新自动压缩后的长会话交接计数与阶段判断 |
| `UserPromptSubmit` | 运行 `handoff_gate.py`，根据用户最新指令更新交接状态或暂缓宽限期 |
| `PreToolUse` | 运行 `status_guard.py` 检查目录写边界、重复读日志、绕过脚本的裸 CLI 调用与重负载批准票据，随后检查交接阶段限制 |
| `Stop` | 运行 `reply_repeat_guard.py` 检查重复空等回复与重负载完成报告，随后由 `handoff_gate.py` 检查强制交接提示词完整性 |

五个共用钩子脚本为 `user_words.py`、`session_context_loader.py`、`status_guard.py`、`reply_repeat_guard.py` 与 `handoff_gate.py`。Antigravity 通过原生插件事件 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 挂载上述检查；DeepSeek Harness 当前命令行未提供同类事件钩子接口，安装时仅同步全局规则与技能目录。

## 五、日常与维护入口

| 使用场景 | 对应入口 | 执行边界 |
| --- | --- | --- |
| 业务课题推导、编码验证与独立审查 | [cae-harness 技能](template/user-guard/skills/cae-harness/SKILL.md) | 支持用户点名或角色选路；重负载须先生成计划、获批登记票据后再执行 |
| 全局工具链全面更新 | [fleet-evolve 技能](template/user-guard/skills/fleet-evolve/SKILL.md) 的 `full` 流程 | 联网核查客户端升级、评估五角色与运行参数，在关键节点停下请用户决定 |
| 代码加固与文档整篇重写 | 同技能的 `hygiene` 流程 | 基于已审定配置修复缺陷并重写文档，不擅自升级外部工具或改动长期推荐矩阵 |
| 本机配置只读巡检 | 同技能的 `audit` 流程 | 只读检查配置漂移、卡片状态、配额冷却与 ABI 耦合风险，零文件修改 |
| 文档词法事实核验 | `cae_harness.py check-grounding --target . --strict` | 扫描源码与配置建立词法集合，拦截文档中无依据的参数名、缩写或失步数字（不替代数学证明与运行测试） |

常用安装器附加选项包括 `--sync-model-defaults`、`--no-model-config`、`--codex-handoff-mode`、`--claude-handoff-mode`、`--no-zotero` 与 `--skip-test`。默认模型配置采取“缺省补齐、已有保留”策略；覆盖已有模型设置须显式传入对应选项并获批准。完整参数说明见 `install.py --help`。

仓库目录结构：`install.py` 为跨平台分发入口；`template/user-guard/` 存放四端共用规则、钩子、代理与技能模板；`template/codex/` 与 `template/claude/` 存放客户端专属适配文件；`docs/` 存放架构与日常工作流文档；`tests/` 存放离线单元测试与端到端沙箱集成测试。插件唯一版本号定义在 [plugin.json](template/user-guard/plugin.json)，版本演进历史记录在 [CHANGELOG.md](CHANGELOG.md)。

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 -m unittest tests.test_e2e_integration
```

修订重要文档时，先按需要运行 `cae_harness.py task --kind review --clean-room` 从新读者视角排查生硬行话与阅读断层，再由主会话整篇重写，最后对照源码核实技术事实、公开脱敏并通过 `check-grounding --strict` 校验。

汇报研发与验证进展时，严格区分“源码或配置已写好”“编译或离线单测已通过”“本机实际环境或算例实测已通过”三个层次；测试验收严禁通过放宽 `rtol`、`atol`、弱化离散守恒或网格收敛阶判据、删改断言来换取通过。

延伸阅读：[架构与钩子说明](docs/architecture-and-hooks.md) · [日常工作流指南](docs/workflow-guide.md) · [插件模板说明](template/user-guard/README.md) · [仓库维护说明](AGENTS.md)。

## 六、致谢、English Overview & License

**致谢**（Acknowledgments）：本项目获**中国国家留学基金委**（**China Scholarship Council, CSC**）资助，学号为 `202506280162`，批准文号为 `留金选[2025]37号`；目前唯一资助来源为该项资助。

### English Overview

`Antigravity_Codex_ClaudeCode_DeepSeekHarness` provides shared engineering rules, deterministic session hooks, read-only review subagents, lexical grounding checks, and headless verification workflows for Google Antigravity, OpenAI Codex, Claude Code, and DeepSeek Harness. Built solely on the Python 3.11+ standard library across Windows, WSL2, and Linux, it is designed for scientific computing, CAE, CFD, FEA, numerical PDE solvers, HPC, and high-reliability software engineering.

All five role slots and quantitative runtime thresholds are configured in `fleet_profile.json` while keeping the user's interactive model selection untouched. Explicit user choices take top priority (P0), with role-based selection filling unspecified parameters. Availability is checked in two stages: user-approved enablement in `subscription_matrix` during `fleet-evolve` maintenance, followed by an offline local pre-dispatch check of binaries, credentials, local router health, and locally readable quota records (leaving unexposed server balances explicitly marked as unknown, and reading offline benchmark timestamps from `capability_ledger` without live web lookups).

Multi-stream or write-capable workloads across both in-client native subagents and external headless processes require a 14-column confirmation plan and an approved ticket enforced at `PreToolUse`. Pending confirmation plans are reloaded independently after context compaction and embedded into session-handoff prompts. At turn completion, the `Stop` hook checks that completion reports include outcome terms, actual Token usage (or an explicit unavailable notice), and channel-specific stream/receipt identifiers, while leaving semantic and arithmetic verification to the main session; see [Workflow Guide](docs/workflow-guide.md) for exact rules.

Network access, client installations, and official credentials are prepared by the user. Built-in rules and prompts are written in Simplified Chinese. This project is currently supported solely by the China Scholarship Council (CSC), No. `202506280162`, Approval No. `留金选[2025]37号`.

本项目采用 [Apache License 2.0](LICENSE)。衍生分发须按协议第 4(d) 条保留 [NOTICE](NOTICE) 原创归属声明：`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`。
