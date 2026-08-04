"""Red regression for preserving safe Ozon API rejection reasons."""

from __future__ import annotations

import sys
import types

from modules.products import server as product_server


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
        "_oneclick_approved_context",
        lambda _data: ({"payload": {"approved": True}}, None),
    )
    monkeypatch.setattr(
        product_server, "_approved_ozon_publish_facts", lambda _payload: approved
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
        "_oneclick_approved_context",
        lambda _data: ({"payload": {"approved": True}}, None),
    )
    monkeypatch.setattr(
        product_server, "_approved_ozon_publish_facts", lambda _payload: approved
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
