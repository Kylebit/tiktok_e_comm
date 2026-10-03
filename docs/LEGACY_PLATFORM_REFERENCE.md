# 历史平台实现参考（非执行规则）

保存日期：2026-09-05；原平台事实形成日期未逐项证实，属于旧实现记录。
来源：C2 基线 `429768b52699cdbe5578d00b06bb0e6b09a35d86` 的 `AGENTS.md`（下方为已保全治理增量中提取的相同历史段）。不把保存日期当成当日业务核验。
这些数据仅用于追溯；未在 R00-A 验证当前有效性。
店铺、价格、SKU 长度、类目和审批入口不能直接用于操作；先核验当前配置、
适用 Skill、冻结目标与用户授权。旧 Web 审批表述不新增重复审批门槛。
本页不授权发布、改价、付费调用或运行任何写入脚本。

## Ozon 上品流程

1. `GET /api/ozon/unmigrated` — 商品目录中未在 Ozon 正式上架的 SKU
2. `GET /api/ozon/draft/{seller_sku}` — **6 位 seller_sku**；DeepSeek 俄语文案 + 类目匹配
3. 草稿页可**手动改 Ozon 类目 / profile / 标题价格**（`web/static/ozon-migrate.js`）
4. `POST /api/ozon/process_images/{seller_sku}` — 3:4 裁剪
5. `POST /api/ozon/migrate` — **4 位 offer_id** 提交 Ozon

类目匹配链：`tk_category_map` → 规则打分 → DeepSeek 窄选；桌布标题特例 type_id=92692。

## 重量

- 不用卖家填的 `package_weight`
- 用 TikTok Fulfillment API 包裹实测重量，四国 MY/PH/TH/VN，近 365 天，**中位数**聚合
- 表 `sku_logistics_weights`；目录 API 字段 `logistics_weight_g`

## 文案（DeepSeek）

- 以 TikTok **原标题**为主，不强制写入样式编号
- Ozon 标题 ≥60 字符；`../ozon/webapp/deepseek_draft.py` + `translate.py`

## 配置要点

- `ozon.data_dir`：指向 `../ozon/webapp/data`
- `ozon.client_id` / `api_key`：优先于 webapp/app.py 内凭据
- `ai.api_key`：DeepSeek

## TikTok MX（妙手 / LivelyHiveMX）

- 店 `shopId=16265910`；货号 = seller_sku **后四位**（如 770005 → 0005）
- POP 定价：`scripts/mx_pop_pricing.py`；妙手只写 **ceil(折前原价)** 到 `price`/`priceIncludeVat`；折扣在 TikTok 后台自设；POP 折后价仅测算/确认用
- 重量用四国物流实测 **中位数**；**包裹尺寸用 TikTok 原链接 `package_dimensions`**（不用物流外箱实测）；手动覆盖见 `KNOWN_BY_MATCH_KEY`
- **已上架 SKU 勿 re-publish 改价**；改价走妙手 save 草稿 + 手动同步
- **每次 publish 前必须用户确认**（对话框展示卡片；通知集成可选，
  不构成审批前置条件或事实来源）
- 确认逻辑：`modules/miaoshou/mx_confirm.py`；继续上架 `--confirm-token TOKEN --user-approved`
- 批量搬运：`scripts/migrate_mx_batch.py`（`--dry-run` 仅测算；默认逐个出卡片后退出等待确认）
- **同链接多 SKU（单 product_id 多规格）**：必须 **一张审批卡 + 一次 publish**，禁止拆成多个链接。索引见 `modules/catalog/tk_sku_groups.py`；派单自动合并见 `modules/miaoshou/migrate_dispatch.py`；MX 整组脚本 `scripts/migrate_mx_group.py`

## TikTok UK（妙手 / GB 4PL 直邮）

- 店 `shopId=10204699`（probe 脚本确认）；货号 = seller_sku **后四位**
- POP 定价：`scripts/uk_pop_pricing.py` + `config/uk_4pl_pricing.json`；默认 **卖家包邮**（全额 4PL）；店铺 **25%** 卖家折扣；目标利润 **17%**；低于 £10 包邮线自动抬价
- Web 审批：`http://127.0.0.1:8765/uk`；模块 `modules/miaoshou/uk_*`
- 兼容派单脚本：`scripts/feishu_uk_dispatch.py` /
  `scripts/orbit_send_uk_approval.py` / `scripts/uk_redispatch_fast.py`
  （保留兼容，不是 Codex 线程治理的必需依赖）
