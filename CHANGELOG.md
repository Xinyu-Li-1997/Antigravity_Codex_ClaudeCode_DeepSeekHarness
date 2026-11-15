# 更新日志（Changelog）

记录 `Antigravity_Codex_ClaudeCode_DeepSeekHarness`（含 `user-guard` 全局插件、`cae-harness` 四端后台调用与验证工具及 `fleet-evolve` 升级整理流程）各版本的功能更新、客户端适配与安全修复。

---

## [1.11.1] - 2026-11-15

新增 `cae-harness` 无头纯净盲读模式（`--clean-room`），建立“多端新手视角盲读 -> 主控重写自然工程中文 -> 独立核对技术准确度与脱敏”三步文档演进机制，完成全仓文档去黑话重写与 CC Switch 退役残留清理：

- **无头纯净盲读隔离模式（`cae_harness.py`、`tests/test_harness_offline.py`）**：在 `cae_harness.py task` 中新增只读专用的 `--clean-room` 选项（支持 `codex`、`claude`、`dsh`；对不支持剥离全局插件的 `agy` 或写任务显式报错拦截）。开启后自动在系统临时目录构造仅保留底层登录凭据或 API 环境变量的隔离配置目录（`claude` 自动启用 `--bare` 与最小环境变量 `--settings`，`codex` 与 `dsh` 剥离全局 `AGENTS.md`、钩子与技能，并在只读工作区镜像中排除 `AGENTS.md` / `CLAUDE.md`；当 `codex` 只读镜像不超过 `256 KiB` 时自动附带行号文件快照以兼容空配置 Windows 只读沙箱），提供真正空历史上下文、无全局规则、无生命周期钩子、无全局技能的零预设盲读环境。
- **三步文档回看校核机制与全仓自然工程文风重写（`README.md`、`docs/`、`AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`fleet_state.py`、`tests/test_e2e_integration.py`）**：
  - 在 `R0` / `R2`、`fleet-evolve/SKILL.md` 与根 `AGENTS.md` 中确立不设死板字数上限的三步文档更新流程：① 先用 `--clean-room` 并发启动 `codex:sol`、`cc:default`、`dsh:default` 按不同目标群体新手视角盲读挑刺；② 主控汇总反馈后从头重写逻辑顺序与平实 CAE 工程中文；③ 由 `readonly-auditor` 对照源码独立校核技术事实与脱敏，并在 `fleet_state.py clear --verified --readback-done` 中记录校核状态；
  - 采纳三端纯净盲读反馈，按“先上手、后全貌、再查表与底层细节”重排 `README.md`（单客户端直连起步前置、示例统一 `--caller auto`、补充概念解释与关闭卸载说明），在 `docs/workflow-guide.md` 中新增可直接复制的 HLLC 通量任务单模板与 JSON 回执字段解读，将 `docs/architecture-and-hooks.md` 中的压缩长表格拆解为清晰的钩子拦截一览表、异常放行（Fail-Open）说明与窗口口径定义；
  - 在 `test_41` 中补充非 `CHANGELOG.md` 文档禁止残留静态日期戳与补丁式附注抬头的回归检查，并在双侧清理已停用的 CC Switch 旧提示词与旧技能残留记录。

---

## [1.11.0] - 2026-11-15

完成全栈非主控工具链三级耦合升级体系、多代理整仓代码／安全／逻辑深度审计加固，以及面向 CAE 工程师的全仓文档单点真源（SSOT）整篇重构：

- **全栈非主控升级三级耦合分级与 `host.toolchain` ABI 耦合预警（`install.py`、`fleet-evolve/SKILL.md`、`AGENTS.md`）**：明确全升级覆盖除当前活跃主控进程外的全部外部 CLI、辅助工具与运行时，按耦合风险划分为 **Tier 1**（无耦合非主控 CLI／`Python.Launcher`／CI Actions 直接原地更新并跑真机探针）、**Tier 2**（同次版本 Python 补丁与 `wsl --update` 先验进程锁再执行）与 **Tier 3**（挂载 `jax`/`jaxlib`/`scipy`/`mpi4py` 等编译扩展的全局 Python 跨次版本跳跃及 WSL 跨 LTS 升级强制发出依赖耦合警告拦截强升）；`install.py --audit-upgrade` 新增 `host.toolchain` 零写入诊断，自动识别非隔离全局编译型科学计算包并输出 `coupling_warnings`。
- **整仓多代理安全、并发与边界审计修复（`status_guard.py`、`cae_harness.py`、`fleet_state.py`、`router.py`、`ccswitch_admin.py`、`install.py`）**：
  - `status_guard.py`：新增递归解包深度上限失败闭合（`depth > 4`），剥离 Shell `if/elif/while/until` 条件头，精确解析 `env`（含 `-C`）、`sudo`、`command`（放行 `-v/-V` 查询）、`timeout` 包装，跨子句串联追踪 `cd/pushd` 工作目录，拦截无空格紧邻重定向（`echo x>/out`、`>|`、`<>`、`&>`），识别 `py`/`python -Bc` 内联脚本并支持 CAE 整数步号 `f-string`（如 `f"step_{i:04d}.vtk"`）、`pathlib.Path()` 与 `.resolve()` 静态路径求值，所有状态读写前置重解析点校验；
  - `cae_harness.py`：加固 Windows PowerShell `finally` 无条件进程树清理、纯 `probe` 与 `--dry-run` 严格零写盘、WSL 跨子系统 `.cmd/.bat` `%`->`%%` 防注入转义、`.github` 纳入快照收集、以及 `AGY_ERROR` 超时行优先拦截；
  - `fleet_state.py`：引入跨平台文件锁（`msvcrt`/`fcntl`）、`repo_root` + `core_sha256` 漂移校验、`verify-pass2` `claimed_by_session_id` 原子认领与损坏状态 `corrupt_state` 诊断；
  - `router.py` ＆ `ccswitch_admin.py` ＆ `install.py`：新增本地路由 `64 MiB` 请求体上限（`413`）与 `64` 并发信号量（`503`）、原生 Linux 无 `powershell.exe` 快速短路；加固 `ccswitch_admin.py` 符号链接拦截、归档先于 SQLite `commit` 失败回滚及 `settings.json` 优先恢复；`install.py` 新增 `_rules_equal` 精确识别无自定义尾段时的 `R10/R11` 漂移并规范化比对 Codex `hooks.json`。
- **全仓文档单点真源（SSOT）整篇重构与矩阵二 `fleet-evolve hygiene` 收编（`README.md`、`docs/`、`AGENTS.md`、`SKILL.md`）**：按职责边界通篇重写全部核心文档，彻底消除补丁式堆叠与跨文件重复，将客户端钩子协议与路由细节统一收敛至 `docs/architecture-and-hooks.md`，并在 `README.md` 第四章矩阵二正式纳管 `fleet-evolve hygiene`（整仓代码／结构／文档重构与主控 `R6` 验收门禁），同步修正 `ccswitch_admin.py switch-card` 位置参数命令示例与 `Hydrogen` 自动压缩标定归属。

---

## [1.10.0] - 2026-11-15

新增多引擎舰队自举演进与减法清淤协议（`fleet-evolve`）、原地升级可视化舰队矩阵，并修复跨端实测检出的钩子 glob、无头 DSML 标签泄漏及升级体检隐私隔离缺陷：

- **`fleet-evolve` 舰队自举演进技能与宿主机二阶段状态锁（`template/user-guard/skills/fleet-evolve/`、`install.py`、`AGENTS.md`）**：新增单一顶层演进协议 `SKILL.md`（支持 `full`、`audit`、`hygiene` 三种单趟模式）与纯标准库状态锁脚本 `scripts/fleet_state.py`（独立落盘于宿主机本地状态目录，绑定 `execution_side`、`repo_commit`、`plugin_version`、`skill_sha256`、`session_id` 不等校验与 `24h` TTL，防止主控换代重入死循环）；扩展 `install.py` 将 `skills/` 下全部托管技能（`cae-harness`、`fleet-evolve`）对称同步并纳入四端 `--audit-upgrade` 漂移核查，同时将双 `AGENTS.md` 减法精简收敛。
- **前置权责硬边界与双核心可视化矩阵原地升级（`README.md`、`docs/`、`rules/AGENTS.md`）**：明确划分“网络连通／客户端安装／官方登录态／API Key 用户自理底座（探针透明暴露缺口，零灰产反代）”与“全局规则／通用技能／确定性钩子／只读子代理／本地路由单仓同源按需编译分发”两层边界；遵循 `R0` 单一真源在 `README.md` 第四章原地建立**双核心可视化矩阵**——**矩阵一**（`宿主外壳控制面 × 基座模型推理面` 六列正交适配矩阵与三层实测证据）与**矩阵二**（用户日常模糊说法、100% 锁定调起的精确模块名及底层硬约束映射矩阵）。
- **真机并发评审检出缺陷修复与体检隐私加固（`status_guard.py`、`cae_harness.py`、`install.py`、`ccswitch_admin.py`）**：修复 `status_guard.py` 在 Claude Code 目录 `Grep` 传入 ripgrep 复杂 glob（如 `**.py`、`{a,b}`、`!exclude`）时触发 `Path.rglob` `ValueError` 及遍历 `.git` 的问题；修复 `cae_harness.py` 在 `dsh` 无头模式下底层模型尾随泄漏 `</｜｜DSML｜｜...>` 停止标签导致的 JSON 解析报错；加固 `install.py` 排除反重力默认选模中的第三方托管模型、隔离沙箱审计对宿主机 `cc-switch.db` 的读取、脱敏非标准自定义卡片名称、抑制未安装客户端的空目录漂移噪点，并补齐 `ccswitch_admin.py` 卡片上下文窗口对齐校验。

---

## [1.9.1] - 2026-11-15

补齐 CC Switch 提示词与技能统一退役归档守护、全栈升级体检精准对齐及 WSL 跨子系统无头实测闭环：

- **CC Switch 提示词与技能双退役守护及在线归档清理（`ccswitch_admin.py`、`install.py`、`R11`）**：明确 CC Switch 仅保留供应商卡片管理职责，内置提示词（`prompts`）与技能（`skills`）分发全部退役，全局规则与 `cae-harness` 统一由本仓库下发至四端各自宿主机全局根目录落实；`inspect_status`／`verify_alignment` 与 `install.py --audit-upgrade` 新增 `skills` 表启用列及 `~/.cc-switch/skills` 残留核查，`--disable-ccswitch-prompts` 扩展为同时禁用陈旧提示词与技能开关，并新增 `ccswitch_admin.py retire-legacy --purge` 与 `install.py --purge-ccswitch-legacy`（在线备份 `cc-switch.db` 并归档旧技能目录后清空已退役提示词与技能记录）。
- **升级体检误报消除与 WSL Fake-IP 直连预检拦截（`install.py`、`cae_harness.py`）**：修正 `audit_upgrade_status` 在反重力已配置最新 `Carbon` 时因上游目录含待下线旧模型导致的 `review_antigravity_models` 误报，以及 WSL 侧仅落盘规则钩子并通过跨子系统互操作复用 Windows Codex CLI 时的 `refresh_codex_model_catalog` 误报；`cae_harness.py` 新增 WSL 直连卡片命中宿主 TUN Fake-IP（`198.18.0.0/15`）且未设代理时的毫秒级预检拦截提示，并完成 WSL 原生 `cc`（经本地 `8241` 路由）与跨子系统 `dsh` 无头端到端实测验收。

---

## [1.9.0] - 2026-11-15

完成仓库更名与版权归属加固（`Antigravity_Codex_ClaudeCode_DeepSeekHarness` + Apache 2.0 `NOTICE`），并将 Antigravity（`agy`）、Codex（`codex`）、Claude Code（`claude`／`cc`）与 DeepSeek Harness（`dsh`）统一收敛为同级对等工作流与 `cae-harness` 四后端无头调度体系：

- **四端同级宿主落盘与 `DSH + DeepSeek` 零门槛入门／应急兜底基线（`install.py`、`status_guard.py`）**：新增 `--app dsh` 与 `--dsh-home` 部署及 `--audit-upgrade` 零漂移巡检支持，自动识别裸装 DeepSeek Harness（`~/.dsh`）并无损同步 `R0–R11` 全局规则（保留末尾自定义 `## 工具与...` 段落）与 `skills/cae-harness/`，同步补齐 Claude Code `~/.claude/skills/cae-harness/` 对称部署；明确本仓库仅作为上游模板与升级体检源，安装后各客户端均在各自宿主配置根目录独立闭环运行，支持新用户以官方 `DSH + DeepSeek` 零反代合规起步、后续按需平滑接入更强模型。
- **`cae-harness` 四引擎对等调度、模糊指定与接入预检门禁（`cae_harness.py`）**：在既有 `agy` 与 `codex` 基础上对等接入 `claude`（别名 `cc`，无 `--bare` 完整继承 `~/.claude` 规则、钩子与 CC Switch／本地分流路由配置）和 `dsh`（`--profile headless` 临时运行时补丁自动激活官方账号登录或 `DEEPSEEK_API_KEY`，映射 `DeepSeek-V4-Pro` 与 `DeepSeek-V4.1-Flash` 百万上下文及思考挡位）；支持中英文语义线索按需点名后端且排除编译环境变量（如 `CC=gcc`）与否定句误触发；调用前强制校验可用大模型凭据、本地路由 `/__router/health` 及关联 CC Switch 卡片密钥或官方登录态，并在 `--fallback-on-quota` 下支持向已就绪对等后端无痕回退。
- **仓库品牌升级与 Apache 2.0 第 4(d) 条 `NOTICE` 原创归属锁定（`NOTICE`、`LICENSE`、`plugin.json`、`.github/workflows/ci.yml`、`tests/`）**：同步仓库标识至 `Antigravity_Codex_ClaudeCode_DeepSeekHarness`，新增根目录 `NOTICE` 归属声明并在端到端门禁测试与跨平台 CI 矩阵中固化六模式沙箱安装（`codex`、`antigravity`、`claude`、`dsh`、`both`、`all`）及全套离线回归校验。

