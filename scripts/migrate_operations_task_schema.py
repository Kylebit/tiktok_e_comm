"""Explicit, fail-closed migration of the four missing operations schema objects.

This tool never starts a service or worker. Snapshot uses SQLite online backup;
apply and rollback require an exact, unchanged source identity and a write lock.
Receipts contain hashes/counts only, never task payloads or credentials.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from shared_platform.workbench_engine import SCHEMA
from shared_platform.workbench_store import _SCHEMA as BASE_SCHEMA


ADDED = (
    ("table", "workbench_profit_requests"),
    ("trigger", "workbench_profit_requests_no_update"),
    ("trigger", "workbench_profit_requests_no_delete"),
    ("table", "workbench_review_identity"),
)


def _path(value: str | Path, *, exists: bool) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("all paths must be absolute")
    if exists and not path.is_file():
        raise ValueError(f"database or receipt missing: {path}")
    return path.resolve(strict=exists)


def _connect(database: Path, *, writable: bool) -> sqlite3.Connection:
    # Path.as_uri percent-encodes '#' and '?' correctly; string-built file: URIs do not.
    uri = database.as_uri() + ("?mode=rw" if writable else "?mode=ro")
    conn = sqlite3.connect(uri, uri=True, timeout=2.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=2000")
    actual = conn.execute("PRAGMA database_list").fetchone()[2]
    if Path(actual).resolve(strict=True) != database:
        conn.close()
        raise ValueError("SQLite opened a different database path")
    return conn


def _sql(sql: str) -> str:
    return " ".join(sql.split()).casefold()


def _schema(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {(kind, name): _sql(sql) for kind, name, sql in conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
    )}


def _desired(schema: str) -> dict[tuple[str, str], str]:
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript(schema)
        return {(kind, name): sql for kind, name, sql in conn.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        )}


DESIRED = _desired(SCHEMA)
BASE_DESIRED = _desired(BASE_SCHEMA)


def _file_id(path: Path) -> list[int]:
    stat = path.stat()
    if not isinstance(stat.st_dev, int) or not isinstance(stat.st_ino, int) or stat.st_dev <= 0 or stat.st_ino <= 0:
        raise ValueError("database file identity unavailable on this filesystem")
    return [stat.st_dev, stat.st_ino]


def _value(value):
    if isinstance(value, bytes):
        return {"blob_hex": value.hex()}
    return value


def _fingerprint(conn: sqlite3.Connection) -> dict:
    """Order-independent logical hash of every table and schema object."""
    objects = _schema(conn)
    tables = {}
    for (kind, name) in objects:
        if kind != "table":
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        rows = [json.dumps([_value(value) for value in row], ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
                for row in conn.execute(f"SELECT * FROM {quoted}")]
        rows.sort()
        digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
        tables[name] = {"rows": len(rows), "sha256": digest}
    body = {"objects": {f"{kind}:{name}": objects[(kind, name)] for kind, name in sorted(objects)},
            "tables": tables, "user_version": conn.execute("PRAGMA user_version").fetchone()[0]}
    body["logical_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return body


def _integrity(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity_check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("SQLite foreign_key_check failed")


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new_json(path: Path, body: dict) -> None:
    if path.exists():
        raise ValueError(f"receipt already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(body, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _replace_json(path: Path, body: dict) -> None:
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    _write_new_json(temporary, body)
    os.replace(temporary, path)


def _exact_source(database: str | Path, expected: str | Path) -> Path:
    path = _path(database, exists=True)
    if path != _path(expected, exists=True):
        raise ValueError("database path differs from approved exact source")
    return path


def snapshot(database: str | Path, *, expected: str | Path, backup: str | Path,
             receipt: str | Path) -> dict:
    """Online WAL-consistent read from source; creates only unique private files."""
    source = _exact_source(database, expected)
    backup = _path(backup, exists=False)
    receipt = _path(receipt, exists=False)
    if backup == source or receipt == source or backup == receipt or backup.exists() or receipt.exists():
        raise ValueError("snapshot output must be new and distinct from source")
    backup.parent.mkdir(parents=True, exist_ok=True)
    source_file_id = _file_id(source)
    with closing(_connect(source, writable=False)) as reader:
        _integrity(reader)
        source_before = _fingerprint(reader)
        with closing(sqlite3.connect(backup)) as copy:
            reader.backup(copy)
            copy.commit()
        source_after = _fingerprint(reader)
    if source_before["logical_sha256"] != source_after["logical_sha256"]:
        raise ValueError("source changed during online backup; retain output as evidence, retry fresh")
    with closing(_connect(backup, writable=False)) as copy:
        _integrity(copy)
        copied = _fingerprint(copy)
    if copied != source_before:
        raise ValueError("online backup did not match source logical snapshot")
    body = {"schema": "orbit-operations-schema-snapshot/v1", "source": str(source),
            "source_file_id": source_file_id,
            "backup": str(backup), "backup_sha256": _file_sha(backup),
            "logical": copied, "source_write_performed": False}
    _write_new_json(receipt, body)
    return body


def _load_snapshot(receipt: str | Path, database: Path) -> dict:
    body = json.loads(_path(receipt, exists=True).read_text(encoding="utf-8"))
    if body.get("schema") != "orbit-operations-schema-snapshot/v1":
        raise ValueError("unsupported snapshot receipt")
    if _path(body["source"], exists=True) != database:
        raise ValueError("snapshot source identity differs")
    if body.get("source_file_id") != _file_id(database):
        raise ValueError("source database file identity changed")
    backup = _path(body["backup"], exists=True)
    if _file_sha(backup) != body["backup_sha256"]:
        raise ValueError("snapshot bytes changed")
    with closing(_connect(backup, writable=False)) as copy:
        _integrity(copy)
        if _fingerprint(copy) != body["logical"]:
            raise ValueError("snapshot receipt and backup content differ")
    return body


def _preflight_schema(conn: sqlite3.Connection) -> None:
    actual = _schema(conn)
    expected = {**BASE_DESIRED, **DESIRED}
    expected_names = set(expected) - set(ADDED)
    extras = set(actual) - expected_names
    if extras:
        raise ValueError(f"unregistered schema object: {sorted(extras)}")
    for key in ADDED:
        if key in actual:
            raise ValueError(f"migration object already exists: {key}")
    for key in expected_names:
        desired_sql = expected[key]
        if actual.get(key) != _sql(desired_sql):
            raise ValueError(f"existing workbench schema differs: {key}")


def migrate(database: str | Path, *, expected: str | Path, snapshot_receipt: str | Path,
            receipt: str | Path, fail_after: int | None = None) -> dict:
    source = _exact_source(database, expected)
    snapshot_body = _load_snapshot(snapshot_receipt, source)
    receipt = _path(receipt, exists=False)
    if receipt.exists() or receipt == source:
        raise ValueError("post-migration receipt must be new")
    with closing(_connect(source, writable=True)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            before = _fingerprint(conn)
            if before != snapshot_body["logical"]:
                raise ValueError("stable task ledger changed since verified snapshot")
            _integrity(conn)
            _preflight_schema(conn)
            for index, key in enumerate(ADDED, start=1):
                # Never use executescript here: Python sqlite3 can commit a pending txn.
                conn.execute(DESIRED[key])
                if fail_after == index:
                    raise RuntimeError(f"injected interruption after DDL {index}")
            actual = _schema(conn)
            for key in ADDED:
                if actual.get(key) != _sql(DESIRED[key]):
                    raise ValueError(f"created schema differs: {key}")
            after = _fingerprint(conn)
            for name, state in before["tables"].items():
                if after["tables"].get(name) != state:
                    raise ValueError(f"preexisting table changed: {name}")
            for key in ADDED:
                if key[0] == "table" and after["tables"][key[1]]["rows"] != 0:
                    raise ValueError("new migration table is unexpectedly nonempty")
            _integrity(conn)
            body = {"schema": "orbit-operations-schema-migration/v1", "source": str(source),
                    "source_file_id": _file_id(source),
                    "snapshot_receipt": str(_path(snapshot_receipt, exists=True)),
                    "before_logical_sha256": before["logical_sha256"],
                    "after": after, "added": [f"{kind}:{name}" for kind, name in ADDED],
                    "worker_enabled": False, "provider_call_performed": False,
                    "commit_state": "prepared"}
            # Durable intent before COMMIT lets an interrupted caller distinguish
            # precommit rollback from committed DDL without replaying anything.
            _write_new_json(receipt, body)
            if fail_after == 5:
                raise RuntimeError("injected interruption before COMMIT")
            conn.commit()
            if fail_after == 6:
                raise RuntimeError("injected interruption after COMMIT before receipt finalization")
        except BaseException:
            conn.rollback()
            raise
    body["commit_state"] = "committed"
    _replace_json(receipt, body)
    return body


def reconcile(database: str | Path, *, expected: str | Path,
              migration_receipt: str | Path, audit_receipt: str | Path | None = None) -> dict:
    """Resolve an interrupted prepared receipt using read-only DB facts."""
    source = _exact_source(database, expected)
    path = _path(migration_receipt, exists=True)
    previous = json.loads(path.read_text(encoding="utf-8"))
    if previous.get("schema") != "orbit-operations-schema-migration/v1" or previous.get("source") != str(source):
        raise ValueError("migration receipt does not bind this source")
    if previous.get("source_file_id") != _file_id(source):
        raise ValueError("source database file identity changed")
    if previous.get("commit_state") != "prepared":
        raise ValueError("migration receipt is not awaiting reconciliation")
    with closing(_connect(source, writable=False)) as conn:
        current = _fingerprint(conn)
    if current == previous["after"]:
        state = "committed"
        classification = "ALREADY_APPLIED"
    elif current["logical_sha256"] == previous["before_logical_sha256"]:
        state = "aborted"
        classification = "NOT_APPLIED"
    else:
        state = "prepared"
        classification = "UNKNOWN_REQUIRES_MANUAL_RECONCILIATION"
    audit = {"schema": "orbit-operations-schema-migration-reconciliation/v1",
             "source": str(source), "source_file_id": _file_id(source),
             "migration_receipt": str(path), "observed_logical_sha256": current["logical_sha256"],
             "classification": classification, "database_write_performed": False,
             "ddl_replayed": False}
    if audit_receipt is not None:
        _write_new_json(_path(audit_receipt, exists=False), audit)
    if state != "prepared":
        previous["commit_state"] = state
        _replace_json(path, previous)
    previous["classification"] = classification
    return previous


def rollback(database: str | Path, *, expected: str | Path, migration_receipt: str | Path,
             receipt: str | Path, fail_after_commit: bool = False) -> dict:
    """Down-migrate only if *no* post-migration task/event/lock change occurred."""
    source = _exact_source(database, expected)
    previous = json.loads(_path(migration_receipt, exists=True).read_text(encoding="utf-8"))
    if (previous.get("schema") != "orbit-operations-schema-migration/v1"
            or previous.get("source") != str(source)
            or previous.get("commit_state") != "committed"):
        raise ValueError("migration receipt does not bind this source")
    if previous.get("source_file_id") != _file_id(source):
        raise ValueError("source database file identity changed")
    receipt = _path(receipt, exists=False)
    if receipt.exists() or receipt == source:
        raise ValueError("rollback receipt must be new")
    with closing(_connect(source, writable=True)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = _fingerprint(conn)
            if current["logical_sha256"] == previous["before_logical_sha256"]:
                raise ValueError("database already rolled back; reconcile existing rollback receipt")
            if current != previous["after"]:
                raise ValueError("ledger changed after migration; rollback would lose or misstate new work")
            for kind, name in ADDED:
                if kind == "table" and current["tables"][name]["rows"] != 0:
                    raise ValueError("new migration table contains data")
            for kind, name in reversed(ADDED):
                quoted = '"' + name.replace('"', '""') + '"'
                conn.execute(f"DROP {kind.upper()} {quoted}")
            restored = _fingerprint(conn)
            if restored["logical_sha256"] != previous["before_logical_sha256"]:
                raise ValueError("down-migration did not restore logical baseline")
            _integrity(conn)
            body = {"schema": "orbit-operations-schema-rollback/v1", "source": str(source),
                    "source_file_id": _file_id(source),
                    "migration_receipt": str(_path(migration_receipt, exists=True)),
                    "pre_rollback_logical_sha256": current["logical_sha256"],
                    "restored_logical_sha256": restored["logical_sha256"],
                    "source_overwritten_from_backup": False, "commit_state": "prepared"}
            _write_new_json(receipt, body)
            conn.commit()
            if fail_after_commit:
                raise RuntimeError("injected interruption after rollback COMMIT")
        except BaseException:
            conn.rollback()
            raise
    body["commit_state"] = "committed"
    _replace_json(receipt, body)
    return body


def reconcile_rollback(database: str | Path, *, expected: str | Path,
                       rollback_receipt: str | Path, audit_receipt: str | Path) -> dict:
    """Classify a prepared rollback from read-only DB facts; emit immutable audit."""
    source = _exact_source(database, expected)
    previous = json.loads(_path(rollback_receipt, exists=True).read_text(encoding="utf-8"))
    if (previous.get("schema") != "orbit-operations-schema-rollback/v1"
            or previous.get("source") != str(source)
            or previous.get("commit_state") != "prepared"):
        raise ValueError("rollback receipt is not prepared for reconciliation")
    if previous.get("source_file_id") != _file_id(source):
        raise ValueError("source database file identity changed")
    with closing(_connect(source, writable=False)) as conn:
        current = _fingerprint(conn)["logical_sha256"]
    if current == previous["restored_logical_sha256"]:
        classification = "ALREADY_ROLLED_BACK"
    elif current == previous["pre_rollback_logical_sha256"]:
        classification = "ROLLBACK_NOT_APPLIED"
    else:
        classification = "UNKNOWN_REQUIRES_MANUAL_RECONCILIATION"
    result = {"schema": "orbit-operations-schema-rollback-reconciliation/v1",
              "source": str(source), "source_file_id": _file_id(source),
              "rollback_receipt": str(_path(rollback_receipt, exists=True)),
              "observed_logical_sha256": current, "classification": classification,
              "database_write_performed": False, "ddl_replayed": False}
    _write_new_json(_path(audit_receipt, exists=False), result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("snapshot", "migrate", "rollback", "reconcile", "reconcile-rollback"):
        command = sub.add_parser(action)
        command.add_argument("--database", required=True)
        command.add_argument("--expected-source", required=True)
        if action not in {"reconcile", "reconcile-rollback"}:
            command.add_argument("--receipt", required=True)
        if action == "snapshot":
            command.add_argument("--backup", required=True)
        elif action == "migrate":
            command.add_argument("--snapshot-receipt", required=True)
        elif action in {"rollback", "reconcile"}:
            command.add_argument("--migration-receipt", required=True)
            if action == "reconcile":
                command.add_argument("--audit-receipt", required=True)
        else:
            command.add_argument("--rollback-receipt", required=True)
            command.add_argument("--audit-receipt", required=True)
    args = parser.parse_args()
    if args.action == "snapshot":
        result = snapshot(args.database, expected=args.expected_source,
                          backup=args.backup, receipt=args.receipt)
    elif args.action == "migrate":
        result = migrate(args.database, expected=args.expected_source,
                         snapshot_receipt=args.snapshot_receipt, receipt=args.receipt)
    elif args.action == "rollback":
        result = rollback(args.database, expected=args.expected_source,
                          migration_receipt=args.migration_receipt, receipt=args.receipt)
    elif args.action == "reconcile":
        result = reconcile(args.database, expected=args.expected_source,
                           migration_receipt=args.migration_receipt,
                           audit_receipt=args.audit_receipt)
    else:
        result = reconcile_rollback(args.database, expected=args.expected_source,
                                    rollback_receipt=args.rollback_receipt,
                                    audit_receipt=args.audit_receipt)
    print(json.dumps({"ok": True, "schema": result["schema"], "source": result["source"],
                      "commit_state": result.get("commit_state"),
                      "classification": result.get("classification")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
