from __future__ import annotations

import pytest

from modules.finance import sku_profit_service


def _success(platform: str):
    return {
        "ok": True,
        "platform": platform,
        "product": {"sale_local": 100, "cost_cny": 20},
        "main": {
            "profit_cny": 5,
            "margin_pct": 10,
            "label": "positive",
            "confidence": "medium",
        },
        "fx": {"THB_CNY": 0.22},
    }


@pytest.fixture
def fake_probes(monkeypatch):
    calls = {"tiktok": [], "shopee": []}

    def tiktok(sku, **kwargs):
        calls["tiktok"].append((sku, kwargs))
        return _success("tiktok")

    def shopee(sku, **kwargs):
        calls["shopee"].append((sku, kwargs))
        return _success("shopee")

    monkeypatch.setattr(sku_profit_service.sku_profit_tk, "estimate", tiktok)
    monkeypatch.setattr(sku_profit_service.sku_profit_shopee, "estimate", shopee)
    return calls


@pytest.mark.parametrize(
    ("value", "normalized"),
    [(0, 0), (0.22, 0.22), (1, 1)],
)
def test_ad_rate_fraction_boundaries_are_explicit(fake_probes, value, normalized):
    result = sku_profit_service.estimate("0021", platform="tiktok", ad_rate=value)
    assert result["ad_rate"] == normalized
    assert fake_probes["tiktok"][-1][1]["ad_rate"] == normalized


@pytest.mark.parametrize(
    ("value", "normalized"),
    [(0.5, 0.005), (1, 0.01), (1.1, 0.011), (22, 0.22), (100, 1)],
)
def test_ad_rate_percent_has_no_one_percent_ambiguity(fake_probes, value, normalized):
    result = sku_profit_service.estimate(
        "0021",
        platform="tiktok",
        ad_rate_percent=value,
    )
    assert result["ad_rate"] == pytest.approx(normalized)
    assert fake_probes["tiktok"][-1][1]["ad_rate"] == pytest.approx(normalized)


@pytest.mark.parametrize("value", [-1, 1.01, 22, float("nan"), float("inf")])
def test_invalid_ad_rate_fractions_are_rejected_before_probe_call(fake_probes, value):
    with pytest.raises(ValueError, match="ad_rate"):
        sku_profit_service.estimate("0021", ad_rate=value)
    assert fake_probes == {"tiktok": [], "shopee": []}


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf")])
def test_invalid_ad_rate_percent_is_rejected_before_probe_call(fake_probes, value):
    with pytest.raises(ValueError, match="ad_rate_percent"):
        sku_profit_service.estimate("0021", ad_rate_percent=value)
    assert fake_probes == {"tiktok": [], "shopee": []}


def test_ad_rate_units_are_mutually_exclusive(fake_probes):
    with pytest.raises(ValueError, match="not both"):
        sku_profit_service.estimate("0021", ad_rate=0.22, ad_rate_percent=22)


@pytest.mark.parametrize("value", [0, -1, 366, "1.5", True])
def test_invalid_lookback_is_rejected(fake_probes, value):
    with pytest.raises(ValueError, match="lookback_days"):
        sku_profit_service.estimate("0021", lookback_days=value)
    assert fake_probes == {"tiktok": [], "shopee": []}


@pytest.mark.parametrize("field", ["sale_override", "cost_override"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_overrides_are_rejected(fake_probes, field, value):
    with pytest.raises(ValueError, match=field):
        sku_profit_service.estimate("0021", **{field: value})
    assert fake_probes == {"tiktok": [], "shopee": []}


def test_platform_aliases_and_invalid_platform(fake_probes):
    assert sku_profit_service.estimate("0021", platform="tk")["ok"] is True
    assert len(fake_probes["tiktok"]) == 1
    assert sku_profit_service.estimate("0021", platform="sp")["ok"] is True
    assert len(fake_probes["shopee"]) == 1
    with pytest.raises(ValueError, match="platform"):
        sku_profit_service.estimate("0021", platform="amazon")


def test_batch_is_bounded_deduplicated_and_reports_counts(fake_probes):
    result = sku_profit_service.estimate_batch(["0021", "0021", "0018"], platform="both")

    assert result["count"] == 2
    assert result["success_count"] == 2
    assert result["failed_count"] == 0
    assert result["ok"] is True
    with pytest.raises(ValueError, match="at most 30"):
        sku_profit_service.estimate_batch([str(index) for index in range(31)])
    with pytest.raises(ValueError, match="array"):
        sku_profit_service.estimate_batch("0021")  # type: ignore[arg-type]
