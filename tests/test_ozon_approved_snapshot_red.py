"""Regression contract for Ozon's independent approved-snapshot path."""

from __future__ import annotations

from modules.ozon import catalog_draft, migrate_batch


def test_approved_snapshot_migration_never_loads_legacy_tiktok_catalog(monkeypatch):
    """An approved Ozon run cannot depend on a historical TikTok catalog row."""

    def legacy_catalog_must_not_run(*_args, **_kwargs):
        raise AssertionError("legacy TikTok catalog lookup must not run")

    monkeypatch.setattr(
        catalog_draft, "catalog_item_by_seller_sku", legacy_catalog_must_not_run
    )
    monkeypatch.setattr(
        migrate_batch,
        "proxy_json",
        lambda _method, path, *, payload: (
            (200, {"images": payload["images"], "errors": []})
            if path.startswith("process_images/")
            else (200, {"status": "pending", "task_id": "safe-task"})
        ),
    )

    result = migrate_batch.migrate_one(
        "0959",
        allow_deepseek=False,
        approved_snapshot={
            "seller_sku": "0959",
            "title": "Approved PVC wall sticker",
            "package_cm": [38.0, 85.0, 2.0],
            "weight_kg": 0.3,
            "quantity": 1,
            "price_cny": 900,
            "old_price_cny": 1100,
            "images": ["https://image.example/1.jpg"],
            "source_category": {"id": "123", "name": "Wall stickers"},
        },
        process_images=False,
        wait_for_import=False,
        skip_rich_content=True,
        skip_mapping_write=True,
    )

    assert result["ok"] is True
    assert result["import_dispatch_outcome"] == "accepted"
