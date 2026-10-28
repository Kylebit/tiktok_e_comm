# 自动上品两周工作审计交接包

审计日期：2026-08-03

审计范围：2026-07-20 至 2026-08-03

仓库：`C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm`

审计基线：`431d868 Isolate Shopee and Ozon publish paths`

主要复现商品：`offer_id=3846511157`
用途：交给独立 agent 做只读代码、需求、测试和过程审计。本文不是修复方案的批准，也不授权任何真实平台写入。

## 0. 给独立审计 agent 的最短说明

请不要从“现有测试大多通过”推断产品可用。当前正式页面在提交 `431d868`、服务 PID `31116` 下进行真实按钮测试时，TikTok、Shopee 全球商品和 Ozon 三个平台均失败：

| 平台 | 当前页面结果 | 后端安全日志 |
| --- | --- | --- |
| TikTok | 发布失败；页面未展示具体原因 | `miaoshou_tiktok_publish_rejected target=tiktok:GB attempt=1 code=fail` |
| Shopee 全球商品 | 发布失败：未找到 TK `[VN]` 对齐码 `0959` | `platform_publish_failed platform=SHOPEE_GLOBAL offer_id=3846511157 reason=未找到 TK [VN] 对齐码 0959` |
| Ozon | 发布失败：官方 API 未接受导入 | `platform_publish_failed platform=OZON offer_id=3846511157 reason=Ozon official API did not accept the import` |

这三个结果是在本地测试声称相关测试通过之后产生的。因此本次审计首先要回答：为什么现有自动化测试、独立测试线程和交付标准没有阻止一个三平台均不可用的版本被描述为“完成”。

## 1. 需要纠正的近期结论

### 1.1 GB 并未在最近一轮被真实修复并验收

- `8e032ad Restore GB category preparation` 是此前的代码修复提交。
- `431d868` 没有修改 TikTok/GB 生产代码，只修改 Shopee、Ozon、server 路由和测试。
- 最近一轮只跑了 GB fixture/冻结测试，没有执行当前环境真实 GB 发布。
- 当前后端日志显示 GB 请求被妙手拒绝：`code=fail`。

因此准确状态是：**GB 有既有修复代码和离线测试证据，但当前版本没有真实成功验收，且当前实测仍失败。**

### 1.2 “三条路径没有剩余阻断”属于过度结论

`431d868` 前后的独立测试给出了：

- 相关测试：109 passed；
- GB 冻结专项：15 passed；
- 全仓：1995 passed、4 skipped、8 failed；
- 真实平台写入：0。

上述证据只能证明 fake transport、fixture、handler 和部分代码合同成立，不能证明真实妙手、Shopee 或 Ozon 接受当前商品。真实点击随后三平台全部失败。

### 1.3 当前需求文档不是单一事实源

`docs/release_v2/PRODUCT_REQUIREMENTS.md` 的 PRD-004 仍写“三个平台统一通过妙手”，但后续 Kyle 已明确：

- TikTok：妙手；
- Shopee：妙手，仅 CNSC 全球商品；
- Ozon：恢复此前官方 Ozon API 直接发布；
- 三个平台完全隔离。

当前 `431d868` 已按“官方 Ozon API”实现，因此 PRD-004 已过期。审计时不可把过期文档当作当前需求。

## 2. 两周变更规模

从 2026-07-20 至 2026-08-03，主分支共有 337 个提交，其中按提交标题筛选，与 release/publish/oneclick/collectbox/TikTok/Shopee/Ozon/source/identity/category/price/approval/UI/status 等相关的提交约 250 个。

按日期的提交数量：

| 日期 | 提交数 |
| --- | ---: |
| 2026-07-20 | 8 |
| 2026-07-21 | 11 |
| 2026-07-22 | 11 |
| 2026-07-25 | 34 |
| 2026-07-26 | 13 |
| 2026-07-27 | 32 |
| 2026-07-28 | 55 |
| 2026-07-29 | 12 |
| 2026-07-30 | 66 |
| 2026-07-31 | 24 |
| 2026-08-01 | 35 |
| 2026-08-02 | 21 |
| 2026-08-03 | 15 |

高频修改热点：