---

## [1.8.0] - 2026-11-14

打通 Antigravity、Codex、Claude Code 与 CC Switch 四组件单点真源协同治理、本地模型分流路由与全栈升级适配回归规程：

- **三端全局规则、只读子代理与生命周期门禁同源统一（`template/user-guard/`、`template/claude/`、`install.py`）**：新增 `--app claude` 与 `--app all` 部署模式，将 `R0–R11` 全局规则无损同步至 `~/.claude/CLAUDE.md`（自动保留末尾自定义 `## 工具与...` 段落），配齐 Claude Code 原生三个严格只读子代理（`readonly-reader`、`readonly-auditor`、`readonly-collaborator`），并将压缩原话回溯（`session_context_loader.py`）、`R4` 项目目录硬边界 AST 守卫与防空转门禁（`status_guard.py`）、防重复等待刷屏（`reply_repeat_guard.py`）及长会话交接门禁（`handoff_gate.py`）扩展支持 `--client claude` 协议，升级时自动迁移旧版拆分式 `args` 钩子并保留可用解释器。
- **CC Switch 卡片无损投影、提示词防回退与混合分流路由收编（`template/claude/router/`）**：收编纯标准库分流守护进程 `router.py`（主模型直连 Kimi、子代理分流 DeepSeek、支持 WSL 跟随 Windows 手动代理与 TUN Fake-IP 回环探测及 60 秒卡片密钥只读缓存）、统一管理 CLI `ccswitch_admin.py`（支持 SQLite 在线原子备份、三卡初始化、按 `floor/exclusive/residue` 规则无损切换卡片、一键禁用 `prompts` 表陈旧提示词防反向覆盖）以及 WSLg 窗口防白屏启动器 `launch-cc-switch-wsl.sh` 与官方 `.deb` 校验升级脚本 `update-cc-switch-wsl.sh`。
- **全客户端、CC Switch 与大模型升级标准适配体检与全量回归测试（`install.py --audit-upgrade`、`AGENTS.md`、`.github/workflows/ci.yml`、`tests/`）**：新增零写入 `--audit-upgrade` 全栈体检器（Windows 应用包探测同步切换为标准库 `winreg` 纯内存只读查询，消除外部进程缓存写入）与触发式六步升级回归规程，覆盖各客户端版本、SQLite Schema、生命周期钩子协议及最新大模型目录（模型代际、上下文窗口上限、思考挡位、卡片与路由对齐）一致性核查，并在 CI 与本地沙箱补齐五模式隔离安装及全套离线单测。

