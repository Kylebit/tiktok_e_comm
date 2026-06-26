"""Publish PH seller_sku 770003 (suffix 0003) to TikTok MX."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.mx_publish import publish_mx_listing

if __name__ == "__main__":
    raise SystemExit(
        publish_mx_listing(
            collect_box_detail_id=2059296237,
            seller_sku="770003",
            ph_product_id="1732379849767749563",
            publish=True,
        )
    )
