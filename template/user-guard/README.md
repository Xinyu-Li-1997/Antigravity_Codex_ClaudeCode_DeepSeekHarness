# user-guard 共用模板与插件入口

本目录既是 Antigravity 原生插件，也是四客户端共用配置的上游来源。根目录的 [install.py](../../install.py) 按客户端协议分发规则、技能和事件脚本；课题项目直接使用已安装资产。版本只在 [plugin.json](plugin.json) 中维护，历史见 [更新日志](../../CHANGELOG.md)。

## 文件归属

| 路径 | 职责 |
| --- | --- |
| [plugin.json](plugin.json)、[hooks.json](hooks.json)、[assets/logo.svg](assets/logo.svg) | 插件清单、Antigravity 事件绑定与图标 |
| [rules/AGENTS.md](rules/AGENTS.md) | 全局工程规则的唯一模板；安装时保留完整标准规则与用户自定义尾段 |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator`；默认跟随主会话，禁止修改文件或运行构建测试 |
| [hooks/](hooks/) | 原话归档与恢复、工具前检查、重复回复检查、工作流交接和完工报账 |
| [skills/cae-harness/](skills/cae-harness/) | 日常派工、全通道确认票据、后台验证与 `check-grounding` |
| [skills/cae-harness/fleet_profile.json](skills/cae-harness/fleet_profile.json) | 五角色、运行参数、订阅启停与离线性能记录的统一配置 |
| [skills/fleet-evolve/](skills/fleet-evolve/) | 上游维护中的工具链评估、审批、全局同步和重启重入验收 |

Codex 与 Claude Code 的代理和事件适配分别位于 [template/codex](../codex/) 与 [template/claude](../claude/)。Codex 对称挂载 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件；共用脚本模块数量也为五个，但事件与模块并非逐一对应。DSH 安装规则和技能，不挂载同类事件钩子。

## 从配置到执行

用户点名按 P0 第一优先级锁定客户端、模型、挡位或省额度意图，通过 `--user-prompt` 保留原话。`role:*` 是并列的职责入口，未点名部分由配置补齐。外部后台调用补齐声明窗口、非委派挡位、无头参数、只读小文件行号内联和快照检查；原生代理按实际客户端能力执行。

可用性经过两道门：`fleet-evolve` 独立评估后由用户批准 `subscription_matrix` 启停；`plan` 在弹卡前离线复查当前配置、路由、可读额度及冷却记录。未提供的信息保持未知。日常派工只读 `capability_ledger.last_online_refresh_at` 与 `capability_ledger.local_bench_verified_at`，不触发在线性能更新。

多路或可写重负载覆盖内部原生与外部无头通道。`plan` 生成人话背景、目标、预计后果和 14 列完整矩阵，用户选择后用 `--approve-ticket` 登记，执行绑定 `--ticket`。`PreToolUse` 检查票据并拦截裸调；`batch` 仅执行外部通道。

压缩后常规原话摘要与未决工作流确认单分开恢复。强制交接时，`Stop` 核对【新对话提示词】内的完整计划和选项卡。`plan --resume-ticket` 复核旧票据但不改绑会话；跨会话重新形成当前会话确认计划。重负载结束后，`Stop` 检查成败词、Token 用量或无法统计说明及回执或日志词项，外部任务按完整词项匹配各流标识（单路 `task` 或 `gate` 默认 `S1` 可用模型名或任务状态替代）及非空回执实际文件名，原生任务匹配完成数量、全部流标识或互不重复的角色名之一及轨迹或日志说明；漏报且同回合尚无其他门禁阻断时单次阻断并附磁盘账单，由主会话逐路核对具体状态与汇总。

## 本地校验

在仓库根目录运行。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`。

```powershell
agy plugin validate --strict template/user-guard
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 install.py --dry-run --yes
```

空跑只展示计划，正式写入宿主机全局目录须另获批准。标准 `R10.`、`R11.` 保留；用户自定义尾段中的冲突编号迁移为 `L1.`、`L2.`。

安装与现阶段角色审阅见 [仓库总说明](../../README.md)，事件链见 [架构与钩子](../../docs/architecture-and-hooks.md)，命令和接力步骤见 [工作流指南](../../docs/workflow-guide.md)。完整参数和票据规程以 [cae-harness 技能](skills/cae-harness/SKILL.md) 为准。
