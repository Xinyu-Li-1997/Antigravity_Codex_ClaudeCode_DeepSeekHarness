# 仓库维护、四端自举与全栈演进指引（Antigravity、Codex、Claude Code ＆ DeepSeek Harness 共读）

本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）是面向 CAE／CFD／FEA 求解器研发与科学计算的**上游规则真源与跨代理验证台架**，仅依赖 Python 3.11+ 标准库，覆盖 Windows 与 WSL2／Linux 环境。`install.py` 执行后，四端运行态规则、生命周期钩子、只读子代理及托管技能均落盘至各客户端宿主机全局根目录，日常科研业务仓库无需携带本仓文件；本仓专责同源模板维护、全栈升级体检、整仓重构清淤与回归门禁。

## 一、同源落盘边界与正交分工

1. **四端宿主机全局落盘范围**：
   - **Antigravity（`--app antigravity`）**：同步 `~/.gemini/config/plugins/user-guard` 原生插件（`R0–R11` 全局规则、原话归档、目录硬边界与防刷屏／收口钩子、三个 `inherit` 只读子代理、`cae-harness` 与 `fleet-evolve` 技能），可选配置 `zotero-mcp`。
   - **Codex（`--app codex`）**：同步 `~/.codex/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）、三个 `read-only` 子代理、原话恢复与交接钩子（默认 `advisory`）、`workspace-write` 沙箱、`~/.codex/skills/` 及可选 `zotero-mcp`。
   - **Claude Code 与 CC Switch（`--app claude`）**：同步 `~/.claude/CLAUDE.md`（保留自定义尾段）、三个只读子代理、五类钩子（无损合并 `settings.json`，默认补齐 `effortLevel: "xhigh"`）及 `~/.claude/skills/`；附 `--with-cc-router` 同步 `~/.claude/router/`；附 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 在线备份并停用／归档 CC Switch 旧提示词与旧技能。
   - **DeepSeek Harness（`--app dsh`）**：同步 `~/.dsh/AGENTS.md`（保留自定义尾段）与 `~/.dsh/skills/`，复用官方 `DEEPSEEK_API_KEY` 或 `deepseek-account` 凭据，绝不触碰 `.credentials.yaml`。
2. **前置自理 vs. 单仓分发**：
   - **用户自理底座**：网络连通／合规代理、客户端初始安装、官方账号登录态（OAuth）与 API Key 属前置前提；探测未就绪时直接暴露自理清单，严禁编写绕过逻辑。
   - **二维正交分工**：单端起步可用 `DSH + DeepSeek` 独立闭环；多端并存时推荐**反重力 `Gemini 4 Carbon (High)` 居中总控**，PDE 离散推导与求解器核心编码／物理门禁**经 `cae-harness` 调 Codex（`Astra`／最新代 `Sol` + `max` + `872k`）攻坚**，终端长上下文或混合分流调 `Claude Code`，配额受限切 `DSH` 保底（以 [README.md](README.md) 第四章双矩阵为唯一真源）。

## 二、本机初始化与增量同步四步法

当用户提出“配置本机”“检查安装状态”时，严格按顺序执行：

1. **只读探测双侧环境（零写入）**：运行 `python -B install.py --probe-apps`、`python -B install.py --audit-upgrade` 与 `cae_harness.py probe`（Linux／WSL 用 `python3`），核实本侧及 Windows `<->` WSL 对侧客户端、CC Switch、资产漂移、工具链耦合与底层 LLM 就绪态；未登录或未填 Key 的端直接列出自理项，不阻塞其余已就绪端部署。
2. **单端场景先请用户拍板**：若双侧仅装有一个客户端，请用户二选一——① 先独立部署当前端工作流，或 ② 安装其他客户端后一次配齐多端协同。
3. **空跑预演并获 `R4` 批准后落盘**：先运行 `python -B install.py --dry-run --yes`（可附 `--app`、`--with-cc-router`、`--disable-ccswitch-prompts`），向用户列明拟改写的宿主机全局路径、既有模型与自定义尾段保留情况及 `cc-switch.db` 备份方案，获明确批准后再正式执行。
4. **源码改动必过全量门禁**：凡修改本仓代码、钩子或文档，必须跑通第三节 Phase 4 全量门禁后再提交或同步宿主机。

## 三、全栈舰队升级、整仓重构与回归门禁（`fleet-evolve` 最高优路由）

当用户提出“全面更新／全升级”“按最新官方动态重算配置”“全仓优化代码与文档可读性”“清淤回归”时，**必须首先读取并遵循 [SKILL.md](template/user-guard/skills/fleet-evolve/SKILL.md)**，在一个顶层模块内流转 `full`（全栈升级演进）、`audit`（只读体检评估）与 `hygiene`（整仓重构清淤）三种单趟模式：

1. **状态锁判定与全员只读并发调研（Phase 1）**：通过 `fleet_state.py status` 识别 `IDLE`（Pass 1）或 `AWAITING_MASTER_RELOAD`（Pass 2）；零写入并发执行双侧 `--audit-upgrade`（含 `host.toolchain` 耦合诊断）、`cae_harness.py probe --discover-models`／`assess`、各组件上游版本核查与本机历史翻车复盘。
2. **全栈工具链三级耦合分级与双矩阵原地维护（Phase 2）**：
   - **全栈非主控升级范围与耦合分级（严禁自杀式热更当前活跃主控进程）**：
     - **Tier 1（无耦合外部 CLI／工具，获 `R4` 批准后原地升级）**：非主控 Agent CLI（如 `claude update`、`codex`、`dsh`、`cc-switch`）、`Python.Launcher`（`py.exe`）与仓库 CI Action 版本，升级后逐一跑真机探针核验。
     - **Tier 2（同次版本运行时补丁／WSL2 内核，先验进程锁再执行）**：如 Python 同次版本安全补丁（`3.13.x -> 3.13.y`，保持 `cp313` ABI 兼容）或 `wsl --update`（会重启运行中的 WSL 实例）；须先检查是否有活跃钩子占用 `python.exe` 或后台正在运行 MPI／求解器算例，无锁安全更新，有锁则明确提示用户在空闲窗口执行。
     - **Tier 3（重度二进制／环境耦合大版本跳跃，强制警告拦截、严禁强升）**：如含 `jax`/`jaxlib`/`scipy`/`mpi4py`/`petsc4py` 等编译扩展且绑定全局钩子的系统 Python 跨次版本升级（`3.13 -> 3.14`），或 WSL 跨 LTS 发行版大版本升级（`do-release-upgrade` 破坏 `glibc`/`gfortran`/MPI ABI）；必须向用户输出显式**依赖耦合警告**并推荐 `uv`／`venv` 独立环境方案，绝不原地强行覆盖。
   - **双矩阵与单点真源重构**：在 [README.md](README.md) 第四章原地维护**矩阵一（Agent/LLM 正交适配矩阵）**与**矩阵二（用户模糊指令、全栈升级与整仓重构精确调起矩阵）**；全仓文档严守 `R0` 整篇重写，将客户端钩子协议与代理细节收敛至 [architecture-and-hooks.md](docs/architecture-and-hooks.md)，保持面向 CAE 工程师的精炼表述。
3. **非主控实装与真机探针验收（Phase 3）**：获 `R4` 批准后执行 Tier 1（及无锁 Tier 2）升级与双侧宿主机配置同步，对各已就绪后端发起真实 CLI 往返探针（硬核验 `returncode == 0` 与 `all_aligned: true`）；若主控自身需换壳／升级客户端，写入 `fleet_state.py record-pass1` 并输出手动更新指引后挂起等待 Pass 2 重入。
4. **整仓门禁终审与待推送硬挂起（Phase 4）**：依次通过 Codex `Sol (max)` 独立代码审查与主控 `R6` 逐行复核、六模式临时沙箱安装与 `--audit-upgrade --with-cc-router` 零漂移、`agy plugin validate --strict`、`python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v`、双 `AGENTS.md < 16,000 B`、`plugin.json` 单一版本真源、链接完整性与公开脱敏核查；最后输出 `git status -sb` 与验收清单，**严禁自动 `git push`**，等待用户明确指令。
