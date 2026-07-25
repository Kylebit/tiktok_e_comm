#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Expand suite_plan.json selected items into per-shot generation prompts.

Examples:
  python scripts/explore_shot_prompts.py --plan outputs/image_suite_plan/660007_my/suite_plan.json
  python scripts/explore_shot_prompts.py --plan ... --ids sp1,wb1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.sourcing.image_shot_prompts import (  # noqa: E402
    build_shot_prompts,
    load_suite_plan,
    render_shots_markdown,
    save_shot_prompts,
)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Build shot prompts from suite_plan.json")
    ap.add_argument("--plan", required=True, help="Path to suite_plan.json")
    ap.add_argument("--ids", default="", help="Comma-separated item ids (default: all selected)")
    ap.add_argument(
        "--out",
        default="",
        help="Output dir (default: same folder as plan)",
    )
    args = ap.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.is_file():
        raise SystemExit(f"plan not found: {plan_path}")

    only_ids = [x.strip() for x in args.ids.split(",") if x.strip()] or None
    plan = load_suite_plan(plan_path)
    bundle = build_shot_prompts(plan, only_ids=only_ids)
    if not bundle["shots"]:
        raise SystemExit("no selected shots to expand (check selected flags / --ids)")

    out_dir = Path(args.out) if args.out else plan_path.parent
    json_path, md_path = save_shot_prompts(bundle, out_dir)
    print(render_shots_markdown(bundle))
    print(f"saved: {json_path}")
    print(f"saved: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
