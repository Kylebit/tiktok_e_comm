"""Regression contract for the unified Miaoshou direct-store adapter.

These tests intentionally describe the public 03 boundary before the
implementation.  They must fail on the old split TikTok/Shopee/Ozon registry.
"""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from domains.channel_operations.oneclick_release_adapters import (
    prepare_oneclick_target,
    production_adapter_registry,
)
from modules.miaoshou import oneclick_release as miaoshou


EXPECTED_STOREFRONTS = {
    "tiktok:LH_PH": ("tiktok", "PH", 7676267),
    "tiktok:LH_MY": ("tiktok", "MY", 13295169),
    "tiktok:LH_TH": ("tiktok", "TH", 13295228),
    "tiktok:LH_VN": ("tiktok", "VN", 13295291),
    "tiktok:MX": ("tiktok", "MX", 16265910),
    "tiktok:GB": ("tiktok", "GB", 10204699),
    "tiktok:HB_PH": ("tiktok", "PH", 15173238),
    "tiktok:HB_MY": ("tiktok", "MY", 16770639),
    "tiktok:HB_TH": ("tiktok", "TH", 16770557),
    "tiktok:HB_VN": ("tiktok", "VN", 16783702),
    "shopee:PH": ("shopee", "PH", 7808255),
    "shopee:MY": ("shopee", "MY", 13295318),
    "shopee:TH": ("shopee", "TH", 13295319),
    "shopee:VN": ("shopee", "VN", 13295320),
    "ozon:RU": ("ozon", "OZON", 16075432),
}


@pytest.fixture(autouse=True)
def _reset_miaoshou_factories():
    miaoshou.configure_prepare_post_factory(None)
    miaoshou.configure_runtime_transport_factory(None)
    yield
    miaoshou.configure_prepare_post_factory(None)
    miaoshou.configure_runtime_transport_factory(None)


def test_all_storefronts_use_one_miaoshou_direct_store_registration():
    registry = production_adapter_registry()

    assert set(registry) == {"miaoshou-direct-store/v1"}
    registration = registry["miaoshou-direct-store/v1"]
    assert registration.adapter_name == "miaoshou-direct-store/v1"
    assert set(registration.target_labels) == set(EXPECTED_STOREFRONTS)
    assert registration.preparation_available is True
    assert registration.dispatch_available is True


def test_fixed_miaoshou_storefront_and_endpoint_matrix_is_exact():
    assert set(miaoshou.DIRECT_STORE_CONFIG) == set(EXPECTED_STOREFRONTS)
    for target, (platform, site, shop_id) in EXPECTED_STOREFRONTS.items():
        config = miaoshou.DIRECT_STORE_CONFIG[target]
        assert config["platform"] == platform
        assert config["site"] == site
        assert config["shop_id"] == shop_id
        assert config["save_path"].startswith(
            f"/open/v1/product/collect_box/{platform}/"
        )
        expected_publish_path = (
            "/open/v1/product/collect_box/tiktok/collect_box/"
            "save_move_collect_task"
            if platform == "tiktok"
            else (
                f"/open/v1/product/collect_box/{platform}/"
                "move_collect/save_move_collect_task"
            )
        )
        assert config["publish_path"] == expected_publish_path


def test_tiktok_draft_mode_matrix_preserves_the_proven_site_shop_split():
    """SEA uses site drafts; only MX/GB use shop drafts."""

    for target in (
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
    ):
        config = miaoshou.DIRECT_STORE_CONFIG[target]
        assert config["draft_mode"] == "site"
        assert config["get_path"].endswith("get_site_collect_item_info")
        assert config["save_path"].endswith("save_site_collect_item_info")

    for target in ("tiktok:MX", "tiktok:GB"):
        config = miaoshou.DIRECT_STORE_CONFIG[target]
        assert config["draft_mode"] == "shop"
        assert config["get_path"].endswith("get_shop_collect_item_info")
        assert config["save_path"].endswith("save_shop_collect_item_info")


def _expected(target):
    config = miaoshou.DIRECT_STORE_CONFIG[target]
    return {
        "common_detail_id": "7",
        "source_offer_id": "986159122616",
        "title": "Approved title",
        "item_num": "0954",
        "weight": "0.2",
        "package_cm": ["30", "20", "1"],
        "images": ["https://assets.example/one.jpg"],
        "notes": "<p>Exact</p>",
        "video_url": "",
        "selected_sku_keys": ["default"],
        "model_skus": {"default": "0954"},
        "target_label": target,
        "shop_name": str(config["shop"]),
        "shop_id": str(config["shop_id"]),
        "region": str(config["region"]),
        "platform": str(config["platform"]),
        "price": "33",
        "currency": {
            "MX": "MXN",
            "GB": "GBP",
            "PH": "PHP",
            "MY": "MYR",
            "TH": "THB",
            "VN": "VND",
            "OZON": "RUB",
        }[str(config["region"])],
    }


def _detail(target):
    expected = _expected(target)
    if expected["platform"] == "ozon":
        return {
            "detailId": 77,
            "title": expected["title"],
            "itemNum": expected["item_num"],
            "notes": expected["notes"],
            "mainImgVideoUrl": "",
            "packageInfo": {
                "depth": 30,
                "width": 20,
                "height": 1,
                "dimensionUnit": "CENTIMETER",
            },
            "weightInfo": {"weight": 0.2, "weightUnit": "KILOGRAM"},
            "skuMap": {
                "default": {
                    "itemNum": "0954",
                    "price": 33,
                    "marketPrice": 33,
                    "originPrice": 33,
                    "imgUrls": list(expected["images"]),
                }
            },
        }
    detail = {
        "detailId": 77,
        "shopId": expected["shop_id"],
        "sourceOfferId": expected["source_offer_id"],
        "title": expected["title"],
        "itemNum": expected["item_num"],
        "weight": 0.2,
        "packageLength": 30,
        "packageWidth": 20,
        "packageHeight": 1,
        "imgUrls": list(expected["images"]),
        "notes": expected["notes"],
        "mainImgVideoUrl": "",
        "skuMap": {
            "default": {
                "itemNum": "0954",
                "weight": 0.2,
                "packageLength": 30,
                "packageWidth": 20,
                "packageHeight": 1,
                "price": 33,
                "priceIncludeVat": 33,
            }
        },
    }
    if miaoshou.DIRECT_STORE_CONFIG[target]["draft_mode"] == "site":
        detail.pop("shopId")
        detail["site"] = expected["region"]
        detail["editModel"] = "site"
        detail["collectBoxDetailShopList"] = [
            {"shopId": expected["shop_id"], "site": expected["region"]}
        ]
    return detail


def _command(target):
    config = miaoshou.DIRECT_STORE_CONFIG[target]
    expected = _expected(target)
    detail = _detail(target)
    return {
        "schema_version": "oneclick-miaoshou-direct-store-command/v1",
        "kind": "DIRECT_STORE",
        "target_label": target,
        "platform": config["platform"],
        "site": config["site"],
        "source_offer_id": expected["source_offer_id"],
        "common_detail_id": expected["common_detail_id"],
        "shop_id": expected["shop_id"],
        "action": "USE_EXISTING",
        "detail_id": "77",
        "api_less": True,
        "expected": expected,
        "observed_snapshot_digest": miaoshou._digest(
            miaoshou._detail_snapshot(detail)
        ),
        "identity_binding": {
            "target_label": target,
            "shop_id": expected["shop_id"],
            "platform": config["platform"],
            "idempotency_key": f"job:{target}",
            "source_identity_digest": "a" * 64,
            "payload_digest": "b" * 64,
            "adapter_policy_digest": "c" * 64,
        },
    }


