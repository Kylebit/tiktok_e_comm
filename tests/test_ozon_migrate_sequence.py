from modules.ozon import catalog_draft, migrate_batch


def _draft():
    return {
        "offer_id": "0953",
        "images": ["https://tiktok.example/approved.jpg"],
        "draft_title": "Approved Russian title",
        "draft_description": "Approved Russian description",
        "price": "49",
        "old_price": "65",
        "depth": 30,
        "width": 580,
        "height": 340,
        "weight": 20,
        "category_id": 1,
        "type_id": 2,
    }


def test_migrate_one_preserves_ambiguous_import_dispatch_evidence(monkeypatch):
    monkeypatch.setattr(catalog_draft, "build_draft", lambda *_a, **_k: _draft())
    monkeypatch.setattr(
        migrate_batch,
        "proxy_json",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("response lost after dispatch")
        ),
    )

    result = migrate_batch.migrate_one(
        "0953",
        allow_deepseek=False,
        process_images=False,
    )

    assert result["ok"] is False
    assert result["step"] == "migrate"
    assert result["import_request_attempted"] is True
    assert result["import_dispatch_outcome"] == "unknown_after_dispatch"


def test_migrate_one_returns_stable_import_task_identity(monkeypatch):
    monkeypatch.setattr(catalog_draft, "build_draft", lambda *_a, **_k: _draft())
    monkeypatch.setattr(
        migrate_batch,
        "proxy_json",
        lambda *_a, **_k: (
            200,
            {
                "status": "imported",
                "task_id": "task-0953",
                "errors": [],
                "rich_status": "skipped_by_audited_release",
            },
        ),
    )

    result = migrate_batch.migrate_one(
        "0953",
        allow_deepseek=False,
        process_images=False,
    )

    assert result["ok"] is True
    assert result["task_id"] == "task-0953"
    assert result["import_request_attempted"] is True
    assert result["import_dispatch_outcome"] == "accepted"
