"""Task-ledger authority check for future worker R1 category observations.

Only options and v3 capture are considered. This module does not expose an
HTTP route, call the category provider, or approve a product. A future route
must invoke this with a server-held worker lease before domain dispatch and
must retain the domain's own request-id/UNKNOWN reconciliation contract.
"""

from __future__ import annotations

import hashlib
import json
import re

from shared_platform.round1_category_observations import (
    OPTIONS_REQUEST_SCHEMA, SELECTION_REQUEST_SCHEMA,
)


COMMON_FIELDS = frozenset({
    'schema_version', 'request_id', 'context_digest', 'offer_id',
    'product_center_revision', 'requested_targets', 'source_region',
    'account_identity_digest',
})
CAPTURE_FIELDS = frozenset({
    'options_reference', 'options_digest', 'selected_category_identity',
    'attribute_selections',
})
SCHEMAS = {'options': OPTIONS_REQUEST_SCHEMA, 'capture': SELECTION_REQUEST_SCHEMA}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def request_id(task_id, action, body):
    """Deterministic domain key; changed inputs cannot reuse an old request."""
    if action not in SCHEMAS or not isinstance(task_id, str) or not task_id.startswith('TASK-'):
        raise ValueError('unsupported worker category request identity')
    if not isinstance(body, dict):
        raise ValueError('category body must be an object')
    payload = {key: value for key, value in body.items() if key != 'request_id'}
    digest = hashlib.sha256(_canonical([task_id, action, payload]).encode('utf-8')).hexdigest()
    return 'wkr-' + digest


def _body(action, body, task_id):
    required = COMMON_FIELDS | (CAPTURE_FIELDS if action == 'capture' else frozenset())
    if (not isinstance(body, dict) or set(body) != required
            or body.get('schema_version') != SCHEMAS[action]
            or body.get('request_id') != request_id(task_id, action, body)
            or not isinstance(body.get('offer_id'), str)
            or not re.fullmatch(r'[0-9]{1,32}', body['offer_id'])
            or type(body.get('product_center_revision')) is not int
            or body['product_center_revision'] < 0
            or body.get('source_region') not in {'PH', 'MY', 'TH', 'VN'}
            or not isinstance(body.get('requested_targets'), list)
            or not body['requested_targets']
            or any(not isinstance(target, str) or not target for target in body['requested_targets'])
            or len(set(body['requested_targets'])) != len(body['requested_targets'])
            or any(not isinstance(body.get(key), str)
                   or not re.fullmatch(r'sha256:[0-9a-f]{64}', body[key])
                   for key in ('context_digest', 'account_identity_digest'))):
        raise ValueError('worker category request contract invalid')
    if action == 'capture' and (
            not isinstance(body['options_reference'], str)
            or not isinstance(body['options_digest'], str)
            or not isinstance(body['selected_category_identity'], str)
            or not isinstance(body['attribute_selections'], list)):
        raise ValueError('worker category selection contract invalid')


def validate_in_transaction(engine, conn, *, task_id, worker_id, lease_token,
                            action, body):
    """Check the complete admission contract inside the caller's transaction.

    Intent reservation must use this on the same connection as its insert, so
    a facts-step or target-scope change cannot occur between admission and the
    one-shot dispatch grant.
    """
    if action not in SCHEMAS:
        raise ValueError('only preapproval category observations are eligible')
    _body(action, body, task_id)
    row = engine._lease(conn, task_id, lease_token)
    if (row['worker'] != worker_id or row['template'] != 'publication'
            or conn.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',
                            (task_id,)).fetchone()):
        raise ValueError('category task worker identity invalid')
    steps = json.loads(row['steps_json'])
    if steps[row['step_index']]['key'] != 'facts':
        raise ValueError('category observation outside facts step')
    scope = json.loads(row['scope_json'])
    if scope.get('offer_id') != body['offer_id']:
        raise ValueError('category offer differs from task scope')
    authorized_targets = scope.get('shops')
    # A Product Center selection is domain input, not permission for this
    # worker task to expand into an arbitrary market. New offer tasks may
    # start with shops=[], but require a separate server-owned frozen
    # target binding before any private category dispatch; until then the
    # admission contract stays closed.
    if not isinstance(authorized_targets, list) or not authorized_targets:
        raise ValueError('category task target scope is not frozen')
    if not set(body['requested_targets']).issubset(set(authorized_targets)):
        raise ValueError('category request target exceeds task scope')
    release = json.loads(row['version_json'])
    return {
        'task_id': task_id, 'offer_id': body['offer_id'], 'action': action,
        'request_id': body['request_id'], 'release': release,
        'body_sha256': hashlib.sha256(_canonical(body).encode('utf-8')).hexdigest(),
    }


def validate(engine, *, task_id, worker_id, lease_token, action, body):
    """Read the trusted task ledger, then return a non-authorizing binding."""
    with engine.transaction() as conn:
        return validate_in_transaction(engine, conn, task_id=task_id,
            worker_id=worker_id, lease_token=lease_token, action=action,
            body=body)
