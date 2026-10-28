# 商品发布旧入口退役说明

状态：`ACTIVE_MIGRATION`

迁移截止点：`2026-09-01`

## 当前唯一写入入口

商品发布中心的新写入只允许经过已批准快照和
`publish-approved-product` Skill 所使用的三个独立 Runner 入口：

- `POST /api/product-workspace/publish-tiktok`
- `POST /api/product-workspace/publish-shopee-global`
- `POST /api/product-workspace/publish-ozon`

三个入口只共享已批准的只读商品快照。任一平台的状态不得阻断、启动或
改变另外两个平台的执行。

## 已退役入口

`POST /api/product-workspace/publish` 是旧版 publish-all / one-click
状态机的兼容入口。自本说明生效后：

1. 禁止新增调用者；
2. 商品发布中心前端不得向该路径发送请求；
3. 旧任务只允许通过只读状态接口查看，不允许从页面恢复写入；
4. 服务端兼容路由可保留到迁移截止点，但不属于现行产品合同；
5. 截止点之后应在独立变更中删除兼容路由及其仅为旧写入服务的代码。

旧状态机中的 `ACCEPTED`、`SUCCEEDED`、人工验收或直接平台响应，均不得
直接投影为新界面的发布成功。新界面只消费持久化 Skill 报告的四态：
`PUBLISHED`、`PROCESSING`、`PARTIAL`、`FAILED`。

## 历史文档

V2 需求、架构、详细设计、可视化测试计划和 Stage 0 baseline 保留用于
审计历史，不再是实现依据。文件不移动，是为了避免破坏已有审计链接；
每份历史文件顶部都必须指向本说明。

## 测试迁移边界

以下测试表达旧业务权威，迁入 `legacy-publication-compat` 历史测试组；它们
不得要求 v4 Runner 回接旧路径，也不得阻止三个新 Runner 的发布：

- `tests/test_product_release_v1.py` 中依赖 COMMON、旧 ReleasePlan 解析器、
  predecessor reuse、旧 `/publish` 批次或人工验收即成功的用例；
- `tests/test_oneclick_release_controlplane.py` 中要求 COMMON 为 TikTok 前置、
  单一 one-click job 聚合三平台，或要求旧 target 状态成为新 UI 权威的用例；
- `tests/test_oneclick_release_http.py` 中要求浏览器启动旧 publish-all 写入的
  用例。

兼容路由存在期间，以下安全性质仍必须保留并单独运行：旧 GET 只读、响应
脱敏、未知写入不自动重试、已有任务不被隐式重开。平台隔离、逐 SKU 完整、
幂等检查点和权威回读等通用性质，应在 v4 Runner / Skill 测试中重新表达，
不能靠旧状态机测试间接证明。
