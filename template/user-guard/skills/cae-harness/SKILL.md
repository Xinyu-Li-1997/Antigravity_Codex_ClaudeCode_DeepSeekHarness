---
name: cae-harness
description: >
  按动态配置组织本机对等客户端的有界推导、编码验证、只读审查和文档润色，
  提供离线可用性与额度检查、全通道派工确认、票据交接、完成回执及事实锚定。
---

# cae-harness 使用规程

本技能的执行入口是 `scripts/cae_harness.py`，依赖 Python 3.11+ 标准库。它使用本机已准备好的 Codex、Antigravity（`agy`）、Claude Code（`claude` 或 `cc`）和 DeepSeek Harness（`dsh`）。外部后台任务在新进程、独立上下文中运行，主会话保留用户选定的模型与推理强度。`check-grounding` 只做确定性检查，不调用模型。

## 先确定任务，再选择执行入口

实质性数理推导、多文件编码验证、关键代码与文档审查应主动核实本机可用端，再组织协作。多个对等端就绪时，推导、实现验证和关键独立审查优先跨客户端执行。同端 `readonly-*` 代理用于原文摘录、多文件读取和行号初筛，不替代跨客户端独立审查。用户限定为本会话执行或禁止再次编排时，服从当前任务边界。

用户点名与角色槽位是并列入口。用户用精确标识符或日常口语指定客户端、模型简称、推理挡位或省额度意图时，按 P0 第一优先级锁定已指定维度，通过 `--user-prompt` 携带原话；任务文本或自动推荐不得覆盖点名。未指定部分由配置补齐。角色可写为 `role:orchestrator`、`role:math`、`role:code`、`role:review`、`role:fast`；也支持 `<client>:@<role>`、`<client>:default`、`<client>:<model>`。点名不可用时说明原因，不自行换端或降级。

`--kind` 表示工作性质，`--engine` 或 `--worker` 表示角色与选路。应按下表明确组合，不声称脚本仅凭 `--kind` 就固定绑定某个槽位。

| 工作性质 | 命令入口 | 角色与限制 |
| --- | --- | --- |
| 技术方向与总体推演 | `task --kind direction` | 用 `--engine role:orchestrator`，只读给出路线与依据。 |
| 数理推导 | `task --kind math` | 用 `--engine role:math`，核对定义、公式、离散格式及成立条件。 |
| 代码实现 | `task --kind code` | 用 `--engine role:code`，必须声明 `--write-scope`。 |
| 实现与强制验收 | `gate` | 用 `--worker role:code`，提供 `--write-scope` 和非空 `--verify-cmd`。 |
| 独立审查 | `task --kind review` 或 `audit` | 前者用 `--engine role:review`；后者用 `--left`、`--right` 指定双源，`--single` 为单源。 |
| 文字润色 | `task --kind polish` | 可用 `--engine role:review`；只读返回改写稿和 `grounding_audit`，不得改源码。 |
| 省额度快检 | 按实际工作选择 `--kind` | 用 `--engine role:fast`；不存在 `--kind fast`。 |

五个槽位的候选、声明窗口与推荐挡位均读取 [fleet_profile.json](fleet_profile.json)。后台常规任务默认采用所选模型支持的单模型非委派最高强度，跳过自动委派的 `ultra`。`polish` 按配置篇幅阈值选短篇 `medium` 或长篇 `high`；`role:fast` 按其低消耗配置执行。显式非 `fast` 角色即使降到 `low`，仍保留该槽位主力模型。轻量卡片承担非 `fast` 角色或所需本地路由未存活时，必须明确报错。

外部调用由脚本补齐声明最大上下文窗口、无头非交互标志、只读小文件行号内联、前后快照与脱敏。`--inline-readonly` 默认开启，实际内联还受目标规模和配置限制。`--output` 保存结构化回执，文件默认脱敏；`--redact` 控制标准输出脱敏。原生通道的能力按客户端实际支持说明，不把外部无头隔离承诺套用于原生代理。

## 两道可用性检查

第一道门在独立的 `fleet-evolve` 流程中完成。维护者联网评估能力，同时展示本机当前能否使用及本机配置的额度类型，由用户决定哪些 agent 与 LLM 启用或停用，写入 `subscription_matrix.agents` 和 `subscription_matrix.models`。退订项设 `enabled: false`，全局选路跳过。一次任务批准不授权修改长期矩阵或 `runtime_tunables`。

第二道门由 `plan` 在生成重负载确认单前纯离线复查。检查本机程序、凭据配置、所需路由存活和可读取的额度状态，按实际配置读取滚动窗口百分比与重置时间、卡片套餐或余额类型、日月限额以及配额冷却，过滤不可用或超限通道。本地回环路由健康检查不等于查询外部服务余额。未暴露的信息保持未知，本地记录未显示超限也不能证明服务端尚有足额余额。

