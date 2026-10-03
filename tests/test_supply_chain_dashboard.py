import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
from threading import Thread

import pytest


DASHBOARD = (
    Path(__file__).resolve().parents[1]
    / "domains"
    / "supply_chain_operations"
    / "dashboard"
)


def test_four_country_inbound_headline_uses_all_reconciled_batches(tmp_path):
    node_root = Path('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node')
    node = node_root / 'bin/node.exe'
    if not node.is_file():
        pytest.skip('bundled Node unavailable')
    env = {**os.environ, 'NODE_PATH': str(node_root / 'node_modules')}
    script = Path(__file__).resolve().parent / 'browser/supply_inbound_headline.cjs'
    handler = partial(SimpleHTTPRequestHandler, directory=str(DASHBOARD))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = subprocess.run(
            [str(node), str(script), f'http://127.0.0.1:{server.server_port}', str(tmp_path)],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=90,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr


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
    } == {"MY": 24, "TH": 28, "VN": 10, "PH": 11}

    for region, rows in data["countries"].items():
        for row in rows:
            assert row["channels"]["tiktok"]["source"].startswith(f"TikTok {region}")
            assert row["channels"]["shopee"]["source"].startswith(f"Shopee {region}")
            for channel in row["channels"].values():
                assert channel["quantityBasis"] == "valid_order"
                assert channel["economicsBasis"] in {
                    "settlement",
                    "settlement_unavailable",
                }
                assert type(channel["settlementUnits"]) is int


def test_thailand_truncated_codes_are_normalized_without_fuzzy_merging():
    data = _data()

    assert _row(data, "TH", "0400")["inventory"] == {
        "stock": 81,
        "available": 81,
        "allocated": 0,
        "frozen": 0,
        "inbound": 0,
        "warehouse": "TH8806",
    }
    assert _row(data, "TH", "0401")["inventory"] == {
        "stock": 136,
        "available": 136,
        "allocated": 0,
        "frozen": 0,
        "inbound": 80,
        "warehouse": "TH8806",
    }
    assert _row(data, "TH", "0401")["sourceAliases"] == ["0401", "990401"]
    assert _row(data, "TH", "0026")["inventory"]["inbound"] == 0
    assert _row(data, "TH", "0604")["inventory"]["available"] == 0
    assert _row(data, "TH", "0605")["inventory"]["available"] == 6
    assert _row(data, "TH", "0605")["sourceAliases"] == ["0605", "990605"]
    assert _row(data, "TH", "0613")["inventory"]["available"] == 16


