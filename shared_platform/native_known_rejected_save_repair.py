"""One exact rejected-SAVE compensation; no replacement approval or base cap edit.

DDL/intake are explicit maintenance operations. GET, construction and the
protected execution factory only read them. All provider dispatch remains on
the original active native ledger and the original signed provider seam.
"""
from copy import deepcopy
import hashlib
import json
import re
import sqlite3
import stat
from pathlib import Path

from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold, _json, _sha, _request_sha

PLAN = 'r3-marketplace:4039931504:b21fbd9f11f01e3d04fc0780'
RUN = 'release-run:715e2b6e14103dbcdb129128'
DECISION = 'native-final-decision:da22e734faad76bf1de4b00ffca5dba3421682d0ce94f68dc6595b7440935ef6'
SNAPSHOT = 'sha256:ed9fb745466e25ae91da35a21f349ccec5dd7703fc14d217e04fd4447472d1c1'
LABELS = frozenset({'tiktok:GB','tiktok:HB_MY','tiktok:HB_TH','tiktok:LH_MY','tiktok:LH_TH','tiktok:MX'})
REASON = '您编辑过程中产品数据发生变动，为避免覆盖其它重要修改，请重新打开弹窗编辑。'
SCHEMA = '''
CREATE TABLE native_save_repair_grants (grant_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, target_label TEXT NOT NULL, run_id TEXT NOT NULL, body_json TEXT NOT NULL, body_sha256 TEXT NOT NULL, UNIQUE(scope_key,target_label));
CREATE TABLE native_save_repair_starts (grant_id TEXT PRIMARY KEY, target_attempt INTEGER NOT NULL CHECK(target_attempt=2), FOREIGN KEY(grant_id) REFERENCES native_save_repair_grants(grant_id));
CREATE TABLE native_save_repair_reservations (grant_id TEXT PRIMARY KEY, operation TEXT NOT NULL CHECK(operation='save_draft'), attempt_count INTEGER NOT NULL CHECK(attempt_count=1), FOREIGN KEY(grant_id) REFERENCES native_save_repair_grants(grant_id));
CREATE TABLE native_save_repair_dispatches (grant_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, FOREIGN KEY(grant_id) REFERENCES native_save_repair_reservations(grant_id));
CREATE TABLE native_save_repair_outcomes (grant_id TEXT PRIMARY KEY, outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED','REJECTED')), FOREIGN KEY(grant_id) REFERENCES native_save_repair_dispatches(grant_id));
CREATE TABLE native_save_repair_verifications (grant_id TEXT PRIMARY KEY, evidence_digest TEXT NOT NULL, FOREIGN KEY(grant_id) REFERENCES native_save_repair_outcomes(grant_id));
'''
for table in ('grants','starts','reservations','dispatches','outcomes','verifications'):
    for event in ('UPDATE','DELETE'):
        SCHEMA += 'CREATE TRIGGER native_save_repair_'+table+'_no_'+event.lower()+' BEFORE '+event+' ON native_save_repair_'+table+" BEGIN SELECT RAISE(ABORT,'immutable SAVE repair record'); END;\n"


def need(value, code):
    if not value: raise NativeMutationBudgetHold(code)


def schema_statements():
    statements=[]; current=''
    for line in SCHEMA.splitlines(keepends=True):
        current += line
        if sqlite3.complete_statement(current): statements.append(current.strip()); current=''
    need(not current.strip(), 'SAVE_REPAIR_SCHEMA_INCOMPLETE')
    return statements


