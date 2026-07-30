from __future__ import annotations

import hashlib
import json
import unicodedata

import pytest

from domains.channel_operations import oneclick_release_adapters as adapters
from modules.shopee import global_plan_candidate as subject
from modules.shopee import oneclick_release as shopee
from shared_platform.shopee_global_plan import BLOCKED_CAPABILITY, NEW_GLOBAL


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _context() -> dict[str, object]:
    return {
        "schema_version": "channel-category-observer-request/v2",
        "product_id": "3845131687",
        "product_revision": 7,
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "source_identity_digest": _digest("source"),
        "sku_lineage_digest": _digest("lineage"),
        "approved_copy_digest": _digest("copy"),
        "targets_digest": _digest("targets"),
    }


def _request(
    *,
    current_selection=None,
    current_attribute_selection=None,
) -> dict[str, object]:
    title = "  Café PVC wall decal  "
    return {
        "schema_version": "channel-category-observer-request/v2",
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "context": _context(),
        "approved_title": title,
        "approved_title_digest": hashlib.sha256(
            unicodedata.normalize("NFC", title.strip()).encode("utf-8")
        ).hexdigest(),
        "current_selection": current_selection,
        "current_attribute_selection": current_attribute_selection,
    }


class _OfficialReadFake:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error_path: str | None = None
        self.malformed_path: str | None = None
        self.loop_brand = False
        self.live_brand_pages = False
        self.nonleaf_category_ids: set[int] = set()
        self.attribute_value_name = "PVC"
        self.attribute_value_id = 91
        self.recommendation_field = "category_id_list"
        self.full_category_tree = False
        self.live_attribute_tree = False
        self.attribute_rows_by_category: dict[
            int, list[dict[str, object]]
        ] = {}

    def merchant_get(self, path, params):
        self.calls.append((path, dict(params)))
        if path == self.error_path:
            return {
                "error": "invalid access token",
                "message": "redacted",
                "response": {},
            }
        if path == self.malformed_path:
            return {"error": "", "response": {"unexpected": True}}
        if path == subject.CATEGORY_RECOMMEND_PATH:
            return {
                "error": "",
                "response": {
                    self.recommendation_field: [101, 202]
                },
            }
        if path == subject.CATEGORY_PATH_PATH:
            category_id = params["category_id"]
            rows = [
                {
                    "category_id": 10,
                    "parent_category_id": 0,
                    "original_category_name": "Home",
                    "has_children": True,
                },
                {
                    "category_id": category_id,
                    "parent_category_id": 10,
                    "original_category_name": (
                        "Wall Stickers"
                        if category_id == 101
                        else "Decorative Decals"
                    ),
                    "has_children": (
                        category_id in self.nonleaf_category_ids
                    ),
                },
            ]
            if self.full_category_tree:
                rows = [
                    {
                        **row,
                        "display_category_name": row[
                            "original_category_name"
                        ],
                        "debug_message": None,
                    }
                    for row in rows
                ]
                rows.insert(
                    1,
                    {
                        "category_id": 20,
                        "parent_category_id": 0,
                        "original_category_name": "Unrelated",
                        "display_category_name": "Unrelated",
                        "has_children": False,
                        "debug_message": None,
                    },
                )
            envelope = {
                "error": "",
                "response": {"category_list": rows},
            }
            if self.full_category_tree:
                envelope.update(
                    {"debug_message": "", "warning": ""}
                )
            return envelope
        if path == subject.ATTRIBUTE_TREE_PATH:
            category_id = int(params["category_id_list"])
            attribute_rows = self.attribute_rows_by_category.get(
                category_id
            )
            if attribute_rows is None:
                attribute_rows = [
                    {
                        "attribute_id": 9001,
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
                    },
                    {
                        "attribute_id": 9002,
                        "original_attribute_name": "Pattern",
                        "is_mandatory": False,
                        "input_type": "TEXT",
                        "attribute_value_list": [],
                    },
                ]
            if self.live_attribute_tree:
                return {
                    "error": "",
                    "debug_message": "",
                    "message": "",
                    "request_id": "fixture",
                    "warning": "",
                    "response": {
                        "list": [
                            {
                                "category_id": category_id,
                                "attribute_tree": [
                                    {
                                        "attribute_id": 9001,
                                        "mandatory": True,
                                        "name": "Material",
                                        "attribute_value_list": [
                                            {
                                                "value_id": 91,
                                                "name": "PVC",
                                                "multi_lang": [
                                                    {
                                                        "language": "en",
                                                        "value": "PVC",
                                                    }
                                                ],
                                            },
                                            {
                                                "value_id": 92,
                                                "name": "Other",
                                                "child_attribute_list": [
                                                    {
                                                        "attribute_id": 9901,
                                                        "mandatory": False,
                                                        "name": "Other detail",
                                                        "attribute_info": {
                                                            "input_type": 3,
                                                            "input_validation_type": 2,
                                                            "format_type": 1,
                                                            "is_oem": False,
                                                            "support_search_value": False,
                                                        },
                                                    }
                                                ],
                                            },
                                        ],
                                        "attribute_info": {
                                            "input_type": 1,
                                            "input_validation_type": 0,
                                            "format_type": 1,
                                            "is_oem": False,
                                            "support_search_value": False,
                                        },
                                        "multi_lang": [
                                            {
                                                "language": "en",
                                                "value": "Material",
                                            }
                                        ],
                                    },
                                    {
                                        "attribute_id": 9002,
                                        "mandatory": False,
                                        "name": "Pattern name",
                                        "attribute_info": {
                                            "input_type": 3,
                                            "input_validation_type": 2,
                                            "format_type": 1,
                                            "is_oem": False,
                                            "support_search_value": False,
                                        },
                                    },
                                ],
                            }
                        ]
                    },
                }
            return {
                "error": "",
                "response": {
                    "attribute_list": attribute_rows
                },
            }
        if path == subject.BRAND_LIST_PATH:
            offset = params["offset"]
            if self.live_brand_pages:
                return {
                    "error": "",
                    "debug_message": "",
                    "message": "",
                    "request_id": "fixture",
                    "warning": "",
                    "response": {
                        "brand_list": [
                            {
                                "brand_id": 0 if offset == 0 else 2,
                                "original_brand_name": (
                                    "NoBrand"
                                    if offset == 0
                                    else "Brand B"
                                ),
                                "display_brand_name": (
                                    "NoBrand"
                                    if offset == 0
                                    else "Brand B"
                                ),
                            }
                        ],
                        "has_next_page": offset == 0,
                        "next_offset": 100,
                        "is_mandatory": False,
                        "input_type": "SINGLE_COMBO_BOX",
                    },
                }
            if offset == 0:
                return {
                    "error": "",
                    "response": {
                        "brand_list": [
                            {
                                "brand_id": 0,
                                "original_brand_name": "NoBrand",
                            }
                        ],
                        "total_count": 2,
                        "has_next_page": True,
                        "next_offset": 0 if self.loop_brand else 100,
                    },
                }
            return {
                "error": "",
                "response": {
                    "brand_list": [
                        {
                            "brand_id": 2,
                            "original_brand_name": "Brand B",
                        }
                    ],
                    "total_count": 2,
                    "has_next_page": False,
                },
            }
        if path == subject.SELLER_LOCATION_PATH:
            return {
                "error": "",
                "response": [
                    {"location_id": "CNZ", "warehouse_name": "中国仓库"},
                    {"location_id": "CNH", "warehouse_name": "Secondary"},
                ],
            }
        raise AssertionError((path, params))

    def transport(self) -> shopee.ShopeePrepareTransport:
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


