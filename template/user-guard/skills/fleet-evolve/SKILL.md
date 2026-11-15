---
name: fleet-evolve
description: >
  Upgrade non-active CLI toolchains with ABI coupling checks, recalculate client-model pairings,
  and refactor repository code and documentation in natural engineering Chinese across Antigravity,
  Codex, Claude Code (plus CC Switch), and DeepSeek Harness. Trigger with highest priority when the
  user asks to comprehensively upgrade, audit, refactor, or run full regression checks.
---

# 全套工具升级、模型搭配重算与仓库整理流程（`fleet-evolve`）

当用户提出全面更新或全仓整理需求（例如“全面更新整套配置”“全升级所有用到的东西，包括 Python 和 WSL”“按最新模型动态重算配置”或“全量检查优化仓库代码和文档可读性”）时，最高优先级启用本技能。本技能只维护一个入口目录，通过三种运行模式覆盖不同需求：

- **`full`（默认：全套升级 + 双侧同步 + 全仓回归测试）**：检查并升级除当前正在运行的主控客户端之外的所有相关 CLI 与辅助工具，重算客户端与模型搭配方案，同步 Windows 与 WSL 双侧全局配置，跑通真机 CLI 验证与全套测试。
- **`audit`（仅做只读体检与依赖冲突评估）**：只扫描当前安装对齐情况、外部工具版本更新及 Python / WSL 二进制依赖耦合风险，不改动任何文件。
- **`hygiene`（全仓代码审查、新手盲读反馈重写与回归测试）**：并发排查全仓代码的逻辑一致性、跨平台兼容性与安全边界，删除无用代码，按“无头纯净新手盲读 -> 主控重写自然文风 -> 独立核对技术准确度与脱敏”三步流程翻新文档，并跑完全套回归测试。

---

## 一、四条执行原则

1. **优先保证推导与代码质量，不靠降低推理强度省额度**：涉及公式推导、核心代码编写与审查的配置，默认启用各模型的最高单模型推理强度（`max`、`xhigh` 或 `high`），不开启自动降级路由，也不选会自动二次拆分任务的 `ultra` 挡。
2. **同时结合客户端机制与模型专长做搭配**：评估时既看客户端本身好不好用（工具调用稳定性、补丁准确率、生命周期钩子支持、沙箱与压缩策略），也看底层模型擅长什么（数理推导深度、长上下文准确度、国内或内网能否官方直连），讲清楚为什么这套客户端配这个模型最合适。
3. **升级除当前活跃主控外的全部相关工具，遇到强依赖耦合必须先警告**：检查范围覆盖四个客户端 CLI、CC Switch、本地路由脚本、GitHub Actions、Python 运行时与 WSL2 环境。**绝不强行关闭当前正在运行的主控客户端进程自身**；对于已经装有编译型科学计算包或求解器环境的系统级 Python、CUDA 与 WSL 发行版，必须先向用户发出明确的依赖冲突警告，严禁原地覆盖。
4. **文档按“新手盲读 -> 自然中文重写 -> 技术与脱敏核验”三步更新（不设死板字数上限）**：更新文档时严禁在原句上叠打括号补丁，也不靠硬压字数把表格写成两百字一行的机器日志。先让无头代理在无全局规则与钩子的 `--clean-room` 模式下按新手视角挑出晦涩用词和阅读断层，再由主控按 CAE 工程师习惯从头写顺逻辑与文字，最后只校核技术事实准确度与公开脱敏，不把自然表达改回生硬公文腔。

---

## 二、重启主控时的状态锁（`scripts/fleet_state.py`）

如果评估发现当前正在运行的主控客户端自己也需要升级版本或更换主力模型，需要提示用户手动重启主控后再继续完成收尾验证。为防止重启后又从第 1 阶段重头查一遍陷入死循环，状态锁保存在宿主机本地状态目录（Windows 位于 `%LOCALAPPDATA%\cae-harness\fleet-evolve\state.windows.json`，WSL2 / Linux 位于 `${XDG_STATE_HOME:-~/.local/state}/cae-harness/fleet-evolve/state.posix.json`），不会在 Git 仓库里留下临时文件：

```bash
python -B -X utf8 <skill_dir>/scripts/fleet_state.py --repo <repo_root> status
```