def check_schema(db, *, optional=False):
    names=[re.search(r'CREATE (?:TABLE|TRIGGER) (\w+)',sql)[1] for sql in schema_statements()]
    rows={r['name']:r['sql'] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'native_save_repair_%'")}
    if not rows and optional: return False
    normalize=lambda s:re.sub(r'\s+',' ',s.strip().rstrip(';')).lower()
    need(set(rows)==set(names) and all(normalize(rows[name])==normalize(sql) for name,sql in zip(names,schema_statements())), 'SAVE_REPAIR_EXPLICIT_SCHEMA_REQUIRED')
    return True


def _unknown(value):
    if isinstance(value,dict):
        if (value.get('outcome')=='UNKNOWN' or value.get('status') in {'UNKNOWN','PROCESSING','SUBMITTED_UNVERIFIED'}
                or value.get('provider_code')=='transport_unknown' or value.get('outcome_unknown') is True
                or value.get('reconciliation_required') is True): return True
        return any(_unknown(v) for v in value.values())
    return isinstance(value,list) and any(_unknown(v) for v in value)


def validate_failure(evidence, label):
    need(type(evidence) is dict, 'SAVE_REPAIR_CANONICAL_REJECTION_REQUIRED')
    canonical=evidence.get('canonical_platform_result') or {}; rows=canonical.get('targets')
    # A platform-wide budget snapshot can include another target's UNKNOWN.
    # That remains unresolved there; only this target's canonical facts and
    # its own exact budget/CP rows can establish this compensation.
    relevant={k:evidence.get(k) for k in ('status','outcome_unknown','reconciliation_required')}
    need(not _unknown(relevant) and not _unknown(canonical), 'SAVE_REPAIR_UNKNOWN_NO_REPLAY')
    need(evidence.get('schema_version')=='native-scoped-v4-target-result/v1'
         and evidence.get('target_label')==label and evidence.get('status')=='FAILED'
         and evidence.get('verified') is False and evidence.get('request_attempted') is False
         and evidence.get('outcome_unknown') is False and evidence.get('reconciliation_required') is False
         and canonical.get('platform')=='TIKTOK' and canonical.get('dispatch_attempted') is False
         and canonical.get('readback_completed') is False and type(rows) is list and len(rows)==1,
         'SAVE_REPAIR_CANONICAL_REJECTION_REQUIRED')
    row=rows[0]; proof=row.get('evidence') or {}
    need(row.get('target_label')==label and row.get('status')=='FAILED'
         and proof.get('provider_code')=='fail' and proof.get('provider_reason')==REASON
         and proof.get('outcome_unknown') is False and proof.get('request_attempted') is True
         and proof.get('stage')=='PREPARATION',
         'SAVE_REPAIR_CANONICAL_VERSION_REJECTION_REQUIRED')


def validate_events(events, body):
    label=body['target_label']; identity=(body['detail_id'],body['shop_id'])
    need(type(events) is list and len(events)==3, 'SAVE_REPAIR_EXACT_ORIGINAL_CHECKPOINT_REQUIRED')
    need([(e.get('operation'),e.get('outcome')) for e in events]==[
        ('CREATE_DRAFT','ACCEPTED'),('CLAIM_TO_SHOP','ACCEPTED'),('SAVE_DRAFT','REJECTED')],
        'SAVE_REPAIR_ACCEPTED_OR_UNKNOWN_NO_REPLAY')
    need(all(e.get('target_label')==label and (e.get('detail_id'),e.get('shop_id'))==identity for e in events)
         and events[-1].get('provider_code')=='fail' and events[-1].get('provider_reason')==REASON
         and _sha(_json(events))==body['checkpoint_events_digest'], 'SAVE_REPAIR_CHECKPOINT_IDENTITY_CHANGED')


def validate_body(body):
    keys={'schema','plan_id','run_id','decision_id','snapshot_digest','scope_key','target_label','detail_id','shop_id',
          'failure_evidence_digest','checkpoint_ref','checkpoint_events_digest','original_reservations_digest',
          'original_reservations','root_addendum_ref','extra_save_maximum','base_target_maximum','combined_target_maximum'}
    need(type(body) is dict and set(body)==keys and body['schema']=='known-rejected-save-compensation/v1', 'SAVE_REPAIR_EXACT_GRANT_REQUIRED')
    need((body['plan_id'],body['run_id'],body['decision_id'],body['snapshot_digest'])==(PLAN,RUN,DECISION,SNAPSHOT)
         and body['target_label'] in LABELS, 'SAVE_REPAIR_ORIGINAL_SCOPE_REQUIRED')
    from modules.miaoshou.tiktok_v4_drafts import EXPECTED_SHOP_ID_BY_TARGET
    need(type(body['detail_id']) is str and re.fullmatch('[1-9][0-9]*',body['detail_id']) is not None
         and body['shop_id']==EXPECTED_SHOP_ID_BY_TARGET[body['target_label']], 'SAVE_REPAIR_DRAFT_SHOP_CHANGED')
    need((body['extra_save_maximum'],body['base_target_maximum'],body['combined_target_maximum'])==(1,3,4)
         and all(type(body[k]) is int for k in ('extra_save_maximum','base_target_maximum','combined_target_maximum')),
         'SAVE_REPAIR_FIXED_EXTRA_SAVE_REQUIRED')
    for key in ('scope_key','failure_evidence_digest','checkpoint_events_digest','original_reservations_digest'):
        need(type(body[key]) is str and re.fullmatch('[0-9a-f]{64}',body[key]) is not None,'SAVE_REPAIR_DIGEST_REQUIRED')
    for key in ('checkpoint_ref','root_addendum_ref'):
        ref=body[key]
        need(type(ref) is dict and set(ref)=={'path','sha256'} and re.fullmatch('[0-9a-f]{64}',ref['sha256'] or '') is not None,
             'SAVE_REPAIR_FROZEN_EVIDENCE_REQUIRED')


def _read_reference(ref):
    path=Path(ref['path'])
    need(path.is_absolute() and '..' not in path.parts and not str(path).startswith(('\\\\','//')),
         'SAVE_REPAIR_LOCAL_PIN_REQUIRED')
    for part in (path,*path.parents):
        info=part.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,
             'SAVE_REPAIR_ORDINARY_PIN_REQUIRED')
    before=path.stat();raw=path.read_bytes();after=path.stat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=2_000_000
         and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns)
         and hashlib.sha256(raw).hexdigest()==ref['sha256'],'SAVE_REPAIR_FROZEN_PIN_CHANGED')
    return raw


