from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import ast
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from domains.data_operations.profit_settlement.knowledge_base import (
    ProfitKnowledgeBase,
)
from domains.data_operations.profit_settlement.audit import audit_profit_report
from domains.data_operations.profit_settlement.render import render_profit_report_html
from domains.data_operations.profit_settlement.shared_inputs import (
    CostSnapshot,
    FxSnapshot,
)
from domains.data_operations.profit_settlement.local_catalog import (
    enrich_settlement_row,
    load_local_catalog,
)
from domains.data_operations.profit_settlement.shopee import (
    build_monthly_report as build_shopee_monthly_report,
    build_weekly_report as build_shopee_weekly_report,
)
from domains.data_operations.profit_settlement.tiktok import (
    build_monthly_report as build_tiktok_monthly_report,
    build_weekly_report as build_tiktok_weekly_report,
)
from domains.data_operations.profit_settlement.ozon import (
    build_monthly_report as build_ozon_monthly_report,
    build_weekly_report as build_ozon_weekly_report,
)


NOW = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)


def _settlement_pull_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "domains"
        / "data_operations"
        / "skills"
        / "manage-profit-settlement"
        / "scripts"
        / "pull_settlement_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("profit_settlement_evidence_script", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _costs() -> CostSnapshot:
    return CostSnapshot.from_mapping(
        {
            "SKU-1": {
                "unit_cost_cny": "10.00",
                "version": "catalog-2026-08",
                "effective_at": "2026-08-01T00:00:00+00:00",
            }
        },
        snapshot_id="costs:test",
    )


def _fx() -> FxSnapshot:
    return FxSnapshot.from_mapping(
        {"THB": "0.20", "RUB": "0.08", "CNY": "1"},
        source="official-fx-test",
        as_of="2026-08-06T00:00:00+00:00",
        snapshot_id="fx:test",
    )


def _row(order_id: str, *, paid: str = "200", settlement: str = "100") -> dict:
    return {
        "order_id": order_id,
        "order_line_id": f"{order_id}:1",
        "shop_id": "th-main",
        "region": "TH",
        "platform_sku": "platform-001",
        "seller_sku": "SKU-1",
        "canonical_sku": "SKU-1",
        "product_name": "Detailed product",
        "variant_name": "Blue / Large",
        "image_url": "https://example.invalid/main.jpg",
        "currency": "THB",
        "quantity": "2",
        "buyer_paid_product_amount": paid,
        "net_settlement_amount": settlement,
        "settlement_status": "settled",
        "occurred_at": "2026-08-03T12:00:00+07:00",
        "settled_at": "2026-08-05T12:00:00+07:00",
        "unit_weight_g": "125.5",
        "package_weight_g": "280",
        "billable_weight_g": "300",
        "weight_source": "platform_fulfillment",
        "fee_items": [
            {
                "code": "commission",
                "label": "Platform commission",
                "amount": "12",
                "currency": "THB",
                "included_in_net_settlement": True,
            },
            {
                "code": "external_logistics",
                "label": "External logistics",
                "amount": "5",
                "currency": "THB",
                "included_in_net_settlement": False,
            },
        ],
        "source_snapshot_id": "settlement:test",
    }


def test_tiktok_weekly_report_has_detailed_order_profit_and_estimated_ads():
    report = build_tiktok_weekly_report(
        [_row("TK-1")],
        period_start="2026-08-03",
        period_end="2026-08-09",
        costs=_costs(),
        fx=_fx(),
        ad_rate="0.20",
        generated_at=NOW,
        code_version="test-v1",
    )

    assert report.status == "ready"
    assert report.calculation_kind == "realized_settlement_with_estimated_ads"
    assert report.totals["settlement_cny"] == Decimal("20.00")
    assert report.totals["product_cost_cny"] == Decimal("20.00")
    assert report.totals["advertising_cny"] == Decimal("8.0000")
    assert report.totals["external_costs_cny"] == Decimal("1.00")
    assert report.totals["profit_cny"] == Decimal("-9.0000")

    line = report.order_lines[0]
    assert line["product"]["image_url"].endswith("main.jpg")
    assert line["product"]["unit_weight_g"] == Decimal("125.5")
    assert line["cost"]["version"] == "catalog-2026-08"
    assert line["advertising"]["basis"] == "buyer_paid_product_amount"
    assert line["advertising"]["mode"] == "estimated_rate"
    assert line["fee_items"][0]["code"] == "commission"
    assert line["fee_items"][1]["included_in_net_settlement"] is False
    assert line["profit_cny"] == Decimal("-9.0000")
    assert report.payload()["totals"]["profit_cny"] == "-9.0000"


