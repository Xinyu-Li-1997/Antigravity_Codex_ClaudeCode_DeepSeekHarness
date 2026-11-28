# 架构设计、共有生命周期钩子与运维参考

本文面向配置仓库维护者、安全审计人员与排查门禁行为的工程师，集中说明底层机制、配置键名、预算公式、上游维护与卸载步骤。入门说明见 [仓库总说明](../README.md)，日常用法见 [日常工作流指南](workflow-guide.md)。

![固定机制层、动态配置层与全通道派工架构](assets/architecture-overview.svg)

---

## 一、模块分工与双工作区隔离

全仓仅依赖 Python 3.11+ 标准库。所有会话钩子均为事件触发的短生命周期子进程，读写 JSON 后立即退出，不常驻内存；唯一可选常驻进程是 Claude Code 本地回环路由 `router.py`（仅绑定 `127.0.0.1`、`::1` 或 `localhost`，健康接口仅代表进程存活，不代表服务端余额充足）。

| 模块与路径 | 适用范围 | 核心职责 |
| --- | --- | --- |
| [rules/AGENTS.md](../template/user-guard/rules/AGENTS.md) | 通用 + 上游 | `R0–R11` 全局规则唯一模板；约束受众分层、机械下沉与对称诚实披露，不含时期性模型名 |
| [fleet_profile.json](../template/user-guard/skills/cae-harness/fleet_profile.json) | 通用 + 上游 | 唯一动态配置源：维护五角色 `roles`、四端 `native_agent_profiles`、`runtime_tunables`、`subscription_matrix` 与 `capability_ledger` |
| [cae_harness.py](../template/user-guard/skills/cae-harness/scripts/cae_harness.py) 与 `scripts/_harness/` | 通用 + 上游 | 跨端无头调度与离线诊断总控（按配置、目录、执行引擎、文档核验、机械对账、阅读分片与调度子模块拆分）：P0 点名锁定、角色选路与风险升挡、`--isolation` 四级隔离、持久化会话与超时抢救、`<ticket_id>_card.md` 落盘，以及 `check-grounding`（含 `--doc-hygiene`/`--hygiene-only`/`--output`）、`read-plan`、`cross-check`、`reconcile` 四项离线确定性工具 |
| [skills/cae-writing/](../template/user-guard/skills/cae-writing/SKILL.md) | 通用 | 按需学术写作与改稿技能：按体裁分册加载中文期刊、英文期刊、学术专著、科研报告与投稿前自审及审稿回复指南，严守审稿保密伦理、优先尊重项目自有词表，不增加常驻钩子 |
| [skills/cae-axioms/](../template/user-guard/skills/cae-axioms/SKILL.md) | 通用 | 点名触发的公理审查与硬状态机技能：承载四层条款级单向公理分册与 [文献溯源](../template/user-guard/skills/cae-axioms/references/layer-citations.md)、双档状态机（`axiom_gate.py` 与 `_axiom_lib/`）、零 Mathlib 纯核 Lean 4 真内核驱动（`KernelAudit.lean` 与四份 `lean/` 骨架）及净室 `packet_replay` 协议，不增加常驻钩子 |
| [user_words.py](../template/user-guard/hooks/user_words.py) | 通用 + 上游 | 钩子共用基础库：跨平台文件锁、原子写、三端轨迹去噪与子代理摘要提取、历史确认单折叠及账单存取 |
| `hooks/_lib/` | 通用 + 上游 | `fs_safety.py`（重解析点防御原子写）、`shell_lex.py`（Shell 拆壳与 PowerShell 续行归一化）、`adaptive_policy.py`（读取 `modelName` 与挡位并按窗口缩放阈值）、`process_guard.py`（墙钟自毁与 POSIX 条件虚拟内存上限） |
| [session_context_loader.py](../template/user-guard/hooks/session_context_loader.py) | 通用 + 上游 | 恢复与调用前门禁：普通回合零字节注入；压缩后分块补回原话、实质子代理摘要、未决票据总览与有界对账及阅读计划指针，并将两次压缩之间的实质助手回复独立落盘为可按需 `read_file` 的紧凑索引 |
| [status_guard.py](../template/user-guard/hooks/status_guard.py) | 通用 + 上游 | 工具执行前门禁：静态解析文件工具、Shell 包装与内联 Python AST（支持字面量 `str.join`、有限元组解包循环及项目内多候选只读 `git -C`），拦截越界写删、本地会话删除 RPC、外部 `git worktree`、`git grep -O`、未分页 Git、无 `timeout` 桌面 GUI 挂死调用、空转轮询与裸调 CLI，核验已批票据与原生通道容量 |
| [reply_repeat_guard.py](../template/user-guard/hooks/reply_repeat_guard.py) | 通用 + 上游 | 回合结束门禁（无头子进程 `CAE_HARNESS_DEPTH` 自动跳过）：拦截空等刷屏、重复贴票、局部补测冒充全过，单次扫描三端轨迹统一守卫零探测否定、局部排查全称缺口与长文单端整写免审（联动 `code_families` 角色提示），并按 `run_id` 核验回执与 `reconcile_file` 链接 |
| [handoff_gate.py](../template/user-guard/hooks/handoff_gate.py) | 通用 + 上游 | 长会话分级交接门禁：追踪压缩次数、三压缩起近期均值间隔与单回合高频压缩熔断（`turn_cp_thrashing`）、轨迹体积与读图字节，在 `drain`/`freeze` 阶段放行界面进度与权限只读工具、拦截会话创建删除 RPC 与新派工，并核验【新对话提示词】与待拍板占位符保护 |
| [install.py](../install.py)、`install.ps1`、`install.sh` | 上游专属 | 校验非符号链接与非重解析点后幂等分发四端配置；支持零进程读取 PE 与 dpkg 桌面应用版本，提供配置漂移、Windows 与 WSL2 双端对齐、DSH 协议探测及科学计算包 ABI 风险体检 |
| [fleet_state.py](../template/user-guard/skills/fleet-evolve/scripts/fleet_state.py) | 上游专属 | 核验 `upstream_config_repo` 身份；管理提案审批锁（`propose`/`approve`）、重启重入锁（`record-pass1`/`verify-pass2`）与终审事实锚定门禁 |
| [router.py](../template/claude/router/router.py)、[ccswitch_admin.py](../template/claude/router/ccswitch_admin.py) | 通用 + 上游 | 本地回环 `/v1/messages` 前缀分流（向非回环上游注入密钥时强制 HTTPS、输出脱敏单行日志、遇 401 或 403 使缓存失效），以及带 SQLite 在线备份的 CC Switch 切卡 |