---

## [1.7.5] - 2026-11-14

完成仓库工程标准化完善、门面英文架构导引与跨平台自动化回归门禁建设（保持中文单源提示词内核零膨胀）：

- **文档路径纯 ASCII 标准化与双向链接补齐（`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`、`README.md`、`template/user-guard/README.md`）**：将 `docs/` 下两份中文命名文档规范重命名为小写 ASCII 短横线路径，彻底消除跨平台 Git 八进制路径转义（`core.quotepath`）、URL 百分号编码与旧版文件名语义脱节问题，并同步更新全仓引用链接。
- **门面英文架构导引与语言作用边界声明（`README.md`）**：在不拆分冗余双语模板、不增加运行期常驻上下文开销的前提下，于根 `README.md` 顶部增设 `English Overview`，简明交代面向 AI-CAE／CFD／FEA／PDE 求解器研发的核心守护机制、三套部署模式及机器契约（英文）与内置交互规则（简体中文）的真实边界。
- **版本号单点真源、行尾规约与脚本提示口径统一（`template/user-guard/plugin.json`、`template/user-guard/README.md`、`install.ps1`、`.gitattributes`）**：移除插件子目录 `README.md` 中易遗漏的静态版本号硬编码，统一以 `plugin.json` 为全仓唯一版本真源；统一 `install.ps1` 错误输出语种并保持 PowerShell 5.1 所需的 UTF-8 BOM + `CRLF` 编码；补齐 `.gitattributes` 对 `.toml`、`.yml`、`.yaml`、`.svg` 的 `LF` 行尾约束。
- **跨平台 CI 自动回归门禁、Windows `8.3` 短路径兼容与仓库规范单测（`.github/workflows/ci.yml`、`install.py`、`status_guard.py`、`tests/`）**：新增最小权限 GitHub Actions 工作流（覆盖 `ubuntu-latest` 与 `windows-latest`、Python `3.11`／`3.13` 三模式沙箱安装与全量离线测试）；修复 Windows 长用户名环境下 `%TEMP%` 等目录以 NTFS `8.3` 短路径（如 `RUNNER~1`）传入时 `status_guard.py` 词法边界与 `install.py` 托管钩子识别不一致的问题；并在端到端测试中新增纯 ASCII 路径、相对链接有效性及单点版本号校验。

