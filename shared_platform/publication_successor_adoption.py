"""Adopt one detached, reviewed publication successor without platform calls."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from domains.product_operations import build_approved_publication_snapshot
from shared_platform.publication_autopilot import (
    _canonical_digest,
    build_final_approval_receipt,
    final_approval_receipt_path,
    load_release_candidate,
    persist_final_approval_receipt,
    persist_release_candidate,
    release_candidate_path,
    validate_final_approval_receipt,
    validate_release_candidate_for_execution,
)
from shared_platform.immutable_approval_files import ImmutableFileError, require_local_path
from shared_platform.publication_successor_preview import (
    _validated_v2_formal_binding,
    load_successor_preview,
    successor_preview_path,
)


class PublicationSuccessorAdoptionError(ValueError):
    """The reviewed successor no longer matches its exact approval binding."""


def _require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise PublicationSuccessorAdoptionError(f"{label} drifted")


def validate_successor_adoption_inputs(
    *,
    formal_plan: Mapping[str, Any],
    successor: Mapping[str, Any],
    expected_offer_id: str,
    expected_plan_id: str,
    expected_candidate_digest: str,
    expected_snapshot_digest: str,
    expected_target_labels: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the conversation-bound successor identity before persistence."""

    plan = deepcopy(dict(formal_plan))
    candidate = deepcopy(dict(successor))
    targets = list(expected_target_labels)
    _require_equal(plan.get("product_id"), expected_offer_id, "offer identity")
    _require_equal(plan.get("plan_id"), expected_plan_id, "plan identity")
    _require_equal(plan.get("targets"), targets, "ordered plan targets")
    _require_equal(candidate.get("offer_id"), expected_offer_id, "candidate offer")
    _require_equal(candidate.get("plan_id"), expected_plan_id, "candidate plan")
    _require_equal(
        candidate.get("candidate_digest"),
        expected_candidate_digest,
        "candidate digest",
    )
    _require_equal(
        candidate.get("snapshot_digest"),
        expected_snapshot_digest,
        "business snapshot digest",
    )
    _require_equal(candidate.get("target_labels"), targets, "ordered candidate targets")
    _require_equal(candidate.get("status"), "NOT_APPROVED", "successor status")
    if candidate.get("review_blocked") is not False or candidate.get("review_blockers"):
        raise PublicationSuccessorAdoptionError("successor review is blocked")
    zero = candidate.get("zero_write_simulation") or {}
    if zero != {"blocker_count": 0, "completed": True, "external_write_count": 0}:
        raise PublicationSuccessorAdoptionError("zero-write preflight is not clean")
    return plan, candidate


