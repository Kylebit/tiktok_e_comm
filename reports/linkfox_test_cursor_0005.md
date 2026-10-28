# LinkFox 商品套图生成测试：SKU 0005

## 测试目标与商品信息

- 别名：`0005`；来源：`data/shop.db` 的 `products` 表第 5 行。
- `sku_id`：`1729650406795090866`；variant：`1pc`；价格：`13.8 GBP`。
- 标题：Contemporary Style 3D Window View Wall Stickers - Faux Window Frame with Floral Design, Self-Adhesive PVC Decals for Living Room and TV Background Decor, Detachable Single-Use Wall Art。
- 源图：该行的 TikTok `image_url`（完整 URL 保存在 `outputs/linkfox_test/0005/result.json`）。

实际执行命令：

```text
python tmp_agent_gen.py --sku 0005 --models gpt-image-2,nano_banana --types white_bg_hero,lifestyle_scene --n 1
```

## 样式锁定与 API 流程

每个 prompt 均由 harness 注入同一段 `CRITICAL STYLE-LOCK`：要求设计、图案、形状、色彩、材质与视觉样式完全相同，禁止 `redesign`、`restyle`、`reinterpret` 或改变产品本身；仅允许调整背景、场景与光影。因此这是请求层的强制约束，而非事后补充。

API key 从 `config/toapis.local.json` 读取，未写入本报告。完整调用链是：`POST /v1/images/generations` 创建任务并取得 `task_id`，随后 `GET /v1/images/generations/{task_id}` 轮询；任务完成后下载返回 URL 到 PNG。积分用 `/v1/user/balance` 的 `used_credits` 前/中/后差值统计；换算率为 200 credits/USD。

## 落盘与结果

目录 `outputs/linkfox_test/0005/` 中有 4 张 PNG 和 `result.json`。其中两张 GPT PNG 是本轮完整 harness 运行前已存在的落盘文件（14:54/14:56）；本轮重跑的两个 GPT 任务均明确返回 `generation_failed`，故不把旧文件伪造为本轮成功任务。`result.json` 保留本轮所有 4 个请求的真实状态与积分。

共同 prompt 前缀为 `Product title: [上述标题]. Variant: 1pc.`，共同尾缀为上述 STYLE-LOCK。中段按图类如下：

| 模型 | 图类 / 场景 prompt | 任务、耗时与积分 | 落盘路径 |
|---|---|---|---|
| gpt-image-2 | `white_bg_hero`: pure white、主体居中、柔和棚拍光、清晰商业摄影 | `tsk_img_01KY4ANRATAR13HP0PDTP226ZN`；10.4 s；1.2 credits；`failed` | `outputs/linkfox_test/0005/gpt-image-2__white_bg_hero__1.png`（旧落盘，非本轮成功下载） |
| gpt-image-2 | `lifestyle_scene`: 温暖自然家居场景、电影感柔光、8k product photography | `tsk_img_01KY4AP1GZ1X59340RWQPFRETB`；9.4 s；1.2 credits；`failed` | `outputs/linkfox_test/0005/gpt-image-2__lifestyle_scene__1.png`（旧落盘，非本轮成功下载） |
| nano_banana | `white_bg_hero`: pure white、主体居中、柔和棚拍光、清晰商业摄影 | `tsk_img_01KY4APBV947NZY4BJ4PD8MBKJ`；14.3 s；3.6 credits；成功 | `outputs/linkfox_test/0005/nano_banana__white_bg_hero__1.png` |
| nano_banana | `lifestyle_scene`: 温暖自然家居场景、电影感柔光、8k product photography | `tsk_img_01KY4APZPPMJWJYM5RWCS2G8X6`；14.1 s；3.6 credits；成功 | `outputs/linkfox_test/0005/nano_banana__lifestyle_scene__1.png` |

积分余额：开始 `77.4`，GPT 后 `79.8`，结束 `87.0`。本次 SKU 请求合计消耗 **9.6 credits**，按 200 credits/USD 约为 **USD 0.048**。此数值包含两个明确失败但由余额差反映的 GPT 请求积分；单图积分为批次差值平均值。

## 质量观察与结论

- 白底图背景干净、主体清晰，具备基础主图的白底与棚拍光效果；场景图具有真实家居陈列、自然光和空间感。
- 但四张可见 PNG 都将“Faux Window Frame wall sticker”重绘成了可开启的实体窗框，并改变了花景与图案；新版 nano 白底图还出现不应有的产品文字。即使 prompt 已强制 STYLE-LOCK，视觉验收仍判定为**产品样式一致性不通过**。
- `gpt-image-2` 在本轮的两次请求都返回上游 `generation_failed`，但余额差显示被计费；这是本轮的主要运行问题。

结论：测试命令、toapis 任务轮询和两张 nano 下载已实际完成，且目录中确有要求的 4 张 PNG；但样式锁未得到模型遵守，所有图均不应作为正式商品素材。下一轮应使用原始商品图作为图生图参考，并在提交前加入图案/材质的视觉一致性验收。
