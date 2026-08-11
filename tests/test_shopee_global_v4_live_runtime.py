import json

from modules.shopee.global_v4_live_runtime import (
    OfficialShopeeGlobalV4Runtime,
    ShopeeGlobalV4LiveRuntimeError,
    _default_image_upload,
    select_exact_official_category,
)
from modules.shopee.global_v4_executor import ShopeeGlobalV4Resolver
from test_shopee_global_v4_executor import _request


def test_default_image_upload_accepts_official_nested_image_info(monkeypatch):
    class PreparedImage:
        suffix = ".jpg"
        content = b"image-bytes"

    monkeypatch.setattr(
        "modules.shopee.oneclick_release._download_public_https_image",
        lambda _url: PreparedImage(),
    )
    monkeypatch.setattr(
        "modules.shopee.client.upload_image",
        lambda _path, *, scene: {
            "image_info": {"image_id": "official-image-1"}
        },
    )

    assert (
        _default_image_upload("https://img.example/approved.jpg", 0)
        == "official-image-1"
    )


def test_default_image_upload_accepts_official_image_info_list(monkeypatch):
    class PreparedImage:
        suffix = ".png"
        content = b"image-bytes"

    monkeypatch.setattr(
        "modules.shopee.oneclick_release._download_public_https_image",
        lambda _url: PreparedImage(),
    )
    monkeypatch.setattr(
        "modules.shopee.client.upload_image",
        lambda _path, *, scene: {
            "image_info_list": [
                {"image_info": {"image_id": "official-image-2"}}
            ]
        },
    )

    assert (
        _default_image_upload("https://img.example/approved.png", 1)
        == "official-image-2"
    )


def test_exact_prior_checkpoint_image_bindings_are_reused_without_upload(tmp_path):
    uploads = []
    command = {
        "offer_id": "3882722296",
        "product_revision": 40,
        "models": [{"model_sku": "0967"}],
        "product": {
            "images": [
                "https://img.example/main-1.jpg",
                "https://img.example/main-2.jpg",
            ]
        },
    }
    checkpoint = tmp_path / "3882722296" / "40" / "old-run"
    checkpoint.mkdir(parents=True)
    (checkpoint / "shopee-global-checkpoint.json").write_text(
        json.dumps(
            {
                "schema_version": "shopee-global-v4-checkpoint/v1",
                "offer_id": "3882722296",
                "product_revision": 40,
                "run_id": "old-run",
                "report_id": "publication-report:old-run",
                "image_bindings": {
                    "https://img.example/main-1.jpg": "image-1",
                    "https://img.example/main-2.jpg": "image-2",
                },
            }
        ),
        encoding="utf-8",
    )
    runtime = OfficialShopeeGlobalV4Runtime(
        context_resolver=lambda _command: {
            "merchant_id": 4970102,
            "merchant_token": "redacted",
            "shop_id": 1,
            "shop_token": "redacted",
        },
        official_fact_reader=lambda _command, _context: {},
        mapping_lookup=lambda _sku: None,
        image_upload_transport=lambda url, position: uploads.append(
            (url, position)
        )
        or f"new-{position}",
        checkpoint_root=tmp_path,
    )

    assert runtime.lookup_global_item_ids(command) == {"0967": None}
    assert runtime.upload_global_images(tuple(command["product"]["images"])) == {
        "https://img.example/main-1.jpg": "image-1",
        "https://img.example/main-2.jpg": "image-2",
    }
    assert uploads == []


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


