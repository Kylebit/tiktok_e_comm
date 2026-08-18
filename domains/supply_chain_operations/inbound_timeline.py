"""Pure time-phased projection for inbound inventory decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil, floor, isfinite


@dataclass(frozen=True)
class InboundEvent:
    batch_id: str
    quantity: int
    estimated_sellable_date: date


@dataclass(frozen=True)
class SupplyProjection:
    projected_stock: int
    counted_inbound: int
    pending_inbound: int
    horizon_days: int
    steps: tuple["SupplyStep", ...]
    projection_method: str = "TIME_PHASED_BATCH_EVENTS_V1"


@dataclass(frozen=True)
class SupplyStep:
    kind: str
    from_date: date | None = None
    to_date: date | None = None
    days: int = 0
    stock_before: int = 0
    demand: int = 0
    stock_after: int = 0
    unmet_demand: int = 0
    batch_id: str | None = None
    quantity: int = 0


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
        if type(event.batch_id) is not str or not event.batch_id.strip():
            raise TypeError("inbound batch_id must be a nonempty built-in string")
        if type(event.quantity) is not int or event.quantity < 0:
            raise TypeError("inbound quantity must be a nonnegative built-in int")
        day = max(0, (event.estimated_sellable_date - snapshot_date).days)
        normalized.append((day, event))
    normalized.sort(key=lambda value: value[0])

    stock = available
    last_day = 0
    counted_inbound = 0
    pending_inbound = 0
    steps: list[SupplyStep] = []
    for event_day, event in normalized:
        if event_day > horizon_days:
            pending_inbound += event.quantity
            continue
        if event_day > last_day:
            days = event_day - last_day
            demand = ceil(float(daily_velocity) * days)
            stock_after = _consume(stock, float(daily_velocity), days)
            steps.append(
                SupplyStep(
                    kind="CONSUMPTION",
                    from_date=snapshot_date + timedelta(days=last_day),
                    to_date=snapshot_date + timedelta(days=event_day),
                    days=days,
                    stock_before=stock,
                    demand=demand,
                    stock_after=stock_after,
                    unmet_demand=max(0, demand - stock),
                )
            )
            stock = stock_after
        stock_before = stock
        stock += event.quantity
        counted_inbound += event.quantity
        steps.append(
            SupplyStep(
                kind="INBOUND",
                from_date=event.estimated_sellable_date,
                to_date=event.estimated_sellable_date,
                stock_before=stock_before,
                stock_after=stock,
                batch_id=event.batch_id,
                quantity=event.quantity,
            )
        )
        last_day = event_day
    if horizon_days > last_day:
        days = horizon_days - last_day
        demand = ceil(float(daily_velocity) * days)
        stock_after = _consume(stock, float(daily_velocity), days)
        steps.append(
            SupplyStep(
                kind="CONSUMPTION",
                from_date=snapshot_date + timedelta(days=last_day),
                to_date=next_arrival_date,
                days=days,
                stock_before=stock,
                demand=demand,
                stock_after=stock_after,
                unmet_demand=max(0, demand - stock),
            )
        )
        stock = stock_after
    return SupplyProjection(
        projected_stock=max(0, floor(stock)),
        counted_inbound=counted_inbound,
        pending_inbound=pending_inbound,
        horizon_days=horizon_days,
        steps=tuple(steps),
    )
