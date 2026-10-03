from copy import deepcopy
from pathlib import Path

import pytest

from modules.ozon import target_scoped


def manifest(**overrides):
    value = {
        "schema_version": "ozon-existing-product-stock-command/v2",
        "builder_policy_version": "target-scoped-ozon-stock/v2",
        "target_label": "ozon:RU",
        "operation_kind": "ozon_existing_product_stock_reconciliation_v1",
        "product_id": "7654321098",
        "seller_sku": "0988",
        "offer_id": "0988",
        "expected_listing_digest": "pending",
        "desired_stock_quantity": 37,
        "warehouse_id": 1020005018928780,
        "warehouse_policy": "exact_active_non_kgt",
        "existing_product_only": True,
        "forbid_import": True,
        "forbid_create": True,
    }
    value.update(overrides)
    return value


def item():
    return {
        "id": 7654321098,
        "offer_id": "0988",
        "name": "Approved listing",
        "price": "56.00",
        "images": ["https://example.test/1.jpg"],
        "description_category_id": 17027906,
        "statuses": {
            "is_created": True,
            "moderate_status": "approved",
            "status": "price_sent",
        },
        "stocks": [],
    }


def live_shape_item():
    row = item()
    cover = "https://example.test/cover.jpg"
    row["primary_image"] = [cover]
    row["color_image"] = [cover]
    row["images"] = [f"https://example.test/{index}.jpg" for index in range(1, 7)]
    row["stocks"] = {"has_stock": False, "stocks": []}
    return row


def listing_digest():
    row = item()
    return target_scoped._canonical_digest(
        {
            "provider": "ozon",
            "product_id": str(row["id"]),
            "seller_sku": row["offer_id"],
            "title": row["name"],
            "price": row["price"],
            "image_count": len(row["images"]),
            "category_id": str(row["description_category_id"]),
        }
    )


def test_historical_product_and_sku_are_not_runtime_defaults():
    source = Path(target_scoped.__file__).read_text(encoding="utf-8")
    assert "5687436857" not in source
    assert '"0954"' not in source


def test_live_info_shape_deduplicates_cover_and_accepts_nested_empty_stock(monkeypatch):
    monkeypatch.setattr(
        target_scoped,
        "ozon_post",
        lambda *_args: {"items": [live_shape_item()]},
    )

    result = target_scoped.read_existing_product(
        offer_id="0988",
        expected_images=[f"expected-{index}" for index in range(7)],
    )

    assert result["checks"]["images"] is True
    assert result["checks"]["stock_false"] is True
    assert result["listing_digest"] == target_scoped._canonical_digest(
        {
            "provider": "ozon",
            "product_id": "7654321098",
            "seller_sku": "0988",
            "title": "Approved listing",
            "price": "56.00",
            "image_count": 7,
            "category_id": "17027906",
        }
    )


@pytest.mark.parametrize(
    "stocks",
    [
        {"has_stock": True, "stocks": []},
        {"has_stock": False, "stocks": [{"present": 0}]},
        {},
        None,
    ],
)
def test_unknown_or_contradictory_stock_shape_fails_closed(monkeypatch, stocks):
    row = item()
    row["stocks"] = stocks
    monkeypatch.setattr(target_scoped, "ozon_post", lambda *_args: {"items": [row]})

    assert target_scoped.read_existing_product(offer_id="0988")["checks"]["stock_false"] is False


@pytest.mark.parametrize(
    "field",
    [
        "product_id",
        "seller_sku",
        "offer_id",
        "warehouse_id",
        "desired_stock_quantity",
        "expected_listing_digest",
    ],
)
def test_missing_exact_binding_refuses_before_any_provider_call(monkeypatch, field):
    value = manifest(expected_listing_digest=listing_digest())
    value.pop(field)
    calls = []
    monkeypatch.setattr(target_scoped, "ozon_post", lambda *args: calls.append(args))

    with pytest.raises(RuntimeError, match="manifest"):
        target_scoped.stock_existing_product(continuation_manifest=value)

    assert calls == []


def test_manifest_binds_one_stock_write_and_exact_readback(monkeypatch):
    calls = []

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path == "/v3/product/info/list":
            return {"items": [item()]}
        if path == "/v2/warehouse/list":
            return {
                "warehouses": [
                    {
                        "warehouse_id": 1020005018928780,
                        "status": "created",
                        "is_kgt": False,
                    }
                ]
            }
        if path == "/v2/products/stocks":
            return {"result": [{"offer_id": "0988", "errors": []}]}
        if path == "/v2/product/info/stocks-by-warehouse/fbs":
            return {
                "products": [
                    {
                        "offer_id": "0988",
                        "stocks": [
                            {
                                "warehouse_id": 1020005018928780,
                                "present": 37,
                            }
                        ],
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(target_scoped, "ozon_post", post)
    result = target_scoped.stock_existing_product(
        continuation_manifest=manifest(expected_listing_digest=listing_digest())
    )

    assert result["verified"] is True
    assert result["product_id"] == "7654321098"
    assert result["external_writes_performed"] == ["ozon:stock:update"]
    assert [path for path, _body in calls].count("/v2/products/stocks") == 1
    assert calls[-1] == (
        "/v2/product/info/stocks-by-warehouse/fbs",
        {"offer_id": ["0988"], "limit": 1000},
    )
    assert calls[2][1] == {
        "stocks": [
            {
                "offer_id": "0988",
                "stock": 37,
                "warehouse_id": 1020005018928780,
            }
        ]
    }


def test_unknown_fbs_response_shape_fails_closed_after_one_write(monkeypatch):
    def post(path, _body):
        if path == "/v3/product/info/list":
            return {"items": [item()]}
        if path == "/v2/warehouse/list":
            return {"warehouses": [{"warehouse_id": 1020005018928780, "status": "created", "is_kgt": False}]}
        if path == "/v2/products/stocks":
            return {"result": [{"offer_id": "0988", "errors": []}]}
        if path == "/v2/product/info/stocks-by-warehouse/fbs":
            return {"cursor": "", "has_next": False}
        raise AssertionError(path)

    monkeypatch.setattr(target_scoped, "ozon_post", post)
    result = target_scoped.stock_existing_product(
        continuation_manifest=manifest(expected_listing_digest=listing_digest())
    )

    assert result["verified"] is False


@pytest.mark.parametrize(
    ("warehouse_id", "present"),
    [
        (1020005018928780, 2),
        (1020005018928781, 37),
    ],
)
def test_has_stock_cannot_replace_exact_fbs_warehouse_and_quantity(
    monkeypatch, warehouse_id, present
):
    calls = []

    def post(path, _body):
        calls.append(path)
        if path == "/v3/product/info/list":
            return {"items": [item()]}
        if path == "/v2/warehouse/list":
            return {"warehouses": [{"warehouse_id": 1020005018928780, "status": "created", "is_kgt": False}]}
        if path == "/v2/products/stocks":
            return {"result": [{"offer_id": "0988", "errors": []}]}
        if path == "/v2/product/info/stocks-by-warehouse/fbs":
            return {
                "products": [
                    {
                        "offer_id": "0988",
                        "has_stock": True,
                        "stocks": [{"warehouse_id": warehouse_id, "present": present}],
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(target_scoped, "ozon_post", post)
    result = target_scoped.stock_existing_product(
        continuation_manifest=manifest(expected_listing_digest=listing_digest())
    )

    assert result["verified"] is False
    assert result["external_writes_performed"] == ["ozon:stock:update"]
    assert calls.count("/v2/products/stocks") == 1
