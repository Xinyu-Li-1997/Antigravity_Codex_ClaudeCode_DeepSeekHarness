---
name: cae-harness
description: >
  Orchestrate bounded engineering, mathematical derivation, review, and verification tasks across
  four peer CLI agents: Codex, Antigravity (agy), Claude Code (claude / cc), and DeepSeek Harness (dsh).
  Verify backend LLM readiness before headless dispatch and enforce workspace write scopes and verification commands.
---

# CAE 四端后台调用与验证脚本（`cae-harness`）

`cae-harness` 通过纯 Python 标准库脚本 `scripts/cae_harness.py` 按需启动本机已安装配好的 **Codex**、**Antigravity（`agy`）**、**Claude Code（`claude` / `cc`）** 与 **DeepSeek Harness（`dsh`）** 无头 CLI 子进程。每次调用都在全新的独立进程中执行，不占用当前主对话的上下文窗口，并自动检查目标文件的读写范围与验证测试结果。四个客户端与各模型的具体搭配原则见仓库根 `README.md` 第四章。

## 一、核心运行机制

1. **只管理后台子进程，不改动前端主窗口设置**：主窗口当前选用的模型以及客户端自带只读子代理（默认 `inherit`）均保持用户原有选择，脚本不会反向修改。内部通过 `CAE_HARNESS_DEPTH=1` 环境变量阻止后台子进程再次递归调用自身。
2. **派发前先检查凭据就绪状态**：网络代理、客户端软件安装、官方账号登录以及 API Key（例如环境变量或 `~/.dsh/.credentials.yaml` 中的官方 `DEEPSEEK_API_KEY`）由用户事先准备。启动任务前先用 `probe` 检查目标客户端是否已安装、模型凭据是否可用以及是否处于额度冷却期；发现未登录、缺 Key 或本地分流服务未启动时直接报错说明缺失项，绝不伪造凭据或绕过检查。
3. **默认继承环境 vs. `--clean-room` 纯净盲读模式**：
   - **常规模式（默认）**：每次调用虽然拥有**全新的空对话历史**（`codex --ephemeral`、`claude -p --no-session-persistence`、`dsh --profile headless`、`agy` 单次调用），但**默认仍会加载该客户端全局配置目录里的共用规则（`AGENTS.md` / `CLAUDE.md`）、生命周期钩子、全局技能，以及目标目录内的 `AGENTS.md`**。这种模式适合日常公式推导、代码编写与工程审查。
   - **纯净盲读模式（`task --clean-room`）**：仅限 `codex`、`claude`、`dsh` 的只读任务（`agy` CLI 不支持临时剥离全局插件，传入时会直接报错说明）。开启后，脚本在系统临时目录构造仅保留底层登录凭据或 API 环境变量的最小配置目录（`claude` 自动加 `--bare` 与纯环境变量 `--settings`），并在只读镜像目录中剔除 `AGENTS.md` 与 `CLAUDE.md`（当 `codex` 只读镜像总大小不超过 `256 KiB` 时自动附带行号快照以兼容空配置下的 Windows 只读沙箱）。此时后台模型处于**空对话历史 + 无全局规则 + 无钩子 + 无全局技能**状态，专门用于模拟外部新手读者检查文档可读性或做零预设盲审。
4. **推理强度与读写范围检查**：除纯文字润色（`polish` 用 `medium` 或 `high` 防止过度引申放大原意）或用户明确要求节省额度外，默认启用所选模型的最高单模型推理强度（`max`、`xhigh` 或 `high`）。只读任务（`review`、`direction`、`polish`、`math`、`audit`）前后会对工作目录（含 `.github`）做文件哈希快照对比，发现任何文件增删改即标记为越界（`scope_violation`）。修改代码的任务（`code`、`gate`）必须用 `--write-scope` 指定允许改动的文件通配范围，`gate` 还必须提供非空的 `--verify-cmd` 测试命令。遇到配额耗尽（退出码 `5`）时按当前运行系统侧记录冷却时间；仅当附带 `--fallback-on-quota` 且工作区文件完全未改动时，才自动切换到下一个可用客户端继续完成。

## 二、五个常用子命令

在已安装技能目录或仓库对应路径下运行 `scripts/cae_harness.py`（Windows 用 `python -B -X utf8`，WSL2 / Linux 用 `python3 -B -X utf8`）：

```bash
# 1. 只读检查四个客户端的安装路径、登录与 Key 状态、模型列表缓存及冷却记录（加 --discover-models 主动刷新模型列表）
python -B -X utf8 scripts/cae_harness.py probe --caller auto

# 2. 查看当前机器上哪些客户端可以直接使用，以及不同科研任务的推荐调用方案（只读不改文件）
python -B -X utf8 scripts/cae_harness.py assess --caller auto

# 3. 对整个代码目录做只读排查（默认双客户端交叉检查；加 --single 可只跑指定引擎，大目录自动分批送审）
python -B -X utf8 scripts/cae_harness.py audit --caller auto --target . --single --left codex:sol \
  --focus "检查有限体积通量守恒性、边界幽灵单元赋值与数组下标越界"

# 4. 派发单次专项任务（--kind 可选 review | direction | polish | math | code；只读盲读可加 --clean-room）
python -B -X utf8 scripts/cae_harness.py task --caller auto --kind math --engine codex:astra --tier max --timeout 900 \
  --target . --task-file "<任务单.md>"

# 5. 修改代码并自动运行测试验证（严禁放宽 rtol/atol 容差或删改断言来凑过测试）
python -B -X utf8 scripts/cae_harness.py gate --caller auto --worker codex:sol --tier max \
  --target . --write-scope "src/**" --verify-cmd "pytest -q tests/test_conservation.py" \
  --task-file "<任务单.md>"
```

## 三、主会话复核要求（`R6` 与 `R3.6`）

- 后台子进程返回的所有问题定位、公式推导和修改建议（回执带 `handoff_to_caller: true`），主会话都必须**亲自打开原文件核对具体行号与上下文**，确认无误后再采纳或向用户汇报。
- 汇报进展时明确区分“配置文件已写好”“离线测试已通过”和“真机 CLI 或算例实测已通过”；Git 提交与推送一律在用户明确同意后执行。
