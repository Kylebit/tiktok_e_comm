#!/usr/bin/env python3
"""Dispatch explicitly selected Shopee regional shops from one global item."""
from __future__ import annotations

import argparse

from _common import add_repo_to_path, emit, load_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dispatch independent Shopee regional publish tasks"
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--global-item-id", required=True)
    parser.add_argument("--dispatch-output")
    parser.add_argument("--repo")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if not args.execute:
            raise RuntimeError("regional Shopee dispatch requires --execute")
        snapshot = load_json(args.snapshot)
        add_repo_to_path(args.repo)
        from modules.shopee.skill_regions import (
            OfficialShopeeRegionRuntime,
            dispatch_selected_regions,
        )

        result = dispatch_selected_regions(
            snapshot,
            global_item_id=args.global_item_id,
            runtime=OfficialShopeeRegionRuntime(),
        )
        emit(result, args.dispatch_output)
        accepted = int(result.get("accepted_target_count") or 0)
        return 0 if accepted == int(result.get("target_count") or 0) else 1
    except Exception as error:
        emit(
            {
                "schema_version": "shopee-regional-dispatch/v1",
                "platform": "shopee",
                "target_count": 0,
                "accepted_target_count": 0,
                "message": str(error),
            },
            args.dispatch_output,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
