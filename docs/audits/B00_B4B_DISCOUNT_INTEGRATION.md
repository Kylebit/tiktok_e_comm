# B00-B4B：折扣窄段

本段从 `99902cccd121689fe969c70ad70a35ab65af267e` 继续；只在隔离候选实现。
这是 TikTok 折扣与历史辅助入口的语义整合，不表示 B4B 全包、COMMON、Shopee 折扣、
浏览器自动执行或个人 Skill 安装完成。

## 行为与批准版本

`shared_platform/postpublish_promotions.py` 新 builder 只生成明确的 v2：比例来自不可变
计划中精确目标的唯一 `pricing.selected_targets[target].store_prices` 行。百分比必须是
1–99 整数，标价/折后价有限且正数、币种正确；折后金额与比例差不超过 0.005。
这是配合现有 `modules/sourcing/new_product_workbench.py` 的 `round(effective_sale_local, 2)`
输出，不引入外汇或新定价算法。未恢复 r4 对所有币种容许 1.01 差额的宽容逻辑。

历史 v1 的 schema/version、政策目录、32/30、批准摘要及 action 摘要保持原样验证。
测试 fixture 从修改前精确源码生成，合成引用 `fixture-existing-frozen-v1`，无真实批准数据。
新计划没有固定比例的 fallback。纯函数校验 JSON 不是数据库批准；运行仍需 ReleaseStore
原计划的批准、逐目标发布回读、OneClick claim 与绑定的 prepared command/proof。
新 adapter 指纹变化，旧 job 的政策漂移保护仍然有效；本段未承诺旧 pending job 可无重新
准备直接重放。旧批准读取兼容不等于豁免现有 ledger 的版本检查。

`domains/channel_operations/pricing_preview.py::_store_price` 只补冻结覆盖的
`price_override` 深拷贝与 `pricing_authority`，原价格计算不变。

## 实际执行接缝

`production_adapter_registry()` 加入独立 `postpublish_promotion`，原主发布注册项不混入
promotion target。目录含 14 个目标；当前默认 writer 仅 LivelyHive TikTok SEA 四店。
Shopee 与浏览器目标明确报告能力缺口，不能把“已注册”说成“已执行”。

真实链为：ReleaseStore 已批准计划 → OneClickReleaseStore job/prerequisite →
OneClickReleaseWorker → 注册项的 prepare/dispatch →
`modules/tiktok/oneclick_promotion.py` → 最低官方 transport。
prepare 完整分页活动列表、确认唯一进行中直接折扣、精确店铺/商品/全部 SKU/价格。
dispatch 再读活动与商品；精确已有成员返回 0 写正式回读，冲突或重复成员停止。
缺成员才在原账本登记 `PRE_INVOCATION_INTENT`，执行一次 PUT，再回读。

`OneClickReleaseStore._record_postpublish_action_result_in_transaction` 的窄增量允许
精确已有成员零写成功：字面 int 0/0/0、空历史累计、无 pending intent、未声称新提交，
且读回 schema、活动/商品摘要、冻结比例、最终 evidence digest 与 prepared command
严格匹配。原“1 次已接受写”和 UNKNOWN 路径保留；没有新表、第二账本或主目标状态机。
缺证据/错活动/错商品/错比例/错摘要/None 计数/UNKNOWN/已接受前缀均有真实 Store 负例。

## 11 个折扣来源与退役范围

冻结 r4 integration 的 11 文件全部全文审阅并保全 SHA，详见配套
`B00_B4B_DISCOUNT_SOURCE_DISPOSITION.json`。输入源和个人安装均未修改。

- 只读 inspect、官方身份/折扣诊断保留；官方认证设施可能刷新 token/本地配置，不能称
  为无任何本地写入的工具。本包未运行真实认证/平台读取。
- 历史 batch discovery 明确输出参考：`NOT_APPROVED`、`REFERENCE_REQUIRES_FROZEN_PLAN`，
  可参考比例放 `reference_discount_percent`，实际 `discount_percent=None`。
- 同标题/同图只生成建议 SKU；实际 `seller_skus=[]`、`identity_verified=false`。
- Shopee 身份恢复按 target+product 匹配旧失败，保留旧 SKU，仅给当前读回的建议，不替换
  成已批准 SKU。导出浏览器参考保留阻断行与缺失目标，不静默缩小范围。
- 旧 `execute_official_discount_plan.py` 替换为无认证/transport 导入的迁移入口。直接函数
  和 `--execute` 均结构化阻断；不读取任意 batch 再写平台，不建立第二份执行报告账本。

退役的是旧旁路；用户需要的 Shopee 折扣能力仍待接通，不是功能取消。

## Shopee 后续后端包的精确输入

1. 继续使用现有注册项、OneClick job/action/prerequisite/claim/occurrence/receipt。增加具名
   Shopee prepare/dispatch adapter，不能复活旧 batch executor。
