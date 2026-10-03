"""Same-owner sole marketplace decision over a real native completed graph.

No startup migration, HTTP bootstrap, private authority, worker or provider EDIT.
The explicit prepare/decide operations request only the existing signed READ;
GET consumers must not call them. Approval is still inert until execution recheck.
"""
from dataclasses import asdict
from hashlib import sha256
from contextlib import contextmanager
import json
import secrets
import time

from shared_platform.release_store import ReleaseStore, PLAN_PENDING_APPROVAL, PLAN_APPROVED
from shared_platform.final_review_server_admission import FrozenReview, ApprovalBlocked, compare_before_execution
from shared_platform.r3_native_final_approval import read_native_actor_identity
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
from shared_platform import native_common_technical_execution as technical
from shared_platform.native_common_retained_completion import read_retained_completion
from shared_platform.native_common_budget_facts import census_same_snapshot

TABLES = (
    """CREATE TABLE native_sole_final_nonces (
        nonce_digest TEXT PRIMARY KEY, review_digest TEXT NOT NULL,
        review_json TEXT NOT NULL, review_sha256 TEXT NOT NULL,
        plan_id TEXT NOT NULL, owner_sid TEXT NOT NULL, session_id TEXT NOT NULL,
        instance_id TEXT NOT NULL, expires_at_epoch INTEGER NOT NULL,
        current_read_json TEXT NOT NULL, current_read_sha256 TEXT NOT NULL,
        decision_id TEXT, FOREIGN KEY(plan_id) REFERENCES release_plans(plan_id),
        FOREIGN KEY(decision_id) REFERENCES native_sole_final_decisions(decision_id))""",
    """CREATE TABLE native_sole_final_decisions (
        decision_id TEXT PRIMARY KEY, review_digest TEXT NOT NULL UNIQUE,
        review_json TEXT NOT NULL, owner_sid TEXT NOT NULL,
        plan_id TEXT NOT NULL UNIQUE, approval_id TEXT NOT NULL UNIQUE,
        snapshot_digest TEXT NOT NULL, created_at_epoch INTEGER NOT NULL,
        FOREIGN KEY(plan_id) REFERENCES release_plans(plan_id),
        FOREIGN KEY(approval_id) REFERENCES release_approvals(approval_id))""",
)
TRIGGERS = (
    "CREATE TRIGGER native_sole_final_decisions_no_update BEFORE UPDATE ON native_sole_final_decisions BEGIN SELECT RAISE(ABORT,'immutable native final decision'); END",
    "CREATE TRIGGER native_sole_final_decisions_no_delete BEFORE DELETE ON native_sole_final_decisions BEGIN SELECT RAISE(ABORT,'immutable native final decision'); END",
    "CREATE TRIGGER native_sole_final_nonces_no_delete BEFORE DELETE ON native_sole_final_nonces BEGIN SELECT RAISE(ABORT,'immutable native final nonce'); END",
    """CREATE TRIGGER native_sole_final_nonces_consume_only BEFORE UPDATE ON native_sole_final_nonces
        WHEN NEW.nonce_digest IS NOT OLD.nonce_digest OR NEW.review_digest IS NOT OLD.review_digest
        OR NEW.review_json IS NOT OLD.review_json OR NEW.review_sha256 IS NOT OLD.review_sha256
        OR NEW.plan_id IS NOT OLD.plan_id OR NEW.owner_sid IS NOT OLD.owner_sid
        OR NEW.session_id IS NOT OLD.session_id OR NEW.instance_id IS NOT OLD.instance_id
        OR NEW.expires_at_epoch IS NOT OLD.expires_at_epoch OR OLD.decision_id IS NOT NULL
        OR NEW.current_read_json IS NOT OLD.current_read_json
        OR NEW.current_read_sha256 IS NOT OLD.current_read_sha256
        OR NEW.decision_id IS NULL
        BEGIN SELECT RAISE(ABORT,'native final nonce consume only'); END""",
)

