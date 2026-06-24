# TikTok Shop 控制台

LivelyHive 东南亚跨境店本地运营工具：商品目录、TikTok/Ozon/Shopee 联动、Listing 优化、结算利润、Ozon 上品搬运。

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-catalog.txt

python3 main.py init
# 编辑 config/settings.json（可从 settings.example.json 复制）
python3 main.py auth
python3 main.py serve --port 8765
```

浏览器：`http://127.0.0.1:8765/`

## 另一台电脑部署

1. **代码**：`git clone git@github.com:Kylebit/tiktok_e_comm.git`
2. **Ozon 子应用**：同级目录放置 `ozon/webapp/`（见 [docs/DEPLOY.md](docs/DEPLOY.md)）
3. **凭据/数据库**：用 U 盘或 `scripts/bundle_secrets.sh` 从旧机器拷贝，**勿提交 GitHub**
4. **Cursor 记忆**：仓库内 `AGENTS.md` + `.cursor/rules/` 会在新机器自动生效

完整步骤：[docs/DEPLOY.md](docs/DEPLOY.md)

## 主要功能

| 页面 / 命令 | 说明 |
|-------------|------|
| `/catalog` | 商品目录（TikTok + Ozon 快照、物流实测重量） |
| `/ozon` | TikTok → Ozon 上品（草稿可改类目/文案后再提交） |
| `/settlement` | 结算与利润 |
| `/titles` | Analytics A 类 → AI 标题 → 推送 |
| `/images` | 主图抠白底（Photoroom） |
| `/promotions` | 促销调价 |
| `/analytics` | 28 天 CTR 分段 |
| `/deactivate` | 零销下架 |
| `/sourcing` | 1688 选品 |

```bash
python3 main.py products sync
python3 main.py serve
```

## 配置

- `config/settings.json` — API Key、DeepSeek、Ozon、飞书（**勿提交 git**）
- `tiktok_tokens.json` — Shop OAuth（**勿提交 git**）
- 模板：`config/settings.example.json`

## 架构

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [AGENTS.md](AGENTS.md) — AI / Cursor 项目说明

## 安全

仓库已忽略 Token、数据库、settings。克隆后需 `init` + `auth`，并从旧机器安全拷贝 `data/shop.db` 与 token 文件。
