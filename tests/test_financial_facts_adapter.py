from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3

from domains.data_operations import adapt_financial_facts, adapt_sqlite_fixture


def test_adapts_decimal_safe_costs_and_negative_refund_to_json_ready_payload():
    result = adapt_financial_facts(
        {"SKU-1": {"cost_cny": "10.123456789012345678", "updated_at": 1_700_000_000}},
        [{"id": 7, "sku_id": "SKU-1", "settlement_amount": "-2.50", "currency": " usd ", "statement_date": "2026-07-01T10:30:00+08:00", "line_type": "refund", "region": "US"}],
    )

    assert [fact.amount for fact in result.facts] == [Decimal("10.123456789012345678"), Decimal("-2.50")]
    assert result.facts[1].fact_type == "refund"
    assert result.facts[1].currency == "USD"
    assert result.facts[0].sku_id == "SKU-1"
    assert result.facts[0].product_id is None
    assert result.facts[1].sku_id == "SKU-1"
    assert result.facts[1].region == "US"
    assert result.facts[1].channel is None
    payload = result.payload()
    assert payload["facts"][0]["amount"] == "10.123456789012345678"
    assert payload["facts"][1]["amount"] == "-2.50"
    json.dumps(payload)


def test_reports_missing_cost_currency_and_time_without_inventing_facts():
    result = adapt_financial_facts(
        {"NO-TIME": {"cost_cny": "5"}},
        [
            {"id": 1, "sku_id": "NO-COST", "settlement_amount": "8", "currency": "CNY", "statement_date": "2026-07-01"},
            {"id": 2, "sku_id": "NO-COST", "settlement_amount": "8", "statement_date": "2026-07-01"},
            {"id": 3, "sku_id": "NO-COST", "settlement_amount": "8", "currency": "CNY"},
        ],
    )

    assert len(result.facts) == 1
    assert {issue.code for issue in result.issues} == {"missing_cost", "missing_currency", "missing_occurred_at"}
    assert result.facts[0].occurred_at == datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_reads_temporary_sqlite_fixture_without_writing():
    connection = sqlite3.connect(":memory:")
    original_row_factory = connection.row_factory
    connection.executescript("""
        CREATE TABLE sku_costs (sku_id TEXT, cost_cny REAL, updated_at INTEGER);
        CREATE TABLE settlement_lines (id INTEGER, statement_id TEXT, statement_date TEXT, line_type TEXT, order_id TEXT, sku_id TEXT, region TEXT, currency TEXT, settlement_amount REAL);
        INSERT INTO sku_costs VALUES ('SKU-1', 3.25, 1700000000);
        INSERT INTO settlement_lines VALUES (1, 'S-1', '2026-07-02', 'fee', 'O-1', 'SKU-1', 'GB', 'GBP', -1.5);
    """)

    result = adapt_sqlite_fixture(connection)

    assert [(fact.fact_type, fact.amount, fact.currency) for fact in result.facts] == [
        ("cost", Decimal("3.25"), "CNY"),
        ("fee", Decimal("-1.5"), "GBP"),
    ]
    assert not result.issues
    assert connection.row_factory is original_row_factory


def test_single_cost_row_is_not_mistaken_for_a_sku_keyed_mapping():
    result = adapt_financial_facts(
        {"sku_id": "SKU-1", "cost_cny": "4.20", "updated_at": "2026-07-01"},
        [],
    )

    assert len(result.facts) == 1
    assert result.facts[0].sku_id == "SKU-1"
    assert result.facts[0].amount == Decimal("4.20")


def test_non_positive_cost_is_rejected_and_reported():
    result = adapt_financial_facts(
        {"SKU-0": {"cost_cny": "0", "updated_at": "2026-07-01"}},
        [{"id": 1, "sku_id": "SKU-0", "settlement_amount": "8", "currency": "CNY", "statement_date": "2026-07-01"}],
    )

    assert [issue.code for issue in result.issues] == ["invalid_cost", "invalid_cost"]
    assert [fact.fact_type for fact in result.facts] == ["settlement"]
