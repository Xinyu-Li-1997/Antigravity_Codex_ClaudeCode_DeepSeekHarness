# 四客户端配置仓库维护说明

本仓库 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 维护科学计算、CAE 和高可靠系统软件工程使用的共用全局规则、技能、只读子代理、会话生命周期钩子与后台验证工具。实现仅依赖 Python 3.11+ 标准库。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`。课题项目直接使用已安装配置，不复制上游模板。

## 内容归属与维护范围

[全局规则模板](template/user-guard/rules/AGENTS.md) 是工程规则的唯一来源。本文件说明仓库维护和安装入口；[cae-harness 技能](template/user-guard/skills/cae-harness/SKILL.md) 负责日常派工、可续接无头会话、确认票据及事实锚定；[fleet-evolve 技能](template/user-guard/skills/fleet-evolve/SKILL.md) 负责全局工具链评估、审批和同步。不要在这些文件中重复维护完整规则。

[fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 统一保存 `orchestrator`、`math`、`code`、`review`、`fast` 五个动态角色槽位的候选、声明窗口和推荐挡位，以及 `native_agent_profiles`、`runtime_tunables`、`subscription_matrix`、`capability_ledger`。常驻文档引用角色与配置键，不写时期性模型名、静态性能日期或插件版本数字。版本以 `template/user-guard/plugin.json` 为准，历史由 Git 和 `CHANGELOG.md` 记录。

### 仓库分层与文档治理（宪法总控边界）

本仓库按三层职责严格切分，避免主体代码、动态元配置与宿主分发产物混写：

| 层次 | 归属路径 | 维护与更新边界 |
| --- | --- | --- |
| 项目主体代码层 | `install.py`、`template/user-guard/hooks/`（含 `_lib/fs_safety.py`、`shell_lex.py`、`adaptive_policy.py`、`process_guard.py`）、`cae_harness.py`、`fleet_state.py`、`ccswitch_admin.py`、`router.py`、`tests/` | 受 Git 与全量回归及 CI 门禁约束，仅通过工程重构或经批准补丁修改，不在日常会话中自改写（`adaptive_policy.py` 与 `process_guard.py` 读取动态配置派生运行期阈值与资源护栏）。 |
| 项目自更新层 | `fleet_profile.json`、`fleet-evolve`、`install.py --audit-upgrade` | `fleet_profile.json` 由 `fleet-evolve` 评估并经用户确认后更新，驱动模型矩阵、自适应策略参数及升级体检。 |
| 分发给其他 Agent 的配置层 | `~/.gemini/config/plugins/user-guard/`、`~/.codex/`、`~/.claude/`、`~/.dsh/` | 由 `install.py` 按宿主协议分发同步（并保留用户自定义尾段），属于部署产物，不在目标端手工硬改主干逻辑。 |

`workspace_mode` 区分两类工作区。`general_project` 是通用课题项目，使用已安装的全局规则、钩子、只读代理、`cae-harness` 和 `ccswitch_admin.py`；`upstream_config_repo` 是上游配置仓库，另有 `fleet-evolve`、`fleet_state.py` 和 `install.py`。工作区或父目录同时包含 `install.py` 与 `template/user-guard/plugin.json` 时识别为上游。已安装到独立目录的 `fleet_state.py` 在通用目录未指定 `--repo` 调用时返回 `not_upstream_config_repo`，退出码为 `2`；直接调用上游仓内脚本时，还会沿脚本路径识别上游仓库。

修改前确认本轮授权和现有 Git 状态，保留其他任务的改动。全局写入、推荐矩阵及运行参数调整、ABI 强耦合升级和 `git push` 均须取得相应批准。范围受限的工作包只修改列明文件，不因维护技能的通常流程扩大写入范围。

## 安装与共有机制同步职责

`install.py` 按所选客户端协议分发共用模板。Antigravity 以独立插件目录 `plugins/user-guard` 同步（不修改用户自有的 `~/.gemini/GEMINI.md`）；Codex、Claude Code 与 DSH 的顶层全局规则文件（`AGENTS.md` 或 `CLAUDE.md`）采用完整标准模板更新，并保留末尾追加的用户自定义段落（包括排在标准 `## 工具与运行环境` 之后的二级标题尾段，或以 `## 工具与` 开头的自定义环境段落），与标准 `R10.`、`R11.` 冲突的自定义条目迁移为 `L1.`、`L2.`。模板标准章节标题 `## 工具与运行环境` 是安装器识别边界，不应当作自定义尾段改名。

