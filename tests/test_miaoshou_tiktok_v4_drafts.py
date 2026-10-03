from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json

import pytest

from shared_platform.product_description_media import miaoshou_rich_description

from modules.miaoshou.client import MiaoshouBusinessRejectedError
from modules.miaoshou.tiktok_v4_drafts import (
    DraftWriteFact,
    MiaoshouOpenApiTikTokV4DraftTransport,
    TikTokV4DraftPreparationError,
    TikTokV4SystemicPreflightError,
    _draft_payload,
    _miaoshou_draft_info,
    _provider_bound_sku_map,
    _tiktok_warehouse_allocation,
    prepare_tiktok_v4_drafts,
)
from modules.miaoshou import tiktok_v4_drafts
from test_tiktok_v4_execution import _snapshot


SHOP_IDS = {
    "tiktok:LH_PH": "7676267",
    "tiktok:LH_MY": "13295169",
}


def test_miaoshou_draft_rounds_provider_parcel_dimensions_up_to_integers() -> None:
    snapshot = _snapshot()
    target = snapshot["publication_targets"][0]
    draft = _draft_payload(
        snapshot,
        target=target,
        category=CategoryResolver().resolve(
            target=target,
            product=snapshot["product"],
            skus=snapshot["skus"],
        ),
    )
    draft["parent_parcel"] = {
        "weight_kg": "0.1",
        "package_cm": ["15", "15.2", "0.8"],
    }
    for row in draft["skus"]:
        row["parcel"] = {
            "weight_kg": "0.1",
            "package_cm": ["15", "15.2", "0.8"],
        }

    info = _miaoshou_draft_info(draft)

    assert [
        info["packageLength"],
        info["packageWidth"],
        info["packageHeight"],
    ] == [15, 16, 1]
    assert [
        [
            row["packageLength"],
            row["packageWidth"],
            row["packageHeight"],
        ]
        for row in info["skuMap"].values()
    ] == [[15, 16, 1], [15, 16, 1]]
    assert info["weight"] == 0.1
    assert [row["weight"] for row in info["skuMap"].values()] == [0.1, 0.1]


def _editable_site_payload(*, site: str, revision: str) -> dict:
    snapshot = _snapshot()
    label = "tiktok:LH_PH" if site == "PH" else "tiktok:LH_MY"
    shop_id = SHOP_IDS[label]
    warehouse_id = f"warehouse-{shop_id}"
    return {
        "result": "success",
        "data": {
            "siteCollectItemInfo": {
                "providerRequired": "keep",
                "collectBoxDetailShopList": [{"shopId": shop_id}],
                "skuMap": {
                    str(row["variant_key"]): {
                        "itemNum": row["model_sku"],
                        "stock": 300,
                        "shopIdToWarehouseIdAndStockMap": {
                            shop_id: {warehouse_id: "300"}
                        },
                    }
                    for row in snapshot["skus"]
                },
            },
            "ossMd5": revision,
        },
    }


