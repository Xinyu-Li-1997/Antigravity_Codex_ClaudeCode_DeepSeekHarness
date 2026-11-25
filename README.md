# 面向科学计算与工程研发的四客户端配置与验证工具

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.24293507-blue.svg)](https://doi.org/10.5281/zenodo.24293507)
[![CI](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/actions/workflows/ci.yml/badge.svg)](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness)](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/releases)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B_stdlib-3776AB.svg)](install.py)

本仓库为 **Google Antigravity**（`agy`）、**OpenAI Codex**（`codex`）、**Claude Code**（`claude`）与 **DeepSeek Harness**（`dsh`）提供一套共用的工程规则、确定性生命周期钩子、只读审查代理与跨端验证脚本。目标读者是使用 AI 助手开发 **CAE、计算流体力学（CFD）、有限元分析（FEA）、偏微分方程（PDE）数值求解器、高性能计算（HPC）及高可靠系统软件** 的科研人员与工程师。

核心思路很直接：**不同模型各有偏科，长对话也迟早会压缩忘事，单靠写更长的提示词祈祷模型自觉并不可靠。** 本仓库把反复出现的工程事故下沉为确定性钩子、机械对账脚本和单元测试，让常驻规则尽量保持简短。你只需安装任意一个客户端就能独立使用，同时装多个时则可按专长跨端协作。全仓仅依赖 Python 3.11+ 标准库，支持 Windows、WSL2 与 Linux。

