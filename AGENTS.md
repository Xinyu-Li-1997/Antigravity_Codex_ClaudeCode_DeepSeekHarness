# 四客户端配置仓库维护说明

本仓库 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 维护科学计算、CAE 与高可靠系统软件工程使用的共享安全不变量内核与四客户端原生适配模板。实现仅依赖 Python 3.11+ 标准库。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`。课题项目直接使用已安装配置，不复制上游模板。

## 内容归属与维护范围

[全局规则模板](template/user-guard/rules/AGENTS.md) 是工程规则的唯一来源，安装时按 `@client` 条件块为四端裁剪分发。三项下发至宿主的课题技能为：[cae-harness 技能](template/user-guard/skills/cae-harness/SKILL.md) 负责跨端无头派工、持久化续接、确认票据与离线核验，[cae-writing 技能](template/user-guard/skills/cae-writing/SKILL.md) 提供按体裁的学术写作规范（零常驻钩子），[cae-axioms 技能](template/user-guard/skills/cae-axioms/SKILL.md) 提供点名触发的四层条款级单向公理分册、双档硬公理状态机、纯核 Lean 4 真内核驱动与净室 `packet_replay` 协议（零常驻钩子）。上游专属维护技能 [fleet-evolve 技能](template/user-guard/skills/fleet-evolve/SKILL.md) 仅留本仓库使用，不再分发至四端宿主目录。

[fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 统一保存五个动态角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）、`native_agent_profiles`、`runtime_tunables`、`client_capability_baselines`、`subscription_matrix` 与 `capability_ledger`。本机私有选卡、停用项与个人覆盖写入不进 Git 的同级 `fleet_profile.local.json`（`hook_safety` 上限只紧不松）。三端只读代理的共享语义统一收敛于 [agents_spec.json](template/_shared/agents_spec.json)。常驻文档仅引用角色与配置键，不写时期性模型名、静态日期或插件版本数字；版本以 `template/user-guard/plugin.json` 为准，历史见 Git 与 `CHANGELOG.md`。

### 仓库分层、公理边界与文档治理

本仓库按三层职责单向切分，严守公理机器（`cae-axioms` `2C` 与命题 $B'$）防自改写红线：自更新层仅常驻**纯只读四端协议探针 + 元配置数据提案 + 只读工程工单/文档漂移信号**，硬禁自改写主体代码层。

| 层次 | 归属路径 | 维护与更新边界 |
| --- | --- | --- |
| 项目主体代码层 | `install.py`、`template/user-guard/hooks/`（含单入口 `uguard_hook.py` 与 `_lib/`）、`template/_shared/agents_spec.json`、`cae_harness.py` 及 `_harness/`、`axiom_gate.py` 及 `_axiom_lib/` 与 `lean/`、`fleet_state.py`、`ccswitch_admin.py`、`router.py`、`tests/` | 受 Git、全量回归与 `fleet_state.py` `core_sha256` 全覆校验锁约束；仅通过明确批准的工程开发修改，严禁由自更新模块静默自改写。 |
| 项目自更新层 | `fleet_profile.json`、`fleet_profile.local.json`（不进 Git）、`fleet-evolve`、`install.py --audit-upgrade` | 只读采集四端四值能力矩阵 `capability_matrix` 与双轨信号（`recommended_actions` 与 `evolution_signals`），数据变更须经用户审定。 |
| 分发给其他 Agent 的配置层 | `~/.gemini/config/plugins/user-guard/`、`~/.codex/`、`~/.claude/`、`~/.dsh/` | 由 `install.py` 按宿主协议裁剪分发并保留用户自定义尾段，自动清理旧版下发的 `fleet-evolve` 残留。 |

公开文档按受众分四级单点维护，由 `cae_harness.py check-grounding --strict --doc-hygiene` 机械守卫受众边界、计数一致、句长与跨文档去重：

| 文档层级 | 唯一归属文件 | 目标读者与禁入红线 |
| --- | --- | --- |
| T0 入口页 | `README.md` | 首次接触的 CAE/科学计算用户；严控精简体积，不堆砌内部私有符号、钩子实现细节或上游维护步骤。 |
| T1 实战手册 | `docs/workflow-guide.md` | 已安装工具的日常研发工程师；涵盖四客户端原生模式配合指南、派工分工、分片阅读、算例闭环与 L1–L4 验收。 |
| T2 架构与运维 | `docs/architecture-and-hooks.md`、`template/user-guard/README.md` | 维护者与安全审计人员；独占模块表、单入口钩子、配置键、预算公式、双轨体检码与精确卸载。 |
| T3 契约与日志 | `AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`CHANGELOG.md`、`LEGAL.md`、`SECURITY.md`、`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md` | 面向 Agent 执行的精简规则、参数契约与开源法律合规治理文件；版本增量历史只记入 `CHANGELOG.md`。 |

`workspace_mode` 区分通用课题项目（`general_project`）与上游配置仓库（`upstream_config_repo`，同时包含 `install.py` 与 `template/user-guard/plugin.json`）。独立运行的 `fleet_state.py` 在非上游目录未传 `--repo` 时返回 `not_upstream_config_repo`（退出码 `2`）。修改前核对本轮授权与 Git 状态；全局写入、推荐矩阵与 `runtime_tunables` 调整、ABI 强耦合升级及 `git push` 均须取得相应批准。

