import json
from pathlib import Path

import pytest

from domains.data_operations import (
    OptimizationThresholds,
    adapt_release_outcome_receipts,
    build_release_optimization_candidates,
    evaluate_release_outcomes,
    release_outcome_dataset,
)


FIXTURE = (
    Path(__file__).parent / "fixtures" / "release_outcome_receipts_v1.json"
)


def _base_receipt():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["receipts"][0]


def _inputs(
    count,
    *,
    error_category="NONE",
    unknown_write_indexes=(),
    policy="policy-v1",
):
    receipts = []
    for index in range(count):
        receipt = json.loads(json.dumps(_base_receipt()))
        receipt["identity"] = {
            "plan_digest": f"{index + 1:064x}",
            "run_digest": f"{index + 101:064x}",
            "target_digest": f"{index + 201:064x}",
        }
        receipt["versions"]["policy"] = policy
        if error_category != "NONE":
            receipt["outcome"] = {"class": "FAILURE"}
            receipt["error"] = {
                "category": error_category,
                "code": f"{error_category}_ERROR",
                "type": "RedactedError",
            }
        if index in set(unknown_write_indexes):
            receipt["dispatch"].pop("external_write_count")
            receipt["dispatch"].pop("external_write_classes")
        receipts.append(receipt)
    facts = adapt_release_outcome_receipts(receipts)
    return release_outcome_dataset(facts), evaluate_release_outcomes(facts)


def _candidate(dataset, evaluation, thresholds):
    artifact = build_release_optimization_candidates(
        dataset,
        evaluation,
        thresholds=thresholds,
    )
    assert artifact["candidate_count"] == 1
    return artifact, artifact["candidates"][0]


def test_candidate_artifact_is_order_independent_and_json_stable():
    dataset, evaluation = _inputs(6)
    limits = OptimizationThresholds(
        min_sample_count=5,
        medium_confidence_sample_count=5,
        high_confidence_sample_count=10,
    )
    first = build_release_optimization_candidates(
        dataset, evaluation, thresholds=limits
    )
    dataset["facts"].reverse()
    evaluation["groups"].reverse()
    second = build_release_optimization_candidates(
        dataset, evaluation, thresholds=limits
    )

    assert first == second
    assert first["status"] == "READY"
    assert first["candidates"][0]["recommended_action_code"] == "REVIEW_POLICY"
    assert first["candidates"][0]["requires_human_approval"] is True
    json.dumps(first)


def test_sample_threshold_is_fail_closed_below_and_passes_at_boundary():
    limits = OptimizationThresholds(
        min_sample_count=3,
        medium_confidence_sample_count=3,
        high_confidence_sample_count=10,
    )
    below_dataset, below_evaluation = _inputs(2)
    _, below = _candidate(below_dataset, below_evaluation, limits)
    assert below["recommended_action_code"] == "COLLECT_MORE_EVIDENCE"
    assert below["quality_blockers"] == ["sample_count_below_minimum"]

    edge_dataset, edge_evaluation = _inputs(3)
    _, edge = _candidate(edge_dataset, edge_evaluation, limits)
    assert edge["recommended_action_code"] == "REVIEW_POLICY"
    assert edge["quality_blockers"] == []


def test_empty_valid_dataset_is_blocked_for_more_evidence():
    facts = adapt_release_outcome_receipts([])
    dataset = release_outcome_dataset(facts)
    evaluation = evaluate_release_outcomes(facts)

    artifact = build_release_optimization_candidates(dataset, evaluation)

    assert artifact["status"] == "BLOCKED"
    assert artifact["candidate_count"] == 1
    candidate = artifact["candidates"][0]
    assert candidate["quality_blockers"] == ["empty_dataset"]
    assert candidate["recommended_action_code"] == "COLLECT_MORE_EVIDENCE"


def test_unknown_write_rate_is_not_optimistic_and_boundary_is_explicit():
    limits = OptimizationThresholds(
        min_sample_count=4,
        max_unknown_write_rate=0.25,
        min_quality_clear_coverage=0.75,
        medium_confidence_sample_count=4,
        high_confidence_sample_count=10,
    )
    edge_dataset, edge_evaluation = _inputs(4, unknown_write_indexes=(0,))
    _, edge = _candidate(edge_dataset, edge_evaluation, limits)
    assert edge["rates"]["external_write_unknown_rate"] == 0.25
    assert edge["recommended_action_code"] == "REVIEW_POLICY"

    high_dataset, high_evaluation = _inputs(
        4, unknown_write_indexes=(0, 1)
    )
    _, high = _candidate(high_dataset, high_evaluation, limits)
    assert high["rates"]["external_write_unknown_rate"] == 0.5
    assert high["recommended_action_code"] == "COLLECT_MORE_EVIDENCE"
    assert {
        "unknown_write_rate_above_maximum",
        "quality_clear_coverage_below_minimum",
    } <= set(high["quality_blockers"])


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("AUTH", "REVIEW_AUTH"),
        ("INVENTORY", "REVIEW_INVENTORY"),
        ("CONTENT", "REVIEW_CONTENT"),
        ("LOGISTICS", "REVIEW_LOGISTICS"),
    ],
)
def test_explicit_error_categories_map_only_to_human_review(category, expected):
    dataset, evaluation = _inputs(5, error_category=category)
    limits = OptimizationThresholds(
        min_sample_count=5,
        medium_confidence_sample_count=5,
        high_confidence_sample_count=10,
    )
    _, candidate = _candidate(dataset, evaluation, limits)
    assert candidate["recommended_action_code"] == expected
    trend = next(
        row
        for row in candidate["failure_category_trends"]
        if row["category"] == category
    )
    assert trend["count"] == 5
    assert trend["comparison_basis"] == "group_vs_dataset"


@pytest.mark.parametrize(
    "mutation",
    [
        "dataset_schema",
        "dataset_digest",
        "fact_digest",
        "evaluation_schema",
        "evaluation_dataset_digest",
        "evaluation_metrics",
    ],
)
def test_schema_or_digest_drift_only_collects_more_evidence(mutation):
    dataset, evaluation = _inputs(5)
    if mutation == "dataset_schema":
        dataset["schema_version"] = "release-outcome-dataset/v2"
    elif mutation == "dataset_digest":
        dataset["snapshot_digest"] = "0" * 64
    elif mutation == "fact_digest":
        dataset["facts"][0]["outcome_class"] = "FAILURE"
    elif mutation == "evaluation_schema":
        evaluation["schema_version"] = "release-outcome-evaluation/v2"
    elif mutation == "evaluation_dataset_digest":
        evaluation["input_snapshot_digest"] = "0" * 64
    else:
        evaluation["overall"]["success_count"] = 0

    artifact = build_release_optimization_candidates(dataset, evaluation)
    candidate = artifact["candidates"][0]
    assert artifact["status"] == "BLOCKED"
    assert candidate["recommended_action_code"] == "COLLECT_MORE_EVIDENCE"
    assert candidate["confidence_band"] == "LOW"
    assert candidate["requires_human_approval"] is True


def test_output_never_contains_execution_retry_or_raw_identity_fields():
    dataset, evaluation = _inputs(5, error_category="CONTENT")
    artifact = build_release_optimization_candidates(dataset, evaluation)

    forbidden_keys = {
        "execute",
        "retry",
        "dispatch",
        "automatic",
        "seller_sku",
        "plan_id",
        "run_id",
        "raw_error",
        "copy",
        "url",
        "token",
    }

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key.lower() not in forbidden_keys
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(artifact)
    rendered = json.dumps(artifact).lower()
    assert "http://" not in rendered
    assert "https://" not in rendered
