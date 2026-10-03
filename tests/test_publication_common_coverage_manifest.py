"""Offline counterexamples for a claimed Offer-wide COMMON evidence envelope."""

from copy import deepcopy

from shared_platform.publication_common_coverage_manifest import build_common_coverage_manifest


D = "a" * 64
START = "2026-09-01T00:00:00Z"
END = "2026-09-29T00:00:00Z"


def envelope():
    return {
        "offer_id": "3956742887",
        "lifecycle_started_at": START,
        "as_of": END,
        "sources": [
            {"source_id": "ledger", "source_kind": "RELEASE_STORE_EXPORT",
             "offer_id": "3956742887", "evidence_digest": D,
             "captured_at": END, "covered_from": START, "covered_until": END,
             "coverage_claim": "COMPLETE"},
            {"source_id": "account", "source_kind": "MIAOSHOU_ACTIVITY_EXPORT",
             "offer_id": "3956742887", "evidence_digest": "b" * 64,
             "captured_at": END, "covered_from": START, "covered_until": END,
             "coverage_claim": "COMPLETE"},
        ],
        "plan_runs": [
            {"source_id": "ledger", "offer_id": "3956742887",
             "plan_id": "old-plan", "run_id": "old-run",
             "attempt_count": 1},
            {"source_id": "ledger", "offer_id": "3956742887",
             "plan_id": "new-plan", "run_id": "new-run",
             "attempt_count": 2},
        ],
        "attempts": [
            {"source_id": "ledger", "offer_id": "3956742887",
             "plan_id": "old-plan", "run_id": "old-run",
             "attempt": 1, "occurred_at": START,
             "outcome": "VERIFIED_WRITE", "evidence_digest": "c" * 64},
            {"source_id": "ledger", "offer_id": "3956742887",
             "plan_id": "new-plan", "run_id": "new-run",
             "attempt": 1, "occurred_at": START,
             "outcome": "NOT_DISPATCHED", "evidence_digest": "d" * 64},
            {"source_id": "ledger", "offer_id": "3956742887",
             "plan_id": "new-plan", "run_id": "new-run",
             "attempt": 2, "occurred_at": END,
             "outcome": "VERIFIED_WRITE", "evidence_digest": "e" * 64},
        ],
        "out_of_band": {"status": "CLAIMED_COMPLETE", "source_id": "account",
                        "observed_edit_count": 0},
    }


def test_even_complete_claims_are_not_authority():
    result = build_common_coverage_manifest(envelope())
    assert result["coverage"] == "UNKNOWN"
    assert result["recorded_scope_completeness"] == "CLAIMED_COMPLETE_UNVERIFIED"
    assert result["observed_verified_write_receipt_count"] == 2
    assert result["historical_write_count"] is None
    assert result["historical_write_budget_maximum"] is None
    assert result["final_review_available"] is False
    assert result["dispatch_allowed"] is False


def test_single_run_cannot_claim_old_plan_or_account_activity():
    data = envelope()
    data["plan_runs"] = data["plan_runs"][1:]
    data["attempts"] = data["attempts"][1:]
    data["sources"] = data["sources"][:1]
    data["out_of_band"] = {"status": "UNKNOWN"}
    result = build_common_coverage_manifest(data)
    assert result["recorded_scope_completeness"] == "UNKNOWN"
    assert "COMMON_ACCOUNT_HISTORY_SOURCE_MISSING" in result["blockers"]
    assert "COMMON_OUT_OF_BAND_HISTORY_UNKNOWN" in result["blockers"]


def test_gap_or_partial_source_is_incomplete():
    data = envelope()
    data["sources"][0]["covered_until"] = "2026-09-15T00:00:00Z"
    result = build_common_coverage_manifest(data)
    assert result["recorded_scope_completeness"] == "INCOMPLETE"
    assert "COMMON_HISTORY_INTERVAL_GAP" in result["blockers"]


def test_missing_attempt_in_old_plan_is_incomplete():
    data = envelope()
    data["attempts"] = data["attempts"][1:]
    result = build_common_coverage_manifest(data)
    assert result["recorded_scope_completeness"] == "INCOMPLETE"
    assert "COMMON_RECORDED_ATTEMPT_MISSING" in result["blockers"]


def test_duplicate_and_conflicting_attempts_fail_closed():
    data = envelope()
    data["attempts"].append(deepcopy(data["attempts"][0]))
    duplicate = build_common_coverage_manifest(data)
    assert "COMMON_ATTEMPT_DUPLICATE" in duplicate["blockers"]
    data["attempts"][-1]["outcome"] = "NOT_DISPATCHED"
    conflict = build_common_coverage_manifest(data)
    assert "COMMON_ATTEMPT_CONFLICT" in conflict["blockers"]


