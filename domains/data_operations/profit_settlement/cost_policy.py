"""Explicit temporary cost policy for operator-reviewed profit runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


POLICY_VERSION = "temporary-cost-policy/default-5-conflict-high/v1"


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


def resolve_temporary_cost_policy(
    catalog: object,
    required_skus: Iterable[str],
    *,
    default_unit_cost_cny: Decimal | str = Decimal("5"),
) -> ResolvedCostPolicy:
    """Use catalog costs, highest conflict candidate, or an explicit CNY 5 default."""
    default_cost = Decimal(str(default_unit_cost_cny))
    if default_cost <= 0:
        raise ValueError("default_unit_cost_cny must be positive")
    existing = dict(getattr(catalog, "costs_by_sku", {}) or {})
    candidates = dict(getattr(catalog, "cost_candidates_by_sku", {}) or {})
    effective_at = str(getattr(catalog, "effective_at", "") or "")
    snapshot_id = str(getattr(catalog, "snapshot_id", "") or "")
    required = {str(value).strip() for value in required_skus if str(value).strip()}
    values: dict[str, Mapping[str, str]] = {}
    warnings: list[CostAssumptionWarning] = []
    for sku in sorted(set(existing) | required):
        choices = tuple(sorted(Decimal(str(value)) for value in candidates.get(sku, ()) if Decimal(str(value)) > 0))
        if len(choices) > 1:
            selected = max(choices)
            code = "conflicting_cost_high_selected"
            source = "operator-policy:highest-positive-catalog-cost"
            message = (
                f"SKU {sku} has conflicting catalog costs; temporary policy selected "
                f"the highest value CNY {_display_money(selected)}"
            )
            warnings.append(CostAssumptionWarning(code, sku, selected, choices, POLICY_VERSION, message))
        elif sku in existing and Decimal(str(existing[sku])) > 0:
            selected = Decimal(str(existing[sku]))
            source = "shop.db:sku_costs:sqlite-mode-ro"
        elif sku in required:
            selected = default_cost
            code = "missing_cost_default_5_selected"
            source = "operator-policy:missing-cost-default"
            message = (
                f"SKU {sku} has no positive catalog cost; temporary policy selected "
                f"CNY {_display_money(selected)}"
            )
            warnings.append(CostAssumptionWarning(code, sku, selected, (), POLICY_VERSION, message))
        else:
            continue
        values[sku] = {
            "unit_cost_cny": str(selected),
            "version": POLICY_VERSION if source.startswith("operator-policy:") else snapshot_id,
            "effective_at": effective_at,
            "source": source,
        }
    return ResolvedCostPolicy(values, tuple(warnings))


def _display_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"
