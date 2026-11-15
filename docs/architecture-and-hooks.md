# 底层架构、生命周期钩子与实测数据说明

本文面向希望了解内部实现、排查钩子拦截原因或二次维护本仓库的工程师，说明全仓代码分工、四个客户端的配置文件挂载细节、每个钩子的触发条件与失败处理策略、本地分流服务参数，以及本机实测数据。日常安装与求解器协作用法见 [仓库总 README](../README.md) 与 [求解器研发实践指南](workflow-guide.md)。

---

## 一、核心代码模块职责一览

全仓脚本仅使用 Python 3.11+ 标准库，钩子不是常驻后台服务，而是客户端在特定事件发生时通过标准输入输出（`stdin` / `stdout` JSON）单次调用的短脚本：

| 模块 | 源码路径 | 一句话职责 |
| :--- | :--- | :--- |
| **安装与环境体检脚本** | `install.py`、`install.ps1`、`install.sh` | 检查本机客户端与 Python 3.11+ 解释器，将规则、钩子、子代理和技能写入四个客户端的全局目录，并提供只读对齐与依赖耦合检查（`--audit-upgrade`） |
| **原话提取与文件锁公共库** | `template/user-guard/hooks/user_words.py` | 从 Antigravity、Codex 与 Claude Code 本地会话记录中提取用户历轮原话，提供跨平台文件锁与符号链接防护 |
| **压缩与恢复注入钩子** | `template/user-guard/hooks/session_context_loader.py` | 在发生上下文压缩或会话恢复时，将保存的用户原话路径与提醒注入当前轮次 |
| **目录边界与日志轮询钩子** | `template/user-guard/hooks/status_guard.py` | 在调用写文件或命令行工具前，拦截未经授权的项目外写入，并阻止对未变化日志文件做原地重复读取 |
| **防重复空等钩子** | `template/user-guard/hooks/reply_repeat_guard.py` | 在模型结束单轮回复时，拦截连续三次无新数值进展的“仍在运行请稍候”式刷屏回复 |
| **长对话交接管理钩子** | `template/user-guard/hooks/handoff_gate.py` | 跟踪长对话压缩次数与读图体积，按阶段提醒或引导整理交接信息 |
| **四端后台调用与验证脚本** | `template/user-guard/skills/cae-harness/scripts/cae_harness.py` | 检查四个客户端登录与模型就绪状态，启动独立无头子进程（支持 `--clean-room` 纯净模式），核对文件改动范围并运行 `--verify-cmd` |
| **升级状态锁脚本** | `template/user-guard/skills/fleet-evolve/scripts/fleet_state.py` | 在需要手动重启主控客户端的两步升级流程中记录当前进度，防止重启后重复执行第一轮调研 |
| **Claude 本地分流与切卡工具** | `template/claude/router/` | `router.py` 按主对话与子代理将请求分流至不同模型 API；`ccswitch_admin.py` 在线备份数据库并切换 CC Switch 供应商配置卡片 |

---

## 二、四个客户端的挂载位置与无头隔离机制

`install.py` 安装时会先验证目标路径不包含指向外部目录的符号链接或 Windows 目录联接（Reparse Point），再探测可用的 Python 3.11+ 解释器，把钩子命令统一写成 `<python> -X utf8 -I -B ...`（若本机找不到 Python 3.11+ 则直接报错退出，不会写入无法运行的钩子）。

