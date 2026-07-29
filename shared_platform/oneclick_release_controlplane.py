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


PREPARED_COMMAND_SCHEMA = "release-target-prepared-command/v1"
BATCH_PREPARATION_SCHEMA = "release-batch-preparation/v1"
PUBLIC_STATUS_SCHEMA = "oneclick-release-status/v1"
REGISTRY_SCHEMA = "release-adapter-registry/v1"
DEPENDENCY_POLICY_VERSION = "oneclick-target-dependency/v1"

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
            for field in ("command", "proof"):
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
        elif result.command is not None or result.proof is not None:
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
    progress_recorder: Callable[
        ["DispatchTargetRequest", tuple[str, ...], str, Mapping[str, Any]],
        None,
    ] | None = None


@dataclass(frozen=True)
class DispatchTargetResult:
    canonical_status: str
    reason_category: str
    reason_scope: str
    reason_code: str
    reason_detail: str
    external_writes: tuple[str, ...]
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
    manual_after_submit INTEGER NOT NULL DEFAULT 0
        CHECK (manual_after_submit IN (0, 1)),
    dispatch_count INTEGER NOT NULL DEFAULT 0,
    cumulative_external_writes_json TEXT NOT NULL DEFAULT '[]',
    cumulative_external_writes_digest TEXT NOT NULL DEFAULT
        '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    dispatch_stage TEXT,
    dispatch_stage_evidence_digest TEXT,
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
            rows = _run_targets(run)
            connection.executemany(
                """
                INSERT INTO oneclick_release_targets (
                    job_id, target_label, ordinal, storefront, status,
                    adapter_name, adapter_policy_digest, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        identity["job_id"],
                        label,
                        ordinal,
                        int(label != _COMMON_LABEL),
                        _initial_public_status(rows[label]),
                        _adapter_name_for_target(label),
                        _policy_digest_for_target(label, registry),
                        now,
                        now,
                    )
                    for ordinal, label in enumerate(targets)
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
                        manual_after_submit = ?, updated_at = ?
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
                        int(prepared.get("manual_after_submit") is True),
                        now,
                        job_id,
                        prepared["target_label"],
                    ),
                )
            ready_count = connection.execute(
                """
                SELECT target_label, status
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
            if claimed.rowcount != 1 or canonical_claimed.rowcount != 1:
                raise SystemicIdentityError("atomic target claim lost a race")
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
                idempotency_key=canonical["idempotency_key"],
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
                command=json.loads(selected["command_json"]),
                proof=json.loads(selected["proof_json"]),
            )

    def record_dispatch_progress(
        self,
        request: DispatchTargetRequest,
        external_writes: tuple[str, ...],
        stage: str,
        evidence: Mapping[str, Any],
    ) -> None:
        """Durably accumulate confirmed write classes during a composite dispatch.

        The evidence payload is never stored; only its digest is retained.  This
        lets a later exception or process restart preserve every earlier write
        without leaking marketplace responses or command data.
        """

        additions = _validated_write_classes(external_writes)
        if not additions:
            raise AdapterContractError("dispatch progress requires a write class")
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
                or not canonical
                or canonical["status"] != "RUNNING"
                or canonical["attempts"] != target["dispatch_count"]
            ):
                raise SystemicIdentityError(
                    "dispatch progress does not match the active atomic claim"
                )
            cumulative = _merge_write_classes(
                _stored_write_classes(target), additions
            )
            connection.execute(
                """
                UPDATE oneclick_release_targets
                SET cumulative_external_writes_json = ?,
                    cumulative_external_writes_digest = ?,
                    dispatch_stage = ?,
                    dispatch_stage_evidence_digest = ?,
                    updated_at = ?
                WHERE job_id = ? AND target_label = ?
                """,
                (
                    _canonical_json(list(cumulative)),
                    _digest_json(list(cumulative)),
                    stage_value,
                    evidence_digest,
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
                    "cumulative_external_write_count": len(cumulative),
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
                ):
                    raise SystemicIdentityError(
                        "verified success readback is unavailable"
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
                        if _UNKNOWN_WRITE_CLASS in cumulative_writes
                        else len(cumulative_writes)
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
                        result_json = ?, updated_at = ?, completed_at = ?
                    WHERE job_id = ? AND target_label = ?
                    """,
                    (
                        durable_detail,
                        _canonical_json(list(cumulative_writes)),
                        _digest_json(list(cumulative_writes)),
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
                    external_id=(
                        canonical["external_id"] if canonical else None
                    ),
                    dispatch_outcome_unknown=True,
                    evidence={"durable_state_uncertain": True},
                )
                _insert_outcome_receipt(
                    connection,
                    job=row,
                    target=row,
                    result=recovered_result,
                    evidence_digest=digest,
                    now=now,
                )
                self._refresh_canonical_run(connection, row["run_id"], now)
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
                        dispatch_stage = NULL,
                        dispatch_stage_evidence_digest = NULL,
                        result_json = NULL, completed_at = NULL,
                        updated_at = ?
                    WHERE job_id = ? AND target_label = ?
                    """,
                    (now, job_id, row["target_label"]),
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
        return {
            "target_label": label,
            "classification": result.classification,
            "status": READY,
            "reason_category": result.reason_category,
            "reason_scope": result.reason_scope,
            "reason_code": result.reason_code,
            "reason_detail": result.reason_detail,
            "command_json": _canonical_json(command),
            "command_digest": _digest_json(command),
            "proof_json": _canonical_json(proof),
            "proof_digest": _digest_json(proof),
            "manual_after_submit": result.manual_after_submit,
        }

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
        return {
            "job": job,
            "plan": plan,
            "run": run,
            "source_identity": source_identity,
            "targets": [
                {
                    **dict(row),
                    "idempotency_key": targets[row["target_label"]][
                        "idempotency_key"
                    ],
                }
                for row in self._raw_targets(job_id)
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
                json.loads(plan["target_labels_json"]),
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
            SELECT target_label, status
            FROM oneclick_release_targets
            WHERE job_id = ?
            """,
            (job["job_id"],),
        ).fetchall()
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
            SELECT target_label, status FROM oneclick_release_targets
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
        status_by_label = {
            row["target_label"]: row["status"]
            for row in rows
        }
        targets = [
            _public_target(
                dict(row),
                dependency=_dependency_state(
                    row["target_label"],
                    status_by_label,
                ),
            )
            for row in rows
        ]
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
        for target in targets:
            outcome = outcomes.get(target["target_label"])
            target["outcome_receipt"] = (
                {
                    "schema_version": "release-outcome-receipt/v1",
                    "attempt": outcome["attempt"],
                    "receipt_digest": outcome["receipt_digest"],
                    "consumer_status": outcome["consumer_status"],
                    "fact_digest": outcome["fact_digest"],
                    "error_code": outcome["error_code"],
                }
                if outcome
                else None
            )
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
            "control_row_count": len(targets) - len(storefronts),
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
                    raise DispatchInvocationError(
                        "adapter receipt omitted a confirmed composite write",
                        external_writes=cumulative,
                        dispatch_outcome_unknown=True,
                        external_id=result.external_id,
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
                cumulative = _merge_write_classes(
                    self.store.cumulative_external_writes(request),
                    error.external_writes,
                )
                if error.dispatch_outcome_unknown or not cumulative:
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
                    external_id=error.external_id,
                    dispatch_outcome_unknown=True,
                    evidence={
                        "durable_state_uncertain": True,
                        "cumulative_external_write_count": (
                            None
                            if _UNKNOWN_WRITE_CLASS in cumulative
                            else len(cumulative)
                        ),
                    },
                )
            except Exception as error:
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
    for label in _target_labels(plan):
        result = pseudo._prepare_one(  # type: ignore[attr-defined]
            context,
            {
                "target_label": label,
                "status": _initial_public_status(rows[label]),
                "idempotency_key": rows[label]["idempotency_key"],
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
            "storefront": result["target_label"] != _COMMON_LABEL,
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
                    if dependency["state"] == "BLOCKED"
                    else result["target_label"]
                )
            ),
        }
        prepared.append(public)
        if result["reason_scope"] == SYSTEMIC_IDENTITY_SCOPE:
            systemic = public["reason"]
            break
    storefronts = [row for row in prepared if row["storefront"]]
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
        "targets": prepared,
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
    registry_digest = _registry_digest(targets, registry)
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
        ),
        "proof_content": _digest_json(request.proof) == request.proof_digest,
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
    if evidence.get("manual_review_accepted") is not True:
        raise AdapterContractError(
            "observation acceptance requires manual_review_accepted=true"
        )
    digest = evidence.get("observation_evidence_digest")
    expected = result.get("observation_digests")
    if (
        not _is_digest(digest)
        or type(expected) is not list
        or digest not in expected
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
        "canonical_status": result.canonical_status,
        "reason_category": result.reason_category,
        "reason_scope": result.reason_scope,
        "reason_code": result.reason_code,
        "external_writes_performed": list(result.external_writes),
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
    count = (
        None
        if _UNKNOWN_WRITE_CLASS in result.external_writes
        else len(result.external_writes)
    )
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
    write_count = (
        None
        if _UNKNOWN_WRITE_CLASS in result.external_writes
        else len(result.external_writes)
    )
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


def _runnable_ready_count(rows: object) -> int:
    if not isinstance(rows, (list, tuple)):
        rows = list(rows)
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
    cumulative_count = (
        None
        if _UNKNOWN_WRITE_CLASS in cumulative_writes
        else len(cumulative_writes)
    )
    return {
        "target_label": row["target_label"],
        "storefront": bool(row["storefront"]),
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
            "digest": row.get("cumulative_external_writes_digest"),
            "stage_evidence_digest": row.get(
                "dispatch_stage_evidence_digest"
            ),
        },
        "digests": {
            "prepared_command": row["command_digest"],
            "proof": row["proof_digest"],
            "adapter_policy": row["adapter_policy_digest"],
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
                if dependency["state"] == "BLOCKED"
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
        "cumulative_external_writes_json": (
            "TEXT NOT NULL DEFAULT '[]'"
        ),
        "cumulative_external_writes_digest": (
            "TEXT NOT NULL DEFAULT "
            "'4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'"
        ),
        "dispatch_stage": "TEXT",
        "dispatch_stage_evidence_digest": "TEXT",
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
