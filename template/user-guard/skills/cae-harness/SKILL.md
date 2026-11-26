---
name: cae-harness
description: >
  按动态配置组织本机对等客户端的有界推导、编码验证、只读审查和文档润色，
  提供持久化可续接无头会话、双层超时抢救、全通道派工确认及事实锚定。
---

# cae-harness 使用规程

执行入口 `scripts/cae_harness.py` 仅依赖 Python 3.11+ 标准库，调度本机已就绪的 Codex、Antigravity（`agy`）、Claude Code（`claude` 或 `cc`）与 DeepSeek Harness（`dsh`）。外部后台任务在独立进程与隔离上下文中运行，主会话保留用户选定的模型与推理强度。`check-grounding` 仅做确定性词法检查，不调用模型。

## 先定边界，再选入口

零碎小修、修审查小项与高频交互对齐直接由主会话完成，避免派工沟通与验收摩擦大于改动本身；接口清晰、成块独立的数理推导、求解器模块实现或关键代码与文档审查，先核实本机可用端并优先跨客户端协作。同端 `readonly-*` 代理仅用于原文摘录、多文件读取和行号初筛，不替代跨端独立审查。涉及新增算例、加密网格或扩大参数范围时，先与现行测试矩阵及已取消方向做集合差分，新增项单列获批后再执行。

用户点名与角色槽位并列。用户以精确标识符或口语指定客户端、模型、推理挡位或省额度意图时，按 P0 最高优先级锁定并通过 `--user-prompt` 传入原话，角色推荐不得覆盖点名。选路支持 `role:orchestrator`、`role:math`、`role:code`、`role:review`、`role:fast` 及 `<client>:@<role>`、`<client>:default`、`<client>:<model>`。点名不可用、轻量卡片冒充非 `fast` 角色、WSL 纯 Linux 可写路径走 Windows 互操作、Windows `codex.exe` 只读关闭内联或所需本地路由失活时明确报错，不静默降级。

| 工作性质 | 命令入口 | 角色与限制 |
| --- | --- | --- |
| 技术方向与总体推演 | `task --kind direction` | 用 `--engine role:orchestrator`，只读给出路线与依据。 |
| 数理推导 | `task --kind math` | 用 `--engine role:math`，核对定义、公式、离散格式及成立条件。 |
| 代码实现 | `task --kind code` | 用 `--engine role:code`，必须声明 `--write-scope`。 |
| 实现与强制验收 | `gate` | 用 `--worker role:code`，提供 `--write-scope` 和非空 `--verify-cmd`。 |
| 独立审查 | `task --kind review` 或 `audit` | 前者用 `--engine role:review`；后者用 `--left`、`--right` 指定双源，`--single` 为单源。 |
| 文字润色 | `task --kind polish` | 可用 `--engine role:review`；只读返回改写稿和 `grounding_audit`，不得改源码。 |
| 按回执增量追问 | `followup --receipt <文件>` | 续接原无头会话 `provider_session_id` 追问或补查，继承原读写与快照边界。 |
| 读前尺寸评估与分片 | `read-plan --target <目录>` | 纯离线零模型调用：按安全预算与内联字节双口径评估大目录/大文件，自动切分超大文件行段并输出保守波次清单与可选 `--emit-spec`。 |
| 跨端分片汇总核对 | `cross-check --receipt <回执>` | 纯离线零模型调用：核对计划分母 × 客户端覆盖矩阵、共同盲区、多端同位并列/分歧、跨片边与 R6 待回源原文件行号清单。 |
| 确定性机械对账 | `reconcile --target <目录>` | 纯离线零模型调用：核对已更新与未更新代码/文档、强显式依赖、义务结算及可选 `--cross-check` 并轨，写出可点击 Markdown 账表。 |
| 省额度快检 | 按实际工作选择 `--kind` | 用 `--engine role:fast`；不存在 `--kind fast`。 |

五个槽位的候选、声明窗口与推荐挡位读取 [fleet_profile.json](fleet_profile.json)，默认跳过自动委派 `ultra`。常规协作按四条原则编排：
1. **常规编码与异构验收**：成块编码与 `gate` 门禁走 `role:code`（内部 `native_swarm` 用 `swarm_worker_model_family`），审查与正向推导走跨端或审慎槽位；主会话用高吞吐编码系列统筹时，后台验收流自动切至低幻觉审慎系列。
2. **重大方案防锚定双出**：面对重大架构、物理建模或软件结构抉择，优先用 `batch` 或 `--isolation clean_room` 仅传入用户原话与必要背景（不附主控草稿与倾向）独立盲出双方案，再由主会话逐条核验合并交用户拍板。
3. **未闭环推导正反双流**：缺乏现成单元测试时，编排“审慎模型正向推导 + 高算力模型对照初稿排查反例与奇点边界”双流，单端受限时 `audit` 自动配对同端 `deep_families` 与 `code_families`。
4. **审查分级、风险分层挡位与轮次收敛**：审查发现区分阻断级（`critical`/`high` 且写清触发或复现链路的实质缺陷）与建议级（`medium`/`low` 仅作参考，不借极端假想反复打回）；`role:review` 常规任务按配置推荐挡位运行，命中物理守恒、离散算子、边界条件、并发死锁或权限越界等高风险关键词时自动升至 `max` 挡深审；门禁按配置的 `default_gate_max_rounds` 封顶，超时未收敛交用户拍板。

