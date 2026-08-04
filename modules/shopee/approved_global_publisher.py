"""Independent Shopee CNSC global-product publisher.

This boundary consumes the already-approved product snapshot only.  In
particular it must not load a TikTok listing, a TikTok product ID, or a
TikTok/Shopee alignment record: the CNSC global product is a Shopee resource.
"""

from __future__ import annotations

from collections.abc import Mapping


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
                "seller_sku": seller_sku,
                "inventory": [{"quantity": quantity}],
                "sku_weight": {"value": weight_kg, "unit": "KILOGRAM"},
                "sku_dimensions": {
                    "length": dimensions[0],
                    "width": dimensions[1],
                    "height": dimensions[2],
                },
            }
        ],
    }


def publish_approved_global(facts: Mapping[str, object]) -> dict[str, object]:
    """Create one CNSC global product through Shopee's official API.

    The PH merchant is only a Shopee merchant context for CNSC; it is not a
    TikTok source and no regional Shopee listing is published here.
    """

    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.publish import (
        _create_global_item,
        _reference_item,
        _upload_images,
    )
    from modules.shopee.global_sku_map import (
        global_item_id_for_match_key,
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
    if existing_global_item_id:
        # A previous click already obtained a Shopee global identity.  The
        # product rule for this button is *create the global product*, not a
        # secondary synchronization system.  Do not turn a safe repeat into
        # a write-plus-brittle-readback failure merely because Shopee
        # normalizes displayed copy.
        return {
            "ok": True,
            "flow": "already_created",
            "global_item_id": int(existing_global_item_id),
            "model_sku": _text(facts.get("seller_sku"), "seller SKU"),
        }
    reference = _reference_item(region, shop_id, token)
    image_ids = _upload_images(list(detail["main_images"]))
    if not image_ids:
        raise RuntimeError("Shopee did not accept any approved image")
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
    upsert_global_entry(
        str(global_item_id),
        match_key=_text(facts.get("seller_sku"), "seller SKU"),
        global_model_sku=_text(facts.get("seller_sku"), "seller SKU"),
        title=_text(facts.get("title"), "title"),
        published_regions=[],
    )
    return result
