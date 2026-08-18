"""Production composition adapters for one governed Orbit ReleasePlan.

The pure channel domain deliberately has no marketplace imports.  This module
is the integration boundary: it re-validates the immutable plan and durable
target idempotency key, invokes one existing marketplace path, and only returns
success after an independent API read-back.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import json
import re
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
MIAOSHOU_SHOP_DETAIL_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
)
MIAOSHOU_TIKTOK_DETAIL_LIST_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list"
)
MIAOSHOU_WAREHOUSE_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list"
)
MIAOSHOU_COMMON_DETAIL_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/"
    "get_common_collect_box_detail"
)
MIAOSHOU_COMMON_EDIT_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/"
    "edit_common_collect_box_detail"
)
SEA_SITES = {"LH_PH": "PH", "LH_MY": "MY", "LH_TH": "TH", "LH_VN": "VN"}
SITE_TARGET_KEYS = {
    "LH_PH": "lh_ph",
    "LH_MY": "lh_my",
    "LH_TH": "lh_th",
    "LH_VN": "lh_vn",
    "HB_PH": "hb_ph",
    "HB_MY": "hb_my",
    "HB_TH": "hb_th",
    "HB_VN": "hb_vn",
    "MX": "mx",
    "GB": "gb",
}
SITE_COUNTRIES = {
    **SEA_SITES,
    "HB_PH": "PH",
    "HB_MY": "MY",
    "HB_TH": "TH",
    "HB_VN": "VN",
    "MX": "MX",
    "GB": "GB",
}
SUBMISSION_ONLY_TIKTOK_SITES = frozenset(
    {"HB_PH", "HB_MY", "HB_TH", "HB_VN", "MX", "GB"}
)


class ReleaseAdapterWriteVerificationError(RuntimeError):
    """A mutating adapter dispatch needs durable reconciliation."""

    def __init__(
        self,
        message: str,
        *,
        external_reference: str,
        evidence: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.external_reference = external_reference
        self.external_write_evidence = evidence


class MiaoshouDraftVerificationError(ReleaseAdapterWriteVerificationError):
    """A Miaoshou update was accepted but exact readback did not verify."""


class MiaoshouPreSubmitError(RuntimeError):
    """A COMMON preparation failed before its editable-detail POST."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code)
        self.external_reference = None
        self.external_write_evidence = {
            "source": "miaoshou_open_api",
            "verified": False,
            "pre_submit_failure": True,
            "submission_accepted": False,
            "reason_code": self.reason_code,
            "external_writes_performed": [],
        }


class _MiaoshouDetailListContractError(ValueError):
    """A read-only TikTok detail-list response is not provably complete."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _complete_miaoshou_tiktok_detail_rows(
    response: Any,
    *,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Validate one bounded Miaoshou page without inventing pagination fields.

    The audited Open API response may contain only ``data.detailList``. A
    short first page is complete under its page-number contract. Optional
    count and cursor fields remain strict hard gates whenever they are present.
    """

    if not isinstance(response, dict) or response.get("result") != "success":
        raise _MiaoshouDetailListContractError(
            "Miaoshou TikTok duplicate scan failed",
            reason_code="miaoshou_tiktok_duplicate_scan_failed",
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise _MiaoshouDetailListContractError(
            "Miaoshou TikTok duplicate scan response is malformed",
            reason_code="miaoshou_tiktok_duplicate_scan_malformed",
        )
    rows = data.get("detailList")
    if rows is None:
        rows = data.get("list")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) for row in rows
    ):
        raise _MiaoshouDetailListContractError(
            "Miaoshou TikTok duplicate scan rows are malformed",
            reason_code="miaoshou_tiktok_duplicate_scan_malformed",
        )
    if len(rows) >= page_size:
        raise _MiaoshouDetailListContractError(
            "Miaoshou TikTok duplicate scan is incomplete",
            reason_code="miaoshou_tiktok_duplicate_scan_incomplete",
        )
    total_key = next(
        (key for key in ("totalCount", "total") if key in data),
        None,
    )
    if total_key is not None:
        total_count = data[total_key]
        if type(total_count) is not int or total_count != len(rows):
            raise _MiaoshouDetailListContractError(
                "Miaoshou TikTok duplicate scan is incomplete",
                reason_code="miaoshou_tiktok_duplicate_scan_incomplete",
            )
    if "hasNextPage" in data and data["hasNextPage"] is not False:
        raise _MiaoshouDetailListContractError(
            "Miaoshou TikTok duplicate scan is incomplete",
            reason_code="miaoshou_tiktok_duplicate_scan_incomplete",
        )
    if "nextPageToken" in data and data["nextPageToken"] not in (None, ""):
        raise _MiaoshouDetailListContractError(
            "Miaoshou TikTok duplicate scan is incomplete",
            reason_code="miaoshou_tiktok_duplicate_scan_incomplete",
        )
    return list(rows)


class ShopeePriceRepairReconciliationError(
    ReleaseAdapterWriteVerificationError
):
    """A one-shot Shopee price repair cannot be safely repeated."""


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
            predecessor_recovery_mode=(
                "OFFICIAL_READBACK_THEN_BOUNDED_WRITE"
            ),
        ),
        "ozon": _registration(
            ADAPTER_NAMES["ozon"],
            execute_ozon_target,
            automatic_first_attempt_mode="BLOCKED_CAPABILITY",
        ),
    }
    return {
        registration.adapter_name: registration
        for registration in registrations.values()
    }


