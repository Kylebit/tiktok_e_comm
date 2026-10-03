from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts import regression_run_envelope as envelope


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows regression envelope contract")

HEAD = "a" * 40
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = Path(r"D:\oet")
DEAD_PID = 2_147_480_001


@pytest.fixture
def allocated_roots(monkeypatch: pytest.MonkeyPatch):
    roots: list[Path] = []

    def allocate(*, owner_pid: int = DEAD_PID):
        marker = envelope.allocate_envelope(str(SCRATCH_ROOT), str(REPO_ROOT), HEAD, owner_pid)
        roots.append(Path(marker["path"]))
        return marker

    yield allocate
    monkeypatch.setattr(envelope, "_pid_alive", lambda _pid: False)
    for root in roots:
        if root.exists():
            try:
                envelope.finalize_envelope(str(root), "failed", HEAD)
                envelope.cleanup_envelope(str(root), str(SCRATCH_ROOT), HEAD)
            except envelope.EnvelopeError:
                shutil.rmtree(root, ignore_errors=True)


def test_allocations_are_unique_short_and_phase_isolated(allocated_roots):
    first = allocated_roots()
    second = allocated_roots()
    assert first["path"] != second["path"]
    all_paths = set()
    for marker in (first, second):
        assert Path(marker["path"]).parent == SCRATCH_ROOT
        for phase in envelope.PHASES:
            values = marker["phases"][phase]
            temp = Path(values["basetemp"])
            ops = Path(values["operations_root"])
            assert temp.is_absolute() and ops.is_absolute()
            assert len(str(temp)) <= envelope.MAX_PHASE_PATH_LENGTH
            assert temp.parent == ops.parent == Path(marker["path"])
            assert temp != ops and temp not in ops.parents and ops not in temp.parents
            all_paths.update((str(temp), str(ops)))
    assert len(all_paths) == 12


def test_concurrent_allocations_never_collide(allocated_roots):
    with ThreadPoolExecutor(max_workers=6) as executor:
        markers = list(executor.map(lambda _: allocated_roots(), range(12)))
    roots = {marker["path"] for marker in markers}
    assert len(roots) == 12
    paths = {
        value
        for marker in markers
        for phase in marker["phases"].values()
        for value in phase.values()
    }
    assert len(paths) == 72


def test_phase_marker_binds_the_only_allowed_operations_root(allocated_roots):
    marker = allocated_roots()
    phase = marker["phases"]["focused"]
    validated = envelope.validate_test_operations_root(
        phase["operations_root"], marker["run_id"], "focused"
    )
    assert validated == Path(phase["operations_root"])
    owner_path = validated / "owner.json"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner["run_id"] = "tampered"
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    with pytest.raises(envelope.EnvelopeError, match="run_id"):
        envelope.validate_test_operations_root(
            phase["operations_root"], marker["run_id"], "focused"
        )


def test_environment_configuration_clears_stable_root_and_rejects_partial_envelope(allocated_roots):
    marker = allocated_roots()
    phase = marker["phases"]["browser"]
    environment = {
        "ORBIT_OPERATIONS_DATA_ROOT": r"C:\stable-operations",
        "ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME": "1",
        "ORBIT_TEST_OPERATIONS_DATA_ROOT": phase["operations_root"],
        "ORBIT_TEST_RUN_ID": marker["run_id"],
        "ORBIT_TEST_PHASE": "browser",
    }
    selected = envelope.configure_test_operations_environment(environment)
    assert selected == Path(phase["operations_root"])
    assert "ORBIT_OPERATIONS_DATA_ROOT" not in environment
    assert "ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME" not in environment
    with pytest.raises(envelope.EnvelopeError, match="incomplete"):
        envelope.configure_test_operations_environment({"ORBIT_TEST_RUN_ID": "only"})


def test_plain_environment_cannot_inherit_workstation_ledger():
    environment = {
        "ORBIT_OPERATIONS_DATA_ROOT": r"C:\stable-operations",
        "ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME": "1",
    }
    assert envelope.configure_test_operations_environment(environment) is None
    assert "ORBIT_OPERATIONS_DATA_ROOT" not in environment
    assert "ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME" not in environment


def test_pytest_process_uses_marker_bound_ledger():
    requested = os.environ.get("ORBIT_TEST_OPERATIONS_DATA_ROOT")
    if not requested:
        pytest.skip("requires a regression envelope")
    assert "ORBIT_OPERATIONS_DATA_ROOT" not in os.environ
    selected = envelope.validate_test_operations_root(
        requested,
        os.environ["ORBIT_TEST_RUN_ID"],
        os.environ["ORBIT_TEST_PHASE"],
    )
    assert selected == Path(requested)
    database = selected / "tasks.db"
    assert not database.exists()
    database.write_bytes(b"isolated-test-ledger")
    assert database.read_bytes() == b"isolated-test-ledger"
    database.unlink()
    (selected / f"pytest-probe-{os.getpid()}.json").write_text(
        json.dumps({"operations_root": str(selected)}), encoding="utf-8"
    )


