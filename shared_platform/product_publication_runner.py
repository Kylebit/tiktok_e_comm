"""Server-owned orchestration for one approved publication snapshot.

The runner is deliberately narrower than a channel adapter.  It loads exactly
one durable ``approved-publication-snapshot/v4``, passes detached copies to
injected per-platform executors, and stores only a redacted outcome report.  It
does not read mutable product/dashboard/source/content state and it contains no
network or provider client.
"""

from __future__ import annotations

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
    publication_report_id,
)


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
    {"target_label", "status", "stage", "provider_code", "provider_reason", "request_attempted", "outcome_unknown", "external_write_count"}
)
_EXECUTION_IDENTITY_FIELDS = frozenset({"skill_digest", "git_commit", "code_digest"})


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


PlatformExecutor = Callable[[PublicationPlatformRequest], Mapping[str, Any]]


def _execution_identity(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("execution_identity must be a mapping")
    _exact_fields(value, _EXECUTION_IDENTITY_FIELDS, "execution_identity")
    result = {name: _text(value[name], name, max_length=64) for name in _EXECUTION_IDENTITY_FIELDS}
    if len(result["git_commit"]) != 40 or any(c not in "0123456789abcdef" for c in result["git_commit"]):
        raise ValueError("git_commit is invalid")
    for name in ("skill_digest", "code_digest"):
        if len(result[name]) != 64 or any(c not in "0123456789abcdef" for c in result[name]):
            raise ValueError(f"{name} is invalid")
    return result


def _target_evidence(value: object, *, label: str, status: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("target evidence must be a mapping or null")
    _exact_fields(value, _TARGET_EVIDENCE_FIELDS, "target evidence")
    if value["target_label"] != label or value["status"] != status:
        raise ValueError("target evidence identity conflicts")
    stage = _text(value["stage"], "target evidence stage", max_length=32)
    code = _text(value["provider_code"], "provider_code", max_length=80)
    reason = _text(value["provider_reason"], "provider_reason", max_length=240)
    if not code.isascii() or any(not (c.isalnum() or c in "_-") for c in code):
        raise ValueError("provider_code is unsafe")
    if "http://" in reason.casefold() or "https://" in reason.casefold():
        raise ValueError("provider_reason contains a URL")
    attempted = value["request_attempted"]
    unknown = value["outcome_unknown"]
    if type(attempted) is not bool or type(unknown) is not bool:
        raise TypeError("target evidence flags must be boolean")
    count = _nonnegative_int_or_none(value["external_write_count"], "target evidence external_write_count")
    return {"target_label": label, "status": status, "stage": stage, "provider_code": code, "provider_reason": reason, "request_attempted": attempted, "outcome_unknown": unknown, "external_write_count": count}


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
    _exact_fields(value, _RESULT_FIELDS, "platform result")
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
    return PreparedPublicationRun(
        offer_id=safe_offer_id,
        revision=snapshot["product_revision"],
        plan_id=snapshot["plan_id"],
        snapshot_digest=snapshot["snapshot_digest"],
        platform_scope=scope,
        target_labels_by_platform=targets_by_platform,
        snapshot=deepcopy(snapshot),
    )


class ProductPublicationRunner:
    """Execute independent platform callables from one frozen v4 snapshot."""

    def __init__(
        self,
        *,
        release_store: ApprovedPublicationSnapshotStore,
        report_store: ProductPublicationReportStore,
    ) -> None:
        self.release_store = release_store
        self.report_store = report_store

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
            stored = self.report_store.store_report(
                {name: existing[name] for name in (
                    "schema_version", "report_id", "run_id", "offer_id", "revision", "plan_id", "snapshot", "execution_identity", "targets", "status", "summary",
                )}
            )
            return PublicationRunReceipt(
                report=existing, stored=stored, replayed=True
            )

        prepared = prepare_product_publication_run(
            release_store=self.release_store,
            offer_id=safe_offer_id,
            plan_id=plan_id,
            snapshot_digest=snapshot_digest,
            platform_scope=scope,
        )
        snapshot = prepared.snapshot
        targets_by_platform = prepared.target_labels_by_platform

        outcomes: list[_PlatformOutcome] = []
        for platform in scope:
            request = PublicationPlatformRequest(
                run_id=run_id,
                report_id=report_id,
                platform=platform,
                target_labels=targets_by_platform[platform],
                snapshot=deepcopy(snapshot),
            )
            try:
                raw_result = executors[platform](request)
                outcome = _validate_platform_result(
                    raw_result,
                    platform=platform,
                    expected_targets=targets_by_platform[platform],
                )
            except Exception:
                # Provider/client details are intentionally neither persisted
                # nor surfaced at this redacted server boundary.
                outcome = _failed_outcome(platform, targets_by_platform[platform])
            outcomes.append(outcome)

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
