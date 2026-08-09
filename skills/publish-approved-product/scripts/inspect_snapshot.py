#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any, Mapping

from _common import DEFAULT_BASE_URL, dashboard, emit, positive, utc_now


PLATFORM_PREFIXES = {
    "tiktok": "tiktok:",
    "shopee": "shopee:",
    "ozon": "ozon:",
}


def build_snapshot(payload: Mapping[str, Any], offer_id: str) -> dict[str, Any]:
    product = _mapping(payload.get("product"), "product")
    release = _mapping(payload.get("release_v1"), "release_v1")
    plan = _mapping(release.get("plan"), "approved release plan")
    if release.get("plan_approved") is not True:
        raise ValueError("release plan is not approved")
    canonical_offer = str(product.get("offer_id") or "").strip()
    if canonical_offer != str(offer_id).strip():
        raise ValueError("approved offer identity differs from the request")
    required_identity = (
        "plan_id",
        "payload_digest",
        "targets_digest",
        "confirmation_token",
    )
    if any(not plan.get(key) for key in required_identity):
        raise ValueError("approved release identity is incomplete")
    targets = [str(value) for value in plan.get("targets") or []]
    selected_keys = [str(value) for value in product.get("selected_sku_keys") or []]
    source_rows = {
        str(row.get("key")): row
        for row in product.get("source_skus") or []
        if isinstance(row, dict) and str(row.get("key") or "")
    }
    commercial = product.get("sku_commercial_facts")
    commercial = commercial if isinstance(commercial, dict) else {}
    skus: list[dict[str, Any]] = []
    for key in selected_keys:
        row = source_rows.get(key, {})
        per_sku = commercial.get(key)
        per_sku = per_sku if isinstance(per_sku, dict) else {}
        skus.append({
            "variant_key": key,
            "seller_sku": str(row.get("model_sku") or product.get("seller_sku_candidate") or "").strip(),
            "option_name": str(row.get("label") or row.get("name") or "").strip(),
            "cost_cny": per_sku.get("cost_cny", row.get("price_cny", product.get("cost_cny"))),
            "weight_kg": per_sku.get("weight_kg", product.get("weight_kg")),
            "package_cm": per_sku.get("package_cm", product.get("package_cm")),
            "source_price_cny": row.get("price_cny"),
        })
    listing = payload.get("listing_copy")
    listing = listing if isinstance(listing, dict) else {}
    content = payload.get("content")
    content = content if isinstance(content, dict) else {}
    images = [
        row.get("image_url")
        for row in sorted(
            (row for row in content.get("images") or [] if isinstance(row, dict)),
            key=lambda row: int(row.get("position") or 0),
        )
        if isinstance(row.get("image_url"), str) and row["image_url"].startswith("https://")
    ]
    pricing = payload.get("pricing_review")
    pricing = pricing if isinstance(pricing, dict) else {}
    available = payload.get("publication_scope")
    available = available.get("available_targets") if isinstance(available, dict) else []
    target_meta = {
        str(row.get("label")): row
        for row in available or []
        if isinstance(row, dict) and row.get("label")
    }
    store_price_by_key = {
        str(row.get("target_key")): row
        for row in pricing.get("selected_store_prices") or []
        if isinstance(row, dict) and row.get("target_key")
    }
    target_pricing = pricing.get("target_pricing")
    target_pricing = target_pricing if isinstance(target_pricing, dict) else {}
    prices: dict[str, Any] = {}
    for target in targets:
        meta = target_meta.get(target, {})
        price = store_price_by_key.get(str(meta.get("target_key") or ""))
        platform_price = _platform_prices(target, target_pricing.get(target))
        per_sku = platform_price.pop("sku_prices", {})
        if price or per_sku or platform_price:
            target_price = {
                "currency": price.get("currency"),
                "list_price": price.get("list_price"),
                "sale_after_discount": price.get("sale_after_discount"),
            } if price else {}
            target_price.update(platform_price)
            if per_sku:
                target_price["sku_prices"] = per_sku
                first = next(iter(per_sku.values()))
                target_price.setdefault("currency", first.get("currency"))
            prices[target] = target_price
    content_facts = {
        "title_master": listing.get("semantic_master_en") or product.get("title"),
        "platform_titles": [
            {
                "channel": row.get("channel"),
                "site": row.get("site"),
                "title": row.get("title"),
            }
            for row in listing.get("candidates") or []
            if isinstance(row, dict) and row.get("policy_check") == "passed"
        ],
        "description": listing.get("shopee_description_en"),
        "images": images,
        "video_urls": list(content.get("video_urls") or []),
        "source_category": product.get("category"),
    }
    platforms = {
        platform: _platform_plan(platform, targets, skus, content_facts, prices)
        for platform in PLATFORM_PREFIXES
    }
    request = {
        "offer_id": canonical_offer,
        "seller_sku": product.get("seller_sku_candidate"),
        "product_revision": product.get("revision"),
        "payload_digest": plan["payload_digest"],
        "targets_digest": plan["targets_digest"],
        "publication_targets": targets,
        "plan_id": plan["plan_id"],
        "confirmation_token": plan["confirmation_token"],
        "confirm_publish": True,
    }
    snapshot = {
        "schema_version": "approved-publication-snapshot/v3",
        "generated_at": utc_now(),
        "identity": {
            "offer_id": canonical_offer,
            "plan_id": plan["plan_id"],
            "product_revision": product.get("revision"),
            "payload_digest": plan["payload_digest"],
            "targets_digest": plan["targets_digest"],
        },
        "request": request,
        "skus": skus,
        "content": content_facts,
        "prices": prices,
        "platforms": platforms,
        "execution_plan": [
            {
                "platform": platform,
                "selected": row["selected"],
                "targets": row["targets"],
                "blocking_reasons": row["blocking_reasons"],
                "warnings": row["warnings"],
            }
            for platform, row in platforms.items()
            if row["selected"]
        ],
    }
    snapshot["snapshot_digest"] = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return snapshot


