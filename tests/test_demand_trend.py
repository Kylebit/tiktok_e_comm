import pytest

from domains.supply_chain_operations.demand_trend import calculate_segmented_trend


def test_recent_15_day_regime_forecasts_54_units_instead_of_30():
    decision = calculate_segmented_trend(
        last_7_units=14,
        days_8_to_15_units=16,
        days_16_to_30_units=0,
        active_sales_days_30=15,
        max_daily_units_30=2,
    )

    assert decision["dailyVelocity"] == 1.8
    assert decision["forecast30Units"] == 54
    assert decision["trendClass"] == "RISING"
    assert decision["confidence"] == "HIGH"
    assert decision["denominatorBasis"] == "calendar_days_no_stockout_adjustment"
    assert decision["spikeProtectionTargetDays"] is None


def test_one_day_viral_burst_gets_15_day_first_stock_protection():
    decision = calculate_segmented_trend(
        last_7_units=30,
        days_8_to_15_units=0,
        days_16_to_30_units=0,
        active_sales_days_30=1,
        max_daily_units_30=30,
    )

    assert decision["trendClass"] == "SPIKE"
    assert decision["confidence"] == "LOW"
    assert decision["spikeProtectionTargetDays"] == 15


def test_verified_sellable_days_replace_calendar_denominators():
    decision = calculate_segmented_trend(
        last_7_units=12,
        days_8_to_15_units=6,
        days_16_to_30_units=0,
        active_sales_days_30=6,
        max_daily_units_30=3,
        last_7_sellable_days=4,
        days_8_to_15_sellable_days=3,
        days_16_to_30_sellable_days=15,
    )

    assert decision["dailyVelocity"] == 2.4
    assert decision["denominatorBasis"] == "verified_sellable_days"


@pytest.mark.parametrize(
    "override",
    [
        {"last_7_units": True},
        {"last_7_units": -1},
        {"active_sales_days_30": 31},
        {"max_daily_units_30": 6},
    ],
)
def test_invalid_trend_inputs_fail_closed(override):
    values = {
        "last_7_units": 5,
        "days_8_to_15_units": 0,
        "days_16_to_30_units": 0,
        "active_sales_days_30": 3,
        "max_daily_units_30": 2,
    }
    values.update(override)

    with pytest.raises(ValueError):
        calculate_segmented_trend(**values)
