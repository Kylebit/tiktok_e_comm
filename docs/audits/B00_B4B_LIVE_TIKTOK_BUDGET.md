# B00-B4B TikTok 真实装配预算与未决目标隔离

本段沿用冻结 r4 的 `DurableTikTokV4DraftPreparer.__call__` 目标范围参数和 `MiaoshouTikTokV4DraftTransportFactory.__call__` 的共享/逐目标 reservation 回调，保留现有 `TikTokV4DraftCheckpointStore`。来源主文件 SHA256 为 `79fdb327e675c88e7aa5fcccd63d7c9242d55c94418596b8a3817a91a2ac4a59`；最小差量不替换整文件。

- factory 核完整冻结目标身份，允许完整已批准 seed map，但仅投影请求目标。具有 candidate 的 request 必须有既有预算账本；create/claim/SAVE 在各自 HTTP mutation 前 reserve。
- preparer 只处理本次目标。claim/SAVE 的本地 `PublicationWriteBudgetExceeded` 向既有 checkpoint 计数出口传播，不伪造新 UNKNOWN；已发成功/未知事实保留。
- shared executor 只把 `PREPARED/DRAFT_SAVED` 目标交给独立 publisher。未决/失败目标没有新的 SAVE 或 publish，仍保留原目标事实；其它已准备目标可继续独立处理。只向后续 projector 传通过验证且被选中准备成功的 context。
- publish 前预算拒绝生成明确 `WRITE_BUDGET_REJECTED`、未发送、不可自动重试结果。它不成为网络未知；准备阶段已计入的前缀仍在总账和 checkpoint 中。
- publisher 的历史 UNKNOWN 回执可能保留已确认 SAVE 次数。本段保留目标层该前缀，但平台聚合总数为 `None`，不把它冒充全部已尝试外写数。

## 真实消费者验证

`tests/test_b4b_live_tiktok_budget.py` 使用真实 factory→durable preparer→shared executor→TikTokPublisher→MiaoshouTikTokTransport，只替换最低 Miaoshou HTTP；category/seed/readback 为合成的精确事实。一个用例再通过真实 runner、最终批准合同和持久 report store 执行并原 run 重放，实际 fake mutation 固定为 create+claim+SAVE+publish=4、账本 shared=1/target=3、重放不新增。

- 原 source 未接 seam：只请求 PH，却对 PH+MY 发 6 次 fake mutation，且不计预算。`live-tiktok-budget-red.xml` 保留证据。
- 接 seam 后：本地 cap 拒绝被改写 UNKNOWN，见 `live-tiktok-budget-first-green.xml`（文件名包含 green 但实际 4 failed，不作绿证据）。
- 超时消费者红例：claim UNKNOWN 后仍 SAVE/publish，SAVE UNKNOWN 后再次 SAVE，publish UNKNOWN 的总数不完整。`live-tiktok-budget-final.xml` 为 4 failed/139 passed 的原始证据，不作验收绿证据。
- `live-tiktok-budget-final4.xml`：153 passed、0 failure/error/skip，包含 13 个新用例和相关原合同。集合重叠，不与此前 138/143/73 等相加。覆盖 shared/逐目标预算0..3、本地拒绝、完整 seed map 投影、4 个 HTTP 超时、准备未知重入不再外写、PH 未决而 MY 独立执行、真实 runner 原 run 重放。

预算耗尽用例是针对现有回调合同的故障注入，故意使用比 candidate 更窄的 runtime ledger；正常 runner 用例直接消费真实已批准 candidate 分配的预算。没有把不足预算候选伪装成可批准正例。

最初 publisher debug fixture 缺 provider `skuPropertyList`，触发合法 preflight 拒绝。随后补真实支持的 property shape，并在冻结前选择明确 Blue/Pink option。未放松 publisher spec 校验；本段不宣称任意多属性 display 投影均已 E2E。

已运行测试均处于审计 Python 进程网络禁止保护下，未进行真实平台调用。完整 B4B 历史仍包括此前 test-stable 异常出网，见 `TEST_ISOLATION_INCIDENT.md/json`。R2→R3 图片链、COMMON 生命周期与其余渠道来源仍在后续段，不在本段验收范围。
