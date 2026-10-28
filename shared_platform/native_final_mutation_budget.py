"""Persistent attempt ceilings; never a human approval or provider authority.

Only the protected native factory builds this ledger. Schema is a separately
reviewed maintenance artifact: constructors/GETs never create or upgrade it.
"""
from collections.abc import Mapping
import hashlib
import json
import sqlite3
from pathlib import Path
import re
from dataclasses import is_dataclass,asdict

from shared_platform.publication_write_budget import PublicationWriteBudgetExceeded, _operation, _nonnegative_int

SCHEMA_SQL = '''
CREATE TABLE native_final_budget_scopes (
 scope_key TEXT PRIMARY KEY, plan_id TEXT NOT NULL, snapshot_digest TEXT NOT NULL,
 platform TEXT NOT NULL, binding_json TEXT NOT NULL, binding_sha256 TEXT NOT NULL,
 UNIQUE(plan_id,snapshot_digest,platform));
CREATE TABLE native_final_budget_reservations (
 scope_key TEXT NOT NULL, sequence INTEGER NOT NULL, target_label TEXT NOT NULL,
 budget_scope TEXT NOT NULL CHECK(budget_scope IN ('shared','target')),
 operation TEXT NOT NULL, attempt_count INTEGER NOT NULL CHECK(attempt_count>0),
 initial_outcome TEXT NOT NULL CHECK(initial_outcome='UNKNOWN'),
 PRIMARY KEY(scope_key,sequence), FOREIGN KEY(scope_key) REFERENCES native_final_budget_scopes(scope_key));
CREATE TABLE native_final_budget_dispatches (
 scope_key TEXT NOT NULL, sequence INTEGER NOT NULL, request_digest TEXT NOT NULL,
 PRIMARY KEY(scope_key,sequence), FOREIGN KEY(scope_key,sequence) REFERENCES native_final_budget_reservations(scope_key,sequence));
CREATE TABLE native_final_budget_outcomes (
 scope_key TEXT NOT NULL, sequence INTEGER NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('ACCEPTED','REJECTED')),
 PRIMARY KEY(scope_key,sequence), FOREIGN KEY(scope_key,sequence) REFERENCES native_final_budget_dispatches(scope_key,sequence));
'''
for _table in ('scopes','reservations','dispatches','outcomes'):
    for _event in ('UPDATE','DELETE'):
        SCHEMA_SQL += "CREATE TRIGGER native_final_budget_"+_table+"_no_"+_event.lower()+" BEFORE "+_event+" ON native_final_budget_"+_table+" BEGIN SELECT RAISE(ABORT,'immutable native budget record'); END;\n"
_EXPECTED_SCHEMA={};_statement=''
for _line in SCHEMA_SQL.splitlines(keepends=True):
    _statement+=_line
    if sqlite3.complete_statement(_statement):
        _match=re.search(r'CREATE (TABLE|TRIGGER) (\w+)',_statement)
        _EXPECTED_SCHEMA[_match.group(2)]=re.sub(r'\s+',' ',_statement.strip().rstrip(';')).lower()
        _statement=''

class NativeMutationBudgetHold(PublicationWriteBudgetExceeded):
    pass

def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def _request_sha(operation,args,kwargs):
    def bounded(value):
        if isinstance(value,(bytes,bytearray)):
            return {'binary_sha256':hashlib.sha256(value).hexdigest(),'length':len(value)}
        if is_dataclass(value) and not isinstance(value,type):return asdict(value)
        raise TypeError('canonical mutation request identity must be JSON, dataclass or bytes')
    return _sha(json.dumps({'operation':operation,'args':args,'kwargs':kwargs},ensure_ascii=False,
                          sort_keys=True,separators=(',',':'),allow_nan=False,default=bounded))

