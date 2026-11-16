# 架构、事件钩子与验证边界

本文说明当前源码如何连接动态配置、客户端事件、派工票据与验证回执。安装入口见 [仓库总说明](../README.md)，操作步骤见 [工作流指南](workflow-guide.md)。版本历史只在 [更新日志](../CHANGELOG.md) 中记录。

![动态配置与双门可用性架构](assets/architecture-overview.svg)

## 一、配置来源与模块关系

规则、选型和执行代码分别维护。[全局规则模板](../template/user-guard/rules/AGENTS.md) 规定工程边界；[fleet_profile.json](../template/user-guard/skills/cae-harness/fleet_profile.json) 保存动态角色和运行参数；脚本读取配置并执行检查。README 的现阶段角色表仅供人工审阅，不作为第二份配置。

| 模块 | 输入与职责 | 适用范围 |
| --- | --- | --- |
| [install.py](../install.py) | 探测客户端、空跑分发计划、合并事件绑定并同步托管资产 | 上游安装与全局配置同步 |
| [cae_harness.py](../template/user-guard/skills/cae-harness/scripts/cae_harness.py) | 点名解析、五角色选路、离线可用性检查、确认票据、外部执行、事实锚定 | 通用课题及上游 |
| [user_words.py](../template/user-guard/hooks/user_words.py) | 共享配置读取、原话与子代理记录提取、状态读写、工作流票据恢复 | 共用钩子基础模块 |
| [session_context_loader.py](../template/user-guard/hooks/session_context_loader.py) | 检测压缩恢复入口，有界恢复常规记录，独立加载未决确认单 | 已挂载恢复事件的客户端 |
| [status_guard.py](../template/user-guard/hooks/status_guard.py) | 工具前检查写边界、重复读日志、裸 CLI 调用与重负载票据 | 已挂载工具前事件的客户端 |
| [reply_repeat_guard.py](../template/user-guard/hooks/reply_repeat_guard.py) | 回合结束检查重复等待回复与重负载完工报账 | 已挂载结束事件的客户端 |
| [handoff_gate.py](../template/user-guard/hooks/handoff_gate.py) | 更新交接阶段，强制阶段限制新任务并检查交接提示词 | 根据当前客户端与模式执行 |
| [fleet_state.py](../template/user-guard/skills/fleet-evolve/scripts/fleet_state.py) | 上游身份、提案批准、重启重入和终审状态锁 | 上游维护专用 |
| [router.py](../template/claude/router/router.py)、[ccswitch_admin.py](../template/claude/router/ccswitch_admin.py) | 本地协议分流、供应商卡片读取、备份与切换 | 可选本地分流与卡片管理 |

`workspace_mode` 根据工作区或父目录中的 `install.py` 与 `template/user-guard/plugin.json` 识别上游仓库，标记为 `upstream_config_repo`；普通业务仓库为 `general_project`。已安装到独立目录的 `fleet_state.py` 在通用目录未指定 `--repo` 调用时返回 `not_upstream_config_repo`，退出码为 `2`。直接调用上游仓内脚本时，还会沿脚本路径识别上游仓库，因此不能将前一结论套用于这一入口。

事件脚本按宿主事件启动后退出，不常驻轮询。可选 `router.py` 是本地回环转发进程；它的健康检查不代表上游余额查询或登录验证已经完成。

## 二、双门可用性与动态参数

第一道门属于独立维护流程：`fleet-evolve` 联网评估能力，展示本机可用性与额度类型，用户决定 agent 和 LLM 启停。结果写入 `subscription_matrix.agents`、`subscription_matrix.models`，停用项 `enabled: false` 在选路时跳过。

第二道门发生于日常 `plan` 弹卡前，纯离线复查当前配置与本机状态。读取内容包括程序、凭据配置、所需路由健康、本地轨迹中的滚动窗口百分比及重置时间、供应商卡片的套餐或余额类型、日月限额及运行侧冷却记录。不可用或超限通道被过滤；本机未暴露的信息保持未知，不能推断服务端实时余额充足。

性能记录由 `capability_ledger` 提供。确认单显示 `last_online_refresh_at` 和 `local_bench_verified_at`，日常派工不联网更新性能；在线刷新由独立 `fleet-evolve` 流程负责。历史性能记录、本机配置状态和本轮实际执行证据应分别解释。

