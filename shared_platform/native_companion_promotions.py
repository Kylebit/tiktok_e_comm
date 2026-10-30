"""Original native decision -> original OneClick promotion-only consumer.

No caller dictionary opens this scope. The transient context exists only inside
execute_decision's concrete ACTIVE, current graph/actor checks and server lock.
Native binding uses existing append-only OneClick events, not publication rows.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import sqlite3

from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.postpublish_promotions import all_promotion_action_targets,promotion_prerequisite_target

_SCOPE=ContextVar('native_companion_promotion_scope',default=None)
BOUND='NATIVE_PROMOTION_BOUND'
STARTED='NATIVE_PROMOTION_BATCH_STARTED'

def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def selected_targets(values):
    if (type(values) not in {tuple,list} or not values
            or any(type(v) is not str or v not in all_promotion_action_targets() for v in values)
            or len(values)!=len(set(values))):
        raise ApprovalBlocked('NATIVE_PROMOTION_EXACT_SUBSET_REQUIRED')
    return tuple(sorted(values))

def execution_targets(targets,payload):
    scope=_held()
    if scope is None:return None
    from shared_platform.postpublish_promotions import enabled_promotion_action_targets
    # MVP publication suppression must not erase the separately approved
    # companion actions. No publication/GLOBAL row is made runnable here.
    return list(targets)+enabled_promotion_action_targets(payload,targets)

def _active():
    from shared_platform.native_sole_final_execution import _ACTIVE,_NativeExecution
    active=_ACTIVE.get()
    if type(active) is not _NativeExecution:
        raise ApprovalBlocked('NATIVE_PROMOTION_PROTECTED_FACTORY_REQUIRED')
    return active

def _held():
    scope=_SCOPE.get()
    if scope is not None and _active() is not scope['active']:
        raise ApprovalBlocked('NATIVE_PROMOTION_PROTECTED_FACTORY_CHANGED')
    return scope

def canonical_prerequisite(row,label):
    """Only Store's complete verified official result, never PROCESSING/flags."""
    readback=row.get('readback') or {};e=readback.get('evidence') or {}
    canonical=e.get('canonical_platform_result') or {};targets=canonical.get('targets')
    if (row.get('target_label')!=label or row.get('status')!='SUCCEEDED'
            or type(row.get('external_id')) is not str or not row['external_id'].strip()
            or type(readback.get('evidence_digest')) is not str
            or readback['evidence_digest']!=_hash(e)
            or e.get('schema_version')!='native-scoped-v4-target-result/v1'
            or e.get('target_label')!=label or e.get('verified') is not True
            or e.get('status')!='PUBLISHED' or e.get('outcome_unknown') is not False
            or e.get('reconciliation_required') is not False
            or canonical.get('readback_completed') is not True
            or type(targets) is not list or len(targets)!=1
            or targets[0].get('target_label')!=label or targets[0].get('status')!='PUBLISHED'
            or (targets[0].get('evidence') or {}).get('outcome_unknown') is not False
            or (label.startswith('shopee:') and (
                canonical.get('platform')!='SHOPEE'
                or (targets[0].get('evidence') or {}).get('target_label')!=label
                or (targets[0].get('evidence') or {}).get('stage')!='READBACK'
                or (targets[0].get('evidence') or {}).get('provider_identity_bound') is not True
                or (targets[0].get('evidence') or {}).get('provider_code')!='shopee_official_readback_verified'))):
        raise ApprovalBlocked('NATIVE_PROMOTION_VERIFIED_CANONICAL_READBACK_REQUIRED')
    return {'schema_version':'postpublish-prerequisite-readback/v1','target_label':label,
        'external_id':row['external_id'],'readback_evidence_digest':readback['evidence_digest'],
        'result_digest':_hash(canonical)}

