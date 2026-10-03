"""An existing stable ledger must not be migrated by a task-page GET."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.operations_launch import preflight_deployment, preflight_operations_schema


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _old_ledger(path):
    release = {"code_version": "a" * 40, "environment": "stable", "manifest_digest": "fixture"}
    engine = WorkbenchEngine(path, release)
    engine.create({"template": "profit", "scope": {"month": "2026-08"}, "source_key": "old-ledger"})
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE workbench_profit_requests")
        conn.execute("DROP TABLE workbench_review_identity")
    return release


def test_existing_ledger_requires_explicit_migration_before_task_get(tmp_path):
    path = tmp_path / "tasks.db"
    release = _old_ledger(path)
    before = _digest(path)
    with pytest.raises(ValueError, match="OPERATIONS_SCHEMA_MIGRATION_REQUIRED") as error:
        preflight_operations_schema(path)
    assert all(name in str(error.value) for name in (
        'workbench_profit_requests', 'workbench_review_identity',
        'workbench_profit_requests_no_update', 'workbench_profit_requests_no_delete'))
    assert _digest(path) == before
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True) as conn:
        objects = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert "workbench_profit_requests" not in objects
    assert "workbench_review_identity" not in objects
    # A rejected preflight leaves no reserved/exclusive lock behind.
    with sqlite3.connect(path, timeout=0) as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.rollback()


def test_current_schema_task_get_keeps_ledger_bytes_and_task(tmp_path):
    path = tmp_path / "tasks.db"
    release = {"code_version": "b" * 40, "environment": "stable", "manifest_digest": "fixture"}
    task = WorkbenchEngine(path, release).create(
        {"template": "profit", "scope": {"month": "2026-08"}, "source_key": "current-ledger"}
    )
    before = _digest(path)
    assert preflight_operations_schema(path)['database_writes'] == 0
    assert _digest(path) == before
    dashboard = WorkbenchEngine(path, release).dashboard()
    assert [row["task_id"] for row in dashboard["tasks"]] == [task["task_id"]]
    assert _digest(path) == before


def test_uncleared_wal_sidecar_is_not_ignored_by_readonly_schema_check(tmp_path):
    path = tmp_path / 'tasks.db'
    release = {"code_version": "b" * 40, "environment": "stable", "manifest_digest": "fixture"}
    WorkbenchEngine(path, release).create(
        {"template": "profit", "scope": {"month": "2026-08"}, "source_key": "sidecar"}
    )
    before = _digest(path)
    (tmp_path / 'tasks.db-wal').write_bytes(b'uncleared fixture')
    with pytest.raises(ValueError, match='OPERATIONS_LEDGER_UNCHECKPOINTED'):
        preflight_operations_schema(path)
    assert _digest(path) == before


def test_changed_existing_trigger_requires_explicit_migration(tmp_path):
    path = tmp_path / 'tasks.db'
    release = {"code_version": "b" * 40, "environment": "stable", "manifest_digest": "fixture"}
    WorkbenchEngine(path, release).create(
        {"template": "profit", "scope": {"month": "2026-08"}, "source_key": "trigger-drift"}
    )
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TRIGGER workbench_profit_requests_no_update')
        conn.execute('CREATE TRIGGER workbench_profit_requests_no_update '
                     'BEFORE UPDATE ON workbench_profit_requests BEGIN SELECT 1; END')
    before = _digest(path)
    with pytest.raises(ValueError, match='OPERATIONS_SCHEMA_MIGRATION_REQUIRED') as error:
        preflight_operations_schema(path)
    assert 'workbench_profit_requests_no_update' in str(error.value)
    assert '"changed"' in str(error.value)
    assert _digest(path) == before


@pytest.mark.parametrize('special_name', ['tasks#old.db', 'tasks%old.db', 'tasks space.db', '任务.db'])
def test_preflight_reads_the_exact_special_character_ledger(tmp_path, special_name):
    release = {"code_version": "b" * 40, "environment": "stable", "manifest_digest": "fixture"}
    # The unescaped URI for tasks#old.db can silently open this complete neighbor.
    WorkbenchEngine(tmp_path / 'tasks', release).create(
        {"template": "profit", "scope": {"month": "2026-08"}, "source_key": "neighbor"}
    )
    path = tmp_path / special_name
    _old_ledger(path)
    before = _digest(path)
    with pytest.raises(ValueError, match='OPERATIONS_SCHEMA_MIGRATION_REQUIRED'):
        preflight_operations_schema(path)
    assert _digest(path) == before


def test_formal_launcher_refuses_old_schema_before_ready_or_ledger_write(tmp_path, monkeypatch):
    from shared_platform import operations_launch as launch

    root = tmp_path / 'candidate'
    root.mkdir()
    data_root = tmp_path / 'operations'
    data_root.mkdir()
    db = data_root / 'tasks.db'
    _old_ledger(db)
    before = _digest(db)
    config = {'code_root': str(root), 'code_version': 'a' * 40,
              'workbench_store_path': str(tmp_path / 'other-workbench.db')}
    for key in ('settings', 'catalog_database', 'catalog_weight_overrides',
                'release_store_path', 'report_store_path'):
        path = tmp_path / key
        path.write_bytes(b'fixture')
        config[key] = str(path)
    for key in ('operations_data_root', 'original_profit_asset_root', 'ozon_data_root'):
        path = data_root if key == 'operations_data_root' else tmp_path / key
        path.mkdir(exist_ok=True)
        config[key] = str(path)
    monkeypatch.setattr(launch.subprocess, 'check_output', lambda args, **kwargs: 'a' * 40 if args[1] == 'rev-parse' else '')
    with pytest.raises(ValueError, match='OPERATIONS_SCHEMA_MIGRATION_REQUIRED'):
        preflight_deployment(config, root)
    assert _digest(db) == before
    assert not (tmp_path / 'ready.json').exists()
