"""Repository-aware wrapper for the profit settlement CLI."""

from __future__ import annotations

from pathlib import Path
import sys


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "domains" / "data_operations" / "profit_settlement").is_dir():
            return parent
    raise RuntimeError("profit settlement repository root not found")


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domains.data_operations.profit_settlement.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
