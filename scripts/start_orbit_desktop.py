from __future__ import annotations

import sys
from pathlib import Path


def main(argv=None):
    # This script's source location is authoritative; cwd and external Python are not.
    root = Path(__file__).resolve().parents[1]
    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, str(root))
    from desktop.startup import main as run
    return run(argv)


if __name__ == '__main__':
    raise SystemExit(main())