class CategoryResolver:
    def __init__(self, *, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
        self.calls: list[str] = []

    def resolve(self, *, target, product, skus):
        label = target["target_label"]
        self.calls.append(label)
        if label in self.missing:
            return None
        return {
            "id": "600338" if label.endswith("PH") else "600339",
            "name": "Refrigerator Magnets",
            "path": [
                {"id": "600001", "name": "Home Decor"},
                {
                    "id": "600338" if label.endswith("PH") else "600339",
                    "name": "Refrigerator Magnets",
                },
            ],
        }


class Transport:
    def __init__(
        self,
        *,
        claim_outcomes: dict[str, str] | None = None,
        save_outcomes: dict[str, str] | None = None,
    ) -> None:
        self.claim_outcomes = claim_outcomes or {}
        self.save_outcomes = save_outcomes or {}
        self.claims: list[dict] = []
        self.saves: list[dict] = []

    def claim_or_create(self, *, target, ordinal):
        self.claims.append({"target": deepcopy(target), "ordinal": ordinal})
        label = target["target_label"]
        outcome = self.claim_outcomes.get(label, "ACCEPTED")
        return DraftWriteFact(
            operation="CLAIM_OR_CREATE",
            outcome=outcome,
            detail_id=(
                {"tiktok:LH_PH": "7001", "tiktok:LH_MY": "7002"}[label]
                if outcome != "REJECTED"
                else None
            ),
            shop_id=target["shop_id"] if outcome != "REJECTED" else None,
        )

    def save_draft(self, *, identity, draft):
        self.saves.append(
            {"identity": deepcopy(identity), "draft": deepcopy(draft)}
        )
        label = identity["target_label"]
        return DraftWriteFact(
            operation="SAVE_DRAFT",
            outcome=self.save_outcomes.get(label, "ACCEPTED"),
            detail_id=identity["detail_id"],
            shop_id=identity["shop_id"],
        )

    def prepare_save_draft(self, *, identity, draft):
        return {"identity": deepcopy(identity), "draft": deepcopy(draft)}

    def save_prepared_draft(self, *, identity, prepared):
        return self.save_draft(identity=identity, draft=prepared["draft"])


def test_v4_snapshot_alone_supplies_every_exact_draft_fact() -> None:
    snapshot = _snapshot()
    transport = Transport()

    receipt = prepare_tiktok_v4_drafts(
        snapshot,
        category_resolver=CategoryResolver(),
        transport=transport,
    )

    assert receipt["schema_version"] == "miaoshou-tiktok-v4-draft-preparation/v1"
    assert receipt["snapshot_digest"] == snapshot["snapshot_digest"]
    assert receipt["plan_id"] == snapshot["plan_id"]
    assert [row["target_label"] for row in receipt["targets"]] == [
        "tiktok:LH_PH",
        "tiktok:LH_MY",
    ]
    assert all(row["status"] == "PREPARED" for row in receipt["targets"])
    ph = transport.saves[0]
    assert ph["identity"] == {
        "target_label": "tiktok:LH_PH",
        "detail_id": "7001",
        "shop_id": SHOP_IDS["tiktok:LH_PH"],
    }
    assert ph["draft"]["title"] == snapshot["product"]["title"]
    assert ph["draft"]["description"] == snapshot["product"]["description"]
    assert ph["draft"]["images"] == snapshot["product"]["images"]
    assert ph["draft"]["category"]["id"] == "600338"
    assert [row["variant_key"] for row in ph["draft"]["skus"]] == [
        row["variant_key"] for row in snapshot["skus"]
    ]
    assert [row["model_sku"] for row in ph["draft"]["skus"]] == [
        row["model_sku"] for row in snapshot["skus"]
    ]
    assert [row["specification"] for row in ph["draft"]["skus"]] == [
        row["specification"] for row in snapshot["skus"]
    ]
    assert [row["price"] for row in ph["draft"]["skus"]] == [
        row["prices"]["tiktok:LH_PH"]["amount"] for row in snapshot["skus"]
    ]
    assert [row["parcel"] for row in ph["draft"]["skus"]] == [
        row["parcel"] for row in snapshot["skus"]
    ]
    assert [row["images"] for row in ph["draft"]["skus"]] == [
        row["variant_images"] for row in snapshot["skus"]
    ]
    contexts = receipt["collectbox_contexts"]
    assert set(contexts) == {"tiktok:LH_PH", "tiktok:LH_MY"}
    ph_identity = contexts["tiktok:LH_PH"]["target_detail_identity"]
    assert ph_identity["schema_version"] == "collectbox-target-detail-identity/v1"
    assert ph_identity["target_label"] == "tiktok:LH_PH"
    assert ph_identity["detail_id"] == "7001"
    assert ph_identity["shop_id"] == SHOP_IDS["tiktok:LH_PH"]
    assert len(ph_identity["identity_digest"]) == 64
    assert contexts["tiktok:LH_PH"]["snapshot_digest"] == snapshot[
        "snapshot_digest"
    ]
    assert "approved_plan_payload" not in receipt


def test_v4_draft_uses_exact_target_localized_images() -> None:
    snapshot = _snapshot()
    base_images = list(snapshot["product"]["images"])
    localized_images = [
        f"https://localized.example/ph-{index}.png"
        for index, _url in enumerate(base_images, start=1)
    ]
    snapshot["product"]["image_routing"] = {
        "schema_version": "localized-publication-images/v1",
        "approval_digest": "sha256:" + "a" * 64,
        "supplement_digest": "sha256:" + "b" * 64,
        "source_snapshot_digest": "sha256:" + "c" * 64,
        "routes": {
            target["target_label"]: {
                "locale": "en-master",
                "ordered_images": (
                    localized_images
                    if target["target_label"] == "tiktok:LH_PH"
                    else base_images
                ),
            }
            for target in snapshot["publication_targets"]
        },
    }
    unsigned = deepcopy(snapshot)
    unsigned.pop("snapshot_digest")
    snapshot["snapshot_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    transport = Transport()

    receipt = prepare_tiktok_v4_drafts(
        snapshot,
        category_resolver=CategoryResolver(),
        transport=transport,
    )

    assert receipt["status"] == "PREPARED"
    ph_draft = transport.saves[0]["draft"]
    assert ph_draft["images"] == localized_images
    assert len(base_images) == len(localized_images)
    routed_by_base = dict(zip(base_images, localized_images))
    assert [row["images"] for row in ph_draft["skus"]] == [
        [routed_by_base.get(url, url) for url in row["variant_images"]]
        for row in snapshot["skus"]
    ]


def test_one_target_failure_never_blocks_later_tiktok_targets() -> None:
    transport = Transport(claim_outcomes={"tiktok:LH_PH": "REJECTED"})

    receipt = prepare_tiktok_v4_drafts(
        _snapshot(),
        category_resolver=CategoryResolver(),
        transport=transport,
    )

    assert [row["status"] for row in receipt["targets"]] == [
        "FAILED",
        "PREPARED",
    ]
    assert [row["target"]["target_label"] for row in transport.claims] == [
        "tiktok:LH_PH",
        "tiktok:LH_MY",
    ]
    assert [row["identity"]["target_label"] for row in transport.saves] == [
        "tiktok:LH_MY"
    ]
    assert set(receipt["collectbox_contexts"]) == {"tiktok:LH_MY"}


def test_phase_a_serializes_every_draft_before_any_claim_or_save(monkeypatch) -> None:
    original = tiktok_v4_drafts._draft_payload
    calls = 0

    def malformed_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        draft = original(*args, **kwargs)
        if calls == 2:
            draft["systemic_non_json_value"] = object()
        return draft

    monkeypatch.setattr(tiktok_v4_drafts, "_draft_payload", malformed_second)
    transport = Transport()
    with pytest.raises(TikTokV4SystemicPreflightError, match="JSON serializable"):
        prepare_tiktok_v4_drafts(
            _snapshot(), category_resolver=CategoryResolver(), transport=transport
        )
    assert transport.claims == []
    assert transport.saves == []


def test_phase_a_shared_projection_failure_is_not_category_unavailable(monkeypatch) -> None:
    original = tiktok_v4_drafts._draft_payload
    calls = 0

    def broken_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TikTokV4DraftPreparationError("shared SKU projection failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(tiktok_v4_drafts, "_draft_payload", broken_second)
    transport = Transport()
    with pytest.raises(TikTokV4SystemicPreflightError, match="projection"):
        prepare_tiktok_v4_drafts(
            _snapshot(), category_resolver=CategoryResolver(), transport=transport
        )
    assert transport.claims == []
    assert transport.saves == []


def test_phase_b_prepares_every_save_before_sending_any_save() -> None:
    class PhaseTransport(Transport):
        def __init__(self) -> None:
            super().__init__()
            self.events = []

        def prepare_save_draft(self, *, identity, draft):
            self.events.append(("prepare", identity["target_label"]))
            if identity["target_label"] == "tiktok:LH_MY":
                raise TikTokV4DraftPreparationError("warehouse unavailable")
            return {"identity": deepcopy(identity), "draft": deepcopy(draft)}

        def save_prepared_draft(self, *, identity, prepared):
            self.events.append(("save", identity["target_label"]))
            return super().save_draft(identity=identity, draft=prepared["draft"])

    transport = PhaseTransport()
    receipt = prepare_tiktok_v4_drafts(
        _snapshot(), category_resolver=CategoryResolver(), transport=transport
    )
    assert transport.events == [
        ("prepare", "tiktok:LH_PH"),
        ("prepare", "tiktok:LH_MY"),
        ("save", "tiktok:LH_PH"),
    ]
    assert [row["status"] for row in receipt["targets"]] == ["PREPARED", "FAILED"]


def test_phase_b_systemic_failure_prevents_every_save() -> None:
    class PhaseTransport(Transport):
        def __init__(self) -> None:
            super().__init__()
            self.prepared = []

        def prepare_save_draft(self, *, identity, draft):
            self.prepared.append(identity["target_label"])
            if identity["target_label"] == "tiktok:LH_MY":
                raise TikTokV4SystemicPreflightError("shared JSON adapter failed")
            return {"identity": deepcopy(identity), "draft": deepcopy(draft)}

    transport = PhaseTransport()
    receipt = prepare_tiktok_v4_drafts(
        _snapshot(), category_resolver=CategoryResolver(), transport=transport
    )
    assert transport.prepared == ["tiktok:LH_PH", "tiktok:LH_MY"]
    assert transport.saves == []
    assert [row["reason_code"] for row in receipt["targets"]] == [
        "SAVE_PREFLIGHT_FAILED", "SAVE_PREFLIGHT_FAILED"
    ]


def test_ambiguous_claim_and_save_preserve_identity_and_write_truth() -> None:
    transport = Transport(
        claim_outcomes={"tiktok:LH_PH": "UNKNOWN"},
        save_outcomes={"tiktok:LH_MY": "UNKNOWN"},
    )

    receipt = prepare_tiktok_v4_drafts(
        _snapshot(),
        category_resolver=CategoryResolver(),
        transport=transport,
    )

    assert [row["status"] for row in receipt["targets"]] == [
        "UNKNOWN",
        "UNKNOWN",
    ]
    assert receipt["external_write_count"] is None
    assert receipt["targets"][0]["writes"] == [
        {"operation": "CLAIM_OR_CREATE", "outcome": "UNKNOWN"}
    ]
    assert receipt["targets"][1]["writes"] == [
        {"operation": "CLAIM_OR_CREATE", "outcome": "ACCEPTED"},
        {"operation": "SAVE_DRAFT", "outcome": "UNKNOWN"},
    ]
    assert transport.saves[0]["identity"]["target_label"] == "tiktok:LH_MY"
    assert set(receipt["collectbox_contexts"]) == {
        "tiktok:LH_PH",
        "tiktok:LH_MY",
    }
    assert receipt["collectbox_contexts"]["tiktok:LH_PH"][
        "target_detail_identity"
    ]["detail_id"] == "7001"
    assert receipt["collectbox_contexts"]["tiktok:LH_MY"][
        "target_detail_identity"
    ]["detail_id"] == "7002"


def test_missing_category_is_zero_write_target_failure() -> None:
    transport = Transport()

    receipt = prepare_tiktok_v4_drafts(
        _snapshot(),
        category_resolver=CategoryResolver(missing={"tiktok:LH_PH"}),
        transport=transport,
    )

    assert receipt["targets"][0] == {
        "target_label": "tiktok:LH_PH",
        "status": "FAILED",
        "reason_code": "CATEGORY_UNAVAILABLE",
        "writes": [],
        "external_write_count": 0,
    }
    assert [row["target"]["target_label"] for row in transport.claims] == [
        "tiktok:LH_MY"
    ]
    assert receipt["targets"][1]["status"] == "PREPARED"


def test_production_seam_uses_injected_audited_low_level_calls_only() -> None:
    calls: list[tuple[str, dict]] = []
    observed: list[tuple[str, DraftWriteFact]] = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, deepcopy(body)))
        serial_rows = body.get("detailSerialNumberPlatformList")
        if isinstance(serial_rows, list):
            serial = serial_rows[0]["serialNumber"]
            return {
                "result": "success",
                "data": {
                    "platformCollectBoxDetailIdMap": {
                        "tiktok": {"5001": 7100 + serial}
                    }
                },
            }
        if path.endswith("get_site_collect_item_info"):
            return _editable_site_payload(site=body["site"], revision="revision-1")
        if path.endswith("get_shop_warehouse_list"):
            return _warehouse_payload(str(body["shopIds"][0]))
        return {"result": "success", "data": {}}

    receipt = prepare_tiktok_v4_drafts(
        _snapshot(),
        category_resolver=CategoryResolver(),
        transport=MiaoshouOpenApiTikTokV4DraftTransport(
            common_detail_id="5001",
            post=post,
            fact_observer=lambda label, fact: observed.append((label, fact)),
        ),
    )

    assert receipt["status"] == "PREPARED"
    assert receipt["external_write_count"] == 6
    assert [fact.operation for _, fact in observed] == [
        "CREATE_DRAFT",
        "CLAIM_TO_SHOP",
        "CREATE_DRAFT",
        "CLAIM_TO_SHOP",
        "SAVE_DRAFT",
        "SAVE_DRAFT",
    ]
    assert [path for path, _ in calls] == [
        "/open/v1/product/common_collect_box/common_collect_box/claimed",
        "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop",
        "/open/v1/product/common_collect_box/common_collect_box/claimed",
            "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop",
            "/open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info",
            "/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list",
            "/open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info",
            "/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list",
            "/open/v1/product/collect_box/tiktok/collect_box/save_site_collect_item_info",
        "/open/v1/product/collect_box/tiktok/collect_box/save_site_collect_item_info",
    ]
    first_save = calls[8][1]
    assert first_save["detailId"] == 7101
    assert first_save["site"] == "PH"
    assert first_save["ossMd5"] == "revision-1"
    info = first_save["siteCollectItemInfo"]
    assert info["providerRequired"] == "keep"
    assert info["title"] == _snapshot()["product"]["title"]
    assert info["notes"] == miaoshou_rich_description(
        _snapshot()["product"]["description"],
        _snapshot()["product"]["images"],
    )
    assert info["cid"] == "600338"
    assert [row["itemNum"] for row in info["skuMap"].values()] == [
        "0958",
        "0959",
    ]
    assert [row["price"] for row in info["skuMap"].values()] == [129.0, 132.0]
    assert [row["currency"] for row in info["skuMap"].values()] == ["PHP", "PHP"]
    my_identity = receipt["collectbox_contexts"]["tiktok:LH_MY"][
        "target_detail_identity"
    ]
    assert my_identity["target_label"] == "tiktok:LH_MY"
    assert my_identity["detail_id"] == "7102"
    assert my_identity["shop_id"] == SHOP_IDS["tiktok:LH_MY"]

    source = inspect.getsource(tiktok_v4_drafts)
    assert "approved_plan_payload" not in source
    assert "oneclick_release" not in source


def test_production_seam_reads_current_draft_and_uses_required_oss_md5() -> None:
    calls: list[tuple[str, dict]] = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, deepcopy(body)))
        serial_rows = body.get("detailSerialNumberPlatformList")
        if isinstance(serial_rows, list):
            serial = serial_rows[0]["serialNumber"]
            return {
                "result": "success",
                "data": {
                    "platformCollectBoxDetailIdMap": {
                        "tiktok": {"5001": 7200 + serial}
                    }
                },
            }
        if path.endswith("get_site_collect_item_info"):
            return _editable_site_payload(site=body["site"], revision="revision-1")
        if path.endswith("get_shop_warehouse_list"):
            return _warehouse_payload(str(body["shopIds"][0]))
        if path.endswith("save_site_collect_item_info"):
            assert body["ossMd5"] == "revision-1"
            assert body["siteCollectItemInfo"]["providerRequired"] == "keep"
            assert body["siteCollectItemInfo"]["deliveryOptionSetType"] == "default"
            assert body["siteCollectItemInfo"]["sizeChart"] == ""
            assert body["siteCollectItemInfo"]["sizeChartType"] == ""
        return {"result": "success", "data": {}}

    receipt = prepare_tiktok_v4_drafts(
        _snapshot(),
        category_resolver=CategoryResolver(),
        transport=MiaoshouOpenApiTikTokV4DraftTransport(
            common_detail_id="5001",
            post=post,
        ),
    )

    assert receipt["status"] == "PREPARED"
    assert sum(path.endswith("get_site_collect_item_info") for path, _ in calls) == 2
    assert sum(path.endswith("save_site_collect_item_info") for path, _ in calls) == 2


