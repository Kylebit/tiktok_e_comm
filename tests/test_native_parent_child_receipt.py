"""Original leased producer, private receipts, restart and no second CLI."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform import operations_runtime, publication_rounds
from shared_platform import workbench_publication_native as native
from shared_platform.local_operator_session import current_windows_owner_sid, verify_owner_only
from shared_platform.native_task_preparation import ExplicitNewTaskWorker, install_explicit_new_task_preparation
from shared_platform.worker_category_readonly_cli import ReadonlyCodexResult
from test_native_parent_facts import _leased, _child, _events, _execute_direct
from test_round1_workspace_freeze import live


def _interrupt_after_capture(engine, profile, worker, token, monkeypatch, images):
    calls = _child(monkeypatch, images)
    task = engine.get(next(iter(worker._task_ids)))
    with monkeypatch.context() as crash:
        def interrupted(_):
            raise RuntimeError('owned parent interruption after durable child output')
        crash.setattr(operations_runtime, '_parse_codex_final_jsonl', interrupted)
        worker.adapters['publication'](engine, task, token, profile)
    current = engine.get(task['task_id'])
    assert current['execution_state'] == 'reconciliation_required'
    assert current['checkpoint']['facts_attempt']['state'] == 'unknown'
    assert len(calls) == 1 and not native.read_frozen(current, profile)
    return current, calls


def _restart(engine, profile, monkeypatch):
    runtime = SimpleNamespace(engine=engine, profile=profile)
    with monkeypatch.context() as startup:
        startup.setattr(ExplicitNewTaskWorker, 'start', lambda self: None)
        worker = install_explicit_new_task_preparation(runtime)
    return worker


def test_default_worker_restart_recovers_private_original_child_and_original_freeze_without_relaunch(live, monkeypatch):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    current, calls = _interrupt_after_capture(engine, profile, worker, token, monkeypatch, images)
    attempt = current['checkpoint']['facts_attempt']
    private = Path(attempt['output']) / 'private-child'
    receipt_bytes = (private / 'child-receipt.json').read_bytes()
    receipt = json.loads(receipt_bytes)
    assert receipt['session_id'] == 'owned-typed-child'
    assert receipt['output_sha256'] == hashlib.sha256((private / 'child-output.jsonl').read_bytes()).hexdigest()
    assert receipt['attempt']['lease_sha256'] == hashlib.sha256(token.encode()).hexdigest()
    verify_owner_only(private, current_windows_owner_sid())
    verify_owner_only(private / 'child-output.jsonl', current_windows_owner_sid(), protected=False)
    public = json.dumps(current['checkpoint'], ensure_ascii=False)
    assert 'owned-typed-child' not in public and 'agent_message' not in public and token not in public
    worker.close()
    restored = _restart(engine, profile, monkeypatch)
    try:
        assert restored.worker_id != receipt['attempt']['worker_id']
        monkeypatch.setattr(restored.wake_event, 'wait', lambda _: restored.stop_event.set())
        # The actual polling path queues only the durable exact new POST, then
        # claims a fresh lease and enters hold/adapter/original domain producer.
        restored._loop()
        restored.inflight[current['task_id']].result(timeout=30)
        recovered = engine.get(current['task_id'])
        assert recovered['steps'][0]['state'] == 'completed'
        assert native.read_frozen(recovered, profile) == recovered['steps'][0]['checkpoint']['native_r1']
        assert recovered['current_step'] == 'images' and not recovered['required_action']
        attempts = [event['detail']['checkpoint']['facts_attempt']
                    for event in engine.store.events(current['task_id'])
                    if event['event_type'] == 'checkpoint_saved'
                    and event['detail']['checkpoint'].get('facts_attempt')]
        returned = [value for value in attempts if value['state'] == 'returned']
        assert returned and attempts[0] == returned[0]
        latest = returned[0]
        assert (latest['number'], latest['output'], latest['input_digest']) == (
            attempt['number'], attempt['output'], attempt['input_digest'])
        assert all((value['number'], value['output']) == (attempt['number'], attempt['output'])
                   for value in attempts)
        assert latest['result']['child_receipt'] == {
            'stage': 'facts', 'receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest()}
        assert latest['result']['recovered_original_child'] is True
        assert len(calls) == 1 and (private / 'child-receipt.json').read_bytes() == receipt_bytes
        with engine.transaction() as db:
            assert db.execute('SELECT COUNT(*) FROM workbench_category_agent_attempts').fetchone()[0] == 1
        assert operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED is False
        assert any(event['event_type'] == 'r1_typed_child_read_recovery_queued'
                   for event in engine.store.events(current['task_id']))
    finally:
        restored.close()


@pytest.mark.parametrize('outcome', ['timeout', 'overflow', 'nonzero', 'incomplete'])
def test_retained_unknown_or_incomplete_turn_cannot_become_prepared_or_launch_again(live, monkeypatch, outcome):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    def result(body, *_):
        events = _events(body)
        if outcome == 'timeout': return ReadonlyCodexResult(None, events, timed_out=True)
        if outcome == 'overflow': return ReadonlyCodexResult(0, events, overflow=True)
        if outcome == 'nonzero': return ReadonlyCodexResult(1, events)
        return ReadonlyCodexResult(0, events.rsplit('\n', 2)[0] + '\n')
    calls = _child(monkeypatch, images, result)
    try:
        first = _execute_direct(engine, profile, worker, token)
        assert first['status'] == 'unknown'
        task = engine.get(next(iter(worker._task_ids)))
        with worker.boundary.hold(engine, task, token, profile):
            recovered = worker.facts_adapter.recover_facts(task,
                profile.data_root / 'artifacts' / task['task_id'] / 'facts-1', [], lease_token=token)
        assert recovered['status'] == 'unknown' and len(calls) == 1
        assert not (publication_rounds.report_dir(live['offer']) / 'first-review-image-plan.json').exists()
        assert not native.read_frozen(task, profile)
    finally:
        worker.close()


@pytest.mark.parametrize('damage', ['output', 'rehashed-receipt', 'hardlink', 'source', 'revoked-capture'])
def test_recovery_rechecks_anchored_bytes_current_source_and_capture_before_sidecars(live, monkeypatch, damage):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    current, calls = _interrupt_after_capture(engine, profile, worker, token, monkeypatch, images)
    private = Path(current['checkpoint']['facts_attempt']['output']) / 'private-child'
    if damage in {'output', 'rehashed-receipt'}:
        output = private / 'child-output.jsonl'
        output.write_bytes(output.read_bytes().replace(b'owned-typed-child', b'other-typed-child'))
        if damage == 'rehashed-receipt':
            receipt = json.loads((private / 'child-receipt.json').read_bytes())
            receipt['output_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
            receipt['output_size'] = output.stat().st_size
            receipt['session_id'] = 'other-typed-child'
            (private / 'child-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    elif damage == 'hardlink':
        outside = live['root'] / 'outside-retained-output'
        outside.write_bytes((private / 'child-output.jsonl').read_bytes())
        (private / 'child-output.jsonl').unlink()
        os.link(outside, private / 'child-output.jsonl')
    elif damage == 'source':
        from modules.sourcing import new_product_workbench as workbench
        state = workbench.load_state(live['offer'])
        state['review']['title'] = 'Changed original preparation source'
        workbench.save_state(live['offer'], state)
    else:
        with engine.transaction() as db:
            reference = db.execute("SELECT result_reference FROM workbench_category_intents WHERE action='capture'").fetchone()[0]
        live['store'].invalidate_round1_category_observation(reference, 'SOURCE_REVOKED')
    worker.close()
    restored = _restart(engine, profile, monkeypatch)
    try:
        assert restored.facts_adapter.queue_retained_recovery(current)
        engine.register_executor(restored.worker_id, ['publication'], engine.release)
        new_token = engine.claim(current['task_id'], restored.worker_id, ttl=300)['lease_token']
        restored.adapters['publication'](engine, engine.get(current['task_id']), new_token, profile)
        rejected = engine.get(current['task_id'])
        assert rejected['execution_state'] == 'reconciliation_required'
        assert not rejected['required_action'] and not native.read_frozen(rejected, profile)
        assert not (publication_rounds.report_dir(live['offer']) / 'first-review-image-plan.json').exists()
        assert len(calls) == 1
        if damage == 'hardlink': assert outside.read_bytes() == (private / 'child-output.jsonl').read_bytes()
    finally:
        restored.close()


def test_interruption_before_original_result_has_no_recovery_grant_and_no_second_child(live, monkeypatch):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    calls = []
    def interrupted(*args, **kwargs):
        calls.append(args)
        raise RuntimeError('owned child reply lost before parent capture')
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl', interrupted)
    try:
        task = engine.get(next(iter(worker._task_ids)))
        worker.adapters['publication'](engine, task, token, profile)
        current = engine.get(task['task_id'])
        assert current['execution_state'] == 'reconciliation_required'
        assert worker.facts_adapter.queue_retained_recovery(current) is False
        assert len(calls) == 1 and not native.read_frozen(current, profile)
        with engine.transaction() as db:
            assert db.execute('SELECT COUNT(*) FROM workbench_category_agent_attempts').fetchone()[0] == 1
        assert not any(event['event_type'] == 'r1_typed_child_output_retained'
                       for event in engine.store.events(task['task_id']))
    finally:
        worker.close()


def test_expired_original_lease_retains_observation_but_requires_new_claim_before_original_adoption(live, monkeypatch):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    def expire(body, *_):
        with engine.transaction() as db:
            db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?',
                       (body['binding']['task_id'],))
    calls = _child(monkeypatch, images, expire)
    task = engine.get(next(iter(worker._task_ids)))
    worker._run(task['task_id'], token)
    current = engine.get(task['task_id'])
    assert current['execution_state'] == 'queued'
    assert current['checkpoint']['facts_attempt']['state'] == 'started'
    assert not native.read_frozen(current, profile) and len(calls) == 1
    private = Path(current['checkpoint']['facts_attempt']['output']) / 'private-child'
    assert json.loads((private / 'child-receipt.json').read_bytes())['session_id'] == 'owned-typed-child'
    worker.close()
    restored = _restart(engine, profile, monkeypatch)
    try:
        engine.register_executor(restored.worker_id, ['publication'], engine.release)
        new_token = engine.claim(task['task_id'], restored.worker_id, ttl=300)['lease_token']
        assert new_token != token
        restored.adapters['publication'](engine, engine.get(task['task_id']), new_token, profile)
        recovered = engine.get(task['task_id'])
        assert recovered['steps'][0]['state'] == 'completed' and native.read_frozen(recovered, profile)
        assert len(calls) == 1 and not recovered['required_action']
    finally:
        restored.close()


def test_fresh_choice_and_facts_sessions_are_retained_in_separate_private_original_attempts(live, monkeypatch):
    from test_native_parent_category import _fresh, _children
    engine, profile, worker, token, images = _fresh(live, monkeypatch)
    choices, facts = _children(monkeypatch, images)
    try:
        task = engine.get(next(iter(worker._task_ids)))
        worker.adapters['publication'](engine, task, token, profile)
        current = engine.get(task['task_id'])
        assert current['steps'][0]['state'] == 'completed' and native.read_frozen(current, profile)
        assert len(choices) == len(facts) == 1 and not current['required_action']
        with engine.transaction() as db:
            attempts = db.execute('SELECT * FROM workbench_category_agent_attempts ORDER BY stage').fetchall()
        assert [row['stage'] for row in attempts] == ['choice', 'facts']
        for row in attempts:
            private = Path(row['output_path']) / 'private-child'
            receipt = json.loads((private / 'child-receipt.json').read_bytes())
            assert receipt['attempt'] == dict(row) and receipt['session_id'] == 'owned-typed-child'
            verify_owner_only(private, current_windows_owner_sid())
        anchored = [event['detail'] for event in engine.store.events(task['task_id'])
                    if event['event_type'] == 'r1_typed_child_output_retained']
        assert len(anchored) == 2 and {event['stage'] for event in anchored} == {'choice', 'facts'}
        assert all(set(event) == {'stage', 'receipt_sha256'} for event in anchored)
    finally:
        worker.close()
