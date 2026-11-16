# RFC 3161 时间戳凭证

本目录保存对已发布制品申请的 RFC 3161 可信时间戳，用于证明对应文件在某一时刻已经存在。凭证仅作完整性参考，不构成法律意见或公证。

## V1.0.0

| 项目 | 内容 |
| --- | --- |
| 制品 | `Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip` |
| 来源 | Zenodo 记录 [10.5281/zenodo.24293508](https://doi.org/10.5281/zenodo.24293508)（V1.0.0 版本 DOI） |
| 大小 | 656,229 字节 |
| SHA-256 | `3a641f079bec69836f09437d06c9aca370cbb7a478bd78a7f1e6c5c5d567fb63` |
| 时间戳机构 | FreeTSA（`https://freetsa.org/tsr`） |
| 时间戳时间 | Nov 16 22:01:49 2026 GMT（UTC+8 为 Nov 17 06:01:49 2026） |
| 响应序列号 | `0x0A12E460` |

文件说明：

- `Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip.sha256`：制品 SHA-256 摘要。
- `Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip.tsq`：RFC 3161 时间戳查询（DER）。
- `Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip.tsr`：RFC 3161 时间戳响应（DER）。
- `freetsa-cacert.pem`：FreeTSA 根证书，用于校验证书链。

时间戳针对 Zenodo 存档的发布包，而不是 GitHub 自动生成的源码压缩包；后者不保证长期字节一致，不适合作为固定哈希对象。

## DOI 体系与后续发版

- **Concept DOI** `10.5281/zenodo.24293507`：代表整个项目，始终解析到最新版本。根目录 `README.md` 的徽章使用它，发布新版本后无需修改徽章。
- **Version DOI** `10.5281/zenodo.24293508`：V1.0.0 的固定存档标识；本目录的时间戳凭证针对该版本存档，因此引用它。
- 后续发版：在 GitHub 新建标签并发布 Release，Zenodo 会自动归档并生成新的 Version DOI；Concept DOI 自动跟随最新版。已发表的标签不要移动或重打。

### 复核

在 `V1.0.0` 目录下执行：

```bash
curl -L -o Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip \
  "https://zenodo.org/api/records/24293508/files/Xinyu-Li-1997/Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip/content"

sha256sum Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip
openssl ts -verify -data Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip \
  -in Antigravity_Codex_ClaudeCode_DeepSeekHarness-V1.0.0.zip.tsr \
  -CAfile freetsa-cacert.pem
```

`sha256sum` 应等于上表的 SHA-256，`openssl ts -verify` 应输出 `Verification: OK`。
