# B00-B4B 质量分类与第一轮事实归属修正

本段修复真实 compiler 的墙贴规则漏选、第一轮事实借用和未核实标题被升级为事实；不是完整 B4B 或 S02→R3 图片链验收。

- 已知主分类优先。缺少已知主分类时，分别应用墙贴/墙纸卷的维护语义与排除词；未知家居不再默认选择墙纸。两规则冲突返回 `QUALITY_RULE_SELECTION_REQUIRED` 阻断，不能变为 HTTP 调用链中的未处理选择异常。全树真实调用者只有 quality evaluator 和 compiler，两处均处理无匹配/错误。
- R1 材料必须满足 schema、自摘要、offer、批准身份、目标包含关系，并以 bridge 的 `release_handoff` 绑定当前 plan、v4 snapshot digest 与 R1 snapshot digest。compiler 和直接 evidence evaluator 都检查，错误为 `ROUND1_EVIDENCE_IDENTITY_CONFLICT`。loader 拒绝错 offer、错摘要以及损坏但存在的 R1 文件。
- 优先保留现有 v4 的结构化已确认事实。当前 domain 的 `_verified_product_claims` 仅接受 `self_adhesive`，要求完整 user_decision/conversation_approval 事实字段；本段不扩 domain 合同。R1 历史标题补投影只用于精确绑定的明确断言，含否定、疑问、待核、未验证或归因性限定的同句不升级为正向事实。它是三类既有中文词的保守兼容，不声称通用自然语言事实抽取。
- 新的 `release_handoff.round1_snapshot_digest` 必须由独立 R3 桥写入；c50 的 R2 没有 v4 handoff producer。本段的正例使用合成的明确绑定，缺该绑定的旧材料阻断并要求重建可验证交接，不能以同 offer 推断。本段尚未验收实际 S02 产物→R3桥→最终候选。

## 回归证据

审计目录 `domain-prep/03/b4b/`：

- `quality-identity-red.xml`：初始 8 个真实 compiler/选择负例失败；保留一个有效事实正例。
- `quality-loader-direct-red.xml`：损坏 R1 被忽略、direct evaluator 未检查 R1 两项失败。
- `quality-uncertain-title-red.xml`：3 个待核/未验证/疑问事实和歧义错误处理共 4 项失败。
- `quality-existing-compiler-first.xml`：6 个预算/版本测试暴露原 fixture 依赖墙贴 NOT_APPLICABLE 的缺口。未放宽业务规则。
- `quality-consumers-final2.xml`：73 passed，0 failed/0 skipped，包含 19 个质量新用例、两图预算/版本、持久最终批准与去重、Ozon partial 和默认 live 库存。集合有交集，不与较早 67/68/122 等相加。

两图缓存预算 fixture 改为无墙贴语义的通用家居对照，仍走真实 snapshot/编译/预算/上传流程。Ozon fixture 保留真正墙贴分类，补齐七图与维护类目，去掉无事实的营销词，因此默认装配仍受到真正墙贴门禁约束。

测试使用审计目录 Python 禁网 launcher 和最低层 I/O 替身；这不抹去本包较早的 test-stable 出网事故，完整边界见 `TEST_ISOLATION_INCIDENT.md/json`。

## 尚未解决

- 图片 QA 目前仍需后续 R3 桥消费 c50 的 R1/generation/artifacts/qa digest，不能把本段 73 绿说成该图片链完成。
- `publication_quality.py` 的变体价格排序仍把不同 SKU 成本绝对数比较，没有先要求 cost currency 一致；当前正例均 CNY。本段未改外汇或 JSON 业务政策，混合成本货币缺少可比依据，应在后续明确币种合同/不可比状态。
- 既有 durable sidecar Loader 的其它文件缺失/格式错误与路径保护属于后续桥链核对；本段只声明上述 R1 已验证边界。