| 文件 | 两周累计增删行（近似） | 被提交触碰次数 |
| --- | ---: | ---: |
| `modules/products/server.py` | 12,887 | 72 |
| `web/static/product_workspace.js` | 12,020 | 70 |
| `tests/browser/release_ux_contract.js` | 9,444 | 58 |
| `shared_platform/oneclick_release_controlplane.py` | 8,820 | 18 |
| `modules/miaoshou/oneclick_release.py` | 6,017 | 38 |
| `modules/products/release_adapters.py` | 5,918 | 18 |
| `shared_platform/release_store.py` | 5,890 | 20 |
| `modules/sourcing/new_product_workbench.py` | 4,914 | 22 |
| `tests/test_oneclick_miaoshou_direct_store.py` | 4,693 | 35 |

这不是正常的“小步稳定收敛”特征。核心 server、前端状态和渠道 adapter 在短时间内被连续重写，导致需求、测试和实现很难保持同一版本语义。

复核命令：

```powershell
git log --since="2026-07-20" --date=iso-local --pretty=format:"%h|%ad|%s" --reverse
git log --since="2026-07-20" --numstat --format=""
```

## 3. 时间线与反复出现的问题

### 阶段 A：内容、图片与审批基础（07-25 至 07-29）

用户可见问题：

1. 保存来源图决定后，身份参考和主参考选择被清空。
2. 删除的来源图片刷新后自动恢复。
3. 视频决定选择“不使用”，保存后又变回“保留”。
4. AI 分镜因身份参考、内容包未创建或代理网络错误失败，页面只显示英文内部原因。
5. AI 标题/描述规则混乱，Shopee English description 校验阻断标题生成。
6. 内容包显示未审批，但页面没有最终确认按钮。
7. 大量 disabled checkbox/button 没有可见原因。

代表提交：

- `0d0302f` 显示 AI planning blocker；
- `19070d3` 原子保存来源身份参考；
- `bb3e622` 拒绝不一致身份保存；
- `62e0598` 保留来源身份选择；
- `d0107cf` 解释缺失 AI review package；
- `891c298` 简化来源审核与文案 fallback；
- `53e76a5` 解释标题 fallback 和 storyboard 网络错误；
- `ade8d6a` 即时持久化来源图移除；
- `0cf9cec`、`c286717` 统一内容最终审批并补入口。

反复模式：修复只覆盖成功 fixture 或单个字段，未覆盖“编辑 -> 保存 -> 服务端重载 -> 页面恢复”的完整状态循环。

### 阶段 B：不可变发布计划与复杂控制面（07-26 至 07-30）

用户可见问题：

1. “应用选择并审查售价”“批准当前发布计划”等按钮多次不可点击。
2. 页面显示“正在读取/更新本地状态”，用户无任何可操作入口。
3. ReleasePlan 因内容、标题、类目、库存、COMMON、revision 等多种 blocker 被锁死。
4. 上一次失败、未知、对账或人工验收状态影响下一次发布。
5. 用户无法理解 READY、BLOCKED、RECONCILIATION_REQUIRED、WAITING_MANUAL_ACCEPTANCE 等内部状态。
6. disabled 控件虽有后台理由，但 UI 未呈现或跳转按钮无作用。

代表提交：

- `5d57577` governed omnichannel release v1；
- `cf9498a` API-less exactly-once；
- `082cf4e` target-scoped release seam；
- `8e4d4b2` one-click release control plane；
- `727bbc9` shared release ledger hardening；
- `34b19cd` persist approved Shopee global plan；
- `99d5a18` finalized governed one-click release；
- `ca063dc` one-click v2 UX；
- `b725928` isolate Shopee from one-click batch。

反复模式：为了避免重复写入和未知结果，系统逐步增加 immutable plan、job、target、dependency、claim、receipt、manual acceptance、reconciliation 和 recovery；但用户当前目标只是“点击后向平台提交”。安全状态机的复杂度远高于当前产品验证阶段的需求，且 UI 没有把复杂度隐藏起来。

### 阶段 C：Shopee 官方 API 类目与区域发布探索（07-27 至 07-31）

用户可见问题：

1. Shopee 全球商品标题错误、规格名错误、区域站点发布不完整。
2. 官方候选类目读取、属性、品牌、卖家位置、仓库、价格、图片回读依次成为 blocker。
3. “核对并批准 Shopee 全球商品方案”按钮无反应或没有可完成入口。
4. 官方 API token/权限、candidate shape、global-to-local 发布差异导致流程长期阻断。
5. 用户最终决定：Shopee 不再通过官方 API 直接区域发布，改走妙手；当前只要求发布一个 CNSC 全球商品。

