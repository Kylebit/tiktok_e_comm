# TikTok 店铺利润与成本管理项目

## 1. 项目概述

本项目用于 TikTok Shop 店铺的**采购成本录入**、**利润汇总**和**订单级利润分析**。数据来源为 TikTok 后台导出的收入/结算 CSV 与产品信息表；商品成本由用户在网页中按 SKU 填写并导出；广告成本按规则估算（如卖家折扣后小计的 20%）。订单利润页**同时显示当地货币与人民币**（输入汇率后每格展示「当地值 / ¥人民币值」），支持多国站点、联盟带货统计与建议。

---

## 2. 目录与文件结构

```
项目根目录/
├── PROJECT.md                 # 本文档：项目说明
├── SCHEMA.md                  # Schema 文档：数据格式与字段定义
├── run_all.py                 # 总脚本：依次执行成本页、订单利润页、总利润页、月度利润表
├── build_order_profit_page.py # 订单级利润 + 统计页（HTML）
├── build_total_profit_page.py # 所有店铺总利润汇总页
├── generate_sku_cost_page.py  # 生成「按 SKU 填写采购成本」页面
├── build_profit_table.py      # 按月汇总利润表（CSV）
├── profit_table.csv           # 月度利润表（生成）
├── console_export_products.js  # 可选：从后台页面抓取商品列表
├── Income_Data/                # 收入表
│   ├── income_*.csv           # 收入表 CSV（可多文件）
│   └── Output/                # 生成的订单利润页、总利润页
│       ├── Outprofit_*.html
│       └── TotalProfit.html
└── product_cost/               # 产品表与成本表
    ├── sku_costs.csv          # SKU 采购成本（用户导出）
    ├── *all_information*template*_*.csv  # 多国产品信息表
    └── sku_cost_input.html   # 采购成本填写页（generate_sku_cost_page.py 生成）
```

- **输入**：`Income_Data/*.csv`、`product_cost/sku_costs.csv`、`product_cost/*all_information*template*.csv`
- **输出**：`product_cost/sku_cost_input.html`、`Income_Data/Output/*.html`、`profit_table.csv`（以及用户从浏览器下载的 `sku_costs.csv`）

---

## 3. 业务流程

### 3.0 一键执行全部脚本（可选）

在项目根目录运行：`python3 run_all.py`  
会依次执行：成本填写页生成 → 订单利润页生成 → 总利润页生成 → 月度利润表生成。若某脚本缺少输入文件会跳过或报错，其余照常执行。

### 3.1 采购成本录入

1. 将 TikTok 后台导出的**产品信息表**（`*all_information*template*.csv`）放入 `product_cost/`，可多国多文件。
2. 在项目根目录运行：`python3 generate_sku_cost_page.py`
3. 用浏览器打开生成的 `product_cost/sku_cost_input.html`，按 SKU 填写采购成本（可筛选国家、筛选「未填」等）。
4. 点击「导出 sku_costs.csv」，将文件保存到本机后，**手动移动到 `product_cost/`**，供后续脚本读取。

### 3.2 月度利润汇总

1. 将后台导出的**结算/收入 CSV** 放在项目根目录或 `Income_Data/`（`build_profit_table.py` 当前读取根目录下固定文件名）。
2. 确保 `product_cost/sku_costs.csv` 已存在且包含 SKU 成本。
3. 运行：`python3 build_profit_table.py`
4. 得到 `profit_table.csv`（按月的销售收入、平台费用、广告扣款、净结算、产品成本、广告费用、利润）。

### 3.3 订单级利润与统计

