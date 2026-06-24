"""结算 CSV 扫描 + 利润汇总（成本来自商品目录 sku_costs）。"""

from __future__ import annotations

import importlib.util
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from core.config import ROOT, get
from modules.finance.catalog_costs import load_product_maps, load_sku_cost_maps

INCOME_DIR = ROOT / "CURSOR" / "Income_Data"

REGION_RATE_KEY = {"PH": "PHP", "MY": "MYR", "TH": "THB", "VN": "VND"}


def default_rates() -> dict[str, float]:
    cfg = get("exchange_rates") or {}
    return {
        "PH": float(cfg.get("PHP") or 0.118),
        "MY": float(cfg.get("MYR") or 1.75),
        "TH": float(cfg.get("THB") or 0.2218),
        "VN": float(cfg.get("VND") or 0.000266),
    }


def default_ad_rates() -> dict[str, float]:
    """各国广告成本占「卖家折扣后小计」的百分比，默认 20。"""
    cfg = get("settlement_ad_rates") or {}
    base = float(cfg.get("default") or 20)
    return {
        "PH": float(cfg.get("PH") or base),
        "MY": float(cfg.get("MY") or base),
        "TH": float(cfg.get("TH") or base),
        "VN": float(cfg.get("VN") or base),
    }


def _load_cursor_module(filename: str, mod_name: str):
    import sys

    cursor_dir = str(ROOT / "CURSOR")
    if cursor_dir not in sys.path:
        sys.path.insert(0, cursor_dir)
    path = ROOT / "CURSOR" / filename
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bop():
    return _load_cursor_module("build_order_profit_page.py", "cursor_bop")


def _total():
    _bop()
    return _load_cursor_module("build_total_profit_page.py", "cursor_total")


def fee_column_defs() -> list[dict]:
    """兼容旧接口；明细页以 CSV 实际表头为准。"""
    bop = _bop()
    return [{"en": en, "cn": cn} for en, cn in bop.FEE_COLUMNS]


def _header_defs(header: list[str]) -> list[dict]:
    out = []
    for i, h in enumerate(header):
        name = (h or "").strip() or f"列{i + 1}"
        out.append({"index": i, "name": name})
    return out


def _classify_row(bop, header: list[str], row: list[str]) -> str:
    key_type = bop._col_index(header, "Type ", "Type")
    key_sku = bop._col_index(header, "SKU ID")
    key_qty = bop._col_index(header, "Quantity")
    key_settlement = bop._col_index(header, "Total settlement amount")
    key_revenue = bop._col_index(header, "Total Revenue")
    if key_settlement < 0 or key_revenue < 0:
        return "other"
    typ = (row[key_type] or "").strip() if key_type >= 0 and len(row) > key_type else ""
    if bop.is_gmv_ads_payment_row(typ):
        return "gmv_ads"
    sku = bop.norm_sku(row[key_sku] if key_sku >= 0 and len(row) > key_sku else "")
    qty = bop.parse_number(row[key_qty] if key_qty >= 0 and len(row) > key_qty else 0)
    if not sku and qty == 0:
        return "other"
    return "order"


def _column_is_numeric(bop, rows: list[list[str]], col_idx: int) -> bool:
    has_nonzero = False
    for row in rows:
        if col_idx >= len(row):
            continue
        cell = (row[col_idx] or "").strip()
        if not cell:
            continue
        if re.search(r"[a-zA-Z\u4e00-\u9fff]", cell) and "E" not in cell.upper():
            return False
        if abs(bop.parse_number(cell)) > 1e-12:
            has_nonzero = True
    return has_nonzero


def _chinese_label(name: str, bop) -> str:
    name = (name or "").strip()
    if not name:
        return name
    if re.search(r"[\u4e00-\u9fff]", name):
        return name
    for en, cn in bop.FEE_COLUMNS:
        if name == en or name.strip() == en.strip():
            return cn
    extras = bop.INCOME_HEADER_EXTRA_NAMES.get(name)
    if extras:
        return extras[0]
    for en, extras in bop.INCOME_HEADER_EXTRA_NAMES.items():
        if name == en:
            for fen, fcn in bop.FEE_COLUMNS:
                if fen == en:
                    return fcn
            return extras[0]
        for ex in extras:
            if name == ex:
                for fen, fcn in bop.FEE_COLUMNS:
                    if fen == en:
                        return fcn
                return ex
    return name


