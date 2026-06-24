# 多平台跨境运营 — 工程地图 & Multi-Agent 分工

> 更新：2026-06-02 · 供多个 Cursor Agent 并行拆解任务  
> 基准商品线：**TikTok 东南亚跨境四国 merge**（VN/PH/MY/TH）· LivelyHive  
> **已有 API：** TikTok Shop Open API、Ozon Seller API、**Shopee Open API（Developing，Test 凭据已收）** · Temu 暂无

---

## 1. 店铺与产品线（业务地图）

### 产品线 A — 跟 TK 东南亚同款（低客单 / 现有库）

| 平台 | 站点/店铺 | 工程覆盖 | 同步方式（现状 → 目标） |
|------|-----------|----------|-------------------------|
| **TikTok 跨境** | VN, PH, MY, TH | ✅ `tiktok_e_comm` | API 已同步 → **母版** |
| **TikTok 跨境 第一组** | + UK, JP, MX | ❌ 未接 | 从 A 母版复制 + 本地化 |
| **TikTok 本土** | MY, TH | ❌ 未接（可能另一 OAuth） | 母版 + 本土价/库存规则 |
| **TikTok 跨境 第二组** | — | — | **不属于 A 线** |
| **Shopee 跨境** | VN, PH, MY, TH | 🟡 `modules/shopee/` auth 骨架 | API 联调 → Master merge |
| **Temu** | MY | ❌ 无 API | 阶段 3：CSV 过渡 → API |
| **Ozon 跨境** | RU | ⚠️ 独立工程 | 见 `ozon/webapp` |

### 产品线 B — 高客单（独立选品）

| 平台 | 站点 | 工程覆盖 |
|------|------|----------|
| **TikTok 跨境 第二组** | VN, PH, MY, TH | ❌ 需独立 `catalog_line=B` + 第二套 Token |

### 商品源优先级

```
TikTok 东南亚跨境 (MY/VN/TH/PH)  ──►  Master Catalog (line=A)
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
              Shopee 4国                  Temu MY                   Ozon CB
              TK 第一组 7国               (A线)                     (已有 webapp)
              TK 本土 MY/TH

Master Catalog (line=B)  ──►  TikTok 第二组 4国 only
```

---

## 2. 代码仓库地图（现在有什么）

### Repo A：`tiktok_e_comm`（本仓库）

**定位：** TikTok Shop **深度运营控制台** — 同步、Analytics 分段、Listing/促销/下架、成本。

| 路径 | 职责 |
|------|------|
| `core/` | OAuth、Shop API 签名、SQLite、LLM |
| `modules/products/` | 商品同步 + 7 条运营队列（见下） |
| `modules/finance/` | 利润公式 ✅ / 结算同步 stub |
| `modules/ads/` | GMV Max stub |
| `modules/affiliate/` | 达人 CSV ✅ / 建联 API stub |
| `web/` + `server.py` | 本地 Web `:8765` |
| `data/shop.db` | 商品、Analytics、各队列表 |
| `data/keywords/` | 站点热搜词 CSV |
| `data/sourcing/` | 选品 CSV 样例（**代码未建**） |
| `CURSOR/` | 历史利润脚本（只读参考） |

**已跑通站点：** MY, VN, TH, PH（同一 Shop Open API 授权下多 cipher）

### Repo B：`ozon/webapp`

**路径：** `/Users/wangyin/Desktop/e-commercial/ozon/webapp`  
**定位：** Ozon **人工上品 + 改价 + 促销 + 分析**（Flask `:5055`）

| 路径 | 职责 |
|------|------|
| `app.py` | 全部 API + Ozon Seller API 调用 |
| `deepseek_draft.py` | 俄语标题/属性 AI 草稿 |
| `translate.py` | 关键词 fallback + **单一类目模板** |
| `img_to_34.py` | TikTok 图 → 3:4 → freeimage.host |
| `data/tk_sku_map.json` | **外部维护** — TK→Ozon 待搬运 SKU（96 条） |
| `data/migrated_offers.json` | 已上品 offer_id |
| `data/all_products_attrs.json` | Ozon 商品快照（需手动刷新） |

**与 TikTok 工程关系：** 无 API 直连；靠 `tk_sku_map.json` 手工/脚本从 TK 导出。Seller SKU 前缀 `660` 与 MY 区一致。

---

