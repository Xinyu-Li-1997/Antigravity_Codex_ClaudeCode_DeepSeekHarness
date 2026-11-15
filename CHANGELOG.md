# 更新日志（Changelog）

记录 `Antigravity_Codex_ClaudeCode_DeepSeekHarness`（含 `user-guard` 全局插件、`cae-harness` 四客户端后台调用与验证工具及 `fleet-evolve` 升级整理流程）各版本的功能更新、客户端适配与缺陷修复。

---

## [1.14.2] - 2026-11-15

在双环境真机无头连通性实测中定位并修复 WSL2 跨子系统进程树清理异常、只读镜像无后缀工程文本漏拷及 WSL2 互操作 MSIX Codex 择新缺失问题：

- **WSL2 跨子系统互操作退出码保护与 MSIX 包择新对齐（`cae_harness.py`、`install.py`、`tests/`）**：
  - 修复 `_interop_command` 在 Windows PowerShell 5.1（`$ErrorActionPreference='Stop'`）下执行 `finally` 进程树清理时，因已正常退出的子进程触发 `taskkill.exe` `2>$null` 流重定向 `NativeCommandError` 而误返退出码 `1` 的真机缺陷，改为通过 `System.Diagnostics.ProcessStartInfo` 启动清理、异步排空重定向流并在等待超时后强制终止及输出诊断；
  - 新增 WSL2 下基于原生 `reg.exe` 的零缓存写入 MSIX 应用包只读解析（支持 UTF-8 与 `ACP` 本地代码页回退严格解码），在 Windows 与 WSL2 的 Codex 探测及最终回退分支中统一排除 `%LOCALAPPDATA%\Microsoft\WindowsApps` 执行别名占位文件，同步对齐 MSIX `app/resources/codex.exe` 与桌面缓存二进制的时间戳择新；
  - 扩展 `collect_target_files` 收录 `NOTICE`、`LICENSE`、`COPYING`、`Dockerfile`、`GNUmakefile` 及 `.svg` 矢量图文件，消除 `--clean-room` 空上下文盲读与跨子系统只读镜像漏拷顶层无后缀文本文件的盲区，并补齐离线与双端真机无头回归验证。

---

## [1.14.1] - 2026-11-15

修复跨客户端无头协作选路与隔离快照校验缺陷、Windows MSIX 应用包新版 Codex CLI 探测覆盖、GitHub CommonMark 强调定界符渲染异常及脱敏单测占位符越界问题：

- **跨客户端无头编排与客户端探测修复（`cae_harness.py`、`install.py`、`rules/AGENTS.md`、`fleet-evolve/SKILL.md`）**：
  - 修复显式指定专长角色槽位（如 `codex:code`、`codex:math`、`codex:review`、`dsh:code`、`agy:@math`）并传入 `--tier low` 时被静默降级替换为 `fast` 轻量模型（如 `gpt-6-luna` 在 `--output-schema` 下跳过工具读取）的缺陷，仅在 `role:fast`／`<client>:@fast` 或未指定专长槽位时允许 `low` 挡切入轻量模型；
  - 修复 `cmd_task` 在 `--clean-room` 或跨子系统副本目录（`staged_readonly_workspace`）模式下比对原工作区 `root` 而非隔离副本目录 `stage_dir`，导致主会话并发准备文件时误报 `scope_violation` 的缺陷，改为自 `stage_dir` 快照建立起全阶段保护并在临时目录清理前执行哈希核验；同时在 `user_words.py` 中对 `CAE_HARNESS_DEPTH=1` 无头瞬态调用（`--ephemeral`／`--no-session-persistence` 无落盘轨迹）静默跳过长会话门禁告警；
  - 统一 `install.py` 与 `cae_harness.py` 在 Windows 下对 Microsoft Store／MSIX 应用包（`WindowsApps\OpenAI.Codex_*`）与 `%LOCALAPPDATA%\OpenAI\Codex\bin` 缓存目录的按时间戳择新逻辑（保留用户显式安装的 `~/.local/bin` 与 `~/.cargo/bin` 优先级），避免旧版桌面缓存二进制遮蔽新版 MSIX CLI；
  - 明确 `R3.1` 与 `fleet-evolve` 中跨客户端 `cae-harness`（`role:math`、`role:code`、`role:review`、`audit`）与同端 `readonly-*` 子代理的分工边界，防止在多端就绪环境下仅用同模型子代理替代跨模型独立审查。
- **GitHub CommonMark 粗体定界符与公开脱敏单测修复（`README.md`、`AGENTS.md`、`CHANGELOG.md`、`docs/workflow-guide.md`、`cae-harness/SKILL.md`、`tests/`）**：
  - 修复全仓 5 份 Markdown 文档中紧邻全角括号与引号的 `**...**` 违背 CommonMark 0.31.2 侧翼定界符规则（在 GitHub 裸露原样星号）的排版问题，并在 `test_e2e_integration.py` 中新增全仓 Markdown 强调定界符静态回归检查；
  - 将 `test_harness_offline.py` 中触发 `test_02` 路径扫描拦截的非白名单模拟主目录占位词统一改为受控占位符 `<user>`，在 `test_claude_and_router_offline.py` 的 `OfflineSandbox` 中补齐外部 `DSH_HOME` 与 `CAE_FLEET_PROFILE` 环境变量隔离，并新增专长槽位保模、隔离副本快照校验与 Windows MSIX 择新的离线单元测试。

