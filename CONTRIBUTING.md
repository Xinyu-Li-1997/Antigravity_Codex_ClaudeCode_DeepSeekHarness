# 贡献指南与权属保证（Contributing Guidelines）

感谢关注 `Antigravity_Codex_ClaudeCode_DeepSeekHarness`。为保证本仓库的知识产权清白、跨平台质量与长期可维护性，所有 Issue、Pull Request 及其他代码或文档贡献均须遵守以下规范。配套治理文档见 [LEGAL.md](LEGAL.md)、[SECURITY.md](SECURITY.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 一、开源许可与贡献授权（Apache License 2.0 第 5 条）

依据根目录 [LICENSE](LICENSE) 中的 Apache License 2.0 第 5 条，除您在提交时明确作出相反声明外，您为纳入本仓库而有意提交的全部代码、补丁、文档、测试或配置文件，均按 Apache License 2.0 的条款与条件提交，不附加任何其他额外条款或条件；您与维护者另行签署的单独许可协议不受影响。您保留对个人贡献的著作权。标注为“Not a Contribution”或附有其他相反声明的内容，维护者将不予合并。

## 二、原创来源声明（Developer Certificate of Origin 1.1）

提交 Pull Request 时，所有提交记录须通过 `git commit -s` 附带 `Signed-off-by` 署名行，例如：

```text
Signed-off-by: Your Name <user@example.com>
```

为避免在公开提交历史中泄露真实个人邮箱，请在 Git 本地与 GitHub 账号设置中统一使用 GitHub 提供的 `noreply` 邮箱地址。

添加 `Signed-off-by` 即表示您确认并遵守 [Developer Certificate of Origin 1.1](https://developercertificate.org/) 的全部内容（以英文原文为准），其四项核心要点为：

1. 该贡献全部或部分由您本人创作，且您有权依本仓库采用的开源许可证提交；
2. 若该贡献基于他人在先作品，据您所知该作品适用相应的开源许可证，且该许可证允许您按本仓库许可证提交修改后的作品；
3. 若该贡献由他人直接提供给您，该人已就上述第 1、2 或 3 项作出确认，且您未作修改；
4. 您理解并同意本项目与贡献均为公开内容，贡献记录及随附提交的署名信息将被永久保留，并可依本仓库许可证再分发。

除上述原创来源声明外，提交前请自行完成项目合规自查：

- 属于职务作品的，须已取得雇主或所属机构的必要授权；借助人工智能辅助工具生成的内容，须由您本人独立审阅、测试并承担相应责任。
- 提交内容不得包含未经授权的闭源代码、商业秘密、保密科研资料、未发表审稿材料或受出口管制的数据。
- 据您所知，提交内容不侵犯他人知识产权，且未混入与 Apache License 2.0 不兼容的强 Copyleft 许可证代码；引用兼容开源作品时已完整保留原版权与许可声明。
- 若后续发现已合并贡献存在权属或合规瑕疵，维护者保留回退或移除相关提交的权利。

## 三、技术门槛与本地验证要求

本仓库核心实现仅依赖 Python 3.11+ 标准库，不引入外部第三方运行时依赖。提交 Pull Request 前，请在仓库根目录执行以下本地验证并确保全部通过：

```bash
# Windows (PowerShell)
python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict --doc-hygiene
python -B -X utf8 -m unittest discover -s tests -p "test_*.py"

# WSL2 / Linux
python3 -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict --doc-hygiene
python3 -B -X utf8 -m unittest discover -s tests -p "test_*.py"
```

提交前请一并自查四项基本规范：

1. **新增源码头**：新创建的 Python、Shell 或 PowerShell 脚本顶部应附带精简的版权声明与 `SPDX-License-Identifier: Apache-2.0` 标识。
2. **严禁夹带敏感信息**：不得在提交或描述中留存真实 API Key、认证令牌、真实用户名路径、硬件型号、私网 IP、真实个人邮箱或未脱敏会话日志。
3. **不弱化测试判据**：不得通过放宽容差、删改既有断言或缩窄文档校验范围来绕过测试。
4. **遵守文档分层**：面向不同读者的公开文档按 [AGENTS.md](AGENTS.md) 的四级分层单点维护，避免在多份文档中重复粘贴细节。

## 四、维护者裁量权

维护者有权自主决定对任何 Issue 或 Pull Request 进行审查、要求修改、关闭或不予合并。除贡献者依 Apache License 2.0 第 5 条就其贡献作出的许可外，提交 Issue 或 Pull Request 不使维护者负有合并、继续开发、技术支持或任何担保义务。

---

## English Summary

This English summary is provided for convenience only; in case of discrepancy, the Chinese text prevails, while the English text of [LICENSE](LICENSE) and [Developer Certificate of Origin 1.1](https://developercertificate.org/) governs their respective terms.

Unless you explicitly state otherwise, all contributions intentionally submitted for inclusion in this repository are licensed under [Apache License 2.0](LICENSE) (Section 5) without additional terms, while you retain copyright in your contributions. Every commit must include a `Signed-off-by` line (`git commit -s`, preferably using your GitHub `noreply` address) certifying compliance with Developer Certificate of Origin 1.1. Contributors must also ensure proper employer authorization where applicable, independent human review of AI-assisted changes, absence of incompatible strong Copyleft code or sensitive credentials, strict adherence to the Python 3.11+ standard library, and passing `check-grounding --target . --strict --doc-hygiene` plus the full offline `unittest` suite on Windows (`python`) or WSL2/Linux (`python3`).