def test_production_seam_reads_exact_shop_warehouse_and_preserves_provider_stock() -> None:
    calls: list[tuple[str, dict]] = []
    snapshot = _snapshot()
    current_sku_map = {
        str(row["variant_key"]): {
            "itemNum": row["model_sku"],
            "stock": 300 - (index * 100),
        }
        for index, row in enumerate(snapshot["skus"])
    }

    def post(path: str, body: dict) -> dict:
        calls.append((path, deepcopy(body)))
        serial_rows = body.get("detailSerialNumberPlatformList")
        if isinstance(serial_rows, list):
            serial = serial_rows[0]["serialNumber"]
            return {
                "result": "success",
                "data": {
                    "platformCollectBoxDetailIdMap": {
                        "tiktok": {"5001": 7400 + serial}
                    }
                },
            }
        if path.endswith("get_site_collect_item_info"):
            shop_id = SHOP_IDS[
                "tiktok:LH_PH" if body["site"] == "PH" else "tiktok:LH_MY"
            ]
            return {
                "result": "success",
                "data": {
                    "siteCollectItemInfo": {
                        "skuMap": deepcopy(current_sku_map),
                        "collectBoxDetailShopList": [{"shopId": shop_id}],
                    },
                    "ossMd5": "revision-with-stock",
                },
            }
        if path.endswith("get_shop_warehouse_list"):
            shop_id = str(body["shopIds"][0])
            return {
                "result": "success",
                "data": {
                    "shopWarehouseList": [
                        {
                            "shopId": shop_id,
                            "warehouseList": [
                                {
                                    "warehouseId": f"warehouse-{shop_id}",
                                        "warehouseName": "The Chinese mainland Pickup Warehouse",
                                        "warehouseEffectStatus": "1",
                                    "isDefault": "1",
                                }
                            ],
                        }
                    ]
                },
            }
        return {"result": "success", "data": {}}

    receipt = prepare_tiktok_v4_drafts(
        snapshot,
        category_resolver=CategoryResolver(),
        transport=MiaoshouOpenApiTikTokV4DraftTransport(
            common_detail_id="5001",
            post=post,
        ),
    )

    assert receipt["status"] == "PREPARED"
    warehouse_calls = [
        body for path, body in calls if path.endswith("get_shop_warehouse_list")
    ]
    assert warehouse_calls == [
        {"shopIds": [SHOP_IDS["tiktok:LH_PH"]]},
        {"shopIds": [SHOP_IDS["tiktok:LH_MY"]]},
    ]
    saves = [
        body for path, body in calls if path.endswith("save_site_collect_item_info")
    ]
    assert len(saves) == 2
    for saved in saves:
        info = saved["siteCollectItemInfo"]
        shop_id = SHOP_IDS[
            "tiktok:LH_PH" if saved["site"] == "PH" else "tiktok:LH_MY"
        ]
        assert [row["stock"] for row in info["skuMap"].values()] == [300, 200]
        assert [
            row["shopIdToWarehouseIdAndStockMap"]
            for row in info["skuMap"].values()
        ] == [
            {shop_id: {f"warehouse-{shop_id}": "300"}},
            {shop_id: {f"warehouse-{shop_id}": "200"}},
        ]


