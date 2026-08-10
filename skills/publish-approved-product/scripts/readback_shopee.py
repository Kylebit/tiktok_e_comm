#!/usr/bin/env python3
"""DEPRECATED COMPATIBILITY: direct Shopee readback for incident diagnosis."""
from __future__ import annotations

from typing import Any, Mapping

from _common import add_repo_to_path, expected_model_skus, safe_text
from _readback_cli import run


def readback(
    snapshot: Mapping[str, Any], dispatch: Mapping[str, Any], args: Any
) -> dict[str, Any]:
    add_repo_to_path(args.repo)
    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.client import merchant_get
    from modules.shopee.global_sku_map import global_item_id_for_match_key
    from modules.shopee.publish import _merchant_token, _shop_meta
    from modules.shopee.shops import sync_shop_ids

    seller_sku = str(snapshot.get("request", {}).get("seller_sku") or "").strip()
    global_item_id = _global_item_id(dispatch) or global_item_id_for_match_key(seller_sku)
    if not global_item_id:
        return _missing("official global_item_id was not returned or mapped")
    target_rows = snapshot.get("platforms", {}).get("shopee", {}).get("targets") or []
    regions = [str(row).split(":", 1)[1].upper() for row in target_rows if ":" in str(row)]
    shop_ids = sync_shop_ids()
    region = next((value for value in regions if int(shop_ids.get(value) or 0) > 0), "PH")
    shop_id = int(shop_ids.get(region) or 0)
    if shop_id <= 0:
        raise RuntimeError("Shopee merchant context is unavailable")
    shop_token = ensure_shop_token(shop_id)
    merchant_id = int(_shop_meta(shop_id, shop_token).get("merchant_id") or 0)
    if merchant_id <= 0:
        raise RuntimeError("Shopee merchant identity is unavailable")
    token = _merchant_token(shop_id, shop_token)
    item_response = merchant_get(
        "/api/v2/global_product/get_global_item_info",
        merchant_id,
        token,
        {"global_item_id_list": str(global_item_id)},
    )
    error = str(item_response.get("error") or "").strip()
    if error and error != "-":
        raise RuntimeError(safe_text(item_response.get("message") or error))
    items = (item_response.get("response") or {}).get("global_item_list") or []
    if len(items) != 1:
        return _missing("official global item does not exist")
    item = items[0]
    state = str(
        item.get("global_item_status")
        or item.get("item_status")
        or item.get("status")
        or ""
    ).upper()
    if state == "DELETED":
        return {
            "schema_version": "platform-readback-fact/v1",
            "platform": "shopee",
            "provider": "official_shopee_partner_api",
            "global_item_id": str(global_item_id),
            "exists": True,
            "verified": False,
            "complete": False,
            "status": "DELETED",
            "stale_local_mapping": True,
            "retry_safe": True,
            "message": "official Shopee global item is DELETED; retire mapping before recreation",
        }
    model_response = merchant_get(
        "/api/v2/global_product/get_global_model_list",
        merchant_id,
        token,
        {"global_item_id": int(global_item_id)},
    )
    model_error = str(model_response.get("error") or "").strip()
    if model_error and model_error != "-":
        raise RuntimeError(safe_text(model_response.get("message") or model_error))
    body = model_response.get("response") or {}
    models = body.get("global_model") or []
    expected_skus = set(expected_model_skus(snapshot))
    observed_skus = {
        str(row.get("global_model_sku") or row.get("model_sku") or row.get("seller_sku") or "").strip()
        for row in models
        if isinstance(row, dict)
    }
    observed_skus.discard("")
    expected_description = str(snapshot.get("content", {}).get("description") or "")
    expected_title = _platform_title(snapshot, "shopee")
    observed_description = str(item.get("description") or "")
    observed_title = str(item.get("global_item_name") or "")
    image_ids = _image_ids(item)
    expected_image_count = len(snapshot.get("content", {}).get("images") or [])
    expected_options = [
        str(row.get("option_name") or "").strip()
        for row in snapshot.get("skus") or []
        if isinstance(row, dict)
    ]
    observed_option_rows = _tier_option_rows(body)
    if not observed_option_rows:
        observed_option_rows = _tier_option_rows(item)
    observed_options = [
        str(row.get("option") or "").strip() for row in observed_option_rows
    ]
    variant_image_ids = [
        str((row.get("image") or {}).get("image_id") or "").strip()
        for row in observed_option_rows
    ]
    variant_images_present = (
        len(variant_image_ids) == len(expected_options)
        and all(variant_image_ids)
        and (
            len(variant_image_ids) != 1
            or not image_ids
            or variant_image_ids[0] == image_ids[0]
        )
    )
    expected_tier_indexes = {
        sku: [index]
        for index, sku in enumerate(expected_model_skus(snapshot))
    }
    observed_tier_indexes = {
        str(row.get("global_model_sku") or "").strip(): row.get("tier_index")
        for row in models
        if isinstance(row, dict) and str(row.get("global_model_sku") or "").strip()
    }
    expected_prices = _expected_shopee_prices(snapshot, regions)
    expected_parcel = _expected_shopee_parcel(snapshot)
    observed_prices = {
        str(row.get("global_model_sku") or "").strip(): _model_price(row)
        for row in models
        if isinstance(row, dict) and str(row.get("global_model_sku") or "").strip()
    }
    checks = {
        "sku_set_exact": observed_skus == expected_skus,
        "title_exact": observed_title == expected_title,
        "description_exact": observed_description == expected_description,
        "images_present": len(image_ids) >= min(expected_image_count, 1),
        "tier_indexes_exact": observed_tier_indexes == expected_tier_indexes,
        "option_names_exact": (
            observed_options == expected_options if observed_options else None
        ),
        "variant_images_present": variant_images_present,
        "model_prices_exact": _numeric_map_exact(observed_prices, expected_prices),
        "master_parcel_exact": _master_parcel_exact(item, expected_parcel),
    }
    complete = all(
        value is True
        for key, value in checks.items()
        if key != "option_names_exact" or value is not None
    )
    return {
        "schema_version": "platform-readback-fact/v1",
        "platform": "shopee",
        "provider": "official_shopee_partner_api",
        "global_item_id": str(global_item_id),
        "exists": True,
        "verified": complete,
        "complete": complete,
        "status": (state or "NORMAL") if complete else "MISMATCH",
        "expected_count": len(expected_skus),
        "verified_count": len(expected_skus & observed_skus),
        "mismatch": not complete,
        "checks": checks,
        "observed": {
            "model_skus": sorted(observed_skus),
            "title_length": len(observed_title),
            "description_length": len(observed_description),
            "image_count": len(image_ids),
            "option_count": len(observed_options),
            "variant_image_count": sum(bool(value) for value in variant_image_ids),
            "option_names_check": (
                "EXACT" if observed_options else "PROVIDER_FIELD_OMITTED"
            ),
            "weight": item.get("weight"),
            "dimension": item.get("dimension"),
        },
        "retry_safe": False,
    }


