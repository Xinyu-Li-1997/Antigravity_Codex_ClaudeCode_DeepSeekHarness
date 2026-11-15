---
name: cae-harness
description: >
  Orchestrate bounded engineering, mathematical derivation, review, polish grounding verification,
  and closed-loop testing across four peer CLI agents: Codex, Antigravity (agy), Claude Code (claude / cc),
  and DeepSeek Harness (dsh). Dynamically route all five role slots from fleet_profile.json, enforce
  workspace write scopes and verification commands, and run zero-blacklist document grounding audits.
---

# 四客户端动态角色路由、事实锚定与后台验证工具（`cae-harness`）

`cae-harness` 通过纯 Python 3.11+ 标准库脚本 `scripts/cae_harness.py` 按需启动本机已就绪的 **Codex**、**Antigravity（`agy`）**、**Claude Code（`claude` 或 `cc`）** 与 **DeepSeek Harness（`dsh`）** 无头命令行（Headless CLI）子进程，并提供零外部依赖的文档事实锚定核验子命令 `check-grounding`。本技能在**通用课题项目**（`general_project`）与**上游配置仓库**（`upstream_config_repo`）中均可直接使用；每次后台调用均在独立进程与全新上下文中运行，不占用交互式主会话窗口。

## 一、动态选路、工作区识别与安全门禁

1. **五个角色槽位与运行期定量参数全部由 [fleet_profile.json](fleet_profile.json) 动态驱动**：
   - 脚本不含任何特定时期的模型名称硬编码，从同目录 [fleet_profile.json](fleet_profile.json)（可通过环境变量 `CAE_FLEET_PROFILE` 覆盖做离线沙箱测试）读取五个对等动态角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）的候选顺序、推荐模型系列、推荐推理挡位、暂缓与退役过滤规则，以及 `runtime_tunables.headless_dispatch` 定量参数（分片安全系数、默认分片上限、超时、验证轮数、配额默认冷却、目录缓存有效期、双盲行号容差与润色长短篇分界）。
   - 支持跨端角色选路（`role:orchestrator`、`role:math`、`role:code`、`role:review`、`role:fast`）、指定客户端角色选路（`<client>:@orchestrator`、`<client>:@math`、`<client>:@code`、`<client>:@review`、`<client>:@fast`，及兼容简写 `codex:math`、`codex:code`、`codex:review`、`dsh:fast`、`claude:fast`）或显式指定模型（`<client>:default`、`<client>:<model_slug>`）。
2. **工作区身份硬判别（`workspace_mode`）与防递归保护**：
   - `probe`、`assess` 与 `check-grounding` 均自动识别 `workspace_mode`：仅当目标目录或其父目录同时包含 `install.py` 与 `template/user-guard/plugin.json` 时标记为 `upstream_config_repo`；其余业务仓库标记为 `general_project`（将 `fleet-evolve`、`fleet_state.py` 与 `install.py` 列入 `upstream_only_capabilities`，防止日常课题优化误触宿主机全局配置演进）。
   - 启动无头子进程时自动注入 `CAE_HARNESS_DEPTH=1`；子进程内除只读探测与 `check-grounding` 外再次调用编排器会被直接拦截。
3. **运行前凭据、路由与配额冷却预检**：
   - 派发前核实可执行文件、官方登录态或 API Key、本地 `router.py` `/__router/health` 状态及当前运行侧（Windows 与 WSL2 独立计量）的配额冷却记录。
   - 遇到滚动窗口限额、周度限额或余额不足（退出码 `5`，`quota_exhausted`）时立即停止重试；仅当显式开启 `--fallback-on-quota` 且工作区文件尚未发生任何改动时，才按 `peer_fallback_order` 切换至下一个就绪客户端（双盲 `audit` 回退自动跳过另一侧已占用的客户端以保持双源独立）。
