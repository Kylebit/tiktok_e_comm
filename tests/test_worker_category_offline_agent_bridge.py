"""Offline parent-owned R1 bridge; isolated ledgers and fake official GETs."""

import json
import multiprocessing
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.products import server
from shared_platform import operations_runtime
from shared_platform.operations_runtime import (
    ControlledAgentBridge, RuntimeProfile, _parse_codex_final_jsonl)
from shared_platform.release_store import ReleaseStore
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_bridge import WorkerCategoryBridge
from shared_platform.worker_category_facts_transport import VerifiedFactsCategoryTransport
from shared_platform.worker_category_parent_flow import ParentR1CategoryFlow
from test_round1_category_observer_http import request
from test_round1_initial_options import initial
from test_worker_category_bridge import fixture
from test_worker_category_parent_flow import _selection


def _bound(engine, store, worker_id='worker'):
    domain = WorkerCategoryBridge(engine, store,
        lambda action, body, origin: server._round1_category_initial_request(
            action, body, worker_origin=origin))
    transport = VerifiedFactsCategoryTransport(engine, store, worker_id=worker_id)
    parent = ParentR1CategoryFlow(domain, transport, worker_id=worker_id)
    profile = RuntimeProfile(engine.store.path.parent, engine.store.path.parent / 'operations',
                             'stable', engine.release['code_version'],
                             engine.release['manifest_digest'])
    return ControlledAgentBridge('fake-codex', profile,
        category_transport=transport, category_parent_flow=parent)


def _context(body):
    return {key: body[key] for key in (
        'context_digest', 'offer_id', 'product_center_revision',
        'requested_targets', 'source_region', 'account_identity_digest')}


def _agent_events(body):
    return '\n'.join(json.dumps(event) for event in (
        {'type': 'thread.started', 'thread_id': 'fixture-session'},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {
            'id': 'fixture-final', 'type': 'agent_message',
            'text': json.dumps(body)}},
        {'type': 'turn.completed', 'usage': {}})) + '\n'


@pytest.mark.parametrize('events', [
    '',
    '{bad json}\n',
    _agent_events({'selection': None, 'missing_inputs': []}).replace(
        '"type": "turn.completed"', '"type": "turn.failed"'),
    _agent_events({'selection': None, 'missing_inputs': []}).replace(
        '{"type": "turn.completed", "usage": {}}\n', ''),
    _agent_events({'selection': None, 'missing_inputs': []}) +
        '{"type": "error", "message": "late failure"}\n',
])
def test_codex_jsonl_rejects_incomplete_or_failed_turn(events):
    with pytest.raises(ValueError):
        _parse_codex_final_jsonl(events)


def test_codex_jsonl_accepts_one_completed_structured_message():
    body = {'selection': None, 'missing_inputs': ['source facts']}
    assert _parse_codex_final_jsonl(_agent_events(body)) == json.dumps(body)


def test_codex_jsonl_uses_last_agent_message_after_progress():
    body = {'selection': None, 'missing_inputs': ['source facts']}
    events = _agent_events(body).replace(
        '{"type": "turn.started"}\n',
        '{"type": "turn.started"}\n'
        '{"type": "item.completed", "item": {"id": "progress", '
        '"type": "agent_message", "text": "Working"}}\n')
    assert _parse_codex_final_jsonl(events) == json.dumps(body)


def _fake_agent(monkeypatch, *, marker=None, crash_stage=None):
    calls = []
    def run(argv, *, input, **kwargs):
        calls.append((argv, input))
        schema = Path(argv[argv.index('--output-schema') + 1])
        stage = 'choice' if schema.name == 'choice-result.schema.json' else 'facts'
        if marker is not None:
            with open(marker, 'a', encoding='utf-8') as stream:
                stream.write(stage + '\n')
                stream.flush(); os.fsync(stream.fileno())
        if stage == 'choice':
            projection = json.loads((schema.parent / 'trusted-r1-category-options.json').read_text(encoding='utf-8'))
            body = {'selection': _selection(projection), 'missing_inputs': []}
        else:
            body = {'summary': 'fixture facts', 'missing_inputs': [], 'evidence_paths': []}
        if stage == crash_stage:
            os._exit(43 if stage == 'facts' else 44)
        return SimpleNamespace(returncode=0, stdout=_agent_events(body),
                               overflow=False, timed_out=False)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    def read_only(argv, prompt, *, cwd, timeout):
        return run(argv, input=prompt, cwd=cwd, timeout=timeout)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        read_only)
    return calls