- dry-run：`scripts/orbit_uk_migrate_prep.py`
- 妙手只写 **ceil(折前原价 GBP)**；无西语翻译，保留 PH 母版英文标题
- **每次 publish 前必须 Web 批准**（同 MX 流程）
- **同链接多 SKU**：与 MX 相同，整组审批 + 整组 publish（`group_*.json` 确认单；`publish_uk_multi_listing`）

## 历史来源定位

本次治理基线为 `429768b52699cdbe5578d00b06bb0e6b09a35d86`。完整旧文档可用只读 Git 对象检索 `git show <上述commit>:<原路径>`；新工单与当前源码优先，历史指令不能授权现在执行。
旧 `docs/GIT_SYNC_MX_UK.md` 记载 2026-06 MX/UK 路径、两个仓库 `Kylebit/tiktok_e_comm` 与 `Kylebit/orbit-hive-agent-ops`、旧 C 盘路径和历史分支。它们仅为定位线索，旧自动 pull/push、大范围暂存、指定用户身份的命令不再作为现行操作说明。
原 AGENTS 还记载 8765/8766、Ozon 兄弟目录桥接及页面入口；当前候选以实际服务身份和配置核验，不能由旧描述认定就绪。

<a id="database-history"></a>
## docs/DATABASE_GOVERNANCE.md 历史摘存

时间：2026-07-25（原文标注）。范围：原文所述本机库/目录及指定 SKU，非现在的生产核验。
来源：`429768b52699cdbe5578d00b06bb0e6b09a35d86:docs/DATABASE_GOVERNANCE.md`，Git blob `ec65c2b0b0ebb01185f509d21af8b09789ca9867`，原 UTF-8 文本 SHA-256 `2d0983ba252ea9607c234fa425c465efd00a1373535209a709a0b76c063f2b45`（换行规范化文本）。
下列原文整体是历史引文，含旧“当前”及操作步骤；不得据此删除、恢复、同步或分配 SKU。原始字节仍在 Git 和恢复集内。

<details>
<summary>历史原文（非现行规则）</summary>

