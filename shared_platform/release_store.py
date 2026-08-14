"""Durable control-plane state for governed multi-channel releases.

The store owns no marketplace clients and performs no commerce-database
writes.  Callers must pass an explicit SQLite path in tests or may opt into
the default Orbit platform database at integration time.  Reading a missing
store is side-effect free; schema creation only happens on a write.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import ROOT
from domains.product_operations import (
    APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION,
    NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION,
    SKU_LINEAGE_SCHEMA_VERSION,
    ModelSkuAssignment,
    NewSourceSkuReservation,
    SourceIdentityEvidence,
    SourceProductIdentity,
    SkuAssignment,
    SkuLineageReservation,
)


DEFAULT_RELEASE_STORE_PATH = ROOT / "data" / "orbit_platform.db"

# One common draft, ten TikTok stores/sites, four Shopee countries and Ozon RU.
# A plan may select any non-empty subset, but cannot invent another adapter
# name or country.
RELEASE_TARGET_LABELS: tuple[str, ...] = (
    "miaoshou:COMMON",
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:HB_PH",
    "tiktok:HB_MY",
    "tiktok:HB_TH",
    "tiktok:HB_VN",
    "tiktok:MX",
    "tiktok:GB",
    "shopee:PH",
    "shopee:MY",
    "shopee:TH",
    "shopee:VN",
    "ozon:RU",
)
_TARGET_SET = frozenset(RELEASE_TARGET_LABELS)

PLAN_PENDING_APPROVAL = "PENDING_APPROVAL"
PLAN_APPROVED = "APPROVED"
SUPERSEDED = "SUPERSEDED"

RUN_PENDING = "PENDING"
RUN_RUNNING = "RUNNING"
RUN_PARTIAL_FAILED = "PARTIAL_FAILED"
RUN_FAILED = "FAILED"
RUN_SUCCEEDED = "SUCCEEDED"
RUN_AWAITING_MANUAL_VERIFICATION = "AWAITING_MANUAL_VERIFICATION"
RUN_COMPLETED_WITH_MANUAL_VERIFICATION = "COMPLETED_WITH_MANUAL_VERIFICATION"

TARGET_PENDING = "PENDING"
TARGET_RUNNING = "RUNNING"
TARGET_FAILED = "FAILED"
TARGET_SUCCEEDED = "SUCCEEDED"
TARGET_SUBMITTED_UNVERIFIED = "SUBMITTED_UNVERIFIED"
TARGET_MANUALLY_VERIFIED = "MANUALLY_VERIFIED"
TARGET_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

REPAIR_RUNNING = "RUNNING"
REPAIR_SUCCEEDED = "SUCCEEDED"
REPAIR_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

TARGET_SCOPED_PROOF_AVAILABLE = "AVAILABLE"
TARGET_SCOPED_PROOF_CONSUMED = "CONSUMED"
TARGET_SCOPED_OPERATION_RUNNING = "RUNNING"
TARGET_SCOPED_OPERATION_SUCCEEDED = "SUCCEEDED"
TARGET_SCOPED_OPERATION_FAILED_PRE_SUBMIT = "FAILED_PRE_SUBMIT"
TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class ReleaseStoreError(RuntimeError):
    """Base error for an invalid release-store operation."""


class ReleaseAuthorizationError(ReleaseStoreError):
    """The exact Kyle approval gate was not satisfied."""


class ImmutableReleaseError(ReleaseStoreError):
    """An existing immutable record was presented with different content."""


class SkuReservationConflict(ReleaseStoreError):
    """The numeric last-four seller SKU is reserved by another active plan."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS release_plans (
    plan_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    seller_sku TEXT NOT NULL,
    sku_key TEXT NOT NULL,
    product_package_id TEXT NOT NULL,
    content_package_id TEXT NOT NULL,
    target_labels_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL UNIQUE,
    confirmation_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING_APPROVAL', 'APPROVED', 'SUPERSEDED')
    ),
    created_at TEXT NOT NULL,
    approved_at TEXT,
    superseded_at TEXT,
    superseded_by_plan_id TEXT,
    supersede_reason TEXT,
    FOREIGN KEY (superseded_by_plan_id) REFERENCES release_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_release_plans_product_created
    ON release_plans(product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_release_plans_status
    ON release_plans(status, created_at DESC);

CREATE TABLE IF NOT EXISTS release_approvals (
    approval_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    payload_digest TEXT NOT NULL,
    confirmation_token TEXT NOT NULL,
    approved_by TEXT NOT NULL CHECK (approved_by = 'Kyle'),
    user_approved INTEGER NOT NULL CHECK (user_approved = 1),
    status TEXT NOT NULL CHECK (status IN ('APPROVED', 'SUPERSEDED')),
    approved_at TEXT NOT NULL,
    superseded_at TEXT,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_release_approvals_status
    ON release_approvals(status, approved_at DESC);

CREATE TABLE IF NOT EXISTS approved_publication_snapshots (
    plan_id TEXT NOT NULL,
    product_revision INTEGER NOT NULL,
    offer_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL UNIQUE,
    release_payload_digest TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, product_revision),
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_approved_publication_snapshots_offer_revision
    ON approved_publication_snapshots(offer_id, product_revision, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_approved_publication_snapshot_immutable
BEFORE UPDATE ON approved_publication_snapshots
BEGIN
    SELECT RAISE(ABORT, 'approved publication snapshot is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_approved_publication_snapshot_append_only
BEFORE DELETE ON approved_publication_snapshots
BEGIN
    SELECT RAISE(ABORT, 'approved publication snapshots are append-only');
END;

CREATE TABLE IF NOT EXISTS release_runs (
    run_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    approval_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING', 'RUNNING', 'PARTIAL_FAILED', 'FAILED',
            'SUCCEEDED', 'SUPERSEDED'
        )
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id),
    FOREIGN KEY (approval_id) REFERENCES release_approvals(approval_id)
);
CREATE INDEX IF NOT EXISTS idx_release_runs_status
    ON release_runs(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS release_target_runs (
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'RUNNING', 'FAILED', 'SUCCEEDED', 'SUPERSEDED')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    external_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (run_id, target_label),
    FOREIGN KEY (run_id) REFERENCES release_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_release_target_runs_status
    ON release_target_runs(run_id, status);

CREATE TABLE IF NOT EXISTS release_target_readbacks (
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (run_id, target_label),
    FOREIGN KEY (run_id, target_label)
        REFERENCES release_target_runs(run_id, target_label)
);

CREATE TABLE IF NOT EXISTS release_target_failure_events (
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    evidence_json TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, target_label, attempt),
    FOREIGN KEY (run_id, target_label)
        REFERENCES release_target_runs(run_id, target_label)
);

CREATE TABLE IF NOT EXISTS release_target_submissions (
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    external_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('SUBMITTED_UNVERIFIED', 'MANUALLY_VERIFIED')
    ),
    submitted_at TEXT NOT NULL,
    verified_by TEXT,
    verified_at TEXT,
    verification_evidence_json TEXT,
    verification_evidence_digest TEXT,
    PRIMARY KEY (run_id, target_label),
    FOREIGN KEY (run_id, target_label)
        REFERENCES release_target_runs(run_id, target_label)
);
CREATE INDEX IF NOT EXISTS idx_release_target_submissions_status
    ON release_target_submissions(run_id, status);

CREATE TABLE IF NOT EXISTS release_target_repairs (
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    operation_digest TEXT NOT NULL UNIQUE,
    operation_json TEXT NOT NULL,
    external_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('RUNNING', 'SUCCEEDED', 'RECONCILIATION_REQUIRED')
    ),
    result_json TEXT,
    result_digest TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (run_id, target_label),
    FOREIGN KEY (run_id, target_label)
        REFERENCES release_target_runs(run_id, target_label),
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_release_target_repairs_status
    ON release_target_repairs(run_id, status);

CREATE TABLE IF NOT EXISTS release_target_retry_proofs (
    proof_digest TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    operation_kind TEXT NOT NULL CHECK (
        operation_kind IN (
            'shopee_safe_pre_submit_retry_v1',
            'ozon_existing_product_stock_reconciliation_v1'
        )
    ),
    product_revision INTEGER NOT NULL CHECK (product_revision >= 0),
    payload_digest TEXT NOT NULL,
    preflight_digest TEXT NOT NULL,
    failure_attempt INTEGER NOT NULL CHECK (failure_attempt >= 0),
    failure_digest TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE', 'CONSUMED')),
    created_at TEXT NOT NULL,
    consumed_at TEXT,
    operation_digest TEXT,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id),
    FOREIGN KEY (run_id, target_label)
        REFERENCES release_target_runs(run_id, target_label)
);
CREATE INDEX IF NOT EXISTS idx_release_target_retry_proof_target
    ON release_target_retry_proofs(run_id, target_label, created_at DESC);

CREATE TABLE IF NOT EXISTS release_target_retry_operations (
    operation_digest TEXT PRIMARY KEY,
    proof_digest TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    operation_kind TEXT NOT NULL CHECK (
        operation_kind IN (
            'shopee_safe_pre_submit_retry_v1',
            'ozon_existing_product_stock_reconciliation_v1'
        )
    ),
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'RUNNING', 'SUCCEEDED', 'FAILED_PRE_SUBMIT',
            'RECONCILIATION_REQUIRED'
        )
    ),
    external_id TEXT,
    result_json TEXT,
    result_digest TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (proof_digest)
        REFERENCES release_target_retry_proofs(proof_digest),
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id),
    FOREIGN KEY (run_id, target_label)
        REFERENCES release_target_runs(run_id, target_label)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_release_target_retry_running
    ON release_target_retry_operations(run_id, target_label)
    WHERE status = 'RUNNING';
CREATE INDEX IF NOT EXISTS idx_release_target_retry_operation_target
    ON release_target_retry_operations(run_id, target_label, created_at DESC);

