"""Pure, fail-closed binding between approved variants and Miaoshou SKU rows."""

from __future__ import annotations

from collections.abc import Mapping


class TikTokVariantBindingError(ValueError):
    """The draft does not expose one exact row for every approved variant."""


def _normalize_variant(value: object) -> str:
    return str(value or "").strip().strip(";")


def approved_variant_key_bindings(
    detail: Mapping[str, object],
    *,
    selected_sku_keys: object,
    model_skus: object,
) -> dict[str, object]:
    """Return ``approved variant_key -> raw Miaoshou skuMap key``.

    Exact readable keys are preferred.  Opaque keys are decoded through the
    draft's own ``skuPropertyList``.  Unique model SKU matching is the final
    exact fallback.  Position, title, image and fuzzy matching are forbidden.
    """

    sku_map = detail.get("skuMap")
    if not isinstance(sku_map, Mapping) or not sku_map or any(
        not isinstance(row, Mapping) for row in sku_map.values()
    ):
        raise TikTokVariantBindingError("Miaoshou SKU map is malformed")

    variants = selected_sku_keys
    if (
        not isinstance(variants, list)
        or not variants
        or any(type(value) is not str or not value for value in variants)
        or len(variants) != len(set(variants))
        or not isinstance(model_skus, Mapping)
        or set(model_skus) != set(variants)
    ):
        raise TikTokVariantBindingError(
            "approved variant identity does not match the draft"
        )

    raw_by_normalized: dict[str, object] = {}
    for raw_key in sku_map:
        normalized = _normalize_variant(raw_key)
        if not normalized or normalized in raw_by_normalized:
            raise TikTokVariantBindingError(
                "Miaoshou variant identity is ambiguous"
            )
        raw_by_normalized[normalized] = raw_key
    if set(raw_by_normalized) == set(variants):
        return {variant: raw_by_normalized[variant] for variant in variants}

    property_values: dict[str, tuple[int, str]] = {}
    raw_properties = detail.get("skuPropertyList")
    if isinstance(raw_properties, list) and all(
        isinstance(prop, Mapping) for prop in raw_properties
    ):
        for property_index, prop in enumerate(raw_properties):
            raw_values = prop.get("attrValueList")
            if not isinstance(raw_values, list) or any(
                not isinstance(value, Mapping) for value in raw_values
            ):
                property_values = {}
                break
            for value in raw_values:
                value_id = value.get("attrValueId")
                label = value.get("attrValue")
                if (
                    type(value_id) is not str
                    or not value_id.strip()
                    or type(label) is not str
                    or not label.strip()
                    or value_id.strip() in property_values
                ):
                    property_values = {}
                    break
                property_values[value_id.strip()] = (
                    property_index,
                    label.strip(),
                )
            if not property_values:
                break
    if property_values:
        raw_by_property_signature: dict[str, object] = {}
        for raw_key in sku_map:
            if type(raw_key) is not str:
                raw_by_property_signature = {}
                break
            value_ids = [value for value in raw_key.split(";") if value]
            if not value_ids or any(
                value_id not in property_values for value_id in value_ids
            ):
                raw_by_property_signature = {}
                break
            dimensions = [property_values[value_id][0] for value_id in value_ids]
            if len(dimensions) != len(set(dimensions)):
                raw_by_property_signature = {}
                break
            signature = _normalize_variant(
                ";".join(
                    property_values[value_id][1]
                    for value_id in sorted(
                        value_ids,
                        key=lambda item: property_values[item][0],
                    )
                )
            )
            if not signature or signature in raw_by_property_signature:
                raw_by_property_signature = {}
                break
            raw_by_property_signature[signature] = raw_key
        if set(raw_by_property_signature) == set(variants):
            return {
                variant: raw_by_property_signature[variant]
                for variant in variants
            }

    expected_by_model: dict[str, str] = {}
    for variant in variants:
        model_sku = model_skus.get(variant)
        if (
            type(model_sku) is not str
            or not model_sku
            or model_sku != model_sku.strip()
            or model_sku in expected_by_model
        ):
            raise TikTokVariantBindingError(
                "approved model SKU identity is ambiguous"
            )
        expected_by_model[model_sku] = variant

    observed_by_model: dict[str, object] = {}
    for raw_key, row in sku_map.items():
        model_sku = row.get("itemNum")
        if (
            type(model_sku) is not str
            or not model_sku
            or model_sku != model_sku.strip()
            or model_sku in observed_by_model
        ):
            raise TikTokVariantBindingError(
                "Miaoshou model SKU identity is ambiguous"
            )
        observed_by_model[model_sku] = raw_key
    if set(observed_by_model) != set(expected_by_model):
        raise TikTokVariantBindingError(
            "TikTok model SKU identity does not match approved plan"
        )
    return {
        variant: observed_by_model[model_sku]
        for model_sku, variant in expected_by_model.items()
    }
