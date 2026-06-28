"""Analyze UK income for free-shipping-over-GBP10 promotion pattern."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PATH = Path(r"c:\Users\Windows11\Downloads\income_20260627083452(UTC+1).xlsx")


def main() -> int:
    df = pd.read_excel(PATH, sheet_name="Order details", header=0)
    df = df[df["Type"].astype(str).eq("Order")].copy()
    nums = [
        "Quantity",
        "Customer shipping fee",
        "Actual shipping fee",
        "Seller shipping fee",
        "Platform shipping fee discount",
        "Seller shipping fee discount",
        "Subtotal after seller discounts",
        "Subtotal before discounts",
        "Customer payment",
        "Shipping subsidy",
        "Fee for FBT free shipping",
    ]
    for c in nums:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    g = df.groupby("Order/adjustment ID").agg(
        subtotal=("Subtotal after seller discounts", "sum"),
        subtotal_before=("Subtotal before discounts", "sum"),
        customer_ship=("Customer shipping fee", "max"),
        customer_payment=("Customer payment", "max"),
        actual_ship=("Actual shipping fee", "sum"),
        seller_ship=("Seller shipping fee", "sum"),
        platform_ship_disc=("Platform shipping fee discount", "sum"),
        seller_ship_disc=("Seller shipping fee discount", "sum"),
        ship_subsidy=("Shipping subsidy", "sum") if "Shipping subsidy" in df.columns else ("Subtotal after seller discounts", "count"),
    ).reset_index()
    g["actual_ship_abs"] = -g["actual_ship"]
    g["free_buyer"] = g["customer_ship"] == 0
    g["paid_buyer"] = g["customer_ship"] > 0

    print("=== UK settlement free-ship analysis ===")
    print(f"Orders: {len(g)}")
    print(f"Buyer pays 0 shipping: {g['free_buyer'].sum()}")
    print(f"Buyer pays shipping:   {g['paid_buyer'].sum()}")
    if g["paid_buyer"].any():
        print("Paid amounts:", sorted(g.loc[g["paid_buyer"], "customer_ship"].unique()))

    print("\n--- Free-ship orders by subtotal band ---")
    free = g[g["free_buyer"]]
    for th in (5, 8, 10, 12, 15, 20):
        ge = (free["subtotal"] >= th).sum()
        print(f"  subtotal>={th:2} GBP: {ge}/{len(free)} ({100*ge/len(free):.0f}%)")

    print("\n--- Paid-ship orders by subtotal band ---")
    paid = g[g["paid_buyer"]]
    for th in (5, 8, 10, 12, 15, 20):
        ge = (paid["subtotal"] >= th).sum()
        lt = (paid["subtotal"] < th).sum()
        print(f"  subtotal>={th:2} GBP: {ge}/{len(paid)}  subtotal<{th}: {lt}")

    g["expect_free_ge10"] = g["subtotal"] >= 10
    g["match_ge10"] = g["expect_free_ge10"] == g["free_buyer"]
    print(f"\nRule test: free shipping iff subtotal>=10 GBP")
    print(f"  Match: {g['match_ge10'].sum()}/{len(g)} ({100*g['match_ge10'].mean():.1f}%)")

    mm = g[~g["match_ge10"]].sort_values("subtotal")
    print("\nMismatches (order, subtotal, customer_ship, actual_ship):")
    for _, r in mm.iterrows():
        print(
            f"  {int(r['Order/adjustment ID'])}  sub={r['subtotal']:.2f}  "
            f"cust_ship={r['customer_ship']:.2f}  actual={r['actual_ship_abs']:.2f}  "
            f"plat_disc={r['platform_ship_disc']:.2f}"
        )

    print("\n--- Platform shipping fee discount ---")
    pdisc = g[g["platform_ship_disc"] != 0]
    print(f"Orders with platform ship discount: {len(pdisc)}")
    for _, r in pdisc.iterrows():
        print(
            f"  sub={r['subtotal']:.2f} cust_ship={r['customer_ship']:.2f} "
            f"plat_disc={r['platform_ship_disc']:.2f}"
        )

    if "Shipping subsidy" in df.columns:
        ss = df[df["Shipping subsidy"] != 0]
        print(f"\nShipping subsidy nonzero lines: {len(ss)} total={df['Shipping subsidy'].sum():.2f}")

    print("\n--- Interpretation ---")
    free_ge10 = ((g["free_buyer"]) & (g["subtotal"] >= 10)).sum()
    free_lt10 = ((g["free_buyer"]) & (g["subtotal"] < 10)).sum()
    paid_lt10 = ((g["paid_buyer"]) & (g["subtotal"] < 10)).sum()
    paid_ge10 = ((g["paid_buyer"]) & (g["subtotal"] >= 10)).sum()
    print(f"  Free ship & subtotal>=10: {free_ge10}")
    print(f"  Free ship & subtotal<10:  {free_lt10}  (seller/platform bears ship, not buyer)")
    print(f"  Paid ship & subtotal<10:   {paid_lt10}  (consistent with under threshold)")
    print(f"  Paid ship & subtotal>=10:  {paid_ge10}  (would contradict simple 10 GBP promo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
