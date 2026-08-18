"""Regression contract for Ozon's independent approved-snapshot path."""

from __future__ import annotations

from modules.ozon import catalog_draft, migrate_batch
from modules.ozon.category_match import match_category


def _approved_table_runner_snapshot(*, seller_sku: str, variant_label: str) -> dict:
    return {
        "seller_sku": seller_sku,
        "variant_key": f";星月金线边（只桌旗）;{variant_label};",
        "variant_label": variant_label,
        "title": (
            "Белая ажурная дорожка на стол с краем в виде звёзд и луны, "
            "цветочный узор, 35x140 см"
        ),
        "description": "Approved factual table-runner description.",
        "package_cm": [20.0, 20.0, 3.0],
        "weight_kg": 0.1,
        "quantity": 1,
        "price_cny": 77,
        "old_price_cny": 100,
        "images": ["https://image.example/table-runner.jpg"],
        "source_category": {"id": "", "name": "居家布艺 > 桌旗"},
    }


def test_approved_table_runner_title_resolves_to_tablecloth_not_sticker():
    """The approved English listing copy is authoritative product evidence."""

    match = match_category(
        title="White Lace Table Runner with Star and Moon Edge",
        tk_path="Home decorations > Festive decorations",
        tk_leaf="Festive decorations",
        tk_category_id="600009",
    )

    assert match["match_method"] == "title_tablecloth"
    assert match["category_id"] == 17028730
    assert match["type_id"] == 92692
    assert match["migrate_profile"] == "tablecloth"


def test_approved_russian_table_runner_and_chinese_source_path_resolve_tablecloth():
    """The actual approved Offer 3838599504 copy is localized before Ozon."""

    match = match_category(
        title=(
            "Белая ажурная дорожка на стол с краем в виде звёзд и луны, "
            "цветочный узор, 35x140 см"
        ),
        tk_path="居家布艺 > 桌旗",
        tk_leaf="居家布艺 > 桌旗",
        tk_category_id="",
    )

    assert match["match_method"] == "title_tablecloth"
    assert match["type_id"] == 92692
    assert match["migrate_profile"] == "tablecloth"


def test_tablecloth_variant_title_uses_product_size_not_shipping_package(monkeypatch):
    """Offer 3838599504 must never reuse the sticker template or 20x20 parcel size."""

    monkeypatch.setattr(
        catalog_draft,
        "_lookup_material",
        lambda _name, _category_id, _type_id: (1, "Полиэстер"),
    )
    monkeypatch.setattr(
        catalog_draft,
        "_lookup_color",
        lambda _name, _category_id, _type_id: (2, "Белый"),
    )
    monkeypatch.setattr(
        catalog_draft,
        "lookup_category_names",
        lambda _category_id, _type_id: {
            "category_name_zh": "家纺",
            "type_name_zh": "桌布、桌旗",
        },
    )

    expected = {
        "0963": ("35*140", "35", "140"),
        "0964": ("35*200", "35", "200"),
        "0965": ("35*300", "35", "300"),
    }
    forbidden = ("самокле", "стен", "плёнк", "пвх", "20х20")
    for seller_sku, (label, expected_len, expected_wid) in expected.items():
        draft = catalog_draft.build_draft_from_approved_snapshot(
            _approved_table_runner_snapshot(
                seller_sku=seller_sku,
                variant_label=label,
            ),
            seller_sku=seller_sku,
        )

        assert draft["migrate_profile"] == "tablecloth"
        assert draft["category_id"] == 17028730
        assert draft["type_id"] == 92692
        assert draft["variant_label"] == label
        assert draft["len_cm"] == expected_len
        assert draft["wid_cm"] == expected_wid
        assert f"{expected_len}х{expected_wid}" in draft["draft_title"]
        assert not any(word in draft["draft_title"].lower() for word in forbidden)
        assert not any(word in draft["draft_description"].lower() for word in forbidden)
        # Shipping dimensions remain the approved parcel and are not product size.
        assert (draft["depth"], draft["width"], draft["height"]) == (
            "200",
            "200",
            "30",
        )


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
    assert result["title"] == "Approved PVC wall sticker"
