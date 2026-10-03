"""Private JSON-byte pipe for a future isolated worker process.

This module opens no listener, socket or HTTP route. A server process must
create a fresh multiprocessing Pipe and pass one handle only to its trusted
worker child. Task/worker/lease identity is bound server-side at construction;
the child can send only an R1 action/body or read-only status request.
"""

from __future__ import annotations

import json


MAX_FRAME_BYTES = 256 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _unique(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError('duplicate JSON field')
        row[key] = value
    return row


def _parse(frame):
    if not isinstance(frame, bytes) or len(frame) > MAX_FRAME_BYTES:
        raise ValueError('worker frame too large')
    return json.loads(frame.decode('utf-8'), object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


class BoundWorkerCategoryPipe:
    def __init__(self, bridge, *, task_id, worker_id, lease_token):
        if any(not isinstance(value, str) or not value for value in
               (task_id, worker_id, lease_token)):
            raise ValueError('server-held worker session identity required')
        with bridge.engine.transaction() as conn:
            task = bridge.engine._lease(conn, task_id, lease_token)
            if task['worker'] != worker_id or task['template'] != 'publication':
                raise ValueError('server-held worker session invalid')
        self.bridge = bridge
        self.task_id = task_id
        self.worker_id = worker_id
        self.lease_token = lease_token

    def handle_frame(self, frame):
        """Return a bounded result; never expose a domain/provider object."""
        try:
            data = _parse(frame)
            if not isinstance(data, dict):
                raise ValueError('worker request must be an object')
            action = data.get('action')
            if action in {'options', 'capture'} and set(data) == {'action', 'body'} and isinstance(data['body'], dict):
                result = self.bridge.execute(task_id=self.task_id,
                    worker_id=self.worker_id, lease_token=self.lease_token,
                    action=action, body=data['body'])
                return _json({'ok': True, 'request_id': result['request_id'],
                    'status': result['status'],
                    'dispatched_this_call': result['dispatched_this_call']})
            if action == 'status' and set(data) == {'action', 'request_id'} and isinstance(data['request_id'], str):
                with self.bridge.engine.transaction() as conn:
                    task = self.bridge.engine._lease(conn, self.task_id,
                                                     self.lease_token)
                    if task['worker'] != self.worker_id or task['template'] != 'publication':
                        raise ValueError('worker session expired')
                result = self.bridge.intents.reconcile(self.bridge.engine,
                    self.bridge.domain_store, task_id=self.task_id,
                    request_id=data['request_id'])
                return _json({'ok': True, 'request_id': result['request_id'],
                    'status': result['status'], 'dispatched_this_call': False})
        except Exception:
            pass
        return _json({'ok': False, 'code': 'WORKER_CATEGORY_DENIED'})

    def serve_once(self, connection):
        frame = connection.recv_bytes(MAX_FRAME_BYTES)
        response = self.handle_frame(frame)
        connection.send_bytes(response)
        return response


def pipe_request(connection, payload):
    """Worker child helper; transmits JSON bytes rather than pickle objects."""
    frame = _json(payload)
    if len(frame) > MAX_FRAME_BYTES:
        raise ValueError('worker frame too large')
    connection.send_bytes(frame)
    return _parse(connection.recv_bytes(MAX_FRAME_BYTES))
