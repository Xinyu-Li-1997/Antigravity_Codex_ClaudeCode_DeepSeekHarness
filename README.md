# 面向科学计算与工程研发的四客户端配置与验证工具

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.24293507-blue.svg)](https://doi.org/10.5281/zenodo.24293507)
[![CI](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/actions/workflows/ci.yml/badge.svg)](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness)](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/releases)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B_stdlib-3776AB.svg)](install.py)

本仓库为 **Google Antigravity**（`agy`）、**OpenAI Codex**（`codex`）、**Claude Code**（`claude`）与 **DeepSeek Harness**（`dsh`）提供共用的工程规范、会话生命周期钩子、只读审查代理、跨客户端离线核验脚本与按需公理审查技能。适用领域包括 **CAE、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值求解器、高性能计算（HPC）及高可靠系统软件工程**。

> **独立开源提示**：**本项目为个人独立开源适配工具，与上述各厂商及第三方工具链无官方关联或背书，全部内容按现状提供；使用前请查阅 [LEGAL.md](LEGAL.md)。**

设计上将研发中反复出现的易错环节固化为确定性钩子、离线核验脚本与自动化测试，而不是无限拉长系统提示词；学术写作与公理审查作为独立按需技能，不增加常驻钩子，也不干扰日常编码。安装任意单个客户端即可独立使用；同时配置多个客户端时，可按专长分工协同。核心脚本仅依赖 Python 3.11+ 标准库，支持 Windows、WSL2 与 Linux（可选调用本机 Lean 4 编译器做纯核内核验签，不依赖 Mathlib）。

