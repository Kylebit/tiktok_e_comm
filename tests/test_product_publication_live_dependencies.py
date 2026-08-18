from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.ozon.approved_publication_v4 import OzonDispatchFact
from modules.miaoshou.tiktok_v4_drafts import DraftWriteFact
from modules.shopee.skill_regions import RegionContext
from shared_platform.product_publication_live_dependencies import (
    CATEGORY_METADATA_PATH,
    CATEGORY_TREE_PATH,
    DurableTikTokV4DraftPreparer,
    LivePublicationDependencyError,
    MIAOSHOU_COMMON_LIST_PATH,
    MIAOSHOU_TIKTOK_LIST_PATH,
    MiaoshouTikTokV4DraftTransportFactory,
    OfficialMiaoshouTikTokCategoryResolver,
    OfficialMiaoshouTikTokV4SeedIdentityResolver,
    OfficialOzonFridgeMagnetProfileResolver,
    OfficialOzonV4Transport,
    ShopeeExactGlobalItemResolver,
    StoredOzonLocalizedCopyResolver,
    TikTokUnavailableStorefrontReadback,
    TikTokV4DraftCheckpointStore,
    build_live_ozon_dependencies,
    build_live_shopee_dependencies,
    build_live_tiktok_dependencies,
    build_ozon_import_item_from_frozen_variant,
)
from shared_platform.product_publication_executors import build_tiktok_v4_executor
from shared_platform.product_publication_runner import PublicationPlatformRequest
from test_tiktok_v4_execution import CategoryResolver, _snapshot


def _tiktok_product(category_name: str = "Refrigerator Magnets") -> dict:
    return {
        "title": "Decorative Resin Fridge Magnet",
        "description": "Approved product description",
        "images": ["https://example.test/main.jpg"],
        "main_category": {
            "id": "internal-fridge-magnets",
            "name": category_name,
            "path": [
                {"id": "internal-home", "name": "Home Decor"},
                {"id": "internal-fridge-magnets", "name": category_name},
            ],
        },
    }


def _category_post(*, exact_disabled: bool = False, include_fallback: bool = False):
    calls: list[tuple[str, dict]] = []

    def post(path: str, body: dict):
        calls.append((path, deepcopy(body)))
        if path == CATEGORY_TREE_PATH:
            tree = {
                "800000": {
                    "cid": 800000,
                    "disabled": False,
                    "name": "Home Decor",
                    "children": {
                        "854536": {
                            "cid": 854536,
                            "disabled": exact_disabled,
                            "name": "Refrigerator Magnets",
                            "nameChinese": "冰箱贴",
                            "children": {},
                        }
                    },
                }
            }
            if include_fallback:
                tree["600009"] = {
                    "cid": 600009,
                    "disabled": False,
                    "name": "Festive Decorations",
                    "nameChinese": "节庆装饰",
                    "children": {},
                }
            return {"result": "success", "data": {"cateTree": tree}}
        if path == CATEGORY_METADATA_PATH:
            return {
                "result": "success",
                "data": {
                    "categoryMetadata": {"categoryProductAttrList": []}
                },
            }
        raise AssertionError(path)

    return post, calls


def test_tiktok_category_resolver_uses_exact_frozen_semantics_and_official_tree():
    post, calls = _category_post()
    resolver = OfficialMiaoshouTikTokCategoryResolver(post=post)

    receipt = resolver.resolve(
        target={
            "target_label": "tiktok:LH_MY",
            "platform": "tiktok",
            "site": "LH_MY",
            "store": "LH_MY",
        },
        product=_tiktok_product(),
        skus=[{"model_sku": "0967"}],
    )

    assert receipt["category"] == {
        "id": "854536",
        "name": "Refrigerator Magnets",
        "path": [
            {"id": "800000", "name": "Home Decor"},
            {"id": "854536", "name": "Refrigerator Magnets"},
        ],
    }
    assert receipt["resolution"] == "EXACT"
    assert receipt["enabled"] is True
    assert receipt["metadata_valid"] is True
    assert str(receipt["evidence_digest"]).startswith("sha256:")
    assert calls == [
        (CATEGORY_TREE_PATH, {"site": "MY"}),
        (
            CATEGORY_METADATA_PATH,
            {"site": "MY", "cid": 854536, "shopIds": [13295169]},
        ),
    ]


def test_tiktok_category_resolver_does_not_guess_from_title():
    post, calls = _category_post()
    resolver = OfficialMiaoshouTikTokCategoryResolver(post=post)

    with pytest.raises(LivePublicationDependencyError, match="exact frozen"):
        resolver.resolve(
            target={
                "target_label": "tiktok:LH_MY",
                "platform": "tiktok",
                "site": "LH_MY",
                "store": "LH_MY",
            },
            product=_tiktok_product("Kitchen Gadget"),
            skus=[{"model_sku": "0967"}],
        )

    assert calls == []


def test_tiktok_category_resolver_uses_approved_festive_decoration_for_tablecloth():
    post, calls = _category_post(include_fallback=True)
    resolver = OfficialMiaoshouTikTokCategoryResolver(post=post)
    product = _tiktok_product("Tablecloth")

    receipt = resolver.resolve(
        target={
            "target_label": "tiktok:LH_PH",
            "platform": "tiktok",
            "site": "LH_PH",
            "store": "LH_PH",
        },
        product=product,
        skus=[{"model_sku": "0967"}],
    )

    assert receipt["category"]["id"] == "600009"
    assert receipt["resolution"] == "USER_APPROVED_FALLBACK"
    assert calls == [
        (CATEGORY_TREE_PATH, {"site": "PH"}),
        (
            CATEGORY_METADATA_PATH,
            {"site": "PH", "cid": 600009, "shopIds": [7676267]},
        ),
    ]


def test_tiktok_category_resolver_uses_approved_festive_decoration_for_placemats():
    post, calls = _category_post(include_fallback=True)
    resolver = OfficialMiaoshouTikTokCategoryResolver(post=post)

    receipt = resolver.resolve(
        target={
            "target_label": "tiktok:HB_PH",
            "platform": "tiktok",
            "site": "HB_PH",
            "store": "HB_PH",
        },
        product=_tiktok_product("餐具 > 餐垫、杯垫"),
        skus=[{"model_sku": "0968"}],
    )

    assert receipt["category"]["id"] == "600009"
    assert receipt["resolution"] == "USER_APPROVED_FALLBACK"
    assert calls == [
        (CATEGORY_TREE_PATH, {"site": "PH"}),
        (
            CATEGORY_METADATA_PATH,
            {"site": "PH", "cid": 600009, "shopIds": [15173238]},
        ),
    ]


