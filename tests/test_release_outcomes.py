import json
from pathlib import Path

import pytest

from domains.data_operations.release_outcomes import (
    FACT_SCHEMA_VERSION,
    SUBMITTED_UNVERIFIED,
    ReleaseOutcomeContractError,
    adapt_release_outcome_receipt,
    adapt_release_outcome_receipts,
    evaluate_release_outcomes,
    release_outcome_dataset,
)


FIXTURE = (
    Path(__file__).parent / "fixtures" / "release_outcome_receipts_v1.json"
)


def _receipt(**overrides):
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))["receipts"][0]
    source.update(overrides)
    return source


def test_release_outcome_fact_is_redacted_json_ready_and_idempotent():
    receipt = _receipt()
    first = adapt_release_outcome_receipt(receipt)
    second = adapt_release_outcome_receipt(json.loads(json.dumps(receipt)))

    assert first == second
    assert first.schema_version == FACT_SCHEMA_VERSION
    assert first.fact_digest == second.fact_digest
    assert first.external_write_count == 1
    assert first.external_write_classes == ("shopee:price",)
    payload = first.payload()
    json.dumps(payload)
    rendered = json.dumps(payload).lower()
    for prohibited in ("token", "seller_sku", "raw_response", "http://"):
        assert prohibited not in rendered


def test_missing_write_evidence_is_unknown_and_never_inferred_as_zero():
    receipt = _receipt()
    receipt["dispatch"].pop("external_write_count")
    receipt["dispatch"].pop("external_write_classes")

    fact = adapt_release_outcome_receipt(receipt)

    assert fact.external_write_count is None
    assert fact.external_write_classes == ("UNKNOWN",)
    assert {
        "missing_external_write_count",
        "missing_external_write_classes",
    } <= set(fact.quality_issues)
    metrics = evaluate_release_outcomes([fact])["overall"]
    assert metrics["external_write_total"] == 0
    assert metrics["unknown_external_write_fact_count"] == 1


@pytest.mark.parametrize(
    "unsafe",
    [
        {"token": "redacted-looking-but-forbidden"},
        {"raw_response": {"status": "ok"}},
        {"image_id": "marketplace-image-1"},
        {"note": "https://marketplace.example/item"},
        {"seller_sku": "0954"},
    ],
)
def test_raw_or_sensitive_receipt_fields_fail_closed(unsafe):
    receipt = _receipt()
    receipt.update(unsafe)
    with pytest.raises(ReleaseOutcomeContractError):
        adapt_release_outcome_receipt(receipt)


def test_schema_identity_and_invalid_counts_fail_closed():
    future = _receipt(schema_version="release-outcome-receipt/v2")
    with pytest.raises(ReleaseOutcomeContractError, match="unsupported"):
        adapt_release_outcome_receipt(future)

    missing_identity = _receipt()
    missing_identity["identity"].pop("run_digest")
    with pytest.raises(ReleaseOutcomeContractError, match="run_digest"):
        adapt_release_outcome_receipt(missing_identity)

    negative_count = _receipt()
    negative_count["counts"]["attempts"] = -1
    with pytest.raises(ReleaseOutcomeContractError, match="attempt_count"):
        adapt_release_outcome_receipt(negative_count)


def test_unknown_semantics_remain_unknown_instead_of_being_guessed():
    receipt = _receipt(
        outcome={"class": "maybe_succeeded"},
        readback={"status": "ambiguous"},
        manual={},
        reconciliation={},
        error={},
    )
    fact = adapt_release_outcome_receipt(receipt)

    assert fact.outcome_class == "UNKNOWN"
    assert fact.readback_status == "UNKNOWN"
    assert fact.manual_status == "UNKNOWN"
    assert fact.reconciliation_status == "UNKNOWN"
    assert fact.error_category == "UNKNOWN"
    assert {
        "unknown_outcome_class",
        "unknown_readback_status",
        "missing_manual_status",
        "missing_reconciliation_status",
        "missing_error_category",
    } <= set(fact.quality_issues)


