"""Fix MX listing: TikTok images, PHP+20%→MXN, itemNum 后四位, 采集箱/母版包裹尺寸。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.mx_publish import publish_mx_listing


def main() -> int:
    parser = argparse.ArgumentParser(description="Fix and publish TikTok MX listing via Miaoshou")
    parser.add_argument("--tk-id", type=int, required=True, help="collectBoxDetailId")
    parser.add_argument("--seller-sku", required=True, help="PH seller_sku e.g. 770002")
    parser.add_argument("--product-id", required=True, help="PH TikTok product_id")
    parser.add_argument("--save-only", action="store_true", help="只保存不发布")
    args = parser.parse_args()
    return publish_mx_listing(
        collect_box_detail_id=args.tk_id,
        seller_sku=args.seller_sku,
        ph_product_id=args.product_id,
        publish=not args.save_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
