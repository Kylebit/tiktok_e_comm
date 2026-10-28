"""Exact ten accepted-SAVE core continuation, with two immutable target roles.

Independent append-only grant; no widening of rejected-SAVE grants or base cap.
Only explicit maintenance installs/intakes. Ordinary GET/factory never migrates.
"""
from copy import deepcopy
from pathlib import Path
import hashlib,json,re,sqlite3
from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold,_json,_sha,_request_sha,_DurableLedger
from shared_platform.native_known_rejected_save_repair import _read_reference,_base_rows

PLAN='r3-marketplace:4039931504:b21fbd9f11f01e3d04fc0780'
RUN='release-run:715e2b6e14103dbcdb129128'
DECISION='native-final-decision:da22e734faad76bf1de4b00ffca5dba3421682d0ce94f68dc6595b7440935ef6'
SNAPSHOT='sha256:ed9fb745466e25ae91da35a21f349ccec5dd7703fc14d217e04fd4447472d1c1'
ORIGINAL_FOUR={'tiktok:HB_PH':(10,'3464605397','15173238'),'tiktok:HB_VN':(17,'3464609151','16783702'),
          'tiktok:LH_PH':(24,'3464611348','7676267'),'tiktok:LH_VN':(31,'3464613765','13295291')}
ORIGINAL_SIX={'tiktok:GB':(None,'3464602559','10204699'),'tiktok:HB_MY':(None,'3464603735','16770639'),
 'tiktok:HB_TH':(None,'3464607443','16770557'),'tiktok:LH_MY':(None,'3464610329','13295169'),
 'tiktok:LH_TH':(None,'3464612979','13295228'),'tiktok:MX':(None,'3464614703','16265910')}
TARGET_ORDER=['tiktok:'+s for s in ('LH_PH','LH_MY','LH_TH','LH_VN','HB_PH','HB_MY','HB_TH','HB_VN','MX','GB')]
ORIGINAL={label:(ORIGINAL_FOUR|ORIGINAL_SIX)[label] for label in TARGET_ORDER}
FOUR='FOUR_ORIGINAL_PREWIRE_SLOT'
SIX='SIX_SPENT_ACCEPTED_SAVE_NO_PUBLISH'
ROLE_LIMITS={FOUR:{'before_attempt':1,'after_attempt':2,'new_publish_reservations':0,'combined_target_maximum':4},
             SIX:{'before_attempt':2,'after_attempt':3,'new_publish_reservations':1,'combined_target_maximum':5}}
SIX_FACTS_REF={'path':'E:/OrbitEvidence/4039931504-publication-20261019/six-accepted-save-facts-readonly-20261019b/SIX_EXACT_CURRENT_FACTS.private.json',
 'sha256':'8065e98e5d1a2d59a7824df7165a516aeabf124c1d4d9813edd8b59edb9a1d6d'}
SIX_CP_REF={'path':'E:/OrbitEvidence/4039931504-publication-20261019/six-accepted-save-facts-readonly-20261019b/CURRENT_ORIGINAL_CHECKPOINT.json',
 'sha256':'a97d2702305e46e6f131fa39f1fbd3efdb9cd751f22b7d5fadbea18d54c5687f'}
ORIGINAL_REF={'path':'E:/OrbitEvidence/4039931504-publication-20261019/four-unknown-readonly-reconciliation/ORIGINAL_IDENTITIES.json',
 'sha256':'41a007e53e22c5c720888ae4e60e81b7c6250d11f00d1a32683d5eea44a2a09c'}
