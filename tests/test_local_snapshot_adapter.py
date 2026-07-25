from datetime import date
from decimal import Decimal
import json

from domains.data_operations import adapt_local_profit_snapshots, adapt_profit_snapshot_text


def test_adapts_tiktok_income_csv_as_realized_rows_and_keeps_source_timestamp():
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount,Currency\nOrder,O-1,PLATFORM-1,2026/07/20,12.34,USD\nTikTok GMV Payment,ADS,PLATFORM-1,2026-07-20,-1,THB\n"
    result = adapt_profit_snapshot_text(text, source_name="income_TH_202607.csv", source_updated_at="2026-07-21T01:00:00+00:00", costs_by_sku={"SELLER-1": "5.50"}, seller_sku_by_platform_sku={"PLATFORM-1": "SELLER-1"})
    assert result.raw_row_count == 2 and result.normalized_row_count == 1 and result.rejected_row_count == 1
    row = result.rows[0]
    assert (row["channel"], row["region"], row["sku_id"], row["source_sku_id"], row["currency"], row["calculation_kind"], row["settlement_amount"], row["cost_cny"]) == ("tiktok", "TH", "SELLER-1", "PLATFORM-1", "USD", "realized", Decimal("12.34"), Decimal("5.50"))
    assert row["source_updated_at"] == "2026-07-21T01:00:00+00:00"


def test_adapts_shopee_html_and_retains_overlap_for_digest_deduplication():
    data = {"headers": [{"name": "Release Time"}, {"name": "Order SN"}, {"name": "SKU"}, {"name": "Currency"}], "rows": [{"region": "TH", "product_cost": "2", "settlement": "8", "cells": ["2026-07-20T10:00:00+07:00", "S-1", "17", "THB"]}]}
    result = adapt_profit_snapshot_text(f"<script>\n const   DATA  =\n {json.dumps(data)} ; \n</script>", source_name="weekly_shopee_profit_20260720_20260726.html", source_updated_at="2026-07-21T00:00:00+00:00")
    assert result.normalized_row_count == 1
    assert result.rows[0]["channel"] == "shopee"
    assert (result.rows[0]["sku_id"], result.rows[0]["source_sku_id"]) == ("0017", "17")
    assert result.rows[0]["calculation_kind"] == "realized"
    assert result.rows[0]["settlement_amount"] == Decimal("8")


def test_snapshot_adapter_rejects_out_of_period_rows_and_is_json_ready():
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount\nOrder,O-1,SKU-1,2026-07-01,2\n"
    result = adapt_profit_snapshot_text(text, source_name="income_TH_202607.csv", source_updated_at="2026-07-21T00:00:00+00:00", seller_sku_by_platform_sku={"SKU-1": "0001"}, reporting_period=(date(2026, 7, 20), date(2026, 7, 26)))
    assert result.normalized_row_count == 0 and result.rejected_row_count == 1
    assert result.issues[0].code == "out_of_reporting_period"
    json.dumps(result.payload())


def test_rejects_missing_tiktok_mapping_and_non_numeric_shopee_sku():
    tiktok = adapt_profit_snapshot_text("Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount\nOrder,O-1,platform,2026-07-20,2\n", source_name="income_TH_202607.csv", source_updated_at="2026-07-21T00:00:00+00:00")
    assert tiktok.rejected_row_count == 1 and tiktok.issues[0].code == "missing_seller_sku_mapping"
    data = {"headers": [{"name": "Release Time"}, {"name": "Order SN"}, {"name": "SKU"}, {"name": "Currency"}], "rows": [{"region": "TH", "product_cost": "2", "settlement": "8", "cells": ["2026-07-20", "S-1", "SKU-17 A", "THB"]}]}
    shopee = adapt_profit_snapshot_text(f"<script>const DATA={json.dumps(data)};</script>", source_name="weekly_shopee_profit_20260720_20260726.html", source_updated_at="2026-07-21T00:00:00+00:00")
    assert shopee.rejected_row_count == 1 and shopee.issues[0].code == "invalid_shopee_seller_sku"


def test_period_accepts_slash_and_iso_dates_and_snapshot_identity_excludes_directory(tmp_path):
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount\nOrder,O-1,P-1,2026/07/20,2\n"
    first_dir, second_dir = tmp_path / "a", tmp_path / "b"
    first_dir.mkdir(); second_dir.mkdir()
    first, second = first_dir / "income_TH_fixture.csv", second_dir / "income_TH_fixture.csv"
    first.write_text(text, encoding="utf-8"); second.write_text(text, encoding="utf-8")
    kwargs = {"seller_sku_by_platform_sku": {"P-1": "0001"}, "reporting_period": (date(2026, 7, 20), date(2026, 7, 20))}
    direct = adapt_profit_snapshot_text(text, source_name="income_TH_fixture.csv", source_updated_at="2026-07-21T00:00:00+00:00", **kwargs)
    assert direct.normalized_row_count == 1
    left, right = adapt_local_profit_snapshots([first], **kwargs), adapt_local_profit_snapshots([second], **kwargs)
    assert left.snapshot_id == right.snapshot_id
