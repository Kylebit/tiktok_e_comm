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


def _row(data: dict, region: str, sku: str) -> dict:
    return next(row for row in data["countries"][region] if row["sku"] == sku)


def test_dashboard_has_four_country_isolated_facts_and_policies():
    data = _data()

    assert set(data["countries"]) == {"MY", "TH", "VN", "PH"}
    regions = ("MY", "TH", "VN", "PH")
    assert {region: data["config"][region]["warehouse"] for region in regions} == {
        "MY": "MY8803",
        "TH": "TH8806",
        "VN": "VN8805",
        "PH": "PH8807",
    }
    assert {region: data["config"][region]["leadDays"] for region in regions} == {
        "MY": 25,
        "TH": 15,
        "VN": 15,
        "PH": 25,
    }
    assert {region: data["config"][region]["taxSavingRate"] for region in regions} == {
        "MY": 0.10,
        "TH": 0.15,
        "VN": 0.10,
        "PH": 0.0,
    }
    assert {
        region: len([row for row in rows if row["kind"] == "existing"])
        for region, rows in data["countries"].items()
    } == {"MY": 24, "TH": 23, "VN": 9, "PH": 4}


def test_thailand_truncated_codes_are_normalized_without_fuzzy_merging():
    data = _data()

    assert _row(data, "TH", "0400")["inventory"] == {
        "stock": 111,
        "available": 111,
        "allocated": 0,
        "frozen": 0,
        "inbound": 0,
        "warehouse": "TH8806",
    }
    assert _row(data, "TH", "0401")["inventory"] == {
        "stock": 51,
        "available": 51,
        "allocated": 0,
        "frozen": 0,
        "inbound": 100,
        "warehouse": "TH8806",
    }
    assert _row(data, "TH", "0401")["sourceAliases"] == ["0401", "990401"]
    assert _row(data, "TH", "0604")["inventory"]["available"] == 0
    assert _row(data, "TH", "0605")["inventory"]["available"] == 13
    assert _row(data, "TH", "0605")["sourceAliases"] == ["0605", "990605"]
    assert _row(data, "TH", "0613")["inventory"]["available"] == 20


def test_vietnam_and_philippines_use_complete_shopee_settlement_snapshots():
    data = _data()

    assert sum(row["inventory"]["available"] for row in data["countries"]["VN"]) == 298
    assert sum(row["inventory"]["available"] for row in data["countries"]["PH"]) == 33
    assert _row(data, "PH", "0820")["sourceAliases"] == ["0820", "770820"]
    assert _row(data, "PH", "0820")["inventory"]["available"] == 0
    assert _row(data, "PH", "0821")["sourceAliases"] == ["0821", "770821"]
    assert _row(data, "PH", "0821")["inventory"]["available"] == 2
    assert _row(data, "PH", "0822")["sourceAliases"] == ["0822", "770822"]
    assert _row(data, "PH", "0822")["inventory"]["available"] == 0
    assert "inventoryIdentityBlocker" not in data["config"]["PH"]
    assert "完整 SKU" in data["config"]["VN"]["inventoryIdentityBlocker"]
    for region in ("VN", "PH"):
        assert all("X" not in row["sku"] for row in data["countries"][region])
        assert all(
            row["channels"]["shopee"]["state"] == "READY"
            for row in data["countries"][region]
        )
        assert data["config"][region]["shopeeDemandEvidence"]["errors"] == 0

    assert data["config"]["VN"]["shopeeDemandEvidence"] == {
        "window": "2025-07-30~2026-07-30",
        "orders": 205,
        "successfulDetails": 205,
        "errors": 0,
        "mappedSkuCount": 49,
        "catalogResolvedItems": 21,
        "unmappedItemLines": 0,
    }
    assert data["config"]["PH"]["shopeeDemandEvidence"] == {
        "window": "2025-07-30~2026-07-30",
        "orders": 450,
        "successfulDetails": 450,
        "errors": 0,
        "mappedSkuCount": 76,
        "catalogResolvedItems": 193,
        "unmappedItemLines": 19,
    }
    assert sum(row["channels"]["shopee"]["units"] for row in data["countries"]["VN"]) == 273
    assert sum(row["channels"]["shopee"]["units"] for row in data["countries"]["PH"]) == 622


def test_every_displayed_sku_has_a_local_main_image_and_both_channels():
    data = _data()

    for region, rows in data["countries"].items():
        assert rows
        assert any(row["kind"] == "first_stock" for row in rows)
        for row in rows:
            assert set(row["channels"]) == {"tiktok", "shopee"}
            assert (DASHBOARD / row["image"]).is_file()
            if row["kind"] == "first_stock":
                complete = (
                    isinstance(row["dimensionsCm"], list)
                    and len(row["dimensionsCm"]) == 3
                    and all(
                        type(value) in (int, float) and value > 0
                        for value in row["dimensionsCm"]
                    )
                    and type(row["weightG"]) in (int, float)
                    and row["weightG"] > 0
                    and type(row["costCny"]) in (int, float)
                    and row["costCny"] > 0
                )
                assert row["dimensionsCm"] is None or (
                    isinstance(row["dimensionsCm"], list)
                    and len(row["dimensionsCm"]) == 3
                )
                assert row["weightG"] is None or (
                    type(row["weightG"]) in (int, float) and row["weightG"] >= 0
                )
                assert row["costCny"] is None or (
                    type(row["costCny"]) in (int, float) and row["costCny"] >= 0
                )
                if not complete:
                    assert (
                        row["dimensionsCm"] is None
                        or row["weightG"] in (None, 0)
                        or row["costCny"] in (None, 0)
                    )


def test_dashboard_loads_facts_before_calculation_code_and_has_four_country_tabs():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    assert html.index('src="./data.js"') < html.index('src="./app.js"')
    for region in ("MY", "TH", "VN", "PH"):
        assert f'data-region="{region}"' in html
    assert "海外仓尚无、可做首批备货" in html


def test_dashboard_contains_no_remote_image_or_secret_dependency_and_marks_blockers():
    data_text = (DASHBOARD / "data.js").read_text(encoding="utf-8")
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")

    assert "http://" not in data_text
    assert "https://" not in data_text
    assert "AppSecret" not in data_text
    assert "access_token" not in data_text
    assert "imageUrl" not in data_text
    assert 'channel.state !== "READY"' in app
    assert "BLOCKED_AUTH" in app
    assert "PENDING_REFRESH" in app
    assert "inventoryIdentityBlocker" in app
    assert "shopeeDemandEvidence" in app
    assert "unmappedItemLines" in app
    assert "082X" not in data_text
    assert 'typeof effectiveItem.costCny === "number"' in app


def test_blocked_logistics_rows_have_local_manual_completion_controls():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")

    assert 'id="manualInputDialog"' in html
    for name in ("lengthCm", "widthCm", "heightCm", "weightG", "costCny"):
        assert f'name="{name}"' in html
    assert 'data-action="manual-entry"' in app
    assert 'supply-chain-manual-logistics-v1' in app
    assert "localStorage.setItem" in app
    assert "clearManualInput" in app
    assert "保存并重新计算" in html
