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
        "product_operations", ("products",), ("/catalog", "/costs"),
        ("modules.catalog", "modules.products.costs", "modules.sourcing.new_product_workbench"),
    ),
    DomainRegistration(
        "content_operations", (), ("/titles", "/images"),
        ("modules.products.titles", "modules.products.images", "modules.sourcing.image_workbench"),
    ),
    DomainRegistration(
        "channel_operations", ("shopee", "ozon", "rus"),
        ("/ozon", "/mx", "/uk", "/promotions", "/deactivate"),
        ("modules.ozon", "modules.shopee", "modules.miaoshou"),
    ),
    DomainRegistration(
        "supply_chain_operations", ("sourcing", "treasury"), ("/sourcing",),
        ("modules.sourcing", "modules.catalog.logistics_weights"),
    ),
    DomainRegistration(
        "data_operations", ("finance", "ads", "affiliate"),
        ("/settlement", "/analytics", "/sku-profit"),
        ("modules.finance", "modules.ads", "modules.affiliate", "modules.pricing"),
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
