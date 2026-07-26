# 商品目录大更新治理

## 当前结论

现有 `/api/catalog/sync` 会在后台依次刷新 Token、TikTok、物流重量、
Shopee 和 Ozon。TikTok 全量模式会先逐店清空 `products`，快速模式也会
根据本次搜索结果删除缺失商品；TikTok/Shopee 写 `shop.db`，Ozon 则原子
替换其独立 `all_products_attrs.json`，因此它们不是同一个一致性快照。

当前实现存在以下发布前风险：

- 没有先生成完整变更集，也没有在删除前展示或批准删除数量。
- TikTok、Shopee、Ozon 分店/分平台提交，不是一次跨平台原子事务；中途失败会
  留下跨平台时间点不一致的目录。
- TikTok 逐店提交，刷新期间读者可能看到新旧店铺快照混合。
- TikTok 的单店删除/更新依赖 SQLite 隐式事务，但没有显式
  `BEGIN`/`rollback`/`finally close`；详情 API 中途失败时连接清理和锁释放
  依赖对象回收，且总编排会记录错误后继续刷新后续数据源。
- Shopee 在写入前检查详情集合，使用 `BEGIN IMMEDIATE` 和回滚；但快速模式
  仅 upsert、不删除远端已缺失的旧行，全量模式才先清店铺。
- Ozon 对 JSON 使用临时文件 + `os.replace`，并拒绝用空响应覆盖非空旧快照；
  但 `migrated_offers.json` 是历史并集，不代表当前在售全集。
- 缓存的 TTL/manifest 只减少 API 调用，不是源快照版本；当前结果没有
  可重放的输入 checksum。
- 后台互斥锁仅存在于单个进程，重启或另一进程可并行执行。
- `products` 只约束 `(sku_id, shop_cipher)`，没有 Seller SKU 预留表或
  跨工作台唯一性约束。
- `_next_seller_sku()` 只读取 `products` 最大尾四位，忽略工作台锁和
  TikTok claim，因而会重复分配仍在流程中的 SKU。
- 结果只记录进程内状态，缺少持久化 run id、操作者、输入/输出快照、
  审批人和可回滚备份引用。

## 0946 的治理含义

目录未占用 `0946` 不等于可用。历史工作台中 4 个旧 offer 已把 `0946`
设为 `fields_locked=true`，并且已验证的 TikTok claim 将两个商品的连续
变体编号扩展到 `0947` 和 `0951`。这些 legacy lock 在迁移到正式 reservation
表之前必须视为有效预留；多个 offer 重叠预留同一编号必须阻断。

当前目标 `3828540231` 的状态文件仍是 `seller_sku=""` 且
`fields_locked=false`；页面展示的 `0946` 是候选值，不是该商品已经持有的
独占 reservation。它仍会被上述历史冲突阻断。

因此分配器应同时读取：

1. 当前 TikTok/Shopee 商品目录的规范尾四位；
2. 已批准的 `product_approval`；
3. legacy `review.fields_locked`；
4. `*_tiktok_claim.json` 中已 claimed 或 verified 的 `sku_item_nums`。

以当前事实计算，下一个安全连续编号段从 `0952` 开始，而不是 `0946`
或 `0947`。

## 已提供的安全预览

`domains.product_operations.preview_catalog_update` 是纯函数：

- 对当前/候选快照按 `(sku_id, shop_cipher)` 计算 add/update/remove；
- 对两个目录快照和 reservation 集分别产生稳定 SHA-256 指纹；
- 展示所有字段级变化及 remove 候选；
- 缺少源 revision、空快照、未声明完整快照的删除、重复主键、目录与
  reservation 重合、跨 offer reservation 重叠都会阻断；
- 计算避开目录占用与全部 reservation 的下一连续 Seller SKU 段；
- payload 永远返回 `dry_run=true`、`apply_allowed=false`，模块没有写入 API。

## 真正执行更新前的门槛

集成层应另行实现并审核：

1. 使用 SQLite online backup 记录可恢复备份及 checksum。
2. 将所有远程响应先落为不可变 staging snapshot，不在抓取过程中写主表。
3. 对 staging 运行本预览、数据库完整性/成本/孤儿审计，并要求人工批准
   snapshot id 与变更集。
4. 通过数据库级租约防止多进程同步；在单一事务中交换 staging 与主目录。
5. 持久化 run id、操作者、审批、前后 snapshot id、变更集和备份位置。
6. 提交后重跑完整性与业务检查；失败则恢复备份，并保留失败审计记录。
7. 建立正式 Seller SKU reservation 表（含 offer、范围、状态、过期/释放、
   唯一约束），迁移并人工裁决当前重复 legacy reservation。

在这些门槛完成前，不应让“全量更新”按钮直接调用现有写入同步。
