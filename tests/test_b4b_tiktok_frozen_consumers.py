from copy import deepcopy

import pytest

from domains.channel_operations.tiktok_v4_execution import (
    TikTokV4ExecutionContractError,
    project_tiktok_v4_execution_plan,
)
from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport, WAREHOUSE_GET_PATH
from shared_platform.product_description_media import miaoshou_rich_description
from test_tiktok_v4_execution import _snapshot, _context, CategoryResolver
from test_tiktok_independent_publisher import _snapshot as publisher_snapshot, _draft_response


def test_target_subset_keeps_frozen_snapshot_and_does_not_prepare_other_shop():
    snapshot = _snapshot()
    resolver = CategoryResolver()
    plan = project_tiktok_v4_execution_plan(
        snapshot,
        collectbox_contexts={"tiktok:LH_MY": _context(snapshot, "tiktok:LH_MY", "7002")},
        category_resolver=resolver,
        target_scope=("tiktok:LH_MY",),
    )
    assert plan["snapshot_digest"] == snapshot["snapshot_digest"]
    assert [row["target_label"] for row in plan["targets"]] == ["tiktok:LH_MY"]
    assert [label for label, _ in resolver.calls] == ["tiktok:LH_MY"]


@pytest.mark.parametrize("scope", [(), ("tiktok:LH_MY", "tiktok:LH_MY"), ("tiktok:HB_MY",)])
def test_invalid_or_unapproved_target_subset_has_zero_provider_reads(scope):
    resolver = CategoryResolver()
    with pytest.raises(TikTokV4ExecutionContractError):
        project_tiktok_v4_execution_plan(
            _snapshot(), collectbox_contexts={}, category_resolver=resolver,
            target_scope=scope,
        )
    assert resolver.calls == []


def _prepared_fixture(*, local_stock="0"):
    target = publisher_snapshot(targets=("tiktok:LH_MY",))["targets"][0]
    shop = target["shop_id"]
    raw = _draft_response("tiktok:LH_MY", target)["data"]["siteCollectItemInfo"]
    info = deepcopy(raw)
    info["notes"] = miaoshou_rich_description(target["expected_description"], target["expected_images"])
    for row in info["skuMap"].values():
        row["stock"] = 300
        row["shopIdToWarehouseIdAndStockMap"] = {shop: {"90001": "300", "90002": local_stock}}
    calls = []
    def post(path, body):
        calls.append((path, deepcopy(body)))
        assert path == WAREHOUSE_GET_PATH, "fixture allows exact-shop read only"
        assert body == {"shopIds": [shop]}
        return {"result": "success", "data": {"shopWarehouseList": [{"shopId": shop, "warehouseList": [
            {"warehouseId": "90001", "warehouseName": "The Chinese mainland Pickup Warehouse", "warehouseEffectStatus": "1"},
            {"warehouseId": "90002", "warehouseName": "Local Warehouse", "warehouseEffectStatus": "1"},
        ]}]}}
    return target, info, MiaoshouTikTokTransport(post=post), calls


def test_exact_prepared_rich_description_and_warehouse_allocation_reads_back():
    target, info, transport, calls = _prepared_fixture()
    assert transport.draft_matches(target, {"info": info}) is True
    assert calls and all(path == WAREHOUSE_GET_PATH for path, _ in calls)


def test_positive_unapproved_local_stock_cannot_be_accepted_as_matching():
    target, info, transport, calls = _prepared_fixture(local_stock="1")
    assert transport.draft_matches(target, {"info": info}) is False
    assert all(path == WAREHOUSE_GET_PATH for path, _ in calls)


@pytest.mark.parametrize("quantity", ["3 pcs", "30 pcs"])
def test_structured_size_and_count_are_not_confused_with_dimension_numbers(quantity):
    assert MiaoshouTikTokTransport._approved_variant_display({
        "size": "30 x 45 cm", "quantity": quantity,
    }) == f"30 x 45 cm ({quantity})"


def test_accepted_save_does_not_approve_arbitrary_different_title():
    target, info, transport, calls = _prepared_fixture()
    info["title"] = "A completely different product"
    assert transport.post_save_draft_matches(target, {"info": info}) is False
    assert all(path == WAREHOUSE_GET_PATH for path, _ in calls)
