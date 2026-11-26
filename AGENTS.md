# 四客户端配置仓库维护说明

本仓库 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 维护科学计算、CAE 和高可靠系统软件工程使用的共用全局规则、技能、只读子代理、会话生命周期钩子与后台验证工具。实现仅依赖 Python 3.11+ 标准库。Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`。课题项目直接使用已安装配置，不复制上游模板。

## 内容归属与维护范围

[全局规则模板](template/user-guard/rules/AGENTS.md) 是工程规则的唯一来源。本文件说明仓库维护与安装入口；[cae-harness 技能](template/user-guard/skills/cae-harness/SKILL.md) 负责日常派工、持久化续接、确认票据及事实锚定；[cae-writing 技能](template/user-guard/skills/cae-writing/SKILL.md) 按体裁提供按需学术写作与改稿规范（不增设常驻硬钩子）；[fleet-evolve 技能](template/user-guard/skills/fleet-evolve/SKILL.md) 负责全局工具链评估、审批和同步。细则按归属单点维护，已被代码或钩子硬守住的机制不在此重复展开。

[fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 统一保存五个动态角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）、`native_agent_profiles`、`runtime_tunables`、`subscription_matrix` 与 `capability_ledger`。常驻文档仅引用角色与配置键，不写时期性模型名、静态日期或插件版本数字；版本以 `template/user-guard/plugin.json` 为准，历史见 Git 与 `CHANGELOG.md`。

### 仓库分层与文档治理（宪法总控边界）

本仓库按三层职责切分，避免主体代码、动态元配置与宿主分发产物混写；反复出现的缺陷优先沉淀为单测或现有钩子守卫，已被检查兜底的文字条文及时减薄：

| 层次 | 归属路径 | 维护与更新边界 |
| --- | --- | --- |
| 项目主体代码层 | `install.py`、`template/user-guard/hooks/`（含 `_lib/fs_safety.py`、`shell_lex.py`、`adaptive_policy.py`、`process_guard.py`）、`cae_harness.py`、`fleet_state.py`、`ccswitch_admin.py`、`router.py`、`tests/` | 受 Git 与全量回归及 CI 门禁约束，仅通过工程重构或经批准补丁修改，不在日常会话中自改写。 |
| 项目自更新层 | `fleet_profile.json`、`fleet-evolve`、`install.py --audit-upgrade` | `fleet_profile.json` 由 `fleet-evolve` 评估并经用户确认后更新，驱动模型矩阵、自适应策略参数及升级体检。 |
| 分发给其他 Agent 的配置层 | `~/.gemini/config/plugins/user-guard/`、`~/.codex/`、`~/.claude/`、`~/.dsh/` | 由 `install.py` 按宿主协议分发同步并保留用户自定义尾段，属于部署产物，不在目标端手工硬改主干逻辑。 |

公开文档按受众分四级单点维护，由 `cae_harness.py check-grounding --strict --doc-hygiene` 机械守卫受众边界、计数一致、句长与跨文档去重：

| 文档层级 | 唯一归属文件 | 目标读者与禁入红线 |
| --- | --- | --- |
| T0 入口页 | `README.md` | 首次接触的 CAE/科学计算用户；严控精简体积，不堆砌内部私有符号、钩子实现细节或上游维护步骤。 |
| T1 实战手册 | `docs/workflow-guide.md` | 已安装工具的日常研发工程师；聚焦派工分工、分片阅读、算例闭环与 L1–L4 验收，不复读架构细节。 |
| T2 架构与运维 | `docs/architecture-and-hooks.md`、`template/user-guard/README.md` | 维护者与安全审计人员；独占模块表、配置键、钩子细节、预算公式、体检建议码与精确卸载。 |
| T3 契约与日志 | `AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`CHANGELOG.md`、`LEGAL.md`、`SECURITY.md`、`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md` | 面向 Agent 执行的精简规则、参数契约与开源法律合规治理文件；版本增量历史只记入 `CHANGELOG.md`。 |

`workspace_mode` 区分通用课题项目（`general_project`）与上游配置仓库（`upstream_config_repo`，同时包含 `install.py` 与 `template/user-guard/plugin.json`）。独立安装的 `fleet_state.py` 在非上游目录未传 `--repo` 时返回 `not_upstream_config_repo`（退出码 `2`）。修改前核对本轮授权与 Git 状态，保留其他任务改动；全局写入、推荐矩阵与 `runtime_tunables` 调整、ABI 强耦合升级及 `git push` 均须取得相应批准。

## 安装与共有机制同步职责

`install.py` 按所选客户端协议分发共用模板。Antigravity 以独立插件目录 `plugins/user-guard` 同步（不动用户自有的 `~/.gemini/GEMINI.md`）；Codex、Claude Code 与 DSH 的顶层全局规则文件（`AGENTS.md` 或 `CLAUDE.md`）更新标准正文并保留 `## 工具与运行环境` 之后或以 `## 工具与` 开头的用户自定义尾段（与标准 `R10.`、`R11.` 冲突的条目迁移为 `L1.`、`L2.`）。模板标准章节标题 `## 工具与运行环境` 是安装器切分边界，不可改名。

下表路径均位于用户主目录，正式写入前先执行只读预览并获批：