`workspace_mode` 依据工作区或父目录是否同时存在 `install.py` 与 `template/user-guard/plugin.json`，自动区分 `upstream_config_repo`（上游配置仓）与 `general_project`（通用课题仓）。独立安装的 `fleet_state.py` 在通用目录未传 `--repo` 时返回 `not_upstream_config_repo`（退出码 `2`）。

---

## 二、两阶段可用性检查与 `fleet_profile.json` 结构

可用性核查分为互不干扰的两个阶段：

1. **维护期联网评估（`fleet-evolve`）**：联网核对客户端与模型变化，经用户确认后写入 `subscription_matrix`（`enabled: false` 全局跳过），刷新时间记入 `capability_ledger`。
2. **派工前纯离线复查（`cae_harness.py plan`）**：仅核查本机可执行文件、凭据、本地回环路由、本地会话滚动窗口百分比、CC Switch 卡片类型与配额冷却记录；未暴露的服务端余额如实标为未知。

`fleet_profile.json` 的顶层配置结构如下（单测可用 `CAE_FLEET_PROFILE` 隔离覆盖）：

| 配置分区 | 核心键名 | 工程作用 |
| --- | --- | --- |
| `roles` | `orchestrator`、`math`、`code`、`review`、`fast` 候选、推荐挡位与短板隔离 | 动态配置五个角色，不绑定单一客户端；用户点名按 P0 锁定；`review` 配合 `select_task_tier` 按风险升挡 |
| `native_agent_profiles` | 四端主模型策略、原生子代理机制、只读代理与钩子协议 | 独立描述各端内生代理能力，与外部无头调度解耦 |
| `subscription_matrix` 与 `capability_ledger` | 客户端及模型启停开关、额度类型与离线刷新时间戳 | `enabled: false` 全局跳过；日常派工仅离线读取时间戳，不发起联网核查 |
| `runtime_tunables.compaction_recovery` | `per_client` 预算、`recent_subagent_window`、`min_substantive_subagent_chars`、`subagent_head_ratio` | 固定保留最新一条子代理返回，其余择优保留实质结论并按窗口与强度缩放 |
| `runtime_tunables.handoff_thresholds` | `default_mode`、压缩次数、轨迹体积、读图阈值与 `drain` 收尾预算 | Antigravity 默认分段交接；Codex 与 Claude Code 默认 `advisory` 提醒 |
| `runtime_tunables.loop_guards` | `hook_safety`（墙钟与 POSIX 条件 `max_rss_mib`）、`reply_repeat`、`status_guard`、`lifecycle_log` | 防止钩子卡死或内存失控，同时拦截空转轮询与重复空等 |
| `runtime_tunables.headless_dispatch` | `persist_sessions_by_default`、`soft_timeout_ratio`、`wrapup_min_sec`、`followup_default_effort`、`default_gate_max_rounds`、分后端内联上限与并发上限 | 统一控制持久化会话、软超时降挡收口（`result_wrapup.json`）、硬超时抢救、超时续接降挡与快照过滤 |
| `runtime_tunables.dispatch_confirmation` | 流数、文件数、Token、超时分界、`ticket_ttl_sec`、`quota_block_percent` | 外部无头与内部原生通道共用同一套轻重任务分界阈值 |

