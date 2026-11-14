---
name: fleet-evolve
description: >-
  Self-hosting multi-agent fleet evolution, orthogonal Shell x LLM capability
  recalculation, host-global synchronization, and full-stack subtraction hygiene
  protocol across Antigravity, Codex, Claude Code (plus CC Switch), and DeepSeek
  Harness. Trigger with highest priority when the user asks to comprehensively
  upgrade, re-evaluate agent/LLM configurations, or run full regression hygiene.
---

# 多引擎舰队自举演进与全栈清淤回归协议（`fleet-evolve`）

当用户提出模糊或明确的全面升级诉求（如“全面更新整套配置”“根据各家最新动态升级适配”“重算各端与模型分工”“全量清淤回归”，或显式点名 `fleet-evolve`／`fleet-evolve audit`／`fleet-evolve hygiene`）时，优先启用本技能。

- **前置自理边界 vs. 单仓按需分发**：网络连通／合规代理、各客户端安装升级、官方登录态（OAuth）与供应商 API Key 属**用户自理基础前提**（体检发现缺失或网络不通时直接如实暴露提示用户自理，严禁编造绕过或反代逻辑）；底层工具循环、补丁解析、沙箱与压缩留给官方 CLI，本仓库作为**唯一上游真源**维护全部全局规则、通用技能、确定性钩子、只读子代理与本地路由，按各 Agent 外壳特性、基座 LLM 特性与本机任务需求编译分发。

## 一、四条核心铁律

1. **性能上限优先，剥离省钱降挡**：调研阶段并发调动可用只读子代理与无头节点拉满信息密度；目标配置凡涉及数理推导、核心编码、边界审查与门禁验收，默认锁死各端**单模型纯思考最高挡**（`max`／`high`，跳过二次派生子代理的 `ultra` 挡），禁止引入静默降级路由或偷懒低挡。
2. **二维正交选型（宿主外壳控制面 × 基座模型推理面）**：禁止只看榜单换模型名。必须正交评估**宿主外壳机制**（工具闭环摩擦、生命周期 Hooks、`--output-schema`/`--json-schema` 结构化约束、压缩阈值、沙箱写域）与**基座模型能力**（高阶数理深度、长程召回率、工具调用稳定性、直连合规性），明确界定每一组合的不可替代场景（如为何选 `Claude Code + Kimi/DeepSeek` 而非 `DSH + DeepSeek`，何时必须调 `Codex Astra/Sol` 而非主控原生子代理）。
3. **证据降噪权重**：评估信源严格按以下权重排序裁决：**① 本机跨项目真实翻车账本**（收敛阶退化、量纲错配、压缩失忆、钩子异常、尾随停止标签泄漏、跨盘 I/O 超时） **> ② 官方 System Card / Changelog 底层机制与客户端目录实测参数 > ③ 社区硬核长程工程实测 > ④ 通用榜单跑分**（剔除玩具页面打榜软文）。
4. **减法清淤与反膨胀（`R0`/`R3.3`）**：新模型或新 CLI 原生修复的旧缺陷，其历史防呆补丁、废弃兼容分支与重复叮嘱必须同步删净；禁止重写底层执行循环或堆砌脆弱的终端输出解析胶水。

## 二、三种单趟模式与二阶段自举（Bootstrap）入口

支持三种单趟执行模式：**`full`**（默认全流程：并发调研 $\rightarrow$ 矩阵重算 $\rightarrow$ 双侧实装与真机探针 $\rightarrow$ 清淤回归）、**`audit`**（严格只读体检与方案评估，零文件写入）、**`hygiene`**（仅执行代码合理性、减法清淤、脱敏与全量测试回归）。

状态锁脚本位于 [scripts/fleet_state.py](scripts/fleet_state.py)，状态文件独立落盘于宿主机本地运行目录（Windows `%LOCALAPPDATA%/cae-harness/fleet-evolve/state.windows.json`、WSL／Linux `${XDG_STATE_HOME:-~/.local/state}/cae-harness/fleet-evolve/state.posix.json`，绝不污染 Git 工作区或四端配置根目录）。启动 `full` 模式时先运行：

