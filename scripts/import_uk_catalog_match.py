"""Import UK TikTok batch export and match to catalog match_key / costs."""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.db import connect, init_db
from modules.catalog.listings import list_products
from modules.catalog.logistics_weights import REGION_SKU_PREFIX, regional_seller_sku
from modules.catalog.sku_key import parse_search_key, tk_match_key

UK_SHOP_CIPHER = "UK_IMPORT_GB"
DEFAULT_XLSX = Path(
    r"c:\Users\Windows11\Downloads\Tiktoksellercenter_batchedit_20260627_all_information_template.xlsx"
)
DEFAULT_REPORT = Path(r"c:\Users\Windows11\Desktop\uk_catalog_match_report.xlsx")


def load_uk_export(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Template", header=0)
    df = df[df["product_id"].astype(str).str.match(r"^\d+$", na=False)].copy()
    qty_col = next((c for c in df.columns if str(c).startswith("warehouse_quantity")), None)
    df["stock"] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0).astype(int) if qty_col else 0
    df["price_gbp"] = pd.to_numeric(df["price"], errors="coerce")
    df["match_key"] = df["seller_sku"].astype(str).map(parse_search_key)
    df = df[df["match_key"] != ""].copy()
    return df


def ensure_uk_shop(conn: sqlite3.Connection) -> None:
    now = int(time.time())
    conn.execute(
        """INSERT INTO shops (cipher, shop_id, name, region, seller_type, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(cipher) DO UPDATE SET
             name=excluded.name, region=excluded.region, updated_at=excluded.updated_at""",
        (UK_SHOP_CIPHER, "uk_batch_import", "TikTok UK (import)", "GB", "CROSS_BORDER", now),
    )