def validate_root_addendum(body):
    root=json.loads(_read_reference(body['root_addendum_ref']))
    core={k:v for k,v in body.items() if k!='root_addendum_ref'}
    need(root.get('schema')=='root-known-rejected-save-repair-addendum/v1'
         and root.get('status')=='LIVE_ADOPTION_APPROVED'
         and root.get('purpose')=='EXTRA_ONE_SAVE_ONLY_RETAIN_ORIGINAL_PRODUCT_DECISION'
         and (root.get('plan_id'),root.get('run_id'),root.get('decision_id'),root.get('snapshot_digest'))==(PLAN,RUN,DECISION,SNAPSHOT)
         and root.get('extra_save_maximum')==1 and type(root.get('extra_save_maximum')) is int
         and type(root.get('target_labels')) is list and len(root['target_labels'])==len(set(root['target_labels']))
         and set(root['target_labels']).issubset(LABELS) and body['target_label'] in root['target_labels']
         and _sha(_json(core)) in root.get('grant_core_digests',[]),'SAVE_REPAIR_LIVE_ROOT_ADDENDUM_REQUIRED')
    ref=root.get('source_pins_ref')
    need(type(ref) is dict and set(ref)=={'path','sha256'},'SAVE_REPAIR_REVIEWED_SOURCE_REQUIRED')
    pins=json.loads(_read_reference(ref));expected=pins.get('shared_platform/native_known_rejected_save_repair.py',{})
    need(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==expected.get('after_sha256'),
         'SAVE_REPAIR_REVIEWED_SOURCE_CHANGED')
    return root


def _base_rows(db, scope, label):
    return [dict(r) for r in db.execute('''SELECT r.sequence,r.target_label,r.budget_scope,r.operation,r.attempt_count,d.request_digest,
        coalesce(o.outcome,r.initial_outcome) AS outcome FROM native_final_budget_reservations r
        LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence)
        LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence)
        WHERE r.scope_key=? AND r.target_label=? ORDER BY r.sequence''',(scope,label))]


