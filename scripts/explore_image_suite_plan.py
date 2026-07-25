#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explore LinkFox-style image understanding + suite planning (no image gen).

Examples:
  python scripts/explore_image_suite_plan.py --image-url https://... --title "..."
  python scripts/explore_image_suite_plan.py --sku 0007 --region MY
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.sourcing.image_suite_plan import (  # noqa: E402
    DEFAULT_VISION_MODEL,
    analyze_and_plan_suite,
    render_plan_markdown,
    save_plan,
)


def _resolve_from_sku(sku: str, region: str) -> tuple[str, str, str]:
    """Return (image_url, title, out_tag)."""
    db = ROOT / "data" / "shop.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    # seller_sku may be 0007 or 660007 depending on shop
    rows = con.execute(
        """
        SELECT p.seller_sku, p.product_name, p.sku_name, p.image_url, p.price, p.currency,
               s.region, s.name AS shop_name
        FROM products p
        LEFT JOIN shops s ON s.cipher = p.shop_cipher
        WHERE (p.seller_sku = ? OR p.seller_sku LIKE ?)
          AND (? = '' OR UPPER(s.region) = UPPER(?))
          AND p.image_url IS NOT NULL AND TRIM(p.image_url) != ''
        ORDER BY CASE WHEN UPPER(s.region)='MY' THEN 0 ELSE 1 END, p.updated_at DESC
        LIMIT 5
        """,
        (sku, f"%{sku}", region, region),
    ).fetchall()
    con.close()
    if not rows:
        raise SystemExit(f"no product with image for sku={sku!r} region={region!r}")
    row = rows[0]
    url = (row["image_url"] or "").strip()
    # Prefer original TikTok asset over tiny 300x300 resize thumbs
    url = url.replace("~tplv-o3syd03w52-resize-jpeg:300:300.jpeg", "~tplv-o3syd03w52-origin-jpeg.jpeg")
    title = " / ".join(
        x for x in [(row["product_name"] or "").strip(), (row["sku_name"] or "").strip()] if x
    )
    tag = f"{row['seller_sku']}_{(row['region'] or 'xx').lower()}"
    print(
        f"resolved sku={row['seller_sku']} region={row['region']} shop={row['shop_name']} "
        f"price={row['price']} {row['currency']}"
    )
    print(f"title: {title[:120]}")
    print(f"image: {url[:160]}")
    return url, title, tag


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Explore product vision + suite planning")
    ap.add_argument("--image-url", default="", help="Public https product image URL")
    ap.add_argument("--title", default="", help="Optional product title hint")
    ap.add_argument("--sku", default="", help="Lookup image from shop.db by seller_sku")
    ap.add_argument("--region", default="MY", help="Region filter when using --sku")
    ap.add_argument("--model", default=DEFAULT_VISION_MODEL)
    ap.add_argument("--out", default="", help="Output dir (default outputs/image_suite_plan/<tag>)")
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()

    tag = "manual"
    image_url = args.image_url.strip()
    title = args.title.strip()
    if args.sku.strip():
        image_url, title, tag = _resolve_from_sku(args.sku.strip(), args.region.strip())
    if not image_url:
        raise SystemExit("need --image-url or --sku")

    out_dir = Path(args.out) if args.out else ROOT / "outputs" / "image_suite_plan" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    proxy = None if args.no_proxy else "http://127.0.0.1:10808"

    print(f"\ncalling vision model={args.model} ...")
    try:
        plan = analyze_and_plan_suite(
            image_url,
            title=title,
            model=args.model,
            proxy=proxy,
            max_tokens=args.max_tokens,
        )
    except Exception as exc:
        raw_path = out_dir / "suite_plan_error.txt"
        raw_path.write_text(str(exc), encoding="utf-8")
        print(f"FAILED, raw dumped to {raw_path}")
        raise
    json_path, md_path = save_plan(plan, out_dir)
    print("\n" + render_plan_markdown(plan))
    print(f"saved: {json_path}")
    print(f"saved: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
