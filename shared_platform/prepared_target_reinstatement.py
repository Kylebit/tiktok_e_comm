"""One trusted append-only successor to one pre-submit target cancellation.

This local coordinator protocol has no HTTP admission and grants no COMMON,
native actor, final review, paid, credential or marketplace execution authority.
The original cancellation, task/version/checkpoint and R1/R2 remain unchanged.
"""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
from datetime import datetime
import hashlib
import json
import sqlite3

from shared_platform.prepared_target_reduction import canonical_digest, _labels, _digest

TABLE = 'workbench_publication_target_reinstatements'
SCHEMA = {
    ('table', TABLE): 'CREATE TABLE workbench_publication_target_reinstatements '
        '(task_id TEXT PRIMARY KEY REFERENCES workbench_execution(task_id), '
        'request_digest TEXT NOT NULL, record_json TEXT NOT NULL)',
    ('trigger', TABLE + '_no_update'): 'CREATE TRIGGER workbench_publication_target_reinstatements_no_update '
        "BEFORE UPDATE ON workbench_publication_target_reinstatements BEGIN SELECT RAISE(ABORT, 'target reinstatement is immutable'); END",
    ('trigger', TABLE + '_no_delete'): 'CREATE TRIGGER workbench_publication_target_reinstatements_no_delete '
        "BEFORE DELETE ON workbench_publication_target_reinstatements BEGIN SELECT RAISE(ABORT, 'target reinstatement is immutable'); END",
}


def _timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('REINSTATEMENT_AUTHORITY_TIMESTAMP_REQUIRED')
    return stamp


def build_reinstatement(request, *, reduction, cancellation_event_id, authority_bytes,
                        expected_authority_sha256):
    """Bind direct-user evidence bytes to the unchanged original lineage."""
    try:
        expected_request = {'schema', 'task_id', 'offer_id', 'cancellation_event_id', 'cancellation_digest',
            'cancellation_request_digest', 'authority_sha256', 'desired_business_scope', 'predecessor_lineage',
            'execution_authority', 'user_final_publication_approval', 'business_writes', 'paid_calls', 'new_refreshes'}
        expected_authority = {'schema', 'task_id', 'offer_id', 'decision', 'restored_target', 'configured_shop_id',
            'complete_target_labels', 'target_count', 'user_message_id', 'user_message_at_utc',
            'verbatim_user_instruction', 'received_via_parent_thread', 'authority_origin',
            'new_final_publication_approval_granted', 'new_paid_or_refresh_authorization_granted',
            'new_security_or_service_authorization_granted'}
        if (type(request) is not dict or set(request) != expected_request
                or type(authority_bytes) is not bytes or type(cancellation_event_id) is not int
                or cancellation_event_id < 1
                or hashlib.sha256(authority_bytes).hexdigest() != _digest(expected_authority_sha256)
                or request['authority_sha256'] != expected_authority_sha256):
            raise ValueError('REINSTATEMENT_EXACT_AUTHORITY_BYTES_REQUIRED')
        authority = json.loads(authority_bytes.decode('utf-8'))
        original = reduction['original_scope']
        removed = reduction['authority']['removed_target']
        from shared_platform.prepared_target_reduction import select_marketplace_targets
        selected = select_marketplace_targets(original['shops'], offer_id=reduction['offer_id'],
            r2_identity=reduction['predecessor_lineage']['predecessor_R2_identity'], reduction=reduction)
        if (type(authority) is not dict or set(authority) != expected_authority
                or request['schema'] != 'prepared-product-target-reinstatement-request/v1'
                or authority['schema'] != 'exact-product-target-reinstatement-authority/v1'
                or request['task_id'] != reduction['task_id'] or authority['task_id'] != reduction['task_id']
                or request['offer_id'] != reduction['offer_id'] or authority['offer_id'] != reduction['offer_id']
                or request['cancellation_event_id'] != cancellation_event_id
                or type(request['cancellation_event_id']) is not int
                or request['cancellation_digest'] != reduction['reduction_digest']
                or request['cancellation_request_digest'] != reduction['request_digest']
                or request['predecessor_lineage'] != reduction['predecessor_lineage']
                or request['desired_business_scope'] != original
                or authority['decision'] != 'RESTORE_EXACT_CANCELLED_TARGET'
                or authority['authority_origin'] != 'DIRECT_USER_MESSAGE'
                or authority['restored_target'] != removed['target']
                or authority['configured_shop_id'] != removed['configured_shop_id']
                or _labels(authority['complete_target_labels']) != _labels(original['shops'])
                or type(authority['target_count']) is not int or authority['target_count'] != len(original['shops'])
                or sorted([*selected, removed['target']]) != _labels(original['shops'])
                or any(type(authority.get(key)) is not str or not authority[key].strip() for key in
                       ('user_message_id','user_message_at_utc','verbatim_user_instruction','received_via_parent_thread'))
                or authority['user_message_id'] == reduction['authority']['user_message_id']
                or _timestamp(authority['user_message_at_utc']) <= _timestamp(reduction['authority']['user_message_at_utc'])
                or request['execution_authority'] is not False or request['user_final_publication_approval'] is not False
                or any(type(request[key]) is not int or request[key] != 0
                       for key in ('business_writes','paid_calls','new_refreshes'))
                or any(authority[key] is not False for key in ('new_final_publication_approval_granted',
                    'new_paid_or_refresh_authorization_granted','new_security_or_service_authorization_granted'))):
            raise ValueError('REINSTATEMENT_AUTHORITY_OR_FROZEN_LINEAGE_CONFLICT')
        body = {'schema_version':'prepared-publication-target-reinstatement/v1',
            'task_id':reduction['task_id'],'offer_id':reduction['offer_id'],
            'request_digest':canonical_digest(request),'request':deepcopy(request),'authority':authority,
            'authority_evidence_utf8':authority_bytes.decode('utf-8'),'authority_sha256':expected_authority_sha256,
            'cancellation_event_id':cancellation_event_id,'cancellation_digest':reduction['reduction_digest'],
            'cancellation_request_digest':reduction['request_digest'],
            'original_version':deepcopy(reduction['original_version']),
            'checkpoint_sha256':reduction['checkpoint_sha256'],'prepared_reference':reduction['prepared_reference'],
            'predecessor_lineage':deepcopy(reduction['predecessor_lineage']),
            'effective_scope':deepcopy(original),'restored_target':{**deepcopy(removed),'status':'USER_REINSTATED_THIS_PRODUCT_ONLY'},
            'execution_authority':False,'final_publication_approval':False,'consumer_adopted':False}
        body['reinstatement_digest']=canonical_digest(body)
        return body
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, AttributeError) as error:
        raise ValueError('REINSTATEMENT_PROOF_INCOMPLETE') from error