def _platform_title(snapshot: Mapping[str, Any], channel: str) -> str:
    rows = snapshot.get("content", {}).get("platform_titles") or []
    matches = [
        str(row.get("title") or "").strip()
        for row in rows
        if isinstance(row, dict)
        and str(row.get("channel") or "").lower() == channel
        and str(row.get("title") or "").strip()
    ]
    return matches[0] if len(matches) == 1 else ""


def _tier_option_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tiers = payload.get("tier_variation") or []
    if not isinstance(tiers, list) or len(tiers) != 1 or not isinstance(tiers[0], dict):
        return []
    return [
        row
        for row in tiers[0].get("option_list") or []
        if isinstance(row, dict)
    ]


def _expected_shopee_prices(
    snapshot: Mapping[str, Any], regions: list[str]
) -> dict[str, Any]:
    prices = snapshot.get("prices")
    prices = prices if isinstance(prices, dict) else {}
    for region in regions:
        row = prices.get(f"shopee:{region}")
        if isinstance(row, dict) and isinstance(row.get("sku_prices"), dict):
            return {
                str(sku): facts.get("list_price")
                for sku, facts in row["sku_prices"].items()
                if isinstance(facts, dict)
            }
    return {}


def _numeric_map_exact(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    if not expected or set(observed) != set(expected):
        return False
    try:
        return all(
            abs(float(observed[key]) - float(expected[key])) < 0.000001
            for key in expected
        )
    except (TypeError, ValueError):
        return False


def _model_price(row: Mapping[str, Any]) -> Any:
    price_info = row.get("price_info")
    if isinstance(price_info, Mapping):
        return price_info.get("original_price")
    return row.get("original_price")


def _expected_shopee_parcel(snapshot: Mapping[str, Any]) -> dict[str, float]:
    rows = snapshot.get("skus") or []
    try:
        return {
            "weight": max(float(row["weight_kg"]) for row in rows),
            "package_length": max(float(row["package_cm"][0]) for row in rows),
            "package_width": max(float(row["package_cm"][1]) for row in rows),
            "package_height": max(float(row["package_cm"][2]) for row in rows),
        }
    except (KeyError, IndexError, TypeError, ValueError):
        return {}


def _master_parcel_exact(
    item: Mapping[str, Any], expected: Mapping[str, float]
) -> bool:
    if not expected:
        return False
    dimensions = item.get("dimension")
    if not isinstance(dimensions, Mapping):
        return False
    try:
        return (
            abs(float(item.get("weight")) - expected["weight"]) < 0.000001
            and all(
                abs(float(dimensions.get(key)) - expected[key]) < 0.000001
                for key in (
                    "package_length",
                    "package_width",
                    "package_height",
                )
            )
        )
    except (TypeError, ValueError):
        return False


def _global_item_id(dispatch: Mapping[str, Any]) -> str:
    value = dispatch.get("platform_item_id")
    if not value and isinstance(dispatch.get("safe_response"), dict):
        value = dispatch["safe_response"].get("global_item_id")
    return str(value or "").strip()


def _image_ids(item: Mapping[str, Any]) -> list[str]:
    image = item.get("image")
    if isinstance(image, dict):
        rows = image.get("image_id_list") or []
    else:
        rows = item.get("image_id_list") or []
    return [str(value) for value in rows if str(value)]


def _missing(message: str) -> dict[str, Any]:
    return {
        "schema_version": "platform-readback-fact/v1",
        "platform": "shopee",
        "provider": "official_shopee_partner_api",
        "exists": False,
        "verified": False,
        "complete": False,
        "status": "NOT_FOUND",
        "message": message,
        "retry_safe": True,
    }


if __name__ == "__main__":
    raise SystemExit(run("shopee", readback))
