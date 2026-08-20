<!-- source_sha256: 6266a2b4ce72886935f4a715b2c01f59996336ff12db0d01cfd5ff16202f7e22 -->

### 元数据

- `name`：`prepare-product-images`
- `description`：冻结会话已批准的第一轮范围；可选地只对用户选中的图片执行 ToAPIs 本地化；精确同步并验证一次妙手公共基线；记录会话批准；在不发布的前提下原子冻结已批准 ReleasePlan 交接。用于第二轮的开始、恢复、批准或完成。

### 准备商品图片

必须使用 `scripts/prepare_product_images.py`，不得用临时 API 调用重建流程。

### 工作流

1. 要求精确 Offer ID 和 Kyle 已批准的第一轮商品发布中心 revision。
2. 要求 `reports/product-preparation/<offer_id>/first-review.json` 为 `FIRST_REVIEW_READY` 且 revision 一致。
3. 第一轮图片计划是权威；OCR 和模型不能额外选择翻译位置。
4. 排除 `REMOVE` 来源图，并确定性重排保留位置。
5. 先不带付费参数运行，报告冻结输入 digest、位置、语言路由和付费任务数。
6. 若没有翻译位置，允许零付费任务继续，不能虚构图片工作。否则必须同时使用 `--execute-paid` 和 `--confirm-paid-generation` 才能开始付费生成。
7. 每个任务必须有完整 ToAPIs 回执和精确输出数量；未知结果不能盲目重试，应先检查持久化 checkpoint。
8. 妙手公共采集箱只写入英语母版一次，并执行官方回读。必须同时使用 `--execute-miaoshou` 和 `--confirm-miaoshou-write`。该技术条件不能阻止或删除会话批准。
9. 本地化图片按目标路由冻结，不能把多国语言图片同时写入公共采集箱。必须保留 ToAPIs 返回的公开 HTTPS 地址；旧资产缺失该事实时，需要明确 uploaded-assets manifest，不能伪造或静默重复上传。
10. 商品发布中心的 `#localizedImageResults` 是唯一人工审核面；独立结果页只是技术查看页，只保留刷新动作。
11. Kyle 会话批准是唯一批准入口，即使技术检查未完成也应立即记录。
12. 批准意图自动接受同一冻结输入下已经就绪或稍后就绪的资产；技术阻断不要求重复批准。
13. 一次妙手验证和会话批准完成后，执行最终交接：复用精确已批准基础 plan，或本地冻结当前精确 plan。有本地化任务时，原子创建并批准只改变图片路由的 successor；无任务时保留基础 plan。只有 v4 快照和目标路由回读精确后才写 `workflow-handoff.json`。

### 安全边界

- ReleasePlan 只能发生本地变更：冻结基础 plan，并在需要时原子创建一个已批准的图片路由 successor。不能让 predecessor 已被 supersede 却没有可用 successor。
- 付费生成期间不写妙手；生成回执完整且会话授权后才执行独立公共基线同步。
- 不发布、不认领、不创建店铺草稿、不直接更新平台图片。
- 不为未批准或过期 revision 生成图片。
- 不生成第一轮计划和目标店铺之外的语言。
- 付费调用按外部写入计数并报告确认数量。
- OCR 只提取用户已选图片中的文字，绝不能决定选图。
- 本 Skill 不发布；`READY_TO_PUBLISH` 只由 `publish-approved-product` 消费。

### 命令

预检：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id>
```

明确授权后的付费生成：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --execute-paid --confirm-paid-generation
```

第二轮唯一妙手同步和验证：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --execute-miaoshou --confirm-miaoshou-write
```

记录 Kyle 会话批准：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --approve-all --approved-by Kyle
```

冻结发布交接：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --finalize-release-handoff
```

旧资产没有 ToAPIs 公开结果地址时增加 `--uploaded-assets <PATH>`。文件必须给出精确 `uploaded_assets` 映射：已批准 artifact ID、匹配 digest 和公开 HTTPS URL。缺失、额外或漂移均失败关闭。

命令只输出一个 JSON 摘要。批准和技术就绪分离；批准后仍可显示 `MIAOSHOU_SYNC_REQUIRED`。最终成功显示 `READY_TO_PUBLISH`、精确 plan/snapshot 身份，不要求另一次页面批准。
