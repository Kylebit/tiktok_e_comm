#!/usr/bin/env python3
"""Build a first-review publication packet with zero external writes.

Miaoshou synchronization belongs exclusively to the third-round publication skill.
Legacy execution flags remain parseable only so older callers receive a clear,
deterministic failure instead of silently crossing the first-round boundary.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
if __name__ == "__main__" and (not (REPO_ROOT / '.git').exists() or not all((REPO_ROOT / name).is_file() for name in (
    "core/config.py", "modules/sourcing/new_product_workbench.py",
    "shared_platform/publication_rounds.py",
))):
    raise SystemExit("COMPLETE_AGENT_SOURCE_REQUIRED: use the selected repository's scripts/repo_bound_agent_entry.py --profile <absolute-profile> --entry preparation")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared_platform.publication_stock_policy import default_publication_stock_policy


class PreparationError(RuntimeError):
    """A deterministic preparation or write-boundary failure."""


_INTERNAL_AUDIT_COPY_RE = re.compile(
    r"(?:based\s+only\s+on\s+(?:the\s+)?supplied|supplied\s+product\s+facts|"
    r"not\s+confirmed|must\s+not\s+be\s+inferred|remain(?:s)?\s+unconfirmed|"
    r"不得(?:自动)?(?:补充|推断)|尚未确认)",
    re.IGNORECASE,
)


PreviewBuilder = Callable[[str], dict[str, Any]]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _unique_text(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(value for raw in values if (value := _clean_text(raw))))


def _selection_key(value: Any) -> str:
    raw = _clean_text(value).lower().replace("-", "_")
    aliases = {
        "miaoshou:common": "miaoshou_common",
        "ozon:ru": "ozon_ru",
        "shopee:ph": "shopee_ph",
        "shopee:my": "shopee_my",
        "shopee:th": "shopee_th",
        "shopee:vn": "shopee_vn",
        "tiktok:mx": "mx",
        "tiktok:gb": "gb",
    }
    if raw in aliases:
        return aliases[raw]
    if raw.startswith("tiktok:"):
        return raw.split(":", 1)[1]
    return raw.replace(":", "_")


def _safe_skus(source: dict[str, Any], review: dict[str, Any]) -> list[dict[str, Any]]:
    selected = set(_unique_text(review.get("selected_sku_keys") or []))
    labels = review.get("sku_label_overrides") if isinstance(review.get("sku_label_overrides"), dict) else {}
    rows: list[dict[str, Any]] = []
    for raw in source.get("skus") or []:
        if not isinstance(raw, dict):
            continue
        source_key = _clean_text(raw.get("key") or raw.get("name"))
        if selected and source_key not in selected:
            continue
        rows.append(
            {
                "source_key": source_key,
                "seller_sku": _clean_text(raw.get("seller_sku") or raw.get("item_num")),
                "approved_display_name": _clean_text(labels.get(source_key)),
                "cost_cny": raw.get("cost_cny"),
                "weight_kg": raw.get("weight_kg"),
                "package_cm": list(raw.get("package_cm") or []),
            }
        )
    return rows


def _safe_dashboard_skus(product: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in product.get("source_skus") or []:
        if not isinstance(raw, dict):
            continue
        commercial = raw.get("commercial_facts") if isinstance(raw.get("commercial_facts"), dict) else {}
        rows.append(
            {
                "source_key": _clean_text(raw.get("key")),
                "seller_sku": _clean_text(raw.get("model_sku")),
                "approved_display_name": _clean_text(raw.get("label") or raw.get("name")),
                "cost_cny": commercial.get("cost_cny"),
                "weight_kg": commercial.get("weight_kg"),
                "package_cm": list(commercial.get("package_cm") or []),
            }
        )
    return rows


def _category_semantic(value: Any) -> str:
    name = value.get("name") if isinstance(value, dict) else value
    return name.strip() if isinstance(name, str) else ""


def _safe_product_facts(preview: dict[str, Any]) -> dict[str, Any]:
    product = preview.get("product") if isinstance(preview.get("product"), dict) else {}
    if product:
        return _annotate_parcel_facts({
            "title": _clean_text(product.get("title")),
            "seller_sku": _clean_text(product.get("seller_sku_candidate")),
            "category_semantic": _category_semantic(product.get("category")),
            "cost_cny": product.get("cost_cny"),
            "weight_kg": product.get("weight_kg"),
            "package_cm": list(product.get("package_cm") or []),
            "skus": _safe_dashboard_skus(product),
            "source_image_count": len(((preview.get("content") or {}).get("images") or [])),
        })
    source = preview.get("source") if isinstance(preview.get("source"), dict) else {}
    review = preview.get("review") if isinstance(preview.get("review"), dict) else {}
    return _annotate_parcel_facts({
        "title": _clean_text(review.get("title") or source.get("title_recommended") or source.get("title_source")),
        "seller_sku": _clean_text(review.get("seller_sku") or source.get("seller_sku")),
        "category_semantic": _clean_text(review.get("category") or source.get("category")),
        "cost_cny": review.get("cost_cny", source.get("cost_cny")),
        "weight_kg": review.get("weight_kg", source.get("weight_kg")),
        "package_cm": list(review.get("package_cm") or source.get("package_cm") or []),
        "skus": _safe_skus(source, review),
        "source_image_count": len(source.get("images") or []),
    })


def _derived_price_pair(derived: dict[str, Any], source: dict[str, Any]) -> tuple[Any, Any]:
    currency = derived.get('currency') or derived.get('source_currency') or source.get('currency')
    for key in ('local_original_price', 'price_cny', 'amount', 'price'):
        if derived.get(key) is not None:
            return derived[key], 'CNY' if key == 'price_cny' else currency
    return None, currency


def _pricing_rows(preview: dict[str, Any]) -> list[dict[str, Any]]:
    modern = preview.get("pricing_review") if isinstance(preview.get("pricing_review"), dict) else {}
    target_pricing = modern.get("target_pricing") if isinstance(modern.get("target_pricing"), dict) else {}
    if target_pricing:
        rows: list[dict[str, Any]] = []
        for target, raw in target_pricing.items():
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row["id"] = _clean_text(target)
            store_prices = [
                dict(item)
                for item in (row.get("store_prices") or [])
                if isinstance(item, dict)
            ]
            derived = row.get("derived_preview") if isinstance(row.get("derived_preview"), dict) else {}
            source = row.get("source") if isinstance(row.get("source"), dict) else {}
            if 'list_price' not in row:
                if len(store_prices) == 1 and 'list_price' in store_prices[0]:
                    row['list_price'], row['currency'] = store_prices[0].get('list_price'), store_prices[0].get('currency')
                else:
                    row['list_price'], row['currency'] = _derived_price_pair(derived, source)
            rows.append(row)
        return rows
    selected_store_prices = modern.get("selected_store_prices")
    if isinstance(selected_store_prices, list) and selected_store_prices:
        rows = []
        for raw in selected_store_prices:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row["id"] = _clean_text(row.get("target_key") or row.get("id"))
            rows.append(row)
        return rows
    pricing = preview.get("pricing") if isinstance(preview.get("pricing"), dict) else {}
    rows: list[dict[str, Any]] = []
    for raw in pricing.get("sea") or []:
        if isinstance(raw, dict):
            rows.append(raw)
    for key in ("mx", "uk", "ozon"):
        raw = pricing.get(key)
        if isinstance(raw, dict) and raw:
            row = dict(raw)
            row.setdefault("id", key)
            rows.append(row)
    return rows


def _safe_target_facts(preview: dict[str, Any], requested_targets: list[str]) -> list[dict[str, Any]]:
    prices = {_selection_key(row.get("id") or row.get("target_id") or row.get("region")): row for row in _pricing_rows(preview)}
    facts: list[dict[str, Any]] = []
    for requested in requested_targets:
        key = _selection_key(requested)
        price = prices.get(key) or {}
        price_fact: dict[str, Any] = {
            "amount": price.get("list_price"),
            "currency": price.get("currency"),
            "source": "product_center_rule_pricing" if price else None,
        }
        for field in ("status", "role"):
            if price.get(field) is not None:
                price_fact[field] = price.get(field)
        calculation = _safe_price_calculation(price)
        if calculation is not None:
            price_fact["calculation"] = calculation
        safe_sku_prices: list[dict[str, Any]] = []
        for raw_sku_price in price.get("sku_prices") or []:
            if not isinstance(raw_sku_price, dict):
                continue
            source = raw_sku_price.get("source") if isinstance(raw_sku_price.get("source"), dict) else {}
            derived = raw_sku_price.get("derived_preview") if isinstance(raw_sku_price.get("derived_preview"), dict) else {}
            derived_amount, derived_currency = _derived_price_pair(derived, source)
            if 'list_price' in raw_sku_price:
                amount, currency = raw_sku_price.get('list_price'), raw_sku_price.get('currency')
            elif derived_amount is not None:
                amount, currency = derived_amount, derived_currency
            else:
                amount, currency = source.get('list_price'), source.get('currency')
            variant_price = {
                    "model_sku": _clean_text(raw_sku_price.get("model_sku")),
                    "display_name": _review_specification_value(raw_sku_price.get("label")),
                    "amount": amount,
                    "currency": currency,
                    "old_price": derived.get("old_price_cny"),
                    "status": _clean_text(raw_sku_price.get("status")),
                }
            if _clean_text(price.get("role")) == "master_listing":
                variant_calculation = _safe_price_calculation({**price, "store_prices": [raw_sku_price]})
            else:
                variant_calculation = _safe_price_calculation({
                    **price,
                    "derived_preview": derived,
                    "store_prices": [],
                })
            if variant_calculation is not None:
                variant_price["calculation"] = variant_calculation
            safe_sku_prices.append(variant_price)
        if safe_sku_prices:
            price_fact["sku_prices"] = safe_sku_prices
        facts.append(
            {
                "target": requested,
                "selection_key": key,
                "category": {"status": "AGENT_RESOLUTION_REQUIRED", "candidate": None},
                "price": price_fact,
                "copy": {"status": "AGENT_GENERATION_REQUIRED"},
            }
        )
    return facts


def _collect_blockers(preview: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    workflow = preview.get("workflow") if isinstance(preview.get("workflow"), dict) else {}
    facts = preview.get("product_facts") if isinstance(preview.get("product_facts"), dict) else {}
    blockers.extend(_unique_text(workflow.get("blockers") or []))
    blockers.extend(_unique_text(facts.get("blockers") or []))
    product = preview.get("product") if isinstance(preview.get("product"), dict) else {}
    fact_evidence = product.get("fact_evidence") if isinstance(product.get("fact_evidence"), dict) else {}
    blockers.extend(_unique_text(fact_evidence.get("blockers") or []))
    if preview.get("ok") is not True:
        blockers.append("Product Center preview is not ready")
    return _unique_text(blockers)


def _safe_image_execution_plan(value: Any) -> dict[str, Any]:
    """Validate one agent-proposed, user-reviewable image plan.

    The packet deliberately contains positions and language routes only.  It
    must not persist provider URLs, generation payloads or hidden OCR output.
    """
    if value is None:
        return {
            "schema_version": "first-review-image-plan/v1",
            "status": "USER_DECISION_REQUIRED",
            "source_actions": [],
            "generated_assets": [],
            "brand_plans": [],
            "summary": {
                "translation_positions": [],
                "localized_output_count": 0,
                "net_new_output_count": 0,
                "paid_generation_required": False,
            },
        }
    if not isinstance(value, dict):
        raise PreparationError("image execution plan must be an object")
    if value.get("schema_version") != "first-review-image-plan/v1":
        raise PreparationError("image execution plan schema is invalid")
    if value.get("status") not in {"PROPOSED", "APPROVED", "SKIPPED"}:
        raise PreparationError("image execution plan status is invalid")
    source_actions = value.get("source_actions")
    generated_assets = value.get("generated_assets")
    brand_plans = value.get("brand_plans") or []
    translation_plan = value.get("translation_plan")
    summary = value.get("summary")
    if not isinstance(source_actions, list) or len(source_actions) > 50:
        raise PreparationError("image source actions must be a bounded list")
    if not isinstance(generated_assets, list) or len(generated_assets) > 20:
        raise PreparationError("generated image assets must be a bounded list")
    if not isinstance(brand_plans, list) or len(brand_plans) > 4:
        raise PreparationError("brand image plans must be a bounded list")
    if not isinstance(summary, dict):
        raise PreparationError("image execution plan summary is required")
    clean_translation_plan: dict[str, str] | None = None
    if translation_plan is not None:
        if not isinstance(translation_plan, dict) or set(translation_plan) != {
            "status", "decision_basis", "note"
        }:
            raise PreparationError("translation plan deferral shape is invalid")
        if (
            _clean_text(translation_plan.get("status")) != "DEFERRED_UNTIL_ALL_IMAGES_GENERATED"
            or _clean_text(translation_plan.get("decision_basis")) != "REVIEW_ALL_GENERATED_IMAGES_FIRST"
            or not _clean_text(translation_plan.get("note"))
        ):
            raise PreparationError("translation plan deferral is invalid")
        clean_translation_plan = {
            "status": "DEFERRED_UNTIL_ALL_IMAGES_GENERATED",
            "decision_basis": "REVIEW_ALL_GENERATED_IMAGES_FIRST",
            "note": _clean_text(translation_plan.get("note"))[:400],
        }
    if brand_plans and clean_translation_plan is None:
        raise PreparationError("dual-brand images require translation deferral until all generated images are reviewed")
    seen_positions: set[int] = set()
    clean_actions: list[dict[str, Any]] = []
    for raw in source_actions:
        if not isinstance(raw, dict):
            raise PreparationError("image source action must be an object")
        position = raw.get("position")
        action = _clean_text(raw.get("action")).upper()
        languages = _unique_text(raw.get("target_languages") or [])
        output_count = raw.get("output_count")
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or not 1 <= position <= 50
            or position in seen_positions
        ):
            raise PreparationError("image source positions must be unique positive integers")
        if action not in {"KEEP", "TRANSLATE", "REMOVE", "REFERENCE"}:
            raise PreparationError("image source action is invalid")
        if isinstance(output_count, bool) or not isinstance(output_count, int) or output_count < 0:
            raise PreparationError("image output_count must be a non-negative integer")
        if action == "TRANSLATE" and output_count != len(languages):
            raise PreparationError("translated output_count must equal target language count")
        seen_positions.add(position)
        clean_actions.append(
            {
                "position": position,
                "action": action,
                "original_language": _clean_text(raw.get("original_language")),
                "target_languages": languages,
                "output_count": output_count,
                "reason": _clean_text(raw.get("reason"))[:240],
            }
        )
    clean_summary = {
        "translation_positions": [
            int(position) for position in (summary.get("translation_positions") or [])
        ],
        "localized_output_count": int(summary.get("localized_output_count") or 0),
        "net_new_output_count": int(summary.get("net_new_output_count") or 0),
        "paid_generation_required": summary.get("paid_generation_required") is True,
    }
    clean_brand_plans: list[dict[str, Any]] = []
    seen_brand_ids: set[str] = set()
    for raw in brand_plans:
        if not isinstance(raw, dict) or set(raw) != {
            "id", "label", "target_group", "generation_mode", "positioning",
            "reference_positions", "generated_assets", "category_guidance",
        }:
            raise PreparationError("brand image plan shape is invalid")
        brand_id = _clean_text(raw.get("id"))
        positions = raw.get("reference_positions")
        planned_assets = raw.get("generated_assets")
        guidance = raw.get("category_guidance")
        if (
            not brand_id
            or brand_id in seen_brand_ids
            or not _clean_text(raw.get("label"))
            or not _clean_text(raw.get("target_group"))
            or not _clean_text(raw.get("positioning"))
            or not isinstance(positions, list)
            or not positions
            or len(positions) > 50
            or any(type(position) is not int or not 1 <= position <= 50 for position in positions)
            or len(set(positions)) != len(positions)
            or _clean_text(raw.get("generation_mode")) not in {
                "NEW_SET_VIA_IMAGE_API",
                "REUSE_APPROVED_AND_GENERATE_GAPS",
            }
            or not isinstance(planned_assets, list)
            or not planned_assets
            or len(planned_assets) > 20
            or not isinstance(guidance, list)
            or not guidance
            or len(guidance) > 8
        ):
            raise PreparationError("brand image plan evidence is invalid")
        clean_assets: list[dict[str, Any]] = []
        for asset in planned_assets:
            if not isinstance(asset, dict) or set(asset) != {"role", "quantity", "brief"}:
                raise PreparationError("brand generated asset shape is invalid")
            quantity = asset.get("quantity")
            if type(quantity) is not int or not 1 <= quantity <= 20 or not _clean_text(asset.get("role")) or not _clean_text(asset.get("brief")):
                raise PreparationError("brand generated asset is invalid")
            clean_assets.append({
                "role": _clean_text(asset.get("role"))[:80],
                "quantity": quantity,
                "brief": _clean_text(asset.get("brief"))[:600],
            })
        sticker_category_ids = {"600338", "101157", "17028954 / type 95819"}
        has_sticker_category = any(
            _clean_text(item.get("category_id")) in sticker_category_ids
            for item in guidance if isinstance(item, dict)
        )
        scene_quantity = sum(
            asset["quantity"] for asset in clean_assets
            if _is_scene_role(asset["role"])
        )
        if has_sticker_category and scene_quantity < 3:
            raise PreparationError("sticker category image plan requires at least three scene images per brand")
        clean_guidance: list[dict[str, str]] = []
        seen_guidance_platforms: set[str] = set()
        for item in guidance:
            if not isinstance(item, dict) or set(item) != {
                "platform", "category_id", "category_zh", "recommendation"
            }:
                raise PreparationError("category image guidance shape is invalid")
            platform = _clean_text(item.get("platform")).lower()
            if (
                platform not in {"tiktok", "shopee", "ozon"}
                or platform in seen_guidance_platforms
                or not _clean_text(item.get("category_id"))
                or not _clean_text(item.get("category_zh"))
                or not _clean_text(item.get("recommendation"))
            ):
                raise PreparationError("category image guidance is invalid")
            seen_guidance_platforms.add(platform)
            clean_guidance.append({
                "platform": platform,
                "category_id": _clean_text(item.get("category_id"))[:80],
                "category_zh": _clean_text(item.get("category_zh"))[:320],
                "recommendation": _clean_text(item.get("recommendation"))[:600],
            })
        seen_brand_ids.add(brand_id)
        clean_brand_plans.append({
            "id": brand_id[:80],
            "label": _clean_text(raw.get("label"))[:120],
            "target_group": _clean_text(raw.get("target_group"))[:80],
            "generation_mode": _clean_text(raw.get("generation_mode")),
            "positioning": _clean_text(raw.get("positioning"))[:400],
            "reference_positions": list(positions),
            "generated_assets": clean_assets,
            "category_guidance": clean_guidance,
        })
    clean_plan = {
        "schema_version": "first-review-image-plan/v1",
        "status": value["status"],
        **({"translation_plan": clean_translation_plan} if clean_translation_plan is not None else {}),
        "source_actions": clean_actions,
        "generated_assets": [dict(row) for row in generated_assets if isinstance(row, dict)],
        **({"brand_plans": clean_brand_plans} if "brand_plans" in value else {}),
        "summary": clean_summary,
    }
    if clean_plan != value:
        raise PreparationError("image execution plan contains unsupported or inconsistent fields")
    return clean_plan


def _build_release_dashboard_with_bootstrap(
    offer_id: str,
    *,
    dashboard_builder: Callable[..., dict[str, Any]] | None = None,
    bootstrapper: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Create missing local workbench state through the existing read-only import."""
    if dashboard_builder is None:
        from shared_platform.release_control import build_release_dashboard

        dashboard_builder = build_release_dashboard
    if bootstrapper is None:
        from modules.products.server import _collect_product_workspace_locally

        def collect(offer: str) -> None:
            status, result = _collect_product_workspace_locally({"offer_id": offer})
            if status not in {200, 201} or result.get("ok") is not True:
                raise PreparationError(
                    _clean_text(result.get("error") or "Product Center collection failed")
                )

        bootstrapper = collect
    try:
        return dashboard_builder(offer_id=offer_id)
    except FileNotFoundError:
        bootstrapper(offer_id)
        return dashboard_builder(offer_id=offer_id)


