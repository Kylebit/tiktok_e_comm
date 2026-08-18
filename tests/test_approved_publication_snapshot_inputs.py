from copy import deepcopy

import pytest

from domains.product_operations import (
    ApprovedPublicationSnapshotError,
    build_approved_publication_snapshot,
    build_approved_publication_snapshot_inputs,
)
from shared_platform.approved_publication_snapshot_projection import (
    project_release_plan_for_publication_snapshot,
)
from test_approved_publication_snapshot import _approved_plan, _rebind


def _raw_approval_inputs(*, sku_count: int = 2):
    approved = _approved_plan()
    payload = deepcopy(approved["payload"])
    payload["targets"].append("miaoshou:COMMON")
    payload["pricing"]["selected_targets"]["miaoshou:COMMON"] = {
        "sku_prices": [
            {
                "model_sku": row["model_sku"],
                "list_price": str(29 + index),
                "currency": "CNY",
            }
            for index, row in enumerate(
                payload["sku_lineage"]["assignment"]["model_skus"][:sku_count]
            )
        ]
    }
    payload["sku_lineage"]["assignment"]["model_skus"] = payload[
        "sku_lineage"
    ]["assignment"]["model_skus"][:sku_count]
    selected = ["blue-38x45", "pink-38x45"][:sku_count]
    payload["product_facts"] = {
        "title": "Approved refrigerator magnet title",
        "category": {"id": "", "name": "Home > Refrigerator Magnets"},
        "selected_sku_keys": selected,
        "sku_commercial_facts": {
            key: {
                "cost_cny": str(8 + index),
                "weight_kg": str(0.2 + index / 100),
                "package_cm": ["8", "8", "2"],
            }
            for index, key in enumerate(selected)
        },
        "selected_skus": [
            {
                "key": key,
                "label": label,
                "model_sku": model,
                "price_cny": str(8 + index),
                "commercial_facts": {
                    "cost_cny": str(8 + index),
                    "weight_kg": str(0.2 + index / 100),
                    "package_cm": ["8", "8", "2"],
                },
            }
            for index, (key, label, model) in enumerate(
                [
                    ("blue-38x45", "Blue 38 x 45 cm", "0958"),
                    ("pink-38x45", "Pink 38 x 45 cm", "0959"),
                ][:sku_count]
            )
        ],
        "shopee_global_variant_image_positions": [
            {"model_sku": model, "position": index}
            for index, model in enumerate(["0958", "0959"][:sku_count])
        ],
    }
    for target in payload["targets"]:
        rows = payload["pricing"]["selected_targets"][target]["sku_prices"]
        payload["pricing"]["selected_targets"][target]["sku_prices"] = rows[
            :sku_count
        ]
    payload.pop("digests", None)
    payload["listing_copy"] = {
        "status": "adopted_in_product_facts",
        "shopee_description_en": "Approved factual product description.",
        "input_signature": "sha256:copy-facts-v4",
        "current_input_signature": "sha256:copy-facts-v4",
    }
    payload["images"] = [
        {"position": 1, "image_url": "https://img.example/main.jpg"},
        {"position": 2, "image_url": "https://img.example/detail.jpg"},
    ]
    payload["approved_postpublish_promotion_policy"] = {
        "schema_version": "postpublish-promotion-policy/v1"
    }

    source_skus = [
        {
            "key": "blue-38x45",
            "label": "Blue 38 x 45 cm",
            "model_sku": "0958",
            "image_urls": ["https://img.example/blue.jpg"],
        },
        {
            "key": "pink-38x45",
            "label": "Pink 38 x 45 cm",
            "model_sku": "0959",
            "image_urls": ["https://img.example/pink.jpg"],
        },
    ][:sku_count]
    sku_commercial_facts = deepcopy(
        payload["product_facts"]["sku_commercial_facts"]
    )
    dashboard = {
        "product": {
            "offer_id": payload["product_id"],
            "revision": payload["product_revision"],
            "title": payload["product_facts"]["title"],
            "category": deepcopy(payload["product_facts"]["category"]),
            "seller_sku_candidate": payload["seller_sku"],
            "actual_product_approved": True,
            "actual_approval": {
                "package_id": payload["product_package_id"],
            },
            "selected_sku_keys": selected,
            "sku_commercial_facts": sku_commercial_facts,
            "source_skus": source_skus,
        },
        "content": {
            "approved": True,
            "package_id": payload["content_package_id"],
            "images": deepcopy(payload["images"]),
        },
        "listing_copy": deepcopy(payload["listing_copy"]),
        "publication_scope": {"selected_labels": list(payload["targets"])},
    }
    return dashboard, payload