快速跳转：[三步上手](#一三步快速上手) · [五类风险兜底](#二它帮你兜住哪五类研发事故) · [日常协同与四端支持](#三日常协作方式与四端支持) · [安装选项](#四常用安装选项与边界说明) · [文档导航](#五文档导航与目录结构) · [致谢与许可](#六致谢english-overview-与开源许可)

---

## 一、三步快速上手

1. **准备官方客户端**：先自行安装上述至少一个客户端并完成官方登录（官方 OAuth 或厂商按量 API Key）。本仓库不内置任何第三方反代，也不创建或修改账号凭据。
2. **运行一次全局安装**：克隆本仓库后，在仓库根目录执行安装脚本（Windows 用 `python -B -X utf8`，WSL2 与 Linux 改用 `python3 -B -X utf8`）：

   ```powershell
   python -B -X utf8 install.py --dry-run --yes   # 先只读预览将写入哪些路径
   python -B -X utf8 install.py                   # 确认后同步到本机已装客户端目录
   python -B -X utf8 install.py --audit-upgrade   # 核查配置对齐、双端状态与 ABI 风险
   ```

   安装器只写入用户主目录下的客户端配置目录，**不会往你的课题或业务仓库塞任何配置文件**。同时在 Windows 和 WSL2 开发时，请在两侧各同步一次。
3. **打开自己的课题仓库直接对话**：平时不需要背命令。几十行小修在主窗口直接改完闭环；涉及核心求解器实现、大仓库审阅或跨端复核时，系统会先给出 3 句话确认提示与可点击明细文件链接，等你确认后再执行，完工后自动产出离线机械对账表。

---

## 二、它帮你兜住哪五类研发事故

下面这五类问题是科学计算与工程开发中裸用 AI 最容易踩的坑，本仓库把它们全部转成了可核查的机械门禁：

| 常见研发痛点 | 裸用客户端时容易出什么事 | 本仓库怎么兜底 |
| --- | --- | --- |
| **长对话压缩后失忆** | 自动压缩丢掉早期的法向约定、符号定义、数值容差或未批任务 | 压缩后自动分块补回历轮关键原话、重要子任务结论、未决任务总览与对账指针，不丢核心约束 |
| **读大仓库撑爆上下文或漏读** | 一次喂入整仓或超长文档触发频繁压缩、跳行遗漏关键条款 | 读前运行 `read-plan` 评估体积、切分超大单文件行段并排出保守波次；多端读完后用 `cross-check` 核对覆盖盲区与分歧 |
| **改完代码忘了同步文档与测试** | 源码改了但配套文档或测试停在旧版，总结阶段报喜不报忧 | 派单完工自动生成离线机械对账表（也可随时运行 `reconcile`），基于真实改动和代码依赖标出未更新文件，不靠 AI 主观回忆 |
| **放宽验收底线或偷懒下结论** | 擅自放宽容差、拿局部补测冒充全套通过，或探测命令报错却直接断言“未连接/查不到” | 编码门禁强制锁死写入白名单并跑通验收命令；回合结束钩子对称拦截矛盾全过宣称与零实查否定结论 |
| **只读审查顺手改代码或越界写删** | 让模型查源码却顺手改了文件、写到项目外目录，或反复空转读未变日志 | 只读代理禁写禁测并在任务前后比对文件快照；工具执行前静态拦截项目外未授权写删、外部 worktree 与空转轮询 |

此外，本仓库的文档校验脚本 `check-grounding --strict --doc-hygiene` 会自动扫描源码与配置，拦截文档里凭空编造的参数、计数矛盾、补丁长句与跨文档复制粘贴，帮助课题仓保持文档真实清爽。

---

## 三、日常协作方式与四端支持

日常研发坚持一条朴素的分工原则：**主会话负责理解物理与工程目标、做小修改、定接口并亲自核对原文行号；接口清晰的成块推导、求解器模块实现与独立审查交由后台子进程隔离执行，只回收结构化结论。**

![日常课题协作与配置仓库维护流程](docs/assets/user-and-evolution-flow.svg)

- **想用哪一端直接说**：在对话里直接点名（例如“用 Codex 高强度审查通量模块”或“用两个客户端各自盲出方案对比”），调度脚本会严格锁定你的要求，目标不可用时直接报错，绝不静默降级。
- **不点名时按专长分工**：系统按统筹、数理推导、编码实现、独立审查和快速初筛五类角色从本机可用端中自动分配；所有动态角色映射与阈值集中在 [fleet_profile.json](template/user-guard/skills/cae-harness/fleet_profile.json)，不把时期性模型名写死在代码或文档里。
- **重负载先确认、完工有对账**：多路并发或可写任务启动前，完整方案落盘到本地明细文件，聊天框只发 3 句话简报和链接；完工后三端钩子统一核对成败结论、Token 用量与机械对账表链接。

四个客户端接入同一套共有机制的方式如下：

| 客户端 | 同步目录 | 生命周期防护方式 | 说明 |
| --- | --- | --- | --- |
| **Antigravity** (`agy`) | `~/.gemini/config/plugins/user-guard` | 原生插件挂载 4 类事件钩子 | 不改动你自有的 `~/.gemini/GEMINI.md`；默认开启长会话分段交接 |
| **Codex** (`codex`) | `~/.codex` | `hooks.json` 挂载 5 类事件钩子 | 保留你写在标准段之后的自定义规则；完整支持四级只读与净室隔离 |
| **Claude Code** (`claude`) | `~/.claude` | `settings.json` 合并 5 类事件钩子 | 默认同步 CC Switch 安全切卡脚本，按需可选本地回环分流路由 |
| **DeepSeek Harness** (`dsh`) | `~/.dsh` | 当前以软规则运行并自动探测钩子协议 | 通过无头调度脚本执行时同样享有范围隔离、行号内联与只读快照核验 |

---

## 四、常用安装选项与边界说明

- **指定安装范围**：`install.py` 默认自动同步本机已安装的客户端；也可传 `--app antigravity`、`codex`、`claude`、`dsh`、`both` 或 `all` 精确控制。
- **保留你的自定义规则**：同步 Codex、Claude Code 与 DSH 的全局规则时，安装器只更新标准规则至 `## 工具与运行环境`，自动保留你写在后面的个人章节，并将编号冲突条目平滑迁移为 `L1.`、`L2.`。
- **配置变更与凭据边界**：如果使用 CC Switch 切换 Claude Code 卡片，建议通过配套的 `ccswitch_admin.py`（或在界面切卡后补跑一次 `install.py`），以免覆盖钩子配置。暂时不用的客户端可在 `fleet_profile.json` 中停用，调度会自动跳过。
- **配置仓库自身维护**：打开普通课题目录时系统自动工作在日常开发模式；只有在本配置仓库目录内才启用上游维护流程（调整模型矩阵、全局同步、跨版本 ABI 升级和远端推送均须先经你确认）。

---

## 五、文档导航与目录结构

本仓库文档按读者角色严格分层，避免把底层实现细节堆到入门页：

| 你现在想做什么 | 去哪份文档 |
| --- | --- |
| **日常干活：选型分工、大仓库分片阅读、Euler HLLC 求解器闭环示例与 L1–L4 验收矩阵** | [日常工作流实战指南](docs/workflow-guide.md) |
| **查原理与运维：模块架构、四阶段钩子细节、配置键、预算公式、本地路由、体检建议与精确卸载** | [架构设计与钩子说明](docs/architecture-and-hooks.md) |
| **查脚本命令与完整参数规范** | [cae-harness 技能规程](template/user-guard/skills/cae-harness/SKILL.md) |
| **维护本配置仓库：模型换代评估、审批门禁与双端同步** | [fleet-evolve 维护规程](template/user-guard/skills/fleet-evolve/SKILL.md) · [仓库维护说明](AGENTS.md) |
| **查看插件目录清单、发版记录与时间戳凭证** | [插件模板索引](template/user-guard/README.md) · [更新日志](CHANGELOG.md) · [时间戳与 DOI 说明](.timestamps/README.md) |

```text
Antigravity_Codex_ClaudeCode_DeepSeekHarness/
├── README.md                              # 入口总览：快速上手、核心能力与文档导航
├── AGENTS.md / CHANGELOG.md               # 上游配置仓库维护说明与版本更新日志
├── LICENSE, NOTICE, CITATION.cff          # Apache License 2.0、原创归属声明与学术引用信息
├── .github/workflows/ci.yml               # 跨平台离线测试、事实锚定与文档卫生流水线
├── .timestamps/                           # RFC 3161 时间戳凭证与 DOI 发版说明
├── install.py / install.ps1 / install.sh  # 跨平台安装同步、配置体检、双端对齐与 ABI 检查
├── docs/                                  # 日常实战指南、架构与钩子说明及 3 张 SVG 流程图
├── template/
│   ├── user-guard/                        # 共用规则 (R0–R11)、钩子库、只读代理、动态配置与双技能
│   ├── codex/                             # Codex 事件钩子与只读子代理模板
│   └── claude/                            # Claude Code 事件钩子、只读代理与本地路由/切卡脚本
└── tests/                                 # 多平台安装、钩子守卫、调度器与文档规范全量离线单测
```

---

## 六、致谢、English Overview 与开源许可

### 1. 资助致谢

本项目获**国家留学基金管理委员会**（简称**国家留学基金委**，**China Scholarship Council, CSC**）资助，学号为 `202506280162`，批准文号为 `留金选[2025]37号`；该项资助为本项目目前的唯一资助来源。

### 2. English Overview

`Antigravity_Codex_ClaudeCode_DeepSeekHarness` provides shared engineering rules (`R0–R11`), deterministic lifecycle hooks, read-only review subagents, and cross-client headless verification scripts for **Google Antigravity** (`agy`), **OpenAI Codex** (`codex`), **Claude Code** (`claude`), and **DeepSeek Harness** (`dsh`). Built solely on the Python 3.11+ standard library for Windows, WSL2, and Linux, it is designed for scientific computing, CAE, CFD, FEA, numerical PDE solvers, HPC, and high-reliability software engineering.

Rather than relying on ever-longer system prompts, this repository turns recurring failure modes into deterministic hooks, unit tests, and verification gates. Routine small edits stay in the main session, while well-scoped derivations, solver modules, and independent reviews run in isolated background workers with concise pre-execution confirmation and verified write scopes. Offline deterministic tools—`read-plan`, `cross-check`, `reconcile`, and `check-grounding --doc-hygiene`—protect against context overflow, shared blind spots, stale documentation, and unverified claims across all four clients.

Run `install.py` once (on both Windows and WSL2 if you develop on both sides) to sync into your user-level client directories without adding config files to your project repositories. Users provide their own official client installations and official OAuth sign-in or vendor API keys. Supported solely by the China Scholarship Council (CSC), CSC No. `202506280162`, Approval No. `留金选[2025]37号`. Licensed under [Apache License 2.0](LICENSE); retain attribution notices in [NOTICE](NOTICE) (`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`).

### 3. 开源许可

本项目采用 [Apache License 2.0](LICENSE) 开源许可，版权归属 `Copyright 2026 Xinyu Li (Xinyu-Li-1997)`。复制或分发本仓库及其衍生作品时，须遵守协议第 4 条及第 4(d) 条，附带许可证副本并保留 [NOTICE](NOTICE) 中的完整原创归属声明。
