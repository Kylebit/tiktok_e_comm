"""Validate and optionally freeze a Shopee-category-attribute-only successor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domains.product_operations import build_approved_publication_snapshot
from shared_platform.release_store import default_release_store
from shared_platform.shopee_category_attribute_successor import (
    build_shopee_category_attribute_successor_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    approval = json.loads(args.approval.read_text(encoding="utf-8"))
    store = default_release_store()
    predecessor = store.active_plan_for_product(args.offer_id)
    if predecessor is None:
        raise RuntimeError("active predecessor release plan was not found")
    snapshot = store.approved_publication_snapshot(
        offer_id=args.offer_id, plan_id=predecessor["plan_id"]
    )
    if snapshot is None:
        raise RuntimeError("approved predecessor snapshot was not found")
    candidate = build_shopee_category_attribute_successor_payload(
        predecessor["payload"], predecessor_snapshot=snapshot, approval=approval
    )
    preview = store.preview_plan(candidate)
    validation_time = "2000-01-01T00:00:00+00:00"
    frozen = build_approved_publication_snapshot({
        **preview,
        "status": "APPROVED",
        "approved_at": validation_time,
        "approval": {
            "status": "APPROVED", "approved_by": "Kyle",
            "approved_at": validation_time, "user_approved": True,
            "plan_id": preview["plan_id"], "payload_digest": preview["payload_digest"],
        },
    }).payload()
    result = {
        "schema_version": "shopee-category-attribute-successor-preparation/v1",
        "status": "VALIDATED_NOT_PERSISTED",
        "offer_id": args.offer_id,
        "predecessor_plan_id": predecessor["plan_id"],
        "predecessor_snapshot_digest": snapshot["snapshot_digest"],
        "successor_plan_id": preview["plan_id"],
        "successor_payload_digest": preview["payload_digest"],
        "successor_snapshot_digest": frozen["snapshot_digest"],
        "category_decision": frozen["shopee_global_master"]["category_decision"],
        "platform_writes": 0,
    }
    if args.apply:
        plan = store.create_plan(candidate, supersedes_plan_id=predecessor["plan_id"])
        approval_result = store.approve_plan(
            plan["plan_id"], approved_by="Kyle", user_approved=True,
            confirmation_token=plan["confirmation_token"],
        )
        persisted = store.approved_publication_snapshot(
            offer_id=args.offer_id, plan_id=plan["plan_id"]
        )
        if persisted is None:
            raise RuntimeError("approved successor snapshot was not persisted")
        result.update(
            status="APPROVED_AND_FROZEN", approval=approval_result,
            successor_snapshot_digest=persisted["snapshot_digest"],
            successor_snapshot=persisted,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
