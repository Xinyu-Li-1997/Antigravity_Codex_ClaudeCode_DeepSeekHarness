# 架构设计、共有生命周期钩子与上下文容量边界说明

本文面向维护本仓库、排查门禁行为或审阅底层安全机制的工程师，说明常驻机制层与动态配置层的模块分工、两阶段可用性检查与 `fleet_profile.json` 配置键、**共有会话生命周期门禁在四个客户端上的协议适配**、压缩后双块恢复与精简票据交接接力原理、零黑名单文档事实锚定（`check-grounding`），以及单分片上下文预算公式、本地路由切卡与清理卸载方式。日常安装与快速上手见 [仓库总说明](../README.md)，算例实战与命令闭环见 [日常工作流实战指南](workflow-guide.md)。

![固定机制层、动态配置层与全通道派工架构](assets/architecture-overview.svg)

---

## 一、模块职责映射与双工作区隔离

全仓仅依赖 Python 3.11+ 标准库。所有会话钩子均为宿主事件触发的短生命周期子进程（经标准输入输出交换 JSON 后立即退出，平时零后台常驻）；仅可选启用的 Claude Code 本地协议分流器 `router.py` 为绑定回环地址（`127.0.0.1`、`::1` 或 `localhost`）的轻量本地转发进程（健康检查接口不代表上游服务端余额查询或账号认证已完成）。

| 模块与路径 | 适用工作区 | 核心职责与确定性安全边界 |
| --- | --- | --- |
| [rules/AGENTS.md](../template/user-guard/rules/AGENTS.md) | 通用 + 上游 | `R0–R11` 全局工程规则唯一模板；零时期性模型名与散落阈值，安装时保留完整标准章节与末尾用户自定义尾段 |
| [fleet_profile.json](../template/user-guard/skills/cae-harness/fleet_profile.json) | 通用 + 上游 | 统一维护顶层键 `roles`（五类动态角色槽位）、`native_agent_profiles`（四端独立内生代理档案）、`runtime_tunables`（运行期定量阈值）、`subscription_matrix`（启停矩阵）与 `capability_ledger`（离线性能更新时间） |
| [cae_harness.py](../template/user-guard/skills/cae-harness/scripts/cae_harness.py) | 通用 + 上游 | 识别 `workspace_mode` 与调用方客户端；执行 P0 点名锁定、五角色选路、`--isolation` 四级正交隔离、派工前离线可用性与额度复查、`<ticket_id>_card.md` 落盘与精简总览卡、`batch`、`gate`、`audit`、`task` 无头执行、进程树清理及 `check-grounding` 事实锚定 |
| [user_words.py](../template/user-guard/hooks/user_words.py) | 通用 + 上游 | 五个钩子共用的基础模块（底层委托 `hooks/_lib/`）：跨平台文件锁（`msvcrt` / `fcntl`）、重解析点防御原子写入、`runtime_tunables` 与自适应策略读取、三端轨迹原话与实质子代理摘要提取、历史确认单折叠（`collapse_historical_cards`）及未决票据与完成回执记录存取 |
| `hooks/_lib/`（`fs_safety.py`、`shell_lex.py`、`adaptive_policy.py`） | 通用 + 上游 | 共享底层安全锁与原子写（`fs_safety.py`）、Shell 词法拆解与引号解码（`shell_lex.py`），以及基于 `fleet_profile.json` 按 `(client, model, effort)` 动态派生压缩恢复预算、交接阈值与代理卡默认参数的自适应策略（`adaptive_policy.py`） |
| [session_context_loader.py](../template/user-guard/hooks/session_context_loader.py) | 通用 + 上游 | 压缩或恢复入口检测：普通回合零字节注入；压缩恢复时分块注入“历轮原话 + 实质子代理摘要”与“磁盘未决确认单精简总览卡及明细链接” |
| [status_guard.py](../template/user-guard/hooks/status_guard.py) | 通用 + 上游 | 工具执行前门禁：静态拆解文件工具、多层 Shell 包装、重定向与内联 Python AST 拦截项目外写删及外部 `git worktree`；拦截未变化日志重复读取；拦截绕过调度器直接调用无头 CLI，核验有效 `APPROVED` 票据、隔离一致性与内部原生通道容量（含 `swarm add` 按 `tasks` 长度或 `n` 精确计费） |
| [reply_repeat_guard.py](../template/user-guard/hooks/reply_repeat_guard.py) | 通用 + 上游 | 回合结束门禁：拦截连续无新数值进展的短句等待，并在重负载完工后静态核验成败词、Token 用量说明，以及流标识、回执文件名或轨迹词项（含 `continuationActive` 静默销账对账） |
| [handoff_gate.py](../template/user-guard/hooks/handoff_gate.py) | 通用 + 上游 | 长会话分级交接门禁：累计压缩次数、轨迹体积与读图字节数，在强制收尾（`drain` / `freeze`）阶段限制新派工并核验【新对话提示词】内嵌精简总览卡、`<ticket_id>_card.md` 链接与 `plan --resume-ticket` 命令 |
| [install.py](../install.py)、`install.ps1`、`install.sh` | 上游专属 | 探测客户端与 Python 3.11+ 解释器，校验非符号链接/重解析点后幂等分发四端配置；提供零写入配置漂移、`cross_side_status` 双端同步体检、DSH `hook_protocol` 能力探测、`subscription_candidates`、`fleet_profile.native_agent_profiles_configured` 与科学计算包三级 ABI 耦合审计（`--audit-upgrade`） |
| [fleet_state.py](../template/user-guard/skills/fleet-evolve/scripts/fleet_state.py) | 上游专属 | 核验 `upstream_config_repo` 身份；维护提案审批锁（`propose` / `approve`）、主会话重启重入锁（`record-pass1` / `verify-pass2`）及终审 `clear --verified --readback-done [--session-id <sid>]` 事实锚定强制校验 |
| [router.py](../template/claude/router/router.py)、[ccswitch_admin.py](../template/claude/router/ccswitch_admin.py) | 通用 + 上游 | 严格回环绑定的 `/v1/messages` 前缀透明分流（校验请求目标路径与上游 URL、保留显式非高挡 `effort`、支持 `chunked` 请求体并在上游 `401`/`403` 时失效密钥缓存）与 CC Switch SQLite 在线热备份安全切卡 |

