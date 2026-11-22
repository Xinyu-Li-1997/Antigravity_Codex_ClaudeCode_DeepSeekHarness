# user-guard 共用模板与插件入口

本目录既是 Antigravity 原生插件，也是四客户端共用规则、生命周期钩子与技能配置的上游来源。根目录的 [install.py](../../install.py) 按各客户端宿主协议分发模板资产；日常业务课题直接使用本机已安装的全局资产，无需向课题仓库复制文件。版本只在 [plugin.json](plugin.json) 中维护，历史演进见 [更新日志](../../CHANGELOG.md)。

## 文件归属

| 路径 | 职责 |
| --- | --- |
| [plugin.json](plugin.json)、[hooks.json](hooks.json)、[assets/logo.svg](assets/logo.svg) | 插件清单、Antigravity 原生插件事件绑定与图标 |
| [rules/AGENTS.md](rules/AGENTS.md) | 四端共用的全局工程规则唯一模板；安装时保留完整标准章节与末尾用户自定义尾段 |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator`；默认 `inherit` 跟随主会话（优先识别显式 `model`），禁止修改文件或运行构建测试 |
| [hooks/](hooks/) | 五个共用钩子模块与共享基础库 `hooks/_lib/`（`fs_safety.py`、`shell_lex.py`、`adaptive_policy.py`、`process_guard.py`）：进程内存与墙钟自毁保护、去噪原话与实质返回提取、按模型与推理强度自适应压缩恢复、工具前写边界（含同会话外部目录批准租约）与票据检查（含 `swarm` 并发流数统计与 `followup` 核验）、同回合待批票据重复刷屏拦截、局部补测与完工回执防翻案核验、长会话交接与待拍板保护 |
| [skills/cae-harness/](skills/cae-harness/) | 日常派工、双门可用性复查、默认持久化可续接无头会话与 `followup` 追问、双层超时抢救（软时限自动降档只读收口与硬超时管道及日志回收）、`--files` 多文件与 `wave` 波次编排（前序可写失败默认阻断后续波次全部流，含只读流）、`--isolation` 四级正交隔离、`<ticket_id>_card.md` 明细落盘与 3 句话极简确认条 `chat_brief` 及 `check-grounding` 事实锚定 |
| [skills/cae-harness/fleet_profile.json](skills/cae-harness/fleet_profile.json) | 五角色（`roles`）、四端内生代理档案（`native_agent_profiles`）、运行期定量参数（`runtime_tunables`）、订阅启停矩阵（`subscription_matrix`）与离线性能更新时间（`capability_ledger`）的唯一动态配置来源 |
| [skills/fleet-evolve/](skills/fleet-evolve/) | 上游配置仓专属的四维工具链评估、审批提案、双端同步与重启重入验收 |

客户端专属适配文件分别位于 [template/codex](../codex/) 与 [template/claude](../claude/)。各客户端按自身事件协议接入同一套共用钩子模块：Antigravity 通过原生插件挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类事件（内部联动 `handoff_gate.py`）；Codex 与 Claude Code 对称挂载 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件（事件类别数与五个共用脚本模块分别统计）；DeepSeek Harness 当前 CLI 处于 `soft_rules_only`（无交互硬钩子），安装时同步全局规则与技能，由 `install.py` 持续探测 `hook_protocol` 并在上游开放硬钩子时触发元更新，通过 `cae_harness.py` 无头调用同样具备完整保护。

## 从配置到执行

用户点名按 P0 第一优先级锁定客户端、模型、挡位、隔离级别或省额度意图，通过 `--user-prompt` 保留原话。`role:*` 是并列的职责入口，未指定部分由配置自动补齐。外部后台调用支持 `--isolation`（`full`、`no_skills`、`hooks_only`、`clean_room`），自动补齐声明最大窗口、非委派挡位、无头标志、只读文件精确行号内联、前后快照检查与回执脱敏；内部原生代理优先识别代理定义中的显式 `model`，缺省按宿主配置与 `native_agent_profiles` 解析 `inherit`。

可用性经过两道门：`fleet-evolve` 独立维护评估后由用户确认决定 `subscription_matrix` 启停；`plan` 在生成确认单前纯离线复查当前程序、凭据、路由、本地可读额度与冷却记录，未知信息保持未知。日常派工只读 `capability_ledger.last_online_refresh_at` 与 `capability_ledger.local_bench_verified_at`，不触发在线性能更新。

多路或可写重负载覆盖内部原生与外部无头全部通道。`plan` 生成平实中文背景、要做什么、预计后果、4 列各路总览表及分路纵向 14 项完整参数明细（先写 `<ticket_id>.json` 再写 `<ticket_id>_card.md`，并同时生成日常对话用的 `chat_brief` 与交接用的 `compact_summary_card`）；日常对话默认仅展示 3 句话极简确认条 `chat_brief` 与明细链接，不贴 Markdown 表格且后台被动唤醒期间勿重复贴票。用户确认后用 `--approve-ticket` 登记，执行绑定 `--ticket`。`PreToolUse` 核验票据、隔离一致性与内部原生通道容量（`swarm add` 按 `tasks` 长度或 `n` 计入并发流数）并拦截直接调用底层无头 CLI；`batch` 仅执行外部无头通道。

压缩后按模型与推理强度自适应预算恢复常规原话摘要（`collapse_historical_cards` 自动折叠历史明细表），并与未决工作流的精简 4 列总览卡及可点击 `<ticket_id>_card.md` 链接分两块独立恢复（仅未落盘明细文件时回退 14 项表）。进入强制交接阶段时，`Stop` 核对【新对话提示词】内的精简 4 列总览表、`<ticket_id>_card.md` 明细链接、离线性能更新时间、选项卡与 `plan --resume-ticket` 命令；新会话用 `plan --resume-ticket` 复核旧票据但不自动改绑会话，跨会话正式执行前重新生成绑定当前会话的确认票据。重负载完工后，`Stop` 检查成败词、Token 用量或无法统计说明及回执或日志词项（外部任务按完整词项匹配各流标识及非空回执实际文件名，单路 `task`/`gate` 省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代；原生任务匹配完成数量、全部流标识或互不重复的角色名之一及轨迹/日志说明）；漏报且同回合无其他门禁阻断时单次阻断并附磁盘账单，由主会话逐路核实具体状态与算术汇总。

## 本地校验

在仓库根目录运行（首条命令需本机已安装 Antigravity CLI `agy`）。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`：

```powershell
agy plugin validate --strict template/user-guard
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 install.py --dry-run --yes
```

只读预览仅展示拟写入计划，正式写入宿主机全局目录须另获批准。标准 `## 工具与运行环境` 及 `R10.`、`R11.` 完整保留；用户末尾追加的自定义段落中的冲突编号自动迁移为 `L1.`、`L2.`。

总览与快速安装见 [仓库总说明](../../README.md)，共有机制与四端协议适配见 [架构设计与钩子说明](../../docs/architecture-and-hooks.md)，日常用法与算例模板见 [日常工作流实战指南](../../docs/workflow-guide.md)。完整命令规程以 [cae-harness 技能](skills/cae-harness/SKILL.md) 为准。
