from __future__ import annotations

from copy import deepcopy
import ast
import json
from pathlib import Path

import pytest

from domains.channel_operations.tiktok_publisher import (
    APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA,
    TIKTOK_PREFLIGHT_RECEIPT_SCHEMA,
    TIKTOK_PUBLISH_RECEIPT_SCHEMA,
    TikTokPublishContractError,
    TikTokPreWritePreparationError,
    TikTokPublisher,
)
from modules.miaoshou.tiktok_publisher import (
    WAREHOUSE_GET_PATH,
    EXPECTED_SHOP_ID_BY_TARGET,
    MiaoshouTikTokTransport,
    PUBLISH_PATH,
    READ_SHOP_DRAFT_PATH,
    READ_SITE_DRAFT_PATH,
    SAVE_SHOP_DRAFT_PATH,
    SAVE_SITE_DRAFT_PATH,
)
from modules.miaoshou.client import MiaoshouBusinessRejectedError


TARGETS = (
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:MX",
    "tiktok:GB",
)
PRICE_BY_TARGET = {
    "tiktok:LH_PH": ("523", "PHP"),
    "tiktok:LH_MY": ("46", "MYR"),
    "tiktok:LH_TH": ("386", "THB"),
    "tiktok:LH_VN": ("408000", "VND"),
    "tiktok:MX": ("286", "MXN"),
    "tiktok:GB": ("15", "GBP"),
}
APPROVED_CATEGORY_ID = "600338"
CATEGORY_METADATA_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata"
)


def _gb_metadata_response() -> dict:
    return {
        "result": "success",
        "data": {
            "categoryMetadata": {
                "categoryProductAttrList": [
                    {
                        "attrId": "102255",
                        "name": "Batch Number",
                        "attributeNameAlias": "Batch Number",
                        "isMandatory": True,
                        "values": [
                            {
                                "id": "1000256",
                                "name": "1",
                                "valueNameAlias": "1",
                            }
                        ],
                    }
                ]
            }
        },
    }


def _snapshot(*, targets: tuple[str, ...] = TARGETS) -> dict:
    rows = []
    for index, target in enumerate(targets, start=1):
        price, currency = PRICE_BY_TARGET[target]
        rows.append(
            {
                "target_label": target,
                "detail_id": str(3249695000 + index),
                "shop_id": str(EXPECTED_SHOP_ID_BY_TARGET[target]),
                "expected_price": price,
                "expected_weight_kg": "0.1",
                "expected_package_cm": ["20", "20", "3"],
                "expected_title": "Approved frozen table runner",
                "expected_description": "Approved frozen description",
                "expected_images": [
                    f"https://example.invalid/approved-{image}.jpg"
                    for image in range(1, 8)
                ],
                "expected_sku_parcels": {},
                "expected_currency": currency,
                # Category is approved product evidence, never a platform/site constant.
                "expected_category_id": APPROVED_CATEGORY_ID,
                "category_evidence_digest": "c" * 64,
                "target_identity_digest": "d" * 64,
                "publish_identity_digest": "e" * 64,
                "receipt_digest": "f" * 64,
            }
        )
    return {
        "schema_version": APPROVED_TIKTOK_PUBLISH_SNAPSHOT_SCHEMA,
        "offer_id": "3846511157",
        "plan_id": "omnichannel:" + "a" * 64,
        "product_revision": 15,
        "payload_digest": "b" * 64,
        "targets": rows,
    }


def _draft_response(target: str, row: dict) -> dict:
    price = row["expected_price"]
    sku_map = {
        "default": {
            "price": price,
            "priceIncludeVat": price,
        }
    }
    variant_models = row.get("expected_variant_model_skus") or {}
    sku_parcels = row.get("expected_sku_parcels") or {}
    if variant_models:
        sku_map = {}
        for variant, model_sku in variant_models.items():
            parcel = sku_parcels[variant]
            sku_map[variant] = {
                "itemNum": model_sku,
                "price": (row.get("expected_sku_prices") or {}).get(
                    model_sku, price
                ),
                "priceIncludeVat": (row.get("expected_sku_prices") or {}).get(
                    model_sku, price
                ),
                "weight": parcel["weight_kg"],
                "packageLength": parcel["package_cm"][0],
                "packageWidth": parcel["package_cm"][1],
                "packageHeight": parcel["package_cm"][2],
            }
    target_shop_id = str(row["shop_id"])
    for sku_row in sku_map.values():
        sku_row["stock"] = 300
        sku_row["shopIdToWarehouseIdAndStockMap"] = {
            target_shop_id: {f"warehouse-{target_shop_id}": "300"}
        }
    detail = {
        "detailId": int(row["detail_id"]),
        "cid": row["expected_category_id"] or "600009",
        "deliveryOptionSetType": "default",
        "weight": row["expected_weight_kg"],
        "packageLength": row["expected_package_cm"][0],
        "packageWidth": row["expected_package_cm"][1],
        "packageHeight": row["expected_package_cm"][2],
        "sizeChart": "",
        "sizeChartType": "",
        "title": row["expected_title"],
        "notes": row["expected_description"],
        "notesText": row["expected_description"],
        "imgUrls": list(row["expected_images"]),
        "skuMap": sku_map,
    }
    if target in {
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
    }:
        return {
            "result": "success",
            "data": {"siteCollectItemInfo": detail, "ossMd5": "x" * 32},
        }
    detail["shopId"] = int(row["shop_id"])
    return {
        "result": "success",
        "data": {"shopCollectItemInfo": detail, "ossMd5": "x" * 32},
    }


class FakeLowestTransport:
    def __init__(self, snapshot: dict, *, reject_target: str | None = None):
        self.snapshot = snapshot
        self.reject_target = reject_target
        self.calls: list[tuple[str, dict]] = []
        self.rows_by_detail = {
            str(row["detail_id"]): row for row in snapshot["targets"]
        }

    def __call__(self, path: str, body: dict) -> dict:
        self.calls.append((path, body))
        if path == CATEGORY_METADATA_PATH:
            return _gb_metadata_response()
        detail_id = str(body["detailIds"][0] if "detailIds" in body else body["detailId"])
        row = self.rows_by_detail[detail_id]
        target = row["target_label"]
        if path in {SAVE_SITE_DRAFT_PATH, SAVE_SHOP_DRAFT_PATH}:
            return {"result": "success", "code": "200", "message": "Success"}
        if path == PUBLISH_PATH:
            if target == self.reject_target:
                raise MiaoshouBusinessRejectedError(
                    "category is incomplete", code="categoryInvalid"
                )
            return {"result": "success", "code": "200", "message": "Success"}
        return _draft_response(target, row)


