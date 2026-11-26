# 面向科学计算与工程研发的四客户端配置与验证工具

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.24293507-blue.svg)](https://doi.org/10.5281/zenodo.24293507)
[![CI](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/actions/workflows/ci.yml/badge.svg)](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness)](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/releases)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B_stdlib-3776AB.svg)](install.py)

本仓库为 **Google Antigravity**（`agy`）、**OpenAI Codex**（`codex`）、**Claude Code**（`claude`）与 **DeepSeek Harness**（`dsh`）提供共用的工程规范、会话生命周期钩子、只读审查代理与跨客户端验证脚本。适用领域包括 **CAE、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值求解器、高性能计算（HPC）及高可靠系统软件工程**。

> **独立开源提示**：**本项目为个人独立开源适配工具，与上述各厂商无官方关联或背书，全部内容按现状提供；使用前请查阅 [LEGAL.md](LEGAL.md)。**

核心设计思路是将研发中反复出现的易错环节固化为确定性钩子、离线核验脚本与自动化测试，而非不断拉长系统提示词。安装任意单个客户端即可独立使用；同时配置多个客户端时，可按任务分工跨客户端协同。核心脚本与校验仅依赖 Python 3.11+ 标准库，支持 Windows、WSL2 与 Linux。

快速跳转：[安装与上手](#一安装与快速上手) · [核心风险防护](#二核心工程风险防护) · [日常课题使用示例](#三日常课题使用方式与示例) · [配置仓库维护示例](#四配置仓库维护与常用选项) · [文档导航](#五文档分层导航与目录结构) · [致谢与许可](#六致谢english-overview-与开源许可)

---

## 一、安装与快速上手

> **执行前提示**：**安装与切换脚本会写入用户配置目录或本机数据库，按现状提供且无担保；执行前请先预览并备份，详见 [LEGAL.md](LEGAL.md)。**

1. **准备官方客户端**：安装上述至少一个官方客户端，并完成官方 OAuth 登录或配置厂商 API Key。本仓库不内置第三方反向代理，也不创建或修改账号凭据。
2. **同步至用户级配置目录**：克隆本仓库后，在仓库根目录运行安装脚本；Windows 使用 `python -B -X utf8`，WSL2 与 Linux 使用 `python3 -B -X utf8`：

   ```powershell
   python -B -X utf8 install.py --dry-run --yes   # 只读预览拟写入路径
   python -B -X utf8 install.py                   # 确认后同步至本机已安装客户端目录
   python -B -X utf8 install.py --audit-upgrade   # 核查配置漂移、Windows 与 WSL2 对齐状态及 ABI 风险
   ```

   安装脚本仅写入用户主目录下的客户端配置路径，不会向课题或业务仓库写入任何文件。若同时在 Windows 与 WSL2 环境中开发，需在两侧分别执行同步。
3. **进入课题目录直接使用**：在课题仓库启动客户端后即可按常规对话开展工作。局部修改由主会话直接完成；涉及独立模块的求解器实现、大型代码库审阅或跨客户端独立审查时，调度脚本先生成三句话确认摘要与本地明细链接，经用户确认后再执行，并在完成后自动生成离线机械对账表。

---

## 二、核心工程风险防护

针对科学计算与工程软件研发中的五类常见风险，本仓库通过生命周期钩子与离线核验脚本提供可复查的确定性约束：

| 工程风险 | 常见问题表现 | 本仓库的确定性机制 |
| --- | --- | --- |
| **长对话压缩后上下文丢失** | 自动摘要遗漏早期的法向约定、符号定义、数值容差或待审任务 | 压缩恢复钩子分块还原关键用户原话、有效子任务结论、待确认任务总览及对账链接 |
| **大型仓库超出上下文窗口或漏读** | 一次性读入整仓或长文档引发频繁压缩，跳行漏读关键约束 | 读前运行 `read-plan` 评估体积、切分长文件行号区间并分批规划；读毕运行 `cross-check` 核对阅读盲区与结论分歧 |
| **代码变更后漏改文档与测试** | 源码已修改但配套文档或测试滞后，人工总结遗漏关联文件 | 后台任务完成后自动生成离线机械对账表，也可随时运行 `reconcile`，基于文件基线与静态依赖列出待复核项 |
| **弱化验收标准或未实查即断言** | 放宽数值容差、以局部补测冒充全套通过、仅查局部路径便断言存量不足，或长文重写跳过审校 | 编码门禁限定写入范围并执行验收命令；回合结束钩子同步拦截失实的“全部通过”表述、未经充分排查的否定或全称缺口结论，以及未经独立复核的长文交付 |
| **只读审查误改源码、越界写入或后台挂死** | 只读审阅期间改动文件、写入项目外目录、反复轮询无变化的日志，或误调桌面 GUI 与未分页命令导致后台任务卡死 | 只读代理禁止修改文件或运行测试，并在任务前后比对文件快照；工具执行前静态拦截项目外未授权写入或删除、本地会话删除接口、外部 worktree 创建、无效日志轮询，以及无超时桌面 GUI 与未分页 Git 挂死调用 |

此外，离线校验命令 `check-grounding --strict --doc-hygiene` 面向 Markdown、LaTeX 与 reStructuredText 文档核查事实依据与结构完整度，检出无依据参数、断链图件、无证明定理空壳、计数失步、超长单句及跨文档重复内容。

---

## 三、日常课题使用方式与示例

日常研发按上下文污染度划分执行边界：**主会话负责需求澄清、局部修改、强耦合小步联动与原文行号复核；高吞吐的广度调研、数理推导、求解器模块实现与独立审查交由后台隔离进程执行，主会话仅回收摘要与落盘链接并复核原文，避免过早频繁压缩。**

![日常课题协作与配置仓库维护流程](docs/assets/user-and-evolution-flow.svg)

### 1. 日常课题对话示例

在科研或工程仓库中，可直接用中文提出任务目标或指定客户端，系统按如下方式分流处理：

| 使用场景 | 对话指令示例 | 系统执行方式与边界 |
| --- | --- | --- |
| **局部修改与高频对齐** | “修正边界条件里的符号并补一个单测” | 由主会话直接修改并验证，不触发后台派工流程 |
| **显式指定客户端或模型** | “用 Codex 高强度审查 `src/flux/` 的守恒实现” | 严格锁定指定客户端、模型或推理挡位；目标不可用时直接报错，不静默降级 |
| **大型代码库分批审阅与核对** | “先评估整个求解器目录的体积，分片让两端独立审阅并核对盲区” | 先运行 `read-plan` 评估上下文预算并划分阅读批次，经确认后执行，完成后通过 `cross-check` 生成覆盖矩阵与分歧清单 |
| **架构方案双端独立设计** | “请两个客户端分别独立给出并行重构方案，不要互相参考草稿” | 仅传入用户需求与必要背景，不附带主会话草稿，并行生成两份独立方案后再逐项对比 |
| **核心模块编码与机械对账** | “实现二维 Euler 方程 HLLC 通量并跑通守恒测试，结束后给对账表” | 经确认后通过 `gate` 限定可写文件白名单并执行验收命令，完成后自动输出 `reconcile` 机械对账表链接 |
| **环境排查与公开文档校核** | “核查直连对端共享目录是否可读；整理后检查文档事实与卫生” | 本机只读诊断保留完整输出以查明事实，对外发布内容再做脱敏处理；公开文档运行 `check-grounding --strict --doc-hygiene` 校验 |

动态角色映射与运行阈值集中存放于 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json)。未显式指定客户端时，系统按统筹、数理推导、编码实现、独立审查与快速初筛五类角色，从本机可用客户端中按配置匹配。

