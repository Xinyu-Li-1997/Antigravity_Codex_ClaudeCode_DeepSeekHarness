# 仓库维护与四客户端配置同步说明（Antigravity、Codex、Claude Code 与 DeepSeek Harness 共读）

本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）集中维护面向科学计算、CAE、计算流体力学（CFD）、有限元分析（FEA）、计算几何与高可靠系统研发的全局工程规则、会话生命周期钩子、只读子代理、模型评估配置与后台命令行验证脚本。全仓仅依赖 Python 3.11+ 标准库，支持 Windows 与 WSL2／Linux 环境。运行 `install.py` 后，模板文件按需同步到四个客户端在宿主机上的全局配置目录，日常课题项目内无需拷入本仓库文件。

## 一、配置分层与四个客户端同步范围

1. **常驻机制与模型评估快照分离**：
   - **常驻规则与脚本**：全局规则 [AGENTS.md](template/user-guard/rules/AGENTS.md)、技能说明、生命周期钩子、`install.py`、`cae_harness.py` 与 `ccswitch_admin.py` 不写死特定时期的具体模型名称，也不将某一个客户端永久固定为主会话客户端，统一通过抽象角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）和状态锁运行。
   - **模型评估配置**：当前阶段各角色选用哪个客户端、配哪款模型、默认采用哪一挡推理强度与上下文窗口，以及哪些模型因平台限制或退役暂不启用，以机器可读配置文件 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 为准，[README.md](README.md) 第四章表一为其对应的人工审阅视图；更新时须经 `fleet-evolve` 评估并获用户明确批准后同步修改。
2. **各客户端写入的宿主机全局目录**：
   - **Antigravity（`--app antigravity`）**：同步 `~/.gemini/config/plugins/user-guard` 插件（含 `R0–R11` 规则、历轮原话与最近子代理返回摘要双路恢复、项目外写操作拦截、重复空等拦截与长会话交接钩子、三个 `inherit` 只读子代理、`cae-harness` 与 `fleet-evolve` 技能及 `fleet_profile.json`），可选配置 `zotero-mcp`。
   - **Codex（`--app codex`）**：同步 `~/.codex/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）、三个 `read-only` 子代理、历轮原话与最近子代理摘要双路恢复及长会话提醒钩子（默认 `advisory`）、`workspace-write` 沙箱与模型窗口补缺、`~/.codex/skills/` 及可选 `zotero-mcp`。
   - **Claude Code 与 CC Switch（`--app claude`）**：同步 `~/.claude/CLAUDE.md`（保留自定义尾段）、三个只读子代理、五个生命周期钩子（含双路压缩恢复，合并写入 `settings.json`，未配置推理强度时默认补 `effortLevel: "xhigh"`）与 `~/.claude/skills/`；加 `--with-cc-router` 时同步 `~/.claude/router/` 本地分流代理；加 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 时先在线备份 `cc-switch.db`，再停用或清理 CC Switch（本地供应商配置卡片管理工具）内的旧提示词与旧技能记录。
   - **DeepSeek Harness（`--app dsh`）**：同步 `~/.dsh/AGENTS.md`（保留自定义尾段）与 `~/.dsh/skills/`，直接复用用户已配好的官方 `DEEPSEEK_API_KEY` 或 `deepseek-account` 凭据，全仓不修改 `.credentials.yaml`。

## 二、本机首次安装与日常状态检查

当用户提出“帮我配置本机”或“检查一下安装状态”时，按以下顺序执行：

1. **只读环境探测**：依次运行 `python -B install.py --probe-apps`、`python -B install.py --audit-upgrade` 与 `cae_harness.py probe`（Linux 或 WSL2 下使用 `python3`），核查 Windows 与 WSL2 环境下的客户端安装状态、CC Switch 卡片、配置差异、工具链二进制接口（ABI）依赖耦合以及模型凭据就绪状态。缺失网络代理、官方登录态或 API Key 的客户端直接列出待办项，不编造绕过手段。
2. **单客户端确认**：若本机仅检测到单个客户端，先请用户选择 ① 先单独配好当前客户端直接开展工作，或 ② 安装其他客户端后再统一配置多端协作。
3. **空跑预览与获批写入**：运行 `python -B install.py --dry-run --yes`（按需附带 `--app`、`--with-cc-router`、`--disable-ccswitch-prompts`），向用户说明拟写入的宿主机全局路径、现有模型与自定义尾段保留方式及数据库备份位置，按 `R4` 获明确批准后再正式执行。

## 三、工具升级、模型重算与代码文档整理（`fleet-evolve`）

当用户提出“全面更新”“升级所有用到的工具”“评估最新模型重算推荐表”或“优化整理仓库代码与文档”时，**先完整读取并遵循 [SKILL.md](template/user-guard/skills/fleet-evolve/SKILL.md)** 中的四阶段规程（支持 `full`、`audit`、`hygiene` 三种模式）：

1. 先运行 `fleet_state.py status` 核对本机状态锁，并在只读前提下采集客户端状态、模型列表缓存与官方文档变更信息；文档改写前按 `R2` 先做 `--clean-room` 空上下文盲读挑刺。
2. 在仓库工作区内完成代码修订、图文分层文档整篇重写、`readonly-auditor` 逐行核对以及全套离线与临时沙箱回归测试；触及 [AGENTS.md](template/user-guard/rules/AGENTS.md) `R3.1.4` 所列四类拍板节点时，附完整预览清单呈交用户确认并暂停。
3. 获批后同步宿主机全局目录并运行无头命令行实测验收；若主会话客户端自身版本或选型发生变更，按 `record-pass1` 提示用户手动重启后再完成复核，最后整理 Git 状态等待用户指令，不自动执行 `git push`。
