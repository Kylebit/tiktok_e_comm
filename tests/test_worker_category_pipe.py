"""Private worker-child pipe tests; all databases and providers are synthetic."""

import json
import multiprocessing
import os
from pathlib import Path
from threading import Thread

import pytest

from modules.products import server
from shared_platform.release_store import ReleaseStore
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_admission import request_id
from shared_platform.worker_category_bridge import WorkerCategoryBridge
from shared_platform.worker_category_pipe import BoundWorkerCategoryPipe, pipe_request
from test_worker_category_bridge import fixture


@pytest.fixture(autouse=True)
def limit_numeric_threads_for_spawn(monkeypatch):
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        monkeypatch.setenv(key, '1')


def _child_request(connection, payload, queue):
    try:
        queue.put(pipe_request(connection, payload))
    finally:
        connection.close()


def _service_process(connection, tasks_path, release_path, release, task_id,
                     lease_token, marker_path, mode):
    """Synthetic service process; crash after fake official read is intentional."""
    from shared_platform.round1_category_evidence import digest
    from shared_platform.release_store import WorkerCategoryOrigin
    engine = WorkbenchEngine(tasks_path, release)
    store = ReleaseStore(release_path)

    def fake_official(action, body, origin):
        payload = dict(body, _ui_request_digest=digest(body))
        store.begin_category_capture(payload, 'synthetic-service',
                                     purpose=action.upper(), worker_origin=origin)
        with open(marker_path, 'a', encoding='utf-8') as file:
            file.write('official-get\n')
            file.flush()
            os.fsync(file.fileno())
        if mode == 'crash':
            os._exit(31)
        store.finish_category_capture(body['request_id'], 'synthetic-service',
                                      unknown=True)
        return 200, {'status': 'UNKNOWN'}

    bridge = WorkerCategoryBridge(engine, store, fake_official)
    bound = BoundWorkerCategoryPipe(bridge, task_id=task_id,
                                   worker_id='worker', lease_token=lease_token)
    try:
        bound.serve_once(connection)
    finally:
        connection.close()


def _request_in_worker_process(bound, payload):
    ctx = multiprocessing.get_context('spawn')
    service, child = ctx.Pipe()
    queue = ctx.Queue()
    thread = Thread(target=bound.serve_once, args=(service,), daemon=True)
    thread.start()
    process = ctx.Process(target=_child_request, args=(child, payload, queue))
    process.start(); child.close()
    result = queue.get(timeout=15)
    process.join(timeout=15); thread.join(timeout=15)
    service.close(); queue.close()
    assert process.exitcode == 0 and not thread.is_alive()
    return result