---

## 三、全通道确认票据与四阶段共有钩子

![会话确认、压缩接力与完成核验流程](assets/lifecycle-and-verification.svg)

### 1. 确认票据与 `PreToolUse` 核验

`plan` 统一覆盖 `cae_harness_headless`、`native_subagent`、`native_workflow`、`native_swarm` 四类通道。落盘时先原子写 `<ticket_id>.json`，再把完整背景、4 列总览和 14 项明细写入 `<ticket_id>_card.md`，同时生成 3 句话确认条 `chat_brief` 与精简交接片段。

登记 `plan --approve-ticket` 前会再次离线核验选路无漂移；执行时 `status_guard.py` 完成四项检查：

1. **拦截裸调无头 CLI**：拆壳识别环境变量前缀、`cmd /c`、`powershell -EncodedCommand`、`bash -lc`、`wsl`、`sudo`、`timeout` 等常见包装；
2. **核验外部重负载票据**：校验当前会话有效 `APPROVED` 票据的 `--plan-dir`、`--isolation`、`--write-scope` 子集及 `--verify-cmd` 一致性（`followup` 按原回执读写属性判定）；
3. **核验内部原生通道容量**：按回合快照核对原生通道名额（`swarm add` 按 `tasks` 长度或 `n` 计流数）；
4. **缺省目标保底估算**：`task` 省略 `--target` 时按当前工作目录估算规模。

### 2. 四阶段门禁与四客户端协议映射

