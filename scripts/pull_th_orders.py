# -*- coding: utf-8 -*-
"""CLI: 拉取 TH TikTok/Shopee 最新结算订单。

  python scripts/pull_th_orders.py
  python scripts/pull_th_orders.py --days 7 --platform both
  python scripts/pull_th_orders.py --platform shopee --region TH
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="TH TikTok/Shopee 订单结算 API 拉取")
    ap.add_argument("--platform", default="both", choices=["both", "tiktok", "shopee"])
    ap.add_argument("--region", default="TH", help="逗号分隔，默认 TH")
    ap.add_argument("--days", type=int, default=14, help="回看天数（含今天）")
    ap.add_argument("--start", default="", help="YYYY-MM-DD")
    ap.add_argument("--end", default="", help="YYYY-MM-DD")
    ap.add_argument("--async", dest="async_mode", action="store_true", help="后台线程（打印 status）")
    args = ap.parse_args()

    from modules.finance import th_orders_pull as pull

    regions = [x.strip().upper() for x in args.region.split(",") if x.strip()]
    end = date.fromisoformat(args.end) if args.end else datetime.now(timezone.utc).date()
    start = date.fromisoformat(args.start) if args.start else (end - timedelta(days=max(args.days, 1) - 1))

    plats = ["tiktok", "shopee"] if args.platform == "both" else [args.platform]

    if args.async_mode:
        ok, msg = pull.start_pull(
            platforms=plats, regions=regions, start=start, end=end, lookback_days=args.days
        )
        print(msg)
        if not ok:
            return 1
        import time

        while True:
            st = pull.pull_status()
            print(f"[{st.get('percent')}%] {st.get('message') or st.get('error') or ''}")
            if not st.get("running"):
                print(json.dumps(st.get("result") or {"error": st.get("error")}, ensure_ascii=False, indent=2))
                return 0 if not st.get("error") else 1
            time.sleep(2)

    result = pull.run_pull(platforms=plats, regions=regions, start=start, end=end, lookback_days=args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
