---
name: delist-products-by-sku
description: Safely unpublish existing TikTok Shop, Shopee, and Ozon listings by exact Seller SKU across selected stores. Use when the user asks to 下架, 停售, unlist, deactivate, or archive marketplace products; freezes product and variant identity first, avoids permanent deletion, executes each store independently, and requires provider readback.
---

# 按 Seller SKU 下架商品

把“下架”解释为可恢复的停止销售：TikTok `deactivate`、Shopee `unlist=true`、Ozon `archive`。除非用户另外明确要求，永远不要删除商品。

## 必须遵守的流程

1. 规范化用户给出的 Seller SKU，但在报告里保留原始输入。长 SKU 只可按项目既有对齐规则映射，不能用模糊标题匹配。
2. 展开用户指定的店铺范围。`所有店铺` 是 15 个目标：TikTok LivelyHive PH/MY/TH/VN、HomeBloom PH/MY/TH/VN、MX、GB；Shopee PH/MY/TH/VN；Ozon RU。
3. 运行 `scripts/delist_products_by_sku.py plan`，生成不可变计划。计划必须列出每个目标的店铺身份、商品身份、目标 SKU、商品内全部 SKU、当前状态、证据来源和可执行性。
4. 如果同一商品还包含不在请求中的 SKU，停止该商品，标记 `BLOCKED_MIXED_PRODUCT`。不得通过整卡下架误伤其他 SKU。
5. 对 API 可直连目标，计划阶段执行实时只读回读。仅本地缓存命中不足以授权写入。
6. 对无官方 API 能力但妙手可操作的 TikTok 店铺，使用已登录妙手页面：先按精确店铺和 Seller SKU 找到商品，核对商品内完整 SKU 集合；再以已冻结的妙手产品 ID 定位唯一商品行，并只在该行内定位唯一“下架”按钮。页面存在多个同名按钮时，禁止使用全页序号、`first()` 或坐标猜测。执行后仍在同一产品 ID 行回读按钮已变为“上架”，最后重新筛选/打开商品确认状态。不要读取浏览器 cookie，也不要调用未公开的页面内部接口。
7. 执行脚本必须绑定计划 SHA-256。计划文件变化、SKU 集变化或商品身份变化时，旧授权失效并重新计划。实时状态是回读证据，不得混入或重算原计划摘要；`readback` 只能追加证据并保留原始 `plan_digest`。
8. 各目标独立执行。一个目标失败不阻塞其他目标；网络超时或未知返回不得自动重试写入，先只读回读判定结果。
9. API 接受不等于完成。TikTok 只能在回读到明确停用状态（例如 `SELLER_DEACTIVATED`）时通过，不能把任意未知的非 `ACTIVATE` 状态当成功；Shopee 必须回读 `UNLIST`，Ozon 必须回读 `is_archived=true`。妙手目标必须保存页面状态证据。
10. 妙手点击后若目标产品 ID 行仍显示“下架”，先检查动作是否严格绑定到了该商品行；只允许在重新观察并重新建立精确行绑定后重试一次。再次不变就停止写入，执行一次“同步”只读对账并标记 `RECONCILIATION_REQUIRED`。不得把其他行的状态或全页提示当作目标回读，也不得无限重试未知写入。
11. 最终报告必须保留 `VERIFIED_DELISTED`、`VERIFIED_ALREADY_DELISTED`、`PROVIDER_VERIFIED_DELISTED`、`NOT_FOUND`、`BLOCKED`、`UNKNOWN`、`RECONCILIATION_REQUIRED` 以及外部写入或浏览器动作次数。只有逐目标回读成功才可称为下架成功。

## 命令

```powershell
python skills/delist-products-by-sku/scripts/delist_products_by_sku.py plan --sku 0975 --sku 0976 --scope all
python skills/delist-products-by-sku/scripts/delist_products_by_sku.py execute --plan reports/product-delisting/0975-0976/delist-plan.json
python skills/delist-products-by-sku/scripts/delist_products_by_sku.py readback --plan reports/product-delisting/0975-0976/delist-plan.json
```

默认写入 `reports/product-delisting/<SKU组合>/`。执行前向用户复述精确 SKU 和范围；用户本轮已经明确要求下架时，不需要重复索取一次相同授权。