日常派工不联网更新性能。确认单展示 `capability_ledger.last_online_refresh_at` 和 `capability_ledger.local_bench_verified_at`，联网刷新仅由 `fleet-evolve` 负责。派工读取当前配置与本地记录，不把维护阶段的历史实测当成本轮服务端验证。

执行返回 `quota_exhausted` 后立即停止重试，按运行侧记录冷却。只有显式开启 `--fallback-on-quota` 且工作区尚无任何文件改动时，才能按配置回退；P0 点名仍须遵守，重负载计划发生变化时重新确认。`batch` 的各后端并发由 `runtime_tunables.headless_dispatch.batch_max_workers_per_backend` 控制，同后端触发额度中止后停止其排队流。

## 轻任务、重负载与确认单

轻任务按 `runtime_tunables.dispatch_confirmation` 的流数、文件数、估算 Token 和超时阈值静默执行。多路、可写或超出轻任务阈值的调用进入重负载流程，不能仅凭名称自行判轻。内部 `invoke_subagent`、`run_workflow`、`spawn_agent`、`Agent`、`swarm` 等多路或可写委派与外部无头调用均须纳入确认。

`plan` 可接收单流参数，也可通过 `--spec-file` 读取非空对象列表。流内字段覆盖相应公共默认值。通道有 `cae_harness_headless`、`native_subagent`、`native_workflow`、`native_swarm`。原生通道必须指定或识别 `--caller`，默认模型为 `inherit`；确认单按前端配置（含 Codex 代理配置与 Claude/Antigravity 代理前置元数据的显式 `model`）解析实际模型，并独立挂载 `fleet_profile.json` 中可选的 `native_agent_profiles` 元数据，无法解析时保留未解析状态。其中 `native_swarm` 若在 `native_agent_profiles` 中配置了 `swarm_worker_model_family` 与 `swarm_worker_kinds`，对应任务且未显式传入 `--model` 时，确认单自动推荐该系列模型供用户审定，获批后在 `swarm` 的 `add` 中显式传入 `model`；只读核查与其他原生通道保持 `inherit`。原生通道不属于无头调用，也不提供净室隔离。`status_guard.py` 对 `swarm` 的 `add` 调用按 `tasks` 列表长度或 `n` 计入通道并发容量，避免单次批量派生绕过票据流数上限。

重负载确认单在落盘文件 `<ticket_id>_card.md` 中完整记录工作流背景、要做什么、预计后果、4 列各路总览表与每路纵向展开的 14 项完整参数表（保存票据时先写 `<ticket_id>.json` 再写 `<ticket_id>_card.md`），同时生成含可点击明细链接的精简总览卡 `compact_summary_card` 与 `compact_handoff_prompt_block`。已落盘票据的 `plan` 标准输出默认采用精简视图（置顶 `compact_summary_card`，省略已落盘的 14 项长表与 `execution_profile`；仅需完整结构时加 `--full-json`）。本地交互生成确认单不要对 `plan` 传 `--redact`，以免将 `<ticket_id>_card.md` 的本地链接替换成占位符。对话展示默认仅呈现 `compact_summary_card`（背景、目标、预计后果、4 列总览表、性能更新时间、可点击 `<ticket_id>_card.md` 链接与三选项），严禁将多路 14 项长表全文刷屏对话污染主上下文；仅当未保存明细文件或用户明确要求展开时才输出完整 14 项表。不能省略未知字段，也不能以角色槽位代替实际模型。

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

参数表后附性能记录的联网刷新和本机实测更新时间，并给出批准当前计划、微调引擎、挡位或超时后批准、暂缓并交接三个选项。主会话结合证据说明推荐，用户明确选择前不执行重负载。`--session-id` 关联当前会话；轻计划如需保存，加 `--save-ticket`。`--plan-dir` 可指定票据目录，所有票据、回执、日志和临时制品均受当前授权边界约束。

## 批准、执行与跨中断恢复

用户批准后运行 `plan --approve-ticket` 登记；若同会话存在更早且通道重叠的 `PENDING` 或 `HANDED_OFF` 票据，批准时会自动将其标记为 `SUPERSEDED`，也可用 `plan --cancel-ticket` 手动取消陈旧待决票据。`--override-json` 按 `stream_id` 提供微调，外部无头通道只允许 `engine`、`tier`、`timeout`，内部原生通道流仅支持微调 `tier` 与 `timeout`（禁止微调 `engine`）；调整须符合用户选择，批准前脚本重新离线解析并核查可用性。执行通过 `--ticket` 绑定内容一致且有效的 `APPROVED` 票据，不能借用其他任务的票据。

