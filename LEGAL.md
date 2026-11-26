# 法律声明、合规边界与免责说明（Legal & Compliance Notice）

本文集中说明 `Antigravity_Codex_ClaudeCode_DeepSeekHarness` 的开源许可关系、第三方商标边界、创作归属、风险提示与合规使用要求。**本文件仅作信息披露与风险提示，不构成正式法律意见，也不修改或替代根目录 [LICENSE](LICENSE) 中的 Apache License 2.0 正文条款。** 本文中英文内容如有不一致，以中文为准；涉及开源许可条款的，以 [LICENSE](LICENSE) 英文原文为准。本声明任一条款若依适用法律被认定无效或不可执行，不影响其余条款继续有效。

配套治理文档：[安全政策](SECURITY.md) · [贡献指南](CONTRIBUTING.md) · [社区行为准则](CODE_OF_CONDUCT.md) · [归属声明](NOTICE)。

---

## 一、独立开源项目与第三方商标声明

1. **无官方关联与背书**：本项目为个人维护的独立第三方开源工具，与 Google、OpenAI、Anthropic、DeepSeek、Moonshot AI、Microsoft、GitHub、Zenodo、Zotero、FreeTSA 或第三方开源项目 CC Switch 等任何厂商、组织或权利人均不存在官方隶属、授权代理、商业合作、赞助或背书关系。
2. **商标权属、无商标授权与调整意愿**：仓库名称、文档、配置与脚本中提及的 Antigravity、Gemini、Google、OpenAI、Codex、ChatGPT、Anthropic、Claude、Claude Code、DeepSeek、Moonshot AI 与 Kimi 等标识均归各自权利人所有。提及的 CC Switch、Zotero、Windows、PowerShell、Linux、WSL、Python、Microsoft、GitHub、Zenodo 与 FreeTSA 等产品名及标志同样归各自权利人所有。依 Apache License 2.0 第 6 条，本许可不授予任何商号、商标或产品名称使用许可。上述名称仅用于说明配置兼容、命令行调用与接口适配关系，不表示任何来源、隶属或认可；若权利人认为名称或用法可能引起混淆，作者愿意依第六节程序沟通并在合理期限内调整。
3. **动态配置文件无商业评测或贬损意图**：`template/user-guard/skills/cae-harness/fleet_profile.json` 中的角色映射、上下文窗口参数、能力备注、`excluded_vendors` 与 `blocked_auto_tokens` 等配置项，仅为作者面向科学计算与工程研发环境的阶段性非商业适配记录。该文件不构成对任何第三方产品或服务的官方基准评测、质量承诺或商业评价，用户应结合自身实测与业务需求自行审阅并修改。

---

## 二、原创归属、许可条件与资助说明

1. **开源许可与再分发条件**：本仓库原创内容由 `Xinyu Li (Xinyu-Li-1997)` 依 [Apache License 2.0](LICENSE) 公开发布。再分发本项目或其衍生作品时，须遵守该协议第 4 条各项条件：向接收者提供许可证副本（第 4(a) 条）；在修改过的文件中附加显著修改说明（第 4(b) 条）；在衍生作品源码中保留原作品的版权、专利、商标及归属声明，与衍生作品任何部分均无关的声明除外（第 4(c) 条）；并依第 4(d) 条以可读形式包含 [NOTICE](NOTICE) 所载、与该衍生作品相关的归属声明。依该协议第 3 条，被许可人提起专利诉讼（包括诉讼中的交叉请求或反诉），主张本项目或其中纳入的贡献构成直接侵权或帮助侵权的，其依本协议就本项目获得的专利许可自起诉之日起终止。
2. **创作归属、资助来源与贡献权属**：本仓库原创核心由作者在非职务开发任务下独立设计与编写，未使用任何机构或个人的涉密资料、商业秘密或非公开内部源码。如 [README.md](README.md) 所载，作者在国家留学基金管理委员会资助的留学期间完成本项目，截至本版本未获得其他专项经费资助；该致谢仅客观反映个人留学奖学金资助情况，不代表资助机构对本软件的官方背书或委托开发。经合并的外部贡献以 Git 提交历史中的署名为准，其著作权归各贡献者所有并依 [CONTRIBUTING.md](CONTRIBUTING.md) 授权。用户借助第三方模型生成内容的可版权性与权属依适用法律判断，作者对此不主张权利，亦不作可版权性担保。
3. **时间戳与长期存档边界**：`.timestamps/` 目录中的 RFC 3161 时间戳与 Zenodo 存档记录仅针对对应历史发布归档包，可用于核验特定字节内容在时间戳所载时刻已经存在且此后未被改动；该记录不能单独证明作者身份，也不构成公证，其作为电子数据的证明力由裁判机构依法认定。如需境内正式司法举证，建议另行办理境内可信时间戳或公证。

