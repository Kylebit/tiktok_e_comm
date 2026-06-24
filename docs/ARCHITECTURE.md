# TikTok Shop 控制台 — 架构说明

## 已锁定决策

| 项 | 决定 |
|----|------|
| 入口 | **CLI 优先**（`main.py` 交互菜单 + 子命令） |
| 广告成本 | **TikTok Marketing API 真实消耗**（GMV Max report），不再用「小计×20%」 |
| 联盟 | **必须支持定向邀请**，达人来自 CSV 列表，批量 Target Collaboration |

## 目录

```
main.py                 # 唯一入口
config/settings.json    # 凭据、汇率、广告账户（勿提交 git）
core/                   # API、授权、SQLite
modules/
  products/             # 商品同步 + SKU 成本
  finance/              # 结算 + profit_engine
  ads/                  # Marketing API 消耗
  affiliate/            # 定向建联
data/shop.db            # 本地库
data/creator_lists/     # 达人 CSV
exports/                # 导出
```

## 利润公式（finance/profit_engine.py）

```
利润(当地) = 结算金额 - 商品成本/汇率 - 广告消耗(当地,来自Ads API分摊) - 本土发货费/汇率

利润(¥)   = 结算×汇率 - 商品成本¥ - 广告消耗×汇率 - 本土发货费¥

本土发货: Seller shipping fee = 0 且 SST = 0（与 CURSOR 一致）
广告分摊: 按同一店铺、同一自然日，用「卖家折扣后小计」比例分到各订单行
```

## 两套 API 授权

| 用途 | 平台 | 配置文件 |
|------|------|----------|
| 商品/订单/结算/联盟 | Shop Open API | `tiktok_tokens.json` |
| GMV Max 广告报表 | TikTok Marketing API | `tiktok_ads_tokens.json` + `ads.advertiser_id` |

Marketing API 关键接口（待实现 `modules/ads/service.py`）：

- `GET /open_api/v1.3/gmv_max/report/get/` — spend、orders、gross_revenue
- 文档: https://business-api.tiktok.com/portal/docs

## 联盟定向建联流程（待实现）

1. 用户维护 `data/creator_lists/{name}.csv`（列 `creator_id`, `username`）
2. CLI: `python3 main.py affiliate invite --products PID1,PID2 --creators my_list --commission 18`
3. 后端：Affiliate API 创建 Target Collaboration → 批量邀请列表内达人
4. 结果写入 `affiliate_invites` 表

需在 Partner Center 申请 **Affiliate API** Scope。

## 实施里程碑

### M1 ✅ 当前（脚手架）
- CLI 入口、配置、DB 表结构、profit_engine、模块 stub

### M2 商品 + 结算
- Product API 同步 → `products` / `sku_costs`
- Finance API 结算 → `settlement_lines`
- 利润 CLI 输出

### M3 广告
- Marketing API OAuth + 日消耗 → `ad_spend_daily`
- 与 profit_engine 联调分摊

### M4 联盟
- Target Collaboration + 达人 CSV 批量邀请
- 联盟订单回查

## 常用命令

```bash
python3 main.py init
python3 main.py auth
python3 main.py status
python3 main.py sync yesterday

python3 main.py products sync
python3 main.py products cost set 1732379861313488827 12.5 --note "660003"

python3 main.py finance sync --date 2026-06-01
python3 main.py finance profit --days 7

python3 main.py ads sync
python3 main.py ads report --days 7

python3 main.py affiliate lists
python3 main.py affiliate invite --products 1732379861313423291 --creators my_creators --commission 20
```

## 与旧脚本

| 旧 | 新 |
|----|-----|
| tiktok_auth.py | main.py auth |
| tiktok_settlement.py | modules/finance（M2 迁入） |
| tiktok_data.py | modules/products + orders |
| CURSOR/ | 仅参考 profit 规则，不再扩展 |
