"""Fail-closed offline optimization candidates from release outcome facts.

This module consumes only the stable JSON dataset and evaluation emitted by
``release_outcomes``.  It never reads ReleaseStore, a database, credentials,
or an API, and its output is advisory only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from domains.data_operations.release_outcomes import (
    DATASET_SCHEMA_VERSION,
    ERROR_CATEGORIES,
    EVALUATION_SCHEMA_VERSION,
    FACT_SCHEMA_VERSION,
    OUTCOME_CLASSES,
    UNKNOWN,
)


CANDIDATE_SCHEMA_VERSION = "release-optimization-candidate/v1"
ARTIFACT_SCHEMA_VERSION = "release-optimization-artifact/v1"

ACTION_CODES = frozenset(
    {
        "COLLECT_MORE_EVIDENCE",
        "REVIEW_AUTH",
        "REVIEW_INVENTORY",
        "REVIEW_CONTENT",
        "REVIEW_LOGISTICS",
        "REVIEW_POLICY",
    }
)
_ERROR_ACTIONS = {
    "AUTH": "REVIEW_AUTH",
    "INVENTORY": "REVIEW_INVENTORY",
    "CONTENT": "REVIEW_CONTENT",
    "LOGISTICS": "REVIEW_LOGISTICS",
}
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DIMENSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_EXPECTED_GROUPING = ("channel", "region", "policy_version")


@dataclass(frozen=True)
class OptimizationThresholds:
    """Explicit evidence thresholds; changing them changes the artifact."""

    min_sample_count: int = 5
    max_unknown_write_rate: float = 0.10
    min_quality_clear_coverage: float = 0.80
    min_outcome_known_coverage: float = 0.80
    medium_confidence_sample_count: int = 10
    high_confidence_sample_count: int = 30

    def __post_init__(self) -> None:
        counts = (
            self.min_sample_count,
            self.medium_confidence_sample_count,
            self.high_confidence_sample_count,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            for value in counts
        ):
            raise ValueError("sample thresholds must be positive integers")
        rates = (
            self.max_unknown_write_rate,
            self.min_quality_clear_coverage,
            self.min_outcome_known_coverage,
        )
        if any(
            isinstance(value, bool) or not 0 <= float(value) <= 1
            for value in rates
        ):
            raise ValueError("coverage thresholds must be between zero and one")
        if self.medium_confidence_sample_count < self.min_sample_count:
            raise ValueError("medium confidence sample threshold cannot be lower")
        if self.high_confidence_sample_count < self.medium_confidence_sample_count:
            raise ValueError("high confidence sample threshold cannot be lower")

    def payload(self) -> dict[str, Any]:
        return {
            "min_sample_count": self.min_sample_count,
            "max_unknown_write_rate": self.max_unknown_write_rate,
            "min_quality_clear_coverage": self.min_quality_clear_coverage,
            "min_outcome_known_coverage": self.min_outcome_known_coverage,
            "medium_confidence_sample_count": self.medium_confidence_sample_count,
            "high_confidence_sample_count": self.high_confidence_sample_count,
        }


class _InputDrift(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_release_optimization_candidates(
    dataset: Mapping[str, object],
    evaluation: Mapping[str, object],
    *,
    thresholds: OptimizationThresholds | None = None,
) -> dict[str, Any]:
    """Build a deterministic, human-only optimization artifact.

    Invalid schema, digest drift, metric drift, or malformed inputs produce a
    blocked ``COLLECT_MORE_EVIDENCE`` candidate.  No output field authorizes
    dispatch, retry, or automatic policy mutation.
    """

    limits = thresholds or OptimizationThresholds()
    dataset_input_digest = _input_digest(dataset)
    evaluation_input_digest = _input_digest(evaluation)
    try:
        normalized_dataset, facts = _validated_dataset(dataset)
        if not facts:
            raise _InputDrift("empty_dataset")
        expected_evaluation = _validated_evaluation(
            evaluation,
            facts=facts,
            dataset_snapshot_digest=normalized_dataset["snapshot_digest"],
        )
    except _InputDrift as error:
        return _blocked_artifact(
            blocker=error.code,
            thresholds=limits,
            dataset_input_digest=dataset_input_digest,
            evaluation_input_digest=evaluation_input_digest,
        )
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return _blocked_artifact(
            blocker="malformed_input",
            thresholds=limits,
            dataset_input_digest=dataset_input_digest,
            evaluation_input_digest=evaluation_input_digest,
        )

    dataset_digest = _digest(normalized_dataset)
    evaluation_digest = _digest(expected_evaluation)
    all_facts = tuple(facts)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for fact in all_facts:
        key = (
            str(fact["channel"]),
            str(fact["region"]),
            str((fact["versions"] or {})["policy"]),
        )
        grouped.setdefault(key, []).append(fact)
    candidates = [
        _candidate(
            key=key,
            facts=tuple(rows),
            all_facts=all_facts,
            thresholds=limits,
            dataset_snapshot_digest=str(normalized_dataset["snapshot_digest"]),
            dataset_digest=dataset_digest,
            evaluation_digest=evaluation_digest,
        )
        for key, rows in sorted(grouped.items())
    ]
    artifact_body = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "status": "READY",
        "thresholds": limits.payload(),
        "evidence": {
            "dataset_snapshot_digest": normalized_dataset["snapshot_digest"],
            "dataset_digest": dataset_digest,
            "evaluation_digest": evaluation_digest,
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    return {**artifact_body, "artifact_digest": _digest(artifact_body)}


def _candidate(
    *,
    key: tuple[str, str, str],
    facts: Sequence[dict[str, Any]],
    all_facts: Sequence[dict[str, Any]],
    thresholds: OptimizationThresholds,
    dataset_snapshot_digest: str,
    dataset_digest: str,
    evaluation_digest: str,
) -> dict[str, Any]:
    sample_count = len(facts)
    known_outcomes = sum(fact["outcome_class"] != UNKNOWN for fact in facts)
    known_writes = sum(
        (fact["dispatch"] or {}).get("external_write_count") is not None
        for fact in facts
    )
    known_readbacks = sum(fact["readback_status"] != UNKNOWN for fact in facts)
    quality_clear = sum(not fact["quality_issues"] for fact in facts)
    outcome_coverage = _rate(known_outcomes, sample_count)
    write_coverage = _rate(known_writes, sample_count)
    readback_coverage = _rate(known_readbacks, sample_count)
    quality_coverage = _rate(quality_clear, sample_count)
    unknown_write_rate = _rate(sample_count - known_writes, sample_count)
    metrics = _metrics(facts)

    blockers: list[str] = []
    if sample_count < thresholds.min_sample_count:
        blockers.append("sample_count_below_minimum")
    if (
        unknown_write_rate is None
        or unknown_write_rate > thresholds.max_unknown_write_rate
    ):
        blockers.append("unknown_write_rate_above_maximum")
    if (
        quality_coverage is None
        or quality_coverage < thresholds.min_quality_clear_coverage
    ):
        blockers.append("quality_clear_coverage_below_minimum")
    if (
        outcome_coverage is None
        or outcome_coverage < thresholds.min_outcome_known_coverage
    ):
        blockers.append("outcome_known_coverage_below_minimum")

    failure_trends = _failure_trends(facts, all_facts)
    action = (
        "COLLECT_MORE_EVIDENCE"
        if blockers
        else _review_action(failure_trends)
    )
    confidence = _confidence(
        sample_count=sample_count,
        minimum_coverage=min(
            value
            for value in (
                outcome_coverage,
                write_coverage,
                readback_coverage,
                quality_coverage,
            )
            if value is not None
        ),
        blockers=blockers,
        thresholds=thresholds,
    )
    candidate_body = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "dimensions": {
            "channel": key[0],
            "region": key[1],
            "policy_version": key[2],
        },
        "sample_count": sample_count,
        "coverage": {
            "outcome_known_count": known_outcomes,
            "outcome_known_rate": outcome_coverage,
            "external_write_known_count": known_writes,
            "external_write_known_rate": write_coverage,
            "readback_known_count": known_readbacks,
            "readback_known_rate": readback_coverage,
            "quality_clear_count": quality_clear,
            "quality_clear_rate": quality_coverage,
        },
        "quality_blockers": sorted(blockers),
        "failure_category_trends": failure_trends,
        "rates": {
            "manual_acceptance_rate": metrics["manual_acceptance_rate"],
            "reconciliation_rate": metrics["reconciliation_rate"],
            "external_write_unknown_rate": unknown_write_rate,
        },
        "recommended_action_code": action,
        "confidence_band": confidence,
        "requires_human_approval": True,
        "evidence": {
            "dataset_snapshot_digest": dataset_snapshot_digest,
            "dataset_digest": dataset_digest,
            "evaluation_digest": evaluation_digest,
            "group_metrics_digest": _digest(metrics),
        },
    }
    return {**candidate_body, "candidate_digest": _digest(candidate_body)}


def _blocked_artifact(
    *,
    blocker: str,
    thresholds: OptimizationThresholds,
    dataset_input_digest: str,
    evaluation_input_digest: str,
) -> dict[str, Any]:
    candidate_body = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "dimensions": {
            "channel": UNKNOWN,
            "region": UNKNOWN,
            "policy_version": UNKNOWN,
        },
        "sample_count": 0,
        "coverage": {
            "outcome_known_count": 0,
            "outcome_known_rate": None,
            "external_write_known_count": 0,
            "external_write_known_rate": None,
            "readback_known_count": 0,
            "readback_known_rate": None,
            "quality_clear_count": 0,
            "quality_clear_rate": None,
        },
        "quality_blockers": [blocker],
        "failure_category_trends": [],
        "rates": {
            "manual_acceptance_rate": None,
            "reconciliation_rate": None,
            "external_write_unknown_rate": None,
        },
        "recommended_action_code": "COLLECT_MORE_EVIDENCE",
        "confidence_band": "LOW",
        "requires_human_approval": True,
        "evidence": {
            "dataset_snapshot_digest": None,
            "dataset_digest": dataset_input_digest,
            "evaluation_digest": evaluation_input_digest,
            "group_metrics_digest": None,
        },
    }
    candidate = {
        **candidate_body,
        "candidate_digest": _digest(candidate_body),
    }
    artifact_body = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "status": "BLOCKED",
        "thresholds": thresholds.payload(),
        "evidence": {
            "dataset_snapshot_digest": None,
            "dataset_digest": dataset_input_digest,
            "evaluation_digest": evaluation_input_digest,
        },
        "candidate_count": 1,
        "candidates": [candidate],
    }
    return {**artifact_body, "artifact_digest": _digest(artifact_body)}


def _validated_dataset(
    dataset: Mapping[str, object],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    if not isinstance(dataset, Mapping):
        raise _InputDrift("malformed_dataset")
    if dataset.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise _InputDrift("dataset_schema_drift")
    if dataset.get("fact_schema_version") != FACT_SCHEMA_VERSION:
        raise _InputDrift("fact_schema_drift")
    raw_facts = dataset.get("facts")
    if isinstance(raw_facts, (str, bytes)) or not isinstance(raw_facts, Sequence):
        raise _InputDrift("malformed_dataset_facts")
    facts: list[dict[str, Any]] = []
    for raw in raw_facts:
        if not isinstance(raw, Mapping):
            raise _InputDrift("malformed_fact")
        fact = json.loads(json.dumps(raw))
        if fact.get("schema_version") != FACT_SCHEMA_VERSION:
            raise _InputDrift("fact_schema_drift")
        supplied_digest = str(fact.get("fact_digest") or "")
        if not _DIGEST_RE.fullmatch(supplied_digest):
            raise _InputDrift("fact_digest_drift")
        fact_body = dict(fact)
        fact_body.pop("fact_digest", None)
        if _digest(fact_body) != supplied_digest:
            raise _InputDrift("fact_digest_drift")
        _validate_public_fact(fact)
        facts.append(fact)
    facts.sort(key=lambda fact: fact["fact_digest"])
    if dataset.get("fact_count") != len(facts):
        raise _InputDrift("dataset_count_drift")
    snapshot_digest = str(dataset.get("snapshot_digest") or "")
    if not _DIGEST_RE.fullmatch(snapshot_digest) or _digest(facts) != snapshot_digest:
        raise _InputDrift("dataset_digest_drift")
    normalized = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "fact_schema_version": FACT_SCHEMA_VERSION,
        "snapshot_digest": snapshot_digest,
        "fact_count": len(facts),
        "facts": facts,
    }
    return normalized, tuple(facts)


def _validate_public_fact(fact: Mapping[str, object]) -> None:
    for field in ("channel", "region"):
        value = str(fact.get(field) or "")
        if not _DIMENSION_RE.fullmatch(value):
            raise _InputDrift("unsafe_public_dimension")
    versions = fact.get("versions")
    if not isinstance(versions, Mapping):
        raise _InputDrift("malformed_fact_versions")
    policy = str(versions.get("policy") or "")
    if not _DIMENSION_RE.fullmatch(policy):
        raise _InputDrift("unsafe_public_dimension")
    if fact.get("outcome_class") not in OUTCOME_CLASSES:
        raise _InputDrift("unknown_fact_outcome")
    error = fact.get("error")
    if not isinstance(error, Mapping) or error.get("category") not in ERROR_CATEGORIES:
        raise _InputDrift("unknown_error_category")
    dispatch = fact.get("dispatch")
    if not isinstance(dispatch, Mapping):
        raise _InputDrift("malformed_dispatch")
    write_count = dispatch.get("external_write_count")
    if write_count is not None and (
        isinstance(write_count, bool)
        or not isinstance(write_count, int)
        or write_count < 0
    ):
        raise _InputDrift("invalid_external_write_count")
    quality = fact.get("quality_issues")
    if isinstance(quality, (str, bytes)) or not isinstance(quality, Sequence):
        raise _InputDrift("malformed_quality_issues")


def _validated_evaluation(
    evaluation: Mapping[str, object],
    *,
    facts: Sequence[dict[str, Any]],
    dataset_snapshot_digest: str,
) -> dict[str, Any]:
    if not isinstance(evaluation, Mapping):
        raise _InputDrift("malformed_evaluation")
    if evaluation.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise _InputDrift("evaluation_schema_drift")
    if evaluation.get("fact_schema_version") != FACT_SCHEMA_VERSION:
        raise _InputDrift("evaluation_fact_schema_drift")
    if evaluation.get("input_snapshot_digest") != dataset_snapshot_digest:
        raise _InputDrift("evaluation_dataset_digest_drift")
    raw_group_by = evaluation.get("group_by")
    if (
        isinstance(raw_group_by, (str, bytes))
        or not isinstance(raw_group_by, Sequence)
        or len(raw_group_by) != 3
        or set(raw_group_by) != set(_EXPECTED_GROUPING)
    ):
        raise _InputDrift("evaluation_grouping_drift")
    group_by = tuple(str(value) for value in raw_group_by)
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for fact in facts:
        values = {
            "channel": fact["channel"],
            "region": fact["region"],
            "policy_version": (fact["versions"] or {})["policy"],
        }
        key = tuple(str(values[field]) for field in group_by)
        grouped.setdefault(key, []).append(fact)
    expected_groups = [
        {
            "dimensions": dict(zip(group_by, key)),
            "metrics": _metrics(rows),
        }
        for key, rows in sorted(grouped.items())
    ]
    supplied_groups = evaluation.get("groups")
    if isinstance(supplied_groups, (str, bytes)) or not isinstance(
        supplied_groups, Sequence
    ):
        raise _InputDrift("evaluation_groups_drift")
    normalized_supplied_groups = sorted(
        (json.loads(json.dumps(group)) for group in supplied_groups),
        key=lambda group: tuple(
            str((group.get("dimensions") or {}).get(field) or "")
            for field in group_by
        ),
    )
    if evaluation.get("overall") != _metrics(facts):
        raise _InputDrift("evaluation_metrics_drift")
    if normalized_supplied_groups != expected_groups:
        raise _InputDrift("evaluation_group_metrics_drift")
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "fact_schema_version": FACT_SCHEMA_VERSION,
        "input_snapshot_digest": dataset_snapshot_digest,
        "group_by": list(group_by),
        "overall": _metrics(facts),
        "groups": expected_groups,
    }


def _metrics(facts: Sequence[Mapping[str, object]]) -> dict[str, Any]:
    total = len(facts)
    successes = sum(
        fact.get("outcome_class") in {"SUCCESS", "MANUAL_ACCEPTED"}
        for fact in facts
    )
    official_readbacks = sum(
        fact.get("readback_status") == "VERIFIED" for fact in facts
    )
    manual_decisions = sum(
        fact.get("manual_status") in {"ACCEPTED", "REJECTED"} for fact in facts
    )
    manual_acceptances = sum(
        fact.get("manual_status") == "ACCEPTED" for fact in facts
    )
    reconciliation_known = sum(
        fact.get("reconciliation_status") != UNKNOWN for fact in facts
    )
    reconciliation_required = sum(
        fact.get("reconciliation_status") == "REQUIRED" for fact in facts
    )
    duplicate_known = sum(
        fact.get("duplicate_prevented") is not None for fact in facts
    )
    duplicate_preventions = sum(
        fact.get("duplicate_prevented") is True for fact in facts
    )
    known_write_facts = [
        fact
        for fact in facts
        if (fact.get("dispatch") or {}).get("external_write_count") is not None
    ]
    error_distribution = Counter(
        (fact.get("error") or {}).get("category")
        for fact in facts
        if (fact.get("error") or {}).get("category") not in {"NONE", UNKNOWN}
    )
    outcome_distribution = Counter(fact.get("outcome_class") for fact in facts)
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
            int((fact.get("dispatch") or {}).get("external_write_count") or 0)
            for fact in known_write_facts
        ),
        "unknown_external_write_fact_count": total - len(known_write_facts),
        "quality_issue_count": sum(
            len(fact.get("quality_issues") or ()) for fact in facts
        ),
        "outcome_distribution": {
            key: outcome_distribution.get(key, 0)
            for key in sorted(OUTCOME_CLASSES)
        },
        "error_distribution": {
            key: error_distribution.get(key, 0)
            for key in ("AUTH", "INVENTORY", "CONTENT", "LOGISTICS", "OTHER")
        },
    }


def _failure_trends(
    facts: Sequence[Mapping[str, object]],
    all_facts: Sequence[Mapping[str, object]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for category in ("AUTH", "INVENTORY", "CONTENT", "LOGISTICS", "OTHER"):
        count = sum(
            (fact.get("error") or {}).get("category") == category
            for fact in facts
        )
        overall_count = sum(
            (fact.get("error") or {}).get("category") == category
            for fact in all_facts
        )
        rate = _rate(count, len(facts)) or 0.0
        overall_rate = _rate(overall_count, len(all_facts)) or 0.0
        delta = round(rate - overall_rate, 6)
        direction = (
            "NONE"
            if count == 0 and overall_count == 0
            else "ELEVATED"
            if delta > 0
            else "LOWER"
            if delta < 0
            else "BASELINE"
        )
        result.append(
            {
                "category": category,
                "count": count,
                "rate": rate,
                "comparison_basis": "group_vs_dataset",
                "dataset_rate": overall_rate,
                "rate_delta": delta,
                "direction": direction,
            }
        )
    return result


def _review_action(trends: Sequence[Mapping[str, object]]) -> str:
    ranked = sorted(
        (
            (
                int(trend.get("count") or 0),
                str(trend.get("category") or ""),
            )
            for trend in trends
            if str(trend.get("category") or "") in _ERROR_ACTIONS
        ),
        key=lambda row: (-row[0], row[1]),
    )
    if ranked and ranked[0][0] > 0:
        return _ERROR_ACTIONS[ranked[0][1]]
    return "REVIEW_POLICY"


def _confidence(
    *,
    sample_count: int,
    minimum_coverage: float,
    blockers: Sequence[str],
    thresholds: OptimizationThresholds,
) -> str:
    if blockers:
        return "LOW"
    if (
        sample_count >= thresholds.high_confidence_sample_count
        and minimum_coverage == 1.0
    ):
        return "HIGH"
    if (
        sample_count >= thresholds.medium_confidence_sample_count
        and minimum_coverage >= 0.90
    ):
        return "MEDIUM"
    return "LOW"


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _input_digest(value: object) -> str:
    try:
        return _digest(value)
    except (TypeError, ValueError):
        return hashlib.sha256(type(value).__name__.encode("utf-8")).hexdigest()


def _digest(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
