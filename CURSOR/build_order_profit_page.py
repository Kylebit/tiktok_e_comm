#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok 订单级利润计算并输出网页
- 从 Income_Data 文件夹读取后台导出的收入表 CSV，每个 CSV 单独处理
- 文件名最后三项解析为：国家_结算开始时间_结算结束时间，用于页面展示与输出命名
- 生成的 HTML 均放在 Income_Data/Output/，命名为 Outprofit_国家_结算开始时间_结算结束时间.html
- 忽略 GMV Payment 行；每条订单行：商品成本（按 SKU ID 从 sku_costs 取）× 数量，广告成本 = Subtotal after seller discounts（卖家折扣后小计）的 20%，单笔利润 = Total settlement amount - 商品成本 - 广告成本，利润率 = 单笔利润 / 卖家折扣后小计
- 商品图从 product_cost 下产品表按 SKU ID 匹配

【成本匹配逻辑】
- 收入表：Income_Data/*.csv，列名 "SKU ID"。该列可能是科学计数法（如 1.73238E+18）或完整数字（如 1732379861313488827）。
- 成本表：product_cost/sku_costs.csv，列名 "SKU ID"、"采购成本"。SKU 来自 sku_cost_input.html 导出，为产品表里的完整 19 位数字（如 1732379861313488827）。
- 匹配步骤：
  1) 收入表 SKU 规范化：若已是纯数字则原样；若为科学计数法则用字符串解析为整数字符串（不经过 float，避免精度丢失）。
  2) 先用「规范化后的收入 SKU」在成本表里做精确匹配（by_sku）。
  3) 若无匹配，再用「规范化后 SKU 的前 6 位」在成本表的前缀表里匹配（by_prefix）；成本表加载时对每个 SKU 存了 sku[:6] -> 成本。
- 商品成本 = 匹配到的单件成本 × 该行数量（数量来自收入表 "Quantity"）。
"""

import csv
import json
import os
import re
from pathlib import Path

INCOME_DATA_DIR = "Income_Data"
INCOME_OUTPUT_SUBDIR = "Output"  # 生成的 HTML 放在 Income_Data/Output/
PRODUCT_COST_DIR = "product_cost"
SKU_COSTS_CSV = "sku_costs.csv"
PRODUCT_CSV_GLOB = "*all_information*template*.csv"
AD_RATE = 0.20
DEFAULT_MYR_TO_CNY = 1.7

# 收入表各项金额列（英文表头, 中文名），用于表格罗列
FEE_COLUMNS = [
    ("Total settlement amount", "结算金额"),
    ("Total Revenue", "销售收入"),
    ("Subtotal after seller discounts", "卖家折扣后小计"),
    ("Subtotal before discounts", "折扣前小计"),
    ("Seller discounts", "卖家折扣"),
    ("Refund subtotal after seller discounts", "退款-卖家折扣后小计"),
    ("Refund subtotal before seller discounts", "退款-折扣前小计"),
    ("Refund of seller discounts", "退款-卖家折扣"),
    ("Total Fees", "总费用"),
    ("Transaction fee", "交易费"),
    ("TikTok Shop commission fee", "TikTok 店铺佣金"),
    ("Credit card installment - Handling fee", "信用卡分期手续费"),
    ("Seller shipping fee", "卖家运费"),
    ("Actual shipping fee", "实际运费"),
    ("Platform shipping fee discount", "平台运费折扣"),
    ("Customer shipping fee", "客户运费"),
    ("Actual return shipping fee", "实际退货运费"),
    ("Refunded customer shipping fee", "已退客户运费"),
    ("Shipping subsidy", "运费补贴"),
    ("Affiliate Commission", "联盟佣金"),
    ("Affiliate partner commission", "联盟合作伙伴佣金"),
    ("Affiliate Shop Ads commission", "联盟店铺广告佣金"),
    ("Affiliate commission deposit", "联盟佣金存入"),
    ("Affiliate commission refund", "联盟佣金退款"),
    ("Affiliate Partner shop ads commission", "联盟合作伙伴店铺广告佣金"),
    ("SFP service fee", "SFP 服务费"),
    ("Dynamic commission", "动态佣金"),
    ("Bonus cashback service fee", "奖金返现服务费"),
    ("LIVE Specials service fee", "直播专享服务费"),
    ("SST", "SST 税"),
    ("Voucher Xtra service fee", "Voucher Xtra 服务费"),
    ("EAMS Program service fee", "EAMS 计划服务费"),
    ("Brands Crazy Deals/Flash Sale service fee", "品牌疯狂大促/闪购服务费"),
    ("TikTok PayLater program fee", "TikTok 先享后付计划费"),
    ("Campaign resource fee", "活动资源费"),
    ("Platform support fee", "平台支持费"),
    ("Ajustment amount", "调整金额"),
    ("Customer payment", "客户实付"),
    ("Customer refund", "客户退款"),
    ("Seller co-funded voucher discount", "卖家共担优惠券折扣"),
    ("Refund of seller co-funded voucher discount", "卖家共担优惠券折扣退款"),
    ("Platform discounts", "平台折扣"),
    ("Refund of platform discounts", "平台折扣退款"),
    ("Platform co-funded voucher discounts", "平台共担优惠券折扣"),
    ("Refund of platform co-funded voucher discounts", "平台共担优惠券折扣退款"),
    ("Seller shipping fee discount", "卖家运费折扣"),
]

# 后台导出语言为中文时，列名与英文版不同；值为该英文表头对应的额外候选名（含英文变体）。
INCOME_HEADER_EXTRA_NAMES = {
    "Statement Date": ("结算日期",),
    "Type ": ("交易类型",),
    "Type": ("交易类型",),
    "Order/adjustment ID  ": ("订单ID/调整单ID",),
    "Order/adjustment ID": ("订单ID/调整单ID",),
    "Quantity": ("数量",),
    "Product name": ("商品名称",),
    "SKU name": ("SKU 名称",),
    "Total settlement amount": ("结算总金额",),
    "Total Revenue": ("总收入",),
    "Subtotal after seller discounts": ("享受商家折扣后小计",),
    "Subtotal before discounts": ("享受折扣前小计",),
    "Seller discounts": ("商家折扣",),
    "Refund subtotal after seller discounts": ("享受商家折扣后的退款小计",),
    "Refund subtotal before discounts": ("享受商家折扣前的退款小计",),
    "Refund of seller discounts": ("商家折扣退款",),
    "Total Fees": ("总费用",),
    "Transaction fee": ("交易手续费",),
    "TikTok Shop commission fee": ("TikTok Shop 佣金费",),
    "Credit card installment - Handling fee": (
        "信用卡分期付款 - 利率成本",
        "Credit card installment - Interest rate cost",
    ),
    "Seller shipping fee": ("商家运费",),
    "Actual shipping fee": ("实际运费",),
    "Platform shipping fee discount": ("平台包邮运费",),
    "Customer shipping fee": ("买家支付运费",),
    "Actual return shipping fee": ("实际退货运费",),
    "Refunded customer shipping fee": ("客户运费退款",),
    "Shipping subsidy": ("运费补贴",),
    "Affiliate Commission": ("联盟佣金",),
    "Affiliate partner commission": ("联盟服务商佣金",),
    "Affiliate Shop Ads commission": ("联盟店铺广告佣金",),
    "Affiliate commission deposit": ("联盟佣金保证金",),
    "Affiliate commission refund": ("联盟佣金退款",),
    "Affiliate Partner shop ads commission": ("联盟服务商店铺广告佣金",),
    "SFP service fee": ("SFP 服务费",),
    "Dynamic commission": ("进口关税和增值税", "Import duty and value added tax"),
    "Bonus cashback service fee": ("奖金返现服务费",),
    "LIVE Specials service fee": ("直播特别活动服务费",),
    "SST": ("SST",),
    "Voucher Xtra service fee": ("超级优惠券服务费",),
    "EAMS Program service fee": ("EAMS 计划服务费",),
    "Brands Crazy Deals/Flash Sale service fee": ("品牌疯狂优惠/秒杀服务费",),
    "TikTok PayLater program fee": ("TikTok PayLater 计划费用",),
    "Campaign resource fee": ("活动资源费",),
    "Platform support fee": ("基础设施费", "Infrastructure fee", "电商增长服务费", "Commerce growth fee"),
    "Ajustment amount": ("调整金额",),
    "Customer payment": ("客户付款",),
    "Customer refund": ("客户退款",),
    "Seller co-funded voucher discount": ("商家共同赞助优惠券折扣",),
    "Refund of seller co-funded voucher discount": ("商家共同赞助优惠券折扣退款",),
    "Platform discounts": ("平台折扣",),
    "Refund of platform discounts": ("平台折扣退款",),
    "Platform co-funded voucher discounts": ("平台共同赞助优惠券折扣",),
    "Refund of platform co-funded voucher discounts": ("平台共同赞助优惠券折扣退款",),
    "Seller shipping fee discount": ("商家运费折扣",),
    "Currency": ("货币",),
}


def is_gmv_ads_payment_row(typ):
    """收入表中 TikTok 广告 GMV 扣款行（中英交易类型文案）。"""
    if not typ:
        return False
    t = typ.strip()
    tl = t.lower()
    if "gmv payment" in tl and "tiktok" in tl:
        return True
    if "tiktok ads" in tl and "gmv" in tl:
        return True
    # 例：TikTok 广告的 GMV 付款
    if "gmv" in tl and "付款" in t and ("tiktok" in tl or "广告" in t):
        return True
    return False


def parse_number(s):
    if s is None or (isinstance(s, str) and s.strip() == ""):
        return 0.0
    s = str(s).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_scientific_sku(s):
    """
    将科学计数法 SKU 转为整数字符串，不经过 float，避免大整数精度丢失。
    例如 "1.73238E+18" -> "1732380000000000000"（有效数字 + 补零）。
    """
    s = str(s).strip().upper()
    if "E" not in s:
        return None
    parts = s.split("E")
    if len(parts) != 2:
        return None
    base_str = parts[0].strip().replace(" ", "")
    exp_str = parts[1].strip().lstrip("+")
    if "." not in base_str:
        base_str += "."
    try:
        exp = int(exp_str)
    except ValueError:
        return None
    # 有效数字：去掉小数点
    digits = base_str.replace(".", "")
    if not digits.isdigit():
        return None
    # 小数点前位数
    if "." in base_str:
        num_decimal_places = len(base_str.split(".")[1])
    else:
        num_decimal_places = 0
    # 整数 = digits * 10^(exp - len(digits) + 1) 等价于 digits 后补 (exp - num_decimal_places) 个零
    num_zeros = exp - num_decimal_places
    if num_zeros < 0:
        return None
    return digits + "0" * num_zeros


def norm_sku(s):
    """
    统一 SKU ID 格式。收入表里有时为科学计数法（如 1.73238E+18），转为整数字符串。
    - 若已是纯数字字符串，原样返回（避免 float 精度丢失）。
    - 若含 E/e，用字符串解析科学计数法。成本匹配仅用完全匹配，不做前缀匹配。
    """
    if s is None:
        return None
    s = str(s).strip()
    if s == "" or s == "/":
        return None
    if re.match(r"^\d+$", s):
        return s
    parsed = _parse_scientific_sku(s)
    if parsed is not None:
        return parsed
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
        return str(int(f))
    except (ValueError, OverflowError):
        return s


def load_sku_costs(path):
    """
    读取 sku_costs.csv：SKU ID, 采购成本。
    返回 (by_sku, by_prefix): 精确 sku_id -> cost，前 6 位前缀 -> cost（用于收入表科学计数法匹配）。
    """
    if not os.path.isfile(path):
        return {}, {}
    by_sku = {}
    by_prefix = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [c.strip() for c in next(reader)]
        sku_col, cost_col = 0, 1
        for i, h in enumerate(header):
            if "sku" in h.lower() or h == "SKU ID":
                sku_col = i
            if "成本" in h or "cost" in h.lower():
                cost_col = i
        for row in reader:
            if len(row) <= max(sku_col, cost_col):
                continue
            raw = (row[sku_col] or "").strip()
            # 导出时用 ="SKU" 避免 Excel 科学计数法，读回时去掉公式外壳
            if raw.startswith('="') and raw.endswith('"') and len(raw) > 4:
                raw = raw[2:-1].replace('""', '"').strip()
            if not raw:
                continue
            # 成本表里可能是完整数字或已规范化的
            sku = raw if re.match(r"^\d+$", raw) else norm_sku(raw)
            if not sku:
                continue
            cost = parse_number(row[cost_col])
            by_sku[sku] = cost
            if len(sku) >= 6 and sku[:6] not in by_prefix:
                by_prefix[sku[:6]] = cost
    return by_sku, by_prefix


def _read_one_product_csv(path):
    """读单个产品表 CSV，返回 [ { sku_id, product_name, image_url, sku_name }, ... ]。"""
    result = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [c.strip() for c in next(reader)]
        idx = {h: i for i, h in enumerate(header)}
        i_sku = idx.get("sku_id", -1)
        i_name = idx.get("product_name", -1)
        i_img = idx.get("main_image", -1)
        i_var = idx.get("variation_value", -1)
        if i_sku < 0:
            return []
        for row in reader:
            if len(row) <= max(i_sku, i_name, i_img):
                continue
            sku_raw = (row[i_sku] or "").strip()
            if not sku_raw or not re.match(r"^\d+$", sku_raw):
                continue
            name = (row[i_name] or "").strip() if i_name >= 0 else ""
            img = (row[i_img] or "").strip() if i_img >= 0 else ""
            var = (row[i_var] or "").strip() if i_var >= 0 else ""
            image_url = img if img and (img.startswith("http://") or img.startswith("https://")) else ""
            result.append({
                "sku_id": sku_raw,
                "product_name": name[:200] + "..." if len(name) > 200 else name,
                "sku_name": var,
                "image_url": image_url,
            })
    return result


def load_product_by_sku_and_prefix(base_dir):
    """
    合并多国产品表，返回 (by_sku, by_prefix)。
    by_sku: 精确 sku_id -> { image_url, product_name, sku_name }
    by_prefix: 前 6 位 -> info（用于结算表科学计数法匹配）
    """
    product_files = sorted(Path(base_dir).glob(PRODUCT_CSV_GLOB))
    if not product_files:
        return {}, {}
    by_sku = {}
    for path in product_files:
        try:
            for r in _read_one_product_csv(path):
                sid = r["sku_id"]
                if sid not in by_sku:
                    by_sku[sid] = r
                else:
                    if not by_sku[sid].get("image_url") and r.get("image_url"):
                        by_sku[sid]["image_url"] = r["image_url"]
                    if not by_sku[sid].get("product_name") and r.get("product_name"):
                        by_sku[sid]["product_name"] = r["product_name"]
                    if not by_sku[sid].get("sku_name") and r.get("sku_name"):
                        by_sku[sid]["sku_name"] = r["sku_name"]
        except Exception as e:
            print(f"读取产品表 {path.name} 时出错: {e}")
    by_prefix = {}
    for sid, info in by_sku.items():
        if len(sid) >= 6 and sid[:6] not in by_prefix:
            by_prefix[sid[:6]] = info
    return by_sku, by_prefix


def _col_index(header, *candidates):
    idx = {c: i for i, c in enumerate(header)}
    expanded = []
    for name in candidates:
        if name is None or name == "":
            continue
        expanded.append(name)
        expanded.extend(INCOME_HEADER_EXTRA_NAMES.get(name, ()))
    seen = set()
    for name in expanded:
        if name in seen:
            continue
        seen.add(name)
        if name in idx:
            return idx[name]
        alt = name.strip()
        for k in idx:
            if k.strip() == alt:
                return idx[k]
    return -1


def parse_income_filename(path):
    """
    从收入表文件名解析：国家、结算开始时间、结算结束时间（文件名最后三项）。
    例如 income_20260216080502_TH_260115_260215.csv -> (TH, 260115, 260215)
    或 xxx_th_260115-260215.csv -> (th, 260115, 260215)
    """
    stem = Path(path).stem
    parts = stem.split("_")
    if len(parts) >= 3:
        last = parts[-1]
        if "-" in last:
            country = parts[-2]
            segs = last.split("-", 1)
            start, end = segs[0], segs[1] if len(segs) > 1 else last
        else:
            country, start, end = parts[-3], parts[-2], parts[-1]
    elif len(parts) == 2:
        country = parts[-2]
        start = end = parts[-1]
    else:
        country, start, end = "?", stem, stem
    return country, start, end


def load_income_rows_from_file(csv_path):
    """读取单个收入表 CSV，返回 (header, rows)。"""
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = [c.strip() for c in next(reader)]
            rows = list(reader)
            return header, rows
    except Exception as e:
        print(f"读取 {csv_path} 时出错: {e}")
        return [], []


def build_order_rows(header, rows, sku_costs, sku_costs_by_prefix, product_by_sku, product_by_prefix):
    """
    忽略 GMV Payment；每条订单行计算：商品成本(人民币)、广告成本(卖家折扣后小计 Subtotal after seller discounts 的 20%，当地货币)、单笔利润。
    收入表金额为当地货币；商品成本来自 sku_costs 为人民币(CNY)。
    返回 [ { date, order_id, sku_id, ..., settlement, revenue, subtotal, product_cost, ad_cost, fees[], image_url }, ... ]
    """
    key_date = _col_index(header, "Statement Date")
    key_type = _col_index(header, "Type ", "Type")
    key_order = _col_index(header, "Order/adjustment ID  ", "Order/adjustment ID")
    key_sku = _col_index(header, "SKU ID")
    key_qty = _col_index(header, "Quantity")
    key_prod = _col_index(header, "Product name")
    key_sku_name = _col_index(header, "SKU name")
    key_settlement = _col_index(header, "Total settlement amount")
    key_revenue = _col_index(header, "Total Revenue")
    key_subtotal = _col_index(header, "Subtotal after seller discounts")

    fee_keys = [_col_index(header, en) for en, _ in FEE_COLUMNS]
    max_key = max(key_date, key_type, key_settlement, key_revenue, key_sku, key_qty, key_subtotal, max(fee_keys) if fee_keys else 0)

    if key_settlement < 0 or key_revenue < 0:
        return []

    idx_seller_shipping = next((i for i, (en, _) in enumerate(FEE_COLUMNS) if en == "Seller shipping fee"), -1)
    idx_sst = next((i for i, (en, _) in enumerate(FEE_COLUMNS) if en == "SST"), -1)

    result = []
    for row in rows:
        if len(row) <= max_key:
            continue
        typ = (row[key_type] or "").strip()
        if is_gmv_ads_payment_row(typ):
            continue
        settlement = parse_number(row[key_settlement])
        revenue = parse_number(row[key_revenue])
        subtotal = parse_number(row[key_subtotal]) if key_subtotal >= 0 else revenue
        sku = norm_sku(row[key_sku] if key_sku >= 0 else "")
        qty = parse_number(row[key_qty] if key_qty >= 0 else 0)
        if not sku and qty == 0:
            continue

        # 成本只做完全匹配：仅用 by_sku 精确匹配，不用前缀/相邻前缀，避免科学计数法或前 6 位相同导致错配
        cost_matched = False
        cost_per = sku_costs.get(sku) if sku_costs else None
        if cost_per is not None:
            cost_matched = True
        if cost_per is None:
            cost_per = 0.0
        product_cost = round(qty * cost_per, 2)  # 人民币
        # 广告成本 = 卖家折扣后小计(Subtotal after seller discounts) 的 20%
        ad_cost_local = round(subtotal * AD_RATE, 2)

        info = product_by_sku.get(sku) or (product_by_prefix.get(sku[:6]) if len(sku) >= 6 else None)
        product_name = (row[key_prod] or "").strip() if key_prod >= 0 else ""
        sku_name = (row[key_sku_name] or "").strip() if key_sku_name >= 0 else ""
        if info:
            if not product_name and info.get("product_name"):
                product_name = info["product_name"]
            if not sku_name and info.get("sku_name"):
                sku_name = info["sku_name"]
        image_url = (info.get("image_url") or "").strip() if info else ""

        fees = []
        for i, k in enumerate(fee_keys):
            val = parse_number(row[k]) if k >= 0 and len(row) > k else 0.0
            fees.append(round(val, 2))

        # 本土发货：Seller shipping fee 与 SST 同时为 0
        local_shipping = (
            idx_seller_shipping >= 0 and idx_sst >= 0
            and len(fees) > max(idx_seller_shipping, idx_sst)
            and fees[idx_seller_shipping] == 0
            and fees[idx_sst] == 0
        )

        result.append({
            "date": (row[key_date] or "").strip() if key_date >= 0 else "",
            "order_id": (row[key_order] or "").strip() if key_order >= 0 else "",
            "sku_id": sku or "",
            "product_name": product_name or "-",
            "sku_name": sku_name or "",
            "qty": int(qty) if qty == int(qty) else qty,
            "settlement": settlement,
            "revenue": revenue,
            "subtotal": round(subtotal, 2),
            "product_cost": product_cost,
            "ad_cost": ad_cost_local,
            "fees": fees,
            "image_url": image_url,
            "cost_matched": cost_matched,
            "local_shipping": local_shipping,
        })
    return result


def write_html(order_rows, output_path, meta=None):
    """
    将订单利润表写入 HTML 页面。含统计、当地/人民币切换、利润率、卖家折扣后小计、各项扣费列。
    meta: 可选 dict {country, start, end}，用于标题与页面展示。
    """
    meta = meta or {}
    country = meta.get("country", "")
    start = meta.get("start", "")
    end = meta.get("end", "")
    meta_line = f"国家：{country}　结算期：{start} — {end}" if (country or start or end) else ""

    rows_json = json.dumps(order_rows, ensure_ascii=False)
    fee_columns_json = json.dumps([{"en": en, "cn": cn} for en, cn in FEE_COLUMNS], ensure_ascii=False)
    default_rate = DEFAULT_MYR_TO_CNY
    ad_rate = AD_RATE

    title_suffix = f" {country} {start}-{end}" if (country or start or end) else ""
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>订单利润表与统计""" + title_suffix + """</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 16px; background: #f5f5f5; }
    h1, h2 { font-size: 20px; color: #1a1a1a; margin-top: 20px; }
    h2 { font-size: 16px; margin-top: 24px; }
    .hint { color: #666; font-size: 13px; margin-bottom: 8px; }
    .toolbar { margin-bottom: 12px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .toolbar label { font-size: 14px; color: #333; }
    .toolbar input[type="number"] { width: 72px; padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }
    .toolbar select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }
    .table-wrap { overflow: auto; max-width: 100%; max-height: 70vh; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-radius: 8px; margin-bottom: 16px; }
    table { border-collapse: collapse; min-width: 100%; }
    th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #eee; font-size: 12px; white-space: nowrap; }
    th { background: #fafafa; font-weight: 600; color: #555; }
    .table-wrap thead th { position: sticky; top: 0; z-index: 20; background: #fafafa; box-shadow: 0 1px 0 #eee; }
    .table-wrap thead th:nth-child(1), .table-wrap tbody td:nth-child(1) { position: sticky; left: 0; z-index: 10; background: #fff; min-width: 72px; }
    .table-wrap thead th:nth-child(1) { z-index: 21; background: #fafafa; }
    .table-wrap thead th:nth-child(2), .table-wrap tbody td:nth-child(2) { position: sticky; left: 90px; z-index: 10; background: #fff; min-width: 100px; }
    .table-wrap thead th:nth-child(2) { z-index: 21; background: #fafafa; }
    .table-wrap thead th:nth-child(3), .table-wrap tbody td:nth-child(3) { position: sticky; left: 180px; z-index: 10; background: #fff; min-width: 56px; }
    .table-wrap thead th:nth-child(3) { z-index: 21; background: #fafafa; }
    .product-img { width: 40px; height: 40px; object-fit: cover; border-radius: 6px; background: #eee; }
    .product-img-none { width: 40px; height: 40px; background: #eee; border-radius: 6px; color: #999; font-size: 10px; display: inline-flex; align-items: center; justify-content: center; }
    .num { text-align: right; }
    .profit-positive { color: #0d6b0d; }
    .profit-negative { color: #b91c1c; }
    .row-no-cost-match { background-color: #fef2f2 !important; }
    .row-local-shipping { background-color: #f0fdf4 !important; }
    .product-name { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .table-wrap table { table-layout: fixed; }
    .table-wrap th { position: relative; }
    .table-wrap th .resizer { position: absolute; top: 0; right: 0; width: 6px; height: 100%; cursor: col-resize; user-select: none; }
    .table-wrap th .resizer:hover { background: rgba(0,0,0,0.1); }
    .th-fee { font-size: 11px; line-height: 1.3; }
    .summary-box { background: #fff; padding: 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 16px; }
    .summary-box h3 { font-size: 14px; margin: 0 0 12px 0; color: #333; }
    .summary-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
    .summary-item { padding: 8px; background: #f9f9f9; border-radius: 6px; }
    .summary-item .label { font-size: 12px; color: #666; }
    .summary-item .value { font-size: 18px; font-weight: 600; }
    .suggestions { background: #f0f9ff; border-left: 4px solid #0ea5e9; padding: 12px 16px; margin-top: 12px; border-radius: 0 6px 6px 0; }
    .suggestions ul { margin: 8px 0 0 0; padding-left: 20px; }
    .suggestions li { margin: 4px 0; }
    .summary-product-table { width: 100%; margin-top: 8px; }
    .summary-product-table th, .summary-product-table td { padding: 6px 10px; font-size: 12px; }
    .summary-product-img { width: 36px; height: 36px; object-fit: cover; border-radius: 4px; background: #eee; vertical-align: middle; }
    .summary-product-img-none { width: 36px; height: 36px; display: inline-flex; align-items: center; justify-content: center; background: #eee; border-radius: 4px; color: #999; font-size: 10px; }
    #orderTable tfoot tr { font-weight: 700; background: #f0f9ff; }
    #orderTable tfoot td { border-top: 2px solid #0ea5e9; padding: 8px 10px; }
    .table-wrap tfoot td:nth-child(1) { position: sticky; left: 0; z-index: 10; background: #f0f9ff; }
    .table-wrap tfoot td:nth-child(2) { position: sticky; left: 90px; z-index: 10; background: #f0f9ff; }
    .table-wrap tfoot td:nth-child(3) { position: sticky; left: 180px; z-index: 10; background: #f0f9ff; }
  </style>
</head>
<body>
  <h1>订单利润表与统计</h1>
  """ + (f'<p class="hint" style="margin-top:0; font-weight:600;">{meta_line}</p>' if meta_line else "") + """
  <p class="hint">收入表为当地货币；商品成本为人民币。广告成本 = 卖家折扣后小计(Subtotal after seller discounts) 的 20%。利润率 = 单笔利润 / 卖家折扣后小计。联盟带货：Affiliate Commission 或 Affiliate Shop Ads commission 不为 0 的订单。本土发货：Seller shipping fee 与 SST 税 同时为 0 的订单（绿底标识），可填写「本土发货费用」从总利润中扣除。已忽略 GMV Payment。</p>
  <div class="toolbar">
    <label>汇率：1 当地货币 =</label>
    <input type="number" id="rate" step="0.01" min="0.01" value=\"""" + str(default_rate) + """\">
    <label>人民币（表格与统计同时显示两种货币）</label>
    <span style="margin-left:16px"></span>
    <label>本土发货费用（元/单）：</label>
    <input type="number" id="localShippingFee" step="0.01" min="0" value="0" title="Seller shipping fee 与 SST 均为 0 的订单按本土发货计，总费用 = 订单数 × 本值，从总利润中扣除">
  </div>

  <h2>统计总览</h2>
  <div class="summary-box" id="summaryBox"></div>

  <h2>订单明细</h2>
  <p class="hint" style="margin-top:0">红底行为成本表未匹配到 SKU 的订单，商品成本按 0 统计，请补全 sku_costs 后重新生成。</p>
  <div class="table-wrap">
  <table id="orderTable">
    <colgroup id="orderTableColgroup"></colgroup>
    <thead>
      <tr id="theadRow"></tr>
    </thead>
    <tbody id="tbody"></tbody>
    <tfoot>
      <tr id="totalsRow"></tr>
    </tfoot>
  </table>
  </div>

  <script>
    var rows = """ + rows_json + """;
    var feeColumns = """ + fee_columns_json + """;
    var defaultRate = """ + str(default_rate) + """;
    var adRate = """ + str(ad_rate) + """;

    function escapeHtml(s) {
      if (s == null) return '';
      var div = document.createElement('div');
      div.textContent = s;
      return div.innerHTML;
    }

    function getRate() { return parseFloat(document.getElementById('rate').value) || defaultRate; }
    function getLocalShippingFee() { return parseFloat(document.getElementById('localShippingFee').value) || 0; }

    function refreshAll() {
      var rate = getRate();
      renderSummary(rate);
      renderTable(rate);
    }

    function renderSummary(rate) {
      var totalSubtotal = 0, totalSettlement = 0, totalProductCost = 0, totalAdCost = 0, totalProfitLocal = 0, totalProfitCny = 0;
      var totalFeesByIndex = {};
      var bySku = {};
      var idxAffiliate = feeColumns.findIndex(function(fc) { return fc.en === 'Affiliate Commission'; });
      var idxShopAds = feeColumns.findIndex(function(fc) { return fc.en === 'Affiliate Shop Ads commission'; });
      var totalQty = 0, affiliateQtyTotal = 0, affiliateSubtotalTotal = 0, orderCount = 0, affiliateOrderCount = 0;
      var localShippingOrderCount = 0;
      var localFeePerRow = getLocalShippingFee();
      rows.forEach(function (r) {
        var st = r.subtotal || 0;
        totalSubtotal += st;
        totalSettlement += r.settlement || 0;
        totalProductCost += r.product_cost || 0;
        totalAdCost += r.ad_cost || 0;
        var pl = (r.settlement || 0) - (r.product_cost || 0) / rate - (r.ad_cost || 0);
        var pc = (r.settlement || 0) * rate - (r.product_cost || 0) - (r.ad_cost || 0) * rate;
        if (r.local_shipping) {
          localShippingOrderCount += 1;
          if (localFeePerRow > 0) {
            pl -= localFeePerRow / rate;
            pc -= localFeePerRow;
          }
        }
        totalProfitLocal += pl;
        totalProfitCny += pc;
        (r.fees || []).forEach(function (v, i) {
          totalFeesByIndex[i] = (totalFeesByIndex[i] || 0) + v;
        });
        var isAffiliate = (idxAffiliate >= 0 && r.fees && r.fees[idxAffiliate] != null && Number(r.fees[idxAffiliate]) !== 0) ||
          (idxShopAds >= 0 && r.fees && r.fees[idxShopAds] != null && Number(r.fees[idxShopAds]) !== 0);
        var q = r.qty || 0;
        totalQty += q;
        if (isAffiliate) { affiliateQtyTotal += q; affiliateSubtotalTotal += st; affiliateOrderCount += 1; }
        orderCount += 1;
        var key = r.sku_id || r.product_name || '-';
        if (!bySku[key]) bySku[key] = { qty: 0, profitLocal: 0, profitCny: 0, name: r.product_name || '-', image_url: r.image_url || '', affiliateQty: 0 };
        bySku[key].qty += q;
        bySku[key].profitLocal += pl;
        bySku[key].profitCny += pc;
        if (isAffiliate) bySku[key].affiliateQty += q;
      });
      var totalFeesIndex = feeColumns.findIndex(function(fc) { return fc.en === 'Total Fees'; });
      var totalFees = totalFeesIndex >= 0 ? (totalFeesByIndex[totalFeesIndex] || 0) : 0;
      var totalLocalShippingCostCny = localShippingOrderCount * localFeePerRow;
      var affiliateQtyPct = totalQty > 0 ? (affiliateQtyTotal / totalQty * 100) : 0;
      var affiliateSubtotalPct = totalSubtotal > 0 ? (affiliateSubtotalTotal / totalSubtotal * 100) : 0;
      var affiliateOrderPct = orderCount > 0 ? (affiliateOrderCount / orderCount * 100) : 0;

      var productCostLocal = totalProductCost / rate;
      var productPct = totalSubtotal > 0 ? (productCostLocal / totalSubtotal * 100) : 0;
      var adPct = totalSubtotal > 0 ? (totalAdCost / totalSubtotal * 100) : 0;
      var feesPct = totalSubtotal > 0 ? (totalFees / totalSubtotal * 100) : 0;
      var profitPct = totalSubtotal > 0 ? (totalProfitLocal / totalSubtotal * 100) : 0;

      var sug = [];
      if (productPct > 35) sug.push('商品成本占销售额比例较高(' + productPct.toFixed(1) + '%)，可考虑优化供应链或提高客单价。');
      if (adPct > 25) sug.push('广告支出占比较高(' + adPct.toFixed(1) + '%)，可优化投放或提升自然转化。');
      if (feesPct > 15) sug.push('平台/费用占比(' + feesPct.toFixed(1) + '%)，关注佣金与活动成本。');
      if (profitPct < 10 && totalSubtotal > 0) sug.push('整体利润率偏低(' + profitPct.toFixed(1) + '%)，建议从成本、定价或高毛利品类入手。');
      if (affiliateQtyPct > 30) sug.push('联盟带货销量占比' + affiliateQtyPct.toFixed(1) + '%，可重点维护高佣金转化产品。');
      if (affiliateQtyPct > 0 && affiliateQtyPct < 10) sug.push('联盟带货占比较低(' + affiliateQtyPct.toFixed(1) + '%)，可拓展达人/联盟渠道提升销量。');
      var topByProfit = Object.keys(bySku).map(function(k) { return { k: k, v: bySku[k] }; }).sort(function(a,b) { return b.v.profitCny - a.v.profitCny; }).slice(0, 5);
      if (topByProfit.length) sug.push('利润贡献前五：' + topByProfit.map(function(x) { return x.v.name.substring(0,15) + (x.v.name.length > 15 ? '...' : ''); }).join('、'));

      var box = document.getElementById('summaryBox');
      var profitClass = totalProfitLocal >= 0 ? 'profit-positive' : 'profit-negative';
      box.innerHTML =
        '<div class="summary-grid">' +
        '<div class="summary-item"><span class="label">总利润</span><div class="value ' + profitClass + '">当地 ' + totalProfitLocal.toFixed(2) + '<br>¥' + totalProfitCny.toFixed(2) + '</div></div>' +
        '<div class="summary-item"><span class="label">卖家折扣后小计合计</span><div class="value">当地 ' + totalSubtotal.toFixed(2) + '<br>¥' + (totalSubtotal * rate).toFixed(2) + '</div></div>' +
        '<div class="summary-item"><span class="label">商品成本占小计</span><div class="value">' + productPct.toFixed(1) + '%</div></div>' +
        '<div class="summary-item"><span class="label">广告成本占小计</span><div class="value">' + adPct.toFixed(1) + '%</div></div>' +
        '<div class="summary-item"><span class="label">平台/费用占小计</span><div class="value">' + feesPct.toFixed(1) + '%</div></div>' +
        '<div class="summary-item"><span class="label">利润率(利润/小计)</span><div class="value">' + profitPct.toFixed(1) + '%</div></div>' +
        '<div class="summary-item"><span class="label">广告耗费(¥)</span><div class="value">¥' + (totalAdCost * rate).toFixed(2) + '</div></div>' +
        '<div class="summary-item"><span class="label">联盟带货销量占比</span><div class="value">' + affiliateQtyPct.toFixed(1) + '%</div></div>' +
        '<div class="summary-item"><span class="label">联盟带货销售额占比</span><div class="value">' + affiliateSubtotalPct.toFixed(1) + '%</div></div>' +
        '<div class="summary-item"><span class="label">联盟带货订单占比</span><div class="value">' + affiliateOrderPct.toFixed(1) + '%</div></div>' +
        '<div class="summary-item"><span class="label">本土发货订单数</span><div class="value">' + localShippingOrderCount + '</div></div>' +
        '<div class="summary-item"><span class="label">本土发货费用(¥)</span><div class="value">¥' + totalLocalShippingCostCny.toFixed(2) + '</div></div>' +
        '</div>' +
        '<div class="summary-box" style="margin-top:12px"><h3>各产品销量与利润</h3><table class="summary-product-table"><thead><tr><th>商品图</th><th>商品</th><th class="num">销量</th><th class="num">利润(当地/¥)</th><th class="num">联盟带货比例</th></tr></thead><tbody>' +
        Object.keys(bySku).map(function(k) {
          var v = bySku[k];
          var pL = v.profitLocal;
          var pC = v.profitCny;
          var affPct = v.qty > 0 ? ((v.affiliateQty || 0) / v.qty * 100) : 0;
          var imgCell = v.image_url
            ? '<td><img class="summary-product-img" src="' + escapeHtml(v.image_url) + '" alt="" onerror="this.style.display=\\'none\\';this.nextElementSibling.style.display=\\'inline\\';"><span class="summary-product-img-none" style="display:none">无图</span></td>'
            : '<td><span class="summary-product-img-none">无图</span></td>';
          var pClass = pL >= 0 ? 'profit-positive' : 'profit-negative';
          return '<tr>' + imgCell + '<td class="product-name" title="' + escapeHtml(v.name) + '">' + escapeHtml(v.name) + '</td><td class="num">' + v.qty + '</td><td class="num ' + pClass + '">' + pL.toFixed(2) + ' / ¥' + pC.toFixed(2) + '</td><td class="num">' + affPct.toFixed(1) + '%</td></tr>';
        }).join('') +
        '</tbody></table></div>' +
        (sug.length ? '<div class="suggestions"><h3>建议</h3><ul>' + sug.map(function(s){ return '<li>' + escapeHtml(s) + '</li>'; }).join('') + '</ul></div>' : '');
    }

    function renderTable(rate) {
      var table = document.getElementById('orderTable');
      var colgroup = document.getElementById('orderTableColgroup');
      var thead = document.getElementById('theadRow');
      var tbody = document.getElementById('tbody');
      thead.innerHTML = '';
      tbody.innerHTML = '';
      colgroup.innerHTML = '';

      var baseHeaders = ['日期', '订单ID', '商品图', 'SKU ID', '商品名称', '规格', '数量'];
      var sumHeaders = ['卖家折扣后小计(当地/¥)', '结算金额(当地/¥)', '销售收入(当地/¥)', '商品成本(¥)', '广告成本(当地/¥)', '单笔利润(当地/¥)', '利润率', '本土发货'];
      var numCols = baseHeaders.length + sumHeaders.length + feeColumns.length;
      for (var c = 0; c < numCols; c++) {
        var col = document.createElement('col');
        col.style.minWidth = (c < 3 ? 90 : 82) + 'px';
        col.style.width = (c < 3 ? 90 : 82) + 'px';
        colgroup.appendChild(col);
      }

      function addTh(text, className, title, colIndex) {
        var th = document.createElement('th');
        if (className) th.className = className;
        if (title) th.title = title;
        th.setAttribute('data-col-index', colIndex);
        th.innerHTML = '<span>' + escapeHtml(text) + '</span><div class="resizer"></div>';
        thead.appendChild(th);
      }
      var ci = 0;
      baseHeaders.forEach(function (h) { addTh(h, '', '', ci++); });
      sumHeaders.forEach(function (h) { addTh(h, 'num', '', ci++); });
      feeColumns.forEach(function (fc) { addTh(fc.en + ' / ' + fc.cn, 'num th-fee', fc.en, ci++); });

      var totalQty = 0, totalSubtotal = 0, totalSettlement = 0, totalRevenue = 0, totalProductCost = 0, totalAdCost = 0, totalProfitLocal = 0, totalProfitCny = 0;
      var totalFeesByIndex = {};
      var localShippingCount = 0;
      rows.forEach(function (r) {
        var tr = document.createElement('tr');
        if (r.cost_matched === false) tr.classList.add('row-no-cost-match');
        if (r.local_shipping) { tr.classList.add('row-local-shipping'); localShippingCount += 1; }
        var imgCell = r.image_url
          ? '<td><img class="product-img" src="' + escapeHtml(r.image_url) + '" alt="" onerror="this.style.display=\\'none\\';this.nextElementSibling.style.display=\\'flex\\';"><span class="product-img-none" style="display:none">无图</span></td>'
          : '<td><span class="product-img-none">无图</span></td>';
        var st = r.subtotal || 0;
        var settlementCny = r.settlement * rate;
        var revenueCny = r.revenue * rate;
        var adCostCny = r.ad_cost * rate;
        var profitLocal = r.settlement - r.product_cost / rate - r.ad_cost;
        var profitCny = r.settlement * rate - r.product_cost - r.ad_cost * rate;
        var localFee = getLocalShippingFee();
        if (r.local_shipping && localFee > 0) {
          profitLocal -= localFee / rate;
          profitCny -= localFee;
        }
        var marginPct = st > 0 ? (profitLocal / st * 100) : null;
        var profitClass = profitLocal >= 0 ? 'profit-positive' : 'profit-negative';

        totalQty += r.qty || 0;
        totalSubtotal += st;
        totalSettlement += r.settlement || 0;
        totalRevenue += r.revenue || 0;
        totalProductCost += r.product_cost || 0;
        totalAdCost += r.ad_cost || 0;
        totalProfitLocal += profitLocal;
        totalProfitCny += profitCny;
        (r.fees || []).forEach(function (v, i) { totalFeesByIndex[i] = (totalFeesByIndex[i] || 0) + v; });

        var fmt = function (localVal, cnyVal) { return (localVal != null ? localVal.toFixed(2) : '—') + ' / ¥' + (cnyVal != null ? cnyVal.toFixed(2) : '—'); };
        var html = '<td>' + escapeHtml(r.date) + '</td><td>' + escapeHtml(r.order_id) + '</td>' + imgCell +
          '<td>' + escapeHtml(r.sku_id) + '</td><td class="product-name" title="' + escapeHtml(r.product_name) + '">' + escapeHtml(r.product_name) + '</td>' +
          '<td>' + escapeHtml(r.sku_name) + '</td><td class="num">' + escapeHtml(r.qty) + '</td>' +
          '<td class="num">' + fmt(st, st * rate) + '</td><td class="num">' + fmt(r.settlement, settlementCny) + '</td><td class="num">' + fmt(r.revenue, revenueCny) + '</td>' +
          '<td class="num">— / ¥' + Number(r.product_cost).toFixed(2) + '</td><td class="num">' + fmt(r.ad_cost, adCostCny) + '</td>' +
          '<td class="num ' + profitClass + '">' + fmt(profitLocal, profitCny) + '</td>' +
          '<td class="num">' + (marginPct != null ? marginPct.toFixed(1) + '%' : '-') + '</td>' +
          '<td>' + (r.local_shipping ? '是' : '否') + '</td>';
        (r.fees || []).forEach(function (v) {
          html += '<td class="num">' + fmt(v, v * rate) + '</td>';
        });
        tr.innerHTML = html;
        tbody.appendChild(tr);
      });

      var totalsRow = document.getElementById('totalsRow');
      if (totalsRow) {
        var fmt = function (localVal, cnyVal) { return (localVal != null ? localVal.toFixed(2) : '—') + ' / ¥' + (cnyVal != null ? cnyVal.toFixed(2) : '—'); };
        var totalMarginPct = totalSubtotal > 0 ? (totalProfitLocal / totalSubtotal * 100) : null;
        var totalProfitClass = totalProfitLocal >= 0 ? 'profit-positive' : 'profit-negative';
        var totalsHtml = '<td>合计</td><td></td><td></td><td></td><td></td><td></td>' +
          '<td class="num">' + totalQty + '</td>' +
          '<td class="num">' + fmt(totalSubtotal, totalSubtotal * rate) + '</td>' +
          '<td class="num">' + fmt(totalSettlement, totalSettlement * rate) + '</td>' +
          '<td class="num">' + fmt(totalRevenue, totalRevenue * rate) + '</td>' +
          '<td class="num">— / ¥' + totalProductCost.toFixed(2) + '</td>' +
          '<td class="num">' + fmt(totalAdCost, totalAdCost * rate) + '</td>' +
          '<td class="num ' + totalProfitClass + '">' + fmt(totalProfitLocal, totalProfitCny) + '</td>' +
          '<td class="num">' + (totalMarginPct != null ? totalMarginPct.toFixed(1) + '%' : '-') + '</td>' +
          '<td>' + localShippingCount + ' 单</td>';
        for (var fi = 0; fi < feeColumns.length; fi++) {
          var fv = totalFeesByIndex[fi] || 0;
          totalsHtml += '<td class="num">' + fmt(fv, fv * rate) + '</td>';
        }
        totalsRow.innerHTML = totalsHtml;
      }

      setupColumnResize();
    }

    function setupColumnResize() {
      var colgroup = document.getElementById('orderTableColgroup');
      var theadRow = document.getElementById('theadRow');
      if (!colgroup || !colgroup.children.length || !theadRow) return;
      var resizers = theadRow.querySelectorAll('.resizer');
      for (var i = 0; i < resizers.length; i++) {
        (function (idx) {
          var resizer = resizers[idx];
          var th = resizer.closest('th');
          if (!th) return;
          var colIndex = parseInt(th.getAttribute('data-col-index'), 10);
          var col = colgroup.children[colIndex];
          if (!col) return;
          resizer.onmousedown = function (e) {
            e.preventDefault();
            var startX = e.clientX;
            var startW = col.offsetWidth || 82;
            function move(e) {
              var dx = e.clientX - startX;
              var newW = Math.max(40, startW + dx);
              col.style.width = newW + 'px';
              col.style.minWidth = newW + 'px';
              var wrap = document.querySelector('.table-wrap');
              if (!wrap) return;
              var cols = colgroup.children;
              if (colIndex === 0) {
                wrap.querySelectorAll('thead th:nth-child(2), tbody td:nth-child(2)').forEach(function (el) { el.style.left = newW + 'px'; });
                var w1 = cols[1] ? cols[1].offsetWidth : 90;
                wrap.querySelectorAll('thead th:nth-child(3), tbody td:nth-child(3)').forEach(function (el) { el.style.left = (newW + w1) + 'px'; });
              } else if (colIndex === 1) {
                var w0 = cols[0] ? cols[0].offsetWidth : 90;
                wrap.querySelectorAll('thead th:nth-child(3), tbody td:nth-child(3)').forEach(function (el) { el.style.left = (w0 + newW) + 'px'; });
              }
            }
            function up() {
              document.removeEventListener('mousemove', move);
              document.removeEventListener('mouseup', up);
            }
            document.addEventListener('mousemove', move);
            document.addEventListener('mouseup', up);
          };
        })(i);
      }
    }

    document.getElementById('rate').oninput = refreshAll;
    document.getElementById('rate').onchange = refreshAll;
    document.getElementById('localShippingFee').oninput = refreshAll;
    document.getElementById('localShippingFee').onchange = refreshAll;
    refreshAll();
  </script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


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

    for csv_path in csv_files:
        country, start, end = parse_income_filename(csv_path)
        header, rows = load_income_rows_from_file(csv_path)
        if not header or not rows:
            print(f"跳过（表为空）：{csv_path.name}")
            continue
        order_rows = build_order_rows(header, rows, sku_costs, sku_costs_by_prefix, product_by_sku, product_by_prefix)
        out_name = f"Outprofit_{country}_{start}_{end}.html"
        output_path = output_dir / out_name
        write_html(order_rows, output_path, meta={"country": country, "start": start, "end": end})
        print(f"已生成：{output_path}（{len(order_rows)} 条订单行，国家={country} 结算期={start}—{end}）")

    if not sku_costs and not sku_costs_by_prefix:
        print("提示：未找到 sku_costs.csv，商品成本均为 0。请从 sku_cost_input.html 导出后放入 product_cost。")


if __name__ == "__main__":
    main()
