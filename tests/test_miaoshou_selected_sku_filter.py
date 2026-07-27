import copy

import pytest

from modules.sourcing import new_product_workbench as workbench


def _draft():
    return {
        "title": "Approved title",
        "notes": "<p>approved</p>",
        "imgUrls": ["https://img.example/1.jpg"],
        "weight": 0.12,
        "packageLength": 40,
        "packageWidth": 3,
        "packageHeight": 3,
        "itemNum": "0954",
        "selectedSkuKeys": [";HS4489Q;30*40CM*3탤경;"],
        "skuLabelOverrides": {},
    }


def _converted_sku_map(item_num="0954"):
    return {";7014909006;eb268b0c71;": {"itemNum": item_num}}


@pytest.mark.parametrize(
    ("sku_map", "message"),
    [
        ({}, "no verifiable SKU map"),
        ({"converted": {}}, "missing or not four digits"),
        (
            {"one": {"itemNum": "0954"}, "two": {"itemNum": "0954"}},
            "entry count",
        ),
        ({"converted": {"itemNum": "954"}}, "missing or not four digits"),
        ({"converted": {"itemNum": "ABCD"}}, "missing or not four digits"),
        ({"converted": {"itemNum": "0955"}}, "itemNum set does not match"),
    ],
)
def test_strict_selected_skus_fail_closed_for_unverifiable_item_numbers(
    sku_map, message
):
    with pytest.raises(RuntimeError, match=message):
        workbench._strict_selected_miaoshou_sku_map(
            sku_map, _draft(), region="PH"
        )


def test_strict_selected_skus_accepts_converted_or_original_keys():
    for sku_map in (
        _converted_sku_map(),
        {";HS4489Q;30*40CM*3탤경;": {"itemNum": "0954"}},
    ):
        assert workbench._strict_selected_miaoshou_sku_map(
            sku_map, _draft(), region="PH"
        ) == sku_map


def test_strict_selected_skus_rejects_duplicate_item_numbers():
    draft = _draft()
    draft["selectedSkuKeys"] = [";one;", ";two;"]
    with pytest.raises(RuntimeError, match="duplicate SKU itemNum"):
        workbench._strict_selected_miaoshou_sku_map(
            {
                "converted-one": {"itemNum": "0954"},
                "converted-two": {"itemNum": "0954"},
            },
            draft,
            region="PH",
        )


def _shop():
    return {
        "shop_id": "7676267",
        "shop": "LivelyHive",
        "warehouses": {
            "shopWarehouseList": [
                {
                    "warehouseList": [
                        {
                            "warehouseId": "WH-1",
                            "warehouseEffectStatus": "1",
                            "isDefault": "1",
                        }
                    ]
                }
            ]
        },
    }


def _existing_info():
    return {
        "skuMap": _converted_sku_map(),
        "imgUrls": ["https://img.example/old.jpg"],
        "notes": "old",
        "weight": 0.1,
        "packageLength": 1,
        "packageWidth": 1,
        "packageHeight": 1,
    }


def test_shop_mode_accepts_converted_key_and_saves_only_after_validation():
    current = _existing_info()
    saved = {}
    calls = []

    def post(path, body):
        calls.append(path)
        if path.endswith("get_shop_collect_item_info"):
            return {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": copy.deepcopy(
                        saved or current
                    ),
                    "ossMd5": "safe-md5",
                },
            }
        if path.endswith("save_shop_collect_item_info"):
            saved.update(copy.deepcopy(body["shopCollectItemInfo"]))
            return {"result": "success"}
        raise AssertionError(path)

    result = workbench._prepare_shop_mode_draft(
        post,
        detail_id=3227305525,
        region="PH",
        shop=_shop(),
        pricing={"list_price": 414, "currency": "PHP"},
        draft=_draft(),
        category_id="600338",
        strict_selected_skus=True,
        allow_claim_repair=False,
    )

    assert result["checks"]["seller_sku"] is True
    assert list(saved["skuMap"]) == [";7014909006;eb268b0c71;"]
    assert saved["skuMap"][";7014909006;eb268b0c71;"]["itemNum"] == "0954"
    assert sum(path.endswith("save_shop_collect_item_info") for path in calls) == 1


def test_site_mode_accepts_converted_key_and_failed_validation_never_saves():
    current = _existing_info()
    saved = {}
    calls = []

    def post(path, body):
        calls.append(path)
        if path.endswith("get_site_collect_item_info"):
            return {
                "result": "success",
                "data": {
                    "siteCollectItemInfo": copy.deepcopy(saved or current),
                    "ossMd5": "safe-md5",
                },
            }
        if path.endswith("save_site_collect_item_info"):
            saved.update(copy.deepcopy(body["siteCollectItemInfo"]))
            return {"result": "success"}
        raise AssertionError(path)

    result = workbench._prepare_site_mode_draft(
        post,
        detail_id=3227305525,
        region="PH",
        region_targets=[("tiktok:LH_PH", _shop(), {"list_price": 414, "currency": "PHP"})],
        draft=_draft(),
        category_id="600338",
        strict_selected_skus=True,
    )

    assert result["checks"]["seller_sku"] is True
    assert list(saved["skuMap"]) == [";7014909006;eb268b0c71;"]
    assert sum(path.endswith("save_site_collect_item_info") for path in calls) == 1

    calls.clear()
    current["skuMap"] = {"converted": {"itemNum": "0955"}}
    saved.clear()
    with pytest.raises(RuntimeError, match="itemNum set does not match"):
        workbench._prepare_site_mode_draft(
            post,
            detail_id=3227305525,
            region="PH",
            region_targets=[("tiktok:LH_PH", _shop(), {"list_price": 414, "currency": "PHP"})],
            draft=_draft(),
            category_id="600338",
            strict_selected_skus=True,
        )
    assert not any(path.endswith("save_site_collect_item_info") for path in calls)