| 配置分组 | 控制内容 | 校核重点 |
| --- | --- | --- |
| `roles` | `orchestrator`、`math`、`code`、`review`、`fast` 的候选、模型系列、推荐挡位及优缺点 | 不永久绑定客户端；用户点名优先 |
| `runtime_tunables.compaction_recovery` | 当前端内联预算、候选返回窗口、实质长度阈值和首尾摘要比例 | 最新一条返回保留，其他实质结论按配置筛选 |
| `runtime_tunables.handoff_thresholds` | 各端交接模式、阈值与收尾动作预算 | Codex 与 Claude Code 默认 `advisory`，不能描述成默认强制冻结 |
| `runtime_tunables.loop_guards` | 重复回复、重复日志读取与生命周期日志管理 | 检查实际事件与文件变化，避免无进展轮询 |
| `runtime_tunables.headless_dispatch` | 分片预算、超时、验证轮数、内联上限、后端并发与路由参数 | 不在说明文档复制静态阈值 |
| `runtime_tunables.dispatch_confirmation` | 轻重任务阈值、票据有效期及额度预警和拦截阈值 | 内部原生与外部无头通道一并纳入 |

`CAE_FLEET_PROFILE` 可用于指定配置来源。调整上述角色矩阵和运行参数须另获批准，不能因某次派工已经批准就直接修改。

## 三、全通道确认与工具前门禁

用户以标识符或口语点名客户端、模型、挡位或省额度意图时，`--user-prompt` 保留原话并按 P0 锁定已指定维度。角色入口补齐未指定维度。外部执行补齐声明窗口、非委派挡位、无头标志、只读行号内联、快照和脱敏；原生 `inherit` 解析依据当前客户端配置，未知时明确显示未解析。

`plan` 支持单流参数及 `--spec-file` 对象列表。通道标识包括 `cae_harness_headless`、`native_subagent`、`native_workflow`、`native_swarm`。原生通道不属于外部无头调用，也不提供净室隔离。

重负载确认单包含背景、要做什么、预计后果、14 列完整矩阵、性能记录更新时间及选项卡。列序由 `_render_plan` 定义：

| 列号 | 字段 |
| --- | --- |
| 1 | 流号 |
| 2 | 子任务与范围 |
| 3 | 通道 |
| 4 | 客户端及子代理类型 |
| 5 | 模型名字，包含 `inherit` 解析结果 |
| 6 | 挡位 |
| 7 | 上下文窗口 |
| 8 | 是否无头 |
| 9 | 是否净室 |
| 10 | 访问模式、写入范围与验收命令 |
| 11 | 内联与超时 |
| 12 | 当前是否可用 |
| 13 | 本机配置额度类型与当前状态 |
| 14 | 回执或子代理日志位置 |

用户选择批准当前计划、微调后批准或暂缓并交接。`plan --approve-ticket` 登记批准前重新解析流并复查可用性；执行绑定内容一致、未过期的 `APPROVED` 票据。主会话不能把角色名代替实际模型，也不能省略未知字段。

`status_guard.py` 在 `PreToolUse` 拦截已识别的裸客户端无头命令，要求通过统一入口保留窗口、快照和额度检查。外部重负载须有有效票据；内部原生调用按工具类型、只读性质和当前回合累计流数判断，多路或可写委派须有包含原生通道的批准计划。具体执行参数仍由执行入口校验，不能把工具前静态检查描述成对任意命令语义的证明。

同一检查入口还负责写边界和日志防轮询：解析文件工具、Shell 包装、重定向与内联 Python 静态路径，识别项目外写删和越界 `git worktree`；对未变化日志的相同区间重复读取进行拦截。当前任务授权比默认放行范围更窄时，按当前授权执行。

## 四、Codex 五事件与客户端挂载

![会话事件、确认接力与验证回执](assets/lifecycle-and-verification.svg)

Codex 的 [事件模板](../template/codex/hooks.json) 与 Claude Code 的 [事件模板](../template/claude/hooks.json) 对齐五类事件。五类事件与五个共用脚本模块是两种计数，`user_words.py` 是共享基础模块，不单独对应一个事件。

| 事件 | 执行顺序 | 主要检查 |
| --- | --- | --- |
| `SessionStart` | `session_context_loader.py`，然后 `handoff_gate.py` | 在 `compact` 或 `resume` 入口恢复原话、实质子代理摘要和未决工作流，再判断交接状态 |
| `PostCompact` | `handoff_gate.py` | 更新压缩后的交接状态 |
| `UserPromptSubmit` | `handoff_gate.py` | 依据新指令更新交接判断 |
| `PreToolUse` | `status_guard.py`，然后 `handoff_gate.py` | 写边界、日志重复读取、反裸调、重负载票据与交接阶段限制 |
| `Stop` | `reply_repeat_guard.py`，然后 `handoff_gate.py` | 重复回复、完工报账与强制交接提示词完整性 |

| 客户端 | 全局挂载与事件差异 |
| --- | --- |
| Antigravity | `~/.gemini/config/plugins/user-guard`；插件事件为 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop`，相关入口内调用交接检查，状态使用宿主注入的 `PLUGIN_DATA` |
| Codex | `~/.codex`；同步代理、钩子和技能，`hooks.json` 挂载上述五事件 |
| Claude Code | `~/.claude`；把五事件合并到 `settings.json`，默认同步钩子中的卡片管理脚本，可选同步 `router/` |
| DeepSeek Harness | `~/.dsh`；同步 `AGENTS.md` 与技能，不修改原始 `.credentials.yaml`，当前不挂载上述事件链 |

安装器比较 Codex 钩子实质内容，内容一致时避免重写以保留已有信任信息。规则同步以完整标准模板为基线，保留标准 `## 工具与运行环境`、`R10.` 与 `R11.`；识别用户自定义环境尾段后保留内容，将其中冲突编号迁移为 `L1.`、`L2.`。

