#!/usr/bin/env python3
"""Validate or append one independently established pre-provider zero-write receipt."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared_platform.product_publication_run_reconciliations import (  # noqa: E402
    ProductPublicationRunReconciliationStore,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--execute", action="store_true",
                        help="append the validated receipt; omission performs a read-only preview")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if payload.pop("schema_version", None) != "product-publication-zero-write-reconciliation-input/v1":
        raise ValueError("input schema_version is invalid")
    store = ProductPublicationRunReconciliationStore(args.db)
    receipt = store.register(**payload) if args.execute else store.prepare(**payload)
    print(json.dumps({"persisted": args.execute, "receipt": receipt}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