def validate_reinstatement(record, reduction):
    try:
        rebuilt=build_reinstatement(record['request'],reduction=reduction,
            cancellation_event_id=record['cancellation_event_id'],
            authority_bytes=record['authority_evidence_utf8'].encode('utf-8'),
            expected_authority_sha256=record['authority_sha256'])
        if record != rebuilt:
            raise ValueError('REINSTATEMENT_RECORD_OR_LINEAGE_CHANGED')
        return rebuilt
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('REINSTATEMENT_RECORD_INCOMPLETE') from error


def _require_schema(db):
    from scripts.migrate_operations_task_schema import _schema, _sql
    actual=_schema(db)
    if any(actual.get(key) != _sql(sql) for key,sql in SCHEMA.items()):
        raise ValueError('REINSTATEMENT_EXPLICIT_SCHEMA_ADOPTION_REQUIRED')


def record_reinstatement(engine, task_id, *, request, expected_version,
        expected_checkpoint_sha256, expected_cancellation_event_id, authority_bytes, expected_authority_sha256):
    from shared_platform.workbench_engine import WorkbenchEngine, _json
    if type(engine) is not WorkbenchEngine:
        raise ValueError('REINSTATEMENT_CONCRETE_ENGINE_REQUIRED')
    with engine.transaction() as db:
        _require_schema(db)
        row=engine._row(db,task_id); engine._require_version(row)
        stored=db.execute('SELECT request_digest,record_json FROM workbench_publication_scope_reductions WHERE task_id=?',
                          (task_id,)).fetchone()
        if stored is None:
            raise ValueError('REINSTATEMENT_CANCELLATION_REQUIRED')
        reduction=json.loads(stored['record_json'])
        if (json.loads(row['version_json']) != expected_version or expected_version != reduction['original_version']
                or hashlib.sha256(row['checkpoint_json'].encode()).hexdigest() != expected_checkpoint_sha256
                or expected_checkpoint_sha256 != reduction['checkpoint_sha256']
                or json.loads(row['scope_json']) != reduction['original_scope']
                or stored['request_digest'] != reduction['request_digest']):
            raise ValueError('REINSTATEMENT_ORIGINAL_TASK_CAS_CHANGED')
        cancellation=db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id DESC LIMIT 1',
                                (task_id,'publication_target_cancelled')).fetchone()
        if (cancellation is None or cancellation['id'] != expected_cancellation_event_id
                or json.loads(cancellation['detail_json']).get('reduction_digest') != reduction['reduction_digest']):
            raise ValueError('REINSTATEMENT_CANCELLATION_EVENT_CHANGED')
        proposed=build_reinstatement(request,reduction=reduction,cancellation_event_id=cancellation['id'],
            authority_bytes=authority_bytes,expected_authority_sha256=expected_authority_sha256)
        existing=db.execute('SELECT request_digest,record_json FROM '+TABLE+' WHERE task_id=?',(task_id,)).fetchone()
        if existing:
            if existing['request_digest'] != proposed['request_digest'] or json.loads(existing['record_json']) != proposed:
                raise ValueError('REINSTATEMENT_ALREADY_HAS_DIFFERENT_SUCCESSOR')
            return proposed  # a receipt replay, never a fresh execution authorization
        latest=db.execute('SELECT id FROM workbench_events WHERE task_id=? ORDER BY id DESC LIMIT 1',(task_id,)).fetchone()
        if (row['template'] != 'publication' or row['step_index'] != 0
                or engine._review_identity(db,task_id)['review_mode'] != 'legacy'
                or row['state'] not in {'queued','failed'} or row['worker'] is not None
                or row['lease_token'] is not None or row['lease_until'] is not None
                or row['external_started'] or row['action_json'] is not None
                or set(json.loads(row['checkpoint_json'])) != {'native_preparation'}
                or latest is None or latest['id'] != expected_cancellation_event_id):
            raise ValueError('REINSTATEMENT_PRE_SUBMIT_ORIGINAL_WRITER_NOT_QUIESCED')
        if (db.execute('SELECT 1 FROM workbench_resource_locks WHERE task_id=?',(task_id,)).fetchone()
                or db.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',(task_id,)).fetchone()
                or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>'completed'",(task_id,)).fetchone()
                or db.execute('SELECT 1 FROM workbench_domain_locks l JOIN workbench_domain_operations o ON o.operation_id=l.operation_id '
                    'WHERE o.owner_task_id=?',(task_id,)).fetchone()):
            raise ValueError('REINSTATEMENT_UNRESOLVED_ORIGINAL_OWNER_OR_LOCK')
        db.execute('INSERT INTO '+TABLE+' VALUES(?,?,?)',(task_id,proposed['request_digest'],_json(proposed)))
        engine._event(db,task_id,'publication_target_reinstated',{'reinstatement_digest':proposed['reinstatement_digest'],
            'cancellation_event_id':expected_cancellation_event_id,'cancellation_digest':reduction['reduction_digest'],
            'execution_authority':False,'consumer_adopted':False})
        return proposed