def _plan_payload(target):
    config = miaoshou.DIRECT_STORE_CONFIG[target]
    return {
        "product_id": "7",
        "seller_sku": "0954",
        "targets": ["miaoshou:COMMON", target],
        "product_facts": {
            "title": "Approved source title",
            "category": {"name": "wall sticker"},
            "weight_kg": "0.2",
            "package_cm": [30, 20, 1],
            "selected_sku_keys": ["default"],
        },
        "images": [{"image_url": "https://assets.example/one.jpg"}],
        "video_urls": [],
        "listing_copy": {
            "shopee_description_en": "Exact",
            "candidates": [{
                "channel": config["platform"],
                "site": config["region"],
                "policy_check": "passed",
                "title": "Approved title",
            }],
        },
        "pricing": {
            "selected_targets": {
                target: {
                    "store_prices": [{
                        "target_key": config["key"],
                        "list_price": "33",
                        "currency": _expected(target)["currency"],
                    }]
                }
            }
        },
        "approved_tiktok_category_decisions": (
            miaoshou.approved_tiktok_category_decisions(
                {"name": "wall sticker"},
                targets=("miaoshou:COMMON", target),
            )
            if target.startswith("tiktok:")
            else {}
        ),
    }


def _tiktok_category_decisions(targets):
    decisions = miaoshou.approved_tiktok_category_decisions(
        {"name": "wall sticker"},
        targets=("miaoshou:COMMON", *targets),
    )
    assert isinstance(decisions, dict)
    return decisions


def test_legacy_missing_category_field_backfills_but_present_malformed_fails():
    target = "tiktok:GB"
    payload = _plan_payload(target)
    payload.pop("approved_tiktok_category_decisions")

    assert miaoshou._approved_tiktok_category_id(
        payload,
        target=target,
    ) == "600338"

    payload["product_facts"]["category"] = {"name": "unmapped"}
    assert miaoshou._approved_tiktok_category_id(
        payload,
        target=target,
    ) is None

    payload = _plan_payload(target)
    payload["approved_tiktok_category_decisions"][target][
        "evidence_digest"
    ] = "e" * 64
    assert miaoshou._approved_tiktok_category_id(
        payload,
        target=target,
    ) is None

    payload["approved_tiktok_category_decisions"] = {target: {}}
    assert miaoshou._approved_tiktok_category_id(
        payload,
        target=target,
    ) is None

    for malformed in (None, [], "legacy"):
        payload = _plan_payload(target)
        payload["approved_tiktok_category_decisions"] = malformed
        assert miaoshou._approved_tiktok_category_id(
            payload,
            target=target,
        ) is None


def test_present_tiktok_category_semantic_drift_fails_closed():
    target = "tiktok:GB"
    payload = _plan_payload(target)
    payload["approved_tiktok_category_decisions"][target][
        "category_id"
    ] = "600339"

    assert miaoshou._approved_tiktok_category_id(
        payload,
        target=target,
    ) is None


def test_present_tiktok_category_semantic_drift_makes_zero_storefront_calls():
    target = "tiktok:GB"
    payload = _plan_payload(target)
    payload["approved_tiktok_category_decisions"][target][
        "category_id"
    ] = "600339"
    calls = []

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=False,
        approved_plan_payload=payload,
        approved_targets=(target,),
        post=lambda path, body: calls.append((path, body)),
    )

    assert calls == []
    assert result["external_writes"] == ()
    assert result["external_write_count"] == 0
    assert result["target_results"] == [
        {
            "target_label": target,
            "status": "FAILED",
            "error_code": "category_not_approved",
            "detail_digest": result["target_results"][0]["detail_digest"],
        }
    ]
    assert len(result["target_results"][0]["detail_digest"]) == 64

def test_collectbox_tiktok_repairs_vendor_auto_converted_price_once_then_reads_exact():
    """A vendor CNY-auto-price readback gets one bounded repair, not success."""

    target = "tiktok:MX"
    payload = _plan_payload(target)
    payload["pricing"]["selected_targets"][target]["store_prices"][0][
        "list_price"
    ] = "286"
    payload["approved_tiktok_category_decisions"] = (
        _tiktok_category_decisions((target,))
    )
    calls = []
    detail = _detail(target)
    detail["cid"] = "600338"
    save_count = 0
    get_count = 0
    web_calls = []

    def post(path, body):
        nonlocal detail, save_count, get_count
        calls.append((path, deepcopy(body)))
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        if path.endswith("get_shop_collect_item_info"):
            get_count += 1
            return {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": deepcopy(detail),
                    "ossMd5": f"md5-{get_count}",
                },
            }
        if path.endswith("save_shop_collect_item_info"):
            save_count += 1
            detail = deepcopy(body["shopCollectItemInfo"])
            detail["detailId"] = int(body["detailId"])
            detail["shopId"] = str(body["shopId"])
            detail["sourceOfferId"] = "986159122616"
            detail["cid"] = "600338"
            if save_count == 1:
                # Sanitized live shape from Offer 3846511157: Miaoshou kept
                # the common CNY 10 auto-conversion instead of approved MXN 286.
                detail["skuMap"]["default"]["price"] = 25.66
                detail["skuMap"]["default"]["priceIncludeVat"] = 25.66
            return {"result": "success"}
        raise AssertionError(path)

    def web_post(path, body):
        web_calls.append((path, deepcopy(body)))
        assert path == miaoshou.WEB_BATCH_SET_PRICE_PATH
        detail["skuMap"]["default"]["price"] = 286
        detail["skuMap"]["default"]["priceIncludeVat"] = 286
        return {"success": True}

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=(target,),
        post=post,
        web_post=web_post,
    )

    saves = [
        body
        for path, body in calls
        if path.endswith("save_shop_collect_item_info")
    ]
    assert len(saves) == 1
    assert saves[0]["ossMd5"] == "md5-1"
    assert all(
        body["shopCollectItemInfo"]["skuMap"]["default"]["price"] == 286
        and body["shopCollectItemInfo"]["skuMap"]["default"][
            "priceIncludeVat"
        ]
        == 286
        for body in saves
    )
    assert web_calls == [
        (
            miaoshou.WEB_BATCH_SET_PRICE_PATH,
            {
                "collectBoxDetailIds": [77],
                "site": "MX",
                "priceConfig": {
                    "price": {
                        "modifyMode": "newValue",
                        "newValue": 286,
                    }
                },
            },
        )
    ]
    assert result["target_results"] == [
        {
            "target_label": target,
            "status": "REPAIRED_SUCCEEDED",
            "error_code": None,
            "detail_digest": None,
        }
    ]
    assert result["checks"]["approved_prices_exact"] is True
    assert all("save_move_collect_task" not in path for path, _ in calls)


def test_collectbox_tiktok_sea_uses_site_draft_payload_and_exact_readback():
    target = "tiktok:LH_MY"
    payload = _plan_payload(target)
    detail = _detail(target)
    detail.pop("shopId", None)
    detail["site"] = "MY"
    detail["editModel"] = "site"
    detail["cid"] = "600338"
    detail["collectBoxDetailShopList"] = [
        {"shopId": str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])}
    ]
    calls = []

    def post(path, body):
        nonlocal detail
        calls.append((path, deepcopy(body)))
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        if path.endswith("get_site_collect_item_info"):
            assert body == {"detailId": 77, "site": "MY"}
            return {
                "result": "success",
                "data": {
                    "siteCollectItemInfo": deepcopy(detail),
                    "ossMd5": "site-md5",
                },
            }
        if path.endswith("save_site_collect_item_info"):
            assert body["detailId"] == 77
            assert body["site"] == "MY"
            assert body["ossMd5"] == "site-md5"
            assert "shopId" not in body
            assert "shopCollectItemInfo" not in body
            detail = deepcopy(body["siteCollectItemInfo"])
            detail["detailId"] = 77
            detail["site"] = "MY"
            return {"result": "success"}
        raise AssertionError(path)

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=(target,),
        post=post,
    )

    assert result["target_results"][0]["status"] == "SUCCEEDED"
    assert [path for path, _ in calls] == [
        miaoshou.SHOP_CLAIM_PATH,
        miaoshou.DIRECT_STORE_CONFIG[target]["get_path"],
        miaoshou.DIRECT_STORE_CONFIG[target]["save_path"],
        miaoshou.DIRECT_STORE_CONFIG[target]["get_path"],
    ]