def _publisher(snapshot: dict, *, reject_target: str | None = None):
    fake = FakeLowestTransport(snapshot, reject_target=reject_target)
    transport = MiaoshouTikTokTransport(post=fake)
    return TikTokPublisher(transport=transport), fake


def test_l1_preflight_reads_exact_six_drafts_and_never_writes():
    snapshot = _snapshot()
    publisher, fake = _publisher(snapshot)

    receipt = publisher.preflight(snapshot)

    assert receipt["schema_version"] == TIKTOK_PREFLIGHT_RECEIPT_SCHEMA
    assert receipt["offer_id"] == "3846511157"
    assert [row["target_label"] for row in receipt["targets"]] == list(TARGETS)
    assert [row["status"] for row in receipt["targets"]] == [
        "READY", "READY", "READY", "READY", "READY", "REPAIR_REQUIRED"
    ]
    assert len(fake.calls) == 7
    assert all(
        path not in {SAVE_SITE_DRAFT_PATH, SAVE_SHOP_DRAFT_PATH, PUBLISH_PATH}
        for path, _body in fake.calls
    )


def test_l1_preflight_accepts_miaoshou_post_submit_projection_omissions():
    """Confirmed live projection omissions must not cause perpetual repair.

    Miaoshou accepts the exact save payload, then projects the submitted draft
    with no deliveryOptionSetType, ``sizeChartType=image``, and without the
    per-model package dimensions.  Parent parcel, model weights, model prices,
    SKU identities and category remain observable and must still match exactly.
    """

    snapshot = _snapshot(targets=("tiktok:LH_PH",))
    row = snapshot["targets"][0]
    row["expected_variant_model_skus"] = {
        ";style;35*140;": "0963",
        ";style;35*200;": "0964",
        ";style;35*300;": "0965",
    }
    row["expected_sku_prices"] = {
        "0963": "649",
        "0964": "824",
        "0965": "1031",
    }
    row["expected_sku_parcels"] = {
        ";style;35*140;": {"weight_kg": "0.1", "package_cm": ["20", "20", "3"]},
        ";style;35*200;": {"weight_kg": "0.15", "package_cm": ["20", "20", "3"]},
        ";style;35*300;": {"weight_kg": "0.2", "package_cm": ["20", "20", "3"]},
    }

    projected = _draft_response("tiktok:LH_PH", row)
    info = projected["data"]["siteCollectItemInfo"]
    info["deliveryOptionSetType"] = None
    info["sizeChartType"] = "image"
    for sku in info["skuMap"].values():
        sku.pop("packageLength")
        sku.pop("packageWidth")
        sku.pop("packageHeight")

    transport = MiaoshouTikTokTransport(post=lambda _path, _body: projected)
    target = snapshot["targets"][0]
    draft = transport.read_draft(target)

    assert transport.draft_matches(target, draft) is False
    assert transport.post_submit_draft_matches(target, draft) is True


def test_miaoshou_transport_uses_ceiled_provider_dimensions_for_save_and_readback():
    target = _snapshot(targets=("tiktok:LH_PH",))["targets"][0]
    target["expected_package_cm"] = ["15", "15.2", "0.8"]
    saved: list[dict] = []

    def post(path: str, body: dict) -> dict:
        if path == SAVE_SITE_DRAFT_PATH:
            saved.append(body)
        return {"result": "success", "code": "success", "data": {}}

    transport = MiaoshouTikTokTransport(post=post)
    draft = {
        "info": {
            "cid": APPROVED_CATEGORY_ID,
            "deliveryOptionSetType": None,
            "weight": "0.1",
                "packageLength": "15",
                "packageWidth": "15.2",
                "packageHeight": "0.8",
            "sizeChart": "",
            "sizeChartType": "image",
            "skuMap": {
                    "default": {
                        "price": target["expected_price"],
                        "priceIncludeVat": target["expected_price"],
                        "stock": 300,
                        "shopIdToWarehouseIdAndStockMap": {
                            target["shop_id"]: {"warehouse-ph": "300"}
                        },
                    }
            },
        },
        "oss_md5": "revision-1",
    }

    transport.save_approved_draft(target, draft)

    info = saved[0]["siteCollectItemInfo"]
    assert [
        info["packageLength"],
        info["packageWidth"],
        info["packageHeight"],
    ] == [15, 16, 1]
    assert all(
        type(info[field]) is int
        for field in ("packageLength", "packageWidth", "packageHeight")
    )
    json.dumps(saved[0])
    assert transport.draft_matches(target, {"info": info}) is True


