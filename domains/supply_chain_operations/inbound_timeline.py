"""Pure time-phased projection for inbound inventory decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil, floor, isfinite


@dataclass(frozen=True)
class InboundEvent:
    quantity: int
    estimated_sellable_date: date


@dataclass(frozen=True)
class SupplyProjection:
    projected_stock: int
    counted_inbound: int
    pending_inbound: int
    horizon_days: int


def _consume(stock: int, daily_velocity: float, days: int) -> int:
    if days <= 0:
        return stock
    return max(0, stock - ceil(daily_velocity * days))


def project_supply(
    *,
    snapshot_date: date,
    next_arrival_date: date,
    available: int,
    daily_velocity: float,
    inbound_events: tuple[InboundEvent, ...] = (),
) -> SupplyProjection:
    if type(available) is not int or available < 0:
        raise TypeError("available must be a nonnegative built-in int")
    if type(daily_velocity) not in (int, float) or isinstance(daily_velocity, bool):
        raise TypeError("daily_velocity must be a nonnegative finite number")
    if not isfinite(daily_velocity) or daily_velocity < 0:
        raise ValueError("daily_velocity must be a nonnegative finite number")
    if next_arrival_date < snapshot_date:
        raise ValueError("next_arrival_date cannot precede snapshot_date")

    horizon_days = (next_arrival_date - snapshot_date).days
    normalized: list[tuple[int, InboundEvent]] = []
    for event in inbound_events:
        if type(event.quantity) is not int or event.quantity < 0:
            raise TypeError("inbound quantity must be a nonnegative built-in int")
        day = max(0, (event.estimated_sellable_date - snapshot_date).days)
        normalized.append((day, event))
    normalized.sort(key=lambda value: value[0])

    stock = available
    last_day = 0
    counted_inbound = 0
    pending_inbound = 0
    for event_day, event in normalized:
        if event_day > horizon_days:
            pending_inbound += event.quantity
            continue
        stock = _consume(stock, float(daily_velocity), event_day - last_day)
        stock += event.quantity
        counted_inbound += event.quantity
        last_day = event_day
    stock = _consume(stock, float(daily_velocity), horizon_days - last_day)
    return SupplyProjection(
        projected_stock=max(0, floor(stock)),
        counted_inbound=counted_inbound,
        pending_inbound=pending_inbound,
        horizon_days=horizon_days,
    )
