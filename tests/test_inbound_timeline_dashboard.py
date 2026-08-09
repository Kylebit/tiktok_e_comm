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
        inbound_events=(InboundEvent(50, date(2026, 8, 19)),),
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
        inbound_events=(InboundEvent(80, date(2026, 8, 30)),),
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
        inbound_events=(InboundEvent(30, date(2026, 8, 14)),),
    )
    late = project_supply(
        **base,
        inbound_events=(InboundEvent(30, date(2026, 8, 30)),),
    )

    assert early.projected_stock == 20
    assert late.projected_stock == 0
