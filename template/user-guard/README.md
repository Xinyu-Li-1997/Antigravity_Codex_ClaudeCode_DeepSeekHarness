# user-guard 共用模板与插件索引

本目录既是 Antigravity 原生插件，也是四客户端共用规则、生命周期钩子、只读代理与技能的上游模板源。根目录的 [install.py](../../install.py) 按各客户端协议同步分发上述模板文件；日常课题研发直接使用已安装的全局配置，无需向课题或业务仓库复制文件。插件版本号在 [plugin.json](plugin.json) 中唯一维护，演进历史见 [更新日志](../../CHANGELOG.md)。

## 目录归属

| 路径 | 职责 |
| --- | --- |
| [plugin.json](plugin.json)、[hooks.json](hooks.json)、[assets/logo.svg](assets/logo.svg) | 插件元数据清单、Antigravity 原生事件绑定配置与图标 |
| [rules/AGENTS.md](rules/AGENTS.md) | 四客户端共用的全局工程规则唯一模板 `R0–R11`，安装时保留用户自定义尾段 |
| [agents/](agents/) | `readonly-reader`、`readonly-auditor`、`readonly-collaborator` 只读代理模板，禁止修改文件或运行测试 |
| [hooks/](hooks/) | 五个共用生命周期钩子脚本及内部共享辅助库，负责上下文恢复、写边界守卫、票据核验、长会话交接与回合结束检查 |
| [skills/cae-harness/](skills/cae-harness/) | 跨客户端无头调度与离线确定性核验技能，含动态配置文件 [fleet_profile.json](skills/cae-harness/fleet_profile.json) |
| [skills/cae-writing/](skills/cae-writing/) | 按需加载的学术写作与改稿技能，按体裁提供论文、专著、报告与审稿回复结构指南（严守保密红线、零常驻钩子） |
| [skills/fleet-evolve/](skills/fleet-evolve/) | 面向本配置仓库的工具链评估、提案审批与多客户端及跨环境同步规程 |

Codex 与 Claude Code 的宿主适配模板分别位于 [template/codex](../codex/) 与 [template/claude](../claude/)。

## 本地静态校验

在仓库根目录执行以下命令。首条命令需本机已安装 `agy` CLI；Python 命令在 Windows 下使用 `python -B -X utf8`，在 WSL2 与 Linux 下改用 `python3 -B -X utf8`：

```powershell
agy plugin validate --strict template/user-guard
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict --doc-hygiene
python -B -X utf8 install.py --dry-run --yes
```

只读预览仅列出拟写入的目标路径；正式写入宿主机全局配置目录前须先经用户确认。

## 延伸阅读

- 安装与入门总览：[仓库总说明](../../README.md)
- 日常协作流程与闭环示例：[日常工作流指南](../../docs/workflow-guide.md)
- 模块原理、四客户端协议适配与卸载步骤：[架构设计与钩子说明](../../docs/architecture-and-hooks.md)
- 完整脚本参数与确认票据规范：[cae-harness 技能规程](skills/cae-harness/SKILL.md)
