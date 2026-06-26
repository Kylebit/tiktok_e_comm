"""Sync TikTok logistics weights (365d median, MY+PH+TH+VN merged) into catalog."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.catalog.logistics_weights import sync_catalog_match_keys, sync_logistics_weights, sync_match_key_weights
from modules.ozon.logistics_weight import SCAN_DAYS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="同步 TikTok 物流实测重量：四国各店查对应货号，对齐码取中位数"
    )
    parser.add_argument("--sku", action="append", dest="skus", help="seller_sku 或对齐码，可重复")
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="同步商品目录内全部 SKU（默认 --sku 未指定时启用）",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="全量扫描四国已完成包裹（慢，适合首次/缓存过期）",
    )
    parser.add_argument("--days", type=int, default=SCAN_DAYS)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="忽略单 SKU 缓存，强制重扫包裹",
    )
    args = parser.parse_args(argv)

    if args.full:
        result = sync_logistics_weights(
            on_progress=lambda m: print(m, flush=True),
            force_refresh=True,
            days=args.days,
        )
    elif args.skus:
        result = sync_match_key_weights(
            args.skus,
            on_progress=lambda m: print(m, flush=True),
            force_refresh=args.refresh,
        )
    else:
        result = sync_catalog_match_keys(
            on_progress=lambda m: print(m, flush=True),
            force_refresh=args.refresh,
        )
    print("\nResult:", result, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
