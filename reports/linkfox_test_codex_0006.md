# LinkFox 套图生图测试：SKU 0006

状态：**BLOCKED**。派单要求的 4 张 PNG 未能全部生成：`nano_banana` 成功生成 2 张，而 `gpt-image-2` 的白底主图和生活场景在首次调用及一次重试中均由 toapis 返回 `generation_failed`（任务处理失败）。没有将失败任务伪报为成功，也没有执行完成回报、提交或推送。

## 测试目标与 SKU 信息

- SKU 别名：`0006`（`data/shop.db` 的 `products` 表按 `rowid` 排序第 6 条）
- 真实 `sku_id`：`1729650408492735410`
- 标题：`12pcs Creative Wall Stickers, Bohemian Style, Wave Wall Decals, Wavy Lines Waterproof Self-Adhesive Removable Living Room, Kitchen, Bedroom Stickers, Furniture Renovation Wall Decals, Good Home Choice for Entrance, Porch, Living Room, Bedroom, Bathroom, O`
- variant：`Pink`
- 价格：`8.8 GBP`
- 来源：TikTok 商品记录的 `image_url`（已记录在 `outputs/linkfox_test/0006/result.json`）

## 调用流程与样式锁定

实际运行：

```text
python tmp_agent_gen.py --sku 0006 --models gpt-image-2,nano_banana --types white_bg_hero,lifestyle_scene --n 1
```

并对失败的 `gpt-image-2` 两个类型各重试一次。harness 从 `config/toapis.local.json` 读取 API key，并按完整链路调用 toapis：`POST /v1/images/generations` 取得 task_id，`GET /v1/images/generations/{task_id}` 轮询至完成或失败，成功后用返回 URL 下载 PNG 至本地。

每个 prompt 均含 `CRITICAL STYLE-LOCK`：要求产品设计、图案、形状、颜色、材质与视觉风格完全一致，只允许变化背景、场景和光影；明确禁止重设计、再风格化或改变产品外观。该约束由 harness 在两种图类、两种模型的每次请求中注入。

实际使用的 prompt（同一图类对两个模型相同；GPT 重试仍使用相同 prompt）：

- `white_bg_hero`：`Product title: 12pcs Creative Wall Stickers, Bohemian Style, Wave Wall Decals, Wavy Lines Waterproof Self-Adhesive Removable Living Room, Kitchen, Bedroom Stickers, Furniture Renovation Wall Decals, Good Home Choice for Entrance, Porch, Living Room, Bedroom, Bathroom, O. Variant: Pink. Professional e-commerce product hero shot on pure white background, the product centered and filling frame, soft studio lighting, sharp detail, clean commercial photography. CRITICAL STYLE-LOCK: You must reproduce the EXACT product as described by the title with identical design, pattern, shape, color scheme, material, and visual style. Do NOT redesign, restyle, reinterpret, or alter the product's appearance in any way. You may only change the background / scene / lighting context; the product ITSELF must remain visually unchanged and recognizable.`
- `lifestyle_scene`：`Product title: 12pcs Creative Wall Stickers, Bohemian Style, Wave Wall Decals, Wavy Lines Waterproof Self-Adhesive Removable Living Room, Kitchen, Bedroom Stickers, Furniture Renovation Wall Decals, Good Home Choice for Entrance, Porch, Living Room, Bedroom, Bathroom, O. Variant: Pink. The product placed in a realistic lifestyle scene, warm natural home environment showing contextual usage, cinematic soft lighting, 8k product photography. CRITICAL STYLE-LOCK: You must reproduce the EXACT product as described by the title with identical design, pattern, shape, color scheme, material, and visual style. Do NOT redesign, restyle, reinterpret, or alter the product's appearance in any way. You may only change the background / scene / lighting context; the product ITSELF must remain visually unchanged and recognizable.`

任务 ID、耗时和落盘路径均以真实值汇总在 `outputs/linkfox_test/0006/result.json`。

## 逐项结果

| 模型 | 图类 | task_id | 耗时 | credits_used | 落盘路径 / 结果 |
|---|---|---|---:|---:|---|
| gpt-image-2 | white_bg_hero | `tsk_img_01KY4AK24Y8XXX7HSKJFDP8005` | 13.9s | 不可得 | 首次失败：`generation_failed` |
| gpt-image-2 | lifestyle_scene | `tsk_img_01KY4AKFVC2TNJ0VVVDF71X28E` | 8.5s | 不可得 | 首次失败：`generation_failed` |
| nano_banana | white_bg_hero | `tsk_img_01KY4ANRB2CDKV9970D84X42GG` | 15.5s | 不可得 | `outputs/linkfox_test/0006/nano_banana__white_bg_hero__1.png` |
| nano_banana | lifestyle_scene | `tsk_img_01KY4APAPNNFBQVZXQ0NKDY503` | 15.7s | 不可得 | `outputs/linkfox_test/0006/nano_banana__lifestyle_scene__1.png` |
| gpt-image-2（重试） | white_bg_hero | `tsk_img_01KY4ARF500MM3ARKJYEZ5JV0J` | 11.7s | 0.0 | 失败：`generation_failed` |
| gpt-image-2（重试） | lifestyle_scene | `tsk_img_01KY4ARSK1375QDZPDN98QAANN` | 15.9s | 0.0 | 失败：`generation_failed` |

完整 prompt 见 `result.json` 的各个结果项。成功 PNG 已确认存在且非空；当前目录仅有 2 张 PNG，而非派单要求的 4 张。

## 积分统计

`credits_per_usd=200`。首轮的 `/v1/user/balance` 起始和中段读取均受限流而不可用，结束读数为 87.0，因此两张成功的 `nano_banana` 图无法由差额反推出每张积分；总积分和美元折算也均不可得。重试前后读数均为 87.0，两个失败的 GPT 任务可确认消耗为 0.0 credits。没有以猜测值补填积分。

## 质量观察

- 白底图：主体居中、背景干净、细节清晰，具备基础白底主图要素；但模型额外生成了产品标题文字，不符合无文字的纯商品主图预期。
- 场景图：有自然室内光影和真实家居场景感；但主画面只展示约 3 条墙贴，且附带拼贴说明框，无法确认“12pcs”及原商品构成完全一致。
- 样式一致性：两张成功图保留了粉色波浪墙贴这一大类外观，但仅凭生成结果不能证明与 TikTok 原图逐像素或逐图案一致。尤其生活场景图的数量/展示形式已偏离商品描述，因此不能给出“产品样式完全未改写”的通过结论。

## 问题与结论

阻塞原因是 toapis 网关的 `gpt-image-2` 任务处理失败，重试后仍复现。当前只产出 2/4 张 PNG，且成功样本还存在文字注入和产品构成一致性风险。结论：本轮 SKU0006 生图测试未通过，需网关恢复 `gpt-image-2` 后重新运行完整矩阵，并以可读取的首尾余额快照重新核算积分。
