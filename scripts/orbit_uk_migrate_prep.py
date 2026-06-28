"""Orbit Hive：UK 旧品迁移准备（dry-run，不发布）。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.mx_collect_match import find_common_id as find_collect_common_id
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY
from scripts.uk_pop_pricing import fetch_cny_gbp, quote_match_key

DEFAULT_KEYS = ["0003", "0169"]


def catalog_row(mk: str) -> dict | None:
    conn = sqlite3.connect(ROOT / "data" / "shop.db")
    conn.row_factory = sqlite3.Row
    for reg in ("PH", "MY", "TH", "VN"):
        row = conn.execute(
            """
            SELECT p.seller_sku, p.product_id, s.region
            FROM products p JOIN shops s ON p.shop_cipher = s.cipher
            LEFT JOIN sku_costs sc ON sc.sku_id = p.sku_id
            WHERE p.seller_sku LIKE ? AND UPPER(s.region) = ?
              AND sc.cost_cny IS NOT NULL AND sc.cost_cny > 0
            LIMIT 1
            """,
            (f"%{mk}", reg),
        ).fetchone()
        if row:
            return dict(row)
    return None


def find_common_id(product_id: str, *, mk: str = "", seller_sku: str = "") -> int | None:
    return find_collect_common_id(mk=mk, seller_sku=seller_sku, product_id=str(product_id))


def prep_one(mk: str, *, rate: float) -> dict:
    row = catalog_row(mk)
    if not row:
        return {"mk": mk, "status": "blocked", "reason": "shop.db 无目录/成本"}
    known = KNOWN_BY_MATCH_KEY.get(mk, {})
    try:
        q = quote_match_key(mk, cny_gbp=rate)
    except Exception as exc:
        return {
            "mk": mk,
            "status": "blocked",
            "reason": str(exc),
            "seller_sku": row.get("seller_sku"),
            "product_id": row.get("product_id"),
        }
    common_id = find_common_id(str(row["product_id"]), mk=mk, seller_sku=str(row["seller_sku"]))
    return {
        "mk": mk,
        "status": "ready" if common_id else "need_collect",
        "seller_sku": row["seller_sku"],
        "product_id": row["product_id"],
        "region": row["region"],
        "common_collect_id": common_id,
        "list_gbp": q.list_price_ceil_gbp,
        "sale_gbp": round(q.sale_price_gbp, 2),
        "weight_kg": q.weight_kg,
        "package_cm": q.package_cm,
        "manual_dims": bool(known.get("l")),
        "publish_allowed": False,
        "note": "需 Web 审批后才可真实发布",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="UK 迁移 dry-run")
    ap.add_argument("keys", nargs="*", default=DEFAULT_KEYS, help="对齐码")
    ap.add_argument("--json-out", default=str(ROOT / "data" / "uk_confirm" / "orbit_dry_run.json"))
    args = ap.parse_args()

    rate = fetch_cny_gbp()
    keys = [k.zfill(4)[-4:] for k in args.keys]
    results = [prep_one(mk, rate=rate) for mk in keys]
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ready = sum(1 for r in results if r["status"] == "ready")
    need = sum(1 for r in results if r["status"] == "need_collect")
    blocked = sum(1 for r in results if r["status"] == "blocked")
    print(f">>> UK dry-run: {len(results)} SKU | ready={ready} need_collect={need} blocked={blocked}")
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    print(f">>> wrote {out}")
    return 0 if blocked == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
