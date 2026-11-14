# 仓库维护、四端自举与升级回归指引（Antigravity、Codex、Claude Code ＆ DeepSeek Harness 共读）

本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）是面向科学计算与工程研发的**上游规则真源与多代理验证台架**，仅依赖 Python 3.11+ 标准库，面向 Windows 与 WSL2／Linux 主力环境。`install.py` 执行后，Google Antigravity（反重力）、OpenAI Codex、Anthropic Claude Code（及 CC Switch）与 DeepSeek Harness（`DSH`）的运行态规则、生命周期钩子、只读子代理及 `cae-harness`、`fleet-evolve` 托管技能均**直接落盘在各客户端宿主机全局根目录**，日常业务仓库无需携带本仓目录；本仓库专责同源模板演进、跨端探测、全栈升级体检与回归验证。

## 一、架构基线：四端全局落盘与专长分工

1. **各端全局落盘边界**：
   - **反重力（`--app antigravity`）**：同步 `~/.gemini/config/plugins/user-guard` 原生插件（`R0–R11` 全局规则、原话恢复、目录硬边界与日志防空转、防刷屏、长会话收口状态机、三个 `inherit` 只读子代理、`cae-harness` 与 `fleet-evolve` 技能），可选配置 `zotero-mcp`。
   - **Codex（`--app codex`）**：同步 `~/.codex/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）、三个 `read-only` 子代理、原话恢复与交接钩子（默认 `advisory`）、`workspace-write` 沙箱、`~/.codex/skills/`（`cae-harness`、`fleet-evolve`）及可选 `zotero-mcp`。
   - **Claude Code 与 CC Switch（`--app claude`）**：同步 `~/.claude/CLAUDE.md`（保留自定义尾段）、三个 `Read/Grep/Glob` 只读子代理、五类钩子（无损合并 `settings.json`，默认补齐 `effortLevel: "xhigh"`）及 `~/.claude/skills/`（`cae-harness`、`fleet-evolve`）；附 `--with-cc-router` 时同步 `~/.claude/router/`（`router.py`、`ccswitch_admin.py` 及 WSLg 脚本）；附 `--disable-ccswitch-prompts` 或 `--purge-ccswitch-legacy` 在线备份并禁用／归档清理 CC Switch 已退役提示词与技能。
   - **DeepSeek Harness（`--app dsh`）**：同步 `~/.dsh/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）与 `~/.dsh/skills/`（`cae-harness`、`fleet-evolve`），复用官方 `DEEPSEEK_API_KEY` 或 `deepseek-account` 凭据，不触碰 `.credentials.yaml`。
2. **前置自理边界、单仓同源分发与二维正交分工**：
   - **用户自理底座 vs. 本仓按需分发**：网络连通／合规代理、各客户端安装升级、官方账号登录态（OAuth）与供应商 API Key 配置属**用户自理前置前提**（探测未就绪时直接暴露告知，严禁内置灰产反代或绕过逻辑）；其上的全部全局规则（`R0–R11`）、通用技能（`cae-harness`、`fleet-evolve`）、生命周期钩子、只读子代理与本地分流路由**仅在本仓库单点维护一套真源**，按各端宿主外壳特性、基座 LLM 特性与本机需求编译分发至各端宿主机全局根目录。
   - **正交分工与薄外层边界**：首次上手或内网环境可直接以官方合规 `DSH + DeepSeek` 独立闭环；多端并存时推荐**反重力 `Gemini 4 Carbon (High)` 居中统管**，PDE 推导与核心求解器编码／门禁**优先经 `cae-harness` 调 Codex（`Astra`／最新代 `Sol` + `max` + `872k` 窗口）攻坚**，终端长上下文或混合分流调 `Claude Code`，配额受限时切 `DSH` 保底（见 [README.md](README.md) 第四章双核心可视化矩阵）。全仓严守 `R3.3` 薄外层边界（仅维护 Python 3.11+、Windows 与 WSL2／Linux 真实所需代码，不重写官方底层工具循环，不留废弃兼容分支）。

## 二、AI 引导本机配置标准流程（四步自举）

