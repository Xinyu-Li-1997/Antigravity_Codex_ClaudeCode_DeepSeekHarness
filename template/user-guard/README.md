# user-guard（客户端插件与共用配置模板目录）

本目录存放 Antigravity 原生插件 `user-guard` 的完整资产，同时作为 Codex、Claude Code 与 DeepSeek Harness（DSH）共用全局规则、生命周期钩子与技能模板的唯一上游维护目录。仓库根目录的 [install.py](../../install.py) 从此处读取模板并按各客户端协议同步至宿主机全局配置目录。

## 目录结构与模块职责

| 子路径 | 作用与分发范围 |
| :--- | :--- |
| [plugin.json](plugin.json)、[hooks.json](hooks.json)、[assets/logo.svg](assets/logo.svg) | Antigravity 原生插件清单（唯一版本号来源）、事件绑定配置与插件图标 |
| [rules/AGENTS.md](rules/AGENTS.md) | 常驻全局工程规则 `R0–R11`（不含特定时期模型名称），同步至四个客户端的全局规则文件并保留用户自定义尾段 |
| [agents/](agents/) | Antigravity 原生只读子代理（`readonly-reader`、`readonly-auditor`、`readonly-collaborator`，均默认 `inherit` 跟随主会话模型）；Codex 与 Claude Code 对应模板位于 `template/codex/agents/` 与 `template/claude/agents/` |
| [hooks/](hooks/) | 纯 Python 3.11+ 标准库编写的五个生命周期钩子脚本：历轮原话与最近 `4` 次已完成子代理摘要双路提取核（`user_words.py`）、压缩后有界恢复注入器（`session_context_loader.py`）、写路径边界与重复读日志拦截（`status_guard.py`）、防空等短句重复拦截（`reply_repeat_guard.py`）及长会话分段交接门禁（`handoff_gate.py`），分别适配 Antigravity、Codex 与 Claude Code 事件协议 |
| [skills/cae-harness/](skills/cae-harness/) | 跨客户端无头命令行角色路由与定量验证技能（含 [fleet_profile.json](skills/cae-harness/fleet_profile.json) 模型评估配置与 [cae_harness.py](skills/cae-harness/scripts/cae_harness.py)），同步至四个客户端的全局 `skills/` 目录 |
| [skills/fleet-evolve/](skills/fleet-evolve/) | 客户端升级检查、模型与配额选型重算及代码文档整理技能（含 [fleet_state.py](skills/fleet-evolve/scripts/fleet_state.py) 审批与重启重入状态锁），同步至四个客户端的全局 `skills/` 目录 |

## 本地校验与预览

在仓库根目录执行插件结构校验与空跑预览：

```bash
agy plugin validate --strict template/user-guard
python3 -B install.py --dry-run --yes
```

完整架构图、双路压缩恢复时序、四个客户端能力边界及日常用法详见仓库根目录 [README.md](../../README.md)、[架构与钩子说明](../../docs/architecture-and-hooks.md) 与 [工作流实战指南](../../docs/workflow-guide.md)。
