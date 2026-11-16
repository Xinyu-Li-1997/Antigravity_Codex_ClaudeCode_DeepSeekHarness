# 日常研发、派工确认与中断接力指南

本指南面向已经安装配置的研发人员。先明确数学或软件契约、文件范围与验收判据，再选择单会话执行或后台协作。工具结构见 [架构与钩子](architecture-and-hooks.md)，完整参数见 [cae-harness 技能](../template/user-guard/skills/cae-harness/SKILL.md)。

## 一、准备任务与确认边界

普通局部修改可以在当前客户端读清源码后直接实现并验证。实质性推导、多文件编码验证及关键独立审查，按本机可用端组织协作；用户限定单会话或禁止再次编排时服从任务边界。

后台进程看不到主会话的完整讨论，任务单须独立写清以下内容：

1. 数学定义或软件契约，包括符号、法向、边界条件和成立前提。
2. 项目根目录、当前分支状态与已有改动。
3. 必读文件和精确行号，限定读取范围。
4. 允许修改路径；只读审查明确禁止修改文件和运行构建测试。
5. 实际验收命令、原有判据和预期证据，严禁放宽 `rtol`、`atol`、守恒与收敛阶要求或删改断言。

任务单中的物理模型和数值判据来自当前课题，不把配置仓库的演示路径、静态容差或假想算例抄成项目事实。运行数值实验前先核对数学定义、源码、边界相容性和参考基准。

以下命令从仓库根目录运行。普通项目中将脚本路径换为已安装技能的实际路径；Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`。尖括号占位符须替换，任务单、工作流文件及输出目录须已在本轮授权内准备。默认票据、日志和临时制品路径也受当前授权约束。

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --caller auto
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py assess --caller auto
```

`probe` 与 `assess` 用于核对本机配置和角色解析，不代表本轮已经完成模型调用。日常派工不联网刷新性能记录。

## 二、点名、双门检查与完整确认单

通过 `--user-prompt "<用户点名原话>"` 保留用户意图。P0 第一优先级锁定点名的客户端、模型、挡位或省额度要求，角色只补齐未指定部分。不可用时说明原因，不静默更换。`--kind` 指定工作性质，`--engine` 或 `--worker` 指定选路。

第一道门由独立 `fleet-evolve` 流程完成：用户批准 `subscription_matrix` 的 agent 与 LLM 启停，停用项全局跳过。第二道门由 `plan` 离线复查当前配置、路由、可读额度及冷却记录。滚动窗口百分比、重置时间、卡片套餐或余额类型、日月限额按本机能提供的信息显示；缺失保持未知，不声称查询了服务端实时余额。

轻任务按 `runtime_tunables.dispatch_confirmation` 阈值处理。重负载覆盖内部原生与外部无头通道，先形成计划再确认。后台任务常规使用单模型非委派最高强度；纯文字润色和显式省额度按配置执行。外部调用自动补齐声明窗口、无头标志、只读行号内联、快照和脱敏。

### 单流计划