`workspace_mode` 根据工作区或父目录是否同时包含 `install.py` 与 `template/user-guard/plugin.json` 区分 `upstream_config_repo` 与 `general_project`。已安装到独立技能目录的 `fleet_state.py` 在通用课题目录未指定 `--repo` 调用时直接返回 `not_upstream_config_repo`（退出码 `2`）；直接运行上游仓内脚本时还会沿脚本自身路径识别上游仓库。

---

## 二、两阶段可用性检查与 `fleet_profile.json` 动态配置结构

1. **维护期联网核查与用户启停确认**（独立 `fleet-evolve` 维护流程）：结合官方资料与本机探测展示各客户端与模型的可用性及额度类型，由用户确认决定启停，写入 `subscription_matrix.agents` 与 `subscription_matrix.models`（`enabled: false` 全局跳过），并在 `capability_ledger` 记录离线性能更新时间（`last_online_refresh_at` 与 `local_bench_verified_at`）。
2. **派工前纯离线可用性复查**（日常 `cae_harness.py plan`）：在生成确认单前纯离线读取本机程序、凭据配置、所需本地路由健康、本地会话记录中的滚动窗口已用百分比与重置时刻、CC Switch 卡片套餐/余额类型与日月限额，以及运行环境配额冷却文件，过滤不可用或超限通道。未暴露的服务端实时余额保持未知。

`fleet_profile.json` 将顶层能力档案与运行期阈值清晰分层（支持 `CAE_FLEET_PROFILE` 测试隔离覆盖，并通过基线比对保留单元测试运行时常量覆盖优先级）：

