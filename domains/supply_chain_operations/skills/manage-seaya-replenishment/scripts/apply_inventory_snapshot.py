#!/usr/bin/env python3
"""Apply a validated, read-only Seaya inventory snapshot to the dashboard."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[5]
DASHBOARD_DATA = ROOT / "domains" / "supply_chain_operations" / "dashboard" / "data.js"
PREFIX = "window.SUPPLY_CHAIN_DATA = "
WAREHOUSE_REGION = {"MY8803": "MY", "TH8806": "TH", "VN8805": "VN", "PH8807": "PH"}
REGION_PREFIX = {"MY": "660", "TH": "990", "VN": "880", "PH": "770"}
QUANTITY_FIELDS = ("stock", "available", "allocated", "frozen", "inbound")


def canonical_inventory_sku(source: object, region: str) -> str:
    if type(source) is not str or not source or "…" in source or "..." in source or "*" in source:
        raise ValueError("inventory SKU must be a complete built-in string")
    if len(source) == 4 and source.isdigit():
        return source
    prefix = REGION_PREFIX[region]
    if len(source) == 6 and source.isdigit() and source.startswith(prefix):
        return source[-4:]
    raise ValueError(f"inventory SKU {source!r} is invalid for {region}")


def aggregate_snapshot(payload: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in payload["records"]:
        warehouse = record["warehouse"]
        region = WAREHOUSE_REGION.get(warehouse)
        if region is None:
            raise ValueError(f"warehouse {warehouse!r} is outside the approved dashboard")
        sku = canonical_inventory_sku(record["seller_sku"], region)
        row = grouped[region].setdefault(
            sku,
            {**{field: 0 for field in QUANTITY_FIELDS}, "warehouse": warehouse, "sourceAliases": []},
        )
        if row["warehouse"] != warehouse:
            raise ValueError("canonical inventory SKU cannot cross warehouses")
        row["sourceAliases"].append(record["seller_sku"])
        for field in QUANTITY_FIELDS:
            value = record[field]
            if type(value) is not int or value < 0:
                raise ValueError(f"{field} must be a nonnegative built-in int")
            row[field] += value
    for rows in grouped.values():
        for row in rows.values():
            row["sourceAliases"] = sorted(set(row["sourceAliases"]))
    return dict(grouped)


def load_dashboard() -> dict[str, Any]:
    text = DASHBOARD_DATA.read_text(encoding="utf-8")
    return json.loads(text[text.index(PREFIX) + len(PREFIX) :].strip().removesuffix(";"))


def apply_inventory(data: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    grouped = aggregate_snapshot(payload)
    captured_at = payload["capturedAt"]
    for region, dashboard_rows in data["countries"].items():
        inventory_rows = grouped.get(region, {})
        for item in dashboard_rows:
            fact = inventory_rows.get(item["sku"])
            if fact is None:
                item["inventory"] = {
                    **{field: 0 for field in QUANTITY_FIELDS},
                    "warehouse": data["config"][region]["warehouse"],
                }
                item["kind"] = "first_stock"
                continue
            item["inventory"] = {field: fact[field] for field in QUANTITY_FIELDS} | {
                "warehouse": fact["warehouse"]
            }
            item["kind"] = "existing"
            item["sourceAliases"] = sorted(
                set(item.get("sourceAliases") or []) | set(fact["sourceAliases"])
            )

        canonical_payload = {
            sku: inventory_rows[sku] for sku in sorted(inventory_rows)
        }
        digest = hashlib.sha256(
            json.dumps(canonical_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        data["config"][region]["inventoryEvidence"] = {
            "capturedAt": captured_at,
            "source": payload["source"],
            "rawRows": sum(
                1 for record in payload["records"] if record["warehouse"] == data["config"][region]["warehouse"]
            ),
            "canonicalSkuCount": len(inventory_rows),
            "digest": digest,
        }
    data["snapshotDate"] = captured_at[:10]
    data["config"]["snapshotDate"] = captured_at[:10]
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    data = apply_inventory(load_dashboard(), payload)
    DASHBOARD_DATA.write_text(
        PREFIX + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "UPDATED", "capturedAt": payload["capturedAt"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
