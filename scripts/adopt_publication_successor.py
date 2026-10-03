"""Freeze one exact reviewed successor; never start a platform run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared_platform.publication_successor_adoption import adopt_reviewed_successor  # noqa: E402
from shared_platform.release_store import ReleaseStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-db", required=True, type=Path)
    parser.add_argument("--reports-root", required=True, type=Path)
    parser.add_argument("--formal-plan", required=True, type=Path)
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--predecessor-plan-id", required=True)
    parser.add_argument("--predecessor-candidate-digest", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--candidate-digest", required=True)
    parser.add_argument("--snapshot-digest", required=True)
    parser.add_argument("--target", action="append", dest="targets", required=True)
    parser.add_argument("--approved-by", required=True, choices=("Kyle",))
    parser.add_argument("--confirm-user-approved", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = adopt_reviewed_successor(
        release_store=ReleaseStore(args.release_db),
        reports_root=args.reports_root,
        formal_plan_path=args.formal_plan,
        offer_id=args.offer_id,
        predecessor_plan_id=args.predecessor_plan_id,
        predecessor_candidate_digest=args.predecessor_candidate_digest,
        expected_plan_id=args.plan_id,
        expected_candidate_digest=args.candidate_digest,
        expected_snapshot_digest=args.snapshot_digest,
        expected_target_labels=args.targets,
        approved_by=args.approved_by,
        user_approved=args.confirm_user_approved,
    )
    summary = {
        "schema_version": result["schema_version"],
        "status": result["status"],
        "offer_id": args.offer_id,
        "plan_id": result["plan"]["plan_id"],
        "candidate_digest": result["candidate"]["candidate_digest"],
        "business_snapshot_digest": result["candidate"]["snapshot_digest"],
        "approved_execution_snapshot_digest": result["publication_snapshot"]["snapshot_digest"],
        "approval_digest": result["final_approval"]["approval_digest"],
        "target_labels": result["candidate"]["target_labels"],
        "candidate_path": result["candidate_path"],
        "receipt_path": result["receipt_path"],
        "release_db": str(args.release_db.resolve()),
        "external_write_count": 0,
        "external_writes_performed": [],
        "platform_run_created": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
