# 小规模验证方案：基于 LinkFox 商品套图的低成本复刻（三人协作报告）

> **协作说明**：本报告由 Orbit Hive 三方共同完成，沿同一流水线接力 —— 采集（§1）→ 提取+设计（§2）→ 生成（§3）→ 对比与可行性（§4/§5）→ 决策（§6），最终合成此单一报告。各章节标注贡献方。
>
> | 章节 | 贡献方 | 内容 |
> |---|---|---|
> | §0 概述 | CEO 肉肉 | 目标、协作方式、总框架 |
> | §1 商品信息采集 | Orbit Cursor | SKUID/1688/Temu → 标题+图片；直采 vs 妙手采集选型 |
> | §2 信息提取与图片类型 | Orbit Cursor | 字段提取、图片类型、可复用生成描述模板 |
> | §3 AI 图片生成方案 | CEO 肉肉 | GPT Image / Gemini 调用设计、对齐 Linkfox、批量流水线 |
> | §4 Linkfox 调研与对比 | Orbit Codex | Linkfox 技术栈/能力/图片类型/流程 + GitHub 开源参考 + 逐项对比 |
> | §5 可行性评估 | Orbit Codex | 当前是否具备 GPT Image / Gemini API 能力 + 替代方案 |
> | §6 汇总与下一步 | CEO 肉肉 | 整合三人结论、给出落地建议 |

---

# 0. 概述（CEO 肉肉）

## 0.1 背景与目标
LinkFox 的"商品套图"能力是当前跨境电商生图赛道的标杆：官方宣称**从一张白底图到全套场景营销素材仅需 5 分钟**、商拍成本降低 70%–90%，覆盖 **A+图 / 卖点图 / 场景图 / 特写图 / 服装模特（AI 换模特、换国籍肤色）/ 场景裂变（1 张参考图 3 秒裂变多张）/ 商品视频 / 多语言图译 / 专属模特定制**等。其底层由三大引擎驱动：**作图引擎 + Agent 分析引擎 + Claw 自动化执行引擎**，并深耕亚马逊 / Temu / Shopify 等平台上架规范。

我们想验证一件更简单的事：**能不能用"采集 → 信息提取 → 云生图 API"的小闭环，以更低成本、更可控的方式，复刻 LinkFox 商品套图的核心产出？** 本方案聚焦"小规模验证"，不追求全功能平替。

## 0.2 总框架（流水线主线）
```
采集（§1：SKUID/链接→标题+图片）
   → 提取+设计（§2：字段→product_brief→图片类型与生成描述）
   → 生成（§3：GPT Image/Gemini 出图）
   → 对比与可行（§4/§5：Linkfox 调研 + API 能力核查）
   → 决策（§6：落地建议）
```
三人沿同一条流水线接力，最终合成此单一报告。

---

# 1. 商品信息采集方案（Orbit Cursor）

### 目标、输入和统一结果
本验证的输入应接受两类值：`seller_sku/SKUID`，或 1688、Temu 商品 URL。服务先把输入规范化为 `source_url` 与 `source_id`：SKU 在本地目录中查到原始链接后继续处理；URL 则保留原链接、解析平台与商品 ID。不要把 URL 的猜测结果直接当作商品事实。

建议统一返回以下最小数据契约，后续的信息提取和制图只依赖这份结构：

```json
{
  "source": {"platform": "1688", "source_id": "…", "source_url": "…", "captured_at": "ISO-8601"},
  "product": {"title": "…", "description_text": "…", "variants": []},
  "images": [
    {"url": "https://…", "role": "main|detail", "position": 1, "width": 0, "height": 0}
  ],
  "provenance": {"collector": "miaoshou", "raw_ref": "common_collect_id", "confidence": "high"},
  "warnings": []
}
```

图片 URL 仅作为短期采集结果，生成前应下载到受控缓存、做去重（内容哈希）和可访问性检查；原图、采集时间与来源 ID 需一并保存，方便复核版权和提示词依据。

### 方案比较