def _skip_fee_column(raw_name: str, cn_name: str) -> bool:
    blob = f"{raw_name} {cn_name}".lower()
    skip_keys = (
        "statement date", "结算日期", "statement id", "结算单", "currency", "货币",
        "type", "交易类型", "order", "订单id", "调整单", "sku id", "quantity", "数量",
        "product name", "商品名称", "sku name", "sku 名称", "related order", "相关订单",
        "客户付款银行", "预估包裹", "计费包裹", "customer payment", "客户付款", "客户实付",
        "买家支付",
    )
    return any(k in blob for k in skip_keys)


def _skip_revenue_column(raw_name: str, cn_name: str) -> bool:
  blob = f"{raw_name} {cn_name}".lower()
  revenue_keys = (
      "total revenue", "总收入", "subtotal", "小计", "gross sales", "折扣前",
      "折扣后小计", "seller discount", "商家折扣", "seller discounts",
      "total settlement", "结算总", "结算金额", "settlement amount",
      "refund subtotal", "退款小计", "refund of seller",
  )
  return any(k in blob for k in revenue_keys)


def _aggregate_fee_composition(
    header: list[str], rows: list[list[str]], rate: float, bop
) -> tuple[list[dict], float]:
    pay_idx = bop._col_index(header, "Customer payment")
    payment_total = 0.0
    if pay_idx >= 0:
        for row in rows:
            if pay_idx < len(row):
                payment_total += bop.parse_number(row[pay_idx])
    payment_total = round(payment_total, 2)

    items: list[dict] = []
    for i, col in enumerate(header):
        raw = (col or "").strip() or f"列{i + 1}"
        cn = _chinese_label(raw, bop)
        if _skip_fee_column(raw, cn) or _skip_revenue_column(raw, cn):
            continue
        if not _column_is_numeric(bop, rows, i):
            continue
        total = 0.0
        for row in rows:
            if i < len(row):
                total += bop.parse_number(row[i])
        total = round(total, 2)
        if total == 0:
            continue
        pct = round(abs(total) / payment_total * 100, 2) if payment_total else None
        items.append(
            {
                "cn": cn,
                "local": total,
                "cny": round(total * rate, 2),
                "pct": pct,
            }
        )
    items.sort(key=lambda x: abs(x["local"]), reverse=True)
    return items, payment_total


def _merge_fee_composition(items: list[dict], payment_total: float) -> list[dict]:
    by_cn: dict[str, dict] = {}
    for item in items:
        cn = item["cn"]
        if cn not in by_cn:
            by_cn[cn] = {"cn": cn, "local": 0.0, "cny": 0.0}
        by_cn[cn]["local"] = round(by_cn[cn]["local"] + float(item["local"]), 2)
        by_cn[cn]["cny"] = round(by_cn[cn]["cny"] + float(item.get("cny", 0)), 2)
    merged = sorted(by_cn.values(), key=lambda x: abs(x["local"]), reverse=True)
    out = []
    for m in merged:
        if m["local"] == 0:
            continue
        m["pct"] = (
            round(abs(m["local"]) / payment_total * 100, 2) if payment_total else None
        )
        out.append(m)
    return out


def _aggregate_csv_columns(header: list[str], rows: list[list[str]], rate: float) -> list[dict]:
    """兼容旧调用：返回带 cn 的费用构成。"""
    bop = _bop()
    items, _ = _aggregate_fee_composition(header, rows, rate, bop)
    return items