代表提交：

- `fc6c0e8` harden global-to-local publication；
- `38c5cd3` local prices/logistics preflight；
- `19155d9` exact local price repair；
- `5ca63b4` approved Shopee global plan；
- `40fb85b` official new global candidate observer；
- `7e1d8b3` category creation decision；
- `82dfd5c` official global candidate selection；
- `7fc5c6b` lock no-brand and China warehouse；
- `b725928` isolate Shopee from batch；
- `77c86fa` later separate TikTok/Shopee collectbox execution；
- `431d868` latest direct Shopee-global handler。

反复模式：官方 API 路线、妙手路线、existing global update 和 new global create 在同一产品周期内交替，导致旧测试、文档和 helper 仍引用不同身份模型。当前真实失败“未找到 TK [VN] 对齐码 0959”表明 Shopee 全球商品实现仍依赖了 TikTok/VN 对齐数据，违反“Shopee 与 TikTok 独立”的产品意图。

### 阶段 D：TikTok 妙手采集箱、六站点价格和类目（07-31 至 08-03）

用户可见问题：

1. 重新导入后只导入一个 TikTok 国家，而不是计划选中的全部国家。
2. 页面显示“待人工确认，不能重试”，与允许新批次重试的决定冲突。
3. 妙手草稿可重复存在，但系统曾把重复/未知当作停止条件。
4. MX/GB 价格一度正确，修改 SEA 后又退回默认小价格；之后反向出现 MX/GB 回归。
5. SEA 四国多次出现本地展示价正确、妙手货源价仍为 CNY 10 或站点价格未持久化。
6. GB 预发布类目、mandatory attributes、标题保留策略反复改变。
7. “上次结果”被展示或用于下一次准入，用户明确否认这一需求。
8. TikTok 最终出现“2 个妙手接受、3 个结果未知、1 个未执行”，随后简化为按钮只看妙手响应。
9. 当前最新真实日志仍为 `tiktok:GB ... code=fail`，UI 只显示通用“TikTok 发布失败”。

代表提交（高密度往返）：

- `5c046d6` typed collectbox claim service；
- `d4ef0f4` collectbox import UI；
- `4c3f439` fresh reimport batches；
- `ef65bb0` serialize reimport requests；
- `3c0fbf1` abandoned lease recovery；
- `a742bbe` verify/repair TikTok drafts per target；
- `7550321` price/category binding；
- `47719b4` legacy category backfill；
- `e0b31a4` SEA site drafts；
- `ddafe6e` per-site price/category drafts；
- `9be2e88` exact draft persistence；
- `ad56a94` reject stale GB drafts；
- `dbbeadc`、`bf637cd`、`92a1288` SEA price persistence/readback/identity；
- `d7b97b0` SEA shop-draft price；
- `f9fe643` restore SEA site-draft price path；
- `21a2e41` narrow GB waiver tests；
- `41f2e68` GB direct submit；
- `70e5e61` allow terminal GB outcome to release TikTok；
- `88309c7` bridge collectbox drafts to publish；
- `0021fc5` publish rate-limit handling；
- `8e032ad` restore GB category preparation。

反复模式：MX/GB 与 SEA 使用不同草稿/店铺/站点 API 形态，却多次共享 helper、expected shape 或 validation gate。对一个区域的修复改变了另一区域路径；测试常用固定 fake shape，没有冻结真实妙手请求参数和响应形态。

### 阶段 E：三平台按钮简化与最新失败（08-02 至 08-03）

用户最终选择：

1. TikTok、Shopee、Ozon 三个按钮和代码必须完全隔离。
2. 每次点击是一次新的显式尝试，历史结果不阻止下一次。
3. 不要人工验收、自动对账或官方回读作为按钮完成条件。
4. 妙手/API 返回接受即可结束当前按钮动作；失败可再次点击。
5. Shopee 只发布全球商品，不发布区域站点。
6. Ozon 后续又明确改为此前成功过的官方 Ozon API 直接发布。

代表提交：

- `c5601e8` isolate three release actions；
- `0c2f508` fix batch activation；
- `10ed5fe` isolated controls and GB waiver；
- `4a9d570` freeze convergence stage zero；
- `8608612` release v2 docs；
- `36a41eb` isolate terminal history from current attempts；
- `6fb433e` simplify buttons to final Miaoshou result；
- `4afa5f3` final Shopee result；
- `431d868` isolate Shopee and Ozon paths。

