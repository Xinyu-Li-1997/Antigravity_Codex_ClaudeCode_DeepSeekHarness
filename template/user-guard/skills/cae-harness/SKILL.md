---
name: cae-harness
description: >
  按动态配置组织本机对等客户端的有界推导、编码验证、只读审查和文档润色，
  提供持久化可续接无头会话、双层超时抢救、全通道派工确认及事实锚定。
---

# cae-harness 使用规程

执行入口 `scripts/cae_harness.py` 仅依赖 Python 3.11+ 标准库，调度本机已就绪的 Codex、Antigravity（`agy`）、Claude Code（`claude` 或 `cc`）与 DeepSeek Harness（`dsh`）。外部后台任务在独立进程与隔离上下文中运行，主会话保留用户选定的模型与推理强度。`check-grounding` 只做确定性词法检查，不调用模型。

## 先确定任务，再选择执行入口

遇到实质性数理推导、多文件编码验证或关键代码与文档审查，应先核实本机可用端，多端就绪时优先跨客户端协作。同端 `readonly-*` 代理仅用于原文摘录、多文件读取和行号初筛，不替代跨客户端独立审查。交接清单或审稿意见涉及新增算例、加密网格或扩大参数范围时，须先与现行测试矩阵及已取消方向做集合差分，新增项单列获批后再执行。

用户点名与角色槽位并列。用户以精确标识符或口语指定客户端、模型简称、推理挡位或省额度意图时，按 P0 第一优先级锁定对应维度，通过 `--user-prompt` 传入原话，角色推荐不得覆盖点名。选路支持 `role:orchestrator`、`role:math`、`role:code`、`role:review`、`role:fast` 及 `<client>:@<role>`、`<client>:default`、`<client>:<model>`。点名对象不可用、轻量卡片承担非 `fast` 角色、WSL 纯 Linux 可写路径走 Windows 互操作、Windows `codex.exe` 只读关闭内联（`--no-inline-readonly`）或所需本地路由未存活时，明确报错，不静默降级。

`--kind` 指定工作性质，`--engine` 或 `--worker` 指定角色与选路：

| 工作性质 | 命令入口 | 角色与限制 |
| --- | --- | --- |
| 技术方向与总体推演 | `task --kind direction` | 用 `--engine role:orchestrator`，只读给出路线与依据。 |
| 数理推导 | `task --kind math` | 用 `--engine role:math`，核对定义、公式、离散格式及成立条件。 |
| 代码实现 | `task --kind code` | 用 `--engine role:code`，必须声明 `--write-scope`。 |
| 实现与强制验收 | `gate` | 用 `--worker role:code`，提供 `--write-scope` 和非空 `--verify-cmd`。 |
| 独立审查 | `task --kind review` 或 `audit` | 前者用 `--engine role:review`；后者用 `--left`、`--right` 指定双源，`--single` 为单源。 |
| 文字润色 | `task --kind polish` | 可用 `--engine role:review`；只读返回改写稿和 `grounding_audit`，不得改源码。 |
| 按回执增量追问 | `followup --receipt <文件>` | 续接原无头会话 `provider_session_id` 追问或补查，继承原读写与快照边界。 |
| 省额度快检 | 按实际工作选择 `--kind` | 用 `--engine role:fast`；不存在 `--kind fast`。 |

五个槽位的候选、声明窗口与推荐挡位读取 [fleet_profile.json](fleet_profile.json)。后台常规任务默认取所选模型支持的单模型非委派最高强度，跳过自动委派的 `ultra`。`polish` 按配置篇幅阈值选短篇 `medium` 或长篇 `high`；`role:fast` 采用低消耗配置；显式非 `fast` 角色降为 `low` 时仍保留主力模型。

日常协作按是否存在确定性验证条件（编译、单元测试或给定待审文本）分流编排：
1. **常规编码、审查与异构验收**：代码实现与门禁默认走 `role:code`（内部 `native_swarm` 用 `swarm_worker_model_family`），常规审查与正向推导走跨端或审慎槽位（`role:review`、`role:math`）。主会话选用高吞吐编码系列统筹时，后台审查及配置了 `swarm_verifier_model_family` 的 `native_swarm` 验收流自动切至审慎低幻觉系列。
2. **早期方案探索与规范收口分离**：前期探索可并发派发高吞吐推理流核查资源边界；正式写入项目规范前，由主会话核对历史归档与物理前提后收口。
3. **未闭环数理推导的正反双流协同**：缺乏现成单元测试时，优先以 `--spec-file` 编排“审慎模型正向推导 + 高算力模型对照初稿排查反例与奇点边界”的双流计划，再由主会话按 R6 复核。单端受限时，双盲 `audit` 自动配对同客户端内的 `deep_families` 与 `code_families`。

## 持久化可续接会话、精确输入信封与双层超时抢救

