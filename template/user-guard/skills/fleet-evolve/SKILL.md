---
name: fleet-evolve
description: >
  在上游配置仓库或明确授权的全局工具链任务中维护四客户端配置。
  全面更新评估外部能力与本机条件，全量优化加固现有实现，
  通过具体提案、用户批准、事实锚定和分层验收完成变更。
---

# fleet-evolve 维护规程

本技能用于上游四客户端配置仓库，或用户明确要求演进宿主机全局工具链的任务。通用课题项目的推导、求解器实现和文档修订使用 `cae-harness`，不能借此修改宿主机配置或长期模型推荐。

先核对任务授权与写入范围，再用 `scripts/fleet_state.py status` 读取工作区身份和状态锁。工作区或父目录同时包含 `install.py` 与 `template/user-guard/plugin.json` 时，可识别为 `workspace_mode: upstream_config_repo`。已安装到独立目录的 `fleet_state.py` 在通用目录未指定 `--repo` 调用时返回 `not_upstream_config_repo`，退出码为 `2`；直接调用上游仓内脚本时，还会沿脚本路径识别上游仓库。状态文件、缓存和制品也受授权边界约束。限定文件的实现包只完成包内工作，不为补齐通常流程而调用本编排器、改变其他会话或写入全局状态。

## 维护模式与单一配置来源

下表说明工作模式，不是额外的命令行参数。

| 模式 | 要完成的工作 | 边界 |
| --- | --- | --- |
| `full`，全面更新 | 收集官方更新和本机记录，核对升级、模型能力、本机可用性与额度类型，评估角色分工及运行参数。 | 提交具体方案，由用户决定后才实施升级或同步。 |
| `hygiene`，全量优化 | 基于已审定配置修正代码、钩子及路由问题，整篇重写文档，完成事实核对、脱敏和回归。 | 不自动升级外部工具，不自行改变长期推荐或运行阈值。 |
| `audit`，只读巡检 | 核对配置漂移、供应商卡片、配额冷却和二进制接口（ABI）风险。 | 不安装、不升级、不刷新缓存、不改变状态。 |

规则、技能、安装器、钩子和派工脚本负责执行机制；[fleet_profile.json](../cae-harness/fleet_profile.json) 是角色与参数的唯一配置来源。评估覆盖 `orchestrator`、`math`、`code`、`review`、`fast` 全部五个槽位，不永久固定某个客户端或模型；同时按可选的 `native_agent_profiles` 独立管理各客户端自身的主会话、内生子代理模型/挡位与并发策略，不与 `cae-harness` 跨端槽位混写。候选、推荐挡位、声明最大窗口、短板隔离和 `runtime_tunables` 集中维护，常驻文档不散布时期性模型名、静态日期和运行阈值。

`runtime_tunables` 包含压缩恢复、长会话交接、循环与轮询防护、无头派工、本地路由和确认门禁。压缩恢复按 `compaction_recovery` 的客户端配置并由 `hooks/_lib/adaptive_policy.py` 结合当前模型与推理强度动态缩放预算，无条件保留最新返回，同时优先归档候选窗口内达到实质长度阈值的推导与审查。测试可用 `CAE_FLEET_PROFILE` 指向隔离配置，不能让测试配置覆盖正在使用的全局配置。

## 第一道门：联网评估与启停决策

`full` 同时回答能力是否适合任务、本机目前是否可用、本机配置何种额度三个问题，并按四个独立维度逐项盘点与提审：

