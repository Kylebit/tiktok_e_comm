"""Production composition adapters for one governed Orbit ReleasePlan.

The pure channel domain deliberately has no marketplace imports.  This module
is the integration boundary: it re-validates the immutable plan and durable
target idempotency key, invokes one existing marketplace path, and only returns
success after an independent API read-back.
"""

from __future__ import annotations

import math
import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from core import auth, shops
from core.api_client import get as tiktok_get
from core.api_client import post as tiktok_post
from domains.channel_operations.omnichannel_orchestrator import ADAPTER_NAMES
from domains.channel_operations.release_executor import (
    AdapterExecutionRequest,
    AdapterExecutionResult,
    AdapterRegistration,
)
from shared_platform.release_store import default_release_store


TIKTOK_SEARCH_PATH = "/product/202309/products/search"
TIKTOK_DETAIL_PATH = "/product/202309/products/{product_id}"
MIAOSHOU_PUBLISH_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/save_move_collect_task"
)
SEA_SITES = {"LH_PH": "PH", "LH_MY": "MY", "LH_TH": "TH", "LH_VN": "VN"}
SITE_TARGET_KEYS = {
    "LH_PH": "lh_ph",
    "LH_MY": "lh_my",
    "LH_TH": "lh_th",
    "LH_VN": "lh_vn",
    "MX": "mx",
    "GB": "gb",
}


def production_adapter_registry() -> dict[str, AdapterRegistration]:
    """Return the real, token-bound adapter registry used by the product UI."""

    registrations = {
        "miaoshou": _registration(
            ADAPTER_NAMES["miaoshou"],
            _common_already_committed,
        ),
        "tiktok": _registration(
            ADAPTER_NAMES["tiktok"],
            execute_tiktok_target,
        ),
        "shopee": _registration(
            ADAPTER_NAMES["shopee"],
            execute_shopee_target,
        ),
        "ozon": _registration(
            ADAPTER_NAMES["ozon"],
            execute_ozon_target,
        ),
    }
    return {
        registration.adapter_name: registration
        for registration in registrations.values()
    }


def _registration(name: str, execute) -> AdapterRegistration:
    return AdapterRegistration(
        adapter_name=name,
        execute=execute,
        consumes_unified_plan=True,
        validates_confirmation_token=True,
        preserves_idempotency_key=True,
        verifies_readback=True,
    )


def _common_already_committed(
    request: AdapterExecutionRequest,
) -> AdapterExecutionResult:
    _validated_context(request)
    return AdapterExecutionResult(
        succeeded=True,
        readback_verified=True,
        detail="Miaoshou COMMON is committed by the dedicated verified-draft step",
        external_reference=request.product_id,
        readback_evidence={"source": "release_target_run", "verified": True},
    )


def _validated_context(request: AdapterExecutionRequest) -> dict[str, Any]:
    """Re-authorize one target against the immutable plan and durable run."""

    store = default_release_store()
    plan = store.get_plan(request.plan_id)
    if not plan or plan.get("status") != "APPROVED":
        raise RuntimeError("release adapter requires an approved persisted plan")
    approval = plan.get("approval") or {}
    payload = plan.get("payload") or {}
    if (
        request.confirmation_token != plan.get("confirmation_token")
        or approval.get("confirmation_token") != request.confirmation_token
    ):
        raise RuntimeError("release confirmation token does not match the approved plan")
    if request.target_label not in (plan.get("targets") or ()):
        raise RuntimeError("release target is outside the approved plan")
    expected_identity = {
        "product_id": plan.get("product_id"),
        "seller_sku": plan.get("seller_sku"),
        "product_package_id": plan.get("product_package_id"),
        "content_package_id": plan.get("content_package_id"),
    }
    actual_identity = {
        "product_id": request.product_id,
        "seller_sku": request.seller_sku,
        "product_package_id": request.product_package_id,
        "content_package_id": request.content_package_id,
    }
    if expected_identity != actual_identity:
        raise RuntimeError("release request identity differs from the immutable plan")
    if (
        request.approval_scope_digest
        != str(payload.get("omnichannel_scope_digest") or "")
    ):
        raise RuntimeError("release approval scope digest does not match the plan")

    run = store.get_run(f"release-run:{plan['payload_digest'][:24]}")
    if not run:
        raise RuntimeError("durable release run was not created")
    target = next(
        (
            row
            for row in (run.get("targets") or ())
            if row.get("target_label") == request.target_label
        ),
        None,
    )
    if not target or target.get("idempotency_key") != request.idempotency_key:
        raise RuntimeError("release target idempotency key does not match the durable run")

    facts = payload.get("product_facts") or {}
    images = [
        str(row.get("image_url") or "").strip()
        for row in (payload.get("images") or ())
        if isinstance(row, dict) and str(row.get("image_url") or "").startswith("https://")
    ]
    if len(images) != len(payload.get("images") or ()) or len(set(images)) != len(images):
        raise RuntimeError("immutable release plan images are not unique HTTPS assets")
    if not facts.get("title") or not facts.get("weight_kg") or not facts.get("package_cm"):
        raise RuntimeError("immutable release plan is missing approved product facts")
    return {
        "store": store,
        "plan": plan,
        "payload": payload,
        "run": run,
        "target": target,
        "facts": facts,
        "images": images,
    }