---

## [1.7.4] - 2026-11-14

落实反过度工程（`R3.3`）与全局规则精简去冗，统一 `Python 3.11+` 标准库基线并清理老版本与外围平台兼容包袱：

- **全局规则瘦身提纯与反过度工程红线（`template/user-guard/rules/AGENTS.md`、根目录 `AGENTS.md`、双端 `readonly-*` 子代理）**：将双端常驻加载的全局规则篇幅精简逾三分之一（去除与 `cae-harness/SKILL.md` 重复的 CLI 参数罗列）；在 `R3.3` 明确写入反过度工程约束（严禁为未要求的老旧版本、外围平台或假想场景编写兼容分支、回退解析器与多余抽象）；收紧 `R3.6` 披露边界（仅标注目标环境与核心验证链路的未实测项，严禁拿无关外围平台凑免责尾巴）；将原生 Shell／UTF-8 编码与公开仓库脱敏红线并入 `R4` 核心区，消除 Codex 启用自定义 `## 工具与本机` 尾段时漏掉脱敏与编码规则的盲区。
- **统一 `Python 3.11+` 基线并移除老版本与外围平台冗余代码（`install.py`、`install.ps1`、`install.sh`、`cae_harness.py`、`status_guard.py`）**：直接采用标准库 `tomllib`、`ast.Match` 与原生完整 ISO 时间戳解析，删除 `install.py` 与 `cae_harness.py` 中近 200 行针对 `Python 3.9/3.10` 的手写正则 TOML 回退解析器及 `Python 3.9` AST 兼容垫片；移除未使用的 `macOS` `/private/*` 根链接与 `.app` 探测分支，聚焦 Windows 与 WSL2／Linux 主力环境。
- **Codex 钩子升级参数一致性修复（`install.py`）**：升级已登记的 `session_context_loader.py` 托管钩子时，在保留既有可用解释器路径的前提下，自动将旧版遗留的 `-I -B` 启动参数补齐为 `-X utf8 -I -B`，保持二次运行严格幂等与信任哈希稳定。