下面形成一项只读审查计划。`--save-ticket` 让轻计划也保存票据；`--plan-dir` 和 `--output` 应指向获准位置。命令只生成计划，不启动代理。

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py plan --caller auto --kind review --engine role:review --target "<目标文件路径>" --session-id "<当前会话标识>" --user-prompt "<用户点名原话>" --inline-readonly --save-ticket --plan-dir "<授权票据目录>" --output "<授权计划回执路径>" --redact --workflow-bg "需要核对文档与实现" --workflow-goal "只读核查目标文件及行号依据" --workflow-impact "不修改源码，保存计划与后续回执"
```

主会话完整展示人话背景、目标、后果、14 列矩阵和选项卡，不只报引擎简称。实际矩阵按如下列序显示，未知值保留：

| 流号 | 子任务与范围 | 通道 | 客户端及子代理类型 | 模型名字 | 挡位 | 上下文窗口 | 是否无头 | 是否净室 | 访问模式、写入范围与验收命令 | 内联与超时 | 当前是否可用 | 本机配置额度类型与当前状态 | 回执或子代理日志位置 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 每路标识 | 工作和目标路径 | 外部或原生 | 执行端与类型 | 实际模型及 `inherit` 解析 | 实际挡位 | 声明值或未知 | 实际状态 | 实际状态 | 只读或可写及边界 | 实际参数 | 判断及原因 | 来源、状态与未知项 | 回执或轨迹路径 |

矩阵后展示 `capability_ledger.last_online_refresh_at` 与 `capability_ledger.local_bench_verified_at`，再给出批准当前计划、微调后批准、暂缓并交接三个选项。静态文档只引用键名；本轮弹卡使用磁盘记录中的实际更新时间。

### 多流计划与 batch

`--spec-file` 使用非空对象列表，每路目标应互不依赖，子任务边界清楚。以下只定义外部只读通道，字段须替换为实际任务。

```json
[
  {"stream_id":"S1","channel":"cae_harness_headless","kind":"review","engine":"role:review","target":"<第一份文件路径>","subtask":"核对接口文档与实现"},
  {"stream_id":"S2","channel":"cae_harness_headless","kind":"review","engine":"role:review","target":"<第二份文件路径>","subtask":"核对命令参数与配置"}
]
```

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py plan --spec-file "<工作流文件路径>" --session-id "<当前会话标识>" --plan-dir "<授权票据目录>" --user-prompt "<用户点名原话>" --workflow-bg "指定文件已修改，需要分路核对" --workflow-goal "检查每路文件的实现依据" --workflow-impact "只读检查并生成回执"
```

两个 `role:review` 不保证来自不同客户端；需要双源独立审查时通过 `audit` 明确来源。不要把并行流数当作模型独立性的证据。

用户明确批准后才登记并执行，所有命令使用相同计划目录和会话标识：

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py plan --approve-ticket "<票据标识>" --session-id "<当前会话标识>" --plan-dir "<授权票据目录>" --user-prompt "<用户点名原话>"
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py batch --ticket "<票据标识>" --session-id "<当前会话标识>" --plan-dir "<授权票据目录>" --user-prompt "<用户点名原话>" --output "<授权回执路径>" --redact
```

`batch` 按 `batch_max_workers_per_backend` 控制并发，只执行 `cae_harness_headless`。额度耗尽后停止同后端排队流；已经启动的任务按实际结果报账。不要绕过 `PreToolUse` 直接裸调客户端无头 CLI。

### 原生通道与 inherit

原生代理也先纳入同一确认机制。下面演示 Codex 原生只读代理计划；多路时用一个工作流文件列全各路。

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py plan --channel native_subagent --caller codex --agent-type readonly-auditor --model inherit --kind review --target "<目标文件路径>" --session-id "<当前会话标识>" --plan-dir "<授权票据目录>" --save-ticket --workflow-bg "需要原文行号核对" --workflow-goal "只读返回目标文件依据" --workflow-impact "不修改文件，不运行构建测试"
```

确认单显示实际模型及 `inherit` 解析状态，不能用角色名代替模型。原生通道由对应客户端按获批计划调用，不能交给 `batch`；它不具备外部无头或净室隔离属性。多路、可写原生委派受工具前票据门禁检查。

## 三、按工作性质执行并核对结果

| 工作 | 任务与角色组合 | 关键边界 |
| --- | --- | --- |
| 方向论证 | `task --kind direction --engine role:orchestrator` | 只读给出方案与依据 |
| 数理推导 | `task --kind math --engine role:math` | 逐项核对定义、公式、离散格式和成立条件 |
| 编码与强制验收 | `gate --worker role:code` | 同时声明 `--write-scope` 和非空 `--verify-cmd` |
| 独立审查 | `task --kind review --engine role:review` 或 `audit` | 禁写、禁构建测试，前后快照核对零改动 |
| 文档润色 | `task --kind polish` | 只读返回稿件，附 `grounding_audit`，由主会话采纳 |
| 明确省额度 | 相应工作性质加 `--engine role:fast` | 不存在 `--kind fast`，不能以轻量检查替代核心审查 |

