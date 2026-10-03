---
name: apply-product-discounts
description: Inspect and apply target-scoped, frozen postpublication discounts through the existing governed OneClick action after exact marketplace readback; report pending Shopee/browser capability separately. Never publish products or create activities.
---

# 应用商品折扣

从 Product Center 已批准的不可变计划及逐店正式发布回读开始。商品发布、折扣执行、
活动创建是不同范围。本 Skill 不发布商品，不创建/改期/终止活动，不把参考报告变成许可。

## 当前入口与能力

- LivelyHive TikTok PH/MY/TH/VN：既有 `OneClickReleaseStore` / `OneClickReleaseWorker`
  的 `postpublish_promotion` 注册项负责准备、持久 claim、单次写入和正式回读。
- Shopee PH/MY/TH/VN：列在目标注册表中，执行明确返回 `promotion_execution_surface_unavailable`。
  需要接通同一账本的官方折扣 adapter；旧独立脚本不能替代它。此能力仍待完成。
- HomeBloom TikTok SEA、MX、GB：必须走明确批准范围内的可审计浏览器流程，参见
  [妙手页面合同](references/miaoshou.md)。仓库当前没有自动浏览器 writer；不要宣称已自动接通。

调用只读计划检查：

```text
python skills/apply-product-discounts/scripts/inspect_discount_plan.py --plan-json <immutable-plan-payload.json>
```

`ready` 只表示定价合同可读取，`execution_authorized=false`；任意本地 JSON 都不能证明
数据库计划已批准。真正执行继续使用 Product Center 的原批准计划、run、OneClick job，
通过正式注册项调用，不能直接调用底层 transport 或伪造 prepared command。

## 冻结事实与执行合同

1. 确认计划在 ReleaseStore 已批准，目标发布已完成正式回读，精确店铺/商品 ID/完整
   Model SKU 集合与冻结目标事实一致。图片、标题相似或其他店同 SKU 不建立身份。
2. 新 v2 折扣只读 `pricing.selected_targets[target].store_prices` 中唯一冻结行：币种、
   标价、整数 `discount_reserve_pct` 和折后价必须一致。缺项停止，不能用其他国家比例、
   当前活动观察值或默认 32/30 补齐。暂不支持多价格行的变体折扣。
3. 历史 v1 仅校验并保留完整已冻结批准的原摘要与原比例；新 builder 不再产生固定比例
   v1。旧 prepare/adapter 指纹可能要求重新只读准备，但不能改写旧批准事实。
4. 完整读取活动目录并核对分页/总数，只接受唯一进行中直接折扣活动。准备时与提交前
   均核店铺、活动窗口、商品、完整 SKU、标价及币种。
5. 活动已包含精确商品及相同比例：正式回读后记录零写成功；已有冲突或重复成员则停止。
   缺成员时，先在既有账本记录写入意图，再执行一次 PUT，再正式回读。
6. 超时/未知保留 UNKNOWN 与占用；接受后回读失败保留已写前缀。重开同 job 不能再发。
   新的已授权变更或确证零写恢复仍走既有显式恢复接口，不使用全局永久锁。

## 历史辅助脚本的用途

| 脚本 | 当前用途 |
| --- | --- |
| `inspect_discount_plan.py` | 本地冻结定价检查与逐目标能力说明，不授予权限 |
| `build_batch_discount_plan.py` | 从历史报告生成 `REFERENCE_REQUIRES_FROZEN_PLAN` 参考，不产批准 |
| `recover_miaoshou_product_identity.py` | 标题/图片相似性仅给建议 SKU，不标 EXACT_IDENTITY |
| `export_miaoshou_browser_batch.py` | 导出参考及缺目标清单，不隐去受阻行，不允许直接提交 |
| `audit_existing_discount_evidence.py` | 读取本地/官方当前折扣作为参考，不复制比例到新目标 |
| `inspect_shopee_model_identity.py` | 官方当前 item/model 身份诊断，不给未知 model 制造 ID |
| `build_shopee_identity_recovery_plan.py` | 精确 target+product 的当前身份建议，保留旧 SKU、待新冻结 |
| `execute_official_discount_plan.py` | 旧旁路迁移提示；包括 `--execute` 也不发送请求或创建账本 |

官方读取脚本使用现有认证设施，可能刷新 token 或同步本地配置；不要把它们描述为完全无
本地状态变化。需要当前外部事实时才在对应授权下运行；本地审计不能擅自读取真实凭据。
历史日期默认文件仅是来源线索，不能冒充当前证据。截图进度、HTTP 200、绿色单元测试
均不代替真实逐目标正式回读。使用前校验个人安装副本与仓库源的文件一致性；
文件一致也不代表已在生产平台执行或取得逐目标回读。
