import json
from pathlib import Path


DASHBOARD = (
    Path(__file__).resolve().parents[1]
    / "domains"
    / "supply_chain_operations"
    / "dashboard"
)


def _data() -> dict:
    text = (DASHBOARD / "data.js").read_text(encoding="utf-8")
    prefix = "window.SUPPLY_CHAIN_DATA = "
    payload = text[text.index(prefix) + len(prefix) :].strip().removesuffix(";")
    return json.loads(payload)


def test_dashboard_has_country_isolated_my_and_th_facts():
    data = _data()

    assert set(data["countries"]) == {"MY", "TH"}
    assert data["config"]["MY"]["warehouse"] == "MY8803"
    assert data["config"]["TH"]["warehouse"] == "TH8806"
    assert data["config"]["MY"]["leadDays"] == 25
    assert data["config"]["TH"]["leadDays"] == 15
    assert data["config"]["MY"]["taxSavingRate"] == 0.10
    assert data["config"]["TH"]["taxSavingRate"] == 0.15
    assert len([row for row in data["countries"]["MY"] if row["kind"] == "existing"]) == 24
    assert len([row for row in data["countries"]["TH"] if row["kind"] == "existing"]) == 22


def test_every_displayed_sku_has_a_local_main_image_and_both_channels():
    data = _data()

    for region in ("MY", "TH"):
        rows = data["countries"][region]
        assert rows
        assert any(row["kind"] == "first_stock" for row in rows)
        for row in rows:
            assert set(row["channels"]) == {"tiktok", "shopee"}
            assert (DASHBOARD / row["image"]).is_file()
            if row["kind"] == "first_stock":
                assert row["dimensionsCm"]
                assert row["weightG"] > 0
                assert row["costCny"] is not None


def test_dashboard_loads_facts_before_calculation_code_and_has_country_tabs():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    assert html.index('src="./data.js"') < html.index('src="./app.js"')
    assert 'data-region="MY"' in html
    assert 'data-region="TH"' in html
    assert "海外仓尚无、可做首批备货" in html


def test_dashboard_contains_no_remote_main_image_or_secret_dependency():
    data = (DASHBOARD / "data.js").read_text(encoding="utf-8")
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")

    assert "http://" not in data
    assert "https://" not in data
    assert "AppSecret" not in data
    assert "access_token" not in data
    assert "imageUrl" not in data
    assert "TikTok" in app
    assert "Shopee" in app
    assert "BLOCKED_DATA" in app
    assert any(
        row["dimensionsCm"] is None
        or row["weightG"] is None
        or row["costCny"] is None
        for row in _data()["countries"]["TH"]
        if row["kind"] == "existing"
    )
