# 查询与本地分析

保留原 C 脚本的两个端点、参数及样本统计语义，源 SHA 见统一目录。旧脚本的 400 三次尝试已收敛为一次；分析空样本或零销量返回 null 比例，不伪造市场数据。

```text
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json preview tikhub query --payload request.json
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json execute tikhub query --payload request.json --authorization authorization.json
python R/scripts/orbit_tools.py --runtime-root R --project-root P --profile profile.json analyze-tikhub --snapshot normalized.json
```

`request.json`：`{"keyword":"sample product","region":"TH","video_count":20}`；count 范围 1–20。预览返回 `request.authorization_scope`，将已核实的原用户授权序列化为 `{"authorization_id":"原批准标识","instruction_ref":"audit://原批准引用","scope":预览中的完整对象}`。代码验证绑定，不自行证明该用户授权真实存在。

每轮固定商品搜索与视频搜索各一次 GET，GET 同样可能计费。产物在 `artifact_root/tikhub/profile_digest/plan_digest前24位/`：`manifest.json`、`products.raw.json`、`videos.raw.json`。完成后同计划重跑只校验原文件并复用，新的计费次数为 0。第二请求未知时保留第一份原始文件与两次占位；不得删 manifest 或改 profile 绕过。提供方没有该查询的可恢复 task ID：先对账原请求参数/时间/用量，当前入口不会凭“无结果”重查，也不把一次新的授权自动当作原未知已清账。

分析消费规范化 `products`、`videos` 数组，`sold_count` / `play_count` 缺失为 null；保留 `sum_not_market_total` 与样本占比。原 raw 响应先按其真实返回 schema 规范化，不能把 raw 文件直接称 normalized。

来源合同为 2026-09-01《TikHub API 采集、下载与审计 SOP》，原件 SHA-256 `40354069d2d9fc6d25d1b763ef839fcdc4443919906e003dab5e3941e803870a`；2026-09-05 与保全清单逐字节核对一致。原 C 路径是历史来源，当前只使用显式 runtime/profile；注册表加载不移植。

两请求沿用商品 `search_word/offset/page_token/region`、视频 `keyword/offset/count/sort_type/publish_time/region` 合同。`region` 是搜索口径，不证明作者国籍；播放、点赞、商品锚点不证明成交或收益。不会自动下载、取得素材复用权或调用平台发布。

SOP 还要求每轮费用前后 usage 快照。本包没有迁移 `tikhub_cost_audit.py`、扩展搜索、详情/下载及视频校验工具；当前 manifest 的 attempted 计数不能代替实际账单。真实查询前沿用已有授权范围，由上层保存带任务/参数/时间绑定的 usage-before 与 usage-after 引用；若上层不能提供费用核验，应停在 preview。历史 0.001 美元/次只是当日观察值，不作为当前价格；本工具不会主动请求费用端点来制造隐藏计费，也不以新 JSON 格式要求重复人工批准。
