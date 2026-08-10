from copy import deepcopy

import pytest

from domains.product_operations import (
    ApprovedPublicationSnapshotError,
    approved_publication_snapshot_from_payload,
    build_approved_publication_snapshot,
    build_approved_publication_snapshot_inputs,
)
from shared_platform.approved_publication_snapshot_projection import (
    project_release_plan_for_publication_snapshot,
)
from shared_platform.channel_category_decisions import (
    approve_category_decision,
    build_category_options,
    category_decision_plan_binding,
    serialize_category_decision,
)
from test_approved_publication_snapshot import _rebind, _sha
from test_approved_publication_snapshot_inputs import _raw_approval_inputs
from test_channel_category_decisions import (
    _context as _category_context,
    _creation_seed as _category_creation_seed,
    _observation as _category_observation,
)


_REGIONAL_PRICES = {
    "PH": ("lh_ph", "340", "PHP", "40.12"),
    "MY": ("lh_my", "27", "MYR", "47.25"),
    "TH": ("lh_th", "212", "THB", "47.02"),
    "VN": ("lh_vn", "246000", "VND", "65.44"),
}


def _offer_3882722296_inputs():
    dashboard, payload = _raw_approval_inputs(sku_count=1)
    payload["product_id"] = "3882722296"
    payload["product_revision"] = 40
    payload["plan_id"] = "omnichannel:offer-3882722296-v4-successor"
    payload["seller_sku"] = "0967"
    payload["sku_lineage"]["assignment"]["seller_sku"] = "0967"
    payload["sku_lineage"]["assignment"]["model_skus"] = [
        {"variant_key": "floral-magnet", "model_sku": "0967"}
    ]
    payload["product_facts"]["selected_sku_keys"] = ["floral-magnet"]
    payload["product_facts"]["selected_skus"] = [
        {
            "key": "floral-magnet",
            "label": "7cm*7cm",
            "model_sku": "0967",
            "price_cny": "4",
        }
    ]
    payload["product_facts"]["sku_commercial_facts"] = {
        "floral-magnet": {
            "cost_cny": "4",
            "weight_kg": "0.1",
            "package_cm": ["10", "10", "2"],
        }
    }
    payload["product_facts"]["shopee_global_variant_image_positions"] = [
        {"model_sku": "0967", "position": 0}
    ]
    for target in ("shopee:MY", "shopee:TH", "shopee:VN"):
        if target not in payload["targets"]:
            payload["targets"].append(target)
    selected = payload["pricing"]["selected_targets"]
    for target, row in list(selected.items()):
        if target == "miaoshou:COMMON":
            row["sku_prices"] = [
                {"model_sku": "0967", "list_price": "4", "currency": "CNY"}
            ]
        elif target.startswith("shopee:"):
            continue
        else:
            old = row["sku_prices"][0]
            row["sku_prices"] = [{**old, "model_sku": "0967"}]
    for region, (target_key, local, currency, cny) in _REGIONAL_PRICES.items():
        selected[f"shopee:{region}"] = {
            "source": {"region": region, "target_key": target_key},
            "sku_prices": [
                {
                    "model_sku": "0967",
                    "list_price": local,
                    "currency": currency,
                    "global_original_price_cny": cny,
                }
            ],
        }
    payload["pricing"]["master_price_source"] = {
        "region": "PH",
        "target_key": "lh_ph",
        "currency": "PHP",
    }

    dashboard["product"].update(
        {
            "offer_id": "3882722296",
            "revision": 40,
            "selected_sku_keys": ["floral-magnet"],
            "sku_commercial_facts": deepcopy(
                payload["product_facts"]["sku_commercial_facts"]
            ),
            "source_skus": [
                {
                    "key": "floral-magnet",
                    "label": "7cm*7cm",
                    "model_sku": "0967",
                    "image_urls": ["https://img.example/main.jpg"],
                }
            ],
        }
    )
    dashboard["publication_scope"]["selected_labels"] = list(payload["targets"])
    return dashboard, payload


