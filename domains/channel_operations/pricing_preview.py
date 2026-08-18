"""Pure mapping from the legacy workbench price audit to channel targets.

The pricing formula remains owned by ``new_product_workbench.price_review``.
This module only preserves its rows and describes how the existing Shopee and
Ozon adapters derive prices from a verified TikTok listing.  It performs no
I/O and does not call any marketplace adapter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


MASTER_SITE_ORDER = ("PH", "MY", "TH", "VN", "MX", "GB")


def build_channel_pricing_preview(
    pricing_review: Mapping[str, Any],
    *,
    selected_site_keys: Iterable[object],
    shopee_exchange_rates: Mapping[str, object],
    ozon_exchange_rates: Mapping[str, object],
) -> dict[str, Any]:
    """Return JSON-ready store prices and cross-channel price lineage."""

    selected_keys = tuple(
        dict.fromkeys(
            str(value or "").strip().lower()
            for value in selected_site_keys
            if str(value or "").strip()
        )
    )
    legacy_rows = _legacy_rows(pricing_review)
    rows_by_key = {
        str(row.get("id") or _special_key(row)).lower(): row
        for row in legacy_rows
    }
    selected_rows = [
        dict(rows_by_key[key])
        for key in selected_keys
        if key in rows_by_key
    ]
    unknown_keys = [key for key in selected_keys if key not in rows_by_key]
    master = _master_row(selected_rows)
    blockers: list[str] = []
    if unknown_keys:
        blockers.append(
            "No legacy pricing row exists for selected target(s): "
            + ", ".join(unknown_keys)
        )
    if not selected_rows:
        blockers.append("No selected TikTok store has a legacy pricing row.")
    if not master:
        blockers.append("A TikTok master price is required for derived channels.")

    target_pricing: dict[str, dict[str, Any]] = {
        "miaoshou:COMMON": {
            "role": "common_draft",
            "status": "ready" if selected_rows else "blocked",
            "store_prices": [_store_price(row) for row in selected_rows],
            "write_fields": [
                "shopCollectItemInfo.skuMap.*.price",
                "shopCollectItemInfo.skuMap.*.priceIncludeVat",
                "shopIdAndReplicatedProductsMap.*.skus.*.priceIncludeVat",
            ],
        }
    }
    for site in sorted({str(row.get("region") or "").upper() for row in selected_rows}):
        site_rows = [
            row for row in selected_rows if str(row.get("region") or "").upper() == site
        ]
        target_pricing[f"tiktok:{site}"] = {
            "role": "master_listing",
            "status": (
                "ready"
                if site_rows and all(row.get("list_price") is not None for row in site_rows)
                else "blocked"
            ),
            "store_prices": [_store_price(row) for row in site_rows],
            "source_field": "legacy price_review.*.list_price",
            "write_fields": [
                "skuMap.*.price",
                "skuMap.*.priceIncludeVat",
            ],
        }

        source = _master_row(site_rows) or master
        target_pricing[f"shopee:{site}"] = _shopee_price(
            source,
            target_site=site,
            exchange_rates=shopee_exchange_rates,
        )

    target_pricing["ozon:RU"] = _ozon_price(
        master,
        exchange_rates=ozon_exchange_rates,
    )
    pricing_status = str(
        "ready"
        if selected_rows
        and not blockers
        and all(row.get("list_price") is not None for row in selected_rows)
        else "blocked"
    )
    return {
        "schema_version": "channel-pricing-preview/v1",
        "status": pricing_status,
        "algorithm": {
            "owner": "modules.sourcing.new_product_workbench.price_review",
            "legacy_api": "/api/new-product/preview",
            "legacy_ui": "/new-product#renderPricing",
            "semantic_rule": (
                "TikTok prices are calculated per selected store; Shopee and "
                "Ozon remain derived from TikTok read-back rather than being "
                "independently repriced."
            ),
        },
        "input": dict(pricing_review.get("input") or {}),
        "workbench_exchange_rates": dict(pricing_review.get("rates") or {}),
        "shopee_exchange_rates": _numeric_rates(shopee_exchange_rates),
        "ozon_exchange_rates": _numeric_rates(ozon_exchange_rates),
        "selected_site_keys": list(selected_keys),
        "selected_store_prices": [_store_price(row) for row in selected_rows],
        "all_legacy_store_prices": [_store_price(row) for row in legacy_rows],
        "master_price_source": _store_price(master) if master else None,
        "target_pricing": target_pricing,
        "legacy_audit": dict(pricing_review.get("audit") or {}),
        "blockers": blockers,
        "external_calls_performed": [],
    }


def _legacy_rows(pricing_review: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in (pricing_review.get("sea") or ())
        if isinstance(row, Mapping)
    ]
    for key in ("mx", "uk"):
        row = pricing_review.get(key)
        if isinstance(row, Mapping) and row.get("region"):
            rows.append(dict(row))
    return rows


def _special_key(row: Mapping[str, Any]) -> str:
    region = str(row.get("region") or "").upper()
    return "gb" if region == "GB" else region.lower()


def _master_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for site in MASTER_SITE_ORDER:
        for row in rows:
            if str(row.get("region") or "").upper() == site:
                return row
    return rows[0] if rows else None


def _store_price(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "target_key": str(row.get("id") or _special_key(row)).lower(),
        "shop": str(row.get("shop") or ""),
        "shop_id": row.get("shop_id"),
        "region": str(row.get("region") or "").upper(),
        "currency": str(row.get("currency") or "").upper(),
        "list_price": row.get("list_price"),
        "sale_after_discount": row.get("sale_after_discount_local"),
        "discount_reserve_pct": row.get("discount_reserve_pct"),
        "estimated_profit_local": (
            row.get("estimated_profit_local")
            if row.get("estimated_profit_local") is not None
            else row.get("estimated_profit")
        ),
        "estimated_profit_cny": row.get("estimated_profit_cny"),
        "profit_margin_on_sale_pct": row.get("profit_margin_on_sale_pct"),
        "minimum_profit_cny": row.get("minimum_profit_cny"),
        "min_profit_adjusted": bool(row.get("min_profit_adjusted")),
        "status": str(row.get("status") or "unknown"),
        "fees": _fees(row),
        "formula_parameters": dict(row.get("header_meta") or {}),
        "notes": str(row.get("notes") or ""),
    }


def _fees(row: Mapping[str, Any]) -> dict[str, Any]:
    names = (
        "goods_cost_local",
        "logistics_local",
        "hidden_shipping_local",
        "shipping_local",
        "commission_local",
        "transaction_local",
        "extra_fee_local",
        "import_tax_local",
        "vat_local",
        "sfp_local",
        "smart_promo_local",
        "affiliate_local",
        "ad_local",
        "creator_local",
        "seller_tax_local",
        "fixed_fee_local",
    )
    return {name: row.get(name) for name in names if row.get(name) is not None}


def _shopee_price(
    source: Mapping[str, Any] | None,
    *,
    target_site: str,
    exchange_rates: Mapping[str, object],
) -> dict[str, Any]:
    if not source or source.get("list_price") is None:
        return {
            "role": "derived_listing",
            "status": "blocked",
            "depends_on": "tiktok:MASTER:verified_readback",
            "blocker": "TikTok master read-back price is unavailable.",
        }
    currency = str(source.get("currency") or "").upper()
    rate = _positive_number(exchange_rates.get(currency))
    local_price = _positive_number(source.get("list_price"))
    global_cny = round(local_price * rate, 2) if rate and local_price else None
    return {
        "role": "derived_listing",
        "status": "awaiting_tiktok_readback" if global_cny is not None else "blocked",
        "target_site": target_site,
        "depends_on": "tiktok:MASTER:verified_readback",
        "source": {
            **_store_price(source),
            "field_after_readback": "skus[].price.sale_price",
        },
        "derived_preview": {
            "global_original_price_cny": global_cny,
            "local_original_price": local_price,
            "source_currency": currency,
            "exchange_rate_cny_per_local": rate,
        },
        "write_fields": [
            "global_item.original_price (CNY)",
            "publish_task.item.original_price (TikTok source numeric value)",
        ],
        "formula": "round(tiktok_sale_price * settings.exchange_rates[currency], 2)",
        "risk": (
            "The adapter must recompute from verified TikTok sale_price. Its "
            "local publish task historically reuses the source numeric price."
        ),
    }


def _ozon_price(
    source: Mapping[str, Any] | None,
    *,
    exchange_rates: Mapping[str, object],
) -> dict[str, Any]:
    if not source or source.get("list_price") is None:
        return {
            "role": "derived_listing",
            "status": "blocked",
            "depends_on": "tiktok:MASTER:verified_readback",
            "blocker": "TikTok master read-back price is unavailable.",
        }
    currency = str(source.get("currency") or "").upper()
    rate = _positive_number(exchange_rates.get(currency))
    local_price = _positive_number(source.get("list_price"))
    price_cny = round(local_price * rate) if rate and local_price else None
    return {
        "role": "derived_listing",
        "status": "awaiting_tiktok_readback" if price_cny is not None else "blocked",
        "target_site": "RU",
        "depends_on": "tiktok:MASTER:verified_readback",
        "source": {
            **_store_price(source),
            "field_after_readback": "tiktok.regions[].price",
        },
        "derived_preview": {
            "price_cny": price_cny,
            "old_price_cny": round(price_cny * 1.3) if price_cny is not None else None,
            "source_currency": currency,
            "exchange_rate_cny_per_local": rate,
        },
        "write_fields": ["draft.price", "draft.old_price"],
        "formula": (
            "price_cny = round(tiktok_price * exchange_rate); "
            "old_price_cny = round(price_cny * 1.3)"
        ),
        "risk": "The adapter must select and verify the TikTok source region before import.",
    }


def _numeric_rates(values: Mapping[str, object]) -> dict[str, float]:
    return {
        str(key).upper(): number
        for key, value in values.items()
        if (number := _positive_number(value)) is not None
    }


def _positive_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
