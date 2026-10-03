# Orbit 商品发布中心 V1（历史索引）

旧 V1 曾设计商品事实审批、内容审批、ReleasePlan 审批、妙手确认和商城确认等多轮
人工动作。该交互合同已经退役，不能作为当前 Agent、页面或自动化的执行指令。

## 当前合同

- 商品上架按 R1 候选、R2 图片候选、R3 发布候选连续推进；R1/R2 不设常规人工批准。
- R1/R2 中名为 `review` 或 `approved` 的兼容 schema 表示可审计候选或技术冻结，
  不授予妙手或商城执行权。
- 唯一常规人工审核是完整冻结候选的 `FINAL_MARKETPLACE_PUBLISH`，发生在真正商城
  发布前，并绑定候选摘要、快照摘要和完整有序目标。
- 有效最终回执可用于同一候选的商城逐目标执行、只读回读、异步收敛和有界技术重试；
  不得因换页面、会话、Agent 或平台而重复索批。
- 冻结候选变化、目标扩张、现有授权外的新付费动作或新外部写入类型，需要新的
  明确授权。未知写入结果先回读对账，禁止盲目重发。
- 妙手 COMMON 写入与官方回读属于 R3 的受控准备动作；它不等于商城发布成功，
  也不能自行构成商城批准。

执行权威：

- [R1 商品候选](../skills/prepare-product-publication/SKILL.md)
- [R2 图片候选](../skills/prepare-product-images/SKILL.md)
- [R3 已批准商品发布](../skills/publish-approved-product/SKILL.md)
- [协作与接管](PRODUCT_PUBLICATION_COLLABORATION_GUIDE.zh-CN.md)
- [线程授权与恢复](THREAD_OPERATING_MODEL.md#authority)
- [测试治理](TESTING_GOVERNANCE.md)

历史截图、固定店铺清单、旧 `/new-product` 路由、ToAPI 示例和旧确认按钮只可用于
溯源。它们不证明当前服务、平台状态、定价、授权或页面入口。
