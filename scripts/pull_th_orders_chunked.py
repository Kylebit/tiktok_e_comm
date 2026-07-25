# -*- coding: utf-8 -*-
"""按月分段拉取 TH 近 N 天订单（避免一次 90 天卡住）。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _chunks(end: date, days: int, chunk: int) -> list[tuple[date, date]]:
    start = end - timedelta(days=max(days, 1) - 1)
    out = []
    cur = start
    while cur <= end:
        piece_end = min(cur + timedelta(days=chunk - 1), end)
        out.append((cur, piece_end))
        cur = piece_end + timedelta(days=1)
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--chunk", type=int, default=30)
    ap.add_argument("--platform", default="both", choices=["both", "tiktok", "shopee"])
    ap.add_argument("--region", default="TH")
    args = ap.parse_args()

    from modules.finance import th_orders_pull as pull

    end = datetime.now(timezone.utc).date()
    plats = ["tiktok", "shopee"] if args.platform == "both" else [args.platform]
    regions = [r.strip().upper() for r in args.region.split(",") if r.strip()]
    results = []
    for i, (s, e) in enumerate(_chunks(end, args.days, args.chunk), start=1):
        print(f"\n=== chunk {i}: {s} ~ {e} · {plats} ===", flush=True)
        r = pull.run_pull(platforms=plats, regions=regions, start=s, end=e, lookback_days=(e - s).days + 1)
        print(json.dumps(r, ensure_ascii=False, indent=2), flush=True)
        results.append(r)
    ok = all(x.get("ok") for x in results) if results else False
    print(json.dumps({"ok": ok, "chunks": len(results)}, ensure_ascii=False), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
