"""Synthetic WAL rehearsals only; never open a real operations ledger."""
from pathlib import Path
import os
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from scripts.migrate_operations_task_schema import (
    ADDED, DESIRED, _fingerprint, _schema, migrate, reconcile, rollback, snapshot,
)
from scripts import migrate_operations_task_schema as migration
from shared_platform.workbench_store import _SCHEMA as BASE_SCHEMA


def old_ledger(path: Path) -> Path:
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.executescript(BASE_SCHEMA)
        for key, ddl in DESIRED.items():
            if key not in ADDED:
                conn.execute(ddl)
        conn.execute("INSERT INTO workbench_tasks(task_id,title,priority,status,created_at,updated_at) "
                     "VALUES('TASK-1','synthetic','P1','done','2026-09-27','2026-09-27')")
        conn.execute("INSERT INTO workbench_events(task_id,event_type,created_at) "
                     "VALUES('TASK-1','created','2026-09-27')")
        conn.execute("INSERT INTO workbench_execution(task_id,template,scope_json,version_json,"
                     "request_digest,state,steps_json) VALUES('TASK-1','profit','{}','{}','digest',"
                     "'completed','[]')")
        conn.execute("INSERT INTO workbench_resource_locks(resource,task_id) "
                     "VALUES('synthetic:lock','TASK-1')")
        conn.commit()
    return path


def receipt_paths(tmp_path: Path):
    return tmp_path / "snapshot.db", tmp_path / "snapshot.json", tmp_path / "migrated.json"


def names(path: Path):
    with closing(sqlite3.connect(path)) as conn:
        return set(_schema(conn))


