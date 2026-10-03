#!/usr/bin/env python3
"""Require dashboard and skill maintenance to land together."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


SKILL_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_ROOT = SKILL_ROOT.parents[1]
MANIFEST = SKILL_ROOT / "references" / "dashboard-sync.json"
SCHEMA_VERSION = "dashboard-sync/v2"
TEXT_DIGEST_POLICY = "sha256-utf8-crlf-to-lf/v1"
RAW_DIGEST_POLICY = "sha256-raw-bytes/v1"
# This explicit allowlist contains UTF-8 text only, not dashboard image assets.
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
    DOMAIN_ROOT / "dashboard" / "transport-history.js",
    DOMAIN_ROOT / "transport_history.py",
    SKILL_ROOT / "SKILL.md",
    SKILL_ROOT / "references" / "decision-contract.md",
)


def digest(path: Path, *, policy: str = TEXT_DIGEST_POLICY) -> str:
    raw = path.read_bytes()
    if policy == TEXT_DIGEST_POLICY:
        text = raw.decode("utf-8")  # Strict validation; no universal-newline read.
        if "\x00" in text:
            raise ValueError(f"not a UTF-8 text file (NUL byte): {path}")
        raw = raw.replace(b"\r\n", b"\n")
    elif policy != RAW_DIGEST_POLICY:
        raise ValueError(f"unsupported digest policy: {policy}")
    return hashlib.sha256(raw).hexdigest()


def current_manifest(*, policy: str = TEXT_DIGEST_POLICY) -> dict[str, str]:
    return {
        path.relative_to(DOMAIN_ROOT).as_posix(): digest(path, policy=policy)
        for path in TRACKED
    }


def expected_manifest() -> tuple[dict[str, str], str]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    if "schema_version" not in payload:
        if set(payload) != {"sha256"}:
            raise ValueError("legacy manifest must contain only sha256; use --update to migrate")
        policy = RAW_DIGEST_POLICY
    else:
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {payload['schema_version']}")
        policy = payload.get("digest_policy")
        if policy != TEXT_DIGEST_POLICY:
            raise ValueError(f"unsupported digest_policy: {policy}")
        if set(payload) != {"schema_version", "digest_policy", "sha256"}:
            raise ValueError("unexpected manifest fields")
    expected = payload.get("sha256")
    tracked = {path.relative_to(DOMAIN_ROOT).as_posix() for path in TRACKED}
    if not isinstance(expected, dict) or set(expected) != tracked:
        raise ValueError("manifest file set mismatch (missing or extra tracked entries)")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in expected.values()):
        raise ValueError("manifest contains an invalid SHA-256 digest")
    return expected, policy


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--update", action="store_true")
    args = parser.parse_args()
    try:
        if args.update:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "digest_policy": TEXT_DIGEST_POLICY,
                "sha256": current_manifest(),
            }
            MANIFEST.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            print(f"updated {MANIFEST} ({SCHEMA_VERSION}; {TEXT_DIGEST_POLICY})")
            return 0
        expected, policy = expected_manifest()
        current = current_manifest(policy=policy)
        if expected != current:
            changed = ", ".join(path for path in current if current[path] != expected[path])
            print(f"dashboard/skill sync mismatch ({policy}): {changed}; review changes, then run --update")
            return 1
    except (OSError, ValueError) as error:
        print(f"dashboard/skill sync invalid: {error}")
        return 1
    print(f"dashboard/skill sync: valid ({policy})")
    if policy == RAW_DIGEST_POLICY:
        print("legacy raw-byte manifest; --update explicitly migrates to the portable text policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
