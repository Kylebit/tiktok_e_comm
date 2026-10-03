"""Existing-product-only Ozon stock recovery primitives."""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Mapping

from modules.ozon.client import ozon_post


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _same_decimal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _unique_product_images(item: Mapping[str, object]) -> list[str]:
    """Normalize Ozon's split gallery fields without double-counting the cover."""

    values: list[str] = []
    for field in ("primary_image", "color_image", "images"):
        raw = item.get(field)
        rows = raw if isinstance(raw, list) else [raw]
        for value in rows:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def _stock_is_empty(value: object) -> bool:
    if isinstance(value, list):
        return value == []
    if isinstance(value, Mapping):
        rows = value.get("stocks")
        return value.get("has_stock") is False and isinstance(rows, list) and rows == []
    return False


def _fbs_stock_rows(response: object) -> list[Mapping[str, object]] | None:
    if not isinstance(response, Mapping):
        return None
    products = response.get("products")
    if products is not None:
        if not isinstance(products, list):
            return None
        rows: list[Mapping[str, object]] = []
        for product in products:
            if not isinstance(product, Mapping):
                return None
            stocks = product.get("stocks")
            if stocks is None:
                rows.append(product)
                continue
            if not isinstance(stocks, list) or any(
                not isinstance(stock, Mapping) for stock in stocks
            ):
                return None
            for stock in stocks:
                rows.append({"offer_id": product.get("offer_id"), **dict(stock)})
        return rows
    result = response.get("result")
    return result if isinstance(result, list) else None


def read_existing_product(
    *,
    offer_id: str,
    expected_title: str | None = None,
    expected_price: int | float | str | None = None,
    expected_images: list[str] | tuple[str, ...] | None = None,
) -> dict:
    response = ozon_post("/v3/product/info/list", {"offer_id": [offer_id], "limit": 10, "visibility": "ALL"})
    items = response.get("items") or []
    if len(items) != 1:
        return {"checks": {}}
    item = items[0]
    statuses = item.get("statuses") if isinstance(item.get("statuses"), dict) else {}
    created = (
        statuses.get("is_created") is True
        or item.get("is_created") is True
    )
    status = str(statuses.get("status") or item.get("status") or "").lower()
    moderate_status = str(statuses.get("moderate_status") or "").lower()
    approved = status in {"approved", "price_sent"} or moderate_status == "approved"
    observed_images = _unique_product_images(item)
    title_matches = bool(item.get("name"))
    if expected_title is not None:
        title_matches = str(item.get("name") or "") == str(expected_title)
    price_matches = bool(item.get("price"))
    if expected_price is not None:
        price_matches = _same_decimal(item.get("price"), expected_price)
    images_match = bool(observed_images)
    if expected_images is not None:
        images_match = len(observed_images) == len(expected_images) and bool(expected_images)
    product_id = str(item.get("id") or item.get("product_id") or "")
    listing_identity = {
        "provider": "ozon",
        "product_id": product_id,
        "seller_sku": str(item.get("offer_id") or offer_id),
        "title": str(item.get("name") or ""),
        "price": str(item.get("price") or ""),
        "image_count": len(observed_images),
        "category_id": str(
            item.get("description_category_id") or item.get("category_id") or ""
        ),
    }
    return {
        "product_id": product_id,
        "listing_digest": _canonical_digest(listing_identity),
        "checks": {
            "created": created,
            "approved": approved,
            "title": title_matches,
            "price": price_matches,
            "images": images_match,
            "stock_false": _stock_is_empty(item.get("stocks")),
        },
    }