def test_vietnam_and_philippines_use_complete_shopee_settlement_snapshots():
    data = _data()

    assert sum(row["inventory"]["available"] for row in data["countries"]["VN"]) == 523
    assert sum(row["inventory"]["available"] for row in data["countries"]["PH"]) == 28
    assert _row(data, "VN", "0004")["sourceAliases"] == ["0004", "880004"]
    assert _row(data, "VN", "0004")["kind"] == "existing"
    assert _row(data, "VN", "0004")["inventory"] == {
        "stock": 42,
        "available": 42,
        "allocated": 0,
        "frozen": 0,
        "inbound": 0,
        "warehouse": "VN8805",
    }
    assert _row(data, "PH", "0820")["sourceAliases"] == ["0820", "770820"]
    assert _row(data, "PH", "0820")["inventory"]["available"] == 0
    assert _row(data, "PH", "0820")["inventory"]["inbound"] == 40
    assert _row(data, "PH", "0821")["sourceAliases"] == ["0821", "770821"]
    assert _row(data, "PH", "0821")["inventory"]["available"] == 2
    assert _row(data, "PH", "0821")["inventory"]["inbound"] == 20
    assert _row(data, "PH", "0822")["sourceAliases"] == ["0822", "770822"]
    assert _row(data, "PH", "0822")["inventory"]["available"] == 0
    assert _row(data, "PH", "0822")["inventory"]["inbound"] == 40
    assert {
        region: sum(row["inventory"]["inbound"] for row in data["countries"][region])
        for region in ("MY", "TH", "VN", "PH")
    } == {"MY": 0, "TH": 1270, "VN": 0, "PH": 510}
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
    assert sum(
        row["channels"]["shopee"]["settlementUnits"]
        for row in data["countries"]["VN"]
    ) == 273
    assert sum(
        row["channels"]["shopee"]["settlementUnits"]
        for row in data["countries"]["PH"]
    ) == 622


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

    assert html.index('src="./data.js?') < html.index('src="./app.js?')
    assert html.index('src="./transport-history.js?') < html.index('src="./app.js?')
    assert html.index('src="./inbound-plan.js?') < html.index('src="./app.js?')
    assert html.index('src="./inbound-timeline.js?') < html.index('src="./app.js?')
    for region in ("MY", "TH", "VN", "PH"):
        assert f'data-region="{region}"' in html
    assert 'data-region="SUMMARY"' in html
    assert "全部 SKU 备货建议" in html
    assert 'id="skuRows"' in html
    assert 'id="skuEmpty"' in html
    assert 'id="existingRows"' not in html
    assert 'id="firstStockRows"' not in html
    assert '<option value="RECENT30">近30天有动销</option>' in html
    assert "待核经济性" not in html
    assert "正式建议件数只使用 TikTok 与 Shopee 的有效订单事实" in html
    assert "结算口径销量仅为旧版参考，不构成正式补货数量" in html


def test_dashboard_has_strict_four_country_over_ten_summary():
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    assert "function renderSummary()" in app
    assert ".filter(item => item.recommendationMax > 10)" in app
    assert 'region = activeRegion === "SUMMARY" ? item.region : activeRegion' in app
    assert 'class="region-badge"' in app
    assert "同一 SKU 在不同国家分别成行" in app
    assert 'data-region="${escapeHtml(region)}"' in app
    assert "const sourceRegion = button.dataset.region || activeRegion" in app
    assert "四国汇总" in html
    assert "&gt;10" in html


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

    assert recent_counts == {"MY": 54, "TH": 87, "VN": 60, "PH": 68}
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

    assert "recommended: Math.max(0, arrivalTarget - projectedAtArrival)" in app
    assert "function channelRecent30Demand" in app
    assert "Number.isInteger(channel.recent30Units)" in app
    assert "daily: channel.recent30Units / 30" in app
    assert 'method: "FULL_30_DAY_ACTUAL"' in app
    assert "demandScenarios: {trend: trendScenario, recent30: recent30Scenario}" in app
    assert "两套逐步计算" in app
    assert "日期权重" in app
    assert "30日实绩" in app
    assert "const NEW_REPLENISHMENT_PREPARATION_DAYS = 3" in app
    assert "const NEW_REPLENISHMENT_DOMESTIC_WAREHOUSE_DAYS = 4" in app
    assert "const transportPolicy = TRANSPORT_HISTORY.regions[region]" in app
    assert "+ NEW_REPLENISHMENT_DOMESTIC_WAREHOUSE_DAYS" in app
    assert "+ effectiveTransportDays" in app
    assert "Math.ceil(dailyVelocity * effectiveLeadDays)" in app
    assert "TIMELINE.addDays(DATA.snapshotDate, effectiveLeadDays)" in app
    assert "3天备货 + 4天到国内仓 + ${item.effectiveTransportDays}天海外运输" in app
    assert "TIMELINE.projectSupply" in app
    assert "countedInbound: supplyProjection.countedInbound" in app
    assert "calculationReady" not in app


