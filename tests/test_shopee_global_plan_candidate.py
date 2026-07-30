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
        "schema_version": "channel-category-observer-request/v1",
        "product_id": "3845131687",
        "product_revision": 7,
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "source_identity_digest": _digest("source"),
        "sku_lineage_digest": _digest("lineage"),
        "approved_copy_digest": _digest("copy"),
        "targets_digest": _digest("targets"),
    }


def _request(*, current_selection=None) -> dict[str, object]:
    title = "  Café PVC wall decal  "
    return {
        "schema_version": "channel-category-observer-request/v1",
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "context": _context(),
        "approved_title": title,
        "approved_title_digest": hashlib.sha256(
            unicodedata.normalize("NFC", title.strip()).encode("utf-8")
        ).hexdigest(),
        "current_selection": current_selection,
    }


class _OfficialReadFake:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error_path: str | None = None
        self.malformed_path: str | None = None
        self.loop_brand = False
        self.attribute_value_name = "PVC"

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
                "response": {"category_id_list": [101, 202]},
            }
        if path == subject.CATEGORY_PATH_PATH:
            category_id = params["category_id"]
            return {
                "error": "",
                "response": {
                    "category_list": [
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
                            "has_children": False,
                        },
                    ]
                },
            }
        if path == subject.ATTRIBUTE_TREE_PATH:
            return {
                "error": "",
                "response": {
                    "attribute_list": [
                        {
                            "attribute_id": 9001,
                            "original_attribute_name": "Material",
                            "is_mandatory": True,
                            "input_type": "SINGLE_SELECT",
                            "attribute_value_list": [
                                {
                                    "value_id": 91,
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
                },
            }
        if path == subject.BRAND_LIST_PATH:
            offset = params["offset"]
            if offset == 0:
                return {
                    "error": "",
                    "response": {
                        "brand_list": [
                            {
                                "brand_id": 1,
                                "original_brand_name": "Brand A",
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
                    {"location_id": "CNZ", "warehouse_name": "Primary"},
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
                    {"value_id": 91, "original_value_name": "PVC"}
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
            "source_digest": creation["evidence_digest"],
            "quantity": creation["seller_stock_quantity"],
            "approval_reference": "Kyle/category-decision/test",
        },
        "location": {
            "location_id": location["location_id"],
            "evidence_digest": location["evidence_digest"],
        },
        "condition": creation["condition"],
        "preorder": creation["preorder"],
        "tier_variation": [
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
        "global_model": [
            {
                "global_model_sku": "0954",
                "tier_index": [0],
                "original_price_cny": "56.05",
                "seller_stock_quantity": 200,
            }
        ],
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
            "selection_kind": "single",
            "option_values": [
                {"value_id": 91, "original_value_name": "PVC"}
            ],
        }
    ]
    assert all(
        path in subject.AUDITED_OFFICIAL_READ_ENDPOINTS
        for path, _params in fake.calls
    )
    assert result["brand_options"][0]["recommended"] is False
    assert result["location_options"][0]["recommended"] is False
    assert result["creation_defaults"] == (
        subject._creation_default_projection()
    )
    assert not any("post" in path.casefold() for path, _params in fake.calls)


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
        and params["category_id"] == 202
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


def test_official_candidate_observes_brand_and_locations_without_defaults():
    fake = _OfficialReadFake()
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
        "Brand A",
        "Primary",
        "9001",
        "101",
    ):
        assert forbidden not in serialized


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
