# 仓库维护与四客户端配置同步说明（Antigravity、Codex、Claude Code 与 DeepSeek Harness 共读）

本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）集中维护面向科学计算、CAE、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值离散、高性能计算（HPC）与高可靠系统软件工程的全局工程规则、会话生命周期钩子、只读子代理、动态角色与运行期参数配置以及后台命令行验证脚本。全仓仅依赖 Python 3.11+ 标准库，原生支持 Windows 与 WSL2／Linux 环境。运行 `install.py` 后，模板文件按需同步到四个客户端在宿主机上的全局配置目录，日常课题项目内无需拷入本仓库文件。

## 一、配置分层、双工作区识别与四端同步范围

1. **常驻机制层与全动态评估快照分离**：
   - **常驻规则与脚本**：全局规则 [AGENTS.md](template/user-guard/rules/AGENTS.md)、技能说明、生命周期钩子、`install.py`、`cae_harness.py`、`fleet_state.py` 与 `ccswitch_admin.py` 不写死特定时期的模型名称，不将任何一个角色永久固定在单一客户端上，也不在各脚本中散落硬编码死数字，统一通过抽象角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）、受控运行期参数（`runtime_tunables`）及工作区身份判别（`workspace_mode`）运行。
   - **动态评估与运行期参数配置**：当前阶段全部五个角色槽位的推荐客户端、模型系列、默认推理强度与上下文窗口，以及压缩后内联恢复预算、实质子代理筛选窗口、长会话交接阈值、防轮询阈值与无头分片参数，统一由机器可读配置文件 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 受控维护，[README.md](README.md) 第四章表一为其人工审阅视图；调整时须经 `fleet-evolve` 评估并获用户明确批准后生效。
2. **两种用法与工作区硬边界**：
   - **通用课题项目（`workspace_mode: "general_project"`，部分功能）**：在任意日常课题目录中直接使用 `R0–R11`、五类钩子、`readonly-*` 子代理、`cae-harness`（含 `task`、`gate`、`audit`、`check-grounding`、`probe`、`assess`）与 `ccswitch_admin.py`。
   - **本上游配置仓库（`workspace_mode: "upstream_config_repo"`，全功能）**：在通用项目能力基础上，额外启用 [`fleet-evolve`](template/user-guard/skills/fleet-evolve/SKILL.md)（含“全面更新” `full` 与“全量优化” `hygiene` 双闭环）、`fleet_state.py` 状态锁及 `install.py` 全局同步；在通用课题目录误调 `fleet_state.py` 会直接返回退出码 `2`（`not_upstream_config_repo`）拦截。
3. **各客户端写入的宿主机全局目录**：
   - **Antigravity（`--app antigravity`）**：同步 `~/.gemini/config/plugins/user-guard` 插件（含 `R0–R11` 规则、按 `runtime_tunables` 动态调节的原话与实质子代理摘要双路恢复、项目外写操作拦截、重复空等拦截与长会话交接钩子、三个 `inherit` 只读子代理、`cae-harness` 与 `fleet-evolve` 技能及 `fleet_profile.json`），可选配置 `zotero-mcp`。
   - **Codex（`--app codex`）**：同步 `~/.codex/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）、三个 `read-only` 子代理、动态双路压缩恢复与长会话提醒钩子（默认模式读取 `fleet_profile.json`）、`workspace-write` 沙箱与模型窗口补缺、`~/.codex/skills/` 及可选 `zotero-mcp`。
   - **Claude Code 与 CC Switch（`--app claude`）**：同步 `~/.claude/CLAUDE.md`（保留自定义尾段）、三个只读子代理、五个生命周期钩子（合并写入 `settings.json`，未配置推理强度时按 `fleet_profile.json` 补默认 `effortLevel`）与 `~/.claude/skills/`；加 `--with-cc-router` 时同步 `~/.claude/router/` 本地分流代理；加 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 时先在线备份 `cc-switch.db`，再停用或清理 CC Switch 内的旧提示词与旧技能记录。
   - **DeepSeek Harness（`--app dsh`）**：同步 `~/.dsh/AGENTS.md`（保留自定义尾段）与 `~/.dsh/skills/`，直接复用用户已配好的官方 `DEEPSEEK_API_KEY` 或 `deepseek-account` 凭据，全仓不修改 `.credentials.yaml`。

## 二、本机首次安装与日常状态检查

当用户提出“帮我配置本机”或“检查一下安装状态”时，按以下顺序执行：

1. **只读环境探测**：依次运行 `python -B install.py --probe-apps`、`python -B install.py --audit-upgrade` 与 `cae_harness.py probe`（Linux 或 WSL2 下使用 `python3`），核查 Windows 与 WSL2 环境下的客户端安装状态、CC Switch 卡片、配置差异、工具链二进制接口（ABI）依赖耦合以及模型凭据就绪状态。缺失网络代理、官方登录态或 API Key 的客户端直接列出待办项，不编造绕过手段。
2. **单客户端确认**：若本机仅检测到单个客户端，先请用户选择 ① 先单独配好当前客户端直接开展工作，或 ② 安装其他客户端后再统一配置多端协作。
3. **空跑预览与获批写入**：运行 `python -B install.py --dry-run --yes`（按需附带 `--app`、`--with-cc-router`、`--disable-ccswitch-prompts`），向用户说明拟写入的宿主机全局路径、现有模型与自定义尾段保留方式及数据库备份位置，按 `R4` 获明确批准后再正式执行。

## 三、“全面更新”与“全量优化”双循环维护（`fleet-evolve`）

在本上游配置仓库中，当用户提出**“全面更新”**（升级外部工具并重算动态角色与定量参数矩阵，对应 `full` 模式）、**“全量优化”**（基于当前配置全仓加固代码、重写图文文档并做事实锚定与脱敏回归，对应 `hygiene` 模式）或只读巡检（`audit` 模式）时，**先完整读取并遵循 [SKILL.md](template/user-guard/skills/fleet-evolve/SKILL.md)**：

1. 先运行 `fleet_state.py status` 核对本机状态锁与 `workspace_mode == "upstream_config_repo"`，在只读前提下采集客户端状态、模型缓存与运行期参数基线；重要文档重写前按 `R2` 先做 `--clean-room` 空上下文盲读挑刺。
2. 在仓库工作区内完成代码加固、`runtime_tunables` 参数校准、图文分层文档整篇重写、`cae_harness.py check-grounding --target . --strict` 零黑名单事实锚定核验、`readonly-auditor` 逐行核对及全套离线与沙箱回归测试；触及 [AGENTS.md](template/user-guard/rules/AGENTS.md) `R3.1.4` 四类拍板节点时，执行 `fleet_state.py propose` 并附 `install.py --dry-run` 预览清单呈交用户确认后暂停。
3. 获批后执行 `fleet_state.py approve`、同步宿主机全局目录并运行无头实测验收；若主会话客户端自身升级或切换，按 `record-pass1` 提示用户手动重启后再完成复核，最终通过 `clear --verified --readback-done`（内置事实锚定硬门禁）归位，未获明确指令不执行 `git push`。