外部无头调用自动补齐声明最大窗口、防分页非交互环境变量、前后快照与脱敏；会话持久化按 `persist_sessions_by_default` 默认开启，仅显式传 `--ephemeral` 时关闭：
- **多文件输入与负向边界**：`task`、`plan`、`batch` 可重复传 `--files <相对路径>`（流字段 `files: [...]`）精确指定目标目录内的多份必读文件，避免只传单文件或整目录拷入；`--negative-scope-file`（流字段 `negative_scope_files: [...]`）与 `env_contract` 显式注入负向排除边界与宿主机环境约定。
- **双层超时抢救**：底层 `run_process` 实时逐行读取管道，POSIX 终止进程组时先发 `SIGTERM` 等待短暂宽限再升级 `SIGKILL`。持久化开启且 `timeout >= 120 s` 时，若主运行到达 `soft_timeout_ratio` 软时限仍未产出结果文件但已建立 `provider_session_id`，自动用剩余预算并以低推理挡位（`agy` 带挡位后缀模型自动切换目录内同家族低挡 slug 并记录 `wrapup_model`，且外层进程超时为 `agy --print-timeout` 保留优雅宽限）发起一次只读续接收口（`soft_timeout_wrapup`）封装结构化结果；若仍触发硬超时，回执如实保留总 `timeout_sec`、主回合 `primary_timeout_sec`、已完成轮次的部分 Token（`cli_partial_on_timeout`）与 `timeout_salvage`（含 `provider_session_id`、`resumable`、`actual_files_read`、`changed_files_on_disk` 及优先保留正式回复的最近消息片段）。若 `agy` 收口超时且零模型进展，回执记 `wrapup_stalled: true` 并将 `timeout_salvage.resumable` 置为 `false`。只读执行同时写入 `inline_readonly_summary` 记录已内联与超限未内联文件统计。
- **按回执续接追问（`followup`）**：回执保存 `provider_session_id`，临时隔离会话归档至 `<plan_dir>/headless_sessions/`。主会话复核存疑或从超时断点续查时，运行 `followup --receipt <回执.json> [--stream <流号>] --task "<追问内容>"` 复用原上下文；若原回执已标记 `timeout_salvage.resumable: false`，`followup` 立即以 `session_not_resumable` 阻断并提示按分片新开会话。
- **读取轨迹与引用行号核验（`citation_audit`）**：只读回执自动提取 `actual_files_read`，核对正文 `path#Lstart-Lend` 或 `path:start-end` 引用的文件存在性、总行数上限及是否真实打开，供主会话按 R6 复核。

## 两道可用性检查与全通道确认单

第一道门由独立 `fleet-evolve` 联网评估并经用户决定 `subscription_matrix` 启停，`enabled: false` 全局跳过。第二道门由 `plan` 纯离线复查本机程序、凭据、所需本地路由健康及可读额度状态（滚动窗口百分比与重置时间、卡片套餐或余额类型、日月限额及冷却记录），未暴露的服务端余额保持未知。日常派工仅读取 `capability_ledger.last_online_refresh_at` 与 `capability_ledger.local_bench_verified_at`，不联网更新。遇到 `quota_exhausted` 立即停止重试并记录冷却；仅显式开启 `--fallback-on-quota` 且工作区无改动时可按配置回退。

轻任务按 `runtime_tunables.dispatch_confirmation` 静默执行（单路只读 `task` 显式传入 `--files` 时仅统计所声明文件，上限由 `heavy_max_light_explicit_files` 或默认 4 个文件控制，不误扫整个目标目录）。外部无头调用及内部 `invoke_subagent`、`run_workflow`、`spawn_agent`、`Agent`、`swarm` 等多路或可写委派均须先经 `plan` 确认；`status_guard.py` 对 `swarm` 的 `add` 按 `tasks` 长度或 `n` 计入通道容量。通道含 `cae_harness_headless`、`native_subagent`、`native_workflow`、`native_swarm`；原生通道默认 `inherit`，优先解析代理定义中的显式 `model` 并挂载 `native_agent_profiles`。

`plan` 依次写入 `<ticket_id>.json` 与 `<ticket_id>_card.md`（含 4 列总览表与 14 项明细），标准输出默认置顶 `chat_brief` 与 `compact_summary_card`；需全量 JSON 时加 `--full-json`，本地交互勿传 `--redact` 以保留可点击链接。对显式传入 `--files` 或单文件目标的外部无头只读流，`plan` 额外核验声明窗口与安全预算：估算超出目标模型声明窗口时默认报 `over_context_window` 并返回 `suggested_shards`（自动选路 `role:*` 优先换更大窗口引擎，确需抽查可显式加 `--allow-over-budget` 或流字段 `allow_over_budget: true` 放行），超出安全输入预算或内联上限时在明细与 `chat_brief` 预警。日常对话默认仅展示 3 句话极简确认条 `chat_brief` 及 `<ticket_id>_card.md` 链接，禁止在日常对话粘贴 Markdown 表格或展开 14 项长表，未发生压缩且票据未变时后台被动唤醒严禁重复贴票：

