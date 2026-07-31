from domains.supply_chain_operations.shopee_demand import (
    aggregate_escrow_details,
    canonical_demand_sku,
)


def test_channel_sku_aliases_are_explicit_and_never_arbitrarily_truncated():
    assert canonical_demand_sku("0401") == "0401"
    assert canonical_demand_sku("990401") == "0401"
    assert canonical_demand_sku("770401") == "0401"
    assert canonical_demand_sku("880401") is None
    assert canonical_demand_sku("prefix0401") is None
    assert canonical_demand_sku(990401) is None


def test_aggregate_escrow_details_counts_units_orders_recent_and_shipping():
    aggregate, evidence = aggregate_escrow_details(
        [
            {
                "release_ts": 200,
                "detail": {
                    "order_income": {
                        "actual_shipping_fee": -30,
                        "items": [
                            {
                                "model_sku": "990401",
                                "quantity_purchased": 2,
                                "discounted_price": 10,
                            },
                            {
                                "model_sku": "0401",
                                "quantity_purchased": 1,
                                "discounted_price": 10,
                            },
                        ],
                    }
                },
            },
            {
                "release_ts": 50,
                "detail": {
                    "order_income": {
                        "actual_shipping_fee": -5,
                        "items": [
                            {
                                "model_sku": "770401",
                                "quantity_purchased": 1,
                                "discounted_price": 5,
                            }
                        ],
                    }
                },
            },
        ],
        window_days=366,
        recent_cutoff_ts=100,
    )

    assert aggregate["0401"] == {
        "days": 366,
        "orders": 2,
        "units": 4,
        "recent30Units": 3,
        "customerPayment": 35.0,
        "actualShippingFee": -35.0,
        "sourceAliases": ["0401", "770401", "990401"],
    }
    assert evidence == {
        "details": 2,
        "catalog_resolved_items": 0,
        "rejected_items": 0,
    }


def test_unstructured_channel_sku_uses_exact_item_model_catalog_mapping():
    aggregate, evidence = aggregate_escrow_details(
        [
            {
                "release_ts": 200,
                "detail": {
                    "order_income": {
                        "items": [
                            {
                                "item_id": 11,
                                "model_id": 22,
                                "model_sku": "601099679895991_5pcs",
                                "quantity_purchased": 2,
                                "discounted_price": 9,
                            }
                        ]
                    }
                },
            }
        ],
        window_days=366,
        recent_cutoff_ts=100,
        catalog_sku_by_model={(11, 22): "990401"},
    )
    assert aggregate["0401"]["units"] == 2
    assert aggregate["0401"]["sourceAliases"] == ["601099679895991_5pcs"]
    assert evidence["catalog_resolved_items"] == 1
    assert evidence["rejected_items"] == 0


def test_invalid_sku_or_quantity_is_rejected_fail_closed():
    aggregate, evidence = aggregate_escrow_details(
        [
            {
                "release_ts": 200,
                "detail": {
                    "order_income": {
                        "items": [
                            {"model_sku": "082X", "quantity_purchased": 2},
                            {"model_sku": "770820", "quantity_purchased": True},
                            {"model_sku": "770821", "quantity_purchased": 0},
                        ]
                    }
                },
            }
        ],
        window_days=30,
        recent_cutoff_ts=100,
    )
    assert aggregate == {}
    assert evidence == {
        "details": 1,
        "catalog_resolved_items": 0,
        "rejected_items": 3,
    }


def test_aggregate_can_emit_exact_segmented_trend_from_release_dates():
    day = 86400
    trend_end = 100 * day
    details = []
    for age, quantity in ((1, 14), (8, 16)):
        details.append(
            {
                "release_ts": trend_end - age * day,
                "detail": {
                    "order_income": {
                        "items": [
                            {
                                "model_sku": "0401",
                                "quantity_purchased": quantity,
                                "discounted_price": 1,
                            }
                        ]
                    }
                },
            }
        )

    aggregate, _ = aggregate_escrow_details(
        details,
        window_days=30,
        recent_cutoff_ts=trend_end - 30 * day,
        trend_end_ts=trend_end,
    )

    trend = aggregate["0401"]["trendDecision"]
    assert trend["units"] == {
        "last7": 14.0,
        "days8To15": 16.0,
        "days16To30": 0.0,
    }
    assert trend["dailyVelocity"] == 1.8
    assert trend["forecast30Units"] == 54


def test_segmented_trend_excludes_records_at_or_after_window_end():
    day = 86400
    trend_end = 100 * day
    aggregate, _ = aggregate_escrow_details(
        [
            {
                "release_ts": trend_end - day,
                "detail": {
                    "order_income": {
                        "items": [
                            {
                                "model_sku": "0401",
                                "quantity_purchased": 3,
                                "discounted_price": 1,
                            }
                        ]
                    }
                },
            },
            {
                "release_ts": trend_end,
                "detail": {
                    "order_income": {
                        "items": [
                            {
                                "model_sku": "0401",
                                "quantity_purchased": 99,
                                "discounted_price": 1,
                            }
                        ]
                    }
                },
            },
        ],
        window_days=30,
        recent_cutoff_ts=trend_end - 30 * day,
        trend_end_ts=trend_end,
    )

    assert aggregate["0401"]["recent30Units"] == 3
    assert aggregate["0401"]["trendDecision"]["units"]["last7"] == 3.0
