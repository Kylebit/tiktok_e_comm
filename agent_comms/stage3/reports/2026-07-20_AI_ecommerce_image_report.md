# Orbit 商品流程图片能力梳理 + AI 电商图片集成建议

> 探索任务 `task-534bac5cb2`｜供 Boss 阅读｜基于仓库 `Kylebit/tiktok_e_comm` 实码调研 + 公开资料  
> 日期：2026-07-20｜**本报告不改代码、不 commit/push**

---

## 一、现有功能清单（以代码为准）

系统里图片相关能力主要落在两条服务链：

| 服务 | 入口 | 端口 |
|------|------|------|
| Orbit Hive 控制台 | `modules/products/server.py` | **8765** |
| Orbit Treasury 新品发布台 | `modules/sourcing/new_product_server.py` + `web/new_product.html` | **8766** |

### 1.1 新品第一波审核（Treasury / 8766）

**页面：** `web/new_product.html`

| 能力 | 效果 | 说明 |
|------|------|------|
| 图片代理加载 | 1688/海外图可在页内显示 | `imgSrc()` → `GET /api/proxy-image`；alicdn 用 `detail.1688.com` Referer；磁盘缓存 `data/new_product_image_cache/` |
| 图片审核网格 | 逐张决定去留与处理意图 | `image_actions[]`：`review` / `keep` / `translate` / `redraw` / `remove` + 备注 |
| 加载进度 / 失败重试 | 统计成功失败、批量重拉 | `retryImage` / `retryFailedImages` |
| 海外同款候选图 | 从 Temu 等抓图作候选 | `overseas_image_candidates`；默认可标 `redraw`（去平台水印意图） |
| 提升到审核区 | 一键并入审核列表 | `promoteOverseasImage()` |
| 「AI 补图需求」 | **只记需求，不调付费 API** | `POST /api/new-product/image-request` → `pending_api_approval` |
| 妙手草稿图片门槛 | 卡住未完成的翻译/重绘 | `prepare_miaoshou_draft()`：至少 3 张 `keep`；`translate`/`redraw` 无 `output_url` 会阻塞 |

**缺口（代码已预留、未执行）：** `translate` / `redraw` 无后端执行器；无本地上传/裁剪 UI。

### 1.2 1688 选图工作台（Hive / 8765）

**页面：** `web/sourcing.html`｜核心：`modules/sourcing/image_workbench.py`

| 能力 | 效果 |
|------|------|
| 下载 1688 原图 | 落到 `data/sourcing/{offer_id}/raw/` |
| 批量去水印 | Photoroom `textRemoval`（`batch_dewatermark`） |
| 单张配方生成 | Photoroom 配方（白底/场景等）→ `generated/` |
| TK 主图 ≤9、详情图 ≤20 编排 | 增删排序、HTML 详情预览 |
| 发布到 TK / 导出 ZIP | `tk_publish` 上传接口 |
| 静态资源服务 | `GET /api/sourcing/asset` |

### 1.3 在售主图优化（Hive / 8765）

**页面：** `web/images.html`｜`modules/products/images.py` + `image_ai.py`

| 能力 | 效果 |
|------|------|
| B 类扫描 / 手动选品 / 探索模式 | 批量或单品生成变体 |
| 约 15 个 Photoroom 配方 | 白底主图、去水印、场景、角度、细节等 |
| 按 TK 槽位下载 / ZIP | `exports/main_images/`、`exports/image_zips/` |
| 标记完成/跳过 | SQLite `image_queue` |

### 1.4 Photoroom 配方橱窗

**页面：** `web/photoroom_showcase.html`  
可对单 offer 跑全配方对比（会消耗 API 次数），结果在 `data/sourcing/{offer_id}/photoroom_showcase/`。

### 1.5 其它图片相关（非主上品编辑）