| 防护阶段 | 对应脚本 | 核心确定性行为 |
| --- | --- | --- |
| **会话恢复与调用前** | `session_context_loader.py` + `handoff_gate.py` | 无新压缩时零字节注入；压缩后分块补回原话、实质子代理摘要、未决票据总览及有界对账与 `read-plan` 指针 |
| **新指令提交时** | `handoff_gate.py`（Antigravity 在 `PreInvocation` 对账） | 识别用户简短明确的交接或暂缓指令，长文引用不误触 |
| **工具执行前** | `status_guard.py` + `handoff_gate.py` | 守卫项目外写删边界与同会话外部目录续租（不禁止本机只读诊断），拦截空转轮询与裸调 CLI，交接期放行只读核验 |
| **回合结束时** | `reply_repeat_guard.py` + `handoff_gate.py` | 拦截空等刷屏、重复贴票、局部补测冒充全过及三类回合实证失实（零探测否定、局部排查全称缺口、长文单端整写免审），并核验回执、`reconcile_file` 链接与交接提示词 |

Antigravity、Codex 与 Claude Code 按原生协议接入同一套生命周期硬钩子，DSH 当前以软规则配合无头调度快照兜底，均不依赖客户端私有扩展字段：

| 客户端 | 全局目录与宿主事件适配 |
| --- | --- |
| **Antigravity** (`agy`) | 同步至 `~/.gemini/config/plugins/user-guard`；原生 `hooks.json` 挂 4 类事件并联动 `handoff_gate.py`；遇截断字段回查 `transcript_full.jsonl`；`--isolation` 拒绝 `hooks_only` 与 `clean_room` |
| **Codex** (`codex`) | 同步至 `~/.codex`；`hooks.json` 挂 5 类事件，实质未变时不覆写以保护宿主信任记录；合并 `sessions/` 与 `archived_sessions/` 提取记录；完整支持全部四档 `--isolation` |
| **Claude Code** (`claude`) | 同步至 `~/.claude`；向 `settings.json` 合并 5 类事件并默认同步 `ccswitch_admin.py`（`--no-claude-hooks` 跳过）；无头只读传 `--permission-mode dontAsk`，`clean_room` 附加 `--bare` 与 `--disable-slash-commands` |
| **DeepSeek Harness** (`dsh`) | 同步至 `~/.dsh/AGENTS.md` 与 `skills/`，不修改 `.credentials.yaml`；当前 CLI 为 `soft_rules_only`（由 `--audit-upgrade` 按需探测 `hook_protocol`）；无头会话隔离于 `headless-sessions`，支持 `clean_room` 与尾随 `DSML` 清洗 |

### 3. 压缩双块恢复与跨会话接力不变量

- **防二次截断与围栏闭合**：在最近 `recent_subagent_window` 条子代理返回中固定保留最新一条，归档与内联均基于完整 `raw_body` 独立截取；截断位置处于代码块内时，由 `_close_unbalanced_fence` 自动闭合围栏。
- **双压缩窗口助手回复归档**：三端恢复钩子经 `select_compaction_window_assistant_replies` 与 `save_assistant_replies_markdown` 仅提取两次压缩边界间的实质助手回复（Antigravity 按需回查 `transcript_full.jsonl`，空窗回退前窗末条），独立写入 `<session_id>_assistant_replies.md` 并与用户原话清单物理隔离。系统注入仅附路径与标题索引；遇简短确认指令时提示先 `read_file` 核对完整方案。
- **未决工作流独立分区**：`collapse_historical_cards` 折叠历史长表；`render_pending_workflow_block` 独立于原话预算直接读取磁盘 `<ticket_id>.json`（存在非符号链接的常规明细文件时，仅注入 4 列总览与文件链接，缺项标为 `未记录`）。`plan --resume-ticket` 保留原 `session_id` 且不自动改绑，跨会话执行须重建当前会话票据。

---

## 四、离线确定性核验工具与单分片预算公式

`cae_harness.py` 提供四项纯离线、零模型调用、四端通用的确定性工具：

### 1. 零黑名单事实锚定与文档卫生守卫（`check-grounding`）

