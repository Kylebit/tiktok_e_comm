from pathlib import Path

import pytest

from domains.data_operations.profit_settlement.historical_partial_view import (
    EXPECTED_CANDIDATE_SHA256,
    load_historical_partial_candidate,
    render_historical_partial_html,
)


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_candidate_renders_real_rows_and_fail_closed_money():
    candidate = load_historical_partial_candidate(ROOT)
    page = render_historical_partial_html(candidate)
    assert len(candidate["rows"]) == 428
    assert candidate["coverage"]["unique_order_count"] == 409
    assert EXPECTED_CANDIDATE_SHA256 in page
    assert page.count("<tr data-day=") == 428
    assert "2026-07-31：226 行" in page
    assert "2026-07-21：202 行" in page
    assert "利润 / 利润率</span><strong class=\"unknown\">未具备" in page
    assert "下单时间</th>" in page
    assert "结算日期从" in page
    assert "下单日期从" not in page
    assert "synthetic-only" not in page
    assert "SYNTHETIC-ORDER" not in page


def test_historical_partial_keeps_old_table_capabilities_without_faking_unknowns():
    page = render_historical_partial_html(load_historical_partial_candidate(ROOT))
    for marker in (
        'data-role="top-scroll"',
        'data-sort="settled-at"',
        "筛选合计",
        "Seller SKU",
        "净结算(CNY)",
        "商品总成本(CNY)",
        "广告费(CNY)",
        "本土履约费(CNY)",
        "联盟营销佣金(AMS)",
        "最新汇率(CNY/当地)",
        "商品名称",
        "分页完成回执缺失",
    ):
        assert marker in page
    assert "当前成本" in page
    assert "未知金额不会填 0" in page


def test_candidate_checksum_drift_fails_closed(tmp_path):
    artifact = tmp_path / "artifacts/historical_settlement_candidates/2026-07"
    artifact.mkdir(parents=True)
    artifact.joinpath("shopee_TH_20260718_20260731.partial.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_historical_partial_candidate(tmp_path)
