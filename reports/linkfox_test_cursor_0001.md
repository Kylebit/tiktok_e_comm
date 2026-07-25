# LinkFox 生图测试：SKU 0001

## 测试目标与商品

本次测试按 LinkFox 商品套图小规模测试要求，运行 `tmp_toapis_gen.py`，为 SKU 别名 `0001` 分别请求 `gpt-image-2` 和 `nano_banana` 的白底主图及生活场景图各一张。

| 字段 | 值 |
| --- | --- |
| SKU 别名 | `0001` |
| 真实 sku_id | `1729650359815412658` |
| 标题 | 1Sheet Colorful Prismatic Window Film, Flowers Daisy Pattern Non-adhesive Static Cling Window Sticker, Birds Anti-Collision Rainbow Sticker, Sunshine Catcher Window Decals, Reusable, Home Office Window Art Sticker |
| 变体 | PVC |
| 价格 | 14.88 GBP |
| 来源 | `data/shop.db` 的 `products` 表（按 `rowid` 第一行映射） |

执行命令：

```powershell
python tmp_toapis_gen.py --sku 0001 --models gpt-image-2,nano_banana --types white_bg_hero,lifestyle_scene --n 1
```

执行时间：2026-07-22 14:16（Asia/Shanghai）。

## toapis 调用流程

Harness 从 `config/toapis.local.json` 读取 API key（本报告不记录 key 值）和 `base_url`，按以下流程处理每一组合：

1. `POST /v1/images/generations`，提交 `model`、`prompt`、`size=1:1`、`resolution=1k`、`n=1`。
2. 若响应包含 `id`，将该值作为 `task_id`。
3. `GET /v1/images/generations/{task_id}` 每 4 秒轮询，直到任务完成或失败。
4. 对完成任务的 URL 使用 `curl -L` 下载，或将 `b64_json` 解码为 PNG，写入 `outputs/linkfox_test/0001/`。
5. 将商品元数据、每次请求的 prompt、任务状态与落盘文件写入 `outputs/linkfox_test/0001/result.json`。

## 实际结果

本次四次 POST 调用均没有返回 JSON 的任务 `id`，harness 记录的错误为 `{'_raw': '', '_stderr': ''}`；故没有进入轮询或下载步骤，未取得 `task_id`，也没有由本次命令新增 PNG。`result.json` 已在输出目录生成并如实记录四项失败结果。

| 模型 | 类型 | Prompt | 耗时 | task_id | 本次落盘路径 |
| --- | --- | --- | ---: | --- | --- |
| gpt-image-2 | white_bg_hero | Product title: [上述商品标题]. Variant: PVC. Professional e-commerce product hero shot on pure white background, the product centered and filling frame, soft studio lighting, sharp detail, clean commercial photography | 0.0 s | 无（POST 无响应） | 无 |
| gpt-image-2 | lifestyle_scene | Product title: [上述商品标题]. Variant: PVC. The product placed in a realistic lifestyle scene, warm natural home environment showing contextual usage, cinematic soft lighting, 8k product photography | 0.0 s | 无（POST 无响应） | 无 |
| nano_banana | white_bg_hero | Product title: [上述商品标题]. Variant: PVC. Professional e-commerce product hero shot on pure white background, the product centered and filling frame, soft studio lighting, sharp detail, clean commercial photography | 0.0 s | 无（POST 无响应） | 无 |
| nano_banana | lifestyle_scene | Product title: [上述商品标题]. Variant: PVC. The product placed in a realistic lifestyle scene, warm natural home environment showing contextual usage, cinematic soft lighting, 8k product photography | 0.0 s | 无（POST 无响应） | 无 |

输出核验：`outputs/linkfox_test/0001/result.json` 存在。目录内另有 `nano_banana__white_bg_hero__1.png`，其修改时间早于本次运行；它未出现在本次 `result.json` 的成功条目中，因此不将其误报为本次生成或纳入本次提交。

## 质量观察

本次未成功生成可归因于本轮请求的图片，故无法对四个计划套图结果作有效质量验收。对目录中上述既有白底 PNG 的仅限视觉观察如下：白色留白和主体印花窗贴较清晰，彩虹花朵与鸟类图案可辨；但画面是平面产品图，缺乏能验证静电贴在真实窗户上折射彩虹光影的场景。因此它部分满足卖点图的白底、主体清晰和基础光照要求，不能代替生活场景图对真实感、使用语境与光影效果的验收。

## 问题与结论

- `config/toapis.local.json` 存在且 API key 非空，但本机通过 harness 发出的 toapis POST 返回为空，未能获得 HTTP/JSON 错误详情或 `task_id`。
- 因未拿到 `task_id`，轮询、下载以及四张新 PNG 的生成均无法执行；本次结果为**生图网关调用阻塞**，并非模型质量失败。
- 已保留可复核的 `result.json` 和本报告。恢复网关连通性或补充可诊断的 HTTP 状态/响应后，应重新执行同一命令，再以新生成的四张 PNG 完成套图质量验收。