- **词法事实锚定（`--strict`）**：扫描源码、配置、`.lean`、`.bib` 与 `NOTICE` 构建标识符、CLI 选项、定量数字与双字词干集合，覆盖 `.md`、`.tex`、`.rst` 与 `.svg` 文档（LaTeX 自动跳过导言区、数学环境、注释与结构宏参数），逐行拦截无代码依据的参数、文件名、陌生缩写与失步数字。扫描阶段跳过 `tests/` 与 Markdown 代理契约，对 Codex `.toml` 仅提取键名，并将三端代理正文列为被审对象。
- **通用文档卫生守卫（`--doc-hygiene`）**：不依赖硬编码黑名单，统一覆盖三类跨格式与分层质量问题。其一是跨格式交付完整度（面向 `.md`、`.tex` 与 `.rst`）：按文档所在目录校验本地图件引用是否真实存在（`broken_local_media_ref`，LaTeX 兼容 `\graphicspath` 与自动扩展名，Markdown 兼容含括号文件名）。同时识别正式文稿声明多条定理或命题却全文缺失证明段落的空壳问题（`unproved_theorem_shell`，兼容 LaTeX 自定义 `\newtheorem` 与 reST 原生下划线标题）。其二是 Markdown 行文结构：拦截引导句计数与紧邻表格或列表行数矛盾（`count_structure_mismatch`），以及补丁超长句与四段以上中文斜杠串（`runon_patch_sentence`、`slash_chain`）。其三是分层治理：拦截长段技术细则跨 ≥ 3 份文档逐字重复（`cross_doc_duplication`），以及根目录 `README.md` 超体积预算、暴露 `_xxx` 私有符号或单段过多行内代码（`landing_size_budget`、`landing_private_symbol`、`landing_jargon_density`）。

### 2. 读前分片、跨端核对与机械对账（`read-plan` / `cross-check` / `reconcile`）

- **`read-plan`（读前尺寸评估与保守层级分片）**：同时按安全输入 Token 预算（扣减 `--context-used-tokens` 与输出预留）和真实 UTF-8 带行号内联字节上限（扣减 `--shared-files`）双口径评估体积。超大单文件按 Markdown 标题或 Python 顶层定义切分为带重叠的连续行段，按 AST 与 Markdown 依赖聚类并标记跨片边（`cross_shard_edges`）。默认采用保守并发策略（总并发 `<= 2` 且受每端并发上限约束），按 `risk-tiered` 或 `all-engines` 编排波次，落盘 `<plan_dir>/read_plans/<plan_id>_read_plan.md` 与可选 `--emit-spec`，并如实列出默认跳过的归档目录（可传 `--include-dirs` 纳入）。
- **`cross-check`（跨端覆盖矩阵与分歧保留）**：读取多份回执与可选 `--read-plan` 计划分母，确定性输出计划分母 × 客户端覆盖矩阵、全端共同盲区（`shared_blind_spots`）以及失败或超时项（不从分母剔除）。多端同位发现保留各端原文并检出严重度或极性分歧（`severity_divergent`），不将同位置结论直接合并为共识。结果同步生成 `r6_source_check_list` 原文件精确行号清单，落盘至 `<plan_dir>/reconcile/<run_id>_cross_check.md`。
- **`reconcile`（双入口确定性机械对账表）**：支持用户点名运行，也在重负载派单完成时由 `record_heavy_completion_for_stop_hook` 自动触发，校验目标非符号链接后落盘至 `<plan_dir>/reconcile/<run_id>_reconcile.md`，不写入业务仓库目录。改动范围按“开工冻结基线或 `--baseline-file` → `--base-ref` → 回执 `changed_files` → `git diff HEAD`”依次定界，隔离任务前已有未提交改动的文件，无基线时标为 `unknown`。依赖分析仅将 Python AST 导入、Markdown 显式链接与反引号精确路径或已变顶层符号列为 `pending_review`（首屏前 10 项），普通提及仅记入 `weak_mention_docs` 计数以降低噪声，同时结算读写义务并支持 `--cross-check` 并轨。

### 3. 单分片安全输入预算公式（`_execution_budget`）

模型声明窗口可能与底层原生窗口或自动压缩上限（如 `client_catalog_rules.claude.auto_compact_window`）不同，`subscription_matrix.models` 按客户端实际生效的活跃窗口记录。

