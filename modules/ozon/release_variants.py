"""Immutable Ozon import facts for every approved selling SKU."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _positive(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"approved Ozon {field} is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"approved Ozon {field} is invalid") from error
    if not number.is_finite() or number <= 0:
        raise ValueError(f"approved Ozon {field} is invalid")
    return float(number)


def approved_variant_snapshots(payload: dict) -> list[dict]:
    product = payload.get("product_facts")
    pricing = payload.get("pricing")
    lineage = payload.get("sku_lineage")
    if not isinstance(product, dict) or not isinstance(pricing, dict):
        raise ValueError("approved Ozon variant facts are incomplete")
    selected = product.get("selected_skus")
    keys = product.get("selected_sku_keys")
    assignment = lineage.get("assignment") if isinstance(lineage, dict) else None
    model_rows = assignment.get("model_skus") if isinstance(assignment, dict) else None
    if (
        not isinstance(selected, list) or not selected
        or not isinstance(keys, list) or len(keys) != len(selected)
        or not isinstance(model_rows, list)
    ):
        raise ValueError("approved Ozon variant facts are incomplete")
    models = {
        row.get("variant_key"): row.get("model_sku")
        for row in model_rows if isinstance(row, dict)
    }
    target = (pricing.get("selected_targets") or {}).get("ozon:RU")
    preview = target.get("derived_preview") if isinstance(target, dict) else None
    sku_prices = {
        row.get("variant_key"): row.get("derived_preview")
        for row in (target.get("sku_prices") or [])
        if isinstance(row, dict)
    } if isinstance(target, dict) else {}
    commercial = product.get("sku_commercial_facts")
    commercial = commercial if isinstance(commercial, dict) else {}
    listing = payload.get("listing_copy")
    listing = listing if isinstance(listing, dict) else {}
    titles = [
        row.get("title") for row in listing.get("candidates") or []
        if isinstance(row, dict)
        and str(row.get("channel") or "").lower() == "ozon"
        and str(row.get("site") or "").upper() == "RU"
        and row.get("policy_check") == "passed"
    ]
    images = [row for row in payload.get("images") or [] if isinstance(row, dict)]
    images.sort(key=lambda row: row.get("position"))
    category = product.get("category")
    if (
        len(titles) != 1 or not titles[0] or not images
        or not isinstance(category, dict)
        or not str(category.get("name") or "").strip()
    ):
        raise ValueError("approved Ozon shared facts are incomplete")
    rows = []
    for index, sku in enumerate(selected):
        if (
            not isinstance(sku, dict) or sku.get("key") != keys[index]
            or sku.get("key") not in models
        ):
            raise ValueError("approved Ozon SKU lineage drifted")
        key = sku["key"]
        per_sku = commercial.get(key)
        per_sku = per_sku if isinstance(per_sku, dict) else {}
        package = per_sku.get("package_cm", product.get("package_cm"))
        weight = per_sku.get("weight_kg", product.get("weight_kg"))
        variant_preview = sku_prices.get(key)
        variant_preview = variant_preview if isinstance(variant_preview, dict) else preview
        if (
            not isinstance(package, (list, tuple)) or len(package) != 3
            or not isinstance(variant_preview, dict)
        ):
            raise ValueError("approved Ozon per-SKU facts are incomplete")
        price = int(_positive(variant_preview.get("price_cny"), "price"))
        old_price = int(_positive(variant_preview.get("old_price_cny"), "old price"))
        if old_price <= price:
            raise ValueError("approved Ozon old price must exceed price")
        rows.append({
            "seller_sku": str(models[key]).strip(),
            "variant_key": key,
            "variant_label": str(sku.get("label") or "").strip(),
            "title": str(titles[0]).strip(),
            "package_cm": [_positive(value, "package dimension") for value in package],
            "weight_kg": _positive(weight, "weight"),
            "quantity": 1,
            "price_cny": price,
            "old_price_cny": old_price,
            "images": [row["image_url"] for row in images],
            "source_category": {
                "id": str(category.get("id") or "").strip(),
                "name": str(category["name"]).strip(),
            },
        })
    if len({row["seller_sku"] for row in rows}) != len(rows):
        raise ValueError("approved Ozon model SKUs are not unique")
    return rows
