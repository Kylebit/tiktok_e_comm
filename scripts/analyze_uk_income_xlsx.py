"""Analyze UK TikTok income settlement Excel (same format as SEA CSV Order details)."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "CURSOR"))

from core.db import connect, init_db
from modules.catalog.listings import list_products
from modules.catalog.sku_key import tk_match_key
from modules.finance.settlement_report import (
    _aggregate_fee_composition,
    _build_order_rows,
    _chinese_label,
    _skip_fee_column,
    _skip_revenue_column,
)

UK_SHOP_CIPHER = "UK_IMPORT_GB"
DEFAULT_GBP_CNY = 9.15
DEFAULT_AD_PCT = 20.0


def _load_bop():
    path = ROOT / "CURSOR" / "build_order_profit_page.py"
    spec = importlib.util.spec_from_file_location("cursor_bop", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def _load_xlsx_orders(path: Path) -> tuple[list[str], list[list[str]], pd.DataFrame]:
    df = pd.read_excel(path, sheet_name="Order details", header=0)
    df = df[df["Type"].astype(str).str.strip().eq("Order")].copy()
    header = [str(c) for c in df.columns]
    sku_idx = header.index("SKU ID") if "SKU ID" in header else -1
    rows: list[list[str]] = []
    bop = _load_bop()
    for _, r in df.iterrows():
        row: list[str] = []
        for c in df.columns:
            v = r[c]
            if pd.isna(v):
                row.append("")
            elif c == "SKU ID":
                fv = float(v)
                row.append(str(int(fv)) if fv == int(fv) else bop.norm_sku(v) or "")
            elif isinstance(v, float):
                row.append(str(int(v)) if v == int(v) else str(v))
            else:
                row.append(str(v))
        rows.append(row)
    return header, rows, df


def _load_batch_sku_maps(batch_xlsx: Path | None) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """sku_id and (product, variation) → seller_sku from UK batch export."""
    by_id: dict[str, str] = {}
    by_name: dict[tuple[str, str], str] = {}
    if not batch_xlsx or not batch_xlsx.is_file():
        return by_id, by_name
    df = pd.read_excel(batch_xlsx, sheet_name="Template", header=0)
    df = df[df["product_id"].astype(str).str.match(r"^\d+$", na=False)]
    for _, r in df.iterrows():
        sid = r.get("sku_id")
        if pd.isna(sid):
            continue
        sid_s = str(int(float(sid)))
        sk = str(r.get("seller_sku") or "").strip()
        if not sk:
            continue
        by_id[sid_s] = sk
        pname = str(r.get("product_name") or "").strip().lower()
        vname = str(r.get("variation_value") or sk).strip().lower()
        by_name[(pname, vname)] = sk
    return by_id, by_name


def _resolve_seller_sku(
    *,
    sku_id: str,
    product_name: str,
    sku_name: str,
    by_id: dict[str, str],
    by_name: dict[tuple[str, str], str],
    db_by_id: dict[str, str],
) -> str:
    if sku_id and sku_id in db_by_id:
        return db_by_id[sku_id]
    if sku_id and sku_id in by_id:
        return by_id[sku_id]
    key = (product_name.strip().lower(), sku_name.strip().lower())
    return by_name.get(key, "")


def _uk_sku_to_match_key() -> dict[str, str]:
    init_db()
    conn = connect()
    mp: dict[str, str] = {}
    for r in conn.execute(
        """SELECT sku_id, seller_sku FROM products
           WHERE shop_cipher = ? AND seller_sku != ''""",
        (UK_SHOP_CIPHER,),
    ):
        sid = str(r["sku_id"] or "").strip()
        mk = tk_match_key(str(r["seller_sku"] or ""))
        if sid and mk:
            mp[sid] = mk
    conn.close()
    return mp


def _db_seller_sku_by_id() -> dict[str, str]:
    init_db()
    conn = connect()
    mp = {
        str(r["sku_id"]): str(r["seller_sku"] or "").strip()
        for r in conn.execute(
            "SELECT sku_id, seller_sku FROM products WHERE shop_cipher = ?",
            (UK_SHOP_CIPHER,),
        )
        if str(r["seller_sku"] or "").strip()
    }
    conn.close()
    return mp


def _cost_by_match_key() -> dict[str, float]:
    page = list_products(limit=5000, offset=0)
    return {
        str(it["match_key"]): float(it["cost_cny"])
        for it in (page.get("items") or [])
        if it.get("match_key") and it.get("cost_cny")
    }


def _fee_breakdown_clean(header: list[str], rows: list[list[str]], rate: float, bop) -> list[dict]:
    """Fee composition excluding weight / non-money columns."""
    pay_idx = bop._col_index(header, "Customer payment")
    payment_total = sum(
        bop.parse_number(row[pay_idx]) for row in rows if pay_idx >= 0 and pay_idx < len(row)
    )
    payment_total = round(payment_total, 2)

    def col_numeric(col_idx: int) -> bool:
        blob = (header[col_idx] or "").lower()
        if "weight" in blob or "package" in blob and "fee" not in blob:
            return False
        return bop._column_is_numeric if False else _column_is_money(bop, rows, col_idx)

    items: list[dict] = []
    for i, col in enumerate(header):
        raw = (col or "").strip()
        cn = _chinese_label(raw, bop)
        if _skip_fee_column(raw, cn) or _skip_revenue_column(raw, cn):
            continue
        if "weight" in raw.lower():
            continue
        if not _column_is_money(bop, rows, i):
            continue
        total = round(
            sum(bop.parse_number(row[i]) for row in rows if i < len(row)),
            2,
        )
        if total == 0:
            continue
        items.append(
            {
                "fee_en": raw,
                "fee_cn": cn,
                "amount_gbp": total,
                "amount_cny": round(total * rate, 2),
                "pct_of_customer_payment": round(abs(total) / payment_total * 100, 2)
                if payment_total
                else None,
            }
        )
    items.sort(key=lambda x: abs(x["amount_gbp"]), reverse=True)
    return items


def _column_is_money(bop, rows: list[list[str]], col_idx: int) -> bool:
    for row in rows:
        if col_idx >= len(row):
            continue
        cell = (row[col_idx] or "").strip()
        if not cell:
            continue
        if any(k in cell for k in ("2026", "Order", "GBP")):
            return False
        try:
            float(cell.replace(",", ""))
            return True
        except ValueError:
            return False
    return False


def analyze(path: Path, *, gbp_cny: float, ad_pct: float, out: Path, batch_xlsx: Path | None = None) -> dict:
    bop = _load_bop()
    header, rows, df_raw = _load_xlsx_orders(path)
    order_rows = _build_order_rows(header, rows, ad_pct)

    batch_xlsx = batch_xlsx or Path(
        r"c:\Users\Windows11\Downloads\Tiktoksellercenter_batchedit_20260627_all_information_template.xlsx"
    )
    by_id, by_name = _load_batch_sku_maps(batch_xlsx)
    db_sk = _db_seller_sku_by_id()
    key_cost = _cost_by_match_key()

    enriched = []
    cost_cny_total = 0.0
    matched = 0
    for o in order_rows:
        sid = str(o.get("sku_id") or "")
        seller = _resolve_seller_sku(
            sku_id=sid,
            product_name=str(o.get("product_name") or ""),
            sku_name=str(o.get("sku_name") or ""),
            by_id=by_id,
            by_name=by_name,
            db_by_id=db_sk,
        )
        mk = tk_match_key(seller) if seller else ""
        qty = float(o.get("qty") or 0)
        unit_cny = key_cost.get(mk, 0.0) if mk else 0.0
        line_cost = unit_cny * qty
        if unit_cny:
            matched += 1
        cost_cny_total += line_cost
        stl = float(o.get("settlement") or 0)
        sub = float(o.get("subtotal") or 0)
        ad = float(o.get("ad_cost") or 0)
        profit_gbp = round(stl - ad - (line_cost / gbp_cny if gbp_cny else 0), 2)
        enriched.append(
            {
                **o,
                "seller_sku": seller,
                "match_key": mk,
                "cost_cny": round(line_cost, 2),
                "cost_matched": bool(unit_cny),
                "profit_gbp_est": profit_gbp,
                "margin_pct_est": round(profit_gbp / sub * 100, 1) if sub else None,
            }
        )

    def col_sum(name: str) -> float:
        if name not in df_raw.columns:
            return 0.0
        return float(pd.to_numeric(df_raw[name], errors="coerce").fillna(0).sum())

    gmv_before = col_sum("Subtotal before discounts")
    sub_after = col_sum("Subtotal after seller discounts")
    revenue = col_sum("Total Revenue")
    settlement = col_sum("Total settlement amount")
    total_fees = col_sum("Total Fees")
    customer_pay = col_sum("Customer payment")
    qty = col_sum("Quantity")

    fees = _fee_breakdown_clean(header, rows, gbp_cny, bop)

    profit_cny_catalog = round(
        settlement * gbp_cny - sum(o["ad_cost"] for o in order_rows) * gbp_cny - cost_cny_total,
        2,
    )

    kpi = {
        "period_note": "Reports sheet: 2026/04/01-2026/06/27 UTC+1",
        "currency": "GBP",
        "order_lines": len(order_rows),
        "quantity": int(qty),
        "gbp_cny_rate_used": gbp_cny,
        "ad_rate_pct": ad_pct,
        "subtotal_before_discounts_gbp": round(gmv_before, 2),
        "subtotal_after_seller_discounts_gbp": round(sub_after, 2),
        "total_revenue_gbp": round(revenue, 2),
        "customer_payment_gbp": round(customer_pay, 2),
        "total_settlement_gbp": round(settlement, 2),
        "total_fees_gbp": round(total_fees, 2),
        "seller_discount_rate_pct_gmv": round(-col_sum("Seller discounts") / gmv_before * 100, 2)
        if gmv_before
        else None,
        "settlement_rate_pct_subtotal": round(settlement / sub_after * 100, 2) if sub_after else None,
        "settlement_margin_pct_subtotal": round(settlement / sub_after * 100, 2),
        "fee_rate_pct_customer_payment": round(abs(total_fees) / customer_pay * 100, 2)
        if customer_pay
        else None,
        "catalog_cost_cny": round(cost_cny_total, 2),
        "catalog_cost_gbp_equiv": round(cost_cny_total / gbp_cny, 2),
        "cost_match_lines": matched,
        "est_profit_gbp_after_cost_ad": round(
            settlement
            - sum(o["ad_cost"] for o in order_rows)
            - cost_cny_total / gbp_cny,
            2,
        ),
        "est_profit_cny_after_cost_ad": profit_cny_catalog,
        "est_margin_pct_on_subtotal": round(
            (
                settlement
                - sum(o["ad_cost"] for o in order_rows)
                - cost_cny_total / gbp_cny
            )
            / sub_after
            * 100,
            2,
        )
        if sub_after
        else None,
        "platform_settlement_only_margin": round(
            (settlement - cost_cny_total / gbp_cny) / settlement * 100, 2
        )
        if settlement
        else None,
    }

    # bucket summary for user
    buckets = [
        ("折扣前小计", gmv_before, "gmv"),
        ("卖家折后小计", sub_after, "revenue_base"),
        ("Total Revenue", revenue, "revenue"),
        ("客户实付 Customer payment", customer_pay, "payment"),
        ("总结算 Total settlement", settlement, "settlement"),
        ("— 费用 Total Fees", total_fees, "fees"),
        ("商家折扣 Seller discounts", col_sum("Seller discounts"), "promo"),
        ("平台折扣 Platform discounts", col_sum("Platform discounts"), "promo"),
        ("TikTok佣金", col_sum("TikTok Shop commission fee"), "platform"),
        ("Smart Promotion fee", col_sum("Smart Promotion fee"), "platform"),
        ("实际运费 Actual shipping", col_sum("Actual shipping fee"), "logistics"),
        ("卖家运费 Seller shipping", col_sum("Seller shipping fee"), "logistics"),
        ("卖家运费折扣", col_sum("Seller shipping fee discount"), "logistics"),
        ("客户运费 Customer shipping", col_sum("Customer shipping fee"), "logistics"),
        ("VAT", col_sum("VAT"), "tax"),
        ("Tax and duty", col_sum("Tax and duty"), "tax"),
        ("退款折后小计", col_sum("Refund subtotal after seller discounts"), "refund"),
        ("联盟佣金", col_sum("Affiliate Commission"), "affiliate"),
        ("目录商品成本(折合GBP)", -cost_cny_total / gbp_cny, "cogs"),
        ("估算广告费(折后小计×%)", -sum(o["ad_cost"] for o in order_rows), "ads"),
    ]
    bucket_df = pd.DataFrame(
        [
            {
                "项目": name,
                "金额GBP": round(val, 2),
                "占客户实付%": round(abs(val) / customer_pay * 100, 2)
                if customer_pay and key not in ("gmv", "settlement")
                else "",
                "占折后小计%": round(abs(val) / sub_after * 100, 2)
                if sub_after and key not in ("gmv",)
                else "",
                "分类": key,
            }
            for name, val, key in buckets
            if abs(val) >= 0.005 or key in ("settlement", "revenue_base")
        ]
    )

    # statements sheet
    stmt = pd.read_excel(path, sheet_name="Statements", header=0)

    order_export = pd.DataFrame(enriched)[
        [
            "date",
            "order_id",
            "sku_id",
            "seller_sku",
            "match_key",
            "product_name",
            "sku_name",
            "qty",
            "subtotal",
            "settlement",
            "ad_cost",
            "cost_cny",
            "cost_matched",
            "profit_gbp_est",
            "margin_pct_est",
        ]
    ]

    with pd.ExcelWriter(out, engine="openpyxl") as w:
        pd.DataFrame([kpi]).T.rename(columns={0: "值"}).reset_index().rename(
            columns={"index": "指标"}
        ).to_excel(w, sheet_name="核心比例", index=False)
        bucket_df.to_excel(w, sheet_name="分类汇总", index=False)
        pd.DataFrame(fees).to_excel(w, sheet_name="费用明细_同东南亚", index=False)
        order_export.to_excel(w, sheet_name="订单利润_含目录成本", index=False)
        stmt.to_excel(w, sheet_name="Statements", index=False)

    return {"kpi": kpi, "fees": fees[:12], "out": str(out)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--xlsx",
        type=Path,
        default=Path(r"c:\Users\Windows11\Downloads\income_20260627083452(UTC+1).xlsx"),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(r"c:\Users\Windows11\Desktop\uk_income_settlement_analysis.xlsx"),
    )
    ap.add_argument("--gbp-cny", type=float, default=DEFAULT_GBP_CNY)
    ap.add_argument("--ad-pct", type=float, default=DEFAULT_AD_PCT)
    ap.add_argument("--batch-xlsx", type=Path, default=None)
    args = ap.parse_args()
    if not args.xlsx.is_file():
        print(f"Missing: {args.xlsx}")
        return 1
    res = analyze(
        args.xlsx,
        gbp_cny=args.gbp_cny,
        ad_pct=args.ad_pct,
        out=args.out,
        batch_xlsx=args.batch_xlsx,
    )
    k = res["kpi"]
    print("=== UK Income Settlement (SEA format) ===")
    for key in [
        "subtotal_before_discounts_gbp",
        "subtotal_after_seller_discounts_gbp",
        "customer_payment_gbp",
        "total_settlement_gbp",
        "total_fees_gbp",
        "seller_discount_rate_pct_gmv",
        "settlement_rate_pct_subtotal",
        "fee_rate_pct_customer_payment",
        "catalog_cost_gbp_equiv",
        "est_profit_gbp_after_cost_ad",
        "est_margin_pct_on_subtotal",
        "cost_match_lines",
        "order_lines",
    ]:
        print(f"  {key}: {k.get(key)}")
    print("\nTop fees (% of customer payment):")
    for f in res["fees"]:
        print(f"  {f['fee_cn']}: {f['amount_gbp']} GBP ({f['pct_of_customer_payment']}%)")
    print(f"\nWrote {res['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
