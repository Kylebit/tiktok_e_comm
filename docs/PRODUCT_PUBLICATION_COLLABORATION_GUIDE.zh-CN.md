# 商品发布协作操作手册

本手册用于上下文丢失、切换 Agent、应用重启或任务中断后的继续执行。
三项英文 Skill 是执行权威；本手册只说明 Kyle 与 Agent 如何配合。

## 一条主线

一个商品始终只走三轮：

1. `prepare-product-publication`：第一轮准备和商品发布中心审核。
2. `prepare-product-images`：可选图片生成、唯一一次妙手同步、会话批准和发布交接。
3. `publish-approved-product`：按冻结快照分别发布 TikTok、Shopee、Ozon。

只有商品发布中心 `/new-product?offer_id=<OFFER_ID>` 是 Kyle 的审核页面。
其他页面都是技术状态或预览页面，不要求点击批准按钮。Kyle 在会话中说
“通过”“继续”“开始下一轮”或“开始发布”，即表示批准当前已展示的冻结范围。

## 开始前

启动并检查本地服务：

```powershell
.venv\Scripts\python.exe scripts\product_publication_runtime.py --start
.venv\Scripts\python.exe scripts\product_publication_runtime.py --status
```

检查三项 Skill 已安装且与仓库一致：

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
```

新 Agent 接管时，先运行：

```powershell
.venv\Scripts\python.exe scripts\product_publication_runtime.py --takeover-check
.venv\Scripts\python.exe scripts\product_publication_workflow.py --offer-id <OFFER_ID>
```

该命令只读，不访问平台，不修复数据，只输出当前阶段和唯一下一条命令。

## 第一轮：准备商品

Kyle 推荐说法：

> `offer ID <OFFER_ID>，发布到 <精确店铺清单>，开始第一轮。`

Agent 必须：

- 使用精确 Offer ID 和精确店铺清单，不自行补 HomeBloom、Shopee 或 Ozon；
- 核对 SKU、类目、价格、标题、发布规格、成本、重量和包裹尺寸；
- 提出图片计划，但翻译位置和双内容组由 Kyle 决定；
- 把结果写入 `reports/product-preparation/<OFFER_ID>/first-review.json`；
- 明确报告：妙手写入 `0`、平台写入 `0`。

第一轮禁止：妙手更新、付费图片调用、认领、创建店铺草稿和平台发布。

Kyle 审核完推荐说法：

> `第一轮通过，开始第二轮。`

## 第二轮：图片、妙手和冻结交接

Agent 先执行只读预检：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <OFFER_ID>
```

有已选翻译任务时，Kyle 的“开始第二轮”授权已选范围内的付费生成；Agent 执行：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <OFFER_ID> --execute-paid --confirm-paid-generation
```

没有翻译任务时，付费任务数必须为 `0`，流程直接继续，不能为了完整流程而生成图片。

第二轮只允许一次妙手公共采集箱更新，并必须官方回读：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <OFFER_ID> --execute-miaoshou --confirm-miaoshou-write
```

Kyle 在会话中说“通过”或“继续”后，Agent 记录批准：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <OFFER_ID> --approve-all --approved-by Kyle
```

最后冻结发布交接：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <OFFER_ID> --finalize-release-handoff
```

新生成的 ToAPIs 图片使用已持久化的公开 HTTPS 结果地址。旧任务若没有该字段，
Agent 必须要求一份精确 uploaded-assets manifest；不能伪造 URL 或静默重复上传。

成功必须产生：

- `workflow-handoff.json` 状态 `READY_TO_PUBLISH`；
- 一个精确已批准的 v4 `plan_id` 与 `snapshot_digest`；
- 妙手确认写入总数恰好 `1`；
- 平台写入总数 `0`；
- 有翻译任务时，所有目标站点的图片语言路由完整；
- 无翻译任务时，直接使用精确基础快照，不创建无意义图片 successor。

## 第三轮：正式发布

Kyle 推荐说法：

> `开始发布。`

Agent 先运行只读状态命令取得精确 `plan_id`，再使用唯一生产入口：

```powershell
.venv\Scripts\python.exe skills\publish-approved-product\scripts\product_center_publication.py --offer-id <OFFER_ID> --plan-id <PLAN_ID> --platform all --execute
```

三个平台独立启动和分类。一个平台失败不能阻止另外两个平台；未知结果不能盲目重发。
Agent 对 Kyle 只报告：发布成功、平台处理中、部分成功、发布失败，以及必要的脱敏原因。

## 中断、换 Agent 或重启

Kyle 推荐说法：

> `检查 <OFFER_ID> 当前进度并继续。`

新 Agent 必须按顺序：

1. 运行 runtime `--takeover-check`，一次核对双服务身份、三项 Skill parity、Git 跟踪状态和真实发布入口；
2. 若仅有服务未启动，运行 runtime `--start` 后再次执行 `--takeover-check`；
3. 运行 `product_publication_workflow.py --offer-id ...`；
4. 只执行输出的唯一下一条命令；
5. 不依赖聊天历史猜测，不重复已经确认的外部写入。

出现 `RECONCILIATION_REQUIRED` 时停止所有写入，先核对持久化回执和官方状态。

## 每轮 Agent 必须报告

- Offer ID、当前 revision、精确目标店铺；
- 当前阶段和下一阶段；
- 本轮付费调用数、妙手确认写入数、平台确认写入数；
- plan ID、snapshot digest 或明确说明尚未生成；
- 未解决的最小决定或脱敏技术阻断；
- 是否可由另一个 Agent 仅凭本地持久化状态继续。

任何时候都不得把“妙手采集箱更新成功”描述成“平台发布成功”。
