"""Only LH_MY failed preparation 3 -> 4; consume its existing allowance.

No GET/init migration. Explicit maintenance installs/intakes append-only rows.
The old Root, core grant, start3, cap3 and all mutation history stay immutable.
"""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from shared_platform import native_accepted_save_core_repair as core

LABEL='tiktok:LH_MY'
BASE_HEAD='4947b8acee9da92c0da359f134697c78d83dbd60'
TABLE='native_tiktok_preparation_successors'
SCHEMA='''
CREATE TABLE native_tiktok_preparation_successors (successor_id TEXT PRIMARY KEY,parent_grant_id TEXT NOT NULL UNIQUE,body_json TEXT NOT NULL);
CREATE TABLE native_tiktok_preparation_successor_starts (successor_id TEXT PRIMARY KEY,target_attempt INTEGER NOT NULL CHECK(target_attempt=4),FOREIGN KEY(successor_id) REFERENCES native_tiktok_preparation_successors(successor_id));
'''
for table in (TABLE,'native_tiktok_preparation_successor_starts'):
    for event in ('UPDATE','DELETE'):
        SCHEMA+='CREATE TRIGGER '+table+'_no_'+event.lower()+' BEFORE '+event+' ON '+table+" BEGIN SELECT RAISE(ABORT,'immutable LH_MY preparation successor'); END;\n"

def need(value,code): core.need(value,'LH_MY_SUCCESSOR_'+code)
def statements():
    pending='';out=[]
    for line in SCHEMA.splitlines(True):
        pending+=line
        if sqlite3.complete_statement(pending):out.append(pending.strip());pending=''
    need(not pending.strip(),'SCHEMA_INCOMPLETE');return out