1. **Google Antigravity（`~/.gemini/config/plugins/user-guard`）**：
   - 挂载 `SessionStart`、`PreInvocation`、`PreToolUse`、`Stop` 四个事件（协议参考 [Antigravity Hooks 文档](https://antigravity.google/docs/hooks)）。
   - 运行过程中产生的原话归档（`state/`）与日志（`logs/`）全部写入客户端提供的 `PLUGIN_DATA` 目录，插件安装目录自身保持只读。
2. **OpenAI Codex（`~/.codex`）**：
   - 将钩子脚本复制到 `~/.codex/hooks/`，在 `~/.codex/hooks.json` 挂载 `SessionStart`（匹配事件 JSON 中 `source` 字段满足正则 `^(compact|resume)$` 的压缩与恢复场景）、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五个事件（协议参考 [Codex Hooks 文档](https://learn.chatgpt.com/docs/hooks)）。
   - 重复运行 `install.py` 时，如果托管钩子配置实质内容未变，脚本不会重写 `hooks.json`，以免变动文件哈希导致 Codex 重新弹出钩子信任确认。
3. **Anthropic Claude Code（`~/.claude`）**：
   - 将五个钩子复制到 `~/.claude/hooks/`，合并进 `~/.claude/settings.json` 的顶层 `hooks` 字段（自动升级旧版拆分式 `"args"` 写法；仅当原文件尚未设置 `effortLevel` 时补上默认 `"xhigh"`，除非用户显式传入 `--sync-model-defaults`，否则不会覆盖你原有的 `effortLevel`）。
   - 常规模式下，`cae-harness` 调用 `claude -p` 时**不加 `--bare`**，以便完整沿用 `~/.claude/CLAUDE.md`、当前激活的 CC Switch 卡片、本地 `router.py` 分流服务和五个钩子。
4. **DeepSeek Harness（`~/.dsh`）**：
   - `install.py` 只更新 `~/.dsh/AGENTS.md` 与 `~/.dsh/skills/`，不读取也不修改 `.credentials.yaml`（目前四个客户端里只有 Antigravity、Codex、Claude Code 三端开放了本地生命周期钩子接口，`DSH` 尚无客户端钩子机制）。
   - 因为 `dsh --profile headless` 默认关闭凭据插件，`cae-harness` 后台调用它时会在系统临时目录生成一份一次性 `--patch` YAML 配置文件，启用已就绪的 `deepseek-official`（读取 `DEEPSEEK_API_KEY`）或 `deepseek-account` 官方登录态并填入模型与推理强度，读取结果时自动剔除偶发尾随的 `</｜｜DSML｜｜...>` 停止标记，任务结束后立即删除临时文件。
5. **无头调用常规模式 vs. `--clean-room` 纯净盲读模式**：
   - 常规无头调用虽然没有历史聊天记录，但仍会加载上述全局规则、钩子、技能与项目根目录下的 `AGENTS.md`。
   - 当传入 `cae_harness.py task --clean-room`（支持 `codex`、`claude`、`dsh` 的只读任务）时，脚本会在临时目录构造仅含登录凭据或环境变量的干净配置目录（`codex` 只复制 `auth.json` 与 `models_cache.json`；`claude` 启用 `--bare` 并传入仅含 `env` 的最小 `--settings`；`dsh` 只复制 `.credentials.yaml`），同时在只读工作区镜像中排除 `AGENTS.md` 与 `CLAUDE.md`，实现真正的零规则、零钩子、零技能纯净环境。

---

## 三、钩子拦截行为一览、异常兜底策略与关闭方法

### 1. 哪些操作会被放行或拦截

| 钩子脚本 | 触发时机 | 默认放行什么 | 什么情况下会提醒或拦截 | 拦截后的表现 |
| :--- | :--- | :--- | :--- | :--- |
| `session_context_loader.py` | 会话恢复、压缩后或每轮调用前 | 未发生新压缩时直接返回空结果，不注入额外内容 | 检测到新的上下文压缩（`CHECKPOINT` / `compact`）或跨会话恢复（`resume`） | 自动注入已保存的用户原话文件路径与核对提示，从不阻断会话 |
| `status_guard.py`（目录边界） | 写文件或执行命令前（`PreToolUse`） | 当前项目根目录（自动互通 Windows `D:/...` 与 WSL `/mnt/d/...`）、系统临时目录（`%TEMP%`、`$TMPDIR`、`/tmp`）、客户端会话记忆与制品目录、标准 `.cache` 目录 | 写文件、Shell 重定向、`git worktree add` 或内联 Python / `run_workflow` 脚本试图创建、修改、删除上述范围之外的路径；或命令嵌套超过 `4` 层、目标写路径无法静态确认 | **交互会话**弹出确认框（Antigravity 返回 `force_ask`，Claude 返回 `ask`），由你决定是否批准；**后台无头子任务**（`CAE_HARNESS_DEPTH=1`）直接拒绝（`deny`） |
| `status_guard.py`（日志防空读） | 读取文件或运行只读查看命令前（`PreToolUse`） | 正常阅读任何源码；首次读取 `.log` / `.out` / `.err` 日志、算例写出新内容后再次读取、或改换读取起止行号与查询参数翻页查看 | 连续**第 2 次**用完全相同的行号区间（`StartLine` / `EndLine` / `ByteOffset`）或完全相同的命令参数去读同一个未发生任何变化的日志文件 | 返回拒绝提示，请模型等待后台任务通知或改读其他内容；一旦日志文件大小、修改时间或内容哈希变化立即自动放行 |
| `reply_repeat_guard.py` | 单轮回复结束时（`Stop`） | 所有正常技术汇报、向用户提问拍板的回复，以及包含新迭代步数、新残差值或新物理时间的进度回复 | 同一用户回合内，连续 **3 次**输出长度不超过 300 字、不含新数值进展的同质等待短句（如反复说“仍在后台运行，请稍候”） | 每回合最多提醒 1 次，要求简明结束本轮等待后台唤醒 |
| `handoff_gate.py` | 压缩后、工具调用前与回复结束时 | Codex 与 Claude Code 默认处于 `advisory` 模式，仅做非阻断文字提醒；Antigravity 在压缩未超阈值前完全静默 | Antigravity 累积压缩达 11 次进入 `advisory` 提醒，达 16 次（或轨迹超 `6 MiB`、读图超 `100 MiB`）依次进入 `drain`（允许最多 4 轮调用 / 8 次收尾操作）和 `freeze` | `drain` / `freeze` 期间禁止新开子代理、`cae_harness.py` 与 `git push`，引导写清：**① 已验证进展、② Git 状态、③ 待办事项、④ 新对话提示词**（你在对话里说“暂不交接 / 继续当前对话”即可延后） |

> **为什么只有 Antigravity 默认启用四阶段交接控制，而 Codex / Claude 默认用 `advisory` 提醒？** 因为 Antigravity 桌面与 CLI 在约二十多万 token 时就会触发自动检查点压缩，且提供了精确的检查点与读图遥测；而 Codex（`872k`）与 Claude Code（`1M`）单会话窗口更大，默认只发提醒更符合终端使用习惯。若希望在终端也强制拦截，安装时传 `--codex-handoff-mode enforce` 或 `--claude-handoff-mode enforce` 即可。

### 2. 钩子自身出错时会不会卡死会话？

日常对话不会被未预期异常卡死，钩子按场景区分处理：
- **分析目标命令时的保守原则**：在 `status_guard.py` 正常解析命令期间，如果遇到超过 `4` 层嵌套包装（如多层 `bash -c` 套 `python -c`）或无法静态确定写入位置的参数解包，按“不确定即询问”处理（交互窗口弹确认框 `force_ask` / `ask`，后台无头任务拒绝 `deny`）。
- **钩子未预期异常的默认放行与两条安全例外**：钩子最外层 `main()` 捕获到未预期异常时默认放行或仅附带提示（Antigravity 将堆栈写入 `logs/lifecycle.log` 并返回 `{"decision": "allow"}` 或 `{"injectSteps": []}`；Claude 与 Codex 输出到 `stderr` 并返回 `{}` 或异常提示文本），但保留两处显式安全例外：① 若当前会话已生成交接状态文件（`<conversation_id>_handoff_gate.json`）而读取校验时发现该文件损坏，返回 `deny` 防止交接冻结期失守；② `session_context_loader.py` 在 Codex 或 Claude 下收到非合法 JSON 标准输入时以退出码 `2` 报错。
- **大日志文件哈希保护**：检查日志文件是否变化时，`4 MiB` 以内的日志计算全文 SHA-256；超过 `4 MiB` 的大算例日志只取文件开头 `1 MiB` 和末尾 `1 MiB` 连同文件大小、纳秒级修改时间一起计算指纹，避免读几百兆残差日志时卡顿磁盘。

### 3. 排查日志位置与临时关闭方法

- **查看钩子诊断日志**：Antigravity 位于 `~/.gemini/antigravity/plugin_data/user-guard/logs/`；Codex 位于 `~/.codex/hooks/logs/`；Claude Code 位于 `~/.claude/hooks/logs/`。
- **临时关闭或卸载**：
  - 交互会话中遇到 `status_guard.py` 拦截合理的项目外写入时，直接在客户端弹窗点击允许即可单次放行；
  - 关闭 Codex 或 Claude Code 的长对话提醒：`python -B install.py --app codex --codex-handoff-mode off`（或 `--app claude --claude-handoff-mode off`）；
  - 在 Antigravity 界面插件管理器中可随时一键禁用 `user-guard` 插件。

---

## 四、本地混合分流路由、CC Switch 切卡与工具链耦合检查

### 1. Claude Code 本地分流服务（`template/claude/router/router.py`）
- **它做什么**：在 `127.0.0.1`（Windows 默认端口 `8240`，WSL 默认端口 `8241`）启动一个纯标准库多线程 HTTP 服务，把 Claude Code 主会话请求转发给 Kimi（`k3.1-pro[1m]`），把子代理（模型名以 `deepseek` 开头）转发给官方 DeepSeek API 并自动加上 `max` 推理强度。
- **关键保护参数与设置原因**：
  - 单次请求体上限 `64 MiB`（超出返回 HTTP `413`）：防止误把整个网格文件或大型 VTK 流场塞进请求导致内存暴涨；
  - 最大并发请求数 `64`（`BoundedSemaphore(64)`，超出返回 HTTP `503`）与空闲连接超时 `60 秒`：防止异常循环打满本地端口；
  - SQLite 只读 URI `60 秒` 密钥缓存：减少每次转发重复读盘；
  - WSL 环境在转发请求时按需探测 Windows 宿主代理与 TUN Fake-IP（`198.18.0.0/15`）网段，探测结果在内存中缓存 `5 秒`（`powershell.exe` 查询超时 `8 秒`；纯原生 Linux 且无 `powershell.exe` 时直接跳过探测，不额外起子进程）。

### 2. CC Switch 安全切卡（`template/claude/router/ccswitch_admin.py`）
- 切换供应商卡片前，脚本先拒绝任何符号链接目标，再通过 SQLite 在线备份接口备份 `cc-switch.db` 与 `~/.claude/settings.json`；
- 切卡时只替换模型名称与连接环境变量，保留 `settings.json` 中的五个生命周期钩子和 `effortLevel`；若数据库提交失败，优先还原 `settings.json` 再回滚事务。

### 3. 为什么钩子已经用了 `-I` 隔离模式，还要做全局 Python 依赖耦合检查？
- 钩子启动命令带有 `python -I`（隔离模式，不加载用户 `site-packages`），所以你装任何第三方包都不会把钩子搞崩。
- **但反过来有风险**：如果宿主机全局 Python 里已经装了 `jax`、`jaxlib`、`scipy`、`mpi4py`、`petsc4py`、`torch`、`h5py` 等带 C/C++/Fortran 编译扩展的科学计算库（与具体次版本如 `cp313` 绑定），一旦在“全套升级”时把全局 Python 从 `3.13` 原地替换成 `3.14`，这些编译扩展会集体失效。
- 因此 `install.py --audit-upgrade` 会在**不改任何文件**的前提下扫描全局 `site-packages`，一旦发现这类编译扩展，就在 `host.toolchain.coupling_warnings` 中输出警告，供 [`fleet-evolve`](../template/user-guard/skills/fleet-evolve/SKILL.md) 第 2 阶段识别并阻止跨次版本原地升级。

---

## 五、本机实测记录与上下文窗口测试数据

### 1. 三个“上下文窗口”数字分别是什么意思

阅读模型参数或 `cae_harness.py probe` 输出时，会看到三个不同口径的窗口数值：
- **模型声明最大窗口**：模型目录或内置契约标明支持的最大上下文长度（如 Codex `GPT-6.1-Sol` / `Astra` 为 `872,000` token，Gemini 4 与 DeepSeek 为 `1,048,576` token）；`cae-harness` 调用 Codex 时会显式传入 `-c model_context_window=872000`。
- **客户端配置窗口**：用户在客户端配置文件或 CC Switch 卡片中填写的窗口上限（如 `1,000,000` 或 `1,048,576` token）。
- **有效可用窗口与安全输入预算**：扣除模型有效上下文比例（如 Codex 模型缓存声明有效比 `95%`，即 `828,400` token）及输出预留（默认预留 `10%`）后，`cae-harness audit` 用于计算单批最多送审多少源码的预算值。

### 2. 四端真机无头调用与钩子实测记录（已去除个人路径与硬件信息）

- **Codex CLI（Windows）**：`advisory` 模式下五类钩子正常触发，跨进程 `resume` 不重复累加压缩次数；`enforce` 模式下准确拦住 `drain` 阶段的 `git push` 与 `freeze` 阶段的源码修改；后台 `codex:sol` 只读审查（含 `--clean-room` 模式）与 `gate` 验证返回码均为 `0`。
- **Antigravity CLI（Windows 与 WSL `Ubuntu-24.04`）**：双侧四类钩子正常触发；在三单元一维周期网格通量差离散守恒测试（检验 9 组有理数通量组合下右端项之和精确为零）中，WSL 原生 Linux 环境下的 `Hydrogen (High)` 约 `50 秒` 一次通过，`Orchestrator (Carbon)` 在放宽超时后约 `5 分 40 秒` 通过。
- **Claude Code CLI（Windows 与 WSL `Ubuntu-24.04`）**：Windows 侧常规模式与 `--clean-room` 模式调用 `claude -p` 均一次返回 `returncode=0`、`changed_files=[]`；WSL 侧在宿主开启 TUN Fake-IP 环境下通过本地 `router.py`（`127.0.0.1:8241`）成功完成无头审查，且预检逻辑可在启动前直接提示未配置代理的 Fake-IP 直连卡片。
- **DeepSeek Harness CLI（Windows 原生与 WSL 跨系统调用）**：在仅配置官方登录凭据的环境下，`probe` 与 `assess` 零写入识别出 `deepseek-v4-pro` 与 `deepseek-flash`；常规模式与 `--clean-room` 模式下的只读审查均正常返回结构化 JSON（`returncode=0`、`changed_files=[]`、`~/.dsh` 原文件零改动）。

### 3. 上下文长度与自动压缩边界测试记录

以下测试使用合成随机口令文本（禁止模型调用工具或读盘，在文本首、中、尾共埋入 6 处随机口令并在同一轮末尾抽检）：

| 测试对象与方式 | 实测输入规模与客户端表现 | 本次抽检结果与工程启示 |
| :--- | :--- | :--- |
| Codex `GPT-6.1-Sol`（分轮追加，同轮抽检） | 单次请求累积至约 `80.9 万` token（占 `828,400` 有效窗口约 `97.7%`），未触发自动压缩 | 本次 6 处随机口令 **6/6 命中**，说明在显式传入 `872,000` 窗口参数后，分轮送入长文本做同轮核对是可行的 |
| Codex 单条消息长度上限测试 | 单条传入约 `344 万` 字符时被 `turn/start` 接口拒绝，服务端返回单条上限为 `1,048,576` 字符 | 超长源码或大算例日志不能硬塞进单条提示词，应让子进程在目录内按文件读取 |
| Antigravity CLI `Hydrogen`（单条超长输入测试） | 单条消息达到 `44 万～66 万` 字节时出现入口截断标记 | CLI 单条提示词不宜过长，大体积材料应改走文件读取 |
| Antigravity CLI `Hydrogen`（分轮累积与压缩测试） | 每轮追加约 `13 万` 字节：累积至约 `24.2 万` token 时未压缩，**6/6 命中**；继续追加至约 `29.0 万` token 时触发自动检查点压缩，上下文被压缩至约 `1.4 万` token | 触发自动压缩后，早期散落在正文里的随机细节仅凭压缩摘要已无法还原（0/6），说明**压缩后从磁盘恢复用户原话**以及**把大体积源码分流给独立子代理或大窗口后台进程**确有必要 |