---

## [1.14.0] - 2026-11-15

完成全仓运行期定量参数（`runtime_tunables`）深度收口、会话生命周期钩子静态拆壳与截断全文恢复加固、无头编排器 JSON 解析与子进程树清理增强、本地分流代理与升级审计边界修复，以及全仓文档、子代理契约与矢量图的整篇重写润色：

- **无头编排器、动态配置收口与状态锁加固（`fleet_profile.json`、`cae_harness.py`、`fleet_state.py`）**：
  - 在 `fleet_profile.json` 的 `runtime_tunables` 中统一收口 Shell 包装深度上限（`max_wrapper_depth = 4`）、过期锁清理周期（`stale_lock_cleanup_days = 7`、`stale_lock_sweep_interval_sec = 86400`）、跨子系统副本上限（`interop_stage_max_mib = 64`）、审查并发数（`audit_max_workers = 4`）、双盲共识行号容差（`consensus_line_tolerance = 8`）、`agy` 强杀宽限与目录探测超时（`15s`／`30s`）、状态锁有效期（`fleet_state_ttl_sec = 86400`）及 WSL2 路由默认端口（`wsl_port = 8241`）；
  - 重写 `_decode_output_json` 为外层代码围栏剥离、字符串内非法单反斜杠转义修复（兼容 Windows 路径与 LaTeX 公式）及 `JSONDecoder.raw_decode` 平衡对象扫描，修复正文中内嵌三重反引号代码片段或未转义反斜杠导致的 `dsh` 无头输出解析失败；
  - 补充宿主环境变量自动识别调用方客户端、Windows Job Object 子进程树绑定强杀、`--clean-room` 配额回退自动排除 `agy`、双盲 `audit` 配额回退跳过对侧已占用客户端以保持双源独立、消费 `agy_retiring_patterns` 过滤退役模型，并修复 `audit_doc_grounding` 对单行闭合围栏、行内代码 `$VAR`、尖括号占位符、中英文相邻大写缩写及互补百分比的边界判定；
  - 加固 `fleet_state.py` 对 UTF-8 BOM 的读取兼容、Windows 文件替换 `PermissionError` 短退避重试、`AWAITING_USER_APPROVAL` 阶段版本漂移校验及顶层异常结构化 JSON 输出。
- **会话生命周期钩子安全与压缩恢复加固（`user_words.py`、`session_context_loader.py`、`status_guard.py`、`handoff_gate.py`、`reply_repeat_guard.py`）**：
  - 引入稳定文件缓存键 `get_fleet_profile_cache_token`（含缺失负缓存与宿主全局插件目录路径兜底）及 `was_unpatched` 单元测试属性打桩基线保护，新增非阻塞低频清理空闲过期 `.lock` 锁文件机制；
  - 修复 Antigravity `SYSTEM_MESSAGE` 长篇子代理返回触发 `truncated_fields` 时先回查 `transcript_full.jsonl` 还原完整正文再做实质筛选的顺序，保留 `raw_body` 消除归档与内联注入的双重摘要省略，保持 Codex 跨分段全局步骤号单调递增，收紧 Claude Code 包裹性系统块剥离规则，并在内联截断切中代码块时自动补齐闭合围栏且不超字符上限；
  - 扩展 `status_guard.py` 支持 PowerShell `-EncodedCommand`／`-ec` UTF-16LE Base64 拆壳（非法编码保守拦截）、PowerShell 写删移复内置别名（`rd`、`ri`、`ni`、`ac`、`clc`、`mi`、`rni`、`cpi`，排除 `sc` 防误伤 Windows 服务查询）、GNU `cp/mv -t`／`--target-directory`（区分大小写 `-t` 与 `-T`）、`sys.executable`／`os.getcwd()`／`Path.cwd()` AST 静态求值，以及多子句命令链按滚动工作目录逐句检查重定向与内联 Python 脚本。
- **安装器、本地分流代理与卡片管理边界修复（`install.py`、`router.py`、`ccswitch_admin.py`、`launch-cc-switch-wsl.sh`、`update-cc-switch-wsl.sh`）**：
  - 修复 Windows 多版本 Codex CLI 按文件时间戳择新与 `DeepSeek Harness.exe` 探测、WSL2 互操作探测完整性标记、清理废弃 `session-*.json.lock`、私网 IPv4 `<private-ip>` 与自定义卡片 ID `<custom-card-id>` 脱敏，以及激活直连供应商卡片时不误探本地路由健康或误报 `start_cc_router`；
  - 加固 `router.py` 支持 `Transfer-Encoding: chunked` 请求体解析与体积限制、空白 `ANTHROPIC_AUTH_TOKEN` 自动回退 `ANTHROPIC_API_KEY`、上游返回 `401`／`403` 时自动失效密钥缓存、中文卡片名 ASCII 错误短语编码保护，以及 `ccswitch_admin.py` 异常回滚时自动清理孤儿备份文件；完善 WSLg 启动脚本已有实例复用与 `.deb` 更新脚本架构自适应。
