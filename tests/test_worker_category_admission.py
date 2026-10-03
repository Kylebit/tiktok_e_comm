"""Task-bound R1 category observation contract; no HTTP or provider calls."""

import copy

import pytest

from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform import worker_category_admission as admission


def _case(tmp_path):
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {
        'code_version': 'a' * 40, 'environment': 'stable', 'manifest_digest': 'pinned'})
    task = engine.create({'template': 'publication', 'scope': {'offer_id': '123',
                          'shops': ['shopee:MY', 'shopee:PH']},
                          'source_key': 'fixture-offer-123'})
    engine.register_executor('synthetic-worker', ['publication'], engine.release)
    lease = engine.claim(task['task_id'], 'synthetic-worker')['lease_token']
    body = {
        'schema_version': 'round1-category-options-request/v1',
        'context_digest': 'sha256:' + '1' * 64,
        'offer_id': '123', 'product_center_revision': 8,
        'requested_targets': ['shopee:MY'], 'source_region': 'MY',
        'account_identity_digest': 'sha256:' + '2' * 64,
    }
    body['request_id'] = admission.request_id(task['task_id'], 'options', body)
    return engine, task['task_id'], lease, body


def test_valid_task_lease_and_exact_offer_admit_only_preapproval_observation(tmp_path):
    engine, task_id, lease, body = _case(tmp_path)
    result = admission.validate(engine, task_id=task_id, worker_id='synthetic-worker',
                                lease_token=lease, action='options', body=body)
    assert result['task_id'] == task_id
    assert result['offer_id'] == '123'
    assert result['action'] == 'options'
    assert result['request_id'] == body['request_id']
    assert result['release'] == engine.release
    assert 'lease_token' not in result


def test_worker_category_targets_must_stay_inside_nonempty_task_shop_scope(tmp_path):
    engine, task_id, lease, body = _case(tmp_path)
    body['requested_targets'] = ['shopee:MY', 'shopee:VN']
    body['request_id'] = admission.request_id(task_id, 'options', body)
    with pytest.raises(ValueError, match='target'):
        admission.validate(engine, task_id=task_id, worker_id='synthetic-worker',
                           lease_token=lease, action='options', body=body)
    body['requested_targets'] = ['shopee:MY']
    body['request_id'] = admission.request_id(task_id, 'options', body)
    assert admission.validate(engine, task_id=task_id, worker_id='synthetic-worker',
                              lease_token=lease, action='options', body=body)['request_id'] == body['request_id']


def test_empty_task_shop_scope_requires_frozen_server_source(tmp_path):
    engine = WorkbenchEngine(tmp_path / 'empty-scope.db', {
        'code_version': 'a' * 40, 'environment': 'stable', 'manifest_digest': 'pinned'})
    task_id = engine.create({'template': 'publication', 'scope': {'offer_id': '123'},
                             'source_key': 'empty-shop-scope'})['task_id']
    engine.register_executor('worker', ['publication'], engine.release)
    lease = engine.claim(task_id, 'worker')['lease_token']
    body = {'schema_version': 'round1-category-options-request/v1',
            'context_digest': 'sha256:' + '1' * 64, 'offer_id': '123',
            'product_center_revision': 8, 'requested_targets': ['shopee:MY'],
            'source_region': 'MY', 'account_identity_digest': 'sha256:' + '2' * 64}
    body['request_id'] = admission.request_id(task_id, 'options', body)
    with pytest.raises(ValueError, match='target'):
        admission.validate(engine, task_id=task_id, worker_id='worker',
                           lease_token=lease, action='options', body=body)


@pytest.mark.parametrize('change', [
    {'lease_token': 'forged'}, {'worker_id': 'other-worker'},
    {'task_id': 'TASK-not-found'}, {'action': 'freeze'},
])
def test_invalid_task_identity_or_action_denied(tmp_path, change):
    engine, task_id, lease, body = _case(tmp_path)
    values = dict(engine=engine, task_id=task_id, worker_id='synthetic-worker',
                  lease_token=lease, action='options', body=body)
    values.update(change)
    with pytest.raises((ValueError, KeyError)):
        admission.validate(**values)


def test_offer_version_and_request_identity_cannot_be_rebound(tmp_path):
    engine, task_id, lease, body = _case(tmp_path)
    for altered in [dict(body, offer_id='999'), dict(body, request_id='attacker'),
                    dict(body, approval_receipt='claimed'),
                    dict(body, schema_version='round1-category-capture-request/v2')]:
        with pytest.raises(ValueError):
            admission.validate(engine, task_id=task_id, worker_id='synthetic-worker',
                               lease_token=lease, action='options', body=altered)
    other_release = WorkbenchEngine(engine.store.path, {
        'code_version': 'b' * 40, 'environment': 'stable', 'manifest_digest': 'other'})
    with pytest.raises(ValueError):
        admission.validate(other_release, task_id=task_id, worker_id='synthetic-worker',
                           lease_token=lease, action='options', body=body)


def test_capture_requires_v3_and_stable_task_bound_request_id(tmp_path):
    engine, task_id, lease, options = _case(tmp_path)
    body = dict(options, schema_version='round1-category-capture-request/v3',
                options_reference='options-ref', options_digest='sha256:' + '3' * 64,
                selected_category_identity='sha256:' + '4' * 64,
                attribute_selections=[])
    body['request_id'] = admission.request_id(task_id, 'capture', body)
    assert body['request_id'] == admission.request_id(task_id, 'capture', copy.deepcopy(body))
    result = admission.validate(engine, task_id=task_id, worker_id='synthetic-worker',
                                lease_token=lease, action='capture', body=body)
    assert result['action'] == 'capture'
    changed = dict(body, selected_category_identity='sha256:' + '5' * 64)
    with pytest.raises(ValueError):
        admission.validate(engine, task_id=task_id, worker_id='synthetic-worker',
                           lease_token=lease, action='capture', body=changed)


def test_after_facts_step_or_lease_expiry_is_rejected(tmp_path):
    engine, task_id, lease, body = _case(tmp_path)
    engine.complete_step(task_id, lease, expected_step='facts', checkpoint={'fixture': True})
    with pytest.raises(ValueError):
        admission.validate(engine, task_id=task_id, worker_id='synthetic-worker',
                           lease_token=lease, action='options', body=body)

    expiring, expiring_id, expiring_lease, expiring_body = _case(tmp_path / 'expired')
    expiring.clock = lambda: float('inf')
    with pytest.raises(ValueError):
        admission.validate(expiring, task_id=expiring_id, worker_id='synthetic-worker',
                           lease_token=expiring_lease, action='options', body=expiring_body)