def test_miaoshou_transport_repairs_and_verifies_frozen_copy_and_images():
    target = _snapshot(targets=("tiktok:LH_PH",))["targets"][0]
    saved: list[dict] = []
    stale = _draft_response("tiktok:LH_PH", target)
    stale_info = stale["data"]["siteCollectItemInfo"]
    stale_info.update(
        {
            "title": "Old provider title",
            "notes": "Old provider description",
            "notesText": "Old provider description",
            "imgUrls": [f"https://example.invalid/old-{image}.jpg" for image in range(14)],
        }
    )
    transport = MiaoshouTikTokTransport(
        post=lambda _path, body: saved.append(deepcopy(body)) or {"code": 0}
    )

    assert transport.draft_matches(target, {"info": stale_info}) is False
    assert transport.post_submit_draft_matches(target, {"info": stale_info}) is False
    transport.save_approved_draft(target, {"info": stale_info, "oss_md5": "digest"})

    saved_info = saved[0]["siteCollectItemInfo"]
    assert saved_info["title"] == target["expected_title"]
    assert saved_info["notes"] == target["expected_description"]
    assert saved_info["notesText"] == target["expected_description"]
    assert saved_info["imgUrls"] == target["expected_images"]
    assert transport.draft_matches(target, {"info": saved_info}) is True
    assert transport.post_submit_draft_matches(target, {"info": saved_info}) is True

    post_submit_projection = deepcopy(saved_info)
    post_submit_projection["notesText"] = None
    assert transport.draft_matches(target, {"info": post_submit_projection}) is False
    assert transport.post_submit_draft_matches(
        target, {"info": post_submit_projection}
    ) is True

    for omitted in ("",):
        post_submit_projection = deepcopy(saved_info)
        post_submit_projection["notesText"] = omitted
        assert transport.draft_matches(target, {"info": post_submit_projection}) is False
        assert transport.post_submit_draft_matches(
            target, {"info": post_submit_projection}
        ) is True
    post_submit_projection = deepcopy(saved_info)
    post_submit_projection.pop("notesText")
    assert transport.draft_matches(target, {"info": post_submit_projection}) is False
    assert transport.post_submit_draft_matches(
        target, {"info": post_submit_projection}
    ) is True

    for field in ("title", "notes", "notesText", "imgUrls"):
        drifted = deepcopy(saved_info)
        drifted[field] = "drift" if field != "imgUrls" else drifted[field][:-1]
        assert transport.draft_matches(target, {"info": drifted}) is False
        assert transport.post_submit_draft_matches(target, {"info": drifted}) is False


def test_miaoshou_transport_converges_and_verifies_frozen_variant_display_name():
    target = _snapshot(targets=("tiktok:LH_PH",))["targets"][0]
    variant = ";PH15-004;44cm宽*3米长（单卷+纸管+塑封）;"
    raw_key = ";type-1;dimension-1;"
    target.update(
        {
            "expected_variant_model_skus": {variant: "0969"},
            "expected_variant_specifications": {variant: {"option": "44cm*3m"}},
            "expected_sku_prices": {"0969": target["expected_price"]},
            "expected_sku_parcels": {
                variant: {"weight_kg": "0.1", "package_cm": ["20", "20", "3"]}
            },
        }
    )
    info = _draft_response("tiktok:LH_PH", target)["data"]["siteCollectItemInfo"]
    row = info["skuMap"].pop(variant)
    info["skuMap"] = {raw_key: row}
    info["skuPropertyList"] = [
        {"attrName": "Product Type", "attrValueList": [{"attrValueId": "type-1", "attrValue": "Wall Sticker"}]},
        {"attrName": "规格", "attrValueList": [{"attrValueId": "dimension-1", "attrValue": "44cm宽*3米长（单卷+纸管+塑封）"}]},
    ]
    saved: list[dict] = []
    transport = MiaoshouTikTokTransport(
        post=lambda _path, body: saved.append(deepcopy(body)) or {"code": 0}
    )

    assert transport.draft_matches(target, {"info": info}) is False
    transport.save_approved_draft(target, {"info": info, "oss_md5": "digest"})
    saved_info = saved[0]["siteCollectItemInfo"]
    assert saved_info["skuMap"][raw_key]["specification"] == {"option": "44cm*3m"}
    assert saved_info["skuPropertyList"][1]["attrName"] == "Specification"
    assert saved_info["skuPropertyList"][1]["attrValueList"][0]["attrValue"] == "44cm*3m"
    assert transport.draft_matches(target, {"info": saved_info}) is True

    omitted = deepcopy(saved_info)
    omitted["skuMap"][raw_key].pop("specification")
    assert transport.draft_matches(target, {"info": omitted}) is False
    assert transport.post_submit_draft_matches(target, {"info": omitted}) is True
    drifted = deepcopy(saved_info)
    drifted["skuPropertyList"][1]["attrValueList"][0]["attrValue"] = "source drift"
    assert transport.post_submit_draft_matches(target, {"info": drifted}) is False


def test_miaoshou_save_binds_unique_current_shop_warehouse_and_preserves_stock():
    target = _snapshot(targets=("tiktok:LH_MY",))["targets"][0]
    saved: list[dict] = []
    info = _draft_response("tiktok:LH_MY", target)["data"]["siteCollectItemInfo"]
    info["skuMap"]["default"].pop("shopIdToWarehouseIdAndStockMap")

    def post(path, body):
        if path == WAREHOUSE_GET_PATH:
            assert body == {"shopIds": [target["shop_id"]]}
            return {"data": {"shopWarehouseList": [{
                "shopId": target["shop_id"],
                "warehouseList": [{"warehouseId": "my-default", "warehouseName": "The Chinese mainland Pickup Warehouse", "warehouseEffectStatus": "1", "isDefault": "1"}],
            }]}}
        saved.append(deepcopy(body))
        return {"code": 0}

    MiaoshouTikTokTransport(post=post).save_approved_draft(
        target, {"info": info, "oss_md5": "digest"}
    )

    row = saved[0]["siteCollectItemInfo"]["skuMap"]["default"]
    assert row["stock"] == 300
    assert row["shopIdToWarehouseIdAndStockMap"] == {
        target["shop_id"]: {"my-default": "300"}
    }


def test_miaoshou_save_rejects_ambiguous_or_cross_shop_warehouse_before_write():
    target = _snapshot(targets=("tiktok:LH_MY",))["targets"][0]
    info = _draft_response("tiktok:LH_MY", target)["data"]["siteCollectItemInfo"]
    info["skuMap"]["default"].pop("shopIdToWarehouseIdAndStockMap")
    writes: list[str] = []

    def post(path, _body):
        if path == WAREHOUSE_GET_PATH:
            return {"data": {"shopWarehouseList": [{
                "shopId": "other-shop",
                "warehouseList": [{"warehouseId": "wrong", "warehouseName": "The Chinese mainland Pickup Warehouse", "warehouseEffectStatus": "1", "isDefault": "1"}],
            }, {
                "shopId": target["shop_id"],
                "warehouseList": [
                    {"warehouseId": "a", "warehouseName": "The Chinese mainland Pickup Warehouse", "warehouseEffectStatus": "1", "isDefault": "1"},
                    {"warehouseId": "b", "warehouseName": "中国大陆揽收仓", "warehouseEffectStatus": "1", "isDefault": "1"},
                ],
            }]}}
        writes.append(path)
        return {"code": 0}

    with pytest.raises(TikTokPreWritePreparationError, match="warehouse"):
        MiaoshouTikTokTransport(post=post).save_approved_draft(
            target, {"info": info, "oss_md5": "digest"}
        )
    assert writes == []


