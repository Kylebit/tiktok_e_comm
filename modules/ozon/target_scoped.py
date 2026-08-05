"""Existing-product-only Ozon stock recovery primitives."""
from __future__ import annotations
from modules.ozon.client import ozon_post


def read_existing_product(*, offer_id: str) -> dict:
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
    return {
        "product_id": str(item.get("id") or item.get("product_id") or ""),
        "checks": {
            "created": created,
            "approved": approved,
            "title": bool(item.get("name")),
            "price": bool(item.get("price")),
            "images": bool(item.get("images")),
            "stock_false": not bool(item.get("stocks")),
        },
    }


def stock_existing_product(*, product_id: str, offer_id: str) -> dict:
    if product_id != "5687436857" or offer_id != "0954":
        raise RuntimeError("target-scoped Ozon stock identity mismatch")
    raise RuntimeError("existing-product stock payload must be injected with prepared warehouse facts")
