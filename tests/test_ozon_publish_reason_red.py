"""Red regression for preserving safe Ozon API rejection reasons."""

from __future__ import annotations

import sys
import types

from modules.products import server as product_server


def test_ozon_existing_product_requires_exact_approved_fields(monkeypatch):
    """A non-empty stale title must not count as an exact existing product."""

    from modules.ozon import target_scoped

    monkeypatch.setattr(
        target_scoped,
        "ozon_post",
        lambda *_args, **_kwargs: {
            "items": [
                {
                    "id": 5875531446,
                    "offer_id": "0966",
                    "name": "Legacy title built from parcel dimensions",
                    "price": "43",
                    "images": ["https://images.example/1.jpg"],
                    "statuses": {"is_created": True, "status": "PRICE_SENT"},
                }
            ]
        },
    )

    result = target_scoped.read_existing_product(
        offer_id="0966",
        expected_title="Approved product title",
        expected_price=43,
        expected_images=["https://images.example/1.jpg"],
    )

    assert result["checks"]["title"] is False
    assert result["checks"]["price"] is True
    assert result["checks"]["images"] is True


def test_ozon_publish_exposes_safe_provider_rejection_reason(monkeypatch):
    """The former handler collapses the provider fact into a generic error."""

    approved = {
        "seller_sku": "0959",
        "title": "Approved title",
        "size": (38.0, 85.0, 1.0),
        "package_cm": [38.0, 85.0, 1.0],
        "weight_kg": 0.3,
        "quantity": 1,
        "source_category": {"id": "123", "name": "Wall stickers"},
        "price": 900.0,
        "old_price": 1100.0,
        "images": ["https://image.example/1.jpg"],
    }
    monkeypatch.setattr(
        product_server,
        "_platform_approved_context",
        lambda _data: ({"payload": {"approved": True}}, None),
    )
    monkeypatch.setattr(
        product_server, "_approved_ozon_publish_facts", lambda _payload: approved
    )
    monkeypatch.setattr(
        "modules.ozon.target_scoped.read_existing_product",
        lambda **_kwargs: {"checks": {}},
    )
    fake_module = types.ModuleType("modules.ozon.migrate_batch")
    fake_module.migrate_one = lambda *_args, **_kwargs: {
        "ok": False,
        "step": "migrate",
        "import_request_attempted": True,
        "import_dispatch_outcome": "rejected_or_unknown",
        "errors": ["category profile is not accepted"],
    }
    monkeypatch.setitem(sys.modules, "modules.ozon.migrate_batch", fake_module)

    status, body = product_server._start_ozon_release(
        {"confirm_publish": True, "offer_id": "3846511157"}
    )

    assert status == 409
    assert body["success"] is False
    assert "category profile is not accepted" in body["message"]


def test_ozon_publish_exposes_safe_nested_draft_reason(monkeypatch):
    """A rejected draft must not be reduced to its generic ``step`` label."""

    approved = {
        "seller_sku": "0959",
        "title": "Approved title",
        "size": (38.0, 85.0, 1.0),
        "package_cm": [38.0, 85.0, 1.0],
        "weight_kg": 0.3,
        "quantity": 1,
        "source_category": {"id": "123", "name": "Wall stickers"},
        "price": 900.0,
        "old_price": 1100.0,
        "images": ["https://image.example/1.jpg"],
    }
    monkeypatch.setattr(
        product_server,
        "_platform_approved_context",
        lambda _data: ({"payload": {"approved": True}}, None),
    )
    monkeypatch.setattr(
        product_server, "_approved_ozon_publish_facts", lambda _payload: approved
    )
    monkeypatch.setattr(
        "modules.ozon.target_scoped.read_existing_product",
        lambda **_kwargs: {"checks": {}},
    )
    fake_module = types.ModuleType("modules.ozon.migrate_batch")
    fake_module.migrate_one = lambda *_args, **_kwargs: {
        "ok": False,
        "step": "draft",
        "error": {"error": "category mapping is unavailable"},
    }
    monkeypatch.setitem(sys.modules, "modules.ozon.migrate_batch", fake_module)

    status, body = product_server._start_ozon_release(
        {"confirm_publish": True, "offer_id": "3846511157"}
    )

    assert status == 409
    assert "category mapping is unavailable" in body["message"]


def test_ozon_existing_approved_offer_is_success_without_duplicate_import(monkeypatch):
    """Official readback is the truth when Ozon already has the approved SKU."""

    context = {"payload": {}, "plan": {}, "store": object(), "dashboard": None}
    facts = {
        "seller_sku": "0959",
        "title": "Approved title",
        "size": (38.0, 85.0),
        "price": 62,
        "old_price": 81,
        "images": ["https://images.example/1.jpg"],
        "package_cm": [38.0, 85.0, 0.1],
        "weight_kg": 0.2,
        "quantity": 1,
        "source_category": {"id": "wall-decor", "name": "Wall decor"},
    }
    monkeypatch.setattr(
        product_server,
        "_platform_approved_context",
        lambda _data: (context, None),
    )
    monkeypatch.setattr(
        product_server,
        "_approved_ozon_publish_facts",
        lambda _payload: facts,
    )
    monkeypatch.setattr(
        "modules.ozon.target_scoped.read_existing_product",
        lambda **_kwargs: {
            "product_id": "5802827890",
            "checks": {
                "created": True,
                "approved": True,
                "title": True,
                "price": True,
                "images": True,
            },
        },
    )
    fake_module = types.ModuleType("modules.ozon.migrate_batch")
    fake_module.migrate_one = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("existing approved Ozon offer must not be imported again")
    )
    monkeypatch.setitem(sys.modules, "modules.ozon.migrate_batch", fake_module)

    status, body = product_server._start_ozon_release(
        {
            "confirm_publish": True,
            "offer_id": "3846511157",
            "plan_id": "approved-plan",
            "confirmation_token": "token",
        }
    )

    assert status == 200
    assert body["success"] is True
    assert body["successful_target_count"] == 1
