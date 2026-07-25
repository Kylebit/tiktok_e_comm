from datetime import date
from decimal import Decimal
import json

from domains.data_operations import adapt_profit_snapshot_text


def test_adapts_tiktok_income_csv_as_realized_rows_and_keeps_source_timestamp():
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount\nOrder,O-1,SKU-1,2026-07-20,12.34\nTikTok GMV Payment,ADS,SKU-1,2026-07-20,-1\n"
    result = adapt_profit_snapshot_text(text, source_name="income_TH_202607.csv", source_updated_at="2026-07-21T01:00:00+00:00", costs_by_sku={"SKU-1": "5.50"})
    assert result.raw_row_count == 2 and result.normalized_row_count == 1 and result.rejected_row_count == 1
    row = result.rows[0]
    assert (row["channel"], row["region"], row["calculation_kind"], row["settlement_amount"], row["cost_cny"]) == ("tiktok", "TH", "realized", Decimal("12.34"), Decimal("5.50"))
    assert row["source_updated_at"] == "2026-07-21T01:00:00+00:00"


def test_adapts_shopee_html_and_retains_overlap_for_digest_deduplication():
    data = {"headers": [{"name": "Release Time"}, {"name": "Order SN"}, {"name": "SKU"}, {"name": "Currency"}], "rows": [{"region": "TH", "product_cost": "2", "settlement": "8", "cells": ["2026-07-20", "S-1", "SKU-2", "THB"]}]}
    result = adapt_profit_snapshot_text(f"<script>\nconst DATA = {json.dumps(data)};\n</script>", source_name="weekly_shopee_profit_20260720_20260726.html", source_updated_at="2026-07-21T00:00:00+00:00")
    assert result.normalized_row_count == 1
    assert result.rows[0]["channel"] == "shopee"
    assert result.rows[0]["calculation_kind"] == "realized"
    assert result.rows[0]["settlement_amount"] == Decimal("8")


def test_snapshot_adapter_rejects_out_of_period_rows_and_is_json_ready():
    text = "Type ,Order/adjustment ID  ,SKU ID,Statement Date,Total settlement amount\nOrder,O-1,SKU-1,2026-07-01,2\n"
    result = adapt_profit_snapshot_text(text, source_name="income_TH_202607.csv", source_updated_at="2026-07-21T00:00:00+00:00", reporting_period=(date(2026, 7, 20), date(2026, 7, 26)))
    assert result.normalized_row_count == 0 and result.rejected_row_count == 1
    assert result.issues[0].code == "out_of_reporting_period"
    json.dumps(result.payload())
