# TikTok Shop 控制台

LivelyHive 东南亚跨境店本地运营工具：商品同步、Listing 优化、促销、Analytics 分段、零销下架。

## 快速开始

```bash
python3 main.py init
# 编辑 config/settings.json（可从 settings.example.json 复制字段）
python3 main.py auth
python3 main.py products sync
python3 main.py serve
```

浏览器打开 `http://127.0.0.1:8765/`。

## 主要功能

| 页面 / 命令 | 说明 |
|-------------|------|
| `/titles` | Analytics A 类 → AI 标题+详情 → 确认推送 |
| `/promotions` | 加深折扣 / 加入促销 / 秒杀 / 优惠券建议 |
| `/analytics` | 28 天 CTR 分段（A/B/C/D） |
| `/deactivate` | 90 天 0 单 + 低 CTR → 批量下架 |
| `/costs` | SKU 采购成本维护 |

```bash
python3 main.py products analytics-sync
python3 main.py products listing-scan --limit 20
python3 main.py products promo-scan --mode analytics --scope add
python3 main.py products deactivate-scan
```

## 配置

- `config/settings.json` — API Key、AI、促销参数（**勿提交 git**）
- `tiktok_tokens.json` — Shop OAuth Token（**勿提交 git**）
- 模板见 `config/settings.example.json`

## 架构

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 安全提示

仓库已忽略 Token、数据库、导出文件。克隆到新机器后需重新 `init` + `auth`，并复制本地配置。
