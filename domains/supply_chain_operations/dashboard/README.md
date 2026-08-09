# 东南亚四国逐 SKU 备货决策台

这是供应链运营域内的纯本地、只读决策页面。它不会连接雅仓、TikTok、Shopee 或数据库，也不会自动生成采购单。

正式双台账口径：建议件数只使用有效订单数据与雅仓可用库存/可信在途；节省金额只使用结算数据。结算、escrow 释放和打款时间不得代替订单发生时间计算 7/15/30 天动销。当前静态快照中仍为结算口径的历史销量只能作为旧版参考，在订单快照完成刷新前不得视为正式补货数量。

“资料待补”的具体 SKU 会显示“手动补齐”入口，可填写包装长/宽/高、重量、采购成本及可选来源备注。批次日期统一在 `inbound-batches.html` 确认；一次覆盖完整批次的预计可售日期，并同步影响该批次内全部 SKU，不会改变其他批次。补货主页面只显示批次结果和独立页面入口。两类数据都只写入当前浏览器的 `localStorage`，保存后即时同步，并可随时清除恢复原始快照或系统估算。

批次供给必须满足 `batch_id + seller_sku + quantity + estimated_sellable_date`，且同一 SKU 的全部批次数量之和必须与雅仓聚合在途对平。多批次分摊不完整时，聚合在途暂不进入可用供应；页面仍显示批次和可修改日期，并明确标注待核对数量。单一运输中批次的国家可把该 SKU 聚合在途安全归属于唯一批次。

批次起算优先使用雅仓日志 `已入库（Reach the domestic warehouse）`。尚未实际入库时，必须显示 `NOT_YET_INBOUND`，并使用用户批准的 `estimated_anchor_at = created_at + 4 days` 回退；该时间只能标为估算，不能伪装成实际已入库。`expected_sellable_date = effective_anchor_date + country_transport_days + 2 days`。截图确认的泰国批次 `THML4038-58701` 已入库时间为 `2026-08-04 15:39:15`，据此估算可售日为 `2026-08-21`。

雅仓入库详情必须读取全部分页，并按完整 SKU 汇总重复分箱行。2026-08-09 只读复核确认泰国 0021：`THML4038-58701` 为 200 件，`THSL4038-59557` 为 600 件，合计 800 件，与库存页聚合在途对平。

在途采用分时点投影，不再视为今天已经到仓：先用当前可用库存满足日需求；到某批预计可售日期时才加入该批在途数量；随后继续消耗到本次新补货的预计可售日期。已有在途批次优先以实际已入库日志起算，缺日志时保留“未入库”状态并用建单 + 4 天估算。本次新补货从快照日起先计 7 天备货准备，再叠加 MY/PH 25 天或 TH/VN 15 天运输周期；7 天准备期同时进入交期需求和预计可售日。已有在途仍按各批次规则另加 2 天签收上架缓冲，不重复增加新货准备期。泰国多批次缺少 SKU 级数量拆分时继续不计入供应。

页面按国家隔离需求与库存：