@pytest.fixture(autouse=True)
def _reset_factory():
    shopee.configure_prepare_transport_factory(None)
    yield
    shopee.configure_prepare_transport_factory(None)


def _install(fake: _OfficialReadFake) -> None:
    shopee.configure_prepare_transport_factory(
        lambda _region: fake.transport()
    )


def _selection(fake: _OfficialReadFake, category_id: int = 202):
    transport = fake.transport()
    path = subject._read_category_path(transport, category_id)
    tree = subject._read_attribute_tree(transport, category_id)
    brand = subject._brand_option_projection(
        subject._read_all_brands(transport, category_id)
    )[0]
    location = subject._location_option_projection(
        subject._read_seller_locations(transport)
    )[0]
    creation = subject._creation_default_projection()
    context_digest = _digest(_context())
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
        "context_digest": _digest(_context()),
        "options_digest": _digest("options"),
        "selected_category_identity_digest": _digest("selected"),
        "category": {
            "category_id": category_id,
            "path": [dict(row) for row in path],
            "path_complete": True,
            "evidence_digest": _digest(path),
        },
        "attribute_list": [
            {
                "attribute_id": 9001,
                "attribute_value_list": [
                    {
                        "value_id": fake.attribute_value_id,
                        "original_value_name": "PVC",
                    }
                ],
            }
        ],
        "attributes_complete": True,
        "attribute_tree_digest": _digest(tree),
        "brand": {
            "brand_id": brand["brand_id"],
            "original_brand_name": brand["original_brand_name"],
            "evidence_digest": brand["evidence_digest"],
        },
        "seller_stock": {
            "source": "kyle-explicit-seller-stock/v1",
            "source_digest": subject.seller_stock_source_digest(
                context_digest=context_digest,
                creation_fact_identity_digest=(
                    creation_identity_digest
                ),
                location_identity_digest=location_identity_digest,
                quantity=creation["seller_stock_quantity"],
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


def _attribute_selection(
    fake: _OfficialReadFake,
    *,
    category_id: int,
    selected_attributes: list[dict[str, object]],
) -> dict[str, object]:
    tree = subject._read_attribute_tree(fake.transport(), category_id)
    return {
        "schema_version": (
            "channel-category-attribute-selection-execution/v1"
        ),
        "product_id": _context()["product_id"],
        "product_revision": _context()["product_revision"],
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "selection_digest": _digest("attribute-selection"),
        "context_digest": _digest(_context()),
        "options_digest": _digest("attribute-options"),
        "category_identity_digest": _digest(
            f"category-{category_id}"
        ),
        "selected_brand_identity_digest": _digest("selected-brand"),
        "selected_location_identity_digest": _digest(
            "selected-location"
        ),
        "selected_creation_fact_identity_digest": _digest(
            "selected-creation"
        ),
        "attribute_tree_digest": _digest(tree),
        "selected_attributes": selected_attributes,
    }


def test_category_options_recommend_but_never_auto_approve_required_value():
    fake = _OfficialReadFake()
    _install(fake)

    result = adapters.observe_channel_category_options(_request())

    assert set(result) == {
        "schema_version",
        "channel",
        "mode",
        "authority",
        "recommendation_source",
        "recommended_category_id",
        "options",
        "brand_options",
        "location_options",
        "creation_defaults",
    }
    assert result["schema_version"] == (
        "channel-category-options-observation/v2"
    )
    assert result["recommended_category_id"] == 101
    assert [row["category_id"] for row in result["options"]] == [101, 202]
    assert all(row["selected_attributes"] == [] for row in result["options"])
    assert all(
        row["required_values_complete"] is False
        for row in result["options"]
    )
    assert result["options"][0]["missing_required_attributes"] == [
        {
            "attribute_id": 9001,
            "label": "Material",
            "selection_kind": "SINGLE",
            "option_values": [
                {
                    "value_id": 91,
                    "original_value_name": "PVC",
                    "recommended": False,
                }
            ],
            "text_value_id": None,
        }
    ]
    assert all(
        path in subject.AUDITED_OFFICIAL_READ_ENDPOINTS
        for path, _params in fake.calls
    )
    assert sum(row["recommended"] for row in result["brand_options"]) == 1
    assert sum(row["recommended"] for row in result["location_options"]) == 1
    assert result["creation_defaults"] == (
        subject._creation_default_projection()
    )
    assert not any("post" in path.casefold() for path, _params in fake.calls)


def test_live_category_id_recommendation_field_is_strictly_supported():
    fake = _OfficialReadFake()
    fake.recommendation_field = "category_id"
    _install(fake)

    result = adapters.observe_channel_category_options(_request())

    assert result["recommended_category_id"] == 101
    assert [row["category_id"] for row in result["options"]] == [101, 202]


def test_live_full_category_tree_reconstructs_selected_paths():
    fake = _OfficialReadFake()
    fake.full_category_tree = True
    _install(fake)

    result = adapters.observe_channel_category_options(_request())

    assert result["options"][0]["path"] == [
        {"category_id": 10, "name": "Home"},
        {"category_id": 101, "name": "Wall Stickers"},
    ]
    assert all(
        row["category_id"] != 20
        for option in result["options"]
        for row in option["path"]
    )


def test_nonleaf_official_recommendation_is_not_offered_for_publish():
    fake = _OfficialReadFake()
    fake.nonleaf_category_ids.add(202)
    _install(fake)

    result = adapters.observe_channel_category_options(_request())

    assert [row["category_id"] for row in result["options"]] == [101]
    assert result["recommended_category_id"] == 101
    assert any(
        path == subject.CATEGORY_PATH_PATH
        and params["category_id"] == 202
        for path, params in fake.calls
    )


def test_explicit_nonleaf_category_selection_fails_closed():
    fake = _OfficialReadFake()
    selection = _selection(fake, category_id=202)
    fake.nonleaf_category_ids.add(202)
    _install(fake)

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        adapters.observe_channel_category_options(
            _request(current_selection=selection)
        )

    assert error.value.reason_code == "shopee_category_not_publishable"


def test_live_grouped_attribute_tree_is_strictly_normalized():
    fake = _OfficialReadFake()
    fake.live_attribute_tree = True
    _install(fake)

    result = adapters.observe_channel_category_options(_request())

    offered = result["options"][0]
    assert offered["missing_required_attributes"] == [
        {
            "attribute_id": 9001,
            "label": "Material",
            "selection_kind": "SINGLE",
            "option_values": [
                {
                    "value_id": 91,
                    "original_value_name": "PVC",
                    "recommended": False,
                },
                {
                    "value_id": 92,
                    "original_value_name": "Other",
                    "recommended": False,
                },
            ],
            "text_value_id": None,
        }
    ]
    attribute_calls = [
        params
        for path, params in fake.calls
        if path == subject.ATTRIBUTE_TREE_PATH
    ]
    assert attribute_calls
    assert all(
        set(params) == {"category_id_list", "language"}
        and params["category_id_list"] in {"101", "202"}
        and params["language"] == "en"
        for params in attribute_calls
    )


def test_live_brand_pages_use_numeric_status_and_terminate_by_cursor():
    fake = _OfficialReadFake()
    fake.live_brand_pages = True
    _install(fake)

    result = adapters.observe_channel_category_options(_request())

    assert len(result["brand_options"]) == 2
    brand_calls = [
        params
        for path, params in fake.calls
        if path == subject.BRAND_LIST_PATH
    ]
    assert len(brand_calls) == 2
    assert all(params["status"] == 1 for params in brand_calls)
    assert {
        params["offset"] for params in brand_calls
    } == {0, 100}


def test_live_exact_duplicate_brand_is_deduplicated_but_conflict_fails():
    fake = _OfficialReadFake()
    fake.live_brand_pages = True
    original = fake.merchant_get

    def duplicated(path, params):
        response = original(path, params)
        if path == subject.BRAND_LIST_PATH and params["offset"] == 0:
            response["response"]["brand_list"].append(
                dict(response["response"]["brand_list"][0])
            )
        return response

    fake.merchant_get = duplicated
    assert len(subject._read_all_brands(fake.transport(), 101)) == 2

    def conflicting(path, params):
        response = original(path, params)
        if path == subject.BRAND_LIST_PATH and params["offset"] == 0:
            row = dict(response["response"]["brand_list"][0])
            row["display_brand_name"] = "Conflicting display"
            response["response"]["brand_list"].append(row)
        return response

    fake.merchant_get = conflicting
    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        subject._read_all_brands(fake.transport(), 101)
    assert error.value.reason_code == "shopee_brand_list_invalid"


def test_fixed_no_brand_accepts_official_spacing_alias():
    result = subject._brand_option_projection(
        (
            {
                "brand_id": 0,
                "original_brand_name": "No Brand",
            },
            {
                "brand_id": 2,
                "original_brand_name": "Brand B",
            },
        )
    )

    assert [row["brand_id"] for row in result if row["recommended"]] == [0]


def test_official_brand_without_no_brand_policy_fails_closed():
    fake = _OfficialReadFake()
    original_merchant_get = fake.merchant_get

    def merchant_get(path, params):
        response = original_merchant_get(path, params)
        if path == subject.BRAND_LIST_PATH and params["offset"] == 0:
            response["response"]["brand_list"][0][
                "original_brand_name"
            ] = "Generic"
        return response

    fake.merchant_get = merchant_get
    _install(fake)
    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        adapters.observe_channel_category_options(_request())

    assert error.value.reason_code == "shopee_fixed_no_brand_unavailable"


def test_ambiguous_no_brand_policy_fails_closed():
    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        subject._brand_option_projection(
            (
                {
                    "brand_id": 0,
                    "original_brand_name": "NoBrand",
                },
                {
                    "brand_id": 0,
                    "original_brand_name": "No Brand",
                },
            )
        )

    assert error.value.reason_code == "shopee_fixed_no_brand_unavailable"


@pytest.mark.parametrize("shape", ["missing", "duplicate"])
def test_official_location_without_unique_china_warehouse_fails_closed(shape):
    fake = _OfficialReadFake()
    original_merchant_get = fake.merchant_get

    def merchant_get(path, params):
        response = original_merchant_get(path, params)
        if path == subject.SELLER_LOCATION_PATH:
            rows = response["response"]
            if shape == "missing":
                rows[0]["warehouse_name"] = "Other Warehouse"
            else:
                rows.append(
                    {
                        "location_id": "CNX",
                        "warehouse_name": "中国仓库",
                    }
                )
        return response

    fake.merchant_get = merchant_get
    _install(fake)
    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        adapters.observe_channel_category_options(_request())

    assert (
        error.value.reason_code
        == "shopee_fixed_china_warehouse_unavailable"
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda tree: [
            *tree,
            {**tree[0], "attribute_id": tree[0]["attribute_id"]},
        ],
        lambda tree: [
            {
                **tree[0],
                "attribute_info": {
                    **tree[0]["attribute_info"],
                    "input_type": True,
                },
            },
            *tree[1:],
        ],
        lambda tree: [
            {
                **tree[0],
                "attribute_value_list": [
                    *tree[0]["attribute_value_list"],
                    {
                        **tree[0]["attribute_value_list"][0],
                        "value_id": tree[0]["attribute_value_list"][0][
                            "value_id"
                        ],
                    },
                ],
            },
            *tree[1:],
        ],
    ],
)
def test_live_grouped_attribute_tree_mixed_shapes_fail_closed(mutator):
    fake = _OfficialReadFake()
    fake.live_attribute_tree = True
    original = fake.merchant_get

    def malformed(path, params):
        response = original(path, params)
        if path == subject.ATTRIBUTE_TREE_PATH:
            group = response["response"]["list"][0]
            group["attribute_tree"] = mutator(group["attribute_tree"])
        return response

    fake.merchant_get = malformed
    _install(fake)

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        adapters.observe_channel_category_options(_request())

    assert error.value.reason_code == "shopee_attribute_tree_invalid"


def test_ambiguous_recommendation_fields_fail_closed():
    fake = _OfficialReadFake()
    original = fake.merchant_get

    def ambiguous(path, params):
        if path == subject.CATEGORY_RECOMMEND_PATH:
            return {
                "error": "",
                "response": {
                    "category_id": [101],
                    "category_id_list": [101],
                },
            }
        return original(path, params)

    fake.merchant_get = ambiguous
    _install(fake)

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        adapters.observe_channel_category_options(_request())

    assert error.value.reason_code == (
        "shopee_category_recommendation_invalid"
    )


def test_required_attribute_option_value_zero_is_projected_and_selectable():
    fake = _OfficialReadFake()
    fake.attribute_value_id = 0
    _install(fake)

    preview = adapters.observe_channel_category_options(_request())
    assert preview["options"][0]["missing_required_attributes"][0][
        "option_values"
    ] == [
        {
            "value_id": 0,
            "original_value_name": "PVC",
            "recommended": False,
        }
    ]

    selection = _selection(fake, category_id=202)
    refreshed = adapters.observe_channel_category_options(
        _request(current_selection=selection)
    )
    selected = next(
        row for row in refreshed["options"] if row["category_id"] == 202
    )
    assert selected["required_values_complete"] is True
    assert selected["selected_attributes"][0][
        "attribute_value_list"
    ] == [{"value_id": 0, "original_value_name": "PVC"}]


def test_explicit_single_multi_text_selection_is_officially_rechecked():
    fake = _OfficialReadFake()
    fake.attribute_rows_by_category[101] = [
        {
            "attribute_id": 9101,
            "original_attribute_name": "Material",
            "is_mandatory": True,
            "input_type": "SINGLE_SELECT",
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "PVC"},
                {"value_id": 1, "original_value_name": "Vinyl"},
            ],
        },
        {
            "attribute_id": 9102,
            "original_attribute_name": "Features",
            "is_mandatory": True,
            "input_type": "MULTI_SELECT",
            "attribute_value_list": [
                {"value_id": 20, "original_value_name": "Removable"},
                {"value_id": 21, "original_value_name": "Waterproof"},
            ],
        },
        {
            "attribute_id": 9103,
            "original_attribute_name": "Style name",
            "is_mandatory": True,
            "input_type": "TEXT",
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "Text Input"}
            ],
        },
    ]
    _install(fake)

    preview = adapters.observe_channel_category_options(_request())
    recommended = preview["options"][0]
    assert [
        row["selection_kind"]
        for row in recommended["missing_required_attributes"]
    ] == ["SINGLE", "MULTI", "TEXT"]
    assert recommended["missing_required_attributes"][2] == {
        "attribute_id": 9103,
        "label": "Style name",
        "selection_kind": "TEXT",
        "option_values": [],
        "text_value_id": 0,
    }
    selected_attributes = [
        {
            "attribute_id": 9101,
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "PVC"}
            ],
        },
        {
            "attribute_id": 9102,
            "attribute_value_list": [
                {
                    "value_id": 20,
                    "original_value_name": "Removable",
                },
                {
                    "value_id": 21,
                    "original_value_name": "Waterproof",
                },
            ],
        },
        {
            "attribute_id": 9103,
            "attribute_value_list": [
                {
                    "value_id": 0,
                    "original_value_name": "Floral",
                }
            ],
        },
    ]
    draft = _attribute_selection(
        fake,
        category_id=101,
        selected_attributes=selected_attributes,
    )
    fake.calls.clear()

    refreshed = adapters.observe_channel_category_options(
        _request(current_attribute_selection=draft)
    )

    selected = next(
        row for row in refreshed["options"] if row["category_id"] == 101
    )
    assert selected["selected_attributes"] == selected_attributes
    assert selected["required_values_complete"] is True
    assert selected["missing_required_attributes"] == []
    assert sum(
        path == subject.ATTRIBUTE_TREE_PATH
        and params["category_id_list"] == "101"
        for path, params in fake.calls
    ) == 1