1. **四端客户端与配套工具**：分别核查 Antigravity、Codex、Claude Code、DeepSeek Harness 与 CC Switch 的本地版本、上游更新及协议能力（DSH 须核对 `hook_protocol`、`hard_hooks_supported` 与 `meta_update_trigger`，CC Switch 须核对卡片、路由与旧残余提示词）。
2. **Windows 与 WSL2 双端**：依据 `install.py --audit-upgrade` 输出的 `cross_side_status`，在已检测到对侧客户端或配置时分别对 Windows 与 WSL2 两侧执行只读体检、预览与同步，不以跨端互操作代替对侧原生配置对齐。
3. **各客户端内生代理维度**：在 `native_agent_profiles` 中独立评估每个客户端自身的主会话默认模型与挡位、内生子代理机制（如 `invoke_subagent`、`swarm`、`spawn_agent`、`Agent`）、只读代理定义与并发上限，与跨端派工解耦。
4. **跨端协同维度（`cae-harness`）**：独立评估五个角色槽位、`subscription_matrix`、`runtime_tunables`、有头原生与无头跨端通道分工，以及 `--isolation`（`full`、`no_skills`、`hooks_only`、`clean_room`）在各端的实际支持边界。

以官方资料、本机报错、只读探测和已授权实测分别支撑结论。程序存在不能证明登录有效，官方声明可用不能证明本机已配置，订阅记录也不能证明当前尚有额度。

对每个候选 agent 与 LLM 展示全网评估依据、本机当前可用性、额度类型、适合角色、优势和限制。额度按实际配置区分滚动窗口、卡片套餐、余额及日月限额，不能把不同通道合并成一种无限额度。资料或本地信息缺失时明确标为未知。

把评估结果呈交用户，由用户在审批节点决定启用或停用哪些 agent 与 LLM，写入 `subscription_matrix.agents` 和 `subscription_matrix.models`。退订后保留记录并设 `enabled: false`，日常选路全局跳过。启停、角色推荐和运行期参数都是长期配置决定，不能由某次派工票据的批准替代。

评估须同时检查优势和严重短板，并按两条正交能力轴拆解角色归属，不得仅凭综合榜单总分指派主控：① **无确定性验证器场景下的审慎拒答校准**（结合无工具幻觉率与拒答率拆解、工程中文平实度及推导条件完整性，决定未闭环理论总控、论文定稿、历史归档查证与正面独立立论角色）；② **有硬验证器约束下的代理闭环执行与挑刺吞吐**（结合编译、自动化测试或给定待审文本约束下的多文件编码基准、响应速度与资源边界检出能力，决定受 `--write-scope` 与 `--verify-cmd` 约束的实现流、`native_swarm` 并行编码流及对抗反例挑刺流）。同时核查工具协议和钩子支持、长会话压缩行为、区域访问限制、额度中止频率、协议转换失败及自动派生代理死等；阻碍实际运行的严重短板用于排除或参数隔离，不能用总分掩盖。

联网性能资料的刷新时间写入 `capability_ledger.last_online_refresh_at`，本机实测时间写入 `capability_ledger.local_bench_verified_at`。常驻文档仅引用键名；联网更新性能由本维护流程独立承担，日常派工不重复联网查询。

## 第二道门：生成确认单前离线复查

`cae_harness.py plan` 在重负载确认前纯离线复查当前程序、凭据配置、本地路由与可读额度。依据本机配置读取滚动窗口百分比和重置时间、卡片套餐或余额类型、日月限额及配额冷却，过滤不可用和超限通道。路由检查只面向本机回环服务；未暴露的服务端余额不得推测为足额，性能资料仍读取 `capability_ledger`。

主会话遇到实质性数理推导、多文件编码验证或关键代码与文档审查，应主动核实可用端并组织协作。主动触发与用户点名分别处理：精确标识符和口语点名的客户端、模型简称、挡位或省额度意图一律作为 P0 第一优先级，锁定已指定维度，并用 `--user-prompt` 保存原话。`role:*` 是并列入口，不覆盖点名。未指定的窗口、非委派挡位、无头非交互标志、只读小文件行号内联、快照及脱敏由脚本补齐；轻量卡片承担非 `fast` 角色或所需路由未存活时明确报错，不静默降级。

