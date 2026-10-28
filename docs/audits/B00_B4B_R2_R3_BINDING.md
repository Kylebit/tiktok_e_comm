# B4B：真实 R2 身份、R3 路由与 COMMON 阶段缺口

本段完成实际 R2 产物的 R3 读取、图片路由准备与 QA 身份校验。当前状态是 `COMMON_STAGE_PLAN_REQUIRED`，不是整条 R3 已接通。COMMON 后端 adapter 将由同一 B4B owner 接续实现；U01 消费其真实 UI 合同。无新增并行批准/任务账本，无自动批准、平台发布或个人 Skill 安装。

## 准确来源与差量

- 路由源：冻结 r4 `skills/prepare-product-images/scripts/prepare_product_images.py`，SHA256 `0c777e4a93cbc06bb875d8e10b470a7c28066aca41fc3c6f12d065681da600ef`。完整 14 函数闭包及各段 SHA 在审计目录 `domain-prep/03/b4b/r3-bridge-closure.json`。本段迁移品牌/语言映射、编号资产摘要、纯路由 builder 到 `shared_platform/publication_r3_image_bridge.py`；不从已完成 R2 的 Skill 反向导入。
- 旧路由缺角色计划时从产物反推范围的 fallback 已去除；只消费冻结 R1 角色计划。实际 R1 producer 把首审 `PROPOSED` 规范成 `APPROVED`，此既有单字段转变得到兼容，其他内容仍精确比较。
- 3 个 R3 入口有具名迁移：`_persist_dual_brand_publication_bridge` 只准备有身份封装的路由；`sync_dual_brand_miaoshou_baseline` / `finalize_dual_brand_release_handoff` 在本阶段分别给出 `COMMON_STAGE_ADAPTER_REQUIRED` / `MARKETPLACE_STAGE_ADAPTER_REQUIRED`。没有复制旧 sync 只在写后记录 sidecar 的 writer，也没有复制旧 finalize 硬编码 Kyle、自行 create/approve 或宽松复用其他 payload 的行为。
- r4 708 行 `prepare_publication_execution.py` 已完整审读，新的同名 R3 CLI 保留 offer/阶段参数，接本模块、核 R2 后冻结图片准备。旧 successor 专项 helper 暂留冻结来源，不在本段盲目恢复或宣称可用。`--confirm-miaoshou-write` 不能替代缺失的阶段批准合同。
- `publication_quality_evidence.py` 的实际 compiler 消费者，过去 QA 只看 offer 与 PASSED。现在必须同时具备真实 R1/首审/母图/翻译计划/翻译结果/QA：自摘要、offer、R1→首审→母图、编号源 URL/SHA、翻译任务与资产一对一、完整产物 SHA 集合、QA 自摘要及 R1/generation 绑定、五类通过检查。直接传入 evidence 也经过同一核验。缺失历史证据是明确失败，不能仅凭同商品旧 PASSED 当本轮成功。

## 真实离线证据

固定 S02 提交 `c50d3d31d55004aacdd32c96c6d5bd7ded9356bb`。`produce_c50_r2_artifacts_v2.py` 在审计目录运行真实 `shared_platform/publication_rounds.py::build_round1_snapshot`，随后真实 `skills/prepare-product-images/scripts/prepare_product_images.py::run` 与 `run_automated_image_qa.py::run`。只替代最低 Lingshi/download/OCR fixture I/O，商业输入与批准身份为明确合成数据。

输出 14 母图、7 RU 本地化图、5 次 QA；既有 paid ledger 共占用 33 个 fake 请求（14 生成、14 翻译、5 QA），UNKNOWN 0。不是实际计费证据。R2 工作树 HEAD 不变且 `status --porcelain=v1 -uall` clean；36 个实际导入文件与精确提交 LF 等价，11 字节全同，其余只是 checkout 换行。

`consume_r2_real_v2.py` 再用当前真实 R3 CLI 直接读取这批文件，得到 7 张 LH COMMON 基线、7 张 PH 英文母图、7 张 RU 本地化路由，重复准备摘要不变。请求 COMMON / final 阶段分别得到具名 BLOCKED，实际 COMMON/marketplace 调用 0，也未产生 workflow-handoff。该链没有生成最终 marketplace candidate；fixture 价格/SKU 商业部分尚非完整最终事实，不能借图片通过声称商品发布完成。

证据都位于审计目录 `domain-prep/03/b4b/`：

- `R2_REAL_PRODUCER_RECEIPT_V2.json`、`R2_PRODUCER_SOURCE_VERIFICATION_V2.json`、`R2_TO_R3_ACTUAL_CONSUMER.json`、`r2-producer-isolation-v2.json`。
- 初版 v1 输出和 `r2-qa-identity-red.xml` 原样保留。4 红准确证明旧 evaluator 对错误 QA 仍标 PASSED，不把其他故意缺失商业事实当作该红例原因。
- `r2-qa-binding-final2.xml`：60 passed，0 skip。25 项新 R2/R3 测试，另含 19 项先前质量事实、15 项 compiler、1 项既有 COMMON-only Store 回读用例；不与之前 57/56/34/153 等重叠集合相加。新增的 3 项保持真实 c50 producer 的整数编号与批准 actor/authority 合同，拒绝布尔编号或任意 actor。
- 命令：`offline_pytest.py tests/test_b4b_r2_qa_binding.py tests/test_b4b_quality_evidence_identity.py tests/test_b4b_release_compiler.py tests/test_release_store.py::test_verified_readback_is_immutable_and_returned_with_target -q --junitxml=.../r2-qa-binding-final2.xml`。Python 3.12.8，`-B`，空合成 config；本组 Python audit hook 禁 socket/DNS/子进程。不是 OS 全局防火墙。
- `tests/fixtures/b4b_r2_actual/PROVENANCE.json` 保存 6 个真实 producer 输出的原 SHA 与仅 JSON 排版变化后的 fixture SHA，供无付费回归重现。