def _bound_stock_command(continuation_manifest: Mapping[str, object]) -> dict:
    if not isinstance(continuation_manifest, Mapping):
        raise RuntimeError("approved Ozon continuation manifest is required")
    required = {
        "schema_version",
        "target_label",
        "operation_kind",
        "product_id",
        "seller_sku",
        "offer_id",
        "expected_listing_digest",
        "desired_stock_quantity",
        "warehouse_id",
        "warehouse_policy",
        "existing_product_only",
        "forbid_import",
        "forbid_create",
    }
    if any(field not in continuation_manifest for field in required):
        raise RuntimeError("approved Ozon continuation manifest is incomplete")
    command = {field: continuation_manifest[field] for field in required}
    if (
        command["schema_version"] != "ozon-existing-product-stock-command/v2"
        or command["target_label"] != "ozon:RU"
        or command["operation_kind"]
        != "ozon_existing_product_stock_reconciliation_v1"
        or command["warehouse_policy"] != "exact_active_non_kgt"
        or command["existing_product_only"] is not True
        or command["forbid_import"] is not True
        or command["forbid_create"] is not True
    ):
        raise RuntimeError("approved Ozon continuation manifest policy is invalid")
    for field in ("product_id", "seller_sku", "offer_id", "expected_listing_digest"):
        if not isinstance(command[field], str) or not command[field].strip():
            raise RuntimeError("approved Ozon continuation manifest identity is invalid")
    if command["seller_sku"] != command["offer_id"]:
        raise RuntimeError("approved Ozon continuation manifest SKU is ambiguous")
    for field in ("desired_stock_quantity", "warehouse_id"):
        value = command[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeError("approved Ozon continuation manifest stock binding is invalid")
    return command


def read_bound_warehouse(*, warehouse_id: int) -> dict:
    if isinstance(warehouse_id, bool) or not isinstance(warehouse_id, int) or warehouse_id <= 0:
        raise RuntimeError("approved Ozon continuation warehouse identity is invalid")
    response = ozon_post("/v2/warehouse/list", {})
    rows = response.get("warehouses") if isinstance(response, Mapping) else None
    if rows is None and isinstance(response, Mapping):
        rows = response.get("result")
    matches = [
        row for row in rows or ()
        if isinstance(row, Mapping)
        and str(row.get("warehouse_id") or "") == str(warehouse_id)
        and str(row.get("status") or "").strip().casefold() in {"active", "created"}
        and row.get("is_kgt") is False
    ]
    return {"exact": isinstance(rows, list) and len(matches) == 1, "warehouse_id": warehouse_id}


def stock_existing_product(*, continuation_manifest: Mapping[str, object]) -> dict:
    """Perform exactly one manifest-bound stock update and one exact readback."""

    command = _bound_stock_command(continuation_manifest)
    product = read_existing_product(offer_id=command["offer_id"])
    checks = product.get("checks") if isinstance(product, Mapping) else None
    if (
        not isinstance(checks, Mapping)
        or str(product.get("product_id") or "") != command["product_id"]
        or str(product.get("listing_digest") or "")
        != command["expected_listing_digest"]
        or any(
            checks.get(field) is not True
            for field in ("created", "approved", "title", "price", "images", "stock_false")
        )
    ):
        raise RuntimeError("approved Ozon continuation listing binding changed")

    if read_bound_warehouse(warehouse_id=command["warehouse_id"])["exact"] is not True:
        raise RuntimeError("approved Ozon continuation warehouse binding changed")

    payload = {
        "stocks": [
            {
                "offer_id": command["offer_id"],
                "stock": command["desired_stock_quantity"],
                "warehouse_id": command["warehouse_id"],
            }
        ]
    }
    response = ozon_post("/v2/products/stocks", payload)
    results = response.get("result") if isinstance(response, Mapping) else None
    if (
        not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], Mapping)
        or any(results[0].get("errors") or ())
    ):
        raise RuntimeError("Ozon stock update was not accepted exactly once")

    readback = ozon_post(
        "/v2/product/info/stocks-by-warehouse/fbs",
        {"offer_id": [command["offer_id"]], "limit": 1000},
    )
    rows = _fbs_stock_rows(readback)
    matched = [
        row
        for row in rows or ()
        if isinstance(row, Mapping)
        and str(row.get("offer_id") or "") == command["offer_id"]
        and str(row.get("warehouse_id") or "") == str(command["warehouse_id"])
        and row.get("present") == command["desired_stock_quantity"]
    ]
    verified = isinstance(rows, list) and len(rows) == 1 and len(matched) == 1
    return {
        "verified": verified,
        "product_id": command["product_id"],
        "seller_sku": command["seller_sku"],
        "warehouse_id": command["warehouse_id"],
        "quantity": command["desired_stock_quantity"],
        "expected_listing_digest": command["expected_listing_digest"],
        "external_writes_performed": ["ozon:stock:update"],
    }