## 持久化会话、输入信封与超时抢救

外部无头调用自动补齐声明窗口、防分页环境变量、分后端只读内联、自适应快照与脱敏，由脚本机械执行：
- **精确输入信封**：用 `--files <相对路径>`（流字段 `files`）显式声明必读文件，用 `--negative-scope-file`（`negative_scope_files`）与 `env_contract` 声明禁止越界范围与环境约定。超声明窗口时默认报 `over_context_window` 并给出 `suggested_shards`（确需抽查加 `--allow-over-budget`）。
- **默认持久化与 `followup` 续接**：按 `persist_sessions_by_default` 默认持久化（`--ephemeral` 关闭），会话归档至 `<plan_dir>/headless_sessions/`。复核存疑或从超时断点续查时运行 `followup --receipt <回执.json> [--stream <流号>] [--effort <挡位>] --task "<追问>"`；续接超时回执未指定挡位时自动按 `followup_default_effort` 降挡，若原回执标记 `resumable: false` 则阻断并提示分片重开。
- **超时抢救与引用核验**：Codex、Claude Code 与 DSH 在持久化开启且 `timeout >= 120 s` 时，到达 `soft_timeout_ratio` 自动降挡向独立 `result_wrapup.json` 紧凑只读收口。`agy` 因 `--print-timeout` 不取消后台回合，不预扣收口预算而是将完整 `timeout` 交给主回合，超时后标记 `resumable: false` 防误续接。硬超时均保留总时长、部分 Token 与 `timeout_salvage`，只读回执自动产出 `citation_audit` 供主会话按 R6 复核。

## 两道可用性检查与全通道确认单

第一道门由 `fleet-evolve` 联网评估并经用户审定 `subscription_matrix`（`enabled: false` 全局跳过）；第二道门由 `plan` 纯离线复查本机程序、凭据、路由健康与可读额度（读取 `capability_ledger`，不联网刷新）。遇 `quota_exhausted` 立即停止重试并记录冷却；仅显式加 `--fallback-on-quota` 且工作区零改动时按配置回退。

轻任务按 `runtime_tunables.dispatch_confirmation` 静默执行（单路只读带 `--files` 时按声明文件数计量）。外部无头调用及内部 `invoke_subagent`、`run_workflow`、`spawn_agent`、`Agent`、`swarm` 等多路或可写委派，均须先经 `plan` 落盘 `<ticket_id>.json` 与 `<ticket_id>_card.md`。明细文件包含 4 列总览表与 14 项字段：流号、子任务与范围、通道、客户端及子代理类型、模型名字、挡位、上下文窗口、是否无头、是否净室与隔离级别、访问模式/写入范围/验收命令、内联与超时、当前是否可用、额度类型与状态、回执或日志位置。日常对话仅展示 3 句话 `chat_brief` 与明细链接（本地交互勿传 `--redact` 以保留可点击链接），不在聊天框贴表刷屏。

获批后以 `plan --approve-ticket` 登记，其中 `--override-json` 对外部无头仅可微调 `engine`/`tier`/`timeout`，对原生通道仅可微调 `tier`/`timeout`、禁止微调 `engine`；规划阶段可选 `--obligations-file` 附带显式义务清单随票据落盘。执行绑定 `--ticket` 并由 `status_guard.py` 核验门禁，持票执行还会校验 `--write-scope`（须为获批范围子集）与 `--verify-cmd` 一致性。`batch` 仅执行 `cae_harness_headless` 通道并按整数 `wave` 升序运行：前波次同目录可写流失败默认阻断后续波次为 `upstream_wave_failed`（仅声明 `run_on_upstream_failure: true` 的只读流放行），同波次互不重叠 `write_scope` 可安全并发。

重负载派单完工硬触发生成机械对账表落盘至 `<plan_dir>/reconcile/<run_id>_reconcile.md`（不污染目标业务仓库），回执仅附精简 `reconciliation` 摘要与 `chat_line` 链接。压缩恢复、交接门禁与完工回执核验由 `session_context_loader.py`、`handoff_gate.py` 与 `reply_repeat_guard.py` 在三端钩子通用守卫，按 `run_id` 核验回执与 `reconcile_file` 查看链接。单路 `task` 或 `gate` 省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代；主会话仍须逐路复核状态、Token 明细与汇总算术。

