"""Offline two-ledger R1 category intent and restart tests."""

import json
from contextlib import contextmanager

import pytest

from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_admission import request_id
from shared_platform.worker_category_intents import CategoryIntentLedger


class FakeDomain:
    def __init__(self):
        self.requests = {}
        self.options = {}
        self.origins = {}
        self.calls = 0

    def category_capture_request(self, key, offer, instance):
        return self.requests.get(key)

    def category_request_progress(self, key):
        return {'purpose': self.requests[key]['purpose']}

    def category_worker_origin(self, key):
        return self.origins.get(key)

    def category_options_record(self, reference, offer):
        return self.options[reference]

    def round1_category_observation(self, reference):
        return {'observer_reference': reference, 'offer_id': '123'}

    def dispatch(self, body, action, *, task_id, release, status='SUCCEEDED'):
        self.calls += 1
        self.origins[body['request_id']] = {'request_id': body['request_id'],
            'task_id': task_id, 'purpose': action.upper(), 'release': release,
            'ui_request_digest': 'sha256:' + __import__('hashlib').sha256(
                json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()}
        reference = ('options-ref' if action == 'options' else 'observation-ref') if status == 'SUCCEEDED' else None
        payload = dict(body, _ui_request_digest='sha256:' + __import__('hashlib').sha256(
            json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest())
        self.requests[body['request_id']] = {'request_id': body['request_id'],
            'offer_id': body['offer_id'], 'request_json': json.dumps(payload),
            'status': status, 'observer_reference': reference,
            'purpose': action.upper()}
        if action == 'options' and status == 'SUCCEEDED':
            self.options[reference] = {'options_reference': reference,
                'options_digest': 'sha256:' + '3' * 64,
                'source_region': 'MY', 'account_identity_digest': body['account_identity_digest'],
                'review_input': {'offer_id': body['offer_id'],
                                 'product_center_revision': body['product_center_revision']}}


def case(tmp_path):
    release = {'code_version': 'a' * 40, 'environment': 'stable', 'manifest_digest': 'pinned'}
    engine = WorkbenchEngine(tmp_path / 'tasks.db', release)
    task = engine.create({'template': 'publication',
                          'scope': {'offer_id': '123', 'shops': ['shopee:MY']},
                          'source_key': 'fixture-123'})
    engine.register_executor('worker', ['publication'], release)
    token = engine.claim(task['task_id'], 'worker')['lease_token']
    body = {'schema_version': 'round1-category-options-request/v1',
            'context_digest': 'sha256:' + '1' * 64, 'offer_id': '123',
            'product_center_revision': 8, 'requested_targets': ['shopee:MY'],
            'source_region': 'MY', 'account_identity_digest': 'sha256:' + '2' * 64}
    body['request_id'] = request_id(task['task_id'], 'options', body)
    return engine, task['task_id'], token, body


def reserve(ledger, engine, task_id, token, action, body):
    return ledger.reserve(engine, task_id=task_id, worker_id='worker',
                          lease_token=token, action=action, body=body)


def capture_body(task_id, options):
    body = dict(options, schema_version='round1-category-capture-request/v3',
                options_reference='options-ref', options_digest='sha256:' + '3' * 64,
                selected_category_identity='sha256:' + '4' * 64,
                attribute_selections=[])
    body['request_id'] = request_id(task_id, 'capture', body)
    return body


def test_crash_before_domain_begin_is_unknown_and_never_dispatched_again(tmp_path):
    engine, task_id, token, body = case(tmp_path)
    ledger = CategoryIntentLedger()
    assert reserve(ledger, engine, task_id, token, 'options', body)['dispatch_once']
    assert not reserve(CategoryIntentLedger(), engine, task_id, token, 'options', body)['dispatch_once']
    domain = FakeDomain()
    assert ledger.reconcile(engine, domain, task_id=task_id, request_id=body['request_id'])['status'] == 'UNKNOWN'
    assert domain.calls == 0


def test_admission_and_one_shot_reservation_share_one_task_transaction(tmp_path, monkeypatch):
    """No second transaction may admit a request after the facts step changes."""
    engine, task_id, token, body = case(tmp_path)
    original = engine.transaction
    starts = 0

    @contextmanager
    def observed_transaction():
        nonlocal starts
        starts += 1
        with original() as conn:
            if starts == 2:
                # A second transaction represents the window in which another
                # owner could complete facts before the intent is inserted.
                conn.execute('UPDATE workbench_execution SET step_index=1 WHERE task_id=?',
                             (task_id,))
            yield conn

    monkeypatch.setattr(engine, 'transaction', observed_transaction)
    assert reserve(CategoryIntentLedger(), engine, task_id, token,
                   'options', body)['dispatch_once']
    assert starts == 1


def test_expired_lease_still_allows_exact_readback_without_provider_replay(tmp_path):
    engine, task_id, token, body = case(tmp_path)
    ledger = CategoryIntentLedger()
    assert reserve(ledger, engine, task_id, token, 'options', body)['dispatch_once']
    domain = FakeDomain()
    domain.dispatch(body, 'options', task_id=task_id, release=engine.release)
    engine.clock = lambda: float('inf')
    assert ledger.reconcile(engine, domain, task_id=task_id,
                            request_id=body['request_id'])['status'] == 'SUCCEEDED'
    with pytest.raises(ValueError, match='stale or invalid lease'):
        reserve(ledger, engine, task_id, token, 'options', body)
    assert domain.calls == 1


def test_domain_success_survives_crash_before_task_receipt_and_capture_is_same_task(tmp_path):
    engine, task_id, token, options = case(tmp_path)
    domain = FakeDomain(); ledger = CategoryIntentLedger()
    assert reserve(ledger, engine, task_id, token, 'options', options)['dispatch_once']
    domain.dispatch(options, 'options', task_id=task_id, release=engine.release)
    capture = capture_body(task_id, options)
    with pytest.raises(ValueError):
        reserve(ledger, engine, task_id, token, 'capture', capture)
    restarted = CategoryIntentLedger()
    assert restarted.reconcile(engine, domain, task_id=task_id,
                             request_id=options['request_id'])['status'] == 'SUCCEEDED'
    assert reserve(restarted, engine, task_id, token, 'capture', capture)['dispatch_once']
    assert not reserve(restarted, engine, task_id, token, 'capture', capture)['dispatch_once']
    domain.dispatch(capture, 'capture', task_id=task_id, release=engine.release)
    assert restarted.reconcile(engine, domain, task_id=task_id,
                             request_id=capture['request_id'])['status'] == 'SUCCEEDED'
    assert domain.calls == 2


def test_options_from_another_task_or_changed_context_cannot_authorize_capture(tmp_path):
    engine, task_id, token, options = case(tmp_path)
    domain = FakeDomain(); ledger = CategoryIntentLedger()
    reserve(ledger, engine, task_id, token, 'options', options)
    domain.dispatch(options, 'options', task_id=task_id, release=engine.release)
    ledger.reconcile(engine, domain, task_id=task_id, request_id=options['request_id'])
    bad = capture_body(task_id, dict(options, source_region='TH'))
    with pytest.raises(ValueError):
        reserve(ledger, engine, task_id, token, 'capture', bad)
    bad = capture_body(task_id, dict(options, product_center_revision=9))
    with pytest.raises(ValueError):
        reserve(ledger, engine, task_id, token, 'capture', bad)

    other = engine.create({'template': 'publication',
                           'scope': {'offer_id': '999', 'shops': ['shopee:MY']},
                           'source_key': 'other-task-other-offer'})['task_id']
    engine.register_executor('worker-two', ['publication'], engine.release)
    other_token = engine.claim(other, 'worker-two')['lease_token']
    foreign_capture = capture_body(other, dict(options, offer_id='999'))
    with pytest.raises(ValueError):
        ledger.reserve(engine, task_id=other, worker_id='worker-two', lease_token=other_token,
                       action='capture', body=foreign_capture)


def test_late_domain_success_reconciles_unknown_without_replay(tmp_path):
    engine, task_id, token, options = case(tmp_path)
    domain = FakeDomain(); ledger = CategoryIntentLedger()
    reserve(ledger, engine, task_id, token, 'options', options)
    assert ledger.reconcile(engine, domain, task_id=task_id,
                            request_id=options['request_id'])['status'] == 'UNKNOWN'
    domain.dispatch(options, 'options', task_id=task_id, release=engine.release)
    assert ledger.reconcile(engine, domain, task_id=task_id,
                            request_id=options['request_id'])['status'] == 'SUCCEEDED'
    assert not reserve(ledger, engine, task_id, token, 'options', options)['dispatch_once']
    assert domain.calls == 1


def test_provider_timeout_after_domain_begin_never_replays_on_restart(tmp_path):
    engine, task_id, token, options = case(tmp_path)
    domain = FakeDomain(); ledger = CategoryIntentLedger()
    assert reserve(ledger, engine, task_id, token, 'options', options)['dispatch_once']
    domain.dispatch(options, 'options', task_id=task_id, release=engine.release,
                    status='IN_PROGRESS')
    # A transport timeout happens after the domain attempt was persisted.
    assert CategoryIntentLedger().reconcile(engine, domain, task_id=task_id,
            request_id=options['request_id'])['status'] == 'UNKNOWN'
    assert not reserve(CategoryIntentLedger(), engine, task_id, token,
                       'options', options)['dispatch_once']
    assert domain.calls == 1


@pytest.mark.parametrize('status', ['IN_PROGRESS', 'UNKNOWN', 'FAILED'])
def test_non_success_domain_outcomes_never_replay_or_authorize_capture(tmp_path, status):
    engine, task_id, token, options = case(tmp_path)
    domain = FakeDomain(); ledger = CategoryIntentLedger()
    reserve(ledger, engine, task_id, token, 'options', options)
    domain.dispatch(options, 'options', task_id=task_id, release=engine.release,
                    status=status)
    result = ledger.reconcile(engine, domain, task_id=task_id, request_id=options['request_id'])
    assert result['status'] == ('UNKNOWN' if status == 'IN_PROGRESS' else status)
    assert not reserve(ledger, engine, task_id, token, 'options', options)['dispatch_once']
    with pytest.raises(ValueError):
        reserve(ledger, engine, task_id, token, 'capture', capture_body(task_id, options))
    assert domain.calls == 1


def test_domain_identity_conflict_fails_closed_and_other_release_cannot_reconcile(tmp_path):
    engine, task_id, token, options = case(tmp_path)
    domain = FakeDomain(); ledger = CategoryIntentLedger()
    reserve(ledger, engine, task_id, token, 'options', options)
    domain.dispatch(options, 'options', task_id=task_id, release=engine.release)
    domain.requests[options['request_id']]['request_json'] = json.dumps(dict(options, offer_id='999'))
    with pytest.raises(ValueError):
        ledger.reconcile(engine, domain, task_id=task_id, request_id=options['request_id'])
    other = WorkbenchEngine(engine.store.path, {'code_version': 'b' * 40,
                            'environment': 'stable', 'manifest_digest': 'other'})
    with pytest.raises(ValueError):
        ledger.reconcile(other, domain, task_id=task_id, request_id=options['request_id'])


@pytest.mark.parametrize('tamper', ['missing', 'other_task', 'other_release'])
def test_preexisting_unbound_or_rebound_domain_request_cannot_prove_task_origin(tmp_path, tamper):
    engine, task_id, token, options = case(tmp_path)
    domain = FakeDomain(); ledger = CategoryIntentLedger()
    reserve(ledger, engine, task_id, token, 'options', options)
    domain.dispatch(options, 'options', task_id=task_id, release=engine.release)
    if tamper == 'missing':
        del domain.origins[options['request_id']]
    elif tamper == 'other_task':
        domain.origins[options['request_id']]['task_id'] = 'TASK-other'
    else:
        domain.origins[options['request_id']]['release'] = dict(engine.release,
                                                               code_version='b' * 40)
    with pytest.raises(ValueError):
        ledger.reconcile(engine, domain, task_id=task_id,
                         request_id=options['request_id'])
    with pytest.raises(ValueError):
        reserve(ledger, engine, task_id, token, 'capture', capture_body(task_id, options))


def test_older_unknown_read_cannot_downgrade_committed_success(tmp_path):
    engine, task_id, token, options = case(tmp_path)
    domain = FakeDomain(); ledger = CategoryIntentLedger()
    reserve(ledger, engine, task_id, token, 'options', options)
    domain.dispatch(options, 'options', task_id=task_id, release=engine.release)
    assert ledger.reconcile(engine, domain, task_id=task_id,
                            request_id=options['request_id'])['status'] == 'SUCCEEDED'
    domain.requests[options['request_id']]['status'] = 'IN_PROGRESS'
    result = ledger.reconcile(engine, domain, task_id=task_id,
                              request_id=options['request_id'])
    assert result['status'] == 'SUCCEEDED'
    assert reserve(ledger, engine, task_id, token, 'capture',
                   capture_body(task_id, options))['dispatch_once']
