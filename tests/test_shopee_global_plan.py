from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
import json

import pytest

from shared_platform.shopee_global_plan import (
    APPROVED_PLAN_RECORD_SCHEMA_VERSION,
    BLOCKED_CAPABILITY,
    COMMUNITY_AUTHORITY,
    EXISTING_GLOBAL,
    GENERATED_SDK_AUTHORITY,
    INJECTED_UNVERIFIED_AUTHORITY,
    NEW_GLOBAL,
    OFFICIAL_AUTHORITY,
    OFFICIAL_OBSERVATION_SCHEMA_VERSION,
    READY,
    ShopeeGlobalPlanApprovalError,
    ShopeeGlobalPlanContractError,
    ShopeeGlobalPlanDriftError,
    approve_shopee_global_plan,
    build_shopee_global_plan_candidate,
    rehydrate_approved_shopee_global_plan,
    serialize_approved_shopee_global_plan,
    validate_approved_shopee_global_plan,
)
from shared_platform.target_scoped_release_contracts import (
    approved_shopee_copy_digest,
    approved_source_image_manifest_digest,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _base_args() -> dict:
    title = "Café Floral PVC Wall Sticker"
    description = "Exact approved English description.\nKeep this spacing."
    images = [
        {
            "source_url": f"https://approved.example/{position}.png?version=1",
            "source_image_digest": _digest(f"image-{position}"),
        }
        for position in range(1, 4)
    ]
    return {
        "mode": NEW_GLOBAL,
        "observation_authority": OFFICIAL_AUTHORITY,
        "observation_schema_version": OFFICIAL_OBSERVATION_SCHEMA_VERSION,
        "observation_evidence_digest": _digest("official-observation"),
        "source_identity_schema_version": "source-product-identity/v1",
        "source_identity_digest": _digest("source-identity"),
        "sku_lineage_schema_version": "new-source-sku-reservation/v1",
        "sku_lineage_digest": _digest("sku-lineage"),
        "content_package_digest": _digest("content-package"),
        "title": title,
        "description": description,
        "approved_copy_digest": approved_shopee_copy_digest(
            title, description
        ),
        "ordered_approved_images": images,
        "approved_source_image_manifest_digest": (
            approved_source_image_manifest_digest(
                [row["source_url"] for row in images]
            )
        ),
        "selected_image_positions": [1, 2, 3],
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
        "category": {
            "category_id": 101157,
            "path": [
                {"category_id": 100000, "name": "Home & Living"},
                {"category_id": 101157, "name": "Wall Stickers"},
            ],
            "path_complete": True,
            "evidence_digest": _digest("category-evidence"),
        },
        "attributes": [
            {
                "attribute_id": 1001,
                "attribute_value_list": [
                    {
                        "value_id": 0,
                        "original_value_name": "PVC",
                        "value_unit": "material",
                    }
                ],
            },
            {
                "attribute_id": 1002,
                "attribute_value_list": [
                    {"value_id": 55, "original_value_name": "Floral"}
                ],
            },
        ],
        "attributes_complete": True,
        "attribute_tree_digest": _digest("attribute-tree"),
        "brand": {
            "brand_id": 0,
            "original_brand_name": "No Brand",
            "evidence_digest": _digest("brand-evidence"),
        },
        "seller_stock": {
            "source": "kyle-explicit-seller-stock/v1",
            "source_digest": _digest("stock-source"),
            "quantity": 200,
            "approval_reference": "Kyle/global-plan/0956",
        },
        "location": {
            "location_id": "CN-WAREHOUSE-APPROVED",
            "evidence_digest": _digest("location-evidence"),
        },
        "condition": "NEW",
        "preorder": {"is_pre_order": False, "days_to_ship": 0},
        "variations": [
            {
                "name": "Size",
                "option_list": [
                    {"option": "38 x 45 cm", "approved_image_position": 1},
                    {"option": "50 x 70 cm", "approved_image_position": 2},
                ],
            }
        ],
        "variations_complete": True,
        "models": [
            {
                "global_model_sku": "0956",
                "tier_index": [0],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 200,
            },
            {
                "global_model_sku": "0957",
                "tier_index": [1],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 200,
            },
        ],
        "existing_global_item_id": None,
        "existing_global_identity_evidence_digest": None,
    }


def _candidate(**changes):
    args = deepcopy(_base_args())
    args.update(changes)
    if (
        ("title" in changes or "description" in changes)
        and "approved_copy_digest" not in changes
    ):
        if (
            type(args["title"]) is str
            and type(args["description"]) is str
            and args["title"].strip()
            and args["description"].strip()
        ):
            args["approved_copy_digest"] = approved_shopee_copy_digest(
                args["title"], args["description"]
            )
    if (
        "ordered_approved_images" in changes
        and "approved_source_image_manifest_digest" not in changes
    ):
        if args["ordered_approved_images"]:
            args["approved_source_image_manifest_digest"] = (
                approved_source_image_manifest_digest(
                    [
                        row["source_url"]
                        for row in args["ordered_approved_images"]
                    ]
                )
            )
    return build_shopee_global_plan_candidate(**args)


def _approve(candidate=None):
    candidate = candidate or _candidate()
    return approve_shopee_global_plan(
        candidate,
        approved_by="Kyle",
        confirm_approved_shopee_global_plan=True,
        expected_candidate_digest=candidate.candidate_digest,
    )


def test_official_complete_candidate_is_ready_and_deterministic():
    first = _candidate()
    second = _candidate()

    assert first.status == READY
    assert first.planning_allowed is True
    assert first.mode == NEW_GLOBAL
    assert first.candidate_digest == second.candidate_digest
    public = first.public_projection()
    assert public["counts"] == {
        "category_path_depth": 2,
        "attribute_count": 2,
        "approved_image_count": 3,
        "selected_image_count": 3,
        "variation_tier_count": 1,
        "model_count": 2,
    }
    assert all(public["checks"].values())


def test_public_candidate_and_approval_are_redacted_but_server_payload_is_exact():
    candidate = _candidate()
    approved = _approve(candidate)
    public_text = json.dumps(
        {
            "candidate": candidate.public_projection(),
            "approved": approved.public_projection(),
            "candidate_repr": repr(candidate),
            "approved_repr": repr(approved),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    for secret in (
        "Café Floral PVC Wall Sticker",
        "Exact approved English description",
        "https://approved.example/1.png",
        "101157",
        "CN-WAREHOUSE-APPROVED",
        "0956",
        "0957",
        "No Brand",
    ):
        assert secret not in public_text

    internal = approved.server_owned_execution_payload(candidate)
    assert internal["plan"]["copy"]["title"] == "Café Floral PVC Wall Sticker"
    assert internal["plan"]["copy"]["description"].endswith(
        "Keep this spacing."
    )
    assert internal["plan"]["selected_image_positions"] == [1, 2, 3]
    assert internal["plan"]["selected_image_urls"][0].startswith(
        "https://approved.example/1.png"
    )
    assert [row["global_model_sku"] for row in internal["plan"]["global_model"]] == [
        "0956",
        "0957",
    ]


@pytest.mark.parametrize(
    "authority",
    [
        GENERATED_SDK_AUTHORITY,
        COMMUNITY_AUTHORITY,
        INJECTED_UNVERIFIED_AUTHORITY,
        "some_other_source",
        None,
    ],
)
def test_nonofficial_authorities_are_blocked_even_with_complete_shapes(authority):
    candidate = _candidate(observation_authority=authority)

    assert candidate.status == BLOCKED_CAPABILITY
    assert candidate.planning_allowed is False
    assert candidate.public_projection()["checks"]["official_authority_exact"] is False
    with pytest.raises(ShopeeGlobalPlanApprovalError):
        _approve(candidate)


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        (
            "observation_schema_version",
            "generated-sdk-observation/v1",
            "audited_schema_unavailable",
        ),
        ("observation_schema_version", None, "audited_schema_unavailable"),
        ("observation_evidence_digest", "bad", "audited_evidence_unavailable"),
        ("observation_evidence_digest", None, "audited_evidence_unavailable"),
    ],
)
def test_audited_schema_and_evidence_are_mandatory(field, value, expected_code):
    candidate = _candidate(**{field: value})

    assert candidate.status == BLOCKED_CAPABILITY
    assert candidate.blocker_codes == (expected_code,)
    if field == "observation_schema_version":
        assert candidate.public_projection()["observation_schema_version"] == (
            "unavailable"
        )


def test_malformed_mode_is_blocked_without_raising_or_echoing_raw_input():
    candidate = _candidate(mode=["NEW_GLOBAL", "malformed"])

    assert candidate.status == BLOCKED_CAPABILITY
    assert candidate.mode is None
    assert candidate.blocker_codes == ("mode_invalid",)


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("mode", None, "mode_invalid"),
        ("source_identity_schema_version", None, "source_identity_invalid"),
        ("source_identity_digest", "bad", "source_identity_invalid"),
        ("sku_lineage_schema_version", "legacy/v0", "sku_lineage_invalid"),
        ("sku_lineage_digest", True, "sku_lineage_invalid"),
        ("content_package_digest", None, "content_binding_invalid"),
        ("title", 123, "approved_copy_invalid"),
        ("description", "", "approved_copy_invalid"),
        ("parcel", None, "parcel_invalid"),
        ("target_pricing", None, "target_pricing_invalid"),
        ("policy_digest", None, "policy_digest_invalid"),
        ("category", None, "category_invalid"),
        ("attributes", None, "attributes_invalid"),
        ("attributes_complete", False, "attributes_incomplete"),
        ("attribute_tree_digest", None, "attribute_tree_invalid"),
        ("brand", None, "brand_invalid"),
        ("seller_stock", None, "seller_stock_invalid"),
        ("location", None, "location_invalid"),
        ("condition", None, "condition_invalid"),
        ("preorder", None, "preorder_invalid"),
        ("variations", None, "variations_invalid"),
        ("variations_complete", False, "variations_incomplete"),
        ("models", None, "models_invalid"),
    ],
)
def test_every_required_fact_fails_closed_instead_of_defaulting(
    field, value, expected_code
):
    candidate = _candidate(**{field: value})

    assert candidate.status == BLOCKED_CAPABILITY
    assert candidate.blocker_codes == (expected_code,)


