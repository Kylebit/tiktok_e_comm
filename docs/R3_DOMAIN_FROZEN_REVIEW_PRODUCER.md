# 原始审核内容的领域来源

本包从 `66398df2957e379b22ed551b4d39b3571da58c27` 的完整领域 producer 接出纯数据桥接，不修改 HTTP、flow、COMMON writer 或已封闭的 R3 旧 POST。

`rebuild_domain_review_graph(common_plan, common_run, marketplace_plan)` 重新验证全部六份 R1/R2 文档、顺序一致的全部平台目标、逐规格价格、类目、文字、图片与 COMMON 回读，再重跑原有 marketplace payload、snapshot、quality、candidate、manifest 编译。保存的 marketplace payload 必须与重建值完全一致，普通重新计算 JSON checksum 不能消除展示漂移。结果为拥有 immutable bytes 的 `DomainReviewGraph`，不是批准或执行权限。

`DomainFrozenReviewProducer.inspect(db, common_reservation_id, marketplace_plan_id)` 在调用方已打开的 SQLite transaction 内仅查询同库存储；它不创建连接、表、迁移、任务或 nonce。成功的图诊断保留完整 display/manifest，并仍返回 `BLOCKED`、`final_review_available=false`、`execution_authority=false`。

`DomainFrozenReviewProducer.build(...)` 当前明确拒绝 `COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED`。它不从一个旧 run 的成功、caller 的 approved_by、或 `PrivateCommonAuthorityStore` 的合成 proof 推断可信预算。当前私有 ledger 的 `common-private-readback/v1` 与真实 compiler 所需的官方字段回读不是同一合同，禁止相互包装。

下一有限接线包需要服务端可信 COMMON proof reader：在同一 compare/decision 事务内读取 Offer/account 范围完整历史预算、standing policy、reservation/run/attempt 与官方字段回读来源，重核其当前代次和实际 bytes，才可把本包的 domain graph 构建为 `FrozenReview`。未提供该 reader 时默认拒绝；新的抽象接口或调用者构造对象不构成可信 producer。HTTP consumer 只能读取服务端持久 task→COMMON reservation→marketplace plan 映射，不能接受请求体的 FrozenReview、目标子集或 approved_by。

14 个有限案例使用保留的真实 R1/R2 producer fixture、实际多规格原始 compiler 与封闭的 COMMON fixture transport。官方形状 fixture 证明数据合同，不证明实际店铺官方结果；未调用真实 provider、正式数据库、HTTP、browser、Task 或服务。先保存 RED scaffold，再激活准备实现；测试尚未运行时不声明通过。
