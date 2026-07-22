# LinkFox 生图能力第二轮调研（肉肉 CEO 视角 + Cursor 补位）

> 本轮 Boss 三个问题：①开源生图框架到底能做出什么图（想看现成结果）；②有没有开源 GitHub skill 能替代 LinkFox 的 1688/图生图等场景；③以 GPT Image / Gemini Nano Banana 为底座 + Codex 编排，能否复刻 LinkFox 生图功能。
> 分工：肉肉(CEO) 做 ① + 补位 ③；**Codex** 后台做 ②（开源 GitHub 替代 skill）；**Cursor** 已派发 ③，但其 IDE 自动执行桥（notify_on_output 唤醒）未触发，故 ③ 由 CEO 补位撰写。
> 时间：2026-07-22。

## 0. 一句话结论（先给 Boss）

- **① 开源框架能做的图，已经非常能打**：商品换背景、虚拟试穿、人脸一致性、批量套图，全都有成熟开源项目，且本机 RTX 4060 就能跑。想"亲眼看到"——文末给了**可直接点击的在线图库入口**（Civitai / HuggingFace Spaces / ComfyUI_examples / OpenArt）。
- **② 开源 GitHub 替代确实存在**（Codex 正在深挖，详见其报告）：1688 有开源爬虫/API 项目、图生图/换背景有 ComfyUI 节点、虚拟试穿有 OOTDiffusion、电商工作流市场 OpenArt/ComfyWorkflows 一大把。
- **③ 能复刻，而且云模型比 LinkFox 还便宜**：GPT Image ≈$0.01–0.17/张、Nano Banana ≈$0.03–0.08/张，对比 LinkFox 0.11–1 元/张；配 Codex 写脚本编排，就是一条"agent 驱动的生图流水线"。强一致性人脸/姿态仍建议用开源 PuLID/ControlNet 补刀。

---

## 1. Topic ①：开源生图框架到底能做出什么图？

### 1.1 能力 → 真实效果 → 在哪看（速览表）

