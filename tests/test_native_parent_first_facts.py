"""A retained choice can resume the not-yet-started first facts stage."""
import json
from pathlib import Path

import pytest

from shared_platform import workbench_publication_native as native
from shared_platform.worker_category_agent_attempts import CategoryAgentAttemptLedger
from shared_platform.worker_category_parent_flow import ParentR1CategoryFlow
from shared_platform.worker_category_readonly_cli import ReadonlyCodexResult
from test_native_parent_category import _fresh, _children
from test_native_parent_child_receipt import _restart
from test_round1_workspace_freeze import live


class ParentInterrupted(BaseException):
    """A process-loss boundary, not an exception relabelled as success."""


def _stop_before_first_facts(live, monkeypatch, phase):
    engine, profile, worker, token, images = _fresh(live, monkeypatch)
    choices, facts = _children(monkeypatch, images)
    task = engine.get(next(iter(worker._task_ids)))
    with monkeypatch.context() as crash:
        if phase == 'after-choice':
            def interrupted(*args, **kwargs): raise ParentInterrupted()
            crash.setattr(ParentR1CategoryFlow, 'capture', interrupted)
        else:
            original = CategoryAgentAttemptLedger.reserve
            def interrupted(self, *args, **kwargs):
                if kwargs['stage'] == 'facts': raise ParentInterrupted()
                return original(self, *args, **kwargs)
            crash.setattr(CategoryAgentAttemptLedger, 'reserve', interrupted)
        with pytest.raises(ParentInterrupted):
            worker.adapters['publication'](engine, task, token, profile)
    with engine.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM workbench_category_agent_attempts WHERE stage='choice'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM workbench_category_agent_attempts WHERE stage='facts'").fetchone()[0] == 0
        count = db.execute("SELECT COUNT(*) FROM workbench_category_intents WHERE action='capture'").fetchone()[0]
        assert count == (0 if phase == 'after-choice' else 1)
        db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?', (task['task_id'],))
    current = engine.get(task['task_id'])
    assert current['execution_state'] == 'queued' and current['checkpoint']['facts_attempt']['state'] == 'started'
    assert len(choices) == 1 and not facts and not native.read_frozen(current, profile)
    worker.close()
    return engine, profile, current, choices, facts


@pytest.mark.parametrize('phase', ['after-choice', 'after-capture'])
def test_real_worker_restart_uses_original_choice_then_starts_facts_only_once_and_freezes(live, monkeypatch, phase):
    engine, profile, current, choices, facts = _stop_before_first_facts(live, monkeypatch, phase)
    restored = _restart(engine, profile, monkeypatch)
    try:
        monkeypatch.setattr(restored.wake_event, 'wait', lambda _: restored.stop_event.set())
        restored._loop()
        restored.inflight[current['task_id']].result(timeout=30)
        result = engine.get(current['task_id'])
        assert result['steps'][0]['state'] == 'completed' and result['current_step'] == 'images'
        assert native.read_frozen(result, profile) == result['steps'][0]['checkpoint']['native_r1']
        assert len(choices) == len(facts) == 1 and len(live['fake'].calls) == 7
        assert not result['required_action']
        with engine.transaction() as db:
            rows = db.execute('SELECT stage,worker_id FROM workbench_category_agent_attempts ORDER BY stage').fetchall()
            assert [row['stage'] for row in rows] == ['choice', 'facts']
            assert rows[0]['worker_id'] != rows[1]['worker_id'] == restored.worker_id
            assert db.execute("SELECT COUNT(*) FROM workbench_category_intents WHERE action='capture'").fetchone()[0] == 1
        checkpoints = [event['detail']['checkpoint'] for event in engine.store.events(current['task_id'])
                       if event['event_type'] == 'checkpoint_saved']
        assert any(value.get('facts_attempt', {}).get('result', {}).get('first_facts_after_retained_choice') is True
                   for value in checkpoints)
    finally:
        restored.close()


def test_completed_capture_without_intact_original_choice_receipt_cannot_start_first_facts(live, monkeypatch):
    engine, profile, current, choices, facts = _stop_before_first_facts(live, monkeypatch, 'after-capture')
    private = Path(current['checkpoint']['facts_attempt']['output']) / 'category-choice/private-child'
    (private / 'child-receipt.json').unlink()
    restored = _restart(engine, profile, monkeypatch)
    try:
        engine.register_executor(restored.worker_id, ['publication'], engine.release)
        token = engine.claim(current['task_id'], restored.worker_id, ttl=300)['lease_token']
        restored.adapters['publication'](engine, engine.get(current['task_id']), token, profile)
        rejected = engine.get(current['task_id'])
        assert rejected['execution_state'] == 'reconciliation_required' and not rejected['required_action']
        assert not native.read_frozen(rejected, profile) and len(choices) == 1 and not facts
        assert len(live['fake'].calls) == 7
        with engine.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM workbench_category_agent_attempts WHERE stage='facts'").fetchone()[0] == 0
    finally:
        restored.close()


def test_existing_unknown_facts_attempt_after_verified_choice_never_receives_first_stage_grant(live, monkeypatch):
    engine, profile, worker, token, images = _fresh(live, monkeypatch)
    choices, facts = _children(monkeypatch, images)
    from shared_platform import worker_category_readonly_cli as cli
    original = cli.run_readonly_jsonl
    def timeout_facts(argv, *args, **kwargs):
        if Path(argv[argv.index('--output-schema') + 1]).name == 'proposal.schema.json':
            facts.append(('original-facts-timeout',))
            return ReadonlyCodexResult(None, '', timed_out=True)
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(cli, 'run_readonly_jsonl', timeout_facts)
    task = engine.get(next(iter(worker._task_ids)))
    worker.adapters['publication'](engine, task, token, profile)
    current = engine.get(task['task_id'])
    assert current['execution_state'] == 'reconciliation_required'
    worker.close()
    restored = _restart(engine, profile, monkeypatch)
    try:
        assert restored.facts_adapter.queue_retained_recovery(current)
        engine.register_executor(restored.worker_id, ['publication'], engine.release)
        next_token = engine.claim(task['task_id'], restored.worker_id, ttl=300)['lease_token']
        restored.adapters['publication'](engine, engine.get(task['task_id']), next_token, profile)
        rejected = engine.get(task['task_id'])
        assert rejected['execution_state'] == 'reconciliation_required' and not native.read_frozen(rejected, profile)
        assert len(choices) == len(facts) == 1 and len(live['fake'].calls) == 7
        with engine.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM workbench_category_agent_attempts WHERE stage='facts'").fetchone()[0] == 1
    finally:
        restored.close()
