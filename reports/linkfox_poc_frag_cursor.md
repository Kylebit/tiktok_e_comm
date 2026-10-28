## 1. 商品信息采集方案

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
| 稳定性 | 官方 API 在授权和字段范围内较稳；公开页面 HTML/接口随页面、地区、登录和反爬变化，维护成本高 | 已由当前工作台验证的“提交链接—轮询—读取详情”链路；平台差异由妙手承接，仍须显示采集失败原因 |
| 成本 | 官方接入、逐平台开发和长期维护成本高；代理/验证码服务还会带来持续成本 | 沿用现有订阅/API 用量；仅增加本地归一化、缓存和图片校验 |
| 合规与数据风险 | 未授权抓取可能违反平台条款；不应绕过登录、验证码或访问控制；官方 API 也必须遵守授权范围、数据最小化和留存规则 | 仍须确认妙手对来源平台和图片的授权边界；采集仅用于内部预览/审核，不自动发布、不把原图转售或当成已获营销授权 |

直采可作为第二阶段的补充：当某平台已具备明确的官方商品 API、书面授权且妙手不能返回需要的字段时，新增一个受限 connector。LinkFox 当前的 `modules/sourcing/linkfox_client.py` 适合做选品搜索/图片搜索的受控付费调用，不是“给任意商品 URL 返回标题和全量详情图”的通用抓取替代品；它要求 `LINKFOXAGENT_API_KEY` 和显式 `--execute-paid`，因此不作为本 POC 的主采集链路。

### 选型与落地入口

选择 **(b) 妙手采集为 POC 主路径**：实现更少、密钥已在服务端配置、并且项目已经有预览与状态展示。该路径只写入公共采集箱、读取采集详情；不认领店铺、不发布商品，符合小规模验证应先人工审核的边界。

现有可直接复用的入口如下。

1. Web 入口为 `/new-product`（页面 `web/new_product.html`）。用户在“1688 链接 / offer_id / 妙手采集箱详情 ID”输入框粘贴 URL 或 ID，点击“生成第一波预览”。SKU 输入先由一个轻量的目录解析步骤转成原始 `source_url`，再调用同一入口。
2. 前端调用 `POST /api/new-product/preview`，请求体为 `{"url":"<source URL 或 offer_id>","overseas_urls":[],"precollect":true}`。`precollect=true` 会走 `modules.sourcing.new_product_workbench.precollect_preview`。
3. `modules/sourcing/miaoshou_precollect.py` 依次调用妙手 `fetch_item`、公共采集箱列表和详情接口，将标准化标题、主图/详情图及采集状态写入 `data/new_product_workbench/<offer_id>_miaoshou.json`；它的设计明确不会认领或发布。
4. 预览页从响应的 `source.precollect`/`normalized` 渲染标题、图片和失败告警。仅允许状态为 success、图片 URL 可下载且人工勾选的图片进入后续“信息提取”；失败时保留缓存和原错误，允许重试，但不静默降级为伪造字段。

上线前应加三项保护：限制 URL 白名单（1688/Temu 域名及 HTTPS）、把来源/图片授权状态作为必填审核项、对缓存设置保留期限和删除任务。若后续加入直采 connector，也必须通过同一统一结果契约和审核门，而非绕开它。

## 2. 商品信息提取与图片类型设计

### 提取逻辑与字段模型

输入为采集到的原始标题、可见详情文本、主图和详情图。流程采用“文本候选 + 图片证据 + 人工确认”：先从标题/详情中抽取明确出现的词，再从图片识别颜色、形态、使用环境和可见结构；二者冲突或图片不足时标记 `unknown/needs_review`，绝不把推测写为规格、功效或认证事实。

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

抽取规则：标题权重最高的是品类、材质、数量/尺寸与型号；详情图权重最高的是结构、细节和使用方式；主图权重最高的是颜色、轮廓和视觉风格。将近义词归一（如“极简/简约”），将营销词（“顶级”“最安全”）移至 `constraints`，将人物年龄、材质成分、承重、防水等级等无法证实内容放入 `unknowns`。生成图只可使用 `confidence=high` 或人工确认的字段。

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

### “提取 → 生成描述”映射流程

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

具体执行时，先按“白底主图 → 卖点图 → 场景图 → 细节图”的顺序生成小样，每张生成请求附上 `product_brief`、所用字段、参考图 ID 与模板版本。审核人检查“是否像商品、是否有臆造文字/零件、是否触犯品牌或功效声明”后才能进入导出；尺寸和对比图必须额外人工批准。这样即使采集源不完整，也能以 `unknowns` 阻止幻觉字段进入商业素材。
