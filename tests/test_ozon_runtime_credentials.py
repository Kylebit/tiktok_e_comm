from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _credential(path: Path, account_id: str = "4953064") -> tuple[Path, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"client_id": account_id, "api_key": "synthetic-secret"}),
        encoding="utf-8",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_relative_ozon_data_dir_is_resolved_from_settings_root(monkeypatch, tmp_path):
    from modules.ozon import config

    settings_root = tmp_path / "configured"
    data = settings_root.parent / "ozon" / "webapp" / "data"
    data.mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setattr(config, "get", lambda key: "../ozon/webapp/data" if key == "ozon.data_dir" else None)
    monkeypatch.setattr(config, "settings_base_dir", lambda: settings_root)

    assert config.ozon_data_dir() == data.resolve()


@pytest.mark.parametrize("mode", ("missing", "account_drift"))
def test_bad_pinned_account_fails_before_provider_call(monkeypatch, tmp_path, mode):
    from modules.ozon import client
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport

    calls = []
    path = tmp_path / "credentials.local.json"
    if mode == "account_drift":
        path, digest = _credential(path, "999")
    else:
        digest = "0" * 64
    monkeypatch.setenv("ORBIT_OZON_CREDENTIALS_PATH", str(path))
    monkeypatch.setenv("ORBIT_OZON_CREDENTIALS_SHA256", digest)
    monkeypatch.setenv("ORBIT_OZON_EXPECTED_ACCOUNT_ID", "4953064")
    monkeypatch.setattr(client, "ozon_post_bound", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises((FileNotFoundError, ValueError)):
        OfficialOzonV4Transport().catalog_account()
    assert calls == []


def test_pinned_manifest_exposes_only_account_and_digest(tmp_path):
    from shared_platform.ozon_runtime_credentials import load_pinned_ozon_credentials

    path, digest = _credential(tmp_path / "credentials.local.json")
    pinned = load_pinned_ozon_credentials(
        path, expected_sha256=digest, expected_account_id="4953064"
    )

    assert pinned.public() == {
        "ozon_account_id": "4953064",
        "ozon_credentials_sha256": digest,
    }
    assert "synthetic-secret" not in json.dumps(pinned.public())
    assert str(path) not in json.dumps(pinned.public())
    assert "synthetic-secret" not in repr(pinned)


@pytest.mark.parametrize("mode", ("missing", "digest_drift", "account_drift"))
def test_publication_dependency_rejects_missing_or_drifted_pin_before_provider_call(
    monkeypatch, tmp_path, mode
):
    from modules.ozon import client
    from shared_platform.product_publication_live_dependencies import (
        build_live_ozon_dependencies,
    )

    calls = []
    for name in (
        "ORBIT_OZON_CREDENTIALS_PATH",
        "ORBIT_OZON_CREDENTIALS_SHA256",
        "ORBIT_OZON_EXPECTED_ACCOUNT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    if mode != "missing":
        account_id = "999" if mode == "account_drift" else "4953064"
        path, digest = _credential(tmp_path / "credentials.local.json", account_id)
        monkeypatch.setenv("ORBIT_OZON_CREDENTIALS_PATH", str(path))
        monkeypatch.setenv(
            "ORBIT_OZON_CREDENTIALS_SHA256",
            "0" * 64 if mode == "digest_drift" else digest,
        )
        monkeypatch.setenv("ORBIT_OZON_EXPECTED_ACCOUNT_ID", "4953064")
    monkeypatch.setattr(
        client,
        "ozon_post_bound",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises((FileNotFoundError, ValueError)):
        build_live_ozon_dependencies()
    assert calls == []


def test_ozon_publication_identity_binds_public_pin_and_credential_code(
    monkeypatch, tmp_path
):
    from modules.products import server
    from shared_platform.product_publication_live_dependencies import (
        build_live_ozon_dependencies,
    )

    path, digest = _credential(tmp_path / "credentials.local.json")
    monkeypatch.setenv("ORBIT_OZON_CREDENTIALS_PATH", str(path))
    monkeypatch.setenv("ORBIT_OZON_CREDENTIALS_SHA256", digest)
    monkeypatch.setenv("ORBIT_OZON_EXPECTED_ACCOUNT_ID", "4953064")

    dependencies = build_live_ozon_dependencies()
    identity = server._product_publication_execution_identity("OZON")
    assert dependencies.catalog_account_resolver() == {
        "account_id": "4953064",
        "credential_ref": "ozon-config-account",
    }
    assert identity["ozon_account_id"] == "4953064"
    assert identity["ozon_credentials_sha256"] == digest
    assert "modules/ozon/config.py" in server._PRODUCT_PUBLICATION_PLATFORM_EXECUTION_FILES["OZON"]
    assert "shared_platform/ozon_runtime_credentials.py" in server._PRODUCT_PUBLICATION_PLATFORM_EXECUTION_FILES["OZON"]
    assert "synthetic-secret" not in json.dumps(identity)


def test_ozon_publication_identity_is_accepted_by_all_durable_boundaries():
    from shared_platform.product_publication_reports import (
        _execution_identity as report_identity,
    )
    from shared_platform.product_publication_runner import (
        _execution_identity as runner_identity,
    )
    from shared_platform.product_publication_runs import (
        _execution_identity as run_identity,
    )

    identity = {
        "skill_digest": "1" * 64,
        "git_commit": "2" * 40,
        "code_digest": "3" * 64,
        "ozon_account_id": "4953064",
        "ozon_credentials_sha256": "4" * 64,
    }
    assert runner_identity(identity) == identity
    assert report_identity(identity) == identity
    assert run_identity(identity) == identity
