from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from domains.data_operations.profit_settlement.knowledge_base import (
    ProfitKnowledgeBase,
)
from domains.data_operations.profit_settlement.audit import audit_profit_report
from domains.data_operations.profit_settlement.render import (
    _visible_base_headers,
    render_profit_report_html,
)
from domains.data_operations.profit_settlement.shared_inputs import (
    CostSnapshot,
    FxSnapshot,
)
from domains.data_operations.profit_settlement.local_catalog import (
    enrich_settlement_row,
    load_local_catalog,
)
from domains.data_operations.profit_settlement.cost_policy import (
    POLICY_VERSION,
    resolve_temporary_cost_policy,
)
from domains.data_operations.profit_settlement.settlement_evidence_adapter import (
    adapt_settlement_evidence,
)
from domains.data_operations.profit_settlement.weekly_evidence_bundle import (
    build_weekly_evidence_bundle,
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


def _weekly_build_script_module():
    path = Path(__file__).parents[1] / "domains" / "data_operations" / "skills" / "manage-profit-settlement" / "scripts" / "build_weekly_from_evidence.py"
    spec = importlib.util.spec_from_file_location("profit_weekly_build_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog_stub():
    return SimpleNamespace(
        seller_sku_by_platform_sku={"platform-1": "0001", "platform-2": "0002"},
        costs_by_sku={"0001": Decimal("1"), "0002": Decimal("2")},
        product_by_platform_sku={
            "platform-1": {"image_url": "https://example.test/1.jpg", "shop_id": "TH"},
            "platform-2": {"image_url": "https://example.test/2.jpg", "shop_id": "TH"},
        },
        product_by_seller_sku={},
        weight_by_seller_sku={
            "0001": {"unit_weight_g": 100, "weight_source": "fixture"},
            "0002": {"unit_weight_g": 200, "weight_source": "fixture"},
        },
    )


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
        "fulfillment": {
            "mode": "cross_border",
            "classification_rule": "import_vat_and_duty_presence/v2",
            "import_vat_local": "13",
            "import_duty_local": "37",
        },
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
        order_created_at="2026-07-29T10:15:00+07:00",
    )

    components = {item["code"]: item for item in order["financial_components"]}
    assert order["settlement_status"] == "settled"
    assert order["net_settlement_amount"] == Decimal("80")
    assert components["commission_fee"]["amount"] == Decimal("-10")
    assert components["service_fee"]["included_in_net_settlement"] == "unknown"
    assert order["items"][0]["quantity"] == Decimal("2")
    assert order["order_created_at"] == "2026-07-29T10:15:00+07:00"


def test_stage_one_shopee_reads_official_order_created_at_in_batches():
    module = _settlement_pull_module()
    zone = module.SITE_TIMEZONES[("shopee", "TH")]
    order_sns = [f"ORDER-{index:03d}" for index in range(51)]
    timestamp = int(datetime(2026, 7, 29, 3, 15, tzinfo=timezone.utc).timestamp())
    calls = []

    def fake_get(path, shop_id, token, params):
        calls.append((path, shop_id, token, params))
        return {
            "response": {
                "order_list": [
                    {"order_sn": order_sn, "create_time": timestamp}
                    for order_sn in params["order_sn_list"].split(",")
                ]
            }
        }

    result, issues = module._shopee_order_created_times(
        123, "redacted-token", order_sns, zone, request_get=fake_get
    )

    assert issues == []
    assert len(calls) == 2
    assert all(call[0] == "/api/v2/order/get_order_detail" for call in calls)
    assert all(call[3]["response_optional_fields"] == "create_time" for call in calls)
    assert result["ORDER-000"] == "2026-07-29T10:15:00+07:00"


def test_stage_one_supports_shopee_my_ph_and_vn_reporting_timezones():
    module = _settlement_pull_module()

    expected = {
        "MY": ("Asia/Kuala_Lumpur", 8),
        "PH": ("Asia/Manila", 8),
        "VN": ("Asia/Ho_Chi_Minh", 7),
    }
    for site, (name, hours) in expected.items():
        zone = module.SITE_TIMEZONES[("shopee", site)]
        assert zone.tzname(None) == name
        assert zone.utcoffset(None).total_seconds() == hours * 3600


def test_stage_one_shopee_refresh_requires_explicit_operator_opt_in(tmp_path):
    module = _settlement_pull_module()
    zone = module.SITE_TIMEZONES[("shopee", "MY")]
    (tmp_path / "shopee_tokens.json").write_text(
        json.dumps(
            {
                "sync_shop_ids": {"MY": 123},
                "shops": {
                    "123": {
                        "access_token": "expired-token",
                        "refresh_token": "redacted-refresh-token",
                        "expire_at": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps({"shopee": {"token_file": "shopee_tokens.json"}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="explicit --allow-credential-refresh"):
        module.pull_shopee(
            tmp_path,
            "MY",
            datetime(2026, 8, 3).date(),
            datetime(2026, 8, 9).date(),
            zone,
        )


def test_stage_one_tiktok_reads_only_official_order_created_at():
    module = _settlement_pull_module()
    zone = module.SITE_TIMEZONES[("tiktok", "TH")]
    timestamp = int(datetime(2026, 7, 29, 3, 15, tzinfo=timezone.utc).timestamp())
    calls = []

    def fake_fetcher(token, cipher, order_ids):
        calls.append((token, cipher, order_ids))
        return {
            "ORDER-1": {"id": "ORDER-1", "create_time": timestamp},
            "ORDER-2": {"id": "ORDER-2", "paid_time": timestamp + 10},
        }

    result, issues = module._tiktok_order_created_times(
        "redacted-token",
        "shop-cipher",
        ["ORDER-2", "ORDER-1", "ORDER-1"],
        zone,
        fetcher=fake_fetcher,
    )

    assert calls == [("redacted-token", "shop-cipher", ["ORDER-1", "ORDER-2"])]
    assert result == {"ORDER-1": "2026-07-29T10:15:00+07:00"}
    assert issues == [
        {
            "code": "missing_order_created_at",
            "record_id": "ORDER-2",
            "field": "create_time",
            "message": "ORDER-2 has invalid create_time",
        }
    ]


def test_stage_one_tiktok_keeps_order_time_but_drops_fulfillment_operator():
    module = _settlement_pull_module()
    zone = module.SITE_TIMEZONES[("tiktok", "TH")]
    timestamp = int(datetime(2026, 7, 29, 3, 15, tzinfo=timezone.utc).timestamp())

    facts, issues = module._tiktok_order_facts(
        "redacted-token",
        "shop-cipher",
        ["ORDER-1"],
        zone,
        fetcher=lambda *_args: {
            "ORDER-1": {
                "id": "ORDER-1",
                "create_time": timestamp,
                "fulfillment_type": "FULFILLMENT_BY_SELLER",
                "delivery_type": "HOME_DELIVERY",
                "shipping_type": "TIKTOK",
                "delivery_option_name": "Standard shipping",
                "warehouse_id": "warehouse-redacted",
            }
        },
    )

    assert issues == []
    assert facts["ORDER-1"] == {
        "order_created_at": "2026-07-29T10:15:00+07:00",
    }


def test_stage_one_tiktok_preserves_import_tax_components():
    module = _settlement_pull_module()
    row = module._tiktok_row(
        "TH",
        {
            "Order/adjustment ID  ": "ORDER-1",
            "Statement ID": "STATEMENT-1",
            "Type ": "Order",
            "Currency": "THB",
            "SKU ID": "platform-1",
            "Quantity": "1",
            "Total settlement amount": "124.87",
            "Subtotal after seller discounts": "205.66",
            "Customs duty": "-13.00",
            "Import VAT": "-14.69",
        },
        0,
        "2026-08-10T07:00:00+07:00",
    )

    components = {item["code"]: item["amount"] for item in row["financial_components"]}
    assert components["customs_duty"] == Decimal("-13.00")
    assert components["import_vat"] == Decimal("-14.69")


def test_stage_one_tiktok_missing_import_tax_is_blocking_evidence_issue():
    module = _settlement_pull_module()
    issues = module._tiktok_import_tax_issues([{
        "order_id": "ORDER-1",
        "transaction_type": "Order",
        "financial_components": [
            {"code": "customs_duty", "amount": Decimal("0")},
        ],
    }])

    assert [issue["code"] for issue in issues] == [
        "missing_fulfillment_tax_evidence"
    ]
    assert "import_vat" in issues[0]["message"]


def test_tiktok_line_expansion_preserves_missing_tax_as_missing():
    import tiktok_settlement

    row = tiktok_settlement.base_row(
        "STATEMENT-1",
        0,
        "THB",
        {
            "type": "ORDER",
            "order_id": "ORDER-1",
            "settlement_amount": "10",
        },
    )
    expanded = tiktok_settlement.expand_order_rows(
        row,
        {},
        {"line_items": [{"sku_id": "platform-1", "sale_price": "12"}]},
    )

    assert expanded[0]["Customs duty"] == ""
    assert expanded[0]["Import VAT"] == ""


def test_tiktok_finance_v202501_maps_nested_tax_and_fee_breakdowns(monkeypatch):
    import tiktok_settlement

    calls = []
    monkeypatch.setattr(
        tiktok_settlement,
        "paginate_get",
        lambda token, path, query, list_key: calls.append(
            (token, path, query, list_key)
        ) or [],
    )
    tiktok_settlement.fetch_statement_transactions("token", "cipher", "STATEMENT-1")

    assert calls[0][1] == "/finance/202501/statements/STATEMENT-1/statement_transactions"
    assert calls[0][3] == "transactions"

    row = tiktok_settlement.base_row(
        "STATEMENT-1",
        0,
        "THB",
        {
            "type": "ORDER",
            "order_id": "ORDER-1",
            "settlement_amount": "124.87",
            "revenue_amount": "205.66",
            "fee_tax_amount": "-66.79",
            "adjustment_amount": "0",
            "revenue_breakdown": {
                "subtotal_before_discount_amount": "220",
                "seller_discount_amount": "-14.34",
            },
            "shipping_cost_breakdown": {
                "actual_shipping_fee_amount": "-14",
                "customer_paid_shipping_fee_amount": "0",
            },
            "fee_tax_breakdown": {
                "fee": {
                    "transaction_fee_amount": "-6.6",
                    "platform_commission_amount": "-17.14",
                },
                "tax": {
                    "customs_duty_amount": "-13",
                    "import_vat_amount": "-14.69",
                },
            },
            "supplementary_component": {"customer_payment_amount": "205.66"},
        },
    )

    assert row["Subtotal after seller discounts"] == 205.66
    assert row["Customs duty"] == -13.0
    assert row["Import VAT"] == -14.69
    assert row["Transaction fee"] == -6.6


@pytest.mark.parametrize(
    ("import_vat", "customs_duty", "expected_mode"),
    (("-14.69", "-13.00", "cross_border"), ("0", "0", "local")),
)
def test_tiktok_th_fulfillment_uses_import_tax_not_operator(
    import_vat, customs_duty, expected_mode
):
    evidence = {
        "schema_version": "settlement-evidence/v1",
        "status": "ready",
        "platform": "tiktok",
        "site": "TH",
        "snapshot_id": "tiktok-settlement:tax-fixture",
        "checksum": "tax-fixture",
        "net_settlement_total_local": "124.87",
        "receipt": {"external_writes_performed": []},
        "orders": [{
            "order_id": "ORDER-1",
            "statement_id": "STATEMENT-1",
            "transaction_type": "Order",
            "settlement_status": "settled",
            "settled_at": "2026-08-10T07:00:00+07:00",
            "currency": "THB",
            "net_settlement_amount": "124.87",
            "buyer_total_amount": "205.66",
            "items": [{"platform_sku": "platform-1", "quantity": "1"}],
            "financial_components": [
                {"code": "import_vat", "amount": import_vat, "currency": "THB"},
                {"code": "customs_duty", "amount": customs_duty, "currency": "THB"},
            ],
            "fulfillment": {"fulfillment_type": "FULFILLMENT_BY_SELLER"},
        }],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "ready"
    fulfillment = result.rows[0]["fulfillment"]
    assert fulfillment["mode"] == expected_mode
    assert fulfillment["classification_rule"] == "tiktok_th_import_tax_charged/v1"
    assert "fulfillment_type" not in fulfillment


def test_tiktok_th_missing_import_tax_evidence_needs_review():
    evidence = {
        "schema_version": "settlement-evidence/v1",
        "status": "ready",
        "platform": "tiktok",
        "site": "TH",
        "snapshot_id": "tiktok-settlement:missing-tax",
        "checksum": "missing-tax",
        "net_settlement_total_local": "10",
        "receipt": {"external_writes_performed": []},
        "orders": [{
            "order_id": "ORDER-1",
            "statement_id": "STATEMENT-1",
            "transaction_type": "Order",
            "settlement_status": "settled",
            "settled_at": "2026-08-10T07:00:00+07:00",
            "currency": "THB",
            "net_settlement_amount": "10",
            "buyer_total_amount": "12",
            "items": [{"platform_sku": "platform-1", "quantity": "1"}],
            "financial_components": [],
        }],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "needs_review"
    assert result.rows[0]["fulfillment"]["mode"] == "unknown"
    assert {issue.code for issue in result.issues} == {
        "missing_fulfillment_tax_evidence"
    }


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


def test_stage_two_adapts_tiktok_orders_and_replaces_actual_ads_with_weekly_rate():
    evidence = {
        "schema_version": "settlement-evidence/v1",
        "status": "ready",
        "platform": "tiktok",
        "site": "TH",
        "snapshot_id": "tiktok-settlement:test",
        "checksum": "evidence-checksum",
        "net_settlement_total_local": "90",
        "receipt": {"external_writes_performed": []},
        "orders": [
            {
                "order_id": "order-1",
                "statement_id": "statement-1",
                "transaction_type": "Order",
                "settlement_status": "settled",
                "settled_at": "2026-07-27T07:00:00+07:00",
                "currency": "THB",
                "net_settlement_amount": "100",
                "buyer_total_amount": "200",
                "items": [
                    {"platform_sku": "platform-1", "quantity": "1", "product_name": "One"},
                    {"platform_sku": "platform-2", "quantity": "3", "product_name": "Two"},
                ],
                    "financial_components": [
                        {"code": "commission", "amount": "-20", "currency": "THB"},
                        {"code": "import_vat", "amount": "0", "currency": "THB"},
                        {"code": "customs_duty", "amount": "0", "currency": "THB"},
                ],
            },
            {
                "order_id": "adjustment-1",
                "statement_id": "statement-1",
                "transaction_type": "GMV Payment for TikTok Ads",
                "settlement_status": "settled",
                "settled_at": "2026-07-27T07:00:00+07:00",
                "currency": "THB",
                "net_settlement_amount": "-10",
                "buyer_total_amount": "0",
                "items": [],
                "financial_components": [],
            },
        ],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "ready"
    assert [row["canonical_sku"] for row in result.rows] == ["0001", "0002"]
    assert [row["net_settlement_amount"] for row in result.rows] == [
        Decimal("25"),
        Decimal("75"),
    ]
    assert [row["buyer_paid_product_amount"] for row in result.rows] == [
        Decimal("50"),
        Decimal("150"),
    ]
    assert result.rows[0]["fee_items"][0]["amount"] == Decimal("-5")
    assert result.rows[0]["fee_items"][0]["included_in_net_settlement"] is True
    assert result.rows[0]["allocation_basis"] == "quantity_share"
    assert result.reconciliation == {
        "official_net_settlement_local": Decimal("90"),
        "included_order_net_settlement_local": Decimal("100"),
        "excluded_actual_advertising_local": Decimal("-10"),
        "unallocated_local": Decimal("0"),
        "tolerance_local": Decimal("0.000000000001"),
    }

    report = build_tiktok_weekly_report(
        result.rows,
        period_start="2026-07-27",
        period_end="2026-08-02",
        costs=CostSnapshot.from_mapping(
            {
                "0001": {"unit_cost_cny": "1", "version": "fixture-v1"},
                "0002": {"unit_cost_cny": "2", "version": "fixture-v1"},
            },
            snapshot_id="costs:stage-two-fixture",
        ),
        fx=FxSnapshot.from_mapping(
            {"THB": "0.2"}, source="fixture-fx", as_of="2026-08-03"
        ),
        ad_rate="0.22",
        generated_at=NOW,
        code_version="stage-two-test",
    ).payload()
    assert report["status"] == "ready"
    assert report["totals"] == {
        "settlement_cny": "20.0",
        "product_cost_cny": "7",
        "advertising_cny": "8.800",
        "local_fulfillment_cost_cny": "4.00",
        "external_costs_cny": "4.00",
        "profit_cny": "0.200",
    }
    assert audit_profit_report(report).status == "PASSED"


def test_stage_two_consolidates_same_period_sale_and_refund_without_repeating_cost_or_ads():
    evidence = {
        "schema_version": "settlement-evidence/v1",
        "status": "ready",
        "platform": "tiktok",
        "site": "TH",
        "snapshot_id": "tiktok-settlement:repeated-order",
        "checksum": "repeated-order",
        "net_settlement_total_local": "70",
        "receipt": {"external_writes_performed": []},
        "orders": [
            {
                "order_id": "order-1", "statement_id": "statement-sale",
                "transaction_type": "Order", "settled_at": "2026-08-06T07:00:00+07:00",
                "currency": "THB", "net_settlement_amount": "100",
                "buyer_total_amount": "120",
                "items": [{"platform_sku": "platform-1", "quantity": "1"}],
                "financial_components": [
                    {"code": "customer_payment", "amount": "120", "currency": "THB"},
                    {"code": "import_vat", "amount": "0", "currency": "THB"},
                    {"code": "customs_duty", "amount": "0", "currency": "THB"},
                ],
            },
            {
                "order_id": "order-1", "statement_id": "statement-refund",
                "transaction_type": "Order", "settled_at": "2026-08-07T07:00:00+07:00",
                "currency": "THB", "net_settlement_amount": "-30",
                "buyer_total_amount": "0",
                "items": [{"platform_sku": "platform-1", "quantity": "1"}],
                "financial_components": [
                    {"code": "customer_refund", "amount": "-30", "currency": "THB"},
                    {"code": "import_vat", "amount": "0", "currency": "THB"},
                    {"code": "customs_duty", "amount": "0", "currency": "THB"},
                ],
            },
        ],
    }

    adapted = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert adapted.status == "ready"
    assert len(adapted.rows) == 1
    assert adapted.rows[0]["net_settlement_amount"] == Decimal("70")
    assert adapted.rows[0]["buyer_paid_product_amount"] == Decimal("120")
    assert [fact["fact_id"] for fact in adapted.rows[0]["source_settlement_facts"]] == [
        "statement-sale", "statement-refund",
    ]
    report = build_tiktok_weekly_report(
        adapted.rows,
        period_start="2026-08-03", period_end="2026-08-09",
        costs=CostSnapshot.from_mapping(
            {"0001": {"unit_cost_cny": "5", "version": "fixture-v1"}},
            snapshot_id="costs:fixture",
        ),
        fx=FxSnapshot.from_mapping({"THB": "0.2"}, source="fixture-fx", as_of="2026-08-10"),
        ad_rate="0.22", generated_at=NOW, code_version="stage-two-test",
    ).payload()
    assert report["totals"]["product_cost_cny"] == "5"
    assert report["totals"]["advertising_cny"] == "5.280"
    assert [fact["fact_id"] for fact in report["order_lines"][0]["source_settlement_facts"]] == [
        "statement-sale", "statement-refund",
    ]
    assert audit_profit_report(report).status == "PASSED"


def test_stage_two_allows_sub_minor_unit_decimal_reconciliation_noise():
    evidence = {
        "schema_version": "settlement-evidence/v1", "status": "ready",
        "platform": "tiktok", "site": "TH", "snapshot_id": "tiktok-settlement:tail",
        "checksum": "tail", "net_settlement_total_local": "1.00000000000000000000001",
        "receipt": {"external_writes_performed": []},
        "orders": [{
            "order_id": "order-tail", "statement_id": "statement-tail",
            "transaction_type": "Order", "settled_at": "2026-08-06T07:00:00+07:00",
            "currency": "THB", "net_settlement_amount": "1", "buyer_total_amount": "1",
            "items": [{"platform_sku": "platform-1", "quantity": "1"}],
            "financial_components": [
                {"code": "import_vat", "amount": "0", "currency": "THB"},
                {"code": "customs_duty", "amount": "0", "currency": "THB"},
            ],
        }],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "ready"
    assert result.reconciliation["unallocated_local"] == Decimal("1E-23")


def test_stage_two_keeps_missing_sku_mapping_as_a_blocking_row():
    evidence = {
        "schema_version": "settlement-evidence/v1",
        "status": "ready",
        "platform": "tiktok",
        "site": "TH",
        "snapshot_id": "tiktok-settlement:missing-map",
        "checksum": "missing-map",
        "net_settlement_total_local": "10",
        "receipt": {"external_writes_performed": []},
        "orders": [{
            "order_id": "order-missing",
            "transaction_type": "Order",
            "settlement_status": "settled",
            "settled_at": "2026-07-27T07:00:00+07:00",
            "currency": "THB",
            "net_settlement_amount": "10",
            "buyer_total_amount": "12",
            "items": [{"platform_sku": "unknown-platform", "quantity": "1"}],
            "financial_components": [
                {"code": "import_vat", "amount": "0", "currency": "THB"},
                {"code": "customs_duty", "amount": "0", "currency": "THB"},
            ],
        }],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "needs_review"
    assert result.rows[0]["canonical_sku"] == ""
    assert {issue.code for issue in result.issues} == {"missing_seller_sku_mapping"}


def test_stage_two_rejects_evidence_that_claims_external_writes():
    evidence = {
        "schema_version": "settlement-evidence/v1",
        "status": "ready",
        "platform": "shopee",
        "site": "TH",
        "snapshot_id": "shopee-settlement:unsafe",
        "checksum": "unsafe",
        "net_settlement_total_local": "0",
        "receipt": {"external_writes_performed": ["unexpected"]},
        "orders": [],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "blocked"
    assert result.rows == ()
    assert {issue.code for issue in result.issues} == {"external_write_claimed"}


def test_stage_two_bundle_supports_global_and_platform_ad_rate_overrides():
    def evidence(platform, site, source_sku):
        item_key = "platform_sku" if platform != "shopee" else "seller_sku"
        return {
            "schema_version": "settlement-evidence/v1",
            "status": "ready",
            "platform": platform,
            "site": site,
            "snapshot_id": f"{platform}-settlement:fixture",
            "checksum": f"{platform}-checksum",
            "net_settlement_total_local": "100",
            "receipt": {"external_writes_performed": []},
            "orders": [{
                "order_id": f"{platform}-order",
                "transaction_type": "Order",
                "settled_at": "2026-07-27T07:00:00+07:00",
                "currency": "RUB" if platform == "ozon" else "THB",
                "net_settlement_amount": "100",
                "buyer_total_amount": "120",
                "items": [{item_key: source_sku, "quantity": "1", "discounted_price": "120"}],
                "financial_components": (
                    [{"code": "OperationAgentDeliveredToCustomer", "amount": "120"}]
                    if platform == "ozon"
                    else [
                        {"code": "fee", "amount": "-2"},
                        {"code": "buyer_paid_shipping_fee", "amount": "0"},
                        *(
                            [{"code": "vat_on_imported_goods", "amount": "13"}, {"code": "th_import_duty", "amount": "37"}, *([{"code": "sales_tax_on_lvg", "amount": "0"}] if site == "MY" else [])]
                            if platform == "shopee"
                            else [
                                {"code": "import_vat", "amount": "0"},
                                {"code": "customs_duty", "amount": "0"},
                            ]
                        ),
                    ]
                ),
            }],
        }

    bundle = build_weekly_evidence_bundle(
        {
            "tiktok": evidence("tiktok", "TH", "platform-1"),
            "shopee": evidence("shopee", "TH", "1"),
            "ozon": evidence("ozon", "RU", "ozon-1"),
        },
        _catalog_stub(),
        period_start="2026-07-27",
        period_end="2026-08-02",
        costs=CostSnapshot.from_mapping(
            {"0001": {"unit_cost_cny": "10", "version": "fixture-v1"}},
            snapshot_id="costs:fixture",
        ),
        fx=FxSnapshot.from_mapping(
            {"THB": "0.2", "RUB": "0.08"}, source="fixture-fx", as_of="2026-08-03"
        ),
        seller_sku_by_ozon_sku={"ozon-1": "1"},
        ad_rate=Decimal("0.15"),
        ad_rate_source="operator_global_override",
        ad_rates={"shopee": Decimal("0.19"), "ozon": Decimal("0.18")},
        generated_at=NOW,
        code_version="stage-two-test",
    )

    assert bundle["status"] == "ready"
    assert bundle["reports"]["tiktok"]["status"] == "ready"
    assert bundle["reports"]["shopee"]["status"] == "ready"
    assert bundle["reports"]["ozon"]["status"] == "ready"
    assert bundle["reports"]["ozon"]["report"]["order_lines"][0]["advertising"] == {
        "mode": "estimated_rate", "rate": "0.18", "input_source": "operator_platform_override",
        "basis": "buyer_paid_product_amount", "basis_amount_local": "120",
        "amount_local": "21.60", "amount_cny": "1.7280",
        "policy_version": "operator-adjustable-ad-rate/v1",
    }
    assert bundle["reports"]["tiktok"]["report"]["order_lines"][0]["advertising"]["rate"] == "0.15"
    assert bundle["reports"]["tiktok"]["report"]["order_lines"][0]["advertising"]["input_source"] == "operator_global_override"
    assert bundle["reports"]["shopee"]["report"]["order_lines"][0]["advertising"]["rate"] == "0.19"
    assert bundle["advertising"]["ozon"]["rate"] == "0.18"
    assert bundle["advertising"]["shopee"]["input_source"] == "operator_platform_override"
    assert bundle["external_writes_performed"] == []

    shopee_only = build_weekly_evidence_bundle(
        {"shopee": evidence("shopee", "MY", "1")},
        _catalog_stub(),
        period_start="2026-07-27",
        period_end="2026-08-02",
        costs=CostSnapshot.from_mapping(
            {"0001": {"unit_cost_cny": "10", "version": "fixture-v1"}},
            snapshot_id="costs:fixture",
        ),
        fx=FxSnapshot.from_mapping(
            {"THB": "0.2"}, source="fixture-fx", as_of="2026-08-03"
        ),
        platforms=("shopee",),
        generated_at=NOW,
        code_version="stage-two-test",
    )
    assert shopee_only["status"] == "ready"
    assert set(shopee_only["reports"]) == {"shopee"}


def test_unified_report_policy_loads_ad_rates_and_combined_local_fee(tmp_path):
    policy_path = tmp_path / "report-policy.json"
    policy_path.write_text(json.dumps({
        "schema_version": "profit-settlement-policy/v1",
        "weekly_ad_rates": {"default": "0.21", "tiktok": "0.20", "shopee": "0.19", "ozon": "0.18"},
        "tiktok": {"local_fulfillment_fee_cny_per_order": "4.25"},
        "shopee": {"local_fulfillment_fee_cny_per_order": "5.50"},
    }), encoding="utf-8")

    policy = _weekly_build_script_module()._load_policy(policy_path)

    assert policy["weekly_ad_rates"] == {"default": "0.21", "tiktok": "0.20", "shopee": "0.19", "ozon": "0.18"}
    assert policy["tiktok"] == {"local_fulfillment_fee_cny_per_order": "4.25"}
    assert policy["shopee"] == {"local_fulfillment_fee_cny_per_order": "5.50"}
    assert policy["snapshot_id"].startswith("sha256:")


@pytest.mark.parametrize("bad_rate", ["-0.01", "1.01", "not-a-number"])
def test_unified_report_policy_rejects_invalid_ad_rates(tmp_path, bad_rate):
    policy_path = tmp_path / "report-policy.json"
    policy_path.write_text(json.dumps({
        "schema_version": "profit-settlement-policy/v1",
        "weekly_ad_rates": {"default": "0.22", "tiktok": bad_rate, "shopee": "0.22", "ozon": "0.22"},
        "shopee": {"local_fulfillment_fee_cny_per_order": "4"},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="weekly_ad_rates.tiktok"):
        _weekly_build_script_module()._load_policy(policy_path)


def test_ozon_read_enrichment_supplies_mapping_and_quantity_without_inference():
    evidence = {
        "schema_version": "settlement-evidence/v1", "status": "ready", "platform": "ozon", "site": "RU",
        "snapshot_id": "ozon-settlement:fixture", "checksum": "fixture", "net_settlement_total_local": "10",
        "receipt": {"external_writes_performed": []},
        "orders": [{"order_id": "posting-1", "transaction_type": "Order", "settled_at": "2026-07-27T00:00:00+03:00", "currency": "RUB", "net_settlement_amount": "10", "items": [{"platform_sku": "ozon-1"}], "financial_components": [{"code": "OperationAgentDeliveredToCustomer", "amount": "12"}]}],
    }

    result = adapt_settlement_evidence(
        evidence,
        _catalog_stub(),
        period_kind="weekly",
        seller_sku_by_platform_sku={"ozon-1": "1"},
        quantity_by_order_platform_sku={"posting-1|ozon-1": "2"},
    )

    assert result.status == "ready"
    assert result.rows[0]["canonical_sku"] == "0001"
    assert result.rows[0]["quantity"] == Decimal("2")


def test_shopee_weekly_ad_basis_uses_product_sales_and_retains_buyer_cash_paid():
    evidence = {
        "schema_version": "settlement-evidence/v1", "status": "ready", "platform": "shopee", "site": "TH",
        "snapshot_id": "shopee-settlement:fixture", "checksum": "fixture", "net_settlement_total_local": "90",
        "receipt": {"external_writes_performed": []},
        "orders": [{"order_id": "order-1", "order_created_at": "2026-07-20T08:30:00+07:00", "settled_at": "2026-07-27T00:00:00+07:00", "currency": "THB", "net_settlement_amount": "90", "buyer_total_amount": "120", "items": [{"seller_sku": "1", "quantity": "2", "discounted_price": "150"}], "financial_components": [{"code": "order_discounted_price", "amount": "150"}, {"code": "buyer_paid_shipping_fee", "amount": "40"}, {"code": "voucher_from_shopee", "amount": "70"}, {"code": "vat_on_imported_goods", "amount": "13"}, {"code": "th_import_duty", "amount": "37"}]}],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "ready"
    assert result.rows[0]["product_sales_amount"] == Decimal("150")
    assert result.rows[0]["buyer_paid_product_amount"] == Decimal("150")
    assert result.rows[0]["buyer_cash_paid_product_amount"] == Decimal("80")
    assert result.rows[0]["occurred_at"] == "2026-07-20T08:30:00+07:00"

    report = build_shopee_weekly_report(
        result.rows,
        period_start="2026-07-27", period_end="2026-07-27",
        costs=CostSnapshot.from_mapping(
            {"0001": {"unit_cost_cny": "10", "version": "fixture-v1"}},
            snapshot_id="costs:fixture",
        ),
        fx=_fx(), ad_rate="0.22", generated_at=NOW,
        code_version="test-v1",
    ).payload()
    line = report["order_lines"][0]
    assert line["settlement"]["product_sales_amount_local"] == "150"
    assert line["settlement"]["buyer_cash_paid_product_amount_local"] == "80"
    assert line["advertising"]["basis"] == "product_sales_amount_after_seller_discount"
    assert line["advertising"]["basis_amount_local"] == "150"
    assert line["advertising"]["amount_local"] == "33.00"


def test_shopee_fulfillment_classification_and_local_cost_are_order_scoped_and_configurable():
    first = _row("SP-LOCAL", paid="100", settlement="100")
    second = copy.deepcopy(first)
    second["order_line_id"] = "SP-LOCAL:2"
    second["buyer_paid_product_amount"] = "300"
    first["fulfillment"] = second["fulfillment"] = {
        "mode": "local",
        "classification_rule": "import_vat_and_duty_presence/v2",
        "import_vat_local": "0",
        "import_duty_local": "0",
    }

    default_report = build_shopee_weekly_report(
        [second, first], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), generated_at=NOW, code_version="test-v1",
    )
    overridden = build_shopee_weekly_report(
        [first, second], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), local_fulfillment_fee_cny="6.50",
        generated_at=NOW, code_version="test-v1",
    )

    assert default_report.status == "ready"
    assert default_report.source["fulfillment_order_counts"] == {"local": 1, "cross_border": 0, "unknown": 0}
    assert default_report.totals["local_fulfillment_cost_cny"] == Decimal("4")
    assert sum((line["external_costs_cny"] for line in default_report.order_lines), Decimal("0")) == Decimal("6")
    assert sum((line["fulfillment"]["local_fulfillment_cost_cny"] for line in default_report.order_lines), Decimal("0")) == Decimal("4")
    assert default_report.source["fulfillment_policy"]["cost_components"] == ["local_shipping", "local_warehouse"]
    assert audit_profit_report(default_report.payload()).status == "PASSED"
    assert overridden.totals["local_fulfillment_cost_cny"] == Decimal("6.50")
    assert overridden.idempotency_key != default_report.idempotency_key


def test_tiktok_local_fulfillment_cost_is_once_per_parent_order_and_configurable():
    first = _row("TK-LOCAL", paid="100", settlement="100")
    second = copy.deepcopy(first)
    second["order_line_id"] = "TK-LOCAL:2"
    second["buyer_paid_product_amount"] = "300"
    first["fulfillment"] = second["fulfillment"] = {
        "mode": "local",
        "classification_rule": "tiktok_th_import_tax_charged/v1",
        "import_vat_local": "0",
        "customs_duty_local": "0",
    }
    cross_border = _row("TK-CROSS", paid="200", settlement="100")

    default_report = build_tiktok_weekly_report(
        [second, cross_border, first],
        period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), generated_at=NOW, code_version="test-v1",
    )
    overridden = build_tiktok_weekly_report(
        [first, second, cross_border],
        period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), local_fulfillment_fee_cny="6.50",
        generated_at=NOW, code_version="test-v1",
    )

    assert default_report.status == "ready"
    assert default_report.source["fulfillment_order_counts"] == {"cross_border": 1, "local": 1, "unknown": 0}
    assert default_report.totals["local_fulfillment_cost_cny"] == Decimal("4")
    local_lines = [line for line in default_report.order_lines if line["identity"]["order_id"] == "TK-LOCAL"]
    assert sum((line["fulfillment"]["local_fulfillment_cost_cny"] for line in local_lines), Decimal("0")) == Decimal("4")
    assert all(line["fulfillment"]["local_fulfillment_cost_cny"] == Decimal("0") for line in default_report.order_lines if line["identity"]["order_id"] == "TK-CROSS")
    assert default_report.source["fulfillment_policy"]["cost_components"] == ["local_fulfillment"]
    assert all(line["fulfillment"]["customs_duty_local"] == Decimal("0") for line in local_lines)
    assert audit_profit_report(default_report.payload()).status == "PASSED"
    assert overridden.totals["local_fulfillment_cost_cny"] == Decimal("6.50")
    assert overridden.idempotency_key != default_report.idempotency_key


def test_tiktok_zero_settlement_parent_keeps_ads_but_excludes_goods_and_local_fulfillment():
    first = _row("TK-ZERO", paid="100", settlement="0")
    second = copy.deepcopy(first)
    second["order_line_id"] = "TK-ZERO:2"
    second["buyer_paid_product_amount"] = "300"
    first["fee_items"] = second["fee_items"] = []
    first["fulfillment"] = second["fulfillment"] = {
        "mode": "local",
        "classification_rule": "tiktok_th_import_tax_charged/v1",
        "import_vat_local": "0",
        "customs_duty_local": "0",
    }

    report = build_tiktok_weekly_report(
        [second, first],
        period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), generated_at=NOW, code_version="test-v1",
    )

    assert report.status == "ready"
    assert report.source["zero_settlement_unshipped_order_count"] == 1
    assert report.source["local_fulfillment_charged_order_count"] == 0
    assert report.totals["product_cost_cny"] == Decimal("0")
    assert report.totals["advertising_cny"] == Decimal("17.6000")
    assert report.totals["local_fulfillment_cost_cny"] == Decimal("0")
    assert report.totals["profit_cny"] == Decimal("-17.6000")
    assert all(line["cost"]["total_cny"] == Decimal("0") for line in report.order_lines)
    assert all(line["cost"]["catalog_total_cny"] == Decimal("20") for line in report.order_lines)
    assert all(line["settlement_outcome"]["advertising_cost_recognized"] is True for line in report.order_lines)
    assert audit_profit_report(report.payload()).status == "PASSED"
    assert "零结算未发货（仅计广告）" in render_profit_report_html(report.payload())


def test_shopee_adapter_classifies_zero_import_vat_and_duty_as_local():
    evidence = {
        "schema_version": "settlement-evidence/v1", "status": "ready", "platform": "shopee", "site": "TH",
        "snapshot_id": "shopee-settlement:local", "checksum": "local", "net_settlement_total_local": "90",
        "receipt": {"external_writes_performed": []},
        "orders": [{"order_id": "local-1", "settled_at": "2026-07-27T00:00:00+07:00", "currency": "THB", "net_settlement_amount": "90", "buyer_total_amount": "120", "items": [{"seller_sku": "1", "quantity": "1", "discounted_price": "120"}], "financial_components": [{"code": "vat_on_imported_goods", "amount": "0"}, {"code": "th_import_duty", "amount": "0"}, {"code": "buyer_paid_shipping_fee", "amount": "0"}, {"code": "order_discounted_price", "amount": "120"}]}],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "ready"
    assert result.rows[0]["fulfillment"]["mode"] == "local"
    assert result.rows[0]["fulfillment"]["import_vat_local"] == Decimal("0")


@pytest.mark.parametrize(
    ("vat", "duty", "expected_mode", "expected_status", "expected_issue"),
    (
        ("13", "37", "cross_border", "ready", None),
        ("13", "0", "local", "ready", None),
        (None, "0", "unknown", "needs_review", "missing_fulfillment_tax_evidence"),
    ),
)
def test_shopee_import_tax_pair_is_fail_closed(vat, duty, expected_mode, expected_status, expected_issue):
    components = [
        {"code": "buyer_paid_shipping_fee", "amount": "0"},
        {"code": "order_discounted_price", "amount": "120"},
    ]
    if vat is not None:
        components.append({"code": "vat_on_imported_goods", "amount": vat})
    if duty is not None:
        components.append({"code": "th_import_duty", "amount": duty})
    evidence = {
        "schema_version": "settlement-evidence/v1", "status": "ready", "platform": "shopee", "site": "TH",
        "snapshot_id": "shopee-settlement:tax-pair", "checksum": "tax-pair", "net_settlement_total_local": "90",
        "receipt": {"external_writes_performed": []},
        "orders": [{"order_id": "tax-pair-1", "settled_at": "2026-07-27T00:00:00+07:00", "currency": "THB", "net_settlement_amount": "90", "buyer_total_amount": "120", "items": [{"seller_sku": "1", "quantity": "1", "discounted_price": "120"}], "financial_components": components}],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == expected_status
    assert result.rows[0]["fulfillment"]["mode"] == expected_mode
    if expected_issue:
        assert expected_issue in {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    ("lvg_sales_tax", "expected_mode", "expected_status"),
    (("5.14", "cross_border", "ready"), ("0", "local", "ready"), (None, "unknown", "needs_review")),
)
def test_shopee_my_uses_low_value_goods_sales_tax_for_fulfillment(
    lvg_sales_tax, expected_mode, expected_status
):
    components = [
        {"code": "buyer_paid_shipping_fee", "amount": "0"},
        {"code": "order_discounted_price", "amount": "56.52"},
        {"code": "vat_on_imported_goods", "amount": "0"},
        {"code": "th_import_duty", "amount": "0"},
    ]
    if lvg_sales_tax is not None:
        components.append({"code": "sales_tax_on_lvg", "amount": lvg_sales_tax})
    evidence = {
        "schema_version": "settlement-evidence/v1", "status": "ready", "platform": "shopee", "site": "MY",
        "snapshot_id": "shopee-settlement:my-lvg", "checksum": "my-lvg", "net_settlement_total_local": "25.83",
        "receipt": {"external_writes_performed": []},
        "orders": [{"order_id": "my-lvg-1", "settled_at": "2026-08-03T00:00:00+08:00", "currency": "MYR", "net_settlement_amount": "25.83", "buyer_total_amount": "56.52", "items": [{"seller_sku": "1", "quantity": "1", "discounted_price": "56.52"}], "financial_components": components}],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == expected_status
    assert result.rows[0]["fulfillment"]["mode"] == expected_mode
    assert result.rows[0]["fulfillment"]["classification_rule"] == "my_lvg_sales_tax_charged/v1"


@pytest.mark.parametrize(
    ("import_vat", "expected_mode", "expected_status"),
    (("14.385", "cross_border", "ready"), ("0", "local", "ready"), (None, "unknown", "needs_review")),
)
def test_shopee_vn_uses_import_vat_for_fulfillment(
    import_vat, expected_mode, expected_status
):
    components = [
        {"code": "buyer_paid_shipping_fee", "amount": "0"},
        {"code": "order_discounted_price", "amount": "194195"},
    ]
    if import_vat is not None:
        components.append({"code": "vat_on_imported_goods", "amount": import_vat})
    evidence = {
        "schema_version": "settlement-evidence/v1", "status": "ready", "platform": "shopee", "site": "VN",
        "snapshot_id": "shopee-settlement:vn-vat", "checksum": "vn-vat", "net_settlement_total_local": "87834",
        "receipt": {"external_writes_performed": []},
        "orders": [{"order_id": "vn-vat-1", "settled_at": "2026-08-03T00:00:00+07:00", "currency": "VND", "net_settlement_amount": "87834", "buyer_total_amount": "194195", "items": [{"seller_sku": "1", "quantity": "1", "discounted_price": "194195"}], "financial_components": components}],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == expected_status
    assert result.rows[0]["fulfillment"]["mode"] == expected_mode
    assert result.rows[0]["fulfillment"]["classification_rule"] == "vn_import_vat_charged/v1"


def test_shopee_ph_classifies_every_order_as_cross_border():
    evidence = {
        "schema_version": "settlement-evidence/v1", "status": "ready", "platform": "shopee", "site": "PH",
        "snapshot_id": "shopee-settlement:ph", "checksum": "ph", "net_settlement_total_local": "100",
        "receipt": {"external_writes_performed": []},
        "orders": [{"order_id": "ph-1", "settled_at": "2026-08-03T00:00:00+08:00", "currency": "PHP", "net_settlement_amount": "100", "buyer_total_amount": "120", "items": [{"seller_sku": "1", "quantity": "1", "discounted_price": "120"}], "financial_components": [{"code": "buyer_paid_shipping_fee", "amount": "0"}, {"code": "order_discounted_price", "amount": "120"}]}],
    }

    result = adapt_settlement_evidence(evidence, _catalog_stub(), period_kind="weekly")

    assert result.status == "ready"
    assert result.rows[0]["fulfillment"]["mode"] == "cross_border"
    assert result.rows[0]["fulfillment"]["classification_rule"] == "ph_all_orders_cross_border/v1"


@pytest.mark.parametrize(
    "builder",
    (build_tiktok_weekly_report, build_shopee_weekly_report, build_ozon_weekly_report),
)
def test_weekly_reports_default_to_22_percent_ads(builder):
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
    assert report.order_lines[0]["advertising"]["input_source"] == "default_22"
    assert report.totals["advertising_cny"] == Decimal("8.8000")


@pytest.mark.parametrize(
    "builder",
    (build_tiktok_weekly_report, build_shopee_weekly_report, build_ozon_weekly_report),
)
def test_direct_non_default_weekly_ad_rate_is_audited_as_operator_override(builder):
    report = builder(
        [_row("CUSTOM-ADS")],
        period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), ad_rate="0.17",
        generated_at=NOW, code_version="test-v1",
    )

    assert report.order_lines[0]["advertising"]["rate"] == Decimal("0.17")
    assert report.order_lines[0]["advertising"]["input_source"] == "operator_global_override"


@pytest.mark.parametrize(
    "builder",
    (build_tiktok_weekly_report, build_shopee_weekly_report, build_ozon_weekly_report),
)
def test_ad_rate_input_source_changes_report_idempotency(builder):
    kwargs = dict(
        period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), ad_rate="0.22",
        generated_at=NOW, code_version="test-v1",
    )
    default = builder([_row("ADS-LINEAGE")], **kwargs)
    overridden = builder(
        [_row("ADS-LINEAGE")],
        ad_rate_source="operator_platform_override",
        **kwargs,
    )

    assert default.idempotency_key != overridden.idempotency_key
    assert overridden.order_lines[0]["advertising"]["input_source"] == "operator_platform_override"


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


def test_ozon_monthly_defaults_to_22_percent_advertising_and_preserves_platform_fees():
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
    report = build_ozon_monthly_report(
        [row],
        period_start="2026-08-01",
        period_end="2026-08-31",
        costs=_costs(),
        fx=_fx(),
        generated_at=NOW,
        code_version="test-v1",
    )
    assert report.status == "ready"
    assert report.order_lines[0]["fee_items"][0]["code"] == "sale_commission"
    # 80 settlement - 10 goods - (1000 RUB * 22% * 0.08 FX); commission is already netted.
    assert report.order_lines[0]["profit_cny"] == Decimal("52.4000")
    assert report.advertising["policy_version"] == "operator-adjustable-ad-rate/v1"
    assert report.advertising["input_source"] == "default_22"


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


@pytest.mark.parametrize(
    "builder",
    (build_tiktok_weekly_report, build_shopee_weekly_report, build_ozon_weekly_report),
)
def test_order_lines_sort_by_settlement_time_descending_with_stable_identity_tie_break(builder):
    rows = [
        {**_row("ORDER-Z"), "settled_at": "2026-08-04T12:00:00+07:00"},
        {**_row("ORDER-B"), "settled_at": "2026-08-06T12:00:00+07:00"},
        {**_row("ORDER-A"), "settled_at": "2026-08-06T12:00:00+07:00"},
    ]

    report = builder(
        rows,
        period_start="2026-08-03",
        period_end="2026-08-09",
        costs=_costs(),
        fx=_fx(),
        generated_at=NOW,
        code_version="test-v1",
    )

    assert [line["identity"]["order_id"] for line in report.order_lines] == [
        "ORDER-A",
        "ORDER-B",
        "ORDER-Z",
    ]


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


def test_detailed_html_renders_main_image_weight_cost_ads_fees_profit_and_live_fx_lineage():
    report = build_tiktok_weekly_report(
        [_row("TK-HTML")], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), ad_rate="0.20", generated_at=NOW, code_version="test-v1",
    ).payload()
    html = render_profit_report_html(report)
    for expected in (
        "商品主图", "Detailed product", "SKU-1", "125.50",
        "Platform commission [commission]", "广告成本", "利润 CNY",
        "单件成本(CNY)", "12.00 THB", "CNY 2.40",
        "最新汇率(CNY/当地)", "0.20000000", "汇率更新时间",
        "2026-08-06T00:00:00+00:00", "汇率来源", "official-fx-test",
        "下单时间", "广告比例来源", "人工全局覆盖",
        "国家", '<img src="https://example.invalid/main.jpg"',
        "商品折后成交额", "买家现金实付商品金额",
        'data-role="order-table-top-scroll"', 'data-role="order-table-scroll"',
        "top.addEventListener('scroll'", "body.addEventListener('scroll'",
        'data-sort="order-created-at"', 'aria-sort="none"',
        'data-order-created-at="2026-08-03T12:00:00+07:00"',
        "orderTimeButton.getAttribute('aria-sort') === 'ascending'",
        "? 'descending' : 'ascending'",
        "if (!leftTime) return 1", "if (!rightTime) return -1",
        "发货方式", "本土履约费(CNY)", "联盟营销佣金(AMS)",
    ):
        assert expected in html
    assert "&lt;img" not in html
    assert "th-main / TH" not in html
    assert "平台 SKU" not in html
    assert "<th>币种</th>" not in html
    assert html.index("<th>Seller SKU</th>") < html.index("<th>净结算(CNY)</th>") < html.index("<th>商品总成本(CNY)</th>") < html.index("<th>广告费(CNY)</th>") < html.index("<th>本土履约费(CNY)</th>") < html.index("<th>利润(CNY)</th>") < html.index("<th>利润率</th>")
    assert html.index("<th>成本/FX/结算证据</th>") < html.index("<th>商品名称</th>")


def test_shopee_html_moves_ams_commission_into_former_currency_column_without_duplicate():
    row = _row("SP-AMS", paid="169", settlement="94")
    row["fee_items"].append({
        "code": "order_ams_commission_fee",
        "label": "order_ams_commission_fee",
        "amount": "22",
        "currency": "THB",
        "included_in_net_settlement": True,
    })
    report = build_shopee_weekly_report(
        [row, _row("SP-NON-AMS", paid="169", settlement="116")], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), generated_at=NOW, code_version="test-v1",
    ).payload()

    html = render_profit_report_html(report)

    assert html.count("<th>联盟营销佣金(AMS)</th>") == 1
    assert "22.00 THB / CNY 4.40" in html
    assert "order_ams_commission_fee [order_ams_commission_fee]" not in html
    assert "<th>币种</th>" not in html
    assert report["source"]["affiliate_marketing"] == {
        "classification_rule": "positive_order_ams_commission_fee_per_parent_order/v1",
        "settled_parent_order_count": 2,
        "affiliate_parent_order_count": 1,
        "non_affiliate_parent_order_count": 1,
        "affiliate_order_share": "0.5",
        "ams_commission_fee_cny": "4.40",
        "ams_commission_fee_local_by_currency": {"THB": "22"},
    }
    assert "联盟营销订单占比" in html
    assert "50.00% (1/2)" in html


def test_shopee_html_hides_order_line_id_and_formats_visible_times_for_people():
    report = build_shopee_weekly_report(
        [_row("SP-TIME")], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), generated_at=NOW, code_version="test-v1",
    ).payload()

    html = render_profit_report_html(report)

    assert "<th>订单行 ID</th>" not in html
    assert "<th>订单 ID</th>" in html
    assert "2026-08-05 12:00:00（UTC+07:00）" in html
    assert "2026-08-03 12:00:00（UTC+07:00）" in html
    assert "2026-08-06 00:00:00（UTC+00:00）" in html
    assert 'data-order-created-at="2026-08-03T12:00:00+07:00"' in html
    assert report["order_lines"][0]["identity"]["order_line_id"] == "SP-TIME:1"


def test_tiktok_html_hides_order_line_id_but_json_retains_it():
    row = _row("TK-HIDDEN-LINE")
    row["fulfillment"] = {
        "mode": "cross_border",
        "classification_rule": "tiktok_th_import_tax_charged/v1",
        "import_vat_local": "-14.69",
        "customs_duty_local": "-13.00",
        "evidence_source": "finance.statement_transactions.import_vat+customs_duty",
    }
    report = build_tiktok_weekly_report(
        [row], period_start="2026-08-03", period_end="2026-08-09",
        costs=_costs(), fx=_fx(), generated_at=NOW, code_version="test-v1",
    ).payload()
    html = render_profit_report_html(report)

    assert _visible_base_headers("TIKTOK") == _visible_base_headers("SHOPEE")
    assert report["order_lines"][0]["identity"]["order_line_id"] == "TK-HIDDEN-LINE:1"
    assert report["order_lines"][0]["fulfillment"]["mode"] == "cross_border"
    assert "fulfillment_type" not in report["order_lines"][0]["fulfillment"]
    assert "跨境发货" in html
    assert "FULFILLMENT_BY_SELLER" not in html


def test_temporary_cost_policy_defaults_missing_to_5_and_selects_highest_conflict():
    catalog = SimpleNamespace(
        costs_by_sku={"0001": Decimal("4"), "0002": Decimal("8")},
        cost_candidates_by_sku={"0001": (Decimal("4"), Decimal("6")), "0002": (Decimal("8"),)},
        snapshot_id="catalog:test", effective_at="2026-08-06T00:00:00+00:00",
    )

    result = resolve_temporary_cost_policy(catalog, {"0001", "0002", "0003"})

    assert result.values["0001"]["unit_cost_cny"] == "6"
    assert result.values["0003"]["unit_cost_cny"] == "5"
    assert {item.code for item in result.warnings} == {
        "conflicting_cost_high_selected", "missing_cost_default_5_selected",
    }
    assert all(item.policy_version == POLICY_VERSION for item in result.warnings)
    messages = {item.code: item.message for item in result.warnings}
    assert messages["conflicting_cost_high_selected"].endswith("CNY 6.00")
    assert messages["missing_cost_default_5_selected"].endswith("CNY 5.00")


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
