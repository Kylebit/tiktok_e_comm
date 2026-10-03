"""Fresh GET-only authority for continuing a known-zero Shopee recovery."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping, Sequence

from shared_platform.shopee_known_zero_recovery import validate_known_zero_receipt
from shared_platform.shopee_recovery_reconciliations import (
    ShopeeRecoveryReconciliationError,
    _validate_direct_readback,
)
from shared_platform.shopee_reportless_recovery_reconciliations import _load_ref, _safe_root
from shared_platform.operations_domain_guard import _publication_operation_id


PREFLIGHT_SCHEMA_VERSION = "shopee-known-zero-recovery-continuation-preflight/v1"
MANIFEST_SCHEMA_VERSION = "shopee-known-zero-recovery-execution-manifest/v1"


class ShopeeKnownZeroContinuationError(ValueError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read_operation(database: Path, operation_id: str) -> tuple[dict[str, Any], int]:
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM workbench_domain_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        locks = connection.execute(
            "SELECT COUNT(*) FROM workbench_domain_locks WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    if row is None:
        raise ShopeeKnownZeroContinuationError("known-zero domain operation is unavailable")
    return dict(row), int(locks)


def _strict_targets(manifest: Mapping[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    labels = manifest.get("target_labels")
    rows = manifest.get("targets")
    if (
        not isinstance(labels, list)
        or not labels
        or any(type(label) is not str or not label for label in labels)
        or len(labels) != len(set(labels))
        or not isinstance(rows, list)
        or len(rows) != len(labels)
        or [row.get("target_label") for row in rows if isinstance(row, Mapping)] != labels
    ):
        raise ShopeeKnownZeroContinuationError("known-zero target scope is invalid")
    return list(labels), {label: dict(row) for label, row in zip(labels, rows)}


def build_known_zero_continuation_preflight(
    *,
    receipt_ref: Mapping[str, str],
    successor_ref: Mapping[str, str],
    manifest_ref: Mapping[str, str],
    fresh_readback_ref: Mapping[str, str],
    fresh_authority_ref: Mapping[str, str],
    run_store: object,
    report_store: object,
    manifest_validator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    operations_db: str | Path,
    allowed_evidence_roots: Sequence[str | Path],
    evidence_resolver=None,
) -> dict[str, Any]:
    """Authorize only an unchanged exact remaining diff after known-zero closure."""
    try:
        receipt, safe_receipt = _load_ref(
            receipt_ref, allowed_evidence_roots, evidence_resolver
        )
        successor, safe_successor = _load_ref(
            successor_ref, allowed_evidence_roots, evidence_resolver
        )
        manifest, safe_manifest = _load_ref(
            manifest_ref, allowed_evidence_roots, evidence_resolver
        )
        readback, safe_readback = _load_ref(
            fresh_readback_ref, allowed_evidence_roots, evidence_resolver
        )
        authority, safe_authority = _load_ref(
            fresh_authority_ref, allowed_evidence_roots, evidence_resolver
        )
        checked_manifest = manifest_validator(manifest)
        checked_receipt = validate_known_zero_receipt(
            receipt,
            run_store=run_store,
            report_store=report_store,
            manifest_validator=manifest_validator,
        )
    except (ShopeeRecoveryReconciliationError, KeyError, TypeError, ValueError) as error:
        raise ShopeeKnownZeroContinuationError(
            "known-zero continuation authority is invalid"
        ) from error
    if checked_manifest != manifest or checked_receipt.get("manifest") != manifest:
        raise ShopeeKnownZeroContinuationError("known-zero manifest authority conflicts")
    if successor != checked_receipt.get("new_manifest"):
        raise ShopeeKnownZeroContinuationError("known-zero successor conflicts")
    if successor.get("authorized") is not False or successor.get(
        "fresh_exact_preflight_required"
    ) is not True:
        raise ShopeeKnownZeroContinuationError("known-zero successor state conflicts")

    run_id = checked_receipt["run_identity"]["run_id"]
    run = run_store.get_run_by_id(run_id=run_id)
    events = []
    connection = sqlite3.connect(Path(run_store.path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        events = [
            dict(row)
            for row in connection.execute(
                "SELECT sequence,state,final_report_id,failure_code,created_at,event_digest "
                "FROM product_publication_run_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        ]
    finally:
        connection.close()
    labels, targets = _strict_targets(manifest)
    skus = {str(row.get("model_sku") or "") for row in targets.values()}
    if len(skus) != 1 or not next(iter(skus)).isdigit():
        raise ShopeeKnownZeroContinuationError("known-zero SKU scope is invalid")
    expected_resources = [
        f'product:["{label}","{next(iter(skus))}"]' for label in labels
    ]
    expected_operation_id = _publication_operation_id(
        manifest["plan_id"],
        labels,
        retry_attempt={
            "run_id": run_id,
            "retry_of_run_id": manifest["direct_predecessor_run_id"],
            "source_evidence_digest": manifest["preflight_digest"],
        },
        recovery_continuation=manifest["continuation"],
    )
    database = _safe_root(operations_db, allowed_evidence_roots)
    closure = authority.get("closure")
    operation = closure.get("operation") if isinstance(closure, Mapping) else None
    if not isinstance(operation, Mapping):
        raise ShopeeKnownZeroContinuationError("known-zero closure operation is invalid")
    current, lock_count = _read_operation(database, str(operation.get("operation_id") or ""))

    expected_authority_keys = {
        "schema_version",
        "authority_digest",
        "authorized_method",
        "closure",
        "expected_report_id",
        "immutable_report_present",
        "known_zero_receipt",
        "manifest",
        "manifest_digest",
        "observed_at",
        "ordered_events",
        "product_writes",
        "run",
        "unauthorized_successor",
    }
    authority_body = dict(authority)
    authority_digest = authority_body.pop("authority_digest", None)
    expected_receipt_ref = {
        **safe_receipt,
        "digest": checked_receipt["receipt_digest"],
    }
    expected_successor_ref = dict(safe_successor)
    if (
        set(authority) != expected_authority_keys
        or authority.get("schema_version") != "shopee-get-only-collector-authority/v1"
        or authority_digest != _digest(authority_body)
        or authority.get("authorized_method") != "GET"
        or authority.get("known_zero_receipt") != expected_receipt_ref
        or authority.get("unauthorized_successor") != expected_successor_ref
        or authority.get("manifest") != safe_manifest
        or authority.get("manifest_digest") != manifest["manifest_digest"]
        or authority.get("run") != dict(run)
        or authority.get("ordered_events") != events
        or authority.get("expected_report_id") != checked_receipt["run_identity"]["report_id"]
        or authority.get("immutable_report_present") is not True
        or authority.get("product_writes") != 0
        or authority.get("observed_at") != readback.get("observed_at")
        or not isinstance(closure, Mapping)
        or set(closure) != {"lock_count", "operation", "strict_file_mtime_gate"}
        or current != dict(operation)
        or operation.get("operation_id") != expected_operation_id
        or closure.get("lock_count") != 0
        or lock_count != 0
        or operation.get("state") != "completed"
        or json.loads(str(operation.get("resources_json") or "null"))
        != expected_resources
        or operation.get("readback_ref")
        != "shopee-known-zero-recovery:"
        + checked_receipt["receipt_digest"].removeprefix("sha256:")
    ):
        raise ShopeeKnownZeroContinuationError("known-zero collector authority conflicts")

    closure_time = str(closure.get("strict_file_mtime_gate") or "")
    receipt_mtime = datetime.fromtimestamp(
        Path(safe_receipt["path"]).stat().st_mtime, timezone.utc
    ).isoformat()
    expected_readback_keys = {
        "schema_version", "evidence_digest", "authority", "run_id", "report_id",
        "manifest_digest", "product_writes", "observed_at", "targets",
        "known_zero_receipt_digest", "unauthorized_successor_sha256",
        "closure_observed_at",
    }
    if (
        set(readback) != expected_readback_keys
        or
        closure_time != receipt_mtime
        or
        readback.get("known_zero_receipt_digest") != checked_receipt["receipt_digest"]
        or readback.get("unauthorized_successor_sha256") != safe_successor["sha256"]
        or readback.get("closure_observed_at") != closure_time
    ):
        raise ShopeeKnownZeroContinuationError("known-zero readback lineage conflicts")
    try:
        normalized = _validate_direct_readback(
            readback,
            identity={
                "run_id": run_id,
                "report_id": checked_receipt["run_identity"]["report_id"],
                "ambiguity_at": closure_time,
            },
            manifest=manifest,
            labels=labels,
            manifest_targets=targets,
            write_counts=[
                {"target_label": label, "outcome_unknown": True} for label in labels
            ],
            allowed_evidence_roots=allowed_evidence_roots,
            allow_unobservable_global_model_status=True,
            evidence_resolver=evidence_resolver,
        )
    except ShopeeRecoveryReconciliationError as error:
        raise ShopeeKnownZeroContinuationError(
            "known-zero official readback conflicts"
        ) from error
    observed = datetime.fromisoformat(str(readback["observed_at"]).replace("Z", "+00:00"))
    if observed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ShopeeKnownZeroContinuationError("known-zero readback is future-dated")
    remaining = [
        {"target_label": row["target_label"], "differences": row["differences"]}
        for row in normalized
    ]
    if (
        remaining != successor.get("exact_remaining_differences")
        or any(row["classification"] != "DIFF" for row in normalized)
        or successor.get("target_scope") != labels
    ):
        raise ShopeeKnownZeroContinuationError("known-zero remaining differences drifted")
    allowed_actions = successor.get("action_budgets")
    expected_actions = {
        label: ["update_copy_and_description_media", "list_existing_item"]
        for label in labels
    }
    if allowed_actions != expected_actions:
        raise ShopeeKnownZeroContinuationError("known-zero action scope conflicts")

    core = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": "READY_ZERO_WRITE_PREFLIGHT",
        "authorized": True,
        "authorization_basis": "ORIGINAL_EXACT_HUMAN_APPROVAL_REUSE",
        "offer_id": manifest["offer_id"],
        "plan_id": manifest["plan_id"],
        "execution_snapshot_digest": manifest["execution_snapshot_digest"],
        "candidate_digest": manifest["candidate_digest"],
        "approval_digest": manifest["approval_digest"],
        "direct_predecessor_run_id": run_id,
        "source_known_zero_receipt_digest": checked_receipt["receipt_digest"],
        "source_execution_manifest_digest": manifest["manifest_digest"],
        "target_scope": list(labels),
        "exact_remaining_differences": remaining,
        "allowed_actions": expected_actions,
        "mutation_budgets": {
            label: {
                "shared_maximum": 0,
                "target_maximum": 2,
                "maximum_confirmed_writes": 2,
            }
            for label in labels
        },
        "forbidden_actions": list(manifest["forbidden_operations"]),
        "zero_write_preflight": {
            "completed": True,
            "external_write_count": 0,
            "observed_at": readback["observed_at"],
        },
        "fresh_official_readback": {
            "evidence_digest": readback["evidence_digest"],
            "targets": normalized,
        },
        "collector_authority_digest": authority_digest,
        "source_refs": {
            "known_zero_receipt": safe_receipt,
            "unauthorized_successor": safe_successor,
            "source_execution_manifest": safe_manifest,
            "fresh_readback": safe_readback,
            "collector_authority": safe_authority,
        },
    }
    return {**core, "preflight_digest": _digest(core)}


def validate_known_zero_continuation_preflight(
    value: Mapping[str, Any], **kwargs: Any
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShopeeKnownZeroContinuationError("known-zero preflight is invalid")
    checked = deepcopy(dict(value))
    supplied = checked.pop("preflight_digest", None)
    if supplied != _digest(checked) or checked.get("schema_version") != PREFLIGHT_SCHEMA_VERSION:
        raise ShopeeKnownZeroContinuationError("known-zero preflight digest conflicts")
    refs = checked.get("source_refs")
    if not isinstance(refs, Mapping) or set(refs) != {
        "known_zero_receipt",
        "unauthorized_successor",
        "source_execution_manifest",
        "fresh_readback",
        "collector_authority",
    }:
        raise ShopeeKnownZeroContinuationError("known-zero preflight sources conflict")
    rebuilt = build_known_zero_continuation_preflight(
        receipt_ref=refs["known_zero_receipt"],
        successor_ref=refs["unauthorized_successor"],
        manifest_ref=refs["source_execution_manifest"],
        fresh_readback_ref=refs["fresh_readback"],
        fresh_authority_ref=refs["collector_authority"],
        **kwargs,
    )
    result = {**checked, "preflight_digest": supplied}
    if rebuilt != result:
        raise ShopeeKnownZeroContinuationError("known-zero preflight semantics conflict")
    return result


def build_known_zero_continuation_manifest(
    *,
    preflight_ref: Mapping[str, str],
    original_manifest_ref: Mapping[str, str],
    evidence_relocation_ref: Mapping[str, str],
    allowed_relocation_root: str | Path,
    **validation: Any,
) -> dict[str, Any]:
    preflight, safe_preflight = _load_ref(
        preflight_ref, validation["allowed_evidence_roots"]
    )
    original, safe_original = _load_ref(
        original_manifest_ref, validation["allowed_evidence_roots"]
    )
    if (
        safe_original != preflight.get("source_refs", {}).get(
            "source_execution_manifest"
        )
        or original.get("manifest_digest")
        != preflight.get("source_execution_manifest_digest")
    ):
        raise ShopeeKnownZeroContinuationError(
            "known-zero source execution manifest conflicts"
        )
    from shared_platform.evidence_relocation_attestations import (
        EvidenceRelocationError,
        EvidenceRelocationResolver,
        validate_evidence_relocation_attestation,
    )
    attestation, safe_attestation = _load_ref(
        evidence_relocation_ref, validation["allowed_evidence_roots"]
    )
    expected_authorities = {
        "known_zero_receipt": preflight["source_known_zero_receipt_digest"],
        "source_execution_manifest": preflight["source_execution_manifest_digest"],
        "fresh_readback": preflight["fresh_official_readback"]["evidence_digest"],
        "collector_authority": preflight["collector_authority_digest"],
        "unauthorized_successor": preflight["source_refs"]["unauthorized_successor"]["sha256"],
    }
    try:
        provisional = EvidenceRelocationResolver(
            attestation,
            allowed_target_root=allowed_relocation_root,
            expected_authority_digests=expected_authorities,
        )
        top, _ = provisional.load(
            preflight["source_refs"]["fresh_readback"],
            allowed_roots=validation["allowed_evidence_roots"],
        )
        logical = [
            preflight["source_refs"]["fresh_readback"],
            preflight["source_refs"]["collector_authority"],
        ]
        for target in top.get("targets") or []:
            reads = target.get("reads") if isinstance(target, Mapping) else None
            if not isinstance(reads, Mapping):
                raise EvidenceRelocationError("known-zero relocated target is invalid")
            for name in ("item", "models", "global_linkage", "global_item", "global_models"):
                meta = reads.get(name)
                if not isinstance(meta, Mapping):
                    raise EvidenceRelocationError("known-zero relocated raw ref is invalid")
                logical.append(
                    {"path": meta.get("response_ref"), "sha256": meta.get("response_digest")}
                )
        by_key = {
            (entry["original"]["path"], entry["original"]["sha256"]): entry["original"]
            for entry in attestation.get("entries") or []
            if isinstance(entry, Mapping)
        }
        keys = [(str(meta.get("path")), str(meta.get("sha256"))) for meta in logical]
        if len(keys) != 17 or len(set(keys)) != 17 or set(keys) != set(by_key):
            raise EvidenceRelocationError("known-zero relocation closure is not exact")
        required = [by_key[key] for key in keys]
        validate_evidence_relocation_attestation(
            attestation,
            allowed_target_root=allowed_relocation_root,
            expected_authority_digests=expected_authorities,
            required_original_refs=required,
        )
        resolver = EvidenceRelocationResolver(
            attestation,
            allowed_target_root=allowed_relocation_root,
            expected_authority_digests=expected_authorities,
            required_original_refs=required,
        )
    except EvidenceRelocationError as error:
        raise ShopeeKnownZeroContinuationError(
            "known-zero evidence relocation conflicts"
        ) from error
    checked = validate_known_zero_continuation_preflight(
        preflight, **{**validation, "evidence_resolver": resolver}
    )
    labels = checked["target_scope"]
    targets = {
        row["target_label"]: deepcopy(row) for row in original.get("targets") or []
    }
    if list(targets) != labels:
        raise ShopeeKnownZeroContinuationError("known-zero target facts are incomplete")
    rows = []
    for label in labels:
        row = targets[label]
        row["allowed_actions"] = list(checked["allowed_actions"][label])
        row["mutation_budget"] = {"shared_maximum": 0, "target_maximum": 2}
        rows.append(row)
    continuation = {
        "schema_version": "shopee-recovery-continuation/v1",
        "receipt_digest": checked["preflight_digest"],
        "target_scope": list(labels),
        "exact_remaining_differences": deepcopy(checked["exact_remaining_differences"]),
        "action_budgets": deepcopy(checked["allowed_actions"]),
    }
    core = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "status": "READY_ZERO_WRITE_PREFLIGHT",
        "authorized": True,
        "offer_id": checked["offer_id"],
        "product_revision": original["product_revision"],
        "plan_id": checked["plan_id"],
        "business_snapshot_digest": original["business_snapshot_digest"],
        "execution_snapshot_digest": checked["execution_snapshot_digest"],
        "candidate_digest": checked["candidate_digest"],
        "approval_digest": checked["approval_digest"],
        "direct_predecessor_run_id": checked["direct_predecessor_run_id"],
        "original_publication_run_id": original["original_publication_run_id"],
        "source_receipt_digest": checked["source_known_zero_receipt_digest"],
        "source_execution_manifest_digest": checked[
            "source_execution_manifest_digest"
        ],
        "preflight_digest": checked["preflight_digest"],
        "evidence_relocation_digest": attestation["attestation_digest"],
        "target_labels": list(labels),
        "targets": rows,
        "continuation": continuation,
        "forbidden_operations": deepcopy(checked["forbidden_actions"]),
        "zero_write_preflight": deepcopy(checked["zero_write_preflight"]),
        "source_refs": {
            "preflight": safe_preflight,
            "original_manifest": safe_original,
            "evidence_relocation": safe_attestation,
        },
    }
    return {**core, "manifest_digest": _digest(core)}


def validate_known_zero_continuation_manifest(
    value: Mapping[str, Any], *, allowed_relocation_root: str | Path, **validation: Any
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShopeeKnownZeroContinuationError("known-zero execution manifest is invalid")
    checked = deepcopy(dict(value))
    supplied = checked.pop("manifest_digest", None)
    if supplied != _digest(checked) or checked.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ShopeeKnownZeroContinuationError("known-zero execution manifest digest conflicts")
    refs = checked.get("source_refs")
    if not isinstance(refs, Mapping) or set(refs) != {
        "preflight", "original_manifest", "evidence_relocation"
    }:
        raise ShopeeKnownZeroContinuationError("known-zero execution sources conflict")
    rebuilt = build_known_zero_continuation_manifest(
        preflight_ref=refs["preflight"],
        original_manifest_ref=refs["original_manifest"],
        evidence_relocation_ref=refs["evidence_relocation"],
        allowed_relocation_root=allowed_relocation_root,
        **validation,
    )
    result = {**checked, "manifest_digest": supplied}
    if rebuilt != result:
        raise ShopeeKnownZeroContinuationError("known-zero execution semantics conflict")
    return result


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "PREFLIGHT_SCHEMA_VERSION",
    "ShopeeKnownZeroContinuationError",
    "build_known_zero_continuation_manifest",
    "build_known_zero_continuation_preflight",
    "validate_known_zero_continuation_preflight",
    "validate_known_zero_continuation_manifest",
]
