"""Inspect/validate/reviewed apply for a newly created private COMMON ledger.

No default database, HTTP, provider, environment path or production option.
Input attachment files must be direct regular children of the packet folder.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared_platform.common_offer_authority_store import (
    CommonAuthorityBlocked, PrivateCommonAuthorityStore, MAX_ATTACHMENT_BYTES, _json, _safe_path,
    review_private_import,
)


def _read_bounded(path):
    path = _safe_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_ATTACHMENT_BYTES:
        raise CommonAuthorityBlocked("IMPORT_FILE_SIZE_INVALID")
    with path.open("rb") as file:
        opened = os.fstat(file.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
            raise CommonAuthorityBlocked("IMPORT_FILE_CHANGED")
        raw = file.read(MAX_ATTACHMENT_BYTES + 1)
    after = _safe_path(path).stat()
    if (len(raw) != before.st_size or len(raw) > MAX_ATTACHMENT_BYTES
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)):
        raise CommonAuthorityBlocked("IMPORT_FILE_CHANGED")
    return raw


def _review(args):
    path = _safe_path(Path(args.packet).absolute())
    if not path.is_file():
        raise CommonAuthorityBlocked("PACKET_NOT_REGULAR_FILE")
    raw = _read_bounded(path)
    packet = _json(raw)
    manifest = packet.get("attachment_manifest")
    if type(manifest) is not dict:
        raise CommonAuthorityBlocked("ATTACHMENT_SET_INVALID")
    attachments = {}
    total = 0
    for name in manifest:
        if type(name) is not str or not name or Path(name).name != name or any(c in name for c in ("/", "\\", ":")):
            raise CommonAuthorityBlocked("ATTACHMENT_REF_NOT_LOCAL_BASENAME")
        source = _safe_path(path.parent / name)
        if not source.is_file():
            raise CommonAuthorityBlocked("ATTACHMENT_NOT_REGULAR_FILE")
        size = source.stat().st_size
        total += size
        if total > 4 * MAX_ATTACHMENT_BYTES:
            raise CommonAuthorityBlocked("ATTACHMENT_BUDGET_EXCEEDED")
        attachments[name] = _read_bounded(source)
    return review_private_import(raw, attachments,
                                expected_packet_digest=args.expected_packet_sha256,
                                review_ref=args.review_ref)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    init = command.add_parser("init-private", help="exclusive new directory only")
    init.add_argument("--private-root", required=True)
    inspect = command.add_parser("inspect", help="query-only; never migrates")
    inspect.add_argument("--private-root", required=True)
    for name in ("validate", "apply-private"):
        sub = command.add_parser(name)
        sub.add_argument("--packet", required=True)
        sub.add_argument("--expected-packet-sha256", required=True)
        sub.add_argument("--review-ref", required=True)
        if name == "apply-private":
            sub.add_argument("--private-root", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init-private":
            store = PrivateCommonAuthorityStore.create(Path(args.private_root))
            result = store.migrate()
        elif args.command == "inspect":
            result = PrivateCommonAuthorityStore.open(Path(args.private_root)).inspect()
        else:
            reviewed = _review(args)
            if args.command == "validate":
                result = {"status": "VALIDATED_REVIEWED_PRIVATE_FIXTURE", "execution_authority": False,
                          "real_source_verified": False, "packet_digest": reviewed.expected_packet_digest}
            else:
                result = PrivateCommonAuthorityStore.open(Path(args.private_root)).import_reviewed(reviewed)
    except (CommonAuthorityBlocked, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "BLOCKED", "error": str(error), "execution_authority": False}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
