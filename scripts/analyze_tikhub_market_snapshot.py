"""Compatibility entry for retained TikHub sample analysis; no provider calls."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='Analyze a normalized local sample, never market totals')
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path: sys.path.insert(0, str(root))
    from modules.tools.tikhub import analyze
    print(json.dumps(analyze(json.loads(args.snapshot.read_text(encoding='utf-8'))), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__': raise SystemExit(main())
