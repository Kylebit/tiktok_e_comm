# TikTok Shop vs Shopee Open API — 能力对比

> LivelyHive 跨境 · MY/VN/TH/PH 四国  
> 更新：2026-06-03

---

## 1. 鉴权与架构

| 维度 | TikTok Shop Open API | Shopee Open API v2 |
|------|---------------------|-------------------|
| 凭据 | app_key + app_secret | partner_id + partner_key |
| 授权 | 一次 OAuth → 多 shop_cipher | 主账号 OAuth → 多 shop_id（我们已过滤 4 主店） |
| Token 寿命 | access ~7d，refresh ~长期 | access ~4h，refresh ~30d |
| 签名 | HMAC(app_key+path+params+body) | HMAC(partner_id+path+ts[+token+shop_id]) |
| 请求风格 | REST JSON，header `x-tts-access-token` | REST JSON，query 带 sign/access_token/shop_id |
| 店铺标识 | shop_cipher | shop_id（整数） |

---

## 2. 商品 / Listing

| 能力 | TikTok | Shopee | 本仓库 |
|------|--------|--------|--------|
| 拉全店商品列表 | ✅ `products/search` | ✅ `get_item_list` | TK ✅ · SP ✅ `shopee sync` |
| 商品详情 | ✅ 单接口含 SKU | ✅ `get_item_base_info` + 有变体时 `get_model_list` | TK ✅ · SP ✅ |
| 商家 SKU | seller_sku（660xxx） | model_sku（常带规格后缀） | 对比用 660 前缀 |
| 改标题/描述 | ✅ update product | ✅ update_item | TK ✅ Listing 队列 · SP ❌ 未建 |
| 改价 | ✅ | ✅ `update_price` | TK ✅ 促销队列 · SP ❌ 未建 |
| 改库存 | ✅ | ✅ `update_stock` | TK 部分 · SP ❌ 未建 |
| 上下架 | ✅ deactivate | ✅ `unlist_item` | TK ✅ · SP ❌ 未建 |
| 主图/多图 | ✅ 上传+绑定 | ✅ `upload_image` + update | TK 主图队列 · SP ❌ |
| Analytics CTR 分段 | ✅ 28d 商品分析 | ❌ 无同等 CTR API | **仅 TK 驱动 A/B/C/D** |
| 全球商品 / 全球价 | ✅ global_product_association | ⚠️ CB/SIP 价字段（sip_item_price） | TK 母版 · SP 读价 |

**Shopee 注意：** 有变体（`has_model=true`）时必须调 `get_model_list` 才能拿到价格和库存。

---

## 3. 订单 / 财务

| 能力 | TikTok | Shopee |
|------|--------|--------|
| 订单列表 | ✅ | ✅ `get_order_list` |
| 订单详情 | ✅ | ✅ `get_order_detail` |
| 结算/对账 | ✅ Statement API | ✅ `get_escrow_detail` 等 |
| 利润报表 | 本仓库 CURSOR 脚本 + 待迁入 finance | 需单独接 |

---

## 4. 营销 / 促销

| 能力 | TikTok | Shopee |
|------|--------|--------|
| 店铺促销/折扣 | ✅ Flash / 优惠券等 | ✅ discount / bundle / add_on_deal（API 较碎） |
| 广告 GMV Max | Marketing API（stub） | Shopee Ads 另一套 |
| 达人/联盟 | Target Collaboration（stub） | Affiliate 另一套 |

**结论：** 促销两边都能做，Shopee 促销 API 比 TK 更分散，需要单独封装。

---

## 5. 在本项目里「Shopee 上能做什么」（建议优先级）

### 已做 ✅
- Live 授权（主账号 + 四国主店过滤）
- 商品 sync → `shopee_products` 表
- TK vs Shopee SKU 对比 → `shopee compare`

### P1 — 与 Master Catalog 对齐
- 按 region + 660xxx 映射 TK ↔ Shopee item_id/model_id
- Hub 日报增加 Shopee 商品数、仅 TK / 仅 SP 清单

### P2 — 运营动作（复用 TK 队列思路）
- 改价 / 改库存（跟 TK 促销队列联动）
- 下架 unlist（跟 TK deactivate 规则联动）

### P3 — 暂无 API 或 ROI 低
- Shopee 侧 Analytics 分段（无 CTR → 继续以 TK Analytics 决策）
- 主图 AI 批量上传（可先做「TK 生成 → 人工上 Shopee」）
- 广告 / 联盟自动化

---

## 6. CLI 速查

```bash
python3 main.py shopee status    # 授权与四国主店
python3 main.py shopee sync      # 同步商品
python3 main.py shopee compare   # 与 TK SKU 对比
python3 main.py products sync    # TikTok 同步
```

---

## 7. 数据表

| 表 | 平台 | 主键 |
|----|------|------|
| `products` | TikTok | sku_id + shop_cipher |
| `shopee_products` | Shopee | model_id + shop_id |

对比逻辑见 `modules/shopee/compare.py`（提取 seller_sku / model_sku 中的 `660xxx` 前缀）。
