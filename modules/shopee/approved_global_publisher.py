"""Independent Shopee CNSC global-product publisher.

This boundary consumes the already-approved product snapshot only.  In
particular it must not load a TikTok listing, a TikTok product ID, or a
TikTok/Shopee alignment record: the CNSC global product is a Shopee resource.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"approved Shopee {name} is unavailable")
    return value.strip()


def _positive_number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"approved Shopee {name} is invalid")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"approved Shopee {name} is invalid") from None
    if number <= 0:
        raise ValueError(f"approved Shopee {name} is invalid")
    return number


def approved_global_detail(facts: Mapping[str, object]) -> dict[str, object]:
    """Make the smallest legacy-compatible detail from immutable facts.

    The legacy *transport* helpers are reused because they know the official
    Shopee payload format.  Their legacy TikTok lookup entrypoint is not used.
    """

    seller_sku = _text(facts.get("seller_sku"), "seller SKU")
    title = _text(facts.get("title"), "title")
    description = _text(facts.get("description"), "description")
    images = facts.get("images")
    package_cm = facts.get("package_cm")
    weight_kg = _positive_number(facts.get("weight_kg"), "weight")
    quantity = facts.get("quantity", 1)
    if (
        not isinstance(images, list)
        or not images
        or any(type(url) is not str or not url.startswith("https://") for url in images)
        or not isinstance(package_cm, list)
        or len(package_cm) != 3
    ):
        raise ValueError("approved Shopee images or parcel facts are invalid")
    dimensions = [_positive_number(value, "package dimension") for value in package_cm]
    if isinstance(quantity, bool) or type(quantity) is not int or quantity <= 0:
        raise ValueError("approved Shopee quantity is invalid")
    raw_variants = facts.get("variants")
    if raw_variants is None:
        raw_variants = [{
            "model_sku": seller_sku,
            "option_label": seller_sku,
        }]
    if not isinstance(raw_variants, list) or not raw_variants:
        raise ValueError("approved Shopee variants are invalid")
    raw_sku_commercial_facts = facts.get("sku_commercial_facts")
    raw_sku_prices = facts.get("sku_prices")
    if raw_sku_commercial_facts is not None and not isinstance(
        raw_sku_commercial_facts, Mapping
    ):
        raise ValueError("approved Shopee per-SKU parcel facts are invalid")
    if raw_sku_prices is not None and not isinstance(raw_sku_prices, Mapping):
        raise ValueError("approved Shopee per-SKU prices are invalid")
    raw_default_price = facts.get("global_original_price_cny")
    default_price = (
        _positive_number(raw_default_price, "global price")
        if raw_default_price is not None
        else None
    )
    variants: list[dict[str, object]] = []
    single_variant = len(raw_variants) == 1
    for row in raw_variants:
        if not isinstance(row, Mapping):
            raise ValueError("approved Shopee variants are invalid")
        model_sku = _text(row.get("model_sku"), "model SKU")
        option_label = _text(row.get("option_label"), "variation option")
        variant_key = str(row.get("variant_key") or model_sku).strip()
        commercial_row = (
            raw_sku_commercial_facts.get(variant_key)
            if isinstance(raw_sku_commercial_facts, Mapping)
            else None
        )
        if commercial_row is None:
            sku_weight = weight_kg
            sku_dimensions = dimensions
        else:
            if not isinstance(commercial_row, Mapping):
                raise ValueError("approved Shopee per-SKU parcel facts are invalid")
            sku_weight = _positive_number(
                commercial_row.get("weight_kg"), "SKU weight"
            )
            raw_dimensions = commercial_row.get("package_cm")
            if not isinstance(raw_dimensions, list) or len(raw_dimensions) != 3:
                raise ValueError("approved Shopee per-SKU parcel facts are invalid")
            sku_dimensions = [
                _positive_number(value, "SKU package dimension")
                for value in raw_dimensions
            ]
        sku_price = (
            _positive_number(raw_sku_prices.get(variant_key), "SKU price")
            if isinstance(raw_sku_prices, Mapping)
            else default_price
        )
        variants.append({
            "variant_key": variant_key,
            "model_sku": model_sku,
            "option_label": option_label,
            "weight_kg": sku_weight,
            "dimensions": sku_dimensions,
            "original_price": sku_price,
            "variation_image_position": (
                0 if single_variant else row.get("variation_image_position")
            ),
        })
    if len({str(row["model_sku"]) for row in variants}) != len(variants):
        raise ValueError("approved Shopee model SKUs are not unique")
    # Shopee CNSC exposes parcel fields at global-item level, not per model.
    # Use the conservative envelope of all approved variants so the master is
    # valid for every SKU instead of silently inheriting the first SKU.
    weight_kg = max(float(row["weight_kg"]) for row in variants)
    dimensions = [
        max(float(row["dimensions"][index]) for row in variants)
        for index in range(3)
    ]
    return {
        "title": title,
        "description": description,
        "main_images": list(images),
        "package_weight": {"value": weight_kg, "unit": "KILOGRAM"},
        "package_dimensions": {
            "length": dimensions[0],
            "width": dimensions[1],
            "height": dimensions[2],
        },
        "skus": [
            {
                "seller_sku": row["model_sku"],
                "variation_option": row["option_label"],
                **(
                    {"variation_image_position": row["variation_image_position"]}
                    if type(row.get("variation_image_position")) is int
                    and 0 <= int(row["variation_image_position"]) < len(images)
                    else {}
                ),
                **(
                    {"original_price": row["original_price"]}
                    if row["original_price"] is not None
                    else {}
                ),
                "inventory": [{"quantity": quantity}],
                "sku_weight": {
                    "value": row["weight_kg"],
                    "unit": "KILOGRAM",
                },
                "sku_dimensions": {
                    "length": row["dimensions"][0],
                    "width": row["dimensions"][1],
                    "height": row["dimensions"][2],
                },
            }
            for row in variants
        ],
    }


def _bind_variant_image_ids(
    detail: Mapping[str, object], image_ids: list[str]
) -> dict[str, object]:
    """Bind approved variant positions to uploaded Shopee image identities."""

    bound = deepcopy(dict(detail))
    skus = bound.get("skus")
    if not isinstance(skus, list):
        return bound
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        position = sku.get("variation_image_position")
        if type(position) is int and 0 <= position < len(image_ids):
            image_id = str(image_ids[position] or "").strip()
            if image_id:
                sku["variation_image_id"] = image_id
    return bound


def publish_approved_global(facts: Mapping[str, object]) -> dict[str, object]:
    """Create one CNSC global product through Shopee's official API.

    The PH merchant is only a Shopee merchant context for CNSC; it is not a
    TikTok source and no regional Shopee listing is published here.
    """

    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.publish import (
        _create_global_item,
        _merchant_token,
        _reference_item,
        _shop_meta,
        _upload_images,
        ensure_global_master,
        ensure_global_models,
    )
    from modules.shopee.global_sku_map import (
        global_item_id_for_match_key,
        replace_inexact_global_entry,
        upsert_global_entry,
    )
    from modules.shopee.shops import sync_shop_ids

    detail = approved_global_detail(facts)
    region = _text(facts.get("region"), "merchant region").upper()
    if region not in {"PH", "MY", "TH", "VN"}:
        raise ValueError("approved Shopee merchant region is unsupported")
    shop_ids = sync_shop_ids()
    shop_id = shop_ids.get(region)
    if isinstance(shop_id, bool) or not isinstance(shop_id, int) or shop_id <= 0:
        raise RuntimeError(f"Shopee {region} merchant context is unavailable")
    token = ensure_shop_token(shop_id)
    existing_global_item_id = global_item_id_for_match_key(
        _text(facts.get("seller_sku"), "seller SKU")
    )
    replaced_inexact_global_item_id: int | None = None
    if existing_global_item_id:
        merchant_id = int(_shop_meta(shop_id, token).get("merchant_id") or 0)
        if merchant_id <= 0:
            raise RuntimeError("Shopee merchant context is unavailable")
        merchant_token = _merchant_token(shop_id, token)
        ref = _reference_item(region, shop_id, token)
        first_sku = detail["skus"][0]
        stock = sum(
            int(row.get("quantity") or 0)
            for row in first_sku.get("inventory") or []
        ) or 1
        try:
            # Reject a stale/inexact multi-model identity before touching its
            # master copy.  A second call after the master read binds and
            # verifies per-variant image identities.
            if len(detail["skus"]) > 1:
                ensure_global_models(
                    global_item_id=int(existing_global_item_id),
                    merchant_id=merchant_id,
                    merchant_token=merchant_token,
                    detail=detail,
                    original_price=_positive_number(
                        facts.get("global_original_price_cny"), "global price"
                    ),
                    stock=stock,
                    create_when_missing=False,
                )
            master = ensure_global_master(
                global_item_id=int(existing_global_item_id),
                merchant_id=merchant_id,
                merchant_token=merchant_token,
                detail=detail,
                title=_text(facts.get("title"), "title"),
                description=_text(facts.get("description"), "description"),
                ref=ref,
                # Once tier variation exists, CNSC exposes price on the Model
                # row and may omit master `original_price`, even for one SKU.
                # The exact price is verified by ensure_global_models below.
                original_price=None,
            )
            detail_with_images = _bind_variant_image_ids(
                detail,
                [
                    str(image_id)
                    for image_id in (master.get("image_ids") or [])
                    if str(image_id or "").strip()
                ],
            )
            model_result = ensure_global_models(
                global_item_id=int(existing_global_item_id),
                merchant_id=merchant_id,
                merchant_token=merchant_token,
                detail=detail_with_images,
                original_price=_positive_number(
                    facts.get("global_original_price_cny"), "global price"
                ),
                stock=stock,
                create_when_missing=False,
            )
        except RuntimeError:
            if len(detail["skus"]) > 1:
                replaced_inexact_global_item_id = int(existing_global_item_id)
            else:
                raise
        else:
            return {
                "ok": True,
                "flow": "already_created",
                "global_item_id": int(existing_global_item_id),
                "model_sku": _text(facts.get("seller_sku"), "seller SKU"),
                "model_count": len(detail["skus"]),
                "copy_converged": master.get("verified") is True,
                "copy_updated": master.get("updated") is True,
                "variant_images_verified": (
                    model_result.get("variant_images_verified") is True
                ),
            }
    reference = _reference_item(region, shop_id, token)
    image_ids = _upload_images(list(detail["main_images"]))
    if not image_ids:
        raise RuntimeError("Shopee did not accept any approved image")
    detail = _bind_variant_image_ids(detail, image_ids)
    result = _create_global_item(
        detail,
        region=region,
        shop_id=shop_id,
        token=token,
        model_sku=_text(facts.get("seller_sku"), "seller SKU"),
        image_ids=image_ids,
        ref=reference,
        title_override=_text(facts.get("title"), "title"),
        description_override=_text(facts.get("description"), "description"),
        global_original_price_cny_override=_positive_number(
            facts.get("global_original_price_cny"), "global price"
        ),
    )
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Shopee official API did not accept the global product")
    global_item_id = result.get("global_item_id")
    if isinstance(global_item_id, bool) or not str(global_item_id or "").isdigit():
        raise RuntimeError("Shopee did not return a global product identity")
    mapping_kwargs = {
        "match_key": _text(facts.get("seller_sku"), "seller SKU"),
        "global_model_sku": _text(facts.get("seller_sku"), "seller SKU"),
        "title": _text(facts.get("title"), "title"),
    }
    if replaced_inexact_global_item_id is not None:
        replace_inexact_global_entry(
            str(replaced_inexact_global_item_id),
            str(global_item_id),
            **mapping_kwargs,
        )
    else:
        upsert_global_entry(
            str(global_item_id),
            **mapping_kwargs,
            published_regions=[],
        )
    return {
        **result,
        **(
            {"replaced_inexact_global_item_id": replaced_inexact_global_item_id}
            if replaced_inexact_global_item_id is not None
            else {}
        ),
    }