设 `input_budget_safety_ratio` 为安全系数 $\alpha$ ， $W_{\text{selec}\text{ted}}$ 为声明窗口， $p_{\text{eff}}$ 为本地目录有效窗口百分比（缺省 `100`）， $W_{\text{compact}}$ 为适用的自动压缩上限，单分片安全输入预算按下式计算：

$$
W_{\text{eff}} = \left\lfloor W_{\text{selec}\text{ted}} \times \frac{p_{\text{eff}}}{100} \right\rfloor, \quad C_{\text{plan}} = \min(W_{\text{eff}}, \space W_{\text{compact}}), \quad B_{\text{input}} = \left\lfloor \alpha C_{\text{plan}} \right\rfloor
$$

未声明整数窗口的模型回退使用 `unlisted_window_fallback_tokens`。`plan` 复用同一阈值：超声明窗口报 `over_context_window` 并给 `suggested_shards`（`--allow-over-budget` 可放行），超安全预算或内联上限则在明细与确认条预警。

---

## 五、上游维护、体检建议、脱敏、本地路由与精确卸载

### 1. 上游维护（`fleet-evolve`）与 `--audit-upgrade` 建议码

在上游配置仓库目录中，`fleet-evolve` 提供三种维护模式：`full`（四维盘点与联网能力更新）、`hygiene`（缺陷修复、机械下沉与文档整理）、`audit`（零修改巡检）。以下四类关键变更节点须先经用户确认：调整推荐矩阵与 `runtime_tunables`（先执行 `fleet_state.py propose`）、写入宿主机全局目录（先执行 `install.py --dry-run --yes`）、跨版本 ABI 强耦合升级、执行 `git push`。

涉及主会话重启更新时，使用 `record-pass1`、`verify-pass2` 和 `clear --verified --readback-done` 归位；终审默认校验文档事实锚定，并对未过期活跃提案、活跃 `AWAITING_MASTER_RELOAD` Pass 2 重启锁及损坏状态文件拒绝清除（已认领会话可传 `--session-id` 归位，其余须显式 `--force`）。

运行 `install.py --audit-upgrade` 返回 `summary.all_aligned: false` 时，按 `summary.recommended_actions` 处理：

- `refresh_codex_model_catalog`：登录一次客户端生成本地模型目录缓存；
- `sync_codex_model_defaults` 或 `sync_claude_model_defaults`：需同步推荐默认模型与强度时，加 `--sync-model-defaults` 重跑 `install.py`；
- `review_codex_hook_trust`：Codex 已启用钩子信任记录但部分受管槽位缺失，或 `hooks.json` 存在漂移（只读体检按槽位覆盖度报告 `slots_recorded` 且不复算宿主内部哈希），请在 Codex 交互终端执行 `/hooks` 复核并重新批准；
- `start_cc_router`：按需启动本地 `router.py`；
- `upgrade_dsh_hook_protocol`：官方 DSH CLI 已开放硬钩子，可纳入上游演进；
- 若 `cross_side_status.dual_side_sync_required` 为 `true`，请在 Windows 与 WSL2 两侧分别同步。

### 2. 自定义尾段保留与全链路自动脱敏

- **自定义规则保留**：Antigravity 安装于独立插件目录 `plugins/user-guard`，不修改 `~/.gemini/GEMINI.md`；其余三端更新至 `## 工具与运行环境`（含 `R10.`、`R11.`），自动保留其后任意二级自定义章节（兼容标题不同的 `## 工具与...` 段），冲突编号自动迁移为 `L1.`、`L2.`。
- **全链路自动脱敏**：`install.py --audit-upgrade` 与 `cae_harness.py`（`--output` 默认脱敏，`--redact` 脱敏标准输出）自动擦除用户主目录名、私网 IPv4 与 IPv6、API Key、带凭据或查询参数的 URL 及精确费用字段。常见令牌前缀与格式（`Bearer`、`Basic`、`sk-`、`ghp_`、`AIza`、`ya29.`、JWT、`AKIA`、`ASIA`、`hf_`、`glpat-`、PEM）同样纳入自动擦除范围。`router.py` 与 `user_words.py` 写日志前同样脱敏且不记录正文。