### 2. 四客户端接入方式

| 客户端 | 同步目录 | 生命周期防护方式 | 适配说明 |
| --- | --- | --- | --- |
| **Antigravity** (`agy`) | `~/.gemini/config/plugins/user-guard` | 原生插件挂载 4 类事件钩子 | 不修改用户自有的 `~/.gemini/GEMINI.md`；默认启用长会话分段交接 |
| **Codex** (`codex`) | `~/.codex` | `hooks.json` 挂载 5 类事件钩子 | 保留标准章节之后的自定义规则；支持四级隔离与净室运行模式 |
| **Claude Code** (`claude`) | `~/.claude` | `settings.json` 合并 5 类事件钩子 | 默认同步 CC Switch 配置切换脚本；可按需启用本地回环分流路由 |
| **DeepSeek Harness** (`dsh`) | `~/.dsh` | 当前以软规则运行，由安装器探测原生钩子协议 | 通过无头调度脚本调用时，同样提供目录隔离、行号内联与只读快照核验 |

---

## 四、配置仓库维护与常用选项

在常规课题仓库中，系统仅启用日常研发流程；在本配置仓库目录下，开放上游工具链维护流程，涵盖三类维护操作：

| 维护模式 | 对话指令示例 | 适用时机与执行内容 |
| --- | --- | --- |
| **全面更新**（`full`） | “核查各客户端和最新模型能力，给出五类角色推荐与启停方案” | 模型换代、客户端升级或订阅调整时，综合外部更新与本机探测生成评估提案 |
| **机制下沉与整理**（`hygiene`） | “保持当前选型不变，把反复出现的问题下沉为单测与钩子，并整理文档” | 修复脚本或钩子缺陷、精简常驻规则，并按四级文档归属修订与校核文档 |
| **只读巡检**（`audit`） | “只读检查配置漂移、Windows 与 WSL2 对齐状态和科学计算包 ABI 风险” | 日常配置与环境体检，仅输出诊断结果，不修改磁盘文件 |

