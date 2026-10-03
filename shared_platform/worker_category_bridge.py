"""Offline trusted worker-to-R1 adapter; no route or worker registration.

Only a server-owned caller with a live task lease may invoke this adapter.
The human HTTP handler does not call it and cannot provide worker_origin.
"""

from __future__ import annotations

from shared_platform.release_store import WorkerCategoryOrigin
from shared_platform.worker_category_admission import validate
from shared_platform.worker_category_intents import CategoryIntentLedger


class WorkerCategoryBridge:
    def __init__(self, engine, domain_store, domain_call):
        self.engine = engine
        self.domain_store = domain_store
        self.domain_call = domain_call
        self.intents = CategoryIntentLedger()

    def execute(self, *, task_id, worker_id, lease_token, action, body):
        """Dispatch only upon a fresh durable intent, then reconcile by ID.

        domain_call(action, body, worker_origin) is a trusted server function,
        not an HTTP client. Any exception/timeout is an ambiguous outcome and
        never grants a replay. Reconciliation uses the domain's durable row.
        """
        binding = validate(self.engine, task_id=task_id, worker_id=worker_id,
                           lease_token=lease_token, action=action, body=body)
        reservation = self.intents.reserve(self.engine, task_id=task_id,
            worker_id=worker_id, lease_token=lease_token, action=action, body=body)
        sent = reservation['dispatch_once']
        domain_response = None
        if sent:
            origin = WorkerCategoryOrigin(task_id=task_id,
                request_id=body['request_id'], purpose=action.upper(),
                release=binding['release'],
                ui_request_digest='sha256:' + binding['body_sha256'])
            try:
                domain_response = self.domain_call(action, body, origin)
            except Exception:
                # The domain may have committed or called the provider. Only
                # readback below can classify it; never dispatch in recovery.
                pass
        result = self.intents.reconcile(self.engine, self.domain_store,
            task_id=task_id, request_id=body['request_id'])
        return {**result, 'dispatched_this_call': sent,
                'domain_response': domain_response}
