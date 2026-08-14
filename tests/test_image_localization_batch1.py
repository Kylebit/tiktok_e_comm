from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from modules.sourcing import new_product_workbench
from modules.sourcing.image_localization import (
    ImageLocalizationStore,
    ImageLocalizationValidationError,
    image_localization_feature_flags,
)
from modules.sourcing.new_product_workbench import (
    image_localization_summary,
    initialize_image_localization,
    save_image_localization_regions,
)


def _png_bytes() -> bytes:
    image = Image.new("RGB", (100, 100), (240, 230, 210))
    for x in range(100):
        for y in range(75, 100):
            image.putpixel((x, y), (255, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _source() -> list[dict[str, str]]:
    return [{"url": "https://img.example/source-1.png", "kind": "main"}]


def test_feature_flags_keep_paid_ocr_disabled_by_default():
    flags = image_localization_feature_flags({})

    assert flags == {
        "manifest_enabled": True,
        "local_clean_master_enabled": True,
        "manual_region_editor_enabled": True,
        "ocr_provider_enabled": False,
    }


def test_manifest_preserves_source_identity_and_uses_revision_compare_and_swap(tmp_path: Path):
    store = ImageLocalizationStore(tmp_path)
    manifest = store.initialize("123", _source())
    original = manifest["assets"][0]

    assert manifest["schema_version"] == "image-localization-manifest/v1"
    assert manifest["revision"] == 1
    assert original["source_url"] == _source()[0]["url"]
    assert original["source_identity_digest"] == hashlib.sha256(
        _source()[0]["url"].encode("utf-8")
    ).hexdigest()

    updated = store.save_regions(
        "123",
        expected_revision=1,
        asset_id=original["asset_id"],
        regions=[{
            "region_id": "wm-1",
            "bbox": [0.05, 0.80, 0.45, 0.95],
            "text": "shop.example.1688.com",
            "classification": "watermark",
            "origin": "manual",
        }],
    )
    assert updated["revision"] == 2
    assert updated["assets"][0]["source_url"] == original["source_url"]

    with pytest.raises(ImageLocalizationValidationError, match="stale revision"):
        store.save_regions(
            "123",
            expected_revision=1,
            asset_id=original["asset_id"],
            regions=[],
        )


def test_clean_master_is_derived_without_overwriting_source(tmp_path: Path):
    store = ImageLocalizationStore(tmp_path / "store")
    manifest = store.initialize("123", _source())
    asset_id = manifest["assets"][0]["asset_id"]
    manifest = store.save_regions(
        "123",
        expected_revision=1,
        asset_id=asset_id,
        regions=[{
            "region_id": "wm-bottom",
            "bbox": [0.0, 0.75, 1.0, 1.0],
            "text": "shop.example.1688.com",
            "classification": "watermark",
            "origin": "manual",
        }],
    )
    source_path = tmp_path / "source.png"
    source_bytes = _png_bytes()
    source_path.write_bytes(source_bytes)

    result = store.create_clean_master(
        "123",
        expected_revision=2,
        asset_id=asset_id,
        source_path=source_path,
        method="local_region_fill/v1",
    )
    clean = result["assets"][0]["clean_master"]
    artifact_path = store.artifact_path("123", clean["artifact_id"])

    assert source_path.read_bytes() == source_bytes
    assert artifact_path.is_file()
    assert artifact_path != source_path
    assert clean["source_content_digest"] == hashlib.sha256(source_bytes).hexdigest()
    assert clean["artifact_digest"] == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert clean["removal_region_ids"] == ["wm-bottom"]


def test_clean_master_rejects_blanket_ai_removal_and_protected_overlap(tmp_path: Path):
    store = ImageLocalizationStore(tmp_path / "store")
    manifest = store.initialize("123", _source())
    asset_id = manifest["assets"][0]["asset_id"]
    manifest = store.save_regions(
        "123",
        expected_revision=1,
        asset_id=asset_id,
        regions=[
            {
                "region_id": "wm-1",
                "bbox": [0.1, 0.1, 0.5, 0.5],
                "text": "supplier",
                "classification": "watermark",
                "origin": "manual",
            },
            {
                "region_id": "protected-1",
                "bbox": [0.4, 0.4, 0.8, 0.8],
                "text": "Hunt Home",
                "classification": "protected_natural_text",
                "origin": "manual",
            },
        ],
    )
    source_path = tmp_path / "source.png"
    source_path.write_bytes(_png_bytes())

    with pytest.raises(ImageLocalizationValidationError, match="blanket text removal"):
        store.create_clean_master(
            "123",
            expected_revision=2,
            asset_id=asset_id,
            source_path=source_path,
            method="ai.all",
        )
    with pytest.raises(ImageLocalizationValidationError, match="protected region"):
        store.create_clean_master(
            "123",
            expected_revision=2,
            asset_id=asset_id,
            source_path=source_path,
            method="local_region_fill/v1",
        )


def test_ocr_scan_keeps_manual_regions_and_rejects_stale_source_digest(tmp_path: Path):
    store = ImageLocalizationStore(tmp_path)
    manifest = store.initialize("123", _source())
    asset = manifest["assets"][0]
    manifest = store.save_regions(
        "123",
        expected_revision=1,
        asset_id=asset["asset_id"],
        regions=[{
            "region_id": "manual-1",
            "bbox": [0.1, 0.1, 0.4, 0.2],
            "text": "Keep this edit",
            "classification": "translatable",
            "origin": "manual",
        }],
    )

    with pytest.raises(ImageLocalizationValidationError, match="source identity"):
        store.merge_ocr_regions(
            "123",
            expected_revision=2,
            asset_id=asset["asset_id"],
            source_identity_digest="wrong",
            provider="fake-ocr",
            provider_version="test-v1",
            regions=[],
        )

    scanned = store.merge_ocr_regions(
        "123",
        expected_revision=2,
        asset_id=asset["asset_id"],
        source_identity_digest=asset["source_identity_digest"],
        provider="fake-ocr",
        provider_version="test-v1",
        regions=[{
            "region_id": "ocr-1",
            "bbox": [0.5, 0.1, 0.9, 0.2],
            "text": "Machine text",
            "confidence": 0.91,
            "detected_language": "en",
            "classification": "product_fact",
        }],
    )

    regions = scanned["assets"][0]["regions"]
    assert [row["region_id"] for row in regions] == ["manual-1", "ocr-1"]
    assert regions[0]["origin"] == "manual"
    assert regions[1]["origin"] == "ocr"
    assert scanned["assets"][0]["ocr"]["provider"] == "fake-ocr"


@pytest.mark.parametrize(
    "region,error",
    [
        ({"region_id": "x", "bbox": [0, 0, 2, 1], "classification": "watermark"}, "bbox"),
        ({"region_id": "x", "bbox": [0, 0, 1, 1], "classification": "delete_everything"}, "classification"),
        ({"region_id": "x", "bbox": [0, 0, 1, 1], "classification": "watermark", "origin": "ai"}, "origin"),
    ],
)
def test_region_contract_is_closed_and_normalized(tmp_path: Path, region: dict, error: str):
    store = ImageLocalizationStore(tmp_path)
    manifest = store.initialize("123", _source())

    with pytest.raises(ImageLocalizationValidationError, match=error):
        store.save_regions(
            "123",
            expected_revision=1,
            asset_id=manifest["assets"][0]["asset_id"],
            regions=[region],
        )


def test_workbench_initializes_from_authoritative_source_snapshot_and_exposes_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(new_product_workbench, "IMAGE_LOCALIZATION_DIR", tmp_path)
    monkeypatch.setattr(
        new_product_workbench,
        "_image_localization_source_rows",
        lambda offer_id: [
            {"url": "https://img.example/main.jpg", "kind": "main"},
            {"url": "https://img.example/detail.jpg", "kind": "detail"},
        ],
    )
    monkeypatch.setattr(
        new_product_workbench, "resolve_offer_key", lambda value: str(value)
    )

    initialized = initialize_image_localization("123")
    summary = image_localization_summary("123")
    saved = save_image_localization_regions(
        "123",
        expected_revision=1,
        asset_id=initialized["manifest"]["assets"][0]["asset_id"],
        regions=[{
            "region_id": "manual-text",
            "bbox": [0.1, 0.2, 0.7, 0.3],
            "text": "English product fact",
            "classification": "translatable",
            "origin": "manual",
        }],
    )

    assert initialized["enabled"] is True
    assert len(summary["manifest"]["assets"]) == 2
    assert saved["manifest"]["revision"] == 2
    assert saved["manifest"]["assets"][0]["regions"][0]["text"] == "English product fact"


def test_batch1_http_and_studio_contracts_are_present():
    root = Path(__file__).resolve().parents[1]
    server = (root / "modules/sourcing/new_product_server.py").read_text(encoding="utf-8")
    product_server = (root / "modules/products/server.py").read_text(encoding="utf-8")
    html = (root / "web/ai_image_studio.html").read_text(encoding="utf-8")
    script = (root / "web/static/ai_image_studio.js").read_text(encoding="utf-8")

    assert "/api/new-product/content-package/image-localization/artifact" in server
    assert '"/api/new-product/content-package/image-localization/initialize"' in server
    assert '"/api/new-product/content-package/image-localization/regions"' in server
    assert '"/api/new-product/content-package/image-localization/clean-master"' in server
    assert '"content-package/image-localization/artifact"' in product_server
    assert '"content-package/image-localization/initialize"' in product_server
    assert '"content-package/image-localization/regions"' in product_server
    assert '"content-package/image-localization/clean-master"' in product_server
    assert 'id="imageLocalization"' in html
    assert 'id="imageLocalizationGrid"' in html
    assert 'id="imageLocalizationStatus"' in html
    assert "renderImageLocalization" in script
    assert "setImageLocalizationStatus" in script
    assert "saveImageLocalizationRegions" in script
    assert "createCleanMaster" in script
    assert "ocr_provider_enabled" in script
    assert "imageLocalizationDraftOfferId" in script
    assert "captureLocalizationDraft" in script
    assert "localizationRegionsFor" in script