## 命令示例

将 `HARNESS` 指向本机已安装技能脚本（上游维护可用 `template/user-guard/skills/cae-harness/scripts/cae_harness.py`）；Windows 用 `python -B -X utf8`，WSL2/Linux 用 `python3 -B -X utf8`：

```powershell
# Windows PowerShell（按所用客户端选一行）：
$HARNESS = "$HOME\.gemini\config\plugins\user-guard\skills\cae-harness\scripts\cae_harness.py" # Antigravity
# $HARNESS = "$HOME\.codex\skills\cae-harness\scripts\cae_harness.py"                         # Codex
# $HARNESS = "$HOME\.claude\skills\cae-harness\scripts\cae_harness.py"                        # Claude Code
# $HARNESS = "$HOME\.dsh\skills\cae-harness\scripts\cae_harness.py"                           # DSH
```

```powershell
python -B -X utf8 "$HARNESS" probe --caller auto --redact
python -B -X utf8 "$HARNESS" assess --caller auto --redact
python -B -X utf8 "$HARNESS" check-grounding --target "<项目根目录>" --strict --doc-hygiene
python -B -X utf8 "$HARNESS" read-plan --target "<项目根目录>" --cross-client-mode risk-tiered --emit-spec "<分片工作流.json>"
python -B -X utf8 "$HARNESS" cross-check --target "<项目根目录>" --receipt "<回执路径>" --read-plan "<阅读计划JSON>"
python -B -X utf8 "$HARNESS" reconcile --target "<项目根目录>" --base-ref HEAD --cross-check "<跨端核对JSON>"
```

多波次与多文件 `--spec-file` 示例（双源审查应显式分端或直接用 `audit`）：

```json
[
  {"stream_id":"S1","wave":1,"channel":"cae_harness_headless","kind":"review","engine":"role:review","target":"<项目根目录>","files":["docs/spec.md","data/index.json"],"negative_scope_files":["docs/cancelled.md"],"subtask":"核对双文件数据与规范一致性"},
  {"stream_id":"S2","wave":2,"channel":"cae_harness_headless","kind":"review","engine":"dsh:@review","isolation":"clean_room","target":"<说明文档路径>","subtask":"净室盲读检查可读性"}
]
```

确认、批准、批处理、续接追问及 `gate` 强制验收示例：

```powershell
python -B -X utf8 "$HARNESS" plan --spec-file "<工作流文件>" --tier high --session-id "<会话标识>" --workflow-bg "核心文档已重写" --workflow-goal "分波次核对实现" --workflow-impact "只读核查并生成回执"
python -B -X utf8 "$HARNESS" plan --approve-ticket "<票据标识>" --session-id "<会话标识>"
python -B -X utf8 "$HARNESS" batch --ticket "<票据标识>" --session-id "<会话标识>" --output "<回执路径>" --redact
python -B -X utf8 "$HARNESS" followup --receipt "<回执路径>" --stream S1 --task "补充核对第 42 行边界条件符号" --output "<追问回执>" --redact
python -B -X utf8 "$HARNESS" plan --resume-ticket "<票据标识>"
python -B -X utf8 "$HARNESS" gate --worker role:code --target "<项目根目录>" --write-scope "<相对路径>" --verify-cmd "<验收命令>" --task-file "<任务文件>" --ticket "<票据标识>" --session-id "<会话标识>"
```

## 只读隔离与结果验收

无头执行支持独立的 `--isolation` 参数，与后端、模型、挡位、持久化及读写模式正交组合：

| 隔离级别 | 保留与剥离边界 | 支持后端与读写限制 |
| --- | --- | --- |
| `full` | 保留全局规则、安全钩子（DSH 当前为 `soft_rules_only`）与全局技能 | 四端均支持，只读与可写均适用（`codex`、`claude`、`dsh` 默认值） |
| `no_skills` | 保留全局规则与安全钩子，剥离全局技能与斜杠命令 | 四端均支持，只读与可写均适用（`agy` 默认值） |
| `hooks_only` | 保留底层凭据与安全钩子，剥离全局规则与技能 | 仅 `codex`、`claude` 支持；`agy` 与 `dsh` 显式报错 |
| `clean_room` | 等同 `--clean-room`：仅保留凭据，剥离全局规则、钩子、技能及项目自动说明 | 仅 `codex`、`claude`、`dsh` 支持，且仅限只读任务 |

审查任务禁止改文件或跑构建测试，前后以文件快照核验零改动。`check-grounding --strict` 从源码和配置构建词法锚点，自动采样大文件并跳过工具链与归档目录。主会话采纳后台建议前须亲自打开原文件核对精确行号；验收严禁放宽容差、守恒或收敛阶判据，严禁删改断言或将局部补测冒充全套通过。