def _case(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr('shared_platform.operations_runtime.runtime_matches',
                        lambda _: True)
    monkeypatch.setattr('shared_platform.operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED',
                        True)
    return engine, task_id, lease, _context(body), fake, store, _bound(engine, store)


@pytest.mark.parametrize('link_kind', ['symlink', 'junction'])
@pytest.mark.parametrize('location', ['output', 'ancestor'])
def test_default_r1_boundary_blocks_linked_output_before_any_work(
        tmp_path, monkeypatch, link_kind, location):
    if link_kind == 'junction' and os.name != 'nt':
        pytest.skip('Windows junction only')
    engine, task_id, lease, body, fake, store, _ = fixture(tmp_path, monkeypatch)
    bridge = _bound(engine, store)
    outside = tmp_path / 'outside-output'
    outside.mkdir()
    sentinel = outside / 'sentinel.txt'
    sentinel.write_text('untouched', encoding='utf-8')
    bridge.profile.data_root.mkdir(parents=True)
    linked = bridge.profile.data_root / 'linked'
    output = linked if location == 'output' else linked / 'child'
    if link_kind == 'symlink':
        linked.symlink_to(outside, target_is_directory=True)
    else:
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J',
                        str(linked), str(outside)], check=True, capture_output=True)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run',
                        lambda *args, **kwargs: pytest.fail('CLI launched behind R1 gate'))
    choice = bridge.execute_facts_offline(engine.get(task_id), output, [],
        lease_token=lease, category_context=_context(body))
    facts = bridge.execute_facts(engine.get(task_id), output, [],
        lease_token=lease)
    assert choice == facts == {'status': 'blocked',
                               'reason': 'r1_filesystem_boundary_unverified'}
    assert fake.calls == [] and not store.path.exists()
    assert sentinel.read_text(encoding='utf-8') == 'untouched'
    assert sorted(outside.iterdir()) == [sentinel]
    assert sorted(bridge.profile.data_root.iterdir()) == [linked]


def test_offline_bridge_options_choice_capture_and_facts_without_web_url(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'facts-1', [], lease_token=lease,
        category_context=context)
    assert result['status'] == 'observed' and len(calls) == 2
    assert result['path'] is None
    task_output = bridge.profile.data_root / 'facts-1' / task_id
    assert not (task_output / 'category-choice' / 'choice-result.json').exists()
    assert not (task_output / 'agent-result.json').exists()
    assert len(fake.calls) == 7
    assert all('127.0.0.1' not in prompt and 'service_url' not in prompt
               and lease not in prompt for _, prompt in calls)
    evidence = json.loads(Path(result['category_evidence']['path']).read_text(encoding='utf-8'))
    assert evidence['task_id'] == task_id
    assert evidence['observer_reference'] == evidence['observation']['observer_reference']
    assert store.category_worker_origin(evidence['capture_request_id'])['task_id'] == task_id
    again = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'facts-2', [], lease_token=lease,
        category_context=context)
    assert again == {'status': 'unknown',
                     'reason': 'category_choice_attempt_exists_reconcile',
                     'attempt_output': str(bridge.profile.data_root / 'facts-1' / task_id)}
    assert len(calls) == 2 and len(fake.calls) == 7
    assert 'not the first available option by default' in calls[0][1]
    assert all('--output-last-message' not in argv for argv, _ in calls)
    facts_argv, facts_prompt = calls[1]
    assert facts_argv[facts_argv.index('--sandbox') + 1] == 'read-only'
    assert '--add-dir' not in facts_argv and '--skip-git-repo-check' not in facts_argv
    assert 'Do not create or modify sidecars' in facts_prompt


@pytest.mark.parametrize('leaf', [
    'trusted-r1-category-options.json', 'choice-attempt.json',
    'choice-result.schema.json', 'choice-result.json'])
