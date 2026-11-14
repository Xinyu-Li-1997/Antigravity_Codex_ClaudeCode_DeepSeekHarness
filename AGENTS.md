# 仓库维护、四端自举与升级回归指引（Antigravity、Codex、Claude Code ＆ DeepSeek Harness 共读）

本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）是面向科学计算与工程研发的**上游规则真源与多代理验证台架（Harness）**，仅依赖 Python 3.11+ 标准库，面向 Windows 与 WSL2／Linux 主力环境。`install.py` 执行后，Google Antigravity（反重力）、OpenAI Codex、Anthropic Claude Code（及 CC Switch）与 DeepSeek Harness（`DSH`）的全部运行态规则、生命周期钩子、只读子代理与 `cae-harness` 技能均**直接落盘在各客户端自身的宿主机全局配置根目录**，日常科研或求解器项目无需携带或依赖本仓库目录；本仓库专责同源模板演进、跨端环境探测、全栈升级体检与自动化回归验证。无论用户在上述任一客户端中打开本仓库，当前 AI 助手均可按以下规范完成自举部署与升级回归。

## 一、架构基线：四端对等层级、渐进接入与按专长分工

反重力、Codex、Claude Code（`cc`）与 DeepSeek Harness（`dsh`）在逻辑层级与被调能力上完全对等——四端既能各自独立闭环，也能作为 `cae-harness` 的发起方（`--caller`）或无头执行后端（`--engine`）：

1. **各端全局落盘边界**：
   - **反重力（`--app antigravity`）**：同步 `~/.gemini/config/plugins/user-guard` 原生插件（`R0–R11` 全局规则、压缩原话恢复、目录硬边界与日志防空转门禁、防刷屏拦截、长会话收口状态机、三个 `inherit` 只读子代理及 `cae-harness` 技能），可选配置 `zotero-mcp`。
   - **Codex（`--app codex`）**：同步 `~/.codex/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）、三个 `read-only` 子代理、压缩原话恢复与交接钩子（默认 `advisory`）、`workspace-write` 沙箱基线、`~/.codex/skills/cae-harness` 及可选 `zotero-mcp`。
   - **Claude Code 与 CC Switch（`--app claude`）**：同步 `~/.claude/CLAUDE.md`（保留自定义尾段）、三个 `Read/Grep/Glob` 只读子代理、五类生命周期钩子（无损合并 `settings.json` 并迁移旧版拆分式钩子，默认补齐 `effortLevel: "xhigh"`）及 `~/.claude/skills/cae-harness`；附带 `--with-cc-router` 时同步 `~/.claude/router/`（Kimi+DeepSeek 本地分流路由 `router.py`、CC Switch 卡片无损投影 `ccswitch_admin.py` 及 WSLg 脚本）；附带 `--disable-ccswitch-prompts` 时原子备份并禁用 `cc-switch.db` 陈旧提示词。
   - **DeepSeek Harness（`--app dsh`）**：同步 `~/.dsh/AGENTS.md`（保留末尾自定义 `## 工具与...` 段落）与 `~/.dsh/skills/cae-harness`，直接复用用户在 DSH 官方渠道配置的 API Key（`DEEPSEEK_API_KEY`）或官方账号登录态（`deepseek-account`），不触碰 `.credentials.yaml` 凭据文件。
2. **零门槛合规起步与日常反重力统管**：
   - **首次上手与应急兜底（`DSH + DeepSeek`）**：初次接触本工作流或受限于外网环境的开发者，可先用官方合规的 `DSH + DeepSeek`（零反代等灰色操作）一键跑通整套 `R0–R11` 规范与 `cae-harness` 闭环，后续按需开通任一客户端即可平滑接管；多端并存时，`DSH + DeepSeek` 同时充当其他三端故障或配额耗尽时的保底调试工具。
   - **日常多端统管与硬核分工（`--app both`、`--app all` 或默认 `auto`）**：日常科研与综合研发推荐由**反重力（单模型 `Gemini 4 Carbon (High)`）居中统管**所有客户端与模型的更新体检、额度状态及上下文窗口规划；偏微分方程推导、高阶数值格式分析、核心求解器编码、深层边界审查与验证门禁**优先通过 `cae-harness` 调 Codex（`Astra`／最新代 `Sol` + `max` 最高挡 + 声明最大窗口）攻坚**，也可按用户点名或模糊意图调度已接入可用 LLM 的 `Claude Code`（无 `--bare` 原生继承 `~/.claude` 与 CC Switch 卡片）或 `DSH`（`DeepSeek-V4-Pro`／`DeepSeek-V4.1-Flash`）；附带 `--fallback-on-quota` 时，若目标端在预检或启动期额度耗尽且未改动工作区文件，自动熔断并切回发起方本侧或可用对等端完成。
3. **反过度工程与阶段收口全量把关**：
   全仓严守 `R3.3` 反过度工程红线，只维护目标环境（Python 3.11+、Windows 与 WSL2／Linux）真实需要的精简代码，不为废弃旧版或假想场景堆砌兼容层。里程碑收口或发版前按全量文件分工闭环——由 Codex 无头工作流负责代码回归、Debug 排错、死代码清理与门禁验证；由反重力负责文档润色、清淤精简与隐私脱敏，经总控逐行复核差异与实测证据后收口。