def _approved_from_projected(payload):
    approved = _approved_plan()
    approved["payload"] = payload
    return _rebind(approved)


@pytest.mark.parametrize("sku_count", [1, 2])
def test_bridge_freezes_complete_v4_inputs_without_provider_category_guessing(
    sku_count,
):
    dashboard, payload = _raw_approval_inputs(sku_count=sku_count)

    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )
    projection = project_release_plan_for_publication_snapshot(
        payload,
        approved_inputs=inputs,
    )

    assert projection.ready is True, projection.missing_fields
    snapshot = build_approved_publication_snapshot(
        _approved_from_projected(projection.payload)
    ).payload()
    assert snapshot["product"]["description"] == (
        "Approved factual product description."
    )
    assert snapshot["product"]["images"] == [
        "https://img.example/main.jpg",
        "https://img.example/detail.jpg",
    ]
    assert snapshot["product"]["main_category"]["name"] == (
        "Home > Refrigerator Magnets"
    )
    assert snapshot["product"]["main_category"]["id"].startswith(
        "product-semantic:"
    )
    assert len(snapshot["skus"]) == sku_count
    assert snapshot["skus"][0]["specification"] == {
        "option": "Blue 38 x 45 cm"
    }
    assert snapshot["skus"][0]["variant_images"] == [
        "https://img.example/blue.jpg"
    ]
    assert snapshot["shopee_global_master"]["parcel_envelope"] == {
        "weight_kg": "0.2" if sku_count == 1 else "0.21000000000000002",
        "package_cm": [8, 8, 2],
        "policy_version": "shopee-global-parcel-ceil-cm/v1",
    }
    assert set(snapshot["digests"]) == {
        "source",
        "content",
        "policy",
        "category",
        "pricing",
        "sku_lineage",
    }
    for target, row in snapshot["categories_by_target"].items():
        if target == "miaoshou:COMMON":
            assert row["decision"]["status"] == "NOT_APPLICABLE"
        else:
            assert row["category"] is None
            assert row["decision"]["status"] == "DEFERRED_TO_SKILL"


def test_bridge_freezes_multisku_shopee_parcel_envelope_with_ceiled_dimensions():
    dashboard, payload = _raw_approval_inputs(sku_count=2)
    parcels = {
        "blue-38x45": {
            "cost_cny": "8",
            "weight_kg": "0.265",
            "package_cm": ["61", "2.1", "5.2"],
        },
        "pink-38x45": {
            "cost_cny": "9",
            "weight_kg": "0.21",
            "package_cm": ["60.1", "3", "5"],
        },
    }
    payload["product_facts"]["sku_commercial_facts"] = deepcopy(parcels)
    dashboard["product"]["sku_commercial_facts"] = deepcopy(parcels)

    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )

    assert inputs["shopee_global_master"]["parcel_envelope"] == {
        "weight_kg": "0.265",
        "package_cm": [61, 3, 6],
        "policy_version": "shopee-global-parcel-ceil-cm/v1",
    }


def test_bridge_does_not_treat_title_workflow_status_as_description_approval():
    """A frozen, exact description is valid even when title workflow metadata drifted."""

    dashboard, payload = _raw_approval_inputs(sku_count=1)
    dashboard["listing_copy"]["status"] = "superseded_product_facts_changed"
    payload["listing_copy"]["status"] = "superseded_product_facts_changed"

    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )

    assert inputs["description"] == "Approved factual product description."


def test_bridge_freezes_provider_price_lineage_from_real_derived_rows():
    dashboard, payload = _raw_approval_inputs(sku_count=1)
    payload["pricing"]["selected_targets"]["shopee:PH"]["sku_prices"] = [
        {
            "model_sku": "0958",
            "derived_preview": {
                "global_original_price_cny": "40.12",
                "local_original_price": "340",
                "source_currency": "PHP",
            },
        }
    ]
    payload["pricing"]["selected_targets"]["ozon:RU"]["sku_prices"] = [
        {
            "model_sku": "0958",
            "derived_preview": {
                "price_cny": "40",
                "old_price_cny": "52",
                "source_currency": "CNY",
            },
        }
    ]

    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )
    projection = project_release_plan_for_publication_snapshot(
        payload,
        approved_inputs=inputs,
    )

    assert projection.ready is True, projection.missing_fields
    snapshot = build_approved_publication_snapshot(
        _approved_from_projected(projection.payload)
    ).payload()
    prices = snapshot["skus"][0]["prices"]
    assert prices["shopee:PH"] == {
        "amount": "340",
        "currency": "PHP",
        "global_original_price_cny": "40.12",
    }
    assert prices["ozon:RU"] == {
        "amount": "40",
        "currency": "CNY",
        "old_price_cny": "52",
    }