@pytest.mark.parametrize(
    "mutator",
    [
        lambda rows: [
            {
                **rows[0],
                "attribute_value_list": [
                    {"value_id": 0, "original_value_name": "PVC"},
                    {"value_id": 1, "original_value_name": "Vinyl"},
                ],
            },
            *rows[1:],
        ],
        lambda rows: [
            *rows[:2],
            {
                **rows[2],
                "attribute_value_list": [
                    {
                        "value_id": 99,
                        "original_value_name": "Floral",
                    }
                ],
            },
        ],
        lambda rows: [
            *rows[:2],
            {
                **rows[2],
                "attribute_value_list": [
                    {
                        "value_id": 0,
                        "original_value_name": " Floral ",
                    }
                ],
            },
        ],
    ],
)
def test_explicit_attribute_kind_or_text_identity_drift_fails_closed(
    mutator,
):
    fake = _OfficialReadFake()
    fake.attribute_rows_by_category[101] = [
        {
            "attribute_id": 9101,
            "original_attribute_name": "Material",
            "is_mandatory": True,
            "input_type": "SINGLE_SELECT",
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "PVC"},
                {"value_id": 1, "original_value_name": "Vinyl"},
            ],
        },
        {
            "attribute_id": 9102,
            "original_attribute_name": "Features",
            "is_mandatory": True,
            "input_type": "MULTI_SELECT",
            "attribute_value_list": [
                {"value_id": 20, "original_value_name": "Removable"}
            ],
        },
        {
            "attribute_id": 9103,
            "original_attribute_name": "Style name",
            "is_mandatory": True,
            "input_type": "TEXT",
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "Text Input"}
            ],
        },
    ]
    rows = [
        {
            "attribute_id": 9101,
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "PVC"}
            ],
        },
        {
            "attribute_id": 9102,
            "attribute_value_list": [
                {"value_id": 20, "original_value_name": "Removable"}
            ],
        },
        {
            "attribute_id": 9103,
            "attribute_value_list": [
                {"value_id": 0, "original_value_name": "Floral"}
            ],
        },
    ]
    draft = _attribute_selection(
        fake,
        category_id=101,
        selected_attributes=mutator(rows),
    )
    _install(fake)

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError):
        adapters.observe_channel_category_options(
            _request(current_attribute_selection=draft)
        )