## 五、压缩恢复与交接接力

常规恢复先提取用户原话，再按当前端配置筛选子代理返回。Antigravity 记录带 `truncated_fields` 时先回查完整轨迹再判断实质长度；最新一条返回无条件保留，候选窗口内其他记录按实质长度、角色关键词和时间筛选。归档与内联摘要分别基于原文生成，避免二次截断。子代理结论独立分区，不混入用户指令。

未决工作流另从磁盘票据加载，不依赖常规摘要是否保留了派工细节。恢复输出分成两块：常规用户原话及实质返回；工作流专项人话和完整确认单。后者保留背景、目标、后果、完整矩阵、性能更新时间、选项卡和票据。缺失或损坏时报告缺口，不推测补齐，也不因恢复而批准。

当实际模式进入 `drain` 或 `freeze`，交接检查限制新开代理、推送和批量读图，保留事实锚定等收尾入口。`Stop` 要求【新对话提示词】包含完整工作流与选项卡，不能只写“接着上次做”。Codex 与 Claude Code 默认提醒模式，强制停机行为以实际模式为准。

新会话用 `plan --resume-ticket` 读取并复核。该命令保留旧票据的 `session_id`，不会自动迁移归属；跨会话执行须根据原流内容与当前会话标识重新形成确认票据。计划改变、过期或校验失败时重新确认。

## 六、外部执行、结构化回执与事实锚定

`batch` 按 `batch_max_workers_per_backend` 控制各后端并发，仅执行 `cae_harness_headless`。含原生通道的确认计划由对应客户端执行。后端触发额度中止后停止其排队流，已经启动的流按实际结果记录。额度耗尽停止重试；自动回退还要求显式 `--fallback-on-quota`、工作区零改动及点名与确认约束相容。

全后端只读小文件默认通过 `--inline-readonly` 附带精确行号，内联上限读取 `inline_readonly_max_kib`；不能据此声称所有目标都完整内联。只读任务禁止构建测试并核对前后快照；可写任务限定范围，`gate` 强制执行验收命令。净室仅用于受支持后端的只读任务，其临时目录也服从任务授权。

DSH 双版本适配按命令能力探测，不只按版本字符串猜测：检查 `--profile headless --help` 是否声明权限参数；支持时传 `--permission-mode` 与 `--approval-policy`，不支持时通过一次性补丁和 `DSH_PERMISSION_MODE` 设置模式。输出由单 JSON 约束、平衡对象解码、外层围栏与 DSML 尾部清洗共同处理。

单分片预算由 `_execution_budget` 计算：优先取模型声明最大窗口，否则取默认窗口；有有效窗口比例时先缩减，再与已知压缩上限取较小值，最后应用配置安全比例。显式输入预算受已知容量约束；未知窗口采用配置的保守预算并标明来源。模型窗口、前端配置、压缩触发点和输入规划预算不能混为一谈。

`reply_repeat_guard.py` 在完工 `Stop` 检查回复中是否含有成败词，以及 Token 用量或无法统计说明。外部任务还须覆盖各流标识和账单中的实际回执文件名；单路 `task` 或 `gate` 可用模型名或任务状态代替默认流号。原生任务须覆盖完成数量、各流标识或各角色名中的一项，并提供日志或轨迹说明。流标识和外部回执文件名按完整词项匹配。漏报时同一回合单次阻断并附上磁盘账单。主会话仍须自行逐路核对具体状态、Token 明细与汇总算术，静态检查不核实这些内容的语义和计算结果。

`check-grounding --strict` 从源码、配置及受控资产构建词法锚点，检查标识符、选项、大写缩写、定量数字与领域举例。它用于拦截无依据的文档漂移，不证明公式、模型性能或算例已经验证。`polish` 自动附 `grounding_audit`；上游 `clear --verified --readback-done` 也执行事实锚定硬门禁。

可选 `router.py` 按模型前缀转发兼容 `/v1/messages` 的请求，显式 `low`、`medium` 及其他非高挡值不受 `force_effort` 覆盖；缺省或已有高挡按路由配置处理。`fleet_state.py` 校验状态结构、有效期、运行侧与仓库哈希；事实锚定模块缺失、导入失败或返回结构无效时拒绝归位。带验收标记的归位在状态锁内检查有效待批提案，默认拒绝清除，显式覆盖使用 `--force`。相关配置修改和全局写入仍须独立授权。