2. 现成最低客户端 `modules/shopee/client.py::shop_get` / `shop_post`；POST 已配置
   `attempts=1, allow_curl_fallback=False`。认证/店铺配置沿用现有官方客户端，测试全替换最低
   运输与合成配置，不读取真实凭据。
3. r4 旧源提供端点线索：`/api/v2/discount/add_discount_item`、`get_discount`、
   `product/get_model_list`、`get_item_base_info`。这些源码线索不是本包重新核过的官方当前
   合同。下一包必须核准确促销目录分页、活动类型/时间、批次/模型限制及返回字段后实现。
4. 对精确 shop/item/model/SKU、币种、每模型冻结标价及折后价进行准备；不能从实时价格
   替换冻结值，不制造 model_id=0，不覆盖重复模型 ID。完整模型集合与批准目标一致。
5. 既有活动精确同价应零写回读；有冲突阻断；缺项按已冻结模型集合在原账本逐 HTTP 记录。
   部分 item/model 失败、POST 未知、接受后回读失败保留对应事实，不把错误改成零写重试。
6. 当前 Store 的促销成功写类校验仍是 TikTok；Shopee 接入时须在这一个直接消费接缝按
   target 派生允许写类，并继续维持计数/身份/UNKNOWN 负例，不能通配所有写类。
7. 用真实 Store/worker/adapter + 最低 fake 的准备→提交→回读→restart 覆盖，不以手工
   伪造 action JSON 或仅 helper 测试充当后端接通。UI 消费原 `postpublish_actions`。

## canonical / 个人 R3 parity

本基底尚未包含后继 T00 registry/manifest；本段给 canonical 文件哈希供最终组合更新，
不复制其他 writer 工作树，也不安装个人 Skill。T00 记录个人 R3 多出的五项：

| 个人额外路径 | B4B 当前处置 |
| --- | --- |
| scripts/prepare_publication_execution.py | 已由 99902cc 的真实 R2 身份/路由替代；COMMON 后端待接续 |
| scripts/compile_release_candidate.py | 仍在 B4B 未完成来源范围，不能声称已 parity |
| scripts/close_product_publication.py | 仍待完成源审/语义迁移 |
| scripts/repair_tiktok_description_media.py | 旧直接修复不能旁路冻结批准/预算/账本；仍待处置 |
| references/incident-registry.json | 仍待来源审/分类，不以安装覆盖消除差异 |

本段结束即接续 COMMON 包；`scripts/product_publication_workflow.py` 的旧 R2 路由和
hardcoded Kyle/--approve-all 由该接续包一起修。上述缺口不是已完成能力。

## 验证与限制

- 原红例 `FROZEN_DISCOUNT_RISKS.json`：冻结旧 executor 对 forged digest/READY 无批准执行，
  UNKNOWN 二次调用共 2 次 fake PUT；接受后回读失败曾误记 0；同标题异商品曾成 EXACT。
- `discount-contract-red.xml` 保留修改前 12 红。负例中错币种/重复价格行已经由旧下游拒绝，
  当时异常类型/调用边界与新契约不同；不把 12 个红全部当成 12 个独立新漏洞。
- 新窄集 49 项通过，包含 25 个合同/账本/投影测试与 5 个 Skill 行为测试，其余为原相关
  回归。完整 OneClick controlplane 相关集合 148 项通过，与 49 有交集，不相加。
- 最终增加“一个目标缺冻结价格仍显示另一目标”的实际检查器回归，并含两处修复后的注册
  名单用例，最终 focused 为 52 项通过（其中新合同 25、新 Skill 6）；以
  `discount-final-focused2.xml` 为最后源码的窄集证据。
- 另外注册/写入边界集合首次 243 项中 241 通过、2 个旧注册名单断言失败；两处断言补入
  新独立促销注册项后单独复跑。没有因纯名单修订重复长集合，也不把初次报告写成全绿。
- 真注册项测试最低 fake 得到：正常 1、接受后回读失败保留 1、超时 exact=None/upper=1、
  已精确成员 0；每种同 job 重开均无新增 PUT，主发布成功事实不变。
- 中间调试证据保留：旧 fixture 缺冻结价格、旧 server fixture 缺当前 v4 必需事实，改为
  使用既有完整 v4 fixture；新增零写 receipt 起初误报 submission，真实 Store 用例暴露并修复；
  None count 被旧通用推断为 0，现零写分支额外要求原始字面整数计数。
- 当前验证为隔离 Python 路径：禁网络连接、DNS、子进程；并非 OS 防火墙。早期 B4B
  曾有真实 test-stable 请求隔离事故，详见外部审计目录 `TEST_ISOLATION_INCIDENT.md/json`。
  本段 fake 计数不能拿来声称整个 B4B 从未出网，也不代表真实平台已发布/已折扣。