轻任务按 `runtime_tunables.dispatch_confirmation` 阈值静默执行。外部无头调用及内部 `invoke_subagent`、`run_workflow`、`spawn_agent`、`Agent`、`swarm`（`status_guard.py` 对 `swarm add` 按 `tasks` 长度或 `n` 计入通道票据容量）等多路或可写委派属于重负载确认范围。先通过 `plan` 将完整 14 项参数明细写入 `<ticket_id>_card.md` 并生成含可点击明细链接的精简总览卡 `compact_summary_card`，对话中仅呈现精简总览卡与明细链接（勿把多路 14 项长表刷屏上下文），再请用户选择批准、微调后批准或暂缓并交接。

完整参数项和顺序见 [cae-harness 技能](../cae-harness/SKILL.md) 及源码。参数表包含流号与子任务范围、通道、客户端与子代理类型、实际模型及 `inherit` 解析、挡位、窗口、无头与净室/隔离级别状态、访问模式和写入范围及验收命令、内联与超时、可用性、额度、回执位置，并附性能更新时间。未知字段保留未知，不以角色名替代实际模型。

用户明确选择后，用 `plan --approve-ticket` 登记，执行时绑定 `--ticket`，由 `status_guard.py` 核验票据并拦截裸 CLI 旁路。`batch` 只执行外部无头通道，原生通道由对应客户端按获批计划调用。不得因为本技能已经启动，就跳过其中某个重负载工作流的确认。

## 确认被打断与完工核验

等待用户确认期间若发生自动压缩，恢复钩子按 `hooks/_lib/adaptive_policy.py` 动态缩放恢复预算并用 `collapse_historical_cards` 折叠历史确认单明细表，同时从磁盘未决票据独立加载工作流背景、目标、预计后果、精简 4 列总览表、可点击 `<ticket_id>_card.md` 完整明细链接和选项卡（无明细文件时回退 14 项长表）。常规用户原话与子代理摘要另成一块；主会话按精简总览卡与明细链接恢复确认，缺失内容不能凭摘要猜补。

实际模式进入 `drain` 或 `freeze` 强制交接后，`handoff_gate.py` 放行 `check-grounding --strict`，并在 `Stop` 硬门禁要求把工作流背景/目标/预计后果、精简 4 列总览表、`<ticket_id>_card.md` 明细链接、性能更新时间、选项卡、票据与 `plan --resume-ticket` 命令放入【新对话提示词】（无明细文件时仍要求完整 14 项表）。Codex 和 Claude Code 默认是 `advisory` 提醒，不能把默认配置写成强制停机。

新会话以 `plan --resume-ticket` 读取并复核，恢复不等于批准。该命令不自动转移原票据的 `session_id`；按原流内容和当前会话标识重新生成确认计划，再按用户选择登记批准，具体用法见 cae-harness 技能。

重负载结束时，`reply_repeat_guard.py` 在 `Stop` 检查回复是否含有成败词、Token 用量或无法统计说明及回执或日志词项：外部任务按完整词项匹配各流标识（单路 `task` 或 `gate` 省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代）及非空回执的实际文件名；原生任务覆盖完成数量、全部流标识或互不重复的角色名之一并说明日志或轨迹。漏报且同回合尚无其他门禁阻断时单次阻断并附上磁盘账单；主会话负责逐路核对具体状态、Token 明细和汇总算术。

## 四类必须经用户审批的节点

以下事项先提出具体、可审阅的方案，获批前不实施对应动作：

1. 生成或调整客户端、模型与推理强度推荐矩阵，以及 `runtime_tunables`。呈交全网依据、本机可用性、额度类型、启停项、优缺点、备选方案与推荐理由。
2. 写入宿主机全局配置目录或切换主会话客户端。展示具体动作、完整目标路径、原因、保留方式、安装只读预览清单，涉及数据库时给出备份方案。
3. ABI 强耦合升级，包括装有编译型科学计算包的系统级 Python 跨次版本升级、CUDA 跨大版本升级和 WSL 跨 LTS 发行版升级。说明受影响依赖、风险与验证办法。
4. 执行 `git push`。先完成本地授权工作和验收，再按用户当前分工单独处理推送。

先完成不依赖上述决定的已授权工作，再集中提请用户审定。`propose` 仅登记草案，`approve` 仅在用户明确批准后执行；后台角色推荐不改变用户选择的主会话模型或强度。