### 1. 顶层能力档案与启停矩阵

| 顶层键名 | 核心字段与职责 | 工程约束边界 |
| --- | --- | --- |
| `roles` | `orchestrator`、`math`、`code`、`review`、`fast` 候选列表、推荐挡位与短板隔离规则 | 五角色全动态，不与单一客户端永久绑定；用户明确点名按 P0 优先锁定 |
| `native_agent_profiles` | `antigravity`、`codex`、`claude`、`dsh` 各自的主模型策略、原生子代理机制（如 `invoke_subagent`、`swarm`、`spawn_agent`、`Agent`）、只读代理列表与钩子协议 | 独立记录各客户端内生代理能力，与外部无头调度解耦；`--audit-upgrade` 上报 `fleet_profile.native_agent_profiles_configured` |
| `subscription_matrix` / `capability_ledger` | 各客户端与模型的启停开关、额度类型及离线性能更新时间（`last_online_refresh_at`、`local_bench_verified_at`） | `enabled: false` 全局跳过；日常派工仅离线读取时间戳，不联网查榜单 |

### 2. 运行期定量阈值（`runtime_tunables` 子分组）

| `runtime_tunables` 分组 | 受控机制与核心键名 | 工程约束边界 |
| --- | --- | --- |
| `compaction_recovery` | `per_client.{antigravity,codex,claude}` 内联预算、`recent_subagent_window`、`min_substantive_subagent_chars`、`subagent_head_ratio` | 最新一条返回无条件保留，窗口内其余名额优先保留达到实质长度阈值且命中审查/推导角色的结论，并由 `adaptive_policy.py` 按当前模型窗口与推理强度动态缩放预算 |
| `handoff_thresholds` | 各客户端交接模式（`default_mode`），压缩次数、轨迹体积与读图阈值，以及 `drain` 收尾预算 | Antigravity 默认启用分阶段交接限制；Codex 与 Claude Code 默认 `advisory` 提醒，是否强制阻断取决于实际启用模式 |
| `loop_guards` | `reply_repeat` 重复等待回复阈值、`status_guard` 大日志采样与 Shell 解包深度、`lifecycle_log` 滚动裁剪与陈旧 `.lock` 清理 | 仅拦截文件未变化时的同区间重复轮询与无新数值进展的重复等待短句 |
| `headless_dispatch` | `input_budget_safety_ratio`、`inline_readonly_max_kib`、`batch_max_workers_per_backend`、超时、重试轮数与路由默认端口 | 常驻文档引用键名，不在规则或脚本中散落硬编码数值常量 |
| `dispatch_confirmation` | 轻重任务分界（流数、文件数、Token、超时）、票据有效期 `ticket_ttl_sec` 与滚动配额熔断百分比（`quota_block_percent`） | 内部原生多路/可写子代理与外部无头调用统一受同一套阈值约束 |

---

## 三、全通道确认票据与共有生命周期门禁的四端适配

![会话确认、压缩接力与完成核验流程](assets/lifecycle-and-verification.svg)

### 1. 全通道 14 项明细落盘、精简总览卡与 `PreToolUse` 票据门禁

`plan` 支持单流参数与 `--spec-file` 多流列表，统一覆盖 `cae_harness_headless`、`native_subagent`、`native_workflow`、`native_swarm` 四类通道，并支持 `--isolation` 四级正交隔离（`full`、`no_skills`、`hooks_only`、`clean_room`，`--clean-room` 为兼容别名）。`_save_plan` 先原子写入 `<ticket_id>.json`，再将含背景、要做什么、预计后果、4 列总览表及分路 3 列纵向 14 项完整参数明细（涵盖流号、范围、通道、子代理与实际模型解析、挡位、窗口、无头与隔离状态、读写及验收边界、内联与超时、可用性、额度状态及回执位置；逐项定义见 [日常实战指南](workflow-guide.md) 与 [cae-harness 技能说明](../template/user-guard/skills/cae-harness/SKILL.md)）的 Markdown 写入 `<ticket_id>_card.md`，同时生成带可点击 `[<ticket_id>_card.md](file:///...)` 链接的 `compact_summary_card` 与 `compact_handoff_prompt_block`——对话与恢复默认仅展示精简 4 列总览卡与可点击明细链接，不展开多路 14 项长表。