def _build_all_display_rows(
    header: list[str],
    rows: list[list[str]],
    order_rows: list[dict],
    rate: float,
) -> tuple[list[dict], list[dict]]:
    bop = _bop()
    headers = _header_defs(header)
    oi = 0
    display: list[dict] = []
    for row in rows:
        cells = [row[i] if i < len(row) else "" for i in range(len(header))]
        kind = _classify_row(bop, header, row)
        item: dict = {"cells": cells, "row_kind": kind}
        if kind == "order" and oi < len(order_rows):
            o = order_rows[oi]
            oi += 1
            pc = float(o["product_cost"])
            ad = float(o["ad_cost"])
            stl = float(o["settlement"])
            sub = float(o["subtotal"])
            profit_local = round(stl - ad - (pc / rate if rate else 0), 2)
            item.update(
                {
                    "image_url": o.get("image_url") or "",
                    "product_cost": pc,
                    "ad_cost": ad,
                    "cost_matched": o.get("cost_matched"),
                    "profit_cny": round(stl * rate - ad * rate - pc, 2) if rate else None,
                    "profit_local": profit_local,
                    "margin_pct": round(profit_local / sub * 100, 1) if sub else None,
                }
            )
        display.append(item)
    return headers, display


def _parse_day_token(token: str) -> date | None:
    token = (token or "").strip()
    if not re.fullmatch(r"\d{6}", token):
        return None
    try:
        return datetime.strptime(token, "%y%m%d").date()
    except ValueError:
        return None


def _file_period(path: Path) -> tuple[date, date] | None:
    m = re.search(r"income_[A-Z]{2}_(\d{6})_(\d{6})\.csv$", path.name, re.I)
    if not m:
        return None
    start = _parse_day_token(m.group(1))
    end = _parse_day_token(m.group(2))
    if not start or not end:
        return None
    return start, end


def list_income_files(start: date | None = None, end: date | None = None) -> list[dict]:
    if not INCOME_DIR.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(INCOME_DIR.glob("income_*.csv")):
        period = _file_period(p)
        if period is None:
            continue
        file_start, file_end = period
        if start and file_end < start:
            continue
        if end and file_start > end:
            continue
        bop = _bop()
        country, s, e = bop.parse_income_filename(p)
        out.append(
            {
                "file": p.name,
                "region": (country or "").upper(),
                "day": file_end.isoformat(),
                "period_start": file_start.isoformat(),
                "period_end": file_end.isoformat(),
                "period": f"{s}—{e}",
            }
        )

    if start and end and out:
        by_region: dict[str, list[dict]] = {}
        for item in out:
            by_region.setdefault(item["region"], []).append(item)
        picked: list[dict] = []
        for reg, items in by_region.items():
            covering = [
                f
                for f in items
                if date.fromisoformat(f["period_start"]) <= start
                and date.fromisoformat(f["period_end"]) >= end
            ]
            if covering:
                best = max(
                    covering,
                    key=lambda f: (
                        date.fromisoformat(f["period_end"])
                        - date.fromisoformat(f["period_start"])
                    ).days,
                )
                picked.append(best)
            else:
                picked.extend(items)
        out = sorted(picked, key=lambda f: (f["region"], f["period_start"], f["file"]))

    return out


def data_range() -> dict:
    files = list_income_files()
    if not files:
        return {"min": None, "max": None, "file_count": 0}
    starts = [f["period_start"] for f in files]
    ends = [f["period_end"] for f in files]
    return {"min": min(starts), "max": max(ends), "file_count": len(files)}


def _build_order_rows(header, rows, ad_rate_pct: float):
    bop = _bop()
    sku_costs, sku_prefix = load_sku_cost_maps()
    prod_sku, prod_prefix = load_product_maps()
    order_rows = bop.build_order_rows(
        header, rows, sku_costs, sku_prefix, prod_sku, prod_prefix
    )
    rate_frac = float(ad_rate_pct or 0) / 100.0
    for r in order_rows:
        r["ad_cost"] = round(float(r.get("subtotal") or 0) * rate_frac, 2)
        r["ad_rate_pct"] = ad_rate_pct
    return order_rows



def _profit_cny_from_orders(
    order_rows: list[dict],
    logistics: float,
    rate: float,
) -> float:
    """订单行利润合计 + 物流赔款（当地→人民币）。"""
    order_profit = 0.0
    for o in order_rows:
        pc = float(o.get("product_cost") or 0)
        ad = float(o.get("ad_cost") or 0)
        stl = float(o.get("settlement") or 0)
        if rate:
            order_profit += stl * rate - ad * rate - pc
    return round(order_profit + logistics * rate, 2)


