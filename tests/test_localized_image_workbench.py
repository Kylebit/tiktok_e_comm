from __future__ import annotations

import json
from pathlib import Path
from io import BytesIO

import pytest
from PIL import Image

from modules.sourcing import new_product_workbench as workbench


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
            {"target_label": "miaoshou:COMMON"},
            {"target_label": "tiktok:LH_TH"},
            {"target_label": "shopee:VN"},
            {"target_label": "ozon:RU"},
        ],
    }


class _ReleaseStore:
    def __init__(self, *, approved: bool = True):
        self.approved = approved
        self.reads: list[tuple] = []

    def active_plan_for_product(self, offer_id: str):
        self.reads.append(("active_plan_for_product", offer_id))
        if not self.approved:
            return None
        return {
            "plan_id": "omnichannel:approved-wallpaper",
            "product_id": offer_id,
            "status": "APPROVED",
        }

    def approved_publication_snapshot(self, *, offer_id: str, plan_id: str):
        self.reads.append(("approved_publication_snapshot", offer_id, plan_id))
        return _snapshot()


def test_initializes_from_release_store_without_mutating_workbench_state(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "new_product_workbench"
    state_dir.mkdir()
    state_path = state_dir / "3900088343.json"
    state_path.write_text('{"offer_id":"3900088343","_revision":17}', encoding="utf-8")
    original = state_path.read_bytes()
    monkeypatch.setattr(workbench, "STATE_DIR", state_dir)
    monkeypatch.setattr(
        workbench, "LOCALIZED_IMAGE_PACKS_DIR", tmp_path / "localized_image_packs"
    )
    monkeypatch.setattr(
        workbench, "resolve_offer_key", lambda value: str(value).strip()
    )
    release_store = _ReleaseStore()

    result = workbench.initialize_localized_image_project(
        "3900088343", release_store=release_store
    )

    assert result["initialized"] is True
    assert result["project"]["external_writes"] == 0
    assert result["project"]["packs"]["th-TH"]["status"] == "PENDING_TEXT_REVIEW"
    assert state_path.read_bytes() == original
    assert release_store.reads == [
        ("active_plan_for_product", "3900088343"),
        (
            "approved_publication_snapshot",
            "3900088343",
            "omnichannel:approved-wallpaper",
        ),
    ]


def test_requires_an_active_approved_v4_release_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(
        workbench, "LOCALIZED_IMAGE_PACKS_DIR", tmp_path / "localized_image_packs"
    )
    monkeypatch.setattr(
        workbench, "resolve_offer_key", lambda value: str(value).strip()
    )

    with pytest.raises(ValueError, match="approved ReleasePlan"):
        workbench.initialize_localized_image_project(
            "3900088343", release_store=_ReleaseStore(approved=False)
        )


def test_server_registers_read_and_initialize_endpoints():
    source = Path("modules/sourcing/new_product_server.py").read_text(
        encoding="utf-8"
    )
    proxy = Path("modules/products/server.py").read_text(encoding="utf-8")

    assert '"/api/new-product/content-package/localized-images"' in source
    assert '"/api/new-product/content-package/localized-images/initialize"' in source
    assert '"content-package/localized-images"' in proxy
    assert '"content-package/localized-images/initialize"' in proxy


class _FakeOcr:
    def __call__(self, _image):
        return (
            [[[[10, 10], [190, 10], [190, 40], [10, 40]], "Easy to install", 0.99]],
            None,
        )


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (200, 100), "white").save(output, format="PNG")
    return output.getvalue()