- **`phase == "IDLE"`（默认首次进入）**：按顺序执行下面的第 1 到第 4 阶段；若返回 `stale_reason` 非空（超时过期、仓库提交已变、运行系统侧不一致或状态文件损坏），先运行 `clear` 清空旧记录再开始。
- **`phase == "AWAITING_MASTER_RELOAD"`（重启主控后进入第二轮）**：运行 `verify-pass2 --session-id <当前会话唯一标识>` 认领状态锁（`<当前会话唯一标识>` 可传入当前对话 ID 或终端自定义唯一标记，用来阻止同一旧会话误重入或多个窗口同时执行），跳过第 1、第 2 阶段重复调研，直接验证新主控并完成第 3、第 4 阶段检查。

---

## 三、第 1～4 阶段操作步骤

### 第 1 阶段：只读并发检查与历史报错复盘（首次进入与 `audit` 模式执行）

在改动任何文件前，并发调用只读子代理与 `cae-harness` 收集三方面事实：
1. **双侧安装对齐与工具链耦合检查**：
   - 在 Windows 与 WSL 双侧运行 `install.py --probe-apps`、`install.py --audit-upgrade --with-cc-router`（读取 `host.toolchain` 中的 Python 次版本、全局已安装的编译型科学计算包列表与 `coupling_warnings`）、`cae_harness.py probe --discover-models` 以及 `cae_harness.py assess --caller auto`。
   - 检查非主控工具是否有新版本：非活跃主控客户端 CLI（`claude`、`codex`、`dsh`、`cc-switch`）、`Python.Launcher`、Python 同次版本补丁、WSL2 内核（`wsl --status`）与 `.github/workflows/ci.yml` 中的 Action 版本。
   - 若发现网络不通、客户端未登录或缺少 API Key，直接向用户列出需要自行补齐的前提清单。
2. **核对官方更新说明与本机真实报错记录**：当外部宣传与本机实测不一致时，优先采信本机历史真实报错记录（如收敛阶退化、量纲错配、压缩后丢指令、钩子误拦），其次参考各家官方更新日志与技术文档，再次参考长任务工程实测，不轻信通用娱乐跑分榜。
3. **全仓代码排查与 `--clean-room` 新手视角盲读（`full` 与 `hygiene` 模式必跑）**：
   - 并发派出只读子代理与无头引擎（如 `codex:sol`、`dsh:default`）排查代码逻辑漏洞、目录越界风险与无用死代码；
   - 将待审阅文档复制到系统临时目录（不含 `AGENTS.md`），通过 `cae_harness.py task --kind review --clean-room` 分别启动 `codex:sol`、`cc:default`、`dsh:default` 模拟不同背景的 CAE 新手读者（如刚进课题组的博士生、仅配单端 `DSH` 的直连用户、终端数值研发工程师），找出难懂黑话、跨文件重复和阅读顺序断层。

### 第 2 阶段：工具分级升级与代码文档重写

1. **非主控工具按依赖耦合程度分三级处理**：

   | 耦合等级 | 涉及组件 | 处理方式 |
   | :--- | :--- | :--- |
   | **第一级：无外部耦合的独立工具** | 非活跃主控客户端 CLI（`claude`、`codex` 独立安装包、`dsh`、`cc-switch`）、`Python.Launcher`（`py.exe`）、GitHub Actions | 获用户 `R4` 批准后直接更新到最新稳定版，并用 `--version` 和真实无头调用验证；Windows 商店版包或正在运行的主控客户端只给出手动更新命令，不强行终止当前进程。 |
   | **第二级：同次版本补丁与 WSL 内核** | 全局 Python 同次版本安全补丁（如 `3.13.x -> 3.13.y`，保持 `cp313` 扩展兼容）、`wsl --update` | **先检查进程占用**：确认没有活跃钩子正在运行 `python.exe`、WSL 内也没有正在计算的 MPI / CFD / FEA 算例后再更新；若当前正被占用，告知用户在关闭客户端后的空闲时间运行哪条命令。 |
   | **第三级：高耦合跨大版本升级** | 装有 `jax`、`jaxlib`、`scipy`、`mpi4py`、`petsc4py`、`torch` 等编译扩展且绑定全局钩子的系统 Python 跨次版本升级（如 `3.13 -> 3.14`）、CUDA 跨大版本升级、WSL 跨 LTS 发行版升级（`do-release-upgrade`） | **强制发出警告并停止自动升级**：向用户说明原地跨版本覆盖会导致已编译的科学计算扩展库、GPU 动态库与全局钩子失效；推荐通过 `uv python install` 或课题目录内独立 `venv` 并行安装新版本。 |

