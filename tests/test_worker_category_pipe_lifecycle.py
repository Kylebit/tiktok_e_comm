"""Fixture-only worker child lifecycle, handle ownership and recovery."""

import multiprocessing
import os

import pytest

from shared_platform.worker_category_pipe import pipe_request
from shared_platform.worker_category_pipe_lifecycle import (
    MARKER, OfflineWorkerPipeSupervisor,
)
from test_worker_category_bridge import fixture


@pytest.fixture(autouse=True)
def limit_numeric_threads_for_spawn(monkeypatch):
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        monkeypatch.setenv(key, '1')


def _one_request(connection, body, queue):
    queue.put(pipe_request(connection, {'action': 'options', 'body': body}))


def _exit_without_request(connection):
    os._exit(19)


def _send_then_exit(connection, body):
    import json
    connection.send_bytes(json.dumps({'action': 'options', 'body': body}).encode())
    os._exit(17)


def _wait_for_stop(connection):
    multiprocessing.get_context('spawn').Event().wait(30)


def supervisor(tmp_path, bridge, task_id, lease, target, args=(), *, enabled=True):
    (tmp_path / '.orbit-worker-pipe-fixture').write_text(MARKER, encoding='utf-8')
    return OfflineWorkerPipeSupervisor(bridge, task_id=task_id,
        worker_id='worker', lease_token=lease, isolation_root=tmp_path,
        worker_target=target, worker_args=args,
        offline_fixture_enabled=enabled)


def test_default_disabled_and_store_escape_denied(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='disabled'):
        supervisor(tmp_path, bridge, task_id, lease, _exit_without_request,
                   enabled=False)
    outside = tmp_path.parent / 'outside-release.db'
    from shared_platform.release_store import ReleaseStore
    old = bridge.domain_store
    bridge.domain_store = ReleaseStore(outside)
    with pytest.raises(ValueError, match='inside isolation root'):
        supervisor(tmp_path, bridge, task_id, lease, _exit_without_request)
    bridge.domain_store = old
    assert fake.calls == []


def test_parent_owns_service_end_closes_child_copy_and_returns_structured_receipt(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    queue = multiprocessing.get_context('spawn').Queue()
    manager = supervisor(tmp_path, bridge, task_id, lease, _one_request, (body, queue))
    started = manager.start()
    assert started['state'] == 'running'
    result = queue.get(timeout=15)
    ended = manager.wait(15)
    queue.close()
    assert result['ok'] and result['status'] == 'SUCCEEDED'
    assert ended['state'] == 'exited' and not ended['child_alive'] and not ended['service_alive']
    assert ended['parent_retains_child_handle'] is False
    assert ended['responses_sent'] == ended['dispatched_in_sent_responses'] == 1
    assert ended['automatic_restart'] is False and ended['production_activation'] is False
    assert ended['domain_outcome_inferred_from_child_exit'] is False
    assert lease not in str(result)
    assert len(fake.calls) == 5
    with pytest.raises(ValueError, match='only once'):
        manager.start()


def test_child_exit_without_frame_closes_pipe_without_orphan_or_dispatch(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    manager = supervisor(tmp_path, bridge, task_id, lease, _exit_without_request)
    manager.start(); result = manager.wait(15)
    assert result['state'] == 'child_failed' and result['child_exitcode'] == 19
    assert not result['service_alive'] and not result['parent_retains_child_handle']
    assert result['responses_sent'] == 0 and fake.calls == []
    assert not store.path.exists()


def test_abrupt_worker_exit_after_request_then_new_supervisor_does_not_replay(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    manager = supervisor(tmp_path, bridge, task_id, lease, _send_then_exit, (body,))
    manager.start(); ended = manager.wait(15)
    assert ended['state'] == 'child_failed' and ended['child_exitcode'] == 17
    assert not ended['service_alive']
    assert ended['domain_outcome_inferred_from_child_exit'] is False
    assert len(fake.calls) == 5
    queue = multiprocessing.get_context('spawn').Queue()
    restarted = supervisor(tmp_path, bridge, task_id, lease, _one_request, (body, queue))
    restarted.start(); result = queue.get(timeout=15); restarted.wait(15)
    queue.close()
    assert result['ok'] and result['status'] == 'SUCCEEDED'
    assert not result['dispatched_this_call'] and len(fake.calls) == 5


def test_stop_terminates_idle_child_and_prevents_later_dispatch(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    manager = supervisor(tmp_path, bridge, task_id, lease, _wait_for_stop)
    manager.start(); stopped = manager.stop(15)
    assert stopped['state'] == 'stopped'
    assert not stopped['child_alive'] and not stopped['service_alive']
    assert stopped['dispatched_in_sent_responses'] == 0 and fake.calls == []
    assert not store.path.exists()
    with pytest.raises(ValueError, match='only once'):
        manager.start()


def test_stop_during_inflight_domain_call_reports_pending_until_it_finishes(tmp_path, monkeypatch):
    from threading import Event
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    entered = Event(); release = Event()
    original = fake.merchant_get
    def blocked(path, params):
        entered.set()
        assert release.wait(10)
        return original(path, params)
    fake.merchant_get = blocked
    queue = multiprocessing.get_context('spawn').Queue()
    manager = supervisor(tmp_path, bridge, task_id, lease, _one_request, (body, queue))
    manager.start()
    assert entered.wait(10)
    pending = manager.stop(0.01)
    assert pending['state'] == 'stop_pending' and pending['service_alive']
    assert pending['domain_outcome_inferred_from_child_exit'] is False
    release.set()
    stopped = manager.stop(10)
    queue.close()
    assert stopped['state'] == 'stopped' and not stopped['service_alive']
    assert stopped['responses_sent'] == 0
    assert len(fake.calls) == 5


def test_two_supervisors_same_task_concurrent_children_dispatch_once(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    ctx = multiprocessing.get_context('spawn')
    queues = [ctx.Queue(), ctx.Queue()]
    managers = [supervisor(tmp_path, bridge, task_id, lease, _one_request,
                           (body, queue)) for queue in queues]
    for manager in managers: manager.start()
    responses = [queue.get(timeout=15) for queue in queues]
    receipts = [manager.wait(15) for manager in managers]
    for queue in queues: queue.close()
    assert all(response['ok'] for response in responses)
    assert sum(response['dispatched_this_call'] for response in responses) == 1
    assert sum(receipt['dispatched_in_sent_responses'] for receipt in receipts) == 1
    assert all(receipt['state'] == 'exited' for receipt in receipts)
    assert len(fake.calls) == 5