def test_scans_saves_and_renders_localized_image_without_product_center_mutation(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "new_product_workbench"
    state_dir.mkdir()
    state_path = state_dir / "3900088343.json"
    state_path.write_text('{"offer_id":"3900088343","_revision":17}', encoding="utf-8")
    original = state_path.read_bytes()
    monkeypatch.setattr(workbench, "STATE_DIR", state_dir)
    monkeypatch.setattr(
        workbench, "LOCALIZED_IMAGE_PACKS_DIR", tmp_path / "localized_image_packs"
    )
    monkeypatch.setattr(workbench, "resolve_offer_key", lambda value: str(value).strip())
    initialized = workbench.initialize_localized_image_project(
        "3900088343", release_store=_ReleaseStore()
    )
    source_url = initialized["project"]["base_package"]["ordered_image_urls"][0]

    scanned = workbench.scan_localized_image_text(
        "3900088343",
        expected_revision=1,
        source_url=source_url,
        source_bytes=_image_bytes(),
        ocr_engine=_FakeOcr(),
    )
    region_id = scanned["project"]["packs"]["th-TH"]["images"][0][
        "translations"
    ][0]["region_id"]
    translated = workbench.save_localized_translation_draft(
        "3900088343",
        expected_revision=scanned["project"]["revision"],
        locale="th-TH",
        source_url=source_url,
        translations=[{"region_id": region_id, "translated_text": "Easy setup"}],
    )
    rendered = workbench.create_localized_translation_preview(
        "3900088343",
        expected_revision=translated["project"]["revision"],
        locale="th-TH",
        source_url=source_url,
        source_bytes=_image_bytes(),
    )

    preview = rendered["project"]["packs"]["th-TH"]["images"][0]["preview"]
    assert preview["status"] == "PREVIEW_READY"
    assert preview["local_url"].startswith(
        "/api/product-flow/content-package/localized-images/artifact?"
    )
    assert workbench.localized_image_preview_artifact(
        "3900088343", preview["artifact_id"]
    ).is_file()
    assert state_path.read_bytes() == original
    assert rendered["external_writes"] == 0
    assert rendered["product_center_mutated"] is False


def test_server_registers_scan_translation_preview_and_artifact_endpoints():
    source = Path("modules/sourcing/new_product_server.py").read_text(
        encoding="utf-8"
    )
    proxy = Path("modules/products/server.py").read_text(encoding="utf-8")

    for endpoint in (
        "content-package/localized-images/scan-text",
        "content-package/localized-images/auto-translate",
        "content-package/localized-images/translation-draft",
        "content-package/localized-images/preview",
        "content-package/localized-images/artifact",
    ):
        assert endpoint in source
        assert endpoint in proxy


def test_automatic_translation_creates_five_locale_previews_without_product_center_mutation(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "new_product_workbench"
    state_dir.mkdir()
    state_path = state_dir / "3900088343.json"
    state_path.write_text('{"offer_id":"3900088343","_revision":17}', encoding="utf-8")
    original = state_path.read_bytes()
    monkeypatch.setattr(workbench, "STATE_DIR", state_dir)
    monkeypatch.setattr(
        workbench, "LOCALIZED_IMAGE_PACKS_DIR", tmp_path / "localized_image_packs"
    )
    monkeypatch.setattr(workbench, "resolve_offer_key", lambda value: str(value).strip())
    initialized = workbench.initialize_localized_image_project(
        "3900088343", release_store=_ReleaseStore()
    )
    urls = initialized["project"]["base_package"]["ordered_image_urls"]
    current = initialized
    for index, source_url in enumerate(urls):
        current = workbench.scan_localized_image_text(
            "3900088343",
            expected_revision=current["project"]["revision"],
            source_url=source_url,
            source_bytes=_image_bytes(),
            ocr_engine=_FakeOcr() if index == 0 else (lambda _image: ([], None)),
        )
    region = current["project"]["text_inventory"]["images"][urls[0]]["regions"][0]
    translated = {
        "ms-MY": "Mudah dipasang",
        "th-TH": "ติดตั้งง่าย",
        "vi-VN": "Dễ lắp đặt",
        "ru-RU": "Простая установка",
        "es-MX": "Fácil de instalar",
    }
    calls = []

    def model_call(_messages, **_kwargs):
        calls.append(True)
        return json.dumps(
            {
                "schema_version": "localized-image-auto-translation/v1",
                "translations": {
                    locale: [
                        {
                            "region_id": region["region_id"],
                            "source_text": region["source_text"],
                            "translated_text": text,
                        }
                    ]
                    for locale, text in translated.items()
                },
            },
            ensure_ascii=False,
        )

    result = workbench.auto_translate_localized_images(
        "3900088343",
        expected_revision=current["project"]["revision"],
        source_bytes_by_url={url: _image_bytes() for url in urls},
        model_call=model_call,
    )

    assert len(calls) == 1
    assert result["project"]["automatic_translation"]["status"] == "AUTO_PREVIEW_READY"
    assert all(
        result["project"]["packs"][locale]["status"] == "AUTO_PREVIEW_READY"
        for locale in translated
    )
    assert result["project"]["packs"]["th-TH"]["images"][0]["preview"][
        "local_url"
    ].startswith("/api/product-flow/content-package/localized-images/artifact?")
    assert state_path.read_bytes() == original
    assert result["external_writes"] == 0
    assert result["product_center_mutated"] is False

    repeated = workbench.auto_translate_localized_images(
        "3900088343",
        expected_revision=result["project"]["revision"],
        source_bytes_by_url={},
        model_call=lambda *_args, **_kwargs: pytest.fail("idempotent call must not use model"),
    )
    assert repeated["project"]["revision"] == result["project"]["revision"]

    project_path = (
        tmp_path / "localized_image_packs" / "3900088343" / "project.json"
    )
    stale_renderer = json.loads(project_path.read_text(encoding="utf-8"))
    stale_renderer["automatic_translation"]["renderer"] = "pillow-local-preview/v1"
    project_path.write_text(
        json.dumps(stale_renderer, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rerendered = workbench.auto_translate_localized_images(
        "3900088343",
        expected_revision=result["project"]["revision"],
        source_bytes_by_url={url: _image_bytes() for url in urls},
        model_call=lambda *_args, **_kwargs: pytest.fail(
            "renderer upgrade must reuse saved translations"
        ),
    )
    assert rerendered["project"]["revision"] == result["project"]["revision"] + 1
    assert rerendered["project"]["automatic_translation"]["renderer"] == (
        "pillow-local-preview/v2"
    )
