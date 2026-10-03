"""Persist one exact final-review packet without provider or marketplace calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared_platform.publication_autopilot import (  # noqa: E402
    compile_release_candidate,
    persist_release_candidate,
)
from shared_platform.release_store import default_release_store  # noqa: E402
from shared_platform.publication_quality_evidence import (  # noqa: E402
    load_publication_quality_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--plan-id", required=True)
    args = parser.parse_args()
    offer_id = str(args.offer_id).strip()
    plan_id = str(args.plan_id).strip()
    if not offer_id.isdigit() or not plan_id:
        raise SystemExit("offer-id and plan-id are required")
    snapshot = default_release_store().approved_publication_snapshot(
        offer_id=offer_id,
        plan_id=plan_id,
        snapshot_digest=None,
    )
    if snapshot is None:
        raise SystemExit("approved publication snapshot is unavailable")
    candidate = compile_release_candidate(
        snapshot,
        durable_evidence=load_publication_quality_evidence(offer_id),
    )
    path = persist_release_candidate(candidate)
    print(
        json.dumps(
            {
                "schema_version": "publication-release-candidate-result/v1",
                "status": candidate["status"],
                "offer_id": candidate["offer_id"],
                "plan_id": candidate["plan_id"],
                "candidate_digest": candidate["candidate_digest"],
                "target_count": len(candidate["target_labels"]),
                "variant_count": candidate["variant_count"],
                "blocker_count": len(candidate["blockers"]),
                "external_write_count": 0,
                "report_path": str(path.relative_to(REPO_ROOT)),
            },
            ensure_ascii=False,
        )
    )
    return 0 if candidate["status"] == "READY_FOR_FINAL_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