| 序号 | 参数项 | 应显示的内容 |
| --- | --- | --- |
| 1 | 流号 | 每路唯一标识。 |
| 2 | 子任务与范围 | 工作内容与目标路径。 |
| 3 | 通道 | 外部无头或具体原生通道。 |
| 4 | 客户端及子代理类型 | 实际客户端和代理类型。 |
| 5 | 模型名字 | 实际模型及 `inherit` 解析结果。 |
| 6 | 挡位 | 本路推理强度。 |
| 7 | 上下文窗口 | 已解析窗口或未声明状态。 |
| 8 | 是否无头 | 是否启动无头进程。 |
| 9 | 是否净室与隔离级别 | 是否使用 `clean_room` 空上下文隔离；显式指定任一非净室隔离模式时追加标注 `（隔离：<级别>）`。 |
| 10 | 访问模式、写入范围与验收命令 | 只读或可写、写入白名单及验证命令。 |
| 11 | 内联与超时 | 只读内容是否内联及本路超时。 |
| 12 | 当前是否可用 | 本机判断与不可用原因。 |
| 13 | 本机配置额度类型与当前状态 | 额度来源、可读状态及冷却情况。 |
| 14 | 回执或子代理日志位置 | 结构化回执或原生轨迹位置。 |

## 批准、波次执行与跨中断恢复

- **批准与微调**：用户批准后运行 `plan --approve-ticket` 登记，同会话更早且通道重叠的待决票据自动标为 `SUPERSEDED`，取消用 `plan --cancel-ticket`。`--override-json` 按 `stream_id` 微调：外部无头通道仅允许 `engine`、`tier`、`timeout`；内部原生通道仅允许 `tier`、`timeout`，禁止微调 `engine`。
- **持票门禁与波次执行（`wave`）**：执行须绑定 `--ticket`，由 `status_guard.py` 核验票据并拦截裸 CLI 旁路。`batch` 仅执行 `cae_harness_headless` 通道，混合票据的原生流记入 `skipped_native_streams`。各流按整数 `wave` 升序分波次运行：前波次同目标可写流失败时，后续波次默认全部中止为 `upstream_wave_failed`（只读流确需独立执行时须显式声明 `run_on_upstream_failure: true`）；同波次下，同根目录声明互不重叠 `write_scope` 的任务可安全并发。
- **压缩恢复与交接**：等待确认期间发生自动压缩时，钩子按 `hooks/_lib/adaptive_policy.py` 缩放预算、过滤异步启动噪音并用 `collapse_historical_cards` 折叠历史明细，同时从 `<ticket_id>.json` 独立加载精简 4 列总览卡、`<ticket_id>_card.md` 链接和选项卡供模型恢复核对，向用户仅补展 1 次 `chat_brief` 与明细链接。进入 `drain` 或 `freeze` 强制交接时，`handoff_gate.py` 放行 `check-grounding --strict`，并在 `Stop` 核验【新对话提示词】内的精简总览表、`<ticket_id>_card.md` 链接、`plan --resume-ticket` 命令及待拍板占位符保护。新会话用 `plan --resume-ticket` 复核旧票据，跨会话正式执行须按当前会话标识重建票据。
- **完工回执核验**：`reply_repeat_guard.py` 在完工 `Stop` 检查成败词（禁止将含失败或超时的账单写成“全部成功”）、局部补测与全量回归区分，以及 Token 用量或无法统计说明。外部任务须覆盖各流标识和账单中的实际回执文件名；单路 `task` 或 `gate` 省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代。原生任务须覆盖完成数量、各流标识或各角色名中的一项，并提供日志或轨迹说明。主会话仍须自行逐路核对状态、Token 明细与汇总算术。

## 命令示例

在课题工作区根目录运行，将 `HARNESS` 指向本机已安装技能脚本；上游维护可设为 `template/user-guard/skills/cae-harness/scripts/cae_harness.py`。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 改用 `python3 -B -X utf8`：

```powershell
# Windows PowerShell（四选一）：
$HARNESS = "$HOME\.codex\skills\cae-harness\scripts\cae_harness.py"                        # Codex
# $HARNESS = "$HOME\.claude\skills\cae-harness\scripts\cae_harness.py"                     # Claude Code
# $HARNESS = "$HOME\.dsh\skills\cae-harness\scripts\cae_harness.py"                        # DSH
# $HARNESS = "$HOME\.gemini\config\plugins\user-guard\skills\cae-harness\scripts\cae_harness.py" # Antigravity
```

