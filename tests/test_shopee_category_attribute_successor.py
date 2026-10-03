from copy import deepcopy

import pytest

from shared_platform.shopee_category_attribute_successor import (
    build_shopee_category_attribute_successor_payload,
)
from test_approved_publication_snapshot import _approved_plan


def approval(product_id: str):
    return {
        "schema_version": "shopee-required-attribute-approval/v1",
        "status": "APPROVED",
        "offer_id": product_id,
        "platform": "shopee",
        "category": {
            "category_id": "101157",
            "category_name": "Wallpapers & Wall Stickers",
            "category_path": [
                {"id": "100636", "name": "Home & Living"},
                {"id": "100711", "name": "Decoration"},
                {"id": "101157", "name": "Wallpapers & Wall Stickers"},
            ],
        },
        "required_attributes": [{
            "attribute_id": 100818,
            "attribute_name": "Seasonal Decoration",
            "attribute_value_list": [{"value_id": 4228, "original_value_name": "No"}],
        }],
        "decision_basis": {
            "authority": "SHOPEE_OFFICIAL",
            "source_evidence_digest": "sha256:" + "9" * 64,
            "user_authorization": "Offer 3956742887 approved for publication",
        },
        "approval_scope": "SHOPEE_REQUIRED_CATEGORY_ATTRIBUTES_ONLY",
        "approved_by": "Kyle",
        "platform_product_write_count": 0,
        "external_writes_performed": [],
    }


def test_successor_changes_only_shopee_category_contract():
    predecessor = _approved_plan()["payload"]
    snapshot = {"schema_version": "approved-publication-snapshot/v4", "offer_id": predecessor["product_id"], "snapshot_digest": "sha256:" + "a" * 64}
    result = build_shopee_category_attribute_successor_payload(
        predecessor, predecessor_snapshot=snapshot, approval=approval(predecessor["product_id"])
    )
    actual = deepcopy(result)
    actual.pop("plan_id")
    actual.pop("shopee_category_attribute_evidence")
    expected = deepcopy(predecessor)
    expected.pop("plan_id")
    expected["shopee_global_master"]["category_decision"] = actual["shopee_global_master"]["category_decision"]
    assert actual == expected
    assert result["shopee_global_master"]["category_decision"]["required_attributes"] == [{
        "attribute_id": 100818,
        "attribute_value_list": [{"value_id": 4228, "original_value_name": "No"}],
    }]


def test_successor_rejects_nonzero_platform_write_evidence():
    predecessor = _approved_plan()["payload"]
    document = approval(predecessor["product_id"])
    document["platform_product_write_count"] = 1
    with pytest.raises(ValueError, match="approval is invalid"):
        build_shopee_category_attribute_successor_payload(
            predecessor,
            predecessor_snapshot={"schema_version": "approved-publication-snapshot/v4", "offer_id": predecessor["product_id"], "snapshot_digest": "sha256:" + "a" * 64},
            approval=document,
        )


def test_successor_binds_validated_preview_digest_without_recomputing():
    predecessor = _approved_plan()["payload"]
    preview_digest = "sha256:" + "b" * 64
    result = build_shopee_category_attribute_successor_payload(
        predecessor,
        predecessor_snapshot={
            "schema_version": "publication-snapshot-preview/v1",
            "offer_id": predecessor["product_id"],
            "preview_digest": preview_digest,
        },
        approval=approval(predecessor["product_id"]),
    )
    assert result["shopee_category_attribute_evidence"]["source_snapshot_digest"] == preview_digest
