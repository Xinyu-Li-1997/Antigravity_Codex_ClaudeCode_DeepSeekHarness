# `user-guard`、`cae-harness` 与 `fleet-evolve` 底层架构、生命周期钩子及安全规范

本文是本仓库底层工程实现的**唯一技术真源（SSOT）**，集中记录全仓代码模块分区、四端宿主事件挂载协议、AST／Shell 静态边界守卫、本地混合分流路由、SQLite 原子切卡事务、全栈工具链耦合体检以及受控实机标定数据。日常科研与求解器协作流见[仓库总 README](../README.md)与[协作实践指南](workflow-guide.md)。

---

## 一、全仓核心代码分区（仅依赖 Python 3.11+ 标准库）

运行 `install.py` 后，四端配置各自落盘至宿主机全局根目录（`~/.gemini/config/plugins/user-guard`、`~/.codex`、`~/.claude`、`~/.dsh`），钩子并非后台常驻进程，而是宿主在特定生命周期节点通过 `stdin/stdout` JSON 同步调用的毫秒级标准库脚本：

| 模块分组 | 源码路径 | 核心职责与安全不变量 |
| --- | --- | --- |
| **安装与全栈体检器** | `install.py`、`install.ps1`、`install.sh` | 软链接／目录联接（Reparse Point）越界防护、四端目标目录互不包含校验、基于 `tomllib` 的保留注释 TOML 更新、`_rules_equal` 规则正文精确对齐（完整检测 `R0–R11` 漂移同时保留用户自定义 `## 工具与...` 尾段）、Codex `hooks.json` 规范化无损比对、以及零写入 `--audit-upgrade`（含四端字节漂移、CC Switch 退役审计与 `_audit_host_toolchain` 三级工具链 ABI 耦合诊断） |
| **五类共用生命周期钩子** | `template/user-guard/hooks/*.py` | `user_words.py`（UTF-8 流重配置、重解析点预检、原子写盘、跨平台文件锁、三端轨迹原话提取）、`session_context_loader.py`（检查点／恢复原话注入）、`status_guard.py`（`R4` 目录硬边界 AST／Shell 静态守卫与算例日志复合指纹防空转）、`reply_repeat_guard.py`（数值进展放行与空等刷屏阻断）、`handoff_gate.py`（长会话 `normal->advisory->drain->freeze` 四态收口与交接四要素校验） |
| **四端无头编排与物理门禁** | `template/user-guard/skills/cae-harness/scripts/cae_harness.py` | 统一预检 `codex`、`agy`、`claude`/`cc`、`dsh` 底层 LLM／登录态／本地路由就绪度；匹配专长旗舰模型、单模型纯思考最高挡与声明最大窗口；按运行侧管理 `quota_exhausted` 冷却与零改动 `--fallback-on-quota` 顺位回退；单次 `lstat` 快照校验 `--write-scope`（含 `.github` 等受管隐藏目录）；Windows PowerShell `finally` 无条件进程树清理；WSL 跨子系统 `.cmd`/`.bat` `%`->`%%` 防环境变量注入转义；纯 `probe` 与 `--dry-run` 严格零写盘 |
| **自举演进二阶段状态锁** | `template/user-guard/skills/fleet-evolve/scripts/fleet_state.py` | 独立存放于宿主机本地状态目录（零污染 Git 工作区）；采用 `msvcrt`/`fcntl` 跨平台文件锁保护并发读写，绑定 `execution_side`、`repo_root`、`repo_commit`、`plugin_version`、`skill_sha256`、`core_sha256` 与 `24h` TTL，并在 `verify-pass2` 中原子写入 `claimed_by_session_id` 阻断同会话死循环与多窗口重入竞态；损坏 JSON 自动暴露 `corrupt_state` |
| **Claude 本地分流路由与切卡** | `template/claude/router/` | `router.py`（多线程本地 HTTP 分流：主请求走 Kimi `k3.1-pro[1m]`、子代理 `deepseek*` 走 DeepSeek 并强制 `max` 挡；内置 `64 MiB` 请求体上限返回 `413`、`BoundedSemaphore(64)` 并发上限返回 `503`、`60s` 套接字超时、SQLite 只读 URI 60s 密钥缓存及 WSL 5s 宿主代理／Fake-IP 跟随；原生 Linux 无 `powershell.exe` 时零子进程快速短路）；`ccswitch_admin.py`（符号链接写盘拦截、SQLite 在线原子备份、归档先于 `commit` 失败自动还原、切卡 `rollback` 前先恢复 `settings.json`、核验 `project_claude_settings` 与损坏 JSON 卡片状态）；WSLg 脚本收紧 `0700` 日志目录与当前用户进程匹配（`pgrep -u`／`pkill -u`）及 `curl --proto '=https' --tlsv1.2` |

