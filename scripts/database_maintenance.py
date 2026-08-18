"""Read-only health checks and verified online backups for the main SQLite DB."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database_maintenance import backup_database, inspect_database
from domains.product_operations.catalog_database_audit import audit_catalog_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite database maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="run a side-effect-free health check")
    check.add_argument("--database", type=Path, default=ROOT / "data" / "shop.db")
    check.add_argument("--full", action="store_true", help="run full integrity_check")
    backup = subparsers.add_parser("backup", help="create a verified online backup")
    backup.add_argument("--database", type=Path, default=ROOT / "data" / "shop.db")
    backup.add_argument("--output", type=Path)
    quality = subparsers.add_parser(
        "quality", help="run the side-effect-free catalog quality audit"
    )
    quality.add_argument("--database", type=Path, default=ROOT / "data" / "shop.db")
    quality.add_argument(
        "--fail-on-review",
        action="store_true",
        help="return exit code 2 when the report contains review blockers",
    )
    args = parser.parse_args(argv)

    if args.command == "check":
        result = inspect_database(args.database, full_integrity=args.full)
        print(json.dumps(result.payload(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 2
    if args.command == "quality":
        result = audit_catalog_database(args.database)
        print(json.dumps(result.payload(), ensure_ascii=False, indent=2))
        return 2 if args.fail_on_review and result.needs_review else 0

    output = args.output or (
        ROOT
        / "backups"
        / "database"
        / f"shop-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    )
    result = backup_database(output, source=args.database)
    print(json.dumps(result.payload(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