def _default_preview_builder() -> PreviewBuilder:
    def dashboard(offer_id: str) -> dict[str, Any]:
        return _build_release_dashboard_with_bootstrap(offer_id)

    return dashboard


def prepare_offer(
    *,
    offer_id: str,
    requested_targets: list[str],
    execute_miaoshou: bool = False,
    confirm_miaoshou_write: bool = False,
    skip_miaoshou: bool = False,
    image_execution_plan: dict[str, Any] | None = None,
    candidate_plan: dict[str, Any] | None = None,
    preview_builder: PreviewBuilder | None = None,
    category_receipt: dict[str, Any] | None = None,
    category_source_region: str | None = None,
    category_observation_resolver: Callable | None = None,
    category_observation: str | None = None,
    category_account_digest: str | None = None,
) -> dict[str, Any]:
    """Return the safe first-review packet without provider mutations."""
    clean_offer_id = _clean_text(offer_id)
    targets = _unique_text(requested_targets)
    if not clean_offer_id:
        raise PreparationError("offer_id is required")
    if not targets:
        raise PreparationError("at least one target store is required")
    if execute_miaoshou or confirm_miaoshou_write:
        raise PreparationError("Miaoshou synchronization belongs to the third round")

    preview_builder = preview_builder or _default_preview_builder()

    preview = preview_builder(clean_offer_id)
    if not isinstance(preview, dict):
        raise PreparationError("Product Center preview returned an invalid shape")

    review = preview.get("review") if isinstance(preview.get("review"), dict) else {}
    publication_scope = preview.get("publication_scope") if isinstance(preview.get("publication_scope"), dict) else {}
    selected_sites = _unique_text(publication_scope.get("selected_labels") or review.get("selected_sites") or [])
    selected_keys = {_selection_key(value) for value in selected_sites}
    missing_targets = [target for target in targets if _selection_key(target) not in selected_keys]
    blockers = _collect_blockers(preview)
    if missing_targets:
        blockers.append(
            "requested targets are not selected in Product Center: "
            + ", ".join(missing_targets)
        )

    blockers = _unique_text(blockers)
    # Preserve legacy normalization outside Shopee; bound evidence needs the actual revision.
    raw_revision = (preview['product'].get('revision') if isinstance(preview.get('product'), dict)
                    and 'revision' in preview['product'] else preview.get('revision'))
    has_shopee = any(target.lower().startswith(('shopee:', 'shopee_')) for target in targets)
    packet_revision = (raw_revision if has_shopee else
                       max(0, int((((preview.get('product') or {}).get('revision'))
                                  if isinstance(preview.get('product'), dict) else None)
                                 or preview.get('revision') or 0)))
    safe_image_plan = _safe_image_execution_plan(image_execution_plan)
    safe_candidate_plan = _safe_candidate_plan(candidate_plan, targets)
    translation_positions = list(
        safe_image_plan["summary"].get("translation_positions") or []
    )
    packet: dict[str, Any] = {
        "schema": "publication-preparation-decision/v1",
        "offer_id": clean_offer_id,
        "product_center_revision": packet_revision,
        "publication_stock_policy": default_publication_stock_policy(),
        "status": (
            "DECISION_REQUIRED"
            if blockers
            else "FIRST_REVIEW_READY"
        ),
        "target_selection": {
            "requested": targets,
            "selected_in_product_center": selected_sites,
            "missing_from_product_center": missing_targets,
        },
        "product_facts": _safe_product_facts(preview),
        "targets": _apply_candidate_plan(_safe_target_facts(preview, targets), safe_candidate_plan),
        "platform_categories": safe_candidate_plan.get("platform_categories", []),
        "copy_review_sets": safe_candidate_plan.get("copy_review_sets", []),
        "image_decisions": {
            "translation_status": (
                "PROPOSED_FOR_USER_REVIEW"
                if safe_image_plan["status"] == "PROPOSED"
                else safe_image_plan["status"]
            ),
            "translation_positions": translation_positions,
            "note": "Complete and review final brand images before selecting localization scope.",
        },
        "image_execution_plan": safe_image_plan,
        "content_groups": _content_group_projection(targets, safe_candidate_plan['content_group_options']),
        "blockers": blockers,
        "miaoshou_sync": {
            "status": "DEFERRED_TO_THIRD_ROUND",
            "written_to_miaoshou": False,
            "verified": False,
            "claimed": False,
            "published": False,
            "change_summary": {},
        },
        "external_write_count": 0,
        "request_attempted": False,
        "readback_verified": False,
    }

    from shared_platform.round1_category_evidence import (
        CategoryEvidenceError, SCOPE, shopee_targets, validate_receipt,
    )
    try:
        if shopee_targets(packet):
            packet['category_review_context'] = {'source_region': category_source_region, 'observation_scope': SCOPE}
            if category_observation is not None:
                if category_receipt is not None or category_observation_resolver is not None:
                    raise CategoryEvidenceError('CATEGORY_REFERENCE_INPUT_CONFLICT')
                from shared_platform.round1_category_observations import default_resolver, build_receipt
                category_observation_resolver = default_resolver(packet, category_account_digest)
                category_receipt = build_receipt(category_observation_resolver(category_observation))
            if category_receipt is None:
                raise CategoryEvidenceError('CATEGORY_RECEIPT_UNAVAILABLE')
            bound = validate_receipt(packet, category_receipt, observation_resolver=category_observation_resolver)
            packet['category_evidence_binding'] = {'status': 'BOUND', 'receipt': bound}
            for row in packet['targets']:
                if shopee_targets({'target_selection': {'requested': [row['target']]}}):
                    row['category'] = {'status': 'EVIDENCE_BOUND', 'id': bound['category']['id'],
                                       'name': bound['category']['name'], 'receipt_digest': bound['receipt_digest']}
    except CategoryEvidenceError as error:
        packet['category_evidence_binding'] = {'status': 'SOURCE_UNVERIFIED' if str(error) == 'SOURCE_UNVERIFIED' else 'DECISION_REQUIRED',
                                               'code': str(error)}
        packet['blockers'].append(str(error))
        packet['status'] = 'DECISION_REQUIRED'
    packet['blockers'] = _unique_text(packet['blockers'] + _material_review_blockers(
        product_facts=packet['product_facts'], targets=packet['targets'],
        image_plan=safe_image_plan, requested_targets=targets,
        content_groups=packet['content_groups']))
    if packet['blockers']:
        packet['status'] = 'DECISION_REQUIRED'
    return packet