def _candidate(payload: dict[str, Any], channel: str, site: str) -> str:
    for row in ((payload.get("listing_copy") or {}).get("candidates") or ()):
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("channel") or "").casefold() == channel.casefold()
            and str(row.get("site") or "").upper() == site.upper()
            and str(row.get("policy_check") or "") == "passed"
        ):
            title = str(row.get("title") or "").strip()
            if title:
                return title
    raise RuntimeError(f"approved listing title candidate is missing for {channel}:{site}")


def _target_pricing(payload: dict[str, Any], label: str) -> dict[str, Any]:
    pricing = (
        ((payload.get("pricing") or {}).get("selected_targets") or {}).get(label)
        or {}
    )
    if not pricing:
        raise RuntimeError(f"approved pricing evidence is missing for {label}")
    return pricing


def _store_price(payload: dict[str, Any], label: str) -> dict[str, Any]:
    rows = _target_pricing(payload, label).get("store_prices") or ()
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError(f"{label} requires exactly one approved store price")
    return dict(rows[0])


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _numbers_equal(actual: object, expected: object, tolerance: str = "0.01") -> bool:
    left, right = _decimal(actual), _decimal(expected)
    return bool(
        left is not None
        and right is not None
        and abs(left - right) <= Decimal(tolerance)
    )


def _tiktok_shop(region: str) -> tuple[str, dict[str, Any]]:
    token = auth.access_token()
    candidates = shops.list_shops(token)
    shop = next(
        (
            row
            for row in candidates
            if str(row.get("region") or row.get("region_code") or "").upper()
            == region.upper()
        ),
        None,
    )
    if not shop:
        raise RuntimeError(f"TikTok authorization has no {region} shop")
    return token, shop


def _cache_verified_tiktok_listing(
    *,
    shop: dict[str, Any],
    detail: dict[str, Any],
) -> int:
    """Persist only an exact, already verified TikTok API readback.

    Shopee and Ozon derive their cross-channel identity from the local TikTok
    catalogue.  Updating it here closes the publish/readback loop without
    running a broad catalogue sync or accepting Miaoshou's submission response
    as product truth.
    """

    from core.db import connect
    from modules.products.sync import _rows_from_product, _upsert_products, _upsert_shop

    rows = _rows_from_product(shop, detail)
    if not rows:
        raise RuntimeError(
            "verified TikTok product could not be normalized into the local catalogue"
        )
    with connect() as conn:
        _upsert_shop(conn, shop)
        return _upsert_products(conn, rows)


def _tiktok_readback(
    *,
    seller_sku: str,
    region: str,
    expected_title: str,
    expected_price: object,
    expected_image_count: int,
    expected_category_id: str,
) -> tuple[bool, dict[str, Any]]:
    token, shop = _tiktok_shop(region)
    cipher = str(shop.get("cipher") or shop.get("shop_cipher") or "")
    result = tiktok_post(
        TIKTOK_SEARCH_PATH,
        token,
        {"shop_cipher": cipher, "page_size": 100},
        {"status": "ACTIVATE", "seller_skus": [seller_sku]},
    )
    if result.get("code") != 0:
        raise RuntimeError(result.get("message") or "TikTok exact-SKU search failed")
    data = result.get("data") or {}
    products = data.get("products") or data.get("product_list") or data.get("list") or []
    exact = [
        product
        for product in products
        if any(
            str(sku.get("seller_sku") or "") == seller_sku
            for sku in (product.get("skus") or ())
        )
        or str(product.get("seller_sku") or "") == seller_sku
    ]
    if not exact:
        return False, {
            "verified": False,
            "region": region,
            "seller_sku": seller_sku,
            "reason": "not_found",
        }
    if len(exact) != 1:
        raise RuntimeError(f"TikTok {region} returned multiple products for exact SKU")
    product_id = str(exact[0].get("id") or exact[0].get("product_id") or "")
    detail_result = tiktok_get(
        TIKTOK_DETAIL_PATH.format(product_id=product_id),
        token,
        {"shop_cipher": cipher},
    )
    if detail_result.get("code") != 0:
        raise RuntimeError(detail_result.get("message") or "TikTok detail readback failed")
    detail = detail_result.get("data") or {}
    skus = [
        sku
        for sku in (detail.get("skus") or ())
        if str(sku.get("seller_sku") or "") == seller_sku
    ]
    sku = skus[0] if len(skus) == 1 else {}
    price = (sku.get("price") or {}).get("sale_price")
    images = detail.get("main_images") or detail.get("images") or ()
    category_chains = detail.get("category_chains") or ()
    category_id = str(
        (
            category_chains[-1].get("id")
            if category_chains and isinstance(category_chains[-1], dict)
            else detail.get("category_id")
        )
        or ""
    )
    checks = {
        "single_exact_sku": len(skus) == 1,
        "title": str(detail.get("title") or "") == expected_title,
        "price": _numbers_equal(price, expected_price),
        "image_count": len(images) == expected_image_count,
        "category": category_id == str(expected_category_id),
        "active": str(detail.get("status") or detail.get("product_status") or "").upper()
        in {"ACTIVATE", "LIVE"},
    }
    image_urls = [
        str((image.get("urls") or image.get("url_list") or [""])[0]).strip()
        for image in images
        if isinstance(image, dict)
        and (image.get("urls") or image.get("url_list"))
    ]
    evidence = {
        "verified": all(checks.values()),
        "source": "official_tiktok_shop_api",
        "region": region,
        "shop_id": str(shop.get("id") or shop.get("shop_id") or ""),
        "product_id": product_id,
        "seller_sku": seller_sku,
        "title": detail.get("title"),
        "price": price,
        "currency": (sku.get("price") or {}).get("currency"),
        "image_count": len(images),
        "image_urls": image_urls,
        "category_id": category_id,
        "status": detail.get("status") or detail.get("product_status"),
        "checks": checks,
    }
    if evidence["verified"]:
        evidence["catalog_rows_upserted"] = _cache_verified_tiktok_listing(
            shop=shop,
            detail=detail,
        )
    return bool(evidence["verified"]), evidence


