from __future__ import annotations

import copy
import hashlib
import json

import pytest

from domains.channel_operations import oneclick_release_adapters as adapters
from modules.shopee import global_plan_candidate as global_candidate
from modules.shopee import oneclick_release as shopee
from shared_platform.shopee_global_plan import (
    EXISTING_GLOBAL,
    NEW_GLOBAL,
    OFFICIAL_AUTHORITY,
    OFFICIAL_OBSERVATION_SCHEMA_VERSION,
    READY,
    ShopeeGlobalPlanObservationError,
    build_shopee_global_plan_candidate,
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


def _request(*, category_decision_execution=None) -> dict[str, object]:
    source_digest = _digest("source")
    lineage_digest = _digest("lineage")
    title = "Approved English title"
    description = "Exact approved English description."
    images = [
        {
            "source_url": "https://assets.example/one.jpg",
            "source_image_digest": _digest("image-1"),
        }
    ]
    request = {
        "schema_version": "shopee-global-plan-observer-request/v1",
        "offer_id": "3838616043",
        "product_revision": 31,
        "targets": ["shopee:MY"],
        "source_identity": {
            "schema_version": "source-product-identity/v1",
            "identity_digest": source_digest,
        },
        "sku_lineage": {
            "assignment": {
                "seller_sku": "0954",
                "model_skus": [
                    {"variant_key": "default", "model_sku": "0954"}
                ],
            },
            "reservation": {
                "schema_version": "new-source-sku-reservation/v1",
                "reservation_digest": lineage_digest,
            },
        },
        "candidate_seed": {
            "source_identity_schema_version": (
                "source-product-identity/v1"
            ),
            "source_identity_digest": source_digest,
            "sku_lineage_schema_version": (
                "new-source-sku-reservation/v1"
            ),
            "sku_lineage_digest": lineage_digest,
            "content_package_digest": _digest("content"),
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
        },
    }
    if category_decision_execution is not None:
        request["candidate_seed"]["category_decision_execution"] = (
            category_decision_execution
        )
    return request


class _OfficialScan:
    def __init__(self, *, existing: bool):
        self.existing = existing
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.attribute_value_name = "PVC"
        self.attribute_value_id = 7
        self.attribute_rows: list[dict[str, object]] | None = None
        self.selected_attributes: list[dict[str, object]] | None = None

    def merchant_get(self, path, params):
        self.calls.append((path, dict(params)))
        if path == shopee.GLOBAL_LIST_PATH:
            rows = (
                [{"global_item_id": 57115039489}]
                if self.existing and params["item_status"] == "NORMAL"
                else []
            )
            return {
                "error": "",
                "response": {
                    "global_item_list": rows,
                    "total_count": len(rows),
                    "has_next_page": False,
                },
            }
        if path == shopee.GLOBAL_MODEL_PATH:
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
        if path == (
            "/api/v2/global_product/category_recommend"
        ):
            return {
                "error": "",
                "response": {"category_id_list": [101157]},
            }
        if path == "/api/v2/global_product/get_category":
            return {
                "error": "",
                "response": {
                    "category_list": [
                        {
                            "category_id": 100000,
                            "parent_category_id": 0,
                            "original_category_name": "Home",
                            "has_children": True,
                        },
                        {
                            "category_id": 101157,
                            "parent_category_id": 100000,
                            "original_category_name": "Wall Stickers",
                            "has_children": False,
                        },
                    ]
                },
            }
        if path == "/api/v2/global_product/get_attribute_tree":
            attribute_rows = self.attribute_rows
            if attribute_rows is None:
                attribute_rows = [
                    {
                        "attribute_id": 1001,
                        "original_attribute_name": "Material",
                        "is_mandatory": True,
                        "input_type": "SINGLE_SELECT",
                        "attribute_value_list": [
                            {
                                "value_id": self.attribute_value_id,
                                "original_value_name": (
                                    self.attribute_value_name
                                ),
                            }
                        ],
                    }
                ]
            return {
                "error": "",
                "response": {
                    "attribute_list": attribute_rows
                },
            }
        if path == "/api/v2/global_product/get_brand_list":
            return {
                "error": "",
                "response": {
                    "brand_list": [
                        {
                            "brand_id": 8,
                            "original_brand_name": "Official Brand",
                        }
                    ],
                    "total_count": 1,
                    "has_next_page": False,
                },
            }
        if path == (
            "/api/v2/merchant/get_merchant_warehouse_location_list"
        ):
            return {
                "error": "",
                "response": [
                    {"location_id": "CNZ", "warehouse_name": "Primary"}
                ],
            }
        if path == shopee.GLOBAL_ITEM_PATH and self.existing:
            return {
                "error": "",
                "response": {
                    "global_item_list": [
                        {
                            "global_item_id": 57115039489,
                            "global_item_name": "Approved English title",
                            "description": (
                                "Exact approved English description."
                            ),
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
                            "seller_stock": [
                                {"location_id": "CNZ", "stock": 200}
                            ],
                            "condition": "NEW",
                            "pre_order": {
                                "is_pre_order": False,
                                "days_to_ship": 0,
                            },
                            "tier_variation": [
                                {
                                    "name": "Style",
                                    "option_list": [
                                        {"option": "Default"}
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }
        raise AssertionError((path, params))

    def transport(self):
        return shopee.ShopeePrepareTransport(
            credentials=shopee.ShopeeCredentials(
                region="MY",
                shop_id=123,
                shop_token="fixture-shop-token",
                merchant_id=456,
                merchant_token="fixture-merchant-token",
            ),
            merchant_get=self.merchant_get,
            shop_get=lambda *_args: pytest.fail("shop GET not expected"),
        )


class _PagedOfficialScan(_OfficialScan):
    def __init__(self, *, incomplete: bool = False):
        super().__init__(existing=True)
        self.incomplete = incomplete

    def merchant_get(self, path, params):
        self.calls.append((path, dict(params)))
        if path == shopee.GLOBAL_LIST_PATH:
            status = params["item_status"]
            offset = params["offset"]
            if status != "NORMAL":
                return {
                    "error": "",
                    "response": {
                        "global_item_list": [],
                        "total_count": 0,
                        "has_next_page": False,
                    },
                }
            if offset == 0:
                return {
                    "error": "",
                    "response": {
                        "global_item_list": [{"global_item_id": 111}],
                        "total_count": 2,
                        "has_next_page": not self.incomplete,
                        **(
                            {"next_offset": 100}
                            if not self.incomplete
                            else {}
                        ),
                    },
                }
            if offset == 100 and not self.incomplete:
                return {
                    "error": "",
                    "response": {
                        "global_item_list": [
                            {"global_item_id": 57115039489}
                        ],
                        "total_count": 2,
                        "has_next_page": False,
                    },
                }
            raise AssertionError(params)
        if path == shopee.GLOBAL_MODEL_PATH:
            sku = (
                "0954"
                if str(params["global_item_id"]) == "57115039489"
                else "OTHER"
            )
            return {
                "error": "",
                "response": {
                    "global_model": [
                        {
                            "global_model_id": 99,
                            "global_model_sku": sku,
                            "tier_index": [0],
                        }
                    ]
                },
            }
        raise AssertionError((path, params))


def _category_execution(fake: _OfficialScan) -> dict[str, object]:
    request = _request()
    seed = request["candidate_seed"]
    context = {
        "schema_version": "channel-category-observer-request/v2",
        "product_id": request["offer_id"],
        "product_revision": request["product_revision"],
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "source_identity_digest": seed["source_identity_digest"],
        "sku_lineage_digest": seed["sku_lineage_digest"],
        "approved_copy_digest": seed["approved_copy_digest"],
        "targets_digest": _digest(sorted(request["targets"])),
    }
    path = global_candidate._read_category_path(
        fake.transport(), 101157
    )
    tree = global_candidate._read_attribute_tree(
        fake.transport(), 101157
    )
    brand = global_candidate._brand_option_projection(
        global_candidate._read_all_brands(
            fake.transport(), 101157
        )
    )[0]
    location = global_candidate._location_option_projection(
        global_candidate._read_seller_locations(fake.transport())
    )[0]
    creation = global_candidate._creation_default_projection()
    context_digest = _digest(context)
    creation_identity_digest = _digest("creation-fact")
    location_identity_digest = _digest(
        {
            "schema_version": "channel-location-option-identity/v1",
            **location,
        }
    )
    return {
        "schema_version": "channel-category-decision-execution/v2",
        "decision_digest": _digest("decision"),
        "context_digest": context_digest,
        "options_digest": _digest("options"),
        "selected_category_identity_digest": _digest("selected"),
        "category": {
            "category_id": 101157,
            "path": [dict(row) for row in path],
            "path_complete": True,
            "evidence_digest": _digest(path),
        },
        "attribute_list": (
            copy.deepcopy(fake.selected_attributes)
            if fake.selected_attributes is not None
            else [
                {
                    "attribute_id": 1001,
                    "attribute_value_list": [
                        {
                            "value_id": fake.attribute_value_id,
                            "original_value_name": "PVC",
                        }
                    ],
                }
            ]
        ),
        "attributes_complete": True,
        "attribute_tree_digest": _digest(tree),
        "brand": {
            "brand_id": brand["brand_id"],
            "original_brand_name": brand["original_brand_name"],
            "evidence_digest": brand["evidence_digest"],
        },
        "seller_stock": {
            "source": "kyle-explicit-seller-stock/v1",
            "source_digest": (
                global_candidate.seller_stock_source_digest(
                    context_digest=context_digest,
                    creation_fact_identity_digest=(
                        creation_identity_digest
                    ),
                    location_identity_digest=(
                        location_identity_digest
                    ),
                    quantity=creation["seller_stock_quantity"],
                )
            ),
            "quantity": creation["seller_stock_quantity"],
            "approval_reference": creation_identity_digest,
        },
        "location": {
            "location_id": location["location_id"],
            "evidence_digest": location["evidence_digest"],
        },
        "condition": creation["condition"],
        "preorder": creation["preorder"],
        "tier_variation": [
            {
                "name": "Default",
                "option_list": [
                    {
                        "option": "Default",
                        "approved_image_position": 1,
                    }
                ],
            }
        ],
        "global_model": [
            {
                "global_model_sku": "0954",
                "tier_index": [0],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 200,
            }
        ],
    }


def _first_party_candidate(_request, seed, _transport):
    source = dict(seed)
    mode = source["mode"]
    context = source["official_identity_observation"]
    existing_id = (
        int(context["global_item_id"])
        if mode == EXISTING_GLOBAL
        else None
    )
    return build_shopee_global_plan_candidate(
        mode=mode,
        observation_authority=OFFICIAL_AUTHORITY,
        observation_schema_version=OFFICIAL_OBSERVATION_SCHEMA_VERSION,
        observation_evidence_digest=_digest(context),
        source_identity_schema_version=source[
            "source_identity_schema_version"
        ],
        source_identity_digest=source["source_identity_digest"],
        sku_lineage_schema_version=source[
            "sku_lineage_schema_version"
        ],
        sku_lineage_digest=source["sku_lineage_digest"],
        content_package_digest=source["content_package_digest"],
        title=source["title"],
        description=source["description"],
        approved_copy_digest=source["approved_copy_digest"],
        ordered_approved_images=source["ordered_approved_images"],
        approved_source_image_manifest_digest=source[
            "approved_source_image_manifest_digest"
        ],
        selected_image_positions=[1],
        parcel=source["parcel"],
        target_pricing=source["target_pricing"],
        policy_digest=source["policy_digest"],
        category={
            "category_id": 101157,
            "path": [
                {"category_id": 100000, "name": "Home"},
                {"category_id": 101157, "name": "Wall Stickers"},
            ],
            "path_complete": True,
            "evidence_digest": _digest("category"),
        },
        attributes=[
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
        attributes_complete=True,
        attribute_tree_digest=_digest("attributes"),
        brand={
            "brand_id": 0,
            "original_brand_name": "No Brand",
            "evidence_digest": _digest("brand"),
        },
        seller_stock={
            "source": "kyle-explicit-seller-stock/v1",
            "source_digest": _digest("stock"),
            "quantity": 200,
            "approval_reference": "Kyle/global-plan/0954",
        },
        location={
            "location_id": "CN-WAREHOUSE-APPROVED",
            "evidence_digest": _digest("location"),
        },
        condition="NEW",
        preorder={"is_pre_order": False, "days_to_ship": 0},
        variations=[
            {
                "name": "Style",
                "option_list": [
                    {
                        "option": "Default",
                        "approved_image_position": 1,
                    }
                ],
            }
        ],
        variations_complete=True,
        models=[
            {
                "global_model_sku": "0954",
                "tier_index": [0],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 200,
            }
        ],
        existing_global_item_id=existing_id,
        existing_global_identity_evidence_digest=(
            _digest(context) if existing_id is not None else None
        ),
    )


@pytest.fixture(autouse=True)
def _reset_factories():
    shopee.configure_prepare_transport_factory(None)
    shopee.configure_global_candidate_observer_factory(None)
    yield
    shopee.configure_prepare_transport_factory(None)
    shopee.configure_global_candidate_observer_factory(None)


@pytest.mark.parametrize(
    ("existing", "expected_mode"),
    [(False, NEW_GLOBAL), (True, EXISTING_GLOBAL)],
)
def test_dynamic_observer_returns_only_shared_ready_candidate(
    existing, expected_mode
):
    official = _OfficialScan(existing=existing)
    shopee.configure_prepare_transport_factory(
        lambda region: (
            official.transport()
            if region == "MY"
            else pytest.fail("wrong region")
        )
    )
    shopee.configure_global_candidate_observer_factory(
        _first_party_candidate
    )

    candidate = adapters.observe_shopee_global_plan_candidate(_request())

    assert candidate.status == READY
    assert candidate.mode == expected_mode
    assert {
        params["item_status"]
        for path, params in official.calls
        if path == shopee.GLOBAL_LIST_PATH
    } == {"NORMAL", "UNLIST", "BANNED"}
    assert "fixture-shop-token" not in repr(candidate)


def test_dynamic_observer_default_official_recommendation_is_not_approval():
    official = _OfficialScan(existing=False)
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )

    candidate = adapters.observe_shopee_global_plan_candidate(_request())

    assert candidate.status == "BLOCKED_CAPABILITY"
    assert candidate.mode == NEW_GLOBAL
    assert candidate.blocker_codes == ("category_invalid",)
    assert any(
        path == "/api/v2/global_product/category_recommend"
        for path, _params in official.calls
    )


def test_global_observer_consumes_and_revalidates_persisted_category_decision():
    official = _OfficialScan(existing=False)
    selection = _category_execution(official)
    official.calls.clear()
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )

    candidate = adapters.observe_shopee_global_plan_candidate(
        _request(category_decision_execution=selection)
    )

    assert candidate.status == READY
    assert candidate.mode == NEW_GLOBAL
    assert candidate.blocker_codes == ()
    assert any(
        path == "/api/v2/global_product/get_attribute_tree"
        for path, _params in official.calls
    )


def test_value_id_zero_roundtrips_to_ready_new_global_candidate():
    official = _OfficialScan(existing=False)
    official.attribute_value_id = 0
    selection = _category_execution(official)
    official.calls.clear()
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )

    candidate = adapters.observe_shopee_global_plan_candidate(
        _request(category_decision_execution=selection)
    )

    assert candidate.status == READY
    assert candidate._plan.payload()["attribute_list"][0][
        "attribute_value_list"
    ][0]["value_id"] == 0


def test_explicit_single_multi_text_decision_reaches_ready_candidate():
    official = _OfficialScan(existing=False)
    official.attribute_rows = [
        {
            "attribute_id": 1001,
            "original_attribute_name": "Material",
            "is_mandatory": True,
            "input_type": "SINGLE_SELECT",
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "PVC"}
            ],
        },
        {
            "attribute_id": 1002,
            "original_attribute_name": "Features",
            "is_mandatory": True,
            "input_type": "MULTI_SELECT",
            "attribute_value_list": [
                {"value_id": 20, "original_value_name": "Removable"},
                {"value_id": 21, "original_value_name": "Waterproof"},
            ],
        },
        {
            "attribute_id": 1003,
            "original_attribute_name": "Style name",
            "is_mandatory": True,
            "input_type": "TEXT",
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "Text Input"}
            ],
        },
    ]
    official.selected_attributes = [
        {
            "attribute_id": 1001,
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "PVC"}
            ],
        },
        {
            "attribute_id": 1002,
            "attribute_value_list": [
                {"value_id": 20, "original_value_name": "Removable"},
                {"value_id": 21, "original_value_name": "Waterproof"},
            ],
        },
        {
            "attribute_id": 1003,
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "Floral"}
            ],
        },
    ]
    selection = _category_execution(official)
    official.calls.clear()
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )

    candidate = adapters.observe_shopee_global_plan_candidate(
        _request(category_decision_execution=selection)
    )

    assert candidate.status == READY
    assert candidate._plan.payload()["attribute_list"] == (
        official.selected_attributes
    )


