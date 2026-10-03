"""Skill identity status is a read-only version check, never execution authority."""

import json
import hashlib
import os
from pathlib import Path
import shutil

import pytest

import shared_platform.skill_identity as identity
from shared_platform.skill_identity import CORE_SKILLS, _manifest, inspect_skill_identities


def _fixture_roots(tmp_path: Path):
    repository = tmp_path / "repository"
    personal = tmp_path / "personal"
    personal.mkdir()
    rows = []
    for skill_id, relative in CORE_SKILLS.items():
        source = repository / relative
        (source / "scripts").mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "# " + skill_id + "\n" + ("Duplicate inventory: BLOCKED_INVENTORY\n" if skill_id == "manage-seaya-replenishment" else "read-only fixture\n"),
            encoding="utf-8",
        )
        (source / "scripts" / "run.py").write_text("print('fixture')\n", encoding="utf-8")
        manifest = _manifest(source)
        rows.append({"id": skill_id, "source_path": relative, "installable": skill_id != "publish-approved-product",
                     "file_digests": {relative + "/" + file["path"]: file["normalized_sha256"] for file in manifest["files"]}})
        if skill_id != "manage-profit-settlement":
            shutil.copytree(source, personal / skill_id)
    (repository / "config").mkdir()
    (repository / "config" / "capability_catalog.json").write_text(json.dumps({"skills": rows}), encoding="utf-8")
    return repository, personal


def test_full_manifest_copy_raw_drift_conflict_and_missing_are_distinct(tmp_path):
    repository, personal = _fixture_roots(tmp_path)
    image = personal / "prepare-product-images" / "SKILL.md"
    image.write_bytes(image.read_bytes().replace(b"\r\n", b"\n"))
    delist = personal / "delist-products-by-sku" / "scripts" / "run.py"
    delist.write_text("print('different decision')\n", encoding="utf-8")
    supply = personal / "manage-seaya-replenishment" / "SKILL.md"
    supply.write_text("# manage-seaya-replenishment\nDuplicate inventory: deduplicate\n", encoding="utf-8")
    result = inspect_skill_identities(root=repository, installed_root=personal)
    assert result["execution_authority"] is False
    assert set(result["skills"]) == set(CORE_SKILLS)
    assert all(row["project"]["registry_verified"] for row in result["skills"].values())
    assert result["skills"]["prepare-product-publication"]["comparison"]["status"] == "MATCH"
    assert result["skills"]["prepare-product-images"]["comparison"]["status"] == "RAW_DRIFT"
    assert result["skills"]["delist-products-by-sku"]["comparison"]["status"] == "CONFLICT"
    assert result["skills"]["manage-seaya-replenishment"]["comparison"]["status"] == "CONFLICT"
    assert result["skills"]["manage-profit-settlement"]["comparison"]["status"] == "NOT_INSTALLED"
    assert result["skills"]["publish-approved-product"]["installable_in_registry"] is False
    files = result["skills"]["prepare-product-publication"]["project"]["manifest"]["files"]
    assert {entry["path"] for entry in files} == {"SKILL.md", "scripts/run.py"}


def test_changed_registered_source_fails_closed_even_when_install_matches(tmp_path):
    repository, personal = _fixture_roots(tmp_path)
    source = repository / CORE_SKILLS["prepare-product-publication"] / "SKILL.md"
    installed = personal / "prepare-product-publication" / "SKILL.md"
    source.write_text("# changed after registry\n", encoding="utf-8")
    installed.write_bytes(source.read_bytes())
    result = inspect_skill_identities(root=repository, installed_root=personal)
    row = result["skills"]["prepare-product-publication"]
    assert row["project"]["registry_verified"] is False
    assert row["comparison"]["status"] == "SOURCE_UNVERIFIED"
    assert row["execution_authority"] is False


def test_public_projection_has_no_absolute_paths_or_file_inventory(tmp_path):
    repository, personal = _fixture_roots(tmp_path)
    result = identity.public_skill_identities(root=repository, installed_root=personal)
    body = json.dumps(result)
    assert result["skills"]["prepare-product-publication"]["comparison"]["status"] == "MATCH"
    assert str(repository) not in body and str(personal) not in body
    for private_key in ("resolved_target", "files", "SKILL.md", "scripts/run.py", "raw_digest"):
        assert private_key not in body


def test_budget_exhaustion_returns_unknown_for_every_skill(tmp_path, monkeypatch):
    repository, personal = _fixture_roots(tmp_path)
    identity._CACHE.clear()
    monkeypatch.setattr(identity, "_MAX_FILE_BYTES", 32)
    result = inspect_skill_identities(root=repository, installed_root=personal)
    assert result["reason"] == "SCAN_LIMIT"
    assert {row["comparison"]["status"] for row in result["skills"].values()} == {"UNKNOWN"}
    assert all(row["execution_authority"] is False for row in result["skills"].values())


