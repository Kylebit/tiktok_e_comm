"""Durable, prepare-first control plane for one-click storefront releases.

This module owns no marketplace client.  Channel operations register two
separate callables:

* ``prepare`` performs official read-only proof and builds a command from the
  immutable ReleasePlan.
* ``dispatch`` consumes the exact stored command/proof identity.

All selected targets are prepared before the first canonical target claim.
The public projections intentionally omit commands, tokens, source IDs,
marketplace IDs, copy, URLs and raw responses.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from shared_platform.release_store import ReleaseStore


PREPARED_COMMAND_SCHEMA = "release-target-prepared-command/v2"
BATCH_PREPARATION_SCHEMA = "release-batch-preparation/v2"
PUBLIC_STATUS_SCHEMA = "oneclick-release-status/v2"
REGISTRY_SCHEMA = "release-adapter-registry/v1"
DEPENDENCY_POLICY_VERSION = "oneclick-target-dependency/v1"
SHARED_RESOURCE_SCHEMA = "oneclick-shared-resource/v1"
MANUAL_ACCEPTANCE_RESOLUTION_SCHEMA = (
    "release-outcome-manual-acceptance/v1"
)
SHOPEE_GLOBAL_MASTER_POLICY = "shopee-global-master/v1"
SHOPEE_GLOBAL_TARGET = "shopee:GLOBAL"
SHOPEE_IMAGE_UPLOAD_WRITE = "shopee:image:upload"
SHOPEE_GLOBAL_WRITE = "shopee:global_master:create"
SHOPEE_GLOBAL_MODEL_WRITE = "shopee:global_model:init"
SHOPEE_GLOBAL_WRITE_CLASSES = (
    SHOPEE_IMAGE_UPLOAD_WRITE,
    SHOPEE_GLOBAL_WRITE,
    SHOPEE_GLOBAL_MODEL_WRITE,
)

EXACT_READY_AUTOMATIC = "EXACT_READY_AUTOMATIC"
READY_SUBMIT_MANUAL = "READY_SUBMIT_MANUAL"
BLOCKED_AUTH = "BLOCKED_AUTH"
BLOCKED_INVENTORY = "BLOCKED_INVENTORY"
BLOCKED_CAPABILITY = "BLOCKED_CAPABILITY"
BLOCKED_SOURCE_IDENTITY = "BLOCKED_SOURCE_IDENTITY"
BLOCKED_SKU_LINEAGE = "BLOCKED_SKU_LINEAGE"
SAFE_ACTION_REQUIRED = "SAFE_ACTION_REQUIRED"
CAPABILITY_CLASSIFICATIONS = frozenset(
    {
        EXACT_READY_AUTOMATIC,
        READY_SUBMIT_MANUAL,
        BLOCKED_AUTH,
        BLOCKED_INVENTORY,
        BLOCKED_CAPABILITY,
        BLOCKED_SOURCE_IDENTITY,
        BLOCKED_SKU_LINEAGE,
        SAFE_ACTION_REQUIRED,
    }
)
READY_CLASSIFICATIONS = frozenset(
    {EXACT_READY_AUTOMATIC, READY_SUBMIT_MANUAL}
)

PENDING = "PENDING"
PREPARING = "PREPARING"
READY = "READY"
DISPATCHING = "DISPATCHING"
SUCCEEDED = "SUCCEEDED"
SUCCEEDED_MANUAL_REVIEW = "SUCCEEDED_MANUAL_REVIEW"
SUBMITTED_UNVERIFIED = "SUBMITTED_UNVERIFIED"
FAILED_PRE_SUBMIT = "FAILED_PRE_SUBMIT"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
PUBLIC_CANONICAL_STATUSES = frozenset(
    {
        PENDING,
        PREPARING,
        READY,
        DISPATCHING,
        SUCCEEDED,
        SUCCEEDED_MANUAL_REVIEW,
        SUBMITTED_UNVERIFIED,
        FAILED_PRE_SUBMIT,
        RECONCILIATION_REQUIRED,
        BLOCKED_AUTH,
        BLOCKED_INVENTORY,
        BLOCKED_CAPABILITY,
        BLOCKED_SOURCE_IDENTITY,
        BLOCKED_SKU_LINEAGE,
    }
)

TARGET_REASON_SCOPE = "TARGET"
SYSTEMIC_IDENTITY_SCOPE = "SYSTEMIC_IDENTITY"
REASON_SCOPES = frozenset({TARGET_REASON_SCOPE, SYSTEMIC_IDENTITY_SCOPE})
REASON_CATEGORIES = frozenset(
    {
        "AUTH",
        "INVENTORY",
        "CAPABILITY",
        "CONTENT",
        "LOGISTICS",
        "SAFE_ACTION",
        "PRE_SUBMIT",
        "POST_WRITE",
        "DEPENDENCY",
        "SYSTEMIC_IDENTITY",
        "SYSTEMIC_CONTRACT",
    }
)

_TERMINAL_TARGET_STATUSES = frozenset(
    {
        SUCCEEDED,
        SUCCEEDED_MANUAL_REVIEW,
        SUBMITTED_UNVERIFIED,
        FAILED_PRE_SUBMIT,
        RECONCILIATION_REQUIRED,
        BLOCKED_AUTH,
        BLOCKED_INVENTORY,
        BLOCKED_CAPABILITY,
        BLOCKED_SOURCE_IDENTITY,
        BLOCKED_SKU_LINEAGE,
    }
)
_JOB_TERMINAL_STATUSES = frozenset(
    {"SUCCEEDED", "WAITING_MANUAL_ACCEPTANCE", "BLOCKED", "SYSTEMIC_STOPPED"}
)
_COMMON_LABEL = "miaoshou:COMMON"
_UNKNOWN_WRITE_CLASS = "UNKNOWN"


class OneClickControlPlaneError(RuntimeError):
    """Base error for the one-click control plane."""


class SystemicIdentityError(OneClickControlPlaneError):
    """The immutable plan/run/batch identity drifted."""


class AdapterContractError(OneClickControlPlaneError):
    """A dynamic adapter violated the prepare/dispatch contract."""


class AdapterPreparationError(OneClickControlPlaneError):
    """A typed read-only preparation failure."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        category: str = "CAPABILITY",
        scope: str = TARGET_REASON_SCOPE,
    ) -> None:
        super().__init__(detail)
        self.code = _clean_code(code, "adapter_prepare_failed")
        self.detail = _clean_detail(detail)
        self.category = _reason_category(category)
        self.scope = _reason_scope(scope)


class DispatchInvocationError(OneClickControlPlaneError):
    """Typed 03 failure preserving every write already performed."""

    def __init__(
        self,
        detail: str,
        *,
        external_writes: tuple[str, ...] = (),
        dispatch_outcome_unknown: bool = False,
        external_id: str | None = None,
        external_write_count: int | None = None,
        confirmed_external_write_count_lower_bound: int = 0,
        possible_external_write_count_upper_bound: int | None = None,
    ) -> None:
        clean_detail = _exact_text(detail, "dispatch invocation detail")
        writes = _validated_write_classes(external_writes)
        if type(dispatch_outcome_unknown) is not bool:
            raise AdapterContractError(
                "dispatch_outcome_unknown must be a literal bool"
            )
        if external_id is not None and (
            type(external_id) is not str
            or not external_id
            or external_id != external_id.strip()
        ):
            raise AdapterContractError(
                "dispatch invocation external_id is invalid"
            )
        super().__init__(clean_detail)
        self.external_writes = writes
        self.dispatch_outcome_unknown = dispatch_outcome_unknown
        self.external_id = external_id
        (
            self.external_write_count,
            self.confirmed_external_write_count_lower_bound,
            self.possible_external_write_count_upper_bound,
        ) = _validated_write_count_bounds(
            writes,
            external_write_count,
            confirmed_external_write_count_lower_bound,
            possible_external_write_count_upper_bound,
        )


class PreDispatchInvocationError(OneClickControlPlaneError):
    """Dedicated proof that dispatch stopped before any merchant invocation."""

    def __init__(self, detail: str) -> None:
        clean_detail = _exact_text(detail, "pre-dispatch detail")
        super().__init__(clean_detail)


@dataclass(frozen=True)
class PrepareTargetRequest:
    schema_version: str
    plan_id: str
    run_id: str
    target_label: str
    product_revision: int
    payload_digest: str
    confirmation_token_digest: str
    targets_digest: str
    source_identity_digest: str
    source_identity_payload_digest: str
    source_identity: Mapping[str, Any]
    sku_lineage_digest: str
    sku_lineage_payload_digest: str
    adapter_policy_digest: str
    idempotency_key: str
    immutable_plan_payload: Mapping[str, Any]


@dataclass(frozen=True)
class PrepareTargetResult:
    classification: str
    reason_category: str
    reason_scope: str
    reason_code: str
    reason_detail: str
    command: Mapping[str, Any] | None = None
    proof: Mapping[str, Any] | None = None
    shared_resource: Mapping[str, Any] | None = None
    manual_after_submit: bool = False

    @classmethod
    def from_value(cls, value: object) -> "PrepareTargetResult":
        if isinstance(value, cls):
            result = value
        elif isinstance(value, Mapping):
            manual_after_submit = value.get("manual_after_submit", False)
            if type(manual_after_submit) is not bool:
                raise AdapterContractError(
                    "prepare manual_after_submit must be a literal bool"
                )
            for field in ("command", "proof", "shared_resource"):
                if value.get(field) is not None and not isinstance(
                    value.get(field),
                    Mapping,
                ):
                    raise AdapterContractError(
                        f"prepare {field} must be a mapping or null"
                    )
            result = cls(
                classification=_exact_text(
                    value.get("classification"), "prepare classification"
                ),
                reason_category=_exact_text(
                    value.get("reason_category"), "prepare reason_category"
                ),
                reason_scope=_exact_text(
                    value.get("reason_scope"), "prepare reason_scope"
                ),
                reason_code=_exact_text(
                    value.get("reason_code"), "prepare reason_code"
                ),
                reason_detail=_exact_text(
                    value.get("reason_detail"), "prepare reason_detail"
                ),
                command=(
                    dict(value["command"])
                    if isinstance(value.get("command"), Mapping)
                    else None
                ),
                proof=(
                    dict(value["proof"])
                    if isinstance(value.get("proof"), Mapping)
                    else None
                ),
                shared_resource=(
                    dict(value["shared_resource"])
                    if isinstance(value.get("shared_resource"), Mapping)
                    else None
                ),
                manual_after_submit=manual_after_submit,
            )
        else:
            raise AdapterContractError("prepare must return PrepareTargetResult")
        if type(result.manual_after_submit) is not bool:
            raise AdapterContractError(
                "prepare manual_after_submit must be a literal bool"
            )
        if result.classification not in CAPABILITY_CLASSIFICATIONS:
            raise AdapterContractError("prepare classification is invalid")
        _reason_category(result.reason_category)
        _reason_scope(result.reason_scope)
        _clean_code(result.reason_code, "adapter_prepare_result")
        _clean_detail(result.reason_detail)
        if result.classification in READY_CLASSIFICATIONS:
            if not isinstance(result.command, Mapping) or not result.command:
                raise AdapterContractError("ready preparation requires command")
            if not isinstance(result.proof, Mapping) or not result.proof:
                raise AdapterContractError("ready preparation requires proof")
            expected_manual = result.classification == READY_SUBMIT_MANUAL
            if result.manual_after_submit is not expected_manual:
                raise AdapterContractError(
                    "manual_after_submit does not match classification"
                )
        elif (
            result.command is not None
            or result.proof is not None
            or result.shared_resource is not None
        ):
            raise AdapterContractError(
                "blocked preparation must not provide executable payload"
            )
        return result


@dataclass(frozen=True)
class DispatchTargetRequest:
    schema_version: str
    job_id: str
    plan_id: str
    run_id: str
    target_label: str
    idempotency_key: str
    product_revision: int
    payload_digest: str
    confirmation_token_digest: str
    targets_digest: str
    source_identity_digest: str
    source_identity_payload_digest: str
    source_identity: Mapping[str, Any]
    sku_lineage_digest: str
    sku_lineage_payload_digest: str
    adapter_policy_digest: str
    prepared_command_digest: str
    proof_digest: str
    command: Mapping[str, Any]
    proof: Mapping[str, Any]
    shared_resource_context: Mapping[str, Any] | None = None
    progress_recorder: Callable[..., None] | None = None


@dataclass(frozen=True)
class DispatchTargetResult:
    canonical_status: str
    reason_category: str
    reason_scope: str
    reason_code: str
    reason_detail: str
    external_writes: tuple[str, ...]
    external_write_count: int | None = None
    confirmed_external_write_count_lower_bound: int = 0
    possible_external_write_count_upper_bound: int | None = None
    external_id: str | None = None
    submission_accepted: bool = False
    readback_verified: bool = False
    dispatch_outcome_unknown: bool = False
    evidence: Mapping[str, Any] | None = None

    @classmethod
    def from_value(cls, value: object) -> "DispatchTargetResult":
        if isinstance(value, cls):
            result = value
        elif isinstance(value, Mapping):
            writes = value.get("external_writes")
            if not isinstance(writes, (list, tuple)):
                raise AdapterContractError("dispatch external_writes is invalid")
            for field in (
                "submission_accepted",
                "readback_verified",
                "dispatch_outcome_unknown",
            ):
                if type(value.get(field, False)) is not bool:
                    raise AdapterContractError(
                        f"dispatch {field} must be a literal bool"
                    )
            external_id = value.get("external_id")
            if external_id is not None and (
                type(external_id) is not str or not external_id.strip()
            ):
                raise AdapterContractError(
                    "dispatch external_id must be a non-empty built-in str"
                )
            if value.get("evidence") is not None and not isinstance(
                value.get("evidence"),
                Mapping,
            ):
                raise AdapterContractError(
                    "dispatch evidence must be a mapping or null"
                )
            result = cls(
                canonical_status=_exact_text(
                    value.get("canonical_status"), "dispatch canonical_status"
                ),
                reason_category=_exact_text(
                    value.get("reason_category"), "dispatch reason_category"
                ),
                reason_scope=_exact_text(
                    value.get("reason_scope"), "dispatch reason_scope"
                ),
                reason_code=_exact_text(
                    value.get("reason_code"), "dispatch reason_code"
                ),
                reason_detail=_exact_text(
                    value.get("reason_detail"), "dispatch reason_detail"
                ),
                external_writes=_validated_write_classes(writes),
                external_write_count=value.get("external_write_count"),
                confirmed_external_write_count_lower_bound=value.get(
                    "confirmed_external_write_count_lower_bound",
                    0,
                ),
                possible_external_write_count_upper_bound=value.get(
                    "possible_external_write_count_upper_bound"
                ),
                external_id=external_id,
                submission_accepted=value.get("submission_accepted", False),
                readback_verified=value.get("readback_verified", False),
                dispatch_outcome_unknown=value.get(
                    "dispatch_outcome_unknown", False
                ),
                evidence=(
                    dict(value["evidence"])
                    if isinstance(value.get("evidence"), Mapping)
                    else None
                ),
            )
        else:
            raise AdapterContractError("dispatch must return DispatchTargetResult")
        if result.canonical_status not in {
            SUCCEEDED,
            SUCCEEDED_MANUAL_REVIEW,
            SUBMITTED_UNVERIFIED,
            FAILED_PRE_SUBMIT,
            RECONCILIATION_REQUIRED,
            BLOCKED_AUTH,
            BLOCKED_INVENTORY,
            BLOCKED_CAPABILITY,
        }:
            raise AdapterContractError("dispatch canonical_status is invalid")
        _reason_category(result.reason_category)
        _reason_scope(result.reason_scope)
        _clean_code(result.reason_code, "adapter_dispatch_result")
        _clean_detail(result.reason_detail)
        if _validated_write_classes(result.external_writes) != (
            result.external_writes
        ):
            raise AdapterContractError("dispatch write classes are invalid")
        _validated_write_count_bounds(
            result.external_writes,
            result.external_write_count,
            result.confirmed_external_write_count_lower_bound,
            result.possible_external_write_count_upper_bound,
            infer_legacy_exact=(
                result.dispatch_outcome_unknown is not True
            ),
        )
        if any(
            type(value) is not bool
            for value in (
                result.submission_accepted,
                result.readback_verified,
                result.dispatch_outcome_unknown,
            )
        ):
            raise AdapterContractError(
                "dispatch flags must be literal bool values"
            )
        if result.external_id is not None and (
            type(result.external_id) is not str
            or not result.external_id
            or result.external_id != result.external_id.strip()
        ):
            raise AdapterContractError(
                "dispatch external_id must be a non-empty built-in str"
            )
        if result.canonical_status in {
            SUCCEEDED,
            SUCCEEDED_MANUAL_REVIEW,
        }:
            if not result.readback_verified or not result.external_id:
                raise AdapterContractError(
                    "success requires external identity and exact readback"
                )
            if (
                result.dispatch_outcome_unknown
                or _UNKNOWN_WRITE_CLASS in result.external_writes
            ):
                raise AdapterContractError(
                    "success cannot carry an unknown dispatch outcome"
                )
            if result.external_writes and not result.submission_accepted:
                raise AdapterContractError(
                    "write-bearing success requires accepted submission"
                )
            if not result.external_writes and result.submission_accepted:
                raise AdapterContractError(
                    "existing no-write success must not invent submission"
                )
            if result.canonical_status == SUCCEEDED_MANUAL_REVIEW:
                if not result.external_writes or not result.submission_accepted:
                    raise AdapterContractError(
                        "manual-review success requires an accepted write"
                    )
                _manual_review_metadata(result.evidence)
            elif (
                isinstance(result.evidence, Mapping)
                and result.evidence.get("manual_review") is True
            ):
                raise AdapterContractError(
                    "warning-bearing success requires manual-review status"
                )
        elif result.canonical_status == SUBMITTED_UNVERIFIED:
            if (
                not result.submission_accepted
                or result.readback_verified
                or not result.external_id
                or not result.external_writes
                or result.dispatch_outcome_unknown
                or _UNKNOWN_WRITE_CLASS in result.external_writes
            ):
                raise AdapterContractError(
                    "submitted-unverified receipt is incomplete"
                )
        elif result.canonical_status == FAILED_PRE_SUBMIT:
            if (
                result.external_writes
                or result.submission_accepted
                or result.readback_verified
                or result.dispatch_outcome_unknown
                or result.external_id is not None
            ):
                raise AdapterContractError(
                    "pre-submit failure must prove exact zero write"
                )
        elif result.canonical_status == RECONCILIATION_REQUIRED:
            if result.readback_verified:
                raise AdapterContractError(
                    "reconciliation cannot claim verified readback"
                )
            if (
                not result.external_writes
                and not result.submission_accepted
                and not result.dispatch_outcome_unknown
            ):
                raise AdapterContractError(
                    "reconciliation requires post-dispatch evidence"
                )
        elif (
            result.external_writes
            or result.submission_accepted
            or result.readback_verified
            or result.dispatch_outcome_unknown
            or result.external_id is not None
        ):
            raise AdapterContractError(
                "blocked result must prove exact zero write"
            )
        return result


PrepareCallable = Callable[[PrepareTargetRequest], PrepareTargetResult]
DispatchCallable = Callable[[DispatchTargetRequest], DispatchTargetResult]


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_name: str
    target_labels: tuple[str, ...]
    prepare: PrepareCallable | None
    dispatch: DispatchCallable | None
    policy_digest: str
    prepare_is_read_only: bool
    consumes_prepared_command: bool
    preserves_idempotency_key: bool
    reports_truthful_receipt: bool

    @property
    def preparation_available(self) -> bool:
        return bool(
            callable(self.prepare)
            and self.prepare_is_read_only
            and _is_digest(self.policy_digest)
            and self.adapter_name.strip()
            and self.target_labels
        )

    @property
    def dispatch_available(self) -> bool:
        return bool(
            callable(self.dispatch)
            and self.consumes_prepared_command
            and self.preserves_idempotency_key
            and self.reports_truthful_receipt
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS oneclick_release_jobs (
    job_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL UNIQUE,
    product_revision INTEGER NOT NULL,
    payload_digest TEXT NOT NULL,
    confirmation_token_digest TEXT NOT NULL,
    targets_digest TEXT NOT NULL,
    source_identity_digest TEXT NOT NULL,
    source_identity_payload_digest TEXT NOT NULL,
    sku_lineage_digest TEXT NOT NULL,
    sku_lineage_payload_digest TEXT NOT NULL,
    adapter_policy_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    systemic_reason_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS oneclick_release_targets (
    job_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    storefront INTEGER NOT NULL CHECK (storefront IN (0, 1)),
    control_target INTEGER NOT NULL DEFAULT 0
        CHECK (control_target IN (0, 1)),
    capability TEXT,
    status TEXT NOT NULL,
    reason_category TEXT,
    reason_scope TEXT,
    reason_code TEXT,
    reason_detail TEXT,
    adapter_name TEXT,
    adapter_policy_digest TEXT NOT NULL,
    command_json TEXT,
    command_digest TEXT,
    proof_json TEXT,
    proof_digest TEXT,
    shared_resource_json TEXT,
    shared_resource_digest TEXT,
    shared_resource_context_json TEXT,
    shared_resource_context_digest TEXT,
    manual_after_submit INTEGER NOT NULL DEFAULT 0
        CHECK (manual_after_submit IN (0, 1)),
    dispatch_count INTEGER NOT NULL DEFAULT 0,
    cumulative_external_writes_json TEXT NOT NULL DEFAULT '[]',
    cumulative_external_writes_digest TEXT NOT NULL DEFAULT
        '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    cumulative_external_write_count INTEGER DEFAULT 0,
    cumulative_external_write_lower_bound INTEGER NOT NULL DEFAULT 0,
    cumulative_external_write_upper_bound INTEGER DEFAULT 0,
    dispatch_stage TEXT,
    dispatch_stage_evidence_digest TEXT,
    pending_write_intent_json TEXT,
    pending_write_intent_digest TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (job_id, target_label),
    FOREIGN KEY (job_id) REFERENCES oneclick_release_jobs(job_id)
);
CREATE INDEX IF NOT EXISTS idx_oneclick_targets_status
    ON oneclick_release_targets(job_id, status, ordinal);
CREATE TABLE IF NOT EXISTS oneclick_release_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    target_label TEXT,
    event_type TEXT NOT NULL,
    event_json TEXT NOT NULL,
    event_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES oneclick_release_jobs(job_id)
);
CREATE TRIGGER IF NOT EXISTS trg_oneclick_event_append_only_update
BEFORE UPDATE ON oneclick_release_events
BEGIN
    SELECT RAISE(ABORT, 'one-click events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_oneclick_event_append_only_delete
BEFORE DELETE ON oneclick_release_events
BEGIN
    SELECT RAISE(ABORT, 'one-click events are append-only');
END;
CREATE TABLE IF NOT EXISTS oneclick_release_outcomes (
    job_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    receipt_json TEXT NOT NULL,
    receipt_digest TEXT NOT NULL,
    consumer_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (consumer_status IN ('PENDING', 'SUCCEEDED', 'FAILED')),
    fact_digest TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, target_label, attempt),
    FOREIGN KEY (job_id, target_label)
        REFERENCES oneclick_release_targets(job_id, target_label)
);
CREATE TRIGGER IF NOT EXISTS trg_oneclick_outcome_identity_immutable
BEFORE UPDATE OF
    job_id, target_label, attempt, receipt_json, receipt_digest, created_at
ON oneclick_release_outcomes
BEGIN
    SELECT RAISE(ABORT, 'one-click outcome identity is immutable');
END;
CREATE TABLE IF NOT EXISTS oneclick_release_manual_acceptances (
    job_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    resolution_json TEXT NOT NULL,
    resolution_digest TEXT NOT NULL,
    consumer_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (consumer_status IN ('PENDING', 'SUCCEEDED', 'FAILED')),
    fact_digest TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, target_label, attempt),
    FOREIGN KEY (job_id, target_label, attempt)
        REFERENCES oneclick_release_outcomes(job_id, target_label, attempt)
);
CREATE TRIGGER IF NOT EXISTS
trg_oneclick_manual_acceptance_identity_immutable
BEFORE UPDATE OF
    job_id, target_label, attempt, resolution_json, resolution_digest,
    created_at
ON oneclick_release_manual_acceptances
BEGIN
    SELECT RAISE(
        ABORT,
        'one-click manual acceptance identity is immutable'
    );
