from __future__ import annotations

import argparse
from typing import Sequence

from _common import DEFAULT_BASE_URL, dispatch_fact, emit, load_json


def run(platform: str, endpoint: str, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Dispatch approved {platform} facts only")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        result = dispatch_fact(
            platform=platform,
            endpoint=endpoint,
            snapshot=load_json(args.snapshot),
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
            execute=args.execute,
        )
        emit(result, args.output)
        return 0 if result.get("accepted") is True else 1
    except Exception as error:
        result = {
            "schema_version": "platform-dispatch-fact/v1",
            "platform": platform,
            "attempted": False,
            "accepted": False,
            "write_outcome": "NOT_ATTEMPTED",
            "message": str(error),
        }
        emit(result, args.output)
        return 2