def test_collectbox_tiktok_blank_gb_category_fails_target_and_continues_next_site():
    """A blank GB cid is not guessed from another market and cannot stop MX."""

    targets = ("tiktok:GB", "tiktok:MX")
    payload = _plan_payload(targets[0])
    payload["targets"] = ["miaoshou:COMMON", *targets]
    payload["listing_copy"]["candidates"].append(
        {
            "channel": "tiktok",
            "site": "MX",
            "policy_check": "passed",
            "title": "Approved MX title",
        }
    )
    payload["pricing"]["selected_targets"] = {
        "tiktok:GB": {
            "store_prices": [
                {"target_key": "gb", "list_price": "15", "currency": "GBP"}
            ]
        },
        "tiktok:MX": {
            "store_prices": [
                {"target_key": "mx", "list_price": "286", "currency": "MXN"}
            ]
        },
    }
    payload["approved_tiktok_category_decisions"] = (
        _tiktok_category_decisions(targets)
    )
    payload["approved_tiktok_category_decisions"].pop("tiktok:GB")
    calls = []
    details = {
        str(miaoshou.DIRECT_STORE_CONFIG["tiktok:GB"]["shop_id"]): {
            **_detail("tiktok:GB"),
            "cid": "",
        },
        str(miaoshou.DIRECT_STORE_CONFIG["tiktok:MX"]["shop_id"]): {
            **_detail("tiktok:MX"),
            "cid": "600338",
        },
    }
    details[str(miaoshou.DIRECT_STORE_CONFIG["tiktok:MX"]["shop_id"])][
        "detailId"
    ] = 78

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == miaoshou.DETAIL_CREATE_PATH:
            return {
                "result": "success",
                "data": {"platformCollectBoxDetailIdMap": {"tiktok": {"7": 78}}},
            }
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        if path.endswith("get_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            return {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": deepcopy(details[shop_id]),
                    "ossMd5": "md5",
                },
            }
        if path.endswith("save_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            cid = details[shop_id].get("cid")
            details[shop_id] = deepcopy(body["shopCollectItemInfo"])
            details[shop_id]["detailId"] = int(body["detailId"])
            details[shop_id]["shopId"] = shop_id
            details[shop_id]["sourceOfferId"] = "986159122616"
            # Vendor preserves blank GB category.  MX keeps its audited cid.
            details[shop_id]["cid"] = cid
            return {"result": "success"}
        raise AssertionError(path)

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=targets,
        post=post,
    )

    assert result["target_results"][0]["target_label"] == "tiktok:GB"
    assert result["target_results"][0]["status"] == "FAILED"
    assert result["target_results"][0]["error_code"] == "category_not_approved"
    assert len(result["target_results"][0]["detail_digest"]) == 64
    assert result["target_results"][1] == {
        "target_label": "tiktok:MX",
        "status": "SUCCEEDED",
        "error_code": None,
        "detail_digest": None,
    }
    claimed_shop_ids = [
        str(body["shopIds"][0])
        for path, body in calls
        if path == miaoshou.SHOP_CLAIM_PATH
    ]
    assert claimed_shop_ids == [
        str(miaoshou.DIRECT_STORE_CONFIG["tiktok:MX"]["shop_id"])
    ]
    assert all("save_move_collect_task" not in path for path, _ in calls)


def test_collectbox_tiktok_failed_bounded_price_repair_continues_next_site():
    targets = ("tiktok:MX", "tiktok:GB")
    payload = _plan_payload(targets[0])
    payload["targets"] = ["miaoshou:COMMON", *targets]
    payload["listing_copy"]["candidates"].append(
        {
            "channel": "tiktok",
            "site": "GB",
            "policy_check": "passed",
            "title": "Approved GB title",
        }
    )
    payload["pricing"]["selected_targets"] = {
        "tiktok:MX": {
            "store_prices": [
                {"target_key": "mx", "list_price": "286", "currency": "MXN"}
            ]
        },
        "tiktok:GB": {
            "store_prices": [
                {"target_key": "gb", "list_price": "15", "currency": "GBP"}
            ]
        },
    }
    payload["approved_tiktok_category_decisions"] = (
        _tiktok_category_decisions(targets)
    )
    details = {}
    for index, target in enumerate(targets):
        detail = _detail(target)
        detail["detailId"] = 77 + index
        detail["cid"] = "600338"
        details[str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])] = detail
    calls = []
    web_calls = []
    target_by_site = {
        str(miaoshou.DIRECT_STORE_CONFIG[target]["site"]): target
        for target in targets
    }

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == miaoshou.DETAIL_CREATE_PATH:
            return {
                "result": "success",
                "data": {"platformCollectBoxDetailIdMap": {"tiktok": {"7": 78}}},
            }
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        if path.endswith("get_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            return {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": deepcopy(details[shop_id]),
                    "ossMd5": f"md5-{len(calls)}",
                },
            }
        if path.endswith("save_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            details[shop_id] = deepcopy(body["shopCollectItemInfo"])
            details[shop_id]["detailId"] = int(body["detailId"])
            details[shop_id]["shopId"] = shop_id
            details[shop_id]["sourceOfferId"] = "986159122616"
            details[shop_id]["cid"] = "600338"
            if shop_id == str(
                miaoshou.DIRECT_STORE_CONFIG["tiktok:MX"]["shop_id"]
            ):
                # The vendor keeps overriding the approved MXN price even
                # after the single repair.  A third write is forbidden.
                details[shop_id]["skuMap"]["default"]["price"] = 6.05
                details[shop_id]["skuMap"]["default"][
                    "priceIncludeVat"
                ] = 6.05
            return {"result": "success"}
        raise AssertionError(path)

    def web_post(path, body):
        web_calls.append((path, deepcopy(body)))
        target = target_by_site[str(body["site"])]
        shop_id = str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
        if target != "tiktok:MX":
            approved_price = int(
                payload["pricing"]["selected_targets"][target][
                    "store_prices"
                ][0]["list_price"]
            )
            details[shop_id]["skuMap"]["default"]["price"] = approved_price
            details[shop_id]["skuMap"]["default"][
                "priceIncludeVat"
            ] = approved_price
        return {"success": True}

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=targets,
        post=post,
        web_post=web_post,
    )

    assert result["target_results"][0]["status"] == "FAILED"
    assert result["target_results"][0]["error_code"] == (
        "approved_price_readback_mismatch"
    )
    assert result["target_results"][1]["status"] == "SUCCEEDED"
    save_shop_ids = [
        str(body["shopId"])
        for path, body in calls
        if path.endswith("save_shop_collect_item_info")
    ]
    assert save_shop_ids.count(
        str(miaoshou.DIRECT_STORE_CONFIG["tiktok:MX"]["shop_id"])
    ) == 1
    assert save_shop_ids.count(
        str(miaoshou.DIRECT_STORE_CONFIG["tiktok:GB"]["shop_id"])
    ) == 1
    assert [body["site"] for _path, body in web_calls] == ["MX"]
    assert all("save_move_collect_task" not in path for path, _ in calls)


LIVE_SIX_TIKTOK_TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
)