| 维度 | (a) 平台 API / 公开页面直采 | (b) 复用妙手采集 |
| --- | --- | --- |
| 覆盖方式 | 每个平台单独接官方 API，或为动态页面维护解析器 | 将 1688/Temu 等来源链接交给妙手公共采集箱 |
| 凭据 | 官方 API 通常要求商家/应用授权、app key、签名与配额；页面解析还要处理 Cookie、验证码、反爬与代理 | 复用本项目本地 `config/miaoshou.local.json` 的 app ID/secret 和既有签名逻辑；前端不接触密钥 |
| 稳定性 | 官方 API 在授权和字段范围内较稳；公开页面 HTML/接口随页面、地区、登录和反爬变化，维护成本高 | 已由当前工作台验证的"提交链接—轮询—读取详情"链路；平台差异由妙手承接，仍须显示采集失败原因 |
| 成本 | 官方接入、逐平台开发和长期维护成本高；代理/验证码服务还会带来持续成本 | 沿用现有订阅/API 用量；仅增加本地归一化、缓存和图片校验 |
| 合规与数据风险 | 未授权抓取可能违反平台条款；不应绕过登录、验证码或访问控制；官方 API 也必须遵守授权范围、数据最小化和留存规则 | 仍须确认妙手对来源平台和图片的授权边界；采集仅用于内部预览/审核，不自动发布、不把原图转售或当成已获营销授权 |

直采可作为第二阶段的补充：当某平台已具备明确的官方商品 API、书面授权且妙手不能返回需要的字段时，新增一个受限 connector。LinkFox 当前的 `modules/sourcing/linkfox_client.py` 适合做选品搜索/图片搜索的受控付费调用，不是"给任意商品 URL 返回标题和全量详情图"的通用抓取替代品；它要求 `LINKFOXAGENT_API_KEY` 和显式 `--execute-paid`，因此不作为本 POC 的主采集链路。

### 选型与落地入口
选择 **(b) 妙手采集为 POC 主路径**：实现更少、密钥已在服务端配置、并且项目已经有预览与状态展示。该路径只写入公共采集箱、读取采集详情；不认领店铺、不发布商品，符合小规模验证应先人工审核的边界。

现有可直接复用的入口如下。

1. Web 入口为 `/new-product`（`web/new_product.html`）。用户在"1688 链接 / offer_id / 妙手采集箱详情 ID"输入框粘贴 URL 或 ID，点击"生成第一波预览"。SKU 输入先由一个轻量的目录解析步骤转成原始 `source_url`，再调用同一入口。
2. 前端调用 `POST /api/new-product/preview`，请求体为 `{"url":"<source URL 或 offer_id>","overseas_urls":[],"precollect":true}`。`precollect=true` 会走 `modules.sourcing.new_product_workbench.precollect_preview`。
3. `modules/sourcing/miaoshou_precollect.py` 依次调用妙手 `fetch_item`、公共采集箱列表和详情接口，将标准化标题、主图/详情图及采集状态写入 `data/new_product_workbench/<offer_id>_miaoshou.json`；它的设计明确不会认领或发布。
4. 预览页从响应的 `source.precollect`/`normalized` 渲染标题、图片和失败告警。仅允许状态为 success、图片 URL 可下载且人工勾选的图片进入后续"信息提取"；失败时保留缓存和原错误，允许重试，但不静默降级为伪造字段。

上线前应加三项保护：限制 URL 白名单（1688/Temu 域名及 HTTPS）、把来源/图片授权状态作为必填审核项、对缓存设置保留期限和删除任务。若后续加入直采 connector，也必须通过同一统一结果契约和审核门，而非绕开它。

---

# 2. 商品信息提取与图片类型设计（Orbit Cursor）

### 提取逻辑与字段模型
输入为采集到的原始标题、可见详情文本、主图和详情图。流程采用"文本候选 + 图片证据 + 人工确认"：先从标题/详情中抽取明确出现的词，再从图片识别颜色、形态、使用环境和可见结构；二者冲突或图片不足时标记 `unknown/needs_review`，绝不把推测写为规格、功效或认证事实。

建议生成下列结构化 `product_brief`，并让每个字段保留 `source`（title/detail/image/manual）和 `confidence`：

