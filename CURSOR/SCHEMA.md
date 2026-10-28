# Schema 与数据格式说明

本文档描述项目中所有数据文件的列结构、关键字段、匹配规则以及网页内嵌数据结构，便于后续增加需求或修改功能时保持一致性。

---

## 1. 收入表 / 结算表 CSV（Income_Data）

**路径**：`Income_Data/*.csv`（或项目根目录下 `income_*.csv`，视脚本而定）

**编码**：UTF-8（带 BOM 时使用 `utf-8-sig`）

**用途**：订单级利润页读取所有 CSV；月度利润表读取单文件。每行一般为一条订单行或一条 GMV 扣款等。

### 1.1 关键列（英文表头，可能有尾随空格）

| 列名 | 说明 | 备注 |
|------|------|------|
| Statement Date | 账单日期 | 用于按月汇总（build_profit_table） |
| Statement ID | 账单 ID | |
| Currency | 币种 | 如 MYR；订单页用「当地货币」不写死马币 |
| Type | 类型 | 如 `Order`、`GMV Payment for TikTok Ads`；GMV/广告扣款行通常被忽略 |
| Order/adjustment ID  | 订单/调整 ID | 注意列名可能带空格 |
| SKU ID | 商品 SKU | **可能为科学计数法**（如 1.73238E+18），需规范化后再与成本表匹配 |
| Quantity | 数量 | 用于 商品成本 = 单件成本 × 数量 |
| Product name | 商品名称 | |
| SKU name | 规格/变体名 | |
| Total settlement amount | 结算金额 | 当地货币 |
| Total Revenue | 销售收入 | |
| **Subtotal after seller discounts** | **卖家折扣后小计** | **广告成本基数**（订单页：广告 = 该列 × 20%）；利润率 = 单笔利润 / 该列 |
| Subtotal before discounts | 折扣前小计 | |
| Seller discounts | 卖家折扣 | |
| Total Fees | 总费用 | 平台/费用占小计统计只用该列 |
| Transaction fee | 交易费 | |
| TikTok Shop commission fee | 店铺佣金 | |
| … | 其他费用列 | 见下方「费用列枚举」 |
| Affiliate Commission | 联盟佣金 | ≠0 视为联盟带货 |
| Affiliate Shop Ads commission | 联盟店铺广告佣金 | ≠0 视为联盟带货 |
| … | 其余列 | 见脚本中 FEE_COLUMNS |

### 1.2 费用列枚举（订单页表格与统计用）

顺序与脚本中 `FEE_COLUMNS` 一致，用于表格展示及「平台/费用占小计」只取 **Total Fees** 一列：

- Total settlement amount, Total Revenue, Subtotal after seller discounts, Subtotal before discounts, Seller discounts  
- Refund subtotal after seller discounts, Refund subtotal before seller discounts, Refund of seller discounts  
- Total Fees, Transaction fee, TikTok Shop commission fee, Credit card installment - Handling fee  
- Seller shipping fee, Actual shipping fee, Platform shipping fee discount, Customer shipping fee  
- Actual return shipping fee, Refunded customer shipping fee, Shipping subsidy  
- Affiliate Commission, Affiliate partner commission, Affiliate Shop Ads commission  
- Affiliate commission deposit, Affiliate commission refund, Affiliate Partner shop ads commission  
- SFP service fee, Dynamic commission, Bonus cashback service fee, LIVE Specials service fee  
- SST, Voucher Xtra service fee, EAMS Program service fee, Brands Crazy Deals/Flash Sale service fee  
- TikTok PayLater program fee, Campaign resource fee, Platform support fee  
- Ajustment amount, Customer payment, Customer refund  
- Seller co-funded voucher discount, Refund of seller co-funded voucher discount  
- Platform discounts, Refund of platform discounts, Platform co-funded voucher discounts  
- Refund of platform co-funded voucher discounts, Seller shipping fee discount  

---

## 2. 成本表 sku_costs.csv

**路径**：`product_cost/sku_costs.csv` 或项目根目录下 `sku_costs.csv`

**编码**：UTF-8（建议带 BOM）

**来源**：由 `sku_cost_input.html` 导出（仅导出成本 > 0 的 SKU）。用户需将下载的文件移动到 `product_cost/`。

### 2.1 列结构

| 列名 | 说明 |
|------|------|
| SKU ID | 商品 SKU，通常为完整 19 位数字字符串（与产品表一致） |
| 采购成本 | 单件采购成本（人民币） |

- 列名识别：脚本按「含 sku / SKU ID」和「含 成本 / cost」自动识别列索引。

### 2.2 与收入表的 SKU 匹配规则（订单页）

