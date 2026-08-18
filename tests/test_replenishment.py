from decimal import Decimal

import pytest

from domains.supply_chain_operations.replenishment import (
    DemandSignal,
    InventoryPosition,
    ReplenishmentPolicy,
    SettlementEconomics,
    blended_daily_velocity,
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


def test_blended_velocity_uses_precise_recent_and_annual_sku_facts():
    recent = DemandSignal("0007", "MY", 77, 30, "2026-07-18T03:17:18Z")
    annual = DemandSignal("0007", "MY", 540, 366, "2026-07-30T08:00:00Z")

    result = blended_daily_velocity(recent=recent, annual=annual)

    assert result == (Decimal(77) / Decimal(30)) * Decimal("0.70") + (
        Decimal(540) / Decimal(366)
    ) * Decimal("0.30")


def test_variant_family_signal_is_not_duplicated_across_skus():
    annual = DemandSignal("0010", "MY", 42, 366, "2026-07-30T08:00:00Z")

    result = blended_daily_velocity(recent=None, annual=annual)

    assert result == Decimal(42) / Decimal(366)


def test_tax_saving_is_exactly_ten_percent_of_customer_settlement_price():
    settlement = SettlementEconomics(
        units=4,
        customer_payment=Decimal("100"),
        actual_shipping_fee=Decimal("-20"),
    )

    assert settlement.customer_payment_per_unit == Decimal("25")
    assert settlement.shipping_fee_per_unit == Decimal("5")
    assert settlement.tax_saving_per_unit(
        tax_rate=Decimal("0.10"),
        fx_to_cny=Decimal("1.659101"),
    ) == Decimal("4.1477525")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SettlementEconomics(True, Decimal("1"), Decimal("1")),
        lambda: SettlementEconomics(1, Decimal("-1"), Decimal("1")),
        lambda: SettlementEconomics(1, Decimal("1"), "1"),
    ],
)
def test_invalid_settlement_economics_fail_closed(factory):
    with pytest.raises(ValueError):
        factory()
