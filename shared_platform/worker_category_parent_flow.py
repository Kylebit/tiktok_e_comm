"""Private parent-process R1 category sequence; no worker or route activation.

The caller owns the live task lease and obtains the options context from the
server's reviewed Product Center facts. A structured choice is required before
capture. Only the existing one-shot bridge may dispatch official read-only
GETs, and only the existing facts transport may hand capture to an agent.
"""

from __future__ import annotations

import json

from shared_platform.round1_category_observations import (
    OPTIONS_REQUEST_SCHEMA, SELECTION_REQUEST_SCHEMA, options_projection,
    resolve_options_selection,
)
from shared_platform.worker_category_admission import request_id, validate
from shared_platform.worker_category_bridge import WorkerCategoryBridge
from shared_platform.worker_category_facts_transport import VerifiedFactsCategoryTransport


_CONTEXT = frozenset({
    'context_digest', 'offer_id', 'product_center_revision',
    'requested_targets', 'source_region', 'account_identity_digest',
})
_SELECTION = frozenset({
    'options_reference', 'options_digest', 'selected_category_identity',
    'attribute_selections',
})


class ParentR1CategoryFlow:
    """Bounded orchestration for one parent-held worker identity.

    Construction is deliberately dependency-injected. No production store,
    domain provider, socket, or subprocess is selected by this module.
    """

    def __init__(self, bridge, transport, *, worker_id):
        if (not isinstance(bridge, WorkerCategoryBridge)
                or not isinstance(transport, VerifiedFactsCategoryTransport)
                or not isinstance(worker_id, str) or not worker_id
                or bridge.engine is not transport.engine
                or bridge.domain_store is not transport.domain_store
                or transport.worker_id != worker_id):
            raise ValueError('parent category bridge identity invalid')
        self.bridge = bridge
        self.transport = transport
        self.worker_id = worker_id

    def _task(self, task, lease_token):
        if (type(task) is not dict or task.get('version') != self.bridge.engine.release
                or type(task.get('task_id')) is not str):
            raise ValueError('parent category task version invalid')
        with self.bridge.engine.transaction() as conn:
            row = self.bridge.engine._lease(conn, task['task_id'], lease_token)
            if (row['worker'] != self.worker_id or row['template'] != 'publication'
                    or json.loads(row['scope_json']) != task.get('scope')
                    or json.loads(row['version_json']) != task['version']
                    or json.loads(row['steps_json'])[row['step_index']]['key'] != 'facts'
                    or conn.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',
                                    (task['task_id'],)).fetchone()):
                raise ValueError('parent category task lease or scope invalid')
        return task['task_id']

    def options(self, task, *, lease_token, context):
        """Dispatch options once and return only a verified, selectable projection."""
        task_id = self._task(task, lease_token)
        if type(context) is not dict or set(context) != _CONTEXT:
            raise ValueError('parent category context invalid')
        body = dict(context, schema_version=OPTIONS_REQUEST_SCHEMA)
        body['request_id'] = request_id(task_id, 'options', body)
        result = self.bridge.execute(task_id=task_id, worker_id=self.worker_id,
            lease_token=lease_token, action='options', body=body)
        packet = {'status': result['status'], 'request_id': body['request_id'],
                  'dispatched_this_call': result['dispatched_this_call']}
        if result['status'] == 'SUCCEEDED':
            record = self.bridge.domain_store.category_options_record(
                result['result_reference'], body['offer_id'])
            if (record['options_reference'] != result['result_reference']
                    or record['review_input']['product_center_revision'] != body['product_center_revision']
                    or record['review_input']['target_selection']['requested'] != body['requested_targets']
                    or record['source_region'] != body['source_region']
                    or record['account_identity_digest'] != body['account_identity_digest']):
                raise ValueError('parent category options readback mismatch')
            packet['projection'] = options_projection(record)
            validate(self.bridge.engine, task_id=task_id, worker_id=self.worker_id,
                     lease_token=lease_token, action='options', body=body)
        return packet

    def capture(self, task, *, lease_token, selection):
        """Validate one structured choice against same-task options, then capture."""
        task_id = self._task(task, lease_token)
        if type(selection) is not dict or set(selection) != _SELECTION:
            raise ValueError('parent category selection invalid')
        with self.bridge.engine.transaction() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='workbench_category_intents'").fetchone():
                raise ValueError('same-task options intent absent or stale')
            row = conn.execute('SELECT * FROM workbench_category_intents WHERE task_id=? AND action=?',
                               (task_id, 'options')).fetchone()
            if row is None or row['release_json'] != json.dumps(
                    self.bridge.engine.release, ensure_ascii=False, sort_keys=True,
                    separators=(',', ':')):
                raise ValueError('same-task options intent absent or stale')
            options_body = json.loads(row['body_json'])
            options_request_id = row['request_id']
        validate(self.bridge.engine, task_id=task_id, worker_id=self.worker_id,
                 lease_token=lease_token, action='options', body=options_body)
        prior = self.bridge.intents.reconcile(self.bridge.engine,
            self.bridge.domain_store, task_id=task_id, request_id=options_request_id)
        if prior['status'] != 'SUCCEEDED':
            raise ValueError('same-task options result is not verified')
        if (selection['options_reference'] != prior['result_reference']
                or selection['options_digest'] != self._options_digest(task_id)):
            raise ValueError('selection differs from same-task options')
        record = self.bridge.domain_store.category_options_record(
            prior['result_reference'], options_body['offer_id'])
        if (record['options_digest'] != selection['options_digest']
                or record['review_input']['target_selection']['requested'] != options_body['requested_targets']):
            raise ValueError('selection options readback mismatch')
        resolve_options_selection(record, selection['selected_category_identity'],
                                  selection['attribute_selections'])
        body = {key: options_body[key] for key in _CONTEXT}
        body.update(selection)
        body['schema_version'] = SELECTION_REQUEST_SCHEMA
        body['request_id'] = request_id(task_id, 'capture', body)
        result = self.bridge.execute(task_id=task_id, worker_id=self.worker_id,
            lease_token=lease_token, action='capture', body=body)
        packet = {'status': result['status'], 'request_id': body['request_id'],
                  'dispatched_this_call': result['dispatched_this_call']}
        if result['status'] == 'SUCCEEDED':
            packet['verified_capture'] = self.transport.verified_capture(
                task, lease_token=lease_token, expected_release=self.bridge.engine.release)
        return packet

    def _options_digest(self, task_id):
        with self.bridge.engine.transaction() as conn:
            row = conn.execute('SELECT options_digest FROM workbench_category_intents '
                               'WHERE task_id=? AND action=?', (task_id, 'options')).fetchone()
            if row is None or not row['options_digest']:
                raise ValueError('same-task options digest absent')
            return row['options_digest']

    def verified_capture(self, task, *, lease_token):
        """Read-only handoff after a previous capture; never dispatches."""
        self._task(task, lease_token)
        return self.transport.verified_capture(
            task, lease_token=lease_token, expected_release=self.bridge.engine.release)
