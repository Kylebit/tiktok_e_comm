# R3 compile 命令迁移

`skills/publish-approved-product/scripts/compile_release_candidate.py` 现在是当前 Product Center 终审预览客户端。先 GET `/api/health` 核验服务身份、commit/build 形态及显式 code root，再单次 POST `/api/product-workspace/r3-marketplace/preview`，请求体仅含 offer_id。它与现有 `prepare_publication_execution.py --finalize-release-handoff` 使用同一服务端预览接缝。

```text
python skills/publish-approved-product/scripts/compile_release_candidate.py --base-url http://127.0.0.1:<已核验端口> --expected-root <服务端绝对仓库根> --offer-id <商品ID>
```

无参数或缺配置返回 `CONFIG_REQUIRED`。`--doctor` 只检查服务身份，不请求预览。`--plan-id` 仅断言服务返回的精确 marketplace plan ID；缺失或不匹配即拒绝，不读取旧本地计划。禁止参数缩写及 `--execute`、`--approve`、`--autopilot`、`--reports-root`。超时、断线、重定向、重复 JSON 字段和超大响应均不重试，也不尝试其他端口。

实际服务端合同为 `publication-stages/v1`，内含 `publication-candidate-preview/v1` 与 `publication-snapshot-preview/v1`。只有 COMMON 为 VERIFIED、两份配置 VALID、候选 PREVIEW_READY/NOT_APPROVED、摘要及完整有序目标一致、final_review 尚无批准或执行时，客户端成功返回 `APPROVAL_REQUIRED`。这表示需要在现有审核面审阅，不能作为发布批准。摘要不可重新提交为 authority；完整终审材料仍由 Product Center 展示。

旧脚本直接读 ReleaseStore 的已批准 snapshot、加载本地 quality sidecar，再另存候选文件。本入口不再执行这条路径，也不导入业务模块；只复用相邻 `close_product_publication.py` 的标准库 HTTP/身份函数，不调用关闭/record功能。需保持两个 canonical 脚本相邻。CLI 不写本地候选、批准、配置、数据库或报告。

服务端副作用与本地客户端不同：当前 `_preview_r3_marketplace_stage_configured` 读取工作台、服务器配置、COMMON 原账本及 R2 证据，调用 `ReleaseStore.preview_plan`（纯预览）和 `compile_release_preview`，没有调用 create_plan、approve 或 persist_release_candidate。ReleaseStore 构造仅设置路径，所读 get_plan 使用只读连接。服务器及其诊断/运行环境可能产生访问日志等本地影响，不能把一次 HTTP 预览承诺为生产环境所有目录零变化；本包未连接真实服务，也未验证生产本地状态。COMMON 正式写回必须已在其他授权流程中完成，预览不会代做。

输出仅保留 schema、offer/plan、摘要、数量、固定状态和有界机器阻断码。不输出原响应、政策、凭据、provider IDs、绝对路径或服务器异常文本。配置 VALID 不表示商品已批准；HTTP 200/ok=True 但 marketplace BLOCKED 仍失败。

组合核验区分现有两种摘要格式：`candidate_digest` 是服务端 `_canonical_digest` 的裸 64 位小写十六进制值；`preview_digest` / `preview_snapshot_digest` 使用 `sha256:` 前缀。客户端分别严格校验并原样比较，不能补前缀或更改服务端批准身份。实际 Handler 到独立标准库客户端的合成测试覆盖可审阅、配置缺失和响应丢失，均不持久化 marketplace 计划或批准。

本包只增加兼容入口、隔离合同测试和本说明；不改变 R1、server、原 compiler、注册installable状态、运行配置、个人安装或历史知识库。既有关闭和 finalize 行为保持原合同。