- **全仓图文文档、规则与子代理模板整篇重写及回归单测扩展（`README.md`、`AGENTS.md`、`docs/`、`template/`、`tests/`）**：
  - 结合 `--clean-room` 空上下文新读者盲读反馈，按自然平实工程书面语整篇重写全仓文档、全局规则、技能说明、三端 9 个 `readonly-*` 子代理配置与两张 SVG 矢量图，通过 `check-grounding --strict` 零黑名单核验并补齐全部新增边界的离线回归单测。

---

## [1.13.0] - 2026-11-15

完成五类角色槽位与运行期定量参数（`runtime_tunables`）全动态收口、压缩后高价值子代理实质筛选、零黑名单文档事实锚定硬门禁（`check-grounding`）、通用课题项目与上游配置仓库双场景分类隔离，以及全仓图文文档校准与国家留学基金委资助致谢补充：

- **五类角色槽位对称动态选路与 `runtime_tunables` 死数字收口（`fleet_profile.json`、`user_words.py`、`session_context_loader.py`、`handoff_gate.py`、`reply_repeat_guard.py`、`status_guard.py`、`router.py`、`cae_harness.py`、`install.py`）**：
  - 统一 `orchestrator`、`math`、`code`、`review`、`fast` 五个角色槽位为全动态对称结构（均支持 `role:<slot>` 与 `<client>:@<slot>` 选路及 `recommended_tier` 读取，不将任何角色与单一客户端或模型永久绑定）；
  - 在 `fleet_profile.json` 中新增 `runtime_tunables`（支持 `CAE_FLEET_PROFILE` 离线覆盖并按文件 `mtime_ns` 缓存，同时保留单元测试属性打桩优先级），统一收口各客户端压缩恢复字符预算（`per_client`：Antigravity `7,600` 字符、Codex／Claude Code `8,000` 字符）、长会话交接阈值（`handoff_thresholds`）、空等与日志轮询阈值（`loop_guards`）、无头分片安全系数／超时／验证轮数／润色长短篇阈值及本地分流代理默认并发与缓存参数；
  - 新增压缩前子代理返回实质筛选函数 `select_recent_subagent_returns`，在候选窗口（`recent_subagent_window`）内无条件保留最新一条返回，并按实质字数阈值（`min_substantive_subagent_chars = 64`）、审查／推导角色关键词与时间先后择优保留高价值结论，防止简短状态回执挤占关键行号证据。
- **零黑名单确定性文档事实锚定门禁（`cae_harness.py`、`fleet_state.py`、`rules/AGENTS.md`）**：
  - 彻底清除前序重写阶段混入仓库规则与文档的无源码依据制造术语（`CAM`、刀轨、曲面求交等）；
  - 在 `cae_harness.py` 中新增通用零黑名单事实锚定子命令 `check-grounding`（支持 `--target`、`--root`、`--base-ref`、`--allow-from-file`、`--strict`），自动扫描目标工作区源码与配置文件构建标识符、`--flag`、数值与派生单位及中文双字词干集合，逐行拦截无依据命令行标志／文件名、陌生全大写缩写、失步容量数字与虚构领域举例；
  - 在 `cae_harness.py task --kind polish` 中以改写前原稿为基线自动输出 `grounding_audit` 核验结果，并在 `fleet_state.py clear --verified --readback-done` 终审归位前强制调用 `audit_doc_grounding` 硬门禁。
- **通用课题项目与上游配置仓库双场景分类及工作区身份硬隔离（`cae_harness.py`、`fleet_state.py`、`fleet-evolve/SKILL.md`、`README.md`）**：
  - 在 `cae_harness.py`（`probe`、`assess`、`check-grounding`）中新增 `detect_workspace_mode`（区分 `general_project` 与 `upstream_config_repo`），在 `fleet_state.py` 中对非上游仓库无 `--repo` 调用返回结构化退出码 `2`（`reason: "not_upstream_config_repo"`），并收窄 `fleet-evolve` 触发条件，确保日常课题研发说出“全量优化代码和文档”时仅在项目内闭环，绝不误触全局元更新；
  - 将 `README.md` 第四章表二重构为“通用课题与工程项目日常研发（部分功能）”与“维护本上游配置仓库（全功能：全面更新 `full` 与全量优化 `hygiene` 双闭环）”两张分类对照表。
- **图文文档全量校准、致谢补充与回归单测扩展（`README.md`、`docs/`、`docs/assets/*.svg`、`tests/`）**：
  - 整篇重写校准 `README.md`、`AGENTS.md`、`template/user-guard/rules/AGENTS.md`、两份 `SKILL.md`、`docs/*.md` 及两张 SVG 架构管线图，在 `README.md` 第六章中英文部分补充**中国国家留学基金委**（**China Scholarship Council, CSC**）目前唯一资助致谢（学号：`202506280162`，批准文号：`留金选[2025]37号`），并新增动态参数覆盖、实质子代理筛选、对称角色路由、双工作区硬拦截与 `check-grounding` 防幻觉回归单测。

---

## [1.12.0] - 2026-11-15

完成常驻规则脚本与模型评估配置两层解耦、压缩后“历轮用户原话 + 最近子代理返回摘要”三端双路有界恢复、推荐搭配表用户审批状态锁、无头调用限流与分类器故障修复，以及图文分层文档重构、窗口／配额／缓存实证校准与静态／运行期脱敏加固：

