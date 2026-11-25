---
name: fleet-evolve
description: >
  在上游配置仓库或明确授权的全局工具链任务中维护四客户端配置。
  全面更新评估外部能力与本机条件，全量优化加固现有实现，
  通过具体提案、用户批准、事实锚定和分层验收完成变更。
---

# fleet-evolve 维护规程

本技能用于上游四客户端配置仓库，或用户明确要求演进宿主机全局工具链的任务。通用课题项目的推导、求解器实现和文档修订使用 `cae-harness`，不能借此修改宿主机配置或长期模型推荐。

先核对任务授权与写入范围，再用 `scripts/fleet_state.py status` 读取工作区身份和状态锁。工作区或父目录同时包含 `install.py` 与 `template/user-guard/plugin.json` 时识别为 `workspace_mode: upstream_config_repo`；独立安装的 `fleet_state.py` 在通用目录未指定 `--repo` 调用时返回 `not_upstream_config_repo`（退出码 `2`）。限定文件的实现包只完成包内工作，不调用本编排器或写入全局状态。

## 维护模式与单一配置来源

| 模式 | 要完成的工作 | 边界 |
| --- | --- | --- |
| `full`，全面更新 | 收集官方更新和本机记录，核对升级、模型能力、本机可用性与额度类型，评估角色分工及运行参数。 | 提交具体方案，由用户决定后才实施升级或同步。 |
| `hygiene`，全量优化 | 基于已审定配置修正代码、钩子及路由问题，优先将纠错下沉为单测与守卫并删薄已被守住的文字规则，完成事实核对、脱敏与回归。 | 不自动升级外部工具，不自行改变长期推荐或运行阈值。 |
| `audit`，只读巡检 | 核对配置漂移、供应商卡片、配额冷却和二进制接口（ABI）风险。 | 不安装、不升级、不刷新缓存、不改变状态。 |

[fleet_profile.json](../cae-harness/fleet_profile.json) 是角色与参数的唯一配置来源，集中维护五个角色槽位、`native_agent_profiles`、`runtime_tunables`、`subscription_matrix` 与 `capability_ledger`。常驻文档不散布时期性模型名、静态日期和运行阈值。测试可用 `CAE_FLEET_PROFILE` 指向隔离配置。

## 第一道门：联网评估与启停决策

`full` 按四个独立维度逐项盘点并提审：
1. **四端客户端与配套工具**：分别核查 Antigravity、Codex、Claude Code、DeepSeek Harness 与 CC Switch 的本地 CLI 及桌面端版本、上游更新及协议能力（DSH 核对 `hook_protocol`、`hard_hooks_supported` 与 `meta_update_trigger`，CC Switch 核对卡片、路由与旧提示词）。
2. **Windows 与 WSL2 双端**：依据 `install.py --audit-upgrade` 的 `cross_side_status` 与 `wsl_probe`，检测到对侧客户端或配置时分别核查与预览。
3. **各客户端内生代理维度**：在 `native_agent_profiles` 中独立评估主会话默认模型与挡位、内生子代理机制、只读代理定义与并发上限。
4. **跨端协同维度（`cae-harness`）**：独立评估五个角色槽位、`subscription_matrix`、`runtime_tunables`、持久化无头会话及 `--isolation` 各端支持边界。

评估结果交用户在审批节点决定启停，写入 `subscription_matrix`（`enabled: false` 全局跳过），并按两条正交能力轴拆解角色归属：① **无确定性验证器场景下的审慎拒答校准**（未闭环理论总控、论文定稿与正向立论）；② **有硬验证器约束下的代理闭环执行与挑刺吞吐**（受 `--write-scope` 与 `--verify-cmd` 约束的实现流、并行编码流与反例挑刺流）。联网刷新与本机实测日期分别写入 `capability_ledger`。

第二道离线复查门禁、确认单票据、压缩恢复与完工回执核验由 `cae_harness.py` 及四阶段钩子统一守卫，操作细则见 [cae-harness/SKILL.md](../cae-harness/SKILL.md)。

## 四类必须经用户审批的节点

以下事项先提出具体方案，获批前不实施对应动作：
1. 生成或调整客户端、模型与推理强度推荐矩阵，以及 `runtime_tunables`。
2. 写入宿主机全局配置目录或切换主会话客户端（展示完整目标路径、保留方式、安装只读预览清单及数据库备份方案）。
3. ABI 强耦合升级（含编译型科学计算包的系统级 Python 跨次版本、CUDA 跨大版本和 WSL 跨 LTS 发行版升级）。
4. 执行 `git push`。

## 探测、实现与验证

常用只读探测入口：

```powershell
python -B -X utf8 install.py --probe-apps
python -B -X utf8 install.py --audit-upgrade
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py probe --caller auto
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py assess --caller auto
```

重要文档按需做 `clean_room` 盲读、主会话整篇精炼重写、源码事实与公开脱敏校核。文件采用 UTF-8 无 BOM 和 LF；两份 `AGENTS.md` 各自严格小于 `16,000 B`。公开文档禁含真实用户名、内网 IP、具体硬件型号、凭据、账号精确账单指纹或会话唯一标识。验收严禁放宽容差、守恒残差与收敛阶判据，严禁删改断言或将局部补测冒充全量回归通过：

```powershell
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict --doc-hygiene
python -B -X utf8 -m unittest tests.test_e2e_integration
python -B -X utf8 -m unittest discover -s tests -p "test_*.py" -v
```

本机已装 `agy` 时配合 `agy plugin validate --strict template/user-guard` 校验清单。

## 提案、同步与重启复核

触及审批节点时用 `fleet_state.py propose` 登记方案，全局同步先运行 `install.py --dry-run --yes` 展示完整路径待用户决定；获批后执行 `approve` 及同步，并用 `install.py --audit-upgrade` 核查 `summary.all_aligned`。主会话客户端升级或切换需重启时，以 `record-pass1`、`verify-pass2` 及 `clear --verified --readback-done --session-id "<重启后会话标识>"`（无活跃重启认领锁时可省略 `--session-id`）通过事实锚定硬门禁后归位：

```powershell
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" status
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" propose --session-id "<当前会话标识>" --summary "<待审具体方案>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" approve --session-id "<当前会话标识>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" record-pass1 --session-id "<当前会话标识>" --target-master "<目标客户端>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" verify-pass2 --session-id "<重启后会话标识>"
python -B -X utf8 template/user-guard/skills/fleet-evolve/scripts/fleet_state.py --repo "<上游根目录>" clear --verified --readback-done --session-id "<重启后会话标识>"
```

最终汇报 Git 状态与实际修改范围，分别说明配置或文档已完成、离线检查已通过、本机实际环境实测已通过；仅列当前目标环境和核心验证链路的未验证项。