```bash
# WSL2 与 Linux Bash（四选一）：
HARNESS="$HOME/.codex/skills/cae-harness/scripts/cae_harness.py"                           # Codex
# HARNESS="$HOME/.claude/skills/cae-harness/scripts/cae_harness.py"                        # Claude Code
# HARNESS="$HOME/.dsh/skills/cae-harness/scripts/cae_harness.py"                           # DSH
# HARNESS="$HOME/.gemini/config/plugins/user-guard/skills/cae-harness/scripts/cae_harness.py"    # Antigravity
```

```powershell
python -B -X utf8 "$HARNESS" probe --caller auto --redact
python -B -X utf8 "$HARNESS" assess --caller auto --redact
python -B -X utf8 "$HARNESS" check-grounding --target "<项目根目录>" --strict
```

多波次与多文件输入的 `--spec-file` 示例；两路均填 `role:review` 不自动保证分属不同客户端，双源审查应显式分端或用 `audit`：

```json
[
  {"stream_id":"S1","wave":1,"channel":"cae_harness_headless","kind":"review","engine":"role:review","target":"<项目根目录>","files":["docs/spec.md","data/index.json"],"negative_scope_files":["docs/cancelled.md"],"subtask":"核对双文件数据与规范一致性"},
  {"stream_id":"S2","wave":2,"channel":"cae_harness_headless","kind":"review","engine":"dsh:@review","isolation":"clean_room","target":"<说明文档路径>","subtask":"净室盲读检查可读性"}
]
```

生成确认单、展示 `chat_brief` 极简确认条与明细链接获批后执行批处理，必要时按回执追问：

```powershell
python -B -X utf8 "$HARNESS" plan --spec-file "<工作流文件路径>" --tier high --session-id "<当前会话标识>" --workflow-bg "核心文档已重写，需要核对实现" --workflow-goal "分波次检查指定文件" --workflow-impact "只读核查并生成回执，不修改源文件"
python -B -X utf8 "$HARNESS" plan --approve-ticket "<票据标识>" --session-id "<当前会话标识>"
python -B -X utf8 "$HARNESS" batch --ticket "<票据标识>" --session-id "<当前会话标识>" --output "<授权回执路径>" --redact
python -B -X utf8 "$HARNESS" followup --receipt "<授权回执路径>" --stream S1 --task "补充核对第 42 行边界条件符号" --output "<追问回执路径>" --redact
```

原生只读代理确认、旧票据复核与单流强制验收 `gate`；持票执行时，除核验执行内容、目标、模型、挡位、输入信封与超时外，还会校验 `--write-scope`（须为获批范围子集）与 `--verify-cmd` 一致性：

```powershell
python -B -X utf8 "$HARNESS" plan --channel native_subagent --caller auto --agent-type readonly-auditor --model inherit --kind review --target "<目标文件路径>" --session-id "<当前会话标识>" --save-ticket --workflow-bg "需要原文行号复核" --workflow-goal "读取目标并返回原文行号" --workflow-impact "只读，不运行构建或测试"
python -B -X utf8 "$HARNESS" plan --resume-ticket "<票据标识>"
python -B -X utf8 "$HARNESS" gate --worker role:code --target "<项目根目录>" --write-scope "<授权相对路径>" --verify-cmd "<实际验收命令>" --task-file "<任务文件路径>" --ticket "<票据标识>" --session-id "<当前会话标识>"
```

## 只读隔离与结果验收

无头执行支持独立的 `--isolation` 参数（`full`、`no_skills`、`hooks_only`、`clean_room`），可与后端、模型、挡位、持久化选项及读写模式正交组合：

| 隔离级别 | 保留与剥离边界 | 支持后端与读写限制 |
| --- | --- | --- |
| `full` | 保留全局规则、安全钩子（DSH 当前为 `soft_rules_only`）与全局技能 | 四端均支持，只读与可写均适用（`codex`、`claude`、`dsh` 默认值） |
| `no_skills` | 保留全局规则与安全钩子，剥离全局技能与斜杠命令 | 四端均支持，只读与可写均适用（`agy` 默认值） |
| `hooks_only` | 保留底层凭据与安全钩子，剥离全局规则与技能 | 仅 `codex`、`claude` 支持；`agy` 与 `dsh` 显式报错 |
| `clean_room` | 等同 `--clean-room`：仅保留凭据，剥离全局规则、钩子、技能及项目自动说明 | 仅 `codex`、`claude`、`dsh` 支持，且仅限只读任务 |

审查任务禁止修改文件或运行构建测试，前后以文件哈希快照核验零改动。`check-grounding --strict` 从源码和配置构建词法锚点，自动采样超大文件并跳过工具链与归档目录。主会话采纳后台建议前须亲自打开原文件核对精确行号；验收严禁放宽 `rtol`、`atol`、守恒与收敛阶判据或删改断言，严禁将局部补测冒充全套测试通过。