def test_copy_digest_is_recomputed_with_nfc_title_and_exact_description():
    title = "  Cafe\u0301 Floral PVC Wall Sticker  "
    description = "Line 1\n  Line 2  "
    candidate = _candidate(title=title, description=description)
    approved = _approve(candidate)
    internal = approved.server_owned_execution_payload(candidate)["plan"]

    assert internal["copy"]["title"] == "Café Floral PVC Wall Sticker"
    assert internal["copy"]["description"] == description

    mismatch = _candidate(
        title=title,
        description=description,
        approved_copy_digest=_digest("wrong-copy"),
    )
    assert mismatch.blocker_codes == ("approved_copy_digest_mismatch",)


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("x" * 121, "valid"),
        ("valid", "x" * 3001),
    ],
)
def test_copy_must_fit_the_global_api_without_silent_truncation(
    title, description
):
    assert _candidate(title=title, description=description).blocker_codes == (
        "approved_copy_invalid",
    )


def test_more_than_nine_approved_images_requires_an_explicit_bounded_selection():
    images = [
        {
            "source_url": f"https://approved.example/{index}.png",
            "source_image_digest": _digest(f"large-image-{index}"),
        }
        for index in range(1, 12)
    ]
    ready = _candidate(
        ordered_approved_images=images,
        selected_image_positions=[1, 3, 5, 7, 9, 10, 11],
        variations=[
            {
                "name": "Size",
                "option_list": [
                    {"option": "38 x 45 cm", "approved_image_position": 1},
                    {"option": "50 x 70 cm", "approved_image_position": 3},
                ],
            }
        ],
    )
    assert ready.status == READY
    internal = _approve(ready).server_owned_execution_payload(ready)["plan"]
    assert len(internal["approved_images"]) == 11
    assert internal["selected_image_positions"] == [1, 3, 5, 7, 9, 10, 11]

    blocked = _candidate(
        ordered_approved_images=images,
        selected_image_positions=list(range(1, 11)),
    )
    assert blocked.blocker_codes == ("selected_images_invalid",)


