# R3 服务端配置诊断

R3 使用现有 `autopilot-policy/v1` 和 `publication-incident-registry/v1` 合同。本入口只选择和验证已有配置文件，不创建政策、不安装个人 Skill、不新增批准、签名或权限开关。有效的既有政策继续复用；每次服务启动不要求重新批准。

## 启动配置

服务导入时捕获以下进程环境，HTTP 参数和后续环境变化不能覆盖已捕获值；改变路径需重启服务。同一路径的内容在新预览时重新读取。

| 环境变量 | 默认值 | 限制 |
| --- | --- | --- |
| `ORBIT_R3_CONFIG_ROOT` | 当前仓库根 | 受信的绝对目录；自身及祖先不得为链接/reparse point |
| `ORBIT_R3_POLICY_PATH` | `config/product_publication_autopilot_policy.json` | 相对配置根；禁止绝对路径、盘符、`..`、ADS 与链接 |
| `ORBIT_R3_INCIDENT_REGISTRY_PATH` | `skills/publish-approved-product/references/incident-registry.json` | 同上 |

部署没有文件时仍能启动并报告缺项。不存在个人安装、历史工作树或 `core.config` 的配置 fallback。不要复制旧 ACTIVE 文件来消除缺项，配置文件必须来自当前已具备的业务授权。此说明不采用任何历史额度或店铺范围。

读取前检查根、祖先、目录和文件；只读取普通文件，最大 2 MiB，打开后检查身份及读取期间大小/时间变化。受信根仍须由部署者控制写权限；这不是对拥有同进程权限的恶意 writer 的隔离沙箱。

## HTTP 增量合同

`GET /api/product-workspace/publication-stages?offer_id=...` 和 `POST /api/product-workspace/r3-marketplace/preview` 返回新增 `configuration`，包括 COMMON 尚未完成时。两份文件总是分别诊断。COMMON 仍保持原执行门禁；配置可用不会使 COMMON 变为 VERIFIED。

```json
{
  "schema_version": "publication-runtime-config/v1",
  "status": "BLOCKED",
  "applies_to": "NEW_MARKETPLACE_PREVIEW",
  "documents": {
    "policy": {"source": "PROJECT_DEFAULT", "status": "MISSING", "content_digest": null},
    "incident_registry": {"source": "PROJECT_DEFAULT", "status": "MISSING", "content_digest": null}
  },
  "blockers": ["R3_CONFIG_POLICY_MISSING", "R3_CONFIG_INCIDENT_REGISTRY_MISSING"]
}
```

| document status | 含义 |
| --- | --- |
| `MISSING` | 文件/父目录缺失 |
| `MALFORMED` | JSON 或 UTF-8 无效 |
| `INVALID` | 现有 schema/字段合同不满足，或文件超限 |
| `AUTHORITY_UNCONFIRMED` | 仅 policy 现有 validator 的“未 ACTIVE”或“缺可归属的既有 authority”错误；不引入新的授权判定 |
| `UNSAFE_PATH` | 越界、链接/reparse point 或非普通文件 |
| `UNREADABLE` | 其他文件读取错误 |
| `CHANGED_DURING_READ` | 打开/读取时文件身份或内容元数据改变 |
| `VALID` | 现有 validator 通过，不表示该商品已批准发布 |

`source` 仅为 `PROJECT_DEFAULT` / `STARTUP_CONFIG`，不泄露路径。`content_digest` 是成功读取的原始字节 SHA-256（包括格式）；坏 JSON 也可有摘要，拒绝读取则为 null。两项 VALID 才是 CONFIGURED；该词只表示配置合同可用。诊断不返回政策全文、错误内容、绝对路径或个人 registry 全文。

新预览继续把已验证的政策及 registry 内容纳入现有冻结 payload/candidate digest，语义漂移使旧预览不能批准。只改变格式可能改变原始内容摘要，但不改变原有规范化候选摘要。已批准计划恢复继续使用原冻结 payload；当前文件缺失/漂移不撤销已有有效批准，也不重发 UNKNOWN。

R3 HTTP 返回中嵌入的 policy/registry 文档改为 `{schema_version, redacted: true, document_digest}` 摘要投影。服务端/ReleaseStore 保留原文，原计划与候选摘要不重算；HTTP 投影不是可提交/可重新计算摘要的 authority 文档。既有内部 compiler 合同没有修改。

## 给 UI owner 的最小接线建议

本包没有 UI 修改或浏览器验收。显示两个文档的状态和“配置来源未就绪”即可；COMMON 门禁单独显示。不要把 CONFIGURED 显示为“发布已批准”。已有批准/UNKNOWN 的商品，`applies_to=NEW_MARKETPLACE_PREVIEW` 诊断只解释新预览配置，原恢复动作仍以服务端 marketplace/common 状态为准。不要提供从浏览器填写本机路径、激活政策或重试未知提交的按钮。
