# `user-guard` 工程与科学计算协作插件及同源核心模板

`user-guard` 是本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）维护的核心同源目录：既作为 Google Antigravity（反重力）的原生插件直接安装至 `~/.gemini/config/plugins/user-guard`，同时也是 OpenAI Codex、Anthropic Claude Code 与 DeepSeek Harness（`DSH`）全局规则、生命周期守护钩子及托管技能（`cae-harness`、`fleet-evolve`）的唯一上游真源（Codex 与 Claude Code 专属的事件挂载模板、只读子代理定义及本地分流路由分别位于同级的 `../codex/` 与 `../claude/`）。

安装脚本执行后，各客户端配置直接落盘在各自的宿主机全局配置根目录（`~/.gemini/config/plugins/user-guard`、`~/.codex`、`~/.claude`、`~/.dsh`），日常科研与求解器业务仓库无需内嵌或依赖本模板目录。

## 目录结构与组件职责

| 路径 | 组件职责 |
| --- | --- |
| `plugin.json` | 反重力原生插件元信息清单（遵循官方 v1 Schema，作为全仓唯一版本号真源，采用 `Apache-2.0` 许可） |
| `assets/logo.svg` | 插件矢量图标，满足 `agy plugin validate --strict` 零提示规范 |
| `hooks.json` | 反重力生命周期钩子注册表，挂载 `SessionStart`、`PreInvocation`、`PreToolUse` 与 `Stop` 四类同步命令事件 |
| `rules/AGENTS.md` | 四端精炼共用的 `R0–R11` 全局规则：文件精简、中文与 CAE/CFD 规范术语、四端对等协作与渐进接入、阶段收口全量把关、反过度工程（`R3.3`）、项目目录硬边界与公开脱敏（`R4`）、防无声放大、验收红线、压缩交接对齐及宿主机全局落盘基线 |
| `hooks/user_words.py` | 多端共享底层库：UTF-8 标准流重配置、跨平台文件锁、原子写盘、反重力 JSONL 截断回查、Codex 跨分段 `rollout` 合并校验及 Claude Code 项目会话轨迹原话提取 |
| `hooks/session_context_loader.py` | 压缩与续接恢复入口：反重力侧按字节游标增量检测 `CHECKPOINT` 并注入用户原话摘要；Codex 与 Claude Code 侧在 `SessionStart`（`^(compact\|resume)$`）时归档并注入历史原话 |
| `hooks/status_guard.py` | `PreToolUse` 守卫：支持反重力与 Claude Code（`--client claude`）协议，静态解析表面文件写改工具、Shell 命令与动态工作流 AST，拦截未授权的项目外写删（交互会话弹窗请示，无头子任务直接拒绝），并用日志复合指纹阻断无进展重复轮询 |
| `hooks/reply_repeat_guard.py` | `Stop` 守卫：在反重力与 Claude Code 侧识别含迭代步数、残差等真实数值推进的正常汇报，拦截连续同质的空等刷屏回复（反重力侧内部联动收口终审，Claude Code 侧与 `handoff_gate.py` 同组挂载） |
| `hooks/handoff_gate.py` | 长会话收口状态机：反重力侧按压缩频次、轨迹体积与读图负载执行 `normal -> advisory -> drain -> freeze` 迁移（支持 R8 暂缓宽限）；Codex 与 Claude Code 侧默认运行 `advisory` 提醒模式（可选 `enforce`） |
| `agents/readonly-reader.md` | 反重力只读通读子代理（默认 `inherit` 跟随主对话模型与独立窗口），负责长文献、大源码与算例日志的忠实摘录和行号定位 |
| `agents/readonly-auditor.md` | 反重力只读审查子代理（默认 `inherit`），负责逐行核对公式符号、边界条件、数值容差与运行证据 |
| `agents/readonly-collaborator.md` | 反重力只读协作子代理（默认 `inherit`），负责数学与离散格式推导、方案论证及技术文档润色 |
| `skills/cae-harness/` | 四端无头 CLI 协作与门禁验证台架（`SKILL.md` 与 `scripts/cae_harness.py`），统一支持 `codex`、`agy`、`claude`（`cc`）与 `dsh` 的环境预检（`probe`／`assess`）、按需／模糊派工（`task`）、分片审计（`audit`）与验证门禁（`gate`） |
| `skills/fleet-evolve/` | 多引擎舰队自举演进与全栈清淤回归元技能（`SKILL.md` 与宿主机二阶段状态锁 `scripts/fleet_state.py`），覆盖只读并发调研、二维正交选型矩阵重算、双侧真机探针验收与减法清淤门禁 |

## 四端编排、配窗与顺位兜底要点

1. **宿主原生子代理与 `cae-harness` 解耦**：反重力通过 `invoke_subagent` 启动的三个 `readonly-*` 子代理始终 `inherit` 跟随当前前端主会话的模型、挡位与独立大窗口（Claude Code 侧三个 `readonly-*` 同样配置 `model: inherit`，在接入本地 `router.py` 时按子代理模型映射转发）；`cae-harness` 仅管理无头 CLI 子进程，调用前自动核验目标端 LLM 就绪状态，不受前端主控模型切换影响。
2. **日常反重力统管、专长分工与零门槛起步**：多端环境下推荐由反重力居中统管配置与上下文，硬核公式推导与求解器编码／门禁优先调 Codex（`Astra`／最新代 `Sol` + `max` 最高挡 + `872,000` 声明最大窗口），也可按需或按自然语言指派调用继承 `~/.claude` 与 CC Switch 卡片的 `Claude Code`（`cc`）及官方直连的 `DeepSeek Harness`（`dsh`，默认 `DeepSeek-V4-Pro`、轻量档 `DeepSeek-V4.1-Flash`，`1,048,576` 窗口）；新用户可直接用零门槛合规的 `DSH + DeepSeek` 独立跑通全套规范，后续开通任一更强客户端无缝接手。
3. **额度耗尽自动回退**：调用时附带 `--fallback-on-quota`，若目标端在预检或启动阶段触发额度熔断（`quota_exhausted`）且尚未改动工作区文件，自动切回发起方本侧或顺位已就绪的对等端完成任务。

命令行细则见 [`cae-harness` 技能说明](skills/cae-harness/SKILL.md)与 [`fleet-evolve` 技能说明](skills/fleet-evolve/SKILL.md)，完整安装、可视化舰队矩阵与架构文档见[仓库总 README](../../README.md)与[架构设计及生命周期钩子规范](../../docs/architecture-and-hooks.md)。