class _DurableLedger:
    """Private storage engine; fixture calls do not prove protected LIVE admission."""
    def __init__(self, *, store, binding, target_label, recheck=None):
        from shared_platform.release_store import ReleaseStore
        if type(store) is not ReleaseStore or not store.path.is_file():
            raise NativeMutationBudgetHold('NATIVE_BUDGET_EXISTING_STORE_REQUIRED')
        if target_label not in binding['target_labels']:
            raise NativeMutationBudgetHold('NATIVE_BUDGET_TARGET_CHANGED')
        self.store=store;self.binding=json.loads(_json(binding));self.target_label=target_label
        self.platform=binding['platform'];self.target_labels=tuple(binding['target_labels'])
        self.shared_maximum=_nonnegative_int(binding['shared_maximum'],'shared_maximum')
        self.per_target_maximum=_nonnegative_int(binding['per_target_maximum'],'per_target_maximum')
        self.scope_key=_sha(_json({'plan_id':binding['plan_id'],'snapshot_digest':binding['snapshot_digest'],'platform':self.platform}))
        self._binding_json=_json(binding);self._pending={};self._recheck=recheck
        # Read-only schema/binding check; no initialization write.
        with self._connect(readonly=True) as db:
            self._check_schema(db);self._binding(db,create=False)

    def _connect(self, *, readonly=False):
        path=self.store.path.resolve(strict=True)
        db=sqlite3.connect(path.as_uri()+('?mode=ro' if readonly else '?mode=rw'),uri=True,timeout=15)
        db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
        if readonly:db.execute('PRAGMA query_only=ON')
        return db

    def _check_schema(self,db):
        for name,expected in _EXPECTED_SCHEMA.items():
            row=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
            if row is None:
                raise NativeMutationBudgetHold('NATIVE_BUDGET_SCHEMA_NOT_INSTALLED')
            if re.sub(r'\s+',' ',row['sql'].strip().rstrip(';')).lower()!=expected:
                raise NativeMutationBudgetHold('NATIVE_BUDGET_APPEND_ONLY_SCHEMA_REQUIRED')

    def _binding(self,db,*,create):
        if (_json(self.binding)!=self._binding_json or self.shared_maximum!=self.binding['shared_maximum']
                or self.per_target_maximum!=self.binding['per_target_maximum'] or self.target_labels!=tuple(self.binding['target_labels'])):
            raise NativeMutationBudgetHold('NATIVE_BUDGET_LOCAL_LIMITS_CHANGED')
        row=db.execute('SELECT binding_json,binding_sha256 FROM native_final_budget_scopes WHERE scope_key=?',(self.scope_key,)).fetchone()
        if row is not None:
            if row['binding_json']!=self._binding_json or row['binding_sha256']!=_sha(self._binding_json):
                raise NativeMutationBudgetHold('NATIVE_BUDGET_FROZEN_BINDING_CHANGED')
        elif create:
            db.execute('INSERT INTO native_final_budget_scopes VALUES(?,?,?,?,?,?)',(self.scope_key,self.binding['plan_id'],self.binding['snapshot_digest'],self.platform,self._binding_json,_sha(self._binding_json)))

    def _rows(self,db):
        return db.execute('SELECT r.*,d.request_digest,coalesce(o.outcome,r.initial_outcome) AS outcome FROM native_final_budget_reservations r LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence) LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence) WHERE r.scope_key=? ORDER BY r.sequence',(self.scope_key,)).fetchall()

    def snapshot(self):
        with self._connect(readonly=True) as db:
            db.execute('BEGIN');self._check_schema(db);self._binding(db,create=False);rows=self._rows(db)
        shared=sum(r['attempt_count'] for r in rows if r['budget_scope']=='shared')
        targets={label:sum(r['attempt_count'] for r in rows if r['budget_scope']=='target' and r['target_label']==label) for label in self.target_labels}
        if shared>self.shared_maximum or any(x>self.per_target_maximum for x in targets.values()):
            raise NativeMutationBudgetHold('NATIVE_BUDGET_PERSISTED_COUNT_EXCEEDS_LIMIT')
        value={'schema_version':'publication-mutation-budget/v1','platform':self.platform,
            'limits':{'shared_maximum':self.shared_maximum,'per_target_maximum':self.per_target_maximum},
            'attempts':{'shared':shared,'per_target':targets,'total':shared+sum(targets.values())},
            'reservations':[{'sequence':r['sequence'],'scope':r['budget_scope'],'target_label':r['target_label'] if r['budget_scope']=='target' else None,
                'mutation_target_label':r['target_label'],'operation':r['operation'],'attempt_count':r['attempt_count'],'outcome':r['outcome']} for r in rows],
            'durable_scope':{'plan_id':self.binding['plan_id'],'snapshot_digest':self.binding['snapshot_digest'],'scope_key':self.scope_key},'execution_authority':False}
        from shared_platform.native_known_rejected_save_repair import include_repair_attempts
        value=include_repair_attempts(self,value)
        from shared_platform.native_accepted_save_core_repair import include_attempts
        return include_attempts(self,value)

    @property
    def shared_attempt_count(self):return self.snapshot()['attempts']['shared']
    @property
    def target_attempt_counts(self):return self.snapshot()['attempts']['per_target']
    @property
    def total_attempt_count(self):return self.snapshot()['attempts']['total']
    @property
    def reservations(self):return self.snapshot()['reservations']

    def _reserve(self,scope,operation,count):
        operation=_operation(operation);count=_nonnegative_int(count,'count')
        if count==0:raise ValueError('count must be positive')
        if self._recheck is not None:self._recheck()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check_schema(db);self._binding(db,create=True);rows=self._rows(db)
            if self._recheck is not None:self._recheck(db)
            if any(r['target_label']==self.target_label and r['operation']==operation and r['outcome']=='UNKNOWN' for r in rows):
                raise NativeMutationBudgetHold('NATIVE_BUDGET_UNKNOWN_OPERATION_NO_REDISPATCH')
            used=sum(r['attempt_count'] for r in rows if r['budget_scope']==scope and (scope=='shared' or r['target_label']==self.target_label))
            ceiling=self.shared_maximum if scope=='shared' else self.per_target_maximum
            if used+count>ceiling:raise PublicationWriteBudgetExceeded('native '+self.platform+' '+scope+' mutation attempt budget exceeded')
            sequence=1+max((r['sequence'] for r in rows),default=0)
            db.execute('INSERT INTO native_final_budget_reservations VALUES(?,?,?,?,?,?,?)',(self.scope_key,sequence,self.target_label,scope,operation,count,'UNKNOWN'))
            db.commit() # UNKNOWN durable before caller may touch a provider.
        record={'sequence':sequence,'scope':scope,'target_label':self.target_label if scope=='target' else None,'operation':operation,'attempt_count':count,'outcome':'UNKNOWN'}
        self._pending[operation]=record
        return dict(record)

    def reserve_shared(self,operation,*,count=1):return self._reserve('shared',operation,count)
    def reserve_target(self,target_label,operation,*,count=1):
        if target_label!=self.target_label:raise NativeMutationBudgetHold('NATIVE_BUDGET_CURRENT_TARGET_CHANGED')
        return self._reserve('target',operation,count)

    def dispatch_reserved(self,operation,call,*args,**kwargs):
        """Use the preceding canonical reservation once, without a second count.

        The LIVE wrapper calls reserve_shared/target first. No implicit scope
        inference; readonly POST never calls this helper. The original canonical
        parser explicitly calls complete_reserved after validating its result.
        """
        operation=_operation(operation);record=self._pending.get(operation)
        if record is None:raise NativeMutationBudgetHold('NATIVE_BUDGET_RESERVATION_REQUIRED')
        request_digest=_request_sha(operation,args,kwargs)
        if self._recheck is not None:self._recheck()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check_schema(db);self._binding(db,create=False)
            if self._recheck is not None:self._recheck(db)
            rows=self._rows(db);current=next((r for r in rows if r['sequence']==record['sequence']),None)
            if current is None or current['request_digest'] is not None:
                raise NativeMutationBudgetHold('NATIVE_BUDGET_RESERVATION_ALREADY_DISPATCHED')
            # Draft cloning is deliberately one identity per selected TikTok
            # target. Other shared requests (e.g. Shopee GLOBAL) are one common
            # effect even when the next regional object is reconstructed.
            shared_effect=current['budget_scope']=='shared' and operation!='create_platform_draft'
            prior=[r for r in rows if r['operation']==operation and r['request_digest']==request_digest
                   and (shared_effect or r['target_label']==self.target_label)]
            if any(r['outcome'] in {'UNKNOWN','ACCEPTED'} for r in prior):
                raise NativeMutationBudgetHold('NATIVE_BUDGET_REQUEST_NO_REDISPATCH')
            db.execute('INSERT INTO native_final_budget_dispatches VALUES(?,?,?)',(self.scope_key,record['sequence'],request_digest));db.commit()
        # Exceptions leave the durable reservation UNKNOWN and propagate intact.
        value=call(*args,**kwargs)
        return value

    def wrap_post(self,post,*,provider):
        """Finite READ/mutation seams; unknown endpoints never bypass accounting."""
        maps={
          'miaoshou':{
           '/open/v1/product/common_collect_box/common_collect_box/claimed':'create_platform_draft',
           '/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop':'claim_draft',
           '/open/v1/product/collect_box/tiktok/collect_box/save_site_collect_item_info':'save_draft',
           '/open/v1/product/collect_box/tiktok/collect_box/save_shop_collect_item_info':'save_draft',
           '/open/v1/product/collect_box/tiktok/collect_box/save_move_collect_task':'publish_target'},
          'ozon':{'/v3/product/import':'import_variant','/v2/products/stocks':'update_stock'}}
        reads={
          'miaoshou':{
            '/open/v1/product/shop/shop/get_shop_list',
            '/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list',
            '/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail',
            '/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list',
            '/open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info',
            '/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info',
            '/open/v1/product/collect_box/tiktok/collect_box/get_category_metadata',
            '/open/v1/product/collect_box/tiktok/collect_box/get_category_tree_by_site',
            '/open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list'},
          'ozon':{'/v1/description-category/tree','/v1/description-category/attribute',
            '/v1/description-category/attribute/values/search','/v2/warehouse/list',
            '/v3/product/info/list','/v4/product/info/attributes','/v1/product/info/description',
            '/v2/product/info/stocks-by-warehouse/fbs','/v1/product/import/info'}}
        if provider not in maps or not callable(post):raise NativeMutationBudgetHold('NATIVE_BUDGET_PROVIDER_SEAM_UNKNOWN')
        def wrapped(endpoint,body,*args,**kwargs):
            if endpoint in reads[provider]:return post(endpoint,body,*args,**kwargs)
            if endpoint not in maps[provider]:raise NativeMutationBudgetHold('NATIVE_BUDGET_ENDPOINT_UNCLASSIFIED')
            operation=maps[provider][endpoint]
            try:value=self.dispatch_reserved(operation,post,endpoint,body,*args,**kwargs)
            except Exception as error:
                if provider=='miaoshou':
                    from modules.miaoshou.client import MiaoshouBusinessRejectedError
                    if type(error) is MiaoshouBusinessRejectedError:self.complete_reserved(operation,'REJECTED')
                raise
            # Raw JSON cannot certify a canonical business result. The original
            # typed transport/fact observer explicitly calls complete_reserved.
            return value
        return wrapped

    def complete_reserved(self,operation,outcome):
        """The protected adapter supplies its canonical parsed business outcome.
        No response body, token, credential or provider wire is persisted.
        """
        operation=_operation(operation);record=self._pending.get(operation)
        if record is None or outcome not in {'ACCEPTED','REJECTED'}:
            raise NativeMutationBudgetHold('NATIVE_BUDGET_KNOWN_OUTCOME_REQUIRED')
        if self._recheck is not None:self._recheck()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');self._check_schema(db);self._binding(db,create=False)
            if self._recheck is not None:self._recheck(db)
            prior=db.execute('SELECT outcome FROM native_final_budget_outcomes WHERE scope_key=? AND sequence=?',(self.scope_key,record['sequence'])).fetchone()
            if prior:
                if prior['outcome']!=outcome:raise NativeMutationBudgetHold('NATIVE_BUDGET_OUTCOME_CONFLICT')
            else:db.execute('INSERT INTO native_final_budget_outcomes VALUES(?,?,?)',(self.scope_key,record['sequence'],outcome))
            db.commit()

    def assert_confirmed_writes_within_attempts(self,confirmed_write_count):
        if _nonnegative_int(confirmed_write_count,'confirmed_write_count')>self.total_attempt_count:
            raise PublicationWriteBudgetExceeded('confirmed writes exceed reserved mutation attempts')