@pytest.mark.parametrize("sku_count", [1, 2])
def test_sku_without_narrower_image_fact_binds_frozen_approved_product_images(
    sku_count,
):
    dashboard, payload = _raw_approval_inputs(sku_count=sku_count)
    for row in dashboard["product"]["source_skus"]:
        row.pop("image_urls")

    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )

    for key in payload["product_facts"]["selected_sku_keys"]:
        assert inputs["sku_details_by_key"][key]["image_urls"] == [
            "https://img.example/main.jpg",
            "https://img.example/detail.jpg",
        ]


def test_bridge_preserves_already_approved_provider_category_decisions():
    dashboard, payload = _raw_approval_inputs(sku_count=1)
    approved_categories = deepcopy(
        _approved_plan()["payload"]["product_facts"]["categories_by_target"]
    )
    approved_categories["miaoshou:COMMON"] = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )["categories_by_target"]["miaoshou:COMMON"]
    payload["product_facts"]["categories_by_target"] = approved_categories

    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )

    assert inputs["categories_by_target"] == approved_categories
    assert inputs["categories_by_target"]["tiktok:LH_PH"]["decision"][
        "status"
    ] == "APPROVED"


def test_bridge_output_is_detached_from_mutable_approval_inputs():
    dashboard, payload = _raw_approval_inputs(sku_count=1)
    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )

    dashboard["product"]["source_skus"][0]["image_urls"][0] = (
        "https://img.example/changed.jpg"
    )
    payload["listing_copy"]["shopee_description_en"] = "Changed later."

    assert inputs["description"] == "Approved factual product description."
    assert inputs["sku_details_by_key"]["blue-38x45"]["image_urls"] == [
        "https://img.example/blue.jpg"
    ]


@pytest.mark.parametrize(
    "mutate,error",
    [
        (
            lambda dashboard, payload: payload["listing_copy"].update(
                shopee_description_en=""
            ),
            "description",
        ),
        (
            lambda dashboard, payload: payload.update(images=[]),
            "images",
        ),
        (
            lambda dashboard, payload: dashboard["product"]["source_skus"][0].update(
                label=""
            ),
            "specification",
        ),
        (
            lambda dashboard, payload: dashboard["product"][
                "sku_commercial_facts"
            ].pop("blue-38x45"),
            "commercial fact coverage",
        ),
        (
            lambda dashboard, payload: dashboard["product"].update(
                offer_id="999999"
            ),
            "offer identity",
        ),
    ],
)
def test_bridge_fails_closed_for_missing_or_drifting_approved_facts(
    mutate,
    error,
):
    dashboard, payload = _raw_approval_inputs(sku_count=2)
    mutate(dashboard, payload)

    with pytest.raises(ApprovedPublicationSnapshotError, match=error):
        build_approved_publication_snapshot_inputs(
            dashboard=dashboard,
            release_plan_payload=payload,
        )


def test_deferred_target_category_digest_and_shape_cannot_be_tampered():
    dashboard, payload = _raw_approval_inputs(sku_count=1)
    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )
    projection = project_release_plan_for_publication_snapshot(
        payload,
        approved_inputs=inputs,
    )
    approved = _approved_from_projected(projection.payload)
    snapshot = build_approved_publication_snapshot(approved).payload()

    tampered = deepcopy(approved)
    tampered["payload"]["product_facts"]["categories_by_target"][
        "tiktok:LH_PH"
    ]["decision"]["decision_digest"] = "sha256:" + "0" * 64
    _rebind(tampered)
    with pytest.raises(ApprovedPublicationSnapshotError, match="decision digest"):
        build_approved_publication_snapshot(tampered)

    tampered = deepcopy(approved)
    row = tampered["payload"]["product_facts"]["categories_by_target"][
        "tiktok:LH_PH"
    ]
    row["category"] = {
        "id": snapshot["product"]["main_category"]["id"],
        "name": snapshot["product"]["main_category"]["name"],
        "path": [deepcopy(snapshot["product"]["main_category"])],
    }
    _rebind(tampered)
    with pytest.raises(ApprovedPublicationSnapshotError, match="deferred"):
        build_approved_publication_snapshot(tampered)
