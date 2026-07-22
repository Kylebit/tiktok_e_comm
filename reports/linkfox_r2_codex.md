# LinkFox 场景的开源 / 自托管替代调研

调研日期：2026-07-22。范围是 GitHub 上公开可取得的代码、Skill 与 MCP 项目；`★` 为本次查看 GitHub 页面时的约数，随时间变化。这里的「本地可跑」只表示软件可在自有机器/容器运行，**不**等同于可以绕过 1688 的登录、授权、风控、接口条款或模型权利限制。

## 结论摘要

- 图像工作流可以自托管：以 ComfyUI 为编排底座，搭配本地开源权重/节点，覆盖图生图、换背景、局部重绘和部分试穿；不必调用付费图像 API。
- 1688 的关键词、图搜、店铺/供应商商品查询有可复用的开源 Skill，但可靠的路径是 **1688 开放平台凭据**。它不是零成本 API；“完全免授权、稳定下单”的成熟开源替代目前没有。
- 采购下单是最不能直接自动化的环节：公开的 Playwright/MCP 能驱动已登录浏览器，但无法替代账户授权、风控、人机验证与最终付款确认。建议只做“候选收集 → 人审 → 人工确认下单”。
- Skill 生态已有开放的 `SKILL.md`/MCP 载体及目录，但电商/1688 垂直 Skill 的供给远少于开发工具类；应把经过审计的 1688 Skill fork 到私有仓库维护。

## 1. 1688 货源、检索与采购