@pytest.mark.parametrize(
    "images",
    [
        [],
        [
            {
                "source_url": "http://approved.example/1.png",
                "source_image_digest": _digest("a"),
            }
        ],
        [
            {
                "source_url": "https://approved.example/1.png",
                "source_image_digest": "bad",
            }
        ],
        [
            {
                "source_url": "https://approved.example/1.png",
                "source_image_digest": _digest("same"),
            },
            {
                "source_url": "https://approved.example/1.png",
                "source_image_digest": _digest("other"),
            },
        ],
    ],
)
def test_approved_image_shape_is_strict(images):
    candidate = _candidate(
        ordered_approved_images=images,
        selected_image_positions=[1],
    )
    assert candidate.status == BLOCKED_CAPABILITY
    assert candidate.blocker_codes == ("approved_images_invalid",)


@pytest.mark.parametrize(
    "positions",
    [[], [0], [4], [2, 1], [1, 1], [True]],
)
def test_selected_image_positions_are_explicit_ordered_and_exact(positions):
    candidate = _candidate(selected_image_positions=positions)
    assert candidate.blocker_codes == ("selected_images_invalid",)


def test_image_manifest_digest_mismatch_is_blocked():
    candidate = _candidate(
        approved_source_image_manifest_digest=_digest("wrong-manifest")
    )
    assert candidate.blocker_codes == (
        "approved_image_manifest_digest_mismatch",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weight_kg", True),
        ("weight_kg", 0.2),
        ("weight_kg", "NaN"),
        ("length_cm", "0"),
        ("width_cm", "-1"),
        ("height_cm", "Infinity"),
        ("contract_digest", "bad"),
    ],
)
def test_parcel_values_are_positive_exact_and_digest_bound(field, value):
    parcel = deepcopy(_base_args()["parcel"])
    parcel[field] = value
    candidate = _candidate(parcel=parcel)
    assert candidate.blocker_codes == ("parcel_invalid",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", "USD"),
        ("global_original_price", True),
        ("global_original_price", 56.05),
        ("global_original_price", "NaN"),
        ("global_original_price", "0"),
        ("contract_digest", "bad"),
    ],
)
def test_global_original_price_is_explicit_positive_cny(field, value):
    pricing = deepcopy(_base_args()["target_pricing"])
    pricing[field] = value
    candidate = _candidate(target_pricing=pricing)
    assert candidate.blocker_codes == ("target_pricing_invalid",)


