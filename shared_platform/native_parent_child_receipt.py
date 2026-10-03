"""Private, fixed parent-captured JSONL; an original attempt is never replayed.

The immutable task event anchors the receipt bytes. Neither this receipt nor a
completed child message is a domain preparation, approval or execution grant.
"""
import hashlib
import json
from pathlib import Path


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _private_directory(adapter, attempt, *, create=False):
    from shared_platform.local_operator_session import (create_owner_only_directory,
        current_windows_owner_sid, verify_owner_only)
    path = Path(attempt['output_path']) / 'private-child'
    if create:
        create_owner_only_directory(path)
    verify_owner_only(path, current_windows_owner_sid())
    return path


def original_attempt(adapter, task, token, stage, binding=None):
    adapter._context(task, token)
    with adapter.engine.transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_category_agent_attempts'").fetchone():
            return None
        row = db.execute('SELECT * FROM workbench_category_agent_attempts '
            'WHERE task_id=? AND step_key=? AND stage=?', (task['task_id'], 'facts', stage)).fetchone()
        if row is None:
            return None
        value = dict(row)
        if (value['release_json'] != _json(adapter.engine.release).decode()
                or (binding is not None and value['input_json'] != _json(binding).decode())):
            raise ValueError('R1_CHILD_ORIGINAL_ATTEMPT_CHANGED')
        root = adapter.profile.data_root / 'artifacts' / task['task_id']
        output = Path(value['output_path'])
        facts = output.parent if stage == 'choice' else output
        if (not output.is_absolute() or '..' in output.parts or facts.parent != root
                or not facts.name.startswith('facts-') or not facts.name[6:].isdigit()
                or (stage == 'choice' and output.name != 'category-choice')):
            raise ValueError('R1_CHILD_ORIGINAL_OUTPUT_INVALID')
        return value


def retain(adapter, task, token, stage, result, *, evidence, schema, binding, attempt):
    from shared_platform.native_parent_facts import _read
    from shared_platform.operations_runtime import _create_agent_artifact, _session_id
    from shared_platform.worker_category_readonly_cli import MAX_JSONL_BYTES, ReadonlyCodexResult
    # This exact row was read under the live lease before launch. An expired
    # lease may retain observational bytes, but can never adopt the proposal.
    with adapter.engine.transaction() as db:
        row = db.execute('SELECT * FROM workbench_category_agent_attempts '
            'WHERE task_id=? AND step_key=? AND stage=?', (task['task_id'], 'facts', stage)).fetchone()
        current = adapter.engine._row(db, task['task_id'])
        if (row is None or dict(row) != attempt
                or attempt['lease_sha256'] != hashlib.sha256(token.encode()).hexdigest()
                or attempt['worker_id'] != adapter.worker_id
                or attempt['input_json'] != _json(binding).decode()
                or json.loads(current['version_json']) != adapter.engine.release
                or json.loads(current['scope_json']) != task['scope']):
            raise ValueError('R1_CHILD_CAPTURE_ATTEMPT_CHANGED')
    if type(result) is not ReadonlyCodexResult:
        raise ValueError('R1_CHILD_CAPTURE_ATTEMPT_REQUIRED')
    if (type(result.stdout) is not str or type(result.overflow) is not bool
            or type(result.timed_out) is not bool
            or (result.returncode is not None and type(result.returncode) is not int)):
        raise ValueError('R1_CHILD_CAPTURE_INVALID')
    raw = result.stdout.encode('utf-8')
    if len(raw) > MAX_JSONL_BYTES:
        raise ValueError('R1_CHILD_CAPTURE_TOO_LARGE')
    session = _session_id(result.stdout)
    if session is not None and (len(session) > 256 or any(ord(c) < 32 for c in session)):
        raise ValueError('R1_CHILD_SESSION_INVALID')
    directory = _private_directory(adapter, attempt, create=True)
    receipt = {'schema_version': 'native-r1-private-child-receipt/v1',
        'attempt': attempt, 'task_scope': task['scope'],
        'evidence_sha256': hashlib.sha256(evidence).hexdigest(),
        'schema_sha256': hashlib.sha256(schema).hexdigest(),
        'output_sha256': hashlib.sha256(raw).hexdigest(), 'output_size': len(raw),
        'session_id': session, 'returncode': result.returncode,
        'timed_out': result.timed_out, 'overflow': result.overflow}
    encoded = _json(receipt)
    # The surrounding parent hold and this subdirectory pin remain active.
    # Fixed exclusive leaves cannot overwrite an old or partial original result.
    with adapter.boundary._pin([directory]):
        for name, data in (('child-output.jsonl', raw), ('child-receipt.json', encoded)):
            path = directory / name
            adapter.boundary._check_leaf(path)
            _create_agent_artifact(path, data)
            if _read(path) != data:
                raise ValueError('R1_CHILD_CAPTURE_FILE_CHANGED')
    digest = hashlib.sha256(encoded).hexdigest()
    with adapter.engine.transaction() as db:
        current = adapter.engine._row(db, task['task_id'])
        if (json.loads(current['version_json']) != adapter.engine.release
                or json.loads(current['scope_json']) != task['scope']
                or current['template'] != 'publication'
                or json.loads(current['steps_json'])[current['step_index']]['key'] != 'facts'
                or db.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',
                              (task['task_id'],)).fetchone()):
            raise ValueError('R1_CHILD_CAPTURE_SOURCE_CHANGED')
        adapter.engine._event(db, task['task_id'], 'r1_typed_child_output_retained',
            {'stage': stage, 'receipt_sha256': digest})
    return {'stage': stage, 'receipt_sha256': digest}


