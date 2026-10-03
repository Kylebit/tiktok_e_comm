"""Start the pinned Orbit maintenance website without background execution."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    args = parser.parse_args()
    from shared_platform.operations_launch import serve
    serve(json.loads(args.deployment.read_text(encoding='utf-8')), ROOT)

if __name__ == '__main__':
    main()