- **压缩后“历轮用户原话 + 最近 4 次已完成子代理摘要”双路有界恢复（`user_words.py`、`session_context_loader.py`、`rules/AGENTS.md`、`AGENTS.md`、`tests/`）**：
  - 扩展 Antigravity（识别 `invoke_subagent` 与子代理完成消息，单遍 $O(M)$ 回查 `transcript_full.jsonl` 补齐截断正文并记录 `logAbsoluteUri`，过滤 `sender=system`、`/task-` 命令噪声与 `BROWSER_HOST_FACTS`）、Codex（合并 `sessions/` 与 `archived_sessions/` 分段，匹配 `spawn_agent`／`wait_agent` 完成状态映射表并按 `(call_id, agent_id, content_sha256)` 去重，自动定位子代理 `rollout-*.jsonl` 路径）与 Claude Code（提取非支线 `Agent`／`Task` 工具完成回执，跳过 `async_launched` 句柄与 `is_error` 异常块并关联 `subagents/agent-<id>.jsonl`）；
  - 将子代理返回摘要严格独立于人类权威指令列表 `words`（遵守 `R8` 与 `R6`，附带首尾双端摘要与原始日志指针），未发生新压缩的普通回合保持零额外字节注入（保护服务端前缀缓存字节一致性），压缩恢复内联总预算严格限制在 `MAX_INLINE_TOTAL_CHARS = 8000`（子代理摘要区上限 `1,400` 字符、单条上限 `280` 字符），且在未调用子代理时保持归档字节完全向后兼容。
- **常驻代码与模型评估配置解耦及审批状态锁（`fleet_profile.json`、`cae_harness.py`、`fleet_state.py`、`install.py`、`ccswitch_admin.py`、`rules/AGENTS.md`、`AGENTS.md`、`SKILL.md`）**：
  - 新增机器可读配置文件 `template/user-guard/skills/cae-harness/fleet_profile.json`（支持 `CAE_FLEET_PROFILE` 环境变量覆盖测试），集中定义五类抽象角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）候选顺序、实证优缺点与隔离措施、暂缓接入平台、客户端模型目录匹配规则及供应商卡片默认参数；
  - 从常驻脚本、规则与技能说明中移除特定时期的模型名称硬编码，支持 `role:*` 与 `<client>:@<role>` 动态选路；在 `fleet_state.py` 中新增 `propose` 与 `approve` 子命令（`AWAITING_USER_APPROVAL` 状态）并将 `fleet_profile.json` 纳入 `core_sha256` 校验，固化四个必停用户拍板节点。
- **无头调用故障修复与边界代码加固（`cae_harness.py`、`handoff_gate.py`、`status_guard.py`、`reply_repeat_guard.py`、`router.py`、`ccswitch_admin.py`、`install.py`）**：
  - 扩展 `QUOTA_ERROR_RE` 与 `_parse_quota_reset` 识别 `hit your session/weekly limit`、`5-hour ... limit` 及无介词 `resets 5pm` 限流报文，修复 Claude Code JSON 错误信封（`is_error: true` 且错误信息位于 `result` 字符串字段）提取遗漏，并为 Claude Code 无头只读任务固定附加 `--permission-mode dontAsk` 以规避 `auto` 分类器限流报错；
  - 补齐 `dsh` 在 `--clean-room` 模式下对 `.env` 文件中 `DEEPSEEK_API_KEY` 的提取，修复 `audit --single --left role:fast` 未透传引擎提示导致推理挡位误选 `max` 的问题，并加强 `dsh` 单 JSON 对象输出提示约束；
  - 对齐 `handoff_gate.py` 在 `drain` 与 `freeze` 阶段对 `env -u/-C/-S`、`sudo` 带参选项及 `command -v/-V` 的 Shell 解包逻辑，统一 `status_guard.py` 在多层无头子进程（`CAE_HARNESS_DEPTH >= 1`）下的项目外写拦截行为，清理 `reply_repeat_guard.py` 未使用的冗余函数，支持 `router.py` 模型前缀不区分大小写匹配，并加固 `ccswitch_admin.py` 对损坏配置文件的容错保护。
- **图文分层文档重构、实证指标校准与静态／运行期脱敏加固（`README.md`、`docs/`、`docs/assets/*.svg`、`install.py`、`ccswitch_admin.py`、`cae_harness.py`、`tests/`）**：
  - 新增纯 ASCII 路径矢量架构图 `docs/assets/architecture-overview.svg` 与生命周期／定量验证管线图 `docs/assets/lifecycle-and-verification.svg`，配合 Mermaid 时序／流程图与 L1–L4 定量证据等级矩阵重组 `README.md`、`docs/architecture-and-hooks.md` 与 `docs/workflow-guide.md`；
  - 逐项核对 `cae_harness.py` 预算公式与本机模型目录缓存，校准单分片安全输入预算（`120,000`／`745,560`／`900,000`／`943,718` token）、出厂默认窗口／挡位与显式上限参数、客户端订阅 5 小时／周度滚动配额池与按量 API 前缀缓存计费机制，剔除未经实测的经验百分比与生造套话；
  - 彻底清除文档中的个人双机请求计数、账单用量统计与敏感措辞；同步加固 `install.py --audit-upgrade`（覆盖驼峰密钥字段、布尔开关防误替换、WSL 跨盘符主目录、`ghp_`/`AIza`/`AKIA`/`hf_`/`glpat-` 令牌、`<ccswitch>` 路径及自定义提示词／技能目录名脱敏）、`ccswitch_admin.py`（上游接口 URL 密钥参数脱敏）与 `cae_harness.py`（配额冷却详情与无头异常日志密钥脱敏），并补齐离线回归单测。