def test_wal_snapshot_migrate_and_safe_rollback_preserve_tasks_events_locks(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    baseline = snapshot(source, expected=source, backup=backup, receipt=first)
    assert baseline["source_write_performed"] is False
    assert baseline["logical"]["tables"]["workbench_tasks"]["rows"] == 1
    assert baseline["logical"]["tables"]["workbench_events"]["rows"] == 1
    assert baseline["logical"]["tables"]["workbench_resource_locks"]["rows"] == 1
    result = migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    assert result["before_logical_sha256"] == baseline["logical"]["logical_sha256"]
    assert set(ADDED) <= names(source)
    with sqlite3.connect(source) as conn:
        migrated = _fingerprint(conn)
    for table, state in baseline["logical"]["tables"].items():
        assert migrated["tables"][table] == state
    assert backup.is_file() and second.is_file()
    down = rollback(source, expected=source, migration_receipt=second,
                    receipt=tmp_path / "rollback.json")
    assert down["source_overwritten_from_backup"] is False
    assert set(ADDED).isdisjoint(names(source))
    with sqlite3.connect(source) as conn:
        assert _fingerprint(conn) == baseline["logical"]


@pytest.mark.parametrize("after", [1, 2, 3, 4])
def test_each_ddl_interruption_rolls_back_all_four_objects(tmp_path, after):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    baseline = snapshot(source, expected=source, backup=backup, receipt=first)
    with pytest.raises(RuntimeError, match="injected interruption"):
        migrate(source, expected=source, snapshot_receipt=first, receipt=second,
                fail_after=after)
    assert not second.exists()
    assert set(ADDED).isdisjoint(names(source))
    with sqlite3.connect(source) as conn:
        assert _fingerprint(conn) == baseline["logical"]


def test_new_event_after_snapshot_blocks_migration_without_overwrite(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO workbench_events(task_id,event_type,created_at) "
                     "VALUES('TASK-1','new_event','2026-09-27')")
    with pytest.raises(ValueError, match="changed since verified snapshot"):
        migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    assert set(ADDED).isdisjoint(names(source))
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT count(*) FROM workbench_events").fetchone()[0] == 2


@pytest.mark.parametrize("after,state,applied", [(5, "aborted", False), (6, "committed", True)])
def test_receipt_reconciles_interruption_around_commit(tmp_path, after, state, applied):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    with pytest.raises(RuntimeError, match="injected interruption"):
        migrate(source, expected=source, snapshot_receipt=first, receipt=second,
                fail_after=after)
    assert second.is_file()
    assert (set(ADDED) <= names(source)) is applied
    audit_path = tmp_path / "migration-reconcile.json"
    result = reconcile(source, expected=source, migration_receipt=second,
                       audit_receipt=audit_path)
    assert result["commit_state"] == state
    assert audit_path.is_file()
    assert result["classification"] == ("ALREADY_APPLIED" if applied else "NOT_APPLIED")
    if applied:
        with pytest.raises(ValueError, match="changed since verified snapshot"):
            migrate(source, expected=source, snapshot_receipt=first,
                    receipt=tmp_path / "retry.json")


@pytest.mark.parametrize("change", ["event", "resource_lock"])
def test_new_work_after_migration_blocks_rollback_and_preserves_it(tmp_path, change):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    with sqlite3.connect(source) as conn:
        if change == "event":
            conn.execute("INSERT INTO workbench_events(task_id,event_type,created_at) "
                         "VALUES('TASK-1','new_after_migration','2026-09-27')")
        else:
            conn.execute("INSERT INTO workbench_resource_locks(resource,task_id) "
                         "VALUES('synthetic:later','TASK-1')")
    with pytest.raises(ValueError, match="changed after migration"):
        rollback(source, expected=source, migration_receipt=second,
                 receipt=tmp_path / "rollback.json")
    assert set(ADDED) <= names(source)
    with sqlite3.connect(source) as conn:
        table = "workbench_events" if change == "event" else "workbench_resource_locks"
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 2


def test_online_backup_includes_committed_wal_event(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        writer.execute("INSERT INTO workbench_events(task_id,event_type,created_at) "
                       "VALUES('TASK-1','wal_event','2026-09-27')")
        writer.commit()
        backup, first, _ = receipt_paths(tmp_path)
        result = snapshot(source, expected=source, backup=backup, receipt=first)
        assert result["logical"]["tables"]["workbench_events"]["rows"] == 2
        with sqlite3.connect(backup) as conn:
            assert conn.execute("SELECT count(*) FROM workbench_events").fetchone()[0] == 2
    finally:
        writer.close()


def test_partial_schema_and_wrong_identity_fail_closed(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    with pytest.raises(ValueError, match="approved exact source"):
        migrate(source, expected=backup, snapshot_receipt=first, receipt=second)
    with sqlite3.connect(source) as conn:
        conn.execute(DESIRED[("table", "workbench_profit_requests")])
    with pytest.raises(ValueError, match="changed since verified snapshot"):
        migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    assert not second.exists()


def test_hash_in_path_opens_exact_database_not_sibling(tmp_path):
    source = old_ledger(tmp_path / "tasks#private.db")
    sibling = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    assert set(ADDED) <= names(source)
    assert set(ADDED).isdisjoint(names(sibling))


def test_unregistered_trigger_blocks_migration(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TRIGGER extra_event_guard BEFORE DELETE ON workbench_events "
                     "BEGIN SELECT RAISE(ABORT, 'extra guard'); END")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    with pytest.raises(ValueError, match="unregistered schema object"):
        migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    assert set(ADDED).isdisjoint(names(source))


def test_rollback_rejects_logically_identical_replaced_file(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    old_inode = source.stat().st_ino
    replacement = tmp_path / "replacement.db"
    with closing(sqlite3.connect(source)) as reader, closing(sqlite3.connect(replacement)) as writer:
        reader.backup(writer)
    assert replacement.stat().st_ino != old_inode
    os.replace(replacement, source)
    assert source.stat().st_ino != old_inode
    with pytest.raises(ValueError, match="file identity changed"):
        rollback(source, expected=source, migration_receipt=second,
                 receipt=tmp_path / "rollback.json")
    assert set(ADDED) <= names(source)


def test_rollback_commit_receipt_crash_is_read_only_reconcilable(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    rollback_receipt = tmp_path / "rollback.json"
    with pytest.raises(RuntimeError, match="injected interruption"):
        migration.rollback(source, expected=source, migration_receipt=second,
                           receipt=rollback_receipt, fail_after_commit=True)
    assert rollback_receipt.is_file()
    assert set(ADDED).isdisjoint(names(source))
    audit = migration.reconcile_rollback(source, expected=source,
                                         rollback_receipt=rollback_receipt,
                                         audit_receipt=tmp_path / "rollback-reconcile.json")
    assert audit["classification"] == "ALREADY_ROLLED_BACK"
    assert (tmp_path / "rollback-reconcile.json").is_file()
    with pytest.raises(ValueError, match="already rolled back"):
        rollback(source, expected=source, migration_receipt=second,
                 receipt=tmp_path / "retry.json")


def test_migration_connections_close_for_immediate_windows_rename(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    migrate(source, expected=source, snapshot_receipt=first, receipt=second)
    moved = tmp_path / "moved.db"
    os.replace(source, moved)
    os.replace(moved, source)
    rollback(source, expected=source, migration_receipt=second,
             receipt=tmp_path / "rollback.json")
    os.replace(source, moved)


def test_unavailable_windows_file_id_fails_closed(tmp_path, monkeypatch):
    source = old_ledger(tmp_path / "tasks.db")
    with monkeypatch.context() as patch:
        patch.setattr(migration.Path, "stat", lambda self: SimpleNamespace(st_dev=0, st_ino=0))
        with pytest.raises(ValueError, match="file identity unavailable"):
            migration._file_id(source)


def test_migration_reconcile_unknown_after_new_event_only_audits(tmp_path):
    source = old_ledger(tmp_path / "tasks.db")
    backup, first, second = receipt_paths(tmp_path)
    snapshot(source, expected=source, backup=backup, receipt=first)
    with pytest.raises(RuntimeError, match="injected interruption"):
        migrate(source, expected=source, snapshot_receipt=first, receipt=second,
                fail_after=6)
    with closing(sqlite3.connect(source)) as conn:
        conn.execute("INSERT INTO workbench_events(task_id,event_type,created_at) "
                     "VALUES('TASK-1','new_event','2026-09-27')")
        conn.commit()
    audit_path = tmp_path / "unknown-audit.json"
    result = reconcile(source, expected=source, migration_receipt=second,
                       audit_receipt=audit_path)
    assert result["classification"] == "UNKNOWN_REQUIRES_MANUAL_RECONCILIATION"
    assert result["commit_state"] == "prepared"
    assert audit_path.is_file()
    assert set(ADDED) <= names(source)
