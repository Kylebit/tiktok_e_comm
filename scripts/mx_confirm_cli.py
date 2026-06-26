"""MX 确认单 CLI：approve / reject / show。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.mx_confirm import (
    approve_confirm,
    dispatch_confirm_card,
    get_confirm,
    reject_confirm,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="MX 上架确认单")
    ap.add_argument("action", choices=["show", "approve", "reject"])
    ap.add_argument("token")
    args = ap.parse_args()

    card = get_confirm(args.token)
    if not card:
        print(f"确认单不存在: {args.token}", file=sys.stderr)
        return 1

    if args.action == "show":
        dispatch_confirm_card(card)
        return 0
    if args.action == "approve":
        approve_confirm(args.token)
        print(f"✓ 已确认 {card.match_key} ({args.token})")
        return 0
    reject_confirm(args.token)
    print(f"✗ 已取消 {card.match_key} ({args.token})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