---

## [1.11.1] - 2026-11-15

新增 `cae-harness` 空上下文盲读模式（`--clean-room`），建立“空上下文新读者盲读 -> 主会话重写自然工程中文 -> 独立核对技术准确度与脱敏”三步文档校核流程，并清理 CC Switch 旧版残留项：

- **无头空上下文盲读模式（`cae_harness.py`、`tests/test_harness_offline.py`）**：在 `cae_harness.py task` 中新增只读专用的 `--clean-room` 选项（支持 `codex`、`claude`、`dsh`；对不支持剥离全局插件的 `agy` 或修改文件任务明确报错拦截）。开启后自动在系统临时目录构造仅保留必要登录凭据或 API 环境变量的隔离配置目录（`claude` 自动启用 `--bare` 与最小环境变量 `--settings`，`codex` 与 `dsh` 剥离全局 `AGENTS.md`、钩子与技能，并在只读工作区副本中排除 `AGENTS.md` 与 `CLAUDE.md`；当 `codex` 只读副本不超过 `256 KiB` 时自动附带行号文件快照以兼容 Windows 只读沙箱），提供不带历史上下文、全局规则、钩子与技能的独立审阅环境。
- **三步文档校核流程与文档结构重排（`README.md`、`docs/`、`AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`fleet_state.py`、`tests/test_e2e_integration.py`）**：
  - 在 `R0`、`R2`、`fleet-evolve/SKILL.md` 与根目录 `AGENTS.md` 中确立三步文档编写与重构流程，并在 `fleet_state.py clear --verified --readback-done` 中记录校核状态；
  - 重排 `README.md` 章节顺序（前置单客户端快速安装步骤、统一 `--caller auto` 命令示例、补充运行概念与卸载说明），在 `docs/workflow-guide.md` 中新增二维可压缩 Euler 方程 HLLC 界面通量任务单模板与 JSON 回执字段说明，并将 `docs/architecture-and-hooks.md` 拆分为清晰的模块职责表与钩子触发条件表；
  - 在 `test_41` 中增加非 `CHANGELOG.md` 文档禁止残留静态日期戳与补丁式附注抬头的回归检查。

---

## [1.11.0] - 2026-11-15

建立非主控外部工具三级二进制接口（ABI）耦合升级检查机制，完成全仓安全与并发边界修复，并统一文档维护范围：

- **非主控工具三级升级评估与 `host.toolchain` 耦合提示（`install.py`、`fleet-evolve/SKILL.md`、`AGENTS.md`）**：按依赖耦合程度将外部工具升级划分为三级——第一级（无外部编译依赖的非当前客户端 CLI、`py.exe` 启动器、CI 工作流）、第二级（同次版本 Python 小补丁与 `wsl --update`，先检查运行中进程与算例再执行）与第三级（装有 `jax`、`jaxlib`、`scipy`、`mpi4py` 等编译扩展的系统级 Python 跨次版本升级、CUDA 跨大版本升级及 WSL 跨 LTS 发行版升级，发出明确依赖耦合警告并交由用户决定）；`install.py --audit-upgrade` 新增 `host.toolchain` 只读扫描并输出 `coupling_warnings`。
- **钩子静态分析、并发锁与本地路由边界修复（`status_guard.py`、`cae_harness.py`、`fleet_state.py`、`router.py`、`ccswitch_admin.py`、`install.py`）**：
  - `status_guard.py`：增加 Shell 嵌套解包深度上限（超过 `4` 层按需确认或拦截），剥离 `if/elif/while/until` 条件前缀，解析 `env`（含 `-C`）、`sudo`、`command`（放行 `-v/-V` 查询）与 `timeout` 包装，跨子句追踪 `cd/pushd` 工作目录变化，识别无空格紧邻重定向（`echo x>/out`、`>|`、`<>`、`&>`），支持 `py` 与 `python -Bc` 内联脚本中整数步号格式化字符串（如 `f"step_{i:04d}.vtk"`）、`pathlib.Path()` 与 `.resolve()` 静态路径求值，并在读写状态文件前检查重解析点；
  - `cae_harness.py`：补充 Windows PowerShell `finally` 子进程树清理、确保 `probe` 与 `--dry-run` 零磁盘写入、转义 WSL 跨子系统 `.cmd/.bat` 参数中的百分号、将 `.github` 纳入快照比对，并优先识别 `AGY_ERROR` 超时诊断行；
  - `fleet_state.py`：引入跨平台文件锁（`msvcrt` 与 `fcntl`）、`repo_root` 与 `core_sha256` 变更检测、`verify-pass2` 会话认领防并发冲突及损坏状态文件诊断；
  - `router.py`、`ccswitch_admin.py` 与 `install.py`：为本地分流脚本增加 `64 MiB` 请求体上限（`413`）与 `64` 路并发限制（`503`）；加固 `ccswitch_admin.py` 符号链接拦截与切卡异常时优先恢复 `settings.json`；在 `install.py` 中增加 `_rules_equal` 精确检测规则尾段差异并规范化比对 Codex `hooks.json`。
- **文档职责梳理与 `fleet-evolve hygiene` 模式补充（`README.md`、`docs/`、`AGENTS.md`、`SKILL.md`）**：统一将钩子协议与分流细节归入 `docs/architecture-and-hooks.md`，在 `README.md` 第四章参考表中补充 `fleet-evolve hygiene` 调用说明，并修正 `ccswitch_admin.py switch-card` 命令行位置参数示例。

---

## [1.10.0] - 2026-11-15

新增 `fleet-evolve` 升级整理技能与二阶段状态锁，补充第四章两张核心参考表，并修复目录 `Grep` 通配符、`DSML` 停止标记清理与升级检查脱敏问题：

- **`fleet-evolve` 技能与宿主机状态锁脚本（`template/user-guard/skills/fleet-evolve/`、`install.py`、`AGENTS.md`）**：新增支持 `full`、`audit`、`hygiene` 三种模式的技能说明 `SKILL.md` 与纯标准库状态锁脚本 `scripts/fleet_state.py`（保存在宿主机本地状态目录，记录运行侧、提交哈希、插件版本、脚本哈希、`session_id` 与 `24h` 有效期，防止主会话客户端重启后重复执行）；扩展 `install.py` 同步 `skills/` 下全部托管技能并纳入 `--audit-upgrade` 一致性检查。
- **用户自理前提划分与参考对照表整理（`README.md`、`docs/`、`rules/AGENTS.md`）**：区分用户自行准备的基础前提（网络环境、客户端安装、官方账号登录与 API Key）与本仓库自动同步的规则、钩子、子代理及技能；在 `README.md` 第四章整理客户端配置参考表与日常需求调用对照表。
- **钩子通配符、停止标记与审计脱敏修复（`status_guard.py`、`cae_harness.py`、`install.py`、`ccswitch_admin.py`）**：修复 `status_guard.py` 在 Claude Code 目录 `Grep` 传入复杂 glob（如 `**.py`、`{a,b}`、`!exclude`）时触发 `Path.rglob` `ValueError` 及遍历 `.git` 目录的问题；修复 `cae_harness.py` 在 `dsh` 无头模式下模型输出尾随 `</｜｜DSML｜｜...>` 标记导致的 JSON 解析失败；在 `install.py` 中排除 Antigravity 默认选模里的退役第三方模型、隔离沙箱审计对宿主机 `cc-switch.db` 的读取、脱敏自定义供应商卡片名称，并补齐 `ccswitch_admin.py` 卡片上下文窗口检查。

---

## [1.9.1] - 2026-11-15

完善 CC Switch 旧版提示词与技能停用归档、升级检查误报修复及 WSL2 跨子系统无头调用预检：

- **CC Switch 旧版提示词与技能停用清理（`ccswitch_admin.py`、`install.py`、`R11`）**：明确 CC Switch 仅用于管理供应商配置卡片，停用其内置提示词（`prompts`）与技能（`skills`）同步功能；`inspect_status`、`verify_alignment` 与 `install.py --audit-upgrade` 增加 `skills` 表启用状态及 `~/.cc-switch/skills` 目录残留检查，`--disable-ccswitch-prompts` 同步停用旧提示词与旧技能开关，并新增 `ccswitch_admin.py retire-legacy --purge` 与 `install.py --purge-ccswitch-legacy` 在线备份归档清理命令。
- **升级检查误报修正与 WSL2 Fake-IP 直连预检（`install.py`、`cae_harness.py`）**：修正 `audit_upgrade_status` 在 Antigravity 已选用最新主力模型时因目录仍含待下线旧模型产生的误报，以及 WSL2 复用 Windows Codex CLI 时的模型目录刷新误报；`cae_harness.py` 新增 WSL2 直连卡片解析命中宿主机透明代理 Fake-IP 地址段（`198.18.0.0/15`）且未配置代理时的预检报错提示。

---

## [1.9.0] - 2026-11-15

统一仓库名称与 Apache 2.0 `NOTICE` 署名声明，并将 Antigravity（`agy`）、Codex（`codex`）、Claude Code（`claude`／`cc`）与 DeepSeek Harness（`dsh`）整合为四客户端对等部署与后台调用体系：

- **支持 DeepSeek Harness 安装与配置检查（`install.py`、`status_guard.py`）**：新增 `--app dsh`、`--dsh-home` 部署模式及 `--audit-upgrade` 一致性检查，自动识别 `~/.dsh` 并同步 `R0–R11` 全局规则（保留末尾自定义 `## 工具与...` 段落）与托管技能目录，同时补齐 `~/.claude/skills/` 同步。
- **`cae-harness` 四客户端对等调用与运行前预检（`cae_harness.py`）**：在 `agy` 与 `codex` 基础上接入 `claude`（别名 `cc`）与 `dsh`（通过临时 `--patch` 激活官方登录态或 `DEEPSEEK_API_KEY`）；支持按中英文提示词识别目标客户端并排除编译环境变量（如 `CC=gcc`）与否定句误匹配；调用前校验模型凭据、本地路由 `/__router/health` 状态及供应商卡片密钥，支持在 `--fallback-on-quota` 且工作区未改动时自动切换至其他就绪客户端。
- **仓库标识更新与六模式沙箱测试（`NOTICE`、`LICENSE`、`plugin.json`、`.github/workflows/ci.yml`、`tests/`）**：统一仓库标识为 `Antigravity_Codex_ClaudeCode_DeepSeekHarness`，新增根目录 `NOTICE` 文件，并在跨平台 CI 与离线测试中覆盖六种 `--app` 安装模式（`codex`、`antigravity`、`claude`、`dsh`、`both`、`all`）。