def _snapshot(dashboard, payload):
    inputs = build_approved_publication_snapshot_inputs(
        dashboard=dashboard,
        release_plan_payload=payload,
    )
    projection = project_release_plan_for_publication_snapshot(
        payload,
        approved_inputs=inputs,
    )
    assert projection.ready is True, projection.missing_fields
    approved = {
        "plan_id": projection.payload["plan_id"],
        "product_id": projection.payload["product_id"],
        "targets": list(projection.payload["targets"]),
        "payload": projection.payload,
        "payload_digest": "",
        "status": "APPROVED",
        "approved_at": "2026-08-10T12:00:00+08:00",
        "approval": {
            "status": "APPROVED",
            "approved_by": "Kyle",
            "approved_at": "2026-08-10T12:00:00+08:00",
            "user_approved": True,
            "plan_id": projection.payload["plan_id"],
            "payload_digest": "",
        },
    }
    return build_approved_publication_snapshot(_rebind(approved)).payload()


def _attach_approved_global_category(payload):
    context = _category_context(revision=40)
    context["product_id"] = "3882722296"
    creation_seed = _category_creation_seed()
    creation_seed.update(
        {
            "model_sku": "0967",
            "selected_image_position": 1,
            "global_original_price_cny": "40.12",
        }
    )
    options = build_category_options(
        _category_observation(),
        context=context,
        creation_seed=creation_seed,
    )
    category = next(
        row for row in options["options"] if row["category_id"] == 101
    )
    brand = next(row for row in options["brand_options"] if row["recommended"])
    location = next(
        row for row in options["location_options"] if row["recommended"]
    )
    decision = approve_category_decision(
        options,
        product_id="3882722296",
        product_revision=40,
        selected_category_identity_digest=category["category_identity_digest"],
        selected_brand_identity_digest=brand["brand_identity_digest"],
        selected_location_identity_digest=location["location_identity_digest"],
        selected_creation_fact_identity_digest=options["creation_fact_option"][
            "creation_fact_identity_digest"
        ],
        attribute_selection_digest=_sha(
            {"fixture": "attribute-selection"}
        ).removeprefix("sha256:"),
        approved_by="Kyle",
        confirm_channel_category_selection=True,
        confirm_seller_stock_quantity=True,
        confirm_condition_and_preorder=True,
    )
    payload["approved_channel_category_decisions"] = {
        "shopee:GLOBAL": category_decision_plan_binding(decision)
    }
    payload["_channel_category_decision_records"] = {
        "shopee:GLOBAL": serialize_category_decision(decision)
    }
    return decision


def test_offer_3882722296_freezes_exact_ph_global_master_without_equalizing_regions():
    dashboard, payload = _offer_3882722296_inputs()

    document = _snapshot(dashboard, payload)

    master = document["shopee_global_master"]
    assert master["price_source"] == {
        "target_label": "shopee:PH",
        "region": "PH",
        "target_key": "lh_ph",
        "source_binding_digest": master["price_source"]["source_binding_digest"],
    }
    assert master["sku_original_prices_cny"] == [
        {"model_sku": "0967", "amount": "40.12", "currency": "CNY"}
    ]
    assert master["category_decision"]["status"] == "DEFERRED_TO_SKILL"
    assert master["category_decision"]["category"] is None
    assert master["category_decision"]["required_attributes"] == []
    assert master["category_decision"]["source_decision_digest"] is None
    assert master["policy"]["brand"] == {
        "brand_id": 0,
        "original_brand_name": "NoBrand",
        "policy_version": "shopee-global-fixed-no-brand/v1",
    }
    assert master["policy"]["condition"] == "NEW"
    assert master["policy"]["preorder"] == {
        "is_pre_order": False,
        "days_to_ship": 0,
    }
    assert master["policy"]["stock"] == {
        "quantity": 200,
        "policy_version": "shopee-global-fixed-stock/v1",
    }
    assert master["policy"]["warehouse"] == {
        "display_name": "中国仓库",
        "location_id": None,
        "policy_version": "shopee-global-fixed-china-warehouse/v1",
        "status": "DEFERRED_TO_SKILL",
    }
    assert master["variant_image_positions"] == [
        {
            "model_sku": "0967",
            "position": 0,
            "image_url": "https://img.example/main.jpg",
        }
    ]
    prices = document["skus"][0]["prices"]
    assert prices["shopee:PH"]["amount"] == "340"
    assert prices["shopee:MY"]["amount"] == "27"
    assert prices["shopee:TH"]["amount"] == "212"
    assert prices["shopee:VN"]["amount"] == "246000"
    assert {prices[f"shopee:{region}"]["global_original_price_cny"] for region in _REGIONAL_PRICES} == {
        "40.12", "47.25", "47.02", "65.44"
    }


