# R1 库存策略来源与 Ozon v4 兼容性（2026-09-22，只读）

结论：`stock_per_model_sku: 2` 与 v4 的 `quantity_per_sku: 200` 都指向**每个 SKU 的数量**，但值和审核合同不同，不能解释成同一策略的两个写法。现存所核的历史业务 R1 拷贝采用完整的 200 件策略；当前仓库的三份合成测试快照分别采用 2 件或空策略。测试快照的冲突不能反推历史业务快照需要重审，历史 200 件快照也不能替代新 Offer 的库存决策。

## 生产者链路

1. 审计基线中，`skills/prepare-product-publication/scripts/prepare_product_publication.py::prepare_offer` 生成的 `publication-preparation-decision/v1` 包**没有** `publication_stock_policy` 字段；`shared_platform/round1_workspace.py::_packet` 直接消费该生产者。
2. 审计基线的 `shared_platform/publication_rounds.py::validate_round1_reviewable` 检查状态、目标、分类与图片计划，但没有核对库存策略；`build_round1_snapshot` 将 `review.get("publication_stock_policy") or {}` 原样写入快照顶层和 `fact_snapshot`，并把全部内容纳入 `snapshot_digest`。`persist_round1_snapshot` 是不可变写入。因此旧基线容许缺字段冻结为空策略，旧快照不应原地补写。
3. `shared_platform/publication_stock_policy.py::default_publication_stock_policy` 定义完整的 `publication-default-stock/v1`（每选中 SKU 200 件）。审计基线的运行生产者没有调用它。`shared_platform/publication_r3_image_bridge.py` 直接把 R1 字段投影到 v4；`domains/product_operations/approved_publication_snapshot.py::_publication_stock_policy` 只接受完整的 200 件策略。
4. 本工作包另有**未提交候选修复**：真实 R1 生产者现写入完整策略，R1 审核门禁严格拒绝缺失/变更，原首轮页面展示库存数量和来源；旧批准快照与旧 2 件 JSON fixture 均未修改。候选行为以最终提交和部署验收为准。

## 有界快照分布

只读取了本候选仓库的 `reports/product-preparation/*/round1-approved-snapshot.json`、其三个仓库合成 fixture，以及历史 `image-approval-395` 审核材料中六份 adoption bundle 和一份 QA 拷贝。统计脚本在 `D:\OrbitEvidence\convergence-20260919\endgame-20260922\audit_r1_stock_policy_readonly.py`，只输出聚合分类，不输出 Offer、SKU 或商品事实。

| 来源 | 文件数 | 独立快照 digest | 库存策略 | 完整性 |
| --- | ---: | ---: | --- | --- |
| 当前候选仓库运行报告 | 0 | 0 | 无可分类对象 | — |
| 仓库合成 fixture | 3 | 3 | 2 份 `{stock_per_model_sku: 2}`；1 份 `{}` | 3 份 digest 有效 |
| 历史 adoption 拷贝 | 6 | 1 | 全部与完整 `publication-default-stock/v1` 200 件合同完全相等 | 6 份 digest 有效 |
| 历史 QA 拷贝 | 1 | 1，与 adoption 为同一 digest | 同一完整 200 件合同 | digest 有效 |

各组快照顶层与 `fact_snapshot` 内的库存字段一致。历史 7 份文件是同一自动审批快照的拷贝，**不是 7 次不同业务批准**。合成 fixture 的 2 件字段虽然摘要有效，却不符合当前 v4 库存合同。该有界统计不涵盖其他运行根、归档和全部线上商品，因此不能报告全局已批准快照总数。

## 迁移判断

- 对已冻结完整 v4 200 件策略的同一 Offer/R1 快照，可以沿用该轮已记录的库存授权；Ozon 仍须补上固定账号官方仓库列表的唯一仓库只读回读，并把决策及来源与该 R1 digest 绑定，才可形成终审预览。无需仅因桥接代码缺口而让用户重复审核库存。
- 对缺字段或明确为 2 件的**真实**已批准快照，不能将 200 件系统默认值回填进原签名。如果目标库存要变为 200，需要一份新的、明确覆盖该库存量的审核决定和独立后继回执；若业务确实要保留 2 件，则须设计经审核的新版 v4 库存合同和执行端支持。无论哪条路，原快照保留，预览保持阻断直至来源清楚。
- 对新 R1，候选修复已在首次审核包展示完整库存策略，并在冻结前校验精确字段；整个 R1 快照受摘要保护。隔离测试用真实生产者覆盖“准备 → R1 冻结 → R3 v4 库存投影”，验证缺失、变更和冻结后篡改会阻断。旧合成 fixture 保持原样，未被改为 200 来消除失败。

本轮未访问 Ozon API、未修改任何批准记录、未执行商品或库存写入。