1. 将后台导出的**收入表 CSV** 放入 `Income_Data/`（可多个文件）。
2. 在项目根目录运行：`python3 build_order_profit_page.py`
3. 用浏览器打开 `Income_Data/Output/Outprofit_国家_开始_结束.html`（每个收入表对应一个 HTML）：
   - 查看**统计总览**：总利润、各项支出占小计比例、联盟带货占比、各产品销量与利润（含商品图、联盟带货比例）及建议。
   - 查看**订单明细**：表格与统计**同时显示当地货币与人民币**（格式：当地值 / ¥人民币值），汇率可输入；表头与左侧三列（日期、订单ID、商品图）固定便于横向/纵向滚动，列宽可拖拽调整。

### 3.4 所有店铺总利润

1. 在项目根目录运行：`python3 build_total_profit_page.py`
2. 用浏览器打开 `Income_Data/Output/TotalProfit.html`，按国家填写汇率，查看各国利润及总利润（人民币）。

---

## 4. 脚本说明

| 脚本 | 作用 | 主要输入 | 主要输出 |
|------|------|----------|----------|
| `generate_sku_cost_page.py` | 合并多国产品表，生成成本填写页 | `product_cost/*all_information*template*.csv`、可选 `sku_costs.csv` | `product_cost/sku_cost_input.html` |
| `build_order_profit_page.py` | 订单级利润 + 统计 + 网页 | `Income_Data/*.csv`、`product_cost/sku_costs.csv`、产品表 | `Income_Data/Output/Outprofit_*.html` |
| `build_total_profit_page.py` | 所有店铺总利润汇总 | `Income_Data/*.csv`、`product_cost/sku_costs.csv` | `Income_Data/Output/TotalProfit.html` |
| `build_profit_table.py` | 按月份汇总利润 | 根目录下结算 CSV、`product_cost/sku_costs.csv` | `profit_table.csv` |

- **SKU 匹配**：收入表 SKU 常为科学计数法，成本表为完整数字；订单页脚本会做规范化 + 前缀匹配（前 6 位）及相邻前缀回退，详见 `SCHEMA.md`。
- **广告成本**：  
  - 月度表（`build_profit_table.py`）：广告费用 = 销售收入的 20%。  
  - 订单页：广告成本 = **Subtotal after seller discounts（卖家折扣后小计）** 的 20%。
- **联盟带货**：订单行中 `Affiliate Commission` 或 `Affiliate Shop Ads commission` 不为 0 视为联盟带货，用于统计占比及每产品联盟带货比例。

---

## 5. 后续扩展与优化建议

- **需求扩展**：在 `SCHEMA.md` 中查对应 CSV/HTML 的字段与结构，新增列时同步更新 Schema 与脚本中的列名/常量。
- **多收入文件**：`build_order_profit_page.py` 已支持 `Income_Data/` 下多 CSV；`build_profit_table.py` 当前仅支持单文件，可改为扫描目录。
- **国家/币种**：订单页同时显示当地货币与人民币，已用「当地货币」避免写死马币；若多国混合，可考虑按行或按文件识别 Currency 列并分别换算。
- **成本表路径**：当前优先 `product_cost/sku_costs.csv`，若无 `product_cost` 则用项目根目录；可增加配置或环境变量指定路径。
- **导出路径**：浏览器只能下载到「下载」目录，需用户自行将 `sku_costs.csv` 移到 `product_cost/`；可在文档或页面上再次强调该步骤。

---

## 6. 依赖与运行

- **Python**：3.x，仅用标准库（`csv`、`json`、`re`、`pathlib` 等）。
- **浏览器**：用于打开生成的 HTML，需支持 ES5+ 与 localStorage。
- 无需安装第三方包。

### 6.1 虚拟环境（可选）

项目根目录已包含虚拟环境目录 `venv/`。使用方式：

```bash
# 激活虚拟环境
# macOS/Linux:
source venv/bin/activate
# Windows:
# venv\Scripts\activate

# 之后在项目根目录运行脚本，例如：
python run_all.py
python Income_Data/build_order_profit_page.py

# 退出虚拟环境
deactivate
```

未激活 venv 时，直接用系统 Python（如 `python3 run_all.py`）也可运行。
