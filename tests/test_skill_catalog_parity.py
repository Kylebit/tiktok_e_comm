"""Offline parity for the registered R3 Skill and closure client guidance."""

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.sync_product_publication_skills import (
    SkillSetInstallError,
    main as sync_main,
    sync_registered,
)
from shared_platform.capability_runtime import catalog


ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / "skills/publish-approved-product/scripts/close_product_publication.py"


def test_registry_default_and_explicit_selection_exclude_noninstallable_r3(tmp_path):
    rows = catalog(ROOT)["skills"]
    r3 = next(row for row in rows if row["id"] == "publish-approved-product")
    assert r3["installable"] is False

    target = tmp_path / "personal-copy"
    checked = sync_registered(
        runtime_root=ROOT, destination_root=target, names=[], install=False
    )
    assert checked["mode"] == "check"
    assert "publish-approved-product" not in checked["skills"]
    assert not target.exists()

    for install in (False, True):
        with pytest.raises(SkillSetInstallError, match="not an installable current registry source"):
            sync_registered(
                runtime_root=ROOT,
                destination_root=target,
                names=["publish-approved-product"],
                install=install,
            )
    assert not target.exists()


def test_registry_without_install_only_checks_and_does_not_copy(tmp_path, capsys):
    target = tmp_path / "new-skills"
    code = sync_main([
        "--registry", "--runtime-root", str(ROOT),
        "--destination-root", str(target), "--skill", "use-lingshi-ai",
    ])
    result = json.loads(capsys.readouterr().out)
    assert code == 1  # Fresh destination is missing, as a check must report.
    assert result["mode"] == "check"
    assert result["backups"] == {}
    assert not target.exists()


def test_catalog_closure_method_is_offline_help_and_matches_client(tmp_path):
    row = next(item for item in catalog(ROOT)["tools"] if item["id"] == "publication-closure")
    argv = shlex.split(row["method"])
    assert argv[:2] == ["python", "-I"]
    assert argv[2] == "R/skills/publish-approved-product/scripts/close_product_publication.py"
    assert argv[3:] == ["--help"]
    assert "--base-url" in row["inputs"] and "--expected-root" in row["inputs"]
    assert all(action in row["inputs"] for action in ("doctor", "prepare", "record", "latest"))

    result = subprocess.run(
        [sys.executable, "-I", str(CLOSURE), "--help"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert all(action in result.stdout for action in ("doctor", "prepare", "record", "latest"))
    assert not list(tmp_path.iterdir())

    spec = importlib.util.spec_from_file_location("closure_catalog_help_parity", CLOSURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed = []

    def stop_before_runtime(base, expected_root):
        parsed.append((base, expected_root))
        return None, 2

    module._runtime = stop_before_runtime
    for action in (
        ["doctor"], ["prepare", "--input", "synthetic.json"],
        ["record", "--prepared", "synthetic.json"],
        ["latest", "--offer-id", "123", "--plan-id", "plan"],
    ):
        assert module.main([
            "--base-url", "http://127.0.0.1:8765", "--expected-root", str(ROOT),
            *action,
        ]) == 2
    assert len(parsed) == 4
    assert not list(tmp_path.iterdir())


def test_obsolete_closure_arguments_fail_before_transport(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("closure_catalog_parity", CLOSURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def no_transport(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("obsolete CLI reached transport")

    monkeypatch.setattr(module, "request", no_transport)
    with pytest.raises(SystemExit) as error:
        module.main([
            "--offer-id", "123", "--revision", "5", "--plan-id", "plan",
            "--snapshot-digest", "sha256:old", "--accepted-by", "fixture",
        ])
    assert error.value.code == 2
    assert calls == []
    stderr = capsys.readouterr().err
    assert "error:" in stderr
    assert "invalid choice" in stderr or "unrecognized arguments" in stderr
