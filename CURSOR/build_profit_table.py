#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok 店铺利润表生成脚本
- 从后台导出的结算 CSV 汇总收入与平台费用
- 广告费用 = 销售收入的 20%
- 产品成本 = 按 SKU 的采购成本 × 数量（需先在 sku_cost_input.html 里填写每个 SKU 的采购成本，导出 sku_costs.csv）
"""

import csv
import os
from collections import defaultdict
from pathlib import Path

from build_order_profit_page import _col_index, is_gmv_ads_payment_row

SETTLEMENT_CSV = "income_20260214145954(UTC+8).csv"
PRODUCT_COST_DIR = "product_cost"  # sku_costs.csv 与产品表所在目录
SKU_COSTS_CSV = "sku_costs.csv"
PROFIT_OUTPUT_CSV = "profit_table.csv"
AD_RATE = 0.20  # 广告费用 = 销售收入的 20%


def parse_number(s):
    if s is None or (isinstance(s, str) and s.strip() == ""):
        return 0.0
    s = str(s).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_date(d):
    if not d or not str(d).strip():
        return None
    d = str(d).strip()
    for sep in ["/", "-"]:
        if sep in d:
            parts = d.split(sep)
            if len(parts) >= 2:
                y, m = parts[0], parts[1]
                try:
                    return f"{y}-{int(m):02d}"
                except ValueError:
                    pass
    return None


def norm_sku(s):
    """统一 SKU ID 格式便于匹配（结算表里可能是科学计数法）。"""
    if s is None:
        return None
    s = str(s).strip()
    if s == "" or s == "/":
        return None
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
        return str(f)
    except ValueError:
        return s


def load_settlement_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [c.strip() for c in next(reader)]
        rows = list(reader)
    return header, rows


def load_sku_costs(path):
    """读取 sku_costs.csv：SKU ID, 采购成本（单件）。返回 { normalized_sku: cost }"""
    if not os.path.isfile(path):
        return {}
    out = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [c.strip() for c in next(reader)]
        rows = list(reader)
    # 期望列：SKU ID 或 sku_id，采购成本 或 cost
    sku_col = 0
    cost_col = 1
    for i, h in enumerate(header):
        if "sku" in h.lower() or h == "SKU ID":
            sku_col = i
        if "成本" in h or "cost" in h.lower():
            cost_col = i
    for row in rows:
        if len(row) <= max(sku_col, cost_col):
            continue
        sku = norm_sku(row[sku_col])
        if not sku:
            continue
        cost = parse_number(row[cost_col])
        out[sku] = cost
    return out


def aggregate_settlement(header, rows, sku_costs):
    """
    按月份汇总：销售收入、平台费用、广告扣款、净结算、产品成本（按 SKU 成本×数量）。
    广告费用在 build_profit_table 里按 revenue 的 20% 计算。
    """
    key_date = _col_index(header, "Statement Date")
    key_type = _col_index(header, "Type ", "Type")
    key_settlement = _col_index(header, "Total settlement amount")
    key_revenue = _col_index(header, "Total Revenue")
    key_fees = _col_index(header, "Total Fees")
    key_sku = _col_index(header, "SKU ID")
    key_qty = _col_index(header, "Quantity")

    by_month = defaultdict(lambda: {"revenue": 0.0, "fees": 0.0, "ad_deduction": 0.0, "settlement": 0.0, "product_cost": 0.0})

    for row in rows:
        if len(row) <= max(key_date, key_type, key_settlement, key_revenue, key_fees, key_sku, key_qty):
            continue
        month = parse_date(row[key_date])
        if not month:
            continue
        typ = (row[key_type] or "").strip()
        settlement = parse_number(row[key_settlement])
        revenue = parse_number(row[key_revenue])
        fees = parse_number(row[key_fees])

        by_month[month]["settlement"] += settlement
        by_month[month]["fees"] += fees

        if is_gmv_ads_payment_row(typ):
            by_month[month]["ad_deduction"] += settlement
        else:
            by_month[month]["revenue"] += revenue
            # 订单行：产品成本 += 数量 × 该 SKU 采购成本
            if key_sku >= 0 and key_qty >= 0:
                sku = norm_sku(row[key_sku])
                qty = parse_number(row[key_qty])
                if sku and qty != 0:
                    cost_per = sku_costs.get(sku, 0.0)
                    by_month[month]["product_cost"] += qty * cost_per

    return dict(by_month)


def build_profit_table(settlement_by_month):
    """广告费用 = 销售收入的 20%。利润 = 净结算 - 产品成本 - 广告费用。"""
    result = []
    for month in sorted(settlement_by_month.keys()):
        s = settlement_by_month[month]
        revenue = s["revenue"]
        ad_spend = round(revenue * AD_RATE, 2)
        profit = s["settlement"] - s["product_cost"] - ad_spend
        result.append({
            "month": month,
            "revenue": revenue,
            "fees": s["fees"],
            "ad_deduction": s["ad_deduction"],
            "net_settlement": s["settlement"],
            "product_cost": s["product_cost"],
            "ad_spend": ad_spend,
            "profit": profit,
        })
    return result


def write_profit_csv(rows, path):
    header = [
        "月份", "销售收入", "平台费用", "广告扣款(平台)", "净结算", "产品成本", "广告费用(20%收入)", "利润"
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([
                r["month"],
                round(r["revenue"], 2),
                round(r["fees"], 2),
                round(r["ad_deduction"], 2),
                round(r["net_settlement"], 2),
                round(r["product_cost"], 2),
                round(r["ad_spend"], 2),
                round(r["profit"], 2),
            ])


def main():
    base = Path(__file__).resolve().parent
    settlement_path = base / SETTLEMENT_CSV
    product_dir = base / PRODUCT_COST_DIR
    sku_costs_path = (product_dir / SKU_COSTS_CSV) if product_dir.is_dir() else (base / SKU_COSTS_CSV)
    output_path = base / PROFIT_OUTPUT_CSV

    if not settlement_path.is_file():
        print(f"未找到结算表：{settlement_path}")
        return

    sku_costs = load_sku_costs(sku_costs_path)
    if not sku_costs:
        print("未找到 sku_costs.csv 或文件为空。请先打开 sku_cost_input.html 填写每个 SKU 的采购成本并导出 sku_costs.csv。")

    header, rows = load_settlement_csv(settlement_path)
    settlement_by_month = aggregate_settlement(header, rows, sku_costs)
    profit_rows = build_profit_table(settlement_by_month)
    write_profit_csv(profit_rows, output_path)

    print(f"已生成利润表：{output_path}")
    print("广告费用已按销售收入的 20% 计算。")
    if not sku_costs:
        print("当前产品成本为 0（请填写 sku_costs.csv 后重新运行）。")


if __name__ == "__main__":
    main()
