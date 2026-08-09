from datetime import date

from domains.supply_chain_operations.inbound_timeline import (
    InboundEvent,
    project_supply,
)

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
    assert "REACHED_DOMESTIC_WAREHOUSE_REQUIRED" in plan
    assert "timingsValid" in app
    assert 'batchId: "THML4038-58701"' in plan
    assert 'batchId: "THSL4038-59557"' in plan
    assert 'skuQuantities: {"0021": null}' in plan
    assert "批次分摊或已入库起算未就绪" in app
