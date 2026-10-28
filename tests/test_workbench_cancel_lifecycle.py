"""Cancellation cannot turn a recorded active writer into a stopped writer."""
import sqlite3

import pytest

from test_workbench_engine import engine, create
from test_prepared_target_reduction import prepared, record, rows


def snapshot(e):
    with sqlite3.connect(e.store.path) as db:
        return {name: db.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                for name in ('workbench_execution', 'workbench_resource_locks', 'workbench_events')}


def test_cancel_expired_running_writer_preserves_lifecycle(engine):
    task = create(engine)['task_id']
    engine.claim(task, 'a')
    engine.test_clock[0] += 301
    before = snapshot(engine)
    with pytest.raises(ValueError, match='stop or reconcile'):
        engine.user_action(task, 'cancel')
    assert snapshot(engine) == before


def test_cancel_idle_task_does_not_expire_another_writer(engine):
    active = create(engine)['task_id']
    idle = create(engine, 'idle', sku='0002')['task_id']
    engine.claim(active, 'a')
    engine.test_clock[0] += 301
    before = snapshot(engine)
    result = engine.user_action(idle, 'cancel')
    after = snapshot(engine)
    assert result['execution_state'] == 'cancelled'
    assert next(r for r in after['workbench_execution'] if r[0] == active) == next(
        r for r in before['workbench_execution'] if r[0] == active)
    assert after['workbench_resource_locks'] == before['workbench_resource_locks']
    assert not any(r[2] == 'lease_expired' for r in after['workbench_events'])


@pytest.mark.parametrize('field,value', [('worker', 'old-owner'), ('lease_token', 'old-token'),
                                      ('lease_until', 1.0)])
def test_cancel_queued_task_with_retained_ownership_is_refused(engine, field, value):
    task = create(engine)['task_id']
    with sqlite3.connect(engine.store.path) as db:
        db.execute('UPDATE workbench_execution SET ' + field + '=? WHERE task_id=?', (value, task))
    before = snapshot(engine)
    with pytest.raises(ValueError, match='stop or reconcile'):
        engine.user_action(task, 'cancel')
    assert snapshot(engine) == before


@pytest.mark.parametrize('state', ['unknown', 'reconciliation_required'])
def test_target_reduction_refuses_every_uncompleted_domain_operation(prepared, state):
    e, task, *_ = prepared
    with e.transaction() as db:
        db.execute('INSERT INTO workbench_domain_operations VALUES(?,?,?,?,?,?)',
                   ('owned-operation', '[]', task, state, '', '2026-10-09T00:00:00Z'))
    before = rows(e)
    with pytest.raises(ValueError, match='unresolved external owner'):
        record(prepared)
    assert rows(e) == before