## 二、AI 引导本机配置标准流程（四步自举）

当用户提出“帮我配置本机”“升级配置”或“检查安装状态”时，严格按以下四步执行：

1. **只读探测客户端与跨子系统环境（零写入）**：
   运行 `python -B install.py --probe-apps` 与 `python -B install.py --audit-upgrade`（WSL2／Linux 用 `python3`），读取输出 JSON，核实本侧及跨子系统对侧（Windows `<->` WSL）是否已安装反重力、Codex、Claude Code、DeepSeek Harness、CC Switch 与 `zotero-mcp`，查清规则／钩子／技能漂移与底层 LLM 就绪状态。
2. **仅安装单端时先请用户拍板**：
   - 若本侧与跨子系统对侧**一共只装了其中一个客户端**（例如仅装了裸态 `DSH`），如实告知探测结果，请用户二选一：**① 直接为当前客户端部署完整独立工作流（后续开通其他客户端随时增量同步）**，或 **② 先安装其他客户端后再一次配齐多端协同**。
   - 若本侧与 WSL／Windows 对侧分别装了不同客户端，先按预演流程配好当前侧，再给出对侧终端的同步命令。
3. **先空跑预演，经 R4 明确批准后再落盘**：
   先运行 `python -B install.py --dry-run --yes`（可附用户选定的 `--app`、`--with-cc-router`、`--disable-ccswitch-prompts`）生成变更计划。向用户列清拟写入的宿主机全局路径、既有模型与自定义 `## 工具与...` 尾段保留情况、`cc-switch.db` 在线备份策略及钩子合并方案，获明确同意后再正式安装。前端默认模型仅补缺失项（`--sync-model-defaults` 显式覆盖，`--no-model-config` 完全跳过，二者互斥）；Codex 与 Claude Code 交接默认 `advisory` 且压缩阈值 `0`。
4. **源码改动后的全量回归、清淤脱敏与沙箱隔离验证**：
   修改本仓库任何源码、规则或模板后，先完成 Codex 代码回归 Debug 与反重力文档润色脱敏，再在系统标准临时目录跑通六模式沙箱隔离安装（`codex`、`antigravity`、`claude`、`dsh`、`both`、`all`）、沙箱 `--audit-upgrade` 零漂移校验与全量离线测试（`python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v`），确认零个人路径、零硬件型号、零内网地址、零密钥凭据且 `NOTICE`／`LICENSE` 齐备后，征得用户同意再提交推送或同步生产环境。

## 三、客户端、CC Switch 与大模型升级标准适配回归流程（触发式六步闭环）

当用户提出**“根据各自升级做适配与回归测试”**（或针对各端客户端、CC Switch、大模型迭代执行例行回归）时，按以下六步流水线端到端闭环：

1. **全栈版本与大模型目录只读体检**：
   依次运行 `python -B install.py --audit-upgrade`、`python -B template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --discover-models` 与 `assess`（启用 WSL 时同步只读检查 WSL 侧），采集反重力、Codex、Claude Code、DSH、CC Switch 的最新版本号、SQLite `user_version`、钩子／无头 CLI 协议，以及四端最新大模型目录（`Astra`/`Sol`/`Carbon`、Kimi/DeepSeek 卡片模型 ID、`DeepSeek-V4-Pro`/`DeepSeek-V4.1-Flash`、声明最大窗口、可调思考挡位及下线模型状态）。
2. **最新大模型能力匹配与协议差异适配**：
   针对模型代际演进、窗口扩容、思考挡位调整、无头 CLI 参数变化或 CC Switch 表结构更新，精准修订 `install.py`、`cae_harness.py`、`template/claude/router/` 及对应模板；严守 `R3.3` 清理过时模型映射，不留废弃兼容分支。
3. **Codex 无头代码审查、Debug 与门禁验证**：
   通过 `cae-harness`（最新代 `Sol` + 单模型最高挡 `max` + 声明最大窗口）对全部改动源码开展边界审查、死代码清理与带 `--verify-cmd` 的自动化门禁闭环。
4. **六模式沙箱隔离安装与离线单元测试**：
   在系统标准临时目录跑通 `codex`、`antigravity`、`claude --with-cc-router`、`dsh`、`both`、`all --with-cc-router` 六种沙箱安装、沙箱 `--audit-upgrade` 零漂移复核与全量 `unittest` 套件，严守 `R3.6` 绝不弱化断言。
5. **技术文档润色、清淤精简与公开脱敏审计**：
   同步更新 `CHANGELOG.md` 与文档能力矩阵和架构说明（保持 `plugin.json` 单一版本真源、纯 ASCII 路径与 `Apache-2.0` + `NOTICE` 归属完整，落实 `R1/R2` 规范书面语与 `R5` 零无声放大），派发只读审计子代理核对行号依据，并通过全仓脱敏扫描。
6. **宿主机双侧空跑预演与生产切流收口**：
   输出 Windows 与 WSL 双侧 `--dry-run --yes` 变更清单（含四端全局目录、`cc-switch.db` 备份与本地路由重启方案），获用户按 `R4` 批准后同步生产环境，最后复跑 `--audit-upgrade` 与四端健康预检确认零漂移收口。
