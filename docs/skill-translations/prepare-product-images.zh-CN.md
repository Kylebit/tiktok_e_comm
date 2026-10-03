<!-- source_sha256: 5054f5f4145c46e623a31f93ca2c4898c7ff87e8c03e721640f5ce97a00556d2 -->

> 历史译本（2026-09-23 标注）：此 source_sha256 与当前英文 Skill 不一致；请读当前英文 `SKILL.md`，它是唯一执行权威。旧译文中的 R1 和图片审核步骤不代表当前一轮最终审核流程。

### 元数据

- `name`：`prepare-product-images`
- `description`：消费不可变第一轮快照，通过灵识 AI 准备双品牌母版与本地化图片，使用共享产品预算、持久化恢复、返工和自动 QA。用于第二轮商品图片准备；不重新批准事实、不写妙手、不发布。

### 准备商品图片

恢复已有商品前，按仓库根 `docs/PUBLICATION_SOURCE_CONTRACT.md` 使用所选项目中绑定提交的 `scripts/publication_takeover.py`。保留已有付费回执与 QA；只读结果不会启动新任务，也不会把已生成资产转为 `keep`。

R2 使用 `scripts/prepare_product_images.py`，视觉 QA 使用 `scripts/run_automated_image_qa.py`。保持冻结 R1 的商品、品牌、目标与来源身份。处理未知结果、旧记录或技术漂移前，读取 `references/paid-recovery.md` 中本地预算与恢复合同。此处 `scripts/`、`references/` 相对 canonical Skill 目录；`shared_platform` 和 `reports` 路径相对仓库根。

### 权威与输入

要求有效的 `round1-approved-snapshot/v1` 和匹配的当前商品发布中心批准。在原范围内复用批准。事实和目标变更仍走既有 R1 批准流程。

付费执行要求由 `shared_platform.publication_autopilot` 加载既有适用政策，其归属权威、允许用途和产品上限必须覆盖本动作。开始新授权的真实生成前，展示所选模型、当前价格、用途与 QA 计划，取得所需付费授权。已有匹配授权无需再次索取。

随包的 `historical-autopilot-policy.example.json` 是非活动的历史来源证据。其原名称、日期和 ACTIVE 状态不是当前权威，不能将其激活为默认配置。离线 fixture 和绿色测试不构成真实生成权限。

### 执行一个阶段

1. 仅带 `--offer-id` 运行以检查本地状态。help/status 不调用付费服务。缺少既有 R1/预算桥接的旧 UI 消费者在调用服务前返回 `PAID_CONTEXT_REQUIRED` 或 `LEGACY_R2_BRIDGE_REQUIRED`。
2. 校验已批准的品牌角色与复用计划。在适用付费授权下运行 `--execute-brand-generation --paid-policy <existing-policy>`。完整来源字节与冻结事实绑定技术计划及每个 checkpoint。完成整套母版图片审核后停止本阶段。
3. 使用 `run_automated_image_qa.py --offer-id <id> --model <approved-model> --paid-policy <existing-policy>` 执行母版 QA。保留原始回复、精确产物摘要和 QA 回执。通过结果只属于这些产物及该 R1 快照。
4. 用 `--approve-translation-images <numbers> --dimension-only-images <numbers-or-none> --approved-by <existing-actor>` 冻结已经批准的编号图片范围。在冻结 R1 目标内路由语言；仅尺寸图不创建翻译任务。
5. 在匹配的付费授权下运行 `--execute-paid --paid-policy <existing-policy>`。OCR、文字翻译、本地化图片生成与母版及 QA 共用产品账本。OCR 区域为空时直接复用，不发送付费模型请求。
6. 使用精确已通过的母版 QA 回执和当前本地化产物执行本地化 QA。保留事实对齐、OCR 语言或重复性失败供复核。

壁纸和墙贴消费 `prepare-product-publication/references` 下既有产品家族规则包；不能在提示词中编造缺失事实。

### 预算与恢复

`reports/product-preparation/<id>/paid-requests/events.jsonl` 是计数权威。品牌、语言、QA、重试、新进程及改变后的批准摘要共用该账本。POST 前先持久化预留并占用名额。已尝试、未知、失败、被替代与完成的工作仍计数；等待服务返回前释放账本锁。

对新商品，代码完整盘点已知 R1/R2 报告与本地化审核/包元数据根。普通审核 HTML/Markdown 和本地计划属于证据，不是付费调用。有效旧回执带来源 SHA 导入，同一服务商的重复任务仅计一次。缺元数据、重定向根、归属不明确或旧工作未知，均不能当作零。

报告分别显示本阶段计划请求数、已占用、已尝试、未知、已确认，以及本次调用新增请求数。资产数量和旧回执生成数量是全生命周期产物事实。原始 usage/成本与价格估算分开。只有适用产品政策包含该用途，且存在绑定的实际请求回执时，才计入先前 R1 标题/文案调用。

遇到 SUBMITTING、UNKNOWN 或提交后超时，检查本地请求/checkpoint 状态。已有 task ID 通过查询恢复，不能重新创建。原始聊天回复先持久化再解析，可在本地重新解析。超时且没有原始回复时需上游对账；修改提示词或重试编号不授予重发权限。

使用 `references/paid-recovery.md` 中的本地恢复 CLI。它检查精确商品/请求/attempt/revision、来源 SHA、核验者、时间戳和证据引用，但不会发现或证明外部计费事实。保留损坏及旧版本字节，绝不编造未计费结论。

### 返工与技术重建

已完成付费产物若被精确 QA 拒绝或被明确选中重做，可在既有返工政策与剩余产品预算内创建新 attempt。品牌请求用 `--prepare-brand-rework`；单个本地化产物用 `--retry-localized-review-number <n> --retry-locale <locale> --retry-failure-code <code> --retry-authorized-by <existing-actor>`。绑定旧任务/产物、QA 或用户意图、当前输入与下一 attempt。最多重试三次；旧成功工作仍计数，无需虚假的未计费证明。

TECHNICAL_PLAN_DRIFT 写出绑定摘要的提案，并保留活动计划。既有批准提供有效当前输入后，通过本地 CLI 显式激活提案。激活归档旧计划和受影响 R2 投影，输出 R3 失效证据并保留全部付费事件。商品请求仍未知时禁止激活。不能删除账本/checkpoint 让新计划运行。

R2 不写妙手、ReleaseStore、ReleasePlan、平台草稿或发布。R3 通过自己的权威消费已批准图片证据和 `round2-technical-invalidation.json`。离线测试和图片 QA 不授予发布权限。
