# user-guard（客户端插件与共用配置模板目录）

本目录存放 Antigravity 原生插件 `user-guard` 的完整模板，同时作为 Codex、Claude Code 与 DeepSeek Harness（DSH）共用全局规则、会话钩子脚本与技能模块的唯一上游维护目录。仓库根目录的 `install.py` 从此处读取资产，并按各客户端协议同步至宿主机全局配置目录。

## 目录结构与模块职责

| 子路径 | 作用与分发范围 |
| :--- | :--- |
| [plugin.json](plugin.json)、[hooks.json](hooks.json)、[assets/logo.svg](assets/logo.svg) | Antigravity 原生插件清单（全仓唯一版本号来源）、4 类事件绑定配置与插件图标 |
| [rules/AGENTS.md](rules/AGENTS.md) | 常驻全局工程规则 `R0–R11`（不含特定时期模型名称或散落死数字），同步至四个客户端的全局规则文件并保留用户自定义尾段 |
| [agents/](agents/) | Antigravity 原生只读子代理（`readonly-reader`、`readonly-auditor`、`readonly-collaborator`，均默认 `inherit` 跟随主会话模型）；Codex 与 Claude Code 对应模板分别位于 `template/codex/agents/` 与 `template/claude/agents/` |
| [hooks/](hooks/) | 纯 Python 3.11+ 标准库编写的 5 个钩子脚本模块：1 个共享提取与参数核（`user_words.py`）加 4 个事件入口脚本（压缩恢复注入器 `session_context_loader.py`、写路径边界与重复读日志拦截 `status_guard.py`、防空等短句重复拦截 `reply_repeat_guard.py`、长会话分段交接门禁 `handoff_gate.py`），定量阈值统一读取 `runtime_tunables` |
| [skills/cae-harness/](skills/cae-harness/) | 通用课题与上游仓库共用的动态角色路由、零黑名单文档事实锚定核验（`check-grounding`）及后台定量验证技能（含 [fleet_profile.json](skills/cae-harness/fleet_profile.json) 与 [cae_harness.py](skills/cae-harness/scripts/cae_harness.py)），同步至四个客户端的全局 `skills/` 目录 |
| [skills/fleet-evolve/](skills/fleet-evolve/) | 上游配置仓库专属的“全面更新”（`full`）与“全量优化”（`hygiene`）双循环维护技能（含 [fleet_state.py](skills/fleet-evolve/scripts/fleet_state.py) 审批锁、重启重入锁与工作区身份拦截），同步至四个客户端的全局 `skills/` 目录 |

## 本地校验与空跑预览

在仓库根目录执行插件结构校验、事实锚定核验与空跑预览（Windows 使用 `python -B -X utf8`，WSL2 或 Linux 使用 `python3 -B -X utf8`）：

```bash
agy plugin validate --strict template/user-guard
python3 -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
python3 -B -X utf8 install.py --dry-run --yes
```

完整架构图、双路压缩恢复时序、通用项目与上游仓库两类指令表详见仓库根目录 `README.md`、`docs/architecture-and-hooks.md` 与 `docs/workflow-guide.md`。
