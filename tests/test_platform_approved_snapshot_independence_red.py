from __future__ import annotations

import hashlib
import json

from modules.products import server as product_server


def _approved_plan() -> dict:
    targets = ["shopee:PH", "ozon:RU"]
    payload = {
        "plan_id": "approved-plan-independent-platforms",
        "product_id": "3846511157",
        "product_revision": 71,
        "targets": targets,
    }
    payload_digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    confirmation_token = f"PUBLISH-{payload_digest[:16].upper()}"
    return {
        "plan_id": payload["plan_id"],
        "product_id": payload["product_id"],
        "seller_sku": "0959",
        "targets": targets,
        "payload": payload,
        "payload_digest": payload_digest,
        "confirmation_token": confirmation_token,
        "status": "APPROVED",
        "approval": {
            "status": "APPROVED",
            "approved_by": "Kyle",
            "user_approved": True,
            "plan_id": payload["plan_id"],
            "payload_digest": payload_digest,
            "confirmation_token": confirmation_token,
        },
        "sku_reservation": {
            "status": "ACTIVE",
            "plan_id": payload["plan_id"],
            "seller_sku": "0959",
        },
    }


def test_platform_buttons_execute_from_approved_snapshot_without_current_dashboard(
    monkeypatch,
):
    """Shopee and Ozon must not inherit the old one-click authoring gate."""

    plan = _approved_plan()

    class FakeStore:
        def get_plan(self, plan_id):
            return plan if plan_id == plan["plan_id"] else None

    monkeypatch.setattr(
        "shared_platform.release_store.default_release_store",
        lambda: FakeStore(),
    )
    monkeypatch.setattr(
        product_server,
        "_release_dashboard_for_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("independent platform publish read current dashboard")
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_approved_shopee_global_publish_facts",
        lambda payload: {"payload": payload},
    )
    monkeypatch.setattr(
        "modules.shopee.approved_global_publisher.publish_approved_global",
        lambda facts: {"ok": True, "facts": facts},
    )
    monkeypatch.setattr(
        product_server,
        "_approved_ozon_publish_facts",
        lambda payload: {
            "seller_sku": "0959",
            "title": "Approved title",
            "size": (38.0, 85.0),
            "price": 62,
            "old_price": 81,
            "images": ["https://images.example/1.jpg"],
            "package_cm": [38.0, 85.0, 0.1],
            "weight_kg": 0.2,
            "quantity": 1,
            "source_category": "wall sticker",
        },
    )
    monkeypatch.setattr(
        "modules.ozon.migrate_batch.migrate_one",
        lambda *_args, **_kwargs: {"ok": True},
    )

    identity = product_server._server_canonical_digest(plan["targets"])
    request = {
        "offer_id": plan["product_id"],
        "product_revision": plan["payload"]["product_revision"],
        "payload_digest": plan["payload_digest"],
        "targets_digest": identity,
        "publication_targets": list(plan["targets"]),
        "plan_id": plan["plan_id"],
        "confirmation_token": plan["confirmation_token"],
        "confirm_publish": True,
    }

    shopee_status, shopee_result = product_server._start_shopee_global_release(
        request
    )
    ozon_status, ozon_result = product_server._start_ozon_release(request)

    assert (shopee_status, shopee_result["success"]) == (200, True)
    assert (ozon_status, ozon_result["success"]) == (200, True)
