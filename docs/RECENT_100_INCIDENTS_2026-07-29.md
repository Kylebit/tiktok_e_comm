# 最近 100 个错误与阻断事件台账

日期：2026-07-29
范围：近期商品发布中心、AI 图片工作室、Shopee/Ozon/TikTok/Miaoshou、自动驾驶、供应链及测试执行记录。

## 口径

- **产品缺陷**：用户可见行为错误、死路、错误状态或可能造成不真实账本。
- **评审缺陷**：在独立源码审查或故障注入中发现，尚未正式造成外部错误，但若上线会出错。
- **外部阻断**：凭据、平台能力、数据事实或第三方 API 不满足，不等于本地代码缺陷。
- **安全门**：系统正确拒绝不充分或不一致证据；若页面没有可执行下一步，仍记为 UX 缺陷。
- 状态：**已修**、**安全阻断**、**待收敛**、**当前问题**。

## A. 内容与 AI 图片工作室（1–20）

| # | 事件 | 类型 | 根因 | 状态 / 应对 |
|---:|---|---|---|---|
| 1 | 点击“AI 规划分镜”没有可见反应 | 产品缺陷 | source-only/identity 阻断只在内部返回，页面无就地反馈 | 已修：aria-live 进度、滚动与聚焦 |
| 2 | 缺身份参考时直接显示英文内部错误 | 产品缺陷 | 服务错误未映射为用户动作 | 已修：中文说明和“前往选择身份参考图” |
| 3 | 报错 `create a local content review package...`，用户不知道下一步 | 产品缺陷 | 内容包前置条件未转成可操作流程 | 已修一部分；仍需全流程下一步 invariant |
| 4 | 保存来源图决定后身份参考被清空 | 产品缺陷 | 旧 `review` 保存路由不持久化 identity refs | 已修：统一走 content-package/review |
| 5 | 保存后主参考图选择被清空 | 产品缺陷 | primary identity 未与 refs 原子保存 | 已修：同一 CAS 请求持久化 |
| 6 | 来源图决定保存后部分选择恢复默认 | 产品缺陷 | 前端局部 state 与服务端完整 source actions 不一致 | 已修：一次保存全部来源图动作 |
| 7 | 已删除来源图刷新后重新出现 | 产品缺陷 | 删除只改 DOM/临时状态，来源重载重新导入 | 已修过局部；需补跨重启持久化矩阵 |
| 8 | 视频选择“不使用”保存后又变成“保留” | 产品缺陷 | video decision 映射/重载默认值覆盖 | 已修过局部；需统一 source/video 状态机 |
| 9 | 新来源图默认“待决定”，审核操作过重 | 产品设计缺口 | 默认值与用户工作习惯不符 | 已调整方向：默认保留、显式删除 |
| 10 | 右上角删除叉只影响当前视图 | 产品缺陷 | 删除动作与持久保存合同分离 | 待收敛：必须验证 reload/restart 后仍删除 |
| 11 | 标题生成失败：Shopee global description must be English | 产品缺陷 | 模型修复后仍未满足英文描述合同 | 已增加保守英文描述兜底 |
| 12 | 标题修复只修标题，描述错误仍导致整单失败 | 评审缺陷 | repair 范围与最终 validator 不一致 | 已修：标题/描述统一终验 |
| 13 | 分镜规划 ToAPIs 代理 SSL EOF | 外部阻断 | 本地代理/上游 TLS 连接中断 | 安全阻断；需可重试分类和健康检查 |
| 14 | 网络错误后没有明确重试/服务诊断入口 | 产品缺陷 | 所有异常被压成同一种失败卡片 | 待收敛：区分网络、合同、内容缺失 |
| 15 | 重启服务后 `ERR_CONNECTION_REFUSED` | 运维缺陷 | 服务未成功启动或启动命令错误 | 已修执行流程；需启动健康门 |
| 16 | source-only 状态禁止规划但没有明确焦点目标 | 产品缺陷 | 禁用控件只有静态文字 | 已修：自动聚焦身份参考 |
| 17 | 身份参考缺失后选择成功，旧阻断仍残留 | 产品缺陷 | 阻断状态未随选择变化失效 | 已修：选择后清除过期阻断 |
| 18 | recipe invalid 只显示失败，没有指出哪项非法 | 产品缺陷 | validator 结果没有字段级投影 | 待收敛：结构化 rule IDs 与字段锚点 |
| 19 | 页面暴露英文异常、代理地址和内部术语 | 产品/安全缺陷 | raw exception 直接进入 UI | 已部分修；应统一错误目录与红化 |
| 20 | 修复代码已落盘但浏览器仍运行旧资源 | 运维/测试缺陷 | 静态资源缓存版本与服务重启未纳入交付门 | 已修局部：版本 bump + 主动重启；需自动校验 hash |

