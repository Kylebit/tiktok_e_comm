"""Server-owned orchestration for one approved publication snapshot.

The runner is deliberately narrower than a channel adapter.  It loads exactly
one durable ``approved-publication-snapshot/v4``, passes detached copies to
injected per-platform executors, and stores only a redacted outcome report.  It
does not read mutable product/dashboard/source/content state and it contains no
network or provider client.
"""

from __future__ import annotations

import hashlib
import json

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from domains.product_operations import (
    APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION,
    validate_approved_publication_snapshot,
)
from shared_platform.product_publication_reports import (
    SUMMARY_SCHEMA_VERSION,
    ProductPublicationReportStore,
    StoredPublicationReport,
    _safe_recovery_retry_authorization,
    _safe_continuation_evidence,
    publication_report_id,
)
from shared_platform.publication_write_budget import PublicationWriteBudgetLedger


PLATFORM_RESULT_SCHEMA_VERSION = "product-publication-platform-result/v1"
INTERNAL_REPORT_SCHEMA_VERSION = "product-publication-report/v2"
_LEGACY_EXECUTION_IDENTITY = {"skill_digest": "0" * 64, "git_commit": "0" * 40, "code_digest": "0" * 64}
_PLATFORM_ORDER = ("TIKTOK", "SHOPEE", "OZON")
_PLATFORMS = frozenset(_PLATFORM_ORDER)
_TARGET_STATUSES = frozenset({"PUBLISHED", "PROCESSING", "FAILED"})
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "platform",
        "targets",
        "dispatch_attempted",
        "readback_completed",
        "external_write_count",
        "requires_human_action",
    }
)
_TARGET_FIELDS = frozenset({"target_label", "status"})
_TARGET_FIELDS_WITH_EVIDENCE = frozenset({"target_label", "status", "evidence"})
_TARGET_EVIDENCE_FIELDS = frozenset(
    {"target_label", "status", "stage", "provider_code", "provider_field_path", "provider_reason", "request_attempted", "outcome_unknown", "external_write_count", "provider_identity_bound"}
)
_EXECUTION_IDENTITY_FIELDS = frozenset({"skill_digest", "git_commit", "code_digest"})
_OZON_EXECUTION_IDENTITY_FIELDS = frozenset({"ozon_account_id", "ozon_credentials_sha256"})


class ApprovedPublicationSnapshotStore(Protocol):
    def approved_publication_snapshot(
        self,
        *,
        offer_id: str,
        plan_id: str | None = None,
        snapshot_digest: str | None = None,
    ) -> dict[str, Any] | None: ...


class ProductPublicationRunnerError(RuntimeError):
    """Base error for the server-owned publication runner."""


class ProductPublicationRunConflictError(ProductPublicationRunnerError):
    """The durable run identity is already bound to different facts."""


@dataclass(frozen=True)
class PublicationPlatformRequest:
    run_id: str
    report_id: str
    platform: str
    target_labels: tuple[str, ...]
    snapshot: dict[str, Any]
    release_candidate: dict[str, Any] | None = None
    write_budget: dict[str, Any] | None = None
    write_budget_ledger: PublicationWriteBudgetLedger | None = None
    catalog_sink: object | None = None
    checkpoint_root: object | None = None


@dataclass(frozen=True)
class PublicationRunReceipt:
    report: dict[str, Any]
    stored: StoredPublicationReport
    replayed: bool


@dataclass(frozen=True)
class PreparedPublicationRun:
    """Validated immutable identity used before a background run is queued."""

    offer_id: str
    revision: int
    plan_id: str
    snapshot_digest: str
    platform_scope: tuple[str, ...]
    target_labels_by_platform: dict[str, tuple[str, ...]]
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class _PlatformOutcome:
    summary: dict[str, Any]
    targets: list[dict[str, Any]]
    dispatch_attempted: bool
    readback_completed: bool
    external_write_count: int | None
    requires_human_action: bool
    continuation_evidence: dict[str, Any] | None = None


PlatformExecutor = Callable[[PublicationPlatformRequest], Mapping[str, Any]]


