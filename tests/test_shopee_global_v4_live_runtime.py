from modules.shopee.global_v4_live_runtime import (
    OfficialShopeeGlobalV4Runtime,
    ShopeeGlobalV4LiveRuntimeError,
    select_exact_official_category,
)


def test_exact_main_category_selects_fridge_magnets_and_ignores_other_candidates():
    selected = select_exact_official_category(
        {"id": "product-semantic:x", "name": "居家日用 > 冰箱贴"},
        [
            {
                "id": "101398",
                "name": "Fridge Magnets",
                "path": [
                    {"id": "100", "name": "Hobbies & Collections"},
                    {"id": "101", "name": "Souvenirs"},
                    {"id": "101398", "name": "Fridge Magnets"},
                ],
                "publishable": True,
            },
            {
                "id": "100209",
                "name": "Refrigerators",
                "path": [
                    {"id": "10", "name": "Home Appliances"},
                    {"id": "100209", "name": "Refrigerators"},
                ],
                "publishable": True,
            },
            {
                "id": "100636",
                "name": "Home Decor",
                "path": [{"id": "100636", "name": "Home Decor"}],
                "publishable": False,
            },
        ],
    )

    assert selected["id"] == "101398"
    assert selected["name"] == "Fridge Magnets"


def test_unrelated_recommendations_never_fall_back_to_title_guessing():
    try:
        select_exact_official_category(
            {"id": "product-semantic:x", "name": "居家日用 > 冰箱贴"},
            [
                {
                    "id": "100209",
                    "name": "Refrigerators",
                    "path": [{"id": "100209", "name": "Refrigerators"}],
                    "publishable": True,
                }
            ],
        )
    except ShopeeGlobalV4LiveRuntimeError as error:
        assert "exact semantic category" in str(error)
    else:
        raise AssertionError("an unrelated category must not be selected")


def test_prepare_creation_returns_only_the_exact_official_leaf():
    observed = {
        "authority": "SHOPEE_OFFICIAL",
        "candidates": [
            {
                "id": "101398",
                "name": "Fridge Magnets",
                "path": [
                    {"id": "100", "name": "Hobbies & Collections"},
                    {"id": "101", "name": "Souvenirs"},
                    {"id": "101398", "name": "Fridge Magnets"},
                ],
                "publishable": True,
                "required_attributes": [],
                "missing_required_attributes": [],
            },
            {
                "id": "100209",
                "name": "Refrigerators",
                "path": [{"id": "100209", "name": "Refrigerators"}],
                "publishable": True,
            },
        ],
        "brand": {"brand_id": 0, "original_brand_name": "NoBrand"},
        "warehouse": {"location_id": "CNZ", "display_name": "中国仓库"},
    }
    runtime = OfficialShopeeGlobalV4Runtime(
        context_resolver=lambda _command: {
            "merchant_id": 4970102,
            "merchant_token": "redacted",
            "shop_id": 1,
            "shop_token": "redacted",
        },
        official_fact_reader=lambda _command, _context: observed,
        mapping_lookup=lambda _sku: None,
    )
    command = {
        "main_category": {
            "id": "product-semantic:x",
            "name": "居家日用 > 冰箱贴",
        },
        "price_source": {"region": "PH"},
        "models": [{"model_sku": "0967"}],
        "category_decision": {"status": "DEFERRED_TO_SKILL"},
        "policy": {
            "brand": {"brand_id": 0, "original_brand_name": "NoBrand"},
            "warehouse": {
                "status": "DEFERRED_TO_SKILL",
                "location_id": None,
                "display_name": "中国仓库",
            },
        },
    }

    assert runtime.lookup_global_item_ids(command) == {"0967": None}
    prepared = runtime.prepare_creation(command)

    assert prepared == {
        "authority": "SHOPEE_OFFICIAL",
        "recommendation_count": 1,
        "category": {
            "id": "101398",
            "name": "Fridge Magnets",
            "path": [
                {"id": "100", "name": "Hobbies & Collections"},
                {"id": "101", "name": "Souvenirs"},
                {"id": "101398", "name": "Fridge Magnets"},
            ],
        },
        "required_attributes": [],
        "missing_required_attributes": [],
        "warehouse": {"location_id": "CNZ", "display_name": "中国仓库"},
    }
