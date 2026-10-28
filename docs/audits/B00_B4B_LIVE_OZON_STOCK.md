# B4B 默认 Ozon 库存运输接入

从 `37305f1d725188aff90006409d8517af70490b7d` 做函数级接入。r4 的 `shared_platform/product_publication_live_dependencies.py` 来源 SHA256 为 `79fdb327e675c88e7aa5fcccd63d7c9242d55c94418596b8a3817a91a2ac4a59`；源函数 `_eligible_warehouse_id`、`update_stocks`、`readback_stocks` 的行号/哈希见审计 `live-r3-source.json`。未整文件替换。

原默认 `OzonV4ExecutorDependencies` 没有库存方法，直接 Ozon executor 的库存测试不能证明默认服务装配。现接入实际源端点 `/v2/warehouse/list`、`/v2/products/stocks`、`/v2/product/info/stocks-by-warehouse/fbs`；仅库存写端点计 mutation。旧自定义运输只有 import/readback 方法仍可构建；需要治理库存但缺运输时原安全失败保留。

`prepare_stock_update` 只读核唯一 active/created 非 KGT 仓库并创建固定 payload，返回带仓库身份的 `PreparedStockUpdate`。executor 比对预读仓库与准备仓库后才 reserve，随后调用一次固定 submit。调用方修改原 updates 不改变提交值；提交不会重新选仓。回读若仓库变化不报 PUBLISHED。成功报告的目标证据记录实际读回仓库身份与每 SKU 冻结库存口径。

新候选 review 明示 `UNIQUE_ACTIVE_OR_CREATED_NON_KGT` 选择策略和每 SKU 冻结数量，沿用已保全 r4 的唯一仓库合同。缺失/歧义仓库直接阻断，不采用任意仓库或目录默认库存。既有快照没有该显式 warehouse ID 字段，本包没有声称其曾批准某个具体 warehouse ID；每次执行要求当前唯一选择在预读、准备与回读保持一致。

`live-ozon-segment-final.xml` 有 122 项通过、0 fail/skip。新增 6 项是默认 server 初始化→真实 store/最终批准→runner→真实 live builder/profile/transport→最低 HTTP callable fake（5 情景）及固定 payload 独立测试（1）。其他是相关既有用例，含与 R3 核心重叠，不累计成独立新增数量。

- 正常：2 次 import、1 次 stock POST，ledger 为 3，官方形状假回读验证后 PUBLISHED。
- 仓库只读准备失败或准备前仓库变化：保留 2 次 import、0 stock POST，ledger 为 2、FAILED。
- stock POST 超时：ledger 为 3，外写总数未知，PROCESSING。
- 提交后仓库变化：已发 3 次，保持 PROCESSING，不向新仓重复写。
- 所有默认 server 情景重入原 run 不再调用 HTTP。

测试仅替换最低 Ozon HTTP callable、临时 stores、后台调度和合成执行身份，不替换 builder/executor/projector 的结果。早期 `live-ozon-stock-red.xml` / debug 的 3 失败包含原 fixture 主类名不在旧别名合同内，不能用作库存缺口的有效红例；修正为原已接受主类后验证实际装配。测试不是线上官方验证，未调用真实平台或读取凭据。B4B 早期 test-stable 外连事故仍按独立事故报告保留，本段不能抹去该事实。

TikTok live 准备预算接缝与其余 B4B 来源仍待后续，不以本段宣称全包完成。仅未发送 Ozon SKU 的用户恢复入口仍未提供。

后续必要配对：`is_kgt` 只接受明确 JSON boolean `false`。r4 已读合法形状为显式 false；本包没有依据宣称官方字段一定必填或省略等于 false。缺失、字符串、整数、null 属无法证实非 KGT，阻断到只读核对。`live-ozon-kgt-red.xml` 五个红例进入了 fake stock POST，真 bool true 原本已阻断；修正后 55 项运输/回归全部通过，详见 `live-ozon-kgt-green.xml`。
