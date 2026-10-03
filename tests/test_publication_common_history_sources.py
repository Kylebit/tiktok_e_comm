"""Red/green checks for byte-bound, offline COMMON history inputs."""

import hashlib
import json

from shared_platform.publication_common_history_sources import build_from_local_exports


OFFER = "3956742887"
START = "2026-09-01T00:00:00Z"
END = "2026-09-29T00:00:00Z"


def _file(root, name, kind, *, plans=None, attempts=None, edits=None,
          covered_from=START, covered_until=END, captured_at=END):
    source_id = name.removesuffix(".json")
    data = {"schema_version": "common-history-export/v1", "source_id": source_id,
            "source_kind": kind, "offer_id": OFFER, "captured_at": captured_at,
            "covered_from": covered_from, "covered_until": covered_until,
            "coverage_claim": "COMPLETE"}
    if kind == "RELEASE_STORE_EXPORT":
        data.update(plan_runs=plans or [], attempts=attempts or [])
    else:
        data["observed_edit_count"] = edits
    raw = json.dumps(data, separators=(",", ":")).encode()
    (root / name).write_bytes(raw)
    return {"path": name, "expected_sha256": hashlib.sha256(raw).hexdigest(),
            "source_id": source_id, "source_kind": kind, "offer_id": OFFER,
            "captured_at": captured_at, "covered_from": covered_from,
            "covered_until": covered_until}


def _run(root, sources):
    return build_from_local_exports(root, OFFER, START, END, sources)


def test_exact_raw_bytes_and_old_plan_run_attempt_still_block(tmp_path):
    ledger = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT",
                   plans=[{"plan_id": "old", "run_id": "r-old", "attempt_count": 1},
                          {"plan_id": "new", "run_id": "r-new", "attempt_count": 1}],
                   attempts=[{"plan_id": "old", "run_id": "r-old", "attempt": 1,
                              "occurred_at": START, "outcome": "VERIFIED_WRITE",
                              "evidence_digest": "a" * 64},
                             {"plan_id": "new", "run_id": "r-new", "attempt": 1,
                              "occurred_at": END, "outcome": "NOT_DISPATCHED",
                              "evidence_digest": "b" * 64}])
    account = _file(tmp_path, "account.json", "MIAOSHOU_ACTIVITY_EXPORT", edits=0)
    result = _run(tmp_path, [ledger, account])
    assert result["recorded_plan_run_count"] == 2
    assert result["observed_verified_write_receipt_count"] == 1
    assert result["source_adapter"]["verified_sha256_by_source"]["ledger"] == ledger["expected_sha256"]
    assert result["recorded_scope_completeness"] == "CLAIMED_COMPLETE_UNVERIFIED"
    assert result["coverage"] == "UNKNOWN"
    assert result["status"] == "BLOCKED"
    assert result["historical_write_count"] is None
    assert result["historical_write_budget_maximum"] is None
    assert result["execution_authority"] is False
    assert result["final_review_available"] is False


def test_tampered_bytes_are_not_ingested(tmp_path):
    spec = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    (tmp_path / "ledger.json").write_bytes((tmp_path / "ledger.json").read_bytes() + b" ")
    result = _run(tmp_path, [spec])
    assert "COMMON_SOURCE_BYTES_SHA_MISMATCH" in result["blockers"]
    assert result["recorded_scope_completeness"] == "INCOMPLETE"
    assert result["source_adapter"]["verified_sha256_by_source"] == {}
    assert result["source_adapter"]["observed_sha256_by_source"]["ledger"] != spec["expected_sha256"]


def test_wrong_offer_or_window_rejected_before_ingest(tmp_path):
    spec = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    spec["offer_id"] = "other"
    assert "COMMON_SOURCE_METADATA_MISMATCH" in _run(tmp_path, [spec])["blockers"]
    spec["offer_id"] = OFFER
    spec["covered_from"] = "2026-09-02T00:00:00Z"
    assert "COMMON_SOURCE_METADATA_MISMATCH" in _run(tmp_path, [spec])["blockers"]


def test_single_run_without_account_export_does_not_infer_history(tmp_path):
    ledger = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT",
                   plans=[{"plan_id": "p", "run_id": "r", "attempt_count": 0}])
    result = _run(tmp_path, [ledger])
    assert "COMMON_ACCOUNT_HISTORY_SOURCE_MISSING" in result["blockers"]
    assert result["recorded_scope_completeness"] == "UNKNOWN"
    assert result["coverage"] == "UNKNOWN"


