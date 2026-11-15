---
name: cae-harness
description: >
  Orchestrate bounded engineering, mathematical derivation, review, and verification tasks across
  four peer CLI agents: Codex, Antigravity (agy), Claude Code (claude / cc), and DeepSeek Harness (dsh).
  Verify backend LLM readiness before headless dispatch and enforce workspace write scopes and verification commands.
---

# 四客户端角色路由与后台验证工具（`cae-harness`）

`cae-harness` 通过纯 Python 3.11+ 标准库脚本 `scripts/cae_harness.py` 按需启动本机已安装就绪的 **Codex**、**Antigravity（`agy`）**、**Claude Code（`claude` 或 `cc`）** 与 **DeepSeek Harness（`dsh`）** 无头命令行（Headless CLI，即不打开交互窗口、在后台批处理运行并输出结构化 JSON）子进程。每次调用均在独立进程与全新上下文中运行，不占用当前主会话上下文容量，并在运行前后自动校验文件读写白名单与定量验证命令退出码。

## 一、选型机制与运行安全边界

1. **配置文件驱动角色选路，脚本零时期性模型名硬编码**：
   - 脚本从同目录的 [fleet_profile.json](fleet_profile.json)（可通过环境变量 `CAE_FLEET_PROFILE` 覆盖做离线测试）读取经用户审定的角色候选顺序、模型系列优先级、暂缓与退役过滤规则及配额回退顺序。
   - 支持按抽象角色选路（`role:math`、`role:code`、`role:review`、`role:fast`、`role:orchestrator`）或按客户端槽位选路（`codex:math`、`codex:code`、`codex:default`、`cc:default`、`dsh:default`、`dsh:fast`、`agy:default`）；当用户显式点名 `<client>:<model_slug>` 时按原样调用。
2. **前后端隔离与防递归保护**：仅管理后台无头子进程，不修改交互式主会话模型或客户端内置 `inherit` 子代理；启动子进程时注入 `CAE_HARNESS_DEPTH=1`，若子进程内部再次调用非探测类子命令则直接拦截递归。
3. **运行前凭据、路由与配额冷却预检**：派发前通过 `probe` 核实客户端可执行文件、官方登录态或 API Key、本地分流代理 `/__router/health` 健康状态，以及当前运行侧（Windows 与 WSL2 独立计量）的配额冷却记录。遇到滚动窗口限额、周度限额或 API 余额不足（退出码 `5`，`quota_exhausted`）时自动解析重置时刻并停止盲目重试；仅当显式传入 `--fallback-on-quota` 且工作区文件尚未发生任何改动时，才自动切换至回退顺序中的下一个就绪客户端。
4. **常规模式与 `--clean-room` 空上下文盲读模式**：
   - **常规模式（默认）**：子进程拥有全新空白对话历史，但按客户端默认机制加载宿主机全局规则、生命周期钩子与目标目录下的 `AGENTS.md`，且系统指令前缀保持稳定以复用服务端自动前缀缓存。其中 `claude` 只读无头调用固定附带 `--permission-mode dontAsk`，规避 `auto` 模式的额外安全分类器限流报错；`dsh` 自动附加单 JSON 输出约束并剥离尾随 `DSML` 内部标记。
   - **空上下文盲读模式（`task --clean-room`）**：支持 `codex`、`claude`、`dsh` 的只读任务（`agy` 暂不支持临时剥离全局插件，调用时明确报错拦截）。脚本在系统临时目录构造仅含最小认证凭据的隔离配置目录，并在只读工作区副本中剔除 `AGENTS.md` 与 `CLAUDE.md`，用于零预设检验技术文档对新读者的可读性。
5. **显式最大窗口、单模型最高非委派挡位与文件读写门禁**：
   - 除文字润色任务 `polish`（默认 `medium` 或 `high`，防止过度引申放大原意）或显式指定 `role:fast` 与低挡位外，默认显式传入目标模型声明的最大上下文窗口（覆盖偏保守的出厂默认窗口）与最高单模型非委派推理挡位，自动跳过会派生多层子代理引发轮询超时的 `ultra` 挡。目录审查 `audit` 按有效上下文窗口与自动压缩窗口最小值的 $90\%$（Antigravity 默认保守预算 `120,000` token，可由 `CAE_AGY_INPUT_BUDGET` 调整）自动分片送审。
   - 只读任务运行前后对工作区（含 `.github`）计算文件哈希快照，出现任何增删改立即判定 `scope_violation`；修改代码任务通过 `--write-scope` 锁定允许写入的路径通配符（单文件目标自动锁定该文件），`gate` 命令强制要求提供非空 `--verify-cmd`。在 Windows 下对大型仓库派发后台任务时，须在任务单与 `--write-scope` 中明确限定目标文件与行号区间，防止全目录扫描超时。

## 二、常用命令示例

在已安装技能目录或仓库对应路径下执行（Windows 使用 `python -B -X utf8`，WSL2 或 Linux 使用 `python3 -B -X utf8`）：

```bash
# 1. 只读探测四个客户端安装状态、凭据就绪度、模型目录缓存、冷却记录与单分片预算摘要
python -B -X utf8 scripts/cae_harness.py probe --caller auto

# 2. 查看本机已就绪客户端及各角色槽位（math / code / review / fast）的当前解析结果
python -B -X utf8 scripts/cae_harness.py assess --caller auto

# 3. 目录级只读代码审查（默认双客户端交叉核查；加 --single 指定单角色槽位，大目录按安全预算自动分片）
python -B -X utf8 scripts/cae_harness.py audit --caller auto --target src --single --left role:code \
  --focus "检查离散通量守恒性、几何奇异退化分支与数组越界风险"

# 4. 派发单次专项任务（--kind 可选 math | review | direction | polish | code；新读者盲读加 --clean-room）
python -B -X utf8 scripts/cae_harness.py task --caller auto --kind math --engine role:math --tier max --timeout 900 \
  --target . --task-file "<任务单.md>"

# 5. 在限定白名单内修改源码并强制运行定量验证脚本闭环（严禁放宽 rtol/atol 或删改测试断言）
python -B -X utf8 scripts/cae_harness.py gate --caller auto --worker role:code --tier max \
  --target . --write-scope "src/**" --verify-cmd "pytest -q tests/test_core.py" \
  --task-file "<任务单.md>"
```

## 三、主会话复核要求（`R6` 与 `R3.6`）

- 所有后台无头调用返回的结构化 JSON 默认标记 `handoff_to_caller: true`，主会话必须亲自打开原文件核对精确行号、符号约定与上下文后再决定是否采纳。
- 汇报时严格按实际验证深度区分“源码或配置已就位（`verification_executed: false`）”“编译与离线单测已通过”以及“本机实际环境或算例不变量／收敛阶实测已通过”；涉及宿主机全局目录写入与远端仓库推送一律先征得用户明确批准。
