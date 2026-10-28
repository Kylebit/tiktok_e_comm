# LinkFox Skills 与自建小系统调研

调研日期：2026-07-22。本文只把网页明确披露的内容当作事实；容量、成本和可替代性均标明计算口径或判断，未做账号实测。

## 结论

LinkFox 的差异化不止是商品作图，而是「已接入的数据源 + 可安装的 Agent Skills + 托管工作流/团队资产」的组合。对于本仓库，最经济的路线不是完全复刻：用自建 ComfyUI 图像流水线承担商品图，再按需安装 LinkFox Skills 获取跨平台选品、竞品、关键词和合规数据。后者仍须使用其 API Key，不能误认为是离线免费数据。

## 1. 已核实的产品与 Skills 体系

### 1.1 LinkFox 作图、模板与企业能力

官方价格页列出商品套图、商品替换、场景裂变、手持商品、图片翻译；模特能力含服装套图、真人换模特、模特换场景、AI 穿衣/穿戴和姿势裂变；修图含局部重绘、局部消除、换色、裁剪、高清放大、扩图、精细抠图及批量抠图。团队档还明确列有批量功能、企业资料库、团队模板、共享素材/算力和最多 20 人的档位。[官方套餐功能表](https://www.linkfox.com/price)

这意味着它适合把「上传商品图 → 生成套图/模特图 → 人工挑选 → 导出」做成可复用的运营模板。边界也很清楚：价格页只证明页面提供这些入口，不能据此推断每种商品、人物或姿势都能一次生成可上架结果；主图合规、文字准确性和人物/商标风险仍需要人工质检。

### 1.2 开放的 `linkfox-ai/linkfox-skills`

GitHub README 当前称该仓库有 **118 个 API 驱动 skills**，基于 Agent Skills 开放标准，兼容 Cursor、Claude Code、GitHub Copilot 等 30+ Agent 平台；仓库为 MIT 许可。安装与最小配置为：

```bash
npx skills add linkfox-ai/linkfox-skills --agent cursor
export LINKFOXAGENT_API_KEY=your-key-here
```

也可以 `--list` 后只装特定 skill。README 同时明确所有脚本要求该环境变量，故代码开放不等于数据/API 免费或本地化。[仓库 README](https://github.com/linkfox-ai/linkfox-skills)

按目录可归为：

| 类别 | 已列出的例子 | 可独立调用性与边界 |
| --- | --- | --- |
| 选品/竞品 | Amazon 实时搜索、Keepa、1688、TikTok EchoTik/FastMoss、Ozon Mpstats/Seerfar | 可由装载 skill 的 Agent 发起；结果受上游授权、配额和数据时效约束。 |
| 关键词/广告 | Jungle Scout 关键词、SIF 反查词、Amazon Ads 授权/管理/报表 | 广告写操作需要店铺 OAuth，不能把「报告」权限等同于投放权限。 |
| 店铺运营 | Amazon、Shopee、Temu 的 listing、订单、价格、促销等 skill | 有些动作会改变外部店铺；应接入审批、幂等键和审计。 |
| 风险/专利 | Ruiguan 商标、版权、外观/实用专利检测；PatSnap/Eureka 专利资料 | 仅作检索/初筛，不能替代律师或平台合规结论。 |
| 多模态/生成 | 属性抽取、图像识别、相似度、商品图生成/编辑 | 可作为自建图像栈的补充，但产生第三方 API 成本和数据出境。 |

特别相关的是 Ozon：目录明确列出 MPSTATS 的 Ozon 搜索、SKU 详情、趋势、店铺/类目分析，以及 Seerfar 的关键词挖掘、反查词和商品报告。这与本仓库 Ozon 上品链路互补，而不是替代本仓库的 SKU、审批和 API 写入逻辑。[Ozon skill 目录](https://github.com/linkfox-ai/linkfox-skills#ozon-mpstats)

### 1.3 LinkFox Agent / Claw

ClawHub 的 `linkfoxagent` 安装说明要求 `LINKFOXAGENT_API_KEY`，并警示**任务 prompt 会连同 API Key 发往 `https://agent-api.linkfox.com/`**。说明还要求通过 `sessions_spawn` 派发任务，页面称这类任务通常需 1–5 分钟。这支持将其理解为托管的、可对话分派的 Agent 能力，而非本地 Python 函数库。[ClawHub：linkfoxagent](https://clawhub.ai/linkfox-ai/skills/linkfoxagent)

LinkFox 官网套餐导航亦单列 LinkFox Agent 与 LinkFox Claw，且页面提供 Claw 教程链接；但是本次可公开抓取的产品页没有可验证的「逐步执行/自动执行/定时任务」完整功能规格。因此这些能力在采购前应通过试用账号验收，不能仅凭营销词承诺。已可确认的网页侧自动化相关能力是插件的「自动识别页面信息、内置运营模板、AI 智能分析」和团队批量功能。[官网价格与插件能力](https://www.linkfox.com/price)

## 2. 工作流与安全设计

建议将一次运营任务拆为四个可观测阶段：

```text
SKU/素材入队 → 研究类 Skills（只读）→ 图像工作流 → 人工审批 → Ozon/TikTok 写入
                     │                      │
                保存来源与时间         保存 workflow、seed、输入输出
```

* **逐步执行**：每个 stage 写入 job 状态、输入哈希、来源 URL 和输出路径；失败可重试而不重复发布。
* **自动执行**：仅对只读研究、草稿、图片生成开放；店铺价格、发布、采购、广告变更必须保留本仓库既有确认门。
* **定时任务**：可用 Windows Task Scheduler/cron 触发只读竞品与关键词扫描，再创建待审任务；不要把 API Key 放在任务 JSON、日志或 prompt 中。
* **浏览器插件/伴随式服务**：可提高页面上下文提取速度，但它扩大了可见数据范围；为不同店铺创建最小权限账号，禁用在含凭据页面自动发送上下文。尤其 LinkFox Agent 已明确提示 prompt 会外传。
* **企业模板/资产**：自建时把品牌参考图、prompt 版本、LoRA/工作流、审批人和输出许可证存入受控对象存储；LinkFox 团队版的企业资料库并不自动等价于本公司的留存、删除和访问控制要求。

## 3. 可替代的自建图像栈

### 3.1 推荐 MVP

以 **ComfyUI** 做后端：其工作流是节点图，前端可导出 API 格式；服务器端 `/prompt` 会校验并将工作流放入队列、返回 `prompt_id`，且可用 WebSocket 观察进度。这很适合由本项目服务端提交受版本控制的 JSON workflow，而不是把生成参数散落在浏览器里。[ComfyUI 工作流](https://docs.comfy.org/development/core-concepts/workflow) · [本地服务器路由](https://docs.comfy.org/development/comfyui-server/comms_routes)

| 目标 | 组件 | 采用理由/注意点 |
| --- | --- | --- |
| 基础生成 | SDXL（或合规许可的同级模型）+ ComfyUI | 作为可编排的基础图像工作流；模型、VAE、节点版本必须锁定。 |
| 品牌/主体一致性 | IP-Adapter | 官方仓库将其定义为让预训练文生图模型接受 image prompt 的 adapter，并提供 SDXL、face 与 ControlNet 示例；适合作为参考图条件，不保证商标/产品结构 100% 保真。[IP-Adapter](https://github.com/tencent-ailab/IP-Adapter) |
| 构图/姿势约束 | ControlNet（Canny/OpenPose） | 官方项目定位即「控制 diffusion」，并提供 Canny、depth 等示例；用于固定构图/姿势，仍需检查边缘、手和文字。[ControlNet](https://github.com/lllyasviel/ControlNet) |
| 虚拟试穿/模特 | OOTDiffusion；人脸一致性用 InstantID 或 PuLID | OOTDiffusion 是可控虚拟试穿的官方实现；PuLID 是 NeurIPS 2024 的身份定制项目。两者都应先用已授权模特图做小样验收。[OOTDiffusion](https://github.com/levihsu/OOTDiffusion) · [PuLID](https://github.com/ToTheBeginning/PuLID) |
| 易用前台 | Fooocus（探索） | Fooocus 的定位是简化 prompt 与生成，可作为运营试验台；生产批量仍以 ComfyUI API 为准。[Fooocus](https://github.com/lllyasviel/Fooocus) |
| 中文文字/语义补强 | Qwen-Image（可选） | 官方仓库称其支持复杂文字渲染与精确编辑，Apache-2.0；也要逐图校对商品文案和平台禁词。[Qwen-Image](https://github.com/QwenLM/Qwen-Image) |

SDXL-Turbo/LCM 可以作为低步数预览或筛图加速器；最终营销图应以实际 A/B 清晰度、产品保真和返工率决定，不能只比较秒数。AUTOMATIC1111 也可做单机 UI，但节点化、可导出的 ComfyUI 更适合接入本仓库的任务队列。

### 3.2 是否复用 LinkFox skills

建议复用，范围限于非图像数据能力：安装 selected skills 后把调用包在本仓库 adapter 中，统一记录 skill 名、请求摘要、时间、响应版本和成本。不要在业务代码中直接依赖一个 Agent 的自然语言结论；把结构化响应映射到现有 catalog/Ozon 草稿字段，并让最终发布仍走现有确认流程。研究、关键词、竞品、合规数据的上游壁垒是自建图像栈无法消除的部分。

## 4. 成本估算与回本点

### 4.1 LinkFox 的公开基准

以下是**年付**页面的可复算等价成本，不把视频额度混入图片计算：基础版 ¥662.40 / 约 4,200 图 = **¥0.158/图**；高级版 ¥2,870.40 / 约 25,200 图 = **¥0.114/图**；团队入门版 ¥4,790.40 / 约 43,200 图 = **¥0.111/图**。免费档 250 点约 25 图，仅适合验证。官方还列出加油包 ¥18/1,000 点；若按其「25 图/250 点」的同一口径，约 **¥0.072/图**，但不同功能实际点数可能不同，不能作为所有任务的报价。[LinkFox 定价](https://www.linkfox.com/price)

### 4.2 自建的透明估算（不是市场报价）

以一张现有或新购 GPU 工作站为例，假设：专用于生成的增量硬件成本 ¥2,500、折旧 24 月；实测平均 20 秒/张、每天有效生成 2 小时；整机增量功率 180W，电价 ¥0.80/kWh。则两年产能约 `24×30×2×3600/20 = 259,200` 张；硬件摊销 **¥0.0096/张**，电费 `0.18×(20/3600)×0.80 = ¥0.0008/张`，合计约 **¥0.0104/张**（未计人工、存储、运维、失败重跑）。

在这一假设下，相对基础版每张节约约 ¥0.147，硬件的简单回本量约 `2500 / 0.147 = 17,007` 张；若 GPU 已经存在，边际成本主要是电和人工，不能把设备沉没成本再算一次。反过来，若每月只出几十张，SaaS 的免运维、模板和人工节省往往比电费更重要。云 GPU/API 必须拿到拟用供应商的地区、显卡、按秒计费和实际工作流时长后才可计算，不能拿本地成本冒充云成本。

## 5. 风险清单

1. **模型与节点许可**：逐个核验基础模型、LoRA、ControlNet、参考图的许可证；SDXL/Stable 系列许可并非永远等同 Apache/MIT。Stability 当前许可对年收入超过 US$1m 的商业使用有企业许可条件。[Stability AI License](https://stability.ai/license)
2. **肖像、商标、产品真实性**：未经授权的人脸、品牌标识和竞品图不可作为可随意商用的训练/参考素材；商品图不得虚构尺寸、材质、配件或认证。
3. **数据与凭据**：LinkFoxAgent 提示会把 prompt 送至 `agent-api.linkfox.com`；不得传店铺 token、客户 PII、采购价表或未发布素材。API Key 放 secret store，并按店铺最小化隔离。
4. **发布安全**：采购、广告、价格、上架均是外部写操作。将 LinkFox 工具或自建 Agent 默认设为 dry-run，发布只经本仓库审批卡和人工确认。
5. **能力差距**：自建可较快覆盖作图、批量和基础编排，但无法自然获得 118 skills 背后的实时/授权数据与维护；把「模型生成」和「数据服务」分别预算、验收。

## 6. 落地顺序（4 周 MVP）

1. **第 1 周：图像最小闭环。** 本机/受控服务器部署 ComfyUI，提交固定的 SDXL 商品背景工作流；保存 prompt、seed、模型 hash、输入/输出；人工验收 30 个 SKU。
2. **第 2 周：一致性与质检。** 加 IP-Adapter、Canny/OpenPose；建立商品保真、文字、主图规则和人工抽检阈值。不要先上虚拟试穿。
3. **第 3 周：系统接入。** 将生成任务接到 catalog 草稿，产物只写入待审图片槽；保持 Ozon/MX/UK 的现有发布确认机制不变。
4. **第 4 周：数据 skills 试点。** 只安装 2–3 个只读 skills（例如 Ozon 研究、关键词/竞品）；量化命中率、API 消耗和人工节省。达标后再评估 Claw/定时自动化与 OOTDiffusion/PuLID。

验收指标建议是：每 SKU 可用图率、人工分钟/张、返工率、主图审核通过率、每张全成本和数据任务的可追溯率；不要只用「生成速度」或「调用次数」做决策。