@pytest.mark.parametrize(
    "category",
    [
        {},
        {
            "category_id": True,
            "path": [{"category_id": 1, "name": "x"}],
            "evidence_digest": _digest("x"),
        },
        {
            "category_id": 2,
            "path": [{"category_id": 1, "name": "x"}],
            "evidence_digest": _digest("x"),
        },
        {
            "category_id": 2,
            "path": [
                {"category_id": 2, "name": "x"},
                {"category_id": 2, "name": "duplicate"},
            ],
            "evidence_digest": _digest("x"),
        },
    ],
)
def test_category_path_is_complete_and_ends_at_the_selected_category(category):
    candidate = _candidate(category=category)
    assert candidate.blocker_codes == ("category_invalid",)


def test_category_path_requires_explicit_official_completeness():
    category = deepcopy(_base_args()["category"])
    category["path_complete"] = False

    assert _candidate(category=category).blocker_codes == (
        "category_path_incomplete",
    )


@pytest.mark.parametrize(
    "attributes",
    [
        [],
        [None],
        [{"attribute_id": 1, "attribute_value_list": []}],
        [
            {
                "attribute_id": True,
                "attribute_value_list": [
                    {"value_id": 1, "original_value_name": "x"}
                ],
            }
        ],
        [
            {
                "attribute_id": 1,
                "attribute_value_list": [
                    {"value_id": -1, "original_value_name": "x"}
                ],
            }
        ],
        [
            {
                "attribute_id": 1,
                "attribute_value_list": [
                    {"value_id": 0, "original_value_name": ""}
                ],
            }
        ],
    ],
)
def test_attribute_selection_is_complete_and_strict(attributes):
    candidate = _candidate(attributes=attributes)
    assert candidate.blocker_codes == ("attributes_invalid",)


@pytest.mark.parametrize(
    "brand",
    [
        None,
        {
            "brand_id": -1,
            "original_brand_name": "No Brand",
            "evidence_digest": _digest("brand"),
        },
        {
            "brand_id": 0,
            "original_brand_name": "",
            "evidence_digest": _digest("brand"),
        },
        {
            "brand_id": 0,
            "original_brand_name": "No Brand",
            "evidence_digest": "bad",
        },
    ],
)
def test_brand_is_never_defaulted(brand):
    assert _candidate(brand=brand).blocker_codes == ("brand_invalid",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "default-200"),
        ("source_digest", "bad"),
        ("quantity", True),
        ("quantity", 0),
        ("quantity", 200.0),
        ("approval_reference", ""),
    ],
)
def test_seller_stock_needs_an_approved_source_and_reference(field, value):
    stock = deepcopy(_base_args()["seller_stock"])
    stock[field] = value
    assert _candidate(seller_stock=stock).blocker_codes == (
        "seller_stock_invalid",
    )