def _run_live_six_site_tiktok_drift(
    *, category_decisions, use_web_price_repair=False, price_batch_faults=None
):
    targets = LIVE_SIX_TIKTOK_TARGETS
    approved_prices = {
        "tiktok:LH_PH": ("523", "PHP"),
        "tiktok:LH_MY": ("46", "MYR"),
        "tiktok:LH_TH": ("386", "THB"),
        "tiktok:LH_VN": ("408000", "VND"),
        "tiktok:MX": ("286", "MXN"),
        "tiktok:GB": ("15", "GBP"),
    }
    vendor_cny10_prices = {
        "tiktok:LH_PH": 90.66,
        "tiktok:LH_MY": 6.05,
        "tiktok:LH_TH": 49.47,
        "tiktok:LH_VN": 38939.8,
        "tiktok:MX": 25.66,
        "tiktok:GB": 1.1,
    }
    observed_categories = {
        target: ("" if target in {"tiktok:LH_VN", "tiktok:GB"} else "600338")
        for target in targets
    }

    payload = _plan_payload(targets[0])
    payload["targets"] = ["miaoshou:COMMON", *targets]
    payload["listing_copy"]["candidates"] = [
        {
            "channel": "tiktok",
            "site": str(miaoshou.DIRECT_STORE_CONFIG[target]["region"]),
            "policy_check": "passed",
            "title": f"Approved {target} title",
        }
        for target in targets
    ]
    payload["pricing"]["selected_targets"] = {
        target: {
            "store_prices": [
                {
                    "target_key": str(
                        miaoshou.DIRECT_STORE_CONFIG[target]["key"]
                    ),
                    "list_price": price,
                    "currency": currency,
                }
            ]
        }
        for target, (price, currency) in approved_prices.items()
    }
    payload["approved_tiktok_category_decisions"] = category_decisions

    details = {}
    target_by_shop_id = {}
    for index, target in enumerate(targets):
        shop_id = str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
        detail = _detail(target)
        detail["detailId"] = 77 + index
        detail["cid"] = observed_categories[target]
        details[shop_id] = detail
        target_by_shop_id[shop_id] = target

    calls = []
    web_calls = []
    save_counts = {target: 0 for target in targets}
    save_bodies = {target: [] for target in targets}
    create_count = 0
    target_by_site = {
        str(miaoshou.DIRECT_STORE_CONFIG[target]["site"]): target
        for target in targets
    }

    def post(path, body):
        nonlocal create_count
        calls.append((path, deepcopy(body)))
        if path == miaoshou.DETAIL_CREATE_PATH:
            create_count += 1
            return {
                "result": "success",
                "data": {
                    "platformCollectBoxDetailIdMap": {
                        "tiktok": {"7": 77 + create_count}
                    }
                },
            }
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        if path.endswith("get_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            return {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": deepcopy(details[shop_id]),
                    "ossMd5": f"md5-{len(calls)}",
                },
            }
        if path.endswith("get_site_collect_item_info"):
            target = target_by_site[str(body["site"])]
            shop_id = str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
            return {
                "result": "success",
                "data": {
                    "siteCollectItemInfo": deepcopy(details[shop_id]),
                    "ossMd5": f"md5-{len(calls)}",
                },
            }
        if path.endswith("save_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            target = target_by_shop_id[shop_id]
            save_counts[target] += 1
            save_bodies[target].append(deepcopy(body))
            details[shop_id] = deepcopy(body["shopCollectItemInfo"])
            details[shop_id]["detailId"] = int(body["detailId"])
            details[shop_id]["shopId"] = shop_id
            details[shop_id]["sourceOfferId"] = "986159122616"
            if save_counts[target] == 1:
                converted = vendor_cny10_prices[target]
                details[shop_id]["skuMap"]["default"]["price"] = converted
                details[shop_id]["skuMap"]["default"][
                    "priceIncludeVat"
                ] = converted
            return {"result": "success"}
        if path.endswith("save_site_collect_item_info"):
            target = target_by_site[str(body["site"])]
            shop_id = str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
            save_counts[target] += 1
            save_bodies[target].append(deepcopy(body))
            details[shop_id] = deepcopy(body["siteCollectItemInfo"])
            details[shop_id]["detailId"] = int(body["detailId"])
            details[shop_id]["site"] = str(body["site"])
            details[shop_id]["sourceOfferId"] = "986159122616"
            return {"result": "success"}
        raise AssertionError(path)

    def web_post(path, body):
        web_calls.append((path, deepcopy(body)))
        assert path == (
            "/api/platform/tiktok/move/collect_box/batchSetPrice"
        )
        target = target_by_site[str(body["site"])]
        price, _currency = approved_prices[target]
        detail_id = details[
            str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
        ]["detailId"]
        assert body == {
            "collectBoxDetailIds": [detail_id],
            "site": str(miaoshou.DIRECT_STORE_CONFIG[target]["site"]),
            "priceConfig": {
                "price": {
                    "modifyMode": "newValue",
                    "newValue": int(price),
                }
            },
        }
        fault = (price_batch_faults or {}).get(target)
        if fault == "exception":
            raise RuntimeError("sanitized batch price transport failure")
        if fault == "rejected":
            return {"success": False, "code": "REJECTED"}
        shop_id = str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
        details[shop_id]["skuMap"]["default"]["price"] = int(price)
        details[shop_id]["skuMap"]["default"]["priceIncludeVat"] = int(
            price
        )
        return {"success": True}

    arguments = dict(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=targets,
        post=post,
    )
    if use_web_price_repair:
        arguments["web_post"] = web_post
    result = miaoshou.prepare_selected_platform_collectbox(**arguments)
    return result, calls, web_calls, save_counts, save_bodies


def test_collectbox_tiktok_live_six_site_uses_effective_repair_and_continues():
    """A repair must change the rejected payload and process all six sites."""

    targets = LIVE_SIX_TIKTOK_TARGETS
    result, calls, web_calls, save_counts, _ = _run_live_six_site_tiktok_drift(
        category_decisions=_tiktok_category_decisions(targets),
        use_web_price_repair=True,
    )

    outcomes = {
        row["target_label"]: row for row in result["target_results"]
    }
    assert tuple(row["target_label"] for row in result["target_results"]) == targets
    assert save_counts == {target: 1 for target in targets}
    claimed_shop_ids = [
        str(body["shopIds"][0])
        for path, body in calls
        if path == miaoshou.SHOP_CLAIM_PATH
    ]
    assert claimed_shop_ids == [
        str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
        for target in targets
    ]
    assert all("save_move_collect_task" not in path for path, _ in calls)
    assert [body["site"] for _path, body in web_calls] == [
        str(miaoshou.DIRECT_STORE_CONFIG[target]["site"])
        for target in ("tiktok:MX", "tiktok:GB")
    ]
    assert {
        target: (outcomes[target]["status"], outcomes[target]["error_code"])
        for target in targets
    } == {
        "tiktok:LH_PH": ("SUCCEEDED", None),
        "tiktok:LH_MY": ("SUCCEEDED", None),
        "tiktok:LH_TH": ("SUCCEEDED", None),
        "tiktok:LH_VN": ("SUCCEEDED", None),
        "tiktok:MX": ("REPAIRED_SUCCEEDED", None),
        "tiktok:GB": ("REPAIRED_SUCCEEDED", None),
    }
    assert all(
        outcomes[target]["detail_digest"] is None
        for target in targets
    )


@pytest.mark.parametrize(
    ("fault", "error_code"),
    [
        ("rejected", "approved_price_batch_repair_rejected"),
        ("exception", "approved_price_batch_repair_unknown"),
    ],
)
def test_collectbox_tiktok_batch_price_failure_is_local_and_continues(
    fault, error_code
):
    targets = LIVE_SIX_TIKTOK_TARGETS
    result, calls, web_calls, save_counts, _ = (
        _run_live_six_site_tiktok_drift(
            category_decisions=_tiktok_category_decisions(targets),
            use_web_price_repair=True,
            price_batch_faults={"tiktok:MX": fault},
        )
    )

    outcomes = {
        row["target_label"]: row for row in result["target_results"]
    }
    assert tuple(row["target_label"] for row in result["target_results"]) == targets
    assert save_counts == {target: 1 for target in targets}
    assert outcomes["tiktok:LH_MY"]["status"] == "SUCCEEDED"
    assert outcomes["tiktok:LH_TH"]["status"] == "SUCCEEDED"
    assert outcomes["tiktok:LH_VN"]["status"] == "SUCCEEDED"
    assert outcomes["tiktok:MX"]["status"] == "FAILED"
    assert outcomes["tiktok:MX"]["error_code"] == error_code
    assert outcomes["tiktok:GB"]["status"] == "REPAIRED_SUCCEEDED"
    assert [body["site"] for _path, body in web_calls] == [
        str(miaoshou.DIRECT_STORE_CONFIG[target]["site"])
        for target in ("tiktok:MX", "tiktok:GB")
    ]
    assert [
        str(body["shopIds"][0])
        for path, body in calls
        if path == miaoshou.SHOP_CLAIM_PATH
    ] == [
        str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
        for target in targets
    ]
    assert all("save_move_collect_task" not in path for path, _ in calls)


def test_collectbox_default_web_business_rejection_is_known_and_continues(
    monkeypatch,
):
    from modules.miaoshou.client import MiaoshouBusinessRejectedError

    targets = LIVE_SIX_TIKTOK_TARGETS
    monkeypatch.setattr(
        "modules.miaoshou.client.ensure_web_batch_price_auth_available",
        lambda: None,
    )

    def rejected_request_web(_method, _path, **_kwargs):
        raise MiaoshouBusinessRejectedError(
            "price rejected", code="PRICE_REJECTED"
        )

    monkeypatch.setattr(
        "modules.miaoshou.client.request_web",
        rejected_request_web,
    )
    result, calls, web_calls, save_counts, _ = (
        _run_live_six_site_tiktok_drift(
            category_decisions=_tiktok_category_decisions(targets),
        )
    )

    assert web_calls == []
    assert save_counts == {target: 1 for target in targets}
    assert tuple(
        row["target_label"] for row in result["target_results"]
    ) == targets
    outcomes = {
        row["target_label"]: row for row in result["target_results"]
    }
    assert all(
        outcomes[target]["status"] == "SUCCEEDED"
        for target in targets[:4]
    )
    assert all(
        outcomes[target]["status"] == "FAILED"
        and outcomes[target]["error_code"]
        == "approved_price_batch_repair_rejected"
        for target in targets[4:]
    )
    assert result["external_write_count"] is not None
    assert all("save_move_collect_task" not in path for path, _ in calls)


def test_collectbox_tiktok_live_six_site_missing_category_approval_is_local():
    """Missing approvals fail every site without aborting the whole batch."""

    targets = LIVE_SIX_TIKTOK_TARGETS
    result, calls, web_calls, save_counts, _ = _run_live_six_site_tiktok_drift(
        category_decisions={},
        use_web_price_repair=True,
    )

    assert tuple(row["target_label"] for row in result["target_results"]) == targets
    assert save_counts == {target: 0 for target in targets}
    assert calls == []
    assert {
        target: (row["status"], row["error_code"])
        for target, row in (
            (row["target_label"], row) for row in result["target_results"]
        )
    } == {
        target: ("FAILED", "category_not_approved") for target in targets
    }
    assert all("save_move_collect_task" not in path for path, _ in calls)
    assert web_calls == []


def test_shopee_simple_description_preserves_approved_cnsc_master_text():
    description = (
        "Product overview\n"
        "Decorative wall sticker made of PVC.\n\n"
        "Verified details\n"
        "- Material: PVC\n\n"
        "Suitable spaces\n"
        "Living room and bedroom"
    )
    payload = _plan_payload("shopee:MY")
    payload["listing_copy"]["shopee_description_en"] = description

    expected = miaoshou._approved_site(
        payload,
        target="shopee:MY",
        config=miaoshou.DIRECT_STORE_CONFIG["shopee:MY"],
        source_offer_id="986159122616",
    )
    updated = miaoshou._apply_expected(_detail("shopee:MY"), expected)

    assert expected["simple_description"] == description
    assert updated["notesText"] == description


def _live_shaped_plan_payload(target):
    """Sanitized shape captured from approved Offer 3846511157.

    Values are synthetic; only the server-owned nesting and cardinality match
    the production payload.
    """
    config = miaoshou.DIRECT_STORE_CONFIG[target]
    payload = _plan_payload(target)
    payload["product_id"] = "3846511157"
    payload["listing_copy"]["candidates"] = [
        {
            "channel": "ozon",
            "site": "RU",
            "policy_check": "passed",
            "title": "Approved Ozon title",
        },
        {
            "channel": "shopee",
            "site": "CNSC",
            "policy_check": "passed",
            "title": "Approved Shopee master title",
        },
        {
            "channel": "tiktok",
            "site": config["region"],
            "policy_check": "passed",
            "title": "Approved TikTok title",
        },
    ]
    if target.startswith("shopee:"):
        region = target.split(":", 1)[1]
        payload["pricing"]["selected_targets"][target] = {
            "selected_source_target_key": f"lh_{region.lower()}",
            "target_site": region,
            "derived_preview": {
                "exchange_rate_cny_per_local": 1,
                "global_original_price_cny": 8,
                "local_original_price": 33,
                "source_currency": "PHP",
            },
            "write_fields": ["global.original_price"],
        }
    elif target == "ozon:RU":
        payload["pricing"]["selected_targets"][target] = {
            "selected_source_target_key": "lh_ph",
            "target_site": "RU",
            "derived_preview": {
                "exchange_rate_cny_per_local": 1,
                "old_price_cny": 56,
                "price_cny": 33,
                "source_currency": "PHP",
            },
            "write_fields": ["draft.price", "draft.old_price"],
        }
    return payload


def _prepare_request(target):
    return SimpleNamespace(
        target_label=target,
        idempotency_key=f"job:{target}",
        source_identity={
            "schema_version": "source-product-identity/v1",
            "source_offer_id": "986159122616",
            "identity_digest": "a" * 64,
        },
        source_identity_digest="a" * 64,
        payload_digest="b" * 64,
        adapter_policy_digest="c" * 64,
        immutable_plan_payload=_plan_payload(target),
    )


class DirectStoreFake:
    def __init__(
        self, target, *, malformed_publish=False, existing=True
    ):
        self.target = target
        self.config = miaoshou.DIRECT_STORE_CONFIG[target]
        self.detail = _detail(target)
        self.malformed_publish = malformed_publish
        self.existing = existing
        self.calls = []

    def post(self, path, body):
        self.calls.append((path, deepcopy(body)))
        if path == self.config["search_path"]:
            rows = (
                [{
                    "collectBoxDetailId": 77,
                    "commonCollectBoxDetailId": 7,
                    "site": self.config["site"],
                }]
                if self.existing
                else []
            )
            return {
                "result": "success",
                "data": {
                    "detailList": rows,
                    "totalCount": len(rows),
                    "hasNextPage": False,
                },
            }
        if path == miaoshou.DETAIL_CREATE_PATH:
            self.existing = True
            return {
                "result": "success",
                "data": {
                    "platformCollectBoxDetailIdMap": {
                        self.config["platform"]: {"7": 77}
                    }
                },
            }
        if path == self.config["get_path"]:
            field = {
                "tiktok": (
                    "siteCollectItemInfo"
                    if self.config["draft_mode"] == "site"
                    else "shopCollectItemInfo"
                ),
                "shopee": "siteDetailSimpleData",
                "ozon": "siteCollectItemInfo",
            }[self.config["platform"]]
            return {
                "result": "success",
                "data": {
                    field: deepcopy(self.detail),
                    "ossMd5": (
                        "" if self.config["platform"] == "ozon" else "md5"
                    ),
                },
            }
        if path == self.config["save_path"]:
            field = {
                "tiktok": (
                    "siteCollectItemInfo"
                    if self.config["draft_mode"] == "site"
                    else "shopCollectItemInfo"
                ),
                "shopee": "siteDetailSimpleData",
                "ozon": "siteCollectItemInfo",
            }[self.config["platform"]]
            self.detail = deepcopy(body[field])
            self.detail["detailId"] = 77
            if self.config["platform"] == "tiktok":
                if self.config["draft_mode"] == "site":
                    self.detail["site"] = str(self.config["site"])
                    self.detail["editModel"] = "site"
                else:
                    self.detail["shopId"] = str(self.config["shop_id"])
                self.detail["sourceOfferId"] = "986159122616"
            return {"result": "success"}
        if path == self.config["publish_path"]:
            return [] if self.malformed_publish else {"result": "success"}
        raise AssertionError(path)


def test_collectbox_tiktok_writes_selected_store_price_without_publish():
    target = "tiktok:MX"
    payload = _plan_payload(target)
    payload["pricing"]["selected_targets"][target]["store_prices"][0][
        "list_price"
    ] = "286"
    fake = DirectStoreFake(target)

    def post(path, body):
        if path == miaoshou.SHOP_CLAIM_PATH:
            fake.calls.append((path, deepcopy(body)))
            return {"result": "success"}
        return fake.post(path, body)

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=(target,),
        post=post,
    )

    save = next(
        body for path, body in fake.calls if path == fake.config["save_path"]
    )
    sku = save["shopCollectItemInfo"]["skuMap"]["default"]
    assert sku["price"] == 286
    assert sku["priceIncludeVat"] == 286
    assert result["target_count"] == 1
    assert result["checks"]["approved_prices_exact"] is True
    assert fake.config["publish_path"] not in [path for path, _ in fake.calls]