### 3. 本地回环路由与 CC Switch 安全切卡

Claude Code 默认同步 `~/.claude/hooks/ccswitch_admin.py`，传 `--with-cc-router` 时另同步 `~/.claude/router/`。常用命令如下（Windows 用 `python -B -X utf8` 与反斜杠，WSL2 与 Linux 改用 `python3 -B -X utf8` 与 `/`；两侧均保留双引号内的 `$HOME`）：

```powershell
python -B -X utf8 "$HOME\.claude\hooks\ccswitch_admin.py" status
python -B -X utf8 "$HOME\.claude\router\router.py" --config "$HOME\.claude\router\router.json"
python -B -X utf8 "$HOME\.claude\hooks\ccswitch_admin.py" switch-card "<目标卡片名称>" --dry-run
python -B -X utf8 "$HOME\.claude\hooks\ccswitch_admin.py" switch-card "<目标卡片名称>" --require-router-alive
```

切换直连卡片可省略 `--require-router-alive`；真实切卡或清理 CC Switch 旧记录前，由 `ccswitch_admin.py` 自动生成 `.bak-before-*` 本地备份。

### 4. 交接模式切换与按文件名精确卸载

- **单独关闭 Codex 或 Claude Code 长会话交接门禁**（保留其余三类钩子）：
  ```powershell
  python -B -X utf8 install.py --app codex --codex-handoff-mode off
  python -B -X utf8 install.py --app claude --claude-handoff-mode off
  ```
- **按托管文件名精确卸载**（`install.py` 采用原地幂等同步且不留 `.bak-*` 文件；卸载时请勿删除整个 `hooks/` 目录，以免误删自建脚本）：
  1. **Antigravity**：删除 `~/.gemini/config/plugins/user-guard` 与 `~/.gemini/antigravity/plugin_data/user-guard`。
  2. **Codex 与 Claude Code 托管钩子**：仅删除 `hooks/` 下托管脚本 `user_words.py`、`session_context_loader.py`、`status_guard.py`、`reply_repeat_guard.py`、`handoff_gate.py`（Claude 含 `ccswitch_admin.py`）、`hooks/_lib/` 及不再使用的 `hooks/state/`、`hooks/logs/`。
  3. **代理、技能与可选路由**：删除 `~/.codex/agents/` 与 `~/.claude/agents/` 下的 `readonly-*`，三端 `skills/` 下的 `cae-harness`、`cae-writing`、`cae-axioms` 与 `fleet-evolve`，以及可选的 `~/.claude/router/`。
  4. **宿主配置与规则文件**：从 `~/.codex/hooks.json` 与 `~/.claude/settings.json` 移除托管钩子项；三端顶层规则文件若无个人内容可直接删除，若有自定义尾段则仅删去标准 `R0–R11` 至 `## 工具与运行环境` 章节。

---

## 六、按需公理审查引擎与纯核 Lean 4 校验架构（`cae-axioms`）

[cae-axioms](../template/user-guard/skills/cae-axioms/SKILL.md) 完全复用本仓库零外部运行时依赖的工程基线，不挂载常驻会话钩子，也不在 `axiom_gate.py` 内重复实现模型客户端或调度器；所有模块同步纳入 `install.py` 四端镜像分发与 `fleet_state.py` 核心完整性校验（`core_sha256`）。

![按需公理审查与硬状态机架构](assets/axiom-machine-architecture.svg)

### 1. 模块边界与条款级单向无环法典

