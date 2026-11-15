# `user-guard`：Antigravity 全局插件说明

`user-guard` 是本仓库面向 Google Antigravity（反重力）提供的全局插件模板，在仓库根目录运行 `python -B install.py --app antigravity` 后会安装到 `~/.gemini/config/plugins/user-guard`（插件版本号统一在 [plugin.json](plugin.json) 中维护）。四个客户端的整体使用方式见仓库根 [README.md](../../README.md)，钩子触发流程、拦截条件与关闭方法见 [architecture-and-hooks.md](../../docs/architecture-and-hooks.md)。

## 插件目录内容

| 文件或子目录 | 用途说明 |
| :--- | :--- |
| [plugin.json](plugin.json) | Antigravity 插件清单与全仓唯一版本号来源 |
| [hooks.json](hooks.json) | 挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四个生命周期事件；安装时自动填入本机可用的 Python 3.11+ 命令前缀 |
| [AGENTS.md](rules/AGENTS.md) | `R0–R11` 全局工程与科学计算规则（四个客户端共用同一套正文） |
| [hooks/](hooks/) | 五个标准库钩子脚本：保存用户原话（`user_words.py`）、压缩后恢复原话（`session_context_loader.py`）、检查项目外写文件与重复读未变化日志（`status_guard.py`）、减少连续无进展空等回复（`reply_repeat_guard.py`）、长对话交接提醒（`handoff_gate.py`） |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator` 三个只读子代理，默认 `inherit` 跟随主对话所选模型 |
| [cae-harness/](skills/cae-harness/SKILL.md) | 按需后台调用四个客户端无头 CLI 做公式推导、代码审查与带测试命令的自动验证 |
| [fleet-evolve/](skills/fleet-evolve/SKILL.md) | 检查升级非主控工具链、重算客户端与模型搭配、整理全仓代码与文档 |

## 运行数据隔离与修改后检查

- **运行状态不写进插件目录**：用户原话归档、阶段状态与诊断日志统一存放在 Antigravity 官方 `PLUGIN_DATA` 目录（`~/.gemini/antigravity/plugin_data/user-guard/`），插件代码目录保持只读干净。
- **修改模板后的本地校验**：改动本目录文件后，先运行 Antigravity 命令行工具 `agy plugin validate --strict template/user-guard` 以及仓库根目录的单元测试 `python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v`（`-B` 不生成 `.pyc` 缓存，`-X utf8` 在 Windows 下固定 UTF-8 编码），全部通过后再运行 `python -B install.py --app antigravity` 同步到本机。
