from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _deployment(tmp_path: Path) -> dict:
    code_root = tmp_path / "candidate"
    settings = tmp_path / "business" / "config" / "settings.json"
    catalog = tmp_path / "catalog.db"
    release = tmp_path / "release.db"
    ozon = tmp_path / "ozon"
    code_root.mkdir()
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    catalog.write_bytes(b"catalog")
    release.write_bytes(b"release")
    ozon.mkdir()
    return {
        "code_root": str(code_root),
        "settings": str(settings),
        "catalog_database": str(catalog),
        "report_store_path": str(tmp_path / "report.db"),
        "release_store_path": str(release),
        "workbench_store_path": str(tmp_path / "workbench.db"),
        "ozon_data_root": str(ozon),
        "runtime_profile_id": "stable-test-profile",
        "runtime_profile_path": str(tmp_path / "runtime-profile.json"),
    }


def test_bootstrap_is_stdlib_only_and_sets_identity_before_application_imports(
    tmp_path, monkeypatch
):
    from scripts.stable_runtime_bootstrap import (
        PROFILE_ENV,
        PUBLICATION_GATE_ENV,
        STORE_ENV,
        configure_stable_runtime,
    )

    source = (ROOT / "scripts/stable_runtime_bootstrap.py").read_text(encoding="utf-8")
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imported <= {"__future__", "json", "os", "pathlib", "typing"}

    deployment = _deployment(tmp_path)
    for variable in (PROFILE_ENV, PUBLICATION_GATE_ENV, *STORE_ENV.values()):
        monkeypatch.setenv(variable, "fixture-before-bootstrap")
    profile = configure_stable_runtime(deployment)
    assert Path(profile["settings_path"]).is_absolute()
    assert set(profile["stores"]) == set(STORE_ENV)
    assert __import__("os").environ[PROFILE_ENV] == deployment["runtime_profile_path"]
    assert __import__("os").environ[PUBLICATION_GATE_ENV] == "1"
    assert json.loads(Path(deployment["runtime_profile_path"]).read_text(encoding="utf-8")) == profile
    assert all(__import__("os").environ[variable] == profile["stores"][name] for name, variable in STORE_ENV.items())

def test_publication_entry_returns_zero_write_before_loading_execution(monkeypatch):
    from modules.products import server
    from shared_platform import runtime_identity

    monkeypatch.setenv("ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME", "1")
    monkeypatch.setattr(
        runtime_identity,
        "health_payload",
        lambda *args, **kwargs: {"state": "DATA_PROFILE_MISMATCH"},
    )
    monkeypatch.setattr(
        server,
        "_product_publication_platform_executors",
        lambda: (_ for _ in ()).throw(AssertionError("executor resolution reached")),
    )
    status, payload = server._start_product_publication({}, platform="OZON")
    assert status == 503
    assert payload["external_write_count"] == 0