- 马来西亚：TikTok MY + Shopee MY 共用雅仓 `MY8803`；25 天补货周期，西马海运，税费节省按用户结算价的 10%。数量使用两平台 31 天有效订单。
- 泰国：TikTok TH + Shopee TH 共用雅仓 `TH8806`；15 天补货周期，泰国陆运，税费节省按用户结算价的 15%。数量使用两平台 31 天有效订单。
- 越南：TikTok VN + Shopee VN 共用雅仓 `VN8805`；15 天补货周期，按越南南部陆运保守价，税费节省按用户结算价的 10%。数量使用两平台 31 天有效订单。
- 菲律宾：TikTok PH + Shopee PH 共用雅仓 `PH8807`；25 天补货周期，马尼拉海运。尚无获批税费优势，按 0。数量使用两平台 31 天有效订单。
- 四国均以 30 天仓储目标为主，并增加相当于补货周期约 20% 的安全期。
- 所有国家、站点和 SKU 的头程统一按人民币 1 元/件计入。体积只用于装运规划，不再用于本页头程金额。
- 收益单独展示，不再作为隐藏 SKU 或拦截补货建议的门槛。
- “建议补货”（海外仓已有）和“建议首批”（海外仓当前没有）在同一张 SKU 决策表中展示，并可按标签筛选。所有近 30 天有动销的 SKU 都保留在页面，可通过“近30天有动销”筛选查看。
- 建议件数只依赖需求、可用库存、带预计可售日期的在途，以及“7 天备货准备 + 国家运输周期”。缺尺寸时体积显示待补充，缺重量时本土处理和收益显示待补充，缺成本时预计占款显示待补充；这些资料不再阻断建议件数。
- TikTok 与 Shopee 分渠道计算趋势后再相加。具备逐日成交事实时使用非重叠窗口：最近 7 天日销占 60%，第 8–15 天占 30%，第 16–30 天占 10%。例如 30 件全部发生在最近 15 天且均匀分布，趋势月需求为 54 件，而不是简单的 30 件。
- 若 30 天销量集中在不超过 2 个活跃销售日，或单日销量达到 30 天总量的 60%，标为“短期爆量”。海外仓尚无该 SKU 时，首批到仓覆盖缩短为 15 天；稳定后再恢复常规覆盖。
- 没有逐日成交分段时，页面明确显示“降级估算”，继续使用已批准的 30 日 + 长窗口径或长窗口径，不伪造趋势。缺货日历不可得时按自然日作分母，并明确披露；取得实际可售天数后改用可售天数。

在本目录运行：

```powershell
C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\.venv\Scripts\python.exe -m http.server 8874
```

然后打开 `http://127.0.0.1:8874/`。

数据截点为 2026-08-01。31 天订单读取共 99 次：TikTok 有效订单 MY 531、TH 1917、VN 283、PH 321；Shopee 有效订单 MY 101、TH 999、VN 32、PH 77，SKU 行均无未解析订单明细。取消、未付款、测试、替换和暂停订单不计需求；Shopee 部分取消数量显式扣除，退货数量单独保留。头程采用用户批准的统一口径人民币 1 元/件。收益仍使用原结算事实：泰国 Shopee 结算快照没有 SKU 级跨境运费字段，因此该部分节省按 0 计，不伪造收益。

Shopee 明细优先使用完整 `model_sku`；只有 `4位`、`77+4位`、`99+4位`属于已批准的渠道映射。其他格式必须通过精确 `item_id + model_id` 商品目录关系恢复 SKU。越南 21 条、菲律宾 193 条明细通过该关系恢复。菲律宾仍有 19 条历史商品明细没有可审计的 4 位 SKU，因此只在证据区披露并排除自动备货计算，不能把标题相似或图片相似当作映射依据。

泰国库存使用显式、可审计的完整别名归一化：`990401` 与 `0401` 合并为 `0401`，`990605` 与 `0605` 合并为 `0605`。任何只读到前缀、后缀、省略号或掩码的 SKU 一律从库存总数和逐 SKU 决策中排除，取得完整原值前不得猜测、合并或创建 `082X` 一类占位 SKU。

菲律宾猫托盘 SKU 已按雅仓完整字段核对：`770820 → 0820`（0件）、`770821 → 0821`（2件）、`770822 → 0822`（0件）。只有 `0821` 抵扣 2 件库存。

Country-isolation hardening:

- Each TikTok and Shopee source identity must match the target decision country.
- A cross-country product template may provide presentation metadata only; its demand, inventory, warehouse binding, and aliases are discarded.
- The dashboard independently rejects mismatched facts as `BLOCKED_COUNTRY_SOURCE`.
- Seaya VN evidence now maps complete source SKU `880004` to canonical `0004`: stock 47, available 47, allocated/inbound/frozen 0 in `VN8805`.

Four-country summary:

- The `四国汇总 >10` tab calculates MY, TH, VN, and PH independently, then places only rows with `recommended > 10` into one table.
- The threshold is strict: a recommendation of exactly 10 units is excluded.
- Every summary row retains its country badge, main image, SKU, demand, local inventory, arrival calculation, recommendation type, quantity, and benefit evidence.
- The same SKU may appear once per country because no demand or inventory is netted across borders.
