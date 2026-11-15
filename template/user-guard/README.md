# `user-guard`：Antigravity 全局插件说明

`user-guard` 是本仓库面向 Google Antigravity（反重力）维护的原生全局插件模板，也是四个客户端同源共享规则、钩子与技能的上游目录。在仓库根目录运行 `python -B install.py --app antigravity` 后会部署到 `~/.gemini/config/plugins/user-guard`（全仓唯一版本号定义于 [plugin.json](plugin.json)）。四端整体协作用法见仓库根 [README.md](../../README.md)，钩子触发矩阵与跨主机实测数据见 [architecture-and-hooks.md](../../docs/architecture-and-hooks.md)。

## 插件目录结构

| 文件或子目录 | 职责说明 |
| :--- | :--- |
| [plugin.json](plugin.json) | Antigravity 插件清单与全仓唯一版本号来源 |
| [hooks.json](hooks.json) | 挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四类生命周期事件；部署时自动替换为本机验证可用的 Python 3.11+ 命令前缀 |
| [AGENTS.md](rules/AGENTS.md) | 四端共用的 `R0–R11` 常驻全局规则（零季节性模型名硬编码，与角色槽位体系解耦） |
| [hooks/](hooks/) | 五个纯标准库钩子脚本：原话归档（`user_words.py`）、压缩恢复注入（`session_context_loader.py`）、项目外写拦截与日志防空读（`status_guard.py`）、防重复空等刷屏（`reply_repeat_guard.py`）、长对话分段交接（`handoff_gate.py`） |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator` 三个严格只读子代理（默认 `inherit` 跟随主对话所选模型） |
| [cae-harness/](skills/cae-harness/SKILL.md) | 跨端角色路由与无头验证编排器（含全仓唯一模型评估快照 [fleet_profile.json](skills/cae-harness/fleet_profile.json) 与 `scripts/cae_harness.py`） |
| [fleet-evolve/](skills/fleet-evolve/SKILL.md) | 优缺点双向重算、用户拍板状态门禁、非主控工具三级耦合升级与仓库清淤协议（含 `scripts/fleet_state.py`） |

## 状态隔离与本地校验

- **运行期状态与代码目录分离**：用户原话归档、交接状态与诊断日志统一写入 Antigravity 官方 `PLUGIN_DATA` 目录（`~/.gemini/antigravity/plugin_data/user-guard/`），插件代码目录保持只读干净。
- **修改后的必跑校验**：更新本目录内容后，依次执行 `agy plugin validate --strict template/user-guard` 与仓库根目录离线单测 `python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v`，通过并获用户批准后再运行 `python -B install.py --app antigravity` 同步至宿主机。