用户批准并运行 `plan --approve-ticket`（登记前脚本再次离线复查可用性与选路漂移）后，`status_guard.py` 在 `PreToolUse` 阶段完成四项派工检查：① 拦截绕过 `cae_harness.py` 直接调用客户端无头 CLI 的命令（含环境变量前缀、`cmd /c`、`powershell -EncodedCommand` 及最短唯一前缀缩写、`bash -lc`、`wsl`、`sudo`、`timeout` 等常见包装拆壳）；② 对外部重负载核验当前会话有效且未过期的 `APPROVED` 票据（含显式 `--plan-dir`、`--isolation` 一致性、`--write-scope` 子集及可写流 `--verify-cmd` 双向严格一致性校验）；③ 对客户端内部多路或可写原生子代理按回合调用快照核验票据中的原生通道容量（`_native_call_stream_weight` 对 `swarm add` 按 `tasks` 列表长度或正整数 `n` 精确计入流数）；④ `task` 省略 `--target` 时按当前工作目录估算规模阈值。

### 2. 共有门禁机制与四客户端事件协议映射

本仓库以**共有生命周期机制**为核心，五个钩子脚本模块与宿主事件并非简单一一对应（其中 `user_words.py` 为底层共享库），各客户端按自身协议接入同一套状态与检查逻辑：

| 共有防护阶段 | 承担职责的共用脚本模块 | 核心确定性行为 |
| --- | --- | --- |
| **会话恢复与调用前** | `session_context_loader.py` + `handoff_gate.py` | 无新压缩时零字节注入；检测到压缩或恢复时分块还原“历轮原话 + 实质子代理摘要”与“磁盘未决票据精简总览卡及可点击 `<ticket_id>_card.md` 链接”，并更新交接阶段 |
| **新指令提交时** | `handoff_gate.py`（Antigravity 在 `PreInvocation` 同步对账轨迹控制步） | 根据用户最新指令更新交接请求或显式暂缓宽限期（仅短而明确的用户指令生效，引用长文不误触） |
| **工具执行前** | `status_guard.py` + `handoff_gate.py` | 核验项目外写边界、日志重复轮询、直接调用底层 CLI 拦截、重负载批准票据（含隔离一致性与 `swarm add` 精确流数计费），以及强制交接阶段（`drain`/`freeze`）的动作预算与新派工封锁（放行 `check-grounding --strict` 等只读收尾检查） |
| **回合结束时** | `reply_repeat_guard.py` + `handoff_gate.py` | 拦截同回合连续无实质进展的等待短句，校验重负载完成回执（成败词、Token 说明、流标识与回执文件名或原生轨迹说明），并在强制交接阶段由 `_matches_compact_card_handoff` 优先核验【新对话提示词】内嵌精简 4 列总览表、`<ticket_id>_card.md` 链接与 `plan --resume-ticket` 命令（无落盘明细文件时回退校验纵向 14 项参数表及历史 14 列行） |

四个对等客户端的具体挂载与适配边界如下：