上游维护过程中，调整推荐矩阵与运行阈值、写入宿主机全局配置目录、跨版本升级存在 ABI 强耦合的编译型科学计算依赖，以及执行 `git push` 四类操作，均须先经用户确认。

常用安装选项与保留边界如下：

- **控制同步范围**：`install.py` 默认同步本机已检出的客户端，也可通过 `--app antigravity`、`codex`、`claude`、`dsh`、`both` 或 `all` 指定目标。
- **保留个人规则**：同步 Codex、Claude Code 与 DSH 全局规则时，更新标准规则正文，自动保留 `## 工具与运行环境` 标准段之后的个人自定义内容，并将编号冲突条目迁移为 `L1.`、`L2.`。
- **配置切换与客户端停用**：使用 CC Switch 切换 Claude Code 供应商卡片时，建议通过配套的 `ccswitch_admin.py` 操作，或在图形界面切换后重新运行 `install.py`，以免覆盖已挂载的钩子配置。暂不启用的客户端可在 `fleet_profile.json` 中停用，调度时会自动跳过。

---

## 五、文档分层导航与目录结构

本仓库公开文档按读者角色分层维护，各主题均有唯一归属：

| 阅读目标 | 对应文档 |
| --- | --- |
| **日常研发：协作分工、大型仓库分片阅读、Euler HLLC 求解器闭环示例与 L1–L4 证据矩阵** | [日常工作流指南](docs/workflow-guide.md) |
| **原理与运维：模块架构、四阶段钩子细节、配置键、预算公式、本地路由、体检建议与精确卸载** | [架构设计与钩子说明](docs/architecture-and-hooks.md) |
| **脚本命令与完整参数规范** | [cae-harness 技能规程](template/user-guard/skills/cae-harness/SKILL.md) |
| **按需学术写作与改稿指南（论文、专著、科研报告与审稿回复）** | [cae-writing 写作规范](template/user-guard/skills/cae-writing/SKILL.md) |
| **上游维护流程：四维评估、审批门禁与多客户端及跨环境同步** | [fleet-evolve 维护规程](template/user-guard/skills/fleet-evolve/SKILL.md) · [仓库维护说明](AGENTS.md) |
| **插件目录索引、版本演进与时间戳凭证** | [插件模板索引](template/user-guard/README.md) · [更新日志](CHANGELOG.md) · [时间戳与 DOI 说明](.timestamps/README.md) |
| **法律合规、安全披露与社区贡献规范** | [法律与合规说明](LEGAL.md) · [安全政策](SECURITY.md) · [贡献指南](CONTRIBUTING.md) · [行为准则](CODE_OF_CONDUCT.md) |

