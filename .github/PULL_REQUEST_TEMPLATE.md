## 变更说明（Summary）

- 变更目的与背景：
- 涉及的主要文件：

## 本地验证（Verification）

Windows 请使用 `python`，WSL2 与 Linux 请将命令中的 `python` 替换为 `python3`：

- [ ] 已运行 `python -B -X utf8 template/user-guard/skills/cae-harness/scripts/cae_harness.py check-grounding --target . --strict --doc-hygiene` 且零告警通过
- [ ] 已运行 `python -B -X utf8 -m unittest discover -s tests -p "test_*.py"` 且全量通过
- [ ] 未引入 Python 3.11+ 标准库以外的运行时依赖，未放宽既有测试容差或删改断言
- [ ] 新增脚本已附带 `SPDX-License-Identifier: Apache-2.0` 标识；若变更涉及行为或第三方归属，已同步更新对应文档、测试与声明

## 权属、合规与脱敏确认（Legal & Compliance Checklist）

- [ ] 我已阅读 [CONTRIBUTING.md](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/blob/main/CONTRIBUTING.md) 与 [LEGAL.md](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/blob/main/LEGAL.md)，确认本提交按 [Apache License 2.0](https://github.com/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness/blob/main/LICENSE) 第 5 条授权
- [ ] 所有提交记录均已通过 `git commit -s` 附带 `Signed-off-by` 声明，符合 [Developer Certificate of Origin 1.1](https://developercertificate.org/)
- [ ] 据本人所知，本次变更不包含他人闭源源码、商业秘密、未授权第三方代码、保密科研数据或与 Apache License 2.0 不兼容的强 Copyleft 许可证代码
- [ ] 本次变更不包含真实 API Key、访问令牌、真实用户名路径、内网 IP、硬件型号或个人隐私信息
