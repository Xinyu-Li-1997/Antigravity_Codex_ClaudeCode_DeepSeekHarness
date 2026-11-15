# 仓库维护与四端元驱动配置更新指引（Antigravity、Codex、Claude Code 与 DeepSeek Harness 共读）

本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）集中维护面向科学计算、CAE／CFD／FEA 数值求解器、计算几何／CAM 及高可靠系统研发的四端共用常驻规则、生命周期钩子、只读子代理、受控模型评估清单与后台协作脚本，仅依赖 Python 3.11+ 标准库，支持 Windows 与 WSL2／Linux。运行 `install.py` 后，资产按需写入四个客户端在宿主机上的全局配置目录，日常课题项目内无需放入本仓库文件。

## 一、两层元驱动结构与四个客户端安装范围

1. **常驻机制层与动态评估快照层彻底解耦**：
   - **常驻机制层（硬机制与软契约）**：全局规则 [AGENTS.md](template/user-guard/rules/AGENTS.md)、技能说明、生命周期钩子、`install.py`、`cae_harness.py` 与 `ccswitch_admin.py` **绝不写死季节性模型名称**，也不把某一个客户端永久焊死为总控，统一通过抽象角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）和确定性状态锁运行。
   - **受控评估快照层**：具体某一时期“哪个客户端配哪款模型、锁哪个挡位、有哪些致命短板与隔离要求、哪些平台因风控或退役暂不启用”，集中且唯一保存在 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 与 [README.md](README.md) 第四章表一中，由 `fleet-evolve` 经双向优缺点评估并获**用户明确拍板**后更新。
2. **各客户端写入的宿主机全局目录**：
   - **Antigravity（`--app antigravity`）**：同步 `~/.gemini/config/plugins/user-guard` 原生插件（含 `R0–R11` 规则、原话归档恢复、项目外写操作拦截、重复空等提醒与长会话交接钩子、三个 `inherit` 只读子代理、`cae-harness` 与 `fleet-evolve` 技能及 `fleet_profile.json`），可选配置 `zotero-mcp`。
   - **Codex（`--app codex`）**：同步 `~/.codex/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）、三个 `read-only` 子代理、原话恢复与长对话提醒钩子（默认 `advisory`）、`workspace-write` 沙箱与模型补缺、`~/.codex/skills/` 及可选 `zotero-mcp`。
   - **Claude Code 与 CC Switch（`--app claude`）**：同步 `~/.claude/CLAUDE.md`（保留自定义尾段）、三个只读子代理、五个生命周期钩子（合并进 `settings.json`，未配置推理强度时默认补 `effortLevel: "xhigh"`）与 `~/.claude/skills/`；加 `--with-cc-router` 时同步 `~/.claude/router/` 本地分流脚本；加 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 时先在线备份 `cc-switch.db`，再停用或清理 CC Switch 内旧提示词与旧技能记录。
   - **DeepSeek Harness（`--app dsh`）**：同步 `~/.dsh/AGENTS.md`（保留自定义尾段）与 `~/.dsh/skills/`，直接复用用户已配好的官方 `DEEPSEEK_API_KEY` 或 `deepseek-account` 凭据，全仓绝不修改 `.credentials.yaml`。

## 二、本机首次安装与日常检查步骤

当用户提出“帮我配置本机”或“检查一下安装状态”时，按以下顺序执行：

1. **先做零写入环境探测**：运行 `python -B install.py --probe-apps`、`python -B install.py --audit-upgrade` 与 `cae_harness.py probe`（Linux／WSL 下用 `python3`），查清双侧客户端安装状态、CC Switch 卡片、配置差异、工具链 ABI 依赖耦合以及模型凭据就绪度。缺失网络代理、登录态或 API Key 的客户端直接列出待办项，绝不编造绕过手段。
2. **仅检测到单个客户端时先请用户选择**：请用户决定 ① 先单独配好当前客户端直接开展工作，或 ② 安装其他客户端后再统一配置多端协作。
3. **空跑预览并获 `R4` 批准后落盘**：运行 `python -B install.py --dry-run --yes`（按需附 `--app`、`--with-cc-router`、`--disable-ccswitch-prompts`），向用户说明拟写入的宿主机全局路径、现有模型与自定义尾段保留策略及数据库备份方式，获明确同意后再正式执行。

## 三、全套工具升级、模型重算与仓库整理流程（`fleet-evolve`）

当用户提出“全面更新”“全升级所有用到的工具”“评估各家最新模型重算推荐表”或“全量整理优化仓库代码与文档”时，**先读取并遵循 [SKILL.md](template/user-guard/skills/fleet-evolve/SKILL.md)**（统一涵盖 `full`、`audit`、`hygiene` 三种模式）：

1. **第 1 阶段：状态锁检查与双向优缺点并发采集（只读）**：
   - 运行 `fleet_state.py status` 区分首次进入（`IDLE`）、等待用户拍板推荐表（`AWAITING_USER_APPROVAL`）与重启主控后的第二轮验证（`AWAITING_MASTER_RELOAD`）。
   - 并发采集双侧 `--audit-upgrade`、`cae_harness.py probe --discover-models`、本机跨项目真实报错与代理请求日志、官方更新说明及社区长任务排错报告。**评估时既找长板，更优先深挖致命短板**（风控封号、5 小时／7 天硬限流、协议转换反代 `HTTP 422` 与断流、`ultra` 子代理派生死等、高频上下文压缩丢指令），以致命短板作为一票否决或硬隔离依据。
2. **第 2 阶段：仓库内修订、离线回归与用户拍板节点（写入宿主机前必停）**：
   - 在仓库工作区内更新代码、[fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json) 草案与文档（按 `--clean-room` 新手盲读 -> 主控重写自然工程中文 -> `readonly-auditor` 逐行技术与脱敏核验三步完成）。
   - 先在仓库与临时沙箱内跑通全套离线测试：六种安装模式沙箱零差异、`agy plugin validate --strict`、`python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v`、两份 `AGENTS.md < 16,000 B`、单一版本号、本地链接及全仓脱敏检查。
   - 若生成或变更了推荐的 `客户端 × 模型 × 推理挡位` 表，运行 `fleet_state.py propose --session-id <sid> --summary "<摘要>"` 记录待审状态，连同非主控工具三级升级评估（第一级独立 CLI 可直升；第二级同次版本补丁／`wsl --update` 须先查进程与算例占用；第三级系统 Python 跨次版本、CUDA 跨大版本、WSL 跨 LTS 发行版强制警告拦截）与宿主机 `install.py --dry-run` 预览清单**一并呈交用户拍板并原地暂停**。
3. **第 3 阶段：获批后升级非主控工具、同步宿主机并做真机验证**：
   - 用户批准推荐表与 `R4` 写入范围后，运行 `fleet_state.py approve --session-id <sid>` 解锁，完成获批工具升级与双侧 `install.py` 同步（绝不强行关闭正在运行的主控进程自身），并对每个就绪后端跑一次真实无头 CLI 调用（核验 `returncode == 0` 与 `summary.all_aligned == true`）。
   - 若总控客户端自身需切换或升级版本，运行 `fleet_state.py record-pass1` 给出简明手动重启步骤后暂停，待用户重启后进入第二轮验证。
4. **第 4 阶段：终审登记与推送挂起**：运行独立代码审查与主控 `R6` 行号复核，执行 `fleet_state.py clear --verified --readback-done`，输出 `git status -sb` 与验证摘要，**绝不自动执行 `git push`**，等用户明确下令后再推送。
