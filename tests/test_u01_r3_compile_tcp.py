"""The retired TCP preview compiler must not bypass the frozen release plan."""

from pathlib import Path
import hashlib
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/publish-approved-product/scripts/compile_release_candidate.py"


def _tree_hash(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_actual_handler_unapproved_preview_to_isolated_compile(tmp_path):
    """Legacy preview arguments fail before any candidate or provider write.

    Release compilation now consumes an exact approved ``plan_id`` from the
    durable store. The former HTTP preview bridge is deliberately absent, so
    an old caller cannot turn an unapproved preview into a release candidate.
    """

    marker = tmp_path / "owned.txt"
    marker.write_text("unchanged", encoding="utf-8")
    before = _tree_hash(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(SCRIPT),
            "--base-url",
            "http://127.0.0.1:9",
            "--expected-root",
            str(ROOT),
            "--offer-id",
            "3956742887",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 2
    assert "--plan-id" in result.stderr
    assert _tree_hash(tmp_path) == before