def test_tiktok_category_resolver_uses_approved_decorative_sticker_for_exact_self_adhesive_wallpaper():
    calls = []

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == CATEGORY_TREE_PATH:
            return {
                "result": "success",
                "data": {
                    "cateTree": {
                        "600338": {
                            "cid": 600338,
                            "disabled": False,
                            "name": "Decorative Stickers",
                            "children": {},
                        }
                    }
                },
            }
        if path == CATEGORY_METADATA_PATH:
            return {
                "result": "success",
                "data": {"categoryMetadata": {"categoryProductAttrList": []}},
            }
        raise AssertionError(path)

    product = _tiktok_product("背景墙 > 墙纸、壁纸")
    product.update(
        title="Self-Adhesive PVC Wallpaper Roll, Elephant Botanical Pattern",
        description=(
            "This is a roll wallpaper made from PVC with a self-adhesive backing. "
            "It features an elephant botanical pattern for smooth walls and cabinets."
        ),
    )
    receipt = OfficialMiaoshouTikTokCategoryResolver(post=post).resolve(
        target={
            "target_label": "tiktok:LH_PH",
            "platform": "tiktok",
            "site": "LH_PH",
            "store": "LH_PH",
        },
        product=product,
        skus=[{"model_sku": "0972"}, {"model_sku": "0973"}],
    )

    assert receipt["category"]["id"] == "600338"
    assert receipt["resolution"] == "USER_APPROVED_FALLBACK"
    assert calls == [
        (CATEGORY_TREE_PATH, {"site": "PH"}),
        (
            CATEGORY_METADATA_PATH,
            {"site": "PH", "cid": 600338, "shopIds": [7676267]},
        ),
    ]


def test_tiktok_category_resolver_rejects_wallpaper_without_self_adhesive_fact_before_api():
    calls = []
    product = _tiktok_product("背景墙 > 墙纸、壁纸")
    product.update(
        title="PVC Wallpaper Roll",
        description="Wallpaper for walls and cabinets.",
    )

    with pytest.raises(LivePublicationDependencyError, match="self-adhesive"):
        OfficialMiaoshouTikTokCategoryResolver(
            post=lambda *args: calls.append(args)
        ).resolve(
            target={
                "target_label": "tiktok:LH_PH",
                "platform": "tiktok",
                "site": "LH_PH",
                "store": "LH_PH",
            },
            product=product,
            skus=[{"model_sku": "0972"}],
        )

    assert calls == []


def test_tiktok_unavailable_storefront_readback_is_truthful_processing_input():
    fact = TikTokUnavailableStorefrontReadback().readback(
        command={"target_label": "tiktok:GB"},
        dispatch={"target_label": "tiktok:GB", "outcome": "ACCEPTED"},
    )

    assert fact == {
        "target_label": "tiktok:GB",
        "authority": "UNAVAILABLE",
        "status": "UNAVAILABLE",
        "exact": False,
    }


class _ShopeeRuntime:
    def context(self, region: str) -> RegionContext:
        return RegionContext(
            region=region,
            shop_id=1001,
            merchant_id=2001,
            shop_token="shop-token",
            merchant_token="merchant-token",
        )


def _shopee_snapshot() -> dict:
    return {
        "schema_version": "approved-publication-snapshot/v4",
        "product": {
            "title": "Approved title",
            "description": "Approved description",
            "images": [
                "https://example.test/main-1.jpg",
                "https://example.test/main-2.jpg",
            ],
        },
        "publication_targets": [
            {
                "target_label": "shopee:MY",
                "platform": "shopee",
                "site": "MY",
                "store": "MY",
            }
        ],
        "skus": [
            {
                "model_sku": "0967",
                "specification": {"Variation": "Blue"},
                "variant_images": ["https://example.test/blue.jpg"],
                "parcel": {"weight_kg": "0.2", "package_cm": ["10", "8", "2"]},
                "prices": {
                    "shopee:MY": {
                        "amount": "7.10",
                        "currency": "MYR",
                        "global_original_price_cny": "10.00",
                    }
                },
            },
            {
                "model_sku": "0968",
                "specification": {"Variation": "Red"},
                "variant_images": ["https://example.test/red.jpg"],
                "parcel": {"weight_kg": "0.3", "package_cm": ["12", "7", "3"]},
                "prices": {
                    "shopee:MY": {
                        "amount": "9.20",
                        "currency": "MYR",
                        "global_original_price_cny": "12.00",
                    }
                },
            },
        ],
    }


def _shopee_request(snapshot: dict | None = None) -> PublicationPlatformRequest:
    return PublicationPlatformRequest(
        run_id="run-1",
        report_id="report-1",
        platform="SHOPEE",
        target_labels=("shopee:MY",),
        snapshot=snapshot or _shopee_snapshot(),
    )


def _official_shopee_get(path: str, _merchant_id: int, _token: str, body: dict):
    if path.endswith("get_global_item_info"):
        assert body == {"global_item_id_list": "987654"}
        return {
            "response": {
                "global_item_list": [
                    {
                        "global_item_id": 987654,
                        "global_item_name": "Approved title",
                        "description": "Approved description",
                        "global_item_status": "NORMAL",
                        "image": {
                            "image_url_list": ["provider://one", "provider://two"],
                            "image_id_list": ["image-one", "image-two"],
                        },
                        "weight": "0.3",
                        "dimension": {
                            "package_length": "12",
                            "package_width": "8",
                            "package_height": "3",
                        },
                    }
                ]
            }
        }
    if path.endswith("get_global_model_list"):
        assert body == {"global_item_id": 987654}
        return {
            "response": {
                "tier_variation": [
                    {
                        "name": "Variation",
                        "option_list": [
                            {"option": "Blue", "image": {"image_id": "img-blue"}},
                            {"option": "Red", "image": {"image_id": "img-red"}},
                        ],
                    }
                ],
                "global_model": [
                    {
                        "global_model_sku": "0967",
                        "global_model_id": 1111,
                        "tier_index": [0],
                        "price_info": {"original_price": "10.00"},
                    },
                    {
                        "global_model_sku": "0968",
                        "global_model_id": 2222,
                        "tier_index": [1],
                        "price_info": {"original_price": "12.00"},
                    },
                ],
            }
        }
    raise AssertionError(path)


def test_shopee_global_resolver_requires_exact_official_master_models_and_images():
    resolver = ShopeeExactGlobalItemResolver(
        runtime=_ShopeeRuntime(),
        mapping_lookup=lambda key: "987654" if key in {"0967", "0968"} else None,
        merchant_get_transport=_official_shopee_get,
    )

    assert resolver(_shopee_request()) == "987654"


def test_shopee_global_resolver_rejects_stale_deleted_identity_before_region_write():
    def deleted_get(path, merchant_id, token, body):
        response = _official_shopee_get(path, merchant_id, token, body)
        if path.endswith("get_global_item_info"):
            response["response"]["global_item_list"][0][
                "global_item_status"
            ] = "DELETED"
        return response

    resolver = ShopeeExactGlobalItemResolver(
        runtime=_ShopeeRuntime(),
        mapping_lookup=lambda _key: "987654",
        merchant_get_transport=deleted_get,
    )

    with pytest.raises(LivePublicationDependencyError, match="deleted"):
        resolver(_shopee_request())


