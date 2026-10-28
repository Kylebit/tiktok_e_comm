# B00-B2B 共享商品合同接入

本包从精确 `26290f878559621c4ae78d1112ab05eee8b8dd92` 开始，仅接入 B2A 合同与修复已复现的预检缺口，不代表 B00 整体完成或生产可发布。

## 差量与边界

| 工作项 | 实现位置 | 最终行为 |
| --- | --- | --- |
| S1 | `release_store._sku_key` | 保留恰好六位 `99xxxx` 治理命名空间，其他历史 SKU 继续使用尾四位键。旧碰撞 fixture 改为 `770946`；新用例证明治理键和同尾历史键可共存。 |
| S2/S3 | `ReleaseStore.source_sku_lineage_context` / `build_release_dashboard` | 读取全部 ACTIVE 来源占用，通过新增 `active_reservation_claims` 传入真实预检 caller。`existing_reservations` 仍只装前驱形状，兼容旧 parser。新来源及继承/复用分支检查其他来源占用，同来源重放和无关键不误阻断。 |
| P1 | `_candidate_payload` | 在 v4 digest 构建前深拷贝已批准仓配字段，显式畸形值不能被静默丢弃。 |
| P2 | 原 `shopee_global_master` 投影 | v1/v2 已整体深拷贝，本包不改算法；新增真实 Store 批准/重开测试验证所有字段一致。 |
| P3 | `build_approved_tiktok_publish_snapshot` 与仓配 helper | 对已有精确 collect-box 目标传递 `expected_warehouse_inventory`，校验目标、店铺、批准来源/时间、仓库唯一性、严格整数库存与总量。缺少 draft identity 仍留在 unavailable_targets；旧快照完全缺少仓配字段仍兼容。 |
| H1 | `_release_plan_payload_from_dashboard` | 将 dashboard 中仓配深拷贝进候选 product_facts，使 B2A 仓配 bridge 能核对两端事实。没有修改 HTTP handler。 |

Store schema、唯一索引、原批准/预留事务、冻结快照构建与读取逻辑保持不变。此前问题是预检可能返回 READY，而原 Store 最终写入已经会抛出跨来源冲突并回滚；不能描述为已允许双重占用。

## 验证

新增 `tests/test_b2b_shared_contracts.py` 使用合成产品、精确来源合同、临时 SQLite 与原实际 server/dashboard 函数。覆盖跨来源 NEW_SOURCE/INHERITED owner、同来源重放与继承、99xxxx、仓配深拷贝、server→bridge→projection→v4→Store/reopen、v1/v2、仓配篡改和目标 consumer。dashboard 用例仅在两处 FX 配置边界使用固定测试汇率，SKU caller、Store 和合同函数均是真实实现。

初始红测 22 失败 / 1 通过；修改后同组通过。直接相关回归包括 `test_release_store.py`（含并发来源占用）、`test_approved_publication_snapshot_integration.py`（含原子回滚与篡改拒绝）、snapshot inputs、snapshot、Shopee global master。精确最终数量和机器/JUnit 证据见私有 `architecture/B00_B2B_RECEIPT.md`。

渠道回归额外探测得到 7 个 handler 409、1 个通过；在精确父提交 `26290f8` 上复现同样结果，错误均为 `approved publication snapshot is unavailable`，而独立快照 builder 对同一 fixture 成功。本包保留该基线缺口，没有把它隐藏为通过或越界改渠道行为。

所有运行使用测试 fixture；未调用真实商品平台、付费服务、生产配置/数据库、业务常驻服务或完整 UI。HTTP 对照仅使用原测试创建的本地随机端口与合成 ledger。实际仓配事实生产者/UI、渠道执行器对新字段的最终消费、内容与平台的其他缺口仍需后续工作包。01 的两个 B2A 严格性修正由单独 writer 提交，按协调者指定的精确提交接入后再做跨层复验。
