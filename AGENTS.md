# 仓库维护与四端配置更新指引（Antigravity、Codex、Claude Code 与 DeepSeek Harness 共读）

本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）集中维护面向 CAE、CFD、FEA 与 PDE 数值求解器研发的四端共用规则、生命周期钩子、只读子代理及后台协作脚本，仅依赖 Python 3.11+ 标准库，支持 Windows 与 WSL2/Linux。运行一次 `install.py` 后，相关配置文件会分别写入四个客户端在宿主机上的全局配置目录，日常求解器课题项目里不需要放本仓库的任何文件；本仓库专门负责模板维护、工具升级检查、代码与文档整理以及自动化回归测试。

## 一、四个客户端的安装内容与配合方式

1. **各客户端写入的全局配置范围**：
   - **Antigravity（`--app antigravity`）**：安装 `~/.gemini/config/plugins/user-guard` 原生插件（包含 `R0–R11` 全局规则、用户原话保存与恢复、项目外写文件拦截、重复空等提醒与长对话交接钩子、三个 `inherit` 只读子代理，以及 `cae-harness` 和 `fleet-evolve` 两个技能），可选写入 `zotero-mcp`。
   - **Codex（`--app codex`）**：更新 `~/.codex/AGENTS.md`（保留用户末尾自定义的 `## 工具与...` 段落）、三个 `read-only` 子代理、原话恢复与长对话提醒钩子（默认 `advisory`）、`workspace-write` 沙箱设置、`~/.codex/skills/` 以及可选 `zotero-mcp`。
   - **Claude Code 与 CC Switch（`--app claude`）**：更新 `~/.claude/CLAUDE.md`（保留自定义尾段）、三个只读子代理、五个生命周期钩子（合并进 `settings.json`，未设置推理强度时默认补上 `effortLevel: "xhigh"`）与 `~/.claude/skills/`；加 `--with-cc-router` 时同步 `~/.claude/router/` 本地分流脚本；加 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 时先在线备份 `cc-switch.db`，再停用或清空 CC Switch 里已退役的旧提示词和旧技能记录。
   - **DeepSeek Harness（`--app dsh`）**：更新 `~/.dsh/AGENTS.md`（保留自定义尾段）与 `~/.dsh/skills/`，直接复用用户已配好的官方 `DEEPSEEK_API_KEY` 或 `deepseek-account` 登录凭据，`install.py` 不读写 `.credentials.yaml`（全仓绝不修改原 `.credentials.yaml`）。
2. **哪些由用户自己准备，哪些由本仓库维护**：
   - **用户事先准备的软件与账号**：网络代理连通、客户端安装、官方账号登录与 API Key 填写由用户本人完成；检查脚本发现缺失时直接列出待办项，绝不编写绕过逻辑。
   - **单端使用与多端配合**：只装了一个客户端（例如只配好 `DSH + DeepSeek`）可以直接独立工作；装了多个客户端时，推荐用**反重力 `Gemini 4 Carbon (High)` 作为主窗口统筹**，把控制方程离散推导、核心求解器编写与带验证命令的自动测试交给 `cae-harness` 后台调用 **Codex（`Astra` 或最新代 `Sol`，开启 `max` 推理强度与 `872k` 窗口）**，终端长上下文改造或 Kimi + DeepSeek 混合审查调 `Claude Code`，其他端额度用尽时切换到 `DSH` 继续工作（具体搭配见 [README.md](README.md) 第四章两张参考表）。

## 二、本机首次安装与日常同步步骤

当用户提出“帮我配置本机”或“检查一下安装状态”时，按以下四步顺序处理：

1. **先做只读环境检查（不改任何文件）**：运行 `python -B install.py --probe-apps`、`python -B install.py --audit-upgrade` 与 `cae_harness.py probe`（Linux 或 WSL 下用 `python3`），查清当前系统及 Windows `<->` WSL 另一侧装了哪些客户端、CC Switch 状态、配置文件是否有差异、全局 Python/WSL 工具链有无二进制依赖耦合，以及各端模型凭据是否可用。未登录或没填 Key 的客户端直接列出待办项，不影响其他已可用客户端的安装。
2. **只检测到一个客户端时先问用户**：如果两边一共只装了一个客户端，请用户二选一——① 先单独配好当前这一个客户端直接使用，或 ② 等装好其他客户端后再一起配置多端协作。
3. **先空跑预览，获 `R4` 批准后再正式写入**：先运行 `python -B install.py --dry-run --yes`（按需附带 `--app`、`--with-cc-router`、`--disable-ccswitch-prompts`），向用户说明将写入哪些全局目录、如何保留原有模型设置与自定义尾段，以及 `cc-switch.db` 的备份方式，拿到明确同意后再正式执行。
4. **改过仓库文件必须先跑完测试**：只要修改了本仓库里的代码、钩子或文档，必须先跑通下面第三节第 4 阶段的全部检查，确认无误后再提交或同步到本机全局目录。

