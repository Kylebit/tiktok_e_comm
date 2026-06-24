# Shopee Open API — 接入分析

> 更新：2026-06-03 · 凭据已写入本地 `config/settings.json`（不提交 Git）  
> 应用状态：**Developing** · Test Partner_id 已配置

---

## 1. 凭据与环境

| 项 | 说明 |
|----|------|
| **Test Partner_id** | 仅沙盒可用 |
| **Test Partner Key** | HMAC-SHA256 签名密钥 |
| **Live Partner_id/Key** | 应用上线审核通过后单独一套 |
| **沙盒 Host** | `https://partner.test-stable.shopeemobile.com` |
| **生产 Host** | `https://partner.shopeemobile.com` |

本地配置节：`config/settings.json` → `shopee`  
Token 文件：`shopee_tokens.json`（每 `shop_id` 一条，4 国 = 4 次 OAuth）

---

## 2. 鉴权流程（与 TikTok 对比）

```
TikTok Shop                    Shopee Open API v2
─────────────                  ──────────────────
app_key + secret               partner_id + partner_key
一次 OAuth → 多 shop_cipher    每个 shop_id 单独 OAuth
access ~7d / refresh ~365d     access ~4h / refresh ~30d
x-tts-access-token 头          query: partner_id, timestamp, sign, access_token, shop_id
```

**步骤：**

1. `python3 main.py shopee auth-url` → 浏览器打开授权
2. 登录测试店铺（控制台 Test Account 可创建）
3. 回调 URL 截取 `code` + `shop_id`
4. `python3 main.py shopee token --code XXX --shop-id YYY`
5. `python3 main.py shopee status` 查看 token 有效期

签名规则：
- **Public API**（换 token）：`HMAC(partner_id + path + timestamp)`
- **Shop API**（读商品等）：`HMAC(partner_id + path + timestamp + access_token + shop_id)`

---

## 3. 对我们 Hub 有价值的核心 API（P0 → P2）

### P0 — Master Catalog 同步（对齐 TK 四国）

| API | 用途 |
|-----|------|
| `v2.product.get_item_list` | 分页拉全店 SKU |
| `v2.product.get_item_base_info` | 标题、主图、状态、item_id |
| `v2.product.get_model_list` | 变体、model_id、seller SKU |
| `v2.shop.get_shop_info` | 站点 region、shop 名称 |

→ 写入 `data/shop.db` 新表 `shopee_products`，与 TK `products` 按 **seller_sku** 或映射表 join。

### P1 — 运营动作（复用现有队列逻辑）

| API | 对应现有模块 |
|-----|-------------|
| `v2.product.update_price` | `promotions.py` 改价 |
| `v2.product.update_stock` | 库存同步 |
| `v2.discount.*` / `v2.add_on_deal.*` | 促销（比 TK 复杂，需单独封装） |
| `v2.product.unlist_item` | `deactivate.py` 下架 |

### P2 — 分析与日报

| API | 用途 |
|-----|------|
| `v2.product.get_item_extra_info` | 销量、浏览、收藏 |
| `v2.order.get_order_list` | 订单量 → 飞书 digest |
| `v2.account_health.*` | 店铺健康分 |

Shopee **无** TikTok 式 Analytics CTR 分段 API → B 类主图策略需用「浏览/转化」或继续以 TK Analytics 驱动、Shopee 只执行同步。

---

## 4. 四国店铺架构建议

```
Master Catalog (TK MY/VN/TH/PH merge, line=A)
        │
        ├── seller_sku 主键对齐
        │
        ▼
┌───────────────────────────────────────┐
│  shopee_tokens.json                   │
│  shops: {                             │
│    "12345": { region: "MY", ... },    │
│    "67890": { region: "VN", ... },    │
│    ...                                │
│  }                                    │
└───────────────────────────────────────┘
        │
        ▼
modules/shopee/sync.py  →  shopee_products 表
modules/shopee/push.py  →  价格/库存/上下架
```

**SKU 映射：** 参考 Ozon `tk_sku_map.json`，可扩展为 `data/platform_sku_map.json`：

```json
{
  "6601234567890": {
    "tk": { "MY": "..." },
    "shopee": { "MY": { "item_id": 0, "model_id": 0 } }
  }
}
```

---

## 5. 与 Ozon / TikTok 工程分工

| 平台 | 仓库 | 同步方式 | 状态 |
|------|------|----------|------|
| TikTok SEA | `tiktok_e_comm` | Shop Open API | ✅ 生产 |
| Ozon | `ozon/webapp` | Seller API + tk_sku_map | ⚠️ 半自动 |
| **Shopee 4国** | `tiktok_e_comm/modules/shopee/` | Open API v2 | 🟡 凭据+auth 骨架 |
| Temu MY | — | 暂无公开 API | ❌ CSV 过渡 |

Hub 层（`modules/hub/digest.py`）后续增加 Shopee 待办计数，与 Ozon pending 并列。

---

## 6. 明天对接清单

**你需要提供：**

- [ ] 4 国 Shopee 测试店铺是否已在开放平台 Create Test Account
- [ ] 每国 `shop_id`（授权后自动获得）
- [ ] Live 上线时间表（决定是否先只做沙盒联调）
- [ ] seller_sku 是否与 TK 完全一致（LivelyHive 660 前缀？）

**我这边下一步（按优先级）：**

1. `modules/shopee/sync.py` — 拉商品列表入 SQLite
2. `main.py shopee sync` — CLI 一键同步
3. Hub digest 增加 Shopee 商品数 / 待改价
4. 与 TK Master merge 视图（按 seller_sku）

---

## 7. CLI 速查

```bash
python3 main.py shopee status      # token 状态
python3 main.py shopee auth-url    # 生成 OAuth 链接
python3 main.py shopee token --code <code> --shop-id <id>
```

文档：[Shopee Open Platform](https://open.shopee.com/developer-guide/16)
