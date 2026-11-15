# 仓库维护与四客户端配置同步说明（Antigravity、Codex、Claude Code 与 DeepSeek Harness 共读）

本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）集中维护面向科学计算、CAE、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值离散、高性能计算（HPC）与高可靠系统软件工程的全局工程规则、会话生命周期钩子、只读子代理、动态角色与运行期参数配置以及后台命令行验证脚本。全仓仅依赖 Python 3.11+ 标准库，原生支持 Windows 与 WSL2 及 Linux 环境（Windows 下运行 `python -B -X utf8`，WSL2 或 Linux 下运行 `python3 -B -X utf8`）。执行 `install.py` 后，模板文件按需同步至四个客户端在宿主机上的全局配置目录，日常课题仓库无需拷入任何本仓文件。

## 一、配置分层、工作区边界与四端同步范围

1. **常驻机制层与全动态参数配置分离**：
   - **常驻规则与脚本**：全局规则 [AGENTS.md](template/user-guard/rules/AGENTS.md)、技能说明、五个钩子脚本模块、`install.py`、`cae_harness.py`、`fleet_state.py` 与 `ccswitch_admin.py` 不写死特定时期的模型名称，不将任何角色永久固定在单一客户端上，也不在脚本内散落静态死数字，统一面向五个抽象角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）、受控运行期参数（`runtime_tunables`）与工作区身份判别（`workspace_mode`）运行。
   - **动态评估与运行期参数配置**：当前阶段五个角色槽位的推荐客户端、模型系列、默认推理强度与声明窗口，以及压缩恢复字符预算、实质子代理筛选窗口、长会话交接阈值、防轮询阈值、无头派工超时与本地路由并发参数，统一由机器可读配置文件 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 集中维护，[README.md](README.md) 第四章表一为其人工审阅视图；调整时须经 `fleet-evolve` 评估并获用户明确批准后生效。
2. **两类工作区硬边界**：
   - **通用课题项目（`workspace_mode: "general_project"`）**：在日常科研或工程代码目录中直接使用 `R0–R11`、生命周期钩子、`readonly-*` 子代理、`cae-harness`（含 `task`、`gate`、`audit`、`check-grounding`、`probe`、`assess`）与 `ccswitch_admin.py`。
   - **本上游配置仓库（`workspace_mode: "upstream_config_repo"`）**：在通用能力基础上额外启用 [`fleet-evolve`](template/user-guard/skills/fleet-evolve/SKILL.md)（含“全面更新” `full` 与“全量优化” `hygiene` 双闭环）、`fleet_state.py` 状态锁及 `install.py` 全局同步；在通用课题目录未传 `--repo` 误调 `fleet_state.py` 时，脚本直接返回退出码 `2`（`not_upstream_config_repo`）拒绝执行。
3. **各客户端写入的宿主机全局目录**：
   - **Antigravity（`--app antigravity`）**：同步 `~/.gemini/config/plugins/user-guard` 插件（含 `R0–R11` 规则、4 类事件钩子配置、5 个钩子脚本模块、3 个 `inherit` 只读子代理、`cae-harness` 与 `fleet-evolve` 技能及 `fleet_profile.json`），可选配置 `zotero-mcp`。
   - **Codex（`--app codex`）**：同步 `~/.codex/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）、3 个 `read-only` 子代理、5 类事件钩子配置、`workspace-write` 沙箱与模型窗口补缺、`~/.codex/skills/` 及可选 `zotero-mcp`。
   - **Claude Code 与 CC Switch（`--app claude`）**：同步 `~/.claude/CLAUDE.md`（保留自定义尾段）、3 个只读子代理、5 类事件钩子（合并写入 `settings.json`，未配置推理强度时按 `fleet_profile.json` 补默认 `effortLevel`）与 `~/.claude/skills/`；加 `--with-cc-router` 时同步 `~/.claude/router/` 本地分流代理；加 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 时先在线备份 `cc-switch.db`，再停用或清理 CC Switch 内的旧提示词与旧技能记录。
   - **DeepSeek Harness（`--app dsh`）**：同步 `~/.dsh/AGENTS.md`（保留自定义尾段）与 `~/.dsh/skills/`，直接复用用户已配置的官方 `DEEPSEEK_API_KEY` 或 `deepseek-account` 凭据，全仓不修改 `.credentials.yaml`。

## 二、本机首次安装与日常状态检查

当用户提出“帮我配置本机”或“检查一下安装状态”时，按以下顺序执行：

1. **只读环境探测**：依次运行 `python -B -X utf8 install.py --probe-apps`、`python -B -X utf8 install.py --audit-upgrade` 与 `cae_harness.py probe`（WSL2 或 Linux 下改用 `python3 -B -X utf8`），核查 Windows 与 WSL2 环境下的客户端安装状态、CC Switch 供应商卡片、配置差异、编译型科学计算包二进制接口（ABI）依赖耦合以及模型凭据就绪状态。缺失网络代理、官方登录态或 API Key 的客户端直接列出待办项，不编造绕过手段。
2. **单客户端确认**：若本机仅检测到单个客户端，先请用户选择 ① 先单独配好当前客户端直接开展工作，或 ② 安装其他客户端后再统一配置多端协作。
3. **空跑预览与获批写入**：运行 `python -B -X utf8 install.py --dry-run --yes`（按需附带 `--app`、`--with-cc-router`、`--disable-ccswitch-prompts`），向用户说明拟写入的宿主机全局路径、现有模型与自定义尾段保留方式及数据库备份位置，按 `R4` 获明确批准后再正式执行。

## 三、“全面更新”与“全量优化”双循环维护（`fleet-evolve`）

在本上游配置仓库中，当用户提出“**全面更新**”（核查外部工具升级并重算动态角色与定量参数配置，对应 `full` 模式）、“**全量优化**”（基于当前配置全仓加固代码、整篇重写图文文档并做事实锚定与脱敏回归，对应 `hygiene` 模式）或只读巡检（`audit` 模式）时，**先完整读取并遵循 [SKILL.md](template/user-guard/skills/fleet-evolve/SKILL.md)**：

1. 先运行 `fleet_state.py status` 核对本机状态锁与 `workspace_mode == "upstream_config_repo"`，在只读前提下采集客户端状态、模型缓存与运行期参数基线；重要文档重写前按 `R2` 先做 `--clean-room` 空上下文盲读挑刺。
2. 在仓库工作区内完成代码加固、`runtime_tunables` 参数收口、图文文档整篇重写、`cae_harness.py check-grounding --target . --strict` 零黑名单事实锚定核验、对等客户端无头交叉审查（多端凭据就绪时调用 `cae_harness.py` 的 `role:review` 或 `audit`，配合 `readonly-auditor` 逐行核对）及全套离线与沙箱回归测试；触及 [AGENTS.md](template/user-guard/rules/AGENTS.md) `R3.1.4` 四类拍板节点时，执行 `fleet_state.py propose` 并附 `install.py --dry-run` 预览清单呈交用户确认后暂停。
3. 获批后执行 `fleet_state.py approve`、同步宿主机全局目录并运行无头实测验收；若主会话客户端自身升级或切换，按 `record-pass1` 提示用户手动重启后再完成复核，最终通过 `clear --verified --readback-done`（内置事实锚定硬门禁）归位，未获明确指令不执行 `git push`。
