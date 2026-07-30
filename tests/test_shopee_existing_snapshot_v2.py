from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from shared_platform.shopee_global_plan import (
    APPROVED_EXISTING_PLAN_SCHEMA_VERSION,
    EXISTING_CURRENT_SNAPSHOT_SCHEMA_VERSION,
    EXISTING_GLOBAL,
    EXISTING_GLOBAL_PERMISSIONS,
    OFFICIAL_AUTHORITY,
    OFFICIAL_OBSERVATION_SCHEMA_VERSION,
    READY,
    ShopeeGlobalPlanContractError,
    approve_shopee_global_plan,
    build_shopee_existing_current_snapshot_candidate,
    rehydrate_approved_shopee_global_plan,
    serialize_approved_shopee_global_plan,
)
from shared_platform.target_scoped_release_contracts import (
    approved_shopee_copy_digest,
    approved_source_image_manifest_digest,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _args() -> dict[str, object]:
    title = "Approved English title"
    description = "Exact approved English description."
    source_url = "https://source.example/image.jpg"
    return {
        "observation_authority": OFFICIAL_AUTHORITY,
        "observation_schema_version": (
            OFFICIAL_OBSERVATION_SCHEMA_VERSION
        ),
        "observation_evidence_digest": _digest("observation"),
        "source_identity_schema_version": "source-product-identity/v1",
        "source_identity_digest": _digest("source"),
        "sku_lineage_schema_version": "new-source-sku-reservation/v1",
        "sku_lineage_digest": _digest("lineage"),
        "content_package_digest": _digest("content"),
        "title": title,
        "description": description,
        "approved_copy_digest": approved_shopee_copy_digest(
            title, description
        ),
        "ordered_approved_images": [
            {
                "source_url": source_url,
                "source_image_digest": _digest("source-image"),
            }
        ],
        "approved_source_image_manifest_digest": (
            approved_source_image_manifest_digest([source_url])
        ),
        "selected_image_positions": [1],
        "parcel": {
            "weight_kg": "0.2",
            "length_cm": "43",
            "width_cm": "5",
            "height_cm": "5",
            "contract_digest": _digest("parcel"),
        },
        "target_pricing": {
            "currency": "CNY",
            "global_original_price": "56.05",
            "contract_digest": _digest("pricing"),
        },
        "policy_digest": _digest("policy"),
        "expected_model_skus": ["0954"],
        "existing_global_item": {
            "global_item_id": 57115039489,
            "global_item_name": title,
            "description": description,
            "image": {
                "image_url_list": [
                    "https://official.example/rehost.jpg"
                ],
                "image_id_list": ["official-image-1"],
            },
            "category_id": 101157,
            "attribute_list": [
                {
                    "attribute_id": 1001,
                    "attribute_value_list": [
                        {
                            "value_id": 0,
                            "original_value_name": "PVC",
                        }
                    ],
                }
            ],
            "brand": {
                "brand_id": 0,
                "original_brand_name": "No Brand",
            },
            "seller_stock": [{"location_id": "CNZ", "stock": 200}],
            "condition": "NEW",
            "pre_order": {
                "is_pre_order": False,
                "days_to_ship": 0,
            },
            "tier_variation": [
                {
                    "name": "Style",
                    "option_list": [{"option": "Default"}],
                }
            ],
        },
        "existing_global_models": [
            {
                "global_model_id": 99,
                "global_model_sku": "0954",
                "tier_index": [0],
            }
        ],
        "existing_global_identity_evidence_digest": _digest("identity"),
    }


def _approved(args: dict[str, object]):
    candidate = build_shopee_existing_current_snapshot_candidate(**args)
    assert candidate.status == READY
    return candidate, approve_shopee_global_plan(
        candidate,
        approved_by="Kyle",
        confirm_approved_shopee_global_plan=True,
        expected_candidate_digest=candidate.candidate_digest,
    )


def test_v2_existing_snapshot_roundtrips_and_is_preserve_only():
    candidate, approved = _approved(_args())
    assert candidate.mode == EXISTING_GLOBAL
    assert approved.schema_version == APPROVED_EXISTING_PLAN_SCHEMA_VERSION
    serialized = serialize_approved_shopee_global_plan(approved)
    restored = rehydrate_approved_shopee_global_plan(serialized)
    execution = restored.server_owned_execution_payload(candidate)

    assert execution["plan"]["current_snapshot"]["schema_version"] == (
        EXISTING_CURRENT_SNAPSHOT_SCHEMA_VERSION
    )
    assert execution["plan"]["permissions"] == EXISTING_GLOBAL_PERMISSIONS
    assert execution["plan"]["current_snapshot"]["global_item_id"] == (
        57115039489
    )
    assert execution["plan"]["current_snapshot"]["global_model"] == [
        {
            "global_model_id": 99,
            "global_model_sku": "0954",
            "tier_index": [0],
        }
    ]
    public = json.dumps(
        approved.public_projection(), ensure_ascii=False, sort_keys=True
    )
    for forbidden in (
        "57115039489",
        "official-image-1",
        "official.example",
        "Approved English title",
        "0954",
        "CNZ",
    ):
        assert forbidden not in public


@pytest.mark.parametrize("brand", [None, {"brand_id": 0, "original_brand_name": "No Brand"}])
def test_brand_is_explicit_and_digest_bound(brand):
    args = _args()
    args["existing_global_item"]["brand"] = brand
    candidate, approved = _approved(args)
    stored = approved.server_owned_execution_payload(candidate)
    assert stored["plan"]["current_snapshot"]["brand"] == brand


def test_missing_brand_fails_closed():
    args = _args()
    del args["existing_global_item"]["brand"]
    candidate = build_shopee_existing_current_snapshot_candidate(**args)
    assert candidate.status != READY
    assert candidate.blocker_codes == ("existing_current_snapshot_invalid",)


def test_permission_or_snapshot_tamper_is_rejected():
    _candidate, approved = _approved(_args())
    record = json.loads(serialize_approved_shopee_global_plan(approved))
    record["approved_plan"]["plan"]["permissions"]["global_create"] = True
    tampered = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(tampered)