def test_production_seam_binds_opaque_provider_key_by_exact_property_label() -> None:
    shop_id = SHOP_IDS["tiktok:LH_PH"]
    current = {
        "skuPropertyList": [
            {
                "attrValueList": [
                    {
                        "attrValueId": "abb6449b29",
                        "attrValue": "田园鲜花铺",
                    }
                ]
            }
        ],
        "skuMap": {
            ";abb6449b29;": {
                "itemNum": "1060462479185",
                "stock": 300,
            }
        },
    }
    draft = {
        "skus": [
            {
                "variant_key": ";田园鲜花铺;;",
                "model_sku": "0967",
            }
        ]
    }
    desired = {
        ";田园鲜花铺;;": {
            "itemNum": "0967",
            "sellerSku": "0967",
        }
    }

    result = _provider_bound_sku_map(
        current=current,
        draft=draft,
        desired=desired,
        shop_id=shop_id,
        warehouse_id="warehouse-ph",
    )

    assert list(result) == [";abb6449b29;"]
    assert result[";abb6449b29;"]["itemNum"] == "0967"
    assert result[";abb6449b29;"]["stock"] == 300


def test_production_seam_binds_source_variant_suffix_by_exact_specification() -> None:
    shop_id = SHOP_IDS["tiktok:LH_PH"]
    current = {
        "skuPropertyList": [
            {
                "attrValueList": [
                    {"attrValueId": "color-blue", "attrValue": "星空蓝"}
                ]
            },
            {
                "attrValueList": [
                    {"attrValueId": "size-15", "attrValue": "15*15cm"}
                ]
            },
        ],
        "skuMap": {
            ";color-blue;size-15;": {
                "itemNum": "991290086160",
                "stock": 300,
            }
        },
    }
    draft = {
        "skus": [
            {
                "variant_key": ";星空蓝;15*15cm无织唛;",
                "model_sku": "0968",
                "specification": {"option": "15*15cm"},
            }
        ]
    }
    desired = {
        ";星空蓝;15*15cm无织唛;": {
            "itemNum": "0968",
            "sellerSku": "0968",
        }
    }

    result = _provider_bound_sku_map(
        current=current,
        draft=draft,
        desired=desired,
        shop_id=shop_id,
        warehouse_id="warehouse-ph",
    )

    assert list(result) == [";color-blue;size-15;"]
    assert result[";color-blue;size-15;"]["itemNum"] == "0968"