@pytest.mark.parametrize('link_kind', ['symlink', 'hardlink'])
def test_preexisting_choice_file_link_never_writes_outside_root(
        tmp_path, monkeypatch, leaf, link_kind):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    outside = tmp_path / 'outside-choice.json'
    outside.write_text('untouched', encoding='utf-8')
    output = bridge.profile.data_root / 'linked-choice'
    choice_dir = output / task_id / 'category-choice'
    choice_dir.mkdir(parents=True)
    linked = choice_dir / leaf
    if link_kind == 'symlink':
        linked.symlink_to(outside)
    else:
        os.link(outside, linked)

    result = bridge.execute_facts_offline(engine.get(task_id), output, [],
        lease_token=lease, category_context=context)
    assert result['status'] == ('observed' if leaf == 'choice-result.json' else 'unknown')
    assert outside.read_text(encoding='utf-8') == 'untouched'
    assert len(calls) == (2 if leaf == 'choice-result.json' else 0)
    retry = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'different-choice', [],
        lease_token=lease, category_context=context)
    assert retry['reason'] == 'category_choice_attempt_exists_reconcile'
    assert len(calls) == (2 if leaf == 'choice-result.json' else 0)


@pytest.mark.parametrize('leaf', [
    'trusted-r1-category-capture.json', 'agent-result.schema.json',
    'agent-result.json'])
@pytest.mark.parametrize('link_kind', ['symlink', 'hardlink'])
def test_preexisting_facts_file_link_never_writes_outside_root(
        tmp_path, monkeypatch, leaf, link_kind):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    outside = tmp_path / 'outside-facts.json'
    outside.write_text('untouched', encoding='utf-8')
    output = bridge.profile.data_root / 'linked-facts'
    facts_dir = output / task_id
    facts_dir.mkdir(parents=True)
    linked = facts_dir / leaf
    if link_kind == 'symlink':
        linked.symlink_to(outside)
    else:
        os.link(outside, linked)

    result = bridge.execute_facts_offline(engine.get(task_id), output, [],
        lease_token=lease, category_context=context)
    assert result['status'] == ('observed' if leaf == 'agent-result.json' else 'unknown')
    assert outside.read_text(encoding='utf-8') == 'untouched'
    assert len(calls) == (2 if leaf == 'agent-result.json' else 1)
    retry = bridge.execute_facts(engine.get(task_id),
        bridge.profile.data_root / 'different-facts', [], lease_token=lease)
    assert retry['reason'] == 'category_facts_attempt_exists_reconcile'
    assert len(calls) == (2 if leaf == 'agent-result.json' else 1)


@pytest.mark.parametrize('schema_name,result_parts', [
    ('choice-result.schema.json', ('category-choice', 'choice-result.json')),
    ('agent-result.schema.json', ('agent-result.json',)),
])
def test_concurrent_hardlink_swap_cannot_redirect_cli_result_write(
        tmp_path, monkeypatch, schema_name, result_parts):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    fake_run = operations_runtime.subprocess.run
    outside = tmp_path / 'outside-sentinel.json'
    outside.write_text('untouched', encoding='utf-8')
    output = bridge.profile.data_root / 'concurrent-swap'
    result_path = output / task_id / Path(*result_parts)
    start_swap, swapped = threading.Event(), threading.Event()

    def attacker():
        if start_swap.wait(5):
            result_path.unlink(missing_ok=True)
            os.link(outside, result_path)
            swapped.set()

    attack = threading.Thread(target=attacker)
    attack.start()
    def run(argv, *, input, **kwargs):
        if Path(argv[argv.index('--output-schema') + 1]).name == schema_name:
            start_swap.set()
            assert swapped.wait(5)
            # Model the CLI's std::fs::write if the legacy output flag returns.
            if '--output-last-message' in argv:
                Path(argv[argv.index('--output-last-message') + 1]).write_text(
                    'CLI wrote outside', encoding='utf-8')
        return fake_run(argv, input=input, **kwargs)

    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        lambda argv, prompt, **kwargs: run(argv, input=prompt, **kwargs))
    try:
        result = bridge.execute_facts_offline(engine.get(task_id), output, [],
            lease_token=lease, category_context=context)
    finally:
        start_swap.set()
        attack.join(5)
    assert result['status'] == 'observed' and len(calls) == 2
    assert outside.read_text(encoding='utf-8') == 'untouched'
    assert result_path.read_text(encoding='utf-8') == 'untouched'