可写计划中须明确访问范围与验收命令，批准后使用一致的任务单、模型、挡位和超时。下例只展示执行阶段，不替代此前的计划与确认：

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py gate --worker role:code --target "<项目根目录>" --task-file "<任务文件路径>" --write-scope "<授权相对路径>" --verify-cmd "<实际验收命令>" --ticket "<票据标识>" --session-id "<当前会话标识>" --plan-dir "<授权票据目录>" --output "<授权回执路径>" --redact
```

验收失败时，`gate` 在配置轮数内回传真实输出供修复；未通过就如实报告。任务本身完成不等于编译、离线单测或算例验证通过。

只读小文件默认附精确行号内联，`--inline-readonly` 可显式声明，规模限制由 `inline_readonly_max_kib` 控制；未内联内容不能声称已经读取。新读者可读性检查可在受支持后端的只读 `task` 加 `--clean-room`，净室暂存目录同样服从授权。无头子进程不得再次调用编排器。

执行触发 `quota_exhausted` 后停止重试。只有显式 `--fallback-on-quota` 且工作区尚无文件改动时才允许配置回退，仍须遵守 P0 和重负载重新确认要求。工作区已修改时保留现场并报告。

| 回执字段 | 核对内容 |
| --- | --- |
| `status`、进程退出码 | 成功、失败、超时或额度耗尽是否与实际输出一致 |
| `verification_executed`、`gate_passed` | 是否实际执行验收，验收是否通过 |
| `changed_files`、`scope_violations` | 是否只修改获准文件，是否违反只读约束 |
| `history`、`verify_exit_code` | 每轮失败与修复依据是否可追溯 |
| `grounding_audit`、`result.evidence` | 文档事实检查及文件行号依据是否成立 |
| `token_usage_summary` | 可取得的实际 Token 汇总，缺失时不得估算冒充 |

文档完成后执行事实锚定，主会话再对照源码、配置和公开脱敏要求核对。

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target "<项目根目录>" --strict
```

## 四、压缩与交接打断后的接力

压缩后先读用户原话和保留的实质子代理结论，对照磁盘文件与 Git 状态说明已完成和剩余工作。常规摘要与未决工作流专项确认单分别恢复；专项确认单直接取自票据，不凭历史印象补写。

若进入 `drain` 或 `freeze` 强制交接，停止新派工并整理【新对话提示词】。`Stop` 检查其中是否完整嵌入背景、目标、预计后果、14 列矩阵、性能记录更新时间、选项卡、票据和恢复命令。Codex 与 Claude Code 默认 `advisory`，实际强制行为取决于模式。

新会话先读取旧票据：

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py plan --resume-ticket "<票据标识>" --plan-dir "<授权票据目录>" --user-prompt "<用户点名原话>"
```

恢复会重新核对内容和可用性，但不会改绑旧 `session_id`。跨会话执行须据旧流内容与当前会话标识重新生成确认票据，重新展示完整计划后按用户选择登记。压缩、恢复和超时不算批准；票据缺失或变化时报告缺口并重新形成计划。

## 五、完工报账与验证深度

`reply_repeat_guard.py` 在完工 `Stop` 检查回复中是否含有成败词，以及 Token 用量或无法统计说明。外部任务还须覆盖各流标识和账单中的实际回执文件名；单路 `task` 或 `gate` 可用模型名或任务状态代替默认流号。原生任务须覆盖完成数量、各流标识或各角色名中的一项，并提供日志或轨迹说明。流标识和外部回执文件名按完整词项匹配。漏报时同一回合单次阻断并附上磁盘账单。主会话仍须自行逐路核对具体状态、Token 明细与汇总算术，静态检查不核实这些内容的语义和计算结果。

| 汇报内容 | 应交代的事实 |
| --- | --- |
| 各路成败 | 哪一路完成、失败、超时或额度耗尽；是否保留了修改现场 |
| Token 明细与汇总 | 按实际暴露字段统计，说明未提供的通道，不重复合计 |
| 回执与轨迹 | 提供实际文件路径，便于复核；未生成时如实说明 |
| 源码或配置完成 | 已写好哪些内容；没有运行证据时只报告这一层 |
| 离线检查通过 | 实际运行的命令、结果和覆盖范围 |
| 本机或算例实测通过 | 当前目标环境、物理判据与结果；只列本轮核心链路的未验证项 |

主会话采纳任何后台结论前，亲自打开原文件核对精确行号、数学符号与适用条件。推送、全局配置写入、矩阵调整和 ABI 强耦合升级仍按各自授权处理，不随任务完工自动执行。
