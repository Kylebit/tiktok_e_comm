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
    return {
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
        "product_facts": {
            "title": "Approved source title",
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
    }


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
                "tiktok": "shopCollectItemInfo",
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
                "tiktok": "shopCollectItemInfo",
                "shopee": "siteDetailSimpleData",
                "ozon": "siteCollectItemInfo",
            }[self.config["platform"]]
            self.detail = deepcopy(body[field])
            self.detail["detailId"] = 77
            if self.config["platform"] == "tiktok":
                self.detail["shopId"] = str(self.config["shop_id"])
                self.detail["sourceOfferId"] = "986159122616"
            return {"result": "success"}
        if path == self.config["publish_path"]:
            return [] if self.malformed_publish else {"result": "success"}
        raise AssertionError(path)


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