---

## [1.7.3] - 2026-11-14

完成双端全量文件回归检查、边界 Debug 修复、性能优化与全仓文档润色清淤脱敏，并将阶段收口双端把关制度化写入核心规范：

- **阶段收口双端全量把关写入核心规范（`R3.1.4` 与根目录 `AGENTS.md`）**：明确双端协作项目在里程碑收口、合并或发版前按全量文件分工闭环——由 Codex 无头工作流承担全仓代码回归检查、Debug 排错、死代码／性能优化与验证门禁测试；由反重力承担全仓文档书面语润色、冗余清淤精简与隐私凭据脱敏把关，经总控逐行复核差异与运行证据后收口。
- **AST 边界守卫 Python 3.9 兼容与关键字／解包参数全覆盖（`status_guard.py`）**：将 `ast.Match` 改为 `getattr` 动态兼容保护，消除 Python 3.9 下 `AttributeError` 导致的静默放行旁路；补齐 `Path.rename`／`replace`、`os.remove`／`unlink`／`mkdir`／`makedirs`／`rmdir`／`removedirs`／`rename`／`replace`、`shutil.copy*`／`move`／`rmtree`、`np.save*`、`open(file=..., mode=...)` 及 `run_workflow` 中 `agent(**kwargs)` 的显式关键字参数与字典解包路径核验，兼容 UTF-8 BOM 工作流脚本并清理冗余未用函数；新增 `ast.parse(..., feature_version=(3, 9))` 全仓语法门禁测试。
- **长会话交接子壳分组拦截与压缩／恢复游标加固（`handoff_gate.py`、`session_context_loader.py`、`user_words.py`、`reply_repeat_guard.py`）**：`_split_shell_clauses` 支持切分引号外的 `( ... )` 子壳分组（保留 `$(...)` 命令替换与函数调用内嵌括号），阻断收口期 `(git push ...)` 绕过；修复 Codex `compacted` 与 `turn_context` 间夹杂普通事件行时 `pendingRecord` 提前清空、新用户回合未重置 `stopCorrectionRequested` 及降回 `advisory` 后的状态残留；补齐压缩游标元数据 Schema 与空文件／新 inode 重写自愈；修复子代理任务书提及系统标签字面量被误滤、中文紧邻 `/goal` 取消与时间戳归一化、Windows 文件锁显式解锁及超 `256 KiB` 交接尾行逆向完整读取。
- **编排器防空门禁、特殊文件防阻塞与 `verification_executed` 显式标记（`cae_harness.py`）**：拦截空串或纯空白 `--verify-cmd`，在 `gate` 与 `code` 任务回执中显式标注 `verification_executed`（无 `--verify-cmd` 的编码任务标注 `false` 并附未验证提示）；`snapshot_files` 与 `collect_target_files` 合并为单次 `lstat` 并拦截／跳过 FIFO、socket 等非常规特殊文件防打开挂死；确保 `assess` 纯只读零删除历史额度事件文件，`audit` 事后快照异常时保留已完成审计结果；`_windows_env` 校验变量名并优先走 `powershell.exe` 安全读取。
- **安装器跨平台兼容性加固与全仓清淤脱敏（`install.py`、`install.ps1`）**：`install.ps1` 改用标准流句柄直接继承与 `CommandLineToArgvW` 参数转义，兼顾无换行交互提示即时显示与 PowerShell 5.1 原生 `stderr`／退出码无损透传；`install.py` 区分 Windows 云盘重解析点与目录联接、放行 POSIX 系统根级 `/var`／`/tmp`／`/etc -> /private/*` 标准别名、支持引号包裹的 Windows `PATH` 项与含 `#` 字符串的回退 TOML 数组解析，并在插件升级时自动清理过时空目录；清理 `README.md` 残留对话措辞，全仓回归测试由 160 项扩充至 220 项。

