"""随机发现 ready SKU → MX/UK Web 审批收件箱。"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.mx_collect_match import load_collect_index
from modules.catalog.tk_sku_groups import expand_skip_keys
from modules.miaoshou.migrate_dispatch import queue_mx_unit, queue_uk_unit, scan_ready_units
from scripts.orbit_mx_migrate_prep import prep_one as mx_prep_one
from scripts.orbit_uk_migrate_prep import prep_one as uk_prep_one
from scripts.orbit_send_mx_approval import build_card_for_mk as mx_build_card
from scripts.orbit_send_uk_approval import build_card_for_mk as uk_build_card
from scripts.mx_pop_pricing import fetch_cny_mxn
from scripts.uk_pop_pricing import fetch_cny_gbp


def _catalog_match_keys() -> list[str]:
    conn = sqlite3.connect(ROOT / "data" / "shop.db")
    rows = conn.execute(
        """
        SELECT DISTINCT SUBSTR(p.seller_sku, -4) AS mk
        FROM products p
        JOIN sku_costs sc ON sc.sku_id = p.sku_id
        WHERE sc.cost_cny IS NOT NULL AND sc.cost_cny > 0
          AND LENGTH(p.seller_sku) >= 4
        """
    ).fetchall()
    return sorted({str(mk).zfill(4)[-4:] for (mk,) in rows})


def _skip_keys(region: str) -> set[str]:
    if region == "mx":
        from modules.miaoshou.mx_web_approval import list_cards, published_match_keys
    else:
        from modules.miaoshou.uk_web_approval import list_cards, published_match_keys

    skip = published_match_keys()
    for card in list_cards(status="pending", auto_archive=False):
        mk = str(card.get("match_key") or "").zfill(4)[-4:]
        if mk:
            skip.add(mk)
        for k in card.get("match_keys") or []:
            skip.add(str(k).zfill(4)[-4:])
    return expand_skip_keys(skip)


def _pick_random_ready_units(
    *,
    region: str,
    count: int,
    pool: list[str],
    skip: set[str],
    rate: float,
    seed: int | None,
) -> tuple[list[list[str]], list[dict]]:
    prep = mx_prep_one if region == "mx" else uk_prep_one
    candidates = [mk for mk in pool if mk not in skip]
    rng = random.Random(seed)
    rng.shuffle(candidates)
    return scan_ready_units(candidates, limit=count, skip=skip, prep_fn=prep, rate=rate)


def main() -> int:
    ap = argparse.ArgumentParser(description="随机 MX/UK dispatch")
    ap.add_argument("--mx", type=int, default=10, help="MX 条数")
    ap.add_argument("--uk", type=int, default=5, help="UK 条数")
    ap.add_argument("--seed", type=int, default=0, help="随机种子（0=系统随机）")
    args = ap.parse_args()
    seed = None if args.seed == 0 else args.seed

    print("Loading collect index (cached if fresh)…", flush=True)
    load_collect_index()
    pool = _catalog_match_keys()
    print(f"Catalog pool: {len(pool)} match keys", flush=True)

    mx_rate = fetch_cny_mxn()
    uk_rate = fetch_cny_gbp()
    mx_skip = _skip_keys("mx")
    uk_skip = _skip_keys("uk")

    mx_keys, mx_skipped = _pick_random_ready_units(
        region="mx", count=args.mx, pool=pool, skip=mx_skip, rate=mx_rate, seed=seed
    )
    uk_keys, uk_skipped = _pick_random_ready_units(
        region="uk",
        count=args.uk,
        pool=pool,
        skip=expand_skip_keys(mx_skip | uk_skip | {mk for unit in mx_keys for mk in unit}),
        rate=uk_rate,
        seed=(seed + 1) if seed is not None else None,
    )

    print(f"\nMX ready units: {len(mx_keys)}/{args.mx} -> {mx_keys}", flush=True)
    mx_queued: list[dict] = []
    for unit in mx_keys:
        row = queue_mx_unit(unit, rate=mx_rate, build_single=mx_build_card)
        mx_queued.append(row)
        print(f"  MX queued {row['kind']} {row['mk']} -> {row['web_url']}")

    print(f"\nUK ready units: {len(uk_keys)}/{args.uk} -> {uk_keys}", flush=True)
    uk_queued: list[dict] = []
    for unit in uk_keys:
        row = queue_uk_unit(unit, rate=uk_rate, build_single=uk_build_card)
        uk_queued.append(row)
        print(f"  UK queued {row['kind']} {row['mk']} -> {row['web_url']}")

    result = {
        "mx": {"requested": args.mx, "units": mx_keys, "queued": mx_queued, "skipped_n": len(mx_skipped)},
        "uk": {"requested": args.uk, "units": uk_keys, "queued": uk_queued, "skipped_n": len(uk_skipped)},
    }
    out = ROOT / "data" / "random_dispatch_last.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n>>> MX inbox: http://127.0.0.1:8765/mx")
    print(f">>> UK inbox: http://127.0.0.1:8765/uk")
    return 0 if mx_queued or uk_queued else 1


if __name__ == "__main__":
    raise SystemExit(main())