## 安装与四端协议适配职责

`install.py` 按目标客户端裁剪规则并分发原生适配产物。Antigravity 同步独立插件目录 `plugins/user-guard`（不动用户自有 `~/.gemini/GEMINI.md`）；Codex、Claude Code 与 DSH 更新顶层全局规则文件至标准章节标题 `## 工具与运行环境`（不可改名），保留其后自定义尾段并将冲突编号迁为 `L1.`、`L2.`。

下表路径均位于用户主目录，正式写入前先执行只读预览并获批：

| 客户端选项 | 同步内容与宿主协议适配 |
| --- | --- |
| `--app antigravity` | `~/.gemini/config/plugins/user-guard` 中的裁剪规则、3 项课题技能、动态配置、只读代理与窄匹配单入口 4 类事件钩子；按选项配置 `zotero-mcp`。 |
| `--app codex` | `~/.codex/AGENTS.md`、`agents/*.toml`、单入口 5 类事件钩子（命令串字节恒定，策略落 `handoff/policy.json` 保护 `[hooks.state]` 信任哈希）与 3 项课题技能；兼容 `default_permissions` 权限体并按选项配置 `zotero-mcp`。 |
| `--app claude` | `~/.claude/CLAUDE.md`、平台硬化只读代理（`disallowedTools` + `permissionMode: dontAsk` + `maxTurns`）、单入口 5 类事件钩子与 3 项课题技能；按动态配置补缺 `effortLevel`。 |
| `--app dsh` | `~/.dsh/AGENTS.md`（剔除伪硬钩子与跨端杂项，写明原生文件沙箱与 `no_lifecycle_hooks`）与 3 项课题技能，复用既有 `.credentials.yaml`。 |

单入口调度器 `uguard_hook.py` 将五个共用钩子模块（`user_words.py`、`session_context_loader.py`、`status_guard.py`、`reply_repeat_guard.py`、`handoff_gate.py`）与 `hooks/_lib/` 串联为每事件单进程，入口统一受 `hook_safety` 保护：

| 共有防护阶段 | 对应入口与核心职责 |
| --- | --- |
| 恢复与调用前 | `uguard_hook.py --event SessionStart/PreInvocation/PostCompact` 驱动 `session_context_loader.py` 与 `handoff_gate.py`，去噪恢复原话、实质子代理摘要、未决票据总览与对账指针，并将手动 `/compact` 独立归档而不计入抖动熔断。 |
| 新指令提交时 | `uguard_hook.py --event UserPromptSubmit`（Antigravity 在 `PreInvocation` 对账）评估交接阶段或宽限期，四端默认均为 `advisory` 温和提醒，`/goal` 激活期不启发式强杀。 |
| 工具执行前 | `uguard_hook.py --event PreToolUse` 驱动 `status_guard.py` 与 `handoff_gate.py`：硬守卫 R4 项目目录边界、只读子代理与外部无头重负载票据及裸调 CLI；放行同端只读多路子代理、用户显式原生斜杠编排与嵌套子代理，未指示可写多路走宿主 `ask`。 |
| 回合结束时 | `uguard_hook.py --event Stop` 驱动 `reply_repeat_guard.py` 与 `handoff_gate.py`：对外部无头 `run_id`/成败/Token/`reconcile_file` 账单、测试失败谎报、重复贴票与连续空等硬拦截；启发式实证门禁默认 `advisory` 记录并豁免 `.agents/`/`skills/`/`skill.md`。 |

Claude 默认随钩子同步 `~/.claude/hooks/ccswitch_admin.py`（`--no-claude-hooks` 跳过，`--with-cc-router` 另行分发 `~/.claude/router/`）；仅在获授权并传 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 时，先在线备份 `cc-switch.db` 再处理旧记录。

## 本机配置入口与上游验收

配置本机或体检安装状态前先只读探测，核对 Windows 与 WSL2 `cross_side_status`、四端四值 `capability_matrix`、双轨 `recommended_actions` 与 `evolution_signals`、卡片与凭据、配置漂移及 ABI 风险：

```powershell
python -B -X utf8 install.py --probe-apps
python -B -X utf8 install.py --audit-upgrade
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --caller auto --redact
```

正式写入前执行只读预览，向用户展示完整拟写入路径与保留方式（`--yes` 仅跳过脚本交互确认，不代替全局写入授权）：

```powershell
python -B -X utf8 install.py --dry-run --yes
```

上游维护遵循 [fleet-evolve/SKILL.md](template/user-guard/skills/fleet-evolve/SKILL.md)，先以 `fleet_state.py status` 核对身份与全覆 `core_sha256` 状态锁。验收严禁放宽容差、守恒或收敛阶判据，严禁删改断言：

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict --doc-hygiene
python -B -X utf8 -m unittest discover -s tests -p "test_*.py"
```

涉及审批节点用 `fleet_state.py propose` 登记具体方案；全局同步附 `install.py --dry-run --yes` 清单，获批后执行 `approve` 与同步，最后经 `clear --verified --readback-done` 通过事实锚定门禁归位。未经明确批准不执行 `git push`。