def test_separate_worker_process_uses_server_held_lease_and_real_domain(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    bound = BoundWorkerCategoryPipe(bridge, task_id=task_id,
                                    worker_id='worker', lease_token=lease)
    response = _request_in_worker_process(bound, {'action': 'options', 'body': body})
    assert response['ok'] and response['status'] == 'SUCCEEDED'
    assert response['dispatched_this_call'] and len(fake.calls) == 5
    repeated = _request_in_worker_process(bound, {'action': 'options', 'body': body})
    assert repeated['ok'] and not repeated['dispatched_this_call']
    assert len(fake.calls) == 5
    assert store.category_worker_origin(body['request_id'])['task_id'] == task_id


def test_pipe_cannot_supply_task_lease_or_human_review_action(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    bound = BoundWorkerCategoryPipe(bridge, task_id=task_id,
                                    worker_id='worker', lease_token=lease)
    for payload in ({'action': 'options', 'body': body, 'lease_token': lease},
                    {'action': 'approve', 'body': body},
                    {'action': 'capture', 'body': dict(body, task_id=task_id)}):
        assert not _request_in_worker_process(bound, payload)['ok']
    assert fake.calls == [] and not store.path.exists()


def test_expired_lease_denies_new_provider_action(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    bound = BoundWorkerCategoryPipe(bridge, task_id=task_id,
                                    worker_id='worker', lease_token=lease)
    engine.clock = lambda: float('inf')
    denied = _request_in_worker_process(bound, {'action': 'options', 'body': body})
    assert denied == {'ok': False, 'code': 'WORKER_CATEGORY_DENIED'}
    denied_status = _request_in_worker_process(bound, {'action': 'status',
        'request_id': body['request_id']})
    assert denied_status == {'ok': False, 'code': 'WORKER_CATEGORY_DENIED'}
    assert fake.calls == [] and not store.path.exists()


def test_fake_official_timeout_over_pipe_is_unknown_and_not_resent(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    original = fake.merchant_get
    def timeout(path, params):
        if len(fake.calls) == 2:
            raise TimeoutError('synthetic timeout')
        return original(path, params)
    fake.merchant_get = timeout
    bound = BoundWorkerCategoryPipe(bridge, task_id=task_id,
                                    worker_id='worker', lease_token=lease)
    first = _request_in_worker_process(bound, {'action': 'options', 'body': body})
    again = _request_in_worker_process(bound, {'action': 'options', 'body': body})
    assert first['status'] == again['status'] == 'UNKNOWN'
    assert first['dispatched_this_call'] and not again['dispatched_this_call']
    assert len(fake.calls) == 2


def test_two_worker_processes_same_request_dispatch_once(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    bound = BoundWorkerCategoryPipe(bridge, task_id=task_id,
                                    worker_id='worker', lease_token=lease)
    ctx = multiprocessing.get_context('spawn')
    pairs = [ctx.Pipe() for _ in range(2)]
    queue = ctx.Queue()
    threads = [Thread(target=bound.serve_once, args=(pair[0],), daemon=True)
               for pair in pairs]
    children = [ctx.Process(target=_child_request,
                args=(pair[1], {'action': 'options', 'body': body}, queue))
                for pair in pairs]
    for thread in threads: thread.start()
    for process in children: process.start()
    for _, child in pairs: child.close()
    results = [queue.get(timeout=15) for _ in children]
    for process in children: process.join(timeout=15)
    for thread in threads: thread.join(timeout=15)
    for service, _ in pairs: service.close()
    queue.close()
    assert all(result['ok'] for result in results)
    assert sum(result['dispatched_this_call'] for result in results) == 1
    assert len(fake.calls) == 5
    assert all(process.exitcode == 0 for process in children)


def test_service_process_crash_after_fake_official_then_restart_no_replay(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    release = {'code_version': 'a' * 40, 'environment': 'stable',
               'manifest_digest': 'pinned'}
    tasks_path = tmp_path / 'tasks.db'
    release_path = tmp_path / 'release.db'
    marker_path = tmp_path / 'fake-official-count.txt'
    engine = WorkbenchEngine(tasks_path, release)
    task_id = engine.create({'template': 'publication',
        'scope': {'offer_id': '123', 'shops': ['shopee:MY']},
        'source_key': 'crash-fixture'})['task_id']
    engine.register_executor('worker', ['publication'], release)
    lease = engine.claim(task_id, 'worker')['lease_token']
    body = {'schema_version': 'round1-category-options-request/v1',
            'context_digest': 'sha256:' + '1' * 64, 'offer_id': '123',
            'product_center_revision': 8, 'requested_targets': ['shopee:MY'],
            'source_region': 'MY', 'account_identity_digest': 'sha256:' + '2' * 64}
    body['request_id'] = request_id(task_id, 'options', body)
    service, child = ctx.Pipe()
    crashed = ctx.Process(target=_service_process,
        args=(child, tasks_path, release_path, release, task_id, lease,
              marker_path, 'crash'))
    crashed.start(); child.close()
    service.send_bytes(json.dumps({'action': 'options', 'body': body}).encode())
    crashed.join(timeout=15); service.close()
    assert crashed.exitcode == 31
    assert marker_path.read_text(encoding='utf-8').splitlines() == ['official-get']
    assert ReleaseStore(release_path).category_worker_origin(body['request_id'])['task_id'] == task_id

    service, child = ctx.Pipe()
    restarted = ctx.Process(target=_service_process,
        args=(child, tasks_path, release_path, release, task_id, lease,
              marker_path, 'normal'))
    restarted.start(); child.close()
    response = pipe_request(service, {'action': 'options', 'body': body})
    restarted.join(timeout=15); service.close()
    assert restarted.exitcode == 0
    assert response['ok'] and response['status'] == 'UNKNOWN'
    assert not response['dispatched_this_call']
    assert marker_path.read_text(encoding='utf-8').splitlines() == ['official-get']
