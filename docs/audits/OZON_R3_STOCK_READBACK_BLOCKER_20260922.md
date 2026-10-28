# Ozon R3 终审预览库存门禁核查（2026-09-22）

状态：**BLOCKED**。本次只核查源码并运行隔离测试，没有调用官方 Ozon 接口，没有商品或库存写入，没有部署。

## 已复现

在候选仓库运行 `tests/test_operations_publication_common.py`：6 通过、2 失败。两个失败均在已核验 COMMON 后构建 R3 终审预览时出现：`modules/products/server.py::_preview_r3_marketplace_stage_configured` 调用 `build_marketplace_stage_payload` 未提供 `ozon_stock_decision`，桥接层报 `OZON_EXACT_STOCK_WAREHOUSE_DECISION_REQUIRED`。因此工作台不能把该最终包标为就绪。

更深的合同冲突是：测试所用已签名 R1 快照 `tests/fixtures/b4b_common_multivariant_actual/round1-approved-snapshot.json` 的 `publication_stock_policy` 为 `{ "stock_per_model_sku": 2 }`；R3 桥接层直接把它放入 `product_facts.stock_policy`。当前 v4 快照校验 `domains/product_operations/approved_publication_snapshot.py::_publication_stock_policy` 只接受 `publication-default-stock/v1`、`quantity_per_sku: 200`、`scope: EACH_SELECTED_SKU` 等完整字段；其 Ozon 决策还必须含同一策略的 digest。现有 R1 测试快照不能被静默改写为 200，也不能凭空生成符合 v4 的决策。此结论针对该隔离测试快照；没有据此推断线上 R1 快照库存值。

## 可复用的只读来源与缺口

- `shared_platform/product_publication_live_dependencies.py::LiveOzonDependencies._eligible_warehouse_id` 已通过官方 `/v2/warehouse/list` 读取，并只接受唯一 `active`/`created` 且 `is_kgt is False` 的正整数仓库 ID。它在执行期使用，目前未接入 R3 终审预览，也没有将账号和回读证据冻结到 R3 计划。
- `shared_platform/ozon_runtime_credentials.py::required_pinned_ozon_credentials` 提供账号 ID 与凭据文件 digest；预览必须绑定此账号执行只读回读。不能把其他账号的仓库 ID 套进当前 Offer。
- v4 决策合同见 `domains/product_operations/approved_publication_snapshot.py::_ozon_stock_decision`：`ozon-stock-warehouse-decision/v1`、官方回读来源、唯一仓库策略、正整数 ID、与已核 R1 库存策略一致的 digest。没有仓库、多个合格仓库、无账号绑定或策略冲突时保持阻断。

## 下一包的验证边界

1. 先核实当前 R1 生产者实际输出和现存批准快照：确立合法的 v4 库存策略来源；旧 R1 的 `stock_per_model_sku: 2` 若代表业务批准量，需明确迁移或重新审核规则，不能由预览层擅自替换为 200。
2. 在固定账号下做官方仓库列表**只读**回读，生成唯一仓库决策；将 Offer、R1 快照 digest、库存策略 digest、账号 ID、凭据 digest 与供应商响应 digest 绑定到可持久的 R3 回执和计划。恢复或重复预览必须验证相同身份，变更则阻断。
3. 使用隔离 fake provider 覆盖唯一/零个/多个合格仓库、无效 ID、错误账号、R1 策略变化、重启后持久回执、审批前漂移，证明无库存/商品写入后，再解除本门禁。

本次尝试的临时接线已撤回；生产路径仍保持原有 fail-closed 错误。`reports/product-delisting/0227/` 未触碰。
