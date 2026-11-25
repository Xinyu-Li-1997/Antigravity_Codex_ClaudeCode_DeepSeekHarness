# 更新日志

本文件记录四客户端共用规则、会话钩子、后台验证工具与上游维护流程的版本演进。当前插件版本以 `template/user-guard/plugin.json` 为准；常驻使用文档只说明机制和配置键，不重复维护版本号与性能日期。

## [2.0.0] - 2026-11-25

面向科学计算与 CAE 工程研发的 **V2.0 架构里程碑版**：在 `V1.18.13` 四客户端通用防遗漏与防爆窗底座（`read-plan`、`cross-check`、`reconcile`）之上，系统性根治两类“单向负向约束失衡”元问题——确立四级受众文档分层架构与通用机械文档卫生门禁（`--doc-hygiene`），并补齐跨三端的对称防偷懒否定结论守卫，同时面向真实科研与工程读者整篇重写全套公开文档：

- **通用文档卫生守卫与四级受众分层（`cae_harness.py`、`.github/workflows/ci.yml`、`AGENTS.md`、`tests/test_harness_offline.py`、`tests/test_e2e_integration.py`）**：
  - 在 `cae_harness.py check-grounding` 新增零硬编码黑名单的通用 `--doc-hygiene` 检查（已接入 CI 与 `test_51`），确定性拦截引导句计数与紧邻表格或列表行数矛盾（`count_structure_mismatch`）、补丁沉积超长句与四段以上中文斜杠串（`runon_patch_sentence`、`slash_chain`）、相同长技术细则跨 ≥ 3 份文档逐字复读（`cross_doc_duplication`），以及根目录 `README.md` 超体积预算、泄漏 `_xxx` 私有符号或单段堆砌过多行内代码（`landing_size_budget`、`landing_private_symbol`、`landing_jargon_density`）；
  - 在 `AGENTS.md` 与 `R0` 确立 T0 入口页（`README.md`）、T1 实战手册（`docs/workflow-guide.md`）、T2 架构与运维参考（`docs/architecture-and-hooks.md`、`template/user-guard/README.md`）、T3 契约与日志（`AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`CHANGELOG.md`）四级单点归属边界；重构 `test_51` 与 `test_45`，解除历史测试对 6 份文档粘贴 `S1` 销账细则及向入口 `README.md` 强插内部键名的反分层约束。
- **对称诚实披露规则与三端未实查否定结论守卫（`template/user-guard/rules/AGENTS.md`、`template/user-guard/hooks/reply_repeat_guard.py`、`tests/test_guards_offline.py`）**：
  - 补正 `R3.6`、`R4` 与 `R11` 的对称边界：明确对“未连接、不存在、查不到、不可用”等否定结论与“全部通过”同等要求实证，项目外写边界不禁止对本机已连通资源做只读诊断，公开脱敏仅约束对外或入仓输出而严禁在本机排障命令中提前遮蔽导致自我致盲，且不得以 `R11` 为由跳过用户提示已就绪渠道的本地只读核查；
  - 在 `reply_repeat_guard.py` 新增跨 Antigravity、Codex、Claude Code 三端通用的 `_check_unverified_negative_claim_locked`（复用三端轨迹统计 `_turn_tool_probe_stats`）：本回合零实质探测或探测命令全部报错退出（成功 `0` 条）却宣称“已核实未连接/不存在/不可用”时触发 1 次拦截，续跑态、至少一条实质探测成功以及诚实标明局部排查范围或仅转述规则时均不误拦。
- **全套公开文档与配图整篇重写（`README.md`、`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`、`template/user-guard/README.md`、`SKILL.md`、`docs/assets/*.svg`、`CITATION.cff`）**：
  - 彻底清理入口文档中的内部行话堆砌与跨文档 4 倍复读：`README.md` 由 `30 KB` 精简至约 `13 KB`，`template/user-guard/README.md` 精简至 `2.4 KB`，拆解 `docs/workflow-guide.md`、`docs/architecture-and-hooks.md` 与 `cae-harness/SKILL.md` 的全部超长补丁句，并同步更新流程图文案与学术引用摘要；全仓 22 份文档在 `check-grounding --target . --strict --doc-hygiene` 下零告警通过。

---

## [1.18.13] - 2026-11-25

面向四客户端通用架构补齐三阶段确定性防遗漏与防爆窗机制——确定性机械对账表（`reconcile`）、读前双口径尺寸评估与保守层级分片（`read-plan`）、跨端分片盲读汇总核对（`cross-check`），并根治三项既有生命周期门禁漏洞：

- **阶段 1 · 确定性机械对账表与派单完工硬触发（`cae_harness.py`、`reply_repeat_guard.py`、`tests/test_harness_offline.py`、`tests/test_guards_offline.py`）**：
  - 新增纯离线子命令 `cae_harness.py reconcile`（支持 `--save-baseline`、`--baseline-file`、`--base-ref`、`--receipt`、`--cross-check`、`--obligations-file`、`--output-md`、`--plan-dir`），由确定性静态代码核对已更新/未更新代码与文档、Python AST 直接导入、Markdown 显式相对链接及反引号精确符号引用（高置信项入 `pending_review` 首屏 Top 10，普通词法提及仅计 `weak_mention_docs` 防扇出），并支持 `plan --obligations-file` 显式义务逐项结算；
  - `capture_reconcile_baseline` 冻结任务开工基线（有内容快照时优先起止比对，同时捕获任务窗口内已提交改动并隔离开工前已脏未改文件；缺少基线或快照未核验时如实标 `unknown`）；重负载派单完工在 `record_heavy_completion_for_stop_hook` 自动将对账表落盘至 `<plan_dir>/reconcile/<run_id>_reconcile.md`（绝不污染目标业务仓库），stdout 仅附单行 `reconciliation` 与 `chat_line` 链接；
  - Antigravity、Codex、Claude Code 三端共用的 `reply_repeat_guard.py` 依据 `heavy_completions` 中的 `reconcile_file` 统一强制核验机械对账表查看链接，零私有字段依赖，DSH 通过同一 CLI 获得同等对账能力。
- **阶段 2 · 读前双口径尺寸评估与保守层级分片（`cae_harness.py`、`tests/test_harness_offline.py`）**：
  - 新增纯离线子命令 `cae_harness.py read-plan`（支持 `--files`、`--shared-files`、`--core-files`、`--engines`、`--cross-client-mode`、`--shard-tokens`、`--max-concurrency`、`--context-used-tokens`、`--include-dirs`、`--clean-room`、`--emit-spec`、`--session-id`），同步校验保守单片 Token 预算与真实 UTF-8 带行号内联字节上限（独立核扣共享核心材料，超限自动降级为 `anchor_reference_only`）；
  - 超大单文件由 `_split_oversized_file_ranges` 按 Markdown 标题或 Python 顶层定义自动切分为带 20 行重叠的连续行段，按 AST/Markdown 依赖聚类分片并标记 `cross_shard_edges`，默认以保守总并发上限（`<= 2`）按 `risk-tiered` 或 `all-engines` 分波，输出 3 句话 `chat_brief` 与 `<plan_dir>/read_plans/<plan_id>_read_plan.md` 清单并如实披露被跳过的归档/历史目录。
- **阶段 3 · 跨端分片汇总核对与机械对账并轨（`cae_harness.py`、`tests/test_harness_offline.py`）**：
  - 新增纯离线子命令 `cae_harness.py cross-check`（支持多份 `--receipt` 与可选 `--read-plan`），确定性生成计划分母 × 执行端覆盖矩阵、全端共同盲区（`shared_blind_spots`）、失败/超时格（保留在分母绝不剔除）、多端同位并列/分歧（保留各端原文并检测 `severity_divergent` 与 `polarity_divergence_hint`，绝不把同位置冒充共识）、跨片接口联审缺口及 `r6_source_check_list` 待回源原文件精确行号清单；
  - 增强 `merge_blind_findings` 与 `cmd_audit`（输出 `cross_client_coverage`），支持通过 `reconcile --cross-check` 无缝并轨至总机械对账表。
- **四端钩子加固与既有门禁缺陷修复（`shell_lex.py`、`status_guard.py`、`reply_repeat_guard.py`、`session_context_loader.py`、`handoff_gate.py`、`tests/test_lifecycle_offline.py`）**：
  - 在 `shell_lex.py` 新增 `join_powershell_line_continuations` 并在 `status_guard.py` `_dispatch_commands` 中归一化 PowerShell 尾部反引号续行（修复多行命令误拆 `--ticket`/`--plan-dir`，且严格不吞噬 POSIX 多行反引号命令替换）；
  - 修复 `reply_repeat_guard.py` 三处绕过与碰撞：封堵含失败项却在同句同时写“全部成功”与“失败”的 `contradicted` 绕过、对共用同名裸回执文件的多次运行要求凭 `run_id`/对账表名/上级目录消歧、将 `_check_test_suite_locked` 收紧为 `2 * largest < total` 拦截全量测试失败后零复跑冒充全绿；
  - 升级 `session_context_loader.py` `_workflow_recovery_blocks` 兼容三端目录路径，压缩恢复时完整保留 R9 待批票据并有界折叠完工账单（附 `reconcile_file` 链接）与活跃 `read-plan` 指针；`handoff_gate.py` 统一放行 `reconcile`、`read-plan`、`cross-check` 诊断检查；同步更新并润色脱敏 `README.md`、`AGENTS.md`、`CITATION.cff`、`SKILL.md`、`template/user-guard/README.md`、`docs/architecture-and-hooks.md`、`docs/workflow-guide.md` 与三张 `docs/assets/*.svg` 流程图。

---

## [1.18.12] - 2026-11-24

围绕“防规则膨胀、降派工摩擦、控审查过拟合”完成常驻规则与公开文档整篇减负重写，并补齐审查阻断/建议分级、风险分层选挡与 2 轮门禁收敛：

- **常驻规则与技能文档系统减法（`AGENTS.md`、`template/user-guard/rules/AGENTS.md`、`template/user-guard/skills/cae-harness/SKILL.md`、`template/user-guard/skills/fleet-evolve/SKILL.md`）**：
  - 在 `R0` 与仓库治理边界确立“反复踩坑优先下沉为单测、类型约束或钩子守卫，已被机械检查守住的细则及时从文字规则减薄”的原则；大幅精简四份常驻与技能文档体积，同步清理与钩子源码重复的底层实现复述，降低日常上下文负担；
  - 在日常协作规程中补齐三项务实约定：零碎小修与高频交互对齐直接由主会话闭环（接口清晰的成块模块再跨端派工）、重大架构与建模路线支持无草稿盲出独立双方案防锚定，以及按风险分层的审查节奏控制。
- **审查分级、风险分层选挡与 2 轮门禁收敛（`cae_harness.py`、`fleet_profile.json`、`tests/test_harness_offline.py`）**：
  - 在 `_build_audit_prompt` 与 `_task_prompt(kind='review')` 注入审查严重度分级约束：仅将具有明确触发或复现链路的实质缺陷标为 `critical`/`high`（阻断级），细节与风格建议记为 `medium`/`low`（参考级），避免借极端假想反复打回；
  - 在 `HIGH_COMPLEXITY_CUES` 补充工程高危词（`越权`、`越界`、`安全钩子`、`权限`、`数据丢失`），并在 `select_task_tier` 中为 `role:review` 增加风险分层：常规文档与辅助脚本审查默认走 `fleet_profile.json` 中的 `recommended_tier: high`，命中物理守恒、离散算子、边界条件、并发死锁或安全权限高危词时自动升至 `max` 挡深审；
  - 将 `runtime_tunables.headless_dispatch.default_gate_max_rounds` 由 `3` 收敛至 `2`，两轮门禁仍未收敛即停下交由人工拍板，并补充离线回归单测 `test_review_severity_prompt_and_risk_tiered_effort`。
- **公开网页文档人话润色与脱敏核验（`README.md`、`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`、`template/user-guard/README.md`）**：
  - 拆解四份公开文档中的超长嵌套句与重复实现细目，按工程师阅读习惯以更自然平实的书面语整篇理顺，同步反映新版协作边界、无草稿独立双方案、审查分级与 2 轮门禁机制，并完成全仓 `check-grounding --strict` 词法锚定与脱敏复核。

---

## [1.18.11] - 2026-11-24

基于 Windows 与 WSL2 双端真实重负载轨迹与回执复盘，根治跨端协议边界失配、Windows 文件句柄锁中断抢救、无头子会话钩子二次注入及大仓库全盘快照耗时等五类系统性缺陷：

- **分后端只读内联上限、未内联清单截断与大目录快快照（`fleet_profile.json`、`cae_harness.py`、`tests/test_harness_offline.py`）**：
  - 在 `runtime_tunables.headless_dispatch` 新增 `agy_inline_readonly_max_kib`（默认 `128`，规避 `agy` CLI 单条输入协议硬截断边界），保留其余后端 `inline_readonly_max_kib`（`512`），`plan` 容量评估、明细卡片与 `_inline_readonly_prompt` 统一按后端口径核算；
  - 对 `_inline_readonly_prompt` 的未内联清单增加条目与字节双上限截断及回执清单上限标记，并为 `codex` 增设单条提示字符数安全护栏（`_CODEX_MAX_PROMPT_CHARS`）防止超大型仓库触发协议层超长拒收；
  - `snapshot_files` 升级为两阶段自适应快照：中小目录维持逐字节 SHA-256 校验，超过文件数或字节数阈值的大目录自动切换元数据快快照（`stat:`），`_changes_report` 自动对齐前后快照模式避免跨阈值误报，消除数万文件目录前后全盘哈希耗时。
- **Windows `[WinError 32]` 容错、紧凑收口与超时续接降挡（`fleet_profile.json`、`cae_harness.py`）**：
  - 软超时收口改写独立输出文件 `result_wrapup.json`（不再在收口前 `unlink` 主回合 `result.json`），临时目录退出清理改用带重试退避与容错的 `_rmtree_retry`，彻底消除 Windows 子进程短暂句柄未释放导致整次运行和 `timeout_salvage` 被异常中断的问题；
  - 软超时收口提示词限定紧凑输出预算（约 `1500` tokens 以内）并将 `wrapup_max_sec` 调至 `150` 秒；新增 `followup_default_effort`（默认 `medium`）与 `followup --effort` / `--tier` 参数，续接超时回执未显式指定挡位时自动降挡防二次超时，并补齐回执顶层 `model` 字段；Windows 可写 Codex 沙箱提示补充临时目录清理边界。
- **`agy` 后台活跃回合短路、`step_update` 真实进展识别、Token 归一化与 `dsh` 独立无头会话目录（`cae_harness.py`）**：
  - 当 `agy` 主回合因 `--print-timeout` 返回且 stderr 明确报告 `with turn in progress`（后台回合未取消）时，跳过必死锁的同会话收口并标记 `wrapup_status: skipped_background_turn_active`、`timeout_salvage.resumable: false` 与 `resume_blocked_reason: agy_background_turn_in_progress`；
  - 修复 `_turn_made_model_progress` 对 `agy` 真实 `step_update`（`step_type`）与 `result` 包裹层的解包逻辑，避免累计 `result.usage` 误掩盖零进展卡死；修复 `extract_cli_token_usage` 对 `agy` `cache_read_tokens` 补入 `input_tokens`/`total_tokens` 的口径，并对 `agy` 多回合累计用量改取各字段最大值消除双倍计数；
  - `dsh` 无头会话持久化目录由 `sessions` 隔离至独立 `headless-sessions`（`_session_logs` 优先扫描新目录并兼容旧目录），消除无压缩配置与既有 `.jsonl.zstd` 压缩日志并存时的启动报错；收紧 `_citation_audit` 噪声过滤，排除裸锚点、纯日期数字、通配符花括号与无文件依据的纯扩展名片段。
- **无头子会话钩子彻底隔离与 Markdown 表格账单识别（`user_words.py`、`session_context_loader.py`、`handoff_gate.py`、`reply_repeat_guard.py`、`tests/test_lifecycle_offline.py`）**：
  - 当处于外部无头子进程（`CAE_HARNESS_DEPTH` 非空且非 `0`）时，`codex_hook_session`、`claude_hook_session`、`session_context_loader.py`、`handoff_gate.py` 与 `reply_repeat_guard.py` 入口立即跳过（即使持久化续接存在既有轨迹文件也不再注入原话回溯），同时保留 `status_guard.py` 对无头子任务越界写入的硬阻断；
  - `reply_repeat_guard.py` 新增 `_has_token_report`，精准识别 Markdown 表格账单中的 Token 列数值或无法统计说明，避免仅因表格换行误拦完工汇报且不被其他列数字误导；同步增补离线回归单测并更新常驻文档。

---

## [1.18.10] - 2026-11-23

基于真实超长双语文档批次（单流显式传入多份超大文档触发分页、压缩与无头超时）的逐行根因排查，补齐 `plan` 显式只读超窗与内联预算护栏，并修复 `agy` 无头软超时收口与进程回收链路的三处缺陷：

- **`plan` 显式只读超窗硬拦截、分片建议与内联遥测（`cae_harness.py`、`SKILL.md`、`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`）**：
  - `plan` 对显式传入 `--files` 或单文件目标的只读流自动核算声明窗口占比、安全输入预算（`input_budget_tokens`）与只读内联上限（`inline_readonly_max_kib`）；当外部无头显式只读估算超出所选模型声明窗口时默认硬拦截并返回 `over_context_window` 与确定性贪心切分的 `suggested_shards`（自动选路 `role:*` 优先改切更大窗口引擎，确需仅抽查时可显式传 `--allow-over-budget` 或流字段 `allow_over_budget: true` 放行；目录级未传 `--files` 任务保持非阻断）；
  - 超出安全输入预算或内联上限时在 `<ticket_id>_card.md` 明细写入容量与内联预警及分片建议，并在 3 句话 `chat_brief` 附简洁预警标记；`_inline_readonly_prompt` 同步输出 `inline_readonly_summary`（已内联与超限未内联文件数及字节数）写入回执。
- **`agy` 带后缀模型收口降档与 `--print-timeout` 宽限协同（`cae_harness.py`）**：
  - `invoke_engine` 软超时收口由 `_wrapup_model_and_effort` 识别 `agy` 带挡位后缀模型（如 `*-high` / `*-xhigh`），自动切换至目录内同家族更低挡模型（优先 `*-low`、次选 `*-minimal` / `*-medium`）并记录 `wrapup_model` 与 `wrapup_effort`，消除此前带后缀模型收口零降档的问题；
  - `soft=True` 默认持久化模式下亦为 `agy` 外层 `proc_timeout` 保留高于 `--print-timeout` 的优雅宽限（不侵占收口最小预留时间），且 POSIX 进程树终止（`_terminate_tree`）先发 `SIGTERM` 等待最多 1.5 秒再升级 `SIGKILL`，让 `agy` 得以先完成内部超时收尾与会话刷盘。
- **`agy` 零进展卡死收口判定与 `followup` 防护（`cae_harness.py`、`tests/test_harness_offline.py`）**：
  - 当 `agy` 收口回合再次超时且无任何模型或工具推进事件（0 token，仅有 `init`、`user_input`、`system_message` 等启动提示事件）时，回执标记 `wrapup_stalled: true` 并将 `timeout_salvage.resumable` 置为 `false`（附 `resume_blocked_reason: agy_wrapup_stalled_without_model_turn`）；`cmd_followup` 启动前核验 `timeout_salvage.resumable`，遇不可续接回执直接返回 `session_not_resumable` 避免盲目空转，同时保持 Codex 等可正常续接后端原有行为不变；补齐离线回归单测覆盖上述全部机制。

---

## [1.18.9] - 2026-11-23

依据 Google Antigravity 官方最新模型文档与四端本地实测目录，校准动态配置中的 Antigravity 客户端活跃上下文窗口口径并修补网页端文档遗留引用：

- **Antigravity 活跃上下文窗口口径校准（`fleet_profile.json`）**：
  - 将 `subscription_matrix.models` 中 `gemini-4-neon`、`gemini-4-carbon`、`gemini-4-hydrogen` 的 `context_window` 由底层原生 API 口径 `1,000,000` 校准为 Antigravity 各表面（App、IDE、插件及 CLI 有头与无头模式）实际共享且不可通过配置文件手改的活跃窗口上限 `512,000`（较此前约 `256,000` 自动压缩阈值翻倍、每轮压缩频率约降 3.5 倍）；
  - 联动收敛 `cae_harness.py` 无头单分片安全输入预算至 `460,800` tokens（避免跨过 512K 压缩墙），并将 `hooks/_lib/adaptive_policy.py` 对 Antigravity 的恢复预算缩放倍数由 `1.5×` 校准至 `1.25×`（`handoff_thresholds.antigravity` 检查点次数阈值维持 `3 / 5` 不变）；同步更新评估与决策日期至 `2026-11-23`。
- **公开文档引用修复与预算口径澄清（`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`、`tests/test_claude_and_router_offline.py`）**：
  - 修复 `docs/workflow-guide.md` 中仍指向 `README.md` 已移除角色表的陈旧引用，统一指向 `fleet_profile.json`；
  - 在 `docs/architecture-and-hooks.md` 的单分片安全输入预算说明中补充客户端实际活跃窗口与底层原生 API 窗口口径区分原则，并同步更新对应单测注释。

---

## [1.18.8] - 2026-11-22

基于真实多波次批处理超时与只读穿透失败样本的逐行根因排查，修复 `cae_harness.py` 五项核心缺陷并补齐 Julia 缓存快照忽略规则：

- **波次失败门控默认阻断后续只读波次（`cae_harness.py`、`SKILL.md`）**：
  - 将 `cmd_batch` 中同工作区（含父子目录关系）前序波次可写流失败后的阻断范围扩展至后续波次全部流（含只读审查流），避免前序产物未落盘时后续只读波次空转消耗配额；确需独立观察前序部分结果的只读流可通过流字段 `run_on_upstream_failure: true` 显式放行（`_resolve_plan_streams` 同步校验其布尔类型）。
- **软超时紧急收口自动降档与总超时字段校正（`cae_harness.py`、`SKILL.md`）**：
  - `invoke_engine` 在触发 `wrapup=True` 短预算只读收口时，为 `codex` 自动降至模型目录声明的最低可用非委派挡位（支持 `low` 时降至 `low`），为 `claude`/`dsh`/`agy` 降至 `low`，并记录 `wrapup_effort`，消除高推理挡位在大上下文下把 30–90 秒收口预算耗尽的问题；
  - 软超时及二次收口超时异常中将 `timeout_sec` 还原为真实总预算，同时保留 `primary_timeout_sec` 与 `wrapup_timeout_sec` 便于事后排查。
- **抢救轨迹防挤丢与 PowerShell 读文件解析补强（`cae_harness.py`）**：
  - `_session_trace` 将正式助手消息（`agent_message`/`assistant`）与短推理小标题（`reasoning`/`thinking`）分队列收集，优先保留正式回复，仅在无正式回复时回退到推理片段；
  - 补全 Windows PowerShell 读文件提取器 `_extract_shell_read_paths`，覆盖嵌套单引号转义、赋值/子表达式后的 `Get-Content` 与显式 `-LiteralPath`/`-Path` 的 `Select-String`，过滤 PowerShell 变量与管道误匹配。
- **单流显式模型保护、Windows Codex 沙箱边界提示、零改动显式标记与 `.julia` 快照忽略（`cae_harness.py`、`fleet_profile.json`、`tests/test_harness_offline.py`）**：
  - 修正 `choose_engine`：当 `--user-prompt` 仅泛指后端而未唯一锁定具体模型时，保留单流 `spec` 中已指定的同后端具体模型，不静默退回 `default`；
  - Windows AppContainer 与 WSL 互操作 `codex.exe` 在非净室首轮自动附带简短沙箱边界提示；可写 `code` 任务退出码为 0 且磁盘零改动时在回执中显式标注 `no_files_changed: true`；
  - 将 `.julia` 纳入 `snapshot_ignored_dirs`（显式声明 `.julia/...` 写入范围时自动取消忽略），消除 Julia 临时 depot 与预编译缓存触发的 `scope_violation` 误报，并补齐离线回归单测。

---

## [1.18.7] - 2026-11-22

基于 `gpt-6.2-astra` 与 `gpt-6.2-sol` 双路独立交叉复核及全历史 Git 对象扫描，完成路线 A 封板前安全加固、常驻规则瘦身与多文档失实校正：

- **本地路由远端明文 HTTP 阻断与异常日志收敛（`template/claude/router/router.py`、`tests/test_claude_and_router_offline.py`）**：
  - 在 `_validate_upstream_url`、`load_config` 与 `_forward_locked` 实施双层校验：当上游配置真实卡片密钥注入（`default_key_from_card` / `key_from_card`）时，向非回环远端主机使用明文 `http://` 一律拒绝（本地回环中继与离线回环测试继续放行）；
  - 将取卡失败与上游异常出口的日志 `err` 字段收敛为异常类型名（`type(e).__name__`），客户端 502 错误响应改为固定 ASCII 文案，消除原始异常文本泄露；同步更新 `router.py` 文件头日志范围说明并补齐离线回归单测。
- **常驻规则减负与跨文档重复及失实校正（`template/user-guard/rules/AGENTS.md`、`README.md`、`AGENTS.md`、`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`）**：
  - 按职责唯一归属重写精简 `template/user-guard/rules/AGENTS.md`（移交 14 项字段释义与脚本内部实现细节至技能和架构文档，保留安装器 `## 工具与运行环境`/`R10.`/`R11.` 锚点、数值验收红线与无技能必生效纪律，大幅降低日常对话上下文占用）；
  - 校正 `process_guard.py` 跨平台自毁上限表述（Windows 仅墙钟计时器，POSIX 按条件附加 `RLIMIT_AS` 虚拟地址空间上限，均在导入与读配置后生效）；校正 `README.md`“主体代码不写死模型名称”表述并移除时期性五角色模型快照表（改指向 `fleet_profile.json`）；移除 `docs/workflow-guide.md` 本地 `plan` 示例中与技能说明冲突的 `--redact` 参数及重复的 14 项字段长表。

---

## [1.18.6] - 2026-11-22

补齐第三方工具合规边界与纯官方按量接口选型说明，并结合 GitHub 网页端逐页渲染巡检修复 Mermaid 控件遮挡、窄视口表格竖排挤压与预算公式间距：

- **补齐外部工具安全边界、切卡防丢钩子与按量接口选型说明（`README.md`）**：在安装与配置章节明确区分官方 OAuth 登录与模型厂商官方按量 API Key（严禁用外部工具读写或轮换官方 OAuth 登录凭据、轮询非公开配额接口或向反重力同步全局提示词），补充使用 `ccswitch_admin.py switch-card` 切卡保护 `~/.claude/settings.json` 五类钩子的防覆写说明、未启用客户端 `enabled: false` 选路跳过及不污染主窗口的日常协作分工心法，并同步完善英文概览。
- **修复 GitHub 网页端 Mermaid 右下角遮挡、窄视口表格首列单字竖排挤压、预算公式间距及历史字面链接（`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`、`README.md`、`CHANGELOG.md`）**：将高风险模块四步协同 Mermaid 图由横向 `flowchart LR` 改为纵向 `flowchart TD`，彻底消除网页端右下角悬浮平移缩放控件对末步节点的遮挡；将三张四列表格精炼为三列布局，根除带左侧目录树的分栏视口下首列被挤压为单字竖条的问题；将预算展示公式中的 `\min\big(...\big)` 改为 `\min(W_{\text{eff}}, \space W_{\text{compact}})` 消除括号空隙与下标逗号粘连；并为历史更新日志中的字面链接语法补齐反引号包裹。

---

## [1.18.5] - 2026-11-22

补充修复架构说明文档在 GitHub 网页端的两处公式解析细节：

- **修复单分片预算公式在 GitHub 网页端的行内标识符跳过与转义符号残留（`docs/architecture-and-hooks.md`）**：拆分 `_execution_budget` 段落下标文本以避开 GitHub Markdown 公式解析器对属性同名英文词的跳过规则，并将整行展示公式改为多行独立块、移除会被行内提前转义的紧缩间距命令，确保网页端与本地渲染一致。

---

## [1.18.4] - 2026-11-22

基于 GitHub 与 Zenodo 线上页面逐页渲染核验，修复首页徽章裂图、网页端行内数学公式裸露及 SVG 架构流程图文案同步：

- **修复首页 DOI 徽章在 GitHub 图片代理下的超时裂图（`README.md`）**：将 `zenodo.org/badge/DOI/...` 慢响应图片源替换为与同排徽章一致的 `img.shields.io` 静态 DOI 徽章，保留 `10.5281/zenodo.24293507` 总概念 DOI 跳转链接不变。
- **修复 GitHub 网页端 6 处行内 LaTeX 公式未渲染（`docs/architecture-and-hooks.md`、`docs/workflow-guide.md`）**：补齐中文全角标点与行内 `$...$` 定界符之间的标准空格，消除 GitHub Markdown 解析器将紧邻全角括号与全角逗号的公式原样裸露为反斜杠文本的问题。
- **同步三张 SVG 流程图与压缩恢复说明（`docs/assets/*.svg`、`README.md`、`docs/architecture-and-hooks.md`）**：将三张 SVG 流程图中的日常确认说明统一更新为 3 句话极简确认条 `chat_brief` 与防重复刷票门禁，并理顺压缩恢复时“后台注入总览供模型核对”与“日常对话仅补展 1 次 `chat_brief`”的表述边界。

---

## [1.18.3] - 2026-11-21

基于 WSL2 重型科学计算课题会话（`V14 总控`）真实轨迹复盘，消除待批票据跨后台唤醒重复刷屏、Windows `codex.exe` 只读关闭内联盲读跳票与完工账单反引号误拦，并将日常对话确认展示精简为 3 句话极简确认条加本地明细文件链接：

- **日常对话 3 句话极简确认条 `chat_brief` 与后台唤醒防重复贴票（`cae_harness.py`、`reply_repeat_guard.py`、`status_guard.py`、`user_words.py`）**：
  - `cae_harness.py plan` 在保留 `<ticket_id>_card.md`（内含 4 列总览表与 14 项完整参数明细）、`compact_summary_card` 与 `compact_handoff_prompt_block` 供明细文件、压缩恢复及跨会话强制交接使用的同时，新增面向日常对话的 3 句话极简确认条 `chat_brief`（一句话目标与读写边界及各路模型挡位、可点击 `<ticket_id>_card.md` 链接、加粗确认动作提示），彻底取消日常聊天框贴表占窗；
  - `reply_repeat_guard.py` 在 `Stop` 阶段新增 `_check_repeated_pending_ticket_locked`：同一用户回合且未发生新 `CHECKPOINT` 压缩期间，若后台任务被动唤醒后再次输出同一 `PENDING` 票据的多行确认块或总览表，单次拦截并提示仅保留单行简短待办说明（跨压缩恢复、新用户提问或强制交接【新对话提示词】不受限）。
- **Windows AppContainer 及 WSL 跨子系统 `codex.exe` 只读内联前置硬校验与完工账单反引号兼容（`cae_harness.py`、`reply_repeat_guard.py`）**：
  - 新增 `_check_readonly_inline_transport`：当后端为 Windows AppContainer 或 WSL 跨子系统 `codex.exe` 且处于 `-s read-only` 只读模式（非 `followup` 续接）时，在 `plan` 出票前与 `invoke_engine` 启动前直接拒绝 `--no-inline-readonly`（状态码 `inline_readonly_required`，自动选路时自动排除该后端回退其他可用端），根治子进程读盘被沙箱 `blocked by policy` 拦截导致白跑作废重开新票的问题，并在回执记录 `inline_readonly` 实际生效布尔值；
  - 修复 `reply_repeat_guard.py` 完工账单核验正则未兼容数字与 `tokens` 之间带反引号或星号（如 `` `201,795` tokens ``）导致的误拦截重打。
- **规则、技能与全仓文档同步及离线回归用例增补（`AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`README.md`、`docs/`、`tests/`）**：
  - 同步更新 `R3.1.1`、`R3.1.5` 与 `R9.3` 对 `chat_brief` 展示边界及后台唤醒禁重贴票的规定，新增覆盖上述四项机制的离线回归测试。

---

## [1.18.2] - 2026-11-21

结合 GitHub Windows CI 失败诊断、本机生命周期门禁实证统计与四路 Codex 高强度交叉审查（经主会话逐行原文复核），完成跨平台路径修复、门禁降噪与会话目录续租、并发状态锁加固及常驻文档润色：

- **Windows 8.3 短路径暂存对比、引文反引号跨句吞噬与续接状态修复（`cae_harness.py`、`ci.yml`、`fleet_state.py`、`ccswitch_admin.py`）**：
  - 新增 `_is_resolved_within` 统一将候选路径与基准目录同时 `resolve()` 后比对包含关系（兼容循环链接 `RuntimeError`），根治 GitHub Actions Windows Runner `RUNNER~1` 8.3 短路径导致 `_session_trace` 与 `_citation_audit` 漏记已读文件的 CI 失败；将归档目录校验改为逐级 `_is_link` 检查，避免短路径别名误报；
  - 修复 `_citation_audit` 正则缺少闭合反引号导致同行代码标记与后续中文正文被跨句吞入 `missing_files` 的缺陷；修复 `needs_stage` 持久化暂存漏记 `session_plan_dir` 及 `cmd_followup` 将空 `session_plan_dir` 解析为工作区 `.` 的越界隐患；修正 Antigravity 非软时限分支未扣减前置准备耗时的超时计算；
  - 修复 `.github/workflows/ci.yml` 中 `bash -n` 仅校验首个位置脚本的语法检查遗漏；在 `fleet_state.py` 锁内刷新仓库元数据并在 `clear --verified --readback-done` 事实锚定后复核指纹无并发漂移；在 `ccswitch_admin.py` 事务内校验目标卡片 `ANTHROPIC_BASE_URL` 与锁前探活端点一致。
- **项目外变动门禁降噪、同会话批准续租与 `--files` 轻任务精确计量（`status_guard.py`、`hooks/_lib/shell_lex.py`、`handoff_gate.py`、`user_words.py`、`reply_repeat_guard.py`、`adaptive_policy.py`）**：
  - 在 `shell_lex.py` 抽取共用 `parse_readonly_git_prefix`，消除内联 Python `subprocess` 调用只读 `git` 子命令（含 `f"HEAD:{rel}"` 动态修订）、`os.O_RDONLY`、`http`/`https` URL `.open()`、完备 `if/else` 路径赋值及工作区内 `range`/`glob`/`rglob` 有界循环的 `<DYNAMIC_UNRESOLVED>` 假阳性弹窗；
  - 支持同一命令内 PowerShell（含 `Join-Path`、`[IO.Path]::Combine` 与尾逗号清理）和 Bash 简单局部变量传播，补齐 Windows 下 Claude Code Git Bash（MSYS `/<drive>/...` 与 `/tmp/...`）路径映射及本地标准包缓存白名单；
  - 新增基于会话轨迹结算验证的同会话外部目录批准租约（`_reconcile_and_check_boundary_lease_locked`）：对已存在的非系统外部业务目录常规文件编辑及 `install.py` 全局同步，经用户首次批准并核实工具成功落地后本会话内自动续租，而新建外部永久目录、外部删除移动、外部 `git worktree`、系统或客户端全局配置文件直改及无头子任务继续保持严格拦截；
  - 对齐 `plan`、`PreToolUse` 与 `Stop` 对单路只读 `--files` 的精确文件计量（默认最多 4 个小文件免票静默执行）；修复 `handoff_gate.py` 子标题票据核验截断、`user_words.py` 按 `run_id` 精确销账防并发漏报及 `adaptive_policy.py` 精确模型 ID 优先于去后缀匹配的顺序。
- **常驻规则与公开文档一致性润色及回归测试增补（`AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`README.md`、`docs/`、`tests/`）**：
  - 同步更新 `R4` 与 `R3.1.5` 说明，修正超时抢救与前缀缓存的绝对化表述及 `clean_room` 后端示例，新增覆盖上述全部跨平台与门禁降噪场景的离线回归测试。

---

## [1.18.1] - 2026-11-21

针对 WSL2 科学计算长会话取证暴露的伪终端分页器死锁、单回合跨多次压缩未收尾及钩子源码自省空耗，补齐三项确定性硬护栏：

- **伪终端裸分页 Git 命令拦截与 `systemd-run` 包装拆壳（`status_guard.py`）**：将 `systemd-run` 纳入多层命令包装拆壳，并在 `PreToolUse` 拦截未显式加 `--no-pager` 或 `-P`、未设置 `GIT_PAGER` 或 `PAGER` 且标准输出直接连向伪终端的裸 `git log`、`git show`、`git diff`、`git reflog`、`git shortlog`，根除 `/usr/bin/pager` 在 `TERM=dumb` 伪终端无限等待按键挂死后台任务的问题。
- **单回合连压失控与完整轨迹体积收口兜底（`handoff_gate.py`、`fleet_profile.json`）**：新增 `drain_turn_cps`、`drain_turn_steps`（单用户回合内连续跨多次 `CHECKPOINT` 且步数超限触发 `turn_runaway`）与 `drain_full_transcript_mib`（`transcript_full.jsonl` 体积超限触发 `full_transcript_bytes`）两项 `Stage-2 drain` 触发器，并将检查点步数间隔配置键统一为 `*_avg_interval_steps`。
- **通用课题会话钩子源码自省拦截（`status_guard.py`）**：在 `general_project` 模式下拦截主会话无谓读取已安装 `hooks/*.py` 逆向猜测门禁规则，直接返回单行明确收尾提示以节省上下文窗口。

---

## [1.18.0] - 2026-11-21

基于 Windows 与 WSL2 多课题真实会话轨迹的跨端实证审计，完成无头子代理持久化可续接会话与双层零丢失超时抢救、多文件输入与波次调度、钩子进程系统级资源自毁护栏、局部补测冒充全过拦截、交接未决拍板保护、压缩去噪与常驻规则精炼升级：

- **默认持久化可续接无头会话、双层零丢失超时抢救与精确输入输出信封（`cae_harness.py`、`fleet_profile.json`）**：
  - 将 `run_process` 升级为实时逐行守护管道读取并默认注入防分页与无头 MPI 非交互环境变量（`GIT_PAGER=cat`、`PAGER=cat`、`LESS=-FRX`、`GCM_INTERACTIVE=never`、`HWLOC_COMPONENTS=-gl`），在超时、取消与非零退出时保留已输出的 `partial_stdout` 与 `partial_stderr`；
  - 默认开启四端无头会话持久化（`persist_sessions_by_default=true`，新增显式 `--ephemeral` 关闭开关），记录 `provider_session_id` 并在隔离临时目录清理前归档会话轨迹至 `<plan_dir>/headless_sessions/`，新增 `cae_harness.py followup` 子命令支持按回执续接原会话执行精准追问并校验快照边界；
  - 实现双层超时恢复：第一层在长任务触及软时限（`soft_timeout_ratio`）时优先提取已完成结果或利用剩余预算发起只读续接收口（`soft_timeout_wrapup`）；第二层在硬超时下从实时流、会话日志与前后快照差分确定性提取已完成轮次 Token（`cli_partial_on_timeout`）及 `timeout_salvage`（含 `provider_session_id`、`resumable`、`actual_files_read`、`changed_files_on_disk` 与最近回复片段）；
  - 为 `task`、`plan`、`batch` 新增 `--files` 跨子目录多文件精确暂存、`--negative-scope-file` 负向边界与 `env_contract` 环境约定，前置拦截 Windows 互操作后端写入 WSL 纯 Linux 路径；新增只读回执 `citation_audit` 行号与读取轨迹核验；为 `cmd_batch` 增加 `wave` 波次顺序与同根目录不重叠 `write_scope` 安全并发；为 `_build_grounding_anchor` 增加 `grounding_max_anchor_file_mib` 大文件首尾采样与 `grounding_excluded_dirs` 工具链及归档目录排除，根治超大结果 JSON 引发的 `MemoryError`。
- **钩子进程资源自毁护栏、压缩恢复去噪与回合结束诚实性硬门禁（`hooks/_lib/process_guard.py`、`status_guard.py`、`session_context_loader.py`、`user_words.py`、`reply_repeat_guard.py`、`handoff_gate.py`、`install.py`）**：
  - 新增 `hooks/_lib/process_guard.py` 并纳入 `install.py` 分发：四个会话钩子在命令行入口按 `loop_guards.hook_safety` 设置 POSIX `RLIMIT_AS` 内存上限与 `SIGALRM` / Windows 守护计时器墙钟自毁护栏（进程内调用退出自动注销计时器），防止异常钩子进程长期驻留消耗内存；
  - 在 `user_words.py` 过滤后台异步启动提示、权限拒绝短文本与看板通知噪音，同时在压缩归档中保留 `timeout_salvage` 关键摘要；在 `status_guard.py` 将 `followup` 纳入重负载与票据校验；
  - 在 `reply_repeat_guard.py` 新增局部补测冒充全套测试通过门禁（识别 `unittest`、`pytest` 与 Julia 套件统计）及未全胜账单写成“全部成功”的防翻案门禁；在 `handoff_gate.py` 新增落盘交接稿待拍板占位符（如 `[请在此填入]`）防越俎代庖篡改门禁。
- **常驻规则与技能精炼重写及全链路回归单测（`AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`README.md`、`docs/`、`tests/`）**：
  - 在不增加字节体积、不稀释模型注意力的前提下整篇精炼更新常驻规则、双技能、主说明与架构及实战指南，补充覆盖双层超时抢救、`followup`、波次并发、资源护栏、局部补测拦截与交接防伪的离线与端到端回归测试。

---

## [1.17.6] - 2026-11-20

完成全仓运行期与诊断日志脱敏一致性收口、批量模式润色事实锚定与验收环境隔离补齐、跨运行侧状态锁防死锁加固、CC Switch 数据库基线升级及常驻文档书面语润色：

- **运行期与诊断日志全链路脱敏对齐（`cae_harness.py`、`install.py`、`user_words.py`、`router.py`）**：
  - 修复 Python 3.13 下 `_redact_public` 对 RFC 6598 共享内网段与 `fe80::/10` 链路本地 IPv6 后半段漏脱敏的问题，并在 `<HOME>` 兜底替换中保护 Markdown 表格竖线与括号分隔边界；
  - 新增 `_split_url_trailing_punct` 保留 Markdown 链接、圆括号与引号包裹带端口 URL 的外侧闭合符，将 `user_words.py` 与 `router.py` 的 URL 凭据擦除扩展至 `ws`、`wss`、`socks4`、`socks5` 及通用协议前缀，统一补齐 PEM 私钥块、JWT、`ya29.`、`ASIA`、`xox*`、`Basic`、`Token`、`ApiKey` 以及 `id_token`、`sessionToken`、`passwd`、`passphrase`、`cookie` 与纯数字密码脱敏。
- **批量编排逻辑、状态机边界与 CC Switch 基线更新（`cae_harness.py`、`fleet_state.py`、`handoff_gate.py`、`session_context_loader.py`、`ccswitch_admin.py`）**：
  - 抽取共用 `_compute_polish_grounding_audit` 接入 `cmd_batch` 的 `polish` 流，并为 `cmd_batch` 可写流 `verify_cmd` 子进程补齐剥离 `_GIT_REPO_ENV_VARS` 与父会话环境变量及注入 `CAE_HARNESS_DEPTH=1`；
  - 在 `fleet_state.py` 的 `propose`、`record-pass1` 与 `clear` 中统一核验 `execution_side` 与仓库配置指纹对齐，避免跨运行侧或已漂移旧记录造成审批死锁；在 `handoff_gate.py` 将 `--cancel-ticket` 与 `--discover-models` 归入写状态子命令，并将 `ccswitch_admin.py` 的 `TESTED_MAX_SCHEMA` 升级至经实库列结构核验的 `27`。
- **常驻文档书面语清理与回归单测增补（`README.md`、`AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`docs/`、`tests/`）**：
  - 清理 7 份常驻说明与规则文档正文中的连续斜杠串并同步脱敏与批量润色锚定说明，新增覆盖上述全部修复项的离线回归单测。

---

## [1.17.5] - 2026-11-20

融合 WSL2 Linux 原生 Antigravity 桌面版无进程版本体检，并结合五路跨端全仓交叉审查完成 Shell/Python AST 安全边界加固、回环路由跨域防护、配置体检零字节码写入收口、重复代码去重与常驻指南去模型名润色：

- **WSL2 Linux 原生 Antigravity 桌面版识别与无进程版本审计（`install.py`、`fleet-evolve/SKILL.md`、`ci.yml`）**：
  - 扩展 `detect_installed_apps` 与 `_probe_windows_wsl` 探测 Linux/WSL2 原生桌面端路径（含 `/usr/local/bin/antigravity`、`/usr/share/antigravity/antigravity`、`/opt/antigravity/antigravity` 及 `~/.config/Antigravity`）并严格剔除 `/mnt/` 跨盘软链；新增 `_read_desktop_app_version` 按 `product.json`（`ideVersion`）优先于 `package.json` 及 `app.asar` 二进制索引纯文件提取 `desktop_version`；
  - 统一 `install.py` 与 `ccswitch_admin.py` 对 `adaptive_policy.py` 的无缓存 `exec(compile(...))` 加载，修复未带 `-B` 运行 `--audit-upgrade` 时向仓库写入 `__pycache__` 的漏口，兼容含 UTF-8 BOM 的钩子配置文件比对，并在 CI 增补无 `-B` 零字节码写入断言。
- **钩子词法/AST 边界、长会话收口阈值标定、回环路由与编排票据状态机加固（`hooks/`、`fleet_profile.json`、`cae_harness.py`、`fleet_state.py`、`router.py`）**：
  - 校准 `handoff_thresholds.antigravity` 收口阈值（`advisory_cp_threshold=3`、`drain_cp_threshold=5`、`advisory_transcript_mib=3`、`drain_transcript_mib=5`、`advisory_thrash_min_cp=3`、`drain_thrash_min_cp=4`、`drain_media_min_cp=3`），并在 `adaptive_policy.py` 中取消对 `advisory_cp_threshold` 与 `drain_cp_threshold` 的窗口系数二次放大（保留 `transcript_mib` 按窗口缩放），解决百万窗口长会话多次 `CHECKPOINT` 压缩后仍不触发收口提醒的问题；
  - 修复 `_lib/shell_lex.py` 未识别非引号 `#` 行注释导致注释内单引号跨行吞并子句绕过门禁的问题，消除 `_INSTALLER_INVOKE_RE` 嵌套量词回溯，收窄 `_COMMIT_MSG_RE` 重定向边界，加固 `status_guard.py` 内联 Python AST 对通配导入、动态导入、`exec`/`eval`、类方法与容器下标函数、`_AstDict` 原地变更及归档落盘接口的检查，为 Claude `Grep` 证据扫描补齐花括号展开与目录遍历上限，并修正 `bash -v -c` / `-o pipefail -c` 拆壳与冻结期动态重定向目标拦截；
  - 在 `router.py` 增加回环 `Host` 校验、`Origin` / `Sec-Fetch-Site` 跨站拦截及 `//` 与 `..` 路径穿越拒绝，同步 Windows 重解析标签精确判定以避免云同步占位符误触发；在 `cae_harness.py` 补齐非 `fast` 默认选路对轻量卡片的过滤、正向单端 P0 指令在双盲 `audit` 下的同端锁定、终态票据（`CANCELLED` / `SUPERSEDED`）防静默复活、部分只读内联提示头区分与多处重复逻辑合并。
- **常驻指南去模型名重写与静态脱敏门禁加固（`docs/`、`tests/`）**：
  - 重写 `docs/workflow-guide.md` 日常无感分流与 4 类特殊口语速查表，消除时期性模型代号硬编码并纳入 `SEASONAL_MODEL_PATTERN` 静态检查，合并精简 `docs/architecture-and-hooks.md` 重复的 14 项参数长表；将 `test_e2e_integration.py` 良性主机词元改为静态受控集合以杜绝源码自豁免盲区，补齐运营商级共享内网段与 IPv6 私有地址静态脱敏扫描及全链路回归单测。

---

## [1.17.4] - 2026-11-20

沉淀认识论校准与硬验证器闭环双轴评估方法论、日常无感与模糊口语编排规范，并补齐 `native_swarm` 异构验收保护及单端受限时的同端异构双盲审查：

- **双轴正交评估准则与日常无感 / 模糊口语编排落位（`fleet-evolve/SKILL.md`、`cae-harness/SKILL.md`、`docs/workflow-guide.md`、`README.md`、`fleet_profile.json`）**：
  - 在 `fleet-evolve/SKILL.md` 与 `fleet_profile.json`（`evaluation_policy.defect_veto_priority`）确立两条正交评估轴：无确定性验证器场景下的审慎拒答校准（无工具幻觉率与拒答率拆解，决定未闭环理论总控、论文定稿与正向立论）与有硬验证器约束下的代理执行及反例挑刺吞吐（决定受控编码、并行实现与对抗挑刺）；
  - 在 `cae-harness/SKILL.md` 增补无感分流、早期探索与定纲收口分离、未闭环数理正反双流协同三条无模型名编排定式；在 `docs/workflow-guide.md` 与 `README.md` 同步公开脱敏的课题阶段选型与日常模糊口语速查矩阵。
- **`native_swarm` 异构验收保护与单端受限 `audit` 异构双盲回退（`cae_harness.py`、`fleet_profile.json`、`tests/`）**：
  - 在 `native_agent_profiles.antigravity` 新增 `swarm_verifier_model_family`（`carbon`）、`swarm_verifier_tier`（`high`）与 `swarm_verifier_kinds`（`review`、`math`）；当宿主前端模型处于非 `carbon` 系列（如成熟工程仓选用 `neon` 主控）时，`plan --channel native_swarm` 对验收与数理流自动推荐异构低幻觉 `carbon` 系列（宿主已为 `carbon` 或合成测试夹具时保持 `inherit`）；
  - 新增 `_same_backend_heterogeneous_right` 接入 `_audit_engines`（同步增强 `_extract_negated_backends` 对顿号/连词并列排除与“只用/仅用”排他口语的识别）：当其余跨端后端均未就绪、处于配额冷却或被提示词排除且未显式指定 `--right` 时，`audit` 自动配对同客户端内的 `deep_families` 与 `code_families` 组建异构双模型审查，并补充离线回归单测。

---

## [1.17.3] - 2026-11-20

结合长周期科研总控与高频编码代理的评测及实战边界，完成 Antigravity 主控与内外子代理编排专长分流优化：

- **元配置驱动的 `agy` 无头专长选模与内部 `native_swarm` 编码分流（`cae_harness.py`、`cae-harness/SKILL.md`）**：
  - 新增无模型名硬编码辅助函数 `_pick_agy_tagged_family`，在 `resolve_execution_profile` 中对 `code`/`gate` 优先匹配 `client_catalog_rules.agy.code_families`，对 `math`/`direction` 及高复杂度 `review` 优先匹配 `deep_families`（未配置或未命中时回退既有 `family_priority` 与非委派最高强度选择），修正此前深度推导与高复杂度审查仅按最高强度后缀而误选高速编码系列的问题；
  - 新增 `_native_swarm_worker_model` 并在 `_resolve_plan_streams` 及 `--approve-ticket --override-json` 中接入：当 `native_swarm` 在 `native_agent_profiles` 中配置了 `swarm_worker_model_family` 与 `swarm_worker_kinds`（`code`、`gate`）且未显式指定 `--model` 或用户模型点名时，确认单自动推荐对应高速编码模型（默认 `swarm_worker_tier`，支持挡位微调且审批无漂移），而 `review` 验收代理、`invoke_subagent` 与 `run_workflow` 保持 `inherit` 继承主控模型。
- **动态角色矩阵与内生代理档案更新（`fleet_profile.json`、`README.md`、`tests/`）**：
  - 将 `roles.orchestrator`、`native_agent_profiles.antigravity.main_model_family` 与 `client_catalog_rules.agy.family_priority` 首位更新为低幻觉 `carbon` 系列（`high`），配置 `deep_families: ["carbon"]`、`code_families: ["neon"]`，并将 `agy:@code`（自动路由 `neon` `xhigh`）提升至 `roles.code.candidates` 跨端编码第一备选；
  - 在 `native_agent_profiles.antigravity` 登记 `coding_agent_model_family`（`xhigh`）与 `swarm_worker_model_family`（`high`）及 `swarm_worker_kinds`，同步 `README.md` 五角色示例表，将 `neon` 纳入常驻文档禁硬编码检查，并新增完整外部无头与内部 `native_swarm` 审批/微调回归测试。

---

## [1.17.2] - 2026-11-20

修复运行态子代理并发计数、AST 静态审查指数膨胀、大目录全盘扫盘与多路选项卡重复注入等缺陷，并合入远端跨环境兼容修复（Windows `CC Switch` 桌面识别、WSL2 代理编码容错与 Python 3.14 深层 JSON 嵌套预检）：

- **钩子并发计数、AST 防挂死与完工账单修复（`status_guard.py`、`reply_repeat_guard.py`）**：
  - 修复 Antigravity 无 `call_id` 并发 `invoke_subagent` 计流与拒绝项排除逻辑：排除已被 `deny` 的历史调用，按当前待决批次投影计数，避免获批多路并行子代理被误拦截或单路轻任务误判超限；
  - 归并 `_merge_ast_value_sets` 中跨分支 `_AstDict` 候选消除指数膨胀，新增 `_AST_MAX_VISITED_NODES` 与 `_AST_MAX_SCAN_SEC` 硬预算护栏（`_AstScanBudgetExceeded`），杜绝内联脚本审查卡死和内存膨胀；
  - 注册 `gzip.open`、`bz2.open`、`lzma.open`、`codecs.open`、`tarfile.open` 读写判定并豁免 `Image.open`、`webbrowser.open`，消除只读压缩流误拦截；改为流式遍历 AST 使 `chdir` 追踪即时生效；
  - 修复 `reply_repeat_guard.py` 在 Antigravity 无 `call_id` 场景下的拒绝槽位对齐与 `run_workflow` 后台任务回执匹配。
- **无头编排器解析、脱敏、混合批处理与大目录扫盘优化（`cae_harness.py`、`cae-harness/SKILL.md`）**：
  - `_dsh_supports_cli_permission_flags` 增加线程锁与合理探测超时；`extract_cli_token_usage` 补齐 DSH `cacheReadTokens`、`cacheWriteTokens`、`uncachedInputTokens` 统计并校验总 Token 防重复计数；
  - 新增保守尾部闭合修补 `_repair_unclosed_json_tail`，在 `OutputParseError` 时附带脱敏 `stderr`、`raw_output_excerpt` 及 `agy` 软拒绝诊断提示；`agy` 只读模式在 Git 仓库子目录自动追加 `--add-dir`；DSH 提示追加单模型直接完成约束；
  - `_redact_public` 按额度键与局部上下文脱敏用量百分比，保护 `result` 与科学正文中的数值百分比；`cmd_batch` 支持混合通道票据（执行无头流并回报 `skipped_native_streams`），补传 `effort`、`retry_quota` 与安全防递归提示包装；
  - 优化 `collect_target_files` 与 `_target_metrics`：大目录体量估算仅读 `lstat` 元数据、遵循 `.agentignore`、支持 `max_files` 上限与同批缓存，可写流优先按具体 `write_scope` 估算，消除大算例目录反复打开文件造成的磁盘 I/O 尖峰。
- **选项卡上下文去重与跨平台恢复闭环（`cae_harness.py`、`session_context_loader.py`、`handoff_gate.py`、`user_words.py`）**：
  - 已落盘票据的 `plan` 默认输出精简 stdout（置顶 `compact_summary_card`，省略已落盘 14 项长表与 `execution_profile`，需要完整结构时加 `--full-json`）；新增 `plan --cancel-ticket`，`--approve-ticket` 自动将同会话更早且通道重叠的待决票据标为 `SUPERSEDED`；
  - 消除 `session_context_loader.py` 与 `handoff_gate.py` 在同一步骤的双重卡片注入，连续同阶段 `PreInvocation` 改为单行提醒；修复 `collapse_historical_cards` 空行双占位符并支持折叠文本与 Markdown 围栏内的 14 项表（保留代码围栏与科学表格）；补齐 `render_pending_workflow_block` 的 Windows 与 WSL 跨平台明细卡路径解析，并在 `handoff_gate.py` 兼容 `*_steps` 阈值别名。
- **跨环境安装探测与 Python 3.14 深度预检（`install.py`、`fleet_state.py`、`tests/`）**：
  - `install.py` 将 Windows `cc-switch.exe` 归入 `desktop` 列表并兼容含空格的 `Programs/CC Switch` 与 WSL `/mnt/c/Program Files` 路径，避免 `_audit_cli` 拉起桌面程序写入默认提示词；为 `_probe_windows_wsl` 补充 `errors="replace"`；
  - `fleet_state.py` 新增 `_check_json_nesting_depth` 并在 `_load_state`、`_default_ttl_seconds`、`_repo_meta` 三处 `json.loads` 前统一预检，兼容 Python 3.14 Linux 栈行为；四套离线测试文件补齐全部回归用例。

---

## [1.17.1] - 2026-11-20

完成精简 4 列总览卡与可点击 `<ticket_id>_card.md` 明细链接闭环，开展多模型全仓深度只读审计与主会话逐行复核，修复调度器、四阶段生命周期钩子、安装器、状态锁、本地分流代理与 WSL2 脚本中的全部已核实 P0/P1/P2 隐患，并全面重写同步主说明、指南、架构文档与三张 SVG 流程图：

- **精简 4 列总览卡与 `<ticket_id>_card.md` 明细链接统一机制（`cae_harness.py`、`user_words.py`、`session_context_loader.py`、`handoff_gate.py`、`status_guard.py`、`AGENTS.md`、`SKILL.md`）**：
  - 统一四端共用机制：`_save_plan` 生成带可点击 `` `[<ticket_id>_card.md](file:///...)` `` 链接的 `compact_summary_card` 与 `compact_handoff_prompt_block`，对话展示、压缩恢复与强制交接提示词默认仅呈现 4 列总览表与明细链接（校验 `_card.md` 真实落盘且非链接，仅未落盘时回退 14 项长表），彻底消除多路 14 项长表刷屏污染主上下文；
  - 新增 `find_session_pending_tickets` 覆盖多张未决票据恢复，`handoff_session_pending_tickets` 幂等同步 `HANDED_OFF` 卡片文本，`handoff_gate.py` 按单票据消耗匹配行消除多票据 `S1` 混叠，`status_guard.py` 阻断文案附带可点击明细链接。
- **调度器 `cae_harness.py` 票据安全与跨端隔离加固**：
  - 修复 `_check_ticket_execution` 可写流 `verify_cmd` 双向严格校验，拦截空字符串或 `None` 绕过；修复 `plan --resume-ticket` 在未传 `--force` 时对已批准票据的 `user_prompt` 保留、核心规格漂移拦截及内存只读刷新；将锁名空间收口为 `ticket_<id>` 消除 `plan_dir` 自锁；
  - 补齐 `_resolve_effective_tier` 长篇 `polish` 文件/Token 传递、单文件可写流 `write_scope` 自动填充、`--override-json` 换端后可用性字段同步；补齐 Claude `hooks_only` 凭据复制与 `clean_room` 跨端环境变量隔离、DSH 互操作环境透传与 `.cmd` 探测、`cmd_audit --isolation clean_room` 指令文件预过滤及 `staged_scope` 合并、`cmd_batch --isolation` 票据核验、Claude 失活本地路由拒绝、`agy` 桌面优先 `inherit` 解析及连字符敏感请求头/代理 URL 脱敏。
- **四阶段钩子与 `_lib/` 跨平台安全及 PowerShell 词法加固（`fs_safety.py`、`shell_lex.py`、`status_guard.py`、`reply_repeat_guard.py`、`handoff_gate.py`、`session_context_loader.py`、`adaptive_policy.py`）**：
  - `fs_safety.py` 对齐 Windows `st_reparse_tag` 区分符号链接/挂载点与云盘占位目录，并在 Windows `EACCES` 锁竞争时重试；`shell_lex.py` 增加 `backslash_escape=False` 适配 PowerShell 引号规则；
  - `status_guard.py` 与 `handoff_gate.py` 补齐 PowerShell `-e`..`-encodedcommand` / `-c`..`-command` 最短唯一前缀缩写与双轨反斜杠扫描，在提交信息掩码前提取命令替换，拦截 `>&/outside`、PowerShell 冒号路径参数、GNU `-t<path>`、安装器路径前缀包装、`builtins.open` 别名、内联危险模块及只读子代理构建/网络命令，并在 `reply_repeat_guard.py` 补齐 `continuationActive` 静默销账对账、负偏移 EOF 游标与大小写不敏感词边界匹配。
- **状态锁、安装器、本地分流代理、WSL 脚本与 CI 加固（`fleet_state.py`、`install.py`、`router.py`、`ccswitch_admin.py`、`launch-cc-switch-wsl.sh`、`update-cc-switch-wsl.sh`、`ci.yml`）**：
  - `fleet_state.py` 为 `clear` 新增可选 `--session-id` 解决 Pass 2 已认领锁的正常归位死锁，对齐 `st_reparse_tag` 与硬链接校验并统一 `AWAITING_USER_APPROVAL` 漂移语义；
  - `install.py` 修复 Codex 已有模型与挡位时无法补齐缺失 `model_context_window` 的早返回缺陷，修复 `audit_upgrade_status` 异常捕获与路径层级、`_probe_dsh_hook_capability` 退出码校验与 `upgrade_dsh_hook_protocol` 推荐动作、`cross_side_status` 未启动 WSL2 提示与 `readlink -f` 解析、`_select_agy_frontier_max_label` 最高代数选择、`_audit_redact` 私网 IPv6/共享内网段/代理 URL 脱敏、规则尾段扫描跳过代码围栏及无 `__pycache__` 加载 `adaptive_policy.py`；
  - `router.py` 在 `load_config` 强制回环地址监听并支持 IPv6 `::1`，收紧 `_request_target_path` 防止非 `/` 路径拼接跨主机请求伪造、拒绝上游 URL 凭据/查询/片段、拆分 `do_DELETE`、精确匹配 `/__router/health` 并规范 `Content-Length`；`ccswitch_admin.py` 单次读取 `settings.json`、将路由健康探测移出 SQLite 写锁、以 `0o600` 原子写备份、在回滚失败时保留备份，并在损坏 `settings.json` 时仍探测 `router_health`；`update-cc-switch-wsl.sh` 与 `launch-cc-switch-wsl.sh` 补齐符号链接拦截、`.deb` `0644` 权限、`dpkg-query` 失败保护、`umask 077` 日志与 `/proc/<pid>/exe` 校验；`.github/workflows/ci.yml` 补充 `errors="replace"` 与 Linux `install.sh --probe-apps` 冒烟校验。
- **全仓用户文档、三张 SVG 流程图与技能全面对齐（`README.md`、`docs/`、`template/user-guard/README.md`、`AGENTS.md`、`SKILL.md`）**：
  - 更新 `README.md` 五角色示例表（`orchestrator` 同步为 `neon` 系列）、更正 `check-grounding --strict` 跳过 `tests/` 与 Markdown 代理契约的说明，依据净室盲读意见补齐全部行话解释并修正逻辑跳步；同步重写 `docs/workflow-guide.md`、`docs/architecture-and-hooks.md`、`template/user-guard/README.md` 与三张 SVG，补全精简 4 列总览卡与明细链接、`--isolation` 四级正交隔离、`native_agent_profiles`、`swarm add` 计流、`cross_side_status` 与 DSH `hook_protocol`，移除架构文档中固化本机派生窗口数值，并同步全仓 `AGENTS.md` 与双技能细则。

---

## [1.17.0] - 2026-11-19

补齐多维正交隔离参数 `--isolation`、独立内生代理档案 `native_agent_profiles`、`swarm` 并发流数精确计费与 Windows/WSL2 双端体检，并完成四客户端 `fleet-evolve` 全面更新与本机无头实测：

- **正交隔离级别与内生代理独立建档（`cae_harness.py`、`user_words.py`、`cae-harness/SKILL.md`）**：
  - 在 `task`、`gate`、`audit`、`plan`、`batch` 及 `--spec-file` 新增 `--isolation`（`full`、`no_skills`、`hooks_only`、`clean_room`），与后端、模型、挡位、读写模式解耦并保持 `--clean-room` 兼容；为 Claude `clean_room` 补齐 `--disable-slash-commands`，对 `agy` 的 `hooks_only`/`clean_room` 及 `dsh` 当前 `soft_rules_only` 状态的 `hooks_only` 显式报错拒绝；确认单第 9 项对显式指定的非净室隔离模式追加标注并在持票执行时校验一致性；
  - 新增 `_native_agent_profile` 读取 `fleet_profile.json` 中独立的 `native_agent_profiles`，并在 `_native_plan_model` 解析 `inherit` 时优先识别 Codex `agents/*.toml` 与 Claude/Antigravity 代理前置元数据中的显式 `model`；在 `PHYSICAL_SUBSTANCE_TAGS` 补充 `neon` 以防气体组分提示误触发模型点名。
- **并发流数计费与双端安装体检加固（`status_guard.py`、`install.py`、`fleet-evolve/SKILL.md`）**：
  - 在 `status_guard.py` 新增 `_native_call_stream_weight`，对 `swarm` 的 `add` 调用按 `tasks` 列表长度或正整数 `n` 精确计入通道票据容量；
  - 在 `install.py --audit-upgrade` 保留 `dsh` 的 `hook_protocol`、`hard_hooks_supported`、`meta_update_trigger`，新增 `cross_side_status` 与 `fleet_profile.native_agent_profiles_configured`；对 `antigravity-cli/settings.json` 与 `cae_models_cache.json` 的只读探查放行同步盘临时硬链接（写入目标与符号链接仍严格拦截）；在 `fleet-evolve/SKILL.md` 补齐四端工具、双端同步、内生代理与跨端协同四维盘点规程。
- **动态能力矩阵全面更新与本机实测（`fleet_profile.json`、`tests/`）**：
  - 按 `fleet-evolve` 审定结果在 `fleet_profile.json` 新增 `native_agent_profiles`，更新 `client_catalog_rules.agy.family_priority`、`roles` 五槽位与 `subscription_matrix`（新增并启用 `gemini-4-neon`、`gpt-6.2-sol`、`gpt-6.2-astra`，保留 `gemini-4-carbon`、`gpt-6.1-sol`、`gpt-6-astra` 兼容点名，维持 `k3.1-pro[1m]` 停用），并完成四客户端轻量只读无头实测与双端 `unittest` 回归。

---

## [1.16.0] - 2026-11-17

重构共享钩子基础库 `hooks/_lib/`、实现按客户端与模型/推理强度动态自适应的压缩与交接策略、新增确认单 `<ticket_id>_card.md` 写入与历史确认单折叠（`collapse_historical_cards`），并加固 `cae_harness.py` 票据并发锁与 `fleet_state.py` 锁/序列化边界：

- **共享钩子基础库与模型/强度自适应策略（`hooks/_lib/`、`session_context_loader.py`、`handoff_gate.py`、`ccswitch_admin.py`、`install.py`）**：
  - 将跨端钩子共用的原子写、符号链接/重解析点校验与跨平台文件锁抽离至 `hooks/_lib/fs_safety.py`，将 Shell 命令词法拆解与 ANSI-C 引号解码抽离至 `hooks/_lib/shell_lex.py`，并由 `hooks/_lib/adaptive_policy.py` 基于 `fleet_profile.json` 按 `(client, model, effort)` 动态派生压缩恢复字符预算与交接阈值（`resolve_adaptive_compaction_policy`、`resolve_adaptive_handoff_policy`），同步至 `ccswitch_admin.py` 自适应路由卡片默认参数与 `install.py --audit-upgrade` 的 `adaptive_meta` 体检；
  - 拓展 `install._probe_dsh_hook_capability`，当上游 DSH 二进制开放 `--hooks` 协议时自动标记 `ready_for_fleet_evolve_dsh_hooks` 元更新触发信号。
- **确认单精简卡片与历史表格折叠（`cae_harness.py`、`user_words.py`、`fleet_profile.json`）**：
  - `_save_plan` 先原子写入 `<ticket_id>.json` 再写入完整 14 项明细文件 `<ticket_id>_card.md`（未保存票据的计划不输出 `--approve-ticket`/`--resume-ticket` 命令），`_render_plan` 同步生成精简摘要 `compact_summary_card`（缺失字段标为 `未声明`）；`user_words.collapse_historical_cards` 在压缩恢复时自动折叠历史 14 项明细表为 `[已折叠历史确认单/账单明细]`；
  - 为 `cmd_plan` 的票据读取-修改-写盘周期增加 `plan_dir` 文件锁（`_plan_dir_lock`），在 `_load_ticket` 强制校验票据与流必填键，并拒绝未经 `--force` 对 `APPROVED` 票据执行 `--mark-handed-off` 或状态降级；移除 `fleet_profile.json` 中 `peer_fallback_order.dsh` 的自指项 `"dsh"`。
- **状态锁与原子写安全加固、CI 与文档同步（`fleet_state.py`、`AGENTS.md`、`README.md`、`docs/`、`ci.yml`、`tests/`）**：
  - 修复 `fleet_state.py` 中 `_atomic_write_json` 先序列化 JSON 再创建临时文件以防异常泄漏句柄，`_state_lock` 改用 `os.open(..., O_NOFOLLOW)` 与 `(st_dev, st_ino)` 复核且仅对新建叶目录执行 `chmod(0o700)`，将 `now = int(time.time())` 移入 `_state_lock` 临界区内采样，并收窄 `_run_repo_grounding_gate` 异常捕获范围；
  - 在 `AGENTS.md` 明确三层仓库治理边界（`hooks/_lib/adaptive_policy.py` 归入 `项目主体代码层`，`项目自更新层` 仅含 `fleet_profile.json`、`fleet-evolve` 与 `install.py --audit-upgrade`），同步更新 `README.md`、`docs/`、`template/user-guard/README.md`、`cae-harness/SKILL.md`、`.github/workflows/ci.yml`（新增 `bash -n` 语法校验与干净仓库 `unittest` 回归）及 `tests/test_claude_and_router_offline.py`。

---

## [1.15.2] - 2026-11-17

修复无头回执未转义 LaTeX 公式控制符损坏、`.agentignore` 前置锚定与同工作区并发快照竞态、命令包装与 Codex 补丁写边界绕过、路由器截断请求体拦截与 WSL2 NAT 代理回退、POSIX `flock` 锁复核与 `propose` 重启锁保护，以及三端只读子代理、CI 工作流与文档对齐：

- **无头 JSON 公式转义、`.agentignore` 前置锚定、快照互斥及本地路由加固（`cae_harness.py`、`ccswitch_admin.py`、`router.py`）**：
  - 修复 `_repair_json_string_escapes` 在修复非法反斜杠时将 `\beta`、`\frac`、`\rho`、`\tau`、`\theta`、`\times`、`\nabla`、`\text{...}` 等常见 LaTeX 宏误判为合法单字符控制转义的问题，完整保留真实路径双反斜杠与换行制表转义；
  - 在可写任务快照与写范围核验前完成 `.agentignore` 规则前置锚定；在 `cmd_batch` 中按解析后的工作区根目录增加可写流与重叠目录流的快照临界区协调，覆盖 `build` 及显式授权子目录，扩展 `_scope_patterns` 与 `_scope_matches` 支持目录级 `--write-scope`（如 `src`、`src/`）及零层子目录递归通配 `/**/`，并补齐 `verify_exit_code` 与 `verify_output_tail` 验收诊断字段；
  - 修复 `cmd_plan --resume-ticket` 在刷新动态额度摘要或继承模型显示名时将已交接（`HANDED_OFF`）票据重置回 `PENDING` 的状态回退问题，补齐 `cmd_plan --approve-ticket --override-json` 非法流标识与参数自救校验（限制内部原生通道仅允许微调 `tier` 与 `timeout`）及 `_check_ticket_execution` 对 `write_scope`、`verify_cmd` 的票据一致性核验，并在 `resolve_dispatch_plan_dir` 中对齐 `USER_GUARD_STATE_DIR` 与 `PLUGIN_DATA` 优先级；
  - 为 `ccswitch_admin.py:inspect_status` 与 `cae_harness.py:_probe_router_health` 补齐本地回环地址的系统代理隔离（`ProxyHandler({})`）及 WSL2 NAT 模式下宿主机代理回退，补齐局部 `router.json` 默认路由合并与 `alias_map` 简称展开；为 `router.py` 增加截断请求体拦截、`_merge_request_headers` 多行同名非单值请求头合并（保留多条 `anthropic-beta`）、不区分大小写的 JSON `Content-Type` 识别及 `_scrub_log_value` 敏感字典键脱敏。
- **生命周期钩子包装拆壳、Codex 写边界、POSIX `flock` 复核与状态锁保护（`status_guard.py`、`handoff_gate.py`、`reply_repeat_guard.py`、`user_words.py`、`fleet_state.py`）**：
  - 补齐 `status_guard.py` 与 `handoff_gate.py` 对 `env`（含 `-i`、`-u`、`-C`、`-S`、`--split-string` 及键值前缀）、`command`、`sudo`、`nohup`、`nice` 与 `uv run` 包装器的递归剥离，收紧 Codex `apply_patch`、进程替换与动态重定向的项目外写删拦截边界，对齐软链 `TMPDIR` 下的词法路径与真实路径核验，并为 `status_guard.py` 增加按文件修改时间戳失效的 `cae_harness` 模块缓存；
  - 扩展 `handoff_gate.py:_classify_run_command` 剔除 PowerShell `2>$null` 与 CMD `2>NUL` 空设备重定向，将携带 `--output`、`--approve-ticket`、`--save-ticket`、`--mark-handed-off` 写盘参数的 `cae_harness.py` 调用归入 `mutating_other`，并补齐 `_workflow_handoff_present` 闭合代码围栏的无后缀判据；
  - 修复 `reply_repeat_guard.py:_codex_turn_reply` 跨用户回合未重置 `step` 偏移、Codex `Stop` 未调用 `codex_hook_session` 跳过子代理会话及单流省略 `S1` 时状态词子串误销账问题；
  - 为 `user_words.py` 跨进程文件锁在 POSIX `flock` 加锁后增加目标路径与打开文件描述符 inode 复核，对齐 `candidate_dispatch_plan_dirs` 防软链检查、`_scrub_lifecycle_entry` 敏感键脱敏及 `sweep_stale_lock_files` 过期锁回收；修复 `fleet_state.py` 中 `propose` 与裸 `clear` 未携带 `--force` 时覆盖未过期 `AWAITING_USER_APPROVAL` 提案、已认领 `AWAITING_MASTER_RELOAD` Pass 2 重启锁或损坏状态文件的问题。
- **三端只读子代理契约、安装体检对齐、CI 门禁与用户文档升级（`install.py`、`AGENTS.md`、`rules/AGENTS.md`、`template/*/agents/`、`README.md`、`docs/`、`ci.yml`、`tests/`）**：
  - 在 `template/codex/agents/readonly-*.toml` 中保持顶层标量键位于所有表段之前并追加 `[features]` 权限收窄（禁用 `request_permissions_tool`、`apps`、`plugins`、`memories`），对齐 `readonly-auditor.toml` 只读命令与编译算例交回主会话边界，补齐 `readonly-collaborator.toml` 计算与内存开销及文献出处条款并统一三端 `readonly-*` 行号标注示例；修正 `AGENTS.md`、`R6` 与 `docs/workflow-guide.md`，区分外部无头 `codex exec -s read-only` 独立沙箱快照硬隔离与交互式原生 `readonly-*` 继承父会话沙箱并受开发者指令及 `PreToolUse` 钩子约束的边界；
  - 统一 `install.py` 升级体检与默认模型选择对 Antigravity 目录的词边界拆分、禁用词剔除及主力优先逻辑；在 `.github/workflows/ci.yml` 中补齐 `::error::` 输出前 `%` 转义为 `%25`、`check-grounding --strict` CLI 子进程门禁、`--audit-upgrade` 零错误与推荐动作集合断言及末尾 `git status --porcelain` 判空断言；
  - 在 `tests/` 中补齐 `ACTIVE_PLUGIN_DIR` 对空值或缺失 `USER_GUARD_INSTALLED_DIR` 的安全回退，扩充宿主机客户端会话、代理、XDG 目录、模块搜索路径与 Git 仓库环境变量剥离（含 `git` 子进程环境隔离），移除 `tests/` 仓内临时目录耦合，将源码与 `.yml` 配置中的固有连字符词项及拆分词干对称纳入 `test_02` 用户名与主机名碰撞豁免，并在 `test_01`、`test_41` 与 `test_50` 中补齐 TOML 顶层键与无自相矛盾条款、单流 `S1` 销账全仓同步、双引号 `$HOME` 规范、Claude 模板、`docs/assets/*.svg` 静态版本日期与页内 `#anchor` 标题锚点校验；
  - 在 `docs/workflow-guide.md` 与 `cae-harness/SKILL.md` 中统一通过四端已安装脚本路径变量 `HARNESS` 在课题工作区根目录调用示例，同步全仓 6 处单流省略 `S1` 销账契约，在 `README.md` 补齐 `summary.recommended_actions`（含 `sync_codex_model_defaults` 与 `sync_claude_model_defaults`）排障提示，在 `docs/architecture-and-hooks.md` 补充 `router.py` 与 `ccswitch_admin.py` 双引号 `$HOME` 跨 Shell 命令示例、`fleet_state.py clear` 保护边界及按托管文件名精确清理（含三端顶层规则与 `plugin_data/user-guard` 运行态目录）的卸载说明。
---

## [1.15.1] - 2026-11-16

修复 14 列超宽横向表格窄视口挤压变形、GitHub Actions `ubuntu-latest` 与 WSL2 跨平台测试断言偏差、DSH 新版命令行无头权限补丁冲突及派工门禁目录边界问题，并由 Antigravity 完成全仓文档整篇润色、由 Codex `gpt-6-astra` 完成全仓脱敏核查：

- **确认单与文档窄视口纵向排版重构（`cae_harness.py`、`user_words.py`、`handoff_gate.py`、`reply_repeat_guard.py`、`README.md`、`docs/`、`docs/assets/*.svg`）**：
  - 将 `_render_plan`、`render_pending_workflow_block`、`README.md` 与 `docs/workflow-guide.md` 中在窄屏视口和侧栏被挤压成单字竖条的 14 列横向大表重构为 **4 列各路总览表 + 每路 3 列纵向 14 项完整参数明细表**，零省略保留全部 14 个标准字段；同时在 `test_e2e_integration.py` 新增全仓 Markdown 表格列数上限（不超过 5 列）静态回归门禁；
  - 修复 `user_words.py:render_pending_workflow_block` 在票据已存完整 `human_popup_markdown` 或 `handoff_prompt_block` 时重复拼接第二份背景与参数表的问题；更新 `handoff_gate.py:_workflow_handoff_present` 同时校验纵向 14 项参数明细表与历史 14 列横向行；将 `reply_repeat_guard.py:_render_heavy_bill` 规范为 4 列标准 Markdown 表格。
- **跨客户端无头调用与门禁边界修复（`cae_harness.py`、`status_guard.py`、`install.py`、`fleet_profile.json`）**：
  - 修复 DSH 支持 `--permission-mode` 新版 CLI（`@deepseek-ai/dsh-permission-presets`）时，临时补丁仍写入旧版 `sandbox-policy`、`approval`、`permission` 导致预设服务启动报错退出码 `1` 的真机缺陷；
  - 修复 `_extract_prompt_backend_hint` 在用户发出“用 Codex 等多工作流”等多端并列指令时误锁单端覆盖分路配置的问题，并在 `--fallback-on-quota` 配额回退中严守用户单端 P0 点名与重负载获批票据绑定边界；为 `probe` 与 `assess` 子命令补齐 `--redact`、`--output`、`--no-redact-output` 参数，补齐 `cae_harness.py` 异常出口、WSL `/mnt/<盘符>/Users/` 路径、私网 IPv6、敏感字段键值的 `--redact` 脱敏及 `router.py`、`user_words.py` 诊断日志密钥擦除；将 `inline_readonly_max_kib` 提升至 `512` 以覆盖大型源码文件的带行号内联读取；
  - 修复 `status_guard.py` 在显式传入 `--plan-dir` 时未将该目录纳入获批票据检索、以及静态分析未带 `--target` 的 `cae_harness.py task` 命令时按当前工作目录估算规模阈值的判定；统一 `install.py:_sync_global_rules` 与 `_rules_equal` 对标准 `## 工具与运行环境` 之后任意二级标题自定义尾段的识别与防漂移判等；修复 `gate` 完工账单子项状态标记与无 `.git` 归档目录测试文件枚举回退；
  - 合并前序闭环加固：完工门禁按完整词项绑定流标识与实际回执文件名并覆盖跨回合原生任务，派工门禁剥离常见命令包装器，状态锁保护有效待批提案，首次路由配置读取动态端口，卡片更新保留合法元数据。
- **Linux（`ubuntu-latest`）与 WSL2 跨平台回归及文档润色脱敏（`tests/`、`README.md`、`docs/`、`CHANGELOG.md`）**：
  - 修复 `test_claude_and_router_offline.py` 健康检查硬编码 Windows 端口 `8240`（POSIX 默认 `8241`）、Windows 应用探测缺少 `_windows_app_binaries` 隔离、WSL2 DrvFS 挂载盘 `chmod` 权限位差异及回环请求未隔离系统代理的问题；修复 `test_e2e_integration.py` POSIX 专属用例在 Codex 五钩子对齐后 `--codex-handoff-mode off` 剩余 `3` 个非交接钩子的断言；将仓库内测试临时沙箱统一迁入已忽略的 `.pytest_cache/` 目录；
  - 按**用户层**（一键安装、明确点名 P0 矩阵、日常课题角色矩阵、本仓库元自动更新独立章节）与**原理层**（常驻与动态配置分层、双阶段额度复查、共有门禁与四客户端协议适配）重构 `README.md`、`AGENTS.md`、`template/user-guard/README.md`、`docs/` 并新增 `docs/assets/user-and-evolution-flow.svg`，消除单一客户端事件视角偏差与密集术语堆砌，统一自定义尾段识别条件、`--disable-ccswitch-prompts` / `--purge-ccswitch-legacy` 说明、国家留学基金管理委员会唯一资助声明及 Apache-2.0 第 4 条 `NOTICE` 原创归属说明。

---

## [1.15.0] - 2026-11-16

本版将可用性检查、全通道派工确认、中断接力与完成回执串成统一流程，并对齐安装器、客户端能力适配和用户文档。下列记录说明实现变化，不把配置记录当成本轮服务端额度或模型性能实测。

- **双门可用性与动态额度检查**：独立 `fleet-evolve` 流程联网评估能力，并由用户批准 `subscription_matrix` 中 agent 与 LLM 的启停；`enabled: false` 项全局跳过。`cae_harness.py plan` 在弹卡前离线复查当前配置、凭据、所需路由及本机可读额度，按来源读取滚动窗口百分比和重置时间、卡片套餐或余额类型、日月限额及运行侧冷却。未知状态保持未知；`capability_ledger.last_online_refresh_at` 与 `local_bench_verified_at` 作为离线性能记录显示，在线刷新与日常派工解耦。
- **用户点名 P0 与参数补全**：通过 `--user-prompt` 保留精确标识符、模型简称、口语挡位和省额度意图，锁定用户已指定维度；`role:*` 是并列职责入口，不覆盖点名。未指定的声明窗口、非委派挡位、无头参数、只读小文件行号内联、快照和脱敏由脚本补齐。点名不可用、轻量卡片承担非 `fast` 角色或所需路由未存活时明确报错，避免静默降级。
- **全通道确认与工具前票据门禁**：内部原生子代理、工作流及外部无头子代理的多路或可写重负载统一进入确认流程。`plan` 保存背景、目标、预计后果、14 列完整矩阵、性能更新时间与选项卡，显示实际模型及 `inherit` 解析、权限范围、额度状态和回执位置。用户批准后以 `--approve-ticket` 登记，执行绑定有效 `--ticket`；`PreToolUse` 的 `status_guard.py` 检查票据，拦截裸客户端 CLI 旁路和未经确认的重负载。
- **压缩与交接中断接力**：恢复入口独立加载磁盘未决工作流，与常规原话及子代理摘要分块显示，不依赖摘要猜补计划。强制交接时，`handoff_gate.py` 的 `Stop` 检查【新对话提示词】内嵌完整工作流、矩阵、性能更新时间、选项卡和票据。`plan --resume-ticket` 读取复核旧票据但不改绑原会话，跨会话重新形成当前会话确认计划；压缩、恢复和等待超时均不构成批准。
- **重负载完工报账硬钩子**：`reply_repeat_guard.py` 在 `Stop` 检查回复是否含有成败词、Token 用量或无法统计说明及回执或日志词项，外部任务按完整词项匹配各流标识（单路 `task` 或 `gate` 默认 `S1` 可用模型名或任务状态替代）及非空回执的实际文件名，原生任务覆盖完成数量、全部流标识或互不重复的角色名之一及轨迹或日志说明；漏报且同回合尚无其他门禁阻断时单次阻断并附上磁盘账单，主会话负责逐路核对具体状态、Token 明细与汇总，未暴露整数的通道明确标为无法统计。
- **全后端行号内联与多流执行**：`--inline-readonly` 默认启用，按 `inline_readonly_max_kib` 为只读小文件附带精确行号，覆盖各外部后端。`--output` 保存结构化回执并默认脱敏；`batch` 按 `batch_max_workers_per_backend` 控制并发，只执行 `cae_harness_headless` 通道，同后端额度中止后停止其排队流。原生通道继续由对应客户端按批准计划调用。
- **DSH 双版本能力自适应**：通过 `--profile headless --help` 探测权限参数支持；新命令能力使用 `--permission-mode` 与 `--approval-policy`，不支持这些参数时使用补丁与 `DSH_PERMISSION_MODE`。保留单 JSON 输出约束、平衡对象解码、围栏剥离与 DSML 尾部清洗，不按未经核实的版本字符串硬分支。
- **Codex 五钩子对称挂载**：事件模板与安装器对齐 `SessionStart`、`PostCompact`、`UserPromptSubmit`、`PreToolUse`、`Stop`。恢复、交接、反裸调、票据与完成报账接入对应事件链；五类事件和五个共用脚本模块分别说明，避免一一对应的误解。
- **规则安装与本地分流加固**：`install.py` 使用完整标准模板更新规则，保留标准章节和 `R10.`、`R11.`；用户自定义环境尾段中的冲突编号迁移为 `L1.`、`L2.`。Claude 默认随钩子同步卡片管理脚本，可选分发本地路由。`router.py` 不再让 `force_effort` 覆盖显式 `low`、`medium` 及其他非高挡值；缺省或已有高挡继续按路由配置处理。
- **上游状态锁加固**：`fleet_state.py` 在目标仓库识别后读取默认有效期，非法参数返回结构化错误。事实锚定模块缺失、导入失败或结果结构无效时拒绝归位，并在状态锁内执行检查；带验收标记的归位默认拒绝清除有效待批提案，显式覆盖使用 `--force`。保留运行侧、仓库哈希、并发锁和重启认领校验，避免漂移状态被误判为有效批准。
- **用户文档与矢量图整篇对齐**：重写根说明、模板说明、架构说明、工作流指南及两张 SVG，补齐双入口权衡、双门可用性、完整矩阵、票据批准与恢复命令、全通道完工报账。保留国家留学基金委唯一资助声明、英文概览、Apache License 2.0 与 NOTICE 原创归属；常驻文档不嵌入插件版本或静态性能日期，统一 UTF-8 无 BOM 与 LF。

---

## [1.14.2] - 2026-11-15

在双环境真机无头连通性实测中定位并修复 WSL2 跨子系统进程树清理异常、只读镜像无后缀工程文本漏拷及 WSL2 互操作 MSIX Codex 择新缺失问题：

- **WSL2 跨子系统互操作退出码保护与 MSIX 包择新对齐（`cae_harness.py`、`install.py`、`tests/`）**：
  - 修复 `_interop_command` 在 Windows PowerShell 5.1（`$ErrorActionPreference='Stop'`）下执行 `finally` 进程树清理时，因已正常退出的子进程触发 `taskkill.exe` `2>$null` 流重定向 `NativeCommandError` 而误返退出码 `1` 的真机缺陷，改为通过 `System.Diagnostics.ProcessStartInfo` 启动清理、异步排空重定向流并在等待超时后强制终止及输出诊断；
  - 新增 WSL2 下基于原生 `reg.exe` 的零缓存写入 MSIX 应用包只读解析（支持 UTF-8 与 `ACP` 本地代码页回退严格解码），在 Windows 与 WSL2 的 Codex 探测及最终回退分支中统一排除 `%LOCALAPPDATA%\Microsoft\WindowsApps` 执行别名占位文件，同步对齐 MSIX `app/resources/codex.exe` 与桌面缓存二进制的时间戳择新；
  - 扩展 `collect_target_files` 收录 `NOTICE`、`LICENSE`、`COPYING`、`Dockerfile`、`GNUmakefile` 及 `.svg` 矢量图文件，消除 `--clean-room` 空上下文盲读与跨子系统只读镜像漏拷顶层无后缀文本文件的盲区，并补齐离线与双端真机无头回归验证。

---

## [1.14.1] - 2026-11-15

修复跨客户端无头协作选路与隔离快照校验缺陷、Windows MSIX 应用包新版 Codex CLI 探测覆盖、GitHub CommonMark 强调定界符渲染异常及脱敏单测占位符越界问题：

- **跨客户端无头编排与客户端探测修复（`cae_harness.py`、`install.py`、`rules/AGENTS.md`、`fleet-evolve/SKILL.md`）**：
  - 修复显式指定专长角色槽位（如 `codex:code`、`codex:math`、`codex:review`、`dsh:code`、`agy:@math`）并传入 `--tier low` 时被静默降级替换为 `fast` 轻量模型（如 `gpt-6-luna` 在 `--output-schema` 下跳过工具读取）的缺陷，仅在 `role:fast`／`<client>:@fast` 或未指定专长槽位时允许 `low` 挡切入轻量模型；
  - 修复 `cmd_task` 在 `--clean-room` 或跨子系统副本目录（`staged_readonly_workspace`）模式下比对原工作区 `root` 而非隔离副本目录 `stage_dir`，导致主会话并发准备文件时误报 `scope_violation` 的缺陷，改为自 `stage_dir` 快照建立起全阶段保护并在临时目录清理前执行哈希核验；同时在 `user_words.py` 中对 `CAE_HARNESS_DEPTH=1` 无头瞬态调用（`--ephemeral`／`--no-session-persistence` 无落盘轨迹）静默跳过长会话门禁告警；
  - 统一 `install.py` 与 `cae_harness.py` 在 Windows 下对 Microsoft Store／MSIX 应用包（`WindowsApps\OpenAI.Codex_*`）与 `%LOCALAPPDATA%\OpenAI\Codex\bin` 缓存目录的按时间戳择新逻辑（保留用户显式安装的 `~/.local/bin` 与 `~/.cargo/bin` 优先级），避免旧版桌面缓存二进制遮蔽新版 MSIX CLI；
  - 明确 `R3.1` 与 `fleet-evolve` 中跨客户端 `cae-harness`（`role:math`、`role:code`、`role:review`、`audit`）与同端 `readonly-*` 子代理的分工边界，防止在多端就绪环境下仅用同模型子代理替代跨模型独立审查。
- **GitHub CommonMark 粗体定界符与公开脱敏单测修复（`README.md`、`AGENTS.md`、`CHANGELOG.md`、`docs/workflow-guide.md`、`cae-harness/SKILL.md`、`tests/`）**：
  - 修复全仓 5 份 Markdown 文档中紧邻全角括号与引号的 `**...**` 违背 CommonMark 0.31.2 侧翼定界符规则（在 GitHub 裸露原样星号）的排版问题，并在 `test_e2e_integration.py` 中新增全仓 Markdown 强调定界符静态回归检查；
  - 将 `test_harness_offline.py` 中触发 `test_02` 路径扫描拦截的非白名单模拟主目录占位词统一改为受控占位符 `<user>`，在 `test_claude_and_router_offline.py` 的 `OfflineSandbox` 中补齐外部 `DSH_HOME` 与 `CAE_FLEET_PROFILE` 环境变量隔离，并新增专长槽位保模、隔离副本快照校验与 Windows MSIX 择新的离线单元测试。

---

## [1.14.0] - 2026-11-15

完成全仓运行期定量参数（`runtime_tunables`）深度收口、会话生命周期钩子静态拆壳与截断全文恢复加固、无头编排器 JSON 解析与子进程树清理增强、本地分流代理与升级审计边界修复，以及全仓文档、子代理契约与矢量图的整篇重写润色：

- **无头编排器、动态配置收口与状态锁加固（`fleet_profile.json`、`cae_harness.py`、`fleet_state.py`）**：
  - 在 `fleet_profile.json` 的 `runtime_tunables` 中统一收口 Shell 包装深度上限（`max_wrapper_depth = 4`）、过期锁清理周期（`stale_lock_cleanup_days = 7`、`stale_lock_sweep_interval_sec = 86400`）、跨子系统副本上限（`interop_stage_max_mib = 64`）、审查并发数（`audit_max_workers = 4`）、双盲共识行号容差（`consensus_line_tolerance = 8`）、`agy` 强杀宽限与目录探测超时（`15s`／`30s`）、状态锁有效期（`fleet_state_ttl_sec = 86400`）及 WSL2 路由默认端口（`wsl_port = 8241`）；
  - 重写 `_decode_output_json` 为外层代码围栏剥离、字符串内非法单反斜杠转义修复（兼容 Windows 路径与 LaTeX 公式）及 `JSONDecoder.raw_decode` 平衡对象扫描，修复正文中内嵌三重反引号代码片段或未转义反斜杠导致的 `dsh` 无头输出解析失败；
  - 补充宿主环境变量自动识别调用方客户端、Windows Job Object 子进程树绑定强杀、`--clean-room` 配额回退自动排除 `agy`、双盲 `audit` 配额回退跳过对侧已占用客户端以保持双源独立、消费 `agy_retiring_patterns` 过滤退役模型，并修复 `audit_doc_grounding` 对单行闭合围栏、行内代码 `$VAR`、尖括号占位符、中英文相邻大写缩写及互补百分比的边界判定；
  - 加固 `fleet_state.py` 对 UTF-8 BOM 的读取兼容、Windows 文件替换 `PermissionError` 短退避重试、`AWAITING_USER_APPROVAL` 阶段版本漂移校验及顶层异常结构化 JSON 输出。
- **会话生命周期钩子安全与压缩恢复加固（`user_words.py`、`session_context_loader.py`、`status_guard.py`、`handoff_gate.py`、`reply_repeat_guard.py`）**：
  - 引入稳定文件缓存键 `get_fleet_profile_cache_token`（含缺失负缓存与宿主全局插件目录路径兜底）及 `was_unpatched` 单元测试属性打桩基线保护，新增非阻塞低频清理空闲过期 `.lock` 锁文件机制；
  - 修复 Antigravity `SYSTEM_MESSAGE` 长篇子代理返回触发 `truncated_fields` 时先回查 `transcript_full.jsonl` 还原完整正文再做实质筛选的顺序，保留 `raw_body` 消除归档与内联注入的双重摘要省略，保持 Codex 跨分段全局步骤号单调递增，收紧 Claude Code 包裹性系统块剥离规则，并在内联截断切中代码块时自动补齐闭合围栏且不超字符上限；
  - 扩展 `status_guard.py` 支持 PowerShell `-EncodedCommand`／`-ec` UTF-16LE Base64 拆壳（非法编码保守拦截）、PowerShell 写删移复内置别名（`rd`、`ri`、`ni`、`ac`、`clc`、`mi`、`rni`、`cpi`，排除 `sc` 防误伤 Windows 服务查询）、GNU `cp/mv -t`／`--target-directory`（区分大小写 `-t` 与 `-T`）、`sys.executable`／`os.getcwd()`／`Path.cwd()` AST 静态求值，以及多子句命令链按滚动工作目录逐句检查重定向与内联 Python 脚本。
- **安装器、本地分流代理与卡片管理边界修复（`install.py`、`router.py`、`ccswitch_admin.py`、`launch-cc-switch-wsl.sh`、`update-cc-switch-wsl.sh`）**：
  - 修复 Windows 多版本 Codex CLI 按文件时间戳择新与 `DeepSeek Harness.exe` 探测、WSL2 互操作探测完整性标记、清理废弃 `session-*.json.lock`、私网 IPv4 `<private-ip>` 与自定义卡片 ID `<custom-card-id>` 脱敏，以及激活直连供应商卡片时不误探本地路由健康或误报 `start_cc_router`；
  - 加固 `router.py` 支持 `Transfer-Encoding: chunked` 请求体解析与体积限制、空白 `ANTHROPIC_AUTH_TOKEN` 自动回退 `ANTHROPIC_API_KEY`、上游返回 `401`／`403` 时自动失效密钥缓存、中文卡片名 ASCII 错误短语编码保护，以及 `ccswitch_admin.py` 异常回滚时自动清理孤儿备份文件；完善 WSLg 启动脚本已有实例复用与 `.deb` 更新脚本架构自适应。
- **全仓图文文档、规则与子代理模板整篇重写及回归单测扩展（`README.md`、`AGENTS.md`、`docs/`、`template/`、`tests/`）**：
  - 结合 `--clean-room` 空上下文新读者盲读反馈，按自然平实工程书面语整篇重写全仓文档、全局规则、技能说明、三端 9 个 `readonly-*` 子代理配置与两张 SVG 矢量图，通过 `check-grounding --strict` 零黑名单核验并补齐全部新增边界的离线回归单测。

---

## [1.13.0] - 2026-11-15

完成五类角色槽位与运行期定量参数（`runtime_tunables`）全动态收口、压缩后高价值子代理实质筛选、零黑名单文档事实锚定硬门禁（`check-grounding`）、通用课题项目与上游配置仓库双场景分类隔离，以及全仓图文文档校准与国家留学基金委资助致谢补充：

- **五类角色槽位对称动态选路与 `runtime_tunables` 死数字收口（`fleet_profile.json`、`user_words.py`、`session_context_loader.py`、`handoff_gate.py`、`reply_repeat_guard.py`、`status_guard.py`、`router.py`、`cae_harness.py`、`install.py`）**：
  - 统一 `orchestrator`、`math`、`code`、`review`、`fast` 五个角色槽位为全动态对称结构（均支持 `role:<slot>` 与 `<client>:@<slot>` 选路及 `recommended_tier` 读取，不将任何角色与单一客户端或模型永久绑定）；
  - 在 `fleet_profile.json` 中新增 `runtime_tunables`（支持 `CAE_FLEET_PROFILE` 离线覆盖并按文件 `mtime_ns` 缓存，同时保留单元测试属性打桩优先级），统一收口各客户端压缩恢复字符预算（`per_client`：Antigravity `7,600` 字符、Codex／Claude Code `8,000` 字符）、长会话交接阈值（`handoff_thresholds`）、空等与日志轮询阈值（`loop_guards`）、无头分片安全系数／超时／验证轮数／润色长短篇阈值及本地分流代理默认并发与缓存参数；
  - 新增压缩前子代理返回实质筛选函数 `select_recent_subagent_returns`，在候选窗口（`recent_subagent_window`）内无条件保留最新一条返回，并按实质字数阈值（`min_substantive_subagent_chars = 64`）、审查／推导角色关键词与时间先后择优保留高价值结论，防止简短状态回执挤占关键行号证据。
- **零黑名单确定性文档事实锚定门禁（`cae_harness.py`、`fleet_state.py`、`rules/AGENTS.md`）**：
  - 彻底清除前序重写阶段混入仓库规则与文档的无源码依据制造术语（`CAM`、刀轨、曲面求交等）；
  - 在 `cae_harness.py` 中新增通用零黑名单事实锚定子命令 `check-grounding`（支持 `--target`、`--root`、`--base-ref`、`--allow-from-file`、`--strict`），自动扫描目标工作区源码与配置文件构建标识符、`--flag`、数值与派生单位及中文双字词干集合，逐行拦截无依据命令行标志／文件名、陌生全大写缩写、失步容量数字与虚构领域举例；
  - 在 `cae_harness.py task --kind polish` 中以改写前原稿为基线自动输出 `grounding_audit` 核验结果，并在 `fleet_state.py clear --verified --readback-done` 终审归位前强制调用 `audit_doc_grounding` 硬门禁。
- **通用课题项目与上游配置仓库双场景分类及工作区身份硬隔离（`cae_harness.py`、`fleet_state.py`、`fleet-evolve/SKILL.md`、`README.md`）**：
  - 在 `cae_harness.py`（`probe`、`assess`、`check-grounding`）中新增 `detect_workspace_mode`（区分 `general_project` 与 `upstream_config_repo`），在 `fleet_state.py` 中对非上游仓库无 `--repo` 调用返回结构化退出码 `2`（`reason: "not_upstream_config_repo"`），并收窄 `fleet-evolve` 触发条件，确保日常课题研发说出“全量优化代码和文档”时仅在项目内闭环，绝不误触全局元更新；
  - 将 `README.md` 第四章表二重构为“通用课题与工程项目日常研发（部分功能）”与“维护本上游配置仓库（全功能：全面更新 `full` 与全量优化 `hygiene` 双闭环）”两张分类对照表。
- **图文文档全量校准、致谢补充与回归单测扩展（`README.md`、`docs/`、`docs/assets/*.svg`、`tests/`）**：
  - 整篇重写校准 `README.md`、`AGENTS.md`、`template/user-guard/rules/AGENTS.md`、两份 `SKILL.md`、`docs/*.md` 及两张 SVG 架构管线图，在 `README.md` 第六章中英文部分补充**中国国家留学基金委**（**China Scholarship Council, CSC**）目前唯一资助致谢（学号：`202506280162`，批准文号：`留金选[2025]37号`），并新增动态参数覆盖、实质子代理筛选、对称角色路由、双工作区硬拦截与 `check-grounding` 防幻觉回归单测。

---

## [1.12.0] - 2026-11-15

完成常驻规则脚本与模型评估配置两层解耦、压缩后“历轮用户原话 + 最近子代理返回摘要”三端双路有界恢复、推荐搭配表用户审批状态锁、无头调用限流与分类器故障修复，以及图文分层文档重构、窗口／配额／缓存实证校准与静态／运行期脱敏加固：

- **压缩后“历轮用户原话 + 最近 4 次已完成子代理摘要”双路有界恢复（`user_words.py`、`session_context_loader.py`、`rules/AGENTS.md`、`AGENTS.md`、`tests/`）**：
  - 扩展 Antigravity（识别 `invoke_subagent` 与子代理完成消息，单遍 $O(M)$ 回查 `transcript_full.jsonl` 补齐截断正文并记录 `logAbsoluteUri`，过滤 `sender=system`、`/task-` 命令噪声与 `BROWSER_HOST_FACTS`）、Codex（合并 `sessions/` 与 `archived_sessions/` 分段，匹配 `spawn_agent`／`wait_agent` 完成状态映射表并按 `(call_id, agent_id, content_sha256)` 去重，自动定位子代理 `rollout-*.jsonl` 路径）与 Claude Code（提取非支线 `Agent`／`Task` 工具完成回执，跳过 `async_launched` 句柄与 `is_error` 异常块并关联 `subagents/agent-<id>.jsonl`）；
  - 将子代理返回摘要严格独立于人类权威指令列表 `words`（遵守 `R8` 与 `R6`，附带首尾双端摘要与原始日志指针），未发生新压缩的普通回合保持零额外字节注入（保护服务端前缀缓存字节一致性），压缩恢复内联总预算严格限制在 `MAX_INLINE_TOTAL_CHARS = 8000`（子代理摘要区上限 `1,400` 字符、单条上限 `280` 字符），且在未调用子代理时保持归档字节完全向后兼容。
- **常驻代码与模型评估配置解耦及审批状态锁（`fleet_profile.json`、`cae_harness.py`、`fleet_state.py`、`install.py`、`ccswitch_admin.py`、`rules/AGENTS.md`、`AGENTS.md`、`SKILL.md`）**：
  - 新增机器可读配置文件 `template/user-guard/skills/cae-harness/fleet_profile.json`（支持 `CAE_FLEET_PROFILE` 环境变量覆盖测试），集中定义五类抽象角色槽位（`orchestrator`、`math`、`code`、`review`、`fast`）候选顺序、实证优缺点与隔离措施、暂缓接入平台、客户端模型目录匹配规则及供应商卡片默认参数；
  - 从常驻脚本、规则与技能说明中移除特定时期的模型名称硬编码，支持 `role:*` 与 `<client>:@<role>` 动态选路；在 `fleet_state.py` 中新增 `propose` 与 `approve` 子命令（`AWAITING_USER_APPROVAL` 状态）并将 `fleet_profile.json` 纳入 `core_sha256` 校验，固化四个必停用户拍板节点。
- **无头调用故障修复与边界代码加固（`cae_harness.py`、`handoff_gate.py`、`status_guard.py`、`reply_repeat_guard.py`、`router.py`、`ccswitch_admin.py`、`install.py`）**：
  - 扩展 `QUOTA_ERROR_RE` 与 `_parse_quota_reset` 识别 `hit your session/weekly limit`、`5-hour ... limit` 及无介词 `resets 5pm` 限流报文，修复 Claude Code JSON 错误信封（`is_error: true` 且错误信息位于 `result` 字符串字段）提取遗漏，并为 Claude Code 无头只读任务固定附加 `--permission-mode dontAsk` 以规避 `auto` 分类器限流报错；
  - 补齐 `dsh` 在 `--clean-room` 模式下对 `.env` 文件中 `DEEPSEEK_API_KEY` 的提取，修复 `audit --single --left role:fast` 未透传引擎提示导致推理挡位误选 `max` 的问题，并加强 `dsh` 单 JSON 对象输出提示约束；
  - 对齐 `handoff_gate.py` 在 `drain` 与 `freeze` 阶段对 `env -u/-C/-S`、`sudo` 带参选项及 `command -v/-V` 的 Shell 解包逻辑，统一 `status_guard.py` 在多层无头子进程（`CAE_HARNESS_DEPTH >= 1`）下的项目外写拦截行为，清理 `reply_repeat_guard.py` 未使用的冗余函数，支持 `router.py` 模型前缀不区分大小写匹配，并加固 `ccswitch_admin.py` 对损坏配置文件的容错保护。
- **图文分层文档重构、实证指标校准与静态／运行期脱敏加固（`README.md`、`docs/`、`docs/assets/*.svg`、`install.py`、`ccswitch_admin.py`、`cae_harness.py`、`tests/`）**：
  - 新增纯 ASCII 路径矢量架构图 `docs/assets/architecture-overview.svg` 与生命周期／定量验证管线图 `docs/assets/lifecycle-and-verification.svg`，配合 Mermaid 时序／流程图与 L1–L4 定量证据等级矩阵重组 `README.md`、`docs/architecture-and-hooks.md` 与 `docs/workflow-guide.md`；
  - 逐项核对 `cae_harness.py` 预算公式与本机模型目录缓存，校准单分片安全输入预算（`120,000`／`745,560`／`900,000`／`943,718` token）、出厂默认窗口／挡位与显式上限参数、客户端订阅 5 小时／周度滚动配额池与按量 API 前缀缓存计费机制，剔除未经实测的经验百分比与生造套话；
  - 彻底清除文档中的个人双机请求计数、账单用量统计与敏感措辞；同步加固 `install.py --audit-upgrade`（覆盖驼峰密钥字段、布尔开关防误替换、WSL 跨盘符主目录、`ghp_`、`AIza`、`AKIA`、`hf_`、`glpat-` 令牌、`<ccswitch>` 路径及自定义提示词／技能目录名脱敏）、`ccswitch_admin.py`（上游接口 URL 密钥参数脱敏）与 `cae_harness.py`（配额冷却详情与无头异常日志密钥脱敏），并补齐离线回归单测。

---

## [1.11.1] - 2026-11-15

新增 `cae-harness` 空上下文盲读模式（`--clean-room`），建立“空上下文新读者盲读 -> 主会话重写自然工程中文 -> 独立核对技术准确度与脱敏”三步文档校核流程，并清理 CC Switch 旧版残留项：

- **无头空上下文盲读模式（`cae_harness.py`、`tests/test_harness_offline.py`）**：在 `cae_harness.py task` 中新增只读专用的 `--clean-room` 选项（支持 `codex`、`claude`、`dsh`；对不支持剥离全局插件的 `agy` 或修改文件任务明确报错拦截）。开启后自动在系统临时目录构造仅保留必要登录凭据或 API 环境变量的隔离配置目录（`claude` 自动启用 `--bare` 与最小环境变量 `--settings`，`codex` 与 `dsh` 剥离全局 `AGENTS.md`、钩子与技能，并在只读工作区副本中排除 `AGENTS.md` 与 `CLAUDE.md`；当 `codex` 只读副本不超过 `256 KiB` 时自动附带行号文件快照以兼容 Windows 只读沙箱），提供不带历史上下文、全局规则、钩子与技能的独立审阅环境。
- **三步文档校核流程与文档结构重排（`README.md`、`docs/`、`AGENTS.md`、`rules/AGENTS.md`、`SKILL.md`、`fleet_state.py`、`tests/test_e2e_integration.py`）**：
  - 在 `R0`、`R2`、`fleet-evolve/SKILL.md` 与根目录 `AGENTS.md` 中确立三步文档编写与重构流程，并在 `fleet_state.py clear --verified --readback-done` 中记录校核状态；
  - 重排 `README.md` 章节顺序（前置单客户端快速安装步骤、统一 `--caller auto` 命令示例、补充运行概念与卸载说明），在 `docs/workflow-guide.md` 中新增二维可压缩 Euler 方程 HLLC 界面通量任务单模板与 JSON 回执字段说明，并将 `docs/architecture-and-hooks.md` 拆分为清晰的模块职责表与钩子触发条件表；
  - 在 `test_41` 中增加非 `CHANGELOG.md` 文档禁止残留静态日期戳与补丁式附注抬头的回归检查。

---

## [1.11.0] - 2026-11-15

建立非主控外部工具三级二进制接口（ABI）耦合升级检查机制，完成全仓安全与并发边界修复，并统一文档维护范围：

- **非主控工具三级升级评估与 `host.toolchain` 耦合提示（`install.py`、`fleet-evolve/SKILL.md`、`AGENTS.md`）**：按依赖耦合程度将外部工具升级划分为三级——第一级（无外部编译依赖的非当前客户端 CLI、`py.exe` 启动器、CI 工作流）、第二级（同次版本 Python 小补丁与 `wsl --update`，先检查运行中进程与算例再执行）与第三级（装有 `jax`、`jaxlib`、`scipy`、`mpi4py` 等编译扩展的系统级 Python 跨次版本升级、CUDA 跨大版本升级及 WSL 跨 LTS 发行版升级，发出明确依赖耦合警告并交由用户决定）；`install.py --audit-upgrade` 新增 `host.toolchain` 只读扫描并输出 `coupling_warnings`。
- **钩子静态分析、并发锁与本地路由边界修复（`status_guard.py`、`cae_harness.py`、`fleet_state.py`、`router.py`、`ccswitch_admin.py`、`install.py`）**：
  - `status_guard.py`：增加 Shell 嵌套解包深度上限（超过 `4` 层按需确认或拦截），剥离 `if/elif/while/until` 条件前缀，解析 `env`（含 `-C`）、`sudo`、`command`（放行 `-v/-V` 查询）与 `timeout` 包装，跨子句追踪 `cd/pushd` 工作目录变化，识别无空格紧邻重定向（`echo x>/out`、`>|`、`<>`、`&>`），支持 `py` 与 `python -Bc` 内联脚本中整数步号格式化字符串（如 `f"step_{i:04d}.vtk"`）、`pathlib.Path()` 与 `.resolve()` 静态路径求值，并在读写状态文件前检查重解析点；
  - `cae_harness.py`：补充 Windows PowerShell `finally` 子进程树清理、确保 `probe` 与 `--dry-run` 零磁盘写入、转义 WSL 跨子系统 `.cmd/.bat` 参数中的百分号、将 `.github` 纳入快照比对，并优先识别 `AGY_ERROR` 超时诊断行；
  - `fleet_state.py`：引入跨平台文件锁（`msvcrt` 与 `fcntl`）、`repo_root` 与 `core_sha256` 变更检测、`verify-pass2` 会话认领防并发冲突及损坏状态文件诊断；
  - `router.py`、`ccswitch_admin.py` 与 `install.py`：为本地分流脚本增加 `64 MiB` 请求体上限（`413`）与 `64` 路并发限制（`503`）；加固 `ccswitch_admin.py` 符号链接拦截与切卡异常时优先恢复 `settings.json`；在 `install.py` 中增加 `_rules_equal` 精确检测规则尾段差异并规范化比对 Codex `hooks.json`。
- **文档职责梳理与 `fleet-evolve hygiene` 模式补充（`README.md`、`docs/`、`AGENTS.md`、`SKILL.md`）**：统一将钩子协议与分流细节归入 `docs/architecture-and-hooks.md`，在 `README.md` 第四章参考表中补充 `fleet-evolve hygiene` 调用说明，并修正 `ccswitch_admin.py switch-card` 命令行位置参数示例。

---

## [1.10.0] - 2026-11-15

新增 `fleet-evolve` 升级整理技能与二阶段状态锁，补充第四章两张核心参考表，并修复目录 `Grep` 通配符、`DSML` 停止标记清理与升级检查脱敏问题：

- **`fleet-evolve` 技能与宿主机状态锁脚本（`template/user-guard/skills/fleet-evolve/`、`install.py`、`AGENTS.md`）**：新增支持 `full`、`audit`、`hygiene` 三种模式的技能说明 `SKILL.md` 与纯标准库状态锁脚本 `scripts/fleet_state.py`（保存在宿主机本地状态目录，记录运行侧、提交哈希、插件版本、脚本哈希、`session_id` 与 `24h` 有效期，防止主会话客户端重启后重复执行）；扩展 `install.py` 同步 `skills/` 下全部托管技能并纳入 `--audit-upgrade` 一致性检查。
- **用户自理前提划分与参考对照表整理（`README.md`、`docs/`、`rules/AGENTS.md`）**：区分用户自行准备的基础前提（网络环境、客户端安装、官方账号登录与 API Key）与本仓库自动同步的规则、钩子、子代理及技能；在 `README.md` 第四章整理客户端配置参考表与日常需求调用对照表。
- **钩子通配符、停止标记与审计脱敏修复（`status_guard.py`、`cae_harness.py`、`install.py`、`ccswitch_admin.py`）**：修复 `status_guard.py` 在 Claude Code 目录 `Grep` 传入复杂 glob（如 `**.py`、`{a,b}`、`!exclude`）时触发 `Path.rglob` `ValueError` 及遍历 `.git` 目录的问题；修复 `cae_harness.py` 在 `dsh` 无头模式下模型输出尾随 `</｜｜DSML｜｜...>` 标记导致的 JSON 解析失败；在 `install.py` 中排除 Antigravity 默认选模里的退役第三方模型、隔离沙箱审计对宿主机 `cc-switch.db` 的读取、脱敏自定义供应商卡片名称，并补齐 `ccswitch_admin.py` 卡片上下文窗口检查。

---

## [1.9.1] - 2026-11-15

完善 CC Switch 旧版提示词与技能停用归档、升级检查误报修复及 WSL2 跨子系统无头调用预检：

- **CC Switch 旧版提示词与技能停用清理（`ccswitch_admin.py`、`install.py`、`R11`）**：明确 CC Switch 仅用于管理供应商配置卡片，停用其内置提示词（`prompts`）与技能（`skills`）同步功能；`inspect_status`、`verify_alignment` 与 `install.py --audit-upgrade` 增加 `skills` 表启用状态及 `~/.cc-switch/skills` 目录残留检查，`--disable-ccswitch-prompts` 同步停用旧提示词与旧技能开关，并新增 `ccswitch_admin.py retire-legacy --purge` 与 `install.py --purge-ccswitch-legacy` 在线备份归档清理命令。
- **升级检查误报修正与 WSL2 Fake-IP 直连预检（`install.py`、`cae_harness.py`）**：修正 `audit_upgrade_status` 在 Antigravity 已选用最新主力模型时因目录仍含待下线旧模型产生的误报，以及 WSL2 复用 Windows Codex CLI 时的模型目录刷新误报；`cae_harness.py` 新增 WSL2 直连卡片解析命中宿主机透明代理 Fake-IP 地址段（`198.18.0.0/15`）且未配置代理时的预检报错提示。

---

## [1.9.0] - 2026-11-15

统一仓库名称与 Apache 2.0 `NOTICE` 署名声明，并将 Antigravity（`agy`）、Codex（`codex`）、Claude Code（`claude`／`cc`）与 DeepSeek Harness（`dsh`）整合为四客户端对等部署与后台调用体系：

- **支持 DeepSeek Harness 安装与配置检查（`install.py`、`status_guard.py`）**：新增 `--app dsh`、`--dsh-home` 部署模式及 `--audit-upgrade` 一致性检查，自动识别 `~/.dsh` 并同步 `R0–R11` 全局规则（保留末尾自定义 `## 工具与...` 段落）与托管技能目录，同时补齐 `~/.claude/skills/` 同步。
- **`cae-harness` 四客户端对等调用与运行前预检（`cae_harness.py`）**：在 `agy` 与 `codex` 基础上接入 `claude`（别名 `cc`）与 `dsh`（通过临时 `--patch` 激活官方登录态或 `DEEPSEEK_API_KEY`）；支持按中英文提示词识别目标客户端并排除编译环境变量（如 `CC=gcc`）与否定句误匹配；调用前校验模型凭据、本地路由 `/__router/health` 状态及供应商卡片密钥，支持在 `--fallback-on-quota` 且工作区未改动时自动切换至其他就绪客户端。
- **仓库标识更新与六模式沙箱测试（`NOTICE`、`LICENSE`、`plugin.json`、`.github/workflows/ci.yml`、`tests/`）**：统一仓库标识为 `Antigravity_Codex_ClaudeCode_DeepSeekHarness`，新增根目录 `NOTICE` 文件，并在跨平台 CI 与离线测试中覆盖六种 `--app` 安装模式（`codex`、`antigravity`、`claude`、`dsh`、`both`、`all`）。

---

## [1.8.0] - 2026-11-14

新增 Claude Code 与 CC Switch 配置同步、本地模型分流脚本 `router.py` 以及零写入 `--audit-upgrade` 检查功能：

- **扩展 Claude Code 全局规则、只读子代理与生命周期钩子（`template/user-guard/`、`template/claude/`、`install.py`）**：新增 `--app claude` 与 `--app all` 安装模式，同步 `~/.claude/CLAUDE.md`（保留末尾自定义段落）与三个只读子代理，并将原话恢复、项目外写操作拦截、防重复等待回复及长会话交接四个钩子适配 `--client claude` 事件协议。
- **新增本地模型分流服务与供应商卡片管理脚本（`template/claude/router/`）**：提供纯标准库 HTTP 分流脚本 `router.py`（支持按模型前缀转发主对话与子代理请求、WSL2 宿主机代理探测及 60 秒卡片密钥只读缓存）、卡片管理命令行 `ccswitch_admin.py`（支持 SQLite 在线备份、卡片初始化、保留钩子配置安全切卡及停用旧提示词）以及 WSLg 启动与 `.deb` 升级辅助脚本。
- **新增 `--audit-upgrade` 只读检查器（`install.py`、`.github/workflows/ci.yml`、`tests/`）**：Windows 应用包探测改用标准库 `winreg` 只读查询，支持零磁盘写入检查各客户端版本、SQLite 表结构、钩子配置与模型窗口参数一致性。

---

## [1.7.5] - 2026-11-14

规范纯 ASCII 文档路径、补充英文概览与跨平台 GitHub Actions 自动回归测试：

- **文档文件名纯 ASCII 规范化（`docs/workflow-guide.md`、`docs/architecture-and-hooks.md`、`README.md`、`template/user-guard/README.md`）**：将 `docs/` 下文档重命名为小写 ASCII 短横线文件名，消除跨平台 Git 路径八进制转义与 URL 编码问题，并更新全仓相对链接。
- **补充英文说明与许可证边界（`README.md`）**：在根 `README.md` 补充 `English Overview`，说明支持的客户端范围、Python 3.11+ 标准库运行环境以及内置简体中文规则模板的适用范围。
- **统一单一版本号与行尾换行符规范（`template/user-guard/plugin.json`、`template/user-guard/README.md`、`install.ps1`、`.gitattributes`）**：移除子目录文档中的重复版本号，以 `plugin.json` 作为唯一版本来源；保持 `install.ps1` 为 PowerShell 5.1 所需的 UTF-8 BOM + `CRLF` 编码，其余文本与脚本统一使用 UTF-8 无 BOM + `LF`。
- **新增跨平台 CI 工作流与 Windows `8.3` 短路径兼容修复（`.github/workflows/ci.yml`、`install.py`、`status_guard.py`、`tests/`）**：新增覆盖 `ubuntu-latest` 与 `windows-latest`（Python `3.11` 与 `3.13`）的回归测试工作流；修复 Windows 临时目录以 NTFS `8.3` 短路径（如 `RUNNER~1`）传入时的路径边界判断与托管钩子去重问题。

---

## [1.7.4] - 2026-11-14

精简常驻全局规则篇幅，统一 `Python 3.11+` 标准库基线并移除未使用的旧版本兼容代码：

- **精简常驻全局规则并补充反过度工程要求（`template/user-guard/rules/AGENTS.md`、`AGENTS.md`、`readonly-*` 子代理）**：删去常驻规则中与技能说明重复的命令行参数罗列；在 `R3.3` 明确不为未要求的老旧版本、外围平台或假想场景编写冗余兼容分支；在 `R3.6` 明确仅针对目标环境如实标注“未验证”项；并将原生 Shell、UTF-8 编码与公开仓库脱敏要求并入 `R4`。
- **统一 `Python 3.11+` 标准库实现（`install.py`、`install.ps1`、`install.sh`、`cae_harness.py`、`status_guard.py`）**：直接使用标准库 `tomllib`、`ast.Match` 与原生 ISO 时间戳解析，删除针对 `Python 3.9/3.10` 的手写 TOML 回退解析器与 AST 兼容垫片，聚焦 Windows 与 WSL2／Linux 环境。
- **Codex 钩子启动参数升级对齐（`install.py`）**：升级已有 `session_context_loader.py` 托管钩子时自动将旧版 `-I -B` 参数补齐为 `-X utf8 -I -B`，保持重复执行幂等。

---

## [1.7.3] - 2026-11-14

补充 AST 静态路径检查覆盖范围、长会话交接子壳分组解析与编排器验证标记：

- **阶段收尾代码测试与文档脱敏分工（`R3.1.4`、`AGENTS.md`）**：明确版本收尾前同步完成全仓回归测试、死代码清理、文档润色与脱敏核查。
- **完善内联脚本关键字与解包参数路径检查（`status_guard.py`）**：补齐 `Path.rename/replace`、`os.remove/unlink/mkdir/makedirs/rmdir/removedirs/rename/replace`、`shutil.copy*/move/rmtree`、`np.save*`、`open(file=..., mode=...)` 及 `run_workflow` 中 `agent(**kwargs)` 的路径静态检查。
- **加固长会话子壳分组拦截与压缩恢复游标（`handoff_gate.py`、`session_context_loader.py`、`user_words.py`、`reply_repeat_guard.py`）**：支持拆分引号外的 `( ... )` 子壳命令以拦截交接期 `(git push ...)` 绕过；修复 Codex 压缩事件游标重置、空状态文件自愈、Windows 文件锁显式释放及大体积交接文件尾行逆向读取。
- **强制校验非空验证命令与特殊文件跳过保护（`cae_harness.py`）**：拦截空字符串 `--verify-cmd`，在回执中显式输出 `verification_executed` 布尔字段；合并文件快照 `lstat` 调用并跳过 FIFO、socket 等特殊文件防止阻塞。
- **安装脚本流透传与目录联接区分（`install.py`、`install.ps1`）**：`install.ps1` 改用标准流直接继承与原生参数转义；`install.py` 区分云同步目录重解析点与目录联接，并在升级时自动清理废弃空目录。

---

## [1.7.2] - 2026-11-14

适配 Antigravity 桌面端 `v2.32.0` 与 CLI `v1.5.8`，新增 `run_workflow` 静态路径检查与 `agy` 超时诊断识别：

- **明确前后端协作分工**：主会话负责需求梳理、文献检索与逐行复核，复杂公式推导、核心求解器编码与带验证命令的闭环测试交由后台无头工作流执行。
- **只读子代理配置兼容桌面端与 CLI**：移除子代理配置中会导致桌面端隐藏代理的 `hidden: true` 字段，保留 `subagent: true`、`mainAgent: true` 与 `[read_file, search_web, send_message, finish]` 工具列表。
- **将 `run_workflow` 与文件修改工具纳入 `R4` 边界检查（`status_guard.py`）**：补齐多类文件修改工具名与路径参数识别，将 `view_file` 纳入未变化日志重复读取拦截，并在 `run_workflow` 启动前静态检查外层 `Cwd`、`ScriptPath` 脚本与内联 `Script` 中的写路径。
- **增强内联 Python 路径静态追踪与冻结期 Git 分支写保护（`status_guard.py`、`handoff_gate.py`、`reply_repeat_guard.py`）**：按执行顺序追踪变量赋值、海象表达式、序列解包、分支合并与 `os.chdir`；在 `freeze` 阶段对 `git branch` 仅放行纯查询参数，并在 `reply_repeat_guard.py` 中同时兼容 `continuationActive` 与 `continuation_active` 续跑字段。
- **完善 `agy` CLI 超时判定与插件清单严格校验（`cae_harness.py`、`plugin.json`、`tests/`）**：识别 `agy --print-timeout` 退出码 `3` 与 `AGY_ERROR` 超时行，过滤正常业务正文后再做限流分类以防误判；补齐 `plugin.json` 的 `$schema`、`license` 与 `assets/logo.svg`，通过 `agy plugin validate --strict` 校验并新增全文件编码与脱敏扫描测试。

---

## [1.7.1] - 2026-11-14

- **默认启用单模型最高推理挡位与按需节省额度**：`cae-harness` 在推导、编码、审查与验证闭环任务中默认选用单模型非委派最高推理挡位（跳过会自动派生多层子代理的 `ultra` 挡），仅在用户明确要求节省额度时调低挡位。
- **安装器与钩子路径校验加固**：完善跨平台符号链接与目录联接检查、Codex `hooks.json` 升级合并逻辑及状态文件锁超时保护。

---

## [1.7.0] - 2026-11-14

- **配额冷却记录与 `--fallback-on-quota` 自动切换**：被调用客户端返回配额耗尽时按运行侧（`windows` 或 `posix`）记录冷却截止时间；开启 `--fallback-on-quota` 且工作区文件尚未改动时，自动切换至已就绪的备用客户端继续执行。
- **原生 `inherit` 子代理与后台选模解耦**：移除 Antigravity 只读子代理中的固定模型字段，使其默认跟随主会话模型，与 `cae-harness` 后台独立选模互不干扰。

---

## [1.6.1] - 2026-11-14

- **长会话分段交接钩子（`handoff_gate.py`）**：新增 `normal -> advisory -> drain -> freeze` 状态机，配合 `PreInvocation`、`PreToolUse` 与 `Stop` 事件引导整理交接摘要并检查 `<!-- GOAL_CANCELLED -->` 结束标记。
- **跨平台安装入口与 Zotero MCP 合并支持**：完善 `install.py`、`install.ps1`、`install.sh` 三个入口及 `mcp_config.json` 非破坏性合并。

---

## [1.5.0] - 2026-11-13

- **初始化 `user-guard` 插件与 `cae-harness` 编排脚本**：建立 `R0–R11` 常驻工程规则、会话原话增量归档与压缩恢复注入（`session_context_loader.py`）、`R4` 项目目录写边界与重复读日志拦截（`status_guard.py`）、防重复等待回复拦截（`reply_repeat_guard.py`）、三个只读子代理配置及离线回归测试集。