2. **代码更新与文档三步定稿**：
   - 主控按 `R6` 亲自核对原文件行号后，写入 `install.py`、`cae_harness.py`、`hooks/*.py` 与 `router/` 的代码修改。
   - 在 `README.md` 第四章原地更新**表一（客户端与模型搭配表）**和**表二（日常模糊说法与精确调用对照表）**，把客户端钩子 JSON 协议、拦截细节与实测数据集中放在 `docs/architecture-and-hooks.md`。
   - 结合第 1 阶段的新手盲读反馈，从头把文档写成通顺直白的工程师中文，再交由 `readonly-auditor` 对照源码逐项核对参数、路径、默认行为与安全限制是否准确一致（只修技术错漏，不改回生硬套话）。

### 第 3 阶段：同步双侧全局配置与真机往返验证

1. **空跑预览与正式同步**：先运行 `install.py --dry-run --yes` 预览拟写入路径，获用户 `R4` 批准后执行工具升级，并在 Windows 与 WSL 双侧运行 `install.py` 写入全局配置（按需附 `--purge-ccswitch-legacy` 在线备份并清空 CC Switch 已停用的旧提示词和旧技能记录）。
2. **真机 CLI 往返检查**：对每个已就绪后端（如 `codex:sol`、Windows / WSL `cc:default`、`dsh:default`）实际发起一次只读无头调用，同时确认**进程返回码为 `0`**、返回结构完整，且双侧 `--audit-upgrade` 显示 **`summary.all_aligned == true`**。
3. **判断主控客户端是否需要手动重启**：
   - **当前主控无需更换或升级，或当前已处于重启后的第二轮**：运行 `fleet_state.py clear --verified --readback-done`，直接进入第 4 阶段。
   - **当前主控客户端需要升级版本或切换主力模型**：运行 `fleet_state.py record-pass1 --session-id <当前会话唯一标识> --target-master "<目标主控配置>"`，输出简明手动操作步骤后暂停，等待用户重启主控后继续跑完第 4 阶段。

### 第 4 阶段：全仓回归检查与等待确认推送

无论是 `full`、第二轮恢复还是 `hygiene` 模式，交付前都必须跑通以下 7 项检查：
1. **独立代码审查与文档技术核验**：完成 `codex:sol (max)` 对代码改动的独立审查以及 `readonly-auditor` 对重写文档的技术事实核验，主控逐行复核确认无冗余死代码、无越界漏洞、无扩大结论范围或夸大量词问题（见 `rules/AGENTS.md` 中 `R5` 与 `R3.6`），且 `fleet_state.py status` 显示 `readback_verified == true`。
2. **六种模式临时沙箱安装与零差异检查**：在系统临时目录依次验证 `codex`、`antigravity`、`claude --with-cc-router`、`dsh`、`both`、`all --with-cc-router` 六种安装模式，且沙箱 `--audit-upgrade --with-cc-router` 各端 `drift_files == []`。
3. **插件清单严格校验**：`agy plugin validate --strict template/user-guard` 无报错通过。
4. **全套离线单元测试**：`python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v` 全部通过（严禁删改或放宽断言）。
5. **常驻规则体积与唯一版本号检查**：`template/user-guard/rules/AGENTS.md` 与根 `AGENTS.md` 均小于 `16,000` 字节（留足余量，低于 Antigravity 客户端 `24,000` 字节的规则截断上限）；版本号仅在 `template/user-guard/plugin.json` 与 `CHANGELOG.md` 中维护，其他 `.md` 文件不含硬编码版本号、静态日期戳或补丁式附注抬头。
6. **本地链接与全文件脱敏扫描**：所有 `.md` 相对链接指向仓库内真实存在的文件；全仓无个人用户名路径、无会话 UUID、无具体 CPU/GPU 型号、无私有内网 IP、无密钥凭据残留。
7. **等待用户确认后再推送**：列出 `git status -sb`、`git diff --stat`、工具升级与依赖警告清单以及测试结果，**绝不自动运行 `git push`**，等用户明确下达推送指令后再提交推送。
