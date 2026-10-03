from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["navigation", "rejection", "late", "late-response"])
def test_response_capture_preserves_bytes_and_lifetime_failures(mode):
    node = Path(os.environ["ORBIT_NODE_BIN"])
    result = subprocess.run(
        [str(node), str(ROOT / "tests/browser/response_body_capture_contract.cjs"), mode],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "mode": mode}