`status_guard.py` 在工具执行前核验获批票据，拦截未经确认的重负载和裸 CLI 旁路。不要直接启动原始客户端命令绕过窗口、快照、额度或确认检查。`batch` 仅执行 `cae_harness_headless` 通道：纯原生票据会被拒绝，混合通道票据仅执行其中的无头流并在回执的 `skipped_native_streams` 中列出跳过项；原生流由对应客户端按获批内容在内部调用。在工作流中声明 `kind: gate` 时仍须提供范围与验收命令，不把批处理标签本身当作已完成验证。

等待用户确认期间发生自动压缩，钩子按 `hooks/_lib/adaptive_policy.py` 根据模型与推理强度动态调整恢复预算并用 `collapse_historical_cards` 折叠历史明细表，同时从磁盘未决票据独立加载背景、要做什么、预计后果、精简 4 列总览表、可点击 `<ticket_id>_card.md` 完整 14 项明细链接和选项卡（仅无落盘明细文件时回退展开 14 项长表）。恢复时分成两块：常规用户原话及子代理摘要；未决重负载工作流精简总览与明细链接。票据缺失时报告缺口，不凭印象补写，也不把压缩或超时当作批准。

进入 `drain` 或 `freeze` 强制交接时，`handoff_gate.py` 放行 `check-grounding --strict`，并在 `Stop` 硬门禁要求把工作流背景/目标/预计后果、精简 4 列总览表、`<ticket_id>_card.md` 明细链接、性能更新时间、选项卡、票据与 `plan --resume-ticket` 命令写入【新对话提示词】（无落盘明细文件时仍要求完整 14 项参数表）。Codex 与 Claude Code 默认是 `advisory` 提醒，强制行为以实际模式为准。

新会话先用 `plan --resume-ticket` 读取并重新核对完整确认内容。该接口保留原票据的 `session_id`，不会因传入新会话标识自动改绑；跨会话接力应据原流内容和当前会话标识重新生成确认票据，再按用户选择登记批准。内容变化、过期或校验失败时重新形成确认单，恢复本身不代表批准。

`reply_repeat_guard.py` 在完工 `Stop` 检查回复中是否含有成败词，以及 Token 用量或无法统计说明。外部任务还须覆盖各流标识和账单中的实际回执文件名；单路 `task` 或 `gate` 省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代。原生任务须覆盖完成数量、各流标识或各角色名中的一项，并提供日志或轨迹说明。流标识和外部回执文件名按完整词项匹配。漏报时同一回合单次阻断并附上磁盘账单。主会话仍须自行逐路核对具体状态、Token 明细与汇总算术，静态检查不核实这些内容的语义和计算结果。

## 命令示例

以下在业务课题工作区根目录运行（保持当前工作目录不变，避免切入全局技能目录导致 `probe` 工作区模式与相对路径失准），先将 `HARNESS` 指向本机已安装客户端对应的技能脚本路径（四选一；若在上游配置仓库维护模式下直接运行，可设为 `template/user-guard/skills/cae-harness/scripts/cae_harness.py`）。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 改用 `python3 -B -X utf8`。占位符须替换为实际值，输入文件须已获授权准备，所有输出均须落在本轮允许范围。示例参数仅作覆盖演示，角色、挡位、窗口、超时、并发和轻重阈值默认由 `fleet_profile.json` 驱动，不应抄成固定策略。

```powershell
# Windows PowerShell（按本机已安装的任意一端设置 HARNESS）：
$HARNESS = "$HOME\.codex\skills\cae-harness\scripts\cae_harness.py"                        # Codex
# $HARNESS = "$HOME\.claude\skills\cae-harness\scripts\cae_harness.py"                     # Claude Code
# $HARNESS = "$HOME\.dsh\skills\cae-harness\scripts\cae_harness.py"                        # DSH
# $HARNESS = "$HOME\.gemini\config\plugins\user-guard\skills\cae-harness\scripts\cae_harness.py" # Antigravity
```