## 三、全套工具升级与仓库整理流程（`fleet-evolve`）

当用户提出“全面更新”“全升级所有用到的工具”“看看各家最新模型动态重算配置”或“全量整理优化仓库代码与文档”时，**先读取并遵循 [SKILL.md](template/user-guard/skills/fleet-evolve/SKILL.md)**，通过同一个入口技能处理 `full`（全套升级与同步）、`audit`（仅做只读体检）与 `hygiene`（全仓代码与文档整理）三种模式：

1. **第 1 阶段：检查状态锁并并发收集现状**：运行 `fleet_state.py status` 区分首次进入（`IDLE`）与重启主控客户端后的第二轮验证（`AWAITING_MASTER_RELOAD`）；在不改文件的前提下，并发执行 Windows 与 WSL 双侧 `--audit-upgrade`、`cae_harness.py probe --discover-models` 与 `assess`、外部版本核查以及本机过往报错复盘。
2. **第 2 阶段：非主控工具三级升级判断与文档三步重写**：
   - **升级除当前正在运行的主控客户端之外的所有相关工具，按依赖耦合程度分三级处理（绝不强行关闭正在运行的主控进程自身）**：
     - **第一级（无外部耦合的独立 CLI 与配置，获 `R4` 批准后直接升级）**：非主控客户端 CLI（如 `claude`、`codex`、`dsh`、`cc-switch`）、Windows `Python.Launcher`（`py.exe`）与 `.github/workflows/ci.yml` 中的 Action 版本，升完逐一用真实命令验证。
     - **第二级（同次版本运行时补丁与 WSL2 内核，先查占用情况再处理）**：例如全局 Python 同次版本安全补丁（`3.13.x -> 3.13.y`，保持 `cp313` 二进制扩展兼容）或 `wsl --update`（会重启正在运行的 WSL 发行版）；必须先确认没有活跃钩子占用 `python.exe`、WSL 内也没有正在跑的 MPI 或求解器算例，无占用时安全更新，有占用则告知用户在空闲窗口执行的具体命令。
     - **第三级（存在严重二进制或科研环境耦合的跨大版本升级，强制警告并拦截）**：例如已经装有 `jax`、`jaxlib`、`scipy`、`mpi4py`、`petsc4py` 等编译扩展且绑定了全局钩子的系统 Python 跨次版本升级（如 `3.13 -> 3.14`）、CUDA 跨大版本升级，或 WSL 发行版跨 LTS 升级（`do-release-upgrade` 会破坏 `glibc`、`gfortran` 与 MPI 二进制兼容性）；必须向用户发出明确的**依赖耦合警告**，推荐使用 `uv` 或项目内独立 `venv` 隔离新版本，严禁原地强行覆盖。
   - **文档三步重写流程（不设固定字数上限）**：① 通过 `cae_harness.py task --clean-room` 启动无全局规则、无钩子、空历史上下文的无头代理（`codex`、`claude`、`dsh`），分别从目标读者新手视角挑出文档里的生造黑话、重复啰嗦与阅读断层；② 主控汇总反馈后抛开旧稿，从头写出逻辑顺畅、贴近 CAE 工程师阅读习惯的自然中文（在 [README.md](README.md) 第四章原地维护两张核心参考表，底层钩子与路由实现细节集中放在 [architecture-and-hooks.md](docs/architecture-and-hooks.md)）；③ 派 `readonly-auditor` 独立核对技术准确度（参数、路径、安全条件与源码一致，不回改自然文风）。
3. **第 3 阶段：同步双侧全局配置并跑真机验证**：获 `R4` 批准后完成工具升级与双侧 `install.py` 同步，对每个可用后端实际运行一次无头 CLI 调用（同时核验 `returncode == 0` 与 `summary.all_aligned == true`）。如果主控客户端自身需要切换或升级，运行 `fleet_state.py record-pass1` 并给出手动更新步骤后暂停，等用户重启主控后继续第二轮。
4. **第 4 阶段：全仓回归检查与等待确认推送**：依次跑通 Codex `Sol (max)` 独立代码审查与主控 `R6` 行号核实、文档三步校核登记（`fleet_state.py clear --verified --readback-done`）、六种安装模式临时沙箱与 `--audit-upgrade --with-cc-router` 零差异检查、`agy plugin validate --strict`、`python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v` 全量单测、两份 `AGENTS.md < 16,000 B` 检查、`plugin.json` 单一版本号与本地链接检查以及全文件脱敏扫描；最后列出 `git status -sb` 与改动摘要，**绝不自动执行 `git push`**，等用户明确确认后再推送。