快速跳转：[安装与上手](#一安装与快速上手) · [核心风险防护](#二核心工程风险防护) · [日常使用与按需技能](#三日常课题使用方式与按需技能) · [配置仓库维护](#四配置仓库维护与常用选项) · [文档导航](#五文档分层导航与目录结构) · [致谢与许可](#六致谢english-overview-与开源许可)

---

## 一、安装与快速上手

> **执行前提示**：**安装与切换脚本会写入用户配置目录或本机数据库，按现状提供且无担保；执行前请先预览并备份，详见 [LEGAL.md](LEGAL.md)。**

1. **准备官方客户端**：安装至少一个上述官方客户端并完成官方登录或配置 API Key。本仓库不内置第三方反向代理，也不修改账号凭据；如需启用纯核 Lean 4 内核验签，可另行安装本机 Lean 4 工具链。
2. **同步至用户级配置目录**：克隆仓库后在根目录执行同步脚本（Windows 用 `python -B -X utf8`，WSL2 与 Linux 用 `python3 -B -X utf8`）：

   ```powershell
   python -B -X utf8 install.py --dry-run --yes   # 只读预览拟写入路径
   python -B -X utf8 install.py                   # 确认后同步至本机已安装客户端目录
   python -B -X utf8 install.py --audit-upgrade   # 核查配置漂移、双端对齐与 ABI 风险
   ```

   安装脚本仅写入用户主目录下的客户端配置路径，不改动任何业务仓库。同时在 Windows 与 WSL2 开发时，须在两侧分别同步。
3. **进入课题目录直接使用**：在任意课题仓库启动客户端即可常规对话。局部修改留在主会话直接闭环；独立求解器实现、大型代码库审阅或跨端独立审查先生成三句话确认摘要与明细链接，获批后执行并自动生成离线机械对账表。

---

## 二、核心工程风险防护

针对科学计算与工程研发中反复出现的五类风险，本仓库通过会话钩子与离线工具提供可复查的确定性约束：

| 工程风险 | 常见问题表现 | 本仓库的确定性机制 |
| --- | --- | --- |
| **长对话压缩后遗忘前置约定** | 自动摘要丢失早期的法向定义、数值容差、符号约定或待批任务 | 压缩恢复钩子分块还原历轮用户原话、实质子任务结论、未决票据总览与对账链接 |
| **大型仓库超窗或跳行漏读** | 一次性读入整仓引发频繁压缩，遗漏关键边界条件或约束 | 读前用 `read-plan` 评估体积并切分连续行段与保守波次；读毕用 `cross-check` 查盲区与分歧 |
| **改码后漏改配套文档与测试** | 源码已变但说明与单测滞后，人工总结遗漏依赖项 | 重负载完工自动产出离线 `reconcile` 机械对账表，基于文件基线与静态依赖列出待复核项 |
| **弱化验收标准或未实查即断言** | 放宽容差、局部补测冒充全过、仅查局部路径便断言全仓缺口，或长文免审交付 | `gate` 锁写入白名单并跑通验收命令；回合结束钩子对称拦截虚假全过、无探测否定、局部全称缺口与长文免审 |
| **审查误改源码、越界写或后台挂死** | 只读审阅改动文件、误写项目外目录、死等日志轮询，或裸调 GUI 与未分页 Git 挂死 | 只读代理前后核验快照；工具执行前静态拦截越界写删、本地会话删除接口、外部 worktree、空转轮询、无超时 GUI 与未分页 Git |

此外，`check-grounding --strict --doc-hygiene` 面向 Markdown、LaTeX 与 reStructuredText 文档核查代码依据、本地图件链接、定理证明完整度、计数一致、长句与跨文档复读。

---

## 三、日常课题使用方式与按需技能

日常研发按**上下文污染度**划分边界：主会话负责需求澄清、局部修改与原文行号复核；大体积通读、数理推导、成块实现与跨端独立审查交由隔离后台进程执行，仅回收摘要与落盘链接，降低过早频繁压缩。

![日常课题协作与配置仓库维护流程](docs/assets/user-and-evolution-flow.svg)

### 1. 日常课题对话示例

| 使用场景 | 对话指令示例 | 执行方式与确定性边界 |
| --- | --- | --- |
| **局部修改与高频对齐** | “修正边界条件符号并补一个单测” | 主会话直接闭环，不触发后台派工确认 |
| **显式指定客户端或模型** | “用 Codex 高强度审查 `src/flux/` 守恒实现” | 锁定指定客户端、模型或挡位；目标不可用直接报错，不静默降级 |
| **大型代码库分批审阅** | “先评估整个求解器体积，分片让两端独立审阅并查盲区” | `read-plan` 分片编排，获批后分波次只读执行，`cross-check` 产出覆盖矩阵与分歧表 |
| **双端无草稿独立设计** | “请两家客户端分别独立给并行重构方案，不看主会话草稿” | 仅传入需求与背景，经空上下文或并行批次独立生成方案后对比取舍 |
| **白名单编码与机械对账** | “实现二维 Euler HLLC 通量并跑通守恒单测，附对账表” | 获批后通过 `gate` 锁写入范围与验收命令，完工自动输出 `reconcile` 对账链接 |
| **点名公理审查与学术润色** | “按 `cae-axioms` 审查接口相容性并净室复演”或“按期刊规范润色章节” | 仅在用户点名时按需加载对应技能，零常驻钩子，严守外部仓库隔离与审稿保密边界 |

动态角色映射与运行阈值集中存放于 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json)，未指定客户端时按统筹、数理、编码、审查与快检五类角色从可用端自动匹配。

### 2. 四客户端接入方式

| 客户端 | 用户级同步目录 | 生命周期防护方式 | 协议适配要点 |
| --- | --- | --- | --- |
| **Antigravity** (`agy`) | `~/.gemini/config/plugins/user-guard` | 插件挂载 4 类事件钩子 | 不改动 `~/.gemini/GEMINI.md`；默认启用长会话分段交接 |
| **Codex** (`codex`) | `~/.codex` | `hooks.json` 挂载 5 类事件钩子 | 保留标准段后个人规则；完整支持四级正交隔离与净室模式 |
| **Claude Code** (`claude`) | `~/.claude` | `settings.json` 合并 5 类事件钩子 | 默认同步 CC Switch 切卡脚本；按需启用本地回环分流路由 |
| **DeepSeek Harness** (`dsh`) | `~/.dsh` | 软规则运行，安装器自动探测原生钩子协议 | 无头调度同样提供隔离目录、行号内联与只读快照核验 |

### 3. 按需公理审查架构（`cae-axioms`）

需要严格拆解前提冲突、核查多模块接口相容性或审查连续介质建模公理时，可点名启用 [cae-axioms](template/user-guard/skills/cae-axioms/SKILL.md)。日常编码不会触发该技能，也不增加常驻钩子；它支持通用工程与科学计算双档检查，并将常规离线推进结果与独立净室复演终态严格区分。各层经典文献溯源与第三方边界详见 [分层文献与边界声明](template/user-guard/skills/cae-axioms/references/layer-citations.md)。