def test_collectbox_tiktok_accepts_latest_draft_opaque_variant_key_by_exact_model_sku():
    """Miaoshou rewrites the claimed draft's SKU-map key to an opaque ID.

    The approved model SKU remains exact and is the only safe binding between
    the approved variant and the latest platform draft.  Historical drafts do
    not participate in this operation because the claim response already
    supplies the exact latest detail identity.
    """

    target = "tiktok:LH_PH"
    payload = _plan_payload(target)
    fake = DirectStoreFake(target)
    original_row = fake.detail["skuMap"].pop("default")
    fake.detail["skuMap"][";a00a0f90f5;"] = original_row

    def post(path, body):
        if path == miaoshou.SHOP_CLAIM_PATH:
            fake.calls.append((path, deepcopy(body)))
            return {"result": "success"}
        return fake.post(path, body)

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=(target,),
        post=post,
    )

    save = next(
        body for path, body in fake.calls if path == fake.config["save_path"]
    )
    draft = save["siteCollectItemInfo"]
    assert set(draft["skuMap"]) == {
        ";a00a0f90f5;"
    }
    assert draft["skuMap"][";a00a0f90f5;"][
        "itemNum"
    ] == "0954"
    assert result["checks"]["readback_exact"] is True
    assert fake.config["publish_path"] not in [path for path, _ in fake.calls]


