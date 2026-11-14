---
name: fleet-evolve
description: >
  Self-hosting multi-agent fleet evolution, full-stack non-Master toolchain upgrade with dependency
  coupling warnings, orthogonal Shell x LLM recalculation, and whole-file SSOT refactoring hygiene across
  Antigravity, Codex, Claude Code (plus CC Switch), and DeepSeek Harness. Trigger with highest priority
  when the user asks to comprehensively upgrade, audit, refactor, or run full regression hygiene.
---

# 多引擎舰队全栈自举演进与整仓重构协议（`fleet-evolve`）

当用户提出模糊或明确的全面升级、全栈更新或整仓治理诉求（如“全面更新整套配置”“全升级所有用到的工具”“看看各家最新动态重算方案”“全量优化仓库代码和文档可读性”“清淤回归”）时，最高优先级启用本技能。为严守 `R0` 目录复杂度 $O(1)$，本协议不拆分额外插件或独立矩阵文件，内部通过**三种单趟入口模式**流转：

1. **`full`（默认：全栈升级演进 + 宿主机同步 + 整仓门禁回归）**：覆盖除当前活跃主控进程外的全栈工具链分级升级、二维正交选型重算、双侧宿主机同步、真机探针验收与整仓清淤终审。
2. **`audit`（只读体检与耦合评估）**：仅执行 Phase 1–2 只读采集、工具链依赖耦合诊断与方案对比，零文件写入。
3. **`hygiene`（整仓代码／结构／文档单点真源重构与主控 `R6` 终审）**：聚焦全仓源码逻辑、安全边界、死代码清淤、文件结构收敛与面向 CAE 工程师的文档整篇重写（消除补丁式堆叠），执行 Phase 4 全量门禁验收。

---

## 0. 四条硬约束

1. **性能与精度优先，锁定单模型纯思考最高挡**：核心推导、代码审查与物理门禁一律锁定单模型最高纯思考挡（`max`／`xhigh`／`high`），严禁静默降级或套娃启用 `ultra`。
2. **二维正交选型（宿主外壳控制面 × 基座模型推理面）**：同时评估宿主 CLI 外壳（工具循环稳定性、补丁命中率、生命周期钩子、压缩策略、沙箱边界）与基座模型（数学物理推导深度、长上下文保真度、配额与内网可达性），明确给出组合选型依据（如为何终端交互选 `Claude Code + DeepSeek` 而纯无头保底选 `DSH + DeepSeek`）。
3. **全栈非主控升级与三级耦合红线**：全面检查仓库运行与科研工作流依赖的客户端 CLI、路由脚本、CI 工作流、Python 运行时及 WSL2 环境；**绝不原地热更当前正在运行的活跃主控进程**，且对高耦合底层环境必须输出显式**依赖耦合警告**，严禁强行覆盖破坏现有编译扩展或求解器 ABI。
4. **整篇重构清淤（反叠补丁）**：每次更新文档与规则时，必须按单点真源（SSOT）通篇重写收敛，剔除过时补丁、重复叮嘱与非 CAE 管道噪音，保持仓库总复杂度收敛。

---

## 1. 二阶段自举状态锁（`scripts/fleet_state.py`）

状态锁存放于宿主机本地状态目录（Windows `%LOCALAPPDATA%\cae-harness\fleet-evolve\state.windows.json`；WSL2／Linux `${XDG_STATE_HOME:-~/.local/state}/cae-harness/fleet-evolve/state.posix.json`），零污染 Git 工作区：

```bash
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> status
```

- **`phase == "IDLE"`（默认 Pass 1）**：正常执行 Phase 1 $\rightarrow$ Phase 4；若 `stale_reason` 非空（`expired_ttl`／`repo_drift`／`side_mismatch`／`corrupt_state`），先 `clear` 归零再进入 Pass 1。
- **`phase == "AWAITING_MASTER_RELOAD"`（新主控重启后 Pass 2 重入）**：运行 `verify-pass2 --session-id <current_session_id>` 原子认领状态锁（防同会话死循环与并发重入），跳过重复外网检索，直接校验新主控并进入 Phase 3 真机联调与 Phase 4 门禁收口。

---

## 2. 标准四阶执行流水线

### Phase 1：零写入全员并发调研与工具链耦合诊断（Pass 1 ＆ `audit` 执行）

在改动任何文件前，并发调起原生只读子代理与 `cae-harness` 采集三路证据：
1. **双侧环境、资产漂移与工具链耦合体检**：
   - 运行 Windows 与 WSL 双侧 `install.py --probe-apps`、`install.py --audit-upgrade --with-cc-router`（读取 `host.toolchain` 中的 Python 次版本、全局编译型科学计算包列表与 `coupling_warnings`）及 `cae_harness.py probe --discover-models`／`assess`。
   - 检查非主控工具链可用更新：各非主控 Agent CLI（`claude`、`codex`、`dsh`、`cc-switch`）、`Python.Launcher`、Python 同次版本补丁、WSL2 内核（`wsl --status`）与 `.github/workflows/ci.yml` Action 版本。
   - 若检出网络不通、客户端未登录或 API Key 缺失，直接向用户列出**前置自理清单**，不编造绕过逻辑。