def adopt_reinstatement_schema(binding, *, expected_logical_sha256, backup_path):
    """Separate reviewed local schema adoption; never invoked by GET or intake."""
    from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
    from scripts.migrate_operations_task_schema import _connect, _fingerprint, _schema, _sql, _integrity
    if type(binding) is not OriginalPreparedTaskContinuation:
        raise ValueError('REINSTATEMENT_ORIGINAL_CONTINUATION_REQUIRED')
    binding.verify_sources()
    path=binding.engine.store.path.resolve(strict=True)
    backup=Path(backup_path)
    if not backup.is_absolute() or backup.exists() or backup.resolve()==path:
        raise ValueError('REINSTATEMENT_NEW_DISTINCT_BACKUP_REQUIRED')
    with closing(_connect(path,writable=False)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN');_integrity(db)
        before=_fingerprint(db);actual=_schema(db)
        if before['logical_sha256'] != expected_logical_sha256:
            raise ValueError('REINSTATEMENT_SCHEMA_CAS_CHANGED')
        if any(key in actual and actual[key] != _sql(sql) for key,sql in SCHEMA.items()):
            raise ValueError('REINSTATEMENT_SCHEMA_CONFLICT')
        missing=[key for key in SCHEMA if key not in actual]
        if not missing:return {'status':'ALREADY_INSTALLED','database_writes':0}
        if len(missing) != len(SCHEMA):raise ValueError('REINSTATEMENT_PARTIAL_SCHEMA_REQUIRES_RECONCILIATION')
        backup.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(backup)) as copy:db.backup(copy)
    with closing(_connect(path,writable=True)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            if _fingerprint(db)['logical_sha256'] != expected_logical_sha256:
                raise ValueError('REINSTATEMENT_SCHEMA_CAS_CHANGED')
            row=db.execute('SELECT state,worker,lease_token,lease_until,external_started FROM workbench_execution WHERE task_id=?',
                           (binding.pins.task_id,)).fetchone()
            if row != ('failed',None,None,None,0):raise ValueError('REINSTATEMENT_SCHEMA_WRITER_NOT_QUIESCED')
            if (db.execute('SELECT 1 FROM workbench_resource_locks WHERE task_id=?',(binding.pins.task_id,)).fetchone()
                    or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>'completed'",(binding.pins.task_id,)).fetchone()
                    or db.execute('SELECT 1 FROM workbench_domain_locks l JOIN workbench_domain_operations o ON o.operation_id=l.operation_id '
                        'WHERE o.owner_task_id=?',(binding.pins.task_id,)).fetchone()):
                raise ValueError('REINSTATEMENT_SCHEMA_UNRESOLVED_ORIGINAL_OPERATION')
            for key in missing:db.execute(SCHEMA[key])
            after=_fingerprint(db)
            if ({k:v for k,v in after['tables'].items() if k in before['tables']} != before['tables']
                    or any(after['objects'].get(k)!=v for k,v in before['objects'].items())):
                raise ValueError('REINSTATEMENT_SCHEMA_EXISTING_HISTORY_CHANGED')
            _integrity(db);db.commit()
            return {'status':'INSTALLED','objects_added':[list(x) for x in missing],
                'backup_path':str(backup),'existing_history_unchanged':True}
        except BaseException:db.rollback();raise