def _execution_identity(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("execution_identity must be a mapping")
    fields = set(value)
    if fields != set(_EXECUTION_IDENTITY_FIELDS) and fields != set(
        _EXECUTION_IDENTITY_FIELDS | _OZON_EXECUTION_IDENTITY_FIELDS
    ):
        raise ValueError("execution_identity fields are invalid")
    result = {name: _text(value[name], name, max_length=64) for name in _EXECUTION_IDENTITY_FIELDS}
    if len(result["git_commit"]) != 40 or any(c not in "0123456789abcdef" for c in result["git_commit"]):
        raise ValueError("git_commit is invalid")
    for name in ("skill_digest", "code_digest"):
        if len(result[name]) != 64 or any(c not in "0123456789abcdef" for c in result[name]):
            raise ValueError(f"{name} is invalid")
    if _OZON_EXECUTION_IDENTITY_FIELDS.issubset(fields):
        account = _text(value["ozon_account_id"], "ozon_account_id", max_length=64)
        digest = _text(value["ozon_credentials_sha256"], "ozon_credentials_sha256", max_length=64)
        if not account.isdecimal() or int(account) <= 0:
            raise ValueError("ozon_account_id is invalid")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("ozon_credentials_sha256 is invalid")
        result.update(ozon_account_id=account, ozon_credentials_sha256=digest)
    return result


def _target_evidence(value: object, *, label: str, status: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("target evidence must be a mapping or null")
    fields = set(value)
    required = _TARGET_EVIDENCE_FIELDS - {"provider_field_path", "provider_identity_bound"}
    if not required.issubset(fields) or not fields.issubset(_TARGET_EVIDENCE_FIELDS):
        raise ValueError("target evidence fields are invalid")
    if value["target_label"] != label or value["status"] != status:
        raise ValueError("target evidence identity conflicts")
    stage = _text(value["stage"], "target evidence stage", max_length=32)
    code = _text(value["provider_code"], "provider_code", max_length=80)
    field_path = value.get("provider_field_path", "")
    if type(field_path) is not str or len(field_path) > 160 or any(
        not (character.isalnum() or character in "_-.[]")
        for character in field_path
    ):
        raise ValueError("provider_field_path is unsafe")
    reason = _text(value["provider_reason"], "provider_reason", max_length=240)
    if not code.isascii() or any(not (c.isalnum() or c in "_-.:") for c in code):
        raise ValueError("provider_code is unsafe")
    if "http://" in reason.casefold() or "https://" in reason.casefold():
        raise ValueError("provider_reason contains a URL")
    attempted = value["request_attempted"]
    unknown = value["outcome_unknown"]
    if type(attempted) is not bool or type(unknown) is not bool:
        raise TypeError("target evidence flags must be boolean")
    count = _nonnegative_int_or_none(value["external_write_count"], "target evidence external_write_count")
    result = {"target_label": label, "status": status, "stage": stage, "provider_code": code, "provider_reason": reason, "request_attempted": attempted, "outcome_unknown": unknown, "external_write_count": count}
    if "provider_identity_bound" in value:
        if type(value["provider_identity_bound"]) is not bool:
            raise TypeError("provider_identity_bound must be boolean")
        result["provider_identity_bound"] = value["provider_identity_bound"]
    if field_path:
        result["provider_field_path"] = field_path
    return result


def _text(value: object, name: str, *, max_length: int = 512) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip() or len(value) > max_length:
        raise ValueError(f"{name} is invalid")
    return value


def _offer_id(value: object) -> str:
    offer_id = _text(value, "offer_id", max_length=32)
    if not offer_id.isascii() or not offer_id.isdigit() or int(offer_id) <= 0:
        raise ValueError("offer_id is invalid")
    return offer_id


def _scope(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("platform_scope must be a sequence")
    requested = list(value)
    if not requested:
        raise ValueError("platform_scope cannot be empty")
    if any(type(platform) is not str for platform in requested):
        raise TypeError("platform_scope values must be strings")
    if any(platform not in _PLATFORMS for platform in requested):
        raise ValueError("platform_scope contains an unsupported platform")
    if len(requested) != len(set(requested)):
        raise ValueError("platform_scope contains duplicates")
    selected = set(requested)
    return tuple(platform for platform in _PLATFORM_ORDER if platform in selected)


def _executors(
    value: object, *, scope: tuple[str, ...]
) -> dict[str, PlatformExecutor]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise TypeError("platform_executors must be a string-keyed mapping")
    if set(value) != set(scope):
        raise ValueError("platform_executors must exactly match platform_scope")
    result: dict[str, PlatformExecutor] = {}
    for platform in scope:
        executor = value[platform]
        if not callable(executor):
            raise TypeError(f"platform executor {platform} must be callable")
        result[platform] = executor
    return result


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    if set(value) != set(expected):
        missing = sorted(set(expected) - set(value))
        extra = sorted(set(value) - set(expected))
        raise ValueError(f"{name} fields are invalid; missing={missing}; extra={extra}")


def _nonnegative_int_or_none(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer or null")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _restrictive_write_budget(value: Mapping[str, Any], approved: Mapping[str, Any],
                              selected_labels: Sequence[str]) -> dict[str, Any]:
    narrowed = deepcopy(dict(value))
    labels = list(selected_labels)
    if (set(narrowed) != {"shared_maximum", "per_target_maximum", "target_labels"}
            or narrowed.get("target_labels") != labels
            or type(narrowed.get("shared_maximum")) is not int
            or type(narrowed.get("per_target_maximum")) is not int
            or narrowed["shared_maximum"] < 0 or narrowed["per_target_maximum"] < 0
            or narrowed["shared_maximum"] > approved["shared_maximum"]
            or narrowed["per_target_maximum"] > approved["per_target_maximum"]):
        raise ValueError("write budget override must strictly narrow approved limits")
    return narrowed


def _classify(statuses: Sequence[str]) -> str:
    unique = set(statuses)
    if unique == {"PUBLISHED"}:
        return "PUBLISHED"
    if unique == {"PROCESSING"}:
        return "PROCESSING"
    if unique == {"FAILED"}:
        return "FAILED"
    return "PARTIAL"


def _validate_platform_result(
    value: object,
    *,
    platform: str,
    expected_targets: tuple[str, ...],
) -> _PlatformOutcome:
    if not isinstance(value, Mapping):
        raise TypeError("platform result must be a mapping")
    if set(value) not in {_RESULT_FIELDS, _RESULT_FIELDS | {"continuation_evidence"}}:
        raise ValueError("platform result fields are invalid")
    if value["schema_version"] != PLATFORM_RESULT_SCHEMA_VERSION:
        raise ValueError("unsupported platform result schema")
    if value["platform"] != platform:
        raise ValueError("platform result identity conflicts")
    for name in (
        "dispatch_attempted",
        "readback_completed",
        "requires_human_action",
    ):
        if type(value[name]) is not bool:
            raise TypeError(f"platform result {name} must be a boolean")

    raw_targets = value["targets"]
    if type(raw_targets) is not list:
        raise TypeError("platform result targets must be a list")
    statuses_by_target: dict[str, str] = {}
    safe_targets: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, Mapping):
            raise TypeError(f"platform result targets[{index}] must be a mapping")
        if set(raw) not in {_TARGET_FIELDS, _TARGET_FIELDS_WITH_EVIDENCE}:
            raise ValueError(f"platform result targets[{index}] fields are invalid")
        target_label = _text(raw["target_label"], "target_label")
        status = _text(raw["status"], "target status", max_length=16)
        if status not in _TARGET_STATUSES:
            raise ValueError("platform target status is unsupported")
        if target_label in statuses_by_target:
            raise ValueError("platform result target is duplicated")
        statuses_by_target[target_label] = status
        safe_targets.append({"target_label": target_label, "status": status, "evidence": _target_evidence(raw.get("evidence"), label=target_label, status=status)})
    if set(statuses_by_target) != set(expected_targets):
        raise ValueError("platform result target coverage conflicts")

    statuses = [statuses_by_target[target] for target in expected_targets]
    verified_count = statuses.count("PUBLISHED")
    processing_count = statuses.count("PROCESSING")
    failed_count = statuses.count("FAILED")
    writes = _nonnegative_int_or_none(
        value["external_write_count"], "external_write_count"
    )
    continuation = None
    if "continuation_evidence" in value:
        if platform != "TIKTOK":
            raise ValueError("continuation evidence is TikTok-only")
        continuation = _safe_continuation_evidence(value["continuation_evidence"])
        detailed = continuation["targets"]
        if ({row["target_label"] for row in detailed} != set(expected_targets)
                or any(statuses_by_target[row["target_label"]] != (
                    "FAILED" if row["status"] == "UNKNOWN" else row["status"]
                ) for row in detailed)):
            raise ValueError("continuation public target projection differs")
        expected_writes = None if continuation["unknown_write_count"] else continuation["confirmed_write_count"]
        if writes != expected_writes:
            raise ValueError("continuation public write count differs")
    return _PlatformOutcome(
        summary={
            "platform": platform,
            "status": _classify(statuses),
            "target_count": len(expected_targets),
            "verified_count": verified_count,
            "processing_count": processing_count,
            "failed_count": failed_count,
        },
        targets=safe_targets,
        dispatch_attempted=value["dispatch_attempted"],
        readback_completed=value["readback_completed"],
        external_write_count=writes,
        requires_human_action=(
            value["requires_human_action"] or failed_count > 0
        ),
        continuation_evidence=continuation,
    )


def _pre_mutation_failed_outcome(
    platform: str, targets: tuple[str, ...]
) -> _PlatformOutcome:
    evidence = [
        {
            "target_label": label,
            "status": "FAILED",
            "evidence": {
                "target_label": label,
                "status": "FAILED",
                "stage": "PREPARATION",
                "provider_code": "executor_preparation_failed",
                "provider_field_path": "preparation",
                "provider_reason": "Platform preparation failed before mutation",
                "request_attempted": False,
                "outcome_unknown": False,
                "external_write_count": 0,
            },
        }
        for label in targets
    ]
    return _PlatformOutcome(
        summary={
            "platform": platform,
            "status": "FAILED",
            "target_count": len(targets),
            "verified_count": 0,
            "processing_count": 0,
            "failed_count": len(targets),
        },
        targets=evidence,
        dispatch_attempted=False,
        readback_completed=False,
        external_write_count=0,
        requires_human_action=True,
    )


def _failed_outcome(platform: str, targets: tuple[str, ...]) -> _PlatformOutcome:
    return _PlatformOutcome(
        summary={
            "platform": platform,
            "status": "FAILED",
            "target_count": len(targets),
            "verified_count": 0,
            "processing_count": 0,
            "failed_count": len(targets),
        },
        targets=[{"target_label": label, "status": "FAILED", "evidence": None} for label in targets],
        # Invocation crossed the adapter boundary.  The runner cannot prove
        # whether a provider write occurred after an exception/malformed result.
        dispatch_attempted=True,
        readback_completed=False,
        external_write_count=None,
        requires_human_action=True,
    )


def _target_labels_by_platform(
    snapshot: Mapping[str, Any], scope: tuple[str, ...]
) -> dict[str, tuple[str, ...]]:
    selected = set(scope)
    result: dict[str, list[str]] = {platform: [] for platform in scope}
    for target in snapshot["publication_targets"]:
        platform = target["platform"].upper()
        if platform in selected:
            result[platform].append(target["target_label"])
    missing = [platform for platform in scope if not result[platform]]
    if missing:
        raise ValueError(
            f"approved publication snapshot has no targets for platforms {missing}"
        )
    return {platform: tuple(result[platform]) for platform in scope}


def _existing_scope(report: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(row["platform"] for row in report["summary"]["platforms"])


def prepare_product_publication_run(
    *,
    release_store: ApprovedPublicationSnapshotStore,
    offer_id: str,
    plan_id: str | None = None,
    snapshot_digest: str | None = None,
    platform_scope: Sequence[str],
    target_scope: Sequence[str] | None = None,
) -> PreparedPublicationRun:
    """Resolve and validate one frozen v4 snapshot without invoking a provider."""

    safe_offer_id = _offer_id(offer_id)
    if (plan_id is None) == (snapshot_digest is None):
        raise ValueError("exactly one of plan_id or snapshot_digest is required")
    if plan_id is not None:
        plan_id = _text(plan_id, "plan_id")
    if snapshot_digest is not None:
        snapshot_digest = _text(snapshot_digest, "snapshot_digest", max_length=71)
    scope = _scope(platform_scope)

    raw_snapshot = release_store.approved_publication_snapshot(
        offer_id=safe_offer_id,
        plan_id=plan_id,
        snapshot_digest=snapshot_digest,
    )
    if raw_snapshot is None:
        raise ValueError("approved publication snapshot is unavailable")
    snapshot = validate_approved_publication_snapshot(raw_snapshot).payload()
    if snapshot["offer_id"] != safe_offer_id:
        raise ValueError("approved publication snapshot offer identity conflicts")
    if plan_id is not None and snapshot["plan_id"] != plan_id:
        raise ValueError("approved publication snapshot plan identity conflicts")
    if snapshot_digest is not None and snapshot["snapshot_digest"] != snapshot_digest:
        raise ValueError("approved publication snapshot digest identity conflicts")
    targets_by_platform = _target_labels_by_platform(snapshot, scope)
    if target_scope is not None:
        if isinstance(target_scope, (str, bytes)) or not isinstance(target_scope, Sequence):
            raise TypeError("target_scope must be a sequence")
        labels = tuple(_text(label, "target label") for label in target_scope)
        available = {label for rows in targets_by_platform.values() for label in rows}
        if not labels or len(labels) != len(set(labels)) or set(labels) - available:
            raise ValueError("target_scope contains duplicate or unapproved targets")
        targets_by_platform = {platform:tuple(label for label in rows if label in labels)
            for platform,rows in targets_by_platform.items()}
        if any(not rows for rows in targets_by_platform.values()):
            raise ValueError("target_scope omits a selected platform")
    return PreparedPublicationRun(
        offer_id=safe_offer_id,
        revision=snapshot["product_revision"],
        plan_id=snapshot["plan_id"],
        snapshot_digest=snapshot["snapshot_digest"],
        platform_scope=scope,
        target_labels_by_platform=targets_by_platform,
        snapshot=deepcopy(snapshot),
    )


def _strict_zero_dispatch_failed_report(
    report: Mapping[str, Any], *, ordered_labels: tuple[str, ...], platform: str
) -> bool:
    """Prove an exact failed report never crossed a registered mutation boundary."""

    try:
        summary = report["summary"]
        summary_evidence = summary["evidence"]
        rows = report["targets"]
        budgets = report["mutation_budgets"]
        if (
            report["status"] != "FAILED"
            or summary_evidence["dispatch_attempted"] is not False
            or type(summary_evidence["external_write_count"]) is not int
            or summary_evidence["external_write_count"] != 0
            or [row["target_label"] for row in rows] != list(ordered_labels)
            or any(row["status"] != "FAILED" for row in rows)
            or not isinstance(budgets, list)
            or len(budgets) != 1
            or budgets[0]["platform"] != platform
            or budgets[0]["reservations"] != []
        ):
            return False
        attempts = budgets[0]["attempts"]
        per_target = attempts["per_target"]
        if not (
            type(attempts["shared"]) is int
            and attempts["shared"] == 0
            and type(attempts["total"]) is int
            and attempts["total"] == 0
            and isinstance(per_target, Mapping)
            and set(per_target) == set(ordered_labels)
            and all(type(value) is int and value == 0 for value in per_target.values())
        ):
            return False
        for row in rows:
            evidence = row.get("evidence")
            if evidence is None:
                continue
            if not isinstance(evidence, Mapping) or (
                evidence.get("request_attempted") is not False
                or evidence.get("outcome_unknown") is not False
                or type(evidence.get("external_write_count")) is not int
                or evidence.get("external_write_count") != 0
            ):
                return False
        return True
    except (KeyError, TypeError, IndexError):
        return False


def _retry_source_zero_dispatch_failed_report(
    report: Mapping[str, Any], *, ordered_labels: tuple[str, ...], platform: str
) -> bool:
    """Verify an explicit failed subset from one proven zero-dispatch source."""

    try:
        rows = report["targets"]
        report_labels = tuple(row["target_label"] for row in rows)
        selected = set(ordered_labels)
        if (
            not selected
            or len(selected) != len(ordered_labels)
            or not selected.issubset(report_labels)
            or tuple(label for label in report_labels if label in selected)
                != ordered_labels
        ):
            return False
        if _strict_zero_dispatch_failed_report(
            report, ordered_labels=report_labels, platform=platform
        ):
            return True
        summary = report["summary"]["evidence"]
        if "release_authorization" in report or "mutation_budgets" in report:
            return False
        if (
            report["status"] != "FAILED"
            or summary["dispatch_attempted"] is not False
            or type(summary["external_write_count"]) is not int
            or summary["external_write_count"] != 0
        ):
            return False
        by_label = {row["target_label"]: row for row in rows}
        for label in ordered_labels:
            row = by_label[label]
            evidence = row.get("evidence")
            if row["status"] != "FAILED" or not (
                evidence is None
                or (
                    isinstance(evidence, Mapping)
                    and evidence.get("request_attempted") is False
                    and evidence.get("outcome_unknown") is False
                    and type(evidence.get("external_write_count")) is int
                    and evidence.get("external_write_count") == 0
                )
            ):
                return False
        return True
    except (KeyError, TypeError, IndexError):
        return False


def claim_product_publication_request(*, prepared, platform, execution_identity,
                                      run_store, report_store, release_store,
                                      retry_of_run_id=None,
                                      recovery_manifest_digest=None,
                                      recovery_continuation=None,
                                      known_zero_continuation_authority=None,
                                      known_zero_continuation_validation=None,
                                      known_zero_receipt=None,
                                      reportless_continuation_authority=None,
                                      reportless_continuation_validation=None,
                                      recovery_reconciliation_receipt=None,
                                      recovery_evidence_roots=None,
                                      recovery_zero_write_receipt=None,
                                      recovery_zero_write_validation=None,
                                      tiktok_continuation_context=None):
    """Claim an approved platform request in the existing durable run ledger.

    Repeats return the original run, including crashes and unknown submissions,
    for offers outside the registered R3 lifecycle. Registered R3 offers must
    use a separate writer bound to current post-COMMON admission.
    """
    from shared_platform.registered_r3_legacy_publish_guard import require_legacy_publish_admission
    require_legacy_publish_admission(
        prepared.offer_id, release_store=release_store, plan_id=prepared.plan_id,
    )
    if tiktok_continuation_context is not None:
        if (platform != 'TIKTOK' or not isinstance(tiktok_continuation_context, Mapping)
                or not {'data', 'completion', 'context_reader'} <= set(tiktok_continuation_context)
                or set(tiktok_continuation_context) - {'data', 'completion', 'context_reader', 'authority_root'}
                or type(tiktok_continuation_context['completion']) is not bool
                or not callable(tiktok_continuation_context['context_reader'])
                or any(value is not None for value in (
                    retry_of_run_id, recovery_manifest_digest, recovery_continuation,
                    known_zero_continuation_authority, known_zero_continuation_validation,
                    known_zero_receipt, reportless_continuation_authority,
                    reportless_continuation_validation, recovery_reconciliation_receipt,
                    recovery_evidence_roots, recovery_zero_write_receipt,
                    recovery_zero_write_validation))):
            raise ValueError('TikTok continuation cannot mix with another recovery contract')
        from shared_platform.tiktok_continuation_claim import claim_tiktok_continuation
        return claim_tiktok_continuation(prepared=prepared, execution_identity=execution_identity,
            run_store=run_store, report_store=report_store, **tiktok_continuation_context)
    targets = set(prepared.target_labels_by_platform[platform])
    if recovery_manifest_digest is not None:
        recovery_manifest_digest = _text(
            recovery_manifest_digest, "recovery_manifest_digest", max_length=71
        )
        digest_hex = recovery_manifest_digest.removeprefix("sha256:")
        if len(digest_hex) != 64 or any(c not in "0123456789abcdef" for c in digest_hex):
            raise ValueError("recovery_manifest_digest is invalid")
    continuation_prior = None
    reportless_continuation = False
    known_zero_continuation = False
    known_zero_authority = None
    continuation_lineage_receipt = recovery_reconciliation_receipt
    if recovery_continuation is not None:
        if recovery_manifest_digest is None or retry_of_run_id is not None \
                or not isinstance(recovery_continuation, Mapping) \
                or recovery_continuation.get("schema_version") != "shopee-recovery-continuation/v1" \
                or recovery_continuation.get("target_scope") != list(prepared.target_labels_by_platform[platform]):
            raise ValueError("recovery continuation identity is invalid")
        if (known_zero_continuation_authority is not None
                and reportless_continuation_authority is not None):
            raise ValueError("continuation authority kinds conflict")
        if (known_zero_continuation_authority is None
                and (known_zero_continuation_validation is not None
                     or known_zero_receipt is not None)):
            raise ValueError("known-zero continuation authority is required")
        if known_zero_continuation_authority is not None:
            authority = known_zero_continuation_authority
            validation = known_zero_continuation_validation
            if (
                platform != "SHOPEE"
                or not isinstance(validation, Mapping)
                or set(validation) != {
                    "candidate", "approval", "operations_db",
                    "allowed_evidence_roots", "allowed_relocation_root",
                    "source_reportless_receipt",
                }
                or not isinstance(validation["candidate"], Mapping)
                or not isinstance(validation["approval"], Mapping)
                or not validation["operations_db"]
                or not validation["allowed_evidence_roots"]
                or not validation["allowed_relocation_root"]
                or not isinstance(validation["source_reportless_receipt"], Mapping)
            ):
                raise ValueError(
                    "known-zero continuation deep validation context is required"
                )
            from shared_platform.shopee_known_zero_continuation import (
                ShopeeKnownZeroContinuationError,
                validate_known_zero_continuation_manifest,
            )
            from shared_platform.shopee_known_zero_recovery import (
                validate_known_zero_receipt,
            )
            from shared_platform.shopee_reportless_recovery_reconciliations import (
                validate_reportless_continuation_manifest,
            )
            from shared_platform.shopee_recovery_reconciliations import (
                ShopeeRecoveryReconciliationError,
            )

            continuation_lineage_receipt = validation["source_reportless_receipt"]
            if not isinstance(known_zero_receipt, Mapping):
                raise ValueError("known-zero continuation receipt is required")

            def validate_source_manifest(value):
                return validate_reportless_continuation_manifest(
                    value,
                    receipt=continuation_lineage_receipt,
                    run_store=run_store,
                    report_store=report_store,
                    snapshot=prepared.snapshot,
                    candidate=validation["candidate"],
                    approval=validation["approval"],
                    operations_db=validation["operations_db"],
                    allowed_evidence_roots=validation["allowed_evidence_roots"],
                )

            try:
                known_zero_receipt = validate_known_zero_receipt(
                    known_zero_receipt,
                    run_store=run_store,
                    report_store=report_store,
                    manifest_validator=validate_source_manifest,
                )
                authority = validate_known_zero_continuation_manifest(
                    authority,
                    allowed_relocation_root=validation["allowed_relocation_root"],
                    run_store=run_store,
                    report_store=report_store,
                    manifest_validator=validate_source_manifest,
                    operations_db=validation["operations_db"],
                    allowed_evidence_roots=validation["allowed_evidence_roots"],
                )
            except (
                KeyError, TypeError, ValueError,
                ShopeeKnownZeroContinuationError,
                ShopeeRecoveryReconciliationError,
            ) as error:
                raise ValueError(
                    "known-zero continuation authority deep validation failed"
                ) from error
            if (
                not isinstance(authority, Mapping)
                or authority.get("schema_version")
                   != "shopee-known-zero-recovery-execution-manifest/v1"
                or authority.get("status") != "READY_ZERO_WRITE_PREFLIGHT"
                or authority.get("authorized") is not True
                or authority.get("manifest_digest") != recovery_manifest_digest
                or authority.get("continuation") != recovery_continuation
                or authority.get("offer_id") != prepared.offer_id
                or authority.get("product_revision") != prepared.revision
                or authority.get("plan_id") != prepared.plan_id
                or authority.get("execution_snapshot_digest")
                   != prepared.snapshot_digest
                or authority.get("target_labels")
                   != list(prepared.target_labels_by_platform[platform])
            ):
                raise ValueError("known-zero continuation authority conflicts")
            predecessor_run_id = authority.get("direct_predecessor_run_id")
            source_receipt_digest = authority.get("source_receipt_digest")
            source_execution_manifest_digest = authority.get(
                "source_execution_manifest_digest"
            )
            preflight_digest = authority.get("preflight_digest")
            evidence_relocation_digest = authority.get("evidence_relocation_digest")
            if (
                type(predecessor_run_id) is not str
                or type(source_receipt_digest) is not str
                or type(source_execution_manifest_digest) is not str
                or type(preflight_digest) is not str
                or type(evidence_relocation_digest) is not str
                or recovery_continuation.get("receipt_digest") != preflight_digest
                or known_zero_receipt.get("receipt_digest")
                   != source_receipt_digest
                or known_zero_receipt.get("schema_version")
                   != "shopee-known-zero-recovery-reconciliation/v1"
                or known_zero_receipt.get("result") != "KNOWN_ZERO_PREFLIGHT"
                or known_zero_receipt.get("attempt_closed") is not True
                or known_zero_receipt.get("provider_mutation_dispatch_attempted")
                   is not False
                or known_zero_receipt.get("external_write_count") != 0
                or (known_zero_receipt.get("run_identity") or {}).get("run_id")
                   != predecessor_run_id
                or (known_zero_receipt.get("manifest") or {}).get("manifest_digest")
                   != source_execution_manifest_digest
            ):
                raise ValueError("known-zero continuation lineage conflicts")
            continuation_prior = {"run_id": predecessor_run_id}
            known_zero_authority = authority
            known_zero_continuation = True
        elif reportless_continuation_authority is not None:
            authority = reportless_continuation_authority
            validation = reportless_continuation_validation
            if (
                not isinstance(validation, Mapping)
                or set(validation) != {
                    "candidate", "approval", "operations_db",
                    "allowed_evidence_roots",
                }
                or not isinstance(validation["candidate"], Mapping)
                or not isinstance(validation["approval"], Mapping)
                or not validation["operations_db"]
                or not validation["allowed_evidence_roots"]
                or not isinstance(recovery_reconciliation_receipt, Mapping)
            ):
                raise ValueError(
                    "reportless continuation deep validation context is required"
                )
            from shared_platform.shopee_reportless_recovery_reconciliations import (
                validate_reportless_continuation_manifest,
            )
            from shared_platform.shopee_recovery_reconciliations import (
                ShopeeRecoveryReconciliationError,
            )

            try:
                authority = validate_reportless_continuation_manifest(
                    authority,
                    receipt=recovery_reconciliation_receipt,
                    run_store=run_store,
                    report_store=report_store,
                    snapshot=prepared.snapshot,
                    candidate=validation["candidate"],
                    approval=validation["approval"],
                    operations_db=validation["operations_db"],
                    allowed_evidence_roots=validation["allowed_evidence_roots"],
                )
            except (KeyError, TypeError, ValueError,
                    ShopeeRecoveryReconciliationError) as error:
                raise ValueError(
                    "reportless continuation authority deep validation failed"
                ) from error
            if (not isinstance(authority, Mapping)
                    or authority.get("schema_version")
                       != "shopee-reportless-recovery-execution-manifest/v1"
                    or authority.get("status") != "READY_ZERO_WRITE_PREFLIGHT"
                    or authority.get("authorized") is not True
                    or authority.get("manifest_digest") != recovery_manifest_digest
                    or authority.get("continuation") != recovery_continuation
                    or authority.get("offer_id") != prepared.offer_id
                    or authority.get("product_revision") != prepared.revision
                    or authority.get("plan_id") != prepared.plan_id
                    or authority.get("execution_snapshot_digest") != prepared.snapshot_digest
                    or authority.get("target_labels")
                       != list(prepared.target_labels_by_platform[platform])):
                raise ValueError("reportless continuation authority conflicts")
            predecessor_run_id = authority.get("direct_predecessor_run_id")
            source_receipt_digest = authority.get("source_receipt_digest")
            preflight_digest = authority.get("preflight_digest")
            evidence_relocation_digest = authority.get("evidence_relocation_digest")
            if (type(predecessor_run_id) is not str
                    or type(source_receipt_digest) is not str
                    or type(preflight_digest) is not str
                    or type(evidence_relocation_digest) is not str
                    or recovery_continuation.get("receipt_digest") != preflight_digest
                    or not isinstance(recovery_reconciliation_receipt, Mapping)
                    or not recovery_evidence_roots
                    or recovery_reconciliation_receipt.get("schema_version")
                       != "shopee-reportless-recovery-reconciliation/v1"
                    or recovery_reconciliation_receipt.get("result") != "REMAINING_DIFF"
                    or recovery_reconciliation_receipt.get("attempt_closed") is not True
                    or recovery_reconciliation_receipt.get("mutation_lock") is not False
                    or recovery_reconciliation_receipt.get("receipt_digest")
                       != source_receipt_digest
                    or (recovery_reconciliation_receipt.get("attempt") or {}).get("run_id")
                       != predecessor_run_id):
                raise ValueError("reportless continuation lineage conflicts")
            continuation_prior = {"run_id": predecessor_run_id}
            reportless_continuation = True
        else:
            continuation_prior = recovery_continuation.get("direct_predecessor")
            ancestor = recovery_continuation.get("original_ancestor")
            if not isinstance(continuation_prior, Mapping) or not isinstance(ancestor, Mapping):
                raise ValueError("recovery continuation lineage is invalid")
            receipt_digest = str(recovery_continuation.get("receipt_digest") or "")
            if not receipt_digest.startswith("sha256:") or len(receipt_digest) != 71:
                raise ValueError("recovery continuation receipt identity is invalid")
            if not isinstance(recovery_reconciliation_receipt, Mapping) or not recovery_evidence_roots:
                raise ValueError("recovery continuation receipt evidence is required")
            from shared_platform.shopee_recovery_coordination import continuation_identity
            rebuilt_continuation = continuation_identity(
                recovery_reconciliation_receipt,
                run_id=str(continuation_prior.get("run_id") or ""),
                report_id=str(continuation_prior.get("report_id") or ""),
                manifest_digest=str(continuation_prior.get("manifest_digest") or ""),
                allowed_evidence_roots=recovery_evidence_roots,
                target_scope=recovery_continuation["target_scope"],
                action_budgets=recovery_continuation.get("action_budgets") or {},
            )
            if any(recovery_continuation.get(key) != rebuilt_continuation.get(key) for key in (
                "schema_version", "receipt_digest", "target_scope", "exact_remaining_differences", "action_budgets"
            )):
                raise ValueError("recovery continuation conflicts with terminal receipt")
    elif (known_zero_continuation_authority is not None
          or known_zero_continuation_validation is not None
          or known_zero_receipt is not None
          or reportless_continuation_authority is not None
          or reportless_continuation_validation is not None):
        raise ValueError("continuation authority requires recovery continuation")
    reconciled_retry_receipt = None
    recovery_retry_receipt = None
    reconciliation_store = None
    retry_source = None
    retry_history_transparency = False

    def prior_report(prior):
        report = report_store.get_report_by_run(run_id=prior["run_id"])
        if report is not None and (
            report["offer_id"] != prior["offer_id"]
            or report["plan_id"] != prior["plan_id"]
            or report["snapshot"]["digest"] != prior["snapshot_digest"]
            or report["report_id"] != prior["report_id"]
        ):
            raise ValueError("prior publication report identity conflicts")
        return report

    def zero_write_run_identity(prior):
        keys = [
            "run_id", "report_id", "offer_id", "revision", "plan_id",
            "snapshot_digest", "platform_scope", "target_count", "execution_identity",
        ]
        if prior.get("request_identity", {}).get("kind") == "SHOPEE_RECOVERY":
            keys.append("request_identity")
        return {key: prior[key] for key in keys}

    def historical_target_labels(prior):
        previous = release_store.approved_publication_snapshot(
            offer_id=prior["offer_id"], snapshot_digest=prior["snapshot_digest"]
        )
        if previous is None:
            raise ValueError("prior publication snapshot requires reconciliation")
        previous = validate_approved_publication_snapshot(previous).payload()
        if (
            previous["offer_id"] != prior["offer_id"]
            or previous["product_revision"] != prior["revision"]
            or previous["plan_id"] != prior["plan_id"]
            or previous["snapshot_digest"] != prior["snapshot_digest"]
        ):
            raise ValueError("prior publication snapshot identity conflicts")
        selected = set(prior["platform_scope"])
        labels = tuple(
            row["target_label"]
            for row in previous["publication_targets"]
            if row["platform"].upper() in selected
        )
        if len(labels) != prior["target_count"]:
            raise ValueError("prior publication target count conflicts")
        return labels

    def reconciled_zero_write(prior, *, ordered_target_labels):
        """Accept only the append-only proof for a pre-running zero-write run.

        A newly approved successor is a fresh request, so it cannot name an old
        plan as ``retry_of_run_id``.  The old failed row must nevertheless stop
        blocking the same targets once its exact run identity and official
        absence readback have been reconciled.
        """
        nonlocal reconciliation_store
        from shared_platform.product_publication_run_reconciliations import (
            ProductPublicationRunReconciliationError,
            ProductPublicationRunReconciliationStore,
        )
        try:
            if reconciliation_store is None:
                reconciliation_store = ProductPublicationRunReconciliationStore(
                    run_store.path
                )
            receipt = reconciliation_store.get(run_id=prior["run_id"])
        except ProductPublicationRunReconciliationError as error:
            raise ValueError("prior publication reconciliation receipt is invalid") from error
        return bool(
            receipt is not None
            and receipt["run_identity"] == {
                key: prior[key]
                for key in (
                    "run_id", "report_id", "offer_id", "revision", "plan_id",
                    "snapshot_digest", "platform_scope", "target_count",
                    "execution_identity",
                )
            }
            and receipt["provider_request_attempted"] is False
            and receipt["external_write_count"] == 0
            and receipt["ordered_target_labels"] == list(ordered_target_labels)
        )

    def recovery_domain_predecessor(prior, report):
        """Admit only the immutable domain attempt named by the recovery manifest.

        The failed infrastructure run is the retry lineage source, while the
        earlier PARTIAL four-region report remains the domain predecessor that
        authorized this exact three-region recovery manifest.
        """
        if not isinstance(recovery_retry_receipt, Mapping) or not isinstance(report, Mapping):
            return False
        manifest = recovery_retry_receipt.get("old_manifest")
        if not isinstance(manifest, Mapping):
            return False
        report_digest = "sha256:" + hashlib.sha256(json.dumps(
            report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        expected_labels = list(prepared.target_labels_by_platform[platform])
        by_label = {
            row.get("target_label"): row.get("status")
            for row in report.get("targets", ()) if isinstance(row, Mapping)
        }
        return bool(
            prior["run_id"] == manifest.get("prior_run_id")
            and prior["state"] == "COMPLETED"
            and report_digest == manifest.get("prior_report_digest")
            and report.get("run_id") == prior["run_id"]
            and report.get("report_id") == prior["report_id"]
            and report.get("offer_id") == prior["offer_id"] == manifest.get("offer_id")
            and report.get("revision") == prior["revision"] == manifest.get("product_revision")
            and report.get("plan_id") == prior["plan_id"] == manifest.get("plan_id")
            and (report.get("snapshot") or {}).get("digest") == prior["snapshot_digest"]
               == manifest.get("execution_snapshot_digest")
            and report.get("status") == "PARTIAL"
            and all(by_label.get(label) == "FAILED" for label in expected_labels)
        )

    if retry_of_run_id is not None:
        retry_of_run_id = _text(retry_of_run_id, "retry_of_run_id")
        prior = run_store.get_run_by_id(run_id=retry_of_run_id)
        retry_source = prior
        if prior is None or any((
            prior["offer_id"] != prepared.offer_id,
            prior["plan_id"] != prepared.plan_id,
            prior["snapshot_digest"] != prepared.snapshot_digest,
            prior["platform_scope"] != [platform],
        )):
            raise ValueError("retry requires the exact approved request")
        from shared_platform.product_publication_run_reconciliations import (
            ProductPublicationRunReconciliationError,
            ProductPublicationRunReconciliationStore,
        )
        try:
            reconciliation_store = ProductPublicationRunReconciliationStore(run_store.path)
        except ProductPublicationRunReconciliationError as error:
            raise ValueError("retry reconciliation store is invalid") from error
        report = prior_report(prior)
        if prior["state"] == "FAILED" and report is None:
            if prior.get("request_identity", {}).get("kind") == "SHOPEE_RECOVERY":
                if platform != "SHOPEE" or recovery_manifest_digest is None \
                        or not isinstance(recovery_zero_write_receipt, Mapping) \
                        or not isinstance(recovery_zero_write_validation, Mapping) \
                        or set(recovery_zero_write_validation) != {
                            "allowed_evidence_roots", "snapshot", "candidate", "approval"}:
                    raise ValueError("Shopee recovery retry requires baseline-unchanged reconciliation")
                from shared_platform.shopee_recovery_run_reconciliations import (
                    validate_stored_recovery_run_receipt,
                )
                recovery_retry_receipt = validate_stored_recovery_run_receipt(
                    recovery_zero_write_receipt, run_store_path=run_store.path,
                    **recovery_zero_write_validation)
                if (recovery_retry_receipt["run_identity"] != {
                        key: prior[key] for key in (
                            "run_id", "report_id", "offer_id", "revision", "plan_id",
                            "snapshot_digest", "platform_scope", "target_count",
                            "execution_identity", "request_identity")}
                        or recovery_retry_receipt["recovery_manifest_digest"] != recovery_manifest_digest
                        or recovery_retry_receipt["ordered_target_labels"]
                           != list(prepared.target_labels_by_platform[platform])
                        or recovery_retry_receipt["deep_manifest_validated"] is not True
                        or recovery_retry_receipt["provider_request_attempted"] is not False
                        or recovery_retry_receipt["external_write_count"] != 0
                        or recovery_retry_receipt["baseline_unchanged"] is not True):
                    raise ValueError("Shopee recovery retry reconciliation conflicts")
                reconciled_retry_receipt = recovery_retry_receipt
                retry_history_transparency = True
            else:
                try:
                    reconciled_retry_receipt = reconciliation_store.get(run_id=retry_of_run_id)
                except ProductPublicationRunReconciliationError as error:
                    raise ValueError("retry reconciliation receipt is invalid") from error
                if (reconciled_retry_receipt is None
                        or reconciled_retry_receipt["run_identity"] != {
                            key: prior[key] for key in (
                                "run_id", "report_id", "offer_id", "revision", "plan_id",
                                "snapshot_digest", "platform_scope", "target_count", "execution_identity")}
                        or reconciled_retry_receipt["provider_request_attempted"] is not False
                        or reconciled_retry_receipt["external_write_count"] != 0
                        or reconciled_retry_receipt["ordered_target_labels"]
                           != list(prepared.target_labels_by_platform[platform])):
                    raise ValueError("retry requires an exact reconciled zero-write failure")
                retry_history_transparency = True
        else:
            prior_targets = {
                row.get("target_label"): row.get("status")
                for row in (report or {}).get("targets", ())
                if isinstance(row, Mapping)
            }
            if not targets.issubset(prior_targets):
                raise ValueError("retry target scope exceeds the prior failed run")
            if any(prior_targets[label] != "FAILED" for label in targets):
                raise ValueError("retry target scope contains a target that did not fail")
            if (
                prior["state"] != "COMPLETED"
                or report is None
                or not _retry_source_zero_dispatch_failed_report(
                    report,
                    ordered_labels=prepared.target_labels_by_platform[platform],
                    platform=platform,
                )
            ):
                raise ValueError("retry requires a verified failure before any external write")
            retry_history_transparency = True

    def guard(prior):
        if continuation_prior is not None and prior["run_id"] == continuation_prior.get("run_id"):
            if known_zero_continuation:
                report = prior_report(prior)
                receipt_identity = known_zero_receipt.get("run_identity") or {}
                if (
                    prior["offer_id"] != prepared.offer_id
                    or prior["revision"] != prepared.revision
                    or prior["plan_id"] != prepared.plan_id
                    or prior["snapshot_digest"] != prepared.snapshot_digest
                    or prior["platform_scope"] != [platform]
                    or prior["target_count"] != len(targets)
                    or prior["state"] != "COMPLETED"
                    or report is None
                    or receipt_identity.get("run_id") != prior["run_id"]
                    or receipt_identity.get("report_id") != prior["report_id"]
                    or receipt_identity.get("report_digest")
                       != hashlib.sha256(json.dumps(
                           report, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False,
                       ).encode("utf-8")).hexdigest()
                ):
                    raise ValueError("known-zero continuation predecessor conflicts")
                return
            if reportless_continuation:
                attempt = recovery_reconciliation_receipt["attempt"]
                authority_identity = recovery_reconciliation_receipt.get(
                    "authority_identity") or {}
                if (prior["offer_id"] != prepared.offer_id
                        or prior["revision"] != prepared.revision
                        or prior["plan_id"] != prepared.plan_id
                        or prior["snapshot_digest"] != prepared.snapshot_digest
                        or prior["platform_scope"] != [platform]
                        or prior["target_count"] != len(targets)
                        or prior["state"] != "FAILED"
                        or prior_report(prior) is not None
                        or attempt.get("run_id") != prior["run_id"]
                        or attempt.get("report_id") != prior["report_id"]
                        or authority_identity.get("offer_id") != prior["offer_id"]
                        or authority_identity.get("plan_id") != prior["plan_id"]
                        or authority_identity.get("snapshot_digest")
                           != prior["snapshot_digest"]
                        or authority_identity.get("target_labels")
                           != list(prepared.target_labels_by_platform[platform])):
                    raise ValueError("reportless continuation predecessor conflicts")
                return
            report = prior_report(prior)
            if (report is None
                    or report.get("report_id") != continuation_prior.get("report_id")
                    or "sha256:" + hashlib.sha256(json.dumps(
                        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")).hexdigest() != continuation_prior.get("report_digest")):
                raise ValueError("recovery continuation predecessor report conflicts")
            return
        if reportless_continuation or known_zero_continuation:
            attempt = continuation_lineage_receipt.get("attempt") or {}
            authority_identity = continuation_lineage_receipt.get(
                "authority_identity") or {}
            if prior["run_id"] == attempt.get("run_id"):
                if (
                    prior["offer_id"] != prepared.offer_id
                    or prior["revision"] != prepared.revision
                    or prior["plan_id"] != prepared.plan_id
                    or prior["snapshot_digest"] != prepared.snapshot_digest
                    or prior["platform_scope"] != [platform]
                    or prior["target_count"] != len(targets)
                    or prior["state"] != "FAILED"
                    or prior_report(prior) is not None
                    or attempt.get("report_id") != prior["report_id"]
                    or authority_identity.get("offer_id") != prior["offer_id"]
                    or authority_identity.get("plan_id") != prior["plan_id"]
                    or authority_identity.get("snapshot_digest")
                       != prior["snapshot_digest"]
                    or authority_identity.get("target_labels")
                       != list(prepared.target_labels_by_platform[platform])
                ):
                    raise ValueError("continuation reportless lineage conflicts")
                return
            reconciled_pre_running = continuation_lineage_receipt.get(
                "new_manifest", {}).get("pre_running_retry_run_id")
            reconciled_pre_running_receipt = continuation_lineage_receipt.get(
                "new_manifest", {}).get("pre_running_retry_receipt_digest")
            if prior["run_id"] == reconciled_pre_running:
                if (type(reconciled_pre_running_receipt) is not str
                        or not reconciled_pre_running_receipt.startswith("sha256:")):
                    raise ValueError("reportless continuation retry lineage conflicts")
                return
            if prior["run_id"] == (continuation_lineage_receipt.get(
                    "authority_identity") or {}).get("prior_run_id"):
                report = prior_report(prior)
                expected_digest = (continuation_lineage_receipt.get(
                    "authority_identity") or {}).get("prior_report_digest")
                if (report is None or "sha256:" + hashlib.sha256(json.dumps(
                        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")).hexdigest() != expected_digest):
                    raise ValueError("reportless continuation original lineage conflicts")
                return
        if retry_history_transparency and prior["run_id"] == retry_of_run_id:
            return
        report = prior_report(prior)
        if retry_history_transparency:
            if recovery_domain_predecessor(prior, report):
                return
            retry_manifest = (
                recovery_retry_receipt.get("old_manifest")
                if isinstance(recovery_retry_receipt, Mapping) else None
            )
            if (isinstance(retry_manifest, Mapping)
                    and prior["run_id"] == retry_manifest.get("prior_run_id")):
                raise ValueError("recovery domain predecessor report conflicts")
            exact_identity = (
                prior["offer_id"] == prepared.offer_id
                and prior["plan_id"] == prepared.plan_id
                and prior["snapshot_digest"] == prepared.snapshot_digest
                and prior["platform_scope"] == [platform]
                and prior["target_count"] == len(targets)
            )
            if exact_identity and prior["created_at"] >= retry_source["created_at"]:
                raise ValueError("retry requires the latest exact zero-write failed run")
            if exact_identity and prior["state"] == "FAILED" and report is None:
                try:
                    if prior.get("request_identity", {}).get("kind") == "SHOPEE_RECOVERY":
                        from shared_platform.shopee_recovery_run_reconciliations import (
                            ShopeeRecoveryRunReconciliationStore,
                        )
                        if not isinstance(recovery_zero_write_validation, Mapping):
                            raise ValueError("recovery history validation context is required")
                        receipt = ShopeeRecoveryRunReconciliationStore(run_store.path).get(
                            run_id=prior["run_id"], **recovery_zero_write_validation)
                    else:
                        receipt = reconciliation_store.get(run_id=prior["run_id"])
                except (ProductPublicationRunReconciliationError, RuntimeError) as error:
                    raise ValueError("prior retry reconciliation receipt is invalid") from error
                if (receipt is not None
                        and receipt["run_identity"] == zero_write_run_identity(prior)
                        and receipt["ordered_target_labels"]
                           == list(prepared.target_labels_by_platform[platform])
                        and receipt["provider_request_attempted"] is False
                        and receipt["external_write_count"] == 0):
                    return
            if (
                exact_identity
                and prior["state"] == "COMPLETED"
                and report is not None
                and _strict_zero_dispatch_failed_report(
                    report,
                    ordered_labels=prepared.target_labels_by_platform[platform],
                    platform=platform,
                )
            ):
                return
            if exact_identity:
                raise ValueError("prior retry history requires reconciliation")
            # Older/different approved snapshots retain the ordinary overlap
            # semantics below.  Retry transparency must not turn every
            # historical run for this offer into an exact-current blocker.
        if report is not None and prior["state"] == "COMPLETED":
            for row in report["targets"]:
                if row["target_label"] not in targets:
                    continue
                evidence = row.get("evidence") or {}
                if row["status"] == "PROCESSING" or evidence.get("outcome_unknown") is True:
                    raise ValueError("overlapping publication target requires reconciliation")
                if row["status"] != "PUBLISHED" and report["summary"]["evidence"]["external_write_count"] is None:
                    raise ValueError("overlapping publication target requires reconciliation")
            return
        historical_labels = historical_target_labels(prior)
        if not targets.intersection(historical_labels):
            return
        if (
            prior["state"] == "FAILED"
            and report is None
            and reconciled_zero_write(prior, ordered_target_labels=historical_labels)
        ):
            return
        raise ValueError("overlapping publication target requires reconciliation")

    # Snapshot digest binds approval, product, revision and all target facts.
    # An explicit retry key also binds the ordered target scope, current code
    # identity and the immutable evidence that admitted its exact source.
    source_evidence_digest = None
    if reconciled_retry_receipt is not None:
        source_evidence_digest = reconciled_retry_receipt["receipt_digest"]
    elif retry_of_run_id is not None:
        source_report = prior_report(retry_source)
        source_evidence_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                source_report,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    elif known_zero_continuation:
        source_evidence_digest = known_zero_authority["preflight_digest"]
    elif reportless_continuation:
        source_evidence_digest = reportless_continuation_authority["preflight_digest"]
    continuation_run_id = continuation_prior.get("run_id") if continuation_prior is not None else None
    if recovery_retry_receipt is not None:
        request_identity = {
            "kind": "SHOPEE_RECOVERY_RETRY",
            "authority_digest": recovery_manifest_digest,
            "reconciliation_receipt_digest": recovery_retry_receipt["receipt_digest"],
            "retry_of_run_id": retry_of_run_id,
        }
    elif known_zero_continuation:
        request_identity = {
            "kind": "SHOPEE_KNOWN_ZERO_CONTINUATION",
            "authority_digest": recovery_manifest_digest,
            "predecessor_run_id": continuation_run_id,
            "source_receipt_digest": known_zero_authority[
                "source_receipt_digest"
            ],
            "source_execution_manifest_digest": known_zero_authority[
                "source_execution_manifest_digest"
            ],
            "preflight_digest": known_zero_authority["preflight_digest"],
            "evidence_relocation_digest": known_zero_authority[
                "evidence_relocation_digest"
            ],
        }
    elif reportless_continuation:
        request_identity = {
            "kind": "SHOPEE_RECOVERY_CONTINUATION",
            "authority_digest": recovery_manifest_digest,
            "predecessor_run_id": continuation_run_id,
            "source_receipt_digest": reportless_continuation_authority[
                "source_receipt_digest"
            ],
            "preflight_digest": reportless_continuation_authority["preflight_digest"],
            "evidence_relocation_digest": reportless_continuation_authority[
                "evidence_relocation_digest"
            ],
        }
    elif recovery_manifest_digest is not None:
        request_identity = {
            "kind": "SHOPEE_RECOVERY",
            "authority_digest": recovery_manifest_digest,
        }
    else:
        # ProductPublicationRunStore canonicalizes an omitted identity to this
        # explicit STANDARD form.  Use the same value for replay comparison so
        # an ordinary reconciled retry remains idempotent on its second claim.
        request_identity = {"kind": "STANDARD", "authority_digest": None}
    identity = {
        "offer_id": prepared.offer_id,
        "plan_id": prepared.plan_id,
        "snapshot_digest": prepared.snapshot_digest,
        "platform": platform,
        "target_labels": list(prepared.target_labels_by_platform[platform]),
        "retry_of_run_id": retry_of_run_id or continuation_run_id,
        "source_evidence_digest": source_evidence_digest,
        "execution_identity": execution_identity if retry_of_run_id is not None else None,
        "recovery_manifest_digest": recovery_manifest_digest,
        "recovery_continuation": deepcopy(dict(recovery_continuation)) if recovery_continuation is not None else None,
        "reportless_continuation_authority": (
            deepcopy(dict(reportless_continuation_authority))
            if reportless_continuation_authority is not None else None
        ),
    }
    if known_zero_authority is not None:
        identity["known_zero_continuation_authority"] = deepcopy(
            dict(known_zero_authority)
        )
    key = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    run_id = f"product-center-{platform.lower()}-{key[:32]}"
    if retry_of_run_id is not None or reportless_continuation \
            or known_zero_continuation:
        existing = run_store.get_run_by_id(run_id=run_id)
        if existing is not None:
            same = (
                existing["offer_id"] == prepared.offer_id
                and existing["revision"] == prepared.revision
                and existing["plan_id"] == prepared.plan_id
                and existing["snapshot_digest"].removeprefix("sha256:")
                    == prepared.snapshot_digest.removeprefix("sha256:")
                and existing["platform_scope"] == [platform]
                and existing["target_count"]
                    == len(prepared.target_labels_by_platform[platform])
                and existing["execution_identity"] == execution_identity
                and existing.get("request_identity") == request_identity
            )
            if not same:
                raise ValueError("retry successor identity conflicts")
            from shared_platform.product_publication_runs import StoredPublicationRun

            return StoredPublicationRun(
                run_id=existing["run_id"],
                report_id=existing["report_id"],
                state=existing["state"],
                created=False,
            )
    return run_store.create_run(
        # Keep the existing path-sized identifier. Store verifies the full facts
        # and rejects any truncated-key collision rather than executing it.
        run_id=run_id, offer_id=prepared.offer_id,
        revision=prepared.revision, plan_id=prepared.plan_id,
        snapshot_digest=prepared.snapshot_digest, platform_scope=(platform,),
        target_count=len(targets), execution_identity=execution_identity,
        approved_request_guard=guard, retry_of_run_id=retry_of_run_id or continuation_run_id,
        request_identity=request_identity)


class ProductPublicationRunner:
    """Execute independent platform callables from one frozen v4 snapshot."""

    def __init__(
        self,
        *,
        release_store: ApprovedPublicationSnapshotStore,
        report_store: ProductPublicationReportStore,
        catalog_sink: object | None = None,
    ) -> None:
        self.release_store = release_store
        self.report_store = report_store
        self.catalog_sink = catalog_sink

    def run(
        self,
        *,
        run_id: str,
        offer_id: str,
        plan_id: str | None = None,
        snapshot_digest: str | None = None,
        platform_scope: Sequence[str],
        platform_executors: Mapping[str, PlatformExecutor],
        execution_identity: Mapping[str, object] | None = None,
        target_scope: Sequence[str] | None = None,
        release_candidate: Mapping[str, Any] | None = None,
        final_approval: Mapping[str, Any] | None = None,
        write_budget_overrides: Mapping[str, Mapping[str, Any]] | None = None,
        recovery_authorization: Mapping[str, Any] | None = None,
        recovery_retry_authorization: Mapping[str, Any] | None = None,
    ) -> PublicationRunReceipt:
        safe_offer_id = _offer_id(offer_id)
        report_id = publication_report_id(run_id)
        if (plan_id is None) == (snapshot_digest is None):
            raise ValueError("exactly one of plan_id or snapshot_digest is required")
        if plan_id is not None:
            plan_id = _text(plan_id, "plan_id")
        if snapshot_digest is not None:
            snapshot_digest = _text(snapshot_digest, "snapshot_digest", max_length=71)
        scope = _scope(platform_scope)
        executors = _executors(platform_executors, scope=scope)
        safe_execution_identity = _execution_identity(
            _LEGACY_EXECUTION_IDENTITY if execution_identity is None else execution_identity
        )
        if (release_candidate is None) != (final_approval is None):
            raise ValueError("final candidate and its approval must be supplied together")
        if write_budget_overrides is not None and (
            not isinstance(write_budget_overrides, Mapping)
            or set(write_budget_overrides) != set(scope)
            or any(not isinstance(value, Mapping) for value in write_budget_overrides.values())
        ):
            raise ValueError("write budget override is invalid")

        existing = self.report_store.get_report_by_run(run_id=run_id)
        if existing is not None:
            identity_matches = (
                existing["report_id"] == report_id
                and existing["offer_id"] == safe_offer_id
                and _existing_scope(existing) == scope
                and existing.get("execution_identity") == safe_execution_identity
            )
            if plan_id is not None:
                identity_matches = identity_matches and existing["plan_id"] == plan_id
            else:
                identity_matches = (
                    identity_matches
                    and existing["snapshot"]["digest"] == snapshot_digest
                )
            if not identity_matches:
                raise ProductPublicationRunConflictError(
                    "run identity already belongs to a different offer, snapshot, or platform scope"
                )
            if release_candidate is not None:
                from shared_platform.publication_autopilot import (
                    validate_final_approval_receipt, validate_release_candidate_for_execution,
                )
                replay_snapshot = self.release_store.approved_publication_snapshot(
                    offer_id=safe_offer_id,
                    plan_id=plan_id,
                )
                validate_release_candidate_for_execution(release_candidate,
                    snapshot=replay_snapshot,
                    platform_scope=scope, target_labels=tuple(row["target_label"] for row in existing["targets"]))
                approval = validate_final_approval_receipt(
                    final_approval, release_candidate, snapshot=replay_snapshot
                )
                if existing.get("release_authorization") != {
                    "candidate_digest":release_candidate.get("candidate_digest"),
                    "approval_digest":approval["approval_digest"]}:
                    raise ProductPublicationRunConflictError("run belongs to a different final candidate approval")
            elif "release_authorization" in existing:
                raise ProductPublicationRunConflictError("approved candidate identity is required to replay this run")
            if target_scope is not None and set(target_scope) != {row["target_label"] for row in existing["targets"]}:
                raise ProductPublicationRunConflictError("run belongs to a different target scope")
            if existing.get("recovery_authorization") != (
                deepcopy(dict(recovery_authorization)) if recovery_authorization is not None else None
            ):
                raise ProductPublicationRunConflictError("run belongs to a different recovery authorization")
            if existing.get("recovery_retry_authorization") != (
                _safe_recovery_retry_authorization(recovery_retry_authorization)
                if recovery_retry_authorization is not None else None
            ):
                raise ProductPublicationRunConflictError(
                    "run belongs to a different recovery retry authorization"
                )
            stored = self.report_store.store_report(
                {name: existing[name] for name in (
                    "schema_version", "report_id", "run_id", "offer_id", "revision", "plan_id", "snapshot", "execution_identity", "targets", "status", "summary",
                    "mutation_budgets", "release_authorization", "recovery_authorization",
                    "recovery_retry_authorization",
                    "continuation_evidence",
                    "target_observations",
                ) if name in existing}
            )
            return PublicationRunReceipt(
                report=existing, stored=stored, replayed=True
            )

        from shared_platform.registered_r3_legacy_publish_guard import require_legacy_publish_admission
        require_legacy_publish_admission(
            safe_offer_id, release_store=self.release_store, plan_id=plan_id,
        )
        prepared = prepare_product_publication_run(
            release_store=self.release_store,
            offer_id=safe_offer_id,
            plan_id=plan_id,
            snapshot_digest=snapshot_digest,
            platform_scope=scope,
            target_scope=target_scope,
        )
        snapshot = prepared.snapshot
        targets_by_platform = prepared.target_labels_by_platform
        if release_candidate is None:
            from shared_platform.publication_autopilot import resolve_persisted_execution_authority
            resolved = resolve_persisted_execution_authority(snapshot=snapshot, platform_scope=scope,
                target_labels=tuple(label for platform in scope for label in targets_by_platform[platform]),
                reports_root=self.report_store.reports_root.parent / "product-preparation")
            if resolved is not None:
                release_candidate, final_approval = resolved
        candidate = None
        authority = None
        if release_candidate is not None:
            from shared_platform.publication_autopilot import (
                validate_release_candidate_for_execution, validate_final_approval_receipt,
            )
            candidate = validate_release_candidate_for_execution(release_candidate,
                snapshot=snapshot,platform_scope=scope,
                target_labels=tuple(label for platform in scope for label in targets_by_platform[platform]))
            approval = validate_final_approval_receipt(
                final_approval, candidate, snapshot=snapshot
            )
            authority = {"candidate_digest":candidate["candidate_digest"],"approval_digest":approval["approval_digest"]}
        if write_budget_overrides is not None:
            if candidate is None:
                raise ValueError("write budget override requires approved candidate authority")
            for platform in scope:
                narrowed = write_budget_overrides[platform]
                approved = candidate["write_budget"][platform]
                selected_labels = list(targets_by_platform[platform])
                _restrictive_write_budget(narrowed, approved, selected_labels)
        if recovery_authorization is not None:
            if write_budget_overrides is None or not isinstance(recovery_authorization, Mapping):
                raise ValueError("recovery authorization requires a restrictive write budget")
            recovery_authorization = deepcopy(dict(recovery_authorization))
        if recovery_retry_authorization is not None:
            if recovery_authorization is None:
                raise ValueError(
                    "recovery retry authorization requires recovery authorization"
                )
            recovery_retry_authorization = _safe_recovery_retry_authorization(
                recovery_retry_authorization
            )
            if (
                recovery_retry_authorization["manifest_digest"]
                != recovery_authorization["manifest_digest"]
                or recovery_retry_authorization["failed_run_identity"]["offer_id"]
                != safe_offer_id
                or recovery_retry_authorization["failed_run_identity"]["revision"]
                != snapshot["product_revision"]
                or recovery_retry_authorization["failed_run_identity"]["plan_id"]
                != snapshot["plan_id"]
                or recovery_retry_authorization["failed_run_identity"]["snapshot_digest"]
                != snapshot["snapshot_digest"]
            ):
                raise ValueError("recovery retry authorization conflicts")

        outcomes: list[_PlatformOutcome] = []
        mutation_budgets = []
        for platform in scope:
            budget = (
                deepcopy(write_budget_overrides[platform])
                if write_budget_overrides is not None and platform in write_budget_overrides
                else deepcopy(candidate["write_budget"][platform]) if candidate is not None else None
            )
            if budget is not None:
                selected_labels = targets_by_platform[platform]
                budget["target_labels"] = list(selected_labels)
                budget["maximum_confirmed_writes"] = (
                    budget["shared_maximum"]
                    + budget["per_target_maximum"] * len(selected_labels)
                )
            ledger = PublicationWriteBudgetLedger(platform=platform,
                target_labels=targets_by_platform[platform],budget=budget) if budget is not None else None
            request = PublicationPlatformRequest(
                run_id=run_id,
                report_id=report_id,
                platform=platform,
                target_labels=targets_by_platform[platform],
                snapshot=deepcopy(snapshot),
                release_candidate=deepcopy(candidate),
                write_budget=budget,
                write_budget_ledger=ledger,
                catalog_sink=self.catalog_sink,
                checkpoint_root=self.report_store.reports_root,
            )
            # The direct CLI may spend time preparing after its first check.
            # Re-read the registry immediately before each platform boundary.
            require_legacy_publish_admission(
                safe_offer_id, release_store=self.release_store, plan_id=prepared.plan_id,
            )
            if self.catalog_sink is not None:
                self.catalog_sink.begin(request)
            try:
                raw_result = executors[platform](request)
            except Exception:
                # A zero-reservation approved ledger proves that execution did
                # not cross any registered mutation boundary. Preserve only a
                # stable, redacted preparation diagnosis.
                budget_snapshot = ledger.snapshot() if ledger is not None else None
                attempts = budget_snapshot.get("attempts") if isinstance(budget_snapshot, Mapping) else None
                reservations = budget_snapshot.get("reservations") if isinstance(budget_snapshot, Mapping) else None
                if (
                    isinstance(attempts, Mapping)
                    and attempts.get("total") == 0
                    and reservations == []
                ):
                    outcome = _pre_mutation_failed_outcome(
                        platform, targets_by_platform[platform]
                    )
                else:
                    outcome = _failed_outcome(platform, targets_by_platform[platform])
            else:
                try:
                    outcome = _validate_platform_result(
                        raw_result,
                        platform=platform,
                        expected_targets=targets_by_platform[platform],
                    )
                    if ledger is not None and outcome.external_write_count is not None:
                        ledger.assert_confirmed_writes_within_attempts(outcome.external_write_count)
                except Exception:
                    # A malformed receipt cannot prove the provider boundary.
                    outcome = _failed_outcome(platform, targets_by_platform[platform])
            outcomes.append(outcome)
            if self.catalog_sink is not None and platform not in {'SHOPEE','OZON','TIKTOK'}:
                from shared_platform.catalog_publication_sync import capture_readback
                capture_readback(request, [
                    {'authority':'UNAVAILABLE','verified':False,'target_label':target,
                     'model_sku':sku['model_sku'],
                     'reason':'official_variant_readback_not_connected'}
                    for target in request.target_labels for sku in snapshot['skus']
                ])
            if ledger is not None:
                mutation_budgets.append(ledger.snapshot())

        statuses = [outcome.summary["status"] for outcome in outcomes]
        known_write_counts = [
            outcome.external_write_count
            for outcome in outcomes
            if outcome.external_write_count is not None
        ]
        external_write_count = (
            sum(known_write_counts)
            if len(known_write_counts) == len(outcomes)
            else None
        )
        overall_status = _classify(statuses)
        report = {
            **({"mutation_budgets":mutation_budgets,"release_authorization":authority} if candidate is not None else {}),
            **({"recovery_authorization": recovery_authorization}
               if recovery_authorization is not None else {}),
            **({"recovery_retry_authorization": recovery_retry_authorization}
               if recovery_retry_authorization is not None else {}),
            "schema_version": INTERNAL_REPORT_SCHEMA_VERSION,
            "report_id": report_id,
            "run_id": run_id,
            "offer_id": safe_offer_id,
            "revision": snapshot["product_revision"],
            "plan_id": snapshot["plan_id"],
            "snapshot": {
                "schema_version": APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION,
                "digest": snapshot["snapshot_digest"],
            },
            "execution_identity": safe_execution_identity,
            "targets": [row for outcome in outcomes for row in outcome.targets],
            "status": overall_status,
            "summary": {
                "schema_version": SUMMARY_SCHEMA_VERSION,
                "overall_status": overall_status,
                "platforms": [outcome.summary for outcome in outcomes],
                "evidence": {
                    "snapshot_verified": True,
                    "dispatch_attempted": any(
                        outcome.dispatch_attempted for outcome in outcomes
                    ),
                    "readback_completed": all(
                        outcome.readback_completed for outcome in outcomes
                    ),
                    "external_write_count": external_write_count,
                },
                "requires_human_action": any(
                    outcome.requires_human_action for outcome in outcomes
                ),
            },
        }
        continuations = [outcome.continuation_evidence for outcome in outcomes
                         if outcome.continuation_evidence is not None]
        if continuations:
            if len(outcomes) != 1 or scope != ("TIKTOK",) or authority is None:
                raise ProductPublicationRunnerError("continuation needs exact persisted TikTok authority")
            continuation = continuations[0]
            lineage = continuation["lineage"]
            expected = {"offer_id": safe_offer_id, "revision": str(snapshot["product_revision"]),
                        "plan_id": snapshot["plan_id"], "snapshot_digest": snapshot["snapshot_digest"],
                        "candidate_digest": "sha256:" + authority["candidate_digest"].removeprefix("sha256:"),
                        "approval_digest": "sha256:" + authority["approval_digest"].removeprefix("sha256:")}
            if any(str(lineage.get(key)) != value for key, value in expected.items()):
                raise ProductPublicationRunnerError("continuation result approved lineage differs")
            report["continuation_evidence"] = continuation
        from shared_platform.publication_target_observations import collect_references
        observations = collect_references(report, getattr(self.report_store, 'target_observation_reader', None))
        if observations:
            report['target_observations'] = observations
        stored = self.report_store.store_report(report)
        persisted = self.report_store.get_report(
            report_id=report_id, offer_id=safe_offer_id
        )
        if persisted is None:  # pragma: no cover - store contract guard
            raise ProductPublicationRunnerError(
                "stored publication report could not be read back"
            )
        return PublicationRunReceipt(
            report=persisted, stored=stored, replayed=False
        )


__all__ = [
    "PLATFORM_RESULT_SCHEMA_VERSION",
    "ProductPublicationRunConflictError",
    "ProductPublicationRunner",
    "ProductPublicationRunnerError",
    "PreparedPublicationRun",
    "PublicationPlatformRequest",
    "PublicationRunReceipt",
    "prepare_product_publication_run",
]