def test_miaoshou_save_rejects_missing_active_current_shop_warehouse_before_write():
    target = _snapshot(targets=("tiktok:LH_MY",))["targets"][0]
    info = _draft_response("tiktok:LH_MY", target)["data"]["siteCollectItemInfo"]
    info["skuMap"]["default"].pop("shopIdToWarehouseIdAndStockMap")
    writes: list[str] = []

    def post(path, _body):
        if path == WAREHOUSE_GET_PATH:
            return {"data": {"shopWarehouseList": [{
                "shopId": target["shop_id"],
                "warehouseList": [{"warehouseId": "disabled", "warehouseName": "The Chinese mainland Pickup Warehouse", "warehouseEffectStatus": "0", "isDefault": "1"}],
            }]}}
        writes.append(path)
        return {"code": 0}

    with pytest.raises(TikTokPreWritePreparationError, match="warehouse"):
        MiaoshouTikTokTransport(post=post).save_approved_draft(
            target, {"info": info, "oss_md5": "digest"}
        )
    assert writes == []


def test_miaoshou_save_rejects_non_mainland_or_ambiguous_mainland_warehouse_before_write():
    target = _snapshot(targets=("tiktok:LH_MY",))["targets"][0]
    info = _draft_response("tiktok:LH_MY", target)["data"]["siteCollectItemInfo"]
    info["skuMap"]["default"].pop("shopIdToWarehouseIdAndStockMap")
    writes: list[str] = []

    def post(path, _body):
        if path == WAREHOUSE_GET_PATH:
            return {"data": {"shopWarehouseList": [{
                "shopId": target["shop_id"],
                "warehouseList": [
                    {"warehouseId": "foreign", "warehouseName": "Malaysia Pickup Warehouse", "warehouseEffectStatus": "1"},
                    {"warehouseId": "cn-a", "warehouseName": "The Chinese mainland Pickup Warehouse", "warehouseEffectStatus": "1"},
                    {"warehouseId": "cn-b", "warehouseName": "中国大陆揽收仓", "warehouseEffectStatus": "1"},
                ],
            }]}}
        writes.append(path)
        return {"code": 0}

    with pytest.raises(TikTokPreWritePreparationError, match="China mainland"):
        MiaoshouTikTokTransport(post=post).save_approved_draft(
            target, {"info": info, "oss_md5": "digest"}
        )
    assert writes == []


def test_l1_one_rejection_does_not_stop_later_tiktok_targets():
    snapshot = _snapshot()
    publisher, fake = _publisher(snapshot, reject_target="tiktok:LH_MY")
    preflight = publisher.preflight(snapshot)

    receipt = publisher.publish(snapshot, preflight)

    assert receipt["schema_version"] == TIKTOK_PUBLISH_RECEIPT_SCHEMA
    outcomes = {row["target_label"]: row for row in receipt["targets"]}
    assert outcomes["tiktok:LH_MY"] == {
        "target_label": "tiktok:LH_MY",
        "outcome": "REJECTED",
        "stage": "PUBLISH",
        "provider_code": "categoryInvalid",
        "provider_reason": "category is incomplete",
        "external_write_count": 0,
        "write_request_count": 1,
    }
    assert outcomes["tiktok:LH_TH"]["outcome"] == "ACCEPTED"
    assert outcomes["tiktok:GB"]["outcome"] == "ACCEPTED"
    publish_calls = [call for call in fake.calls if call[0] == PUBLISH_PATH]
    assert len(publish_calls) == 6
    assert receipt["accepted_target_count"] == 5
    assert receipt["rejected_target_count"] == 1


def test_l1_publish_uses_exact_endpoint_and_per_target_identity():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:MX", "tiktok:GB"))
    publisher, fake = _publisher(snapshot)
    preflight = publisher.preflight(snapshot)

    publisher.publish(snapshot, preflight)

    calls = [body for path, body in fake.calls if path == PUBLISH_PATH]
    assert calls == [
        {
            "detailIds": [int(row["detail_id"])],
            "shopIds": [
                str(row["shop_id"])
                if row["target_label"] == "tiktok:LH_PH"
                else int(row["shop_id"])
            ],
        }
        for row in snapshot["targets"]
    ]


def test_l1_direct_production_publish_reads_each_target_once():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:MX", "tiktok:GB"))
    publisher, fake = _publisher(snapshot)

    receipt = publisher.publish(snapshot)

    assert receipt["accepted_target_count"] == 3
    read_paths = {READ_SITE_DRAFT_PATH, READ_SHOP_DRAFT_PATH}
    assert len([call for call in fake.calls if call[0] in read_paths]) == 3
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 3


def test_site_resolved_category_uses_verified_draft_category_for_publish():
    snapshot = _snapshot()
    for row in snapshot["targets"]:
        row["expected_category_id"] = None
    publisher, fake = _publisher(snapshot)

    receipt = publisher.publish(snapshot)

    assert receipt["accepted_target_count"] == 6
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 6


@pytest.mark.parametrize("target", ("tiktok:LH_PH", "tiktok:MX"))
def test_missing_delivery_option_is_repaired_before_submit(target: str):
    snapshot = _snapshot(targets=(target,))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        response = fake(path, body)
        if path in {READ_SITE_DRAFT_PATH, READ_SHOP_DRAFT_PATH}:
            container = (
                "siteCollectItemInfo"
                if path == READ_SITE_DRAFT_PATH
                else "shopCollectItemInfo"
            )
            response["data"][container].pop("deliveryOptionSetType")
        return response

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))

    receipt = publisher.publish(snapshot)

    save_path = (
        SAVE_SITE_DRAFT_PATH
        if target == "tiktok:LH_PH"
        else SAVE_SHOP_DRAFT_PATH
    )
    saved = next(body for path, body in fake.calls if path == save_path)
    container = (
        "siteCollectItemInfo"
        if save_path == SAVE_SITE_DRAFT_PATH
        else "shopCollectItemInfo"
    )
    assert saved[container]["deliveryOptionSetType"] == "default"
    assert receipt["accepted_target_count"] == 1