def test_shopee_global_resolver_rejects_conflicting_local_mapping_without_api_call():
    calls = []
    resolver = ShopeeExactGlobalItemResolver(
        runtime=_ShopeeRuntime(),
        mapping_lookup=lambda key: {"0967": "111", "0968": "222"}[key],
        merchant_get_transport=lambda *args: calls.append(args),
    )

    with pytest.raises(LivePublicationDependencyError, match="ambiguous"):
        resolver(_shopee_request())

    assert calls == []


def test_shopee_global_resolver_requires_mapping_for_every_approved_sku():
    calls = []
    resolver = ShopeeExactGlobalItemResolver(
        runtime=_ShopeeRuntime(),
        mapping_lookup=lambda key: "987654" if key == "0967" else None,
        merchant_get_transport=lambda *args: calls.append(args),
    )

    with pytest.raises(LivePublicationDependencyError, match="missing"):
        resolver(_shopee_request())

    assert calls == []


@pytest.mark.parametrize("drift", ["price", "variant-image"])
def test_shopee_global_resolver_rejects_model_price_or_variant_image_drift(drift):
    def drifted_get(path, merchant_id, token, body):
        response = _official_shopee_get(path, merchant_id, token, body)
        if path.endswith("get_global_model_list"):
            if drift == "price":
                response["response"]["global_model"][1]["price_info"][
                    "original_price"
                ] = "99.00"
            else:
                response["response"]["tier_variation"][0]["option_list"][1][
                    "image"
                ] = {}
        return response

    resolver = ShopeeExactGlobalItemResolver(
        runtime=_ShopeeRuntime(),
        mapping_lookup=lambda _key: "987654",
        merchant_get_transport=drifted_get,
    )

    with pytest.raises(LivePublicationDependencyError):
        resolver(_shopee_request())


def _ozon_variant() -> dict:
    return {
        "schema_version": "ozon-approved-import-variant/v1",
        "target_label": "ozon:RU",
        "offer_id": "0967",
        "approved_seller_sku": "0967",
        "variant_key": "blue",
        "specification": {"Variation": "Blue"},
        "title": "Approved Ozon title",
        "description": "Approved Ozon description",
        "price": "100",
        "old_price": "120",
        "currency": "CNY",
        "parcel": {"weight_kg": "0.2", "package_cm": ["10", "8", "2"]},
        "images": ["https://example.test/blue.jpg"],
        "image_count": 1,
        "category": {
            "id": "17028913",
            "name": "Fridge Magnets",
            "path": [{"id": "17028913", "name": "Fridge Magnets"}],
        },
    }


def test_ozon_dispatch_requires_frozen_official_profile_before_network():
    calls = []
    transport = OfficialOzonV4Transport(post=lambda *args: calls.append(args))

    fact = transport.dispatch_variant(_ozon_variant())

    assert fact == OzonDispatchFact(
        outcome="PRE_SUBMIT_FAILED",
        provider_code="ozon_profile_unavailable",
        provider_reason="Ozon frozen profile cannot build an import item",
    )
    assert calls == []