2. **外部官方机制与硬核实测核查**：按权重优先级采信——**本机历史真实翻车记录（收敛阶退化、量纲错配、压缩失忆、钩子误拦） > 官方 Release Notes / System Card 机制参数 > 长程工程实测 > 通用榜单**。
3. **整仓代码、结构与文档全量审计（`full` ＆ `hygiene` 必跑）**：并发派发只读子代理与异构无头引擎（如 `codex:sol`、`dsh:default`），逐文件排查逻辑矛盾、目录越界绕过、死代码与多文档重复堆叠。

### Phase 2：全栈升级分级裁决、正交重算与双矩阵原地维护

1. **全栈非主控工具链三级升级裁决（严守耦合安全边界）**：

   | 耦合等级 | 涵盖对象 | 执行准则 |
   | :--- | :--- | :--- |
   | **Tier 1：无耦合工具／CLI** | 非活跃主控的 `claude`、`codex`（独立安装包）、`dsh`、`cc-switch`、`Python.Launcher`（`py.exe`）、GitHub Actions | 经 `R4` 批准后直接原地升级至最新稳定版，并跑 `--version` 与无头探针验收；Windows 商店版 AppX 包或活跃主控自身仅提示更新路径，不强杀运行态进程。 |
   | **Tier 2：同次版本运行时／WSL 内核** | 全局 Python 同次版本安全补丁（如 `3.13.x -> 3.13.y`，`cp313` ABI 兼容）、`wsl --update` | **先查进程锁**：确认无活跃钩子／脚本占用 `python.exe`、WSL 内无正在运行的 MPI／CFD／FEA 求解任务后再升级；若存在活跃占用，向用户发出明确提示并给出空闲执行命令。 |
   | **Tier 3：重度 ABI／环境耦合升级** | 含 `jax`/`jaxlib`/`scipy`/`mpi4py`/`petsc4py`/`torch` 等编译扩展的全局 Python 跨次版本升级（如 `3.13 -> 3.14`）、WSL 跨 LTS 发行版升级（`do-release-upgrade`） | **强制警告拦截（绝不自动强升）**：明确告知用户跨版本原地覆盖将导致已编译科学计算轮子、求解器动态库与全局钩子解释器失效；推荐通过 `uv python install` 或项目独立 `venv` 并行安装新版本。 |

2. **代码重构与 `README.md` 第四章双矩阵原地更新**：
   - 更新 `install.py`、`cae_harness.py`、`hooks/*.py` 与 `router/` 中经主控 `R6` 核实的代码改进。
   - 在上游仓库 `README.md` 第四章原地重写更新**矩阵一（Agent/LLM 正交适配矩阵）**与**矩阵二（用户模糊指令、全栈升级与整仓重构精确调起矩阵）**，并按单点真源原则整篇收敛关联文档（底层钩子 JSON、代理协议与压测细节统一归口至 `docs/architecture-and-hooks.md`）。

### Phase 3：非主控实装、宿主机同步与真机 CLI 探针验收

1. **预演与授权落盘**：先运行 `install.py --dry-run --yes` 预演，获用户 `R4` 授权后执行 Tier 1（及无锁 Tier 2）工具升级与双侧宿主机 `install.py` 正式同步。
2. **真机无头往返探针（硬门禁）**：对每一条已就绪后端（如 `codex:sol`、Windows/WSL `cc:default`、`dsh:default`）发起真实 CLI 只读探针，必须同时拿到 **`returncode == 0`**、合法结构化回执以及双侧 `--audit-upgrade` **`all_aligned: true`**，严禁仅凭静态配置文件报完工。
3. **主控自举分支判定**：
   - **当前主控无需换壳／升级客户端（或已处于 Pass 2）**：运行 `fleet_state.py clear --verified`，直通 Phase 4。
   - **主控客户端或常驻模型需切换**：运行 `fleet_state.py record-pass1 --session-id <sid> --target-master "<Target>"`，输出简明切换步骤后暂停退出，待用户重启主控后重发指令完成 Pass 2 合龙。

### Phase 4：整仓清淤、沙箱门禁与待推送硬挂起（Explicit Push Gate）

无论是 `full`、Pass 2 还是 `hygiene` 模式，收口前必须全部跑通以下 7 项确定性门禁：
1. **独立审查与主控 `R6` 复核**：完成 `codex:sol (max)` 对改动文件的独立审查，主控逐行查对行号确认无死代码、无越界隐患、无未验证夸大表述（`R5`／`R3.6`）。
2. **六模式临时沙箱安装与零漂移体检**：在临时目录跑通 `codex`、`antigravity`、`claude --with-cc-router`、`dsh`、`both`、`all --with-cc-router` 六种模式，且沙箱 `--audit-upgrade --with-cc-router` 全端 `drift_files == []`。
3. **插件严格校验**：`agy plugin validate --strict template/user-guard` 零错误通过。
4. **全量离线单测**：`python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v` 100% 通过（严禁删改或弱化测试断言）。
5. **字节预算与单一版本真源**：`template/user-guard/rules/AGENTS.md` 与根 `AGENTS.md` 均 `< 16,000` 字节；版本号仅在 `template/user-guard/plugin.json` 与 `CHANGELOG.md` 出现，其余 `.md` 零硬编码版本号、零静态验证日期。
6. **链接与公开脱敏终审**：全仓 `.md` 相对链接 100% 指向仓内真实文件；全仓零个人用户名路径、零会话 UUID、零具体 CPU/GPU 型号、零私有 IP、零密钥残留。
7. **待推送硬挂起**：输出 `git status -sb`、`git diff --stat`、工具链升级与耦合警告清单及验收摘要，**严禁自动执行 `git push`**，等待用户明确推送指令。