| 客户端 | 全局配置目录与宿主协议适配 |
| --- | --- |
| **Antigravity** (`agy`) | 同步至 `~/.gemini/config/plugins/user-guard`；原生插件 `hooks.json` 注册 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类事件，脚本内部联动 `handoff_gate.py`，运行态写入宿主 `PLUGIN_DATA`，遇到 `truncated_fields` 自动单遍回查 `transcript_full.jsonl` 还原截断正文后再做实质筛选；`--isolation` 拒绝 `hooks_only` 与 `clean_room` |
| **Codex** (`codex`) | 同步至 `~/.codex`；`hooks.json` 对称注册 `SessionStart`（匹配 `^(compact|resume)$`）、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件；安装器比对钩子实质内容一致时不重写文件以保留已有信任哈希，合并 `sessions/` 与 `archived_sessions/` 分段提取原话与 `spawn_agent`/`wait_agent` 完成返回；`--isolation` 完整支持全部四档 |
| **Claude Code** (`claude` / `cc`) | 同步至 `~/.claude`；将上述五类事件合并写入 `settings.json`，默认随钩子同步 `hooks/ccswitch_admin.py`（`--no-claude-hooks` 跳过），可选 `--with-cc-router` 分发 `router/`；无头只读任务固定传 `--permission-mode dontAsk` 避免 `auto` 分类器限流，`--isolation clean_room` 同时启用 `--bare` 与 `--disable-slash-commands`（仅限只读盲读） |
| **DeepSeek Harness** (`dsh`) | 同步至 `~/.dsh/AGENTS.md` 与 `skills/`，原样保留 `.credentials.yaml`；当前官方 CLI 处于 `soft_rules_only`（无交互硬钩子），`install.py --audit-upgrade` 上报 `hook_protocol`、`hard_hooks_supported` 与 `meta_update_trigger` 以便上游开放硬钩子后触发元更新；由 `cae_harness.py` 无头调用时自动探测 `--profile headless --help` 权限参数能力（新版传 `--permission-mode` 与 `--approval-policy never`，旧版回退补丁与 `DSH_PERMISSION_MODE`），`--isolation` 拒绝 `hooks_only` 并支持 `clean_room`，统一执行单 JSON 平衡解码与尾随 `DSML` 清洗 |

---

## 四、压缩双块恢复、零黑名单事实锚定与单分片预算公式

### 1. 压缩恢复与跨会话票据接力不变量

- **实质子代理筛选与防二次截断**：在最近 `recent_subagent_window` 条已完成子代理记录中无条件保留最新一条，其余名额按实质字数阈值、审查/推导角色关键词与时间序择优；状态归档写入与内联注入各自基于完整 `raw_body` 独立执行一次首尾截取（避免双重省略号），截断切中代码块时由 `_close_unbalanced_fence` 自动闭合围栏且不超预算。
- **未决工作流独立分区与历史折叠（零幻觉）**：`collapse_historical_cards` 在压缩恢复时将历史 14 项明细表折叠为 `[已折叠历史确认单/账单明细]`；`render_pending_workflow_block` 独立于常规原话字符预算之外直接读取磁盘票据 `<ticket_id>.json`——当 `<ticket_id>_card.md` 已实际落盘且为常规非链接文件时，优先输出精简 4 列总览卡、可点击 `[<ticket_id>_card.md](file:///...)` 链接与三选项（明确要求勿在对话中展开 14 项长表），仅在未落盘明细文件时回退 14 项全字段表（缺项标明 `未记录`），并结合 `adaptive_policy.py` 按当前模型与推理强度动态缩放压缩与交接阈值。`plan --resume-ticket` 在新会话离线重算可用性但保留原票据 `session_id` 不自动改绑，跨会话执行须重新生成绑定新会话的确认票据。

### 2. 零黑名单文档事实锚定（`check-grounding`）

