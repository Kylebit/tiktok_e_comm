"""Governed TikTok post-publish direct-discount action.

Preparation is read-only and exhaustive.  Dispatch revalidates the immutable
selection, opens one durable write occurrence, invokes the audited promotion
endpoint once, and requires exact official readback before success.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import time
from typing import Any

from shared_platform.postpublish_promotions import (
    PROMOTION_POLICY_VERSION,
    PROMOTION_SELECTION_POLICY,
    TIKTOK_PROMOTION_WRITE_CLASS,
    approved_promotion_action_policy,
    promotion_target_policy,
)


ACTIVITY_SEARCH_PATH = "/promotion/202309/activities/search"
ACTIVITY_DETAIL_PATH = "/promotion/202309/activities/{activity_id}"
ACTIVITY_PRODUCTS_PATH = (
    "/promotion/202309/activities/{activity_id}/products"
)
PRODUCT_DETAIL_PATH = "/product/202309/products/{product_id}"
SHOP_LIST_PATH = "/authorization/202309/shops"
PREPARED_SCHEMA = "oneclick-tiktok-postpublish-promotion/v1"
PROOF_SCHEMA = "oneclick-tiktok-postpublish-promotion-proof/v1"
MAX_ACTIVITY_PAGES = 100
PAGE_SIZE = 100


class TikTokPromotionBlocked(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        category: str = "CAPABILITY",
    ) -> None:
        super().__init__(detail)
        self.classification = "BLOCKED_CAPABILITY"
        self.reason_category = category
        self.reason_code = code
        self.reason_detail = detail


class TikTokPromotionPreDispatchError(RuntimeError):
    pass


class TikTokPromotionDispatchError(RuntimeError):
    def __init__(
        self,
        detail: str,
        *,
        unknown: bool,
        external_write_count: int | None,
        lower_bound: int,
        upper_bound: int | None,
    ) -> None:
        super().__init__(detail)
        self.external_writes = (TIKTOK_PROMOTION_WRITE_CLASS,)
        self.dispatch_outcome_unknown = unknown
        self.external_write_count = external_write_count
        self.confirmed_external_write_count_lower_bound = lower_bound
        self.possible_external_write_count_upper_bound = upper_bound


@dataclass(frozen=True)
class TikTokPromotionTransport:
    list_shops: Callable[[], Mapping[str, Any]]
    search_activities: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    get_activity: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
    get_product: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
    put_activity_products: Callable[
        [str, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]
    ]


_transport_factory: Callable[[], TikTokPromotionTransport] | None = None


def configure_tiktok_promotion_transport_factory(
    factory: Callable[[], TikTokPromotionTransport] | None,
) -> None:
    global _transport_factory
    _transport_factory = factory


def promotion_adapter_policy_digest() -> str:
    return _digest(
        {
            "schema_version": PREPARED_SCHEMA,
            "policy_version": PROMOTION_POLICY_VERSION,
            "selection_policy": PROMOTION_SELECTION_POLICY,
            "activity_search": ACTIVITY_SEARCH_PATH,
            "activity_detail": ACTIVITY_DETAIL_PATH,
            "activity_products": ACTIVITY_PRODUCTS_PATH,
            "product_detail": PRODUCT_DETAIL_PATH,
            "complete_pagination": True,
            "exactly_one_write_occurrence": True,
            "official_readback_required": True,
            "shopee": "blocked_no_audited_api",
        }
    )


def prepare_postpublish_promotion(request: object) -> Mapping[str, Any]:
    target = _text(getattr(request, "target_label", None), "target")
    target_policy = promotion_target_policy(target)
    if target_policy["channel"] != "tiktok":
        return _blocked(
            "shopee_promotion_api_unavailable",
            "Shopee promotion endpoint and exact readback are not audited",
        )
    plan = _mapping(
        getattr(request, "immutable_plan_payload", None),
        "immutable plan payload",
    )
    approval = approved_promotion_action_policy(plan, target)
    prerequisite = _mapping(
        getattr(request, "prerequisite_context", None),
        "verified storefront prerequisite",
    )
    if (
        prerequisite.get("target_label")
        != target_policy["prerequisite_target"]
        or not _is_digest(
            prerequisite.get("readback_evidence_digest")
        )
        or type(prerequisite.get("external_id")) is not str
        or not prerequisite["external_id"].strip()
    ):
        raise TikTokPromotionBlocked(
            "promotion_prerequisite_readback_invalid",
            "promotion requires verified official storefront readback",
            category="SYSTEMIC_CONTRACT",
        )
    expected_price, currency = _approved_price(
        plan,
        target_policy["prerequisite_target"],
    )
    if currency != target_policy["currency"]:
        raise TikTokPromotionBlocked(
            "approved_promotion_currency_mismatch",
            "approved storefront currency does not match promotion policy",
            category="CONTENT",
        )
    expected_skus = _approved_model_skus(plan)
    shop_id, shop_name = _configured_shop(
        target_policy["prerequisite_target"]
    )
    transport = _resolve_transport()
    shop = _unique_shop(
        transport.list_shops(),
        shop_id=shop_id,
        shop_name=shop_name,
        region=target_policy["region"],
    )
    shop_cipher = _text(
        shop.get("cipher") or shop.get("shop_cipher"),
        "shop cipher",
    )
    activities = _all_ongoing_activities(
        transport,
        shop_cipher=shop_cipher,
    )
    direct = [
        row
        for row in activities
        if row["activity_type"] == "DIRECT_DISCOUNT"
        and row["status"] == "ONGOING"
    ]
    if len(direct) != 1:
        raise TikTokPromotionBlocked(
            "unique_ongoing_direct_discount_unavailable",
            "exactly one ongoing direct-discount activity is required",
        )
    activity_id = direct[0]["activity_id"]
    activity = _activity_detail(
        transport.get_activity(
            activity_id,
            {"shop_cipher": shop_cipher},
        ),
        expected_activity_id=activity_id,
    )
    now = int(time.time())
    if not (activity["begin_time"] <= now < activity["end_time"]):
        raise TikTokPromotionBlocked(
            "selected_activity_not_currently_ongoing",
            "selected direct-discount activity is outside its active window",
        )
    product_id = prerequisite["external_id"].strip()
    product = _product_snapshot(
        transport.get_product(
            product_id,
            {"shop_cipher": shop_cipher},
        ),
        expected_product_id=product_id,
        expected_skus=expected_skus,
        expected_price=expected_price,
        expected_currency=currency,
    )
    activity_identity_digest = _digest(
        {
            "activity_id": activity_id,
            "activity_type": activity["activity_type"],
            "begin_time": activity["begin_time"],
            "end_time": activity["end_time"],
        }
    )
    product_identity_digest = _digest(
        {
            "product_id": product_id,
            "seller_skus": list(product["seller_skus"]),
            "price": expected_price,
            "currency": currency,
        }
    )
    command = {
        "schema_version": PREPARED_SCHEMA,
        "policy_version": PROMOTION_POLICY_VERSION,
        "selection_policy": PROMOTION_SELECTION_POLICY,
        "target_label": target,
        "prerequisite_target": target_policy["prerequisite_target"],
        "idempotency_key": _text(
            getattr(request, "idempotency_key", None),
            "idempotency key",
        ),
        "shop_cipher": shop_cipher,
        "activity_id": activity_id,
        "product_id": product_id,
        "discount_percent": target_policy["discount_percent"],
        "approved_list_price": expected_price,
        "currency": currency,
        "seller_skus": list(expected_skus),
        "begin_time": activity["begin_time"],
        "end_time": activity["end_time"],
        "activity_identity_digest": activity_identity_digest,
        "product_identity_digest": product_identity_digest,
        "action_policy_digest": approval["action_policy_digest"],
        "prerequisite_readback_evidence_digest": prerequisite[
            "readback_evidence_digest"
        ],
    }
    proof = {
        "schema_version": PROOF_SCHEMA,
        "policy_version": PROMOTION_POLICY_VERSION,
        "selection_policy": PROMOTION_SELECTION_POLICY,
        "target_label": target,
        "activity_count": len(activities),
        "ongoing_direct_discount_count": 1,
        "activity_identity_digest": activity_identity_digest,
        "product_identity_digest": product_identity_digest,
        "shop_identity_digest": _digest(
            {
                "shop_id": shop_id,
                "shop_name": shop_name,
                "region": target_policy["region"],
                "shop_cipher": shop_cipher,
            }
        ),
        "begin_time": activity["begin_time"],
        "end_time": activity["end_time"],
        "discount_percent": target_policy["discount_percent"],
        "approved_list_price": expected_price,
        "currency": currency,
        "action_policy_digest": approval["action_policy_digest"],
        "official_product_exact": True,
        "complete_activity_pagination": True,
        "evidence_digest": _digest(
            {
                "activity_identity_digest": activity_identity_digest,
                "product_identity_digest": product_identity_digest,
                "readback_evidence_digest": prerequisite[
                    "readback_evidence_digest"
                ],
                "discount_percent": target_policy["discount_percent"],
            }
        ),
    }
    return {
        "classification": "EXACT_READY_AUTOMATIC",
        "reason_category": "CAPABILITY",
        "reason_scope": "TARGET",
        "reason_code": "promotion_official_preflight_exact",
        "reason_detail": "official promotion and product preflight are exact",
        "command": command,
        "proof": proof,
        "manual_after_submit": False,
    }


def dispatch_postpublish_promotion(request: object) -> Mapping[str, Any]:
    target = _text(getattr(request, "target_label", None), "target")
    policy = promotion_target_policy(target)
    if policy["channel"] != "tiktok":
        raise TikTokPromotionPreDispatchError(
            "Shopee promotion API remains unavailable"
        )
    command = _mapping(
        _mapping(getattr(request, "command", None), "prepared command").get(
            "payload"
        ),
        "promotion command payload",
    )
    proof = _mapping(
        _mapping(getattr(request, "proof", None), "prepared proof").get(
            "payload"
        ),
        "promotion proof payload",
    )
    _validate_command_and_proof(command, proof, target)
    transport = _resolve_transport()
    activity = _activity_detail(
        transport.get_activity(
            command["activity_id"],
            {"shop_cipher": command["shop_cipher"]},
        ),
        expected_activity_id=command["activity_id"],
    )
    product = _product_snapshot(
        transport.get_product(
            command["product_id"],
            {"shop_cipher": command["shop_cipher"]},
        ),
        expected_product_id=command["product_id"],
        expected_skus=tuple(command["seller_skus"]),
        expected_price=command["approved_list_price"],
        expected_currency=command["currency"],
    )
    if (
        _activity_identity_digest(activity)
        != command["activity_identity_digest"]
        or _product_identity_digest(
            command["product_id"],
            product["seller_skus"],
            command["approved_list_price"],
            command["currency"],
        )
        != command["product_identity_digest"]
        or not (
            activity["begin_time"]
            <= int(time.time())
            < activity["end_time"]
        )
    ):
        raise TikTokPromotionPreDispatchError(
            "official promotion or product identity drifted before write"
        )
    progress = getattr(request, "progress_recorder", None)
    if not callable(progress):
        raise TikTokPromotionPreDispatchError(
            "durable promotion write recorder is unavailable"
        )
    progress(
        request,
        (TIKTOK_PROMOTION_WRITE_CLASS,),
        "promotion_apply-1",
        {"prepared_command_digest": getattr(request, "prepared_command_digest")},
        external_write_count=None,
        confirmed_external_write_count_lower_bound=0,
        possible_external_write_count_upper_bound=1,
        write_boundary="PRE_INVOCATION_INTENT",
    )
    body = {
        "activity_id": command["activity_id"],
        "products": [
            {
                "id": command["product_id"],
                "discount": str(command["discount_percent"]),
                "quantity_limit": -1,
                "quantity_per_user": -1,
            }
        ],
    }
    try:
        response = transport.put_activity_products(
            command["activity_id"],
            {"shop_cipher": command["shop_cipher"]},
            body,
        )
    except Exception as error:
        raise TikTokPromotionDispatchError(
            "promotion write transport outcome is unknown",
            unknown=True,
            external_write_count=None,
            lower_bound=0,
            upper_bound=1,
        ) from error
    if not isinstance(response, Mapping):
        raise TikTokPromotionDispatchError(
            "promotion write response is malformed",
            unknown=True,
            external_write_count=None,
            lower_bound=0,
            upper_bound=1,
        )
    if response.get("code") != 0:
        progress(
            request,
            (),
            "promotion_apply-1",
            {"business_code_digest": _digest(response.get("code"))},
            external_write_count=0,
            confirmed_external_write_count_lower_bound=0,
            possible_external_write_count_upper_bound=0,
            write_boundary="POST_RESPONSE_REJECTED",
        )
        return {
            "canonical_status": "FAILED_PRE_SUBMIT",
            "reason_category": "CAPABILITY",
            "reason_scope": "TARGET",
            "reason_code": "promotion_write_rejected",
            "reason_detail": "official promotion write was rejected without mutation",
            "external_writes": (),
            "external_write_count": 0,
            "confirmed_external_write_count_lower_bound": 0,
            "possible_external_write_count_upper_bound": 0,
            "dispatch_outcome_unknown": False,
            "evidence": {"durable_state_uncertain": False},
        }
    progress(
        request,
        (TIKTOK_PROMOTION_WRITE_CLASS,),
        "promotion_apply-1",
        {"response_shape_digest": _digest(sorted(response))},
        external_write_count=1,
        confirmed_external_write_count_lower_bound=1,
        possible_external_write_count_upper_bound=1,
        write_boundary="POST_RESPONSE_CONFIRMED",
    )
    try:
        observed = _activity_detail(
            transport.get_activity(
                command["activity_id"],
                {"shop_cipher": command["shop_cipher"]},
            ),
            expected_activity_id=command["activity_id"],
        )
        _require_exact_discount_readback(
            observed,
            product_id=command["product_id"],
            discount=command["discount_percent"],
        )
    except Exception as error:
        raise TikTokPromotionDispatchError(
            "promotion write accepted but exact readback failed",
            unknown=False,
            external_write_count=1,
            lower_bound=1,
            upper_bound=1,
        ) from error
    evidence_digest = _digest(
        {
            "activity_identity_digest": command["activity_identity_digest"],
            "product_identity_digest": command["product_identity_digest"],
            "discount_percent": command["discount_percent"],
            "readback_exact": True,
        }
    )
    return {
        "canonical_status": "SUCCEEDED",
        "reason_category": "CAPABILITY",
        "reason_scope": "TARGET",
        "reason_code": "promotion_official_readback_exact",
        "reason_detail": "official activity readback matches approved discount",
        "external_writes": (TIKTOK_PROMOTION_WRITE_CLASS,),
        "external_write_count": 1,
        "confirmed_external_write_count_lower_bound": 1,
        "possible_external_write_count_upper_bound": 1,
        "external_id": "sha256:" + command["activity_identity_digest"],
        "submission_accepted": True,
        "readback_verified": True,
        "dispatch_outcome_unknown": False,
        "evidence": {
            "schema_version": "tiktok-promotion-readback/v1",
            "discount_percent": command["discount_percent"],
            "official_readback_exact": True,
            "activity_identity_digest": command["activity_identity_digest"],
            "product_identity_digest": command["product_identity_digest"],
            "evidence_digest": evidence_digest,
        },
    }


def _blocked(code: str, detail: str) -> dict[str, Any]:
    return {
        "classification": "BLOCKED_CAPABILITY",
        "reason_category": "CAPABILITY",
        "reason_scope": "TARGET",
        "reason_code": code,
        "reason_detail": detail,
        "command": None,
        "proof": None,
        "manual_after_submit": False,
    }


def _resolve_transport() -> TikTokPromotionTransport:
    factory = _transport_factory or _default_transport
    transport = factory()
    if not isinstance(transport, TikTokPromotionTransport):
        raise TikTokPromotionBlocked(
            "promotion_transport_unavailable",
            "TikTok promotion transport is unavailable",
        )
    return transport


def _default_transport() -> TikTokPromotionTransport:
    from core import auth
    from core.api_client import _do_request_once

    token = auth.access_token()

    def call(
        method: str,
        path: str,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        value = _do_request_once(
            method,
            path,
            token,
            dict(query or {}),
            dict(body) if body is not None else None,
            False,
            False,
        )
        if not isinstance(value, Mapping):
            raise RuntimeError("TikTok response is not a mapping")
        return value

    return TikTokPromotionTransport(
        list_shops=lambda: call("GET", SHOP_LIST_PATH),
        search_activities=lambda query, body: call(
            "POST", ACTIVITY_SEARCH_PATH, query, body
        ),
        get_activity=lambda activity_id, query: call(
            "GET",
            ACTIVITY_DETAIL_PATH.format(activity_id=activity_id),
            query,
        ),
        get_product=lambda product_id, query: call(
            "GET",
            PRODUCT_DETAIL_PATH.format(product_id=product_id),
            query,
        ),
        put_activity_products=lambda activity_id, query, body: call(
            "PUT",
            ACTIVITY_PRODUCTS_PATH.format(activity_id=activity_id),
            query,
            body,
        ),
    )


def _unique_shop(
    response: Mapping[str, Any],
    *,
    shop_id: str,
    shop_name: str,
    region: str,
) -> Mapping[str, Any]:
    if not isinstance(response, Mapping) or response.get("code") != 0:
        raise TikTokPromotionBlocked(
            "official_shop_list_unavailable",
            "official TikTok shop list is unavailable",
            category="AUTH",
        )
    data = response.get("data")
    rows = (
        data.get("shops") or data.get("list")
        if isinstance(data, Mapping)
        else None
    )
    if (
        not isinstance(rows, list)
        or any(not isinstance(row, Mapping) for row in rows)
    ):
        raise TikTokPromotionBlocked(
            "official_shop_list_shape_invalid",
            "official TikTok shop list has invalid shape",
        )
    exact = [
        row
        for row in rows
        if str(row.get("id") or row.get("shop_id") or "") == shop_id
        and str(row.get("name") or row.get("shop_name") or "") == shop_name
        and str(row.get("region") or row.get("region_code") or "").upper()
        == region
    ]
    if len(exact) != 1:
        raise TikTokPromotionBlocked(
            "official_shop_identity_not_unique",
            "official TikTok shop identity is not unique",
        )
    return exact[0]


def _all_ongoing_activities(
    transport: TikTokPromotionTransport,
    *,
    shop_cipher: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    seen_ids: set[str] = set()
    page_token = ""
    declared_total: int | None = None
    for _ in range(MAX_ACTIVITY_PAGES):
        if page_token in seen_tokens:
            raise TikTokPromotionBlocked(
                "promotion_activity_cursor_loop",
                "promotion activity pagination cursor repeated",
            )
        seen_tokens.add(page_token)
        query: dict[str, Any] = {
            "shop_cipher": shop_cipher,
            "page_size": PAGE_SIZE,
        }
        if page_token:
            query["page_token"] = page_token
        response = transport.search_activities(
            query,
            {"page_size": PAGE_SIZE, "status": "ONGOING"},
        )
        if not isinstance(response, Mapping) or response.get("code") != 0:
            raise TikTokPromotionBlocked(
                "promotion_activity_search_failed",
                "official promotion activity search failed",
            )
        data = response.get("data")
        rows = data.get("activities") if isinstance(data, Mapping) else None
        total = data.get("total_count") if isinstance(data, Mapping) else None
        next_token = (
            data.get("next_page_token")
            if isinstance(data, Mapping)
            else None
        )
        if (
            not isinstance(rows, list)
            or any(not isinstance(row, Mapping) for row in rows)
            or type(total) is not int
            or total < 0
            or type(next_token) is not str
            or (declared_total is not None and total != declared_total)
        ):
            raise TikTokPromotionBlocked(
                "promotion_activity_page_shape_invalid",
                "official promotion activity page shape is invalid",
            )
        declared_total = total
        for row in rows:
            activity_id = _activity_id(row)
            if activity_id in seen_ids:
                raise TikTokPromotionBlocked(
                    "promotion_activity_identity_duplicate",
                    "official promotion activity identity is duplicated",
                )
            seen_ids.add(activity_id)
            result.append(
                {
                    "activity_id": activity_id,
                    "activity_type": _text(
                        row.get("activity_type"), "activity type"
                    ),
                    "status": _text(row.get("status"), "activity status"),
                }
            )
        if not next_token:
            if len(result) != declared_total:
                raise TikTokPromotionBlocked(
                    "promotion_activity_total_mismatch",
                    "official promotion activity total is incomplete",
                )
            return result
        page_token = next_token
    raise TikTokPromotionBlocked(
        "promotion_activity_page_limit_exceeded",
        "official promotion activity pagination did not terminate",
    )


def _activity_detail(
    response: Mapping[str, Any],
    *,
    expected_activity_id: str,
) -> dict[str, Any]:
    if not isinstance(response, Mapping) or response.get("code") != 0:
        raise TikTokPromotionBlocked(
            "promotion_activity_detail_failed",
            "official promotion activity detail failed",
        )
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise TikTokPromotionBlocked(
            "promotion_activity_detail_shape_invalid",
            "official promotion activity detail has invalid shape",
        )
    activity_id = _activity_id(data)
    begin = data.get("begin_time")
    end = data.get("end_time")
    products = data.get("products")
    if (
        activity_id != expected_activity_id
        or _text(data.get("activity_type"), "activity type")
        != "DIRECT_DISCOUNT"
        or _text(data.get("status"), "activity status") != "ONGOING"
        or type(begin) is not int
        or type(end) is not int
        or begin <= 0
        or end <= begin
        or not isinstance(products, list)
        or any(not isinstance(row, Mapping) for row in products)
    ):
        raise TikTokPromotionBlocked(
            "promotion_activity_detail_shape_invalid",
            "official promotion activity detail has invalid shape",
        )
    return {
        "activity_id": activity_id,
        "activity_type": "DIRECT_DISCOUNT",
        "status": "ONGOING",
        "begin_time": begin,
        "end_time": end,
        "products": [dict(row) for row in products],
    }


def _product_snapshot(
    response: Mapping[str, Any],
    *,
    expected_product_id: str,
    expected_skus: tuple[str, ...],
    expected_price: str,
    expected_currency: str,
) -> dict[str, Any]:
    if not isinstance(response, Mapping) or response.get("code") != 0:
        raise TikTokPromotionBlocked(
            "promotion_product_readback_failed",
            "official storefront product readback failed",
        )
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise TikTokPromotionBlocked(
            "promotion_product_shape_invalid",
            "official storefront product shape is invalid",
        )
    product_id = str(data.get("id") or data.get("product_id") or "")
    status = str(
        data.get("product_status") or data.get("status") or ""
    ).upper()
    rows = data.get("skus")
    if (
        product_id != expected_product_id
        or status != "ACTIVATE"
        or not isinstance(rows, list)
        or not rows
        or any(not isinstance(row, Mapping) for row in rows)
    ):
        raise TikTokPromotionBlocked(
            "promotion_product_shape_invalid",
            "official storefront product shape is invalid",
        )
    observed_skus: list[str] = []
    for row in rows:
        sku = _text(row.get("seller_sku"), "seller SKU")
        price = row.get("price")
        if not isinstance(price, Mapping):
            raise TikTokPromotionBlocked(
                "promotion_product_price_shape_invalid",
                "official storefront product price shape is invalid",
            )
        sale = _decimal(price.get("sale_price"), "official sale price")
        currency = _text(
            price.get("currency"),
            "official price currency",
        )
        if (
            str(sale) != expected_price
            or currency != expected_currency
        ):
            raise TikTokPromotionBlocked(
                "promotion_product_price_drift",
                "official storefront price drifted from approved plan",
                category="CONTENT",
            )
        observed_skus.append(sku)
    if (
        sorted(observed_skus) != sorted(expected_skus)
        or len(observed_skus) != len(set(observed_skus))
    ):
        raise TikTokPromotionBlocked(
            "promotion_product_sku_drift",
            "official storefront SKU identity drifted from approved plan",
            category="CONTENT",
        )
    return {
        "product_id": product_id,
        "seller_skus": tuple(sorted(observed_skus)),
    }


def _require_exact_discount_readback(
    activity: Mapping[str, Any],
    *,
    product_id: str,
    discount: int,
) -> None:
    matches = []
    for row in activity["products"]:
        observed_id = str(row.get("id") or row.get("product_id") or "")
        if observed_id == product_id:
            matches.append(row)
    if len(matches) != 1:
        raise TikTokPromotionDispatchError(
            "official promotion product identity is not exact",
            unknown=False,
            external_write_count=1,
            lower_bound=1,
            upper_bound=1,
        )
    if _decimal(matches[0].get("discount"), "discount") != Decimal(
        discount
    ):
        raise TikTokPromotionDispatchError(
            "official promotion discount did not converge",
            unknown=False,
            external_write_count=1,
            lower_bound=1,
            upper_bound=1,
        )


def _validate_command_and_proof(
    command: Mapping[str, Any],
    proof: Mapping[str, Any],
    target: str,
) -> None:
    if (
        command.get("schema_version") != PREPARED_SCHEMA
        or proof.get("schema_version") != PROOF_SCHEMA
        or command.get("target_label") != target
        or proof.get("target_label") != target
        or command.get("activity_identity_digest")
        != proof.get("activity_identity_digest")
        or command.get("product_identity_digest")
        != proof.get("product_identity_digest")
        or command.get("discount_percent") != 32
        or proof.get("discount_percent") != 32
        or command.get("selection_policy")
        != PROMOTION_SELECTION_POLICY
        or proof.get("selection_policy")
        != PROMOTION_SELECTION_POLICY
        or not _is_digest(command.get("action_policy_digest"))
        or command.get("action_policy_digest")
        != proof.get("action_policy_digest")
    ):
        raise TikTokPromotionPreDispatchError(
            "prepared promotion command/proof identity drifted"
        )


def _approved_price(
    payload: Mapping[str, Any],
    target: str,
) -> tuple[str, str]:
    pricing = _mapping(payload.get("pricing"), "pricing")
    selected = _mapping(
        pricing.get("selected_targets"), "selected pricing"
    )
    row = _mapping(selected.get(target), "target pricing")
    prices = row.get("store_prices")
    if (
        not isinstance(prices, list)
        or len(prices) != 1
        or not isinstance(prices[0], Mapping)
    ):
        raise TikTokPromotionBlocked(
            "approved_store_price_not_unique",
            "exactly one approved storefront list price is required",
            category="CONTENT",
        )
    return (
        str(_decimal(prices[0].get("list_price"), "approved list price")),
        _text(prices[0].get("currency"), "approved currency"),
    )


def _approved_model_skus(payload: Mapping[str, Any]) -> tuple[str, ...]:
    lineage = _mapping(payload.get("sku_lineage"), "SKU lineage")
    assignment = _mapping(lineage.get("assignment"), "SKU assignment")
    rows = assignment.get("model_skus")
    if not isinstance(rows, list) or any(
        not isinstance(row, Mapping) for row in rows
    ):
        raise TikTokPromotionBlocked(
            "approved_model_sku_lineage_invalid",
            "approved model SKU lineage is invalid",
            category="CONTENT",
        )
    values = tuple(
        sorted(_text(row.get("model_sku"), "model SKU") for row in rows)
    )
    if not values or len(values) != len(set(values)):
        raise TikTokPromotionBlocked(
            "approved_model_sku_lineage_invalid",
            "approved model SKU lineage is invalid",
            category="CONTENT",
        )
    return values


def _configured_shop(target: str) -> tuple[str, str]:
    from modules.sourcing.new_product_workbench import SEA_MARKETS

    site = target.split(":", 1)[1].casefold()
    exact = [
        row
        for row in SEA_MARKETS
        if row.get("id") == site
        and row.get("shop") == "LivelyHive"
        and row.get("region") == site.rsplit("_", 1)[-1].upper()
        and type(row.get("shop_id")) is int
        and row["shop_id"] > 0
    ]
    if len(exact) != 1:
        raise TikTokPromotionBlocked(
            "configured_livelyhive_shop_invalid",
            "configured LivelyHive shop identity is invalid",
        )
    return str(exact[0]["shop_id"]), "LivelyHive"


def _activity_id(row: Mapping[str, Any]) -> str:
    return _text(
        row.get("id") or row.get("activity_id"),
        "activity id",
    )


def _activity_identity_digest(activity: Mapping[str, Any]) -> str:
    return _digest(
        {
            "activity_id": activity["activity_id"],
            "activity_type": activity["activity_type"],
            "begin_time": activity["begin_time"],
            "end_time": activity["end_time"],
        }
    )


def _product_identity_digest(
    product_id: str,
    seller_skus: tuple[str, ...],
    price: str,
    currency: str,
) -> str:
    return _digest(
        {
            "product_id": product_id,
            "seller_skus": list(seller_skus),
            "price": price,
            "currency": currency,
        }
    )


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TikTokPromotionBlocked(
            "promotion_contract_shape_invalid",
            f"{field} is invalid",
            category="SYSTEMIC_CONTRACT",
        )
    return value


def _text(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
    ):
        raise TikTokPromotionBlocked(
            "promotion_contract_shape_invalid",
            f"{field} is invalid",
            category="SYSTEMIC_CONTRACT",
        )
    return value


def _decimal(value: object, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise TikTokPromotionBlocked(
            "promotion_numeric_shape_invalid",
            f"{field} is invalid",
            category="CONTENT",
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise TikTokPromotionBlocked(
            "promotion_numeric_shape_invalid",
            f"{field} is invalid",
            category="CONTENT",
        ) from error
    if not number.is_finite() or number <= 0:
        raise TikTokPromotionBlocked(
            "promotion_numeric_shape_invalid",
            f"{field} is invalid",
            category="CONTENT",
        )
    return number.normalize()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _is_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "TikTokPromotionBlocked",
    "TikTokPromotionDispatchError",
    "TikTokPromotionPreDispatchError",
    "TikTokPromotionTransport",
    "configure_tiktok_promotion_transport_factory",
    "dispatch_postpublish_promotion",
    "prepare_postpublish_promotion",
    "promotion_adapter_policy_digest",
]
