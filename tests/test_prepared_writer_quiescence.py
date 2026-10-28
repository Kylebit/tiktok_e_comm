from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib, json, sqlite3

import pytest
from test_prepared_target_reduction import prepared
from shared_platform.prepared_writer_quiescence import review_owner_quiescence, reconcile_quiesced_prepared_writer

def encoded(x):return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def tables(e):
    with sqlite3.connect(e.store.path) as db:
        return {name:db.execute('SELECT * FROM '+name+' ORDER BY rowid').fetchall()
                for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}

@pytest.fixture
def owned(prepared):
    e,task,*_=prepared
    e.register_executor('original-owner',['publication'],e.release)
    token=e.claim(task,'original-owner')['lease_token']
    with sqlite3.connect(e.store.path) as db:
        cp=json.loads(db.execute('SELECT checkpoint_json FROM workbench_execution WHERE task_id=?',(task,)).fetchone()[0])
    e.record_checkpoint(task,token,cp)
    e.clock=lambda: 5000
    now=datetime.now(timezone.utc)
    at=(now-timedelta(minutes=1)).isoformat()
    inventory={'owner_thread':'owner-thread','task_id':task,'worker':'original-owner',
        'R1_invocation_count':1,'R1_invocations':[{'invocation_id':'claim','action':'claim-original',
          'started_at_utc':(now-timedelta(minutes=3)).isoformat(),'returned_at_utc':(now-timedelta(minutes=2)).isoformat(),
          'execution_mode':'SYNCHRONOUS_RUNPY_IN_CALLING_PROCESS','returned_status':'ACTUAL_ENTRY_SUCCEEDED',
          'this_call_return_proven':True}], 'R2_and_COMMON_preview_child_returns':[],
        'unpaired_R1_claims':[],'unreturned_owned_business_calls':[],'new_provider_async_generation_job_ids':[]}
    with sqlite3.connect(e.store.path) as db:
        db.row_factory=sqlite3.Row
        claim=dict(db.execute("SELECT id,event_type,created_at FROM workbench_events WHERE task_id=? AND event_type='claimed' ORDER BY id DESC LIMIT 1",(task,)).fetchone())
        last=dict(db.execute('SELECT id,event_type,created_at FROM workbench_events WHERE task_id=? ORDER BY id DESC LIMIT 1',(task,)).fetchone())
        row=db.execute('SELECT checkpoint_json,scope_json FROM workbench_execution WHERE task_id=?',(task,)).fetchone()
    receipt={'schema':'owning-conversation-product-executor-quiescence/v1',
        'status':'OWNER_BUSINESS_EXECUTION_QUIESCED_ALL_RECORDED_OWNED_CALLS_RETURNED',
        'coordinator_thread_id':'coordinator','owner_thread_id':'owner-thread','current_CODEX_THREAD_ID':'owner-thread',
        'current_CODEX_SESSION_ID':'owner-thread','task_id':task,'original_worker_label':'original-owner',
        'attested_at_utc':at,'latest_claim_invocation_id':'claim',
        'latest_claim_invocation_returned_at_utc':inventory['R1_invocations'][0]['returned_at_utc'],
        'latest_owned_lease_claim_event':claim,'latest_owned_durable_checkpoint_event':last,
        'actual_invocation_return_inventory':{'sha256':hashlib.sha256(encoded(inventory)).hexdigest()},
        'owner_attestation_no_pending_business_work':True,'owner_old_worker_business_write_activity_stopped':True,
        'no_further_use_of_old_worker_lease_until_coordinator_A2_A6_acceptance':True,
        'no_invented_exit_code_PID_or_tool_session':True,'independent_OS_process_exit_receipt_obtained':False,
        'business_invocations_pending_in_owner':[],'owned_subagents_pending':[],
        'owned_tool_exec_sessions_pending_in_owner':[],'new_paid_generation_or_translation_provider_job_ids':[],
        'A2_exact_CAS_inputs':{'task_id':task,'worker':'original-owner','state':'running','version':e.release,
            'scope':json.loads(row['scope_json']),'checkpoint_sha256':hashlib.sha256(row['checkpoint_json'].encode()).hexdigest(),
            'latest_claim_event':claim,'latest_event':last,'step_index':0,'external_started':0}}
    return e,task,receipt,inventory