@pytest.mark.parametrize('stream,expected', [
    ('', 'category_choice_event_stream_unverified'),
    ('[]\n', 'category_choice_event_stream_unverified'),
    ('{"type":"thread.started","thread_id":"fixture"}\n',
     'category_choice_event_stream_unverified'),
    (_agent_events({'selection': None, 'missing_inputs': []}) +
     '{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}\n',
     'category_choice_event_stream_unverified'),
])
def test_choice_incomplete_stream_never_captures_or_relaunches(
        tmp_path, monkeypatch, stream, expected):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    fake_run = operations_runtime.subprocess.run
    def run(argv, *, input, **kwargs):
        answer = fake_run(argv, input=input, **kwargs)
        return SimpleNamespace(returncode=0, stdout=stream,
                               overflow=False, timed_out=False)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'bad-stream', [], lease_token=lease,
        category_context=context)
    assert result['status'] == 'unknown' and result['reason'] == expected
    assert len(calls) == 1 and len(fake.calls) == 5
    retry = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'retry-stream', [], lease_token=lease,
        category_context=context)
    assert retry['reason'] == 'category_choice_attempt_exists_reconcile'
    assert len(calls) == 1


def test_choice_nonzero_exit_rejects_even_complete_message(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    fake_run = operations_runtime.subprocess.run
    def run(argv, *, input, **kwargs):
        answer = fake_run(argv, input=input, **kwargs)
        return SimpleNamespace(returncode=7, stdout=answer.stdout,
                               overflow=False, timed_out=False)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'nonzero-choice', [], lease_token=lease,
        category_context=context)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'category_choice_agent_failed'
    assert result['session_id'] == 'fixture-session'
    assert len(calls) == 1 and len(fake.calls) == 5


def test_facts_empty_final_result_is_unknown(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    fake_run = operations_runtime.subprocess.run
    def run(argv, *, input, **kwargs):
        answer = fake_run(argv, input=input, **kwargs)
        if Path(argv[argv.index('--output-schema') + 1]).name == 'agent-result.schema.json':
            return SimpleNamespace(returncode=0, stdout=_agent_events(''),
                                   overflow=False, timed_out=False)
        return answer
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        lambda argv, prompt, **kwargs: run(argv, input=prompt, **kwargs))
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'empty-facts', [], lease_token=lease,
        category_context=context)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'category_facts_result_invalid'
    assert len(calls) == 2
    retry = bridge.execute_facts(engine.get(task_id),
        bridge.profile.data_root / 'different-facts', [], lease_token=lease)
    assert retry['reason'] == 'category_facts_attempt_exists_reconcile'
    assert len(calls) == 2


def test_facts_missing_completion_event_is_unknown(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    fake_run = operations_runtime.subprocess.run
    def run(argv, *, input, **kwargs):
        answer = fake_run(argv, input=input, **kwargs)
        if Path(argv[argv.index('--output-schema') + 1]).name == 'agent-result.schema.json':
            return SimpleNamespace(returncode=0, stdout=answer.stdout.rsplit('\n', 2)[0] + '\n',
                                   overflow=False, timed_out=False)
        return answer
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        lambda argv, prompt, **kwargs: run(argv, input=prompt, **kwargs))
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'incomplete-facts', [], lease_token=lease,
        category_context=context)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'category_facts_event_stream_unverified'
    assert result['session_id'] == 'fixture-session'
    assert len(calls) == 2


def test_direct_facts_stage_is_one_shot_across_output_directories(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'complete', [], lease_token=lease,
        category_context=context)
    assert result['status'] == 'observed' and len(calls) == 2
    second = bridge.execute_facts(engine.get(task_id),
        bridge.profile.data_root / 'direct-retry', [], lease_token=lease)
    assert second == {'status': 'unknown',
                      'reason': 'category_facts_attempt_exists_reconcile',
                      'attempt_output': str(bridge.profile.data_root / 'complete' / task_id)}
    assert len(calls) == 2 and len(fake.calls) == 7


def test_new_valid_lease_cannot_relaunch_either_agent_stage(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    assert bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'old-lease', [], lease_token=lease,
        category_context=context)['status'] == 'observed'
    with engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET lease_until=? WHERE task_id=?',
                     (engine.clock() - 1, task_id))
    fresh_lease = engine.claim(task_id, 'worker')['lease_token']
    assert fresh_lease != lease
    choice = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'new-lease-choice', [],
        lease_token=fresh_lease, category_context=context)
    facts = bridge.execute_facts(engine.get(task_id),
        bridge.profile.data_root / 'new-lease-facts', [],
        lease_token=fresh_lease)
    assert choice['status'] == facts['status'] == 'unknown'
    assert choice['reason'] == 'category_choice_attempt_exists_reconcile'
    assert facts['reason'] == 'category_facts_attempt_exists_reconcile'
    assert len(calls) == 2 and len(fake.calls) == 7