当前结果：三个按钮在 `431d868` 上全部失败，说明“代码隔离”和“真实渠道可用”是两个不同验收维度；前者的测试通过不能证明后者。

## 4. Bug 分类清单

### 4.1 状态持久化与页面恢复

- 身份参考、主参考、来源图片删除、视频决定保存后恢复旧值。
- revision/CAS 成功后重载读取了旧字段或错误端点。
- 页面临时 DOM 状态与服务端 canonical 状态不一致。
- service restart、browser cache key、旧 JS asset 造成“已修复代码未在页面生效”的假象。

### 4.2 无下一步与不可点击控件

- checkbox/button disabled 但无解释。
- blocker 给了跳转按钮但目标区域不存在或 click handler 不工作。
- 页面说“等待状态更新”但轮询没有完成条件。
- 多个确认按钮表达同一个业务决定。

### 4.3 历史状态污染当前尝试

- 上一次失败/未知/人工验收/对账限制下一次。
- 旧 job/target 复用、重置而非创建新 attempt。
- UI 把历史结果放在当前执行主区域。
- 用户已明确：历史只显示，不参与准入。

### 4.4 身份模型混用

- `source_offer_id`、`source_item_code`、seller SKU、model SKU、TK 对齐码、global item ID、detail ID 被跨层复用。
- 当前 Shopee 全球失败仍查找 `TK [VN] 对齐码 0959`，是最直接的跨平台身份耦合证据。
- GB/SEA/MX 草稿身份和 readback shape 不同，但 helper/fixture 曾共享。

### 4.5 价格与类目写入漂移

- ReleasePlan 价格、妙手货源价、本地展示价、店铺草稿价、站点价没有单一字段映射表。
- CNY 10 默认值多次重新出现。
- SEA 和 MX/GB 修复互相回归。
- 类目决策、预发布类目、mandatory attributes 和页面显示并非同一字段。

### 4.6 渠道边界漂移

- Shopee 官方 API 与妙手 global path 多次切换。
- Ozon 在“统一妙手”和“官方 API”之间切换，文档未同步。
- COMMON/TikTok/Shopee/Ozon 曾存在隐式依赖，之后又要求完全隔离。

### 4.7 错误可观测性不足

- TikTok 当前 UI 只显示“发布失败”，后台才有 `GB code=fail`。
- Ozon UI 仅有汇总英文，缺官方响应的安全错误类别、请求阶段和 task receipt。
- 此前多次用 generic exception 映射为“结果未知”或“预提交失败”，实际写入边界不清。
- 日志体系直到后期才开始添加安全摘要，且没有统一 attempt/request ID 串联按钮、HTTP、adapter 和平台响应。

## 5. 为什么测试没有拦住这些问题

### 5.1 测试证明的是 fake 合同，不是真实可发布性

当前大量测试替换了：

- 妙手 transport；
- Shopee/Ozon client；
- approved payload；
- current database；
- browser API response。

这类测试能证明函数按假数据工作，却没有验证当前 offer 的真实身份、真实 token、真实类目、真实价格和真实平台响应。

### 5.2 浏览器测试也是 fixture-first

现有 Chromium 测试能够验证按钮可见、可点击、loading/failure 文案和 viewport，但通常拦截网络并返回预设 payload。它不能证明按钮后面的正式 server 使用当前配置能调用成功。

### 5.3 缺少纵向真实链路门禁

没有一个强制门禁同时覆盖：

```text
真实页面按钮
 -> 当前服务 HTTP handler
 -> 当前 offer/ReleasePlan
 -> production adapter factory
 -> 当前凭据和真实只读预检
 -> 受控平台写入
 -> 平台接受回执
 -> 页面最终状态
```

因此可以出现“109 个相关测试通过，但三个按钮全失败”。

### 5.4 测试之间存在过期语义冲突

全仓在 `431d868` 为 1995 passed、4 skipped、8 failed。剩余 8 项是旧 one-click MX/HomeBloom 边界测试，期待旧控制面行为。此前还有 GB 测试要求保留 vendor 类目，与后来要求写入 approved category 的合同冲突。

测试不只是缺少覆盖，也包含多个历史需求版本。为让当前实现通过而修改旧测试，如果没有明确的需求 supersession 记录，会失去回归门禁的可信度。

### 5.5 “测试通过”被错误提升为“修复完成”

