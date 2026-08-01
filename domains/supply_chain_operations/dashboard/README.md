# 东南亚四国逐 SKU 备货决策台

这是供应链运营域内的纯本地、只读决策页面。它不会连接雅仓、TikTok、Shopee 或数据库，也不会自动生成采购单。

正式双台账口径：建议件数只使用有效订单数据与雅仓可用库存/可信在途；节省金额只使用结算数据。结算、escrow 释放和打款时间不得代替订单发生时间计算 7/15/30 天动销。当前静态快照中仍为结算口径的历史销量只能作为旧版参考，在订单快照完成刷新前不得视为正式补货数量。

“资料待补”的具体 SKU 会显示“手动补齐”入口，可填写包装长/宽/高、重量、采购成本及可选来源备注。数据仅写入当前浏览器的 `localStorage`，保存后即时重算；可随时通过“修改已补资料”清除恢复到原始快照。

页面按国家隔离需求与库存：

- 马来西亚：TikTok MY + Shopee MY，共用雅仓 `MY8803`；25 天补货周期，西马海运，税费节省按用户结算价的 10%。
- 泰国：TikTok TH + Shopee TH，共用雅仓 `TH8806`；15 天补货周期，泰国陆运，税费节省按用户结算价的 15%。
- 越南：雅仓 `VN8805`；15 天补货周期，按越南南部陆运保守价，税费节省按用户结算价的 10%。TikTok 使用 31 天 SKU 级结算；Shopee 使用 2025-07-30 至 2026-07-30 的完整已结算订单。
- 菲律宾：雅仓 `PH8807`；25 天补货周期，马尼拉海运。尚无获批税费优势，按 0。TikTok 使用 31 天 SKU 级结算；Shopee 使用 2025-07-30 至 2026-07-30 的完整已结算订单。
- 四国均以 30 天仓储目标为主，并增加相当于补货周期约 20% 的安全期。
- 所有国家、站点和 SKU 的头程统一按人民币 1 元/件计入。体积只用于装运规划，不再用于本页头程金额。
- 收益单独展示，不再作为隐藏 SKU 或拦截补货建议的门槛。
- “建议补货”（海外仓已有）和“建议首批”（海外仓当前没有）在同一张 SKU 决策表中展示，并可按标签筛选。所有近 30 天有动销的 SKU 都保留在页面，可通过“近30天有动销”筛选查看。
- 建议件数只依赖需求、可用库存、可信在途和国家补货周期。缺尺寸时体积显示待补充，缺重量时本土处理和收益显示待补充，缺成本时预计占款显示待补充；这些资料不再阻断建议件数。
- TikTok 与 Shopee 分渠道计算趋势后再相加。具备逐日成交事实时使用非重叠窗口：最近 7 天日销占 60%，第 8–15 天占 30%，第 16–30 天占 10%。例如 30 件全部发生在最近 15 天且均匀分布，趋势月需求为 54 件，而不是简单的 30 件。
- 若 30 天销量集中在不超过 2 个活跃销售日，或单日销量达到 30 天总量的 60%，标为“短期爆量”。海外仓尚无该 SKU 时，首批到仓覆盖缩短为 15 天；稳定后再恢复常规覆盖。
- 没有逐日成交分段时，页面明确显示“降级估算”，继续使用已批准的 30 日 + 长窗口径或长窗口径，不伪造趋势。缺货日历不可得时按自然日作分母，并明确披露；取得实际可售天数后改用可售天数。

在本目录运行：

```powershell
C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\.venv\Scripts\python.exe -m http.server 8874
```

然后打开 `http://127.0.0.1:8874/`。

数据截点为 2026-07-30。头程采用用户批准的统一口径人民币 1 元/件，不再引用体积报价计算页面收益。泰国 Shopee 的本地规范化结算快照没有 SKU 级跨境运费字段，因此该部分节省按 0 计，不伪造收益。越南与菲律宾 Shopee 已刷新授权并完成全年分页：越南 205/205 单详情成功、273 件、49 个已映射 SKU；菲律宾 450/450 单详情成功、622 件、76 个已映射 SKU。两站 API 明细错误均为 0。

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
