# 架构设计、共有生命周期钩子与上下文容量边界说明

本文面向维护本仓库、排查门禁行为或审阅底层安全机制的工程师，说明常驻机制层与动态配置层的模块分工、双门可用性与 `runtime_tunables` 参数整定、**共有会话生命周期门禁在四个客户端上的协议适配**、压缩后双块恢复与交接接力原理、零黑名单文档事实锚定（`check-grounding`），以及单分片上下文预算公式、本地路由切卡与卸载还原方式。日常安装与快速上手见 [仓库总说明](../README.md)，算例实战与命令闭环见 [日常工作流实战指南](workflow-guide.md)。

![四客户端共有机制与全通道派工架构](assets/architecture-overview.svg)

---

## 一、模块职责映射与双工作区隔离

全仓仅依赖 Python 3.11+ 标准库。所有会话钩子均为宿主事件触发的短生命周期子进程（经标准输入输出交换 JSON 后立即退出，平时零后台常驻）；仅可选启用的 Claude Code 本地协议分流器 `router.py` 为绑定回环地址 `127.0.0.1` 的轻量本地转发进程（健康检查接口不代表上游服务端余额查询或账号认证已完成）。

| 模块与路径 | 适用工作区 | 核心职责与确定性安全边界 |
| --- | --- | --- |
| [rules/AGENTS.md](../template/user-guard/rules/AGENTS.md) | 通用 + 上游 | `R0–R11` 全局工程规则唯一模板；零时期性模型名与散落阈值，安装时保留完整标准章节与末尾用户自定义尾段 |
| [fleet_profile.json](../template/user-guard/skills/cae-harness/fleet_profile.json) | 通用 + 上游 | 统一维护五个动态角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）、`runtime_tunables`、`subscription_matrix` 与 `capability_ledger` |
| [cae_harness.py](../template/user-guard/skills/cae-harness/scripts/cae_harness.py) | 通用 + 上游 | 识别 `workspace_mode` 与调用方客户端；执行 P0 点名锁定与五角色选路、第二道门离线可用性与额度复查、14 项确认票据、`batch`/`gate`/`audit`/`task` 无头执行、进程树清理及 `check-grounding` 事实锚定 |
| [user_words.py](../template/user-guard/hooks/user_words.py) | 通用 + 上游 | 五个钩子共用的基础模块：跨平台文件锁（`msvcrt` / `fcntl`）、重解析点防御原子写入、`runtime_tunables` 缓存读取、三端轨迹原话与实质子代理摘要提取、未决票据与完工账单存取 |
| [session_context_loader.py](../template/user-guard/hooks/session_context_loader.py) | 通用 + 上游 | 压缩或恢复入口检测：普通回合零字节注入；压缩恢复时分块注入“历轮原话 + 实质子代理摘要”与“磁盘未决重负载确认单” |
| [status_guard.py](../template/user-guard/hooks/status_guard.py) | 通用 + 上游 | 工具执行前门禁：静态拆解文件工具、多层 Shell 包装、重定向与内联 Python AST 拦截项目外写删及外部 `git worktree`；拦截未变化日志重复读取；拦截裸无头 CLI 并核验有效 `APPROVED` 票据 |
| [reply_repeat_guard.py](../template/user-guard/hooks/reply_repeat_guard.py) | 通用 + 上游 | 回合结束门禁：拦截连续无新数值进展的短句等待，并在重负载完工后静态核验成败词、Token 用量说明及流/回执/轨迹词项 |
| [handoff_gate.py](../template/user-guard/hooks/handoff_gate.py) | 通用 + 上游 | 长会话分级交接门禁：累计压缩次数、轨迹体积与读图字节数，在强制收尾（`drain` / `freeze`）阶段限制新派工并核验【新对话提示词】与待选工作流完整性 |
| [install.py](../install.py)、`install.ps1`、`install.sh` | 上游专属 | 探测客户端与 Python 3.11+ 解释器，校验非符号链接/重解析点后幂等分发四端配置；提供零写入配置漂移、`subscription_candidates` 与科学计算包三级 ABI 耦合审计（`--audit-upgrade`） |
| [fleet_state.py](../template/user-guard/skills/fleet-evolve/scripts/fleet_state.py) | 上游专属 | 核验 `upstream_config_repo` 身份；维护提案审批锁（`propose` / `approve`）、主会话重启重入锁（`record-pass1` / `verify-pass2`）及终审 `clear --verified --readback-done` 事实锚定硬门禁 |
| [router.py](../template/claude/router/router.py)、[ccswitch_admin.py](../template/claude/router/ccswitch_admin.py) | 通用 + 上游 | 本地 `/v1/messages` 前缀透明分流（保留显式非高挡 `effort`、支持 `chunked` 请求体并在上游 `401`/`403` 时失效密钥缓存）与 CC Switch SQLite 在线热备份安全切卡 |

