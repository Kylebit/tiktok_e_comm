from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8767
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from modules.ozon.rus_server import serve

    serve(port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
