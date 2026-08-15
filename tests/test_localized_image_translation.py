from __future__ import annotations

from io import BytesIO

from PIL import Image
import pytest

from modules.sourcing.localized_image_ocr import detect_english_text_regions
from modules.sourcing.localized_image_render import render_translation_preview
from modules.sourcing.localized_image_packs import (
    LocalizedImagePackError,
    LocalizedImagePackStore,
)


def _snapshot() -> dict:
    return {
        "schema_version": "approved-publication-snapshot/v4",
        "offer_id": "3900088343",
        "plan_id": "omnichannel:approved-wallpaper",
        "snapshot_digest": f"sha256:{'a' * 64}",
        "product": {
            "images": [
                "https://assets.example/master-01.png",
                "https://assets.example/master-02.png",
            ]
        },
        "publication_targets": [
            {"target_label": "tiktok:LH_TH"},
            {"target_label": "tiktok:LH_VN"},
            {"target_label": "ozon:RU"},
        ],
    }


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (200, 100), "white").save(output, format="PNG")
    return output.getvalue()


class _FakeOcr:
    def __call__(self, _image):
        return (
            [
                [[[20, 10], [180, 10], [180, 40], [20, 40]], "Easy to install", 0.98],
                [[[20, 50], [80, 50], [80, 75], [20, 75]], "12345", 0.99],
                [[[100, 50], [180, 50], [180, 75], [100, 75]], "low", 0.2],
            ],
            None,
        )


def test_local_ocr_normalizes_only_confident_english_text_regions():
    regions = detect_english_text_regions(_image_bytes(), engine=_FakeOcr())

    assert len(regions) == 1
    assert regions[0]["source_text"] == "Easy to install"
    assert regions[0]["bbox"] == [0.1, 0.1, 0.9, 0.4]
    assert regions[0]["confidence"] == 0.98
    assert regions[0]["origin"] == "rapidocr-local/v1"
    assert regions[0]["region_id"].startswith("text-")


def test_ocr_inventory_seeds_each_locale_without_changing_english_master(tmp_path):
    store = LocalizedImagePackStore(tmp_path)
    project = store.initialize_from_approved_snapshot(_snapshot())
    source_url = project["base_package"]["ordered_image_urls"][0]
    region = detect_english_text_regions(_image_bytes(), engine=_FakeOcr())[0]

    updated = store.save_text_inventory(
        "3900088343",
        expected_revision=1,
        source_url=source_url,
        source_url_digest=project["packs"]["en-master"]["images"][0][
            "source_url_digest"
        ],
        provider="rapidocr-local/v1",
        regions=[region],
    )

    assert updated["revision"] == 2
    assert updated["text_inventory"]["images"][source_url]["status"] == "SCANNED"
    assert "translations" not in updated["packs"]["en-master"]["images"][0]
    thai = updated["packs"]["th-TH"]["images"][0]
    assert thai["translations"] == [
        {
            "region_id": region["region_id"],
            "source_text": "Easy to install",
            "translated_text": "",
            "status": "PENDING_TRANSLATION",
        }
    ]
    assert updated["external_writes"] == 0


def test_translation_draft_is_revisioned_and_cannot_change_region_identity(tmp_path):
    store = LocalizedImagePackStore(tmp_path)
    project = store.initialize_from_approved_snapshot(_snapshot())
    source_url = project["base_package"]["ordered_image_urls"][0]
    region = detect_english_text_regions(_image_bytes(), engine=_FakeOcr())[0]
    scanned = store.save_text_inventory(
        "3900088343",
        expected_revision=1,
        source_url=source_url,
        source_url_digest=project["packs"]["en-master"]["images"][0][
            "source_url_digest"
        ],
        provider="rapidocr-local/v1",
        regions=[region],
    )

    saved = store.save_translation_draft(
        "3900088343",
        expected_revision=scanned["revision"],
        locale="th-TH",
        source_url=source_url,
        translations=[
            {"region_id": region["region_id"], "translated_text": "ติดตั้งง่าย"}
        ],
    )

    row = saved["packs"]["th-TH"]["images"][0]["translations"][0]
    assert saved["revision"] == 3
    assert row["source_text"] == "Easy to install"
    assert row["translated_text"] == "ติดตั้งง่าย"
    assert row["status"] == "DRAFT_TRANSLATED"
    assert saved["packs"]["th-TH"]["status"] == "DRAFT_TRANSLATED"

    with pytest.raises(LocalizedImagePackError, match="coverage"):
        store.save_translation_draft(
            "3900088343",
            expected_revision=saved["revision"],
            locale="th-TH",
            source_url=source_url,
            translations=[
                {"region_id": "text-not-approved", "translated_text": "ผิด"}
            ],
        )


def test_translation_draft_rejects_english_master_and_stale_revision(tmp_path):
    store = LocalizedImagePackStore(tmp_path)
    project = store.initialize_from_approved_snapshot(_snapshot())
    source_url = project["base_package"]["ordered_image_urls"][0]

    with pytest.raises(LocalizedImagePackError, match="localized pack"):
        store.save_translation_draft(
            "3900088343",
            expected_revision=1,
            locale="en-master",
            source_url=source_url,
            translations=[],
        )

    with pytest.raises(LocalizedImagePackError, match="revision"):
        store.save_text_inventory(
            "3900088343",
            expected_revision=99,
            source_url=source_url,
            source_url_digest=project["packs"]["en-master"]["images"][0][
                "source_url_digest"
            ],
            provider="rapidocr-local/v1",
            regions=[],
        )


