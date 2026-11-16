# 四客户端配置仓库维护说明

本仓库 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 维护科学计算、CAE 和高可靠系统软件工程使用的全局规则、技能、只读子代理、事件钩子与后台验证工具。实现依赖 Python 3.11+ 标准库。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`。课题项目直接使用已安装配置，不复制上游模板。

## 内容归属与维护范围

[全局规则模板](template/user-guard/rules/AGENTS.md) 是工程规则的唯一来源。本文件说明仓库维护和安装入口；[cae-harness 技能](template/user-guard/skills/cae-harness/SKILL.md) 负责日常派工、确认票据及事实锚定；[fleet-evolve 技能](template/user-guard/skills/fleet-evolve/SKILL.md) 负责全局工具链评估、审批和同步。不要在这些文件中重复维护完整规则。

[fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 统一保存 `orchestrator`、`math`、`code`、`review`、`fast` 五个角色槽位的候选、声明窗口和推荐挡位，以及 `runtime_tunables`、`subscription_matrix`、`capability_ledger`。常驻文档引用角色与配置键，不写时期性模型名、静态性能日期或插件版本数字。版本以 `template/user-guard/plugin.json` 为准，历史由 Git 和 `CHANGELOG.md` 记录。

`workspace_mode` 区分两类工作区。`general_project` 是通用课题项目，使用已安装的全局规则、钩子、只读代理、`cae-harness` 和 `ccswitch_admin.py`；`upstream_config_repo` 是上游配置仓库，另有 `fleet-evolve`、`fleet_state.py` 和 `install.py`。工作区或父目录同时包含 `install.py` 与 `template/user-guard/plugin.json` 时可识别为上游。已安装到独立目录的 `fleet_state.py` 在通用目录未指定 `--repo` 调用时返回 `not_upstream_config_repo`，退出码为 `2`；直接调用上游仓内脚本时，还会沿脚本路径识别上游仓库。

修改前确认本轮授权和现有 Git 状态，保留其他任务的改动。全局写入、推荐矩阵及运行参数调整、ABI 强耦合升级和 `git push` 均须取得相应批准。范围受限的工作包只修改列明文件，不因维护技能的通常流程扩大写入范围。

## 安装与同步职责

`install.py` 按所选客户端分发模板。全局规则采用完整标准模板，并保留末尾的用户自定义环境段落；与标准 `R10.`、`R11.` 冲突的自定义条目迁移为 `L1.`、`L2.`。模板的标准章节标题 `## 工具与运行环境` 是安装器识别边界，不应当作自定义尾段改名。

下表路径均位于用户主目录。正式写入前先空跑，展示实际目标路径并获批。

| 客户端选项 | 同步内容 |
| --- | --- |
| `--app antigravity` | `~/.gemini/config/plugins/user-guard` 中的规则、技能、动态配置、只读子代理和钩子；按选项配置 `zotero-mcp`。 |
| `--app codex` | `~/.codex/AGENTS.md`、`agents`、`hooks`、`hooks.json` 和 `skills`，同步只读沙箱配置并补缺模型窗口配置；按选项配置 `zotero-mcp`。 |
| `--app claude` | `~/.claude/CLAUDE.md`、只读子代理、技能和钩子，向 `settings.json` 合并事件配置；未配置 `effortLevel` 时按动态配置补齐。 |
| `--app dsh` | `~/.dsh/AGENTS.md` 和 `skills`，复用用户准备的 `DEEPSEEK_API_KEY` 或 `deepseek-account` 凭据，不改 `.credentials.yaml`。 |

Codex 已对称挂载以下 5 类事件，事件类别与脚本模块数量应分别说明：

| 事件 | 当前职责 |
| --- | --- |
| `SessionStart` | 在压缩或恢复入口调用 `session_context_loader.py` 恢复原话和未决工作流，并运行 `handoff_gate.py`。 |
| `PostCompact` | 运行 `handoff_gate.py`，更新压缩后的交接判断。 |
| `UserPromptSubmit` | 运行 `handoff_gate.py`，按新指令更新交接判断。 |
| `PreToolUse` | 运行 `status_guard.py` 核查写入边界、日志重复读取和派工票据，再运行交接检查。 |
| `Stop` | 运行 `reply_repeat_guard.py` 检查重复回复和重负载完工报账，再由 `handoff_gate.py` 检查交接要求。 |

钩子模块包括 `user_words.py`、`session_context_loader.py`、`handoff_gate.py`、`status_guard.py` 和 `reply_repeat_guard.py`。Codex 与 Claude Code 默认交接模式是 `advisory`；强制停机仅在实际模式要求时生效。

