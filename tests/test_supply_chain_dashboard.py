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
        region: data["config"][region]["fixedHeadFreightUnitCny"]
        for region in regions
    } == {"MY": 1, "TH": 1, "VN": 1, "PH": 1}
    for region in regions:
        assert "freightRateCnyM3" not in data["config"][region]
        assert "minimumBillableM3" not in data["config"][region]
        assert "inboundSurchargeCny" not in data["config"][region]
    assert {
        region: len([row for row in rows if row["kind"] == "existing"])
        for region, rows in data["countries"].items()
    } == {"MY": 24, "TH": 23, "VN": 10, "PH": 4}

    for region, rows in data["countries"].items():
        for row in rows:
            assert row["channels"]["tiktok"]["source"].startswith(f"TikTok {region}")
            assert row["channels"]["shopee"]["source"].startswith(f"Shopee {region}")


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

    assert sum(row["inventory"]["available"] for row in data["countries"]["VN"]) == 345
    assert sum(row["inventory"]["available"] for row in data["countries"]["PH"]) == 33
    assert _row(data, "VN", "0004")["sourceAliases"] == ["0004", "880004"]
    assert _row(data, "VN", "0004")["kind"] == "existing"
    assert _row(data, "VN", "0004")["inventory"] == {
        "stock": 47,
        "available": 47,
        "allocated": 0,
        "frozen": 0,
        "inbound": 0,
        "warehouse": "VN8805",
    }
    assert _row(data, "PH", "0820")["sourceAliases"] == ["0820", "770820"]
    assert _row(data, "PH", "0820")["inventory"]["available"] == 0
    assert _row(data, "PH", "0821")["sourceAliases"] == ["0821", "770821"]
    assert _row(data, "PH", "0821")["inventory"]["available"] == 2
    assert _row(data, "PH", "0822")["sourceAliases"] == ["0822", "770822"]
    assert _row(data, "PH", "0822")["inventory"]["available"] == 0
    assert "inventoryIdentityBlocker" not in data["config"]["PH"]
    assert "inventoryIdentityBlocker" not in data["config"]["VN"]
    assert "880004→0004" in data["config"]["VN"]["inventoryIdentityEvidence"]
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
    assert "全部 SKU 备货建议" in html
    assert 'id="skuRows"' in html
    assert 'id="skuEmpty"' in html
    assert 'id="existingRows"' not in html
    assert 'id="firstStockRows"' not in html
    assert '<option value="RECENT30">近30天有动销</option>' in html
    assert "待核经济性" not in html


def test_every_recent_30_day_sku_is_present_and_filterable_without_economic_gate():
    data = _data()
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    recent_counts = {}
    for region, rows in data["countries"].items():
        assert len({row["sku"] for row in rows}) == len(rows)
        recent_rows = [
            row
            for row in rows
            if any(
                (channel.get("recent30Units") or 0) > 0
                for channel in row["channels"].values()
            )
        ]
        recent_counts[region] = len(recent_rows)
        assert all((DASHBOARD / row["image"]).is_file() for row in recent_rows)

    assert recent_counts == {"MY": 20, "TH": 47, "VN": 43, "PH": 76}
    assert 'filter === "RECENT30" && recent30Units > 0' in app
    assert 'status = item.kind === "first_stock" ? "FIRST_STOCK" : "REPLENISH"' in app
    assert '"REVIEW"' not in app
    assert "待核经济性" not in app
    assert "待核经济性" not in html


def test_head_freight_is_fixed_at_one_cny_per_unit_and_benefit_is_presentation_only():
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    reference = (
        DASHBOARD.parent
        / "skills"
        / "manage-seaya-replenishment"
        / "references"
        / "decision-contract.md"
    ).read_text(encoding="utf-8")

    assert "recommendedUnits * config.fixedHeadFreightUnitCny" in app
    assert "const headFreightUnit = config.fixedHeadFreightUnitCny" in app
    assert "economics(item, metrics)" not in app
    assert "netUnit > 0" not in app
    assert "CNY 1 per unit" in reference
    assert "must not remove the demand row" in reference


def test_quantity_is_independent_from_dimensions_weight_and_cost():
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    assert "const recommended = Math.max(0, arrivalTarget - projectedAtArrival)" in app
    assert "calculationReady" not in app
    assert "const handlingUnit = item.weightReady" in app
    assert "const netTotal = netUnit === null ? null" in app
    assert '"BLOCKED_DATA"' not in app
    assert "体积待补充" in app
    assert "待补充（需重量）" in app
    assert "成本待补充" in app
    assert 'filter === "MISSING_DATA" && item.dataIncomplete' in app
    assert '<option value="MISSING_DATA">资料待补</option>' in html
    assert "两类建议按同一规则排序并在同一张表中展示" in html


def test_dashboard_consumes_segmented_trend_and_discloses_fallbacks():
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    assert 'trend.method !== "segmented_7_8_15_v1"' in app
    assert 'demand.trendClass === "SPIKE"' in app
    assert "const targetCoverageDays = spikeProtection ? 15" in app
    assert "缺逐日分段，按30日+长窗降级" in app
    assert "趋势算法数据覆盖" in app
    assert "最近7天60% + 第8–15天30% + 第16–30天10%" in html
    assert "缺逐日分段时明确降级，不伪造趋势" in html


def test_dashboard_contains_no_remote_image_or_secret_dependency_and_marks_blockers():
    data_text = (DASHBOARD / "data.js").read_text(encoding="utf-8")
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")

    assert "http://" not in data_text
    assert "https://" not in data_text
    assert "AppSecret" not in data_text
    assert "access_token" not in data_text
    assert "imageUrl" not in data_text
    assert 'channel.state !== "READY"' in app
    assert 'method: "COUNTRY_MISMATCH"' in app
    assert "BLOCKED_COUNTRY_SOURCE" in app
    assert "channelDemand(effectiveItem.channels.tiktok, region, \"TikTok\")" in app
    assert "channelDemand(effectiveItem.channels.shopee, region, \"Shopee\")" in app
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