def test_full_runtime_creates_models_persists_identity_and_officially_reads_back(
    tmp_path, monkeypatch
):
    state = {"item": None, "tiers": None, "models": None, "map": {}}

    def merchant_post(path, _merchant_id, _token, body):
        if path.endswith("add_global_item"):
            state["item"] = dict(body)
            return {"error": "", "response": {"global_item_id": 9001}}
        if path.endswith("init_tier_variation"):
            state["tiers"] = body["tier_variation"]
            state["models"] = [
                {
                    **row,
                    "global_model_id": 9101 + index,
                    "price_info": {"original_price": row["original_price"]},
                    "global_model_status": "NORMAL",
                }
                for index, row in enumerate(body["global_model"])
            ]
            return {"error": "", "response": {}}
        raise AssertionError(path)

    def merchant_get(path, _merchant_id, _token, _params):
        if path.endswith("get_global_item_info"):
            item = state["item"]
            return {
                "error": "",
                "response": {
                    "global_item_list": [
                        {
                            "global_item_id": 9001,
                            "global_item_status": "NORMAL",
                            "global_item_name": item["global_item_name"],
                            "description": item["description"],
                            "weight": item["weight"],
                            "dimension": item["dimension"],
                            "image": {
                                "image_id_list": item["image"]["image_id_list"],
                                "image_url_list": [
                                    "https://img.example/main-1.jpg",
                                    "https://img.example/main-2.jpg",
                                ],
                            },
                        }
                    ]
                },
            }
        if path.endswith("get_global_model_list"):
            return {
                "error": "",
                "response": {
                    "tier_variation": state["tiers"],
                    "global_model": state["models"],
                },
            }
        raise AssertionError(path)

    def upsert(global_item_id, *, match_keys, title, tier_name, models, **_kw):
        state["map"][str(global_item_id)] = {
            "match_key": match_keys[0],
            "match_keys": list(match_keys),
            "title": title,
            "tier_name": tier_name,
            "models": [dict(row) for row in models],
            "published_regions": [],
            "shop_items": {},
        }

    monkeypatch.setattr("modules.shopee.global_sku_map.upsert_global_group_entry", upsert)
    monkeypatch.setattr("modules.shopee.global_sku_map.load_map", lambda: state["map"])
    monkeypatch.setattr(
        "modules.shopee.global_sku_map.save_map",
        lambda value: state.update(map=value),
    )
    runtime = OfficialShopeeGlobalV4Runtime(
        context_resolver=lambda _command: {
            "merchant_id": 4970102,
            "merchant_token": "redacted",
            "shop_id": 1,
            "shop_token": "redacted",
        },
        official_fact_reader=lambda _command, _context: {
            "authority": "SHOPEE_OFFICIAL",
            "candidates": [
                {
                    "id": "101",
                    "name": "Wall Stickers",
                    "path": [
                        {"id": "10", "name": "Home"},
                        {"id": "101", "name": "Wall Stickers"},
                    ],
                    "publishable": True,
                    "required_attributes": [],
                    "missing_required_attributes": [],
                }
            ],
            "brand": {"brand_id": 0, "original_brand_name": "NoBrand"},
            "warehouse": {"location_id": "CNZ", "display_name": "中国仓库"},
        },
        mapping_lookup=lambda _sku: None,
        merchant_get_transport=merchant_get,
        merchant_post_transport=merchant_post,
        image_upload_transport=lambda _url, index: f"image-{index + 1}",
        checkpoint_root=tmp_path,
    )
    request = _request()

    assert ShopeeGlobalV4Resolver(runtime=runtime)(request) == "9001"
    assert [row["global_model_sku"] for row in state["models"]] == ["0958", "0959"]
    assert [row["original_price"] for row in state["models"]] == [40.12, 41.25]
    assert [row["global_model_id"] for row in state["map"]["9001"]["models"]] == [
        "9101",
        "9102",
    ]
    checkpoint = (
        tmp_path
        / request.snapshot["offer_id"]
        / str(request.snapshot["product_revision"])
        / request.run_id
        / "shopee-global-checkpoint.json"
    )
    assert checkpoint.is_file()


def test_model_init_error_converges_when_official_readback_is_already_exact(tmp_path):
    """A provider error must not erase a model write already visible officially."""

    state = {
        "tiers": None,
        "models": None,
    }

    def merchant_post(path, _merchant_id, _token, body):
        assert path.endswith("init_tier_variation")
        state["tiers"] = body["tier_variation"]
        state["models"] = [
            {
                **row,
                "global_model_id": 366349471396,
                "price_info": {"original_price": row["original_price"]},
                "global_model_status": "NORMAL",
            }
            for row in body["global_model"]
        ]
        return {
            "error": "product.error_param",
            "message": "The level of tier-variation not change.",
        }

    def merchant_get(path, _merchant_id, _token, _params):
        assert path.endswith("get_global_model_list")
        return {
            "error": "",
            "response": {
                "tier_variation": state["tiers"],
                "global_model": state["models"],
            },
        }

    runtime = OfficialShopeeGlobalV4Runtime(
        context_resolver=lambda _command: {
            "merchant_id": 4970102,
            "merchant_token": "redacted",
            "shop_id": 1,
            "shop_token": "redacted",
        },
        official_fact_reader=lambda _command, _context: {},
        mapping_lookup=lambda _sku: None,
        merchant_get_transport=merchant_get,
        merchant_post_transport=merchant_post,
        checkpoint_root=tmp_path,
    )
    runtime.lookup_global_item_ids(
        {
            "offer_id": "3882722296",
            "product_revision": 40,
            "models": [{"model_sku": "0967"}],
            "product": {"images": ["https://img.example/main.jpg"]},
        }
    )

    assert runtime.initialize_global_models(
        "45315817021",
        {
            "variation_names": ["option"],
            "models": [
                {
                    "model_sku": "0967",
                    "option_values": ["7cm*7cm"],
                    "variant_image_id": "image-1",
                    "price_cny": 40.12,
                    "warehouse_location_id": "CNZ",
                    "stock": {"quantity": 200},
                }
            ],
        },
    ) == {"0967": "366349471396"}
