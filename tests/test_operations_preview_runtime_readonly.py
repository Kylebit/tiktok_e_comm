"""Preview runtime inspection must not perform dashboard lease maintenance."""
from email.message import Message
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from shared_platform.operations_http import handle
from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine


def runtime_fixture(tmp_path, environment, external_started=False):
    now = [100.0]
    identity = {'code_version': 'synthetic-preview', 'environment': environment, 'manifest_digest': 'synthetic'}
    engine = WorkbenchEngine(tmp_path / 'tasks.db', identity, clock=lambda: now[0])
    task = engine.create({'template': 'profit', 'scope': {'month': '2026-08'}, 'source_key': 'synthetic-expired'})
    engine.register_executor('synthetic-worker', ['profit'], identity)
    token = engine.claim(task['task_id'], 'synthetic-worker', ttl=1)
    assert token
    if external_started:
        with engine.transaction() as connection:
            connection.execute('UPDATE workbench_execution SET external_started=1 WHERE task_id=?', (task['task_id'],))
    now[0] = 102.0
    profile = RuntimeProfile(tmp_path, tmp_path, environment, identity['code_version'], identity['manifest_digest'])
    runtime = SimpleNamespace(engine=engine, profile=profile, worker_enabled=False,
                              worker=SimpleNamespace(status=lambda: {'running': False, 'state': 'stopped'}))
    request = SimpleNamespace(path='/api/orbit/operations-runtime', headers=Message(),
                              client_address=('127.0.0.1', 1), server=SimpleNamespace(server_port=0), responses=[])
    request.headers['Host'] = '127.0.0.1:0'
    request._json = lambda code, value: request.responses.append((code, value))
    return engine, task, runtime, request


def persisted_state(path, task_id):
    with sqlite3.connect(path) as connection:
        return (connection.execute('SELECT state,worker,lease_token,lease_until FROM workbench_execution WHERE task_id=?', (task_id,)).fetchone(),
                connection.execute("SELECT count(*) FROM workbench_events WHERE event_type='lease_expired'").fetchone()[0])


@pytest.mark.parametrize('external_started', [False, True])
def test_preview_runtime_GET_preserves_expired_task_and_ledger_bytes(tmp_path, external_started):
    engine, task, runtime, request = runtime_fixture(tmp_path, 'preview', external_started)
    before = engine.store.path.read_bytes()
    state = persisted_state(engine.store.path, task['task_id'])
    assert handle(request, method='GET', runtime=runtime)
    assert request.responses[0][0] == 200
    assert engine.store.path.read_bytes() == before, 'preview runtime GET changed the expired task ledger'
    assert persisted_state(engine.store.path, task['task_id']) == state
    assert state[0][0] == 'running' and state[1] == 0


@pytest.mark.parametrize('external_started,expected', [(False, 'queued'), (True, 'reconciliation_required')])
def test_stable_runtime_GET_retains_existing_expiry_maintenance(tmp_path, external_started, expected):
    engine, task, runtime, request = runtime_fixture(tmp_path, 'stable', external_started)
    assert handle(request, method='GET', runtime=runtime)
    assert request.responses[0][0] == 200
    state, expired_events = persisted_state(engine.store.path, task['task_id'])
    assert state[0] == expected and state[1:] == (None, None, None)
    assert expired_events == 1


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_preview_runtime_GET_refuses_uncertain_snapshot_without_source_write(tmp_path, suffix):
    engine, task, runtime, request = runtime_fixture(tmp_path, 'preview')
    before = engine.store.path.read_bytes()
    sidecar = Path(str(engine.store.path) + suffix)
    sidecar.write_bytes(b'synthetic-uncertain-snapshot')
    assert handle(request, method='GET', runtime=runtime)
    assert request.responses[0][0] == 503
    assert request.responses[0][1]['code'] == 'READONLY_RUNTIME_SNAPSHOT_UNAVAILABLE'
    assert engine.store.path.read_bytes() == before
    assert sidecar.read_bytes() == b'synthetic-uncertain-snapshot'


def test_preview_runtime_GET_missing_source_is_not_created(tmp_path):
    engine, task, runtime, request = runtime_fixture(tmp_path, 'preview')
    engine.store.path = tmp_path / 'never-created.db'
    assert handle(request, method='GET', runtime=runtime)
    assert request.responses[0][0] == 503
    assert not engine.store.path.exists()


def test_preview_runtime_GET_missing_schema_is_not_initialized(tmp_path):
    engine, task, runtime, request = runtime_fixture(tmp_path, 'preview')
    path = tmp_path / 'uninitialized.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE synthetic_marker(value TEXT)')
    engine.store.path = path
    before = path.read_bytes()
    assert handle(request, method='GET', runtime=runtime)
    assert request.responses[0][0] == 503
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('synthetic_marker',)]


def test_preview_status_uses_read_snapshot_and_preserves_current_executor_guard(tmp_path, monkeypatch):
    engine, task, runtime, request = runtime_fixture(tmp_path, 'preview')
    def reject(*args, **kwargs):
        pytest.fail('preview status used a write/expiry dashboard consumer')
    monkeypatch.setattr(engine, 'transaction', reject)
    monkeypatch.setattr(engine, '_expire', reject)
    monkeypatch.setattr(engine, 'dashboard', reject)
    value = engine.read_runtime_status()
    assert value['executor'] == {'connected': True, 'templates': ['profit']}
    assert value['domain_guard'] == {'unresolved_operation_count': 0, 'unattached_operation_count': 0, 'locked_resource_count': 0}
    assert handle(request, method='GET', runtime=runtime)
    assert request.responses[0][0] == 200