def test_attribute_selection_identity_or_official_tree_drift_fails_closed():
    fake = _OfficialReadFake()
    rows = [
        {
            "attribute_id": 9001,
            "attribute_value_list": [
                {"value_id": 91, "original_value_name": "PVC"}
            ],
        }
    ]
    draft = _attribute_selection(
        fake,
        category_id=101,
        selected_attributes=rows,
    )
    fake.calls.clear()
    _install(fake)
    with pytest.raises(subject.ShopeeGlobalPlanCandidateError):
        adapters.observe_channel_category_options(
            _request(
                current_attribute_selection={
                    **draft,
                    "selection_digest": "not-a-digest",
                }
            )
        )
    assert fake.calls == []

    fake.calls.clear()
    fake.attribute_value_name = "Vinyl"
    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        adapters.observe_channel_category_options(
            _request(current_attribute_selection=draft)
        )
    assert error.value.reason_code == "shopee_attribute_selection_drift"


def test_persisted_nonrecommended_selection_is_refetched_and_revalidated():
    fake = _OfficialReadFake()
    selection = _selection(fake)
    fake.calls.clear()
    _install(fake)

    result = adapters.observe_channel_category_options(
        _request(current_selection=selection)
    )

    selected = next(
        row for row in result["options"] if row["category_id"] == 202
    )
    assert selected["selected_attributes"] == selection["attribute_list"]
    assert selected["required_values_complete"] is True
    assert selected["missing_required_attributes"] == []
    assert sum(
        path == subject.ATTRIBUTE_TREE_PATH
        and params["category_id_list"] == "202"
        for path, params in fake.calls
    ) == 1
    assert result["recommended_category_id"] == 101