@pytest.mark.parametrize(
    "location",
    [
        None,
        {"location_id": "", "evidence_digest": _digest("location")},
        {"location_id": "CNZ", "evidence_digest": "bad"},
        {"location_id": 123, "evidence_digest": _digest("location")},
    ],
)
def test_location_requires_an_explicit_observed_identity(location):
    assert _candidate(location=location).blocker_codes == ("location_invalid",)


@pytest.mark.parametrize(
    ("condition", "preorder"),
    [
        ("new", {"is_pre_order": False, "days_to_ship": 0}),
        ("NEW", {"is_pre_order": 0, "days_to_ship": 0}),
        ("NEW", {"is_pre_order": False, "days_to_ship": 2}),
        ("NEW", {"is_pre_order": True, "days_to_ship": 0}),
        ("NEW", {"is_pre_order": True, "days_to_ship": True}),
    ],
)
def test_condition_and_preorder_are_explicit_not_defaults(condition, preorder):
    candidate = _candidate(condition=condition, preorder=preorder)
    assert candidate.status == BLOCKED_CAPABILITY
    assert candidate.blocker_codes[0] in {"condition_invalid", "preorder_invalid"}


def test_model_matrix_must_cover_every_variation_combination():
    variations = [
        {
            "name": "Size",
            "option_list": [{"option": "Small"}, {"option": "Large"}],
        },
        {
            "name": "Color",
            "option_list": [{"option": "Blue"}, {"option": "Green"}],
        },
    ]
    models = [
        {
            "global_model_sku": f"SKU-{size}-{color}",
            "tier_index": [size, color],
            "original_price_cny": "56.05",
            "seller_stock_quantity": 200,
        }
        for size in range(2)
        for color in range(2)
    ]
    complete = _candidate(variations=variations, models=models)
    assert complete.status == READY

    collapsed = _candidate(variations=variations, models=models[:1])
    assert collapsed.blocker_codes == ("models_incomplete",)


@pytest.mark.parametrize(
    "models",
    [
        [],
        [
            {
                "global_model_sku": "0956",
                "tier_index": [0],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 200,
            }
        ],
        [
            {
                "global_model_sku": "0956",
                "tier_index": [0],
                "original_price_cny": "50",
                "seller_stock_quantity": 200,
            },
            {
                "global_model_sku": "0957",
                "tier_index": [1],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 200,
            },
        ],
        [
            {
                "global_model_sku": "0956",
                "tier_index": [0],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 100,
            },
            {
                "global_model_sku": "0957",
                "tier_index": [1],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 200,
            },
        ],
    ],
)
def test_models_cannot_be_missing_collapsed_or_detached_from_price_stock(models):
    candidate = _candidate(models=models)
    assert candidate.status == BLOCKED_CAPABILITY
    assert candidate.blocker_codes[0] in {"models_invalid", "models_incomplete"}


@pytest.mark.parametrize("sku", ["with space", "商品0956", "x" * 65])
def test_model_skus_are_explicit_bounded_api_safe_identifiers(sku):
    models = deepcopy(_base_args()["models"])
    models[0]["global_model_sku"] = sku
    assert _candidate(models=models).blocker_codes == ("models_invalid",)


def test_existing_mode_requires_exact_internal_global_identity_evidence():
    missing = _candidate(mode=EXISTING_GLOBAL)
    assert missing.blocker_codes == ("existing_global_identity_invalid",)

    ready = _candidate(
        mode=EXISTING_GLOBAL,
        existing_global_item_id=57115039489,
        existing_global_identity_evidence_digest=_digest("existing-id"),
    )
    assert ready.status == READY
    public = json.dumps(ready.public_projection(), sort_keys=True)
    assert "57115039489" not in public
    internal = _approve(ready).server_owned_execution_payload(ready)["plan"]
    assert internal["existing_global_item_id"] == 57115039489