def read_retained(adapter, task, token, stage, *, evidence, schema, binding):
    from shared_platform.native_parent_facts import _read, _unique, _nonfinite
    from shared_platform.operations_runtime import _session_id
    from shared_platform.worker_category_readonly_cli import ReadonlyCodexResult
    attempt = original_attempt(adapter, task, token, stage, binding)
    if attempt is None:
        raise ValueError('R1_CHILD_ORIGINAL_ATTEMPT_REQUIRED')
    directory = _private_directory(adapter, attempt)
    from shared_platform.local_operator_session import current_windows_owner_sid, verify_owner_only
    for name in ('child-output.jsonl', 'child-receipt.json'):
        verify_owner_only(directory / name, current_windows_owner_sid(), protected=False)
    with adapter.boundary._pin([directory]):
        encoded = _read(directory / 'child-receipt.json')
        raw = _read(directory / 'child-output.jsonl')
    digest = hashlib.sha256(encoded).hexdigest()
    with adapter.engine.transaction() as db:
        adapter.engine._lease(db, task['task_id'], token)
        records = db.execute("SELECT detail_json FROM workbench_events WHERE task_id=? "
            "AND event_type='r1_typed_child_output_retained'", (task['task_id'],)).fetchall()
    if sum(json.loads(row[0]) == {'stage': stage, 'receipt_sha256': digest} for row in records) != 1:
        raise ValueError('R1_CHILD_RETAINED_RECEIPT_UNBOUND')
    receipt = json.loads(encoded, object_pairs_hook=_unique, parse_constant=_nonfinite)
    keys = {'schema_version', 'attempt', 'task_scope', 'evidence_sha256', 'schema_sha256',
            'output_sha256', 'output_size', 'session_id', 'returncode', 'timed_out', 'overflow'}
    if (type(receipt) is not dict or set(receipt) != keys
            or receipt['schema_version'] != 'native-r1-private-child-receipt/v1'
            or receipt['attempt'] != attempt or receipt['task_scope'] != task['scope']
            or receipt['evidence_sha256'] != hashlib.sha256(evidence).hexdigest()
            or receipt['schema_sha256'] != hashlib.sha256(schema).hexdigest()
            or receipt['output_sha256'] != hashlib.sha256(raw).hexdigest()
            or type(receipt['output_size']) is not int or receipt['output_size'] != len(raw)
            or type(receipt['timed_out']) is not bool or receipt['timed_out']
            or type(receipt['overflow']) is not bool or receipt['overflow']
            or type(receipt['returncode']) is not int or receipt['returncode'] != 0):
        raise ValueError('R1_CHILD_RETAINED_OUTCOME_UNPROVEN')
    text = raw.decode('utf-8')
    if receipt['session_id'] != _session_id(text) or not receipt['session_id']:
        raise ValueError('R1_CHILD_RETAINED_SESSION_CHANGED')
    # Original strict completed-turn and proposal validators still run next.
    return ReadonlyCodexResult(0, text), {'stage': stage, 'receipt_sha256': digest}