@pytest.mark.parametrize("target", ("tiktok:LH_PH", "tiktok:MX"))
def test_invalid_size_chart_is_removed_before_submit(target: str):
    snapshot = _snapshot(targets=(target,))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        response = fake(path, body)
        if path in {READ_SITE_DRAFT_PATH, READ_SHOP_DRAFT_PATH}:
            container = (
                "siteCollectItemInfo"
                if path == READ_SITE_DRAFT_PATH
                else "shopCollectItemInfo"
            )
            response["data"][container]["sizeChart"] = (
                "https://provider.example/size-chart.gif"
            )
            response["data"][container]["sizeChartType"] = "image"
        return response

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))

    receipt = publisher.publish(snapshot)

    save_path = (
        SAVE_SITE_DRAFT_PATH
        if target == "tiktok:LH_PH"
        else SAVE_SHOP_DRAFT_PATH
    )
    saved = next(body for path, body in fake.calls if path == save_path)
    container = (
        "siteCollectItemInfo"
        if save_path == SAVE_SITE_DRAFT_PATH
        else "shopCollectItemInfo"
    )
    assert saved[container]["sizeChart"] == ""
    assert saved[container]["sizeChartType"] == ""
    assert receipt["accepted_target_count"] == 1


def test_l1_mismatched_target_is_repaired_then_submitted_and_other_targets_continue():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:LH_MY"))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        response = fake(path, body)
        if path == READ_SITE_DRAFT_PATH and body.get("site") == "PH":
            response["data"]["siteCollectItemInfo"]["skuMap"]["default"]["price"] = "1"
        return response

    publisher = TikTokPublisher(
        transport=MiaoshouTikTokTransport(post=post)
    )
    preflight = publisher.preflight(snapshot)
    assert [row["status"] for row in preflight["targets"]] == [
        "REPAIR_REQUIRED",
        "READY",
    ]

    receipt = publisher.publish(snapshot, preflight)

    outcomes = {row["target_label"]: row for row in receipt["targets"]}
    assert outcomes["tiktok:LH_PH"]["outcome"] == "ACCEPTED"
    assert outcomes["tiktok:LH_PH"]["external_write_count"] == 2
    assert outcomes["tiktok:LH_PH"]["write_request_count"] == 2
    assert outcomes["tiktok:LH_MY"]["outcome"] == "ACCEPTED"
    publish_calls = [call for call in fake.calls if call[0] == PUBLISH_PATH]
    assert len(publish_calls) == 2
    save_calls = [call for call in fake.calls if call[0] == SAVE_SITE_DRAFT_PATH]
    assert len(save_calls) == 1
    saved = save_calls[0][1]["siteCollectItemInfo"]
    assert saved["cid"] == APPROVED_CATEGORY_ID
    assert saved["skuMap"]["default"]["price"] == 523.0
    assert saved["skuMap"]["default"]["priceIncludeVat"] == 523.0


def test_l1_repair_rejection_is_reported_and_does_not_stop_later_target():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:LH_MY"))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        response = fake(path, body)
        if path == READ_SITE_DRAFT_PATH and body.get("site") == "PH":
            response["data"]["siteCollectItemInfo"]["cid"] = "999"
        if path == SAVE_SITE_DRAFT_PATH:
            raise MiaoshouBusinessRejectedError(
                "category is incomplete", code="categoryInvalid"
            )
        return response

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    receipt = publisher.publish(snapshot, publisher.preflight(snapshot))
    outcomes = {row["target_label"]: row for row in receipt["targets"]}

    assert outcomes["tiktok:LH_PH"]["outcome"] == "REJECTED"
    assert outcomes["tiktok:LH_PH"]["provider_code"] == "categoryInvalid"
    assert outcomes["tiktok:LH_PH"]["provider_reason"] == "category is incomplete"
    assert outcomes["tiktok:LH_PH"]["external_write_count"] == 0
    assert outcomes["tiktok:LH_PH"]["write_request_count"] == 1
    assert outcomes["tiktok:LH_MY"]["outcome"] == "ACCEPTED"
    assert len([call for call in fake.calls if call[0] == PUBLISH_PATH]) == 1


@pytest.mark.parametrize("forbidden", ("miaoshou:COMMON", "shopee:VN", "ozon:RU"))
def test_l1_snapshot_rejects_non_tiktok_targets(forbidden: str):
    snapshot = _snapshot(targets=("tiktok:GB",))
    snapshot["targets"].append(
        {
            "target_label": forbidden,
            "detail_id": "1",
            "shop_id": "1",
            "expected_price": "1",
            "expected_currency": "GBP",
            "expected_category_id": "1",
        }
    )
    publisher, fake = _publisher(_snapshot(targets=("tiktok:GB",)))

    with pytest.raises(TikTokPublishContractError):
        publisher.preflight(snapshot)

    assert fake.calls == []


def test_l1_production_module_has_no_oneclick_shopee_or_ozon_imports():
    paths = (
        Path("domains/channel_operations/tiktok_publisher.py"),
        Path("modules/miaoshou/tiktok_publisher.py"),
    )
    imported = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not any(
        token in module
        for module in imported
        for token in ("oneclick", "shopee", "ozon")
    )


def test_l1_publish_rejects_tampered_or_incomplete_preflight():
    snapshot = _snapshot(targets=("tiktok:LH_PH", "tiktok:LH_MY"))
    publisher, fake = _publisher(snapshot)
    preflight = publisher.preflight(snapshot)
    preflight["targets"] = preflight["targets"][:-1]

    with pytest.raises(TikTokPublishContractError):
        publisher.publish(snapshot, preflight)

    assert not [call for call in fake.calls if call[0] == PUBLISH_PATH]


