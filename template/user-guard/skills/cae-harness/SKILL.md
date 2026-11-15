---
name: cae-harness
description: >
  Orchestrate bounded engineering, mathematical derivation, review, and verification tasks across
  four peer CLI agents: Codex, Antigravity (agy), Claude Code (claude / cc), and DeepSeek Harness (dsh).
  Verify backend LLM readiness before headless dispatch and enforce workspace write scopes and verification commands.
---

# 四端角色路由与后台验证编排器（`cae-harness`）

`cae-harness` 通过纯 Python 标准库脚本 `scripts/cae_harness.py` 按需拉起本机已就绪的 **Codex**、**Antigravity（`agy`）**、**Claude Code（`claude`／`cc`）** 与 **DeepSeek Harness（`dsh`）** 无头 CLI 子进程。每次派工均在独立进程与全新上下文中运行，不占用当前主窗口上下文，自动校验文件读写边界与验证命令退出码。

## 一、元驱动选模与核心安全机制

1. **受控清单驱动，代码与规则零模型名硬编码**：
   - 脚本从同目录的 `fleet_profile.json`（可通过环境变量 `CAE_FLEET_PROFILE` 指定替代清单做离线验证）读取当前经审定生效的角色候选链、模型系列优先级、淘汰模型规则与回退顺序。
   - 支持直接按抽象角色选路（`role:math`、`role:code`、`role:review`、`role:fast`、`role:orchestrator`）或按客户端槽位选路（`codex:math`、`codex:code`、`codex:default`、`cc:default`、`dsh:default`、`dsh:fast`、`agy:default`）；用户显式指定 `<client>:<model_slug>` 时按原样透传。
2. **前后端隔离与防递归**：只管理后台无头子进程，绝不反向篡改前端主会话模型或客户端自带 `inherit` 子代理；内部注入 `CAE_HARNESS_DEPTH=1` 阻断后台进程递归套娃。
3. **派发前凭据与配额预检**：运行前通过 `probe` 核实目标客户端可执行文件、官方登录态／API Key、本地路由 `/__router/health` 状态及同平台冷却账本。遇到会话限额、周度限额或配额耗尽（退出码 `5`）立即记录冷却并停止盲目重试；仅当传入 `--fallback-on-quota` 且工作区文件零改动时才切往下一可用客户端。
4. **常规模式与 `--clean-room` 纯净盲读模式**：
   - **常规模式（默认）**：拥有全新空对话历史，但仍按客户端默认机制加载宿主全局规则、生命周期钩子与目标目录 `AGENTS.md`，适合数学推导、算法实现与常规代码审查。`claude` 只读无头调用固定附带 `--permission-mode dontAsk`，避免触发 `auto` 分类器限流报错；`dsh` 自动注入单 JSON 输出结构强约束。
   - **纯净盲读模式（`task --clean-room`）**：支持 `codex`、`claude`、`dsh` 只读任务（`agy` 不支持剥离全局插件，调用时明确报错）。脚本在系统临时目录构造仅含最小认证信息的隔离配置，并在只读镜像中剔除 `AGENTS.md`／`CLAUDE.md`，以**空历史 + 无全局规则 + 无钩子 + 无全局技能**状态检验文档在外部新手眼中的真实可读性。
5. **单模型最高挡位与读写硬边界**：除 `polish`（默认 `medium`／`high` 防扩写失真）或显式指定 `role:fast`／低挡位外，默认选取所选模型支持的最高单模型非委派挡位（自动过滤会派发大量子代理空等的 `ultra`）。只读任务前后执行工作区（含 `.github`）哈希快照比对，任何增删改立即判定 `scope_violation`；写任务支持多次传入 `--write-scope` 限定白名单（目标为单文件且未传时自动锁定该文件，越界即判 `scope_violation`），`gate` 强制要求非空 `--verify-cmd`。在 Windows 下对大型仓库派工时，应在任务单与 `--write-scope` 中明确限定目标文件范围，避免全仓遍历超时。

## 二、常用命令示例

在已安装技能目录或仓库对应路径下运行（Windows 用 `python -B -X utf8`，WSL2／Linux 用 `python3 -B -X utf8`）：

```bash
# 1. 只读探测四端安装状态、凭据就绪度、模型目录缓存、冷却记录与当前生效的 fleet_profile 摘要
python -B -X utf8 scripts/cae_harness.py probe --caller auto

# 2. 查看本机当前就绪后端及各角色槽位（math / code / review / fast）的推荐派工路由
python -B -X utf8 scripts/cae_harness.py assess --caller auto

# 3. 目录级只读审查（默认异构双引擎交叉核查；加 --single 指定单角色槽位，大目录自动分片）
python -B -X utf8 scripts/cae_harness.py audit --caller auto --target src --single --left role:code \
  --focus "检查离散通量守恒性、几何奇异退化分支与数组越界风险"

# 4. 派发单次专项任务（--kind 可选 math | review | direction | polish | code；新手盲读加 --clean-room）
python -B -X utf8 scripts/cae_harness.py task --caller auto --kind math --engine role:math --tier max --timeout 900 \
  --target . --task-file "<任务单.md>"

# 5. 限定文件范围修改代码并运行验证命令闭环（严禁放宽 rtol/atol 或删改测试断言）
python -B -X utf8 scripts/cae_harness.py gate --caller auto --worker role:code --tier max \
  --target . --write-scope "src/**" --verify-cmd "pytest -q tests/test_core.py" \
  --task-file "<任务单.md>"
```

## 三、主会话复核要求（`R6` 与 `R3.6`）

- 所有无头回执默认携带 `handoff_to_caller: true`，主会话必须**亲自打开原文件核对具体行号与上下文**后再采纳或汇报。
- 严格区分“配置／源码已就位”“离线测试已通过”与“真实宿主 CLI／算例实测已通过”；涉及全局目录写入与远端推送一律先请用户批准。