def test_two_pytest_processes_write_only_their_own_phase_ledgers(allocated_roots):
    markers = [allocated_roots(), allocated_roots()]

    def launch(marker):
        phase = marker["phases"]["full"]
        child_env = os.environ.copy()
        child_env.update(
            {
                "PYTHONPATH": str(REPO_ROOT),
                "ORBIT_OPERATIONS_DATA_ROOT": r"C:\stable-never-write",
                "ORBIT_TEST_OPERATIONS_DATA_ROOT": phase["operations_root"],
                "ORBIT_TEST_RUN_ID": marker["run_id"],
                "ORBIT_TEST_PHASE": "full",
            }
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_regression_run_envelope.py::test_pytest_process_uses_marker_bound_ledger",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                phase["basetemp"],
            ],
            cwd=REPO_ROOT,
            env=child_env,
            check=False,
            capture_output=True,
            text=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(launch, markers))
    assert [result.returncode for result in results] == [0, 0]
    for marker in markers:
        own = Path(marker["phases"]["full"]["operations_root"])
        receipts = list(own.glob("pytest-probe-*.json"))
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text(encoding="utf-8"))["operations_root"] == str(own)
        assert not (own / "tasks.db").exists()


def test_cleanup_refuses_live_nonterminal_and_marker_mismatch(allocated_roots, monkeypatch):
    marker = allocated_roots(owner_pid=os.getpid())
    root = Path(marker["path"])
    with pytest.raises(envelope.EnvelopeError, match="non-terminal"):
        envelope.cleanup_envelope(str(root), str(SCRATCH_ROOT), HEAD)
    envelope.finalize_envelope(str(root), "passed", HEAD)
    monkeypatch.setattr(envelope, "_pid_alive", lambda _pid: True)
    with pytest.raises(envelope.EnvelopeError, match="still alive"):
        envelope.cleanup_envelope(str(root), str(SCRATCH_ROOT), HEAD)
    monkeypatch.setattr(envelope, "_pid_alive", lambda _pid: False)
    with pytest.raises(envelope.EnvelopeError, match="head"):
        envelope.cleanup_envelope(str(root), str(SCRATCH_ROOT), "b" * 40)
    assert root.exists()


def test_cleanup_deletes_only_one_owned_terminal_leaf(allocated_roots, monkeypatch):
    first = allocated_roots()
    second = allocated_roots()
    first_root = Path(first["path"])
    second_root = Path(second["path"])
    sentinel = second_root / "logs" / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    envelope.finalize_envelope(str(first_root), "passed", HEAD)
    monkeypatch.setattr(envelope, "_pid_alive", lambda _pid: False)
    envelope.cleanup_envelope(str(first_root), str(SCRATCH_ROOT), HEAD)
    assert not first_root.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_cleanup_refuses_git_checkout_and_reparse(allocated_roots, monkeypatch):
    marker = allocated_roots()
    root = Path(marker["path"])
    envelope.finalize_envelope(str(root), "failed", HEAD)
    monkeypatch.setattr(envelope, "_pid_alive", lambda _pid: False)
    (root / ".git").mkdir()
    with pytest.raises(envelope.EnvelopeError, match="Git checkout"):
        envelope.cleanup_envelope(str(root), str(SCRATCH_ROOT), HEAD)
    (root / ".git").rmdir()
    original = envelope._is_reparse
    monkeypatch.setattr(envelope, "_is_reparse", lambda path: path == root or original(path))
    with pytest.raises(envelope.EnvelopeError, match="reparse"):
        envelope.cleanup_envelope(str(root), str(SCRATCH_ROOT), HEAD)


def test_cleanup_refuses_scratch_root_and_unmarked_leaf():
    with pytest.raises(envelope.EnvelopeError, match="direct child"):
        envelope.cleanup_envelope(str(SCRATCH_ROOT), str(SCRATCH_ROOT), HEAD)
    leaf = SCRATCH_ROOT / f"unmarked-{os.getpid()}"
    leaf.mkdir(exist_ok=False)
    try:
        with pytest.raises(envelope.EnvelopeError, match="invalid owner marker"):
            envelope.cleanup_envelope(str(leaf), str(SCRATCH_ROOT), HEAD)
        assert leaf.exists()
    finally:
        leaf.rmdir()


def test_long_or_protected_scratch_roots_fail_closed():
    with pytest.raises(envelope.EnvelopeError, match="too long"):
        envelope.validate_scratch_root(r"D:\this-path-is-far-too-long", str(REPO_ROOT))
    with pytest.raises(envelope.EnvelopeError, match="protected"):
        envelope.validate_scratch_root("D:\\", str(REPO_ROOT))