Claude 默认随钩子同步 `~/.claude/hooks/ccswitch_admin.py`，用于读取与管理供应商卡片，不要求启用本地路由。`--no-claude-hooks` 会跳过该默认同步；`--with-cc-router` 则另行分发 `~/.claude/router/`。只有使用相应选项并获得授权时，`--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 才会先在线备份 `cc-switch.db`，再停用或清理旧提示词与技能记录。

## 本机配置入口

用户要求配置本机或检查安装状态时，先只读探测：

```powershell
python -B -X utf8 install.py --probe-apps
python -B -X utf8 install.py --audit-upgrade
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --caller auto
```

核对 Windows 与 WSL2 运行侧、客户端安装、供应商卡片、凭据配置、配置漂移及编译型科学计算包的二进制接口（ABI）风险。程序存在与登录有效应分别核实。网络、官方登录或 API Key 缺失时列待办，不编造绕过方法。只有单端就绪时，说明先配置该端与补齐其他端后统一配置的差异，供用户选择；单端仍能独立工作。

正式安装前按所需客户端和路由选项空跑：

```powershell
python -B -X utf8 install.py --dry-run --yes
```

向用户展示完整拟写入路径、模型配置与自定义尾段的保留方式；涉及数据库时说明备份位置。`--yes` 是安装器参数，不能代替全局写入授权。获批后才执行对应安装。

## 派工与可用性检查

主会话遇到实质性推导、多文件编码验证或关键代码与文档审查，应主动通过 `cae-harness` 核查可用端。用户以标识符或口语指定客户端、模型简称、挡位或省额度意图时，按 P0 第一优先级锁定指定维度，以 `--user-prompt` 传递原话。`role:*` 是并列入口，不能覆盖点名；未指定的窗口、非委派挡位、无头标志、只读行号内联、快照和脱敏由脚本补齐。轻量卡片跑非 `fast` 角色或所需路由未存活时明确报错，不能静默降级。

可用性经过两道门。第一道门由 `fleet-evolve` 在独立维护流程联网评估能力，同时展示本机可用性和额度类型，由用户决定 agent 与 LLM 的启停，写入 `subscription_matrix`；退订项设 `enabled: false` 后全局跳过。第二道门由 `plan` 在弹卡前纯离线复查当前配置、路由存活及本机可读额度，包括滚动窗口百分比与重置时间、卡片套餐或余额类型、日月限额和冷却记录，过滤不可用或超限通道。本地信息不足时保留未知，不声称已取得服务端实时余额。

轻任务按 `runtime_tunables.dispatch_confirmation` 阈值静默执行。重负载包括外部无头调用，也包括内部 `invoke_subagent`、`run_workflow`、`spawn_agent`、`Agent` 等多路或可写委派。`plan` 先生成背景、要做什么、预计后果和 14 列全字段矩阵，列出通道、客户端与子代理类型、实际模型及 `inherit` 解析、挡位、窗口、无头与净室状态、读写边界、内联与超时、可用性、额度及回执位置。完整列序见 cae-harness 技能和源码。矩阵附 `capability_ledger.last_online_refresh_at` 与 `capability_ledger.local_bench_verified_at`；日常派工不联网更新性能，联网刷新归独立的 `fleet-evolve` 流程。

请用户选择批准当前计划、微调后批准或暂缓并交接。获批后以 `plan --approve-ticket` 登记，执行绑定 `--ticket`，由 `status_guard.py` 核验并拦截裸 CLI 旁路。`batch` 只执行外部无头通道；内部原生通道由对应客户端按获批计划调用。

## 中断恢复与完成回执

等待弹卡期间发生自动压缩时，恢复钩子从磁盘未决票据独立加载工作流背景、目标、预计后果、完整矩阵和选项卡，与常规回读摘要分为两块显示。主会话须完整转述确认内容，不能凭摘要补猜；压缩与恢复都不构成批准。

当实际交接模式进入 `drain` 或 `freeze` 强制流程时，`handoff_gate.py` 放行 `check-grounding --strict`，并在 `Stop` 硬门禁要求把完整工作流、全字段矩阵、性能更新时间、选项卡与票据写入【新对话提示词】。新会话先用 `plan --resume-ticket` 读取复核，再继续确认。当前接口不会自动改绑原票据的会话归属，跨会话执行须按技能说明重新形成当前会话的确认计划。

`reply_repeat_guard.py` 在完工 `Stop` 检查回复中是否含有成败词，以及 Token 用量或无法统计说明。外部任务还须覆盖各流标识和账单中的实际回执文件名；单路 `task` 或 `gate` 可用模型名或任务状态代替默认流号。原生任务须覆盖完成数量、各流标识或各角色名中的一项，并提供日志或轨迹说明。流标识和外部回执文件名按完整词项匹配。漏报时同一回合单次阻断并附上磁盘账单。主会话仍须自行逐路核对具体状态、Token 明细与汇总算术，静态检查不核实这些内容的语义和计算结果。

## 上游维护与验收

上游维护遵循 fleet-evolve 技能，先以 `fleet_state.py status` 核对身份和状态锁。`full` 对应全面更新，核查外部升级并评估角色和运行参数；`hygiene` 对应全量优化，基于已审定配置加固代码、整篇重写文档；`audit` 只读巡检，不安装、不升级、不改变配置。限定工作包以本轮授权为边界，不执行无关状态写入。

重要文档先按需要进行净室可读性检查，再由主会话整篇重写，最后逐项核对源码事实和公开脱敏。独立审查不得修改文件或运行构建测试；主会话采纳建议前亲自核对原文与行号。验收不得放宽容差、守恒或收敛阶判据，也不得删改断言。

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python -B -X utf8 -m unittest tests.test_e2e_integration
```

涉及拍板节点时用 `fleet_state.py propose` 登记具体方案；全局同步附 `install.py --dry-run --yes` 清单。获批后才运行 `approve` 和对应同步，再做已授权的本机无头实测。主会话客户端需要重启时，以 `record-pass1` 和 `verify-pass2` 完成重启前后验收，最后由 `clear --verified --readback-done` 执行事实锚定硬门禁并归位。

汇报分别说明文档或配置完成、离线检查通过、本机实际环境或算例实测通过，未验证项只列当前任务的核心缺口。提交与推送按用户当前分工处理，未经明确批准不执行 `git push`。
