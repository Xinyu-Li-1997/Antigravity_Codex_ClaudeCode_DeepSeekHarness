# 面向科学计算与工程研发的四客户端配置与验证工具

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24293507.svg)](https://doi.org/10.5281/zenodo.24293507)

本仓库 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 为 **Google Antigravity**（`agy`）、**OpenAI Codex**（`codex`）、**Claude Code**（`claude` 或 `cc`）与 **DeepSeek Harness**（`dsh`）提供共用的工程规范、事件钩子、只读审查子代理与命令行调度脚本。全仓仅依赖 Python 3.11+ 标准库，支持 Windows、WSL2 与 Linux；只安装其中任意一个客户端即可独立使用，安装多个客户端时可按任务类型协同完成公式推导、代码实现与交叉审查。

## 快速上手

1. **安装到本机客户端配置目录**：请先自行安装上述至少一个官方客户端并完成登录（OAuth 或 API Key；可选配合 CC Switch 管理供应商配置卡片）。克隆本仓库后，在仓库根目录执行一次安装命令：

   ```powershell
   # Windows 使用 python -B -X utf8；WSL2 与 Linux 改用 python3 -B -X utf8（也可运行 install.ps1 或 install.sh）
   python -B -X utf8 install.py
   ```

   脚本会将共用规则、钩子脚本与技能同步到本机已安装客户端的用户配置目录（`~/.gemini/config/plugins/user-guard`、`~/.codex`、`~/.claude`、`~/.dsh`），无需向你的课题或业务代码仓库复制任何配置文件。

2. **在项目目录中正常对话开发**：打开你自己的代码仓库启动客户端直接提问或布置任务即可，日常使用无需记忆额外命令：
   - **单文件问答与局部修改**：直接在主会话中完成；后台钩子仅拦截越界修改项目外目录、未获批的外部 `git worktree` 以及重复读取无变化的日志文件。
   - **多文件修改或多子任务协作**：启动前会先给出任务目标、拟调用的模型、允许修改的文件范围及验证测试命令（完整参数保存在本地 `*_card.md` 文件中），经你回复确认后才开始执行；执行完成后自动运行测试命令，并汇报成败结果与 Token 用量。
   - **长对话上下文压缩恢复**：当对话过长触发自动压缩时，钩子自动补回历轮关键指令与重要子任务结论，防止丢失物理符号约定与数值容差要求。

3. **指定模型与后续配置检查**：在对话中可直接写明要使用的客户端、模型或推理强度（例如“用 Codex 检查 `src/flux/`”）；后续升级客户端或变更订阅模型时，回到本仓库运行 `python -B -X utf8 install.py --audit-upgrade` 即可检查配置漂移并按提示同步。

## 目录导航

- [快速上手](#快速上手)
- [一、适用场景与解决的问题](#一适用场景与解决的问题)
- [二、安装与配置选项](#二安装与配置选项)
- [三、日常课题使用方式](#三日常课题使用方式)
- [四、配置仓库维护与模型更新](#四配置仓库维护与模型更新)
- [五、工作原理与四端协议适配](#五工作原理与四端协议适配)
- [六、文档索引与目录结构](#六文档索引与目录结构)
- [七、致谢、英文概览与开源许可](#七致谢英文概览与开源许可)

## 一、适用场景与解决的问题

本工具面向**科学计算、计算机辅助工程（CAE）、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值离散、高性能计算（HPC）及高可靠系统软件工程**领域的研发工作。

各家官方客户端已具备成熟的交互编辑循环与服务端自动前缀缓存（连续工具调用保持消息前缀一致时可复用缓存）。本仓库不改动客户端内核，而是通过全局工程规则（`R0–R11`）、事件钩子与验证脚本解决以下六类常见工程问题：

| 常见问题 | 客户端默认行为的局限 | 本仓库的处理方式 |
| --- | --- | --- |
| **长对话压缩丢失前文约束** | 自动摘要易漏掉早期物理符号、法向约定与容差要求；简短状态输出会冲掉审查行号 | 压缩恢复时分两块独立补回：**历轮用户原话 + 达到有效篇幅的子代理关键结论**，以及磁盘保存的**未决确认单** |
| **指定模型未生效或后台调用参数不全** | 口头指定的客户端或推理强度易被忽略；手写后台命令常漏传上下文窗口或文件快照 | 优先锁定用户明确指定的客户端、模型与推理强度，自动补全声明最大窗口、小文件带行号内联与前后哈希快照；目标不可用直接报错，不静默替换 |
| **测试未过放宽容差或漏报执行结果** | 倾向将 `rtol=1e-13` 放宽、删改断言，或多路任务结束后未说明成败与 Token 消耗 | 规则 `R3.6` 禁止擅自放宽容差、守恒残差与收敛阶判据；`gate` 强制运行验收命令；回合结束检查成败词、Token 说明与回执文件标识 |
| **只读审查误改源码、越界写目录或空转轮询** | 审查时顺手改代码、修改项目外目录或反复读取未更新的日志 | `readonly-*` 只读代理禁写文件、禁跑构建测试；后台只读任务前后比对文件哈希快照；工具执行前静态拦截越界写删与重复读日志 |
| **重写或润色文档时引入源码不存在的参数** | 润色时易混入仓库未实现的命令行选项、陌生缩写或失步数字 | `check-grounding --strict` 扫描仓库源码与配置校验参数和术语依据，配合 `--clean-room` 空上下文检查文档独立可读性 |
| **模型更替导致配置失步或升级破坏 ABI** | 在规则和脚本中写死模型名易过期；跨次版本升级系统级 Python 会破坏编译扩展 | 固定代码与 `fleet_profile.json` 动态参数分离维护，并提供 `--audit-upgrade` 检查配置漂移与科学计算包 ABI 兼容性风险 |

## 二、安装与配置选项

本仓库**不内置任何第三方反代中转，不创建也不修改任何账号凭据**。推荐按以下顺序探测、预览并同步：

```powershell
# Windows 使用 python -B -X utf8；WSL2 与 Linux 改用 python3 -B -X utf8
python -B -X utf8 install.py --probe-apps        # 1. 只读探测本机已安装客户端、配置目录与 WSL2 互操作状态
python -B -X utf8 install.py --dry-run --yes     # 2. 只读预览拟写入的目标文件路径（零文件修改）
python -B -X utf8 install.py                     # 3. 交互确认后幂等同步已安装客户端（非交互场景可加 --yes）
python -B -X utf8 install.py --audit-upgrade     # 4. 检查各端配置是否对齐（summary.all_aligned）及科学计算扩展 ABI 状态
```

- **指定同步范围与本地路由**：默认 `--app auto` 自动同步本机已安装的客户端；也可指定 `--app antigravity`、`codex`、`claude`、`dsh`、`both`（`Antigravity + Codex`）或 `all`。Claude Code 如需启用本地 API 协议转换路由或清理旧提示词，可附加 `--with-cc-router`、`--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy`（修改前自动在线备份 `cc-switch.db`）。
- **处理配置检查提示**：若 `--audit-upgrade` 返回 `summary.all_aligned: false`，按 `summary.recommended_actions` 处理即可——提示 `refresh_codex_model_catalog` 时先完成客户端首次登录以生成本地模型目录缓存；提示 `sync_codex_model_defaults` 或 `sync_claude_model_defaults` 且希望用推荐默认模型与推理强度覆盖现有设置时，附加 `--sync-model-defaults` 重跑 `install.py`；提示 `start_cc_router` 时按需启动本地 `router.py` 进程。
- **保留用户自有配置**：Antigravity 安装在独立插件目录 `plugins/user-guard`，不改动 `~/.gemini/GEMINI.md`；Codex、Claude Code 与 DSH 更新全局规则文件（`AGENTS.md` 或 `CLAUDE.md`）时，完整写入标准规则至 `## 工具与运行环境`（含 `R10.`、`R11.`），同时保留排在其后的用户自定义二级标题章节（含 `## 工具与` 开头的自定义环境说明），并将冲突条目编号重命名为 `L1.`、`L2.`。

## 三、日常课题使用方式

打开普通科研或工程仓库时，系统自动识别为通用课题模式（`workspace_mode: "general_project"`）。下面左图展示了日常课题目录中的调用流程，右图展示了打开本配置仓库时的维护流程：

![日常课题协作与配置仓库维护流程](docs/assets/user-and-evolution-flow.svg)

### 1. 明确指定客户端、模型或推理强度

当你在对话中点明客户端、具体模型、推理强度或节省额度要求时，外部无头调度脚本 `cae_harness.py` 通过 `--user-prompt` 将其作为最高优先级（P0）约束锁定，并自动补全上下文窗口、只读小文件行号内联与文件快照校验（客户端内部原生子代理则按宿主当前设置解析 `inherit` 模型与强度）：

| 指定方式 | 对话指令示例 | 锁定的参数 | 自动补全与报错保护 |
| --- | --- | --- | --- |
| **指定客户端或模型** | “这步编码用 Codex 高强度做”“交给 gpt-6-astra 做脱敏检查” | 指定的客户端、模型名称或模型系列 | 自动补全模型声明的最大上下文窗口、无头标志、小文件行号内联、前后哈希快照与脱敏；目标不可用或已停用时直接报错，不静默换用其他端 |
| **指定推理强度** | “用高强度推导离散格式”“用中等强度整理这段说明” | 指定的推理强度等级 | 对显式专业角色即使调低到 `low` 仍保留主力模型，避免退化为轻量模型或轻量套餐 |
| **指定节省额度或配额回退** | “省点额度快速检查一遍格式”“加 `--fallback-on-quota` 遇限额回退” | 低消耗模式或未改文件时的限额回退 | 遇 `quota_exhausted` 立即停止重试；仅在未锁定单一客户端、未绑定已批票据且工作区无文件改动时按配置回退 |
| **同时指定多个客户端** | “用 Codex 和 Claude 交叉审查这部分代码” | 识别“等、和、与、及”并列关系，不误锁单端 | 保留多路计划中各分支各自指定的客户端与模型组合 |

### 2. 未指定模型时按任务类型自动选路

只描述工程或数学目标而未指定具体模型时，系统按 `orchestrator`、`math`、`code`、`review`、`fast` 五类角色从本机已启用且额度正常的客户端中自动选择：

| 任务场景 | 对话指令示例 | 分配的角色与执行约束 |
| --- | --- | --- |
| **方案讨论与单文件小修改** | 直接在主窗口讨论方案、修改局部代码或做小范围只读检查 | `role:orchestrator`（保持主窗口当前客户端与模型，直接执行不额外询问） |
| **多篇文献摘录与大源码行号定位** | “把这三篇文献的控制方程和对应源码行号整理出来” | 当前客户端内部 `readonly-*` 只读子代理并行读取，禁止写文件、禁止运行构建与测试 |
| **控制方程与离散守恒性质推导** | “推导弯曲网格几何守恒律（GCL）离散条件与熵稳定判据” | `role:math`，默认选用单模型最高非自动委派强度（跳过会自动派生多层子代理的 `ultra`） |
| **核心求解器实现与测试验证** | “实现二维 Euler 方程 HLLC 界面通量并跑通守恒单测” | `role:code` 配合 `gate`，限制 `--write-scope` 可写路径并强制通过非空 `--verify-cmd` 测试命令 |
| **关键代码交叉审查与文档独立检查** | “独立审查 `src/flux/` 边界相容性”“检查文档是否存在未定义缩写” | 优先跨客户端调用 `role:review` 或双路 `audit`；文档可读性使用 `--clean-room` 空上下文检查 |
| **格式初筛与低成本检查** | “快速扫一遍目录结构与明显拼写错误” | `role:fast`，使用轻量快速模型低消耗完成，不替代正式数学与代码审查 |
| **文档整理与供应商配置卡切换** | “重新整理说明文档并核对源码参数”“切换到备用供应商卡片” | 文档重写或 `polish` 后运行 `check-grounding --strict` 校验；切卡由 `ccswitch_admin.py` 在线备份数据库后执行 |

### 3. 多路与可写任务的执行前确认和完成后核验

- **执行前确认**：无论是客户端内部多路或可写子代理（`invoke_subagent`、`run_workflow`、`swarm`、`spawn_agent`、`Agent`），还是外部无头命令（`batch`、`gate`、`audit`），启动前均由 `cae_harness.py plan` 生成任务背景、目标、预计影响与精简摘要 `compact_summary_card`，并将完整 14 项参数明细写入本地 `<ticket_id>_card.md` 文件（同时附带离线能力记录日期）。用户确认并执行 `--approve-ticket` 登记后，钩子才放行启动。
- **压缩与跨对话恢复**：等待确认期间若发生自动压缩或会话交接，待确认票据会从磁盘独立恢复或完整写入【新对话提示词】；新对话可通过 `plan --resume-ticket` 读取复核。
- **完成后结果核验**：多路或可写任务结束时，回合结束钩子自动检查回复是否写明成败结论、Token 用量（客户端未返回具体数值时须如实说明无法统计）以及回执文件名或轨迹标识，漏写时提示补全。

## 四、配置仓库维护与模型更新

打开本配置仓库自身时，系统识别为上游配置仓库模式（`workspace_mode: "upstream_config_repo"`），可通过 `fleet-evolve` 技能更新模型推荐配置或维护仓库代码。**在普通业务仓库中调用已安装的 `fleet_state.py` 会直接返回 `not_upstream_config_repo`（退出码 `2`）拒绝执行**，避免日常项目误改全局配置。

### 1. 三类维护操作

| 维护类型 | 对话指令示例 | 适用场景与执行内容 |
| --- | --- | --- |
| **全面更新**（`full`） | “检查最新客户端与模型版本，更新五类角色推荐配置” | **模型换代、客户端升级或增减订阅时使用**。联网核查客户端与模型更新，探测本机可用性与额度类型，生成启停列表与角色推荐方案供确认 |
| **代码与文档整理**（`hygiene`） | “全仓运行静态检查、回归单测并核对文档与源码一致性” | **模型配置不变、排查缺陷或整理文档时使用**。基于现有配置修复代码问题、更新文档、执行脱敏检查、跑通回归测试与 `check-grounding --strict` |
| **只读检查**（`audit`） | “检查本机四端配置是否一致以及科学计算包 ABI 状态” | **日常环境检查时使用**。只读运行 `--probe-apps`、`--audit-upgrade`、`cae_harness.py probe` 与 `check-grounding --strict`，不修改任何文件 |

### 2. 四项人工确认要求

在本配置仓库执行维护时，以下四类操作必须先展示变更内容并取得用户明确同意后才会执行：

1. **修改角色推荐、运行参数或订阅启停状态**：先通过 `fleet_state.py propose` 登记具体方案；停用的客户端或模型在 `subscription_matrix` 中设为 `enabled: false` 后，后续选路自动跳过。
2. **写入本机客户端全局配置目录**：先运行 `install.py --dry-run --yes` 展示拟写入路径与自定义规则保留结果，确认后再写入。
3. **跨版本升级编译型科学计算依赖环境**：涉及系统级 Python 跨次版本、CUDA 跨大版本或 WSL 跨 LTS 版本升级时，须先核查编译型科学计算包 ABI 兼容性并单独确认。
4. **推送远端仓库**：本地测试、文档核对与脱敏检查通过后，经明确同意才执行 `git push`。

当配置更新涉及当前正在运行的主客户端自身时，通过 `fleet_state.py` 的 `record-pass1`、`verify-pass2` 与 `clear --verified --readback-done` 在重启前后分两步核验并校验文档一致性。

## 五、工作原理与四端协议适配

### 1. 固定代码与动态配置文件分离

![固定机制层、动态配置层与全通道派工架构](docs/assets/architecture-overview.svg)

- **主体代码不写死模型名称**：全局规则模板（`R0–R11`）、钩子脚本与 `hooks/_lib/` 公共库、`readonly-*` 只读子代理、`cae_harness.py`、`install.py`、`fleet_state.py`、`ccswitch_admin.py` 及可选本地路由 `router.py` 只引用角色名称与配置键名。
- **模型与运行参数集中存放在 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json)**：统一管理 `orchestrator`、`math`、`code`、`review`、`fast` 五类角色候选、`runtime_tunables` 运行阈值、`subscription_matrix` 启停状态与 `capability_ledger` 评估日期；`hooks/_lib/adaptive_policy.py` 根据当前客户端、模型上下文窗口与推理强度自动缩放压缩恢复预算、交接阈值及 CC Switch 代理卡默认参数。
- **维护期与执行前两阶段可用性检查**：维护期通过 `fleet-evolve` 联网核查能力并记录评估日期，日常任务不再联网查询榜单；每次执行多路任务前，`cae_harness.py plan` 纯离线检查本机可执行程序、凭据配置、本地回环路由状态及本地可读配额记录，过滤不可用或已超限的通道（服务端未公开实时余额时如实标为未知）。

下表为仓库默认随附的五类角色配置示例（实际运行时以本机已安装且启用的客户端为准）：

| 角色名称 | 默认示例客户端与模型系列 | 默认强度 | 适用任务与边界 |
| --- | --- | --- | --- |
| `orchestrator` | Antigravity，`carbon` 系列 | `high` | 主对话需求沟通、方案权衡与任务协调；配置矩阵声明 `1,000,000` 窗口（未声明模型按保守分片预算规划） |
| `math` | Codex，`astra` 系列 | `max` | PDE 离散格式推导、守恒与熵稳定证明；显式传入声明最大窗口，跳过 `ultra` 自动委派挡（常规分片亦可按需指定 `high` 兼顾单轮耗时） |
| `code` | Codex，`sol` 系列 | `max` | 核心求解器编码与 `gate --verify-cmd` 测试验证；限制 `--write-scope` 可写文件范围 |
| `review` | Codex `sol` 系列（备选含 DSH、Antigravity、Claude Code） | `max` | 优先跨客户端交叉审查（同端 `readonly-*` 仅做摘录与行号初筛）；拒绝用轻量模型替代主力审查 |
| `fast` | DeepSeek Harness，`deepseek-flash` | `low` | 用户要求节省额度或做表面格式初筛；不用于核心数学与代码正确性审查 |

### 2. 共用钩子脚本与四端事件映射

![会话确认、压缩接力与完成核验流程](docs/assets/lifecycle-and-verification.svg)

`template/user-guard/hooks/` 包含 5 个顶层钩子脚本与 `hooks/_lib/` 公共库目录（`fs_safety.py` 跨平台文件锁与安全原子写、`shell_lex.py` Shell 语句拆解、`adaptive_policy.py` 按客户端与模型动态计算阈值，配合 `user_words.py` 完成轨迹解析与密钥脱敏）：

1. **压缩恢复**（`session_context_loader.py` + `handoff_gate.py`）：未发生压缩且未达交接阈值的普通回合零字节注入，保持服务端前缀缓存命中；发生压缩或恢复时，按当前客户端、模型与推理强度动态计算字符上限，恢复历轮用户原话与关键子代理结论（并通过 `collapse_historical_cards` 折叠历史确认单表格防止重复占用上下文），同时从磁盘独立恢复未决票据。
2. **执行前安全与票据检查**（`status_guard.py` + `handoff_gate.py`）：静态解析文件工具、Shell 包装命令、重定向与内联 Python 语法树，拦截未授权的项目外写删与外部 `git worktree`、拦截重复读取未变化日志、拦截绕过 `cae_harness.py` 直接调用底层无头 CLI 并核验 `APPROVED` 票据。
3. **长对话交接保护**（`handoff_gate.py`）：会话体积或压缩次数达到收尾阈值（`drain` 或 `freeze`）时限制新派工，并在回合结束检查【新对话提示词】是否包含完整待办工作流、参数表与票据号。
4. **回合结束检查**（`reply_repeat_guard.py` + `handoff_gate.py`）：拦截连续无新进展的重复短句；多路或可写任务结束时在 `Stop` 阶段核对成败结论词、Token 说明与回执标识。
5. **文档与源码一致性检查**（`check-grounding --strict`）：直接扫描当前仓库源码、配置与测试文件提取合法命令行选项、文件名、全大写缩写与技术词干，拦截文档中无代码依据的参数或虚构缩写（静态词法检查，不替代数学推导与算例实跑验证）。

安装器按四个客户端各自的原生配置格式接入上述机制：

| 客户端 | 接入方式与挂载事件 | 默认行为与适配说明 |
| --- | --- | --- |
| **Antigravity** (`agy`) | 原生插件挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类事件（运行态写入 `PLUGIN_DATA`） | 默认开启长对话分段交接提示；遇到 `truncated_fields` 自动回查 `transcript_full.jsonl` |
| **Codex** (`codex`) | `~/.codex/hooks.json` 挂载 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件 | 默认交接模式为 `advisory` 提醒；钩子实质内容未变时不重复改写文件以保留信任状态 |
| **Claude Code** (`claude` 或 `cc`) | `~/.claude/settings.json` 合并上述五类事件，默认随钩子同步 `ccswitch_admin.py` | 默认交接模式为 `advisory` 提醒；可按需启用本地 `127.0.0.1` `router.py` 协议转换路由 |
| **DeepSeek Harness** (`dsh`) | 当前官方 CLI 未开放交互式事件钩子接口，安装器同步全局 `AGENTS.md` 与 `skills/`（后续若官方 CLI 开放交互式事件钩子接口，由 `install.py` 自动探测并纳入同步） | 通过 `cae_harness.py` 无头调用时自动适配新旧 CLI 权限参数，同样具备可用性检查、只读行号内联、空上下文检查、尾随工具标记（`DSML`）清理与文件哈希快照校验 |

## 六、文档索引与目录结构

| 查阅内容 | 对应文档 |
| --- | --- |
| **指令与子命令对照、14 项参数说明、Euler HLLC 示例、中断恢复与四级（L1–L4）验证流程** | [日常工作流实战指南](docs/workflow-guide.md) |
| **钩子模块分工、`runtime_tunables` 参数说明、压缩恢复细节、分片预算公式、本地路由与卸载清理** | [架构设计与钩子说明](docs/architecture-and-hooks.md) |
| **`cae_harness.py` 子命令（`probe`、`assess`、`plan`、`batch`、`gate`、`audit`、`task`、`check-grounding`）说明** | [cae-harness 技能规程](template/user-guard/skills/cae-harness/SKILL.md) |
| **上游仓库维护流程（`full`、`hygiene`、`audit`）、`fleet_state.py` 状态锁与 `install.py` 维护说明** | [fleet-evolve 维护规程](template/user-guard/skills/fleet-evolve/SKILL.md) · [仓库维护说明](AGENTS.md) |
| **插件模板目录说明与版本变更历史** | [插件模板说明](template/user-guard/README.md) · [更新日志](CHANGELOG.md) |
| **V1.0.0 存档时间戳、Concept DOI 与 Version DOI 说明及后续发版流程** | [时间戳与 DOI 说明](.timestamps/README.md) |

```text
Antigravity_Codex_ClaudeCode_DeepSeekHarness/
├── README.md                              # 仓库说明：快速上手、适用场景、安装选项、日常用法与工作原理
├── AGENTS.md / CHANGELOG.md               # 上游配置仓库维护边界说明与版本变更记录
├── LICENSE / NOTICE                       # Apache License 2.0 许可证与第 4(d) 条原创归属声明
├── .github/workflows/ci.yml               # 跨平台离线回归测试与文档一致性检查流水线
├── .timestamps/                           # RFC 3161 时间戳凭证与 DOI 发版说明
├── install.py / install.ps1 / install.sh  # 跨平台安装脚本、配置漂移检查与科学计算包 ABI 风险检查
├── docs/                                  # 日常工作流指南、架构与钩子说明及 3 张 SVG 结构流程图
├── template/
│   ├── user-guard/                        # 共用规则 (R0-R11)、5 个钩子脚本与 hooks/_lib/ 公共库、只读代理、fleet_profile.json 与双技能
│   ├── codex/                             # Codex 事件钩子模板与 readonly-* 只读子代理配置
│   └── claude/                            # Claude Code 事件钩子模板、只读代理及 router/ 本地路由与切卡脚本
└── tests/                                 # 多平台安装、钩子安全边界、调度器与文档一致性离线测试套件
```

## 七、致谢、英文概览与开源许可

### 1. 资助致谢

本项目获**国家留学基金管理委员会**（简称**国家留学基金委**，**China Scholarship Council, CSC**）资助，学号为 `202506280162`，批准文号为 `留金选[2025]37号`；该项资助为本项目目前的唯一资助来源。

### 2. English Overview

`Antigravity_Codex_ClaudeCode_DeepSeekHarness` provides shared engineering rules, deterministic session lifecycle hooks, read-only review subagents, lexical grounding checks, and cross-client headless verification workflows across **Google Antigravity** (`agy`), **OpenAI Codex** (`codex`), **Claude Code** (`claude` / `cc`), and **DeepSeek Harness** (`dsh`). Built solely on the Python 3.11+ standard library across Windows, WSL2, and Linux, it targets scientific computing, CAE, CFD, FEA, numerical PDE solvers, HPC, and high-reliability software engineering—preserving official client prefix caching and subscription quotas while preventing post-compaction instruction loss, silent model substitution, tolerance relaxation, out-of-workspace writes, and ungrounded documentation drift.

Running `install.py` once installs the rules, hooks, and skills into your local client configuration directories so any research or engineering repository (`general_project`) works immediately without copying config files. Explicit user model or client choices take top priority alongside five role slots (`orchestrator`, `math`, `code`, `review`, `fast`), while multi-stream or write-capable tasks write full 14-item parameters to `<ticket_id>_card.md` and present a concise summary before execution. Opening this repository itself (`upstream_config_repo`) enables configuration maintenance via `fleet-evolve` (`full`, `hygiene`, `audit`) with explicit user confirmation gates.

Network access, official client installations, and credentials are prepared by the user. This project is currently supported solely by the China Scholarship Council (CSC), CSC No. `202506280162`, Approval No. `留金选[2025]37号`. Licensed under the [Apache License 2.0](LICENSE); redistributions and derivative works must retain the attribution notices in [NOTICE](NOTICE) (`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`) per Section 4.

### 3. 开源许可

本项目采用 [Apache License 2.0](LICENSE) 开源许可，版权归属 `Copyright 2026 Xinyu Li (Xinyu-Li-1997)`。复制或分发本仓库及其衍生作品时，须遵守协议第 4 条及第 4(d) 条，附带许可证副本并保留 [NOTICE](NOTICE) 中的完整原创归属声明。