SCHEMA='''
CREATE TABLE native_accepted_core_grants (grant_id TEXT PRIMARY KEY,scope_key TEXT NOT NULL,target_label TEXT NOT NULL,body_json TEXT NOT NULL,UNIQUE(scope_key,target_label));
CREATE TABLE native_accepted_core_starts (grant_id TEXT PRIMARY KEY,target_attempt INTEGER NOT NULL CHECK(target_attempt IN (2,3)),FOREIGN KEY(grant_id) REFERENCES native_accepted_core_grants(grant_id));
CREATE TABLE native_accepted_core_reservations (grant_id TEXT PRIMARY KEY,attempt_count INTEGER NOT NULL CHECK(attempt_count=1),FOREIGN KEY(grant_id) REFERENCES native_accepted_core_grants(grant_id));
CREATE TABLE native_accepted_core_dispatches (grant_id TEXT PRIMARY KEY,request_digest TEXT NOT NULL,FOREIGN KEY(grant_id) REFERENCES native_accepted_core_reservations(grant_id));
CREATE TABLE native_accepted_core_outcomes (grant_id TEXT PRIMARY KEY,outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED','REJECTED')),FOREIGN KEY(grant_id) REFERENCES native_accepted_core_dispatches(grant_id));
CREATE TABLE native_accepted_core_verifications (grant_id TEXT PRIMARY KEY,evidence_digest TEXT NOT NULL,FOREIGN KEY(grant_id) REFERENCES native_accepted_core_grants(grant_id));
'''
for table in ('grants','starts','reservations','dispatches','outcomes','verifications'):
    for event in ('UPDATE','DELETE'):
        SCHEMA+='CREATE TRIGGER native_accepted_core_'+table+'_no_'+event.lower()+' BEFORE '+event+' ON native_accepted_core_'+table+" BEGIN SELECT RAISE(ABORT,'immutable accepted core repair'); END;\n"

def need(value,code):
    if not value:raise NativeMutationBudgetHold('ACCEPTED_CORE_'+code)

def role(label):
    need(label in ORIGINAL,'EXACT_TEN_ONLY')
    return FOUR if label in ORIGINAL_FOUR else SIX

def six_facts(label):
    rows=json.loads(_read_reference(SIX_FACTS_REF))
    need(type(rows) is list and {r['target_label'] for r in rows}==set(ORIGINAL_SIX),'EXACT_SIX_FACTS_REQUIRED')
    return next(r for r in rows if r['target_label']==label)

def statements():
    pending='';out=[]
    for line in SCHEMA.splitlines(True):
        pending+=line
        if sqlite3.complete_statement(pending):out.append(pending.strip());pending=''
    need(not pending.strip(),'SCHEMA_INCOMPLETE');return out

