---
name: fleet-evolve
description: >
  Upgrade non-active CLI toolchains with ABI coupling checks, recalculate client-model pairings,
  and refactor repository code and documentation in natural engineering Chinese across Antigravity,
  Codex, Claude Code (plus CC Switch), and DeepSeek Harness. Trigger with highest priority when the
  user asks to comprehensively upgrade, audit, refactor, or run full regression checks.
---

# 客户端工具升级、模型选型重算与仓库整理流程（`fleet-evolve`）

当用户提出全面更新、重新调研校准最新模型与配额推荐表，或全量整理优化仓库代码与图文文档时，优先启用本技能。支持三种运行模式：

- **`full`（默认完整流程：本机缓存与外部模型调研 -> 用户拍板 -> 非当前客户端升级与全局同步 -> 本机实际环境验证与全仓回归）**
- **`audit`（仅只读检查：核查 Windows 与 WSL2 环境下的配置差异、工具链二进制接口耦合风险、报错冷却记录与外部模型变动，不修改任何文件）**
- **`hygiene`（代码审查与图文文档重写：排查钩子与编排边界缺陷，按 `--clean-room` 空上下文盲读、平实工程中文分层重写与逐行技术脱敏核对三步更新文档，跑通全套离线与沙箱测试）**

---

## 一、代码约束与工程规则分工

为避免每次大模型换代或配额规则调整都要改动常驻规则与 Python 分支，系统划分为代码约束层与工程规则层：

| 层级 | 组成模块 | 职责范围 |
| :--- | :--- | :--- |
| **代码与配置约束层** | [fleet_profile.json](../cae-harness/fleet_profile.json)、`cae_harness.py`、`fleet_state.py`、`install.py`、五类生命周期钩子、`tests/` 离线测试集 | 集中维护角色候选、窗口预算与模型评估快照；校验读写白名单快照、配额冷却记录、推荐表审批状态锁（`propose`、`approve`）、主会话客户端切换重入锁（`record-pass1`、`verify-pass2`）、项目外写操作拦截，以及压缩后历轮原话与最近 `4` 次已完成子代理摘要的双路有界恢复。 |
| **工程规则与审阅流程层** | [AGENTS.md](../../rules/AGENTS.md)、技能说明、只读子代理配置、三步文档校核流程 | 约束优缺点双向实证评估标准、自然工程书面语与图文结构要求、无实测依据不臆测百分比（`R5`）及主会话亲自核对原文件行号（`R6`）。 |

---

## 二、选型校准原则与四个必停拍板节点

1. **严格基于代码与官方实证校准，同步评估优势与严重短板**：
   - 采集本机客户端探测结果（`probe --discover-models`）、运行期报错日志与官方文档时，每条关于窗口大小、出厂默认挡位、订阅滚动／周度配额池或 API 前缀缓存计费的陈述均须与本地缓存及源码公式逐一对齐，严禁编写未经核实的经验百分比或夸大形容词。
   - **实证优势决定角色分工**：重点核对中文自然语言表达清晰度、长链条公式推导严密性、跨文件补丁与定量验证收敛效率、生命周期钩子与原生 `--json-schema`／`--output-schema` 支持度，以及客户端订阅配额（如 5 小时滚动窗口与 7 天周期配额）与按量 API 前缀缓存的成本特征。
   - **严重短板作为排除或代码隔离依据**：凡存在平台区域访问限制、高频会话／周度配额上限、出厂默认推理挡位或上下文窗口偏保守、第三方 `Responses API` 协议转换反代引发 `HTTP 422` 工具调用 ID 错位与断流、开启 `ultra` 委派挡位派生多层子代理导致轮询超时，或较低上下文阈值触发自动压缩丢失早期约束及子代理结论等问题，均须如实记入 [fleet_profile.json](../cae-harness/fleet_profile.json)；不满足稳定运行条件的直接暂缓接入，可通过参数或钩子规避的在代码层固化隔离。
2. **主会话与后台角色槽位均支持动态重算切换**：主会话槽位（`orchestrator`）不与特定客户端永久绑定；若评估表明其他客户端与主力模型组合更合适，生成草案后交由用户确认切换。
3. **四个必停用户拍板节点**：严格执行 [AGENTS.md](../../rules/AGENTS.md) `R3.1.4` 规定的四类停机确认门禁（生成或调整推荐搭配表、向宿主机全局配置目录写入变更或切换主会话客户端、系统级 Python／CUDA／WSL 跨大版本 ABI 强耦合升级、以及远端 `git push`），获明确批准前严禁跨步执行。

---

## 三、状态锁命令（`scripts/fleet_state.py`）

审批与升级重入状态保存在宿主机本地状态目录（Windows 默认位于 `%LOCALAPPDATA%\cae-harness\fleet-evolve\state.windows.json`，WSL2 或 Linux 默认位于 `${XDG_STATE_HOME:-~/.local/state}/cae-harness/fleet-evolve/state.posix.json`），不写入 Git 工作区：