## B. 商品事实、计划审批与发布流程（21–40）

| # | 事件 | 类型 | 根因 | 状态 / 应对 |
|---:|---|---|---|---|
| 21 | “应用选择并审查售价”禁用且无解释 | 产品缺陷 | 选择/预检忙碌或无变化状态没有动作提示 | 已补禁用提示；需真实状态矩阵 |
| 22 | 售价选择完成后页面没有任何下一步按钮 | 产品缺陷 | 阶段卡片各自正确，但没有全页 next-action 仲裁 | 当前核心缺口 |
| 23 | 发布计划审批框因旧文案签名失效而禁用，无恢复入口 | 产品缺陷 | 后端安全门正确，前端只显示 blocker | 已修：结构化 recovery actions |
| 24 | 文案快照绑定 3 个规格，当前批准事实只剩 1 个规格 | 安全门 | immutable input signature 正确检测漂移 | 安全阻断；恢复流程已补 |
| 25 | 重新生成并采用 EN MASTER 后审批仍显示“正在读取” | 产品缺陷 | 异步 busy 状态释放依赖后续 incidental render | 已修：成功响应前清 loading、finally 重算控件 |
| 26 | “计划预览已形成”被误判成“正在读取或提交” | 产品缺陷 | disabled hint 根据文案猜状态 | 已修：显式 `data-disabled-reason` |
| 27 | Chromium 测试只验证恢复按钮出现，未点击采用到审批解锁 | 测试缺陷 | 测试停在组件存在，没有覆盖状态迁移终点 | 已补完整生成→采用→解锁→勾选 |
| 28 | `release plan is not ready for approval` 直接暴露 | 产品缺陷 | 后端 blocker 未结构化为下一步 | 已部分修；所有 blocker 仍需 action code |
| 29 | 妙手 COMMON 无 approved spec label 治理字段 | 合同缺陷 | 旧草稿字段模型不足以表达批准规格名 | 已做专用治理修复 |
| 30 | 妙手同步失败后只有“修复后重试”，无具体修复点 | 产品缺陷 | adapter error 未映射字段/阶段 | 待收敛：字段级差异与只读修复预览 |
| 31 | 一键发布卡片禁用，但页面没有可点击的前置动作 | 产品缺陷 | 发布门依赖多阶段，UI 未选择唯一下一步 | 当前问题 |
| 32 | 执行显示 `1/12` 回读、`6` 项需修复，用户无法判断是否完成 | 产品缺陷 | 聚合统计没有 disposition 分组和下一步 | 已部分改善账本；仍需任务化动作 |
| 33 | immutable revision/plan 冲突被解释为“版本冲突” | 产品缺陷 | 技术一致性错误未翻译成用户方案 | 待收敛：刷新、废止、successor 三选一 |
| 34 | 用户希望“清缓存重做”，但不可变账本不能安全删除 | 产品/治理冲突 | UI 没有提供正式 successor/restart 流程 | 待收敛：产品化“建立新计划” |
| 35 | Offer 3838600989 没有可见重新发布入口 | 产品缺陷 | 失败/提交/待验收状态均被 generic publish 隔离 | 待收敛：按 disposition 给专用动作 |
| 36 | 本地曾显示发布完成，但店铺后台没有商品 | 产品/账本缺陷 | 把妙手 accepted/draft 当作平台成功或缺少官方回读 | 已建立 ACCEPTED_UNVERIFIED；旧数据需人工验收 |
| 37 | MX 已成功、GB 未上架，但页面无法解释差异 | 产品缺陷 | API-less 目标只有提交事实，没有店铺级回读/验收入口 | 待收敛：逐目标 manual acceptance |
| 38 | 当前 plan 已批准、COMMON 已成功，但 run 为 RUNNING、其余 11 项 PENDING | 当前状态 | V1 run 已创建，`publish_ready=false`，页面只说等待条件 | 当前问题，需只读仲裁 automation/generic 所有权 |
| 39 | 自动驾驶状态面板与旧发布卡片各自渲染，可能给出冲突指引 | 产品架构缺陷 | 两套控制面没有统一 next-action projection | 待收敛：单一 workflow action API |
| 40 | generic publish 正确隔离后，若 automation 不可用会形成死路 | 产品架构缺陷 | 安全隔离有测试，替代路径可达性没有测试 | 当前最高优先级 |