def _require_schema(path):
    # No implicit DDL in a getter, constructor, native factory or bridge. The
    # existing OneClick schema must have been explicitly installed beforehand.
    if not Path(path).is_file():raise ApprovalBlocked('NATIVE_PROMOTION_SCHEMA_NOT_INSTALLED')
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={'oneclick_release_jobs','oneclick_release_targets','oneclick_release_events',
                  'oneclick_release_outcomes','oneclick_release_write_occurrences'}
        if not required<=names:raise ApprovalBlocked('NATIVE_PROMOTION_SCHEMA_NOT_INSTALLED')
        from shared_platform.oneclick_release_controlplane import _SCHEMA
        with sqlite3.connect(':memory:') as expected:
            expected.executescript(_SCHEMA)
            for table in required:
                columns={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
                wanted={r[1] for r in expected.execute('PRAGMA table_info('+table+')')}
                if not wanted<=columns:raise ApprovalBlocked('NATIVE_PROMOTION_SCHEMA_MIGRATION_REQUIRED')

@contextmanager
def _execution_scope(active,plan,run,labels):
    if _active() is not active:raise ApprovalBlocked('NATIVE_PROMOTION_PROTECTED_FACTORY_CHANGED')
    from shared_platform.native_approved_discount_prices import B_OFFER
    if plan.get('product_id')==B_OFFER:
        with _b0431_execution_scope(active,plan,run,labels) as scope:
            yield scope
        return
    from shared_platform.native_prepared_v4_targets import _scope
    _scope(active,plan) # Exact original continuation, actual six R1/R2 files.
    if _SCOPE.get() is not None:raise ApprovalBlocked('NATIVE_PROMOTION_SCOPE_ALREADY_HELD')
    rows={r['target_label']:r for r in run['targets']};proofs={}
    from shared_platform.postpublish_promotions import approved_promotion_action_policy
    for action in labels:
        approved_promotion_action_policy(plan['payload'],action)
        label=promotion_prerequisite_target(action)
        proofs[action]=canonical_prerequisite(rows[label],label)
    scope={'active':active,'plan_id':plan['plan_id'],'run_id':run['run_id'],
           'targets':labels,'proofs':proofs,'job_id':None}
    token=_SCOPE.set(scope)
    try:yield scope
    finally:_SCOPE.reset(token)


@contextmanager
def _b0431_execution_scope(active,plan,run,labels):
    """Only the genuine signed adoption may restore this B's missing quote."""
    from shared_platform.agent_prepared_v4_targets import _scope
    from shared_platform.native_approved_discount_prices import (
        B_PLAN,B_RUN,B_DECISION,B_TASK,B_CANDIDATE,B_PAYLOAD,B_TARGETS,B_EVENT,
        read_b0431_discount_decision,
    )
    if (active.plan_id!=B_PLAN or active.decision_id!=B_DECISION
            or plan.get('plan_id')!=B_PLAN or run.get('run_id')!=B_RUN
            or _hash(plan['payload'])!=B_PAYLOAD
            or not labels or any(v not in {'promotion:'+t for t in B_TARGETS} for v in labels)):
        raise ApprovalBlocked('NATIVE_PROMOTION_B0431_EXACT_APPROVED_SCOPE_REQUIRED')
    if _SCOPE.get() is not None:raise ApprovalBlocked('NATIVE_PROMOTION_SCOPE_ALREADY_HELD')
    bound,task,adoption=_scope(active,plan)  # Current HMAC/history/R1/R2/owner first.
    if task['task_id']!=B_TASK or adoption['owner_sid']!=active.owner_sid:
        raise ApprovalBlocked('NATIVE_PROMOTION_B0431_CURRENT_ADOPTION_CHANGED')
    if active.service._actor()['owner_sid']!=active.owner_sid:
        raise ApprovalBlocked('NATIVE_PROMOTION_B0431_CURRENT_ACTOR_CHANGED')
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                          (B_TASK,B_EVENT)).fetchall()
    if len(events)!=1:raise ApprovalBlocked('NATIVE_PROMOTION_B0431_SIGNED_SOURCE_REQUIRED')
    envelope=json.loads(events[0][0]);raw=envelope.get('record_json')
    if type(raw) is not str or hashlib.sha256(raw.encode()).hexdigest()!=envelope.get('record_digest'):
        raise ApprovalBlocked('NATIVE_PROMOTION_B0431_SOURCE_ENVELOPE_CHANGED')
    # _scope already authenticated this exact event and its new purpose. JSON
    # parsing below never substitutes for that current-source verification.
    record=json.loads(raw);ref=record.get('b0431_discount_decision_ref')
    doc=read_b0431_discount_decision(ref)
    if _hash(doc)!=record.get('b0431_discount_decision_digest'):
        raise ApprovalBlocked('NATIVE_PROMOTION_B0431_SIGNED_INTENT_CHANGED')
    with active.store._connect_readonly() as db:
        db.execute('BEGIN')
        active.service._decision(db,B_DECISION,owner_sid=active.owner_sid)
        original=db.execute('SELECT review_json FROM native_sole_final_decisions WHERE decision_id=?',
                            (B_DECISION,)).fetchone()
    if original is None or json.loads(original[0]).get('candidate_digest')!=B_CANDIDATE:
        raise ApprovalBlocked('NATIVE_PROMOTION_B0431_ORIGINAL_CANDIDATE_CHANGED')
    rows={r['target_label']:r for r in run['targets']}
    proofs={action:canonical_prerequisite(rows[promotion_prerequisite_target(action)],
                                        promotion_prerequisite_target(action)) for action in labels}
    scope={'active':active,'plan_id':B_PLAN,'run_id':B_RUN,'targets':labels,
           'proofs':proofs,'job_id':None,'b0431_discount_decision_ref':ref,
           'b0431_discount_decision_digest':_hash(doc),
           'b0431_discount_source_digest':envelope['record_digest']}
    token=_SCOPE.set(scope)
    try:
        from shared_platform.postpublish_promotions import approved_promotion_action_policy
        for action in labels:approved_promotion_action_policy(plan['payload'],action)
        yield scope
    finally:_SCOPE.reset(token)

