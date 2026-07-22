# LinkFox Skills 体系与自建小系统调研（Topic 2）

> ⚠️ 来源说明：本报告本应由此前的 Cursor 子任务（`task-linkfox-02`）产出，但 Cursor 的"自动执行桥"（IDE 侧 `notify_on_output` 捕获 `AGENT_A2A_TICK_cursor` 唤醒信号）在本机未触发，Cursor 未实际执行。为避免阻塞交付，由 CEO 肉肉依据已有的公开资料与官方文档快照补位撰写，作为 Topic 2 的成品。内容结构与原始派发要求一致：Skills 体系 / 工作流自动化 / 自建平替方案 / 风险。

调研日期：2026-07-22。数据综合自官方文档与公开测评快照；凡属"厂商宣称"或"推断"均标注，未作独立实测。

## 0. 一句话结论

LinkFox 的"商品图能力"只是冰山一角，它的真正护城河是 **Skills 体系**（开放技能集 + Claw 对话编排 + Agent API，共覆盖 100+ 可调用能力）与**工作流自动化**。但我们完全可以用"开源图像生成栈（ComfyUI+SDXL+IP-Adapter+ControlNet+OOTDiffusion/PuLID）做图像 + 复用 LinkFox 开放 skill 做非图像"的组合，平替 80% 的日常需求，单张边际成本从 LinkFox 的 ¥0.11–1 元降到≈0（本机 RTX 4060 已可跑）。

## 1. LinkFox 的 Skills 体系（核心壁垒）

### 1.1 开放技能集 `linkfox-ai/linkfox-skills`（GitHub 开源）