def test_new_mode_rejects_any_existing_identity_instead_of_ignoring_it():
    candidate = _candidate(
        existing_global_item_id=57115039489,
        existing_global_identity_evidence_digest=_digest("existing-id"),
    )
    assert candidate.blocker_codes == (
        "new_global_existing_identity_forbidden",
    )


@pytest.mark.parametrize(
    ("actor", "consent"),
    [
        ("kyle", True),
        ("Kyle ", True),
        ("Kyle", False),
        ("Kyle", 1),
        (None, True),
    ],
)
def test_approval_requires_exact_kyle_actor_and_literal_consent(actor, consent):
    candidate = _candidate()
    with pytest.raises(ShopeeGlobalPlanApprovalError):
        approve_shopee_global_plan(
            candidate,
            approved_by=actor,
            confirm_approved_shopee_global_plan=consent,
            expected_candidate_digest=candidate.candidate_digest,
        )


def test_approval_requires_exact_current_candidate_digest():
    candidate = _candidate()
    with pytest.raises(ShopeeGlobalPlanApprovalError):
        approve_shopee_global_plan(
            candidate,
            approved_by="Kyle",
            confirm_approved_shopee_global_plan=True,
            expected_candidate_digest=_digest("stale-candidate"),
        )


def _drift_candidates():
    base = _base_args()
    changed_images = deepcopy(base["ordered_approved_images"])
    changed_images[0] = {
        "source_url": "https://approved.example/replacement.png",
        "source_image_digest": _digest("replacement-image"),
    }
    changed_parcel = deepcopy(base["parcel"])
    changed_parcel["contract_digest"] = _digest("parcel-v2")
    changed_pricing = deepcopy(base["target_pricing"])
    changed_pricing["contract_digest"] = _digest("pricing-v2")
    changed_category = deepcopy(base["category"])
    changed_category["evidence_digest"] = _digest("category-v2")
    changed_attributes = deepcopy(base["attributes"])
    changed_attributes[0]["attribute_value_list"][0][
        "original_value_name"
    ] = "Vinyl"
    changed_brand = deepcopy(base["brand"])
    changed_brand["evidence_digest"] = _digest("brand-v2")
    changed_stock = deepcopy(base["seller_stock"])
    changed_stock["source_digest"] = _digest("stock-v2")
    changed_location = deepcopy(base["location"])
    changed_location["evidence_digest"] = _digest("location-v2")
    changed_models = deepcopy(base["models"])
    changed_models[0]["global_model_sku"] = "0956-NEW"
    return [
        {"source_identity_digest": _digest("source-v2")},
        {"sku_lineage_digest": _digest("lineage-v2")},
        {"content_package_digest": _digest("content-v2")},
        {"title": "Changed approved title"},
        {"ordered_approved_images": changed_images},
        {"parcel": changed_parcel},
        {"target_pricing": changed_pricing},
        {"policy_digest": _digest("policy-v2")},
        {"observation_evidence_digest": _digest("observation-v2")},
        {"category": changed_category},
        {"attributes": changed_attributes},
        {"attribute_tree_digest": _digest("tree-v2")},
        {"brand": changed_brand},
        {"seller_stock": changed_stock},
        {"location": changed_location},
        {"condition": "USED"},
        {"preorder": {"is_pre_order": True, "days_to_ship": 3}},
        {"models": changed_models},
    ]


@pytest.mark.parametrize("change", _drift_candidates())
def test_any_candidate_lineage_policy_or_execution_fact_drift_invalidates_approval(
    change,
):
    original = _candidate()
    approved = _approve(original)
    current = _candidate(**change)

    assert current.status == READY
    assert current.candidate_digest != original.candidate_digest
    with pytest.raises(ShopeeGlobalPlanDriftError):
        validate_approved_shopee_global_plan(approved, current)
    with pytest.raises(ShopeeGlobalPlanDriftError):
        approved.server_owned_execution_payload(current)


def test_candidate_and_approval_digest_forgery_are_rejected():
    candidate = _candidate()
    approved = _approve(candidate)

    with pytest.raises(ShopeeGlobalPlanContractError):
        replace(candidate, candidate_digest=_digest("forged-candidate"))
    with pytest.raises(ShopeeGlobalPlanContractError):
        replace(candidate, planning_allowed=False)
    with pytest.raises(ShopeeGlobalPlanContractError):
        replace(
            approved,
            approved_plan_digest=_digest("forged-approved-plan"),
        )
    with pytest.raises(ShopeeGlobalPlanContractError):
        replace(approved, approved_by="Someone")