`workspace_mode` 根据工作区或父目录是否同时包含 `install.py` 与 `template/user-guard/plugin.json` 区分 `upstream_config_repo` 与 `general_project`。已安装到独立技能目录的 `fleet_state.py` 在通用课题目录未指定 `--repo` 调用时直接返回 `not_upstream_config_repo`（退出码 `2`）；直接运行上游仓内脚本时还会沿脚本自身路径识别上游仓库。

---

## 二、双门可用性检查与 `runtime_tunables` 动态参数

1. **第一道门（独立 `fleet-evolve` 维护流程）**：结合官方资料与本机探测展示各客户端与模型的可用性及额度类型，由用户拍板决定启停，写入 `subscription_matrix.agents` 与 `subscription_matrix.models`（`enabled: false` 全局跳过），并在 `capability_ledger` 记录 `last_online_refresh_at` 与 `local_bench_verified_at`。
2. **第二道门（日常 `cae_harness.py plan` 离线复查）**：在生成确认单前纯离线读取本机程序、凭据配置、所需本地路由健康、本地会话记录中的滚动窗口已用百分比与重置时刻、CC Switch 卡片套餐/余额类型与日月限额，以及运行环境配额冷却文件，过滤不可用或超限通道。未暴露的服务端实时余额保持未知。

所有运行期定量阈值集中在 `fleet_profile.json` 的 `runtime_tunables` 下（支持 `CAE_FLEET_PROFILE` 测试隔离覆盖，并通过基线比对保留单元测试属性打桩优先级）：

| 配置分组 | 受控机制与核心键名 | 工程约束边界 |
| --- | --- | --- |
| `roles` | `orchestrator`、`math`、`code`、`review`、`fast` 候选列表、推荐挡位与短板隔离规则 | 五角色全动态，不与单一客户端永久绑定；用户明确点名按 P0 优先锁定 |
| `compaction_recovery` | `per_client.{antigravity,codex,claude}` 内联预算、`recent_subagent_window`、`min_substantive_subagent_chars`、`subagent_head_ratio` | 最新一条返回无条件保留，窗口内其余名额优先保留达到实质长度阈值且命中审查/推导角色的结论 |
| `handoff_thresholds` | 各客户端交接模式（`default_mode`）、压缩次数/轨迹体积/读图阈值与 `drain` 收尾预算 | Antigravity 默认启用分段收口；Codex 与 Claude Code 默认 `advisory` 提醒，是否强制阻断取决于实际启用模式 |
| `loop_guards` | `reply_repeat` 空等阈值、`status_guard` 大日志采样与 Shell 解包深度、`lifecycle_log` 滚动裁剪与陈旧 `.lock` 清理 | 仅拦截文件未变化时的同区间重复轮询与无新数值进展的重复等待短句 |
| `headless_dispatch` | `input_budget_safety_ratio`、`inline_readonly_max_kib`、`batch_max_workers_per_backend`、超时、重试轮数与路由默认端口 | 常驻文档引用键名，不在规则或脚本中散落静态死数字 |
| `dispatch_confirmation` | 轻重任务分界（流数、文件数、Token、超时）、票据有效期 `ticket_ttl_sec` 与滚动配额预警/熔断百分比 | 内部原生多路/可写子代理与外部无头调用统一受同一套阈值约束 |

