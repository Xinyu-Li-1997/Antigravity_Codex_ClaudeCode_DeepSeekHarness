# 面向科学计算与工程研发的四客户端配置与验证工具

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.24293507-blue.svg)](https://doi.org/10.5281/zenodo.24293507)
[![CI](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/actions/workflows/ci.yml/badge.svg)](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness)](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/releases)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B_stdlib-3776AB.svg)](install.py)

本仓库 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 为 **Google Antigravity**（`agy`）、**OpenAI Codex**（`codex`）、**Claude Code**（`claude` 或 `cc`）与 **DeepSeek Harness**（`dsh`）提供一套共用的工程规范、生命周期硬钩子、只读审查子代理与跨端无头验证脚本。

核心设计思路很直接：**模型各有偏科，长对话也一定会丢上下文，靠手写越来越长的提示词祈祷模型自觉并不可靠。** 本仓库把反复踩过的坑尽量下沉成确定性钩子、单测和门禁脚本，再把已经守住的文字规则做薄；单装任意一个客户端即可独立工作，装多个时按专长协作，让合适的端干合适的活。全仓仅依赖 Python 3.11+ 标准库，支持 Windows、WSL2 与 Linux。

## 快速上手

1. **安装到本机客户端配置目录**：先自行安装上述至少一个官方客户端并完成登录（官方 OAuth 或厂商按量 API Key；可选用 CC Switch 管理 Claude Code 兼容接口卡片）。克隆本仓库后运行一次安装脚本：

   ```powershell
   # Windows 使用 python -B -X utf8；WSL2 与 Linux 改用 python3 -B -X utf8（也可运行 install.ps1 或 install.sh）
   python -B -X utf8 install.py
   ```

   安装器会把共用规则、钩子和技能同步到对应客户端的用户目录（`~/.gemini/config/plugins/user-guard`、`~/.codex`、`~/.claude`、`~/.dsh`），**不会往你的业务或课题仓库塞任何配置文件**。同时使用 Windows 与 WSL2 时，请在两侧各跑一次安装和体检。

2. **打开自己的仓库直接对话**：日常工作不需要背命令，系统按任务规模自动分流：
   - **零碎小修与高频对齐**：主窗口直接改完闭环，不走派工流程，省掉沟通和建票摩擦。后台钩子自动放行系统临时目录与包缓存，只拦未授权的项目外越界写删、外部 `git worktree` 和重复读取未变化的日志；你在本会话批准过的已有外部目录，后续同目录常规编辑会自动续租。
   - **成块实现、大仓库阅读与跨端审查**：面对大型仓库或超长文档，可先运行 `cae_harness.py read-plan` 按安全预算与内联字节双口径评估尺寸并切分保守波次和超大单文件连续行段（对话只出 3 句话简报 + 清单链接）；委派多路任务、可写无头任务或超轻任务阈值前，脚本先把 4 列总览和 14 项明细落盘到本地 `<ticket_id>_card.md`，聊天框里只给你看 **3 句话确认条（`chat_brief`）+ 明细链接**，绝不贴长表刷屏。你回复确认并登记批准后才放行执行；`gate --verify-cmd` 会自动跑验收命令，不通过则带真实报错迭代修正。派单完工自动落盘确定性机械对账表（`<run_id>_reconcile.md`），也可随时运行 `reconcile` 或 `cross-check` 核对变更与多端覆盖。
   - **长对话自动压缩**：触发压缩时，钩子自动补回历轮关键原话、重要子任务结论、完整未决确认单、未报账单的机械对账链接与活跃阅读计划指针，避免写到一半忘了物理符号约定或数值容差。

3. **随时点名模型或检查配置漂移**：想指定哪一端干活，直接在对话里说即可（例如“用 Codex 高强度查 `src/flux/`”）。客户端升级或调整订阅后，回到本仓库执行 `python -B -X utf8 install.py --audit-upgrade`，即可一次核查配置漂移、Windows/WSL2 双端对齐状态和编译型科学计算包的二进制接口（ABI）风险。

## 目录导航