- **条款级单向无环图（DAG）**：四份公理分册严守 `第一层（零.1、零.3、一节、五.1）-> 层 1.5`、`第一层 + 层 1.5（一至二节）+ 2A·D1–D2 -> 层 2M -> 2A 审查规范`，`2B` 与 `2C` 跨档通用；`2A·D1–D3` 不依赖层 `2M`，从源头消除跨层回环。所有经典文献依据与第三层外部仓库隔离声明独立汇总于 [references/layer-citations.md](../template/user-guard/skills/cae-axioms/references/layer-citations.md)，不作为诉诸权威的前提混入公理正文。
- **薄入口与标准库内部包 `_axiom_lib/`**：[axiom_gate.py](../template/user-guard/skills/cae-axioms/scripts/axiom_gate.py) 先执行目录与模块非符号链接/重解析点校验，再装载 `_axiom_lib/{contract,lean_gate,evidence,rules,case,replay}.py` 与受控单文件真内核驱动 `KernelAudit.lean`，保证 `advance`、`export-packet`、`verify-replay` 与 `report` 共用单一 Schema 与判定核。

### 2. 状态机安全不变量、双哈希失活与净室 `packet_replay`

- **单源严格 Schema 与前驱哈希链**：拒绝未知键、重复 JSON 键、布尔冒充整数、字符串真值强转与非标准数值；每次推进均由前驱记录重算 `stage_digest` 链与 `immutable_conflict_history` 哈希链并持跨平台文件锁（`msvcrt.locking` / `fcntl.flock`）写盘，防止跳步、并发覆写与双写中断锁死。
- **双哈希精准失活**：对所有锚定文件同时记录 `raw_sha256` 与严格 `\r\n -> \n` 归一化的 `normalized_sha256`（脚本、BOM 文件、`.olean` 及探针目标强制按 `raw_sha256` 判漂移）。第 $k$ 关锚定源码漂移仅精准将第 $k$ 关及下游（含 `replay_certificate`）置为 `STALE_INVALIDATED`，保留前序有效前缀；根冲突吸收态 `HALT_ROOT_CONFLICT` 绝不因文件漂移自动解锁。
- **净室复演与零自动命令执行边界**：`export-packet` 按 Schema 剥离主会话自评布尔与诱导标题，打包完整源码 `blobs` 与基线机械 diff。`verify-replay` 整单升格时交叉验签 `cae_harness.py` 的 `APPROVED` 票据、机械对账账本、零改动净室回执与逐关盲推断言，拒绝 `timeout_salvage`、`ast_only` 与无明确退出码的离线自报探针，且**绝不自动执行复演包内任何命令**（实跑探针须由用户显式传 `--allow-probe-exec` 现场重跑本地案卷已锁定命令）。

### 3. 纯核 Lean 4 反空真门禁与真内核重放

- **静态预检硬短路**：先剥离注释后逐词法拦截 `sorry`、`sorryAx`、`admit`、`axiom`、`unsafe`、`partial`、`opaque`、`implemented_by`、`extern`、`csimp`、`initialize`、`run_cmd`、`native_decide` 与非法 `set_option`（仅独占行放行 `set_option autoImplicit false`）。同时核验零前提构造性 `Nonempty` 见证。静态预检任一违规即刻短路，绝不启动外部 `lean` 编译器。
- **受控真内核编译与重放（`--run-lean` / `--wsl-lean`）**：锁定 `KernelAudit.lean` 自身 SHA-256，在洁净临时目录与隔离 `LEAN_PATH` 中经 `Parser.parseHeader` 校验导入白名单，现场编译 `.olean` 并禁用初始化器加载后执行 `Kernel.Environment.replay`。驱动逐常量核查无 `unsafe` 或编译器逃逸属性、全量 `collectAxioms` 闭包属于 `{propext, Classical.choice, Quot.sound}`，以及 `LogicCore.lean` 40 条契约定理黄金类型签名与 `_root_.Nonempty` 精确常量。四份纯核骨架均零 Mathlib、零 Lake，Windows 与 WSL2 双端 `.olean` 可独立编译校验且不污染源码目录。
