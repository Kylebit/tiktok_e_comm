# 平台数据源规则（中文审阅镜像）

> 英文 [`platform-sources.md`](../../../domains/data_operations/skills/manage-profit-settlement/references/platform-sources.md) 是唯一执行来源。

## TikTok

核对 TikTok 某个下单月份是否全部结算时，应按站点时区分段搜索官方订单创建时间，再与截至明确日期的 Finance 订单交易按脱敏订单 ID 对账。取消订单与未取消但未结算订单必须分开；送达状态不能替代结算证据。经证实的客户拒收订单，本土退货可再售不计商品损耗，跨境退货销毁照计商品成本；不得仅凭负数结算自动分类。

读取 Finance statements 和 statement transactions。以 statement 时间作为结算时间，转为站点时区后再按用户区间过滤。订单交易展开到商品行时，只分摊一次交易总额，不得重复计算。保留佣金、交易费、联盟费、物流、税、退款和调整。

凭证依次检查配置文件和 `tiktok_tokens_livelyhive.json`。只有得到明确批准后，才可把有效来源复制到一次性目录并刷新副本；整个读取会话绑定副本，结束后删除，绝不覆盖来源文件。

对已结算 TikTok 订单 ID 分批只读调用 `/order/202309/orders`，只把官方 `create_time` 保存为 `order_created_at`。缺失或非法下单时间必须成为质量问题；周期纳入仍以 Finance 结算证据为准，禁止用支付、发货、完成、statement、结算或拉取时间冒充下单时间。

周报若结算中包含 `GMV Payment for TikTok Ads`，将它作为实际广告调整单独对账，不纳入按 22% 估算的订单净结算基数，避免重复扣广告。

同一 TikTok 订单可能在一个报告周期内先出现正数销售 statement，随后出现负数退款 statement。必须保留两个 statement 身份；若重复商品的 SKU、数量一致，且只有销售 statement 具有正数买家实付基数，则合并为一条订单行，使结算/退款组件相加，但商品成本与估算广告只扣一次。任何不一致或多个正数基数都必须阻断。

## Shopee

读取按 `escrow_release_time` 筛选的 payment escrow list，再读取 escrow detail。只有已释放 escrow 的记录才是已结算。保留放款时间、payout/escrow 金额、商品数量和价格、平台费、服务费、佣金、运费、退款及商品行分摊方法。普通 `COMPLETED` 订单不能替代结算证据。

对同一批已结算订单分批只读调用 `/api/v2/order/get_order_detail`，保存官方 `create_time` 为 `order_created_at`。缺少下单时间必须成为质量问题；周期纳入仍只按 escrow 放款时间，禁止用下单时间纳入未结算订单，也禁止用放款时间冒充缺失的下单时间。

Shopee 可能在同一结算批次集中释放许多较早订单，因此周报只有一个释放日期也可能正常。仅当所有时间都直接来自 `escrow_release_time`、位于请求区间内，并且证据同时报告父订单数和展开后的商品行数时，才接受这种集中分布。不得为了把订单摊到一周内而改用下单时间、完成时间、API 请求时间或拉取时间。

发货方式必须从 escrow detail 的进口税费字段判断，禁止再用运费判断。读取 `vat_on_imported_goods` 与 `th_import_duty`（其他地区只能使用显式映射的进口关税别名）。VAT 或关税非 0 是跨境证据；两项都存在且严格为 0 才是本土。缺任一字段必须报质量问题；只有一项非 0 时仍标跨境，但产生 `incomplete_cross_border_tax_pair`。合并后的本土运费仓储费按父订单只扣一次，不能按展开后的商品行重复扣除。

`order_ams_commission_fee` 是已包含在 escrow 净结算中的真实联盟营销扣费，不是操作人输入的估算广告费。Shopee 泰国规则以扣除折扣、运费、优惠券和返利后的 Net Completed Purchase Value 乘卖家联盟佣金率，费率可因商品、类目或 Affiliate 不同；间接订单使用直接订单费率的30%，并额外计适用税费。因此直接用费用除以商品标价会出现很大差异。必须保留官方费用；可展示明确标注的观察有效费率，但来源没有直接/间接标志和配置费率时不得反推成平台事实。

Escrow detail 需要按父订单逐一读取，可能超过较短的命令时限。应根据 escrow list 的父订单数量预留充足但有界的运行时间；第一次执行仍在运行时，禁止启动重叠重试。脚本只会在全部详情完成后写入最终证据；没有最终 JSON 表示本次运行未完成，不能解释为空结算周期或成功结果。

## Ozon

读取 finance transaction list，按 posting 聚合 operation；只纳入符合官方已结算判定的 posting。保留全部 operation/service 行及其净结算包含关系。

凭证只从 `config/ozon.local.json` 或 legacy credentials 文件读入内存。财务接口可能返回日期边界外记录，必须按用户给定的本地业务日期再次过滤。

Ozon 的 `in_process_at`、发货、送达、operation 或结算时间都不能标为下单时间。只有官方订单读取明确返回创建时间字段时才允许填写，否则必须留空。

财务记录可能只有 Ozon 平台 SKU，且没有数量。利润阶段可只读调用 `/v3/product/info/list` 将平台 SKU 映射为 seller SKU，并调用 `/v3/posting/fbs/get` 获取实际履约数量。记录请求数、映射数、数量键和失败类型，不保存原始响应。

当前 Ozon V1 广告政策：按 posting 汇总正数 `OperationAgentDeliveredToCustomer` 商品销售组件作为广告基数，默认乘 22%，允许人工明确覆盖，并标记为估算；缺少该销售组件时仍然阻断，不能以 0 代替。

## 脱敏回执

回执至少包含平台、周期、snapshot ID/checksum、拉取时间、源行数、规范化已结算数、排除/拒绝数、分页摘要以及 `external_writes_performed=[]`。不得包含凭证或原始响应。`settlement-evidence/v1` 的 `ready` 只表示结算读取完成，不表示利润已可用。