## C. Shopee 单目标恢复、官方证明与对账（41–80）

| # | 事件 | 类型 | 根因 | 状态 / 应对 |
|---:|---|---|---|---|
| 41 | generic publish 将全部 FAILED 重置 PENDING | 合同缺陷 | 旧 retry loop 不区分单目标证据 | 已修：FAILED/对账目标隔离 |
| 42 | 03 adapter 无法安全暴露 route/atomic claim | 架构缺陷 | HTTP/store seam 归 00 所有 | 已修：动态 target-scoped seam |
| 43 | TargetScoped request 没有 immutable prepared action | 合同缺陷 | proof 只绑定摘要，不绑定将写字段 | 已修：planned command v2 |
| 44 | preview 可用，claim 后占位 primitive 才失败 | 产品/账本缺陷 | capability 在 claim 后发现 | 已修：缺 action 必须 claim 前 409 |
| 45 | 本地 global map 被当成官方 identity authority | 安全缺陷 | candidate 与 official proof 混用 | 已修：官方 merchant GET 唯一确认 |
| 46 | NORMAL 扫描结束后提前 return，未扫 UNLIST/BANNED | 产品缺陷 | 状态外层循环早退 | 已修并补三状态分页 |
| 47 | Shopee top-level error 被当成空列表 | 安全缺陷 | parser 对错误响应 fail-open | 已修：error/schema fail-closed |
| 48 | 游标缺 seen/max-page，可能死循环 | 鲁棒性缺陷 | 只检查递增，不限制终止 | 已修：seen cursor + max pages |
| 49 | base/model 批次返回不完整仍可能通过 | 安全缺陷 | 未核对 requested/returned IDs | 已修：batch completeness |
| 50 | global model parser 使用错误字段名 | 产品缺陷 | 猜测 `model_list/model_sku`，真实为 `global_model/global_model_sku` | 已修 captured fixture |
| 51 | global image parser 不兼容 `image.image_url_list` | 产品缺陷 | 未基于真实响应 shape | 已修 captured fixture |
| 52 | official row 未精确匹配 candidate global_item_id | 安全缺陷 | 只证明“有唯一行” | 已修 exact ID |
| 53 | global_model_id/tier_index 空值或非整数可通过 | 安全缺陷 | shape gate 不完整 | 已修 strict numeric/list |
| 54 | proof `approved_master_digest` 错填 planned command digest | 审计缺陷 | semantic evidence 字段含义混淆 | 已修 |
| 55 | 物流兼容函数完全未使用 parcel | 安全缺陷 | 只筛 enabled/excluded | 已修复用 parcel limits |
| 56 | VN 先过滤 50052 再证明其不存在 | 审计缺陷 | 派生后自证 | 已修：保留官方集合与规则结果 |
| 57 | create_publish_task body 使用错误字段 `logistics_channel_id` | 产品缺陷 | 未锁定官方 fixture | 已修为 `logistic_id` |
| 58 | task result success 被当作完整官方回读成功 | 产品/账本缺陷 | 缺 regional item/model GET | 已修：task 后 bounded regional reads |
| 59 | task polling/terminal shape 不完整 | 鲁棒性缺陷 | 单次 GET 即终态 | 已修 bounded poll |
| 60 | regional item 未反解 global linkage | 安全缺陷 | 无法证明由批准 global master 派生 | 已修 merchant resolve |
| 61 | regional original price/currency 未做 Decimal exact | 安全缺陷 | 字符串/当前价混用 | 已修 original_price + currency exact |
| 62 | regional enabled logistics 未与 proof-bound set exact | 安全缺陷 | 只检查数量/包含 | 已修 exact set/digest |
| 63 | rehost URL 不同被错误当成图片内容/顺序不一致 | 合同缺陷 | URL identity 与视觉/稳定 ID 混淆 | 已拆 observation contract |
| 64 | 平台自动翻译被要求与英语母版 exact | 合同缺陷 | 批准事实与平台派生观察混淆 | 已拆 translation observation |
| 65 | 03 读取不存在的 outcome keys，warning 永远不能成功 | 产品缺陷 | 00/03 字段名不一致 | 已修并补 cross gate |
| 66 | POST 后普通异常丢失外部写证据 | 账本缺陷 | 只捕获 typed error | 已修 full post-dispatch wrapper |
| 67 | adapter broad catch 把 pre-POST 异常误报成已写 | 账本缺陷 | catch 范围跨越 dispatch boundary | 已修 pre-submit typed error |
| 68 | `derived_translation_status` 被持久化为 `SUCCEEDED` | 审计缺陷 | aggregate outcome 覆盖 observation status | 已修保存 warning/observed |
| 69 | Shopee 图片 URL rehost 导致 global master digest 假漂移 | 合同缺陷 | source lineage digest 被当 official-recomputable | 已修 copy digest + image ID observation |
| 70 | BANNED 零商品响应缺 `item` 被判 invalid | 兼容缺陷 | 未接受官方合法 zero-list shape | 已修严格零形状兼容 |
| 71 | proof→runtime 未绑定 model_id/tier，TOCTOU 可漂移 | 安全缺陷 | 只重验 image snapshot | 已修 exact model/tier |
| 72 | official title/description 非字符串被 `str()` 后接受 | 安全缺陷 | digest helper隐式转换 | 已修 built-in string/nonempty |
| 73 | preview 未展示 global image warning/manual review | 产品缺陷 | redacted summary 缺 observation | 已修状态/scope/rules/digest |
| 74 | durable receipt 缺 source_copy/global image facts | 审计缺陷 | 只保存 aggregate status | 已修 redacted receipt fields |
| 75 | GET-only close 找不到 eligible operation | 状态/合同阻断 | 原 operation/proof identity 与新 close gate 不一致 | 安全阻断；需新治理动作 |
| 76 | model 列表混入非 mapping 行仍可通过 | 安全缺陷 | 先 filter 后验证唯一性 | 已修：全列表 strict shape |
| 77 | price_info 混入非 mapping 行仍可通过 | 安全缺陷 | 只筛目标币种 | 已修：每行验证 |
| 78 | logistics `enabled=1` 被当 disabled 忽略 | 安全缺陷 | 未要求 literal bool | 已修 strict bool/int |
| 79 | 额外币种价格 `NaN/inf/dict` 未逐行校验 | 安全缺陷 | Decimal 只解析目标币种 | 已修每行 finite positive Decimal |
| 80 | legacy proof 物流 raw26/unique24/current12 无法 exact close | 安全门 | 历史重复列表真实进入命令，不能事后去重重签 | 安全阻断；需 successor action |