def test_l1_provider_reason_is_redacted_before_receipt():
    snapshot = _snapshot(targets=("tiktok:GB",))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        fake.calls.append((path, body))
        if path == PUBLISH_PATH:
            raise MiaoshouBusinessRejectedError(
                "Authorization Bearer super-secret-token "
                "https://provider.example/items/123456789012",
                code="rejected",
            )
        if path == CATEGORY_METADATA_PATH:
            return _gb_metadata_response()
        row = snapshot["targets"][0]
        return _draft_response("tiktok:GB", row)

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    result = publisher.publish(snapshot, publisher.preflight(snapshot))["targets"][0]

    assert result["outcome"] == "REJECTED"
    assert "super-secret-token" not in result["provider_reason"]
    assert "provider.example" not in result["provider_reason"]
    assert "123456789012" not in result["provider_reason"]


def test_l1_provider_reason_is_bounded_and_code_rejects_non_ascii():
    snapshot = _snapshot(targets=("tiktok:GB",))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        fake.calls.append((path, body))
        if path == PUBLISH_PATH:
            raise MiaoshouBusinessRejectedError("x" * 400, code="??")
        if path == CATEGORY_METADATA_PATH:
            return _gb_metadata_response()
        return _draft_response("tiktok:GB", snapshot["targets"][0])

    result = TikTokPublisher(
        transport=MiaoshouTikTokTransport(post=post)
    ).publish(snapshot)["targets"][0]

    assert result["provider_code"] == "business_rejected"
    assert len(result["provider_reason"]) == 240


def test_l1_transport_unknown_keeps_only_confirmed_external_writes():
    snapshot = _snapshot(targets=("tiktok:GB",))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        fake.calls.append((path, body))
        if path == PUBLISH_PATH:
            raise TimeoutError("after dispatch")
        if path == CATEGORY_METADATA_PATH:
            return _gb_metadata_response()
        if path == SAVE_SHOP_DRAFT_PATH:
            return {"result": "success", "code": "200", "message": "Success"}
        return _draft_response("tiktok:GB", snapshot["targets"][0])

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    result = publisher.publish(snapshot, publisher.preflight(snapshot))["targets"][0]

    assert result["outcome"] == "UNKNOWN"
    assert result["external_write_count"] == 1
    assert result["write_request_count"] == 2


def test_red_gb_repairs_bound_draft_before_submit():
    snapshot = _snapshot(targets=("tiktok:GB",))

    class RecordingTransport:
        def __init__(self):
            self.calls = []

        def read_draft(self, target):
            self.calls.append("read")
            return {"info": {"cid": "", "skuMap": {"default": {}}}}

        def draft_matches(self, target, draft):
            self.calls.append("match")
            return False

        def prepare_approved_draft(self, target, draft):
            self.calls.append("prepare")
            return {"body": {"detailId": target["detail_id"]}}

        def save_prepared_draft(self, target, prepared):
            self.calls.append("save")
            return {"result": "success", "code": "200", "message": "Success"}

        def submit(self, target):
            self.calls.append("submit")
            return {"result": "success", "code": "200", "message": "Success"}

    transport = RecordingTransport()
    publisher = TikTokPublisher(transport=transport)
    preflight = publisher.preflight(snapshot)
    transport.calls.clear()

    result = publisher.publish(snapshot, preflight)["targets"][0]

    assert transport.calls == ["save", "submit"]
    assert result["outcome"] == "ACCEPTED"
    assert result["external_write_count"] == 2


def test_red_gb_repair_does_not_parse_malformed_old_price_before_save():
    snapshot = _snapshot(targets=("tiktok:GB",))

    class RepairTransport:
        def __init__(self):
            self.calls = []

        def read_draft(self, target):
            self.calls.append("read")
            return {"info": {"skuMap": {"default": {"price": 0}}}}

        def draft_matches(self, target, draft):
            self.calls.append("match")
            raise ValueError("old draft price is malformed")

        def prepare_approved_draft(self, target, draft):
            self.calls.append("prepare")
            return {"body": {"detailId": target["detail_id"]}}

        def save_prepared_draft(self, target, prepared):
            self.calls.append("save")
            return {"result": "success", "code": "200", "message": "Success"}

        def submit(self, target):
            self.calls.append("submit")
            return {"result": "success", "code": "200", "message": "Success"}

    transport = RepairTransport()
    result = TikTokPublisher(transport=transport).publish(snapshot)["targets"][0]

    assert transport.calls == ["read", "prepare", "save", "submit"]
    assert result["outcome"] == "ACCEPTED"


def test_red_multisku_prices_are_written_per_model_sku():
    snapshot = _snapshot(targets=("tiktok:MX",))
    target = snapshot["targets"][0]
    target["expected_sku_prices"] = {
        "0963": "286",
        "0964": "321",
        "0965": "359",
    }
    target["expected_variant_model_skus"] = {
        "variant-1": "0963",
        "variant-2": "0964",
        "variant-3": "0965",
    }
    target["expected_sku_parcels"] = {
        "variant-1": {"weight_kg": "0.1", "package_cm": ["20", "20", "3"]},
        "variant-2": {"weight_kg": "0.15", "package_cm": ["20", "20", "3"]},
        "variant-3": {"weight_kg": "0.2", "package_cm": ["20", "20", "3"]},
    }
    calls = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == READ_SHOP_DRAFT_PATH:
            return {
                "result": "success",
                "data": {
                    "ossMd5": "mx-md5",
                    "shopCollectItemInfo": {
                        "detailId": int(target["detail_id"]),
                        "cid": target["expected_category_id"],
                        "deliveryOptionSetType": "default",
                        "sizeChart": "",
                        "sizeChartType": "",
                        "skuMap": {
                            "variant-1": {
                                "itemNum": "0963",
                                "price": 1,
                                "priceIncludeVat": 1,
                                "stock": 300,
                                "shopIdToWarehouseIdAndStockMap": {target["shop_id"]: {"warehouse-mx": "300"}},
                            },
                            "variant-2": {
                                "itemNum": "0964",
                                "price": 1,
                                "priceIncludeVat": 1,
                                "stock": 300,
                                "shopIdToWarehouseIdAndStockMap": {target["shop_id"]: {"warehouse-mx": "300"}},
                            },
                            "variant-3": {
                                "itemNum": "0965",
                                "price": 1,
                                "priceIncludeVat": 1,
                                "stock": 300,
                                "shopIdToWarehouseIdAndStockMap": {target["shop_id"]: {"warehouse-mx": "300"}},
                            },
                        },
                    },
                },
            }
        if path in {SAVE_SHOP_DRAFT_PATH, PUBLISH_PATH}:
            return {"result": "success", "code": "200", "message": "Success"}
        raise AssertionError(path)

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    publisher.publish(snapshot, publisher.preflight(snapshot))
    saved = next(body for path, body in calls if path == SAVE_SHOP_DRAFT_PATH)
    saved_rows = saved["shopCollectItemInfo"]["skuMap"]

    assert {
        row["itemNum"]: str(row["price"]).rstrip("0").rstrip(".")
        for row in saved_rows.values()
    } == {"0963": "286", "0964": "321", "0965": "359"}