def test_windows_default_scratch_root_is_exact_short_orbt_root():
    if Path("D:\\").exists():
        assert envelope._default_scratch_root() == r"D:\orbt"
    else:
        assert envelope._default_scratch_root() == r"C:\orbt"


def test_gate_settings_defaults_to_repository_example():
    selected = envelope.resolve_gate_settings(str(REPO_ROOT))
    assert selected == {
        "path": str((REPO_ROOT / "config" / "settings.example.json").resolve()),
        "inherited": False,
    }


def test_gate_settings_preserves_an_explicit_existing_file(tmp_path):
    explicit = tmp_path / "caller-settings.json"
    explicit.write_text("{}\n", encoding="utf-8")
    selected = envelope.resolve_gate_settings(str(REPO_ROOT), str(explicit))
    assert selected == {"path": str(explicit.resolve()), "inherited": True}


def test_gate_settings_missing_paths_fail_closed(tmp_path):
    with pytest.raises(envelope.EnvelopeError, match="configured ORBIT_HIVE_SETTINGS"):
        envelope.resolve_gate_settings(str(REPO_ROOT), str(tmp_path / "missing.json"))
    with pytest.raises(envelope.EnvelopeError, match="not a file"):
        envelope.resolve_gate_settings(str(REPO_ROOT), "")
    with pytest.raises(envelope.EnvelopeError, match="not a file"):
        envelope.resolve_gate_settings(str(REPO_ROOT), str(tmp_path))
    fake_repo = tmp_path / "repo"
    (fake_repo / "config").mkdir(parents=True)
    with pytest.raises(envelope.EnvelopeError, match="repository default settings"):
        envelope.resolve_gate_settings(str(fake_repo))


def test_powershell_gate_restores_settings_on_all_controlled_exits():
    script = (REPO_ROOT / "scripts" / "run_workbench_regression_gate.ps1").read_text(
        encoding="utf-8"
    )
    assert '"ORBIT_HIVE_SETTINGS"' in script
    assert "if (-not $settings.inherited) { $env:ORBIT_HIVE_SETTINGS = $settings.path }" in script
    assert script.index("if (-not $settings.inherited)") > script.index("try {")
    assert script.index('foreach ($name in $environmentNames)', script.index("finally {")) > script.index(
        "finally {"
    )


@pytest.mark.parametrize("setting", ["default", "explicit", "missing"])
def test_powershell_gate_settings_fail_closed_and_restore_after_failure(
    tmp_path, setting, monkeypatch
):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    scratch = Path(f"D:/o{uuid.uuid4().hex[:8]}")
    explicit = tmp_path / "explicit-settings.json"
    explicit.write_text("{}\n", encoding="utf-8")
    inherited = str(explicit) if setting == "explicit" else str(tmp_path / "missing.json")
    child_env = os.environ.copy()
    child_env["ORBIT_NODE_BIN"] = sys.executable  # Deliberately fail at the first JS check.
    if setting == "default":
        child_env.pop("ORBIT_HIVE_SETTINGS", None)
    else:
        child_env["ORBIT_HIVE_SETTINGS"] = inherited
    script = REPO_ROOT / "scripts" / "run_workbench_regression_gate.ps1"
    command = (
        f"try {{ & '{script}' -Python '{sys.executable}' -ScratchRoot '{scratch}'; "
        "Write-Output 'ORBIT_GATE_RESULT=unexpected-pass' } "
        "catch { Write-Output 'ORBIT_GATE_RESULT=failed'; "
        "Write-Output ('ORBIT_GATE_ERROR=' + $_.Exception.Message) }; "
        "Write-Output ('ORBIT_SETTINGS_AFTER=' + [string]$env:ORBIT_HIVE_SETTINGS)"
    )
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=REPO_ROOT,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "ORBIT_GATE_RESULT=failed" in result.stdout
        expected = "" if setting == "default" else inherited
        assert f"ORBIT_SETTINGS_AFTER={expected}" in result.stdout
        if setting == "missing":
            assert not scratch.exists(), "missing settings must fail before allocation"
        else:
            assert scratch.exists(), f"gate failed before allocation: {result.stdout}\n{result.stderr}"
            runs = list(scratch.iterdir())
            assert len(runs) == 1
            marker = json.loads((runs[0] / "owner.json").read_text(encoding="utf-8"))
            assert marker["status"] == "failed"
    finally:
        if scratch.exists():
            # The child has exited; PID reuse must not strand this test-owned run.
            monkeypatch.setattr(envelope, "_pid_alive", lambda _pid: False)
            for run_root in scratch.iterdir():
                marker_path = run_root / "owner.json"
                if marker_path.is_file():
                    marker = json.loads(marker_path.read_text(encoding="utf-8"))
                    envelope.cleanup_envelope(str(run_root), str(scratch), marker["head"])
            scratch.rmdir()
