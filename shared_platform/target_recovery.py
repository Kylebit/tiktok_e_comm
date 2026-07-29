"""Channel-neutral release-target recovery classification.

The browser and channel adapters must not invent different recovery states for
MY, VN, GB, or any other site.  This pure projection classifies durable target
facts once.  It never mutates a release run and never authorizes a write by
itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "release-target-recovery-action/v1"

_WRITE_EVIDENCE_KEYS = (
    "external_writes_performed",
    "prior_external_writes_performed",
    "possible_external_writes_performed",
)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _has_write_evidence(target: Mapping[str, Any]) -> bool:
    if str(target.get("external_id") or "").strip():
        return True
    evidence_values = (
        target.get("readback"),
        target.get("submission"),
        target.get("latest_failure_evidence"),
    )
    for raw_evidence in evidence_values:
        evidence = _mapping(raw_evidence)
        nested = _mapping(evidence.get("evidence"))
        for candidate in (evidence, nested):
            if candidate.get("submission_accepted") is True:
                return True
            if any(candidate.get(key) for key in _WRITE_EVIDENCE_KEYS):
                return True
    for event in target.get("failure_events") or ():
        if not isinstance(event, Mapping):
            continue
        evidence = _mapping(event.get("evidence"))
        if evidence.get("submission_accepted") is True:
            return True
        if any(evidence.get(key) for key in _WRITE_EVIDENCE_KEYS):
            return True
    return False


def classify_target_recovery(
    target: Mapping[str, Any],
    *,
    safe_retry_eligible: bool = False,
    predecessor_target: Mapping[str, Any] | None = None,
    predecessor_recovery_eligible: bool = False,
    first_attempt_eligible: bool = True,
) -> dict[str, Any]:
    """Return one redacted recovery action from durable target facts."""

    label = str(target.get("target_label") or "").strip()
    status = str(target.get("status") or "").strip().upper()
    attempts = target.get("attempts")
    attempts_exact = (
        attempts if type(attempts) is int and attempts >= 0 else None
    )
    has_write_evidence = _has_write_evidence(target)
    predecessor = _mapping(predecessor_target)
    predecessor_write_evidence = _has_write_evidence(predecessor)
    predecessor_status = str(
        predecessor.get("status") or ""
    ).strip().upper()

    action_kind = "BLOCKED"
    runnable = False
    reason_code = "target_state_requires_review"
    if status in {"SUCCEEDED", "MANUALLY_VERIFIED"}:
        action_kind = "TERMINAL"
        reason_code = "target_already_complete"
    elif status == "SUBMITTED_UNVERIFIED":
        action_kind = "MANUAL_ACCEPT"
        reason_code = "submission_requires_manual_acceptance"
    elif status == "PENDING":
        if (
            attempts_exact == 0
            and not has_write_evidence
            and not predecessor_write_evidence
        ):
            if first_attempt_eligible:
                action_kind = "FIRST_ATTEMPT"
                runnable = True
                reason_code = "pristine_target_ready"
            else:
                action_kind = "BLOCKED_CAPABILITY"
                reason_code = (
                    "automatic_first_attempt_capability_unavailable"
                )
        elif predecessor_write_evidence:
            if predecessor_recovery_eligible:
                action_kind = "GOVERNED_RECOVERY"
                runnable = True
                reason_code = (
                    "official_readback_then_bounded_write_recovery"
                )
            else:
                action_kind = "READONLY_RECONCILE"
                reason_code = (
                    "predecessor_external_outcome_requires_resolution"
                )
        else:
            reason_code = "pending_target_has_prior_execution_evidence"
    elif status == "RUNNING":
        action_kind = "READONLY_RECONCILE"
        reason_code = "interrupted_or_inflight_execution"
    elif status in {
        "RECONCILIATION_REQUIRED",
        "DRAFT_VERIFICATION_REQUIRED",
    } or has_write_evidence:
        action_kind = "READONLY_RECONCILE"
        reason_code = "external_outcome_requires_readback"
    elif status in {"FAILED", "DRAFT_VERSION_CONFLICT"}:
        if safe_retry_eligible:
            action_kind = "SAFE_RETRY"
            runnable = True
            reason_code = "exact_zero_write_pre_submit_failure"
        else:
            action_kind = "SAFE_REPAIR"
            reason_code = "bounded_repair_proof_required"
    elif not status:
        reason_code = "target_status_missing"

    return {
        "schema_version": SCHEMA_VERSION,
        "target_label": label,
        "action_kind": action_kind,
        "runnable": runnable,
        "reason_code": reason_code,
        "status": status,
        "attempts": attempts_exact,
        "prior_write_evidence": bool(
            has_write_evidence or predecessor_write_evidence
        ),
        "predecessor_status": predecessor_status or None,
    }


def project_run_recovery_actions(
    targets: object,
    *,
    safe_retry_labels: set[str] | frozenset[str] = frozenset(),
    predecessor_recovery_labels: set[str] | frozenset[str] = frozenset(),
    first_attempt_blocked_labels: set[str] | frozenset[str] = frozenset(),
    predecessor_targets: object = (),
) -> list[dict[str, Any]]:
    """Project every durable target without channel or site allowlists."""

    if not isinstance(targets, (list, tuple)):
        return []
    predecessor_by_label = {
        str(target.get("target_label") or ""): target
        for target in (
            predecessor_targets
            if isinstance(predecessor_targets, (list, tuple))
            else ()
        )
        if isinstance(target, Mapping)
        and str(target.get("target_label") or "")
    }
    return [
        classify_target_recovery(
            target,
            safe_retry_eligible=(
                str(target.get("target_label") or "") in safe_retry_labels
            ),
            predecessor_target=predecessor_by_label.get(
                str(target.get("target_label") or "")
            ),
            predecessor_recovery_eligible=(
                str(target.get("target_label") or "")
                in predecessor_recovery_labels
            ),
            first_attempt_eligible=(
                str(target.get("target_label") or "")
                not in first_attempt_blocked_labels
            ),
        )
        for target in targets
        if isinstance(target, Mapping)
        and str(target.get("target_label") or "") != "miaoshou:COMMON"
    ]
