"""Trusted original-owner reconciliation, with no HTTP or runtime activation.

Owner conversation quiescence is a bounded business proof, never OS retirement.
Only the original prepared facts task may close; schema and history stay intact.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class ReviewedOwnerQuiescence:
    receipt_json: str
    receipt_sha256: str
    review_digest: str
    coordinator_thread: str


def review_owner_quiescence(receipt_bytes, inventory_bytes, *, expected_receipt_sha256,
                            coordinator_thread, original_owner_thread):
    """Internal review seam: caller pins the parent-received primary receipt.

    The trusted entry separately verifies public return-receipt files and source
    topology; this seam never accepts HTTP proof, reads profiles or asserts PID
    absence. The original owner must remain paused until coordinated acceptance.
    """
    if hashlib.sha256(receipt_bytes).hexdigest()!=expected_receipt_sha256:
        raise ValueError('OWNER_QUIESCENCE_RECEIPT_PIN_CHANGED')
    r=json.loads(receipt_bytes); inv=json.loads(inventory_bytes)
    if (r.get('schema')!='owning-conversation-product-executor-quiescence/v1'
            or r.get('status')!='OWNER_BUSINESS_EXECUTION_QUIESCED_ALL_RECORDED_OWNED_CALLS_RETURNED'
            or not coordinator_thread or not original_owner_thread
            or r.get('coordinator_thread_id')!=coordinator_thread
            or r.get('owner_thread_id')!=original_owner_thread
            or r.get('current_CODEX_THREAD_ID')!=original_owner_thread
            or r.get('current_CODEX_SESSION_ID')!=original_owner_thread
            or inv.get('owner_thread')!=original_owner_thread
            or inv.get('task_id')!=r.get('task_id') or inv.get('worker')!=r.get('original_worker_label')
            or hashlib.sha256(inventory_bytes).hexdigest()!=r['actual_invocation_return_inventory']['sha256']):
        raise ValueError('OWNER_QUIESCENCE_ORIGIN_OR_INVENTORY_CHANGED')
    required=('owner_attestation_no_pending_business_work','owner_old_worker_business_write_activity_stopped',
              'no_further_use_of_old_worker_lease_until_coordinator_A2_A6_acceptance',
              'no_invented_exit_code_PID_or_tool_session')
    pending=('business_invocations_pending_in_owner','owned_subagents_pending',
             'owned_tool_exec_sessions_pending_in_owner','new_paid_generation_or_translation_provider_job_ids')
    if (any(r.get(k) is not True for k in required) or any(r.get(k)!=[] for k in pending)
            or r.get('independent_OS_process_exit_receipt_obtained') is not False
            or inv.get('unpaired_R1_claims')!=[] or inv.get('unreturned_owned_business_calls')!=[]
            or inv.get('new_provider_async_generation_job_ids')!=[]):
        raise ValueError('OWNER_QUIESCENCE_PENDING_OR_PROOF_SCOPE_CONFLICT')
    at=datetime.fromisoformat(r['attested_at_utc'])
    if at.utcoffset() is None or at>datetime.now(timezone.utc):
        raise ValueError('OWNER_QUIESCENCE_TIMESTAMP_INVALID')
    rows=inv.get('R1_invocations')
    if type(rows) is not list or len(rows)!=inv.get('R1_invocation_count') or not rows:
        raise ValueError('OWNER_QUIESCENCE_RETURN_INVENTORY_INCOMPLETE')
    ids=[]
    for item in rows:
        start=datetime.fromisoformat(item['started_at_utc']); end=datetime.fromisoformat(item['returned_at_utc'])
        if (item.get('this_call_return_proven') is not True or end<start or end>at
                or start.utcoffset() is None or end.utcoffset() is None
                or item.get('execution_mode')!='SYNCHRONOUS_RUNPY_IN_CALLING_PROCESS'
                or item.get('returned_status') not in {'ACTUAL_ENTRY_SUCCEEDED','ACTUAL_ENTRY_STOPPED'}):
            raise ValueError('OWNER_QUIESCENCE_CALL_NOT_RETURNED')
        ids.append(item['invocation_id'])
    latest=[x for x in rows if x['invocation_id']==r.get('latest_claim_invocation_id')]
    if (len(ids)!=len(set(ids)) or len(latest)!=1 or latest[0]['action']!='claim-original'
            or latest[0]['returned_status']!='ACTUAL_ENTRY_SUCCEEDED'
            or latest[0]['returned_at_utc']!=r.get('latest_claim_invocation_returned_at_utc')
            or any(x.get('outstanding') is not False or type(x.get('child_returncode')) is not int
                   for x in inv.get('R2_and_COMMON_preview_child_returns',[]))):
        raise ValueError('OWNER_QUIESCENCE_CLAIM_OR_CHILD_RETURN_CHANGED')
    pin=r['A2_exact_CAS_inputs']
    if (pin['task_id']!=r['task_id'] or pin['worker']!=r['original_worker_label']
            or pin['latest_claim_event']!=r['latest_owned_lease_claim_event']
            or pin['latest_event']!=r['latest_owned_durable_checkpoint_event']
            or pin['latest_claim_event']['event_type']!='claimed'
            or pin['latest_event']['event_type']!='checkpoint_saved'
            or pin['latest_event']['id']<=pin['latest_claim_event']['id']
            or pin.get('step_index')!=0 or pin.get('external_started')!=0
            or pin['scope'].get('skus')!=[]):
        raise ValueError('OWNER_QUIESCENCE_TASK_PIN_CHANGED')
    raw=json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return ReviewedOwnerQuiescence(raw,expected_receipt_sha256,digest(r),coordinator_thread)


def reconcile_quiesced_prepared_writer(engine, proof, *, execute=False):
    """Exact task lifecycle protocol; never expire leases or initialize schema.

    The source performing this maintenance is distinct from the preserved task
    release. An engine bound to the original task release supplies its canonical
    state/event operations. This method registers no executor and grants no
    continuation, COMMON, market, account, or OS authority.
    """
    if type(proof) is not ReviewedOwnerQuiescence or type(execute) is not bool:
        raise ValueError('OWNER_QUIESCENCE_REVIEW_REQUIRED')
    receipt=json.loads(proof.receipt_json)
    if digest(receipt)!=proof.review_digest or receipt['coordinator_thread_id']!=proof.coordinator_thread:
        raise ValueError('OWNER_QUIESCENCE_REVIEW_CHANGED')
    pin=receipt['A2_exact_CAS_inputs']; task_id=pin['task_id']
    if engine.release!=pin['version']:
        raise ValueError('OWNER_QUIESCENCE_ORIGINAL_VERSION_REQUIRED')
    path=Path(engine.store.path).resolve(strict=True)
    mode='rw' if execute else 'ro'
    with closing(sqlite3.connect(path.as_uri()+'?mode='+mode,uri=True,timeout=10)) as db, db:
        db.row_factory=sqlite3.Row
        if not execute: db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE' if execute else 'BEGIN')
        schema=[tuple(x) for x in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]
        existing=db.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='prepared_writer_quiesced' ORDER BY id DESC LIMIT 1",(task_id,)).fetchone()
        row=db.execute('SELECT task_id,template,state,worker,lease_until,scope_json,version_json,checkpoint_json,'
                       'step_index,external_started,action_json FROM workbench_execution WHERE task_id=?',(task_id,)).fetchone()
        if row is None:raise ValueError('OWNER_QUIESCENCE_TASK_MISSING')
        checkpoint_sha=hashlib.sha256(row['checkpoint_json'].encode()).hexdigest()
        if (json.loads(row['scope_json'])!=pin['scope'] or json.loads(row['version_json'])!=pin['version']
                or checkpoint_sha!=pin['checkpoint_sha256'] or row['template']!='publication'
                or row['step_index']!=0 or row['external_started'] or row['action_json'] is not None
                or set(json.loads(row['checkpoint_json']))!={'native_preparation'}):
            raise ValueError('OWNER_QUIESCENCE_PREPARED_TASK_CAS_CONFLICT')
        if existing:
            detail=json.loads(existing[0])
            if (detail.get('review_digest')!=proof.review_digest or row['state']!='failed' or row['worker'] is not None
                    or row['lease_until'] is not None
                    or db.execute('SELECT 1 FROM workbench_resource_locks WHERE task_id=?',(task_id,)).fetchone()):
                raise ValueError('OWNER_QUIESCENCE_REPLAY_CONFLICT')
            return dict(status='PREPARED_WRITER_BUSINESS_QUIESCENCE_RECORDED',task_id=task_id,
                        changed=False,OS_exit_proven=False,execution_authority=False,receipt=detail)
        latest=dict(db.execute('SELECT id,event_type,created_at FROM workbench_events WHERE task_id=? ORDER BY id DESC LIMIT 1',(task_id,)).fetchone())
        claim=db.execute("SELECT id,event_type,created_at,detail_json FROM workbench_events WHERE task_id=? AND event_type='claimed' ORDER BY id DESC LIMIT 1",(task_id,)).fetchone()
        if (row['state']!='running' or row['worker']!=pin['worker'] or pin['state']!='running'
                or latest!=pin['latest_event'] or claim is None
                or {k:claim[k] for k in ('id','event_type','created_at')}!=pin['latest_claim_event']
                or json.loads(claim['detail_json']).get('worker')!=pin['worker']):
            raise ValueError('OWNER_QUIESCENCE_LATEST_OWNER_CAS_CONFLICT')
        locks=[x[0] for x in db.execute('SELECT resource FROM workbench_resource_locks WHERE task_id=? ORDER BY resource',(task_id,))]
        if (locks!=['product-offer:'+pin['scope']['offer_id']]
                or db.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',(task_id,)).fetchone()
                or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state<>'completed'",(task_id,)).fetchone()
                or db.execute('SELECT 1 FROM workbench_domain_locks WHERE operation_id IN (SELECT operation_id FROM workbench_domain_operations WHERE owner_task_id=?)',(task_id,)).fetchone()
                or db.execute('SELECT 1 FROM workbench_receipts WHERE task_id=?',(task_id,)).fetchone()
                or db.execute('SELECT 1 FROM workbench_review_identity WHERE task_id=?',(task_id,)).fetchone()):
            raise ValueError('OWNER_QUIESCENCE_EXTERNAL_OR_LOCK_CONFLICT')
        detail=dict(schema='prepared-writer-business-quiescence/v1',review_digest=proof.review_digest,
                    owner_receipt_sha256=proof.receipt_sha256,owner_thread=receipt['owner_thread_id'],
                    coordinator_thread=proof.coordinator_thread,original_worker=pin['worker'],
                    original_version=pin['version'],checkpoint_sha256=checkpoint_sha,
                    last_claim_event=pin['latest_claim_event'],last_checkpoint_event=pin['latest_event'],
                    original_scope=pin['scope'],business_invocations_returned=True,
                    OS_exit_proven=False,execution_authority=False,COMMON_authority=False,
                    cancellation_recorded=False,next_state='failed',
                    reason='Original owning conversation quiesced; exact receiver and target reduction adoption pending')
        if execute:
            engine._state(db,task_id,'failed',detail['reason'])
            db.execute('UPDATE workbench_execution SET worker=NULL,lease_token=NULL,lease_until=NULL WHERE task_id=?',(task_id,))
            db.execute('DELETE FROM workbench_resource_locks WHERE task_id=?',(task_id,))
            engine._event(db,task_id,'prepared_writer_quiesced',detail)
            assert schema==[tuple(x) for x in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]
            db.commit()
        return dict(status='PREPARED_WRITER_BUSINESS_QUIESCENCE_RECORDED' if execute else 'PREPARED_WRITER_QUIESCENCE_READY',
                    task_id=task_id,changed=execute,OS_exit_proven=False,execution_authority=False,receipt=detail)
