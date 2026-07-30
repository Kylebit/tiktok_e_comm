"""Deterministic, side-effect-free replenishment calculations.

The model works on already-audited demand and inventory facts.  It never reads
credentials, calls a warehouse, writes a database, or treats channel listing
stock as physical warehouse inventory.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class DemandSignal:
    seller_sku: str
    country: str
    units_sold: int
    window_days: int
    captured_at: str

    def __post_init__(self) -> None:
        _nonempty(self.seller_sku, "seller_sku")
        _nonempty(self.country, "country")
        _non_negative_int(self.units_sold, "units_sold")
        _positive_int(self.window_days, "window_days")
        _nonempty(self.captured_at, "captured_at")

    @property
    def daily_velocity(self) -> Decimal:
        return Decimal(self.units_sold) / Decimal(self.window_days)


@dataclass(frozen=True)
class InventoryPosition:
    """Physical local inventory expected to be usable before the next arrival."""

    available: int | None
    high_confidence_inbound: int | None
    counted_not_shelved: int = 0
    low_confidence_inbound: int = 0

    def __post_init__(self) -> None:
        for field in ("available", "high_confidence_inbound"):
            value = getattr(self, field)
            if value is not None:
                _non_negative_int(value, field)
        _non_negative_int(self.counted_not_shelved, "counted_not_shelved")
        _non_negative_int(self.low_confidence_inbound, "low_confidence_inbound")

    @property
    def complete(self) -> bool:
        return self.available is not None and self.high_confidence_inbound is not None

    @property
    def trusted_units(self) -> int:
        return (self.available or 0) + (self.high_confidence_inbound or 0)


@dataclass(frozen=True)
class ReplenishmentPolicy:
    lead_days: int
    target_cover_days: int = 30
    safety_days: int | None = None

    def __post_init__(self) -> None:
        _positive_int(self.lead_days, "lead_days")
        _positive_int(self.target_cover_days, "target_cover_days")
        if self.safety_days is not None:
            _non_negative_int(self.safety_days, "safety_days")

    @property
    def resolved_safety_days(self) -> int:
        return self.safety_days if self.safety_days is not None else _ceil(
            Decimal(self.lead_days) * Decimal("0.20")
        )


@dataclass(frozen=True)
class ReplenishmentRecommendation:
    seller_sku: str
    country: str
    recommended_quantity: int
    target_arrival_stock: int
    projected_stock_at_arrival: int
    lead_demand: int
    daily_velocity: Decimal
    status: str
    reason: str


def recommend_replenishment(
    demand: DemandSignal,
    inventory: InventoryPosition,
    policy: ReplenishmentPolicy,
) -> ReplenishmentRecommendation:
    """Return an arrival-cover recommendation without mutating any source.

    Low-confidence inbound and counted-but-not-shelved units are deliberately
    excluded.  When physical inventory is incomplete, the result is an upper
    bound and must be reduced after the missing facts are captured.
    """

    velocity = demand.daily_velocity
    lead_demand = _ceil(velocity * Decimal(policy.lead_days))
    target = _ceil(
        velocity
        * Decimal(policy.target_cover_days + policy.resolved_safety_days)
    )
    projected = max(0, inventory.trusted_units - lead_demand)
    quantity = max(0, target - projected)
    if not inventory.complete:
        status = "PROVISIONAL_UPPER_BOUND"
        reason = "physical_inventory_incomplete"
    elif quantity == 0:
        status = "HOLD"
        reason = "trusted_stock_covers_target"
    else:
        status = "RECOMMEND"
        reason = "arrival_stock_below_target"
    return ReplenishmentRecommendation(
        seller_sku=demand.seller_sku,
        country=demand.country,
        recommended_quantity=quantity,
        target_arrival_stock=target,
        projected_stock_at_arrival=projected,
        lead_demand=lead_demand,
        daily_velocity=velocity,
        status=status,
        reason=reason,
    )