## 3. TikTok 工程 — 模块与队列（Agent 边界）

### 3.1 已实现 ✅

| 模块 | 文件 | 输入 | 输出 |
|------|------|------|------|
| 商品同步 | `sync.py` | Shop API | `products`, `shops` |
| 成本 | `costs.py` | Web/CSV | `sku_costs` |
| 商家 SKU | `seller_sku.py` | 规则+API | CSV/xlsx/push |
| Analytics | `analytics.py` | 28d API | `product_analytics` A/B/C/D |
| Listing | `titles.py`, `title_ai.py` | A类/低动销 | `title_queue` → push |
| 主图 | `images.py`, `image_ai.py` | B类/手动/探索 | `image_queue` → 手动上传 |
| 促销 | `promotions.py` | Analytics/动销 | `promo_queue` → push |
| 下架 | `deactivate.py` | D类规则 | `deactivate_queue` → push |
| 热搜词 | `keyword_intel.py`, `keyword_build.py` | CSV/商品库 | `data/keywords/*.csv` |

### 3.2 Stub / 搁置 ⏸

| 模块 | 状态 |
|------|------|
| `finance/service.py` | 结算同步未迁入（用 `tiktok_settlement.py` 临时代替） |
| `ads/service.py` | 广告消耗未接 |
| `affiliate/service.py` | Target Collaboration 未接 |
| `sourcing` | 配置占位；1688 CSV 导入未建 |
| **主图 9 槽位 / 1688 新品** | **待办 — 需深入讨论后做** |

### 3.3 Analytics 分段 → 运营动作（已锁定）

| 分段 | 条件（28d） | 动作 |
|------|-------------|------|
| A | 高 CTR · 0 单 | Listing AI + 促销 |
| B | 低 CTR · 有库存 | 主图优化（探索模式待办） |
| C | 中间带 | 观察 |
| D | 低 CTR · 0 单 | 下架候选 |

---

## 4. 目标架构（尚未建 — 多 Agent 共建）

### 4.1 建议新增：Master Catalog 层

**不在任一现有 repo 完整实现** — 建议在本仓库新增 `modules/catalog/` 或独立 `e-commercial/catalog/` 包。

```
master_skus          # 你的货号、采购价、catalog_line (A|B)、1688 链接
master_images        # 主图 URL 列表
channel_listings     # platform + region + shop_group + platform_listing_id
category_mappings    # TK 类目 → Shopee/Ozon/Temu 类目
sync_jobs            # 待同步/失败重试
```

**母版来源（待定）：** 默认 `TikTok SEA cross-border · MY` 或四国 merge。

### 4.2 连接器（Connector）模式

每个平台一个 connector，**只写自己的 API**，读写 Master：

| Connector | Repo 建议 | 依赖 Master |
|-----------|-----------|-------------|
| `tiktok` | `tiktok_e_comm`（已有） | 扩展 push 为多店/多组 |
| `ozon` | 迁入或调用 `ozon/webapp` 逻辑 | 替代手工 `tk_sku_map.json` |
| `shopee` | 新 module | 新 |
| `temu` | 新 module（可能 CSV 先行） | 新 |

### 4.3 Ozon 工程演进建议

| 现状 | 目标 |
|------|------|
| `tk_sku_map.json` 手维护 | 从 `tiktok_e_comm` export API 自动生成 |
| 单一类目 `17027906` | `category_mappings` 按 TK 类目分支 |
| 凭证 hardcode | 迁入 `config.json` / env |
| 无 TikTok API | 只读 Master，不直连 TK |

---

## 5. Multi-Agent 任务包（可并行）

依赖图：

```
Layer 0（必须先有）
  [A0] core 稳定 — auth, api_client, db 迁移工具

Layer 1（可并行，依赖 A0）
  [A1] TikTok 多店/多 Token  — shop_group, 第二组 OAuth
  [A2] Master Catalog 表结构 + export API
  [A3] Finance 结算迁入
  [A4] Ads GMV Max

Layer 2（依赖 A2）
  [B1] TK 母版 → 第一组 UK/JP/MX 复制草稿
  [B2] TK → Ozon export 替换 tk_sku_map
  [B3] Shopee connector（CSV MVP → API）
  [B4] Temu MY export（CSV/API 调研）
  [B5] Catalog line B + TK 第二组

Layer 3（依赖 B*）
  [C1] 统一 Web 入口或 dashboard 链接各服务
  [C2] 跨平台 SKU 利润看板
  [C3] 库存/超卖规则（若共享仓）
```

