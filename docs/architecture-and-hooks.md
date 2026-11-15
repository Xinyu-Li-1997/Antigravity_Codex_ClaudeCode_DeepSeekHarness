# 底层架构、生命周期钩子与跨主机实测遥测说明

本文面向维护本仓库、排查钩子拦截逻辑或审计安全边界的工程师，说明模块职责、两层元驱动配置协议、四个客户端挂载细节、钩子失败兜底策略、本地分流路由参数，以及跨主机真实对话与代理日志的量化审计结论。日常安装与实战流程见 [仓库总 README](../README.md) 与 [实战指南](workflow-guide.md)。

---

## 一、核心代码模块与两层解耦职责

全仓脚本仅依赖 Python 3.11+ 标准库，钩子均为客户端事件触发的短生命周期进程（`stdin`／`stdout` JSON），无常驻守护进程负担（仅可选的 Claude 本地分流器 `router.py` 为轻量本地 HTTP 转发服务）：

| 模块 | 路径 | 职责边界 |
| :--- | :--- | :--- |
| **受控能力评估清单** | `template/user-guard/skills/cae-harness/fleet_profile.json` | 全仓唯一维护角色槽位（`orchestrator`／`math`／`code`／`review`／`fast`）候选链、优缺点与隔离说明、排除厂商、模型目录匹配规则及卡片默认值的机器可读真源 |
| **安装与耦合体检器** | `install.py`、`install.ps1`、`install.sh` | 探测双侧客户端与 Python 3.11+ 解释器，幂等部署规则、钩子、子代理与技能，提供零写入配置漂移与全局科学计算包 ABI 耦合体检（`--audit-upgrade`） |
| **原话归档与文件锁库** | `template/user-guard/hooks/user_words.py` | 增量解析 Antigravity、Codex、Claude Code 本地会话轨迹提取用户原话，提供跨平台文件锁与符号链接／重解析点拦截 |
| **压缩恢复注入钩子** | `template/user-guard/hooks/session_context_loader.py` | 在发生自动压缩或会话恢复时注入原话归档路径与核对提醒 |
| **写边界与日志防空读钩子** | `template/user-guard/hooks/status_guard.py` | 拦截未经批准的项目外写删操作，阻止对未发生字节／时间戳变化的日志文件做同区间原地重复读取 |
| **防重复刷屏钩子** | `template/user-guard/hooks/reply_repeat_guard.py` | 单回合连续三次输出无新数值进展的短句等待回复时发出收口提醒 |
| **长会话交接门禁钩子** | `template/user-guard/hooks/handoff_gate.py` | 统计压缩次数、轨迹体积与读图体积，按 `advisory`／`drain`／`freeze` 分阶段引导整理交接摘要 |
| **角色路由与验证编排器** | `template/user-guard/skills/cae-harness/scripts/cae_harness.py` | 解析 `fleet_profile.json` 角色槽位，预检凭据／配额冷却，拉起独立无头进程（含 `--clean-room` 纯净隔离），核验读写快照并执行 `--verify-cmd` |
| **演进与拍板状态机** | `template/user-guard/skills/fleet-evolve/scripts/fleet_state.py` | 在宿主机状态目录维护 `propose`／`approve`（推荐表用户拍板门禁）与 `record-pass1`／`verify-pass2`（主控重启二阶段重入锁），校验代码与清单 SHA-256 |
| **Claude 分流与安全切卡** | `template/claude/router/` | `router.py` 按模型前缀将主会话与子代理透明分流至兼容 Anthropic Messages 的不同官方上游；`ccswitch_admin.py` 在线备份 SQLite 并安全切换供应商卡片 |

---

## 二、四个客户端的挂载机制与无头隔离设计

`install.py` 写入前逐级校验目标路径不含外部符号链接或 Windows 目录联接（Reparse Point），并将钩子解释器统一固化为已验证可用的 Python 3.11+ `<python> -X utf8 -I -B`。

