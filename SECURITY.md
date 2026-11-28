# 安全政策与漏洞披露（Security Policy）

本文件说明本仓库的安全覆盖范围、私密漏洞报告渠道与合理响应边界。完整法律声明与合规使用边界见 [LEGAL.md](LEGAL.md)。

## 一、支持范围

本仓库原则上仅针对 `main` 分支及最新发布的稳定版本提供安全修复维护。历史旧版本与未合并分支原则上不单独回溯补丁。

| 类别 | 是否在本仓库安全范围内 | 说明 |
| --- | --- | --- |
| 本仓库脚本、安装器与钩子 | 是 | 覆盖 `install.py`、`install.ps1`、`install.sh`、`cae_harness.py` 及 `_harness/`、`axiom_gate.py` 及 `_axiom_lib/`、`KernelAudit.lean`、`fleet_state.py`、共用 5 个生命周期钩子及 `hooks/_lib/`、`router.py`、`ccswitch_admin.py` 与辅助 Shell 脚本 |
| 文档脱敏、静默执行与状态机守卫 | 是 | 覆盖路径边界校验、符号链接防御、凭据脱敏规则、`axiom_gate.py` 探针显式授权（`--allow-probe-exec`）与复演包零自动执行边界及 CI 回归测试 |
| 第三方官方客户端、编译器与服务 | 否 | Antigravity、Codex、Claude Code、DeepSeek Harness（DSH）、CC Switch、Zotero、`zotero-mcp`、Lean 4 / `elan` 及上游模型接口的安全问题请直接向对应厂商或项目方报告 |
| 用户自建代理与外部环境 | 否 | 用户本机操作系统、外部网络代理及自行填写的密钥保管由用户自行负责 |

## 二、私密漏洞报告渠道

为防止未修复细节被滥用，请**不要**在公开 Issue、Pull Request 或公开评论区直接披露尚未修复的安全漏洞细节或可利用载荷。

请优先通过 GitHub 仓库的 **Security** 标签页中的 **Report a vulnerability**（GitHub Private Vulnerability Reporting）提交私密安全报告。该功能由 GitHub 平台托管，相关数据的存储与访问边界受 GitHub 平台隐私与安全条款约束。报告中建议附带以下四项信息：

1. 受影响的文件路径、函数或命令行参数。
2. 最小可复现步骤与运行环境（如 Windows、WSL2 或 Linux 及 Python 版本）。
3. 潜在安全影响评估（如越界写入、符号链接穿越、本地凭据泄露或命令注入）。
4. 若已有修复思路，可一并附上补丁建议。

**脱敏提醒**：提交安全报告时请彻底清除真实 API Key、访问令牌、真实用户名路径、内网 IP 与未公开课题数据。

## 三、合规测试边界与响应预期

- **禁止越界测试**：请遵守《网络产品安全漏洞管理规定》及适用网络安全法规，仅在您拥有合法权限的本地隔离沙箱环境中复现本仓库脚本问题；不应针对任何第三方模型接口、厂商服务器或他人主机实施未经授权的扫描、压力测试或越权探测。
- **响应预期与无赏金说明**：本项目为个人维护的非商业开源仓库，未设立任何漏洞赏金计划，不提供资金报酬。维护者将在合理时间内尽力核实并修复确认属实的安全缺陷，但不承诺商业级固定响应时限或服务等级担保。
- **协调披露**：经核实并完成补丁发布后，维护者将通过 GitHub Security Advisories 或 [CHANGELOG.md](CHANGELOG.md) 公开说明修复内容。

---

## English Summary

This English summary is provided for convenience only; in case of discrepancy, the Chinese text prevails.

Please report suspected security vulnerabilities in this repository privately through GitHub **Private Vulnerability Reporting** under the **Security** tab (hosted under GitHub's platform policies) rather than opening a public issue. Only code, hooks, and verification scripts maintained in this repository (`main` branch and latest release, including `cae_harness.py`, `axiom_gate.py`, `_axiom_lib/`, and `KernelAudit.lean`) are in scope; vulnerabilities in third-party clients, CC Switch, Zotero, `zotero-mcp`, Lean 4 / `elan`, or upstream model providers must be reported directly to their respective vendors. Remove all real API keys, tokens, personal paths, and confidential project data before submitting a report, and keep all testing strictly inside your own local sandbox. This personal open-source project offers no bug bounty and no commercial response-time warranty; confirmed fixes are disclosed via GitHub Security Advisories or [CHANGELOG.md](CHANGELOG.md).