## D. Ozon、库存与 Seaya（81–90）

| # | 事件 | 类型 | 根因 | 状态 / 应对 |
|---:|---|---|---|---|
| 81 | Ozon 当前计划没有 desired_stock_quantity | 安全门 | 库存数量属于未批准业务事实 | 安全阻断 |
| 82 | legacy Ozon 默认 stock=50 | 安全缺陷 | 硬编码值被当业务决策 | 已禁止默认值 |
| 83 | Ozon product/offer ID 曾硬编码 | 合同缺陷 | fixture 值进入 production proof | 已改由 official proof/plan identity |
| 84 | Ozon 状态 parser 读取顶层而真实语义在 nested statuses | 产品缺陷 | response shape 假设错误 | 已在新 first-attempt proof 修复 |
| 85 | 未证明唯一 active non-KGT warehouse | 安全门 | warehouse policy 缺官方事实 | 安全阻断 |
| 86 | 本地记录与“existing product 已创建”事实冲突 | 数据阻断 | 本地审计滞后于外部状态 | 需官方只读 snapshot |
| 87 | Seaya 环境变量名称/作用域不明确 | 外部阻断 | 禁止枚举且配置命名不统一 | 已定位部分变量；需配置合同 |
| 88 | Seaya AppId/AppSecret/UserKey 请求统一返回 code -1 | 外部阻断 | 应用与 OMS tenant/UserKey 绑定或签名不成立 | 待 Seaya 支持处理 |
| 89 | stock/getList 无 data，不能解释为零库存 | 安全门 | 非成功业务响应没有 snapshot | 正确保留 BLOCKED_INVENTORY |
| 90 | stock 记录无 active/non-KGT 仓信息；缺货单也无直接 API | 外部能力缺口 | 必须 join order/stock/warehouse，当前合同不足 | 待官方 schema/权限 |

