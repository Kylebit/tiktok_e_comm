import json
import re
from datetime import date
from math import ceil
from pathlib import Path

from domains.supply_chain_operations.inbound_timeline import (
    InboundEvent,
    project_supply,
)
from domains.supply_chain_operations.demand_trend import calculate_full_30_day_actual


DASHBOARD = (
    Path(__file__).resolve().parents[1]
    / "domains"
    / "supply_chain_operations"
    / "dashboard"
)


def _th_batch_quantities() -> tuple[dict[str, int], dict[str, int]]:
    plan = (DASHBOARD / "inbound-plan.js").read_text(encoding="utf-8")
    maps = [json.loads(value) for value in re.findall(r"skuQuantities:\s*(\{.*?\})", plan, re.S)]
    return maps[0], maps[1]

def test_inbound_arriving_midway_is_not_available_before_its_date():
    result = project_supply(
        snapshot_date=date(2026, 8, 9),
        next_arrival_date=date(2026, 8, 24),
        available=10,
        daily_velocity=2,
        inbound_events=(InboundEvent("TH-BATCH-A", 50, date(2026, 8, 19)),),
    )

    assert result.projected_stock == 40
    assert result.counted_inbound == 50
    assert result.pending_inbound == 0


def test_inbound_later_than_new_replenishment_is_not_counted():
    result = project_supply(
        snapshot_date=date(2026, 8, 9),
        next_arrival_date=date(2026, 8, 24),
        available=20,
        daily_velocity=1,
        inbound_events=(InboundEvent("TH-BATCH-A", 80, date(2026, 8, 30)),),
    )

    assert result.projected_stock == 5
    assert result.counted_inbound == 0
    assert result.pending_inbound == 80


def test_delaying_manual_eta_reduces_projected_supply_fail_closed():
    base = {
        "snapshot_date": date(2026, 8, 9),
        "next_arrival_date": date(2026, 8, 24),
        "available": 0,
        "daily_velocity": 1,
    }
    early = project_supply(
        **base,
        inbound_events=(InboundEvent("TH-BATCH-A", 30, date(2026, 8, 14)),),
    )
    late = project_supply(
        **base,
        inbound_events=(InboundEvent("TH-BATCH-A", 30, date(2026, 8, 30)),),
    )

    assert early.projected_stock == 20
    assert late.projected_stock == 0


def test_same_sku_can_receive_two_batches_on_different_dates():
    result = project_supply(
        snapshot_date=date(2026, 8, 9),
        next_arrival_date=date(2026, 8, 24),
        available=0,
        daily_velocity=10,
        inbound_events=(
            InboundEvent("TH-BATCH-OLD", 400, date(2026, 8, 15)),
            InboundEvent("TH-BATCH-NEW", 400, date(2026, 8, 24)),
        ),
    )

    assert result.projected_stock == 710
    assert result.counted_inbound == 800
    assert result.pending_inbound == 0


def test_th_0021_exact_paginated_batch_split_changes_arrival_stock():
    daily_velocity = 37.280833333333
    result = project_supply(
        snapshot_date=date(2026, 8, 9),
        next_arrival_date=date(2026, 8, 31),
        available=0,
        daily_velocity=daily_velocity,
        inbound_events=(
            InboundEvent("THML4038-58701", 200, date(2026, 8, 19)),
            InboundEvent("THSL4038-59557", 600, date(2026, 8, 26)),
        ),
    )

    assert result.projected_stock == 413
    assert result.counted_inbound == 800
    assert [step.kind for step in result.steps] == [
        "CONSUMPTION",
        "INBOUND",
        "CONSUMPTION",
        "INBOUND",
        "CONSUMPTION",
    ]
    assert [step.demand for step in result.steps if step.kind == "CONSUMPTION"] == [373, 261, 187]
    assert [step.stock_after for step in result.steps] == [0, 200, 0, 600, 413]
    assert result.projection_method == "TIME_PHASED_BATCH_EVENTS_V1"
    assert ceil(daily_velocity * 33) - result.projected_stock == 818


def test_th_0021_full_30_day_orders_get_a_separate_arrival_result():
    actual = calculate_full_30_day_actual(tiktok_units=606, shopee_units=201)
    result = project_supply(
        snapshot_date=date(2026, 8, 9),
        next_arrival_date=date(2026, 8, 31),
        available=0,
        daily_velocity=actual["dailyVelocity"],
        inbound_events=(
            InboundEvent("THML4038-58701", 200, date(2026, 8, 19)),
            InboundEvent("THSL4038-59557", 600, date(2026, 8, 26)),
        ),
    )

    assert actual["totalUnits"] == 807
    assert actual["dailyVelocity"] == 26.9
    assert result.projected_stock == 476
    assert ceil(actual["dailyVelocity"] * 33) == 888
    assert ceil(actual["dailyVelocity"] * 33) - result.projected_stock == 412


def test_every_thailand_inbound_sku_reconciles_to_complete_paginated_batches():
    old_batch, new_batch = _th_batch_quantities()
    data_text = (DASHBOARD / "data.js").read_text(encoding="utf-8")
    payload = data_text.removeprefix("window.SUPPLY_CHAIN_DATA = ").strip().removesuffix(";")
    thailand = json.loads(payload)["countries"]["TH"]

    assert sum(old_batch.values()) == 2100
    assert sum(new_batch.values()) == 1250
    assert old_batch["0021"] == 200
    assert new_batch["0021"] == 600
    for row in thailand:
        if row["inventory"]["inbound"] > 0:
            assert old_batch.get(row["sku"], 0) + new_batch.get(row["sku"], 0) == row["inventory"]["inbound"]


def test_batch_identity_is_required():
    try:
        project_supply(
            snapshot_date=date(2026, 8, 9),
            next_arrival_date=date(2026, 8, 24),
            available=0,
            daily_velocity=1,
            inbound_events=(InboundEvent("", 10, date(2026, 8, 15)),),
        )
    except TypeError as exc:
        assert "batch_id" in str(exc)
    else:
        raise AssertionError("empty batch identity must fail closed")


def test_dashboard_uses_batch_level_overrides_and_never_sku_level_eta():
    app = open("domains/supply_chain_operations/dashboard/app.js", encoding="utf-8").read()
    batch_app = open("domains/supply_chain_operations/dashboard/inbound-batches.js", encoding="utf-8").read()
    plan = open("domains/supply_chain_operations/dashboard/inbound-plan.js", encoding="utf-8").read()

    assert "supply-chain-inbound-batch-timing-v3" in app
    assert "const inboundEtaId = (region, batchId)" in app
    assert "data-sku=\"${escapeHtml(item.sku)}\">修改到货时间" not in app
    assert "inboundEtaDialog" not in app
    assert "overrideId(region, batchId)" in batch_app
    assert "data-action=\"save\"" in batch_app
    assert "CREATED_PLUS_4_DAYS_ESTIMATE" in plan
    assert "NOT_YET_INBOUND" in plan
    assert "timingsValid" in app
    assert 'batchId: "THML4038-58701"' in plan
    assert 'batchId: "THSL4038-59557"' in plan
    old_batch, new_batch = _th_batch_quantities()
    assert old_batch["0021"] == 200
    assert new_batch["0021"] == 600
    assert len(old_batch) == 13
    assert len(new_batch) == 8
    assert "批次 SKU 分摊未对平" in app
    assert "未入库 · 建单+4天估算" in app