def validate_original(db, body, *, target_status, attempt):
    validate_body(body)
    scope=db.execute('SELECT * FROM native_final_budget_scopes WHERE scope_key=?',(body['scope_key'],)).fetchone()
    need(scope is not None,'SAVE_REPAIR_ORIGINAL_BUDGET_REQUIRED')
    binding=json.loads(scope['binding_json'])
    need(_sha(scope['binding_json'])==scope['binding_sha256'] and binding['plan_id']==PLAN
         and binding['snapshot_digest']==SNAPSHOT and binding['decision_id']==DECISION
         and binding['platform']=='TIKTOK' and binding['per_target_maximum']==3
         and body['target_label'] in binding['target_labels'], 'SAVE_REPAIR_FROZEN_BUDGET_CHANGED')
    rows=_base_rows(db,body['scope_key'],body['target_label'])
    need(rows==body['original_reservations'] and _sha(_json(rows))==body['original_reservations_digest']
         and [(r['budget_scope'],r['operation'],r['attempt_count'],r['outcome']) for r in rows]==[
             ('shared','create_platform_draft',1,'ACCEPTED'),('target','claim_draft',1,'ACCEPTED'),('target','save_draft',1,'REJECTED')]
         and all(re.fullmatch('[0-9a-f]{64}',r['request_digest'] or '') for r in rows), 'SAVE_REPAIR_ORIGINAL_RESERVATIONS_CHANGED')
    target=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(RUN,body['target_label'])).fetchone()
    need(target is not None and target['status']==target_status and target['attempts']==attempt
         and target['external_id'] is None,'SAVE_REPAIR_TARGET_NOT_EXACT_FAILED_ATTEMPT')
    run=db.execute('SELECT plan_id FROM release_runs WHERE run_id=?',(RUN,)).fetchone()
    need(run is not None and run['plan_id']==PLAN,'SAVE_REPAIR_RUN_CHANGED')
    for table in ('release_target_submissions','release_target_readbacks','release_target_repairs'):
        need(db.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND target_label=?',(RUN,body['target_label'])).fetchone() is None,
             'SAVE_REPAIR_SUBMITTED_OR_REPAIRED_NO_REPLAY')
    failures=db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',(RUN,body['target_label'])).fetchall()
    need(len(failures)==1 and failures[0]['attempt']==1 and failures[0]['evidence_digest']==body['failure_evidence_digest'],
         'SAVE_REPAIR_ORIGINAL_FAILURE_CHANGED')
    evidence=json.loads(failures[0]['evidence_json'])
    need(_sha(_json(evidence))==body['failure_evidence_digest'],'SAVE_REPAIR_FAILURE_DIGEST_CHANGED')
    validate_failure(evidence,body['target_label'])
    return evidence


def validate_checkpoint(body, checkpoint):
    from shared_platform.native_prepared_v4_targets import _file_identity
    expected=_file_identity(run_id=RUN,plan_id=PLAN,decision_id=DECISION,snapshot_digest=SNAPSHOT)['file_run_id']
    copied=dict(checkpoint); supplied=copied.pop('checkpoint_digest',None)
    need(supplied=='sha256:'+_sha(_json(copied)) and checkpoint.get('schema_version')=='tiktok-v4-draft-checkpoint/v1'
         and checkpoint.get('offer_id')=='4039931504' and checkpoint.get('snapshot_digest')==SNAPSHOT
         and checkpoint.get('run_id')==expected,'SAVE_REPAIR_ORIGINAL_CHECKPOINT_REQUIRED')
    events=[e for e in checkpoint['events'] if e.get('target_label')==body['target_label']]
    validate_events(events,body)