def _summarize_rows(
    header: list[str],
    rows: list[list[str]],
    region: str,
    rate: float,
    ad_pct: float,
    *,
    file: str = "",
    statement_id: str = "",
    statement_date: str = "",
    day: str = "",
) -> dict | None:
    bop = _bop()
    total_mod = _total()
    if not header or not rows:
        return None

    sku_costs, sku_prefix = load_sku_cost_maps()
    prod_sku, prod_prefix = load_product_maps()

    stats = total_mod.compute_country_stats(
        header, rows, sku_costs, sku_prefix, prod_sku, prod_prefix
    )
    (
        total_settlement,
        gmv_ads,
        order_settlement,
        subtotal_local,
        logistics,
        product_cost_neg,
        currency,
    ) = stats

    product_cost_cny = round(-product_cost_neg, 2)

    order_rows = _build_order_rows(header, rows, ad_pct)
    matched = sum(1 for r in order_rows if r.get("cost_matched"))
    missing_cost = len(order_rows) - matched
    ad_cost_local = round(sum(r["ad_cost"] for r in order_rows), 2)

    fee_breakdown, customer_payment_total = _aggregate_fee_composition(
        header, rows, rate, bop
    )
    fee_breakdown_cny = [{**f} for f in fee_breakdown]

    # 与 CURSOR TotalProfit 一致：(总结算+物流赔款)×汇率 + 商品成本(负)
    profit_cny = round((total_settlement + logistics) * rate + product_cost_neg, 2)

    return {
        "file": file,
        "region": region,
        "statement_id": statement_id,
        "statement_date": statement_date,
        "day": day,
        "currency": currency,
        "total_settlement": total_settlement,
        "gmv_ads": gmv_ads,
        "order_settlement": order_settlement,
        "subtotal_local": subtotal_local,
        "logistics_reimbursement": logistics,
        "product_cost_cny": product_cost_cny,
        "ad_cost_local": ad_cost_local,
        "ad_rate_pct": ad_pct,
        "order_lines": len(order_rows),
        "cost_matched_lines": matched,
        "cost_missing_lines": missing_cost,
        "rate": rate,
        "profit_cny": profit_cny,
        "fee_breakdown": fee_breakdown,
        "fee_breakdown_cny": fee_breakdown_cny,
        "customer_payment_total": customer_payment_total,
        "customer_payment_cny": round(customer_payment_total * rate, 2),
        "gmv_ads_cny": round(gmv_ads * rate, 2),
        "logistics_cny": round(logistics * rate, 2),
        "ad_cost_cny": round(ad_cost_local * rate, 2),
        "order_settlement_cny": round(order_settlement * rate, 2),
        "total_settlement_cny": round(total_settlement * rate, 2),
        "row_count": len(rows),
    }


def summarize_file(
    csv_path: Path,
    rates: dict[str, float] | None = None,
    ad_rates: dict[str, float] | None = None,
) -> dict | None:
    bop = _bop()
    rates = rates or default_rates()
    ad_rates = ad_rates or default_ad_rates()

    header, rows = bop.load_income_rows_from_file(str(csv_path))
    if not header or not rows:
        return None

    country, start, end = bop.parse_income_filename(csv_path)
    reg = (country or "").upper()
    rate = float(rates.get(reg) or default_rates().get(reg) or 0)
    ad_pct = float(ad_rates.get(reg) or default_ad_rates().get(reg, 20))

    key_sid = bop._col_index(header, "Statement ID")
    key_date = bop._col_index(header, "Statement Date")
    statement_id = ""
    statement_date = ""
    if rows:
        if key_sid >= 0 and len(rows[0]) > key_sid:
            statement_id = (rows[0][key_sid] or "").strip()
        if key_date >= 0 and len(rows[0]) > key_date:
            statement_date = (rows[0][key_date] or "").strip()

    row = _summarize_rows(
        header,
        rows,
        reg,
        rate,
        ad_pct,
        file=csv_path.name,
        statement_id=statement_id,
        statement_date=statement_date,
        day=(_file_day(csv_path) or date.today()).isoformat(),
    )
    if not row:
        return None
    row["period"] = f"{start}—{end}"
    return row