def test_persisted_selection_attribute_tree_drift_fails_closed():
    fake = _OfficialReadFake()
    selection = _selection(fake)
    fake.attribute_value_name = "Vinyl"
    fake.calls.clear()
    _install(fake)

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        adapters.observe_channel_category_options(
            _request(current_selection=selection)
        )

    assert error.value.reason_code == "shopee_category_selection_drift"


@pytest.mark.parametrize(
    "request_mutator",
    [
        lambda value: {**value, "unknown": True},
        lambda value: {**value, "approved_title_digest": _digest("bad")},
        lambda value: {**value, "current_selection": False},
        lambda value: {**value, "current_attribute_selection": False},
    ],
)
def test_category_request_and_identity_shapes_fail_before_official_read(
    request_mutator,
):
    fake = _OfficialReadFake()
    _install(fake)

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError):
        adapters.observe_channel_category_options(
            request_mutator(_request())
        )

    assert fake.calls == []


@pytest.mark.parametrize("recommendation_field", ["category_id", "category_id_list"])
def test_official_candidate_observes_brand_and_locations_without_defaults(
    recommendation_field,
):
    fake = _OfficialReadFake()
    fake.recommendation_field = recommendation_field
    observation = subject.observe_official_new_global_candidate(
        approved_title="Approved title",
        selected_category_id=101,
        transport=fake.transport(),
    )

    public = observation.public_projection()
    assert public["counts"] == {
        "category_candidates": 2,
        "category_path": 2,
        "attributes": 2,
        "required_attributes": 1,
        "required_attribute_decisions": 0,
        "brand_candidates": 2,
        "seller_locations": 2,
    }
    assert public["checks"]["required_attributes_complete"] is False
    assert public["checks"]["stock_decision_present"] is False
    assert public["checks"]["no_default_brand"] is True
    assert public["checks"]["no_default_stock"] is True
    assert public["checks"]["no_default_location"] is True
    serialized = json.dumps(public, sort_keys=True)
    for forbidden in (
        "fixture-shop-token",
        "NoBrand",
        "中国仓库",
        "9001",
        "101",
    ):
        assert forbidden not in serialized