---

## [1.7.2] - 2026-11-14

全面适配 Google Antigravity App `v2.32.0` 与 CLI `v1.5.8`，补齐动态工作流边界审查，并明确双端协作中优先交由 Codex 端无头工作流攻坚的分工规范：

- **双端按专长分工（硬核工作流优先交由 Codex 端攻坚）**：双端均可用时，凡涉及偏微分方程推导、高阶数值格式分析、核心求解器编码、严苛边界／脱敏审查及带验证命令的门禁闭环，优先通过 `cae-harness` 发起 Codex 无头工作流（默认启用 `Astra`／最新代 `Sol` 单模型最高思考挡 `max` 与 `872,000` token 声明窗口，配合 `workspace-write` 工程沙箱）；反重力主会话（推荐 `Carbon High`）负责全局统筹、多文献综合、同端纯只读盘点与最终逐行验收。
- **只读子代理兼容桌面端 `v2.32.0` 与 CLI `v1.5.8`**：移除 `readonly-reader`、`readonly-auditor`、`readonly-collaborator` 中会导致桌面端 `<subagents>` 目录隐藏该代理的非公开字段 `hidden: true`，保留 `subagent: true` 与 `mainAgent: true`；工具白名单统一为 `[read_file, search_web, send_message, finish]`，兼顾桌面端子代理消息回传与 CLI `--json-schema` 结构化收尾。
- **`run_workflow` 动态工作流与跨表面写改工具纳入 `R4` 目录硬边界**：`status_guard.py` 补齐 `write_to_file`、`replace_file_content`、`multi_replace_file_content`、`sed_file`、`notebook_edit` 五类文件修改工具及 `AbsolutePath`／`FilePath` 等路径字段识别（审批提示保留原始工具名），并将 `view_file` 纳入未变化日志防空转门禁；新增 `run_workflow` 启动前预审，静态检查外层 `Cwd`、`ScriptPath` 脚本文件（拦截缺失、超长、不可读或语法错误脚本）以及内联 `Script` 中的 Python 写删、`call_tool(...)` 与 `agent(..., cwd=...)` 越界调用。
- **内联 Python 与工作流 AST 静态路径追踪加固**：按源码真实求值顺序追踪变量候选路径、海象表达式（`:=`）、元组／列表解包、`with ... as` 绑定、f-string 拼接、`if/try/for/while` 分支合并、函数调用点实参重审及 `os.chdir` 目录切换；凡遇到无法静态证明安全的动态路径、非常量 `open` 模式、动态工具名或含未知解包（`**kwargs`）的工具参数，均触发审批或拦截；安装脚本调用检查覆盖重复传入的目标目录参数。
- **长会话收口门禁、冻结期 Git 分支保护与 CLI `Stop` 续跑兼容**：`handoff_gate.py` 在收口期禁止新发起 `run_workflow`，将 `post_message`、`swarm_wait` 计入 `drain` 期收尾预算并在 `freeze` 期阻断，补齐 `read_board`、`read_url_content`、`list_resources`、`read_resource` 只读放行及 `view_file` 读图限流；双端 `freeze` 期对 `git branch` 改用纯查询参数白名单，阻断 `--unset-upstream`、修改上游跟踪、建删改移分支等写操作；`reply_repeat_guard.py` 同时识别桌面端 `continuationActive` 与 CLI `v1.5.6+` 的 `continuation_active` 续跑标记。
- **`agy` CLI 超时识别与错误诊断防掩盖**：`cae_harness.py` 识别 `agy --print-timeout` 退出码 `3`、`[agy] print timeout after ...` 及 `AGY_ERROR` 超时行并统一归类为 `timeout`（外层进程预留 `+15s` 收尾宽限），拦截 `INVALID`／`WAITING`／`RUNNING` 非正常终态；即使子进程返回码为 `0` 或输出流中同时带有 `result` 事件，只要检出明确错误诊断仍判定失败，并在错误分类前剥离正常业务回执正文，避免报告内容提及“限流／超时”字样误触发额度冷却。
- **官方 v1 插件清单 `--strict` 满分校验与全文件脱敏回归**：`plugin.json` 补齐 `$schema`、`license` 与极简矢量图标 `assets/logo.svg`（通过 `agy plugin validate --strict` 零警告零提示校验）；脱敏回归扫描全仓所有普通文件并拒绝未知非 UTF-8 二进制文件，覆盖跨平台路径变体、用户名、主机名、会话 UUID、非示例邮箱、内网 IP、常见密钥凭据与具体 CPU/GPU 型号，并新增单端／双端三模式沙箱安装与钩子运行回归测试。

