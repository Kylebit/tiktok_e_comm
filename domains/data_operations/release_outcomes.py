"""Replayable, redacted release outcome facts and offline evaluation.

The adapter in this module consumes caller-provided public receipts only.  It
does not import the release store, open a database, call an adapter, or perform
network I/O.  Raw marketplace identities and responses are deliberately
outside the contract.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


RECEIPT_SCHEMA_VERSION = "release-outcome-receipt/v1"
FACT_SCHEMA_VERSION = "release-outcome-fact/v1"
DATASET_SCHEMA_VERSION = "release-outcome-dataset/v1"
EVALUATION_SCHEMA_VERSION = "release-outcome-evaluation/v1"

UNKNOWN = "UNKNOWN"

OUTCOME_CLASSES = frozenset(
    {
        "SUCCESS",
        "FAILURE",
        "MANUAL_ACCEPTED",
        "RECONCILIATION_REQUIRED",
        "DUPLICATE_PREVENTED",
        UNKNOWN,
    }
)
DISPATCH_BOUNDARIES = frozenset(
    {"NOT_REACHED", "PRE_SUBMIT", "SUBMITTED", "ACCEPTED", UNKNOWN}
)
READBACK_STATUSES = frozenset({"VERIFIED", "FAILED", "UNAVAILABLE", UNKNOWN})
MANUAL_STATUSES = frozenset(
    {"ACCEPTED", "REJECTED", "PENDING", "NOT_REQUIRED", UNKNOWN}
)
RECONCILIATION_STATUSES = frozenset(
    {"REQUIRED", "RESOLVED", "NOT_REQUIRED", UNKNOWN}
)
ERROR_CATEGORIES = frozenset(
    {"AUTH", "INVENTORY", "CONTENT", "LOGISTICS", "OTHER", "NONE", UNKNOWN}
)

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_SAFE_DIMENSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_PROHIBITED_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "copy",
        "description",
        "external_id",
        "image",
        "image_id",
        "item_id",
        "model_id",
        "password",
        "plan_id",
        "product_id",
        "raw_copy",
        "raw_response",
        "response_body",
        "run_id",
        "secret",
        "seller_sku",
        "title",
        "token",
        "url",
    }
)


class ReleaseOutcomeContractError(ValueError):
    """The public receipt is unsafe or structurally incompatible."""


@dataclass(frozen=True)
class ReleaseOutcomeFact:
    """Versioned, JSON-ready result of one release target attempt."""

    fact_digest: str
    source_receipt_digest: str
    plan_identity_digest: str
    run_identity_digest: str
    target_identity_digest: str
    channel: str
    region: str
    adapter_version: str
    policy_version: str
    outcome_class: str
    dispatch_boundary: str
    external_write_count: int | None
    external_write_classes: tuple[str, ...]
    readback_status: str
    manual_status: str
    reconciliation_status: str
    error_category: str
    error_code: str | None
    error_type: str | None
    latency_ms: int | None
    attempt_count: int | None
    dispatch_count: int | None
    readback_count: int | None
    manual_review_count: int | None
    reconciliation_count: int | None
    duplicate_prevented: bool | None
    evidence_digests: tuple[str, ...]
    quality_issues: tuple[str, ...]
    schema_version: str = FACT_SCHEMA_VERSION

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fact_digest": self.fact_digest,
            "source_receipt_digest": self.source_receipt_digest,
            "identity": {
                "plan_digest": self.plan_identity_digest,
                "run_digest": self.run_identity_digest,
                "target_digest": self.target_identity_digest,
            },
            "channel": self.channel,
            "region": self.region,
            "versions": {
                "adapter": self.adapter_version,
                "policy": self.policy_version,
            },
            "outcome_class": self.outcome_class,
            "dispatch": {
                "boundary": self.dispatch_boundary,
                "external_write_count": self.external_write_count,
                "external_write_classes": list(self.external_write_classes),
            },
            "readback_status": self.readback_status,
            "manual_status": self.manual_status,
            "reconciliation_status": self.reconciliation_status,
            "error": {
                "category": self.error_category,
                "code": self.error_code,
                "type": self.error_type,
            },
            "latency_ms": self.latency_ms,
            "counts": {
                "attempts": self.attempt_count,
                "dispatches": self.dispatch_count,
                "readbacks": self.readback_count,
                "manual_reviews": self.manual_review_count,
                "reconciliations": self.reconciliation_count,
            },
            "duplicate_prevented": self.duplicate_prevented,
            "evidence_digests": list(self.evidence_digests),
            "quality_issues": list(self.quality_issues),
        }


def adapt_release_outcome_receipt(
    receipt: Mapping[str, object],
) -> ReleaseOutcomeFact:
    """Convert one redacted public receipt to an immutable outcome fact.

    Missing observational fields become ``UNKNOWN`` or ``None`` and produce a
    quality issue.  In particular, an absent write count is never interpreted
    as zero.  Missing identity digests, unsupported schemas, negative counts,
    or sensitive/raw fields reject the receipt.
    """

    if not isinstance(receipt, Mapping):
        raise ReleaseOutcomeContractError("receipt must be a mapping")
    source = dict(receipt)
    _reject_sensitive_content(source)
    if source.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ReleaseOutcomeContractError(
            f"unsupported receipt schema: {source.get('schema_version')!r}"
        )

    identity = _mapping(source.get("identity"), "identity")
    plan_digest = _required_digest(identity.get("plan_digest"), "identity.plan_digest")
    run_digest = _required_digest(identity.get("run_digest"), "identity.run_digest")
    target_digest = _required_digest(
        identity.get("target_digest"), "identity.target_digest"
    )
    issues: list[str] = []

    channel = _dimension(source.get("channel"), "channel", issues, lower=True)
    region = _dimension(source.get("region"), "region", issues, upper=True)
    versions = _optional_mapping(source.get("versions"))
    adapter_version = _version(versions.get("adapter"), "adapter_version", issues)
    policy_version = _version(versions.get("policy"), "policy_version", issues)

    outcome = _optional_mapping(source.get("outcome"))
    outcome_class = _status(
        outcome.get("class"),
        OUTCOME_CLASSES,
        "outcome_class",
        issues,
    )
    dispatch = _optional_mapping(source.get("dispatch"))
    dispatch_boundary = _status(
        dispatch.get("boundary"),
        DISPATCH_BOUNDARIES,
        "dispatch_boundary",
        issues,
    )
    external_write_count = _optional_count(
        dispatch.get("external_write_count"),
        "external_write_count",
        issues,
    )
    write_classes = _write_classes(
        dispatch.get("external_write_classes"),
        external_write_count,
        issues,
    )

    readback = _optional_mapping(source.get("readback"))
    readback_status = _status(
        readback.get("status"),
        READBACK_STATUSES,
        "readback_status",
        issues,
    )
    manual = _optional_mapping(source.get("manual"))
    manual_status = _status(
        manual.get("status"),
        MANUAL_STATUSES,
        "manual_status",
        issues,
    )
    reconciliation = _optional_mapping(source.get("reconciliation"))
    reconciliation_status = _status(
        reconciliation.get("status"),
        RECONCILIATION_STATUSES,
        "reconciliation_status",
        issues,
    )

    error = _optional_mapping(source.get("error"))
    error_category = _status(
        error.get("category"),
        ERROR_CATEGORIES,
        "error_category",
        issues,
    )
    error_code = _safe_optional_code(error.get("code"), "error.code")
    error_type = _safe_optional_code(error.get("type"), "error.type")

    latency_ms = _optional_count(source.get("latency_ms"), "latency_ms", issues)
    counts = _optional_mapping(source.get("counts"))
    attempt_count = _optional_count(counts.get("attempts"), "attempt_count", issues)
    dispatch_count = _optional_count(
        counts.get("dispatches"), "dispatch_count", issues
    )
    readback_count = _optional_count(
        counts.get("readbacks"), "readback_count", issues
    )
    manual_review_count = _optional_count(
        counts.get("manual_reviews"), "manual_review_count", issues
    )
    reconciliation_count = _optional_count(
        counts.get("reconciliations"), "reconciliation_count", issues
    )
    duplicate_prevented = source.get("duplicate_prevented")
    if duplicate_prevented is not None and not isinstance(duplicate_prevented, bool):
        raise ReleaseOutcomeContractError("duplicate_prevented must be boolean or null")
    if duplicate_prevented is None:
        issues.append("missing_duplicate_prevention_status")

    evidence_digests = _evidence_digests(
        source.get("evidence_digests"),
        readback.get("evidence_digest"),
        manual.get("evidence_digest"),
        reconciliation.get("evidence_digest"),
    )
    source_receipt_digest = _digest(source)
    fact_body = {
        "schema_version": FACT_SCHEMA_VERSION,
        "source_receipt_digest": source_receipt_digest,
        "identity": {
            "plan_digest": plan_digest,
            "run_digest": run_digest,
            "target_digest": target_digest,
        },
        "channel": channel,
        "region": region,
        "versions": {
            "adapter": adapter_version,
            "policy": policy_version,
        },
        "outcome_class": outcome_class,
        "dispatch": {
            "boundary": dispatch_boundary,
            "external_write_count": external_write_count,
            "external_write_classes": list(write_classes),
        },
        "readback_status": readback_status,
        "manual_status": manual_status,
        "reconciliation_status": reconciliation_status,
        "error": {
            "category": error_category,
            "code": error_code,
            "type": error_type,
        },
        "latency_ms": latency_ms,
        "counts": {
            "attempts": attempt_count,
            "dispatches": dispatch_count,
            "readbacks": readback_count,
            "manual_reviews": manual_review_count,
            "reconciliations": reconciliation_count,
        },
        "duplicate_prevented": duplicate_prevented,
        "evidence_digests": list(evidence_digests),
        "quality_issues": sorted(set(issues)),
    }
    return ReleaseOutcomeFact(
        fact_digest=_digest(fact_body),
        source_receipt_digest=source_receipt_digest,
        plan_identity_digest=plan_digest,
        run_identity_digest=run_digest,
        target_identity_digest=target_digest,
        channel=channel,
        region=region,
        adapter_version=adapter_version,
        policy_version=policy_version,
        outcome_class=outcome_class,
        dispatch_boundary=dispatch_boundary,
        external_write_count=external_write_count,
        external_write_classes=write_classes,
        readback_status=readback_status,
        manual_status=manual_status,
        reconciliation_status=reconciliation_status,
        error_category=error_category,
        error_code=error_code,
        error_type=error_type,
        latency_ms=latency_ms,
        attempt_count=attempt_count,
        dispatch_count=dispatch_count,
        readback_count=readback_count,
        manual_review_count=manual_review_count,
        reconciliation_count=reconciliation_count,
        duplicate_prevented=duplicate_prevented,
        evidence_digests=evidence_digests,
        quality_issues=tuple(sorted(set(issues))),
    )


def adapt_release_outcome_receipts(
    receipts: Iterable[Mapping[str, object]],
) -> tuple[ReleaseOutcomeFact, ...]:
    """Adapt receipts without depending on input ordering."""

    facts = [adapt_release_outcome_receipt(receipt) for receipt in receipts]
    return tuple(sorted(facts, key=lambda fact: fact.fact_digest))


def release_outcome_dataset(
    facts: Iterable[ReleaseOutcomeFact],
) -> dict[str, Any]:
    """Return a stable JSON payload for dashboards or an offline nightly job."""

    payloads = sorted(
        (_validated_fact(fact).payload() for fact in facts),
        key=lambda payload: payload["fact_digest"],
    )
    snapshot_digest = _digest(payloads)
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "fact_schema_version": FACT_SCHEMA_VERSION,
        "snapshot_digest": snapshot_digest,
        "fact_count": len(payloads),
        "facts": payloads,
    }


def evaluate_release_outcomes(
    facts: Iterable[ReleaseOutcomeFact],
    *,
    group_by: Sequence[str] = ("channel", "region", "policy_version"),
) -> dict[str, Any]:
    """Evaluate release results without changing production policy."""

    allowed_dimensions = {"channel", "region", "policy_version"}
    dimensions = tuple(group_by)
    if not dimensions or len(set(dimensions)) != len(dimensions):
        raise ValueError("group_by must contain unique dimensions")
    if set(dimensions) - allowed_dimensions:
        raise ValueError("group_by supports channel, region, and policy_version only")
    materialized = tuple(_validated_fact(fact) for fact in facts)
    dataset = release_outcome_dataset(materialized)
    grouped: dict[tuple[str, ...], list[ReleaseOutcomeFact]] = {}
    for fact in materialized:
        key = tuple(str(getattr(fact, name)) for name in dimensions)
        grouped.setdefault(key, []).append(fact)
    groups = [
        {
            "dimensions": dict(zip(dimensions, key)),
            "metrics": _metrics(rows),
        }
        for key, rows in sorted(grouped.items())
    ]
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "fact_schema_version": FACT_SCHEMA_VERSION,
        "input_snapshot_digest": dataset["snapshot_digest"],
        "group_by": list(dimensions),
        "overall": _metrics(materialized),
        "groups": groups,
    }


def _metrics(facts: Sequence[ReleaseOutcomeFact]) -> dict[str, Any]:
    total = len(facts)
    successes = sum(
        fact.outcome_class in {"SUCCESS", "MANUAL_ACCEPTED"} for fact in facts
    )
    official_readbacks = sum(
        fact.readback_status == "VERIFIED" for fact in facts
    )
    manual_decisions = sum(
        fact.manual_status in {"ACCEPTED", "REJECTED"} for fact in facts
    )
    manual_acceptances = sum(fact.manual_status == "ACCEPTED" for fact in facts)
    reconciliation_known = sum(
        fact.reconciliation_status != UNKNOWN for fact in facts
    )
    reconciliation_required = sum(
        fact.reconciliation_status == "REQUIRED" for fact in facts
    )
    duplicate_known = sum(fact.duplicate_prevented is not None for fact in facts)
    duplicate_preventions = sum(fact.duplicate_prevented is True for fact in facts)
    known_write_facts = [fact for fact in facts if fact.external_write_count is not None]
    error_distribution = Counter(
        fact.error_category
        for fact in facts
        if fact.error_category not in {"NONE", UNKNOWN}
    )
    outcome_distribution = Counter(fact.outcome_class for fact in facts)
    return {
        "fact_count": total,
        "success_count": successes,
        "success_rate": _rate(successes, total),
        "official_readback_verified_count": official_readbacks,
        "official_readback_rate": _rate(official_readbacks, total),
        "manual_acceptance_count": manual_acceptances,
        "manual_decision_count": manual_decisions,
        "manual_acceptance_rate": _rate(manual_acceptances, manual_decisions),
        "reconciliation_required_count": reconciliation_required,
        "reconciliation_known_count": reconciliation_known,
        "reconciliation_rate": _rate(
            reconciliation_required, reconciliation_known
        ),
        "duplicate_prevention_count": duplicate_preventions,
        "duplicate_prevention_known_count": duplicate_known,
        "duplicate_prevention_rate": _rate(
            duplicate_preventions, duplicate_known
        ),
        "external_write_total": sum(
            fact.external_write_count or 0 for fact in known_write_facts
        ),
        "unknown_external_write_fact_count": total - len(known_write_facts),
        "quality_issue_count": sum(len(fact.quality_issues) for fact in facts),
        "outcome_distribution": {
            key: outcome_distribution.get(key, 0)
            for key in sorted(OUTCOME_CLASSES)
        },
        "error_distribution": {
            key: error_distribution.get(key, 0)
            for key in ("AUTH", "INVENTORY", "CONTENT", "LOGISTICS", "OTHER")
        },
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _validated_fact(fact: ReleaseOutcomeFact) -> ReleaseOutcomeFact:
    if not isinstance(fact, ReleaseOutcomeFact):
        raise TypeError("facts must be ReleaseOutcomeFact instances")
    if fact.schema_version != FACT_SCHEMA_VERSION:
        raise ReleaseOutcomeContractError("unsupported fact schema")
    expected = dict(fact.payload())
    supplied_digest = expected.pop("fact_digest")
    if _digest(expected) != supplied_digest:
        raise ReleaseOutcomeContractError("fact digest does not match payload")
    return fact


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ReleaseOutcomeContractError(f"{field} must be a mapping")
    return dict(value)


def _optional_mapping(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ReleaseOutcomeContractError("receipt section must be a mapping")
    return dict(value)


def _required_digest(value: object, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise ReleaseOutcomeContractError(f"{field} must be a sha256 digest")
    return text


def _dimension(
    value: object,
    field: str,
    issues: list[str],
    *,
    lower: bool = False,
    upper: bool = False,
) -> str:
    text = str(value or "").strip()
    if not text:
        issues.append(f"missing_{field}")
        return UNKNOWN
    if not _SAFE_DIMENSION_RE.fullmatch(text):
        raise ReleaseOutcomeContractError(f"{field} is not a safe public dimension")
    if lower:
        return text.lower()
    if upper:
        return text.upper()
    return text


def _version(value: object, field: str, issues: list[str]) -> str:
    text = str(value or "").strip()
    if not text:
        issues.append(f"missing_{field}")
        return UNKNOWN
    if not _SAFE_DIMENSION_RE.fullmatch(text):
        raise ReleaseOutcomeContractError(f"{field} is invalid")
    return text


def _status(
    value: object,
    allowed: frozenset[str],
    field: str,
    issues: list[str],
) -> str:
    text = str(value or "").strip().upper()
    if not text:
        issues.append(f"missing_{field}")
        return UNKNOWN
    if text not in allowed:
        issues.append(f"unknown_{field}")
        return UNKNOWN
    return text


def _optional_count(
    value: object,
    field: str,
    issues: list[str],
) -> int | None:
    if value is None:
        issues.append(f"missing_{field}")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReleaseOutcomeContractError(f"{field} must be a non-negative integer")
    return value


def _write_classes(
    value: object,
    count: int | None,
    issues: list[str],
) -> tuple[str, ...]:
    if value is None:
        issues.append("missing_external_write_classes")
        return () if count == 0 else (UNKNOWN,)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReleaseOutcomeContractError("external_write_classes must be a list")
    result: list[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if not text or not _SAFE_DIMENSION_RE.fullmatch(text):
            raise ReleaseOutcomeContractError("external write class is invalid")
        result.append(text)
    unique = tuple(sorted(set(result)))
    if count == 0 and unique:
        raise ReleaseOutcomeContractError(
            "zero external writes cannot include write classes"
        )
    if count is not None and count > 0 and not unique:
        raise ReleaseOutcomeContractError(
            "positive external writes require at least one write class"
        )
    return unique


def _safe_optional_code(value: object, field: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not _SAFE_CODE_RE.fullmatch(text):
        raise ReleaseOutcomeContractError(f"{field} must be a redacted code")
    return text


def _evidence_digests(
    values: object,
    *additional: object,
) -> tuple[str, ...]:
    if values is None:
        candidates: list[object] = []
    elif isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ReleaseOutcomeContractError("evidence_digests must be a list")
    else:
        candidates = list(values)
    candidates.extend(value for value in additional if value is not None)
    return tuple(
        sorted(
            {
                _required_digest(value, "evidence_digest")
                for value in candidates
            }
        )
    )


def _reject_sensitive_content(value: object, path: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).strip().lower()
            if (
                lowered in _PROHIBITED_KEYS
                or lowered.endswith("_url")
                or lowered.startswith("image_")
            ):
                raise ReleaseOutcomeContractError(
                    f"prohibited raw/sensitive field at {path}.{key}"
                )
            _reject_sensitive_content(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _reject_sensitive_content(child, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if "://" in value or "bearer " in lowered or lowered.startswith("sk-"):
            raise ReleaseOutcomeContractError(
                f"prohibited raw/sensitive value at {path}"
            )


def _digest(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