def test_global_observer_rejects_persisted_required_value_drift():
    official = _OfficialScan(existing=False)
    selection = _category_execution(official)
    official.attribute_value_name = "Vinyl"
    official.calls.clear()
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )

    with pytest.raises(shopee.ShopeeOneClickPrepareBlocked) as error:
        adapters.observe_shopee_global_plan_candidate(
            _request(category_decision_execution=selection)
        )

    assert error.value.reason_category == "CONTENT"
    assert error.value.reason_code == "shopee_category_selection_drift"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda value: value["brand"].update({"brand_id": 999}),
            "shopee_selected_brand_drift",
        ),
        (
            lambda value: value["location"].update(
                {"location_id": "OTHER"}
            ),
            "shopee_selected_location_drift",
        ),
        (
            lambda value: value["seller_stock"].update({"quantity": 50}),
            "shopee_creation_decision_drift",
        ),
        (
            lambda value: value["tier_variation"][0][
                "option_list"
            ][0].update({"option": "Invented"}),
            "shopee_single_sku_mapping_drift",
        ),
    ],
)
def test_full_execution_decision_drift_is_zero_write_blocked(
    mutate, expected_code
):
    official = _OfficialScan(existing=False)
    selection = _category_execution(official)
    mutate(selection)
    official.calls.clear()
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )

    with pytest.raises(shopee.ShopeeOneClickPrepareBlocked) as error:
        adapters.observe_shopee_global_plan_candidate(
            _request(category_decision_execution=selection)
        )

    assert error.value.reason_code == expected_code
    assert all(path.startswith("/api/v2/") for path, _ in official.calls)