## 探测、实现与验证

先只读查看相关模块、配置和现有改动，再限定目标文件。常用探测入口如下；仅在当前任务允许时调用：

```powershell
python -B -X utf8 install.py --probe-apps
python -B -X utf8 install.py --audit-upgrade
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --caller auto
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py assess --caller auto
```

`full` 确需刷新模型目录时可显式使用 `--discover-models`，先确认联网和缓存写入已获授权。`hygiene` 与 `audit` 不借此刷新。缺失客户端、登录、网络或密钥时列待办，不编造绕过访问限制的方法。

重要文档按三步处理：需要新读者检查时，以 `task --kind review --clean-room` 使用支持客户端的只读净室；主会话抛开旧措辞整篇重写；再对照源码、公式与配置核对事实和公开脱敏，运行严格事实锚定。净室配置与暂存文件也受目录授权限制。独立关键审查优先跨客户端使用 `role:review` 或双源 `audit`，同端 `readonly-auditor` 辅助逐行复核。审查前后快照须核验零改动，审查代理不得构建或运行测试；所有建议由主会话亲自打开原文件核对行号后决定。

实现与回归由承担修改的会话执行。按实际改动选择离线与沙箱验证，不放宽容差、守恒残差和收敛阶判据，不删改断言。文件采用 UTF-8 无 BOM 和 LF；两份 `AGENTS.md` 各自严格小于 `16,000 B`。公开文档不得写入真实用户名、内网 IP、具体硬件型号、凭据、账号精确账单指纹或会话唯一标识。版本数字和演进日期交给相应版本来源与历史文件，不复制到常驻规则或技能中。

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 -m unittest tests.test_e2e_integration
python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v
```

本机已装 `agy` 时，按维护验收要求使用 `agy plugin validate --strict template/user-guard`。受限工作包按外层验证分工执行，不重复启动未授权或耗时的子进程回归；未执行项不得写成通过。

## 提案、同步与重启复核

触及审批节点时，用 `fleet_state.py propose` 登记具体方案。全局同步先运行 `install.py --dry-run --yes`，展示完整路径并等待用户决定；获批后才执行 `approve` 及对应升级或同步。

Claude 默认随钩子安装 `~/.claude/hooks/ccswitch_admin.py`，不依赖启用路由；`--no-claude-hooks` 跳过该同步，`--with-cc-router` 另行分发路由。CC Switch 旧提示词和技能记录的停用或清理需要相应选项与授权，不能当作默认安装动作。

同步后用 `install.py --audit-upgrade` 核查 `summary.all_aligned`，再对已就绪客户端完成获授权的无头实测。主会话客户端升级或切换需要重启时，先 `record-pass1`，由用户手动重启，再用重启后的会话执行 `verify-pass2`。最后用 `clear --verified --readback-done --session-id "<重启后会话标识>"`（无活跃重启认领锁时可省略 `--session-id`）通过事实锚定硬门禁后归位。

以下从上游根目录执行，路径和会话标识需替换。示例仅展示接口，运行期阈值由动态配置驱动；WSL2 与 Linux 将 `python` 改为 `python3`。

```powershell
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" status
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" propose --session-id "<当前会话标识>" --summary "<待审具体方案>"
```

用户批准后，按实际需要运行对应步骤，示例本身不是授权：

```powershell
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" approve --session-id "<当前会话标识>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" record-pass1 --session-id "<当前会话标识>" --target-master "<目标客户端>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" verify-pass2 --session-id "<重启后会话标识>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" clear --verified --readback-done --session-id "<重启后会话标识>"
```

`propose` 与 `approve` 校验仓库及配置摘要，方案变化须重新提审，不能通过强制清锁绕过批准或重启复核。最终报告 Git 状态、实际修改范围，并分别说明配置或文档已完成、离线或沙箱检查已通过、本机实际环境或算例实测已通过；仅列当前目标环境和核心验证链路的未验证项。提交与推送遵从用户当前分工，不擅自执行远端推送。
