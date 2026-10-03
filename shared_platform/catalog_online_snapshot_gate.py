"""Isolated catalog backup evidence; never grants formal JIT or execution authority.

This module is deliberately not wired into the web service or deployment runner.
Its SQLite connections are read-only at the SQL layer, but SQLite may update
WAL shared-memory coordination bytes. Use only on synthetic data until a
separately reviewed formal JIT plan authorizes the exact source and outputs.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import tempfile
import time


class SnapshotGateHold(ValueError):
    """Fail-closed classification. A partial private backup is retained."""


def _hold(code: str) -> None:
    raise SnapshotGateHold(code)


def _deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _hold("SNAPSHOT_TIME_BUDGET_EXCEEDED")


def _sha(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            _deadline(deadline)
            digest.update(chunk)
    return digest.hexdigest()


def _file(path: Path, deadline: float) -> dict:
    if path.is_symlink():
        _hold("SOURCE_SYMLINK_SIDECAR")
    if not path.is_file():
        return {"exists": False}
    before = path.stat()
    if before.st_ino == 0:
        _hold("SOURCE_FILE_ID_UNAVAILABLE")
    result = {"exists": True, "file_id": [before.st_dev, before.st_ino],
              "size": before.st_size, "mtime_ns": before.st_mtime_ns,
              "sha256": _sha(path, deadline)}
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        _hold("SOURCE_CHANGED_DURING_FILE_SAMPLE")
    return result


def _wal_index_header(shm_header: bytes, main_header: bytes, main_size: int) -> dict:
    """Validate the two WAL-index header copies in native byte order.

    WAL is empty here, so salt/frame checksums cannot be compared with a WAL
    header. This does not prove reader absence or validate unused hash slots.
    """
    if len(shm_header) != 136:
        _hold("SHM_HEADER_TRUNCATED")
    if shm_header[:48] != shm_header[48:96]:
        _hold("SHM_HEADER_COPIES_DIFFER")
    order = "<" if sys.byteorder == "little" else ">"
    u32 = lambda offset: struct.unpack_from(order + "I", shm_header, offset)[0]
    if u32(0) != 3007000 or shm_header[4:8] != b"\0" * 4:
        _hold("SHM_HEADER_VERSION_OR_PADDING_INVALID")
    if shm_header[12] != 1 or shm_header[13] not in (0, 1):
        _hold("SHM_HEADER_FLAGS_INVALID")
    words = struct.unpack_from(order + "10I", shm_header)
    first = second = 0
    for index in range(0, len(words), 2):
        first = (first + words[index] + second) & 0xffffffff
        second = (second + words[index + 1] + first) & 0xffffffff
    if (first, second) != struct.unpack_from(order + "2I", shm_header, 40):
        _hold("SHM_HEADER_CHECKSUM_INVALID")
    stored_page_size = struct.unpack_from(order + "H", shm_header, 14)[0]
    mx_frame, n_page = u32(16), u32(20)
    main_page_size = int.from_bytes(main_header[16:18], "big")
    if main_page_size == 1:
        main_page_size = 65536
    if (main_page_size < 512 or main_page_size > 65536
            or main_page_size & (main_page_size - 1)
            or main_size % main_page_size):
        _hold("SOURCE_MAIN_PAGE_SIZE_INVALID")
    # SQLite may initialize an empty WAL-index without a committed WAL frame;
    # in that case szPage and nPage remain zero even for a nonempty main DB.
    page_size_bound = stored_page_size != 0
    if page_size_bound:
        if stored_page_size not in {1, 512, 1024, 2048, 4096, 8192, 16384, 32768}:
            _hold("SHM_HEADER_PAGE_SIZE_INVALID")
        page_size = (stored_page_size & 0xfe00) + ((stored_page_size & 1) << 16)
        if (page_size < 512 or page_size > 65536 or page_size & (page_size - 1)
                or page_size != main_page_size or main_size % page_size
                or n_page != main_size // page_size):
            _hold("SHM_HEADER_PAGE_SIZE_INVALID")
    elif n_page != 0 or mx_frame != 0:
        _hold("SHM_HEADER_PAGE_SIZE_INVALID")
    else:
        page_size = None
    n_backfill, n_backfill_attempted = u32(96), u32(128)
    if (mx_frame != 0 or n_backfill > mx_frame or n_backfill_attempted > mx_frame
            or u32(100) != 0):
        _hold("SHM_HEADER_FRAME_OR_PAGE_COUNTS_INVALID")
    return {"version": 3007000, "copies_identical": True,
            "native_byte_order": sys.byteorder, "header_checksum_verified": True,
            "salt_and_frame_checksum_unbound_to_empty_wal": True,
            "page_size": page_size, "page_size_bound_to_main": page_size_bound,
            "database_pages": n_page,
            "mxFrame_copies": [mx_frame, mx_frame],
            "nBackfill": n_backfill, "nBackfillAttempted": n_backfill_attempted}


def _sample(source: Path, deadline: float, max_source_bytes: int) -> dict:
    result = {suffix or "main": _file(Path(str(source) + suffix), deadline)
              for suffix in ("", "-wal", "-shm", "-journal")}
    main, wal, shm = result["main"], result["-wal"], result["-shm"]
    if not main["exists"] or main["size"] <= 100 or main["size"] > max_source_bytes:
        _hold("SOURCE_MAIN_MISSING_OR_OVER_BUDGET")
    with source.open("rb") as stream:
        header = stream.read(100)
    if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x02\x02":
        _hold("SOURCE_NOT_SQLITE_WAL")
    if result["-journal"]["exists"]:
        _hold("JOURNAL_SIDECAR_PRESENT")
    if not wal["exists"] or wal["size"] != 0:
        _hold("WAL_NOT_EMPTY")
    if not shm["exists"] or shm["size"] != 32768:
        _hold("SHM_NOT_EXPECTED_32K")
    with Path(str(source) + "-shm").open("rb") as stream:
        shm_header = stream.read(136)
    result["wal_index"] = _wal_index_header(shm_header, header, main["size"])
    return result


def _connect_readonly(path: Path, deadline: float) -> sqlite3.Connection:
    _deadline(deadline)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                 timeout=1.0, isolation_level=None)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=1000")
        connection.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 1000)
        opened = connection.execute("PRAGMA database_list").fetchone()[2]
        if Path(opened).resolve(strict=True) != path:
            _hold("SQLITE_SOURCE_PATH_MISMATCH")
        return connection
    except BaseException:
        connection.close()
        raise


def _backup_sidecar_occupied(root: Path, backup: Path) -> bool:
    return any(entry.name.startswith(backup.name + "-") for entry in root.iterdir())


def _unexpected_backup_sidecars(root: Path, backup: Path) -> set[str]:
    allowed = {backup.name + "-wal", backup.name + "-shm"}
    return {entry.name for entry in root.iterdir()
            if entry.name.startswith(backup.name + "-") and entry.name not in allowed}


def _cell(value):
    if value is None:
        return ["null"]
    if isinstance(value, bytes):
        return ["blob", len(value), hashlib.sha256(value).hexdigest()]
    if isinstance(value, float):
        return ["real", value.hex()]
    if isinstance(value, int):
        return ["int", value]
    return ["text", value]


def _logical_in_transaction(connection: sqlite3.Connection, deadline: float, max_rows: int) -> dict:
    _deadline(deadline)
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        _hold("SQLITE_INTEGRITY_FAILED")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        _hold("SQLITE_FOREIGN_KEY_FAILED")
    objects = [(kind, name, sql) for kind, name, sql in connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]
    schema_hash = hashlib.sha256(json.dumps(objects, ensure_ascii=False,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    tables = {}
    total_rows = 0
    for kind, name, _ in objects:
        if kind != "table":
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        row_digests = []
        for row in connection.execute(f"SELECT * FROM {quoted}"):
            _deadline(deadline)
            total_rows += 1
            if total_rows > max_rows:
                _hold("ROW_BUDGET_EXCEEDED")
            payload = json.dumps([_cell(value) for value in row], ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8")
            row_digests.append(hashlib.sha256(payload).digest())
        row_digests.sort()
        digest = hashlib.sha256()
        for row_digest in row_digests:
            digest.update(row_digest)
        tables[name] = {"rows": len(row_digests), "sha256": digest.hexdigest()}
    body = {"schema_sha256": schema_hash, "tables": tables,
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0]}
    body["logical_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return body


def _logical(connection: sqlite3.Connection, deadline: float, max_rows: int) -> dict:
    # Every table and integrity result must describe one SQLite read snapshot.
    connection.execute("BEGIN")
    try:
        return _logical_in_transaction(connection, deadline, max_rows)
    finally:
        connection.execute("ROLLBACK")


def create_catalog_snapshot_evidence(
    *, source: str | Path, expected_source: str | Path, private_root: str | Path,
    backup: str | Path, receipt: str | Path, expected_main_sha256: str,
    expected_shm_sha256: str, max_seconds: float = 30.0,
    max_source_bytes: int = 128 * 1024 * 1024, max_rows: int = 250_000,
    quiet_seconds: float = 0.05,
) -> dict:
    """Create a private technical proof, always returning policy HOLD.

    Formal use requires a separately reviewed JIT policy/runner. This function
    cannot clear a nonempty-sidecar hold and is never called by the web server.
    """
    if not (0 < max_seconds <= 120 and 0 < max_source_bytes <= 128 * 1024 * 1024
            and 0 <= max_rows <= 250_000 and 0 <= quiet_seconds <= 2):
        _hold("INVALID_BUDGET")
    deadline = time.monotonic() + max_seconds
    started_at_utc = datetime.now(timezone.utc).isoformat()
    if Path(source).is_symlink():
        _hold("SOURCE_SYMLINK_SIDECAR")
    source_path = Path(source).resolve(strict=True)
    expected_path = Path(expected_source).resolve(strict=True)
    if Path(private_root).is_symlink():
        _hold("PRIVATE_OUTPUT_NOT_NEW_OR_EXACT")
    root = Path(private_root).resolve(strict=False)
    backup_path = Path(backup).resolve(strict=False)
    receipt_path = Path(receipt).resolve(strict=False)
    if source_path != expected_path:
        _hold("SOURCE_IDENTITY_MISMATCH")
    if (root == source_path.parent or root.is_relative_to(source_path.parent)
            or source_path.parent.is_relative_to(root)):
        _hold("PRIVATE_ROOT_OVERLAPS_SOURCE")
    # The destination directory must not exist. Atomic mkdir later provides a
    # fresh namespace, so no old SQLite sidecar can be opened or deleted.
    if (not root.parent.is_dir() or os.path.lexists(root)
            or backup_path.parent != root or receipt_path.parent != root
            or backup_path == receipt_path or os.path.lexists(backup_path)
            or receipt_path.name.startswith(backup_path.name + "-")
            or os.path.lexists(receipt_path)):
        _hold("PRIVATE_OUTPUT_NOT_NEW_OR_EXACT")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_main_sha256) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_shm_sha256):
        _hold("INVALID_EXPECTED_SHA")
    try:
        source_before = _sample(source_path, deadline, max_source_bytes)
        if source_before["main"]["sha256"] != expected_main_sha256:
            _hold("EXPECTED_MAIN_SHA_MISMATCH")
        if source_before["-shm"]["sha256"] != expected_shm_sha256:
            _hold("EXPECTED_SHM_SHA_MISMATCH")
        time.sleep(quiet_seconds)
        if _sample(source_path, deadline, max_source_bytes) != source_before:
            _hold("SOURCE_NOT_QUIET_BEFORE_BACKUP")
        with closing(_connect_readonly(source_path, deadline)) as reader:
            logical_before = _logical(reader, deadline, max_rows)
        _deadline(deadline)
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            _hold("PRIVATE_OUTPUT_NOT_NEW_OR_EXACT")
        if _backup_sidecar_occupied(root, backup_path):
            _hold("PRIVATE_OUTPUT_NOT_NEW_OR_EXACT")
        # O_EXCL prevents overwriting an existing recovery point. A failed run
        # leaves a partial private file for forensic review, never a PASS receipt.
        fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(_connect_readonly(source_path, deadline)) as reader:
            with closing(sqlite3.connect(backup_path, timeout=1.0)) as copy:
                reader.backup(copy, pages=256, sleep=0.05,
                              progress=lambda *_: _deadline(deadline))
                copy.commit()
        with closing(_connect_readonly(backup_path, deadline)) as copy:
            logical_backup = _logical(copy, deadline, max_rows)
        with closing(_connect_readonly(source_path, deadline)) as reader:
            logical_after = _logical(reader, deadline, max_rows)
        source_after = _sample(source_path, deadline, max_source_bytes)
        if source_before != source_after:
            _hold("SOURCE_PHYSICAL_SIGNATURE_DRIFT")
        if not (logical_before == logical_backup == logical_after):
            _hold("SOURCE_BACKUP_LOGICAL_MISMATCH")
        backup_physical = _sample(backup_path, deadline, max_source_bytes)
        if _unexpected_backup_sidecars(root, backup_path):
            _hold("BACKUP_UNEXPECTED_SIDECAR_PRESENT")
        body = {"schema": "orbit-catalog-online-snapshot-evidence/v1",
                "status": "TECHNICAL_VERIFIED_POLICY_HOLD", "execution_authority": False,
                "started_at_utc": started_at_utc,
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                "sqlite_version": sqlite3.sqlite_version,
                "source": str(source_path), "backup": str(backup_path),
                "source_before": source_before, "source_after": source_after,
                "backup_physical": backup_physical,
                "logical_before": logical_before, "logical_backup": logical_backup,
                "logical_after": logical_after, "backup_sha256": _sha(backup_path, deadline),
                "backup_size": backup_path.stat().st_size,
                "source_sql_write_performed": False,
                "source_sidecar_coordination_may_change": True,
                "private_root_newly_created": True,
                "private_root_os_acl_and_single_writer_not_verified": True,
                "publication_operation_time_not_verified": True,
                "pre_publication_max_seconds": max_seconds,
                "formal_jit_policy_exception_required": True}
        # Never expose a parsable success receipt until its bytes are durable
        # and the deadline has been checked. Linking publishes without clobber.
        temporary = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=receipt_path.name + ".pending-", suffix=".tmp", dir=root)
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(body, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            _deadline(deadline)
            if _sample(source_path, deadline, max_source_bytes) != source_before:
                _hold("SOURCE_PHYSICAL_SIGNATURE_DRIFT")
            if _sample(backup_path, deadline, max_source_bytes) != backup_physical:
                _hold("BACKUP_PHYSICAL_SIGNATURE_DRIFT")
            if _unexpected_backup_sidecars(root, backup_path):
                _hold("BACKUP_UNEXPECTED_SIDECAR_PRESENT")
            _deadline(deadline)
            # This is the final committing operation. An OS-level delayed link
            # cannot safely be cancelled after publication; the receipt remains
            # policy HOLD and its publication time needs outside verification.
            os.link(temporary, receipt_path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return body
    except SnapshotGateHold:
        raise
    except (OSError, sqlite3.Error, TimeoutError) as exc:
        raise SnapshotGateHold("SNAPSHOT_IO_OR_SQLITE_ERROR:" + type(exc).__name__) from exc
