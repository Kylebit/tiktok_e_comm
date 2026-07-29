import copy
import unittest

from modules.sourcing.new_product_workbench import (
    _apply_audited_english_variant_labels,
    _english_variant_checks_pass,
    _prepare_site_mode_draft,
)


def _live_variant_info() -> dict:
    return {
        "title": "Old title",
        "notes": "",
        "imgUrls": [],
        "skuPropertyList": [
            {
                "attrName": "颜色",
                "attrValueList": [
                    {"attrValueId": "color-1", "attrValue": "图片色"},
                ],
            },
            {
                "attrName": "尺寸",
                "attrValueList": [
                    {"attrValueId": "size-1", "attrValue": "特大"},
                ],
            },
        ],
        "skuMap": {
            ";color-1;size-1;": {
                "price": 0,
                "stock": 0,
                "itemNum": "",
            }
        },
        "collectBoxDetailShopList": [],
        "productAttributes": [],
        "productCertifications": [],
    }


def _site_target() -> tuple[str, dict, dict]:
    return (
        "lh_ph",
        {
            "shop_id": 1001,
            "shop": "Fixture shop",
            "warehouses": {
                "shopWarehouseList": [
                    {
                        "warehouseList": [
                            {
                                "warehouseId": "warehouse-1",
                                "warehouseEffectStatus": "1",
                                "isDefault": "1",
                            }
                        ]
                    }
                ]
            },
        },
        {
            "list_price": 100,
            "currency": "PHP",
            "discount_price": 80,
            "profit_margin_on_sale_pct": 20,
        },
    )


def _approved_draft() -> dict:
    return {
        "title": "Approved wall sticker",
        "notes": '<p><img src="https://img.example/1.jpg"></p>',
        "imgUrls": ["https://img.example/1.jpg"],
        "weight": 0.1,
        "packageLength": 10,
        "packageWidth": 10,
        "packageHeight": 1,
        "itemNum": "0001",
        "skuLabelOverrides": {
            ";图片色;特大;": "Flower sticker",
        },
    }


class MiaoshouVariantContractTests(unittest.TestCase):
    def test_live_picture_color_shape_is_canonical_and_exact(self):
        expected = _live_variant_info()
        _apply_audited_english_variant_labels(
            expected,
            _approved_draft()["skuLabelOverrides"],
        )

        self.assertEqual(
            expected["skuPropertyList"][0]["attrValueList"][0]["attrValue"],
            "As Shown",
        )
        self.assertEqual(
            expected["skuPropertyList"][1]["attrValueList"][0]["attrValue"],
            "Flower sticker",
        )
        self.assertTrue(_english_variant_checks_pass(copy.deepcopy(expected), expected))

    def test_readback_fault_matrix_fails_closed(self):
        expected = _live_variant_info()
        _apply_audited_english_variant_labels(
            expected,
            _approved_draft()["skuLabelOverrides"],
        )
        mutations = {
            "property_name": lambda value: value["skuPropertyList"][0].update(
                {"attrName": "Different"}
            ),
            "value_text": lambda value: value["skuPropertyList"][1][
                "attrValueList"
            ][0].update({"attrValue": "Different English label"}),
            "value_identity": lambda value: value["skuPropertyList"][1][
                "attrValueList"
            ][0].update({"attrValueId": "different-id"}),
            "extra_property": lambda value: value["skuPropertyList"].append(
                {
                    "attrName": "Material",
                    "attrValueList": [
                        {"attrValueId": "material-1", "attrValue": "PVC"}
                    ],
                }
            ),
            "malformed_values": lambda value: value["skuPropertyList"][0].update(
                {"attrValueList": {"unexpected": True}}
            ),
            "non_english": lambda value: value["skuPropertyList"][0][
                "attrValueList"
            ][0].update({"attrValue": "图片色"}),
        }

        for name, mutate in mutations.items():
            with self.subTest(name=name):
                actual = copy.deepcopy(expected)
                mutate(actual)
                self.assertFalse(_english_variant_checks_pass(actual, expected))

    def test_site_save_requires_exact_variant_readback(self):
        saved = []

        def fake_post(path, body=None):
            body = body or {}
            if path.endswith("get_site_collect_item_info"):
                info = (
                    copy.deepcopy(saved[-1])
                    if saved
                    else _live_variant_info()
                )
                return {
                    "result": "success",
                    "data": {
                        "siteCollectItemInfo": info,
                        "ossMd5": "revision-1",
                        "claimToShopIds": [1001],
                    },
                }
            if path.endswith("save_site_collect_item_info"):
                saved.append(copy.deepcopy(body["siteCollectItemInfo"]))
                return {"result": "success"}
            raise AssertionError(path)

        result = _prepare_site_mode_draft(
            fake_post,
            detail_id=123,
            region="PH",
            region_targets=[_site_target()],
            draft=_approved_draft(),
            category_id="600338",
            cod_enabled=True,
        )

        self.assertEqual(len(saved), 1)
        self.assertTrue(result["ready"])
        self.assertTrue(result["checks"]["english_variants"])

    def test_post_save_variant_drift_is_one_write_unverified(self):
        saved = []

        def fake_post(path, body=None):
            body = body or {}
            if path.endswith("get_site_collect_item_info"):
                info = (
                    copy.deepcopy(saved[-1])
                    if saved
                    else _live_variant_info()
                )
                if saved:
                    info["skuPropertyList"][1]["attrValueList"][0][
                        "attrValue"
                    ] = "Different English label"
                return {
                    "result": "success",
                    "data": {
                        "siteCollectItemInfo": info,
                        "ossMd5": "revision-1",
                        "claimToShopIds": [1001],
                    },
                }
            if path.endswith("save_site_collect_item_info"):
                saved.append(copy.deepcopy(body["siteCollectItemInfo"]))
                return {"result": "success"}
            raise AssertionError(path)

        result = _prepare_site_mode_draft(
            fake_post,
            detail_id=123,
            region="PH",
            region_targets=[_site_target()],
            draft=_approved_draft(),
            category_id="600338",
            cod_enabled=True,
        )

        self.assertEqual(len(saved), 1)
        self.assertFalse(result["ready"])
        self.assertFalse(result["checks"]["english_variants"])


if __name__ == "__main__":
    unittest.main()