---

## [1.7.1] - 2026-11-14

- **默认满血最高思考挡位与按需分级省流**：`cae-harness` 在 `math`、`direction`、`code`、`review`、`audit`、`gate` 任务中默认启用所选旗舰专长模型（Codex `Astra`／最新代 `Sol`、反重力 `Carbon`）的单模型纯思考最高挡（`max`／`high`）及目录声明最大窗口（当前 Codex 目录为 `872,000` token），默认跳过会自动二次派生子代理的 `ultra` 挡；仅当用户明确或模糊提出降挡、省额度、省 token 时切换为分级省流模式。
- **安装器、钩子与编排器加固**：完善跨平台路径与符号链接／目录联接校验、Codex `hooks.json` 升级冲突检测、状态锁超时保护，并完成面向 AI-CAE 研发场景的全仓中文规范文档重构。

---

## [1.7.0] - 2026-11-14

- **额度冷却熔断与 `--fallback-on-quota` 本侧自动回退**：对侧返回配额耗尽时按执行侧（`windows`／`posix`）记录冷却截止时间并阻止盲目重试；开启 `--fallback-on-quota` 且工作区尚无文件改动时，自动切回发起方本侧匹配旗舰模型与最高可用挡位继续完成任务。
- **原生 `inherit` 子代理与跨端 CLI 选模解耦**：移除反重力原生只读子代理中的硬编码模型字段，使其默认跟随前端主会话模型与挡位，与 `cae-harness` 后台独立选模彻底解耦。

---

## [1.6.1] - 2026-11-14

- **长会话两阶段交接停机门禁（`handoff_gate.py`）**：新增 `normal -> advisory -> drain -> freeze` 增量状态机，联动 `PreInvocation`、`PreToolUse` 与 `Stop` 钩子验收交接完整性与 `<!-- GOAL_CANCELLED -->` 终止标记。
- **多表面跨平台同步与 Zotero 可选集成**：完善 `install.py`、`install.ps1`、`install.sh` 三入口及 `mcp_config.json` 安全合并。

---

## [1.5.0] - 2026-11-13

- **初始化 `user-guard` 原生插件与 `cae-harness` 确定性编排器**：建立 `R0–R11` 全局规则、压缩原话增量归档回溯（`session_context_loader.py`）、`R4` 项目目录硬边界与防空转门禁（`status_guard.py`）、防重复等待刷屏门禁（`reply_repeat_guard.py`）、只读子代理三件套及端到端单测套件。