END;
CREATE TRIGGER IF NOT EXISTS
trg_oneclick_manual_acceptance_append_only_delete
BEFORE DELETE ON oneclick_release_manual_acceptances
BEGIN
    SELECT RAISE(
        ABORT,
        'one-click manual acceptances are append-only'
    );
END;
CREATE TABLE IF NOT EXISTS oneclick_release_write_occurrences (
    job_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    occurrence_id TEXT NOT NULL,
    intended_classes_json TEXT NOT NULL,
    intended_classes_digest TEXT NOT NULL,
    prior_classes_json TEXT NOT NULL,
    prior_classes_digest TEXT NOT NULL,
    confirmed_lower_bound INTEGER NOT NULL,
    possible_upper_bound INTEGER NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('OPEN', 'CONFIRMED', 'REJECTED')),
    resolution_count INTEGER,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (job_id, target_label, attempt, occurrence_id),
    FOREIGN KEY (job_id, target_label)
        REFERENCES oneclick_release_targets(job_id, target_label)
);
CREATE TRIGGER IF NOT EXISTS
trg_oneclick_write_occurrence_identity_immutable
BEFORE UPDATE OF
    job_id, target_label, attempt, occurrence_id,
    intended_classes_json, intended_classes_digest,
    prior_classes_json, prior_classes_digest,
    confirmed_lower_bound, possible_upper_bound, created_at
ON oneclick_release_write_occurrences
BEGIN
    SELECT RAISE(
        ABORT,
        'one-click write occurrence identity is immutable'
    );
END;
CREATE TRIGGER IF NOT EXISTS
trg_oneclick_write_occurrence_append_only_delete
BEFORE DELETE ON oneclick_release_write_occurrences
BEGIN
    SELECT RAISE(
        ABORT,
        'one-click write occurrences are append-only'
    );
