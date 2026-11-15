---
name: fleet-evolve
description: >
  Maintain the upstream multi-agent configuration repository (Antigravity_Codex_ClaudeCode_DeepSeekHarness)
  via two autonomous loops: "全面更新" (full: non-active CLI upgrades with ABI coupling checks + dynamic
  5-role and runtime_tunables recalibration) and "全量优化" (hygiene: codebase hardening, clean-room
  engineering Chinese documentation rewrite, zero-blacklist grounding verification, and sandbox regression).
  Trigger ONLY inside the upstream config repository or when explicitly asked to evolve global host configs;
  never trigger for routine refactoring inside general scientific/engineering project repositories.
---

# 上游配置仓“全面更新”与“全量优化”双循环规程（`fleet-evolve`）

本技能**专用于维护上游全局配置仓库**（`workspace_mode == "upstream_config_repo"`，即工作区同时包含 `install.py` 与 `template/user-guard/plugin.json`）或用户显式指明演进宿主机四端全局工具链的场景。若当前处于普通课题项目（`workspace_mode == "general_project"`）且用户仅要求优化当前项目代码或文档，严禁调用本技能或 `fleet_state.py`，应直接使用 `cae-harness` 与同端 `readonly-*` 子代理在项目目录内完成。

在上游配置仓库中，日常维护收敛为两大自主闭环与一种只读检查模式：

- **`full`（“全面更新”闭环）**：采集本机四端探测缓存、运行报错日志与官方文档 -> 重新评估全部五个动态角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）及运行期定量参数（`runtime_tunables`） -> 登记草案并停机请用户拍板 -> 执行获批的非当前客户端升级与宿主机全局同步 -> 本机无头实测与全仓回归。
- **`hygiene`（“全量优化”闭环）**：在不升级外部工具的前提下，基于当前已审定客户端与模型配置校准 `runtime_tunables`、消除散落死数字并加固钩子与编排脚本边界 -> 按“`--clean-room` 空上下文盲读挑刺 -> 平实工程中文图文重写 -> `check-grounding --strict` 零黑名单事实锚定核验 + `readonly-auditor` 逐行技术与脱敏复核”重构全仓文档与矢量图 -> 跑通六模式沙箱与全套离线单测 -> 如需同步宿主机全局目录，附 `--dry-run` 清单停机请用户拍板。
- **`audit`（只读漂移与 ABI 巡检）**：全程零磁盘写入，核查 Windows 与 WSL2 下的四端配置对齐度、CC Switch 卡片状态、报错冷却记录及全局科学计算扩展包 ABI 耦合风险。

---

## 一、两层解耦架构与全动态参数收口范围

为使常驻治理代码在模型换代与客户端上下文策略变化时保持零返工，所有动态选型与定量运行阈值统一收口至 [fleet_profile.json](../cae-harness/fleet_profile.json)：

| 分层 | 受控文件与模块 | 维护职责与硬约束 |
| :--- | :--- | :--- |
| **常驻机制与门禁层** | `cae_harness.py`、`fleet_state.py`、`install.py`、`router.py`、`ccswitch_admin.py`、五类生命周期钩子、`tests/` | 零时期性模型名硬编码、零单一主控端锁死、零散落业务魔数；提供工作区身份识别（`workspace_mode`）、文件读写白名单哈希快照、运行侧配额冷却、事实锚定核验（`check-grounding`）及审批／重启重入状态锁。 |
| **全动态评估与参数层** | [fleet_profile.json](../cae-harness/fleet_profile.json)（人工审阅视图见根目录 [README.md](../../../../README.md) 表一与架构说明表） | 1) **五类全动态角色槽位**：`orchestrator`、`math`、`code`、`review`、`fast` 的客户端候选顺序、推荐模型系列、推荐推理挡位与短板隔离策略；<br>2) **运行期定量参数（`runtime_tunables`）**：各端压缩后内联恢复总字数、实质子代理筛选窗口与最小有效字数、长会话交接阈值、空等与日志轮询阈值、无头分片安全系数、润色长短篇分界及本地路由并发／缓存上限。 |
| **工程规则与审阅层** | [AGENTS.md](../../rules/AGENTS.md)、技能说明、`readonly-*` 子代理、三步文档校核规程 | 约束优缺点同步实证评估、自然工程书面语、防无声放大（`R5`）及主会话亲自核对原文件精确行号（`R6`）。 |