def statements_from_file(
    csv_path: Path,
    rates: dict[str, float] | None = None,
    ad_rates: dict[str, float] | None = None,
) -> list[dict]:
    bop = _bop()
    rates = rates or default_rates()
    ad_rates = ad_rates or default_ad_rates()

    header, rows = bop.load_income_rows_from_file(str(csv_path))
    if not header or not rows:
        return []

    country, _, _ = bop.parse_income_filename(csv_path)
    reg = (country or "").upper()
    rate = float(rates.get(reg) or default_rates().get(reg) or 0)
    ad_pct = float(ad_rates.get(reg) or default_ad_rates().get(reg, 20))

    key_sid = bop._col_index(header, "Statement ID")
    key_date = bop._col_index(header, "Statement Date")
    groups: dict[str, list[list[str]]] = {}
    dates: dict[str, str] = {}
    for row in rows:
        sid = (row[key_sid] or "").strip() if key_sid >= 0 and len(row) > key_sid else ""
        if not sid:
            sid = f"__file_{csv_path.name}"
        groups.setdefault(sid, []).append(row)
        if key_date >= 0 and len(row) > key_date:
            d = (row[key_date] or "").strip()
            if d:
                dates[sid] = d

    out: list[dict] = []
    for sid, group_rows in groups.items():
        item = _summarize_rows(
            header,
            group_rows,
            reg,
            rate,
            ad_pct,
            file=csv_path.name,
            statement_id=sid if not sid.startswith("__file_") else "",
            statement_date=dates.get(sid, ""),
            day=(_file_day(csv_path) or date.today()).isoformat(),
        )
        if item:
            out.append(item)
    return out


def _enrich_order_row(
    o: dict,
    region: str,
    rate: float,
    currency: str,
    file: str,
) -> dict:
    bop = _bop()
    pc = float(o.get("product_cost") or 0)
    ad = float(o.get("ad_cost") or 0)
    stl = float(o.get("settlement") or 0)
    sub = float(o.get("subtotal") or 0)
    profit_local = round(stl - ad - (pc / rate if rate else 0), 2)
    customer_payment = 0.0
    fees = o.get("fees") or []
    for i, (en, _) in enumerate(bop.FEE_COLUMNS):
        if en == "Customer payment" and i < len(fees):
            customer_payment = float(fees[i] or 0)
            break
    return {
        "region": region,
        "currency": currency,
        "file": file,
        "date": o.get("date") or "",
        "order_id": o.get("order_id") or "",
        "sku_id": o.get("sku_id") or "",
        "product_name": o.get("product_name") or "",
        "sku_name": o.get("sku_name") or "",
        "qty": o.get("qty") or 0,
        "settlement": stl,
        "revenue": float(o.get("revenue") or 0),
        "subtotal": sub,
        "customer_payment": customer_payment,
        "product_cost": pc,
        "ad_cost": ad,
        "fees": list(o.get("fees") or []),
        "local_shipping": bool(o.get("local_shipping")),
        "cost_matched": bool(o.get("cost_matched")),
        "profit_local": profit_local,
        "profit_cny": round(stl * rate - ad * rate - pc, 2) if rate else None,
        "margin_pct": round(profit_local / sub * 100, 1) if sub else None,
        "image_url": o.get("image_url") or "",
    }


def _cursor_profit_row(meta: dict, row: dict, rate: float, bop) -> dict:
    country, start, end = bop.parse_income_filename(INCOME_DIR / meta["file"])
    return {
        "country": row["region"],
        "period": f"{start}—{end}",
        "currency": row["currency"],
        "file": meta["file"],
        "total_settlement": row["total_settlement"],
        "gmv_ads_settlement": row["gmv_ads"],
        "effective_settlement": row["order_settlement"],
        "subtotal_local": row["subtotal_local"],
        "logistics_reimbursement": row["logistics_reimbursement"],
        "product_cost_cny": -row["product_cost_cny"],
        "rate": rate,
    }


