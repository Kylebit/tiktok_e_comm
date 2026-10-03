"""Fail-closed review POST boundary for a future worker HTTP surface.

This module does not register routes or relax the current web-only handler.
All staged routes remain denied until the domain request itself binds an
Orbit task, pinned release and durable idempotency key where applicable.
Human-review routes additionally require the original domain's approval.
Caller-supplied fields are not authority and never make a route executable.
"""

from __future__ import annotations

import ipaddress


REVIEW_POST_GAPS = {
    '/api/product-workspace/round1-category/options': {
        'actor': 'worker_preparation',
        'effect': 'official read plus local capture-request/receipt persistence',
        'missing_contract': 'request body has offer/request IDs but no Orbit task lease or release identity',
    },
    '/api/product-workspace/round1-category/capture': {
        'actor': 'worker_preparation',
        'effect': 'official category read plus local observation persistence',
        'missing_contract': 'request body has offer/request IDs but no Orbit task lease or release identity',
    },
    '/api/product-workspace/round1-category/prepare': {
        'actor': 'worker_preparation',
        'effect': 'persist first-round preparation candidate',
        'missing_contract': 'request_id is local idempotency only; no Orbit task lease/release binding',
    },
    '/api/product-workspace/round1-category/freeze': {
        'actor': 'approval_bound_domain',
        'effect': 'freeze approved first-round identity',
        'missing_contract': 'human approval must be validated in original domain; no worker-authority contract',
    },
    '/api/product-workspace/approve': {
        'actor': 'human_review',
        'effect': 'record first-round human product approval',
        'missing_contract': 'must remain a human review action, not a worker-generated approval',
    },
    '/api/product-workspace/r2-candidate/decision': {
        'actor': 'human_review',
        'effect': 'persist human image keep/rework decisions',
        'missing_contract': 'revision and binding protect image choice, but request has no Orbit task/release identity',
    },
    '/api/product-workspace/release-plan/approve': {
        'actor': 'human_review',
        'effect': 'approve frozen final publication plan',
        'missing_contract': 'human approval and target identity cannot be inferred from a worker request',
    },
}


def decide_post(*, path, client_ip, host_values, origin_values, listener_port,
                task, release, body, idempotency_key):
    """Classify a proposed POST. Never grant execution in this version."""
    canonical = str(path).split('?', 1)[0].rstrip('/')
    if canonical not in REVIEW_POST_GAPS:
        return {'allowed': False, 'reason': 'ROUTE_NOT_STAGED'}
    if path != canonical:
        return {'allowed': False, 'reason': 'CANONICAL_ROUTE_REQUIRED'}
    if REVIEW_POST_GAPS[canonical]['actor'] != 'worker_preparation':
        return {'allowed': False, 'reason': 'HUMAN_OR_APPROVAL_BOUND_ROUTE'}
    try:
        if not ipaddress.ip_address(client_ip).is_loopback:
            raise ValueError()
    except ValueError:
        return {'allowed': False, 'reason': 'LOOPBACK_REQUIRED'}
    expected_hosts = {'127.0.0.1:' + str(listener_port), 'localhost:' + str(listener_port)}
    if (not isinstance(host_values, list) or len(host_values) != 1
            or host_values[0] not in expected_hosts):
        return {'allowed': False, 'reason': 'EXACT_HOST_REQUIRED'}
    if (not isinstance(origin_values, list) or len(origin_values) != 1
            or origin_values[0] != 'http://' + host_values[0]):
        return {'allowed': False, 'reason': 'EXACT_ORIGIN_REQUIRED'}
    if (not isinstance(task, dict) or not isinstance(body, dict)
            or task.get('template') != 'publication'
            or task.get('execution_state') != 'running'
            or not str(task.get('task_id') or '').startswith('TASK-')
            or body.get('task_id') != task.get('task_id')):
        return {'allowed': False, 'reason': 'TASK_IDENTITY_REQUIRED'}
    scope = task.get('scope')
    if not isinstance(scope, dict) or scope.get('offer_id') != body.get('offer_id'):
        return {'allowed': False, 'reason': 'TASK_SCOPE_MISMATCH'}
    if not isinstance(release, dict) or task.get('version') != release:
        return {'allowed': False, 'reason': 'RELEASE_MISMATCH'}
    if (not isinstance(idempotency_key, str) or not idempotency_key
            or body.get('request_id') != idempotency_key):
        return {'allowed': False, 'reason': 'IDEMPOTENCY_MISMATCH'}
    return {'allowed': False, 'reason': 'ROUTE_CONTRACT_UNPROVEN'}
