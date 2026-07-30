#!/usr/bin/env python3
"""Validate exact inventory identities before replenishment consumption."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


IDENTITY_FIELDS = ("seller_sku", "warehouse")
QUANTITY_FIELDS = ("stock", "available", "allocated", "frozen", "inbound")
TRUNCATION_MARKERS = ("…", "...", "****")


def validate_identity(value: Any, field: str) -> list[str]:
    if type(value) is not str or not value.strip():
        return [f"{field}: must be a nonempty built-in string"]
    normalized = value.strip()
    errors = [
        f"{field}: truncated or masked identifiers are forbidden"
        for marker in TRUNCATION_MARKERS
        if marker in normalized
    ]
    if normalized.endswith(("X", "x", "*")):
        errors.append(f"{field}: wildcard or synthetic identifiers are forbidden")
    return errors


def validate_record(record: Any, index: int) -> list[str]:
    if type(record) is not dict:
        return [f"records[{index}]: must be an object"]
    errors: list[str] = []
    for field in IDENTITY_FIELDS:
        errors.extend(
            f"records[{index}].{error}"
            for error in validate_identity(record.get(field), field)
        )
    for field in QUANTITY_FIELDS:
        value = record.get(field)
        if type(value) is not int or value < 0:
            errors.append(
                f"records[{index}].{field}: must be a nonnegative built-in int"
            )
    captured_at = record.get("captured_at")
    if type(captured_at) is not str or not captured_at.strip():
        errors.append(
            f"records[{index}].captured_at: must be a nonempty built-in string"
        )
    return errors


def validate_payload(payload: Any) -> list[str]:
    records = payload.get("records") if type(payload) is dict else payload
    if type(records) is not list:
        return ["payload: expected a list or an object containing a records list"]
    errors: list[str] = []
    for index, record in enumerate(records):
        errors.extend(validate_record(record, index))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    errors = validate_payload(payload)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("inventory snapshot identities: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