def _collect_orders_for_files(
    files: list[dict],
    rates: dict[str, float],
    ad_rates: dict[str, float],
) -> dict[str, list[dict]]:
    orders_by_region: dict[str, list[dict]] = {}
    bop = _bop()
    for meta in files:
        path = INCOME_DIR / meta["file"]
        header, rows = bop.load_income_rows_from_file(str(path))
        if not header or not rows:
            continue
        reg = meta["region"]
        rate = float(rates.get(reg) or default_rates().get(reg) or 0)
        ad_pct = float(ad_rates.get(reg) or default_ad_rates().get(reg, 20))
        row = _summarize_rows(header, rows, reg, rate, ad_pct, file=meta["file"], day=meta["day"])
        if not row:
            continue
        orders_by_region.setdefault(reg, []).extend(
            _orders_from_rows(header, rows, reg, rate, ad_pct, row["currency"], meta["file"])
        )
    for reg in orders_by_region:
        orders_by_region[reg].sort(
            key=lambda x: (x.get("date") or "", x.get("order_id") or ""),
            reverse=True,
        )
    return orders_by_region


def orders_for_period(
    start: date,
    end: date,
    rates: dict[str, float] | None = None,
    ad_rates: dict[str, float] | None = None,
) -> dict:
    rates = rates or default_rates()
    ad_rates = ad_rates or default_ad_rates()
    files = list_income_files(start, end)
    orders_by_region = _collect_orders_for_files(files, rates, ad_rates)
    return {
        "order_count": sum(len(v) for v in orders_by_region.values()),
        "orders_by_region": orders_by_region,
    }


def _orders_from_rows(
    header: list[str],
    rows: list[list[str]],
    region: str,
    rate: float,
    ad_pct: float,
    currency: str,
    file: str,
) -> list[dict]:
    order_rows = _build_order_rows(header, rows, ad_pct)
    return [
        _enrich_order_row(o, region, rate, currency, file) for o in order_rows
    ]


def summarize_period(
    start: date,
    end: date,
    rates: dict[str, float] | None = None,
    ad_rates: dict[str, float] | None = None,
) -> dict:
    rates = rates or default_rates()
    ad_rates = ad_rates or default_ad_rates()
    files = list_income_files(start, end)
    total_profit_rows: list[dict] = []
    orders_by_region: dict[str, list[dict]] = {}
    by_region: dict[str, dict] = {}
    fee_raw_by_region: dict[str, list[dict]] = {}
    bop = _bop()

    for meta in files:
        path = INCOME_DIR / meta["file"]
        header, rows = bop.load_income_rows_from_file(str(path))
        if not header or not rows:
            continue

        reg = meta["region"]
        rate = float(rates.get(reg) or default_rates().get(reg) or 0)
        ad_pct = float(ad_rates.get(reg) or default_ad_rates().get(reg, 20))

        row = _summarize_rows(
            header,
            rows,
            reg,
            rate,
            ad_pct,
            file=meta["file"],
            day=meta["day"],
        )
        if not row:
            continue

        total_profit_rows.append(_cursor_profit_row(meta, row, rate, bop))
        orders_by_region.setdefault(reg, []).extend(
            _orders_from_rows(header, rows, reg, rate, ad_pct, row["currency"], meta["file"])
        )

        agg = by_region.setdefault(
            reg,
            {
                "region": reg,
                "currency": row["currency"],
                "order_lines": 0,
                "total_settlement": 0.0,
                "gmv_ads": 0.0,
                "order_settlement": 0.0,
                "subtotal_local": 0.0,
                "logistics_reimbursement": 0.0,
                "product_cost_cny": 0.0,
                "ad_cost_local": 0.0,
                "ad_cost_cny": 0.0,
                "cost_matched_lines": 0,
                "cost_missing_lines": 0,
                "rate": rate,
                "ad_rate_pct": ad_pct,
                "profit_cny": 0.0,
                "customer_payment_total": 0.0,
                "customer_payment_cny": 0.0,
                "gmv_ads_cny": 0.0,
                "logistics_cny": 0.0,
                "order_settlement_cny": 0.0,
                "total_settlement_cny": 0.0,
            },
        )
        for k in (
            "total_settlement",
            "gmv_ads",
            "order_settlement",
            "subtotal_local",
            "logistics_reimbursement",
            "product_cost_cny",
            "ad_cost_local",
            "ad_cost_cny",
            "order_lines",
            "cost_matched_lines",
            "cost_missing_lines",
            "customer_payment_total",
            "customer_payment_cny",
            "gmv_ads_cny",
            "logistics_cny",
            "order_settlement_cny",
            "total_settlement_cny",
        ):
            agg[k] = round(agg[k] + float(row[k]), 2)
        agg["profit_cny"] = round(agg["profit_cny"] + row["profit_cny"], 2)
        fee_raw_by_region.setdefault(reg, []).extend(row.get("fee_breakdown_cny", []))

    regions = []
    for reg in sorted(by_region):
        agg = by_region[reg]
        agg["fee_breakdown"] = _merge_fee_composition(
            fee_raw_by_region.get(reg, []), agg["customer_payment_total"]
        )
        regions.append(agg)

    for reg in orders_by_region:
        orders_by_region[reg].sort(
            key=lambda x: (x.get("date") or "", x.get("order_id") or ""),
            reverse=True,
        )

    total_profit_cny = round(
        sum(
            (r["total_settlement"] + r["logistics_reimbursement"]) * r["rate"]
            + r["product_cost_cny"]
            for r in total_profit_rows
        ),
        2,
    )
    total_cost_cny = round(
        sum(-r["product_cost_cny"] for r in total_profit_rows if r["product_cost_cny"]),
        2,
    )
    order_count = sum(len(v) for v in orders_by_region.values())

    total_profit_rows.sort(key=lambda r: (r.get("country") or "", r.get("period") or ""))

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "file_count": len(files),
        "order_count": order_count,
        "regions": regions,
        "orders_by_region": orders_by_region,
        "total_profit_rows": total_profit_rows,
        "total_profit_cny": total_profit_cny,
        "total_product_cost_cny": total_cost_cny,
        "rates": rates,
        "ad_rates": ad_rates,
        "cost_source": "catalog:sku_costs",
        "cost_stats": __import__(
            "modules.finance.catalog_costs", fromlist=["cost_stats"]
        ).cost_stats(),
    }