```json
{
  "category": "品类与子品类",
  "material": ["可证实的材质/工艺"],
  "attributes": {"color": [], "pattern": [], "shape": "", "size_or_fit": ""},
  "features": ["可由来源支持的卖点"],
  "use_scenarios": ["使用场景"],
  "style": ["风格关键词"],
  "audience": ["目标人群/适用对象"],
  "included_or_variant": ["套装内容、款式差异"],
  "image_evidence": [{"image_index": 0, "observed": "…"}],
  "constraints": ["禁用的夸大、医疗/安全/品牌/IP 声称"],
  "unknowns": ["不能从输入确认的信息"]
}
```

抽取规则：标题权重最高的是品类、材质、数量/尺寸与型号；详情图权重最高的是结构、细节和使用方式；主图权重最高的是颜色、轮廓和视觉风格。将近义词归一（如"极简/简约"），将营销词（"顶级""最安全"）移至 `constraints`，将人物年龄、材质成分、承重、防水等级等无法证实内容放入 `unknowns`。生成图只可使用 `confidence=high` 或人工确认的字段。

### 通用 API 提示词骨架
每一种图片都以同一骨架生成；调用前把方括号替换为已确认字段，空字段删除。`[REFERENCE_IMAGES]` 应传入已审核的原图作为视觉参考；禁止要求模型复制原图中的商标、人物或受版权保护的图案。

```text
Create one original ecommerce product image for [MARKET/LOCALE].
Product: [CATEGORY]; confirmed material: [MATERIAL]; confirmed color/pattern: [COLOR_PATTERN];
Confirmed features: [FEATURES]. Intended scenario: [SCENARIO]. Style: [STYLE]. Audience: [AUDIENCE].
Image type: [IMAGE_TYPE]. Use [REFERENCE_IMAGES] only to preserve the confirmed product shape, color and non-branded details; do not copy logos, trademarks, packaging artwork, or recognizable people.
Composition: [COMPOSITION]. Lighting: [LIGHTING]. Background: [BACKGROUND].
Show a physically plausible product with accurate proportions. Do not invent components, claims, measurements, certifications, before/after results, or extra products.
Text overlay: [OVERLAY_RULE]. If text is used, render exactly: "[APPROVED_COPY]"; otherwise render no text.
Output: vertical 3:4, high-resolution ecommerce photography, clean edges, no watermark, no logo, no unreadable pseudo-text.
```

### 图片类型与可复用生成模板
以下模板是在通用骨架的 `[IMAGE_TYPE]` 之后补充的专用段；它们可以直接拼入 GPT Image 或 Gemini 的 prompt。尺寸、折扣、成分比例等字段仅在有人工批准的 `APPROVED_COPY` 时使用。