def check_schema(db,*,optional=False):
    rows={r['name']:r['sql'] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'native_accepted_core_%'")}
    if not rows and optional:return False
    expected={re.search(r'CREATE (?:TABLE|TRIGGER) (\w+)',s)[1]:s for s in statements()}
    norm=lambda s:re.sub(r'\s+',' ',s.strip().rstrip(';')).lower()
    need(set(rows)==set(expected) and all(norm(rows[k])==norm(v) for k,v in expected.items()),'EXPLICIT_SCHEMA_REQUIRED');return True

def originals():
    document=json.loads(_read_reference(ORIGINAL_REF))
    checkpoint=json.loads(_read_reference(document['checkpoint_snapshot_ref']))
    snapshot=json.loads(_read_reference(document['approved_snapshot_ref']))
    need(snapshot.get('snapshot_digest')==SNAPSHOT,'SNAPSHOT_CHANGED')
    return document,checkpoint,snapshot

def frozen_target(label):
    from modules.miaoshou.tiktok_v4_drafts import _collectbox_context
    from domains.channel_operations.tiktok_v4_execution import project_tiktok_v4_execution_plan
    from domains.channel_operations.tiktok_publisher import _validate_snapshot
    _,_,snapshot=originals();_,detail,shop=ORIGINAL[label]
    identity={'target_label':label,'detail_id':detail,'shop_id':shop}
    plan=project_tiktok_v4_execution_plan(snapshot,collectbox_contexts={label:_collectbox_context(snapshot,identity=identity)},category_resolver=None,target_scope=(label,))
    need(not plan['blocked_targets'] and len(plan['targets'])==1,'FROZEN_COMMAND_REQUIRED')
    return _validate_snapshot(plan['targets'][0]['publisher_snapshot'])['targets'][0]

def checkpoint(body,current):
    _,original,_=originals();label=body['target_label'];_,detail,shop=ORIGINAL[label]
    need(all(current.get(k)==original.get(k) for k in ('run_id','offer_id','product_revision','snapshot_digest')),'CHECKPOINT_IDENTITY_CHANGED')
    need(current.get('checkpoint_digest')=='sha256:'+_sha(_json({k:v for k,v in current.items() if k!='checkpoint_digest'})),'CHECKPOINT_DIGEST_CHANGED')
    events=[e for e in current['events'] if e.get('target_label')==label]
    expected=[e for e in original['events'] if e.get('target_label')==label]
    if role(label)==FOUR:
        need(events==expected and len(events)==3 and [(e['operation'],e['outcome']) for e in events]==[
            ('CREATE_DRAFT','ACCEPTED'),('CLAIM_TO_SHOP','ACCEPTED'),('SAVE_DRAFT','ACCEPTED')],'EXACT_FOUR_ACCEPTED_CHECKPOINT_REQUIRED')
    else:
        retained=json.loads(_read_reference(SIX_CP_REF));actual=[e for e in retained['events'] if e['target_label']==label]
        need(events==actual and len(events)==4 and events[:3]==expected
            and [(e['operation'],e['outcome']) for e in events]==[('CREATE_DRAFT','ACCEPTED'),('CLAIM_TO_SHOP','ACCEPTED'),
                ('SAVE_DRAFT','REJECTED'),('SAVE_DRAFT','ACCEPTED')],'EXACT_SIX_FOUR_EVENTS_REQUIRED')
    need(all((e['detail_id'],e['shop_id'])==(detail,shop) for e in events),'CHECKPOINT_IDENTITY_CHANGED')
    need(_sha(_json(events))==body['checkpoint_events_digest'],'CHECKPOINT_EVENTS_CHANGED')

def validate_root(body):
    root=json.loads(_read_reference(body['root_addendum_ref']))
    need(root.get('schema')=='root-ten-accepted-save-core-repair/v2' and root.get('status')=='LIVE_ADOPTION_APPROVED'
        and (root.get('plan_id'),root.get('run_id'),root.get('decision_id'),root.get('snapshot_digest'))==(PLAN,RUN,DECISION,SNAPSHOT)
        and root.get('target_labels')==TARGET_ORDER and root.get('original_identities_ref')==ORIGINAL_REF
        and root.get('six_facts_ref')==SIX_FACTS_REF and root.get('six_checkpoint_ref')==SIX_CP_REF
        and _json(root.get('role_limits'))==_json(ROLE_LIMITS)
        and root.get('extra_save_maximum')==1 and type(root.get('extra_save_maximum')) is int
        and root.get('new_human_decision') is False
        and _sha(_json({k:v for k,v in body.items() if k!='root_addendum_ref'})) in root.get('grant_core_digests',[]),'LIVE_EXACT_ROOT_REQUIRED')
    pins=json.loads(_read_reference(root['source_pins_ref']));app=Path(__file__).resolve().parents[1]
    need('shared_platform/native_accepted_save_core_repair.py' in pins,'SOURCE_PIN_REQUIRED')
    for name,row in pins.items():
        path=app/name;need(path.resolve().is_relative_to(app) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==row['after_sha256'],'ADOPTED_SOURCE_CHANGED')

def validate_original(db,body,*,status,attempt,allowed_pending_publish=None):
    keys={'schema','plan_id','run_id','decision_id','snapshot_digest','scope_key','target_label','detail_id','shop_id','sequence',
          'failure_evidence_digests','original_six_grant_id','role','original_reservations_digest','checkpoint_events_digest','canonical_checkpoint_path','root_addendum_ref'}
    need(type(body) is dict and set(body)==keys and body['schema']=='ten-accepted-save-core-grant/v2','EXACT_BODY_REQUIRED')
    label=body['target_label'];kind=role(label);limits=ROLE_LIMITS[kind];sequence,detail,shop=ORIGINAL[label]
    need((body['plan_id'],body['run_id'],body['decision_id'],body['snapshot_digest'])==(PLAN,RUN,DECISION,SNAPSHOT)
        and body['role']==kind and (body['sequence'],body['detail_id'],body['shop_id'])==(sequence,detail,shop),'ORIGINAL_IDENTITY_CHANGED')
    need(attempt==(limits['after_attempt'] if status=='RUNNING' else limits['before_attempt']),'ROLE_ATTEMPT_CHANGED')
    row=db.execute('SELECT * FROM native_final_budget_scopes WHERE scope_key=?',(body['scope_key'],)).fetchone()
    need(row is not None,'ORIGINAL_BUDGET_REQUIRED');binding=json.loads(row['binding_json'])
    need(_sha(row['binding_json'])==row['binding_sha256'] and (binding['plan_id'],binding['snapshot_digest'],binding['decision_id'],binding['platform'],binding['per_target_maximum'])==(PLAN,SNAPSHOT,DECISION,'TIKTOK',3)
        and label in binding['target_labels'],'ORIGINAL_CAP3_BINDING_CHANGED')
    rows=_base_rows(db,body['scope_key'],label)
    if kind==FOUR:
        document,_,_=originals();item=next(t for t in document['targets'] if t['target_label']==label)
        expected=[{**r,'outcome':r['outcome'] or 'UNKNOWN'} for r in item['mutation_attempts']]
        need(rows==expected and body['original_six_grant_id'] is None,'FOUR_ORIGINAL_SLOT_CHANGED')
        need(rows[-1]['sequence']==sequence and rows[-1]['operation']=='publish_target' and rows[-1]['outcome']=='UNKNOWN'
            and rows[-1]['request_digest'] is None,'EXACT_UNDISPATCHED_SLOT_REQUIRED')
        expected_failures=[item['original_failure_evidence_digest']]
    else:
        item=six_facts(label);expected=item['original_budget_records']
        need(rows[:3]==expected and len(rows)==(4 if allowed_pending_publish is not None else 3),'SIX_NO_OLD_PUBLISH_REQUIRED')
        if allowed_pending_publish is not None:
            pending=rows[-1]
            need(status=='RUNNING' and pending['sequence']==allowed_pending_publish and pending['target_label']==label
                and pending['budget_scope']=='target' and pending['operation']=='publish_target' and pending['attempt_count']==1
                and pending['request_digest'] is None and pending['outcome']=='UNKNOWN','EXACT_NEW_PENDING_PUBLISH_REQUIRED')
        from shared_platform.native_known_rejected_save_repair import check_schema as old_schema
        old_schema(db)
        grant=db.execute('SELECT * FROM native_save_repair_grants WHERE scope_key=? AND target_label=?',(body['scope_key'],label)).fetchone()
        need(grant is not None and dict(grant)==item['original_six_grant'] and body['original_six_grant_id']==grant['grant_id'],'ORIGINAL_SPENT_SIX_GRANT_CHANGED')
        for table in ('starts','reservations','dispatches','outcomes','verifications'):
            actual=[dict(r) for r in db.execute('SELECT * FROM native_save_repair_'+table+' WHERE grant_id=?',(grant['grant_id'],))]
            need(actual==item['original_six_grant_tables'][table],'ORIGINAL_SIX_'+table.upper()+'_CHANGED')
        old=item['original_six_grant_tables']
        need(len(old['starts'])==len(old['reservations'])==len(old['dispatches'])==len(old['outcomes'])==1
            and not old['verifications'] and old['starts'][0]['target_attempt']==2 and old['reservations'][0]['operation']=='save_draft'
            and old['reservations'][0]['attempt_count']==1 and old['outcomes'][0]['outcome']=='ACCEPTED','SIX_ACCEPTED_SPENT_NO_VERIFICATION_REQUIRED')
        expected_failures=[f['evidence_digest'] for f in item['failures']]
    need(_sha(_json(expected))==body['original_reservations_digest'],'ORIGINAL_RESERVATIONS_CHANGED')
    target=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(RUN,label)).fetchone()
    need(target is not None and (target['status'],target['attempts'],target['external_id'])==(status,attempt,None),'ORIGINAL_ATTEMPT_CHANGED')
    failures=db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',(RUN,label)).fetchall()
    need([f['attempt'] for f in failures]==list(range(1,limits['before_attempt']+1))
        and [f['evidence_digest'] for f in failures]==body['failure_evidence_digests']==expected_failures
        and all(_sha(f['evidence_json'])==f['evidence_digest'] for f in failures),'ORIGINAL_FAILURE_CHANGED')
    if kind==SIX:
        need([dict(f) for f in failures]==item['failures'] and target['idempotency_key']==item['target']['idempotency_key'],'EXACT_SIX_FAILURES_OR_KEY_CHANGED')
        proof=json.loads(failures[1]['evidence_json']);nested=proof['canonical_platform_result']['targets']
        need(proof['target_label']==label and proof['outcome_unknown'] is False and proof['request_attempted'] is False
            and len(nested)==1 and nested[0]['target_label']==label and nested[0]['evidence']['provider_code']=='write_budget_exceeded'
            and nested[0]['evidence']['stage']=='PUBLISH' and nested[0]['evidence']['request_attempted'] is False,'SIX_EXACT_PUBLISH_NOT_ATTEMPTED_FAILURE_REQUIRED')
    for table in ('release_target_submissions','release_target_readbacks','release_target_repairs'):
        need(db.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND target_label=?',(RUN,label)).fetchone() is None,'EXTERNAL_OUTCOME_NO_REPLAY')
    run=db.execute('SELECT plan_id FROM release_runs WHERE run_id=?',(RUN,)).fetchone();need(run and run['plan_id']==PLAN,'RUN_CHANGED')

def intake(db,body,current):
    check_schema(db);validate_original(db,body,status='FAILED',attempt=ROLE_LIMITS[role(body['target_label'])]['before_attempt']);validate_root(body);checkpoint(body,current)
    raw=_json(body);grant_id=_sha(raw)
    db.execute('INSERT INTO native_accepted_core_grants VALUES(?,?,?,?)',(grant_id,body['scope_key'],body['target_label'],raw))
    return grant_id

def protected(active):
    from shared_platform.native_sole_final_execution import _ACTIVE,_NativeExecution
    from shared_platform.native_prepared_v4_targets import _scope
    need(type(active) is _NativeExecution and _ACTIVE.get() is active and active.plan_id==PLAN and active.decision_id==DECISION
        and active.store is active.service.store and active.service._actor()['owner_sid']==active.owner_sid,'PROTECTED_ACTIVE_REQUIRED')
    _scope(active,active.store.get_plan(PLAN))

def current_checkpoint(active,body):
    from shared_platform.native_prepared_v4_targets import _file_identity
    mapping=_file_identity(run_id=RUN,plan_id=PLAN,decision_id=DECISION,snapshot_digest=SNAPSHOT)
    expected=Path(active.runtime.supervisor.root)/'4039931504'/'2'/mapping['file_run_id']/'tiktok-draft-checkpoint.json'
    path=Path(body['canonical_checkpoint_path']);need(path.resolve(strict=True)==expected.resolve(strict=True),'CANONICAL_CHECKPOINT_PATH_CHANGED')
    value=json.loads(path.read_bytes());checkpoint(body,value);return value

def _grants(db,scope=None):
    if not check_schema(db,optional=True):return []
    rows=db.execute('SELECT * FROM native_accepted_core_grants'+(' WHERE scope_key=?' if scope else ''),(scope,) if scope else ()).fetchall()
    out=[]
    for row in rows:
        row=dict(row);need(_sha(row['body_json'])==row['grant_id'],'GRANT_CHANGED');body=json.loads(row['body_json'])
        need(row['scope_key']==body['scope_key'] and row['target_label']==body['target_label'],'GRANT_ROW_CHANGED');out.append((row,body))
    return out

def activate_failed_targets(active):
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return
    from shared_platform.native_tiktok_core_save_before_publish import activate
    activate(active)
    with active.store._connect_readonly() as db:grants=_grants(db)
    if not grants:return
    protected(active)
    with active.store._transaction() as db:
        active.service._decision(db,DECISION,owner_sid=active.owner_sid);active.store._require_active_run(db,RUN)
        for grant,body in grants:
            old=db.execute('SELECT 1 FROM native_accepted_core_starts WHERE grant_id=?',(grant['grant_id'],)).fetchone()
            if old:continue
            limits=ROLE_LIMITS[role(body['target_label'])]
            validate_original(db,body,status='FAILED',attempt=limits['before_attempt']);validate_root(body);current_checkpoint(active,body)
            db.execute('INSERT INTO native_accepted_core_starts VALUES(?,?)',(grant['grant_id'],limits['after_attempt']))
            changed=db.execute("UPDATE release_target_runs SET status='PENDING',error=NULL,completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=?",(RUN,body['target_label'],limits['before_attempt']))
            need(changed.rowcount==1,'ACTIVATION_CAS_LOST')

def recovery_actions(active,run,actions):
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return actions
    from shared_platform.native_tiktok_core_save_before_publish import actions as successor_actions
    actions=successor_actions(active,run,actions)
    with active.store._connect_readonly() as db:grants=_grants(db)
    if not grants:return actions
    protected(active);need(run['run_id']==RUN,'RUN_CHANGED');eligible=set()
    with active.store._connect_readonly() as db:
        for grant,body in grants:
            row=next(t for t in run['targets'] if t['target_label']==body['target_label'])
            limits=ROLE_LIMITS[role(body['target_label'])]
            if (row['status'],row['attempts'])!=('PENDING',limits['before_attempt']):continue
            validate_original(db,body,status='PENDING',attempt=limits['before_attempt']);validate_root(body);current_checkpoint(active,body)
            started=db.execute('SELECT target_attempt FROM native_accepted_core_starts WHERE grant_id=?',(grant['grant_id'],)).fetchone()
            need(started and started['target_attempt']==limits['after_attempt'],'PROTECTED_START_REQUIRED')
            eligible.add(body['target_label'])
    return [{**a,'runnable':True,'action_kind':'EXACT_TEN_ACCEPTED_CORE_REPAIR','reason_code':'fixed_role_and_single_new_corrective_save'} if a['target_label'] in eligible else a for a in actions]

def for_active(active,base):
    from shared_platform.native_tiktok_core_save_before_publish import for_active as successor_for_active
    successor=successor_for_active(active,base)
    if successor is not None:return successor
    if type(base) is not _DurableLedger or base.platform!='TIKTOK' or base.target_label not in ORIGINAL:return base
    with base._connect(readonly=True) as db:
        grants=[g for g in _grants(db,base.scope_key) if g[1]['target_label']==base.target_label]
        if not grants:return base
        need(len(grants)==1,'ONE_GRANT_REQUIRED');grant,body=grants[0]
        limits=ROLE_LIMITS[role(body['target_label'])]
        validate_original(db,body,status='RUNNING',attempt=limits['after_attempt']);validate_root(body)
        started=db.execute('SELECT target_attempt FROM native_accepted_core_starts WHERE grant_id=?',(grant['grant_id'],)).fetchone()
        need(started and started['target_attempt']==limits['after_attempt'],'PROTECTED_START_REQUIRED')
        need(db.execute('SELECT 1 FROM native_accepted_core_reservations WHERE grant_id=?',(grant['grant_id'],)).fetchone() is None,'EXTRA_SAVE_SPENT_OR_UNKNOWN')
    protected(active);current_checkpoint(active,body)
    return _CoreLedger(active,base,grant,body,frozen_target(body['target_label']))

class _CoreLedger:
    def __init__(self,active,base,grant,body,target,*,successor=None):
        self.active=active;self.base=base;self.grant=grant;self.body=body;self.target=target
        self.successor=successor
        self.prepared=None;self.fresh_match=False;self.reserved=False
    def __getattr__(self,key):return getattr(self.base,key)
    def _check(self,db=None):
        if self.successor is not None:
            from shared_platform.native_tiktok_core_save_before_publish import runtime_check
            return runtime_check(self,db)
        protected(self.active);validate_root(self.body);current_checkpoint(self.active,self.body)
        if self.base._recheck:self.base._recheck(db)
        if db is None:
            with self.base._connect(readonly=True) as current:self._persisted(current)
        else:self._persisted(db)
    def _persisted(self,db):
        pending=self.base._pending.get('publish_target')
        allowed=pending['sequence'] if role(self.body['target_label'])==SIX and pending is not None else None
        check_schema(db);validate_original(db,self.body,status='RUNNING',attempt=ROLE_LIMITS[role(self.body['target_label'])]['after_attempt'],allowed_pending_publish=allowed)
        row=db.execute('SELECT body_json FROM native_accepted_core_grants WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
        need(row and row['body_json']==_json(self.body),'GRANT_CHANGED')
    def _target(self,target):need(target==self.target and len(target['expected_images'])==8 and len(set(target['expected_images']))==8,'FROZEN_TARGET_CHANGED')
    def reserve_shared(self,*args,**kwargs):raise NativeMutationBudgetHold('ACCEPTED_CORE_NO_RECREATE')
    def bind_prepared(self,target,draft,prepared):
        from modules.miaoshou.tiktok_v4_drafts import SAVE_SITE_DRAFT_PATH,SAVE_SHOP_DRAFT_PATH
        from shared_platform.product_publication_live_dependencies import _TIKTOK_SITE_BY_TARGET
        self._target(target);self._check();body=prepared.get('body') or {}
        label=self.body['target_label'];site=label not in {'tiktok:GB','tiktok:MX'}
        need(not self.fresh_match and self.prepared is None and prepared.get('path')==(SAVE_SITE_DRAFT_PATH if site else SAVE_SHOP_DRAFT_PATH)
            and str(body.get('detailId'))==self.body['detail_id']
            and (body.get('site')==_TIKTOK_SITE_BY_TARGET[label] if site else str(body.get('shopId'))==self.body['shop_id'])
            and type(body.get('ossMd5')) is str and bool(body['ossMd5']) and body['ossMd5']==draft.get('oss_md5'),'FRESH_PREPARED_SAVE_REQUIRED')
        self.prepared=deepcopy(prepared)
    def retain_match(self,target,draft,matched):
        self._target(target);self._check();self.fresh_match=matched is True
        if not self.fresh_match:return
        with self.base._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check(db)
            used=db.execute('SELECT 1 FROM native_accepted_core_reservations WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
            known=db.execute('SELECT outcome FROM native_accepted_core_outcomes WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
            need(not used or known and known['outcome']=='ACCEPTED','EXTRA_SAVE_UNKNOWN_OR_REJECTED')
            db.execute('INSERT OR IGNORE INTO native_accepted_core_verifications VALUES(?,?)',(self.grant['grant_id'],_sha(_json(draft))));db.commit()
    def require_publish_match(self,target):
        self._target(target);self._check();need(self.fresh_match,'FRESH_EXACT_POST_SAVE_READ_REQUIRED')
    def reserve_target(self,label,operation,*,count=1):
        need(label==self.body['target_label'] and type(count) is int and count==1,'TARGET_OR_COUNT_CHANGED');self._check()
        if operation=='publish_target':
            if role(label)==SIX:
                # Six accepted corrective attempts have never reserved publish.
                # Use their one remaining original cap3 slot exactly once.
                return self.base.reserve_target(label,operation,count=count)
            # Only rehydrate the exact old slot in memory. The base dispatch
            # still commits its unique dispatch row before its one wire call.
            with self.base._connect(readonly=True) as db:validate_original(db,self.body,status='RUNNING',attempt=2)
            record={'sequence':self.body['sequence'],'scope':'target','target_label':label,'operation':operation,'attempt_count':1,'outcome':'UNKNOWN'}
            self.base._pending[operation]=record;return dict(record)
        need(operation=='save_draft' and self.prepared is not None and not self.fresh_match,'ONE_EXTRA_SAVE_ONLY')
        with self.base._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check(db)
            need(db.execute('SELECT 1 FROM native_accepted_core_reservations WHERE grant_id=?',(self.grant['grant_id'],)).fetchone() is None,'EXTRA_SAVE_SPENT_OR_UNKNOWN')
            db.execute('INSERT INTO native_accepted_core_reservations VALUES(?,1)',(self.grant['grant_id'],));db.commit()
        self.reserved=True;return {'scope':'accepted_core_repair','operation':operation,'attempt_count':1,'outcome':'UNKNOWN'}
    def dispatch_reserved(self,operation,call,*args,**kwargs):
        self._check()
        if operation=='publish_target':
            from modules.miaoshou.tiktok_publisher import PUBLISH_PATH
            self.require_publish_match(self.target)
            label=self.body['target_label'];shop=self.body['shop_id'] if label not in {'tiktok:GB','tiktok:MX'} else int(self.body['shop_id'])
            expected=(PUBLISH_PATH,{'detailIds':[int(self.body['detail_id'])],'shopIds':[shop]})
            need(args==expected and not kwargs,'ORIGINAL_PUBLISH_BODY_CHANGED')
            return self.base.dispatch_reserved(operation,call,*args,**kwargs)
        need(operation=='save_draft' and self.reserved and args==(self.prepared['path'],self.prepared['body']) and not kwargs,'EXACT_PREPARED_WIRE_REQUIRED')
        with self.base._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check(db)
            need(db.execute('SELECT 1 FROM native_accepted_core_dispatches WHERE grant_id=?',(self.grant['grant_id'],)).fetchone() is None,'EXTRA_SAVE_DISPATCH_ONCE')
            db.execute('INSERT INTO native_accepted_core_dispatches VALUES(?,?)',(self.grant['grant_id'],_request_sha(operation,args,kwargs)));db.commit()
        return call(*args,**kwargs)
    def complete_reserved(self,operation,outcome):
        if operation!='save_draft':return self.base.complete_reserved(operation,outcome)
        need(self.reserved and outcome in {'ACCEPTED','REJECTED'},'TYPED_SAVE_OUTCOME_REQUIRED');self._check()
        with self.base._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check(db)
            row=db.execute('SELECT outcome FROM native_accepted_core_outcomes WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
            need(row is None or row['outcome']==outcome,'SAVE_OUTCOME_CONFLICT')
            if row is None:db.execute('INSERT INTO native_accepted_core_outcomes VALUES(?,?)',(self.grant['grant_id'],outcome))
            db.commit()
    def wrap_post(self,post,*,provider):return _DurableLedger.wrap_post(self,post,provider=provider)
    def snapshot(self):return self.base.snapshot()
    @property
    def total_attempt_count(self):return self.snapshot()['attempts']['total']
    @property
    def target_attempt_counts(self):return self.snapshot()['attempts']['per_target']
    @property
    def reservations(self):return self.snapshot()['reservations']

def include_attempts(base,value):
    with base._connect(readonly=True) as db:
        if not check_schema(db,optional=True):return value
        rows=db.execute("SELECT g.grant_id,g.target_label,r.attempt_count,coalesce(o.outcome,'UNKNOWN') AS outcome FROM native_accepted_core_grants g JOIN native_accepted_core_reservations r USING(grant_id) LEFT JOIN native_accepted_core_outcomes o USING(grant_id) WHERE g.scope_key=?",(base.scope_key,)).fetchall()
    if 'base_attempts' not in value:value['base_attempts']=deepcopy(value['attempts'])
    value['accepted_core_repair_attempts']={'per_target':{l:0 for l in base.target_labels},'total':0}
    for row in rows:
        label=row['target_label'];value['attempts']['per_target'][label]+=1;value['attempts']['total']+=1
        value['accepted_core_repair_attempts']['per_target'][label]+=1;value['accepted_core_repair_attempts']['total']+=1
        value['reservations'].append({'sequence':'accepted-core:'+row['grant_id'],'scope':'accepted_core_repair','target_label':label,
            'mutation_target_label':label,'operation':'save_draft','attempt_count':1,'outcome':row['outcome']})
    value['accepted_core_repair_limits']={'base_target_maximum':3,'new_core_extra_save_maximum':1,'role_limits':deepcopy(ROLE_LIMITS)}
    return value
