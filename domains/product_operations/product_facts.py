"""Pure product-fact evidence and selected-SKU price review.

This module deliberately does not choose or rewrite commercial values.  It
shows which source won the legacy precedence rules and blocks approval when
the selected SKU facts do not support one unambiguous product cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


@dataclass(frozen=True)
class FieldSourceCandidate:
    """One observed candidate for a product field."""

    source: str
    value: Any

    def payload(self) -> dict[str, Any]:
        return {"source": self.source, "value": _json_value(self.value)}


@dataclass(frozen=True)
class FieldSourceEvidence:
    """The effective value and every available source considered for it."""

    field_name: str
    value: Any
    selected_source: str
    candidates: tuple[FieldSourceCandidate, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "value": _json_value(self.value),
            "selected_source": self.selected_source,
            "candidates": [candidate.payload() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class SelectedSkuPriceFact:
    """Price evidence for one selected source SKU."""

    selected_key: str
    source_key: str
    label: str
    price_cny: Decimal | None
    source: str = "source.skus"

    def payload(self) -> dict[str, Any]:
        return {
            "selected_key": self.selected_key,
            "source_key": self.source_key,
            "label": self.label,
            "price_cny": _decimal_text(self.price_cny),
            "source": self.source,
        }


@dataclass(frozen=True)
class ProductFactsSnapshot:
    """Immutable, JSON-ready evidence used before product approval."""

    product_id: str
    fields: tuple[FieldSourceEvidence, ...]
    selected_sku_prices: tuple[SelectedSkuPriceFact, ...]
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers

    def field(self, field_name: str) -> FieldSourceEvidence | None:
        return next(
            (evidence for evidence in self.fields if evidence.field_name == field_name),
            None,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "product_id": self.product_id,
            "ready": self.ready,
            "fields": {
                evidence.field_name: evidence.payload()
                for evidence in self.fields
            },
            "selected_sku_prices": [
                fact.payload() for fact in self.selected_sku_prices
            ],
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


_PLACEHOLDER_MARKERS = (
    "咨询客服",
    "联系客服",
    "客服咨询",
    "定制",
    "订制",
    "custom",
    "contact customer service",
    "contact service",
)


def build_product_facts_snapshot(
    *,
    product_id: str,
    source: Mapping[str, Any],
    review: Mapping[str, Any],
) -> ProductFactsSnapshot:
    """Review product facts without reading, writing, or correcting any value."""

    clean_product_id = str(product_id or "").strip()
    if not clean_product_id:
        raise ValueError("product_id is required")

    fields = (
        _field_evidence(
            "title",
            _candidates(
                ("review.title", review.get("title")),
                ("source.title_recommended", source.get("title_recommended")),
                ("source.title_source", source.get("title_source")),
            ),
        ),
        _field_evidence(
            "seller_sku",
            _candidates(
                ("review.seller_sku", review.get("seller_sku")),
                ("source.seller_sku", source.get("seller_sku")),
            ),
        ),
        _field_evidence(
            "cost_cny",
            _candidates(
                ("review.cost_cny", review.get("cost_cny")),
                ("source.cost_cny", source.get("cost_cny")),
            ),
        ),
        _field_evidence(
            "weight_kg",
            _candidates(
                ("review.weight_kg", review.get("weight_kg")),
                ("source.weight_kg", source.get("weight_kg")),
            ),
        ),
        _field_evidence(
            "package_cm",
            _candidates(
                ("review.package_cm", review.get("package_cm")),
                ("source.package_cm", source.get("package_cm")),
            ),
        ),
        _field_evidence(
            "category",
            _candidates(
                ("review.category", review.get("category")),
                ("source.category", source.get("category")),
            ),
        ),
        _field_evidence(
            "video_action",
            _candidates(
                ("review.video_action", review.get("video_action")),
                (
                    "source.video.action",
                    (source.get("video") or {}).get("action")
                    if isinstance(source.get("video"), Mapping)
                    else None,
                ),
            ),
        ),
        _field_evidence(
            "video_url",
            _candidates(
                ("review.video_url", review.get("video_url")),
                (
                    "source.video.url",
                    (source.get("video") or {}).get("url")
                    if isinstance(source.get("video"), Mapping)
                    else None,
                ),
            ),
        ),
    )

    source_skus = [
        row for row in (source.get("skus") or []) if isinstance(row, Mapping)
    ]
    selected_values = review.get("selected_sku_keys")
    if isinstance(selected_values, (list, tuple)):
        selected_keys = [
            str(value or "").strip()
            for value in selected_values
            if str(value or "").strip()
        ]
    else:
        selected_keys = [
            str(row.get("key") or row.get("name") or "").strip()
            for row in source_skus
            if str(row.get("key") or row.get("name") or "").strip()
        ]

    sku_facts: list[SelectedSkuPriceFact] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for selected_key in dict.fromkeys(selected_keys):
        matching = [
            row
            for row in source_skus
            if str(row.get("key") or "").strip() == selected_key
        ]
        if not matching:
            matching = [
                row
                for row in source_skus
                if str(row.get("name") or "").strip() == selected_key
            ]
        if not matching:
            if not source_skus:
                continue
            blockers.append(
                f"selected SKU {selected_key!r} is not present in source.skus"
            )
            continue
        for row in matching:
            source_key = str(row.get("key") or row.get("name") or "").strip()
            label = str(row.get("name") or source_key).strip()
            price = _decimal(row.get("price"))
            fact = SelectedSkuPriceFact(
                selected_key=selected_key,
                source_key=source_key,
                label=label,
                price_cny=price,
            )
            if fact not in sku_facts:
                sku_facts.append(fact)
            normalized_label = f"{source_key} {label}".casefold()
            if any(marker.casefold() in normalized_label for marker in _PLACEHOLDER_MARKERS):
                blockers.append(
                    f"selected SKU {selected_key!r} is a customer-service/custom placeholder and cannot establish product cost"
                )
            if price is None or price <= 0:
                blockers.append(
                    f"selected SKU {selected_key!r} has no valid positive source price"
                )

    valid_prices = sorted(
        {
            fact.price_cny
            for fact in sku_facts
            if fact.price_cny is not None and fact.price_cny > 0
        }
    )
    if len(valid_prices) > 1:
        blockers.append(
            "selected SKU prices conflict: "
            + ", ".join(f"{_decimal_text(price)} CNY" for price in valid_prices)
        )
    elif len(valid_prices) == 1:
        cost_evidence = next(
            evidence for evidence in fields if evidence.field_name == "cost_cny"
        )
        effective_cost = _decimal(cost_evidence.value)
        if effective_cost is not None and effective_cost > 0 and effective_cost != valid_prices[0]:
            blockers.append(
                "cost_cny does not match the selected SKU price: "
                f"{_decimal_text(effective_cost)} CNY vs {_decimal_text(valid_prices[0])} CNY"
            )

    if source_skus and not selected_keys:
        warnings.append(
            "source SKUs exist but no selected_sku_keys were provided; no SKU price was approved"
        )
    elif selected_keys and not source_skus:
        warnings.append(
            "selected SKU source-price evidence is unavailable in this local snapshot"
        )

    return ProductFactsSnapshot(
        product_id=clean_product_id,
        fields=fields,
        selected_sku_prices=tuple(sku_facts),
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _candidates(*values: tuple[str, Any]) -> tuple[FieldSourceCandidate, ...]:
    return tuple(
        FieldSourceCandidate(source=source, value=_immutable_value(value))
        for source, value in values
        if _has_value(value)
    )


def _field_evidence(
    field_name: str,
    candidates: tuple[FieldSourceCandidate, ...],
) -> FieldSourceEvidence:
    if candidates:
        selected = candidates[0]
        return FieldSourceEvidence(
            field_name=field_name,
            value=selected.value,
            selected_source=selected.source,
            candidates=candidates,
        )
    return FieldSourceEvidence(
        field_name=field_name,
        value=None,
        selected_source="missing",
        candidates=(),
    )


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list, Mapping)):
        return bool(value)
    return True


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _immutable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _immutable_value(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_immutable_value(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        if all(
            isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str)
            for item in value
        ):
            return {item[0]: _json_value(item[1]) for item in value}
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return _decimal_text(value)
    return value
