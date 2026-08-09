from copy import deepcopy
import hashlib
import json

import pytest

from domains.product_operations.approved_publication_snapshot import (
    APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION,
    ApprovedPublicationSnapshotError,
    approved_publication_snapshot_from_payload,
    build_approved_publication_snapshot,
    validate_approved_publication_snapshot,
)
from domains.product_operations.source_identity import (
    resolve_source_product_identity,
)


def _sha(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _approved_plan():
    source_resolution = resolve_source_product_identity(
        collect_box={
            "source_item_id": "986159122616",
            "itemNum": "JD5047（38*45cm）",
        },
        precollect={"source_id": "986159122616"},
        source_authority="1688",
    )
    assert source_resolution.identity is not None
    source_identity = source_resolution.identity.payload()
    source_digest = source_identity["identity_digest"]
    content_digest = _sha({"content": "approved-content:r4"})
    policy_digest = _sha({"policy": "publication-policy:v7"})
    category_digest = _sha({"category": "wall-stickers"})
    pricing_digest = _sha({"pricing": "r42"})
    lineage_digest = _sha({"lineage": "0958-0959"})
    targets = ["tiktok:LH_PH", "shopee:PH", "ozon:RU"]
    payload = {
        "plan_id": "release-plan:3838616043:r42",
        "product_id": "3838616043",
        "product_revision": 42,
        "seller_sku": "0958",
        "targets": targets,
        "product_package_id": "product:3838616043:r42",
        "content_package_id": "content:3838616043:r4",
        "source_product_identity": source_identity,
        "sku_lineage": {
            "source_identity_digest": source_digest,
            "reservation_digest": lineage_digest,
            "assignment": {
                "seller_sku": "0958",
                "model_skus": [
                    {"variant_key": "blue-38x45", "model_sku": "0958"},
                    {"variant_key": "pink-38x45", "model_sku": "0959"},
                ],
            },
        },
        "product_facts": {
            "title": "Bear Peekaboo PVC Wall Sticker",
            "description": "Removable waterproof wall sticker for nursery decor.",
            "image_urls": [
                "https://img.example/main-1.jpg",
                "https://img.example/main-2.jpg",
            ],
            "category": {
                "id": "wall-stickers",
                "name": "Home > Wall Stickers",
            },
            "selected_sku_keys": ["blue-38x45", "pink-38x45"],
            "sku_commercial_facts": {
                "blue-38x45": {
                    "specification": {"color": "Blue", "size": "38x45cm"},
                    "cost": {"amount": "8.1", "currency": "CNY"},
                    "weight_kg": "0.2",
                    "package_cm": ["38", "45", "0.2"],
                    "image_urls": ["https://img.example/blue.jpg"],
                },
                "pink-38x45": {
                    "specification": {"color": "Pink", "size": "38x45cm"},
                    "cost": {"amount": "8.3", "currency": "CNY"},
                    "weight_kg": "0.21",
                    "package_cm": ["38", "45", "0.2"],
                    "image_urls": ["https://img.example/pink.jpg"],
                },
            },
        },
        "pricing": {
            "selected_targets": {
                "tiktok:LH_PH": {
                    "sku_prices": [
                        {"model_sku": "0958", "list_price": "129", "currency": "PHP"},
                        {"model_sku": "0959", "list_price": "132", "currency": "PHP"},
                    ]
                },
                "shopee:PH": {
                    "sku_prices": [
                        {"model_sku": "0958", "list_price": "125", "currency": "PHP"},
                        {"model_sku": "0959", "list_price": "128", "currency": "PHP"},
                    ]
                },
                "ozon:RU": {
                    "sku_prices": [
                        {"model_sku": "0958", "list_price": "799", "currency": "RUB"},
                        {"model_sku": "0959", "list_price": "819", "currency": "RUB"},
                    ]
                },
            }
        },
        "digests": {
            "source": source_digest,
            "content": content_digest,
            "policy": policy_digest,
            "category": category_digest,
            "pricing": pricing_digest,
            "sku_lineage": lineage_digest,
        },
    }
    payload_digest = _sha(payload)
    return {
        "plan_id": payload["plan_id"],
        "product_id": payload["product_id"],
        "targets": targets,
        "payload": payload,
        "payload_digest": payload_digest,
        "status": "APPROVED",
        "approved_at": "2026-08-09T09:30:00+08:00",
        "approval": {
            "status": "APPROVED",
            "approved_by": "Kyle",
            "approved_at": "2026-08-09T09:30:00+08:00",
            "user_approved": True,
            "plan_id": payload["plan_id"],
            "payload_digest": payload_digest,
        },
    }


def _rebind(plan):
    digest = _sha(plan["payload"])
    plan["payload_digest"] = digest
    plan["approval"]["payload_digest"] = digest
    plan["plan_id"] = plan["payload"]["plan_id"]
    plan["product_id"] = plan["payload"]["product_id"]
    plan["targets"] = list(plan["payload"]["targets"])
    plan["approval"]["plan_id"] = plan["payload"]["plan_id"]
    return plan


def test_builds_self_contained_multisku_approved_snapshot():
    snapshot = build_approved_publication_snapshot(_approved_plan())

    document = snapshot.payload()
    assert document["schema_version"] == APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION
    assert document["offer_id"] == "3838616043"
    assert document["product_revision"] == 42
    assert [row["model_sku"] for row in document["skus"]] == ["0958", "0959"]
    assert set(document["skus"][0]["prices"]) == {
        "tiktok:LH_PH",
        "shopee:PH",
        "ozon:RU",
    }
    assert document["skus"][0]["parcel"]["package_cm"] == ["38", "45", "0.2"]
    assert document["skus"][1]["variant_images"] == ["https://img.example/pink.jpg"]
    assert document["product"]["category"]["id"] == "wall-stickers"
    assert document["product"]["source_identity"]["source_offer_id"] == "986159122616"
    assert document["product"]["source_identity"]["source_item_code"] == (
        "JD5047（38*45cm）"
    )
    assert len(document["product"]["source_identity"]["provenance"]) == 2
    assert document["snapshot_digest"].startswith("sha256:")


def test_same_approval_is_idempotent_and_round_trips_from_json_payload():
    plan = _approved_plan()

    first = build_approved_publication_snapshot(plan)
    second = build_approved_publication_snapshot(deepcopy(plan))
    restored = approved_publication_snapshot_from_payload(
        json.loads(json.dumps(first.payload(), ensure_ascii=False))
    )

    assert first.snapshot_digest == second.snapshot_digest
    assert first.canonical_json() == second.canonical_json()
    assert restored == first
    assert validate_approved_publication_snapshot(restored) == first
    assert validate_approved_publication_snapshot(first.payload()) == first


def test_frozen_snapshot_is_detached_from_all_mutable_upstream_views():
    plan = _approved_plan()
    snapshot = build_approved_publication_snapshot(plan)
    frozen = snapshot.payload()
    current_dashboard = {
        "title": "New mutable title",
        "source": {"source_offer_id": "111111"},
        "content": {"images": []},
    }

    plan["payload"]["product_facts"]["title"] = "Changed after approval"
    plan["payload"]["product_facts"]["image_urls"].clear()
    plan["payload"]["source_product_identity"]["source_offer_id"] = "111111"
    current_dashboard["title"] = "Changed again"
    returned = snapshot.payload()
    returned["product"]["title"] = "Caller mutation"

    assert snapshot.payload() == frozen
    assert snapshot.payload()["product"]["title"] == (
        "Bear Peekaboo PVC Wall Sticker"
    )
    assert snapshot.snapshot_digest == frozen["snapshot_digest"]


def test_successor_revision_produces_a_new_snapshot_digest():
    first_plan = _approved_plan()
    successor = deepcopy(first_plan)
    successor["payload"]["product_revision"] = 43
    successor["payload"]["plan_id"] = "release-plan:3838616043:r43"
    _rebind(successor)

    first = build_approved_publication_snapshot(first_plan)
    second = build_approved_publication_snapshot(successor)

    assert second.payload()["product_revision"] == 43
    assert second.snapshot_digest != first.snapshot_digest


def test_tampering_is_rejected_even_when_the_document_remains_valid_json():
    document = build_approved_publication_snapshot(_approved_plan()).payload()
    document["product"]["title"] = "Tampered title"

    with pytest.raises(ApprovedPublicationSnapshotError, match="tampered"):
        approved_publication_snapshot_from_payload(document)


@pytest.mark.parametrize(
    "mutate,error",
    [
        (
            lambda plan: plan["payload"]["pricing"]["selected_targets"].pop(
                "ozon:RU"
            ),
            "pricing target coverage",
        ),
        (
            lambda plan: plan["payload"]["pricing"]["selected_targets"][
                "shopee:PH"
            ]["sku_prices"].pop(),
            "SKU price coverage",
        ),
        (
            lambda plan: plan["payload"]["product_facts"][
                "sku_commercial_facts"
            ].pop("pink-38x45"),
            "SKU coverage",
        ),
        (
            lambda plan: plan["payload"]["sku_lineage"]["assignment"].update(
                seller_sku="0960"
            ),
            "seller_sku conflicts",
        ),
        (
            lambda plan: plan["payload"]["sku_lineage"].update(
                source_identity_digest=_sha({"different": "source"})
            ),
            "source identity conflicts",
        ),
        (
            lambda plan: plan["payload"]["product_facts"].update(category={}),
            "category requires id and name",
        ),
        (
            lambda plan: plan["payload"].update(product_revision=True),
            "built-in int",
        ),
    ],
)
def test_builder_fails_closed_on_incomplete_or_conflicting_approval_facts(
    mutate, error
):
    plan = _approved_plan()
    mutate(plan)
    _rebind(plan)

    with pytest.raises(ApprovedPublicationSnapshotError, match=error):
        build_approved_publication_snapshot(plan)


@pytest.mark.parametrize(
    "approval_change,error",
    [
        ({"user_approved": 1}, "user_approved=True"),
        ({"approved_by": ""}, "approved_by"),
        ({"approved_at": "2026-08-09T09:30:00"}, "timezone"),
        ({"payload_digest": "sha256:" + "0" * 64}, "approval payload_digest"),
    ],
)
def test_approval_types_and_binding_are_strict(approval_change, error):
    plan = _approved_plan()
    plan["approval"].update(approval_change)

    with pytest.raises(ApprovedPublicationSnapshotError, match=error):
        build_approved_publication_snapshot(plan)


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda body: body["skus"].clear(), "at least one SKU"),
        (
            lambda body: body["skus"][1].update(model_sku="0958"),
            "identities conflict",
        ),
        (
            lambda body: body["skus"][0]["prices"].pop("tiktok:LH_PH"),
            "price coverage",
        ),
        (
            lambda body: body["publication_targets"][0].update(store="OTHER"),
            "target identity conflicts",
        ),
        (
            lambda body: body["product"]["source_identity"].update(
                source_offer_id=True
            ),
            "source_offer_id",
        ),
    ],
)
def test_deserializer_rejects_malformed_payload_even_with_recomputed_digest(
    mutate, error
):
    document = build_approved_publication_snapshot(_approved_plan()).payload()
    document.pop("snapshot_digest")
    mutate(document)
    document["snapshot_digest"] = _sha(document)

    with pytest.raises(ApprovedPublicationSnapshotError, match=error):
        approved_publication_snapshot_from_payload(document)


def test_snapshot_payload_is_plain_json_without_runtime_contract_dependencies():
    snapshot = build_approved_publication_snapshot(_approved_plan())

    encoded = json.dumps(snapshot.payload(), ensure_ascii=False, allow_nan=False)

    assert "ApprovedProductPackage" not in encoded
    assert "ContentPackage" not in encoded
    assert "986159122616" in encoded
    assert "approved-publication-snapshot/v4" in encoded
