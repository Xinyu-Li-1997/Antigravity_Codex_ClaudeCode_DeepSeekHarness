---
name: fleet-evolve
description: >
  Upgrade non-active CLI toolchains with ABI coupling checks, recalculate client-model pairings,
  and refactor repository code and documentation in natural engineering Chinese across Antigravity,
  Codex, Claude Code (plus CC Switch), and DeepSeek Harness. Trigger with highest priority when the
  user asks to comprehensively upgrade, audit, refactor, or run full regression checks.
---

# 全套工具升级、模型优缺点重算与仓库清淤协议（`fleet-evolve`）

当用户提出全面更新、重算模型搭配或全仓清淤需求（如“全面更新整套配置”“全升级除主控外所有工具”“调研各家最新模型优缺点更新推荐表”或“全量检查优化仓库代码与文档”）时，最高优先级启用本技能。单一入口支持三种运行模式：

- **`full`（默认：调研评估 -> 用户拍板 -> 非主控升级与双侧同步 -> 真机与全仓回归）**
- **`audit`（仅只读体检：扫描双侧配置漂移、工具链 ABI 耦合风险、历史报错账本与外部模型优缺点，不改任何文件）**
- **`hygiene`（仓库清淤与文档三步重写：排查代码边界与死代码，执行 `--clean-room` 新手盲读、自然中文重写与逐行核验，跑通全量离线回归）**

---

## 一、软机制与硬机制协同设计

本技能通过“软契约 + 硬门禁”避免规则随模型换代反复膨胀：

| 层级 | 组成模块 | 承担职责 |
| :--- | :--- | :--- |
| **硬机制（代码与状态机强制约束）** | `fleet_profile.json`、`cae_harness.py`、`fleet_state.py`、`install.py`、五类生命周期钩子、`tests/` 离线回归集 | 集中存放唯一模型评估快照；强制执行角色路由、读写边界快照、配额冷却识别、用户拍板状态锁（`propose`／`approve`）、主控重入锁（`record-pass1`／`verify-pass2`）、项目外写拦截与原话压缩恢复。 |
| **软机制（工程认知与审阅规范）** | `rules/AGENTS.md`、`SKILL.md`、只读子代理规范、文档三步校核法 | 约束优缺点双向调研权重、致命短板一票否决准则、自然工程书面语重写、防无声放大（`R5`）及主控逐行复核（`R6`）。 |

---

## 二、核心评估准则与四个必停用户拍板节点

1. **优缺点双向调研，致命短板优先否决**：
   - 采集官方更新日志、社区长任务工程实测与本机跨项目历史对话日志时，**既找长板，更优先搜集致命短板**。
   - **长板决定角色上限**（如自然语言沟通是否直白说人话、长链条公式推导严密度、跨文件补丁一次命中率、生命周期钩子与 `--json-schema` 确定性、订阅配额缓存复用比）。
   - **致命短板决定一票否决或隔离边界**：凡存在过严封号风控、严苛 5 小时会话／7 天周度硬限流、第三方协议转换反代引发的工具调用 ID 错乱（`HTTP 422`）与流截断、开启 `ultra` 挡位触发子代理风暴与轮询超时、或低阈值上下文压缩导致早期指令丢失，必须如实记入 `fleet_profile.json`，该排除的直接排除（例如在 Anthropic 解除过严封号操作与硬限流前暂不纳入 Claude 原生模型），该加硬参数隔离的在代码中固化（如过滤 `ultra`、固定 `--permission-mode dontAsk`）。
2. **总控与各角色槽位均支持动态重算**：没有任何客户端天生绑定固定角色。总控（`orchestrator`）重点考察模型是否“最说人话”、长对话抗退化能力及官方订阅与按量 API 的成本差距；若评估发现更优组合，生成草案后请用户拍板切换。
3. **四个强制停机拍板节点（未获用户明确同意严禁跨步执行）**：
   - **节点 A（推荐搭配表拍板）**：凡新增或修改推荐的 `客户端 × 模型 × 推理挡位` 表（`fleet_profile.json` 与 `README.md` 表一），必须运行 `fleet_state.py propose` 进入 `AWAITING_USER_APPROVAL` 状态，列出各候选优缺点与变更依据，**停下等待用户拍板**。
   - **节点 B（宿主机全局目录写入与主控切换批准，`R4`）**：展示 `install.py --dry-run` 变更清单与数据库备份策略，获批后再写入 `~/.gemini`、`~/.codex`、`~/.claude`、`~/.dsh`。
   - **节点 C（高耦合工具链升级裁决）**：遇到装有编译型科学计算包的全局 Python 跨次版本升级、CUDA 跨大版本升级或 WSL 跨 LTS 发行版升级，必须发出明确依赖破坏警告并停下交由用户决定。
   - **节点 D（远端推送确认）**：全量测试通过后展示 `git status -sb` 与改动摘要，等用户下达明确指令后再执行 `git push`。