def test_prewrite_variant_binding_failure_is_zero_write_rejection() -> None:
    calls: list[tuple[str, dict]] = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, deepcopy(body)))
        if path.endswith("get_site_collect_item_info"):
            return {
                "result": "success",
                "data": {
                    "siteCollectItemInfo": {
                        "skuMap": {
                            ";opaque;": {
                                "itemNum": "source-offer",
                                "stock": 300,
                            }
                        },
                        "collectBoxDetailShopList": [
                            {"shopId": SHOP_IDS["tiktok:LH_PH"]}
                        ],
                    },
                    "ossMd5": "revision-prewrite",
                },
            }
        if path.endswith("get_shop_warehouse_list"):
            return {
                "result": "success",
                "data": {
                    "shopWarehouseList": [
                        {
                            "shopId": str(body["shopIds"][0]),
                            "warehouseList": [
                                {
                                    "warehouseId": "warehouse-ph",
                                    "warehouseName": "The Chinese mainland Pickup Warehouse",
                                    "warehouseEffectStatus": "1",
                                    "isDefault": "1",
                                }
                            ],
                        }
                    ]
                },
            }
        raise AssertionError("a pre-write failure must not send a save request")

    snapshot = _snapshot()
    target = snapshot["publication_targets"][0]
    draft = _draft_payload(
        snapshot,
        target=target,
        category=CategoryResolver().resolve(
            target=target,
            product=snapshot["product"],
            skus=snapshot["skus"],
        ),
    )
    fact = MiaoshouOpenApiTikTokV4DraftTransport(
        common_detail_id="5001",
        post=post,
    ).save_draft(
        identity={
            "target_label": "tiktok:LH_PH",
            "detail_id": "7301",
            "shop_id": SHOP_IDS["tiktok:LH_PH"],
        },
        draft=draft,
    )

    assert fact.operation == "SAVE_DRAFT"
    assert fact.outcome == "REJECTED"
    assert not any(path.endswith("save_site_collect_item_info") for path, _ in calls)