def order_rows_for_file(
    filename: str,
    rate: float | None = None,
    ad_rate_pct: float | None = None,
    statement_id: str | None = None,
    order_id: str | None = None,
) -> dict:
    path = INCOME_DIR / Path(filename).name
    if not path.is_file():
        raise FileNotFoundError(f"未找到 {filename}")

    bop = _bop()
    header, rows = bop.load_income_rows_from_file(str(path))
    if statement_id:
        key_sid = bop._col_index(header, "Statement ID")
        sid = statement_id.strip()
        if key_sid >= 0:
            rows = [
                r
                for r in rows
                if key_sid < len(r) and (r[key_sid] or "").strip() == sid
            ]
    if order_id:
        key_order = bop._col_index(header, "Order/adjustment ID  ", "Order/adjustment ID")
        oid = order_id.strip()
        if key_order >= 0:
            rows = [
                r
                for r in rows
                if key_order < len(r) and (r[key_order] or "").strip() == oid
            ]

    country, start, end = bop.parse_income_filename(path)
    reg = (country or "").upper()
    r = rate if rate is not None else default_rates().get(reg, 0)
    ad_pct = ad_rate_pct if ad_rate_pct is not None else default_ad_rates().get(reg, 20)

    order_rows = _build_order_rows(header, rows, ad_pct)
    headers, display_rows = _build_all_display_rows(header, rows, order_rows, r)
    summary = summarize_file(path, {reg: r}, {reg: ad_pct})
    if statement_id and summary:
        sub = _summarize_rows(
            header,
            rows,
            reg,
            r,
            ad_pct,
            file=path.name,
            statement_id=statement_id.strip(),
        )
        if sub:
            summary = sub

    return {
        "file": path.name,
        "region": reg,
        "period": f"{start}—{end}",
        "rate": r,
        "ad_rate_pct": ad_pct,
        "statement_id": statement_id or "",
        "order_id": order_id or "",
        "headers": headers,
        "column_count": len(headers),
        "rows": display_rows,
        "row_count": len(display_rows),
        "orders": display_rows,
        "summary": summary,
    }


def parse_iso_date(s: str) -> date:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