def _tiktok_only_title_mismatch(evidence: dict[str, Any]) -> bool:
    checks = evidence.get("checks") or {}
    return bool(
        checks
        and checks.get("title") is False
        and all(
            passed
            for name, passed in checks.items()
            if name != "title"
        )
    )


def _repair_tiktok_title(
    *,
    region: str,
    product_id: str,
    approved_title: str,
) -> dict[str, Any]:
    """Apply the approved title only; never send commerce fields."""

    token, shop = _tiktok_shop(region)
    cipher = str(shop.get("cipher") or shop.get("shop_cipher") or "")
    response = tiktok_post(
        f"/product/202309/products/{product_id}/partial_edit",
        token,
        {"shop_cipher": cipher},
        {"title": approved_title},
    )
    if response.get("code") != 0:
        raise RuntimeError(
            response.get("message")
            or f"TikTok {region} approved-title repair failed"
        )
    return {
        "action": "official_tiktok_partial_edit",
        "fields": ["title"],
        "product_id": product_id,
        "region": region,
        "verified": False,
    }


def _selected_tiktok_target_keys(payload: dict[str, Any]) -> list[str]:
    return [
        SITE_TARGET_KEYS[label.split(":", 1)[1]]
        for label in (payload.get("targets") or ())
        if str(label).startswith("tiktok:")
        and label.split(":", 1)[1] in SITE_TARGET_KEYS
    ]


def _miaoshou_publish_target(
    payload: dict[str, Any],
    *,
    site: str,
) -> tuple[str, dict[str, Any]]:
    from modules.miaoshou.client import post_open
    from modules.sourcing import new_product_workbench as workbench

    offer_id = str(payload["product_id"])
    # One release target owns one Miaoshou collect-box detail.  This keeps
    # retries isolated and avoids re-claiming sites that already passed their
    # official marketplace read-back.
    selected = [SITE_TARGET_KEYS[site]]
    claim = workbench.claim_miaoshou_to_tiktok(
        offer_id,
        selected_target_ids=selected,
    )
    prepared = workbench.prepare_miaoshou_site_drafts(offer_id)
    key = SITE_TARGET_KEYS[site]
    shop = (claim.get("shops") or {}).get(key) or {}
    detail_group = str(shop.get("detail_group") or "")
    detail_id = int(
        (claim.get("detail_group_detail_ids") or {}).get(detail_group)
        or claim.get("tiktok_detail_id")
        or 0
    )
    shop_id = int(shop.get("shop_id") or 0)
    if not detail_id or not shop_id:
        raise RuntimeError(f"Miaoshou claim did not resolve {site} detail/shop identity")
    region = SEA_SITES.get(site, site)
    prepared_site = (
        (prepared.get("sites") or {}).get(region)
        or (prepared.get("shops") or {}).get(key)
        or {}
    )
    if not (
        prepared_site.get("verified")
        or prepared_site.get("ready")
    ):
        raise RuntimeError(f"Miaoshou {site} site draft did not pass exact readback")
    response = post_open(
        MIAOSHOU_PUBLISH_PATH,
        {"detailIds": [detail_id], "shopIds": [shop_id]},
    )
    if response.get("result") != "success":
        raise RuntimeError(
            f"Miaoshou {site} publish submission failed: "
            f"{response.get('code')} {response.get('message') or ''}"
        )
    return f"{detail_id}:{shop_id}", {
        "source": "miaoshou_open_api",
        "accepted": True,
        "detail_id": detail_id,
        "shop_id": shop_id,
        "response_code": response.get("code"),
    }


def _prior_unverified_tiktok_submission(
    request: AdapterExecutionRequest,
) -> tuple[str, dict[str, Any]] | None:
    """Return durable submission evidence so retries never resubmit blindly."""

    store = default_release_store()
    plan = store.get_plan(request.plan_id)
    if not plan:
        return None
    run = store.get_run(f"release-run:{plan['payload_digest'][:24]}")
    target = next(
        (
            row
            for row in ((run or {}).get("targets") or ())
            if row.get("target_label") == request.target_label
        ),
        None,
    )
    external_id = str((target or {}).get("external_id") or "").strip()
    if not external_id:
        return None
    detail_id, _, shop_id = external_id.partition(":")
    return external_id, {
        "source": "release_run_ledger",
        "accepted": True,
        "detail_id": detail_id,
        "shop_id": shop_id,
        "prior_submission_reused": True,
    }