def test_read_only_preparation_retries_but_save_is_sent_once() -> None:
    calls: list[str] = []
    read_attempts = 0
    warehouse_attempts = 0

    def post(path: str, body: dict) -> dict:
        nonlocal read_attempts, warehouse_attempts
        calls.append(path)
        if path.endswith("get_site_collect_item_info"):
            read_attempts += 1
            if read_attempts == 1:
                raise MiaoshouBusinessRejectedError("draft is materializing")
            return _editable_site_payload(site=body["site"], revision="retry-md5")
        if path.endswith("get_shop_warehouse_list"):
            warehouse_attempts += 1
            if warehouse_attempts == 1:
                raise MiaoshouBusinessRejectedError("warehouse is materializing")
            shop_id = str(body["shopIds"][0])
            return {
                "result": "success",
                "data": {
                    "shopWarehouseList": [
                        {
                            "shopId": shop_id,
                            "warehouseList": [
                                    {
                                        "warehouseId": f"warehouse-{shop_id}",
                                        "warehouseName": "The Chinese mainland Pickup Warehouse",
                                        "warehouseEffectStatus": "1",
                                    "isDefault": "1",
                                }
                            ],
                        }
                    ]
                },
            }
        if path.endswith("save_site_collect_item_info"):
            return {"result": "success", "data": {}}
        raise AssertionError(path)

    snapshot = _snapshot()
    target = snapshot["publication_targets"][0]
    fact = MiaoshouOpenApiTikTokV4DraftTransport(
        common_detail_id="5001",
        post=post,
        read_retry_seconds=0,
    ).save_draft(
        identity={
            "target_label": "tiktok:LH_PH",
            "detail_id": "7301",
            "shop_id": SHOP_IDS["tiktok:LH_PH"],
        },
        draft=_draft_payload(
            snapshot,
            target=target,
            category=CategoryResolver().resolve(
                target=target,
                product=snapshot["product"],
                skus=snapshot["skus"],
            ),
        ),
    )

    assert fact.outcome == "ACCEPTED"
    assert read_attempts == 3
    assert warehouse_attempts == 2
    assert sum(path.endswith("save_site_collect_item_info") for path in calls) == 1


def test_production_seam_reuses_exact_claimed_target_identities_without_reclaim() -> None:
    calls: list[tuple[str, dict]] = []
    observed: list[DraftWriteFact] = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, deepcopy(body)))
        if path.endswith("get_site_collect_item_info"):
            return _editable_site_payload(site=body["site"], revision="revision-2")
        if path.endswith("get_shop_warehouse_list"):
            return _warehouse_payload(str(body["shopIds"][0]))
        return {"result": "success", "data": {}}

    receipt = prepare_tiktok_v4_drafts(
        _snapshot(),
        category_resolver=CategoryResolver(),
        transport=MiaoshouOpenApiTikTokV4DraftTransport(
            common_detail_id="5001",
            platform_detail_ids_by_target={
                "tiktok:LH_PH": "7301",
                "tiktok:LH_MY": "7302",
            },
            post=post,
            fact_observer=lambda _label, fact: observed.append(fact),
        ),
    )

    assert receipt["status"] == "PREPARED"
    assert receipt["external_write_count"] == 2
    assert [fact.operation for fact in observed] == [
        "IDENTITY_OBSERVED",
        "IDENTITY_OBSERVED",
        "SAVE_DRAFT",
        "SAVE_DRAFT",
    ]
    assert not any(path.endswith("claim_to_shop") for path, _ in calls)
    assert not any(path.endswith("common_collect_box/claimed") for path, _ in calls)


def test_v4_draft_binds_exact_approved_two_warehouse_allocation() -> None:
    snapshot = _snapshot()
    target = snapshot["publication_targets"][0]
    shop_id = SHOP_IDS["tiktok:LH_PH"]
    draft = _draft_payload(
        snapshot,
        target=target,
        category=CategoryResolver().resolve(
            target=target,
            product=snapshot["product"],
            skus=snapshot["skus"],
        ),
    )
    allocation = {
        "schema_version": "miaoshou-tiktok-warehouse-allocation/v1",
        "shop_id": shop_id,
        "warehouses": [
            {
                "warehouse_id": "7635880818728552209",
                "warehouse_name": "瓯江口",
                "stock": 500,
            },
            {
                "warehouse_id": "7677806106640287506",
                "warehouse_name": "TH8806",
                "stock": 40,
            },
        ],
        "total_stock": 540,
        "source": "CONVERSATION_APPROVAL",
        "approved_by": "Kyle",
        "approved_at": "2026-08-30T00:00:00+08:00",
    }
    draft["warehouse_inventory"] = allocation
    current = _editable_site_payload(site="PH", revision="r")["data"][
        "siteCollectItemInfo"
    ]

    def post(_path: str, _body: dict) -> dict:
        return {
            "data": {
                "shopWarehouseList": [
                    {
                        "shopId": shop_id,
                        "warehouseList": [
                            {
                                "warehouseId": "7635880818728552209",
                                "warehouseName": "瓯江口",
                                "warehouseEffectStatus": "1",
                            },
                            {
                                "warehouseId": "7677806106640287506",
                                "warehouseName": "TH8806",
                                "warehouseEffectStatus": "1",
                            },
                        ],
                    }
                ]
            }
        }

    stock_map, legacy_id, zero_ids, total = _tiktok_warehouse_allocation(
        post,
        current=current,
        shop_id=shop_id,
        approved=allocation,
    )
    bound = _provider_bound_sku_map(
        current=current,
        draft=draft,
        desired=_miaoshou_draft_info(draft)["skuMap"],
        shop_id=shop_id,
        warehouse_id=legacy_id,
        zero_stock_warehouse_ids=zero_ids,
        approved_warehouse_map=stock_map,
        approved_total=total,
    )

    assert total == 540
    assert all(row["stock"] == 540 for row in bound.values())
    assert all(
        row["shopIdToWarehouseIdAndStockMap"][shop_id]
        == {
            "7635880818728552209": "500",
            "7677806106640287506": "40",
        }
        for row in bound.values()
    )


