"""Bind real compressed reference bytes without losing the original identity."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def entry():
    spec = importlib.util.spec_from_file_location(
        "manual_reference_binding_test",
        ROOT / "skills/prepare-product-images/scripts/prepare_product_images.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def compressed_reference(entry, tmp_path, monkeypatch):
    intake = tmp_path / "data/product_intake/123"
    source = intake / "images/image-01.jpg"
    source.parent.mkdir(parents=True)
    Image.effect_noise((64, 64), 80).convert("RGB").save(source, quality=75)
    (intake / "record.json").write_text(json.dumps({
        "source_mode": "manual_intake",
        "images": [{"position": 1, "filename": source.name,
                    "sha256": digest(source)[7:]}],
    }), encoding="utf-8")
    monkeypatch.setattr(entry, "REPO_ROOT", tmp_path)
    row = entry._prepare_manual_lingshi_references("123", {1})[1]
    assert row["source_digest"] != row["artifact_digest"]
    return source, row


def test_real_jpeg_compression_binds_both_byte_identities(entry, tmp_path, monkeypatch):
    source, row = compressed_reference(entry, tmp_path, monkeypatch)
    binding = entry._validated_reference_binding(row)
    assert Path(row["source_path"]) == source
    assert binding["source_digest"] == digest(source)
    assert binding["artifact_digest"] == digest(Path(row["local_path"]))
    assert row["source_digest"] != binding["artifact_digest"]


@pytest.mark.parametrize("changed", ["source", "artifact"])
def test_drift_of_either_original_or_compressed_bytes_is_rejected(
    entry, tmp_path, monkeypatch, changed
):
    source, row = compressed_reference(entry, tmp_path, monkeypatch)
    path = source if changed == "source" else Path(row["local_path"])
    path.write_bytes(path.read_bytes() + b"drift")
    with pytest.raises(ValueError, match=f"reference {changed} bytes drifted"):
        entry._validated_reference_binding(row)


def test_legacy_byte_identical_reference_keeps_original_binding(entry, tmp_path):
    source = tmp_path / "original.jpg"
    Image.new("RGB", (8, 8), "white").save(source)
    row = {"local_path": str(source), "source_digest": digest(source)}
    assert entry._validated_reference_binding(row) == {
        "url": None, "source_digest": digest(source)
    }


def test_derived_reference_cannot_hide_missing_original_path(entry, tmp_path):
    artifact = tmp_path / "derived.jpg"
    Image.new("RGB", (8, 8), "white").save(artifact)
    row = {"local_path": str(artifact), "source_digest": "sha256:" + "a" * 64,
           "artifact_digest": digest(artifact)}
    with pytest.raises(ValueError, match="reference source bytes drifted"):
        entry._validated_reference_binding(row)
