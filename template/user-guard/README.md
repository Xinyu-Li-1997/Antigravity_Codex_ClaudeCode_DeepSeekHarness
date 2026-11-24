# user-guard 共用模板与插件入口

本目录既是 Antigravity 原生插件，也是四客户端共用规则、生命周期钩子与技能配置的上游源头。根目录的 [install.py](../../install.py) 按各客户端协议分发这些资产；日常课题直接使用本机已安装的全局配置，不用往业务仓库复制文件。版本号只在 [plugin.json](plugin.json) 单点维护，演进历史见 [更新日志](../../CHANGELOG.md)。

## 文件归属

| 路径 | 职责 |
| --- | --- |
| [plugin.json](plugin.json)、[hooks.json](hooks.json)、[assets/logo.svg](assets/logo.svg) | 插件清单、Antigravity 原生插件事件绑定与图标 |
| [rules/AGENTS.md](rules/AGENTS.md) | 四端共用的全局工程规则唯一模板（`R0–R11`），坚持“纠错优先下沉机械守卫、已守住条文及时减薄”；安装时保留完整标准章节与用户自定义尾段 |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator`；默认 `inherit` 跟随主会话（优先识别显式 `model`），禁写文件、禁跑构建与测试 |
| [hooks/](hooks/) | 五个共用钩子脚本与 `hooks/_lib/` 公共库：提供进程墙钟/内存自毁护栏、自适应压缩恢复、无头子进程非写钩子隔离、工具前写边界与票据门禁（含同会话外部目录续租与 `swarm` 流数统计）、重复刷屏与局部补测拦截、完工回执核验与长会话交接保护 |
| [skills/cae-harness/](skills/cae-harness/) | 跨端无头派工与门禁验证：双门可用性复查、审查阻断/建议分级与风险分层选挡、分后端只读内联与自适应快照、默认持久化会话（`dsh` 独立 `headless-sessions`）与 `followup` 超时降挡续查、双层超时抢救、`--files`/`wave` 波次编排、`--isolation` 四级隔离、`<ticket_id>_card.md` 明细落盘与 `check-grounding` 事实锚定 |
| [skills/cae-harness/fleet_profile.json](skills/cae-harness/fleet_profile.json) | 五角色（`roles`）、四端内生代理档案（`native_agent_profiles`）、运行期定量阈值（`runtime_tunables`）、启停矩阵（`subscription_matrix`）与离线更新时间（`capability_ledger`）的唯一动态配置源 |
| [skills/fleet-evolve/](skills/fleet-evolve/) | 上游配置仓专属的四维工具链评估、审批提案、双端同步与重启重入验收 |

客户端专属适配模板分别位于 [template/codex](../codex/) 与 [template/claude](../claude/)。各客户端按原生协议接入同一套共用钩子：Antigravity 挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类事件（内部联动 `handoff_gate.py`）；Codex 与 Claude Code 挂载 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件；DeepSeek Harness 当前 CLI 为 `soft_rules_only`，安装器同步规则与技能并持续探测 `hook_protocol`，通过 `cae_harness.py` 无头调用时仍具备完整保护。

## 从配置到执行

- **点名优先与动态角色并列**：用户点名按 P0 锁定客户端、模型、挡位、隔离级别或省额度意图（经 `--user-prompt` 保留原话）；`role:*` 按职责动态选路，零碎小修直接主会话闭环，接口清晰的成块模块与独立审查再经 `cae-harness` 派工。
- **两道门禁与极简确认单**：第一道门由 `fleet-evolve` 评估并经用户审定 `subscription_matrix`；第二道门由 `plan` 纯离线核查本机程序、凭据、路由与可读额度（只读 `capability_ledger` 时间戳，不联网刷新）。多路或可写任务先落盘 `<ticket_id>.json` 与含 4 列总览及 14 项明细的 `<ticket_id>_card.md`，日常对话只展示 3 句话确认条 `chat_brief` 与明细链接，获批登记后由 `PreToolUse` 核验票据并拦截裸调底层无头 CLI。
- **压缩恢复与完工核验**：压缩后钩子自动折叠历史长表，分两块还原历轮原话与未决票据总览；强制交接时 `Stop` 核验【新对话提示词】内的精简总览、明细链接、离线更新时间、选项卡与 `plan --resume-ticket` 命令。重负载完工后，`Stop` 检查成败词、Token 用量或无法统计说明及回执或日志词项（单路 `task`/`gate` 省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代），主会话仍须逐路核实状态与汇总算术。

## 本地校验

在仓库根目录运行（首条命令需已装 Antigravity CLI `agy`；Windows 用 `python -B -X utf8`，WSL2/Linux 用 `python3 -B -X utf8`）：

```powershell
agy plugin validate --strict template/user-guard
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 install.py --dry-run --yes
```

只读预览仅展示拟写入路径，正式写入宿主机全局目录须先获批。同步时完整保留标准 `## 工具与运行环境` 及 `R10.`、`R11.`，用户末尾自定义段落中的冲突编号自动迁移为 `L1.`、`L2.`。

总览与快速安装见 [仓库总说明](../../README.md)，底层机制与四端适配见 [架构设计与钩子说明](../../docs/architecture-and-hooks.md)，日常用法与算例模板见 [日常工作流实战指南](../../docs/workflow-guide.md)，完整命令规程以 [cae-harness 技能](skills/cae-harness/SKILL.md) 为准。
