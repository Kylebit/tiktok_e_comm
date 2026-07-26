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


def test_logistic_info_excludes_channels_that_reject_package_measurements():
    reference = {
        "logistic_info": [
            {"logistic_id": 20087, "enabled": True},
            {"logistic_id": 28079, "enabled": True},
            {"logistic_id": 70126, "enabled": True},
            {"logistic_id": 50052, "enabled": True},
            {"logistic_id": 2000, "enabled": True},
            {"logistic_id": 28016, "enabled": True},
        ]
    }

    assert [
        row["logistic_id"]
        for row in _logistic_info(123, "token", reference)
    ] == [2000, 28016]


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
