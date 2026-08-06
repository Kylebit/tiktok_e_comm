from datetime import date
from decimal import Decimal
import json

from domains.data_operations import adapt_local_profit_snapshots, adapt_profit_snapshot_text, discover_local_profit_snapshots


def test_adapts_tiktok_income_csv_as_realized_rows_and_keeps_source_timestamp():
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Currency,Quantity,Subtotal after seller discounts,Product name,SKU name,Transaction fee\nOrder,O-1,PLATFORM-1,2026/07/20,12.34,USD,2,20,Widget,Blue,-1.20\nTikTok GMV Payment,ADS,PLATFORM-1,2026-07-20,-1,THB,1,,,,\n"
    result = adapt_profit_snapshot_text(text, source_name="income_TH_202607.csv", source_updated_at="2026-07-21T01:00:00+00:00", costs_by_sku={"0001": "5.50"}, seller_sku_by_platform_sku={"PLATFORM-1": "1"})
    assert result.raw_row_count == 2 and result.normalized_row_count == 1 and result.rejected_row_count == 1
    row = result.rows[0]
    assert (row["channel"], row["region"], row["sku_id"], row["source_seller_sku"], row["source_sku_id"], row["currency"], row["quantity"], row["unit_cost_cny"], row["cost_cny"]) == ("tiktok", "TH", "0001", "1", "PLATFORM-1", "USD", Decimal("2"), Decimal("5.50"), Decimal("11.00"))
    assert row["source_updated_at"] == "2026-07-21T01:00:00+00:00"
    assert row["settlement_status"] == "settled"
    assert row["settled_at"] == "2026-07-20"
    assert row["buyer_paid_product_amount"] == Decimal("20")
    assert row["product_name"] == "Widget"
    assert row["fee_items"][0]["code"] == "transaction_fee"
    assert row["source_snapshot_id"].startswith("local-snapshot:")


def test_adapts_shopee_html_and_retains_overlap_for_digest_deduplication():
    data = {"headers": [{"name": "Release Time"}, {"name": "Order SN"}, {"name": "SKU"}, {"name": "Currency"}], "rows": [{"region": "TH", "product_cost": "2", "settlement": "8", "cells": ["2026-07-20T10:00:00+07:00", "S-1", "17", "THB"]}]}
    result = adapt_profit_snapshot_text(f"<script>\n const   DATA  =\n {json.dumps(data)} ; \n</script>", source_name="weekly_shopee_profit_20260720_20260726.html", source_updated_at="2026-07-21T00:00:00+00:00")
    assert result.normalized_row_count == 1
    assert result.rows[0]["channel"] == "shopee"
    assert (result.rows[0]["sku_id"], result.rows[0]["source_sku_id"]) == ("0017", "17")
    assert result.rows[0]["calculation_kind"] == "realized"
    assert result.rows[0]["settlement_amount"] == Decimal("8")


def test_snapshot_adapter_rejects_out_of_period_rows_and_is_json_ready():
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Quantity\nOrder,O-1,SKU-1,2026-07-01,2,1\n"
    result = adapt_profit_snapshot_text(text, source_name="income_TH_202607.csv", source_updated_at="2026-07-21T00:00:00+00:00", seller_sku_by_platform_sku={"SKU-1": "0001"}, reporting_period=(date(2026, 7, 20), date(2026, 7, 26)))
    assert result.normalized_row_count == 0 and result.rejected_row_count == 1
    assert result.issues[0].code == "out_of_reporting_period"
    json.dumps(result.payload())


def test_rejects_missing_tiktok_mapping_and_non_numeric_shopee_sku():
    tiktok = adapt_profit_snapshot_text("Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Quantity\nOrder,O-1,platform,2026-07-20,2,1\n", source_name="income_TH_202607.csv", source_updated_at="2026-07-21T00:00:00+00:00")
    assert tiktok.rejected_row_count == 1 and tiktok.issues[0].code == "missing_seller_sku_mapping"
    data = {"headers": [{"name": "Release Time"}, {"name": "Order SN"}, {"name": "SKU"}, {"name": "Currency"}], "rows": [{"region": "TH", "product_cost": "2", "settlement": "8", "cells": ["2026-07-20", "S-1", "SKU-17 A", "THB"]}]}
    shopee = adapt_profit_snapshot_text(f"<script>const DATA={json.dumps(data)};</script>", source_name="weekly_shopee_profit_20260720_20260726.html", source_updated_at="2026-07-21T00:00:00+00:00")
    assert shopee.rejected_row_count == 1 and shopee.issues[0].code == "invalid_shopee_seller_sku"


def test_period_accepts_slash_and_iso_dates_and_snapshot_identity_excludes_directory(tmp_path):
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Quantity\nOrder,O-1,P-1,2026/07/20,2,1\n"
    first_dir, second_dir = tmp_path / "a", tmp_path / "b"
    first_dir.mkdir(); second_dir.mkdir()
    first, second = first_dir / "income_TH_fixture.csv", second_dir / "income_TH_fixture.csv"
    first.write_text(text, encoding="utf-8"); second.write_text(text, encoding="utf-8")
    kwargs = {"seller_sku_by_platform_sku": {"P-1": "0001"}, "reporting_period": (date(2026, 7, 20), date(2026, 7, 20))}
    direct = adapt_profit_snapshot_text(text, source_name="income_TH_fixture.csv", source_updated_at="2026-07-21T00:00:00+00:00", **kwargs)
    assert direct.normalized_row_count == 1
    left, right = adapt_local_profit_snapshots([first], **kwargs), adapt_local_profit_snapshots([second], **kwargs)
    assert left.snapshot_id == right.snapshot_id


