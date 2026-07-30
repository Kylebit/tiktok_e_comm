"""Merge redacted Shopee demand snapshots into the local dashboard.

This script never reads credentials or calls Shopee.  Its inputs are
SKU-aggregate JSON snapshots and a read-only catalog database.
"""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))

from domains.supply_chain_operations.shopee_demand import canonical_demand_sku

PREFIX = "window.SUPPLY_CHAIN_DATA = "


def _read_dashboard(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(PREFIX) or not text.endswith(";\n"):
        raise ValueError("unexpected dashboard data wrapper")
    return json.loads(text[len(PREFIX) : -2])


def _catalog_metadata(db_path: Path, region: str) -> dict[str, dict]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    result: dict[str, dict] = {}
    try:
        rows = connection.execute(
            """
            SELECT seller_sku, product_name, image_url
            FROM shopee_products
            WHERE region = ?
            ORDER BY updated_at DESC
            """,
            (region,),
        )
        for row in rows:
            sku = canonical_demand_sku(str(row["seller_sku"] or ""))
            if sku and sku not in result:
                result[sku] = {
                    "name": str(row["product_name"] or "").strip(),
                    "image_url": str(row["image_url"] or "").strip(),
                }
    finally:
        connection.close()
    return result


def _download_image(url: str, target: Path) -> bool:
    if target.is_file() or not url.startswith("https://"):
        return target.is_file()
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read()
    if len(payload) < 100:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return True


def apply_region(
    data: dict,
    *,
    region: str,
    snapshot: dict,
    metadata: dict[str, dict],
    assets_dir: Path,
    download_images: bool,
) -> dict[str, int]:
    rows = data["countries"][region]
    global_templates: dict[str, dict] = {}
    for country_rows in data["countries"].values():
        for row in country_rows:
            global_templates.setdefault(row["sku"], row)

    by_sku = {row["sku"]: row for row in rows}
    warehouse = data["config"][region]["warehouse"]
    downloaded = 0
    added = 0
    for sku in sorted(snapshot["skus"]):
        if sku in by_sku:
            continue
        if sku in global_templates:
            row = copy.deepcopy(global_templates[sku])
            row["kind"] = "first_stock"
            row["inventory"] = {
                "stock": 0,
                "available": 0,
                "allocated": 0,
                "frozen": 0,
                "inbound": 0,
                "warehouse": warehouse,
            }
        else:
            meta = metadata.get(sku) or {}
            row = {
                "sku": sku,
                "name": meta.get("name") or f"Shopee {region} SKU {sku}",
                "image": f"assets/sku-{sku}.jpg",
                "kind": "first_stock",
                "dimensionsCm": None,
                "weightG": 0,
                "costCny": 0,
                "inventory": {
                    "stock": 0,
                    "available": 0,
                    "allocated": 0,
                    "frozen": 0,
                    "inbound": 0,
                    "warehouse": warehouse,
                },
                "channels": {},
            }
        rows.append(row)
        by_sku[sku] = row
        added += 1

        target = assets_dir / f"sku-{sku}.jpg"
        if download_images and not target.is_file():
            if _download_image((metadata.get(sku) or {}).get("image_url") or "", target):
                downloaded += 1

    empty_channel = {
        "days": 366,
        "orders": 0,
        "units": 0,
        "recent30Units": 0,
        "customerPayment": 0.0,
        "actualShippingFee": 0.0,
        "source": f"Shopee {region} 结算",
        "evidence": "complete_settled_window",
        "state": "READY",
    }
    for row in rows:
        row.setdefault("channels", {})
        row["channels"].setdefault(
            "tiktok",
            {
                "days": 31,
                "orders": 0,
                "units": 0,
                "recent30Units": 0,
                "customerPayment": 0.0,
                "actualShippingFee": None,
                "source": f"TikTok {region}",
                "evidence": "no_sku_fact",
                "state": "READY",
            },
        )
        fact = copy.deepcopy(empty_channel)
        if row["sku"] in snapshot["skus"]:
            fact.update(snapshot["skus"][row["sku"]])
            fact["source"] = f"Shopee {region} 2025-07-30~2026-07-30 完整结算"
        row["channels"]["shopee"] = fact

    rows.sort(key=lambda row: row["sku"])
    config = data["config"][region]
    config["demandCoverage"] = "TikTok 31天 + Shopee 366天完整已结算"
    config["shippingCoverage"] = "TikTok 与 Shopee 均有SKU级跨境运费"
    config["shopeeDemandEvidence"] = {
        "window": "2025-07-30~2026-07-30",
        "orders": snapshot["order_count"],
        "successfulDetails": snapshot["successful_details"],
        "errors": snapshot["error_count"],
        "mappedSkuCount": len(snapshot["skus"]),
        "catalogResolvedItems": snapshot["evidence"]["catalog_resolved_items"],
        "unmappedItemLines": snapshot["evidence"]["rejected_items"],
    }
    return {"added": added, "downloaded": downloaded}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--catalog-db", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--download-images", action="store_true")
    parser.add_argument(
        "--fixed-head-freight-only",
        action="store_true",
        help="Only persist the approved CNY 1/unit policy; do not read snapshots or catalog.",
    )
    args = parser.parse_args()

    data = _read_dashboard(args.data)
    for region in ("MY", "TH", "VN", "PH"):
        config = data["config"][region]
        config["fixedHeadFreightUnitCny"] = 1
        for obsolete_key in (
            "freightRateCnyM3",
            "minimumBillableM3",
            "inboundSurchargeThresholdM3",
            "inboundSurchargeCny",
        ):
            config.pop(obsolete_key, None)
    summary = {}
    if args.fixed_head_freight_only:
        args.data.write_text(
            PREFIX + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
            encoding="utf-8",
        )
        print(json.dumps({"fixedHeadFreightUnitCny": 1}, ensure_ascii=False))
        return 0
    for region in ("VN", "PH"):
        snapshot_path = (
            args.snapshot_dir / f"shopee_demand_{region}_20250730_20260730.json"
        )
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if snapshot.get("error_count") != 0:
            raise RuntimeError(f"{region} snapshot has detail errors")
        summary[region] = apply_region(
            data,
            region=region,
            snapshot=snapshot,
            metadata=_catalog_metadata(args.catalog_db, region),
            assets_dir=args.assets_dir,
            download_images=args.download_images,
        )

    args.data.write_text(
        PREFIX + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