CREATE TABLE IF NOT EXISTS release_sku_reservations (
    reservation_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    product_id TEXT NOT NULL,
    seller_sku TEXT NOT NULL,
    sku_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('ACTIVE', 'RELEASED', 'SUPERSEDED')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    released_at TEXT,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_release_active_sku_key
    ON release_sku_reservations(sku_key)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_release_sku_product
    ON release_sku_reservations(product_id, status);

CREATE TABLE IF NOT EXISTS release_source_sku_reservations (
    reservation_digest TEXT PRIMARY KEY,
    source_identity_digest TEXT NOT NULL,
    source_offer_id TEXT NOT NULL,
    source_authority TEXT NOT NULL,
    lineage_mode TEXT NOT NULL CHECK (
        lineage_mode IN ('INHERITED_PREDECESSOR', 'NEW_SOURCE')
    ),
    predecessor_id TEXT,
    predecessor_revision INTEGER,
    predecessor_digest TEXT,
    assignment_json TEXT NOT NULL,
    reservation_keys_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'SUPERSEDED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_release_source_sku_identity
    ON release_source_sku_reservations(
        source_identity_digest, status, created_at
    );
CREATE TABLE IF NOT EXISTS release_source_sku_reservation_keys (
    reservation_digest TEXT NOT NULL,
    sku_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'SUPERSEDED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (reservation_digest, sku_key),
    FOREIGN KEY (reservation_digest)
        REFERENCES release_source_sku_reservations(reservation_digest)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_release_source_sku_active_key
    ON release_source_sku_reservation_keys(sku_key)
    WHERE status = 'ACTIVE';
CREATE TABLE IF NOT EXISTS release_source_sku_plan_links (
    plan_id TEXT PRIMARY KEY,
    reservation_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id),
    FOREIGN KEY (reservation_digest)
        REFERENCES release_source_sku_reservations(reservation_digest)
);

CREATE TABLE IF NOT EXISTS release_common_overwrite_reviews (
    plan_id TEXT PRIMARY KEY,
    review_json TEXT NOT NULL,
    review_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('MISMATCH', 'RESOLVED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_release_common_overwrite_review_status
    ON release_common_overwrite_reviews(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS release_shopee_global_plan_approvals (
    approval_record_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    product_revision INTEGER NOT NULL CHECK (product_revision >= 0),
    source_identity_digest TEXT NOT NULL,
    sku_lineage_digest TEXT NOT NULL,
    candidate_digest TEXT NOT NULL,
    approved_plan_digest TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_digest TEXT NOT NULL,
    approved_by TEXT NOT NULL CHECK (approved_by = 'Kyle'),
    created_at TEXT NOT NULL,
    UNIQUE (product_id, product_revision, candidate_digest)
);
CREATE INDEX IF NOT EXISTS idx_release_shopee_global_plan_product
    ON release_shopee_global_plan_approvals(
        product_id, product_revision DESC, created_at DESC
    );

CREATE TABLE IF NOT EXISTS release_channel_category_decisions (
    decision_digest TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    product_revision INTEGER NOT NULL CHECK (product_revision >= 0),
    channel TEXT NOT NULL,
    mode TEXT NOT NULL,
    context_digest TEXT NOT NULL,
    options_digest TEXT NOT NULL,
    selected_category_identity_digest TEXT NOT NULL,
    attribute_tree_digest TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_digest TEXT NOT NULL,
    approved_by TEXT NOT NULL CHECK (approved_by = 'Kyle'),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_release_channel_category_product
    ON release_channel_category_decisions(
        product_id, product_revision DESC, channel, mode, created_at DESC
    );
CREATE TABLE IF NOT EXISTS release_active_channel_category_decisions (
    product_id TEXT NOT NULL,
    product_revision INTEGER NOT NULL CHECK (product_revision >= 0),
    channel TEXT NOT NULL,
    mode TEXT NOT NULL,
    decision_digest TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (product_id, product_revision, channel, mode),
    FOREIGN KEY (decision_digest)
        REFERENCES release_channel_category_decisions(decision_digest)
);
CREATE TABLE IF NOT EXISTS release_channel_category_attribute_selections (
    selection_digest TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    product_revision INTEGER NOT NULL CHECK (product_revision >= 0),
    channel TEXT NOT NULL,
    mode TEXT NOT NULL,
    context_digest TEXT NOT NULL,
    options_digest TEXT NOT NULL,
    category_identity_digest TEXT NOT NULL,
    attribute_tree_digest TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_digest TEXT NOT NULL,
    approved_by TEXT NOT NULL CHECK (approved_by = 'Kyle'),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_active_channel_category_attribute_selections (
    product_id TEXT NOT NULL,
    product_revision INTEGER NOT NULL CHECK (product_revision >= 0),
    channel TEXT NOT NULL,
    mode TEXT NOT NULL,
    selection_digest TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (product_id, product_revision, channel, mode),
    FOREIGN KEY (selection_digest)
        REFERENCES release_channel_category_attribute_selections(
            selection_digest
        )
);

CREATE TRIGGER IF NOT EXISTS trg_release_plan_immutable
BEFORE UPDATE OF
    plan_id, product_id, seller_sku, sku_key, product_package_id,
    content_package_id, target_labels_json, payload_json, payload_digest,
    confirmation_token, created_at
ON release_plans
BEGIN
    SELECT RAISE(ABORT, 'release plan payload is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_approval_immutable
BEFORE UPDATE OF
    approval_id, plan_id, payload_digest, confirmation_token,
    approved_by, user_approved, approved_at
ON release_approvals
BEGIN
    SELECT RAISE(ABORT, 'release approval is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_run_identity_immutable
BEFORE UPDATE OF run_id, plan_id, approval_id, created_at
ON release_runs
BEGIN
    SELECT RAISE(ABORT, 'release run identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_target_identity_immutable
BEFORE UPDATE OF run_id, target_label, idempotency_key, created_at
ON release_target_runs
BEGIN
    SELECT RAISE(ABORT, 'release target identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_target_repair_identity_immutable
BEFORE UPDATE OF
    run_id, target_label, plan_id, operation_digest, operation_json,
    external_id, created_at
ON release_target_repairs
BEGIN
    SELECT RAISE(ABORT, 'release target repair identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_target_retry_proof_identity_immutable
BEFORE UPDATE OF
    proof_digest, plan_id, run_id, target_label, operation_kind,
    product_revision, payload_digest, preflight_digest, failure_attempt,
    failure_digest, proof_json, created_at
ON release_target_retry_proofs
BEGIN
    SELECT RAISE(ABORT, 'release target retry proof identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_target_retry_operation_identity_immutable
BEFORE UPDATE OF
    operation_digest, proof_digest, plan_id, run_id, target_label,
    operation_kind, request_json, created_at
ON release_target_retry_operations
BEGIN
    SELECT RAISE(ABORT, 'release target retry operation identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_sku_reservation_immutable
BEFORE UPDATE OF
    reservation_id, plan_id, product_id, seller_sku, sku_key, created_at
ON release_sku_reservations
BEGIN
    SELECT RAISE(ABORT, 'release SKU reservation identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_shopee_global_plan_immutable
BEFORE UPDATE ON release_shopee_global_plan_approvals
BEGIN
    SELECT RAISE(ABORT, 'approved Shopee global plan record is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_release_shopee_global_plan_append_only_delete
BEFORE DELETE ON release_shopee_global_plan_approvals
BEGIN
    SELECT RAISE(ABORT, 'approved Shopee global plan records are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_release_channel_category_decision_immutable
BEFORE UPDATE ON release_channel_category_decisions
BEGIN
    SELECT RAISE(ABORT, 'channel category decisions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_release_channel_category_decision_append_only
BEFORE DELETE ON release_channel_category_decisions
BEGIN
    SELECT RAISE(ABORT, 'channel category decisions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_release_active_category_identity_immutable
BEFORE UPDATE OF product_id, product_revision, channel, mode
ON release_active_channel_category_decisions
BEGIN
    SELECT RAISE(ABORT, 'active channel category identity is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_release_category_attribute_selection_immutable
BEFORE UPDATE ON release_channel_category_attribute_selections
BEGIN
    SELECT RAISE(
        ABORT,
        'channel category attribute selections are immutable'
    );
END;
CREATE TRIGGER IF NOT EXISTS trg_release_category_attribute_selection_delete
BEFORE DELETE ON release_channel_category_attribute_selections
BEGIN
    SELECT RAISE(
        ABORT,
        'channel category attribute selections are append-only'
    );
END;
CREATE TRIGGER IF NOT EXISTS trg_release_active_attribute_identity_immutable
BEFORE UPDATE OF product_id, product_revision, channel, mode
ON release_active_channel_category_attribute_selections
BEGIN
    SELECT RAISE(
        ABORT,
        'active channel category attribute identity is immutable'
    );
END;
"""


def _text(value: object) -> str:
    return str(value or "").strip()


def _sha256_text(value: object, *, field: str) -> str:
    if type(value) is str and value.startswith("sha256:"):
        value = value[7:]
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("release payload must be JSON-serializable") from error


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _legacy_unverified_submission(row: Mapping[str, Any]) -> dict[str, Any] | None:
    error = _text(row.get("error")).lower()
    external_id = _text(row.get("external_id"))
    if not (
        row.get("status") == TARGET_FAILED
        and external_id
        and "official" in error
        and "readback" in error
        and any(
            marker in error
            for marker in ("unavailable", "no authorised", "no authorized")
        )
    ):
        return None
    evidence = {
        "source": "legacy_release_run_ledger",
        "accepted": True,
        "external_id": external_id,
        "legacy_attempts": row.get("attempts"),
        "legacy_detail": row.get("error"),
        "migration": "accepted_without_official_readback/v1",
    }
    encoded = _canonical_json(evidence)
    return {
        "external_id": external_id,
        "evidence": evidence,
        "evidence_digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "status": TARGET_SUBMITTED_UNVERIFIED,
        "submitted_at": row.get("completed_at"),
        "verified_by": None,
        "verified_at": None,
        "verification_evidence": None,
        "verification_evidence_digest": None,
        "legacy_inferred": True,
    }


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = _text(payload.get(field))
    if not value:
        raise ValueError(f"release plan requires {field}")
    return value


def _sku_key(seller_sku: str) -> str:
    if not seller_sku.isdigit() or len(seller_sku) > 32:
        raise ValueError("seller_sku must contain 1-32 digits")
    return seller_sku[-4:].zfill(4)


def _target_labels(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("release plan targets must be a non-empty list")
    raw = [_text(label) for label in value]
    if not raw or any(not label for label in raw):
        raise ValueError("release plan targets must be a non-empty list")
    if len(set(raw)) != len(raw):
        raise ValueError("release plan targets must not contain duplicates")
    unsupported = sorted(set(raw) - _TARGET_SET)
    if unsupported:
        raise ValueError(f"unsupported release targets: {', '.join(unsupported)}")
    selected = set(raw)
    return tuple(label for label in RELEASE_TARGET_LABELS if label in selected)


def _validated_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("release plan payload must be a mapping")
    plan = dict(payload)
    for field in (
        "plan_id",
        "product_id",
        "seller_sku",
        "product_package_id",
        "content_package_id",
    ):
        plan[field] = _required_text(plan, field)
    _sku_key(plan["seller_sku"])
    plan["targets"] = list(_target_labels(plan.get("targets")))
    identity = plan.get("source_product_identity")
    lineage = plan.get("sku_lineage")
    if (identity is None) != (lineage is None):
        raise ValueError(
            "source identity and SKU lineage must be provided together"
        )
    if identity is not None:
        source_contract = _source_identity_contract(identity)
        if source_contract.payload() != dict(identity):
            raise ValueError("source identity payload is not canonical")
        if not isinstance(lineage, Mapping):
            raise ValueError("SKU lineage payload is invalid")
        assignment = _sku_assignment_contract(lineage.get("assignment"))
        reservation = lineage.get("reservation")
        if not isinstance(reservation, Mapping):
            raise ValueError("SKU lineage reservation is invalid")
        reservation_contract = _sku_reservation_contract(
            lineage=lineage,
            reservation=reservation,
            assignment=assignment,
        )
        if (
            lineage.get("schema_version") != SKU_LINEAGE_SCHEMA_VERSION
            or lineage.get("status") != "READY"
            or lineage.get("ready") is not True
            or lineage.get("source_identity_digest")
            != source_contract.identity_digest
            or lineage.get("assignment") != assignment.payload()
            or reservation_contract.source_identity_digest
            != source_contract.identity_digest
        ):
            raise ValueError("SKU lineage payload is not canonical")
        revision = plan.get("product_revision")
        if (
            type(revision) is not int
            or revision < 0
        ):
            raise ValueError(
                "product_revision must be a non-negative built-in int"
            )
    snapshot_schema = plan.get(
        "approved_publication_snapshot_schema_version"
    )
    if (
        snapshot_schema is not None
        and snapshot_schema
        != APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError(
            "approved publication snapshot schema declaration is invalid"
        )
    return plan


def preview_release_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable identity/token for a plan without opening SQLite."""
    plan = _validated_plan(payload)
    encoded = _canonical_json(plan)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return {
        "plan_id": plan["plan_id"],
        "product_id": plan["product_id"],
        "seller_sku": plan["seller_sku"],
        "product_package_id": plan["product_package_id"],
        "content_package_id": plan["content_package_id"],
        "targets": list(plan["targets"]),
        "payload": plan,
        "payload_digest": digest,
        "confirmation_token": f"PUBLISH-{digest[:16].upper()}",
        "status": "NOT_PERSISTED",
        "persisted": False,
        "approved": False,
    }


def _target_idempotency_key(payload_digest: str, target_label: str) -> str:
    target_digest = _sha256(
        {
            "payload_digest": payload_digest,
            "target_label": target_label,
        }
    )
    channel, site = target_label.split(":", 1)
    return f"publish:{channel}:{site}:{target_digest[:24]}"


def _plan_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "plan_id": row["plan_id"],
        "product_id": row["product_id"],
        "seller_sku": row["seller_sku"],
        "sku_key": row["sku_key"],
        "product_package_id": row["product_package_id"],
        "content_package_id": row["content_package_id"],
        "targets": json.loads(row["target_labels_json"]),
        "payload": json.loads(row["payload_json"]),
        "payload_digest": row["payload_digest"],
        "confirmation_token": row["confirmation_token"],
        "status": row["status"],
        "created_at": row["created_at"],
        "approved_at": row["approved_at"],
        "superseded_at": row["superseded_at"],
        "superseded_by_plan_id": row["superseded_by_plan_id"],
        "supersede_reason": row["supersede_reason"],
    }


def _approval_from_row(row: sqlite3.Row) -> dict[str, Any]:
    """Decode SQLite's constrained approval flag into a strict Python bool."""

    approval = dict(row)
    value = approval.get("user_approved")
    approval["user_approved"] = value is True or (
        type(value) is int and value == 1
    )
    return approval


def _validated_publication_snapshot_row(
    row: sqlite3.Row,
    *,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Rehydrate and bind one internal full snapshot to its ReleasePlan."""

    from domains.product_operations import (
        approved_publication_snapshot_from_payload,
    )

    try:
        document = json.loads(row["snapshot_json"])
        snapshot = approved_publication_snapshot_from_payload(document)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ImmutableReleaseError(
            "stored approved publication snapshot is invalid"
        ) from error
    payload = snapshot.payload()
    if (
        row["schema_version"]
        != APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION
        or payload["schema_version"] != row["schema_version"]
        or payload["snapshot_digest"] != row["snapshot_digest"]
        or payload["plan_id"] != row["plan_id"]
        or payload["offer_id"] != row["offer_id"]
        or payload["product_revision"] != row["product_revision"]
        or payload["bindings"]["release_payload_digest"]
        != "sha256:" + row["release_payload_digest"]
        or plan["plan_id"] != row["plan_id"]
        or plan["product_id"] != row["offer_id"]
        or plan["payload_digest"] != row["release_payload_digest"]
        or plan["payload"].get("product_revision")
        != row["product_revision"]
        or _canonical_json(payload) != row["snapshot_json"]
    ):
        raise ImmutableReleaseError(
            "stored approved publication snapshot identity drifted"
        )
    return payload


def _persist_publication_snapshot_in_transaction(
    connection: sqlite3.Connection,
    *,
    plan_row: sqlite3.Row,
    approval_row: sqlite3.Row,
    now: str,
) -> dict[str, Any] | None:
    """Build and persist a declared v4 snapshot in the approval transaction."""

    from domains.product_operations import build_approved_publication_snapshot

    plan = _plan_from_row(plan_row)
    if (
        plan["payload"].get(
            "approved_publication_snapshot_schema_version"
        )
        is None
    ):
        return None
    if (
        plan["payload"].get(
            "approved_publication_snapshot_schema_version"
        )
        != APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ImmutableReleaseError(
            "declared approved publication snapshot schema is invalid"
        )
    approval = _approval_from_row(approval_row)
    approved_plan = {
        **plan,
        "approval": approval,
    }
    snapshot = build_approved_publication_snapshot(approved_plan)
    document = snapshot.payload()
    serialized = _canonical_json(document)
    revision = document["product_revision"]
    existing = connection.execute(
        """
        SELECT * FROM approved_publication_snapshots
        WHERE plan_id = ? AND product_revision = ?
        """,
        (plan["plan_id"], revision),
    ).fetchone()
    if existing:
        stored = _validated_publication_snapshot_row(existing, plan=plan)
        if stored != document:
            raise ImmutableReleaseError(
                "approval already has a different publication snapshot"
            )
        return stored
    digest_conflict = connection.execute(
        """
        SELECT plan_id, product_revision
        FROM approved_publication_snapshots
        WHERE snapshot_digest = ?
        """,
        (snapshot.snapshot_digest,),
    ).fetchone()
    if digest_conflict:
        raise ImmutableReleaseError(
            "publication snapshot digest belongs to a different approval"
        )
    connection.execute(
        """
        INSERT INTO approved_publication_snapshots (
            plan_id, product_revision, offer_id, schema_version,
            snapshot_digest, release_payload_digest, snapshot_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan["plan_id"],
            revision,
            plan["product_id"],
            APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION,
            snapshot.snapshot_digest,
            plan["payload_digest"],
            serialized,
            now,
        ),
    )
    return document


def _source_sku_reservation_from_row(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": (
            NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION
            if row["lineage_mode"] == "NEW_SOURCE"
            else SKU_LINEAGE_SCHEMA_VERSION
        ),
        "reservation_digest": row["reservation_digest"],
        "source_identity_digest": row["source_identity_digest"],
        "source_offer_id": row["source_offer_id"],
        "source_authority": row["source_authority"],
        "lineage_mode": row["lineage_mode"],
        "predecessor_id": row["predecessor_id"],
        "predecessor_revision": row["predecessor_revision"],
        "predecessor_digest": row["predecessor_digest"],
        "assignment": json.loads(row["assignment_json"]),
        "reservation_keys": json.loads(row["reservation_keys_json"]),
        "status": row["status"],
    }


def _source_identity_contract(value: object) -> SourceProductIdentity:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "source_offer_id",
        "source_item_code",
        "source_authority",
        "provenance",
        "identity_digest",
    }:
        raise ValueError("source identity fields are invalid")
    provenance_rows = value.get("provenance")
    if type(provenance_rows) is not list:
        raise ValueError("source identity provenance must be a list")
    provenance: list[SourceIdentityEvidence] = []
    for row in provenance_rows:
        if not isinstance(row, Mapping) or set(row) != {
            "path",
            "source_offer_id",
        }:
            raise ValueError("source identity provenance fields are invalid")
        provenance.append(
            SourceIdentityEvidence(
                path=row.get("path"),
                source_offer_id=row.get("source_offer_id"),
            )
        )
    return SourceProductIdentity(
        schema_version=value.get("schema_version"),
        source_offer_id=value.get("source_offer_id"),
        source_item_code=value.get("source_item_code"),
        source_authority=value.get("source_authority"),
        provenance=tuple(provenance),
        identity_digest=value.get("identity_digest"),
    )


def _sku_assignment_contract(value: object) -> SkuAssignment:
    if not isinstance(value, Mapping):
        raise ValueError("assignment must be a mapping")
    if set(value) != {"seller_sku", "model_skus"}:
        raise ValueError("assignment fields are invalid")
    model_rows = value.get("model_skus")
    if type(model_rows) is not list:
        raise ValueError("model_skus must be a list")
    models: list[ModelSkuAssignment] = []
    for row in model_rows:
        if not isinstance(row, Mapping) or set(row) != {
            "variant_key",
            "model_sku",
        }:
            raise ValueError("model SKU assignment fields are invalid")
        models.append(
            ModelSkuAssignment(
                variant_key=row.get("variant_key"),
                model_sku=row.get("model_sku"),
            )
        )
    return SkuAssignment(
        seller_sku=value.get("seller_sku"),
        model_skus=tuple(models),
    )


def _sku_reservation_contract(
    *,
    lineage: Mapping[str, Any],
    reservation: Mapping[str, Any],
    assignment: SkuAssignment,
) -> NewSourceSkuReservation | SkuLineageReservation:
    keys = reservation.get("reservation_keys")
    if type(keys) is not list or any(type(value) is not str for value in keys):
        raise ValueError("reservation_keys must be built-in strings")
    idempotent = reservation.get("idempotent")
    if type(idempotent) is not bool:
        raise ValueError("reservation idempotent must be a literal bool")
    mode = lineage.get("lineage_mode")
    if mode == "NEW_SOURCE":
        if set(reservation) != {
            "schema_version",
            "source_identity_digest",
            "assignment",
            "reservation_digest",
            "reservation_keys",
            "idempotent",
        }:
            raise ValueError("new-source reservation fields are invalid")
        return NewSourceSkuReservation(
            schema_version=reservation.get("schema_version"),
            source_identity_digest=reservation.get(
                "source_identity_digest"
            ),
            assignment=assignment,
            reservation_digest=reservation.get("reservation_digest"),
            reservation_keys=tuple(keys),
            idempotent=idempotent,
        )
    if mode != "INHERITED_PREDECESSOR":
        raise ValueError("lineage_mode is invalid")
    if set(reservation) != {
        "schema_version",
        "source_identity_digest",
        "predecessor_id",
        "predecessor_revision",
        "predecessor_digest",
        "assignment",
        "reservation_digest",
        "reservation_keys",
        "idempotent",
    }:
        raise ValueError("inherited reservation fields are invalid")
    contract = SkuLineageReservation(
        schema_version=reservation.get("schema_version"),
        source_identity_digest=reservation.get("source_identity_digest"),
        predecessor_id=reservation.get("predecessor_id"),
        predecessor_revision=reservation.get("predecessor_revision"),
        predecessor_digest=reservation.get("predecessor_digest"),
        assignment=assignment,
        reservation_digest=reservation.get("reservation_digest"),
        reservation_keys=tuple(keys),
        idempotent=idempotent,
    )
    if (
        lineage.get("predecessor_id") != contract.predecessor_id
        or lineage.get("predecessor_revision")
        != contract.predecessor_revision
        or lineage.get("predecessor_digest") != contract.predecessor_digest
    ):
        raise ValueError("lineage predecessor does not match reservation")
    return contract


def _insert_source_sku_lineage_in_transaction(
    connection: sqlite3.Connection,
    plan: Mapping[str, Any],
    *,
    now: str,
) -> dict[str, bool]:
    identity = plan.get("source_product_identity")
    lineage = plan.get("sku_lineage")
    if identity is None and lineage is None:
        return {"inherited_existing_source": False}
    if not isinstance(identity, Mapping) or not isinstance(lineage, Mapping):
        raise SkuReservationConflict(
            "source identity and SKU lineage reservation are required"
        )
    assignment = lineage.get("assignment")
    reservation = lineage.get("reservation")
    if (
        lineage.get("schema_version") != "sku-lineage-reservation/v1"
        or lineage.get("status") != "READY"
        or lineage.get("ready") is not True
        or not isinstance(assignment, Mapping)
        or not isinstance(reservation, Mapping)
        or assignment.get("seller_sku") != plan.get("seller_sku")
    ):
        raise SkuReservationConflict("SKU lineage reservation is invalid")
    try:
        source_contract = _source_identity_contract(identity)
        if source_contract.payload() != dict(identity):
            raise ValueError("source identity is not canonical")
        source_digest = source_contract.identity_digest
        source_offer_id = source_contract.source_offer_id
        source_authority = source_contract.source_authority
        assignment_contract = _sku_assignment_contract(assignment)
        reservation_contract = _sku_reservation_contract(
            lineage=lineage,
            reservation=reservation,
            assignment=assignment_contract,
        )
    except (TypeError, ValueError) as error:
        raise SkuReservationConflict(
            "SKU lineage reservation shape is invalid"
        ) from error
    reservation_payload = reservation_contract.payload()
    reservation_digest = reservation_payload["reservation_digest"]
    reservation_keys = reservation_payload["reservation_keys"]
    if (
        reservation_contract.source_identity_digest != source_digest
        or assignment_contract.payload() != dict(assignment)
        or reservation_payload != dict(reservation)
    ):
        raise SkuReservationConflict(
            "SKU lineage reservation does not match its typed contract"
        )
    existing = connection.execute(
        """
        SELECT * FROM release_source_sku_reservations
        WHERE reservation_digest = ?
        """,
        (reservation_digest,),
    ).fetchone()
    inherited_existing_source = False
    if existing:
        if (
            existing["source_identity_digest"] != source_digest
            or json.loads(existing["assignment_json"]) != dict(assignment)
            or json.loads(existing["reservation_keys_json"]) != reservation_keys
        ):
            raise SkuReservationConflict(
                "reservation digest belongs to different SKU lineage"
            )
        inherited_existing_source = True
    else:
        placeholders = ",".join("?" for _ in reservation_keys)
        conflicts = connection.execute(
            f"""
            SELECT key.sku_key, reservation.*
            FROM release_source_sku_reservation_keys AS key
            JOIN release_source_sku_reservations AS reservation
              ON reservation.reservation_digest = key.reservation_digest
            WHERE key.status = 'ACTIVE'
              AND key.sku_key IN ({placeholders})
            """,
            tuple(reservation_keys),
        ).fetchall()
        if conflicts and any(
            row["source_identity_digest"] != source_digest
            for row in conflicts
        ):
            raise SkuReservationConflict(
                "SKU lineage keys are reserved by another canonical source"
            )
        inherited_existing_source = bool(conflicts)
        for old_digest in {
            row["reservation_digest"] for row in conflicts
        }:
            connection.execute(
                """
                UPDATE release_source_sku_reservations
                SET status = 'SUPERSEDED', updated_at = ?
                WHERE reservation_digest = ? AND status = 'ACTIVE'
                """,
                (now, old_digest),
            )
            connection.execute(
                """
                UPDATE release_source_sku_reservation_keys
                SET status = 'SUPERSEDED', updated_at = ?
                WHERE reservation_digest = ? AND status = 'ACTIVE'
                """,
                (now, old_digest),
            )
        connection.execute(
            """
            INSERT INTO release_source_sku_reservations (
                reservation_digest, source_identity_digest, source_offer_id,
                source_authority, lineage_mode, predecessor_id,
                predecessor_revision, predecessor_digest, assignment_json,
                reservation_keys_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (
                reservation_digest,
                source_digest,
                source_offer_id,
                source_authority,
                lineage.get("lineage_mode"),
                getattr(reservation_contract, "predecessor_id", None),
                getattr(reservation_contract, "predecessor_revision", None),
                getattr(reservation_contract, "predecessor_digest", None),
                _canonical_json(assignment_contract.payload()),
                _canonical_json(reservation_keys),
                now,
                now,
            ),
        )
        connection.executemany(
            """
            INSERT INTO release_source_sku_reservation_keys (
                reservation_digest, sku_key, status, created_at, updated_at
            ) VALUES (?, ?, 'ACTIVE', ?, ?)
            """,
            [
                (reservation_digest, key, now, now)
                for key in reservation_keys
            ],
        )
    connection.execute(
        """
        INSERT INTO release_source_sku_plan_links (
            plan_id, reservation_digest, created_at
        ) VALUES (?, ?, ?)
        """,
        (plan["plan_id"], reservation_digest, now),
    )
    return {"inherited_existing_source": inherited_existing_source}


def _target_scoped_operation_from_row(
    row: sqlite3.Row,
) -> dict[str, Any]:
    return {
        "operation_digest": row["operation_digest"],
        "proof_digest": row["proof_digest"],
        "plan_id": row["plan_id"],
        "run_id": row["run_id"],
        "target_label": row["target_label"],
        "operation_kind": row["operation_kind"],
        "request": json.loads(row["request_json"]),
        "status": row["status"],
        "external_id": row["external_id"],
        "result": (
            json.loads(row["result_json"]) if row["result_json"] else None
        ),
        "result_digest": row["result_digest"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


class ReleaseStore:
    """Transactional SQLite repository for the V1 release state machine."""

    def __init__(self, path: str | Path = DEFAULT_RELEASE_STORE_PATH) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _connect_readonly(self) -> sqlite3.Connection:
        source = self.path.resolve()
        connection = sqlite3.connect(
            source.as_uri() + "?mode=ro",
            uri=True,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_SCHEMA)
        self._backfill_legacy_unverified_submissions(connection)

    def _backfill_legacy_unverified_submissions(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Classify old accepted/no-readback failures without retrying them.

        Earlier releases represented a successful Miaoshou submission with no
        authorised marketplace readback as ``FAILED``.  The publish endpoint
        consequently retried those rows.  Preserve the physical legacy row for
        schema compatibility, but add a durable submission receipt that makes
        the public state terminal and non-retryable.
        """

        rows = connection.execute(
            """
            SELECT target.run_id, target.target_label, target.external_id,
                   target.error, target.attempts, target.completed_at
            FROM release_target_runs AS target
            LEFT JOIN release_target_submissions AS submission
              ON submission.run_id = target.run_id
             AND submission.target_label = target.target_label
            WHERE target.status = 'FAILED'
              AND target.external_id IS NOT NULL
              AND submission.run_id IS NULL
              AND lower(COALESCE(target.error, '')) LIKE '%official%'
              AND lower(COALESCE(target.error, '')) LIKE '%readback%'
              AND (
                    lower(COALESCE(target.error, '')) LIKE '%unavailable%'
                 OR lower(COALESCE(target.error, '')) LIKE '%no authorised%'
                 OR lower(COALESCE(target.error, '')) LIKE '%no authorized%'
              )
            """
        ).fetchall()
        for row in rows:
            evidence = {
                "source": "legacy_release_run_ledger",
                "accepted": True,
                "external_id": row["external_id"],
                "legacy_attempts": row["attempts"],
                "legacy_detail": row["error"],
                "migration": "accepted_without_official_readback/v1",
            }
            encoded = _canonical_json(evidence)
            connection.execute(
                """
                INSERT OR IGNORE INTO release_target_submissions (
                    run_id, target_label, external_id, evidence_json,
                    evidence_digest, status, submitted_at
                ) VALUES (?, ?, ?, ?, ?, 'SUBMITTED_UNVERIFIED', ?)
                """,
                (
                    row["run_id"],
                    row["target_label"],
                    row["external_id"],
                    encoded,
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    row["completed_at"] or _utc_now(),
                ),
            )

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def available_targets(self) -> tuple[str, ...]:
        """Return the exact V1 target allowlist in dependency-display order."""
        return RELEASE_TARGET_LABELS

    def preview_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Build the exact write-free preview consumed by the formal UI."""
        return preview_release_plan(payload)

    def persist_channel_category_attribute_selection(
        self,
        serialized_record: object,
    ) -> dict[str, Any]:
        """Append one immutable Kyle attribute intent and make it active."""

        from shared_platform.channel_category_decisions import (
            rehydrate_attribute_selection,
            serialize_attribute_selection,
        )

        if type(serialized_record) is not str:
            raise ValueError(
                "channel category attribute selection must be canonical JSON"
            )
        selection = rehydrate_attribute_selection(serialized_record)
        if serialize_attribute_selection(selection) != serialized_record:
            raise ImmutableReleaseError(
                "channel category attribute selection is not canonical"
            )
        record_digest = hashlib.sha256(
            serialized_record.encode("utf-8")
        ).hexdigest()
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM release_channel_category_attribute_selections
                WHERE selection_digest = ?
                """,
                (selection["selection_digest"],),
            ).fetchone()
            created = existing is None
            if existing is not None:
                if (
                    existing["record_json"] != serialized_record
                    or existing["record_digest"] != record_digest
                    or existing["product_id"] != selection["product_id"]
                    or existing["product_revision"]
                    != selection["product_revision"]
                    or existing["context_digest"]
                    != selection["context_digest"]
                    or existing["options_digest"]
                    != selection["options_digest"]
                ):
                    raise ImmutableReleaseError(
                        "channel category attribute selection is immutable"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO release_channel_category_attribute_selections (
                        selection_digest, product_id, product_revision,
                        channel, mode, context_digest, options_digest,
                        category_identity_digest, attribute_tree_digest,
                        record_json, record_digest, approved_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Kyle', ?)
                    """,
                    (
                        selection["selection_digest"],
                        selection["product_id"],
                        selection["product_revision"],
                        selection["channel"],
                        selection["mode"],
                        selection["context_digest"],
                        selection["options_digest"],
                        selection["category_identity_digest"],
                        selection["attribute_tree_digest"],
                        serialized_record,
                        record_digest,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO release_active_channel_category_attribute_selections (
                    product_id, product_revision, channel, mode,
                    selection_digest, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id, product_revision, channel, mode)
                DO UPDATE SET
                    selection_digest = excluded.selection_digest,
                    updated_at = excluded.updated_at
                """,
                (
                    selection["product_id"],
                    selection["product_revision"],
                    selection["channel"],
                    selection["mode"],
                    selection["selection_digest"],
                    now,
                ),
            )
        return {
            "selection": selection,
            "record_json": serialized_record,
            "record_digest": record_digest,
            "created": created,
        }

    def channel_category_attribute_selection(
        self,
        *,
        product_id: object,
        product_revision: object,
        channel: object,
        mode: object,
        context_digest: object | None = None,
    ) -> dict[str, Any] | None:
        """Read and fully validate the active Kyle attribute intent."""

        from shared_platform.channel_category_decisions import (
            rehydrate_attribute_selection,
            serialize_attribute_selection,
        )

        clean_product_id = _text(product_id)
        clean_channel = _text(channel)
        clean_mode = _text(mode)
        if (
            not clean_product_id
            or type(product_revision) is not int
            or product_revision < 0
            or not clean_channel
            or not clean_mode
            or not self.path.is_file()
        ):
            return None
        clean_context_digest = (
            _sha256_text(context_digest, field="context_digest")
            if context_digest is not None
            else None
        )
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT selection.*
                    FROM release_active_channel_category_attribute_selections
                        AS active
                    JOIN release_channel_category_attribute_selections
                        AS selection
                      ON selection.selection_digest =
                         active.selection_digest
                    WHERE active.product_id = ?
                      AND active.product_revision = ?
                      AND active.channel = ?
                      AND active.mode = ?
                    """,
                    (
                        clean_product_id,
                        product_revision,
                        clean_channel,
                        clean_mode,
                    ),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        result = dict(row)
        selection = rehydrate_attribute_selection(result["record_json"])
        if (
            serialize_attribute_selection(selection)
            != result["record_json"]
            or hashlib.sha256(
                result["record_json"].encode("utf-8")
            ).hexdigest()
            != result["record_digest"]
            or any(
                selection[field] != result[field]
                for field in (
                    "selection_digest",
                    "product_id",
                    "product_revision",
                    "channel",
                    "mode",
                    "context_digest",
                    "options_digest",
                    "category_identity_digest",
                    "attribute_tree_digest",
                    "approved_by",
                )
            )
        ):
            raise ImmutableReleaseError(
                "stored channel category attribute selection is invalid"
            )
        if (
            clean_context_digest is not None
            and selection["context_digest"] != clean_context_digest
        ):
            return None
        return {
            **result,
            "selection": selection,
        }

    def persist_channel_category_decision(
        self,
        serialized_record: object,
    ) -> dict[str, Any]:
        """Append one immutable selection and make it current locally."""

        from shared_platform.channel_category_decisions import (
            rehydrate_category_decision,
            serialize_category_decision,
        )

        if type(serialized_record) is not str:
            raise ValueError(
                "channel category decision must be canonical JSON"
            )
        decision = rehydrate_category_decision(serialized_record)
        if serialize_category_decision(decision) != serialized_record:
            raise ImmutableReleaseError(
                "channel category decision record is not canonical"
            )
        record_digest = hashlib.sha256(
            serialized_record.encode("utf-8")
        ).hexdigest()
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM release_channel_category_decisions
                WHERE decision_digest = ?
                """,
                (decision["decision_digest"],),
            ).fetchone()
            created = existing is None
            if existing is not None:
                if (
                    existing["record_json"] != serialized_record
                    or existing["record_digest"] != record_digest
                    or existing["product_id"] != decision["product_id"]
                    or existing["product_revision"]
                    != decision["product_revision"]
                    or existing["context_digest"]
                    != decision["context_digest"]
                    or existing["options_digest"]
                    != decision["options_digest"]
                ):
                    raise ImmutableReleaseError(
                        "channel category decision identity is immutable"
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO release_channel_category_decisions (
                        decision_digest, product_id, product_revision,
                        channel, mode, context_digest, options_digest,
                        selected_category_identity_digest,
                        attribute_tree_digest, record_json, record_digest,
                        approved_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Kyle', ?)
                    """,
                    (
                        decision["decision_digest"],
                        decision["product_id"],
                        decision["product_revision"],
                        decision["channel"],
                        decision["mode"],
                        decision["context_digest"],
                        decision["options_digest"],
                        decision["selected_category_identity_digest"],
                        decision["attribute_tree_digest"],
                        serialized_record,
                        record_digest,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO release_active_channel_category_decisions (
                    product_id, product_revision, channel, mode,
                    decision_digest, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id, product_revision, channel, mode)
                DO UPDATE SET
                    decision_digest = excluded.decision_digest,
                    updated_at = excluded.updated_at
                """,
                (
                    decision["product_id"],
                    decision["product_revision"],
                    decision["channel"],
                    decision["mode"],
                    decision["decision_digest"],
                    now,
                ),
            )
        return {
            "decision": decision,
            "record_json": serialized_record,
            "record_digest": record_digest,
            "created": created,
        }

    def channel_category_decision(
        self,
        *,
        product_id: object,
        product_revision: object,
        channel: object,
        mode: object,
        context_digest: object | None = None,
    ) -> dict[str, Any] | None:
        """Read and fully revalidate the current local category selection."""

        from shared_platform.channel_category_decisions import (
            rehydrate_category_decision,
            serialize_category_decision,
        )

        clean_product_id = _text(product_id)
        clean_channel = _text(channel)
        clean_mode = _text(mode)
        if (
            not clean_product_id
            or type(product_revision) is not int
            or product_revision < 0
            or not clean_channel
            or not clean_mode
            or not self.path.is_file()
        ):
            return None
        clean_context_digest = (
            _sha256_text(context_digest, field="context_digest")
            if context_digest is not None
            else None
        )
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT decision.*
                    FROM release_active_channel_category_decisions AS active
                    JOIN release_channel_category_decisions AS decision
                      ON decision.decision_digest = active.decision_digest
                    WHERE active.product_id = ?
                      AND active.product_revision = ?
                      AND active.channel = ?
                      AND active.mode = ?
                    """,
                    (
                        clean_product_id,
                        product_revision,
                        clean_channel,
                        clean_mode,
                    ),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        result = dict(row)
        decision = rehydrate_category_decision(result["record_json"])
        if (
            serialize_category_decision(decision) != result["record_json"]
            or hashlib.sha256(
                result["record_json"].encode("utf-8")
            ).hexdigest()
            != result["record_digest"]
            or decision["decision_digest"] != result["decision_digest"]
            or decision["product_id"] != result["product_id"]
            or decision["product_revision"]
            != result["product_revision"]
            or decision["channel"] != result["channel"]
            or decision["mode"] != result["mode"]
            or decision["context_digest"] != result["context_digest"]
            or decision["options_digest"] != result["options_digest"]
            or decision["selected_category_identity_digest"]
            != result["selected_category_identity_digest"]
            or decision["attribute_tree_digest"]
            != result["attribute_tree_digest"]
            or decision["approved_by"] != result["approved_by"]
        ):
            raise ImmutableReleaseError(
                "stored channel category decision is invalid"
            )
        if (
            clean_context_digest is not None
            and decision["context_digest"] != clean_context_digest
        ):
            return None
        return {
            "decision": decision,
            "record_json": result["record_json"],
            "record_digest": result["record_digest"],
            "created_at": result["created_at"],
        }

    def persist_shopee_global_plan_approval(
        self,
        *,
        product_id: object,
        product_revision: object,
        source_identity_digest: object,
        sku_lineage_digest: object,
        serialized_record: object,
    ) -> dict[str, Any]:
        """Persist one canonical, immutable Kyle-approved Shopee plan.

        The raw record is server-internal.  Its public identity is the exact
        product/revision/candidate tuple plus digests recomputed from the
        canonical contract.  Repeating that tuple is idempotent; presenting a
        different record for the tuple is an immutable-identity violation.
        """

        from shared_platform.shopee_global_plan import (
            APPROVED_PLAN_RECORD_SCHEMA_VERSION,
            rehydrate_approved_shopee_global_plan,
            serialize_approved_shopee_global_plan,
        )

        clean_product_id = _text(product_id)
        if not clean_product_id:
            raise ValueError("Shopee global plan approval requires product_id")
        if type(product_revision) is not int or product_revision < 0:
            raise ValueError(
                "Shopee global plan approval requires an exact product revision"
            )
        clean_source_digest = _sha256_text(
            source_identity_digest,
            field="source_identity_digest",
        )
        clean_lineage_digest = _sha256_text(
            sku_lineage_digest,
            field="sku_lineage_digest",
        )
        if type(serialized_record) is not str:
            raise ValueError(
                "approved Shopee global plan record must be canonical JSON"
            )
        approved = rehydrate_approved_shopee_global_plan(serialized_record)
        if serialize_approved_shopee_global_plan(approved) != serialized_record:
            raise ImmutableReleaseError(
                "approved Shopee global plan record is not canonical"
            )
        execution = approved._plan.payload()
        bindings = execution.get("bindings")
        if (
            not isinstance(bindings, Mapping)
            or bindings.get("source_identity_digest") != clean_source_digest
            or bindings.get("sku_lineage_digest") != clean_lineage_digest
        ):
            raise ImmutableReleaseError(
                "approved Shopee global plan identity does not match the product"
            )
        record_digest = hashlib.sha256(
            serialized_record.encode("utf-8")
        ).hexdigest()
        identity_digest = _sha256(
            {
                "schema_version": APPROVED_PLAN_RECORD_SCHEMA_VERSION,
                "product_id": clean_product_id,
                "product_revision": product_revision,
                "candidate_digest": approved.candidate_digest,
            }
        )
        approval_record_id = (
            f"shopee-global-plan-approval:{identity_digest[:24]}"
        )
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM release_shopee_global_plan_approvals
                WHERE product_id = ?
                  AND product_revision = ?
                  AND candidate_digest = ?
                """,
                (
                    clean_product_id,
                    product_revision,
                    approved.candidate_digest,
                ),
            ).fetchone()
            if existing:
                if (
                    existing["approval_record_id"] != approval_record_id
                    or existing["approved_plan_digest"]
                    != approved.approved_plan_digest
                    or existing["record_digest"] != record_digest
                    or existing["record_json"] != serialized_record
                    or existing["source_identity_digest"]
                    != clean_source_digest
                    or existing["sku_lineage_digest"]
                    != clean_lineage_digest
                ):
                    raise ImmutableReleaseError(
                        "Shopee global plan approval identity is immutable"
                    )
                result = dict(existing)
                result["created"] = False
                return result
            connection.execute(
                """
                INSERT INTO release_shopee_global_plan_approvals (
                    approval_record_id, product_id, product_revision,
                    source_identity_digest, sku_lineage_digest,
                    candidate_digest, approved_plan_digest, record_json,
                    record_digest, approved_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Kyle', ?)
                """,
                (
                    approval_record_id,
                    clean_product_id,
                    product_revision,
                    clean_source_digest,
                    clean_lineage_digest,
                    approved.candidate_digest,
                    approved.approved_plan_digest,
                    serialized_record,
                    record_digest,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM release_shopee_global_plan_approvals
                WHERE approval_record_id = ?
                """,
                (approval_record_id,),
            ).fetchone()
            result = dict(row)
            result["created"] = True
            return result

    def shopee_global_plan_approval(
        self,
        *,
        product_id: object,
        product_revision: object | None = None,
        candidate_digest: object | None = None,
    ) -> dict[str, Any] | None:
        """Load and revalidate one append-only approval record.

        Callers that authorize a ReleasePlan must supply ``product_revision``.
        A no-revision lookup is display-only and must never authorize.
        """

        from shared_platform.shopee_global_plan import (
            rehydrate_approved_shopee_global_plan,
            serialize_approved_shopee_global_plan,
        )

        clean_product_id = _text(product_id)
        if not clean_product_id or not self.path.is_file():
            return None
        clauses = ["approval.product_id = ?"]
        params: list[object] = [clean_product_id]
        if product_revision is not None:
            if type(product_revision) is not int or product_revision < 0:
                raise ValueError("product_revision must be a non-negative int")
            clauses.append("approval.product_revision = ?")
            params.append(product_revision)
        if candidate_digest is not None:
            clauses.append("approval.candidate_digest = ?")
            params.append(
                _sha256_text(candidate_digest, field="candidate_digest")
            )
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    f"""
                    SELECT approval.*
                    FROM release_shopee_global_plan_approvals AS approval
                    WHERE {' AND '.join(clauses)}
                    ORDER BY approval.rowid DESC
                    LIMIT 1
                    """,
                    tuple(params),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        result = dict(row)
        approved = rehydrate_approved_shopee_global_plan(
            result["record_json"]
        )
        if (
            serialize_approved_shopee_global_plan(approved)
            != result["record_json"]
            or hashlib.sha256(
                result["record_json"].encode("utf-8")
            ).hexdigest()
            != result["record_digest"]
            or approved.candidate_digest != result["candidate_digest"]
            or approved.approved_plan_digest
            != result["approved_plan_digest"]
            or approved.approved_by != result["approved_by"]
        ):
            raise ImmutableReleaseError(
                "stored Shopee global plan approval is invalid"
            )
        bindings = approved._plan.payload().get("bindings") or {}
        if (
            bindings.get("source_identity_digest")
            != result["source_identity_digest"]
            or bindings.get("sku_lineage_digest")
            != result["sku_lineage_digest"]
        ):
            raise ImmutableReleaseError(
                "stored Shopee global plan lineage binding is invalid"
            )
        result["approved"] = approved
        return result

    def create_plan(
        self,
        payload: Mapping[str, Any],
        *,
        supersedes_plan_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an immutable plan and reserve its numeric seller SKU.

        Repeating the same plan ID and digest is idempotent.  Reusing the plan
        ID for different content is rejected.  ``supersedes_plan_id`` performs
        successor creation, old-plan supersession and SKU hand-off atomically.
        """
        plan = _validated_plan(payload)
        plan_id = plan["plan_id"]
        predecessor_id = _text(supersedes_plan_id) or None
        if predecessor_id == plan_id:
            raise ValueError("a release plan cannot supersede itself")
        encoded = _canonical_json(plan)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        token = f"PUBLISH-{digest[:16].upper()}"
        targets_json = _canonical_json(plan["targets"])
        sku_key = _sku_key(plan["seller_sku"])
        now = _utc_now()

        try:
            with self._transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM release_plans WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone()
                if existing:
                    if existing["payload_digest"] != digest:
                        raise ImmutableReleaseError(
                            "plan_id already belongs to a different payload digest"
                        )
                    result = _plan_from_row(existing)
                    result["created"] = False
                    return result

                predecessor = None
                if predecessor_id:
                    predecessor = connection.execute(
                        "SELECT * FROM release_plans WHERE plan_id = ?",
                        (predecessor_id,),
                    ).fetchone()
                    if not predecessor:
                        raise ReleaseStoreError("superseded release plan was not found")
                    relink_unlinked_superseded = bool(
                        predecessor["status"] == SUPERSEDED
                        and not predecessor["superseded_by_plan_id"]
                    )
                    if (
                        predecessor["status"] == SUPERSEDED
                        and not relink_unlinked_superseded
                    ):
                        raise ReleaseStoreError("superseded release plan is already superseded")
                    if predecessor["product_id"] != plan["product_id"]:
                        raise ReleaseStoreError(
                            "a successor plan must belong to the same product_id"
                        )
                    if predecessor["seller_sku"] != plan["seller_sku"]:
                        raise ReleaseStoreError(
                            "a successor plan must keep the same seller SKU"
                        )
                else:
                    relink_unlinked_superseded = False

                connection.execute(
                    """
                    INSERT INTO release_plans (
                        plan_id, product_id, seller_sku, sku_key,
                        product_package_id, content_package_id,
                        target_labels_json, payload_json, payload_digest,
                        confirmation_token, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        plan["product_id"],
                        plan["seller_sku"],
                        sku_key,
                        plan["product_package_id"],
                        plan["content_package_id"],
                        targets_json,
                        encoded,
                        digest,
                        token,
                        PLAN_PENDING_APPROVAL,
                        now,
                    ),
                )
                if predecessor is not None:
                    if relink_unlinked_superseded:
                        updated = connection.execute(
                            """
                            UPDATE release_plans
                            SET superseded_by_plan_id = ?
                            WHERE plan_id = ?
                              AND status = 'SUPERSEDED'
                              AND superseded_by_plan_id IS NULL
                            """,
                            (plan_id, predecessor_id),
                        )
                        if updated.rowcount != 1:
                            raise ReleaseStoreError(
                                "unlinked predecessor changed before successor link"
                            )
                    else:
                        self._supersede_in_transaction(
                            connection,
                            predecessor_id,
                            superseded_by_plan_id=plan_id,
                            reason="replaced by a newer immutable release plan",
                            now=now,
                        )

                source_lineage = _insert_source_sku_lineage_in_transaction(
                    connection,
                    plan,
                    now=now,
                )
                active_legacy = connection.execute(
                    """
                    SELECT * FROM release_sku_reservations
                    WHERE sku_key = ? AND status = 'ACTIVE'
                    """,
                    (sku_key,),
                ).fetchone()
                if not active_legacy:
                    connection.execute(
                        """
                        INSERT INTO release_sku_reservations (
                            reservation_id, plan_id, product_id, seller_sku,
                            sku_key, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                        """,
                        (
                            f"sku-reservation:{plan_id}",
                            plan_id,
                            plan["product_id"],
                            plan["seller_sku"],
                            sku_key,
                            now,
                            now,
                        ),
                    )
                elif not source_lineage["inherited_existing_source"]:
                    raise SkuReservationConflict(
                        f"seller SKU key {sku_key} is already reserved"
                    )
                row = connection.execute(
                    "SELECT * FROM release_plans WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone()
                result = _plan_from_row(row)
                result["created"] = True
                return result
        except sqlite3.IntegrityError as error:
            if "release_sku_reservations.sku_key" in str(error):
                raise SkuReservationConflict(
                    f"seller SKU key {sku_key} is already reserved"
                ) from error
            raise

    def create_target_only_successor(
        self,
        predecessor_plan_id: str,
        *,
        additions: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Persist an additive successor without rebuilding dashboard facts."""
        predecessor = self.get_plan(_text(predecessor_plan_id))
        if predecessor is None:
            raise ReleaseStoreError("predecessor release plan was not found")
        if predecessor["status"] != PLAN_APPROVED:
            raise ReleaseAuthorizationError(
                "target-only successor requires an approved predecessor"
            )
        if self.approved_publication_snapshot(
            offer_id=predecessor["product_id"], plan_id=predecessor["plan_id"]
        ) is None:
            raise ReleaseAuthorizationError(
                "target-only successor requires a durable frozen predecessor snapshot"
            )
        from shared_platform.target_only_successor import (
            build_target_only_successor_payload,
        )

        payload = build_target_only_successor_payload(
            predecessor["payload"],
            additions=additions,
            ordered_targets=RELEASE_TARGET_LABELS,
        )
        from domains.product_operations import build_approved_publication_snapshot

        preview = self.preview_plan(payload)
        validation_time = "2000-01-01T00:00:00+00:00"
        build_approved_publication_snapshot(
            {
                **preview,
                "status": PLAN_APPROVED,
                "approved_at": validation_time,
                "approval": {
                    "status": PLAN_APPROVED,
                    "approved_by": "Kyle",
                    "approved_at": validation_time,
                    "user_approved": True,
                    "plan_id": preview["plan_id"],
                    "payload_digest": preview["payload_digest"],
                },
            }
        )
        return self.create_plan(payload, supersedes_plan_id=predecessor["plan_id"])

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    "SELECT * FROM release_plans WHERE plan_id = ?",
                    (_text(plan_id),),
                ).fetchone()
                if not row:
                    return None
                result = _plan_from_row(row)
                approval = connection.execute(
                    "SELECT * FROM release_approvals WHERE plan_id = ?",
                    (_text(plan_id),),
                ).fetchone()
                reservation = connection.execute(
                    "SELECT * FROM release_sku_reservations WHERE plan_id = ?",
                    (_text(plan_id),),
                ).fetchone()
                source_reservation = connection.execute(
                    """
                    SELECT reservation.*
                    FROM release_source_sku_plan_links AS link
                    JOIN release_source_sku_reservations AS reservation
                      ON reservation.reservation_digest = link.reservation_digest
                    WHERE link.plan_id = ?
                    """,
                    (_text(plan_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        result["approval"] = _approval_from_row(approval) if approval else None
        result["sku_reservation"] = dict(reservation) if reservation else None
        result["source_sku_reservation"] = (
            _source_sku_reservation_from_row(source_reservation)
            if source_reservation
            else None
        )
        return result

    def approved_publication_snapshot(
        self,
        *,
        offer_id: object,
        plan_id: object | None = None,
        snapshot_digest: object | None = None,
    ) -> dict[str, Any] | None:
        """Return the full verified v4 document for server-internal runners."""

        clean_offer_id = _text(offer_id)
        clean_plan_id = _text(plan_id)
        clean_snapshot_digest = (
            "sha256:"
            + _sha256_text(snapshot_digest, field="snapshot_digest")
            if snapshot_digest is not None
            else ""
        )
        if not clean_offer_id:
            raise ValueError("offer_id is required")
        if bool(clean_plan_id) == bool(clean_snapshot_digest):
            raise ValueError("exactly one of plan_id or snapshot_digest is required")
        if not self.path.is_file():
            return None
        clauses = ["snapshot.offer_id = ?"]
        parameters: list[object] = [clean_offer_id]
        if clean_plan_id:
            clauses.append("snapshot.plan_id = ?")
            parameters.append(clean_plan_id)
        else:
            clauses.append("snapshot.snapshot_digest = ?")
            parameters.append(clean_snapshot_digest)
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    f"""
                    SELECT snapshot.*
                    FROM approved_publication_snapshots AS snapshot
                    WHERE {' AND '.join(clauses)}
                    """,
                    tuple(parameters),
                ).fetchone()
                if not row:
                    return None
                plan_row = connection.execute(
                    "SELECT * FROM release_plans WHERE plan_id = ?",
                    (row["plan_id"],),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not plan_row:
            raise ImmutableReleaseError(
                "approved publication snapshot lost its ReleasePlan"
            )
        return _validated_publication_snapshot_row(
            row,
            plan=_plan_from_row(plan_row),
        )

    def publication_snapshot_projection(
        self,
        *,
        offer_id: object,
        plan_id: object | None = None,
        snapshot_digest: object | None = None,
    ) -> dict[str, Any] | None:
        """Return the redacted Product Center snapshot status/coverage."""

        clean_offer_id = _text(offer_id)
        clean_plan_id = _text(plan_id)
        if not clean_offer_id:
            raise ValueError("offer_id is required")
        if not clean_plan_id and snapshot_digest is None:
            raise ValueError("plan_id or snapshot_digest is required")
        document = self.approved_publication_snapshot(
            offer_id=clean_offer_id,
            plan_id=clean_plan_id or None,
            snapshot_digest=snapshot_digest,
        )
        if document is None:
            if not clean_plan_id:
                return None
            plan = self.get_plan(clean_plan_id)
            if plan is None or plan["product_id"] != clean_offer_id:
                return None
            if (
                plan["status"] == PLAN_APPROVED
                and plan["payload"].get(
                    "approved_publication_snapshot_schema_version"
                )
                == APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION
            ):
                raise ImmutableReleaseError(
                    "declared v4 approval is missing its durable snapshot"
                )
            return {
                "schema_version": "approved-publication-snapshot-summary/v1",
                "status": "SNAPSHOT_UNAVAILABLE",
                "reason_code": (
                    "legacy_approval_without_v4_snapshot"
                    if plan["status"] == PLAN_APPROVED
                    else "release_plan_not_approved"
                ),
                "identity": {
                    "offer_id": plan["product_id"],
                    "plan_id": plan["plan_id"],
                    "product_revision": plan["payload"].get(
                        "product_revision"
                    ),
                    "snapshot_digest": None,
                },
                "coverage": None,
            }
        provider_category_count = sum(
            1
            for row in document["categories_by_target"].values()
            if row["category"] is not None
        )
        return {
            "schema_version": "approved-publication-snapshot-summary/v1",
            "status": "AVAILABLE",
            "reason_code": None,
            "identity": {
                "offer_id": document["offer_id"],
                "plan_id": document["plan_id"],
                "product_revision": document["product_revision"],
                "snapshot_digest": document["snapshot_digest"],
                "snapshot_schema_version": document["schema_version"],
            },
            "bindings": {
                "release_payload_digest": document["bindings"][
                    "release_payload_digest"
                ]
            },
            "coverage": {
                "publication_target_count": len(
                    document["publication_targets"]
                ),
                "provider_category_count": provider_category_count,
                "sku_count": len(document["skus"]),
                "approved_image_count": len(document["product"]["images"]),
            },
            "approved_at": document["approved_at"],
            "approved_by": document["approved_by"],
        }

    def active_plan_for_product(self, product_id: str) -> dict[str, Any] | None:
        """Return the newest non-superseded plan without creating the store."""
        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT plan_id FROM release_plans
                    WHERE product_id = ? AND status != 'SUPERSEDED'
                    ORDER BY created_at DESC, plan_id DESC
                    LIMIT 1
                    """,
                    (_text(product_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        return self.get_plan(row["plan_id"]) if row else None

    def source_sku_lineage_context(
        self,
        *,
        source_offer_id: str,
        source_authority: str,
        source_identity_digest: str,
        exclude_product_id: str | None = None,
    ) -> dict[str, Any]:
        """Load immutable predecessor/reservation facts for SKU lineage."""

        if not self.path.is_file():
            return {"predecessor_records": [], "existing_reservations": []}
        predecessors: list[dict[str, Any]] = []
        reservations: list[dict[str, Any]] = []
        with self._connect_readonly() as connection:
            try:
                plan_rows = connection.execute(
                    """
                    SELECT * FROM release_plans
                    WHERE status IN ('APPROVED', 'SUPERSEDED')
                    ORDER BY created_at, plan_id
                    """
                ).fetchall()
                reservation_rows = connection.execute(
                    """
                    SELECT * FROM release_source_sku_reservations
                    WHERE source_identity_digest = ? AND status = 'ACTIVE'
                    ORDER BY created_at, reservation_digest
                    """,
                    (_text(source_identity_digest),),
                ).fetchall()
            except sqlite3.OperationalError:
                return {
                    "predecessor_records": [],
                    "existing_reservations": [],
                }
        for row in plan_rows:
            if (
                exclude_product_id
                and row["product_id"] == _text(exclude_product_id)
            ):
                continue
            payload = json.loads(row["payload_json"])
            identity = payload.get("source_product_identity")
            lineage = payload.get("sku_lineage")
            assignment = (
                lineage.get("assignment")
                if isinstance(lineage, Mapping)
                else None
            )
            if not isinstance(identity, Mapping):
                continue
            if (
                identity.get("source_offer_id") != source_offer_id
                or identity.get("source_authority") != source_authority
            ):
                continue
            try:
                source_contract = _source_identity_contract(identity)
            except (TypeError, ValueError) as error:
                raise ImmutableReleaseError(
                    "stored source identity contract is invalid"
                ) from error
            revision = payload.get("product_revision")
            if type(revision) is not int or revision < 0:
                raise ImmutableReleaseError(
                    "stored predecessor revision is invalid"
                )
            if (
                source_contract.payload() != dict(identity)
                or not isinstance(assignment, Mapping)
            ):
                raise ImmutableReleaseError(
                    "stored predecessor lineage is invalid"
                )
            predecessors.append(
                {
                    "predecessor_id": row["plan_id"],
                    "revision": revision,
                    "status": (
                        "APPROVED"
                        if row["status"] == "APPROVED"
                        else "RELEASED"
                    ),
                    "source_identity": {
                        "source_offer_id": source_offer_id,
                        "source_authority": source_authority,
                        "identity_digest": source_contract.identity_digest,
                    },
                    "seller_sku": assignment.get("seller_sku"),
                    "model_skus": list(assignment.get("model_skus") or ()),
                    "predecessor_digest": lineage.get(
                        "predecessor_digest"
                    ),
                }
            )
        for row in reservation_rows:
            value = _source_sku_reservation_from_row(row)
            # A NEW_SOURCE ownership row is superseded atomically by the first
            # inherited reservation. Passing it to the 01 resolver would
            # incorrectly treat its different predecessor identity as an
            # overlapping conflict.
            if value["lineage_mode"] == "INHERITED_PREDECESSOR":
                reservations.append(value)
        return {
            "predecessor_records": predecessors,
            "existing_reservations": reservations,
        }

    def predecessor_plan_for(self, successor_plan_id: str) -> dict[str, Any] | None:
        """Return the exact plan atomically superseded by one successor."""

        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT plan_id FROM release_plans
                    WHERE superseded_by_plan_id = ?
                    ORDER BY superseded_at DESC, plan_id DESC
                    """,
                    (_text(successor_plan_id),),
                ).fetchall()
            except sqlite3.OperationalError:
                return None
        if len(rows) > 1:
            raise ImmutableReleaseError(
                "successor plan has multiple predecessor identities"
            )
        return self.get_plan(rows[0]["plan_id"]) if rows else None

    def get_common_overwrite_review(
        self,
        plan_id: str,
    ) -> dict[str, Any] | None:
        """Return the latest redacted COMMON mismatch review without writes."""

        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT review_json, review_digest, status,
                           created_at, updated_at, resolved_at
                    FROM release_common_overwrite_reviews
                    WHERE plan_id = ?
                    """,
                    (_text(plan_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        review = json.loads(row["review_json"])
        review.update(
            {
                "review_digest": row["review_digest"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "resolved_at": row["resolved_at"],
            }
        )
        return review

    def record_common_overwrite_review(
        self,
        plan_id: str,
        review: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist one redacted mismatch review without creating a release run."""

        clean_plan_id = _text(plan_id)
        candidate = dict(review)
        if (
            candidate.get("status") != "MISMATCH"
            or candidate.get("external_writes_performed") != []
            or _text(candidate.get("plan_id")) != clean_plan_id
        ):
            raise ReleaseStoreError("invalid COMMON overwrite review contract")
        encoded = _canonical_json(candidate)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = _utc_now()
        with self._transaction() as connection:
            plan = connection.execute(
                "SELECT * FROM release_plans WHERE plan_id = ?",
                (clean_plan_id,),
            ).fetchone()
            if not plan:
                raise ReleaseStoreError("release plan was not found")
            if (
                candidate.get("payload_digest") != plan["payload_digest"]
                or candidate.get("confirmation_token")
                != plan["confirmation_token"]
            ):
                raise ImmutableReleaseError(
                    "COMMON overwrite review does not match the immutable plan"
                )
            existing = connection.execute(
                """
                SELECT created_at FROM release_common_overwrite_reviews
                WHERE plan_id = ?
                """,
                (clean_plan_id,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            connection.execute(
                """
                INSERT INTO release_common_overwrite_reviews (
                    plan_id, review_json, review_digest, status,
                    created_at, updated_at, resolved_at
                ) VALUES (?, ?, ?, 'MISMATCH', ?, ?, NULL)
                ON CONFLICT(plan_id) DO UPDATE SET
                    review_json = excluded.review_json,
                    review_digest = excluded.review_digest,
                    status = 'MISMATCH',
                    updated_at = excluded.updated_at,
                    resolved_at = NULL
                """,
                (
                    clean_plan_id,
                    encoded,
                    digest,
                    created_at,
                    now,
                ),
            )
        return self.get_common_overwrite_review(clean_plan_id) or candidate

    def resolve_common_overwrite_review(self, plan_id: str) -> None:
        """Mark a review resolved after verified write/readback or exact reuse."""

        with self._transaction() as connection:
            now = _utc_now()
            connection.execute(
                """
                UPDATE release_common_overwrite_reviews
                SET status = 'RESOLVED', updated_at = ?, resolved_at = ?
                WHERE plan_id = ? AND status = 'MISMATCH'
                """,
                (now, now, _text(plan_id)),
            )

    def latest_unlinked_common_predecessor(
        self,
        *,
        product_id: str,
        seller_sku: str,
    ) -> dict[str, Any] | None:
        """Return the sole unlinked superseded plan with COMMON proof."""

        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT plan.plan_id
                    FROM release_plans AS plan
                    JOIN release_runs AS run
                      ON run.plan_id = plan.plan_id
                    JOIN release_target_runs AS target
                      ON target.run_id = run.run_id
                     AND target.target_label = 'miaoshou:COMMON'
                     AND target.status = 'SUCCEEDED'
                    JOIN release_target_readbacks AS readback
                      ON readback.run_id = run.run_id
                     AND readback.target_label = target.target_label
                    WHERE plan.product_id = ?
                      AND plan.seller_sku = ?
                      AND plan.status = 'SUPERSEDED'
                      AND plan.superseded_by_plan_id IS NULL
                    ORDER BY plan.superseded_at DESC,
                             plan.created_at DESC,
                             plan.plan_id DESC
                    """,
                    (_text(product_id), _text(seller_sku)),
                ).fetchall()
            except sqlite3.OperationalError:
                return None
        if len(rows) > 1:
            raise ImmutableReleaseError(
                "multiple unlinked COMMON predecessors require explicit identity"
            )
        return self.get_plan(rows[0]["plan_id"]) if rows else None

    def approve_plan(
        self,
        plan_id: str,
        *,
        approved_by: str,
        user_approved: bool,
        confirmation_token: str,
    ) -> dict[str, Any]:
        """Persist Kyle's exact approval for one immutable payload digest."""
        if user_approved is not True:
            raise ReleaseAuthorizationError("literal user_approved=True is required")
        if _text(approved_by) != "Kyle":
            raise ReleaseAuthorizationError("approved_by must be Kyle")
        with self._transaction() as connection:
            plan = connection.execute(
                "SELECT * FROM release_plans WHERE plan_id = ?",
                (_text(plan_id),),
            ).fetchone()
            if not plan:
                raise ReleaseStoreError("release plan was not found")
            if plan["status"] == SUPERSEDED:
                raise ReleaseAuthorizationError("a superseded plan cannot be approved")
            if _text(confirmation_token) != plan["confirmation_token"]:
                raise ReleaseAuthorizationError(
                    "confirmation token does not match the immutable release plan"
                )
            existing = connection.execute(
                "SELECT * FROM release_approvals WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
            if existing:
                if (
                    existing["payload_digest"] != plan["payload_digest"]
                    or existing["confirmation_token"] != plan["confirmation_token"]
                    or existing["approved_by"] != "Kyle"
                ):
                    raise ImmutableReleaseError(
                        "release plan already has a different approval"
                    )
                snapshot_projection = None
                if json.loads(plan["payload_json"]).get(
                    "approved_publication_snapshot_schema_version"
                ) == APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION:
                    snapshot_row = connection.execute(
                        """
                        SELECT * FROM approved_publication_snapshots
                        WHERE plan_id = ?
                        """,
                        (plan["plan_id"],),
                    ).fetchone()
                    if not snapshot_row:
                        raise ImmutableReleaseError(
                            "declared v4 approval is missing its durable snapshot"
                        )
                    snapshot = _validated_publication_snapshot_row(
                        snapshot_row,
                        plan=_plan_from_row(plan),
                    )
                    snapshot_projection = {
                        "schema_version": snapshot["schema_version"],
                        "snapshot_digest": snapshot["snapshot_digest"],
                        "product_revision": snapshot["product_revision"],
                    }
                return {
                    **_approval_from_row(existing),
                    "created": False,
                    "publication_snapshot": snapshot_projection,
                }

            now = _utc_now()
            approval_id = f"release-approval:{plan['payload_digest'][:24]}"
            connection.execute(
                """
                INSERT INTO release_approvals (
                    approval_id, plan_id, payload_digest, confirmation_token,
                    approved_by, user_approved, status, approved_at
                ) VALUES (?, ?, ?, ?, 'Kyle', 1, 'APPROVED', ?)
                """,
                (
                    approval_id,
                    plan["plan_id"],
                    plan["payload_digest"],
                    plan["confirmation_token"],
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE release_plans
                SET status = 'APPROVED', approved_at = ?
                WHERE plan_id = ?
                """,
                (now, plan["plan_id"]),
            )
            row = connection.execute(
                "SELECT * FROM release_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            approved_plan_row = connection.execute(
                "SELECT * FROM release_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
            snapshot = _persist_publication_snapshot_in_transaction(
                connection,
                plan_row=approved_plan_row,
                approval_row=row,
                now=now,
            )
            return {
                **_approval_from_row(row),
                "created": True,
                "publication_snapshot": (
                    {
                        "schema_version": snapshot["schema_version"],
                        "snapshot_digest": snapshot["snapshot_digest"],
                        "product_revision": snapshot["product_revision"],
                    }
                    if snapshot is not None
                    else None
                ),
            }

    def start_run(
        self,
        plan_id: str,
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Create one durable run and one idempotent target row per selection."""
        with self._transaction() as connection:
            plan = connection.execute(
                "SELECT * FROM release_plans WHERE plan_id = ?",
                (_text(plan_id),),
            ).fetchone()
            if not plan:
                raise ReleaseStoreError("release plan was not found")
            if plan["status"] != PLAN_APPROVED:
                raise ReleaseAuthorizationError(
                    "release plan requires an active Kyle approval"
                )
            approval = connection.execute(
                """
                SELECT * FROM release_approvals
                WHERE plan_id = ? AND status = 'APPROVED'
                """,
                (plan["plan_id"],),
            ).fetchone()
            if not approval:
                raise ReleaseAuthorizationError(
                    "release plan requires an active Kyle approval"
                )
            existing = connection.execute(
                "SELECT run_id FROM release_runs WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
            if existing:
                return self._run_in_transaction(connection, existing["run_id"])

            clean_run_id = _text(run_id) or f"release-run:{plan['payload_digest'][:24]}"
            conflict = connection.execute(
                "SELECT plan_id FROM release_runs WHERE run_id = ?",
                (clean_run_id,),
            ).fetchone()
            if conflict:
                raise ImmutableReleaseError(
                    "run_id already belongs to a different release plan"
                )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_runs (
                    run_id, plan_id, approval_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    clean_run_id,
                    plan["plan_id"],
                    approval["approval_id"],
                    now,
                    now,
                ),
            )
            targets = json.loads(plan["target_labels_json"])
            connection.executemany(
                """
                INSERT INTO release_target_runs (
                    run_id, target_label, idempotency_key, status,
                    attempts, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', 0, ?, ?)
                """,
                [
                    (
                        clean_run_id,
                        label,
                        _target_idempotency_key(plan["payload_digest"], label),
                        now,
                        now,
                    )
                    for label in targets
                ],
            )
            return self._run_in_transaction(connection, clean_run_id)

    def begin_target(self, run_id: str, target_label: str) -> dict[str, Any]:
        """Claim one pending target attempt before any adapter is called."""
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            self._require_active_run(connection, row["run_id"])
            if row["status"] != TARGET_PENDING:
                raise ReleaseStoreError(
                    f"target must be PENDING before begin; found {row['status']}"
                )
            now = _utc_now()
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'RUNNING', attempts = attempts + 1,
                    error = NULL, completed_at = NULL,
                    updated_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (now, row["run_id"], row["target_label"]),
            )
            connection.execute(
                """
                UPDATE release_runs
                SET status = 'RUNNING', updated_at = ?, completed_at = NULL
                WHERE run_id = ?
                """,
                (now, row["run_id"]),
            )
            return dict(
                connection.execute(
                    """
                    SELECT * FROM release_target_runs
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
            )

    def record_target_success(
        self,
        run_id: str,
        target_label: str,
        *,
        external_id: str | None = None,
        readback_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record verified success; repeated identical readback is idempotent."""
        evidence_json = (
            _canonical_json(dict(readback_evidence))
            if readback_evidence is not None
            else None
        )
        evidence_digest = (
            hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
            if evidence_json is not None
            else None
        )
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            clean_external_id = _text(external_id) or None
            if row["status"] == TARGET_SUCCEEDED:
                if (row["external_id"] or None) != clean_external_id:
                    raise ImmutableReleaseError(
                        "successful target already has a different external_id"
                    )
                existing_evidence = connection.execute(
                    """
                    SELECT evidence_json, evidence_digest
                    FROM release_target_readbacks
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
                if evidence_json is not None and existing_evidence:
                    if existing_evidence["evidence_digest"] != evidence_digest:
                        raise ImmutableReleaseError(
                            "successful target already has different readback evidence"
                        )
                elif evidence_json is not None:
                    connection.execute(
                        """
                        INSERT INTO release_target_readbacks (
                            run_id, target_label, evidence_json,
                            evidence_digest, verified_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            row["run_id"],
                            row["target_label"],
                            evidence_json,
                            evidence_digest,
                            _utc_now(),
                        ),
                    )
                result = dict(row)
                result["readback"] = (
                    json.loads(evidence_json)
                    if evidence_json is not None
                    else (
                        json.loads(existing_evidence["evidence_json"])
                        if existing_evidence
                        else None
                    )
                )
                return result
            self._require_active_run(connection, row["run_id"])
            if row["status"] != TARGET_RUNNING:
                raise ReleaseStoreError(
                    f"target must be RUNNING before success; found {row['status']}"
                )
            now = _utc_now()
            if evidence_json is not None:
                connection.execute(
                    """
                    INSERT INTO release_target_readbacks (
                        run_id, target_label, evidence_json,
                        evidence_digest, verified_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["run_id"],
                        row["target_label"],
                        evidence_json,
                        evidence_digest,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUCCEEDED', external_id = ?, error = NULL,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    clean_external_id,
                    now,
                    now,
                    row["run_id"],
                    row["target_label"],
                ),
            )
            self._refresh_run_status(connection, row["run_id"], now=now)
            return dict(
                connection.execute(
                    """
                    SELECT * FROM release_target_runs
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
            )

    def record_common_reconciled_success(
        self,
        run_id: str,
        *,
        external_id: str,
        readback_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Close one accepted COMMON write using a later exact GET-only readback."""

        incoming = dict(readback_evidence)
        checks = incoming.get("checks")
        if (
            incoming.get("verified") is not True
            or incoming.get("mode") != "readback_reconciliation_no_write"
            or incoming.get("external_writes_performed") != []
            or not isinstance(checks, dict)
            or not checks
            or any(value is not True for value in checks.values())
        ):
            raise ValueError(
                "COMMON reconciliation requires exact zero-write readback"
            )
        clean_external_id = _text(external_id)
        if not clean_external_id:
            raise ValueError("COMMON reconciliation requires external_id")

        with self._transaction() as connection:
            row = self._target_for_update(
                connection,
                run_id,
                "miaoshou:COMMON",
            )
            if row["status"] != TARGET_FAILED:
                raise ReleaseStoreError(
                    "only a failed COMMON target may be reconciled"
                )
            if _text(row["external_id"]) != clean_external_id:
                raise ReleaseAuthorizationError(
                    "COMMON reconciliation external identity changed"
                )
            failure = connection.execute(
                """
                SELECT evidence_json, evidence_digest
                FROM release_target_failure_events
                WHERE run_id = ? AND target_label = 'miaoshou:COMMON'
                ORDER BY attempt DESC, created_at DESC
                LIMIT 1
                """,
                (row["run_id"],),
            ).fetchone()
            prior = (
                json.loads(failure["evidence_json"])
                if failure and failure["evidence_json"]
                else {}
            )
            prior_writes = ["miaoshou:COMMON:immutable_plan_write"]
            if (
                not failure
                or failure["evidence_digest"] != _sha256(prior)
                or prior.get("save_accepted") is not True
                or prior.get("verified") is not False
                or prior.get("external_writes_performed") != prior_writes
            ):
                raise ReleaseAuthorizationError(
                    "prior COMMON write evidence is not exact and truthful"
                )

            merged = {
                **incoming,
                "schema_version": "miaoshou-common-reconciled/v1",
                "prior_external_write_evidence_digest": failure[
                    "evidence_digest"
                ],
                "prior_external_writes_performed": prior_writes,
                "reconciliation_external_writes_performed": [],
                "external_writes_performed": prior_writes,
            }
            evidence_json = _canonical_json(merged)
            evidence_digest = hashlib.sha256(
                evidence_json.encode("utf-8")
            ).hexdigest()
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_readbacks (
                    run_id, target_label, evidence_json,
                    evidence_digest, verified_at
                ) VALUES (?, 'miaoshou:COMMON', ?, ?, ?)
                """,
                (
                    row["run_id"],
                    evidence_json,
                    evidence_digest,
                    now,
                ),
            )
            changed = connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUCCEEDED', error = NULL,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = 'miaoshou:COMMON'
                  AND status = 'FAILED' AND external_id = ?
                """,
                (
                    now,
                    now,
                    row["run_id"],
                    clean_external_id,
                ),
            ).rowcount
            if changed != 1:
                raise ReleaseStoreError(
                    "COMMON reconciliation state changed before durable close"
                )
            self._refresh_run_status(connection, row["run_id"], now=now)
            return self._run_in_transaction(connection, row["run_id"])

    def record_target_failure(
        self,
        run_id: str,
        target_label: str,
        *,
        error: str,
        external_id: str | None = None,
        failure_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one failed attempt without changing its idempotency key."""
        clean_error = _text(error)
        if not clean_error:
            raise ValueError("target failure requires an error")
        clean_error = clean_error[:4000]
        evidence_json = (
            _canonical_json(dict(failure_evidence))
            if failure_evidence is not None
            else None
        )
        evidence_digest = (
            hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
            if evidence_json is not None
            else None
        )
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            clean_external_id = _text(external_id) or None
            if row["status"] == TARGET_FAILED:
                if (
                    row["error"] == clean_error
                    and (row["external_id"] or None) == clean_external_id
                ):
                    return dict(row)
                raise ImmutableReleaseError(
                    "failed target already records a different result"
                )
            self._require_active_run(connection, row["run_id"])
            if row["status"] != TARGET_RUNNING:
                raise ReleaseStoreError(
                    f"target must be RUNNING before failure; found {row['status']}"
                )
            now = _utc_now()
            if evidence_json is not None:
                connection.execute(
                    """
                    INSERT INTO release_target_failure_events (
                        run_id, target_label, attempt, evidence_json,
                        evidence_digest, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["run_id"],
                        row["target_label"],
                        row["attempts"],
                        evidence_json,
                        evidence_digest,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'FAILED', external_id = ?, error = ?,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    clean_external_id,
                    clean_error,
                    now,
                    now,
                    row["run_id"],
                    row["target_label"],
                ),
            )
            self._refresh_run_status(connection, row["run_id"], now=now)
            return dict(
                connection.execute(
                    """
                    SELECT * FROM release_target_runs
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
            )

    def record_target_submission(
        self,
        run_id: str,
        target_label: str,
        *,
        external_id: str,
        submission_evidence: Mapping[str, Any],
        detail: str,
    ) -> dict[str, Any]:
        """Persist one accepted submission that has no authorised API readback.

        This is deliberately not a failure and deliberately not a verified
        success.  The companion receipt makes the target terminal for automatic
        execution while retaining the legacy physical target status required
        by existing SQLite databases.
        """

        clean_external_id = _text(external_id)
        clean_detail = _text(detail)
        if not clean_external_id:
            raise ValueError("accepted submission requires an external_id")
        if not clean_detail:
            raise ValueError("accepted submission requires a detail")
        evidence = dict(submission_evidence)
        if evidence.get("accepted") is not True:
            raise ValueError("submission evidence must record accepted=true")
        evidence_json = _canonical_json(evidence)
        evidence_digest = hashlib.sha256(
            evidence_json.encode("utf-8")
        ).hexdigest()
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            existing = connection.execute(
                """
                SELECT * FROM release_target_submissions
                WHERE run_id = ? AND target_label = ?
                """,
                (row["run_id"], row["target_label"]),
            ).fetchone()
            if existing:
                if (
                    existing["external_id"] != clean_external_id
                    or existing["evidence_digest"] != evidence_digest
                ):
                    raise ImmutableReleaseError(
                        "accepted target already has different submission evidence"
                    )
                run = self._run_in_transaction(connection, row["run_id"])
                return next(
                    target
                    for target in run["targets"]
                    if target["target_label"] == row["target_label"]
                )
            self._require_active_run(connection, row["run_id"])
            if row["status"] != TARGET_RUNNING:
                raise ReleaseStoreError(
                    "target must be RUNNING before accepted submission; "
                    f"found {row['status']}"
                )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_submissions (
                    run_id, target_label, external_id, evidence_json,
                    evidence_digest, status, submitted_at
                ) VALUES (?, ?, ?, ?, ?, 'SUBMITTED_UNVERIFIED', ?)
                """,
                (
                    row["run_id"],
                    row["target_label"],
                    clean_external_id,
                    evidence_json,
                    evidence_digest,
                    now,
                ),
            )
            # Old stores constrain the physical status enum.  FAILED is only a
            # compatibility carrier; _run_in_transaction exposes the truthful
            # SUBMITTED_UNVERIFIED state and retries exclude receipt rows.
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'FAILED', external_id = ?, error = ?,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    clean_external_id,
                    clean_detail[:4000],
                    now,
                    now,
                    row["run_id"],
                    row["target_label"],
                ),
            )
            self._refresh_run_status(connection, row["run_id"], now=now)
            run = self._run_in_transaction(connection, row["run_id"])
            return next(
                target
                for target in run["targets"]
                if target["target_label"] == row["target_label"]
            )

    def record_manual_verification(
        self,
        run_id: str,
        target_label: str,
        *,
        verified_by: str,
        user_verified: bool,
        verification_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Close an API-less target using an explicit Kyle verification."""

        if verified_by != "Kyle" or user_verified is not True:
            raise ReleaseAuthorizationError(
                "manual target verification requires explicit Kyle confirmation"
            )
        evidence = dict(verification_evidence)
        marketplace_product_id = _text(evidence.get("marketplace_product_id"))
        if not marketplace_product_id:
            raise ValueError(
                "manual verification requires the marketplace product ID"
            )
        required_checks = (
            "identity_matches",
            "seller_sku_matches",
            "single_listing_for_sku",
            "title_matches",
            "price_matches",
            "images_match",
            "logistics_match",
        )
        if any(evidence.get(check) is not True for check in required_checks):
            raise ValueError(
                "manual verification requires all listing checks to be true"
            )
        evidence_json = _canonical_json(evidence)
        evidence_digest = hashlib.sha256(
            evidence_json.encode("utf-8")
        ).hexdigest()
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            self._require_active_run(connection, row["run_id"])
            submission = connection.execute(
                """
                SELECT * FROM release_target_submissions
                WHERE run_id = ? AND target_label = ?
                """,
                (row["run_id"], row["target_label"]),
            ).fetchone()
            if not submission:
                raise ReleaseStoreError(
                    "manual verification requires an accepted submission receipt"
                )
            if submission["status"] == TARGET_MANUALLY_VERIFIED:
                if (
                    submission["verified_by"] != verified_by
                    or submission["verification_evidence_digest"]
                    != evidence_digest
                ):
                    raise ImmutableReleaseError(
                        "manual verification is already recorded with different evidence"
                    )
                run = self._run_in_transaction(connection, row["run_id"])
                return next(
                    target
                    for target in run["targets"]
                    if target["target_label"] == row["target_label"]
                )
            now = _utc_now()
            connection.execute(
                """
                UPDATE release_target_submissions
                SET status = 'MANUALLY_VERIFIED', verified_by = ?,
                    verified_at = ?, verification_evidence_json = ?,
                    verification_evidence_digest = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    verified_by,
                    now,
                    evidence_json,
                    evidence_digest,
                    row["run_id"],
                    row["target_label"],
                ),
            )
            run = self._run_in_transaction(connection, row["run_id"])
            return next(
                target
                for target in run["targets"]
                if target["target_label"] == row["target_label"]
            )

    def claim_failed_target_repair(
        self,
        *,
        plan_id: str,
        run_id: str,
        target_label: str,
        external_id: str,
        operation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically claim one exact failed target for a governed repair."""

        clean_plan_id = _text(plan_id)
        clean_run_id = _text(run_id)
        clean_target = _text(target_label)
        clean_external_id = _text(external_id)
        if not all(
            (clean_plan_id, clean_run_id, clean_target, clean_external_id)
        ):
            raise ValueError("target repair identity must be complete")
        operation_payload = dict(operation)
        if operation_payload.get("kind") != "shopee_original_price_repair_v1":
            raise ValueError("unsupported target repair operation")
        if _text(operation_payload.get("plan_id")) != clean_plan_id:
            raise ValueError("target repair plan_id does not match")
        if _text(operation_payload.get("run_id")) != clean_run_id:
            raise ValueError("target repair run_id does not match")
        if _text(operation_payload.get("target_label")) != clean_target:
            raise ValueError("target repair target does not match")
        if _text(operation_payload.get("external_id")) != clean_external_id:
            raise ValueError("target repair external_id does not match")
        operation_json = _canonical_json(operation_payload)
        operation_digest = hashlib.sha256(
            operation_json.encode("utf-8")
        ).hexdigest()
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM release_target_repairs
                WHERE run_id = ? AND target_label = ?
                """,
                (clean_run_id, clean_target),
            ).fetchone()
            if existing:
                if existing["operation_digest"] != operation_digest:
                    raise ImmutableReleaseError(
                        "target repair already has a different operation"
                    )
                if existing["status"] == REPAIR_SUCCEEDED:
                    return {
                        "action": "already_succeeded",
                        "operation_digest": operation_digest,
                        "repair": dict(existing),
                    }
                raise ReleaseStoreError(
                    "target repair is already terminal or awaiting reconciliation"
                )
            plan = connection.execute(
                """
                SELECT plan.status AS plan_status, approval.approval_id,
                       approval.status AS approval_status,
                       approval.approved_by, approval.user_approved
                FROM release_plans AS plan
                JOIN release_approvals AS approval
                  ON approval.plan_id = plan.plan_id
                WHERE plan.plan_id = ?
                """,
                (clean_plan_id,),
            ).fetchone()
            if not plan or (
                plan["plan_status"] != PLAN_APPROVED
                or plan["approval_status"] != PLAN_APPROVED
                or plan["approved_by"] != "Kyle"
                or plan["user_approved"] != 1
            ):
                raise ReleaseAuthorizationError(
                    "target repair requires the active Kyle-approved plan"
                )
            run = connection.execute(
                """
                SELECT * FROM release_runs
                WHERE run_id = ? AND plan_id = ? AND approval_id = ?
                """,
                (clean_run_id, clean_plan_id, plan["approval_id"]),
            ).fetchone()
            if not run or run["status"] in {RUN_SUCCEEDED, SUPERSEDED}:
                raise ReleaseAuthorizationError(
                    "target repair requires the active plan run"
                )
            target = self._target_for_update(
                connection, clean_run_id, clean_target
            )
            if target["status"] != TARGET_FAILED:
                raise ReleaseStoreError(
                    "target repair requires an exact FAILED target"
                )
            if _text(target["external_id"]) != clean_external_id:
                raise ImmutableReleaseError(
                    "target repair external_id does not match the failed target"
                )
            ambiguous_receipt = connection.execute(
                """
                SELECT 1 FROM release_target_submissions
                WHERE run_id = ? AND target_label = ?
                UNION ALL
                SELECT 1 FROM release_target_readbacks
                WHERE run_id = ? AND target_label = ?
                LIMIT 1
                """,
                (clean_run_id, clean_target, clean_run_id, clean_target),
            ).fetchone()
            if ambiguous_receipt:
                raise ReleaseAuthorizationError(
                    "target repair cannot overwrite an existing terminal receipt"
                )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_repairs (
                    run_id, target_label, plan_id, operation_digest,
                    operation_json, external_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)
                """,
                (
                    clean_run_id,
                    clean_target,
                    clean_plan_id,
                    operation_digest,
                    operation_json,
                    clean_external_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'RUNNING', attempts = attempts + 1,
                    error = NULL, completed_at = NULL, updated_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (now, clean_run_id, clean_target),
            )
            connection.execute(
                """
                UPDATE release_runs
                SET status = 'RUNNING', updated_at = ?, completed_at = NULL
                WHERE run_id = ?
                """,
                (now, clean_run_id),
            )
            return {
                "action": "claimed",
                "operation_digest": operation_digest,
                "repair": dict(
                    connection.execute(
                        """
                        SELECT * FROM release_target_repairs
                        WHERE run_id = ? AND target_label = ?
                        """,
                        (clean_run_id, clean_target),
                    ).fetchone()
                ),
            }

    def record_target_repair_success(
        self,
        operation_digest: str,
        *,
        readback_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Close one claimed repair after an exact official readback."""

        evidence = dict(readback_evidence)
        if (
            evidence.get("verified") is not True
            or evidence.get("reconciliation_required") is True
            or evidence.get("external_writes_performed")
            != ["shopee:update_price"]
        ):
            raise ValueError("repair success requires exact verified evidence")
        result_json = _canonical_json(evidence)
        result_digest = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            repair = connection.execute(
                """
                SELECT * FROM release_target_repairs
                WHERE operation_digest = ?
                """,
                (_text(operation_digest),),
            ).fetchone()
            if not repair:
                raise ReleaseStoreError("target repair was not found")
            if repair["status"] == REPAIR_SUCCEEDED:
                if repair["result_digest"] != result_digest:
                    raise ImmutableReleaseError(
                        "target repair already has different success evidence"
                    )
                return self._run_in_transaction(connection, repair["run_id"])
            if repair["status"] != REPAIR_RUNNING:
                raise ReleaseStoreError(
                    "target repair requires reconciliation and cannot succeed"
                )
            target = self._target_for_update(
                connection, repair["run_id"], repair["target_label"]
            )
            if target["status"] != TARGET_RUNNING:
                raise ReleaseStoreError("claimed repair target is not RUNNING")
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_readbacks (
                    run_id, target_label, evidence_json,
                    evidence_digest, verified_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    repair["run_id"],
                    repair["target_label"],
                    result_json,
                    result_digest,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE release_target_repairs
                SET status = 'SUCCEEDED', result_json = ?, result_digest = ?,
                    updated_at = ?, completed_at = ?
                WHERE operation_digest = ?
                """,
                (result_json, result_digest, now, now, operation_digest),
            )
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUCCEEDED', external_id = ?, error = NULL,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    repair["external_id"],
                    now,
                    now,
                    repair["run_id"],
                    repair["target_label"],
                ),
            )
            self._refresh_run_status(connection, repair["run_id"], now=now)
            return self._run_in_transaction(connection, repair["run_id"])

    def record_target_repair_reconciliation(
        self,
        operation_digest: str,
        *,
        error: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Make an ambiguous repair permanently ineligible for auto retry."""

        clean_error = _text(error)[:4000]
        result = dict(evidence)
        if not clean_error or result.get("reconciliation_required") is not True:
            raise ValueError("reconciliation requires an error and evidence")
        result_json = _canonical_json(result)
        result_digest = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            repair = connection.execute(
                """
                SELECT * FROM release_target_repairs
                WHERE operation_digest = ?
                """,
                (_text(operation_digest),),
            ).fetchone()
            if not repair:
                raise ReleaseStoreError("target repair was not found")
            if repair["status"] == REPAIR_RECONCILIATION_REQUIRED:
                if repair["result_digest"] != result_digest:
                    raise ImmutableReleaseError(
                        "target repair already has different reconciliation evidence"
                    )
                return self._run_in_transaction(connection, repair["run_id"])
            if repair["status"] != REPAIR_RUNNING:
                raise ReleaseStoreError("successful target repair is immutable")
            now = _utc_now()
            connection.execute(
                """
                UPDATE release_target_repairs
                SET status = 'RECONCILIATION_REQUIRED',
                    result_json = ?, result_digest = ?,
                    updated_at = ?, completed_at = ?
                WHERE operation_digest = ?
                """,
                (result_json, result_digest, now, now, operation_digest),
            )
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'FAILED', external_id = ?, error = ?,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    repair["external_id"],
                    clean_error,
                    now,
                    now,
                    repair["run_id"],
                    repair["target_label"],
                ),
            )
            self._refresh_run_status(connection, repair["run_id"], now=now)
            return self._run_in_transaction(connection, repair["run_id"])

    def record_target_repair_reconciled_success(
        self,
        operation_digest: str,
        *,
        readback_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Close a prior ambiguous write using a new GET-only exact readback."""

        incoming = dict(readback_evidence)
        checks = incoming.get("checks")
        if (
            incoming.get("verified") is not True
            or incoming.get("reconciliation_required") is True
            or incoming.get("write_status") != "verified"
            or incoming.get("listing_price_verified") is not True
            or incoming.get("financial_verification_status")
            != "price_verified_profit_unverified"
            or incoming.get("profit_status") not in {"unverified", "estimate"}
            or incoming.get("derived_price_status") not in {"warning", "matched"}
            or incoming.get("external_writes_performed") != []
            or not isinstance(checks, dict)
            or not checks
            or any(value is not True for value in checks.values())
        ):
            raise ValueError(
                "GET-only reconciliation requires exact listing evidence "
                "and zero incoming external writes"
            )
        with self._transaction() as connection:
            repair = connection.execute(
                """
                SELECT * FROM release_target_repairs
                WHERE operation_digest = ?
                """,
                (_text(operation_digest),),
            ).fetchone()
            if not repair:
                raise ReleaseStoreError("target repair was not found")
            if repair["status"] != REPAIR_RECONCILIATION_REQUIRED:
                raise ReleaseStoreError(
                    "only a reconciliation-required repair may be closed"
                )
            prior = (
                json.loads(repair["result_json"])
                if repair["result_json"]
                else {}
            )
            if (
                not prior
                or repair["result_digest"]
                != _sha256(prior)
                or prior.get("reconciliation_required") is not True
                or prior.get("external_writes_performed")
                != ["shopee:update_price"]
            ):
                raise ReleaseAuthorizationError(
                    "prior repair write evidence is not exact and truthful"
                )
            merged = {
                **incoming,
                "schema_version": "shopee-price-repair-reconciled/v1",
                "reconciliation_mode": "official_get_only_durable_close",
                "prior_external_write_evidence_digest": repair[
                    "result_digest"
                ],
                "prior_external_writes_performed": [
                    "shopee:update_price"
                ],
                "reconciliation_external_writes_performed": [],
                "external_writes_performed": ["shopee:update_price"],
            }
            result_json = _canonical_json(merged)
            result_digest = hashlib.sha256(
                result_json.encode("utf-8")
            ).hexdigest()
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_readbacks (
                    run_id, target_label, evidence_json,
                    evidence_digest, verified_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    repair["run_id"],
                    repair["target_label"],
                    result_json,
                    result_digest,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE release_target_repairs
                SET status = 'SUCCEEDED', result_json = ?, result_digest = ?,
                    updated_at = ?, completed_at = ?
                WHERE operation_digest = ?
                  AND status = 'RECONCILIATION_REQUIRED'
                """,
                (
                    result_json,
                    result_digest,
                    now,
                    now,
                    operation_digest,
                ),
            )
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUCCEEDED', external_id = ?, error = NULL,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                  AND status = 'FAILED'
                """,
                (
                    repair["external_id"],
                    now,
                    now,
                    repair["run_id"],
                    repair["target_label"],
                ),
            )
            if connection.total_changes < 3:
                raise ReleaseStoreError(
                    "reconciliation target state changed before durable close"
                )
            self._refresh_run_status(connection, repair["run_id"], now=now)
            return self._run_in_transaction(connection, repair["run_id"])

    def target_scoped_reconciliation_context(
        self,
        *,
        plan_id: str,
        target_label: str,
    ) -> dict[str, Any]:
        """Return one exact existing-operation GET-only close identity."""

        from shared_platform.target_scoped_release_contracts import (
            TargetScopedOperationRequest,
            TargetScopedReconciliationRequest,
            original_target_proof_evidence,
            planned_target_command,
        )

        if not self.path.is_file():
            raise ReleaseStoreError("release store was not found")
        with self._connect_readonly() as connection:
            row = connection.execute(
                """
                SELECT
                    plan.*, approval.approval_id,
                    approval.payload_digest AS approval_payload_digest,
                    approval.confirmation_token AS approval_confirmation_token,
                    approval.approved_by, approval.user_approved,
                    approval.status AS approval_status,
                    run.run_id, run.status AS run_status,
                    target.idempotency_key,
                    target.status AS target_status,
                    target.attempts,
                    target.external_id AS target_external_id,
                    target.error AS target_error
                FROM release_plans AS plan
                JOIN release_approvals AS approval
                  ON approval.plan_id = plan.plan_id
                JOIN release_runs AS run
                  ON run.plan_id = plan.plan_id
                 AND run.approval_id = approval.approval_id
                JOIN release_target_runs AS target
                  ON target.run_id = run.run_id
                WHERE plan.plan_id = ? AND target.target_label = ?
                """,
                (_text(plan_id), _text(target_label)),
            ).fetchone()
            if not row:
                raise ReleaseStoreError(
                    "target-scoped reconciliation context was not found"
                )
            operation_row = connection.execute(
                """
                SELECT * FROM release_target_retry_operations
                WHERE run_id = ? AND target_label = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (row["run_id"], _text(target_label)),
            ).fetchone()
            if not operation_row:
                raise ReleaseStoreError(
                    "target-scoped reconciliation operation was not found"
                )
            operation = _target_scoped_operation_from_row(operation_row)
            proof_row = connection.execute(
                """
                SELECT proof_digest, proof_json, status, operation_digest
                FROM release_target_retry_proofs
                WHERE proof_digest = ?
                """,
                (operation["proof_digest"],),
            ).fetchone()
            plan = _plan_from_row(row)
            payload = plan.get("payload") or {}
            blockers: list[str] = []
            if (
                row["status"] != PLAN_APPROVED
                or row["approval_status"] != PLAN_APPROVED
                or row["approved_by"] != "Kyle"
                or not (
                    row["user_approved"] is True
                    or (
                        type(row["user_approved"]) is int
                        and row["user_approved"] == 1
                    )
                )
                or row["run_status"] == SUPERSEDED
            ):
                blockers.append(
                    "reconciliation requires the active Kyle-approved plan"
                )
            if target_label not in {"shopee:MY", "shopee:VN"}:
                blockers.append(
                    "GET-only reconciliation target is not allowlisted"
                )
            external_id = _text(row["target_external_id"])
            if not external_id:
                blockers.append(
                    "reconciliation target requires an external_id"
                )
            if operation.get("external_id") != external_id:
                blockers.append(
                    "operation and target external identity differ"
                )
            if operation.get("operation_kind") != (
                "shopee_safe_pre_submit_retry_v1"
            ):
                blockers.append(
                    "operation kind is not a Shopee scoped publish"
                )
            stored_request = operation.get("request") or {}
            if (
                stored_request.get("product_revision")
                != payload.get("product_revision")
            ):
                blockers.append(
                    "stored operation revision differs from immutable plan"
                )
            proof_evidence = None
            if not proof_row:
                blockers.append("stored operation proof was not found")
            else:
                try:
                    proof_payload = json.loads(proof_row["proof_json"])
                except (TypeError, ValueError):
                    proof_payload = {}
                if (
                    proof_row["status"] != TARGET_SCOPED_PROOF_CONSUMED
                    or proof_row["operation_digest"]
                    != operation["operation_digest"]
                    or proof_payload.get("proof_digest")
                    != operation["proof_digest"]
                    or proof_payload.get("plan_id") != plan["plan_id"]
                    or proof_payload.get("run_id") != row["run_id"]
                    or proof_payload.get("target_label") != target_label
                    or proof_payload.get("preflight_digest")
                    != stored_request.get("preflight_digest")
                ):
                    blockers.append(
                        "stored operation proof identity is invalid"
                    )
                else:
                    try:
                        proof_evidence = original_target_proof_evidence(
                            proof_payload
                        )
                    except (TypeError, ValueError) as error:
                        blockers.append(str(error))
            try:
                current_command, current_command_digest = (
                    planned_target_command(
                        payload,
                        target_label=target_label,
                    )
                )
                base_request = TargetScopedOperationRequest(
                    plan_id=str(plan.get("plan_id") or ""),
                    confirmation_token=str(
                        plan.get("confirmation_token") or ""
                    ),
                    approval_scope_digest=str(
                        payload.get("omnichannel_scope_digest") or ""
                    ),
                    product_id=str(plan.get("product_id") or ""),
                    seller_sku=str(plan.get("seller_sku") or ""),
                    product_package_id=str(
                        plan.get("product_package_id") or ""
                    ),
                    content_package_id=str(
                        plan.get("content_package_id") or ""
                    ),
                    run_id=str(row["run_id"] or ""),
                    target_label=str(target_label),
                    operation_kind=str(
                        operation.get("operation_kind") or ""
                    ),
                    product_revision=stored_request.get(
                        "product_revision"
                    ),
                    payload_digest=str(
                        plan.get("payload_digest") or ""
                    ),
                    planned_command=current_command,
                    planned_command_digest=current_command_digest,
                    preflight_digest=str(
                        stored_request.get("preflight_digest") or ""
                    ),
                    failure_attempt=stored_request.get(
                        "failure_attempt"
                    ),
                    failure_digest=str(
                        stored_request.get("failure_digest") or ""
                    ),
                    target_idempotency_key=str(
                        row["idempotency_key"] or ""
                    ),
                    approved_by="Kyle",
                )
                if base_request.durable_identity() != stored_request:
                    blockers.append(
                        "stored operation no longer matches immutable plan"
                    )
                if (
                    base_request.operation_digest(
                        str(operation.get("proof_digest") or "")
                    )
                    != operation.get("operation_digest")
                ):
                    blockers.append(
                        "stored operation digest is invalid"
                    )
            except (TypeError, ValueError) as error:
                base_request = None
                blockers.append(str(error))
            result = operation.get("result")
            if (
                not isinstance(result, dict)
                or not operation.get("result_digest")
                or _sha256(result) != operation.get("result_digest")
            ):
                blockers.append(
                    "operation result evidence digest is invalid"
                )
                result = {}
            already_succeeded = (
                operation.get("status")
                == TARGET_SCOPED_OPERATION_SUCCEEDED
            )
            if already_succeeded:
                if (
                    row["target_status"] != TARGET_SUCCEEDED
                    or result.get("schema_version")
                    != "target-scoped-reconciled-result/v1"
                    or result.get("reconciliation_mode")
                    != "official_get_only_durable_close"
                    or result.get("prior_external_writes_performed")
                    != ["shopee:regional_publish"]
                    or result.get(
                        "reconciliation_external_writes_performed"
                    )
                    != []
                ):
                    blockers.append(
                        "stored reconciled success evidence is incomplete"
                    )
                prior_result_digest = _text(
                    result.get("prior_result_digest")
                )
            else:
                if operation.get("status") != (
                    TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED
                ):
                    blockers.append(
                        "operation must require reconciliation"
                    )
                if row["target_status"] != TARGET_FAILED:
                    blockers.append(
                        "physical target must remain FAILED before close"
                    )
                if (
                    result.get("outcome")
                    != TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED
                    or result.get("reconciliation_required") is not True
                    or result.get("external_writes_performed")
                    != ["shopee:regional_publish"]
                    or (
                        (result.get("evidence") or {}).get(
                            "external_writes_performed"
                        )
                        != ["shopee:regional_publish"]
                    )
                    or _text(result.get("external_reference"))
                    != external_id
                ):
                    blockers.append(
                        "truthful prior Shopee publish evidence is incomplete"
                    )
                prior_result_digest = _text(
                    operation.get("result_digest")
                )
            reconciliation_request = None
            if (
                base_request is not None
                and prior_result_digest
                and proof_evidence is not None
            ):
                try:
                    reconciliation_request = (
                        TargetScopedReconciliationRequest(
                            operation_request=base_request,
                            operation_digest=str(
                                operation.get("operation_digest") or ""
                            ),
                            operation_proof_digest=str(
                                operation.get("proof_digest") or ""
                            ),
                            prior_result_digest=prior_result_digest,
                            external_id=external_id,
                            publication_targets=tuple(
                                plan.get("targets") or ()
                            ),
                            original_proof_evidence=proof_evidence,
                        )
                    )
                except (TypeError, ValueError) as error:
                    blockers.append(str(error))
            return {
                "eligible": not blockers,
                "blockers": blockers,
                "plan": plan,
                "approval": {
                    "approval_id": row["approval_id"],
                    "status": row["approval_status"],
                    "approved_by": row["approved_by"],
                    "user_approved": (
                        row["user_approved"] is True
                        or (
                            type(row["user_approved"]) is int
                            and row["user_approved"] == 1
                        )
                    ),
                },
                "run_id": row["run_id"],
                "run_status": row["run_status"],
                "target_label": target_label,
                "target_status": row["target_status"],
                "target_attempts": int(row["attempts"] or 0),
                "target_external_id": external_id,
                "operation": operation,
                "reconciliation_request": reconciliation_request,
                "already_succeeded": already_succeeded,
            }

    def record_target_scoped_reconciled_success(
        self,
        *,
        request,
        proof,
        result,
    ) -> dict[str, Any]:
        """Atomically close an ambiguous scoped write after exact GET-only proof."""

        from shared_platform.target_scoped_release_contracts import (
            OfficialTargetReconciliationProof,
            TargetScopedOperationResult,
            TargetScopedReconciliationRequest,
            original_target_proof_evidence,
        )

        if not isinstance(request, TargetScopedReconciliationRequest):
            raise TypeError(
                "target-scoped reconciliation request is required"
            )
        normalized_proof = (
            OfficialTargetReconciliationProof.from_value(
                (
                    proof.durable_payload()
                    if isinstance(
                        proof, OfficialTargetReconciliationProof
                    )
                    else proof
                ),
                request=request,
            )
        )
        normalized_result = TargetScopedOperationResult.from_value(result)
        checks = normalized_result.evidence.get("checks")
        if (
            normalized_result.outcome != TARGET_SCOPED_OPERATION_SUCCEEDED
            or normalized_result.external_reference != request.external_id
            or normalized_result.external_writes_performed != []
            or normalized_result.evidence.get("verified") is not True
            or normalized_result.evidence.get("reconciliation_mode")
            != "official_get_only_durable_close"
            or not isinstance(checks, dict)
            or not checks
            or any(value is not True for value in checks.values())
        ):
            raise ValueError(
                "GET-only reconciliation requires exact zero-write readback"
            )
        with self._transaction() as connection:
            operation = self._target_scoped_operation_for_update(
                connection, request.operation_digest
            )
            if operation["status"] == TARGET_SCOPED_OPERATION_SUCCEEDED:
                stored = (
                    json.loads(operation["result_json"])
                    if operation["result_json"]
                    else {}
                )
                if (
                    stored.get("schema_version")
                    != "target-scoped-reconciled-result/v1"
                    or stored.get("reconciliation_proof_digest")
                    != normalized_proof.proof_digest
                    or stored.get("prior_result_digest")
                    != request.prior_result_digest
                ):
                    raise ImmutableReleaseError(
                        "reconciled target already has different evidence"
                    )
                return self._run_in_transaction(
                    connection, operation["run_id"]
                )
            if operation["status"] != (
                TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED
            ):
                raise ReleaseStoreError(
                    "only a reconciliation-required operation may close"
                )
            if (
                operation["proof_digest"]
                != request.operation_proof_digest
                or operation["result_digest"]
                != request.prior_result_digest
                or operation["external_id"] != request.external_id
                or operation["request_json"]
                != _canonical_json(
                    request.operation_request.durable_identity()
                )
            ):
                raise ImmutableReleaseError(
                    "target-scoped reconciliation identity changed"
                )
            proof_row = connection.execute(
                """
                SELECT proof_json, status, operation_digest
                FROM release_target_retry_proofs
                WHERE proof_digest = ?
                """,
                (request.operation_proof_digest,),
            ).fetchone()
            if not proof_row:
                raise ReleaseAuthorizationError(
                    "original target-scoped proof was not found"
                )
            try:
                original_proof = json.loads(proof_row["proof_json"])
            except (TypeError, ValueError) as error:
                raise ReleaseAuthorizationError(
                    "original target-scoped proof is invalid"
                ) from error
            if (
                proof_row["status"] != TARGET_SCOPED_PROOF_CONSUMED
                or proof_row["operation_digest"]
                != request.operation_digest
                or original_proof.get("proof_digest")
                != request.operation_proof_digest
                or original_proof.get("preflight_digest")
                != request.operation_request.preflight_digest
            ):
                raise ReleaseAuthorizationError(
                    "original target-scoped proof identity changed"
                )
            try:
                current_proof_evidence = (
                    original_target_proof_evidence(original_proof)
                )
            except (TypeError, ValueError) as error:
                raise ReleaseAuthorizationError(
                    "original target-scoped proof evidence is invalid"
                ) from error
            if (
                current_proof_evidence
                != dict(request.original_proof_evidence)
                or request.original_proof_evidence_digest
                != _sha256(current_proof_evidence)
            ):
                raise ReleaseAuthorizationError(
                    "original target-scoped proof evidence changed"
                )
            prior = (
                json.loads(operation["result_json"])
                if operation["result_json"]
                else {}
            )
            if (
                not prior
                or _sha256(prior) != operation["result_digest"]
                or prior.get("outcome")
                != TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED
                or prior.get("reconciliation_required") is not True
                or prior.get("external_writes_performed")
                != ["shopee:regional_publish"]
                or (
                    (prior.get("evidence") or {}).get(
                        "external_writes_performed"
                    )
                    != ["shopee:regional_publish"]
                )
                or _text(prior.get("external_reference"))
                != request.external_id
            ):
                raise ReleaseAuthorizationError(
                    "prior scoped publish evidence is not exact and truthful"
                )
            target = self._target_for_update(
                connection,
                operation["run_id"],
                operation["target_label"],
            )
            if (
                target["status"] != TARGET_FAILED
                or _text(target["external_id"]) != request.external_id
            ):
                raise ReleaseStoreError(
                    "physical target changed before GET-only close"
                )
            merged_evidence = {
                **dict(normalized_result.evidence),
                "reconciliation_mode": (
                    "official_get_only_durable_close"
                ),
                "reconciliation_proof_digest": (
                    normalized_proof.proof_digest
                ),
                "prior_external_write_evidence_digest": (
                    request.prior_result_digest
                ),
                "prior_external_writes_performed": [
                    "shopee:regional_publish"
                ],
                "reconciliation_external_writes_performed": [],
                "external_writes_performed": [
                    "shopee:regional_publish"
                ],
            }
            merged = {
                "schema_version": "target-scoped-reconciled-result/v1",
                "reconciliation_mode": (
                    "official_get_only_durable_close"
                ),
                "succeeded": True,
                "readback_verified": True,
                "detail": normalized_result.detail,
                "external_reference": request.external_id,
                "submission_accepted": (
                    prior.get("submission_accepted") is True
                ),
                "evidence": merged_evidence,
                "external_writes_performed": [
                    "shopee:regional_publish"
                ],
                "prior_external_writes_performed": [
                    "shopee:regional_publish"
                ],
                "reconciliation_external_writes_performed": [],
                "prior_result_digest": request.prior_result_digest,
                "reconciliation_proof_digest": (
                    normalized_proof.proof_digest
                ),
                "outcome": TARGET_SCOPED_OPERATION_SUCCEEDED,
            }
            result_json = _canonical_json(merged)
            result_digest = _sha256(merged)
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_readbacks (
                    run_id, target_label, evidence_json,
                    evidence_digest, verified_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    operation["run_id"],
                    operation["target_label"],
                    result_json,
                    result_digest,
                    now,
                ),
            )
            updated_operation = connection.execute(
                """
                UPDATE release_target_retry_operations
                SET status = 'SUCCEEDED', result_json = ?,
                    result_digest = ?, updated_at = ?, completed_at = ?
                WHERE operation_digest = ?
                  AND status = 'RECONCILIATION_REQUIRED'
                """,
                (
                    result_json,
                    result_digest,
                    now,
                    now,
                    request.operation_digest,
                ),
            )
            updated_target = connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUCCEEDED', error = NULL,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                  AND status = 'FAILED' AND external_id = ?
                """,
                (
                    now,
                    now,
                    operation["run_id"],
                    operation["target_label"],
                    request.external_id,
                ),
            )
            if (
                updated_operation.rowcount != 1
                or updated_target.rowcount != 1
            ):
                raise ReleaseStoreError(
                    "target-scoped reconciliation lost atomic transition"
                )
            self._refresh_run_status(
                connection, operation["run_id"], now=now
            )
            return self._run_in_transaction(
                connection, operation["run_id"]
            )

    def target_scoped_action_context(
        self,
        *,
        plan_id: str,
        target_label: str,
    ) -> dict[str, Any]:
        """Return the exact write-free identity for one governed target action."""

        if not self.path.is_file():
            raise ReleaseStoreError("release store was not found")
        with self._connect_readonly() as connection:
            return self._target_scoped_context_in_transaction(
                connection,
                plan_id=_text(plan_id),
                target_label=_text(target_label),
            )

    def get_target_scoped_operation(
        self,
        *,
        run_id: str,
        target_label: str,
    ) -> dict[str, Any] | None:
        """Read the latest target-scoped operation without creating schema."""

        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT * FROM release_target_retry_operations
                    WHERE run_id = ? AND target_label = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (_text(run_id), _text(target_label)),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
            return _target_scoped_operation_from_row(row) if row else None

    def claim_target_scoped_operation(
        self,
        *,
        request,
        proof,
    ) -> dict[str, Any]:
        """Consume one official proof and claim one FAILED target atomically.

        The physical target remains FAILED. Generic publication therefore never
        observes a retry PENDING row; the companion operation row is the source
        of truth while the single-target adapter is running.
        """

        from shared_platform.target_scoped_release_contracts import (
            OfficialTargetProof,
            TargetScopedOperationRequest,
        )

        if not isinstance(request, TargetScopedOperationRequest):
            raise TypeError(
                "target-scoped claim requires TargetScopedOperationRequest"
            )
        normalized_proof = OfficialTargetProof.from_value(
            (
                proof.durable_payload()
                if isinstance(proof, OfficialTargetProof)
                else proof
            ),
            request=request,
        )
        operation_digest = request.operation_digest(
            normalized_proof.proof_digest
        )
        request_json = _canonical_json(request.durable_identity())
        proof_json = _canonical_json(normalized_proof.durable_payload())

        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM release_target_retry_operations
                WHERE operation_digest = ?
                """,
                (operation_digest,),
            ).fetchone()
            if existing:
                if (
                    existing["request_json"] != request_json
                    or existing["proof_digest"] != normalized_proof.proof_digest
                ):
                    raise ImmutableReleaseError(
                        "target-scoped operation identity changed"
                    )
                if existing["status"] == TARGET_SCOPED_OPERATION_SUCCEEDED:
                    return {
                        "action": "already_succeeded",
                        "operation": _target_scoped_operation_from_row(
                            existing
                        ),
                    }
                raise ReleaseStoreError(
                    "target-scoped operation is already running or terminal"
                )

            context = self._target_scoped_context_in_transaction(
                connection,
                plan_id=request.plan_id,
                target_label=request.target_label,
            )
            if not context["eligible"]:
                raise ReleaseAuthorizationError(
                    "target-scoped action is blocked: "
                    + "; ".join(context["blockers"])
                )
            exact = {
                "run_id": request.run_id,
                "operation_kind": request.operation_kind,
                "product_revision": request.product_revision,
                "payload_digest": request.payload_digest,
                "planned_command": dict(request.planned_command),
                "planned_command_digest": (
                    request.planned_command_digest
                ),
                "preflight_digest": request.preflight_digest,
                "failure_attempt": request.failure_attempt,
                "failure_digest": request.failure_digest,
                "target_idempotency_key": request.target_idempotency_key,
            }
            actual = {field: context[field] for field in exact}
            if actual != exact:
                raise ImmutableReleaseError(
                    "target-scoped failure identity changed before claim"
                )
            plan = context["plan"]
            approval = context["approval"]
            if (
                request.confirmation_token
                != plan.get("confirmation_token")
                or request.confirmation_token
                != approval.get("confirmation_token")
                or request.confirmation_token_digest
                != request.durable_identity()["confirmation_token_digest"]
                or request.approval_scope_digest
                != str(
                    (plan.get("payload") or {}).get(
                        "omnichannel_scope_digest"
                    )
                    or ""
                )
                or request.product_id != plan.get("product_id")
                or request.seller_sku != plan.get("seller_sku")
                or request.product_package_id
                != plan.get("product_package_id")
                or request.content_package_id
                != plan.get("content_package_id")
                or request.approved_by != "Kyle"
                or approval.get("approved_by") != "Kyle"
                or approval.get("user_approved") is not True
            ):
                raise ReleaseAuthorizationError(
                    "target-scoped action authority does not match the active plan"
                )

            proof_row = connection.execute(
                """
                SELECT * FROM release_target_retry_proofs
                WHERE proof_digest = ?
                """,
                (normalized_proof.proof_digest,),
            ).fetchone()
            if proof_row:
                if proof_row["proof_json"] != proof_json:
                    raise ImmutableReleaseError(
                        "official target proof already has different evidence"
                    )
                if proof_row["status"] != TARGET_SCOPED_PROOF_AVAILABLE:
                    raise ReleaseAuthorizationError(
                        "official target proof was already consumed"
                    )
            else:
                now = _utc_now()
                connection.execute(
                    """
                    INSERT INTO release_target_retry_proofs (
                        proof_digest, plan_id, run_id, target_label,
                        operation_kind, product_revision, payload_digest,
                        preflight_digest, failure_attempt, failure_digest,
                        proof_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', ?)
                    """,
                    (
                        normalized_proof.proof_digest,
                        request.plan_id,
                        request.run_id,
                        request.target_label,
                        request.operation_kind,
                        request.product_revision,
                        request.payload_digest,
                        request.preflight_digest,
                        request.failure_attempt,
                        request.failure_digest,
                        proof_json,
                        now,
                    ),
                )

            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_retry_operations (
                    operation_digest, proof_digest, plan_id, run_id,
                    target_label, operation_kind, request_json, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)
                """,
                (
                    operation_digest,
                    normalized_proof.proof_digest,
                    request.plan_id,
                    request.run_id,
                    request.target_label,
                    request.operation_kind,
                    request_json,
                    now,
                    now,
                ),
            )
            consumed = connection.execute(
                """
                UPDATE release_target_retry_proofs
                SET status = 'CONSUMED', consumed_at = ?,
                    operation_digest = ?
                WHERE proof_digest = ? AND status = 'AVAILABLE'
                """,
                (
                    now,
                    operation_digest,
                    normalized_proof.proof_digest,
                ),
            )
            if consumed.rowcount != 1:
                raise ReleaseAuthorizationError(
                    "official target proof could not be consumed exactly once"
                )
            claimed = connection.execute(
                """
                UPDATE release_target_runs
                SET attempts = attempts + 1, updated_at = ?
                WHERE run_id = ? AND target_label = ? AND status = 'FAILED'
                """,
                (now, request.run_id, request.target_label),
            )
            if claimed.rowcount != 1:
                raise ReleaseAuthorizationError(
                    "FAILED target changed before atomic claim"
                )
            connection.execute(
                """
                UPDATE release_runs
                SET status = 'RUNNING', updated_at = ?, completed_at = NULL
                WHERE run_id = ?
                """,
                (now, request.run_id),
            )
            operation = connection.execute(
                """
                SELECT * FROM release_target_retry_operations
                WHERE operation_digest = ?
                """,
                (operation_digest,),
            ).fetchone()
            return {
                "action": "claimed",
                "operation": _target_scoped_operation_from_row(operation),
            }

    def record_target_scoped_success(
        self,
        operation_digest: str,
        *,
        result,
    ) -> dict[str, Any]:
        """Atomically close one claimed target after exact official readback."""

        from shared_platform.target_scoped_release_contracts import (
            TargetScopedOperationResult,
        )

        normalized = TargetScopedOperationResult.from_value(result)
        if normalized.outcome != TARGET_SCOPED_OPERATION_SUCCEEDED:
            raise ValueError(
                "target-scoped success requires exact verified evidence"
            )
        if not normalized.external_reference:
            raise ValueError(
                "target-scoped success requires an official external identity"
            )
        payload = normalized.durable_payload()
        result_json = _canonical_json(payload)
        result_digest = _sha256(payload)
        with self._transaction() as connection:
            operation = self._target_scoped_operation_for_update(
                connection, operation_digest
            )
            if operation["status"] == TARGET_SCOPED_OPERATION_SUCCEEDED:
                if operation["result_digest"] != result_digest:
                    raise ImmutableReleaseError(
                        "target-scoped success evidence changed"
                    )
                return self._run_in_transaction(
                    connection, operation["run_id"]
                )
            if operation["status"] != TARGET_SCOPED_OPERATION_RUNNING:
                raise ReleaseStoreError(
                    "target-scoped operation is terminal and cannot succeed"
                )
            target = self._target_for_update(
                connection,
                operation["run_id"],
                operation["target_label"],
            )
            if target["status"] != TARGET_FAILED:
                raise ReleaseStoreError(
                    "claimed target is no longer physically FAILED"
                )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_readbacks (
                    run_id, target_label, evidence_json,
                    evidence_digest, verified_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    operation["run_id"],
                    operation["target_label"],
                    result_json,
                    result_digest,
                    now,
                ),
            )
            updated_operation = connection.execute(
                """
                UPDATE release_target_retry_operations
                SET status = 'SUCCEEDED', external_id = ?,
                    result_json = ?, result_digest = ?,
                    updated_at = ?, completed_at = ?
                WHERE operation_digest = ? AND status = 'RUNNING'
                """,
                (
                    normalized.external_reference,
                    result_json,
                    result_digest,
                    now,
                    now,
                    operation["operation_digest"],
                ),
            )
            updated_target = connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUCCEEDED', external_id = ?, error = NULL,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ? AND status = 'FAILED'
                """,
                (
                    normalized.external_reference,
                    now,
                    now,
                    operation["run_id"],
                    operation["target_label"],
                ),
            )
            if (
                updated_operation.rowcount != 1
                or updated_target.rowcount != 1
            ):
                raise ReleaseStoreError(
                    "target-scoped success lost its atomic state transition"
                )
            self._refresh_run_status(
                connection, operation["run_id"], now=now
            )
            return self._run_in_transaction(
                connection, operation["run_id"]
            )

    def record_target_scoped_pre_submit_failure(
        self,
        operation_digest: str,
        *,
        result,
    ) -> dict[str, Any]:
        """Record an explicit zero-write pre-submit failure without PENDING."""

        from shared_platform.target_scoped_release_contracts import (
            TargetScopedOperationResult,
        )

        normalized = TargetScopedOperationResult.from_value(result)
        if normalized.outcome != TARGET_SCOPED_OPERATION_FAILED_PRE_SUBMIT:
            raise ValueError(
                "pre-submit failure requires explicit zero-write evidence"
            )
        return self._record_target_scoped_terminal_failure(
            operation_digest,
            normalized=normalized,
            status=TARGET_SCOPED_OPERATION_FAILED_PRE_SUBMIT,
        )

    def record_target_scoped_reconciliation(
        self,
        operation_digest: str,
        *,
        result,
    ) -> dict[str, Any]:
        """Persist a potentially written or unknown result and forbid replay."""

        from shared_platform.target_scoped_release_contracts import (
            TargetScopedOperationResult,
        )

        normalized = TargetScopedOperationResult.from_value(result)
        if normalized.outcome != (
            TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED
        ):
            raise ValueError(
                "reconciliation requires an ambiguous or external outcome"
            )
        return self._record_target_scoped_terminal_failure(
            operation_digest,
            normalized=normalized,
            status=TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED,
        )

    def _record_target_scoped_terminal_failure(
        self,
        operation_digest: str,
        *,
        normalized,
        status: str,
    ) -> dict[str, Any]:
        payload = normalized.durable_payload()
        payload["reconciliation_required"] = (
            status
            == TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED
        )
        result_json = _canonical_json(payload)
        result_digest = _sha256(payload)
        with self._transaction() as connection:
            operation = self._target_scoped_operation_for_update(
                connection, operation_digest
            )
            if operation["status"] == status:
                if operation["result_digest"] != result_digest:
                    raise ImmutableReleaseError(
                        "target-scoped terminal evidence changed"
                    )
                return self._run_in_transaction(
                    connection, operation["run_id"]
                )
            if operation["status"] != TARGET_SCOPED_OPERATION_RUNNING:
                raise ReleaseStoreError(
                    "target-scoped operation is already terminal"
                )
            target = self._target_for_update(
                connection,
                operation["run_id"],
                operation["target_label"],
            )
            if target["status"] != TARGET_FAILED:
                raise ReleaseStoreError(
                    "claimed target is no longer physically FAILED"
                )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_failure_events (
                    run_id, target_label, attempt, evidence_json,
                    evidence_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    operation["run_id"],
                    operation["target_label"],
                    target["attempts"],
                    result_json,
                    result_digest,
                    now,
                ),
            )
            updated_operation = connection.execute(
                """
                UPDATE release_target_retry_operations
                SET status = ?, external_id = ?, result_json = ?,
                    result_digest = ?, updated_at = ?, completed_at = ?
                WHERE operation_digest = ? AND status = 'RUNNING'
                """,
                (
                    status,
                    normalized.external_reference,
                    result_json,
                    result_digest,
                    now,
                    now,
                    operation["operation_digest"],
                ),
            )
            updated_target = connection.execute(
                """
                UPDATE release_target_runs
                SET external_id = COALESCE(?, external_id), error = ?,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ? AND status = 'FAILED'
                """,
                (
                    normalized.external_reference,
                    normalized.detail[:4000],
                    now,
                    now,
                    operation["run_id"],
                    operation["target_label"],
                ),
            )
            if (
                updated_operation.rowcount != 1
                or updated_target.rowcount != 1
            ):
                raise ReleaseStoreError(
                    "target-scoped failure lost its atomic state transition"
                )
            self._refresh_run_status(
                connection, operation["run_id"], now=now
            )
            return self._run_in_transaction(
                connection, operation["run_id"]
            )

    def _target_scoped_operation_for_update(
        self,
        connection: sqlite3.Connection,
        operation_digest: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM release_target_retry_operations
            WHERE operation_digest = ?
            """,
            (_text(operation_digest),),
        ).fetchone()
        if not row:
            raise ReleaseStoreError(
                "target-scoped operation was not found"
            )
        return row

    def _target_scoped_context_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        plan_id: str,
        target_label: str,
    ) -> dict[str, Any]:
        from shared_platform.target_scoped_release_contracts import (
            operation_kind_for_target,
            planned_target_command,
            target_failure_digest,
            target_preflight_digest,
        )

        operation_kind = operation_kind_for_target(target_label)
        row = connection.execute(
            """
            SELECT
                plan.*, approval.approval_id,
                approval.payload_digest AS approval_payload_digest,
                approval.confirmation_token AS approval_confirmation_token,
                approval.approved_by, approval.user_approved,
                approval.status AS approval_status,
                run.run_id, run.status AS run_status,
                target.idempotency_key, target.status AS target_status,
                target.attempts, target.external_id AS target_external_id,
                target.error AS target_error
            FROM release_plans AS plan
            JOIN release_approvals AS approval
              ON approval.plan_id = plan.plan_id
            JOIN release_runs AS run
              ON run.plan_id = plan.plan_id
             AND run.approval_id = approval.approval_id
            JOIN release_target_runs AS target
              ON target.run_id = run.run_id
            WHERE plan.plan_id = ? AND target.target_label = ?
            """,
            (_text(plan_id), _text(target_label)),
        ).fetchone()
        if not row:
            raise ReleaseStoreError(
                "active release target context was not found"
            )
        if (
            row["status"] != PLAN_APPROVED
            or row["approval_status"] != PLAN_APPROVED
            or row["approved_by"] != "Kyle"
            or row["user_approved"] != 1
            or row["run_status"] in {RUN_SUCCEEDED, SUPERSEDED}
        ):
            raise ReleaseAuthorizationError(
                "target-scoped action requires the active Kyle-approved run"
            )
        failure_rows = connection.execute(
            """
            SELECT attempt, evidence_json, evidence_digest, created_at
            FROM release_target_failure_events
            WHERE run_id = ? AND target_label = ?
            ORDER BY attempt
            """,
            (row["run_id"], _text(target_label)),
        ).fetchall()
        failure_digests = [
            str(item["evidence_digest"] or "") for item in failure_rows
        ]
        failure_identity = target_failure_digest(
            target_label=target_label,
            attempts=int(row["attempts"] or 0),
            error=row["target_error"],
            failure_event_digests=failure_digests,
        )
        plan = _plan_from_row(row)
        payload = plan.get("payload") or {}
        product_revision = payload.get("product_revision")
        if (
            isinstance(product_revision, bool)
            or not isinstance(product_revision, int)
            or product_revision < 0
        ):
            raise ReleaseAuthorizationError(
                "immutable plan product_revision is invalid"
            )
        planned_command, planned_command_digest = planned_target_command(
            payload,
            target_label=target_label,
        )
        preflight = target_preflight_digest(
            plan_id=plan["plan_id"],
            run_id=row["run_id"],
            target_label=target_label,
            operation_kind=operation_kind,
            product_revision=product_revision,
            payload_digest=plan["payload_digest"],
            planned_command_digest=planned_command_digest,
            failure_attempt=int(row["attempts"] or 0),
            failure_digest=failure_identity,
            target_idempotency_key=row["idempotency_key"],
        )
        blockers: list[str] = []
        if row["target_status"] != TARGET_FAILED:
            blockers.append(
                f"target must be physically FAILED; found {row['target_status']}"
            )
        if _text(row["target_external_id"]):
            blockers.append("target already records an external_id")
        terminal = connection.execute(
            """
            SELECT 'submission' AS kind
            FROM release_target_submissions
            WHERE run_id = ? AND target_label = ?
            UNION ALL
            SELECT 'readback' AS kind
            FROM release_target_readbacks
            WHERE run_id = ? AND target_label = ?
            UNION ALL
            SELECT 'repair' AS kind
            FROM release_target_repairs
            WHERE run_id = ? AND target_label = ?
            LIMIT 1
            """,
            (
                row["run_id"],
                target_label,
                row["run_id"],
                target_label,
                row["run_id"],
                target_label,
            ),
        ).fetchone()
        if terminal:
            blockers.append(
                f"target already records {terminal['kind']} evidence"
            )
        for failure in failure_rows:
            evidence = json.loads(failure["evidence_json"])
            if (
                evidence.get("external_writes_performed")
                or evidence.get("submission_accepted") is True
                or evidence.get("accepted") is True
                or evidence.get("durable_state_uncertain") is True
                or _text(evidence.get("external_id"))
                or _text(evidence.get("external_reference"))
            ):
                blockers.append(
                    "prior failure evidence is not safely pre-submit"
                )
                break
        try:
            operation_row = connection.execute(
                """
                SELECT * FROM release_target_retry_operations
                WHERE run_id = ? AND target_label = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (row["run_id"], target_label),
            ).fetchone()
        except sqlite3.OperationalError:
            operation_row = None
        operation = (
            _target_scoped_operation_from_row(operation_row)
            if operation_row
            else None
        )
        operation_contract_stale = False
        if operation:
            stored_request = operation.get("request") or {}
            operation_contract_stale = (
                stored_request.get("planned_command_digest")
                != planned_command_digest
                or stored_request.get("payload_digest")
                != plan["payload_digest"]
            )
            blockers.append(
                (
                    "target already has a stale target-scoped contract"
                    if operation_contract_stale
                    else (
                        "target already has operation status "
                        f"{operation['status']}"
                    )
                )
            )
        approval = {
            "approval_id": row["approval_id"],
            "plan_id": row["plan_id"],
            "payload_digest": row["approval_payload_digest"],
            "confirmation_token": row["approval_confirmation_token"],
            "approved_by": row["approved_by"],
            "user_approved": (
                row["user_approved"] is True
                or (
                    type(row["user_approved"]) is int
                    and row["user_approved"] == 1
                )
            ),
            "status": row["approval_status"],
        }
        return {
            "plan": plan,
            "approval": approval,
            "run_id": row["run_id"],
            "run_status": row["run_status"],
            "target_label": target_label,
            "target_status": row["target_status"],
            "target_idempotency_key": row["idempotency_key"],
            "failure_attempt": int(row["attempts"] or 0),
            "failure_digest": failure_identity,
            "operation_kind": operation_kind,
            "product_revision": product_revision,
            "payload_digest": plan["payload_digest"],
            "planned_command": planned_command,
            "planned_command_digest": planned_command_digest,
            "preflight_digest": preflight,
            "operation": operation,
            "operation_contract_stale": operation_contract_stale,
            "eligible": not blockers,
            "blockers": blockers,
        }

    def retry_failed_targets(
        self,
        run_id: str,
        target_labels: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Reset only failed targets; successful external results are retained."""
        from shared_platform.target_scoped_release_contracts import (
            TARGET_SCOPED_OPERATION_KINDS,
        )

        with self._transaction() as connection:
            self._require_active_run(connection, _text(run_id))
            scoped_failed_rows = connection.execute(
                """
                SELECT target_label
                FROM release_target_runs
                WHERE run_id = ? AND status = 'FAILED'
                ORDER BY target_label
                """,
                (_text(run_id),),
            ).fetchall()
            scoped_failed = {
                row["target_label"]
                for row in scoped_failed_rows
                if row["target_label"] in TARGET_SCOPED_OPERATION_KINDS
            }
            requested = (
                None
                if target_labels is None
                else {_text(label) for label in target_labels}
            )
            blocked = (
                scoped_failed
                if requested is None
                else scoped_failed.intersection(requested)
            )
            if blocked:
                raise ReleaseAuthorizationError(
                    "target-scoped action required for FAILED targets: "
                    + ", ".join(sorted(blocked))
                )
            failed_rows = connection.execute(
                """
                SELECT target.target_label
                FROM release_target_runs AS target
                LEFT JOIN release_target_submissions AS submission
                  ON submission.run_id = target.run_id
                 AND submission.target_label = target.target_label
                LEFT JOIN release_target_repairs AS repair
                  ON repair.run_id = target.run_id
                 AND repair.target_label = target.target_label
                WHERE target.run_id = ?
                  AND target.status = 'FAILED'
                  AND submission.run_id IS NULL
                  AND repair.run_id IS NULL
                ORDER BY target.target_label
                """,
                (_text(run_id),),
            ).fetchall()
            failed = {row["target_label"] for row in failed_rows}
            if not failed:
                raise ReleaseStoreError("release run has no failed targets to retry")
            if requested is None:
                selected = failed
            else:
                selected = requested
                if not selected or not selected.issubset(failed):
                    raise ReleaseStoreError(
                        "retry targets must be a non-empty subset of failed targets"
                    )
            now = _utc_now()
            placeholders = ",".join("?" for _ in selected)
            connection.execute(
                f"""
                UPDATE release_target_runs
                SET status = 'PENDING', error = NULL,
                    updated_at = ?, completed_at = NULL
                WHERE run_id = ? AND target_label IN ({placeholders})
                """,
                (now, _text(run_id), *sorted(selected)),
            )
            self._refresh_run_status(connection, _text(run_id), now=now)
            return self._run_in_transaction(connection, _text(run_id))

    def recover_interrupted_targets(
        self,
        run_id: str,
        target_labels: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Return explicitly selected stale RUNNING targets to PENDING.

        This is only a crash-recovery control-plane operation.  Marketplace
        adapters must still perform an idempotent read-back before any new
        submission, so recovery cannot duplicate an already-created listing.
        """

        with self._transaction() as connection:
            self._require_active_run(connection, _text(run_id))
            running_rows = connection.execute(
                """
                SELECT target.target_label
                FROM release_target_runs AS target
                LEFT JOIN release_target_repairs AS repair
                  ON repair.run_id = target.run_id
                 AND repair.target_label = target.target_label
                WHERE target.run_id = ? AND target.status = 'RUNNING'
                  AND repair.run_id IS NULL
                ORDER BY target.target_label
                """,
                (_text(run_id),),
            ).fetchall()
            running = {row["target_label"] for row in running_rows}
            if not running:
                raise ReleaseStoreError("release run has no interrupted targets")
            if target_labels is None:
                selected = running
            else:
                selected = {_text(label) for label in target_labels}
                if not selected or not selected.issubset(running):
                    raise ReleaseStoreError(
                        "recovery targets must be a non-empty subset of RUNNING targets"
                    )
            now = _utc_now()
            placeholders = ",".join("?" for _ in selected)
            connection.execute(
                f"""
                UPDATE release_target_runs
                SET status = 'PENDING',
                    error = 'recovered after an interrupted worker',
                    updated_at = ?, completed_at = NULL
                WHERE run_id = ? AND target_label IN ({placeholders})
                """,
                (now, _text(run_id), *sorted(selected)),
            )
            self._refresh_run_status(connection, _text(run_id), now=now)
            return self._run_in_transaction(connection, _text(run_id))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    "SELECT run_id FROM release_runs WHERE run_id = ?",
                    (_text(run_id),),
                ).fetchone()
                return (
                    self._run_in_transaction(connection, row["run_id"])
                    if row
                    else None
                )
            except sqlite3.OperationalError:
                return None

    def target_repair_confirmation_matches(
        self,
        *,
        run_id: str,
        target_label: str,
        plan_id: str,
        expected_revision: int,
        payload_digest: str,
        preflight_digest: str,
    ) -> dict[str, Any] | None:
        """Compare a repeat request with the immutable repair operation."""

        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT operation_digest, operation_json, status
                    FROM release_target_repairs
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (_text(run_id), _text(target_label)),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        operation = json.loads(row["operation_json"])
        expected = {
            "plan_id": _text(plan_id),
            "run_id": _text(run_id),
            "target_label": _text(target_label),
            "expected_revision": int(expected_revision),
            "payload_digest": _text(payload_digest),
            "preflight_digest": _text(preflight_digest),
        }
        actual = {
            field: (
                int(operation.get(field) or 0)
                if field == "expected_revision"
                else _text(operation.get(field))
            )
            for field in expected
        }
        return {
            "matches": actual == expected,
            "status": row["status"],
            "operation_digest": row["operation_digest"],
        }

    def target_repair_reconciliation_context(
        self,
        *,
        run_id: str,
        target_label: str,
        plan_id: str,
        expected_revision: int,
        payload_digest: str,
        preflight_digest: str,
        operation_digest: str | None = None,
    ) -> dict[str, Any] | None:
        """Return exact internal repair identity for a GET-only close."""

        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT repair.*, target.status AS target_status,
                           target.external_id AS target_external_id,
                           run.plan_id AS run_plan_id,
                           approval.status AS approval_status,
                           approval.approved_by,
                           approval.user_approved
                    FROM release_target_repairs AS repair
                    JOIN release_target_runs AS target
                      ON target.run_id = repair.run_id
                     AND target.target_label = repair.target_label
                    JOIN release_runs AS run
                      ON run.run_id = repair.run_id
                    JOIN release_approvals AS approval
                      ON approval.approval_id = run.approval_id
                    WHERE repair.run_id = ? AND repair.target_label = ?
                    """,
                    (_text(run_id), _text(target_label)),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        if not row:
            return None
        operation = json.loads(row["operation_json"])
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        requested_preflight_digest = (
            _text(preflight_digest)
            or _text(operation.get("preflight_digest"))
        )
        expected = {
            "plan_id": _text(plan_id),
            "run_id": _text(run_id),
            "target_label": _text(target_label),
            "expected_revision": int(expected_revision),
            "payload_digest": _text(payload_digest),
            "preflight_digest": requested_preflight_digest,
        }
        actual = {
            field: (
                int(operation.get(field) or 0)
                if field == "expected_revision"
                else _text(operation.get(field))
            )
            for field in expected
        }
        exact_operation_digest = _text(operation_digest)
        if (
            actual != expected
            or (
                exact_operation_digest
                and exact_operation_digest != row["operation_digest"]
            )
            or row["plan_id"] != _text(plan_id)
            or row["run_plan_id"] != _text(plan_id)
            or row["target_status"] != TARGET_FAILED
            or row["status"] != REPAIR_RECONCILIATION_REQUIRED
            or _text(row["target_external_id"]) != _text(row["external_id"])
            or row["approval_status"] != PLAN_APPROVED
            or row["approved_by"] != "Kyle"
            or row["user_approved"] != 1
            or not result
            or row["result_digest"] != _sha256(result)
            or result.get("reconciliation_required") is not True
            or result.get("external_writes_performed")
            != ["shopee:update_price"]
        ):
            return None
        return {
            "operation_digest": row["operation_digest"],
            "operation": operation,
            "prior_result_digest": row["result_digest"],
            "status": row["status"],
        }

    def supersede_plan(
        self,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None = None,
        reason: str = "release plan inputs changed",
    ) -> dict[str, Any]:
        """Invalidate approval and unfinished execution without deleting history."""
        with self._transaction() as connection:
            successor = _text(superseded_by_plan_id) or None
            if successor:
                exists = connection.execute(
                    "SELECT 1 FROM release_plans WHERE plan_id = ?",
                    (successor,),
                ).fetchone()
                if not exists:
                    raise ReleaseStoreError("successor release plan was not found")
            self._supersede_in_transaction(
                connection,
                _text(plan_id),
                superseded_by_plan_id=successor,
                reason=_text(reason) or "release plan inputs changed",
                now=_utc_now(),
            )
            row = connection.execute(
                "SELECT * FROM release_plans WHERE plan_id = ?",
                (_text(plan_id),),
            ).fetchone()
            return _plan_from_row(row)

    def reset_unexecuted_test_product(self, product_id: str) -> dict[str, Any]:
        """Release local reservations only when no publication run ever began.

        Immutable plans and approvals are retained as superseded audit facts.
        Once a run exists, callers must use platform-aware rollback/reconciliation
        instead of pretending that deleting local state erased an external write.
        """

        clean_product_id = _text(product_id)
        if not clean_product_id:
            raise ValueError("product_id is required")
        if not self.path.is_file():
            return {"superseded_plan_count": 0, "released_source_reservation_count": 0}
        with self._transaction() as connection:
            plans = connection.execute(
                """
                SELECT * FROM release_plans
                WHERE product_id = ? AND status != 'SUPERSEDED'
                ORDER BY created_at, plan_id
                """,
                (clean_product_id,),
            ).fetchall()
            plan_ids = [row["plan_id"] for row in plans]
            if plan_ids:
                placeholders = ",".join("?" for _ in plan_ids)
                started = connection.execute(
                    f"""
                    SELECT run_id FROM release_runs
                    WHERE plan_id IN ({placeholders})
                    LIMIT 1
                    """,
                    tuple(plan_ids),
                ).fetchone()
                if started:
                    raise ReleaseAuthorizationError(
                        "test offer already has a publication run; platform-aware rollback is required"
                    )
            now = _utc_now()
            reservation_digests: set[str] = set()
            for plan_id in plan_ids:
                linked = connection.execute(
                    """
                    SELECT reservation_digest
                    FROM release_source_sku_plan_links
                    WHERE plan_id = ?
                    """,
                    (plan_id,),
                ).fetchone()
                if linked:
                    reservation_digests.add(linked["reservation_digest"])
                self._supersede_in_transaction(
                    connection,
                    plan_id,
                    superseded_by_plan_id=None,
                    reason="test offer removed from Product Center queue",
                    now=now,
                )
            released_source_count = 0
            for digest in reservation_digests:
                active_consumer = connection.execute(
                    """
                    SELECT 1
                    FROM release_source_sku_plan_links AS link
                    JOIN release_plans AS plan ON plan.plan_id = link.plan_id
                    WHERE link.reservation_digest = ?
                      AND plan.status != 'SUPERSEDED'
                    LIMIT 1
                    """,
                    (digest,),
                ).fetchone()
                if active_consumer:
                    continue
                changed = connection.execute(
                    """
                    UPDATE release_source_sku_reservations
                    SET status = 'SUPERSEDED', updated_at = ?
                    WHERE reservation_digest = ? AND status = 'ACTIVE'
                    """,
                    (now, digest),
                ).rowcount
                connection.execute(
                    """
                    UPDATE release_source_sku_reservation_keys
                    SET status = 'SUPERSEDED', updated_at = ?
                    WHERE reservation_digest = ? AND status = 'ACTIVE'
                    """,
                    (now, digest),
                )
                released_source_count += int(bool(changed))
            return {
                "superseded_plan_count": len(plan_ids),
                "released_source_reservation_count": released_source_count,
            }

    def active_sku_reservations(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._connect_readonly() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM release_sku_reservations
                    WHERE status = 'ACTIVE'
                    ORDER BY sku_key, plan_id
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]

    def active_reserved_sku_keys(self) -> tuple[str, ...]:
        """Return every active key owned by legacy and source-lineage plans.

        ``release_sku_reservations`` predates multi-variant source lineage and
        normally contains only the base Seller SKU.  The complete namespace
        for a source-lineage reservation lives in
        ``release_source_sku_reservation_keys``.  Allocation must consume both
        ledgers or later products can reuse an already-owned model SKU.
        """

        if not self.path.is_file():
            return ()
        with self._connect_readonly() as connection:
            keys: set[str] = set()
            for table in (
                "release_sku_reservations",
                "release_source_sku_reservation_keys",
            ):
                try:
                    rows = connection.execute(
                        f"SELECT sku_key FROM {table} WHERE status = 'ACTIVE'"
                    ).fetchall()
                except sqlite3.OperationalError:
                    continue
                keys.update(
                    str(row["sku_key"] or "").strip()
                    for row in rows
                    if str(row["sku_key"] or "").strip()
                )
        return tuple(sorted(keys))

    def database_health(self) -> dict[str, Any]:
        """Return read-only SQLite integrity evidence for operations/tests."""
        if not self.path.is_file():
            return {"exists": False}
        with self._connect_readonly() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = [
                tuple(row) for row in connection.execute("PRAGMA foreign_key_check")
            ]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        return {
            "exists": True,
            "integrity_check": integrity,
            "foreign_key_violations": foreign_keys,
            "busy_timeout": busy_timeout,
        }

    def _target_for_update(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        target_label: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM release_target_runs
            WHERE run_id = ? AND target_label = ?
            """,
            (_text(run_id), _text(target_label)),
        ).fetchone()
        if not row:
            raise ReleaseStoreError("release target run was not found")
        return row

    def _require_active_run(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT r.*, p.status AS plan_status
            FROM release_runs AS r
            JOIN release_plans AS p ON p.plan_id = r.plan_id
            WHERE r.run_id = ?
            """,
            (_text(run_id),),
        ).fetchone()
        if not row:
            raise ReleaseStoreError("release run was not found")
        if row["plan_status"] != PLAN_APPROVED or row["status"] == SUPERSEDED:
            raise ReleaseAuthorizationError("release run belongs to a superseded plan")
        if row["status"] == RUN_SUCCEEDED:
            raise ReleaseStoreError("release run is already complete")
        return row

    def _run_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> dict[str, Any]:
        run = connection.execute(
            "SELECT * FROM release_runs WHERE run_id = ?",
            (_text(run_id),),
        ).fetchone()
        if not run:
            raise ReleaseStoreError("release run was not found")
        targets = connection.execute(
            """
            SELECT * FROM release_target_runs
            WHERE run_id = ?
            ORDER BY rowid
            """,
            (run["run_id"],),
        ).fetchall()
        try:
            readback_rows = connection.execute(
                """
                SELECT target_label, evidence_json, evidence_digest, verified_at
                FROM release_target_readbacks
                WHERE run_id = ?
                """,
                (run["run_id"],),
            )
            readbacks = {
                row["target_label"]: {
                    "evidence": json.loads(row["evidence_json"]),
                    "evidence_digest": row["evidence_digest"],
                    "verified_at": row["verified_at"],
                }
                for row in readback_rows
            }
        except sqlite3.OperationalError:
            # Old stores remain readable before the next controlled write
            # creates the additive evidence table.
            readbacks = {}
        try:
            failure_rows = connection.execute(
                """
                SELECT target_label, attempt, evidence_json,
                       evidence_digest, created_at
                FROM release_target_failure_events
                WHERE run_id = ?
                ORDER BY target_label, attempt
                """,
                (run["run_id"],),
            )
            failure_events: dict[str, list[dict[str, Any]]] = {}
            for row in failure_rows:
                failure_events.setdefault(row["target_label"], []).append(
                    {
                        "attempt": row["attempt"],
                        "evidence": json.loads(row["evidence_json"]),
                        "evidence_digest": row["evidence_digest"],
                        "created_at": row["created_at"],
                    }
                )
        except sqlite3.OperationalError:
            failure_events = {}
        try:
            submission_rows = connection.execute(
                """
                SELECT target_label, external_id, evidence_json,
                       evidence_digest, status, submitted_at, verified_by,
                       verified_at, verification_evidence_json,
                       verification_evidence_digest
                FROM release_target_submissions
                WHERE run_id = ?
                """,
                (run["run_id"],),
            )
            submissions = {
                row["target_label"]: {
                    "external_id": row["external_id"],
                    "evidence": json.loads(row["evidence_json"]),
                    "evidence_digest": row["evidence_digest"],
                    "status": row["status"],
                    "submitted_at": row["submitted_at"],
                    "verified_by": row["verified_by"],
                    "verified_at": row["verified_at"],
                    "verification_evidence": (
                        json.loads(row["verification_evidence_json"])
                        if row["verification_evidence_json"]
                        else None
                    ),
                    "verification_evidence_digest": row[
                        "verification_evidence_digest"
                    ],
                }
                for row in submission_rows
            }
        except sqlite3.OperationalError:
            submissions = {}
        try:
            repair_rows = connection.execute(
                """
                SELECT target_label, operation_digest, external_id, status,
                       result_json, result_digest, created_at, updated_at,
                       completed_at
                FROM release_target_repairs
                WHERE run_id = ?
                """,
                (run["run_id"],),
            )
            repairs = {
                row["target_label"]: {
                    "operation_digest": row["operation_digest"],
                    "external_id": row["external_id"],
                    "status": row["status"],
                    "result": (
                        json.loads(row["result_json"])
                        if row["result_json"]
                        else None
                    ),
                    "result_digest": row["result_digest"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "completed_at": row["completed_at"],
                }
                for row in repair_rows
            }
        except sqlite3.OperationalError:
            repairs = {}
        try:
            operation_rows = connection.execute(
                """
                SELECT *
                FROM release_target_retry_operations
                WHERE run_id = ?
                ORDER BY created_at
                """,
                (run["run_id"],),
            )
            target_scoped_operations: dict[str, dict[str, Any]] = {}
            for operation_row in operation_rows:
                target_scoped_operations[
                    operation_row["target_label"]
                ] = _target_scoped_operation_from_row(operation_row)
        except sqlite3.OperationalError:
            target_scoped_operations = {}
        target_payloads: list[dict[str, Any]] = []
        for row in targets:
            payload = dict(row)
            payload["storage_status"] = payload["status"]
            payload["readback"] = readbacks.get(row["target_label"])
            payload["failure_events"] = list(
                failure_events.get(row["target_label"]) or ()
            )
            payload["latest_failure_evidence"] = (
                payload["failure_events"][-1]
                if payload["failure_events"]
                else None
            )
            payload["submission"] = (
                submissions.get(row["target_label"])
                or _legacy_unverified_submission(payload)
            )
            if payload["submission"]:
                payload["status"] = payload["submission"]["status"]
            payload["repair"] = repairs.get(row["target_label"])
            if payload["repair"]:
                if (
                    payload["repair"]["status"]
                    == REPAIR_RECONCILIATION_REQUIRED
                ):
                    payload["status"] = TARGET_RECONCILIATION_REQUIRED
                elif payload["repair"]["status"] == REPAIR_RUNNING:
                    payload["status"] = TARGET_RUNNING
            payload["target_scoped_operation"] = (
                target_scoped_operations.get(row["target_label"])
            )
            if payload["target_scoped_operation"]:
                operation_status = payload["target_scoped_operation"]["status"]
                if operation_status == TARGET_SCOPED_OPERATION_RUNNING:
                    payload["status"] = TARGET_RUNNING
                elif operation_status == (
                    TARGET_SCOPED_OPERATION_RECONCILIATION_REQUIRED
                ):
                    payload["status"] = TARGET_RECONCILIATION_REQUIRED
            target_payloads.append(payload)
        result = {**dict(run), "targets": target_payloads}
        logical_statuses = [target["status"] for target in target_payloads]
        success_statuses = {TARGET_SUCCEEDED, TARGET_MANUALLY_VERIFIED}
        if logical_statuses and all(
            status in success_statuses for status in logical_statuses
        ):
            result["status"] = (
                RUN_COMPLETED_WITH_MANUAL_VERIFICATION
                if TARGET_MANUALLY_VERIFIED in logical_statuses
                else RUN_SUCCEEDED
            )
            result["completed_at"] = max(
                str(target.get("completed_at") or "")
                for target in target_payloads
            ) or result.get("completed_at")
        elif (
            TARGET_SUBMITTED_UNVERIFIED in logical_statuses
            and not any(
                status in {TARGET_PENDING, TARGET_RUNNING, TARGET_FAILED}
                for status in logical_statuses
            )
        ):
            result["status"] = RUN_AWAITING_MANUAL_VERIFICATION
        elif (
            TARGET_FAILED in logical_statuses
            or TARGET_RECONCILIATION_REQUIRED in logical_statuses
        ):
            completed = sum(
                status in {
                    TARGET_SUCCEEDED,
                    TARGET_SUBMITTED_UNVERIFIED,
                    TARGET_MANUALLY_VERIFIED,
                }
                for status in logical_statuses
            )
            result["status"] = RUN_PARTIAL_FAILED if completed else RUN_FAILED
        return result

    def _refresh_run_status(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        now: str,
    ) -> None:
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM release_target_runs WHERE run_id = ?",
                (run_id,),
            )
        ]
        if statuses and all(status == TARGET_SUCCEEDED for status in statuses):
            status = RUN_SUCCEEDED
            completed_at = now
        elif TARGET_FAILED in statuses and TARGET_SUCCEEDED in statuses:
            status = RUN_PARTIAL_FAILED
            completed_at = None
        elif TARGET_FAILED in statuses:
            status = RUN_FAILED
            completed_at = None
        elif TARGET_RUNNING in statuses or TARGET_SUCCEEDED in statuses:
            status = RUN_RUNNING
            completed_at = None
        else:
            status = RUN_PENDING
            completed_at = None
        connection.execute(
            """
            UPDATE release_runs
            SET status = ?, updated_at = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (status, now, completed_at, run_id),
        )

    def _supersede_in_transaction(
        self,
        connection: sqlite3.Connection,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None,
        reason: str,
        now: str,
    ) -> None:
        plan = connection.execute(
            "SELECT * FROM release_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if not plan:
            raise ReleaseStoreError("release plan was not found")
        if plan["status"] == SUPERSEDED:
            same_successor = (
                (plan["superseded_by_plan_id"] or None)
                == (superseded_by_plan_id or None)
            )
            if same_successor:
                return
            raise ImmutableReleaseError(
                "release plan was already superseded by another successor"
            )
        running_target = connection.execute(
            """
            SELECT target.target_label
            FROM release_runs AS run
            JOIN release_target_runs AS target
              ON target.run_id = run.run_id
            WHERE run.plan_id = ?
              AND target.status = 'RUNNING'
            LIMIT 1
            """,
            (plan_id,),
        ).fetchone()
        if running_target:
            raise ReleaseAuthorizationError(
                "release plan cannot be superseded while target "
                f"{running_target['target_label']} is RUNNING"
            )
        connection.execute(
            """
            UPDATE release_plans
            SET status = 'SUPERSEDED', superseded_at = ?,
                superseded_by_plan_id = ?, supersede_reason = ?
            WHERE plan_id = ?
            """,
            (now, superseded_by_plan_id, reason[:1000], plan_id),
        )
        connection.execute(
            """
            UPDATE release_approvals
            SET status = 'SUPERSEDED', superseded_at = ?
            WHERE plan_id = ? AND status = 'APPROVED'
            """,
            (now, plan_id),
        )
        run_rows = connection.execute(
            """
            SELECT run_id FROM release_runs
            WHERE plan_id = ? AND status != 'SUCCEEDED'
            """,
            (plan_id,),
        ).fetchall()
        for run in run_rows:
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUPERSEDED', updated_at = ?, completed_at = ?
                WHERE run_id = ? AND status != 'SUCCEEDED'
                """,
                (now, now, run["run_id"]),
            )
            connection.execute(
                """
                UPDATE release_runs
                SET status = 'SUPERSEDED', updated_at = ?, completed_at = ?
                WHERE run_id = ?
                """,
                (now, now, run["run_id"]),
            )
        connection.execute(
            """
            UPDATE release_sku_reservations
            SET status = 'SUPERSEDED', updated_at = ?, released_at = ?
            WHERE plan_id = ? AND status = 'ACTIVE'
            """,
            (now, now, plan_id),
        )


def default_release_store() -> ReleaseStore:
    """Return the production-path store without opening or creating it."""
    return ReleaseStore(DEFAULT_RELEASE_STORE_PATH)