def test_multi_sku_without_exact_variation_mapping_is_actionable_blocked():
    official = _OfficialScan(existing=False)
    selection = _category_execution(official)
    request = _request(category_decision_execution=selection)
    request = copy.deepcopy(request)
    request["sku_lineage"]["assignment"]["model_skus"].append(
        {"variant_key": "second", "model_sku": "0955"}
    )
    official.calls.clear()
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )

    with pytest.raises(ShopeeGlobalPlanObservationError) as error:
        adapters.observe_shopee_global_plan_candidate(request)

    assert error.value.category == "CAPABILITY"
    assert error.value.code == (
        "shopee_multi_sku_variation_mapping_required"
    )


def test_v1_category_execution_is_rejected_for_new_global():
    official = _OfficialScan(existing=False)
    selection = _category_execution(official)
    selection["schema_version"] = (
        "channel-category-decision-execution/v1"
    )
    official.calls.clear()
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )

    with pytest.raises(shopee.ShopeeOneClickPrepareBlocked) as error:
        adapters.observe_shopee_global_plan_candidate(
            _request(category_decision_execution=selection)
        )

    assert error.value.reason_code == "shopee_category_selection_invalid"
    assert official.calls
    assert not any("add_" in path for path, _params in official.calls)


def test_dynamic_observer_missing_prepared_credentials_is_auth_blocked():
    def unavailable(_region):
        raise shopee.ShopeeOneClickPreDispatchError(
            "prepared Shopee no-refresh credentials are unavailable"
        )

    shopee.configure_prepare_transport_factory(unavailable)

    with pytest.raises(ShopeeGlobalPlanObservationError) as error:
        adapters.observe_shopee_global_plan_candidate(_request())

    assert error.value.category == "AUTH"
    assert error.value.code == (
        "shopee_prepared_credentials_unavailable"
    )


