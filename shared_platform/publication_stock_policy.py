"""Governed stock policy shared by all product-publication rounds."""

from __future__ import annotations

from typing import Any


PUBLICATION_STOCK_POLICY_VERSION = "publication-default-stock/v1"
DEFAULT_LISTING_STOCK = 200


def default_publication_stock_policy() -> dict[str, Any]:
    """Return the reviewable default applied to every selected SKU."""

    return {
        "schema_version": PUBLICATION_STOCK_POLICY_VERSION,
        "quantity_per_sku": DEFAULT_LISTING_STOCK,
        "scope": "EACH_SELECTED_SKU",
        "source": "SYSTEM_GOVERNED_DEFAULT",
        "review_round": "ROUND1",
    }


__all__ = [
    "DEFAULT_LISTING_STOCK",
    "PUBLICATION_STOCK_POLICY_VERSION",
    "default_publication_stock_policy",
]
