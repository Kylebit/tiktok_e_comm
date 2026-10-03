"""Synthetic, read-only checks for a deployment input seal."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.verify_sealed_deployment_inputs import verify


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_unchanged_seal_passes_and_input_drift_holds(tmp_path: Path) -> None:
    work_order = tmp_path / "WORK_ORDER.md"
    work_order.write_text("source version 1", encoding="utf-8")
    manifest = tmp_path / "INPUT_HASHES.json"
    manifest.write_text(json.dumps({"files": {str(work_order): _sha(work_order.read_bytes())}}), encoding="utf-8")
    seal_sha = _sha(manifest.read_bytes())

    assert verify(manifest, seal_sha)["decision"] == "PASS_READONLY_INPUTS_MATCH"
    work_order.write_text("source version 2", encoding="utf-8")
    held = verify(manifest, seal_sha)
    assert held["decision"] == "HOLD_INPUT_DRIFT"
    assert held["drifts"][0]["reason"] == "SHA256_MISMATCH"


def test_manifest_drift_and_duplicate_keys_hold(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("safe", encoding="utf-8")
    manifest = tmp_path / "seal.json"
    manifest.write_text(json.dumps({"files": {str(target): _sha(target.read_bytes())}}), encoding="utf-8")
    reviewed_sha = _sha(manifest.read_bytes())
    manifest.write_text("{}", encoding="utf-8")
    assert verify(manifest, reviewed_sha)["drifts"][0]["reason"] == "MANIFEST_DRIFT"

    duplicate = b'{"files": {}, "files": {}}'
    manifest.write_bytes(duplicate)
    assert verify(manifest, _sha(duplicate))["drifts"][0]["reason"] == "INVALID_MANIFEST"


def test_optimized_interpreter_is_not_a_deployment_gate(tmp_path: Path) -> None:
    manifest = tmp_path / "seal.json"
    manifest.write_text('{"files": {}}', encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_sealed_deployment_inputs.py"
    run = subprocess.run(
        [sys.executable, "-O", str(script), str(manifest), "--expected-manifest-sha256", _sha(manifest.read_bytes())],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 2
    assert json.loads(run.stdout)["decision"] == "HOLD_OPTIMIZED_PYTHON"