def _bytes(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()

def _hash(raw):return sha256(raw).hexdigest()

def _fail(code):raise ApprovalBlocked(code)

def check_schema(db):
    for statement in TABLES+TRIGGERS:
        kind,name=statement.split()[1:3]
        row=db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',(kind.lower(),name)).fetchone()
        if row is None or row['sql'].strip().rstrip(';')!=statement.strip().rstrip(';'):
            _fail('NATIVE_SOLE_FINAL_SCHEMA_NOT_INSTALLED')

def _frozen(raw):
    value=json.loads(raw);value['targets']=tuple(value['targets'])
    review=FrozenReview(**value)
    if _bytes(asdict(review)).decode()!=raw:_fail('NATIVE_SOLE_FINAL_REVIEW_NONCANONICAL')
    return review

class NativeSoleFinalDecisionService:
    """Inject the existing OS reader and trusted service store, never a request dict."""
    def __init__(self,store,actor_reader):
        if type(store) is not ReleaseStore:_fail('NATIVE_SOLE_FINAL_STORE_REQUIRED')
        self.store,self.actor_reader=store,actor_reader

    def _actor(self):
        actor=read_native_actor_identity(self.actor_reader)
        if (actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER'
                or actor.get('logical_profile')!='Kyle'
                or any(type(actor.get(k)) is not str or not actor[k]
                       for k in ('owner_sid','session_id','instance_id'))):
            _fail('NATIVE_SOLE_FINAL_OWNER_IDENTITY_UNVERIFIED')
        return actor

    @contextmanager
    def _transaction(self):
        # Existing path must already exist; this does not call initialize_schema.
        with technical._existing_transaction(self.store) as db:
            check_schema(db)
            yield db
            check_schema(db)

    def _candidate(self,db,plan_id):
        """Rebuild full source and current READ in the same existing snapshot."""
        from modules.products import server
        from shared_platform import native_common_edit_boundary as boundary
        from shared_platform.publication_common_standing_policy import inspect_service_policy
        reader=NativeCommonSourceReader(self.store)
        graph=read_stored_domain_graph(db,plan_id,native_reader=reader)
        row=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(graph.common_plan_id,)).fetchone()
        payload=json.loads(row['payload_json'])
        completion=read_retained_completion(reader,db,graph.common_plan_id,graph.common_run_id)
        run=db.execute('SELECT * FROM release_runs WHERE run_id=?',(graph.common_run_id,)).fetchone()
        credential=technical._binding(run)
        context=credential.get('native_account_context')
        if context is None:_fail('NATIVE_SOLE_FINAL_GENUINE_COMMON_CONTEXT_REQUIRED')
        census=census_same_snapshot(self.store,db,payload,reader.read_source_facts(db,graph.common_plan_id))
        if (census['status']!='LOCAL_PERSISTED_CENSUS' or census['unresolved_attempts']
                or census['local_observed_confirmed_writes']!=1
                or any(r.get('same_preparation_root') is True for r in census['unclassified_history'])):
            _fail('NATIVE_SOLE_FINAL_COMMON_BUDGET_UNVERIFIED')
        try:
            active=boundary.service_boundary()
        except boundary.CommonEditBlocked as error:
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_SERVICE_CONTEXT_MISSING') from error
        signing_reader=server._service_common_signing_context_reader(self.store)
        policy_reader=server._service_common_standing_policy_reader()
        if (active is None or signing_reader is None or policy_reader is None
                or signing_reader._config is not active._config or policy_reader._config is not active._config):
            _fail('NATIVE_SOLE_FINAL_SERVICE_CONTEXT_CHANGED')
        coverage=active.same_snapshot_coverage(self.store,db,graph.common_plan_id)
        policy=inspect_service_policy(policy_reader)
        if policy.status!='KNOWN_LOCAL_USER_INTENT' or policy.maximum_confirmed_writes!=1:
            _fail('NATIVE_SOLE_FINAL_STANDING_POLICY_UNKNOWN')
        current=signing_reader.read_current(db,graph.common_plan_id,common_run_id=graph.common_run_id)
        if (current.status!='CURRENT_SIGNED_COMMON_READ_ACCEPTED' or current.current_fields_match is not True
                or current.common_signing_context_digest!=context['common_signing_context_digest']
                or current.preparation_root_id!=completion.preparation_root_id
                or current.retained_readback_digest!=completion.readback_digest):
            _fail('NATIVE_SOLE_FINAL_CURRENT_COMMON_READ_UNVERIFIED')
        # Source hashes/config wrappers are installation facts, not write count,
        # business content or a reason to demand a second human review.
        budget={'scope':'IMMUTABLE_PREPARED_BASELINE','root_id':completion.preparation_root_id,
            'r1_account_digest':completion.account_identity_digest,
            'signing_context_digest':context['common_signing_context_digest'],
            'mutation_sha256':credential['mutation_sha256'],'maximum_confirmed_writes':1,
            'confirmed_writes':census['local_observed_confirmed_writes']}
        frozen=FrozenReview(graph.offer_id,graph.marketplace_plan_id,graph.candidate_digest,
            graph.critical_content_digest,graph.targets,graph.common_plan_id,graph.common_run_id,
            graph.common_readback_digest,'sha256:'+_hash(_bytes(budget)),graph.round1_digest,
            graph.round2_digest,'native-final:'+_hash(_bytes([graph.offer_id,plan_id])))
        if (type(current.current_observation_bytes) is not bytes
                or _hash(current.current_observation_bytes)!=current.current_comparison_digest):
            _fail('NATIVE_SOLE_FINAL_CURRENT_READ_BYTES_REQUIRED')
        return frozen,graph,{'current_common_read_digest':current.current_comparison_digest,
            'service_source_digest':coverage.registered_source_digest,
            'provider_edit_permission':'UNKNOWN','execution_authority':False},current.current_observation_bytes

    def prepare(self,plan_id):
        """Explicit final-review preparation, never called by a read-only GET."""
        actor=self._actor()
        with self._transaction() as db:
            review,graph,diagnostic,read_bytes=self._candidate(db,plan_id)
            plan=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(plan_id,)).fetchone()
            if plan['status']!=PLAN_PENDING_APPROVAL:_fail('NATIVE_SOLE_FINAL_PENDING_MARKET_REQUIRED')
            if self._actor()!=actor:_fail('NATIVE_SOLE_FINAL_OWNER_CHANGED_DURING_RECHECK')
            raw=_bytes(asdict(review));nonce=secrets.token_hex(32)
            db.execute('INSERT INTO native_sole_final_nonces VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)',
                (_hash(nonce.encode()),review.digest(),raw.decode(),_hash(raw),plan_id,
                 actor['owner_sid'],actor['session_id'],actor['instance_id'],int(time.time())+300,
                 read_bytes.decode(),_hash(read_bytes)))
            return {'review':asdict(review),'review_digest':review.digest(),'nonce':nonce,
                'display':json.loads(graph.display_bytes),'manifest':json.loads(graph.manifest_bytes),
                'technical_details':diagnostic,'execution_authority':False}

    def decide(self,*,nonce,review_digest):
        """One nonce CAS, native decision and original approved snapshot atomically."""
        actor=self._actor()
        if type(nonce) is not str or len(nonce)!=64 or type(review_digest) is not str:
            _fail('NATIVE_SOLE_FINAL_NONCE_INVALID')
        with self._transaction() as db:
            row=db.execute('SELECT * FROM native_sole_final_nonces WHERE nonce_digest=?',(_hash(nonce.encode()),)).fetchone()
            if (row is None or row['review_digest']!=review_digest
                    or row['owner_sid']!=actor['owner_sid']
                    or row['decision_id'] is None and any(row[k]!=actor[k] for k in ('session_id','instance_id'))
                    or _hash(row['review_json'].encode())!=row['review_sha256']
                    or _hash(row['current_read_json'].encode())!=row['current_read_sha256']):
                _fail('NATIVE_SOLE_FINAL_NONCE_BINDING_CHANGED')
            approved=_frozen(row['review_json'])
            if approved.digest()!=review_digest:_fail('NATIVE_SOLE_FINAL_REVIEW_BYTES_CHANGED')
            current,_,_,_=self._candidate(db,row['plan_id'])
            if compare_before_execution(approved,current).disposition!='MATCH_ONLY':
                _fail('NATIVE_SOLE_FINAL_REVIEW_CHANGED')
            if self._actor()!=actor:_fail('NATIVE_SOLE_FINAL_OWNER_CHANGED_DURING_RECHECK')
            if row['decision_id'] is not None:
                return self._decision(db,row['decision_id'],owner_sid=actor['owner_sid'])
            if int(time.time())>=row['expires_at_epoch']:_fail('NATIVE_SOLE_FINAL_NONCE_EXPIRED')
            existing=db.execute('SELECT decision_id,owner_sid FROM native_sole_final_decisions WHERE plan_id=?',
                                (row['plan_id'],)).fetchone()
            if existing:
                if existing['owner_sid']!=actor['owner_sid']:_fail('NATIVE_SOLE_FINAL_DECISION_OWNER_CHANGED')
                db.execute('UPDATE native_sole_final_nonces SET decision_id=? WHERE nonce_digest=? AND decision_id IS NULL',
                           (existing['decision_id'],_hash(nonce.encode())))
                return self._decision(db,existing['decision_id'],owner_sid=actor['owner_sid'])
            plan=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(row['plan_id'],)).fetchone()
            if plan['status']!=PLAN_PENDING_APPROVAL:_fail('NATIVE_SOLE_FINAL_PENDING_MARKET_REQUIRED')
            receipt=self.store._persist_approved_plan_in_transaction(db,plan,logical_profile='Kyle')
            snapshot=receipt.get('publication_snapshot')
            if not snapshot:_fail('NATIVE_SOLE_FINAL_APPROVED_SNAPSHOT_REQUIRED')
            decision='native-final-decision:'+_hash(row['review_json'].encode())
            db.execute('INSERT INTO native_sole_final_decisions VALUES (?,?,?,?,?,?,?,?)',
                (decision,review_digest,row['review_json'],actor['owner_sid'],plan['plan_id'],
                 receipt['approval_id'],snapshot['snapshot_digest'],int(time.time())))
            claimed=db.execute('UPDATE native_sole_final_nonces SET decision_id=? WHERE nonce_digest=? AND decision_id IS NULL',
                               (decision,_hash(nonce.encode())))
            if claimed.rowcount!=1:_fail('NATIVE_SOLE_FINAL_NONCE_CAS_LOST')
            if self._actor()!=actor:_fail('NATIVE_SOLE_FINAL_OWNER_CHANGED_DURING_RECHECK')
            return self._decision(db,decision,owner_sid=actor['owner_sid'])

    def refresh_nonce(self,*,nonce,review_digest):
        """Trusted HTTP consumer may recover the same displayed review.

        A helper session or page nonce expiry is technical recovery, not a new
        human review. This operation still validates the original nonce's SID,
        exact displayed digest, and full current source before issuing a nonce
        for the current genuine session. It never records a decision itself.
        """
        actor=self._actor()
        if type(nonce) is not str or len(nonce)!=64:_fail('NATIVE_SOLE_FINAL_NONCE_INVALID')
        with self._transaction() as db:
            old=db.execute('SELECT * FROM native_sole_final_nonces WHERE nonce_digest=?',(_hash(nonce.encode()),)).fetchone()
            if (old is None or old['owner_sid']!=actor['owner_sid'] or old['review_digest']!=review_digest
                    or _hash(old['review_json'].encode())!=old['review_sha256']):
                _fail('NATIVE_SOLE_FINAL_NONCE_BINDING_CHANGED')
            approved=_frozen(old['review_json'])
            current,_,_,read_bytes=self._candidate(db,old['plan_id'])
            if compare_before_execution(approved,current).disposition!='MATCH_ONLY':
                _fail('NATIVE_SOLE_FINAL_REVIEW_CHANGED')
            if self._actor()!=actor:_fail('NATIVE_SOLE_FINAL_OWNER_CHANGED_DURING_RECHECK')
            if old['decision_id'] is not None:
                return self._decision(db,old['decision_id'],owner_sid=actor['owner_sid'])
            fresh=secrets.token_hex(32)
            db.execute('INSERT INTO native_sole_final_nonces VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)',
                (_hash(fresh.encode()),review_digest,old['review_json'],old['review_sha256'],old['plan_id'],
                 actor['owner_sid'],actor['session_id'],actor['instance_id'],int(time.time())+300,
                 read_bytes.decode(),_hash(read_bytes)))
            return {'nonce':fresh,'review_digest':review_digest,'new_human_review_required':False,
                'execution_authority':False,'external_writes_performed':[]}

    def _decision(self,db,decision_id,*,owner_sid):
        from shared_platform.release_store import _plan_from_row, _validated_publication_snapshot_row
        row=db.execute('SELECT * FROM native_sole_final_decisions WHERE decision_id=?',(decision_id,)).fetchone()
        approval=db.execute('SELECT * FROM release_approvals WHERE approval_id=?',(row['approval_id'],)).fetchone() if row else None
        snapshot=db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?',(row['plan_id'],)).fetchone() if row else None
        plan=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(row['plan_id'],)).fetchone() if row else None
        if (row is None or approval is None or snapshot is None or plan is None
                or row['owner_sid']!=owner_sid or _frozen(row['review_json']).digest()!=row['review_digest']
                or plan['status']!=PLAN_APPROVED or approval['plan_id']!=row['plan_id']
                or approval['approved_by']!='Kyle' or approval['user_approved']!=1
                or approval['payload_digest']!=plan['payload_digest']
                or approval['confirmation_token']!=plan['confirmation_token']
                or snapshot['snapshot_digest']!=row['snapshot_digest']):
            _fail('NATIVE_SOLE_FINAL_ATOMIC_APPROVAL_CHANGED')
        _validated_publication_snapshot_row(snapshot,plan=_plan_from_row(plan))
        return {'decision_id':row['decision_id'],'review_digest':row['review_digest'],
            'release_approval_id':row['approval_id'],'snapshot_digest':row['snapshot_digest'],
            'execution_authority':False,'execution_recheck_required':True,'external_writes_performed':[]}

    def recheck(self,decision_id):
        """Automatic execution-time source recheck; this never publishes.

        Only the original exact critical equality contract permits a technical
        rebind. One native decision is retained; no second human decision is
        manufactured. Provider/state UNKNOWN preserves approval and refuses.
        """
        actor=self._actor()
        with self._transaction() as db:
            receipt=self._decision(db,decision_id,owner_sid=actor['owner_sid'])
            row=db.execute('SELECT * FROM native_sole_final_decisions WHERE decision_id=?',(decision_id,)).fetchone()
            approved=_frozen(row['review_json'])
            current,_,diagnostic,_=self._candidate(db,row['plan_id'])
            if self._actor()!=actor:_fail('NATIVE_SOLE_FINAL_OWNER_CHANGED_DURING_RECHECK')
            disposition=compare_before_execution(approved,current).disposition
            if disposition not in {'MATCH_ONLY','NONCRITICAL_REBIND_REQUIRED'}:
                _fail('NATIVE_SOLE_FINAL_'+disposition)
            # This leaf keeps the same immutable marketplace plan. Current
            # service code/signing JSON wrappers are rechecked but not hashed
            # as business-critical approval facts. Cross-plan rebinding is a
            # separate execution integration, not fabricated by this producer.
            return {**receipt,'rebind_disposition':disposition,
                'technical_details':diagnostic,'execution_recheck_succeeded':True}