### Agent 包明细

#### Agent-TK-CORE（Layer 0–1，本仓库）

- **范围：** `core/`, `modules/products/sync.py`, `server.py` 店铺列表
- **任务：**
  - `shops` 表增加 `shop_group`（`sea_cb` | `group1` | `group2` | `local_my` | `local_th`）
  - 多 `token_file` 配置（settings.json）
  - 商品同步按 group 过滤
- **不改：** titles/promotions 业务规则
- **验收：** 同一 DB 能区分两组 TikTok 店

#### Agent-TK-OPS（Layer 1，可并行）

- **范围：** `analytics`, `titles`, `promotions`, `deactivate`, `web/*`
- **任务：** 按 `shop_group` 筛选；A 类 workflow 只对 A 线店
- **依赖：** Agent-TK-CORE 的 shop_group 字段

#### Agent-TK-IMAGES（搁置 ⏸）

- **范围：** `images.py`, `image_ai.py`, `web/images.html`
- **状态：** 探索 recipe 已写代码；**产品决策未完成 — 默认不继续**

#### Agent-MASTER（Layer 1，新建）

- **范围：** 新建 `modules/catalog/` + migration
- **任务：**
  - 从现有 `products` 表 export → `master_skus` + `channel_listings`
  - CLI: `python3 main.py catalog export --line A --region MY`
  - CLI: `python3 main.py catalog import-csv`
- **依赖：** A0
- **并行：** 与 Agent-TK-CORE 可并行（只读 products）

#### Agent-OZON（Layer 2）

- **范围：** `/Users/wangyin/Desktop/e-commercial/ozon/webapp`
- **任务：**
  - 读 Master export JSON 替代 `tk_sku_map.json`
  - 类目 mapping 配置化
  - 凭证出代码
  - 刷新 `all_products_attrs` 的 CLI
- **依赖：** Agent-MASTER export API
- **并行：** 与 Agent-SHOPEE 并行

#### Agent-SHOPEE（Layer 2，新建）

- **范围：** 新 `modules/shopee/` 或新 repo
- **任务：** Open API 调研 → MVP：Master CSV → Shopee 批量上架模板
- **依赖：** Agent-MASTER
- **并行：** Agent-OZON, Agent-TEMU

#### Agent-TEMU（Layer 2，新建）

- **范围：** 新 `modules/temu/`
- **任务：** MY 店 A 线 — API 或 CSV 导出；与 Master 对齐 seller_sku
- **依赖：** Agent-MASTER
- **并行：** Agent-SHOPEE

#### Agent-FINANCE（Layer 1）

- **范围：** `finance/`, 迁入 `tiktok_settlement.py`
- **任务：** settlement → `settlement_lines` → 对接 `profit_engine`
- **依赖：** A0
- **并行：** Agent-MASTER

#### Agent-SOURCING（ backlog）

- 1688 CSV、新品 pipeline — **等 Master + 图片策略定稿**

---

## 6. 并行 vs 串行（给调度用）

| 可立即并行 | 必须串行 |
|------------|----------|
| Agent-MASTER + Agent-TK-CORE + Agent-FINANCE | MASTER export → OZON/SHOPEE/TEMU |
| Agent-TK-OPS（用 mock shop_group） | TK-CORE shop_group → TK-OPS 联调 |
| Agent-OZON + Agent-SHOPEE + Agent-TEMU | Catalog line B → TK 第二组 |
| Ozon 改价/促销页（现有 webapp） | 统一 Dashboard（最后） |

---

## 7. 配置与环境（跨 Agent 约定）

| 文件 | 仓库 | 内容 |
|------|------|------|
| `config/settings.json` | tiktok_e_comm | TK API、AI、促销参数 |
| `tiktok_tokens.json` | tiktok_e_comm | Shop OAuth（可扩展多文件） |
| `data/shop.db` | tiktok_e_comm | 运行时 DB |
| `ozon/webapp/data/config.json` | ozon | DeepSeek key |
| `ozon/webapp/data/tk_sku_map.json` | ozon | **待废弃 → Master export** |

**命名约定：**