---

## 三、重要免责声明与责任限制

**请在使用、安装、执行或再分发本仓库任何脚本、钩子或配置前，仔细阅读以下全部加粗条款；如对任一条款含义或风险边界存在疑问，欢迎在执行前通过第六节渠道联系维护者说明，或直接审阅对应开源源码后再行决定。**

1. **按现状提供且无任何担保**：**依据 Apache License 2.0 第 7 条，在适用法律允许的最大范围内，本仓库全部源码、配置模板、规则、脚本与文档均按现状提供，作者及贡献者不提供任何明示或默示担保，包括但不限于权属、非侵权、适销性、特定用途适用性、持续可用性或零缺陷保证。**
2. **责任限制与法定边界**：**本仓库系免费公开的开源工程工具，源码完全透明且执行完全由用户自主控制。依据 Apache License 2.0 第 8 条，除各方另行签署明确书面协议外，在适用法律允许的最大范围内，作者及贡献者不对因使用或无法使用本仓库所引发的任何直接、间接、附带、特殊或后果性损害承担赔偿责任，包括但不限于数据丢失、配置覆盖、计算错误、业务中断、商誉损失、第三方账号限制或接口费用损失。适用法律强制规定不得免除的责任不受本条限制，包括《中华人民共和国民法典》第五百零六条所指造成对方人身损害的责任，以及因故意或者重大过失造成对方财产损失的责任。**
3. **安全关键领域设计边界与风险警示**：**本仓库的规则、生命周期钩子与核验脚本仅是科学计算与软件研发过程的辅助检查工具，不能替代人工审查、独立的物理或数值验证及法定安全认证。本项目并非为核工程、航空航天、医疗器械、自动驾驶、承压特种设备、关键基础设施或其他涉及生命财产安全的高风险用途而设计或验证；未经充分的独立人工核验与法定审定，不应将本工具或其辅助生成的求解器、代码、算例或技术报告直接用于上述场景的生产决策。用户仍执意如此使用的，全部风险与责任由用户自行承担。本条系设计边界与风险提示，不构成对 Apache License 2.0 所授权利的附加限制。**
4. **本地写入、系统变更、第三方脚本与费用风险**：
   - **配置目录写入与清理**：`install.py` 会写入用户主目录下的客户端配置路径，在探测阶段以只读方式核查客户端元数据或注册表，在升级阶段按规则清理退役脚本并写入模板配置。执行正式写入前请先审阅源码、运行 `--dry-run --yes` 只读预览并备份既有配置。
   - **本地数据库与回环路由**：`ccswitch_admin.py` 会读取或更新用户本机 CC Switch 的 SQLite 数据库。`router.py` 仅监听本机回环地址，在内存中读取卡片密钥并按配置转发 HTTPS 请求，仅在用户显式开启 `follow_windows_proxy` 时读取 Windows 系统代理注册表。
   - **WSLg 窗口恢复脚本**：`launch-cc-switch-wsl.sh` 在 WSL 环境下调用本地窗口接口检查可见性，并在窗口异常隐藏时可能重启无响应的 CC Switch 进程。
   - **第三方安装包下载风险**：`update-cc-switch-wsl.sh` 仅在用户显式执行时联网访问独立开源项目 `farion1231/cc-switch` 的 GitHub 发布页下载 `.deb` 安装包、结束正在运行的 CC Switch 进程并调用 `sudo apt install`；该脚本默认校验 SHA-256 摘要，若用户传入 `--no-hash` 跳过摘要核验，全部供应链安全风险由用户自行承担。
   - **第三方费用与条款风险**：各官方客户端、模型接口及第三方兼容端点产生的订阅费、按量 API 费用、配额消耗、限流、政策变更或账号封禁后果，均由用户自行承担。

