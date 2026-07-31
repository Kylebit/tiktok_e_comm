"""Pure recency-weighted demand trend policy for volatile marketplace sales."""

from __future__ import annotations

from typing import Any

_WEIGHTS = (0.60, 0.30, 0.10)
_CALENDAR_DAYS = (7, 8, 15)


def _nonnegative_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be a nonnegative built-in number")
    return float(value)


def _selling_days(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= default:
        raise ValueError(f"{name} must be a built-in int from 1 to {default}")
    return value


def calculate_segmented_trend(
    *,
    last_7_units: object,
    days_8_to_15_units: object,
    days_16_to_30_units: object,
    active_sales_days_30: object,
    max_daily_units_30: object,
    last_7_sellable_days: object = None,
    days_8_to_15_sellable_days: object = None,
    days_16_to_30_sellable_days: object = None,
) -> dict[str, Any]:
    """Return an auditable 60/30/10 velocity and volatility classification.

    Calendar days are used only when an upstream source has no stock-availability
    history.  Callers must not pretend calendar days are verified sellable days.
    """

    units = (
        _nonnegative_number(last_7_units, "last_7_units"),
        _nonnegative_number(days_8_to_15_units, "days_8_to_15_units"),
        _nonnegative_number(days_16_to_30_units, "days_16_to_30_units"),
    )
    denominators = (
        _selling_days(last_7_sellable_days, 7, "last_7_sellable_days"),
        _selling_days(days_8_to_15_sellable_days, 8, "days_8_to_15_sellable_days"),
        _selling_days(days_16_to_30_sellable_days, 15, "days_16_to_30_sellable_days"),
    )
    if (
        type(active_sales_days_30) is not int
        or isinstance(active_sales_days_30, bool)
        or not 0 <= active_sales_days_30 <= 30
    ):
        raise ValueError("active_sales_days_30 must be a built-in int from 0 to 30")
    max_daily = _nonnegative_number(max_daily_units_30, "max_daily_units_30")
    total = sum(units)
    if max_daily > total:
        raise ValueError("max_daily_units_30 cannot exceed the 30-day units")

    rates = tuple(value / days for value, days in zip(units, denominators))
    daily_velocity = sum(weight * rate for weight, rate in zip(_WEIGHTS, rates))
    recent_15_rate = (units[0] + units[1]) / (denominators[0] + denominators[1])
    older_15_rate = rates[2]
    spike = total >= 5 and (
        active_sales_days_30 <= 2 or max_daily >= total * 0.60
    )

    if spike:
        trend_class = "SPIKE"
    elif (
        units[0] + units[1] >= 5
        and recent_15_rate >= older_15_rate * 1.25
        and recent_15_rate - older_15_rate >= 0.10
    ):
        trend_class = "RISING"
    elif (
        total >= 5
        and older_15_rate >= recent_15_rate * 1.25
        and older_15_rate - recent_15_rate >= 0.10
    ):
        trend_class = "FALLING"
    else:
        trend_class = "STABLE"

    if total >= 10 and active_sales_days_30 >= 5:
        confidence = "HIGH"
    elif total >= 5 and active_sales_days_30 >= 3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "method": "segmented_7_8_15_v1",
        "weights": {"last7": 0.60, "days8To15": 0.30, "days16To30": 0.10},
        "units": {
            "last7": units[0],
            "days8To15": units[1],
            "days16To30": units[2],
        },
        "denominatorDays": {
            "last7": denominators[0],
            "days8To15": denominators[1],
            "days16To30": denominators[2],
        },
        "denominatorBasis": (
            "verified_sellable_days"
            if all(
                value is not None
                for value in (
                    last_7_sellable_days,
                    days_8_to_15_sellable_days,
                    days_16_to_30_sellable_days,
                )
            )
            else "calendar_days_no_stockout_adjustment"
        ),
        "dailyVelocity": round(daily_velocity, 6),
        "forecast30Units": round(daily_velocity * 30, 3),
        "trendClass": trend_class,
        "confidence": confidence,
        "activeSalesDays30": active_sales_days_30,
        "maxDailyUnits30": max_daily,
        "spikeProtectionTargetDays": 15 if spike else None,
    }
