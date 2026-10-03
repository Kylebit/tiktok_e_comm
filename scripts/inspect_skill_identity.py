"""Print a read-only seven-Skill source and personal-install identity snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared_platform.skill_identity import inspect_skill_identities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=ROOT)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--summary", action="store_true", help="omit per-file hashes from stdout")
    args = parser.parse_args()
    result = inspect_skill_identities(root=args.runtime_root, installed_root=args.installed_root)
    if args.summary:
        for row in result["skills"].values():
            for version in (row["project"], row["installed"]):
                if "manifest" in version:
                    version["manifest"].pop("files", None)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
