"""Read-only local catalog snapshot for profit settlement previews."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping


@dataclass(frozen=True)
class CatalogQualityIssue:
    code: str
    record_id: str
    field: str
    message: str


@dataclass(frozen=True)
class LocalCatalogSnapshot:
    seller_sku_by_platform_sku: Mapping[str, str]
    costs_by_sku: Mapping[str, Decimal]
    cost_candidates_by_sku: Mapping[str, tuple[Decimal, ...]]
    product_by_platform_sku: Mapping[str, Mapping[str, Any]]
    product_by_seller_sku: Mapping[str, Mapping[str, Any]]
    weight_by_seller_sku: Mapping[str, Mapping[str, Any]]
    snapshot_id: str
    effective_at: str
    issues: tuple[CatalogQualityIssue, ...]


def load_local_catalog(database_path: str | Path) -> LocalCatalogSnapshot:
    """Open an existing SQLite catalog with ``mode=ro`` and return a snapshot."""
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        products = connection.execute(
            """SELECT p.sku_id, p.seller_sku, p.product_name, p.sku_name,
                      p.image_url, p.currency, p.shop_cipher, p.updated_at,
                      c.cost_cny, c.updated_at AS cost_updated_at
               FROM products p LEFT JOIN sku_costs c ON c.sku_id=p.sku_id
               ORDER BY COALESCE(c.updated_at, 0) DESC, COALESCE(p.updated_at, 0) DESC,
                        p.sku_id, p.shop_cipher"""
        ).fetchall()
        shopee = connection.execute(
            """SELECT seller_sku, product_name, model_name, image_url, currency,
                      region, shop_id, updated_at
               FROM shopee_products ORDER BY COALESCE(updated_at, 0) DESC,
                      seller_sku, shop_id"""
        ).fetchall()
        weights = connection.execute(
            """SELECT seller_sku, weight_g, package_count, depth_mm, width_mm,
                      height_mm, updated_at
               FROM sku_logistics_weights ORDER BY COALESCE(updated_at, 0) DESC,
                      seller_sku"""
        ).fetchall()
    finally:
        connection.close()

    mapping_candidates: dict[str, set[str]] = defaultdict(set)
    cost_candidates: dict[str, set[Decimal]] = defaultdict(set)
    mapping: dict[str, str] = {}
    costs: dict[str, Decimal] = {}
    by_platform: dict[str, Mapping[str, Any]] = {}
    by_seller: dict[str, Mapping[str, Any]] = {}
    for row in products:
        platform_sku = _text(row["sku_id"])
        seller_sku = _canonical_sku(row["seller_sku"])
        if not platform_sku or not seller_sku:
            continue
        mapping_candidates[platform_sku].add(seller_sku)
        mapping.setdefault(platform_sku, seller_sku)
        metadata = {
            "seller_sku": seller_sku,
            "product_name": _text(row["product_name"]),
            "variant_name": _text(row["sku_name"]),
            "image_url": _text(row["image_url"]),
            "currency": _text(row["currency"]).upper(),
            "shop_id": _text(row["shop_cipher"]),
        }
        by_platform.setdefault(platform_sku, metadata)
        by_seller.setdefault(seller_sku, metadata)
        cost = _decimal(row["cost_cny"])
        if cost is not None and cost > 0:
            cost_candidates[seller_sku].add(cost)
            costs.setdefault(seller_sku, cost)

    for row in shopee:
        seller_sku = _canonical_sku(row["seller_sku"])
        if not seller_sku:
            continue
        by_seller.setdefault(seller_sku, {
            "seller_sku": seller_sku,
            "product_name": _text(row["product_name"]),
            "variant_name": _text(row["model_name"]),
            "image_url": _text(row["image_url"]),
            "currency": _text(row["currency"]).upper(),
            "region": _text(row["region"]).upper(),
            "shop_id": _text(row["shop_id"]),
        })

    weight_by_seller: dict[str, Mapping[str, Any]] = {}
    for row in weights:
        seller_sku = _canonical_sku(row["seller_sku"])
        if seller_sku and seller_sku not in weight_by_seller:
            weight_by_seller[seller_sku] = {
                "unit_weight_g": row["weight_g"],
                "package_count": row["package_count"],
                "depth_mm": row["depth_mm"],
                "width_mm": row["width_mm"],
                "height_mm": row["height_mm"],
                "weight_source": "shop.db:sku_logistics_weights",
            }

    issues: list[CatalogQualityIssue] = []
    for platform_sku, values in sorted(mapping_candidates.items()):
        if len(values) > 1:
            issues.append(CatalogQualityIssue("conflicting_platform_sku_mapping", platform_sku, "seller_sku", f"platform SKU maps to {len(values)} seller SKUs"))
    for seller_sku, values in sorted(cost_candidates.items()):
        if len(values) > 1:
            issues.append(CatalogQualityIssue("conflicting_cost", seller_sku, "cost_cny", f"seller SKU has {len(values)} positive costs; newest catalog row selected"))

    canonical = {
        "mapping": mapping,
        "costs": {key: str(value) for key, value in costs.items()},
        "products": by_platform,
        "shopee_products": by_seller,
        "weights": weight_by_seller,
    }
    digest = sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    effective_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    candidates = {
        sku: tuple(sorted(values)) for sku, values in sorted(cost_candidates.items())
    }
    return LocalCatalogSnapshot(mapping, costs, candidates, by_platform, by_seller, weight_by_seller, f"shop-db-catalog:{digest}", effective_at, tuple(issues))


def enrich_settlement_row(row: Mapping[str, Any], catalog: LocalCatalogSnapshot) -> dict[str, Any]:
    """Attach display metadata and weight without changing monetary evidence."""
    output = dict(row)
    platform_sku = _text(row.get("platform_sku"))
    seller_sku = _canonical_sku(row.get("canonical_sku") or row.get("seller_sku"))
    metadata = catalog.product_by_platform_sku.get(platform_sku) or catalog.product_by_seller_sku.get(seller_sku) or {}
    weight = catalog.weight_by_seller_sku.get(seller_sku) or {}
    for field in ("product_name", "variant_name", "image_url", "shop_id"):
        if not _text(output.get(field)) and _text(metadata.get(field)):
            output[field] = metadata[field]
    for field, value in weight.items():
        if output.get(field) in (None, ""):
            output[field] = value
    return output


def _canonical_sku(value: object) -> str:
    raw = _text(value)
    return raw[-4:].zfill(4) if raw.isdigit() else raw


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
