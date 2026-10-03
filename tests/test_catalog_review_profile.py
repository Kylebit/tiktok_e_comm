from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "catalog_review_preview.py"


def _wait_for_ready(path: Path, process: subprocess.Popen[str], previous_pid: int | None = None) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("pid") != previous_pid:
                return value
        assert process.poll() is None, process.stderr.read()
        time.sleep(0.05)
    raise AssertionError("review preview did not write a readiness receipt")


def test_profile_bound_review_preview_uses_only_its_explicit_copy(tmp_path: Path):
    evidence = tmp_path / "evidence"
    private = evidence / "private"; private.mkdir(parents=True)
    snapshot = private / "review.db"
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE products (image_url TEXT)")
        connection.execute("CREATE TABLE shopee_products (image_url TEXT)")
    metadata = evidence / "metadata.json"
    metadata.write_text(json.dumps({
        "snapshot_database": str(snapshot), "source_database": str(evidence / "formal.db"), "captured_at": "2026-09-08T00:00:00Z", "source_updated_at": "fixture",
    }), encoding="utf-8")
    state = tmp_path / "review-state"; state.mkdir()
    settings = state / "settings.json"
    profile = state / "runtime-profile.json"
    profile.write_text(json.dumps({
        "profile_id": "catalog-review-fixture", "settings_path": str(settings.resolve()),
        "stores": {
            "catalog": str(snapshot.resolve()),
            "reports_release": str((state / "unconnected-platform.db").resolve()),
            "workbench": str((state / "unconnected-workbench.db").resolve()),
            "ozon": str((ROOT / "modules/ozon/legacy_webapp/data").resolve()),
        },
    }), encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-X", "utf8", str(SCRIPT), "--metadata", str(metadata), "--state-dir", str(state), "--port", "0", "--profile", str(profile)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    database_digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    try:
        ready = _wait_for_ready(state / "ready.json", process)
        assert ready["mode"] == "LOCAL_REVIEW_COPY"
        assert ready["database"] == str(snapshot.resolve())
        assert ready["profile_path"] == str(profile.resolve())
        with urllib.request.urlopen(f"http://127.0.0.1:{ready['port']}/api/health", timeout=5) as response:
            health = json.loads(response.read())
        assert health["state"] == "READY"
        assert health["profile"]["profile_id"] == "catalog-review-fixture"
        assert health["stores"]["catalog"] == str(snapshot.resolve())
        assert health["business_execution_verified"] is False
    finally:
        process.terminate()
        process.wait(timeout=5)
    restarted = subprocess.Popen(
        [sys.executable, "-I", "-B", "-X", "utf8", str(SCRIPT), "--metadata", str(metadata), "--state-dir", str(state), "--port", "0", "--profile", str(profile)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        resumed = _wait_for_ready(state / "ready.json", restarted, ready["pid"])
        assert resumed["pid"] != ready["pid"]
        assert resumed["database"] == str(snapshot.resolve())
        with urllib.request.urlopen(f"http://127.0.0.1:{resumed['port']}/api/health", timeout=5) as response:
            assert json.loads(response.read())["state"] == "READY"
    finally:
        restarted.terminate()
        restarted.wait(timeout=5)
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == database_digest
    assert not (state / "unconnected-platform.db").exists()
    assert not (state / "unconnected-workbench.db").exists()
