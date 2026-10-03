"""Explicit temporary cost policy for operator-reviewed profit runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any


POLICY_VERSION = "temporary-cost-policy/default-5-conflict-high/v2"


@dataclass(frozen=True)
class CostAssumptionWarning:
    code: str
    canonical_sku: str
    selected_unit_cost_cny: Decimal
    candidate_costs_cny: tuple[Decimal, ...]
    policy_version: str
    message: str

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "canonical_sku": self.canonical_sku,
            "selected_unit_cost_cny": str(self.selected_unit_cost_cny),
            "candidate_costs_cny": [str(value) for value in self.candidate_costs_cny],
            "policy_version": self.policy_version,
            "message": self.message,
        }


@dataclass(frozen=True)
class ResolvedCostPolicy:
    values: Mapping[str, Mapping[str, str]]
    warnings: tuple[CostAssumptionWarning, ...]
    policy_version: str = POLICY_VERSION
    issues: tuple[Mapping[str, str], ...] = ()
    snapshot_id: str = ""


def resolve_temporary_cost_policy(
    catalog: object,
    required_skus: Iterable[str],
    *,
    default_unit_cost_cny: Decimal | str = Decimal("5"),
    allow_missing_default: bool = True,
    allow_conflict_high: bool = True,
    period_start: str | datetime | None = None,
    period_end: str | datetime | None = None,
) -> ResolvedCostPolicy:
    """Explicit assumptions only; dates use a declared [start, end) interval.

    Identity/currency ambiguity or unusable dated evidence cannot fall through
    to a default. Observation time and file mtime never establish applicability.
    """
    default_cost = Decimal(str(default_unit_cost_cny))
    if not default_cost.is_finite() or default_cost <= 0:
        raise ValueError("default_unit_cost_cny must be positive")
    start = _timestamp(period_start) if period_start is not None else None
    end = _timestamp(period_end) if period_end is not None else None
    if (start is None) != (end is None) or start is not None and end <= start:
        raise ValueError("period_start and period_end require an ordered timezone-aware interval")
    existing = dict(getattr(catalog, "costs_by_sku", {}) or {})
    candidates = dict(getattr(catalog, "cost_candidates_by_sku", {}) or {})
    effective_at = str(getattr(catalog, "effective_at", "") or "")
    snapshot_id = str(getattr(catalog, "snapshot_id", "") or "")
    required = {str(value).strip() for value in required_skus if str(value).strip()}
    values: dict[str, Mapping[str, str]] = {}
    warnings: list[CostAssumptionWarning] = []
    issues = []
    records = dict(getattr(catalog, "cost_records_by_sku", {}) or {})
    blocked = set(getattr(catalog, "blocked_skus", ()) or ())
    for sku in sorted(set(existing) | required):
        if sku in blocked:
            issues.append({"code": "ambiguous_catalog_identity", "canonical_sku": sku, "message": "No temporary global cost is permitted for ambiguous catalog identity or currency"})
            continue
        source_records = records.get(sku, ())
        if source_records:
            choices = tuple(sorted({_positive(c.get("amount")) for c in source_records
                                    if c.get("valid_value") and _positive(c.get("amount")) is not None
                                    and _covers_period(c, start, end)}))
            if not choices:
                issues.append({"code": "unusable_cost_for_period", "canonical_sku": sku, "message": "Recorded cost cannot be applied to the declared calculation interval; no default substituted"})
                continue
        else:
            choices = tuple(sorted({_positive(value) for value in candidates.get(sku, ()) if _positive(value) is not None}))
        if len(choices) > 1:
            if not allow_conflict_high:
                issues.append({"code": "conflicting_cost_requires_approval", "canonical_sku": sku, "message": "This run did not authorize selection among conflicting applicable costs"})
                continue
            selected = max(choices)
            code = "conflicting_cost_high_selected"
            source = "operator-policy:highest-positive-catalog-cost"
            message = (
                f"SKU {sku} has conflicting catalog costs; temporary policy selected "
                f"the highest value CNY {_display_money(selected)}"
            )
            warnings.append(CostAssumptionWarning(code, sku, selected, choices, POLICY_VERSION, message))
        elif choices or sku in existing and _positive(existing[sku]) is not None:
            selected = choices[0] if choices else _positive(existing[sku])
            source = "shop.db:sku_costs:sqlite-mode-ro"
        elif sku in required and allow_missing_default:
            selected = default_cost
            code = "missing_cost_default_5_selected"
            source = "operator-policy:missing-cost-default"
            message = (
                f"SKU {sku} has no positive catalog cost; temporary policy selected "
                f"CNY {_display_money(selected)}"
            )
            warnings.append(CostAssumptionWarning(code, sku, selected, (), POLICY_VERSION, message))
        elif sku in required:
            issues.append({"code": "missing_cost_requires_approval", "canonical_sku": sku, "message": "This run did not authorize a temporary missing-cost default"})
            continue
        else:
            continue
        values[sku] = {
            "unit_cost_cny": str(selected),
            "version": POLICY_VERSION if source.startswith("operator-policy:") else snapshot_id,
            "effective_at": effective_at,
            "source": source,
        }
    lineage = {
        "policy_version": POLICY_VERSION, "default_unit_cost_cny": str(default_cost),
        "allow_missing_default": allow_missing_default,
        "allow_conflict_high": allow_conflict_high,
        "catalog_snapshot_id": snapshot_id, "required_skus": sorted(required),
        "period": [start.isoformat(), end.isoformat()] if start is not None else None,
        "candidate_costs": {sku: [str(value) for value in candidates.get(sku, ())] for sku in sorted(set(existing) | required)},
        "values": values, "warnings": [warning.payload() for warning in warnings], "issues": issues,
        "catalog_issues": [asdict(item) if hasattr(item, "__dataclass_fields__") else item for item in getattr(catalog, "issues", ())],
    }
    policy_snapshot = "cost-policy:sha256:" + sha256(json.dumps(lineage, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()).hexdigest()
    values = {sku: {**value, "version": policy_snapshot} for sku, value in values.items()}
    return ResolvedCostPolicy(values, tuple(warnings), POLICY_VERSION, tuple(issues), policy_snapshot)


def _positive(value):
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and amount > 0 else None
    except (InvalidOperation, ValueError):
        return None


def _timestamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone-aware calculation and cost timestamps required")
    return result


def _covers_period(candidate, start, end):
    low, high = candidate.get("valid_from"), candidate.get("valid_to")
    if low is None and high is None:
        return True
    if start is None or end is None:
        return False
    try:
        low = _timestamp(low) if low is not None else None
        high = _timestamp(high) if high is not None else None
    except (TypeError, ValueError):
        return False
    return (not (low is not None and high is not None and low >= high)
            and (low is None or low <= start) and (high is None or high >= end))


def _display_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"
