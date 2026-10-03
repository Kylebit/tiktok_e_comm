from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
OFFICIAL = ROOT / "reports/product-discounts/uncovered-products-20260901.json"
MIAOSHOU = ROOT / "reports/product-discounts/miaoshou-uncovered-products-20260901.json"
OUTPUT = ROOT / "reports/product-discounts/miaoshou-product-identity-evidence-20260901.json"


def _asset_hash(url: object) -> str | None:
    match = re.search(r"/([0-9a-f]{32})~", str(url or ""), re.I)
    return match.group(1).lower() if match else None


def _products(report: Mapping[str, Any], *, region: str | None = None, shop_name: str | None = None) -> list[Mapping[str, Any]]:
    result = []
    for store in report.get("stores", []):
        if region and str(store.get("region") or "").upper() != region.upper():
            continue
        if shop_name and str(store.get("shop_name") or "").casefold() != shop_name.casefold():
            continue
        result.extend(row for row in store.get("products", []) if isinstance(row, Mapping))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Suggest image/title identity references; never authorize a SKU or discount.")
    parser.add_argument("--official", type=Path, default=OFFICIAL)
    parser.add_argument("--miaoshou", type=Path, default=MIAOSHOU)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    official = json.loads(args.official.read_text(encoding="utf-8"))
    miaoshou = json.loads(args.miaoshou.read_text(encoding="utf-8"))
    official_hashes: dict[str, set[str]] = {}
    for product in _products(official):
        key = _asset_hash(product.get("main_image"))
        skus = {str(sku).strip() for sku in product.get("seller_skus", []) if str(sku).strip()}
        if key and skus:
            official_hashes.setdefault(key, set()).update(skus)

    # Similar titles/images only suggest review candidates; they never establish SKU identity.
    official_ph = _products(official, region="PH")
    homebloom_hashes: dict[str, dict[str, Any]] = {}
    for product in _products(miaoshou, region="PH", shop_name="HomeBloom"):
        key = _asset_hash(product.get("main_image"))
        title = str(product.get("title") or "").casefold()
        candidate_by_skus: dict[tuple[str, ...], tuple[float, list[str], str]] = {}
        for source in official_ph:
            skus = sorted({str(sku).strip() for sku in source.get("seller_skus", []) if str(sku).strip()})
            if skus:
                candidate = (SequenceMatcher(None, title, str(source.get("title") or "").casefold()).ratio(), skus, str(source.get("title") or ""))
                key_skus = tuple(skus)
                if key_skus not in candidate_by_skus or candidate[0] > candidate_by_skus[key_skus][0]:
                    candidate_by_skus[key_skus] = candidate
        candidates = list(candidate_by_skus.values())
        candidates.sort(reverse=True)
        if not key or not candidates:
            continue
        best = candidates[0]
        runner_up = candidates[1][0] if len(candidates) > 1 else 0.0
        if best[0] >= 0.65 and best[0] - runner_up >= 0.15:
            homebloom_hashes[key] = {"seller_skus": best[1], "match_score": round(best[0], 6), "source_title": best[2]}

    rows = []
    for store in miaoshou.get("stores", []):
        target = f"tiktok:HB_{store['region']}" if str(store.get("shop_name") or "").casefold().startswith("homebloom") else f"tiktok:{store['region']}"
        for product in store.get("products", []):
            key = _asset_hash(product.get("main_image"))
            direct = sorted(official_hashes.get(key, set())) if key else []
            branded = homebloom_hashes.get(key) if key else None
            skus = direct or (branded["seller_skus"] if branded else [])
            row = {
                "target_label": target,
                "product_id": str(product.get("product_id") or ""),
                "title": str(product.get("title") or ""),
                "main_image_asset_hash": key,
                "suggested_seller_skus": skus,
                "seller_skus": [],
                "status": "REFERENCE_ONLY" if skus else "BLOCKED_IDENTITY",
                "identity_verified": False,
                "source_kind": "CROSS_STORE_IMAGE_HASH_REFERENCE" if direct else ("TITLE_SIMILARITY_IMAGE_FAMILY_REFERENCE" if branded else None),
            }
            if branded:
                row.update(match_score=branded["match_score"], source_title=branded["source_title"])
            rows.append(row)
    result = {
        "schema_version": "miaoshou-product-identity-reference/v2",
        "external_writes": 0,
        "rows": rows,
        "summary": {
            "total": len(rows),
            "exact_identity": 0,
            "reference_only": sum(row["status"] == "REFERENCE_ONLY" for row in rows),
            "blocked_identity": sum(row["status"] == "BLOCKED_IDENTITY" for row in rows),
        },
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