def _platform_plan(
    platform: str,
    targets: list[str],
    skus: list[dict[str, Any]],
    content: Mapping[str, Any],
    prices: Mapping[str, Any],
) -> dict[str, Any]:
    prefix = PLATFORM_PREFIXES[platform]
    selected_targets = [target for target in targets if target.startswith(prefix)]
    blockers: list[str] = []
    warnings: list[str] = []
    if selected_targets and not skus:
        blockers.append("no approved SKU is selected")
    for row in skus:
        if not row.get("seller_sku") or not row.get("option_name"):
            blockers.append("approved SKU identity or option name is missing")
            break
        package = row.get("package_cm")
        if not positive(row.get("cost_cny")) or not positive(row.get("weight_kg")):
            blockers.append("approved SKU cost or weight is not positive")
            break
        if not isinstance(package, list) or len(package) != 3 or not all(positive(value) for value in package):
            blockers.append("approved SKU package dimensions are incomplete")
            break
    if selected_targets and not content.get("images"):
        blockers.append("approved image list is empty")
    titles = [
        row for row in content.get("platform_titles") or []
        if str(row.get("channel") or "").lower() == platform
    ]
    if selected_targets and not titles:
        blockers.append(f"approved {platform} title is missing")
    if platform == "shopee" and selected_targets and not str(content.get("description") or "").strip():
        blockers.append("approved Shopee description is empty")
    if platform == "tiktok":
        missing_prices = [target for target in selected_targets if target not in prices]
        if missing_prices:
            blockers.append("approved TikTok store price is missing: " + ", ".join(missing_prices))
        approved_models = {str(row.get("seller_sku") or "").strip() for row in skus}
        if len(approved_models) > 1:
            incomplete = [
                target
                for target in selected_targets
                if set((prices.get(target) or {}).get("sku_prices") or {}) != approved_models
            ]
            if incomplete:
                blockers.append(
                    "approved TikTok per-SKU prices are incomplete: " + ", ".join(incomplete)
                )
    category = content.get("source_category")
    if selected_targets and (
        not isinstance(category, dict) or not str(category.get("name") or "").strip()
    ):
        warnings.append("source category is absent; platform candidate mapping must supply it")
    elif selected_targets and not str(category.get("id") or "").strip():
        warnings.append("source category has no platform ID; use the approved platform candidate")
    return {
        "selected": bool(selected_targets),
        "targets": selected_targets,
        "blocking_reasons": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} is missing")
    return value


