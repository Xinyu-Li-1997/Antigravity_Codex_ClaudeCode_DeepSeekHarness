# `user-guard` 与 `cae-harness` 架构设计、生命周期钩子及四端编排规范

本文面向需要理解底层工程实现、维护代码或排查边界行为的开发者，将“生命周期钩子（Lifecycle Hooks）”“四端对等无头编排”与“本地模型分流路由”还原为直白的确定性机制，并完整记录全仓模块分区、宿主事件协议、状态机算法及受控实机验收数据。日常上手与协作流见[仓库总 README](../README.md)和[协作实践指南](workflow-guide.md)。

---

## 一、什么是宿主机全局落盘与生命周期钩子？全仓代码结构长什么样？

1. **上游台架与宿主机全局落盘分离**：本仓库（`Antigravity_Codex_ClaudeCode_DeepSeekHarness`）是上游模板与门禁台架源码库；运行 `install.py` 后，Google Antigravity（反重力）、OpenAI Codex、Anthropic Claude Code 与 DeepSeek Harness（`DSH`）的配置各自独立写入宿主机全局根目录（`~/.gemini/config/plugins/user-guard`、`~/.codex`、`~/.claude`、`~/.dsh`），日常业务仓库内无需携带任何本仓文件。
2. **确定性生命周期钩子**：所谓“生命周期钩子”并非后台常驻黑盒，而是宿主客户端在“启动／恢复会话、发包前、调用读写或命令工具前、结束当前回复前”几个固定节点自动同步调用的一组本地 Python 标准库脚本。宿主通过标准输入（`stdin`）传入事件 JSON，脚本在数毫秒内向标准输出（`stdout`）返回放行、弹窗请示、拒绝执行或补充上下文提示的 JSON。

全仓核心代码按自顶向下划分为四大模块，仅依赖 Python 3.11+ 标准库：

### 1. 跨平台安装与全栈升级体检器 `install.py`

| 职责模块 | 核心能力 |
| --- | --- |
| 常量、路径安全与无损配置处理 | 软链接／目录联接越界防护、各客户端目录互不包含校验、基于 `tomllib` 的保留注释 TOML 编辑、JSON 原子替换写盘 |
| 四端客户端、WSL 互操作与环境探测 | 零写入探测本侧与跨子系统（Windows `<->` WSL）的反重力、Codex、Claude Code、DeepSeek Harness、CC Switch、Python 3.11+ 与 `zotero-mcp` |
| 反重力插件、Codex 与 DSH 配置同步 | 幂等同步 `~/.gemini/config/plugins/user-guard`、`~/.codex/` 与 `~/.dsh/`（自动保留 `AGENTS.md` 末尾自定义 `## 工具与...` 段落，同步各自 `skills/cae-harness`，绝不触碰 DSH `.credentials.yaml` 凭据文件） |
| Claude Code、本地路由与 CC Switch 协同同步 | 同步 `~/.claude/CLAUDE.md`、`agents/readonly-*.md`、`hooks/*.py`、`skills/cae-harness`、无损合并 `settings.json`（自动迁移旧版拆分式 `"args"` 钩子），可选同步 `router/` 与禁用 `cc-switch.db` 陈旧提示词 |
| 全栈升级与大模型对齐体检（`--audit-upgrade`） | 零写入核查四端版本、规则／钩子／技能字节漂移、CC Switch Schema 与激活卡片、路由健康度及大模型目录对齐状态 |

### 2. 多端共用生命周期守护脚本 `template/user-guard/hooks/*.py`（5 个模块）