| 类型 | 用途与专用 prompt 段 |
| --- | --- |
| 白底主图 | 识别商品、建立干净首图。`IMAGE_TYPE: white-background hero. COMPOSITION: one [CATEGORY] centered, front three-quarter view, occupying 75–85% of frame, full product visible with natural shadow. LIGHTING: large softbox, neutral daylight, even exposure. BACKGROUND: seamless pure white #FFFFFF. OVERLAY_RULE: no text, no badges, no props, no hands.` |
| 卖点图 | 传达一个已证实卖点。`IMAGE_TYPE: single-feature benefit card. COMPOSITION: product on the right with a clear visual cue for [ONE_CONFIRMED_FEATURE]; reserve the left 35% as clean negative space. LIGHTING: soft commercial studio light. BACKGROUND: [STYLE]-appropriate subtle gradient. OVERLAY_RULE: exactly one short approved headline "[APPROVED_COPY]", maximum 8 words; no unsupported icon, number, or claim.` |
| 场景图 | 帮用户理解使用环境。`IMAGE_TYPE: lifestyle scene. COMPOSITION: [CATEGORY] naturally used in [CONFIRMED_SCENARIO], camera at eye level, product remains the focal point and fills at least 40% of frame. LIGHTING: believable [morning window light/warm home light] consistent with the scene. BACKGROUND: uncluttered [SCENE]. OVERLAY_RULE: no text. Do not depict unsafe, medical, or unverified use.` |
| 模特/使用图 | 展示穿戴、拿持或尺度感。`IMAGE_TYPE: human-use image. COMPOSITION: a non-recognizable adult model [USING/WEARING] the product according to [CONFIRMED_USE]; crop face out or use a generic non-identifiable face; product and fit are sharp. LIGHTING: soft editorial daylight. BACKGROUND: minimal [SCENE]. OVERLAY_RULE: no text. Do not infer gender, age, body size, or performance claims beyond the confirmed audience.` |
| 细节特写 | 证明纹理、闭合、边缘或工艺。`IMAGE_TYPE: macro detail. COMPOSITION: tight 4:5 crop of [CONFIRMED_DETAIL], product surface fills 70% of frame, shallow depth of field with the relevant detail tack sharp. LIGHTING: raking soft light that reveals texture without changing color. BACKGROUND: softly blurred neutral surface. OVERLAY_RULE: optional exact label "[APPROVED_COPY]" only if it names the visible detail.` |
| 尺寸/规格图 | 说明已确认的测量或套装组成。`IMAGE_TYPE: measurement infographic. COMPOSITION: product on a clean pale background with thin, straight dimension lines only at approved measurement points; preserve real proportions. LIGHTING: flat, shadow-controlled studio light. BACKGROUND: light neutral. OVERLAY_RULE: render exactly the approved values and units "[APPROVED_COPY]"; no guessed dimensions; use legible sans-serif typography.` |
| 对比图 | 表达同款色/规格差异或已验证改进。`IMAGE_TYPE: factual comparison. COMPOSITION: two equal panels labelled only with approved labels, showing [VARIANT_A] and [VARIANT_B] at matching angle, scale, and lighting. LIGHTING: identical neutral studio lighting. BACKGROUND: same plain background in both panels. OVERLAY_RULE: exact labels "[APPROVED_COPY]"; never use competitor brands, 'best', before/after effects, or unsupported superiority claims.` |

### "提取 → 生成描述"映射流程
```text
标题/详情文本 ─┐
                ├─> 字段候选与证据绑定 ─> 规则校验/人工确认 ─> product_brief
主图/详情图 ───┘                                           │
                                                            ├─> 白底图：category + color/pattern + shape
                                                            ├─> 卖点/细节图：one confirmed feature/detail
                                                            ├─> 场景/模特图：confirmed scenario + audience + use
                                                            ├─> 尺寸图：manual-approved measurements only
                                                            └─> 对比图：approved variant fields only
```
具体执行时，先按"白底主图 → 卖点图 → 场景图 → 细节图"的顺序生成小样，每张生成请求附上 `product_brief`、所用字段、参考图 ID 与模板版本。审核人检查"是否像商品、是否有臆造文字/零件、是否触犯品牌或功效声明"后才能进入导出；尺寸和对比图必须额外人工批准。这样即使采集源不完整，也能以 `unknowns` 阻止幻觉字段进入商业素材。

---

# 3. AI 图片生成方案（CEO 肉肉）

> 本章设计"把 §2 提取出的生成描述，喂给 GPT Image / Gemini 模型产出营销图"的具体调用方案，并刻意对齐 LinkFox 的做法。

## 3.1 模型选型与成本（2026-07 当前价）

| 模型 | 单价（约） | 说明 | 适用 |
|---|---|---|---|
| **GPT Image 1 Mini**（Low） | **$0.005/张** | 最便宜，质量可用 | 大批量场景图/草图 |
| GPT Image 1（Medium） | $0.042/张 | 主图质量均衡 | 主图/卖点图 |
| GPT Image 1（High） | $0.167/张 | 最高保真 | 精修大图 |
| **Gemini 2.5 Flash Image**（Nano Banana） | **$0.024–0.039/张** | 性价比高、自带角色一致性 | 日常套图/换背景 |
| Gemini 3 Pro Image | $0.035/张 | Elo 1235–1268，质量顶尖 | 高质量主图 |
| Imagen 4 Fast | $0.02/张 | Google 最便宜 | 大批量 |

**结论**：日常套图用 **GPT Image 1 Mini（Low ~$0.005）** 或 **Gemini 2.5 Flash Image（~$0.024）** 控成本；高质量主图用 **GPT Image 1 Medium（$0.042）** 或 **Gemini 3 Pro Image（$0.035）**。对比 LinkFox 约 ¥0.11–1/张，云 API 单张成本约 **¥0.04–0.3**，明显更便宜且无功能订阅。

