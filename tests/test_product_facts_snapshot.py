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


def test_snapshot_allows_distinct_sku_costs_but_blocks_custom_placeholder():
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
    assert not any("selected SKU prices conflict" in blocker for blocker in snapshot.blockers)
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
    assert not any("selected SKU prices conflict" in blocker for blocker in snapshot.blockers)
    assert any("customer-service/custom placeholder" in blocker for blocker in snapshot.blockers)


def test_selected_variants_keep_independent_commercial_facts():
    source = {
        "title_source": "Three-size wall decor",
        "skus": [
            {"key": "a", "name": "35 x 140", "price": "15"},
            {"key": "b", "name": "35 x 200", "price": "18"},
            {"key": "c", "name": "35 x 300", "price": "22"},
        ],
    }
    review = {
        "selected_sku_keys": ["a", "b", "c"],
        "sku_commercial_facts": {
            "a": {
                "cost_cny": 15,
                "weight_kg": 0.1,
                "package_cm": [20, 20, 3],
            },
            "b": {
                "cost_cny": 18,
                "weight_kg": 0.4,
                "package_cm": [35, 20, 10],
            },
            "c": {
                "cost_cny": 22,
                "weight_kg": 0.8,
                "package_cm": [45, 30, 15],
            },
        },
    }

    payload = build_product_facts_snapshot(
        product_id="3838599504",
        source=source,
        review=review,
    ).payload()

    assert payload["ready"] is True
    assert not any(
        "selected SKU prices conflict" in blocker
        for blocker in payload["blockers"]
    )
    assert payload["selected_sku_commercial_facts"] == [
        {
            "selected_key": "a",
            "source_key": "a",
            "label": "35 x 140",
            "cost_cny": "15",
            "weight_kg": "0.1",
            "package_cm": ["20", "20", "3"],
            "source_price_cny": "15",
            "source": "review.sku_commercial_facts",
        },
        {
            "selected_key": "b",
            "source_key": "b",
            "label": "35 x 200",
            "cost_cny": "18",
            "weight_kg": "0.4",
            "package_cm": ["35", "20", "10"],
            "source_price_cny": "18",
            "source": "review.sku_commercial_facts",
        },
        {
            "selected_key": "c",
            "source_key": "c",
            "label": "35 x 300",
            "cost_cny": "22",
            "weight_kg": "0.8",
            "package_cm": ["45", "30", "15"],
            "source_price_cny": "22",
            "source": "review.sku_commercial_facts",
        },
    ]