def test_collectbox_tiktok_rejects_opaque_variant_with_wrong_model_sku():
    target = "tiktok:LH_PH"
    payload = _plan_payload(target)
    fake = DirectStoreFake(target)
    original_row = fake.detail["skuMap"].pop("default")
    original_row["itemNum"] = "different-model"
    fake.detail["skuMap"][";a00a0f90f5;"] = original_row

    def post(path, body):
        if path == miaoshou.SHOP_CLAIM_PATH:
            fake.calls.append((path, deepcopy(body)))
            return {"result": "success"}
        return fake.post(path, body)

    with pytest.raises(miaoshou.MiaoshouCollectBoxPreparationError) as captured:
        miaoshou.prepare_selected_platform_collectbox(
            platform="tiktok",
            common_detail_id="7",
            initial_platform_detail_id="77",
            initial_claim_written=True,
            approved_plan_payload=payload,
            approved_targets=(target,),
            post=post,
        )

    assert captured.value.external_writes == (
        "miaoshou:collectbox:claim:tiktok",
        "miaoshou:collectbox:tiktok:shop:claim:tiktok:LH_PH",
    )
    assert captured.value.external_write_count == 2
    assert fake.config["save_path"] not in [path for path, _ in fake.calls]


def test_collectbox_tiktok_writes_each_selected_country_and_approved_price():
    targets = ("tiktok:MX", "tiktok:GB")
    payload = _plan_payload(targets[0])
    payload["targets"] = ["miaoshou:COMMON", *targets]
    payload["approved_tiktok_category_decisions"] = (
        _tiktok_category_decisions(targets)
    )
    payload["listing_copy"]["candidates"].append(
        {
            "channel": "tiktok",
            "site": "GB",
            "policy_check": "passed",
            "title": "Approved GB title",
        }
    )
    payload["pricing"]["selected_targets"] = {
        "tiktok:MX": {
            "store_prices": [
                {"target_key": "mx", "list_price": "286", "currency": "MXN"}
            ]
        },
        "tiktok:GB": {
            "store_prices": [
                {"target_key": "gb", "list_price": "42", "currency": "GBP"}
            ]
        },
    }
    calls = []
    details = {
        str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"]): _detail(target)
        for target in targets
    }
    details[str(miaoshou.DIRECT_STORE_CONFIG["tiktok:GB"]["shop_id"])][
        "detailId"
    ] = 78

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == miaoshou.DETAIL_CREATE_PATH:
            return {
                "result": "success",
                "data": {"platformCollectBoxDetailIdMap": {"tiktok": {"7": 78}}},
            }
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        if path.endswith("get_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            return {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": deepcopy(details[shop_id]),
                    "ossMd5": "md5",
                },
            }
        if path.endswith("save_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            details[shop_id] = deepcopy(body["shopCollectItemInfo"])
            details[shop_id]["detailId"] = int(body["detailId"])
            details[shop_id]["shopId"] = shop_id
            return {"result": "success"}
        raise AssertionError(path)

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=targets,
        post=post,
    )

    saved = {
        str(body["shopId"]): body["shopCollectItemInfo"]["skuMap"]["default"]["price"]
        for path, body in calls
        if path.endswith("save_shop_collect_item_info")
    }
    assert saved == {
        str(miaoshou.DIRECT_STORE_CONFIG["tiktok:MX"]["shop_id"]): 286,
        str(miaoshou.DIRECT_STORE_CONFIG["tiktok:GB"]["shop_id"]): 42,
    }
    assert result["target_count"] == 2
    assert result["platform_detail_count"] == 2
    assert all("save_move_collect_task" not in path for path, _ in calls)