def test_v4_homebloom_oujiangkou_is_the_exact_mainland_pickup_warehouse() -> None:
    shop_id = "16770557"

    def post(_path: str, body: dict) -> dict:
        assert body == {"shopIds": [shop_id]}
        return {
            "data": {
                "shopWarehouseList": [
                    {
                        "shopId": shop_id,
                        "warehouseList": [
                            {
                                "warehouseId": "7635880818728552209",
                                "warehouseName": "瓯江口",
                                "warehouseEffectStatus": "1",
                            },
                            {
                                "warehouseId": "7677806106640287506",
                                "warehouseName": "TH8806",
                                "warehouseEffectStatus": "1",
                            },
                        ],
                    }
                ]
            }
        }

    stock_map, mainland_id, zero_ids, total = _tiktok_warehouse_allocation(
        post,
        current=_editable_site_payload(site="PH", revision="r")["data"][
            "siteCollectItemInfo"
        ],
        shop_id=shop_id,
    )

    assert stock_map is None
    assert mainland_id == "7635880818728552209"
    assert zero_ids == ("7677806106640287506",)
    assert total is None


def _warehouse_payload(shop_id: str, *, local_id: str | None = None) -> dict:
    rows = [
        {
            "warehouseId": f"warehouse-{shop_id}",
            "warehouseName": "The Chinese mainland Pickup Warehouse",
            "warehouseEffectStatus": "1",
            "isDefault": "1",
        }
    ]
    if local_id:
        rows.append(
            {
                "warehouseId": local_id,
                "warehouseName": "Local Warehouse",
                "warehouseEffectStatus": "1",
            }
        )
    return {
        "result": "success",
        "data": {
            "shopWarehouseList": [
                {"shopId": shop_id, "warehouseList": rows}
            ]
        },
    }


def test_target_only_draft_preparation_never_claims_or_saves_other_targets() -> None:
    transport = Transport()

    receipt = prepare_tiktok_v4_drafts(
        _snapshot(),
        category_resolver=CategoryResolver(),
        transport=transport,
        target_scope=("tiktok:LH_MY",),
    )

    assert [row["target_label"] for row in receipt["targets"]] == [
        "tiktok:LH_MY"
    ]
    assert [row["target"]["target_label"] for row in transport.claims] == [
        "tiktok:LH_MY"
    ]
    assert [row["identity"]["target_label"] for row in transport.saves] == [
        "tiktok:LH_MY"
    ]
    assert set(receipt["collectbox_contexts"]) == {"tiktok:LH_MY"}


