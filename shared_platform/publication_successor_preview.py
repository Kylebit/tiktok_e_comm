"""Read a detached, approval-neutral publication successor for UI review."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMAS = {
    "localized-copy-successor-preview/v1",
    "publication-snapshot-preview/v1",
    "publication-copy-successor-preview/v2",
}
CHANGED_TARGETS = ("shopee:MY", "shopee:TH", "shopee:VN")
COPY_FIELDS = ("title", "description")


def _preview_formal_plan(formal: Mapping[str, Any]) -> dict[str, Any]:
    from shared_platform.release_store import preview_release_plan

    return preview_release_plan(formal)


def _snapshot_for_preview(preview: Mapping[str, Any]) -> dict[str, Any]:
    from shared_platform.publication_autopilot import _business_snapshot_digest
    from domains.product_operations import build_approved_publication_snapshot

    when = "2000-01-01T00:00:00+00:00"
    snapshot = build_approved_publication_snapshot(
        {
            **preview,
            "status": "APPROVED",
            "approved_at": when,
            "approval": {
                "status": "APPROVED",
                "approved_by": "Kyle",
                "approved_at": when,
                "user_approved": True,
                "plan_id": preview["plan_id"],
                "payload_digest": preview["payload_digest"],
            },
        }
    ).payload()
    return snapshot


def _compile_candidate_for_snapshot(
    snapshot: Mapping[str, Any], *, platform_scope: list[str]
) -> dict[str, Any]:
    from shared_platform.publication_autopilot import compile_release_candidate

    return compile_release_candidate(snapshot, platform_scope=platform_scope)


def _business_digest_for_snapshot(snapshot: Mapping[str, Any]) -> str:
    from shared_platform.publication_autopilot import _business_snapshot_digest

    return _business_snapshot_digest(snapshot)


def _validated_v2_formal_binding(
    document: Mapping[str, Any], candidate: Mapping[str, Any],
    predecessor: Mapping[str, Any], changed_fields: Mapping[str, tuple[str, ...]],
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    """Rebuild every approval-neutral identity from the embedded formal plan."""

    formal = document.get("formal_plan")
    if not isinstance(formal, Mapping):
        raise ValueError("v2 successor formal_plan is missing")
    formal = deepcopy(dict(formal))
    supplied_plan_id = str(formal.pop("plan_id", "") or "")
    from shared_platform.publication_autopilot import _canonical_digest

    rebuilt_plan_id = "omnichannel:" + _canonical_digest(formal)
    if supplied_plan_id != rebuilt_plan_id:
        raise ValueError("v2 successor formal_plan identity drifted")
    formal["plan_id"] = supplied_plan_id
    preview = _preview_formal_plan(formal)
    snapshot = _snapshot_for_preview(preview)
    business_digest = _business_digest_for_snapshot(snapshot)
    rebuilt = _compile_candidate_for_snapshot(
        snapshot, platform_scope=list(candidate.get("platform_scope") or ())
    )
    rebuilt.pop("approved_execution_snapshot_digest", None)
    rebuilt.pop("candidate_digest", None)
    rebuilt["candidate_digest"] = _canonical_digest(rebuilt)
    candidate_body = deepcopy(dict(candidate))
    supplied_candidate_digest = str(candidate_body.pop("candidate_digest", "") or "")
    if supplied_candidate_digest != _canonical_digest(candidate_body):
        raise ValueError("v2 successor candidate identity drifted")
    if (
        candidate.get("plan_id") != rebuilt_plan_id
        or candidate.get("offer_id") != formal.get("product_id")
        or candidate.get("target_labels") != formal.get("targets")
        or candidate.get("snapshot_digest") != business_digest
        or candidate.get("business_snapshot_digest") != business_digest
    ):
        raise ValueError("v2 successor rebuilt identity mismatch")
    if dict(candidate) != rebuilt:
        raise ValueError("v2 successor canonical candidate drifted")
    if (
        predecessor.get("candidate_digest") == candidate.get("candidate_digest")
        and predecessor.get("plan_id") == candidate.get("plan_id")
    ):
        return formal, business_digest, rebuilt, rebuilt

    predecessor_formal = deepcopy(formal)
    predecessor_copy = _copy_by_target(predecessor)
    content = predecessor_formal.get("product_facts", {}).get("content_by_target")
    if not isinstance(content, dict):
        raise ValueError("v2 successor formal_plan content is incomplete")
    for target, fields in changed_fields.items():
        if target not in content or target not in predecessor_copy:
            raise ValueError("v2 successor predecessor copy is incomplete")
        for field in fields:
            content[target][field] = predecessor_copy[target][field]
    digests = predecessor_formal.get("digests")
    if not isinstance(digests, dict):
        raise ValueError("v2 successor formal_plan digests are incomplete")
    digests["content"] = "sha256:" + _canonical_digest(content)
    predecessor_formal.pop("plan_id", None)
    predecessor_plan_id = "omnichannel:" + _canonical_digest(predecessor_formal)
    if (
        predecessor_plan_id != document.get("predecessor_plan_id")
        or predecessor_plan_id != predecessor.get("plan_id")
    ):
        raise ValueError("v2 successor predecessor formal identity drifted")
    predecessor_formal["plan_id"] = predecessor_plan_id
    predecessor_preview = _preview_formal_plan(predecessor_formal)
    predecessor_snapshot = _snapshot_for_preview(predecessor_preview)
    rebuilt_predecessor = _compile_candidate_for_snapshot(
        predecessor_snapshot, platform_scope=list(candidate.get("platform_scope") or ())
    )
    return formal, business_digest, rebuilt, rebuilt_predecessor


def _change_contract(
    document: Mapping[str, Any], *, target_labels: list[str]
) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    """Resolve an explicit v2 change set while preserving the legacy Shopee v1."""

    declared = list(
        document.get("changed_target_labels")
        or document.get("changed_targets")
        or ()
    )
    if (
        not declared
        or len(declared) != len(set(declared))
        or any(target not in target_labels for target in declared)
    ):
        raise ValueError("successor changed-target scope is invalid")
    if document.get("schema_version") != "publication-copy-successor-preview/v2":
        if tuple(declared) != CHANGED_TARGETS:
            raise ValueError("successor changed-target scope is invalid")
        return declared, {target: ("description",) for target in declared}

    raw_fields = document.get("changed_copy_fields_by_target")
    if not isinstance(raw_fields, Mapping) or set(raw_fields) != set(declared):
        raise ValueError("successor changed-copy contract is invalid")
    normalized: dict[str, tuple[str, ...]] = {}
    for target in declared:
        values = raw_fields.get(target)
        if (
            not isinstance(values, list)
            or not values
            or len(values) != len(set(values))
            or any(value not in COPY_FIELDS for value in values)
        ):
            raise ValueError("successor changed-copy contract is invalid")
        normalized[target] = tuple(values)
    return declared, normalized


def successor_preview_path(*, offer_id: str, reports_root: Path) -> Path:
    """Prefer the generic v2 sidecar without hiding an existing legacy v1 file."""

    directory = Path(reports_root) / str(offer_id)
    generic = directory / "publication-successor-preview.json"
    return generic if generic.is_file() else directory / "localized-copy-successor-preview.json"


def _copy_by_target(candidate: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    manifest = candidate.get("review_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("successor review manifest is missing")
    for group in manifest.get("copy_sets") or ():
        if not isinstance(group, Mapping):
            raise ValueError("successor copy set is invalid")
        value = {
            "title": str(group.get("title") or ""),
            "description": str(group.get("description") or ""),
        }
        for target in group.get("target_labels") or ():
            if target in result:
                raise ValueError("successor target has duplicate copy")
            result[str(target)] = value
    return result


def _without_copy_reference(row: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(row))
    value.pop("copy_set_id", None)
    return value


def validate_successor_preview(
    document: Mapping[str, Any],
    *,
    offer_id: str,
    predecessor: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the safe candidate projection or fail closed on scope/fact drift."""

    if not isinstance(document, Mapping) or document.get("schema_version") not in SCHEMAS:
        raise ValueError("successor preview schema is invalid")
    if str(document.get("offer_id") or "") != str(offer_id):
        raise ValueError("successor preview offer identity drifted")
    if document.get("status") != "NOT_APPROVED":
        raise ValueError("successor preview must remain NOT_APPROVED")
    if document.get("approval") is not None or document.get("approval_digest") is not None:
        raise ValueError("successor preview must not contain approval authority")

    candidate = document.get("candidate") or document.get("release_candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("successor release candidate is missing")
    candidate = deepcopy(dict(candidate))
    if str(candidate.get("offer_id") or "") != str(offer_id):
        raise ValueError("successor candidate offer identity drifted")
    if candidate.get("status") not in {"PREVIEW_READY", "READY_FOR_FINAL_REVIEW"}:
        raise ValueError("successor candidate is not ready for review")
    if not candidate.get("candidate_digest") or not candidate.get("snapshot_digest"):
        raise ValueError("successor candidate identity is incomplete")
    predecessor_digest = document.get("predecessor_candidate_digest")
    predecessor_plan = document.get("predecessor_plan_id")
    if predecessor_digest is not None:
        if predecessor_digest != predecessor.get("candidate_digest"):
            raise ValueError("successor predecessor identity drifted")
    elif predecessor_plan != predecessor.get("plan_id"):
        raise ValueError("successor predecessor identity drifted")

    targets = candidate.get("target_labels")
    predecessor_targets = predecessor.get("target_labels")
    if not isinstance(targets, list) or targets != predecessor_targets or len(targets) != 15:
        raise ValueError("successor must retain the complete ordered 15-target scope")
    declared, changed_fields = _change_contract(
        document, target_labels=targets
    )

    rebuilt_candidate = None
    rebuilt_predecessor = None
    if document.get("schema_version") == "publication-copy-successor-preview/v2":
        _, _, rebuilt_candidate, rebuilt_predecessor = _validated_v2_formal_binding(
            document, candidate, predecessor, changed_fields
        )

    old_manifest = (rebuilt_predecessor or predecessor).get("review_manifest")
    new_manifest = (rebuilt_candidate or candidate).get("review_manifest")
    if not isinstance(old_manifest, Mapping) or not isinstance(new_manifest, Mapping):
        raise ValueError("successor comparison manifest is missing")
    old_rows = old_manifest.get("targets") or []
    new_rows = new_manifest.get("targets") or []
    if [row.get("target_label") for row in new_rows] != targets:
        raise ValueError("successor review target order drifted")
    if [_without_copy_reference(row) for row in new_rows] != [
        _without_copy_reference(row) for row in old_rows
    ]:
        raise ValueError("successor changed a non-copy target fact")
    for key in ("variants", "image_sets"):
        if new_manifest.get(key) != old_manifest.get(key):
            raise ValueError(f"successor changed frozen {key}")
    for key in ("write_budget", "platform_scope"):
        if candidate.get(key) != predecessor.get(key):
            raise ValueError(f"successor changed frozen {key}")

    before = _copy_by_target(predecessor)
    after = _copy_by_target(candidate)
    if set(before) != set(targets) or set(after) != set(targets):
        raise ValueError("successor copy coverage is incomplete")
    for target in targets:
        expected_fields = set(changed_fields.get(target, ()))
        for field in COPY_FIELDS:
            changed = after[target][field] != before[target][field]
            if changed != (field in expected_fields):
                if (
                    document.get("schema_version")
                    != "publication-copy-successor-preview/v2"
                    and field == "title"
                    and changed
                ):
                    raise ValueError("successor changed a frozen title")
                raise ValueError("successor copy change set drifted")

    quality = candidate.get("durable_quality_evidence")
    quality_status = quality.get("status") if isinstance(quality, Mapping) else None
    if document.get("schema_version") == "publication-copy-successor-preview/v2":
        automated = candidate.get("automated_quality_gate")
        automated_status = (
            automated.get("status") if isinstance(automated, Mapping) else None
        )
        zero_write = candidate.get("zero_write_simulation")
        ready = (
            candidate.get("blockers") == []
            and automated_status == "PASSED"
            and zero_write
            == {"blocker_count": 0, "completed": True, "external_write_count": 0}
        )
    else:
        ready = quality_status == "PASSED"
    candidate["status"] = "NOT_APPROVED"
    candidate["changed_target_labels"] = declared
    candidate["changed_copy_fields_by_target"] = {
        target: list(changed_fields[target]) for target in declared
    }
    candidate["predecessor_candidate_digest"] = predecessor.get("candidate_digest")
    candidate["review_blocked"] = not ready
    candidate["review_blockers"] = [] if ready else [
        (
            "SUCCESSOR_PREFLIGHT_NOT_PASSED"
            if document.get("schema_version") == "publication-copy-successor-preview/v2"
            else "DURABLE_QUALITY_EVIDENCE_NOT_PASSED"
        )
    ]
    return candidate


def load_successor_preview(
    *, offer_id: str, predecessor: Mapping[str, Any], reports_root: Path,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    path = successor_preview_path(offer_id=offer_id, reports_root=reports_root)
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    successor = document.get("candidate") or document.get("release_candidate")
    # Once the reviewed successor is the active approved authority, the retained
    # sidecar is audit history rather than another pending successor.  Match all
    # immutable execution identities before returning the authoritative active
    # candidate; every partial or drifted match still follows the fail-closed
    # predecessor validation below.
    active_identity_matches = (
        isinstance(successor, Mapping)
        and document.get("schema_version") in SCHEMAS
        and str(document.get("offer_id") or "") == str(offer_id)
        and document.get("status") == "NOT_APPROVED"
        and document.get("approval") is None
        and document.get("approval_digest") is None
        and bool(document.get("changed_target_labels") or document.get("changed_targets"))
        and predecessor.get("status") == "READY_FOR_FINAL_REVIEW"
        and successor.get("offer_id") == predecessor.get("offer_id")
        and successor.get("plan_id") == predecessor.get("plan_id")
        and successor.get("candidate_digest") == predecessor.get("candidate_digest")
        and successor.get("snapshot_digest") == predecessor.get("snapshot_digest")
        and successor.get("target_labels") == predecessor.get("target_labels")
    )
    if active_identity_matches:
        if document.get("schema_version") == "publication-copy-successor-preview/v2":
            _, changed_fields = _change_contract(
                document, target_labels=list(predecessor.get("target_labels") or ())
            )
            _validated_v2_formal_binding(
                document, successor, predecessor, changed_fields
            )
        declared, changed_fields = _change_contract(
            document, target_labels=list(predecessor.get("target_labels") or ())
        )
        adopted = deepcopy(dict(predecessor))
        adopted["changed_target_labels"] = declared
        adopted["changed_copy_fields_by_target"] = {
            target: list(changed_fields[target]) for target in declared
        }
        adopted["predecessor_candidate_digest"] = document.get(
            "predecessor_candidate_digest"
        )
        if document.get("schema_version") == "publication-copy-successor-preview/v2":
            from shared_platform.publication_autopilot import (
                load_final_approval_receipt,
            )

            try:
                if snapshot is None:
                    raise ValueError("actual execution snapshot is required")
                load_final_approval_receipt(
                    adopted,
                    reports_root=reports_root,
                    snapshot=snapshot,
                )
            except (ValueError, TypeError, OSError):
                adopted["status"] = "AWAITING_APPROVAL"
                adopted["successor_adopted"] = False
                adopted["review_blocked"] = True
                adopted["review_blockers"] = ["EXECUTION_BINDING_REQUIRED"]
                return adopted
        adopted["status"] = "APPROVED"
        adopted["successor_adopted"] = True
        adopted["review_blocked"] = False
        adopted["review_blockers"] = []
        return adopted
    return validate_successor_preview(
        document, offer_id=offer_id, predecessor=predecessor
    )


__all__ = [
    "_validated_v2_formal_binding",
    "load_successor_preview",
    "successor_preview_path",
    "validate_successor_preview",
]