def upsert_uk_products(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    now = int(time.time())
    rows = []
    for _, r in df.iterrows():
        sku_id = str(r["sku_id"]).strip()
        if not sku_id or sku_id == "nan":
            continue
        img = str(r.get("main_image") or "").strip()
        if img in ("nan", ""):
            img = ""
        rows.append(
            {
                "sku_id": sku_id,
                "shop_cipher": UK_SHOP_CIPHER,
                "product_id": str(r["product_id"]).strip(),
                "global_product_id": "",
                "global_sku_id": "",
                "seller_sku": str(r["seller_sku"]).strip(),
                "product_name": str(r.get("product_name") or "")[:500],
                "sku_name": str(r.get("variation_value") or r.get("seller_sku") or "")[:200],
                "image_url": img,
                "price": float(r["price_gbp"]) if pd.notna(r["price_gbp"]) else 0.0,
                "currency": "GBP",
                "stock": int(r.get("stock") or 0),
                "status": "ACTIVATE",
                "updated_at": now,
            }
        )
    conn.executemany(
        """INSERT INTO products (
            sku_id, shop_cipher, product_id, global_product_id, global_sku_id,
            seller_sku, product_name, sku_name, image_url, price, currency, stock, status, updated_at
        ) VALUES (
            :sku_id, :shop_cipher, :product_id, :global_product_id, :global_sku_id,
            :seller_sku, :product_name, :sku_name, :image_url, :price, :currency, :stock, :status, :updated_at
        )
        ON CONFLICT(sku_id, shop_cipher) DO UPDATE SET
            product_id=excluded.product_id,
            seller_sku=excluded.seller_sku,
            product_name=excluded.product_name,
            sku_name=excluded.sku_name,
            image_url=CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE products.image_url END,
            price=excluded.price,
            currency=excluded.currency,
            stock=excluded.stock,
            status=excluded.status,
            updated_at=excluded.updated_at""",
        rows,
    )
    return len(rows)


def build_catalog_index() -> dict[str, dict]:
    page = list_products(limit=5000, offset=0)
    by_key: dict[str, dict] = {}
    for item in page.get("items") or []:
        mk = item.get("match_key") or ""
        if mk:
            by_key[mk] = item
    return by_key


def sea_tk_regions(catalog_item: dict | None) -> list[str]:
    if not catalog_item:
        return []
    tk = catalog_item.get("tiktok") or {}
    return sorted(
        {
            str(r.get("region") or "").upper()
            for r in (tk.get("regions") or [])
            if str(r.get("region") or "").upper() in REGION_SKU_PREFIX
        }
    )


def match_row(r: pd.Series, catalog: dict[str, dict]) -> dict:
    mk = str(r["match_key"])
    cat = catalog.get(mk)
    regions = sea_tk_regions(cat)
    cost = cat.get("cost_cny") if cat else None
    tk = (cat or {}).get("tiktok") or {}
    ph_row = next((x for x in (tk.get("regions") or []) if x.get("region") == "PH"), None)
    if not regions:
        status = "uk_only"
    elif cost:
        status = "matched_with_cost"
    else:
        status = "matched_tk_no_cost"
    return {
        "match_key": mk,
        "match_status": status,
        "uk_seller_sku": r["seller_sku"],
        "uk_sku_id": str(r["sku_id"]),
        "uk_product_id": str(r["product_id"]),
        "uk_product_name": str(r.get("product_name") or "")[:80],
        "uk_variation": str(r.get("variation_value") or ""),
        "uk_price_gbp": r["price_gbp"],
        "uk_stock": int(r.get("stock") or 0),
        "cost_cny": cost,
        "sea_tk_regions": ",".join(regions) if regions else "",
        "ph_seller_sku": regional_seller_sku(mk, "PH") if mk else "",
        "ph_listed": "PH" in regions,
        "catalog_tiktok": bool(cat and cat.get("tiktok")),
        "catalog_shopee": bool(cat and cat.get("shopee")),
        "catalog_ozon": bool(cat and cat.get("ozon")),
        "ph_product_name": (ph_row or {}).get("product_name", "")[:80] if ph_row else "",
        "logistics_weight_g": (cat or {}).get("logistics_weight_g"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Import UK export and match catalog")
    ap.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--no-import", action="store_true", help="Match only, do not write shop.db")
    args = ap.parse_args()

    if not args.xlsx.is_file():
        print(f"Missing export: {args.xlsx}")
        return 1

    df = load_uk_export(args.xlsx)
    print(f"UK export: {len(df)} SKU rows, {df['match_key'].nunique()} match keys")

    if not args.no_import:
        init_db()
        conn = connect()
        ensure_uk_shop(conn)
        n = upsert_uk_products(conn, df)
        conn.commit()
        conn.close()
        print(f"Imported {n} UK SKUs → shop.db ({UK_SHOP_CIPHER})")

    catalog = build_catalog_index()
    rows = [match_row(r, catalog) for _, r in df.iterrows()]
    rep = pd.DataFrame(rows).sort_values(["match_status", "match_key"])

    summary = {
        "total_uk_skus": len(rep),
        "matched_with_cost": int((rep["match_status"] == "matched_with_cost").sum()),
        "matched_tk_no_cost": int((rep["match_status"] == "matched_tk_no_cost").sum()),
        "uk_only": int((rep["match_status"] == "uk_only").sum()),
    }
    uk_keys = set(rep["match_key"])
    in_catalog_not_uk = sorted(set(catalog) - uk_keys)

    with pd.ExcelWriter(args.report, engine="openpyxl") as w:
        pd.DataFrame([summary]).to_excel(w, sheet_name="汇总", index=False)
        rep.to_excel(w, sheet_name="UK匹配明细", index=False)
        if in_catalog_not_uk:
            pd.DataFrame({"match_key": in_catalog_not_uk[:500]}).to_excel(
                w, sheet_name="目录有UK未导入", index=False
            )

    print("Match summary:", summary)
    print(f"Report: {args.report}")
    print("\nSample matched:")
    print(
        rep[rep["match_status"] == "matched_with_cost"][
            ["match_key", "uk_price_gbp", "cost_cny", "sea_tk_regions"]
        ]
        .head(8)
        .to_string(index=False)
    )
    if summary["uk_only"]:
        print("\nUK-only (no SEA catalog):")
        print(rep[rep["match_status"] == "uk_only"][["match_key", "uk_seller_sku", "uk_product_name"]].head(8).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
