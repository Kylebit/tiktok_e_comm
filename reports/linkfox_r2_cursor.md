# GPT Image / Nano Banana + Codex 复刻 LinkFox 生图能力调研

调研日期：2026-07-22。

## 结论

**可以复刻 LinkFox 中大部分“商品图生产”能力。** GPT Image 或 Gemini 2.5 Flash Image 负责像素生成/编辑；Codex 负责编写、运行和维护批处理、提示词、掩码、质检、重试、资产目录与审批。难点不在“能否调用模型”，而在可追溯流水线、商品保真、精确文案排版和人像/姿态控制。

本文将 “Banana”限定为题目指定的 `gemini-2.5-flash-image`（Nano Banana）。Google 当前把它描述为旧代 Nano Banana，并建议迁往 Nano Banana 2；因此它适合作为低成本兼容基线，不应是新项目唯一长期依赖。[Google 图像生成指南](https://ai.google.dev/gemini-api/docs/image-generation)

## 1. 两个生成底座的能力边界

|维度|OpenAI `gpt-image-1`|Google `gemini-2.5-flash-image`（Nano Banana）|商品图决策|
|---|---|---|---|
|文生图|Images API 的 `generations`；支持多张、尺寸、质量、格式、压缩和背景参数。|Gemini API 原生多模态生成；指定比例，输出约 1024px。|都可做白底主图、场景图、卖点图底稿。|
|图生图/参考图|`edits` 可传输入图并用提示词整体或局部修改，可多图合成。|文字、图片或两者组合；官方建议 2.5 最多约 3 张输入图。|保留 SKU/材质参考图，生成场景展示。|
|局部编辑（inpainting）|原生 `images.edit` + 同尺寸带 alpha 的 mask；官方说明 mask 只是引导，非像素级严格边界。|可用自然语言多轮编辑/参考图；未见与 GPT 同等明确的独立 mask 承诺。|精确换背景、修瑕疵优先 GPT Image + 分割 mask。|
|风格/角色一致性|可有一定一致性，但官方明确提示重复角色/品牌元素可能漂移。|以对话和图像上下文维持近似一致，适合快速迭代；2.5 多参考容量较小。|可做“近似一致”，不可承诺同一模特脸/Logo 零漂移。|
|版式与文字|文字能力提升，但官方仍列精确位置、清晰度及结构化构图限制。|可生成图中文字；Google 建议先生成文字再要求模型放入图；2.5 为 1024px。|法定规格、价格、促销文字最终应代码排版。|
|速度|无固定 SLA；官方说复杂提示可能到 2 分钟，低质量适合草稿。|官方定位 high-volume、low-latency。|批量先用 Nano Banana 出候选，精选后再精修。|
|API/密钥|Python `OpenAI()` + `images.generate/edit`，需 `OPENAI_API_KEY`，组织可能须验证。|Python `google-genai` 的 `genai.Client()`，需 Gemini API key/付费项目；该模型免费层不可用。|都不是无 key 本地能力；密钥只进环境变量/密钥管理器。|

OpenAI 的 [Image generation 指南](https://developers.openai.com/api/docs/guides/image-generation) 说明 generations、edits 和多轮 Responses API；[限制说明](https://developers.openai.com/api/docs/guides/image-generation#limitations) 明确列出延迟、文字、跨图一致性及构图局限。Gemini 的 [模型页](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-image) 将 2.5 Flash Image 定位为高容量、低延迟、会话式图像编辑；[比例表](https://ai.google.dev/gemini-api/docs/image-generation#gemini-2.5-flash-image) 给出 1K 各比例。

### API 最小接入示意

```python
# OPENAI_API_KEY 在环境变量中；生产环境另加限流、超时、重试、日志
from openai import OpenAI
client = OpenAI()
r = client.images.edit(
    model="gpt-image-1", image=open("input.png", "rb"),
    mask=open("product_mask.png", "rb"),
    prompt="Keep product geometry and logo unchanged; replace only background.",
    size="1024x1536", quality="medium",
)

# GEMINI_API_KEY 在环境变量中；实际还应循环读取 response 的 image part
from google import genai
gemini = genai.Client()
response = gemini.models.generate_content(
    model="gemini-2.5-flash-image",
    contents=["Create a 3:4 ecommerce scene with this product unchanged."]
)
```

调用方式见 [OpenAI Python 示例](https://developers.openai.com/api/docs/guides/image-generation#generate-images) 和 [Google Python 示例](https://ai.google.dev/gemini-api/docs/image-generation)。

## 2. Codex 如何编排为可运营流水线

Codex 不替代图像模型；它是读写仓库、调用 SDK、执行质检和持续改进 prompt 的编排层。建议沉淀如下可重跑任务：

```text
catalog.csv / 原图
        ↓  validate_assets.py（分辨率、重复、SKU、敏感内容）
jobs/<job_id>/manifest.json（参数、模型、输入 hash、成本、状态）
        ↓
01_cutout/      rembg/SAM2 生成商品 mask，人工抽检
        ↓
02_background/  GPT Image mask 换景 或 Nano Banana 多候选场景
        ↓
03_repair/      仅失败区域重绘；保留全部中间版本
        ↓
04_copy/        LLM 生成已审核的卖点/翻译文案
        ↓
05_layout/      Pillow/HTML/SVG 精确叠加文字、Logo、价格
        ↓
06_qc/          OCR、清晰度、白底、相似度、尺寸、人工批准
        ↓
exports/<sku>/<channel>/ + 失败队列 + 重试队列 + 成本报表
```

实施原则：

- 建立 `generate.py --provider openai|gemini --manifest job.json`。以有限并发批量请求，记录 provider request-id、耗时、尺寸、返回文件 hash；对 429/5xx 指数退避。绝不把 key 写入 manifest 或 Git。
- 每个 SKU 以 `job_id`、输入 hash、prompt/模板版本可追溯；原图、mask、候选图不得覆盖，才能诊断并局部重跑。
- 一次动作只改变一个变量：背景 → 局部修复 → 文案排版。不要将三种变化混成一次不可诊断的大请求。
- 采用“低价多候选—机器筛—人工批准—高质最终”。QC 可含 OCR 编辑距离、商品区域与原图的 CLIP/DINO 相似度、背景白度、最短边、文件大小、水印检测；儿童、安全、化妆品等高风险类目仍应人工审图。
- 文字、商标、价格用 Pillow/Canvas/SVG 模板渲染，字体、字距和 safe area 版本化。不要让图像模型承担精确法律文案。
- Gemini Batch API 可用最长 24 小时换取更高限额，适合夜间批量；实时审批走同步 API。[Google Batch 说明](https://ai.google.dev/gemini-api/docs/image-generation#generate-images-in-batch)

## 3. LinkFox 功能映射与缺口

LinkFox 官网套餐页列出 Agent/模特图/商品图/POD 素材/AI 修图，其 API 仅向 Team 用户开放；这表明其产品价值还包括模板、并发、账户与 UX，而非单一模型调用。[LinkFox 价格/API 页](https://www.linkfox.com/price)

|LinkFox 类功能|GPT Image / Nano Banana + Codex|建议实现|仍需或建议补充|
|---|---|---|---|
|商品套图|可以|统一视觉 brief；每 SKU 生成白底、场景、卖点三类；模板排版。|素材库、渠道尺寸规则、人工选片。|
|换背景|可以，较成熟|SAM2/rembg 分割，GPT Image mask 编辑，或 Nano Banana 生成候选场景。|反光、透明、毛发、饰品需人工修 mask。|
|局部重绘|可以|GPT Image mask + 修复 prompt；仅重跑失败区域。|像素级边界或严格姿态不能只靠通用模型。|
|AI 模特换装|可达商业可用的近似结果|服装/商品参考 + 模特底图 + 多轮编辑；检查手、衣物边缘、Logo。|强脸一致性：PuLID/IP-Adapter FaceID；强姿态：ControlNet OpenPose/Depth；虚拟试衣评估 IDM-VTON/CatVTON；还要肖像授权。|
|相似图衍生|可以|以原图 reference，固定镜头/产品约束，生成多场景、角度、色调。|“完全同款、无漂移”需要分割、相似度阈值、人工验收。|
|智能修图|可以|检测→去背景、修瑕、扩图、裁切；只处理确有问题区域。|超分/去噪可用 Real-ESRGAN。|
|模板化详情页|可以，但不应让模型最终排版|HTML/SVG/Pillow 模板 + 真实字段 + OCR 回归测试。|设计系统、字体授权、渠道审核规则。|

结论：标准化商品套图、换背景、局部修图、相似衍生和多数模特图可复刻；“同一真人百图零漂移”“强姿态控制”“像素级不越界”需开源控制模型/专用试衣模型并加入人工验收。开源组件还须单独核对许可证、显存、推理延迟和人像数据合规。

## 4. 成本比较

以下人民币是预算换算，统一按 **$1 = ¥7.20**，不代表结算汇率；不含工程、人审、存储和失败重试。GPT Image 的 gpt-image-1 使用官方 1024×1024 输出价；编辑还会增加文字/图片输入 token。Gemini 输入 token 成本通常较小但非零。

|方案|官方/题设单位价|约人民币/张|100 张输出预算|说明|
|---|---:|---:|---:|---|
|GPT Image 1，1024² low|$0.011|¥0.079|$1.10 / ¥7.92|低成本草稿，质量/成功率须实测。|
|GPT Image 1，1024² medium|$0.042|¥0.302|$4.20 / ¥30.24|常规商品图基线。|
|GPT Image 1，1024² high|$0.167|¥1.202|$16.70 / ¥120.24|最终精选或复杂修复，另加输入 token。|
|Nano Banana 标准|$0.039|¥0.281|$3.90 / ¥28.08|高吞吐候选图；Batch/Flex 为 $0.0195/张。|
|LinkFox 题设比较口径|¥0.11/张|¥0.11|¥11|应视为待核验促销/套餐口径。|
|本地开源（边际）|$0 API 费|¥0 API 费|¥0 API 费|总成本并不为零：GPU、折旧、电费、运维另计。|

OpenAI [图像成本表](https://developers.openai.com/api/docs/guides/image-generation#cost-and-latency) 列出 gpt-image-1 的 low/medium/high 单图输出价，[价格页](https://developers.openai.com/api/docs/pricing) 说明图像/文本 token 分开计。Google [Gemini 定价页](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash-image-nano-banana) 列出 2.5 Flash Image 标准 $0.039、Batch/Flex $0.0195、Priority $0.0702 的每图输出价。

LinkFox 当前公开页展示的是算力点/套餐和“约生成”量（基础版年 42,000 点、约 4,200 张），而不是题设的 ¥0.11 统一公开单价。因此该数字只能作为对比假设；采购前应向销售或后台报价确认。[LinkFox 价格页](https://www.linkfox.com/price)

## 5. 推荐落地顺序与验收

1. 选 20 个 SKU、每个 3 张原素材，定义 3:4 主图、白底合规、场景图和卖点图四种验收模板；同批测试 GPT Image medium 与 Nano Banana，记录成功率、端到端耗时、人工返工分钟数和真实总成本。
2. 先完成 manifest、素材校验、目录与人工审批，不先做大而全前端；一次 API 返回只是“候选”，不是“已发布资产”。
3. 先上线背景替换与套图；文字一律模板渲染。AI 模特待肖像授权、敏感类目策略、人物/商品一致性 A/B 通过后灰度。
4. 量大或需要脸/姿态强控制时，再部署 ControlNet/PuLID/虚拟试衣；以商品保真率、OCR 通过率、人工一次通过率、每合格图成本决定是否转本地。

**最终判断：GPT Image/Nano Banana + Codex 能在能力层复刻 LinkFox 的商品生图、换背景、局部修图、相似衍生和多数模特图；要复刻产品体验还需模板、队列、质检、资产管理和审批。强可控人像/姿态及最终文字版式不应只靠两个通用图像 API。**

