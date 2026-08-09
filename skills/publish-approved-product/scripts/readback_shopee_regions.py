#!/usr/bin/env python3
"""Read official Shopee shop facts for accepted regional publish tasks."""
from __future__ import annotations

import argparse

from _common import add_repo_to_path, emit, load_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify independent Shopee regional shop items"
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--dispatch", required=True)
    parser.add_argument("--global-item-id", required=True)
    parser.add_argument("--readback-output")
    parser.add_argument("--poll-attempts", type=int, default=3)
    parser.add_argument("--repo")
    parser.add_argument("--execute-readback", action="store_true")
    args = parser.parse_args()
    try:
        if not args.execute_readback:
            raise RuntimeError("regional Shopee readback requires --execute-readback")
        snapshot = load_json(args.snapshot)
        dispatch = load_json(args.dispatch)
        add_repo_to_path(args.repo)
        from modules.shopee.skill_regions import (
            OfficialShopeeRegionRuntime,
            readback_dispatched_regions,
        )

        result = readback_dispatched_regions(
            snapshot,
            dispatch,
            global_item_id=args.global_item_id,
            runtime=OfficialShopeeRegionRuntime(),
            poll_attempts=args.poll_attempts,
        )
        emit(result, args.readback_output)
        return 0 if result.get("complete") is True else 1
    except Exception as error:
        emit(
            {
                "schema_version": "shopee-regional-readback/v1",
                "platform": "shopee",
                "target_count": 0,
                "verified_target_count": 0,
                "complete": False,
                "message": str(error),
            },
            args.readback_output,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