---

## [1.8.0] - 2026-11-14

新增 Claude Code 与 CC Switch 配置同步、本地模型分流脚本 `router.py` 以及零写入 `--audit-upgrade` 检查功能：

- **扩展 Claude Code 全局规则、只读子代理与生命周期钩子（`template/user-guard/`、`template/claude/`、`install.py`）**：新增 `--app claude` 与 `--app all` 安装模式，同步 `~/.claude/CLAUDE.md`（保留末尾自定义段落）与三个只读子代理，并将原话恢复、项目外写操作拦截、防重复等待回复及长会话交接四个钩子适配 `--client claude` 事件协议。
- **新增本地模型分流服务与供应商卡片管理脚本（`template/claude/router/`）**：提供纯标准库 HTTP 分流脚本 `router.py`（支持按模型前缀转发主对话与子代理请求、WSL2 宿主机代理探测及 60 秒卡片密钥只读缓存）、卡片管理命令行 `ccswitch_admin.py`（支持 SQLite 在线备份、卡片初始化、保留钩子配置安全切卡及停用旧提示词）以及 WSLg 启动与 `.deb` 升级辅助脚本。
- **新增 `--audit-upgrade` 只读检查器（`install.py`、`.github/workflows/ci.yml`、`tests/`）**：Windows 应用包探测改用标准库 `winreg` 只读查询，支持零磁盘写入检查各客户端版本、SQLite 表结构、钩子配置与模型窗口参数一致性。