`audit_doc_grounding` 不依赖人工维护违禁词表，而是自动扫描工作区源码与配置文件（`fleet_profile.json`、`.py`、`.json`、`.toml`、`.yaml`、C++/Fortran/Rust 源码等）及 `NOTICE`——**刻意跳过 `tests/` 目录与 Markdown 子代理契约（`template/user-guard/agents/*.md`、`template/claude/agents/*.md`），对 Codex 子代理 `.toml` 仅提取键名，并将三端代理契约正文作为被审文档而非锚点白名单**——构建标识符集合、命令行选项集合、定量数值及派生换算集合与中文双字技术词干集合，逐行拦截无源码依据的参数名与文件名、陌生全大写缩写、失步数字与虚构领域举例。`task` 与 `batch` 的 `polish` 流均自动附带 `grounding_audit`，上游 `fleet_state.py clear --verified --readback-done` 在状态锁内强制执行事实锚定校验（模块缺失或结果无效时拒绝归位），且 `fleet_state.py clear` 对未过期活跃提案、活跃 `AWAITING_MASTER_RELOAD` Pass 2 重启锁及损坏状态文件默认拒绝清除（已认领的 Pass 2 会话可显式传 `--session-id <sid>` 归位，其余覆盖需显式加 `--force`）。

### 3. 单分片安全输入预算公式（`_execution_budget`）

模型声明的最大上下文窗口（由 `subscription_matrix.models` 或客户端本地模型目录缓存提供）可能高于客户端出厂默认窗口，也不同于自动压缩触发阈值（如 Claude 的 `client_catalog_rules.claude.auto_compact_window`）。设 `runtime_tunables.headless_dispatch.input_budget_safety_ratio` 为安全系数 $\alpha$，$W_{\text{selected}}$ 为当前模型声明窗口，$p_{\text{eff}}$ 为本地目录声明的有效窗口百分比（缺省按 `100` 计），$W_{\text{compact}}$ 为适用的自动压缩窗口上限，`cae_harness.py` 按下式动态计算单分片安全输入预算：

$$W_{\text{eff}} = \left\lfloor W_{\text{selected}} \times \frac{p_{\text{eff}}}{100} \right\rfloor, \quad C_{\text{plan}} = \min\!\big(W_{\text{eff}},\, W_{\text{compact}}\big), \quad B_{\text{input}} = \left\lfloor \alpha \, C_{\text{plan}} \right\rfloor$$

对于客户端命令未输出整数窗口且未在 `subscription_matrix.models` 声明窗口的模型，调度器按 `runtime_tunables.headless_dispatch.unlisted_window_fallback_tokens` 回退采用保守分片预算（支持环境变量覆盖）；具体窗口与预算数值随 `fleet_profile.json` 与本机目录动态重算，不在常驻文档中固化本机派生指纹。

---

## 五、规则同步细节、安全脱敏、本地路由切卡与清理卸载

