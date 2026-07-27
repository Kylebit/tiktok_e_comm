from unittest.mock import patch

import pytest

from modules.shopee.global_sku_map import (
    load_map,
    record_shop_item,
    upsert_global_entry,
)
from modules.shopee.publish import (
    _english_safe_sku,
    _logistic_info,
    _local_item_fields,
    _regional_listing_detail,
    _run_publish_task,
    enable_all_applicable_logistics,
    ensure_single_global_model,
    update_local_listing_copy,
)


def test_numeric_seller_sku_stays_numeric_for_cross_platform_alignment():
    assert _english_safe_sku("0952") == "0952"
    assert _english_safe_sku("990952") == "990952"


def test_regional_listing_detail_keeps_target_price_and_uses_approved_copy():
    regional = {
        "title": "Malay local title",
        "description": "regional",
        "main_images": [{"urls": ["https://regional/image.jpg"]}],
        "skus": [{"price": {"currency": "MYR", "sale_price": "45"}}],
    }
    semantic = {
        "title": "English semantic title",
        "description": "<p>Approved English description</p>",
        "main_images": [{"urls": ["https://approved/image.jpg"]}],
        "skus": [{"price": {"currency": "PHP", "sale_price": "524"}}],
    }

    result = _regional_listing_detail(
        regional,
        semantic,
        title_override="Approved Shopee title",
    )

    assert result["title"] == "Approved Shopee title"
    assert result["description"] == semantic["description"]
    assert result["main_images"] == semantic["main_images"]
    assert result["skus"] == regional["skus"]
    assert result["skus"][0]["price"]["sale_price"] == "45"


def test_live_publish_task_sends_normal_status_and_regional_price():
    seen = {}

    def merchant_post(path, merchant_id, token, body):
        seen.update(
            {
                "path": path,
                "merchant_id": merchant_id,
                "token": token,
                "body": body,
            }
        )
        return {"response": {"publish_task_id": 123}}

    with patch(
        "modules.shopee.publish._shop_meta",
        return_value={"merchant_id": 9},
    ), patch(
        "modules.shopee.publish._merchant_token",
        return_value="merchant-token",
    ), patch(
        "modules.shopee.publish._logistic_info",
        return_value=[{"logistic_id": 1, "enabled": True}],
    ), patch(
        "modules.shopee.publish.ensure_single_global_model",
        return_value={"created": False, "model_skus": ["0952"]},
    ), patch(
        "modules.shopee.publish.enable_all_applicable_logistics",
        return_value={"verified": True, "enabled_logistic_ids": [1]},
    ), patch(
        "modules.shopee.publish.merchant_post",
        side_effect=merchant_post,
    ), patch(
        "modules.shopee.publish.merchant_get",
        return_value={
            "response": {
                "publish_status": "success",
                "success": {"item_id": 456},
            }
        },
    ), patch("modules.shopee.publish.time.sleep"):
        result = _run_publish_task(
            global_item_id=77,
            detail={
                "title": "Approved Shopee title",
                "description": "<p>Approved product description long enough.</p>",
                "skus": [{"price": {"currency": "MYR", "sale_price": "45"}}],
            },
            region="MY",
            shop_id=8,
            token="shop-token",
            model_sku="0952",
            ref=None,
            item_status="NORMAL",
        )

    assert result["item_id"] == 456
    assert seen["body"]["item"]["item_status"] == "NORMAL"
    assert seen["body"]["item"]["original_price"] == 45.0
    assert "item_name" not in seen["body"]["item"]
    assert "description" not in seen["body"]["item"]
    assert "item_sku" not in seen["body"]["item"]
    assert result["copy_mode"] == "shopee_global_master_auto_translation"


def test_publish_task_rejects_unknown_item_status_before_network():
    with pytest.raises(ValueError, match="UNLIST or NORMAL"):
        _run_publish_task(
            global_item_id=77,
            detail={},
            region="MY",
            shop_id=8,
            token="shop-token",
            model_sku="0952",
            ref=None,
            item_status="PENDING",
        )


def test_logistic_info_preserves_reference_eligibility_without_a_silent_cap():
    reference = {
        "logistic_info": [
            {"logistic_id": 20087, "enabled": True},
            {"logistic_id": 28079, "enabled": True},
            {"logistic_id": 70126, "enabled": True},
            {"logistic_id": 50052, "enabled": True},
            {"logistic_id": 2000, "enabled": True},
            {"logistic_id": 28016, "enabled": False},
        ]
    }

    assert [
        row["logistic_id"]
        for row in _logistic_info(123, "token", reference)
    ] == [20087, 28079, 70126, 50052, 2000, 28016]
    assert [row["enabled"] for row in _logistic_info(123, "token", reference)] == [
        True,
        True,
        True,
        True,
        True,
        False,
    ]


def test_local_description_meets_strictest_regional_minimum_without_space_padding():
    title, description, price = _local_item_fields(
        {
            "title": "Approved product title",
            "description": "Short factual description.",
            "skus": [{"price": {"sale_price": "210000"}}],
        },
        shop_id=123,
        token="token",
        model_sku="0953",
        ref=None,
    )

    assert title
    assert price == 210000
    assert 100 <= len(description) <= 3000
    assert description == description.strip()
    assert "Please review the product images and specifications" in description