def test_exact_approved_cnsc_category_and_required_attributes_are_frozen():
    dashboard, payload = _offer_3882722296_inputs()
    decision = _attach_approved_global_category(payload)

    document = _snapshot(dashboard, payload)

    master = document["shopee_global_master"]
    assert master["category_decision"] == {
        "status": "APPROVED",
        "category": {
            "id": "101",
            "name": "Wall Stickers",
            "path": [
                {"id": "10", "name": "Home"},
                {"id": "101", "name": "Wall Stickers"},
            ],
        },
        "required_attributes": decision["selected_attributes"],
        "source_decision_digest": "sha256:" + decision["decision_digest"],
        "decision_digest": master["category_decision"]["decision_digest"],
    }
    assert master["policy"]["brand"]["brand_id"] == 0
    assert master["policy"]["brand"]["original_brand_name"] == "NoBrand"
    assert master["policy"]["stock"]["quantity"] == 200
    assert master["policy"]["warehouse"] == {
        "display_name": "中国仓库",
        "location_id": "CN-A",
        "policy_version": "shopee-global-fixed-china-warehouse/v1",
        "status": "APPROVED",
    }

    tampered = deepcopy(document)
    tampered.pop("snapshot_digest")
    tampered_category = tampered["shopee_global_master"]["category_decision"][
        "category"
    ]
    tampered_category["name"] = "Tampered category"
    tampered_category["path"][-1]["name"] = "Tampered category"
    tampered["snapshot_digest"] = _sha(tampered)
    with pytest.raises(
        ApprovedPublicationSnapshotError,
        match="category decision digest",
    ):
        approved_publication_snapshot_from_payload(tampered)


def test_present_cnsc_category_requires_matching_binding_and_record():
    dashboard, payload = _offer_3882722296_inputs()
    _attach_approved_global_category(payload)
    payload["_channel_category_decision_records"].pop("shopee:GLOBAL")

    with pytest.raises(
        ApprovedPublicationSnapshotError,
        match="category decision is incomplete",
    ):
        build_approved_publication_snapshot_inputs(
            dashboard=dashboard,
            release_plan_payload=payload,
        )


@pytest.mark.parametrize(
    "mutate,error",
    [
        (
            lambda payload: payload["pricing"].pop("master_price_source"),
            "master price source",
        ),
        (
            lambda payload: payload["pricing"]["selected_targets"]["shopee:MY"][
                "source"
            ].update(target_key="lh_ph"),
            "master price source",
        ),
        (
            lambda payload: payload["pricing"]["master_price_source"].update(
                region="MY"
            ),
            "master price source",
        ),
    ],
)
def test_bridge_rejects_absent_ambiguous_or_drifting_global_master_source(
    mutate, error
):
    dashboard, payload = _offer_3882722296_inputs()
    mutate(payload)

    with pytest.raises(ApprovedPublicationSnapshotError, match=error):
        build_approved_publication_snapshot_inputs(
            dashboard=dashboard,
            release_plan_payload=payload,
        )


def test_snapshot_validator_rejects_master_price_from_the_wrong_region_even_with_new_digest():
    dashboard, payload = _offer_3882722296_inputs()
    document = _snapshot(dashboard, payload)
    document.pop("snapshot_digest")
    document["shopee_global_master"]["sku_original_prices_cny"][0][
        "amount"
    ] = "47.25"
    document["snapshot_digest"] = _sha(document)

    with pytest.raises(ApprovedPublicationSnapshotError, match="master price"):
        approved_publication_snapshot_from_payload(document)


def test_multisku_global_master_requires_exact_variant_image_positions():
    dashboard, payload = _raw_approval_inputs(sku_count=2)
    payload["product_facts"].pop("shopee_global_variant_image_positions")
    payload["pricing"]["master_price_source"] = {
        "region": "PH",
        "target_key": "lh_ph",
    }
    payload["pricing"]["selected_targets"]["shopee:PH"]["source"] = {
        "region": "PH",
        "target_key": "lh_ph",
    }
    for index, row in enumerate(
        payload["pricing"]["selected_targets"]["shopee:PH"]["sku_prices"]
    ):
        row["global_original_price_cny"] = str(40 + index)

    with pytest.raises(ApprovedPublicationSnapshotError, match="variant image"):
        build_approved_publication_snapshot_inputs(
            dashboard=dashboard,
            release_plan_payload=payload,
        )