1. **全局规则自定义尾段保留与冲突编号重命名**：Antigravity 以独立插件目录 `plugins/user-guard` 安装，不触碰用户自有的 `~/.gemini/GEMINI.md`；Codex、Claude Code 与 DSH 的顶层全局规则文件（`AGENTS.md` / `CLAUDE.md`）以完整标准模板更新，保留标准 `## 工具与运行环境`、`R10.` 与 `R11.`，同时自动识别并保留排在标准 `## 工具与运行环境` 之后的任意二级标题段落（跳过代码围栏，亦兼容以 `## 工具与` 开头且不同于 `## 工具与运行环境` 的自定义环境段落），将其内部冲突的 `R10.`、`R11.` 改标为 `L1.`、`L2.`，且 `--audit-upgrade` 使用同一边界判等不误报漂移。
2. **运行期与静态全链路脱敏**：`install.py --audit-upgrade` 与 `cae_harness.py`（`--output` 默认脱敏、`--redact` 标准输出与异常出口脱敏）均自动清洗 Windows、WSL 与 Linux 真实用户名路径，私网 IPv4（含共享内网段）与私网 IPv6，API Key、`Bearer`、`Basic`、`sk-`、`ghp_`、`AIza`、`ya29.`、JWT、`AKIA`、`ASIA`、`hf_`、`glpat-` 与 PEM 私钥等凭据，各类协议 URL（含 `http`、`https`、`ws`、`wss`、`socks4`、`socks5` 等）中的认证信息与查询参数，以及精确费用字段（`install.py --audit-upgrade` 同时脱敏自定义卡片 ID，而 `ccswitch_admin.py` 面向本机管理员直接查看与切换本地卡片状态）；`router.py` 与 `user_words.py` 写入本地诊断日志前亦自动擦除私钥、令牌、密钥键值与 URL 凭据。
3. **本地分流代理启动与 CC Switch 安全切卡**：Claude Code 默认随钩子同步 `~/.claude/hooks/ccswitch_admin.py`，显式传入 `--with-cc-router` 时另行同步 `~/.claude/router/`。日常查看卡片对齐状态、启动本地 `127.0.0.1` 分流服务或切换激活卡片的命令如下（Windows PowerShell 使用 `python -B -X utf8` 与反斜杠路径，WSL2 与 Linux Bash 改用 `python3 -B -X utf8` 并将 `\` 换为 `/`，两侧均保留双引号内的 `$HOME`）：
   ```powershell
   python -B -X utf8 "$HOME\.claude\hooks\ccswitch_admin.py" status
   python -B -X utf8 "$HOME\.claude\router\router.py" --config "$HOME\.claude\router\router.json"
   python -B -X utf8 "$HOME\.claude\hooks\ccswitch_admin.py" switch-card "<目标卡片名称>" --dry-run
   python -B -X utf8 "$HOME\.claude\hooks\ccswitch_admin.py" switch-card "<目标卡片名称>" --require-router-alive
   ```
   切换直连供应商卡片时可省略 `--require-router-alive`；执行真实切卡或调用 `--disable-ccswitch-prompts`、`--purge-ccswitch-legacy` 时，仅 `ccswitch_admin.py` 会在修改 `cc-switch.db` 或 `~/.claude/settings.json` 前生成 `.bak-before-*` 本地备份。
4. **交接模式切换与按托管文件名精确卸载**：
   - 如需调整或关闭 Codex / Claude Code 的长会话交接钩子模式，可分别运行 `python -B -X utf8 install.py --app codex --codex-handoff-mode off` 或 `python -B -X utf8 install.py --app claude --claude-handoff-mode off`（此时保留原话恢复、工具前门禁与完工回执核验钩子，仅移除 `handoff_gate.py` 注册项）。
   - `install.py` 采用原地幂等更新，不会为规则或钩子文件生成 `.bak-*` 备份（仅 `ccswitch_admin.py` 切卡或清理旧数据时生成 `.bak-before-*`）。如需完整卸载本工具，请按托管文件名精确清理，切勿直接整目录删除 `hooks/` 以免误删用户自建脚本：删除 `~/.gemini/config/plugins/user-guard` 插件目录与 `~/.gemini/antigravity/plugin_data/user-guard` 运行态目录；在 `~/.codex/hooks/` 与 `~/.claude/hooks/` 中仅删除托管脚本 `user_words.py`、`session_context_loader.py`、`status_guard.py`、`reply_repeat_guard.py`、`handoff_gate.py`（Claude 另含 `ccswitch_admin.py`）、共享基础库目录 `hooks/_lib/` 及不再使用的 `hooks/state/`、`hooks/logs/` 子目录；删除 `~/.codex/agents/` 与 `~/.claude/agents/` 下的 `readonly-auditor.*`、`readonly-collaborator.*`、`readonly-reader.*`，以及 `~/.codex/skills/`、`~/.claude/skills/`、`~/.dsh/skills/` 下的 `cae-harness`、`fleet-evolve`（和可选 `~/.claude/router/`）；从 `~/.codex/hooks.json` 与 `~/.claude/settings.json` 中移除托管钩子条目；最后对 `~/.codex/AGENTS.md`、`~/.claude/CLAUDE.md` 与 `~/.dsh/AGENTS.md` 三端顶层规则文件，若无自定义规则可直接删除，若有末尾自定义段落则仅删去标准 `R0–R11` 至 `## 工具与运行环境` 章节并保留自定义尾段。