| 脚本文件 | 核心职责 |
| --- | --- |
| `user_words.py` | 共享底层库：UTF-8 标准流重配置、跨平台文件锁、原子写盘、反重力 JSONL 截断回查、Codex 跨分段 `rollout` 合并与 SHA-256 校验、Claude Code 主会话轨迹原话提取（核验 `AskUserQuestion` `tool_use_id` 来源） |
| `session_context_loader.py` | 上下文压缩或续接后（反重力 `SessionStart/PreInvocation`、Codex 与 Claude Code `SessionStart` `^(compact\|resume)$`），自动回溯并注入用户历史原话与归档路径 |
| `status_guard.py` | 反重力与 Claude Code `PreToolUse` 守卫：静态解析表面文件写改工具、Python AST、动态工作流与 Shell 子句，拦截项目目录外未授权写删（合法放行四端临时沙箱安装命令），并用复合指纹阻断未变化算例日志的原地空转轮询 |
| `reply_repeat_guard.py` | 反重力与 Claude Code `Stop` 守卫：放行含迭代步数／残差推进的正常汇报，拦截纯空等刷屏（反重力侧内部联动收口终审，Claude Code 侧与 `handoff_gate.py` 并列挂载于 `Stop`） |
| `handoff_gate.py` | 长会话分级预警、局部排空收尾（`drain`）、冻结保护（`freeze`）与交接四要素结构校验（反重力四态状态机；Codex 与 Claude Code 在 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop` 对账压缩边界，默认 `advisory`、可选 `enforce`） |

### 3. 四端对等无头协作与门禁验证台架 `cae_harness.py`（七大章节）

| 章节 | 职责范围 |
| --- | --- |
| ① 全局常量、异常与回执 JSON Schema | 统一定义四端引擎标识（`codex`、`agy`、`claude`/`cc`、`dsh`）、退出码、任务类型与结构化回执契约 |
| ② 基础配置、跨平台路径与 WSL 互操作工具 | 处理 Windows `<->` WSL 路径映射、四端全局目录定位、纯 Linux 路径只读临时镜像与环境隔离 |
| ③ 模型目录、LLM 就绪预检、语义指派与执行画像 | 校验目标后端底层 LLM／路由／登录态就绪（拦截空占位符与离线路由），解析显式参数与自然语言模糊指派，匹配专长旗舰模型、最高纯思考挡与声明最大窗口 |
| ④ 额度冷却持久化、熔断门禁与顺位回退资格 | 按运行侧记录 `quota_exhausted` 冷却期，发包前预检拦截，并在工作区零改动时执行 `--fallback-on-quota` 四端顺位接管 |
| ⑤ 进程树监督、多端事件流解析与无头执行 | 统一监督 `codex exec`、`agy`、`claude -p`（无 `--bare` 完整继承 `~/.claude`）与 `dsh --profile headless --patch`（临时补丁注入模型／挡位、容忍中间工具调用恢复并提取 `final` 回执）生命周期 |
| ⑥ 工作区快照、分片审计规划与双盲归并 | 单次 `lstat` 对比任务前后文件哈希（拒绝非常规特殊文件）防止 `--write-scope` 越界写入，按安全预算编排 `single-pass` 或 `sharded` 审计 |
| ⑦ 物理门禁循环与五大 CLI 子命令入口 | 拦截空白验收命令，驱动 `gate` 多轮排错验证闭环并显式标注 `verification_executed`，暴露 `probe`、`assess`、`task`、`audit`、`gate` 五个命令 |

### 4. Claude Code 本地模型分流路由与 CC Switch 管理 `template/claude/router/`

| 脚本／配置 | 核心职责 |
| --- | --- |
| `router.py` + `router.windows.json` / `router.wsl.json` | 纯标准库多线程本地 HTTP 分流守护进程（Windows 默认 `127.0.0.1:8240`，WSL 默认 `127.0.0.1:8241`）：主会话请求转发至 Kimi Coding API（支持 `kimi_effort_map` 挡位映射），匹配 `deepseek` 前缀的子代理请求转发至 DeepSeek Anthropic 兼容端点（自动剥离 `[1m]`/`[1M]` 后缀并施加 `force_effort: "max"`），通过只读 URI 从 `cc-switch.db` 读取上游密钥（内存缓存 60 秒，磁盘零明文落盘），支持 SSE 逐块流式透传与 WSL 自动跟随宿主系统代理（缓存 5 秒） |
| `ccswitch_admin.py` | 零依赖 SQLite 管理 CLI（`status`、`prepare-cards`、`switch-card`、`disable-prompts`、`verify`）：执行在线数据库原子备份，维护直连 Kimi（`k3.1-pro[1m]`）、直连 DeepSeek（`deepseek-flash[1M]`）与本地混合分流（`k3.1-pro[1m]` + `deepseek-flash`）三张卡片，切卡时仅无损投影受管的模型／鉴权字段（完整保留 `hooks`、`effortLevel`、`permissions` 及用户自定义键） |
| `launch-cc-switch-wsl.sh` / `update-cc-switch-wsl.sh` | WSLg 下禁用 WebKitGTK DMA-BUF 防白屏启动包装器，以及带 GitHub API SHA-256 摘要强校验的 `.deb` 升级脚本 |

---

## 二、宿主事件挂载、配置继承与运行态目录隔离

### 1. 反重力侧插件协议（`template/user-guard/hooks.json`）

反重力从插件根目录的 `hooks.json` 读取同步命令配置，通过 `stdin` 传入 **camelCase** JSON，读取 `stdout` 单行 JSON 响应（协议说明见[反重力钩子文档](https://antigravity.google/docs/hooks)）。运行时自动注入 `PLUGIN_ROOT`（只读代码目录）与 `PLUGIN_DATA`（运行数据目录），所有会话游标状态（`state/`）与诊断日志（`logs/`）均只写入 `PLUGIN_DATA`：

```json
{
  "user-guard-lifecycle": {
    "SessionStart": [
      {
        "type": "command",
        "command": "python3 -X utf8 -I -B ./hooks/session_context_loader.py --event SessionStart",
        "timeout": 30
      }
    ],
    "PreInvocation": [
      {
        "type": "command",
        "command": "python3 -X utf8 -I -B ./hooks/session_context_loader.py --event PreInvocation",
        "timeout": 30
      }
    ],
    "PreToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 -X utf8 -I -B ./hooks/status_guard.py",
            "timeout": 15
          }
        ]
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "python3 -X utf8 -I -B ./hooks/reply_repeat_guard.py",
        "timeout": 15
      }
    ]
  }
}
```

### 2. Codex 侧钩子协议（`template/codex/hooks.json`）

安装器将原话恢复与交接脚本同步至 `~/.codex/hooks/`，在 `~/.codex/hooks.json` 中挂载五类生命周期事件（`SessionStart` 匹配 `^(compact|resume)$`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop`；协议说明见[Codex 钩子文档](https://learn.chatgpt.com/docs/hooks)）。二次升级时若托管钩子实质配置未变，保持原 `hooks.json` 字节完全不变，避免破坏客户端信任哈希。

### 3. Claude Code 侧钩子协议与无头配置继承（`template/claude/hooks.json`）

安装器将 5 个核心钩子同步至 `~/.claude/hooks/`（状态与日志隔离存放在 `~/.claude/hooks/state/` 与 `logs/`），无损合并写入 `~/.claude/settings.json` 的 `hooks` 顶层键（自动清理历史遗留的 `"args"` 拆分式废弃条目并保留第三方钩子与 `env` 卡片参数）：
- `SessionStart`（`^(compact|resume)$`）、`PostCompact`、`UserPromptSubmit`、`PreToolUse`（`status_guard.py --client claude` 执行 `R4` AST 守卫与算例日志防空转）与 `Stop`（`reply_repeat_guard.py` + `handoff_gate.py`）。
- **无头 CLI 完整继承**：`cae-harness` 启动 `claude -p` 时刻意不传 `--bare`，使其在 Windows 与 WSL2 双端天然继承 `~/.claude/CLAUDE.md`、`settings.json`（含 CC Switch 当前激活卡片或本地 `router.py` 分流端点）、只读子代理及生命周期门禁。

### 4. DeepSeek Harness（`DSH`）全局指令与无头临时补丁协议

DSH 采用模块化插件栈（Profile + Patch Overlay）架构，不暴露反重力／Codex／Claude Code 同构的同步命令式 `PreToolUse`／`Stop` JSON 钩子接口：
- **全局指令与技能落盘**：`install.py` 将 `R0–R11` 全局规则同步至 `~/.dsh/AGENTS.md`（由 `@deepseek-ai/dsh-agent-instructions` 插件自动加载，完整保留用户末尾自定义 `## 工具与...` 段落），并将 `cae-harness` 同步至 `~/.dsh/skills/cae-harness`（由 `@deepseek-ai/dsh-skill-filesystem` 自动发现）。
- **无头执行零污染补丁**：DSH 内置的 `--profile headless` 默认将 `credentials`、`authorization` 与 `deepseek-account` 置为 `disabled: true`。`cae-harness` 在系统临时目录动态生成一次性 `--patch` YAML 覆盖层，按预检检出的官方凭据类型（`deepseek-official` API Key 或 `deepseek-account` 官方登录态）按需启用凭据插件并注入目标模型（`deepseek-v4-pro`／`deepseek-flash`）与 `reasoningEffort`（`off`/`low`/`high`/`max`），任务结束后立即清理临时补丁，对 `~/.dsh` 下的持久化配置文件与 `.credentials.yaml` 零改动；只读审查通过 `--permission-mode read-only --approval-policy never` 与工作区文件快照双重锁定。

> **跨平台 Python 解释器自动重写**：`install.py` 部署时自动探测可用的 Python 3.11+（Windows 探测 `python.exe` 或 `py.exe -3`，WSL2／Linux 优先探测 `python3`），将钩子命令统一升级为 `-X utf8 -I -B`；未找到合规解释器则直接中止报错，绝不写入失效钩子。

---

## 三、四大核心守护机制的工程细节

### 1. 压缩与续接后的用户原话无损回溯（`session_context_loader.py` + `user_words.py`）

- **反重力增量字节游标（`<convId>_compaction.json`）**：每次 `PreInvocation` 从上次 `scannedByteOffset` 增量扫描 `transcript.jsonl`（$O(\Delta\text{bytes})$），仅在遇到 `truncated_fields` 时回查 `transcript_full.jsonl`；未压缩轮次直接返回 `{"injectSteps": []}` 零开销；检测到新 `CHECKPOINT` 或显式 `compact`/`resume` 后原子写入 `<PLUGIN_DATA>/state/<convId>_user_words.md`，在 `8,000` 字符预算内构造 `systemMessage` 提示按 `R9` 核对。
- **Codex 跨分段 Rollout 合并归档（`--client codex`）**：跨 `sessions/` 与 `archived_sessions/` 合并同一逻辑会话分段，过滤子代理轨迹与系统注入的 `# AGENTS.md instructions` 块，成对写入 Markdown 归档与 `user-guard-codex-v2` SHA-256 校验清单。
- **Claude Code 项目轨迹归档与恢复（`--client claude`）**：从 `transcript_path` 或 `<claude_home>/projects/*/<session_id>.jsonl` 提取主会话真实用户输入（过滤 `isSidechain`、`isMeta` 与非 `AskUserQuestion` 的普通 `tool_result`），原子写入 `<claude_home>/hooks/state/user-words/<session_id>.md`，并在 `SessionStart`／`PostCompact` 对账压缩边界。

### 2. 项目目录硬边界与算例日志防空转门禁（`status_guard.py`）

#### （1）项目外写删静态拦截（落实 `R4`）
审查跨宿主表面文件写改工具、命令行工具（`run_command`／`Bash`／`PowerShell`）及 `run_workflow` 动态工作流：
- **合法放行白名单**：当前项目根目录（自动等价映射 Windows `<盘符>:/...` 与 WSL `/mnt/<盘符>/...`）、经真实物理路径解析校验的系统标准临时目录（`%TEMP%`、`/tmp`）、宿主会话记忆／轨迹／制品目录及标准 `.cache` 缓存区（含带 `--target-dir`/`--codex-home`/`--claude-home`/`--dsh-home` 指向临时沙箱的 `install.py` 回归命令）。
- **Python AST、工作流预审与 Shell 子句解包分析**：使用标准库 `ast` 按求值顺序识别内联脚本与 `run_workflow` 中的写删路径及动态参数解包；剥离 `wsl`、`bash -lc`、`powershell -Command` 等包装后识别重定向、`git worktree add` 及原地改写命令。前端交互会话返回请示（`force_ask`／`permissionDecision: "ask"`），无头子任务（`CAE_HARNESS_DEPTH=1`）直接拒绝拦截（`deny`）。

#### （2）算例日志复合指纹防空转轮询
仅对 `.log`、`.out`、`.err`、`.output` 后缀文件、宿主任务日志及典型输出文件启用指纹比对，普通源码与配置文件随时自由精读；仅在以相同参数重复读取且文件内容未变时阻断空转。超大算例输出采用头尾分块哈希避免阻塞 I/O：
$$\text{Fingerprint} = \begin{cases} (\text{mtime\_ns},\ \text{size},\ \text{SHA256}_{\text{full}}), & \text{size} \le 4\ \text{MiB} \\ (\text{mtime\_ns},\ \text{size},\ \text{SHA256}_{\text{head 1MiB} \parallel \text{tail 1MiB}}), & \text{size} > 4\ \text{MiB} \end{cases}$$

### 3. 防空等刷屏与数值迭代进展放行守卫（`reply_repeat_guard.py`）

反重力侧 `continuationActive == true` 或 Claude Code 侧 `stop_hook_active == true`／子代理支线无条件放行；同一用户回合最多提醒 1 次。对短等待回复剥离时间戳后比对数值特征：只要迭代步数、残差量级或推进时间等数字有真实推进即正常放行，仅在连续 3 次同质空等时提醒静默等待后台通知。

### 4. 长会话收口与交接状态机（`handoff_gate.py`）

- **反重力四态状态机（`normal -> advisory -> drain -> freeze`）**：按压缩频次、轨迹体积与读图负载分级迁移；进入 `drain` 后禁止新开子代理、`run_workflow`、`cae_harness.py`、`git push` 与读图，排空预算耗尽转入 `freeze`（仅放行只读核查、交接文档与本地提交；支持 `R8` 用户暂缓宽限），`Stop` 阶段核验交接四要素及 `/goal` 取消标记。
- **Codex 与 Claude Code 适配策略**：默认运行 `advisory` 模式（压缩阈值 `0`）仅提醒不强停；显式启用 `enforce` 时执行 `4` 次排空收尾预算与最多 1 次交接要素补正校验后安全停机。

---

## 四、跨平台 UTF-8 管道与并发文件锁规约

1. **标准输入输出全程 UTF-8**：所有钩子带 `-X utf8 -I -B` 启动，入口显式调用 `reconfigure(encoding="utf-8", errors="replace")`，杜绝 Windows 默认代码页乱码与 Linux `LANG=C` 编码异常。
2. **原子写盘与独立文件锁域**：所有状态 JSON 与 Markdown 归档先写同目录临时文件，`flush()` + `os.fsync()` 后经 `os.replace()` 原子替换（内置 Windows 句柄占用短退避重试），原话归档与交接状态采用独立系统文件锁互不阻塞。

---

## 五、受控实机验收记录（2026-11-13 ～ 2026-11-15）

以下数据均在本机与独立临时目录中完成真实宿主受控实测，公开文档仅记录脱敏后的定量结果。

### 1. 四端生命周期钩子、本地分流路由与无头 CLI 跨端执行实测

- **Codex CLI `0.167.0-alpha.4.1`（Windows）**：
  - 默认 `advisory` 模式下五类事件全部真实触发，跨进程 `resume` 压缩计数不重复累加；隔离进程开启 `enforce` 后准确拦截 `drain` 期远端推送与 `freeze` 期源码补丁，正常放行局部收尾与交接文档补正。
- **反重力 CLI `1.5.8`（Windows 与 WSL `Ubuntu-24.04`）**：
  - 四类事件在双平台原生触发；WSL 原生 CLI 登录后，`cae-harness` 只读审查与 `Hydrogen (High)` 写入任务以纯 Linux 原生链路（`side=posix`、`windows_interop=false`）一次通过三单元周期通量差 9 组精确分数验收（`49.64s`）；`Orchestrator (Carbon)` 在 `--timeout 900` 下以 `342.07s` 通过同一守恒验收。
- **Claude Code CLI `2.1.323` 无头继承执行、钩子协议与本地路由联调（Windows 实机 + 离线全套夹具）**：
  - 实机运行 `cae_harness.py task --caller antigravity --engine cc:default --kind review`：预检准确识别 `~/.claude/settings.json` 中的 CC Switch 卡片环境（`deepseek-flash[1M]`，配置窗口 `1,048,576`、压缩窗口 `1,000,000`），不加 `--bare` 原生继承全局配置启动 `claude -p --output-format json --json-schema ... --tools Read,Grep,Glob --permission-prompts none`，一次返回 `returncode=0`、通过 `modelUsage` 回填真实执行模型 `actual_model="deepseek-flash[1m]"`、工作区快照 `changed_files=[]`，并交付逐行精确引用的结构化审查回执；
  - 五类钩子事件、`router.py`（Kimi `k3.1-pro[1m]` + DeepSeek `deepseek-flash` SSE 流式分流与 WSL 代理跟随）及 `ccswitch_admin.py` SQLite 原子备份切卡全部通过事务回归验证。
- **DeepSeek Harness CLI `0.2.2-rc.3` 裸态预检与临时补丁无头执行实测（Windows 实机）**：
  - 在本机裸装 DSH（仅含官方账号登录凭据 `deepseek-account`）环境下运行 `cae_harness.py probe`／`assess`：零写入准确检出 `dsh_provider="deepseek-account"`、内置双模型 `deepseek-v4-pro`／`deepseek-flash`（声明窗口 `1,048,576` token，安全预算 `943,718` token）；
  - 实机运行 `cae_harness.py task --caller antigravity --engine dsh:default --kind review`：动态生成临时 `--patch` YAML 开启 `credentials`/`authorization`/`deepseek-account` 并在 `--permission-mode read-only --approval-policy never` 下驱动 `dsh --profile headless --json`，成功过滤中间工具探测异常（`tool_result` 恢复轮次）并精确提取末尾 `type="final"`（`reason.kind="completed"`）结构化 JSON 回执（`returncode=0`、`changed_files=[]`、`~/.dsh` 持久化凭据与配置零改动）。

### 2. 上下文窗口容量与自动检查点压缩边界压力测试

测试采用合成随机口令语料，禁止模型调用工具或读取磁盘归档，逐一核对分布在首、中、尾的 6 处随机值：

| 测试对象与场景 | 实测输入规模与宿主行为 | 提取命中率与工程结论 |
| --- | --- | --- |
| Codex `GPT-6.1-Sol`（分轮累积，同轮提取） | 单次请求输入达 `808,892` token（占宿主 `828,400` 有效窗口的 **97.65%**），未触发压缩、无工具调用 | **6/6 全部精确命中**，证实 `872,000` 声明最大窗口下的超长上下文同轮检索真实可用 |
| Codex 单条消息 RPC 上限探针 | 单条 `3,444,360` 字符消息被 `turn/start` 拒绝，服务端返回上限 `1,048,576` 字符 | 属客户端单次 RPC 载荷限制；超长源码或日志应让模型在工作目录内按文件读取或分轮送入 |
| 反重力 CLI `1.5.8` `Hydrogen`（单条超大输入探针） | 单条 44～66 万字节消息在轨迹与模型回复中出现入口截断标记 | CLI 无头单条提示词不宜过长，大体积材料应走文件读取或分块送入 |
| 反重力 CLI `1.5.8` `Hydrogen`（分块累积与检查点触发） | 每轮约 13 万 UTF-8 字节无截断：累积至 `241,625` token 时未压缩，**6/6 全部精确命中**；继续追加至 `290,028` token 时触发自动检查点压缩，压缩后输入降至 `14,063` token | 自动检查点压缩后早期散落的细节无法单凭压缩摘要还原（0/6），直接验证了 **`user-guard` 在检查点后从磁盘归档恢复用户原话**以及**把大体积源码分流给独立窗口子代理或大窗口无头后端**的工程必要性 |