def test_official_candidate_rejects_ambiguous_recommendation_fields():
    fake = _OfficialReadFake()
    original_merchant_get = fake.merchant_get

    def merchant_get(path, params):
        if path == subject.CATEGORY_RECOMMEND_PATH:
            return {
                "error": "",
                "response": {
                    "category_id": [101, 202],
                    "category_id_list": [101, 202],
                },
            }
        return original_merchant_get(path, params)

    fake.merchant_get = merchant_get

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        subject.observe_official_new_global_candidate(
            approved_title="Approved title",
            selected_category_id=101,
            transport=fake.transport(),
        )

    assert error.value.reason_code == "shopee_category_recommendation_invalid"


def test_global_candidate_remains_blocked_without_approved_execution_facts():
    fake = _OfficialReadFake()
    seed = {
        "title": "Approved title",
        "selected_category_id": 101,
    }
    candidate = subject.build_official_new_global_candidate(
        {}, seed, fake.transport()
    )

    assert candidate.status == BLOCKED_CAPABILITY
    assert candidate.mode == NEW_GLOBAL
    assert candidate.planning_allowed is False
    assert candidate.blocker_codes
    assert candidate.public_projection()["counts"] == {}


@pytest.mark.parametrize(
    ("path", "expected_category"),
    [
        (subject.CATEGORY_RECOMMEND_PATH, "AUTH"),
        (subject.CATEGORY_PATH_PATH, "AUTH"),
        (subject.ATTRIBUTE_TREE_PATH, "AUTH"),
        (subject.BRAND_LIST_PATH, "AUTH"),
        (subject.SELLER_LOCATION_PATH, "AUTH"),
    ],
)
def test_official_error_envelopes_fail_closed(path, expected_category):
    fake = _OfficialReadFake()
    fake.error_path = path

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        subject.observe_official_new_global_candidate(
            approved_title="Approved title",
            selected_category_id=101,
            transport=fake.transport(),
        )

    assert error.value.reason_category == expected_category