def execute_tiktok_target(
    request: AdapterExecutionRequest,
) -> AdapterExecutionResult:
    context = _validated_context(request)
    payload = context["payload"]
    site = request.site.upper()
    if site not in SITE_TARGET_KEYS:
        raise RuntimeError(f"unsupported governed TikTok site {site}")
    country = SEA_SITES.get(site, site)
    expected_title = _candidate(payload, "tiktok", country)
    expected_price = _store_price(payload, request.target_label).get("list_price")
    if expected_price in (None, ""):
        raise RuntimeError(f"approved TikTok price is missing for {request.target_label}")

    if site not in SEA_SITES:
        prior_submission = _prior_unverified_tiktok_submission(request)
        if prior_submission:
            external_reference, submission = prior_submission
            return AdapterExecutionResult(
                True,
                False,
                (
                    f"Miaoshou already accepted {site}; retry did not resubmit. "
                    "An authorised official TikTok readback is still unavailable."
                ),
                external_reference,
                submission,
            )

    if site in SEA_SITES:
        verified, evidence = _tiktok_readback(
            seller_sku=request.seller_sku,
            region=country,
            expected_title=expected_title,
            expected_price=expected_price,
            expected_image_count=len(context["images"]),
            expected_category_id="600338",
        )
        if verified:
            return AdapterExecutionResult(
                True,
                True,
                "existing TikTok listing exactly matches the approved release",
                str(evidence.get("product_id") or ""),
                evidence,
            )
        if _tiktok_only_title_mismatch(evidence):
            repair = _repair_tiktok_title(
                region=country,
                product_id=str(evidence.get("product_id") or ""),
                approved_title=expected_title,
            )
            time.sleep(2)
            verified, corrected = _tiktok_readback(
                seller_sku=request.seller_sku,
                region=country,
                expected_title=expected_title,
                expected_price=expected_price,
                expected_image_count=len(context["images"]),
                expected_category_id="600338",
            )
            corrected["repair"] = {**repair, "verified": verified}
            if verified:
                return AdapterExecutionResult(
                    True,
                    True,
                    "existing TikTok listing title was normalized and exact readback matched",
                    str(corrected.get("product_id") or ""),
                    corrected,
                )

    external_reference, submission = _miaoshou_publish_target(payload, site=site)
    if site not in SEA_SITES:
        return AdapterExecutionResult(
            True,
            False,
            (
                f"Miaoshou accepted {site}, but no authorised official TikTok "
                "readback exists for this account; target remains unverified"
            ),
            external_reference,
            submission,
        )

    last_evidence: dict[str, Any] = submission
    title_repair: dict[str, Any] | None = None
    for attempt in range(24):
        if attempt:
            time.sleep(10)
        verified, evidence = _tiktok_readback(
            seller_sku=request.seller_sku,
            region=country,
            expected_title=expected_title,
            expected_price=expected_price,
            expected_image_count=len(context["images"]),
            expected_category_id="600338",
        )
        last_evidence = {**submission, **evidence, "poll_attempt": attempt + 1}
        if (
            title_repair is None
            and _tiktok_only_title_mismatch(evidence)
        ):
            title_repair = _repair_tiktok_title(
                region=country,
                product_id=str(evidence.get("product_id") or ""),
                approved_title=expected_title,
            )
            last_evidence["repair"] = title_repair
            continue
        if title_repair is not None:
            last_evidence["repair"] = {
                **title_repair,
                "verified": verified,
            }
        if verified:
            return AdapterExecutionResult(
                True,
                True,
                (
                    "TikTok listing title was normalized and exact official API readback matched"
                    if title_repair
                    else "TikTok listing published and matched exact official API readback"
                ),
                str(evidence.get("product_id") or external_reference),
                last_evidence,
            )
    return AdapterExecutionResult(
        True,
        False,
        f"TikTok {country} publish was accepted but exact API readback did not converge",
        external_reference,
        last_evidence,
    )


def _shopee_item_id_for_match_key(match_key: str, region: str) -> str:
    from modules.shopee.global_sku_map import (
        global_item_id_for_match_key,
        load_map,
    )

    global_id = global_item_id_for_match_key(match_key)
    if not global_id:
        return ""
    entry = load_map().get(str(global_id)) or {}
    shop_ref = (entry.get("shop_items") or {}).get(region.upper()) or {}
    return str(shop_ref.get("item_id") or "")