def proof(receipt,inventory):
    raw=encoded(receipt)
    return review_owner_quiescence(raw,encoded(inventory),expected_receipt_sha256=hashlib.sha256(raw).hexdigest(),
                                 coordinator_thread='coordinator',original_owner_thread='owner-thread')

def test_exact_quiesced_owner_can_close_without_global_expiry_and_without_OS_claim(owned):
    e,task,r,inv=owned
    e.clock=lambda:1000
    other=e.create({'template':'publication','source_key':'other','scope':{'offer_id':'another'}})['task_id']
    e.register_executor('other',['publication'],e.release);e.claim(other,'other')
    e.clock=lambda:5000
    before=tables(e)
    p=proof(r,inv)
    preview=reconcile_quiesced_prepared_writer(e,p)
    assert preview['status']=='PREPARED_WRITER_QUIESCENCE_READY' and tables(e)==before
    with sqlite3.connect(e.store.path) as db:schema=db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()
    result=reconcile_quiesced_prepared_writer(e,p,execute=True)
    after=tables(e)
    assert result['changed'] and result['OS_exit_proven'] is result['execution_authority'] is False
    assert result['receipt']['COMMON_authority'] is False
    with sqlite3.connect(e.store.path) as db:
        assert db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()==schema
        row=db.execute('SELECT state,worker,lease_token,lease_until,checkpoint_json,scope_json,version_json FROM workbench_execution WHERE task_id=?',(task,)).fetchone()
    assert row[:4]==('failed',None,None,None)
    for name in ('workbench_execution','workbench_tasks','workbench_resource_locks'):
        assert [x for x in before[name] if (x[0] if name!='workbench_resource_locks' else x[1])==other]==[
            x for x in after[name] if (x[0] if name!='workbench_resource_locks' else x[1])==other]
    replay=reconcile_quiesced_prepared_writer(e,p,execute=True)
    assert replay['changed'] is False and tables(e)==after

@pytest.mark.parametrize('conflict',['worker','version','checkpoint','scope','external','event','attached','domain','lock','review'])
def test_changed_or_unresolved_task_is_never_retired(owned,conflict):
    e,task,r,inv=owned;p=proof(r,inv)
    with e.transaction() as db:
        if conflict in {'worker','version','checkpoint','scope','external'}:
            name,value={'worker':('worker','different'), 'version':('version_json','{}'),
                'checkpoint':('checkpoint_json','{}'),'scope':('scope_json','{}'),'external':('external_started',1)}[conflict]
            db.execute('UPDATE workbench_execution SET '+name+'=? WHERE task_id=?',(value,task))
        if conflict=='event':e._event(db,task,'changed',{})
        if conflict=='attached':db.execute('INSERT INTO workbench_external_tasks VALUES(?,?)',(task,'{}'))
        if conflict=='domain':db.execute('INSERT INTO workbench_domain_operations VALUES(?,?,?,?,?,?)',('operation','[]',task,'unknown','','now'))
        if conflict=='lock':db.execute('INSERT INTO workbench_resource_locks VALUES(?,?)',('unexpected',task))
        if conflict=='review':db.execute('INSERT INTO workbench_review_identity VALUES(?,?,?)',(task,'single-final-review/v1',1))
    before=tables(e)
    with pytest.raises(ValueError):reconcile_quiesced_prepared_writer(e,p,execute=True)
    assert tables(e)==before

@pytest.mark.parametrize('conflict',['origin','pending','unreturned','claim','OS','future','pin'])
def test_owner_receipt_cannot_broaden_proof_or_hide_pending_calls(owned,conflict):
    e,_,r,inv=owned;r=deepcopy(r)
    if conflict=='origin':r['owner_thread_id']='other'
    if conflict=='pending':r['owned_subagents_pending']=['pending']
    if conflict=='unreturned':inv['R1_invocations'][0]['this_call_return_proven']=False
    if conflict=='claim':r['latest_claim_invocation_id']='missing'
    if conflict=='OS':r['independent_OS_process_exit_receipt_obtained']=True
    if conflict=='future':r['attested_at_utc']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    if conflict=='pin':r['A2_exact_CAS_inputs']['worker']='other'
    r['actual_invocation_return_inventory']['sha256']=hashlib.sha256(encoded(inv)).hexdigest()
    with pytest.raises(ValueError):proof(r,inv)