交付语言多次把以下证据混为一谈：

- 单元测试通过；
- handler 测试通过；
- fake browser 测试通过；
- production service 启动；
- 真实平台接受；
- 店铺后台最终商品存在。

这些必须是六个独立结论，不能互相替代。

## 6. 当前代码地图

| 功能 | 当前入口 |
| --- | --- |
| TikTok 按钮 handler | `modules/products/server.py::_start_tiktok_release`（约 9281 行） |
| Shopee approved facts | `modules/products/server.py::_approved_shopee_global_publish_facts`（约 9289 行） |
| Ozon approved facts | `modules/products/server.py::_approved_ozon_publish_facts`（约 9353 行） |
| 安全错误摘要 | `modules/products/server.py::_safe_platform_publish_error`（约 9418 行） |
| Shopee 全球按钮 | `modules/products/server.py::_start_shopee_global_release`（约 9441 行） |
| Ozon 按钮 | `modules/products/server.py::_start_ozon_release`（约 9522 行） |
| Shopee global master update | `modules/shopee/publish.py::update_global_master`（约 1328 行） |
| Shopee global ensure | `modules/shopee/publish.py::ensure_global_master`（约 1436 行） |
| Shopee publish main | `modules/shopee/publish.py::publish_match_key`（约 1856 行） |
| Ozon direct import | `modules/ozon/migrate_batch.py::migrate_one`（约 28 行） |
| TikTok/妙手 prepare+dispatch | `modules/miaoshou/oneclick_release.py` |

注意：行号会随修复变化，审计 agent 应使用 `rg -n` 重新定位。

## 7. 当前有效业务决定与过期决定

### 7.1 当前有效（以最新对话为准）

- 三个平台完全独立。
- 每次点击可创建新的尝试；旧结果不阻断。
- TikTok 通过妙手发布计划选中的 TikTok 目标。
- Shopee 通过妙手，只发布一个 CNSC 全球商品。
- Ozon 通过官方 Ozon API 直接提交。
- 当前阶段不把人工验收、官方回读、对账作为按钮完成条件。
- API 明确接受则该按钮显示成功；明确拒绝则显示安全、具体、可重试原因。
- 不因 GB 的类目/回读差异阻断其他 TikTok 站点；GB 当前仍需单独真实验证。

### 7.2 已被后续决定取代

- 三个平台统一走妙手。
- Shopee 官方 API 直接区域发布。
- Ozon 必须等待官方回读/库存条件后才可完成。
- 上次未知/对账必须处理后才能再发。
- COMMON 是所有目标的依赖。
- 一个“一键发布所有店铺”按钮统一处理全部平台。
- TikTok、Shopee、Ozon 共用一个 job 或全局 busy 锁。

### 7.3 尚未写成稳定合同的问题

- “妙手接受”所需的最小成功字段是什么。
- Ozon “官方 API 接受”的精确成功 envelope 和可重试错误分类。
- Shopee 新 global 与 existing global 更新是否允许依赖任何 TikTok 对齐数据；按最新意图应禁止。
- GB `code=fail` 时是否直接继续其他 TikTok 目标；按最新意图应继续，并单独报告 GB。
- 真实发布验收使用哪个专用商品、允许产生多少草稿/全球商品/导入任务。

## 8. 方法层面的根因

### 8.1 在需求仍快速变化时先建设了重型通用控制面

系统在渠道最小 API 合同尚未稳定前，就引入不可变计划、依赖、claim、ledger、receipt、reconciliation、manual acceptance、recovery 和统一 next-action。后来用户要求简化，导致大量逆向拆除和兼容层。

### 8.2 没有把“渠道适配器可用性”作为先决条件

正确顺序应是：先用一个固定测试商品分别打通 TikTok、Shopee global、Ozon 的最小真实调用，再考虑统一页面和自动化。实际顺序多次相反：先做 UI/状态机，再发现真实 API 参数、身份和权限不成立。

### 8.3 本地权限与真实发布权限没有形成正式验收阶段

开发默认外部写入为 0，这是正确的安全原则；问题在于交付流程没有强制一个由 Kyle 明确授权、03 单写者执行的 canary 发布阶段。因此开发者既不能在普通测试里真实写，又在没有真实写证据时宣布功能完成。

### 8.4 一个巨大 server/UI 文件承载过多责任

