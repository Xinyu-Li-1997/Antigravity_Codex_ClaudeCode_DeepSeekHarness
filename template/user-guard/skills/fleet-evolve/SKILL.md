---
name: fleet-evolve
description: >
  在上游配置仓库或明确授权的全局工具链任务中维护四客户端配置。
  全面更新评估外部能力与本机条件，全量优化加固现有实现，
  通过具体提案、用户批准、事实锚定和分层验收完成变更。
---

# fleet-evolve 维护规程

本技能用于上游四客户端配置仓库，或用户明确要求演进宿主机全局工具链的任务。通用课题项目的推导、求解器实现和文档修订使用 `cae-harness`，不能借此修改宿主机配置或长期模型推荐。

先核对任务授权与写入范围，再用 `scripts/fleet_state.py status` 读取工作区身份和状态锁。工作区或父目录同时包含 `install.py` 与 `template/user-guard/plugin.json` 时，识别为 `workspace_mode: upstream_config_repo`。已安装到独立目录的 `fleet_state.py` 在通用目录未指定 `--repo` 调用时返回 `not_upstream_config_repo`，退出码为 `2`；直接调用上游仓内脚本时还会沿脚本路径识别上游仓库。限定文件的实现包只完成包内工作，不调用本编排器或写入全局状态。

## 维护模式与单一配置来源

| 模式 | 要完成的工作 | 边界 |
| --- | --- | --- |
| `full`，全面更新 | 收集官方更新和本机记录，核对升级、模型能力、本机可用性与额度类型，评估角色分工及运行参数。 | 提交具体方案，由用户决定后才实施升级或同步。 |
| `hygiene`，全量优化 | 基于已审定配置修正代码、钩子及路由问题，整篇重写精炼文档，完成事实核对、脱敏和回归。 | 不自动升级外部工具，不自行改变长期推荐或运行阈值。 |
| `audit`，只读巡检 | 核对配置漂移、供应商卡片、配额冷却和二进制接口（ABI）风险。 | 不安装、不升级、不刷新缓存、不改变状态。 |

[fleet_profile.json](../cae-harness/fleet_profile.json) 是角色与参数的唯一配置来源，集中维护 `orchestrator`、`math`、`code`、`review`、`fast` 五个槽位、独立的 `native_agent_profiles` 以及 `runtime_tunables`（含压缩恢复、长会话交接、`hook_safety` 钩子内存与墙钟自毁上限、循环与轮询防护、持久化无头派工与软超时收口、大文件锚点采样上限、本地路由和确认门禁）。常驻文档不散布时期性模型名、静态日期和运行阈值。测试可用 `CAE_FLEET_PROFILE` 指向隔离配置。

## 第一道门：联网评估与启停决策

`full` 同时回答能力是否适合任务、本机目前是否可用、本机配置何种额度三个问题，并按四个独立维度逐项盘点与提审：

1. **四端客户端与配套工具**：分别核查 Antigravity、Codex、Claude Code、DeepSeek Harness 与 CC Switch 的本地 CLI 及原生桌面端版本、上游更新及协议能力（DSH 核对 `hook_protocol`、`hard_hooks_supported` 与 `meta_update_trigger`，CC Switch 核对卡片、路由与旧提示词）。
2. **Windows 与 WSL2 双端**：依据 `install.py --audit-upgrade` 的 `cross_side_status` 与 `wsl_probe`，在检测到对侧客户端或配置时，分别对两侧执行版本核查、只读体检、预览与同步。
3. **各客户端内生代理维度**：在 `native_agent_profiles` 中独立评估主会话默认模型与挡位、内生子代理机制、只读代理定义与并发上限。
4. **跨端协同维度（`cae-harness`）**：独立评估五个角色槽位、`subscription_matrix`、`runtime_tunables`、持久化可续接无头会话及 `--isolation`（`full`、`no_skills`、`hooks_only`、`clean_room`）在各端的实际支持边界。

评估结果交用户在审批节点决定启停，写入 `subscription_matrix.agents` 和 `subscription_matrix.models`（`enabled: false` 全局跳过），并按两条正交能力轴拆解角色归属：① **无确定性验证器场景下的审慎拒答校准**（决定未闭环理论总控、论文定稿与正面独立立论角色）；② **有硬验证器约束下的代理闭环执行与挑刺吞吐**（决定受 `--write-scope` 与 `--verify-cmd` 约束的实现流、并行编码流及对抗反例挑刺流）。联网刷新时间写入 `capability_ledger.last_online_refresh_at`，本机实测时间写入 `capability_ledger.local_bench_verified_at`。

## 第二道门：生成确认单前离线复查

