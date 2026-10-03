"""Portable CLI can inspect a full runtime but cannot replace its authority."""

import json
from pathlib import Path
import subprocess
import sys

from scripts.package_agent_tools import build, PORTABLE_SKILLS
from shared_platform import capability_runtime as rt
from test_portable_tool_context import profile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "skills/publish-approved-product/scripts/close_product_publication.py"
RUNTIME_MODULE = "shared_platform/product_publication_closure.py"


def test_catalog_declares_closure_as_full_runtime_only():
    catalog = rt.catalog(ROOT)
    row = next(item for item in catalog["tools"] if item["id"] == "publication-closure")

    assert row["stage"] == "WORKFLOW_RUNTIME_REQUIRED"
    assert row["required_files"] == [SCRIPT, RUNTIME_MODULE]
    assert row["python_dependencies"] == []
    assert "publish-approved-product" not in PORTABLE_SKILLS


def test_packaged_closure_source_cannot_run_without_full_runtime(tmp_path):
    package = tmp_path / "package"
    receipt = build(ROOT, package, write=True)
    project = tmp_path / "project"
    project.mkdir()

    assert SCRIPT in receipt["manifest"]["files"]
    assert RUNTIME_MODULE not in receipt["manifest"]["files"]
    assert (package / SCRIPT).is_file()
    assert not (package / RUNTIME_MODULE).exists()

    doctor = rt.doctor(package, profile(project), "publication-closure")
    capability = doctor["capabilities"][0]
    assert doctor["ok"] is False
    assert capability["missing_files"] == [RUNTIME_MODULE]

    result = subprocess.run(
        [sys.executable, "-I", str(package / SCRIPT), "--help"],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert "prepare" in result.stdout and "record" in result.stdout
    assert result.stderr == ""

    before = set(project.iterdir())
    detached = subprocess.run(
        [sys.executable, "-I", str(package / SCRIPT)], cwd=project,
        capture_output=True, text=True, encoding="utf-8",
    )
    assert detached.returncode == 2
    assert json.loads(detached.stdout)["status"] == "CONFIG_REQUIRED"
    assert set(project.iterdir()) == before


def test_two_builds_have_identical_manifest_and_bytes(tmp_path):
    first = build(ROOT, tmp_path / "first", write=True)
    second = build(ROOT, tmp_path / "second", write=True)

    assert first["manifest"] == second["manifest"]
    for relative in [*first["manifest"]["files"], "config/tool_runtime_manifest.json"]:
        assert (tmp_path / "first" / relative).read_bytes() == (
            tmp_path / "second" / relative
        ).read_bytes()