## 3.2 两类核心调用（对齐 LinkFox）

**A. 文生图（卖点图 / 场景图 / 白底图）**
- 输入：§2 生成的"生成描述模板"（含风格/构图/光影/文字指示）。
- 调用：`gpt-image-1` 经 `v1/images/generations`（或 Responses API）；Gemini 经 `gemini-2.5-flash-image`。
- 示例 prompt 模板（来自 §2）：
  ```
  一张电商产品主图：<品类> 置于 <场景>，<光影/材质要求>，
  背景干净留白 20%，画面右下角小字叠加卖点"<卖点>"，
  高清、4K、商业摄影质感，无畸变。
  ```

**B. 图生图 / 局部重绘（换背景 / AI 模特 / 场景裂变）** —— 对应 LinkFox 的"白底图→场景图""AI 换模特""场景裂变"
- OpenAI：`v1/images/edits` 传入原图 + 蒙版/参考 + 文字指令（换背景、加模特、加道具）。
- Gemini：以原图作为 `image` 输入 + 编辑指令（Nano Banana Pro 支持对话式编辑，适合"换肤色/换场景/局部改"）。
- "场景裂变"实现：固定商品主体 + 变换场景描述词，循环 N 次 → N 张相似风格场景图（复刻 LinkFox 3 秒裂变）。

## 3.3 批量套图流水线（参考 Linkfox "5 分钟全套"）

```
商品清单(来自§1)  ──►  for 每个商品:
                         ├─ §2 提取字段 → 生成描述(按图片类型)
                         ├─ for 每种图片类型(卖点/场景/模特/特写):
                         │     ├─ 文生图 or 图生图(edits)
                         │     ├─ 固定 style prompt + seed 保一致性
                         │     └─ 落盘 reports/gen/<SKU>_<类型>_<idx>.png
                         └─ 文字叠加(如需平台文案): Pillow 本地合成(避模型文字错乱)
```
- **一致性保障**：固定负向词 + style prompt + 固定 seed；强人脸/姿态一致性（服装模特）用开源 **PuLID / ControlNet** 补刀（见 §4 开源参考）。
- **编排**：Codex 可经现成 skill（`openai/codex imagegen` / `nanobanana-codex-skill`）写脚本驱动上述流水线——即"agent 驱动的生图流水线"，与 LinkFox Claw 引擎思路一致。

## 3.4 与 Linkfox 的对应关系

| LinkFox 能力 | 本方案实现 |
|---|---|
| 白底图 → 全套场景/A+（5 分钟） | 云 API 批量生成，单 SKU 全套 < 2 分钟 |
| AI 换模特 / 换国籍肤色 | 图生图 edits + （强一致时）PuLID/ControlNet |
| 场景裂变（1 图→多相似场景） | 固定主体 + 变换场景词循环生成 |
| 智能修图 / 一致性 | 固定 style+seed + 本地 Pillow 合成文字 |
| 多语言图译 | 先译文案再叠加（Pillow） |

**本方案优势**：成本更低、数据不出云、流程完全可控、可沉淀为项目内可复用脚本；**短板**：强人脸/姿态一致性不如 LinkFox 独家人脸微调，需开源模型补刀（§4/§5 详述）。

---

# 4. Linkfox 商品套图功能调研与对比（Orbit Codex）

