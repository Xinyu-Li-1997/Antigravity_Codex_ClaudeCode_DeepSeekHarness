---
name: cae-harness
description: >-
  Orchestrate bounded engineering, mathematical derivation, review, and
  verification tasks across four peer CLI agents: Codex, Antigravity (agy),
  Claude Code (claude / cc), and DeepSeek Harness (dsh). Verify backend LLM
  readiness before headless dispatch and enforce workspace write scopes and
  verification commands.
---

# 四端对等无头协作与门禁验证台架（`cae-harness`）

Antigravity（反重力，`agy`）、Codex（`codex`）、Claude Code（`claude`，别名 `cc`）与 DeepSeek Harness（`dsh`）处于同一对等逻辑层级：四端均独立落盘于各自的宿主机全局目录，既能作为发起方（`--caller`），也能经本技能预检确认底层 LLM／登录态就绪后作为无头执行后端（`--engine`）被按需或按用户自然语言指派调用。

- **日常统管与硬核攻坚分工**：多端齐备时，日常科研与数值求解器开发**默认由反重力总控**（统管四端更新、额度与上下文预算，负责多篇文献对照、跨模块梳理、中文技术写作与最终验收）；凡涉及**偏微分方程推导、高阶数值格式分析（如 DGSEM／WENO 通量、雅可比矩阵与守恒性证明）、核心求解器编码、深层边界审查及带 `--verify-cmd` 的门禁闭环**，**优先调 Codex 无头攻坚**；终端工程重构或按用户要求调用 Kimi／DeepSeek 混合分流卡片时可直接调 `claude`（`cc`）；阶段收口或发版前由 Codex 负责全仓代码回归 Debug 与门禁测试，由反重力负责文档润色、清淤精简与脱敏把关。
- **零门槛起步与应急保底（`DSH + DeepSeek`）**：初次上手或纯内网／国内直连环境可直接以 `DSH`（官方 `DEEPSEEK_API_KEY` 或官方账号，零反代灰色操作）独立承担主控与无头验证；多端环境下，当反重力、Codex 与 Claude Code 均不可用或配额冷却时，`dsh` 可随时接手调试与门禁复核。所有跨端无头结果均返回发起方总控，经按 `R6` 亲自核对原文件行号与运行证据后再交付。

## 职责边界：跨端无头子进程与本侧原生工具解耦

本技能仅管理通过官方 `codex`、`agy`、`claude` 与 `dsh` CLI 启动的无头独立子进程：
1. **不干预宿主原生只读子代理**：主会话通过原生机制派生的 `readonly-reader`、`readonly-auditor`、`readonly-collaborator` 始终保持 `inherit`（直接继承前端主会话模型与独立窗口，或在 Claude Code 本地路由下按 `CLAUDE_CODE_SUBAGENT_MODEL` 分流）；反重力原生 `run_workflow` 仅用于同端 10 个以上同构模块或算例目录的轻量纯只读盘点（由 `status_guard.py` 执行 R4 预审）。
2. **发包前强制校验底层 LLM 就绪（网络与登录／Key 由用户自理）**：网络连通、客户端安装、官方登录态（OAuth）与真实 API Key 属用户自理基础前提；调用任一后端前自动核实可执行文件与有效模型接入，未就绪时直接报明缺口请用户补齐，绝不编造绕过或灰产反代——`claude` 自动拦截未填密钥的占位符（如 `PASTE_*`），本地路由模式下主动探活 `/__router/health` 并只读核查 `cc-switch.db` 上游卡片密钥，直连／订阅模式下核验有效凭据或 `claude auth status`，且执行时不加 `--bare` 以完整继承 `~/.claude`（`CLAUDE.md`、`settings.json`、钩子、子代理与 CC Switch 当前卡片）；`dsh` 自动识别环境变量、`.env` 或 `.credentials.yaml` 中的官方 API Key／官方账号凭据，通过临时运行补丁（`--profile headless --patch`）注入模型与挡位，绝不改写用户全局配置文件。
3. **支持自然语言模糊指派与 100% 精确模块名锁定**：日常模糊诉求由选路器按任务专长自动分流（或在未锁死具体引擎且自然点名“让 CC 审一下”“交给 Codex 推导”“用 DSH 跑个兜底检查”时自动识别目标后端，否定句如“不用 CC”自动排除）；若需 100% 确定性调起特定功能，用户可直接在对话或参数中显式点名 `cae-harness` 子命令（`task`、`audit`、`gate`）与引擎别名（`codex:astra`、`codex:sol`、`cc:default`、`dsh:default`、`dsh:flash`）。

## 选模、最高思考挡位与最长上下文规则

