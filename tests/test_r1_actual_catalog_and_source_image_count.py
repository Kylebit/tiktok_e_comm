import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from shared_platform.release_control import build_release_dashboard
from test_release_control import _release_fixture


@pytest.fixture(autouse=True)
def synthetic_runtime_settings(monkeypatch):
    monkeypatch.setenv(
        "ORBIT_HIVE_SETTINGS",
        str(Path(__file__).parents[1] / "config/settings.example.json"),
    )


def _preparation_module():
    path = Path(__file__).parents[1] / "skills/prepare-product-publication/scripts/prepare_product_publication.py"
    spec = importlib.util.spec_from_file_location("r1_actual_count_regression", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dashboard_consumes_explicit_catalog_binding_without_default_catalog_read(
    tmp_path, monkeypatch
):
    root, legacy_database = _release_fixture(tmp_path / "runtime")
    catalog = tmp_path / "bound-catalog.db"
    shutil.copy2(legacy_database, catalog)
    legacy_database.write_bytes(b"Wrong source-relative catalog must not be read")
    monkeypatch.setenv("ORBIT_CATALOG_DATABASE", str(catalog))
    before = catalog.read_bytes()

    result = build_release_dashboard(
        root=root, offer_id="3828811808", strict_catalog=True,
        report_store_path=tmp_path / "absent-report.db",
    )

    assert result["safety"]["external_writes_performed"] == []
    assert catalog.read_bytes() == before


def test_dashboard_explicit_database_argument_precedes_environment(tmp_path, monkeypatch):
    root, catalog = _release_fixture(tmp_path / "runtime")
    monkeypatch.setenv("ORBIT_CATALOG_DATABASE", "relative-invalid-catalog.db")

    result = build_release_dashboard(
        root=root, offer_id="3828811808", database_path=catalog,
        report_store_path=tmp_path / "absent-report.db", strict_catalog=True,
    )

    assert result["safety"]["external_writes_performed"] == []


def test_actual_dashboard_source_count_is_separate_from_generated_content(tmp_path):
    root, catalog = _release_fixture(tmp_path / "runtime")
    state_path = root / "data/new_product_workbench/3828811808.json"
    state = json.loads(state_path.read_text())
    state["source"] = {"images": [{"url": f"https://example.com/source-{n}.jpg"} for n in range(3)]}
    state_path.write_text(json.dumps(state))

    preview = build_release_dashboard(
        root=root, offer_id="3828811808", database_path=catalog,
        report_store_path=tmp_path / "absent-report.db",
    )

    assert len(preview["content"]["images"]) == 2
    assert preview["product"]["source_image_count"] == 3
    assert _preparation_module()._safe_product_facts(preview)["source_image_count"] == 3


@pytest.mark.parametrize("count", [0, 3])
def test_r1_prefers_explicit_valid_source_count_over_rendered_gallery(count):
    facts = _preparation_module()._safe_product_facts(
        {"product": {"title": "Local product", "source_image_count": count},
         "content": {"images": [{}] * 7}}
    )
    assert facts["source_image_count"] == count


@pytest.mark.parametrize("count", [True, -1, "3", None, 3.0])
def test_r1_rejects_invalid_explicit_source_count_instead_of_falling_back(count):
    module = _preparation_module()
    with pytest.raises(module.PreparationError):
        module._safe_product_facts(
            {"product": {"title": "Local product", "source_image_count": count},
             "content": {"images": [{}] * 7}}
        )


def test_old_dashboard_without_source_count_retains_compatible_gallery_fallback():
    facts = _preparation_module()._safe_product_facts(
        {"product": {"title": "Legacy product"}, "content": {"images": [{}, {}]}}
    )
    assert facts["source_image_count"] == 2