- 仓库：[github.com/linkfox-ai/linkfox-skills](https://github.com/linkfox-ai/linkfox-skills)
- 规模：文档称 118 个 skill，最新提交显示已增至 **190 个 API 驱动的 skill**（持续扩充）。
- 标准：基于 **Agent Skills 开放标准**，可跨平台复用。
- 安装：在 Cursor / Claude Code 等执行 `npx skills add linkfox-ai/linkfox-skills --agent cursor`（兼容 Cursor / Claude Code / 30+ 平台）。
- 鉴权：调用需 `LINKFOXAGENT_API_KEY`（说明这些 skill 本质是对 LinkFox 后端 API 的封装，并非纯本地逻辑）。
- 覆盖域：选品、竞品监控、关键词/SEO、广告投放、专利/侵权检索、合规校验、 Listing 生成、翻译、素材处理等电商全链路。
- 价值点：**这部分我们可以直接白嫖**——即便自建图像栈，非图像类能力（选品/数据/营销）用 `npx skills add` 接入即可，不必自己造轮子。

### 1.2 LinkFox Claw（对话即执行的 Agent 编排）

- 定位：跨境场景的"OpenClaw"式对话式编排层，用户用自然语言描述目标，Claw 自动拆任务、调度对应 Skill、多步协同完成（例如"把这批 50 个 SKU 生成 A+ 图并写英文五点"）。
- 特点：深度集成 Skills，复杂任务自动协同；对不熟悉工作流的用户更友好。
- 可平替性：低——编排智能本身不在开源生态里，需自建一个轻量 orchestrator（本项目的 A2A + AG-UI frame 已具备雏形）。

### 1.3 LinkFox Agent（`linkfoxagent` skill）

- 入口：ClawHub 上的 `linkfoxagent` skill，工具数 **67–79 个**（版本间浮动）。
- API：`agent-api.linkfox.com`，需 `LINKFOXAGENT_API_KEY`。
- 能力：以工具/函数形式暴露给 Agent 调用，适合做"程序化、可批量的电商操作"。
- 可平替性：中等——开源侧可逐个用脚本/API 替代（如用平台开放 API + 自写 skill 替代其 Agent 工具），但不如官方一键集成顺滑。

### 1.4 能力边界与可独立调用性（一张表）

| 能力形态 | 是否可独立调用 | 调用方式 | 自建平替难度 |
| --- | --- | --- | --- |
| 开放 skill（选品/数据/营销） | 是 | `npx skills add` + API key | 低（直接复用） |
| Claw 编排 | 否（绑定官方产品） | 官方对话界面 | 高（需自建 orchestrator） |
| Agent 工具（67–79） | 是 | Agent API | 中（逐个脚本替代） |
| 商品图生成 | 是 | 官网/API | 中（开源图像栈替代） |

## 2. 工作流 / 自动化能力

LinkFox 在"作图"之外的自动化卖点：

- **逐步执行 / 自动执行 / 定时任务**：可把多步流程保存为模板，定时或触发式运行（如每日抓取竞品价格并生成周报）。
- **浏览器插件**：在 Amazon / Shopify 后台侧边直接唤起能力，所见即所得。
- **伴随式服务（Copilot 形态）**：在选品/上架过程中实时给建议。
- **企业私有模板与资产安全**：团队可沉淀私有工作流模板，素材/品牌资产隔离。
- 可平替项：定时任务可用系统 cron / 本项目 automation 框架；浏览器插件可用 DrissionPage/Playwright 自写；私有模板=本仓库 SOP + Base 沉淀。

## 3. 自建更小系统的可行方案

### 3.1 开源图像生成栈（引擎 + 模型）

推荐以 **ComfyUI** 为引擎（节点工作流 + 自带 REST API，便于批量/服务化），配套：

- **SDXL**（8GB 显存友好，本机 RTX 4060 可跑，单张约 5–15s）。
- **IP-Adapter**：锁定品牌风格/主体一致性（多图同风格的关键）。
- **ControlNet（OpenPose / Canny）**：锁定姿态与构图，避免"换背景连姿势也变"。
- **OOTDiffusion 或 InstantID / PuLID**：AI 模特上身、换脸锁人，替代 LinkFox 的"AI 模特/服装上身"。
- **Fooocus / Z-Image**：中文开箱即用的前台界面，降低人工操作门槛。
- 提速可选 **SDXL-Turbo / LCM**；语义增强可选 **Qwen-Image**。

技术原理反推（与 LinkFox 同类）：SAM 分割 + ControlNet 锁姿态 + 局部重绘（不变形关键）+ IP-Adapter/PuLID 锁脸 + OOTDiffusion 虚拟试穿。

### 3.2 非图像部分：直接复用 LinkFox 开放 skill

图像自己做，选品/数据/营销/合规等**直接 `npx skills add linkfox-ai/linkfox-skills`** 接入 Cursor / Claude Code / 本项目 agent，省去自建。即"图像栈自建 + 数据栈白嫖官方 skill"的混合架构，性价比最高。

### 3.3 成本对比（关键省钱逻辑）

| 方案 | 单张成本 | 备注 |
| --- | --- | --- |
| LinkFox 基础版 | ≈¥0.158/张 | ¥662.4/年 ≈ 4,200 张算力 |
| LinkFox 高级/团队版 | ≈¥0.11–0.114/张 | 量大更便宜 |
| LinkFox 免费版 | ≈¥0.04/张（折算） | 250 点 ≈ 25 张 |
| LinkFox 实测（用户口径） | ≈¥1/张 | 含高级功能/损耗 |
| 自建（本机 RTX 4060） | ≈¥0 边际 | 一次性硬件；单张电费可忽略；5–15s/张 |
| 自建（升级 RTX 4090 24GB） | ≈¥0 边际 | ≈1.8s/张，吞吐更高 |
| 自建（云 GPU API） | 按张计费 | 无硬件投入，适合峰值 |

**回本点**：以 RTX 4060 级别二手卡/现有机器为例，硬件边际≈0，只要月产图量超过几十张，相对 LinkFox 付费即当月回本；若用云 GPU，则需对比云计费与 LinkFox 单价。

### 3.4 MVP 最小可行架构与分阶段落地

- **P0 基础设施**：部署 ComfyUI + SDXL，打通 REST API，能单张出图（本机已具备 GPU）。
- **P1 一致性**：接入 IP-Adapter + ControlNet，做"商品套图"（A+/卖点/场景/特写）批量生成。
- **P2 AI 模特**：OOTDiffusion / PuLID 实现服装上身、换脸锁人。
- **P3 数据栈白嫖**：`npx skills add linkfox-ai/linkfox-skills` 接入选品/数据/营销 skill，与图像栈通过本项目 A2A 编排串联。
- **P4 编排与自动化**：用本项目 A2A + AG-UI orchestrator 做"对话即执行"的轻量 Claw，定时任务接入 automation 框架。

## 4. 风险与注意

- **模型版权 / 商用合规**：开源模型（SDXL 等）多允许商用，但部分 LoRA/模型有条款限制；生成图用于销售前需确认训练数据合规、避免复刻版权图。
- **品牌一致性调参成本**：IP-Adapter/ControlNet 需针对本店风格调参，前期有调参工时；LinkFox 已内置调好的风格。
- **能力差距**：LinkFox 的 118→190 个 skill 背后是**数据源壁垒**（电商平台的实时选品/竞品/广告数据），这部分开源生态无法完全复制，需依赖官方 skill 或自接平台 API。
- **运维成本**：自建需维护 ComfyUI/模型/显存，故障需自己排查；LinkFox 是 SaaS 免运维。

## 5. 与另两份报告的衔接

- **Codex《商品图功能调研》**：聚焦"LinkFox 能生成什么图、怎么用、定价值不值"，本报告在其之上补"如何自己造"。
- **CEO《总控视角》**：给出三方取舍与待 Boss 拍板项；本报告是其中的"自建技术可行性"详版。
- 结论一致：**图像栈自建 + 数据 skill 白嫖**是性价比最优解，建议按 P0–P4 推进，GPU 已就位（RTX 4060 8GB）。