def test_red_opaque_gb_variants_bind_to_approved_model_sku_and_price():
    snapshot = _snapshot(targets=("tiktok:GB",))
    target = snapshot["targets"][0]
    variants = ("gold;35*140", "gold;35*200", "gold;35*300")
    target["expected_sku_prices"] = {
        "0963": "17",
        "0964": "18",
        "0965": "20",
    }
    target["expected_variant_model_skus"] = {
        variants[0]: "0963",
        variants[1]: "0964",
        variants[2]: "0965",
    }
    target["expected_sku_parcels"] = {
        variants[0]: {"weight_kg": "0.1", "package_cm": ["20", "20", "3"]},
        variants[1]: {"weight_kg": "0.15", "package_cm": ["20", "20", "3"]},
        variants[2]: {"weight_kg": "0.2", "package_cm": ["20", "20", "3"]},
    }
    calls = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == READ_SHOP_DRAFT_PATH:
            repeated_source_item = {
                "itemNum": "1070173617923",
                "price": 1,
                "stock": 300,
                "shopIdToWarehouseIdAndStockMap": {target["shop_id"]: {"warehouse-gb": "300"}},
            }
            return {
                "result": "success",
                "data": {
                    "ossMd5": "gb-md5",
                    "shopCollectItemInfo": {
                        "detailId": int(target["detail_id"]),
                        "cid": target["expected_category_id"],
                        "skuMap": {
                            ";size-140;color-gold;": dict(repeated_source_item),
                            ";color-gold;size-200;": dict(repeated_source_item),
                            ";size-300;color-gold;": dict(repeated_source_item),
                        },
                        "skuPropertyList": [
                            {
                                "attrName": "Color",
                                "attrValueList": [
                                    {
                                        "attrValueId": "color-gold",
                                        "attrValue": "gold",
                                    }
                                ],
                            },
                            {
                                "attrName": "Size",
                                "attrValueList": [
                                    {"attrValueId": "size-140", "attrValue": "35*140"},
                                    {"attrValueId": "size-200", "attrValue": "35*200"},
                                    {"attrValueId": "size-300", "attrValue": "35*300"},
                                ],
                            },
                        ],
                    },
                },
            }
        if path == CATEGORY_METADATA_PATH:
            return _gb_metadata_response()
        if path in {SAVE_SHOP_DRAFT_PATH, PUBLISH_PATH}:
            return {"result": "success", "code": "200", "message": "Success"}
        raise AssertionError(path)

    receipt = TikTokPublisher(
        transport=MiaoshouTikTokTransport(post=post)
    ).publish(snapshot)

    assert receipt["accepted_target_count"] == 1
    saved = next(body for path, body in calls if path == SAVE_SHOP_DRAFT_PATH)
    saved_rows = saved["shopCollectItemInfo"]["skuMap"]
    assert {
        row["itemNum"]: str(row["price"]).rstrip("0").rstrip(".")
        for row in saved_rows.values()
    } == {"0963": "17", "0964": "18", "0965": "20"}
    saved_info = saved["shopCollectItemInfo"]
    assert (
        saved_info["weight"],
        saved_info["packageLength"],
        saved_info["packageWidth"],
        saved_info["packageHeight"],
    ) == (0.1, 20.0, 20.0, 3.0)
    assert {
        row["itemNum"]: (
            row["weight"],
            row["packageLength"],
            row["packageWidth"],
            row["packageHeight"],
        )
        for row in saved_rows.values()
    } == {
        "0963": (0.1, 20.0, 20.0, 3.0),
        "0964": (0.15, 20.0, 20.0, 3.0),
        "0965": (0.2, 20.0, 20.0, 3.0),
    }


def test_red_local_gb_variant_binding_failure_is_zero_write_rejection():
    snapshot = _snapshot(targets=("tiktok:GB",))
    target = snapshot["targets"][0]
    target["expected_sku_prices"] = {
        "0963": "17",
        "0964": "18",
        "0965": "20",
    }
    target["expected_variant_model_skus"] = {
        "gold;35*140": "0963",
        "gold;35*200": "0964",
        "gold;35*300": "0965",
    }
    target["expected_sku_parcels"] = {
        "gold;35*140": {"weight_kg": "0.1", "package_cm": ["20", "20", "3"]},
        "gold;35*200": {"weight_kg": "0.15", "package_cm": ["20", "20", "3"]},
        "gold;35*300": {"weight_kg": "0.2", "package_cm": ["20", "20", "3"]},
    }
    calls = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == READ_SHOP_DRAFT_PATH:
            return {
                "result": "success",
                "data": {
                    "ossMd5": "gb-md5",
                    "shopCollectItemInfo": {
                        "detailId": int(target["detail_id"]),
                        "cid": target["expected_category_id"],
                        "skuMap": {
                            "opaque-a": {"itemNum": "1070173617923", "price": 1},
                            "opaque-b": {"itemNum": "1070173617923", "price": 1},
                            "opaque-c": {"itemNum": "1070173617923", "price": 1},
                        },
                    },
                },
            }
        raise AssertionError("a local binding failure must not call a write endpoint")

    result = TikTokPublisher(
        transport=MiaoshouTikTokTransport(post=post)
    ).publish(snapshot)["targets"][0]

    assert result["outcome"] == "REJECTED"
    assert result["provider_code"] == "sku_price_binding_invalid"
    assert result["external_write_count"] == 0
    assert result["write_request_count"] == 0
    assert [path for path, _body in calls] == [READ_SHOP_DRAFT_PATH]