def intake_grant(db, body, checkpoint):
    """Explicit maintenance intake in its caller's single BEGIN IMMEDIATE.

    The maintenance command verifies all file pins and original schema before
    calling this; it is not invoked by GET, factory or constructor.
    """
    check_schema(db); validate_original(db,body,target_status='FAILED',attempt=1)
    validate_root_addendum(body)
    validate_checkpoint(body,checkpoint)
    payload=_json(body); grant_id=_sha(payload)
    db.execute('INSERT INTO native_save_repair_grants VALUES(?,?,?,?,?,?)',
        (grant_id,body['scope_key'],body['target_label'],RUN,payload,_sha(payload)))
    return grant_id


def _protected(active):
    from shared_platform.native_sole_final_execution import _NativeExecution,_ACTIVE
    need(type(active) is _NativeExecution and _ACTIVE.get() is active and active.plan_id==PLAN
         and active.decision_id==DECISION and active.service.store is active.store
         and active.service._actor()['owner_sid']==active.owner_sid,'SAVE_REPAIR_PROTECTED_ACTIVE_REQUIRED')
    from shared_platform.native_prepared_v4_targets import _scope
    _scope(active,active.store.get_plan(PLAN))


def _activate_grant(db, body, grant_id):
    # Same transaction as the held active decision check in the only caller.
    need(_sha(_json(body))==grant_id,'SAVE_REPAIR_GRANT_CHANGED')
    validate_original(db,body,target_status='FAILED',attempt=1)
    validate_root_addendum(body)
    db.execute('INSERT INTO native_save_repair_starts VALUES(?,2)',(grant_id,))
    changed=db.execute("UPDATE release_target_runs SET status='PENDING',error=NULL,completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=1",(RUN,body['target_label']))
    need(changed.rowcount==1,'SAVE_REPAIR_FAILED_TARGET_CAS_CHANGED')


def activate_failed_targets(active):
    """Called only after the original current gate, under original executor lock."""
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return
    _protected(active)
    store=active.store
    with store._connect_readonly() as db:
        if not check_schema(db,optional=True): return
        grants=[dict(r) for r in db.execute('SELECT * FROM native_save_repair_grants WHERE run_id=?',(RUN,))]
    for grant in grants:
        body=json.loads(grant['body_json'])
        need(_sha(grant['body_json'])==grant['body_sha256']==grant['grant_id'],'SAVE_REPAIR_GRANT_CHANGED')
        with store._transaction() as db:
            check_schema(db)
            if db.execute('SELECT 1 FROM native_save_repair_starts WHERE grant_id=?',(grant['grant_id'],)).fetchone(): continue
            active.service._decision(db,DECISION,owner_sid=active.owner_sid)
            store._require_active_run(db,RUN)
            _activate_grant(db,body,grant['grant_id'])
    # Original begin_target will atomically claim PENDING and create attempt 2.


def recovery_actions(active,run,actions):
    """Only the protected executor may project this started grant as runnable.

    Generic recovery correctly blocks PENDING rows with old write evidence.
    This explicit branch preserves that evidence and does not call them a
    zero-write retry or a pristine first attempt.
    """
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return actions
    _protected(active)
    need(run.get('run_id')==RUN,'SAVE_REPAIR_RUN_CHANGED')
    approved=set()
    with active.store._connect_readonly() as db:
        if not check_schema(db,optional=True):return actions
        for row in db.execute('''SELECT g.* FROM native_save_repair_grants g JOIN native_save_repair_starts s USING(grant_id)
            LEFT JOIN native_save_repair_reservations r USING(grant_id) WHERE g.run_id=? AND r.grant_id IS NULL''',(RUN,)):
            target=next((t for t in run['targets'] if t['target_label']==row['target_label']),None)
            if target is None or target.get('status')!='PENDING' or target.get('attempts')!=1:continue
            body=json.loads(row['body_json'])
            need(_sha(row['body_json'])==row['grant_id']==row['body_sha256'],'SAVE_REPAIR_GRANT_CHANGED')
            validate_original(db,body,target_status='PENDING',attempt=1);validate_root_addendum(body)
            approved.add(row['target_label'])
    return [{**action,'action_kind':'KNOWN_REJECTED_SAVE_COMPENSATION','runnable':True,
             'reason_code':'protected_single_extra_save_same_original_run'}
            if action.get('target_label') in approved else action for action in actions]