下表路径均位于用户主目录。正式写入前先执行只读预览，展示实际目标路径并获批。

| 客户端选项 | 同步内容与宿主协议适配 |
| --- | --- |
| `--app antigravity` | `~/.gemini/config/plugins/user-guard` 中的规则、技能、动态配置、只读子代理与 4 类插件事件钩子；按选项配置 `zotero-mcp`。 |
| `--app codex` | `~/.codex/AGENTS.md`、`agents`（只读子代理定义）、`hooks`、`hooks.json`（5 类事件）和 `skills`，同步 `workspace-write` 工作区沙箱配置并补缺模型窗口配置；按选项配置 `zotero-mcp`。 |
| `--app claude` | `~/.claude/CLAUDE.md`、只读子代理、技能和钩子，向 `settings.json` 合并 5 类事件配置；未配置 `effortLevel` 时按动态配置补齐。 |
| `--app dsh` | `~/.dsh/AGENTS.md` 和 `skills`，复用用户准备的 `DEEPSEEK_API_KEY` 或 `deepseek-account` 凭据，不改 `.credentials.yaml`（当前为软约束 `soft_rules_only`；当官方 CLI 开放硬钩子时由 `install.py` 探测触发元更新）。 |

五个共用钩子脚本模块（`user_words.py` 基础库、`session_context_loader.py`、`status_guard.py`、`reply_repeat_guard.py`、`handoff_gate.py`）及共享基础库 `hooks/_lib/`（`fs_safety.py`、`shell_lex.py`、`adaptive_policy.py`、`process_guard.py`）承载统一的四阶段会话防护，命令行入口在模块导入与配置读取后受 `hook_safety` 墙钟自毁上限保护（POSIX 环境按条件附加虚拟地址空间上限）：

| 共有防护阶段 | 对应共用脚本与宿主事件映射 |
| --- | --- |
| 恢复与调用前 | `session_context_loader.py`（内部联动 `handoff_gate.py`；Antigravity 注册 `SessionStart`、`PreInvocation`；Codex 与 Claude Code 在 `SessionStart` 同时注册两脚本，在 `PostCompact` 注册 `handoff_gate.py`）去噪恢复原话、实质子代理与超时抢救摘要及未决确认单，并评估交接阶段。 |
| 新指令提交时 | `handoff_gate.py`（Codex 与 Claude Code 注册 `UserPromptSubmit`；Antigravity 在 `PreInvocation` 对账轨迹控制步）更新交接判断或暂缓宽限期。 |
| 工具执行前 | `status_guard.py`（内部联动 `handoff_gate.py`；三端注册 `PreToolUse`）核查写边界与同会话外部目录批准租约、日志重复轮询、直接调用底层 CLI 拦截、重负载与 `followup` 票据（`--files` 单路只读按实际声明文件计量）及交接限制。 |
| 回合结束时 | `reply_repeat_guard.py`（内部联动 `handoff_gate.py`，Codex 与 Claude Code 同时注册两脚本；三端挂载 `Stop`）检查重复空等短句、同回合重复刷屏待批票据、局部补测冒充全过、重负载完工回执（按 `run_id` 精确销账）及防翻案核验、强制交接提示词与待拍板占位符保护。 |

宿主事件类别数与共用脚本模块数分别说明，不作简单一一对应。Antigravity 默认启用长会话分段收口；Codex 与 Claude Code 默认交接模式为 `advisory`，强制阻断仅在实际模式要求时生效。