def test_local_catalog_is_read_only_versioned_and_enriches_weight_and_image(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (sku_id TEXT, shop_cipher TEXT, seller_sku TEXT,
          product_name TEXT, sku_name TEXT, image_url TEXT, currency TEXT,
          updated_at INTEGER);
        CREATE TABLE sku_costs (sku_id TEXT, cost_cny NUMERIC, updated_at INTEGER);
        CREATE TABLE shopee_products (seller_sku TEXT, product_name TEXT,
          model_name TEXT, image_url TEXT, currency TEXT, region TEXT,
          shop_id INTEGER, updated_at INTEGER);
        CREATE TABLE sku_logistics_weights (seller_sku TEXT, weight_g INTEGER,
          package_count INTEGER, depth_mm INTEGER, width_mm INTEGER,
          height_mm INTEGER, updated_at INTEGER);
        INSERT INTO products VALUES
          ('PLATFORM-1','TK-TH','12345','Widget','Blue','https://image.invalid/1.jpg','THB',10);
        INSERT INTO sku_costs VALUES ('PLATFORM-1',7.25,11);
        INSERT INTO shopee_products VALUES
          ('2345','Shopee Widget','Blue','https://image.invalid/s.jpg','THB','TH',22,12);
        INSERT INTO sku_logistics_weights VALUES ('2345',188,1,10,20,30,13);
        """
    )
    connection.commit()
    connection.close()

    catalog = load_local_catalog(path)
    enriched = enrich_settlement_row(
        {"platform_sku": "PLATFORM-1", "canonical_sku": "2345"}, catalog
    )

    assert catalog.seller_sku_by_platform_sku == {"PLATFORM-1": "2345"}
    assert catalog.costs_by_sku["2345"] == Decimal("7.25")
    assert catalog.snapshot_id.startswith("shop-db-catalog:")
    assert enriched["image_url"] == "https://image.invalid/1.jpg"
    assert enriched["unit_weight_g"] == 188
    # A mode=ro read cannot create SQLite sidecar files.
    assert sorted(item.name for item in tmp_path.iterdir()) == ["shop.db"]


def test_stage_one_shopee_evidence_preserves_components_without_guessing_net_inclusion():
    module = _settlement_pull_module()
    release = int(datetime(2026, 7, 31, 12, tzinfo=timezone.utc).timestamp())
    order = module._shopee_order(
        "TH",
        "ORDER-1",
        {"escrow_release_time": release, "payout_amount": "80"},
        {
            "order_income": {
                "escrow_amount_after_adjustment": "80",
                "buyer_total_amount": "100",
                "commission_fee": "-10",
                "service_fee": "-5",
                "items": [
                    {
                        "line_item_id": 11,
                        "model_sku": "0021",
                        "item_name": "Widget",
                        "quantity_purchased": 2,
                        "discounted_price": "50",
                    }
                ],
            }
        },
        module.SITE_TIMEZONES[("shopee", "TH")],
    )

    components = {item["code"]: item for item in order["financial_components"]}
    assert order["settlement_status"] == "settled"
    assert order["net_settlement_amount"] == Decimal("80")
    assert components["commission_fee"]["amount"] == Decimal("-10")
    assert components["service_fee"]["included_in_net_settlement"] == "unknown"
    assert order["items"][0]["quantity"] == Decimal("2")


def test_stage_one_blocked_receipt_never_claims_reads_writes_or_refresh():
    module = _settlement_pull_module()
    payload = module.failure_payload(
        "tiktok",
        "TH",
        datetime(2026, 7, 27).date(),
        datetime(2026, 8, 2).date(),
        module.SITE_TIMEZONES[("tiktok", "TH")],
        RuntimeError("token missing"),
    )

    assert payload["status"] == "blocked"
    assert payload["orders"] == []
    assert payload["receipt"] == {
        "external_reads_performed": [],
        "external_writes_performed": [],
        "credential_refresh_performed": False,
        "raw_response_retained": False,
    }


def test_stage_one_discovers_ozon_local_credentials_without_copying_them(tmp_path):
    module = _settlement_pull_module()
    path = tmp_path / "config" / "ozon.local.json"
    path.parent.mkdir()
    path.write_text(
        '{"client_id":"client-test","api_key":"secret-test"}',
        encoding="utf-8",
    )

    client_id, api_key, source = module._ozon_credentials(tmp_path)

    assert (client_id, api_key) == ("client-test", "secret-test")
    assert source == "config/ozon.local.json"
    assert sorted(item.name for item in path.parent.iterdir()) == ["ozon.local.json"]


def test_stage_one_uses_nonempty_tiktok_fallback_when_configured_file_is_empty(tmp_path):
    module = _settlement_pull_module()
    (tmp_path / "tiktok_tokens.json").write_text("", encoding="utf-8")
    fallback = tmp_path / "tiktok_tokens_livelyhive.json"
    fallback.write_text(
        '{"access_token":"access-test","refresh_token":"refresh-test",'
        '"access_token_expire_in":1}',
        encoding="utf-8",
    )

    credentials, source = module._tiktok_credentials(
        tmp_path, {"token_file": "tiktok_tokens.json"}
    )

    assert credentials["access_token"] == "access-test"
    assert source == "tiktok_tokens_livelyhive.json"


def test_stage_one_tiktok_refresh_uses_disposable_copy_and_preserves_source(tmp_path):
    module = _settlement_pull_module()
    configured = tmp_path / "tiktok_tokens.json"
    configured.write_text("", encoding="utf-8")
    source = tmp_path / "tiktok_tokens_livelyhive.json"
    source_payload = (
        '{"access_token":"expired-access","refresh_token":"valid-refresh",'
        '"access_token_expire_in":1,"refresh_token_expire_in":4102444800}'
    )
    source.write_text(source_payload, encoding="utf-8")
    observed = {}

    class FakeAuth:
        @staticmethod
        def token_path():
            raise AssertionError("temporary token path was not installed")

    class FakeConfig:
        _cache = None

    def fake_load_token():
        temporary_path = FakeAuth.token_path()
        observed["temporary_path"] = temporary_path
        payload = json.loads(temporary_path.read_text(encoding="utf-8"))
        payload["access_token"] = "refreshed-access"
        payload["access_token_expire_in"] = 4102444800
        temporary_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    with module._tiktok_credential_session(
        tmp_path,
        {"token_file": "tiktok_tokens.json"},
        allow_refresh=True,
        token_loader=fake_load_token,
        auth_module=FakeAuth,
        config_module=FakeConfig,
    ) as (credentials, credential_source, refreshed):
        assert credentials["access_token"] == "refreshed-access"
        assert credential_source == "tiktok_tokens_livelyhive.json"
        assert refreshed is True
        assert observed["temporary_path"].exists()
    assert source.read_text(encoding="utf-8") == source_payload
    assert configured.read_text(encoding="utf-8") == ""
    assert not observed["temporary_path"].exists()


def test_stage_one_tiktok_normalizes_statement_time_and_product_item():
    module = _settlement_pull_module()
    utc_time = datetime(2026, 7, 26, 18, 30, tzinfo=timezone.utc)
    settled_at = module._tiktok_statement_times(
        [{"id": "statement-1", "statement_time": int(utc_time.timestamp())}],
        module.SITE_TIMEZONES[("tiktok", "TH")],
    )["statement-1"]
    order = module._tiktok_row(
        "TH",
        {
            "Statement Date": "2026/07/26",
            "Statement ID": "statement-1",
            "Currency": "THB",
            "Type ": "Order",
            "Order/adjustment ID  ": "order-1",
            "Total settlement amount": "10.25",
            "SKU ID": "platform-sku-1",
            "Quantity": "2",
            "Product name": "Widget",
            "SKU name": "Blue",
            "Related order ID": "related-order-9",
        },
        0,
        settled_at,
    )

    assert settled_at == "2026-07-27T01:30:00+07:00"
    assert order["settled_at"] == settled_at
    assert order["transaction_type"] == "Order"
    assert order["related_order_id"] == "related-order-9"
    assert "related_order_id" not in {
        component["code"] for component in order["financial_components"]
    }
    assert order["items"] == [
        {
            "platform_sku": "platform-sku-1",
            "quantity": Decimal("2"),
            "product_name": "Widget",
            "variant_name": "Blue",
        }
    ]


def test_stage_one_tiktok_queries_inclusive_utc_statement_days():
    module = _settlement_pull_module()
    start, end = module._tiktok_period_bounds(
        datetime(2026, 7, 27).date(), datetime(2026, 8, 2).date()
    )

    assert start == datetime(2026, 7, 27, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 3, tzinfo=timezone.utc)


def test_stage_one_tiktok_groups_item_rows_into_one_settled_order():
    module = _settlement_pull_module()
    settled_at = "2026-07-27T07:00:00+07:00"
    rows = []
    for index, sku in enumerate(("sku-1", "sku-2")):
        rows.append(
            module._tiktok_row(
                "TH",
                {
                    "Statement ID": "statement-1",
                    "Currency": "THB",
                    "Type ": "Order",
                    "Order/adjustment ID  ": "order-1",
                    "Total settlement amount": "10.25",
                    "Subtotal after seller discounts": "12.00",
                    "SKU ID": sku,
                    "Quantity": "1",
                    "Product name": f"Widget {index}",
                    "SKU name": "Blue",
                },
                index,
                settled_at,
            )
        )

    records = module._aggregate_tiktok_records(rows)

    assert len(records) == 1
    assert records[0]["net_settlement_amount"] == Decimal("20.50")
    assert records[0]["buyer_total_amount"] == Decimal("24.00")
    assert len(records[0]["items"]) == 2
    assert records[0]["source_row_indices"] == [0, 1]


@pytest.mark.parametrize(
    "builder",
    (build_tiktok_weekly_report, build_shopee_weekly_report),
)
def test_tiktok_and_shopee_weekly_reports_default_to_22_percent_ads(builder):
    report = builder(
        [_row("DEFAULT-ADS")],
        period_start="2026-08-03",
        period_end="2026-08-09",
        costs=_costs(),
        fx=_fx(),
        generated_at=NOW,
        code_version="test-v1",
    )

    assert report.status == "ready"
    assert report.order_lines[0]["advertising"]["rate"] == Decimal("0.22")
    assert report.totals["advertising_cny"] == Decimal("8.8000")


def test_shopee_monthly_actual_ads_are_deterministically_allocated_by_paid_gmv():
    rows = [
        _row("SP-1", paid="100", settlement="100"),
        _row("SP-2", paid="300", settlement="100"),
    ]
    advertising = {
        "total_cny": "8",
        "source": "shopee-ads-api",
        "as_of": "2026-09-02T00:00:00+00:00",
        "snapshot_id": "ads:shopee:2026-08",
    }
    kwargs = dict(
        period_start="2026-08-01",
        period_end="2026-08-31",
        costs=_costs(),
        fx=_fx(),
        actual_advertising=advertising,
        generated_at=NOW,
        code_version="test-v1",
    )

    first = build_shopee_monthly_report(rows, **kwargs)
    reordered = build_shopee_monthly_report(list(reversed(rows)), **kwargs)

    assert first.status == "ready"
    assert first.calculation_kind == "realized_settlement_with_actual_ads"
    assert [line["advertising"]["amount_cny"] for line in first.order_lines] == [
        Decimal("2.00"),
        Decimal("6.00"),
    ]
    # 40 settlement - 40 goods - 8 ads - 2 external logistics.
    assert first.totals["profit_cny"] == Decimal("-10.00")
    assert first.idempotency_key == reordered.idempotency_key
    assert first.payload() == reordered.payload()


def test_ozon_monthly_requires_actual_advertising_and_preserves_platform_fees():
    row = {
        **_row("OZ-1", paid="1000", settlement="1000"),
        "region": "RU",
        "currency": "RUB",
        "quantity": "1",
        "fee_items": [
            {
                "code": "sale_commission",
                "label": "Sales commission",
                "amount": "150",
                "currency": "RUB",
                "included_in_net_settlement": True,
            }
        ],
    }
    missing = build_ozon_monthly_report(
        [row],
        period_start="2026-08-01",
        period_end="2026-08-31",
        costs=_costs(),
        fx=_fx(),
        generated_at=NOW,
        code_version="test-v1",
    )
    assert missing.status == "needs_review"
    assert "missing_actual_advertising" in {issue.code for issue in missing.quality_issues}

    ready = build_ozon_monthly_report(
        [{**row, "actual_ad_cost_cny": "5"}],
        period_start="2026-08-01",
        period_end="2026-08-31",
        costs=_costs(),
        fx=_fx(),
        generated_at=NOW,
        code_version="test-v1",
    )
    assert ready.status == "ready"
    assert ready.order_lines[0]["fee_items"][0]["code"] == "sale_commission"
    # 80 settlement - 10 goods - 5 actual ads; commission is already netted.
    assert ready.order_lines[0]["profit_cny"] == Decimal("65.00")


def test_missing_cost_and_fx_fail_closed_without_zero_profit_fabrication():
    report = build_tiktok_weekly_report(
        [{**_row("TK-MISSING"), "canonical_sku": "UNKNOWN", "currency": "PHP"}],
        period_start="2026-08-03",
        period_end="2026-08-09",
        costs=_costs(),
        fx=_fx(),
        ad_rate="0.20",
        generated_at=NOW,
        code_version="test-v1",
    )

    assert report.status == "needs_review"
    assert report.order_lines == ()
    assert {issue.code for issue in report.quality_issues} >= {"missing_cost", "missing_fx"}
    assert report.totals["profit_cny"] == Decimal("0")


def test_unsettled_and_unknown_orders_never_enter_profit():
    report = build_tiktok_weekly_report(
        [
            _row("TK-SETTLED"),
            {**_row("TK-PENDING"), "settlement_status": "processing"},
            {**_row("TK-UNKNOWN"), "settlement_status": ""},
        ],
        period_start="2026-08-03",
        period_end="2026-08-09",
        costs=_costs(),
        fx=_fx(),
        ad_rate="0.20",
        generated_at=NOW,
        code_version="test-v1",
    )

    assert [line["identity"]["order_id"] for line in report.order_lines] == ["TK-SETTLED"]
    assert report.source["unsettled_row_count"] == 2
    assert report.status == "ready"


def test_reporting_period_uses_settlement_date_not_order_date():
    row = {
        **_row("TK-LATE-SETTLEMENT"),
        "occurred_at": "2026-07-01T12:00:00+07:00",
        "settled_at": "2026-08-05T12:00:00+07:00",
    }
    report = build_tiktok_weekly_report(
        [row], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), ad_rate="0.20", generated_at=NOW, code_version="test-v1",
    )
    assert len(report.order_lines) == 1
    assert report.order_lines[0]["occurred_at"].date().isoformat() == "2026-07-01"
    assert report.order_lines[0]["settled_at"].date().isoformat() == "2026-08-05"


def test_all_platforms_offer_weekly_and_monthly_without_cross_platform_inputs():
    actual_ads = {
        "total_cny": "4",
        "source": "platform-ads-api",
        "as_of": "2026-09-02T00:00:00+00:00",
        "snapshot_id": "ads:test",
    }
    tiktok_month = build_tiktok_monthly_report(
        [_row("TK-MONTH")], period_start="2026-08-01", period_end="2026-08-31",
        costs=_costs(), fx=_fx(), actual_advertising=actual_ads,
        generated_at=NOW, code_version="test-v1",
    )
    shopee_week = build_shopee_weekly_report(
        [_row("SP-WEEK")], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), ad_rate="0.20",
        generated_at=NOW, code_version="test-v1",
    )
    ozon_week = build_ozon_weekly_report(
        [{**_row("OZ-WEEK", paid="1000", settlement="1000"), "region": "RU", "currency": "RUB", "quantity": "1", "actual_ad_cost_cny": "5"}],
        period_start="2026-08-03", period_end="2026-08-09", costs=_costs(), fx=_fx(),
        generated_at=NOW, code_version="test-v1",
    )

    assert tiktok_month.period_kind == "monthly"
    assert tiktok_month.order_lines[0]["identity"]["platform"] == "tiktok"
    assert shopee_week.period_kind == "weekly"
    assert shopee_week.order_lines[0]["identity"]["platform"] == "shopee"
    assert ozon_week.period_kind == "weekly"
    assert ozon_week.order_lines[0]["identity"]["platform"] == "ozon"


def test_approved_monthly_report_enters_immutable_local_knowledge_base(tmp_path):
    report = build_shopee_monthly_report(
        [_row("SP-KB", paid="100", settlement="100")],
        period_start="2026-08-01",
        period_end="2026-08-31",
        costs=_costs(),
        fx=_fx(),
        actual_advertising={
            "total_cny": "2",
            "source": "shopee-ads-api",
            "as_of": "2026-09-02T00:00:00+00:00",
            "snapshot_id": "ads:shopee:2026-08",
        },
        generated_at=NOW,
        code_version="test-v1",
    )
    store = ProfitKnowledgeBase(tmp_path)

    stored = store.approve_monthly_report(
        report.payload(),
        approved_by="Kyle",
        approved_at="2026-09-03T08:00:00+08:00",
        approval_note="Monthly close checked",
    )
    duplicate = store.approve_monthly_report(
        report.payload(),
        approved_by="Kyle",
        approved_at="2026-09-03T08:00:00+08:00",
        approval_note="Monthly close checked",
    )

    assert stored.created is True
    assert duplicate.created is False
    assert stored.path.read_bytes() == duplicate.path.read_bytes()
    found = store.list_reports(platform="shopee", year=2026, month=8)
    assert len(found) == 1
    assert found[0]["approval"]["status"] == "APPROVED"
    assert found[0]["approval"]["approved_by"] == "Kyle"

    weekly = build_tiktok_weekly_report(
        [_row("TK-WEEK")],
        period_start="2026-08-03",
        period_end="2026-08-09",
        costs=_costs(),
        fx=_fx(),
        ad_rate="0.20",
        generated_at=NOW,
        code_version="test-v1",
    )
    with pytest.raises(ValueError, match="monthly"):
        store.approve_monthly_report(
            weekly.payload(),
            approved_by="Kyle",
            approved_at="2026-09-03T08:00:00+08:00",
        )


def test_second_pass_audit_recomputes_every_order_and_detects_tampering():
    report = build_tiktok_weekly_report(
        [_row("TK-AUDIT")], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), ad_rate="0.20", generated_at=NOW, code_version="test-v1",
    ).payload()
    passed = audit_profit_report(report)
    assert passed.status == "PASSED"
    assert passed.checked_order_line_count == 1

    report["order_lines"][0]["profit_cny"] = "999"
    failed = audit_profit_report(report)
    assert failed.status == "FAILED"
    assert {finding.code for finding in failed.findings} >= {"profit_mismatch", "total_mismatch"}


def test_knowledge_base_rejects_secret_or_raw_response_fields(tmp_path):
    report = build_shopee_monthly_report(
        [_row("SP-SECRET")], period_start="2026-08-01", period_end="2026-08-31",
        costs=_costs(), fx=_fx(), actual_advertising={"total_cny": "2", "source": "ads-api", "as_of": "2026-09-02", "snapshot_id": "ads:test"},
        generated_at=NOW, code_version="test-v1",
    ).payload()
    report["raw_response"] = {"access_token": "must-not-persist"}
    with pytest.raises(ValueError, match="forbidden"):
        ProfitKnowledgeBase(tmp_path).approve_monthly_report(
            report, approved_by="Kyle", approved_at="2026-09-03T08:00:00+08:00"
        )


def test_detailed_html_renders_main_image_weight_cost_ads_fees_and_profit():
    report = build_tiktok_weekly_report(
        [_row("TK-HTML")], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), ad_rate="0.20", generated_at=NOW, code_version="test-v1",
    ).payload()
    html = render_profit_report_html(report)
    for expected in ("商品主图", "Detailed product", "SKU-1", "125.5g", "Platform commission", "广告成本", "利润 CNY"):
        assert expected in html


def test_platform_engines_do_not_import_each_other():
    root = Path(__file__).parents[1] / "domains" / "data_operations" / "profit_settlement"
    for platform in ("tiktok", "shopee", "ozon"):
        tree = ast.parse((root / f"{platform}.py").read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any(other in name for other in ("tiktok", "shopee", "ozon") if other != platform for name in imported)