def _parse_targets(raw: str) -> list[str]:
    return _unique_text(raw.split(","))


def _default_output_path(offer_id: str) -> Path:
    return (
        REPO_ROOT
        / "reports"
        / "product-preparation"
        / _clean_text(offer_id)
        / "first-review.json"
    )


def _write_text_atomic(path: Path, text_value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    # An existing temporary symlink/hardlink is never an output target.
    claimed=False
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            claimed=True
            stream.write(text_value)
        temporary.replace(path)
    finally:
        if claimed and temporary.exists():temporary.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--targets", required=True, help="Comma-separated exact target labels")
    parser.add_argument("--output", type=Path)
    parser.add_argument('--candidate-plan', type=Path, help='Bounded proposed copy/category sidecar; never official authority')
    parser.add_argument('--category-review', type=Path, help='Untrusted local category reference; never establishes source authority')
    parser.add_argument('--category-source-region', choices=['PH', 'MY', 'TH', 'VN'])
    parser.add_argument('--category-observation', help='Reference to an internally captured observation; never starts capture')
    parser.add_argument('--category-account-digest', help='Exact intended source account identity digest')
    parser.add_argument("--execute-miaoshou", action="store_true", help="Legacy flag; rejected because first round is zero-write")
    parser.add_argument("--confirm-miaoshou-write", action="store_true", help="Legacy flag; rejected because first round is zero-write")
    parser.add_argument("--skip-miaoshou", action="store_true", help="Legacy no-op; first round always defers Miaoshou")
    parser.add_argument(
        "--image-plan",
        type=Path,
        help="Validated first-review image plan JSON; performs no image generation.",
    )
    return parser


def _read_category_reference(path: Path | None, offer_id: str, targets: list[str]):
    from shared_platform.round1_category_evidence import shopee_targets, read_receipt, CategoryEvidenceError
    if not shopee_targets({'target_selection': {'requested': targets}}):
        return None
    if not offer_id.isdigit():
        raise PreparationError('offer_id must contain digits for category evidence')
    source = path or REPO_ROOT / 'reports' / 'product-preparation' / offer_id / 'shopee-category-review.json'
    if path is None and not source.is_file():
        return None
    try:
        return read_receipt(source)
    except CategoryEvidenceError:
        return {'schema_version': 'invalid-category-reference'}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if re.fullmatch(r"[0-9]+", args.offer_id) is None:
            raise PreparationError("offer_id must contain ASCII digits only")
        standard = _default_output_path(args.offer_id).parent
        image_path = args.image_plan or (standard/'first-review-image-plan.json' if (standard/'first-review-image-plan.json').is_file() else None)
        candidate_path = args.candidate_plan or (standard/'first-review-candidate-plan.json' if (standard/'first-review-candidate-plan.json').is_file() else None)
        image_plan = (
            json.loads(image_path.read_text(encoding="utf-8"))
            if image_path
            else None
        )
        packet = prepare_offer(
            offer_id=args.offer_id,
            requested_targets=_parse_targets(args.targets),
            execute_miaoshou=args.execute_miaoshou,
            confirm_miaoshou_write=args.confirm_miaoshou_write,
            skip_miaoshou=args.skip_miaoshou,
            image_execution_plan=image_plan,
            candidate_plan=json.loads(candidate_path.read_text(encoding='utf-8')) if candidate_path else None,
            category_receipt=(_read_category_reference(args.category_review, args.offer_id, _parse_targets(args.targets))
                              if not args.category_observation or args.category_review else None),
            category_source_region=args.category_source_region,
            category_observation=args.category_observation,
            category_account_digest=args.category_account_digest,
        )
    except Exception as exc:
        error = {
            "schema": "publication-preparation-error/v1",
            "status": "FAILED",
            "kind": type(exc).__name__,
            "reason": _clean_text(exc)[:240],
            "external_write_count": 0,
        }
        print(json.dumps(error, ensure_ascii=True, indent=2))
        return 2

    rendered = json.dumps(packet, ensure_ascii=False, indent=2)
    output = args.output or _default_output_path(args.offer_id)
    _write_text_atomic(output, rendered + "\n")
    print(rendered)
    return 0




def _reject_internal_audit_copy(*values: Any) -> None:
    if any(_INTERNAL_AUDIT_COPY_RE.search(_clean_text(value)) for value in values):
        raise PreparationError(
            "consumer copy must omit unsupported claims, not expose internal audit instructions"
        )


def _target_brand_family(target: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", _clean_text(target).upper()).strip("_")
    parts = set(normalized.split("_"))
    if "LIVELYHIVE" in normalized or "LH" in parts:
        return "LIVELYHIVE"
    if "HOMEBLOOM" in normalized or "HB" in parts:
        return "HOMEBLOOM"
    return ""


def _is_sea_target(target: str) -> bool:
    normalized = re.sub(r"[^A-Z0-9]+", "_", _clean_text(target).upper()).strip("_")
    return bool({"PH", "MY", "TH", "VN"}.intersection(normalized.split("_")))


def _requires_dual_sea_content(requested_targets: list[str]) -> bool:
    brands = {
        _target_brand_family(target)
        for target in requested_targets
        if _is_sea_target(target)
    }
    return {"LIVELYHIVE", "HOMEBLOOM"}.issubset(brands)


def _content_group_projection(
    requested_targets: list[str], candidate_options: list[dict[str, Any]]
) -> dict[str, Any]:
    if not _requires_dual_sea_content(requested_targets):
        selected = [row for row in candidate_options if row.get("recommended") is True]
        group_targets = {
            "livelyhive-sea": {
                "lh_my", "lh_th", "lh_vn", "lh_ph", "mx", "gb",
                "shopee_my", "shopee_th", "shopee_vn", "shopee_ph", "ozon_ru",
            },
            "homebloom-sea": {"hb_my", "hb_th", "hb_vn", "hb_ph"},
        }
        target_keys = {_selection_key(target) for target in requested_targets}
        if (len(selected) == 1 and target_keys
                and target_keys <= group_targets.get(selected[0]["id"], set())):
            return {
                "status": "SELECTED_BY_EXPLICIT_TARGET_SCOPE",
                "groups": [
                    {
                        "id": selected[0]["id"],
                        "label": selected[0]["label"],
                        "targets": list(requested_targets),
                    }
                ],
                "options": candidate_options,
                "note": "The sole recommended group matches the explicit single-brand target scope.",
            }
        return {
            "status": "USER_DECISION_REQUIRED",
            "groups": [],
            "options": candidate_options,
            "note": "The user selects the content-group scope during first review.",
        }
    livelyhive_targets = []
    for target in requested_targets:
        normalized = re.sub(r"[^A-Z0-9]+", "_", _clean_text(target).upper()).strip("_")
        platform = normalized.split("_")[0] if normalized else ""
        follows_livelyhive = (
            _target_brand_family(target) == "LIVELYHIVE"
            or platform in {"SHOPEE", "OZON"}
            or bool({"MX", "GB", "UK"}.intersection(normalized.split("_")))
        )
        if follows_livelyhive:
            livelyhive_targets.append(target)
    homebloom_targets = [
        target
        for target in requested_targets
        if _is_sea_target(target) and _target_brand_family(target) == "HOMEBLOOM"
    ]
    return {
        "status": "REQUIRED_BY_TARGET_SCOPE",
        "groups": [
            {
                "id": "livelyhive-sea",
                "label": "LivelyHive SEA",
                "targets": livelyhive_targets,
            },
            {
                "id": "homebloom-sea",
                "label": "HomeBloom SEA",
                "targets": homebloom_targets,
            },
        ],
        "options": [
            {
                "id": "split-by-brand",
                "label": "LivelyHive / HomeBloom 两套内容",
                "description": "双品牌 SEA 店铺同时发布时，图片和文案必须拆成两套；Shopee、MX、英国和 Ozon 跟随 LivelyHive。",
                "recommended": True,
            }
        ],
        "note": "双品牌 SEA 同发自动锁定两套内容；HomeBloom SEA 独立，LivelyHive SEA、Shopee、MX、英国和 Ozon 共用 LivelyHive 方案。",
    }


def _safe_candidate_plan(value: Any, requested_targets: list[str]) -> dict[str, Any]:
    """Validate reviewable category/copy/content candidates without approvals."""
    if value is None:
        return {
            "schema_version": "first-review-candidate-plan/v1",
            "target_candidates": [],
            "platform_categories": [],
            "copy_review_sets": [],
            "content_group_options": [],
        }
    if not isinstance(value, dict) or set(value) not in ({
        "schema_version",
        "target_candidates",
        "content_group_options",
    }, {
        "schema_version",
        "target_candidates",
        "platform_categories",
        "copy_review_sets",
        "content_group_options",
    }):
        raise PreparationError("first-review candidate plan shape is invalid")
    if value.get("schema_version") != "first-review-candidate-plan/v1":
        raise PreparationError("first-review candidate plan schema is invalid")
    raw_targets = value.get("target_candidates")
    raw_platform_categories = value.get("platform_categories") or []
    raw_copy_review_sets = value.get("copy_review_sets") or []
    raw_groups = value.get("content_group_options")
    if not isinstance(raw_targets, list) or len(raw_targets) > len(requested_targets):
        raise PreparationError("candidate target rows are invalid")
    if not isinstance(raw_groups, list) or len(raw_groups) > 4:
        raise PreparationError("content-group candidate rows are invalid")
    if not isinstance(raw_platform_categories, list) or len(raw_platform_categories) > 8:
        raise PreparationError("platform category rows are invalid")
    if not isinstance(raw_copy_review_sets, list) or len(raw_copy_review_sets) > 4:
        raise PreparationError("copy review sets are invalid")

    requested = set(requested_targets)
    seen: set[str] = set()
    clean_targets: list[dict[str, Any]] = []
    for row in raw_targets:
        if not isinstance(row, dict) or set(row) != {"target", "category", "copy"}:
            raise PreparationError("candidate target row shape is invalid")
        target = _clean_text(row.get("target"))
        if target not in requested or target in seen:
            raise PreparationError("candidate target identity is invalid")
        category = row.get("category")
        copy = row.get("copy")
        if not isinstance(category, dict) or set(category) != {
            "status", "candidate", "authority", "evidence_digest", "note"
        }:
            raise PreparationError("candidate category shape is invalid")
        category_status = _clean_text(category.get("status")).upper()
        candidate = _clean_text(category.get("candidate"))
        authority = _clean_text(category.get("authority"))
        digest = _clean_text(category.get("evidence_digest"))
        if category_status not in {"PROPOSED", "DECISION_REQUIRED"}:
            raise PreparationError("candidate category status is invalid")
        if not candidate or not authority or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise PreparationError("candidate category evidence is incomplete")
        if not isinstance(copy, dict) or set(copy) != {
            "status", "language", "title", "description", "specification_name", "variants"
        }:
            raise PreparationError("candidate copy shape is invalid")
        if _clean_text(copy.get("status")).upper() != "PROPOSED":
            raise PreparationError("candidate copy status is invalid")
        variants = copy.get("variants")
        if not isinstance(variants, list) or not variants or len(variants) > 20:
            raise PreparationError("candidate copy variants are invalid")
        clean_variants: list[dict[str, str]] = []
        variant_skus: set[str] = set()
        for variant in variants:
            if not isinstance(variant, dict) or set(variant) != {"seller_sku", "display_name"}:
                raise PreparationError("candidate copy variant shape is invalid")
            sku = _clean_text(variant.get("seller_sku"))
            display = _clean_text(variant.get("display_name"))
            if not sku or not display or sku in variant_skus:
                raise PreparationError("candidate copy variant identity is invalid")
            variant_skus.add(sku)
            clean_variants.append({"seller_sku": sku, "display_name": display[:160]})
        language = _clean_text(copy.get("language"))
        title = _clean_text(copy.get("title"))
        description = _clean_text(copy.get("description"))
        specification = _clean_text(copy.get("specification_name"))
        if not language or not title or not description or not specification:
            raise PreparationError("candidate copy evidence is incomplete")
        _reject_internal_audit_copy(title, description, specification)
        seen.add(target)
        clean_targets.append(
            {
                "target": target,
                "category": {
                    "status": category_status,
                    "candidate": candidate[:320],
                    "authority": authority[:120],
                    "evidence_digest": digest,
                    "note": _clean_text(category.get("note"))[:320],
                },
                "copy": {
                    "status": "PROPOSED",
                    "language": language[:40],
                    "title": title[:240],
                    "description": description[:2000],
                    "specification_name": specification[:80],
                    "variants": clean_variants,
                },
            }
        )

    clean_groups: list[dict[str, Any]] = []
    group_ids: set[str] = set()
    for row in raw_groups:
        if not isinstance(row, dict) or set(row) != {"id", "label", "description", "recommended"}:
            raise PreparationError("content-group candidate shape is invalid")
        group_id = _clean_text(row.get("id"))
        label = _clean_text(row.get("label"))
        description = _clean_text(row.get("description"))
        if not group_id or group_id in group_ids or not label or not description or type(row.get("recommended")) is not bool:
            raise PreparationError("content-group candidate identity is invalid")
        group_ids.add(group_id)
        clean_groups.append(
            {
                "id": group_id[:80],
                "label": label[:120],
                "description": description[:400],
                "recommended": row["recommended"],
            }
        )
    if clean_groups and sum(row["recommended"] is True for row in clean_groups) != 1:
        raise PreparationError("content-group candidates require one recommendation")
    clean_platform_categories: list[dict[str, str]] = []
    seen_platforms: set[str] = set()
    for row in raw_platform_categories:
        required = {
            "platform", "status", "category_id", "category_en", "category_zh",
            "authority", "evidence_digest", "note",
        }
        if not isinstance(row, dict) or set(row) != required:
            raise PreparationError("platform category shape is invalid")
        platform = _clean_text(row.get("platform")).lower()
        status = _clean_text(row.get("status")).upper()
        digest = _clean_text(row.get("evidence_digest"))
        if (
            platform not in {"tiktok", "shopee", "ozon"}
            or platform in seen_platforms
            or status not in {"PROPOSED", "DECISION_REQUIRED"}
            or not _clean_text(row.get("category_id"))
            or not _clean_text(row.get("category_en"))
            or not _clean_text(row.get("category_zh"))
            or not _clean_text(row.get("authority"))
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
        ):
            raise PreparationError("platform category evidence is invalid")
        seen_platforms.add(platform)
        clean_platform_categories.append({
            "platform": platform,
            "status": status,
            "category_id": _clean_text(row.get("category_id"))[:80],
            "category_en": _clean_text(row.get("category_en"))[:320],
            "category_zh": _clean_text(row.get("category_zh"))[:320],
            "authority": _clean_text(row.get("authority"))[:120],
            "evidence_digest": digest,
            "note": _clean_text(row.get("note"))[:320],
        })
    clean_copy_review_sets: list[dict[str, Any]] = []
    seen_review_ids: set[str] = set()
    for row in raw_copy_review_sets:
        required = {
            "id", "label", "target_group", "title_en", "title_zh",
            "description_en", "description_zh", "specification_name_en",
            "specification_name_zh", "specification_values",
        }
        if not isinstance(row, dict) or set(row) != required:
            raise PreparationError("copy review set shape is invalid")
        review_id = _clean_text(row.get("id"))
        values = _unique_text(row.get("specification_values") or [])
        text_fields = {
            key: _clean_text(row.get(key))
            for key in required - {"specification_values"}
        }
        if (
            not review_id
            or review_id in seen_review_ids
            or not values
            or len(values) > 20
            or any(not value for value in text_fields.values())
        ):
            raise PreparationError("copy review set evidence is incomplete")
        _reject_internal_audit_copy(
            text_fields["title_en"],
            text_fields["title_zh"],
            text_fields["description_en"],
            text_fields["description_zh"],
        )
        seen_review_ids.add(review_id)
        clean_copy_review_sets.append({
            "id": review_id[:80],
            "label": text_fields["label"][:120],
            "target_group": text_fields["target_group"][:80],
            "title_en": text_fields["title_en"][:240],
            "title_zh": text_fields["title_zh"][:240],
            "description_en": text_fields["description_en"][:2000],
            "description_zh": text_fields["description_zh"][:2000],
            "specification_name_en": text_fields["specification_name_en"][:80],
            "specification_name_zh": text_fields["specification_name_zh"][:80],
            "specification_values": [value[:160] for value in values],
        })
    clean = {
        "schema_version": "first-review-candidate-plan/v1",
        "target_candidates": clean_targets,
        **({"platform_categories": clean_platform_categories, "copy_review_sets": clean_copy_review_sets}
           if "platform_categories" in value else {}),
        "content_group_options": clean_groups,
    }
    if clean != value:
        raise PreparationError("candidate plan contains unsupported or inconsistent fields")
    return clean


def _apply_candidate_plan(
    targets: list[dict[str, Any]], plan: dict[str, Any]
) -> list[dict[str, Any]]:
    candidates = {row["target"]: row for row in plan["target_candidates"]}
    projected: list[dict[str, Any]] = []
    for target in targets:
        row = dict(target)
        candidate = candidates.get(row["target"])
        if candidate is not None:
            row["category"] = dict(candidate["category"])
            row["copy"] = dict(candidate["copy"])
        projected.append(row)
    return projected


def _is_scene_role(value: Any) -> bool:
    role = _clean_text(value).lower()
    return role.endswith("_scene") or bool(re.fullmatch(r"scene_[1-9][0-9]*", role))


def _annotate_parcel_facts(facts: dict[str, Any]) -> dict[str, Any]:
    def number(value: Any) -> float | None:
        import math
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    def package(value: Any) -> list[float]:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return []
        parsed = [number(item) for item in value]
        return [float(item) for item in parsed] if all(item is not None for item in parsed) else []

    sku_rows = facts.get("skus") if isinstance(facts.get("skus"), list) else []
    complete_rows: list[dict[str, Any]] = []
    incomplete_skus: list[str] = []
    for index, row in enumerate(sku_rows, start=1):
        if not isinstance(row, dict):
            continue
        sku = _clean_text(row.get("seller_sku") or row.get("model_sku") or row.get("source_key")) or f"SKU {index}"
        row_package = package(row.get("package_cm"))
        row_weight = number(row.get("weight_kg"))
        row_cost = number(row.get("cost_cny"))
        if row_package and row_weight is not None and row_cost is not None:
            complete_rows.append(
                {
                    "seller_sku": sku,
                    "cost_cny": row_cost,
                    "weight_kg": row_weight,
                    "package_cm": row_package,
                }
            )
        else:
            incomplete_skus.append(sku)

    shared_package = package(facts.get("package_cm"))
    shared_weight = number(facts.get("weight_kg"))
    shared_cost = number(facts.get("cost_cny"))
    per_sku_complete = bool(sku_rows) and len(complete_rows) == len(sku_rows)
    any_per_sku_facts = any(
        isinstance(row, dict) and any(row.get(key) not in (None, [])
                                     for key in ('cost_cny', 'weight_kg', 'package_cm'))
        for row in sku_rows)
    warnings: list[str] = []
    if per_sku_complete and shared_package and any(
        row["package_cm"] != shared_package for row in complete_rows
    ):
        warnings.append(
            "shared package candidate differs from per-SKU parcel facts; per-SKU facts are authoritative"
        )
    annotated = dict(facts)
    annotated["parcel_fact_mode"] = (
        "PER_SKU"
        if per_sku_complete
        else (
            "SHARED"
            if not any_per_sku_facts and shared_package and shared_weight is not None and shared_cost is not None
            else "INCOMPLETE"
        )
    )
    annotated["per_sku_parcel_facts"] = complete_rows
    annotated["incomplete_parcel_skus"] = incomplete_skus
    annotated["parcel_warnings"] = warnings
    return annotated

def _review_specification_value(label: Any) -> str:
    """Return a human review label: size first, then style, then quantity."""
    from domains.product_operations.sku_display_name import (
        derive_sku_display_name,
    )

    return derive_sku_display_name(label)

def _material_review_blockers(
    *, product_facts: dict[str, Any], targets: list[dict[str, Any]],
    image_plan: dict[str, Any], requested_targets: list[str],
    content_groups: dict[str, Any] | None = None,
) -> list[str]:
    """Check parcel and price completeness without changing category authority."""
    import math
    def valid_price(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value)) and float(value) > 0
        except (TypeError, ValueError, OverflowError):
            return False
    def complete_price(price: dict[str, Any]) -> bool:
        variants = price.get('sku_prices') or []
        if variants:
            identities = [_clean_text(row.get('model_sku')) for row in variants]
            expected = {_clean_text(row.get('seller_sku')) for row in product_facts.get('skus', [])
                        if _clean_text(row.get('seller_sku'))}
            return (all(identities) and len(set(identities)) == len(identities)
                    and (not expected or set(identities) == expected)
                    and all(valid_price(row.get('amount')) and _clean_text(row.get('currency'))
                            for row in variants))
        return valid_price(price.get('amount')) and bool(_clean_text(price.get('currency')))
    price_targets = [row['target'] for row in targets
                     if _selection_key(row['target']) != 'miaoshou_common'
                     and not complete_price(row.get('price') or {})]
    blockers = []
    if price_targets:
        blockers.append(f"reviewed price remains unresolved for {len(price_targets)} targets")
    if product_facts.get('parcel_fact_mode') == 'INCOMPLETE':
        blockers.append('cost, weight, or package dimensions are incomplete')
    return blockers

def _safe_price_calculation(price: dict[str, Any]) -> dict[str, Any] | None:
    """Project a bounded, public formula audit from Product Center pricing."""
    role = _clean_text(price.get("role"))
    formula = _clean_text(price.get("formula"))
    derived = price.get("derived_preview") if isinstance(price.get("derived_preview"), dict) else {}
    store_prices = [
        row for row in (price.get("store_prices") or []) if isinstance(row, dict)
    ]
    if role == "master_listing" and store_prices:
        source = store_prices[0]
        override = (
            source.get("price_override")
            if isinstance(source.get("price_override"), dict)
            else None
        )
        if override is not None:
            return {
                "contract_version": "target-price-override/v1",
                "kind": "OFFICIAL_SAME_STORE_A_LINK_PRICE",
                "formula_zh": "挂牌价直接继承同店官方 A 链当前标价；本次不执行营销折扣。",
                "inputs": {
                    "authority": override.get("authority"),
                    "source_product_id": override.get("source_product_id"),
                    "evidence_digest": override.get("evidence_digest"),
                    "reason": override.get("reason"),
                },
                "derived_preview": {
                    "local_original_price": source.get("list_price"),
                    "currency": source.get("currency"),
                },
            }
        fees = source.get("fees") if isinstance(source.get("fees"), dict) else {}
        parameters = (
            source.get("formula_parameters")
            if isinstance(source.get("formula_parameters"), dict)
            else {}
        )
        header = source.get("header_meta") if isinstance(source.get("header_meta"), dict) else {}
        region = _clean_text(source.get("region")).upper()
        if region == "MX":
            inputs = {
                key: value for key, value in {
                    "goods_cost_local": source.get("goods_cost_local"),
                    "hidden_shipping_local": source.get("hidden_shipping_local"),
                    "fixed_fee_local": source.get("fixed_fee_local", header.get("fixed_fee_local")),
                    "import_tax_rate_pct": header.get("import_tax_rate"),
                    "commission_rate_pct": header.get("commission_rate"),
                    "sfp_rate_pct": header.get("sfp_rate"),
                    "affiliate_rate_pct": header.get("affiliate_rate"),
                    "ad_rate_pct": header.get("ad_rate"),
                    "target_margin_pct": header.get("target_margin_pct"),
                    "discount_reserve_pct": source.get("discount_reserve_pct", header.get("discount_reserve_pct")),
                    "minimum_profit_cny": source.get("minimum_profit_cny"),
                }.items() if value is not None
            }
            if set(inputs) <= {"minimum_profit_cny"}:
                return {
                    "kind": "MX_REVERSE_PRICING_LEGACY_PROJECTION",
                    "derived_preview": {
                        "local_original_price": source.get("list_price"),
                    },
                }
            return {
                "contract_version": "publication-price-calculation/v2",
                "kind": "MX_REVERSE_PRICING",
                "formula_zh": "成交价 = (本地货值 + 隐藏物流 + 固定费) ÷ (1 - 进口税率 - 佣金率 - SFP费率 - 达人费率 - 广告费率 - 目标利润率)；挂牌价 = 成交价 ÷ (1 - 折扣预留率)。",
                "inputs": inputs,
                "result": {
                    "sale_after_discount": source.get("sale_after_discount_local", source.get("discount_price")),
                    "list_price": source.get("list_price"),
                    "currency": source.get("currency"),
                    "estimated_profit_cny": source.get("estimated_profit_cny"),
                    "min_profit_adjusted": source.get("min_profit_adjusted") is True,
                },
            }
        if region == "GB":
            inputs = {
                key: value for key, value in {
                    "goods_cost_local": source.get("goods_cost_local"),
                    "shipping_local": source.get("shipping_local"),
                    "vat_rate_pct": header.get("vat_rate"),
                    "commission_rate_pct": header.get("commission_rate"),
                    "smart_promo_rate_pct": header.get("smart_promo_rate"),
                    "affiliate_rate_pct": header.get("affiliate_rate"),
                    "ad_rate_pct": header.get("ad_rate"),
                    "target_margin_pct": header.get("target_margin_pct"),
                    "discount_reserve_pct": source.get("discount_reserve_pct", header.get("discount_reserve_pct")),
                    "minimum_profit_cny": source.get("minimum_profit_cny"),
                }.items() if value is not None
            }
            if set(inputs) <= {"minimum_profit_cny"}:
                return {
                    "kind": "GB_REVERSE_PRICING_LEGACY_PROJECTION",
                    "derived_preview": {
                        "local_original_price": source.get("list_price"),
                    },
                }
            return {
                "contract_version": "publication-price-calculation/v2",
                "kind": "GB_REVERSE_PRICING",
                "formula_zh": "成交价 = (本地货值 + 本地物流) ÷ (1 - VAT费率 - 佣金率 - Smart Promo费率 - 达人费率 - 广告费率 - 目标利润率)；挂牌价 = 成交价 ÷ (1 - 折扣预留率)。",
                "inputs": inputs,
                "result": {
                    "sale_after_discount": source.get("sale_after_discount_local", source.get("discount_price")),
                    "list_price": source.get("list_price"),
                    "currency": source.get("currency"),
                    "estimated_profit_cny": source.get("estimated_profit_cny"),
                    "min_profit_adjusted": source.get("min_profit_adjusted") is True,
                },
            }
        inputs = {
            key: value
            for key, value in {
                "goods_cost_local": fees.get("goods_cost_local"),
                "logistics_local": fees.get("logistics_local"),
                "fixed_fee_local": fees.get("fixed_fee_local"),
                "commission_rate_pct": parameters.get("commission_rate"),
                "transaction_rate_pct": parameters.get("transaction_rate"),
                "extra_rate_pct": parameters.get("extra_rate"),
                "extra_fee_cap_local": parameters.get("extra_cap_local"),
                "affiliate_rate_pct": parameters.get("affiliate_rate"),
                "ad_rate_pct": parameters.get("ad_rate"),
                "creator_rate_pct": parameters.get("creator_rate"),
                "seller_tax_rate_pct": parameters.get("seller_tax_rate"),
                "target_margin_pct": parameters.get("target_margin_pct"),
                "discount_reserve_pct": source.get("discount_reserve_pct"),
                "minimum_profit_cny": source.get("minimum_profit_cny"),
            }.items()
            if value is not None
        }
        return {
            "contract_version": "publication-price-calculation/v2",
            "kind": "SEA_REVERSE_PRICING",
            "formula_zh": (
                "固定成本 = 本地货值 + 本地物流 + 固定费；基础费率 = 佣金率 + 交易费率 + "
                "达人费率 + 广告费率 + 创作者费率 + 卖家税率；初算成交价 = 固定成本 ÷ "
                "(1 - 目标利润率 - 基础费率 - 额外费率)。若额外费达到大于 0 的封顶上限，"
                "则成交价 = (固定成本 + 封顶额外费) ÷ (1 - 目标利润率 - 基础费率)；"
                "挂牌价 = 成交价 ÷ (1 - 折扣预留率)；"
                "再按市场价格步长向上取整，并继续上调直到预计利润达到最低人民币利润门槛。"
            ),
            "inputs": inputs,
            "result": {
                "sale_after_discount": source.get("sale_after_discount"),
                "list_price": source.get("list_price"),
                "currency": source.get("currency"),
                "estimated_profit_cny": source.get("estimated_profit_cny"),
                "min_profit_adjusted": source.get("min_profit_adjusted") is True,
            },
        }
    if formula or derived:
        return {
            "kind": "DERIVED_PRICE_CANDIDATE",
            "formula": formula,
            "formula_zh": (
                "展示 Product Center 提供的 TikTok 价格及工作台汇率候选换算；"
                "具体表达式、汇率与结果如下，发布前仍须重新回读。"
            ),
            "derived_preview": {
                key: derived.get(key)
                for key in (
                    "global_original_price_cny",
                    "local_original_price",
                    "price_cny",
                    "old_price_cny",
                    "source_currency",
                    "exchange_rate_cny_per_local",
                )
                if derived.get(key) is not None
            },
        }
    return None

if __name__ == "__main__":
    raise SystemExit(main())
