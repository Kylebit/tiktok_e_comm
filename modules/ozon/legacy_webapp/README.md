# Ozon Webapp

TikTok MY → Ozon 上品、改价、促销、Rich 内容。由 [`tiktok_e_comm`](https://github.com/Kylebit/tiktok_e_comm) 通过 `webapp_bridge` 内嵌加载，一般无需单独 `flask run`。

## 目录布局

与主仓库并列放置：

```
e-commercial/
├── tiktok_e_comm/
└── ozon/webapp/    ← 本仓库
```

## 新机器

```bash
cd ~/e-commercial
git clone git@github.com:Kylebit/ozon-webapp.git ozon/webapp

cd tiktok_e_comm
cp ../ozon/webapp/data/config.example.json ../ozon/webapp/data/config.json
cp ../ozon/webapp/data/credentials.example.json ../ozon/webapp/data/credentials.local.json
# 填入 DeepSeek / Ozon 凭据，或在 tiktok_e_comm/config/settings.json 配置 ozon.* 与 ai.api_key
```

`config/settings.json` 示例：

```json
"ozon": {
  "client_id": "...",
  "api_key": "...",
  "data_dir": "../ozon/webapp/data"
},
"ai": { "api_key": "..." }
```

## 可提交的数据

- `data/category_options.json` — Ozon 类目列表
- `data/tk_category_ozon_map.json` — TK→Ozon 映射学习表
- `data/all_products_attrs.json` — 已有商品属性快照

## 依赖

- Python 3 + Flask（主控制台环境即可）
- `curl`（Ozon / DeepSeek API 调用）
- 兄弟目录 `tiktok_e_comm` 需在 Python path 中（bridge 自动处理）

完整部署见 [tiktok_e_comm/docs/DEPLOY.md](https://github.com/Kylebit/tiktok_e_comm/blob/main/docs/DEPLOY.md)。
