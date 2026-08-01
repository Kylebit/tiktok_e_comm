from datetime import datetime, timezone

from domains.supply_chain_operations.order_demand import (
    aggregate_shopee_orders,
    aggregate_tiktok_orders,
    canonical_order_sku,
    finalize_order_snapshot,
)


def test_order_sku_mapping_is_country_exact_and_preserves_source():
    assert canonical_order_sku("660123", "MY") == ("0123", "660123")
    assert canonical_order_sku("990401", "TH") == ("0401", "990401")
    assert canonical_order_sku("880004", "VN") == ("0004", "880004")
    assert canonical_order_sku("770820", "PH") == ("0820", "770820")
    assert canonical_order_sku("0820", "PH") == ("0820", "0820")
    assert canonical_order_sku("660123", "TH") is None
    assert canonical_order_sku("7708…", "PH") is None
    assert canonical_order_sku(770820, "PH") is None


def test_tiktok_uses_paid_time_and_excludes_cancelled_sample_and_replacement():
    good = {
        "id": "o1",
        "status": "IN_TRANSIT",
        "paid_time": 1785542400,
        "create_time": 1785456000,
        "line_items": [
            {"seller_sku": "660123", "product_name": "Test product", "sku_image": "https://example.invalid/image.jpg"},
            {"seller_sku": "660123", "product_name": "Test product", "sku_image": "https://example.invalid/image.jpg"},
        ],
    }
    orders = [
        good,
        {**good, "id": "o2", "status": "CANCELLED"},
        {**good, "id": "o3", "is_sample_order": True},
        {**good, "id": "o4", "is_replacement_order": True},
    ]

    rows, evidence = aggregate_tiktok_orders(orders, "MY")

    assert rows["0123"]["units"] == 2
    assert rows["0123"]["order_ids"] == {"o1"}
    assert rows["0123"]["source_aliases"] == {"660123"}
    assert rows["0123"]["name"] == "Test product"
    assert rows["0123"]["image_url"].endswith("image.jpg")
    assert evidence["orders_included"] == 1
    assert evidence["orders_excluded"] == 3


def test_shopee_uses_purchased_less_cancelled_and_tracks_returns_separately():
    rows, evidence = aggregate_shopee_orders(
        [
            {
                "order_sn": "s1",
                "order_status": "SHIPPED",
                "create_time": 1785542400,
                "item_list": [
                    {
                        "model_sku": "0401",
                        "model_quantity_purchased": 4,
                        "cancelled_qty": 1,
                        "returned_qty": 2,
                    }
                ],
            },
            {
                "order_sn": "s2",
                "order_status": "UNPAID",
                "create_time": 1785542400,
                "item_list": [
                    {"model_sku": "0401", "model_quantity_purchased": 9}
                ],
            },
        ],
        "TH",
    )

    assert rows["0401"]["units"] == 3
    assert rows["0401"]["cancelled_units"] == 1
    assert rows["0401"]["returned_units"] == 2
    assert evidence["orders_included"] == 1
    assert evidence["orders_excluded"] == 1


def test_final_snapshot_is_order_based_and_digest_bound():
    rows, evidence = aggregate_tiktok_orders(
        [
            {
                "id": "o1",
                "status": "DELIVERED",
                "paid_time": 1785542400,
                "line_items": [{"seller_sku": "770820"}],
            }
        ],
        "PH",
    )
    snapshot = finalize_order_snapshot(
        rows,
        region="PH",
        platform="TikTok",
        captured_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        days=31,
        evidence=evidence,
    )

    fact = snapshot["facts"]["0820"]
    assert fact["quantityBasis"] == "valid_order"
    assert fact["sourceAliases"] == ["770820"]
    assert fact["recent30Units"] == 1
    assert len(snapshot["digest"]) == 64