def test_unknown_receipt_and_out_of_window_attempt_block(tmp_path):
    ledger = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT",
                   covered_until="2026-09-15T00:00:00Z",
                   plans=[{"plan_id": "p", "run_id": "r", "attempt_count": 1}],
                   attempts=[{"plan_id": "p", "run_id": "r", "attempt": 1,
                              "occurred_at": END, "outcome": "RECEIPT_UNKNOWN",
                              "evidence_digest": "a" * 64}])
    result = _run(tmp_path, [ledger])
    assert "COMMON_RECEIPT_RECONCILIATION_REQUIRED" in result["blockers"]
    assert "COMMON_ATTEMPT_OUTSIDE_SOURCE_WINDOW" in result["blockers"]


def test_account_claim_with_edits_and_missing_policy_remains_blocked(tmp_path):
    ledger = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    account = _file(tmp_path, "account.json", "MIAOSHOU_ACTIVITY_EXPORT", edits=2)
    result = _run(tmp_path, [ledger, account])
    assert "COMMON_OUT_OF_BAND_EDITS_RECONCILIATION_REQUIRED" in result["blockers"]
    assert "COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN" in result["blockers"]


def test_path_escape_and_non_json_refused(tmp_path):
    spec = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    spec["path"] = "../ledger.json"
    assert "COMMON_SOURCE_PATH_REJECTED" in _run(tmp_path, [spec])["blockers"]
    spec["path"] = "ledger.db"
    assert "COMMON_SOURCE_PATH_REJECTED" in _run(tmp_path, [spec])["blockers"]


def test_duplicate_source_id_rejected(tmp_path):
    spec = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    result = _run(tmp_path, [spec, spec])
    assert "COMMON_SOURCE_SPEC_DUPLICATE" in result["blockers"]
    assert result["recorded_scope_completeness"] == "INCOMPLETE"


def test_multiple_account_windows_cannot_share_one_zero_edit_statement(tmp_path):
    ledger = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    first = _file(tmp_path, "account1.json", "MIAOSHOU_ACTIVITY_EXPORT", edits=0,
                  covered_until="2026-09-15T00:00:00Z")
    second = _file(tmp_path, "account2.json", "MIAOSHOU_ACTIVITY_EXPORT", edits=0,
                   covered_from="2026-09-15T00:00:00Z")
    result = _run(tmp_path, [ledger, first, second])
    assert "COMMON_OUT_OF_BAND_HISTORY_UNKNOWN" in result["blockers"]
    assert result["recorded_scope_completeness"] == "UNKNOWN"


def test_overlapping_ledger_exports_need_reconciliation(tmp_path):
    first = _file(tmp_path, "ledger1.json", "RELEASE_STORE_EXPORT",
                  covered_until="2026-09-20T00:00:00Z")
    second = _file(tmp_path, "ledger2.json", "RELEASE_STORE_EXPORT",
                   covered_from="2026-09-10T00:00:00Z")
    result = _run(tmp_path, [first, second])
    assert "COMMON_HISTORY_SOURCE_OVERLAP_REQUIRES_RECONCILIATION" in result["blockers"]
    assert result["recorded_scope_completeness"] == "INCOMPLETE"


def test_duplicate_json_keys_fail_closed_even_when_sha_matches(tmp_path):
    spec = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    raw = (tmp_path / "ledger.json").read_bytes().replace(
        b'"offer_id":"3956742887",',
        b'"offer_id":"3956742887","offer_id":"3956742887",')
    (tmp_path / "ledger.json").write_bytes(raw)
    spec["expected_sha256"] = hashlib.sha256(raw).hexdigest()
    result = _run(tmp_path, [spec])
    assert "COMMON_SOURCE_PARSE_FAILED" in result["blockers"]
    assert result["recorded_scope_completeness"] == "INCOMPLETE"


def test_untrusted_spec_field_types_fail_closed_without_exception(tmp_path):
    base = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    for field, bad_value in (("source_kind", []), ("source_kind", {}),
                             ("source_kind", 17), ("offer_id", []),
                             ("path", []), ("expected_sha256", []),
                             ("captured_at", []), ("covered_from", {})):
        spec = {**base, field: bad_value}
        result = _run(tmp_path, [spec])
        assert result["status"] == "BLOCKED", field
        assert result["coverage"] == "UNKNOWN", field
        assert result["recorded_scope_completeness"] == "INCOMPLETE", field
        assert result["execution_authority"] is False, field


def test_deep_json_under_size_limit_fails_closed_without_recursion_error(tmp_path):
    spec = _file(tmp_path, "ledger.json", "RELEASE_STORE_EXPORT")
    raw = b"[" * 20000 + b"0" + b"]" * 20000
    assert len(raw) < 4 * 1024 * 1024
    (tmp_path / "ledger.json").write_bytes(raw)
    spec["expected_sha256"] = hashlib.sha256(raw).hexdigest()
    result = _run(tmp_path, [spec])
    assert "COMMON_SOURCE_PARSE_FAILED" in result["blockers"]
    assert result["recorded_scope_completeness"] == "INCOMPLETE"
    assert result["status"] == "BLOCKED"
    assert result["execution_authority"] is False
