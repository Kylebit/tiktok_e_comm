"""Auditable projection of country transport days from completed Seaya batches."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Iterable


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise TypeError(f"{name} must be a positive built-in int")
    return value


@dataclass(frozen=True)
class HistoricalTransportPolicy:
    baseline_transport_days: int
    eligible_samples: int
    p80_total_days: int | None
    derived_transport_days: int | None
    effective_transport_days: int
    state: str


def derive_transport_policy(
    observed_created_to_sign_days: Iterable[int],
    *,
    baseline_transport_days: int,
    preparation_days: int = 3,
    domestic_warehouse_days: int = 4,
    minimum_samples: int = 5,
) -> HistoricalTransportPolicy:
    """Use a conservative nearest-rank P80 without lowering the approved baseline."""

    baseline = _positive_int(baseline_transport_days, "baseline_transport_days")
    preparation = _positive_int(preparation_days, "preparation_days")
    domestic = _positive_int(domestic_warehouse_days, "domestic_warehouse_days")
    minimum = _positive_int(minimum_samples, "minimum_samples")
    samples = sorted(
        _positive_int(value, "observed_created_to_sign_days")
        for value in observed_created_to_sign_days
    )
    if not samples:
        return HistoricalTransportPolicy(
            baseline, 0, None, None, baseline, "FALLBACK_NO_SAMPLE"
        )

    p80_total = samples[ceil(0.8 * len(samples)) - 1]
    derived = max(1, p80_total - preparation - domestic)
    if len(samples) < minimum:
        return HistoricalTransportPolicy(
            baseline,
            len(samples),
            p80_total,
            derived,
            baseline,
            "FALLBACK_INSUFFICIENT_SAMPLE",
        )

    effective = max(baseline, derived)
    state = "HISTORICAL_P80_UPLIFT" if effective > baseline else "BASELINE_FLOOR"
    return HistoricalTransportPolicy(
        baseline, len(samples), p80_total, derived, effective, state
    )
