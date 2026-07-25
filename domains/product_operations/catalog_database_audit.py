"""Side-effect-free catalog database quality audit."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re
from typing import Any

from core.db import connect_readonly


@dataclass(frozen=True)
class CatalogDatabaseAudit:
    product_count: int
    shop_count: int
    cost_count: int
    products_by_currency: dict[str, int]
    direct_missing_cost_rows: int
    direct_missing_cost_by_currency: dict[str, int]
    fallback_resolved_cost_rows: int
    unresolved_cost_rows: int
    unresolved_cost_key_count: int
    cost_conflicts: tuple[dict[str, Any], ...]
    same_shop_seller_sku_duplicates: tuple[dict[str, Any], ...]
    product_shop_orphans: int
    analytics_orphans: int
    logistics_exact_unmatched: int
    logistics_canonical_unmatched: int
    shopee_nonpositive_prices: int

    @property
    def needs_review(self) -> bool:
        return any(
            (
                self.same_shop_seller_sku_duplicates,
                self.cost_conflicts,
                self.unresolved_cost_rows,
                self.product_shop_orphans,
                self.analytics_orphans,
                self.logistics_canonical_unmatched,
                self.shopee_nonpositive_prices,
            )
        )

    def payload(self) -> dict[str, Any]:
        return {
            "counts": {
                "products": self.product_count,
                "shops": self.shop_count,
                "costs": self.cost_count,
                "products_by_currency": dict(self.products_by_currency),
            },
            "cost_coverage": {
                "direct_missing_rows": self.direct_missing_cost_rows,
                "direct_missing_by_currency": dict(self.direct_missing_cost_by_currency),
                "fallback_resolved_rows": self.fallback_resolved_cost_rows,
                "unresolved_rows": self.unresolved_cost_rows,
                "unresolved_key_count": self.unresolved_cost_key_count,
                "conflicts": [dict(item) for item in self.cost_conflicts],
            },
            "identity": {
                "same_shop_seller_sku_duplicates": [
                    dict(item) for item in self.same_shop_seller_sku_duplicates
                ],
                "product_shop_orphans": self.product_shop_orphans,
            },
            "derived_data": {
                "analytics_orphans": self.analytics_orphans,
                "logistics_exact_unmatched": self.logistics_exact_unmatched,
                "logistics_canonical_unmatched": self.logistics_canonical_unmatched,
                "shopee_nonpositive_prices": self.shopee_nonpositive_prices,
            },
            "needs_review": self.needs_review,
        }


def audit_catalog_database(path: str | Path) -> CatalogDatabaseAudit:
    """Audit catalog semantics without initializing or migrating the database."""
    connection = connect_readonly(path)
    try:
        products = [
            dict(row)
            for row in connection.execute(
                """
                SELECT p.sku_id, p.shop_cipher, p.product_id, p.seller_sku,
                       p.currency, c.cost_cny
                FROM products p
                LEFT JOIN sku_costs c ON c.sku_id = p.sku_id
                """
            )
        ]
        product_seller_skus = {
            str(row["seller_sku"] or "").strip()
            for row in products
            if str(row["seller_sku"] or "").strip()
        }
        product_match_keys = {
            _canonical_seller_sku(value) for value in product_seller_skus
        }
        cost_values_by_key: dict[str, set[Decimal]] = defaultdict(set)
        for row in products:
            value = row.get("cost_cny")
            key = _canonical_seller_sku(row.get("seller_sku"))
            if key and value is not None and Decimal(str(value)) > 0:
                cost_values_by_key[key].add(Decimal(str(value)))

        missing = [row for row in products if row.get("cost_cny") is None]
        fallback_resolved = 0
        unresolved_keys: set[str] = set()
        for row in missing:
            key = _canonical_seller_sku(row.get("seller_sku"))
            if key and cost_values_by_key.get(key):
                fallback_resolved += 1
            elif key:
                unresolved_keys.add(key)
        same_shop_duplicates = tuple(
            {
                "shop_cipher": row["shop_cipher"],
                "seller_sku": row["seller_sku"],
                "row_count": int(row["row_count"]),
                "product_count": int(row["product_count"]),
                "sku_count": int(row["sku_count"]),
            }
            for row in connection.execute(
                """
                SELECT shop_cipher, seller_sku, COUNT(*) AS row_count,
                       COUNT(DISTINCT product_id) AS product_count,
                       COUNT(DISTINCT sku_id) AS sku_count
                FROM products
                WHERE seller_sku IS NOT NULL AND TRIM(seller_sku) != ''
                GROUP BY shop_cipher, seller_sku
                HAVING COUNT(*) > 1
                ORDER BY shop_cipher, seller_sku
                """
            )
        )
        weights = [
            str(row[0] or "").strip()
            for row in connection.execute("SELECT seller_sku FROM sku_logistics_weights")
        ]
        return CatalogDatabaseAudit(
            product_count=len(products),
            shop_count=int(connection.execute("SELECT COUNT(*) FROM shops").fetchone()[0]),
            cost_count=int(
                connection.execute("SELECT COUNT(*) FROM sku_costs").fetchone()[0]
            ),
            products_by_currency=dict(
                sorted(Counter(str(row["currency"] or "") for row in products).items())
            ),
            direct_missing_cost_rows=len(missing),
            direct_missing_cost_by_currency=dict(
                sorted(Counter(str(row["currency"] or "") for row in missing).items())
            ),
            fallback_resolved_cost_rows=fallback_resolved,
            unresolved_cost_rows=len(missing) - fallback_resolved,
            unresolved_cost_key_count=len(unresolved_keys),
            cost_conflicts=tuple(
                {
                    "seller_sku_key": key,
                    "costs_cny": tuple(str(value) for value in sorted(values)),
                }
                for key, values in sorted(cost_values_by_key.items())
                if len(values) > 1
            ),
            same_shop_seller_sku_duplicates=same_shop_duplicates,
            product_shop_orphans=int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM products p
                    LEFT JOIN shops s ON s.cipher = p.shop_cipher
                    WHERE s.cipher IS NULL
                    """
                ).fetchone()[0]
            ),
            analytics_orphans=int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM product_analytics a
                    LEFT JOIN products p
                      ON p.product_id = a.product_id
                     AND p.shop_cipher = a.shop_cipher
                    WHERE p.sku_id IS NULL
                    """
                ).fetchone()[0]
            ),
            logistics_exact_unmatched=sum(
                value not in product_seller_skus for value in weights
            ),
            logistics_canonical_unmatched=sum(
                _canonical_seller_sku(value) not in product_match_keys
                for value in weights
            ),
            shopee_nonpositive_prices=int(
                connection.execute(
                    "SELECT COUNT(*) FROM shopee_products WHERE price IS NULL OR price <= 0"
                ).fetchone()[0]
            ),
        )
    finally:
        connection.close()


def _canonical_seller_sku(value: object) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d+", raw):
        return raw[-4:].zfill(4)
    return raw
