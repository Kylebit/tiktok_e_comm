#!/usr/bin/env python3
"""DEPRECATED COMPATIBILITY: direct Shopee dispatch for incident diagnosis."""
from __future__ import annotations

import argparse
from typing import Any, Mapping

from _common import (
    DEFAULT_BASE_URL,
    add_repo_to_path,
    dispatch_fact,
    emit,
    load_json,
)


def retire_deleted_entry(
    data: Mapping[str, Any], *, old_global_item_id: str, seller_sku: str
) -> dict[str, Any]:
    old_id = str(old_global_item_id or "").strip()
    expected_sku = str(seller_sku or "").strip()
    copied = {str(key): dict(value) if isinstance(value, dict) else value for key, value in data.items()}
    entry = copied.get(old_id)
    if not isinstance(entry, dict):
        raise ValueError("deleted Shopee mapping entry is unavailable")
    keys = [str(entry.get("match_key") or ""), *[str(value) for value in entry.get("match_keys") or []]]
    if expected_sku not in keys:
        raise ValueError("deleted Shopee mapping does not match the approved seller SKU")
    entry["retired_match_key"] = expected_sku
    entry["match_key"] = ""
    entry["match_keys"] = []
    entry["retired_reason"] = "official_global_status_deleted"
    copied[old_id] = entry
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch the approved Shopee global product")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--repo")
    parser.add_argument("--retire-deleted-global-id")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        snapshot = load_json(args.snapshot)
        recovery = None
        if args.retire_deleted_global_id:
            if not args.execute:
                raise RuntimeError("mapping retirement requires --execute")
            add_repo_to_path(args.repo)
            from modules.shopee.global_sku_map import load_map, save_map

            seller_sku = str(snapshot.get("request", {}).get("seller_sku") or "").strip()
            save_map(retire_deleted_entry(
                load_map(),
                old_global_item_id=args.retire_deleted_global_id,
                seller_sku=seller_sku,
            ))
            recovery = {
                "action": "RETIRED_DELETED_MAPPING",
                "old_global_item_id": str(args.retire_deleted_global_id),
            }
        result = dispatch_fact(
            platform="shopee",
            endpoint="/api/product-workspace/publish-shopee-global",
            snapshot=snapshot,
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
            execute=args.execute,
        )
        if recovery:
            result["recovery"] = recovery
        emit(result, args.output)
        return 0 if result.get("accepted") is True else 1
    except Exception as error:
        result = {
            "schema_version": "platform-dispatch-fact/v1",
            "platform": "shopee",
            "attempted": False,
            "accepted": False,
            "write_outcome": "NOT_ATTEMPTED",
            "message": str(error),
        }
        emit(result, args.output)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
