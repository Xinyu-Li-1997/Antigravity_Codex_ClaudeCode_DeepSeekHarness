# `user-guard`：Antigravity 原生插件参考卡

`user-guard` 是本仓库面向 Google Antigravity（反重力）分发的宿主机全局插件模板（安装目标 `~/.gemini/config/plugins/user-guard`；插件版本号以 [plugin.json](plugin.json) 为唯一真源）。四端全局协同总览、部署模式与双核心矩阵见仓库根 [README.md](../../README.md)，钩子状态机与跨平台底层实现见 [architecture-and-hooks.md](../../docs/architecture-and-hooks.md)。

## 插件资产清单

| 目录／文件 | 工程职责 |
| :--- | :--- |
| [plugin.json](plugin.json) | Antigravity 插件元数据与全仓唯一版本真源 |
| [hooks.json](hooks.json) | `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四阶段生命周期事件绑定（安装时自动注入本机验证通过的 Python 3.11+ 命令前缀） |
| [AGENTS.md](rules/AGENTS.md) | `R0–R11` 全局工程与科学计算规则（四端同源编译真源） |
| [hooks/](hooks/) | 原话增量归档（`user_words.py`）、压缩恢复与读图保护（`session_context_loader.py`）、`R4` 目录硬边界与日志防空转（`status_guard.py`）、防刷屏门禁（`reply_repeat_guard.py`）、长会话收口状态机（`handoff_gate.py`） |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator` 三个只读子代理（默认 `inherit` 跟随主会话模型） |
| [cae-harness/](skills/cae-harness/SKILL.md) | 跨端无头 CLI 编排、分片审查与数值物理门禁技能 |
| [fleet-evolve/](skills/fleet-evolve/SKILL.md) | 全栈工具链分级升级、二维正交选型重算与整仓重构回归元技能 |

## 宿主机数据隔离与校验

- **运行态零污染**：会话原话归档、门禁状态与日志默认写入 Antigravity 官方注入的 `PLUGIN_DATA` 目录（`~/.gemini/antigravity/plugin_data/user-guard/`），插件代码目录保持纯静态只读。
- **结构严格校验**：修改插件模板后，执行 `agy plugin validate --strict template/user-guard` 与根仓库 `python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v` 验证通过后再同步宿主机。