def for_active(active, base):
    if base.platform!='TIKTOK' or base.target_label not in LABELS: return base
    with base._connect(readonly=True) as db:
        if not check_schema(db,optional=True): return base
        grant=db.execute('SELECT * FROM native_save_repair_grants WHERE scope_key=? AND target_label=?',(base.scope_key,base.target_label)).fetchone()
        if grant is None:return base
        body=json.loads(grant['body_json'])
        need(_sha(grant['body_json'])==grant['body_sha256']==grant['grant_id'],'SAVE_REPAIR_GRANT_CHANGED')
        validate_original(db,body,target_status='RUNNING',attempt=2)
        validate_root_addendum(body)
        need(db.execute('SELECT 1 FROM native_save_repair_starts WHERE grant_id=?',(grant['grant_id'],)).fetchone() is not None,
             'SAVE_REPAIR_PROTECTED_START_REQUIRED')
    _protected(active)
    return _RepairLedger(base,dict(grant),body)


class _RepairLedger:
    """Private adapter tested on owned fixtures; only for_active exposes it LIVE."""
    def __init__(self,base,grant,body):
        self.base=base;self.grant=grant;self.body=body;self._prepared=None;self._reserved=False

    def __getattr__(self,key): return getattr(self.base,key)

    def reserve_shared(self,*args,**kwargs):
        raise NativeMutationBudgetHold('SAVE_REPAIR_NO_RECREATE')

    def _recheck(self,db=None):
        if self.base._recheck is not None:self.base._recheck(db)
        need(_json(self.body)==self.grant['body_json'] and _sha(self.grant['body_json'])==self.grant['grant_id']==self.grant['body_sha256'],
             'SAVE_REPAIR_LOCAL_GRANT_CHANGED')
        validate_root_addendum(self.body)
        if db is None:
            with self.base._connect(readonly=True) as current:self._check_persisted(current)
        else:self._check_persisted(db)

    def _check_persisted(self,db):
        row=db.execute('SELECT * FROM native_save_repair_grants WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
        need(row is not None and dict(row)==self.grant,'SAVE_REPAIR_PERSISTED_GRANT_CHANGED')

    def authorize_rejected_save(self,events,identity,request):
        self._recheck()
        need(self._prepared is None,'SAVE_REPAIR_PREPARE_ONCE')
        need(request.snapshot['snapshot_digest']==SNAPSHOT and request.snapshot['plan_id']==PLAN
             and request.run_id==self._checkpoint_run() and identity=={'target_label':self.body['target_label'],
             'detail_id':self.body['detail_id'],'shop_id':self.body['shop_id']},'SAVE_REPAIR_REQUEST_IDENTITY_CHANGED')
        validate_events(list(events),self.body)
        with self.base._connect(readonly=True) as db:
            validate_original(db,self.body,target_status='RUNNING',attempt=2)
            need(db.execute('SELECT 1 FROM native_save_repair_reservations WHERE grant_id=?',(self.grant['grant_id'],)).fetchone() is None,
                 'SAVE_REPAIR_GRANT_SPENT_OR_UNKNOWN_NO_REPLAY')
        return True

    def _checkpoint_run(self):
        from shared_platform.native_prepared_v4_targets import _file_identity
        return _file_identity(run_id=RUN,plan_id=PLAN,decision_id=DECISION,snapshot_digest=SNAPSHOT)['file_run_id']

    def bind_prepared_save(self,identity,prepared):
        need(self._prepared is None,'SAVE_REPAIR_PREPARE_ONCE')
        from modules.miaoshou.tiktok_v4_drafts import SAVE_SITE_DRAFT_PATH,SAVE_SHOP_DRAFT_PATH
        from shared_platform.product_publication_live_dependencies import _TIKTOK_SITE_BY_TARGET
        need(type(prepared) is dict and set(prepared)=={'path','body'} and prepared['path'] in {SAVE_SITE_DRAFT_PATH,SAVE_SHOP_DRAFT_PATH},'SAVE_REPAIR_PREPARED_SAVE_REQUIRED')
        body=prepared['body']; label=self.body['target_label']
        need(str(body.get('detailId'))==self.body['detail_id'] and type(body.get('ossMd5')) is str and bool(body['ossMd5'])
             and (prepared['path']==SAVE_SHOP_DRAFT_PATH and str(body.get('shopId'))==self.body['shop_id']
                  or prepared['path']==SAVE_SITE_DRAFT_PATH and body.get('site')==_TIKTOK_SITE_BY_TARGET[label]),
             'SAVE_REPAIR_FRESH_VERSION_OR_DRAFT_CHANGED')
        need(identity=={'target_label':label,'detail_id':self.body['detail_id'],'shop_id':self.body['shop_id']},'SAVE_REPAIR_DRAFT_SHOP_CHANGED')
        self._prepared=deepcopy(prepared)

    def reserve_target(self,label,operation,*,count=1):
        need(label==self.body['target_label'] and type(count) is int and count==1,'SAVE_REPAIR_TARGET_OR_COUNT_CHANGED')
        if operation!='save_draft':
            need(operation=='publish_target','SAVE_REPAIR_NO_RECLAIM')
            with self.base._connect(readonly=True) as db:
                known=db.execute('SELECT outcome FROM native_save_repair_outcomes WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
                verified=db.execute('SELECT 1 FROM native_save_repair_verifications WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
                need(known is not None and known['outcome']=='ACCEPTED' and verified is not None,'SAVE_REPAIR_POST_SAVE_EXACT_READ_REQUIRED')
            return self.base.reserve_target(label,operation,count=count)
        need(self._prepared is not None,'SAVE_REPAIR_FRESH_PREPARE_REQUIRED')
        self._recheck()
        with self.base._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._recheck(db);check_schema(db)
            validate_original(db,self.body,target_status='RUNNING',attempt=2)
            need(db.execute('SELECT 1 FROM native_save_repair_reservations WHERE grant_id=?',(self.grant['grant_id'],)).fetchone() is None,
                 'SAVE_REPAIR_GRANT_SPENT_OR_UNKNOWN_NO_REPLAY')
            db.execute("INSERT INTO native_save_repair_reservations VALUES(?,'save_draft',1)",(self.grant['grant_id'],));db.commit()
        self._reserved=True
        return {'scope':'repair','operation':'save_draft','attempt_count':1,'outcome':'UNKNOWN','grant_id':self.grant['grant_id']}

    def dispatch_reserved(self,operation,call,*args,**kwargs):
        if operation!='save_draft':
            need(operation=='publish_target','SAVE_REPAIR_NO_RECLAIM')
            return self.base.dispatch_reserved(operation,call,*args,**kwargs)
        need(self._reserved and len(args)==2 and not kwargs and args==(self._prepared['path'],self._prepared['body']),
             'SAVE_REPAIR_EXACT_PREPARED_WIRE_REQUIRED')
        self._recheck()
        with self.base._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._recheck(db);check_schema(db)
            validate_original(db,self.body,target_status='RUNNING',attempt=2)
            need(db.execute('SELECT 1 FROM native_save_repair_dispatches WHERE grant_id=?',(self.grant['grant_id'],)).fetchone() is None,
                 'SAVE_REPAIR_DISPATCH_ONCE')
            db.execute('INSERT INTO native_save_repair_dispatches VALUES(?,?)',(self.grant['grant_id'],_request_sha(operation,args,kwargs)));db.commit()
        return call(*args,**kwargs)

    def wrap_post(self,post,*,provider):
        # Use the original finite endpoint classifier and typed rejection hook,
        # with this object's reserve/dispatch/complete implementations.
        from shared_platform.native_final_mutation_budget import _DurableLedger
        return _DurableLedger.wrap_post(self,post,provider=provider)

    def complete_reserved(self,operation,outcome):
        if operation!='save_draft':return self.base.complete_reserved(operation,outcome)
        need(self._reserved and outcome in {'ACCEPTED','REJECTED'},'SAVE_REPAIR_KNOWN_OUTCOME_REQUIRED')
        self._recheck()
        with self.base._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._recheck(db)
            prior=db.execute('SELECT outcome FROM native_save_repair_outcomes WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
            need(prior is None or prior['outcome']==outcome,'SAVE_REPAIR_OUTCOME_CONFLICT')
            if prior is None:db.execute('INSERT INTO native_save_repair_outcomes VALUES(?,?)',(self.grant['grant_id'],outcome))
            db.commit()

    def retain_post_save_match(self,target,draft,matched):
        need(matched is True and target.get('target_label')==self.body['target_label']
             and str(target.get('detail_id'))==self.body['detail_id'] and str(target.get('shop_id'))==self.body['shop_id']
             and len(target.get('expected_images') or [])==8,'SAVE_REPAIR_POST_SAVE_EXACT_READ_REQUIRED')
        self._recheck()
        with self.base._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._recheck(db)
            result=db.execute('SELECT outcome FROM native_save_repair_outcomes WHERE grant_id=?',(self.grant['grant_id'],)).fetchone()
            need(result is not None and result['outcome']=='ACCEPTED','SAVE_REPAIR_ACCEPTED_SAVE_REQUIRED')
            raw=_json({'target':target,'official_draft':draft})
            if db.execute('SELECT 1 FROM native_save_repair_verifications WHERE grant_id=?',(self.grant['grant_id'],)).fetchone() is None:
                db.execute('INSERT INTO native_save_repair_verifications VALUES(?,?)',(self.grant['grant_id'],_sha(raw)))
            db.commit()

    def snapshot(self):
        return self.base.snapshot()

    @property
    def total_attempt_count(self):return self.snapshot()['attempts']['total']
    @property
    def reservations(self):return self.snapshot()['reservations']
    @property
    def target_attempt_counts(self):return self.snapshot()['attempts']['per_target']
    def assert_confirmed_writes_within_attempts(self,count):
        need(type(count) is int and 0<=count<=self.total_attempt_count,'SAVE_REPAIR_CONFIRMED_WRITES_EXCEED_ALL_ATTEMPTS')


def include_repair_attempts(base,value):
    """Platform totals include every repair, even on another pending target.

    Original cap checking and immutable base rows run before this projection.
    No grant schema on an ordinary store means exactly the old projection.
    """
    with base._connect(readonly=True) as db:
        if not check_schema(db,optional=True):return value
        rows=db.execute('''SELECT g.grant_id,g.target_label,r.attempt_count,coalesce(o.outcome,'UNKNOWN') AS outcome
            FROM native_save_repair_grants g JOIN native_save_repair_reservations r USING(grant_id)
            LEFT JOIN native_save_repair_outcomes o USING(grant_id) WHERE g.scope_key=?''',(base.scope_key,)).fetchall()
    value['base_attempts']=deepcopy(value['attempts'])
    value['repair_attempts']={'per_target':{label:0 for label in base.target_labels},'total':0}
    for row in rows:
        label=row['target_label'];value['attempts']['per_target'][label]+=row['attempt_count'];value['attempts']['total']+=row['attempt_count']
        value['repair_attempts']['per_target'][label]+=row['attempt_count'];value['repair_attempts']['total']+=row['attempt_count']
        value['reservations'].append({'sequence':'repair:'+row['grant_id'],'scope':'repair','target_label':label,
            'mutation_target_label':label,'operation':'save_draft','attempt_count':row['attempt_count'],'outcome':row['outcome']})
    value['repair_limits']={'base_target_maximum':3,'extra_save_maximum':1,'combined_target_maximum':4}
    return value