---

## 三、状态机子命令（`scripts/fleet_state.py`）

状态文件保存在宿主机本地状态目录（Windows 为 `%LOCALAPPDATA%\cae-harness\fleet-evolve\state.windows.json`，POSIX／WSL 为 `${XDG_STATE_HOME:-~/.local/state}/cae-harness/fleet-evolve/state.posix.json`），不污染 Git 工作区：

```bash
# 查看当前演进阶段（IDLE | AWAITING_USER_APPROVAL | AWAITING_MASTER_RELOAD）与代码/清单哈希
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> status

# 登记新版推荐搭配草案并挂起等待用户拍板（节点 A）
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> propose --session-id <sid> --summary "<草案摘要>"

# 用户明确同意推荐表后解锁
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> approve --session-id <sid>
```

---

## 四、标准四阶段执行流水线

### 第 1 阶段：只读并发采集与历史失误深挖（`IDLE` 首次进入或 `audit` 模式）

1. **双侧环境与耦合体检**：在 Windows 与 WSL 双侧并发运行 `install.py --probe-apps`、`install.py --audit-upgrade --with-cc-router`、`cae_harness.py probe --discover-models` 与 `cae_harness.py assess --caller auto`，检查客户端 CLI、`cc-switch`、`py.exe`、Python 次版本科学计算编译包耦合及 WSL 状态。
2. **本机历史对话与外部缺陷交叉比对**：统计本机历史会话与代理日志中的真实报错模式、缓存命中率、压缩频率与超时原因；检索官方更新说明与硬核工程排错反馈（优先检索翻车案例、限流风控变动、协议兼容缺陷与长上下文退化点；本机实测翻车记录权重高于外部宣传）。
3. **代码边界排查与 `--clean-room` 新手盲读**：并发调用只读子代理与限定文件范围的无头引擎排查死代码与越界漏洞；对核心文档在系统临时目录发起 `cae_harness.py task --kind review --clean-room` 空上下文盲读，挑出生造黑话、重复叮嘱与阅读断层。

### 第 2 阶段：仓库内修订、离线验证与呈交用户拍板

1. **修订仓库代码、评估清单与文档**：
   - 主控按 `R6` 核对精确行号后修改脚本与 `template/user-guard/skills/cae-harness/fleet_profile.json`；
   - 按“新手盲读反馈 -> 主控通篇重写自然工程中文 -> `readonly-auditor` 核对技术参数与脱敏”三步更新 `README.md`（第四章原地维护表一与表二）及 `docs/`。
2. **宿主机写入前先跑通全套离线检查**：
   - 六种安装模式临时沙箱部署与 `--audit-upgrade --with-cc-router` 零漂移校验；
   - `agy plugin validate --strict template/user-guard`；
   - `python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v` 全量离线单测通过；
   - 两份 `AGENTS.md < 16,000 B`、`plugin.json` 单一版本号、Markdown 相对链接有效性与全文件脱敏扫描通过。
3. **非主控工具三级升级评估与停机拍板**：
   - 第一级（无外部耦合的独立 CLI、`py.exe`、GitHub Actions）、第二级（同次版本 Python 补丁、`wsl --update`，须先查进程与算例占用）、第三级（跨次版本系统 Python、跨大版本 CUDA、跨 LTS WSL，强制警告拦截）分类列出；
   - 若涉及推荐搭配表变更，执行 `fleet_state.py propose`；向用户完整呈报【本机历史实测摘要 + 推荐客户端 × 模型 × 挡位表（含优缺点与排除项）+ 工具升级分级建议 + `install.py --dry-run` 写入预览】，**停下等待用户拍板**。

### 第 3 阶段：获批后升级非主控工具、双侧同步与真机往返验收

1. 用户拍板同意后，运行 `fleet_state.py approve`，升级获批的非主控工具（绝不强行关闭正在运行的主控客户端自身），并在 Windows 与 WSL 双侧执行 `install.py` 同步全局配置。
2. 对每个已就绪后端实际跑一次最小只读无头调用，同时验证 **`returncode == 0`**、结构化输出合规且双侧 `--audit-upgrade` 显示 **`summary.all_aligned == true`**。
3. 若主控客户端自身需切换或升级版本，运行 `fleet_state.py record-pass1 --session-id <sid> --target-master "<目标主控>"` 输出手动重启清单后暂停；重启后新会话运行 `verify-pass2` 接续收尾。

### 第 4 阶段：收尾核验与远端推送挂起

运行独立代码终审与主控行号复核，执行 `fleet_state.py clear --verified --readback-done`，展示 `git status -sb`、`git diff --stat` 与测试结果，**原地等待用户下达明确推送指令（节点 D）**。
