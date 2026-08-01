"""Pull redacted TikTok and Shopee order-demand snapshots without business writes."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import auth as tiktok_auth
from core import shops as tiktok_shops
from core.api_client import post as tiktok_post
from domains.supply_chain_operations.order_demand import (
    aggregate_shopee_orders,
    aggregate_tiktok_orders,
    finalize_order_snapshot,
)
from modules.shopee.auth import ensure_shop_token
from modules.shopee.client import shop_get
from modules.shopee.shops import sync_shop_ids


REGIONS = ("MY", "TH", "VN", "PH")
TIKTOK_ORDER_SEARCH = "/order/202309/orders/search"
SHOPEE_ORDER_LIST = "/api/v2/order/get_order_list"
SHOPEE_ORDER_DETAIL = "/api/v2/order/get_order_detail"


def _chunks(start: int, end: int, days: int):
    step = days * 86400
    cursor = start
    while cursor < end:
        chunk_end = min(end, cursor + step)
        yield cursor, chunk_end
        cursor = chunk_end


def _tiktok_shop_ciphers(token: str) -> tuple[dict[str, str], int]:
    shops = tiktok_shops.list_shops(token)
    selected: dict[str, str] = {}
    for shop in shops:
        region = str(shop.get("region") or "").upper()
        cipher = shop.get("cipher") or shop.get("shop_cipher")
        if region in REGIONS and region not in selected and type(cipher) is str and cipher:
            selected[region] = cipher
    return selected, 1


def pull_tiktok_region(
    token: str, cipher: str, start: int, end: int
) -> tuple[list[dict[str, Any]], int]:
    by_id: dict[str, dict[str, Any]] = {}
    reads = 0
    for chunk_start, chunk_end in _chunks(start, end, 7):
        page_token = ""
        while True:
            query = {"shop_cipher": cipher, "page_size": "100"}
            if page_token:
                query["page_token"] = page_token
            result = tiktok_post(
                TIKTOK_ORDER_SEARCH,
                token,
                query,
                {"create_time_ge": chunk_start, "create_time_lt": chunk_end},
            )
            reads += 1
            if result.get("code") != 0:
                raise RuntimeError("TikTok order search returned a business error")
            data = result.get("data") or {}
            for order in data.get("orders") or []:
                order_id = order.get("id")
                if type(order_id) is str and order_id:
                    by_id[order_id] = order
            page_token = data.get("next_page_token") or ""
            if not page_token:
                break
    return list(by_id.values()), reads


def _shopee_order_numbers(
    shop_id: int, token: str, start: int, end: int
) -> tuple[list[str], int]:
    numbers: set[str] = set()
    reads = 0
    for chunk_start, chunk_end in _chunks(start, end, 14):
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "time_range_field": "create_time",
                "time_from": chunk_start,
                "time_to": chunk_end,
                "page_size": 100,
            }
            if cursor:
                params["cursor"] = cursor
            response = shop_get(SHOPEE_ORDER_LIST, shop_id, token, params)
            reads += 1
            if response.get("error"):
                raise RuntimeError("Shopee order list returned a business error")
            body = response.get("response") or {}
            for order in body.get("order_list") or []:
                order_sn = order.get("order_sn")
                if type(order_sn) is str and order_sn:
                    numbers.add(order_sn)
            if not body.get("more"):
                break
            cursor = body.get("next_cursor") or ""
            if not cursor:
                raise RuntimeError("Shopee order list pagination cursor unavailable")
    return sorted(numbers), reads


def pull_shopee_region(
    shop_id: int, token: str, start: int, end: int
) -> tuple[list[dict[str, Any]], int]:
    order_numbers, reads = _shopee_order_numbers(shop_id, token, start, end)
    details: list[dict[str, Any]] = []
    for offset in range(0, len(order_numbers), 50):
        batch = order_numbers[offset : offset + 50]
        response = shop_get(
            SHOPEE_ORDER_DETAIL,
            shop_id,
            token,
            {
                "order_sn_list": ",".join(batch),
                "response_optional_fields": "item_list",
            },
        )
        reads += 1
        if response.get("error"):
            raise RuntimeError("Shopee order detail returned a business error")
        details.extend((response.get("response") or {}).get("order_list") or [])
    if len(details) != len(order_numbers):
        raise RuntimeError("Shopee order detail coverage is incomplete")
    return details, reads


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--regions", nargs="+", default=list(REGIONS))
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "supply_chain_order_demand_latest.json",
    )
    args = parser.parse_args()
    regions = tuple(dict.fromkeys(str(value).upper() for value in args.regions))
    if args.days < 30 or any(region not in REGIONS for region in regions):
        raise SystemExit("days must be >=30 and regions must be MY/TH/VN/PH")

    captured_at = datetime.now(timezone.utc)
    end = int(captured_at.timestamp())
    start = end - args.days * 86400
    output: dict[str, Any] = {
        "schemaVersion": "order_demand_snapshot_v1",
        "capturedAt": captured_at.isoformat(),
        "days": args.days,
        "countries": {},
        "networkReads": 0,
        "authWrites": "Shopee refresh only when required",
        "businessWrites": 0,
    }

    tiktok_token = tiktok_auth.access_token()
    ciphers, reads = _tiktok_shop_ciphers(tiktok_token)
    output["networkReads"] += reads
    shopee_ids = sync_shop_ids()

    for region in regions:
        if region not in ciphers or region not in shopee_ids:
            raise RuntimeError(f"{region} shop binding unavailable")
        print(f"[{region}] TikTok order pages...", flush=True)
        tiktok_orders, reads = pull_tiktok_region(
            tiktok_token, ciphers[region], start, end
        )
        output["networkReads"] += reads
        tk_rows, tk_evidence = aggregate_tiktok_orders(tiktok_orders, region)
        tk_snapshot = finalize_order_snapshot(
            tk_rows,
            region=region,
            platform="TikTok",
            captured_at=captured_at,
            days=args.days,
            evidence=tk_evidence,
        )
        print(
            f"[{region}] TikTok ready: {tk_evidence.get('orders_included', 0)} orders, "
            f"{len(tk_snapshot['facts'])} mapped SKUs",
            flush=True,
        )

        print(f"[{region}] Shopee order pages/details...", flush=True)
        shop_id = int(shopee_ids[region])
        shopee_token = ensure_shop_token(shop_id)
        shopee_orders, reads = pull_shopee_region(
            shop_id, shopee_token, start, end
        )
        output["networkReads"] += reads
        sp_rows, sp_evidence = aggregate_shopee_orders(shopee_orders, region)
        sp_snapshot = finalize_order_snapshot(
            sp_rows,
            region=region,
            platform="Shopee",
            captured_at=captured_at,
            days=args.days,
            evidence=sp_evidence,
        )
        print(
            f"[{region}] Shopee ready: {sp_evidence.get('orders_included', 0)} orders, "
            f"{len(sp_snapshot['facts'])} mapped SKUs",
            flush=True,
        )
        output["countries"][region] = {
            "tiktok": tk_snapshot,
            "shopee": sp_snapshot,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "READY",
                "regions": list(regions),
                "network_reads": output["networkReads"],
                "business_writes": 0,
                "output": str(args.output),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