1. **Google Antigravity（`~/.gemini/config/plugins/user-guard`）**：挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四事件；运行期状态与日志统一落盘至 `PLUGIN_DATA`，插件目录保持只读干净。
2. **OpenAI Codex（`~/.codex`）**：同步 `~/.codex/hooks/` 并在 `hooks.json` 挂载 `SessionStart`（`^(compact|resume)$`）、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五事件；实质配置未变时不重写文件，避免重复触发客户端钩子信任弹窗。
3. **Claude Code（`~/.claude`）**：合并五个钩子至 `~/.claude/settings.json`（缺失 `effortLevel` 时补默认 `"xhigh"`）。常规无头模式调用 `claude -p` 时保留全局规则、激活卡片与钩子；**只读无头任务固定附加 `--permission-mode dontAsk`**，直接配合 `--tools Read,Grep,Glob` 执行只读工具，规避 `auto` 权限模式下额外请求安全分类器引发的限流失败。
4. **DeepSeek Harness（`~/.dsh`）**：仅同步 `AGENTS.md` 与 `skills/`，绝不读写 `.credentials.yaml`。无头调用时在系统临时目录生成一次性 `--patch` YAML 激活官方凭据，注入单 JSON 输出格式强约束，并在解析层自动剥离偶发尾随的 `</｜｜DSML｜｜...>` 标记。
5. **`--clean-room` 纯净盲读隔离**：支持 `codex`、`claude`、`dsh` 的只读 `task`。脚本在系统临时目录构造仅含认证凭据的最小配置目录（`claude` 加 `--bare` 与纯环境变量 `--settings`），并在只读工作区镜像中剔除 `AGENTS.md` 与 `CLAUDE.md`，实现零历史、零规则、零钩子、零技能的新手盲读环境。

---

## 三、钩子拦截矩阵与异常兜底策略

| 钩子脚本 | 触发点 | 默认静默放行范围 | 触发提醒或拦截条件 | 动作与异常兜底 |
| :--- | :--- | :--- | :--- | :--- |
| `session_context_loader.py` | 恢复／压缩／调用前 | 未发生新压缩时返回空，零额外 token 注入 | 检出新增 `CHECKPOINT`／`compact` 或跨会话 `resume` | 注入原话归档路径与 `R9` 核对提示，绝不阻断对话 |
| `status_guard.py`（写边界） | 写文件或跑命令前 | 当前项目根目录（自动互通 Windows 盘符与 WSL `/mnt/<drive>`）、系统临时目录、会话记忆／制品目录、标准 `.cache` | 写文件、重定向、`git worktree add` 或内联脚本越出上述目录；或 Shell 包装嵌套 `> 4` 层、写路径无法静态确认 | 交互窗口弹框请用户确认（`force_ask`／`ask`）；后台无头进程（`CAE_HARNESS_DEPTH=1`）直接 `deny` |
| `status_guard.py`（日志防空读） | 读文件或只读查看前 | 首次读 `.log`／`.out`／`.err`、文件更新后重读、或更换起止行号翻页读取 | 连续第 2 次以完全相同的行号参数读取大小／时间戳／哈希均未变化的同一日志文件 | 返回 `deny` 提示等待后台通知；`> 4 MiB` 大日志仅采首尾各 `1 MiB` 计算指纹防读盘卡顿 |
| `reply_repeat_guard.py` | 单轮回复结束时 | 正常汇报、提问拍板，或含新迭代步数／新残差值的进度消息 | 同一用户回合内连续 3 次输出 `<= 300` 字且无新数值进展的同质等待短句 | 每回合最多提醒 1 次，要求立即结束本轮等待唤醒 |
| `handoff_gate.py` | 压缩后／工具前／结束时 | Codex 与 Claude 默认 `advisory` 仅文字提醒；Antigravity 未达阈值前静默 | Antigravity 达 11 次压缩进入 `advisory`，达 16 次（或轨迹 `>= 6 MiB`、读图 `>= 100 MiB`）进入 `drain`／`freeze` | `drain`／`freeze` 期禁新开子代理、`cae_harness` 与 `git push`；若已有交接状态文件损坏则保守 `deny` 防冻结失守 |

---

## 四、本地透明分流、安全切卡与工具链 ABI 预警

