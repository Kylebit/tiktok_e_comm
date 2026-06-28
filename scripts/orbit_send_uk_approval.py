"""Orbit Hive：UK 审批卡入 Web 收件箱。"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.catalog.tk_sku_groups import collapse_match_keys_to_units, expand_match_keys
from modules.miaoshou.migrate_dispatch import queue_uk_unit
from modules.miaoshou.mx_collect_match import find_common_id as find_collect_common_id
from modules.miaoshou.mx_migrate import collect_master_images_and_product
from modules.miaoshou.uk_confirm import create_confirm_card
from modules.miaoshou.uk_web_approval import uk_approval_url
from scripts.uk_pop_pricing import fetch_cny_gbp, quote_match_key

DEFAULT_TASK = "TASK-UK-DISPATCH"


def catalog_row(mk: str) -> dict | None:
    conn = sqlite3.connect(ROOT / "data" / "shop.db")
    conn.row_factory = sqlite3.Row
    for reg in ("PH", "MY", "TH", "VN"):
        row = conn.execute(
            """
            SELECT p.seller_sku, p.product_id, s.region
            FROM products p JOIN shops s ON p.shop_cipher = s.cipher
            LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
            WHERE p.seller_sku LIKE ? AND UPPER(s.region) = ?
              AND sc.cost_cny IS NOT NULL AND sc.cost_cny > 0
            LIMIT 1
            """,
            (f"%{mk}", reg),
        ).fetchone()
        if row:
            return dict(row)
    return None


def find_common_id(product_id: str, *, mk: str = "", seller_sku: str = "") -> int | None:
    return find_collect_common_id(mk=mk, seller_sku=seller_sku, product_id=str(product_id))


def build_card_for_mk(mk: str, *, rate: float):
    row = catalog_row(mk)
    if not row:
        raise RuntimeError(f"{mk} 无目录/成本")
    q = quote_match_key(mk, cny_gbp=rate)
    urls, product = collect_master_images_and_product(str(row["product_id"]), region=row["region"] or "PH")
    common_id = find_common_id(str(row["product_id"]), mk=mk, seller_sku=str(row["seller_sku"]))
    card = create_confirm_card(
        pop_quote=q,
        collect_box_detail_id=0,
        seller_sku=row["seller_sku"],
        master_product_id=str(row["product_id"]),
        master_region=row["region"] or "PH",
        product_name=str(product.get("title") or row["seller_sku"]),
        main_image_url=urls[0] if urls else "",
    )
    return card, common_id


def main() -> int:
    ap = argparse.ArgumentParser(description="UK 审批卡入 Web 收件箱")
    ap.add_argument("keys", nargs="+", help="对齐码，如 0003 0169")
    args = ap.parse_args()

    rate = fetch_cny_gbp()
    units = collapse_match_keys_to_units(expand_match_keys(args.keys))

    for unit in units:
        row = queue_uk_unit(unit, rate=rate, build_single=build_card_for_mk)
        print(f"  web queued {row['kind']} {row['mk']} → {row['web_url']}")
    print(f">>> Web 审批: {uk_approval_url()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