`modules/products/server.py` 和 `product_workspace.js` 两周内分别被触碰 72、70 次。HTTP、业务校验、状态机、渠道调用、错误映射和 UI 渲染高度集中，使一个平台的小改动容易影响其他平台。

### 8.5 对话决定没有及时固化为不可歧义的 supersession 记录

需求在对话中多次改变，但代码、测试和文档没有同一事务更新。“当前决定”“实验想法”“已撤销设计”混在一起，后续 agent 会从旧测试或旧文档恢复已经取消的行为。

## 9. 建议的后续工程方法

### 9.1 先暂停继续扩展统一状态机

在三个最小发布器分别获得真实成功回执之前，不新增：

- 自动对账；
- 自动重试；
- 跨平台依赖；
- 促销活动；
- 通用恢复控制面；
- 新的统一状态 enum。

### 9.2 建立三条最小独立发布器

```text
TikTokPublisher.publish(approved_plan, target)
ShopeeGlobalPublisher.publish(approved_plan)
OzonPublisher.publish(approved_plan)
```

每条发布器只返回统一但很小的结果：

```json
{
  "accepted": true,
  "platform": "TIKTOK",
  "target": "tiktok:GB",
  "request_digest": "sha256...",
  "provider_code": "redacted-code",
  "safe_message": "..."
}
```

不得读取另一个平台的身份字段或状态表。

### 9.3 三层验收，禁止跨层宣称

1. **离线合同测试**：0 外部写，验证请求字段、异常分类、平台隔离。
2. **真实只读预检**：当前 offer、当前配置、当前凭据、目标对象和类目/价格身份存在。
3. **受控 canary 写入**：Kyle 明确授权一个 offer/测试商品，03 单写者执行，保存安全回执。

交付报告必须分别写：L1 通过、L2 通过、L3 通过/未执行。L3 未执行时只能说“代码候选”，不能说“发布已修复”。

### 9.4 增加操作日志的最小关联字段

每次按钮点击生成 `attempt_id`，所有安全日志至少包含：

- `attempt_id`；
- `offer_id`；
- `platform`；
- `target`；
- `stage`：VALIDATE / PREPARE / SUBMIT / PARSE；
- `request_digest`；
- `provider_http_status`；
- `provider_code`；
- `accepted`；
- `safe_reason`；
- `duration_ms`。

严禁 token、Authorization、完整 URL query、原始响应和商品敏感正文。

### 9.5 测试必须冻结真实请求/响应“形状”，不是只冻结结果字符串

每个平台保存经脱敏的成功和失败 fixture：

- exact endpoint/method；
- request JSON keys 和类型；
- required identity mapping；
- provider success envelope；
- provider rejection envelope；
- timeout/非 JSON/HTTP 4xx/HTTP 5xx。

fixture 的来源、采集日期、文档版本和真实成功回执 digest 必须标注。

## 10. Skill 化是否更合适

结论：**适合作为编排层，但不能替代渠道模块。**

建议 Skill：`publish-approved-product`。

输入：

- offer ID；
- 选择的平台/目标；
- 当前批准计划 ID；
- 是否允许 canary 外部写入及精确范围。

Skill 步骤：

1. 读取当前批准计划；
2. 运行三个独立发布器的只读 preflight；
3. 输出明确计划和预计外部写入；
4. 在有精确授权时逐平台执行；
5. 每个平台独立保存回执；
6. 失败不停止其他平台；
7. 最后输出成功、失败和下一步。

Skill 不应该：

- 自行猜类目、价格或 SKU；
- 把历史失败当准入条件；
- 修改另一个平台；
- 在没有真实写入证据时宣称已发布；
- 内置第二套业务状态机。

这会比让 Codex 每次临时理解巨大 UI/控制面更简单，也能保留人工参与：Kyle 只需给 offer 和平台选择，Skill 负责重复、确定性的技术步骤。

## 11. 给独立 agent 的审计问题

请按优先级回答：

### P0：为什么三个按钮都失败但测试仍通过

1. 相关测试在哪些位置替换了 production client/transport/config/data？
2. 是否存在一条从真实按钮到 production adapter、但阻断外部写的纵向测试？
3. 为什么当前 offer 的关键身份和参数没有进入交付门禁？

### P0：平台隔离是否真实成立

1. Shopee global 为什么读取/要求 `TK [VN] 对齐码 0959`？
2. Shopee 的 model/global identity 能否完全从 approved Shopee facts 构建？
3. TikTok/GB failure 是否会阻止其他 TikTok target？
4. Ozon handler 是否读取任何 one-click/Miaoshou/TikTok/Shopee 状态？