```bash
# WSL2 与 Linux Bash（按本机已安装的任意一端设置 HARNESS）：
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

多路只读检查的 `--spec-file` 可使用以下对象列表。每路目标互不依赖，子任务边界清楚；两个 `role:review` 并不保证来自不同客户端，需要双源独立审查时另用 `audit` 明确两路来源。

```json
[
  {"stream_id":"S1","channel":"cae_harness_headless","kind":"review","engine":"role:review","target":"<第一份文件路径>","subtask":"核对接口文档与实现是否一致"},
  {"stream_id":"S2","channel":"cae_harness_headless","kind":"review","engine":"role:review","target":"<第二份文件路径>","subtask":"核对命令参数与配置依据"}
]
```

先生成确认单。`--tier high` 只演示显式覆盖，实际挡位应与用户意图及批准计划一致：

```powershell
python -B -X utf8 "$HARNESS" plan --spec-file "<工作流文件路径>" --tier high --session-id "<当前会话标识>" --workflow-bg "核心文档已重写，需要核对实现" --workflow-goal "分路检查指定文件" --workflow-impact "只读核查并生成回执，不修改源文件"
```

在对话中呈现精简总览卡 `compact_summary_card`、可点击 `<ticket_id>_card.md` 链接及三选项（勿展开多路 14 项长表），待用户明确批准后登记，再执行外部批处理：

```powershell
python -B -X utf8 "$HARNESS" plan --approve-ticket "<票据标识>" --session-id "<当前会话标识>"
python -B -X utf8 "$HARNESS" batch --ticket "<票据标识>" --session-id "<当前会话标识>" --output "<授权回执路径>" --redact
```

如用户有点名要求，在形成计划和执行任务时通过 `--user-prompt "<用户点名原话>"` 保留同一约束。不要在执行时增加会改变已批准模型或挡位的参数。

原生只读代理可先用以下入口形成计划，`plan` 不会启动代理。多路时通过同一 `--spec-file` 列全所有流，并按当前客户端自动识别或显式设置 `--caller`：

```powershell
python -B -X utf8 "$HARNESS" plan --channel native_subagent --caller auto --agent-type readonly-auditor --model inherit --kind review --target "<目标文件路径>" --session-id "<当前会话标识>" --save-ticket --workflow-bg "需要原文行号复核" --workflow-goal "读取目标并返回原文行号" --workflow-impact "只读，不运行构建或测试"
```

跨会话先读取旧票据；此命令不转移会话归属：

```powershell
python -B -X utf8 "$HARNESS" plan --resume-ticket "<票据标识>"
```

单流可写任务需要强制验收时使用 `gate`；持票据执行时，除核验执行内容、目标、模型、挡位与超时外，还会校验 `--write-scope`（须为获批范围子集）与 `--verify-cmd` 一致性，`--task-file` 中的任务也须与票据子任务一致：

```powershell
python -B -X utf8 "$HARNESS" gate --worker role:code --target "<项目根目录>" --write-scope "<授权相对路径>" --verify-cmd "<实际验收命令>" --task-file "<任务文件路径>" --ticket "<票据标识>" --session-id "<当前会话标识>"
```

## 只读隔离与结果验收

无头执行支持独立的 `--isolation` 参数（`full`、`no_skills`、`hooks_only`、`clean_room`），可与客户端、模型、挡位及读写模式正交组合；流规格 JSON 也可逐流声明 `isolation`：

| 隔离级别 | 保留与剥离边界 | 支持后端与读写限制 |
| --- | --- | --- |
| `full` | 保留全局规则、安全钩子（DSH 当前为 `soft_rules_only`，无硬安全钩子）与全局技能（`agy` 显式放开斜杠命令与技能） | 四端均支持，只读与可写均适用（`codex`、`claude`、`dsh` 默认值） |
| `no_skills` | 保留全局规则与安全钩子（DSH 为软规则），剥离全局技能与斜杠命令 | 四端均支持，只读与可写均适用（`agy` 未显式指定时默认值） |
| `hooks_only` | 保留底层凭据与安全钩子，剥离全局规则与技能 | 仅 `codex`、`claude` 支持，只读与可写均适用；`agy` 与当前 `soft_rules_only` 状态的 `dsh` 显式报错 |
| `clean_room` | 等同 `--clean-room`：仅保留凭据，剥离全局规则、钩子、技能及项目自动说明（`claude` 同时启用 `--bare` 与 `--disable-slash-commands`） | 仅 `codex`、`claude`、`dsh` 支持，且仅限只读任务 |

净室与隔离所需的临时配置及暂存目录同样服从本轮授权，不因默认使用临时目录就获得额外写入许可。显式指定的非净室隔离模式均会在确认单第 9 项标注，并在持票执行时校验一致性。

审查任务禁止修改文件或运行构建测试，前后以文件哈希快照核验零改动。可写任务通过 `--write-scope` 限定白名单；发生 `scope_violation` 时保留现场并报告，不扩大范围掩盖越界。无头子进程设置 `CAE_HARNESS_DEPTH=1` 防止嵌套派工，不得再次调用编排器；只读探测及 `check-grounding` 按脚本规定放行。

重要文档依次做可读性检查、主会话整篇重写、源码事实与公开脱敏校核。`check-grounding --strict` 从源码和配置构建词法锚点，检查参数、文件标识符、大写缩写、定量数字和领域举例；`--base-ref HEAD` 可检查相对基线的新表述。词法检查不能替代数学证明、实际运行或人工复核。

主会话采纳后台建议前亲自打开原文件，核对精确行号。公开材料不得含真实用户名、内网 IP、具体硬件型号、密钥或精确账单指纹。验收严禁放宽 `rtol`、`atol`、守恒与收敛阶判据，或删改测试断言。最终分别说明源码或配置完成、离线检查通过、本机实际环境或算例实测通过，未验证项只列本轮核心验证链路的缺口。