- 收入表 SKU 可能为科学计数法（如 1.73238E+18），会先**按字符串解析**为整数字符串（不经过 float，避免大数精度丢失）。
- 若已是纯数字字符串则原样使用。
- 匹配顺序：
  1. **精确匹配**：规范化后的收入 SKU 在成本表 by_sku 中查找。
  2. **前缀匹配**：用规范化后 SKU 的**前 6 位**在 by_prefix 中查找（成本表加载时对每个 SKU 存 `sku[:6] -> 成本`）。
  3. **相邻前缀**：若仍无匹配，尝试前 6 位 ±1 的前缀（应对科学计数法四舍五入导致的偏差）。
- 商品成本 = 匹配到的单件成本 × 该行 Quantity。

---

## 3. 产品信息表 CSV（product_cost）

**路径**：`product_cost/*all_information*template*.csv`

**命名**：多国多文件，如 `Tiktoksellercenter_batchedit_20260214_all_information_template_MY.csv`，国家可从文件名末尾 `_XX` 解析（如 _vn → VN）。

**编码**：UTF-8（带 BOM 时用 utf-8-sig）

**用途**：生成成本填写页的 SKU 列表（主图、商品名、规格、国家）；订单页用主图、商品名与成本表一起做展示与匹配。

### 3.1 关键列（英文表头）

| 列名 | 说明 |
|------|------|
| product_id | 商品 ID |
| category | 类目 |
| product_name | 商品名称 |
| sku_id | SKU ID（完整数字，用于与收入表/成本表匹配） |
| variation_value | 销售变体选项 / 规格（页面显示为「规格：xxx」） |
| main_image | 主图 URL（需 http/https） |
| image_2 … image_9 | 其他图片 |
| … | 其他属性列 |

- 合并多国时：按 `sku_id` 去重，保留主图、商品名、规格等，并收集国家列表（用于成本页「国家」列与筛选）。

---

## 4. 月度利润表 profit_table.csv

**路径**：项目根目录 `profit_table.csv`

**生成**：`build_profit_table.py`

**编码**：UTF-8 with BOM

### 4.1 列结构

| 列名 | 说明 |
|------|------|
| 月份 | 格式 YYYY-MM，来自 Statement Date |
| 销售收入 | 当月 Total Revenue 之和（不含 GMV Payment 行） |
| 平台费用 | 当月 Total Fees 之和 |
| 广告扣款(平台) | GMV Payment for TikTok Ads 等行的 settlement 之和 |
| 净结算 | 当月 Total settlement amount 之和 |
| 产品成本 | 当月 各订单行 (Quantity × 该 SKU 采购成本) 之和 |
| 广告费用(20%收入) | 销售收入 × 20% |
| 利润 | 净结算 − 产品成本 − 广告费用(20%收入) |

---

## 5. 订单利润页内嵌数据（order_profit.html）

页面由 `build_order_profit_page.py` 生成，数据以 JSON 内嵌在 `<script>` 中。**无币种切换**：表格与统计**同时显示当地货币与人民币**（每格格式：当地值 / ¥人民币值），汇率由用户输入。

### 5.1 单条订单行对象（rows[]）

| 字段 | 类型 | 说明 |
|------|------|------|
| date | string | Statement Date |
| order_id | string | Order/adjustment ID |
| sku_id | string | 规范化后的 SKU |
| product_name | string | 商品名称 |
| sku_name | string | 规格 |
| qty | number | Quantity |
| settlement | number | Total settlement amount（当地货币） |
| revenue | number | Total Revenue（当地货币） |
| subtotal | number | Subtotal after seller discounts（当地货币） |
| product_cost | number | 商品成本（人民币） |
| ad_cost | number | 广告成本（当地货币，= subtotal × 20%） |
| fees | number[] | 与 FEE_COLUMNS 顺序一致的各费用值（当地货币） |
| image_url | string | 主图 URL，来自产品表按 SKU 匹配 |
| cost_matched | boolean | 是否在成本表中匹配到该 SKU；false 时该行标红，商品成本按 0 统计 |
| local_shipping | boolean | 本土发货：Seller shipping fee 与 SST 税 同时为 0；表格中该行绿底、列「本土发货」为「是」 |

- 单笔利润（前端计算）：当地 = settlement − product_cost/rate − ad_cost；若该行为本土发货则再减去（本土发货费用/汇率）；人民币同理减去本土发货费用。**每行扣一次**：同一订单多件商品（多行）则每行各扣一次，总费用 = 本土发货行数 × 元/单。
- 利润率 = 单笔利润(当地) / subtotal；subtotal 为 0 时显示 `-`。
- **展示**：订单明细每格金额列显示「当地值 / ¥人民币值」；商品成本列显示「— / ¥人民币值」。统计总览与各产品利润同样双币展示。