### P0：GB 当前失败的代码路径

1. `8e032ad` 实际改了哪些字段和 API 调用？
2. 当前 `code=fail` 来自哪个妙手 endpoint、哪一阶段、哪个请求 digest？
3. 失败前是否已写 category/mandatory attribute/price？
4. 页面为何丢失安全具体原因？

### P1：Ozon success contract

1. `migrate_one(wait_for_import=False)` 对官方返回的成功判定是否符合真实 envelope？
2. 当前 “did not accept” 是 HTTP、provider error、missing task ID 还是解析错误？
3. 是否有一份来自此前真实成功发布的脱敏响应 fixture？

### P1：测试与文档可信度

1. 哪些测试属于过期需求，应删除、迁移还是重写？
2. `release_v2` 文档哪些条目已被后续决定取代？
3. 如何强制每个 bug 保留修改前红测证据，而不是只保留修改后绿测？

## 12. 审计边界

本报告要求的下一步是只读审计，不是修复。独立 agent 不应：

- 调用任何真实发布、修改、删除或撤回接口；
- 修改生产 DB、ignored token/config 或外部平台状态；
- 根据当前错误文案直接打 offer-specific 补丁；
- 把 1995 passed 当作可发布结论；
- 在没有说明当前有效需求版本时修改测试。

独立 agent 应交付：

1. 代码级 root-cause map；
2. 当前需求、代码、测试三方差异矩阵；
3. 哪些旧层应保留、旁路或删除；
4. 三条最小发布器的建议接口；
5. L1/L2/L3 验收设计；
6. 第一个最小修复 Work Order 草案，但暂不执行。

## 13. 关键提交索引

### 内容与前端状态

`0d0302f 19070d3 bb3e622 62e0598 d0107cf 891c298 53e76a5 ade8d6a 0cf9cec c286717`

### 重型控制面与 Shopee 官方路线

`5d57577 082cf4e 8e4d4b2 727bbc9 34b19cd 99d5a18 ca063dc 5ca63b4 40fb85b 7e1d8b3 82dfd5c 7fc5c6b b725928`

### 妙手采集箱与 TikTok 六站点

`5c046d6 d4ef0f4 4c3f439 ef65bb0 3c0fbf1 a742bbe 7550321 47719b4 e0b31a4 ddafe6e 9be2e88 ad56a94 dbbeadc bf637cd 92a1288 d7b97b0 f9fe643 41f2e68 70e5e61 88309c7 0021fc5 8e032ad`

### 三平台简化与当前基线

`c5601e8 0c2f508 10ed5fe 4a9d570 8608612 36a41eb 6fb433e 4afa5f3 431d868`

逐提交查看：

```powershell
git show --stat <commit>
git show <commit> -- <file>
```

## 14. 当前运行和测试证据

- HEAD：`431d868`；
- tracked worktree：文档生成前为 clean；
- 服务：`127.0.0.1:8765`，PID `31116`；
- 页面：`/new-product?offer_id=3846511157`；
- 全仓：1995 passed、4 skipped、8 failed；
- 剩余 8 fail：`tests/test_oneclick_channel_dispatch_boundaries.py` 的旧 MX/HomeBloom control-plane 期望；
- 独立相关测试：109 passed；
- GB frozen tests：15 passed；
- 真实按钮结果：0 success、3 failed；
- 本轮代码开发阶段真实平台写入：0；
- 用户随后真实按钮点击产生外部请求，三平台均返回失败。

安全日志：`D:\tiktok-ecomm-8765-431d868.err.log`。日志可能在其他机器不存在，因此关键行已抄录在本文第 0 节。

## 15. 最终审计假设

本报告不预设具体修复，但提出一个需要独立验证的主假设：

> 当前主要问题不是某一个 if/字段错误，而是系统在渠道真实合同未冻结、需求仍快速变化、且缺少受控真实写入验收的情况下，过早建设了跨渠道统一控制面；随后又通过大量 fixture 测试证明内部自洽，却没有证明 production adapter 对当前商品和当前平台可用。

如果该假设成立，继续逐错误打补丁会延长失败周期。正确下一步是先冻结当前有效需求，审计并拆出三个最小独立发布器，再为每个发布器建立一次受控真实 canary 验收，最后才考虑 Skill 编排和进一步自动化。