当用户提出“帮我配置本机”“检查安装状态”时，按以下四步执行：

1. **只读探测本侧与跨子系统环境（零写入，直接暴露前置缺口）**：
   运行 `python -B install.py --probe-apps`、`python -B install.py --audit-upgrade` 与 `cae_harness.py probe`（WSL2／Linux 用 `python3`），核实本侧及 Windows `<->` WSL 对侧的四端客户端、CC Switch、`zotero-mcp`、资产漂移与底层 LLM 就绪状态；若检出网络不通、未完成官方登录或未填 API Key，直接向用户列明自理清单，不阻塞其余已就绪端配置。
2. **仅检出单端时先请用户拍板**：
   - 若双侧一共只装了一个客户端（如仅装裸态 `DSH`），请用户二选一：**① 直接为当前客户端部署独立工作流（后续开通其他端随时增量同步）**，或 **② 先安装其他客户端再一次配齐多端协同**。
   - 若双侧分别装了不同客户端，先预演配好当前侧，再给出对侧同步命令。
3. **先空跑预演，经 R4 明确批准后再落盘**：
   运行 `python -B install.py --dry-run --yes`（可附 `--app`、`--with-cc-router`、`--disable-ccswitch-prompts`），向用户列清拟写入的宿主机全局路径、既有模型与 `## 工具与...` 尾段保留情况及 `cc-switch.db` 在线备份方案，获同意后再正式执行（模型配置默认仅补缺项，`--sync-model-defaults` 显式覆盖，`--no-model-config` 跳过）。
4. **源码改动后的全量门禁回归**：
   修改本仓源码、规则或模板后，必须跑通第三章第 4 阶段的全量门禁（Codex `Sol` 审查、六模式临时沙箱安装、沙箱 `--audit-upgrade` 零漂移、全量 `unittest` 与脱敏核查），经用户明确批准后再提交推送或同步生产环境。

## 三、全栈舰队自举演进与升级回归（`fleet-evolve` 最高优路由）

当用户提出模糊或明确的全面升级／重算诉求（如“全面更新整套配置”“根据各家升级做适配与回归测试”“重算各端与模型方案”“全量清淤回归”）时，**必须首先读取并严格遵循 [`template/user-guard/skills/fleet-evolve/SKILL.md`](template/user-guard/skills/fleet-evolve/SKILL.md)** 执行四阶闭环：

1. **状态锁判定与全员只读并发调研（Phase 1）**：通过 `fleet_state.py status` 识别 `IDLE`（Pass 1）或 `AWAITING_MASTER_RELOAD`（Pass 2，防死循环重入）；在零写入前提下并发运行双侧 `--audit-upgrade`、`cae_harness.py probe --discover-models`／`assess`、外部官方变更调研与本机历史翻车账本匿名深挖。
2. **二维正交重算与双矩阵原地维护（Phase 2）**：按 `宿主外壳控制面 × 基座模型推理面` 与单模型最高纯思考挡原则更新代码映射，并在 `README.md` 第四章原地更新**双核心可视化矩阵**（矩阵一：Agent/LLM 正交适配矩阵；矩阵二：用户模糊用法与精确调起矩阵；严守 `R0` 不拆独立矩阵文件，严守 `R3.6` 区分三层证据）。
3. **自举落地与真机 CLI 探针验收（Phase 3）**：预演并获 `R4` 批准后同步宿主机配置，对各已就绪后端执行真实无头往返探针（硬核验 `returncode == 0` 与 `all_aligned: true`）；主控无需换壳换模时单趟直通，需切换主控时写入 `fleet_state.py record-pass1` 挂起待重入。
4. **全仓减法清淤、沙箱单测与待推送硬挂起（Phase 4）**：完成 Codex `Sol (max)` 门禁审查、六模式临时沙箱安装与 `--audit-upgrade` 零漂移、`agy plugin validate --strict`、`python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v`、双 `AGENTS.md < 16,000 B`、`plugin.json` 单一版本真源及公开脱敏终审，最后输出 `git status -sb` 与变更报告，**严禁自动 `git push`**，等待用户明确指令。
