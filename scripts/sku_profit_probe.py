# -*- coding: utf-8 -*-
"""CLI: python scripts/sku_profit_probe.py --sku 990001"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="SKU 利润探针（TH TK/Shopee）")
    ap.add_argument("--sku", default="", help="单个 SKU；批量用 --skus")
    ap.add_argument("--skus", default="", help="逗号/空格分隔多个 SKU")
    ap.add_argument("--platform", default="both", choices=["both", "tiktok", "shopee"])
    ap.add_argument("--ad-rate", type=float, default=None, help="默认 0.22；可传 22 或 0.22")
    ap.add_argument("--lookback-days", type=int, default=None)
    ap.add_argument("--sale", type=float, default=None, help="手动覆盖售价")
    ap.add_argument("--cost", type=float, default=None, help="手动覆盖货本 CNY")
    ap.add_argument("--force-fx", action="store_true")
    ap.add_argument("--hot", action="store_true", help="列出热销 SKU")
    args = ap.parse_args()

    from modules.finance import sku_profit_service as svc

    if args.hot:
        print(json.dumps(svc.list_hot_skus(platform=args.platform), ensure_ascii=False, indent=2))
        return 0

    skus = []
    if args.skus:
        skus = [x.strip() for x in args.skus.replace(",", " ").split() if x.strip()]
    elif args.sku:
        skus = [args.sku.strip()]
    if not skus:
        ap.error("需要 --sku 或 --skus 或 --hot")

    if len(skus) > 1:
        data = svc.estimate_batch(
            skus,
            platform=args.platform,
            ad_rate=args.ad_rate,
            lookback_days=args.lookback_days,
            cost_override=args.cost,
        )
    else:
        data = svc.estimate(
            skus[0],
            platform=args.platform,
            ad_rate=args.ad_rate,
            lookback_days=args.lookback_days,
            sale_override=args.sale,
            cost_override=args.cost,
            force_fx_refresh=args.force_fx,
        )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