`cae_harness.py plan` 在重负载确认前纯离线复查当前程序、凭据、本地路由与可读额度，过滤不可用和超限通道；未暴露的服务端余额保持未知。用户点名按 P0 第一优先级锁定并通过 `--user-prompt` 保存原话。外部无头调用及内部 `invoke_subagent`、`run_workflow`、`spawn_agent`、`Agent`、`swarm`（`swarm add` 按 `tasks` 长度或 `n` 计入票据容量）等多路或可写委派均通过 `plan` 将完整 14 项参数明细写入 `<ticket_id>_card.md` 并生成 `compact_summary_card`，对话仅呈现精简总览卡与明细链接。获批后以 `plan --approve-ticket` 登记，执行绑定 `--ticket`；`batch` 仅执行外部无头通道。

## 确认被打断与完工核验

等待确认期间若发生自动压缩，恢复钩子按 `hooks/_lib/adaptive_policy.py` 动态缩放预算、过滤异步启动噪音并用 `collapse_historical_cards` 折叠历史明细表，同时从磁盘未决票据独立加载精简 4 列总览表、`<ticket_id>_card.md` 明细链接和选项卡。进入 `drain` 或 `freeze` 强制交接后，`handoff_gate.py` 放行 `check-grounding --strict`，并在 `Stop` 核验【新对话提示词】内的精简总览表、`<ticket_id>_card.md` 链接、`plan --resume-ticket` 命令及待拍板占位符保护。新会话以 `plan --resume-ticket` 读取复核，恢复不等于批准。

重负载结束时，`reply_repeat_guard.py` 在 `Stop` 检查回复是否含有成败词（拦截将含失败或超时的账单写成“全部成功”）、局部补测与全量测试区分、Token 用量或无法统计说明及回执或日志词项：外部任务按完整词项匹配各流标识（单路 `task` 或 `gate` 省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代）及非空回执的实际文件名；原生任务覆盖完成数量、全部流标识或互不重复的角色名之一并说明日志或轨迹。主会话负责逐路核对具体状态、Token 明细和汇总算术。

## 四类必须经用户审批的节点

以下事项先提出具体方案，获批前不实施对应动作：
1. 生成或调整客户端、模型与推理强度推荐矩阵，以及 `runtime_tunables`。
2. 写入宿主机全局配置目录或切换主会话客户端（展示完整目标路径、保留方式、安装只读预览清单及数据库备份方案）。
3. ABI 强耦合升级（含编译型科学计算包的系统级 Python 跨次版本、CUDA 跨大版本和 WSL 跨 LTS 发行版升级）。
4. 执行 `git push`。

## 探测、实现与验证

常用只读探测入口如下：

```powershell
python -B -X utf8 install.py --probe-apps
python -B -X utf8 install.py --audit-upgrade
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --caller auto
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py assess --caller auto
```

重要文档按需做 `clean_room` 盲读、主会话抛开旧措辞整篇精炼重写（保持规则与技能文字紧凑，不堆砌冗词发散模型注意力）、源码事实与公开脱敏校核。文件采用 UTF-8 无 BOM 和 LF；两份 `AGENTS.md` 各自严格小于 `16,000 B`。公开文档不得写入真实用户名、内网 IP、具体硬件型号、凭据、账号精确账单指纹或会话唯一标识。验收严禁放宽容差、守恒残差和收敛阶判据，严禁删改断言，严禁把局部补测冒充全量回归通过：

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 -m unittest tests.test_e2e_integration
python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v
```

本机已装 `agy` 时可配合 `agy plugin validate --strict template/user-guard` 校验清单。

## 提案、同步与重启复核

触及审批节点时用 `fleet_state.py propose` 登记方案，全局同步先运行 `install.py --dry-run --yes` 展示完整路径待用户决定；获批后执行 `approve` 及同步，并用 `install.py --audit-upgrade` 核查 `summary.all_aligned`。主会话客户端升级或切换需重启时，以 `record-pass1`、`verify-pass2` 及 `clear --verified --readback-done --session-id "<重启后会话标识>"`（无活跃重启认领锁时可省略 `--session-id`）通过事实锚定硬门禁后归位：

```powershell
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" status
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" propose --session-id "<当前会话标识>" --summary "<待审具体方案>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" approve --session-id "<当前会话标识>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" record-pass1 --session-id "<当前会话标识>" --target-master "<目标客户端>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" verify-pass2 --session-id "<重启后会话标识>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" clear --verified --readback-done --session-id "<重启后会话标识>"
```

最终汇报 Git 状态与实际修改范围，分别说明配置或文档已完成、离线检查已通过、本机实际环境实测已通过；仅列当前目标环境和核心验证链路的未验证项。