def test_decimal_canonicalization_is_stable_and_does_not_accept_float_shortcuts():
    candidate = _candidate(
        parcel={
            **_base_args()["parcel"],
            "weight_kg": Decimal("0.2000"),
        },
        target_pricing={
            **_base_args()["target_pricing"],
            "global_original_price": Decimal("56.0500"),
        },
    )
    internal = _approve(candidate).server_owned_execution_payload(candidate)[
        "plan"
    ]

    assert internal["parcel"]["weight_kg"] == "0.2"
    assert internal["pricing"]["global_original_price"] == "56.05"
    assert _candidate(
        target_pricing={
            **_base_args()["target_pricing"],
            "global_original_price": 56.05,
        }
    ).status == BLOCKED_CAPABILITY


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _serialized_record(candidate=None) -> str:
    return serialize_approved_shopee_global_plan(_approve(candidate))


def _tamper_serialized(mutator) -> str:
    record = json.loads(_serialized_record())
    mutator(record)
    return _canonical_json(record)


def test_canonical_server_internal_record_roundtrips_deterministically():
    candidate = _candidate()
    approved = _approve(candidate)
    serialized = serialize_approved_shopee_global_plan(approved)
    restored = rehydrate_approved_shopee_global_plan(serialized)

    assert serialized == _canonical_json(json.loads(serialized))
    assert (
        json.loads(serialized)["record_schema_version"]
        == APPROVED_PLAN_RECORD_SCHEMA_VERSION
    )
    assert restored.candidate_digest == approved.candidate_digest
    assert restored.approved_plan_digest == approved.approved_plan_digest
    assert restored.public_projection() == approved.public_projection()
    assert serialize_approved_shopee_global_plan(restored) == serialized
    assert (
        restored.server_owned_execution_payload(candidate)
        == approved.server_owned_execution_payload(candidate)
    )


def test_existing_global_record_roundtrips_without_public_identity_leakage():
    candidate = _candidate(
        mode=EXISTING_GLOBAL,
        existing_global_item_id=57115039489,
        existing_global_identity_evidence_digest=_digest("existing-id"),
    )
    restored = rehydrate_approved_shopee_global_plan(
        _serialized_record(candidate)
    )

    public = json.dumps(restored.public_projection(), sort_keys=True)
    assert "57115039489" not in public
    assert (
        restored.server_owned_execution_payload(candidate)["plan"][
            "existing_global_item_id"
        ]
        == 57115039489
    )