4. **推理挡位规则、`--clean-room` 空上下文盲读与读写快照门禁**：
   - **推理挡位与窗口**：常规推导、编码、审查与验证任务显式传入模型声明的最大上下文窗口与单模型非委派最高推理强度（`max`、`xhigh` 或 `high`），过滤 `ultra` 自动委派挡；`polish` 润色按篇幅阈值自动选用 `medium`（短篇）或 `high`（长篇）；`role:fast` 省额度快检默认选用 `low` 挡。`claude` 只读调用固定附加 `--permission-mode dontAsk`，`dsh` 自动附加单 JSON 输出约束并清洗尾随 `DSML` 标记与外层代码围栏。
   - **空上下文盲读模式（`task --clean-room`）**：支持 `codex`、`claude`、`dsh` 只读任务，在系统临时目录构造仅含最小认证凭据的隔离配置并剔除 `AGENTS.md` 与 `CLAUDE.md`，从零预设新读者视角检验文档可读性。
   - **确定性事实锚定核验（`check-grounding` 与 `polish` 自动审计）**：零硬编码领域黑名单，直接扫描工作区源码与配置文件构建词法锚点集合，逐行校验文档与 SVG 中的行内 `--<选项>` 及文件名标识符、全大写技术缩写、带单位定量数字以及举例与括注并列领域词干；`task --kind polish` 自动附带相对原稿的 `grounding_audit` 核验结果。
   - **读写边界校验**：只读任务前后计算全目录（含 `.github`）文件哈希快照，任何改动立即报 `scope_violation`；改码任务通过 `--write-scope` 锁定路径白名单，`gate` 强制要求非空 `--verify-cmd`。

## 二、常用命令示例

在已安装技能目录或仓库对应路径下执行（Windows 使用 `python -B -X utf8`，WSL2 或 Linux 使用 `python3 -B -X utf8`）：

```bash
# 1. 只读探测工作区类型（general_project / upstream_config_repo）、四端凭据、冷却记录与预算摘要
python -B -X utf8 scripts/cae_harness.py probe --caller auto

# 2. 查看本机已就绪客户端及全部五个动态角色槽位（orchestrator/math/code/review/fast）的解析结果
python -B -X utf8 scripts/cae_harness.py assess --caller auto

# 3. 零黑名单文档事实锚定核验（重写或润色终审必跑；加 --base-ref HEAD 可额外拦截相对基线新增的陌生短语）
python -B -X utf8 scripts/cae_harness.py check-grounding --target . --strict

# 4. 目录级只读代码审查（大目录按 runtime_tunables 分片安全系数自动分片送审）
python -B -X utf8 scripts/cae_harness.py audit --caller auto --target src --single --left role:review \
  --focus "检查离散通量守恒性、奇异网格退化保护与数组越界风险"

# 5. 派发专项无头任务（--kind 可选 math | review | direction | polish | code；新读者盲读加 --clean-room）
python -B -X utf8 scripts/cae_harness.py task --caller auto --kind math --engine role:math --tier max --timeout 900 \
  --target . --task-file "<任务单.md>"

# 6. 限定白名单修改源码并强制运行定量验证脚本闭环（严禁放宽 rtol/atol 或删改断言）
python -B -X utf8 scripts/cae_harness.py gate --caller auto --worker role:code --tier max \
  --target . --write-scope "src/**" --verify-cmd "pytest -q tests/test_core.py" \
  --task-file "<任务单.md>"
```

## 三、主会话复核要求（`R6` 与 `R3.6`）

- 所有后台无头调用返回的结构化 JSON 默认标记 `handoff_to_caller: true`，主会话必须亲自打开原文件核对精确行号、符号约定与上下文后再决定是否采纳。
- 汇报时严格按实际验证深度区分“源码或配置已就位（`verification_executed: false`）”“编译与离线单测已通过”以及“本机实际环境或算例守恒与收敛阶实测已通过”；涉及宿主机全局目录写入与远端仓库推送一律先征得用户明确批准。
