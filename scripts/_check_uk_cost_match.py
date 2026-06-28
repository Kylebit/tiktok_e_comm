"""One-off: match UK bill SKU IDs to shop.db costs."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BILL = Path(r"c:\Users\Windows11\Desktop\merchant_statement_profit_loss_7654907999706547990.xlsx")
DB = ROOT / "data" / "shop.db"


def main() -> int:
    bill = pd.read_excel(BILL, sheet_name=0, header=5)
    bill = bill.rename(
        columns={
            bill.columns[1]: "sku_id",
            bill.columns[2]: "sku_name",
            bill.columns[3]: "product_name",
            bill.columns[5]: "bill_cost",
            bill.columns[7]: "qty",
        }
    )
    bill["sku_id"] = bill["sku_id"].astype(str)
    bill["bill_cost"] = pd.to_numeric(bill["bill_cost"], errors="coerce").fillna(0)
    by_sku = (
        bill.groupby("sku_id", as_index=False)
        .agg(
            sku_name=("sku_name", "first"),
            bill_cost_gbp=("bill_cost", "sum"),
            qty=("qty", "sum"),
        )
        .sort_values("bill_cost_gbp")
    )

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    print("=== shops by region ===")
    for r in conn.execute(
        "SELECT UPPER(region) reg, COUNT(*) c FROM shops GROUP BY reg ORDER BY c DESC"
    ):
        print(f"  {r['reg']}: {r['c']} shops")

    print("\n=== UK/GB shops ===")
    uk = conn.execute(
        """
        SELECT cipher, region, name FROM shops
        WHERE UPPER(region) IN ('GB','UK','GBR')
           OR name LIKE '%UK%' OR name LIKE '%英国%'
        """
    ).fetchall()
    for r in uk:
        print(f"  {dict(r)}")

    print("\n=== products with cost by region ===")
    for r in conn.execute(
        """
        SELECT UPPER(s.region) reg,
               COUNT(DISTINCT p.sku_id) products,
               SUM(CASE WHEN sc.cost_cny IS NOT NULL AND sc.cost_cny > 0 THEN 1 ELSE 0 END) with_cost
        FROM products p
        JOIN shops s ON p.shop_cipher = s.cipher
        LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
        GROUP BY reg ORDER BY products DESC
        """
    ):
        print(f"  {r['reg']}: {r['products']} SKUs, {r['with_cost']} with cost_cny")

    matched_id = 0
    matched_sku = 0
    has_db_cost = 0
    rows = []

    for _, r in by_sku.iterrows():
        sid = str(r["sku_id"])
        hit = conn.execute(
            """
            SELECT p.sku_id, p.seller_sku, p.product_id, UPPER(s.region) region,
                   sc.cost_cny, sc.note
            FROM products p
            JOIN shops s ON p.shop_cipher = s.cipher
            LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
            WHERE CAST(p.sku_id AS TEXT) = ?
            LIMIT 5
            """,
            (sid,),
        ).fetchall()

        db_cost = None
        seller = None
        region = None
        if hit:
            matched_id += 1
            best = hit[0]
            seller = best["seller_sku"]
            region = best["region"]
            if best["cost_cny"]:
                db_cost = float(best["cost_cny"])
                has_db_cost += 1
        else:
            # try seller_sku from bill sku_name / product cross
            pass

        rows.append(
            {
                "sku_id": sid,
                "sku_name": str(r["sku_name"])[:40],
                "bill_cost_gbp": round(-float(r["bill_cost_gbp"]), 2),
                "qty": int(r["qty"]),
                "in_products": bool(hit),
                "seller_sku": seller,
                "region": region,
                "cost_cny": db_cost,
            }
        )

    # cross-region: match UK bill items to PH/MY master by seller_sku suffix
    print(f"\n=== bill SKU ID match in shop.db ===")
    print(f"  unique bill SKUs: {len(by_sku)}")
    print(f"  matched by sku_id: {matched_id}")
    print(f"  with cost_cny in db: {has_db_cost}")

    # sample bill costs vs catalog - search seller_sku patterns from UK listing
    print("\n=== try match via UK products table (any region same product_id) ===")
    # Get product_ids from UK if any
    uk_pids = conn.execute(
        """
        SELECT COUNT(*) FROM products p
        JOIN shops s ON p.shop_cipher = s.cipher
        WHERE UPPER(s.region) IN ('GB','UK')
        """
    ).fetchone()[0]
    print(f"  UK region products in db: {uk_pids}")

    # Check config exchange GBP
    try:
        from core.config import get

        rates = get("exchange_rates") or {}
        gbp = rates.get("GBP")
        print(f"\n  config GBP rate (CNY per GBP): {gbp}")
    except Exception as exc:
        gbp = None
        print(f"\n  config GBP rate: unavailable ({exc})")

    print("\n=== bill cost samples (GBP from TK export) ===")
    for row in rows[:10]:
        cny_est = (
            round(row["bill_cost_gbp"] * float(gbp), 2)
            if gbp and row["bill_cost_gbp"]
            else None
        )
        print(
            f"  {row['sku_name'][:35]:35} bill_GBP={row['bill_cost_gbp']:6.2f} "
            f"db_cny={row['cost_cny']} est_cny={cny_est} region={row['region']}"
        )

    print(f"\n=== global_sku_id cross-region match ===")
    g_match = 0
    g_filled = conn.execute(
        "SELECT COUNT(*) FROM products WHERE global_sku_id IS NOT NULL AND TRIM(global_sku_id) != ''"
    ).fetchone()[0]
    for sid in by_sku["sku_id"]:
        hit = conn.execute(
            """
            SELECT p.seller_sku, UPPER(s.region) region, sc.cost_cny
            FROM products p
            JOIN shops s ON p.shop_cipher = s.cipher
            LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
            WHERE p.global_sku_id = ?
            LIMIT 3
            """,
            (sid,),
        ).fetchall()
        if hit:
            g_match += 1
    print(f"  products with global_sku_id in db: {g_filled}")
    print(f"  bill SKUs matched via global_sku_id: {g_match}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