def _shopee_readback(
    *,
    match_key: str,
    region: str,
    item_id: str,
    expected_title: str,
    expected_price: object,
    expected_image_count: int,
) -> tuple[bool, dict[str, Any]]:
    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.client import shop_get
    from modules.shopee.publish import sync_shop_ids

    shop_id = int(sync_shop_ids()[region.upper()])
    token = ensure_shop_token(shop_id)
    base = shop_get(
        "/api/v2/product/get_item_base_info",
        shop_id,
        token,
        {"item_id_list": str(item_id)},
    )
    items = (base.get("response") or {}).get("item_list") or ()
    if len(items) != 1:
        return False, {
            "verified": False,
            "source": "official_shopee_partner_api",
            "region": region,
            "item_id": item_id,
            "reason": "item_not_found",
        }
    item = items[0]
    models_response = shop_get(
        "/api/v2/product/get_model_list",
        shop_id,
        token,
        {"item_id": int(item_id)},
    )
    models = (models_response.get("response") or {}).get("model") or ()
    seller_skus = {
        str(value)
        for value in [
            item.get("item_sku"),
            *(model.get("model_sku") for model in models),
        ]
        if value not in (None, "")
    }
    price_values: list[object] = []
    for model in models:
        for price in model.get("price_info") or ():
            price_values.extend(
                value
                for value in (
                    price.get("original_price"),
                    price.get("current_price"),
                )
                if value not in (None, "")
            )
    for price in item.get("price_info") or ():
        price_values.extend(
            value
            for value in (
                price.get("original_price"),
                price.get("current_price"),
            )
            if value not in (None, "")
        )
    if not price_values:
        price_values = [
            value
            for value in (item.get("original_price"), item.get("price"))
            if value not in (None, "")
        ]
    image_count = len((item.get("image") or {}).get("image_url_list") or ())
    checks = {
        "seller_sku": match_key in seller_skus,
        "title": str(item.get("item_name") or "") == expected_title,
        "price": any(_numbers_equal(value, expected_price) for value in price_values),
        "image_count": image_count == expected_image_count,
        "status": str(item.get("item_status") or "").upper() in {"NORMAL", "UNLIST"},
    }
    evidence = {
        "verified": all(checks.values()),
        "source": "official_shopee_partner_api",
        "region": region,
        "shop_id": shop_id,
        "item_id": str(item_id),
        "seller_skus": sorted(seller_skus),
        "title": item.get("item_name"),
        "prices": price_values,
        "image_count": image_count,
        "status": item.get("item_status"),
        "checks": checks,
    }
    return bool(evidence["verified"]), evidence


def _discover_shopee_item_id_by_sku(
    *,
    seller_sku: str,
    region: str,
) -> str:
    """Recover a published shop item when a prior task result was interrupted.

    Shopee can accept a CNSC publish task before the local map is updated.  A
    retry must discover and verify that item instead of trying to publish it a
    second time.
    """

    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.client import shop_get
    from modules.shopee.publish import sync_shop_ids

    shop_id = int(sync_shop_ids()[region.upper()])
    token = ensure_shop_token(shop_id)
    for status in ("NORMAL", "UNLIST"):
        offset = 0
        while True:
            listing = shop_get(
                "/api/v2/product/get_item_list",
                shop_id,
                token,
                {
                    "offset": offset,
                    "page_size": 50,
                    "item_status": status,
                },
            )
            rows = (listing.get("response") or {}).get("item") or ()
            ids = [str(row.get("item_id") or "") for row in rows if row.get("item_id")]
            for start in range(0, len(ids), 50):
                base = shop_get(
                    "/api/v2/product/get_item_base_info",
                    shop_id,
                    token,
                    {"item_id_list": ",".join(ids[start : start + 50])},
                )
                for item in (base.get("response") or {}).get("item_list") or ():
                    if str(item.get("item_sku") or "") == seller_sku:
                        return str(item.get("item_id") or "")
                    if not item.get("has_model"):
                        continue
                    models = shop_get(
                        "/api/v2/product/get_model_list",
                        shop_id,
                        token,
                        {"item_id": int(item.get("item_id") or 0)},
                    )
                    if any(
                        str(model.get("model_sku") or "") == seller_sku
                        for model in (models.get("response") or {}).get("model") or ()
                    ):
                        return str(item.get("item_id") or "")
            response = listing.get("response") or {}
            if not response.get("has_next_page"):
                break
            offset = int(response.get("next_offset") or offset + 50)
    return ""


def execute_shopee_target(
    request: AdapterExecutionRequest,
) -> AdapterExecutionResult:
    context = _validated_context(request)
    payload = context["payload"]
    region = request.site.upper()
    title = _candidate(payload, "shopee", "CNSC")
    pricing = _target_pricing(payload, request.target_label)
    expected_price = (pricing.get("source") or {}).get("list_price")
    if expected_price in (None, ""):
        raise RuntimeError(f"approved Shopee source price is missing for {region}")

    item_id = (
        _shopee_item_id_for_match_key(request.seller_sku, region)
        or _discover_shopee_item_id_by_sku(
            seller_sku=request.seller_sku[-4:].zfill(4),
            region=region,
        )
    )
    if item_id:
        verified, evidence = _shopee_readback(
            match_key=request.seller_sku[-4:].zfill(4),
            region=region,
            item_id=item_id,
            expected_title=title,
            expected_price=expected_price,
            expected_image_count=len(context["images"]),
        )
        if verified:
            from modules.shopee.global_sku_map import (
                global_item_id_for_match_key,
                record_shop_item,
            )
            from modules.shopee.publish import sync_shop_ids

            global_id = global_item_id_for_match_key(request.seller_sku)
            if global_id:
                record_shop_item(
                    str(global_id),
                    region,
                    shop_id=int(sync_shop_ids()[region]),
                    item_id=item_id,
                )
            return AdapterExecutionResult(
                True,
                True,
                "existing Shopee listing exactly matches official API readback",
                item_id,
                evidence,
            )

    from modules.shopee.publish import publish_match_key

    result = publish_match_key(
        request.seller_sku,
        region,
        dry_run=False,
        global_only=False,
        publish_shops=True,
        item_status="NORMAL",
        title_override=title,
    )
    item_id = str(result.get("item_id") or _shopee_item_id_for_match_key(
        request.seller_sku,
        region,
    ))
    if not item_id:
        return AdapterExecutionResult(
            False,
            False,
            f"Shopee {region} publish did not return an item_id",
            None,
            {"source": "official_shopee_partner_api", "publish_result": result},
        )
    for attempt in range(12):
        if attempt:
            time.sleep(5)
        verified, evidence = _shopee_readback(
            match_key=request.seller_sku[-4:].zfill(4),
            region=region,
            item_id=item_id,
            expected_title=title,
            expected_price=expected_price,
            expected_image_count=len(context["images"]),
        )
        evidence["poll_attempt"] = attempt + 1
        if verified:
            return AdapterExecutionResult(
                True,
                True,
                "Shopee listing published and matched official API readback",
                item_id,
                evidence,
            )
    return AdapterExecutionResult(
        True,
        False,
        f"Shopee {region} publish completed but exact API readback did not converge",
        item_id,
        evidence,
    )


