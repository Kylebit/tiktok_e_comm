from datetime import datetime, timezone
from decimal import Decimal
import json

from domains.data_operations import build_weekly_profit_digest


def test_weekly_digest_separates_realized_estimates_deduplicates_and_is_json_ready():
    rows = [
        {"order_id": "A", "sku_id": "SKU-1", "currency": "USD", "settlement_amount": "20.00", "cost_cny": "30", "fx_cny_per_local": "7", "statement_date": "2026-07-20T10:00:00+08:00", "source_updated_at": "2026-07-20T11:00:00+08:00"},
        {"order_id": "A", "sku_id": "SKU-1", "currency": "USD", "settlement_amount": "10.00", "cost_cny": "1", "fx_cny_per_local": "7", "statement_date": "2026-07-20T10:00:00+08:00", "source_updated_at": "2026-07-20T09:00:00+08:00"},
        {"order_id": "B", "sku_id": "SKU-2", "currency": "CNY", "settlement_amount": "5", "cost_cny": "9", "calculation_kind": "estimate", "statement_date": "2026-07-21"},
    ]
    report = build_weekly_profit_digest(rows, period_start="2026-07-20", period_end="2026-07-26", code_version="abc", generated_at=datetime(2026, 7, 26, tzinfo=timezone.utc))

    assert report.raw_row_count == 3 and report.deduplicated_row_count == 2
    assert report.realized_by_sku[0]["profit_cny"] == Decimal("110.00")
    assert report.estimate_by_sku[0]["profit_cny"] == Decimal("-4")
    assert report.negative_profit_skus[0]["sku_id"] == "SKU-2"
    payload = report.payload()
    assert payload["period"]["timezone"] == "Asia/Shanghai"
    assert payload["input_snapshot"]["checksum"]
    assert payload["idempotency_key"].startswith("weekly_profit_digest:")
    json.dumps(payload)


def test_weekly_digest_marks_missing_cost_fx_settlement_and_stale_data():
    report = build_weekly_profit_digest(
        [
            {"order_id": "1", "sku_id": "A", "currency": "USD", "settlement_amount": "1", "statement_date": "2026-01-01"},
            {"order_id": "2", "sku_id": "B", "currency": "CNY", "cost_cny": "1", "statement_date": "2026-01-01"},
            {"order_id": "3", "sku_id": "C", "currency": "CNY", "settlement_amount": "1", "statement_date": "2026-01-01"},
        ], period_start="2026-01-01", period_end="2026-01-07", generated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    assert report.status == "needs_review"
    assert {issue.code for issue in report.quality_issues} >= {"missing_fx", "missing_settlement", "missing_cost", "stale_data"}
    assert report.freshness["state"] == "stale"


def test_weekly_digest_is_idempotent_for_same_snapshot_and_configuration():
    kwargs = dict(rows=[{"order_id": "1", "sku_id": "A", "currency": "CNY", "settlement_amount": "2.123456789", "cost_cny": "1", "statement_date": "2026-07-20"}], period_start="2026-07-20", period_end="2026-07-26", assumptions={"ad_cost_model": "observed_only"}, code_version="v1", generated_at=datetime(2026, 7, 26, tzinfo=timezone.utc))
    first, second = build_weekly_profit_digest(**kwargs), build_weekly_profit_digest(**kwargs)
    assert first.run_id == second.run_id and first.idempotency_key == second.idempotency_key
    assert first.realized_by_sku[0]["profit_cny"] == Decimal("1.123456789")


def test_weekly_digest_excludes_out_of_period_rows_and_records_the_exclusion():
    report = build_weekly_profit_digest(
        [
            {"order_id": "inside", "sku_id": "A", "currency": "CNY", "settlement_amount": "10", "cost_cny": "2", "statement_date": "2026-07-20"},
            {"order_id": "outside", "sku_id": "A", "currency": "CNY", "settlement_amount": "100", "cost_cny": "1", "statement_date": "2026-07-27"},
        ], period_start="2026-07-20", period_end="2026-07-26", generated_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
    )
    assert report.out_of_period_row_count == 1
    assert report.realized_by_sku[0]["profit_cny"] == Decimal("8")
    assert any(issue.code == "out_of_reporting_period" for issue in report.quality_issues)


def test_weekly_digest_keeps_realized_and_estimate_with_same_business_key():
    rows = [
        {"order_id": "same", "sku_id": "A", "channel": "tiktok", "region": "US", "currency": "CNY", "settlement_amount": "10", "cost_cny": "1", "statement_date": "2026-07-20"},
        {"order_id": "same", "sku_id": "A", "channel": "tiktok", "region": "US", "currency": "CNY", "settlement_amount": "20", "cost_cny": "1", "calculation_kind": "estimate", "statement_date": "2026-07-20"},
    ]
    report = build_weekly_profit_digest(rows, period_start="2026-07-20", period_end="2026-07-26", generated_at=datetime(2026, 7, 26, tzinfo=timezone.utc))
    assert report.deduplicated_row_count == 2
    assert report.realized_by_sku[0]["profit_cny"] == Decimal("9")
    assert report.estimate_by_sku[0]["profit_cny"] == Decimal("19")


def test_weekly_digest_separates_channels_and_preserves_negative_provenance():
    rows = [
        {"order_id": "tk", "sku_id": "A", "channel": "tiktok", "region": "US", "currency": "CNY", "settlement_amount": "10", "cost_cny": "1", "statement_date": "2026-07-20"},
        {"order_id": "sp", "sku_id": "A", "channel": "shopee", "region": "TH", "currency": "CNY", "settlement_amount": "2", "cost_cny": "5", "calculation_kind": "estimate", "statement_date": "2026-07-20"},
    ]
    report = build_weekly_profit_digest(rows, period_start="2026-07-20", period_end="2026-07-26", generated_at=datetime(2026, 7, 26, tzinfo=timezone.utc))
    assert report.realized_by_sku[0]["channel"] == "tiktok"
    negative = report.negative_profit_skus[0]
    assert (negative["calculation_kind"], negative["channel"], negative["region"], negative["sku_id"]) == ("estimate", "shopee", "TH", "A")


def test_weekly_digest_requires_audit_metadata_for_ready_status():
    row = {"order_id": "1", "sku_id": "A", "currency": "CNY", "settlement_amount": "2", "cost_cny": "1", "statement_date": "2026-07-20"}
    incomplete = build_weekly_profit_digest([row], period_start="2026-07-20", period_end="2026-07-26", generated_at=datetime(2026, 7, 26, tzinfo=timezone.utc))
    assert incomplete.status == "needs_review"
    assert {issue.code for issue in incomplete.quality_issues} >= {"missing_cost_version", "missing_code_version", "missing_fx_as_of"}
    complete = build_weekly_profit_digest([row], period_start="2026-07-20", period_end="2026-07-26", cost_version="sku-costs:2026-07-20", code_version="7689731", fx_source="approved-table", fx_as_of="2026-07-20", snapshot_id="fixture:1", generated_at=datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert complete.status == "ready"
    assert complete.payload()["input_snapshot"]["snapshot_id"] == "fixture:1"