def test_collectbox_tiktok_first_target_unknown_does_not_skip_later_countries():
    """One target fault must not truncate the approved storefront set."""

    targets = (
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
        "tiktok:MX",
        "tiktok:GB",
    )
    payload = _plan_payload(targets[0])
    payload["targets"] = ["miaoshou:COMMON", *targets]
    payload["listing_copy"]["candidates"] = [
        {
            "channel": "tiktok",
            "site": miaoshou.DIRECT_STORE_CONFIG[target]["region"],
            "policy_check": "passed",
            "title": f"Approved {target} title",
        }
        for target in targets
    ]
    payload["pricing"]["selected_targets"] = {
        target: {
            "store_prices": [{
                "target_key": miaoshou.DIRECT_STORE_CONFIG[target]["key"],
                "list_price": "33",
                "currency": _expected(target)["currency"],
            }]
        }
        for target in targets
    }
    payload["approved_tiktok_category_decisions"] = (
        _tiktok_category_decisions(targets)
    )
    payload["approved_tiktok_category_decisions"].pop(targets[-1])
    calls = []
    detail_ids = {target: 77 + index for index, target in enumerate(targets)}
    details = {}
    for target in targets:
        detail = _detail(target)
        detail["detailId"] = detail_ids[target]
        details[str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])] = detail
    created_by_serial = {
        index + 1: detail_ids[target]
        for index, target in enumerate(targets)
        if index > 0
    }
    target_by_site = {
        str(miaoshou.DIRECT_STORE_CONFIG[target]["site"]): target
        for target in targets
    }

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == miaoshou.DETAIL_CREATE_PATH:
            serial = body["detailSerialNumberPlatformList"][0]["serialNumber"]
            return {
                "result": "success",
                "data": {
                    "platformCollectBoxDetailIdMap": {
                        "tiktok": {"7": created_by_serial[serial]}
                    }
                },
            }
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        if path.endswith("get_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            return {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": deepcopy(details[shop_id]),
                    "ossMd5": "md5",
                },
            }
        if path.endswith("get_site_collect_item_info"):
            target = target_by_site[str(body["site"])]
            shop_id = str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
            return {
                "result": "success",
                "data": {
                    "siteCollectItemInfo": deepcopy(details[shop_id]),
                    "ossMd5": "md5",
                },
            }
        if path.endswith("save_shop_collect_item_info"):
            shop_id = str(body["shopId"])
            if shop_id == str(
                miaoshou.DIRECT_STORE_CONFIG["tiktok:LH_PH"]["shop_id"]
            ):
                raise RuntimeError("fixture unknown after first target update")
            details[shop_id] = deepcopy(body["shopCollectItemInfo"])
            details[shop_id]["detailId"] = int(body["detailId"])
            details[shop_id]["shopId"] = shop_id
            return {"result": "success"}
        if path.endswith("save_site_collect_item_info"):
            target = target_by_site[str(body["site"])]
            shop_id = str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
            if target == "tiktok:LH_PH":
                raise RuntimeError("fixture unknown after first target update")
            details[shop_id] = deepcopy(body["siteCollectItemInfo"])
            details[shop_id]["detailId"] = int(body["detailId"])
            details[shop_id]["site"] = str(body["site"])
            return {"result": "success"}
        raise AssertionError(path)

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=targets,
        post=post,
    )

    assert [row["target_label"] for row in result["target_results"]] == list(
        targets
    )
    assert result["target_results"][0]["status"] == "FAILED"
    assert result["target_results"][0]["error_code"] == (
        "target_preparation_failed"
    )
    assert len(result["target_results"][0]["detail_digest"]) == 64
    for row in result["target_results"][1:-1]:
        assert row["status"] == "SUCCEEDED"
        assert row["error_code"] is None
        assert row["detail_digest"] is None
    assert result["target_results"][-1]["status"] == "FAILED"
    assert result["target_results"][-1]["error_code"] == (
        "category_not_approved"
    )
    attempted_shop_ids = {
        str(shop_id)
        for path, body in calls
        if path == miaoshou.SHOP_CLAIM_PATH
        for shop_id in body["shopIds"]
    }
    assert attempted_shop_ids == {
        str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"])
        for target in targets[:-1]
    }
    assert result["external_write_count"] is None
    assert all("save_move_collect_task" not in path for path, _ in calls)


def test_collectbox_invalid_approved_price_preserves_prior_claim_write():
    target = "tiktok:MX"
    payload = _plan_payload(target)
    payload["pricing"]["selected_targets"][target]["store_prices"][0][
        "list_price"
    ] = "invalid"
    calls = []

    def post(path, body):
        calls.append((path, body))
        return {"result": "success"}

    with pytest.raises(miaoshou.MiaoshouCollectBoxPreparationError) as captured:
        miaoshou.prepare_selected_platform_collectbox(
            platform="tiktok",
            common_detail_id="7",
            initial_platform_detail_id="77",
            initial_claim_written=True,
            approved_plan_payload=payload,
            approved_targets=(target,),
            post=post,
        )

    assert captured.value.external_writes == (
        "miaoshou:collectbox:claim:tiktok",
    )
    assert captured.value.external_write_count == 1
    assert calls == []


def test_collectbox_wrong_target_currency_stops_before_storefront_writes():
    target = "tiktok:MX"
    payload = _plan_payload(target)
    payload["pricing"]["selected_targets"][target]["store_prices"][0][
        "currency"
    ] = "GBP"
    calls = []

    def post(path, body):
        calls.append((path, body))
        return {"result": "success"}

    with pytest.raises(miaoshou.MiaoshouCollectBoxPreparationError) as captured:
        miaoshou.prepare_selected_platform_collectbox(
            platform="tiktok",
            common_detail_id="7",
            initial_platform_detail_id="77",
            initial_claim_written=True,
            approved_plan_payload=payload,
            approved_targets=(target,),
            post=post,
        )

    assert captured.value.external_writes == (
        "miaoshou:collectbox:claim:tiktok",
    )
    assert captured.value.external_write_count == 1
    assert calls == []


def test_collectbox_shopee_writes_exact_simple_description_without_publish():
    target = "shopee:MY"
    description = (
        "Product overview\nDecorative wall sticker made of PVC.\n\n"
        "Verified details\n- Material: PVC\n\nSuitable spaces"
    )
    payload = _plan_payload(target)
    payload["listing_copy"]["shopee_description_en"] = description
    fake = DirectStoreFake(target)

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="shopee",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=(target,),
        post=fake.post,
    )

    save = next(
        body for path, body in fake.calls if path == fake.config["save_path"]
    )
    assert save["siteDetailSimpleData"]["notesText"] == description
    assert result["checks"]["approved_content_exact"] is True
    assert fake.config["publish_path"] not in [path for path, _ in fake.calls]


def _dispatch_request(command):
    binding = command["identity_binding"]
    return SimpleNamespace(
        target_label=command["target_label"],
        idempotency_key=binding["idempotency_key"],
        source_identity_digest=binding["source_identity_digest"],
        payload_digest=binding["payload_digest"],
        adapter_policy_digest=binding["adapter_policy_digest"],
        command={"payload": {"provider_command": json.loads(
            json.dumps(command, sort_keys=True)
        )}},
        progress_recorder=None,
    )


@pytest.mark.parametrize("target", ["shopee:PH", "ozon:RU"])
def test_shopee_and_ozon_dispatch_only_miaoshou_and_wait_for_manual(target):
    command = _command(target)
    fake = DirectStoreFake(target)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )

    result = miaoshou.dispatch_tiktok_miaoshou_prepared_target(
        _dispatch_request(command)
    )

    platform = command["platform"]
    assert result["canonical_status"] == "SUBMITTED_UNVERIFIED"
    assert result["readback_verified"] is False
    assert result["external_writes"] == (
        f"miaoshou:{platform}_detail:update",
        f"miaoshou:{platform}_publish:submission",
    )
    assert [path for path, _ in fake.calls] == [
        miaoshou.DIRECT_STORE_CONFIG[target]["search_path"],
        miaoshou.DIRECT_STORE_CONFIG[target]["get_path"],
        miaoshou.DIRECT_STORE_CONFIG[target]["save_path"],
        miaoshou.DIRECT_STORE_CONFIG[target]["get_path"],
        miaoshou.DIRECT_STORE_CONFIG[target]["publish_path"],
    ]
    assert not any("modules.shopee" in path for path, _ in fake.calls)


def test_malformed_publish_preserves_confirmed_update_and_unknown_submission():
    target = "shopee:MY"
    command = _command(target)
    fake = DirectStoreFake(target, malformed_publish=True)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )

    with pytest.raises(miaoshou.MiaoshouOneClickDispatchError) as captured:
        miaoshou.dispatch_tiktok_miaoshou_prepared_target(
            _dispatch_request(command)
        )

    error = captured.value
    assert error.dispatch_outcome_unknown is True
    assert error.external_writes == (
        "miaoshou:shopee_detail:update",
        "miaoshou:shopee_publish:submission",
    )
    assert error.confirmed_external_write_count_lower_bound == 1
    assert error.possible_external_write_count_upper_bound == 2


def test_stored_idempotency_or_shop_drift_stops_before_transport():
    command = _command("ozon:RU")
    command["identity_binding"]["idempotency_key"] = "different"
    called = []
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(
            post=lambda path, body: called.append((path, body))
        )
    )

    with pytest.raises(miaoshou.MiaoshouOneClickPreDispatchError):
        request = _dispatch_request(command)
        request.idempotency_key = "original"
        miaoshou.dispatch_tiktok_miaoshou_prepared_target(request)

    assert called == []