---

## 三、全通道确认票据与共有生命周期门禁的四端适配

![会话确认、压缩接力与完成核验](assets/lifecycle-and-verification.svg)

### 1. 全通道 14 项确认单与 `PreToolUse` 票据门禁

`plan` 支持单流参数与 `--spec-file` 多流列表，统一覆盖 `cae_harness_headless`、`native_subagent`、`native_workflow`、`native_swarm` 四类通道。`_render_plan` 输出工作流背景、要做什么、预计后果、**4 列各路总览表**以及每路 **3 列纵向展开的 14 项完整参数表**：

| 序号 | 参数项 |
| --- | --- |
| 1 | 流号 |
| 2 | 子任务与范围 |
| 3 | 通道 |
| 4 | 客户端及子代理类型 |
| 5 | 模型名字（含原生 `inherit` 的本地配置解析结果） |
| 6 | 挡位 |
| 7 | 上下文窗口 |
| 8 | 是否无头 |
| 9 | 是否净室 |
| 10 | 访问模式、写入范围与验收命令 |
| 11 | 内联与超时 |
| 12 | 当前是否可用 |
| 13 | 本机配置额度类型与当前状态 |
| 14 | 回执或子代理日志位置 |

用户批准并运行 `plan --approve-ticket`（登记前脚本再次离线复查可用性与选路漂移）后，`status_guard.py` 在 `PreToolUse` 阶段完成三项派工检查：① 拦截绕过 `cae_harness.py` 的裸客户端无头命令（含环境变量前缀、`cmd /c`、`powershell -EncodedCommand`、`bash -lc`、`wsl`、`sudo`、`timeout` 等常见包装拆壳）；② 对外部重负载核验当前会话有效且未过期的 `APPROVED` 票据（支持显式 `--plan-dir` 及 `task` 省略 `--target` 时的工作目录规模估算）；③ 对客户端内部多路或可写原生子代理按回合调用快照核验票据中的原生通道容量。

### 2. 共有门禁机制与四客户端事件协议映射

本仓库以**共有生命周期机制**为核心，五个钩子脚本模块与宿主事件并非简单一一对应（其中 `user_words.py` 为底层共享库），各客户端按自身协议接入同一套状态与检查逻辑：

| 共有防护阶段 | 承担职责的共用脚本模块 | 核心确定性行为 |
| --- | --- | --- |
| **会话恢复与调用前** | `session_context_loader.py` + `handoff_gate.py` | 无新压缩时零字节注入；检测到压缩或恢复时分块还原“历轮原话 + 实质子代理摘要”与“磁盘未决重负载确认单”，并更新交接阶段 |
| **新指令提交时** | `handoff_gate.py`（Antigravity 在 `PreInvocation` 同步对账轨迹控制步） | 根据用户最新指令更新交接请求或显式暂缓宽限期（仅短而明确的用户指令生效，引用长文不误触） |
| **工具执行前** | `status_guard.py` + `handoff_gate.py` | 核验项目外写边界、日志重复轮询、反裸调、重负载批准票据，以及强制交接阶段（`drain`/`freeze`）的动作预算与新派工封锁（放行 `check-grounding --strict` 等只读收尾检查） |
| **回合结束时** | `reply_repeat_guard.py` + `handoff_gate.py` | 检查同回合连续空等短句、重负载完工硬报账（成败词、Token 说明、流标识与回执文件名或原生轨迹说明），并在强制交接阶段核验【新对话提示词】内嵌完整工作流与纵向 14 项参数表（兼容历史 14 列行） |