---

## 四、运行方式、数据流向与合规使用提示

1. **本地运行与用户触发的数据流向**：本仓库的核心校验命令与会话生命周期钩子均在用户本机运行，不内置任何远端遥测或数据回传服务，不向作者或无关第三方上传用户源码、对话轨迹或账号凭据。需要明确说明的是，`cae_harness.py` 等跨客户端调度脚本会按用户指令调用用户自行安装的官方在线客户端，用户输入的代码、文档与提示词将由相应客户端依其服务条款传输至对应模型服务商服务器，并可能涉及数据跨境传输；可选的 `router.py` 会将请求转发至用户自配的上游接口；可选的 `update-cc-switch-wsl.sh` 仅在用户主动执行时访问 GitHub。`router.py` 强制仅监听本机回环地址，**不提供跨境网络代理、第三方中转反代、凭据窃取或任何绕过平台访问控制与服务条款的功能**；使用非官方兼容端点可能违反上游服务条款，用户须自行核实授权并承担全部后果。
2. **非生成式人工智能服务提供者声明**：本仓库仅为本地运行的开源配置与工程校验工具，不训练、不托管、不部署亦不转售任何大语言模型，不向公众提供生成式人工智能服务或互联网信息服务。用户如将本仓库封装为面向公众的在线服务，须自行履行适用法律法规要求的全部备案与安全评估义务。
3. **适用法律法规与合规提示**：用户使用本仓库及配套第三方客户端、模型服务与网络环境时，须独立遵守中华人民共和国及用户所在地全部适用法律法规与各第三方服务条款。用户应特别注意遵守《数据安全法》《网络数据安全管理条例》《个人信息保护法》及数据出境相关办法、《保守国家秘密法》《出口管制法》《两用物项出口管制条例》与技术出口管理规定、知识产权法、《人工智能生成合成内容标识办法》以及科研诚信管理规定。以下事项系对法定义务与合规风险的提示，不构成对 Apache License 2.0 所授权利的附加限制；用户违法违规使用的，相关责任由用户自行承担：
   - 不应在涉密计算机、涉密信息系统或连接互联网的涉密环境中安装、运行本工具或任何在线第三方客户端；不应将国家秘密、商业秘密、受出口管制或制裁约束的数据、技术与软件，或未获合法授权的个人信息输入外部在线模型。
   - 不应将处于同行评议保密期的他人未发表稿件、基金申请书或受保密协议约束的材料输入外部在线模型；对外发布或投稿人工智能辅助产出的文本与代码时，应遵守目标期刊、机构及适用法规的标识与披露要求。
   - 不应将本仓库用于恶意入侵、绕过访问控制、破坏计算机信息系统、侵犯他人知识产权或其他违法违规活动。

---

## 五、第三方组件与外部软件边界

- **运行时零第三方代码内嵌**：据维护者核查，本仓库核心脚本与钩子运行时仅依赖 Python 3.11+ 标准库，未在源码树中复制或内嵌任何第三方开源代码库，不构成强 Copyleft 许可证下的衍生作品。
- **公钥证书与构建期工具**：`.timestamps/V1.0.0/freetsa-cacert.pem` 为 FreeTSA 公开根证书，权属归 FreeTSA 所有，仅用于离线验证对应版本的 RFC 3161 时间戳签名。`.github/workflows/ci.yml` 中引用的 GitHub Actions 仅在持续集成流水线运行期使用，不随本仓库源码分发。
- **外部软件与独立扩展**：用户自行安装的各官方客户端、CC Switch、Zotero、可选第三方 `zotero-mcp` 扩展及相关模型服务，均受其各自独立的许可证与用户协议约束。本仓库不转授其任何权利，也不对其可用性、安全性或合规性承担责任。

---

## 六、知识产权与合规异议沟通机制

作者尊重各方合法知识产权与其他民事权益。若任何个人、企业或机构认为本仓库中的文本、配置、脚本、仓库名称或引用内容存在商标、著作权、专利、隐私或其他合规疑虑，可通过以下对应渠道提交具体说明：