def test_local_preview_renders_only_the_saved_translation_regions():
    regions = detect_english_text_regions(_image_bytes(), engine=_FakeOcr())
    rendered = render_translation_preview(
        _image_bytes(),
        regions=regions,
        translations=[
            {
                "region_id": regions[0]["region_id"],
                "translated_text": "Easy setup",
            }
        ],
        locale="th-TH",
    )

    with Image.open(BytesIO(rendered)) as output:
        assert output.size == (200, 100)
        assert output.format == "PNG"


def test_preview_artifact_is_bound_to_translation_revision(tmp_path):
    store = LocalizedImagePackStore(tmp_path)
    project = store.initialize_from_approved_snapshot(_snapshot())
    source_url = project["base_package"]["ordered_image_urls"][0]
    region = detect_english_text_regions(_image_bytes(), engine=_FakeOcr())[0]
    scanned = store.save_text_inventory(
        "3900088343",
        expected_revision=1,
        source_url=source_url,
        source_url_digest=project["packs"]["en-master"]["images"][0][
            "source_url_digest"
        ],
        provider="rapidocr-local/v1",
        regions=[region],
    )
    translated = store.save_translation_draft(
        "3900088343",
        expected_revision=scanned["revision"],
        locale="th-TH",
        source_url=source_url,
        translations=[
            {"region_id": region["region_id"], "translated_text": "Easy setup"}
        ],
    )
    preview = render_translation_preview(
        _image_bytes(),
        regions=[region],
        translations=[
            {"region_id": region["region_id"], "translated_text": "Easy setup"}
        ],
        locale="th-TH",
    )

    saved = store.save_preview_artifact(
        "3900088343",
        expected_revision=translated["revision"],
        locale="th-TH",
        source_url=source_url,
        artifact_bytes=preview,
        renderer="pillow-local-preview/v1",
    )

    metadata = saved["packs"]["th-TH"]["images"][0]["preview"]
    assert metadata["status"] == "PREVIEW_READY"
    assert store.preview_artifact_path("3900088343", metadata["artifact_id"]).is_file()
    assert saved["external_writes"] == 0


def test_automatic_bundle_commits_all_locales_once_and_is_bound_to_inventory(tmp_path):
    store = LocalizedImagePackStore(tmp_path)
    project = store.initialize_from_approved_snapshot(_snapshot())
    source_url = project["base_package"]["ordered_image_urls"][0]
    region = detect_english_text_regions(_image_bytes(), engine=_FakeOcr())[0]
    scanned = store.save_text_inventory(
        "3900088343",
        expected_revision=1,
        source_url=source_url,
        source_url_digest=project["packs"]["en-master"]["images"][0][
            "source_url_digest"
        ],
        provider="rapidocr-local/v1",
        regions=[region],
    )
    second_url = project["base_package"]["ordered_image_urls"][1]
    scanned = store.save_text_inventory(
        "3900088343",
        expected_revision=scanned["revision"],
        source_url=second_url,
        source_url_digest=project["packs"]["en-master"]["images"][1][
            "source_url_digest"
        ],
        provider="rapidocr-local/v1",
        regions=[],
    )
    translations = {
        "ms-MY": "Mudah dipasang",
        "th-TH": "ติดตั้งง่าย",
        "vi-VN": "Dễ lắp đặt",
        "ru-RU": "Простая установка",
        "es-MX": "Fácil de instalar",
    }
    items = []
    for url in scanned["base_package"]["ordered_image_urls"]:
        regions = (scanned.get("text_inventory") or {}).get("images", {}).get(url, {}).get("regions", [])
        if regions:
            locale_rows = {
                locale: [
                    {
                        "region_id": region["region_id"],
                        "source_text": region["source_text"],
                        "translated_text": translated,
                    }
                ]
                for locale, translated in translations.items()
            }
            previews = {
                locale: render_translation_preview(
                    _image_bytes(),
                    regions=[region],
                    translations=rows,
                    locale=locale,
                )
                for locale, rows in locale_rows.items()
            }
        else:
            locale_rows = {locale: [] for locale in translations}
            previews = {}
        items.append(
            {
                "source_url": url,
                "translations": locale_rows,
                "previews": previews,
                "receipt": {
                    "status": "AUTO_TRANSLATED" if regions else "NO_TEXT_REUSE_BASE",
                    "provider": "toapis-chat-completions/v1",
                    "model": "gpt-5.4-mini-official",
                    "model_calls": 1 if regions else 0,
                },
            }
        )

    saved = store.save_automatic_bundle(
        "3900088343",
        expected_revision=scanned["revision"],
        items=items,
    )

    assert saved["revision"] == scanned["revision"] + 1
    assert saved["automatic_translation"]["status"] == "AUTO_PREVIEW_READY"
    assert saved["automatic_translation"]["model_calls"] == 1
    assert saved["packs"]["th-TH"]["status"] == "AUTO_PREVIEW_READY"
    translated_image = saved["packs"]["th-TH"]["images"][0]
    assert translated_image["translations"][0]["translated_text"] == "ติดตั้งง่าย"
    assert store.preview_artifact_path(
        "3900088343", translated_image["preview"]["artifact_id"]
    ).is_file()
    assert saved["packs"]["th-TH"]["images"][1]["status"] == "REUSE_BASE_NO_TEXT"
    assert saved["external_writes"] == 0

    with pytest.raises(LocalizedImagePackError, match="revision"):
        store.save_automatic_bundle(
            "3900088343", expected_revision=scanned["revision"], items=items
        )
