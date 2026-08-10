#!/usr/bin/env python3
"""DEPRECATED COMPATIBILITY: direct Ozon dispatch for incident diagnosis."""
from _dispatch_cli import run


if __name__ == "__main__":
    raise SystemExit(run("ozon", "/api/product-workspace/publish-ozon"))