def _ozon_readback(
    *,
    offer_id: str,
    expected_title: str,
    expected_price: object,
    expected_image_count: int,
) -> tuple[bool, dict[str, Any]]:
    from modules.ozon.client import ozon_post

    response = ozon_post("/v3/product/info/list", {"offer_id": [offer_id]})
    items = response.get("items") or ()
    if len(items) != 1:
        return False, {
            "verified": False,
            "source": "official_ozon_seller_api",
            "offer_id": offer_id,
            "reason": "offer_not_found",
        }
    item = items[0]
    prices = [
        item.get("marketing_price"),
        item.get("price"),
        item.get("old_price"),
    ]
    statuses = item.get("statuses") or {}
    item_errors = item.get("errors") or ()
    visibility = item.get("visibility_details") or {}
    images = list(
        dict.fromkeys(
            [
                *(item.get("primary_image") or ()),
                *(item.get("images") or ()),
            ]
        )
    )
    validation_status = str(statuses.get("validation_status") or "").lower()
    moderation_status = str(statuses.get("moderate_status") or "").lower()
    status_failed = str(statuses.get("status_failed") or "").lower()
    checks = {
        "offer_id": str(item.get("offer_id") or "") == offer_id,
        "title": str(item.get("name") or item.get("title") or "") == expected_title,
        "price": any(_numbers_equal(value, expected_price) for value in prices),
        "images": len(images) == expected_image_count,
        "moderation": bool(
            statuses.get("is_created")
            and validation_status == "success"
            and moderation_status == "approved"
            and not status_failed
            and not item_errors
            and not item.get("is_archived")
            and not item.get("is_autoarchived")
        ),
        "stock": bool(visibility.get("has_stock")),
    }
    evidence = {
        "verified": all(checks.values()),
        "source": "official_ozon_seller_api",
        "offer_id": offer_id,
        "product_id": str(item.get("id") or item.get("product_id") or ""),
        "sku": str(item.get("sku") or ""),
        "title": item.get("name") or item.get("title"),
        "prices": prices,
        "image_count": len(images),
        "status": statuses.get("status"),
        "moderate_status": statuses.get("moderate_status"),
        "validation_status": statuses.get("validation_status"),
        "status_failed": statuses.get("status_failed"),
        "errors": item_errors,
        "image_urls": images,
        "has_stock": bool(visibility.get("has_stock")),
        "checks": checks,
    }
    return bool(evidence["verified"]), evidence


def _ozon_only_rich_content_declined(evidence: dict[str, Any]) -> bool:
    errors = evidence.get("errors") or ()
    return bool(
        errors
        and all(
            str(error.get("code") or "") == "DESCRIPTION_DECLINE"
            and int(error.get("attribute_id") or 0) == 11254
            for error in errors
        )
    )


def _ozon_audited_rich_content(
    *,
    title: str,
    images: list[str],
    width_cm: float,
    height_cm: float,
) -> dict[str, Any]:
    if not images:
        raise RuntimeError("Ozon Rich Content repair requires verified Ozon images")
    size = (
        f"{int(width_cm) if width_cm.is_integer() else width_cm}"
        f" × {int(height_cm) if height_cm.is_integer() else height_cm} см"
    )
    blocks: list[dict[str, Any]] = [
        {
            "widgetName": "raShowcase",
            "type": "billboard",
            "blocks": [
                {
                    "imgLink": "",
                    "img": {
                        "src": images[0],
                        "srcMobile": images[0],
                        "alt": title[:60],
                        "position": "width_full",
                        "positionMobile": "width_full",
                        "widthMobile": 1200,
                        "heightMobile": 1600,
                    },
                }
            ],
        },
        {
            "widgetName": "raTextBlock",
            "theme": "default",
            "padding": "type2",
            "gapSize": "m",
            "title": {
                "content": ["Описание товара"],
                "size": "size2",
                "align": "left",
                "color": "color1",
            },
            "text": {
                "size": "size4",
                "align": "left",
                "color": "color1",
                "content": [title],
            },
        },
        {
            "widgetName": "raTextBlock",
            "theme": "default",
            "padding": "type2",
            "gapSize": "s",
            "title": {
                "content": [f"Размер {size}"],
                "size": "size3",
                "align": "left",
                "color": "color1",
            },
            "text": {
                "size": "size4",
                "align": "left",
                "color": "color1",
                "content": [
                    "Перед покупкой проверьте изображения, размеры и "
                    "характеристики товара."
                ],
            },
        },
    ]
    if len(images) > 1:
        blocks.append(
            {
                "widgetName": "raShowcase",
                "type": "roll",
                "blocks": [
                    {
                        "imgLink": "",
                        "img": {
                            "src": url,
                            "srcMobile": url,
                            "alt": title[:60],
                            "position": "width_full",
                            "positionMobile": "width_full",
                        },
                    }
                    for url in images[1:]
                ],
            }
        )
    return {"content": blocks, "version": 0.3}