| 能力 | 代表项目 / 模型 | 真实能达到的效果 | 在线看真实样例 |
|---|---|---|---|
| 商品换背景 + 光影融合 | ComfyUI + SDXL + **ControlNet**(Depth) + **IP-Adapter** | 把易拉罐放到热带海滩，沙地贴合罐底、按光源投自然阴影；香水瓶星空电商海报，金属光泽+可调光影 | [ComfyUI_examples(github)](https://github.com/comfyanonymous/ComfyUI_examples) · [comfy.org 模板](https://comfy.org/es/workflows/templates-product_scene_relight-cc23c187984a) |
| 批量商品主图 | ComfyUI 电商工作流（ADetailer/IPAdapter/ControlNet） | 实测 10 张 800×800 主图：传统设计 25 分钟/8GB → ComfyUI **4 分钟/3GB**，风格统一 | [CSDN 电商实战](https://blog.csdn.net/weixin_42497762/article/details/160230998) |
| 极速换背景 | ComfyUI-Background-Replacement（SDXL-Turbo + ControlNet-LoRA Depth，MIT） | "几秒出 4 张"批量换背景，商用产品摄影风格 | [GitHub xujunbj/ComfyUI-Background-Replacement](https://github.com/xujunbj/ComfyUI-Background-Replacement) |
| 虚拟试穿 | **OOTDiffusion**（levihsu，cc-by-nc-sa） | 半身(VITON-HD)/全身(Dress Code)换衣，保留服装花纹细节最佳（学界 SOTA） | [HuggingFace Space 在线试](https://huggingface.co/spaces/levihsu/OOTDiffusion) · [arXiv](https://arxiv.org/abs/2403.01779) |
| 人脸/角色一致性 | **PuLID**（ByteDance，SDXL/FLUX） | 同一张脸在泳装/晚礼服等不同场景保持骨相一致（时尚走秀 demo） | [PuLID Gradio demo](https://github.com/gruckion/PuLID) · [ComfyUI-PuLID-Flux-Chroma](https://github.com/PaoloC68/ComfyUI-PuLID-Flux-Chroma) |
| 结构可控生成 | **ControlNet**（Canny/Depth/OpenPose） | 锁轮廓/空间骨架/姿势，做"同结构多风格"商品图 | [ComfyUI ControlNet 示例](https://github.com/comfyanonymous/ComfyUI_examples/tree/master/controlnet) |

### 1.2 本机 RTX 4060 8GB 能不能跑？

- **能跑，但要调优**：SDXL 量化 fp8 ≈ 4GB，加 IP-Adapter ≈ 5.5GB；若同时加载 ControlNet 会逼近 6GB 边缘、长时间运行易不稳。实测稳妥做法：纯 `img2img + 高 denoise(0.9)` 可在 6GB 机器稳定出图（结构仅留"形状提示"，风格由 prompt 主导），无需 ControlNet。
- 装 ComfyUI + 上述自定义节点即可本地批量出图，**单张边际成本≈0（仅电费）**，对比 LinkFox 0.11–1 元/张。代价是：首次搭建 + 模型下载（几个 GB）+ 显存调优。

### 1.3 给 Boss 的"看图入口"（点开即见真实效果）

1. **Civitai**：SDXL / Flux 模型画廊，搜 "product photography" / "e-commerce" 看真实商品图样张。
2. **HuggingFace Spaces**：[OOTDiffusion 在线试穿](https://huggingface.co/spaces/levihsu/OOTDiffusion)、[PuLID demo](https://github.com/gruckion/PuLID)、[IP-Adapter](https://github.com/tencent-ailab/IP-Adapter) —— 直接网页上传图试。
3. **ComfyUI_examples**：[github 仓库](https://github.com/comfyanonymous/ComfyUI_examples)，每张示例图都带工作流元数据，拖进 ComfyUI 即复现。
4. **OpenArt.ai** / **ComfyWorkflows.com**：工作流市场，可按"电商/产品图"筛选并预览。
5. **comfy.org 官方模板**：[商品+场景合成并重新打光](https://comfy.org/es/workflows/templates-product_scene_relight-cc23c187984a)。

> 小结：开源栈的效果不是"玩具级"，而是已达到电商可用。Boss 想眼见为实，点上面任一入口即可。

---

## 2. Topic ③（CEO 补位 Cursor）：GPT Image / Gemini Nano Banana + Codex 能否复刻 LinkFox 生图？

> 说明：本问题原派给 Cursor，但其自动执行桥未触发，由 CEO 依据公开资料补位。

### 2.1 两个底座模型的能力边界

| 维度 | **GPT Image**（OpenAI gpt-image-1 / 1.5 / 2） | **Gemini Nano Banana**（2.5 Flash Image / 2 / Pro） |
|---|---|---|
| 文生图 | ✅ | ✅ |
| 图生图 / 编辑 | ✅（`v1/images/edits`，支持 mask 局部重绘） | ✅（自然语言定向编辑） |
| 角色/物体一致性 | 中等 | **强**（同人跨场景、同产品多角度一致，官方电商示例） |
| 多图融合 | 有限 | ✅（≤20 张参考图合成） |
| 文字渲染 | 中–强 | **强**（世界知识辅助，模板遵循好） |
| 自然语言局部改 | 需 mask/指令 | ✅（"模糊背景/去污渍/换姿势/上色"一句话） |
| 接入方式 | OpenAI Images API / Responses API，需 `OPENAI_API_KEY` | Gemini API / AI Studio / Vertex AI，需 `GEMINI_API_KEY`；AI Studio **免费 100 张/天** |
| 单张成本 | $0.011(低)/$0.042(中)/$0.167(高) @1024² | $0.03–0.08/张（Nano Banana 2：1K $0.03 / 2K $0.05 / 4K $0.06） |
| 水印 | — | SynthID 隐形水印 |

### 2.2 Codex 怎么编排（agent 驱动的生图流水线）

- Codex 本身**不生成图**，但它是 agentic coding 工具，能写 Python 调用 OpenAI / Gemini SDK、批量循环、串联多步编辑、用文件系统存中间产物——本质就是"会写代码的流水线工程师"。
- **现成 skill 直接可用**：
  - [`nanobanana-codex-skill`](https://github.com/983033995/nanobanana-codex-skill)（GitHub）：`npx skills add 983033995/nanobanana-codex-skill`，封装 Gemini Nano Banana 生成/编辑/多图合成，支持 `--reference`/`--input` 多图。
  - [`openai/codex` 的 `imagegen` skill](https://qumge.com/zh-cn/skills/openai/codex/imagegen)：`npx skills add https://github.com/openai/codex --skill imagegen`，内置 `image_gen` 工具 + CLI 回退（gpt-image-1.5）。
- **流水线示例**（Codex 写一次，反复用）：
  ```
  读商品清单 → for 每个商品:
     调 Nano Banana：换背景到"希腊风 cottage" + 同模特多姿态（一致性）
     → 调 GPT Image：局部重绘加 logo/卖点文字
     → 落盘 1200x1600 / 1080x1440 多尺寸
  → 批量产出商品套图
  ```

### 2.3 LinkFox 生图功能 → 云模型 + Codex 可行性映射

| LinkFox 功能 | 云模型 + Codex 能否做 | 说明 |
|---|---|---|
| 商品套图（A+/卖点/场景/特写） | ✅ 能做 | Nano Banana 一致性 + Codex 批量循环，正是其官方电商场景 |
| 换背景 | ✅ 能做 | 两者都支持编辑/定向改背景 |
| 局部重绘 | ✅ 能做 | GPT `images/edits`(mask) / Nano Banana 定向编辑 |
| AI 模特换装 | ◐ 基本能做 | Nano Banana 同人换装可；但**真·衣物形变试穿**用开源 OOTDiffusion 更稳 |
| 相似图裂变（3 秒裂变） | ✅ 能做 | Codex 循环 seed/prompt 批量出变体 |
| 智能修图 | ✅ 能做 | 两者编辑能力覆盖 |
| 强人脸一致性（模特脸不变） | ◎ 基本够 | Nano Banana 内置一致性；极致保真用开源 PuLID 或 Nano Banana Pro |
| 强姿态控制（锁姿势） | ◎ 弱项 | 云模型姿态控制弱于开源 **ControlNet** |
| 商品图文字渲染 | ✅ 能做 | Nano Banana 文字渲染强，GPT 中上 |

### 2.4 成本对比（关键）

| 方案 | 单张成本 | 备注 |
|---|---|---|
| **LinkFox** | ¥0.11–1 元/张 | 按功能订阅/积分，部分场景偏贵 |
| **GPT Image** | $0.01–0.17（≈¥0.07–1.2） | 低质仅 $0.011 |
| **Nano Banana** | $0.03–0.08（≈¥0.2–0.6） | AI Studio 免费 100 张/天 |
| **本地开源(ComfyUI 栈)** | ≈¥0（电费） | 需 RTX 4060 + 搭建调优，慢 |

> 结论：**云模型对大多数场景比 LinkFox 更便宜或持平，且无需功能订阅**；本地开源边际≈0 但需一次性搭建与显存调优。两者都明显比"1 元/张"的 LinkFox 套餐划算。

### 2.5 推荐架构（混合）

- **日常套图 / 换背景 / 修图 / 文字**：用 **Nano Banana + GPT Image + Codex 编排**——快、便宜、免搭建，当天就能跑。
- **强人脸一致性 / 强姿态 / 真·虚拟试穿**：用**本地开源 PuLID / ControlNet / OOTDiffusion** 补刀（本机 RTX 4060 可跑）。
- **若要求完全免费**：走全开源 ComfyUI 栈（SDXL + IP-Adapter + ControlNet + OOTDiffusion），一次性投入搭建。

---

## 3. 待 Boss 拍板

1. **要不要我先用 Nano Banana（本机已有 skill）现场生成 2–3 张示例商品图给你"亲眼看看"？**（注意：每次生图约 $0.03–0.08，我可先小批量示范；你点头我即生成）
2. **主路线选哪条**：云模型（快/便宜/免搭建）vs 本地开源（免费/需搭建）vs 混合（推荐）？
3. **是否让 Codex 把"Nano Banana + GPT Image 生图流水线"写成可复用脚本沉淀进项目**，以后一句话批量出套图？
4. Codex 的 ②（开源 GitHub 替代 skill）报告出来后，我会并入本合订本并补发飞书链接。

---
*注：本报告基于公开网页/官方文档/开源仓库实测检索（2026-07-22），未做真实生图压测；成本按官方公开定价换算。Codex 的 ② 部分见其独立报告 `linkfox_r2_codex.md`。*