def test_single_variant_global_item_gets_an_explicit_model_sku():
    calls = []
    reads = iter(
        [
            {"response": {"global_model": []}},
            {
                "response": {
                    "global_model": [
                        {
                            "global_model_id": 88,
                            "global_model_sku": "0953",
                        }
                    ]
                }
            },
        ]
    )

    with patch(
        "modules.shopee.publish.merchant_get",
        side_effect=lambda *_args, **_kwargs: next(reads),
    ), patch(
        "modules.shopee.publish.merchant_post",
        side_effect=lambda path, _mid, _token, body: calls.append((path, body))
        or {"error": ""},
    ):
        result = ensure_single_global_model(
            global_item_id=77,
            merchant_id=9,
            merchant_token="token",
            detail={"package_dimensions": {"length": 58, "width": 34}},
            model_sku="0953",
            original_price=30.33,
            stock=200,
        )

    assert result["created"] is True
    assert result["variant_label"] == "34 x 58 cm"
    path, body = calls[0]
    assert path.endswith("init_tier_variation")
    assert body["tier_variation"] == [
        {
            "name": "Size",
            "option_list": [{"option": "34 x 58 cm"}],
        }
    ]
    assert body["global_model"][0]["global_model_sku"] == "0953"


def test_published_legacy_single_sku_is_not_destructively_converted_to_a_model():
    with patch(
        "modules.shopee.publish.merchant_get",
        return_value={"response": {"global_model": []}},
    ), patch("modules.shopee.publish.merchant_post") as post:
        result = ensure_single_global_model(
            global_item_id=77,
            merchant_id=9,
            merchant_token="token",
            detail={"package_dimensions": {"length": 58, "width": 34}},
            model_sku="0953",
            original_price=30.33,
            stock=200,
            create_when_missing=False,
        )

    assert result == {
        "created": False,
        "global_item_id": 77,
        "model_skus": [],
        "variant_label": "34 x 58 cm",
        "legacy_item_sku": True,
    }
    post.assert_not_called()


def test_existing_local_repair_updates_in_place_and_enables_every_item_channel():
    before = {
        "item_id": 456,
        "item_name": "English old copy",
        "description": "short",
        "logistic_info": [
            {"logistic_id": 1, "enabled": True},
            {"logistic_id": 2, "enabled": False},
        ],
    }
    after = {
        **before,
        "item_name": "ชื่อสินค้าไทย",
        "description": ("รายละเอียดสินค้า " * 80).strip(),
        "logistic_info": [
            {"logistic_id": 1, "enabled": True},
            {"logistic_id": 2, "enabled": True},
        ],
    }
    posted = []
    with patch(
        "modules.shopee.publish._item_base_info",
        return_value=after,
    ), patch(
        "modules.shopee.publish.enable_all_applicable_logistics",
        return_value={
            "verified": True,
            "enabled_logistic_ids": [1, 2],
            "rejected_logistics": [],
        },
    ), patch(
        "modules.shopee.publish.shop_post",
        side_effect=lambda path, _shop, _token, body: posted.append((path, body))
        or {"error": ""},
    ):
        result = update_local_listing_copy(
            shop_id=123,
            token="token",
            item_id=456,
            title=after["item_name"],
            description=after["description"],
        )

    assert result["verified"] is True
    assert posted[0][1]["item_id"] == 456
    assert "logistic_info" not in posted[0][1]
    assert result["logistics"]["enabled_logistic_ids"] == [1, 2]


def test_enable_all_logistics_keeps_platform_rejections_as_audited_exceptions():
    current = {
        "item_id": 456,
        "logistic_info": [
            {"logistic_id": 1, "logistic_name": "Standard", "enabled": True},
            {"logistic_id": 2, "logistic_name": "Locker", "enabled": False},
            {"logistic_id": 3, "logistic_name": "Sea", "enabled": False},
        ],
    }
    after_sea = {
        **current,
        "logistic_info": [
            {"logistic_id": 1, "logistic_name": "Standard", "enabled": True},
            {"logistic_id": 2, "logistic_name": "Locker", "enabled": False},
            {"logistic_id": 3, "logistic_name": "Sea", "enabled": True},
        ],
    }
    reads = iter([current, after_sea, after_sea])

    def post(_path, _shop, _token, body):
        enabled = {
            row["logistic_id"]
            for row in body["logistic_info"]
            if row["enabled"]
        }
        if 2 in enabled:
            return {
                "error": "invalid_logistic",
                "message": "parcel is outside Locker limits",
            }
        return {"error": ""}

    with patch(
        "modules.shopee.publish._item_base_info",
        side_effect=lambda *_args, **_kwargs: next(reads),
    ), patch("modules.shopee.publish.shop_post", side_effect=post):
        result = enable_all_applicable_logistics(123, "token", 456)

    assert result["verified"] is True
    assert result["enabled_logistic_ids"] == [1, 3]
    assert result["newly_enabled_logistic_ids"] == [3]
    assert result["rejected_logistics"] == [
        {
            "logistic_id": 2,
            "logistic_name": "Locker",
            "reason": "parcel is outside Locker limits",
        }
    ]


def test_record_shop_item_expands_only_verified_published_regions(tmp_path):
    mapping = tmp_path / "global-map.json"
    with patch(
        "modules.shopee.global_sku_map.map_path",
        return_value=mapping,
    ):
        upsert_global_entry(
            "7001",
            match_key="0952",
            global_model_sku="0952",
            published_regions=["PH"],
        )
        record_shop_item(
            "7001",
            "MY",
            shop_id=8,
            item_id=456,
        )
        result = load_map()["7001"]

    assert result["published_regions"] == ["MY", "PH"]
    assert result["shop_items"]["MY"]["item_id"] == "456"