```sh
python -B scripts/fleet_state.py --repo "<上游仓库根目录>" status
```

- **`phase == "IDLE"`（含已完成收口或因超时／侧别不符／仓库演进而自动失效的旧锁）** $\rightarrow$ 进入 **Pass 1**：完成全量只读并发调研、方案重算、仓库与非主控端配置更新及真机 CLI 探针验收：
  - **单趟直通（默认常态）**：若评估结论为当前常驻主控宿主与旗舰模型无需更换（如保持反重力 `Gemini 4 Carbon (High)` 总控），无需写挂起锁，直接进入 Phase 4 完成全仓回归并提示用户“新会话即自动加载更新后的插件技能”。
  - **二阶段挂起（主控需切换宿主或模型时）**：调用 `fleet_state.py --repo "<上游仓库根目录>" record-pass1 --session-id "<当前会话ID>" --target-master "<目标主控>"` 写入绑定 `execution_side`、`repo_commit`、`plugin_version`、`skill_sha256`、`session_id` 与 `24h TTL` 的状态锁，输出手动切换主控清单并暂停；待用户切换主控后重发本指令。
- **`phase == "AWAITING_MASTER_RELOAD"`** $\rightarrow$ 调用 `fleet_state.py --repo "<上游仓库根目录>" verify-pass2 --session-id "<新会话ID>"`：校验通过（确认新会话 ID 异于 Pass 1、未超时且仓库提交一致）后**跳过重复外网调研**，直接进入 **Pass 2** 完成新主控接管核验、跨端联调与 Phase 4 终审，收口时执行 `fleet_state.py --repo "<上游仓库根目录>" clear --verified` 重置回 `IDLE`。校验不通过则报明原因并执行 `fleet_state.py --repo "<上游仓库根目录>" clear` 重置，严防死循环。

## 三、四阶标准流水线

### Phase 1：读写屏障与全员只读并发调研（改配置前零写入）

改动任何仓库文件或宿主机全局配置前，先确认 `git status -sb` 干净可回滚，随后利用当前稳定态的**原生只读子代理 + `cae-harness` 只读节点**并发采集三路证据：

1. **本机全栈目录与漂移体检（双侧零写入）**：
   依次运行仓库根 `python -B install.py --probe-apps`、`python -B install.py --audit-upgrade`（WSL 侧用 `python3`），以及 `cae_harness.py probe --discover-models`、`assess` 与 `ccswitch_admin.py status`，获取四端客户端版本、CC Switch `schema_version`、声明最大窗口、可调思考挡位及规则／钩子／技能漂移清单。
2. **外部官方与硬核实测拉齐**：
   检索 Antigravity / Gemini、OpenAI Codex、Anthropic Claude Code（及 Kimi / DeepSeek 卡片模型）、DeepSeek Harness 最新版本变更、上下文退化拐点、工具调用与新增 CLI 协议约束。
3. **本机跨项目历史账本匿名深挖**：
   只读扫描宿主机各端会话与钩子日志（`lifecycle.log`、`router.log`、历史会话记录），提取真实故障模式（如复杂 glob 异常、无头 JSON 尾随标签泄漏、压缩失忆、沙箱锁冲突）。**严禁将任何个人路径、用户名、主机名、会话 UUID 或密钥写入仓库文件（`R4`）**。
   > **跨端并发投喂准则**：向 `Codex Astra (max)` 或 `Kimi K3.1 (max)` 派发深层只读分析时，须预先标明核心文件与行号区间并设 `--timeout 900` 以上；避免在 WSL 侧对 `/mnt/` 跨盘大目录发起全树遍历。

### Phase 2：全栈正交配置重算与双核心可视化矩阵维护

