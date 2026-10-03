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
from domains.product_operations.catalog_database_audit import audit_catalog_database, failed_catalog_audit


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
    quality.add_argument("--cost-view", choices=("current", "historical"), default="current",
                         help="current SKU projection or original dated cost observations")
    quality.add_argument("--identity-evidence", type=Path, help="explicit alias/history reference JSON; approval authority is not verified")
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
        try:
            evidence = json.loads(args.identity_evidence.read_text(encoding="utf-8")) if args.identity_evidence else None
        except (OSError, ValueError):
            result = failed_catalog_audit(args.database, "review_evidence_read_failed")
        else:
            result = audit_catalog_database(args.database, review_evidence=evidence,
                                            cost_view=args.cost_view)
        payload = result.payload()
        if args.identity_evidence:
            payload["source"]["review_evidence_path"] = str(args.identity_evidence.resolve())
        payload["cli_exit_policy"] = "fail_on_review" if args.fail_on_review else "compatibility"
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        if result.status == "check_failed":
            return 3
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