def test_red_gb_save_uses_official_required_category_attribute():
    snapshot = _snapshot(targets=("tiktok:GB",))
    row = snapshot["targets"][0]
    calls = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == READ_SHOP_DRAFT_PATH:
            return {
                "result": "success",
                "data": {
                    "ossMd5": "gb-md5",
                    "shopCollectItemInfo": {
                        "detailId": int(row["detail_id"]),
                        "cid": "",
                        "isCodOpen": "1",
                        "sizeChart": "https://provider.example/size-chart.gif",
                        "sizeChartType": "image",
                        "deliveryOptionSetType": "",
                        "skuMap": {
                            "default": {
                                "price": 1.1,
                                "priceIncludeVat": 1.1,
                                "stock": 300,
                                "shopIdToWarehouseIdAndStockMap": {row["shop_id"]: {"warehouse-gb": "300"}},
                            }
                        },
                    },
                },
            }
        if path == CATEGORY_METADATA_PATH:
            assert body == {"site": "GB", "cid": 600338, "shopIds": [10204699]}
            return {
                "result": "success",
                "data": {
                    "categoryMetadata": {
                        "categoryProductAttrList": [
                            {
                                "attrId": "102255",
                                "name": "Batch Number",
                                "attributeNameAlias": "Batch Number",
                                "isMandatory": True,
                                "values": [
                                    {
                                        "id": "1000256",
                                        "name": "1",
                                        "valueNameAlias": "1",
                                    }
                                ],
                            }
                        ]
                    }
                },
            }
        if path == SAVE_SHOP_DRAFT_PATH:
            return {"result": "success", "code": "200", "message": "Success"}
        raise AssertionError(path)

    transport = MiaoshouTikTokTransport(post=post)
    draft = transport.read_draft(row)
    transport.save_approved_draft(row, draft)
    saved = next(body for path, body in calls if path == SAVE_SHOP_DRAFT_PATH)

    assert saved["shopCollectItemInfo"]["cid"] == "600338"
    assert saved["shopCollectItemInfo"]["isCodOpen"] == "0"
    assert saved["shopCollectItemInfo"]["deliveryOptionSetType"] == "default"
    assert saved["shopCollectItemInfo"]["sizeChart"] == ""
    assert saved["shopCollectItemInfo"]["sizeChartType"] == ""
    assert saved["shopCollectItemInfo"]["productAttributes"] == [
        {
            "attributeId": "102255",
            "attributeName": "Batch Number",
            "attributeNameAlias": "Batch Number",
            "attributeValues": [
                {"valueName": "1", "valueId": "1000256", "valueNameAlias": "1"}
            ],
        }
    ]


def test_red_gb_save_allows_category_with_no_mandatory_attributes():
    snapshot = _snapshot(targets=("tiktok:GB",))
    row = snapshot["targets"][0]
    calls = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == READ_SHOP_DRAFT_PATH:
            return _draft_response("tiktok:GB", row)
        if path == CATEGORY_METADATA_PATH:
            return {
                "result": "success",
                "data": {
                    "categoryMetadata": {
                        "categoryProductAttrList": [
                            {
                                "attrId": "100370",
                                "name": "Batteries Included",
                                "isMandatory": False,
                                "values": [{"id": "1", "name": "No"}],
                            }
                        ]
                    }
                },
            }
        if path == SAVE_SHOP_DRAFT_PATH:
            return {"result": "success", "code": "200", "message": "Success"}
        raise AssertionError(path)

    transport = MiaoshouTikTokTransport(post=post)
    draft = transport.read_draft(row)
    transport.save_approved_draft(row, draft)
    saved = next(body for path, body in calls if path == SAVE_SHOP_DRAFT_PATH)

    assert saved["shopCollectItemInfo"]["productAttributes"] == []


def test_red_gb_metadata_preparation_failure_is_zero_write_rejection():
    snapshot = _snapshot(targets=("tiktok:GB",))
    row = snapshot["targets"][0]
    calls = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, body))
        if path == READ_SHOP_DRAFT_PATH:
            return _draft_response("tiktok:GB", row)
        if path == CATEGORY_METADATA_PATH:
            return {
                "result": "success",
                "data": {
                    "categoryMetadata": {
                        "categoryProductAttrList": [
                            {
                                "attrId": "102255",
                                "name": "Batch Number",
                                "isMandatory": True,
                                "values": [
                                    {"id": "1", "name": "One"},
                                    {"id": "2", "name": "Two"},
                                ],
                            }
                        ]
                    }
                },
            }
        raise AssertionError("pre-write preparation must not call a write endpoint")

    result = TikTokPublisher(
        transport=MiaoshouTikTokTransport(post=post)
    ).publish(snapshot)["targets"][0]

    assert result["outcome"] == "REJECTED"
    assert result["provider_code"] == "draft_repair_preparation_invalid"
    assert result["external_write_count"] == 0
    assert result["write_request_count"] == 0
    assert [path for path, _body in calls] == [
        READ_SHOP_DRAFT_PATH,
        CATEGORY_METADATA_PATH,
    ]


def test_l1_gb_save_rejection_stops_submit():
    snapshot = _snapshot(targets=("tiktok:GB",))
    fake = FakeLowestTransport(snapshot)

    def post(path: str, body: dict) -> dict:
        response = fake(path, body)
        if path == SAVE_SHOP_DRAFT_PATH:
            raise MiaoshouBusinessRejectedError(
                "delivery option is invalid", code="fail"
            )
        return response

    publisher = TikTokPublisher(transport=MiaoshouTikTokTransport(post=post))
    receipt = publisher.publish(snapshot, publisher.preflight(snapshot))
    result = receipt["targets"][0]

    assert result["outcome"] == "REJECTED"
    assert result["external_write_count"] == 0
    assert result["write_request_count"] == 1
    assert not [call for call in fake.calls if call[0] == PUBLISH_PATH]
    assert [call for call in fake.calls if call[0] == SAVE_SHOP_DRAFT_PATH]