@pytest.mark.parametrize(
    "path",
    [
        subject.CATEGORY_RECOMMEND_PATH,
        subject.CATEGORY_PATH_PATH,
        subject.ATTRIBUTE_TREE_PATH,
        subject.BRAND_LIST_PATH,
        subject.SELLER_LOCATION_PATH,
    ],
)
def test_malformed_official_shapes_fail_closed(path):
    fake = _OfficialReadFake()
    fake.malformed_path = path

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError):
        subject.observe_official_new_global_candidate(
            approved_title="Approved title",
            selected_category_id=101,
            transport=fake.transport(),
        )


def test_brand_pagination_cursor_loop_fails_closed():
    fake = _OfficialReadFake()
    fake.loop_brand = True

    with pytest.raises(subject.ShopeeGlobalPlanCandidateError) as error:
        subject.observe_official_new_global_candidate(
            approved_title="Approved title",
            selected_category_id=101,
            transport=fake.transport(),
        )

    assert error.value.reason_code == "shopee_brand_pagination_invalid"


def test_generated_sdk_metadata_cannot_promote_an_endpoint():
    assert subject.GENERATED_SDK_METADATA_AUTHORITY == (
        "untrusted_endpoint_hint_only"
    )
    assert all(
        path.startswith("/api/v2/")
        for path in subject.AUDITED_OFFICIAL_READ_ENDPOINTS
    )
    assert not hasattr(subject, "GENERATED_SDK_ENDPOINT_HINTS")
