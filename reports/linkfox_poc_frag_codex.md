## 4. Linkfox 商品套图功能调研与对比

### 4.1 已核实的产品能力与生成流程

LinkFox 将“商品套图”定位为从一张商品图出发的电商素材生产能力：官方页面列出 A+ 图、卖点图、场景图和特写图，并说明会识别品牌元素以维持同一套图的风格；商品套图页还列出尺寸图和细节图。服装路径另有服装套图、真人换模特、模特换场景、AI 穿衣与姿势裂变。因此本轮 POC 应将其理解为“商品主体保真 + 多版式/多场景衍生”，而不是保证每次都输出可直接过审的全部图片类型。[LinkFox 商品套图说明](https://ai.linkfox.com/listing) [LinkFox 套餐功能表](https://www.linkfox.com/price)

可归纳的输入→处理→输出流程如下：

1. 输入：至少一张商品白底/实拍图；服装能力还可输入平铺服装或真人模特图。用户可按目标国家、语言、平台设定服装套图方向。
2. 处理：平台识别品类、卖点和品牌元素，推荐 AI 场景；再对商品替换、场景裂变、换模特/穿衣、姿势裂变等任务执行生成或编辑。官方同时提供抠图、局部重绘、扩图、换色、精修等后处理。
3. 输出：普通商品可获得白底/商品主图、场景图、卖点图、A+ 图、尺寸图、细节/特写图；服装还可获得不同人种/场景/姿势的模特图。结果仍应进入人工质检：文字、尺寸、商标、手部和商品结构均是生成式图像的高风险点。

该产品的交付边界也很清晰：其宣传为“一张商品图”驱动，场景图是 AI 推荐/生成，批量生图可按多任务打包；这降低操作门槛，但意味着生成策略、底层模型、审核规则和失败重试均是平台黑盒。[LinkFox 场景与批量能力](https://ai.linkfox.com/listing)

### 4.2 定价、配额与使用限制（以 2026-07-22 页面为准）

LinkFox 的免费版是 7 天、250 点、约 25 张图、1 个并发任务且高清下载带水印。年付基础版页面显示 662.40 元/年、42,000 点、约 4,200 张图或 140 个视频、10 个并发和无水印下载；高级版为 252,000 点、约 25,200 张图或 840 个视频、30 个并发。团队版从 4,790.4 元/年、432,000 点、约 43,200 张图或 1,440 个视频和 5 人共享起，包含批量、企业资料库等；其 API 仅向团队版开放。该页同时注明批量生图按多任务打包计费，单图最低可到 7 折。[官方价格与 API 限制](https://www.linkfox.com/price) [批量计费说明](https://ai.linkfox.com/listing)

这些“约生成”数是算力点折算，不能当成固定单图报价；视频、修图、重试和不同模式会消耗不同点数。POC 不能只以免费版出图效果判定可规模化，也不能假定个人版可直接接入本系统。

### 4.3 与自建方案的逐项比较

| 维度 | LinkFox 商品套图 | 我方：采集→提取→GPT Image/Gemini 生成 |
| --- | --- | --- |
| 能力覆盖 | 已封装套图、场景、卖点、模特、修图、批量与素材库；服装路径完整 | 可从 TikTok 商品资料/图片抽取卖点、结构化生成提示词，按商品类目定制白底、场景、卖点、细节任务；虚拟试穿需另接模型 |
| 生成流程 | 上传后由平台识别、推荐并生成，最快验证 | 我方掌握输入清洗、提示词、参考图、版本、审核和回写；需建设编排、队列、存储与 UI |
| 成本模型 | 算力订阅+点数，免费试用小、团队 API 才开放；有并发和历史保存限制 | 按模型/API/算力实耗；GPT Image 官方为文本输入 $5/百万 token、图像输入 $10/百万 token、图像输出 $40/百万 token；Gemini 2.5 Flash Image 付费标准 1024px 图约 $0.039/张，免费层不提供该模型图像输出 [OpenAI 定价](https://openai.com/index/image-generation-api/) [Gemini 定价](https://ai.google.dev/gemini-api/docs/pricing) |
| 可控性 | 快，但模型、提示词、审核阈值、素材数据与任务恢复不可见 | 高：可固化商品 JSON、品牌规则、比例和平台禁词；可记录 seed/版本、做 A/B 与失败重试 |
| 外部依赖 | 高：LinkFox 账号、点数、团队 API 权限和产品功能变动 | 仍依赖 OpenAI/Google 或本地 GPU；但可多模型降级、原图和任务记录留在本仓库控制域 |
| 适用结论 | 用免费版/团队试用做效果标杆与人工运营提效 | 用于需要和 TikTok SKU、审核、成本统计联动的可持续生产；不必复刻 LinkFox 的完整前端 |

建议的分工不是二选一：用 LinkFox 免费版对 10 个代表 SKU 生成基准套图，记录单张有效率、人工修正分钟数和点数；自建 MVP 只实现“白底图/商品图 + 2 个场景 + 1 个卖点图 + 人工批准”四类任务，达到同等可用率后再扩大。

### 4.4 可借鉴的开源项目

| 项目 | 链接 | 可借鉴点与边界 |
| --- | --- | --- |
| ComfyUI | [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI) | 节点图编排、队列和本地工作流 API，适合把抠图、修补、放大、生成拆为可观察步骤；须自行运维 GPU、模型和安全更新。 |
| ComfyUI IP-Adapter Plus | [cubiq/ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus) | 用商品/风格参考图约束构图与视觉风格，可用于“同一商品多场景”一致性；其模型与 LoRA 存放/配对要求应纳入部署检查。 |
| OOTDiffusion | [levihsu/OOTDiffusion](https://github.com/levihsu/OOTDiffusion) | 面向服装虚拟试穿，强调服装特征和人体的可控融合；适合做服装模特图备选，跨品类试穿仍可能失败。[论文与代码说明](https://arxiv.org/abs/2403.01779) |
| CatVTON | [Zheng-Chong/CatVTON](https://github.com/Zheng-Chong/CatVTON) | 轻量虚拟试穿路线，仓库声明 1024×768 推理低于 8GB VRAM，并提供 ComfyUI 工作流；适合低成本试验，但须实测服装纹理、遮挡和商用许可证。[项目说明](https://github.com/Zheng-Chong/CatVTON) |
| rembg | [danielgatis/rembg](https://github.com/danielgatis/rembg) | 先做可重复的背景移除/透明 PNG，再交给生成模型补场景，减少商品主体被改写；透明、反光和毛绒边缘要设人工复检。 |

## 5. 可行性评估（API 调用能力确认）

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

Gemini 的 `gemini-2.5-flash-image`（Nano Banana）仍列为稳定模型，支持图像与文本输入、图像与文本输出，适合高吞吐图像生成和对话式编辑；但 Google 当前文档将其免费层图像输出列为“不提供”，付费标准层 1024×1024 图约 $0.039。官方同时建议新项目迁往更新的 Nano Banana 2 系列，因此接口层必须使用可配置 model id，避免把 2.5 写死。[模型能力](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-image) [图像生成迁移建议](https://ai.google.dev/gemini-api/docs/image-generation) [价格](https://ai.google.dev/gemini-api/docs/pricing)

### 5.3 可落地结论与下一步

结论：可以开始“只接 OpenAI、人工批准”的开发，不应声称已经具备生产出图能力；因为密钥存在但图像端点权限、计费和质量均未验证。Gemini 目前是明确缺口。LinkFox 可作为低代码对照，而不是本系统的无权限替代。

下一步按风险由低到高执行：

1. 新增一个不含密钥的图像后端配置（`provider`、`model`、单图预算、尺寸、比例、超时），并实现 OpenAI Images API 适配器、任务日志和 dry-run（只校验输入/预算，绝不请求生成）。
2. 由账户所有者在控制台确认 OpenAI 图像模型权限、预算上限和数据合规后，选 1 个非敏感 SKU 做一次人工授权的付费冒烟测试；记录响应、实际账单和可用图率，不能以本报告替代该测试。
3. 若需要 Gemini，申请/配置专用 `GEMINI_API_KEY`，在 Google AI Studio/计费项目设置预算与地域合规；优先评估当前推荐的 Nano Banana 2，而非新建项目锁定 2.5。
4. 若 API 权限或成本不满足，部署 ComfyUI + rembg + IP-Adapter 作为离线兜底，先以 10 个 SKU 的主体保真率、文字错误率、每图人工分钟数、GPU/电费为验收指标；服装再单独试验 CatVTON/OOTDiffusion，禁止混入普通商品主图生产线。
