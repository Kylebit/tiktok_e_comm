# 工作台反馈合同与采用边界

唯一维护候选是本仓库 `workbench-home-integration-20261005`。它继承真实维护提交9334001和正式5946154；商品/供应链作者的候选不在本次合入范围。具体候选HEAD、检查时点、失败与源码字节记录由仓外的本次交付索引绑定，不能沿用8c/ae的旧验收数量。

4039931504只有一个业务主卡，工程接续在详情中保留。只有双方原证据可读、准确COMMON身份、原会话及主/工程角色都一致且接管accepted时才能折叠。主任务01a1066a-bdd4-77ad-b3d7-8750d0d08021；工程01a101b6-0ab6-72cb-9a61-d557641ba023。工程通过、独立数据库副本中的测试入档及私有TASK编号均不能提升商品业务阶段。名称和来源原图分别受原字节、身份及lineage校验，冲突时不借用另一任务的图名。

每卡显示原业务状态、协调责任、具体下一动作和原证据时间。协调责任不是执行器运行证明。用户动作须有新鲜、可核验且明确分类的原决定证据，blocked本身不产生用户审核。工作台保留原UNKNOWN、未接受接管及历史记录。详情展开状态在15秒刷新后保持。

## 三条来源

| 来源 | 当前接入 | 可表达的变化 | 仍缺的证据 |
| --- | --- | --- | --- |
| 商品主业务 | 尚无可自动读取的独立业务新回执；通过协调者带收据更新命令接续 | 商品本身的新阶段只能来自该业务原证据 | 正式入档、实际类目/身份、费用及授权、终审候选 |
| 商品工程 | 原`ACTUAL_INDEPENDENT_RECEIVER.json`只读适配器 | 4039931504主记录的工程依赖变化及工程子记录；主业务仍未准备完毕 | 工程交付不能证明正式登记或最终业务完成 |
| 供应链 | 原`handoff-receipt.json`及已核`DELIVERY_RECEIPT.json`只读适配器 | 原页47行候选交付、正式未采用；不会将候选交付说成生产完成 | 正式页仍旧快照；MY空SKU、非原子来源及历史结算条件保留，正式准入待定 |

目前自动接入2个产物路径，更新3个记录；接受范围总计7个原会话。工作台、六文件回收、达人、视频4个记录仍需协调者核验反馈后带收据更新。产品业务新回执缺源和未知schema都明确显示，不靠工程回执补造业务完成。没有全会话事件连接、账号自动授权、系统自启动或新业务调度器。

工程适配器只接受4039931504/1050277205528、准确schema、独立副本状态、正式登记false和共享业务写入0。供应适配器接受旧八店完成回执或准确的retained-original-supply-delivery/v1：原页候选、正式未采用、47行/MY1异常/库存100+8/入库38/非原子与业务、库、认证、采购、库存及网络写入0。状态推进、schema改版或正式登记变更都须重新由协调者核验原产物并更新适配器。不会跟随回执内路径，也不会加载业务配置、凭据、数据库或provider。

## 写入与读取合同

`config/work_feedback.example.json`默认全部缺源，无实际机器路径。协调者在仓外创建明确的`orbit-source-feedback-profile/v1`，`sources.engineering.path`和`sources.supply.path`各绑定上述两个原JSON绝对本地路径；最新供应来源是原kpi/delivery-final/DELIVERY_RECEIPT.json，旧八店回执仍作历史证据。`product_business`维持null，直到原业务提供可核验合同。非准入文件名、相对/网络/重解析路径及未知来源拒绝。不能把settings或认证文件作为回执。

原供应链7be3c680相对933的20文件差异已组合到本唯一候选。公式前缀、原Handler和本地编辑/导出路线保留；没有替换为另一个供应前端。独立数据根只按哈希复用原四份输入及已有图片，重新绑定本候选root/branch/HEAD/源码SHA，使用原captured-stage和确切digest的captured-apply。原来源四份输入、31日运输窗口、原作者runtime-v2及正式9/30包不改动。正式采用时供应capture与首页观察文件一起纳入一个部署profile；采购、merchant授权重试与共享队列不在采用范围。

`scripts/observe_work_feedback.py`要求显式`--profile --manifest --state-dir --health-file`，全部输出在源码仓外。单观察器持有独占observer.lock；遇到现有锁直接失败，不删除其他writer的锁。每5秒只读来源，保存准确原字节，生成绑定work_id/原会话/原时点/全部changes的更新收据，再调用`update_observation`。该调用检查旧manifest SHA、范围证明、新收据、产品来源与独占更新锁后原子替换协调观察文件。禁止改变原身份、owner、handover或执行映射。不派单、不创建业务任务、不改任何原业务DB。

同SHA或只变mtime不更新证据时点；旧回执不覆盖更晚业务记录；未知或变化证据保留上次记录并显示待核。保存的原观察副本被改动也会报告来源待核。观察器心跳仅证明来源观察检查，不证明业务执行器健康。原业务证据超过30分钟仍过期。

页面每15秒使用纯GET：`/api/orbit/home/tasks`、`runtime`、`feedback`和受身份约束的images。心跳由环境`ORBIT_WORK_FEEDBACK_HEALTH`指向仓外JSON并作公共字段投影，源码static目录无需写入。观察manifest由`ORBIT_WORK_OBSERVATIONS_MANIFEST`显式绑定。旧`/api/orbit/tasks`和`/api/orbit/operations-runtime`可能处理租约，禁止用来做本交接核验。

定时预算约为5秒来源检查加15秒页面读取及处理时间；不是绝对20秒SLA。隐藏页暂停，恢复可见即读取。观察器超过20秒未报告则显示观察不可用，业务实时状态仍unknown。

## 最小正式采用流程（本轮未执行）

1. 协调者先解决本次交付索引列出的剩余门禁，并确认正式入口/部署profile时点、594仍是accepted、无冲突writer。商品工程、供应链或其他作者WIP均保持独立。
2. 只把本候选的本地提交系列按933基线应用到一个正式维护候选；核对源文件、资源和入口版本，按Git签出的字节物化历史冻结结算JSON（本仓.gitattributes明确CRLF）。不替换商品Skill、不拿失配的全量日志做通过证明。
3. 在选定协调数据根准备带独立scope_ref及原证据SHA的观察manifest和来源profile。它们是协调元数据；既有settings路径、catalog/release/workbench/operations数据库、业务数据根和暂停/UNKNOWN状态全部沿用。不得新建缺失的正式workbench数据库来制造可用。
4. 只在待采用的web-only部署profile显式加入`work_observations_manifest`及`work_feedback_health`绝对路径。`operations_web_entry.py`在导入应用前绑定，未配置时清除继承预览路径；冲突的继承路径拒绝。观察器与其manifest/state/health一一绑定，已有writer未交接时不启动第二个。
5. 按正式采用权限完成入口切换后，以纯health验证准确源码根/HEAD、配置身份及web-only；再用新home纯GET与实际浏览器核验图名、7会话覆盖、三源缺项、去重、用户动作及无业务写入。旧租约GET不用于验收。回滚只恢复原入口/profile，不回退或迁移数据库。

当前用户57700预览仍为8c后端加已经验证的展示覆盖层及本任务旧观察器，具有自己的隔离观察文件。它没有生产DB或worker，不能证明此新提交的后端正在用户页运行。新候选源码及其有界验证须单独绑定；原8c seal、旧177通过或旧截图均不能代替。正式49289/594本轮未切换。