def test_platform_accepted_unverified_is_not_human_acceptance_or_success():
    receipt = _receipt(
        outcome={
            "class": "MANUAL_ACCEPTED",
            "platform_status": "ACCEPTED_UNVERIFIED",
        },
        manual={"status": "ACCEPTED"},
        reconciliation={"status": "NOT_REQUIRED"},
    )

    fact = adapt_release_outcome_receipt(receipt)
    evaluation = evaluate_release_outcomes([fact])

    assert fact.outcome_class == SUBMITTED_UNVERIFIED
    assert fact.manual_status == "PENDING"
    assert fact.reconciliation_status == "NOT_REQUIRED"
    assert {
        "outcome_class_overridden_by_platform_status",
        "manual_status_normalized_for_submitted_unverified",
    } <= set(fact.quality_issues)
    overall = evaluation["overall"]
    assert overall["success_count"] == 0
    assert overall["success_rate"] == 0.0
    assert overall["manual_acceptance_count"] == 0
    assert overall["manual_decision_count"] == 0
    assert overall["manual_acceptance_rate"] is None
    assert overall["outcome_distribution"][SUBMITTED_UNVERIFIED] == 1


def test_submitted_unverified_preserves_separate_reconciliation_requirement():
    receipt = _receipt(
        outcome={"class": SUBMITTED_UNVERIFIED},
        manual={"status": "PENDING"},
        reconciliation={"status": "REQUIRED"},
    )

    fact = adapt_release_outcome_receipt(receipt)

    assert fact.outcome_class == SUBMITTED_UNVERIFIED
    assert fact.manual_status == "PENDING"
    assert fact.reconciliation_status == "REQUIRED"
    assert fact.quality_issues == ()


def test_legacy_accepted_unverified_class_normalizes_without_claiming_a_person():
    fact = adapt_release_outcome_receipt(
        _receipt(
            outcome={"class": "ACCEPTED_UNVERIFIED"},
            manual={"status": "ACCEPTED"},
            reconciliation={},
        )
    )

    assert fact.outcome_class == SUBMITTED_UNVERIFIED
    assert fact.manual_status == "PENDING"
    assert fact.reconciliation_status == "NOT_REQUIRED"
    assert "legacy_accepted_unverified_class_normalized" in fact.quality_issues


def test_replay_fixture_dataset_and_evaluation_are_stable_across_input_order():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == "release-outcome-receipt-fixture/v1"
    receipts = fixture["receipts"]
    facts = adapt_release_outcome_receipts(receipts)
    reversed_facts = adapt_release_outcome_receipts(reversed(receipts))

    first_dataset = release_outcome_dataset(facts)
    second_dataset = release_outcome_dataset(reversed_facts)
    assert first_dataset == second_dataset
    assert first_dataset["fact_count"] == 3
    assert len(first_dataset["snapshot_digest"]) == 64

    evaluation = evaluate_release_outcomes(facts)
    overall = evaluation["overall"]
    assert overall["success_count"] == 2
    assert overall["success_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert overall["official_readback_verified_count"] == 1
    assert overall["official_readback_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert overall["manual_acceptance_rate"] == 1.0
    assert overall["reconciliation_required_count"] == 1
    assert overall["error_distribution"]["INVENTORY"] == 1
    assert overall["error_distribution"]["AUTH"] == 0
    assert len(evaluation["groups"]) == 3
    json.dumps(evaluation)


def test_error_distributions_and_duplicate_prevention_are_explicit():
    base = _receipt()
    categories = ("AUTH", "INVENTORY", "CONTENT", "LOGISTICS")
    facts = []
    for index, category in enumerate(categories, 5):
        receipt = json.loads(json.dumps(base))
        receipt["identity"]["target_digest"] = str(index) * 64
        receipt["outcome"] = {"class": "FAILURE"}
        receipt["error"] = {
            "category": category,
            "code": f"{category}_ERROR",
            "type": "RedactedError",
        }
        receipt["duplicate_prevented"] = category == "CONTENT"
        facts.append(adapt_release_outcome_receipt(receipt))

    overall = evaluate_release_outcomes(facts)["overall"]
    assert overall["error_distribution"] == {
        "AUTH": 1,
        "INVENTORY": 1,
        "CONTENT": 1,
        "LOGISTICS": 1,
        "OTHER": 0,
    }
    assert overall["duplicate_prevention_count"] == 1
    assert overall["duplicate_prevention_rate"] == 0.25


def test_grouping_supports_channel_region_and_policy_version_only():
    facts = adapt_release_outcome_receipts(
        json.loads(FIXTURE.read_text(encoding="utf-8"))["receipts"]
    )
    by_policy = evaluate_release_outcomes(facts, group_by=("policy_version",))
    assert [group["dimensions"] for group in by_policy["groups"]] == [
        {"policy_version": "policy-2026-07"},
        {"policy_version": "policy-2026-08"},
    ]
    with pytest.raises(ValueError, match="supports"):
        evaluate_release_outcomes(facts, group_by=("adapter_version",))