def check_ensure(path,plan,run,labels):
    scope=_held()
    if labels is None:
        if scope is not None:raise ApprovalBlocked('NATIVE_PROMOTION_SUBSET_REQUIRED')
        return
    if (scope is None or tuple(labels)!=scope['targets'] or plan['plan_id']!=scope['plan_id']
            or run['run_id']!=scope['run_id'] or Path(path).resolve()!=scope['active'].store.path.resolve()):
        raise ApprovalBlocked('NATIVE_PROMOTION_EXACT_CONTEXT_REQUIRED')

def check_job(db,job_id):
    rows=db.execute('SELECT event_json,event_digest FROM oneclick_release_events '
                   'WHERE job_id=? AND event_type=? ORDER BY event_id',(job_id,BOUND)).fetchall()
    if not rows:return
    scope=_held()
    if scope is None:raise ApprovalBlocked('NATIVE_PROMOTION_OWNED_JOB_SCOPE_REQUIRED')
    known={}
    for row in rows:
        value=json.loads(row['event_json'])
        if (_hash(value)!=row['event_digest'] or value['decision_id']!=scope['active'].decision_id
                or value['plan_id']!=scope['plan_id'] or value['run_id']!=scope['run_id']):
            raise ApprovalBlocked('NATIVE_PROMOTION_BOUND_JOB_CHANGED')
        for label in value['targets']:
            proof=value['proofs'][label]
            if label in known and known[label]!=proof:raise ApprovalBlocked('NATIVE_PROMOTION_BOUND_PROOF_CHANGED')
            known[label]=proof
    for label in scope['targets']:
        if label in known and known[label]!=scope['proofs'][label]:
            raise ApprovalBlocked('NATIVE_PROMOTION_BOUND_PROOF_CHANGED')
    scope['known_labels']=set(known)
    scope['job_id']=job_id