def _platform_prices(target: str, value: object) -> dict[str, Any]:
    if target.startswith("tiktok:"):
        return {"sku_prices": _tiktok_sku_prices(value)}
    if not isinstance(value, dict):
        return {}
    preview = value.get("derived_preview")
    preview = preview if isinstance(preview, dict) else {}
    rows = value.get("sku_prices")
    rows = rows if isinstance(rows, list) else []
    result: dict[str, Any] = {}
    per_sku: dict[str, dict[str, Any]] = {}
    if target.startswith("shopee:"):
        if positive(preview.get("global_original_price_cny")):
            result["global_original_price_cny"] = preview[
                "global_original_price_cny"
            ]
        if positive(preview.get("local_original_price")):
            result["local_original_price"] = preview["local_original_price"]
        source_currency = str(preview.get("source_currency") or "").strip().upper()
        if source_currency:
            result["currency"] = source_currency
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("approved Shopee per-SKU price row is invalid")
            model_sku = str(row.get("model_sku") or "").strip()
            variant_key = str(row.get("variant_key") or "").strip()
            derived = row.get("derived_preview")
            derived = derived if isinstance(derived, dict) else {}
            amount = derived.get("global_original_price_cny")
            if not model_sku or not variant_key or not positive(amount):
                raise ValueError("approved Shopee per-SKU price identity is incomplete")
            if model_sku in per_sku:
                raise ValueError("approved Shopee model SKU price is duplicated")
            per_sku[model_sku] = {
                "variant_key": variant_key,
                "list_price": amount,
                **(
                    {"local_original_price": derived["local_original_price"]}
                    if positive(derived.get("local_original_price"))
                    else {}
                ),
                **(
                    {"currency": str(derived["source_currency"]).strip().upper()}
                    if str(derived.get("source_currency") or "").strip()
                    else {}
                ),
            }
    elif target == "ozon:RU":
        if positive(preview.get("price_cny")):
            result["price"] = preview["price_cny"]
        if positive(preview.get("old_price_cny")):
            result["old_price"] = preview["old_price_cny"]
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("approved Ozon per-SKU price row is invalid")
            model_sku = str(row.get("model_sku") or "").strip()
            variant_key = str(row.get("variant_key") or "").strip()
            derived = row.get("derived_preview")
            derived = derived if isinstance(derived, dict) else {}
            amount = derived.get("price_cny")
            old_amount = derived.get("old_price_cny")
            if (
                not model_sku
                or not variant_key
                or not positive(amount)
                or not positive(old_amount)
            ):
                raise ValueError("approved Ozon per-SKU price identity is incomplete")
            if model_sku in per_sku:
                raise ValueError("approved Ozon model SKU price is duplicated")
            per_sku[model_sku] = {
                "variant_key": variant_key,
                "price": amount,
                "old_price": old_amount,
            }
    if per_sku:
        result["sku_prices"] = per_sku
    return result


def _tiktok_sku_prices(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    rows = value.get("sku_prices")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("approved TikTok per-SKU price row is invalid")
        model_sku = str(row.get("model_sku") or "").strip()
        variant_key = str(row.get("variant_key") or row.get("source_key") or "").strip()
        if not model_sku or not variant_key or not positive(row.get("list_price")):
            raise ValueError("approved TikTok per-SKU price identity is incomplete")
        if model_sku in result:
            raise ValueError("approved TikTok model SKU price is duplicated")
        result[model_sku] = {
            "variant_key": variant_key,
            "currency": row.get("currency"),
            "list_price": row.get("list_price"),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read one approved Product Center snapshot without writing")
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--dashboard-fixture")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.dashboard_fixture:
            from _common import load_json
            source = load_json(args.dashboard_fixture)
        else:
            source = dashboard(args.base_url, args.offer_id, args.timeout_seconds)
        result = build_snapshot(source, args.offer_id)
        emit(result, args.output)
        return 0
    except Exception as error:
        result = {
            "schema_version": "approved-publication-snapshot/v3",
            "ok": False,
            "error": str(error),
        }
        emit(result, args.output)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
