# LinkFox 商品套图生成测试：SKU 0005

生成时间：2026-07-22；执行命令：`python tmp_agent_gen.py --sku 0005 --models gpt-image-2,nano_banana --types white_bg_hero,lifestyle_scene --n 1`。

## 测试目标与商品

- 别名：`0005`；来源：`data/shop.db` 的 `products` 表第 5 行。
- `sku_id`：`1729650406795090866`；variant：`1pc`；价格：`13.8 GBP`。
- 标题：Contemporary Style 3D Window View Wall Stickers - Faux Window Frame with Floral Design, Self-Adhesive PVC Decals for Living Room and TV Background Decor, Detachable Single-Use Wall Art。
- 源图：TikTok `image_url`（其完整 URL 已写入 `outputs/linkfox_test/0005/result.json`）。

## 硬性样式锁与 API 流程

每个请求都加入了以下 STYLE-LOCK：产品的 design、pattern、shape、color scheme、material 和 visual style 必须完全一致；禁止 redesign/restyle/reinterpret/alter；仅允许改变背景、场景和光影。该约束由 harness 拼入每条 prompt，而非事后人工补充。

调用通过 `config/toapis.local.json` 中的 key 进行：`POST /v1/images/generations` 创建任务、读取返回的 `task_id`、`GET /v1/images/generations/{task_id}` 轮询至 completed、再用返回 URL 下载为 PNG。积分以生成前后 `/v1/user/balance` 的 `used_credits` 差值统计，换算率为 `200 credits/USD`。

## 结果明细

共同 prompt 前缀为：`Product title: [上述标题]. Variant: 1pc.`；共同尾缀为上述 STYLE-LOCK。

| 模型 | 图型与场景 prompt | 耗时 | credits_used | 落盘文件 |
|---|---|---:|---:|---|
| gpt-image-2 | `white_bg_hero`：Professional e-commerce product hero shot on pure white background, product centered/filling frame, soft studio lighting, sharp detail, clean commercial photography. | 未持久化（文件于 14:54:22 落盘） | 两张 GPT 图合计 6.0；单张未持久化，不臆测拆分 | `outputs/linkfox_test/0005/gpt-image-2__white_bg_hero__1.png` |
| gpt-image-2 | `lifestyle_scene`：Product placed in a realistic warm natural home environment showing contextual usage, cinematic soft lighting, 8k product photography. | 未持久化（文件于 14:56:57 落盘） | 同上 | `outputs/linkfox_test/0005/gpt-image-2__lifestyle_scene__1.png` |
| nano_banana | `white_bg_hero`：Professional e-commerce product hero shot on pure white background, product centered/filling frame, soft studio lighting, sharp detail, clean commercial photography. | 14.5 s | 2.4 | `outputs/linkfox_test/0005/nano_banana__white_bg_hero__1.png` |
| nano_banana | `lifestyle_scene`：Product placed in a realistic warm natural home environment showing contextual usage, cinematic soft lighting, 8k product photography. | 13.2 s | 网关余额查询在重试窗口返回空值 | `outputs/linkfox_test/0005/nano_banana__lifestyle_scene__1.png` |

最终 `result.json` 保留了可完整轮询的两个 nano_banana task：`tsk_img_01KY49TZ6547YX5AZ1RGPH1HEB`、`tsk_img_01KY49WAKR4J41Y2QKYW2K0RSR`。首次完整组合运行因本地命令时限中断，两个 GPT 文件已下载但对应 task metadata 没有被 harness 写回；因此报告按实际落盘和余额差值记录，没有伪造 task_id、耗时或单张积分。

## 积分汇总

可确认下限为 **8.4 credits**（GPT 两图余额差 6.0 + nano 白底图 2.4），约 **$0.042**。nano 场景图已成功下载，但余额端点在其统计重试和后续复查均未返回有效数据，故实际总消耗高于该下限、未能精确确定。

## 质量观察与结论

- 白底图主体清晰，构图和工作室光线符合商品主图基本要求；生活场景图的家居环境、光影和陈列感较完整。
- 但四张图均把“窗景墙贴”重绘成可开启的实体窗框／不同花景；nano 白底图还加入了产品文字。这说明生成模型没有可靠保持原商品的图案和材质，STYLE-LOCK 虽已在请求层强制注入，视觉验收仍判定为**未通过样式一致性**。
- 首次请求的 Unicode 破折号触发 toapis `invalid byte sequence for encoding UTF8 (0xa1)`；将该字符替换为 ASCII 连接语后，图片可成功生成。另有余额 API 间歇性空响应，导致一张成功图的单图积分无法读取。

结论：4 张图均已落盘，场景与白底效果可见，但由于产品样式被明显改写，本 SKU 不应将这些图片作为正式商品素材；建议下一轮使用原始商品图作为图生图参考，并在网关稳定返回余额后再做精确成本统计。
