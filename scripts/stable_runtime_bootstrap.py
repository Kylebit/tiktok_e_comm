"""Build the non-secret stable runtime identity before application imports.

This module uses only the Python standard library so a launcher can load it by
file path before adding the candidate repository to ``sys.path``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping


PROFILE_ENV = "ORBIT_RUNTIME_PROFILE"
PUBLICATION_GATE_ENV = "ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME"
STORE_ENV = {
    "catalog": "ORBIT_CATALOG_DATABASE",
    "report": "ORBIT_REPORT_STORE_PATH",
    "release": "ORBIT_RELEASE_STORE_PATH",
    "workbench": "ORBIT_WORKBENCH_STORE_PATH",
    "ozon": "ORBIT_OZON_DATA_ROOT",
}


def prebind_workbench_store(deployment: Mapping[str, object]) -> Path:
    """Pin the legacy store before schema preflight imports its default path.

    Full profile generation remains in configure_stable_runtime after the
    deployment preflight, so a failed preflight does not rewrite the profile.
    """
    path = _absolute(deployment.get("workbench_store_path"), field="workbench_store_path")
    os.environ[STORE_ENV["workbench"]] = str(path)
    return path


def _absolute(value: object, *, field: str, must_exist: bool = False) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path")
    return path.resolve(strict=must_exist)


def configure_stable_runtime(deployment: Mapping[str, object], *, expected_workbench_store_path: Path | None = None) -> dict:
    """Write/reuse the public identity profile and export every path pin."""
    code_root = _absolute(deployment.get("code_root"), field="code_root", must_exist=True)
    profile_path = _absolute(
        deployment.get("runtime_profile_path"), field="runtime_profile_path"
    )
    settings_path = _absolute(
        deployment.get("settings"), field="settings", must_exist=True
    )
    stores = {
        "catalog": _absolute(
            deployment.get("catalog_database"),
            field="catalog_database",
            must_exist=True,
        ),
        "report": _absolute(
            deployment.get("report_store_path") or code_root / "data/orbit_platform.db",
            field="report_store_path",
        ),
        "release": _absolute(
            deployment.get("release_store_path"),
            field="release_store_path",
            must_exist=True,
        ),
        "workbench": _absolute(
            deployment.get("workbench_store_path") or code_root / "data/orbit_workbench.db",
            field="workbench_store_path",
        ),
        "ozon": _absolute(
            deployment.get("ozon_data_root"),
            field="ozon_data_root",
            must_exist=True,
        ),
    }
    if expected_workbench_store_path is not None and (
        stores["workbench"] != expected_workbench_store_path
        or os.environ.get(STORE_ENV["workbench"]) != str(expected_workbench_store_path)
    ):
        raise ValueError("WORKBENCH_STORE_PIN_DRIFT")
    profile = {
        "profile_id": str(deployment.get("runtime_profile_id") or "").strip(),
        "settings_path": str(settings_path),
        "stores": {name: str(path) for name, path in stores.items()},
    }
    if not profile["profile_id"]:
        raise ValueError("runtime_profile_id is required")
    encoded = json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    if not profile_path.is_file() or profile_path.read_text(encoding="utf-8") != encoded:
        temporary = profile_path.with_suffix(profile_path.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(profile_path)
    os.environ[PROFILE_ENV] = str(profile_path)
    os.environ[PUBLICATION_GATE_ENV] = "1"
    for name, variable in STORE_ENV.items():
        os.environ[variable] = str(stores[name])
    return profile