@pytest.mark.parametrize("budget_name,budget_value", [
    ("_MAX_DIRECTORIES", 1), ("_MAX_ENTRIES", 2), ("_MAX_FILES", 1), ("_MAX_BYTES", 4), ("_MAX_SECONDS", -1),
])
def test_global_budget_dimensions_fail_closed(tmp_path, monkeypatch, budget_name, budget_value):
    repository, personal = _fixture_roots(tmp_path)
    identity._CACHE.clear()
    monkeypatch.setattr(identity, budget_name, budget_value)
    result = inspect_skill_identities(root=repository, installed_root=personal)
    assert {row["comparison"]["status"] for row in result["skills"].values()} == {"UNKNOWN"}


def test_streaming_hash_normalizes_crlf_across_chunk_boundary(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("# fixture\n", encoding="utf-8")
    raw = b"a" * (64 * 1024 - 1) + b"\r\nb\r"
    (root / "large.md").write_bytes(raw)
    row = next(file for file in _manifest(root)["files"] if file["path"] == "large.md")
    assert row["sha256"] == hashlib.sha256(raw).hexdigest()
    assert row["normalized_sha256"] == hashlib.sha256(raw.decode().replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()


def test_nested_directory_link_is_not_traversed(tmp_path):
    repository, personal = _fixture_roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("do not read", encoding="utf-8")
    link = personal / "prepare-product-images" / "scripts" / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Windows symlink unavailable: {type(error).__name__}")
    identity._CACHE.clear()
    result = inspect_skill_identities(root=repository, installed_root=personal)
    row = result["skills"]["prepare-product-images"]
    assert row["installed"]["state"] == "INVALID"
    assert row["comparison"]["status"] == "INVALID"
    assert "private.txt" not in json.dumps(row)


def test_root_directory_link_must_resolve_to_named_skill_and_nested_link_is_invalid(tmp_path):
    repository, personal = _fixture_roots(tmp_path)
    skill_id = "manage-seaya-replenishment"
    source = personal / skill_id
    target = tmp_path / "other-worktree" / skill_id
    target.parent.mkdir()
    shutil.copytree(source, target)
    shutil.rmtree(source)
    try:
        os.symlink(target, source, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Windows symlink unavailable: {type(error).__name__}")
    identity._CACHE.clear()
    linked = inspect_skill_identities(root=repository, installed_root=personal)["skills"][skill_id]
    assert linked["installed"]["source_type"] == "symlink"
    assert linked["installed"]["resolved_target"] == str(target.resolve())
    assert linked["comparison"]["status"] == "MATCH"
    secret = tmp_path / "unrelated-secret"
    secret.mkdir()
    (secret / "private.txt").write_text("outside root", encoding="utf-8")
    os.symlink(secret, target / "scripts" / "escape", target_is_directory=True)
    blocked = inspect_skill_identities(root=repository, installed_root=personal)["skills"][skill_id]
    assert blocked["comparison"]["status"] == "INVALID"
    assert "private.txt" not in json.dumps(blocked)
    source.unlink()
    wrong = tmp_path / "wrong-target-name"
    wrong.mkdir()
    (wrong / "SKILL.md").write_text("# wrong\n", encoding="utf-8")
    os.symlink(wrong, source, target_is_directory=True)
    invalid = inspect_skill_identities(root=repository, installed_root=personal)["skills"][skill_id]
    assert invalid["installed"]["state"] == "INVALID"


def test_cache_detects_file_change_and_busy_scan_is_unknown(tmp_path):
    repository, personal = _fixture_roots(tmp_path)
    identity._CACHE.clear()
    first = inspect_skill_identities(root=repository, installed_root=personal)
    second = inspect_skill_identities(root=repository, installed_root=personal)
    assert second["observed_at"] == first["observed_at"]
    changed = personal / "prepare-product-images" / "scripts" / "run.py"
    changed.write_text("print('changed cache fixture')\n", encoding="utf-8")
    after = inspect_skill_identities(root=repository, installed_root=personal)
    assert after["skills"]["prepare-product-images"]["comparison"]["status"] == "CONFLICT"
    assert after["observed_at"] != first["observed_at"]
    assert identity._SCAN_LOCK.acquire(blocking=False)
    try:
        concurrent = inspect_skill_identities(root=repository, installed_root=personal)
    finally:
        identity._SCAN_LOCK.release()
    assert {row["comparison"]["status"] for row in concurrent["skills"].values()} == {"UNKNOWN"}
