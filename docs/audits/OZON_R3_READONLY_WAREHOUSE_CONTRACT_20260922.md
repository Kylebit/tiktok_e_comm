# Ozon R3 仓库只读决策合同（候选，2026-09-22）

本包提供 `shared_platform/ozon_stock_warehouse_decision.py`，但**未接入正式 R3 预览**，也没有解除预览门禁。隔离测试只使用 fake `/v2/warehouse/list`，没有访问 Ozon 或写入业务数据。

## 输入与结果

调用方必须提供同一 Offer 的原始 R1 冻结快照、固定账号凭据对象和显式 `post_bound`。函数先校验 R1 自身摘要、Offer、顶层与事实内相同的完整 `publication-default-stock/v1` 200 件策略；任一不符时在调用供应商前拒绝。只读回读固定为 `/v2/warehouse/list`，使用该账号的 `client_id` 与 `api_key`，仅接受唯一一个 `active`/`created`、`is_kgt is False`、正整数 ID 的仓库。没有默认 ID，多仓或无仓均阻断。

结果是 `ozon-stock-warehouse-readback/v1` 回执，绑定 Offer、R1 snapshot digest、库存策略 digest、账号 ID、凭据文件 digest、官方响应 digest 和五字段 v4 `ozon_stock_decision`，不含 API key 或原始响应。`validate_warehouse_receipt` 在读取已持久化回执时校验该身份及摘要。**回执自摘要并非签名**；正式接线还必须将其纳入不可变 R3 计划或等效持久批准边界，防止任意方改写回执并重算摘要。

## 正式接线仍阻断

- `modules/products/server.py::_preview_r3_marketplace_stage_configured` 目前不传 `ozon_stock_decision`，所以保持 `OZON_EXACT_STOCK_WAREHOUSE_DECISION_REQUIRED`。正式接线应使用与执行端相同的固定账号和官方绑定运输，先生成/复核回执，再将 `receipt.decision` 投影到 `build_marketplace_stage_payload`，并将完整回执纳入服务端不可变 R3 计划；重复预览和重启必须比较同一 Offer/R1/库存/账号/回读证据。
- `tests/fixtures/b4b_common_multivariant_actual/round1-approved-snapshot.json` 的 2 件策略与 v4 200 件策略语义冲突。不能把它改成 200 来使既有端到端测试通过；真实的旧 2 件快照需要独立后继审核/兼容合同。另一个空策略 fixture 同样不适用。
- 接线前还需端到端证明：预览与恢复用相同回执、账号或 R1 变化阻断、批准后仓库漂移在执行期阻断、全过程无商品/库存写入；旧合成快照的基线失败应单独报告。

隔离验证：`tests/test_ozon_stock_warehouse_decision.py` 10 passed。该通过只证明新的只读合同，不代表正式预览已可用。
