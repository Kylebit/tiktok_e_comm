"""No product-review POST may be inherited by a future worker listener."""

from shared_platform import operations_worker_post_policy as policy
from shared_platform.operations_launch import local_post_allowed


def _request(path, **overrides):
    release = {'code_version': 'a' * 40, 'environment': 'stable',
               'manifest_digest': 'pinned'}
    value = {'path': path, 'client_ip': '127.0.0.1',
             'host_values': ['127.0.0.1:49345'],
             'origin_values': ['http://127.0.0.1:49345'],
             'listener_port': 49345,
             'task': {'task_id': 'TASK-20260922-001', 'template': 'publication',
                      'scope': {'offer_id': '123'}, 'version': release,
                      'execution_state': 'running'},
             'release': release,
             'body': {'offer_id': '123', 'task_id': 'TASK-20260922-001',
                      'request_id': 'attempt-001'},
             'idempotency_key': 'attempt-001'}
    value.update(overrides)
    return value


def test_worker_post_review_routes_stay_denied_even_with_full_claimed_context():
    for route in policy.REVIEW_POST_GAPS:
        assert not local_post_allowed(route)
        decision = policy.decide_post(**_request(route))
        assert decision['allowed'] is False
        expected = ('ROUTE_CONTRACT_UNPROVEN'
                    if policy.REVIEW_POST_GAPS[route]['actor'] == 'worker_preparation'
                    else 'HUMAN_OR_APPROVAL_BOUND_ROUTE')
        assert decision['reason'] == expected
        assert policy.REVIEW_POST_GAPS[route]['missing_contract']


def test_worker_post_requires_exact_loopback_same_origin_before_task_inspection():
    path = '/api/product-workspace/round1-category/options'
    for change, reason in [
        ({'client_ip': '192.0.2.1'}, 'LOOPBACK_REQUIRED'),
        ({'host_values': []}, 'EXACT_HOST_REQUIRED'),
        ({'host_values': ['127.0.0.1:49345', '127.0.0.1:49345']}, 'EXACT_HOST_REQUIRED'),
        ({'origin_values': []}, 'EXACT_ORIGIN_REQUIRED'),
        ({'origin_values': ['http://127.0.0.1:49289']}, 'EXACT_ORIGIN_REQUIRED'),
        ({'path': path + '?offer_id=123'}, 'CANONICAL_ROUTE_REQUIRED'),
        ({'path': path + '/'}, 'CANONICAL_ROUTE_REQUIRED'),
    ]:
        request = _request(path)
        request.update(change)
        assert policy.decide_post(**request)['reason'] == reason


def test_worker_preparation_post_rejects_missing_task_version_scope_and_idempotency():
    path = '/api/product-workspace/round1-category/options'
    release = _request(path)['release']
    for change, reason in [
        ({'task': None}, 'TASK_IDENTITY_REQUIRED'),
        ({'task': {**_request(path)['task'], 'scope': {'offer_id': '999'}}},
         'TASK_SCOPE_MISMATCH'),
        ({'release': {**release, 'code_version': 'b' * 40}}, 'RELEASE_MISMATCH'),
        ({'body': {**_request(path)['body'], 'request_id': 'another'}},
         'IDEMPOTENCY_MISMATCH'),
    ]:
        assert policy.decide_post(**_request(path, **change))['reason'] == reason


def test_worker_post_unknown_or_commerce_write_never_falls_through():
    for path in ['/api/product-workspace/publish-ozon',
                 '/api/product-workspace/collectbox-action/start',
                 '/api/catalog/cost', '/api/orbit/tasks', '/api/unknown']:
        assert policy.decide_post(**_request(path)) == {
            'allowed': False, 'reason': 'ROUTE_NOT_STAGED'}
