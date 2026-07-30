"""Read-only Shopee global-plan candidate observation.

This module deliberately stops before a release command is built.  It scans
every existing-global status through the already audited official reader, but
does not call category, attribute, brand, or warehouse endpoints until a
first-party fixture is available.  A community generated SDK is useful as an
endpoint hint, not as production authority.  The observer is therefore not
registered in the production one-click adapter.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json

from modules.shopee.oneclick_release import (
    ShopeePrepareTransport,
    _scan_global_model_candidates,
)


SCHEMA_VERSION = "shopee-global-plan-candidate/v1"
ENDPOINT_EVIDENCE_LEVEL = "third_party_generated_sdk_unverified"
GENERATED_SDK_COMMIT = "c229515cf349d55d687035f16e9691cb80663562"
GENERATED_SDK_ENDPOINT_HINTS = (
    {
        "scope": "category_recommendation",
        "method": "GET",
        "path": "/api/v2/global_product/category_recommend",
        "request_fields": ["global_item_name"],
        "response_fields": ["response.category_id_list"],
    },
    {
        "scope": "attribute_tree",
        "method": "GET",
        "path": "/api/v2/global_product/get_attribute_tree",
        "request_fields": ["category_id", "language"],
        "response_fields": ["response.attribute_list"],
    },
    {
        "scope": "brand_list",
        "method": "GET",
        "path": "/api/v2/global_product/get_brand_list",
        "request_fields": [
            "category_id",
            "page_size",
            "offset",
            "status",
            "language",
        ],
        "response_fields": [
            "response.brand_list",
            "response.has_next_page",
            "response.next_offset",
            "response.total_count",
        ],
    },
    {
        "scope": "merchant_warehouse_locations",
        "method": "GET",
        "path": "/api/v2/merchant/get_merchant_warehouse_location_list",
        "request_fields": [],
        "response_fields": [
            "response[].location_id",
            "response[].warehouse_name",
        ],
    },
    {
        "scope": "merchant_warehouses",
        "method": "POST",
        "path": "/api/v2/merchant/get_merchant_warehouse_list",
        "request_fields": ["cursor", "warehouse_type"],
        "response_fields": [
            "response.warehouse_list",
            "response.cursor",
            "response.total_count",
        ],
    },
    {
        "scope": "shop_warehouse_detail",
        "method": "GET",
        "path": "/api/v2/shop/get_warehouse_detail",
        "request_fields": ["warehouse_type"],
        "response_fields": ["response[].location_id"],
    },
)


class ShopeeGlobalPlanCandidateError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.classification = "BLOCKED_CAPABILITY"
        self.reason_category = "CAPABILITY"
        self.reason_scope = "TARGET"
        self.reason_code = code


@dataclass(frozen=True)
class ShopeeGlobalPlanCandidateTransport:
    official_reads: ShopeePrepareTransport


def observe_shopee_global_plan_candidate(
    *,
    approved_title: str,
    approved_model_sku: str,
    transport: ShopeeGlobalPlanCandidateTransport,
) -> dict[str, object]:
    """Return a redacted candidate observation, never approved write facts."""
    if (
        type(approved_title) is not str
        or not approved_title.strip()
        or type(approved_model_sku) is not str
        or not approved_model_sku.strip()
        or not isinstance(transport, ShopeeGlobalPlanCandidateTransport)
    ):
        raise ShopeeGlobalPlanCandidateError(
            "shopee_global_candidate_input_invalid",
            "approved title, model SKU, and official read transport are required",
        )

    matches = _scan_global_model_candidates(
        transport.official_reads,
        model_sku=approved_model_sku.strip(),
    )
    existing = {
        "scan_mode": "NORMAL_UNLIST_BANNED_FULL",
        "matched_model_count": len(matches),
        "matched_global_identity_digests": sorted(
            _text_digest(value) for value in matches
        ),
    }
    if len(matches) > 1:
        raise ShopeeGlobalPlanCandidateError(
            "shopee_existing_global_model_ambiguous",
            "more than one official global item has the approved model SKU",
        )
    if len(matches) == 1:
        observation = {
            "schema_version": SCHEMA_VERSION,
            "status": "EXISTING_GLOBAL_OBSERVED",
            "planning_allowed": False,
            "existing_global": existing,
            "approved_title_digest": _text_digest(approved_title.strip()),
            "approved_model_sku_digest": _text_digest(
                approved_model_sku.strip()
            ),
            "category_candidates": [],
            "attribute_candidates": [],
            "brand_authority": {
                "status": "not_required_for_existing_observation",
                "endpoint": None,
            },
            "seller_location_authority": {
                "status": "not_required_for_existing_observation",
                "endpoint": None,
            },
            "blockers": [
                {
                    "category": "CAPABILITY",
                    "code": "shopee_existing_global_exact_proof_required",
                }
            ],
        }
        return _with_digest(observation)

    observation = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED_CAPABILITY",
        "planning_allowed": False,
        "existing_global": existing,
        "approved_title_digest": _text_digest(approved_title.strip()),
        "approved_model_sku_digest": _text_digest(
            approved_model_sku.strip()
        ),
        "category_candidates": [],
        "attribute_candidates": [],
        "endpoint_authority": {
            "status": "waiting_official_fixture",
            "evidence_level": ENDPOINT_EVIDENCE_LEVEL,
            "generated_sdk_commit": GENERATED_SDK_COMMIT,
            "endpoint_hints_digest": _digest(
                list(GENERATED_SDK_ENDPOINT_HINTS)
            ),
        },
        "brand_authority": {
            "status": "waiting_official_fixture",
            "endpoint": None,
        },
        "seller_location_authority": {
            "status": "waiting_official_fixture",
            "endpoint": None,
        },
        "blockers": [
            {
                "category": "CAPABILITY",
                "code": "shopee_official_global_candidate_fixture_required",
            },
            {
                "category": "CAPABILITY",
                "code": "shopee_global_brand_authority_unavailable",
            },
            {
                "category": "CAPABILITY",
                "code": "shopee_seller_location_authority_unavailable",
            },
        ],
    }
    return _with_digest(observation)


def _with_digest(value: Mapping[str, object]) -> dict[str, object]:
    result = dict(value)
    result["evidence_digest"] = _digest(result)
    return result


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