![按需公理审查与硬状态机架构](docs/assets/axiom-machine-architecture.svg)

---

## 四、配置仓库维护与常用选项

在常规课题仓库仅启用日常研发流程；在本配置仓库目录下开放上游工具链维护，提供三类模式：

| 维护模式 | 对话指令示例 | 适用时机与执行内容 |
| --- | --- | --- |
| **全面更新**（`full`） | “核查各客户端与最新模型能力，给五类角色推荐与启停方案” | 模型换代、客户端升级或订阅调整时，综合外部更新与本机探测生成提案 |
| **机制整理**（`hygiene`） | “保持选型不变，把反复暴露的问题下沉为单测与钩子并整理文档” | 修复脚本或钩子缺陷、精简常驻文字规则，并按四级受众整篇校核文档 |
| **只读巡检**（`audit`） | “只读检查配置漂移、Windows 与 WSL2 对齐和科学包 ABI 风险” | 日常体检，仅输出结构化诊断结果，零磁盘写入 |

调整推荐矩阵与运行阈值、写入宿主机全局目录、跨版本升级编译型科学计算包 ABI 以及执行 `git push` 四类操作，均须先获用户确认。常用安装要点如下：

- **控制同步范围**：`install.py` 默认同步已检出客户端，也可通过 `--app antigravity`、`codex`、`claude`、`dsh`、`both` 或 `all` 指定目标。
- **保留个人规则**：同步 Codex、Claude Code 与 DSH 全局规则时，仅更新至 `## 工具与运行环境` 标准段，自动保留其后个人自定义规则并将冲突编号迁为 `L1.`、`L2.`。
- **切卡与停用**：通过 CC Switch 切换 Claude Code 卡片时，建议使用配套 `ccswitch_admin.py`，或在图形界面切换后重跑 `install.py` 以免覆盖钩子。暂不启用的客户端可在 `fleet_profile.json` 停用。

---

## 五、文档分层导航与目录结构

公开文档按读者角色分四级单点维护，各主题均有唯一归属：

| 阅读目标 | 对应文档 |
| --- | --- |
| **日常研发：协作分工、分片阅读、Euler HLLC 闭环、L1–L4 证据矩阵与按需技能配合** | [日常工作流指南](docs/workflow-guide.md) |
| **原理与运维：模块分工、四阶段钩子、预算公式、公理引擎架构、体检建议与精确卸载** | [架构设计与钩子说明](docs/architecture-and-hooks.md) |
| **调度脚本与确认票据参数契约** | [cae-harness 技能规程](template/user-guard/skills/cae-harness/SKILL.md) |
| **按需学术写作与改稿规范（期刊论文、专著、报告与审稿回复）** | [cae-writing 写作规范](template/user-guard/skills/cae-writing/SKILL.md) |
| **按需公理审查、双档硬状态机、纯核 Lean 4 与经典文献溯源** | [cae-axioms 公理与状态机](template/user-guard/skills/cae-axioms/SKILL.md) · [分层文献溯源](template/user-guard/skills/cae-axioms/references/layer-citations.md) |
| **上游维护流程与审批门禁** | [fleet-evolve 维护规程](template/user-guard/skills/fleet-evolve/SKILL.md) · [仓库维护说明](AGENTS.md) |
| **插件目录索引、更新日志与时间戳说明** | [插件模板索引](template/user-guard/README.md) · [更新日志](CHANGELOG.md) · [时间戳与 DOI 说明](.timestamps/README.md) |
| **法律合规、安全披露、贡献与行为准则** | [法律与合规说明](LEGAL.md) · [安全政策](SECURITY.md) · [贡献指南](CONTRIBUTING.md) · [行为准则](CODE_OF_CONDUCT.md) |