四个对等客户端的具体挂载与适配边界如下：

| 客户端 | 全局配置目录与宿主协议适配 |
| --- | --- |
| **Antigravity** (`agy`) | 同步至 `~/.gemini/config/plugins/user-guard`；原生插件 `hooks.json` 注册 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类事件，脚本内部联动 `handoff_gate.py`，运行态写入宿主 `PLUGIN_DATA`，遇到 `truncated_fields` 自动单遍回查 `transcript_full.jsonl` 还原截断正文后再做实质筛选 |
| **Codex** (`codex`) | 同步至 `~/.codex`；`hooks.json` 对称注册 `SessionStart`（匹配 `^(compact|resume)$`）、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件；安装器比对钩子实质内容一致时不重写文件以保留已有信任哈希，合并 `sessions/` 与 `archived_sessions/` 分段提取原话与 `spawn_agent`/`wait_agent` 完成返回 |
| **Claude Code** (`claude` / `cc`) | 同步至 `~/.claude`；将上述五类事件合并写入 `settings.json`，默认随钩子同步 `hooks/ccswitch_admin.py`（`--no-claude-hooks` 跳过），可选 `--with-cc-router` 分发 `router/`；无头只读任务固定传 `--permission-mode dontAsk` 避免 `auto` 分类器限流 |
| **DeepSeek Harness** (`dsh`) | 同步至 `~/.dsh/AGENTS.md` 与 `skills/`，原样保留 `.credentials.yaml`，当前 CLI 未提供同类交互式事件钩子接口；由 `cae_harness.py` 无头调用时自动探测 `--profile headless --help` 权限参数能力——新版 CLI 直接传 `--permission-mode` 与 `--approval-policy never` 且不在补丁中注入冲突的旧版预设键，旧版 CLI 回退完整补丁与 `DSH_PERMISSION_MODE`，统一执行单 JSON 平衡解码与尾随 `DSML` 标记清洗 |

---

## 四、压缩双块恢复、零黑名单事实锚定与单分片预算公式

### 1. 压缩恢复与跨会话票据接力不变量

- **实质子代理筛选与防二次截断**：在最近 `recent_subagent_window` 条已完成子代理记录中无条件保留最新一条，其余名额按实质字数阈值、审查/推导角色关键词与时间序择优；归档落盘与内联注入各自基于完整 `raw_body` 独立执行一次首尾截取（避免双重省略号），截断切中代码块时由 `_close_unbalanced_fence` 自动闭合围栏且不超预算。
- **未决工作流独立分区（零幻觉）**：`render_pending_workflow_block` 独立于常规原话字符预算之外直接读取磁盘票据（已存完整纵向确认单时不重复拼接，缺项时如实标明 `未记录`），确保压缩后待拍板的 14 项细节零丢失。`plan --resume-ticket` 在新会话离线重算可用性但保留原票据 `session_id` 不自动改绑，跨会话执行须重新生成绑定新会话的确认票据。

### 2. 零黑名单文档事实锚定（`check-grounding`）

`audit_doc_grounding` 不依赖人工维护违禁词表，而是自动扫描工作区源码、配置文件（`fleet_profile.json`、`.py`、`.json`、`.toml`、`.yaml`、C++/Fortran/Rust 源码等）、子代理契约与 `NOTICE`，构建标识符集合、命令行选项集合、定量数值及派生换算集合与中文双字技术词干集合，逐行拦截无源码依据的参数名/文件名、陌生全大写缩写、失步数字与虚构领域举例。`task --kind polish` 自动附带 `grounding_audit`，上游 `fleet_state.py clear --verified --readback-done` 在状态锁内强制执行事实锚定硬门禁（模块缺失或结果无效时拒绝归位；存在有效待批提案时默认拒绝清除，显式覆盖需传 `--force`）。

### 3. 单分片安全输入预算公式（`_execution_budget`）