---

## [1.7.5] - 2026-11-14

规范纯 ASCII 文档路径、补充英文概览与跨平台 GitHub Actions 自动回归测试：

- **文档文件名纯 ASCII 规范化（`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`、`README.md`、`template/user-guard/README.md`）**：将 `docs/` 下文档重命名为小写 ASCII 短横线文件名，消除跨平台 Git 路径八进制转义与 URL 编码问题，并更新全仓相对链接。
- **补充英文说明与许可证边界（`README.md`）**：在根 `README.md` 补充 `English Overview`，说明支持的客户端范围、Python 3.11+ 标准库运行环境以及内置简体中文规则模板的适用范围。
- **统一单一版本号与行尾换行符规范（`template/user-guard/plugin.json`、`template/user-guard/README.md`、`install.ps1`、`.gitattributes`）**：移除子目录文档中的重复版本号，以 `plugin.json` 作为唯一版本来源；保持 `install.ps1` 为 PowerShell 5.1 所需的 UTF-8 BOM + `CRLF` 编码，其余文本与脚本统一使用 UTF-8 无 BOM + `LF`。
- **新增跨平台 CI 工作流与 Windows `8.3` 短路径兼容修复（`.github/workflows/ci.yml`、`install.py`、`status_guard.py`、`tests/`）**：新增覆盖 `ubuntu-latest` 与 `windows-latest`（Python `3.11` 与 `3.13`）的回归测试工作流；修复 Windows 临时目录以 NTFS `8.3` 短路径（如 `RUNNER~1`）传入时的路径边界判断与托管钩子去重问题。

---

## [1.7.4] - 2026-11-14

精简常驻全局规则篇幅，统一 `Python 3.11+` 标准库基线并移除未使用的旧版本兼容代码：

- **精简常驻全局规则并补充反过度工程要求（`template/user-guard/rules/AGENTS.md`、`AGENTS.md`、`readonly-*` 子代理）**：删去常驻规则中与技能说明重复的命令行参数罗列；在 `R3.3` 明确不为未要求的老旧版本、外围平台或假想场景编写冗余兼容分支；在 `R3.6` 明确仅针对目标环境如实标注“未验证”项；并将原生 Shell、UTF-8 编码与公开仓库脱敏要求并入 `R4`。
- **统一 `Python 3.11+` 标准库实现（`install.py`、`install.ps1`、`install.sh`、`cae_harness.py`、`status_guard.py`）**：直接使用标准库 `tomllib`、`ast.Match` 与原生 ISO 时间戳解析，删除针对 `Python 3.9/3.10` 的手写 TOML 回退解析器与 AST 兼容垫片，聚焦 Windows 与 WSL2／Linux 环境。
- **Codex 钩子启动参数升级对齐（`install.py`）**：升级已有 `session_context_loader.py` 托管钩子时自动将旧版 `-I -B` 参数补齐为 `-X utf8 -I -B`，保持重复执行幂等。

---

## [1.7.3] - 2026-11-14

补充 AST 静态路径检查覆盖范围、长会话交接子壳分组解析与编排器验证标记：