def test_default_existing_observer_returns_v2_preserve_only_candidate(
    monkeypatch,
):
    official = _OfficialScan(existing=True)
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )
    from shared_platform import shopee_global_plan

    actual = (
        shopee_global_plan.build_shopee_official_existing_global_seller_stock
    )
    observed = []

    def record_shared_binding(**kwargs):
        observed.append(kwargs)
        return actual(**kwargs)

    monkeypatch.setattr(
        shopee_global_plan,
        "build_shopee_official_existing_global_seller_stock",
        record_shared_binding,
    )

    candidate = adapters.observe_shopee_global_plan_candidate(_request())

    assert candidate.status == READY
    assert candidate.mode == EXISTING_GLOBAL
    assert len(observed) == 1
    assert observed[0]["seller_stock_rows"] == [
        {"location_id": "CNZ", "stock": 200}
    ]
    assert type(observed[0]["existing_global_item_id"]) is int
    assert len(observed[0]["observation_evidence_digest"]) == 64
    approved = candidate.public_projection()
    assert approved["checks"]["no_default_execution_fact"] is True
    assert any(
        path == shopee.GLOBAL_ITEM_PATH for path, _params in official.calls
    )
    assert not any(
        path
        in {
            shopee.GLOBAL_CREATE_PATH,
            shopee.GLOBAL_MODEL_INIT_PATH,
            shopee.REGIONAL_TASK_PATH,
        }
        for path, _params in official.calls
    )


