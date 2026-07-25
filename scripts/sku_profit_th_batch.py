# -*- coding: utf-8 -*-
"""全量估算近 N 天有单的 TH SKU（末四位对齐）。

  python scripts/sku_profit_th_batch.py --days 90
  python scripts/sku_profit_th_batch.py --days 90 --list-only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="TH 近N天有单 SKU 全量利润估算")
    ap.add_argument("--days", type=int, default=90, help="有单回看天数")
    ap.add_argument("--estimate-lookback", type=int, default=None, help="估算用近单窗口，默认同 --days")
    ap.add_argument("--platform", default="both", choices=["both", "tiktok", "shopee"])
    ap.add_argument("--list-only", action="store_true", help="只列出有单 SKU，不估算")
    args = ap.parse_args()

    from modules.finance import sku_profit_catalog as cat

    if args.list_only:
        data = cat.collect_ordered_skus(lookback_days=args.days)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    data = cat.estimate_ordered_skus(
        lookback_days=args.days,
        platform=args.platform,
        estimate_lookback_days=args.estimate_lookback,
    )
    print(
        json.dumps(
            {
                "ok": data.get("ok"),
                "catalog": data.get("catalog"),
                "json": data.get("json"),
                "csv": data.get("csv"),
                "sample": (data.get("rows") or [])[:5],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    ok_n = sum(1 for r in data.get("rows") or [] if r.get("ok"))
    print(f"\n完成：{ok_n}/{len(data.get('rows') or [])} 有至少一侧成功 → {data.get('csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
