# user-guard 共用模板与插件索引

本目录既是 Antigravity 原生插件，也是四客户端共用规则、生命周期钩子、只读代理与技能的上游模板源。根目录的 [install.py](../../install.py) 负责按各客户端协议分发这些资产；日常课题直接使用已安装的全局配置，无需向业务仓库复制文件。版本号只在 [plugin.json](plugin.json) 单点维护，演进历史见 [更新日志](../../CHANGELOG.md)。

## 目录归属

| 路径 | 职责 |
| --- | --- |
| [plugin.json](plugin.json)、[hooks.json](hooks.json)、[assets/logo.svg](assets/logo.svg) | 插件清单、Antigravity 原生事件绑定与图标 |
| [rules/AGENTS.md](rules/AGENTS.md) | 四端共用的全局工程规则唯一模板（`R0–R11`），安装时保留用户自定义尾段 |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator` 只读代理模板，禁写禁测 |
| [hooks/](hooks/) | 五个共用钩子脚本与 `hooks/_lib/` 基础库，承载恢复、写边界、票据、交接与停顿核验 |
| [skills/cae-harness/](skills/cae-harness/) | 跨端无头调度与离线确定性核验工具（含动态档案 [fleet_profile.json](skills/cae-harness/fleet_profile.json)） |
| [skills/fleet-evolve/](skills/fleet-evolve/) | 上游配置仓库专属的工具链评估、提案审批与双端同步规程 |

Codex 与 Claude Code 的宿主适配模板分别位于 [template/codex](../codex/) 与 [template/claude](../claude/)。

## 本地静态校验

在仓库根目录执行（首条需本机已装 `agy` CLI；Windows 用 `python -B -X utf8`，WSL2 与 Linux 改用 `python3 -B -X utf8`）：

```powershell
agy plugin validate --strict template/user-guard
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict --doc-hygiene
python -B -X utf8 install.py --dry-run --yes
```

只读预览仅列出拟写入路径，正式写入宿主机全局目录前须先获批。

## 延伸阅读

- 快速上手与总览：[仓库总说明](../../README.md)
- 日常课题实战与算例模板：[日常工作流实战指南](../../docs/workflow-guide.md)
- 模块原理、四端协议适配与卸载步骤：[架构设计与钩子说明](../../docs/architecture-and-hooks.md)
- 完整脚本参数与确认单规范：[cae-harness 技能规程](skills/cae-harness/SKILL.md)