def test_offline_runtime_drift_blocks_before_options_or_child(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    monkeypatch.setattr('shared_platform.operations_runtime.runtime_matches',
                        lambda _: False)
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'drift', [], lease_token=lease,
        category_context=context)
    assert result == {'status': 'blocked', 'reason': 'trusted_r1_runtime_drift'}
    assert calls == [] and fake.calls == [] and not store.path.exists()


def test_choice_attempt_database_commit_precedes_child_and_failed_commit_is_safe(tmp_path, monkeypatch):
    from shared_platform.worker_category_agent_attempts import _TABLE
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    with engine.transaction() as conn:
        conn.execute(_TABLE)
        conn.execute("CREATE TRIGGER reject_choice BEFORE INSERT ON "
                     "workbench_category_agent_attempts "
                     "BEGIN SELECT RAISE(ABORT, 'fixture insert failure'); END")
    rejected = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'rejected', [], lease_token=lease,
        category_context=context)
    assert rejected['status'] == 'blocked' and calls == [] and len(fake.calls) == 5
    with engine.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM workbench_category_agent_attempts').fetchone()[0] == 0
        conn.execute('DROP TRIGGER reject_choice')
    safe_retry = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'after-zero-launch', [], lease_token=lease,
        category_context=context)
    assert safe_retry['status'] == 'observed' and len(calls) == 2 and len(fake.calls) == 7


def test_facts_attempt_insert_failure_never_launches_facts_child(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    from shared_platform.operations_runtime import subprocess as runtime_subprocess
    fake_run = runtime_subprocess.run
    def reject_facts(argv, *, input, **kwargs):
        result = fake_run(argv, input=input, **kwargs)
        if Path(argv[argv.index('--output-schema') + 1]).name == 'choice-result.schema.json':
            with engine.transaction() as conn:
                conn.execute("CREATE TRIGGER reject_facts BEFORE INSERT ON "
                             "workbench_category_agent_attempts "
                             "WHEN NEW.stage='facts' "
                             "BEGIN SELECT RAISE(ABORT, 'fixture facts insert failure'); END")
        return result
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', reject_facts)
    rejected = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'facts-insert-fails', [], lease_token=lease,
        category_context=context)
    assert rejected['status'] == 'blocked'
    assert rejected['reason'] == 'trusted_r1_facts_attempt_denied'
    assert len(calls) == 1 and len(fake.calls) == 7
    with engine.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workbench_category_agent_attempts "
                            "WHERE task_id=? AND stage='facts'", (task_id,)).fetchone()[0] == 0
        conn.execute('DROP TRIGGER reject_facts')
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', fake_run)
    safe_retry = bridge.execute_facts(engine.get(task_id),
        bridge.profile.data_root / 'facts-zero-launch-retry', [], lease_token=lease)
    assert safe_retry['status'] == 'observed' and len(calls) == 2 and len(fake.calls) == 7


def test_runtime_drift_after_choice_blocks_capture_and_facts(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    checks = []
    def changing(_):
        checks.append(True)
        return len(checks) == 1
    monkeypatch.setattr('shared_platform.operations_runtime.runtime_matches', changing)
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'changed-after-choice', [], lease_token=lease,
        category_context=context)
    assert result == {'status': 'blocked', 'reason': 'trusted_r1_runtime_drift'}
    assert len(calls) == 1 and len(fake.calls) == 5
    with engine.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM workbench_category_intents "
                            "WHERE task_id=? AND action='capture'", (task_id,)).fetchone()[0] == 0


