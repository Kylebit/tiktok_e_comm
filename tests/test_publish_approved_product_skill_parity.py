from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "skills" / "publish-approved-product"
INSTALLED = Path.home() / ".codex" / "skills" / "publish-approved-product"
SYNC_SCRIPT = ROOT / "scripts" / "sync_publish_approved_product_skill.py"


def _sync_module():
    spec = importlib.util.spec_from_file_location("skill_sync", SYNC_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repo_is_canonical_complete_skill_tree():
    assert (CANONICAL / "SKILL.md").is_file()
    assert (CANONICAL / "agents" / "openai.yaml").is_file()
    assert (CANONICAL / "references" / "tiktok.md").is_file()
    assert (CANONICAL / "references" / "shopee.md").is_file()
    assert (CANONICAL / "references" / "ozon.md").is_file()
    assert (CANONICAL / "scripts" / "publish_approved_product.py").is_file()


def test_installed_skill_matches_repository_manifest_and_hash():
    sync = _sync_module()
    canonical = sync.build_manifest(CANONICAL)
    installed = sync.build_manifest(INSTALLED)

    assert canonical.files == installed.files
    assert canonical.digest == installed.digest


def test_manifest_ignores_only_runtime_cache_artifacts(tmp_path):
    sync = _sync_module()
    source = tmp_path / "skill"
    (source / "scripts" / "__pycache__").mkdir(parents=True)
    (source / ".pytest_cache").mkdir()
    (source / "SKILL.md").write_text("canonical", encoding="utf-8")
    (source / "scripts" / "tool.py").write_text("print('ok')", encoding="utf-8")
    (source / "scripts" / "__pycache__" / "tool.pyc").write_bytes(b"cache")
    (source / ".pytest_cache" / "state").write_text("cache", encoding="utf-8")

    manifest = sync.build_manifest(source)

    assert tuple(manifest.files) == ("SKILL.md", "scripts/tool.py")


def test_sync_install_is_idempotent_and_detects_unmanaged_drift(tmp_path):
    sync = _sync_module()
    destination = tmp_path / "installed"

    first = sync.sync_install(CANONICAL, destination)
    second = sync.sync_install(CANONICAL, destination)
    assert first.digest == second.digest == sync.build_manifest(CANONICAL).digest
    assert sync.check_parity(CANONICAL, destination)["ok"] is True

    (destination / "references" / "unmanaged.md").write_text("drift", encoding="utf-8")
    parity = sync.check_parity(CANONICAL, destination)
    assert parity["ok"] is False
    assert parity["extra_files"] == ["references/unmanaged.md"]
