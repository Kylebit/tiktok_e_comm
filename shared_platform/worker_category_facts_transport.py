"""Parent-owned, read-only R1 capture handoff for facts preparation.

This is the *second* stage of a future private category transport. It accepts
only a capture already bound to a live task lease and immutable domain origin.
It neither dispatches options/capture nor gives the Codex child a pipe, lease,
provider object, or HTTP endpoint. Missing/unknown capture fails closed.
"""

from __future__ import annotations

import json

from shared_platform.worker_category_admission import validate
from shared_platform.worker_category_intents import CategoryIntentLedger


class VerifiedFactsCategoryTransport:
    def __init__(self, engine, domain_store, *, worker_id):
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError('parent-held worker identity required')
        self.engine = engine
        self.domain_store = domain_store
        self.worker_id = worker_id

    def verified_capture(self, task, *, lease_token, expected_release):
        """Return source evidence only after task and domain readback agree."""
        if (not isinstance(task, dict) or task.get('version') != expected_release
                or self.engine.release != expected_release
                or not isinstance(lease_token, str) or not lease_token):
            raise ValueError('category release or lease missing')
        task_id = task.get('task_id')
        offer_id = (task.get('scope') or {}).get('offer_id')
        with self.engine.transaction() as conn:
            row = self.engine._lease(conn, task_id, lease_token)
            steps = json.loads(row['steps_json'])
            if (row['worker'] != self.worker_id or row['template'] != 'publication'
                    or steps[row['step_index']]['key'] != 'facts'
                    or json.loads(row['scope_json']).get('offer_id') != offer_id
                    or conn.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',
                                    (task_id,)).fetchone()):
                raise ValueError('category facts task identity invalid')
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workbench_category_intents'").fetchone()
            if not exists:
                raise ValueError('trusted category capture absent')
            intent = conn.execute('SELECT * FROM workbench_category_intents WHERE task_id=? AND action=?',
                                  (task_id, 'capture')).fetchone()
            if intent is None:
                raise ValueError('trusted category capture absent')
            body = json.loads(intent['body_json'])
            request_id = intent['request_id']
            if intent['release_json'] != json.dumps(expected_release, ensure_ascii=False,
                                                    sort_keys=True, separators=(',', ':')):
                raise ValueError('category release identity changed')
        binding = validate(self.engine, task_id=task_id, worker_id=self.worker_id,
                           lease_token=lease_token, action='capture', body=body)
        if binding['request_id'] != request_id:
            raise ValueError('category capture request identity changed')
        result = CategoryIntentLedger().reconcile(self.engine, self.domain_store,
                                                   task_id=task_id, request_id=request_id)
        if result['status'] != 'SUCCEEDED' or not result['result_reference']:
            raise ValueError('trusted category capture is not verified')
        observation = self.domain_store.round1_category_observation(result['result_reference'])
        if (not observation or observation['observer_reference'] != result['result_reference']
                or observation['offer_id'] != offer_id):
            raise ValueError('category observation identity invalid')
        # Reconciliation crosses two stores. The lease may have expired while
        # reading the domain result, so check it again before handing evidence
        # to a child process.
        with self.engine.transaction() as conn:
            row = self.engine._lease(conn, task_id, lease_token)
            if row['worker'] != self.worker_id or row['template'] != 'publication':
                raise ValueError('category facts lease expired during readback')
        return {'schema_version': 'worker-r1-capture-evidence/v1',
                'task_id': task_id, 'release': expected_release,
                'offer_id': offer_id, 'capture_request_id': request_id,
                'observer_reference': result['result_reference'],
                'observation': observation}