- **阶段收尾代码测试与文档脱敏分工（`R3.1.4`、`AGENTS.md`）**：明确版本收尾前同步完成全仓回归测试、死代码清理、文档润色与脱敏核查。
- **完善内联脚本关键字与解包参数路径检查（`status_guard.py`）**：补齐 `Path.rename/replace`、`os.remove/unlink/mkdir/makedirs/rmdir/removedirs/rename/replace`、`shutil.copy*/move/rmtree`、`np.save*`、`open(file=..., mode=...)` 及 `run_workflow` 中 `agent(**kwargs)` 的路径静态检查。
- **加固长会话子壳分组拦截与压缩恢复游标（`handoff_gate.py`、`session_context_loader.py`、`user_words.py`、`reply_repeat_guard.py`）**：支持拆分引号外的 `( ... )` 子壳命令以拦截交接期 `(git push ...)` 绕过；修复 Codex 压缩事件游标重置、空状态文件自愈、Windows 文件锁显式释放及大体积交接文件尾行逆向读取。
- **强制校验非空验证命令与特殊文件跳过保护（`cae_harness.py`）**：拦截空字符串 `--verify-cmd`，在回执中显式输出 `verification_executed` 布尔字段；合并文件快照 `lstat` 调用并跳过 FIFO、socket 等特殊文件防止阻塞。
- **安装脚本流透传与目录联接区分（`install.py`、`install.ps1`）**：`install.ps1` 改用标准流直接继承与原生参数转义；`install.py` 区分云同步目录重解析点与目录联接，并在升级时自动清理废弃空目录。

---

## [1.7.2] - 2026-11-14

适配 Antigravity 桌面端 `v2.32.0` 与 CLI `v1.5.8`，新增 `run_workflow` 静态路径检查与 `agy` 超时诊断识别：

- **明确前后端协作分工**：主会话负责需求梳理、文献检索与逐行复核，复杂公式推导、核心求解器编码与带验证命令的闭环测试交由后台无头工作流执行。
- **只读子代理配置兼容桌面端与 CLI**：移除子代理配置中会导致桌面端隐藏代理的 `hidden: true` 字段，保留 `subagent: true`、`mainAgent: true` 与 `[read_file, search_web, send_message, finish]` 工具列表。
- **将 `run_workflow` 与文件修改工具纳入 `R4` 边界检查（`status_guard.py`）**：补齐多类文件修改工具名与路径参数识别，将 `view_file` 纳入未变化日志重复读取拦截，并在 `run_workflow` 启动前静态检查外层 `Cwd`、`ScriptPath` 脚本与内联 `Script` 中的写路径。
- **增强内联 Python 路径静态追踪与冻结期 Git 分支写保护（`status_guard.py`、`handoff_gate.py`、`reply_repeat_guard.py`）**：按执行顺序追踪变量赋值、海象表达式、序列解包、分支合并与 `os.chdir`；在 `freeze` 阶段对 `git branch` 仅放行纯查询参数，并在 `reply_repeat_guard.py` 中同时兼容 `continuationActive` 与 `continuation_active` 续跑字段。
- **完善 `agy` CLI 超时判定与插件清单严格校验（`cae_harness.py`、`plugin.json`、`tests/`）**：识别 `agy --print-timeout` 退出码 `3` 与 `AGY_ERROR` 超时行，过滤正常业务正文后再做限流分类以防误判；补齐 `plugin.json` 的 `$schema`、`license` 与 `assets/logo.svg`，通过 `agy plugin validate --strict` 校验并新增全文件编码与脱敏扫描测试。

---

## [1.7.1] - 2026-11-14

- **默认启用单模型最高推理挡位与按需节省额度**：`cae-harness` 在推导、编码、审查与验证闭环任务中默认选用单模型非委派最高推理挡位（跳过会自动派生多层子代理的 `ultra` 挡），仅在用户明确要求节省额度时调低挡位。
- **安装器与钩子路径校验加固**：完善跨平台符号链接与目录联接检查、Codex `hooks.json` 升级合并逻辑及状态文件锁超时保护。

---

## [1.7.0] - 2026-11-14

- **配额冷却记录与 `--fallback-on-quota` 自动切换**：被调用客户端返回配额耗尽时按运行侧（`windows` 或 `posix`）记录冷却截止时间；开启 `--fallback-on-quota` 且工作区文件尚未改动时，自动切换至已就绪的备用客户端继续执行。
- **原生 `inherit` 子代理与后台选模解耦**：移除 Antigravity 只读子代理中的固定模型字段，使其默认跟随主会话模型，与 `cae-harness` 后台独立选模互不干扰。

---

## [1.6.1] - 2026-11-14

- **长会话分段交接钩子（`handoff_gate.py`）**：新增 `normal -> advisory -> drain -> freeze` 状态机，配合 `PreInvocation`、`PreToolUse` 与 `Stop` 事件引导整理交接摘要并检查 `<!-- GOAL_CANCELLED -->` 结束标记。
- **跨平台安装入口与 Zotero MCP 合并支持**：完善 `install.py`、`install.ps1`、`install.sh` 三个入口及 `mcp_config.json` 非破坏性合并。

---

## [1.5.0] - 2026-11-13

- **初始化 `user-guard` 插件与 `cae-harness` 编排脚本**：建立 `R0–R11` 常驻工程规则、会话原话增量归档与压缩恢复注入（`session_context_loader.py`）、`R4` 项目目录写边界与重复读日志拦截（`status_guard.py`）、防重复等待回复拦截（`reply_repeat_guard.py`）、三个只读子代理配置及离线回归测试集。
