# 整套系统 — 新机器部署指南

本文档用于在**另一台电脑**复现 TikTok 控制台 + 商品目录 + Ozon 搬运 + 结算/选品等全部功能。

## 1. 系统组成（两个目录）

```
e-commercial/                    ← 建议统一父目录名
├── tiktok_e_comm/               ← 主仓库（GitHub: Kylebit/tiktok_e_comm）
│   ├── main.py                  ← 唯一入口，Web 8765
│   ├── config/settings.json     ← 本地凭据（勿提交 git）
│   ├── data/shop.db             ← SQLite（勿提交 git）
│   └── modules/ozon/            ← Ozon 集成（草稿/类目/重量/代理）
└── ozon/webapp/                 ← Ozon Flask 子应用（兄弟目录，需单独同步）
    ├── app.py
    ├── translate.py / deepseek_draft.py / img_to_34.py
    ├── templates/
    └── data/                    ← 类目表、映射表、运营 JSON（部分可提交）
```

**关系**：`main.py serve` 启动 HTTP 8765；`/api/ozon/*` 通过 `modules/ozon/webapp_bridge.py` **内嵌加载**兄弟目录 `ozon/webapp/app.py`（无需单独起 Flask 端口）。

默认查找顺序：

1. `config/settings.json` → `ozon.data_dir` 指向的目录（如 `../ozon/webapp/data`）
2. 否则 `tiktok_e_comm/../ozon/webapp/`

---

## 2. 从 GitHub 克隆（代码）

```bash
mkdir -p ~/e-commercial && cd ~/e-commercial

git clone git@github.com:Kylebit/tiktok_e_comm.git
git clone git@github.com:Kylebit/ozon-webapp.git ozon/webapp
```

---

## 3. 新机器初始化

```bash
cd ~/e-commercial/tiktok_e_comm

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements-catalog.txt
# 飞书/Hub 可选：pip install -r requirements-hub.txt

python3 main.py init
```

编辑 `config/settings.json`（从 `config/settings.example.json` 对照）：

| 配置块 | 用途 |
|--------|------|
| `app_key` / `app_secret` | TikTok Shop Open API |
| `ai.*` | DeepSeek 文案（Ozon 标题/描述/类目） |
| `images.photoroom_*` | 主图抠白底 |
| `ozon.client_id` / `ozon.api_key` | Ozon Seller API（推荐填这里，勿写进 git） |
| `ozon.data_dir` | **相对路径** 示例：`../ozon/webapp/data` |
| `feishu.*` | 飞书日报 / 机器人（可选） |
| `shopee.*` | Shopee 发布（可选） |
| `exchange_rates` | 四国汇率 |

**路径示例**（Linux/macOS，按实际目录改）：

```json
"ozon": {
  "client_id": "你的Ozon_CLIENT_ID",
  "api_key": "你的Ozon_API_KEY",
  "data_dir": "../ozon/webapp/data"
},
"feishu": {
  "ozon_data_dir": "../ozon/webapp/data"
}
```

授权 TikTok：

```bash
python3 main.py auth
# 或沿用 tiktok_auth.py
python3 main.py status
```

启动：

```bash
python3 main.py serve --port 8765
```

浏览器：`http://127.0.0.1:8765/`  
主要页面：`/catalog` 商品目录、`/ozon` 上品搬运、`/settlement` 结算。

---

## 4. 从旧机器迁移（凭据 + 数据，不走 GitHub）

以下文件含密钥或业务数据，**用 U 盘 / scp / 加密网盘** 拷贝，不要 push 到 GitHub：

```bash
# 在旧机器执行（示例）
OLD=~/Desktop/e-commercial
NEW=user@newhost:~/e-commercial

# TikTok 凭据与库
scp $OLD/tiktok_e_comm/config/settings.json $NEW/tiktok_e_comm/config/
scp $OLD/tiktok_e_comm/tiktok_tokens.json $NEW/tiktok_e_comm/
scp $OLD/tiktok_e_comm/tiktok_ads_tokens.json $NEW/tiktok_e_comm/ 2>/dev/null || true
scp $OLD/tiktok_e_comm/shopee_tokens.json $NEW/tiktok_e_comm/ 2>/dev/null || true
scp $OLD/tiktok_e_comm/data/shop.db $NEW/tiktok_e_comm/data/

# Ozon webapp 代码 + 运营数据（排除日志）
rsync -av --exclude 'logs/' --exclude '*.log' \
  $OLD/ozon/webapp/ $NEW/ozon/webapp/
```

**Ozon data 里建议保留的**（无密钥）：`category_options.json`、`tk_category_ozon_map.json`、`all_products_attrs.json`  
**含业务状态、可拷可不拷**：`migrated_offers.json`、`migrate_log.json`、价格/促销 JSON

---

## 5. 首次同步建议

```bash
cd ~/e-commercial/tiktok_e_comm
source .venv/bin/activate

python3 main.py products sync          # TikTok 商品
# Web /catalog → 同步 Ozon 快照、物流实测重量（365天×四国）

# 验证 Ozon 桥接
curl -s http://127.0.0.1:8765/api/ozon/unmigrated | head
curl -s http://127.0.0.1:8765/api/ozon/category_options | head
```

---

## 6. Cursor「记忆」怎么带到新电脑

AI 对话**不会**自动跨机器同步。持久化靠仓库内文件：

| 文件 | 作用 |
|------|------|
| `AGENTS.md` | 项目总览、模块、约定（给 Cursor Agent） |
| `.cursor/rules/*.mdc` | 自动注入的规则（部署路径、Ozon 流程等） |
| `docs/DEPLOY.md` | 本文档 |
| `docs/ARCHITECTURE.md` | 架构决策 |
| `config/settings.example.json` | 配置字段说明 |

新机器：`git clone` 后 Cursor 打开 `tiktok_e_comm` 即可读取上述规则。

---

## 7. 常见问题

**`/api/ozon/*` 404 或找不到 webapp**  
→ 检查 `../ozon/webapp/app.py` 是否存在，或 `ozon.data_dir` 是否正确。

**草稿无 `logistics_weight_g`**  
→ 重启 `main.py serve`；在商品目录执行物流重量同步。

**DeepSeek 未调用**  
→ `settings.json` → `ai.api_key`；看草稿页「AI: DeepSeek ✓」。

**TH/VN 重量同步失败**  
→ TikTok API timestamp 过期，可仅 MY/PH 或缩短扫描页数。

**批量搬运不能改类目**  
→ 用「生成草稿」单条编辑类目后再提交；批量仍用自动匹配。

---

## 8. 安全清单（上传 GitHub 前）

- [ ] `config/settings.json` 已在 `.gitignore`
- [ ] `tiktok_tokens.json` / `*.db` 未提交
- [ ] `ozon/webapp/app.py` 内无硬编码 `CLIENT_ID` / `API_KEY`（应走 settings）
- [ ] 不含 `photoroom` / `deepseek` 真实 key 的截图或日志