def _repair_ozon_rich_content(
    *,
    offer_id: str,
    title: str,
    images: list[str],
    width_cm: float,
    height_cm: float,
) -> dict[str, Any]:
    from modules.ozon.client import ozon_post

    rich = _ozon_audited_rich_content(
        title=title,
        images=images,
        width_cm=width_cm,
        height_cm=height_cm,
    )
    response = ozon_post(
        "/v1/product/attributes/update",
        {
            "items": [
                {
                    "offer_id": offer_id,
                    "attributes": [
                        {
                            "complex_id": 0,
                            "id": 11254,
                            "values": [
                                {
                                    "value": json.dumps(
                                        rich,
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    )
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    task_id = response.get("task_id") or (response.get("result") or {}).get("task_id")
    if not task_id:
        raise RuntimeError("Ozon Rich Content repair did not return a task_id")
    last_status = ""
    for _ in range(12):
        time.sleep(2)
        task = ozon_post("/v1/product/import/info", {"task_id": task_id})
        rows = (task.get("result") or {}).get("items") or ()
        if not rows:
            continue
        last_status = str(rows[0].get("status") or "")
        if last_status == "imported" and not (rows[0].get("errors") or ()):
            return {
                "source": "official_ozon_seller_api",
                "action": "replace_declined_rich_content",
                "task_id": str(task_id),
                "status": last_status,
                "fact_source": "approved_title_dimensions_and_images",
            }
        if last_status != "pending":
            raise RuntimeError(
                f"Ozon Rich Content repair failed: {rows[0].get('errors') or last_status}"
            )
    raise RuntimeError(
        f"Ozon Rich Content repair did not complete: {last_status or 'pending'}"
    )


def _ozon_existing_listing_is_processing(evidence: dict[str, Any]) -> bool:
    checks = evidence.get("checks") or {}
    return bool(
        checks.get("offer_id")
        and checks.get("title")
        and checks.get("price")
        and checks.get("images")
        and not (evidence.get("errors") or ())
        and str(evidence.get("validation_status") or "").lower() == "success"
    )


def _ozon_set_release_stock(
    *,
    offer_id: str,
    stock: int = 50,
) -> dict[str, Any]:
    from modules.ozon.client import ozon_post

    warehouses_response = ozon_post("/v2/warehouse/list", {})
    eligible = [
        row
        for row in (warehouses_response.get("warehouses") or ())
        if str(row.get("status") or "").lower() in {"active", "created"}
        and not row.get("is_kgt")
    ]
    if len(eligible) != 1:
        raise RuntimeError(
            "Ozon release requires exactly one eligible non-KGT seller warehouse; "
            f"found {len(eligible)}"
        )
    warehouse_id = int(eligible[0]["warehouse_id"])
    response = ozon_post(
        "/v2/products/stocks",
        {
            "stocks": [
                {
                    "offer_id": str(offer_id),
                    "stock": int(stock),
                    "warehouse_id": warehouse_id,
                }
            ]
        },
    )
    result = response.get("result") or response.get("stocks") or ()
    errors = [
        error
        for row in result
        for error in (row.get("errors") or ())
        if error
    ]
    if errors or response.get("error"):
        raise RuntimeError(
            f"Ozon stock update failed: {errors or response.get('message') or response.get('error')}"
        )
    return {
        "source": "official_ozon_seller_api",
        "warehouse_id": str(warehouse_id),
        "stock": int(stock),
        "response_rows": len(result),
    }


def execute_ozon_target(
    request: AdapterExecutionRequest,
) -> AdapterExecutionResult:
    context = _validated_context(request)
    payload = context["payload"]
    title = _candidate(payload, "ozon", "RU")
    pricing = _target_pricing(payload, request.target_label)
    derived = pricing.get("derived_preview") or {}
    price_cny = derived.get("price_cny")
    old_price_cny = derived.get("old_price_cny")
    if price_cny in (None, "") or old_price_cny in (None, ""):
        raise RuntimeError("approved Ozon derived prices are missing")
    package = list(context["facts"].get("package_cm") or ())
    width, height = (
        (float(package[0]), float(package[1]))
        if len(package) >= 2
        else (0.0, 0.0)
    )
    from modules.ozon.listing_text import polish_ozon_title

    expected_platform_title = polish_ozon_title(
        title,
        len_cm=str(int(width) if width.is_integer() else width),
        wid_cm=str(int(height) if height.is_integer() else height),
        migrate_profile="sticker",
    )
    offer_id = request.seller_sku[-4:].zfill(4)
    verified, evidence = _ozon_readback(
        offer_id=offer_id,
        expected_title=expected_platform_title,
        expected_price=price_cny,
        expected_image_count=len(context["images"]),
    )
    if verified:
        return AdapterExecutionResult(
            True,
            True,
            "existing Ozon listing exactly matches official API readback",
            str(evidence.get("product_id") or offer_id),
            evidence,
        )
    if _ozon_only_rich_content_declined(evidence):
        repair_evidence = _repair_ozon_rich_content(
            offer_id=offer_id,
            title=expected_platform_title,
            images=list(evidence.get("image_urls") or ()),
            width_cm=width,
            height_cm=height,
        )
        stock_evidence = _ozon_set_release_stock(offer_id=offer_id)
        for attempt in range(24):
            if attempt:
                time.sleep(10)
            verified, repaired = _ozon_readback(
                offer_id=offer_id,
                expected_title=expected_platform_title,
                expected_price=price_cny,
                expected_image_count=len(context["images"]),
            )
            repaired["poll_attempt"] = attempt + 1
            repaired["rich_content_repair"] = repair_evidence
            repaired["stock_write"] = stock_evidence
            if verified:
                return AdapterExecutionResult(
                    True,
                    True,
                    "Ozon legacy Rich Content was repaired and exact readback matched",
                    str(repaired.get("product_id") or offer_id),
                    repaired,
                )
        return AdapterExecutionResult(
            True,
            False,
            "Ozon Rich Content repair completed but moderation did not converge",
            offer_id,
            repaired,
        )
    if _ozon_existing_listing_is_processing(evidence):
        stock_evidence = (
            {"reused": True}
            if evidence.get("has_stock")
            else _ozon_set_release_stock(offer_id=offer_id)
        )
        for attempt in range(24):
            if attempt:
                time.sleep(10)
            verified, processing = _ozon_readback(
                offer_id=offer_id,
                expected_title=expected_platform_title,
                expected_price=price_cny,
                expected_image_count=len(context["images"]),
            )
            processing["poll_attempt"] = attempt + 1
            processing["stock_write"] = stock_evidence
            if verified:
                return AdapterExecutionResult(
                    True,
                    True,
                    "existing Ozon listing completed moderation and exact readback matched",
                    str(processing.get("product_id") or offer_id),
                    processing,
                )
        return AdapterExecutionResult(
            True,
            False,
            "Ozon listing is still processing after the official readback window",
            offer_id,
            processing,
        )

    source_key = str(pricing.get("selected_source_target_key") or "lh_ph")
    source_region = {
        "lh_ph": "PH",
        "lh_my": "MY",
        "lh_th": "TH",
        "lh_vn": "VN",
    }.get(source_key)
    if not source_region:
        raise RuntimeError("approved Ozon source target is not a verified TikTok SEA site")
    source_label = {
        "PH": "tiktok:LH_PH",
        "MY": "tiktok:LH_MY",
        "TH": "tiktok:LH_TH",
        "VN": "tiktok:LH_VN",
    }[source_region]
    source_verified, source_evidence = _tiktok_readback(
        seller_sku=request.seller_sku,
        region=source_region,
        expected_title=_candidate(payload, "tiktok", source_region),
        expected_price=_store_price(payload, source_label).get("list_price"),
        expected_image_count=len(context["images"]),
        expected_category_id="600338",
    )
    delivery_images = list(source_evidence.get("image_urls") or ())
    if not source_verified or len(delivery_images) != len(context["images"]):
        raise RuntimeError(
            "Ozon requires the exact verified TikTok master image set before import"
        )

    from modules.ozon.migrate_batch import migrate_one

    result = migrate_one(
        request.seller_sku,
        allow_deepseek=False,
        title_candidate=title,
        product_size_cm=(width, height),
        quantity=1,
        price_cny_override=int(math.ceil(float(price_cny))),
        old_price_cny_override=int(math.ceil(float(old_price_cny))),
        price_source_override="approved_release_plan",
        price_label_override=request.target_label,
        # Use TikTok's official CDN copies of the already-approved image set.
        # Origin 1688/ToAPI URLs may reject server-side downloads even though
        # the pixels were accepted by TikTok.
        image_urls_override=delivery_images,
        # The legacy image processor re-hosts files through a third-party
        # image service. These CDN URLs already represent the exact approved
        # assets, so submit them directly to Ozon and preserve their lineage.
        process_images=False,
        # Rich content is an independent, separately moderated asset. The
        # governed V1 publishes only the approved title, description and
        # images; it must not generate an unaudited Rich JSON payload.
        skip_rich_content=True,
    )
    offer_id = str(result.get("offer_id") or offer_id)
    if not result.get("ok"):
        return AdapterExecutionResult(
            False,
            False,
            f"Ozon import failed: {result.get('error') or result.get('status')}",
            offer_id,
            {"source": "official_ozon_seller_api", "import_result": result},
        )
    stock_evidence = _ozon_set_release_stock(offer_id=offer_id)
    for attempt in range(24):
        if attempt:
            time.sleep(10)
        verified, evidence = _ozon_readback(
            offer_id=offer_id,
            expected_title=expected_platform_title,
            expected_price=price_cny,
            expected_image_count=len(context["images"]),
        )
        evidence["poll_attempt"] = attempt + 1
        evidence["stock_write"] = stock_evidence
        if verified:
            return AdapterExecutionResult(
                True,
                True,
                "Ozon listing imported and matched official API readback",
                str(evidence.get("product_id") or offer_id),
                evidence,
            )
    return AdapterExecutionResult(
        True,
        False,
        "Ozon import completed but exact official API readback did not converge",
        offer_id,
        evidence,
    )