def bind_job(db,job,store,labels,now):
    if labels is None:return
    scope=_held()
    row=db.execute('SELECT 1 FROM oneclick_release_events WHERE job_id=? AND event_type=?',
                   (job['job_id'],BOUND)).fetchone()
    if row is not None:
        check_job(db,job['job_id'])
        known=scope['known_labels'];selected=set(labels)
        if selected&known and not selected<=known:
            raise ApprovalBlocked('NATIVE_PROMOTION_NEW_AND_PREVIOUS_ACTIONS_MUST_BE_SEPARATE')
        for previous in db.execute('SELECT * FROM oneclick_release_targets WHERE job_id=? AND active_for_batch=1',
                                   (job['job_id'],)):
            from shared_platform.promotion_price_variance_acceptance import accepted_existing_price
            if previous['target_label'] not in selected and not accepted_existing_price(db, previous) and (previous['status'] not in {
                    'SUCCEEDED','BLOCKED_CAPABILITY','BLOCKED_AUTH','BLOCKED_INVENTORY','FAILED_PRE_SUBMIT'}
                    or previous['pending_write_intent_json'] is not None):
                raise ApprovalBlocked('NATIVE_PROMOTION_PREVIOUS_BATCH_REQUIRES_RECONCILIATION')
        if not selected&known:
            store._event(db,job['job_id'],None,BOUND,{'schema_version':'native-oneclick-promotion-binding/v1',
                'decision_id':scope['active'].decision_id,'plan_id':scope['plan_id'],'run_id':scope['run_id'],
                'targets':list(labels),'proofs':scope['proofs']},now)
        # The legacy MVP registry may refresh every row's active flag during
        # ensure_job. Restore only this exact subset in the same transaction.
        db.execute('UPDATE oneclick_release_targets SET active_for_batch=0 WHERE job_id=?',(job['job_id'],))
        for label in labels:
            db.execute('UPDATE oneclick_release_targets SET active_for_batch=1 WHERE job_id=? AND target_label=?',(job['job_id'],label))
        return
    if db.execute('SELECT 1 FROM oneclick_release_events WHERE job_id=? AND event_type=?',
                  (job['job_id'],'EXPLICIT_BATCH_STARTED')).fetchone():
        raise ApprovalBlocked('NATIVE_PROMOTION_EXISTING_BATCH_REQUIRES_RECONCILIATION')
    store._event(db,job['job_id'],None,BOUND,{'schema_version':'native-oneclick-promotion-binding/v1',
        'decision_id':scope['active'].decision_id,'plan_id':scope['plan_id'],'run_id':scope['run_id'],
        'targets':list(labels),'proofs':scope['proofs']},now)
    # Subset isolation is committed atomically with the binding marker.
    db.execute('UPDATE oneclick_release_targets SET active_for_batch=0 WHERE job_id=?',(job['job_id'],))
    for label in labels:
        changed=db.execute('UPDATE oneclick_release_targets SET active_for_batch=1 WHERE job_id=? AND target_label=?',
                           (job['job_id'],label)).rowcount
        if changed!=1:raise ApprovalBlocked('NATIVE_PROMOTION_TARGET_UNAVAILABLE')
    scope['job_id']=job['job_id']

def before_start(db,job,labels,store,now):
    scope=_held()
    if scope is None:return False
    check_job(db,job['job_id'])
    if tuple(labels or ())!=scope['targets']:raise ApprovalBlocked('NATIVE_PROMOTION_BATCH_SUBSET_CHANGED')
    prior=db.execute('SELECT event_json,event_digest FROM oneclick_release_events WHERE job_id=? AND event_type=?',
                     (job['job_id'],STARTED)).fetchall()
    for row in prior:
        value=json.loads(row['event_json'])
        if _hash(value)!=row['event_digest']:raise ApprovalBlocked('NATIVE_PROMOTION_BATCH_EVENT_CHANGED')
        if tuple(value['targets'])==tuple(labels):return True # No UNKNOWN/intent reset.
        if set(value['targets'])&set(labels):
            raise ApprovalBlocked('NATIVE_PROMOTION_PREVIOUS_ACTION_REQUIRES_RECONCILIATION')
    rows=db.execute('SELECT * FROM oneclick_release_targets WHERE job_id=? AND active_for_batch=1',
                    (job['job_id'],)).fetchall()
    if (set(r['target_label'] for r in rows)!=set(labels)
            or any(r['status']!='PENDING' or r['dispatch_count']!=0
                   or r['pending_write_intent_json'] is not None
                   or r['cumulative_external_write_upper_bound']!=0 for r in rows)):
        raise ApprovalBlocked('NATIVE_PROMOTION_PRIOR_INTENT_REQUIRES_RECONCILIATION')
    marks=','.join('?' for unused in labels)
    if db.execute('SELECT 1 FROM oneclick_release_outcomes WHERE job_id=? AND target_label IN ('+marks+') LIMIT 1',
                  (job['job_id'],*labels)).fetchone():
        raise ApprovalBlocked('NATIVE_PROMOTION_PRIOR_OUTCOME_REQUIRES_RECONCILIATION')
    store._event(db,job['job_id'],None,STARTED,{'targets':list(labels)},now)
    return False

