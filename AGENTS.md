# Agent 指南 — TikTok + Ozon 电商控制台

供 Cursor / 其他 AI 在新会话中快速理解本项目。

## 仓库与目录

- **本仓库**：`tiktok_e_comm`（主控制台，端口 8765）
- **兄弟目录**：`../ozon/webapp/`（Flask Ozon 逻辑，由 `webapp_bridge` 内嵌加载，不单独起端口）
- **配置**：`config/settings.json`（勿提交）；模板 `config/settings.example.json`
- **数据库**：`data/shop.db`（商品目录、SKU 成本、物流重量等）

## 入口

```bash
python3 main.py serve --port 8765
```

Web 页面：`/`、`/catalog`、`/ozon`、`/settlement`、`/titles`、`/images`、`/sourcing`

## 核心模块

| 路径 | 职责 |
|------|------|
| `modules/products/server.py` | HTTP 服务；`/api/ozon/*` 代理与 catalog 草稿 |
| `modules/catalog/` | 商品目录同步、SKU 编辑、物流实测重量 |
| `modules/ozon/` | Ozon 草稿、类目匹配、搬运属性、webapp 桥接 |
| `modules/finance/` | 结算拉取、利润 |
| `modules/shopee/` | Shopee 发布（可选） |
| `modules/sourcing/` | 1688 选品（可选） |
| `../ozon/webapp/` | Ozon API：migrate、改价、促销、图片 3:4 |

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

## 部署

见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 代码原则

- 最小 diff；匹配现有命名与模块边界
- 勿提交 token、settings.json、*.db
- Ozon 集成优先走 `modules/ozon/`，避免在 webapp 写死本机绝对路径

## TikTok MX（妙手 / LivelyHiveMX）

- 店 `shopId=16265910`；货号 = seller_sku **后四位**（如 770005 → 0005）
- POP 定价：`scripts/mx_pop_pricing.py`；妙手只写 **ceil(折前原价)** 到 `price`/`priceIncludeVat`；折扣在 TikTok 后台自设；POP 折后价仅测算/确认用
- 重量用四国物流实测 **中位数**；**包裹尺寸用 TikTok 原链接 `package_dimensions`**（不用物流外箱实测）；手动覆盖见 `KNOWN_BY_MATCH_KEY`
- **已上架 SKU 勿 re-publish 改价**；改价走妙手 save 草稿 + 手动同步
- **每次 publish 前必须用户确认**（对话框展示卡片；飞书 webhook 打通后同步推送）
- 确认逻辑：`modules/miaoshou/mx_confirm.py`；继续上架 `--confirm-token TOKEN --user-approved`
- 批量搬运：`scripts/migrate_mx_batch.py`（`--dry-run` 仅测算；默认逐个出卡片后退出等待确认）
- **同链接多 SKU（单 product_id 多规格）**：必须 **一张审批卡 + 一次 publish**，禁止拆成多个链接。索引见 `modules/catalog/tk_sku_groups.py`；派单自动合并见 `modules/miaoshou/migrate_dispatch.py`；MX 整组脚本 `scripts/migrate_mx_group.py`

## TikTok UK（妙手 / GB 4PL 直邮）

- 店 `shopId=10204699`（probe 脚本确认）；货号 = seller_sku **后四位**
- POP 定价：`scripts/uk_pop_pricing.py` + `config/uk_4pl_pricing.json`；默认 **卖家包邮**（全额 4PL）；店铺 **25%** 卖家折扣；目标利润 **17%**；低于 £10 包邮线自动抬价
- Web 审批：`http://127.0.0.1:8765/uk`；模块 `modules/miaoshou/uk_*`
- 派单入队：`scripts/feishu_uk_dispatch.py` / `scripts/orbit_send_uk_approval.py` / `scripts/uk_redispatch_fast.py`
- dry-run：`scripts/orbit_uk_migrate_prep.py`
- 妙手只写 **ceil(折前原价 GBP)**；无西语翻译，保留 PH 母版英文标题
- **每次 publish 前必须 Web 批准**（同 MX 流程）
- **同链接多 SKU**：与 MX 相同，整组审批 + 整组 publish（`group_*.json` 确认单；`publish_uk_multi_listing`）

## Git 同步（MX / UK → GitHub）

- 业务代码仓库：`Kylebit/tiktok_e_comm`，分支 **`master`**
- 详细步骤（Codex / Cursor 通用）：[docs/GIT_SYNC_MX_UK.md](docs/GIT_SYNC_MX_UK.md)
- 协作规则与飞书广播模板：`../orbit-hive-agent-ops/README_FOR_AGENTS.md`（兄弟目录）或 GitHub `Kylebit/orbit-hive-agent-ops`
- **勿提交**：token、`config/settings.json`、`config/*.local.json`、`*.db`（除文档允许的 `data/weight_overrides.json`）