```text
Antigravity_Codex_ClaudeCode_DeepSeekHarness/
├── README.md                              # 入口总览：安装上手、风险防护、使用示例与文档导航
├── AGENTS.md, CHANGELOG.md                # 上游配置仓库维护说明与版本更新日志
├── LICENSE, NOTICE, CITATION.cff          # Apache License 2.0、原创归属声明与学术引用元数据
├── LEGAL.md, SECURITY.md, CONTRIBUTING.md # 商标与免责边界、私密漏洞披露、DCO 贡献与行为准则（含 CODE_OF_CONDUCT.md）
├── .github/                               # 跨平台离线 CI 流水线、Issue 与 Pull Request 合规模板
├── .timestamps/                           # RFC 3161 时间戳凭证与 DOI 发版说明
├── install.py, install.ps1, install.sh    # 跨平台安装同步、配置体检、Windows 与 WSL2 对齐及 ABI 检查
├── docs/                                  # 日常工作流指南、架构与钩子说明及 3 张 SVG 流程图
├── template/
│   ├── user-guard/                        # 共用规则 (R0–R11)、钩子库、只读代理、动态配置与三项技能
│   ├── codex/                             # Codex 事件钩子与只读子代理模板
│   └── claude/                            # Claude Code 事件钩子、只读代理与本地路由及配置切换脚本
└── tests/                                 # 多平台安装、钩子守卫、调度器与文档规范离线测试套件
```

---

## 六、致谢、English Overview 与开源许可

### 1. 资助致谢

作者在**国家留学基金管理委员会**（简称**国家留学基金委**，**China Scholarship Council, CSC**）资助的留学期间完成本项目（学号 `202506280162`，批准文号 `留金选[2025]37号`）；截至本版本，未获得其他专项经费资助。

### 2. English Overview

`Antigravity_Codex_ClaudeCode_DeepSeekHarness` provides shared engineering rules (`R0–R11`), deterministic lifecycle hooks, read-only review subagents, and cross-client verification scripts. It supports **Google Antigravity** (`agy`), **OpenAI Codex** (`codex`), **Claude Code** (`claude`), and **DeepSeek Harness** (`dsh`) on Windows, WSL2, and Linux using only the Python 3.11+ standard library. Target domains include scientific computing, CAE, CFD, FEA, numerical PDE solvers, HPC, and high-reliability software engineering.

Rather than expanding system prompts indefinitely, the repository addresses recurring engineering failure modes through deterministic hooks, automated tests, and verification gates. Routine local edits remain in the main session, while self-contained derivations, solver modules, and independent reviews run in isolated workers with explicit confirmation and bounded write scopes. Offline tools—`read-plan`, `cross-check`, `reconcile`, and `check-grounding --doc-hygiene`—mitigate context overflow, review blind spots, stale documentation, and unverified claims.

Run `install.py` to sync configurations into user-level client directories without modifying project repositories (run on both Windows and WSL2 if developing across both). Users supply official client installations and sign-in/API keys. Developed by the author while studying under China Scholarship Council (CSC) funding (CSC No. `202506280162`, Approval No. `留金选[2025]37号`), with no other dedicated funding. Independent open-source project unaffiliated with any listed vendor; provided **"AS IS"** without warranty and not for unverified safety-critical use. Licensed under [Apache License 2.0](LICENSE) (`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`); see [NOTICE](NOTICE), [LEGAL.md](LEGAL.md), and [SECURITY.md](SECURITY.md).

### 3. 开源许可、商标声明与重要免责提示

本项目采用 [Apache License 2.0](LICENSE)（`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`）；再分发须遵守第 4 条并依第 4(d) 条保留 [NOTICE](NOTICE) 相关归属声明。

- **独立项目与商标**：**本项目为个人独立开源工具，与文中涉及厂商及开源项目均无官方关联或背书；相关名称与商标归各自权利人所有，详见 [LEGAL.md](LEGAL.md) 第一节。**
- **免责与安全关键警示**：**在法定最大范围内，本仓库按现状提供且无任何担保，维护者不承担直接或间接使用损失（法定不得免除的除外）。未经独立人工验证与法定审定，不应直接用于高风险安全关键生产决策，风险由用户自担。**
- **运行方式与合规边界**：**核心校验与钩子本地运行且无遥测；调度、路由与可选更新脚本按用户指令联网，传输与合规责任由用户自担。本仓库不提供代理绕过，写入前请先预览备份。** 详见 [LEGAL.md](LEGAL.md)、[SECURITY.md](SECURITY.md)、[CONTRIBUTING.md](CONTRIBUTING.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