def adopt_reviewed_successor(
    *,
    release_store,
    reports_root: Path,
    formal_plan_path: Path,
    offer_id: str,
    predecessor_plan_id: str,
    predecessor_candidate_digest: str,
    expected_plan_id: str,
    expected_candidate_digest: str,
    expected_snapshot_digest: str,
    expected_target_labels: Sequence[str],
    approved_by: str,
    user_approved: bool,
) -> dict[str, Any]:
    """Persist the exact successor, v4 snapshot and final approval receipt."""

    if user_approved is not True or approved_by != "Kyle":
        raise PublicationSuccessorAdoptionError("explicit Kyle approval is required")
    predecessor_plan = release_store.get_plan(predecessor_plan_id)
    if not predecessor_plan or predecessor_plan.get("status") not in {"APPROVED", "SUPERSEDED"}:
        raise PublicationSuccessorAdoptionError("active approved predecessor is required")
    if (
        predecessor_plan.get("status") == "SUPERSEDED"
        and predecessor_plan.get("superseded_by_plan_id") != expected_plan_id
    ):
        raise PublicationSuccessorAdoptionError("predecessor belongs to another successor")
    predecessor_candidate = load_release_candidate(
        offer_id,
        predecessor_candidate_digest,
        reports_root=reports_root,
    )
    _require_equal(
        predecessor_candidate.get("offer_id"), offer_id, "predecessor candidate offer"
    )
    _require_equal(
        predecessor_candidate.get("plan_id"),
        predecessor_plan_id,
        "predecessor candidate plan",
    )
    _require_equal(
        predecessor_candidate.get("candidate_digest"),
        predecessor_candidate_digest,
        "predecessor candidate digest",
    )
    successor = load_successor_preview(
        offer_id=offer_id,
        predecessor=predecessor_candidate,
        reports_root=reports_root,
    )
    if successor is None:
        raise PublicationSuccessorAdoptionError("successor sidecar is missing")
    sidecar_path = successor_preview_path(
        offer_id=offer_id, reports_root=reports_root
    )
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    approved_candidate = deepcopy(
        dict(sidecar.get("candidate") or sidecar.get("release_candidate") or {})
    )
    if sidecar.get("schema_version") == "publication-copy-successor-preview/v2":
        changed_fields = {
            target: tuple(fields)
            for target, fields in (successor.get("changed_copy_fields_by_target") or {}).items()
        }
        formal_plan, rebuilt_business_digest, _, _ = _validated_v2_formal_binding(
            sidecar, approved_candidate, predecessor_candidate, changed_fields
        )
        _require_equal(
            rebuilt_business_digest,
            expected_snapshot_digest,
            "rebuilt business snapshot digest",
        )
        supplied_formal = json.loads(Path(formal_plan_path).read_text(encoding="utf-8"))
        if supplied_formal.get("formal_plan") == formal_plan:
            supplied_formal = supplied_formal["formal_plan"]
        _require_equal(supplied_formal, formal_plan, "formal plan sidecar binding")
    else:
        formal_plan = json.loads(Path(formal_plan_path).read_text(encoding="utf-8"))
    formal_plan, successor = validate_successor_adoption_inputs(
        formal_plan=formal_plan,
        successor=successor,
        expected_offer_id=offer_id,
        expected_plan_id=expected_plan_id,
        expected_candidate_digest=expected_candidate_digest,
        expected_snapshot_digest=expected_snapshot_digest,
        expected_target_labels=expected_target_labels,
    )
    _require_equal(
        approved_candidate.get("candidate_digest"),
        expected_candidate_digest,
        "approval candidate digest",
    )
    _require_equal(
        approved_candidate.get("snapshot_digest"),
        expected_snapshot_digest,
        "approval business snapshot digest",
    )
    _require_equal(
        approved_candidate.get("target_labels"),
        list(expected_target_labels),
        "approval ordered targets",
    )
    if approved_candidate.get("status") != "READY_FOR_FINAL_REVIEW":
        raise PublicationSuccessorAdoptionError("approval candidate is not ready")

    # Prove the exact v4 projection and compiled digest before changing SQLite.
    preview = release_store.preview_plan(formal_plan)
    validation_time = "2000-01-01T00:00:00+00:00"
    projected_snapshot = build_approved_publication_snapshot(
        {
            **preview,
            "status": "APPROVED",
            "approved_at": validation_time,
            "approval": {
                "status": "APPROVED",
                "approved_by": "Kyle",
                "approved_at": validation_time,
                "user_approved": True,
                "plan_id": preview["plan_id"],
                "payload_digest": preview["payload_digest"],
            },
        }
    ).payload()
    validate_release_candidate_for_execution(
        approved_candidate,
        snapshot=projected_snapshot,
        platform_scope=approved_candidate["platform_scope"],
        target_labels=list(expected_target_labels),
    )
    candidate = approved_candidate
    candidate_path = release_candidate_path(candidate, reports_root=reports_root)
    receipt_path = final_approval_receipt_path(candidate, reports_root=reports_root)
    candidate_bytes = (
        json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    candidate_info = require_local_path(candidate_path, root=reports_root)
    if candidate_info is not None and candidate_path.read_bytes() != candidate_bytes:
        raise ImmutableFileError("immutable successor candidate file conflicts")
    # Persist the approval-neutral candidate before changing ReleaseStore.  If
    # the later DB transaction or receipt write fails, this file causes the
    # resolver to fail closed instead of falling back to bare v4 authority.
    candidate_path = persist_release_candidate(candidate, reports_root=reports_root)
    receipt_info = require_local_path(receipt_path, root=reports_root)
    if receipt_info is not None:
        existing_plan = release_store.get_plan(expected_plan_id)
        if not existing_plan or existing_plan.get("status") != "APPROVED":
            raise ImmutableFileError(
                "final approval receipt exists without an approved successor plan"
            )
        existing_snapshot = release_store.approved_publication_snapshot(
            offer_id=offer_id, plan_id=expected_plan_id
        )
        existing_receipt = validate_final_approval_receipt(
            json.loads(receipt_path.read_text(encoding="utf-8")),
            candidate,
            snapshot=existing_snapshot,
        )
    adopted = release_store.create_and_approve_reviewed_successor(
        predecessor_plan_id,
        payload=formal_plan,
        approved_by=approved_by,
        user_approved=user_approved,
        expected_business_snapshot_digest=expected_snapshot_digest,
        expected_target_labels=list(expected_target_labels),
        expected_candidate_digest=expected_candidate_digest,
        reviewed_candidate=candidate,
        reviewed_candidate_path=candidate_path,
    )
    plan = adopted["plan"]
    approval = adopted["approval"]
    snapshot = adopted["publication_snapshot"]
    validate_release_candidate_for_execution(
        candidate,
        snapshot=snapshot,
        platform_scope=candidate["platform_scope"],
        target_labels=list(expected_target_labels),
    )
    receipt = build_final_approval_receipt(
        candidate,
        approved_by=approved_by,
        execution_snapshot=snapshot,
    )
    receipt["approved_at"] = approval["approved_at"]
    receipt.pop("approval_digest")
    receipt["approval_digest"] = _canonical_digest(receipt)
    if receipt_info is not None and existing_receipt != receipt:
        raise ImmutableFileError("immutable final approval receipt conflicts")
    receipt_path = persist_final_approval_receipt(
        receipt,
        candidate,
        reports_root=reports_root,
    )
    return {
        "schema_version": "publication-successor-adoption/v1",
        "status": "APPROVED_ZERO_WRITE",
        "plan": plan,
        "approval": approval,
        "publication_snapshot": snapshot,
        "candidate": candidate,
        "final_approval": receipt,
        "candidate_path": str(candidate_path),
        "receipt_path": str(receipt_path),
        "external_write_count": 0,
        "external_writes_performed": [],
    }


__all__ = [
    "PublicationSuccessorAdoptionError",
    "adopt_reviewed_successor",
    "validate_successor_adoption_inputs",
]