- [快速上手](#快速上手)
- [一、这套工具解决什么问题](#一这套工具解决什么问题)
- [二、安装与配置选项](#二安装与配置选项)
- [三、日常怎么协同干活](#三日常怎么协同干活)
- [四、配置仓库维护与演进](#四配置仓库维护与演进)
- [五、底层原理与四端适配](#五底层原理与四端适配)
- [六、文档索引与目录结构](#六文档索引与目录结构)
- [七、致谢、英文概览与开源许可](#七致谢英文概览与开源许可)

## 一、这套工具解决什么问题

本工具面向**科学计算、计算机辅助工程（CAE）、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值离散、高性能计算（HPC）及高可靠系统软件工程**。

它不魔改官方客户端内核，也不破坏客户端自带的交互循环和服务端前缀缓存；而是通过 `R0–R11` 精简规则、四阶段硬钩子和调度脚本 `cae_harness.py`，把工程研发里最容易出事故的六个环节变成机械检查：

| 常见工程痛点 | 裸用客户端时容易出什么事 | 本仓库怎么兜底 |
| --- | --- | --- |
| **长对话压缩后失忆** | 自动摘要丢掉早期法向约定、符号定义或容差；大量短回执把关键审查行号挤出窗口 | 压缩恢复分块还原：原话与实质子任务结论按模型窗口动态缩放；磁盘未决票据总览与 `<ticket_id>_card.md` 链接完整保留；完工账单（含 `reconcile_file`）与活跃 `read-plan` 指针有界带回防膨胀 |
| **大仓库塞爆上下文或跳行漏读** | 一股脑全读大仓库或几百 KB 长文档引发频繁压缩、中途跳行丢关键条款；跨端全量重复读烧穿额度 | 读前运行 `read-plan` 按保守单片 Token 与真实 UTF-8 带行号内联字节双口径切分保守波次，超大单文件按标题或顶层定义切成带重叠连续行段，核心片多端盲读、其余分担，并如实披露被跳过的归档/历史目录 |
| **多端共同盲区与改漏配套文档** | 多路审查收到同一残缺信封形成共同盲区，或按行合并掩盖相反观点；改完源码忘了同步配套文档与测试，靠 AI 总结报喜不报忧 | `cross-check` 确定性生成计划分母 × 执行端覆盖矩阵、全端共同盲区（`shared_blind_spots`）、失败/超时格与分歧并列清单；`reconcile` 与派单完工硬触发确定性机械对账表（基于冻结基线、Python AST 导入、Markdown 显式链接与反引号符号引用），零上下文刷屏 |
| **派工成本失控或超时白干** | 小修改硬派后台浪费沟通成本；点名模型被静默换掉；长任务一超时所有中间推演和账单全丢 | 小修直接主控闭环，接口清晰的成块模块再跨端派工；点名按 P0 锁定绝不静默降级；无头会话默认持久化、到软时限先降挡紧凑收口、硬超时回收 `timeout_salvage` 并支持 `followup` 续查 |
| **过度挑刺或放宽验收底线** | 审查靠极端假想反复打回拖垮节奏；或为了过测试擅自放宽 `rtol`、拿局部补测或零复跑冒充全套通过 | 审查区分**阻断级**（有明确触发或复现链路的实质缺陷）与**建议级**（仅记录），常规审查用 `high` 挡、高危核心自动升 `max` 挡，门禁默认 2 轮封顶；`R3.6` 禁止放宽容差，`Stop` 钩子拦零复跑/局部补测冒充全过、同句矛盾宣称全过与漏报对账链接 |
| **只读审查顺手改代码或越界** | 让模型读代码却顺手改了文件、写到项目外目录，或死循环轮询未变化的日志 | `readonly-*` 代理禁写禁测；外部只读任务前后比对自适应文件快照（中小目录逐字节 SHA-256，大目录切对称元数据快快照）；`PreToolUse` 静态拦截项目外越界写删与空转轮询 |
| **文档越堆越重、越改越失真** | 每次踩坑都往规则文件叠补丁，上下文越来越重；润色文档时编出源码不存在的参数或缩写 | `R0` 要求纠错优先下沉为单测和钩子守卫，已被守住的文字及时减薄；`check-grounding --strict` 扫描源码与配置核对参数和术语依据，配合 `--isolation clean_room` 盲读检查可读性 |
| **模型换代或升级把环境弄坏** | 模型名写死在脚本里很快过期；系统级 Python 跨次版本升级弄崩编译好的科学计算扩展 | 固定代码与 `fleet_profile.json` 动态配置完全分离；`install.py --audit-upgrade` 统一检查四端漂移、双端同步缺口及科学计算包 ABI 兼容风险 |

## 二、安装与配置选项

本仓库**不内置任何第三方反代，也不创建或修改账号凭据**。建议按下面四步执行探测、预览、安装和核验：

```powershell
# Windows 使用 python -B -X utf8；WSL2 与 Linux 改用 python3 -B -X utf8
python -B -X utf8 install.py --probe-apps        # 1. 只读探测本机客户端、配置目录、DSH 钩子协议与 WSL2 状态
python -B -X utf8 install.py --dry-run --yes     # 2. 只读预览将要写入的文件路径（不改磁盘）
python -B -X utf8 install.py                     # 3. 交互确认后幂等同步已安装客户端（非交互可加 --yes）
python -B -X utf8 install.py --audit-upgrade     # 4. 体检配置对齐（summary.all_aligned）、双端状态与 ABI 风险
```

- **按需控制同步范围**：默认 `--app auto` 只同步本机已装客户端；`--app all` 覆盖全部四端，也可单独指定 `antigravity`、`codex`、`claude`、`dsh` 或 `both`（Antigravity + Codex）。若为 Claude Code 启用本地 `127.0.0.1` 分流路由或处理 CC Switch 旧记录，可按需附加 `--with-cc-router`、`--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy`（改动前自动在线备份 `cc-switch.db`）。
- **看懂 `--audit-upgrade` 的体检建议**：若返回 `summary.all_aligned: false`，照着 `summary.recommended_actions` 处理即可：
  - 提示 `refresh_codex_model_catalog`：先登录一次客户端生成本地模型目录缓存；
  - 提示 `sync_codex_model_defaults` 或 `sync_claude_model_defaults`：希望同步推荐默认模型与强度时，加 `--sync-model-defaults` 重跑 `install.py`；
  - 提示 `start_cc_router`：按需拉起本地 `router.py`；
  - 提示 `upgrade_dsh_hook_protocol`：说明官方 DSH CLI 已开放硬钩子，可纳入上游演进；
  - 若 `cross_side_status.dual_side_sync_required` 为 `true`，请在 Windows 与 WSL2 两侧分别同步。
- **保留你的自定义规则**：Antigravity 安装在独立插件目录 `plugins/user-guard`，不碰 `~/.gemini/GEMINI.md`；Codex、Claude Code 与 DSH 更新标准规则至 `## 工具与运行环境`（含 `R10.`、`R11.`），自动保留你写在标准段之后的自定义章节（以及标题不同的 `## 工具与...` 自定义段落），并将冲突编号迁移为 `L1.`、`L2.`。
- **外部工具边界与纯按量接口用法**：请始终使用客户端官方 OAuth 登录或厂商签发的按量 API Key，不要用外部工具读写官方 OAuth 凭据、轮询非公开配额接口或向反重力同步提示词。为 Claude Code 切换 CC Switch 卡片时，建议用 `ccswitch_admin.py switch-card`（或在界面切卡后补跑一次 `install.py`）以防冲掉 `~/.claude/settings.json` 中的五类钩子。不用的客户端可在 `fleet_profile.json` 的 `subscription_matrix.agents` 中置 `enabled: false`，选路会自动跳过。

## 三、日常怎么协同干活

打开任意科研或工程仓库时，系统自动工作在通用课题模式（`workspace_mode: "general_project"`）。日常把控一条很朴素的分工原则：**主会话负责理解需求、做小修改、定接口与亲自核对原文行号；成块独立推导、核心求解器实现、大范围扫描与独立审查交后台子进程隔离执行，只回收干净的结构化回执。**

![日常课题协作与配置仓库维护流程](docs/assets/user-and-evolution-flow.svg)

### 1. 明确点名（P0 最高优先级）

后台调度涵盖**外部无头子进程**（`cae_harness.py` 启动的全新非交互 CLI，看不到主会话历史）和**客户端内部原生子代理**（Antigravity 的 `invoke_subagent`/`run_workflow`/`swarm`，Codex 的 `spawn_agent`，Claude Code 的 `Agent`/`Task`）。

你在对话里直接说出想要的客户端、模型、挡位、隔离级别或省额度意图，调度器通过 `--user-prompt` 按 P0 锁定，绝不被默认角色覆盖：

| 你怎么说 | 锁定的维度 | 脚本自动兜底 |
| --- | --- | --- |
| “这步编码用 Codex 高强度做”“用 DSH 独立核对脱敏与公式” | 指定客户端、模型或模型系列 | 自动补齐声明最大窗口、非交互标志、分后端只读内联、自适应快照与脱敏；目标不可用或已停用时直接报错 |
| “挂 high 挡跑”“用 `--isolation clean_room` 盲读 README” | 指定挡位或四级隔离（`full`、`no_skills`、`hooks_only`、`clean_room`） | 非 `fast` 专业角色即使降到 `low` 仍保留主力模型；不支持所选隔离级别时显式拒绝 |
| “省点额度快速扫一遍格式”“加 `--fallback-on-quota` 遇限额回退” | 低消耗策略或零改动时的配额回退 | 遇 `quota_exhausted` 立即停手不盲目重试；仅未锁定单端、未绑已批票据且工作区零改动时按配置回退 |
| “用 Codex 和 DSH 交叉审查这部分代码” | 识别“和、与、及、等”并列语义，不误锁单端 | 完整保留多路计划中各分支各自指定的客户端、模型与隔离级别 |

### 2. 不点名时的按专长协同

只说目标不点名时，系统按 `orchestrator`、`math`、`code`、`review`、`fast` 五类角色从本机可用端里自动分配：

| 常见工作场景 | 推荐协同方式 | 背后的工程逻辑 |
| --- | --- | --- |
| **零碎小修与高频对齐** | 主窗口直接闭环（`role:orchestrator`） | 几十行改动和来回对接口不值得花派工沟通成本 |
| **重大架构与建模路线抉择** | `batch` 或 `clean_room` **无草稿盲出独立双方案** | 只喂用户原话与必要背景，不给主控草稿，避免第二个模型被先入为主带偏 |
| **文献摘录与大源码定位** | 同端 `readonly-*` 子代理并行读原文 | 禁写禁测、只带回原文摘录与精确行号，不替代跨端审查 |
| **大仓库与超长文档防爆窗阅读** | `read-plan` 读前双口径分片 + 多端 `batch` + `cross-check` 汇总核对 | 先核算保守单片预算与带行号内联字节，超大文件自动切带重叠连续行段；核心片跨端盲读，完工由确定性代码查覆盖矩阵、共同盲区与分歧 |
| **公式推导与高风险命题攻坚** | `role:math` 单模型高强度推导，或正反双流对抗 | 缺乏现成单测时，由审慎模型正向立论 + 高算力模型找反例和奇点边界 |
| **成块核心模块实现与验证** | `role:code` 配合 `cae_harness.py gate` | 写清接口与验收标准，锁定 `--write-scope` 白名单并强制跑通 `--verify-cmd`，默认最多 2 轮不过交人拍板 |
| **独立代码与文档审查** | 跨端 `role:review` 或双源 `audit`（文档可读性加 `clean_room`） | 区分阻断级与建议级发现；常规审查默认 `high` 挡，涉及守恒律、离散算子、边界条件或权限安全自动升 `max` 挡 |
| **表面格式初筛与润色核验** | `role:fast` 快检；`polish` 润色后跑 `check-grounding --strict` | 低成本扫表面问题，不冒充核心审查；润色结果经词法锚定防编造参数 |

### 3. 读前评估、执行前确认与完成后机械对账

- **大读先量尺寸、确认单不刷屏**：面对大目录或超长文档，先跑 `read-plan` 评估体积、自动切分超大文件行段与保守并发波次，并落盘完整清单与可选 `--emit-spec` 工作流配置；多路并发、可写任务或超轻任务阈值前，`plan` 把完整 4 列总览和分路 14 项明细写进 `<ticket_id>_card.md`（可选 `--obligations-file` 绑定显式义务），聊天框里只显示 3 句话确认条 `chat_brief` 和可点击链接；批准并执行 `plan --approve-ticket` 后，钩子才放行启动。
- **压缩与交接不断链**：等审批时遇到自动压缩，钩子直接从磁盘完整加载未决票据总览与明细链接，并有界带回未报账单的对账表链接与活跃阅读计划指针，只向你补展 1 次 `chat_brief`；进入强制交接时，【新对话提示词】自动附带总览、明细链接和 `plan --resume-ticket` 复核命令，同时放行 `reconcile`/`read-plan`/`cross-check` 等纯诊断核对。
- **完工硬出机械对账表与三端核账**：重负载派单完工硬触发生成确定性机械对账表落盘至 `<plan_dir>/reconcile/<run_id>_reconcile.md`（不污染目标业务仓库），你也可随时点名运行 `reconcile` 或 `cross-check` 核对实改与未更新文件、强显式依赖、跨端覆盖矩阵、共同盲区与分歧。Antigravity、Codex、Claude Code 三端 `Stop` 钩子统一核对成败结论（拦截含失败项却同句宣称“全部成功”）、Token 用量（兼容 Markdown 表格账单，无法统计须如实写明）、同名回执消歧与 `reconcile_file` 查看链接，漏报或把未全量复跑的测试写成全绿时拦截补正。

## 四、配置仓库维护与演进

在本仓库自身目录工作时，系统自动识别为上游配置仓库（`workspace_mode: "upstream_config_repo"`），启用 `fleet-evolve` 维护流程。若在普通业务仓库调用已安装的 `fleet_state.py`，会直接返回 `not_upstream_config_repo`（退出码 `2`），防止误改全局配置。

### 1. 三类维护模式

| 模式 | 什么时候用 | 做什么 |
| --- | --- | --- |
| **全面更新**（`full`） | 模型换代、客户端升级或增减订阅 | 按四维清单（四端版本与协议、Windows/WSL2 双端、内生代理档案、跨端协同）核查能力与本机可用性，产出提案供你决定 |
| **代码与文档整理**（`hygiene`） | 修缺陷、清冗余、重整文档 | 优先把重复踩坑下沉为单测和钩子守卫，删薄已被守住的文字规则，完成脱敏、`check-grounding --strict` 与全量回归 |
| **只读巡检**（`audit`） | 日常体检 | 只读跑 `--probe-apps`、`--audit-upgrade`、`probe` 与 `check-grounding --strict`，零修改 |

### 2. 四道人工拍板门禁

在维护上游仓库时，以下四类操作必须先拿出具体方案，等你点头后才动手：

1. **调整推荐矩阵、内生代理档案、运行阈值或订阅启停**：先用 `fleet_state.py propose` 登记方案；
2. **写入宿主机全局配置目录**：先用 `install.py --dry-run --yes` 展示完整目标路径与自定义段保留方式（双端环境两侧分别同步）；
3. **跨版本升级编译型科学计算依赖**：涉及系统级 Python 跨次版本、CUDA 跨大版本或 WSL 跨 LTS 升级时，先核查 ABI 兼容性；
4. **推送远端仓库**：本地全量回归与脱敏校验全绿后，经明确授权才执行 `git push`。

涉及正在运行的主会话客户端更新时，通过 `fleet_state.py` 的 `record-pass1`、`verify-pass2` 与 `clear --verified --readback-done` 在重启前后两步核验归位。

## 五、底层原理与四端适配

### 1. 固定机制与动态配置解耦

![固定机制层、动态配置层与全通道派工架构](docs/assets/architecture-overview.svg)

- **主体代码不写死时期性模型**：规则模板（`R0–R11`）、钩子与 `hooks/_lib/`、`readonly-*` 代理、`cae_harness.py`、`install.py`、`fleet_state.py`、`ccswitch_admin.py` 与可选本地路由 `router.py` 均按角色和配置键名驱动。
- **动态档案集中存放在 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json)**：统一管理五类角色（`roles`）、四端内生代理档案（`native_agent_profiles`）、运行期定量阈值（`runtime_tunables`）、启停矩阵（`subscription_matrix`）和离线更新时间（`capability_ledger`）；`hooks/_lib/adaptive_policy.py` 据此随当前客户端、窗口和推理强度动态缩放恢复与交接阈值。
- **维护期与派工期两阶段解耦**：维护期才联网更新能力评估，日常派工前由 `plan` 纯离线复查本机可执行程序、凭据、路由健康与可读配额，未暴露的服务端余额如实标为未知。

### 2. 四阶段共用钩子与四端协议映射

![会话确认、压缩接力与完成核验流程](docs/assets/lifecycle-and-verification.svg)

`template/user-guard/hooks/` 包含共用基础库 `user_words.py`（底层调用 `hooks/_lib/` 中的跨平台文件锁、Shell 词法拆解、自适应策略与进程墙钟/内存自毁护栏）以及四个事件阶段门禁：

1. **压缩恢复**（`session_context_loader.py` + `handoff_gate.py`）：普通回合零字节注入不扰动前缀缓存；压缩后按窗口缩放还原原话与关键子任务结论，折叠历史长表，完整加载磁盘未决票据总览，并有界带回未报账单的 `reconcile_file` 链接与活跃 `read-plan` 指针。
2. **工具执行前**（`status_guard.py` + `handoff_gate.py`）：静态解析文件工具、Shell 包装（含 PowerShell 尾部反引号续行归一化）与内联 Python AST，拦截未授权项目外写删、外部 `git worktree`、空转读日志和绕过调度器裸调无头 CLI，核验已批票据、隔离一致性和原生通道容量（含 `swarm add` 按 `tasks` 长度或 `n` 计流数）。
3. **长会话交接**（`handoff_gate.py`）：达到 `drain`/`freeze` 阶段限制新派工（放行 `check-grounding`、`reconcile`、`read-plan`、`cross-check` 等纯诊断核对），在收尾时核验【新对话提示词】内嵌的精简总览、明细链接、恢复命令与待拍板占位符保护。
4. **回合结束**（`reply_repeat_guard.py` + `handoff_gate.py`）：拦截空等刷屏、同回合重复贴票、全量测试失败后零复跑或局部补测冒充全过，以及含失败项却宣称“全部成功”的矛盾表述，按 `run_id` 核验完工成败词、Token 用量、同名回执消歧与 `reconcile_file` 机械对账表链接。外部无头子进程（`CAE_HARNESS_DEPTH`）自动跳过恢复、交接和停顿钩子，仅保留越界写硬阻断。

独立于事件钩子之外，`cae_harness.py` 提供四项纯离线零模型调用的确定性诊断与对账子命令，四客户端完全通用：`check-grounding --strict` 扫描仓库源码与配置做零黑名单词法核验；`read-plan` 做读前双口径预算评估与超大单文件保守层级分片；`cross-check` 核对多端覆盖矩阵、共同盲区与发现分歧；`reconcile` 生成实改与未更新文件、强显式依赖及义务结算机械对账表。

四个客户端接入同一套机制的适配方式如下：

| 客户端 | 接入方式与挂载事件 | 适配要点 |
| --- | --- | --- |
| **Antigravity** (`agy`) | 原生插件挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类事件 | 默认启用长会话分段交接；通过检查点步识别压缩，遇截断字段自动回查 `transcript_full.jsonl` |
| **Codex** (`codex`) | `~/.codex/hooks.json` 挂载 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件 | 默认 `advisory` 提醒；钩子实质内容未变时不重写文件，保护宿主信任记录 |
| **Claude Code** (`claude` / `cc`) | `~/.claude/settings.json` 合并上述五类事件，默认同步 `ccswitch_admin.py` | 默认 `advisory` 提醒；按需启用本地 `127.0.0.1` `router.py` 分流路由 |
| **DeepSeek Harness** (`dsh`) | 当前官方 CLI 为 `soft_rules_only`（未开放交互硬钩子），安装器同步 `AGENTS.md` 与 `skills/` 并探测 `hook_protocol` | 交互窗口靠软规则约束；通过 `cae_harness.py` 无头调用时使用独立 `headless-sessions` 目录，同样具备 `--isolation` 隔离、行号内联、尾随 `DSML` 清洗与自适应快照校验 |

## 六、文档索引与目录结构

| 想看什么 | 去哪找 |
| --- | --- |
| **日常协作选型、无草稿双方案、审查分级、Euler HLLC 任务单模板与 L1–L4 验证矩阵** | [日常工作流实战指南](docs/workflow-guide.md) |
| **钩子模块分工、`fleet_profile.json` 配置键、压缩接力原理、预算公式、本地路由与卸载指南** | [架构设计与钩子说明](docs/architecture-and-hooks.md) |
| **`cae_harness.py` 子命令与 14 项确认参数规范** | [cae-harness 技能规程](template/user-guard/skills/cae-harness/SKILL.md) |
| **上游仓库维护（`full`/`hygiene`/`audit`）、状态锁与安装器维护说明** | [fleet-evolve 维护规程](template/user-guard/skills/fleet-evolve/SKILL.md) · [仓库维护说明](AGENTS.md) |
| **插件模板入口与版本演进记录** | [插件模板说明](template/user-guard/README.md) · [更新日志](CHANGELOG.md) |
| **V1.0.0 时间戳凭证、Concept DOI 与发版流程** | [时间戳与 DOI 说明](.timestamps/README.md) |

```text
Antigravity_Codex_ClaudeCode_DeepSeekHarness/
├── README.md                              # 仓库总览：设计理念、快速上手、日常协同与工作原理
├── AGENTS.md / CHANGELOG.md               # 上游配置仓库维护说明与版本更新日志
├── LICENSE, NOTICE, CITATION.cff          # Apache License 2.0、原创归属声明与学术引用元数据
├── .github/workflows/ci.yml               # 跨平台离线回归测试与文档事实锚定流水线
├── .timestamps/                           # RFC 3161 时间戳凭证与 DOI 发版说明
├── install.py / install.ps1 / install.sh  # 跨平台安装同步、漂移体检、双端对齐与 ABI 风险检查
├── docs/                                  # 日常实战指南、架构与钩子说明及 3 张 SVG 流程图
├── template/
│   ├── user-guard/                        # 共用规则 (R0–R11)、钩子与 _lib/、只读代理、fleet_profile.json 与双技能
│   ├── codex/                             # Codex 事件钩子模板与 readonly-* 只读子代理配置
│   └── claude/                            # Claude Code 事件钩子模板、只读代理及 router/ 本地路由与切卡脚本
└── tests/                                 # 多平台安装、钩子边界、调度器与文档一致性全量离线单测
```

## 七、致谢、英文概览与开源许可

### 1. 资助致谢

本项目获**国家留学基金管理委员会**（简称**国家留学基金委**，**China Scholarship Council, CSC**）资助，学号为 `202506280162`，批准文号为 `留金选[2025]37号`；该项资助为本项目目前的唯一资助来源。

### 2. English Overview

`Antigravity_Codex_ClaudeCode_DeepSeekHarness` brings shared engineering rules (`R0–R11`), deterministic session lifecycle hooks, read-only review subagents, lexical grounding checks, and cross-client headless verification workflows to **Google Antigravity** (`agy`), **OpenAI Codex** (`codex`), **Claude Code** (`claude` / `cc`), and **DeepSeek Harness** (`dsh`). Built solely on the Python 3.11+ standard library for Windows, WSL2, and Linux, it is tailored for scientific computing, CAE, CFD, FEA, numerical PDE solvers, HPC, and high-reliability software engineering.

Instead of piling ever-longer prose onto system prompts, the repository pushes recurring failure modes into deterministic hooks, unit tests, and verification gates while keeping permanent rules lean. Small local edits stay in the main session; well-scoped modules, independent dual proposals (blind of host drafts to prevent anchoring), and cross-client reviews run through `cae_harness.py` with P0 user-override locking, severity-separated findings (blocking vs. advisory), risk-tiered review effort, bounded gate rounds, and bounded post-compaction recovery. Offline deterministic tools—`read-plan` (dual-budget pre-read sizing, oversized file section splitting, and conservative wave sharding), `cross-check` (multi-client coverage matrix, shared blind-spot detection, and divergence preservation), and `reconcile` (dispatch-triggered and on-demand mechanical reconciliation tables verified across hooks)—guard against context overflow, blind spots, and stale documentation across all four clients.

Run `install.py` once (separately on Windows and WSL2 when both are used) to install globally so any project repository (`general_project`) works immediately without copying config files. Opening this repository (`upstream_config_repo`) enables `fleet-evolve` (`full`, `hygiene`, `audit`) with explicit user approval checkpoints. Users provide their own network access, official client installations, and official OAuth sign-in or vendor API keys (avoid external OAuth credential switchers; use `ccswitch_admin.py switch-card` or re-run `install.py` after Claude Code card changes to preserve lifecycle hooks). Supported solely by the China Scholarship Council (CSC), CSC No. `202506280162`, Approval No. `留金选[2025]37号`. Licensed under [Apache License 2.0](LICENSE); retain attribution notices in [NOTICE](NOTICE) (`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`).

### 3. 开源许可

本项目采用 [Apache License 2.0](LICENSE) 开源许可，版权归属 `Copyright 2026 Xinyu Li (Xinyu-Li-1997)`。复制或分发本仓库及其衍生作品时，须遵守协议第 4 条及第 4(d) 条，附带许可证副本并保留 [NOTICE](NOTICE) 中的完整原创归属声明。
