---
name: cae-harness
description: >
  Orchestrate bounded engineering, mathematical derivation, review, and verification tasks across
  four peer CLI agents: Codex, Antigravity (agy), Claude Code (claude / cc), and DeepSeek Harness (dsh).
  Verify backend LLM readiness before headless dispatch and enforce workspace write scopes and verification commands.
---

# CAE 四端无头协同与数值门禁编排器（`cae-harness`）

`cae-harness` 通过纯标准库脚本 `scripts/cae_harness.py` 调度宿主机已就绪的 **Codex**、**Antigravity（`agy`）**、**Claude Code（`claude`）** 与 **DeepSeek Harness（`dsh`）** 无头 CLI 子进程，在严格隔离主控上下文的前提下执行控制方程推导、高阶格式审查、有界源码修改与确定性验收循环。四端选型矩阵与触发优先级以仓库根 `README.md` 第四章为唯一真源。

## 一、核心约束

1. **只管无头 CLI，不碰宿主原生子代理**：主会话模型与原生只读子代理（默认 `inherit`）由各宿主自行管理；`cae_harness.py` 仅管理跨进程无头调用，严禁递归嵌套自调（内置 `CAE_HARNESS_DEPTH=1` 硬阻断）。
2. **预检先行，未就绪诚实暴露**：网络连通、客户端安装、官方登录态与 API Key 属用户自理底座；执行前先通过 `probe` 核实 `available`、`llm_configured` 与 `quota_status`，严禁伪造凭据或编写绕过逻辑。
3. **专长选模与最高纯思考挡**：
   - `codex`：`math`/`direction` 默认选 `Astra`，`code`/`review`/`audit`/`gate` 默认选最新代 `Sol`，下发目录最高纯思考挡（`max`）与声明最大窗口（`872,000`），跳过二次派生的 `ultra`。
   - `agy`：默认选最新代 `Carbon` 最高单模型挡位（`high`），默认排除自动路由与稀缺配额第三方模型。
   - `claude`：继承 `~/.claude/settings.json`（及本地 `router.py`）激活卡片模型，开启 `--bare` 隔离宿主交互钩子，下发纯思考最高挡（`max`／`xhigh`）。
   - `dsh`：默认选 `deepseek-v4-pro`（`max`）；工程工具快审或显式省流选 `deepseek-flash`（`DeepSeek-V4.1-Flash`）。
   - 仅纯文字润色（`polish`）按篇幅取 `medium`／`high` 防 `R5` 无声放大；仅用户明确提出“省额度／降挡”时切换省流挡位。
4. **快照防越界与额度熔断**：只读任务（`review`/`direction`/`polish`/`math`/`audit`）前后比对工作区文件快照（含 `.github` 工作流），出现任何写删立即判 `scope_violation`；写入任务（`code`/`gate`）强制校验 `--write-scope`。触发 `quota_exhausted`（退出码 `5`）立即熔断并记录冷却锁；仅当显式传入 `--fallback-on-quota` 且工作区零改动时自动回退至本侧或次选就绪端。

## 二、五大 CLI 子命令速查

> 脚本路径：在宿主机已安装技能目录下调用对应 `<skill_dir>/scripts/cae_harness.py`（Windows 用 `python -B -X utf8`，WSL2／Linux 用 `python3 -B -X utf8`）。

```bash
# 1. 只读探测四端 CLI、登录/Key 就绪态、模型缓存与配额冷却锁（零写入；加 --discover-models 主动刷新模型表）
python -B -X utf8 scripts/cae_harness.py probe --caller auto

# 2. 输出各端能力画像与七类科研任务选路推荐（零写入）
python -B -X utf8 scripts/cae_harness.py assess --caller auto

# 3. 分片或单趟只读代码审查（默认异构双盲；加 --single 单路指定引擎，如 codex:sol）
python -B -X utf8 scripts/cae_harness.py audit --caller auto --target . --single --left codex:sol \
  --focus "检查有限体积通量守恒性、边界条件相容性与数组越界"

# 4. 单次有界专家任务（kind: review | direction | polish | math | code）
python -B -X utf8 scripts/cae_harness.py task --caller auto --kind math --engine codex:astra --target . \
  --task "推导可压缩 Navier-Stokes 特征边界条件离散形式并核对量纲一致性"

# 5. 编码 + 确定性验收命令闭环（严禁放宽 rtol/atol 或删改断言凑过门禁）
python -B -X utf8 scripts/cae_harness.py gate --caller auto --engine codex:sol --target . \
  --write-scope "src/**" --verify-cmd "pytest -q tests/test_conservation.py" \
  --task "修复边界面通量积分符号错误并验证离散守恒残差至机器精度"
```

## 三、主控复核铁律（`R6` ＆ `R3.6`）

- `cae_harness.py` 返回的所有候选问题、公式推导与补丁建议（`handoff_to_caller: true`），当前主控线程**必须亲自读取原文件与对应行号逐条核实**后再采纳或汇报。
- 如实区分配置存在、离线单测通过与真机无头往返实测通过三层证据；Git 提交与推送始终由主控在用户明确授权后执行。