Claude 默认随钩子同步 `~/.claude/hooks/ccswitch_admin.py`，不要求启用本地路由。`--no-claude-hooks` 跳过该同步；`--with-cc-router` 另行分发 `~/.claude/router/`。只有使用相应选项并获授权时，`--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 才会先在线备份 `cc-switch.db`，再停用或清理旧提示词与技能记录。

## 本机配置入口

用户要求配置本机或检查安装状态时，先只读探测：

```powershell
python -B -X utf8 install.py --probe-apps
python -B -X utf8 install.py --audit-upgrade
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --caller auto --redact
```

核对 Windows 与 WSL2 运行侧、`cross_side_status` 双端同步提示、客户端安装、供应商卡片、凭据配置、`fleet_profile.native_agent_profiles_configured`、配置漂移及编译型科学计算包 ABI 风险。程序存在与登录有效分别核实。正式安装前执行只读预览：

```powershell
python -B -X utf8 install.py --dry-run --yes
```

展示完整拟写入路径、模型配置与自定义尾段保留方式；涉及数据库时说明备份位置。`--yes` 是安装器参数，不能代替全局写入授权。获批后才执行对应安装。

## 派工、持久化续接与可用性检查

主会话遇到实质性推导、多文件编码验证或关键代码与文档审查，应主动通过 `cae-harness` 核查可用端。用户点名按 P0 第一优先级锁定指定维度，以 `--user-prompt` 传递原话（仅泛指后端且未唯一锁定单模型时，保留单流已指定的同后端具体模型）。`role:*` 是并列入口，不覆盖点名；未指定的窗口、非委派挡位、无头防分页标志、`--files` 多文件输入与 `--negative-scope-file` 负向契约、只读行号内联、快照（默认忽略常见语言工具链缓存目录）和脱敏由脚本补齐。外部无头会话默认持久化（`--ephemeral` 显式关闭），支持 `followup` 增量追问，并在超时场景下执行软时限自动降档只读收口与硬超时 `timeout_salvage`（优先保留正式助手回复、部分 Token、已读文件与落盘改动，并如实记录总超时与主回合超时）。轻量卡片跑非 `fast` 角色、WSL 纯 Linux 可写路径走 Windows 互操作、Windows `codex.exe` 只读关闭内联或本地路由未存活时明确报错，不静默降级。

可用性经过两道门：第一道门由 `fleet-evolve` 联网评估并由用户决定 `subscription_matrix` 启停；第二道门由 `plan` 纯离线复查当前配置、路由存活及可读额度状态。日常派工读取 `capability_ledger`，不联网更新性能。

重负载涵盖外部无头调用（支持 `--isolation` 四级正交隔离）及内部 `invoke_subagent`、`run_workflow`、`spawn_agent`、`Agent`、`swarm` 多路或可写委派。`plan` 先写 `<ticket_id>.json` 再写 `<ticket_id>_card.md`（含 4 列总览表与 14 项明细），日常对话默认仅展示 3 句话极简确认条 `chat_brief` 与明细链接，后台被动唤醒期间严禁重复贴票。获批后以 `plan --approve-ticket` 登记，执行绑定 `--ticket`；`batch` 支持按 `wave` 波次及同目录不重叠 `write_scope` 并发执行外部无头通道，前序波次同工作区可写流失败时默认阻断后续波次全部流（含只读流，仅显式声明 `run_on_upstream_failure: true` 的只读流放行）。

## 中断恢复与完成回执

等待确认期间发生自动压缩时，恢复钩子按 `hooks/_lib/adaptive_policy.py` 动态缩放预算、过滤异步启动噪音并用 `collapse_historical_cards` 折叠历史明细表；同时从 `<ticket_id>.json` 独立加载精简 4 列总览卡、`<ticket_id>_card.md` 链接与选项卡供模型恢复核对，主会话向用户仅补展 1 次 `chat_brief` 与明细链接。进入 `drain` 或 `freeze` 强制流程时，`handoff_gate.py` 放行 `check-grounding --strict`，并在 `Stop` 核验交接要素与未决拍板占位符保护。

`reply_repeat_guard.py` 在完工 `Stop` 检查成败词（禁止将含失败或超时的账单写成“全部成功”）、局部补测与全量测试区分，以及 Token 用量或无法统计说明。外部任务须覆盖各流标识和账单中的实际回执文件名；单路 `task` 或 `gate` 省略默认流号 `S1` 时须匹配具体模型名，仅在未记录具体模型名时可用任务状态词替代。原生任务须覆盖完成数量、各流标识或各角色名中的一项，并提供日志或轨迹说明。主会话仍须自行逐路核对具体状态、Token 明细与汇总算术。

## 上游维护与验收

上游维护遵循 fleet-evolve 技能，先以 `fleet_state.py status` 核对身份和状态锁。重要文档按需做净室可读性检查、主会话整篇重写与源码事实脱敏核对。验收严禁放宽容差、守恒或收敛阶判据，严禁删改断言。

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 -m unittest tests.test_e2e_integration
```

涉及审批节点用 `fleet_state.py propose` 登记具体方案；全局同步附 `install.py --dry-run --yes` 清单，获批后执行 `approve` 与同步，最后由 `clear --verified --readback-done` 执行事实锚定硬门禁归位。未经明确批准不执行 `git push`。