1. **Claude 本地分流服务（`router.py`）**：监听 `127.0.0.1`（Windows 默认 `8240`，WSL 默认 `8241`），按请求体 `model` 字段前缀将主对话与子代理透明转发至原生兼容 Anthropic Messages 协议的官方接口（默认主模型走 Kimi，子代理前缀 `deepseek` 走官方 DeepSeek 并注入 `max` 推理挡位）。内置 `64 MiB` 请求体上限（超限返 `413`）、`64` 并发信号量（超限返 `503`）、`60s` SQLite 只读密钥缓存，以及 WSL 下 `5s` TTL 的宿主代理／Fake-IP 探测缓存。
2. **CC Switch 在线备份切卡（`ccswitch_admin.py`）**：默认模型与卡片名读取自 `fleet_profile.json`；切换前拦截符号链接并在线备份 `cc-switch.db` 与 `settings.json`，仅投影模型与认证变量，完整保留五类钩子与 `effortLevel`，异常时优先回滚 `settings.json`。
3. **全局 Python 编译扩展耦合体检**：尽管钩子自身运行于 `-I` 隔离模式不受第三方包干扰，但若宿主机全局 Python 已安装 `jax`、`jaxlib`、`scipy`、`mpi4py`、`petsc4py`、`torch` 等绑定次版本 ABI（如 `cp313`）的编译包，原地跨次版本升级会导致科研环境集体报错。`install.py --audit-upgrade` 会零写入扫描 `site-packages` 并在 `host.toolchain.coupling_warnings` 中输出预警，由 `fleet-evolve` 强制拦截原地强升。

---

## 五、跨主机真实遥测审计摘要与上下文边界标定

以下数据汇总自双机（Windows + WSL2）共 `153,900` 次代理转发记录与近万份本地客户端原始会话日志（均已脱敏），解释了本仓各项硬隔离参数的设计依据：

1. **前缀缓存复用率与模型换代速度**：
   - Codex CLI（`282` 个会话，`62.5 亿`输入侧 token）前缀缓存命中率达 `98.59%～98.75%`；Claude Code（`9,117` 份轨迹，`968.8 亿`输入侧 token）命中率达 `95.52%～98.60%`；DSH（`153` 个会话）命中率达 `94.38%～98.70%`。高缓存命中率是重度代码审查与长链条推导得以在订阅或低成本下持续运转的前提，因此严禁在系统提示词前缀拼入动态随机时间戳破坏缓存。
   - 跨度三个半月的日志内主力模型完成整代更替，印证了必须将季节性模型名隔离在 `fleet_profile.json` 中，而不能写死进常驻规则与 Python 分支。
2. **真实故障分布与针对性代码加固**：
   - **压缩失忆高频发生**：Antigravity 共 `271` 个会话中有 `154` 个（`56.8%`）触发了自动检查点压缩（累计 `601` 次检查点，实测在 `24 万～29 万` token 区间触发后早期随机细节召回率降至 `0/6`），Claude Code 亦累计触发 `835` 次自动压缩——直接验证了 `user_words.py` + `session_context_loader.py` 原话归档恢复的必要性。
   - **`ultra` 委派风暴**：历史开启 `ultra` 挡位的会话在 `670` 轮中共触发 `1,200` 次 `spawn_agent`、`4,587` 次 `wait` 与 `665` 次 `sleep` 轮询阻塞——因此 `cae_harness.py` 与 `install.py` 硬性过滤 `ultra`，只选单模型非委派最高挡。
   - **协议转换反代与分类器限流故障**：第三方 `Responses API` 协议转换接口共引发 `26` 次 `HTTP 422 No tool output found for function call call_...` 断链与 `38` 次流式传输中断；Claude Code `auto` 权限模式安全分类器在高频调用下引发 `71` 次限流报错；此外真实限流报文包含 `hit your session/weekly limit · resets 5pm` 格式——已分别通过禁用协议转换反代、为 `claude` 只读无头固定传入 `--permission-mode dontAsk`、以及在 `cae_harness.py` 中扩展 Claude `is_error` 信封与会话／周度限额正则予以根治。
3. **单条输入与窗口安全预算标定**：
   - Codex `turn/start` 单条输入上限为 `1,048,576` 字符，而显式声明 `-c model_context_window=872000` 后分轮累积至 `80.9 万` token 同轮抽检 **6/6 命中**；Antigravity CLI 单条超 `44 万～66 万` 字节会出现入口截断。因此超长源码审查一律走目录内按文件读取与 `cae-harness audit` 自动分片预算（扣除有效窗口比与 `10%` 输出预留）。