1. **正式侵权投诉与含隐私权属材料（GitHub 官方渠道）**：如涉及身份证明、非公开权属证书、私人联系方式或正式版权与商标下架请求，请优先通过 [GitHub 官方内容移除流程](https://docs.github.com/en/site-policy/content-removal-policies/submitting-content-removal-requests) 提交，以便由 GitHub 依平台政策私密接收并转交处理。
2. **可公开脱敏异议（仓库异议模板）**：如涉及商标澄清、署名勘误、措辞调整等无需提交私人敏感证件的情形，可使用 [合规与知识产权异议模板](.github/ISSUE_TEMPLATE/compliance_and_ip_notice.md) 提交 Issue。**注意：GitHub Issue 为公开页面，请勿在其中填写身份证件号、私人住址、手机号或保密原件。**
3. **安全漏洞专属通道**：GitHub 仓库 **Security** 页面的私密报告功能仅用于受理 [SECURITY.md](SECURITY.md) 所界定的安全漏洞，不适用于常规商业沟通或非安全类权属争议。

为便于核实与处理，请在通知中写明所涉文件路径与行号、权利证明或可核查的事实依据、具体诉求、有效联络方式，并附具通知内容真实准确且通知人为权利人或经授权代理人的声明。作者收到内容明确、依据充分的通知后，将在合理时间内及时核查，并视核查结果与通知人沟通采取澄清、修正、补充说明或移除等措施；适用平台规则及《中华人民共和国民法典》第一千一百九十五条至第一千一百九十七条所涉反通知与恢复程序的，依法依规办理。

**善意处理不构成自认与防范不实通知**：作者为善意化解争议而作出的澄清、修改、隐藏或移除，不构成对通知所述事实、侵权指控或法律责任的认可，也不放弃作者依法享有的任何权利与抗辩。任何主体提交虚假、伪造或恶意通知并给作者或第三方造成损失的，应依《中华人民共和国民法典》第一千一百九十五条第三款及相关法律承担相应责任，作者保留固定证据并依法追责的权利。

---

## English Summary

This English summary is provided for convenience only; in case of discrepancy, the Chinese text prevails, while the English text of [LICENSE](LICENSE) governs Apache License 2.0 terms. If any provision of this notice is held unenforceable under applicable law, the remaining provisions remain in effect.

`Antigravity_Codex_ClaudeCode_DeepSeekHarness` is an independent open-source project released under [Apache License 2.0](LICENSE) (`Copyright 2026 Xinyu Li (Xinyu-Li-1997)`). It is **not affiliated with, endorsed by, or sponsored by** Google, OpenAI, Anthropic, DeepSeek, Moonshot AI, Microsoft, GitHub, Zenodo, Zotero, FreeTSA, or CC Switch. Pursuant to Section 6 of Apache License 2.0, no trademark license is granted; all third-party product names belong to their respective owners and are used solely to describe compatibility, and the author is willing to discuss reasonable naming or wording adjustments upon good-faith notice.

**AS-IS, Liability Limitation & Design Boundary:** To the maximum extent permitted by applicable law, this repository is provided **"AS IS"** without warranties of any kind (Section 7), and the author and contributors are not liable for direct or indirect damages arising from its use (Section 8), except where liability cannot be excluded under mandatory applicable law (such as personal injury or property loss caused by intent or gross negligence). This tool is not designed or certified for nuclear, aerospace, medical, autonomous, pressure-equipment, critical-infrastructure, or other life-safety production decisions without independent human verification and statutory certification; this is a risk notice and adds no restrictions to Apache License 2.0.

**Local Execution, Data Flow & Notices:** Core verification commands and hooks run locally on the Python 3.11+ standard library with zero telemetry and do not provide public generative AI services, proxy circumvention, or access-control bypass. However, dispatch scripts invoke user-configured online official clients at the user's direction (transmitting user-provided content under each provider's terms, potentially cross-border), `router.py` forwards loopback requests to user-configured upstreams, and `update-cc-switch-wsl.sh` downloads third-party `farion1231/cc-switch` releases from GitHub upon explicit execution. For formal intellectual property notices containing private proof, please use [GitHub Content Removal Policies](https://docs.github.com/en/site-policy/content-removal-policies/submitting-content-removal-requests); for sanitized public notices, use the [compliance issue template](.github/ISSUE_TEMPLATE/compliance_and_ip_notice.md). Good-faith adjustments do not constitute admission of liability, and false or malicious notices carry statutory liability under applicable law.
