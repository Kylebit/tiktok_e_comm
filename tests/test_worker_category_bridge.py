"""Offline bridge against the real R1 domain function and fake official GETs."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from modules.products import server
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_admission import request_id
from shared_platform.worker_category_bridge import WorkerCategoryBridge
from shared_platform.worker_category_intents import CategoryIntentLedger
from test_round1_initial_options import initial, choice
from test_round1_category_observer_http import request


def fixture(tmp_path, monkeypatch):
    body, _, fake, store = initial(tmp_path, monkeypatch)
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {
        'code_version': 'a' * 40, 'environment': 'stable',
        'manifest_digest': 'pinned'})
    task_id = engine.create({'template': 'publication',
        'scope': {'offer_id': body['offer_id'],
                  'shops': body['requested_targets']},
        'source_key': 'worker-category-fixture'})['task_id']
    engine.register_executor('worker', ['publication'], engine.release)
    lease = engine.claim(task_id, 'worker')['lease_token']
    body['request_id'] = request_id(task_id, 'options', body)
    bridge = WorkerCategoryBridge(engine, store,
        lambda action, request_body, origin: server._round1_category_initial_request(
            action, request_body, worker_origin=origin))
    return engine, task_id, lease, body, fake, store, bridge


def execute(bridge, task_id, lease, action, body):
    return bridge.execute(task_id=task_id, worker_id='worker',
        lease_token=lease, action=action, body=body)


def test_real_domain_options_capture_and_duplicate_never_repeat_provider(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    result = execute(bridge, task_id, lease, 'options', body)
    assert result['status'] == 'SUCCEEDED' and result['dispatched_this_call']
    assert result['domain_response'][0] == 200
    assert store.category_worker_origin(body['request_id'])['task_id'] == task_id
    assert len(fake.calls) == 5
    assert not execute(bridge, task_id, lease, 'options', body)['dispatched_this_call']
    assert len(fake.calls) == 5
    selected = choice(body, result['domain_response'][1])
    selected['request_id'] = request_id(task_id, 'capture', selected)
    captured = execute(bridge, task_id, lease, 'capture', selected)
    assert captured['status'] == 'SUCCEEDED' and captured['dispatched_this_call']
    assert store.category_worker_origin(selected['request_id'])['task_id'] == task_id
    assert len(fake.calls) == 7
    assert not execute(bridge, task_id, lease, 'capture', selected)['dispatched_this_call']
    assert len(fake.calls) == 7


def test_worker_request_digest_matches_real_domain_payload(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    result = execute(bridge, task_id, lease, 'options', body)
    from shared_platform.round1_category_evidence import digest
    row = store.category_capture_request(body['request_id'], body['offer_id'],
                                         server._ROUND1_CATEGORY_INSTANCE)
    origin = store.category_worker_origin(body['request_id'])
    import json
    assert origin['ui_request_digest'] == digest(body)
    assert json.loads(row['request_json'])['_ui_request_digest'] == digest(body)
    assert result['status'] == 'SUCCEEDED' and len(fake.calls) == 5


def test_real_server_early_existing_branch_checks_private_origin(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    assert execute(bridge, task_id, lease, 'options', body)['status'] == 'SUCCEEDED'
    from shared_platform.release_store import WorkerCategoryOrigin
    from shared_platform.round1_category_evidence import digest
    valid = WorkerCategoryOrigin(task_id, body['request_id'], 'OPTIONS',
                                 engine.release, digest(body))
    assert server._round1_category_initial_request('options', body,
        worker_origin=valid)[0] == 200
    for wrong in (WorkerCategoryOrigin('TASK-other', body['request_id'],
                      'OPTIONS', engine.release, digest(body)),
                  WorkerCategoryOrigin(task_id, body['request_id'],
                      'OPTIONS', dict(engine.release, code_version='b' * 40), digest(body)),
                  WorkerCategoryOrigin(task_id, body['request_id'],
                      'OPTIONS', engine.release, 'sha256:' + '0' * 64)):
        assert server._round1_category_initial_request('options', body,
            worker_origin=wrong)[0] == 409
    assert len(fake.calls) == 5


def test_crash_after_task_intent_before_domain_call_is_unknown_not_replayed(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    assert CategoryIntentLedger().reserve(engine, task_id=task_id, worker_id='worker',
        lease_token=lease, action='options', body=body)['dispatch_once']
    result = execute(bridge, task_id, lease, 'options', body)
    assert result['status'] == 'UNKNOWN' and not result['dispatched_this_call']
    assert not store.path.exists() and fake.calls == []


def test_real_provider_timeout_is_unknown_and_no_replay(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    original = fake.merchant_get
    def timeout(path, params):
        if len(fake.calls) == 2:
            raise TimeoutError('synthetic provider timeout')
        return original(path, params)
    fake.merchant_get = timeout
    first = execute(bridge, task_id, lease, 'options', body)
    assert first['status'] == 'UNKNOWN' and len(fake.calls) == 2
    repeated = execute(bridge, task_id, lease, 'options', body)
    assert repeated['status'] == 'UNKNOWN' and not repeated['dispatched_this_call']
    assert len(fake.calls) == 2


def test_process_exit_after_provider_reads_preserves_unknown_without_replay(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    original_finish = store.finish_category_capture
    def process_exit(*args, **kwargs):
        raise SystemExit('synthetic process exit before result commit')
    monkeypatch.setattr(store, 'finish_category_capture', process_exit)
    with pytest.raises(SystemExit):
        execute(bridge, task_id, lease, 'options', body)
    assert len(fake.calls) == 5
    monkeypatch.setattr(store, 'finish_category_capture', original_finish)
    restarted = WorkerCategoryBridge(engine, store, bridge.domain_call)
    recovery = execute(restarted, task_id, lease, 'options', body)
    assert recovery['status'] == 'UNKNOWN'
    assert not recovery['dispatched_this_call']
    assert len(fake.calls) == 5


def test_concurrent_worker_duplicate_dispatches_one_provider_sequence(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(bridge, task_id, lease,
                                                  'options', body), range(2)))
    assert sum(result['dispatched_this_call'] for result in results) == 1
    assert len(fake.calls) == 5
    assert execute(bridge, task_id, lease, 'options', body)['status'] == 'SUCCEEDED'


def test_human_same_id_cannot_be_adopted_and_http_cannot_inject_origin(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    # Existing human R1 route remains usable and has no worker-origin sidecar.
    status, human = request('options', body)
    assert status == 200 and human['status'] == 'SUCCEEDED'
    assert store.category_worker_origin(body['request_id']) is None
    from shared_platform.release_store import WorkerCategoryOrigin
    from shared_platform.round1_category_evidence import digest
    worker_origin = WorkerCategoryOrigin(task_id, body['request_id'], 'OPTIONS',
                                         engine.release, digest(body))
    assert server._round1_category_initial_request('options', body,
        worker_origin=worker_origin)[0] == 409
    assert server._round1_category_initial_request('options', body,
        worker_origin={'task_id': task_id})[0] == 409
    with pytest.raises(ValueError, match='trusted task origin'):
        execute(bridge, task_id, lease, 'options', body)
    assert len(fake.calls) == 5
    rejected = dict(body, worker_origin={'task_id': task_id})
    assert request('options', rejected)[0] == 400
    assert len(fake.calls) == 5


def test_invalid_lease_rejected_before_domain_or_provider(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        execute(bridge, task_id, 'forged', 'options', body)
    assert not store.path.exists() and fake.calls == []


def test_out_of_scope_target_rejected_before_domain_or_provider(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    outside = dict(body, requested_targets=[*body['requested_targets'], 'shopee:VN'])
    outside['request_id'] = request_id(task_id, 'options', outside)
    with pytest.raises(ValueError, match='target'):
        execute(bridge, task_id, lease, 'options', outside)
    assert fake.calls == [] and not store.path.exists()


def test_actual_loopback_maintenance_http_still_denies_worker_category_post(tmp_path, monkeypatch):
    import json
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import Request, build_opener, ProxyHandler
    from shared_platform.operations_launch import maintenance_handler

    engine, task_id, lease, body, fake, store, bridge = fixture(tmp_path, monkeypatch)
    http = ThreadingHTTPServer(('127.0.0.1', 0), maintenance_handler(server.Handler))
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        req = Request('http://127.0.0.1:%d/api/product-workspace/round1-category/options'
                      % http.server_port, data=json.dumps(body).encode('utf-8'),
                      headers={'Content-Type': 'application/json'})
        with pytest.raises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(req, timeout=5)
        assert error.value.code == 409
        assert json.loads(error.value.read())['code'] == 'WEB_ONLY_EXECUTION_PAUSED'
        assert fake.calls == [] and not store.path.exists()
    finally:
        http.shutdown(); http.server_close(); thread.join(timeout=5)