| 位置 | 能力 |
|------|------|
| `web/catalog.html` | 缩略图经 `/api/proxy-image` |
| `detail_text_cards.py` | Pillow 合成尺寸/文案卡（非 Photoroom） |
| `pipeline.py` | CLI：下载 → Photoroom hero → 9 槽草稿 |
| Ozon `img_to_34.py` | **3:4 裁剪**（Ozon 专用，未接到 Treasury UI） |
| Shopee / TK upload 客户端 | 各平台媒体上传 API |
| `toapis_client.py` | 外部图像模型客户端**已写，未接 UI** |
| `settings` 里 `gpt-image` | **配置占位，无调用代码** |
| LinkFox 以图搜 1688 | 可选付费 intel，默认不执行 |

### 1.6 已接入的 AI 图像服务（真实调用）

| 服务 | 配置 | 用途 |
|------|------|------|
| **Photoroom Image API** | `images.photoroom_api_key` / `PHOTOROOM_API_KEY` | 抠图白底、AI 背景、去字去水印、阴影/打光、Edit-with-AI、放大等 |

### 1.7 能力对照（有 / 无）

| 常见能力 | 现状 |
|----------|------|
| 代理加载防盗链图 | ✅ 有（8765/8766） |
| 图片审核决策流 | ✅ 有（Treasury） |
| 批量去水印 | ✅ 有（Photoroom） |
| 白底/场景主图生成 | ✅ 有（Photoroom 配方） |
| 批处理队列 | ✅ 有（images 页 + workbench） |
| 缩略图展示 | ✅ 有（catalog） |
| AI 文生图/补图执行 | ❌ 仅记需求 |
| 在线裁剪 UI | ❌（仅 Ozon 脚本） |
| 虚拟模特 / 鬼衣 / Flat Lay | ❌ UI 写了展望，未接 API |
| 图片合规自动检测 | ❌ 无 |
| 翻译文案进图 / 重绘执行 | ❌ 动作有，执行无 |

---

## 二、AI 电商图片趋势调研（公开资料摘要）

### 2.1 主流做法（2025–2026）

行业已从「抠图工具」演进到 **视觉生产线 API**：

1. **抠图 + 白底 + 合规主图**（平台硬门槛，仍是最高 ROI）  
2. **AI 场景背景 / Studio 级背景**（Photoroom Studio / Studio HD 等，强调真实感）  
3. **去水印 / 去中文贴纸**（跨境货源刚需）  
4. **服装向**：鬼衣（Ghost Mannequin）、虚拟模特、熨烫去皱、Flat Lay（Photoroom 等已产品化）  
5. **批量**：一次对整批 catalog 套同一 Brand Kit / 配方（上百张级）  
6. **图生图 / Edit-with-AI**：改角度、细节、陈列，而不是从零文生图  
7. **合规**：TikTok Shop 等对 **显著 AI 合成内容要求披露**；禁止用 AI **歪曲真实商品外观**；主图仍强调白底、无水印、足够张数与分辨率

参考（公开页）：

- Photoroom API / Studio HD / H1 2026 产品更新（背景、批量、Virtual Model、Ironing 等）  
- TikTok Shop Seller University《AI-Generated Content Restrictions and Requirements》  
- 第三方整理的 TK 主图规格（常见要求：约 800×800+、1:1、主图白底、无水印；listing 质量与图片数量相关）

### 2.2 和「纯文生图」的关系

对 **1688 货源跨境铺货**，业界更稳的路径是：

**真图（货源图）→ 清洗（去水印/抠图）→ 规范化（白底/比例）→ 少量场景变体**  

而不是直接用 Midjourney/文生图「造一个不像货」的主图（易违规、易客诉）。

---

## 三、结合本店现状的集成建议（优先级与落地）

**现状画像：** TikTok 跨境为主，兼 Shopee / Ozon；货源以 1688 为主；仓库里 **Photoroom 已深接**，Treasury 审核流已有 `translate`/`redraw` 语义但未执行；ToAPIs/gpt-image 未接线。

### P0 — 立刻值得做（1–2 周级，复用现有）

