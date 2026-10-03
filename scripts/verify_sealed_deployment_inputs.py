"""Read-only hash check for a reviewed deployment input seal.

This is an observation, never permission to stop or start a service. Pass the
manifest digest from an independent review, not one calculated by this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _digest(path: Path) -> tuple[str, bool]:
    before = path.stat()
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    after = path.stat()
    stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    )
    return hasher.hexdigest(), stable


def verify(manifest_path: Path, expected_manifest_sha256: str) -> dict[str, object]:
    result: dict[str, object] = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "HOLD_INPUT_DRIFT",
        "checked_count": 0,
        "drifts": [],
    }
    drifts: list[dict[str, str]] = result["drifts"]  # type: ignore[assignment]
    if not SHA256.fullmatch(expected_manifest_sha256):
        drifts.append({"reason": "INVALID_EXPECTED_MANIFEST_DIGEST"})
        return result
    try:
        before = manifest_path.stat()
        manifest_bytes = manifest_path.read_bytes()
        after = manifest_path.stat()
    except OSError as exc:
        drifts.append({"reason": "MANIFEST_UNREADABLE", "detail": type(exc).__name__})
        return result
    actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    )
    result["manifest_sha256"] = actual_manifest_sha256
    if not stable or actual_manifest_sha256 != expected_manifest_sha256:
        drifts.append({"reason": "MANIFEST_DRIFT"})
        return result
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            raise ValueError("files must be a nonempty object")
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        drifts.append({"reason": "INVALID_MANIFEST", "detail": type(exc).__name__})
        return result

    for name, expected in sorted(files.items()):
        result["checked_count"] = int(result["checked_count"]) + 1
        if not isinstance(name, str) or not isinstance(expected, str) or not SHA256.fullmatch(expected):
            drifts.append({"path": str(name), "reason": "INVALID_ENTRY"})
            continue
        target = Path(name)
        if not target.is_absolute():
            drifts.append({"path": name, "reason": "PATH_NOT_ABSOLUTE"})
            continue
        try:
            actual, stable = _digest(target)
        except OSError as exc:
            drifts.append({"path": name, "reason": "UNREADABLE", "detail": type(exc).__name__})
            continue
        if not stable:
            drifts.append({"path": name, "reason": "CHANGED_DURING_READ"})
        elif actual != expected:
            drifts.append({"path": name, "reason": "SHA256_MISMATCH", "actual_sha256": actual})
    if not drifts:
        result["decision"] = "PASS_READONLY_INPUTS_MATCH"
    return result


def main() -> int:
    if not __debug__:
        print(json.dumps({"decision": "HOLD_OPTIMIZED_PYTHON"}))
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    result = verify(args.manifest, args.expected_manifest_sha256)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"] == "PASS_READONLY_INPUTS_MATCH" else 2


if __name__ == "__main__":
    sys.exit(main())
