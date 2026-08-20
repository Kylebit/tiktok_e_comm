from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_product_publication_skills.py"


def _module():
    spec = importlib.util.spec_from_file_location("product_publication_skill_sync", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_skill(root: Path, name: str, value: str) -> Path:
    skill = root / name
    (skill / "agents").mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"# {name}\n{value}\n", encoding="utf-8")
    (skill / "agents" / "openai.yaml").write_text(
        f"description: {value}\n", encoding="utf-8"
    )
    return skill


def test_canonical_publication_skill_set_is_explicit_and_complete():
    sync = _module()

    assert sync.SKILL_NAMES == (
        "prepare-product-publication",
        "prepare-product-images",
        "publish-approved-product",
    )
    for name in sync.SKILL_NAMES:
        skill = ROOT / "skills" / name
        assert (skill / "SKILL.md").is_file()
        assert (skill / "agents" / "openai.yaml").is_file()


def test_install_all_is_idempotent_and_check_all_reports_one_suite(tmp_path):
    sync = _module()
    source = tmp_path / "source"
    destination = tmp_path / "installed"
    for index, name in enumerate(sync.SKILL_NAMES, start=1):
        _write_skill(source, name, f"revision-{index}")

    first = sync.install_all(source_root=source, destination_root=destination)
    second = sync.install_all(source_root=source, destination_root=destination)
    checked = sync.check_all(source_root=source, destination_root=destination)

    assert first["ok"] is True
    assert second["ok"] is True
    assert checked["ok"] is True
    assert checked["suite_digest"] == first["suite_digest"] == second["suite_digest"]
    assert tuple(checked["skills"]) == sync.SKILL_NAMES
    assert all(row["ok"] for row in checked["skills"].values())


def test_install_preflight_refuses_unmanaged_files_before_writing_any_skill(tmp_path):
    sync = _module()
    source = tmp_path / "source"
    destination = tmp_path / "installed"
    for name in sync.SKILL_NAMES:
        _write_skill(source, name, "canonical")
        installed = _write_skill(destination, name, "old")
        if name == "prepare-product-images":
            (installed / "unmanaged.txt").write_text("do not erase", encoding="utf-8")

    before = (destination / sync.SKILL_NAMES[0] / "SKILL.md").read_text(encoding="utf-8")
    with pytest.raises(sync.SkillSetInstallError, match="unmanaged"):
        sync.install_all(source_root=source, destination_root=destination)

    assert (destination / sync.SKILL_NAMES[0] / "SKILL.md").read_text(
        encoding="utf-8"
    ) == before
    assert (destination / "prepare-product-images" / "unmanaged.txt").is_file()


def test_check_all_returns_actionable_missing_and_extra_drift(tmp_path):
    sync = _module()
    source = tmp_path / "source"
    destination = tmp_path / "installed"
    for name in sync.SKILL_NAMES:
        _write_skill(source, name, "canonical")
    sync.install_all(source_root=source, destination_root=destination)
    (destination / "prepare-product-publication" / "extra.md").write_text(
        "drift", encoding="utf-8"
    )
    missing = destination / "prepare-product-images" / "agents" / "openai.yaml"
    missing.unlink()

    result = sync.check_all(source_root=source, destination_root=destination)

    assert result["ok"] is False
    assert result["skills"]["prepare-product-publication"]["extra_files"] == [
        "extra.md"
    ]
    assert result["skills"]["prepare-product-images"]["missing_files"] == [
        "agents/openai.yaml"
    ]
