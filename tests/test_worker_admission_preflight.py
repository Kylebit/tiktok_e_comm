"""Read-only admission preview; never starts an operations worker."""

import json
import sqlite3
from pathlib import Path

from scripts import worker_admission_preflight


def _fixture(tmp_path):
    root = tmp_path / 'pinned-code'
    root.mkdir()
    data = tmp_path / 'operations'
    data.mkdir()
    database = data / 'tasks.db'
    release = {'code_version': 'a' * 40, 'environment': 'stable', 'manifest_digest': 'digest'}
    with sqlite3.connect(database) as conn:
        conn.executescript('''
            CREATE TABLE workbench_tasks(task_id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
            CREATE TABLE workbench_execution(task_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                version_json TEXT NOT NULL);
            CREATE TABLE workbench_external_tasks(task_id TEXT PRIMARY KEY, external_json TEXT NOT NULL);
            CREATE TABLE workbench_domain_operations(operation_id TEXT PRIMARY KEY, state TEXT NOT NULL);
        ''')
        for task_id, created_at, state, version in [
            ('TASK-old', '2026-09-21T00:00:00+00:00', 'queued', release),
            ('TASK-external', '2026-09-21T00:01:00+00:00', 'queued', release),
            ('TASK-other', '2026-09-21T00:02:00+00:00', 'queued',
             {**release, 'code_version': 'b' * 40}),
        ]:
            conn.execute('INSERT INTO workbench_tasks VALUES(?,?)', (task_id, created_at))
            conn.execute('INSERT INTO workbench_execution VALUES(?,?,?)',
                         (task_id, state, json.dumps(version)))
        conn.execute('INSERT INTO workbench_external_tasks VALUES(?,?)',
                     ('TASK-external', '{}'))
    deployment = tmp_path / 'deployment.json'
    deployment.write_text(json.dumps({'code_root': str(root), 'code_version': release['code_version'],
                                      'manifest_digest': 'digest', 'operations_data_root': str(data),
                                      'execution_mode': 'worker-only'}), encoding='utf-8')
    agent = tmp_path / 'codex.exe'
    agent.write_bytes(b'synthetic-agent-not-run')
    return deployment, agent, database, release


def _patch_preflight(monkeypatch, database):
    calls = []
    monkeypatch.setattr(worker_admission_preflight, 'preflight_deployment',
                        lambda config, root: calls.append((config, root)) or
                        {'operations_data_root': database.parent})
    monkeypatch.setattr(worker_admission_preflight, 'runtime_manifest', lambda root: 'digest')
    return calls


def test_preview_enumerates_old_tasks_without_creating_admission_or_running_worker(tmp_path, monkeypatch):
    deployment, agent, database, _ = _fixture(tmp_path)
    calls = _patch_preflight(monkeypatch, database)
    before = database.read_bytes()
    result = worker_admission_preflight.assess(deployment, agent)
    assert calls and calls[0][1] == Path(json.loads(deployment.read_text())['code_root'])
    assert result['ok'] is True
    assert result['worker_started'] is False
    assert result['admission']['record_exists'] is False
    assert result['ledger']['same_release_open_task_ids'] == ['TASK-external', 'TASK-old']
    assert result['ledger']['external_task_count'] == 1
    assert result['ledger']['unresolved_domain_operation_count'] == 0
    assert database.read_bytes() == before
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_worker_admissions'").fetchone() is None


def test_preview_existing_boundary_and_unresolved_operation_are_reported(tmp_path, monkeypatch):
    deployment, agent, database, release = _fixture(tmp_path)
    _patch_preflight(monkeypatch, database)
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE workbench_worker_admissions(version_json TEXT PRIMARY KEY, cutoff_at TEXT NOT NULL)')
        conn.execute('INSERT INTO workbench_worker_admissions VALUES(?,?)',
                     (json.dumps(release, sort_keys=True, separators=(',', ':')),
                      '2026-09-21T00:00:30+00:00'))
        conn.execute('INSERT INTO workbench_domain_operations VALUES(?,?)', ('op-unresolved', 'inflight'))
    result = worker_admission_preflight.assess(deployment, agent)
    assert result['admission']['record_exists'] is True
    assert result['admission']['cutoff_at'] == '2026-09-21T00:00:30+00:00'
    assert result['ledger']['same_release_admitted_open_task_ids'] == ['TASK-external']
    assert result['ledger']['unresolved_domain_operation_count'] == 1
    assert 'UNRESOLVED_DOMAIN_OPERATIONS' in result['blockers']


def test_preview_rejects_invalid_agent_and_manifest_without_touching_ledger(tmp_path, monkeypatch):
    deployment, agent, database, _ = _fixture(tmp_path)
    _patch_preflight(monkeypatch, database)
    config = json.loads(deployment.read_text())
    config['manifest_digest'] = 'wrong'
    deployment.write_text(json.dumps(config), encoding='utf-8')
    before = database.read_bytes()
    result = worker_admission_preflight.assess(deployment, tmp_path / 'missing.exe')
    assert result['ok'] is False
    assert 'MANIFEST_MISMATCH' in result['blockers']
    assert 'AGENT_EXECUTABLE_INVALID' in result['blockers']
    assert database.read_bytes() == before


def test_web_only_deployment_is_not_worker_ready(tmp_path, monkeypatch):
    deployment, agent, database, _ = _fixture(tmp_path)
    _patch_preflight(monkeypatch, database)
    config = json.loads(deployment.read_text())
    config['execution_mode'] = 'web-only'
    deployment.write_text(json.dumps(config), encoding='utf-8')
    before = database.read_bytes()
    result = worker_admission_preflight.assess(deployment, agent)
    assert result['ok'] is False
    assert result['blockers'] == ['DEPLOYMENT_NOT_WORKER_ONLY']
    assert database.read_bytes() == before


def test_cli_emits_json_and_fails_closed_on_deployment_preflight(tmp_path, monkeypatch, capsys):
    deployment, agent, database, _ = _fixture(tmp_path)
    before = database.read_bytes()

    def reject(*args):
        raise ValueError('deployment candidate must be clean')

    monkeypatch.setattr(worker_admission_preflight, 'preflight_deployment', reject)
    code = worker_admission_preflight.main([
        '--deployment', str(deployment), '--agent-executable', str(agent)])
    body = json.loads(capsys.readouterr().out)
    assert code == 2
    assert body['worker_started'] is False
    assert body['blockers'] == ['DEPLOYMENT_PREFLIGHT_FAILED']
    assert body['ledger'] is None
    assert database.read_bytes() == before
