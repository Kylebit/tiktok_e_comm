"""Profit UNKNOWN readback must not dispatch another agent or producer."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.workbench_profit_adapter import adapter, inspect_unknown_attempt


def _case(tmp_path):
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'fixture'})
    engine.register_executor('worker', ['profit'], engine.release, ttl=300)
    task = engine.create({'template': 'profit', 'source_key': 'profit-unknown',
                          'scope': {'month': '2026-08', 'platforms': ['tiktok'],
                                    'sites': ['MY'], 'shops': ['shop-MY']}})
    token = engine.claim(task['task_id'], 'worker')['lease_token']
    return engine, task['task_id'], token, SimpleNamespace(data_root=tmp_path)


def test_crash_after_agent_output_remains_unknown_and_readback_is_read_only(tmp_path):
    engine, task_id, token, profile = _case(tmp_path)
    calls = []

    class FakeAgent:
        def execute_monthly(self, task, output, notes):
            calls.append(str(output))
            output.mkdir(parents=True)
            (output / 'agent-result.json').write_text('{"summary":"candidate"}', encoding='utf-8')
            raise SystemExit('simulated crash before agent receipt')

    run = adapter(FakeAgent())
    with pytest.raises(SystemExit):
        run(engine, engine.get(task_id), token, profile)
    task = engine.get(task_id)
    owner = task['checkpoint']['attempt_receipt']
    assert owner['schema_version'] == 'profit-agent-attempt/v1'
    assert owner['task_id'] == task_id
    assert owner['release'] == task['version']
    assert owner['output_dir'] == calls[0]
    assert owner['attempt_id'] in Path(calls[0]).name
    receipt = inspect_unknown_attempt(task, profile)
    assert receipt['status'] == 'UNKNOWN'
    assert receipt['attempt_id'] == owner['attempt_id']
    assert receipt['session_readback'] == 'NOT_RECORDED'
    assert receipt['agent_result_file']['sha256'] == hashlib.sha256(
        (Path(calls[0]) / 'agent-result.json').read_bytes()).hexdigest()
    assert receipt['agent_result_file']['ownership'] == 'UNVERIFIED'
    # Do not turn the original candidate output into a new agent attempt.
    run(engine, task, token, profile)
    assert len(calls) == 1
    assert engine.get(task_id)['execution_state'] == 'failed'


def test_timeout_session_id_is_hint_not_authoritative_completion(tmp_path):
    engine, task_id, token, profile = _case(tmp_path)
    class FakeAgent:
        def execute_monthly(self, task, output, notes):
            return {'status': 'unknown', 'reason': 'timeout', 'session_id': 'synthetic-session'}
    adapter(FakeAgent())(engine, engine.get(task_id), token, profile)
    receipt = inspect_unknown_attempt(engine.get(task_id), profile)
    assert receipt['status'] == 'UNKNOWN'
    assert receipt['session_id'] == 'synthetic-session'
    assert receipt['session_readback'] == 'REPORTED_UNVERIFIED'
    assert receipt['agent_result_file'] is None


def test_crash_before_agent_start_has_durable_receipt_and_no_replay(tmp_path):
    engine, task_id, token, profile = _case(tmp_path)
    calls = []
    class FakeAgent:
        def execute_monthly(self, task, output, notes):
            calls.append(str(output))
            raise SystemExit('crash before output directory creation')
    run = adapter(FakeAgent())
    with pytest.raises(SystemExit):
        run(engine, engine.get(task_id), token, profile)
    checkpoint = engine.get(task_id)['checkpoint']
    assert checkpoint['attempt_receipt']['output_dir'] == calls[0]
    assert not Path(calls[0]).exists()
    assert inspect_unknown_attempt(engine.get(task_id), profile)['status'] == 'UNKNOWN'
    run(engine, engine.get(task_id), token, profile)
    assert len(calls) == 1


def test_readback_rejects_checkpoint_path_outside_exact_task_attempt(tmp_path):
    engine, task_id, token, profile = _case(tmp_path)
    engine.record_checkpoint(task_id, token, {'agent_attempt_started': True,
        'attempt_output': str(tmp_path / 'other-task' / 'monthly-forged')})
    receipt = inspect_unknown_attempt(engine.get(task_id), profile)
    assert receipt['status'] == 'BLOCKED'
    assert receipt['agent_result_file'] is None


def test_readback_rejects_another_release_identity(tmp_path):
    engine, task_id, token, profile = _case(tmp_path)
    engine.record_checkpoint(task_id, token, {'agent_attempt_started': True,
        'attempt_output': str(tmp_path / 'artifacts' / task_id / ('monthly-' + token))})
    profile.version = 'different-release'
    profile.environment = 'stable'
    profile.manifest_digest = ''
    assert inspect_unknown_attempt(engine.get(task_id), profile)['status'] == 'BLOCKED'


@pytest.mark.parametrize('field,value', [
    ('task_id', 'TASK-another'), ('release', {'code_version': 'different'}),
    ('scope_sha256', '0' * 64), ('attempt_id', 'forged'),
])
def test_readback_rejects_rebound_attempt_receipt(tmp_path, field, value):
    engine, task_id, token, profile = _case(tmp_path)
    class FakeAgent:
        def execute_monthly(self, task, output, notes):
            raise SystemExit('synthetic crash')
    with pytest.raises(SystemExit):
        adapter(FakeAgent())(engine, engine.get(task_id), token, profile)
    checkpoint = engine.get(task_id)['checkpoint']
    checkpoint['attempt_receipt'][field] = value
    engine.record_checkpoint(task_id, token, checkpoint)
    rejected = inspect_unknown_attempt(engine.get(task_id), profile)
    assert rejected['status'] == 'BLOCKED'
    assert rejected['attempt_output'] is None
    assert rejected['session_id'] is None


def test_concurrent_duplicate_adapter_call_launches_only_one_agent(tmp_path):
    engine, task_id, token, profile = _case(tmp_path)
    started, release = Event(), Event()
    calls = []
    class FakeAgent:
        def execute_monthly(self, task, output, notes):
            calls.append(str(output))
            started.set()
            assert release.wait(5)
            raise SystemExit('synthetic exit')
    run = adapter(FakeAgent())
    stale = engine.get(task_id)
    def invoke():
        try:
            run(engine, stale, token, profile)
        except SystemExit:
            return 'crashed'
        return 'skipped'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke)
        assert started.wait(5)
        second = pool.submit(invoke)
        assert second.result(timeout=5) == 'skipped'
        release.set()
        assert first.result(timeout=5) == 'crashed'
    assert len(calls) == 1
    assert inspect_unknown_attempt(engine.get(task_id), profile)['status'] == 'UNKNOWN'
