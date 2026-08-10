from __future__ import annotations

from copy import deepcopy
import inspect

from modules.miaoshou.tiktok_v4_drafts import (
    DraftWriteFact,
    MiaoshouOpenApiTikTokV4DraftTransport,
    prepare_tiktok_v4_drafts,
)
from modules.miaoshou import tiktok_v4_drafts
from test_tiktok_v4_execution import _snapshot


SHOP_IDS = {
    "tiktok:LH_PH": "7676267",
    "tiktok:LH_MY": "13295169",
}


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
        "SAVE_DRAFT",
        "CREATE_DRAFT",
        "CLAIM_TO_SHOP",
        "SAVE_DRAFT",
    ]
    assert [path for path, _ in calls] == [
        "/open/v1/product/common_collect_box/common_collect_box/claimed",
        "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop",
        "/open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info",
        "/open/v1/product/collect_box/tiktok/collect_box/save_site_collect_item_info",
        "/open/v1/product/common_collect_box/common_collect_box/claimed",
        "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop",
        "/open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info",
        "/open/v1/product/collect_box/tiktok/collect_box/save_site_collect_item_info",
    ]
    first_save = calls[3][1]
    assert first_save["detailId"] == 7101
    assert first_save["site"] == "PH"
    assert first_save["ossMd5"] == "revision-1"
    info = first_save["siteCollectItemInfo"]
    assert info["providerRequired"] == "keep"
    assert info["title"] == _snapshot()["product"]["title"]
    assert info["notes"] == _snapshot()["product"]["description"]
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


def test_production_seam_reuses_exact_claimed_target_identities_without_reclaim() -> None:
    calls: list[tuple[str, dict]] = []
    observed: list[DraftWriteFact] = []

    def post(path: str, body: dict) -> dict:
        calls.append((path, deepcopy(body)))
        if path.endswith("get_site_collect_item_info"):
            return _editable_site_payload(site=body["site"], revision="revision-2")
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
        "SAVE_DRAFT",
        "IDENTITY_OBSERVED",
        "SAVE_DRAFT",
    ]
    assert not any(path.endswith("claim_to_shop") for path, _ in calls)
    assert not any(path.endswith("common_collect_box/claimed") for path, _ in calls)
