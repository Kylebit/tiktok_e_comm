# LinkFox 生图测试报告：SKU 0002

## 测试目标与 SKU 信息

- 测试目标：为 LinkFox 商品套图小规模测试生成 SKU 别名 `0002` 的白底主图和生活场景图，并比较 `gpt-image-2` 与 `nano_banana`。
- 数据来源：仓库 `data/shop.db` 的 `products` 表第 2 行；harness 将该行映射为别名 `0002`。
- `sku_id`：`1729650398374108082`
- 标题：`2pcs Creative Wall Stickers, Tropical Green Plant Potted Pattern Self-Adhesive Wall Stickers, Bedroom Porch Living Room Porch Home Decoration Wall Stickers, Wall Decoration Decals`
- 规格：`Green Plants`
- 价格：`14.18 GBP`
- 源商品图 URL 已由数据库提供；本次 harness **没有把该 URL 或图片二进制传给图像模型**，只把标题和规格拼入 prompt。这是本轮测试最重要的边界。

## toapis API 调用链

执行命令：

```powershell
python tmp_toapis_gen.py --sku 0002 --models gpt-image-2,nano_banana --types white_bg_hero,lifestyle_scene --n 1
```

脚本从 `config/toapis.local.json` 读取网关地址与 API key；密钥未被输出到日志或报告。对每个“模型 x 图片类型”组合，脚本执行：

1. `POST /v1/images/generations`，提交 `model`、`prompt`、`size=1:1`、`resolution=1k` 与 `n=1`。
2. 接收异步 `task_id` 后，以 `GET /v1/images/generations/{task_id}` 每 4 秒轮询，直到状态为 `completed`。
3. 读取返回 URL，下载为 PNG；将商品信息、prompt、耗时、task_id 与落盘路径写入 `outputs/linkfox_test/0002/result.json`。

本轮四项真实请求均完成，生成时间为 2026-07-22 14:37--14:39（本地脚本时间）。

| 模型 | 类型 | task_id | 耗时 | PNG 落盘路径 |
| --- | --- | --- | ---: | --- |
| `gpt-image-2` | `white_bg_hero` | `tsk_img_01KY48MY0TM94NN3N6F9FPJHR7` | 36.5 s | `outputs/linkfox_test/0002/gpt-image-2__white_bg_hero__1.png` |
| `gpt-image-2` | `lifestyle_scene` | `tsk_img_01KY48P8FZMJ66GHJJRYJF676X` | 55.4 s | `outputs/linkfox_test/0002/gpt-image-2__lifestyle_scene__1.png` |
| `nano_banana` | `white_bg_hero` | `tsk_img_01KY48R8HDH0HMKBV3ZM9ZB62B` | 15.1 s | `outputs/linkfox_test/0002/nano_banana__white_bg_hero__1.png` |
| `nano_banana` | `lifestyle_scene` | `tsk_img_01KY48RYZQP8T7YM6YVXT1R7FG` | 19.2 s | `outputs/linkfox_test/0002/nano_banana__lifestyle_scene__1.png` |

## Prompt 与生成质量观察

### 使用的 prompt

白底主图：

```text
Product title: 2pcs Creative Wall Stickers, Tropical Green Plant Potted Pattern Self-Adhesive Wall Stickers, Bedroom Porch Living Room Porch Home Decoration Wall Stickers, Wall Decoration Decals. Variant: Green Plants. Professional e-commerce product hero shot on pure white background, the product centered and filling frame, soft studio lighting, sharp detail, clean commercial photography
```

生活场景图：

```text
Product title: 2pcs Creative Wall Stickers, Tropical Green Plant Potted Pattern Self-Adhesive Wall Stickers, Bedroom Porch Living Room Porch Home Decoration Wall Stickers, Wall Decoration Decals. Variant: Green Plants. The product placed in a realistic lifestyle scene, warm natural home environment showing contextual usage, cinematic soft lighting, 8k product photography
```

### 与 LinkFox 商品套图元素的对照

LinkFox 官方将商品套图描述为由商品图衍生 A+ 图、卖点图、场景图和特写图，并强调商品主体与品牌风格一致性。[LinkFox Listing 页面](https://ai.linkfox.com/listing) 本轮仅覆盖其白底主图/场景图的前置验证，未覆盖卖点文字、A+ 编排、特写、批量模板、抠图或人工审核工作流。

| 成图 | 观察 | 对“商品套图”要素的判断 |
| --- | --- | --- |
| `gpt-image-2` 白底图 | 白底、居中、棚拍感和绿植主题均满足；但生成成两盆大型绿植，而不是墙贴/墙贴卷材。 | 构图目标达成；商品品类与实物形态偏离，不能作为上架主图。 |
| `gpt-image-2` 场景图 | 室内自然家居场景完整，墙面装饰感和暖色调较好；画面将主题表现为墙上植物/装饰物，未明确呈现“2pcs 自粘墙贴”。 | 可作为场景灵感候选；不具备商品保真证据。 |
| `nano_banana` 白底图 | 生成了两卷带热带植物印花的商品式包装，较接近“墙贴卷材”这一商品形式；但包装文字、标签和具体花纹均为模型自行虚构。 | 在产品形式上优于本轮 GPT 输出；仍不能直接用于销售，因为文字、包装与真实 SKU 未校验。 |
| `nano_banana` 场景图 | 生成卧室墙面贴花的实际使用场景，贴花主题和家居语境一致；但图案、盆栽数量和布局不等同于原商品。 | 最接近“场景图”目标；适合视觉方向验证，不适合作为真实 SKU 的直接素材。 |

### 结果与模型比较

- **速度：** 本次 `nano_banana` 为 15.1 s / 19.2 s，快于 `gpt-image-2` 的 36.5 s / 55.4 s。
- **商品形态理解：** 在没有参考原图的条件下，`nano_banana` 白底图更接近“成卷墙贴”这一文字商品描述；两种模型都无法可靠复刻真实图案、包装和“2pcs”组合。
- **场景产出：** 两种模型都能做出暖色家居场景；`nano_banana` 的墙贴呈现更直接，`gpt-image-2` 的场景氛围更像植物装饰摄影。
- **结论：** 这证明 toapis 网关与两个模型可以完成异步生图、轮询和下载闭环，但并不能证明“从商品图生成保真套图”。当前 harness 只测到了**标题驱动的概念图**。

## 遇到的问题、限制与下一步

1. **先前失败结果已被真实结果覆盖。** 本地旧报告曾记录 Windows `curl` 的 Schannel TLS 错误；本次命令实际拿到四个 task_id、完成轮询并下载四张 PNG，说明该网络阻塞在当前运行中不存在。
2. **关键功能缺口：未使用原商品图。** `tmp_toapis_gen.py` 只将 `product_name` 与 `sku_name` 组成 prompt，没有将 `source_image` 传给网关。因此无法测试 LinkFox 所强调的“商品主体保真/品牌一致性”。
3. **主图合规仍需人工质检。** 特别是模型虚构的包装文字、标签、商品数量、花纹和产品结构，不能直接用于 TikTok 或其他平台上架。
4. **建议的下一步：** 修改 harness，使其先下载 `source_image`，再调用支持参考图/编辑输入的 toapis 端点；为每个 SKU 固定“原图、白底、场景、卖点”四类输出，记录原图相似度、文字错误率、人工返工分钟数和真实 API 费用。完成该图像条件化测试后，才可与 LinkFox 的商品套图保真能力做公平对比。