END;
"""


class OneClickReleaseStore:
    """Additive durable store sharing the canonical ReleaseStore database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def ensure_job(
        self,
        *,
        plan: Mapping[str, Any],
        run: Mapping[str, Any],
        product_revision: int,
        registry: Mapping[str, AdapterRegistration],
    ) -> dict[str, Any]:
        identity = _batch_identity(
            plan=plan,
            run=run,
            product_revision=product_revision,
            registry=registry,
        )
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM oneclick_release_jobs WHERE plan_id = ?",
                (identity["plan_id"],),
            ).fetchone()
            if existing:
                _require_job_identity(existing, identity)
                return self._project_job_in_transaction(
                    connection,
                    existing["job_id"],
                )
            connection.execute(
                """
                INSERT INTO oneclick_release_jobs (
                    job_id, plan_id, run_id, product_revision, payload_digest,
                    confirmation_token_digest, targets_digest,
                    source_identity_digest, source_identity_payload_digest,
                    sku_lineage_digest, sku_lineage_payload_digest,
                    adapter_policy_digest, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    identity["job_id"],
                    identity["plan_id"],
                    identity["run_id"],
                    identity["product_revision"],
                    identity["payload_digest"],
                    identity["confirmation_token_digest"],
                    identity["targets_digest"],
                    identity["source_identity_digest"],
                    identity["source_identity_payload_digest"],
                    identity["sku_lineage_digest"],
                    identity["sku_lineage_payload_digest"],
                    identity["adapter_policy_digest"],
                    now,
                    now,
                ),
            )
            targets = _target_labels(plan)
            execution_targets = _execution_target_labels(targets)
            rows = _run_targets(run)
            connection.executemany(
                """
                INSERT INTO oneclick_release_targets (
                    job_id, target_label, ordinal, storefront, control_target,
                    status,
                    adapter_name, adapter_policy_digest, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        identity["job_id"],
                        label,
                        ordinal,
                        int(
                            label not in {_COMMON_LABEL, SHOPEE_GLOBAL_TARGET}
                        ),
                        int(label == SHOPEE_GLOBAL_TARGET),
                        (
                            PENDING
                            if label == SHOPEE_GLOBAL_TARGET
                            else _initial_public_status(rows[label])
                        ),
                        _adapter_name_for_target(label),
                        _policy_digest_for_target(label, registry),
                        now,
                        now,
                    )
                    for ordinal, label in enumerate(execution_targets)
                ],
            )
            self._event(
                connection,
                identity["job_id"],
                None,
                "JOB_CREATED",
                {
                    "schema_version": BATCH_PREPARATION_SCHEMA,
                    "targets_digest": identity["targets_digest"],
                    "source_identity_digest": identity["source_identity_digest"],
                    "source_identity_payload_digest": identity[
                        "source_identity_payload_digest"
                    ],
                    "sku_lineage_digest": identity["sku_lineage_digest"],
                    "sku_lineage_payload_digest": identity[
                        "sku_lineage_payload_digest"
                    ],
                    "adapter_policy_digest": identity["adapter_policy_digest"],
                },
                now,
            )
            return self._project_job_in_transaction(
                connection,
                identity["job_id"],
            )

    def get_job(
        self,
        *,
        job_id: str | None = None,
        plan_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        connection = self._connect()
        try:
            row = None
            try:
                if job_id:
                    row = connection.execute(
                        "SELECT job_id FROM oneclick_release_jobs WHERE job_id = ?",
                        (str(job_id),),
                    ).fetchone()
                elif plan_id:
                    row = connection.execute(
                        "SELECT job_id FROM oneclick_release_jobs WHERE plan_id = ?",
                        (str(plan_id),),
                    ).fetchone()
            except sqlite3.OperationalError:
                return None
            return (
                self._project_job_in_transaction(connection, row["job_id"])
                if row
                else None
            )
        finally:
            connection.close()

    def resumable_job_ids(self) -> list[str]:
        """Return durable non-terminal jobs for startup recovery/wakeup."""

        if not self.path.is_file():
            return []
        connection = self._connect()
        try:
            try:
                return [
                    str(row["job_id"])
                    for row in connection.execute(
                        """
                        SELECT job_id FROM oneclick_release_jobs
                        WHERE status IN ('PENDING', 'PREPARING', 'READY', 'RUNNING')
                        ORDER BY created_at, job_id
                        """
                    )
                ]
            except sqlite3.OperationalError:
                return []
        finally:
            connection.close()

    def pending_outcome_receipts(
        self,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return redacted terminal facts awaiting the pure 05 adapter."""

        if type(limit) is not int or limit < 1 or limit > 500:
            raise OneClickControlPlaneError(
                "outcome receipt limit is invalid"
            )
        if not self.path.is_file():
            return []
        connection = self._connect()
        try:
            try:
                rows = connection.execute(
                    """
                    SELECT job_id, target_label, attempt,
                           receipt_json, receipt_digest
                    FROM oneclick_release_outcomes
                    WHERE consumer_status = 'PENDING'
                    ORDER BY created_at, job_id, target_label, attempt
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            return [
                {
                    "job_id": row["job_id"],
                    "target_label": row["target_label"],
                    "attempt": row["attempt"],
                    "receipt": json.loads(row["receipt_json"]),
                    "receipt_digest": row["receipt_digest"],
                }
                for row in rows
            ]
        finally:
            connection.close()

    def record_outcome_consumer_result(
        self,
        *,
        job_id: str,
        target_label: str,
        attempt: int,
        receipt_digest: str,
        fact_digest: str | None,
        error_code: str | None,
    ) -> None:
        """Record only 05 normalization metadata; never alter release state."""

        if type(attempt) is not int or attempt < 1:
            raise OneClickControlPlaneError(
                "outcome attempt is invalid"
            )
        if not _is_digest(receipt_digest):
            raise OneClickControlPlaneError(
                "outcome receipt digest is invalid"
            )
        if (fact_digest is None) == (error_code is None):
            raise OneClickControlPlaneError(
                "outcome consumer requires exactly one result"
            )
        if fact_digest is not None and not _is_digest(fact_digest):
            raise OneClickControlPlaneError(
                "outcome fact digest is invalid"
            )
        clean_error = (
            _clean_code(error_code, "outcome_consumer_failed")
            if error_code is not None
            else None
        )
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM oneclick_release_outcomes
                WHERE job_id = ? AND target_label = ? AND attempt = ?
                """,
                (job_id, target_label, attempt),
            ).fetchone()
            if (
                not row
                or row["receipt_digest"] != receipt_digest
                or row["consumer_status"] != "PENDING"
            ):
                raise OneClickControlPlaneError(
                    "outcome consumer identity is unavailable"
                )
            connection.execute(
                """
                UPDATE oneclick_release_outcomes
                SET consumer_status = ?, fact_digest = ?, error_code = ?,
                    updated_at = ?
                WHERE job_id = ? AND target_label = ? AND attempt = ?
                  AND receipt_digest = ? AND consumer_status = 'PENDING'
                """,
                (
                    "SUCCEEDED" if fact_digest is not None else "FAILED",
                    fact_digest,
                    clean_error,
                    _utc_now(),
                    job_id,
                    target_label,
                    attempt,
                    receipt_digest,
                ),
            )

    def pending_manual_acceptance_resolutions(
        self,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return acceptance resolutions without creating outcome samples."""

        if type(limit) is not int or limit < 1 or limit > 500:
            raise OneClickControlPlaneError(
                "manual acceptance resolution limit is invalid"
            )
        if not self.path.is_file():
            return []
        connection = self._connect()
        try:
            try:
                rows = connection.execute(
                    """
                    SELECT job_id, target_label, attempt,
                           resolution_json, resolution_digest
                    FROM oneclick_release_manual_acceptances
                    WHERE consumer_status = 'PENDING'
                    ORDER BY created_at, job_id, target_label, attempt
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            return [
                {
                    "job_id": row["job_id"],
                    "target_label": row["target_label"],
                    "attempt": row["attempt"],
                    "resolution": json.loads(row["resolution_json"]),
                    "resolution_digest": row["resolution_digest"],
                }
                for row in rows
            ]
        finally:
            connection.close()

    def record_manual_acceptance_consumer_result(
        self,
        *,
        job_id: str,
        target_label: str,
        attempt: int,
        resolution_digest: str,
        fact_digest: str | None,
        error_code: str | None,
    ) -> None:
        """Record only resolution-consumer metadata; never alter release."""

        if type(attempt) is not int or attempt < 1:
            raise OneClickControlPlaneError(
                "manual acceptance resolution attempt is invalid"
            )
        if not _is_digest(resolution_digest):
            raise OneClickControlPlaneError(
                "manual acceptance resolution digest is invalid"
            )
        if (fact_digest is None) == (error_code is None):
            raise OneClickControlPlaneError(
                "manual acceptance consumer requires exactly one result"
            )
        if fact_digest is not None and not _is_digest(fact_digest):
            raise OneClickControlPlaneError(
                "manual acceptance fact digest is invalid"
            )
        clean_error = (
            _clean_code(
                error_code,
                "manual_acceptance_consumer_failed",
            )
            if error_code is not None
            else None
        )
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM oneclick_release_manual_acceptances
                WHERE job_id = ? AND target_label = ? AND attempt = ?
                """,
                (job_id, target_label, attempt),
            ).fetchone()
            if (
                not row
                or row["resolution_digest"] != resolution_digest
                or row["consumer_status"] != "PENDING"
            ):
                raise OneClickControlPlaneError(
                    "manual acceptance consumer identity is unavailable"
                )
            connection.execute(
                """
                UPDATE oneclick_release_manual_acceptances
                SET consumer_status = ?, fact_digest = ?, error_code = ?,
                    updated_at = ?
                WHERE job_id = ? AND target_label = ? AND attempt = ?
                  AND resolution_digest = ? AND consumer_status = 'PENDING'
                """,
                (
                    "SUCCEEDED" if fact_digest is not None else "FAILED",
                    fact_digest,
                    clean_error,
                    _utc_now(),
                    job_id,
                    target_label,
                    attempt,
                    resolution_digest,
                ),
            )

    def record_systemic_stop(self, job_id: str, error: Exception) -> dict[str, Any]:
        """Persist a prepare/claim identity stop without touching a target."""

        with self._transaction() as connection:
            self._stop_systemic_in_transaction(
                connection,
                job_id,
                "batch_identity_drift",
                str(error),
            )
            return self._project_job_in_transaction(connection, job_id)

    def set_dispatch_capability(
        self,
        job_id: str,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        """Durably project the server-owned dispatch feature capability."""

        if type(enabled) is not bool:
            raise OneClickControlPlaneError(
                "dispatch capability must be a literal bool"
            )
        now = _utc_now()
        with self._transaction() as connection:
            job = connection.execute(
                "SELECT * FROM oneclick_release_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not job:
                raise OneClickControlPlaneError(
                    "one-click job was not found"
                )
            if enabled:
                changed = connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET status = 'READY',
                        capability = CASE
                            WHEN manual_after_submit = 1
                            THEN 'READY_SUBMIT_MANUAL'
                            ELSE 'EXACT_READY_AUTOMATIC'
                        END,
                        reason_category = 'CAPABILITY',
                        reason_scope = 'TARGET',
                        reason_code = 'dispatch_capability_enabled',
                        reason_detail = ?,
                        completed_at = NULL, updated_at = ?
                    WHERE job_id = ?
                      AND status = 'BLOCKED_CAPABILITY'
                      AND reason_code = 'oneclick_dispatch_disabled'
                    """,
                    (
                        _durable_reason_detail(
                            "CAPABILITY",
                            "dispatch_capability_enabled",
                            "server-owned one-click dispatch is enabled",
                        ),
                        now,
                        job_id,
                    ),
                )
            else:
                changed = connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET status = 'BLOCKED_CAPABILITY',
                        capability = 'BLOCKED_CAPABILITY',
                        reason_category = 'CAPABILITY',
                        reason_scope = 'TARGET',
                        reason_code = 'oneclick_dispatch_disabled',
                        reason_detail = ?,
                        completed_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = 'READY'
                    """,
                    (
                        _durable_reason_detail(
                            "CAPABILITY",
                            "oneclick_dispatch_disabled",
                            "server-owned one-click dispatch is disabled",
                        ),
                        now,
                        now,
                        job_id,
                    ),
                )
            if changed.rowcount:
                self._refresh_job(connection, job_id)
                self._event(
                    connection,
                    job_id,
                    None,
                    (
                        "DISPATCH_CAPABILITY_ENABLED"
                        if enabled
                        else "DISPATCH_CAPABILITY_BLOCKED"
                    ),
                    {
                        "schema_version": REGISTRY_SCHEMA,
                        "enabled": enabled,
                        "reason_code": (
                            "dispatch_capability_enabled"
                            if enabled
                            else "oneclick_dispatch_disabled"
                        ),
                    },
                    now,
                )
            return self._project_job_in_transaction(connection, job_id)

    def prepare_job(
        self,
        job_id: str,
        registry: Mapping[str, AdapterRegistration],
    ) -> dict[str, Any]:
        context = self._load_exact_context(job_id, registry)
        if context["job"]["status"] in _JOB_TERMINAL_STATUSES:
            return self.get_job(job_id=job_id) or {}
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE oneclick_release_jobs
                SET status = 'PREPARING', updated_at = ?
                WHERE job_id = ? AND status IN ('PENDING', 'PREPARING', 'READY')
                """,
                (_utc_now(), job_id),
            )

        prepared_rows: list[dict[str, Any]] = []
        systemic: dict[str, str] | None = None
        for row in context["targets"]:
            result = self._prepare_one(context, row, registry)
            prepared_rows.append(result)
            if result["reason_scope"] == SYSTEMIC_IDENTITY_SCOPE:
                systemic = {
                    "category": result["reason_category"],
                    "scope": result["reason_scope"],
                    "code": result["reason_code"],
                    "detail": result["reason_detail"],
                }
                break

        # Plan/policy drift during official read-only preparation invalidates
        # the whole batch before any canonical target claim.
        try:
            self._load_exact_context(job_id, registry)
        except SystemicIdentityError as error:
            systemic = {
                "category": "SYSTEMIC_IDENTITY",
                "scope": SYSTEMIC_IDENTITY_SCOPE,
                "code": "batch_identity_drift_after_prepare",
                "detail": str(error),
            }

        now = _utc_now()
        with self._transaction() as connection:
            if systemic:
                durable_reason = json.loads(
                    _durable_reason_detail(
                        systemic["category"],
                        systemic["code"],
                        systemic["detail"],
                    )
                )
                systemic = {
                    "category": systemic["category"],
                    "scope": systemic["scope"],
                    "code": systemic["code"],
                    **durable_reason,
                }
                connection.execute(
                    """
                    UPDATE oneclick_release_jobs
                    SET status = 'SYSTEMIC_STOPPED', systemic_reason_json = ?,
                        updated_at = ?, completed_at = ?
                    WHERE job_id = ?
                    """,
                    (_canonical_json(systemic), now, now, job_id),
                )
                connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET status = CASE
                            WHEN status IN (
                                'SUCCEEDED', 'SUCCEEDED_MANUAL_REVIEW',
                                'SUBMITTED_UNVERIFIED',
                                'RECONCILIATION_REQUIRED'
                            ) THEN status
                            ELSE 'BLOCKED_CAPABILITY'
                        END,
                        capability = CASE
                            WHEN capability IS NULL THEN 'BLOCKED_CAPABILITY'
                            ELSE capability
                        END,
                        reason_category = ?,
                        reason_scope = ?,
                        reason_code = ?,
                        reason_detail = ?,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        systemic["category"],
                        systemic["scope"],
                        systemic["code"],
                        _canonical_json(durable_reason),
                        now,
                        job_id,
                    ),
                )
                self._event(
                    connection,
                    job_id,
                    None,
                    "SYSTEMIC_PREPARATION_STOP",
                    systemic,
                    now,
                )
                return self._project_job_in_transaction(connection, job_id)

            for prepared in prepared_rows:
                if prepared["reason_code"] == "already_terminal":
                    continue
                durable_detail = _durable_reason_detail(
                    prepared["reason_category"],
                    prepared["reason_code"],
                    prepared["reason_detail"],
                )
                connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET capability = ?, status = ?, reason_category = ?,
                        reason_scope = ?, reason_code = ?, reason_detail = ?,
                        command_json = ?, command_digest = ?,
                        proof_json = ?, proof_digest = ?,
                        shared_resource_json = ?,
                        shared_resource_digest = ?,
                        shared_resource_context_json = ?,
                        shared_resource_context_digest = ?,
                        result_json = ?,
                        manual_after_submit = ?, updated_at = ?,
                        completed_at = ?
                    WHERE job_id = ? AND target_label = ?
                    """,
                    (
                        prepared["classification"],
                        prepared["status"],
                        prepared["reason_category"],
                        prepared["reason_scope"],
                        prepared["reason_code"],
                        durable_detail,
                        prepared.get("command_json"),
                        prepared.get("command_digest"),
                        prepared.get("proof_json"),
                        prepared.get("proof_digest"),
                        prepared.get("shared_resource_json"),
                        prepared.get("shared_resource_digest"),
                        prepared.get("shared_resource_context_json"),
                        prepared.get("shared_resource_context_digest"),
                        prepared.get("result_json"),
                        int(prepared.get("manual_after_submit") is True),
                        now,
                        (
                            now
                            if prepared["status"] == SUCCEEDED
                            else None
                        ),
                        job_id,
                        prepared["target_label"],
                    ),
                )
            global_prepared = next(
                (
                    row
                    for row in prepared_rows
                    if row["target_label"] == SHOPEE_GLOBAL_TARGET
                    and row["status"] == SUCCEEDED
                    and row.get("shared_resource_context_json")
                ),
                None,
            )
            if global_prepared is None:
                stored_global = connection.execute(
                    """
                    SELECT *
                    FROM oneclick_release_targets
                    WHERE job_id = ? AND target_label = ?
                    """,
                    (job_id, SHOPEE_GLOBAL_TARGET),
                ).fetchone()
                if (
                    stored_global
                    and stored_global["status"] == SUCCEEDED
                    and stored_global["shared_resource_context_json"]
                ):
                    global_prepared = dict(stored_global)
            if global_prepared:
                connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET shared_resource_context_json = ?,
                        shared_resource_context_digest = ?,
                        updated_at = ?
                    WHERE job_id = ?
                      AND target_label LIKE 'shopee:%'
                      AND target_label != ?
                    """,
                    (
                        global_prepared["shared_resource_context_json"],
                        global_prepared["shared_resource_context_digest"],
                        now,
                        job_id,
                        SHOPEE_GLOBAL_TARGET,
                    ),
                )
            ready_count = connection.execute(
                """
                SELECT *
                FROM oneclick_release_targets
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchall()
            ready_count = _runnable_ready_count(ready_count)
            self._refresh_job(connection, job_id)
            self._event(
                connection,
                job_id,
                None,
                "BATCH_PREPARED",
                {
                    "schema_version": BATCH_PREPARATION_SCHEMA,
                    "ready_count": ready_count,
                    "target_count": len(prepared_rows),
                },
                now,
            )
            return self._project_job_in_transaction(connection, job_id)

    def claim_next_dispatch(
        self,
        job_id: str,
        registry: Mapping[str, AdapterRegistration],
    ) -> DispatchTargetRequest | None:
        context = self._load_exact_context(job_id, registry)
        if context["job"]["status"] != "READY":
            return None
        with self._transaction() as connection:
            # Repeat the exact identity check under BEGIN IMMEDIATE.
            self._require_exact_context_in_transaction(
                connection,
                context["job"],
                registry,
            )
            candidates = connection.execute(
                """
                SELECT * FROM oneclick_release_targets
                WHERE job_id = ? AND status = 'READY'
                ORDER BY ordinal
                """,
                (job_id,),
            ).fetchall()
            selected = None
            for candidate in candidates:
                if self._dependency_satisfied(connection, context["job"], candidate):
                    selected = candidate
                    break
            if selected is None:
                self._refresh_job(connection, job_id)
                return None
            registration = registry.get(selected["adapter_name"])
            if not registration or not registration.dispatch_available:
                durable_detail = _durable_reason_detail(
                    "CAPABILITY",
                    "dispatch_seam_unavailable",
                    "registered dispatch seam is unavailable",
                )
                connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET status = 'BLOCKED_CAPABILITY',
                        capability = 'BLOCKED_CAPABILITY',
                        reason_category = 'CAPABILITY',
                        reason_scope = 'TARGET',
                        reason_code = 'dispatch_seam_unavailable',
                        reason_detail = ?,
                        updated_at = ?, completed_at = ?
                    WHERE job_id = ? AND target_label = ? AND status = 'READY'
                    """,
                    (
                        durable_detail,
                        _utc_now(),
                        _utc_now(),
                        job_id,
                        selected["target_label"],
                    ),
                )
                self._refresh_job(connection, job_id)
                return None

            try:
                stored_command = json.loads(selected["command_json"])
                stored_proof = json.loads(selected["proof_json"])
            except (TypeError, ValueError) as error:
                raise SystemicIdentityError(
                    "prepared command/proof is not valid JSON"
                ) from error
            if (
                not isinstance(stored_command, dict)
                or not isinstance(stored_proof, dict)
                or stored_command.get("schema_version")
                != PREPARED_COMMAND_SCHEMA
                or stored_proof.get("schema_version")
                != PREPARED_COMMAND_SCHEMA
                or stored_command.get("target_label")
                != selected["target_label"]
                or stored_proof.get("target_label")
                != selected["target_label"]
                or _digest_json(stored_command) != selected["command_digest"]
                or _digest_json(stored_proof) != selected["proof_digest"]
            ):
                raise SystemicIdentityError(
                    "prepared command/proof schema or identity is stale"
                )

            is_control = bool(selected["control_target"])
            canonical = None
            if not is_control:
                canonical = connection.execute(
                    """
                    SELECT target.*, run.plan_id
                    FROM release_target_runs AS target
                    JOIN release_runs AS run ON run.run_id = target.run_id
                    WHERE target.run_id = ? AND target.target_label = ?
                    """,
                    (context["job"]["run_id"], selected["target_label"]),
                ).fetchone()
                if (
                    not canonical
                    or canonical["plan_id"] != context["job"]["plan_id"]
                    or canonical["status"] != "PENDING"
                    or type(canonical["attempts"]) is not int
                    or canonical["attempts"] != selected["dispatch_count"]
                    or canonical["external_id"] is not None
                    or canonical["error"] is not None
                ):
                    self._stop_systemic_in_transaction(
                        connection,
                        job_id,
                        "canonical_target_identity_drift",
                        "canonical target is no longer pristine PENDING",
                    )
                    return None
                if connection.execute(
                    """
                    SELECT 1 FROM release_target_submissions
                    WHERE run_id = ? AND target_label = ?
                    UNION ALL
                    SELECT 1 FROM release_target_readbacks
                    WHERE run_id = ? AND target_label = ?
                    LIMIT 1
                    """,
                    (context["job"]["run_id"], selected["target_label"]) * 2,
                ).fetchone():
                    self._stop_systemic_in_transaction(
                        connection,
                        job_id,
                        "canonical_target_evidence_drift",
                        "canonical target gained durable evidence after preparation",
                    )
                    return None
                failure_rows = connection.execute(
                    """
                    SELECT evidence_json FROM release_target_failure_events
                    WHERE run_id = ? AND target_label = ?
                    ORDER BY attempt
                    """,
                    (context["job"]["run_id"], selected["target_label"]),
                ).fetchall()
                if failure_rows and not _failure_rows_are_safe_zero_write(
                    failure_rows
                ):
                    self._stop_systemic_in_transaction(
                        connection,
                        job_id,
                        "canonical_target_failure_evidence_drift",
                        "canonical target has unsafe historical write evidence",
                    )
                    return None
            now = _utc_now()
            claimed = connection.execute(
                """
                UPDATE oneclick_release_targets
                SET status = 'DISPATCHING',
                    dispatch_count = dispatch_count + 1,
                    updated_at = ?
                WHERE job_id = ? AND target_label = ? AND status = 'READY'
                """,
                (now, job_id, selected["target_label"]),
            )
            canonical_claimed = None
            if not is_control:
                canonical_claimed = connection.execute(
                    """
                    UPDATE release_target_runs
                    SET status = 'RUNNING', attempts = attempts + 1,
                        error = NULL, completed_at = NULL, updated_at = ?
                    WHERE run_id = ? AND target_label = ?
                      AND status = 'PENDING' AND attempts = ?
                      AND external_id IS NULL AND error IS NULL
                    """,
                    (
                        now,
                        context["job"]["run_id"],
                        selected["target_label"],
                        selected["dispatch_count"],
                    ),
                )
            if claimed.rowcount != 1 or (
                canonical_claimed is not None
                and canonical_claimed.rowcount != 1
            ):
                raise SystemicIdentityError("atomic target claim lost a race")
            if not is_control:
                connection.execute(
                    """
                    UPDATE release_runs
                    SET status = 'RUNNING', updated_at = ?, completed_at = NULL
                    WHERE run_id = ?
                    """,
                    (now, context["job"]["run_id"]),
                )
            connection.execute(
                """
                UPDATE oneclick_release_jobs
                SET status = 'RUNNING', updated_at = ?
                WHERE job_id = ?
                """,
                (now, job_id),
            )
            self._event(
                connection,
                job_id,
                selected["target_label"],
                "TARGET_CLAIMED",
                {
                    "command_digest": selected["command_digest"],
                    "proof_digest": selected["proof_digest"],
                },
                now,
            )
            return DispatchTargetRequest(
                schema_version=PREPARED_COMMAND_SCHEMA,
                job_id=job_id,
                plan_id=context["job"]["plan_id"],
                run_id=context["job"]["run_id"],
                target_label=selected["target_label"],
                idempotency_key=(
                    _shopee_global_idempotency_key(context["job"])
                    if is_control
                    else canonical["idempotency_key"]
                ),
                product_revision=context["job"]["product_revision"],
                payload_digest=context["job"]["payload_digest"],
                confirmation_token_digest=context["job"][
                    "confirmation_token_digest"
                ],
                targets_digest=context["job"]["targets_digest"],
                source_identity_digest=context["job"]["source_identity_digest"],
                source_identity_payload_digest=context["job"][
                    "source_identity_payload_digest"
                ],
                source_identity=dict(context["source_identity"]),
                sku_lineage_digest=context["job"]["sku_lineage_digest"],
                sku_lineage_payload_digest=context["job"][
                    "sku_lineage_payload_digest"
                ],
                adapter_policy_digest=selected["adapter_policy_digest"],
                prepared_command_digest=selected["command_digest"],
                proof_digest=selected["proof_digest"],
                command=stored_command,
                proof=stored_proof,
                shared_resource_context=(
                    _stored_shared_resource(selected, context=False)
                    if is_control
                    else _stored_shared_resource(selected, context=True)
                ),
            )

    def record_dispatch_progress(
        self,
        request: DispatchTargetRequest,
        external_writes: tuple[str, ...],
        stage: str,
        evidence: Mapping[str, Any],
        external_write_count: int | None = None,
        confirmed_external_write_count_lower_bound: int = 0,
        possible_external_write_count_upper_bound: int | None = None,
        write_boundary: str | None = None,
    ) -> None:
        """Durably accumulate confirmed write classes during a composite dispatch.

        The evidence payload is never stored; only its digest is retained.  This
        lets a later exception or process restart preserve every earlier write
        without leaking marketplace responses or command data.
        """

        additions = _validated_write_classes(external_writes)
        addition_exact, addition_lower, addition_upper = (
            _validated_write_count_bounds(
                additions,
                external_write_count,
                confirmed_external_write_count_lower_bound,
                possible_external_write_count_upper_bound,
            )
        )
        stage_value = _clean_code(stage, "dispatch_progress")
        evidence_digest = _digest_json(dict(evidence))
        now = _utc_now()
        with self._transaction() as connection:
            job = connection.execute(
                "SELECT * FROM oneclick_release_jobs WHERE job_id = ?",
                (request.job_id,),
            ).fetchone()
            target = connection.execute(
                """
                SELECT * FROM oneclick_release_targets
                WHERE job_id = ? AND target_label = ?
                """,
                (request.job_id, request.target_label),
            ).fetchone()
            if not job or not target:
                raise SystemicIdentityError("dispatch progress target was not found")
            _require_dispatch_identity(job, target, request)
            canonical = connection.execute(
                """
                SELECT status, attempts FROM release_target_runs
                WHERE run_id = ? AND target_label = ?
                """,
                (request.run_id, request.target_label),
            ).fetchone()
            if (
                target["status"] != DISPATCHING
                or (
                    target["control_target"] != 1
                    and (
                        not canonical
                        or canonical["status"] != "RUNNING"
                        or canonical["attempts"] != target["dispatch_count"]
                    )
                )
            ):
                raise SystemicIdentityError(
                    "dispatch progress does not match the active atomic claim"
                )
            if target["control_target"] == 1 and any(
                item not in SHOPEE_GLOBAL_WRITE_CLASSES
                and item != _UNKNOWN_WRITE_CLASS
                for item in additions
            ):
                raise AdapterContractError(
                    "Shopee GLOBAL progress reported an unrelated write class"
                )
            if target["control_target"] == 1:
                addition_exact, addition_lower, addition_upper = (
                    _validated_write_count_bounds(
                        additions,
                        external_write_count,
                        confirmed_external_write_count_lower_bound,
                        possible_external_write_count_upper_bound,
                    )
                )
                if (
                    addition_exact is None
                    and addition_upper is None
                ):
                    raise AdapterContractError(
                        "Shopee GLOBAL uncertain progress requires a possible write upper bound"
                    )
                if write_boundary not in {
                    "PRE_INVOCATION_INTENT",
                    "POST_RESPONSE_CONFIRMED",
                    "POST_RESPONSE_REJECTED",
                }:
                    raise AdapterContractError(
                        "Shopee GLOBAL progress requires an exact write boundary"
                    )
                if (
                    write_boundary == "PRE_INVOCATION_INTENT"
                    and (
                        addition_exact is not None
                        or addition_upper != addition_lower + 1
                    )
                ) or (
                    write_boundary == "POST_RESPONSE_CONFIRMED"
                    and (
                        addition_exact is None
                        or addition_exact != addition_lower
                        or addition_exact != addition_upper
                    )
                ) or (
                    write_boundary == "POST_RESPONSE_REJECTED"
                    and (
                        addition_exact is None
                        or addition_exact != addition_lower
                        or addition_exact != addition_upper
                    )
                ):
                    raise AdapterContractError(
                        "Shopee GLOBAL progress bounds do not match its write boundary"
                    )
            if (
                target["control_target"] != 1
                and any(
                    item in SHOPEE_GLOBAL_WRITE_CLASSES
                    for item in additions
                )
            ):
                raise AdapterContractError(
                    "storefront dispatch cannot report the Shopee global write"
                )
            if target["control_target"] != 1:
                addition_exact, addition_lower, addition_upper = (
                    _validated_write_count_bounds(
                        additions,
                        external_write_count,
                        confirmed_external_write_count_lower_bound,
                        possible_external_write_count_upper_bound,
                        infer_legacy_exact=True,
                    )
                )
            if target["control_target"] != 1 and not additions:
                raise AdapterContractError(
                    "dispatch progress requires a write class"
                )
            stored_classes = _stored_write_classes(target)
            stored_exact, stored_lower, stored_upper = (
                _stored_write_count_bounds(target)
            )
            pending_intent = _stored_pending_write_intent(target)
            next_pending = None
            if target["control_target"] == 1:
                occurrence = connection.execute(
                    """
                    SELECT * FROM oneclick_release_write_occurrences
                    WHERE job_id = ? AND target_label = ?
                      AND attempt = ? AND occurrence_id = ?
                    """,
                    (
                        request.job_id,
                        request.target_label,
                        target["dispatch_count"],
                        stage_value,
                    ),
                ).fetchone()
                if write_boundary == "PRE_INVOCATION_INTENT":
                    if occurrence is not None:
                        raise AdapterContractError(
                            "Shopee GLOBAL write occurrence was already opened"
                        )
                    declaration = _stored_shared_resource(
                        target,
                        context=False,
                    )
                    if not declaration:
                        raise SystemicIdentityError(
                            "Shopee GLOBAL write sequence declaration is unavailable"
                        )
                    image_count = declaration[
                        "approved_selected_image_count"
                    ]
                    if addition_lower < image_count:
                        expected_occurrence = (
                            f"image_upload-{addition_lower + 1}"
                        )
                        expected_classes = (
                            SHOPEE_IMAGE_UPLOAD_WRITE,
                        )
                    elif addition_lower == image_count:
                        expected_occurrence = "global_create-1"
                        expected_classes = (
                            SHOPEE_IMAGE_UPLOAD_WRITE,
                            SHOPEE_GLOBAL_WRITE,
                        )
                    elif addition_lower == image_count + 1:
                        expected_occurrence = "model_init-1"
                        expected_classes = SHOPEE_GLOBAL_WRITE_CLASSES
                    else:
                        raise AdapterContractError(
                            "Shopee GLOBAL write sequence exceeded its approved plan"
                        )
                    if (
                        stage_value != expected_occurrence
                        or additions != expected_classes
                    ):
                        raise AdapterContractError(
                            "Shopee GLOBAL write occurrence is out of order"
                        )
                    if (
                        pending_intent is not None
                        or stored_exact != addition_lower
                        or stored_lower != addition_lower
                        or stored_upper != addition_lower
                        or any(
                            item not in additions for item in stored_classes
                        )
                    ):
                        raise AdapterContractError(
                            "Shopee GLOBAL write intent did not follow the confirmed ledger"
                        )
                    next_pending = {
                        "stage": stage_value,
                        "prior_classes": list(stored_classes),
                        "intended_classes": list(additions),
                        "confirmed_lower_bound": addition_lower,
                        "possible_upper_bound": addition_upper,
                    }
                else:
                    expected_occurrence_status = (
                        "CONFIRMED"
                        if write_boundary == "POST_RESPONSE_CONFIRMED"
                        else "REJECTED"
                    )
                    if occurrence is None:
                        raise AdapterContractError(
                            "Shopee GLOBAL write resolution has no occurrence"
                        )
                    if occurrence["status"] != "OPEN":
                        if (
                            occurrence["status"]
                            == expected_occurrence_status
                            and occurrence["resolution_count"]
                            == addition_exact
                            and additions == stored_classes
                            and addition_exact == stored_exact
                            and addition_lower == stored_lower
                            and addition_upper == stored_upper
                        ):
                            return
                        raise AdapterContractError(
                            "Shopee GLOBAL write occurrence was already resolved"
                        )
                    if (
                        pending_intent is None
                        or pending_intent["stage"] != stage_value
                    ):
                        raise AdapterContractError(
                            "Shopee GLOBAL write resolution has no matching intent"
                        )
                    if write_boundary == "POST_RESPONSE_CONFIRMED":
                        expected_classes = tuple(
                            pending_intent["intended_classes"]
                        )
                        expected_count = pending_intent[
                            "possible_upper_bound"
                        ]
                    else:
                        expected_classes = tuple(
                            pending_intent["prior_classes"]
                        )
                        expected_count = pending_intent[
                            "confirmed_lower_bound"
                        ]
                    if (
                        additions != expected_classes
                        or addition_exact != expected_count
                        or addition_lower != expected_count
                        or addition_upper != expected_count
                    ):
                        raise AdapterContractError(
                            "Shopee GLOBAL write resolution drifted from its intent"
                        )
                cumulative = additions
            else:
                cumulative = _merge_write_classes(
                    stored_classes,
                    additions,
                )
            cumulative_exact = addition_exact
            cumulative_lower = addition_lower
            cumulative_upper = addition_upper
            if (
                target["control_target"] != 1
                and (
                    cumulative_lower < stored_lower or (
                        stored_exact is not None
                        and cumulative_exact is not None
                        and cumulative_exact < stored_exact
                    ) or (
                        stored_upper is not None
                        and cumulative_upper is not None
                        and cumulative_upper < stored_upper
                    )
                )
            ):
                raise AdapterContractError(
                    "dispatch progress write-count bounds regressed"
                )
            if (
                target["control_target"] == 1
                and write_boundary == "PRE_INVOCATION_INTENT"
            ):
                connection.execute(
                    """
                    INSERT INTO oneclick_release_write_occurrences (
                        job_id, target_label, attempt, occurrence_id,
                        intended_classes_json, intended_classes_digest,
                        prior_classes_json, prior_classes_digest,
                        confirmed_lower_bound, possible_upper_bound,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
                    """,
                    (
                        request.job_id,
                        request.target_label,
                        target["dispatch_count"],
                        stage_value,
                        _canonical_json(list(additions)),
                        _digest_json(list(additions)),
                        _canonical_json(list(stored_classes)),
                        _digest_json(list(stored_classes)),
                        addition_lower,
                        addition_upper,
                        now,
                    ),
                )
            elif target["control_target"] == 1:
                connection.execute(
                    """
                    UPDATE oneclick_release_write_occurrences
                    SET status = ?, resolution_count = ?, resolved_at = ?
                    WHERE job_id = ? AND target_label = ?
                      AND attempt = ? AND occurrence_id = ?
                      AND status = 'OPEN'
                    """,
                    (
                        (
                            "CONFIRMED"
                            if write_boundary
                            == "POST_RESPONSE_CONFIRMED"
                            else "REJECTED"
                        ),
                        addition_exact,
                        now,
                        request.job_id,
                        request.target_label,
                        target["dispatch_count"],
                        stage_value,
                    ),
                )
            connection.execute(
                """
                UPDATE oneclick_release_targets
                SET cumulative_external_writes_json = ?,
                    cumulative_external_writes_digest = ?,
                    cumulative_external_write_count = ?,
                    cumulative_external_write_lower_bound = ?,
                    cumulative_external_write_upper_bound = ?,
                    dispatch_stage = ?,
                    dispatch_stage_evidence_digest = ?,
                    pending_write_intent_json = ?,
                    pending_write_intent_digest = ?,
                    updated_at = ?
                WHERE job_id = ? AND target_label = ?
                """,
                (
                    _canonical_json(list(cumulative)),
                    _digest_json(list(cumulative)),
                    cumulative_exact,
                    cumulative_lower,
                    cumulative_upper,
                    stage_value,
                    evidence_digest,
                    (
                        _canonical_json(next_pending)
                        if next_pending
                        else None
                    ),
                    (
                        _digest_json(next_pending)
                        if next_pending
                        else None
                    ),
                    now,
                    request.job_id,
                    request.target_label,
                ),
            )
            self._event(
                connection,
                request.job_id,
                request.target_label,
                "DISPATCH_PROGRESS",
                {
                    "stage": stage_value,
                    "cumulative_external_write_classes": list(cumulative),
                    "cumulative_external_write_count": cumulative_exact,
                    "confirmed_external_write_count_lower_bound": (
                        cumulative_lower
                    ),
                    "possible_external_write_count_upper_bound": (
                        cumulative_upper
                    ),
                    "write_boundary": write_boundary,
                    "evidence_digest": evidence_digest,
                },
                now,
            )

    def cumulative_external_writes(
        self,
        request: DispatchTargetRequest,
    ) -> tuple[str, ...]:
        """Return the exact durable write ledger for an active dispatch."""

        connection = self._connect()
        try:
            job = connection.execute(
                "SELECT * FROM oneclick_release_jobs WHERE job_id = ?",
                (request.job_id,),
            ).fetchone()
            target = connection.execute(
                """
                SELECT * FROM oneclick_release_targets
                WHERE job_id = ? AND target_label = ?
                """,
                (request.job_id, request.target_label),
            ).fetchone()
            if not job or not target:
                raise SystemicIdentityError("dispatch ledger target was not found")
            _require_dispatch_identity(job, target, request)
            return _stored_write_classes(target)
        finally:
            connection.close()

    def cumulative_external_write_bounds(
        self,
        request: DispatchTargetRequest,
    ) -> tuple[int | None, int, int | None]:
        connection = self._connect()
        try:
            job = connection.execute(
                "SELECT * FROM oneclick_release_jobs WHERE job_id = ?",
                (request.job_id,),
            ).fetchone()
            target = connection.execute(
                """
                SELECT * FROM oneclick_release_targets
                WHERE job_id = ? AND target_label = ?
                """,
                (request.job_id, request.target_label),
            ).fetchone()
            if not job or not target:
                raise SystemicIdentityError(
                    "dispatch write-count ledger target was not found"
                )
            _require_dispatch_identity(job, target, request)
            return _stored_write_count_bounds(target)
        finally:
            connection.close()

    def has_pending_write_intent(
        self,
        request: DispatchTargetRequest,
    ) -> bool:
        connection = self._connect()
        try:
            job = connection.execute(
                "SELECT * FROM oneclick_release_jobs WHERE job_id = ?",
                (request.job_id,),
            ).fetchone()
            target = connection.execute(
                """
                SELECT * FROM oneclick_release_targets
                WHERE job_id = ? AND target_label = ?
                """,
                (request.job_id, request.target_label),
            ).fetchone()
            if not job or not target:
                raise SystemicIdentityError(
                    "pending write-intent target was not found"
                )
            _require_dispatch_identity(job, target, request)
            return _stored_pending_write_intent(target) is not None
        finally:
            connection.close()

    def record_dispatch_result(
        self,
        request: DispatchTargetRequest,
        value: object,
    ) -> dict[str, Any]:
        result = DispatchTargetResult.from_value(value)
        now = _utc_now()
        with self._transaction() as connection:
            job = connection.execute(
                "SELECT * FROM oneclick_release_jobs WHERE job_id = ?",
                (request.job_id,),
            ).fetchone()
            target = connection.execute(
                """
                SELECT * FROM oneclick_release_targets
                WHERE job_id = ? AND target_label = ?
                """,
                (request.job_id, request.target_label),
            ).fetchone()
            if not job or not target:
                raise SystemicIdentityError("dispatch job target was not found")
            _require_dispatch_identity(job, target, request)
            if target["control_target"] == 1:
                return self._record_shared_control_result_in_transaction(
                    connection,
                    job=job,
                    target=target,
                    request=request,
                    result=result,
                    now=now,
                )
            if any(
                item in SHOPEE_GLOBAL_WRITE_CLASSES
                for item in result.external_writes
            ):
                raise AdapterContractError(
                    "storefront dispatch cannot perform the Shopee global write"
                )
            canonical = connection.execute(
                """
                SELECT * FROM release_target_runs
                WHERE run_id = ? AND target_label = ?
                """,
                (request.run_id, request.target_label),
            ).fetchone()
            if (
                target["status"] != DISPATCHING
                or not canonical
                or canonical["status"] != "RUNNING"
                or canonical["attempts"] != target["dispatch_count"]
            ):
                raise SystemicIdentityError(
                    "dispatch receipt does not match the active atomic claim"
                )
            cumulative = _stored_write_classes(target)
            if any(item not in result.external_writes for item in cumulative):
                raise AdapterContractError(
                    "dispatch receipt omitted a previously confirmed write"
                )
            result_exact, result_lower, result_upper = (
                _result_write_count_bounds(result)
            )
            _, stored_lower, stored_upper = _stored_write_count_bounds(target)
            if result_lower < stored_lower or (
                stored_upper is not None
                and result_upper is not None
                and result_upper < stored_upper
            ):
                raise AdapterContractError(
                    "dispatch receipt omitted confirmed write-count evidence"
                )
            evidence = _canonical_evidence(request, result)
            encoded = _canonical_json(evidence)
            evidence_digest = _digest_json(evidence)
            durable_detail = _durable_reason_detail(
                result.reason_category,
                result.reason_code,
                result.reason_detail,
            )
            if result.canonical_status in {
                SUCCEEDED,
                SUCCEEDED_MANUAL_REVIEW,
            }:
                connection.execute(
                    """
                    INSERT INTO release_target_readbacks (
                        run_id, target_label, evidence_json,
                        evidence_digest, verified_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        request.run_id,
                        request.target_label,
                        encoded,
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
                        result.external_id,
                        now,
                        now,
                        request.run_id,
                        request.target_label,
                    ),
                )
            elif result.canonical_status == SUBMITTED_UNVERIFIED:
                connection.execute(
                    """
                    INSERT INTO release_target_submissions (
                        run_id, target_label, external_id, evidence_json,
                        evidence_digest, status, submitted_at
                    ) VALUES (?, ?, ?, ?, ?, 'SUBMITTED_UNVERIFIED', ?)
                    """,
                    (
                        request.run_id,
                        request.target_label,
                        result.external_id,
                        encoded,
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
                        result.external_id,
                        "submission accepted; official readback unavailable",
                        now,
                        now,
                        request.run_id,
                        request.target_label,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO release_target_failure_events (
                        run_id, target_label, attempt, evidence_json,
                        evidence_digest, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.run_id,
                        request.target_label,
                        canonical["attempts"],
                        encoded,
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
                        result.external_id,
                        durable_detail,
                        now,
                        now,
                        request.run_id,
                        request.target_label,
                    ),
                )
            public_result = _public_result(result, evidence_digest)
            connection.execute(
                """
                UPDATE oneclick_release_targets
                SET status = ?, reason_category = ?, reason_scope = ?,
                    reason_code = ?, reason_detail = ?, result_json = ?,
                    updated_at = ?, completed_at = ?
                WHERE job_id = ? AND target_label = ?
                """,
                (
                    result.canonical_status,
                    result.reason_category,
                    result.reason_scope,
                    result.reason_code,
                    durable_detail,
                    _canonical_json(public_result),
                    now,
                    now,
                    request.job_id,
                    request.target_label,
                ),
            )
            if result.canonical_status == SUCCEEDED_MANUAL_REVIEW:
                connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET manual_after_submit = 1
                    WHERE job_id = ? AND target_label = ?
                    """,
                    (request.job_id, request.target_label),
                )
            _insert_outcome_receipt(
                connection,
                job=job,
                target=target,
                result=result,
                evidence_digest=evidence_digest,
                now=now,
            )
            self._refresh_canonical_run(connection, request.run_id, now)
            if result.reason_scope == SYSTEMIC_IDENTITY_SCOPE:
                self._stop_systemic_in_transaction(
                    connection,
                    request.job_id,
                    result.reason_code,
                    result.reason_detail,
                )
            else:
                self._refresh_job(connection, request.job_id)
            self._event(
                connection,
                request.job_id,
                request.target_label,
                "TARGET_TERMINAL",
                public_result,
                now,
            )
            return self._project_job_in_transaction(
                connection,
                request.job_id,
            )

    def _record_shared_control_result_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        job: Mapping[str, Any],
        target: Mapping[str, Any],
        request: DispatchTargetRequest,
        result: DispatchTargetResult,
        now: str,
    ) -> dict[str, Any]:
        if (
            target["target_label"] != SHOPEE_GLOBAL_TARGET
            or target["status"] != DISPATCHING
            or target["dispatch_count"] < 1
        ):
            raise SystemicIdentityError(
                "Shopee GLOBAL receipt does not match the active control claim"
            )
        declaration = _stored_shared_resource(target, context=False)
        if (
            not declaration
            or declaration.get("mode") != "ENSURE_NEW"
            or request.shared_resource_context != declaration
        ):
            raise SystemicIdentityError(
                "Shopee GLOBAL dispatch declaration drifted"
            )
        cumulative = _stored_write_classes(target)
        if any(item not in result.external_writes for item in cumulative):
            raise AdapterContractError(
                "Shopee GLOBAL receipt omitted a confirmed write"
            )
        result_exact, result_lower, result_upper = (
            _result_write_count_bounds(result, infer_legacy_exact=False)
        )
        _, stored_lower, stored_upper = _stored_write_count_bounds(target)
        pending_intent = _stored_pending_write_intent(target)
        if pending_intent is not None and not (
            result.canonical_status == RECONCILIATION_REQUIRED
            and result.dispatch_outcome_unknown is True
            and result_exact is None
            and result_lower == stored_lower
            and result_upper == stored_upper
        ):
            raise AdapterContractError(
                "Shopee GLOBAL terminal receipt left an unresolved write intent"
            )
        if result_lower < stored_lower or (
            stored_upper is not None
            and result_upper is not None
            and result_upper < stored_upper
        ):
            raise AdapterContractError(
                "Shopee GLOBAL receipt omitted confirmed write-count evidence"
            )
        context_payload = None
        if result.canonical_status in {
            SUCCEEDED,
            SUCCEEDED_MANUAL_REVIEW,
            SUBMITTED_UNVERIFIED,
        }:
            if (
                result.canonical_status != SUCCEEDED
                or result.external_writes != SHOPEE_GLOBAL_WRITE_CLASSES
                or result_exact is None
                or result_exact
                != declaration["expected_external_write_count"]
                or result_lower != result_exact
                or result_upper != result_exact
                or result.submission_accepted is not True
                or result.readback_verified is not True
            ):
                raise AdapterContractError(
                    "Shopee GLOBAL can terminate successfully only after exact readback"
                )
            context_payload = _validated_shared_resource_result(
                result.evidence,
                declaration,
            )
            if result.external_id != (
                "sha256:" + context_payload["global_identity_digest"]
            ):
                raise AdapterContractError(
                    "Shopee GLOBAL external identity must be a redacted digest"
                )
        elif result.canonical_status == RECONCILIATION_REQUIRED:
            known = tuple(
                item
                for item in result.external_writes
                if item != _UNKNOWN_WRITE_CLASS
            )
            if (
                any(item not in SHOPEE_GLOBAL_WRITE_CLASSES for item in known)
                or tuple(
                    item
                    for item in SHOPEE_GLOBAL_WRITE_CLASSES
                    if item in known
                )
                != known
                or (
                    result_exact is None
                    and result_upper is None
                )
            ):
                raise AdapterContractError(
                    "Shopee GLOBAL reconciliation write ledger is invalid"
                )
        elif result.external_writes:
            raise AdapterContractError(
                "Shopee GLOBAL non-success cannot carry an unclassified write"
            )
        evidence = _canonical_evidence(request, result)
        evidence_digest = _digest_json(evidence)
        durable_detail = _durable_reason_detail(
            result.reason_category,
            result.reason_code,
            result.reason_detail,
        )
        public_result = _public_result(result, evidence_digest)
        if context_payload:
            context_digest = _digest_json(context_payload)
            public_result = {
                **public_result,
                "shared_resource_status": "VERIFIED_CREATED",
                "shared_resource_context_digest": context_digest,
            }
            connection.execute(
                """
                UPDATE oneclick_release_targets
                SET shared_resource_context_json = ?,
                    shared_resource_context_digest = ?,
                    updated_at = ?
                WHERE job_id = ? AND target_label LIKE 'shopee:%'
                """,
                (
                    _canonical_json(context_payload),
                    context_digest,
                    now,
                    request.job_id,
                ),
            )
        connection.execute(
            """
            UPDATE oneclick_release_targets
            SET status = ?, reason_category = ?, reason_scope = ?,
                reason_code = ?, reason_detail = ?, result_json = ?,
                cumulative_external_writes_json = ?,
                cumulative_external_writes_digest = ?,
                cumulative_external_write_count = ?,
                cumulative_external_write_lower_bound = ?,
                cumulative_external_write_upper_bound = ?,
                updated_at = ?, completed_at = ?
            WHERE job_id = ? AND target_label = ? AND status = 'DISPATCHING'
            """,
            (
                result.canonical_status,
                result.reason_category,
                result.reason_scope,
                result.reason_code,
                durable_detail,
                _canonical_json(public_result),
                _canonical_json(list(result.external_writes)),
                _digest_json(list(result.external_writes)),
                result_exact,
                result_lower,
                result_upper,
                now,
                now,
                request.job_id,
                request.target_label,
            ),
        )
        self._refresh_job(connection, request.job_id)
        self._event(
            connection,
            request.job_id,
            request.target_label,
            "SHARED_CONTROL_TERMINAL",
            {
                **public_result,
                "storefront": False,
            },
            now,
        )
        return self._project_job_in_transaction(
            connection,
            request.job_id,
        )

    def record_manual_acceptance(
        self,
        *,
        run_id: str,
        target_label: str,
        verified_by: str,
        user_verified: bool,
        verification_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically close canonical and one-click manual-review ledgers."""

        if verified_by != "Kyle" or user_verified is not True:
            raise AdapterContractError(
                "manual acceptance requires explicit Kyle confirmation"
            )
        clean_run_id = _exact_text(run_id, "manual acceptance run_id")
        clean_target = _exact_text(
            target_label,
            "manual acceptance target_label",
        )
        if not isinstance(verification_evidence, Mapping):
            raise AdapterContractError(
                "manual acceptance evidence must be a mapping"
            )
        evidence = dict(verification_evidence)
        evidence_json = _canonical_json(evidence)
        evidence_digest = hashlib.sha256(
            evidence_json.encode("utf-8")
        ).hexdigest()
        now = _utc_now()

        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT target.*, job.run_id
                FROM oneclick_release_targets AS target
                JOIN oneclick_release_jobs AS job
                  ON job.job_id = target.job_id
                WHERE job.run_id = ? AND target.target_label = ?
                """,
                (clean_run_id, clean_target),
            ).fetchone()
            if not row:
                raise SystemicIdentityError(
                    "manual acceptance target was not found"
                )
            authority = connection.execute(
                """
                SELECT job.plan_id, job.run_id, job.payload_digest,
                       job.targets_digest, job.status AS job_status,
                       plan.status AS plan_status,
                       plan.payload_digest AS plan_payload_digest,
                       plan.target_labels_json,
                       approval.status AS approval_status,
                       approval.approved_by, approval.user_approved,
                       approval.payload_digest AS approval_payload_digest,
                       run.plan_id AS run_plan_id,
                       run.status AS run_status
                FROM oneclick_release_jobs AS job
                JOIN release_plans AS plan ON plan.plan_id = job.plan_id
                JOIN release_approvals AS approval
                  ON approval.plan_id = plan.plan_id
                JOIN release_runs AS run ON run.run_id = job.run_id
                WHERE job.job_id = ?
                """,
                (row["job_id"],),
            ).fetchone()
            if (
                not authority
                or authority["run_id"] != clean_run_id
                or authority["run_plan_id"] != authority["plan_id"]
                or authority["plan_status"] != "APPROVED"
                or authority["approval_status"] != "APPROVED"
                or authority["approved_by"] != "Kyle"
                or authority["user_approved"] != 1
                or authority["payload_digest"]
                != authority["plan_payload_digest"]
                or authority["payload_digest"]
                != authority["approval_payload_digest"]
                or authority["job_status"]
                not in {
                    "WAITING_MANUAL_ACCEPTANCE",
                    "BLOCKED",
                    "SUCCEEDED",
                }
                or authority["targets_digest"]
                != _digest_json(
                    json.loads(authority["target_labels_json"])
                )
                or clean_target
                not in json.loads(authority["target_labels_json"])
                or authority["run_status"] == "SUPERSEDED"
            ):
                raise SystemicIdentityError(
                    "manual acceptance authority identity drifted"
                )
            result = (
                json.loads(row["result_json"])
                if row["result_json"]
                else {}
            )
            if row["status"] == SUCCEEDED:
                if (
                    result.get("manual_acceptance_evidence_digest")
                    != evidence_digest
                    or result.get("manual_review_status") != "ACCEPTED"
                ):
                    raise SystemicIdentityError(
                        "manual acceptance is already terminal with different evidence"
                    )
                return {
                    "idempotent": True,
                    "external_writes_performed": [],
                    "job": self._project_job_in_transaction(
                        connection,
                        row["job_id"],
                    ),
                }
            if row["status"] not in {
                SUCCEEDED_MANUAL_REVIEW,
                SUBMITTED_UNVERIFIED,
            }:
                raise SystemicIdentityError(
                    "one-click target is not awaiting manual acceptance"
                )

            canonical = connection.execute(
                """
                SELECT * FROM release_target_runs
                WHERE run_id = ? AND target_label = ?
                """,
                (clean_run_id, clean_target),
            ).fetchone()
            if not canonical:
                raise SystemicIdentityError(
                    "canonical manual-acceptance target was not found"
                )
            if (
                canonical["attempts"] != row["dispatch_count"]
                or canonical["attempts"] < 1
                or result.get("canonical_status") != row["status"]
                or not _is_digest(result.get("evidence_digest"))
            ):
                raise SystemicIdentityError(
                    "manual acceptance dispatch receipt identity drifted"
                )
            outcome = connection.execute(
                """
                SELECT receipt_json, receipt_digest
                FROM oneclick_release_outcomes
                WHERE job_id = ? AND target_label = ? AND attempt = ?
                """,
                (
                    row["job_id"],
                    clean_target,
                    row["dispatch_count"],
                ),
            ).fetchone()
            if (
                not outcome
                or hashlib.sha256(
                    outcome["receipt_json"].encode("utf-8")
                ).hexdigest()
                != outcome["receipt_digest"]
            ):
                raise SystemicIdentityError(
                    "manual acceptance outcome receipt identity drifted"
                )
            outcome_receipt = json.loads(outcome["receipt_json"])
            expected_outcome = (
                "SUBMITTED_UNVERIFIED"
                if row["status"] == SUBMITTED_UNVERIFIED
                else "SUCCESS"
            )
            if (
                outcome_receipt.get("outcome", {}).get("class")
                != expected_outcome
                or outcome_receipt.get("manual", {}).get("status")
                != "PENDING"
            ):
                raise SystemicIdentityError(
                    "manual acceptance outcome receipt is not pending"
                )

            if row["status"] == SUBMITTED_UNVERIFIED:
                _validate_marketplace_manual_acceptance_evidence(evidence)
                submission = connection.execute(
                    """
                    SELECT * FROM release_target_submissions
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (clean_run_id, clean_target),
                ).fetchone()
                if (
                    not submission
                    or submission["status"] != "SUBMITTED_UNVERIFIED"
                    or submission["evidence_digest"]
                    != result["evidence_digest"]
                ):
                    raise SystemicIdentityError(
                        "accepted submission receipt is unavailable"
                    )
                connection.execute(
                    """
                    UPDATE release_target_submissions
                    SET status = 'MANUALLY_VERIFIED', verified_by = ?,
                        verified_at = ?, verification_evidence_json = ?,
                        verification_evidence_digest = ?
                    WHERE run_id = ? AND target_label = ?
                      AND status = 'SUBMITTED_UNVERIFIED'
                    """,
                    (
                        verified_by,
                        now,
                        evidence_json,
                        evidence_digest,
                        clean_run_id,
                        clean_target,
                    ),
                )
            else:
                _validate_observation_manual_acceptance_evidence(
                    evidence,
                    result,
                )
                if (
                    evidence.get("job_identity_digest")
                    != hashlib.sha256(
                        row["job_id"].encode("utf-8")
                    ).hexdigest()
                    or evidence.get("outcome_receipt_digest")
                    != outcome["receipt_digest"]
                ):
                    raise SystemicIdentityError(
                        "verified observation acceptance identity drifted"
                    )
                readback = connection.execute(
                    """
                    SELECT evidence_digest FROM release_target_readbacks
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (clean_run_id, clean_target),
                ).fetchone()
                if (
                    canonical["status"] != "SUCCEEDED"
                    or not readback
                    or readback["evidence_digest"]
                    != result["evidence_digest"]
                    or evidence.get("readback_evidence_digest")
                    != readback["evidence_digest"]
                ):
                    raise SystemicIdentityError(
                        "verified success readback is unavailable"
                    )

            resolution = {
                "schema_version": MANUAL_ACCEPTANCE_RESOLUTION_SCHEMA,
                "source_outcome_receipt_digest": outcome["receipt_digest"],
                "target_attempt_identity_digest": _digest_json(
                    {
                        "job_id": row["job_id"],
                        "target_label": clean_target,
                        "attempt": row["dispatch_count"],
                    }
                ),
                "acceptance_evidence_digest": evidence_digest,
                "manual": {
                    "status": "ACCEPTED",
                    "reviewer_role": "approved_release_actor",
                },
                "external_writes_performed": [],
            }
            resolution_json = _canonical_json(resolution)
            resolution_digest = hashlib.sha256(
                resolution_json.encode("utf-8")
            ).hexdigest()
            existing_resolution = connection.execute(
                """
                SELECT resolution_digest
                FROM oneclick_release_manual_acceptances
                WHERE job_id = ? AND target_label = ? AND attempt = ?
                """,
                (
                    row["job_id"],
                    clean_target,
                    row["dispatch_count"],
                ),
            ).fetchone()
            if existing_resolution:
                raise SystemicIdentityError(
                    "manual acceptance resolution already exists"
                )
            connection.execute(
                """
                INSERT INTO oneclick_release_manual_acceptances (
                    job_id, target_label, attempt,
                    resolution_json, resolution_digest,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["job_id"],
                    clean_target,
                    row["dispatch_count"],
                    resolution_json,
                    resolution_digest,
                    now,
                    now,
                ),
            )
            accepted_result = {
                **result,
                "canonical_status": SUCCEEDED,
                "manual_review": True,
                "manual_review_status": "ACCEPTED",
                "manual_acceptance_evidence_digest": evidence_digest,
            }
            durable_reason = _durable_reason_detail(
                "CAPABILITY",
                "manual_acceptance_recorded",
                "Kyle accepted the verified manual-review evidence",
            )
            connection.execute(
                """
                UPDATE oneclick_release_targets
                SET status = 'SUCCEEDED', manual_after_submit = 0,
                    reason_category = 'CAPABILITY',
                    reason_scope = 'TARGET',
                    reason_code = 'manual_acceptance_recorded',
                    reason_detail = ?, result_json = ?,
                    updated_at = ?, completed_at = ?
                WHERE job_id = ? AND target_label = ?
                """,
                (
                    durable_reason,
                    _canonical_json(accepted_result),
                    now,
                    now,
                    row["job_id"],
                    clean_target,
                ),
            )
            self._event(
                connection,
                row["job_id"],
                clean_target,
                "TARGET_MANUAL_ACCEPTANCE",
                {
                    "verified_by": verified_by,
                    "verification_evidence_digest": evidence_digest,
                    "prior_status": row["status"],
                },
                now,
            )
            self._refresh_job(connection, row["job_id"])
            return {
                "idempotent": False,
                "external_writes_performed": [],
                "job": self._project_job_in_transaction(
                    connection,
                    row["job_id"],
                ),
            }

    def recover_interrupted_dispatches(self) -> int:
        """Fail closed after process interruption without redispatch."""

        if not self.path.is_file():
            return 0
        now = _utc_now()
        recovered = 0
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT target.*, job.run_id, job.plan_id,
                       job.payload_digest, job.targets_digest
                FROM oneclick_release_targets AS target
                JOIN oneclick_release_jobs AS job ON job.job_id = target.job_id
                WHERE target.status = 'DISPATCHING'
                """
            ).fetchall()
            for row in rows:
                known_writes = _stored_write_classes(row)
                _, confirmed_lower, possible_upper = (
                    _stored_write_count_bounds(row)
                )
                interrupted_upper = (
                    possible_upper
                    if row["control_target"] == 1
                    else (
                        possible_upper + 1
                        if possible_upper is not None
                        else None
                    )
                )
                cumulative_writes = _merge_write_classes(
                    known_writes,
                    (_UNKNOWN_WRITE_CLASS,),
                )
                evidence = {
                    "schema_version": PREPARED_COMMAND_SCHEMA,
                    "reason_category": "POST_WRITE",
                    "reason_scope": TARGET_REASON_SCOPE,
                    "reason_code": "worker_interrupted_dispatch_unknown",
                    "external_writes_performed": list(cumulative_writes),
                    "cumulative_external_write_count": (
                        None
                    ),
                    "confirmed_external_write_count_lower_bound": (
                        confirmed_lower
                    ),
                    "possible_external_write_count_upper_bound": (
                        interrupted_upper
                    ),
                    "dispatch_outcome_unknown": True,
                    "durable_state_uncertain": True,
                }
                encoded = _canonical_json(evidence)
                digest = _digest_json(evidence)
                canonical = connection.execute(
                    """
                    SELECT status, attempts, external_id
                    FROM release_target_runs
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
                if canonical and canonical["status"] == "RUNNING":
                    durable_detail = _durable_reason_detail(
                        "POST_WRITE",
                        "worker_interrupted_dispatch_unknown",
                        "worker interrupted after atomic claim",
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO release_target_failure_events (
                            run_id, target_label, attempt, evidence_json,
                            evidence_digest, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["run_id"],
                            row["target_label"],
                            max(1, int(canonical["attempts"])),
                            encoded,
                            digest,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE release_target_runs
                        SET status = 'FAILED',
                            error = ?,
                            updated_at = ?, completed_at = ?
                        WHERE run_id = ? AND target_label = ?
                        """,
                        (
                            durable_detail,
                            now,
                            now,
                            row["run_id"],
                            row["target_label"],
                        ),
                    )
                else:
                    durable_detail = _durable_reason_detail(
                        "POST_WRITE",
                        "worker_interrupted_dispatch_unknown",
                        "worker interrupted after atomic claim",
                    )
                connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET status = 'RECONCILIATION_REQUIRED',
                        reason_category = 'POST_WRITE',
                        reason_scope = 'TARGET',
                        reason_code = 'worker_interrupted_dispatch_unknown',
                        reason_detail = ?,
                        cumulative_external_writes_json = ?,
                        cumulative_external_writes_digest = ?,
                        cumulative_external_write_count = NULL,
                        cumulative_external_write_lower_bound = ?,
                        cumulative_external_write_upper_bound = ?,
                        result_json = ?, updated_at = ?, completed_at = ?
                    WHERE job_id = ? AND target_label = ?
                    """,
                    (
                        durable_detail,
                        _canonical_json(list(cumulative_writes)),
                        _digest_json(list(cumulative_writes)),
                        confirmed_lower,
                        interrupted_upper,
                        _canonical_json(
                            {
                                "external_write_count": None,
                                "external_write_classes": list(
                                    cumulative_writes
                                ),
                                "cumulative_external_write_count": None,
                                "cumulative_external_write_classes": list(
                                    cumulative_writes
                                ),
                                "confirmed_external_write_count_lower_bound": (
                                    confirmed_lower
                                ),
                                "possible_external_write_count_upper_bound": (
                                    interrupted_upper
                                ),
                                "dispatch_outcome_unknown": True,
                                "evidence_digest": digest,
                            }
                        ),
                        now,
                        now,
                        row["job_id"],
                        row["target_label"],
                    ),
                )
                recovered_result = DispatchTargetResult(
                    canonical_status=RECONCILIATION_REQUIRED,
                    reason_category="POST_WRITE",
                    reason_scope=TARGET_REASON_SCOPE,
                    reason_code="worker_interrupted_dispatch_unknown",
                    reason_detail="worker interrupted after atomic claim",
                    external_writes=cumulative_writes,
                    external_write_count=None,
                    confirmed_external_write_count_lower_bound=(
                        confirmed_lower
                    ),
                    possible_external_write_count_upper_bound=(
                        interrupted_upper
                    ),
                    external_id=(
                        canonical["external_id"] if canonical else None
                    ),
                    dispatch_outcome_unknown=True,
                    evidence={"durable_state_uncertain": True},
                )
                if row["control_target"] != 1:
                    _insert_outcome_receipt(
                        connection,
                        job=row,
                        target=row,
                        result=recovered_result,
                        evidence_digest=digest,
                        now=now,
                    )
                    self._refresh_canonical_run(
                        connection,
                        row["run_id"],
                        now,
                    )
                self._refresh_job(connection, row["job_id"])
                recovered += 1
        return recovered

    def resume_exact_zero_write_failures(self, job_id: str) -> int:
        """Allow only explicit exact zero-write failures to be prepared again."""

        now = _utc_now()
        resumed = 0
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM oneclick_release_targets
                WHERE job_id = ? AND status = 'FAILED_PRE_SUBMIT'
                """,
                (job_id,),
            ).fetchall()
            for row in rows:
                result = (
                    json.loads(row["result_json"]) if row["result_json"] else {}
                )
                if (
                    result.get("external_write_count") != 0
                    or result.get("external_write_classes") != []
                    or result.get("dispatch_outcome_unknown") is not False
                ):
                    continue
                connection.execute(
                    """
                    UPDATE oneclick_release_targets
                    SET status = 'PENDING', capability = NULL,
                        command_json = NULL, command_digest = NULL,
                        proof_json = NULL, proof_digest = NULL,
                        cumulative_external_writes_json = '[]',
                        cumulative_external_writes_digest =
                            '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
                        cumulative_external_write_count = 0,
                        cumulative_external_write_lower_bound = 0,
                        cumulative_external_write_upper_bound = 0,
                        dispatch_stage = NULL,
                        dispatch_stage_evidence_digest = NULL,
                        result_json = NULL, completed_at = NULL,
                        updated_at = ?
                    WHERE job_id = ? AND target_label = ?
                    """,
                    (now, job_id, row["target_label"]),
                )
                if row["control_target"] == 1:
                    connection.execute(
                        """
                        UPDATE oneclick_release_targets
                        SET shared_resource_json = CASE
                                WHEN target_label = ? THEN NULL
                                ELSE shared_resource_json
                            END,
                            shared_resource_digest = CASE
                                WHEN target_label = ? THEN NULL
                                ELSE shared_resource_digest
                            END,
                            shared_resource_context_json = NULL,
                            shared_resource_context_digest = NULL,
                            updated_at = ?
                        WHERE job_id = ? AND target_label LIKE 'shopee:%'
                        """,
                        (
                            SHOPEE_GLOBAL_TARGET,
                            SHOPEE_GLOBAL_TARGET,
                            now,
                            job_id,
                        ),
                    )
                # Compatibility reset is safe only because the durable event
                # proves the previous attempt performed zero external writes.
                job = connection.execute(
                    "SELECT run_id FROM oneclick_release_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                connection.execute(
                    """
                    UPDATE release_target_runs
                    SET status = 'PENDING', external_id = NULL, error = NULL,
                        completed_at = NULL, updated_at = ?
                    WHERE run_id = ? AND target_label = ? AND status = 'FAILED'
                    """,
                    (now, job["run_id"], row["target_label"]),
                )
                resumed += 1
            if resumed:
                connection.execute(
                    """
                    UPDATE oneclick_release_jobs
                    SET status = 'PREPARING', completed_at = NULL, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (now, job_id),
                )
        return resumed

    def _prepare_one(
        self,
        context: Mapping[str, Any],
        row: Mapping[str, Any],
        registry: Mapping[str, AdapterRegistration],
    ) -> dict[str, Any]:
        label = str(row["target_label"])
        current_status = str(row["status"])
        if current_status in {
            SUCCEEDED,
            SUCCEEDED_MANUAL_REVIEW,
            SUBMITTED_UNVERIFIED,
            RECONCILIATION_REQUIRED,
        }:
            return _prepared_terminal_row(label, current_status)
        if current_status == FAILED_PRE_SUBMIT:
            return _prepared_blocked_row(
                label,
                SAFE_ACTION_REQUIRED,
                FAILED_PRE_SUBMIT,
                "SAFE_ACTION",
                "exact_zero_write_retry_requires_explicit_resume",
                "exact zero-write failure requires explicit retry",
            )
        if current_status != PENDING:
            return _prepared_blocked_row(
                label,
                BLOCKED_CAPABILITY,
                BLOCKED_CAPABILITY,
                "CAPABILITY",
                "canonical_target_not_pristine",
                "target is not a pristine first attempt",
            )
        if label == "ozon:RU":
            inventory_reason = _ozon_inventory_blocker(
                context["plan"]["payload"]
            )
            if inventory_reason:
                return _prepared_blocked_row(
                    label,
                    BLOCKED_INVENTORY,
                    BLOCKED_INVENTORY,
                    "INVENTORY",
                    inventory_reason,
                    "approved READY Ozon inventory decision is unavailable",
                )
        adapter_name = _adapter_name_for_target(label)
        registration = registry.get(adapter_name)
        if (
            not registration
            or label not in registration.target_labels
            or not registration.preparation_available
            or not registration.dispatch_available
        ):
            return _prepared_blocked_row(
                label,
                BLOCKED_CAPABILITY,
                BLOCKED_CAPABILITY,
                "CAPABILITY",
                "prepare_or_dispatch_seam_unavailable",
                "adapter requires read-only prepare and governed dispatch seams",
            )
        request = PrepareTargetRequest(
            schema_version=PREPARED_COMMAND_SCHEMA,
            plan_id=context["job"]["plan_id"],
            run_id=context["job"]["run_id"],
            target_label=label,
            product_revision=context["job"]["product_revision"],
            payload_digest=context["job"]["payload_digest"],
            confirmation_token_digest=context["job"][
                "confirmation_token_digest"
            ],
            targets_digest=context["job"]["targets_digest"],
            source_identity_digest=context["job"]["source_identity_digest"],
            source_identity_payload_digest=context["job"][
                "source_identity_payload_digest"
            ],
            source_identity=dict(context["source_identity"]),
            sku_lineage_digest=context["job"]["sku_lineage_digest"],
            sku_lineage_payload_digest=context["job"][
                "sku_lineage_payload_digest"
            ],
            adapter_policy_digest=registration.policy_digest,
            idempotency_key=str(row["idempotency_key"]),
            immutable_plan_payload=context["plan"]["payload"],
        )
        try:
            result = PrepareTargetResult.from_value(registration.prepare(request))
        except AdapterPreparationError as error:
            return _prepared_blocked_row(
                label,
                _classification_for_category(error.category),
                _status_for_classification(
                    _classification_for_category(error.category)
                ),
                error.category,
                error.code,
                error.detail,
                scope=error.scope,
            )
        except Exception as error:
            return _prepared_blocked_row(
                label,
                BLOCKED_CAPABILITY,
                BLOCKED_CAPABILITY,
                "SYSTEMIC_CONTRACT",
                "adapter_prepare_contract_error",
                str(error),
                scope=SYSTEMIC_IDENTITY_SCOPE,
            )
        if result.classification not in READY_CLASSIFICATIONS:
            return _prepared_blocked_row(
                label,
                result.classification,
                _status_for_classification(result.classification),
                result.reason_category,
                result.reason_code,
                result.reason_detail,
                scope=result.reason_scope,
            )
        try:
            shared_resource = _validated_shared_resource_declaration(
                request,
                result.shared_resource,
            )
        except AdapterContractError as error:
            return _prepared_blocked_row(
                label,
                BLOCKED_CAPABILITY,
                BLOCKED_CAPABILITY,
                "SYSTEMIC_CONTRACT",
                "shared_resource_contract_invalid",
                str(error),
                scope=SYSTEMIC_IDENTITY_SCOPE,
            )
        command = {
            "schema_version": PREPARED_COMMAND_SCHEMA,
            "target_label": label,
            "adapter_policy_digest": registration.policy_digest,
            "source_identity_digest": context["job"]["source_identity_digest"],
            "source_identity_payload_digest": context["job"][
                "source_identity_payload_digest"
            ],
            "sku_lineage_digest": context["job"]["sku_lineage_digest"],
            "sku_lineage_payload_digest": context["job"][
                "sku_lineage_payload_digest"
            ],
            "shared_resource": shared_resource,
            "payload": dict(result.command or {}),
        }
        proof = {
            "schema_version": PREPARED_COMMAND_SCHEMA,
            "target_label": label,
            "adapter_policy_digest": registration.policy_digest,
            "source_identity_digest": context["job"]["source_identity_digest"],
            "source_identity_payload_digest": context["job"][
                "source_identity_payload_digest"
            ],
            "sku_lineage_digest": context["job"]["sku_lineage_digest"],
            "sku_lineage_payload_digest": context["job"][
                "sku_lineage_payload_digest"
            ],
            "payload": dict(result.proof or {}),
        }
        prepared = {
            "target_label": label,
            "classification": result.classification,
            "status": (
                SUCCEEDED
                if shared_resource
                and shared_resource["mode"] == "EXISTING_GLOBAL"
                else READY
            ),
            "reason_category": result.reason_category,
            "reason_scope": result.reason_scope,
            "reason_code": result.reason_code,
            "reason_detail": result.reason_detail,
            "command_json": _canonical_json(command),
            "command_digest": _digest_json(command),
            "proof_json": _canonical_json(proof),
            "proof_digest": _digest_json(proof),
            "manual_after_submit": result.manual_after_submit,
            "shared_resource_json": (
                _canonical_json(shared_resource) if shared_resource else None
            ),
            "shared_resource_digest": (
                _digest_json(shared_resource) if shared_resource else None
            ),
        }
        if (
            shared_resource
            and shared_resource["mode"] == "EXISTING_GLOBAL"
        ):
            context_payload = _verified_shared_resource_context(
                shared_resource
            )
            prepared["shared_resource_context_json"] = _canonical_json(
                context_payload
            )
            prepared["shared_resource_context_digest"] = _digest_json(
                context_payload
            )
            prepared["result_json"] = _canonical_json(
                {
                    "canonical_status": SUCCEEDED,
                    "shared_resource_status": "VERIFIED_EXISTING_NO_WRITE",
                    "external_write_count": 0,
                    "external_write_classes": [],
                    "dispatch_outcome_unknown": False,
                    "shared_resource_context_digest": _digest_json(
                        context_payload
                    ),
                }
            )
        return prepared

    def _load_exact_context(
        self,
        job_id: str,
        registry: Mapping[str, AdapterRegistration],
    ) -> dict[str, Any]:
        job = self._raw_job(job_id)
        if not job:
            raise OneClickControlPlaneError("one-click job was not found")
        release = ReleaseStore(self.path)
        plan = release.get_plan(job["plan_id"])
        run = release.get_run(job["run_id"])
        if not plan or not run:
            raise SystemicIdentityError("approved plan/run is unavailable")
        identity = _batch_identity(
            plan=plan,
            run=run,
            product_revision=job["product_revision"],
            registry=registry,
        )
        _require_job_identity(job, identity)
        targets = _run_targets(run)
        source_identity = _resolve_plan_source_identity(plan["payload"])
        raw_targets = self._raw_targets(job_id)
        expected_labels = _execution_target_labels(_target_labels(plan))
        if (
            [row["target_label"] for row in raw_targets] != expected_labels
            or sum(
                row["target_label"] == SHOPEE_GLOBAL_TARGET
                for row in raw_targets
            )
            > 1
        ):
            raise SystemicIdentityError(
                "one-click control target identity drifted"
            )
        return {
            "job": job,
            "plan": plan,
            "run": run,
            "source_identity": source_identity,
            "targets": [
                {
                    **dict(row),
                    "idempotency_key": (
                        _shopee_global_idempotency_key(job)
                        if row["target_label"] == SHOPEE_GLOBAL_TARGET
                        else targets[row["target_label"]]["idempotency_key"]
                    ),
                }
                for row in raw_targets
            ],
        }

    def _require_exact_context_in_transaction(
        self,
        connection: sqlite3.Connection,
        job: Mapping[str, Any],
        registry: Mapping[str, AdapterRegistration],
    ) -> None:
        plan = connection.execute(
            "SELECT * FROM release_plans WHERE plan_id = ?",
            (job["plan_id"],),
        ).fetchone()
        approval = connection.execute(
            """
            SELECT * FROM release_approvals
            WHERE plan_id = ? AND status = 'APPROVED'
            """,
            (job["plan_id"],),
        ).fetchone()
        run = connection.execute(
            "SELECT * FROM release_runs WHERE run_id = ?",
            (job["run_id"],),
        ).fetchone()
        reservation = connection.execute(
            """
            SELECT * FROM release_sku_reservations
            WHERE plan_id = ? AND status = 'ACTIVE'
            """,
            (job["plan_id"],),
        ).fetchone()
        source_reservation = connection.execute(
            """
            SELECT reservation.*
            FROM release_source_sku_plan_links AS link
            JOIN release_source_sku_reservations AS reservation
              ON reservation.reservation_digest = link.reservation_digest
            WHERE link.plan_id = ?
            """,
            (job["plan_id"],),
        ).fetchone()
        plan_payload = json.loads(plan["payload_json"]) if plan else {}
        transaction_plan = (
            {
                **dict(plan),
                "payload": plan_payload,
                "sku_reservation": dict(reservation) if reservation else None,
                "source_sku_reservation": (
                    {
                        **dict(source_reservation),
                        "assignment": json.loads(
                            source_reservation["assignment_json"]
                        ),
                    }
                    if source_reservation
                    else None
                ),
            }
            if plan
            else {}
        )
        transaction_lineage = (
            _resolve_plan_sku_lineage(
                plan_payload,
                plan=transaction_plan,
            )
            if plan and (reservation or source_reservation)
            else {}
        )
        legacy_reservation_exact = bool(
            reservation
            and reservation["product_id"] == plan["product_id"]
            and reservation["seller_sku"] == plan["seller_sku"]
        )
        source_reservation_exact = bool(
            source_reservation
            and source_reservation["status"] == "ACTIVE"
            and json.loads(source_reservation["assignment_json"]).get(
                "seller_sku"
            )
            == plan["seller_sku"]
        )
        if (
            not plan
            or plan["status"] != "APPROVED"
            or not approval
            or not run
            or run["plan_id"] != job["plan_id"]
            or (
                not legacy_reservation_exact
                and not source_reservation_exact
            )
            or plan["payload_digest"] != job["payload_digest"]
            or _digest_text(plan["confirmation_token"])
            != job["confirmation_token_digest"]
            or _digest_json(json.loads(plan["target_labels_json"]))
            != job["targets_digest"]
            or _source_identity_digest(plan_payload)
            != job["source_identity_digest"]
            or _digest_json(
                _resolve_plan_source_identity(
                    plan_payload
                )
            )
            != job["source_identity_payload_digest"]
            or _sku_lineage_identity_digest(transaction_lineage)
            != job["sku_lineage_digest"]
            or _digest_json(transaction_lineage)
            != job["sku_lineage_payload_digest"]
            or _registry_digest(
                _execution_target_labels(
                    json.loads(plan["target_labels_json"])
                ),
                registry,
            )
            != job["adapter_policy_digest"]
        ):
            raise SystemicIdentityError(
                "plan/run/approval/registry identity drifted"
            )

    def _dependency_satisfied(
        self,
        connection: sqlite3.Connection,
        job: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> bool:
        rows = connection.execute(
            """
            SELECT *
            FROM oneclick_release_targets
            WHERE job_id = ?
            """,
            (job["job_id"],),
        ).fetchall()
        _validate_job_shared_resource_rows(rows)
        statuses = {
            row["target_label"]: row["status"]
            for row in rows
        }
        return _dependency_state(
            target["target_label"],
            statuses,
        )["satisfied"]

    def _stop_systemic_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        code: str,
        detail: str,
    ) -> None:
        now = _utc_now()
        clean_code = _clean_code(code, "systemic_identity")
        durable_reason = json.loads(
            _durable_reason_detail(
                "SYSTEMIC_IDENTITY",
                clean_code,
                detail,
            )
        )
        reason = {
            "category": "SYSTEMIC_IDENTITY",
            "scope": SYSTEMIC_IDENTITY_SCOPE,
            "code": clean_code,
            **durable_reason,
        }
        connection.execute(
            """
            UPDATE oneclick_release_jobs
            SET status = 'SYSTEMIC_STOPPED', systemic_reason_json = ?,
                updated_at = ?, completed_at = ?
            WHERE job_id = ?
            """,
            (_canonical_json(reason), now, now, job_id),
        )

    def _refresh_job(self, connection: sqlite3.Connection, job_id: str) -> None:
        rows = connection.execute(
            """
            SELECT * FROM oneclick_release_targets
            WHERE job_id = ? ORDER BY ordinal
            """,
            (job_id,),
        ).fetchall()
        statuses = [row["status"] for row in rows]
        runnable_ready = _runnable_ready_count(rows)
        if any(status == DISPATCHING for status in statuses):
            status = "RUNNING"
            completed_at = None
        elif runnable_ready:
            status = "READY"
            completed_at = None
        elif any(status == RECONCILIATION_REQUIRED for status in statuses):
            status = "BLOCKED"
            completed_at = _utc_now()
        elif any(
            status in {
                SUCCEEDED_MANUAL_REVIEW,
                SUBMITTED_UNVERIFIED,
            }
            for status in statuses
        ):
            status = "WAITING_MANUAL_ACCEPTANCE"
            completed_at = _utc_now()
        elif any(status == READY for status in statuses):
            # Every READY row is dependency-blocked.  Keeping the job READY
            # would make the background worker poll forever while no atomic
            # claim can ever succeed.
            status = "BLOCKED"
            completed_at = _utc_now()
        elif statuses and all(
            value
            in {
                SUCCEEDED,
                SUCCEEDED_MANUAL_REVIEW,
                BLOCKED_AUTH,
                BLOCKED_INVENTORY,
                BLOCKED_CAPABILITY,
                BLOCKED_SOURCE_IDENTITY,
                BLOCKED_SKU_LINEAGE,
                FAILED_PRE_SUBMIT,
            }
            for value in statuses
        ):
            status = (
                "SUCCEEDED"
                if all(value == SUCCEEDED for value in statuses)
                else "BLOCKED"
            )
            completed_at = _utc_now()
        else:
            status = "RUNNING"
            completed_at = None
        connection.execute(
            """
            UPDATE oneclick_release_jobs
            SET status = ?, updated_at = ?, completed_at = ?
            WHERE job_id = ?
            """,
            (status, _utc_now(), completed_at, job_id),
        )

    def _refresh_canonical_run(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        now: str,
    ) -> None:
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM release_target_runs WHERE run_id = ?",
                (run_id,),
            )
        ]
        if statuses and all(status == "SUCCEEDED" for status in statuses):
            status, completed = "SUCCEEDED", now
        elif "FAILED" in statuses and "SUCCEEDED" in statuses:
            status, completed = "PARTIAL_FAILED", None
        elif "FAILED" in statuses:
            status, completed = "FAILED", None
        elif "RUNNING" in statuses or "SUCCEEDED" in statuses:
            status, completed = "RUNNING", None
        else:
            status, completed = "PENDING", None
        connection.execute(
            """
            UPDATE release_runs
            SET status = ?, updated_at = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (status, now, completed, run_id),
        )

    def _project_job_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_id: str,
    ) -> dict[str, Any]:
        job = connection.execute(
            "SELECT * FROM oneclick_release_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not job:
            raise OneClickControlPlaneError("one-click job was not found")
        plan_row = connection.execute(
            "SELECT payload_json FROM release_plans WHERE plan_id = ?",
            (job["plan_id"],),
        ).fetchone()
        source_identity = (
            _resolve_plan_source_identity(json.loads(plan_row["payload_json"]))
            if plan_row
            else {}
        )
        rows = connection.execute(
            """
            SELECT * FROM oneclick_release_targets
            WHERE job_id = ? ORDER BY ordinal
            """,
            (job_id,),
        ).fetchall()
        _validate_job_shared_resource_rows(rows)
        status_by_label = {
            row["target_label"]: row["status"]
            for row in rows
        }
        all_targets = [
            _public_target(
                dict(row),
                dependency=_dependency_state(
                    row["target_label"],
                    status_by_label,
                ),
            )
            for row in rows
        ]
        _attach_shared_control_dependency_summaries(all_targets)
        outcomes = {
            row["target_label"]: row
            for row in connection.execute(
                """
                SELECT target_label, attempt, receipt_digest, consumer_status,
                       fact_digest, error_code
                FROM oneclick_release_outcomes
                WHERE job_id = ?
                ORDER BY target_label, attempt
                """,
                (job_id,),
            )
        }
        manual_resolutions = {
            row["target_label"]: row
            for row in connection.execute(
                """
                SELECT target_label, attempt, resolution_digest,
                       consumer_status, fact_digest, error_code
                FROM oneclick_release_manual_acceptances
                WHERE job_id = ?
                ORDER BY target_label, attempt
                """,
                (job_id,),
            )
        }
        for target in all_targets:
            outcome = outcomes.get(target["target_label"])
            resolution = manual_resolutions.get(
                target["target_label"]
            )
            target["outcome_receipt"] = (
                {
                    "schema_version": "release-outcome-receipt/v1",
                    "attempt": outcome["attempt"],
                    "receipt_digest": outcome["receipt_digest"],
                    "consumer_status": outcome["consumer_status"],
                    "fact_digest": outcome["fact_digest"],
                    "error_code": outcome["error_code"],
                    "manual_resolution": (
                        {
                            "schema_version": (
                                MANUAL_ACCEPTANCE_RESOLUTION_SCHEMA
                            ),
                            "attempt": resolution["attempt"],
                            "resolution_digest": resolution[
                                "resolution_digest"
                            ],
                            "consumer_status": resolution[
                                "consumer_status"
                            ],
                            "fact_digest": resolution["fact_digest"],
                            "error_code": resolution["error_code"],
                        }
                        if resolution
                        else None
                    ),
                }
                if outcome
                else None
            )
        shared_controls = [
            row for row in all_targets if row["control_target"]
        ]
        targets = [
            row for row in all_targets if not row["control_target"]
        ]
        storefronts = [row for row in targets if row["storefront"]]
        will_dispatch = [
            row["target_label"]
            for row in storefronts
            if row["runnable_now"] is True
            and row["classification"] == EXACT_READY_AUTOMATIC
        ]
        manual_after_submit = [
            row["target_label"]
            for row in storefronts
            if (
                row["runnable_now"] is True
                and row["classification"] == READY_SUBMIT_MANUAL
            )
            or row["status"]
            in {SUCCEEDED_MANUAL_REVIEW, SUBMITTED_UNVERIFIED}
        ]
        blocked = [
            row["target_label"]
            for row in storefronts
            if row["status"]
            in {
                BLOCKED_AUTH,
                BLOCKED_INVENTORY,
                BLOCKED_CAPABILITY,
                BLOCKED_SOURCE_IDENTITY,
                BLOCKED_SKU_LINEAGE,
                FAILED_PRE_SUBMIT,
                RECONCILIATION_REQUIRED,
            }
            or row["dependency"]["state"] == "BLOCKED"
        ]
        already_terminal = [
            row["target_label"]
            for row in storefronts
            if row["status"]
            in {
                SUCCEEDED,
                SUCCEEDED_MANUAL_REVIEW,
                SUBMITTED_UNVERIFIED,
            }
        ]
        return {
            "schema_version": PUBLIC_STATUS_SCHEMA,
            "job_id": job["job_id"],
            "plan_id": job["plan_id"],
            "run_id": job["run_id"],
            "phase": job["status"],
            "requires_human": job["status"] == "WAITING_MANUAL_ACCEPTANCE",
            "product_revision": job["product_revision"],
            "source_item_code": source_identity.get("source_item_code"),
            "digests": {
                "payload": job["payload_digest"],
                "targets": job["targets_digest"],
                "source_identity": job["source_identity_digest"],
                "source_identity_payload": job[
                    "source_identity_payload_digest"
                ],
                "sku_lineage": job["sku_lineage_digest"],
                "sku_lineage_payload": job[
                    "sku_lineage_payload_digest"
                ],
                "adapter_policy": job["adapter_policy_digest"],
            },
            "systemic_reason": _public_stored_reason(
                job["systemic_reason_json"]
            ),
            "storefront_count": len(storefronts),
            "control_row_count": len(all_targets) - len(storefronts),
            "runnable_target_count": sum(
                target["runnable_now"] is True
                for target in storefronts
            ),
            "summary": {
                "will_dispatch": will_dispatch,
                "manual_after_submit": manual_after_submit,
                "blocked": blocked,
                "already_terminal": already_terminal,
            },
            "targets": targets,
            "shared_controls": shared_controls,
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "completed_at": job["completed_at"],
        }

    def _raw_job(self, job_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM oneclick_release_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.OperationalError:
            return None
        finally:
            connection.close()

    def _raw_targets(self, job_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM oneclick_release_targets
                    WHERE job_id = ? ORDER BY ordinal
                    """,
                    (job_id,),
                )
            ]
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(_SCHEMA)
            _ensure_oneclick_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _event(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        target_label: str | None,
        event_type: str,
        payload: Mapping[str, Any],
        now: str,
    ) -> None:
        encoded = _canonical_json(dict(payload))
        connection.execute(
            """
            INSERT INTO oneclick_release_events (
                job_id, target_label, event_type, event_json,
                event_digest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                target_label,
                event_type,
                encoded,
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                now,
            ),
        )


class OneClickReleaseWorker:
    """One-target-at-a-time worker; safe to wake repeatedly."""

    def __init__(
        self,
        store: OneClickReleaseStore,
        registry_provider: Callable[[], Mapping[str, AdapterRegistration]],
        *,
        dispatch_enabled: Callable[[], bool],
    ):
        self.store = store
        self.registry_provider = registry_provider
        self.dispatch_enabled = dispatch_enabled

    def recover(self) -> int:
        return self.store.recover_interrupted_dispatches()

    def advance_once(self, job_id: str) -> bool:
        registry = dict(self.registry_provider())
        job = self.store.get_job(job_id=job_id)
        if not job:
            return False
        if job["phase"] in {"PENDING", "PREPARING"}:
            self.store.prepare_job(job_id, registry)
            if not self.dispatch_enabled():
                self.store.set_dispatch_capability(
                    job_id,
                    enabled=False,
                )
            return True
        if not self.dispatch_enabled():
            if job["phase"] == "READY":
                self.store.set_dispatch_capability(
                    job_id,
                    enabled=False,
                )
                return True
            return False
        try:
            request = self.store.claim_next_dispatch(job_id, registry)
        except SystemicIdentityError as error:
            self.store.record_systemic_stop(job_id, error)
            return True
        if request is None:
            return False
        request = replace(
            request,
            progress_recorder=self.store.record_dispatch_progress,
        )
        registration = registry.get(_adapter_name_for_target(request.target_label))
        if not registration or not registration.dispatch_available:
            result = DispatchTargetResult(
                canonical_status=BLOCKED_CAPABILITY,
                reason_category="CAPABILITY",
                reason_scope=TARGET_REASON_SCOPE,
                reason_code="dispatch_seam_unavailable_after_claim",
                reason_detail="dispatch seam disappeared after preparation",
                external_writes=(),
            )
        else:
            try:
                result = DispatchTargetResult.from_value(
                    registration.dispatch(request)
                )
                cumulative = self.store.cumulative_external_writes(request)
                if any(item not in result.external_writes for item in cumulative):
                    stored_exact, stored_lower, stored_upper = (
                        self.store.cumulative_external_write_bounds(request)
                    )
                    raise DispatchInvocationError(
                        "adapter receipt omitted a confirmed composite write",
                        external_writes=cumulative,
                        dispatch_outcome_unknown=True,
                        external_id=result.external_id,
                        external_write_count=None,
                        confirmed_external_write_count_lower_bound=(
                            stored_lower
                        ),
                        possible_external_write_count_upper_bound=(
                            stored_upper
                        ),
                    )
            except PreDispatchInvocationError as error:
                result = DispatchTargetResult(
                    canonical_status=FAILED_PRE_SUBMIT,
                    reason_category="PRE_SUBMIT",
                    reason_scope=TARGET_REASON_SCOPE,
                    reason_code="dispatch_failed_before_external_write",
                    reason_detail=str(error),
                    external_writes=(),
                    dispatch_outcome_unknown=False,
                    evidence={"durable_state_uncertain": False},
                )
            except DispatchInvocationError as error:
                stored_exact, stored_lower, stored_upper = (
                    self.store.cumulative_external_write_bounds(request)
                )
                cumulative = _merge_write_classes(
                    self.store.cumulative_external_writes(request),
                    error.external_writes,
                )
                lower_bound = max(
                    stored_lower,
                    error.confirmed_external_write_count_lower_bound,
                )
                upper_candidates = (
                    stored_upper,
                    error.possible_external_write_count_upper_bound,
                )
                upper_bound = (
                    max(value for value in upper_candidates if value is not None)
                    if all(value is not None for value in upper_candidates)
                    else None
                )
                exact_count = (
                    error.external_write_count
                    if (
                        error.dispatch_outcome_unknown is False
                        and error.external_write_count is not None
                        and error.external_write_count >= stored_lower
                    )
                    else None
                )
                if exact_count is not None:
                    lower_bound = exact_count
                    upper_bound = exact_count
                outcome_unknown = (
                    error.dispatch_outcome_unknown or exact_count is None
                )
                if outcome_unknown:
                    cumulative = _merge_write_classes(
                        cumulative,
                        (_UNKNOWN_WRITE_CLASS,),
                    )
                result = DispatchTargetResult(
                    canonical_status=RECONCILIATION_REQUIRED,
                    reason_category="POST_WRITE",
                    reason_scope=TARGET_REASON_SCOPE,
                    reason_code="dispatch_invocation_requires_reconciliation",
                    reason_detail=str(error),
                    external_writes=cumulative,
                    external_write_count=exact_count,
                    confirmed_external_write_count_lower_bound=lower_bound,
                    possible_external_write_count_upper_bound=upper_bound,
                    external_id=error.external_id,
                    dispatch_outcome_unknown=outcome_unknown,
                    evidence={
                        "durable_state_uncertain": True,
                        "cumulative_external_write_count": (
                            exact_count
                        ),
                        "confirmed_external_write_count_lower_bound": (
                            lower_bound
                        ),
                        "possible_external_write_count_upper_bound": (
                            upper_bound
                        ),
                    },
                )
            except Exception as error:
                _, stored_lower, stored_upper = (
                    self.store.cumulative_external_write_bounds(request)
                )
                pending_intent = self.store.has_pending_write_intent(
                    request
                )
                cumulative = _merge_write_classes(
                    self.store.cumulative_external_writes(request),
                    (_UNKNOWN_WRITE_CLASS,),
                )
                result = DispatchTargetResult(
                    canonical_status=RECONCILIATION_REQUIRED,
                    reason_category="POST_WRITE",
                    reason_scope=TARGET_REASON_SCOPE,
                    reason_code="dispatch_invocation_outcome_unknown",
                    reason_detail=str(error),
                    external_writes=cumulative,
                    external_write_count=None,
                    confirmed_external_write_count_lower_bound=stored_lower,
                    possible_external_write_count_upper_bound=(
                        stored_upper
                        if (
                            request.target_label == SHOPEE_GLOBAL_TARGET
                            and pending_intent
                        )
                        else (
                            stored_upper + 1
                            if stored_upper is not None
                            else None
                        )
                    ),
                    dispatch_outcome_unknown=True,
                    evidence={"durable_state_uncertain": True},
                )
        self.store.record_dispatch_result(request, result)
        return True


def preview_run_for_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Build the exact non-persistent run shape used by read-only preparation."""

    payload_digest = str(plan.get("payload_digest") or "")
    if not _is_digest(payload_digest):
        raise SystemicIdentityError("plan payload digest is invalid")
    targets = _target_labels(plan)
    run_id = f"release-run:{payload_digest[:24]}"
    rows = []
    for label in targets:
        channel, _, site = label.partition(":")
        idempotency_digest = _digest_json(
            {"payload_digest": payload_digest, "target_label": label}
        )
        rows.append(
            {
                "run_id": run_id,
                "target_label": label,
                "channel": channel,
                "site": site,
                "status": "PENDING",
                "attempts": 0,
                "idempotency_key": (
                    f"publish:{channel}:{site}:{idempotency_digest[:24]}"
                ),
                "external_id": None,
                "error": None,
                "failure_events": [],
                "readback": None,
                "submission": None,
            }
        )
    return {
        "run_id": run_id,
        "plan_id": str(plan.get("plan_id") or ""),
        "status": "NOT_PERSISTED",
        "targets": rows,
    }


def build_batch_preview(
    *,
    plan: Mapping[str, Any],
    run: Mapping[str, Any],
    product_revision: int,
    registry: Mapping[str, AdapterRegistration],
) -> dict[str, Any]:
    """Pure/read-only preview.  It never creates control-plane tables."""

    identity = _batch_identity(
        plan=plan,
        run=run,
        product_revision=product_revision,
        registry=registry,
    )
    rows = _run_targets(run)
    # Reuse the same classification rules without durable writes.
    pseudo = object.__new__(OneClickReleaseStore)
    context = {
        "job": identity,
        "plan": plan,
        "run": run,
        "source_identity": _resolve_plan_source_identity(plan["payload"]),
    }
    prepared_rows = []
    systemic = None
    for label in _execution_target_labels(_target_labels(plan)):
        result = pseudo._prepare_one(  # type: ignore[attr-defined]
            context,
            {
                "target_label": label,
                "status": (
                    PENDING
                    if label == SHOPEE_GLOBAL_TARGET
                    else _initial_public_status(rows[label])
                ),
                "idempotency_key": (
                    _shopee_global_idempotency_key(identity)
                    if label == SHOPEE_GLOBAL_TARGET
                    else rows[label]["idempotency_key"]
                ),
            },
            registry,
        )
        prepared_rows.append(result)
        if result["reason_scope"] == SYSTEMIC_IDENTITY_SCOPE:
            break
    status_by_label = {
        row["target_label"]: row["status"]
        for row in prepared_rows
    }
    prepared = []
    for result in prepared_rows:
        dependency = _dependency_state(
            result["target_label"],
            status_by_label,
        )
        public = {
            "target_label": result["target_label"],
            "storefront": result["target_label"]
            not in {_COMMON_LABEL, SHOPEE_GLOBAL_TARGET},
            "control_target": (
                result["target_label"] == SHOPEE_GLOBAL_TARGET
            ),
            "classification": result["classification"],
            "status": result["status"],
            "reason": _public_reason(
                category=result["reason_category"],
                scope=result["reason_scope"],
                code=result["reason_code"],
                detail=result["reason_detail"],
            ),
            "manual_after_submit": result.get("manual_after_submit") is True,
            "requires_human": result["status"]
            in {SUCCEEDED_MANUAL_REVIEW, SUBMITTED_UNVERIFIED},
            "dependency": dependency,
            "runnable_now": (
                result["status"] == READY
                and dependency["satisfied"] is True
            ),
            "digests": {
                "prepared_command": result.get("command_digest"),
                "proof": result.get("proof_digest"),
                "adapter_policy": _policy_digest_for_target(
                    result["target_label"],
                    registry,
                ),
                "shared_resource": result.get("shared_resource_digest"),
                "shared_resource_context": result.get(
                    "shared_resource_context_digest"
                ),
            },
            "next_action": _next_action(
                result["status"],
                result["classification"],
                dependency_state=dependency["state"],
                reason_code=result.get("reason_code"),
                reason_category=result.get("reason_category"),
            ),
            "next_action_target": (
                None
                if result.get("reason_code")
                == "oneclick_dispatch_disabled"
                else (
                    dependency["prerequisite_target"]
                    if dependency["state"] in {"BLOCKED", "WAITING"}
                    else result["target_label"]
                )
            ),
        }
        prepared.append(public)
        if result["reason_scope"] == SYSTEMIC_IDENTITY_SCOPE:
            systemic = public["reason"]
            break
    _attach_shared_control_dependency_summaries(prepared)
    shared_controls = [
        row for row in prepared if row["control_target"]
    ]
    visible_targets = [
        row for row in prepared if not row["control_target"]
    ]
    storefronts = [row for row in visible_targets if row["storefront"]]
    return {
        "schema_version": BATCH_PREPARATION_SCHEMA,
        "plan_id": identity["plan_id"],
        "run_id": identity["run_id"],
        "product_revision": identity["product_revision"],
        "digests": {
            "payload": identity["payload_digest"],
            "targets": identity["targets_digest"],
            "source_identity": identity["source_identity_digest"],
            "source_identity_payload": identity[
                "source_identity_payload_digest"
            ],
            "sku_lineage": identity["sku_lineage_digest"],
            "sku_lineage_payload": identity["sku_lineage_payload_digest"],
            "adapter_policy": identity["adapter_policy_digest"],
        },
        "systemic_reason": systemic,
        "storefront_count": len(storefronts),
        "control_row_count": len(prepared) - len(storefronts),
        "runnable_target_count": sum(
            row["runnable_now"] is True for row in storefronts
        ),
        "will_dispatch": [
            row["target_label"]
            for row in storefronts
            if row["runnable_now"] is True
            and row["classification"] == EXACT_READY_AUTOMATIC
        ],
        "manual_after_submit": [
            row["target_label"]
            for row in storefronts
            if row["runnable_now"] is True
            and row["classification"] == READY_SUBMIT_MANUAL
        ],
        "blocked": [
            row["target_label"]
            for row in storefronts
            if row["classification"]
            in {
                BLOCKED_AUTH,
                BLOCKED_INVENTORY,
                BLOCKED_CAPABILITY,
                BLOCKED_SOURCE_IDENTITY,
                BLOCKED_SKU_LINEAGE,
                SAFE_ACTION_REQUIRED,
            }
            or row["dependency"]["state"] == "BLOCKED"
        ],
        "already_terminal": [
            row["target_label"]
            for row in storefronts
            if row["status"]
            in {
                SUCCEEDED,
                SUCCEEDED_MANUAL_REVIEW,
                SUBMITTED_UNVERIFIED,
            }
        ],
        "targets": visible_targets,
        "shared_controls": shared_controls,
    }


def _batch_identity(
    *,
    plan: Mapping[str, Any],
    run: Mapping[str, Any],
    product_revision: int,
    registry: Mapping[str, AdapterRegistration],
) -> dict[str, Any]:
    if type(product_revision) is not int or product_revision < 0:
        raise SystemicIdentityError("product revision must be a non-negative int")
    payload = plan.get("payload")
    approval = plan.get("approval")
    if (
        not isinstance(payload, Mapping)
        or plan.get("status") != "APPROVED"
        or not isinstance(approval, Mapping)
        or approval.get("status") != "APPROVED"
        or run.get("plan_id") != plan.get("plan_id")
    ):
        raise SystemicIdentityError("active approved plan/run is required")
    _require_active_sku_reservation(plan)
    targets = _target_labels(plan)
    rows = _run_targets(run)
    if set(rows) != set(targets):
        raise SystemicIdentityError("run target identity differs from plan")
    payload_digest = str(plan.get("payload_digest") or "")
    token = str(plan.get("confirmation_token") or "")
    if not _is_digest(payload_digest) or not token:
        raise SystemicIdentityError("plan digest/token identity is invalid")
    targets_digest = _digest_json(targets)
    source_identity = _resolve_plan_source_identity(payload)
    source_digest = str(source_identity["identity_digest"])
    source_payload_digest = _digest_json(source_identity)
    sku_lineage = _resolve_plan_sku_lineage(
        payload,
        plan=plan,
    )
    sku_lineage_digest = _sku_lineage_identity_digest(sku_lineage)
    sku_lineage_payload_digest = _digest_json(sku_lineage)
    registry_digest = _registry_digest(
        _execution_target_labels(targets),
        registry,
    )
    identity = {
        "schema_version": BATCH_PREPARATION_SCHEMA,
        "plan_id": str(plan.get("plan_id") or ""),
        "run_id": str(run.get("run_id") or ""),
        "product_revision": product_revision,
        "payload_digest": payload_digest,
        "confirmation_token_digest": _digest_text(token),
        "targets_digest": targets_digest,
        "source_identity_digest": source_digest,
        "source_identity_payload_digest": source_payload_digest,
        "sku_lineage_digest": sku_lineage_digest,
        "sku_lineage_payload_digest": sku_lineage_payload_digest,
        "adapter_policy_digest": registry_digest,
    }
    if not identity["plan_id"] or not identity["run_id"]:
        raise SystemicIdentityError("plan/run identity is incomplete")
    identity["job_id"] = "oneclick-job:" + _digest_json(identity)[:24]
    return identity


def _require_active_sku_reservation(plan: Mapping[str, Any]) -> None:
    reservation = plan.get("sku_reservation")
    source_reservation = plan.get("source_sku_reservation")
    legacy_exact = bool(
        isinstance(reservation, Mapping)
        and reservation.get("status") == "ACTIVE"
        and reservation.get("plan_id") == plan.get("plan_id")
        and reservation.get("product_id") == plan.get("product_id")
        and reservation.get("seller_sku") == plan.get("seller_sku")
    )
    source_exact = bool(
        isinstance(source_reservation, Mapping)
        and source_reservation.get("status") == "ACTIVE"
        and (source_reservation.get("assignment") or {}).get("seller_sku")
        == plan.get("seller_sku")
    )
    if not legacy_exact and not source_exact:
        raise SystemicIdentityError(
            "predecessor SKU reservation conflicts with the active plan"
        )


def _require_job_identity(
    row: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> None:
    for key in (
        "job_id",
        "plan_id",
        "run_id",
        "product_revision",
        "payload_digest",
        "confirmation_token_digest",
        "targets_digest",
        "source_identity_digest",
        "source_identity_payload_digest",
        "sku_lineage_digest",
        "sku_lineage_payload_digest",
        "adapter_policy_digest",
    ):
        if row[key] != identity[key]:
            raise SystemicIdentityError(f"one-click job {key} drifted")


def _require_dispatch_identity(
    job: Mapping[str, Any],
    target: Mapping[str, Any],
    request: DispatchTargetRequest,
) -> None:
    checks = {
        "schema": request.schema_version == PREPARED_COMMAND_SCHEMA,
        "job_id": job["job_id"] == request.job_id,
        "plan_id": job["plan_id"] == request.plan_id,
        "run_id": job["run_id"] == request.run_id,
        "revision": job["product_revision"] == request.product_revision,
        "payload": job["payload_digest"] == request.payload_digest,
        "token": (
            job["confirmation_token_digest"]
            == request.confirmation_token_digest
        ),
        "targets": job["targets_digest"] == request.targets_digest,
        "source": (
            job["source_identity_digest"] == request.source_identity_digest
        ),
        "source_payload": (
            job["source_identity_payload_digest"]
            == request.source_identity_payload_digest
            == _digest_json(request.source_identity)
        ),
        "sku_lineage": (
            job["sku_lineage_digest"] == request.sku_lineage_digest
        ),
        "sku_lineage_payload": (
            job["sku_lineage_payload_digest"]
            == request.sku_lineage_payload_digest
        ),
        "target": target["target_label"] == request.target_label,
        "policy": (
            target["adapter_policy_digest"] == request.adapter_policy_digest
        ),
        "command": target["command_digest"] == request.prepared_command_digest,
        "proof": target["proof_digest"] == request.proof_digest,
        "command_content": (
            _digest_json(request.command) == request.prepared_command_digest
            and request.command.get("schema_version")
            == PREPARED_COMMAND_SCHEMA
            and request.command.get("target_label")
            == request.target_label
        ),
        "proof_content": (
            _digest_json(request.proof) == request.proof_digest
            and request.proof.get("schema_version")
            == PREPARED_COMMAND_SCHEMA
            and request.proof.get("target_label")
            == request.target_label
        ),
        "shared_resource_context": (
            (
                request.shared_resource_context is None
                and target["shared_resource_context_digest"] is None
                and (
                    target["shared_resource_digest"] is None
                    or target["control_target"] != 1
                )
            )
            or (
                isinstance(request.shared_resource_context, Mapping)
                and _digest_json(request.shared_resource_context)
                == (
                    target["shared_resource_digest"]
                    if target["control_target"] == 1
                    else target["shared_resource_context_digest"]
                )
            )
        ),
    }
    if not all(checks.values()):
        raise SystemicIdentityError(
            "dispatch request identity drifted: "
            + ", ".join(key for key, passed in checks.items() if not passed)
        )


def _target_labels(plan: Mapping[str, Any]) -> list[str]:
    payload = plan.get("payload")
    targets = (
        payload.get("targets") if isinstance(payload, Mapping) else None
    )
    if not isinstance(targets, list) or not targets:
        raise SystemicIdentityError("immutable plan targets are invalid")
    clean = [str(label) for label in targets]
    if len(clean) != len(set(clean)) or any(not label for label in clean):
        raise SystemicIdentityError("immutable plan targets are ambiguous")
    return clean


def _execution_target_labels(targets: list[str]) -> list[str]:
    """Add server-owned controls without changing approved storefront identity."""

    result = list(targets)
    if any(label.startswith("shopee:") for label in result):
        if SHOPEE_GLOBAL_TARGET in result:
            raise SystemicIdentityError(
                "server-owned Shopee GLOBAL cannot be a storefront target"
            )
        result.insert(0, SHOPEE_GLOBAL_TARGET)
    return result


def shopee_shared_resource_owner_key(
    request: PrepareTargetRequest,
    master_lineage_digest: str,
) -> str:
    """Return the server-recomputable owner for one approved global master."""

    if (
        not isinstance(request, PrepareTargetRequest)
        or request.target_label != SHOPEE_GLOBAL_TARGET
        or not _is_digest(master_lineage_digest)
    ):
        raise AdapterContractError(
            "Shopee shared-resource owner inputs are invalid"
        )
    return _digest_json(
        {
            "policy_version": SHOPEE_GLOBAL_MASTER_POLICY,
            "plan_id": request.plan_id,
            "payload_digest": request.payload_digest,
            "source_identity_digest": request.source_identity_digest,
            "sku_lineage_digest": request.sku_lineage_digest,
            "master_lineage_digest": master_lineage_digest,
        }
    )


def _shopee_global_idempotency_key(job: Mapping[str, Any]) -> str:
    return "oneclick-shopee-global:" + _digest_json(
        {
            "policy_version": SHOPEE_GLOBAL_MASTER_POLICY,
            "job_id": job["job_id"],
            "plan_id": job["plan_id"],
            "payload_digest": job["payload_digest"],
            "source_identity_digest": job["source_identity_digest"],
            "sku_lineage_digest": job["sku_lineage_digest"],
        }
    )[:32]


def _run_targets(run: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = run.get("targets")
    if not isinstance(rows, list):
        raise SystemicIdentityError("run targets are unavailable")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise SystemicIdentityError("run target shape is invalid")
        label = str(row.get("target_label") or "")
        if not label or label in result:
            raise SystemicIdentityError("run target identity is ambiguous")
        result[label] = dict(row)
    return result


def _initial_public_status(row: Mapping[str, Any]) -> str:
    status = str(row.get("status") or "")
    if status in {SUCCEEDED, SUBMITTED_UNVERIFIED, RECONCILIATION_REQUIRED}:
        return status
    if status == "RUNNING":
        return RECONCILIATION_REQUIRED
    if status == "FAILED":
        return FAILED_PRE_SUBMIT if _safe_zero_write_failure(row) else (
            RECONCILIATION_REQUIRED
        )
    return PENDING


def _safe_zero_write_failure(row: Mapping[str, Any]) -> bool:
    events = row.get("failure_events")
    if not isinstance(events, list) or not events:
        return False
    evidence = events[-1].get("evidence") if isinstance(events[-1], Mapping) else None
    if not isinstance(evidence, Mapping):
        return False
    writes = evidence.get("external_writes_performed")
    return bool(
        writes == []
        and evidence.get("dispatch_outcome_unknown") is not True
        and evidence.get("submission_accepted") is not True
        and (
            evidence.get("phase") == "PRE_SUBMIT"
            or evidence.get("failure_class") == "FAILED_PRE_SUBMIT"
            or evidence.get("durable_state_uncertain") is False
        )
    )


def _failure_rows_are_safe_zero_write(rows: object) -> bool:
    if not isinstance(rows, (list, tuple)) or not rows:
        return False
    for row in rows:
        try:
            evidence = json.loads(row["evidence_json"])
        except (KeyError, TypeError, ValueError):
            return False
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("external_writes_performed") != []
            or evidence.get("dispatch_outcome_unknown") is True
            or evidence.get("submission_accepted") is True
            or evidence.get("canonical_status") != FAILED_PRE_SUBMIT
        ):
            return False
    return True


def _ozon_inventory_blocker(payload: Mapping[str, Any]) -> str | None:
    decisions = payload.get("approved_inventory_decisions")
    decision = (
        decisions.get("ozon:RU")
        if isinstance(decisions, Mapping)
        else None
    )
    if not isinstance(decision, Mapping):
        return "ozon_inventory_decision_missing"
    if decision.get("schema_version") != "approved-sellable-inventory-decision/v1":
        return "ozon_inventory_decision_schema_invalid"
    status = decision.get("autopilot_status", decision.get("status"))
    quantity = decision.get("quantity", decision.get("desired_quantity"))
    if status != "READY":
        return "ozon_inventory_decision_not_ready"
    if type(quantity) is not int or quantity <= 0:
        return "ozon_inventory_quantity_invalid"
    return None


def _resolve_plan_source_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Re-run the 01 resolver and verify the stored immutable identity."""

    from domains.product_operations import resolve_source_product_identity

    stored = payload.get("source_product_identity")
    if not isinstance(stored, Mapping):
        raise SystemicIdentityError("BLOCKED_SOURCE_IDENTITY: identity is missing")
    if stored.get("schema_version") != "source-product-identity/v1":
        raise SystemicIdentityError(
            "BLOCKED_SOURCE_IDENTITY: identity schema is invalid"
        )
    source_offer_id = stored.get("source_offer_id")
    source_item_code = stored.get("source_item_code")
    authority = stored.get("source_authority")
    provenance = stored.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        raise SystemicIdentityError(
            "BLOCKED_SOURCE_IDENTITY: provenance is missing"
        )
    collect_box: dict[str, Any] = {}
    precollect: dict[str, Any] = {}
    source_record: dict[str, Any] = {}
    records: dict[int, dict[str, Any]] = {}
    for row in provenance:
        if not isinstance(row, Mapping):
            raise SystemicIdentityError(
                "BLOCKED_SOURCE_IDENTITY: provenance shape is invalid"
            )
        path = str(row.get("path") or "")
        value = row.get("source_offer_id")
        if path == "collect_box.source_item_id":
            collect_box["source_item_id"] = value
        elif path == "precollect.source_id":
            precollect["source_id"] = value
        elif path == "source_record.source_id":
            source_record["source_id"] = value
        elif path.startswith("precollect.records[") and path.endswith(
            "].source_id"
        ):
            index_text = path[len("precollect.records[") : -len("].source_id")]
            if not index_text.isdigit():
                raise SystemicIdentityError(
                    "BLOCKED_SOURCE_IDENTITY: provenance index is invalid"
                )
            records[int(index_text)] = {"source_id": value}
        else:
            raise SystemicIdentityError(
                "BLOCKED_SOURCE_IDENTITY: provenance path is unsupported"
            )
    if records:
        highest = max(records)
        if set(records) != set(range(highest + 1)):
            raise SystemicIdentityError(
                "BLOCKED_SOURCE_IDENTITY: provenance indices are not consecutive"
            )
        precollect["records"] = [records[index] for index in range(highest + 1)]
    if source_item_code is not None:
        # Display-only.  The resolver never uses it as lookup authority.
        collect_box["source_item_code"] = source_item_code
    resolution = resolve_source_product_identity(
        collect_box=collect_box or None,
        precollect=precollect or None,
        source_record=source_record or None,
        source_authority=authority,
    )
    if not resolution.ready or resolution.identity is None:
        raise SystemicIdentityError(
            "BLOCKED_SOURCE_IDENTITY: "
            + "; ".join(resolution.blockers)
        )
    resolved = resolution.identity.payload()
    resolved["source_item_code"] = source_item_code
    if (
        resolved.get("source_offer_id") != source_offer_id
        or resolved.get("identity_digest") != stored.get("identity_digest")
        or resolved != dict(stored)
    ):
        raise SystemicIdentityError(
            "BLOCKED_SOURCE_IDENTITY: stored identity failed TOCTOU validation"
        )
    return resolved


def _source_identity_digest(payload: Mapping[str, Any]) -> str:
    return str(_resolve_plan_source_identity(payload)["identity_digest"])


def _resolve_plan_sku_lineage(
    payload: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    lineage = payload.get("sku_lineage")
    if not isinstance(lineage, Mapping):
        raise SystemicIdentityError(
            "BLOCKED_SKU_LINEAGE: lineage contract is missing"
        )
    if (
        lineage.get("schema_version") != "sku-lineage-reservation/v1"
        or lineage.get("status") != "READY"
        or lineage.get("ready") is not True
        or lineage.get("source_identity_digest")
        != _source_identity_digest(payload)
    ):
        raise SystemicIdentityError(
            "BLOCKED_SKU_LINEAGE: lineage contract is invalid"
        )
    mode = lineage.get("lineage_mode")
    if mode == "INHERITED_PREDECESSOR":
        assignment = lineage.get("assignment")
        reservation = lineage.get("reservation")
        if (
            not isinstance(assignment, Mapping)
            or assignment.get("seller_sku") != plan.get("seller_sku")
            or not isinstance(assignment.get("model_skus"), list)
            or not assignment["model_skus"]
            or not isinstance(reservation, Mapping)
            or reservation.get("reservation_digest")
            != lineage.get("reservation", {}).get("reservation_digest")
            or reservation.get("source_identity_digest")
            != lineage.get("source_identity_digest")
        ):
            raise SystemicIdentityError(
                "BLOCKED_SKU_LINEAGE: inherited assignment drifted"
            )
    elif mode == "NEW_SOURCE":
        assignment = lineage.get("assignment")
        reservation = lineage.get("reservation")
        if (
            not isinstance(assignment, Mapping)
            or assignment.get("seller_sku") != plan.get("seller_sku")
            or not isinstance(assignment.get("model_skus"), list)
            or not assignment["model_skus"]
            or not isinstance(reservation, Mapping)
            or reservation.get("source_identity_digest")
            != lineage.get("source_identity_digest")
        ):
            raise SystemicIdentityError(
                "BLOCKED_SKU_LINEAGE: new-source reservation is incomplete"
            )
    else:
        raise SystemicIdentityError(
            "BLOCKED_SKU_LINEAGE: lineage mode is invalid"
        )
    reservation = plan.get("sku_reservation")
    source_reservation = plan.get("source_sku_reservation")
    if (
        (
            not isinstance(reservation, Mapping)
            or reservation.get("status") != "ACTIVE"
            or reservation.get("seller_sku") != plan.get("seller_sku")
        )
        and (
            not isinstance(source_reservation, Mapping)
            or source_reservation.get("status") != "ACTIVE"
            or (source_reservation.get("assignment") or {}).get(
                "seller_sku"
            )
            != plan.get("seller_sku")
        )
    ):
        raise SystemicIdentityError(
            "BLOCKED_SKU_LINEAGE: active Seller SKU reservation is missing"
        )
    return dict(lineage)


def _sku_lineage_identity_digest(lineage: Mapping[str, Any]) -> str:
    reservation = lineage.get("reservation")
    return _digest_json(
        {
            "schema_version": lineage.get("schema_version"),
            "source_identity_digest": lineage.get(
                "source_identity_digest"
            ),
            "lineage_mode": lineage.get("lineage_mode"),
            "predecessor_id": lineage.get("predecessor_id"),
            "predecessor_revision": lineage.get("predecessor_revision"),
            "predecessor_digest": lineage.get("predecessor_digest"),
            "assignment": lineage.get("assignment"),
            "reservation_digest": (
                reservation.get("reservation_digest")
                if isinstance(reservation, Mapping)
                else None
            ),
        }
    )


def _registry_digest(
    targets: list[str],
    registry: Mapping[str, AdapterRegistration],
) -> str:
    return _digest_json(
        {
            "schema_version": REGISTRY_SCHEMA,
            "targets": [
                {
                    "target_label": label,
                    "adapter_name": _adapter_name_for_target(label),
                    "policy_digest": _policy_digest_for_target(label, registry),
                }
                for label in targets
            ],
        }
    )


def _policy_digest_for_target(
    label: str,
    registry: Mapping[str, AdapterRegistration],
) -> str:
    registration = registry.get(_adapter_name_for_target(label))
    if (
        registration
        and label in registration.target_labels
        and _is_digest(registration.policy_digest)
    ):
        return registration.policy_digest
    return _digest_json(
        {
            "schema_version": REGISTRY_SCHEMA,
            "target_label": label,
            "status": "UNAVAILABLE",
        }
    )


def _adapter_name_for_target(label: str) -> str:
    channel = str(label).split(":", 1)[0]
    return {
        "miaoshou": "new_product_workbench_miaoshou_commit",
        "tiktok": "miaoshou_tiktok_publish",
        "shopee": "shopee_cnsc_publish",
        "ozon": "ozon_product_publish",
    }.get(channel, f"unsupported:{channel}")


def _classification_for_category(category: str) -> str:
    return {
        "AUTH": BLOCKED_AUTH,
        "INVENTORY": BLOCKED_INVENTORY,
        "SYSTEMIC_IDENTITY": BLOCKED_SOURCE_IDENTITY,
        "SAFE_ACTION": SAFE_ACTION_REQUIRED,
    }.get(category, BLOCKED_CAPABILITY)


def _status_for_classification(classification: str) -> str:
    return {
        BLOCKED_AUTH: BLOCKED_AUTH,
        BLOCKED_INVENTORY: BLOCKED_INVENTORY,
        BLOCKED_CAPABILITY: BLOCKED_CAPABILITY,
        BLOCKED_SOURCE_IDENTITY: BLOCKED_SOURCE_IDENTITY,
        BLOCKED_SKU_LINEAGE: BLOCKED_SKU_LINEAGE,
        SAFE_ACTION_REQUIRED: FAILED_PRE_SUBMIT,
    }.get(classification, READY)


def _validated_shared_resource_declaration(
    request: PrepareTargetRequest,
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if request.target_label != SHOPEE_GLOBAL_TARGET:
        if value is not None:
            raise AdapterContractError(
                "only the server-owned Shopee GLOBAL target may declare a shared resource"
            )
        return None
    if not isinstance(value, Mapping):
        raise AdapterContractError(
            "Shopee GLOBAL preparation requires shared_resource"
        )
    declaration = dict(value)
    mode = declaration.get("mode")
    common_keys = {
        "schema_version",
        "policy_version",
        "mode",
        "owner_key",
        "master_lineage_digest",
        "approved_selected_image_count",
        "expected_external_write_count",
    }
    expected_keys = (
        common_keys
        if mode == "ENSURE_NEW"
        else common_keys
        | {"global_identity_digest", "master_evidence_digest"}
    )
    if (
        mode not in {"ENSURE_NEW", "EXISTING_GLOBAL"}
        or set(declaration) != expected_keys
        or declaration.get("schema_version") != SHARED_RESOURCE_SCHEMA
        or declaration.get("policy_version")
        != SHOPEE_GLOBAL_MASTER_POLICY
        or not _is_digest(declaration.get("master_lineage_digest"))
        or not _is_digest(declaration.get("owner_key"))
        or declaration["owner_key"]
        != shopee_shared_resource_owner_key(
            request,
            declaration["master_lineage_digest"],
        )
        or declaration.get("approved_selected_image_count")
        != _approved_selected_image_count(request.immutable_plan_payload)
        or declaration.get("expected_external_write_count")
        != (
            0
            if mode == "EXISTING_GLOBAL"
            else declaration["approved_selected_image_count"] + 2
        )
    ):
        raise AdapterContractError(
            "Shopee shared-resource declaration is invalid"
        )
    if mode == "EXISTING_GLOBAL" and (
        not _is_digest(declaration.get("global_identity_digest"))
        or not _is_digest(declaration.get("master_evidence_digest"))
    ):
        raise AdapterContractError(
            "existing Shopee global proof digests are invalid"
        )
    return declaration


def _approved_selected_image_count(
    payload: Mapping[str, Any],
) -> int:
    approved_global = payload.get("approved_shopee_global_plan")
    if (
        not isinstance(approved_global, Mapping)
        or set(approved_global)
        != {"schema_version", "selected_image_positions"}
        or approved_global.get("schema_version")
        != "approved-shopee-global-plan/v1"
    ):
        raise AdapterContractError(
            "immutable plan lacks an approved Shopee global image selection"
        )
    selected = approved_global.get("selected_image_positions")
    if (
        not isinstance(selected, list)
        or not selected
        or len(selected) > 9
        or any(type(value) is not int or value < 1 for value in selected)
        or len(set(selected)) != len(selected)
    ):
        raise AdapterContractError(
            "approved Shopee global image selection is invalid"
        )
    images = payload.get("images")
    if not isinstance(images, list) or not images:
        raise AdapterContractError(
            "immutable plan requires approved ordered images"
        )
    for position, row in enumerate(images, start=1):
        if (
            not isinstance(row, Mapping)
            or type(row.get("position")) is not int
            or row.get("position") != position
            or type(row.get("image_url")) is not str
            or not row["image_url"].strip()
        ):
            raise AdapterContractError(
                "immutable approved image sequence is invalid"
            )
    positions = {row["position"] for row in images}
    if any(position not in positions for position in selected):
        raise AdapterContractError(
            "approved Shopee image selection references an unknown image"
        )
    return len(selected)


def _verified_shared_resource_context(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    context = {
        "schema_version": SHARED_RESOURCE_SCHEMA,
        "policy_version": SHOPEE_GLOBAL_MASTER_POLICY,
        "owner_key": value.get("owner_key"),
        "master_lineage_digest": value.get("master_lineage_digest"),
        "global_identity_digest": value.get("global_identity_digest"),
        "master_evidence_digest": value.get("master_evidence_digest"),
    }
    if (
        context["schema_version"] != SHARED_RESOURCE_SCHEMA
        or context["policy_version"] != SHOPEE_GLOBAL_MASTER_POLICY
        or any(
            not _is_digest(context[key])
            for key in (
                "owner_key",
                "master_lineage_digest",
                "global_identity_digest",
                "master_evidence_digest",
            )
        )
    ):
        raise AdapterContractError(
            "verified Shopee shared-resource context is invalid"
        )
    return context


def _validated_shared_resource_result(
    evidence: Mapping[str, Any] | None,
    declaration: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "shared_resource"
    }:
        raise AdapterContractError(
            "Shopee GLOBAL success requires only redacted shared-resource evidence"
        )
    shared = evidence.get("shared_resource")
    if not isinstance(shared, Mapping) or set(shared) != {
        "schema_version",
        "policy_version",
        "mode",
        "owner_key",
        "master_lineage_digest",
        "global_identity_digest",
        "master_evidence_digest",
    }:
        raise AdapterContractError(
            "Shopee GLOBAL success evidence shape is invalid"
        )
    if (
        shared.get("mode") != "ENSURE_NEW"
        or shared.get("owner_key") != declaration.get("owner_key")
        or shared.get("master_lineage_digest")
        != declaration.get("master_lineage_digest")
    ):
        raise AdapterContractError(
            "Shopee GLOBAL success evidence does not match preparation"
        )
    return _verified_shared_resource_context(shared)


def _stored_shared_resource(
    row: Mapping[str, Any],
    *,
    context: bool,
) -> dict[str, Any] | None:
    json_key = (
        "shared_resource_context_json"
        if context
        else "shared_resource_json"
    )
    digest_key = (
        "shared_resource_context_digest"
        if context
        else "shared_resource_digest"
    )
    encoded = row[json_key]
    digest = row[digest_key]
    if encoded is None and digest is None:
        return None
    if type(encoded) is not str or not _is_digest(digest):
        raise SystemicIdentityError(
            "stored Shopee shared-resource identity is incomplete"
        )
    try:
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise SystemicIdentityError(
            "stored Shopee shared-resource JSON is invalid"
        ) from error
    if not isinstance(decoded, Mapping) or _digest_json(decoded) != digest:
        raise SystemicIdentityError(
            "stored Shopee shared-resource digest drifted"
        )
    return dict(decoded)


def _validate_job_shared_resource_rows(rows: object) -> None:
    materialized = list(rows)
    globals_ = [
        row for row in materialized
        if row["target_label"] == SHOPEE_GLOBAL_TARGET
    ]
    regions = [
        row
        for row in materialized
        if str(row["target_label"]).startswith("shopee:")
        and row["target_label"] != SHOPEE_GLOBAL_TARGET
    ]
    if regions and len(globals_) != 1:
        raise SystemicIdentityError(
            "Shopee regions require exactly one server-owned GLOBAL target"
        )
    if not regions and globals_:
        raise SystemicIdentityError(
            "Shopee GLOBAL exists without approved regional targets"
        )
    for row in materialized:
        is_global = row["target_label"] == SHOPEE_GLOBAL_TARGET
        if bool(row["control_target"]) is not is_global:
            raise SystemicIdentityError(
                "one-click control-target marker drifted"
            )
        if is_global and bool(row["storefront"]):
            raise SystemicIdentityError(
                "Shopee GLOBAL must not be counted as a storefront"
            )
        if not is_global and _stored_shared_resource(row, context=False):
            raise SystemicIdentityError(
                "storefront target cannot own a shared resource"
            )
    if not globals_:
        return
    global_row = globals_[0]
    declaration = _stored_shared_resource(global_row, context=False)
    global_context = _stored_shared_resource(global_row, context=True)
    if global_row["status"] in {READY, DISPATCHING, SUCCEEDED}:
        if not declaration:
            raise SystemicIdentityError(
                "prepared Shopee GLOBAL declaration is unavailable"
            )
        if (
            declaration.get("schema_version") != SHARED_RESOURCE_SCHEMA
            or declaration.get("policy_version")
            != SHOPEE_GLOBAL_MASTER_POLICY
            or declaration.get("mode")
            not in {"ENSURE_NEW", "EXISTING_GLOBAL"}
            or not _is_digest(declaration.get("owner_key"))
            or not _is_digest(declaration.get("master_lineage_digest"))
        ):
            raise SystemicIdentityError(
                "stored Shopee GLOBAL declaration is invalid"
            )
    if global_row["status"] == SUCCEEDED:
        if not global_context:
            raise SystemicIdentityError(
                "verified Shopee GLOBAL context is unavailable"
            )
        verified = _verified_shared_resource_context(global_context)
        if (
            declaration is None
            or verified["owner_key"] != declaration["owner_key"]
            or verified["master_lineage_digest"]
            != declaration["master_lineage_digest"]
        ):
            raise SystemicIdentityError(
                "verified Shopee GLOBAL context drifted from preparation"
            )
        expected_digest = _digest_json(verified)
        for region in regions:
            context = _stored_shared_resource(region, context=True)
            if context != verified or region[
                "shared_resource_context_digest"
            ] != expected_digest:
                raise SystemicIdentityError(
                    "Shopee region shared-resource context drifted"
                )
    else:
        if global_context is not None or any(
            _stored_shared_resource(region, context=True) is not None
            for region in regions
        ):
            raise SystemicIdentityError(
                "unverified Shopee GLOBAL cannot authorize regional dispatch"
            )


def _prepared_blocked_row(
    label: str,
    classification: str,
    status: str,
    category: str,
    code: str,
    detail: str,
    *,
    scope: str = TARGET_REASON_SCOPE,
) -> dict[str, Any]:
    return {
        "target_label": label,
        "classification": classification,
        "status": status,
        "reason_category": _reason_category(category),
        "reason_scope": _reason_scope(scope),
        "reason_code": _clean_code(code, "target_blocked"),
        "reason_detail": _clean_detail(detail),
        "manual_after_submit": False,
    }


def _prepared_terminal_row(label: str, status: str) -> dict[str, Any]:
    return {
        "target_label": label,
        "classification": (
            READY_SUBMIT_MANUAL
            if status in {
                SUCCEEDED_MANUAL_REVIEW,
                SUBMITTED_UNVERIFIED,
            }
            else EXACT_READY_AUTOMATIC
        ),
        "status": status,
        "reason_category": "CAPABILITY",
        "reason_scope": TARGET_REASON_SCOPE,
        "reason_code": "already_terminal",
        "reason_detail": "target already has a durable terminal receipt",
        "manual_after_submit": status
        in {SUCCEEDED_MANUAL_REVIEW, SUBMITTED_UNVERIFIED},
    }


def _validated_write_classes(values: object) -> tuple[str, ...]:
    if type(values) not in (list, tuple):
        raise AdapterContractError("external write classes are invalid")
    result = tuple(values)
    if (
        any(
            type(value) is not str
            or not value
            or value != value.strip()
            for value in result
        )
        or len(result) != len(set(result))
        or any(
            len(value) > 80
            or any(
                character
                not in (
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "abcdefghijklmnopqrstuvwxyz"
                    "0123456789_.:-"
                )
                for character in value
            )
            for value in result
        )
    ):
        raise AdapterContractError("external write classes are invalid")
    return result


def _validated_write_count_bounds(
    writes: tuple[str, ...],
    exact_count: object,
    confirmed_lower_bound: object,
    possible_upper_bound: object,
    *,
    infer_legacy_exact: bool = False,
) -> tuple[int | None, int, int | None]:
    classes = _validated_write_classes(writes)
    if exact_count is None and infer_legacy_exact and (
        _UNKNOWN_WRITE_CLASS not in classes
    ):
        exact_count = len(classes)
    if exact_count is not None and (
        type(exact_count) is not int or exact_count < 0
    ):
        raise AdapterContractError(
            "external_write_count must be a non-negative built-in int or null"
        )
    if (
        type(confirmed_lower_bound) is not int
        or confirmed_lower_bound < 0
    ):
        raise AdapterContractError(
            "confirmed write lower bound must be a non-negative built-in int"
        )
    if possible_upper_bound is not None and (
        type(possible_upper_bound) is not int
        or possible_upper_bound < 0
    ):
        raise AdapterContractError(
            "possible write upper bound must be a non-negative built-in int or null"
        )
    unique_known = len(
        [item for item in classes if item != _UNKNOWN_WRITE_CLASS]
    )
    if exact_count is not None:
        if _UNKNOWN_WRITE_CLASS in classes:
            raise AdapterContractError(
                "unknown write class cannot carry an exact write count"
            )
        if exact_count < unique_known:
            raise AdapterContractError(
                "exact write count is below its unique write classes"
            )
        if confirmed_lower_bound not in {0, exact_count}:
            raise AdapterContractError(
                "exact write count must equal its confirmed lower bound"
            )
        confirmed_lower_bound = exact_count
        if possible_upper_bound not in {None, exact_count}:
            raise AdapterContractError(
                "exact write count must equal its possible upper bound"
            )
        possible_upper_bound = exact_count
    else:
        if (
            possible_upper_bound is not None
            and possible_upper_bound < confirmed_lower_bound
        ):
            raise AdapterContractError(
                "possible write upper bound is below the confirmed lower bound"
            )
    return exact_count, confirmed_lower_bound, possible_upper_bound


def _result_write_count_bounds(
    result: DispatchTargetResult,
    *,
    infer_legacy_exact: bool = True,
) -> tuple[int | None, int, int | None]:
    return _validated_write_count_bounds(
        result.external_writes,
        result.external_write_count,
        result.confirmed_external_write_count_lower_bound,
        result.possible_external_write_count_upper_bound,
        infer_legacy_exact=(
            infer_legacy_exact
            and result.dispatch_outcome_unknown is not True
        ),
    )


def _stored_write_classes(row: Mapping[str, Any]) -> tuple[str, ...]:
    if not hasattr(row, "get"):
        row = dict(row)
    encoded = row.get("cumulative_external_writes_json")
    if encoded in (None, ""):
        return ()
    if type(encoded) is not str:
        raise SystemicIdentityError(
            "durable dispatch write ledger is invalid"
        )
    try:
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise SystemicIdentityError(
            "durable dispatch write ledger is invalid"
        ) from error
    result = _validated_write_classes(decoded)
    stored_digest = row.get("cumulative_external_writes_digest")
    if stored_digest and stored_digest != _digest_json(list(result)):
        raise SystemicIdentityError(
            "durable dispatch write ledger digest drifted"
        )
    return result


def _stored_write_count_bounds(
    row: Mapping[str, Any],
) -> tuple[int | None, int, int | None]:
    if not hasattr(row, "get"):
        row = dict(row)
    classes = _stored_write_classes(row)
    try:
        return _validated_write_count_bounds(
            classes,
            row.get("cumulative_external_write_count"),
            row.get("cumulative_external_write_lower_bound", 0),
            row.get("cumulative_external_write_upper_bound"),
        )
    except AdapterContractError as error:
        raise SystemicIdentityError(
            "durable dispatch write-count ledger is invalid"
        ) from error


def _stored_pending_write_intent(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not hasattr(row, "get"):
        row = dict(row)
    encoded = row.get("pending_write_intent_json")
    digest = row.get("pending_write_intent_digest")
    if encoded in (None, "") and digest in (None, ""):
        return None
    if type(encoded) is not str or not _is_digest(digest):
        raise SystemicIdentityError(
            "durable pending write intent is invalid"
        )
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise SystemicIdentityError(
            "durable pending write intent is invalid"
        ) from error
    if (
        not isinstance(value, dict)
        or digest != _digest_json(value)
        or set(value)
        != {
            "stage",
            "prior_classes",
            "intended_classes",
            "confirmed_lower_bound",
            "possible_upper_bound",
        }
        or type(value.get("stage")) is not str
        or _validated_write_classes(value.get("prior_classes"))
        != tuple(value["prior_classes"])
        or _validated_write_classes(value.get("intended_classes"))
        != tuple(value["intended_classes"])
        or type(value.get("confirmed_lower_bound")) is not int
        or type(value.get("possible_upper_bound")) is not int
        or value["possible_upper_bound"]
        != value["confirmed_lower_bound"] + 1
    ):
        raise SystemicIdentityError(
            "durable pending write intent is invalid"
        )
    return value


def _merge_write_classes(
    existing: tuple[str, ...],
    additions: tuple[str, ...],
) -> tuple[str, ...]:
    merged = list(_validated_write_classes(existing))
    for value in _validated_write_classes(additions):
        if value not in merged:
            merged.append(value)
    return tuple(merged)


def _manual_review_metadata(
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise AdapterContractError(
            "manual-review success requires redacted evidence"
        )
    if evidence.get("manual_review") is not True:
        raise AdapterContractError(
            "manual-review success requires manual_review=true"
        )
    _reject_sensitive_manual_review_evidence(evidence)
    raw_rules = evidence.get("rule_ids")
    if type(raw_rules) not in (list, tuple) or not raw_rules:
        raise AdapterContractError(
            "manual-review success requires non-empty rule_ids"
        )
    rules = tuple(raw_rules)
    if (
        any(
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > 120
            or any(
                character
                not in (
                    "abcdefghijklmnopqrstuvwxyz"
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "0123456789_.:-"
                )
                for character in value
            )
            for value in rules
        )
        or len(rules) != len(set(rules))
    ):
        raise AdapterContractError("manual-review rule_ids are invalid")

    observation_digests: list[str] = []
    explicit = evidence.get("observation_digests")
    if explicit is not None:
        if not isinstance(explicit, Mapping) or not explicit:
            raise AdapterContractError(
                "manual-review observation_digests are invalid"
            )
        for key, value in explicit.items():
            if (
                type(key) is not str
                or not key
                or key != key.strip()
                or not _is_digest(value)
            ):
                raise AdapterContractError(
                    "manual-review observation_digests are invalid"
                )
            observation_digests.append(value)
    for key, value in evidence.items():
        if (
            type(key) is str
            and key.endswith("_digest")
            and (
                "observation" in key
                or key.endswith("_outcome_digest")
            )
        ):
            if not _is_digest(value):
                raise AdapterContractError(
                    "manual-review observation digest is invalid"
                )
            observation_digests.append(value)
    if not observation_digests:
        raise AdapterContractError(
            "manual-review success requires observation evidence digest"
        )
    return {
        "manual_review": True,
        "rule_ids": sorted(rules),
        "observation_digests": sorted(set(observation_digests)),
    }


def _validate_marketplace_manual_acceptance_evidence(
    evidence: Mapping[str, Any],
) -> None:
    marketplace_product_id = evidence.get("marketplace_product_id")
    if (
        type(marketplace_product_id) is not str
        or not marketplace_product_id
        or marketplace_product_id != marketplace_product_id.strip()
        or len(marketplace_product_id) > 128
        or any(character.isspace() for character in marketplace_product_id)
    ):
        raise AdapterContractError(
            "manual acceptance requires marketplace product identity"
        )
    for check in (
        "identity_matches",
        "seller_sku_matches",
        "single_listing_for_sku",
        "title_matches",
        "price_matches",
        "images_match",
        "logistics_match",
    ):
        if evidence.get(check) is not True:
            raise AdapterContractError(
                "manual acceptance requires all listing checks"
            )


def _validate_observation_manual_acceptance_evidence(
    evidence: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    expected_keys = {
        "source",
        "manual_review_accepted",
        "observation_evidence_digest",
        "job_identity_digest",
        "result_evidence_digest",
        "readback_evidence_digest",
        "outcome_receipt_digest",
        "observation_evidence_digests",
    }
    if (
        set(evidence) != expected_keys
        or evidence.get("source")
        != "kyle_verified_shopee_observation_review"
        or evidence.get("manual_review_accepted") is not True
    ):
        raise AdapterContractError(
            "observation acceptance requires the exact receipt-bound review"
        )
    digests = evidence.get("observation_evidence_digests")
    expected = result.get("observation_digests")
    if (
        type(digests) is not list
        or not digests
        or any(not _is_digest(value) for value in digests)
        or type(expected) is not list
        or digests != expected
        or evidence.get("observation_evidence_digest")
        != sorted(expected)[0]
        or evidence.get("result_evidence_digest")
        != result.get("evidence_digest")
        or evidence.get("readback_evidence_digest")
        != result.get("evidence_digest")
        or not _is_digest(evidence.get("job_identity_digest"))
        or not _is_digest(evidence.get("outcome_receipt_digest"))
    ):
        raise AdapterContractError(
            "observation acceptance evidence does not match readback"
        )


def _reject_sensitive_manual_review_evidence(
    value: object,
    path: str = "evidence",
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if type(key) is not str:
                raise AdapterContractError(
                    "manual-review evidence keys must be built-in str"
                )
            lowered = key.strip().casefold()
            if (
                lowered
                in {
                    "authorization",
                    "cookie",
                    "copy",
                    "description",
                    "external_id",
                    "image_id",
                    "image_ids",
                    "image_url",
                    "image_urls",
                    "item_id",
                    "model_id",
                    "password",
                    "raw_copy",
                    "raw_response",
                    "response",
                    "response_body",
                    "secret",
                    "seller_sku",
                    "title",
                    "token",
                    "url",
                    "urls",
                }
                or lowered.endswith("_token")
                or lowered.endswith("_url")
            ):
                raise AdapterContractError(
                    f"manual-review evidence contains sensitive field at {path}"
                )
            _reject_sensitive_manual_review_evidence(
                child,
                f"{path}.{key}",
            )
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_sensitive_manual_review_evidence(
                child,
                f"{path}[{index}]",
            )
    elif isinstance(value, str):
        lowered = value.casefold()
        if (
            "://" in value
            or "bearer " in lowered
            or lowered.startswith("sk-")
        ):
            raise AdapterContractError(
                f"manual-review evidence contains sensitive value at {path}"
            )


def _canonical_evidence(
    request: DispatchTargetRequest,
    result: DispatchTargetResult,
) -> dict[str, Any]:
    manual_review = (
        _manual_review_metadata(result.evidence)
        if result.canonical_status == SUCCEEDED_MANUAL_REVIEW
        else {
            "manual_review": False,
            "rule_ids": [],
            "observation_digests": [],
        }
    )
    write_count, write_lower, write_upper = _result_write_count_bounds(result)
    evidence = {
        "schema_version": PREPARED_COMMAND_SCHEMA,
        "target_label": request.target_label,
        "prepared_command_digest": request.prepared_command_digest,
        "proof_digest": request.proof_digest,
        "source_identity_digest": request.source_identity_digest,
        "source_identity_payload_digest": (
            request.source_identity_payload_digest
        ),
        "sku_lineage_digest": request.sku_lineage_digest,
        "sku_lineage_payload_digest": request.sku_lineage_payload_digest,
        "adapter_policy_digest": request.adapter_policy_digest,
        "shared_resource_context_digest": (
            _digest_json(request.shared_resource_context)
            if isinstance(request.shared_resource_context, Mapping)
            else None
        ),
        "canonical_status": result.canonical_status,
        "reason_category": result.reason_category,
        "reason_scope": result.reason_scope,
        "reason_code": result.reason_code,
        "external_writes_performed": list(result.external_writes),
        "external_write_count": write_count,
        "confirmed_external_write_count_lower_bound": write_lower,
        "possible_external_write_count_upper_bound": write_upper,
        "submission_accepted": result.submission_accepted,
        "readback_verified": result.readback_verified,
        "dispatch_outcome_unknown": result.dispatch_outcome_unknown,
        "evidence_digest": _digest_json(dict(result.evidence or {})),
        **manual_review,
    }
    return evidence


def _public_result(
    result: DispatchTargetResult,
    evidence_digest: str,
) -> dict[str, Any]:
    count, lower_bound, upper_bound = _result_write_count_bounds(result)
    manual_review = (
        _manual_review_metadata(result.evidence)
        if result.canonical_status == SUCCEEDED_MANUAL_REVIEW
        else {
            "manual_review": False,
            "rule_ids": [],
            "observation_digests": [],
        }
    )
    return {
        "canonical_status": result.canonical_status,
        "reason_category": result.reason_category,
        "reason_scope": result.reason_scope,
        "reason_code": result.reason_code,
        "external_write_count": count,
        "external_write_classes": list(result.external_writes),
        "cumulative_external_write_count": count,
        "cumulative_external_write_classes": list(result.external_writes),
        "confirmed_external_write_count_lower_bound": lower_bound,
        "possible_external_write_count_upper_bound": upper_bound,
        "submission_accepted": result.submission_accepted,
        "readback_verified": result.readback_verified,
        "dispatch_outcome_unknown": result.dispatch_outcome_unknown,
        "evidence_digest": evidence_digest,
        **manual_review,
    }


def _release_outcome_receipt(
    *,
    job: Mapping[str, Any],
    target: Mapping[str, Any],
    result: DispatchTargetResult,
    evidence_digest: str,
) -> dict[str, Any]:
    label_parts = target["target_label"].split(":", 1)
    channel = label_parts[0].casefold()
    region = label_parts[1].upper() if len(label_parts) == 2 else "UNKNOWN"
    write_count, write_lower, _write_upper = _result_write_count_bounds(result)
    outcome_class = {
        SUCCEEDED: "SUCCESS",
        SUCCEEDED_MANUAL_REVIEW: "SUCCESS",
        SUBMITTED_UNVERIFIED: "SUBMITTED_UNVERIFIED",
        FAILED_PRE_SUBMIT: "FAILURE",
        RECONCILIATION_REQUIRED: "RECONCILIATION_REQUIRED",
        BLOCKED_AUTH: "FAILURE",
        BLOCKED_INVENTORY: "FAILURE",
        BLOCKED_CAPABILITY: "FAILURE",
    }[result.canonical_status]
    if result.canonical_status in {
        SUCCEEDED,
        SUCCEEDED_MANUAL_REVIEW,
    }:
        dispatch_boundary = (
            "ACCEPTED" if result.external_writes else "NOT_REACHED"
        )
        readback_status = "VERIFIED"
    elif result.canonical_status == FAILED_PRE_SUBMIT:
        dispatch_boundary = "PRE_SUBMIT"
        readback_status = "UNAVAILABLE"
    elif result.canonical_status == SUBMITTED_UNVERIFIED:
        dispatch_boundary = "ACCEPTED"
        readback_status = "UNAVAILABLE"
    else:
        dispatch_boundary = (
            "UNKNOWN"
            if result.dispatch_outcome_unknown
            else "SUBMITTED"
        )
        readback_status = "FAILED"
    manual_status = (
        "PENDING"
        if result.canonical_status
        in {SUCCEEDED_MANUAL_REVIEW, SUBMITTED_UNVERIFIED}
        else "NOT_REQUIRED"
    )
    reconciliation_status = (
        "REQUIRED"
        if result.canonical_status == RECONCILIATION_REQUIRED
        else "NOT_REQUIRED"
    )
    error_category = (
        "NONE"
        if result.canonical_status
        in {SUCCEEDED, SUCCEEDED_MANUAL_REVIEW}
        else (
            result.reason_category
            if result.reason_category
            in {"AUTH", "INVENTORY", "CONTENT", "LOGISTICS"}
            else "OTHER"
        )
    )
    return {
        "schema_version": "release-outcome-receipt/v1",
        "identity": {
            "plan_digest": _digest_json(
                {
                    "plan_id": job["plan_id"],
                    "payload_digest": job["payload_digest"],
                }
            ),
            "run_digest": _digest_json(
                {
                    "run_id": job["run_id"],
                    "targets_digest": job["targets_digest"],
                }
            ),
            "target_digest": _digest_json(
                {
                    "target_label": target["target_label"],
                    "attempt": target["dispatch_count"],
                    "prepared_command_digest": target["command_digest"],
                    "proof_digest": target["proof_digest"],
                }
            ),
        },
        "channel": channel,
        "region": region,
        "versions": {
            "adapter": "oneclick-dynamic-adapter-v1",
            "policy": "release-target-prepared-command-v1",
        },
        "outcome": {"class": outcome_class},
        "dispatch": {
            "boundary": dispatch_boundary,
            "external_write_count": write_count,
            "external_write_classes": list(result.external_writes),
            "confirmed_external_write_count_lower_bound": write_lower,
        },
        "readback": {"status": readback_status},
        "manual": {
            "status": manual_status,
            "evidence_digest": (
                evidence_digest
                if result.canonical_status == SUCCEEDED_MANUAL_REVIEW
                else None
            ),
        },
        "reconciliation": {"status": reconciliation_status},
        "error": {
            "category": error_category,
            "code": (
                None
                if result.canonical_status
                in {SUCCEEDED, SUCCEEDED_MANUAL_REVIEW}
                else (
                    result.reason_code
                    if len(result.reason_code) <= 80
                    else "release_target_terminal"
                )
            ),
            "type": None,
        },
        "latency_ms": None,
        "counts": {
            "attempts": target["dispatch_count"],
            "dispatches": target["dispatch_count"],
            "readbacks": (
                1 if result.readback_verified else 0
            ),
            "manual_reviews": (
                1
                if result.canonical_status
                in {SUCCEEDED_MANUAL_REVIEW, SUBMITTED_UNVERIFIED}
                else 0
            ),
            "reconciliations": (
                1
                if result.canonical_status == RECONCILIATION_REQUIRED
                else 0
            ),
        },
        "duplicate_prevented": (
            result.canonical_status
            in {SUCCEEDED, SUCCEEDED_MANUAL_REVIEW}
            and not result.external_writes
        ),
        "evidence_digests": [
            evidence_digest,
            target["command_digest"],
            target["proof_digest"],
            target["adapter_policy_digest"],
        ],
    }


def _insert_outcome_receipt(
    connection: sqlite3.Connection,
    *,
    job: Mapping[str, Any],
    target: Mapping[str, Any],
    result: DispatchTargetResult,
    evidence_digest: str,
    now: str,
) -> None:
    receipt = _release_outcome_receipt(
        job=job,
        target=target,
        result=result,
        evidence_digest=evidence_digest,
    )
    encoded = _canonical_json(receipt)
    digest = _digest_json(receipt)
    existing = connection.execute(
        """
        SELECT receipt_digest FROM oneclick_release_outcomes
        WHERE job_id = ? AND target_label = ? AND attempt = ?
        """,
        (
            job["job_id"],
            target["target_label"],
            target["dispatch_count"],
        ),
    ).fetchone()
    if existing:
        if existing["receipt_digest"] != digest:
            raise SystemicIdentityError(
                "terminal release outcome receipt is immutable"
            )
        return
    connection.execute(
        """
        INSERT INTO oneclick_release_outcomes (
            job_id, target_label, attempt, receipt_json, receipt_digest,
            consumer_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """,
        (
            job["job_id"],
            target["target_label"],
            target["dispatch_count"],
            encoded,
            digest,
            now,
            now,
        ),
    )


_PUBLIC_REASON_SUMMARY_BY_CATEGORY = {
    "AUTH": "authorization_required",
    "INVENTORY": "inventory_decision_required",
    "CAPABILITY": "channel_capability_status",
    "CONTENT": "content_contract_status",
    "LOGISTICS": "logistics_contract_status",
    "SAFE_ACTION": "governed_safe_action_required",
    "PRE_SUBMIT": "pre_submit_action_required",
    "POST_WRITE": "reconciliation_required",
    "DEPENDENCY": "dependency_action_required",
    "SYSTEMIC_IDENTITY": "release_identity_invalid",
    "SYSTEMIC_CONTRACT": "release_contract_invalid",
}


def _durable_reason_detail(
    category: object,
    code: object,
    detail: object,
) -> str:
    clean_category = _reason_category(category)
    clean_code = _clean_code(code, "reason_unavailable")
    clean_detail = _clean_detail(detail)
    return _canonical_json(
        {
            "summary_code": _PUBLIC_REASON_SUMMARY_BY_CATEGORY[
                clean_category
            ],
            "code": clean_code,
            "detail_digest": _digest_text(clean_detail),
        }
    )


def _durable_detail_digest(value: str) -> str:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, Mapping):
        candidate = decoded.get("detail_digest")
        if _is_digest(candidate):
            return candidate
    return _digest_text(value)


def _public_reason(
    *,
    category: object,
    scope: object,
    code: object,
    detail: object,
) -> dict[str, str]:
    clean_category = _reason_category(category)
    clean_scope = _reason_scope(scope)
    clean_code = _clean_code(code, "reason_unavailable")
    clean_detail = _clean_detail(detail)
    return {
        "category": clean_category,
        "scope": clean_scope,
        "code": clean_code,
        "summary_code": _PUBLIC_REASON_SUMMARY_BY_CATEGORY[
            clean_category
        ],
        "detail_digest": _durable_detail_digest(clean_detail),
    }


def _public_stored_reason(value: object) -> dict[str, str] | None:
    if value in (None, ""):
        return None
    if type(value) is not str:
        raise SystemicIdentityError("stored systemic reason is invalid")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as error:
        raise SystemicIdentityError(
            "stored systemic reason is invalid"
        ) from error
    if not isinstance(decoded, Mapping):
        raise SystemicIdentityError("stored systemic reason is invalid")
    try:
        category = _reason_category(decoded.get("category"))
        scope = _reason_scope(decoded.get("scope"))
        code = _clean_code(decoded.get("code"), "reason_unavailable")
        summary = _exact_text(
            decoded.get("summary_code"),
            "systemic summary_code",
        )
        detail_digest = decoded.get("detail_digest")
        if (
            summary != _PUBLIC_REASON_SUMMARY_BY_CATEGORY[category]
            or not _is_digest(detail_digest)
        ):
            raise AdapterContractError("systemic reason summary is invalid")
        return {
            "category": category,
            "scope": scope,
            "code": code,
            "summary_code": summary,
            "detail_digest": detail_digest,
        }
    except AdapterContractError as error:
        raise SystemicIdentityError(
            "stored systemic reason is invalid"
        ) from error


def _dependency_state(
    target_label: object,
    statuses: Mapping[str, str],
) -> dict[str, Any]:
    label = _exact_text(target_label, "dependency target_label")
    if label == SHOPEE_GLOBAL_TARGET:
        return {
            "policy_version": DEPENDENCY_POLICY_VERSION,
            "state": "SATISFIED",
            "satisfied": True,
            "prerequisite_target": None,
            "prerequisite_status": None,
        }
    if label.startswith("shopee:"):
        if SHOPEE_GLOBAL_TARGET not in statuses:
            return {
                "policy_version": DEPENDENCY_POLICY_VERSION,
                "state": "BLOCKED",
                "satisfied": False,
                "prerequisite_target": SHOPEE_GLOBAL_TARGET,
                "prerequisite_status": "MISSING",
                "reason_category": "SYSTEMIC_CONTRACT",
                "reason_code": "required_shopee_global_control_missing",
            }
        global_status = statuses[SHOPEE_GLOBAL_TARGET]
        if global_status == SUCCEEDED:
            state, satisfied = "SATISFIED", True
        elif global_status in {PENDING, PREPARING, READY, DISPATCHING}:
            state, satisfied = "WAITING", False
        else:
            state, satisfied = "BLOCKED", False
        return {
            "policy_version": DEPENDENCY_POLICY_VERSION,
            "state": state,
            "satisfied": satisfied,
            "prerequisite_target": SHOPEE_GLOBAL_TARGET,
            "prerequisite_status": global_status,
        }
    if not label.startswith("tiktok:"):
        return {
            "policy_version": DEPENDENCY_POLICY_VERSION,
            "state": "SATISFIED",
            "satisfied": True,
            "prerequisite_target": None,
            "prerequisite_status": None,
        }
    if _COMMON_LABEL not in statuses:
        return {
            "policy_version": DEPENDENCY_POLICY_VERSION,
            "state": "BLOCKED",
            "satisfied": False,
            "prerequisite_target": _COMMON_LABEL,
            "prerequisite_status": "MISSING",
            "reason_category": "SYSTEMIC_CONTRACT",
            "reason_code": "required_common_control_target_missing",
        }
    common_status = statuses[_COMMON_LABEL]
    if common_status == SUCCEEDED:
        state, satisfied = "SATISFIED", True
    elif common_status in {PENDING, PREPARING, READY, DISPATCHING}:
        state, satisfied = "WAITING", False
    else:
        state, satisfied = "BLOCKED", False
    return {
        "policy_version": DEPENDENCY_POLICY_VERSION,
        "state": state,
        "satisfied": satisfied,
        "prerequisite_target": _COMMON_LABEL,
        "prerequisite_status": common_status,
    }


def _attach_shared_control_dependency_summaries(
    targets: list[dict[str, Any]],
) -> None:
    controls = {
        row["target_label"]: row
        for row in targets
        if row.get("control_target") is True
    }
    for row in targets:
        dependency = row.get("dependency")
        if not isinstance(dependency, dict):
            continue
        prerequisite_label = dependency.get("prerequisite_target")
        control = controls.get(prerequisite_label)
        if not control:
            continue
        dependency["prerequisite"] = {
            "target_label": control["target_label"],
            "status": control["status"],
            "reason": control.get("reason"),
            "next_action": control.get("next_action"),
            "digests": {
                "prepared_command": (
                    control.get("digests") or {}
                ).get("prepared_command"),
                "proof": (
                    control.get("digests") or {}
                ).get("proof"),
                "shared_resource": (
                    control.get("digests") or {}
                ).get("shared_resource"),
                "shared_resource_context": (
                    control.get("digests") or {}
                ).get("shared_resource_context"),
            },
        }


def _runnable_ready_count(rows: object) -> int:
    if not isinstance(rows, (list, tuple)):
        rows = list(rows)
    _validate_job_shared_resource_rows(rows)
    status_by_label = {
        row["target_label"]: row["status"]
        for row in rows
    }
    return sum(
        row["status"] == READY
        and _dependency_state(
            row["target_label"],
            status_by_label,
        )["satisfied"]
        is True
        for row in rows
    )


def _public_target(
    row: dict[str, Any],
    *,
    dependency: Mapping[str, Any],
) -> dict[str, Any]:
    status = row["status"]
    result = json.loads(row["result_json"]) if row.get("result_json") else None
    cumulative_writes = _stored_write_classes(row)
    cumulative_count, cumulative_lower, cumulative_upper = (
        _stored_write_count_bounds(row)
    )
    return {
        "target_label": row["target_label"],
        "storefront": bool(row["storefront"]),
        "control_target": bool(row.get("control_target")),
        "classification": row["capability"],
        "status": status,
        "reason": (
            _public_reason(
                category=row["reason_category"],
                scope=row["reason_scope"],
                code=row["reason_code"],
                detail=row["reason_detail"],
            )
            if row["reason_code"]
            else None
        ),
        "manual_after_submit": bool(row["manual_after_submit"]),
        "requires_human": status
        in {SUCCEEDED_MANUAL_REVIEW, SUBMITTED_UNVERIFIED},
        "dependency": dict(dependency),
        "runnable_now": (
            status == READY and dependency["satisfied"] is True
        ),
        "dispatch_count": row["dispatch_count"],
            "dispatch_ledger": {
            "stage": row.get("dispatch_stage"),
            "cumulative_external_write_count": cumulative_count,
            "cumulative_external_write_classes": list(cumulative_writes),
            "confirmed_external_write_count_lower_bound": (
                cumulative_lower
            ),
            "possible_external_write_count_upper_bound": cumulative_upper,
            "digest": row.get("cumulative_external_writes_digest"),
            "stage_evidence_digest": row.get(
                "dispatch_stage_evidence_digest"
            ),
            "pending_write_intent_digest": row.get(
                "pending_write_intent_digest"
            ),
        },
        "digests": {
            "prepared_command": row["command_digest"],
            "proof": row["proof_digest"],
            "adapter_policy": row["adapter_policy_digest"],
            "shared_resource": row.get("shared_resource_digest"),
            "shared_resource_context": row.get(
                "shared_resource_context_digest"
            ),
        },
        "result": result,
        "next_action": _next_action(
            status,
            row["capability"],
            dependency_state=dependency["state"],
            reason_code=row.get("reason_code"),
            reason_category=row.get("reason_category"),
        ),
        "next_action_target": (
            None
            if row.get("reason_code") == "oneclick_dispatch_disabled"
            else (
                dependency["prerequisite_target"]
                if dependency["state"] in {"BLOCKED", "WAITING"}
                else row["target_label"]
            )
        ),
    }


def _next_action(
    status: str,
    capability: str | None,
    *,
    dependency_state: str,
    reason_code: str | None = None,
    reason_category: str | None = None,
) -> str | None:
    if reason_code == "oneclick_dispatch_disabled":
        return "enable_oneclick_dispatch"
    if status == PENDING:
        return "prepare_batch"
    if status == PREPARING:
        return "wait_for_preparation"
    if status == READY:
        if dependency_state == "BLOCKED":
            return "resolve_prerequisite_target"
        if dependency_state == "WAITING":
            return "wait_for_dependency"
        return "wait_for_worker"
    if status == DISPATCHING:
        return "wait_for_dispatch_receipt"
    if status == SUBMITTED_UNVERIFIED:
        return "verify_submission_in_marketplace"
    if status == SUCCEEDED_MANUAL_REVIEW:
        return "review_verified_observation_warning"
    if status == FAILED_PRE_SUBMIT:
        return "retry_exact_zero_write_action"
    if status == RECONCILIATION_REQUIRED:
        return "reconcile_before_any_retry"
    if status == BLOCKED_AUTH:
        return "restore_channel_authorization"
    if status == BLOCKED_INVENTORY:
        return "approve_sellable_inventory"
    if status == BLOCKED_CAPABILITY:
        if reason_category == "CONTENT":
            return "review_approved_content_facts"
        if reason_category == "LOGISTICS":
            return "review_logistics_policy"
        return "wait_for_channel_capability"
    if status == BLOCKED_SOURCE_IDENTITY:
        return "resolve_source_product_identity"
    if status == BLOCKED_SKU_LINEAGE:
        return "resolve_predecessor_sku_lineage"
    if capability == SAFE_ACTION_REQUIRED:
        return "perform_governed_safe_action"
    return None


def _exact_text(value: object, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
    ):
        raise AdapterContractError(
            f"{label} must be a canonical non-empty built-in str"
        )
    return value


def _reason_category(value: str) -> str:
    clean = _exact_text(value, "reason category")
    if clean not in REASON_CATEGORIES:
        raise AdapterContractError("reason category is invalid")
    return clean


def _reason_scope(value: str) -> str:
    clean = _exact_text(value, "reason scope")
    if clean not in REASON_SCOPES:
        raise AdapterContractError("reason scope is invalid")
    return clean


def _clean_code(value: object, fallback: str) -> str:
    candidate = fallback if value is None else value
    clean = _exact_text(candidate, "reason code")
    if not clean or len(clean) > 120 or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
        for character in clean
    ):
        raise AdapterContractError("reason code is invalid")
    return clean


def _clean_detail(value: object) -> str:
    clean = _exact_text(value, "reason detail")
    if len(clean) > 4000:
        raise AdapterContractError("reason detail is too long")
    return clean


def _is_digest(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _ensure_oneclick_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(oneclick_release_targets)"
        )
    }
    additions = {
        "control_target": "INTEGER NOT NULL DEFAULT 0",
        "cumulative_external_writes_json": (
            "TEXT NOT NULL DEFAULT '[]'"
        ),
        "cumulative_external_writes_digest": (
            "TEXT NOT NULL DEFAULT "
            "'4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'"
        ),
        "cumulative_external_write_count": "INTEGER DEFAULT 0",
        "cumulative_external_write_lower_bound": (
            "INTEGER NOT NULL DEFAULT 0"
        ),
        "cumulative_external_write_upper_bound": "INTEGER DEFAULT 0",
        "dispatch_stage": "TEXT",
        "dispatch_stage_evidence_digest": "TEXT",
        "pending_write_intent_json": "TEXT",
        "pending_write_intent_digest": "TEXT",
        "shared_resource_json": "TEXT",
        "shared_resource_digest": "TEXT",
        "shared_resource_context_json": "TEXT",
        "shared_resource_context_digest": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE oneclick_release_targets "
                f"ADD COLUMN {name} {declaration}"
            )


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