遵循 `R0` 文件最少原则，**严禁新建独立 `FLEET_MATRIX.md` 或拆分 `README_ZH.md`**；直接在仓库根 `README.md` 第四章原地维护单一权威**双核心可视化矩阵**（**矩阵一：Agent × LLM 二维正交适配矩阵**；**矩阵二：用户模糊说法与 100% 精确调起映射矩阵**），覆盖四层正交参数：
1. **常驻总控（Master）与原生子代理**：确定总控宿主、旗舰单模型、思考挡位、长会话收口阈值，以及同端 `inherit` 只读子代理（`readonly-reader` / `readonly-auditor` / `readonly-collaborator`）与 `run_workflow` 的分流边界。
2. **四端宿主外壳与确定性 Hooks**：对齐反重力、Codex、Claude Code、DSH 的全局规则（保留用户自定义 `## 工具与...` 尾段）、前置目录边界与防空转门禁（`status_guard.py`）、压缩原话恢复（`session_context_loader.py`）、长会话交接（`handoff_gate.py`）及本地分流路由（`router.py` 强制子代理挡位映射）。
3. **双矩阵同步对齐**：矩阵一逐条写清 `角色层级 | 宿主外壳 × 基座模型 | 锁定挡位与窗口 | 核心 Hooks 与隔离约束 | 不可替代适配准则 | 宿主探针状态`（严格区分 `官方声明`、`本机目录声明` 与 `宿主受控实测` 三层证据，`R3.6`/`R5`）；矩阵二逐条对齐日常模糊说法、100% 锁定调起的精确模块名（如 `fleet-evolve`、`cae-harness gate`、`codex:astra`、`readonly-auditor`、`ccswitch_admin.py`）及底层硬约束。
4. **代码同步演进**：在 `install.py`、`cae_harness.py`、`template/claude/router/` 及对应模板中精准落实模型映射、多技能同步与缺陷修复，清理过时模型映射与死代码。

### Phase 3：自举分步落地与真机 CLI 探针门禁

1. **双侧预演与 `R4` 授权落盘**：先执行 `install.py --dry-run --yes`（含 `--with-cc-router` 等选定参数）核验变更计划、既有模型保留与 `cc-switch.db` 在线备份策略；获用户明确授权后同步宿主机全局目录（Windows 与已启用的 WSL 对侧）。
2. **真机 CLI 往返探针（硬退出码门禁）**：对本机已就绪的非主控端逐一跑最小真实无头往返探针（如 `cae_harness.py task` 只读微探针及 `install.py --audit-upgrade`），**必须取得真实 `returncode == 0`、合法结构化输出且 `all_aligned: true`**，严禁仅凭静态配置文件宣称完工；任一探针失败立即定位修复或回滚。

### Phase 4：全仓清淤、脱敏、双语导引与待推送硬挂起（Explicit Push Gate）

1. **Codex 无头回归审查与自动化门禁（`R3.1.4`）**：调用 `cae-harness`（`codex:sol` + `max` 挡 + `--verify-cmd`）对全部改动源码做边界审查、Debug 排错与死代码清理。
2. **六模式临时沙箱隔离安装与离线全量单测**：在系统标准临时目录跑通 `codex`、`antigravity`、`claude --with-cc-router`、`dsh`、`both`、`all --with-cc-router` 六种沙箱安装、沙箱 `--audit-upgrade` 零漂移复核、`agy plugin validate --strict` 及全量 `python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v`（严守 `R3.6` 绝不弱化断言）。
3. **减法清淤、单一版本真源与公开脱敏终审**：
   - 双 `AGENTS.md` 均严格 `< 16,000` UTF-8 字节，保持 `template/user-guard/plugin.json` 单一版本真源（除 `CHANGELOG.md` 外任何 `.md` 严禁硬编码当前版本号）；
   - 保持单本 `README.md` 顶层 `English Overview` 与中文正文能力矩阵同步，纯 ASCII 路径与本地相对链接全部有效，`LICENSE`（Apache-2.0）与 `NOTICE` 归属完整；
   - 派发 `readonly-auditor` 核对行号依据，全仓扫描确认零本机路径、零用户名／主机名、零具体硬件型号、零内网地址、零会话 UUID 与零密钥残留。
4. **待推送硬挂起**：运行 `git status -sb` 与 `git diff --stat`，向用户交付【架构与矩阵变更摘要 + 真机 CLI 探针结果 + 清淤脱敏核查单】。**严禁自动执行 `git push`**，原地等待用户明确下达推送指令后再推送远端。
