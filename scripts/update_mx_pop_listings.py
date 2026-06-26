"""按 POP 测算更新 MX 店售价与库存（仅 save 草稿，不触发发布）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.mx_publish import publish_mx_listing
from scripts.mx_pop_pricing import fetch_cny_mxn, quote_sku

MX_LISTINGS = [
    {
        "collect_box_detail_id": 1742250495,
        "seller_sku": "770002",
        "ph_product_id": "1731673032762296251",
    },
    {
        "collect_box_detail_id": 2059296237,
        "seller_sku": "770003",
        "ph_product_id": "1732379849767749563",
    },
]
STOCK = 200


def main() -> int:
    rate = fetch_cny_mxn()
    rc = 0
    for row in MX_LISTINGS:
        q = quote_sku(row["seller_sku"], cny_mxn=rate)
        print(
            f"\n>>> {row['seller_sku']}: POP {q.pop_sale_mxn:.2f} -> "
            f"sale {q.sale_price_mxn:.2f} MXN | list ceil {q.list_price_ceil_mxn} | stock {STOCK}"
        )
        if q.sfp_adjustment:
            print(f"    {q.sfp_adjustment}")
        code = publish_mx_listing(
            collect_box_detail_id=row["collect_box_detail_id"],
            seller_sku=row["seller_sku"],
            ph_product_id=row["ph_product_id"],
            publish=False,
            mxn_sale=q.sale_price_mxn,
            mxn_list=q.list_price_ceil_mxn,
            stock=STOCK,
        )
        if code:
            rc = code
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
