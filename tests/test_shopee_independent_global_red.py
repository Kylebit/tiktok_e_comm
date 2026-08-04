"""Regression tests for the independent Shopee global button."""

from __future__ import annotations

import pytest

from modules.products import server as product_server
from modules.shopee import approved_global_publisher


def test_shopee_global_button_does_not_call_legacy_tiktok_alignment_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global-product button must not be coupled to a TikTok record."""

    facts = {
        "seller_sku": "0959", "region": "VN", "title": "Approved title",
        "description": "Approved English description", "global_original_price_cny": 10.0,
    }
    legacy_called = []
    monkeypatch.setattr(product_server, "_oneclick_approved_context", lambda _data: ({"payload": {}}, None))
    monkeypatch.setattr(product_server, "_approved_shopee_global_publish_facts", lambda _payload: facts)
    monkeypatch.setattr("modules.shopee.publish.publish_match_key", lambda *args, **kwargs: legacy_called.append((args, kwargs)) or {"ok": True})
    monkeypatch.setattr("modules.shopee.approved_global_publisher.publish_approved_global", lambda received: {"ok": received is facts})

    status, body = product_server._start_shopee_global_release({"confirm_publish": True, "offer_id": "3846511157"})

    assert status == 200
    assert body["success"] is True
    assert legacy_called == []


def test_approved_global_detail_preserves_approved_product_facts() -> None:
    detail = approved_global_publisher.approved_global_detail({
        "seller_sku": "0959", "title": "Approved title", "description": "Approved description",
        "images": ["https://example.test/1.jpg"], "package_cm": [30, 5, 5],
        "weight_kg": 0.12, "quantity": 300,
    })
    assert detail["skus"][0]["seller_sku"] == "0959"
    assert detail["skus"][0]["inventory"] == [{"quantity": 300}]
    assert detail["package_dimensions"] == {"length": 30.0, "width": 5.0, "height": 5.0}


def test_new_global_product_is_mapped_for_independent_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = {
        "seller_sku": "0959", "region": "PH", "title": "Approved title",
        "description": "Approved description", "images": ["https://example.test/1.jpg"],
        "package_cm": [30, 5, 5], "weight_kg": 0.12,
        "global_original_price_cny": 10.0,
    }
    monkeypatch.setattr("modules.shopee.auth.ensure_shop_token", lambda _shop_id: "token")
    monkeypatch.setattr("modules.shopee.shops.sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr("modules.shopee.global_sku_map.global_item_id_for_match_key", lambda _sku: None)
    monkeypatch.setattr("modules.shopee.publish._reference_item", lambda *_args: None)
    monkeypatch.setattr("modules.shopee.publish._upload_images", lambda _images: ["image-1"])
    monkeypatch.setattr("modules.shopee.publish._create_global_item", lambda *_args, **_kwargs: {"ok": True, "global_item_id": 123})
    saved = []
    monkeypatch.setattr("modules.shopee.global_sku_map.upsert_global_entry", lambda *args, **kwargs: saved.append((args, kwargs)))

    receipt = approved_global_publisher.publish_approved_global(facts)

    assert receipt["global_item_id"] == 123
    assert saved == [(('123',), {"match_key": "0959", "global_model_sku": "0959", "title": "Approved title", "published_regions": []})]


def test_existing_global_is_a_success_without_copy_readback_or_tiktok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = {
        "seller_sku": "0959", "region": "PH", "title": "Approved title",
        "description": "Approved description", "images": ["https://example.test/1.jpg"],
        "package_cm": [30, 5, 5], "weight_kg": 0.12,
        "global_original_price_cny": 10.0,
    }
    monkeypatch.setattr("modules.shopee.auth.ensure_shop_token", lambda _shop_id: "token")
    monkeypatch.setattr("modules.shopee.shops.sync_shop_ids", lambda: {"PH": 1})
    monkeypatch.setattr("modules.shopee.global_sku_map.global_item_id_for_match_key", lambda _sku: "123")
    monkeypatch.setattr("modules.shopee.publish._reference_item", lambda *_args: (_ for _ in ()).throw(AssertionError("reference read is unnecessary")))

    receipt = approved_global_publisher.publish_approved_global(facts)

    assert receipt == {"ok": True, "flow": "already_created", "global_item_id": 123, "model_sku": "0959"}