模型声明的最大上下文窗口不同于客户端出厂默认窗口（例如 Codex 缓存中 `sol` 默认窗口为 `500k`、`astra` 默认窗口为 `272k`，而最大窗口为 `872k`），也不同于自动压缩触发阈值。设 `input_budget_safety_ratio` 为 $\alpha$（当前配置 $\alpha = 0.90$），`cae_harness.py` 按下式计算单分片安全输入预算：

$$W_{\text{eff}} = \left\lfloor W_{\text{selected}} \times \frac{p_{\text{eff}}}{100} \right\rfloor, \quad C_{\text{plan}} = \min\!\big(W_{\text{eff}},\, W_{\text{compact}}\big), \quad B_{\text{input}} = \left\lfloor \alpha \, C_{\text{plan}} \right\rfloor$$

以随附配置为例：Codex 为 $\lfloor \lfloor 872000 \times 0.95 \rfloor \times 0.90 \rfloor = 745,560$ token；Claude Code 为 $\lfloor \min(1048576, 1000000) \times 0.90 \rfloor = 900,000$ token；DSH 为 $\lfloor 1048576 \times 0.90 \rfloor = 943,718$ token；Antigravity 因 `agy models` 未输出整数窗口字段，采用配置的保守预算 `unlisted_window_fallback_tokens = 120,000` token（支持环境变量覆盖）。

---

## 五、规则同步细节、安全脱敏与卸载还原

1. **全局规则自定义尾段保留与撞号迁移**：Antigravity 以独立插件目录 `plugins/user-guard` 安装，不触碰用户自有的 `~/.gemini/GEMINI.md`；Codex、Claude Code 与 DSH 的顶层全局规则文件（`AGENTS.md` / `CLAUDE.md`）以完整标准模板更新，保留标准 `## 工具与运行环境`、`R10.` 与 `R11.`，同时自动识别并保留排在标准 `## 工具与运行环境` 之后的任意二级标题段落（或以 `## 工具与` 开头且不同于 `## 工具与运行环境` 的自定义环境段落），将其内部冲突的 `R10.`、`R11.` 改标为 `L1.`、`L2.`，且 `--audit-upgrade` 使用同一边界判等不误报漂移。
2. **运行期与静态全链路脱敏**：`install.py --audit-upgrade` 与 `cae_harness.py`（`--output` 默认脱敏、`--redact` 标准输出与异常出口脱敏）自动清洗 Windows/WSL/Linux 真实用户名路径、私网 IPv4/IPv6、API Key / Bearer / `sk-` / `ghp_` / `AIza` / `AKIA` / `hf_` / `glpat-` 令牌、URL 凭据与精确费用字段（`install.py --audit-upgrade` 同时脱敏自定义卡片 ID，而 `ccswitch_admin.py` 面向本机管理员直接查看与切换本地卡片状态）；`router.py` 与 `user_words.py` 写入本地诊断日志前亦自动擦除令牌、密钥键值与 URL 凭据。
3. **交接模式切换与干净卸载**：
   - 如需调整或关闭 Codex / Claude Code 的长会话交接钩子模式，可分别运行 `python -B -X utf8 install.py --app codex --codex-handoff-mode off` 或 `python -B -X utf8 install.py --app claude --claude-handoff-mode off`（此时保留原话恢复、工具前门禁与完工报账钩子，仅移除 `handoff_gate.py` 注册项）。
   - 如需完整卸载本工具，删除 `~/.gemini/config/plugins/user-guard` 插件目录及 `~/.codex`、`~/.claude`、`~/.dsh` 下托管的 `hooks/`、`agents/readonly-*`、`skills/cae-harness`、`skills/fleet-evolve`（及可选 `router/`），并移除 `~/.codex/hooks.json` 与 `~/.claude/settings.json` 中的托管钩子条目，或直接使用安装与切卡时生成的本地 `.bak-*` 备份文件还原。