def test_crash_after_choice_reservation_before_child_is_unknown_across_dirs(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    original_mkdir = Path.mkdir
    def crash(self, *args, **kwargs):
        if self.name == 'category-choice':
            raise SystemExit('synthetic crash after task-ledger reservation')
        return original_mkdir(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', crash)
    with pytest.raises(SystemExit):
        bridge.execute_facts_offline(engine.get(task_id),
            bridge.profile.data_root / 'crashed', [], lease_token=lease,
            category_context=context)
    monkeypatch.setattr(Path, 'mkdir', original_mkdir)
    retry = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'different-dir', [], lease_token=lease,
        category_context=context)
    assert retry['status'] == 'unknown'
    assert retry['attempt_output'] == str(bridge.profile.data_root / 'crashed' / task_id)
    assert calls == [] and len(fake.calls) == 5


def test_attempt_rows_are_scoped_to_each_task(tmp_path, monkeypatch):
    from shared_platform.worker_category_admission import request_id
    from shared_platform.worker_category_agent_attempts import CategoryAgentAttemptLedger
    from shared_platform.worker_category_intents import CategoryIntentLedger
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    _fake_agent(monkeypatch)
    first = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'first-task', [], lease_token=lease,
        category_context=context)
    assert first['status'] == 'observed'
    other = engine.create({'template': 'publication',
        'scope': {'offer_id': '124', 'shops': context['requested_targets']},
        'source_key': 'independent-choice-attempt'})['task_id']
    other_lease = engine.claim(other, 'worker')['lease_token']
    body = dict(context, offer_id='124',
                schema_version='round1-category-options-request/v1')
    body['request_id'] = request_id(other, 'options', body)
    CategoryIntentLedger().reserve(engine, task_id=other, worker_id='worker',
        lease_token=other_lease, action='options', body=body)
    with engine.transaction() as conn:
        conn.execute('UPDATE workbench_category_intents SET status=?,result_reference=?,options_digest=? '
                     'WHERE task_id=? AND action=?',
                     ('SUCCEEDED', 'fixture-options-124', 'sha256:' + '4' * 64,
                      other, 'options'))
    grant = CategoryAgentAttemptLedger().reserve(engine, task=engine.get(other),
        worker_id='worker', lease_token=other_lease, stage='choice',
        input_binding={'options_request_id': body['request_id'],
                       'options_reference': 'fixture-options-124',
                       'options_digest': 'sha256:' + '4' * 64},
        output_path=str(bridge.profile.data_root / 'second-task'))
    assert grant['started_this_call']
    with engine.transaction() as conn:
        rows = conn.execute('SELECT task_id,stage FROM workbench_category_agent_attempts '
                            'WHERE stage=? ORDER BY task_id', ('choice',)).fetchall()
    assert {row['task_id'] for row in rows} == {task_id, other}
    assert len(fake.calls) == 7


def test_offline_bridge_unknown_options_and_capture_do_not_launch_facts(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    original = fake.merchant_get
    def timeout(path, params):
        if len(fake.calls) == 2:
            raise TimeoutError('synthetic official GET timeout')
        return original(path, params)
    fake.merchant_get = timeout
    first = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'unknown-options', [], lease_token=lease,
        category_context=context)
    second = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'unknown-options', [], lease_token=lease,
        category_context=context)
    assert first['status'] == second['status'] == 'unknown'
    assert len(fake.calls) == 2 and calls == []

    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path / 'capture', monkeypatch)
    calls = _fake_agent(monkeypatch)
    original = fake.merchant_get
    def capture_timeout(path, params):
        if len(fake.calls) == 6:
            raise TimeoutError('synthetic capture GET timeout')
        return original(path, params)
    fake.merchant_get = capture_timeout
    first = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'unknown-capture', [], lease_token=lease,
        category_context=context)
    assert first['status'] == 'unknown' and len(calls) == 1
    assert len(fake.calls) == 6
    second = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'unknown-capture', [], lease_token=lease,
        category_context=context)
    assert second['status'] == 'unknown' and len(calls) == 1 and len(fake.calls) == 6


def test_offline_bridge_lease_version_and_shop_scope_fail_before_get(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    task = engine.get(task_id)
    cases = [(task, 'forged', context),
             (dict(task, version=dict(task['version'], code_version='b' * 40)), lease, context),
             (task, lease, dict(context, requested_targets=['shopee:VN']))]
    for index, (candidate, token, candidate_context) in enumerate(cases):
        result = bridge.execute_facts_offline(candidate,
            bridge.profile.data_root / ('denied-' + str(index)), [],
            lease_token=token, category_context=candidate_context)
        assert result['status'] == 'blocked'
    with engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET lease_until=? WHERE task_id=?',
                     (engine.clock() - 1, task_id))
    expired = bridge.execute_facts_offline(task,
        bridge.profile.data_root / 'expired-lease', [], lease_token=lease,
        category_context=context)
    assert expired['status'] == 'blocked'
    assert fake.calls == [] and calls == [] and not store.path.exists()