def check_schema(db,optional=False):
    rows={r['name']:r['sql'] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'native_tiktok_preparation_successor%'")}
    if not rows and optional:return False
    expected={re.search(r'CREATE (?:TABLE|TRIGGER) (\w+)',s)[1]:s for s in statements()}
    norm=lambda s:re.sub(r'\s+',' ',s.strip().rstrip(';')).lower()
    need(set(rows)==set(expected) and all(norm(rows[k])==norm(v) for k,v in expected.items()),'EXPLICIT_SCHEMA_REQUIRED');return True

def _facts(body):
    need(type(body) is dict and set(body)=={'schema','plan_id','run_id','decision_id','snapshot_digest','target_label','parent_grant_id','facts_ref','root_addendum_ref'}
         and body['schema']=='lh-my-unspent-core-successor/v1'
         and (body['plan_id'],body['run_id'],body['decision_id'],body['snapshot_digest'],body['target_label'])==(core.PLAN,core.RUN,core.DECISION,core.SNAPSHOT,LABEL),'EXACT_BODY_REQUIRED')
    facts=json.loads(core._read_reference(body['facts_ref']))
    need(facts.get('schema')=='lh-my-failed-preparation-unspent-core-facts/v1','EXACT_FACTS_REQUIRED')
    parent=facts['parent_grant'];parent_body=json.loads(parent['body_json'])
    need(core._sha(parent['body_json'])==parent['grant_id']==body['parent_grant_id']
         and (parent['scope_key'],parent['target_label'])==(parent_body['scope_key'],LABEL)
         and (parent_body['plan_id'],parent_body['run_id'],parent_body['decision_id'],parent_body['snapshot_digest'])==(core.PLAN,core.RUN,core.DECISION,core.SNAPSHOT)
         and parent_body['role']==core.SIX and parent_body['schema']=='ten-accepted-save-core-grant/v2'
         and (parent_body['target_label'],parent_body['detail_id'],parent_body['shop_id'],parent_body['sequence'])==(LABEL,'3464610329','13295169',None),'PARENT_GRANT_CHANGED')
    old_root=json.loads(core._read_reference(parent_body['root_addendum_ref']))
    need(old_root.get('schema')=='root-ten-accepted-save-core-repair/v2' and old_root.get('status')=='LIVE_ADOPTION_APPROVED'
         and (old_root.get('plan_id'),old_root.get('run_id'),old_root.get('decision_id'),old_root.get('snapshot_digest'))==(core.PLAN,core.RUN,core.DECISION,core.SNAPSHOT)
         and old_root.get('target_labels')==core.TARGET_ORDER and old_root.get('role_limits')==core.ROLE_LIMITS
         and old_root.get('original_identities_ref')==core.ORIGINAL_REF and old_root.get('six_facts_ref')==core.SIX_FACTS_REF
         and old_root.get('six_checkpoint_ref')==core.SIX_CP_REF and old_root.get('extra_save_maximum')==1
         and old_root.get('new_human_decision') is False
         and core._sha(core._json({k:v for k,v in parent_body.items() if k!='root_addendum_ref'})) in old_root.get('grant_core_digests',[]),'ORIGINAL_ROOT_CHANGED')
    core._read_reference(old_root['source_pins_ref'])  # Historical bytes; never rewrite/relabel as current source.
    return facts,parent_body,old_root

def _root_receipt(body):
    facts,parent,old_root=_facts(body);root=json.loads(core._read_reference(body['root_addendum_ref']))
    need(root.get('schema')=='root-lh-my-unspent-core-successor/v1' and root.get('status')=='LIVE_ADOPTION_APPROVED'
         and (root.get('plan_id'),root.get('run_id'),root.get('decision_id'),root.get('snapshot_digest'))==(core.PLAN,core.RUN,core.DECISION,core.SNAPSHOT)
         and root.get('target_labels')==[LABEL] and root.get('before_attempt')==3 and root.get('after_attempt')==4
         and root.get('base_source_head')==BASE_HEAD and root.get('original_root_ref')==parent['root_addendum_ref']
         and root.get('original_source_pins_ref')==old_root['source_pins_ref']
         and root.get('parent_grant_id')==body['parent_grant_id'] and root.get('facts_ref')==body['facts_ref']
         and root.get('new_extra_save_allowance')==0 and type(root.get('new_extra_save_allowance')) is int
         and root.get('existing_core_save_maximum')==1 and root.get('base_target_maximum')==3
         and root.get('combined_target_maximum')==5 and root.get('new_human_decision') is False
         and root.get('successor_core_digest')==core._sha(core._json({k:v for k,v in body.items() if k!='root_addendum_ref'})),'LIVE_EXACT_ROOT_REQUIRED')
    pins=json.loads(core._read_reference(root['source_pins_ref']));app=Path(core.__file__).resolve().parents[1]
    required={'shared_platform/native_tiktok_core_save_before_publish.py','shared_platform/native_accepted_save_core_repair.py','shared_platform/product_publication_executors.py',
              'shared_platform/native_final_mutation_budget.py','shared_platform/native_canonical_provider_edges.py','shared_platform/native_prepared_v4_targets.py',
              'shared_platform/native_shopee_known_zero_recovery.py','modules/miaoshou/tiktok_v4_drafts.py','modules/products/server.py'}
    need(required.issubset(pins),'FINAL_SOURCE_PIN_SET_REQUIRED')
    return facts,parent,pins

def validate_root(body):
    facts,parent,pins=_root_receipt(body);app=Path(core.__file__).resolve().parents[1]
    for name,row in pins.items():
        path=app/name
        need(path.resolve().is_relative_to(app) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==row['after_sha256'],'FINAL_SOURCE_CHANGED')
    return facts,parent

def _rows(db,table,field,value):return [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE '+field+'=?',(value,))]

def validate_state(db,body,*,status,attempt,allowed_pending_publish=None,require_unspent=False):
    facts,parent,_=_facts(body);gid=body['parent_grant_id'];core.check_schema(db)
    need(_rows(db,'native_accepted_core_grants','grant_id',gid)==[facts['parent_grant']],'PARENT_ROW_CHANGED')
    need(_rows(db,'native_accepted_core_starts','grant_id',gid)==[facts['parent_start']]
         and facts['parent_start']=={'grant_id':gid,'target_attempt':3},'PARENT_START_CHANGED')
    need(set(facts['parent_mutations'])=={'reservations','dispatches','outcomes','verifications'} and all(v==[] for v in facts['parent_mutations'].values()),'ROOT_MUST_PROVE_UNSPENT')
    mutable={t:_rows(db,'native_accepted_core_'+t,'grant_id',gid) for t in facts['parent_mutations']}
    if require_unspent:need(all(v==[] for v in mutable.values()),'CORE_SPENT_OR_UNKNOWN')
    need(all(len(v)<=1 for v in mutable.values()) and (not mutable['dispatches'] or mutable['reservations'])
         and (not mutable['outcomes'] or mutable['dispatches']) and all(r['outcome'] in {'ACCEPTED','REJECTED'} for r in mutable['outcomes']),'CORE_MUTATION_SHAPE_CHANGED')
    failures=_rows(db,'release_target_failure_events','run_id',core.RUN)
    failures=[r for r in failures if r['target_label']==LABEL];failures.sort(key=lambda r:r['attempt'])
    need(failures==facts['failures'] and [r['attempt'] for r in failures]==[1,2,3]
         and all(core._sha(r['evidence_json'])==r['evidence_digest'] for r in failures)
         and [r['evidence_digest'] for r in failures[:2]]==parent['failure_evidence_digests'],'THREE_FAILURES_CHANGED')
    proof=json.loads(failures[-1]['evidence_json']);canonical=proof['canonical_platform_result'];nested=canonical['targets']
    need(proof.get('target_label')==LABEL and proof.get('status')=='FAILED' and proof.get('request_attempted') is False
         and proof.get('outcome_unknown') is False and canonical.get('dispatch_attempted') is False
         and len(nested)==1 and nested[0]['target_label']==LABEL and nested[0]['status']=='FAILED'
         and nested[0]['evidence']['provider_code']=='tiktok_preparation_failed' and nested[0]['evidence']['stage']=='PREPARATION'
         and nested[0]['evidence']['outcome_unknown'] is False,'EXACT_FAILED_PREPARATION_REQUIRED')
    target=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(core.RUN,LABEL)).fetchone()
    original=facts['target'];need((original['status'],original['attempts'],original['external_id'])==('FAILED',3,None),'BEFORE_ATTEMPT_CHANGED')
    need(target and (target['status'],target['attempts'],target['external_id'])==(status,attempt,None)
         and target['idempotency_key']==original['idempotency_key'],'CURRENT_ATTEMPT_CHANGED')
    if status=='FAILED':need(dict(target)==original,'FAILED_ROW_CHANGED')
    binding=db.execute('SELECT * FROM native_final_budget_scopes WHERE scope_key=?',(parent['scope_key'],)).fetchone()
    need(binding and core._sha(binding['binding_json'])==binding['binding_sha256'],'ORIGINAL_BINDING_CHANGED')
    value=json.loads(binding['binding_json']);need((value['plan_id'],value['snapshot_digest'],value['decision_id'],value['platform'],value['per_target_maximum'])==(core.PLAN,core.SNAPSHOT,core.DECISION,'TIKTOK',3),'CAP3_CHANGED')
    expected=core.six_facts(LABEL);need(facts['base_rows']==expected['original_budget_records'],'OLD_BASE_FACTS_CHANGED')
    rows=core._base_rows(db,parent['scope_key'],LABEL)
    need(rows[:3]==facts['base_rows'] and len(rows)==(4 if allowed_pending_publish is not None else 3),'BASE_PUBLISH_SPENT_OR_UNKNOWN')
    if allowed_pending_publish is not None:
        row=rows[-1];need(status=='RUNNING' and attempt==4 and row=={'sequence':allowed_pending_publish,'target_label':LABEL,'budget_scope':'target','operation':'publish_target','attempt_count':1,'request_digest':None,'outcome':'UNKNOWN'},'EXACT_NEW_PENDING_PUBLISH_REQUIRED')
    for table in ('starts','reservations','dispatches','outcomes','verifications'):
        need(_rows(db,'native_save_repair_'+table,'grant_id',parent['original_six_grant_id'])==expected['original_six_grant_tables'][table],'OLD_SIX_'+table.upper()+'_CHANGED')
    need(_rows(db,'native_save_repair_grants','grant_id',parent['original_six_grant_id'])==[expected['original_six_grant']],'OLD_SIX_GRANT_CHANGED')
    for table in ('release_target_submissions','release_target_readbacks','release_target_repairs'):
        need(db.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND target_label=?',(core.RUN,LABEL)).fetchone() is None,'EXTERNAL_OUTCOME_NO_REPLAY')
    return parent

def _checkpoint(active,parent):core.current_checkpoint(active,parent)

def intake(active,db,body):
    core.protected(active);check_schema(db);facts,parent=validate_root(body)
    active.service._decision(db,core.DECISION,owner_sid=active.owner_sid);active.store._require_active_run(db,core.RUN)
    validate_state(db,body,status='FAILED',attempt=3,require_unspent=True);_checkpoint(active,parent)
    raw=core._json(body);sid=core._sha(raw)
    db.execute('INSERT INTO '+TABLE+' VALUES(?,?,?)',(sid,body['parent_grant_id'],raw));return sid

def maintenance_install_and_intake(active,body):
    """Explicit maintenance only; prove scope before any DDL/INSERT."""
    core.protected(active);_,parent=validate_root(body);_checkpoint(active,parent)
    with active.store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active.service._decision(db,core.DECISION,owner_sid=active.owner_sid);active.store._require_active_run(db,core.RUN)
        validate_state(db,body,status='FAILED',attempt=3,require_unspent=True)
        if not check_schema(db,optional=True):
            for statement in statements():db.execute(statement)
        sid=intake(active,db,body);db.commit();return sid

def _record(db):
    if not check_schema(db,optional=True):return None
    rows=db.execute('SELECT * FROM '+TABLE).fetchall();need(len(rows)<=1,'ONE_EXACT_SUCCESSOR_ONLY')
    if not rows:return None
    row=dict(rows[0]);body=json.loads(row['body_json']);need(core._sha(row['body_json'])==row['successor_id'] and row['parent_grant_id']==body['parent_grant_id'],'SUCCESSOR_CHANGED')
    return row,body

MAINTENANCE_REQUEST=Path('E:/OrbitEvidence/4039931504-publication-20261019/ROOT_LH_MY_MAINTENANCE_REQUEST.json')

CONSUMED_COMPATIBILITY=Path('E:/OrbitEvidence/4039931504-publication-20261019/ROOT_LH_MY_CONSUMED_COMPATIBILITY_NORMAL22.json')

def _consumed_terminal(active,db,record,body):
    """Only skip an already consumed LH operation; never authorize LH I/O."""
    if not CONSUMED_COMPATIBILITY.exists():return False
    core.protected(active);row,stored=record
    facts,parent,historical_pins=_root_receipt(body)
    need(stored==body and row['body_json']==core._json(body),'CONSUMED_BODY_CHANGED')
    receipt=json.loads(CONSUMED_COMPATIBILITY.read_bytes())
    expected={'schema','status','plan_id','run_id','decision_id','snapshot_digest','target_label',
              'consumed_successor_id','historical_body_digest','historical_root_ref','historical_source_pins_ref',
              'terminal_facts_ref','current_source_pins_ref','allowed_continuation_targets','new_LH_action'}
    old_root=json.loads(core._read_reference(body['root_addendum_ref']))
    need(type(receipt) is dict and set(receipt)==expected
         and receipt['schema']=='root-lh-my-consumed-source-compatibility/v1'
         and receipt['status']=='LIVE_ADOPTION_APPROVED'
         and (receipt['plan_id'],receipt['run_id'],receipt['decision_id'],receipt['snapshot_digest'],receipt['target_label'])
             ==(core.PLAN,core.RUN,core.DECISION,core.SNAPSHOT,LABEL)
         and receipt['consumed_successor_id']==row['successor_id']
         and receipt['historical_body_digest']==core._sha(core._json(body))
         and receipt['historical_root_ref']==body['root_addendum_ref']
         and receipt['historical_source_pins_ref']==old_root['source_pins_ref']
         and receipt['allowed_continuation_targets']==['shopee:MY','shopee:PH','shopee:TH','shopee:VN']
         and receipt['new_LH_action'] is False,'CONSUMED_EXACT_ROOT_REQUIRED')
    # Both old pin-file hashes remain historical authority. The new composition
    # must independently bind every current source, including this new adapter.
    terminal=json.loads(core._read_reference(receipt['terminal_facts_ref']))
    need(Path(receipt['terminal_facts_ref']['path']).resolve()==Path('E:/OrbitEvidence/4039931504-publication-20261019/current-live-readonly-20261021/LH_MY_ACCEPTED40_CURRENT.private.json').resolve()
         and receipt['terminal_facts_ref']['sha256']=='3db36bad29d517f5077894a1f5baac04d4b64c262d9de6eb453be55f795f89d7'
         and terminal.get('schema')=='LH_MY-original-accepted-evidence-current-readonly/v1','CONSUMED_EXACT_TERMINAL_FACTS_REQUIRED')
    current=json.loads(core._read_reference(receipt['current_source_pins_ref']))
    need(set(current)==set(historical_pins)|{'modules/shopee/global_v4_executor.py'},'CONSUMED_CURRENT_SOURCE_SET_REQUIRED')
    app=Path(core.__file__).resolve().parents[1]
    for name,pin in current.items():
        path=app/name
        need(path.resolve().is_relative_to(app) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==pin['after_sha256'],'CONSUMED_CURRENT_SOURCE_CHANGED')
    need(terminal['successor']=={TABLE:[row],'native_tiktok_preparation_successor_starts':[{'successor_id':row['successor_id'],'target_attempt':4}]},'CONSUMED_FROZEN_START_CHANGED')
    need(_rows(db,TABLE,'successor_id',row['successor_id'])==[row]
         and _rows(db,'native_tiktok_preparation_successor_starts','successor_id',row['successor_id'])==terminal['successor']['native_tiktok_preparation_successor_starts'],'CONSUMED_START_CHANGED')
    target=_rows(db,'release_target_runs','run_id',core.RUN);target=[r for r in target if r['target_label']==LABEL]
    need(target==terminal['target'] and len(target)==1 and target[0]['status']=='FAILED' and target[0]['attempts']==4
         and target[0]['idempotency_key']==facts['target']['idempotency_key'],'CONSUMED_TERMINAL_TARGET_CHANGED')
    failure=_rows(db,'release_target_failure_events','run_id',core.RUN);failure=sorted([r for r in failure if r['target_label']==LABEL],key=lambda r:r['attempt'])
    need(failure==terminal['evidence']['release_target_failure_events'] and [r['attempt'] for r in failure]==[1,2,3,4]
         and all(core._sha(r['evidence_json'])==r['evidence_digest'] for r in failure),'CONSUMED_FAILURE_HISTORY_CHANGED')
    need(_rows(db,'native_accepted_core_grants','grant_id',body['parent_grant_id'])==[facts['parent_grant']]
         and _rows(db,'native_accepted_core_starts','grant_id',body['parent_grant_id'])==[facts['parent_start']], 'CONSUMED_PARENT_CHANGED')
    accepted=[dict(r) for r in db.execute('SELECT s.platform,r.sequence,r.target_label,r.budget_scope,r.operation,d.request_digest,o.outcome FROM native_final_budget_scopes s JOIN native_final_budget_reservations r USING(scope_key) JOIN native_final_budget_dispatches d USING(scope_key,sequence) JOIN native_final_budget_outcomes o USING(scope_key,sequence) WHERE s.scope_key=? AND s.plan_id=? AND r.sequence=40',(parent['scope_key'],core.PLAN))]
    need(accepted==terminal['accepted_base40'] and len(accepted)==1
         and (accepted[0]['platform'],accepted[0]['target_label'],accepted[0]['budget_scope'],accepted[0]['operation'],accepted[0]['outcome'])==('TIKTOK',LABEL,'target','publish_target','ACCEPTED')
         and re.fullmatch('[0-9a-f]{64}',accepted[0]['request_digest']) is not None,'CONSUMED_ACCEPTED_PUBLISH_REQUIRED')
    save=[dict(r) for r in db.execute('SELECT g.grant_id,g.target_label,s.target_attempt,r.attempt_count,d.request_digest,o.outcome,v.evidence_digest FROM native_accepted_core_grants g JOIN native_accepted_core_starts s USING(grant_id) JOIN native_accepted_core_reservations r USING(grant_id) JOIN native_accepted_core_dispatches d USING(grant_id) JOIN native_accepted_core_outcomes o USING(grant_id) JOIN native_accepted_core_verifications v USING(grant_id) WHERE g.grant_id=?',(body['parent_grant_id'],))]
    need(save==terminal['core'] and len(save)==1 and (save[0]['target_label'],save[0]['target_attempt'],save[0]['attempt_count'],save[0]['outcome'])==(LABEL,3,1,'ACCEPTED')
         and re.fullmatch('[0-9a-f]{64}',save[0]['request_digest']) is not None
         and re.fullmatch('[0-9a-f]{64}',save[0]['evidence_digest']) is not None,'CONSUMED_ACCEPTED_SAVE_VERIFICATION_REQUIRED')
    # Deliberately return only 'consumed'. No state/receipt/published projection.
    return True

def consume_explicit_maintenance_request(active):
    """A Root-selected body, under the existing protected execution and lock."""
    if getattr(active,'plan_id',None)!=core.PLAN or getattr(active,'decision_id',None)!=core.DECISION:return
    if not MAINTENANCE_REQUEST.exists():return
    core.protected(active)
    request=json.loads(MAINTENANCE_REQUEST.read_bytes())
    need(type(request) is dict and set(request)=={'schema','body_ref'}
         and request['schema']=='root-lh-my-explicit-maintenance-request/v1'
         and type(request['body_ref']) is dict and set(request['body_ref'])=={'path','sha256'},'EXPLICIT_REQUEST_REQUIRED')
    body=json.loads(core._read_reference(request['body_ref']))
    raw=core._json(body);sid=core._sha(raw)
    with active.store._connect_readonly() as db:
        record=_record(db)
        if record is not None:
            row,stored=record
            need(row=={'successor_id':sid,'parent_grant_id':body['parent_grant_id'],'body_json':raw}
                 and stored==body,'EXPLICIT_REQUEST_CONFLICT')
            if _consumed_terminal(active,db,record,body):return sid
            validate_root(body)
            return sid  # Already intaken: no DDL, INSERT, or state reset.
    validate_root(body)
    return maintenance_install_and_intake(active,body)

def activate(active):
    consume_explicit_maintenance_request(active)
    if getattr(active,'plan_id',None)!=core.PLAN or getattr(active,'decision_id',None)!=core.DECISION:return
    with active.store._connect_readonly() as db:record=_record(db)
    if record is None:return
    core.protected(active);row,body=record
    with active.store._connect_readonly() as db:
        if _consumed_terminal(active,db,record,body):return
    _,parent=validate_root(body);_checkpoint(active,parent)
    with active.store._transaction() as db:
        active.service._decision(db,core.DECISION,owner_sid=active.owner_sid);active.store._require_active_run(db,core.RUN)
        if db.execute('SELECT 1 FROM native_tiktok_preparation_successor_starts WHERE successor_id=?',(row['successor_id'],)).fetchone():return
        validate_state(db,body,status='FAILED',attempt=3,require_unspent=True)
        db.execute('INSERT INTO native_tiktok_preparation_successor_starts VALUES(?,4)',(row['successor_id'],))
        changed=db.execute("UPDATE release_target_runs SET status='PENDING',error=NULL,completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=3",(core.RUN,LABEL))
        need(changed.rowcount==1,'ACTIVATION_CAS_LOST')

def actions(active,run,values):
    with active.store._connect_readonly() as db:record=_record(db)
    if record is None:return values
    core.protected(active);row,body=record;need(run['run_id']==core.RUN,'RUN_CHANGED')
    target=next(t for t in run['targets'] if t['target_label']==LABEL)
    out=[{**a,'runnable':False} if a['target_label'].startswith('tiktok:') and a['target_label']!=LABEL else a for a in values]
    if (target['status'],target['attempts'])!=('PENDING',3):return out
    _,parent=validate_root(body);_checkpoint(active,parent)
    with active.store._connect_readonly() as db:
        validate_state(db,body,status='PENDING',attempt=3,require_unspent=True)
        need(_rows(db,'native_tiktok_preparation_successor_starts','successor_id',row['successor_id'])==[{'successor_id':row['successor_id'],'target_attempt':4}],'PROTECTED_START_REQUIRED')
    return [{**a,'runnable':True,'action_kind':'EXACT_LH_MY_UNSPENT_CORE_SUCCESSOR','reason_code':'failed_preparation_zero_dispatch_existing_allowance'} if a['target_label']==LABEL else a for a in out]

def for_active(active,base):
    if type(base) is not core._DurableLedger or base.platform!='TIKTOK' or base.target_label!=LABEL:return None
    with base._connect(readonly=True) as db:record=_record(db)
    if record is None:return None
    core.protected(active);row,body=record;_,parent=validate_root(body);_checkpoint(active,parent)
    with base._connect(readonly=True) as db:
        validate_state(db,body,status='RUNNING',attempt=4,require_unspent=True)
        need(_rows(db,'native_tiktok_preparation_successor_starts','successor_id',row['successor_id'])==[{'successor_id':row['successor_id'],'target_attempt':4}],'PROTECTED_START_REQUIRED')
    facts,_,_=_facts(body)
    return core._CoreLedger(active,base,facts['parent_grant'],parent,core.frozen_target(LABEL),successor=record)

def runtime_check(ledger,db=None):
    core.protected(ledger.active);row,body=ledger.successor;_,parent=validate_root(body);_checkpoint(ledger.active,parent)
    if ledger.base._recheck:ledger.base._recheck(db)
    def check(current):
        need(_record(current)==ledger.successor,'SUCCESSOR_ROW_CHANGED')
        need(_rows(current,'native_tiktok_preparation_successor_starts','successor_id',row['successor_id'])==[{'successor_id':row['successor_id'],'target_attempt':4}],'PROTECTED_START_REQUIRED')
        pending=ledger.base._pending.get('publish_target')
        validate_state(current,body,status='RUNNING',attempt=4,allowed_pending_publish=pending['sequence'] if pending else None)
    if db is not None:check(db)
    else:
        with ledger.base._connect(readonly=True) as current:check(current)
