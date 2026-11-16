# user-guard 共用模板与插件入口

本目录既是 Antigravity 原生插件，也是四客户端共用规则、生命周期钩子与技能配置的上游来源。根目录的 [install.py](../../install.py) 按各客户端宿主协议分发模板资产；日常业务课题直接使用本机已安装的全局资产，无需向课题仓库复制文件。版本只在 [plugin.json](plugin.json) 中维护，历史演进见 [更新日志](../../CHANGELOG.md)。

## 文件归属

| 路径 | 职责 |
| --- | --- |
| [plugin.json](plugin.json)、[hooks.json](hooks.json)、[assets/logo.svg](assets/logo.svg) | 插件清单、Antigravity 原生插件事件绑定与图标 |
| [rules/AGENTS.md](rules/AGENTS.md) | 四端共用的全局工程规则唯一模板；安装时保留完整标准章节与末尾用户自定义尾段 |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator`；默认 `inherit` 跟随主会话，禁止修改文件或运行构建测试 |
| [hooks/](hooks/) | 五个共用钩子模块：原话与实质返回提取、压缩恢复、工具前写边界与票据检查、重复空等与完工报账、长会话交接 |
| [skills/cae-harness/](skills/cae-harness/) | 日常派工、双门可用性复查、纵向 14 项确认票据、无头验证与 `check-grounding` 事实锚定 |
| [skills/cae-harness/fleet_profile.json](skills/cae-harness/fleet_profile.json) | 五角色、运行期定量参数、订阅启停矩阵与离线性能记录时间的唯一动态配置来源 |
| [skills/fleet-evolve/](skills/fleet-evolve/) | 上游配置仓专属的工具链评估、拍板提案、全局同步与重启重入验收 |

客户端专属适配文件分别位于 [template/codex](../codex/) 与 [template/claude](../claude/)。各客户端按自身事件协议接入同一套共用钩子模块：Antigravity 通过原生插件挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类事件（内部联动 `handoff_gate.py`）；Codex 与 Claude Code 对称挂载 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件（事件类别数与五个共用脚本模块分别统计）；DeepSeek Harness 当前 CLI 无交互事件钩子接口，安装时同步全局规则与技能，并通过 `cae_harness.py` 获得完整无头调用保护。

## 从配置到执行

用户点名按 P0 第一优先级锁定客户端、模型、挡位或省额度意图，通过 `--user-prompt` 保留原话。`role:*` 是并列的职责入口，未指定部分由配置自动补齐。外部后台调用自动补齐声明最大窗口、非委派挡位、无头标志、只读文件精确行号内联、前后快照检查与回执脱敏；内部原生代理按当前客户端配置能力执行。

可用性经过两道门：`fleet-evolve` 独立维护评估后由用户拍板决定 `subscription_matrix` 启停；`plan` 在弹卡前纯离线复查当前程序、凭据、路由、本地可读额度与冷却记录，未知信息保持未知。日常派工只读 `capability_ledger.last_online_refresh_at` 与 `capability_ledger.local_bench_verified_at`，不触发在线性能更新。

多路或可写重负载覆盖内部原生与外部无头全部通道。`plan` 生成平实中文背景、要做什么、预计后果及分路纵向展开的 14 项完整参数表，用户选择后用 `--approve-ticket` 登记，执行绑定 `--ticket`。`PreToolUse` 核验票据并拦截裸调底层无头 CLI；`batch` 仅执行外部无头通道。

压缩后常规原话摘要与未决工作流确认单分两块独立恢复。进入强制交接阶段时，`Stop` 核对【新对话提示词】内的完整待选工作流与选项卡；新会话用 `plan --resume-ticket` 复核旧票据但不自动改绑会话，跨会话正式执行前重新生成绑定当前会话的确认票据。重负载完工后，`Stop` 检查成败词、Token 用量或无法统计说明及回执或日志词项（外部任务按完整词项匹配各流标识及非空回执实际文件名，单路 `task`/`gate` 默认 `S1` 可用模型名或任务状态替代；原生任务匹配完成数量、全部流标识或互不重复的角色名之一及轨迹/日志说明）；漏报且同回合无其他门禁阻断时单次阻断并附磁盘账单，由主会话逐路核实具体状态与算术汇总。

## 本地校验

在仓库根目录运行。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`：

```powershell
agy plugin validate --strict template/user-guard
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 install.py --dry-run --yes
```

空跑仅展示拟写入计划，正式写入宿主机全局目录须另获批准。标准 `## 工具与运行环境` 及 `R10.`、`R11.` 完整保留；用户末尾追加的自定义段落中的冲突编号自动迁移为 `L1.`、`L2.`。

总览与快速安装见 [仓库总说明](../../README.md)，共有机制与四端协议适配见 [架构设计与钩子说明](../../docs/architecture-and-hooks.md)，日常用法与算例模板见 [日常工作流实战指南](../../docs/workflow-guide.md)。完整命令规程以 [cae-harness 技能](skills/cae-harness/SKILL.md) 为准。
