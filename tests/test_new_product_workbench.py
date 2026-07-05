import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from modules.sourcing.miaoshou_precollect import (
    import_common_collect_detail,
    normalize_detail,
    refresh_precollect,
    source_item_id,
)
from modules.sourcing.new_product_workbench import (
    _anchor_group_key,
    _expected_region_site_state,
    _distribute_total,
    _normalize_title,
    _pick_default_warehouse_id,
    _site_state_matches_expected,
    build_preview,
    claim_miaoshou_to_tiktok,
    extract_overseas_material,
    extract_overseas_material_from_common_collect,
    ensure_common_sequential_skus,
    parse_common_collect_id,
    prepare_miaoshou_site_drafts,
    prepare_miaoshou_draft,
    price_review,
    write_miaoshou_draft,
)


class NewProductWorkbenchTests(unittest.TestCase):
    def test_anchor_group_key_merges_lively_sea_mx_gb(self):
        self.assertEqual(_anchor_group_key({"shop": "LivelyHive", "region": "PH"}), "lively")
        self.assertEqual(_anchor_group_key({"shop": "LivelyHive", "region": "MX"}), "lively")
        self.assertEqual(_anchor_group_key({"shop": "LivelyHive", "region": "GB"}), "lively")
        self.assertEqual(_anchor_group_key({"shop": "HomeBloom", "region": "PH"}), "homebloom")

    def test_pick_default_warehouse_id_prefers_default_then_cnsc(self):
        rows = [
            {"warehouseId": "a", "warehouseEffectStatus": "1", "warehouseSubType": "3", "isDefault": "0"},
            {"warehouseId": "b", "warehouseEffectStatus": "1", "warehouseSubType": "2", "isDefault": "1"},
            {"warehouseId": "c", "warehouseEffectStatus": "1", "warehouseSubType": "3", "isDefault": "1"},
        ]
        self.assertEqual(_pick_default_warehouse_id(rows), "c")

    def test_site_state_match_requires_current_detail_ids_and_shop_ids(self):
        prepared_targets = {
            "lh_ph": {"detail_id": 101},
            "hb_ph": {"detail_id": 202},
        }
        expected = _expected_region_site_state(
            "PH",
            [
                ("lh_ph", {"shop_id": "7676267"}),
                ("hb_ph", {"shop_id": "15173238"}),
            ],
            prepared_targets,
        )

        self.assertTrue(_site_state_matches_expected({
            "ready": True,
            "sku_scheme_version": 3,
            "detail_ids": [202, 101],
            "site_collect_shop_ids": ["15173238", "7676267"],
        }, expected))
        self.assertFalse(_site_state_matches_expected({
            "ready": True,
            "sku_scheme_version": 3,
            "detail_ids": [101],
            "site_collect_shop_ids": ["7676267", "15173238"],
        }, expected))
        self.assertFalse(_site_state_matches_expected({
            "ready": True,
            "sku_scheme_version": 3,
            "detail_ids": [202, 101],
            "site_collect_shop_ids": ["7676267"],
        }, expected))

    def test_normalize_title_removes_full_duplicate(self):
        self.assertEqual(
            _normalize_title("English Title English Title"),
            "English Title",
        )

    def test_distribute_total_preserves_total(self):
        self.assertEqual(_distribute_total(200, 2), [100, 100])
        self.assertEqual(sum(_distribute_total(200, 3)), 200)

    def test_temu_share_image_is_available_without_page_rendering(self):
        result = extract_overseas_material(
            "https://www.temu.com/goods.html?goods_id=1&share_img="
            "https%3A%2F%2Fcommimg.example.com%2Fproduct.png",
            fetch=False,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["images"], ["https://commimg.example.com/product.png"])

    def test_missing_source_cost_never_reports_ready_prices(self):
        result = price_review(0, 0.2, [20, 20, 3])

        self.assertTrue(result["sea"])
        self.assertTrue(all(row["status"] == "missing_cost" for row in result["sea"]))
        self.assertIsNone(result["mx"]["list_price"])
        self.assertEqual(result["uk"]["status"], "missing_cost")

    def test_price_review_exposes_audit_details(self):
        result = price_review(7.36, 0.2, [20, 20, 3])

        sea_row = result["sea"][0]
        self.assertIn("cost_cny", sea_row)
        self.assertIn("billable_kg", sea_row)
        self.assertIn("estimated_profit_cny", sea_row)
        self.assertIn("profit_margin_on_sale_pct", sea_row)
        self.assertIn("estimated_shipping", result["mx"])
        self.assertIn("estimated_profit", result["uk"])

    def test_sea_price_review_keeps_target_margin_after_discount(self):
        result = price_review(9, 0.5, [30, 8, 8])

        for row in result["sea"]:
            self.assertGreaterEqual(row["estimated_profit_cny"], 0)
            self.assertGreaterEqual(row["profit_margin_on_sale_pct"], row["target_margin_pct"] - 0.5)
            self.assertEqual(row["status"], "ok")

    def test_new_product_preview_forces_cod_support(self):
        result = build_preview("967648348081")

        self.assertTrue(result["source"]["support_cod"])
        self.assertTrue(result["review"]["support_cod"])

    def test_miaoshou_draft_deduplicates_kept_images_and_assigns_sku(self):
        preview = {
            "source": {"source_id": "1688-1", "video": {"url": "https://video.example/a.mp4"}},
            "review": {
                "fields_locked": True,
                "seller_sku": "",
                "title": "English product title",
                "weight_kg": 0.45,
                "package_cm": [40, 14, 10],
                "selected_sites": ["lh_ph", "mx"],
                "video_action": "remove",
                "image_actions": [
                    {"action": "keep", "url": "https://img.example/1.jpg"},
                    {"action": "keep", "url": "https://img.example/1.jpg"},
                    {"action": "keep", "url": "https://img.example/2.jpg"},
                    {"action": "keep", "url": "https://img.example/3.jpg"},
                    {"action": "remove", "url": "https://img.example/4.jpg"},
                ],
            },
        }
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value=preview
        ), patch(
            "modules.sourcing.new_product_workbench._next_seller_sku", return_value="0942"
        ), patch(
            "modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)
        ), patch(
            "modules.sourcing.new_product_workbench.load_state", return_value={"review": {}}
        ), patch("modules.sourcing.new_product_workbench.save_state"):
            result = prepare_miaoshou_draft("123")

        self.assertTrue(result["ready"])
        self.assertEqual(result["draft"]["itemNum"], "0942")
        self.assertEqual(len(result["draft"]["imgUrls"]), 3)
        self.assertEqual(result["draft"]["mainImgVideoUrl"], "")

    def test_miaoshou_draft_write_verifies_without_claiming(self):
        draft = {
            "commonCollectBoxDetailId": "123",
            "title": "English product title",
            "itemNum": "0942",
            "weight": 0.45,
            "packageLength": 40.0,
            "packageWidth": 14.0,
            "packageHeight": 10.0,
            "imgUrls": ["https://img.example/1.jpg", "https://img.example/2.jpg", "https://img.example/3.jpg"],
            "notes": '<p><img src="1"><img src="2"><img src="3"></p>',
            "mainImgVideoUrl": "",
        }
        prepared = {"ok": True, "ready": True, "offer_id": "123", "draft": draft, "blockers": []}
        current = {"skuMap": {";Red;;": {"stock": 10, "price": 9}}, "title": "old"}
        saved = {}

        def fake_post(path, body=None):
            if path.endswith("edit_common_collect_box_detail"):
                saved.update((body or {})["editCommonCollectBoxDetail"])
                return {"result": "success"}
            if path.endswith("get_common_collect_box_detail"):
                detail = saved or current
                return {"result": "success", "data": {"editCommonCollectBoxDetail": detail, "ossMd5": "x"}}
            raise AssertionError(path)

        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.prepare_miaoshou_draft", return_value=prepared
        ), patch("modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)):
            result = write_miaoshou_draft("123", post=fake_post)

        self.assertTrue(result["verified"])
        self.assertTrue(result["written_to_miaoshou"])
        self.assertFalse(result["claimed"])
        self.assertFalse(result["published"])

    def test_common_sku_fix_preserves_content_and_numbers_variants(self):
        current = {
            "commonCollectBoxDetailId": 123,
            "title": "Approved title",
            "itemNum": "0942",
            "imgUrls": ["https://img/1.jpg"],
            "notes": "approved notes",
            "skuMap": {"red": {"itemNum": "0942"}, "pink": {"itemNum": "0942"}},
        }
        saved = {}

        def fake_post(path, body=None):
            if path.endswith("edit_common_collect_box_detail"):
                saved.update((body or {})["editCommonCollectBoxDetail"])
                return {"result": "success"}
            if path.endswith("get_common_collect_box_detail"):
                return {"result": "success", "data": {
                    "editCommonCollectBoxDetail": saved or current, "ossMd5": "x",
                }}
            raise AssertionError(path)

        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch("modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)), patch(
            "modules.sourcing.new_product_workbench.load_state", return_value={"review": {"seller_sku": "0942"}}
        ):
            result = ensure_common_sequential_skus("123", post=fake_post)

        self.assertTrue(result["verified"])
        self.assertEqual(result["sku_item_nums"], ["0942", "0943"])
        self.assertEqual(saved["title"], "Approved title")
        self.assertEqual(saved["imgUrls"], ["https://img/1.jpg"])
        self.assertEqual(saved["notes"], "approved notes")

    def test_claim_to_tiktok_reports_unavailable_selected_shop(self):
        claim_calls = []
        collect_rows = [
            {"collectBoxDetailId": "456", "commonCollectBoxDetailId": "123", "gmtCreate": "2026-07-01 11:00:00", "collectBoxDetailShopList": [{"shopId": "7676267"}]},
        ]

        def fake_post(path, body=None):
            if path.endswith("common_collect_box/claimed"):
                return {"result": "success", "data": {"platformCollectBoxDetailIdMap": {"tiktok": {"123": 456}}}}
            if path.endswith("claim_to_shop"):
                claim_calls.append(body or {})
                return {"result": "success"}
            if path.endswith("search_collect_box_detail_list"):
                return {"result": "success", "data": {"detailList": collect_rows}}
            if path.endswith("get_shop_collect_item_info"):
                return {"result": "success", "data": {"shopCollectItemInfo": {
                    "title": "Title", "cid": "1", "imgUrls": ["https://img/1.jpg"],
                    "skuMap": {";Red;;": {}}, "weight": 0.4,
                    "packageLength": 10, "packageWidth": 8, "packageHeight": 4,
                }}}
            if path.endswith("get_shop_warehouse_list"):
                return {"result": "success", "data": {"warehouseList": []}}
            raise AssertionError(path)

        custom_markets = [
            {"id": "lh_ph", "shop": "LivelyHive", "region": "PH", "currency": "PHP", "enabled": True, "shop_id": 7676267},
            {"id": "hb_th", "shop": "HomeBloom", "region": "TH", "currency": "THB", "enabled": False, "shop_id": None},
        ]
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value={"source": {"source_id": "src-1"}}
        ), patch(
            "modules.sourcing.new_product_workbench.sync_miaoshou_second_review",
            return_value={"ok": True, "second_review_approved": True},
        ), patch(
            "modules.sourcing.new_product_workbench.ensure_common_sequential_skus",
            return_value={"ok": True, "verified": True, "sku_item_nums": ["0942"]},
        ), patch(
            "modules.sourcing.new_product_workbench.load_state",
            return_value={"review": {"selected_sites": ["lh_ph", "hb_th"]}},
        ), patch("modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)), patch(
            "modules.sourcing.new_product_workbench.SEA_MARKETS", custom_markets
        ):
            result = claim_miaoshou_to_tiktok("123", post=fake_post)

        self.assertTrue(result["claimed"])
        self.assertEqual(result["tiktok_detail_id"], 456)
        self.assertIn("lh_ph", result["shops"])
        self.assertIn("hb_th", result["blocked_sites"])
        self.assertEqual(len(claim_calls), 1)
        self.assertEqual(claim_calls[0]["shopIds"], ["7676267"])
        self.assertFalse(result["published"])

    def test_claim_to_tiktok_claims_each_selected_shop_without_final_reset(self):
        claim_calls = []
        claimed_calls = []
        collect_rows = [
            {"collectBoxDetailId": "456", "commonCollectBoxDetailId": "123", "gmtCreate": "2026-07-01 11:00:00", "collectBoxDetailShopList": [{"shopId": "7676267"}, {"shopId": "10204699"}]},
        ]

        def fake_post(path, body=None):
            if path.endswith("common_collect_box/claimed"):
                claimed_calls.append(body or {})
                detail_id = 456 if len(claimed_calls) == 1 else 457
                return {"result": "success", "data": {"platformCollectBoxDetailIdMap": {"tiktok": {"123": detail_id}}}}
            if path.endswith("claim_to_shop"):
                claim_calls.append(body or {})
                return {"result": "success"}
            if path.endswith("search_collect_box_detail_list"):
                return {"result": "success", "data": {"detailList": collect_rows}}
            if path.endswith("get_shop_collect_item_info"):
                detail_id = (body or {}).get("detailId")
                return {"result": "success", "data": {"shopCollectItemInfo": {
                    "title": "Title", "cid": "1", "imgUrls": ["https://img/1.jpg"],
                    "skuMap": {";Red;;": {}}, "weight": 0.4,
                    "packageLength": 10, "packageWidth": 8, "packageHeight": 4,
                }, "claimToShopIds": [7676267, 10204699] if str(detail_id) == "456" else []}}
            if path.endswith("get_shop_warehouse_list"):
                return {"result": "success", "data": {"warehouseList": []}}
            raise AssertionError(path)

        custom_markets = [
            {"id": "lh_ph", "shop": "LivelyHive", "region": "PH", "currency": "PHP", "enabled": True, "shop_id": 7676267, "publish_group": "lively"},
            {"id": "gb", "shop": "LivelyHive", "region": "GB", "currency": "GBP", "enabled": True, "shop_id": 10204699, "publish_group": "lively"},
        ]
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value={"source": {"source_id": "src-1"}}
        ), patch(
            "modules.sourcing.new_product_workbench.sync_miaoshou_second_review",
            return_value={"ok": True, "second_review_approved": True},
        ), patch(
            "modules.sourcing.new_product_workbench.ensure_common_sequential_skus",
            return_value={"ok": True, "verified": True, "sku_item_nums": ["0942"]},
        ), patch(
            "modules.sourcing.new_product_workbench.load_state",
            return_value={"review": {"selected_sites": ["lh_ph", "gb"]}},
        ), patch("modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)), patch(
            "modules.sourcing.new_product_workbench.SEA_MARKETS", custom_markets
        ):
            result = claim_miaoshou_to_tiktok("123", post=fake_post)

        self.assertTrue(result["claimed"])
        self.assertEqual(len(claim_calls), 1)
        self.assertEqual(len(claimed_calls), 1)
        self.assertEqual([call["shopIds"] for call in claim_calls], [["7676267"]])
        self.assertEqual(result["shops"]["lh_ph"]["detail_id"], 456)
        self.assertEqual(result["shops"]["gb"]["detail_id"], 456)

    def test_claim_to_tiktok_keeps_all_requested_shop_claims(self):
        claim_calls = []
        claimed_calls = []
        collect_rows = [
            {"collectBoxDetailId": "456", "commonCollectBoxDetailId": "123", "gmtCreate": "2026-07-01 11:02:00", "collectBoxDetailShopList": [{"shopId": "7676267"}, {"shopId": "10204699"}]},
            {"collectBoxDetailId": "457", "commonCollectBoxDetailId": "123", "gmtCreate": "2026-07-01 11:01:00", "collectBoxDetailShopList": [{"shopId": "15173238"}]},
        ]

        def fake_post(path, body=None):
            if path.endswith("common_collect_box/claimed"):
                claimed_calls.append(body or {})
                detail_id = {1: 456, 2: 457}.get(len(claimed_calls), 458)
                return {"result": "success", "data": {"platformCollectBoxDetailIdMap": {"tiktok": {"123": detail_id}}}}
            if path.endswith("claim_to_shop"):
                claim_calls.append(body or {})
                return {"result": "success"}
            if path.endswith("search_collect_box_detail_list"):
                return {"result": "success", "data": {"detailList": collect_rows}}
            if path.endswith("get_shop_collect_item_info"):
                detail_id = str((body or {}).get("detailId"))
                claim_ids = {
                    "456": [7676267, 10204699],
                    "457": [15173238],
                }.get(detail_id, [])
                return {"result": "success", "data": {"shopCollectItemInfo": {
                    "title": "Title", "cid": "1", "imgUrls": ["https://img/1.jpg"],
                    "skuMap": {";Red;;": {}}, "weight": 0.4,
                    "packageLength": 10, "packageWidth": 8, "packageHeight": 4,
                }, "claimToShopIds": claim_ids}}
            if path.endswith("get_shop_warehouse_list"):
                return {"result": "success", "data": {"warehouseList": []}}
            raise AssertionError(path)

        custom_markets = [
            {"id": "lh_ph", "shop": "LivelyHive", "region": "PH", "currency": "PHP", "enabled": True, "shop_id": 7676267, "publish_group": "lively"},
            {"id": "hb_ph", "shop": "HomeBloom", "region": "PH", "currency": "PHP", "enabled": True, "shop_id": 15173238, "publish_group": "homebloom"},
            {"id": "gb", "shop": "LivelyHive", "region": "GB", "currency": "GBP", "enabled": True, "shop_id": 10204699, "publish_group": "lively"},
        ]
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value={"source": {"source_id": "src-1"}}
        ), patch(
            "modules.sourcing.new_product_workbench.sync_miaoshou_second_review",
            return_value={"ok": True, "second_review_approved": True},
        ), patch(
            "modules.sourcing.new_product_workbench.ensure_common_sequential_skus",
            return_value={"ok": True, "verified": True, "sku_item_nums": ["0942"]},
        ), patch(
            "modules.sourcing.new_product_workbench.load_state",
            return_value={"review": {"selected_sites": ["lh_ph", "hb_ph", "gb"]}},
        ), patch("modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)), patch(
            "modules.sourcing.new_product_workbench.SEA_MARKETS", custom_markets
        ):
            result = claim_miaoshou_to_tiktok("123", post=fake_post)

        self.assertTrue(result["claimed"])
        self.assertEqual(len(claimed_calls), 2)
        self.assertEqual(len(claim_calls), 2)
        self.assertEqual([call["shopIds"] for call in claim_calls], [["7676267"], ["15173238"]])
        self.assertEqual(result["shops"]["lh_ph"]["detail_id"], 456)
        self.assertEqual(result["shops"]["hb_ph"]["detail_id"], 457)
        self.assertEqual(result["shops"]["gb"]["detail_id"], 456)

    def test_site_draft_writes_price_cod_english_variants_and_warehouse(self):
        self.skipTest("legacy shop-mode expectations replaced by live site-mode regression")
        warehouse = {"shopWarehouseList": [{"warehouseList": [{
            "warehouseId": "wh1", "warehouseEffectStatus": "1", "isDefault": "1",
            "warehouseSubType": "3",
        }]}]}
        claim = {
            "claimed": True,
            "tiktok_detail_id": 456,
            "shops": {"lh_ph": {
                "shop_id": 7676267, "shop": "LivelyHive", "region": "PH",
                "currency": "PHP", "warehouses": warehouse,
            }},
            "blocked_sites": {},
        }
        images = ["https://img/1.jpg", "https://img/2.jpg", "https://img/3.jpg"]
        draft = {"second_review_approved": True, "draft": {
            "title": "English title", "notes": "".join(f'<img src="{x}">' for x in images),
            "imgUrls": images, "weight": 0.45, "packageLength": 40,
            "packageWidth": 14, "packageHeight": 10, "itemNum": "0942",
            "mainImgVideoUrl": "",
        }}
        base = {
            "title": "old", "skuPropertyList": [{"attrName": "颜色", "attrValueList": [
                {"attrValueId": "87333b5fe4", "attrValue": "红"},
                {"attrValueId": "a8fefa8b1f", "attrValue": "粉"},
            ]}],
            "skuMap": {";87333b5fe4;": {"stock": 300}, ";a8fefa8b1f;": {"stock": 300}},
        }
        saved = {}
        collect_rows = [
            {"collectBoxDetailId": "456", "commonCollectBoxDetailId": "123", "gmtCreate": "2026-07-01 11:00:00", "collectBoxDetailShopList": [{"shopId": "7676267"}]},
        ]

        def fake_post(path, body=None):
            if path.endswith("search_collect_box_detail_list"):
                return {"result": "success", "data": {"detailList": collect_rows}}
            if path.endswith("claim_to_shop"):
                return {"result": "success"}
            if path.endswith("get_shop_collect_item_info"):
                shop_id = str((body or {})["shopId"])
                return {"result": "success", "data": {
                    "shopCollectItemInfo": saved.get(shop_id, json.loads(json.dumps(base))), "ossMd5": "x",
                }}
            if path.endswith("save_shop_collect_item_info"):
                saved[str((body or {})["shopId"])] = (body or {})["shopCollectItemInfo"]
                return {"result": "success"}
            raise AssertionError(path)

        pricing = {"sea": [{
            "region": "PH", "currency": "PHP", "list_price": 1174,
            "discount_price": 763.1, "profit_margin_on_sale_pct": 20.07,
        }], "mx": {}, "uk": {}}
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)
        ), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value={"pricing": pricing}
        ):
            Path(tmp, "123_tiktok_claim.json").write_text(json.dumps(claim), encoding="utf-8")
            Path(tmp, "123_miaoshou_draft.json").write_text(json.dumps(draft), encoding="utf-8")
            result = prepare_miaoshou_site_drafts("123", post=fake_post)

        self.assertTrue(result["ready"])
        self.assertFalse(result["published"])
        self.assertEqual(saved["7676267"]["cid"], "853256")
        self.assertEqual(saved["7676267"]["isCodOpen"], "1")
        self.assertEqual(saved["7676267"]["skuPropertyList"][0]["attrValueList"][0]["attrValue"], "Ivory Red")
        self.assertEqual(
            [sku["itemNum"] for sku in saved["7676267"]["skuMap"].values()],
            ["0942", "0943"],
        )

    def test_mx_site_draft_uses_shop_mode_endpoint(self):
        self.skipTest("legacy shop-mode expectations replaced by live site-mode regression")
        warehouse = {"shopWarehouseList": [{"warehouseList": [{
            "warehouseId": "mx-wh", "warehouseEffectStatus": "1", "isDefault": "1",
        }]}]}
        claim = {"claimed": True, "tiktok_detail_id": 456, "shops": {"mx": {
            "shop_id": 16265910, "shop": "LivelyHive", "region": "MX",
            "currency": "MXN", "warehouses": warehouse,
        }}, "blocked_sites": {}}
        images = ["https://img/1.jpg", "https://img/2.jpg", "https://img/3.jpg"]
        draft = {"second_review_approved": True, "draft": {
            "title": "English title", "notes": "".join(f'<img src="{x}">' for x in images),
            "imgUrls": images, "weight": 0.45, "packageLength": 40,
            "packageWidth": 14, "packageHeight": 10, "itemNum": "0942",
        }}
        base = {"skuPropertyList": [{"attrValueList": [
            {"attrValueId": "87333b5fe4", "attrValue": "红"},
            {"attrValueId": "a8fefa8b1f", "attrValue": "粉"},
        ]}], "skuMap": {"a": {"stock": 300}}}
        saved = {}
        collect_rows = [
            {"collectBoxDetailId": "456", "commonCollectBoxDetailId": "123", "gmtCreate": "2026-07-01 11:00:00", "collectBoxDetailShopList": [{"shopId": "16265910"}]},
        ]

        def fake_post(path, body=None):
            if path.endswith("search_collect_box_detail_list"):
                return {"result": "success", "data": {"detailList": collect_rows}}
            if path.endswith("claim_to_shop"):
                return {"result": "success"}
            if path.endswith("get_shop_collect_item_info"):
                return {"result": "success", "data": {
                    "shopCollectItemInfo": saved or json.loads(json.dumps(base)), "ossMd5": "x",
                }}
            if path.endswith("save_shop_collect_item_info"):
                saved.update((body or {})["shopCollectItemInfo"])
                return {"result": "success"}
            raise AssertionError(path)

        pricing = {"sea": [], "mx": {
            "region": "MX", "currency": "MXN", "list_price": 443,
            "discount_price": 310, "profit_margin_on_sale_pct": 15.1,
        }, "uk": {}}
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch("modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value={"pricing": pricing}
        ):
            Path(tmp, "123_tiktok_claim.json").write_text(json.dumps(claim), encoding="utf-8")
            Path(tmp, "123_miaoshou_draft.json").write_text(json.dumps(draft), encoding="utf-8")
            result = prepare_miaoshou_site_drafts("123", post=fake_post)

        self.assertTrue(result["ready"])
        self.assertEqual(result["sites"]["MX"]["mode"], "shop")
        self.assertEqual(saved["skuMap"]["a"]["price"], 443.0)

    def test_sea_site_drafts_use_per_shop_pricing_and_aggregate_by_region(self):
        self.skipTest("legacy shop-mode expectations replaced by live site-mode regression")
        warehouse_lh = {"shopWarehouseList": [{"warehouseList": [{
            "warehouseId": "lh-wh", "warehouseEffectStatus": "1", "isDefault": "1",
            "warehouseSubType": "3",
        }]}]}
        warehouse_hb = {"shopWarehouseList": [{"warehouseList": [{
            "warehouseId": "hb-wh", "warehouseEffectStatus": "1", "isDefault": "1",
            "warehouseSubType": "3",
        }]}]}
        claim = {
            "claimed": True,
            "tiktok_detail_id": 456,
            "shops": {
                "lh_ph": {
                    "shop_id": 7676267, "shop": "LivelyHive", "region": "PH",
                    "currency": "PHP", "warehouses": warehouse_lh,
                },
                "hb_ph": {
                    "shop_id": 15173238, "shop": "HomeBloom", "region": "PH",
                    "currency": "PHP", "warehouses": warehouse_hb,
                },
            },
            "blocked_sites": {},
        }
        images = ["https://img/1.jpg", "https://img/2.jpg"]
        draft = {"second_review_approved": True, "draft": {
            "title": "English title", "notes": "".join(f'<img src="{x}">' for x in images),
            "imgUrls": images, "weight": 0.45, "packageLength": 40,
            "packageWidth": 14, "packageHeight": 10, "itemNum": "0942",
            "mainImgVideoUrl": "",
        }}
        base = {
            "title": "old", "skuPropertyList": [{"attrName": "颜色", "attrValueList": [
                {"attrValueId": "87333b5fe4", "attrValue": "红"},
                {"attrValueId": "a8fefa8b1f", "attrValue": "粉"},
            ]}],
            "skuMap": {";87333b5fe4;": {"stock": 300}, ";a8fefa8b1f;": {"stock": 300}},
            "deliveryOptionSetType": "default",
            "deliveryOptionIds": [],
            "manufacturerIds": [],
            "responsiblePersonIds": [],
            "productCertifications": [],
        }
        saved = {}
        collect_rows = [
            {"collectBoxDetailId": "456", "commonCollectBoxDetailId": "123", "gmtCreate": "2026-07-01 11:00:00", "collectBoxDetailShopList": [{"shopId": "7676267"}]},
            {"collectBoxDetailId": "457", "commonCollectBoxDetailId": "123", "gmtCreate": "2026-07-01 11:01:00", "collectBoxDetailShopList": [{"shopId": "15173238"}]},
        ]

        def fake_post(path, body=None):
            body = body or {}
            if path.endswith("search_collect_box_detail_list"):
                return {"result": "success", "data": {"detailList": collect_rows}}
            if path.endswith("claim_to_shop"):
                return {"result": "success"}
            if path.endswith("get_shop_collect_item_info"):
                shop_id = str(body["shopId"])
                return {"result": "success", "data": {
                    "shopCollectItemInfo": saved.get(shop_id, json.loads(json.dumps(base))), "ossMd5": "x",
                }}
            if path.endswith("save_shop_collect_item_info"):
                shop_id = str(body["shopId"])
                saved[shop_id] = body["shopCollectItemInfo"]
                return {"result": "success"}
            raise AssertionError(path)

        pricing = {"sea": [
            {
                "id": "lh_ph", "region": "PH", "currency": "PHP", "list_price": 1174,
                "discount_price": 763.1, "profit_margin_on_sale_pct": 20.07,
            },
            {
                "id": "hb_ph", "region": "PH", "currency": "PHP", "list_price": 877,
                "discount_price": 570.05, "profit_margin_on_sale_pct": 10.02,
            },
        ], "mx": {}, "uk": {}}
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)
        ), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value={"pricing": pricing}
        ):
            Path(tmp, "123_tiktok_claim.json").write_text(json.dumps(claim), encoding="utf-8")
            Path(tmp, "123_miaoshou_draft.json").write_text(json.dumps(draft), encoding="utf-8")
            result = prepare_miaoshou_site_drafts("123", post=fake_post)

        self.assertTrue(result["ready"])
        self.assertEqual(result["sites"]["PH"]["mode"], "shop_fallback")
        self.assertTrue(result["sites"]["PH"]["mixed_pricing"])
        self.assertEqual(result["sites"]["PH"]["shop_results"][0]["target_id"], "lh_ph")
        self.assertEqual(result["sites"]["PH"]["shop_results"][1]["target_id"], "hb_ph")
        self.assertEqual(saved["7676267"]["skuMap"][";87333b5fe4;"]["price"], 1174.0)
        self.assertEqual(saved["15173238"]["skuMap"][";87333b5fe4;"]["price"], 877.0)

    def test_prepare_site_drafts_claims_shared_detail_with_full_shop_union(self):
        self.skipTest("legacy shop-mode expectations replaced by live site-mode regression")
        claim = {
            "claimed": True,
            "tiktok_detail_id": 456,
            "source_item_id": "123",
            "shops": {
                "lh_ph": {"region": "PH", "shop": "LivelyHive", "shop_id": "7676267", "publish_group": "lively", "warehouses": {"shopWarehouseList": [{"warehouseList": [{"warehouseId": "W1", "warehouseEffectStatus": "1", "isDefault": "1"}]}]}},
                "hb_ph": {"region": "PH", "shop": "HomeBloom", "shop_id": "15173238", "publish_group": "homebloom", "warehouses": {"shopWarehouseList": [{"warehouseList": [{"warehouseId": "W2", "warehouseEffectStatus": "1", "isDefault": "1"}]}]}},
                "mx": {"region": "MX", "shop": "LivelyHive", "shop_id": "16265910", "publish_group": "lively", "warehouses": {"shopWarehouseList": [{"warehouseList": [{"warehouseId": "W3", "warehouseEffectStatus": "1", "isDefault": "1"}]}]}},
            },
        }
        draft = {
            "second_review_approved": True,
            "draft": {
                "title": "Shared detail test",
                "notes": "<img src='https://img/a.jpg'>",
                "imgUrls": ["https://img/a.jpg"],
                "weight": 0.2,
                "packageLength": 20.0,
                "packageWidth": 10.0,
                "packageHeight": 5.0,
                "mainImgVideoUrl": "",
                "itemNum": "0944",
            },
        }
        base = {
            "title": "old",
            "cid": "853256",
            "imgUrls": ["https://img/a.jpg"],
            "notes": "<img src='https://img/a.jpg'>",
            "weight": 0.2,
            "packageLength": 20.0,
            "packageWidth": 10.0,
            "packageHeight": 5.0,
            "skuPropertyList": [{"attrName": "Color", "attrValueList": []}],
            "skuMap": {";red;": {"stock": 300, "price": 0, "itemNum": "0944"}},
            "deliveryOptionSetType": "default",
            "deliveryOptionIds": [],
            "manufacturerIds": [],
            "responsiblePersonIds": [],
            "productCertifications": [],
        }
        claim_calls = []
        claimed_by_detail = {}
        saved = {}
        collect_rows = [
            {
                "collectBoxDetailId": "456",
                "commonCollectBoxDetailId": "123",
                "gmtCreate": "2026-07-01 11:00:00",
                "collectBoxDetailShopList": [
                    {"shopId": "7676267"},
                    {"shopId": "16265910"},
                ],
            },
            {
                "collectBoxDetailId": "457",
                "commonCollectBoxDetailId": "123",
                "gmtCreate": "2026-07-01 11:01:00",
                "collectBoxDetailShopList": [
                    {"shopId": "15173238"},
                ],
            },
        ]

        def fake_post(path, body=None):
            body = body or {}
            if path.endswith("search_collect_box_detail_list"):
                return {"result": "success", "data": {"detailList": collect_rows}}
            if path.endswith("claim_to_shop"):
                detail = int(body["detailIds"][0])
                claimed_by_detail[detail] = [str(x) for x in body.get("shopIds") or []]
                claim_calls.append({"detail": detail, "shopIds": list(claimed_by_detail[detail])})
                return {"result": "success"}
            if path.endswith("get_shop_collect_item_info"):
                detail = int(body["detailId"])
                shop_id = str(body["shopId"])
                if shop_id not in claimed_by_detail.get(detail, []):
                    return {"result": "fail", "code": "fail", "message": "未选择预发布店铺"}
                return {"result": "success", "data": {
                    "shopCollectItemInfo": saved.get((detail, shop_id), json.loads(json.dumps(base))),
                    "ossMd5": "x",
                    "claimToShopIds": [int(x) for x in claimed_by_detail.get(detail, [])],
                }}
            if path.endswith("save_shop_collect_item_info"):
                detail = int(body["detailId"])
                shop_id = str(body["shopId"])
                saved[(detail, shop_id)] = body["shopCollectItemInfo"]
                return {"result": "success"}
            raise AssertionError(path)

        pricing = {
            "sea": [
                {"id": "lh_ph", "region": "PH", "currency": "PHP", "list_price": 469, "discount_price": 304.85, "profit_margin_on_sale_pct": 15.08},
                {"id": "hb_ph", "region": "PH", "currency": "PHP", "list_price": 420, "discount_price": 273.0, "profit_margin_on_sale_pct": 10.0},
            ],
            "mx": {"region": "MX", "currency": "MXN", "list_price": 199, "discount_price": 139.3, "profit_margin_on_sale_pct": 18.0},
            "uk": {},
        }
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)
        ), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value={"pricing": pricing}
        ):
            Path(tmp, "123_tiktok_claim.json").write_text(json.dumps(claim), encoding="utf-8")
            Path(tmp, "123_miaoshou_draft.json").write_text(json.dumps(draft), encoding="utf-8")
            result = prepare_miaoshou_site_drafts("123", post=fake_post)

        self.assertTrue(result["ready"])
        self.assertIn({"detail": 456, "shopIds": ["16265910", "7676267"]}, claim_calls)
        self.assertIn({"detail": 457, "shopIds": ["15173238"]}, claim_calls)
        self.assertEqual(claimed_by_detail[456], ["16265910", "7676267"])
        self.assertEqual(claimed_by_detail[457], ["15173238"])

    def test_prepare_site_drafts_claims_all_sea_shops_for_same_detail_group(self):
        claim = {
            "claimed": True,
            "tiktok_detail_id": 456,
            "source_item_id": "123",
            "detail_group_detail_ids": {"lively_sea": 456},
            "shops": {
                "lh_ph": {
                    "region": "PH",
                    "shop": "LivelyHive",
                    "shop_id": "7676267",
                    "detail_group": "lively_sea",
                    "publish_group": "lively",
                    "warehouses": {"shopWarehouseList": [{"warehouseList": [{"warehouseId": "W1", "warehouseEffectStatus": "1", "isDefault": "1"}]}]},
                },
                "lh_my": {
                    "region": "MY",
                    "shop": "LivelyHive",
                    "shop_id": "13295169",
                    "detail_group": "lively_sea",
                    "publish_group": "lively",
                    "warehouses": {"shopWarehouseList": [{"warehouseList": [{"warehouseId": "W2", "warehouseEffectStatus": "1", "isDefault": "1"}]}]},
                },
            },
        }
        draft = {
            "second_review_approved": True,
            "draft": {
                "title": "Shared SEA detail test",
                "notes": "<img src='https://img/a.jpg'>",
                "imgUrls": ["https://img/a.jpg"],
                "weight": 0.2,
                "packageLength": 20.0,
                "packageWidth": 10.0,
                "packageHeight": 5.0,
                "mainImgVideoUrl": "",
                "itemNum": "0944",
            },
        }
        base = {
            "title": "old",
            "cid": "853256",
            "imgUrls": ["https://img/a.jpg"],
            "notes": "<img src='https://img/a.jpg'>",
            "weight": 0.2,
            "packageLength": 20.0,
            "packageWidth": 10.0,
            "packageHeight": 5.0,
            "skuPropertyList": [{"attrName": "Color", "attrValueList": []}],
            "skuMap": {";red;": {"stock": 300, "price": 0, "itemNum": "0944"}},
            "productAttributes": [],
            "productCertifications": [],
        }
        claim_calls = []
        saved_site_infos = {}

        def fake_post(path, body=None):
            body = body or {}
            if path.endswith("claim_to_shop"):
                claim_calls.append({"detail": int(body["detailIds"][0]), "shopIds": list(body.get("shopIds") or [])})
                return {"result": "success"}
            if path.endswith("get_site_collect_item_info"):
                site = body["site"]
                return {"result": "success", "data": {
                    "siteCollectItemInfo": saved_site_infos.get(site, {
                        **json.loads(json.dumps(base)),
                        "site": site,
                        "editModel": "site",
                        "collectBoxDetailShopList": [],
                    }),
                    "ossMd5": "x",
                    "claimToShopIds": [7676267, 13295169],
                }}
            if path.endswith("save_site_collect_item_info"):
                saved_site_infos[body["site"]] = body["siteCollectItemInfo"]
                return {"result": "success"}
            raise AssertionError(path)

        pricing = {
            "sea": [
                {"id": "lh_ph", "region": "PH", "currency": "PHP", "list_price": 469, "discount_price": 304.85, "profit_margin_on_sale_pct": 15.08},
                {"id": "lh_my", "region": "MY", "currency": "MYR", "list_price": 35, "discount_price": 22.75, "profit_margin_on_sale_pct": 15.57},
            ],
            "mx": {},
            "uk": {},
        }
        with TemporaryDirectory() as tmp, patch(
            "modules.sourcing.new_product_workbench.resolve_offer_key", return_value="123"
        ), patch(
            "modules.sourcing.new_product_workbench.STATE_DIR", Path(tmp)
        ), patch(
            "modules.sourcing.new_product_workbench.build_preview", return_value={"pricing": pricing}
        ):
            Path(tmp, "123_tiktok_claim.json").write_text(json.dumps(claim), encoding="utf-8")
            Path(tmp, "123_miaoshou_draft.json").write_text(json.dumps(draft), encoding="utf-8")
            result = prepare_miaoshou_site_drafts("123", post=fake_post)

        self.assertTrue(result["ready"])
        self.assertIn({"detail": 456, "shopIds": ["13295169", "7676267"]}, claim_calls)

    def test_miaoshou_source_ids_cover_1688_and_temu(self):
        self.assertEqual(
            source_item_id("https://detail.1688.com/offer/967648348081.html"),
            "967648348081",
        )
        self.assertEqual(
            source_item_id("https://www.temu.com/goods.html?goods_id=606524340571188"),
            "606524340571188",
        )

    def test_common_collect_id_input_builds_preview_from_miaoshou_detail(self):
        self.assertEqual(parse_common_collect_id("ms:123456"), "123456")

        def fake_post(path, body=None):
            if path.endswith("get_common_collect_box_detail"):
                return {
                    "result": "success",
                    "data": {
                        "editCommonCollectBoxDetail": {
                            "commonCollectBoxDetailId": 123456,
                            "title": "Imported ERP item",
                            "price": 9,
                            "stock": 20,
                            "weight": 0.4,
                            "imgUrls": ["https://img.example/main.jpg"],
                            "sourceList": [{
                                "sourceItemId": "606524340571188",
                                "source": "temu",
                                "sourceItemUrl": "https://www.temu.com/goods.html?goods_id=606524340571188",
                            }],
                        }
                    },
                }
            raise AssertionError(path)

        key, payload = import_common_collect_detail("123456", post=fake_post, state_key="123456")
        self.assertEqual(key, "123456")
        self.assertEqual(payload["normalized"]["title"], "Imported ERP item")

        result = build_preview("123456")
        self.assertEqual(result["offer_id"], "123456")
        self.assertEqual(result["review"]["title"], "Imported ERP item")
        self.assertEqual(len(result["review"]["image_actions"]), 1)

    def test_overseas_common_collect_id_becomes_material(self):
        def fake_post(path, body=None):
            if path.endswith("get_common_collect_box_detail"):
                return {
                    "result": "success",
                    "data": {
                        "editCommonCollectBoxDetail": {
                            "commonCollectBoxDetailId": 456789,
                            "title": "Temu imported item",
                            "price": 12,
                            "stock": 5,
                            "imgUrls": ["https://img.example/temu-main.jpg"],
                            "mainImgVideoUrl": "https://video.example/temu.mp4",
                            "sourceList": [{
                                "sourceItemId": "606524340571188",
                                "source": "temu",
                                "sourceItemUrl": "https://www.temu.com/goods.html?goods_id=606524340571188",
                            }],
                        }
                    },
                }
            raise AssertionError(path)

        material = extract_overseas_material_from_common_collect("456789", post=fake_post)

        self.assertEqual(material["url"], "ms:456789")
        self.assertEqual(material["source_type"], "temu")
        self.assertEqual(material["title"], "Temu imported item")
        self.assertEqual(material["images"], ["https://img.example/temu-main.jpg"])
        self.assertEqual(material["videos"], ["https://video.example/temu.mp4"])

    def test_miaoshou_detail_normalizes_media_variants_and_attributes(self):
        detail = {
            "commonCollectBoxDetailId": 12,
            "title": "source title",
            "itemNum": "ITEM-1",
            "price": 9,
            "stock": 20,
            "weight": 0.4,
            "imgUrls": ["https://img.example/main.jpg"],
            "notes": '<img src="https://img.example/detail.jpg">',
            "mainImgVideoUrl": "https://video.example/a.mp4",
            "sourceAttrs": [{"name": "材质", "value": "塑料"}],
            "skuMap": {";Red;;": {"price": 9, "stock": 10, "weight": 0.4}},
            "cateList": ["整理用具", "置物架"],
        }

        result = normalize_detail(detail, source_url="https://1688.example/item", source_id="1")

        self.assertEqual(result["images"], ["https://img.example/main.jpg", "https://img.example/detail.jpg"])
        self.assertEqual(result["skus"][0]["name"], "Red")
        self.assertEqual(result["attributes"]["材质"], "塑料")
        self.assertEqual(result["video_url"], "https://video.example/a.mp4")

    def test_miaoshou_precollect_records_failure_reason(self):
        def fake_post(path, body=None):
            if path.endswith("get_common_collect_box_list"):
                source_id = ((body or {}).get("filter") or {}).get("sourceItemIdKeyword")
                if source_id == "967648348081":
                    return {"result": "success", "data": {"detailList": []}}
                return {
                    "result": "success",
                    "data": {
                        "detailList": [{
                            "commonCollectBoxDetailId": 1,
                            "status": "fail",
                            "reason": "use plugin collection",
                            "sourceList": [{"sourceItemId": "606524340571188", "source": "temu"}],
                        }]
                    },
                }
            if path.endswith("fetch_item"):
                return {"result": "success", "data": {"sourceItemIdAndDetailIdMap": {}}}
            raise AssertionError(path)

        result = refresh_precollect(
            "967648348081",
            "https://detail.1688.com/offer/967648348081.html",
            ["https://www.temu.com/goods.html?goods_id=606524340571188"],
            force=True,
            post=fake_post,
        )

        temu = result["records"][1]
        self.assertEqual(temu["status"], "fail")
        self.assertEqual(temu["notes"], ["use plugin collection"])


if __name__ == "__main__":
    unittest.main()
