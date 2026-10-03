"""Offline R1 worker intent ledger. No HTTP route or provider dispatch.

The task DB and release DB cannot commit atomically. A committed task intent is
therefore a one-shot dispatch grant: only the caller that inserted it may send
the matching domain request. After a crash, even an absent domain request is
UNKNOWN, never authority to send again. Domain reconciliation is read-only and
may prove a result, but cannot reconstruct a lost dispatch grant.
"""

from __future__ import annotations

import hashlib
import json

from shared_platform.worker_category_admission import validate_in_transaction


_TABLE = """CREATE TABLE IF NOT EXISTS workbench_category_intents (
 request_id TEXT PRIMARY KEY,
 task_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('options','capture')),
 body_json TEXT NOT NULL,
 release_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('RESERVED','UNKNOWN','FAILED','SUCCEEDED')),
 result_reference TEXT,
 options_digest TEXT,
 UNIQUE(task_id,action)
 )"""
_IMMUTABLE = """CREATE TRIGGER IF NOT EXISTS workbench_category_intent_identity_immutable
BEFORE UPDATE ON workbench_category_intents
WHEN NEW.request_id != OLD.request_id OR NEW.task_id != OLD.task_id
 OR NEW.action != OLD.action OR NEW.body_json != OLD.body_json
 OR NEW.release_json != OLD.release_json
BEGIN SELECT RAISE(ABORT, 'immutable category intent'); END"""
_NO_DELETE = """CREATE TRIGGER IF NOT EXISTS workbench_category_intent_no_delete
BEFORE DELETE ON workbench_category_intents
BEGIN SELECT RAISE(ABORT, 'category intent cannot be deleted'); END"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _digest(value):
    return 'sha256:' + hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


class CategoryIntentLedger:
    def reserve(self, engine, *, task_id, worker_id, lease_token, action, body):
        """Persist one task-bound intent; only a new row grants one dispatch."""
        body_json = _json(body)
        with engine.transaction() as conn:
            binding = validate_in_transaction(engine, conn, task_id=task_id,
                worker_id=worker_id, lease_token=lease_token, action=action,
                body=body)
            release_json = _json(binding['release'])
            # executescript() would commit the engine's active transaction.
            for statement in (_TABLE, _IMMUTABLE, _NO_DELETE):
                conn.execute(statement)
            old = conn.execute('SELECT * FROM workbench_category_intents WHERE task_id=? AND action=?',
                               (task_id, action)).fetchone()
            if old:
                if (old['request_id'] != body['request_id'] or old['body_json'] != body_json
                        or old['release_json'] != release_json):
                    raise ValueError('category intent already binds a different request')
                return {'request_id': old['request_id'], 'status': old['status'],
                        'dispatch_once': False}
            if action == 'capture':
                prior = conn.execute('SELECT * FROM workbench_category_intents WHERE task_id=? AND action=?',
                                     (task_id, 'options')).fetchone()
                if not prior or prior['status'] != 'SUCCEEDED':
                    raise ValueError('same-task options result is not verified')
                options = json.loads(prior['body_json'])
                for key in ('offer_id', 'product_center_revision', 'requested_targets',
                            'source_region', 'account_identity_digest', 'context_digest'):
                    if body[key] != options[key]:
                        raise ValueError('capture context differs from same-task options')
                if (body['options_reference'] != prior['result_reference']
                        or body['options_digest'] != prior['options_digest']):
                    raise ValueError('capture options reference differs from same-task result')
            conn.execute('INSERT INTO workbench_category_intents VALUES (?,?,?,?,?,?,?,?)',
                         (body['request_id'], task_id, action, body_json, release_json,
                          'RESERVED', None, None))
        return {'request_id': body['request_id'], 'status': 'RESERVED',
                'dispatch_once': True}

    def reconcile(self, engine, domain, *, task_id, request_id):
        """Read the domain ledger and persist only verified results; no retry."""
        with engine.transaction() as conn:
            task = engine._row(conn, task_id)
            engine._require_version(task)
            row = conn.execute('SELECT * FROM workbench_category_intents WHERE request_id=? AND task_id=?',
                               (request_id, task_id)).fetchone()
            if row is None or json.loads(row['release_json']) != engine.release:
                raise ValueError('task-bound category intent missing or stale')
            intent = dict(row)
        body = json.loads(intent['body_json'])
        domain_row = domain.category_capture_request(request_id, body['offer_id'], 'worker-reconcile')
        status = 'UNKNOWN'; reference = None; options_digest = None
        if domain_row is not None:
            origin = domain.category_worker_origin(request_id)
            if (origin is None or origin.get('request_id') != request_id
                    or origin.get('task_id') != task_id
                    or origin.get('purpose') != intent['action'].upper()
                    or origin.get('release') != engine.release
                    or origin.get('ui_request_digest') != _digest(body)):
                raise ValueError('domain request lacks exact trusted task origin')
            try:
                payload = json.loads(domain_row['request_json'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('domain request identity unreadable') from None
            if (domain_row['request_id'] != request_id
                    or domain_row['offer_id'] != body['offer_id']
                    or payload.get('_ui_request_digest') != _digest(body)
                    or any(payload.get(key) != value for key, value in body.items())
                    or domain.category_request_progress(request_id)['purpose'] != intent['action'].upper()):
                raise ValueError('domain request does not match task intent')
            domain_status = domain_row['status']
            if domain_status not in {'IN_PROGRESS', 'UNKNOWN', 'FAILED', 'SUCCEEDED'}:
                raise ValueError('domain status invalid')
            status = 'UNKNOWN' if domain_status == 'IN_PROGRESS' else domain_status
            if status == 'SUCCEEDED':
                reference = domain_row['observer_reference']
                if not isinstance(reference, str) or not reference:
                    raise ValueError('domain success lacks immutable reference')
                if intent['action'] == 'options':
                    option = domain.category_options_record(reference, body['offer_id'])
                    if (option['options_reference'] != reference
                            or option['review_input']['offer_id'] != body['offer_id']
                            or option['review_input']['product_center_revision'] != body['product_center_revision']
                            or option['source_region'] != body['source_region']
                            or option['account_identity_digest'] != body['account_identity_digest']
                            or not isinstance(option['options_digest'], str)
                            or not option['options_digest'].startswith('sha256:')):
                        raise ValueError('domain options result context mismatch')
                    options_digest = option['options_digest']
                else:
                    observed = domain.round1_category_observation(reference)
                    if (not observed or observed['observer_reference'] != reference
                            or observed['offer_id'] != body['offer_id']):
                        raise ValueError('domain capture result context mismatch')
        with engine.transaction() as conn:
            task = engine._row(conn, task_id)
            engine._require_version(task)
            old = conn.execute('SELECT * FROM workbench_category_intents WHERE request_id=? AND task_id=?',
                               (request_id, task_id)).fetchone()
            if old is None or old['body_json'] != intent['body_json']:
                raise ValueError('task intent changed during reconciliation')
            if old['status'] in {'SUCCEEDED', 'FAILED'}:
                if status == 'UNKNOWN':
                    # Another reconciler may have read the domain before its
                    # result commit, then arrived after a newer terminal read.
                    status = old['status']
                    reference = old['result_reference']
                    options_digest = old['options_digest']
                elif old['status'] != status or (status == 'SUCCEEDED' and
                        (old['result_reference'] != reference or
                         old['options_digest'] != options_digest)):
                    raise ValueError('terminal category outcome changed')
            else:
                conn.execute('UPDATE workbench_category_intents SET status=?,result_reference=?,options_digest=? WHERE request_id=?',
                             (status, reference, options_digest, request_id))
        return {'request_id': request_id, 'status': status,
                'result_reference': reference, 'dispatch_once': False}
