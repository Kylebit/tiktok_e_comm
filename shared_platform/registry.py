"""Compatibility registry for ownership of legacy CLI and HTTP entry points.

It records current ownership only; dispatch remains in the existing handlers.
This creates a verified seam for later extraction without changing routes,
ports, command names, or side effects in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainRegistration:
    name: str
    cli_roots: tuple[str, ...]
    http_prefixes: tuple[str, ...]
    legacy_modules: tuple[str, ...]


DOMAIN_REGISTRATIONS: tuple[DomainRegistration, ...] = (
    DomainRegistration(
        "product_operations", ("products", "sourcing", "treasury"),
        (
            "/new-product",
            "/catalog",
            "/costs",
            "/sourcing",
            "/api/product-workspace",
            "/api/catalog",
            "/api/costs",
            "/api/new-product",
            "/api/sourcing",
        ),
        ("modules.catalog", "modules.products.costs", "modules.sourcing.new_product_workbench"),
    ),
    DomainRegistration(
        "content_operations", (),
        ("/titles", "/images", "/api/titles", "/api/images", "/api/proxy-image"),
        ("modules.products.titles", "modules.products.images", "modules.sourcing.image_workbench"),
    ),
    DomainRegistration(
        "channel_operations", ("shopee", "ozon", "rus", "affiliate"),
        (
            "/ozon",
            "/mx",
            "/uk",
            "/promotions",
            "/deactivate",
            "/api/ozon",
            "/api/rus",
            "/api/mx",
            "/api/uk",
            "/api/promotions",
            "/api/deactivate",
            "/api/shopee",
        ),
        ("modules.ozon", "modules.shopee", "modules.miaoshou", "modules.affiliate"),
    ),
    DomainRegistration(
        "supply_chain_operations", (), (),
        ("modules.catalog.logistics_weights",),
    ),
    DomainRegistration(
        "data_operations", ("finance", "ads"),
        (
            "/profit",
            "/settlement",
            "/analytics",
            "/sku-profit",
            "/api/profit-center",
            "/api/settlement",
            "/api/analytics",
            "/api/sku-profit",
            "/api/orders-pull",
            "/api/shopee/profit",
            "/api/billing",
        ),
        ("modules.finance", "modules.ads", "modules.pricing"),
    ),
    DomainRegistration(
        "shared_platform", ("init", "auth", "status", "tokens", "serve", "sync", "digest", "feishu"),
        (
            "/",
            "/release",
            "/internal/release",
            "/api/status",
            "/api/health",
            "/api/orbit",
            "/api/release",
            "/api/digest",
            "/api/feishu",
        ),
        ("core", "modules.hub", "modules.products.server"),
    ),
)


def cli_registry() -> dict[str, str]:
    return {command: item.name for item in DOMAIN_REGISTRATIONS for command in item.cli_roots}


def http_registry() -> dict[str, str]:
    return {prefix: item.name for item in DOMAIN_REGISTRATIONS for prefix in item.http_prefixes}


def owner_for_http_path(path: str) -> str | None:
    """Return the most-specific registered owner for a legacy HTTP path."""
    matches = [(prefix, owner) for prefix, owner in http_registry().items() if path == prefix or path.startswith(prefix + "/")]
    return max(matches, default=("", None), key=lambda match: len(match[0]))[1]