def build_native_budget_ledger(active,*,platform,target_label,snapshot,candidate):
    from shared_platform.native_sole_final_execution import _NativeExecution,_ACTIVE
    from shared_platform.release_store import ReleaseStore
    if type(active) is not _NativeExecution or _ACTIVE.get() is not active or type(active.store) is not ReleaseStore:
        raise NativeMutationBudgetHold('NATIVE_BUDGET_PROTECTED_FACTORY_REQUIRED')
    adopted=getattr(active.runtime._operations,'agent_prepared_common',None)
    adopted_targets=None
    if adopted is not None:
        from shared_platform.agent_prepared_v4_targets import _scope
        bound,task,record=_scope(active,active.store.get_plan(active.plan_id))
        from shared_platform.prepared_target_consumer import effective_marketplace_targets
        adopted_targets=effective_marketplace_targets(record['offer_id'],record['targets'])
        if snapshot['offer_id']!=record['offer_id']:raise NativeMutationBudgetHold('ADOPTION_BUDGET_OFFER_CHANGED')
    if (adopted is None and snapshot['offer_id']!='4039931504') or candidate.get('schema_version')!='publication-release-candidate/v1' or candidate.get('status')!='READY_FOR_FINAL_REVIEW':
        raise NativeMutationBudgetHold('NATIVE_BUDGET_EXACT_FINAL_CANDIDATE_REQUIRED')
    with active.store._connect_readonly() as db:
        db.execute('BEGIN');receipt=active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        plan=db.execute('SELECT payload_json,payload_digest,target_labels_json FROM release_plans WHERE plan_id=?',(active.plan_id,)).fetchone()
        frozen=db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?',(active.plan_id,)).fetchone()
        decision=db.execute('SELECT review_json,plan_id FROM native_sole_final_decisions WHERE decision_id=?',(active.decision_id,)).fetchone()
        if plan is None or frozen is None or decision['plan_id']!=active.plan_id or snapshot!=json.loads(frozen['snapshot_json']) or receipt['snapshot_digest']!=snapshot['snapshot_digest']:
            raise NativeMutationBudgetHold('NATIVE_BUDGET_APPROVED_SNAPSHOT_CHANGED')
        _snapshot_candidate_identity(snapshot,candidate)
        payload=json.loads(plan['payload_json']);material=payload['r3_marketplace_binding']['reviewed_candidate_facts']
        review=json.loads(decision['review_json'])
        if (candidate['plan_id']!=active.plan_id or candidate['candidate_digest']!=review['candidate_digest']
                or candidate['write_budget']!=material['write_budget'] or candidate['target_labels']!=json.loads(plan['target_labels_json'])
                or (sorted(candidate['target_labels'])!=sorted(adopted_targets) if adopted_targets is not None else len(candidate['target_labels'])!=15)
                or 'miaoshou:COMMON' in candidate['target_labels']):
            raise NativeMutationBudgetHold('NATIVE_BUDGET_FROZEN_CANDIDATE_CHANGED')
    budget=candidate['write_budget'][platform]
    labels=budget['target_labels']
    if labels!=[label for label in candidate['target_labels'] if label.split(':')[0].upper()==platform] or target_label not in labels:
        raise NativeMutationBudgetHold('NATIVE_BUDGET_PLATFORM_TARGET_CHANGED')
    binding={'plan_id':active.plan_id,'snapshot_digest':snapshot['snapshot_digest'],'platform':platform,'target_labels':labels,
        'shared_maximum':budget['shared_maximum'],'per_target_maximum':budget['per_target_maximum'],
        'payload_digest':plan['payload_digest'],'candidate_digest':candidate['candidate_digest'],'decision_id':active.decision_id}
    captured_store=active.store
    captured_path=captured_store.path.resolve(strict=True)
    def recheck(db=None):
        if (_ACTIVE.get() is not active or active.store is not captured_store or active.service.store is not captured_store
                or active.store.path.resolve(strict=True)!=captured_path
                or active.plan_id!=binding['plan_id'] or active.service._actor()['owner_sid']!=active.owner_sid):
            raise NativeMutationBudgetHold('NATIVE_BUDGET_ACTIVE_OR_ACTOR_CHANGED')
        if db is None:return
        active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        current=db.execute('SELECT payload_digest,payload_json FROM release_plans WHERE plan_id=?',(active.plan_id,)).fetchone()
        target=db.execute('SELECT t.status FROM release_target_runs t JOIN release_runs r ON r.run_id=t.run_id WHERE r.plan_id=? AND t.target_label=?',(active.plan_id,target_label)).fetchall()
        if (current is None or current['payload_digest']!=binding['payload_digest']
                or json.loads(current['payload_json'])['r3_marketplace_binding']['reviewed_candidate_facts']['write_budget'][platform]!=budget
                or len(target)!=1 or target[0]['status']!='RUNNING'):
            raise NativeMutationBudgetHold('NATIVE_BUDGET_PLAN_OR_RUNNING_TARGET_CHANGED')
    base=_DurableLedger(store=active.store,binding=binding,target_label=target_label,recheck=recheck)
    if adopted is not None:return base
    from shared_platform.native_accepted_save_core_repair import for_active as accepted_core_for_active
    scoped=accepted_core_for_active(active,base)
    if scoped is not base:return scoped
    from shared_platform.native_known_rejected_save_repair import for_active
    return for_active(active,base)

def _snapshot_candidate_identity(snapshot,candidate):
    """Business identity excludes approval metadata; execution digest keeps it."""
    from shared_platform.publication_autopilot import _business_snapshot_digest
    business=_business_snapshot_digest(snapshot)
    if (candidate.get('snapshot_digest')!=business
            or candidate.get('business_snapshot_digest',business)!=business
            or candidate.get('approved_execution_snapshot_digest',snapshot['snapshot_digest'])!=snapshot['snapshot_digest']):
        raise NativeMutationBudgetHold('NATIVE_BUDGET_BUSINESS_OR_EXECUTION_IDENTITY_CHANGED')
    return business