def test_tiktok_currency_does_not_leak_and_invalid_quantity_is_rejected():
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Currency,Quantity\nOrder,A,P1,2026-07-20,2,USD,1\nOrder,B,P2,2026-07-20,2,,0\n"
    result = adapt_profit_snapshot_text(text, source_name="income_TH_fixture.csv", source_updated_at="2026-07-21T00:00:00+00:00", seller_sku_by_platform_sku={"P1": "0001", "P2": "0002"})
    assert result.rows[0]["currency"] == "USD"
    assert result.rejected_row_count == 1
    assert any(issue.code == "invalid_quantity" for issue in result.issues)


def test_shopee_quantity_cost_variants_and_legacy_missing_quantity_issue():
    headers = [{"name": "Release Time"}, {"name": "Order SN"}, {"name": "SKU"}, {"name": "Currency"}]
    data = {"headers": headers, "rows": [
        {"region": "TH", "quantity": 3, "unit_cost_cny": "2", "product_cost": "99", "settlement": "8", "cells": ["2026-07-20", "S-1", "17", "THB"]},
        {"region": "TH", "product_cost": "4", "settlement": "8", "cells": ["2026-07-20", "S-2", "18", "THB"]},
    ]}
    result = adapt_profit_snapshot_text(f"<script>const DATA={json.dumps(data)};</script>", source_name="weekly_shopee_profit_20260720_20260726.html", source_updated_at="2026-07-21T00:00:00+00:00")
    assert result.rows[0]["cost_cny"] == Decimal("6")
    assert result.rows[1]["cost_cny"] == Decimal("4")
    assert any(issue.code == "missing_quantity" for issue in result.issues)


def test_source_order_does_not_change_snapshot_identity(tmp_path):
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Quantity\nOrder,O-1,P-1,2026-07-20,2,1\n"
    one, two = tmp_path / "income_TH_one.csv", tmp_path / "income_TH_two.csv"
    one.write_text(text, encoding="utf-8"); two.write_text(text, encoding="utf-8")
    kwargs = {"seller_sku_by_platform_sku": {"P-1": "0001"}}
    assert adapt_local_profit_snapshots([one, two], **kwargs).snapshot_id == adapt_local_profit_snapshots([two, one], **kwargs).snapshot_id


def test_discovery_excludes_manual_and_probe_tiktok_experiments(tmp_path):
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Quantity\nOrder,O-1,P-1,2026-07-20,2,1\n"
    for name in ("income_TH_standard.csv", "income_TH_latest.api.csv", "income_TH_manual.csv", "income_TH_full_probe.csv"):
        (tmp_path / name).write_text(text, encoding="utf-8")
    result = discover_local_profit_snapshots([tmp_path], seller_sku_by_platform_sku={"P-1": "0001"})
    assert [item["name"] for item in result.source_files] == ["income_TH_latest.api.csv", "income_TH_standard.csv"]


def test_discovery_excludes_filename_periods_that_cannot_overlap(tmp_path):
    old = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Quantity\nOrder,O-OLD,P-1,2026-06-10,2,1\n"
    current = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Quantity\nOrder,O-NOW,P-1,2026-07-20,2,1\n"
    (tmp_path / "income_TH_260601_260630.csv").write_text(old, encoding="utf-8")
    (tmp_path / "income_TH_260720_260726.csv").write_text(current, encoding="utf-8")

    result = discover_local_profit_snapshots(
        [tmp_path],
        seller_sku_by_platform_sku={"P-1": "0001"},
        reporting_period=(date(2026, 7, 20), date(2026, 7, 26)),
    )

    assert [item["name"] for item in result.source_files] == ["income_TH_260720_260726.csv"]
    assert result.raw_row_count == 1


def test_out_of_period_rows_do_not_emit_sku_or_mapping_blockers():
    tiktok = adapt_profit_snapshot_text("Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Quantity\nOrder,O-1,unmapped,2026-07-01,2,0\n", source_name="income_TH_fixture.csv", source_updated_at="2026-07-21T00:00:00+00:00", reporting_period=(date(2026, 7, 20), date(2026, 7, 26)))
    assert [issue.code for issue in tiktok.issues] == ["out_of_reporting_period"]
    data = {"headers": [{"name": "Release Time"}, {"name": "Order SN"}, {"name": "SKU"}, {"name": "Currency"}], "rows": [{"region": "TH", "settlement": "8", "cells": ["2026-07-01", "S-1", "not a SKU", "THB"]}]}
    shopee = adapt_profit_snapshot_text(f"<script>const DATA={json.dumps(data)};</script>", source_name="weekly_shopee_profit_20260701_20260707.html", source_updated_at="2026-07-21T00:00:00+00:00", reporting_period=(date(2026, 7, 20), date(2026, 7, 26)))
    assert [issue.code for issue in shopee.issues] == ["out_of_reporting_period"]
