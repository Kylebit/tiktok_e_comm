from pathlib import Path

from modules.finance import sku_profit_shopee, sku_profit_tk
from modules.finance.sku_profit_model import enrich_comp, mark_outliers


ROOT = Path(__file__).resolve().parents[1]


def _sources() -> tuple[str, str, str]:
    html = (ROOT / "web/profit_center.html").read_text(encoding="utf-8")
    script = (ROOT / "web/static/profit_center.js").read_text(encoding="utf-8")
    css = (ROOT / "web/static/profit_center.css").read_text(encoding="utf-8")
    return html, script, css


def test_profit_center_is_read_only_and_has_no_legacy_probe_escape_hatch():
    html, script, _ = _sources()

    assert "打开高级利润探针" not in html
    assert 'href="/sku-profit"' not in html
    assert "只读分析 · 不写回成本与定价" in html
    assert "/api/profit-center/weekly" in script
    assert "/api/sku-profit" in script
    assert 'method: "POST"' not in script
    assert "fetchJson(`/api/sku-profit?" in script


def test_sku_evidence_exposes_image_price_and_cost_lineage():
    _, script, css = _sources()

    assert "/api/proxy-image?url=" in script
    assert "data-product-image" in script
    assert "近单实付中位价" in script
    assert "当前商品标价" in script
    assert "recent_comp_median_paid" in script
    assert "优先：平台 SKU ID 精确匹配" in script
    assert "响应只标注 sku_costs" in script
    assert "Shopee Seller SKU 尾四位 → TikTok Seller SKU 尾四位" in script
    assert "sku_costs_via_tk_seller_sku_tail4" in script
    assert ".product-image" in css
    assert ".price-evidence-grid" in css
    assert ".lineage-card" in css


def test_profit_waterfalls_preserve_available_breakdown_and_missing_evidence():
    _, script, css = _sources()

    for field in (
        "goods_local",
        "logistics_local",
        "commission_local",
        "transaction_local",
        "extra_local",
        "creator_local",
        "affiliate_local",
        "ad_local",
        "seller_tax_local",
        "fixed_fee_local",
        "extra_cap_hit",
    ):
        assert f"breakdown.{field}" in script

    assert "平台结算扣减（合并）" in script
    assert "页面不会自行伪造拆分" in script
    assert "当前缺失 / 未暴露证据" in script
    assert ".waterfall-row" in css
    assert ".missing-evidence" in css


def test_returned_posterior_samples_are_filterable_paginated_and_visualized():
    _, script, css = _sources()

    assert "posterior.recent_comps" in script
    assert "<canvas" in script
    assert "data-sample-filter" in script
    assert "data-sample-search" in script
    assert 'data-page-action="prev"' in script
    assert 'data-page-action="next"' in script
    assert "接口只返回最近" in script
    assert "下表完整展示接口实际返回的每一条" in script
    assert ".distribution-canvas" in css
    assert ".sample-table-wrap" in css
    assert ".pagination" in css


def test_both_profit_probes_return_every_collected_posterior_sample(monkeypatch):
    samples = mark_outliers(
        [
            enrich_comp(
                order_id=f"order-{index:02d}",
                statement_date="2026-07-24",
                sale_local=100,
                settlement_local=60 + index / 10,
                cost_cny=5,
                fx=0.2,
                source=f"fixture-{index:02d}",
            )
            for index in range(25)
        ]
    )

    monkeypatch.setattr(
        sku_profit_tk,
        "resolve_product",
        lambda _sku: {
            "sku_id": "platform-0021",
            "seller_sku": "0021",
            "product_name": "Fixture product",
            "cost_cny": 5,
            "cost_source": "sku_costs",
            "is_th_listing": True,
        },
    )
    monkeypatch.setattr(
        sku_profit_tk,
        "_live_fx",
        lambda **_kwargs: {"THB": 0.2, "as_of": "2026-07-24"},
    )
    monkeypatch.setattr(
        sku_profit_tk,
        "fetch_live_price_and_weight",
        lambda _product: {
            "list_price_local": 100,
            "weight_kg": 0.2,
            "price_source": "fixture",
            "weight_source": "fixture",
            "warnings": [],
        },
    )
    monkeypatch.setattr(sku_profit_tk, "load_csv_comps", lambda *_args: samples)
    monkeypatch.setattr(sku_profit_tk, "get", lambda _key: {})

    monkeypatch.setattr(
        sku_profit_shopee,
        "resolve_product",
        lambda _sku: {
            "model_id": "model-0021",
            "seller_sku": "0021",
            "model_name": "Fixture model",
            "sale_local": 100,
            "cost_cny": 5,
            "cost_source": "sku_costs_via_tk_seller_sku_tail4",
        },
    )
    monkeypatch.setattr(
        sku_profit_shopee,
        "_live_fx",
        lambda **_kwargs: {"THB": 0.2, "as_of": "2026-07-24"},
    )
    monkeypatch.setattr(
        sku_profit_shopee,
        "load_weekly_comps",
        lambda *_args, **_kwargs: (samples, samples),
    )
    monkeypatch.setattr(sku_profit_shopee, "get", lambda _key: {})

    tiktok = sku_profit_tk.estimate("0021", lookback_days=14)
    shopee = sku_profit_shopee.estimate("0021", lookback_days=45)

    assert tiktok["posterior"]["comps_in_window"] == 25
    assert len(tiktok["posterior"]["recent_comps"]) == 25
    assert shopee["posterior"]["comps_same_sku"] == 25
    assert len(shopee["posterior"]["recent_comps"]) == 25
