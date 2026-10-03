"""Repair an approved offer's existing TikTok descriptions without relisting."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.products.server import _approved_publication_snapshot_internal
from modules.products.tiktok_description_media import repair_description_media


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--shop-name", default="LivelyHive")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    snapshot = _approved_publication_snapshot_internal(
        offer_id=args.offer_id,
        plan_id=args.plan_id,
    )
    if not snapshot:
        raise RuntimeError("approved publication snapshot is unavailable")
    product = snapshot.get("product") or {}
    routes = ((product.get("image_routing") or {}).get("routes") or {})
    seller_skus = tuple(
        dict.fromkeys(
            str(row.get("model_sku") or row.get("seller_sku") or "").strip()
            for row in (snapshot.get("skus") or [])
            if str(row.get("model_sku") or row.get("seller_sku") or "").strip()
        )
    )
    brand_code = {"livelyhive": "LH", "homebloom": "HB"}.get(
        args.shop_name.casefold()
    )
    if not brand_code:
        raise RuntimeError("shop-name must be LivelyHive or HomeBloom")
    targets = [
        str(row.get("target_label") or "")
        for row in (snapshot.get("publication_targets") or [])
        if isinstance(row, dict)
        and str(row.get("target_label") or "").startswith(
            f"tiktok:{brand_code}_"
        )
    ]
    unsupported_targets = [
        str(row.get("target_label") or "")
        for row in (snapshot.get("publication_targets") or [])
        if isinstance(row, dict)
        and args.shop_name.casefold() == "livelyhive"
        and str(row.get("target_label") or "").startswith("tiktok:HB_")
    ]
    rows = []
    if args.shop_name.casefold() == "homebloom":
        for row in (snapshot.get("publication_targets") or []):
            label = str(row.get("target_label") or "") if isinstance(row, dict) else ""
            if not label.startswith("tiktok:LH_"):
                continue
            region = label.rsplit("_", 1)[-1]
            route = routes.get(label) or {}
            rows.append(
                {
                    "target_label": label,
                    **repair_description_media(
                        region=region,
                        shop_name="LivelyHive",
                        seller_skus=seller_skus,
                        expected_image_count=len(route.get("ordered_images") or []),
                        execute=False,
                    ),
                }
            )
    for label in targets:
        region = label.rsplit("_", 1)[-1]
        route = routes.get(label) or {}
        rows.append(
            {
                "target_label": label,
                **repair_description_media(
                    region=region,
                    shop_name=args.shop_name,
                    seller_skus=seller_skus,
                    expected_image_count=len(route.get("ordered_images") or []),
                    execute=args.execute,
                ),
            }
        )
    rows.extend(
        {
            "target_label": label,
            "region": label.rsplit("_", 1)[-1],
            "shop_name": "HomeBloom",
            "stage": "STOREFRONT_CAPABILITY_CHECK",
            "status": "BLOCKED",
            "code": "official_storefront_edit_unavailable",
            "request_attempted": False,
            "external_write_count": 0,
            "reason": (
                "Miaoshou publishing evidence does not expose a live product ID "
                "or an online-product edit/readback endpoint"
            ),
        }
        for label in unsupported_targets
    )
    now = datetime.now(timezone.utc).isoformat()
    run_id = "tiktok-description-media-" + uuid.uuid4().hex
    report = {
        "schema_version": "product-description-media-repair/v1",
        "run_id": run_id,
        "created_at": now,
        "updated_at": now,
        "offer_id": args.offer_id,
        "plan_id": args.plan_id,
        "revision": snapshot.get("product_revision"),
        "snapshot_digest": snapshot.get("snapshot_digest"),
        "platform": "TIKTOK",
        "operation": "UPDATE_EXISTING_DESCRIPTION_ONLY",
        "status": (
            "PUBLISHED"
            if rows and all(row.get("status") == "PUBLISHED" for row in rows)
            else "PROCESSING"
            if any(row.get("status") == "PROCESSING" for row in rows)
            else "PARTIAL"
            if any(row.get("status") == "PUBLISHED" for row in rows)
            else "BLOCKED"
        ),
        "external_write_count": sum(int(row.get("external_write_count") or 0) for row in rows),
        "targets": rows,
    }
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_digest"] = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    output = (
        ROOT
        / "reports"
        / "product-publication"
        / args.offer_id
        / str(snapshot.get("product_revision"))
        / run_id
        / "description-media-repair.json"
    )
    output.parent.mkdir(parents=True, exist_ok=False)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report_path": str(output)}, ensure_ascii=True, indent=2))
    return 0 if report["status"] == "PUBLISHED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
