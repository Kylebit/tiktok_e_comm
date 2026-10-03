from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts/verify_dashboard_sync.py"


def _load():
    spec = importlib.util.spec_from_file_location("dashboard_sync_portability", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_crlf_and_lf_have_the_same_text_digest(tmp_path):
    validator = _load()
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    content = "中文看板\nalpha \nbeta\n".encode("utf-8")
    lf.write_bytes(content)
    crlf.write_bytes(content.replace(b"\n", b"\r\n"))
    assert validator.digest(lf) == validator.digest(crlf)


def _snapshot(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def workspace(tmp_path):
    validator = _load()
    root = tmp_path / "supply"
    files = {}
    for index, source in enumerate(validator.TRACKED):
        relative = source.relative_to(validator.DOMAIN_ROOT).as_posix()
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"中文 {index}\nAlpha é \nBeta\n".encode("utf-8"))
        files[relative] = target
    script = root / "skills/manage-seaya-replenishment/scripts/verify_dashboard_sync.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(SCRIPT.read_bytes())
    manifest = root / "skills/manage-seaya-replenishment/references/dashboard-sync.json"

    def call(mode):
        before = _snapshot(root)
        result = subprocess.run(
            [sys.executable, "-B", str(script), mode], cwd=root,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        after = _snapshot(root)
        if mode == "--check":
            assert after == before, "--check must not alter bytes, mtimes or file set"
        else:
            name = manifest.relative_to(root).as_posix()
            assert {k: v for k, v in after.items() if k != name} == {
                k: v for k, v in before.items() if k != name
            }, "--update may only change the manifest"
        return result

    return SimpleNamespace(root=root, files=files, manifest=manifest, call=call)


def _update(workspace):
    result = workspace.call("--update")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(workspace.manifest.read_text(encoding="utf-8"))


def _write_manifest(workspace, payload):
    workspace.manifest.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def test_one_versioned_manifest_accepts_lf_and_crlf_without_fixture_updates(workspace):
    payload = _update(workspace)
    assert payload["schema_version"] == "dashboard-sync/v2"
    assert payload["digest_policy"] == "sha256-utf8-crlf-to-lf/v1"
    assert set(payload["sha256"]) == set(workspace.files)
    expected = workspace.manifest.read_bytes()
    assert workspace.call("--check").returncode == 0
    for path in workspace.files.values():
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert workspace.call("--check").returncode == 0
    assert workspace.manifest.read_bytes() == expected


@pytest.mark.parametrize("change", (
    lambda raw: raw.replace(b"Alpha", b"alpha"),
    lambda raw: raw.replace(b" \n", b"\n"),
    lambda raw: raw.replace(b"\n", b"\r"),
    lambda raw: raw.rstrip(b"\n"),
    lambda raw: b"\xef\xbb\xbf" + raw,
    lambda raw: raw.replace("中文".encode("utf-8"), "中又".encode("utf-8")),
    lambda raw: raw.replace("é".encode("utf-8"), "e\u0301".encode("utf-8")),
), ids=("case", "trailing-space", "lone-cr", "final-newline", "bom", "chinese", "unicode-form"))
def test_all_other_content_differences_remain_visible(workspace, change):
    _update(workspace)
    target = workspace.files["dashboard/app.js"]
    original = target.read_bytes()
    assert change(original) != original
    target.write_bytes(change(original))
    result = workspace.call("--check")
    assert result.returncode == 1
    assert "sync mismatch" in result.stdout
    assert "dashboard/app.js" in result.stdout


def test_legacy_raw_digest_remains_physical_until_explicit_update(workspace):
    first = workspace.files["dashboard/app.js"]
    first.write_bytes(first.read_bytes().replace(b"\n", b"\r\n"))
    _write_manifest(workspace, {"sha256": {
        relative: hashlib.sha256(path.read_bytes()).hexdigest()
        for relative, path in workspace.files.items()
    }})
    result = workspace.call("--check")
    assert result.returncode == 0
    assert "legacy raw-byte manifest" in result.stdout
    assert "--update" in result.stdout
    first.write_bytes(first.read_bytes().replace(b"\r\n", b"\n"))
    result = workspace.call("--check")
    assert result.returncode == 1
    assert "sha256-raw-bytes/v1" in result.stdout
    assert _update(workspace)["schema_version"] == "dashboard-sync/v2"
    assert workspace.call("--check").returncode == 0


@pytest.mark.parametrize("mutation, message", (
    (lambda p: p.update(schema_version="dashboard-sync/v999"), "unsupported schema_version"),
    (lambda p: p.update(digest_policy="ignore-whitespace"), "unsupported digest_policy"),
    (lambda p: p.pop("digest_policy"), "unsupported digest_policy"),
    (lambda p: p.pop("schema_version"), "legacy manifest"),
    (lambda p: p.update(unexpected=True), "unexpected manifest fields"),
    (lambda p: p["sha256"].pop("dashboard/app.js"), "file set mismatch"),
    (lambda p: p["sha256"].update({"dashboard/extra.bin": "0" * 64}), "file set mismatch"),
    (lambda p: p["sha256"].update({"dashboard/app.js": "bad-digest"}), "invalid SHA-256"),
    (lambda p: p.update(sha256=[]), "file set mismatch"),
))
def test_unsupported_or_incomplete_manifests_fail_without_writes(workspace, mutation, message):
    payload = _update(workspace)
    mutation(payload)
    _write_manifest(workspace, payload)
    result = workspace.call("--check")
    assert result.returncode == 1
    assert message in result.stdout


@pytest.mark.parametrize("raw", (b"[]", b"not-json"))
def test_invalid_manifest_document_fails_clearly(workspace, raw):
    workspace.manifest.write_bytes(raw)
    result = workspace.call("--check")
    assert result.returncode == 1
    assert "sync invalid" in result.stdout


def test_missing_managed_file_is_an_error(workspace):
    _update(workspace)
    workspace.files["dashboard/app.js"].unlink()
    result = workspace.call("--check")
    assert result.returncode == 1
    assert "sync invalid" in result.stdout
    assert "app.js" in result.stdout


@pytest.mark.parametrize("raw", (b"\xff\r\n", b"binary\x00\r\n"), ids=("invalid-utf8", "binary-nul"))
def test_non_text_managed_content_fails_check_and_update(workspace, raw):
    _update(workspace)
    before = workspace.manifest.read_bytes()
    workspace.files["dashboard/app.js"].write_bytes(raw)
    for mode in ("--check", "--update"):
        result = workspace.call(mode)
        assert result.returncode == 1
        assert "sync invalid" in result.stdout
    assert workspace.manifest.read_bytes() == before


def test_binary_assets_are_not_normalized_or_added_to_manifest(workspace, tmp_path):
    image = workspace.root / "dashboard/pixel.png"
    raw = b"\x89PNG\r\n\x1a\nfixture"
    image.write_bytes(raw)
    payload = _update(workspace)
    assert "dashboard/pixel.png" not in payload["sha256"]
    assert workspace.call("--check").returncode == 0
    assert image.read_bytes() == raw
    validator = _load()
    assert validator.digest(image, policy=validator.RAW_DIGEST_POLICY) == hashlib.sha256(raw).hexdigest()
    with pytest.raises(UnicodeDecodeError):
        validator.digest(image)
    with pytest.raises(ValueError, match="unsupported digest policy"):
        validator.digest(image, policy="unknown")