## 紧接的 COMMON 后端工作包：无循环前置

现有生产者与消费者：

1. R1 producer `shared_platform/publication_rounds.py::build_round1_snapshot`（226）绑定首审与 Product Center 批准；实际 `fact_snapshot` 投影在 292。R2 producer 如上。新 `publication_r3_image_bridge.py::validate_r2_identity` / `build_dual_brand_publication_bridge` 给出 exact R1/R2 摘要、COMMON 基线及平台路由，只有准备权限。
2. 现有 `modules/products/server.py::_release_plan_payload_from_dashboard`（2729）从当前 dashboard 组装 payload，`_approve_release_plan_locally`（7677）与 `_prepare_miaoshou_release`（7871）各自重算。后者要求同一 stored approved plan/token/current payload 且包含 `miaoshou:COMMON`。所以旧 r4 marketplace-only dual-brand payload 不能直接转发进该 handler。
3. 下一包拟增加**纯** `build_common_stage_plan_payload(round1, r2_identity, bridge, current_product_payload)`，仍在本 R3 bridge 模块，只生成 COMMON 阶段 payload：`targets=['miaoshou:COMMON']`，固定 source identity、完整 SKU reservation、revision、LH baseline URLs/SHA、必须写入的冻结事实与 R1/R2 digests。该阶段**不声明 marketplace v4 snapshot，也不要求 release_handoff/final candidate**。不从 R2 QA 推断写许可。
4. 复用 `ReleaseStore.preview_plan`（1430）→现有 `create_plan`（2046）→`approve_plan`（3167）→`start_run`/`begin_target`（3370）。当前 Store 的 COMMON-only 用例已实际通过：不需要 marketplace candidate 即可记录本阶段精确批准和 immutable readback，所以 Store 层没有循环依赖。批准参数仍须来自已经存在的匹配授权或真实批准请求；同一有效 scope 重用现有批准，不对同一决定重复提问。新 payload/执行范围不相同则不能复制旧 approval row、写死 actor 或伪造 `user_approved`。
5. 下一包在现有 `_prepare_miaoshou_release` 内加入具名 COMMON-stage payload resolver，仍检查 exact plan/token/current frozen R1/R2；实际调用 `write_miaoshou_common_from_plan` 前保留 `begin_target`（8396），同样调用现有读取与恢复分支（8310–8374）。成功沿用 `record_target_success` / `record_common_reconciled_success`，保存 exact COMMON ID、image URLs/order/SHA关系、SKU/规格/价格事实回读与 evidence digest。RUNNING/UNKNOWN/已接受未核不能新起 uuid 再写。
6. **COMMON 成功之后**，下一纯 producer `build_marketplace_stage_plan_payload(..., common_run_id, common_readback_digest)` 才使用 r4 已保全 `_dual_brand_pricing`、`_dual_brand_categories_by_target`、`build_dual_brand_release_plan_payload` 的合适部分，生成完整平台 payload。用现有 Store successor/同来源 reservation 关系，不能释放已发布 SKU。由准确已批准计划取得 v4 snapshot 后，才生成 release_handoff（含 R1/R2/common run/readback 摘要和 snapshot digest）、执行技术 preflight、compile final candidate，并沿用既有 final approval / B4A runner。
7. 现有 HTTP seam 是 `POST /api/product-workspace/release-plan/approve`（server 14831）和 `POST /api/product-workspace/miaoshou-draft/commit`（14839）；扩展 stage/冻结身份必须由服务端解析，不能因为请求省字段跳回旧 payload。UI 消费位置 `web/static/product_workspace.js::approveReleasePlan`（9038）、`prepareMiaoshou`（9066），`currentReleaseBody` 提供 plan/token。U01 应显示 COMMON 准备、授权、已提交待回读、已验证、下一 marketplace candidate 的真实状态，并维持三个平台独立执行。

上列两个 stage payload producer 与 server resolver **是下一包拟实现函数，当前源码没有这两个函数**。必须用临时 DB + 真实 server/adapter/Store + 最低 HTTP fake 做完整 R1/R2→COMMON→readback→marketplace candidate，覆盖同批准并发/中断/UNKNOWN/重复0写/错scope与过期R2。不能用注入整个 writer 来验收默认装配。

## 当前保留边界

COMMON adapter 缺失是中间交付状态，不能当作长期功能退役。`modules/sourcing/new_product_workbench.py::write_miaoshou_draft` 不接受旧 `approved_bridge` 参数且没有本段需要的 durable claim，不在本段使用。第一版 image-only R2 产物 v1 与实际 R1 producer v2 分开保留，不混写来源。

现有 cost 跨币种排序风险未扩为外汇体系。quality sidecar 加载完整性/路径与原子持久仍由 S03 协调；新 R3 路径在 exists/read/create 前检查外逃/断链，half-written exclusive 文件保全为冲突，尚不宣称本地目录交换竞态安全或原子恢复已完成。现有自动生成 QA 不等于人工重新审批；当前校验只验证既有批准与产物身份。

本包早先发生的 test-stable Shopee 实际出网异常仍按 `TEST_ISOLATION_INCIDENT.md/json` 披露；本段离线隔离和 fake 写计数不能把整个 B4B 改称从未出网。