- `catalog_line`: `A` | `B`
- `shop_group`: `sea_cb` | `tk_g1` | `tk_g2` | `tk_local` | `shopee_cb` | `temu_my` | `ozon_cb`
- `seller_sku`: 继续用现有编码规则（如 MY `660xxx`）

---

## 8. 建议执行顺序（2–4 周一轮）

1. **Week 1：** Agent-TK-CORE（多 Token/shop_group）+ Agent-MASTER（表+export）
2. **Week 2：** Agent-OZON 接 Master export；Agent-TK-OPS 按 group 过滤
3. **Week 3：** Agent-SHOPEE + Agent-TEMU MVP（CSV 也行）
4. **Week 4：** TK 第一组 UK/JP/MX 试点；Agent-FINANCE 结算

---

## 9. 给新 Agent 的启动 Prompt 模板

```
你负责 [Agent-XXX]。
仓库：/Users/wangyin/Desktop/e-commercial/tiktok_e_comm（或 ozon/webapp）
先读 docs/MULTI_AGENT_ROADMAP.md §5 对应包。
禁止修改：[其他 agent 拥有的模块]
依赖：[列出必须先完成的包]
验收标准：[从上文复制]
```

---

## 10. 已确认决策

- [x] **母版：** 四国 merge（VN/PH/MY/TH，按 seller_sku / global_product_id 去重）
- [x] **已有 API：** TikTok Shop、Ozon Seller
- [ ] Shopee / Temu API — 未获取，阶段 3 用 CSV 过渡
- [ ] A 线同步范围：全 SKU vs 动销 — 待定
- [ ] TK 第二组 / UK·JP·MX / 本土店 — 待定
- [ ] 主图/1688 新品 — **搁置**

---

## 11. 软件基础架构 & 产品形态

### 定位：私人「妙手 ERP」Lite

围绕 **一人管多店** 的核心链，不是复刻妙手全部功能：

```
选品调研 → 商品信息生成 → 多站推送 → 日常采集 → 分析建议 → 成本利润 → 库存
                ↑                                              ↑
           Agent 可自动                                    日报 + 待你确认
```

### Web vs App

| 方案 | 建议 |
|------|------|
| 本地 Web 控制台（现状） | **保留** — 办公室深度操作、批量审核 |
| 响应式 Web / PWA | P1 — 手机浏览器打开收件箱 |
| **Telegram / 飞书 Bot** | **P1 最优先** — 日报推送、确认/跳过 |
| Native App | P3+ — 维护成本高，暂不 |

**结论：** 一个后端 + Web 工作台 + **Bot 当私人助理**，不做两套 UI。

### Control Plane（Hub 层 — 各 Agent 统一写入）

| 表/模块 | 作用 |
|---------|------|
| `activity_log` | 每条自动操作（同步、扫描、推送、失败） |
| `approval_inbox` | 需你确认：Listing/促销/下架/Ozon改价/上品 |
| `daily_digest` | 按日汇总：数据变化 + 已完成 + 待办 |
| `job_scheduler` | 定时 sync → analytics → scan → digest → push bot |

现有 `title_queue` / `promo_queue` 等 **pending 状态 = approval_inbox 的来源**。

### 日报模板

1. 昨日数据：TK 四国 + Ozon 订单/曝光/CTR 变化  
2. 软件自动完成：同步 N SKU、扫描 M 条建议…  
3. **待你确认：** K 条（带 Web 链接）  
4. 异常：Token 过期、API 失败、零库存仍在售  
5. Agent 三句话摘要（可选）

### 产品阶段

| 阶段 | 交付 |
|------|------|
| P0 | ✅ TK Web + Ozon webapp |
| **P1** | Master 四国 merge + Hub + **日报页 + Telegram** |
| P2 | Ozon 接 Master；结算→利润；TK shop_group |
| P3 | Shopee/Temu **CSV**（无 API） |
| P4 | 选品 → 生成信息 → 推 TK+Ozon |
| P5 | 库存、全平台利润、自动调研 |

### 新 Agent 包

- **Agent-HUB** — activity_log, approval_inbox, digest, Web 收件箱  
- **Agent-BOT** — Telegram 推送 + 确认回调  
- **Agent-SCHEDULER** — cron 编排  
- **Agent-MASTER** — 四国 merge（P1 与 HUB 并行）

---

## 9. 给新 Agent 的启动 Prompt 模板