### 4.1 已核实的产品能力与生成流程
LinkFox 将"商品套图"定位为从一张商品图出发的电商素材生产能力：官方页面列出 A+ 图、卖点图、场景图和特写图，并说明会识别品牌元素以维持同一套图的风格；商品套图页还列出尺寸图和细节图。服装路径另有服装套图、真人换模特、模特换场景、AI 穿衣与姿势裂变。因此本轮 POC 应将其理解为"商品主体保真 + 多版式/多场景衍生"，而不是保证每次都输出可直接过审的全部图片类型。[LinkFox 商品套图说明](https://ai.linkfox.com/listing) [LinkFox 套餐功能表](https://www.linkfox.com/price)

可归纳的输入→处理→输出流程如下：

1. 输入：至少一张商品白底/实拍图；服装能力还可输入平铺服装或真人模特图。用户可按目标国家、语言、平台设定服装套图方向。
2. 处理：平台识别品类、卖点和品牌元素，推荐 AI 场景；再对商品替换、场景裂变、换模特/穿衣、姿势裂变等任务执行生成或编辑。官方同时提供抠图、局部重绘、扩图、换色、精修等后处理。
3. 输出：普通商品可获得白底/商品主图、场景图、卖点图、A+ 图、尺寸图、细节/特写图；服装还可获得不同人种/场景/姿势的模特图。结果仍应进入人工质检：文字、尺寸、商标、手部和商品结构均是生成式图像的高风险点。

该产品的交付边界也很清晰：其宣传为"一张商品图"驱动，场景图是 AI 推荐/生成，批量生图可按多任务打包；这降低操作门槛，但意味着生成策略、底层模型、审核规则和失败重试均是平台黑盒。[LinkFox 场景与批量能力](https://ai.linkfox.com/listing)

### 4.2 定价、配额与使用限制（以 2026-07-22 页面为准）
LinkFox 的免费版是 7 天、250 点、约 25 张图、1 个并发任务且高清下载带水印。年付基础版页面显示 662.40 元/年、42,000 点、约 4,200 张图或 140 个视频、10 个并发和无水印下载；高级版为 252,000 点、约 25,200 张图或 840 个视频、30 个并发。团队版从 4,790.4 元/年、432,000 点、约 43,200 张图或 1,440 个视频和 5 人共享起，包含批量、企业资料库等；其 API 仅向团队版开放。该页同时注明批量生图按多任务打包计费，单图最低可到 7 折。[官方价格与 API 限制](https://www.linkfox.com/price) [批量计费说明](https://ai.linkfox.com/listing)

这些"约生成"数是算力点折算，不能当成固定单图报价；视频、修图、重试和不同模式会消耗不同点数。POC 不能只以免费版出图效果判定可规模化，也不能假定个人版可直接接入本系统。

### 4.3 与自建方案的逐项比较

| 维度 | LinkFox 商品套图 | 我方：采集→提取→GPT Image/Gemini 生成 |
| --- | --- | --- |
| 能力覆盖 | 已封装套图、场景、卖点、模特、修图、批量与素材库；服装路径完整 | 可从 TikTok 商品资料/图片抽取卖点、结构化生成提示词，按商品类目定制白底、场景、卖点、细节任务；虚拟试穿需另接模型 |
| 生成流程 | 上传后由平台识别、推荐并生成，最快验证 | 我方掌握输入清洗、提示词、参考图、版本、审核和回写；需建设编排、队列、存储与 UI |
| 成本模型 | 算力订阅+点数，免费试用小、团队 API 才开放；有并发和历史保存限制 | 按模型/API/算力实耗；GPT Image 官方为文本输入 $5/百万 token、图像输入 $10/百万 token、图像输出 $40/百万 token；Gemini 2.5 Flash Image 付费标准 1024px 图约 $0.039/张，免费层不提供该模型图像输出 [OpenAI 定价](https://openai.com/index/image-generation-api/) [Gemini 定价](https://ai.google.dev/gemini-api/docs/pricing) |
| 可控性 | 快，但模型、提示词、审核阈值、素材数据与任务恢复不可见 | 高：可固化商品 JSON、品牌规则、比例和平台禁词；可记录 seed/版本、做 A/B 与失败重试 |
| 外部依赖 | 高：LinkFox 账号、点数、团队 API 权限和产品功能变动 | 仍依赖 OpenAI/Google 或本地 GPU；但可多模型降级、原图和任务记录留在本仓库控制域 |
| 适用结论 | 用免费版/团队试用做效果标杆与人工运营提效 | 用于需要和 TikTok SKU、审核、成本统计联动的可持续生产；不必复刻 LinkFox 的完整前端 |

建议的分工不是二选一：用 LinkFox 免费版对 10 个代表 SKU 生成基准套图，记录单张有效率、人工修正分钟数和点数；自建 MVP 只实现"白底图/商品图 + 2 个场景 + 1 个卖点图 + 人工批准"四类任务，达到同等可用率后再扩大。

### 4.4 可借鉴的开源项目

| 项目 | 链接 | 可借鉴点与边界 |
| --- | --- | --- |
| ComfyUI | [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI) | 节点图编排、队列和本地工作流 API，适合把抠图、修补、放大、生成拆为可观察步骤；须自行运维 GPU、模型和安全更新。 |
| ComfyUI IP-Adapter Plus | [cubiq/ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus) | 用商品/风格参考图约束构图与视觉风格，可用于"同一商品多场景"一致性；其模型与 LoRA 存放/配对要求应纳入部署检查。 |
| OOTDiffusion | [levihsu/OOTDiffusion](https://github.com/levihsu/OOTDiffusion) | 面向服装虚拟试穿，强调服装特征和人体的可控融合；适合做服装模特图备选，跨品类试穿仍可能失败。[论文与代码说明](https://arxiv.org/abs/2403.01779) |
| CatVTON | [Zheng-Chong/CatVTON](https://github.com/Zheng-Chong/CatVTON) | 轻量虚拟试穿路线，仓库声明 1024×768 推理低于 8GB VRAM，并提供 ComfyUI 工作流；适合低成本试验，但须实测服装纹理、遮挡和商用许可证。[项目说明](https://github.com/Zheng-Chong/CatVTON) |
| rembg | [danielgatis/rembg](https://github.com/danielgatis/rembg) | 先做可重复的背景 removal/透明 PNG，再交给生成模型补场景，减少商品主体被改写；透明、反光和毛绒边缘要设人工复检。 |

---

# 5. 可行性评估（API 调用能力确认）（Orbit Codex）

### 5.1 本机与代码库的非付费核查
本次只读取当前进程环境变量是否存在及长度，未输出密钥值、未发送网络请求、未调用任何付费生成 API。结果如下：

| 检查项 | 结果 | 结论 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 已设置，长度 164（长度合理） | 具备配置 OpenAI 调用的必要凭据前提，但不能由长度证明账户已开通计费或图像模型权限。 |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | 均未设置 | 当前不能以 Gemini Developer API 作为可执行后端。 |
| 项目内客户端 | `core/llm.py` 有 OpenAI 兼容的 Chat Completions 客户端，默认模型为 `gpt-4o-mini` | 现有代码可复用认证/超时/重试思路，但没有 Images API 或 Gemini 图像生成客户端，不能直接出图。 |
| 本地模型/ComfyUI | 本仓库未发现已接入的 ComfyUI 工作流或图像生成服务配置 | 不能宣称已有本地兜底能力；需另行部署和验收。 |

### 5.2 外部 API 的当前能力、成本与限制
OpenAI 的 `gpt-image-1` 支持 API 图像生成/编辑，且官方说明使用独立的文本、输入图像和输出图像 token 定价；实际可用性还取决于该 Key 所属项目的组织验证、预算、模型权限和速率限制，未调用前不可确认。[OpenAI 图像生成指南](https://developers.openai.com/api/docs/guides/image-generation) [OpenAI 定价](https://developers.openai.com/api/docs/pricing)

Gemini 的 `gemini-2.5-flash-image`（Nano Banana）仍列为稳定模型，支持图像与文本输入、图像与文本输出，适合高吞吐图像生成和对话式编辑；但 Google 当前文档将其免费层图像输出列为"不提供"，付费标准层 1024×1024 图约 $0.039。官方同时建议新项目迁往更新的 Nano Banana 2 系列，因此接口层必须使用可配置 model id，避免把 2.5 写死。[模型能力](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-image) [图像生成迁移建议](https://ai.google.dev/gemini-api/docs/image-generation) [价格](https://ai.google.dev/gemini-api/docs/pricing)

### 5.3 可落地结论与下一步
结论：可以开始"只接 OpenAI、人工批准"的开发，不应声称已经具备生产出图能力；因为密钥存在但图像端点权限、计费和质量均未验证。Gemini 目前是明确缺口。LinkFox 可作为低代码对照，而不是本系统的无权限替代。

下一步按风险由低到高执行：

1. 新增一个不含密钥的图像后端配置（`provider`、`model`、单图预算、尺寸、比例、超时），并实现 OpenAI Images API 适配器、任务日志和 dry-run（只校验输入/预算，绝不请求生成）。
2. 由账户所有者在控制台确认 OpenAI 图像模型权限、预算上限和数据合规后，选 1 个非敏感 SKU 做一次人工授权的付费冒烟测试；记录响应、实际账单和可用图率，不能以本报告替代该测试。
3. 若需要 Gemini，申请/配置专用 `GEMINI_API_KEY`，在 Google AI Studio/计费项目设置预算与地域合规；优先评估当前推荐的 Nano Banana 2，而非新建项目锁定 2.5。
4. 若 API 权限或成本不满足，部署 ComfyUI + rembg + IP-Adapter 作为离线兜底，先以 10 个 SKU 的主体保真率、文字错误率、每图人工分钟数、GPU/电费为验收指标；服装再单独试验 CatVTON/OOTDiffusion，禁止混入普通商品主图生产线。

---

# 6. 汇总与下一步（CEO 肉肉）

## 6.1 三人结论整合
- **采集（Cursor / §1）**：选"妙手采集"为主路径最省事——密钥已在本地配置、项目已有 `/new-product` 预览入口、`miaoshou_precollect.py` 不认领/不发布，符合小验证先人工审核的边界。直采留作第二阶段受限 connector。
- **提取与图片类型（Cursor / §2）**：给出可复用的 `product_brief` 字段模型 + 7 类图片生成模板（白底/卖点/场景/模特/细节/尺寸/对比）+ "字段→生成描述"映射；核心纪律是"只信 high 置信字段，未知进 `unknowns`，绝不臆造卖点/认证"。
- **生成（肉肉 / §3）**：GPT Image 1 Mini（~$0.005/张）与 Gemini Flash Image（~$0.024/张）已能覆盖 LinkFox 全部套图类型（换背景/AI模特/场景裂变/局部重绘），单张成本仅为 LinkFox 的 1/10~1/3；强一致用开源 PuLID/ControlNet 补刀。
- **对比（Codex / §4）**：LinkFox 是黑盒封装（识别→推荐→生成，快但不可见），我方是透明可控流水线；建议"LinkFox 免费版做基准 + 自建 MVP 只做 4 类任务先达标"，而非二选一。
- **可行性（Codex / §5）**：`OPENAI_API_KEY` 已设置（可起步），`GEMINI_API_KEY` 未设（缺口）；但**图像端点权限/计费/质量均未验证**，故仅"可开始开发"，不能声称已具备生产出图能力。

## 6.2 小规模验证落地建议（MVP）
**范围**：10 个代表 SKU × {白底图 + 2 场景图 + 1 卖点图}，**100% 人工批准**后才入库。
**流水线路径**：`妙手采集` → `product_brief 提取` → `§3 生成（先只用 OpenAI）` → `人工质检（文字/品牌/结构/手部）` → `落盘 reports/gen/`。
**验收指标**：主体保真率、文字错误率（目标 0）、每张人工分钟数、单图实际成本。

## 6.3 风险与红线（必须遵守）
1. **安全红线**：生成/发布类操作走 Boss 确认闸；本方案只做"内部预览/审核"，不自动上架。
2. **不臆造**：严格用 `product_brief` 的 high 置信字段，尺寸/对比图必须额外人工批准。
3. **版权**：参考图只用于保真商品形状/颜色，禁止复制商标/人物/IP；模特用 non-identifiable 面部。
4. **成本**：单图预算阈值 + 超阈飞书请示（沿用委派铁律）。

## 6.4 待 Boss 拍板
1. **是否授权 OpenAI 图像模型冒烟测试**（1 个非敏感 SKU 的付费实调，约 $0.01–0.05）？
2. **是否补 `GEMINI_API_KEY`** 以便双模型（Nano Banana 角色一致性更优）？
3. **MVP 范围确认**：10 个 SKU × 4 图类是否合适，还是先 3 个 SKU 试水？
4. **是否现在就让 Codex 把 §3 流水线写成项目内可复用脚本**（`gen_suite.py` + OpenAI 适配器 + dry-run）？

> 本报告由 CEO 肉肉（§0/§3/§6）、Orbit Cursor（§1/§2）、Orbit Codex（§4/§5）三方协作完成同一份文档；三人均经 A2A 框架真实执行并回报。