def test_rehydrated_approval_still_requires_the_current_official_candidate():
    original = _candidate()
    restored = rehydrate_approved_shopee_global_plan(
        _serialized_record(original)
    )
    drifted = _candidate(policy_digest=_digest("policy-after-restart"))
    blocked = _candidate(observation_authority=GENERATED_SDK_AUTHORITY)

    with pytest.raises(ShopeeGlobalPlanDriftError):
        restored.server_owned_execution_payload(drifted)
    with pytest.raises(ShopeeGlobalPlanDriftError):
        restored.server_owned_execution_payload(blocked)
    assert restored.server_owned_execution_payload(original)["plan"][
        "policy_digest"
    ] == _base_args()["policy_digest"]


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("approved_plan",),
        ("approved_plan", "plan"),
        ("approved_plan", "plan", "bindings"),
        ("approved_plan", "plan", "copy"),
        ("approved_plan", "plan", "approved_images", 0),
        ("approved_plan", "plan", "parcel"),
        ("approved_plan", "plan", "parcel", "package_cm"),
        ("approved_plan", "plan", "pricing"),
        ("approved_plan", "plan", "category"),
        ("approved_plan", "plan", "category", "path", 0),
        ("approved_plan", "plan", "attribute_list", 0),
        (
            "approved_plan",
            "plan",
            "attribute_list",
            0,
            "attribute_value_list",
            0,
        ),
        ("approved_plan", "plan", "brand"),
        ("approved_plan", "plan", "seller_stock"),
        ("approved_plan", "plan", "location"),
        ("approved_plan", "plan", "preorder"),
        ("approved_plan", "plan", "tier_variation", 0),
        (
            "approved_plan",
            "plan",
            "tier_variation",
            0,
            "option_list",
            0,
        ),
        ("approved_plan", "plan", "global_model", 0),
    ],
)
def test_rehydration_rejects_unknown_fields_at_every_persisted_layer(path):
    def add_extra(record):
        target = record
        for key in path:
            target = target[key]
        target["unexpected"] = "must fail closed"

    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(_tamper_serialized(add_extra))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("approved_plan", "confirm_approved_shopee_global_plan"), 1),
        (("approved_plan", "candidate_digest"), 123),
        (("approved_plan", "mode"), True),
        (("approved_plan", "plan", "attributes_complete"), 1),
        (("approved_plan", "plan", "variations_complete"), 1),
        (
            (
                "approved_plan",
                "plan",
                "category",
                "path_complete",
            ),
            1,
        ),
        (
            (
                "approved_plan",
                "plan",
                "selected_image_positions",
                0,
            ),
            True,
        ),
        (
            (
                "approved_plan",
                "plan",
                "pricing",
                "global_original_price",
            ),
            56.05,
        ),
        (
            (
                "approved_plan",
                "plan",
                "seller_stock",
                "quantity",
            ),
            200.0,
        ),
        (
            (
                "approved_plan",
                "plan",
                "global_model",
                0,
                "seller_stock_quantity",
            ),
            True,
        ),
    ],
)
def test_rehydration_rejects_json_type_drift(path, value):
    def change_type(record):
        target = record
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(_tamper_serialized(change_type))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda record: record["approved_plan"].__setitem__(
            "candidate_digest", _digest("forged-candidate")
        ),
        lambda record: record["approved_plan"].__setitem__(
            "approved_plan_digest", _digest("forged-plan")
        ),
        lambda record: record["approved_plan"]["plan"]["copy"].__setitem__(
            "title", "Tampered title"
        ),
        lambda record: record["approved_plan"]["plan"][
            "approved_images"
        ][0].__setitem__(
            "source_url", "https://approved.example/tampered.png"
        ),
        lambda record: record["approved_plan"]["plan"].__setitem__(
            "selected_image_urls",
            ["https://approved.example/tampered.png"],
        ),
        lambda record: record["approved_plan"]["plan"]["bindings"].__setitem__(
            "policy_digest", _digest("binding-only-policy")
        ),
        lambda record: record["approved_plan"]["plan"].__setitem__(
            "selected_source_image_manifest_digest",
            _digest("selected-manifest-tamper"),
        ),
        lambda record: record["approved_plan"]["plan"]["category"].__setitem__(
            "evidence_digest", _digest("category-tamper")
        ),
        lambda record: record["approved_plan"]["plan"]["global_model"][
            0
        ].__setitem__("global_model_sku", "TAMPERED"),
    ],
)
def test_rehydration_recomputes_raw_shapes_and_all_identity_digests(mutator):
    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(_tamper_serialized(mutator))


@pytest.mark.parametrize("value", [None, {}, b"{}", 1, True])
def test_rehydration_accepts_only_a_canonical_json_string(value):
    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(value)


def test_rehydration_rejects_noncanonical_json_duplicate_keys_and_constants():
    canonical = _serialized_record()
    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(
            json.dumps(json.loads(canonical), ensure_ascii=False, indent=2)
        )
    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(
            '{"record_schema_version":"a","record_schema_version":"b",'
            '"approved_plan":{}}'
        )
    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(
            canonical.replace('"mode":"NEW_GLOBAL"', '"mode":NaN')
        )


def test_record_schema_and_approval_identity_are_strict():
    wrong_schema = _tamper_serialized(
        lambda record: record.__setitem__("record_schema_version", "legacy/v0")
    )
    wrong_actor = _tamper_serialized(
        lambda record: record["approved_plan"].__setitem__(
            "approved_by", "kyle"
        )
    )
    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(wrong_schema)
    with pytest.raises(ShopeeGlobalPlanContractError):
        rehydrate_approved_shopee_global_plan(wrong_actor)


def test_serializer_revalidates_even_a_low_level_mutated_frozen_object():
    approved = _approve()
    object.__setattr__(
        approved, "candidate_digest", _digest("low-level-memory-tamper")
    )

    with pytest.raises(ShopeeGlobalPlanContractError):
        serialize_approved_shopee_global_plan(approved)
