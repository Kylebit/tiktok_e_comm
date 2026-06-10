#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
所有店铺总利润汇总：根据 Income_Data 下各国收入表，按国家汇总。
- 单国利润 = (Total settlement amount 合计 - GMV Payment for TikTok Ads 金额) × 汇率 - 商品成本(人民币)
- 汇率由用户在输出 HTML 中按国家输入（1 当地货币 = ? 人民币）
- 输出 HTML：Income_Data/Output/TotalProfit.html
"""

import json
from pathlib import Path

from build_order_profit_page import (
    INCOME_DATA_DIR,
    INCOME_OUTPUT_SUBDIR,
    PRODUCT_COST_DIR,
    SKU_COSTS_CSV,
    _col_index,
    build_order_rows,
    is_gmv_ads_payment_row,
    load_income_rows_from_file,
    load_product_by_sku_and_prefix,
    load_sku_costs,
    parse_income_filename,
    parse_number,
)

# 各国默认汇率（1 当地货币 = ? 人民币），总利润页预填
DEFAULT_RATES_BY_COUNTRY = {
    "VN": 0.000266,
    "TH": 0.2218,
    "PH": 0.118,
    "MY": 1.75,
}


def compute_country_stats(header, rows, sku_costs, sku_costs_by_prefix, product_by_sku, product_by_prefix):
    """
    从收入表 (header, rows) 计算：总结算额(当地)、GMV Payment for TikTok Ads 额(当地)、
    有效结算额(当地，仅订单行)、物流赔款(当地，Type=Logistics reimbursement)、
    商品成本(人民币，返回负数供显示)、币种。
    返回 (total_settlement, gmv_ads_settlement, order_settlement, subtotal_order, logistics_reimbursement, product_cost_cny_neg, currency)。
    """
    key_type = _col_index(header, "Type ", "Type")
    key_settlement = _col_index(header, "Total settlement amount")
    key_currency = _col_index(header, "Currency")
    if key_settlement < 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ""

    total_settlement = 0.0
    gmv_ads_settlement = 0.0
    logistics_reimbursement = 0.0
    for row in rows:
        if len(row) <= key_settlement:
            continue
        amt = parse_number(row[key_settlement])
        typ = (row[key_type] or "").strip() if key_type >= 0 else ""
        total_settlement += amt
        if is_gmv_ads_payment_row(typ):
            gmv_ads_settlement += amt
        if "Logistics reimbursement" in typ or "物流赔款" in typ:
            logistics_reimbursement += amt

    order_rows = build_order_rows(
        header, rows, sku_costs, sku_costs_by_prefix, product_by_sku, product_by_prefix
    )
    order_settlement = round(sum(r["settlement"] for r in order_rows), 2)  # 与 Outprofit 总结算完全一致
    subtotal_local = round(sum(r["subtotal"] for r in order_rows), 2)  # 卖家折扣后小计合计(当地)
    product_cost_cny = round(sum(r["product_cost"] for r in order_rows), 2)
    product_cost_cny_neg = -product_cost_cny  # 以负数存储和展示

    currency = ""
    if key_currency >= 0 and rows and len(rows[0]) > key_currency:
        currency = (rows[0][key_currency] or "").strip()

    return (
        round(total_settlement, 2),
        round(gmv_ads_settlement, 2),
        round(order_settlement, 2),
        subtotal_local,
        round(logistics_reimbursement, 2),
        product_cost_cny_neg,
        currency,
    )


def main():
    base = Path(__file__).resolve().parent
    income_dir = base / INCOME_DATA_DIR
    output_dir = income_dir / INCOME_OUTPUT_SUBDIR
    product_dir = base / PRODUCT_COST_DIR
    if not product_dir.is_dir():
        product_dir = base
    sku_costs_path = product_dir / SKU_COSTS_CSV

    if not income_dir.is_dir():
        print(f"未找到收入表文件夹：{income_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_files = sorted(income_dir.glob("*.csv"))
    if not csv_files:
        print("Income_Data 下未找到 CSV 文件。")
        return

    sku_costs, sku_costs_by_prefix = load_sku_costs(sku_costs_path)
    product_by_sku, product_by_prefix = load_product_by_sku_and_prefix(product_dir)

    countries_data = []
    for csv_path in csv_files:
        country, start, end = parse_income_filename(csv_path)
        header, rows = load_income_rows_from_file(csv_path)
        if not header or not rows:
            print(f"跳过（表为空）：{csv_path.name}")
            continue
        total_settlement, gmv_ads, order_settlement, subtotal_order, logistics_reimb, product_cost_cny_neg, currency = compute_country_stats(
            header, rows, sku_costs, sku_costs_by_prefix, product_by_sku, product_by_prefix
        )
        rate_default = DEFAULT_RATES_BY_COUNTRY.get((country or "").upper())
        countries_data.append({
            "country": country,
            "period": f"{start}—{end}",
            "currency": currency or "?",
            "total_settlement": total_settlement,
            "gmv_ads_settlement": gmv_ads,
            "effective_settlement": order_settlement,
            "subtotal_local": subtotal_order,
            "logistics_reimbursement": logistics_reimb,
            "product_cost_cny": product_cost_cny_neg,
            "rate": rate_default,
        })

    if not countries_data:
        print("没有可汇总的国家数据。")
        return

    out_path = output_dir / "TotalProfit.html"
    write_html(countries_data, out_path)
    print(f"已生成总利润页：{out_path}")
    print(f"共 {len(countries_data)} 个国家/表格，请在页面中填写各国汇率后查看总利润（人民币）。")


def write_html(countries_data, output_path):
    """输出总利润 HTML：各国结算额、GMV Ads 扣减、商品成本、汇率输入、利润及总利润。"""
    data_json = json.dumps(countries_data, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>所有店铺总利润</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 16px; background: #f5f5f5; }
    h1 { font-size: 20px; color: #1a1a1a; }
    .hint { color: #666; font-size: 13px; margin: 8px 0 16px 0; }
    table { border-collapse: collapse; width: 100%; max-width: 960px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-radius: 8px; overflow: hidden; }
    th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; }
    th { background: #fafafa; font-weight: 600; color: #555; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    td input.rate-input { width: 96px; padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }
    .total-row { font-weight: 700; background: #f0f9ff; }
    .total-row td { border-top: 2px solid #0ea5e9; padding-top: 12px; }
    .profit-positive { color: #0d6b0d; }
    .profit-negative { color: #b91c1c; }
  </style>
</head>
<body>
  <h1>所有店铺总利润</h1>
  <p class="hint">数据来源：Income_Data 下各国收入表。单国利润 = (总结算额(当地) + 物流赔款(当地)) × 汇率 + 商品成本(人民币)（商品成本为负数，即减去）。请在下表「汇率」列填写：1 当地货币 = ? 人民币。</p>
  <table id="table">
    <thead>
      <tr>
        <th>国家</th>
        <th>结算期</th>
        <th>币种</th>
        <th class="num">总结算额(当地)</th>
        <th class="num">GMV Payment for TikTok Ads(当地)</th>
        <th class="num">有效结算额(当地)</th>
        <th class="num">物流赔款(当地)</th>
        <th class="num">卖家折扣后小计合计(人民币)</th>
        <th class="num">商品成本(人民币)</th>
        <th>汇率 (1 当地= ? 人民币)</th>
        <th class="num">利润(人民币)</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
    <tfoot>
      <tr class="total-row">
        <td colspan="10">总利润(人民币)</td>
        <td class="num" id="totalProfit">—</td>
      </tr>
    </tfoot>
  </table>

  <script>
    var data = """ + data_json + """;

    function fmtNum(x) {
      if (x == null || isNaN(x)) return '—';
      return Number(x).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function render() {
      var tbody = document.getElementById('tbody');
      tbody.innerHTML = '';
      var totalProfitCny = 0;
      data.forEach(function (row, i) {
        var rate = parseFloat(row.rate);
        if (isNaN(rate)) rate = 0;
        var logisticsLocal = Number(row.logistics_reimbursement) || 0;
        var costCny = Number(row.product_cost_cny) || 0;
        var profitCny = (row.total_settlement + logisticsLocal) * rate + costCny;
        if (!isNaN(profitCny)) totalProfitCny += profitCny;
        var tr = document.createElement('tr');
        tr.innerHTML =
          '<td>' + (row.country || '') + '</td>' +
          '<td>' + (row.period || '') + '</td>' +
          '<td>' + (row.currency || '') + '</td>' +
          '<td class="num">' + fmtNum(row.total_settlement) + '</td>' +
          '<td class="num">' + fmtNum(row.gmv_ads_settlement) + '</td>' +
          '<td class="num">' + fmtNum(row.effective_settlement) + '</td>' +
          '<td class="num">' + fmtNum(row.logistics_reimbursement) + '</td>' +
          '<td class="num">' + fmtNum((row.subtotal_local || 0) * rate) + '</td>' +
          '<td class="num">' + fmtNum(row.product_cost_cny) + '</td>' +
          '<td><input type="text" inputmode="decimal" class="rate-input" placeholder="如 0.2 或 1.7" data-i="' + i + '" value="' + (row.rate != null && row.rate !== '' ? (typeof row.rate === 'number' ? (row.rate < 0.001 ? row.rate.toFixed(6).replace(/\\.?0+$/, '') : String(row.rate)) : String(row.rate)) : '') + '"></td>' +
          '<td class="num ' + (profitCny >= 0 ? 'profit-positive' : 'profit-negative') + '">' + fmtNum(isNaN(profitCny) ? null : profitCny) + '</td>';
        tbody.appendChild(tr);
      });
      document.getElementById('totalProfit').textContent = fmtNum(totalProfitCny);
      document.getElementById('totalProfit').className = 'num ' + (totalProfitCny >= 0 ? 'profit-positive' : 'profit-negative');
    }

    document.getElementById('table').addEventListener('input', function (e) {
      var input = e.target;
      if (input.dataset.i != null && input.classList.contains('rate-input')) {
        var i = parseInt(input.dataset.i, 10);
        var v = input.value.trim().replace(',', '.');
        data[i].rate = v === '' ? null : parseFloat(v);
        render();
      }
    });
    document.getElementById('table').addEventListener('change', function (e) {
      var input = e.target;
      if (input.dataset.i != null && input.classList.contains('rate-input')) {
        var i = parseInt(input.dataset.i, 10);
        var v = input.value.trim().replace(',', '.');
        var num = v === '' ? null : parseFloat(v);
        data[i].rate = num;
        if (v !== '' && !isNaN(num)) input.value = String(num);
        render();
      }
    });

    render();
  </script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