def test_offline_bridge_cannot_take_over_human_same_request_id(tmp_path, monkeypatch):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    body = dict(context, schema_version='round1-category-options-request/v1')
    from shared_platform.worker_category_admission import request_id
    body['request_id'] = request_id(task_id, 'options', body)
    assert request('options', body)[0] == 200
    result = bridge.execute_facts_offline(engine.get(task_id),
        bridge.profile.data_root / 'human-conflict', [], lease_token=lease,
        category_context=context)
    assert result['status'] == 'blocked' and len(fake.calls) == 5
    assert calls == [] and store.category_worker_origin(body['request_id']) is None


def _spawn_offline(root, task_id, lease, mode, marker, queue):
    """New service process rebuilds only fixture context; ledgers persist."""
    from pytest import MonkeyPatch
    patch = MonkeyPatch()
    try:
        root = Path(root)
        body, _, fake, store = initial(root, patch)
        engine = WorkbenchEngine(root / 'tasks.db', {
            'code_version': 'a' * 40, 'environment': 'stable',
            'manifest_digest': 'pinned'})
        original = fake.merchant_get
        def counted(path, params):
            result = original(path, params)
            with open(marker, 'a', encoding='utf-8') as stream:
                stream.write('get\n')
                stream.flush(); os.fsync(stream.fileno())
            return result
        fake.merchant_get = counted
        if mode in {'crash', 'crash_capture'}:
            finish = store.finish_category_capture
            def crash(*args, **kwargs):
                if mode == 'crash' or len(fake.calls) == 7:
                    os._exit(41 if mode == 'crash' else 42)
                return finish(*args, **kwargs)
            store.finish_category_capture = crash
        bridge = _bound(engine, store)
        patch.setattr('shared_platform.operations_runtime.runtime_matches',
                      lambda _: True)
        patch.setattr('shared_platform.operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED',
                      True)
        _fake_agent(patch, marker=root / 'agent-count.txt',
                    crash_stage='facts' if mode == 'crash_facts' else
                    'choice' if mode == 'crash_choice' else None)
        if mode == 'facts_direct_restart':
            result = bridge.execute_facts(engine.get(task_id),
                bridge.profile.data_root / 'spawn-facts-direct-retry', [],
                lease_token=lease)
        else:
            result = bridge.execute_facts_offline(engine.get(task_id),
                bridge.profile.data_root / ('spawn-facts-retry' if mode == 'restart'
                                            else 'spawn-facts'), [], lease_token=lease,
                category_context=_context(body))
        queue.put(result)
    finally:
        patch.undo()


@pytest.mark.parametrize('mode', ['success', 'crash', 'crash_capture',
                                  'crash_choice', 'crash_facts'])
def test_separate_service_process_and_restart_never_replay_official_get(tmp_path, monkeypatch, mode):
    engine, task_id, lease, context, fake, store, bridge = _case(tmp_path, monkeypatch)
    ctx = multiprocessing.get_context('spawn')
    marker = tmp_path / 'official-count.txt'
    queue = ctx.Queue()
    process = ctx.Process(target=_spawn_offline,
        args=(str(tmp_path), task_id, lease, mode, str(marker), queue))
    process.start(); process.join(20)
    assert process.exitcode == {'success': 0, 'crash': 41, 'crash_capture': 42,
                                'crash_choice': 44, 'crash_facts': 43}[mode]
    if mode == 'success':
        assert queue.get(timeout=2)['status'] == 'observed'
        assert len(marker.read_text(encoding='utf-8').splitlines()) == 7
    else:
        assert len(marker.read_text(encoding='utf-8').splitlines()) == (
            5 if mode in {'crash', 'crash_choice'} else 7)
    agent_marker = tmp_path / 'agent-count.txt'
    before_agents = (agent_marker.read_text(encoding='utf-8').splitlines()
                     if agent_marker.exists() else [])
    restarted = ctx.Process(target=_spawn_offline,
        args=(str(tmp_path), task_id, lease,
              'facts_direct_restart' if mode == 'crash_facts' else 'restart',
              str(marker), queue))
    restarted.start(); restarted.join(20)
    assert restarted.exitcode == 0
    result = queue.get(timeout=2)
    assert result['status'] == 'unknown'
    if mode == 'crash_facts':
        assert result['reason'] == 'category_facts_attempt_exists_reconcile'
    assert len(marker.read_text(encoding='utf-8').splitlines()) == (
        5 if mode in {'crash', 'crash_choice'} else 7)
    assert (agent_marker.read_text(encoding='utf-8').splitlines()
            if agent_marker.exists() else []) == before_agents
    queue.close()