### 5.2 费用列元数据（feeColumns[]）

每项：`{ en: string, cn: string }`，与 Python 中 `FEE_COLUMNS` 一一对应，用于表头与导出。

### 5.3 前端常量

- `defaultRate`：默认汇率（1 当地货币 = x 人民币），如 1.7。
- `adRate`：0.2（广告 = 卖家折扣后小计 × 20%）。
- 用户输入「本土发货费用（元/单）」：仅对本土发货订单计费，总本土发货费用 = 本土发货订单数 × 该值，从总利润中扣除（当地与人民币均扣除）。

### 5.4 统计汇总（前端计算）

- 总利润、卖家折扣后小计合计、商品成本占小计、广告成本占小计、**平台/费用占小计**（仅用 Total Fees 列汇总）、利润率(利润/小计)。**总利润与小计合计**同时显示当地货币与人民币（两行或「当地 x / ¥y」）；**总利润已扣除本土发货费用**（本土发货订单数 × 本土发货费用元/单）。
- **本土发货**：统计总览显示「本土发货订单数」「本土发货费用(¥)」；订单明细表有「本土发货」列（是/否），本土发货行绿底标识。
- **联盟带货**：联盟带货销量占比、联盟带货销售额占比、联盟带货订单占比（Affiliate Commission 或 Affiliate Shop Ads commission ≠ 0 视为联盟带货）。
- **各产品**：按 sku_id/product_name 聚合，每产品有 商品图、销量、**利润(当地/¥)**、**联盟带货比例**（该产品联盟带货销量/该产品总销量）。

---

## 6. 成本填写页（sku_cost_input.html）

**生成**：`generate_sku_cost_page.py`

### 6.1 内嵌数据

- **SKU_LIST**：`{ sku_id, product_name, sku_name, image_url, countries[] }[]`，来自合并后的产品表。
- **EXISTING**：`{ [sku_id]: cost }`，来自已存在的 `sku_costs.csv` 预填。
- **ALL_COUNTRIES**：国家代码列表，用于筛选下拉。
- **PRODUCT_COST_PATH**：`product_cost` 的绝对路径，用于提示用户将导出的 CSV 移入该目录。

### 6.2 前端存储

- **localStorage 键**：`sku_costs_draft`。  
- **值**：`JSON.stringify({ [skuId]: value })`，未导出前的输入草稿，刷新后仍会预填。

### 6.3 导出规则

- 仅导出 **成本 > 0** 的行；成本为 0 或空的视为未更新，不写入 CSV。
- 导出文件名为 `sku_costs.csv`，由浏览器下载到用户「下载」目录，需用户自行移动到 `product_cost/`。

---

## 7. 常量与配置（脚本内）

| 常量/配置 | 文件 | 说明 |
|-----------|------|------|
| INCOME_DATA_DIR | build_order_profit_page.py | `"Income_Data"` |
| PRODUCT_COST_DIR | 多个 | `"product_cost"` |
| SKU_COSTS_CSV | 多个 | `"sku_costs.csv"` |
| PRODUCT_CSV_GLOB | 多个 | `"*all_information*template*.csv"` |
| AD_RATE | build_order_profit_page.py / build_profit_table.py | 0.20 |
| DEFAULT_MYR_TO_CNY | build_order_profit_page.py | 1.7 |
| SETTLEMENT_CSV | build_profit_table.py / generate_sku_cost_page.py | 结算 CSV 文件名（根目录） |
| FEE_COLUMNS | build_order_profit_page.py | 收入表费用列（英文, 中文）列表，顺序与 fees[] 一致 |

---

## 8. 扩展时注意点

- **新增收入表列**：在 `FEE_COLUMNS` 中增加 (英文表头, 中文名)，并确保收入 CSV 表头与英文表头一致（含空格）。
- **新增统计指标**：在订单页 `renderSummary` 中基于 `rows` 与 `feeColumns` 计算，并在 summary 区域增加展示；若涉及新列，需在 `build_order_rows` 中写入对应字段。
- **SKU 匹配**：若新增数据源或列，需统一使用 `norm_sku` 及与前 6 位前缀、相邻前缀一致的逻辑，避免与成本表/产品表不一致。
- **币种与汇率**：订单页**同时显示当地货币与人民币**（无切换，每格格式：当地值 / ¥人民币值）；汇率由用户输入。若支持多币种混合，需在行级或文件级区分 Currency 并选用对应汇率。
