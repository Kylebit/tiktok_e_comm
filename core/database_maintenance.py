"""Read-only SQLite health checks and WAL-safe online backups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from core.db import connect_readonly, db_path


@dataclass(frozen=True)
class DatabaseHealth:
    path: Path
    size_bytes: int
    wal_size_bytes: int
    shm_size_bytes: int
    journal_mode: str
    page_size: int
    page_count: int
    freelist_count: int
    user_version: int
    table_count: int
    index_count: int
    trigger_count: int
    row_counts: dict[str, int]
    quick_check: tuple[str, ...]
    integrity_check: tuple[str, ...] | None
    foreign_key_violation_count: int

    @property
    def ok(self) -> bool:
        integrity = self.integrity_check or self.quick_check
        return integrity == ("ok",) and self.foreign_key_violation_count == 0

    def payload(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "wal_size_bytes": self.wal_size_bytes,
            "shm_size_bytes": self.shm_size_bytes,
            "journal_mode": self.journal_mode,
            "page_size": self.page_size,
            "page_count": self.page_count,
            "freelist_count": self.freelist_count,
            "user_version": self.user_version,
            "object_counts": {
                "tables": self.table_count,
                "indexes": self.index_count,
                "triggers": self.trigger_count,
            },
            "row_counts": dict(self.row_counts),
            "quick_check": list(self.quick_check),
            "integrity_check": (
                list(self.integrity_check) if self.integrity_check is not None else None
            ),
            "foreign_key_violation_count": self.foreign_key_violation_count,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class DatabaseBackup:
    source_path: Path
    destination_path: Path
    created_at: datetime
    size_bytes: int
    sha256: str
    integrity_check: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "destination_path": str(self.destination_path),
            "created_at": self.created_at.isoformat(),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "integrity_check": list(self.integrity_check),
        }


def inspect_database(
    path: str | Path | None = None,
    *,
    full_integrity: bool = False,
) -> DatabaseHealth:
    """Inspect an existing database without creating or migrating anything."""
    source = Path(path) if path is not None else db_path()
    source = source.resolve()
    connection = connect_readonly(source)
    try:
        objects = connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
        tables = [row["name"] for row in objects if row["type"] == "table"]
        row_counts = {
            table: int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in tables
        }
        quick_check = tuple(row[0] for row in connection.execute("PRAGMA quick_check"))
        integrity_check = (
            tuple(row[0] for row in connection.execute("PRAGMA integrity_check"))
            if full_integrity
            else None
        )
        foreign_key_violations = sum(
            1 for _ in connection.execute("PRAGMA foreign_key_check")
        )
        return DatabaseHealth(
            path=source,
            size_bytes=source.stat().st_size,
            wal_size_bytes=_sidecar_size(source, "-wal"),
            shm_size_bytes=_sidecar_size(source, "-shm"),
            journal_mode=str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
            page_size=int(connection.execute("PRAGMA page_size").fetchone()[0]),
            page_count=int(connection.execute("PRAGMA page_count").fetchone()[0]),
            freelist_count=int(connection.execute("PRAGMA freelist_count").fetchone()[0]),
            user_version=int(connection.execute("PRAGMA user_version").fetchone()[0]),
            table_count=sum(1 for row in objects if row["type"] == "table"),
            index_count=sum(1 for row in objects if row["type"] == "index"),
            trigger_count=sum(1 for row in objects if row["type"] == "trigger"),
            row_counts=row_counts,
            quick_check=quick_check,
            integrity_check=integrity_check,
            foreign_key_violation_count=foreign_key_violations,
        )
    finally:
        connection.close()


def backup_database(
    destination: str | Path,
    *,
    source: str | Path | None = None,
) -> DatabaseBackup:
    """Create a verified SQLite online backup without overwriting a prior file."""
    source_path = (Path(source) if source is not None else db_path()).resolve()
    destination_path = Path(destination).resolve()
    if source_path == destination_path:
        raise ValueError("backup destination must differ from source database")
    if destination_path.exists():
        raise FileExistsError(f"backup destination already exists: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary basename bounded.  SQLite may append ``-journal``;
    # echoing a long destination name plus a full UUID can otherwise cross
    # the legacy Windows path limit even when the final backup path is valid.
    temporary = destination_path.with_name(
        f".db-backup-{uuid4().hex[:16]}.tmp"
    )
    source_connection = connect_readonly(source_path)
    destination_connection: sqlite3.Connection | None = None
    try:
        destination_connection = sqlite3.connect(temporary, timeout=30)
        destination_connection.execute("PRAGMA foreign_keys=ON")
        destination_connection.execute("PRAGMA busy_timeout=30000")
        source_connection.backup(destination_connection, pages=256)
        destination_connection.commit()
        integrity = tuple(
            row[0]
            for row in destination_connection.execute("PRAGMA integrity_check")
        )
        if integrity != ("ok",):
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        destination_connection.close()
        destination_connection = None
        os.replace(temporary, destination_path)
    finally:
        source_connection.close()
        if destination_connection is not None:
            destination_connection.close()
        if temporary.exists():
            temporary.unlink()
    return DatabaseBackup(
        source_path=source_path,
        destination_path=destination_path,
        created_at=datetime.now(timezone.utc),
        size_bytes=destination_path.stat().st_size,
        sha256=_file_sha256(destination_path),
        integrity_check=("ok",),
    )


def _sidecar_size(path: Path, suffix: str) -> int:
    sidecar = Path(f"{path}{suffix}")
    return sidecar.stat().st_size if sidecar.is_file() else 0


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