def test_cross_offer_wrong_run_and_unknown_outcome_fail_closed():
    data = envelope()
    data["sources"][0]["offer_id"] = "other"
    data["attempts"][0]["run_id"] = "new-run"
    data["attempts"][1]["outcome"] = "RECEIPT_UNKNOWN"
    result = build_common_coverage_manifest(data)
    assert "COMMON_SOURCE_OFFER_MISMATCH" in result["blockers"]
    assert "COMMON_ATTEMPT_BINDING_MISMATCH" in result["blockers"]
    assert "COMMON_RECEIPT_RECONCILIATION_REQUIRED" in result["blockers"]


def test_unsupported_source_and_self_claimed_policy_never_unlock():
    data = envelope()
    data["sources"][1]["source_kind"] = "SYNTHETIC_TEST_ONLY"
    data["policy_claim"] = {"maximum_write_count": 99, "source": "self"}
    result = build_common_coverage_manifest(data)
    assert "COMMON_ACCOUNT_HISTORY_SOURCE_MISSING" in result["blockers"]
    assert "COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN" in result["blockers"]
    assert result["execution_authority"] is False


def test_reordered_input_is_digest_stable():
    data = envelope()
    expected = build_common_coverage_manifest(data)["input_digest"]
    data["sources"].reverse()
    data["plan_runs"].reverse()
    data["attempts"].reverse()
    assert build_common_coverage_manifest(data)["input_digest"] == expected


def test_duplicate_plan_and_overlapping_exports_are_incomplete():
    data = envelope()
    data["plan_runs"].append(deepcopy(data["plan_runs"][0]))
    overlapping = deepcopy(data["sources"][0])
    overlapping["source_id"] = "ledger-two"
    overlapping["evidence_digest"] = "f" * 64
    data["sources"].append(overlapping)
    result = build_common_coverage_manifest(data)
    assert "COMMON_PLAN_RUN_DUPLICATE" in result["blockers"]
    assert "COMMON_HISTORY_SOURCE_OVERLAP_REQUIRES_RECONCILIATION" in result["blockers"]


def test_out_of_band_edits_and_wrong_offer_binding_are_unresolved():
    data = envelope()
    data["out_of_band"]["observed_edit_count"] = 1
    data["plan_runs"][0]["offer_id"] = "other"
    data["attempts"][0]["offer_id"] = "other"
    result = build_common_coverage_manifest(data)
    assert "COMMON_OUT_OF_BAND_EDITS_RECONCILIATION_REQUIRED" in result["blockers"]
    assert "COMMON_PLAN_RUN_OFFER_MISMATCH" in result["blockers"]
    assert "COMMON_ATTEMPT_BINDING_MISMATCH" in result["blockers"]


def test_same_plan_id_across_distinct_runs_needs_reconciliation():
    data = envelope()
    data["plan_runs"][1]["plan_id"] = "old-plan"
    data["attempts"][1]["plan_id"] = "old-plan"
    data["attempts"][2]["plan_id"] = "old-plan"
    result = build_common_coverage_manifest(data)
    assert result["recorded_scope_completeness"] == "INCOMPLETE"
    assert "COMMON_PLAN_MULTI_RUN_UNRECONCILED" in result["blockers"]


def test_attempt_must_fall_inside_its_own_source_window_and_capture():
    data = envelope()
    data["sources"][0]["covered_until"] = "2026-09-15T00:00:00Z"
    second = deepcopy(data["sources"][0])
    second["source_id"] = "ledger2"
    second["covered_from"] = "2026-09-15T00:00:00Z"
    second["covered_until"] = END
    second["evidence_digest"] = "f" * 64
    data["sources"].append(second)
    result = build_common_coverage_manifest(data)
    assert result["recorded_scope_completeness"] == "INCOMPLETE"
    assert "COMMON_ATTEMPT_OUTSIDE_SOURCE_WINDOW" in result["blockers"]
    data["sources"][0]["covered_until"] = END
    data["sources"].pop()
    data["sources"][0]["captured_at"] = "2026-09-15T00:00:00Z"
    data["sources"][0]["covered_until"] = "2026-09-15T00:00:00Z"
    assert "COMMON_ATTEMPT_OUTSIDE_SOURCE_WINDOW" in (
        build_common_coverage_manifest(data)["blockers"])


def test_every_account_source_needs_out_of_band_statement():
    data = envelope()
    data["sources"][1]["covered_until"] = "2026-09-15T00:00:00Z"
    second = deepcopy(data["sources"][1])
    second["source_id"] = "account2"
    second["covered_from"] = "2026-09-15T00:00:00Z"
    second["covered_until"] = END
    second["evidence_digest"] = "f" * 64
    data["sources"].append(second)
    result = build_common_coverage_manifest(data)
    assert result["recorded_scope_completeness"] in {"UNKNOWN", "INCOMPLETE"}
    assert "COMMON_OUT_OF_BAND_HISTORY_UNKNOWN" in result["blockers"]
    assert result["coverage"] == "UNKNOWN"
    assert result["final_review_available"] is False