```bash
# 查看当前阶段（IDLE | AWAITING_USER_APPROVAL | AWAITING_MASTER_RELOAD）及核心文件 SHA-256
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> status

# 登记新版推荐搭配表草案并暂停等待用户拍板
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> propose --session-id <sid> --summary "<草案摘要>"

# 用户明确批准推荐表后解锁
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> approve --session-id <sid>
```

---

## 四、四阶段执行步骤

### 第 1 阶段：只读状态采集与盲读审查（`IDLE` 首次进入或 `audit` 模式）

1. **环境与依赖耦合检查**：在可用的 Windows 与 WSL2 环境中运行 `install.py --probe-apps`、`install.py --audit-upgrade --with-cc-router`、`cae_harness.py probe --discover-models` 与 `cae_harness.py assess --caller auto`，检查各客户端命令行、CC Switch（供应商卡片管理工具）、Python 启动器以及全局科学计算编译扩展包的二进制接口（ABI）耦合状态。
2. **运行日志与外部变更核对**：核查近期无头调用报错类型、配额限流报文格式、模型缓存默认参数与官方更新说明；涉及模型选型调整时，本机实际复现的故障记录优先级高于外部基准分数。
3. **代码边界审查与 `--clean-room` 空上下文盲读**：并发使用只读子代理与限定文件范围的后台只读任务检查代码边界漏洞与冗余死代码；重构核心文档前，先通过 `cae_harness.py task --kind review --clean-room` 在空上下文环境中从新读者视角挑出晦涩黑话、无依据断言、跨文件重复段落与阅读断层。

### 第 2 阶段：仓库内修订、全量离线测试与提请用户拍板

1. **更新仓库源码、评估配置与图文文档**：
   - 主会话按 `R6` 核对源码精确行号后完成修改，并在需要调整模型选型时更新 [fleet_profile.json](../cae-harness/fleet_profile.json)；
   - 按“空上下文盲读挑刺 -> 主会话重写层次清晰、图文并茂（配合 `docs/assets/*.svg` 纯 ASCII 路径矢量图、Mermaid 图与结构化矩阵）且紧扣仓库代码的工程中文 -> `readonly-auditor` 逐行核对技术事实与脱敏”三步更新 [README.md](../../../../README.md) 与 `docs/` 文档。
2. **写入宿主机前先在仓库与临时沙箱跑通全套离线测试**：
   - 在临时目录完整验证六种 `--app` 安装模式（`antigravity`、`codex`、`claude`、`dsh`、`both`、`all`）及 `install.py --audit-upgrade --with-cc-router` 零配置漂移；
   - 运行 `agy plugin validate --strict template/user-guard`（本机已安装 `agy` 时必跑）；
   - 运行 `python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v` 确保全部离线单元测试通过；
   - 核验两份 `AGENTS.md < 16,000 B`、`plugin.json` 唯一版本号、Markdown 与 SVG 本地相对链接有效性、UTF-8 无 BOM + `LF` 编码规范（`install.ps1` 保持 UTF-8 BOM + `CRLF`）及全文件脱敏。
3. **非主控工具三级升级评估与停机确认**：
   - **第一级（独立工具）**：无外部编译依赖的非当前客户端 CLI、`py.exe` 启动器、GitHub Actions 版本；
   - **第二级（同系列补丁）**：同次版本 Python 小补丁、`wsl --update`，须先确认无正在运行的求解算例或后台任务；
   - **第三级（强耦合跨大版本）**：装有 `jax`、`scipy`、`mpi4py` 等编译扩展的系统级 Python 跨次版本升级、CUDA 跨大版本升级、WSL 跨 LTS 发行版升级，必须发出明确依赖破坏警告并交由用户裁决。
   - 若修改了推荐搭配表，执行 `fleet_state.py propose`；汇总变更说明、升级分级建议与 `install.py --dry-run` 宿主机写入预览清单呈交用户确认，原地暂停。

### 第 3 阶段：获批后执行升级、全局同步与本机验证

1. 获用户明确批准后，若存在待审草案则先运行 `fleet_state.py approve`，完成获批的非主控工具升级（不强行终止当前正在运行的主会话客户端进程），并在本机目标环境执行 `install.py` 同步全局配置。
2. 验证已就绪客户端状态与无头调用连通性，确认 `install.py --audit-upgrade` 返回 `summary.all_aligned == true`。
3. 若主会话客户端自身需升级或切换，运行 `fleet_state.py record-pass1 --session-id <sid> --target-master "<目标客户端>"` 给出简明手动重启步骤后暂停；重启后在新会话中运行 `verify-pass2` 完成复核。

### 第 4 阶段：终审登记与等待推送指令

运行独立只读代码复核并由主会话核对行号，执行 `fleet_state.py clear --verified --readback-done`，输出 `git status -sb`、`git diff --stat` 与测试结果摘要，未获用户明确推送授权前不自动执行 `git push`。