1. **四端专长模型自动匹配（默认主力旗舰，不擅自降级小模型）**：
   - **Codex（`codex:default` / `codex:astra` / `codex:sol`）**：数学推导（`math`）、路线论证（`direction`）及高复杂度数理审查自动选深度推理旗舰 **`Astra`**（如 `gpt-6-astra`）；核心编码（`code`）、常规审查（`review`）、大输入审计（`audit`）与验证门禁（`gate`）自动选最新代工程旗舰 **`Sol`**（如 `gpt-6.1-sol`）。
   - **反重力（`agy:default` / `agy:carbon`）**：默认选当前最高代主力单模型 **`Carbon`**（如 `gemini-4-carbon-high`），默认排除固定复合模式（`Orchestrator`）、自动路由与稀缺配额第三方模型。
   - **Claude Code（`claude:default` 或 `cc:default`，支持 `cc:kimi` / `cc:deepseek` 别名）**：默认继承 `~/.claude/settings.json` 或 CC Switch 当前激活卡片配置的主模型（如混合分流下的 `k3.1-pro[1m]` 或直连卡片下的 `deepseek-flash[1M]`；官方订阅登录下回退标准别名 `opus`/`sonnet`/`haiku`）。
   - **DeepSeek Harness（`dsh:default` / `dsh:deepseek-v4-pro` / `dsh:flash`）**：默认选旗舰 **`deepseek-v4-pro`**（`DeepSeek-V4-Pro`），轻量省流档自动切 **`deepseek-flash`**（`DeepSeek-V4.1-Flash`；在多文件代码审查与工具循环场景亦可显式传 `dsh:flash --tier max` 调用）。
2. **默认单模型最高纯思考挡 + 按需分级省流（禁套娃 `ultra`）**：
   - **默认满血模式**：除纯文字润色（`polish` 短篇 `medium`、长篇 `high`，防 R5 无声放大）外，所有 `math`、`direction`、`code`、`review`、`audit`、`gate` 默认启用所选模型的**单模型纯思考最高挡**（Codex／Claude Code／DSH 为 `max`，反重力 `Carbon` 为 `high`），默认跳过二次派生子代理的 `ultra` 挡。
   - **按需省流模式**：仅当显式传 `--tier`、设置 `CAE_DEFAULT_TIER`，或用户在提示词中明确／模糊提出降挡、省额度、省 token 时切换分级自适应——常规任务用 `high` 挡，表面拼写／格式微查启用轻量高速模型（`Luna`、`Hydrogen`、`deepseek-flash`）配 `low` 挡。
3. **自动匹配声明最大上下文窗口**：
   - 读取所选模型的声明最大窗口（如 Codex `872,000` token、Claude Code 卡片 `1,000,000`／`1,048,576` token、DSH `1,048,576` token）计算安全输入预算，并在支持显式窗口参数的后端下发最大窗口配置；若 Codex 模型目录缺少窗口声明且宿主存在全局窗口覆盖，直接阻断提示刷新目录，防止跨模型误套上限。

## 子命令实战指南

编排脚本位于 [scripts/cae_harness.py](scripts/cae_harness.py)。以下命令假设当前工作目录为技能目录（Windows 用 `python`，WSL2／Linux 用 `python3`）：

### 1. 四端探测（`probe`）与选路评估（`assess`）

```sh
python -B scripts/cae_harness.py probe
python -B scripts/cae_harness.py assess --caller antigravity
python -B scripts/cae_harness.py task --help
```

- `probe`：零写入探测 `codex`、`agy`、`claude`、`dsh` 四端可执行路径、LLM 凭据／路由就绪态与模型目录；附 `--discover-models` 时调用 `agy models` 刷新反重力本地目录缓存（模型换代或配合仓库根 `install.py --audit-upgrade` 做全栈体检时先行调用）。
- `assess`：零写入输出四端就绪状态、目录新鲜度、声明窗口、安全输入预算及各任务类型的推荐执行画像。

### 2. 独立任务派发（`task`）

无头 CLI 子进程无法读取桌面聊天历史，派工任务单（`--task-file` 或 `--task`）须一次写清：**① 物理／数学假设、符号约定与最新裁定；② Git 基线与 `--target` 根目录；③ 必读源码或文献列表；④ 允许修改的文件白名单（`--write-scope`）；⑤ 交付格式或验收命令（`--verify-cmd`）**。

```sh
# 反重力调 Codex 推导高阶离散格式／雅可比矩阵（自动选 Astra + max 挡 + 872k 窗口；配额耗尽自动回退）
python -B scripts/cae_harness.py task --caller antigravity --engine codex:default --kind math --tier max --fallback-on-quota --target "<项目目录或单文件>" --task-file "<UTF-8任务单.md>"

# 反重力按需调 Claude Code（继承 ~/.claude 与 CC Switch 当前激活卡片）执行独立代码审查
python -B scripts/cae_harness.py task --caller antigravity --engine cc:default --kind review --target "<项目目录>" --task-file "<UTF-8任务单.md>"

# 首装起步或应急兜底：调 DeepSeek Harness (DeepSeek-V4-Pro) 执行编码与单元测试验收
python -B scripts/cae_harness.py task --caller dsh --engine dsh:default --kind code --tier max --target "<项目目录>" --task-file "<UTF-8任务单.md>" --write-scope "src/*.py" --verify-cmd "python -B -m unittest discover -s tests"
```