def test_order_refresh_keeps_inventory_clock_separate():
    apply_script = (
        DASHBOARD.parent
        / "skills"
        / "manage-seaya-replenishment"
        / "scripts"
        / "apply_order_demand.py"
    ).read_text(encoding="utf-8")
    data = _data()
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")

    assert 'data["orderDemandCapturedAt"] = snapshot["capturedAt"]' in apply_script
    assert 'data["snapshotDate"] = snapshot["capturedAt"][:10]' not in apply_script
    assert data["snapshotDate"] == "2026-09-02"
    assert data["orderDemandCapturedAt"].startswith("2026-09-01T")
    assert "const handlingUnit = item.weightReady" in app
    assert "const netTotal = netUnit === null ? null" in app
    assert '"BLOCKED_DATA"' not in app
    assert "体积待补充" in app
    assert "待补充（需重量）" in app
    assert "成本待补充" in app
    assert 'filter === "MISSING_DATA" && item.dataIncomplete' in app
    assert '<option value="MISSING_DATA">资料待补</option>' in html
    assert "两类建议按同一规则排序并在同一张表中展示" in html
    assert "本次新货预计可售日按 3 天备货 + 4 天到国内仓 + 当前国家海外运输周期计算" in html


def test_dashboard_consumes_segmented_trend_and_discloses_fallbacks():
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    transport = (DASHBOARD / "transport-history.js").read_text(encoding="utf-8")

    assert 'trend.method !== "segmented_7_8_15_v1"' in app
    assert 'demand.trendClass === "SPIKE"' in app
    assert "const targetCoverageDays = spikeProtection ? 15" in app
    assert "transportPolicy.approvedTargetCoverageDays" in app
    assert "approvedTargetCoverageDays: 33" in transport
    assert "Thailand target coverage = 33 days" in transport
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
    assert 'channel.quantityBasis !== "valid_order"' in app
    assert 'method: "BLOCKED_ORDER_DATA"' in app
    assert "channel.settlementUnits" in app
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


def test_dashboard_uses_complete_new_order_snapshots_for_quantity():
    data = _data()

    assert data["quantityBasis"] == "valid_order"
    assert data["economicsBasis"] == "settlement"
    assert data["snapshotDate"] == "2026-09-02"
    assert {region: len(rows) for region, rows in data["countries"].items()} == {
        "MY": 92,
        "TH": 123,
        "VN": 114,
        "PH": 147,
    }
    expected = {
        "MY": {"tiktok": (740, 520), "shopee": (117, 76)},
        "TH": {"tiktok": (1891, 1610), "shopee": (1023, 815)},
        "VN": {"tiktok": (275, 209), "shopee": (21, 17)},
        "PH": {"tiktok": (161, 144), "shopee": (55, 44)},
    }
    for region, platforms in expected.items():
        evidence = data["config"][region]["orderDemandEvidence"]
        for platform, (seen, included) in platforms.items():
            assert evidence[platform]["ordersSeen"] == seen
            assert evidence[platform]["ordersIncluded"] == included
            assert evidence[platform]["itemLinesUnresolved"] == 0
            assert len(evidence[platform]["digest"]) == 64


def test_dashboard_records_current_seaya_inventory_lineage():
    data = _data()
    expected = {
        "MY": (31, 24),
        "TH": (42, 28),
        "VN": (15, 10),
        "PH": (17, 11),
    }

    for region, (raw_rows, canonical_skus) in expected.items():
        evidence = data["config"][region]["inventoryEvidence"]
        assert evidence["capturedAt"] == "2026-09-02T06:34:25.064+08:00"
        assert evidence["source"] == "seaya_browser_inventory_complete_2026-09-02"
        assert evidence["rawRows"] == raw_rows
        assert evidence["canonicalSkuCount"] == canonical_skus
        assert len(evidence["digest"]) == 64


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


