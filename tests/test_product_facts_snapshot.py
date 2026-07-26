from copy import deepcopy

from domains.product_operations.product_facts import (
    build_product_facts_snapshot,
)


def _source(*, prices=("8.1", "0.2")):
    return {
        "title_source": "PVC wall decal",
        "seller_sku": "0946",
        "cost_cny": 0.2,
        "weight_kg": 0.2,
        "package_cm": [20, 20, 3],
        "category": {"name": "Wall decals"},
        "video": {"action": "none", "url": ""},
        "skus": [
            {
                "key": ";30*90cm*2pcs;半透自粘款;",
                "name": "30*90cm*2pcs 半透自粘款",
                "price": prices[0],
            },
            {
                "key": ";30*90cm*2pcs;需要其他材质咨询客服定制;",
                "name": "30*90cm*2pcs 需要其他材质咨询客服定制",
                "price": prices[1],
            },
        ],
    }


def test_snapshot_blocks_selected_price_conflict_and_custom_placeholder_without_rewriting_cost():
    source = _source()
    review = {
        "cost_cny": 0.2,
        "selected_sku_keys": [
            ";30*90cm*2pcs;半透自粘款;",
            ";30*90cm*2pcs;需要其他材质咨询客服定制;",
        ],
    }
    source_before = deepcopy(source)
    review_before = deepcopy(review)

    snapshot = build_product_facts_snapshot(
        product_id="3828540231",
        source=source,
        review=review,
    )

    assert snapshot.ready is False
    assert any("selected SKU prices conflict" in blocker for blocker in snapshot.blockers)
    assert any("customer-service/custom placeholder" in blocker for blocker in snapshot.blockers)
    cost = snapshot.field("cost_cny")
    assert cost is not None
    assert cost.value == 0.2
    assert cost.selected_source == "review.cost_cny"
    assert [fact.payload()["price_cny"] for fact in snapshot.selected_sku_prices] == [
        "8.1",
        "0.2",
    ]
    assert source == source_before
    assert review == review_before


def test_snapshot_records_field_sources_and_accepts_one_supported_price():
    source = _source(prices=("8.1", "8.1"))
    review = {
        "cost_cny": "8.1",
        "title": "Cute PVC Wall Decal",
        "selected_sku_keys": [";30*90cm*2pcs;半透自粘款;"],
    }

    snapshot = build_product_facts_snapshot(
        product_id="product-1",
        source=source,
        review=review,
    )

    assert snapshot.ready is True
    assert snapshot.blockers == ()
    assert snapshot.field("title").selected_source == "review.title"
    assert [
        candidate.source for candidate in snapshot.field("title").candidates
    ] == ["review.title", "source.title_source"]
    assert snapshot.payload()["fields"]["category"]["value"] == {
        "name": "Wall decals"
    }


def test_snapshot_warns_but_does_not_block_reviewed_cost_that_differs_from_source_price():
    source = _source(prices=("8.1", "8.1"))

    snapshot = build_product_facts_snapshot(
        product_id="product-1",
        source=source,
        review={
            "cost_cny": "6.5",
            "selected_sku_keys": [";30*90cm*2pcs;半透自粘款;"],
        },
    )

    assert snapshot.ready is True
    assert not snapshot.blockers
    assert any(
        "cost_cny does not match the selected SKU price" in warning
        for warning in snapshot.warnings
    )


def test_snapshot_uses_all_source_skus_when_legacy_review_has_no_selection():
    snapshot = build_product_facts_snapshot(
        product_id="product-1",
        source=_source(),
        review={},
    )

    assert len(snapshot.selected_sku_prices) == 2
    assert any("selected SKU prices conflict" in blocker for blocker in snapshot.blockers)