def test_dynamic_observer_rejects_partial_model_identity_before_candidate():
    request = _request()
    request["sku_lineage"]["assignment"]["model_skus"].append(
        {"variant_key": "second", "model_sku": "0955"}
    )
    official = _OfficialScan(existing=True)
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )
    shopee.configure_global_candidate_observer_factory(
        _first_party_candidate
    )

    with pytest.raises(shopee.ShopeeOneClickPrepareBlocked) as error:
        adapters.observe_shopee_global_plan_candidate(request)

    assert error.value.reason_code == (
        "shopee_existing_global_model_set_drift"
    )


def test_dynamic_observer_consumes_complete_paginated_status_scan():
    official = _PagedOfficialScan()
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )
    shopee.configure_global_candidate_observer_factory(
        _first_party_candidate
    )

    candidate = adapters.observe_shopee_global_plan_candidate(_request())

    assert candidate.status == READY
    normal_offsets = [
        params["offset"]
        for path, params in official.calls
        if path == shopee.GLOBAL_LIST_PATH
        and params["item_status"] == "NORMAL"
    ]
    assert normal_offsets == [0, 100]


def test_dynamic_observer_incomplete_page_blocks_before_first_party_candidate():
    official = _PagedOfficialScan(incomplete=True)
    first_party_calls = []
    shopee.configure_prepare_transport_factory(
        lambda _region: official.transport()
    )
    shopee.configure_global_candidate_observer_factory(
        lambda *_args: first_party_calls.append(True)
    )

    with pytest.raises(
        shopee.ShopeeOneClickPreDispatchError,
        match="pagination is incomplete",
    ):
        adapters.observe_shopee_global_plan_candidate(_request())

    assert first_party_calls == []
    assert not any(
        path
        in {
            shopee.GLOBAL_CREATE_PATH,
            shopee.GLOBAL_MODEL_INIT_PATH,
            shopee.REGIONAL_TASK_PATH,
        }
        for path, _params in official.calls
    )