---

## 二、四端宿主事件挂载与运行态目录隔离

1. **Google Antigravity（`template/user-guard/hooks.json`）**：
   - 挂载 `SessionStart`、`PreInvocation`（`session_context_loader.py`）、`PreToolUse`（`status_guard.py`）与 `Stop`（`reply_repeat_guard.py` 内联调用 `handoff_gate.py`），通过 `stdin` 接收 camelCase JSON 并返回单行 JSON（协议见[反重力钩子文档](https://antigravity.google/docs/hooks)）。运行态游标（`state/`）与诊断日志（`logs/`）严格隔离在 `PLUGIN_DATA` 目录，`PLUGIN_ROOT` 保持纯只读。
2. **OpenAI Codex（`template/codex/hooks.json`）**：
   - 将钩子同步至 `~/.codex/hooks/`，在 `~/.codex/hooks.json` 挂载 `SessionStart`（`^(compact|resume)$`）、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 五类事件（协议见[Codex 钩子文档](https://learn.chatgpt.com/docs/hooks)）。`install.py` 以规范化结构比对托管钩子，实质未变时不重写文件，保护客户端信任哈希。
3. **Anthropic Claude Code（`template/claude/hooks.json`）**：
   - 将 5 个钩子同步至 `~/.claude/hooks/`（运行态存于 `hooks/state/` 与 `hooks/logs/`），无损合并进 `~/.claude/settings.json` 顶层 `hooks` 键（自动清理旧版拆分式 `"args"` 条目并补齐 `effortLevel: "xhigh"`）。`cae-harness` 启动 `claude -p` 时刻意**不传 `--bare`**，使其在 Windows 与 WSL2 双侧完整继承 `~/.claude/CLAUDE.md`、激活的 CC Switch 卡片或本地 `router.py` 分流端点、只读子代理及五类钩子。
4. **DeepSeek Harness（`DSH` 临时补丁协议）**：
   - `install.py` 同步 `~/.dsh/AGENTS.md` 与 `~/.dsh/skills/`，绝不触碰 `.credentials.yaml`。因 `--profile headless` 默认禁用凭据插件，`cae-harness` 在系统临时目录生成一次性 `--patch` YAML 覆盖层，按实测就绪的 `deepseek-official` 或 `deepseek-account` 启用对应凭据插件并注入模型（`deepseek-v4-pro`／`deepseek-flash`）与 `reasoningEffort`，解码时自动剥离尾随泄漏的 `</｜｜DSML｜｜...>` 停止标签，结束后立即清理临时文件。

> **解释器统一重写**：`install.py` 自动探测合规 Python 3.11+，将全部钩子命令重写为 `<python> -X utf8 -I -B ...`；未检出合规解释器时直接报错中止，绝不落盘失效钩子。

---

## 三、关键确定性守卫与工具链体检机制细节

### 1. 增量原话回溯与重解析点防御（`user_words.py` + `session_context_loader.py`）
- **反重力增量游标**：每次 `PreInvocation` 从上次 `scannedByteOffset` 增量扫描 `transcript.jsonl`（$O(\Delta\text{bytes})$），遇 `truncated_fields` 精确回查 `transcript_full.jsonl`；未发生新 `CHECKPOINT` 时直接返回 `{"injectSteps": []}` 零开销。
- **Codex 与 Claude Code 轨迹提纯**：Codex 跨 `sessions/` 与 `archived_sessions/` 合并同一逻辑会话分段并写入 `user-guard-codex-v2` SHA-256 校验清单；Claude Code 过滤 `isSidechain`、`isMeta` 与非 `AskUserQuestion` 的普通 `tool_result`。所有状态文件打开与原子替换前均执行 `_is_reparse_or_link` 校验，阻断符号链接／目录联接劫持。

### 2. `R4` 目录硬边界静态分析与日志防空转（`status_guard.py`）
- **合法放行白名单**：当前项目根目录（自动等价映射 Windows `<盘符>:/...` 与 WSL `/mnt/<盘符>/...`）、经真实物理路径核验的系统标准临时目录（`%TEMP%`、`$TMPDIR`、以及固定通过 `/usr/bin/python3 -B -I -c` 防 `PATH` 劫持核验的 WSL `/tmp`）、宿主会话记忆／轨迹／制品目录、标准 `.cache`/`$XDG_CACHE_HOME` 缓存区，以及显式指向临时沙箱的 `install.py` 回归命令。
- **Shell 与内联 Python AST 深度静态解包**：
  - 递归包装深度超过 `4` 层直接失败闭合拦截；剥离 `if`/`elif`/`while`/`until` 条件头与子壳分组 `( ... )`；精确解析 `env`（含 `-C` 切换目录）、`sudo`、`command`（跳过 `-v`/`-V` 纯查询）、`timeout` 参数；跨子句串联追踪 `cd`/`pushd` 当前工作目录变化；识别紧邻无空格重定向（如 `echo x>/outside/f`、`>|`、`<>`、`&>`）与外部 `git worktree add`。
  - 识别 `python`/`python3`/`py` 及其组合短选项（如 `-Bc`、`-Ic`）内联代码与 `run_workflow` 脚本，按求值顺序追踪海象表达式、容器解包、分支合并、`pathlib.Path()` 无参当前目录、`.resolve()`/`os.path.abspath()` 以及 CAE 步号整数格式化（如 `f"field_{step:04d}.vtk"`）；交互会话返回请示（`ask`），无头子任务（`CAE_HARNESS_DEPTH=1`）直接拒绝（`deny`）。
- **算例日志复合指纹防空转**：仅对 `.log`/`.out`/`.err`/`.output` 及宿主任务日志在相同读取范围且内容未变时阻断原地轮询，大文件采用头尾分块哈希避免 I/O 阻塞：
$$\text{Fingerprint} = \begin{cases} (\text{mtime\_ns},\ \text{size},\ \text{SHA256}_{\text{full}}), & \text{size} \le 4\ \text{MiB} \\ (\text{mtime\_ns},\ \text{size},\ \text{SHA256}_{\text{head 1MiB} \parallel \text{tail 1MiB}}), & \text{size} > 4\ \text{MiB} \end{cases}$$

### 3. 防刷屏守卫与长会话收口状态机（`reply_repeat_guard.py` + `handoff_gate.py`）
- 短回复剥离时间戳后提取数值特征：只要迭代步数、残差量级或物理时间推进即正常放行，连续 3 次同质空等才提示静默等待后台唤醒。
- 反重力四态收口机（`normal -> advisory -> drain -> freeze`）在进入 `freeze` 时统一记录 `enteredFreezeAtStep` 并刷新冻结提示，禁止新开子代理、`cae_harness.py` 与 `git push`；Codex 与 Claude Code 默认运行 `advisory` 提醒模式（可选 `enforce`）。

### 4. 全栈工具链三级耦合体检（`install.py --audit-upgrade` `host.toolchain`）
`audit_upgrade_status` 零写入扫描当前 Python 运行时的次版本、是否处于 `venv`/`conda` 隔离环境、`uv` 可用性、`git` 版本以及全局 `site-packages` 中已安装的编译型科学计算扩展包（`jax`、`jaxlib`、`jaxopt`、`scipy`、`matplotlib`、`torch`、`cupy`、`mpi4py`、`petsc4py`、`h5py`、`numba`、`vtk`、`gmsh`、`fenics-dolfinx`）：
- 一旦检出宿主机全局非隔离 Python 直接挂载上述 ABI 敏感编译扩展且同时承担全局生命周期钩子解释器，立即在 `host.toolchain.coupling_warnings` 中生成显式耦合告警，指导 `fleet-evolve` 拦截跨次版本原地覆盖（如 `3.13 -> 3.14`）并推荐 `uv`／独立 `venv` 隔离方案。

---

## 四、受控实机验收与上下文压力标定数据

以下定量数据均在本机与独立临时沙箱中经真实宿主受控实测取得（完全脱敏）：

1. **四端生命周期钩子与无头 CLI 往返实测**：
   - **Codex CLI（Windows）**：`advisory` 五类事件真实触发且跨进程 `resume` 不重复累加压缩计数；`enforce` 准确拦截 `drain` 期 `git push` 与 `freeze` 期源码写改；无头 `codex:sol` 审查与门禁 `returncode=0`。
   - **Antigravity CLI（Windows 与 WSL `Ubuntu-24.04`）**：双平台四类钩子原生触发；WSL 原生 Linux 链路（`side=posix`、`windows_interop=false`）下 `Hydrogen (High)` 写入任务以 `49.64s` 一次通过三单元周期通量差 9 组精确分数守恒验收，`Orchestrator (Carbon)` 在 `--timeout 900` 下以 `342.07s` 通过同一验收。
   - **Claude Code CLI（Windows 与 WSL `Ubuntu-24.04`）**：Windows 侧不加 `--bare` 继承 CC Switch 卡片环境（`deepseek-flash[1M]`，`1,048,576` 配置窗口）驱动 `claude -p --output-format json --json-schema ... --tools Read,Grep,Glob` 一次返回 `returncode=0`、`changed_files=[]`；WSL 侧在宿主 TUN Fake-IP（`198.18.0.0/15`）环境下经本地 `router.py`（`127.0.0.1:8241` 自动跟随宿主代理）驱动原生 `claude` 一次返回 `returncode=0`，且预检对未挂代理的 WSL 直连 Fake-IP 卡片实现毫秒级拦截提示。
   - **DeepSeek Harness CLI（Windows 原生与 WSL 互操作）**：裸装仅含 `deepseek-account` 官方登录态下，`probe`／`assess` 零写入检出双模型 `deepseek-v4-pro`／`deepseek-flash`（`1,048,576` 窗口）；双侧运行 `dsh:default` 只读审查均动态注入临时 `--patch` YAML 并精确提取末尾 `type="final"` 回执（`returncode=0`、`changed_files=[]`、`~/.dsh` 零改动）。
2. **上下文窗口容量与自动检查点压缩边界压力标定**（合成随机口令语料，禁止调用工具或读盘，核对首／中／尾 6 处随机值）：

| 测试对象与场景 | 实测输入规模与宿主行为 | 命中率与工程设计结论 |
| --- | --- | --- |
| Codex `GPT-6.1-Sol`（分轮累积，同轮提取） | 单次请求输入达 `808,892` token（占宿主 `828,400` 有效窗口 **97.65%**），未触发压缩 | **6/6 精确命中**，证实 `872,000` 声明最大窗口下的超长同轮检索真实可用 |
| Codex 单条 RPC 载荷上限探针 | 单条 `3,444,360` 字符被 `turn/start` 拒绝，服务端返回单条上限 `1,048,576` 字符 | 超长源码或算例日志须让模型在工作目录内按文件读取或分轮送入 |
| 反重力 CLI `Hydrogen`（单条超大输入探针） | 单条 44～66 万字节消息出现入口截断标记 | CLI 无头单条提示词不宜过长，大体积材料应走文件读取 |
| 反重力 CLI `Hydrogen`（分块累积与检查点标定） | 每轮约 13 万字节无截断：累积至 `241,625` token 时未压缩，**6/6 精确命中**；继续追加至 `290,028` token 触发自动检查点压缩，输入降至 `14,063` token | 自动检查点压缩后早期散落细节无法仅凭摘要还原（0/6），直接证实了**检查点后从磁盘恢复用户原话**与**将大体积源码分流至独立窗口子代理／大窗口无头后端**的必要性 |
