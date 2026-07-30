from decimal import Decimal

import pytest

from domains.supply_chain_operations.replenishment import (
    DemandSignal,
    InventoryPosition,
    ReplenishmentPolicy,
    recommend_replenishment,
)


def test_unknown_inventory_returns_arrival_cover_upper_bound():
    result = recommend_replenishment(
        DemandSignal("0003", "MY", units_sold=187, window_days=30, captured_at="2026-07-18T03:17:18Z"),
        InventoryPosition(available=None, high_confidence_inbound=None),
        ReplenishmentPolicy(lead_days=25, target_cover_days=30),
    )

    assert result.daily_velocity == Decimal(187) / Decimal(30)
    assert result.lead_demand == 156
    assert result.target_arrival_stock == 219
    assert result.recommended_quantity == 219
    assert result.status == "PROVISIONAL_UPPER_BOUND"


def test_trusted_inventory_is_consumed_by_lead_demand_before_arrival_target():
    result = recommend_replenishment(
        DemandSignal("0003", "MY", units_sold=187, window_days=30, captured_at="2026-07-18T03:17:18Z"),
        InventoryPosition(available=200, high_confidence_inbound=20),
        ReplenishmentPolicy(lead_days=25, target_cover_days=30),
    )

    assert result.projected_stock_at_arrival == 64
    assert result.recommended_quantity == 155
    assert result.status == "RECOMMEND"


def test_counted_and_low_confidence_inbound_do_not_reduce_recommendation():
    result = recommend_replenishment(
        DemandSignal("0001", "TH", units_sold=30, window_days=30, captured_at="2026-07-30T00:00:00Z"),
        InventoryPosition(
            available=0,
            high_confidence_inbound=0,
            counted_not_shelved=20,
            low_confidence_inbound=80,
        ),
        ReplenishmentPolicy(lead_days=15, target_cover_days=30),
    )

    assert result.recommended_quantity == 33
    assert result.projected_stock_at_arrival == 0


def test_complete_stock_can_produce_hold():
    result = recommend_replenishment(
        DemandSignal("0008", "MY", units_sold=79, window_days=30, captured_at="2026-07-18T03:17:18Z"),
        InventoryPosition(available=150, high_confidence_inbound=20),
        ReplenishmentPolicy(lead_days=25, target_cover_days=30),
    )

    assert result.recommended_quantity == 0
    assert result.status == "HOLD"


def test_current_my_candidate_batch_reproduces_496_unit_upper_bound():
    quantities = []
    for sku, sold in (("0003", 187), ("0015", 80), ("0008", 79), ("0007", 77)):
        result = recommend_replenishment(
            DemandSignal(sku, "MY", units_sold=sold, window_days=30, captured_at="2026-07-18T03:17:18Z"),
            InventoryPosition(available=None, high_confidence_inbound=None),
            ReplenishmentPolicy(lead_days=25, target_cover_days=30),
        )
        quantities.append(result.recommended_quantity)

    assert quantities == [219, 94, 93, 90]
    assert sum(quantities) == 496


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: DemandSignal("", "MY", 1, 30, "now"), "seller_sku"),
        (lambda: DemandSignal("1", "MY", True, 30, "now"), "units_sold"),
        (lambda: InventoryPosition(available=-1, high_confidence_inbound=0), "available"),
        (lambda: InventoryPosition(available=True, high_confidence_inbound=0), "available"),
        (lambda: ReplenishmentPolicy(lead_days=0), "lead_days"),
    ],
)
def test_invalid_inputs_fail_closed(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()