def test_ozon_fridge_magnet_profile_requires_exact_official_tree_and_metadata():
    calls = []

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == "/v1/description-category/tree":
            return {
                "result": [
                    {
                        "description_category_id": 17027901,
                        "category_name": "House & Garden",
                        "disabled": False,
                        "children": [
                            {
                                "description_category_id": 17028743,
                                "category_name": "Souvenirs and Gifts",
                                "disabled": False,
                                "children": [
                                    {
                                        "type_id": 93785,
                                        "type_name": "Fridge Magnet",
                                        "disabled": False,
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        if path == "/v1/description-category/attribute":
            return {
                "result": [
                    {"id": 85, "name": "Brand", "is_required": True},
                    {"id": 9048, "name": "Model name", "is_required": True},
                    {"id": 8229, "name": "Type", "is_required": True},
                    {"id": 4191, "name": "Annotation", "is_required": False},
                ]
            }
        if path == "/v1/description-category/attribute/values/search":
            if body["attribute_id"] == 85:
                return {"result": [{"id": 126745801, "value": "No Brand"}]}
            if body["attribute_id"] == 8229:
                return {"result": [{"id": 93785, "value": "Fridge Magnet"}]}
        raise AssertionError((path, body))

    resolver = OfficialOzonFridgeMagnetProfileResolver(post=post)
    snapshot = {
        "schema_version": "approved-publication-snapshot/v4",
        "product": {
            "title": "Decorative Resin Fridge Magnet",
            "description": "Approved factual description",
            "main_category": {
                "id": "product-semantic:fridge-magnet",
                "name": "Home > Fridge Magnets",
            },
        },
    }

    profile = resolver(snapshot)

    assert profile["resolution"] == "EXACT"
    assert profile["description_category_id"] == 17028743
    assert profile["type_id"] == 93785
    assert profile["required_attributes"]["brand"] == {
        "attribute_id": 85,
        "dictionary_value_id": 126745801,
        "value": "No Brand",
    }
    assert [path for path, _body in calls] == [
        "/v1/description-category/tree",
        "/v1/description-category/attribute",
        "/v1/description-category/attribute/values/search",
        "/v1/description-category/attribute/values/search",
    ]


def test_ozon_profile_resolver_selects_exact_enabled_mug_coaster_profile():
    def post(path, body):
        if path == "/v1/description-category/tree":
            return {"result": [{"description_category_id": 17027494, "category_name": "House & Garden", "disabled": False, "children": [{"description_category_id": 17027926, "category_name": "Drinking Utensils & Accessories", "disabled": False, "children": [{"type_id": 96376, "type_name": "Mug Coaster", "disabled": False, "children": []}]}]}]}
        if path == "/v1/description-category/attribute":
            assert body["description_category_id"] == 17027926
            assert body["type_id"] == 96376
            return {"result": [{"id": 85, "is_required": True}, {"id": 9048, "is_required": True}, {"id": 8229, "is_required": True}]}
        if path == "/v1/description-category/attribute/values/search":
            return {"result": [{"id": 126745801, "value": "No Brand"}]} if body["attribute_id"] == 85 else {"result": [{"id": 96376, "value": "Mug Coaster"}]}
        raise AssertionError(path)

    profile = OfficialOzonFridgeMagnetProfileResolver(post=post)({
        "schema_version": "approved-publication-snapshot/v4",
        "product": {"main_category": {"name": "餐具 > 餐垫、杯垫"}},
    })

    assert (profile["description_category_id"], profile["type_id"]) == (17027926, 96376)


def test_ozon_profile_resolver_and_builder_accept_exact_wallpaper_profile():
    def post(path, body):
        if path == "/v1/description-category/tree":
            return {"result": [{"description_category_id": 17000001, "category_name": "Construction & Renovation", "disabled": False, "children": [{"description_category_id": 17028954, "category_name": "Wallpaper & Wall Coatings", "disabled": False, "children": [{"type_id": 95819, "type_name": "Wallpaper", "disabled": False, "children": []}]}]}]}
        if path == "/v1/description-category/attribute":
            assert body["description_category_id"] == 17028954
            assert body["type_id"] == 95819
            return {"result": [{"id": 85, "is_required": True}, {"id": 9048, "is_required": True}, {"id": 8229, "is_required": True}]}
        if path == "/v1/description-category/attribute/values/search":
            return {"result": [{"id": 126745801, "value": "No Brand"}]} if body["attribute_id"] == 85 else {"result": [{"id": 95819, "value": "Wallpaper"}]}
        raise AssertionError(path)

    snapshot = {
        "schema_version": "approved-publication-snapshot/v4",
        "product": {"main_category": {"name": "背景墙 > 墙纸、壁纸"}},
    }
    profile = OfficialOzonFridgeMagnetProfileResolver(post=post)(snapshot)
    assert (profile["description_category_id"], profile["type_id"]) == (17028954, 95819)
    variant = _ozon_variant()
    variant.update({
        "offer_id": "0969", "approved_seller_sku": "0969",
        "category": {"id": "17028954", "name": "Wallpaper & Wall Coatings", "path": []},
        "official_profile": profile,
    })
    item = build_ozon_import_item_from_frozen_variant(variant)
    attrs = {row["id"]: row for row in item["attributes"]}
    assert (item["description_category_id"], item["type_id"]) == (17028954, 95819)
    assert attrs[9048]["values"] == [{"dictionary_value_id": 0, "value": "0969-wallpaper"}]
    assert attrs[8229]["values"] == [{"dictionary_value_id": 95819, "value": "Wallpaper"}]


def test_ozon_import_builder_uses_only_frozen_variant_and_official_profile():
    variant = _ozon_variant()
    variant["description"] = "Exact approved Ozon description"
    variant["category"] = {"id": "17028743", "name": "Souvenirs and Gifts", "path": []}
    variant["official_profile"] = {
        "schema_version": "ozon-official-profile-resolution/v1",
        "resolution": "EXACT",
        "description_category_id": 17028743,
        "category_name": "Souvenirs and Gifts",
        "category_path": [
            {"id": "17027901", "name": "House & Garden"},
            {"id": "17028743", "name": "Souvenirs and Gifts"},
        ],
        "type_id": 93785,
        "type_name": "Fridge Magnet",
        "required_attributes": {
            "brand": {
                "attribute_id": 85,
                "dictionary_value_id": 126745801,
                "value": "No Brand",
            },
            "model_name": {"attribute_id": 9048},
            "product_type": {
                "attribute_id": 8229,
                "dictionary_value_id": 93785,
                "value": "Fridge Magnet",
            },
        },
    }

    item = build_ozon_import_item_from_frozen_variant(variant)

    assert item["offer_id"] == "0967"
    assert item["description_category_id"] == 17028743
    assert item["type_id"] == 93785
    assert item["name"] == variant["title"]
    assert item["price"] == "100"
    assert item["old_price"] == "120"
    assert item["images"] == variant["images"]
    assert item["weight"] == 200
    assert item["weight_unit"] == "g"
    assert item["depth"] == 100
    assert item["width"] == 80
    assert item["height"] == 20
    assert item["dimension_unit"] == "mm"
    attrs = {row["id"]: row for row in item["attributes"]}
    assert attrs[85]["values"] == [
        {"dictionary_value_id": 126745801, "value": "No Brand"}
    ]
    assert attrs[9048]["values"] == [
        {"dictionary_value_id": 0, "value": "0967-fridge-magnet"}
    ]
    assert attrs[8229]["values"] == [
        {"dictionary_value_id": 93785, "value": "Fridge Magnet"}
    ]
    assert attrs[4191]["values"] == [
        {"dictionary_value_id": 0, "value": "Exact approved Ozon description"}
    ]


def test_0968_mug_coaster_profile_builds_exact_frozen_payload_before_fake_post():
    variant = _ozon_variant()
    variant.update({"offer_id": "0968", "approved_seller_sku": "0968", "title": "Approved coaster title", "description": "Approved coaster description", "images": [f"https://example.test/coaster-{index}.jpg" for index in range(1, 8)], "parcel": {"weight_kg": "0.1", "package_cm": ["15", "15", "0.8"]}, "price": "76.58", "old_price": "90", "category": {"id": "17027926", "name": "Drinking Utensils & Accessories", "path": []}, "official_profile": {"schema_version": "ozon-official-profile-resolution/v1", "resolution": "EXACT", "description_category_id": 17027926, "type_id": 96376, "required_attributes": {"brand": {"attribute_id": 85, "dictionary_value_id": 126745801, "value": "No Brand"}, "model_name": {"attribute_id": 9048}, "product_type": {"attribute_id": 8229, "dictionary_value_id": 96376, "value": "Mug Coaster"}}}})
    calls = []
    fact = OfficialOzonV4Transport(post=lambda path, body: calls.append((path, deepcopy(body))) or {"result": {"task_id": 444}}).dispatch_variant(variant)
    assert fact == OzonDispatchFact(outcome="ACCEPTED", task_id="444")
    item = calls[0][1]["items"][0]
    assert calls[0][0] == "/v3/product/import"
    assert (item["description_category_id"], item["type_id"], item["weight"], item["depth"], item["width"], item["height"]) == (17027926, 96376, 100, 150, 150, 8)
    assert item["images"] == variant["images"]
    assert {row["id"]: row["values"][0]["value"] for row in item["attributes"]}[9048] == "0968-mug-coaster"


def test_ozon_http_400_is_a_definite_rejection_not_unknown_write():
    variant = _ozon_variant()
    variant["description"] = "Exact approved Ozon description"
    variant["category"] = {
        "id": "17028743",
        "name": "Souvenirs and Gifts",
        "path": [
            {"id": "17027901", "name": "House & Garden"},
            {"id": "17028743", "name": "Souvenirs and Gifts"},
        ],
    }
    variant["official_profile"] = {
        "schema_version": "ozon-official-profile-resolution/v1",
        "resolution": "EXACT",
        "description_category_id": 17028743,
        "category_name": "Souvenirs and Gifts",
        "category_path": [
            {"id": "17027901", "name": "House & Garden"},
            {"id": "17028743", "name": "Souvenirs and Gifts"},
        ],
        "type_id": 93785,
        "type_name": "Fridge Magnet",
        "required_attributes": {
            "brand": {
                "attribute_id": 85,
                "dictionary_value_id": 126745801,
                "value": "No Brand",
            },
            "model_name": {"attribute_id": 9048},
            "product_type": {
                "attribute_id": 8229,
                "dictionary_value_id": 93785,
                "value": "Fridge Magnet",
            },
        },
    }
    transport = OfficialOzonV4Transport(
        post=lambda *_args: (_ for _ in ()).throw(
            RuntimeError('Ozon HTTP 400: invalid value for int32 field weight')
        )
    )

    fact = transport.dispatch_variant(variant)

    assert fact == OzonDispatchFact(
        outcome="REJECTED",
        provider_code="ozon_business_rejected",
        provider_reason="Ozon import was rejected",
    )


def test_ozon_business_rejection_response_is_sanitized_without_raw_message():
    variant = _ozon_variant()
    transport = OfficialOzonV4Transport(
        post=lambda *_args: {"message": "attribute rejected Authorization Bearer SECRET"},
        import_item_builder=lambda _variant: {
            "offer_id": variant["offer_id"],
            "description_category_id": int(variant["category"]["id"]),
            "type_id": 999, "name": variant["title"], "price": variant["price"],
            "old_price": variant["old_price"], "currency_code": variant["currency"],
            "images": variant["images"], "weight": variant["parcel"]["weight_kg"],
            "weight_unit": "kg", "dimension_unit": "cm",
            "depth": variant["parcel"]["package_cm"][0], "width": variant["parcel"]["package_cm"][1],
            "height": variant["parcel"]["package_cm"][2],
            "attributes": [{"id": 1, "values": [{"value": "exact"}]}],
        },
    )
    fact = transport.dispatch_variant(variant)
    assert fact == OzonDispatchFact(
        outcome="REJECTED",
        provider_code="ozon_attribute_rejected",
        provider_reason="Ozon rejected an approved attribute value",
    )
    assert "SECRET" not in str(fact)


def test_ozon_dispatch_uses_injected_exact_builder_and_official_import():
    calls = []

    def post(path, body):
        calls.append((path, deepcopy(body)))
        return {"result": {"task_id": 444}}

    transport = OfficialOzonV4Transport(
        post=post,
        import_item_builder=lambda variant: {
            "offer_id": variant["offer_id"],
            "description_category_id": int(variant["category"]["id"]),
            "type_id": 999,
            "name": variant["title"],
            "price": variant["price"],
            "old_price": variant["old_price"],
            "currency_code": variant["currency"],
            "images": variant["images"],
            "weight": variant["parcel"]["weight_kg"],
            "weight_unit": "kg",
            "dimension_unit": "cm",
            "depth": variant["parcel"]["package_cm"][0],
            "width": variant["parcel"]["package_cm"][1],
            "height": variant["parcel"]["package_cm"][2],
            "attributes": [{"id": 1, "values": [{"value": "exact"}]}],
        },
    )

    fact = transport.dispatch_variant(_ozon_variant())

    assert fact == OzonDispatchFact(outcome="ACCEPTED", task_id="444")
    assert calls == [
        (
            "/v3/product/import",
            {
                "items": [
                    {
                        "offer_id": "0967",
                        "description_category_id": 17028913,
                        "type_id": 999,
                        "name": "Approved Ozon title",
                        "price": "100",
                        "old_price": "120",
                        "currency_code": "CNY",
                        "images": ["https://example.test/blue.jpg"],
                        "weight": "0.2",
                        "weight_unit": "kg",
                        "dimension_unit": "cm",
                        "depth": "10",
                        "width": "8",
                        "height": "2",
                        "attributes": [
                            {"id": 1, "values": [{"value": "exact"}]}
                        ],
                    }
                ]
            },
        )
    ]


def test_ozon_readback_normalizes_authoritative_id_statuses_and_parcel():
    calls = []

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == "/v3/product/info/list":
            return {"items": [
                {
                    "offer_id": "0967",
                    "id": 7654321,
                    "product_id": 9999999,
                    "name": "Approved Ozon title",
                    "price": "100.00",
                    "old_price": "120.00",
                    "images": ["provider://image"],
                    "description_category_id": 17028913,
                    "weight": 200,
                    "weight_unit": "g",
                    "depth": 100,
                    "width": 80,
                    "height": 20,
                    "dimension_unit": "mm",
                    "statuses": {
                        "is_created": True,
                        "status": "CREATED",
                        "status_failed": "",
                    },
                }
            ]}
        if path == "/v4/product/info/attributes":
            return {"result": [{
                "offer_id": "0967",
                "id": 7654321,
                "type_id": 999,
                "weight": 200,
                "weight_unit": "g",
                "depth": 100,
                "width": 80,
                "height": 20,
                "dimension_unit": "mm",
                "attributes": [],
            }]}
        if path == "/v1/product/info/description":
            return {"result": {
                "offer_id": "0967",
                "id": 7654321,
                "description": "Approved Ozon description",
            }}
        raise AssertionError((path, body))

    rows = OfficialOzonV4Transport(post=post).readback_variants(("0967",))

    assert rows == [
        {
            "offer_id": "0967",
            "id": 7654321,
            "statuses": {
                "is_created": True,
                "status": "CREATED",
                "status_failed": "",
            },
            "name": "Approved Ozon title",
            "description": "Approved Ozon description",
            "price": "100.00",
            "old_price": "120.00",
            "images": ["provider://image"],
            "category_id": "17028913",
            "type_id": "999",
            "weight_kg": "0.2",
            "package_cm": ["10", "8", "2"],
            "attributes": {},
        }
    ]
    assert calls == [
        (
            "/v3/product/info/list",
            {"offer_id": ["0967"], "limit": 1000, "visibility": "ALL"},
        ),
        (
            "/v4/product/info/attributes",
            {
                "filter": {"offer_id": ["0967"], "visibility": "ALL"},
                "limit": 1000,
            },
        ),
        ("/v1/product/info/description", {"offer_id": "0967"}),
    ]


def test_ozon_readback_combines_color_image_and_uses_attribute_parcel():
    approved_gallery = "https://provider.example/gallery.jpg"
    approved_color = "https://provider.example/color.jpg"

    def post(path, body):
        if path == "/v3/product/info/list":
            return {
                "items": [
                    {
                        "offer_id": "0967",
                        "id": 5906709656,
                        "name": "Русское название",
                        "price": "40.00",
                        "old_price": "52.00",
                        "images": [approved_gallery],
                        "color_image": [approved_color],
                        "description_category_id": 17028743,
                        "type_id": 93785,
                        "statuses": {
                            "is_created": True,
                            "status": "PRICE_SENT",
                            "status_failed": "",
                        },
                    }
                ]
            }
        if path == "/v4/product/info/attributes":
            return {
                "result": [
                    {
                        "offer_id": "0967",
                        "id": 5906709656,
                        "weight": 100,
                        "weight_unit": "g",
                        "depth": 100,
                        "width": 100,
                        "height": 20,
                        "dimension_unit": "mm",
                        "attributes": [
                            {
                                "id": 85,
                                "values": [
                                    {
                                        "dictionary_value_id": 126745801,
                                        "value": "No Brand",
                                    }
                                ],
                            },
                            {
                                "id": 9048,
                                "values": [
                                    {
                                        "dictionary_value_id": 0,
                                        "value": "0967-fridge-magnet",
                                    }
                                ],
                            },
                        ],
                    }
                ]
            }
        if path == "/v1/product/info/description":
            return {
                "result": {
                    "offer_id": "0967",
                    "id": 5906709656,
                    "description": "Точное русское описание",
                }
            }
        raise AssertionError((path, body))

    rows = OfficialOzonV4Transport(post=post).readback_variants(("0967",))

    assert rows == [
        {
            "offer_id": "0967",
            "id": 5906709656,
            "statuses": {
                "is_created": True,
                "status": "PRICE_SENT",
                "status_failed": "",
            },
            "name": "Русское название",
            "description": "Точное русское описание",
            "price": "40.00",
            "old_price": "52.00",
            "images": [approved_gallery, approved_color],
            "category_id": "17028743",
            "type_id": "93785",
            "weight_kg": "0.1",
            "package_cm": ["10", "10", "2"],
            "attributes": {
                "85": [
                    {"dictionary_value_id": 126745801, "value": "No Brand"}
                ],
                "9048": [
                    {
                        "dictionary_value_id": 0,
                        "value": "0967-fridge-magnet",
                    }
                ],
            },
        }
    ]


def test_live_dependency_builders_are_independent():
    publisher = SimpleNamespace(preflight=lambda _value: {}, publish=lambda _value: {})
    tiktok = build_live_tiktok_dependencies(
        collectbox_context_resolver=lambda _request: {}, publisher=publisher
    )
    shopee = build_live_shopee_dependencies(
        resolver=ShopeeExactGlobalItemResolver(
            runtime=_ShopeeRuntime(),
            mapping_lookup=lambda _key: None,
            merchant_get_transport=_official_shopee_get,
        ),
        runtime=_ShopeeRuntime(),
    )
    ozon = build_live_ozon_dependencies(
        transport=OfficialOzonV4Transport(post=lambda *_args: {})
    )

    assert tiktok.collectbox_context_resolver is not shopee.global_item_id_resolver
    assert shopee.runtime is not ozon.dispatch_variant
    assert callable(ozon.readback_variants)


def test_stored_ozon_localized_copy_uses_exact_offer_revision_path(tmp_path: Path):
    receipt_dir = tmp_path / "3882722296" / "40"
    receipt_dir.mkdir(parents=True)
    receipt = {
        "schema_version": "ozon-localized-copy/v1",
        "source_snapshot_digest": "sha256:" + "a" * 64,
        "language": "ru",
        "title": "Русское название 7 на 7 см",
        "description": "Точное русское описание товара.",
    }
    (receipt_dir / "ozon-localized-copy.json").write_text(
        json.dumps(receipt, ensure_ascii=False), encoding="utf-8"
    )
    resolver = StoredOzonLocalizedCopyResolver(tmp_path)

    assert resolver(
        {
            "offer_id": "3882722296",
            "product_revision": 40,
            "snapshot_digest": "sha256:" + "a" * 64,
        }
    ) == receipt

    with pytest.raises(LivePublicationDependencyError, match="identity"):
        resolver(
            {
                "offer_id": "3882722296",
                "product_revision": 40,
                "snapshot_digest": "sha256:" + "b" * 64,
            }
        )


class _ObservedDraftTransport:
    def __init__(self, observer, calls, *, unknown_claim: bool = False):
        self.observer = observer
        self.calls = calls
        self.unknown_claim = unknown_claim

    def claim_or_create(self, *, target, ordinal):
        self.calls.append(("claim", target["target_label"], ordinal))
        if self.unknown_claim:
            fact = DraftWriteFact("CLAIM_OR_CREATE", "UNKNOWN")
        else:
            fact = DraftWriteFact(
                "CLAIM_OR_CREATE",
                "ACCEPTED",
                detail_id=str(7001 + ordinal),
                shop_id=str(target["shop_id"]),
            )
        self.observer(target["target_label"], fact)
        return fact

    def save_draft(self, *, identity, draft):
        self.calls.append(("save", identity["target_label"], draft["target_label"]))
        fact = DraftWriteFact(
            "SAVE_DRAFT",
            "ACCEPTED",
            detail_id=identity["detail_id"],
            shop_id=identity["shop_id"],
        )
        self.observer(identity["target_label"], fact)
        return fact

    def prepare_save_draft(self, *, identity, draft):
        self.calls.append(("prepare_save", identity["target_label"], draft["target_label"]))
        return {"identity": deepcopy(identity), "draft": deepcopy(draft)}

    def save_prepared_draft(self, *, identity, prepared):
        return self.save_draft(identity=identity, draft=prepared["draft"])


def _tiktok_request() -> PublicationPlatformRequest:
    snapshot = _snapshot()
    labels = tuple(
        row["target_label"]
        for row in snapshot["publication_targets"]
        if row["platform"] == "tiktok"
    )
    return PublicationPlatformRequest(
        run_id="run-checkpoint-1",
        report_id="publication-report:run-checkpoint-1",
        platform="TIKTOK",
        target_labels=labels,
        snapshot=snapshot,
    )


def test_tiktok_preparation_checkpoint_survives_publish_preflight_failure(tmp_path):
    calls = []
    store = TikTokV4DraftCheckpointStore(tmp_path)
    preparer = DurableTikTokV4DraftPreparer(
        checkpoint_store=store,
        category_resolver=CategoryResolver(),
        transport_factory=lambda _request, observer: _ObservedDraftTransport(
            observer, calls
        ),
    )
    request = _tiktok_request()
    publisher = SimpleNamespace(
        preflight=lambda _snapshot: (_ for _ in ()).throw(
            RuntimeError("simulated preflight failure")
        ),
        publish=lambda *_args: pytest.fail("publish must not run"),
    )

    result = build_tiktok_v4_executor(
        collectbox_context_resolver=None,
        draft_preparer=preparer,
        category_resolver=CategoryResolver(),
        publisher=publisher,
        storefront_readback=TikTokUnavailableStorefrontReadback(),
    )(request)

    assert result["external_write_count"] == 4
    assert result["dispatch_attempted"] is False
    checkpoint = store.load(request)
    assert checkpoint["external_write_count"] == 4
    assert set(checkpoint["receipt"]["collectbox_contexts"]) == set(
        request.target_labels
    )


def test_tiktok_production_transport_factory_requires_digest_bound_seed_identity():
    request = _tiktok_request()
    body = {
        "schema_version": "miaoshou-tiktok-v4-seed-identity/v2",
        "snapshot_digest": request.snapshot["snapshot_digest"],
        "common_detail_id": "5001",
        "initial_platform_detail_id": "7101",
        "platform_detail_ids_by_target": {},
    }
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    identity = {
        **body,
        "identity_digest": "sha256:"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
    observed = []
    calls = []
    factory = MiaoshouTikTokV4DraftTransportFactory(
        seed_identity_resolver=lambda _request: identity,
        post=lambda path, payload: (
            calls.append((path, deepcopy(payload)))
            or {"result": "success", "data": {}}
        ),
    )
    transport = factory(
        request,
        lambda label, fact: observed.append((label, fact)),
    )

    facts = transport.claim_or_create(
        target={
            "target_label": "tiktok:LH_PH",
            "shop_id": "7676267",
        },
        ordinal=0,
    )

    assert len(facts) == 1
    assert facts[0].operation == "CLAIM_TO_SHOP"
    assert facts[0].outcome == "ACCEPTED"
    assert observed == [("tiktok:LH_PH", facts[0])]
    assert calls[0][0].endswith("claim_to_shop")

    invalid = dict(identity, identity_digest="sha256:" + "0" * 64)
    with pytest.raises(LivePublicationDependencyError):
        MiaoshouTikTokV4DraftTransportFactory(
            seed_identity_resolver=lambda _request: invalid,
            post=lambda *_args: pytest.fail("drifted seed must not call provider"),
        )(request, lambda *_args: None)


def _seed_list_response(rows):
    return {
        "result": "success",
        "data": {
            "detailList": deepcopy(rows),
            "totalCount": len(rows),
            "hasNextPage": False,
        },
    }


def _real_miaoshou_seed_list_response(rows):
    """Mirror the production Open API envelope observed on 2026-08-10."""

    return {
        "code": "success",
        "message": "",
        "data": {
            "detailList": deepcopy(rows),
            "totalCount": len(rows),
            "hasNextPage": False,
        },
    }


def _seed_source_offer_id(request: PublicationPlatformRequest) -> str:
    return request.snapshot["product"]["source_identity"]["source_offer_id"]


def _common_seed_row(source_offer_id: str, common_detail_id: str = "3882722296"):
    return {
        "commonCollectBoxDetailId": common_detail_id,
        "sourceList": [{"sourceItemId": source_offer_id}],
    }


def _platform_seed_row(
    source_offer_id: str,
    detail_id: str,
    created_at: str,
    *,
    common_detail_id: str = "3882722296",
):
    return {
        "collectBoxDetailId": detail_id,
        "commonCollectBoxDetailId": common_detail_id,
        "sourceList": [{"sourceItemId": source_offer_id}],
        "gmtCreate": created_at,
        "collectBoxDetailShopList": [],
    }


def test_tiktok_seed_identity_selects_latest_exact_unclaimed_platform_detail():
    request = _tiktok_request()
    source_offer_id = _seed_source_offer_id(request)
    calls = []

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == MIAOSHOU_COMMON_LIST_PATH:
            return _seed_list_response([_common_seed_row(source_offer_id)])
        if path == MIAOSHOU_TIKTOK_LIST_PATH:
            return _seed_list_response(
                [
                    _platform_seed_row(
                        source_offer_id,
                        "3271694633",
                        "2026-08-10 10:00:00",
                    ),
                    _platform_seed_row(
                        source_offer_id,
                        "3272335044",
                        "2026-08-10 11:00:00",
                    ),
                ]
            )
        raise AssertionError(path)

    identity = OfficialMiaoshouTikTokV4SeedIdentityResolver(post=post)(request)

    body = {
        "schema_version": "miaoshou-tiktok-v4-seed-identity/v2",
        "snapshot_digest": request.snapshot["snapshot_digest"],
        "common_detail_id": "3882722296",
        "initial_platform_detail_id": "3272335044",
        "platform_detail_ids_by_target": {},
    }
    assert identity == {
        **body,
        "identity_digest": "sha256:"
        + hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    assert calls == [
        (
            MIAOSHOU_COMMON_LIST_PATH,
            {
                "pageNo": 1,
                "pageSize": 100,
                "filter": {
                    "tabPaneName": "all",
                    "sourceItemIdKeyword": source_offer_id,
                },
            },
        ),
        (
            MIAOSHOU_TIKTOK_LIST_PATH,
            {
                "pageNo": 1,
                "pageSize": 100,
                "filter": {"sourceItemIdKeyword": source_offer_id},
            },
        ),
    ]


def test_tiktok_category_resolver_uses_exact_leaf_from_frozen_breadcrumb_name():
    post, calls = _category_post()
    resolver = OfficialMiaoshouTikTokCategoryResolver(post=post)
    product = _tiktok_product("居家日用 > 冰箱贴")
    product["main_category"]["path"] = []

    receipt = resolver.resolve(
        target={
            "target_label": "tiktok:LH_MY",
            "platform": "tiktok",
            "site": "LH_MY",
            "store": "LH_MY",
        },
        product=product,
        skus=[{"model_sku": "0967"}],
    )

    assert receipt["category"]["id"] == "854536"
    assert receipt["resolution"] == "EXACT"
    assert calls[0] == (CATEGORY_TREE_PATH, {"site": "MY"})


def test_tiktok_seed_identity_accepts_real_open_api_success_envelope():
    request = _tiktok_request()
    source_offer_id = _seed_source_offer_id(request)

    def post(path, _body):
        if path == MIAOSHOU_COMMON_LIST_PATH:
            return _real_miaoshou_seed_list_response(
                [_common_seed_row(source_offer_id)]
            )
        if path == MIAOSHOU_TIKTOK_LIST_PATH:
            return _real_miaoshou_seed_list_response(
                [
                    _platform_seed_row(
                        source_offer_id,
                        "3272335044",
                        "2026-08-10 11:00:00",
                    )
                ]
            )
        raise AssertionError(path)

    identity = OfficialMiaoshouTikTokV4SeedIdentityResolver(post=post)(request)

    assert identity["common_detail_id"] == "3882722296"
    assert identity["initial_platform_detail_id"] == "3272335044"


def test_tiktok_seed_identity_joins_real_platform_row_through_exact_common_id():
    request = _tiktok_request()
    source_offer_id = _seed_source_offer_id(request)
    platform_row = _platform_seed_row(
        source_offer_id,
        "3272335044",
        "2026-08-10 11:00:00",
    )
    platform_row.pop("sourceList")

    def post(path, _body):
        if path == MIAOSHOU_COMMON_LIST_PATH:
            return _real_miaoshou_seed_list_response(
                [_common_seed_row(source_offer_id)]
            )
        if path == MIAOSHOU_TIKTOK_LIST_PATH:
            return _real_miaoshou_seed_list_response([platform_row])
        raise AssertionError(path)

    identity = OfficialMiaoshouTikTokV4SeedIdentityResolver(post=post)(request)

    assert identity["common_detail_id"] == "3882722296"
    assert identity["initial_platform_detail_id"] == "3272335044"


@pytest.mark.parametrize(
    ("common_rows", "platform_rows", "message"),
    [
        (
            "duplicate-common",
            "valid-platform",
            "COMMON identity is unavailable or ambiguous",
        ),
        ("valid-common", "common-drift", "platform COMMON identity conflicts"),
        ("valid-common", "source-drift", "platform source identity conflicts"),
    ],
)
def test_tiktok_seed_identity_fails_closed_on_common_or_source_ambiguity(
    common_rows,
    platform_rows,
    message,
):
    request = _tiktok_request()
    source_offer_id = _seed_source_offer_id(request)
    exact_common = [_common_seed_row(source_offer_id)]
    exact_platform = [
        _platform_seed_row(
            source_offer_id,
            "3272335044",
            "2026-08-10 11:00:00",
        )
    ]
    common_fixture = {
        "valid-common": exact_common,
        "duplicate-common": [
            *exact_common,
            _common_seed_row(source_offer_id, "3882722297"),
        ],
    }[common_rows]
    platform_fixture = {
        "valid-platform": exact_platform,
        "common-drift": [
            _platform_seed_row(
                source_offer_id,
                "3272335044",
                "2026-08-10 11:00:00",
                common_detail_id="3882722297",
            )
        ],
        "source-drift": [
            _platform_seed_row(
                "999999999999",
                "3272335044",
                "2026-08-10 11:00:00",
            )
        ],
    }[platform_rows]

    def post(path, _body):
        if path == MIAOSHOU_COMMON_LIST_PATH:
            return _seed_list_response(common_fixture)
        if path == MIAOSHOU_TIKTOK_LIST_PATH:
            return _seed_list_response(platform_fixture)
        raise AssertionError(path)

    with pytest.raises(LivePublicationDependencyError, match=message):
        OfficialMiaoshouTikTokV4SeedIdentityResolver(post=post)(request)


def test_tiktok_seed_identity_returns_null_only_when_no_platform_detail_exists():
    request = _tiktok_request()
    source_offer_id = _seed_source_offer_id(request)

    def post(path, _body):
        if path == MIAOSHOU_COMMON_LIST_PATH:
            return _seed_list_response([_common_seed_row(source_offer_id)])
        if path == MIAOSHOU_TIKTOK_LIST_PATH:
            return _seed_list_response([])
        raise AssertionError(path)

    identity = OfficialMiaoshouTikTokV4SeedIdentityResolver(post=post)(request)

    assert identity["common_detail_id"] == "3882722296"
    assert identity["initial_platform_detail_id"] is None


def test_tiktok_seed_identity_requires_reconciliation_for_duplicate_claimed_target():
    request = _tiktok_request()
    source_offer_id = _seed_source_offer_id(request)
    claimed = [
        {
            **_platform_seed_row(
                source_offer_id,
                detail_id,
                created_at,
            ),
            "collectBoxDetailShopList": [{"shopId": shop_id}],
        }
        for detail_id, created_at, shop_id in (
            ("3271694633", "2026-08-10 10:00:00", "7676267"),
            ("3272335044", "2026-08-10 11:00:00", "7676267"),
        )
    ]

    def post(path, _body):
        if path == MIAOSHOU_COMMON_LIST_PATH:
            return _seed_list_response([_common_seed_row(source_offer_id)])
        if path == MIAOSHOU_TIKTOK_LIST_PATH:
            return _seed_list_response(claimed)
        raise AssertionError(path)

    with pytest.raises(LivePublicationDependencyError, match="identity is ambiguous"):
        OfficialMiaoshouTikTokV4SeedIdentityResolver(post=post)(request)


def test_tiktok_seed_identity_reuses_unique_claimed_rows_by_exact_target_shop():
    request = _tiktok_request()
    source_offer_id = _seed_source_offer_id(request)
    claimed = [
        {
            **_platform_seed_row(source_offer_id, detail_id, created_at),
            "collectBoxDetailShopList": [{"shopId": shop_id}],
        }
        for detail_id, created_at, shop_id in (
            ("3271694633", "2026-08-10 10:00:00", "7676267"),
            ("3272335044", "2026-08-10 11:00:00", "13295169"),
        )
    ]

    def post(path, _body):
        if path == MIAOSHOU_COMMON_LIST_PATH:
            return _seed_list_response([_common_seed_row(source_offer_id)])
        if path == MIAOSHOU_TIKTOK_LIST_PATH:
            return _seed_list_response(claimed)
        raise AssertionError(path)

    identity = OfficialMiaoshouTikTokV4SeedIdentityResolver(post=post)(request)

    assert identity["platform_detail_ids_by_target"] == {
        "tiktok:LH_MY": "3272335044",
        "tiktok:LH_PH": "3271694633",
    }
    assert identity["initial_platform_detail_id"] is None


def test_tiktok_draft_factory_wires_official_seed_resolver_without_writing():
    request = _tiktok_request()
    source_offer_id = _seed_source_offer_id(request)
    calls = []

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == MIAOSHOU_COMMON_LIST_PATH:
            return _seed_list_response([_common_seed_row(source_offer_id)])
        if path == MIAOSHOU_TIKTOK_LIST_PATH:
            return _seed_list_response(
                [
                    _platform_seed_row(
                        source_offer_id,
                        "3272335044",
                        "2026-08-10 11:00:00",
                    )
                ]
            )
        raise AssertionError("factory construction must remain read-only")

    transport = MiaoshouTikTokV4DraftTransportFactory(post=post)(
        request,
        lambda *_args: None,
    )

    assert transport is not None
    assert [path for path, _body in calls] == [
        MIAOSHOU_COMMON_LIST_PATH,
        MIAOSHOU_TIKTOK_LIST_PATH,
    ]


def test_tiktok_preparation_retry_reuses_checkpoint_without_blind_claim(tmp_path):
    calls = []
    store = TikTokV4DraftCheckpointStore(tmp_path)
    preparer = DurableTikTokV4DraftPreparer(
        checkpoint_store=store,
        category_resolver=CategoryResolver(),
        transport_factory=lambda _request, observer: _ObservedDraftTransport(
            observer, calls
        ),
    )
    request = _tiktok_request()

    first = preparer(request)
    calls_after_first = list(calls)
    second = preparer(request)

    assert first["external_write_count"] == 4
    assert second["external_write_count"] == 4
    assert calls == calls_after_first
    assert first["collectbox_contexts"] == second["collectbox_contexts"]


def test_tiktok_unknown_claim_checkpoint_prevents_blind_retry(tmp_path):
    calls = []
    store = TikTokV4DraftCheckpointStore(tmp_path)
    preparer = DurableTikTokV4DraftPreparer(
        checkpoint_store=store,
        category_resolver=CategoryResolver(),
        transport_factory=lambda _request, observer: _ObservedDraftTransport(
            observer, calls, unknown_claim=True
        ),
    )
    request = _tiktok_request()

    first = preparer(request)
    calls_after_first = list(calls)
    second = preparer(request)

    assert first["external_write_count"] is None
    assert second["external_write_count"] is None
    assert calls == calls_after_first
