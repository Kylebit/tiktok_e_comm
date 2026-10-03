"""Worker-only launch planning must not open a server or start a worker."""

import json
import sqlite3

from scripts import operations_worker_entry


def _deployment(tmp_path):
    root = tmp_path / 'sealed-code'
    root.mkdir()
    data = tmp_path / 'operations'
    data.mkdir()
    database = data / 'tasks.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE marker(value TEXT)')
        conn.execute("INSERT INTO marker VALUES('unchanged')")
    config = {'code_root': str(root), 'code_version': 'a' * 40,
              'manifest_digest': 'pinned', 'operations_data_root': str(data),
              'execution_mode': 'worker-only'}
    deployment = tmp_path / 'worker.json'
    deployment.write_text(json.dumps(config), encoding='utf-8')
    agent = tmp_path / 'codex.exe'
    agent.write_bytes(b'synthetic-not-run')
    return root, database, deployment, agent, config


def _assessment(config):
    return {'ok': True, 'blockers': [], 'worker_started': False,
            'release': {'code_version': config['code_version'], 'environment': 'stable',
                        'manifest_digest': config['manifest_digest']},
            'admission': {'record_exists': False, 'cutoff_at': None},
            'ledger': {'same_release_open_task_ids': ['TASK-old'],
                       'same_release_admitted_open_task_ids': [],
                       'external_task_count': 1, 'unresolved_domain_operation_count': 0}}


def test_plan_checks_sealed_identity_and_keeps_legacy_queue_unadmitted(tmp_path, monkeypatch):
    root, database, deployment, agent, config = _deployment(tmp_path)
    monkeypatch.setattr(operations_worker_entry, 'ROOT', root)
    called = []
    monkeypatch.setattr(operations_worker_entry.admission_preflight, 'assess',
                        lambda path, cli: called.append((path, cli)) or _assessment(config))
    before = database.read_bytes()
    result = operations_worker_entry.plan(deployment, agent)
    assert called == [(deployment, agent)]
    assert result['ok'] is True
    assert result['worker_started'] is False
    assert result['http_listener_started'] is False
    assert result['activation_available'] is False
    assert result['existing_tasks_require_explicit_resume'] == ['TASK-old']
    assert result['resume_task_ids'] == []
    assert database.read_bytes() == before
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_worker_admissions'").fetchone() is None


def test_plan_rejects_foreign_code_root_before_ledger_read(tmp_path, monkeypatch):
    root, _, deployment, agent, _ = _deployment(tmp_path)
    monkeypatch.setattr(operations_worker_entry, 'ROOT', tmp_path / 'other-code')
    monkeypatch.setattr(operations_worker_entry.admission_preflight, 'assess',
                        lambda *args: (_ for _ in ()).throw(AssertionError('ledger read')))
    result = operations_worker_entry.plan(deployment, agent)
    assert not result['ok']
    assert 'ENTRY_CODE_ROOT_MISMATCH' in result['blockers']


def test_plan_propagates_manifest_and_agent_blockers(tmp_path, monkeypatch):
    root, _, deployment, agent, config = _deployment(tmp_path)
    monkeypatch.setattr(operations_worker_entry, 'ROOT', root)
    failed = {**_assessment(config), 'ok': False,
              'blockers': ['MANIFEST_MISMATCH', 'AGENT_EXECUTABLE_INVALID']}
    monkeypatch.setattr(operations_worker_entry.admission_preflight, 'assess',
                        lambda *args: failed)
    result = operations_worker_entry.plan(deployment, agent)
    assert not result['ok']
    assert result['blockers'] == ['MANIFEST_MISMATCH', 'AGENT_EXECUTABLE_INVALID']


def test_plan_rejects_web_mode_and_implicit_old_task_resume(tmp_path, monkeypatch):
    root, _, deployment, agent, config = _deployment(tmp_path)
    monkeypatch.setattr(operations_worker_entry, 'ROOT', root)
    monkeypatch.setattr(operations_worker_entry.admission_preflight, 'assess',
                        lambda *args: (_ for _ in ()).throw(AssertionError('should fail earlier')))
    config['execution_mode'] = 'web-only'
    config['resume_task_ids'] = ['TASK-old']
    deployment.write_text(json.dumps(config), encoding='utf-8')
    result = operations_worker_entry.plan(deployment, agent)
    assert not result['ok']
    assert 'DEPLOYMENT_NOT_WORKER_ONLY' in result['blockers']
    assert 'IMPLICIT_RESUME_NOT_ALLOWED' in result['blockers']


def test_run_flag_is_explicitly_disabled_and_emits_json(tmp_path, monkeypatch, capsys):
    root, database, deployment, agent, config = _deployment(tmp_path)
    monkeypatch.setattr(operations_worker_entry, 'ROOT', root)
    monkeypatch.setattr(operations_worker_entry.admission_preflight, 'assess',
                        lambda *args: _assessment(config))
    before = database.read_bytes()
    code = operations_worker_entry.main([
        '--deployment', str(deployment), '--agent-executable', str(agent), '--run'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload['worker_started'] is False
    assert 'WORKER_ACTIVATION_NOT_AVAILABLE' in payload['blockers']
    assert database.read_bytes() == before