| 项目 | 一句话功能与对应 LinkFox 功能 | 协议；成熟度（本次核验） | 是否真能本地跑 / 关键限制 |
|---|---|---|---|
| [openclaw/skills：1688-product-search](https://github.com/openclaw/skills/tree/main/skills/1688aiinfra/1688-product-search) | `SKILL.md` + Python 脚本封装类目、关键词、图片、详情、相关商品、店铺商品等 1688 跨境开放接口；对应“1688 搜索、以图搜图、供应商/同店商品查询”。 | 仓库 MIT，约 ★4.5k；归档的是 clawhub 技能版本，仓库明确提示可能含恶意/可疑 Skill，故应逐文件审计后 fork。该 Skill 文件标注 v1.0.3。 | **能本地运行**，但必须提供 `ALI1688_APP_KEY`、`APP_SECRET` 和 refresh/access token；图片上传对非 1688 图片成功率有限。最贴近需求的首选。 |
| [netkaruma/search1688api](https://github.com/netkaruma/search1688api) | Python 库，通过文字和图片在 1688/Alibaba 搜索商品；对应“找货 / 图搜同款”。 | GitHub 1688 topic 的公开项目，最近更新显示为 2026-03；应在接入前再次查看 LICENSE（本次检索摘要未能确认 SPDX）。 | **代码可本地跑**，但其搜索 API/会话依赖需自行验证；未确认许可前不建议直接商用嵌入。 |
| [ihmily/1688-Decryptor](https://github.com/ihmily/1688-Decryptor) | 研究 1688/淘宝请求签名参数；仅可作为理解旧接口的参考。 | Python，topic 页显示最近更新 2025-04；许可、可用性需逐仓核实。 | 可本地研究，**不推荐用于生产**：逆向接口易失效且可能违反平台规则，不能作为官方 API 替代。 |
| [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | 给 MCP Agent 提供浏览器导航、点击、填表等能力；对应“已登录 1688 的人工辅助询价、加购、生成待下单单”。 | Apache-2.0；约 ★35.4k、565 commits，维护活跃。 | **能本地/容器跑**。它只是浏览器控制层；需用户合法登录，遇验证码/支付/风控必须转人工，禁止把它表述为稳定无头下单器。 |
| [Oxylabs/1688-scraper](https://github.com/oxylabs/1688-scraper) | 1688 商品数据抓取 API 示例；对应“商品搜索/详情采集”。 | GitHub topic 当前列出，最近更新显示 2026-04；服务本身是商业抓取 API。 | 示例可跑，但结果依赖付费 Oxylabs，**不符合尽量不依赖付费 API 的主方案**，故仅作对比，不推荐。 |

### 1688 的可行集成边界

`1688-product-search` 的说明明确列出了官方 `keywordQuery`、`imageQuery`、`queryProductDetail`、`querySellerOfferList` 等端点和环境变量；这证实“搜索/图搜/供应商商品查询”可被 Agent Skill 标准化，但也证实它依赖官方凭据，而不是免费匿名抓取。建议实现为：

1. 将 Skill 的最小脚本及测试输入 fork 到本项目/私有仓库，Secrets 只放运行环境；
2. 仅把 SKU、商品链接、offerId、店铺 ID、价格和证据图传入审批流；
3. 让 Playwright 打开已登录页面供人工复核，采购、地址、付款及最终提交必须由人确认；
4. 记录请求时间、账号和原始响应，限流并遵守 1688 开放平台及商家页面条款。

**明确缺口：暂无成熟、合规、可自托管且不需要账户/API 授权的 1688 自动采购下单替代。** 原因是下单天然涉及身份、价格、库存、收货与支付，公开爬虫只能采集公开页，不能合法地取代授权交易接口或账户确认。

## 2. 图像生成、商品图编辑与虚拟试穿

| 项目 | 一句话功能与对应 LinkFox 功能 | 协议；成熟度（本次核验） | 是否真能本地跑 / 限制 |
|---|---|---|---|
| [Comfy-Org/ComfyUI](https://github.com/comfy-org/ComfyUI) | 节点图/队列/API 编排扩散模型、图生图、ControlNet、inpaint、放大；是商品图生产流水线底座。 | GPL-3.0；约 ★122k、5,619 commits；README 写明周度发布节奏，持续维护。 | **是**。Windows portable、Linux/macOS 与 CPU 模式均有说明；本地核心可离线，模型权重另行下载且应逐个审许可证。 |
| [lllyasviel/Fooocus](https://github.com/lllyasviel/Fooocus) | 面向少参数操作的 SDXL 图生图、变体、inpaint/outpaint、图像提示；适合手工商品图改图。 | GPL-3.0；约 ★49.1k；官方状态为“Limited LTS（仅 bug fix）”，最新 release 为 2024-08。 | **是**，Windows/Linux 可启动，官方最低 4GB NVIDIA VRAM（不同环境差异大）。易用但不宜作为持续演进的生产核心。 |
| [AUTOMATIC1111/stable-diffusion-webui](https://github.com/AUTOMATIC1111/stable-diffusion-webui) | Stable Diffusion WebUI 与扩展生态，支持 img2img、inpaint、ControlNet；对应商品图生成与局部重绘。 | AGPL-3.0；约 ★164k、7,689 commits，社区很大。 | **是**，但扩展/模型兼容性和安全性要单独锁版本；AGPL 对将修改后服务化有义务，需法务评估。 |
| [danielgatis/rembg](https://github.com/danielgatis/rembg) | 本地 CLI/HTTP/Docker 的 AI 去背景；对应“白底商品图、换背景前的抠图”。 | MIT；约 ★23.9k、536 commits。 | **是**，CPU/GPU、Docker 都可；输出质量取决于所选模型，建议以商品类测试集验收。 |
| [Zheng-Chong/CatVTON](https://github.com/Zheng-Chong/CatVTON) | ICLR 2025 的轻量虚拟试穿扩散模型；对应服饰“模特试穿”。 | 仓库含 LICENSE（接入前需按当前文件核定）；约 ★1.8k、61 commits。 | **是**，README 标称 1024×768 推理少于 8GB VRAM；属于研究实现，需评估人像/服饰品类偏差和权重许可。 |
| [yisol/IDM-VTON](https://github.com/yisol/IDM-VTON) | ECCV 2024 的高保真虚拟试穿官方实现；对应服饰试穿。 | 研究项目，仓库含 `LICENSE.txt`；约 ★5.1k、28 commits，提交量较小。 | **可本地推理**（含 Gradio demo/脚本），但须先核对代码与预训练权重的非商用条款；不能未审许可就用于店铺商业生成。 |
| [kijai/ComfyUI-IC-Light](https://github.com/kijai/ComfyUI-IC-Light) | 将 IC-Light 接入 ComfyUI，按参考图控制光照；对应“商品换背景后统一棚拍光感”。 | Apache-2.0；约 ★1.2k、98 commits，README 提供工作流和安装说明。上游 IC-Light 权重仍须另核许可。 | **能本地跑**（ComfyUI 节点）；这不是单独的生成平台，依赖 ComfyUI、显卡和模型，适合做工作流插件。 |

### 推荐的本地商品图工作流

`rembg（抠图） → ComfyUI（背景/光照/局部重绘/放大） → 人审 → 导出规格图`。服饰再分支到 CatVTON 或 IDM-VTON。ComfyUI 的 README 明确支持 inpainting、ControlNet、放大和 API，且本地核心可完全离线；因此它比把 Fooocus/A1111 当作无人值守服务更适合接入现有控制台。

注意：开源代码许可证不自动授予模型、LoRA、人物肖像、商标或素材的商用权。对于 IDM-VTON/CatVTON，应把“代码可运行”和“可商用交付”分开验收。

## 3. Agent Skills / MCP 生态与可复用入口

| 仓库 | 一句话功能与对应 LinkFox 功能 | 协议；成熟度 | 本地可用性与取舍 |
|---|---|---|---|
| [openclaw/skills](https://github.com/openclaw/skills) | Skill 存档，包含前述 1688 商品搜索 Skill；对应“可安装的货源 Agent 能力”。 | MIT，约 ★4.5k、196k+ commits 的归档；项目明确是 archive，且提示可能有恶意内容。 | 可复制 Skill 到本地运行，但**不可盲装**：审计 `SKILL.md`、脚本、依赖和网络目标后 fork。 |
| [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) | 跨 Claude Code、Codex、Cursor 等的精选 Skill 目录，可发现图像、MCP、网页自动化能力。 | 约 ★（页面动态）；有持续 PR，定位为人工精选目录。许可证应以仓库当前 LICENSE 为准。 | 可本地 clone/按目标 Agent 路径安装；是“发现索引”，不是 1688 成品业务 Skill。 |
| [junminhong/awesome-agent-skills](https://github.com/junminhong/awesome-agent-skills) | 含 Business & Marketing、Creative & Media 分类的技能/资源目录；对应“从通用 Skill 中组合电商图文流程”。 | 公开维护的 curated list；许可证与动态数据应以仓库页为准。 | 可本地使用条目，不提供平台交易权限；适合寻找并改造而不是直接上生产。 |
| [skillcreatorai/Awesome-Agent-Skills](https://github.com/skillcreatorai/Awesome-Agent-Skills) | 提供统一 CLI/目录，覆盖 Cursor、Claude、VS Code 等 Skill 安装位置；对应“把内部电商 Skill 分发给多个 Agent”。 | 公开仓库；含可安装的 `SKILL.md` 结构。许可证、star 与更新应在锁定版本时复核。 | **能本地使用**；推荐只把它作为格式与分发参考，不把未审第三方 skill 直接接触账号/采购数据。 |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | MCP 参考服务器集合；对应“为内部货源/图像服务包一层标准工具接口”。 | MIT；官方示例/参考实现，部分服务器可能标为 archive 或实验性质。 | **能本地跑**，适合作为自建 `1688-sourcing-mcp` 的协议参考，不提供 1688 商务能力本身。 |

### 生态判断

有现成的“图像处理”“浏览器自动化”“Skill 安装/发现”能力，也有一个直接的 1688 搜索 Skill；但没有发现一个同时满足 **1688 免授权采购 + 商品图生成 + 商业可用试穿 + 订单自动支付** 的成熟开源总成。把这些能力拆为可审计的 MCP/Skill，比引入来源不明的全能 agent 更稳妥。

## LinkFox 功能 → 开源替代映射

| LinkFox 目标功能 | 首选开源替代 | 接入方式 | 是否可无付费 API | 生产建议 |
|---|---|---|---|---|
| 1688 关键词搜索、详情 | `openclaw/skills` 的 `1688-product-search` | 审计后 fork 为本地 Skill/MCP | 软件可；**官方 1688 凭据不可省** | 推荐，走官方授权 |
| 以图搜图 | 同一 1688 Skill 的 `imageQuery` | 上传/传入合规图源 URL | 同上，外部图成功率不保证 | 推荐，失败降级为关键词 |
| 供应商/同店商品查询 | 同一 Skill 的 `querySellerOfferList` | 从商品详情取 seller 标识 | 同上 | 推荐，保留原始链接与证据 |
| 采购加购/下单 | Playwright MCP + 已登录浏览器 | Agent 辅助填写，人工完成确认与支付 | 可不付 API，但需合法账户 | 仅半自动；没有成熟合规的全自动替代 |
| 商品图图生图、换背景、局部重绘 | ComfyUI + 本地模型；Fooocus/A1111 作备选 UI | HTTP/队列/工作流 JSON | **可以** | 首选 ComfyUI，锁定工作流与模型版本 |
| 白底抠图 | rembg | CLI/Docker/HTTP | **可以** | 先建立品类验收样本 |
| 虚拟试穿 | CatVTON / IDM-VTON | 本地 GPU 推理 | **可以运行**，但要核模型商用许可 | 研究/灰度，不可未经许可直接商用 |
| 让多个 Agent 复用能力 | SKILL.md + MCP；awesome 目录作发现源 | 内部 Git + 审计 + 最小权限 | **可以** | 私有 fork、Secrets 隔离、人工审批 |

## 核验方法与引用

本次通过 GitHub 仓库页、GitHub 1688 topic 和公开 README/Skill 文件核验；环境中 `gh` 不可用、对 `api.github.com` 的 PowerShell TLS 请求被关闭，因此没有把无法实时核对的精确 star/更新时间伪造成数据。报告中有数值的项目均以本次 GitHub 页面所示为准；对未能确认的许可证明确标注“接入前核实”。

- [ComfyUI 仓库页](https://github.com/Comfy-Org/ComfyUI)：GPL-3.0、约 ★122k、支持本地/离线核心、inpaint/ControlNet/API。
- [Fooocus 仓库页](https://github.com/lllyasviel/Fooocus)：GPL-3.0、约 ★49.1k、LTS bug-fix 状态及本地运行要求。
- [A1111 仓库页](https://github.com/AUTOMATIC1111/stable-diffusion-webui)：约 ★164k、AGPL-3.0。
- [rembg 仓库页](https://github.com/danielgatis/rembg)：约 ★23.9k、MIT。
- [CatVTON 仓库页](https://github.com/Zheng-Chong/CatVTON)：约 ★1.8k、论文/推理资源。
- [IDM-VTON 仓库页](https://github.com/yisol/IDM-VTON)：约 ★5.1k、官方论文实现。
- [OpenClaw 1688 Skill](https://github.com/openclaw/skills/blob/main/skills/1688aiinfra/1688-product-search/SKILL.md)：环境变量、官方端点及图片搜索限制。
- [OpenClaw Skills 仓库页](https://github.com/openclaw/skills)：MIT、约 ★4.5k，并有 archive/安全提示。
- [Playwright MCP 仓库页](https://github.com/microsoft/playwright-mcp)：约 ★35.4k，可自托管浏览器自动化。