def check_access(path,job_id):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN');check_job(db,job_id)
        scope=_held()
        if scope is not None:
            for action in scope['targets']:transaction_prerequisite(db,job_id,action)

def dependency(label):
    scope=_held()
    if scope is None:return None
    if label not in scope['targets']:return None
    return {'policy_version':'native-canonical-promotion-prerequisite/v1','state':'SATISFIED',
            'satisfied':True,'prerequisite_target':promotion_prerequisite_target(label),
            'prerequisite_status':'SUCCEEDED'}

def prerequisite_context(context,label):
    scope=_held()
    if scope is None:return None
    if context['job']['job_id']!=scope['job_id'] or label not in scope['targets']:
        raise ApprovalBlocked('NATIVE_PROMOTION_JOB_CHANGED')
    fresh=canonical_prerequisite(context['run_targets'][promotion_prerequisite_target(label)],
                                 promotion_prerequisite_target(label))
    if fresh!=scope['proofs'][label]:raise ApprovalBlocked('NATIVE_PROMOTION_CANONICAL_READBACK_CHANGED')
    return fresh

def transaction_prerequisite(db,job_id,label):
    scope=_held()
    if scope is None:return None
    check_job(db,job_id)
    prerequisite=promotion_prerequisite_target(label)
    row=db.execute('SELECT t.*,r.evidence_json,r.evidence_digest FROM release_target_runs t '
        'JOIN release_target_readbacks r ON r.run_id=t.run_id AND r.target_label=t.target_label '
        'WHERE t.run_id=? AND t.target_label=?',(scope['run_id'],prerequisite)).fetchone()
    if row is None:raise ApprovalBlocked('NATIVE_PROMOTION_OFFICIAL_READBACK_MISSING')
    value=dict(row);value['readback']={'evidence':json.loads(row['evidence_json']),
                                   'evidence_digest':row['evidence_digest']}
    proof=canonical_prerequisite(value,prerequisite)
    if proof!=scope['proofs'].get(label):raise ApprovalBlocked('NATIVE_PROMOTION_CANONICAL_READBACK_CHANGED')
    return proof

def execute_active(active,data,target_labels):
    from modules.products import server
    from domains.channel_operations.oneclick_release_adapters import production_adapter_registry
    from shared_platform.oneclick_release_controlplane import OneClickReleaseStore,OneClickReleaseWorker
    labels=selected_targets(target_labels);_require_schema(active.store.path)
    with server._release_execution_lock:
        gate,error=active.gate(data,store=active.store)
        if error is not None:return error
        plan=gate['plan'];run=gate['run']
        if run is None:raise ApprovalBlocked('NATIVE_PROMOTION_ORIGINAL_RUN_REQUIRED')
        with _execution_scope(active,plan,run,labels):
            store=OneClickReleaseStore(active.store.path);registry=production_adapter_registry()
            job=store.ensure_job(plan=plan,run=run,product_revision=plan['payload']['product_revision'],
                                 registry=registry,active_target_labels=labels)
            job=store.start_explicit_batch(job['job_id'],target_labels=labels)
            def current():
                _,failure=active.gate(data,store=active.store)
                if failure is not None:raise ApprovalBlocked('NATIVE_PROMOTION_CURRENT_RECHECK_FAILED')
                return True
            worker=OneClickReleaseWorker(store,lambda:registry,dispatch_enabled=current)
            # Original finite transitions. No provider retry or publication executor.
            for unused in range(2+len(labels)*3):
                if not worker.advance_once(job['job_id']):break
            result=store.get_job(job_id=job['job_id'])
    return 200,{'ok':True,'schema_version':'native-oneclick-promotion-result/v1',
        'native_decision_id':active.decision_id,'promotion_targets':list(labels),'promotion_job':result,
        'approval_saved':True,'new_human_review_required':False,'publication_replayed':False}
