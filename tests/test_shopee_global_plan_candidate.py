import json

import pytest

from modules.shopee import global_plan_candidate as subject
from modules.shopee.oneclick_release import (
    GLOBAL_LIST_PATH,
    GLOBAL_MODEL_PATH,
    ShopeeCredentials,
    ShopeePrepareTransport,
)


class _OfficialCandidateFake:
    def __init__(
        self,
        *,
        existing=False,
        malformed_list=None,
    ):
        self.existing = existing
        self.malformed_list = malformed_list
        self.calls = []

    def merchant_get(self, path, params):
        self.calls.append(("merchant_get", path, dict(params)))
        if path == GLOBAL_LIST_PATH:
            if self.malformed_list is not None:
                return self.malformed_list
            rows = []
            if self.existing and params["item_status"] == "NORMAL":
                rows = [{"global_item_id": 9}]
            return {
                "error": "",
                "response": {
                    "global_item_list": rows,
                    "total_count": len(rows),
                    "has_next_page": False,
                },
            }
        if path == GLOBAL_MODEL_PATH:
            return {
                "error": "",
                "response": {
                    "global_model": [
                        {
                            "global_model_id": 99,
                            "global_model_sku": "0954",
                            "tier_index": [0],
                        }
                    ]
                },
            }
        raise AssertionError(path)

    def shop_get(self, path, params):
        raise AssertionError((path, params))

    def transport(self):
        official = ShopeePrepareTransport(
            credentials=ShopeeCredentials(
                region="MY",
                shop_id=123,
                shop_token="fixture-shop-token",
                merchant_id=456,
                merchant_token="fixture-merchant-token",
            ),
            merchant_get=self.merchant_get,
            shop_get=self.shop_get,
        )
        return subject.ShopeeGlobalPlanCandidateTransport(
            official_reads=official,
        )


def _observe(fake):
    return subject.observe_shopee_global_plan_candidate(
        approved_title="Approved PVC wall decal",
        approved_model_sku="0954",
        transport=fake.transport(),
    )


def test_new_global_candidate_scans_all_statuses_and_waits_for_official_fixture():
    fake = _OfficialCandidateFake()
    result = _observe(fake)

    assert result["schema_version"] == subject.SCHEMA_VERSION
    assert result["status"] == "BLOCKED_CAPABILITY"
    assert result["planning_allowed"] is False
    assert result["existing_global"]["scan_mode"] == (
        "NORMAL_UNLIST_BANNED_FULL"
    )
    assert result["existing_global"]["matched_model_count"] == 0
    assert result["category_candidates"] == []
    assert result["attribute_candidates"] == []
    assert result["endpoint_authority"] == {
        "status": "waiting_official_fixture",
        "evidence_level": "third_party_generated_sdk_unverified",
        "generated_sdk_commit": (
            "c229515cf349d55d687035f16e9691cb80663562"
        ),
        "endpoint_hints_digest": (
            "bc3487644a048b52cec8f2e37d5619abc17e4c066bd145ff"
            "3bcf1741aa7b0fcc"
        ),
    }
    blocker_codes = {
        row["code"] for row in result["blockers"]
    }
    assert blocker_codes == {
        "shopee_official_global_candidate_fixture_required",
        "shopee_global_brand_authority_unavailable",
        "shopee_seller_location_authority_unavailable",
    }
    assert result["brand_authority"]["endpoint"] is None
    assert result["seller_location_authority"]["endpoint"] is None
    assert len(result["evidence_digest"]) == 64
    serialized = json.dumps(result, sort_keys=True)
    assert "CNZ" not in serialized
    assert "NoBrand" not in serialized
    scan_calls = [
        call
        for call in fake.calls
        if call[0] == "merchant_get" and call[1] == GLOBAL_LIST_PATH
    ]
    assert [call[2]["item_status"] for call in scan_calls] == [
        "NORMAL",
        "UNLIST",
        "BANNED",
    ]
    assert not any(call[0].endswith("_post") for call in fake.calls)


def test_existing_global_candidate_never_falls_through_to_create_fact_guessing():
    fake = _OfficialCandidateFake(existing=True)
    result = _observe(fake)

    assert result["status"] == "EXISTING_GLOBAL_OBSERVED"
    assert result["planning_allowed"] is False
    assert result["existing_global"]["matched_model_count"] == 1
    assert len(
        result["existing_global"]["matched_global_identity_digests"][0]
    ) == 64
    assert result["blockers"] == [
        {
            "category": "CAPABILITY",
            "code": "shopee_existing_global_exact_proof_required",
        }
    ]
    assert not any(call[0].endswith("_post") for call in fake.calls)


@pytest.mark.parametrize(
    "malformed",
    [
        {"error": "bad", "response": {}},
        {
            "error": "",
            "response": {
                "global_item_list": [],
                "total_count": True,
                "has_next_page": False,
            },
        },
        {
            "error": "",
            "response": {
                "global_item_list": [False],
                "total_count": 1,
                "has_next_page": False,
            },
        },
    ],
)
def test_full_global_scan_faults_fail_before_candidate_endpoints(malformed):
    fake = _OfficialCandidateFake(malformed_list=malformed)
    with pytest.raises(Exception):
        _observe(fake)
    assert not any(call[0].endswith("_post") for call in fake.calls)


def test_generated_sdk_hints_are_redacted_non_authoritative_metadata():
    assert subject.ENDPOINT_EVIDENCE_LEVEL == (
        "third_party_generated_sdk_unverified"
    )
    assert {
        (row["method"], row["path"])
        for row in subject.GENERATED_SDK_ENDPOINT_HINTS
    } == {
        ("GET", "/api/v2/global_product/category_recommend"),
        ("GET", "/api/v2/global_product/get_attribute_tree"),
        ("GET", "/api/v2/global_product/get_brand_list"),
        (
            "GET",
            "/api/v2/merchant/get_merchant_warehouse_location_list",
        ),
        ("POST", "/api/v2/merchant/get_merchant_warehouse_list"),
        ("GET", "/api/v2/shop/get_warehouse_detail"),
    }
    serialized = json.dumps(
        subject.GENERATED_SDK_ENDPOINT_HINTS,
        sort_keys=True,
    )
    assert "token" not in serialized.casefold()
    assert "response body" not in serialized.casefold()
