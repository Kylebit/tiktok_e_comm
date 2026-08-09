from __future__ import annotations

import argparse
from typing import Any, Callable, Mapping, Sequence

from _common import emit, load_json


Readback = Callable[[Mapping[str, Any], Mapping[str, Any], argparse.Namespace], dict[str, Any]]


def run(platform: str, reader: Readback, argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Read back {platform} publication facts")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--dispatch", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout-seconds", type=float, default=90)
    parser.add_argument("--poll-attempts", type=int, default=4)
    parser.add_argument("--poll-interval-seconds", type=float, default=3.0)
    parser.add_argument("--execute-readback", action="store_true")
    parser.add_argument("--fixture")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        snapshot = load_json(args.snapshot)
        dispatch = load_json(args.dispatch)
        if args.fixture:
            result = load_json(args.fixture)
        else:
            if not args.execute_readback:
                raise RuntimeError("official readback requires --execute-readback")
            result = reader(snapshot, dispatch, args)
        result.setdefault("schema_version", "platform-readback-fact/v1")
        result.setdefault("platform", platform)
        emit(result, args.output)
        return 0 if result.get("verified") is True else 1
    except Exception as error:
        result = {
            "schema_version": "platform-readback-fact/v1",
            "platform": platform,
            "verified": False,
            "complete": False,
            "status": "UNAVAILABLE",
            "message": str(error),
            "retry_safe": False,
        }
        emit(result, args.output)
        return 2