@pytest.mark.parametrize("target", ["tiktok:MX", "shopee:PH", "ozon:RU"])
def test_prepare_is_read_only_json_only_and_binds_server_identity(target):
    fake = DirectStoreFake(target)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)

    result = prepare_oneclick_target(_prepare_request(target))
    command = json.loads(
        json.dumps(
            result["command"]["provider_command"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    assert result["classification"] == "READY_SUBMIT_MANUAL"
    assert result["manual_after_submit"] is True
    assert command["kind"] == "DIRECT_STORE"
    assert command["target_label"] == target
    assert command["identity_binding"] == {
        "target_label": target,
        "shop_id": str(miaoshou.DIRECT_STORE_CONFIG[target]["shop_id"]),
        "platform": miaoshou.DIRECT_STORE_CONFIG[target]["platform"],
        "idempotency_key": f"job:{target}",
        "source_identity_digest": "a" * 64,
        "payload_digest": "b" * 64,
        "adapter_policy_digest": "c" * 64,
    }
    assert all(
        path
        in {
            miaoshou.DIRECT_STORE_CONFIG[target]["search_path"],
            miaoshou.DIRECT_STORE_CONFIG[target]["get_path"],
        }
        for path, _ in fake.calls
    )


@pytest.mark.parametrize("target", ["shopee:VN", "ozon:RU"])
def test_missing_site_detail_is_created_then_saved_and_submitted_once(target):
    command = _command(target)
    command["action"] = "CREATE_AND_CLAIM"
    command["detail_id"] = None
    command["observed_snapshot_digest"] = None
    fake = DirectStoreFake(target, existing=False)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )

    result = miaoshou.dispatch_tiktok_miaoshou_prepared_target(
        _dispatch_request(command)
    )

    platform = command["platform"]
    assert result["canonical_status"] == "SUBMITTED_UNVERIFIED"
    assert result["external_writes"] == (
        f"miaoshou:{platform}_detail:create",
        f"miaoshou:{platform}_detail:update",
        f"miaoshou:{platform}_publish:submission",
    )
    assert [path for path, _ in fake.calls].count(
        miaoshou.DETAIL_CREATE_PATH
    ) == 1
    assert fake.calls[-3][0] == miaoshou.DIRECT_STORE_CONFIG[target]["save_path"]
    assert fake.calls[-1][0] == (
        miaoshou.DIRECT_STORE_CONFIG[target]["publish_path"]
    )


def test_one_store_failure_does_not_poison_a_different_store_dispatch():
    failed_command = _command("shopee:TH")
    failed = DirectStoreFake("shopee:TH", malformed_publish=True)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=failed.post)
    )
    with pytest.raises(miaoshou.MiaoshouOneClickDispatchError):
        miaoshou.dispatch_tiktok_miaoshou_prepared_target(
            _dispatch_request(failed_command)
        )

    succeeding_command = _command("ozon:RU")
    succeeding = DirectStoreFake("ozon:RU")
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=succeeding.post)
    )
    result = miaoshou.dispatch_tiktok_miaoshou_prepared_target(
        _dispatch_request(succeeding_command)
    )

    assert result["canonical_status"] == "SUBMITTED_UNVERIFIED"
    assert result["external_writes"] == (
        "miaoshou:ozon_detail:update",
        "miaoshou:ozon_publish:submission",
    )


def test_live_shaped_shopee_cnsc_title_prepares_each_regional_store():
    target = "shopee:MY"
    fake = DirectStoreFake(target)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    request = _prepare_request(target)
    request.immutable_plan_payload = _live_shaped_plan_payload(target)

    result = prepare_oneclick_target(request)

    assert result["classification"] == "READY_SUBMIT_MANUAL"
    assert result["command"]["provider_command"]["expected"]["title"] == (
        "Approved Shopee master title"
    )
    assert result["command"]["provider_command"]["expected"]["price"] == "33"
    assert result["command"]["provider_command"]["expected"]["currency"] == (
        "MYR"
    )


def test_live_shaped_ozon_derived_preview_supplies_approved_price():
    target = "ozon:RU"
    fake = DirectStoreFake(target)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    request = _prepare_request(target)
    request.immutable_plan_payload = _live_shaped_plan_payload(target)

    result = prepare_oneclick_target(request)

    assert result["classification"] == "READY_SUBMIT_MANUAL"
    assert result["command"]["provider_command"]["expected"]["price"] == "33"
    assert result["command"]["provider_command"]["expected"]["currency"] == (
        "CNY"
    )


def test_live_shaped_tiktok_default_prepare_uses_miaoshou_common_id_not_offer_id(
    monkeypatch,
):
    target = "tiktok:LH_PH"
    fake = DirectStoreFake(target)
    original_post = fake.post

    def post(path, body):
        result = original_post(path, body)
        if path == fake.config["search_path"]:
            result["data"]["detailList"][0][
                "commonCollectBoxDetailId"
            ] = 7001
        return result

    monkeypatch.setattr("modules.miaoshou.client.post_open", post)
    request = _prepare_request(target)
    request.immutable_plan_payload = _live_shaped_plan_payload(target)

    result = prepare_oneclick_target(request)
    command = result["command"]["provider_command"]

    assert command["source_offer_id"] == "986159122616"
    assert command["common_detail_id"] == "7001"
    assert command["detail_id"] == "77"
    assert command["action"] == "USE_EXISTING"


def test_shopee_master_title_and_derived_price_remain_strictly_unique():
    target = "shopee:MY"
    fake = DirectStoreFake(target)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    duplicate_title = _prepare_request(target)
    duplicate_title.immutable_plan_payload = _live_shaped_plan_payload(target)
    duplicate_title.immutable_plan_payload["listing_copy"]["candidates"].append(
        {
            "channel": "shopee",
            "site": "CNSC",
            "policy_check": "passed",
            "title": "A second approved master is invalid",
        }
    )

    title_result = prepare_oneclick_target(duplicate_title)

    assert title_result["classification"] == "BLOCKED_CAPABILITY"
    assert title_result["reason_code"] == (
        "approved_storefront_title_not_unique"
    )

    wrong_site = _prepare_request(target)
    wrong_site.immutable_plan_payload = _live_shaped_plan_payload(target)
    wrong_site.immutable_plan_payload["pricing"]["selected_targets"][target][
        "target_site"
    ] = "PH"

    price_result = prepare_oneclick_target(wrong_site)

    assert price_result["classification"] == "BLOCKED_CAPABILITY"
    assert price_result["reason_code"] == "approved_store_price_not_unique"


def test_source_result_common_identity_mismatch_or_ambiguity_fails_closed():
    target = "tiktok:LH_PH"
    request = _prepare_request(target)
    request.immutable_plan_payload = _live_shaped_plan_payload(target)
    fake = DirectStoreFake(target)
    original_post = fake.post

    def mismatched_source(path, body):
        result = original_post(path, body)
        if path == fake.config["search_path"]:
            result["data"]["detailList"][0]["sourceList"] = [
                {"sourceItemId": "111111111111"}
            ]
        return result

    miaoshou.configure_prepare_post_factory(lambda: mismatched_source)
    mismatch = prepare_oneclick_target(request)
    assert mismatch["classification"] == "BLOCKED_CAPABILITY"
    assert mismatch["reason_code"] == (
        "miaoshou_official_prepare_proof_unavailable"
    )

    def ambiguous_common(path, body):
        result = original_post(path, body)
        if path == fake.config["search_path"]:
            second = deepcopy(result["data"]["detailList"][0])
            second["commonCollectBoxDetailId"] = 7002
            second["collectBoxDetailId"] = 78
            result["data"]["detailList"].append(second)
            result["data"]["totalCount"] = 2
        return result

    miaoshou.configure_prepare_post_factory(lambda: ambiguous_common)
    ambiguous = prepare_oneclick_target(request)
    assert ambiguous["classification"] == "BLOCKED_CAPABILITY"
    assert ambiguous["reason_code"] == (
        "miaoshou_official_prepare_proof_unavailable"
    )