| 客户端选项 | 同步内容与宿主协议适配 |
| --- | --- |
| `--app antigravity` | `~/.gemini/config/plugins/user-guard` 中的规则、技能、动态配置、只读子代理与 4 类插件事件钩子；按选项配置 `zotero-mcp`。 |
| `--app codex` | `~/.codex/AGENTS.md`、`agents`、`hooks`、`hooks.json`（5 类事件）与 `skills`，同步 `workspace-write` 沙箱并补缺模型窗口；按选项配置 `zotero-mcp`。 |
| `--app claude` | `~/.claude/CLAUDE.md`、只读子代理、技能和钩子，向 `settings.json` 合并 5 类事件配置；按动态配置补缺 `effortLevel`。 |
| `--app dsh` | `~/.dsh/AGENTS.md` 与 `skills`，复用既有凭据且不改 `.credentials.yaml`（当前 `soft_rules_only`，官方开放硬钩子时由 `install.py` 探测触发元更新）。 |

五个共用钩子模块（`user_words.py`、`session_context_loader.py`、`status_guard.py`、`reply_repeat_guard.py`、`handoff_gate.py`）与共享库 `hooks/_lib/` 承载四阶段会话防护，入口均受 `hook_safety` 墙钟与条件内存上限保护：

| 共有防护阶段 | 对应共用脚本与宿主事件映射 |
| --- | --- |
| 恢复与调用前 | `session_context_loader.py`（联动 `handoff_gate.py`；Antigravity 挂 `SessionStart`、`PreInvocation`；Codex 与 Claude Code 挂 `SessionStart`、`PostCompact`）去噪恢复原话、实质子代理与超时抢救摘要、完整未决票据总览及有界完工对账链接与活跃 `read-plan` 指针，并评估交接阶段。 |
| 新指令提交时 | `handoff_gate.py`（Codex 与 Claude Code 挂 `UserPromptSubmit`；Antigravity 在 `PreInvocation` 对账轨迹步）更新交接判断或宽限期。 |
| 工具执行前 | `status_guard.py`（联动 `handoff_gate.py`；三端挂 `PreToolUse`）守卫写边界与同会话外部目录批准租约、归一化 PowerShell 续行、拦截日志轮询与裸调 CLI、核验重负载与 `followup` 票据及交接限制（收口期放行 `check-grounding`/`reconcile`/`read-plan`/`cross-check` 诊断）。 |
| 回合结束时 | `reply_repeat_guard.py`（联动 `handoff_gate.py`；三端挂 `Stop`）拦截空等刷屏、同回合重复贴票、局部补测冒充全过、含失败项宣称全过，并以单次轨迹扫描统一守卫零探测否定、局部排查全称缺口及单端整写正式长文免审（联动 `code_families` 提示）；同时按 `run_id` 核验完工回执与 `reconcile_file` 链接并核查强制交接提示词。 |

Antigravity 默认启用长会话分段收口；Codex 与 Claude Code 默认交接模式为 `advisory`。Claude 默认随钩子同步 `~/.claude/hooks/ccswitch_admin.py`（`--no-claude-hooks` 跳过，`--with-cc-router` 另行分发 `~/.claude/router/`）；仅在获授权并传 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 时，先在线备份 `cc-switch.db` 再处理旧记录。

## 本机配置入口

配置本机或体检安装状态前先只读探测，核对 Windows 与 WSL2 `cross_side_status`、客户端安装、卡片与凭据、配置漂移及 ABI 风险（程序存在与登录有效分别核实）：

```powershell
python -B -X utf8 install.py --probe-apps
python -B -X utf8 install.py --audit-upgrade
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --caller auto --redact
```

正式写入前执行只读预览，向用户展示完整拟写入路径与保留方式（`--yes` 仅跳过脚本交互确认，不代替全局写入授权）：

```powershell
python -B -X utf8 install.py --dry-run --yes
```

## 派工协同与上游验收

日常跨端派工、点名 P0 锁定、两道可用性门禁、持久化 `followup`、软超时抢救、`--isolation` 四级隔离、多波次 `batch` 编排、审查分级与压缩接力均由 `cae_harness.py` 及上述四阶段钩子机械落盘与守卫，操作规程详见 [cae-harness/SKILL.md](template/user-guard/skills/cae-harness/SKILL.md) 与 [docs/architecture-and-hooks.md](docs/architecture-and-hooks.md)。外部无头子进程（`CAE_HARNESS_DEPTH`）自动跳过原话注入、交接与停顿复核，仅保留 `status_guard.py` 越界写硬阻断。

上游维护遵循 [fleet-evolve/SKILL.md](template/user-guard/skills/fleet-evolve/SKILL.md)，先以 `fleet_state.py status` 核对身份与状态锁。验收严禁放宽容差、守恒或收敛阶判据，严禁删改断言：

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict --doc-hygiene
python -B -X utf8 -m unittest discover -s tests -p "test_*.py"
```

涉及审批节点用 `fleet_state.py propose` 登记具体方案；全局同步附 `install.py --dry-run --yes` 清单，获批后执行 `approve` 与同步，最后经 `clear --verified --readback-done` 通过事实锚定门禁归位。未经明确批准不执行 `git push`。
