from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    try:
        from desktop.orbit_desktop_webview import main as run_app
    except Exception as exc:
        print(f"[Orbit Desktop] webview shell unavailable, fallback to tkinter: {exc}")
        from desktop.orbit_desktop_app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