def test_inbound_eta_is_estimated_time_phased_and_locally_editable():
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    batch_html = (DASHBOARD / "inbound-batches.html").read_text(encoding="utf-8")
    batch_app = (DASHBOARD / "inbound-batches.js").read_text(encoding="utf-8")
    plan = (DASHBOARD / "inbound-plan.js").read_text(encoding="utf-8")
    timeline = (DASHBOARD / "inbound-timeline.js").read_text(encoding="utf-8")

    assert 'anchorAt: "2026-08-22T16:48:05+08:00"' in plan
    assert 'estimatedSellableDate: "2026-09-04"' in plan
    assert 'estimatedSellableConfirmedAt: "2026-09-01T23:47:53+08:00"' in plan
    assert 'confidence: "USER_CONFIRMED_BATCH_ETA"' in plan
    assert 'estimatedSellableSource: "user_confirmed_arrival_and_shelving_date"' in plan
    assert 'anchorType: "REACHED_DOMESTIC_WAREHOUSE"' in plan
    assert 'inboundStatus: "INBOUND_CONFIRMED"' in plan
    assert 'estimatedAnchorAt: null' in plan
    assert 'anchorType: "MARKED_SHIPPED"' not in plan
    assert 'anchorType: "CREATED_FALLBACK"' not in plan
    assert 'batchId: "THSL4038-60638"' in plan
    assert '"0021": 600' in plan
    assert '"0140": 400' in plan
    assert 'allocationPolicy: "EXACT_BATCH_SKU_REQUIRED"' in plan
    assert "function projectSupply" in timeline
    assert "const steps = []" in timeline
    assert "crossBorderFallbackUnits" in timeline
    assert "firstLocalStockoutDate" in timeline
    assert "localFulfillmentRate" in timeline
    assert 'projectionMethod: "TIME_PHASED_BATCH_EVENTS_V1"' in timeline
    assert "if (event.day > horizonDays)" in timeline
    assert "supplySteps: supplyProjection.steps" in app
    assert "currentOverride?.estimatedSellableDate\n      || batch.estimatedSellableDate" in app
    assert "overrideUpdateTime > planConfirmationTime" in timeline
    assert "saved.basePlanConfirmedAt === batch.estimatedSellableConfirmedAt" in timeline
    assert "function projectionAuditHtml(item)" in app
    assert "本土仓不足不是完全断货" in app
    assert "跨境直发兜底" in app
    assert "BLOCKED_STALE_INVENTORY" in app
    assert 'filter === "LOCAL_FALLBACK"' in app
    assert 'item.sku === "0021"' not in app
    assert 'supply-chain-inbound-batch-timing-v3' in timeline
    assert "const inboundEtaId = (region, batchId)" in app
    assert 'href="./inbound-batches.html"' in html
    assert 'id="inboundEtaDialog"' not in html
    assert "前往批次时间确认页" in app
    assert 'id="batchRows"' in batch_html
    assert "确认批次时间" in batch_app
    assert "overrideId(region, batchId)" in batch_app
    assert "OVERRIDE_STORE.write(localStorage, overrideStorage, nextOverrides)" in batch_app
    assert "storage.setItem(KEY, JSON.stringify(state))" in timeline
    assert 'name="anchorAt"' in batch_app
    assert 'type="datetime-local"' in batch_app
    assert "batch.transportDays + batch.shelvingDays" not in batch_app
    assert "|| batch.estimatedSellableDate" in batch_app
    assert "function currentOverride(batch)" in batch_app
    assert "overrideUpdateTime > planConfirmationTime" in timeline
    assert "saved.basePlanConfirmedAt === batch.estimatedSellableConfirmedAt" in timeline
    assert "basePlanConfirmedAt: batch?.estimatedSellableConfirmedAt || null" in batch_app
    assert "TIMELINE.addDays(effectiveAnchorDate, batch.transportDays)" in batch_app
    assert "用户确认到达并上架" in batch_app
    assert "签收上架缓冲 <b>0 天（已取消）</b>" in batch_app
    assert "有“已入库”日志时使用实际时间" in batch_html
    assert "尚未入库时明确标记“未入库”" in batch_html
    assert "未入库 · 建单时间 + 4 天估算" in batch_app