> # Database governance
>
> Status date: 2026-07-25
>
> ## Canonical runtime databases
>
> - `data/shop.db` is the commerce catalog and operational SQLite database.
> - `data/orbit_platform.db` stores immutable local report runs and Orbit inbox
>   items.
> - Browser-profile cache databases under `data/auth/` are third-party runtime
>   caches and are not business sources of truth.
>
> The main database currently uses WAL mode. A live copy of `shop.db` alone is
> not a valid backup procedure because committed pages may still be in
> `shop.db-wal`.
>
> ## Safe commands
>
> Full read-only health check:
>
> ```powershell
> .\.venv\Scripts\python.exe scripts\database_maintenance.py check --full
> ```
>
> Read-only catalog quality report:
>
> ```powershell
> .\.venv\Scripts\python.exe scripts\database_maintenance.py quality
> ```
>
> Use `quality --fail-on-review` in a release or publication gate; it returns
> exit code 2 while identity, cost, or derived-data blockers remain.
>
> Create a non-overwriting, integrity-checked online backup:
>
> ```powershell
> .\.venv\Scripts\python.exe scripts\database_maintenance.py backup
> ```
>
> The backup uses SQLite's online backup API, so it includes committed WAL data
> without stopping Orbit. It writes to `backups/database/`, verifies
> `integrity_check=ok`, and returns a SHA-256 digest. It never overwrites an
> existing backup.
>
> ## Current verified baseline
>
> The 2026-07-25 production audit found:
>
> - `quick_check=ok` and `integrity_check=ok`;
> - 16 business tables, 1,091 TikTok product rows, 632 Shopee product rows, 847
>   costs, and 725 logistics-weight rows;
> - no current product-to-shop or cost-to-product orphans;
> - `settlement_lines`, `ad_spend_daily`, and `affiliate_invites` are empty;
> - the schema has no declared foreign keys or triggers and `user_version=0`;
> - schema creation and incremental `ALTER TABLE` calls are still distributed
>   across business modules.
>
> The first verified online backup is recorded locally under
> `backups/database/`. Backup files and databases are ignored by Git.
>
> ## Data-quality gates
>
> P0:
>
> - Five seller-SKU duplicate groups exist inside `UK_IMPORT_GB`: `0003`,
>   `0153`, `0200`, `0619`, and `0926`. Reads or mutations that select one row
>   with `LIMIT 1` are ambiguous until the UK identities are reviewed.
>
> P1:
>
> - 244 product rows have no direct platform-SKU cost. Canonical seller-SKU
>   fallback resolves 146; 98 rows across 28 business keys remain unresolved.
> - Three canonical keys have conflicting positive costs:
>   `0018` (`5`/`5.5`), `0810` (`8`/`8.5`), and `0934` (`10`/`11`).
> - 137 analytics rows no longer match the current active-product snapshot.
>   They must be classified as history or stale derived data before deletion.
> - 97 logistics rows do not match an exact regional seller SKU, but only four
>   fail the approved numeric tail-four alignment. Do not bulk-delete all 97.
> - One Shopee product row has a non-positive price.
>
> P2:
>
> - Add a central, versioned migration ledger before introducing foreign keys or
>   CHECK constraints.
> - Define whether analytics is a current snapshot or retained history.
> - Add explicit reservation/uniqueness governance for new seller SKUs.
> - Remove or quarantine packaged `dist` database copies so runtime state cannot
>   drift from the canonical database.
>
> The Orbit build script now strips a generated `_internal/data` directory only
> after verifying that it is inside the current build bundle, then rejects any
> remaining `shop.db`, `orbit_platform.db`, browser `Cookies`, or `Login Data`
> file. Existing older bundles must still be quarantined or rebuilt once.
>
> ## Restore procedure
>
> 1. Stop the Orbit process that owns port 8765.
> 2. Run `check --full` against the backup file.
> 3. Preserve the current `shop.db`, `shop.db-wal`, and `shop.db-shm` as one
>    recovery set; do not delete them immediately.
> 4. Copy the verified backup to `data/shop.db`.
> 5. Start Orbit and run `check --full` again before any synchronization or
>    channel write.
>
> Do not restore into a running process, and do not copy only a live WAL-mode
> `shop.db`.

</details>

<a id="catalog-history"></a>
## docs/CATALOG_UPDATE_GOVERNANCE.md 历史摘存

时间：原文未标明业务观测日期；仅确认 2026-09-05 保存在此基线。范围：原文所述本机库/目录及指定 SKU，非现在的生产核验。
来源：`429768b52699cdbe5578d00b06bb0e6b09a35d86:docs/CATALOG_UPDATE_GOVERNANCE.md`，Git blob `c847dc0313282bf560bcd3a15bfafa8a32d31bbc`，原 UTF-8 文本 SHA-256 `68153c084db24a9355619dc213ee2af8dca07153c3659999b5c1ed2433cc27e9`（换行规范化文本）。
下列原文整体是历史引文，含旧“当前”及操作步骤；不得据此删除、恢复、同步或分配 SKU。原始字节仍在 Git 和恢复集内。

<details>
<summary>历史原文（非现行规则）</summary>