## E. 批准后自动驾驶、TikTok 与妙手（91–96）

| # | 事件 | 类型 | 根因 | 状态 / 应对 |
|---:|---|---|---|---|
| 91 | Autopilot V2 只终结 automation ledger，未写 canonical ReleaseStore | 架构缺陷 | 缺 typed receipt bridge | 已修 V3 dual-ledger bridge |
| 92 | COMMON 在 run 创建后可能绕过 overwrite 治理 | 安全缺陷 | legacy helper 与 worker 所有权冲突 | 已保持 BLOCKED_CAPABILITY |
| 93 | 状态 UI 曾轮询错误 endpoint/假定不存在的 feature flag | 产品缺陷 | preview/status schema 未锁定 | 已修 fixture 与轮询路径 |
| 94 | API-less TikTok 初版 scaffold 使用不存在字段和非 typed receipt | 架构缺陷 | 未接现有 dynamic seam | 已删除重建 |
| 95 | malformed success/non-mapping publish reply 可能丢失两类写 | 账本缺陷 | post-dispatch shape 未 typed 化 | 已修 truthful reconciliation |
| 96 | detail update 已写，随后 audit 异常却记为 pre-submit 0 write | 严重账本缺陷 | dispatch boundary 放错位置 | 已修 exact one-write reconciliation |

## F. 测试与执行基础设施（97–100）

| # | 事件 | 类型 | 根因 | 状态 / 应对 |
|---:|---|---|---|---|
| 97 | direct adapter 测试全绿，但真实 SQLite/worker 双账本缺失 | 测试缺陷 | 组件测试替代跨层状态测试 | 已为关键路径补 cross tests；需制度化 |
| 98 | root pytest 收集 live utility scripts，因缺秘密配置失败；一次 full 在 24% 后终止 | 测试基础设施 | 正式测试边界不清、后台进程不可靠 | 待收敛统一 test manifest/runner |
| 99 | WinError10053、Windows 长路径、Node 不在 PATH、catalog backup 等宿主波动 | 测试基础设施 | 环境依赖未预检/隔离 | 待建设统一 runtime、短 basetemp、flake 分类 |
| 100 | 当前测试按“功能点/工单”验收，没有“页面任何状态必须存在唯一下一步”的全局不变量 | 测试框架缺陷 | fixture 驱动、happy-path 终点过早、真实状态组合未生成 | 当前最高优先级：建立全状态图 + no-dead-end browser gate |

## 汇总

| 类别 | 数量 |
|---|---:|
| 内容与 AI 图片工作室 | 20 |
| 商品事实、计划审批与发布流程 | 20 |
| Shopee 单目标恢复与对账 | 40 |
| Ozon、库存与 Seaya | 10 |
| 自动驾驶、TikTok 与妙手 | 6 |
| 测试与基础设施 | 4 |
| **合计** | **100** |

## 反复出现的根因

1. **安全门有了，但没有对应的产品恢复动作。**
2. **测试停在组件存在或 HTTP 200，没有走到用户最终能继续。**
3. **fixture 由实现反推，未覆盖真实官方 response 的混合/畸形 shape。**
4. **跨异步边界的状态和写入事实没有统一状态机。**
5. **旧 generic flow、target-scoped flow、autopilot flow 同时存在，UI 没有唯一所有权。**
6. **真实浏览器、真实本地服务、真实持久账本没有作为同一个验收门。**
7. **服务重启、缓存版本、运行时路径和测试临时目录属于交付流程之外。**

## 下一步测试框架必须回答的问题

- 当前页面的唯一下一步是什么？
- 这个下一步是否可见、可点击、中文可理解？
- 点击后是本地读取、状态写入还是外部写入？
- 请求成功、失败、超时、响应丢失后是否仍存在下一步？
- reload、服务重启、另一标签 revision 变化后是否仍一致？
- canonical store、automation ledger、UI projection 是否表达同一真相？
- 所有被禁用控件是否有准确原因，而不是推断文案？
- 未经官方回读的提交是否绝不会显示为成功？