def test_v4_draft_uses_exact_target_localized_content() -> None:
    snapshot = _snapshot()
    snapshot["product"]["content_by_target"] = {
        target["target_label"]: {
            "locale": "ms-MY" if target["target_label"] == "tiktok:LH_MY" else "en-PH",
            "title": (
                "Tajuk MY yang diluluskan"
                if target["target_label"] == "tiktok:LH_MY"
                else "Approved PH title"
            ),
            "description": (
                "Penerangan MY yang diluluskan"
                if target["target_label"] == "tiktok:LH_MY"
                else "Approved PH description"
            ),
        }
        for target in snapshot["publication_targets"]
    }
    unsigned = deepcopy(snapshot)
    unsigned.pop("snapshot_digest")
    snapshot["snapshot_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    transport = Transport()

    receipt = prepare_tiktok_v4_drafts(
        snapshot,
        category_resolver=CategoryResolver(),
        transport=transport,
        target_scope=("tiktok:LH_MY",),
    )

    assert receipt["status"] == "PREPARED"
    my_draft = transport.saves[0]["draft"]
    assert my_draft["title"] == "Tajuk MY yang diluluskan"
    assert my_draft["description"] == "Penerangan MY yang diluluskan"


def test_phase_a_rejects_provider_overlong_specification_before_any_claim() -> None:
    snapshot = _snapshot()
    snapshot["skus"][0]["specification"] = {"option": "ก" * 51}
    unsigned = deepcopy(snapshot)
    unsigned.pop("snapshot_digest")
    snapshot["snapshot_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    transport = Transport()

    with pytest.raises(TikTokV4SystemicPreflightError, match="projection"):
        prepare_tiktok_v4_drafts(
            snapshot, category_resolver=CategoryResolver(), transport=transport
        )

    assert transport.claims == []
    assert transport.saves == []


def test_mutation_budget_callback_runs_before_claim_transport() -> None:
    events: list[str] = []

    def post(path: str, _body: dict) -> dict:
        events.append("provider:" + path.rsplit("/", 1)[-1])
        return {"result": "success", "data": {}}

    transport = MiaoshouOpenApiTikTokV4DraftTransport(
        common_detail_id="5001",
        initial_platform_detail_id="7301",
        post=post,
        before_mutation=lambda _label, operation: events.append(
            "budget:" + operation
        ),
    )

    facts = transport.claim_or_create(
        target={
            "target_label": "tiktok:LH_PH",
            "shop_id": SHOP_IDS["tiktok:LH_PH"],
        },
        ordinal=0,
    )

    assert facts[-1].operation == "CLAIM_TO_SHOP"
    assert events == ["budget:claim_draft", "provider:claim_to_shop"]


def test_local_mutation_budget_failure_is_not_reported_as_provider_unknown() -> None:
    provider_called = False

    def post(_path: str, _body: dict) -> dict:
        nonlocal provider_called
        provider_called = True
        return {"result": "success", "data": {}}

    def reject_budget(_label: str, _operation: str) -> None:
        raise RuntimeError("local mutation budget exceeded")

    transport = MiaoshouOpenApiTikTokV4DraftTransport(
        common_detail_id="5001",
        post=post,
        before_mutation=reject_budget,
    )

    with pytest.raises(RuntimeError, match="local mutation budget exceeded"):
        transport.claim_or_create(
            target={
                "target_label": "tiktok:LH_PH",
                "shop_id": SHOP_IDS["tiktok:LH_PH"],
            },
            ordinal=0,
        )

    assert provider_called is False


def test_all_ten_target_save_payloads_preserve_cod_except_exact_gb_contract() -> None:
    snapshot = _snapshot()
    target = snapshot["publication_targets"][0]
    draft = _draft_payload(
        snapshot,
        target=target,
        category=CategoryResolver().resolve(
            target=target,
            product=snapshot["product"],
            skus=snapshot["skus"],
        ),
    )
    desired_skus = _miaoshou_draft_info(draft)["skuMap"]
    expected_attributes = [
        {
            "attributeId": "102255",
            "attributeName": "Batch Number",
            "attributeNameAlias": "Batch Number",
            "attributeValues": [
                {
                    "valueName": "1",
                    "valueId": "1000256",
                    "valueNameAlias": "1",
                }
            ],
        }
    ]

    def post(path: str, body: dict) -> dict:
        if path == tiktok_v4_drafts.CATEGORY_METADATA_PATH:
            assert body["site"] == "GB"
            return {
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
                }
            }
        if path == tiktok_v4_drafts.WAREHOUSE_GET_PATH:
            return _warehouse_payload(str(body["shopIds"][0]))
        shop_id = str(body.get("shopId") or tiktok_v4_drafts.EXPECTED_SHOP_ID_BY_TARGET[
            next(
                label
                for label in tiktok_v4_drafts.EXPECTED_SHOP_ID_BY_TARGET
                if label.endswith("_" + str(body.get("site") or ""))
            )
        ])
        info = {
            "isCodOpen": "1",
            "productAttributes": [],
            "skuMap": {
                key: {
                    "itemNum": row["itemNum"],
                    "stock": 100,
                    "shopIdToWarehouseIdAndStockMap": {
                        shop_id: {f"warehouse-{shop_id}": "100"}
                    },
                }
                for key, row in desired_skus.items()
            },
        }
        field = (
            "siteCollectItemInfo"
            if path == tiktok_v4_drafts.READ_SITE_DRAFT_PATH
            else "shopCollectItemInfo"
        )
        return {"data": {field: info, "ossMd5": "read-only-revision"}}

    transport = MiaoshouOpenApiTikTokV4DraftTransport(
        common_detail_id="5001", post=post, read_attempts=1
    )
    assert len(tiktok_v4_drafts.EXPECTED_SHOP_ID_BY_TARGET) == 10
    for label, shop_id in tiktok_v4_drafts.EXPECTED_SHOP_ID_BY_TARGET.items():
        prepared = transport.prepare_save_draft(
            identity={
                "target_label": label,
                "detail_id": "7301",
                "shop_id": shop_id,
            },
            draft=draft,
        )
        body = prepared["body"]
        info = body.get("siteCollectItemInfo") or body["shopCollectItemInfo"]
        if label == "tiktok:GB":
            assert info["isCodOpen"] == "0"
            assert info["productAttributes"] == expected_attributes
        else:
            assert info["isCodOpen"] == "1"
            assert info["productAttributes"] == []


def test_save_rejection_retains_only_sanitized_provider_diagnostics() -> None:
    def rejected(_path: str, _body: dict) -> dict:
        raise MiaoshouBusinessRejectedError(
            "invalid image https://provider.example/raw token=super-secret",
            code="FIELD.INVALID",
            field_path="skuMap[0].imgUrls[6]",
        )

    fact = MiaoshouOpenApiTikTokV4DraftTransport(
        common_detail_id="5001", post=rejected
    ).save_prepared_draft(
        identity={
            "target_label": "tiktok:LH_PH",
            "detail_id": "7301",
            "shop_id": SHOP_IDS["tiktok:LH_PH"],
        },
        prepared={
            "path": tiktok_v4_drafts.SAVE_SITE_DRAFT_PATH,
            "body": {"detailId": 7301, "site": "PH"},
        },
    )

    assert fact.outcome == "REJECTED"
    assert fact.provider_code == "FIELD.INVALID"
    assert fact.provider_field_path == "skuMap[0].imgUrls[6]"
    assert fact.provider_reason == "invalid image [redacted-url] token=[redacted]"
    assert fact.public_fact() == {
        "operation": "SAVE_DRAFT",
        "outcome": "REJECTED",
        "provider_code": "FIELD.INVALID",
        "provider_field_path": "skuMap[0].imgUrls[6]",
        "provider_reason": "invalid image [redacted-url] token=[redacted]",
    }