> # 商品目录大更新治理
>
> ## 当前结论
>
> 现有 `/api/catalog/sync` 会在后台依次刷新 Token、TikTok、物流重量、
> Shopee 和 Ozon。TikTok 全量模式会先逐店清空 `products`，快速模式也会
> 根据本次搜索结果删除缺失商品；TikTok/Shopee 写 `shop.db`，Ozon 则原子
> 替换其独立 `all_products_attrs.json`，因此它们不是同一个一致性快照。
>
> 当前实现存在以下发布前风险：
>
> - 没有先生成完整变更集，也没有在删除前展示或批准删除数量。
> - TikTok、Shopee、Ozon 分店/分平台提交，不是一次跨平台原子事务；中途失败会
>   留下跨平台时间点不一致的目录。
> - TikTok 逐店提交，刷新期间读者可能看到新旧店铺快照混合。
> - TikTok 的单店删除/更新依赖 SQLite 隐式事务，但没有显式
>   `BEGIN`/`rollback`/`finally close`；详情 API 中途失败时连接清理和锁释放
>   依赖对象回收，且总编排会记录错误后继续刷新后续数据源。
> - Shopee 在写入前检查详情集合，使用 `BEGIN IMMEDIATE` 和回滚；但快速模式
>   仅 upsert、不删除远端已缺失的旧行，全量模式才先清店铺。
> - Ozon 对 JSON 使用临时文件 + `os.replace`，并拒绝用空响应覆盖非空旧快照；
>   但 `migrated_offers.json` 是历史并集，不代表当前在售全集。
> - 缓存的 TTL/manifest 只减少 API 调用，不是源快照版本；当前结果没有
>   可重放的输入 checksum。
> - 后台互斥锁仅存在于单个进程，重启或另一进程可并行执行。
> - `products` 只约束 `(sku_id, shop_cipher)`，没有 Seller SKU 预留表或
>   跨工作台唯一性约束。
> - `_next_seller_sku()` 只读取 `products` 最大尾四位，忽略工作台锁和
>   TikTok claim，因而会重复分配仍在流程中的 SKU。
> - 结果只记录进程内状态，缺少持久化 run id、操作者、输入/输出快照、
>   审批人和可回滚备份引用。
>
> ## 0946 的治理含义
>
> 目录未占用 `0946` 不等于可用。历史工作台中 4 个旧 offer 已把 `0946`
> 设为 `fields_locked=true`，并且已验证的 TikTok claim 将两个商品的连续
> 变体编号扩展到 `0947` 和 `0951`。这些 legacy lock 在迁移到正式 reservation
> 表之前必须视为有效预留；多个 offer 重叠预留同一编号必须阻断。
>
> 当前目标 `3828540231` 的状态文件仍是 `seller_sku=""` 且
> `fields_locked=false`；页面展示的 `0946` 是候选值，不是该商品已经持有的
> 独占 reservation。它仍会被上述历史冲突阻断。
>
> 因此分配器应同时读取：
>
> 1. 当前 TikTok/Shopee 商品目录的规范尾四位；
> 2. 已批准的 `product_approval`；
> 3. legacy `review.fields_locked`；
> 4. `*_tiktok_claim.json` 中已 claimed 或 verified 的 `sku_item_nums`。
>
> 以当前事实计算，下一个安全连续编号段从 `0952` 开始，而不是 `0946`
> 或 `0947`。
>
> ## 已提供的安全预览
>
> `domains.product_operations.preview_catalog_update` 是纯函数：
>
> - 对当前/候选快照按 `(sku_id, shop_cipher)` 计算 add/update/remove；
> - 对两个目录快照和 reservation 集分别产生稳定 SHA-256 指纹；
> - 展示所有字段级变化及 remove 候选；
> - 缺少源 revision、空快照、未声明完整快照的删除、重复主键、目录与
>   reservation 重合、跨 offer reservation 重叠都会阻断；
> - 计算避开目录占用与全部 reservation 的下一连续 Seller SKU 段；
> - payload 永远返回 `dry_run=true`、`apply_allowed=false`，模块没有写入 API。
>
> ## 真正执行更新前的门槛
>
> 集成层应另行实现并审核：
>
> 1. 使用 SQLite online backup 记录可恢复备份及 checksum。
> 2. 将所有远程响应先落为不可变 staging snapshot，不在抓取过程中写主表。
> 3. 对 staging 运行本预览、数据库完整性/成本/孤儿审计，并要求人工批准
>    snapshot id 与变更集。
> 4. 通过数据库级租约防止多进程同步；在单一事务中交换 staging 与主目录。
> 5. 持久化 run id、操作者、审批、前后 snapshot id、变更集和备份位置。
> 6. 提交后重跑完整性与业务检查；失败则恢复备份，并保留失败审计记录。
> 7. 建立正式 Seller SKU reservation 表（含 offer、范围、状态、过期/释放、
>    唯一约束），迁移并人工裁决当前重复 legacy reservation。
>
> 在这些门槛完成前，不应让“全量更新”按钮直接调用现有写入同步。

</details>
