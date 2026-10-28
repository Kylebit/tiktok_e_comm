"""Explicit three-object cancellation schema adoption; never a GET initializer."""
from contextlib import closing
from pathlib import Path
import sqlite3

from scripts.migrate_operations_task_schema import _connect, _fingerprint, _integrity, _schema, _sql, DESIRED

ADDED = (
    ('table', 'workbench_publication_scope_reductions'),
    ('trigger', 'workbench_publication_scope_reductions_no_update'),
    ('trigger', 'workbench_publication_scope_reductions_no_delete'),
)


def inspect_schema_adoption(engine):
    with closing(_connect(engine.store.path.resolve(strict=True), writable=False)) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        _integrity(db)
        actual = _schema(db)
        changed = [key for key in DESIRED if key in actual and _sql(DESIRED[key]) != actual[key]]
        missing = set(DESIRED) - set(actual)
        if changed or not missing <= set(ADDED):
            raise ValueError('PREPARED_TARGET_SCHEMA_UNRELATED_MIGRATION_REQUIRED')
        return {'status': 'EXACT_TARGET_ADDITIONS_REQUIRED' if missing else 'INSTALLED',
                'missing': [list(key) for key in ADDED if key in missing],
                'logical_sha256': _fingerprint(db)['logical_sha256'], 'database_writes': 0}


def adopt_schema(binding, *, expected_logical_sha256, backup_path):
    from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
    if type(binding) is not OriginalPreparedTaskContinuation:
        raise ValueError('ORIGINAL_CONTINUATION_CONCRETE_BINDING_REQUIRED')
    binding.verify_sources()
    path = binding.engine.store.path.resolve(strict=True)
    planned = inspect_schema_adoption(binding.engine)
    if planned['logical_sha256'] != expected_logical_sha256:
        raise ValueError('PREPARED_TARGET_SCHEMA_CAS_CHANGED')
    if not planned['missing']:
        return {'status': 'ALREADY_INSTALLED', 'objects_added': [], 'database_writes': 0}
    backup_path = Path(backup_path)
    if not backup_path.is_absolute() or backup_path.exists() or backup_path.resolve() == path:
        raise ValueError('PREPARED_TARGET_SCHEMA_NEW_DISTINCT_BACKUP_REQUIRED')
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(path, writable=False)) as db:
        with closing(sqlite3.connect(backup_path)) as copy:
            db.backup(copy)
    with closing(_connect(path, writable=True)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            before = _fingerprint(db)
            if before['logical_sha256'] != expected_logical_sha256:
                raise ValueError('PREPARED_TARGET_SCHEMA_CAS_CHANGED')
            row = db.execute('SELECT state,worker,lease_token,lease_until,external_started FROM workbench_execution WHERE task_id=?',
                             (binding.pins.task_id,)).fetchone()
            if row is None or row != ('failed', None, None, None, 0):
                raise ValueError('PREPARED_TARGET_SCHEMA_ORIGINAL_WRITER_NOT_QUIESCED')
            if (db.execute('SELECT 1 FROM workbench_resource_locks WHERE task_id=?', (binding.pins.task_id,)).fetchone()
                    or db.execute('SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>?', (binding.pins.task_id, 'completed')).fetchone()):
                raise ValueError('PREPARED_TARGET_SCHEMA_UNRESOLVED_ORIGINAL_OPERATION')
            actual = _schema(db)
            added = [key for key in ADDED if key not in actual]
            for key in added:
                db.execute(DESIRED[key])
            after = _fingerprint(db)
            old_tables = {name: value for name, value in after['tables'].items() if name in before['tables']}
            if old_tables != before['tables'] or any(after['objects'].get(name) != value for name, value in before['objects'].items()):
                raise ValueError('PREPARED_TARGET_SCHEMA_EXISTING_DATA_OR_OBJECT_CHANGED')
            _integrity(db)
            db.commit()
            return {'status': 'INSTALLED', 'objects_added': [list(key) for key in added],
                    'backup_path': str(backup_path), 'existing_table_rows_and_schema_unchanged': True,
                    'logical_sha256_before': before['logical_sha256'], 'logical_sha256_after': after['logical_sha256']}
        except BaseException:
            db.rollback()
            raise
