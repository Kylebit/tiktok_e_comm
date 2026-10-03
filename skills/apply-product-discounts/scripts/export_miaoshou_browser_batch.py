from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


def export_batch(plan: Mapping[str, Any], targets: set[str]) -> dict[str, Any]:
    rows = []
    for action in plan.get("actions", []):
        if not isinstance(action, Mapping):
            continue
        target = str(action.get("target_label") or "")
        if target not in targets:
            continue
        if target.startswith("tiktok:LH_") or target.startswith("shopee:"):
            continue
        rows.append({
            "target_label": target,
            "product_id": str(action.get("product_id") or ""),
            "seller_skus": list(action.get("seller_skus") or []),
            "discount_percent": action.get("discount_percent"),
            "activity_ids": list(action.get("activity_ids") or []),
            "action_digest": action.get("action_digest"),
            "status": "REFERENCE_REQUIRES_FROZEN_PLAN",
            "source_status": action.get("status"),
            "approval_status": "NOT_APPROVED",
        })
    rows.sort(key=lambda row: (row["target_label"], row["product_id"]))
    return {"schema_version": "miaoshou-browser-reference/v2", "approval_status": "NOT_APPROVED", "requested_targets": sorted(targets), "actions": rows,
            "missing_targets": sorted(targets - {row["target_label"] for row in rows})}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--target", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.plan.read_text(encoding="utf-8"))
    output = export_batch(payload, set(args.target))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "count": len(output["actions"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
