"""Existing-product-only Ozon stock recovery primitives."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

from modules.ozon.client import ozon_post


def _same_decimal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


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
    observed_images = item.get("images") if isinstance(item.get("images"), list) else []
    title_matches = bool(item.get("name"))
    if expected_title is not None:
        title_matches = str(item.get("name") or "") == str(expected_title)
    price_matches = bool(item.get("price"))
    if expected_price is not None:
        price_matches = _same_decimal(item.get("price"), expected_price)
    images_match = bool(observed_images)
    if expected_images is not None:
        images_match = len(observed_images) == len(expected_images) and bool(expected_images)
    return {
        "product_id": str(item.get("id") or item.get("product_id") or ""),
        "checks": {
            "created": created,
            "approved": approved,
            "title": title_matches,
            "price": price_matches,
            "images": images_match,
            "stock_false": not bool(item.get("stocks")),
        },
    }


def stock_existing_product(*, product_id: str, offer_id: str) -> dict:
    if product_id != "5687436857" or offer_id != "0954":
        raise RuntimeError("target-scoped Ozon stock identity mismatch")
    raise RuntimeError("existing-product stock payload must be injected with prepared warehouse facts")
