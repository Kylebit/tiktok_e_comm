"""Synthetic-only tests for the unconnected catalog JIT snapshot evidence gate."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import os
import sqlite3
import struct
import sys
import time
from types import SimpleNamespace

import pytest

from shared_platform import catalog_online_snapshot_gate as gate


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _rechecksum_wal_index_header(header: bytearray, order: str | None = None) -> None:
    order = order or ("<" if sys.byteorder == "little" else ">")
    words = struct.unpack_from(order + "10I", header)
    first = second = 0
    for index in range(0, len(words), 2):
        first = (first + words[index] + second) & 0xffffffff
        second = (second + words[index + 1] + first) & 0xffffffff
    struct.pack_into(order + "2I", header, 40, first, second)
    header[48:96] = header[:48]


def _source(tmp_path: Path, *, checkpoint: bool) -> tuple[Path, sqlite3.Connection]:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "catalog.db"
    connection = sqlite3.connect(source)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.execute("CREATE TABLE products (sku TEXT PRIMARY KEY, cost REAL NOT NULL)")
    connection.execute("INSERT INTO products VALUES ('synthetic-A', 3.5)")
    connection.commit()
    if checkpoint:
        assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        assert Path(str(source) + "-wal").stat().st_size == 0
        assert Path(str(source) + "-shm").stat().st_size == 32768
    return source, connection


def _run(source: Path, tmp_path: Path, **kwargs):
    private_root = tmp_path / "private"
    expected_main = kwargs.pop("expected_main_sha256", _sha(source))
    expected_shm = kwargs.pop("expected_shm_sha256", _sha(Path(str(source) + "-shm")))
    return gate.create_catalog_snapshot_evidence(
        source=source, expected_source=source, private_root=private_root,
        backup=private_root / "backup.db", receipt=private_root / "receipt.json",
        expected_main_sha256=expected_main,
        expected_shm_sha256=expected_shm,
        **kwargs,
    )


def test_nonempty_shm_empty_wal_can_produce_technical_evidence_only(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    try:
        result = _run(source, tmp_path)
        assert result["status"] == "TECHNICAL_VERIFIED_POLICY_HOLD"
        assert result["execution_authority"] is False
        assert result["source_before"] == result["source_after"]
        assert result["source_before"]["-shm"]["size"] == 32768
        assert result["source_before"]["-wal"]["size"] == 0
        assert result["source_before"]["wal_index"]["header_checksum_verified"] is True
        assert result["source_before"]["wal_index"]["salt_and_frame_checksum_unbound_to_empty_wal"] is True
        assert result["logical_before"] == result["logical_backup"] == result["logical_after"]
        assert result["backup_sha256"] == _sha(tmp_path / "private" / "backup.db")
    finally:
        writer.close()


@pytest.mark.parametrize("offset", (8, 32, 40))
def test_corrupt_wal_index_header_checksum_is_hold(tmp_path, offset):
    source, writer = _source(tmp_path, checkpoint=True)
    shm = Path(str(source) + "-shm")
    try:
        header = bytearray(shm.read_bytes()[:136])
        header[offset] ^= 1
        header[offset + 48] ^= 1
        with pytest.raises(gate.SnapshotGateHold, match="SHM_HEADER_CHECKSUM_INVALID"):
            gate._wal_index_header(header, source.read_bytes()[:100], source.stat().st_size)
    finally:
        writer.close()


def test_wal_index_header_copies_must_match(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    shm = Path(str(source) + "-shm")
    try:
        header = bytearray(shm.read_bytes()[:136])
        header[8 + 48] ^= 1
        with pytest.raises(gate.SnapshotGateHold, match="SHM_HEADER_COPIES_DIFFER"):
            gate._wal_index_header(header, source.read_bytes()[:100], source.stat().st_size)
    finally:
        writer.close()


def test_wal_index_checksum_uses_native_big_endian_when_applicable(monkeypatch):
    header = bytearray(136)
    struct.pack_into(">I", header, 0, 3007000)
    header[12] = 1
    header[13] = 1
    struct.pack_into(">H", header, 14, 4096)
    struct.pack_into(">I", header, 20, 3)
    header[32:40] = b"saltdata"
    _rechecksum_wal_index_header(header, ">")
    main = bytearray(100)
    main[16:18] = (4096).to_bytes(2, "big")
    monkeypatch.setattr(gate, "sys", SimpleNamespace(byteorder="big"))
    result = gate._wal_index_header(header, main, 3 * 4096)
    assert result["native_byte_order"] == "big"
    assert result["header_checksum_verified"] is True


@pytest.mark.parametrize("field,offset,new_byte,reason", (
    ("padding", 4, 1, "SHM_HEADER_VERSION_OR_PADDING_INVALID"),
    ("initialized", 12, 0, "SHM_HEADER_FLAGS_INVALID"),
    ("page_size", 14, 3, "SHM_HEADER_PAGE_SIZE_INVALID"),
    ("backfill", 96, 1, "SHM_HEADER_FRAME_OR_PAGE_COUNTS_INVALID"),
))
def test_rechecksummed_but_structurally_invalid_header_is_hold(
        tmp_path, field, offset, new_byte, reason):
    source, writer = _source(tmp_path, checkpoint=True)
    shm = Path(str(source) + "-shm")
    try:
        header = bytearray(shm.read_bytes()[:136])
        header[offset] = new_byte
        if offset < 48:
            _rechecksum_wal_index_header(header)
        with pytest.raises(gate.SnapshotGateHold, match=reason):
            gate._wal_index_header(header, source.read_bytes()[:100], source.stat().st_size)
    finally:
        writer.close()


def test_nonzero_wal_rejected_before_backup(tmp_path):
    source, writer = _source(tmp_path, checkpoint=False)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="WAL_NOT_EMPTY"):
            _run(source, tmp_path)
        assert not (tmp_path / "private" / "backup.db").exists()
    finally:
        writer.close()


def test_stale_source_sha_rejected_before_backup(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="EXPECTED_MAIN_SHA_MISMATCH"):
            _run(source, tmp_path, expected_main_sha256="0" * 64)
        assert not (tmp_path / "private" / "backup.db").exists()
    finally:
        writer.close()


def test_budget_fails_closed_without_receipt(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="ROW_BUDGET_EXCEEDED"):
            _run(source, tmp_path, max_rows=0)
        assert not (tmp_path / "private" / "receipt.json").exists()
    finally:
        writer.close()


def test_source_directory_cannot_be_private_root(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="PRIVATE_ROOT_OVERLAPS_SOURCE"):
            gate.create_catalog_snapshot_evidence(
                source=source, expected_source=source, private_root=tmp_path,
                backup=tmp_path / "backup.db", receipt=tmp_path / "receipt.json",
                expected_main_sha256=_sha(source),
                expected_shm_sha256=_sha(Path(str(source) + "-shm")),
            )
    finally:
        writer.close()


def test_private_backup_must_be_new(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    try:
        private = tmp_path / "private"
        private.mkdir()
        old = private / "backup.db"
        old.write_bytes(b"preserved-recovery-point")
        with pytest.raises(gate.SnapshotGateHold, match="PRIVATE_OUTPUT_NOT_NEW_OR_EXACT"):
            _run(source, tmp_path)
        assert old.read_bytes() == b"preserved-recovery-point"
    finally:
        writer.close()


def test_source_identity_mismatch_rejected_before_backup(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    other = tmp_path / "other.db"
    other.write_bytes(b"not-the-source")
    private = tmp_path / "private"
    private.mkdir()
    try:
        with pytest.raises(gate.SnapshotGateHold, match="SOURCE_IDENTITY_MISMATCH"):
            gate.create_catalog_snapshot_evidence(
                source=source, expected_source=other, private_root=private,
                backup=private / "backup.db", receipt=private / "receipt.json",
                expected_main_sha256=_sha(source),
                expected_shm_sha256=_sha(Path(str(source) + "-shm")),
            )
        assert not (private / "backup.db").exists()
    finally:
        writer.close()


def test_nonempty_shm_is_not_silently_ignored(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="EXPECTED_SHM_SHA_MISMATCH"):
            _run(source, tmp_path, expected_shm_sha256="0" * 64)
        assert not (tmp_path / "private" / "backup.db").exists()
    finally:
        writer.close()


def test_physical_drift_after_backup_has_no_pass_receipt(tmp_path, monkeypatch):
    source, writer = _source(tmp_path, checkpoint=True)
    real_sample = gate._sample
    sample_count = 0

    def changed_at_final_sample(*args, **kwargs):
        nonlocal sample_count
        sample_count += 1
        result = real_sample(*args, **kwargs)
        if sample_count == 3:
            result["-shm"]["mtime_ns"] += 1
        return result

    monkeypatch.setattr(gate, "_sample", changed_at_final_sample)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="SOURCE_PHYSICAL_SIGNATURE_DRIFT"):
            _run(source, tmp_path)
        assert (tmp_path / "private" / "backup.db").exists()
        assert not (tmp_path / "private" / "receipt.json").exists()
    finally:
        writer.close()


def test_real_writer_during_gate_cannot_pass(tmp_path, monkeypatch):
    source, writer = _source(tmp_path, checkpoint=True)
    real_logical = gate._logical
    logical_count = 0

    def write_after_backup(connection, deadline, max_rows):
        nonlocal logical_count
        logical_count += 1
        result = real_logical(connection, deadline, max_rows)
        if logical_count == 2:
            writer.execute("INSERT INTO products VALUES ('synthetic-B', 4.0)")
            writer.commit()
        return result

    monkeypatch.setattr(gate, "_logical", write_after_backup)
    try:
        with pytest.raises(gate.SnapshotGateHold):
            _run(source, tmp_path)
        assert not (tmp_path / "private" / "receipt.json").exists()
    finally:
        writer.close()


def test_source_symlink_and_private_source_overlap_rejected(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    alias = tmp_path / "source-link.db"
    try:
        alias.symlink_to(source)
        private = tmp_path / "private"
        private.mkdir()
        with pytest.raises(gate.SnapshotGateHold, match="SOURCE_SYMLINK_SIDECAR"):
            gate.create_catalog_snapshot_evidence(
                source=alias, expected_source=alias, private_root=private,
                backup=private / "backup.db", receipt=private / "receipt.json",
                expected_main_sha256=_sha(source),
                expected_shm_sha256=_sha(Path(str(source) + "-shm")),
            )
        assert not (tmp_path / "private" / "backup.db").exists()
    finally:
        writer.close()


@pytest.mark.parametrize("sidecar", ("-journal", "-wal", "-shm"))
def test_preexisting_backup_sidecar_is_preserved(tmp_path, sidecar):
    source, writer = _source(tmp_path, checkpoint=True)
    private = tmp_path / "private"
    private.mkdir()
    sentinel = private / ("backup.db" + sidecar)
    sentinel.write_bytes(b"preserve-existing-recovery-sidecar")
    try:
        with pytest.raises(gate.SnapshotGateHold, match="PRIVATE_OUTPUT_NOT_NEW_OR_EXACT"):
            _run(source, tmp_path)
        assert sentinel.read_bytes() == b"preserve-existing-recovery-sidecar"
        assert not (private / "backup.db").exists()
        assert not (private / "receipt.json").exists()
    finally:
        writer.close()


def test_sidecar_appearing_during_backup_never_gets_receipt(tmp_path, monkeypatch):
    source, writer = _source(tmp_path, checkpoint=True)
    real_logical = gate._logical
    logical_count = 0

    def create_private_sidecar_after_copy(connection, deadline, max_rows):
        nonlocal logical_count
        logical_count += 1
        result = real_logical(connection, deadline, max_rows)
        if logical_count == 2:
            (tmp_path / "private" / "backup.db-journal").write_bytes(b"new-synthetic-sidecar")
        return result

    monkeypatch.setattr(gate, "_logical", create_private_sidecar_after_copy)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="JOURNAL_SIDECAR_PRESENT"):
            _run(source, tmp_path)
        assert not (tmp_path / "private" / "receipt.json").exists()
    finally:
        writer.close()


def test_receipt_fsync_failure_never_leaves_success_json(tmp_path, monkeypatch):
    source, writer = _source(tmp_path, checkpoint=True)

    def fail_fsync(_fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(gate.os, "fsync", fail_fsync)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="SNAPSHOT_IO_OR_SQLITE_ERROR"):
            _run(source, tmp_path)
        assert not (tmp_path / "private" / "receipt.json").exists()
        assert not list((tmp_path / "private").glob("receipt.json.pending-*"))
    finally:
        writer.close()


def test_extra_backup_sidecar_before_publish_is_hold(tmp_path, monkeypatch):
    source, writer = _source(tmp_path, checkpoint=True)
    real_fsync = os.fsync

    def add_sidecar_after_fsync(fd):
        real_fsync(fd)
        (tmp_path / "private" / "backup.db-mj-synthetic").write_bytes(b"late-sidecar")

    monkeypatch.setattr(gate.os, "fsync", add_sidecar_after_fsync)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="BACKUP_UNEXPECTED_SIDECAR_PRESENT"):
            _run(source, tmp_path)
        assert not (tmp_path / "private" / "receipt.json").exists()
        assert not list((tmp_path / "private").glob("receipt.json.pending-*"))
    finally:
        writer.close()


def test_receipt_fsync_over_deadline_never_publishes(tmp_path, monkeypatch):
    source, writer = _source(tmp_path, checkpoint=True)
    real_fsync = os.fsync
    calls = []

    def slow_fsync(fd):
        calls.append(fd)
        time.sleep(0.2)
        real_fsync(fd)

    monkeypatch.setattr(gate.os, "fsync", slow_fsync)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="SNAPSHOT_TIME_BUDGET_EXCEEDED"):
            _run(source, tmp_path, max_seconds=0.1, quiet_seconds=0)
        assert calls
        assert not (tmp_path / "private" / "receipt.json").exists()
        assert not list((tmp_path / "private").glob("receipt.json.pending-*"))
    finally:
        writer.close()


def test_receipt_link_failure_leaves_no_published_or_pending_json(tmp_path, monkeypatch):
    source, writer = _source(tmp_path, checkpoint=True)

    def fail_link(*_args, **_kwargs):
        raise OSError("synthetic link failure")

    monkeypatch.setattr(gate.os, "link", fail_link)
    try:
        with pytest.raises(gate.SnapshotGateHold, match="SNAPSHOT_IO_OR_SQLITE_ERROR"):
            _run(source, tmp_path)
        assert not (tmp_path / "private" / "receipt.json").exists()
        assert not list((tmp_path / "private").glob("receipt.json.pending-*"))
    finally:
        writer.close()


def test_slow_atomic_publication_remains_policy_hold(tmp_path, monkeypatch):
    source, writer = _source(tmp_path, checkpoint=True)
    real_link = os.link
    calls = []

    def slow_link(*args, **kwargs):
        calls.append(args)
        time.sleep(0.2)
        return real_link(*args, **kwargs)

    monkeypatch.setattr(gate.os, "link", slow_link)
    try:
        result = _run(source, tmp_path, max_seconds=0.1, quiet_seconds=0)
        assert calls
        assert result["status"] == "TECHNICAL_VERIFIED_POLICY_HOLD"
        assert result["execution_authority"] is False
        assert result["publication_operation_time_not_verified"] is True
        assert (tmp_path / "private" / "receipt.json").exists()
    finally:
        writer.close()


def test_receipt_name_cannot_be_backup_sidecar(tmp_path):
    source, writer = _source(tmp_path, checkpoint=True)
    private = tmp_path / "private"
    try:
        with pytest.raises(gate.SnapshotGateHold, match="PRIVATE_OUTPUT_NOT_NEW_OR_EXACT"):
            gate.create_catalog_snapshot_evidence(
                source=source, expected_source=source, private_root=private,
                backup=private / "backup.db", receipt=private / "backup.db-journal",
                expected_main_sha256=_sha(source),
                expected_shm_sha256=_sha(Path(str(source) + "-shm")),
            )
        assert not (private / "backup.db").exists()
    finally:
        writer.close()