---

## 二、全角色与定量参数动态校准原则及四个必停拍板节点

1. **五类角色槽位全部动态评估（非仅主控端动态）**：
   - `orchestrator`、`math`、`code`、`review`、`fast` 五个槽位均无永久绑定的客户端或模型家族；文档、矩阵与矢量图表述必须与 `<client>:@<role>`／`role:*` 代码路由机制严格一致，明确区分“恒定架构机制”与“现阶段实测趋势”。
   - **实证优势决定当前分工，严重短板决定排除或参数隔离**：核对中文表达清晰度、长链条公式推导严密性、多文件补丁与定量验证收敛率、生命周期钩子与原生 `--json-schema`／`--output-schema` 支持度、以及客户端订阅滚动／周度配额与 API 自动前缀缓存计费；对平台区域限制、高频配额上限、出厂默认窗口／挡位偏保守、第三方 `Responses API` 转换反代 `HTTP 422` 断流、`ultra` 自动委派死等及低阈值自动压缩丢上下文等问题，在 `fleet_profile.json` 中逐项绑定隔离参数。
2. **`runtime_tunables` 按主会话客户端与子代理特性定量整定**：
   - **压缩恢复参数（`compaction_recovery`）**：结合各客户端交互式自动压缩触发频率与可用窗口设定 `per_client` 内联预算，并通过 `(recent_subagent_window, recent_subagent_returns, min_substantive_subagent_chars, priority_subagent_keywords)` 动态决定重读哪些子代理结果——始终保留最新一次返回，并在候选窗口内优先保留达到实质字数阈值的审查／推导报告，防止简短状态回执挤掉关键行号证据。
   - **交接、防轮询与无头派工参数（`handoff_thresholds`、`loop_guards`、`headless_dispatch`）**：统一由 `handoff_gate.py`、`reply_repeat_guard.py`、`status_guard.py`、`router.py`、`cae_harness.py` 与 `install.py` 读取，支持通过环境变量 `CAE_FLEET_PROFILE` 在离线沙箱中独立验证，同时保留单元测试属性打桩兼容。
3. **四个必停用户拍板门禁（`R3.1.4`／`R4`）**：
   - ① 生成或调整推荐的 `客户端 × 模型 × 推理挡位` 矩阵或 `runtime_tunables` 定量参数时（执行 `fleet_state.py propose` 登记待审）；
   - ② 向宿主机全局配置目录写入变更（正式运行 `install.py`）或切换主会话客户端时；
   - ③ 涉及装有 `jax`、`scipy`、`mpi4py` 等编译扩展的系统级 Python 跨次版本升级、CUDA 跨大版本升级或 WSL 跨 LTS 发行版升级时；
   - ④ 执行 `git push` 推送远端仓库前。

---

## 三、状态锁与事实锚定门禁命令（`scripts/fleet_state.py`）

状态锁保存在宿主机本地状态目录（Windows 默认 `%LOCALAPPDATA%\cae-harness\fleet-evolve\state.windows.json`，WSL2／Linux 默认 `${XDG_STATE_HOME:-~/.local/state}/cae-harness/fleet-evolve/state.posix.json`），不污染 Git 工作区；在非上游仓库目录未传 `--repo` 调用时会直接返回退出码 `2`（`not_upstream_config_repo`）：