| 建议 | 理由 | 落地思路 |
|------|------|----------|
| **打通 Treasury 的 `redraw` / 去水印执行** | 审核页已选动作，却卡在妙手草稿 | `redraw` → 调现有 `image_ai` / workbench 的 Photoroom `prep_dewatermark` 或白底配方，写回 `output_url` |
| **打通「补图需求」到 Photoroom 配方（人工点确认）** | 现有 `image_generation_requests` 已是队列雏形 | 审核通过后一键跑白底/场景，禁止静默扣费；结果进 `image_actions` |
| **主图合规预检（规则，非重模型）** | TK 拒图成本高 | 本地检查：尺寸、近似白底、是否含大块中文/水印（可先用现成去字 API + 简单阈值） |
| **统一代理缓存策略** | 8765/8766 两套 cache | 文档化 + 失败占位已有；监控 cache 体积即可 |

### P1 — 中期（增强转化，仍偏 Photoroom）

| 建议 | 理由 | 落地思路 |
|------|------|----------|
| **场景图 / Studio 背景批量** | 详情与广告素材 | 启用更高质量 background model（如 Studio 系列 header），按类目 prompt 模板（家居/浴室等已有 `SCENE_PROMPT_TEMPLATES`） |
| **Ozon 3:4 接到共用裁剪工具** | 已有 `img_to_34.py` | 抽成通用服务，Treasury/Shopee/Ozon 出口前调用 |
| **Brand Kit 一致性** | 多店多站点 | 固定 padding、阴影、背景 prompt、Logo 禁区规则 |
| **AI 披露标记字段** | TK AIGC 政策 | 凡场景/虚拟模特生成图，在内部标记 `aigc=true`，发布清单提示是否需披露 |

### P2 — 选择性（品类相关再上）

| 建议 | 适用 | 注意 |
|------|------|------|
| Virtual Model / Ghost Mannequin / Flat Lay | 服装、软装 | Photoroom API 已支持相关参数；家居小件优先级低于白底 |
| 文生图主图（gpt-image / ToAPIs） | 营销海报、非主图 | **不要**替代真实商品主图；可接 ToAPIs 做「尺寸说明图/英文卡」类补图 |
| 以图搜款（LinkFox） | 选品 | 保持 opt-in 付费，与上品图编辑分开 |
| 深度合规多模态检测（品牌/违规） | 规模化后 | 可后接第三方；先做规则+人工抽检 |

### 不建议优先

- 用 DeepSeek **做图像**：DeepSeek 强项在文本/推理，图像生成应继续走 Photoroom / 专用图像 API。  
- EchoTik：按现有仓库定位偏数据/运营情报，不适合当主图像引擎。  
- 全量自动文生图上架：合规与货不对板风险高。

### 建议落地顺序（一句话路线图）

```text
P0: Treasury 审核动作真正跑通 Photoroom（去水印/白底） 
 → P0: 合规预检 + 人工确认扣费 
 → P1: 场景变体批量 + 多平台出口裁剪 
 → P2: 服装向虚拟模特 / 文生补图（非主图）
```

### 成本与风控提醒

- Photoroom 按次计费：Showcase「跑全配方」很贵，生产默认应用 **白底 + 去水印** 最小集。  
- 所有生成图保留 `source_url` → `output_url` 溯源，便于客诉与审核。  
- AI 场景图遵守各站点 AIGC 披露要求；主图尽量「真商品 + 清洗」，少用虚构模特。

---

## 附录：关键代码索引

| 主题 | 路径 |
|------|------|
| Treasury 审核 UI | `web/new_product.html` |
| 代理图 | `modules/sourcing/new_product_server.py`、`modules/products/server.py` |
| 审核状态机 | `modules/sourcing/new_product_workbench.py` |
| Photoroom 引擎 | `modules/products/image_ai.py` |
| 选图工作台 | `modules/sourcing/image_workbench.py`、`web/sourcing.html` |
| 在售优图 | `modules/products/images.py`、`web/images.html` |
| 未接线图像客户端 | `modules/sourcing/toapis_client.py` |

---

*报告结束。如需下一步，建议先开一个实现任务：把 Treasury `redraw`/`translate` 接到现有 Photoroom 配方并写回 `output_url`。*