def _registration(
    name: str,
    execute,
    *,
    predecessor_recovery_mode: str = "BLOCKED",
    automatic_first_attempt_mode: str = "ENABLED",
) -> AdapterRegistration:
    return AdapterRegistration(
        adapter_name=name,
        execute=execute,
        consumes_unified_plan=True,
        validates_confirmation_token=True,
        preserves_idempotency_key=True,
        verifies_readback=True,
        predecessor_recovery_mode=predecessor_recovery_mode,
        automatic_first_attempt_mode=automatic_first_attempt_mode,
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


def _shopee_description(payload: dict[str, Any]) -> str:
    listing_copy = payload.get("listing_copy") or {}
    approved = str(listing_copy.get("shopee_description_en") or "").strip()
    if approved:
        if len(approved) < 500:
            raise RuntimeError("approved Shopee global description is too short")
        return approved[:3000]

    from modules.shopee.global_copy import build_factual_english_description

    facts = payload.get("product_facts") or {}
    package = list(facts.get("package_cm") or ())
    detail = {
        "title": str(facts.get("title") or ""),
        "description": "",
        "package_dimensions": {
            "length": package[0] if len(package) > 0 else None,
            "width": package[1] if len(package) > 1 else None,
            "height": package[2] if len(package) > 2 else None,
        },
    }
    return build_factual_english_description(
        detail,
        str(payload.get("seller_sku") or ""),
        title=str(facts.get("title") or ""),
    )


def _local_title_matches_region(
    title: str,
    *,
    region: str,
    english_master: str,
) -> bool:
    clean = str(title or "").strip()
    site = region.upper()
    if not clean:
        return False
    if site == "TH":
        return bool(any("\u0e00" <= char <= "\u0e7f" for char in clean))
    if site == "VN":
        vietnamese = set(
            "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệ"
            "ìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụ"
            "ưừứửữựỳýỷỹỵđĐ"
        )
        return bool(vietnamese.intersection(clean))
    if site in {"PH", "MY"}:
        return bool(
            any(char.isascii() and char.isalpha() for char in clean)
            and not any("\u4e00" <= char <= "\u9fff" for char in clean)
        )
    return clean == english_master


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
    evidence = {
        "source": "official_tiktok_shop_api",
        "verified": False,
        "action": "official_tiktok_partial_edit",
        "fields": ["title"],
        "product_id": product_id,
        "region": region,
        "approved_title": approved_title,
        "external_writes_performed": [
            "tiktok:official_title_partial_edit"
        ],
    }
    try:
        response = tiktok_post(
            f"/product/202309/products/{product_id}/partial_edit",
            token,
            {"shop_cipher": cipher},
            {"title": approved_title},
        )
    except Exception as error:
        raise ReleaseAdapterWriteVerificationError(
            (
                f"TikTok {region} title repair outcome is unknown after "
                f"dispatch: {error}"
            ),
            external_reference=product_id,
            evidence={
                **evidence,
                "write_outcome": "unknown_after_dispatch",
                "repair_exception": str(error),
            },
        ) from error
    if response.get("code") != 0:
        raise ReleaseAdapterWriteVerificationError(
            (
                response.get("message")
                or f"TikTok {region} approved-title repair failed"
            ),
            external_reference=product_id,
            evidence={
                **evidence,
                "write_outcome": "repair_rejected",
                "response_code": response.get("code"),
            },
        )
    return {
        **evidence,
        "write_outcome": "accepted",
    }


def _selected_tiktok_target_keys(payload: dict[str, Any]) -> list[str]:
    return [
        SITE_TARGET_KEYS[label.split(":", 1)[1]]
        for label in (payload.get("targets") or ())
        if str(label).startswith("tiktok:")
        and label.split(":", 1)[1] in SITE_TARGET_KEYS
    ]


def _miaoshou_submission_audit(
    payload: dict[str, Any],
    *,
    site: str,
    target_key: str,
    detail_id: int,
    shop_id: int,
    prepared_site: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the exact fields reviewed immediately before an API-less submit."""

    country = SITE_COUNTRIES[site]
    facts = payload.get("product_facts") or {}
    package_cm = list(facts.get("package_cm") or ())
    selected_sku_keys = [
        str(value).strip()
        for value in (facts.get("selected_sku_keys") or ())
        if str(value).strip()
    ]
    selected_skus = [
        {
            "key": str(row.get("key") or "").strip(),
            "label": str(row.get("label") or "").strip(),
        }
        for row in (facts.get("selected_skus") or ())
        if isinstance(row, dict)
        and str(row.get("key") or "").strip()
        and str(row.get("label") or "").strip()
    ]
    raw_sku_label_overrides = facts.get("sku_label_overrides")
    sku_label_overrides = {
        str(key).strip(): str(value).strip()
        for key, value in (
            raw_sku_label_overrides.items()
            if isinstance(raw_sku_label_overrides, dict)
            else ()
        )
        if str(key).strip() and str(value).strip()
    }
    selected_sku_labels = {
        row["key"]: row["label"]
        for row in selected_skus
    }
    category = facts.get("category") or {}
    category_name = str(
        category.get("name") if isinstance(category, dict) else category
    ).strip()
    images = [
        str(row.get("image_url") or "").strip()
        for row in (payload.get("images") or ())
        if isinstance(row, dict)
    ]
    video_urls = [
        str(url).strip()
        for url in (payload.get("video_urls") or ())
        if str(url).strip()
    ]
    pricing = _store_price(payload, f"tiktok:{site}")
    title = _candidate(payload, "tiktok", country)
    prepared_shop_ids = {
        str(value)
        for value in (
            prepared_site.get("site_collect_shop_ids")
            or prepared_site.get("shop_ids")
            or ()
        )
        if str(value or "").strip()
    }
    checks = {
        "immutable_identity": bool(
            payload.get("product_id")
            and payload.get("seller_sku")
            and payload.get("product_package_id")
            and payload.get("content_package_id")
        ),
        "approved_title": bool(title),
        "approved_price": pricing.get("list_price") not in (None, ""),
        "approved_images": bool(images)
        and len(images) == len(set(images))
        and all(url.startswith("https://") for url in images),
        "approved_logistics": bool(facts.get("weight_kg"))
        and len(package_cm) == 3
        and all(float(value or 0) > 0 for value in package_cm),
        "approved_variants": bool(selected_sku_keys)
        and (
            not sku_label_overrides
            or all(
                selected_sku_labels.get(key) == label
                for key, label in sku_label_overrides.items()
            )
        ),
        "approved_category": bool(category_name),
        "approved_video": all(url.startswith("https://") for url in video_urls),
        "exact_shop_claim": str(shop_id) in prepared_shop_ids,
        "miaoshou_draft_ready": bool(
            prepared_site.get("verified") or prepared_site.get("ready")
        ),
        "miaoshou_field_checks": bool(prepared_site.get("checks"))
        and all(bool(value) for value in (prepared_site.get("checks") or {}).values()),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(
            f"Miaoshou {site} pre-submit audit failed: {', '.join(failed)}"
        )
    audit_payload = {
        "schema_version": "miaoshou_submission_audit/v1",
        "site": site,
        "country": country,
        "target_key": target_key,
        "detail_id": detail_id,
        "shop_id": shop_id,
        "product_id": str(payload.get("product_id") or ""),
        "seller_sku": str(payload.get("seller_sku") or ""),
        "title": title,
        "price": pricing.get("list_price"),
        "currency": pricing.get("currency"),
        "weight_kg": facts.get("weight_kg"),
        "package_cm": package_cm,
        "selected_sku_keys": selected_sku_keys,
        "selected_skus": selected_skus,
        "sku_label_overrides": sku_label_overrides,
        "category": category_name,
        "image_count": len(images),
        "image_urls": images,
        "video_count": len(video_urls),
        "video_urls": video_urls,
        "checks": checks,
        "miaoshou_checks": dict(prepared_site.get("checks") or {}),
    }
    encoded = json.dumps(
        audit_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    audit_payload["submission_fingerprint"] = hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()
    return audit_payload


def _immutable_miaoshou_plan_draft(
    payload: dict[str, Any],
    *,
    site: str,
) -> dict[str, Any]:
    """Compose a draft exclusively from the approved ReleasePlan payload."""

    facts = payload.get("product_facts") or {}
    package_cm = list(facts.get("package_cm") or ())
    images = [
        str(row.get("image_url") or "").strip()
        for row in (payload.get("images") or ())
        if isinstance(row, dict) and str(row.get("image_url") or "").strip()
    ]
    videos = [
        str(value).strip()
        for value in (payload.get("video_urls") or ())
        if str(value).strip()
    ]
    selected_sku_keys = [
        str(value).strip()
        for value in (facts.get("selected_sku_keys") or ())
        if str(value).strip()
    ]
    if (
        len(package_cm) != 3
        or not images
        or not selected_sku_keys
        or not all(url.startswith("https://") for url in [*images, *videos])
    ):
        raise RuntimeError(
            "immutable release plan lacks exact logistics, images, variants, or video"
        )
    country = SITE_COUNTRIES[site]
    description = str(
        (payload.get("listing_copy") or {}).get("shopee_description_en") or ""
    ).strip()
    notes = (
        ("<p>" + description.replace("\n", "<br>") + "</p>")
        if description
        else ""
    ) + "".join(f'<p><img src="{url}"></p>' for url in images)
    return {
        "commonCollectBoxDetailId": int(payload["product_id"]),
        "title": _candidate(payload, "tiktok", country),
        "itemNum": str(payload["seller_sku"]),
        "weight": float(facts.get("weight_kg") or 0),
        "packageLength": float(package_cm[0]),
        "packageWidth": float(package_cm[1]),
        "packageHeight": float(package_cm[2]),
        "imgUrls": images,
        "notes": notes,
        "mainImgVideoUrl": videos[0] if videos else "",
        "selectedSkuKeys": selected_sku_keys,
        "skuLabelOverrides": dict(facts.get("sku_label_overrides") or {}),
    }


def _miaoshou_description_notes(
    description: str,
    image_urls: list[str],
) -> str:
    """Build the exact immutable rich-text payload shared by write/readback."""

    description_html = (
        ("<p>" + str(description).replace("\r\n", "\n").replace("\n", "<br>") + "</p>")
        if str(description).strip()
        else ""
    )
    return description_html + "".join(
        f'<p><img src="{url}"></p>' for url in image_urls
    )


def _normalize_miaoshou_notes(value: Any) -> str:
    """Ignore transport-only whitespace while preserving HTML/content exactly."""

    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return re.sub(r">\s+<", "><", normalized)


def _miaoshou_value_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _miaoshou_detail_digest(detail: dict[str, Any]) -> str:
    """Fingerprint only fields that can affect a governed COMMON overwrite."""

    return _miaoshou_value_digest(
        {
            "common_id": (
                detail.get("commonCollectBoxDetailId")
                or detail.get("commonCollectBoxId")
                or detail.get("id")
            ),
            "source_offer_id": (
                detail.get("sourceOfferId")
                or detail.get("offerId")
                or detail.get("sourceProductId")
            ),
            "title": detail.get("title"),
            "item_num": detail.get("itemNum"),
            "weight": detail.get("weight"),
            "package": [
                detail.get("packageLength"),
                detail.get("packageWidth"),
                detail.get("packageHeight"),
            ],
            "images": list(detail.get("imgUrls") or ()),
            "notes": _normalize_miaoshou_notes(detail.get("notes")),
            "video": detail.get("mainImgVideoUrl"),
            "sku_map": detail.get("skuMap") or {},
        }
    )


def _miaoshou_safe_summary(field: str, value: Any) -> str:
    """Summarise comparison values without returning copy, URLs, or identifiers."""

    digest = _miaoshou_value_digest(value)[:12]
    if field == "seller_sku":
        text = str(value or "")
        suffix = text[-2:] if text else "--"
        return f"••{suffix} · sha256:{digest}"
    if field == "weight":
        return f"{value if value not in (None, '') else 'unknown'} kg"
    if field == "package":
        values = list(value or ())
        if len(values) == 3:
            return " × ".join(str(item) for item in values) + " cm"
        return f"unknown · sha256:{digest}"
    if field == "images":
        return f"{len(list(value or ()))} images · order sha256:{digest}"
    if field in {"spec_key", "spec_label"}:
        return f"{len(list(value or ()))} values · sha256:{digest}"
    if field == "video_action":
        return "keep" if str(value or "").strip() else "remove"
    text = str(value or "")
    return f"{len(text)} chars · sha256:{digest}"


def _expected_miaoshou_sku_labels(
    payload: dict[str, Any],
    sku_keys: set[str],
) -> dict[str, str]:
    facts = payload.get("product_facts") or {}
    selected_labels = {
        str(row.get("key") or "").strip(";"): str(row.get("label") or "").strip()
        for row in (facts.get("selected_skus") or ())
        if isinstance(row, dict)
        and str(row.get("key") or "").strip(";")
        and str(row.get("label") or "").strip()
    }
    overrides = {
        str(key).strip(";"): str(value).strip()
        for key, value in dict(facts.get("sku_label_overrides") or {}).items()
        if str(key).strip(";") and str(value).strip()
    }
    return {
        key: overrides.get(key) or selected_labels.get(key) or key
        for key in sku_keys
    }


def _miaoshou_common_spec_label_application(
    payload: dict[str, Any],
    sku_map: dict[str, Any],
    sku_keys: set[str],
) -> dict[str, Any]:
    """Describe where approved labels are enforced without exposing labels."""

    expected = _expected_miaoshou_sku_labels(payload, sku_keys)
    applied_count = 0
    deferred_count = 0
    common_label_field_count = 0
    safe = set(expected) == set(sku_keys)
    for raw_key, raw_row in sku_map.items():
        key = str(raw_key).strip(";")
        if key not in sku_keys:
            continue
        if not isinstance(raw_row, dict):
            safe = False
            continue
        expected_label = expected.get(key)
        if not expected_label:
            safe = False
            continue
        label_fields = [
            field
            for field in ("specLabel", "specName", "skuName")
            if field in raw_row
        ]
        if label_fields:
            common_label_field_count += 1
            applied_count += 1
            if any(
                str(raw_row.get(field) or "").strip() != expected_label
                for field in label_fields
            ):
                safe = False
        elif expected_label == key:
            applied_count += 1
        else:
            # COMMON has no governed sale-property surface. The immutable
            # override remains mandatory in each site draft, where
            # _apply_audited_english_variant_labels() maps skuPropertyList and
            # fails before dispatch if the mapping is unavailable.
            deferred_count += 1
    status = (
        "deferred_to_site_draft"
        if safe and deferred_count
        else ("applied_in_common" if safe else "invalid")
    )
    evidence = {
        "schema_version": "miaoshou-common-spec-label-application/v1",
        "status": status,
        "authority": (
            "approved_release_plan_then_site_sku_property"
            if deferred_count
            else "miaoshou_common"
        ),
        "approved_label_count": len(expected),
        "applied_in_common_count": applied_count,
        "deferred_to_site_count": deferred_count,
        "common_label_field_available": (
            common_label_field_count == len(sku_keys)
        ),
        "site_draft_mapping_required": deferred_count > 0,
        "safe": safe,
        "approved_label_digest": _miaoshou_value_digest(
            sorted(expected.items())
        ),
    }
    evidence["evidence_digest"] = _miaoshou_value_digest(evidence)
    return evidence


def miaoshou_common_overwrite_review(
    payload: dict[str, Any],
    readback: dict[str, Any],
    *,
    plan_id: str,
    confirmation_token: str,
    payload_digest: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Build the redacted, fail-closed contract used by API and UI."""

    comparison = dict(readback.get("_comparison") or {})
    raw_diffs = dict(readback.get("field_diffs") or {})
    checks = {
        str(name): bool(passed)
        for name, passed in dict(readback.get("checks") or {}).items()
    }
    known_fields = {
        "title",
        "seller_sku",
        "selected_sku_keys",
        "selected_sku_numbers",
        "spec_labels",
        "weight",
        "dimensions",
        "images",
        "description_notes",
        "description_image_count",
        "video_action",
        "common_id",
        "source_identity",
        "detail_binding",
        "spec_label_binding",
    }
    unknown_fields = sorted(set(raw_diffs) - known_fields)
    identity_fields = {
        "seller_sku",
        "selected_sku_keys",
        "selected_sku_numbers",
        "common_id",
        "source_identity",
        "detail_binding",
        "spec_label_binding",
    }
    blocking_fields = sorted(
        {
            field
            for field in identity_fields
            if checks.get(field) is False or field in raw_diffs
        }
        | set(unknown_fields)
    )

    grouped = (
        ("title", "标题", ("title",)),
        ("seller_sku", "Seller SKU", ("seller_sku",)),
        ("spec_key", "规格 key", ("selected_sku_keys", "selected_sku_numbers")),
        ("spec_label", "规格标签", ("spec_labels",)),
        ("weight", "重量", ("weight",)),
        ("package", "包装尺寸", ("dimensions",)),
        ("images", "图片数量与顺序", ("images",)),
        (
            "description",
            "描述",
            ("description_notes", "description_image_count"),
        ),
        ("video_action", "视频动作", ("video_action",)),
    )
    field_rows = []
    for public_field, label, source_fields in grouped:
        changed = any(
            checks.get(source) is False or source in raw_diffs
            for source in source_fields
        )
        expected_values = [
            (comparison.get(source) or {}).get("expected")
            for source in source_fields
        ]
        actual_values = [
            (comparison.get(source) or {}).get("actual")
            for source in source_fields
        ]
        expected_value = (
            expected_values[0] if len(expected_values) == 1 else expected_values
        )
        actual_value = actual_values[0] if len(actual_values) == 1 else actual_values
        summary_field = (
            "package"
            if public_field == "package"
            else ("description" if public_field == "description" else public_field)
        )
        field_rows.append(
            {
                "field": public_field,
                "label": label,
                "changed": changed,
                "overwriteable": not any(
                    source in identity_fields for source in source_fields
                ),
                "existing_summary": _miaoshou_safe_summary(
                    summary_field,
                    actual_value,
                ),
                "immutable_plan_summary": _miaoshou_safe_summary(
                    summary_field,
                    expected_value,
                ),
            }
        )

    non_ambiguous = bool(
        readback.get("source") == "miaoshou_common_readonly_detail"
        and readback.get("readback_ambiguous") is not True
        and readback.get("existing_detail_digest")
    )
    overwrite_allowed = bool(
        not readback.get("verified")
        and non_ambiguous
        and not blocking_fields
        and raw_diffs
    )
    review = {
        "schema_version": "miaoshou-common-overwrite-review-v1",
        "status": "MISMATCH" if not readback.get("verified") else "MATCH",
        "plan_id": str(plan_id),
        "confirmation_token": str(confirmation_token),
        "payload_digest": str(payload_digest),
        "expected_revision": int(expected_revision),
        "existing_detail_digest": str(
            readback.get("existing_detail_digest") or ""
        ),
        "identity_exact": not any(field in blocking_fields for field in identity_fields),
        "readback_non_ambiguous": non_ambiguous,
        "overwrite_allowed": overwrite_allowed,
        "changed_fields": [
            row["field"] for row in field_rows if row["changed"]
        ],
        "blocking_fields": blocking_fields,
        "unknown_fields": unknown_fields,
        "fields": field_rows,
        "external_writes_performed": [],
    }
    review["review_digest"] = _miaoshou_value_digest(review)
    return review


def _immutable_miaoshou_common_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """Build COMMON exclusively from the approved immutable ReleasePlan."""

    facts = payload.get("product_facts") or {}
    package_cm = list(facts.get("package_cm") or ())
    images = [
        str(row.get("image_url") or "").strip()
        for row in (payload.get("images") or ())
        if isinstance(row, dict) and str(row.get("image_url") or "").strip()
    ]
    videos = [
        str(value).strip()
        for value in (payload.get("video_urls") or ())
        if str(value).strip()
    ]
    selected_sku_keys = [
        str(value).strip()
        for value in (facts.get("selected_sku_keys") or ())
        if str(value).strip()
    ]
    title = str(facts.get("title") or "").strip()
    seller_sku = str(payload.get("seller_sku") or "").strip()
    description = str(
        (payload.get("listing_copy") or {}).get("shopee_description_en") or ""
    ).strip()
    if (
        not title
        or not seller_sku
        or len(package_cm) != 3
        or not images
        or not selected_sku_keys
        or not all(url.startswith("https://") for url in [*images, *videos])
    ):
        raise RuntimeError("immutable COMMON release facts are incomplete")
    return {
        "commonCollectBoxDetailId": int(payload["product_id"]),
        "title": title,
        "itemNum": seller_sku,
        "weight": float(facts.get("weight_kg") or 0),
        "packageLength": float(package_cm[0]),
        "packageWidth": float(package_cm[1]),
        "packageHeight": float(package_cm[2]),
        "imgUrls": images,
        "notes": _miaoshou_description_notes(description, images),
        "mainImgVideoUrl": videos[0] if videos else "",
        "selectedSkuKeys": selected_sku_keys,
        "skuLabelOverrides": dict(facts.get("sku_label_overrides") or {}),
    }


def readback_miaoshou_common(
    payload: dict[str, Any],
    *,
    post=None,
) -> dict[str, Any]:
    """Read and compare COMMON without editing Miaoshou or local state."""

    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open
    expected = _immutable_miaoshou_common_draft(payload)
    response = post(
        MIAOSHOU_COMMON_DETAIL_PATH,
        {"commonCollectBoxDetailId": int(payload["product_id"])},
    )
    if response.get("result") != "success":
        raise RuntimeError(
            "Miaoshou COMMON readback failed: "
            f"{response.get('code')} {response.get('message') or ''}"
        )
    detail = (response.get("data") or {}).get("editCommonCollectBoxDetail")
    if not isinstance(detail, dict) or not detail:
        raise RuntimeError("Miaoshou COMMON readback returned no editable detail")

    actual_sku_map = (
        detail.get("skuMap") if isinstance(detail.get("skuMap"), dict) else {}
    )
    expected_sku_keys = {
        str(value).strip(";")
        for value in (expected.get("selectedSkuKeys") or ())
        if str(value).strip(";")
    }
    actual_sku_keys = {
        str(value).strip(";") for value in actual_sku_map
    }
    base_number = int(str(payload["seller_sku"]))
    expected_sku_numbers = {
        str((base_number + offset) % 10000).zfill(4)
        for offset in range(len(expected_sku_keys))
    }
    actual_sku_numbers = {
        str(row.get("itemNum") or "").strip()
        for row in actual_sku_map.values()
        if isinstance(row, dict) and str(row.get("itemNum") or "").strip()
    }
    actual_dimensions = [
        detail.get("packageLength"),
        detail.get("packageWidth"),
        detail.get("packageHeight"),
    ]
    expected_dimensions = [
        expected["packageLength"],
        expected["packageWidth"],
        expected["packageHeight"],
    ]
    actual_notes = str(detail.get("notes") or "")
    response_detail_id = (
        detail.get("commonCollectBoxDetailId")
        or detail.get("commonCollectBoxId")
        or detail.get("id")
    )
    expected_detail_id = int(payload["product_id"])
    actual_source_offer_id = (
        detail.get("sourceOfferId")
        or detail.get("offerId")
        or detail.get("sourceProductId")
    )
    expected_source_offer_id = str(
        (payload.get("product_facts") or {}).get("source_offer_id")
        or payload["product_id"]
    )
    expected_spec_labels_by_key = _expected_miaoshou_sku_labels(
        payload,
        expected_sku_keys,
    )
    spec_label_application = _miaoshou_common_spec_label_application(
        payload,
        actual_sku_map,
        expected_sku_keys,
    )
    spec_label_safe = spec_label_application.get("safe") is True
    checks = {
        "title": str(detail.get("title") or "") == expected["title"],
        "seller_sku": str(detail.get("itemNum") or "") == expected["itemNum"],
        "selected_sku_keys": actual_sku_keys == expected_sku_keys,
        "selected_sku_numbers": actual_sku_numbers == expected_sku_numbers,
        "spec_labels": spec_label_safe,
        "spec_label_binding": spec_label_safe,
        "weight": _numbers_equal(detail.get("weight"), expected["weight"], "0.0001"),
        "dimensions": all(
            _numbers_equal(actual, wanted, "0.0001")
            for actual, wanted in zip(actual_dimensions, expected_dimensions)
        ),
        "images": list(detail.get("imgUrls") or []) == expected["imgUrls"],
        "description_notes": (
            _normalize_miaoshou_notes(actual_notes)
            == _normalize_miaoshou_notes(expected["notes"])
        ),
        "description_image_count": actual_notes.count("<img")
        == len(expected["imgUrls"]),
        "video_action": (
            str(detail.get("mainImgVideoUrl") or "")
            == str(expected.get("mainImgVideoUrl") or "")
        ),
        "common_id": (
            response_detail_id is None
            or str(response_detail_id) == str(expected_detail_id)
        ),
        "source_identity": (
            actual_source_offer_id is None
            or str(actual_source_offer_id) == expected_source_offer_id
        ),
        "detail_binding": (
            response_detail_id is None
            or str(response_detail_id) == str(expected_detail_id)
        ),
    }
    comparison = {
        "title": {
            "expected": expected["title"],
            "actual": str(detail.get("title") or ""),
        },
        "seller_sku": {
            "expected": expected["itemNum"],
            "actual": str(detail.get("itemNum") or ""),
        },
        "selected_sku_keys": {
            "expected": sorted(expected_sku_keys),
            "actual": sorted(actual_sku_keys),
        },
        "selected_sku_numbers": {
            "expected": sorted(expected_sku_numbers),
            "actual": sorted(actual_sku_numbers),
        },
        "spec_labels": {
            "expected": {
                "approved_label_count": len(expected_spec_labels_by_key),
                "authority": "approved_release_plan",
            },
            "actual": {
                "applied_in_common_count": spec_label_application[
                    "applied_in_common_count"
                ],
                "deferred_to_site_count": spec_label_application[
                    "deferred_to_site_count"
                ],
                "status": spec_label_application["status"],
            },
        },
        "spec_label_binding": {
            "expected": True,
            "actual": spec_label_safe,
        },
        "weight": {
            "expected": expected["weight"],
            "actual": detail.get("weight"),
        },
        "dimensions": {
            "expected": expected_dimensions,
            "actual": actual_dimensions,
        },
        "images": {
            "expected": expected["imgUrls"],
            "actual": list(detail.get("imgUrls") or []),
        },
        "description_image_count": {
            "expected": len(expected["imgUrls"]),
            "actual": actual_notes.count("<img"),
        },
        "description_notes": {
            "expected": _normalize_miaoshou_notes(expected["notes"]),
            "actual": _normalize_miaoshou_notes(actual_notes),
        },
        "video_action": {
            "expected": str(expected.get("mainImgVideoUrl") or ""),
            "actual": str(detail.get("mainImgVideoUrl") or ""),
        },
        "common_id": {
            "expected": expected_detail_id,
            "actual": response_detail_id if response_detail_id is not None else expected_detail_id,
        },
        "source_identity": {
            "expected": expected_source_offer_id,
            "actual": (
                actual_source_offer_id
                if actual_source_offer_id is not None
                else expected_source_offer_id
            ),
        },
        "detail_binding": {
            "expected": expected_detail_id,
            "actual": response_detail_id if response_detail_id is not None else expected_detail_id,
        },
    }
    field_diffs = {
        field: comparison[field]
        for field, passed in checks.items()
        if not passed
    }
    return {
        "verified": all(checks.values()),
        "mode": "readback_reuse_no_write",
        "offer_id": str(payload["product_id"]),
        "checks": checks,
        "field_diffs": field_diffs,
        "_comparison": comparison,
        "existing_detail_digest": _miaoshou_detail_digest(detail),
        "readback_ambiguous": False,
        "image_count": len(detail.get("imgUrls") or ()),
        "spec_label_application": spec_label_application,
        "external_writes_performed": [],
        "source": "miaoshou_common_readonly_detail",
    }


def write_miaoshou_common_from_plan(
    payload: dict[str, Any],
    *,
    post=None,
    overwrite_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one immutable COMMON draft, then exact-read it back."""

    from modules.sourcing import new_product_workbench as workbench

    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open
    draft = _immutable_miaoshou_common_draft(payload)
    detail_id = int(draft["commonCollectBoxDetailId"])
    current_response = post(
        MIAOSHOU_COMMON_DETAIL_PATH,
        {"commonCollectBoxDetailId": detail_id},
    )
    if current_response.get("result") != "success":
        raise MiaoshouPreSubmitError(
            "Miaoshou COMMON immutable write could not read current detail",
            reason_code="common_detail_read_failed",
        )
    data = current_response.get("data") or {}
    current = data.get("editCommonCollectBoxDetail")
    oss_md5 = str(data.get("ossMd5") or "")
    if not isinstance(current, dict) or not current or not oss_md5:
        raise MiaoshouPreSubmitError(
            "Miaoshou COMMON immutable write lacks editable detail or ossMd5",
            reason_code="common_editable_detail_unavailable",
        )
    if overwrite_guard is not None:
        if (
            overwrite_guard.get("overwrite_allowed") is not True
            or overwrite_guard.get("identity_exact") is not True
            or overwrite_guard.get("readback_non_ambiguous") is not True
            or not str(overwrite_guard.get("existing_detail_digest") or "")
            or _miaoshou_detail_digest(current)
            != str(overwrite_guard.get("existing_detail_digest"))
        ):
            raise MiaoshouPreSubmitError(
                "Miaoshou COMMON changed after overwrite review; no edit was sent",
                reason_code="common_overwrite_review_drift",
            )
    current_sku_map = (
        current.get("skuMap")
        if isinstance(current.get("skuMap"), dict)
        else {}
    )
    selected_sku_keys = {
        str(value).strip(";")
        for value in (draft.get("selectedSkuKeys") or ())
        if str(value).strip(";")
    }
    selected_skus = {
        key: value
        for key, value in current_sku_map.items()
        if str(key).strip(";") in selected_sku_keys
    }
    if (
        not selected_skus
        or {str(key).strip(";") for key in selected_skus}
        != selected_sku_keys
    ):
        raise MiaoshouPreSubmitError(
            "immutable ReleasePlan variants do not exactly match Miaoshou COMMON",
            reason_code="common_variant_identity_mismatch",
        )
    sku_numbers = workbench._sequential_sku_numbers(  # noqa: SLF001
        selected_skus,
        draft["itemNum"],
    )
    updated_skus: dict[str, Any] = {}
    expected_sku_labels = _expected_miaoshou_sku_labels(
        payload,
        selected_sku_keys,
    )
    for key, value in selected_skus.items():
        sku = dict(value)
        sku.update(
            {
                "itemNum": sku_numbers[key],
                "weight": draft["weight"],
                "packageLength": draft["packageLength"],
                "packageWidth": draft["packageWidth"],
                "packageHeight": draft["packageHeight"],
            }
        )
        normalized_key = str(key).strip(";")
        expected_label = expected_sku_labels.get(normalized_key, normalized_key)
        label_fields = [
            field
            for field in ("specLabel", "specName", "skuName")
            if field in sku
        ]
        for field in label_fields:
            sku[field] = expected_label
        updated_skus[key] = sku
    updated = dict(current)
    for field in (
        "title",
        "itemNum",
        "weight",
        "packageLength",
        "packageWidth",
        "packageHeight",
        "imgUrls",
        "notes",
        "mainImgVideoUrl",
    ):
        updated[field] = draft[field]
    updated["skuMap"] = updated_skus
    workbench._filter_miaoshou_variant_maps(updated, updated_skus)  # noqa: SLF001
    try:
        save_response = post(
            MIAOSHOU_COMMON_EDIT_PATH,
            {
                "commonCollectBoxDetailId": detail_id,
                "editCommonCollectBoxDetail": updated,
                "ossMd5": oss_md5,
            },
        )
    except Exception as error:
        raise MiaoshouDraftVerificationError(
            (
                "Miaoshou COMMON immutable plan write outcome is unknown "
                f"after dispatch: {error}"
            ),
            external_reference=str(payload["product_id"]),
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "save_accepted": False,
                "offer_id": str(payload["product_id"]),
                "detail_id": detail_id,
                "write_outcome": "unknown_after_dispatch",
                "save_exception": str(error),
                "external_writes_performed": [
                    "miaoshou:COMMON:immutable_plan_write"
                ],
            },
        ) from error
    if save_response.get("result") != "success":
        raise RuntimeError(
            "Miaoshou COMMON immutable plan write was rejected: "
            f"{save_response.get('code')} {save_response.get('message') or ''}"
        )
    try:
        readback = readback_miaoshou_common(payload, post=post)
    except Exception as error:
        raise MiaoshouDraftVerificationError(
            (
                "Miaoshou COMMON immutable plan write was accepted but "
                f"verification raised: {error}"
            ),
            external_reference=str(payload["product_id"]),
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "save_accepted": True,
                "offer_id": str(payload["product_id"]),
                "external_writes_performed": [
                    "miaoshou:COMMON:immutable_plan_write"
                ],
                "verification_error": str(error),
            },
        ) from error
    return {
        "offer_id": str(payload["product_id"]),
        "detail_id": detail_id,
        "written_to_miaoshou": True,
        "verified": bool(readback.get("verified")),
        "checks": dict(readback.get("checks") or {}),
        "field_diffs": dict(readback.get("field_diffs") or {}),
        "spec_label_application": dict(
            readback.get("spec_label_application") or {}
        ),
        "draft": draft,
        "readback": readback,
        "external_writes_performed": ["miaoshou:COMMON:immutable_plan_write"],
    }


def _prepare_existing_miaoshou_target_from_plan(
    payload: dict[str, Any],
    *,
    site: str,
    resolved: dict[str, Any],
    post,
) -> dict[str, Any]:
    """Write one exact existing detail using only immutable ReleasePlan facts."""

    from modules.sourcing import new_product_workbench as workbench

    shop_id = str(resolved["shop_id"])
    warehouse = post(MIAOSHOU_WAREHOUSE_PATH, {"shopIds": [shop_id]})
    if warehouse.get("result") != "success":
        raise RuntimeError(
            f"Miaoshou warehouse lookup failed for {site}: "
            f"{warehouse.get('code')} {warehouse.get('message') or ''}"
        )
    shop = dict(resolved["shop"])
    shop["warehouses"] = warehouse.get("data") or {}
    target_key = str(resolved["target_key"])
    region = SITE_COUNTRIES[site]
    pricing = _store_price(payload, f"tiktok:{site}")
    draft = _immutable_miaoshou_plan_draft(payload, site=site)
    category_id = "600338"
    write_state = {
        "accepted": False,
        "dispatched": False,
        "outcome": "not_dispatched",
    }
    prior_writes = list(
        dict.fromkeys(resolved.get("external_writes_performed") or ())
    )

    def tracked_post(path, body):
        is_save = (
            "save_site_collect_item_info" in str(path)
            or "save_shop_collect_item_info" in str(path)
        )
        if is_save:
            write_state["dispatched"] = True
            write_state["outcome"] = "unknown_after_dispatch"
        response = post(path, body)
        if (
            is_save
            and response.get("result") == "success"
        ):
            write_state["accepted"] = True
            write_state["outcome"] = "accepted"
        elif (
            is_save
        ):
            write_state["outcome"] = "rejected"
        return response

    try:
        if site in SEA_SITES:
            prepared = workbench._prepare_site_mode_draft(
                tracked_post,
                detail_id=int(resolved["detail_id"]),
                region=region,
                region_targets=[(target_key, shop, pricing)],
                draft=draft,
                category_id=category_id,
                cod_enabled=True,
                strict_selected_skus=True,
            )
        else:
            prepared = workbench._prepare_shop_mode_draft(
                tracked_post,
                detail_id=int(resolved["detail_id"]),
                region=region,
                shop=shop,
                pricing=pricing,
                draft=draft,
                category_id=category_id,
                cod_enabled=False,
                claim_shop_ids=[],
                allow_claim_repair=False,
                strict_selected_skus=True,
            )
    except Exception as error:
        if getattr(error, "external_write_evidence", None):
            raise
        if (
            prior_writes
            or write_state["accepted"]
            or write_state["dispatched"]
        ):
            outcome_detail = (
                "was accepted but verification raised"
                if write_state["accepted"]
                else (
                    "was dispatched but its durable outcome is not safely "
                    "retryable"
                )
            )
            raise MiaoshouDraftVerificationError(
                (
                    f"Miaoshou {site} immutable draft update "
                    f"{outcome_detail}: {error}"
                ),
                external_reference=(
                    f"{int(resolved['detail_id'])}:{int(resolved['shop_id'])}"
                ),
                evidence={
                    "source": "miaoshou_open_api",
                    "verified": False,
                    "save_accepted": bool(write_state["accepted"]),
                    "detail_id": int(resolved["detail_id"]),
                    "shop_id": int(resolved["shop_id"]),
                    "write_outcome": str(write_state["outcome"]),
                    "verification_error": str(error),
                    "external_writes_performed": list(
                        dict.fromkeys(
                            [
                                *prior_writes,
                                *(
                                    ["miaoshou:tiktok_detail:update"]
                                    if (
                                        write_state["accepted"]
                                        or write_state["dispatched"]
                                    )
                                    else []
                                ),
                            ]
                        )
                    ),
                },
            ) from error
        raise
    if not prepared.get("ready"):
        failed = [
            str(key)
            for key, passed in (prepared.get("checks") or {}).items()
            if not passed
        ]
        raise MiaoshouDraftVerificationError(
            (
                f"Miaoshou {site} immutable draft update was accepted but "
                "readback did not verify: "
                + ", ".join(failed or ["unknown fields"])
            ),
            external_reference=(
                f"{int(resolved['detail_id'])}:{int(resolved['shop_id'])}"
            ),
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "save_accepted": True,
                "detail_id": int(resolved["detail_id"]),
                "shop_id": int(resolved["shop_id"]),
                "checks": dict(prepared.get("checks") or {}),
                "external_writes_performed": list(
                    dict.fromkeys(
                        [
                            *prior_writes,
                            "miaoshou:tiktok_detail:update",
                        ]
                    )
                ),
                "readback": dict(prepared),
            },
        )
    prepared["external_writes_performed"] = list(
        dict.fromkeys(
            [
                *prior_writes,
                "miaoshou:tiktok_detail:update",
            ]
        )
    )
    return prepared


def _prepare_missing_miaoshou_tiktok_claim(
    payload: dict[str, Any],
    *,
    site: str,
    post,
) -> dict[str, Any]:
    """Create one site-isolated TikTok draft from verified COMMON exactly once."""

    from modules.sourcing import new_product_workbench as workbench

    clean_site = str(site or "").upper()
    target_key = SITE_TARGET_KEYS.get(clean_site)
    target = next(
        (
            dict(row)
            for row in workbench.SEA_MARKETS
            if str(row.get("id") or "").lower() == target_key
        ),
        None,
    )
    if not target:
        raise MiaoshouPreSubmitError(
            f"fixed Miaoshou target configuration is missing for {clean_site}",
            reason_code="miaoshou_target_configuration_missing",
        )
    product_id = str(payload.get("product_id") or "").strip()
    seller_sku = str(payload.get("seller_sku") or "").strip()
    source_item_id = str(
        (payload.get("product_facts") or {}).get("source_offer_id") or ""
    ).strip()
    shop_id = str(target.get("shop_id") or "").strip()
    detail_group = workbench._anchor_group_key(target)  # noqa: SLF001
    if not product_id or not seller_sku or not source_item_id or not shop_id:
        raise MiaoshouPreSubmitError(
            "immutable plan lacks TikTok draft preparation identity",
            reason_code="miaoshou_tiktok_preparation_identity_missing",
        )
    claim_path = workbench.STATE_DIR / f"{product_id}_tiktok_claim.json"
    existing_claim = workbench._load_json(claim_path) or {}  # noqa: SLF001
    known_detail_ids: set[int] = set()
    raw_known_ids = existing_claim.get("detail_group_detail_ids") or {}
    if not isinstance(raw_known_ids, dict):
        raise MiaoshouPreSubmitError(
            "persisted TikTok detail identity is malformed",
            reason_code="miaoshou_tiktok_existing_identity_unresolved",
        )
    for value in raw_known_ids.values():
        if (
            isinstance(value, bool)
            or not str(value or "").isdigit()
            or int(value) <= 0
        ):
            raise MiaoshouPreSubmitError(
                "persisted TikTok detail identity is malformed",
                reason_code="miaoshou_tiktok_existing_identity_unresolved",
            )
        known_detail_ids.add(int(value))

    try:
        search = post(
            MIAOSHOU_TIKTOK_DETAIL_LIST_PATH,
            {
                "pageNo": 1,
                "pageSize": 100,
                "filter": {"sourceItemIdKeyword": source_item_id},
            },
        )
    except Exception as error:
        raise MiaoshouPreSubmitError(
            f"Miaoshou TikTok duplicate scan failed for {clean_site}: {error}",
            reason_code="miaoshou_tiktok_duplicate_scan_failed",
        ) from error
    try:
        rows = _complete_miaoshou_tiktok_detail_rows(search)
    except _MiaoshouDetailListContractError as error:
        raise MiaoshouPreSubmitError(
            str(error),
            reason_code=error.reason_code,
        ) from error
    observed_detail_ids: set[int] = set()
    for row in rows:
        raw_detail_id = row.get("collectBoxDetailId") or row.get("detailId")
        if (
            isinstance(raw_detail_id, bool)
            or not str(raw_detail_id or "").isdigit()
            or int(raw_detail_id) <= 0
        ):
            raise MiaoshouPreSubmitError(
                "Miaoshou TikTok duplicate scan identity is malformed",
                reason_code="miaoshou_tiktok_duplicate_scan_malformed",
            )
        observed_detail_ids.add(int(raw_detail_id))
    if (
        len(observed_detail_ids) != len(rows)
        or not observed_detail_ids.issubset(known_detail_ids)
    ):
        raise MiaoshouPreSubmitError(
            (
                "Miaoshou TikTok details already exist but no exact persisted "
                "claim identity is available"
            ),
            reason_code="miaoshou_tiktok_existing_identity_unresolved",
        )

    create_class = "miaoshou:tiktok_detail:create"
    try:
        create_response = post(
            "/open/v1/product/common_collect_box/common_collect_box/claimed",
            {
                "detailSerialNumberPlatformList": [
                    {
                        "detailId": int(product_id),
                        "platform": "tiktok",
                        "serialNumber": int(
                            workbench._claim_serial_number(  # noqa: SLF001
                                [(target_key, target, shop_id)]
                            )
                        ),
                    }
                ]
            },
        )
        if not isinstance(create_response, dict):
            raise TypeError("detail creation response is not a mapping")
        create_result = create_response.get("result")
    except Exception as error:
        raise MiaoshouDraftVerificationError(
            f"Miaoshou {clean_site} detail creation outcome is unknown: {error}",
            external_reference=product_id,
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "write_outcome": "detail_create_unknown_after_dispatch",
                "external_writes_performed": [create_class],
            },
        ) from error
    if create_result != "success":
        raise MiaoshouDraftVerificationError(
            f"Miaoshou {clean_site} detail creation was not confirmed",
            external_reference=product_id,
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "write_outcome": "detail_create_not_confirmed",
                "external_writes_performed": [create_class],
            },
        )
    create_data = create_response.get("data")
    platform_map_root = (
        create_data.get("platformCollectBoxDetailIdMap")
        if isinstance(create_data, dict)
        else None
    )
    platform_map = (
        platform_map_root.get("tiktok")
        if isinstance(platform_map_root, dict)
        else None
    )
    if not isinstance(platform_map, dict):
        platform_map = {}
    detail_id = (
        platform_map.get(product_id)
        or platform_map.get(int(product_id))
    )
    try:
        detail_id = int(detail_id)
    except (TypeError, ValueError):
        detail_id = 0
    write_classes = [create_class]
    if detail_id <= 0:
        raise MiaoshouDraftVerificationError(
            f"Miaoshou {clean_site} detail creation returned no identity",
            external_reference=product_id,
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "write_outcome": "detail_created_identity_missing",
                "external_writes_performed": list(write_classes),
            },
        )

    claim_class = "miaoshou:tiktok_shop:claim"
    try:
        claim_response = post(
            "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop",
            {"detailIds": [detail_id], "shopIds": [shop_id]},
        )
        if not isinstance(claim_response, dict):
            raise TypeError("shop claim response is not a mapping")
        claim_result = claim_response.get("result")
    except Exception as error:
        raise MiaoshouDraftVerificationError(
            f"Miaoshou {clean_site} shop claim outcome is unknown: {error}",
            external_reference=f"{detail_id}:{shop_id}",
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "write_outcome": "shop_claim_unknown_after_dispatch",
                "external_writes_performed": [
                    *write_classes,
                    claim_class,
                ],
            },
        ) from error
    if claim_result != "success":
        raise MiaoshouDraftVerificationError(
            f"Miaoshou {clean_site} shop claim was not confirmed",
            external_reference=f"{detail_id}:{shop_id}",
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "write_outcome": "shop_claim_not_confirmed",
                "external_writes_performed": [
                    *write_classes,
                    claim_class,
                ],
            },
        )
    write_classes.append(claim_class)

    existing = workbench._load_json(claim_path) or {}  # noqa: SLF001
    detail_ids = dict(existing.get("detail_group_detail_ids") or {})
    if (
        detail_group in detail_ids
        and int(detail_ids[detail_group]) != detail_id
    ):
        raise MiaoshouDraftVerificationError(
            "persisted TikTok detail identity changed after creation",
            external_reference=f"{detail_id}:{shop_id}",
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "write_outcome": "local_identity_conflict_after_claim",
                "external_writes_performed": list(write_classes),
            },
        )
    detail_ids[detail_group] = detail_id
    shops = dict(existing.get("shops") or {})
    shops[target_key] = {
        "shop_id": shop_id,
        "shop": target.get("shop"),
        "region": target.get("region"),
        "publish_group": target.get("publish_group"),
        "detail_group": detail_group,
        "detail_id": detail_id,
        "claimed": True,
    }
    receipt = {
        **existing,
        "ok": True,
        "offer_id": product_id,
        "common_detail_id": int(product_id),
        "source_item_id": source_item_id,
        "seller_sku": seller_sku,
        "detail_group_detail_ids": detail_ids,
        "publish_group_detail_ids": detail_ids,
        "shops": shops,
        "claimed": True,
        "published": False,
        "in_progress": False,
        "last_error": "",
        "updated_at": workbench._now(),  # noqa: SLF001
    }
    try:
        workbench._write_json_atomic(claim_path, receipt)  # noqa: SLF001
    except Exception as error:
        raise MiaoshouDraftVerificationError(
            f"Miaoshou {clean_site} claim receipt could not be persisted: {error}",
            external_reference=f"{detail_id}:{shop_id}",
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "write_outcome": "claim_succeeded_local_receipt_failed",
                "external_writes_performed": list(write_classes),
            },
        ) from error
    return {
        "detail_id": detail_id,
        "shop_id": int(shop_id),
        "external_writes_performed": list(write_classes),
    }


def _miaoshou_publish_target(
    payload: dict[str, Any],
    *,
    site: str,
) -> tuple[str, dict[str, Any]]:
    from modules.miaoshou.client import post_open

    preparation_writes: list[str] = []
    try:
        resolved = _resolve_existing_miaoshou_tiktok_detail(
            payload,
            site=site,
            post=post_open,
        )
    except MiaoshouPreSubmitError as error:
        if error.reason_code != "miaoshou_tiktok_detail_missing":
            raise
        prepared_claim = _prepare_missing_miaoshou_tiktok_claim(
            payload,
            site=site,
            post=post_open,
        )
        preparation_writes = list(
            dict.fromkeys(
                prepared_claim.get("external_writes_performed") or ()
            )
        )
        readback_error: Exception | None = None
        for attempt in range(3):
            if attempt:
                time.sleep(1)
            try:
                resolved = _resolve_existing_miaoshou_tiktok_detail(
                    payload,
                    site=site,
                    post=post_open,
                )
                readback_error = None
                break
            except Exception as error:
                readback_error = error
        if readback_error is not None:
            raise MiaoshouDraftVerificationError(
                (
                    f"Miaoshou {site} target claim was written but its exact "
                    f"identity could not be read back: {readback_error}"
                ),
                external_reference=str(
                    prepared_claim.get("detail_id") or payload["product_id"]
                ),
                evidence={
                    "source": "miaoshou_open_api",
                    "verified": False,
                    "submission_accepted": False,
                    "write_outcome": "claim_written_readback_unverified",
                    "readback_attempts": 3,
                    "external_writes_performed": preparation_writes,
                },
            ) from readback_error
        resolved["external_writes_performed"] = preparation_writes
    prepared_site = _prepare_existing_miaoshou_target_from_plan(
        payload,
        site=site,
        resolved=resolved,
        post=post_open,
    )
    key = SITE_TARGET_KEYS[site]
    detail_id = int(resolved["detail_id"])
    shop_id = int(resolved["shop_id"])
    try:
        audit = _miaoshou_submission_audit(
            payload,
            site=site,
            target_key=key,
            detail_id=detail_id,
            shop_id=shop_id,
            prepared_site=prepared_site,
        )
    except Exception as error:
        draft_writes = list(
            dict.fromkeys(
                prepared_site.get("external_writes_performed") or ()
            )
        ) or ["miaoshou:tiktok_detail:update"]
        raise MiaoshouDraftVerificationError(
            f"Miaoshou {site} draft audit failed after detail update: {error}",
            external_reference=f"{detail_id}:{shop_id}",
            evidence={
                "source": "miaoshou_open_api",
                "verified": False,
                "verified_draft": True,
                "submission_accepted": False,
                "detail_id": detail_id,
                "shop_id": shop_id,
                "write_outcome": "draft_saved_pre_submit_audit_failed",
                "external_writes_performed": draft_writes,
            },
        ) from error
    draft_writes = list(
        dict.fromkeys(
            prepared_site.get("external_writes_performed") or ()
        )
    ) or ["miaoshou:tiktok_detail:update"]
    publish_writes = list(
        dict.fromkeys(
            [
                *draft_writes,
                "miaoshou:tiktok_publish:submission",
            ]
        )
    )
    failure_evidence = {
        "source": "miaoshou_open_api",
        "verified": False,
        "save_accepted": True,
        "verified_draft": True,
        "detail_id": detail_id,
        "shop_id": shop_id,
        "pre_submit_audit": audit,
        "publish_dispatched": True,
        "external_writes_performed": publish_writes,
    }
    try:
        response = post_open(
            MIAOSHOU_PUBLISH_PATH,
            {"detailIds": [detail_id], "shopIds": [shop_id]},
        )
    except Exception as error:
        raise MiaoshouDraftVerificationError(
            (
                f"Miaoshou {site} publish dispatch outcome is unknown: "
                f"{error}"
            ),
            external_reference=f"{detail_id}:{shop_id}",
            evidence={
                **failure_evidence,
                "write_outcome": "unknown_after_dispatch",
                "publish_exception": str(error),
            },
        ) from error
    try:
        if not isinstance(response, dict):
            raise ValueError("publish response is not a mapping")
        publish_result = response.get("result")
    except Exception as error:
        raise MiaoshouDraftVerificationError(
            f"Miaoshou {site} publish response is ambiguous: {error}",
            external_reference=f"{detail_id}:{shop_id}",
            evidence={
                **failure_evidence,
                "write_outcome": "unknown_after_dispatch",
            },
        ) from error
    if publish_result != "success":
        raise MiaoshouDraftVerificationError(
            (
                f"Miaoshou {site} publish submission failed: "
                f"{response.get('code')} {response.get('message') or ''}"
            ),
            external_reference=f"{detail_id}:{shop_id}",
            evidence={
                **failure_evidence,
                "write_outcome": "draft_saved_publish_rejected",
                "publish_response": {
                    "result": response.get("result"),
                    "code": response.get("code"),
                    "message": response.get("message"),
                },
            },
        )
    return f"{detail_id}:{shop_id}", {
        "source": "miaoshou_open_api",
        "accepted": True,
        "detail_id": detail_id,
        "shop_id": shop_id,
        "response_code": response.get("code"),
        "pre_submit_audit": audit,
        "write_outcome": "submission_accepted",
        "external_writes_performed": publish_writes,
    }


def _resolve_existing_miaoshou_tiktok_detail(
    payload: dict[str, Any],
    *,
    site: str,
    post=None,
) -> dict[str, Any]:
    """Resolve one already-created detail without any claim/create fallback."""

    from modules.sourcing import new_product_workbench as workbench

    clean_site = str(site or "").upper()
    target_key = SITE_TARGET_KEYS.get(clean_site)
    if not target_key:
        raise RuntimeError(f"unsupported governed TikTok site {clean_site}")
    target = next(
        (
            dict(row)
            for row in workbench.SEA_MARKETS
            if str(row.get("id") or "").lower() == target_key
        ),
        None,
    )
    if not target:
        raise RuntimeError(
            f"fixed Miaoshou target configuration is missing for {clean_site}"
        )
    expected_shop_id = str(target.get("shop_id") or "").strip()
    publish_group = str(target.get("publish_group") or "").strip().lower()
    region = str(target.get("region") or "").strip().upper()
    detail_group = f"{publish_group}:{region}"
    if not expected_shop_id or not publish_group or not region:
        raise RuntimeError(
            f"fixed Miaoshou target configuration is incomplete for {clean_site}"
        )

    claim = workbench.load_miaoshou_tiktok_claim(
        str(payload.get("product_id") or "")
    )
    product_id = str(payload.get("product_id") or "").strip()
    seller_sku = str(payload.get("seller_sku") or "").strip()
    source_item_id = str(
        claim.get("source_item_id")
        or (payload.get("product_facts") or {}).get("source_offer_id")
        or ""
    ).strip()
    if not product_id or not seller_sku or not source_item_id:
        raise MiaoshouPreSubmitError(
            "immutable plan lacks product/SKU/source identity",
            reason_code="miaoshou_tiktok_preparation_identity_missing",
        )
    detail_map = claim.get("detail_group_detail_ids")
    if not isinstance(detail_map, dict):
        raise MiaoshouPreSubmitError(
            "persisted Miaoshou claim lacks detail_group_detail_ids",
            reason_code="miaoshou_tiktok_detail_missing",
        )
    normalized: dict[str, int] = {}
    for key, value in detail_map.items():
        try:
            detail_id = int(value)
        except (TypeError, ValueError):
            detail_id = 0
        if detail_id <= 0:
            raise RuntimeError(
                f"persisted Miaoshou detail ID is invalid for {key}"
            )
        normalized[str(key)] = detail_id
    duplicated = sorted(
        {
            detail_id
            for detail_id in normalized.values()
            if list(normalized.values()).count(detail_id) > 1
        }
    )
    if duplicated:
        raise RuntimeError(
            "persisted Miaoshou detail IDs are not unique: "
            + ", ".join(str(value) for value in duplicated)
        )
    detail_id = int(normalized.get(detail_group) or 0)
    if not detail_id:
        raise MiaoshouPreSubmitError(
            f"persisted Miaoshou detail ID is missing for {detail_group}",
            reason_code="miaoshou_tiktok_detail_missing",
        )

    if post is None:
        from modules.miaoshou.client import post_open

        post = post_open
    response = post(
        MIAOSHOU_SHOP_DETAIL_PATH,
        {"detailId": detail_id, "shopId": expected_shop_id},
    )
    if response.get("result") != "success":
        raise RuntimeError(
            f"Miaoshou existing detail lookup failed for {clean_site}: "
            f"{response.get('code')} {response.get('message') or ''}"
        )
    data = response.get("data") or {}
    info = data.get("shopCollectItemInfo")
    if not isinstance(info, dict) or not info:
        raise RuntimeError(
            f"Miaoshou existing detail lookup returned no shop item for {clean_site}"
        )
    claim_shop_ids = {
        str(value).strip()
        for value in (data.get("claimToShopIds") or ())
        if str(value).strip()
    }
    explicit_shop_id = str(info.get("shopId") or "").strip()
    returned_detail_id = str(info.get("detailId") or "").strip()
    if returned_detail_id != str(detail_id):
        raise RuntimeError(
            f"Miaoshou detail identity {returned_detail_id or 'missing'} "
            f"does not match mapped detail {detail_id}"
        )
    if explicit_shop_id != expected_shop_id:
        raise RuntimeError(
            f"Miaoshou detail {detail_id} shop {explicit_shop_id or 'missing'} "
            f"does not match fixed shop {expected_shop_id}"
        )
    if claim_shop_ids != {expected_shop_id}:
        raise RuntimeError(
            f"Miaoshou detail {detail_id} is not bound to fixed shop "
            f"{expected_shop_id} for {clean_site}"
        )
    returned_common_id = str(
        info.get("commonCollectBoxDetailId")
        or data.get("commonCollectBoxDetailId")
        or ""
    ).strip()
    if returned_common_id and returned_common_id != product_id:
        raise RuntimeError(
            f"Miaoshou detail {detail_id} belongs to common product "
            f"{returned_common_id}, expected {product_id}"
        )
    sku_map = info.get("skuMap")
    if not isinstance(sku_map, dict) or not sku_map:
        raise RuntimeError(
            f"Miaoshou detail {detail_id} did not return verifiable SKU identity"
        )
    selected_count = len(
        (payload.get("product_facts") or {}).get("selected_sku_keys") or ()
    )
    if selected_count < 1:
        raise RuntimeError("immutable release plan has no selected SKU identity")
    base_number = int(seller_sku)
    expected_item_nums = {
        str((base_number + offset) % 10000).zfill(4)
        for offset in range(selected_count)
    }
    actual_item_nums = {
        str(row.get("itemNum") or "").strip()
        for row in sku_map.values()
        if isinstance(row, dict) and str(row.get("itemNum") or "").strip()
    }
    if actual_item_nums != expected_item_nums:
        raise RuntimeError(
            f"Miaoshou detail {detail_id} variant SKU scheme "
            f"{sorted(actual_item_nums)} does not match "
            f"{sorted(expected_item_nums)}"
        )

    search = post(
        MIAOSHOU_TIKTOK_DETAIL_LIST_PATH,
        {
            "pageNo": 1,
            "pageSize": 100,
            "filter": {"sourceItemIdKeyword": source_item_id},
        },
    )
    if search.get("result") != "success":
        raise RuntimeError(
            f"Miaoshou existing detail uniqueness lookup failed for {clean_site}: "
            f"{search.get('code')} {search.get('message') or ''}"
        )
    try:
        rows = _complete_miaoshou_tiktok_detail_rows(search)
    except _MiaoshouDetailListContractError as error:
        raise RuntimeError(str(error)) from error
    row_detail_ids = {
        int(row.get("collectBoxDetailId") or row.get("detailId") or 0)
        for row in rows
        if isinstance(row, dict)
        and int(row.get("collectBoxDetailId") or row.get("detailId") or 0) > 0
    }
    if (
        len(rows) != len(normalized)
        or row_detail_ids != set(normalized.values())
    ):
        raise RuntimeError(
            "Miaoshou source-item lookup does not exactly match the persisted "
            "detail group identity set"
        )
    matches: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_common = str(row.get("commonCollectBoxDetailId") or "").strip()
        row_item_num = str(row.get("itemNum") or "").strip()
        row_shop_ids = {
            str(shop_row.get("shopId") or "").strip()
            for shop_row in (row.get("collectBoxDetailShopList") or ())
            if isinstance(shop_row, dict)
            and str(shop_row.get("shopId") or "").strip()
        }
        if (
            row_common == product_id
            and row_item_num == seller_sku
            and expected_shop_id in row_shop_ids
        ):
            raw_detail_id = row.get("collectBoxDetailId") or row.get("detailId")
            if not raw_detail_id:
                raise RuntimeError(
                    "Miaoshou uniqueness lookup returned an unverifiable detail"
                )
            matches.append(int(raw_detail_id))
    if matches != [detail_id]:
        raise RuntimeError(
            f"Miaoshou {clean_site} detail identity is not unique and exact: "
            f"mapped={detail_id}, matches={sorted(matches)}"
        )
    common_identity_provenance = "search_row"
    if returned_common_id:
        common_identity_provenance = "search_row_and_shop_detail"
    return {
        "site": clean_site,
        "target_key": target_key,
        "detail_group": detail_group,
        "detail_id": detail_id,
        "shop_id": int(expected_shop_id),
        "shop": target,
        "shop_collect_item_info": dict(info),
        "oss_md5": str(data.get("ossMd5") or ""),
        "common_identity": product_id,
        "common_identity_provenance": common_identity_provenance,
        "source": (
            "persisted_detail_group_ids_plus_readonly_shop_lookup_plus_"
            + common_identity_provenance
        ),
        "external_writes_performed": [],
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
    receipt = ((target or {}).get("submission") or {}).get("evidence") or {}
    detail_id, _, shop_id = external_id.partition(":")
    return external_id, {
        **dict(receipt),
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
    country = SITE_COUNTRIES[site]
    expected_title = _candidate(payload, "tiktok", country)
    expected_price = _store_price(payload, request.target_label).get("list_price")
    if expected_price in (None, ""):
        raise RuntimeError(f"approved TikTok price is missing for {request.target_label}")

    if site in SUBMISSION_ONLY_TIKTOK_SITES:
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
                True,
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
            try:
                verified, corrected = _tiktok_readback(
                    seller_sku=request.seller_sku,
                    region=country,
                    expected_title=expected_title,
                    expected_price=expected_price,
                    expected_image_count=len(context["images"]),
                    expected_category_id="600338",
                )
            except Exception as error:
                product_id = str(evidence.get("product_id") or "")
                raise ReleaseAdapterWriteVerificationError(
                    (
                        f"TikTok {country} title repair was accepted, but "
                        f"official readback raised: {error}"
                    ),
                    external_reference=product_id,
                    evidence={
                        **repair,
                        "source": "official_tiktok_shop_api",
                        "verified": False,
                        "write_outcome": (
                            "title_repair_accepted_readback_unknown"
                        ),
                        "readback_error": str(error),
                        "external_writes_performed": list(
                            repair.get("external_writes_performed")
                            or ["tiktok:official_title_partial_edit"]
                        ),
                    },
                ) from error
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
    if site in SUBMISSION_ONLY_TIKTOK_SITES:
        return AdapterExecutionResult(
            True,
            False,
            (
                f"Miaoshou accepted {site}, but no authorised official TikTok "
                "readback exists for this account; target remains unverified"
            ),
            external_reference,
            submission,
            True,
        )

    last_evidence: dict[str, Any] = submission
    title_repair: dict[str, Any] | None = None
    for attempt in range(24):
        if attempt:
            time.sleep(10)
        try:
            verified, evidence = _tiktok_readback(
                seller_sku=request.seller_sku,
                region=country,
                expected_title=expected_title,
                expected_price=expected_price,
                expected_image_count=len(context["images"]),
                expected_category_id="600338",
            )
        except Exception as error:
            writes = list(
                dict.fromkeys(
                    [
                        *(
                            submission.get("external_writes_performed")
                            or ()
                        ),
                        *(
                            (
                                title_repair.get(
                                    "external_writes_performed"
                                )
                                or ()
                            )
                            if title_repair
                            else ()
                        ),
                        "miaoshou:tiktok_publish:submission",
                    ]
                )
            )
            raise MiaoshouDraftVerificationError(
                (
                    f"Miaoshou accepted {site}, but official TikTok readback "
                    f"raised after submission: {error}"
                ),
                external_reference=external_reference,
                evidence={
                    **submission,
                    "source": "miaoshou_open_api_then_tiktok_official_api",
                    "verified": False,
                    "submission_accepted": True,
                    "write_outcome": (
                        "submission_accepted_readback_unknown"
                    ),
                    "official_readback_error": str(error),
                    "external_writes_performed": writes,
                    **(
                        {"repair": title_repair}
                        if title_repair
                        else {}
                    ),
                },
            ) from error
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


SHOPEE_LOCAL_CURRENCIES = {
    "PH": "PHP",
    "MY": "MYR",
    "TH": "THB",
    "VN": "VND",
}


def _shopee_price_expectation(
    pricing: dict[str, Any],
    *,
    region: str,
) -> dict[str, Any]:
    """Resolve the immutable regional listing-price contract.

    The regional ``update_price`` endpoint owns only the local-currency model
    ``original_price``. Shopee derives SIP independently, so SIP is retained
    as a non-writable observation and never authorizes or rejects a regional
    price write.
    """

    site = region.upper()
    target_currency = SHOPEE_LOCAL_CURRENCIES.get(site)
    if not target_currency:
        raise RuntimeError(f"unsupported Shopee price region: {region}")
    if str(pricing.get("target_site") or "").upper() != site:
        raise RuntimeError("approved Shopee pricing target_site does not match region")

    derived = pricing.get("derived_preview") or {}
    expected_cny = _decimal(derived.get("global_original_price_cny"))
    source_local = _decimal(derived.get("local_original_price"))
    exchange_rate = _decimal(derived.get("exchange_rate_cny_per_local"))
    source_currency = str(derived.get("source_currency") or "").upper()
    if source_currency != target_currency:
        raise RuntimeError(
            "approved Shopee pricing source currency does not match target region"
        )
    if (
        expected_cny is None
        or expected_cny <= 0
        or source_local is None
        or source_local <= 0
        or exchange_rate is None
        or exchange_rate <= 0
    ):
        raise RuntimeError("approved Shopee pricing expectation is incomplete")
    if not _numbers_equal(
        round(source_local * exchange_rate, 2),
        expected_cny,
    ):
        raise RuntimeError("approved Shopee CNY price derivation is inconsistent")

    return {
        "schema_version": "shopee-regional-price-readback/v2",
        "field": "price_info.original_price",
        "value": float(source_local),
        "currency": target_currency,
        "target_local_currency": target_currency,
        "source_local_price": float(source_local),
        "source_local_currency": source_currency,
        "sip_reference_cny": float(expected_cny),
        "exchange_rate_cny_per_local": float(exchange_rate),
        "source_field": "derived_preview.local_original_price",
        "sip_reference_source_field": (
            "derived_preview.global_original_price_cny"
        ),
    }


def _shopee_observed_price_rows(
    *,
    item: dict[str, Any],
    models: list[dict[str, Any]],
    match_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the one SKU-bound price row without mixing item/model scopes."""

    issues: list[str] = []
    if item.get("has_model"):
        matched_models = [
            model
            for model in models
            if str(model.get("model_sku") or "") == match_key
        ]
        if len(matched_models) != 1:
            return [], ["matching_model_price_scope_is_not_unique"]
        matched_model = matched_models[0]
        model_id = str(matched_model.get("model_id") or "").strip()
        if not model_id.isdigit():
            return [], ["matching_model_id_is_not_exact"]
        source_rows = list(matched_model.get("price_info") or ())
        scope = "model"
    else:
        source_rows = list(item.get("price_info") or ())
        scope = "item"
        model_id = ""
        if str(item.get("item_sku") or "") != match_key:
            issues.append("item_sku_does_not_match_price_scope")

    if not source_rows and scope == "item":
        source_rows = [
            {
                "currency": item.get("currency"),
                "original_price": item.get("original_price"),
                "current_price": item.get("price"),
                "sip_item_price": item.get("sip_item_price"),
            }
        ]

    observed: list[dict[str, Any]] = []
    for row in source_rows:
        if not isinstance(row, dict):
            issues.append("price_info_row_is_not_an_object")
            continue
        observed.append(
            {
                "scope": scope,
                "model_id": model_id,
                "currency": str(
                    row.get("currency") or item.get("currency") or ""
                ).upper(),
                "original_price": row.get("original_price"),
                "current_price": row.get("current_price"),
                "inflated_price_of_original_price": row.get(
                    "inflated_price_of_original_price",
                    row.get("inflated_price"),
                ),
                "inflated_price_of_current_price": row.get(
                    "inflated_price_of_current_price"
                ),
                "sip_item_price": row.get("sip_item_price"),
            }
        )
    return observed, issues


def _verify_shopee_price_rows(
    observed: list[dict[str, Any]],
    expectation: dict[str, Any],
    *,
    initial_issues: list[str],
) -> tuple[bool, list[str]]:
    issues = list(initial_issues)
    target_currency = str(expectation.get("target_local_currency") or "")
    eligible = [
        row for row in observed if str(row.get("currency") or "") == target_currency
    ]
    if len(eligible) != 1:
        issues.append("target_currency_price_row_is_not_unique")
        return False, sorted(set(issues))

    row = eligible[0]
    current = _decimal(row.get("current_price"))
    original = _decimal(row.get("original_price"))
    inflated_current = _decimal(row.get("inflated_price_of_current_price"))
    inflated_original = _decimal(row.get("inflated_price_of_original_price"))
    expected_local = _decimal(expectation.get("source_local_price"))
    if current is None or current <= 0:
        issues.append("current_price_is_not_positive")
    elif not _numbers_equal(current, expected_local):
        issues.append("current_price_does_not_match_approved_local_price")
    if original is None or original <= 0:
        issues.append("original_price_is_not_positive")
    elif not _numbers_equal(original, expected_local):
        issues.append("original_price_does_not_match_approved_local_price")
    if current is not None and original is not None and current > original:
        issues.append("current_price_exceeds_original_price")
    if (
        row.get("inflated_price_of_current_price") not in (None, "")
        and (inflated_current is None or inflated_current <= 0)
    ):
        issues.append("inflated_current_price_is_not_positive")
    if (
        row.get("inflated_price_of_original_price") not in (None, "")
        and (inflated_original is None or inflated_original <= 0)
    ):
        issues.append("inflated_original_price_is_not_positive")
    if (
        inflated_current is not None
        and inflated_original is not None
        and inflated_current > inflated_original
    ):
        issues.append("inflated_current_price_exceeds_inflated_original_price")
    return not issues, sorted(set(issues))


def _shopee_platform_derived_price_observation(
    row: dict[str, Any],
    expectation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Describe Shopee-derived SIP without treating it as writable truth."""

    observed = _decimal(row.get("sip_item_price"))
    reference = _decimal(expectation.get("sip_reference_cny"))
    matched = bool(
        observed is not None
        and reference is not None
        and _numbers_equal(observed, reference)
    )
    delta = (
        observed - reference
        if observed is not None and reference is not None
        else None
    )
    pct = (
        (delta / reference) * Decimal("100")
        if delta is not None and reference not in (None, Decimal("0"))
        else None
    )
    observation = {
        "kind": "platform_derived_observation",
        "field": "price_info.sip_item_price",
        "writable": False,
        "authority": "shopee",
        "currency": "CNY",
        "observed": str(observed) if observed is not None else None,
        "reference": str(reference) if reference is not None else None,
        "delta": str(delta) if delta is not None else None,
        "pct": str(pct.quantize(Decimal("0.01"))) if pct is not None else None,
        "source": "official_shopee_partner_api",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    observation["evidence_digest"] = hashlib.sha256(
        json.dumps(
            observation,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    warning = None
    if not matched:
        warning = {
            "code": "shopee_sip_platform_derived_variance",
            "severity": "warning",
            "writable": False,
            "authority": "shopee",
            "message": (
                "Shopee-derived SIP differs from the plan reference; "
                "listing price remains independently verifiable"
            ),
        }
    return observation, warning


def _shopee_readback_credentials(
    region: str,
    *,
    allow_token_refresh: bool,
) -> tuple[int, str]:
    if allow_token_refresh:
        from modules.shopee.auth import ensure_shop_token
        from modules.shopee.publish import sync_shop_ids

        shop_id = int(sync_shop_ids()[region.upper()])
        return shop_id, ensure_shop_token(shop_id)

    from modules.shopee.auth import load_tokens

    store = load_tokens()
    shop_id = int((store.get("sync_shop_ids") or {}).get(region.upper()) or 0)
    entry = (store.get("shops") or {}).get(str(shop_id)) or {}
    token = str(entry.get("access_token") or "").strip()
    if not shop_id or not token:
        raise RuntimeError(
            f"Shopee {region.upper()} read-only reconciliation token is unavailable"
        )
    if int(entry.get("expire_at") or 0) < int(time.time()) + 120:
        raise RuntimeError(
            f"Shopee {region.upper()} read-only reconciliation token is expired"
        )
    return shop_id, token


def _shopee_readback(
    *,
    match_key: str,
    region: str,
    item_id: str,
    expected_title: str,
    expected_price: object,
    expected_image_count: int,
    expected_description: str = "",
    require_model_sku: bool = True,
    require_all_logistics: bool = False,
    expected_enabled_logistic_ids: object = None,
    allow_token_refresh: bool = True,
) -> tuple[bool, dict[str, Any]]:
    from modules.shopee.client import shop_get

    shop_id, token = _shopee_readback_credentials(
        region,
        allow_token_refresh=allow_token_refresh,
    )
    base = shop_get(
        "/api/v2/product/get_item_base_info",
        shop_id,
        token,
        {"item_id_list": str(item_id)},
    )
    base_error = (
        str(base.get("error") or "").strip()
        if isinstance(base, dict)
        else "malformed_response"
    )
    base_response = base.get("response") if isinstance(base, dict) else None
    items = (
        base_response.get("item_list")
        if isinstance(base_response, dict)
        else None
    )
    if (
        base_error not in {"", "-"}
        or not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], dict)
        or str(items[0].get("item_id") or "") != str(item_id)
    ):
        return False, {
            "verified": False,
            "source": "official_shopee_partner_api",
            "region": region,
            "item_id": item_id,
            "reason": "item_base_info_invalid",
        }
    item = items[0]
    models_response = shop_get(
        "/api/v2/product/get_model_list",
        shop_id,
        token,
        {"item_id": int(item_id)},
    )
    models_error = (
        str(models_response.get("error") or "").strip()
        if isinstance(models_response, dict)
        else "malformed_response"
    )
    models_payload = (
        models_response.get("response")
        if isinstance(models_response, dict)
        else None
    )
    models = (
        models_payload.get("model")
        if isinstance(models_payload, dict)
        else None
    )
    if (
        models_error not in {"", "-"}
        or not isinstance(models, list)
        or any(not isinstance(model, dict) for model in models)
    ):
        return False, {
            "verified": False,
            "source": "official_shopee_partner_api",
            "region": region,
            "item_id": item_id,
            "reason": "model_list_invalid",
        }
    model_skus = {
        str(model.get("model_sku") or "")
        for model in models
        if model.get("model_sku") not in (None, "")
    }
    seller_skus = {
        str(value)
        for value in [
            item.get("item_sku"),
            *(model.get("model_sku") for model in models),
        ]
        if value not in (None, "")
    }
    item_status = str(item.get("item_status") or "").upper()
    deleted_model_matches = [
        model
        for model in models
        if str(model.get("model_sku") or "") == match_key
    ]
    deleted_model_identity_exact = (
        len(deleted_model_matches) == 1
        and type(deleted_model_matches[0].get("model_id")) is int
        and deleted_model_matches[0]["model_id"] > 0
    )
    if item_status == "SELLER_DELETE":
        deleted_checks = {
            "item_identity": str(item.get("item_id") or "") == str(item_id),
            "seller_sku": match_key in seller_skus,
            "model_identity": deleted_model_identity_exact,
            "deletion_status": True,
        }
        return False, {
            "verified": False,
            "source": "official_shopee_partner_api",
            "authentication_mode": (
                "existing_token_only"
                if not allow_token_refresh
                else "refresh_allowed"
            ),
            "region": region,
            "shop_id": shop_id,
            "item_id": str(item_id),
            "status": item_status,
            "official_item_deleted": True,
            "replacement_release_allowed": all(deleted_checks.values()),
            "reason": (
                "seller_deleted_item_exact"
                if all(deleted_checks.values())
                else "seller_deleted_item_identity_invalid"
            ),
            "checks": deleted_checks,
            "external_writes_performed": [],
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
    description = str(item.get("description") or "")
    logistics = list(item.get("logistic_info") or ())
    logistics_shape_valid = bool(logistics) and all(
        isinstance(row, dict)
        and type(row.get("enabled")) is bool
        and type(row.get("logistic_id")) is int
        and row["logistic_id"] > 0
        for row in logistics
    ) and len(
        {
            row["logistic_id"]
            for row in logistics
            if isinstance(row, dict)
            and type(row.get("logistic_id")) is int
        }
    ) == len(logistics)
    enabled_logistics = [
        {
            "logistic_id": row.get("logistic_id"),
            "logistic_name": row.get("logistic_name"),
        }
        for row in logistics
        if isinstance(row, dict) and row.get("enabled") is True
    ]
    disabled_logistics = [
        {
            "logistic_id": row.get("logistic_id"),
            "logistic_name": row.get("logistic_name"),
        }
        for row in logistics
        if isinstance(row, dict) and row.get("enabled") is False
    ]
    observed_enabled_logistic_ids = sorted(
        row["logistic_id"]
        for row in logistics
        if (
            isinstance(row, dict)
            and row.get("enabled") is True
            and type(row.get("logistic_id")) is int
        )
    )
    expected_logistics_valid = False
    normalized_expected_logistic_ids: list[int] = []
    if isinstance(expected_enabled_logistic_ids, (list, tuple)):
        normalized_expected_logistic_ids = list(expected_enabled_logistic_ids)
        expected_logistics_valid = bool(normalized_expected_logistic_ids) and all(
            type(value) is int and value > 0
            for value in normalized_expected_logistic_ids
        ) and len(set(normalized_expected_logistic_ids)) == len(
            normalized_expected_logistic_ids
        )
        normalized_expected_logistic_ids.sort()
    logistics_policy_satisfied = bool(
        logistics_shape_valid
        and observed_enabled_logistic_ids
        and (
            (
                expected_logistics_valid
                and observed_enabled_logistic_ids
                == normalized_expected_logistic_ids
            )
            if require_all_logistics
            else True
        )
    )
    price_expectation = (
        dict(expected_price)
        if isinstance(expected_price, dict)
        and expected_price.get("schema_version")
        == "shopee-regional-price-readback/v2"
        else None
    )
    observed_price_fields: list[dict[str, Any]] = []
    price_issues: list[str] = []
    derived_observation: dict[str, Any] | None = None
    variance_warning: dict[str, Any] | None = None
    if price_expectation is not None:
        observed_price_fields, price_issues = _shopee_observed_price_rows(
            item=item,
            models=list(models),
            match_key=match_key,
        )
        price_verified, price_issues = _verify_shopee_price_rows(
            observed_price_fields,
            price_expectation,
            initial_issues=price_issues,
        )
        eligible_rows = [
            row
            for row in observed_price_fields
            if str(row.get("currency") or "")
            == str(price_expectation.get("target_local_currency") or "")
        ]
        if len(eligible_rows) == 1:
            (
                derived_observation,
                variance_warning,
            ) = _shopee_platform_derived_price_observation(
                eligible_rows[0],
                price_expectation,
            )
    else:
        price_verified = any(
            _numbers_equal(value, expected_price) for value in price_values
        )
    checks = {
        "seller_sku": match_key in seller_skus,
        "model_sku": (
            match_key in model_skus and bool(item.get("has_model"))
            if require_model_sku
            else True
        ),
        "localized_title": _local_title_matches_region(
            str(item.get("item_name") or ""),
            region=region,
            english_master=expected_title,
        ),
        "rich_localized_description": (
            len(description) >= 500
            and _local_title_matches_region(
                description,
                region=region,
                english_master=expected_description,
            )
            if expected_description
            else True
        ),
        "price": price_verified,
        "image_count": image_count == expected_image_count,
        # Shopee returns both enabled and unavailable channel rows. A disabled
        # locker or other non-applicable channel is not a listing failure.
        # Generic release requires a strict official shape plus at least one
        # enabled channel; target-scoped execution keeps its stronger exact
        # selected-ID contract separately.
        "all_applicable_logistics": logistics_policy_satisfied,
        "status": str(item.get("item_status") or "").upper() in {"NORMAL", "UNLIST"},
    }
    evidence = {
        "verified": all(checks.values()),
        "write_status": (
            "verified" if all(checks.values()) else "unverified"
        ),
        "listing_price_verified": checks["price"] is True,
        "derived_price_status": (
            "warning"
            if variance_warning or derived_observation is None
            else "matched"
        ),
        "profit_status": "unverified",
        "financial_verification_status": (
            "price_verified_profit_unverified"
            if checks["price"] is True
            else "price_unverified_profit_unverified"
        ),
        "source": "official_shopee_partner_api",
        "authentication_mode": (
            "existing_token_only"
            if not allow_token_refresh
            else "refresh_allowed"
        ),
        "region": region,
        "shop_id": shop_id,
        "item_id": str(item_id),
        "seller_skus": sorted(seller_skus),
        "model_skus": sorted(model_skus),
        "has_model": bool(item.get("has_model")),
        "title": item.get("item_name"),
        "description_length": len(description),
        "prices": price_values,
        "expected_price": price_expectation,
        "observed_price_fields": observed_price_fields,
        "price_issues": price_issues,
        "platform_derived_observation": derived_observation,
        "variance_warning": variance_warning,
        "image_count": image_count,
        "logistics_verification_policy": (
            "shopee-publish-receipt-enabled-logistics-exact/v1"
            if require_all_logistics
            else "shopee-official-enabled-logistics-present/v1"
        ),
        "enabled_logistics_count": len(enabled_logistics),
        "enabled_logistic_ids_digest": _miaoshou_value_digest(
            observed_enabled_logistic_ids
        ),
        "expected_enabled_logistic_ids_digest": (
            _miaoshou_value_digest(normalized_expected_logistic_ids)
            if expected_logistics_valid
            else None
        ),
        "logistics": [
            {
                "logistic_id": row.get("logistic_id"),
                "logistic_name": row.get("logistic_name"),
                "enabled": row.get("enabled"),
            }
            for row in logistics
            if isinstance(row, dict)
        ],
        "disabled_logistics": disabled_logistics,
        "status": item.get("item_status"),
        "checks": checks,
    }
    return bool(evidence["verified"]), evidence


def reconcile_existing_shopee_target(
    request: AdapterExecutionRequest,
) -> AdapterExecutionResult:
    """Read back a failed Shopee target without repairing or republishing it."""

    context = _validated_context(request)
    if request.channel != "shopee":
        raise RuntimeError("Shopee reconciliation requires a Shopee target")
    target = context["target"]
    if str(target.get("status") or "") != "FAILED":
        raise RuntimeError("Shopee reconciliation requires a FAILED durable target")
    item_id = str(target.get("external_id") or "").strip()
    if not item_id:
        raise RuntimeError("Shopee reconciliation requires the recorded item_id")

    payload = context["payload"]
    region = request.site.upper()
    pricing = _target_pricing(payload, request.target_label)
    expectation = _shopee_price_expectation(pricing, region=region)
    verified, evidence = _shopee_readback(
        match_key=request.seller_sku[-4:].zfill(4),
        region=region,
        item_id=item_id,
        expected_title=_candidate(payload, "shopee", "CNSC"),
        expected_price=expectation,
        expected_image_count=len(context["images"]),
        expected_description=_shopee_description(payload),
        require_model_sku=False,
        require_all_logistics=False,
        allow_token_refresh=False,
    )
    evidence["reconciliation_mode"] = "read_only_existing_item"
    evidence["external_writes_performed"] = []
    return AdapterExecutionResult(
        succeeded=verified,
        readback_verified=verified,
        detail=(
            f"Shopee {region} existing item matched immutable-plan API readback"
            if verified
            else f"Shopee {region} existing item did not match immutable-plan API readback"
        ),
        external_reference=item_id,
        readback_evidence=evidence,
    )


def _shopee_price_repair_preflight(
    request: AdapterExecutionRequest,
    *,
    allowed_statuses: frozenset[str] = frozenset({"FAILED"}),
) -> dict[str, Any]:
    """Prove one existing PH/TH item differs only in its local price."""

    context = _validated_context(request)
    if request.channel != "shopee" or request.site.upper() not in {"PH", "TH"}:
        raise RuntimeError("Shopee price repair only supports PH and TH")
    target = context["target"]
    if str(target.get("status") or "") not in allowed_statuses:
        raise RuntimeError(
            "Shopee price repair requires the exact governed target state"
        )
    item_id = str(target.get("external_id") or "").strip()
    if not item_id.isdigit():
        raise RuntimeError("Shopee price repair requires the recorded item_id")

    payload = context["payload"]
    region = request.site.upper()
    expectation = _shopee_price_expectation(
        _target_pricing(payload, request.target_label),
        region=region,
    )
    _verified, evidence = _shopee_readback(
        match_key=request.seller_sku[-4:].zfill(4),
        region=region,
        item_id=item_id,
        expected_title=_candidate(payload, "shopee", "CNSC"),
        expected_price=expectation,
        expected_image_count=len(context["images"]),
        expected_description=_shopee_description(payload),
        require_model_sku=True,
        require_all_logistics=False,
        allow_token_refresh=False,
    )
    checks = dict(evidence.get("checks") or {})
    required_checks = {
        "seller_sku",
        "model_sku",
        "localized_title",
        "rich_localized_description",
        "price",
        "image_count",
        "all_applicable_logistics",
        "status",
    }
    if set(checks) != required_checks:
        raise RuntimeError("Shopee repair readback checks are incomplete")
    non_price_failures = sorted(
        name for name, passed in checks.items()
        if name != "price" and passed is not True
    )
    if non_price_failures:
        raise RuntimeError(
            "Shopee repair is blocked by non-price drift: "
            + ", ".join(non_price_failures)
        )
    allowed_price_issues = {
        "current_price_does_not_match_approved_local_price",
        "original_price_does_not_match_approved_local_price",
        # Legacy fixtures/readbacks may still report this observation as an
        # issue. It is explicitly non-writable and never a repair blocker.
        "sip_item_price_does_not_match_immutable_cny_price",
    }
    unexpected_price_issues = sorted(
        set(evidence.get("price_issues") or ()) - allowed_price_issues
    )
    if unexpected_price_issues:
        raise RuntimeError(
            "Shopee repair is blocked by ambiguous price semantics: "
            + ", ".join(unexpected_price_issues)
        )
    rows = list(evidence.get("observed_price_fields") or ())
    eligible = [
        row
        for row in rows
        if (
            str(row.get("scope") or "") == "model"
            and str(row.get("currency") or "")
            == str(expectation["target_local_currency"])
        )
    ]
    if len(eligible) != 1:
        raise RuntimeError(
            "Shopee repair requires one unique SKU-bound local price row"
        )
    row = eligible[0]
    model_id = str(row.get("model_id") or "").strip()
    if not model_id.isdigit():
        raise RuntimeError("Shopee repair requires one exact model_id")
    expected_local = _decimal(expectation.get("source_local_price"))
    if expected_local is None or expected_local <= 0:
        raise RuntimeError("Shopee repair expected local price is invalid")
    local_exact = (
        _numbers_equal(row.get("current_price"), expected_local)
        and _numbers_equal(row.get("original_price"), expected_local)
    )
    if local_exact:
        raise RuntimeError("Shopee local price already matches the immutable plan")
    operation = {
        "kind": "shopee_original_price_repair_v1",
        "plan_id": request.plan_id,
        "run_id": str(context["run"].get("run_id") or ""),
        "target_label": request.target_label,
        "external_id": item_id,
        "model_id": model_id,
        "seller_sku": request.seller_sku[-4:].zfill(4),
        "expected_local_price": str(expected_local),
        "currency": expectation["target_local_currency"],
        "expected_sip_cny": str(expectation["sip_reference_cny"]),
        "observed_local_price_digest": hashlib.sha256(
            json.dumps(
                {
                    "current": row.get("current_price"),
                    "original": row.get("original_price"),
                    "sip": row.get("sip_item_price"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    operation["preflight_digest"] = hashlib.sha256(
        json.dumps(
            operation,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "operation": operation,
        "evidence": {
            "verified_identity": True,
            "region": region,
            "item_id": item_id,
            "model_id": model_id,
            "seller_sku": operation["seller_sku"],
            "currency": operation["currency"],
            "checks": checks,
            "price_issues": list(evidence.get("price_issues") or ()),
            "external_writes_performed": [],
        },
    }


def preflight_shopee_price_repair(
    request: AdapterExecutionRequest,
) -> dict[str, Any]:
    """Public no-write preflight for the controlled repair endpoint."""

    return _shopee_price_repair_preflight(request)


def execute_shopee_price_repair(
    request: AdapterExecutionRequest,
    *,
    expected_preflight_digest: str,
) -> AdapterExecutionResult:
    """Perform exactly one official update_price and bounded exact readback."""

    from modules.shopee.client import shop_post

    preflight = _shopee_price_repair_preflight(
        request,
        allowed_statuses=frozenset({"RUNNING"}),
    )
    operation = preflight["operation"]
    if operation["preflight_digest"] != str(expected_preflight_digest or ""):
        raise RuntimeError("Shopee price repair inputs changed before dispatch")
    item_id = operation["external_id"]
    shop_id, token = _shopee_readback_credentials(
        request.site.upper(),
        allow_token_refresh=False,
    )
    body = {
        "item_id": int(item_id),
        "price_list": [
            {
                "model_id": int(operation["model_id"]),
                "original_price": float(
                    Decimal(operation["expected_local_price"])
                ),
            }
        ],
    }
    try:
        response = shop_post(
            "/api/v2/product/update_price",
            shop_id,
            token,
            body,
        )
    except Exception as error:
        raise ShopeePriceRepairReconciliationError(
            "Shopee price repair response is unknown; do not repeat",
            external_reference=item_id,
            evidence={
                "verified": False,
                "reconciliation_required": True,
                "region": request.site.upper(),
                "item_id": item_id,
                "model_id": operation["model_id"],
                "dispatch_outcome": "response_unknown",
                "error_type": type(error).__name__,
                "external_writes_performed": ["shopee:update_price"],
            },
        ) from error
    api_error = str(response.get("error") or "").strip()
    if api_error and api_error != "-":
        raise ShopeePriceRepairReconciliationError(
            "Shopee rejected the one-shot price repair; do not auto retry",
            external_reference=item_id,
            evidence={
                "verified": False,
                "reconciliation_required": True,
                "region": request.site.upper(),
                "item_id": item_id,
                "model_id": operation["model_id"],
                "dispatch_outcome": "api_rejected",
                "response_error": api_error[:120],
                "external_writes_performed": ["shopee:update_price"],
            },
        )

    last_evidence: dict[str, Any] = {}
    for attempt in range(6):
        if attempt:
            time.sleep(2)
        try:
            context = _validated_context(request)
            payload = context["payload"]
            expectation = _shopee_price_expectation(
                _target_pricing(payload, request.target_label),
                region=request.site.upper(),
            )
            _verified, evidence = _shopee_readback(
                match_key=request.seller_sku[-4:].zfill(4),
                region=request.site.upper(),
                item_id=item_id,
                expected_title=_candidate(payload, "shopee", "CNSC"),
                expected_price=expectation,
                expected_image_count=len(context["images"]),
                expected_description=_shopee_description(payload),
                require_model_sku=True,
                require_all_logistics=False,
                allow_token_refresh=False,
            )
        except Exception as error:
            raise ShopeePriceRepairReconciliationError(
                (
                    "Shopee accepted the one-shot price repair, but official "
                    "readback is unknown; do not repeat"
                ),
                external_reference=item_id,
                evidence={
                    "verified": False,
                    "reconciliation_required": True,
                    "source": "official_shopee_partner_api",
                    "region": request.site.upper(),
                    "dispatch_outcome": "accepted_readback_unknown",
                    "error_type": type(error).__name__,
                    "poll_attempt": attempt + 1,
                    "external_writes_performed": ["shopee:update_price"],
                },
            ) from error
        rows = [
            row for row in (evidence.get("observed_price_fields") or ())
            if (
                str(row.get("model_id") or "") == operation["model_id"]
                and str(row.get("currency") or "") == operation["currency"]
            )
        ]
        checks = dict(evidence.get("checks") or {})
        row = rows[0] if len(rows) == 1 else {}
        local_exact = (
            len(rows) == 1
            and _numbers_equal(
                row.get("current_price"),
                operation["expected_local_price"],
            )
            and _numbers_equal(
                row.get("original_price"),
                operation["expected_local_price"],
            )
        )
        derived_observation, variance_warning = (
            _shopee_platform_derived_price_observation(
                row,
                {
                    "sip_reference_cny": operation["expected_sip_cny"],
                },
            )
            if len(rows) == 1
            else (None, {
                "code": "shopee_sip_platform_derived_variance",
                "severity": "warning",
                "writable": False,
                "authority": "shopee",
                "message": "Shopee-derived SIP observation is unavailable",
            })
        )
        nonprice_checks = {
            name: passed for name, passed in checks.items()
            if name != "price"
        }
        required_checks = {
            "seller_sku",
            "model_sku",
            "localized_title",
            "rich_localized_description",
            "price",
            "image_count",
            "all_applicable_logistics",
            "status",
        }
        all_nonprice_checks = set(checks) == required_checks and all(
            value is True for value in nonprice_checks.values()
        )
        last_evidence = {
            "verified": bool(local_exact and all_nonprice_checks),
            "reconciliation_required": False,
            "write_status": (
                "verified"
                if local_exact and all_nonprice_checks
                else "unverified"
            ),
            "listing_price_verified": local_exact,
            "derived_price_status": (
                "warning" if variance_warning else "matched"
            ),
            "profit_status": "unverified",
            "financial_verification_status": (
                "price_verified_profit_unverified"
                if local_exact
                else "price_unverified_profit_unverified"
            ),
            "source": "official_shopee_partner_api",
            "region": request.site.upper(),
            "item_id": item_id,
            "model_id": operation["model_id"],
            "seller_sku": operation["seller_sku"],
            "currency": operation["currency"],
            "local_price_exact": local_exact,
            "sip_cny_exact": variance_warning is None,
            "platform_derived_observation": derived_observation,
            "variance_warning": variance_warning,
            "checks": checks,
            "poll_attempt": attempt + 1,
            "external_writes_performed": ["shopee:update_price"],
        }
        if last_evidence["verified"]:
            return AdapterExecutionResult(
                succeeded=True,
                readback_verified=True,
                detail=(
                    f"Shopee {request.site.upper()} original price repaired "
                    "and exact official readback matched"
                ),
                external_reference=item_id,
                readback_evidence=last_evidence,
            )
    raise ShopeePriceRepairReconciliationError(
        "Shopee price repair was sent but exact readback did not converge",
        external_reference=item_id,
        evidence={
            **last_evidence,
            "verified": False,
            "reconciliation_required": True,
        },
    )


def reconcile_shopee_price_repair(
    request: AdapterExecutionRequest,
    *,
    operation: dict[str, Any],
) -> AdapterExecutionResult:
    """GET-only verification for one durably ambiguous price repair."""

    context = _validated_context(request)
    target = context["target"]
    repair = target.get("repair") or {}
    if request.channel != "shopee" or request.site.upper() not in {"PH", "TH"}:
        raise RuntimeError("Shopee price reconciliation only supports PH and TH")
    if (
        str(target.get("status") or "") != "RECONCILIATION_REQUIRED"
        or str(repair.get("status") or "") != "RECONCILIATION_REQUIRED"
    ):
        raise RuntimeError(
            "Shopee price reconciliation requires the exact durable state"
        )
    expected_identity = {
        "kind": "shopee_original_price_repair_v1",
        "plan_id": request.plan_id,
        "run_id": str(context["run"].get("run_id") or ""),
        "target_label": request.target_label,
        "external_id": str(target.get("external_id") or ""),
        "seller_sku": request.seller_sku[-4:].zfill(4),
    }
    for field, expected in expected_identity.items():
        if str(operation.get(field) or "") != str(expected):
            raise RuntimeError(
                f"Shopee price reconciliation operation {field} changed"
            )
    model_id = str(operation.get("model_id") or "").strip()
    currency = str(operation.get("currency") or "").strip()
    expected_local = str(operation.get("expected_local_price") or "").strip()
    if not model_id.isdigit() or not currency or not expected_local:
        raise RuntimeError("Shopee price reconciliation operation is incomplete")

    payload = context["payload"]
    expectation = _shopee_price_expectation(
        _target_pricing(payload, request.target_label),
        region=request.site.upper(),
    )
    verified, evidence = _shopee_readback(
        match_key=request.seller_sku[-4:].zfill(4),
        region=request.site.upper(),
        item_id=expected_identity["external_id"],
        expected_title=_candidate(payload, "shopee", "CNSC"),
        expected_price=expectation,
        expected_image_count=len(context["images"]),
        expected_description=_shopee_description(payload),
        require_model_sku=True,
        require_all_logistics=False,
        allow_token_refresh=False,
    )
    rows = [
        row
        for row in (evidence.get("observed_price_fields") or ())
        if (
            str(row.get("model_id") or "") == model_id
            and str(row.get("currency") or "") == currency
        )
    ]
    local_exact = bool(
        len(rows) == 1
        and _numbers_equal(rows[0].get("current_price"), expected_local)
        and _numbers_equal(rows[0].get("original_price"), expected_local)
    )
    derived_observation, variance_warning = (
        _shopee_platform_derived_price_observation(
            rows[0],
            {"sip_reference_cny": operation.get("expected_sip_cny")},
        )
        if len(rows) == 1
        else (None, {
            "code": "shopee_sip_platform_derived_variance",
            "severity": "warning",
            "writable": False,
            "authority": "shopee",
            "message": "Shopee-derived SIP observation is unavailable",
        })
    )
    checks = dict(evidence.get("checks") or {})
    required_checks = {
        "seller_sku",
        "model_sku",
        "localized_title",
        "rich_localized_description",
        "price",
        "image_count",
        "all_applicable_logistics",
        "status",
    }
    nonprice_exact = set(checks) == required_checks and all(
        passed is True
        for name, passed in checks.items()
        if name != "price"
    )
    exact = bool(verified and local_exact and nonprice_exact)
    evidence = {
        **evidence,
        "verified": exact,
        "reconciliation_required": not exact,
        "reconciliation_mode": "official_get_only_durable_close",
        "write_status": "verified" if exact else "unverified",
        "listing_price_verified": local_exact,
        "derived_price_status": (
            "warning" if variance_warning else "matched"
        ),
        "platform_derived_observation": derived_observation,
        "variance_warning": variance_warning,
        "profit_status": "unverified",
        "financial_verification_status": (
            "price_verified_profit_unverified"
            if local_exact
            else "price_unverified_profit_unverified"
        ),
        "external_writes_performed": [],
    }
    return AdapterExecutionResult(
        succeeded=exact,
        readback_verified=exact,
        detail=(
            f"Shopee {request.site.upper()} regional listing price verified "
            "by GET-only readback; profit remains unverified"
            if exact
            else (
                f"Shopee {request.site.upper()} GET-only price readback "
                "did not match the immutable repair"
            )
        ),
        external_reference=expected_identity["external_id"],
        readback_evidence=evidence,
    )


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
    from modules.shopee.publish import sync_shop_ids
    from modules.shopee.target_scoped import scan_prepared_shop_sku

    shop_id = int(sync_shop_ids()[region.upper()])
    token = ensure_shop_token(shop_id)
    scan = scan_prepared_shop_sku(
        shop_id=shop_id,
        access_token=token,
        seller_sku=seller_sku,
    )
    if scan.get("complete") is not True:
        raise RuntimeError("Shopee full status SKU scan is incomplete")
    matches = scan.get("matches")
    if not isinstance(matches, list) or any(
        not isinstance(row, dict) for row in matches
    ):
        raise RuntimeError("Shopee full status SKU scan is malformed")
    item_ids = {
        str(row.get("item_id") or "").strip()
        for row in matches
        if str(row.get("item_id") or "").strip()
    }
    if len(item_ids) > 1:
        raise RuntimeError("Shopee regional SKU identity is ambiguous")
    return next(iter(item_ids), "")


def _shopee_publish_receipt_summary(result: dict) -> tuple[list[str], dict]:
    """Return truthful write classes and a raw-free global-master summary."""

    writes = ["shopee:regional_publish"]
    global_master = result.get("global_master")
    summary = {
        "source_copy_verified": False,
        "global_master_updated": False,
    }
    if isinstance(global_master, dict):
        global_writes = list(
            global_master.get("external_writes_performed") or ()
        )
        writes = list(dict.fromkeys([*global_writes, *writes]))
        summary = {
            "source_copy_verified": global_master.get("verified") is True,
            "global_master_updated": global_master.get("updated") is True,
            "global_master_write_count": len(global_writes),
            "global_master_evidence_digest": hashlib.sha256(
                json.dumps(
                    {
                        "verified": global_master.get("verified") is True,
                        "updated": global_master.get("updated") is True,
                        "external_writes_performed": global_writes,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
    return writes, summary


def execute_shopee_target(
    request: AdapterExecutionRequest,
) -> AdapterExecutionResult:
    context = _validated_context(request)
    payload = context["payload"]
    region = request.site.upper()
    title = _candidate(payload, "shopee", "CNSC")
    description = _shopee_description(payload)
    pricing = _target_pricing(payload, request.target_label)
    expected_price = _shopee_price_expectation(pricing, region=region)
    retired_item_evidence: dict[str, Any] | None = None
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
            expected_description=description,
            require_model_sku=False,
            require_all_logistics=False,
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
        if evidence.get("replacement_release_allowed") is True:
            retired_item_evidence = {
                "source": "official_shopee_partner_api",
                "region": region,
                "retired_item_id_digest": _miaoshou_value_digest(
                    str(item_id)
                ),
                "status": "SELLER_DELETE",
                "identity_exact": True,
                "evidence_digest": _miaoshou_value_digest(evidence),
            }
        repairable_checks = {
            "localized_title",
            "rich_localized_description",
            "all_applicable_logistics",
        }
        failed_checks = {
            name
            for name, passed in (evidence.get("checks") or {}).items()
            if not passed
        }
        if (
            retired_item_evidence is None
            and failed_checks
            and failed_checks.issubset(repairable_checks)
        ):
            from modules.shopee.auth import ensure_shop_token
            from modules.shopee.global_copy import localize_shopee_copy
            from modules.shopee.publish import (
                sync_shop_ids,
                update_local_listing_copy,
            )

            localized = localize_shopee_copy(
                english_title=title,
                english_description=description,
                region=region,
            )
            shop_id = int(sync_shop_ids()[region])
            repair = update_local_listing_copy(
                shop_id=shop_id,
                token=ensure_shop_token(shop_id),
                item_id=int(item_id),
                title=localized["title"],
                description=localized["description"],
            )
            corrected, corrected_evidence = _shopee_readback(
                match_key=request.seller_sku[-4:].zfill(4),
                region=region,
                item_id=item_id,
                expected_title=title,
                expected_price=expected_price,
                expected_image_count=len(context["images"]),
                expected_description=description,
                require_model_sku=False,
                require_all_logistics=False,
            )
            corrected_evidence["repair"] = {
                **repair,
                "localization_provider": localized["provider"],
                "localization_model": localized["model"],
            }
            if corrected:
                return AdapterExecutionResult(
                    True,
                    True,
                    (
                        f"existing Shopee {region} listing was repaired in place "
                        "and matched official API readback"
                    ),
                    item_id,
                    corrected_evidence,
                )
            evidence = corrected_evidence
        if retired_item_evidence is None:
            return AdapterExecutionResult(
                False,
                False,
                (
                    f"existing Shopee {region} item still requires in-place repair; "
                    "a second publish was blocked to prevent a duplicate SKU"
                ),
                item_id,
                evidence,
            )

    local_original_price = expected_price["source_local_price"]
    local_currency = expected_price["source_local_currency"]
    global_original_price_cny = expected_price["sip_reference_cny"]

    from modules.shopee.publish import publish_match_key

    result = publish_match_key(
        request.seller_sku,
        region,
        dry_run=False,
        global_only=False,
        publish_shops=True,
        item_status="NORMAL",
        title_override=title,
        description_override=description,
        global_original_price_cny_override=global_original_price_cny,
        local_original_price_override=local_original_price,
        local_price_currency_override=local_currency,
    )
    if not isinstance(result, dict):
        return AdapterExecutionResult(
            True,
            False,
            (
                f"Shopee {region} publish was invoked but its receipt is "
                "malformed"
            ),
            None,
            {
                "verified": False,
                "source": "official_shopee_partner_api",
                "region": region,
                "reason": "publish_receipt_invalid",
                "submission_accepted": False,
                "durable_state_uncertain": True,
                "external_writes_performed": ["shopee:regional_publish"],
            },
        )
    publish_writes, global_master_summary = _shopee_publish_receipt_summary(
        result
    )
    raw_item_id = result.get("item_id") or _shopee_item_id_for_match_key(
        request.seller_sku,
        region,
    )
    item_id = str(raw_item_id or "").strip()
    if not item_id:
        return AdapterExecutionResult(
            True,
            False,
            (
                f"Shopee {region} publish was invoked but did not return an "
                "item_id"
            ),
            None,
            {
                "verified": False,
                "source": "official_shopee_partner_api",
                "region": region,
                "reason": "publish_item_identity_missing",
                "submission_accepted": False,
                "durable_state_uncertain": True,
                "external_writes_performed": publish_writes,
                "global_master": global_master_summary,
            },
        )
    logistics_receipt = (
        result.get("logistics")
        if isinstance(result.get("logistics"), dict)
        else {}
    )
    expected_enabled_logistic_ids = logistics_receipt.get(
        "enabled_logistic_ids"
    )
    if (
        not isinstance(expected_enabled_logistic_ids, list)
        or not expected_enabled_logistic_ids
        or any(
            type(value) is not int or value <= 0
            for value in expected_enabled_logistic_ids
        )
        or len(set(expected_enabled_logistic_ids))
        != len(expected_enabled_logistic_ids)
    ):
        return AdapterExecutionResult(
            True,
            False,
            (
                f"Shopee {region} publish completed but its exact logistics "
                "receipt is incomplete"
            ),
            item_id,
            {
                "verified": False,
                "source": "official_shopee_partner_api",
                "region": region,
                "item_id": item_id,
                "reason": "publish_logistics_receipt_invalid",
                "external_writes_performed": publish_writes,
                "global_master": global_master_summary,
            },
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
            expected_description=description,
            require_all_logistics=True,
            expected_enabled_logistic_ids=expected_enabled_logistic_ids,
        )
        evidence["poll_attempt"] = attempt + 1
        evidence["external_writes_performed"] = publish_writes
        evidence["global_master"] = global_master_summary
        if retired_item_evidence is not None:
            evidence["replacement_release"] = retired_item_evidence
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
            "reason": (
                "offer_not_found"
                if not items
                else "ambiguous_offer_identity"
            ),
            "item_count": len(items),
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
        "is_created": bool(statuses.get("is_created")),
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
    )


def _ozon_product_creation_probe(*, offer_id: str) -> dict[str, Any]:
    """Read the official product identity without mutating stock or listing data."""

    from modules.ozon.client import ozon_post

    response = ozon_post("/v3/product/info/list", {"offer_id": [offer_id]})
    items = response.get("items") or ()
    if not items:
        return {
            "state": "pending",
            "offer_id": offer_id,
            "reason": "product_not_visible",
        }
    if len(items) != 1:
        return {
            "state": "ambiguous",
            "offer_id": offer_id,
            "reason": "multiple_products_returned",
            "item_count": len(items),
        }
    item = items[0]
    actual_offer_id = str(item.get("offer_id") or "")
    product_id = str(item.get("id") or item.get("product_id") or "")
    if actual_offer_id != offer_id or not product_id:
        return {
            "state": "ambiguous",
            "offer_id": offer_id,
            "actual_offer_id": actual_offer_id,
            "product_id": product_id,
            "reason": "product_identity_mismatch",
        }
    statuses = item.get("statuses") or {}
    if not statuses.get("is_created"):
        return {
            "state": "pending",
            "offer_id": offer_id,
            "product_id": product_id,
            "reason": "product_not_created",
            "status": statuses.get("status"),
            "validation_status": statuses.get("validation_status"),
        }
    return {
        "state": "created",
        "offer_id": offer_id,
        "product_id": product_id,
        "status": statuses.get("status"),
        "validation_status": statuses.get("validation_status"),
        "is_created": True,
    }


def _await_ozon_product_creation(
    *,
    offer_id: str,
    task_id: str = "",
    attempts: int = 24,
    delay_seconds: float = 5,
) -> dict[str, Any]:
    """Wait until import task and exact product identity are safe for stock writes."""

    from modules.ozon.client import ozon_post

    last_task: dict[str, Any] = {}
    last_product: dict[str, Any] = {}
    for attempt in range(max(1, attempts)):
        if attempt and delay_seconds:
            time.sleep(delay_seconds)
        task_ready = not task_id
        if task_id:
            try:
                response = ozon_post(
                    "/v1/product/import/info",
                    {"task_id": task_id},
                )
                rows = (response.get("result") or {}).get("items") or ()
                if len(rows) > 1:
                    return {
                        "state": "ambiguous",
                        "offer_id": offer_id,
                        "task_id": task_id,
                        "poll_attempt": attempt + 1,
                        "reason": "multiple_import_task_items",
                        "task_item_count": len(rows),
                    }
                if rows:
                    row = rows[0]
                    row_offer_id = str(row.get("offer_id") or "")
                    if row_offer_id and row_offer_id != offer_id:
                        return {
                            "state": "ambiguous",
                            "offer_id": offer_id,
                            "task_id": task_id,
                            "poll_attempt": attempt + 1,
                            "reason": "import_task_offer_mismatch",
                            "task_offer_id": row_offer_id,
                        }
                    status = str(row.get("status") or "").lower()
                    errors = list(row.get("errors") or ())
                    last_task = {
                        "status": status,
                        "errors": errors,
                        "offer_id": row_offer_id,
                    }
                    if errors or status not in {"", "pending", "imported"}:
                        return {
                            "state": "failed",
                            "offer_id": offer_id,
                            "task_id": task_id,
                            "poll_attempt": attempt + 1,
                            "reason": "import_task_failed",
                            "task": last_task,
                        }
                    task_ready = status == "imported" and not errors
            except Exception as error:
                last_task = {"error": str(error)}
        if not task_ready:
            continue
        try:
            last_product = _ozon_product_creation_probe(offer_id=offer_id)
        except Exception as error:
            last_product = {
                "state": "pending",
                "offer_id": offer_id,
                "reason": "product_probe_failed",
                "error": str(error),
            }
        if last_product.get("state") == "created":
            return {
                **last_product,
                "task_id": task_id,
                "poll_attempt": attempt + 1,
                "task": last_task,
            }
        if last_product.get("state") == "ambiguous":
            return {
                **last_product,
                "task_id": task_id,
                "poll_attempt": attempt + 1,
                "task": last_task,
            }
    return {
        "state": "timeout",
        "offer_id": offer_id,
        "task_id": task_id,
        "poll_attempts": max(1, attempts),
        "reason": "product_creation_not_confirmed",
        "task": last_task,
        "product": last_product,
    }


def _ozon_reconciliation_result(
    *,
    offer_id: str,
    detail: str,
    evidence: dict[str, Any],
) -> AdapterExecutionResult:
    receipt = {
        **evidence,
        "source": "official_ozon_seller_api",
        "offer_id": offer_id,
        "reconciliation_required": True,
    }
    return AdapterExecutionResult(
        succeeded=True,
        readback_verified=False,
        detail=detail,
        external_reference=offer_id,
        readback_evidence=receipt,
        submission_accepted=True,
    )


def _ozon_set_release_stock(
    *,
    offer_id: str,
    stock: int,
) -> dict[str, Any]:
    if isinstance(stock, bool) or not isinstance(stock, int) or stock <= 0:
        raise RuntimeError(
            "Ozon stock update requires a positive plan-approved quantity"
        )
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
    from shared_platform.target_scoped_release_contracts import (
        TargetScopedCommandUnavailable,
        planned_target_command,
    )

    try:
        planned_stock_command, _planned_stock_digest = (
            planned_target_command(
                payload,
                target_label=request.target_label,
            )
        )
    except TargetScopedCommandUnavailable as error:
        raise RuntimeError(
            "Ozon automatic release requires an immutable Kyle-approved "
            "inventory decision; no default stock is allowed"
        ) from error
    desired_stock_quantity = planned_stock_command[
        "desired_stock_quantity"
    ]
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
        if not evidence.get("is_created") or not evidence.get("product_id"):
            return _ozon_reconciliation_result(
                offer_id=offer_id,
                detail=(
                    "Ozon listing exists but product creation is not yet "
                    "confirmed; Rich Content and stock writes were not attempted"
                ),
                evidence={
                    "phase": "existing_product_creation",
                    "existing_readback": evidence,
                    "external_writes_performed": [],
                },
            )
        repair_evidence = _repair_ozon_rich_content(
            offer_id=offer_id,
            title=expected_platform_title,
            images=list(evidence.get("image_urls") or ()),
            width_cm=width,
            height_cm=height,
        )
        stock_evidence = _ozon_set_release_stock(
            offer_id=offer_id,
            stock=desired_stock_quantity,
        )
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
        creation_evidence = (
            {
                "state": "created",
                "offer_id": offer_id,
                "product_id": evidence.get("product_id"),
                "is_created": True,
                "reused_readback": True,
            }
            if evidence.get("is_created") and evidence.get("product_id")
            else _await_ozon_product_creation(offer_id=offer_id)
        )
        if creation_evidence.get("state") != "created":
            return _ozon_reconciliation_result(
                offer_id=offer_id,
                detail=(
                    "Existing Ozon import is still awaiting exact product "
                    "creation; duplicate import and stock update were blocked"
                ),
                evidence={
                    "phase": "existing_product_creation",
                    "existing_readback": evidence,
                    "creation": creation_evidence,
                    "external_writes_performed": [],
                },
            )
        stock_evidence = (
            {"reused": True}
            if evidence.get("has_stock")
            else _ozon_set_release_stock(
                offer_id=offer_id,
                stock=desired_stock_quantity,
            )
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
            processing["creation"] = creation_evidence
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
    if int(evidence.get("item_count") or 0) > 1:
        return AdapterExecutionResult(
            False,
            False,
            "Ozon official readback returned an ambiguous existing offer identity",
            None,
            {
                **evidence,
                "external_writes_performed": [],
                "duplicate_import_blocked": True,
            },
        )
    if evidence.get("product_id") or evidence.get("is_created"):
        return AdapterExecutionResult(
            False,
            False,
            "Existing Ozon offer does not match the immutable release payload",
            str(evidence.get("product_id") or offer_id),
            {
                **evidence,
                "external_writes_performed": [],
                "duplicate_import_blocked": True,
            },
        )
    durable_attempts = int(
        ((context.get("target") or {}).get("attempts") or 1)
    )
    if durable_attempts > 1:
        return _ozon_reconciliation_result(
            offer_id=offer_id,
            detail=(
                "A prior Ozon target attempt may already have dispatched an "
                "import, but no exact product is visible yet; a second import "
                "was blocked"
            ),
            evidence={
                "phase": "prior_import_reconciliation",
                "durable_attempts": durable_attempts,
                "existing_readback": evidence,
                "duplicate_import_blocked": True,
                "external_writes_performed": [],
            },
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
        # A governed release must not silently train the shared TikTok→Ozon
        # category mapping from one product. Mapping changes remain a separate
        # reviewed catalogue operation.
        skip_mapping_write=True,
    )
    offer_id = str(result.get("offer_id") or offer_id)
    import_attempted = bool(result.get("import_request_attempted"))
    task_id = str(result.get("task_id") or "")
    import_evidence = {
        "phase": "product_import",
        "task_id": task_id,
        "status": result.get("status"),
        "dispatch_outcome": result.get("import_dispatch_outcome"),
        "errors": list(result.get("errors") or ()),
        "external_writes_performed": (
            ["ozon:product_import:create"]
            if import_attempted
            else []
        ),
    }
    if not result.get("ok") and not import_attempted:
        return AdapterExecutionResult(
            False,
            False,
            f"Ozon import failed: {result.get('error') or result.get('status')}",
            offer_id,
            {"source": "official_ozon_seller_api", "import_result": result},
        )
    if import_attempted and not task_id:
        return _ozon_reconciliation_result(
            offer_id=offer_id,
            detail=(
                "Ozon import dispatch did not return a stable task identity; "
                "stock update and duplicate import were blocked"
            ),
            evidence={
                **import_evidence,
                "import_result": result,
                "creation": {
                    "state": "ambiguous",
                    "reason": "missing_import_task_id",
                },
            },
        )
    creation_evidence = _await_ozon_product_creation(
        offer_id=offer_id,
        task_id=task_id,
    )
    if creation_evidence.get("state") != "created":
        return _ozon_reconciliation_result(
            offer_id=offer_id,
            detail=(
                "Ozon import was dispatched but exact product creation did "
                "not converge; stock update and duplicate import were blocked"
            ),
            evidence={
                **import_evidence,
                "import_result": result,
                "creation": creation_evidence,
            },
        )
    try:
        stock_evidence = _ozon_set_release_stock(
            offer_id=offer_id,
            stock=desired_stock_quantity,
        )
    except Exception as error:
        return _ozon_reconciliation_result(
            offer_id=offer_id,
            detail=(
                "Ozon product was created but the stock update outcome "
                "requires reconciliation"
            ),
            evidence={
                **import_evidence,
                "import_result": result,
                "creation": creation_evidence,
                "stock_error": str(error),
                "external_writes_performed": [
                    *import_evidence["external_writes_performed"],
                    "ozon:stock:update_attempted",
                ],
            },
        )
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
        evidence["import"] = import_evidence
        evidence["creation"] = creation_evidence
        evidence["stock_write"] = stock_evidence
        evidence["external_writes_performed"] = [
            *import_evidence["external_writes_performed"],
            "ozon:stock:update",
        ]
        if verified:
            return AdapterExecutionResult(
                True,
                True,
                "Ozon listing imported and matched official API readback",
                str(evidence.get("product_id") or offer_id),
                evidence,
            )
    return _ozon_reconciliation_result(
        offer_id=offer_id,
        detail=(
            "Ozon product creation and stock update completed but exact "
            "official readback did not converge"
        ),
        evidence=evidence,
    )