```bash
# 1. 查看当前阶段（IDLE | AWAITING_USER_APPROVAL | AWAITING_MASTER_RELOAD）与工作区身份
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> status

# 2. 登记候选角色矩阵或 runtime_tunables 调整草案并停机等待用户拍板
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> propose --session-id <sid> --summary "<草案摘要>"

# 3. 获用户明确批准后解锁（校验 profile_sha256 与 core_sha256 未被中途篡改）
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> approve --session-id <sid>

# 4. 终审归位（加 --verified --readback-done 时自动联动 audit_doc_grounding 硬门禁，检出虚构术语或失步数字直接拒绝通过）
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> clear --verified --readback-done
```

---

## 四、四阶段执行步骤（`full` 与 `hygiene` 共用骨架）

### 第 1 阶段：只读基线采集与空上下文盲读（`IDLE` 进入或 `audit` 模式）

1. **状态锁与环境探测**：确认 `fleet_state.py status` 处于 `upstream_config_repo`；运行 `install.py --probe-apps`、`install.py --audit-upgrade --with-cc-router`、`cae_harness.py probe`（`full` 模式加 `--discover-models`）与 `cae_harness.py assess --caller auto`。
2. **差异分流**：`full` 模式同步核查外部模型变更、报错冷却记录与三级工具链升级项；`hygiene` 模式跳过外部工具升级，聚焦当前配置下的 `runtime_tunables` 死数字收口、钩子／路由边界加固与文档重写。
3. **空上下文盲读挑刺**：重构重要文档前，可调用 `cae_harness.py task --kind review --clean-room` 从零预设新读者视角挑出术语断层、跨文件重复与潜在幻觉表述。

### 第 2 阶段：工作区加固、文档重写、确定性锚定核验与提请拍板

1. **代码与配置修订**：按 `R6` 核对源码精确行号后完成修订，收口散落定量阈值至 [fleet_profile.json](../cae-harness/fleet_profile.json)。
2. **三步文档与矢量图重构**：主会话抛开旧措辞整篇重写 `README.md`、`docs/*.md` 与 `docs/assets/*.svg`（确保区分“通用项目日常研发”与“上游配置仓维护”两类矩阵）；随后**必须运行零黑名单确定性事实锚定核验**：
   ```bash
   python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict
   ```
   确认零无依据标识符、零陌生大写缩写、零失步容量数字、零虚构领域举例，并由 `readonly-auditor` 完成逐行技术与脱敏复核。
3. **全套离线与沙箱回归测试**：
   - 运行 `agy plugin validate --strict template/user-guard`（本机装有 `agy` 时必跑）；
   - 运行 `python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v`（含六种 `--app` 沙箱安装、`--audit-upgrade` 零漂移、压缩恢复动态预算、角色对称路由与 `check-grounding` 拦截单测）；
   - 核验两份 `AGENTS.md < 16,000 B`、SVG XML 与右边界留白 `>= 8px`、UTF-8 无 BOM + `LF` 编码及 `R4` 公开脱敏。
4. **停机提请用户拍板**：若调整了 `fleet_profile.json` 或需同步宿主机全局目录，运行 `fleet_state.py propose` 与 `install.py --dry-run --yes`，向用户一次汇报变更摘要、三级 ABI 升级评估（`full` 模式）及拟写入路径清单，原地暂停等待用户指令。

### 第 3 阶段：获批后执行全局同步与本机实测验收

1. 用户明确批准后，运行 `fleet_state.py approve`（若有待审草案），执行获批的非当前客户端升级（`full` 模式）及 `install.py` 宿主机同步。
2. 核验 `install.py --audit-upgrade` 返回 `summary.all_aligned == true` 及已就绪客户端无头连通性。
3. 若主会话客户端自身版本或推荐槽位发生变更，执行 `fleet_state.py record-pass1 --session-id <sid> --target-master "<目标端>"` 提示用户手动重启客户端，并在重启后运行 `verify-pass2` 完成复核。

### 第 4 阶段：终审归位与等待推送指令

运行 `fleet_state.py clear --verified --readback-done`（自动再次执行全仓 `audit_doc_grounding` 硬校验），输出 `git status -sb`、`git diff --stat` 与验证等级摘要，未获用户明确指令前严禁自动执行 `git push`。