```text
Antigravity_Codex_ClaudeCode_DeepSeekHarness/
├── README.md                              # T0 入口总览：安装上手、风险防护、日常示例与文档导航
├── AGENTS.md, CHANGELOG.md                # 上游配置仓库维护说明与版本更新日志
├── LICENSE, NOTICE, CITATION.cff          # Apache License 2.0、原创归属声明与学术引用元数据
├── LEGAL.md, SECURITY.md, CONTRIBUTING.md # 商标免责边界、漏洞披露、DCO 贡献与行为准则（含 CODE_OF_CONDUCT.md）
├── .github/                               # 跨平台离线 CI 流水线、Issue 与 Pull Request 合规模板
├── .timestamps/                           # RFC 3161 时间戳凭证与 DOI 发版说明
├── install.py, install.ps1, install.sh    # 跨平台同步、配置体检、Windows 与 WSL2 对齐及 ABI 检查
├── docs/                                  # 日常工作流指南、架构与钩子说明及 4 张 SVG 架构与流程图
├── template/
│   ├── user-guard/                        # 共用规则 (R0–R11)、钩子库、只读代理、动态配置与四项托管技能
│   ├── codex/                             # Codex 事件钩子与只读子代理模板
│   └── claude/                            # Claude Code 事件钩子、只读代理、本地回环路由与切卡脚本
└── tests/                                 # 多平台安装、钩子、调度器、公理状态机与文档卫生离线回归套件
```

---

## 六、致谢、English Overview 与开源许可

### 1. 资助致谢

作者在**国家留学基金管理委员会**（简称**国家留学基金委**，**China Scholarship Council, CSC**）资助的留学期间完成本项目（学号 `202506280162`，批准文号 `留金选[2025]37号`）；截至本版本，未获得其他专项经费资助。

### 2. English Overview

`Antigravity_Codex_ClaudeCode_DeepSeekHarness` provides shared engineering rules (R0–R11), deterministic lifecycle hooks, read-only review subagents, cross-client verification scripts, on-demand academic writing guides (`cae-writing`), and an on-demand four-layer acyclic axiom verification machine (`cae-axioms`) for **Google Antigravity**, **OpenAI Codex**, **Claude Code**, and **DeepSeek Harness** on Windows, WSL2, and Linux. Core tools run solely on the Python 3.11+ standard library, with optional pure-core Lean 4 kernel checking (zero Mathlib) for `cae-axioms`.

Instead of endlessly growing system prompts, recurring failure modes in scientific computing, CAE, CFD, FEA, numerical PDE solvers, HPC, and high-reliability software engineering are guarded mechanically. Offline tools—`read-plan`, `cross-check`, `reconcile`, and `check-grounding --strict --doc-hygiene`—control context overflow, review blind spots, stale docs, and unverified claims. On-demand `cae-axioms` separates offline structure checks from clean-room verified verdicts (`VERIFIED_NO_T` and `VERIFIED_T_ASSERTED`) without adding permanent hooks to daily coding.

Run `install.py` to sync configurations into user-level directories without touching project repos (sync both Windows and WSL2 when using both). Developed by the author while studying under China Scholarship Council (CSC) funding (CSC No. `202506280162`, Approval No. `留金选[2025]37号`), with no other dedicated funding. Independent open-source project unaffiliated with any listed vendor or external toolchain; provided **"AS IS"** without warranty and not for unverified safety-critical use. Licensed under [Apache License 2.0](LICENSE) (`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`); see [NOTICE](NOTICE), [LEGAL.md](LEGAL.md), and [SECURITY.md](SECURITY.md).

### 3. 开源许可、商标声明与重要免责提示

本项目采用 [Apache License 2.0](LICENSE)（`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`）；再分发须遵守第 4 条并依第 4(d) 条保留 [NOTICE](NOTICE) 归属声明。

- **独立项目与商标**：**本项目为个人独立开源工具，与文中涉及厂商、工具链及外部开源项目均无官方关联或背书，亦未内嵌任何第三方源码；相关名称与商标归各自权利人所有，详见 [LEGAL.md](LEGAL.md) 第一节与第五节。**
- **免责与安全关键警示**：**在法定最大范围内，本仓库按现状提供且无任何担保，维护者不承担直接或间接使用损失（法定不得免除的除外）。未经独立人工验证与法定审定，不应直接用于高风险安全关键生产决策，风险由用户自担。**
- **运行方式与合规边界**：**核心校验、状态机与钩子本地运行且无遥测；调度、路由与可选脚本按用户指令联网或调用本机编译器，合规责任由用户自担。本仓库不提供代理绕过，写入前请先预览备份。** 详见 [LEGAL.md](LEGAL.md)、[SECURITY.md](SECURITY.md)、[CONTRIBUTING.md](CONTRIBUTING.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