| `--kind` 类型 | 读写权限与结构化回执内容 |
| --- | --- |
| `math` | 只读读取输入材料，在回执中交付控制方程假设、完整推导步骤、适用范围与未验证项 |
| `direction` | 只读开展方案与数值格式相容性论证，返回依据对比与待拍板项 |
| `review` | 只读逐行审查代码或公式，返回问题定位、精确行号与原文证据 |
| `polish` | 只读读取原稿，在回执 `content` 中直接交付去套话、防无声放大的润色稿 |
| `code` | 仅在 `--write-scope` 授权范围内修改文件，推荐附 `--verify-cmd` 执行自动化验收（回执显式标注 `verification_executed`） |

写入范围必须位于 `--target` 之内（单文件未传 `--write-scope` 时默认仅授权该文件）。任务前后自动对比全目录快照，白名单外的任何写删均判定为 `scope_violation`。`--verify-cmd` 严禁传空白串；未传 `--verify-cmd` 的 `code` 任务在回执顶层显式标记 `"verification_executed": false`。向 `Astra (max)` 派发 `math`／`direction` 任务时宜聚焦预提炼方程（必要时 `--timeout 900–1200`）。

### 3. 大型求解器审计（`audit`）与物理／测试门禁（`gate`）

```sh
# 跨端交叉／自动分片审计大型代码库（支持 codex / agy / cc / dsh 任意两两组合或单端分片）
python -B scripts/cae_harness.py audit --caller antigravity --target "<项目目录>" --left codex:default --right cc:default --strategy auto --fallback-on-quota

# 编码 + 验证命令闭环门禁（失败时在限定轮次内携带真实报错排错，严禁放宽容差）
python -B scripts/cae_harness.py gate --caller antigravity --worker codex:sol --tier max --fallback-on-quota --target "<项目目录>" --task-file "<UTF-8任务单.md>" --write-scope "src/*.py" --verify-cmd "<验收命令>"
```

- `audit`：扣除提示词、清单与 JSON Schema 开销后，预算内采用单次全量审查（`single-pass`），超预算按模块内聚性自动分片（`sharded`），并核对计划文件与实际已读覆盖。横跨多文件的强耦合算子（如 DGSEM 体积分、界面数值通量与弯曲网格度量恒等式）须由总控做跨片联合复核。
- `gate`：编码落盘后自动运行非空 `--verify-cmd`（验收命令自身生成的临时测试产物归入 `verification_changed_files`）；未通过时在 `--max-rounds` 内携带报错继续修复，严禁放宽 `rtol`／`atol`、弱化守恒判据或删改断言。

## 额度熔断回退与跨平台运行边界

1. **四端额度熔断与 `--fallback-on-quota` 顺位接管**：
   - 任一后端返回配额／速率耗尽时，立即熔断同批次后续调用，返回 `quota_exhausted`（退出码 `5`）并按执行侧（`windows`／`posix`）持久化冷却截止时间；冷却期内请求在发包前直接拦截（`preflight_blocked: true`）。
   - 附带 `--fallback-on-quota` 且工作区文件尚无任何改动时，脚本自动切回发起方本侧或顺位已就绪的对等端完成任务，切换详情记入 `quota_fallback`；若中途已修改过工作区文件，绝不自动换端重跑写入。
2. **无痕会话隔离与 Windows／WSL 互操作**：
   - 四端无头调用均保持会话隔离（`codex exec --ephemeral`、`~/.gemini/antigravity-cli`、`claude -p --no-session-persistence`、`dsh --profile headless` 独立运行目录），不污染桌面或终端交互会话历史。
   - **Windows 沙箱反向调用提示**：若从 Windows 版 Codex 受限沙箱（AppContainer）内部反向调 `agy.exe`，受限令牌会阻止写入 `~/.gemini/antigravity-cli` 运行锁导致超时，须在放行 `~/.gemini` 写权限的宿主通道中执行并设 `--timeout 300+`。
   - **WSL 互操作**：WSL 优先调用本侧已就绪的原生 Linux CLI；未安装或未登录时自动回退调用 Windows 侧对应 CLI（`/mnt/<盘符>` 共享盘可直接读写；纯 Linux 路径支持只读临时镜像审查，写入任务须置于共享盘或使用 WSL 原生 CLI）。
