#!/usr/bin/env python3
"""Require dashboard and skill maintenance to land together."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_ROOT = SKILL_ROOT.parents[1]
MANIFEST = SKILL_ROOT / "references" / "dashboard-sync.json"
TRACKED = (
    DOMAIN_ROOT / "dashboard" / "README.md",
    DOMAIN_ROOT / "dashboard" / "app.js",
    DOMAIN_ROOT / "dashboard" / "data.js",
    DOMAIN_ROOT / "dashboard" / "inbound-batches.html",
    DOMAIN_ROOT / "dashboard" / "inbound-batches.js",
    DOMAIN_ROOT / "dashboard" / "inbound-plan.js",
    DOMAIN_ROOT / "dashboard" / "inbound-timeline.js",
    DOMAIN_ROOT / "dashboard" / "index.html",
    DOMAIN_ROOT / "dashboard" / "styles.css",
    SKILL_ROOT / "SKILL.md",
    SKILL_ROOT / "references" / "decision-contract.md",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_manifest() -> dict[str, str]:
    return {
        path.relative_to(DOMAIN_ROOT).as_posix(): digest(path)
        for path in TRACKED
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--update", action="store_true")
    args = parser.parse_args()
    current = current_manifest()
    if args.update:
        MANIFEST.write_text(
            json.dumps({"sha256": current}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"updated {MANIFEST}")
        return 0
    expected = json.loads(MANIFEST.read_text(encoding="utf-8")).get("sha256")
    if expected != current:
        print("dashboard/skill sync mismatch; update the skill, then run --update")
        return 1
    print("dashboard/skill sync: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
