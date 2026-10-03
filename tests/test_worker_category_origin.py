"""Trusted worker provenance for category domain requests, isolated SQLite only."""

import hashlib
import json
import sys
import threading
import types

import pytest

from shared_platform.release_store import ReleaseStore, ImmutableReleaseError, WorkerCategoryOrigin
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_admission import request_id
from shared_platform.worker_category_intents import CategoryIntentLedger


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return 'sha256:' + hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


@pytest.fixture(autouse=True)
def fake_evidence_digest(monkeypatch):
    monkeypatch.setitem(sys.modules, 'shared_platform.round1_category_evidence',
                        types.SimpleNamespace(digest=digest))


def body(request_id='wkr-one'):
    ui = {'request_id': request_id, 'offer_id': '123', 'schema_version':
          'round1-category-options-request/v1', 'source_region': 'MY'}
    return dict(ui, _ui_request_digest=digest(ui))


def origin(payload, *, task='TASK-1', code='a' * 40):
    return WorkerCategoryOrigin(task_id=task, request_id=payload['request_id'],
        purpose='OPTIONS', release={'code_version': code,
            'environment': 'stable', 'manifest_digest': 'pinned'},
        ui_request_digest=payload['_ui_request_digest'])


def test_worker_origin_is_atomic_readable_across_restart_and_repeated_begin_denied(tmp_path):
    store = ReleaseStore(tmp_path / 'release.db')
    payload = body(); trusted = origin(payload)
    assert store.begin_category_capture(payload, 'instance-a', purpose='OPTIONS',
                                        worker_origin=trusted)
    restart = ReleaseStore(store.path)
    assert restart.category_worker_origin(payload['request_id']) == {
        'request_id': payload['request_id'], 'task_id': trusted.task_id,
        'purpose': 'OPTIONS', 'release': trusted.release,
        'ui_request_digest': trusted.ui_request_digest}
    assert not restart.begin_category_capture(payload, 'instance-a', purpose='OPTIONS',
                                              worker_origin=trusted)
    restart.finish_category_capture(payload['request_id'], 'instance-a', unknown=True)
    assert restart.category_capture_request(payload['request_id'], '123', 'instance-b')['status'] == 'UNKNOWN'
    assert not restart.begin_category_capture(payload, 'instance-b', purpose='OPTIONS',
                                              worker_origin=trusted)


def test_legacy_identical_request_cannot_be_taken_over_by_worker(tmp_path):
    store = ReleaseStore(tmp_path / 'release.db'); payload = body()
    assert store.begin_category_capture(payload, 'human', purpose='OPTIONS')
    assert store.category_worker_origin(payload['request_id']) is None
    assert not store.begin_category_capture(payload, 'human', purpose='OPTIONS')
    with pytest.raises(ImmutableReleaseError):
        store.begin_category_capture(payload, 'worker', purpose='OPTIONS',
                                     worker_origin=origin(payload))
    assert store.category_worker_origin(payload['request_id']) is None


@pytest.mark.parametrize('changed', ['task', 'release', 'digest', 'purpose'])
def test_existing_worker_request_cannot_be_rebound(tmp_path, changed):
    store = ReleaseStore(tmp_path / 'release.db'); payload = body(); trusted = origin(payload)
    store.begin_category_capture(payload, 'worker', purpose='OPTIONS', worker_origin=trusted)
    fields = {'task_id': trusted.task_id, 'request_id': trusted.request_id,
              'purpose': trusted.purpose, 'release': trusted.release,
              'ui_request_digest': trusted.ui_request_digest}
    if changed == 'task': fields['task_id'] = 'TASK-2'
    if changed == 'release': fields['release'] = dict(trusted.release, code_version='b' * 40)
    if changed == 'digest': fields['ui_request_digest'] = 'sha256:' + '0' * 64
    if changed == 'purpose': fields['purpose'] = 'CAPTURE'
    with pytest.raises((ImmutableReleaseError, ValueError)):
        store.begin_category_capture(payload, 'worker', purpose='OPTIONS',
                                     worker_origin=WorkerCategoryOrigin(**fields))
    with pytest.raises(ImmutableReleaseError):
        store.begin_category_capture(payload, 'human', purpose='OPTIONS')


def test_sidecar_insert_failure_rolls_back_domain_request_in_same_transaction(tmp_path):
    store = ReleaseStore(tmp_path / 'release.db'); payload = body()
    with store._round1_category_transaction(requests=True):
        pass
    with store._connect() as conn:
        conn.execute("CREATE TRIGGER deny_origin BEFORE INSERT ON round1_category_worker_origins "
                     "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    with pytest.raises(Exception):
        store.begin_category_capture(payload, 'worker', purpose='OPTIONS',
                                     worker_origin=origin(payload))
    assert store.category_capture_request(payload['request_id'], '123', 'worker') is None
    assert store.category_worker_origin(payload['request_id']) is None


def test_concurrent_first_begin_has_one_owner_and_same_origin(tmp_path):
    store = ReleaseStore(tmp_path / 'release.db'); payload = body(); trusted = origin(payload)
    outcomes = []; errors = []
    def run():
        try:
            outcomes.append(store.begin_category_capture(payload, 'instance-a',
                            purpose='OPTIONS', worker_origin=trusted))
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors
    assert sorted(outcomes) == [False, True]
    assert store.category_worker_origin(payload['request_id'])['task_id'] == 'TASK-1'


def test_real_two_sqlite_unknown_readback_requires_exact_origin(tmp_path):
    release = {'code_version': 'a' * 40, 'environment': 'stable',
               'manifest_digest': 'pinned'}
    engine = WorkbenchEngine(tmp_path / 'tasks.db', release)
    task_id = engine.create({'template': 'publication',
                             'scope': {'offer_id': '123', 'shops': ['shopee:MY']},
                             'source_key': 'two-ledger-fixture'})['task_id']
    engine.register_executor('worker', ['publication'], release)
    lease = engine.claim(task_id, 'worker')['lease_token']
    ui = {'schema_version': 'round1-category-options-request/v1',
          'context_digest': 'sha256:' + '1' * 64, 'offer_id': '123',
          'product_center_revision': 8, 'requested_targets': ['shopee:MY'],
          'source_region': 'MY', 'account_identity_digest': 'sha256:' + '2' * 64}
    ui['request_id'] = request_id(task_id, 'options', ui)
    ledger = CategoryIntentLedger()
    assert ledger.reserve(engine, task_id=task_id, worker_id='worker',
        lease_token=lease, action='options', body=ui)['dispatch_once']
    store = ReleaseStore(tmp_path / 'release.db')
    payload = dict(ui, _ui_request_digest=digest(ui))
    assert store.begin_category_capture(payload, 'worker-instance', purpose='OPTIONS',
        worker_origin=WorkerCategoryOrigin(task_id, ui['request_id'], 'OPTIONS',
                                           release, digest(ui)))
    store.finish_category_capture(ui['request_id'], 'worker-instance', unknown=True)
    assert CategoryIntentLedger().reconcile(engine, ReleaseStore(store.path),
        task_id=task_id, request_id=ui['request_id'])['status'] == 'UNKNOWN'
    assert not ledger.reserve(engine, task_id=task_id, worker_id='worker',
        lease_token=lease, action='options', body=ui)['dispatch_once']
