"""Task-ledger one-shot grants for the two offline R1 agent stages.

An output directory is evidence, not authority to launch a child. The task DB
commits an immutable attempt before either child starts. A crash after commit
can strand the attempt as UNKNOWN; a new lease or output path never replays it.
"""

from __future__ import annotations

import hashlib
import json


_TABLE = """CREATE TABLE IF NOT EXISTS workbench_category_agent_attempts (
 task_id TEXT NOT NULL,
 step_key TEXT NOT NULL CHECK(step_key='facts'),
 stage TEXT NOT NULL CHECK(stage IN ('choice','facts')),
 worker_id TEXT NOT NULL,
 release_json TEXT NOT NULL,
 lease_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL,
 output_path TEXT NOT NULL,
 PRIMARY KEY(task_id,step_key,stage)
 )"""
_IMMUTABLE = """CREATE TRIGGER IF NOT EXISTS workbench_category_agent_attempt_immutable
BEFORE UPDATE ON workbench_category_agent_attempts
BEGIN SELECT RAISE(ABORT, 'immutable category agent attempt'); END"""
_NO_DELETE = """CREATE TRIGGER IF NOT EXISTS workbench_category_agent_attempt_no_delete
BEFORE DELETE ON workbench_category_agent_attempts
BEGIN SELECT RAISE(ABORT, 'category agent attempt cannot be deleted'); END"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


class CategoryAgentAttemptLedger:
    def reserve(self, engine, *, task, worker_id, lease_token, stage,
                input_binding, output_path):
        """Return a one-shot child launch grant, bound to the live facts lease."""
        if (stage not in {'choice', 'facts'} or type(input_binding) is not dict
                or type(output_path) is not str or not output_path
                or type(task) is not dict or type(task.get('task_id')) is not str
                or type(worker_id) is not str or not worker_id
                or type(lease_token) is not str or not lease_token):
            raise ValueError('category agent attempt identity invalid')
        expected_keys = ({'options_request_id', 'options_reference', 'options_digest'}
                         if stage == 'choice' else
                         {'capture_request_id', 'observer_reference'})
        if (set(input_binding) != expected_keys
                or any(type(value) is not str or not value
                       for value in input_binding.values())):
            raise ValueError('category agent input binding invalid')
        task_id = task['task_id']
        with engine.transaction() as conn:
            row = engine._lease(conn, task_id, lease_token)
            steps = json.loads(row['steps_json'])
            if (row['worker'] != worker_id or row['template'] != 'publication'
                    or steps[row['step_index']]['key'] != 'facts'
                    or task.get('version') != engine.release
                    or json.loads(row['version_json']) != task['version']
                    or json.loads(row['scope_json']) != task.get('scope')
                    or conn.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',
                                    (task_id,)).fetchone()):
                raise ValueError('category agent task lease or scope invalid')
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='workbench_category_intents'").fetchone():
                raise ValueError('category agent source intent absent')
            action = 'options' if stage == 'choice' else 'capture'
            source = conn.execute('SELECT * FROM workbench_category_intents '
                                  'WHERE task_id=? AND action=?',
                                  (task_id, action)).fetchone()
            if (source is None or source['status'] != 'SUCCEEDED'
                    or source['release_json'] != _json(engine.release)):
                raise ValueError('category agent source intent not verified')
            if stage == 'choice':
                matches = (source['request_id'] == input_binding['options_request_id']
                           and source['result_reference'] == input_binding['options_reference']
                           and source['options_digest'] == input_binding['options_digest'])
            else:
                matches = (source['request_id'] == input_binding['capture_request_id']
                           and source['result_reference'] == input_binding['observer_reference'])
            if not matches:
                raise ValueError('category agent input differs from task intent')
            # DDL is executed one statement at a time; executescript would
            # commit the engine's BEGIN IMMEDIATE transaction prematurely.
            for statement in (_TABLE, _IMMUTABLE, _NO_DELETE):
                conn.execute(statement)
            old = conn.execute('SELECT * FROM workbench_category_agent_attempts '
                               'WHERE task_id=? AND step_key=? AND stage=?',
                               (task_id, 'facts', stage)).fetchone()
            input_json = _json(input_binding)
            if old is not None:
                if (old['worker_id'] != worker_id
                        or old['release_json'] != _json(engine.release)
                        or old['input_json'] != input_json):
                    raise ValueError('category agent attempt already binds another identity')
                return {'started_this_call': False, 'output_path': old['output_path']}
            conn.execute('INSERT INTO workbench_category_agent_attempts '
                         '(task_id,step_key,stage,worker_id,release_json,lease_sha256,input_json,output_path) '
                         'VALUES (?,?,?,?,?,?,?,?)',
                         (task_id, 'facts', stage, worker_id, _json(engine.release),
                          hashlib.sha256(lease_token.encode('utf-8')).hexdigest(),
                          input_json, output_path))
            engine._event(conn, task_id, 'category_agent_attempt_reserved',
                          {'stage': stage, 'output_path': output_path,
                           'input_binding': input_binding})
        return {'started_this_call': True, 'output_path': output_path}
