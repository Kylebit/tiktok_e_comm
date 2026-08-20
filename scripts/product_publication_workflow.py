#!/usr/bin/env python3
"""Read one offer's durable preparation state and emit its sole next command.

This command is takeover-safe and read-only. It never initializes a workflow,
calls a provider, mutates Product Center, or repairs missing state implicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _text(value: object) -> str:
    return str(value or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {"_invalid": True}
    return dict(payload) if isinstance(payload, Mapping) else {"_invalid": True}


def _command(script: str, offer_id: str, flags: str = "") -> str:
    suffix = f" {flags.strip()}" if flags.strip() else ""
    return f".venv\\Scripts\\python.exe {script} --offer-id {offer_id}{suffix}"


def classify_workflow(
    *,
    offer_id: str,
    first_review: Mapping[str, Any] | None = None,
    localized: Mapping[str, Any] | None = None,
    handoff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify already-persisted facts without reading or writing external state."""

    clean_offer = _text(offer_id)
    first = dict(first_review or {})
    local = dict(localized or {})
    transfer = dict(handoff or {})
    review = local.get("review") if isinstance(local.get("review"), Mapping) else {}
    tasks = [row for row in (review.get("tasks") or []) if isinstance(row, Mapping)]
    statuses = [_text(row.get("status")) for row in tasks]
    task_counts = {
        status: statuses.count(status)
        for status in sorted(set(statuses))
        if status
    }
    approval_recorded = bool(review.get("approval_intent") or review.get("approval"))
    sync = (
        review.get("miaoshou_pre_review_sync")
        if isinstance(review.get("miaoshou_pre_review_sync"), Mapping)
        else {}
    )
    miaoshou_verified = bool(
        sync.get("status") == "VERIFIED"
        and sync.get("written_to_miaoshou") is True
        and sync.get("verified") is True
        and int(sync.get("external_write_count") or 0) == 1
    )
    first_valid = bool(
        first.get("schema") == "publication-preparation-decision/v1"
        and _text(first.get("offer_id")) == clean_offer
        and first.get("status") == "FIRST_REVIEW_READY"
    )
    handoff_valid = bool(
        transfer.get("schema_version") == "product-publication-workflow-handoff/v1"
        and _text(transfer.get("offer_id")) == clean_offer
        and transfer.get("status") == "READY_TO_PUBLISH"
        and _text(transfer.get("plan_id"))
        and _text(transfer.get("snapshot_digest")).startswith("sha256:")
    )
    invalid_state = bool(
        first.get("_invalid")
        or local.get("_invalid")
        or transfer.get("_invalid")
        or (transfer and not handoff_valid)
        or any(status in {"FAILED", "UNKNOWN", "OUTCOME_UNKNOWN"} for status in statuses)
    )

    if invalid_state:
        stage = "RECONCILIATION_REQUIRED"
        next_command = _command(
            "scripts\\product_publication_workflow.py", clean_offer
        )
    elif handoff_valid:
        stage = "READY_TO_PUBLISH"
        next_command = _command(
            "skills\\publish-approved-product\\scripts\\product_center_publication.py",
            clean_offer,
            f"--plan-id {_text(transfer.get('plan_id'))} --platform all --execute",
        )
    elif not first:
        stage = "FIRST_ROUND_REQUIRED"
        next_command = _command(
            "skills\\prepare-product-publication\\scripts\\prepare_product_publication.py",
            clean_offer,
            "--targets <EXACT_TARGETS>",
        )
    elif not first_valid:
        stage = "FIRST_REVIEW_REQUIRED"
        next_command = _command(
            "skills\\prepare-product-publication\\scripts\\prepare_product_publication.py",
            clean_offer,
            "--targets <EXACT_TARGETS>",
        )
    elif not local.get("initialized"):
        stage = "SECOND_ROUND_REQUIRED"
        next_command = _command(
            "skills\\prepare-product-images\\scripts\\prepare_product_images.py",
            clean_offer,
        )
    elif any(status in {"PENDING_GENERATION", "RETRY_REQUESTED", "GENERATING"} for status in statuses):
        stage = "IMAGE_GENERATION_REQUIRED"
        next_command = _command(
            "skills\\prepare-product-images\\scripts\\prepare_product_images.py",
            clean_offer,
            "--execute-paid --confirm-paid-generation",
        )
    elif not miaoshou_verified:
        stage = "MIAOSHOU_SYNC_REQUIRED"
        next_command = _command(
            "skills\\prepare-product-images\\scripts\\prepare_product_images.py",
            clean_offer,
            "--execute-miaoshou --confirm-miaoshou-write",
        )
    elif not approval_recorded:
        stage = "CHAT_APPROVAL_REQUIRED"
        next_command = _command(
            "skills\\prepare-product-images\\scripts\\prepare_product_images.py",
            clean_offer,
            "--approve-all --approved-by Kyle",
        )
    else:
        stage = "LOCALIZED_HANDOFF_REQUIRED"
        next_command = _command(
            "skills\\prepare-product-images\\scripts\\prepare_product_images.py",
            clean_offer,
            "--finalize-release-handoff",
        )

    return {
        "schema_version": "product-publication-workflow-status/v1",
        "offer_id": clean_offer,
        "stage": stage,
        "first_review_revision": int(first.get("product_center_revision") or 0) or None,
        "second_round_revision": int(review.get("revision") or 0) or None,
        "task_counts": task_counts,
        "approval_recorded": approval_recorded,
        "miaoshou_verified": miaoshou_verified,
        "active_plan_id": _text(transfer.get("plan_id")) or None,
        "snapshot_digest": _text(transfer.get("snapshot_digest")) or None,
        "next_command": next_command,
        "requires_reconciliation": stage == "RECONCILIATION_REQUIRED",
    }


def status_offer(offer_id: str) -> dict[str, Any]:
    clean_offer = _text(offer_id)
    if not clean_offer or not clean_offer.isdigit():
        raise ValueError("offer_id must contain digits only")
    report_dir = ROOT / "reports" / "product-preparation" / clean_offer
    first = _read_json(report_dir / "first-review.json")
    handoff = _read_json(report_dir / "workflow-handoff.json")
    try:
        from modules.sourcing.new_product_workbench import localized_image_review_summary

        localized = localized_image_review_summary(clean_offer)
    except (FileNotFoundError, ValueError):
        localized = {}
    return classify_workflow(
        offer_id=clean_offer,
        first_review=first,
        localized=localized,
        handoff=handoff,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offer-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = status_offer(args.offer_id)
    except Exception as error:
        result = {
            "schema_version": "product-publication-workflow-status/v1",
            "offer_id": _text(args.offer_id),
            "stage": "RECONCILIATION_REQUIRED",
            "reason": type(error).__name__,
            "requires_reconciliation": True,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
