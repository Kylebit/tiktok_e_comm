"""The scheduled web entry must pin stores before any app module imports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _deployment(tmp_path: Path) -> tuple[Path, Path]:
    settings = tmp_path / "business" / "config" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    for name in ("catalog.db", "release.db"):
        (tmp_path / name).write_bytes(b"test-only")
    (tmp_path / "ozon").mkdir()
    workbench = tmp_path / "pinned" / "workbench.db"
    deployment = {
        "execution_mode": "web-only",
        "code_root": str(ROOT),
        "settings": str(settings),
        "catalog_database": str(tmp_path / "catalog.db"),
        "report_store_path": str(tmp_path / "report.db"),
        "release_store_path": str(tmp_path / "release.db"),
        "workbench_store_path": str(workbench),
        "ozon_data_root": str(tmp_path / "ozon"),
        "runtime_profile_id": "private-entry-test",
        "runtime_profile_path": str(tmp_path / "runtime-profile.json"),
    }
    candidate = tmp_path / "deployment.json"
    candidate.write_text(json.dumps(deployment), encoding="utf-8")
    return candidate, workbench


def _run_entry(tmp_path: Path, deployment: Path, *, preimport: bool = False):
    result = tmp_path / "import-result.json"
    marker = tmp_path / "serve-reached.txt"
    code = textwrap.dedent(
        """
        import json, os, sys, types
        from pathlib import Path
        deployment_path, result_path, marker_path, mode = sys.argv[1:5]
        if mode == 'preimport':
            import shared_platform.workbench_store
        fake = types.ModuleType('shared_platform.operations_launch')
        def serve(config, root):
            from shared_platform.workbench_store import WorkbenchStore, default_workbench_store
            Path(marker_path).write_text('serve reached')
            Path(result_path).write_text(json.dumps({
                'env': os.environ.get('ORBIT_WORKBENCH_STORE_PATH'),
                'constructor': str(WorkbenchStore().path),
                'factory': str(default_workbench_store().path),
                'configured': config['workbench_store_path'],
            }))
        fake.serve = serve
        sys.modules['shared_platform.operations_launch'] = fake
        from scripts.operations_web_entry import main
        sys.argv = ['operations_web_entry', '--deployment', deployment_path,
                    '--log', str(Path(deployment_path).with_name('entry.log'))]
        main()
        """
    )
    env = os.environ.copy()
    env["ORBIT_WORKBENCH_STORE_PATH"] = str(tmp_path / "stale" / "wrong.db")
    process = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code, str(deployment), str(result),
         str(marker), "preimport" if preimport else "fresh"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return process, result, marker


def test_scheduled_entry_pins_workbench_before_first_application_import(tmp_path):
    candidate, workbench = _deployment(tmp_path)
    process, result, marker = _run_entry(tmp_path, candidate)
    assert process.returncode == 0, process.stderr + (tmp_path / "entry.log").read_text(encoding="utf-8")
    assert marker.is_file()
    observed = json.loads(result.read_text(encoding="utf-8"))
    assert observed == {"env": str(workbench), "constructor": str(workbench),
                        "factory": str(workbench), "configured": str(workbench)}


def test_scheduled_entry_rejects_relative_workbench_pin_before_server(tmp_path):
    candidate, _ = _deployment(tmp_path)
    value = json.loads(candidate.read_text(encoding="utf-8"))
    value["workbench_store_path"] = "relative/workbench.db"
    candidate.write_text(json.dumps(value), encoding="utf-8")
    process, _, marker = _run_entry(tmp_path, candidate)
    assert process.returncode != 0
    assert not marker.exists()
    assert "workbench_store_path must be an absolute path" in process.stderr


def test_scheduled_entry_rejects_preimported_workbench_default(tmp_path):
    candidate, _ = _deployment(tmp_path)
    process, _, marker = _run_entry(tmp_path, candidate, preimport=True)
    assert process.returncode != 0
    assert not marker.exists()
    assert "application store imported before runtime pin" in process.stderr


@pytest.mark.parametrize("drift", ["deployment", "environment"])
def test_full_profile_refuses_workbench_path_drift_without_rewriting_profile(tmp_path, monkeypatch, drift):
    from scripts.stable_runtime_bootstrap import prebind_workbench_store, configure_stable_runtime

    candidate, old = _deployment(tmp_path)
    config = json.loads(candidate.read_text(encoding="utf-8"))
    profile = Path(config["runtime_profile_path"])
    profile.write_bytes(b"untouched-preexisting-profile")
    monkeypatch.setenv("ORBIT_WORKBENCH_STORE_PATH", str(tmp_path / "ambient-stale.db"))
    bound = prebind_workbench_store(config)
    assert bound == old
    if drift == "deployment":
        config["workbench_store_path"] = str(tmp_path / "changed" / "workbench.db")
    else:
        monkeypatch.setenv("ORBIT_WORKBENCH_STORE_PATH", str(tmp_path / "changed" / "workbench.db"))
    with pytest.raises(ValueError, match="WORKBENCH_STORE_PIN_DRIFT"):
        configure_stable_runtime(config, expected_workbench_store_path=bound)
    assert profile.read_bytes() == b"untouched-preexisting-profile"


def test_direct_serve_rejects_preflight_path_swap_before_profile_write(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from shared_platform import operations_launch
    from shared_platform.operations_runtime import RuntimeProfile

    # Direct serve writes these process keys before the injected late drift.
    # Register their originals with pytest so this rejection cannot alter the
    # following tests' settings, store roots or publication runtime identity.
    for name in (
        "ORBIT_WORKBENCH_STORE_PATH", "ORBIT_OPERATIONS_ENV", "ORBIT_OPERATIONS_DATA_ROOT",
        "ORBIT_SHOPEE_RECOVERY_ENABLED", "ORBIT_OPERATIONS_AGENT_EXECUTABLE",
        "ORBIT_HIVE_SETTINGS", "ORBIT_R3_CONFIG_ROOT", "TIKTOK_ECOMM_HOME",
        "ORBIT_OPERATIONS_CONFIG_ROOT", "ORBIT_PUBLICATION_HISTORY_ROOT",
    ):
        monkeypatch.setenv(name, str(tmp_path / "ambient" / name))
    candidate, _ = _deployment(tmp_path)
    config = json.loads(candidate.read_text(encoding="utf-8"))
    config.update({"operations_data_root": str(tmp_path / "operations"),
                   "r3_config_root": str(tmp_path), "manifest_digest": "synthetic"})
    profile = Path(config["runtime_profile_path"])
    profile.write_bytes(b"untouched-preexisting-profile")
    old = config["workbench_store_path"]
    changed = str(tmp_path / "swapped" / "workbench.db")

    def drift_during_preflight(value, root):
        assert value["workbench_store_path"] == old
        value["workbench_store_path"] = changed
        return {"operations_data_root": tmp_path / "operations", "settings": Path(value["settings"])}

    monkeypatch.setattr(operations_launch, "preflight_deployment", drift_during_preflight)
    monkeypatch.setattr(RuntimeProfile, "capture", classmethod(
        lambda cls, root: SimpleNamespace(manifest_digest="synthetic")))
    with pytest.raises(ValueError, match="WORKBENCH_STORE_PIN_DRIFT"):
        operations_launch.serve(config, ROOT)
    assert profile.read_bytes() == b"untouched-preexisting-profile"


def test_legacy_preview_can_explicitly_override_constructor_default(tmp_path, monkeypatch):
    from shared_platform.workbench_store import WorkbenchStore, default_workbench_store

    fixture = tmp_path / "legacy-preview" / "workbench.db"
    monkeypatch.setenv("ORBIT_WORKBENCH_STORE_PATH", str(tmp_path / "ambient" / "wrong.db"))
    monkeypatch.setattr(WorkbenchStore.__init__, "__defaults__", (fixture,))
    assert WorkbenchStore().path == fixture
    assert default_workbench_store().path == fixture
