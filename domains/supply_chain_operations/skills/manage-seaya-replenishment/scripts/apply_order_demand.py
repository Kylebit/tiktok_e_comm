"""Apply redacted valid-order demand to the local dashboard."""

from __future__ import annotations

import argparse
import json
import ssl
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[5]
DASHBOARD = ROOT / "domains" / "supply_chain_operations" / "dashboard"
DATA_PATH = DASHBOARD / "data.js"
ASSETS = DASHBOARD / "assets"
PREFIX = "window.SUPPLY_CHAIN_DATA = "
PLATFORMS = ("tiktok", "shopee")


def load_dashboard() -> dict[str, Any]:
    text = DATA_PATH.read_text(encoding="utf-8")
    payload = text[text.index(PREFIX) + len(PREFIX) :].strip().removesuffix(";")
    return json.loads(payload)


def _zero_order_fact(platform: str, region: str, days: int) -> dict[str, Any]:
    display = "TikTok" if platform == "tiktok" else "Shopee"
    return {
        "days": days,
        "orders": 0,
        "units": 0,
        "recent30Units": 0,
        "quantityBasis": "valid_order",
        "eventTimeBasis": (
            "paid_time_preferred_confirmed_create_fallback"
            if platform == "tiktok"
            else "create_time_confirmed_order"
        ),
        "state": "READY",
        "source": f"{display} {region} 有效订单",
        "evidence": "complete_order_window_no_sku",
        "sourceAliases": [],
        "cancelledUnits": 0,
        "returnedUnits": 0,
    }


def _settlement_fields(channel: dict[str, Any]) -> dict[str, Any]:
    basis = (
        channel.get("economicsBasis", "settlement")
        if channel
        else "settlement_unavailable"
    )
    return {
        "settlementOrders": channel.get("settlementOrders", channel.get("orders", 0)),
        "settlementUnits": channel.get("settlementUnits", channel.get("units", 0)),
        "customerPayment": channel.get("customerPayment", 0),
        "actualShippingFee": channel.get("actualShippingFee"),
        "economicsBasis": basis,
        "settlementSource": channel.get("settlementSource", channel.get("source", "")),
        "settlementEvidence": channel.get(
            "settlementEvidence", channel.get("evidence", "")
        ),
    }


def _download_image(url: str, destination: Path) -> None:
    if destination.is_file():
        return
    if not url.startswith("https://"):
        raise ValueError("main image URL must use HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(
        request, timeout=20, context=ssl.create_default_context()
    ) as response:
        content = response.read(8 * 1024 * 1024 + 1)
    if not content or len(content) > 8 * 1024 * 1024:
        raise ValueError("main image is empty or exceeds 8 MiB")
    destination.write_bytes(content)


def apply_snapshot(data: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    days = snapshot["days"]
    presentation: dict[str, dict[str, Any]] = {}
    for rows in data["countries"].values():
        for row in rows:
            presentation.setdefault(row["sku"], row)

    for region, country_snapshot in snapshot["countries"].items():
        existing_by_sku = {row["sku"]: row for row in data["countries"][region]}
        all_skus = set(existing_by_sku)
        for platform in PLATFORMS:
            all_skus.update(country_snapshot[platform]["facts"])

        rebuilt: list[dict[str, Any]] = []
        for sku in sorted(all_skus):
            row = existing_by_sku.get(sku)
            if row is None:
                source_fact = next(
                    (
                        country_snapshot[platform]["facts"].get(sku)
                        for platform in PLATFORMS
                        if sku in country_snapshot[platform]["facts"]
                    ),
                    None,
                )
                if source_fact is None:
                    raise RuntimeError(f"{region} {sku} has no order presentation fact")
                template = presentation.get(sku) or {}
                image_path = ASSETS / f"sku-{sku}.jpg"
                _download_image(source_fact["imageUrl"], image_path)
                row = {
                    "sku": sku,
                    "name": source_fact["name"],
                    "image": f"assets/sku-{sku}.jpg",
                    "kind": "first_stock",
                    "dimensionsCm": template.get("dimensionsCm"),
                    "weightG": template.get("weightG", 0),
                    "costCny": template.get("costCny", 0),
                    "inventory": {
                        "stock": 0,
                        "available": 0,
                        "allocated": 0,
                        "frozen": 0,
                        "inbound": 0,
                        "warehouse": data["config"][region]["warehouse"],
                    },
                    "channels": {},
                }

            channels: dict[str, Any] = {}
            aliases = set(row.get("sourceAliases") or [])
            for platform in PLATFORMS:
                previous = (row.get("channels") or {}).get(platform) or {}
                settlement = _settlement_fields(previous)
                order_fact = country_snapshot[platform]["facts"].get(sku)
                if order_fact is None:
                    order_fact = _zero_order_fact(platform, region, days)
                else:
                    order_fact = {
                        key: value
                        for key, value in order_fact.items()
                        if key not in {"name", "imageUrl"}
                    }
                aliases.update(order_fact.get("sourceAliases") or [])
                channels[platform] = {**order_fact, **settlement}
            row["channels"] = channels
            if aliases:
                row["sourceAliases"] = sorted(aliases)
            rebuilt.append(row)

        data["countries"][region] = rebuilt
        config = data["config"][region]
        config["demandCoverage"] = "TikTok 31天有效订单 + Shopee 31天有效订单"
        config["orderDemandEvidence"] = {
            platform: {
                "ordersSeen": country_snapshot[platform]["evidence"].get(
                    "orders_seen", 0
                ),
                "ordersIncluded": country_snapshot[platform]["evidence"].get(
                    "orders_included", 0
                ),
                "ordersExcluded": country_snapshot[platform]["evidence"].get(
                    "orders_excluded", 0
                ),
                "itemLinesUnresolved": country_snapshot[platform]["evidence"].get(
                    "item_lines_unresolved", 0
                ),
                "digest": country_snapshot[platform]["digest"],
            }
            for platform in PLATFORMS
        }
    data["snapshotDate"] = snapshot["capturedAt"][:10]
    data["quantityBasis"] = "valid_order"
    data["economicsBasis"] = "settlement"
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    if snapshot.get("schemaVersion") != "order_demand_snapshot_v1":
        raise SystemExit("unsupported order-demand snapshot")
    data = apply_snapshot(load_dashboard(), snapshot)
    DATA_PATH.write_text(
        PREFIX
        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "UPDATED",
                "countries": {
                    region: len(rows) for region, rows in data["countries"].items()
                },
                "quantityBasis": data["quantityBasis"],
                "economicsBasis": data["economicsBasis"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
